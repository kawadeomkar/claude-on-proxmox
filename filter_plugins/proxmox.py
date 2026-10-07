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


def proxmox_access_denied(msg):
    """Whether a community.proxmox error means the API refused the token.

    That is worth failing on straight away: it applies to every VM, and waiting
    or skipping would only hide it. Anything else - a stopped VM, an agent that
    is not up yet, a node that is down - is about one VM and is not.
    """
    return isinstance(msg, str) and DENIED_RE.search(msg) is not None


class FilterModule:
    def filters(self):
        return {
            "net_mac": net_mac,
            "guest_ipv4": guest_ipv4,
            "proxmox_access_denied": proxmox_access_denied,
        }
