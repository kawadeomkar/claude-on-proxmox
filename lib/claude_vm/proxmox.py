"""Proxmox's API and qm read: guest agent output, a refused token, the managed SSH config's blocks, the built template
and the duplicated names in a listing, a VM's tags, the drift of a VM's and a template's settings. Stdlib only,
nothing read or written (docs/architecture/custom-code.md §7.1)."""

from __future__ import annotations

import ipaddress
import re
from collections import Counter
from urllib.parse import unquote

from .checks import _alias_refusal, _ipv4, guest_ipv4
from .vpn.model import MARK

MAC_RE = re.compile(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")

# proxmoxer words every API error "<status> <reason>: <detail>", and a
# community.proxmox module puts its own "...: " in front. 401 and 403 are only
# looked for in that status position: Proxmox names the VM in the detail, so
# "500 Internal Server Error: VM 4013 is not running" must not read as a 401.
DENIED_RE = re.compile(r"(?:^|: )40[13] |[Pp]ermission|[Aa]uthentication")


_RAISE = object()


def net_mac(net_config, default=_RAISE):
    """Return the MAC address from a Proxmox ``netN`` config string.

    Proxmox stores it as ``virtio=BC:24:11:0E:72:04,bridge=vmbr0``.

    Raises when no MAC can be read, because a caller that is about to act on
    one VM wants to know. Pass ``default`` to get a fallback instead: a caller
    sweeping the whole fleet should skip an odd VM (one whose NIC was removed
    in the UI, or that has ``net1`` but no ``net0``) rather than abort the run
    for every other VM as well.
    """
    if isinstance(net_config, str):
        match = MAC_RE.search(net_config)
        if match:
            return match.group(0).lower()
        problem = f"no MAC address found in {net_config!r}"
    else:
        problem = f"net_mac expects a string, got {type(net_config).__name__}"

    if default is _RAISE:
        raise ValueError(problem)
    return default


_TAILNET_V4 = ipaddress.IPv4Network("100.64.0.0/10")


def tailnet_ipv4(addresses):
    """Return a node's tailnet IPv4 address from `tailscale status --json`'s Self.TailscaleIPs, or "".

    Tailscale gives each node one IPv4 address in 100.64.0.0/10 (beside an IPv6
    one). The list is what tailscaled on the VM reports, so the VM chooses it, as
    with guest_ipv4: only a string ``ipaddress`` parses as one IPv4 address in
    that range is taken, and anything else is no address.
    """
    for text in addresses if isinstance(addresses, list) else []:
        ip = _ipv4(text)
        if ip is not None and ip in _TAILNET_V4:
            return str(ip)
    return ""


# A mark: a fact about a VM that the VM cannot change, kept as a tag beside the ownership tag, <tag>-<mark>
# (docs/architecture/custom-code.md §7.11): tailscale.
MARK_RE = MARK  # the core's own pattern (lib/claude_vm/vpn/model.py), so the two never disagree


def _tag_list(tags):
    """A VM's tags as proxmox_tags reads them, from its string or a list of them, each lowercased, as the ownership
    tag is matched."""
    return [tag.lower() for tag in proxmox_tags(tags if isinstance(tags, str) else ";".join(map(str, tags or ())))]


def _owned(tag):
    """The ownership tags, lowercased: one, or every tag a VM of the project may carry as its own (claude_vm_tags_owned:
    the tag, and its name through 1.x while the tag is the default)."""
    return [str(each).lower() for each in ([tag] if isinstance(tag, str) else tag or ())]


def proxmox_marks(tags, tag):
    """The marks a VM's tags hold, the ownership tag's prefix taken off, in the order Proxmox gave them, once each: a
    mark under any of the ownership tags is the VM's."""
    marks = []
    for each in _tag_list(tags):
        for owned in _owned(tag):
            mark = each[len(owned) + 1 :] if each.startswith(f"{owned}-") else ""
            if MARK_RE.fullmatch(mark) and mark not in marks:
                marks.append(mark)
    return marks


def proxmox_tags_moved(tags, tag, old):
    """A VM's tags, each one the project gave it under ``old`` - its ownership tag through 1.x, a mark under it and its
    unfinished tag - under ``tag`` in its place, and every other tag as Proxmox gave it, none twice. ``old`` is a list,
    empty when the tag is not the default: a tag set in local.yml has no other name."""
    moved = []
    for each in proxmox_tags(tags if isinstance(tags, str) else ";".join(map(str, tags or ()))):
        for was in _owned(old):
            rest = each.lower()[len(was) :]
            if each.lower().startswith(was) and (
                rest in ("", "-unfinished") or (rest.startswith("-") and MARK_RE.fullmatch(rest[1:]))
            ):
                each = f"{str(tag).lower()}{rest}"
                break
        if each.lower() not in [m.lower() for m in moved]:
            moved.append(each)
    return moved


def guest_addresses(vms, configs, networks, nic="net0"):
    """Pair each VM of a cluster listing with the address its agent reports.

    ``vms`` is a list of ``/cluster/resources`` entries; ``configs`` and
    ``networks`` are the registered results of a ``uri`` loop over them, one
    per VM in the same order, reading ``/config`` and
    ``/agent/network-get-interfaces``. Returns the entries with an ``address``
    key added: the IPv4 the agent reports on the interface whose MAC is the
    config's ``nic``, so a bridge the guest grew later (docker0) is never
    returned. ``nic`` is the one setting that decides this, which is why the
    caller passes ``proxmox_nic``.

    Anything that leaves one VM without an address - its request failed or was
    skipped, the VM is stopped, the agent has not answered, the VM has no such
    NIC, an answer of another shape - gives that VM "", never an error: the
    caller sweeps a whole fleet and skips that VM rather than abort for every
    other one, and never guesses an address without the MAC.
    """
    if not isinstance(vms, list) or not isinstance(configs, list) or not isinstance(networks, list):
        raise ValueError("guest_addresses expects three lists")
    if not len(vms) == len(configs) == len(networks):
        raise ValueError(
            f"guest_addresses got {len(vms)} VMs, {len(configs)} configs and {len(networks)} network replies"
        )

    def body(result):
        data = result.get("json") if isinstance(result, dict) else None
        return data.get("data") if isinstance(data, dict) else None

    rows = []
    for vm, config, network in zip(vms, configs, networks, strict=True):
        settings, interfaces = body(config), body(network)
        mac = net_mac(settings.get(nic), "") if isinstance(settings, dict) else ""
        reported = interfaces.get("result") if isinstance(interfaces, dict) else None
        address = (guest_ipv4(reported, mac) or "") if mac and isinstance(reported, list) else ""
        repos = proxmox_notes_repos(settings.get("description")) if isinstance(settings, dict) else {"own": False}
        rows.append({**vm, "address": address, **({"repos": repos} if repos["own"] else {})})
    return rows


def proxmox_access_denied(msg):
    """Whether a community.proxmox error means the API refused the token.

    That is worth failing on straight away: it applies to every VM, and waiting
    or skipping would only hide it. Anything else - a stopped VM, an agent that
    is not up yet, a node that is down - is about one VM and is not.
    """
    return isinstance(msg, str) and DENIED_RE.search(msg) is not None


# The managed SSH config on the controller (playbooks/tasks/ssh_config.yml):
# one blockinfile block per VM, keyed by the VM's name, marked agent-warehouse
# from 2.0 and claude-on-proxmox through 1.x, which a file not moved yet has.
BLOCK_RE = re.compile(
    r"^# BEGIN (?P<by>agent-warehouse|claude-on-proxmox): (?P<name>\S+)$\n(?P<body>.*?)^# END (?P=by): (?P=name)$",
    re.M | re.S,
)
# Inside a block, the one line that is no ssh option: where the VM was found, the inventory name of the Proxmox host
# whose run wrote it (from 2.5). A comment, so ssh, and a checkout of an older version, read the block as before.
ORIGIN_RE = re.compile(r"^# origin: (?P<origin>\S+)$", re.M)


def ssh_config_blocks(content):
    """The alias blocks in a managed ssh config, as ``{name, origin}``, in file order.

    ``origin`` is what the block records of where its VM is - a host's name - or ``""`` for a block with no record,
    as every version before 2.5 wrote them: its host is unknown, and the next write of that VM records it.
    """
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise ValueError(f"ssh_config_blocks expects the file's text, got {type(content).__name__}")
    blocks = []
    for match in BLOCK_RE.finditer(content):
        origin = ORIGIN_RE.search(match.group("body"))
        blocks.append({"name": match.group("name"), "origin": origin.group("origin") if origin else ""})
    return blocks


def ssh_config_elsewhere(blocks, origin, others=None):
    """The blocks of ``blocks`` (``ssh_config_blocks``'s) that record another host than ``origin``, the run's own, as
    ``{name, origin}`` in file order: the alias of a VM that is that host's, which a run against this one must neither
    point elsewhere nor remove.

    With ``others`` given - the names of the other hosts this checkout has - only those recording one of them: a host
    this checkout no longer has is no one's to keep a block for, and a write takes the block over. A block with no
    record is nobody's here: its host is unknown.
    """
    if not isinstance(blocks, list):
        raise ValueError("ssh_config_elsewhere expects the blocks as a list")
    known = None if others is None else {str(other) for other in others}
    return [
        {"name": block.get("name", ""), "origin": block["origin"]}
        for block in blocks
        if isinstance(block, dict)
        and block.get("origin")
        and block["origin"] != origin
        and (known is None or block["origin"] in known)
    ]


def ssh_config_prune(blocks, resources, tag_pattern, hosts=None, origin="", others=None):
    """Which alias blocks a fleet-wide refresh should remove, and which it must not.

    ``blocks`` is what ``ssh_config_blocks`` read; ``resources`` the cluster's
    ``/cluster/resources`` listing; ``tag_pattern`` the project's whole-tag
    regex; ``hosts`` the names this machine reaches the inventory's hosts by;
    ``origin`` the name of the host whose listing this is, and ``others`` the
    names of the checkout's other hosts.
    Pruning only ever removes alias blocks, never VMs, and a block was
    only ever written for a tagged VM, so the question is just whether that VM
    is still there - on this host. A listing says nothing of another host's VMs,
    so a block that records another host is never removed for its VM's absence
    here, whether that host is one of ``others`` or not: a host swapped out of
    ``inventory/hosts.yml`` by hand is still there. Returns a dict:

    - ``refused``: names no alias may have, whatever VM carries them: one ssh
      reads as an address, or one of ``hosts`` (``ssh_alias_refusal``). An
      earlier version wrote them, and the refresh no longer does, so a block
      left would take this machine's connections to that host for good.
      Removed even while the listing is incomplete. A name with a dot is not
      among them: a named run writes one.

    - ``prune``: names with no VM of that name left anywhere in the cluster.
    - ``untagged``: names a VM still carries but without the tag. Left alone
      and reported: an untagged VM is not this project's to reason about, as
      provisioning and destroy already refuse it.
    - ``nameless``: VMIDs of tagged VMs the listing shows without a name,
      which is what a node that is down looks like. Any block could be one of
      those VMs, so when this is non-empty nothing is pruned: ``prune`` is
      empty and the names it would have held are in ``held`` instead.
    - ``elsewhere``: ``{name, origin}`` of the blocks recorded for a host that
      is not one of the checkout's (not ``origin``, not in ``others``) whose VM
      this listing lacks: left alone, and said. A block recorded for one of
      ``others`` is left too, unsaid: it is that host's.
    - ``unknown``: names of blocks with no record whose VM this listing lacks,
      while the checkout has other hosts (``others``): any of them could hold
      it, so it is left, and said. With one host, such a block is that host's,
      and pruned as before records were written.
    """
    if not isinstance(blocks, list) or not isinstance(resources, list):
        raise ValueError("ssh_config_prune expects the blocks and the cluster listing as lists")
    qemu = [vm for vm in resources if isinstance(vm, dict) and vm.get("type") == "qemu"]
    tagged = [vm for vm in qemu if re.search(tag_pattern, str(vm.get("tags", "")))]
    tagged_names = {vm["name"] for vm in tagged if vm.get("name")}
    all_names = {vm["name"] for vm in qemu if vm.get("name")}
    nameless = [str(vm.get("vmid", "?")) for vm in tagged if not vm.get("name")]
    others = {str(other) for other in others or []}

    result = {
        "prune": [],
        "held": [],
        "untagged": [],
        "nameless": nameless,
        "refused": [],
        "elsewhere": [],
        "unknown": [],
    }
    for block in blocks:
        name = block.get("name", "")
        why, unnamed = _alias_refusal(name, hosts, None)
        if why and not unnamed:
            result["refused"].append(name)
            continue
        recorded = block.get("origin") or ""
        if recorded and recorded != origin:
            if recorded not in others and name not in tagged_names:
                result["elsewhere"].append({"name": name, "origin": recorded})
            continue
        if name in tagged_names:
            continue
        if not recorded and others:
            result["unknown"].append(name)
            continue
        if name in all_names:
            result["untagged"].append(name)
        elif nameless:
            result["held"].append(name)
        else:
            result["prune"].append(name)
    return result


def vms_not_named(vms, names):
    """The entries of ``vms`` (the cluster listing's) whose name is none of ``names``, in their order.

    An entry without a name - a VM on a node that is down - is kept: it can never
    be taken for a host by its name, and the listing still shows it.
    """
    if isinstance(vms, (str, dict)) or not hasattr(vms, "__iter__"):
        raise ValueError(f"vms_not_named expects a list, got {type(vms).__name__}")
    taken = set(names or [])
    return [vm for vm in vms if not (isinstance(vm, dict) and "name" in vm and vm["name"] in taken)]


def built_template(vms, name):
    """The built template named ``name`` among ``vms`` (the cluster listing's entries, or proxmox_vm_info's), or {}.

    A qemu VM of that name whose ``template`` is set, as Proxmox lists a template (1): a VM that merely shares the
    template's name (0, or no key at all) is no template, nor is a container's. An entry that names no type counts
    as qemu. Of two, the first.
    """
    if isinstance(vms, (str, dict)) or not hasattr(vms, "__iter__"):
        raise ValueError(f"built_template expects a list, got {type(vms).__name__}")
    for vm in vms:
        if isinstance(vm, dict) and vm.get("type", "qemu") == "qemu" and vm.get("name") == name and vm.get("template"):
            return vm
    return {}


def proxmox_tags(tags):
    """A VM's tags, as a list: split on ";" or ",", each trimmed, an empty one left out, the case as given.

    Proxmox joins tags with ";" on the wire, lowercased; proxmox_kvm sends them joined with ",", and some versions
    echo that back. Whole tags, so that a caller compares a tag whole: agent-warehouse is never
    agent-warehouse-staging.
    """
    return [tag.strip() for tag in re.split(r"[;,]", str(tags or "")) if tag.strip()]


def duplicate_names(vms):
    """The names more than one entry of ``vms`` (the cluster listing's) carries, sorted as Jinja's sort sorts them,
    regardless of case; an entry without a name is none of them.

    Proxmox lets two VMs share a name, and what finds a VM by its name - add_host, an alias, destroy's release -
    would act on one of them only, unpredictably.
    """
    if isinstance(vms, (str, dict)) or not hasattr(vms, "__iter__"):
        raise ValueError(f"duplicate_names expects a list, got {type(vms).__name__}")
    counts = Counter(vm["name"] for vm in vms if isinstance(vm, dict) and vm.get("name"))
    return sorted((name for name, count in counts.items() if count > 1), key=lambda name: str(name).lower())


_SIZE_RE = re.compile(r"^\s*([0-9.]+)\s*([KMGT])?B?\s*$", re.I)


def _size_bytes(text):
    """A Proxmox size ("32G", "1T", "3584M") in bytes; None when it is none."""
    match = _SIZE_RE.match(str(text or ""))
    if not match:
        return None
    unit = (match.group(2) or "G").upper()
    return int(float(match.group(1)) * 1024 ** ("KMGT".index(unit) + 1))


def scsi0_storage(scsi0):
    """The storage a disk's property string names (``local-lvm:vm-9001-disk-0,size=32G``): its volume, the first item,
    is "<storage>:<name>". "" for a disk that names none."""
    volume = str(scsi0 or "").split(",", 1)[0]
    return volume.split(":", 1)[0] if ":" in volume else ""


def _disk_size(scsi0):
    match = re.search(r"(?:^|,)size=([0-9.]+[KMGT])", str(scsi0 or ""))
    return _size_bytes(match.group(1)) if match else None


def proxmox_disk_bytes(value):
    """A disk's size in bytes, from its property string (``local-lvm:vm-105-disk-0,size=32G``) or a size (``64G``);
    0 when it says none."""
    text = str(value or "")
    return (_disk_size(text) if "size=" in text else _size_bytes(text)) or 0


def proxmox_vm_drift(config, wanted):
    """How a VM's settings differ from what roles/proxmox_vm would send it: ``{hardware: [...], cloud_init: [...]}``.

    ``config`` is the VM's config as /nodes/<node>/qemu/<vmid>/config gives it; ``wanted`` what the role sends, from
    its own variables. Deploy applies these only to a new VM or under
    proxmox_vm_force_update, so an edited vm_cores never reaches an existing VM by itself: this is how `just status`
    sees it. Hardware differences are ``{setting, wanted, has}``, the disk's size in bytes and only when it is
    smaller than asked, since the role grows a disk and never shrinks one; the cloud-init settings that differ are
    named. A config not read gives no difference at all. A VM sized when it was created (``wanted["sized"]``, its
    tag) has a size of its own: its cores, memory and disk are no difference from the fleet's settings.
    """
    if not isinstance(config, dict) or not config or not isinstance(wanted, dict):
        return {"hardware": [], "cloud_init": []}
    if wanted.get("sized"):
        wanted = {key: value for key, value in wanted.items() if key not in ("cores", "memory", "balloon", "disk")}
    hardware = []

    def number(value):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    for setting in ("cores", "memory", "balloon"):
        has = number(config.get(setting))
        if setting == "balloon" and has is None:
            has = number(config.get("memory"))
        want = number(wanted.get(setting))
        if want is not None and has is not None and has != want:
            hardware.append({"setting": setting, "wanted": want, "has": has})
    onboot = str(config.get("onboot", "0")) in ("1", "true", "True")
    if "onboot" in wanted and bool(wanted["onboot"]) != onboot:
        hardware.append({"setting": "onboot", "wanted": bool(wanted["onboot"]), "has": onboot})
    has_disk, want_disk = _disk_size(config.get("scsi0")), _size_bytes(wanted.get("disk"))
    if has_disk and want_disk and has_disk < want_disk:
        hardware.append({"setting": "disk", "wanted": want_disk, "has": has_disk})
    cloud_init = []
    if "ciuser" in wanted and str(config.get("ciuser", "")) != str(wanted.get("ciuser") or ""):
        cloud_init.append("user")
    has_keys = {k.strip() for k in unquote(str(config.get("sshkeys", ""))).splitlines() if k.strip()}
    want_keys = {str(k).strip() for k in wanted.get("sshkeys") or [] if str(k).strip()}
    if "sshkeys" in wanted and has_keys != want_keys:
        cloud_init.append("SSH keys")
    if "ipconfig0" in wanted and str(config.get("ipconfig0", "")) != str(wanted.get("ipconfig0") or ""):
        cloud_init.append("network")
    has_dns = (str(config.get("nameserver", "")).split(), str(config.get("searchdomain", "")))
    want_dns = (list(wanted.get("nameserver") or []), str(wanted.get("searchdomain") or ""))
    if ("nameserver" in wanted or "searchdomain" in wanted) and has_dns != want_dns:
        cloud_init.append("DNS")
    return {"hardware": hardware, "cloud_init": cloud_init}


# What roles/proxmox_template records in a template's notes when it builds one: the version of its build steps and
# what went into the disk, none of which the template's config shows. One line, so that `qm config` prints it as one,
# found by this marker wherever it is among the notes a person may have added. A full clone copies its template's
# notes, so each VM keeps the line too, which reads true of it as well: what it was built from.
TEMPLATE_RECORD = "Built by agent-warehouse:"
# The same, as 1.x's builds wrote it in the notes of every template they made: read as the record too, so that 2.0
# rebuilds none for the record's name alone, and written as TEMPLATE_RECORD by the next build.
TEMPLATE_RECORD_1X = "Built by claude-on-proxmox:"
_TEMPLATE_RECORD_RE = re.compile(
    "(?:" + re.escape(TEMPLATE_RECORD) + "|" + re.escape(TEMPLATE_RECORD_1X) + r")\s*(.*)$"
)
# What the record holds, in its order: the build steps' version, the image's URL, the packages put into it (empty
# when none were), and the image's SHA-256, kept to say which image it was but never compared.
TEMPLATE_RECORD_KEYS = ("build", "image", "packages", "sha256")
# What proxmox_template_drift says of a template whose notes keep no record of its build, which a deploy refuses to
# rebuild (roles/proxmox_template/tasks/main.yml).
NO_TEMPLATE_RECORD = "no record of how it was built, so it was built by an earlier version of this project"
# What a template is compared on, in the order its differences are said: the settings every clone inherits from it,
# then the record. Not cores or memory, which each VM sets for itself.
TEMPLATE_FIELDS = (
    "cpu",
    "ostype",
    "bridge",
    "storage",
    "scsihw",
    "agent",
    "serial0",
    "vga",
    "build",
    "image",
    "packages",
)


# What just deploy --repos records in a VM's notes: the repositories that VM clones, in place of the fleet's list. One
# line beside the build record, found by this marker wherever it is among the notes a person may have added, and in
# Proxmox, where the VM cannot change it: what is cloned onto a VM is never the VM's to say.
VM_REPOS_RECORD = "agent-warehouse repositories:"
_REPO_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,99}")


