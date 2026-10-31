"""Filters for interpreting Proxmox API responses."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import unquote

from ansible.errors import AnsibleFilterError

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
        raise AnsibleFilterError(problem)
    return default


def guest_ipv4(interfaces, mac=None):
    """Return the guest's IPv4 address from ``network-get-interfaces`` output.

    ``interfaces`` is what the QEMU guest agent reports. When ``mac`` is given,
    only the matching interface is considered, which keeps later bridges the
    guest may grow (docker0, podman, virbr0) from being picked up on a re-run.
    Loopback and link-local addresses are never returned. Returns ``None`` when
    no address is available yet, so a wait loop can poll on it.

    The agent runs inside the guest, so whatever it reports is the VM's to
    choose, and the result becomes ``ansible_host`` and the ``HostName`` of an
    alias in this machine's ssh config. Only a string ``ipaddress`` parses as one
    IPv4 address is taken: "192.0.2.7" followed by a newline and a ProxyCommand
    would otherwise be a directive of its own in that file, run on this machine
    at the next ``ssh``. Anything else counts as no address yet.
    """
    if not interfaces:
        return None
    if not isinstance(interfaces, list):
        raise AnsibleFilterError(f"guest_ipv4 expects a list, got {type(interfaces).__name__}")

    wanted = mac.lower() if mac else None
    for iface in interfaces:
        if not isinstance(iface, dict):
            continue
        if wanted and iface.get("hardware-address", "").lower() != wanted:
            continue
        if not wanted and iface.get("name") == "lo":
            continue
        for address in iface.get("ip-addresses") or []:
            if address.get("ip-address-type") != "ipv4":
                continue
            ip = _ipv4(address.get("ip-address"))
            if ip is None or ip.is_loopback or ip.is_link_local:
                continue
            return str(ip)
    return None


def _ipv4(text):
    """The IPv4 address ``text`` is, or None for anything else (a newline, a space, a leading "-", a non-string)."""
    if not isinstance(text, str):
        return None
    try:
        return ipaddress.IPv4Address(text)
    except ValueError:
        return None


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


def guest_address(vm_info, nic="net0"):
    """Return the address a VM's guest agent reports on one of its NICs, or "".

    ``vm_info`` is what ``proxmox_vm_info`` registers for a single VM read with
    ``config: current`` and ``network: true``. The NIC's MAC is read from
    ``config[nic]`` and picks the agent's matching interface, so a bridge the
    guest grew later (docker0) is never returned. ``nic`` is the one setting
    that decides this, which is why every caller passes ``proxmox_nic``.

    Anything that leaves one VM without an address - the call failed, the VM is
    stopped, the agent has not answered, the VM has no such NIC - gives "",
    never an error: callers sweep a whole fleet and skip that VM rather than
    abort for every other one, and never guess an address without the MAC.
    """
    vms = vm_info.get("proxmox_vms") if isinstance(vm_info, dict) else None
    vm = vms[0] if isinstance(vms, list) and vms else None
    if not isinstance(vm, dict) or not isinstance(vm.get("config"), dict):
        return ""
    mac = net_mac(vm["config"].get(nic), "")
    if not mac or not isinstance(vm.get("network"), list):
        return ""
    return guest_ipv4(vm["network"], mac) or ""


def guest_addresses(vms, configs, networks, nic="net0"):
    """Pair each VM of a cluster listing with the address its agent reports.

    ``vms`` is a list of ``/cluster/resources`` entries; ``configs`` and
    ``networks`` are the registered results of a ``uri`` loop over them, one
    per VM in the same order, reading ``/config`` and
    ``/agent/network-get-interfaces``. Returns the entries with an ``address``
    key added: the IPv4 the agent reports on ``nic``, or "" for a VM whose
    request failed, was skipped, or whose NIC or agent gave nothing - never an
    error, for the reasons ``guest_address`` gives.
    """
    if not isinstance(vms, list) or not isinstance(configs, list) or not isinstance(networks, list):
        raise AnsibleFilterError("guest_addresses expects three lists")
    if not len(vms) == len(configs) == len(networks):
        raise AnsibleFilterError(
            f"guest_addresses got {len(vms)} VMs, {len(configs)} configs and {len(networks)} network replies"
        )

    def body(result):
        data = result.get("json") if isinstance(result, dict) else None
        return data.get("data") if isinstance(data, dict) else None

    rows = []
    for vm, config, network in zip(vms, configs, networks, strict=True):
        interfaces = body(network)
        vm_info = {
            "proxmox_vms": [
                {
                    "config": body(config),
                    "network": interfaces.get("result") if isinstance(interfaces, dict) else None,
                }
            ]
        }
        rows.append({**vm, "address": guest_address(vm_info, nic)})
    return rows


def proxmox_access_denied(msg):
    """Whether a community.proxmox error means the API refused the token.

    That is worth failing on straight away: it applies to every VM, and waiting
    or skipping would only hide it. Anything else - a stopped VM, an agent that
    is not up yet, a node that is down - is about one VM and is not.
    """
    return isinstance(msg, str) and DENIED_RE.search(msg) is not None


# The managed SSH config on the controller (playbooks/tasks/ssh_config.yml):
# one blockinfile block per VM, keyed by the VM's name.
BLOCK_RE = re.compile(
    r"^# BEGIN claude-on-proxmox: (?P<name>\S+)$\n(?P<body>.*?)^# END claude-on-proxmox: (?P=name)$",
    re.M | re.S,
)


def ssh_config_blocks(content):
    """The alias blocks in a managed ssh config, as ``{name}``, in file order."""
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise AnsibleFilterError(f"ssh_config_blocks expects the file's text, got {type(content).__name__}")
    return [{"name": match.group("name")} for match in BLOCK_RE.finditer(content)]


def ssh_config_prune(blocks, resources, tag_pattern):
    """Which alias blocks a fleet-wide refresh should remove, and which it must not.

    ``blocks`` is what ``ssh_config_blocks`` read; ``resources`` the cluster's
    ``/cluster/resources`` listing; ``tag_pattern`` the project's whole-tag
    regex. Pruning only ever removes alias blocks, never VMs, and a block was
    only ever written for a tagged VM, so the question is just whether that VM
    is still there. Returns a dict:

    - ``prune``: names with no VM of that name left anywhere in the cluster.
    - ``untagged``: names a VM still carries but without the tag. Left alone
      and reported: an untagged VM is not this project's to reason about, as
      provisioning and destroy already refuse it.
    - ``nameless``: VMIDs of tagged VMs the listing shows without a name,
      which is what a node that is down looks like. Any block could be one of
      those VMs, so when this is non-empty nothing is pruned: ``prune`` is
      empty and the names it would have held are in ``held`` instead.
    """
    if not isinstance(blocks, list) or not isinstance(resources, list):
        raise AnsibleFilterError("ssh_config_prune expects the blocks and the cluster listing as lists")
    qemu = [vm for vm in resources if isinstance(vm, dict) and vm.get("type") == "qemu"]
    tagged = [vm for vm in qemu if re.search(tag_pattern, str(vm.get("tags", "")))]
    tagged_names = {vm["name"] for vm in tagged if vm.get("name")}
    all_names = {vm["name"] for vm in qemu if vm.get("name")}
    nameless = [str(vm.get("vmid", "?")) for vm in tagged if not vm.get("name")]

    result = {"prune": [], "held": [], "untagged": [], "nameless": nameless}
    for block in blocks:
        name = block.get("name", "")
        if name in tagged_names:
            continue
        if name in all_names:
            result["untagged"].append(name)
        elif nameless:
            result["held"].append(name)
        else:
            result["prune"].append(name)
    return result


_SIZE_RE = re.compile(r"^\s*([0-9.]+)\s*([KMGT])?B?\s*$", re.I)


def _size_bytes(text):
    """A Proxmox size ("32G", "1T", "3584M") in bytes; None when it is none."""
    match = _SIZE_RE.match(str(text or ""))
    if not match:
        return None
    unit = (match.group(2) or "G").upper()
    return int(float(match.group(1)) * 1024 ** ("KMGT".index(unit) + 1))


def _disk_size(scsi0):
    match = re.search(r"(?:^|,)size=([0-9.]+[KMGT])", str(scsi0 or ""))
    return _size_bytes(match.group(1)) if match else None


def proxmox_vm_drift(config, wanted):
    """How a VM's settings differ from what roles/proxmox_vm would send it: ``{hardware: [...], cloud_init: [...]}``.

    ``config`` is the VM's config as /nodes/<node>/qemu/<vmid>/config gives it; ``wanted`` what the role sends, from
    its own variables (tasks/settings_drift.yml). Deploy applies these only to a new VM or under
    proxmox_vm_force_update, so an edited vm_cores never reaches an existing VM by itself: this is how `just status`
    sees it. Hardware differences are ``{setting, wanted, has}``, the disk's size in bytes and only when it is
    smaller than asked, since the role grows a disk and never shrinks one; the cloud-init settings that differ are
    named. A config not read gives no difference at all.
    """
    if not isinstance(config, dict) or not config or not isinstance(wanted, dict):
        return {"hardware": [], "cloud_init": []}
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
TEMPLATE_RECORD = "Built by claude-on-proxmox:"
_TEMPLATE_RECORD_RE = re.compile(re.escape(TEMPLATE_RECORD) + r"\s*(.*)$")
# What the record holds, in its order: the build steps' version, the image's URL, the packages put into it (empty
# when none were), and the image's SHA-256, kept to say which image it was but never compared.
TEMPLATE_RECORD_KEYS = ("build", "image", "packages", "sha256")
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


def qm_config(text):
    """`qm config`'s output as the dict /nodes/<node>/qemu/<vmid>/config gives: each "key: value" line, and a line
    that is none added to the key before it. qm prints the description encoded, on one line, as the VM's config file
    keeps it - a colon, a newline and any byte outside printable ASCII as %XX (PVE::Tools::encode_text; "Built by
    claude-on-proxmox%3A build=1" on Proxmox VE 9.2) - which the API gives decoded, and so does this."""
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
        raise AnsibleFilterError("proxmox_template_drift expects the template's config and what is wanted, as dicts")
    record = _template_record(config)
    if record is None:
        return ["no record of how it was built, so it was built by an earlier version of this project"]
    has = {
        "cpu": _option(config.get("cpu"), "cputype"),
        "ostype": str(config.get("ostype") or ""),
        "bridge": _option(config.get("net0"), "bridge"),
        "storage": str(config.get("scsi0") or "").split(":", 1)[0] if ":" in str(config.get("scsi0") or "") else "",
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


class FilterModule:
    def filters(self):
        return {
            "qm_config": qm_config,
            "proxmox_template_record": proxmox_template_record,
            "proxmox_template_drift": proxmox_template_drift,
            "proxmox_vm_drift": proxmox_vm_drift,
            "ssh_config_blocks": ssh_config_blocks,
            "ssh_config_prune": ssh_config_prune,
            "net_mac": net_mac,
            "guest_ipv4": guest_ipv4,
            "tailnet_ipv4": tailnet_ipv4,
            "guest_address": guest_address,
            "guest_addresses": guest_addresses,
            "proxmox_access_denied": proxmox_access_denied,
        }
