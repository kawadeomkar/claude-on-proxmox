"""Filters for interpreting Proxmox API responses."""

from __future__ import annotations

import re

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
            ip = address.get("ip-address", "")
            if ip.startswith(("127.", "169.254.")):
                continue
            return ip
    return None


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


class FilterModule:
    def filters(self):
        return {
            "ssh_config_blocks": ssh_config_blocks,
            "ssh_config_prune": ssh_config_prune,
            "net_mac": net_mac,
            "guest_ipv4": guest_ipv4,
            "guest_address": guest_address,
            "guest_addresses": guest_addresses,
            "proxmox_access_denied": proxmox_access_denied,
        }