def _notes_repos(notes):
    """The names on a VM's line of repositories, without any that is not a name; None when it has no such line."""
    for line in str(notes or "").splitlines():
        text = line.strip()
        if text.startswith(VM_REPOS_RECORD):
            names = (name.strip() for name in text[len(VM_REPOS_RECORD) :].split(","))
            return [name for name in names if _REPO_NAME_RE.fullmatch(name)]
    return None


def proxmox_notes_repos(notes, given=()):
    """A VM's own list of repositories, from its notes and what this run gives it: {"own": whether it has one, "names":
    what is on it, the notes' first}. A line left with no valid name is still its list, and clones nothing rather than
    the fleet's."""
    recorded = _notes_repos(notes)
    if recorded is None and not given:
        return {"own": False, "names": []}
    return {"own": True, "names": list(dict.fromkeys([*(recorded or []), *(given or [])]))}


def proxmox_notes_with_repos(notes, given):
    """``notes`` with the line of repositories holding what it held and ``given``: the line replaced where it is, or
    added last, and every other line as it was."""
    line = f"{VM_REPOS_RECORD} {','.join(proxmox_notes_repos(notes, given)['names'])}"
    lines = str(notes or "").splitlines()
    at = next((i for i, each in enumerate(lines) if each.strip().startswith(VM_REPOS_RECORD)), None)
    if at is None:
        return "\n".join([*lines, line])
    return "\n".join([*lines[:at], line, *lines[at + 1 :]])


