"""Filters for interpreting Proxmox API responses."""

from __future__ import annotations

import re

from ansible.errors import AnsibleFilterError

MAC_RE = re.compile(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")


def net_mac(net_config):
    """Return the MAC address from a Proxmox ``netN`` config string.

    Proxmox stores it as ``virtio=BC:24:11:0E:72:04,bridge=vmbr0``.
    """
    if not isinstance(net_config, str):
        raise AnsibleFilterError(f"net_mac expects a string, got {type(net_config).__name__}")
    match = MAC_RE.search(net_config)
    if not match:
        raise AnsibleFilterError(f"no MAC address found in {net_config!r}")
    return match.group(0).lower()


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


class FilterModule:
    def filters(self):
        return {"net_mac": net_mac, "guest_ipv4": guest_ipv4}
