"""Unit tests for filter_plugins/proxmox.py."""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from ansible.errors import AnsibleFilterError

SPEC = importlib.util.spec_from_file_location(
    "proxmox_filters",
    pathlib.Path(__file__).resolve().parents[2] / "filter_plugins" / "proxmox.py",
)
proxmox_filters = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxmox_filters)

net_mac = proxmox_filters.net_mac
guest_ipv4 = proxmox_filters.guest_ipv4


def iface(name, mac, addresses):
    return {
        "name": name,
        "hardware-address": mac,
        "ip-addresses": [
            {"ip-address": ip, "ip-address-type": kind, "prefix": prefix} for ip, kind, prefix in addresses
        ],
    }


LAN_MAC = "bc:24:11:0e:72:04"
LO = iface("lo", "00:00:00:00:00:00", [("127.0.0.1", "ipv4", 8), ("::1", "ipv6", 128)])
ETH0 = iface("eth0", LAN_MAC, [("192.0.2.51", "ipv4", 24), ("fe80::1", "ipv6", 64)])
DOCKER0 = iface("docker0", "02:42:9a:11:22:33", [("172.17.0.1", "ipv4", 16)])


class TestNetMac:
    def test_extracts_from_proxmox_config(self):
        assert net_mac("virtio=BC:24:11:0E:72:04,bridge=vmbr0") == LAN_MAC

    def test_lowercases(self):
        # Assert the value, not .islower(): that predicate is True for a MAC of
        # digits only, and for a truncated return such as "aa:bb".
        assert net_mac("virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0") == "aa:bb:cc:dd:ee:ff"

    def test_returns_the_whole_address_when_it_is_all_digits(self):
        assert net_mac("virtio=00:11:22:33:44:55,bridge=vmbr0") == "00:11:22:33:44:55"

    def test_rejects_config_without_a_mac(self):
        with pytest.raises(AnsibleFilterError, match="no MAC address"):
            net_mac("virtio,bridge=vmbr0")

    def test_rejects_non_string(self):
        with pytest.raises(AnsibleFilterError, match="expects a string"):
            net_mac(None)


class TestGuestIpv4:
    def test_returns_the_address_of_the_matching_nic(self):
        assert guest_ipv4([LO, ETH0], LAN_MAC) == "192.0.2.51"

    def test_ignores_other_nics_when_a_mac_is_given(self):
        # A re-run after dev_tools installs Docker must not pick up docker0.
        assert guest_ipv4([LO, DOCKER0, ETH0], LAN_MAC) == "192.0.2.51"

    def test_mac_match_is_case_insensitive(self):
        assert guest_ipv4([ETH0], LAN_MAC.upper()) == "192.0.2.51"

    def test_skips_loopback_without_a_mac(self):
        # A plain 127.0.0.1 loopback proves nothing here - the "127." filter
        # already drops it. Give lo a routable secondary, as a host with a
        # service VIP has, so the name check is what excludes it.
        lo_with_vip = iface("lo", "00:00:00:00:00:00", [("127.0.0.1", "ipv4", 8), ("10.0.0.1", "ipv4", 32)])
        assert guest_ipv4([lo_with_vip, ETH0]) == "192.0.2.51"

    def test_skips_link_local(self):
        link_local = iface("eth0", LAN_MAC, [("169.254.3.4", "ipv4", 16), ("192.0.2.51", "ipv4", 24)])
        assert guest_ipv4([link_local], LAN_MAC) == "192.0.2.51"

    def test_none_while_the_agent_has_not_answered(self):
        assert guest_ipv4([]) is None
        assert guest_ipv4(None) is None

    def test_none_when_the_nic_has_no_address_yet(self):
        # Both shapes: QEMU's schema makes ip-addresses optional, so a NIC with
        # no address omits the key entirely rather than sending []. That is
        # exactly the state during the DHCP window the retry loop exists for.
        assert guest_ipv4([iface("eth0", LAN_MAC, [])], LAN_MAC) is None
        assert guest_ipv4([{"name": "eth0", "hardware-address": LAN_MAC}], LAN_MAC) is None
        assert guest_ipv4([{"name": "eth0", "hardware-address": LAN_MAC, "ip-addresses": None}], LAN_MAC) is None

    def test_none_when_the_interface_has_no_hardware_address(self):
        # Also optional in the agent's schema.
        assert guest_ipv4([{"name": "eth0", "ip-addresses": []}], LAN_MAC) is None

    def test_ignores_malformed_entries(self):
        assert guest_ipv4(["not-a-dict", ETH0], LAN_MAC) == "192.0.2.51"

    def test_none_when_no_nic_matches(self):
        assert guest_ipv4([ETH0], "aa:aa:aa:aa:aa:aa") is None

    def test_without_a_mac_the_first_non_loopback_nic_wins(self):
        # Pinning the documented limit of the no-MAC path: it takes whatever
        # comes first, so on a VM that has grown a docker bridge it can return
        # 172.17.0.1. Callers that care pass a MAC - which is why discover.yml
        # skips a VM whose MAC it cannot read rather than calling this bare.
        assert guest_ipv4([DOCKER0, ETH0]) == "172.17.0.1"
        assert guest_ipv4([ETH0, DOCKER0]) == "192.0.2.51"

    def test_ipv6_only_is_not_returned(self):
        v6 = iface("eth0", LAN_MAC, [("2001:db8::1", "ipv6", 64)])
        assert guest_ipv4([v6], LAN_MAC) is None

    def test_rejects_non_list(self):
        with pytest.raises(AnsibleFilterError, match="expects a list"):
            guest_ipv4({"eth0": []})


class TestNetMacDefault:
    """A fleet-wide sweep must not abort because one VM has no readable NIC."""

    def test_returns_the_default_when_no_mac_is_present(self):
        assert net_mac("", "") == ""
        assert net_mac("bridge=vmbr0", "") == ""

    def test_returns_the_default_for_a_non_string(self):
        assert net_mac(None, "") == ""
        assert net_mac(["virtio=BC:24:11:00:00:01"], "") == ""

    def test_still_raises_without_a_default(self):
        with pytest.raises(AnsibleFilterError):
            net_mac("bridge=vmbr0")

    def test_a_default_does_not_mask_a_real_mac(self):
        assert net_mac("virtio=BC:24:11:0E:72:04,bridge=vmbr0", "") == "bc:24:11:0e:72:04"