def qm_config(text):
    """`qm config`'s output as the dict /nodes/<node>/qemu/<vmid>/config gives: each "key: value" line, and a line
    that is none added to the key before it. qm prints the description encoded, on one line, as the VM's config file
    keeps it - a colon, a newline and any byte outside printable ASCII as %XX (PVE::Tools::encode_text; "Built by
    agent-warehouse%3A build=1" on Proxmox VE 9.2) - which the API gives decoded, and so does this."""
    config, key = {}, None
    for line in str(text or "").splitlines():
        match = re.match(r"^([a-z][a-z0-9_-]*): ?(.*)$", line)
        if match:
            key = match.group(1)
            config[key] = match.group(2)
        elif key is not None:
            config[key] = f"{config[key]}\n{line}"
    if "description" in config:
        config["description"] = unquote(config["description"])
    return config


def _option(value, key):
    """One option of a Proxmox property string (``virtio=BC:..,bridge=vmbr0``): ``key``'s value, or the first item
    when it names none, which is the default key's (``cpu: host`` is ``cputype=host``, ``agent: 1`` ``enabled=1``)."""
    items = [item for item in str(value or "").split(",") if item]
    for item in items:
        name, _, rest = item.partition("=")
        if name == key and rest:
            return rest
    return items[0] if items and "=" not in items[0] else ""


