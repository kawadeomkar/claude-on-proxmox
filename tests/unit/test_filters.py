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
        assert net_mac("virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0").islower()

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
        assert guest_ipv4([LO, ETH0]) == "192.0.2.51"

    def test_skips_link_local(self):
        link_local = iface("eth0", LAN_MAC, [("169.254.3.4", "ipv4", 16), ("192.0.2.51", "ipv4", 24)])
        assert guest_ipv4([link_local], LAN_MAC) == "192.0.2.51"

    def test_none_while_the_agent_has_not_answered(self):
        assert guest_ipv4([]) is None
        assert guest_ipv4(None) is None

    def test_none_when_the_nic_has_no_address_yet(self):
        assert guest_ipv4([iface("eth0", LAN_MAC, [])], LAN_MAC) is None

    def test_none_when_no_nic_matches(self):
        assert guest_ipv4([ETH0], "aa:aa:aa:aa:aa:aa") is None

    def test_ipv6_only_is_not_returned(self):
        v6 = iface("eth0", LAN_MAC, [("2001:db8::1", "ipv6", 64)])
        assert guest_ipv4([v6], LAN_MAC) is None

    def test_rejects_non_list(self):
        with pytest.raises(AnsibleFilterError, match="expects a list"):
            guest_ipv4({"eth0": []})