def proxmox_template_record(values):
    """The one line a template's notes keep: ``values`` has TEMPLATE_RECORD_KEYS."""
    return TEMPLATE_RECORD + " " + "; ".join(f"{key}={values.get(key, '')}" for key in TEMPLATE_RECORD_KEYS)


def _template_record(config):
    for line in str(config.get("description") or "").splitlines():
        match = _TEMPLATE_RECORD_RE.search(line)
        if match:
            return dict(part.split("=", 1) for part in match.group(1).split("; ") if "=" in part)
    return None


def proxmox_template_drift(config, wanted):
    """How a template differs from what roles/proxmox_template would build: a line for each difference, "<field>
    <has> → <wanted>" in TEMPLATE_FIELDS' order; one line alone when its notes keep no record of how it was built;
    [] when it is what the role would build.

    ``config`` is the template's config, as /nodes/<node>/qemu/<vmid>/config gives it or as ``qm config`` prints it
    (through qm_config); ``wanted`` has TEMPLATE_FIELDS, as the role's own variables make them (vars/main.yml). The
    qm settings are read from the config, so a change made in Proxmox's UI is seen; what went into the disk is read
    from the record the build left in the notes, since Proxmox cannot show it.
    """
    if not isinstance(config, dict) or not isinstance(wanted, dict):
        raise ValueError("proxmox_template_drift expects the template's config and what is wanted, as dicts")
    record = _template_record(config)
    if record is None:
        return [NO_TEMPLATE_RECORD]
    has = {
        "cpu": _option(config.get("cpu"), "cputype"),
        "ostype": str(config.get("ostype") or ""),
        "bridge": _option(config.get("net0"), "bridge"),
        "storage": scsi0_storage(config.get("scsi0")),
        "scsihw": str(config.get("scsihw") or ""),
        "agent": _option(config.get("agent"), "enabled"),
        "serial0": str(config.get("serial0") or ""),
        "vga": _option(config.get("vga"), "type"),
        **{key: record.get(key, "") for key in ("build", "image", "packages")},
    }
    return [
        f"{field} {has[field] or 'unset'} → {wanted.get(field) or 'unset'}"
        for field in TEMPLATE_FIELDS
        if str(has[field]) != str(wanted.get(field) or "")
    ]


# The role just init's offer makes for the API token's user (tools/proxmox-access.sh).
ACCESS_ROLE = "AnsibleVM"


def proxmox_read_grant(node, user):
    """How the API token comes to read a node's CPU threads and memory: Sys.Audit, read only, in the role just init's
    offer makes, granted on the node, where Proxmox looks for it - the role alone is not enough where it is granted
    elsewhere. The lines just status prints under the node, and an agent reads in its JSON."""
    return [
        f"to read them, give the token's role Sys.Audit, read-only (just init's is {ACCESS_ROLE}):",
        f"pveum role modify {ACCESS_ROLE} --append 1 --privs Sys.Audit",
        f"pveum acl modify /nodes/{node} --users {user or 'ansible@pve'} --roles {ACCESS_ROLE}",
    ]


# A golden template's notes: one line under a marker of its own, never TEMPLATE_RECORD's, so _template_record and
# built_template never take a golden for a base. It replaces the notes the build VM inherited from its base whole
# (docs/architecture/lifecycle.md §4.14). Its key, its build number, its base's identity, the node it is on, when it
# was built (UTC, ISO 8601) and the size its disk was built at.
GOLDEN_RECORD = "Golden by agent-warehouse:"
GOLDEN_RECORD_KEYS = ("key", "build", "base", "node", "built", "disk")
# A build VM's notes while it is built: the key and when it started. A clone's `meta: ctime` is its template's (Proxmox
# VE 9.2), so a build VM's age is read from here, never from its config's meta.
BUILD_RECORD = "Building by agent-warehouse:"
BUILD_RECORD_KEYS = ("key", "started")


def _record(values, marker, keys):
    return marker + " " + "; ".join(f"{key}={values.get(key, '')}" for key in keys)


def _marked(config, marker):
    """The key=value pairs of the line under marker in a config's notes; None when there is none."""
    notes = config.get("description") if isinstance(config, dict) else config
    for line in str(notes or "").splitlines():
        if line.startswith(marker):
            return dict(part.split("=", 1) for part in line[len(marker) :].strip().split("; ") if "=" in part)
    return None


def golden_record(values):
    """A golden template's notes, from values with GOLDEN_RECORD_KEYS."""
    return _record(values, GOLDEN_RECORD, GOLDEN_RECORD_KEYS)


def build_record(values):
    """A build VM's notes, from values with BUILD_RECORD_KEYS."""
    return _record(values, BUILD_RECORD, BUILD_RECORD_KEYS)


def golden_read(config):
    """A golden template's record, from its config or its notes; None when it carries none."""
    return _marked(config, GOLDEN_RECORD)


def build_read(config):
    """A build VM's record, from its config or its notes; None when it carries none."""
    return _marked(config, BUILD_RECORD)


def golden_base(config, vmid):
    """A base template's identity, for a golden template's key: its VMID, the image its record names, and its config's
    digest, which Proxmox changes with any change to the config - and a template built again gets a new vmgenid and
    smbios1 UUID - so a base rebuilt for a new bridge or CPU type makes a new key, not the old golden."""
    if not isinstance(config, dict) or not config.get("digest"):
        return ""
    record = _template_record(config) or {}
    return f"{vmid}:{record.get('sha256', '')}:{config['digest']}"


def _seconds(stamp):
    """Seconds since the epoch of a UTC ISO 8601 stamp (2026-11-09T12:00:00Z); None for one that is not."""
    from datetime import UTC, datetime

    try:
        return datetime.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def golden_pick(goldens, key, node, now, max_age, disk):
    """The VMID of the golden template a VM of this key clones: the newest of goldens (each {vmid, node, config}) with
    this key in its record, on the VM's node, built less than max_age seconds before now (UTC ISO 8601), its disk no
    larger than the VM's (disk, a size such as 32G); "" when none is."""
    now_s, wanted = _seconds(now), _size_bytes(str(disk)) or 0
    found = []
    for golden in goldens or []:
        record = golden_read(golden.get("config") or {}) or {}
        built = _seconds(record.get("built"))
        if (
            record.get("key") == key
            and golden.get("node") == node == record.get("node")
            and built is not None
            and now_s is not None
            and 0 <= now_s - built < max_age
            and (_size_bytes(record.get("disk", "")) or 0) <= wanted
        ):
            found.append((built, int(golden["vmid"])))
    return str(max(found)[1]) if found else ""


def golden_collectable(goldens, now, age, configs=None):
    """The VMIDs of goldens (each {vmid, config}, or {vmid} with configs, the reads of them) built more than age
    seconds before now: no run selects one older than the maximum age, half of this, so none of these can be in use by
    another run.
    """
    now_s, read = _seconds(now), read_configs(configs or {})
    out = []
    for golden in goldens or []:
        built = _seconds((golden_read(golden.get("config") or read.get(str(golden["vmid"])) or {}) or {}).get("built"))
        if built is not None and now_s is not None and now_s - built > age:
            out.append(str(golden["vmid"]))
    return sorted(out, key=int)


def build_leftovers(builds, key, now, fresh, prefix, golden_prefix, tag, configs=None):
    """Of the VMs that may be build VMs (each {vmid, config}, or {vmid} with configs, the reads of them): busy, whether
    one of this key started less than fresh seconds before now - another run building it - and stale, the VMIDs of those
    a run cut short left. Everything is read from the VM's own config, which the listing lags behind. One named for a
    build counts where it carries tag or a build's notes; one renamed as a golden but never converted where it carries a
    golden's notes; a template never. One of them that started recently is another run's, and is not stale, nor is one
    whose config could not be read. Carrying tag with no record of its start, nothing says another run is at work on it.
    """
    now_s, read = _seconds(now), read_configs(configs or {})
    busy, stale = False, []
    for build in builds or []:
        config = build.get("config") or read.get(str(build["vmid"]))
        if config is None or int(config.get("template") or 0):
            continue
        name = str(config.get("name", ""))
        if name.startswith(golden_prefix):
            record = golden_read(config)
            started = (record or {}).get("built")
        elif name.startswith(prefix) and (build_read(config) is not None or tag in proxmox_tags(config.get("tags"))):
            record = build_read(config) or {}
            started = record.get("started")
        else:
            continue
        if record is None:
            continue
        started_s = _seconds(started)
        if started_s is None or now_s is None or not 0 <= now_s - started_s < fresh:
            stale.append(str(build["vmid"]))
        elif record.get("key") == key:
            busy = True
    return {"busy": busy, "stale": sorted(stale, key=int)}


def _templates(listing, node, prefix=None, name=None):
    return [
        entry
        for entry in listing or []
        if entry.get("type") == "qemu"
        and int(entry.get("template") or 0) == 1
        and entry.get("node") == node
        and (prefix is None or str(entry.get("name", "")).startswith(prefix))
        and (name is None or entry.get("name") == name)
    ]


def golden_reads(listing, base, node, prefix):
    """What golden_choice needs read beyond the listing: the configs of the base template, by its name, and of each
    golden on the node, as [{node, vmid}]."""
    return [
        {"node": entry["node"], "vmid": str(entry["vmid"])}
        for entry in _templates(listing, node, name=base) + _templates(listing, node, prefix=prefix)
    ]


def read_configs(reads):
    """The configs a loop of reads registered - each {item: {vmid}, json: {data: config}} - by VMID, as {vmid: config};
    one that failed is left out. A dict is taken as it is."""
    if isinstance(reads, dict):
        return {str(vmid): config for vmid, config in reads.items()}
    return {
        str(read["item"]["vmid"]): read["json"]["data"]
        for read in reads or []
        if isinstance(read, dict) and isinstance((read.get("json") or {}).get("data"), dict) and "item" in read
    }


def golden_size(boot_disk, least):
    """The size a golden built on a base with this boot disk (its property string) has: least, or the disk's own size,
    as the config writes it, where that is no smaller, since Proxmox cannot shrink a disk."""
    return _option(boot_disk, "size") if proxmox_disk_bytes(boot_disk) >= (_size_bytes(least) or 0) else str(least)


def golden_choice(
    listing, configs, vm, base, node, prefix, now, max_age, disk, boot_disk, least, digest, build, shared=True
):
    """Which template a VM is cloned from, as {exists, base, key, disk, golden, golden_name, wanted, said}: golden, the
    VMID of a fresh one of its key ("" for the base template), and golden_name its name, for the run to say; wanted,
    whether a golden of its key should be built for it; key, the key, base, the base template's VMID, and disk, the size
    a golden built now has ("" when they are unknown); said, why not a golden, for the run to say. listing is
    /cluster/resources?type=vm; configs each config golden_reads named, by VMID; disk the VM's size; boot_disk the
    base's boot disk, by its name in the config; least the smallest a golden is built at. A VM that exists keeps its
    disk: it is never cloned. A VM whose own options differ from the ones a build would see (shared false) clones the
    base, since a golden built for it could not be shared, and so does one whose disk a golden's would be too large
    for, since Proxmox cannot shrink it.
    """
    from .golden import golden_key

    configs = read_configs(configs)
    out = {
        "exists": False,
        "base": "",
        "key": "",
        "disk": "",
        "golden": "",
        "golden_name": "",
        "wanted": False,
        "said": "",
    }
    if any(e.get("type") == "qemu" and e.get("name") == vm and not int(e.get("template") or 0) for e in listing or []):
        return {**out, "exists": True, "said": f"{vm} exists"}
    bases = _templates(listing, node, name=base)
    if len(bases) != 1:
        return {**out, "said": f"the base template {base} is not on {node} once"}
    base_vmid = str(bases[0]["vmid"])
    base_config = configs.get(base_vmid) or {}
    identity = golden_base(base_config, base_vmid)
    if not identity:
        return {**out, "said": f"the base template {base}'s config could not be read"}
    out.update(
        base=base_vmid, key=golden_key(build, identity, digest), disk=golden_size(base_config.get(boot_disk), least)
    )
    if not shared:
        return {**out, "said": f"{vm} sets an image option of its own (a host var), so a golden could not be shared"}
    goldens = [
        {**e, "vmid": str(e["vmid"]), "config": configs.get(str(e["vmid"]), {})}
        for e in _templates(listing, node, prefix=prefix)
    ]
    picked = golden_pick(goldens, out["key"], node, now, max_age, disk)
    if picked:
        name = next(str(g.get("name", "")) for g in goldens if g["vmid"] == picked)
        return {**out, "golden": picked, "golden_name": name}
    if (_size_bytes(str(disk)) or 0) < (_size_bytes(out["disk"]) or 0):
        return {**out, "said": f"{vm}'s disk is smaller than a golden template's, {out['disk']}"}
    return {**out, "wanted": True}


def golden_targets(choices):
    """Of the choices of a run's VMs, {vm: choice}, what to build a golden for, as {key, base, disk, vms}: the first VM
    that wants one names it, as a VM wants one only with the options and the base the others share; {} when none does.
    """
    wanting = [(vm, choice) for vm, choice in (choices or {}).items() if choice.get("wanted")]
    if not wanting:
        return {}
    first = wanting[0][1]
    return {
        "key": first["key"],
        "base": first["base"],
        "disk": first["disk"],
        "vms": [vm for vm, choice in wanting if choice["key"] == first["key"]],
    }


def build_vms(listing, node, prefixes):
    """The VMs that may be left over from a build, by their names in the listing: on the node, named with one of
    prefixes, never a template. build_leftovers then holds each to its own config, and nothing else here deletes one."""
    return [
        {"node": entry["node"], "vmid": str(entry["vmid"]), "name": entry.get("name", "")}
        for entry in listing or []
        if entry.get("type") == "qemu"
        and not int(entry.get("template") or 0)
        and entry.get("node") == node
        and str(entry.get("name", "")).startswith(tuple(prefixes))
    ]


def build_vm_ours(config, prefixes):
    """Whether the VM a run cloned for a build is one its rescue may delete, from the VM's own config: never a template,
    and named with one of prefixes, for a build or, once golden_template has renamed it, as a golden. Its tag cannot
    say: the clone has none yet, and golden_template deletes it."""
    return (
        isinstance(config, dict)
        and not int(config.get("template") or 0)
        and str(config.get("name", "")).startswith(tuple(prefixes))
    )


def golden_templates(listing, node, prefix):
    """The goldens on the node, by name prefix, as [{node, vmid, name}]: golden_collectable then holds each to its
    record."""
    return [
        {"node": e["node"], "vmid": str(e["vmid"]), "name": e.get("name", "")}
        for e in _templates(listing, node, prefix=prefix)
    ]


def not_templates(guests):
    """The entries of a listing that are no template: one carrying the ownership tag is never a VM of the fleet."""
    return [guest for guest in guests or [] if not int(guest.get("template") or 0)]


def template_recorded(config):
    """Whether a template's notes keep the record this project's builds leave: one with none was not built by it, or
    so long ago that nothing says so, and is never rebuilt, which would destroy it."""
    return isinstance(config, dict) and _template_record(config) is not None


def os_release_debian(text):
    """Whether an os-release file names a Debian-family system: its ID, or an ID_LIKE word, ubuntu or debian."""
    values = {}
    for line in str(text or "").splitlines():
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return bool({"debian", "ubuntu"} & {values.get("ID", ""), *values.get("ID_LIKE", "").split()})


# CPU types with no AVX2, which Claude Code's native binary spins forever without (measured). Unset is kvm64, Proxmox's
# default where a VM names none.
_NO_AVX2 = re.compile(
    r"^(|kvm64|kvm32|qemu64|qemu32|486|athlon|phenom|pentium.*|coreduo|core2duo|conroe|penryn|nehalem.*|westmere.*"
    r"|sandybridge.*|ivybridge.*|opteron_g[1-5].*|x86-64-v2.*)$",
    re.I,
)


def custom_template_problems(listing, configs, name, node, disk, nic, owned):
    """What a template the user built lacks, as sentences naming the fix; [] when it can be cloned. listing is
    /cluster/resources?type=vm; configs the reads golden_reads named; disk and nic, the boot disk and NIC a VM is
    resized and addressed by; owned, the ownership tags, which a template must carry neither of nor any made from them.
    Nothing here changes it: a template of the user's own is only read and cloned."""
    found = [e for e in listing or [] if e.get("type") == "qemu" and e.get("name") == name]
    templates = [e for e in found if int(e.get("template") or 0)]
    if not found:
        return [f'there is no template named "{name}" on Proxmox']
    if not templates:
        return [f'"{name}" is a VM, not a template: convert it with qm template {found[0].get("vmid")}']
    if len(found) > 1:
        return [f'{len(found)} VMs and templates are named "{name}": give each a name of its own']
    template = templates[0]
    if template.get("node") != node:
        return [f'"{name}" is on node {template.get("node")}, and VMs are created on {node}: set proxmox_node to match']
    config = read_configs(configs).get(str(template["vmid"]))
    if not config:
        return [f'the config of "{name}" could not be read']
    vmid, problems = template["vmid"], []
    if not any(
        "cloudinit" in str(value) for key, value in config.items() if re.match(r"(ide|scsi|sata|virtio)\d+$", key)
    ):
        problems.append(f"it has no cloud-init drive: qm set {vmid} --ide2 <storage>:cloudinit")
    if _option(config.get("agent"), "enabled") != "1":
        problems.append(f"its guest agent is off, and a VM's address comes from it: qm set {vmid} --agent enabled=1")
    if nic not in config:
        problems.append(f"it has no {nic}: give it one, or set proxmox_nic to the NIC it has")
    if disk not in config:
        problems.append(
            f"it has no {disk} disk, which each VM's disk is resized as: set proxmox_vm_disk to its boot disk"
        )
    if _NO_AVX2.match(_option(config.get("cpu"), "cputype")):
        problems.append(f"its CPU type has no AVX2, without which Claude Code hangs: qm set {vmid} --cpu host")
    if config.get("ciuser"):
        problems.append(
            f"it has a cloud-init user, which makes every clone look configured: qm set {vmid} --delete ciuser"
        )
    tags = proxmox_tags(config.get("tags"))
    held = [tag for tag in tags if any(tag == each or tag.startswith(f"{each}-") for each in owned)]
    if held:
        problems.append(
            f"it carries this project's tags ({', '.join(held)}), which every clone would inherit: remove them"
        )
    return problems
