"""Unit tests for lib/claude_vm/proxmox.py and checks.py, the filters of filter_plugins/proxmox.py."""

from __future__ import annotations

import pathlib

import pytest

from claude_vm import checks
from claude_vm import proxmox as proxmox_filters

net_mac = proxmox_filters.net_mac
guest_ipv4 = checks.guest_ipv4
tailnet_ipv4 = proxmox_filters.tailnet_ipv4
guest_addresses = proxmox_filters.guest_addresses
proxmox_access_denied = proxmox_filters.proxmox_access_denied


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
        with pytest.raises(ValueError, match="no MAC address"):
            net_mac("virtio,bridge=vmbr0")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="expects a string"):
            net_mac(None)

    # With a default, as a fleet-wide sweep passes one: it must not abort because one VM has no readable NIC. Without
    # one it raises, as the two tests above hold.
    def test_returns_the_default_when_no_mac_is_present(self):
        assert net_mac("", "") == ""
        assert net_mac("bridge=vmbr0", "") == ""

    def test_returns_the_default_for_a_non_string(self):
        assert net_mac(None, "") == ""
        assert net_mac(["virtio=BC:24:11:00:00:01"], "") == ""

    def test_a_default_does_not_mask_a_real_mac(self):
        assert net_mac("virtio=BC:24:11:0E:72:04,bridge=vmbr0", "") == "bc:24:11:0e:72:04"


class TestGuestIpv4:
    def test_returns_the_address_of_the_matching_nic(self):
        assert guest_ipv4([LO, ETH0], LAN_MAC) == "192.0.2.51"

    def test_ignores_other_nics_when_a_mac_is_given(self):
        # A re-run after dev_tools installs Docker must not pick up docker0.
        assert guest_ipv4([LO, DOCKER0, ETH0], LAN_MAC) == "192.0.2.51"

    def test_mac_match_is_case_insensitive(self):
        assert guest_ipv4([ETH0], LAN_MAC.upper()) == "192.0.2.51"

    def test_an_uppercase_hardware_address_from_the_agent_still_matches(self):
        # Windows guests report BC-24-style uppercase MACs; the fake's docker0 is uppercase too.
        assert guest_ipv4([iface("eth0", LAN_MAC.upper(), [("192.0.2.51", "ipv4", 24)])], LAN_MAC) == "192.0.2.51"

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
        with pytest.raises(ValueError, match="expects a list"):
            guest_ipv4({"eth0": []})

    @pytest.mark.parametrize(
        "reported",
        [
            # The agent runs inside the VM, so this is the VM's to choose. A
            # newline would start a directive of its own in the alias block of
            # this machine's ssh config, and ProxyCommand runs here.
            "192.0.2.7\n    ProxyCommand sh -c 'touch /tmp/owned'",
            "192.0.2.7 -oProxyCommand=x",
            "-oProxyCommand=x",
            " 192.0.2.7",
            "192.0.2.7\t",
            "192.0.2.256",
            "192.0.2",
            "192.000.002.007",
            "",
            None,
            7,
            ["192.0.2.7"],
        ],
    )
    def test_anything_but_one_ipv4_address_is_no_address(self, reported):
        crafted = {
            "name": "eth0",
            "hardware-address": LAN_MAC,
            "ip-addresses": [{"ip-address": reported, "ip-address-type": "ipv4", "prefix": 24}],
        }
        assert guest_ipv4([crafted], LAN_MAC) is None
        assert guest_ipv4([crafted]) is None

    def test_a_crafted_address_does_not_hide_a_real_one_after_it(self):
        both = iface("eth0", LAN_MAC, [("192.0.2.7\n    ProxyCommand x", "ipv4", 24), ("192.0.2.51", "ipv4", 24)])
        assert guest_ipv4([both], LAN_MAC) == "192.0.2.51"

    def test_what_comes_back_is_exactly_the_address(self):
        returned = guest_ipv4([ETH0], LAN_MAC)
        assert returned == "192.0.2.51" and isinstance(returned, str)

    @pytest.mark.parametrize("reported", ["0.0.0.0", "255.255.255.255", "224.0.0.1", "239.255.255.250", "240.0.0.1"])
    def test_an_address_no_nic_answers_at_is_no_address(self, reported):
        # The VM's to choose: 0.0.0.0 as a destination is this machine itself, so
        # its aliases and the next run's connections would come here.
        crafted = iface("eth0", LAN_MAC, [(reported, "ipv4", 24)])
        assert guest_ipv4([crafted], LAN_MAC) is None
        then_real = iface("eth0", LAN_MAC, [(reported, "ipv4", 24), ("192.0.2.51", "ipv4", 24)])
        assert guest_ipv4([then_real]) == "192.0.2.51"


class TestTailnetIpv4:
    """The tailnet address a VM reports for itself, held to one IPv4 address in Tailscale's range."""

    def test_the_ipv4_address_beside_the_ipv6_one(self):
        # Self.TailscaleIPs as Phase 0 recorded it, IPv4 first.
        assert tailnet_ipv4(["100.64.0.2", "fd7a:115c:a1e0::2"]) == "100.64.0.2"
        assert tailnet_ipv4(["fd7a:115c:a1e0::2", "100.101.102.103"]) == "100.101.102.103"

    @pytest.mark.parametrize(
        "reported",
        [
            [],
            None,
            "100.64.0.2",
            ["fd7a:115c:a1e0::2"],
            # Outside 100.64.0.0/10: the LAN, and the edges of the range.
            ["192.0.2.7"],
            ["100.63.255.255"],
            ["100.128.0.0"],
            # The VM's to choose: a newline would carry text of its own into a report.
            ["100.64.0.2\nProxyCommand x"],
            [" 100.64.0.2"],
            [7],
        ],
    )
    def test_anything_else_is_no_address(self, reported):
        assert tailnet_ipv4(reported) == ""

    def test_a_bad_entry_does_not_hide_a_good_one_after_it(self):
        assert tailnet_ipv4(["100.64.0.2 x", "100.127.255.254"]) == "100.127.255.254"


class TestProxmoxAccessDenied:
    @pytest.mark.parametrize(
        "msg",
        [
            # A token that fails authentication: the module's first API call.
            "401 Unauthorized: authentication failure - {'errors': b''}",
            # A token without guest-agent access, as proxmox_vm_info reports it.
            "Failed to retrieve QEMU VMs information: 403 Forbidden: Permission check failed (/vms/4013, VM.Monitor)",
        ],
    )
    def test_true_for_a_refused_token(self, msg):
        assert proxmox_access_denied(msg) is True

    @pytest.mark.parametrize(
        "msg",
        [
            # VMIDs containing 401 or 403, in the detail Proxmox writes: a
            # stopped VM like this used to fail the listing for the whole fleet.
            "Failed to retrieve QEMU VMs information: 500 Internal Server Error: VM 4013 is not running",
            "Failed to retrieve QEMU VMs information: 500 Internal Server Error: VM 401 is not running",
            "Failed to retrieve QEMU VMs information: 500 Internal Server Error: VM 14030 is not running",
            "Failed to retrieve QEMU VMs information: 500 Internal Server Error: QEMU guest agent is not running",
            "Failed to retrieve QEMU VMs information: 595 Errors during connect(): No route to host",
            "",
        ],
    )
    def test_false_for_a_problem_with_one_vm(self, msg):
        assert proxmox_access_denied(msg) is False

    def test_false_for_no_message(self):
        assert proxmox_access_denied(None) is False


def uri_result(data=None, status=200, skipped=False):
    """What a `uri` loop registers for one VM: a JSON body, or nothing."""
    if skipped:
        return {"skipped": True, "changed": False}
    result = {"status": status, "changed": False}
    if data is not None or status != 595:
        result["json"] = {"data": data}
    return result


NET0 = "virtio=BC:24:11:0E:72:04,bridge=vmbr0"
ALPHA = {"vmid": 9001, "name": "alpha", "node": "pve", "status": "running", "tags": "agent-warehouse"}
PARKED = {"vmid": 4013, "name": "parked", "node": "pve", "status": "stopped"}
NAMELESS = {"vmid": 8001, "node": "pve2", "status": "unknown", "tags": "agent-warehouse"}


class TestGuestAddresses:
    def test_pairs_each_vm_with_its_agents_address_in_order(self):
        rows = guest_addresses(
            [ALPHA, PARKED],
            [uri_result({"net0": NET0}), uri_result({"net0": NET0})],
            [uri_result({"result": [LO, DOCKER0, ETH0]}), uri_result(skipped=True)],
        )
        assert rows == [{**ALPHA, "address": "192.0.2.51"}, {**PARKED, "address": ""}]

    def test_reads_the_nic_it_is_given(self):
        # A template whose LAN NIC is net1: reading net0 regardless is the bug
        # that left such a VM out of just deploy --only configure and blank in just status.
        config = uri_result({"net0": "virtio=02:00:00:00:00:01,bridge=vmbr1", "net1": NET0})
        network = uri_result({"result": [LO, DOCKER0, ETH0]})
        assert guest_addresses([ALPHA], [config], [network], "net1")[0]["address"] == "192.0.2.51"
        assert guest_addresses([ALPHA], [config], [network], "net0")[0]["address"] == ""

    def test_empty_for_a_request_that_failed_was_skipped_or_had_no_body(self):
        rows = guest_addresses(
            [NAMELESS, ALPHA, ALPHA, ALPHA],
            [uri_result(skipped=True), uri_result({"net0": NET0}), uri_result({"net0": NET0}), uri_result(None, 595)],
            [
                uri_result(skipped=True),
                uri_result(None, 500),
                uri_result({"result": []}),
                uri_result({"result": [ETH0]}),
            ],
        )
        assert [row["address"] for row in rows] == ["", "", "", ""]
        assert rows[0] == {**NAMELESS, "address": ""}

    def test_never_guesses_without_the_nics_mac(self):
        # Without the MAC, docker0 would come first: no such NIC, or one whose config names no MAC.
        configs = [uri_result({}), uri_result({"net1": NET0}), uri_result({"net0": "bridge=vmbr0"})]
        network = uri_result({"result": [DOCKER0, ETH0]})
        rows = guest_addresses([ALPHA] * 3, configs, [network] * 3)
        assert [row["address"] for row in rows] == ["", "", ""]

    def test_empty_for_answers_of_another_shape(self):
        # A config that is no object, an agent reply without a list of interfaces, a reply that is no object.
        rows = guest_addresses(
            [ALPHA] * 3,
            [uri_result("net0"), uri_result({"net0": NET0}), uri_result({"net0": NET0})],
            [uri_result({"result": [ETH0]}), uri_result({"result": {"eth0": ETH0}}), uri_result([ETH0])],
        )
        assert [row["address"] for row in rows] == ["", "", ""]

    def test_rejects_lists_of_different_lengths(self):
        with pytest.raises(ValueError, match="1 VMs, 2 configs"):
            guest_addresses([ALPHA], [uri_result({}), uri_result({})], [uri_result({})])

    def test_rejects_non_lists(self):
        with pytest.raises(ValueError, match="three lists"):
            guest_addresses({"vmid": 1}, [], [])


ssh_config_blocks = proxmox_filters.ssh_config_blocks
ssh_config_prune = proxmox_filters.ssh_config_prune
ssh_config_elsewhere = proxmox_filters.ssh_config_elsewhere

MANAGED = """# BEGIN agent-warehouse: alpha
Host alpha
    HostName 192.0.2.51
    User dev
# END agent-warehouse: alpha
# BEGIN agent-warehouse: beta
Host beta
    HostName 192.0.2.52
    User dev
# END agent-warehouse: beta
# BEGIN agent-warehouse: old
Host old
    HostName 192.0.2.9
    User dev
# END agent-warehouse: old
"""

# claude_vm_tag_pattern as the inventory renders it for the default tag: it, and its 1.x name.
TAG = r"(?i)(^|[;,])(agent\-warehouse|claude\-on\-proxmox)([;,]|$)"


def vm(name, vmid, tags="agent-warehouse", kind="qemu"):
    entry = {"vmid": vmid, "type": kind, "node": "pve"}
    if name is not None:
        entry["name"] = name
    if tags is not None:
        entry["tags"] = tags
    return entry


class TestVmsNotNamed:
    """The listing without the VMs named after a host of the inventory, which add_host would merge into it."""

    LISTING = [
        {"vmid": 100, "name": "alpha"},
        {"vmid": 101, "name": "pve"},
        {"vmid": 102},
        {"vmid": 103, "name": "localhost"},
        {"vmid": 104, "name": "beta"},
    ]

    def test_leaves_out_the_inventory_s_names_and_keeps_the_order(self):
        kept = proxmox_filters.vms_not_named(self.LISTING, ["pve", "localhost"])
        assert [vm["vmid"] for vm in kept] == [100, 102, 104]

    def test_a_vm_without_a_name_is_kept(self):
        assert proxmox_filters.vms_not_named([{"vmid": 102}], ["pve"]) == [{"vmid": 102}]

    def test_takes_what_selectattr_hands_on(self):
        names = (vm for vm in self.LISTING if "name" in vm)
        assert [vm["vmid"] for vm in proxmox_filters.vms_not_named(names, ["pve"])] == [100, 103, 104]

    def test_refuses_what_is_no_listing(self):
        with pytest.raises(ValueError):
            proxmox_filters.vms_not_named({"vmid": 100}, [])


class TestBuiltTemplate:
    """The template deploy, init and status find by its name: a qemu VM of that name that is a template."""

    NAME = "ubuntu-24.04-cloudinit"

    def test_a_vm_that_merely_shares_the_name_is_no_template(self):
        listing = [
            {"vmid": 100, "type": "qemu", "name": self.NAME},
            {"vmid": 101, "type": "qemu", "name": self.NAME, "template": 0},
            {"vmid": 102, "type": "lxc", "name": self.NAME, "template": 1},
            {"vmid": 103, "type": "qemu", "name": "other", "template": 1},
            {"vmid": 104},
        ]
        assert proxmox_filters.built_template(listing, self.NAME) == {}
        assert proxmox_filters.built_template([], self.NAME) == {}

    def test_the_first_template_of_that_name(self):
        listing = [
            {"vmid": 100, "type": "qemu", "name": self.NAME},
            {"vmid": 9000, "type": "qemu", "name": self.NAME, "template": 1, "node": "pve"},
            {"vmid": 9001, "type": "qemu", "name": self.NAME, "template": 1, "node": "pve2"},
        ]
        assert proxmox_filters.built_template(listing, self.NAME)["vmid"] == 9000

    def test_an_entry_that_names_no_type_counts_as_qemu(self):
        entry = {"name": self.NAME, "template": 1, "node": "pve", "config": {}}
        assert proxmox_filters.built_template([entry], self.NAME) == entry

    def test_refuses_what_is_no_listing(self):
        with pytest.raises(ValueError):
            proxmox_filters.built_template({"name": self.NAME, "template": 1}, self.NAME)


class TestProxmoxTags:
    """A VM's tags as a list, however Proxmox or proxmox_kvm joined them."""

    @pytest.mark.parametrize(
        ("tags", "listed"),
        [
            ("agent-warehouse;agent-warehouse-tailscale", ["agent-warehouse", "agent-warehouse-tailscale"]),
            # As proxmox_kvm sends them, and some versions echo them back.
            ("ansible,agent-warehouse", ["ansible", "agent-warehouse"]),
            (" Agent-Warehouse ; ;staging,", ["Agent-Warehouse", "staging"]),
            ("", []),
            (None, []),
        ],
    )
    def test_each_tag_whole_trimmed_and_as_given(self, tags, listed):
        assert proxmox_filters.proxmox_tags(tags) == listed

    def test_a_tag_is_never_matched_inside_another(self):
        tags = proxmox_filters.proxmox_tags("agent-warehouse-staging;not-agent-warehouse")
        assert "agent-warehouse" not in tags


OWNED = ["agent-warehouse", "claude-on-proxmox"]


class TestTheTagThrough1x:
    """A VM 1.x made: its tags under the ownership tag through 1.x, read as the new one's and written so once."""

    def test_the_project_s_own_tags_move_in_their_places_and_every_other_stays_as_given(self):
        tags = (
            "claude-on-proxmox;Keep.Me;claude-on-proxmox-Tailscale;claude-on-proxmox-unfinished;"
            "claude-on-proxmox-wg-198.51.100.130-reached;claude-on-proxmox-staging;not-claude-on-proxmox"
        )
        assert proxmox_filters.proxmox_tags_moved(tags, "agent-warehouse", ["claude-on-proxmox"]) == [
            "agent-warehouse",
            "Keep.Me",
            "agent-warehouse-tailscale",
            "agent-warehouse-unfinished",
            # Not a tag the project makes now - WireGuard's address, which 2.0 alone wrote - nor a lookalike.
            "claude-on-proxmox-wg-198.51.100.130-reached",
            "claude-on-proxmox-staging",
            "not-claude-on-proxmox",
        ]

    @pytest.mark.parametrize(
        ("tags", "moved"),
        [
            # Both, as on a VM someone tagged by hand: once, where the first was.
            ("claude-on-proxmox;agent-warehouse;ansible", ["agent-warehouse", "ansible"]),
            ("agent-warehouse,agent-warehouse-tailscale", ["agent-warehouse", "agent-warehouse-tailscale"]),
            # A tag that is no mark: left as it is.
            ("claude-on-proxmox;claude-on-proxmox-wg-999.0.0.1", ["agent-warehouse", "claude-on-proxmox-wg-999.0.0.1"]),
            ("", []),
        ],
    )
    def test_a_vm_s_tags_are_written_once_each(self, tags, moved):
        assert proxmox_filters.proxmox_tags_moved(tags, "agent-warehouse", ["claude-on-proxmox"]) == moved

    def test_a_tag_of_one_s_own_moves_nothing(self):
        tags = ["team.a", "claude-on-proxmox", "claude-on-proxmox-tailscale"]
        assert proxmox_filters.proxmox_tags_moved(tags, "team.a", []) == tags

    def test_a_mark_under_either_tag_is_the_vm_s_once(self):
        tags = "claude-on-proxmox;claude-on-proxmox-tailscale;agent-warehouse-tailscale;agent-warehouse-wg-198.51.100.9"
        assert proxmox_filters.proxmox_marks(tags, OWNED) == ["tailscale"]
        # One tag alone reads its own marks only: a reader that forgets the 1.x name misses the record.
        assert proxmox_filters.proxmox_marks("claude-on-proxmox-tailscale", "agent-warehouse") == []


class TestDuplicateNames:
    """The names two VMs carry, which discovery, the alias refresh and destroy refuse to act on."""

    def test_each_name_more_than_one_vm_carries_once_sorted_as_jinja_sorts(self):
        names = ["twin", "Beta", "alpha", "twin", "beta", "twin", "solo", "Beta", "alpha"]
        listing = [vm(name, vmid) for vmid, name in enumerate(names, start=100)]
        assert proxmox_filters.duplicate_names(listing) == ["alpha", "Beta", "twin"]

    def test_a_vm_without_a_name_is_none_of_them(self):
        listing = [vm(None, 100), vm(None, 101), vm("", 102), vm("", 103), vm("alpha", 104)]
        assert proxmox_filters.duplicate_names(listing) == []
        assert proxmox_filters.duplicate_names([]) == []

    def test_refuses_what_is_no_listing(self):
        with pytest.raises(ValueError):
            proxmox_filters.duplicate_names({"name": "twin"})


class TestSshAliasRefusal:
    """Which VM names may be an alias in a file included ahead of the user's whole ssh config."""

    @pytest.mark.parametrize("name", ["alpha", "dev-01", "agent-warehouse-default", "b2", "x", "1a", "0xg", "9-1"])
    def test_a_name_that_can_be_nothing_else(self, name):
        assert checks.ssh_alias_refusal(name, ["pve", "localhost"]) == ""

    @pytest.mark.parametrize(
        ("name", "said"),
        [
            # github.com: this machine's git over ssh would go to the VM's address.
            ("github.com", "dot"),
            ("192.0.2.10", "address"),
            # ssh resolves a bare number, or a hex one, as an IPv4 address.
            ("3232235777", "address"),
            ("0x7f000001", "address"),
            ("127", "address"),
            # What any platform's inet_aton takes: macOS's takes numbers past 32 bits that glibc's refuses, and both
            # take octal and hex parts, and anything after a space.
            ("4294967296", "address"),
            ("12345678901234567890", "address"),
            ("0177.1", "address"),
            ("0X7F.0.0.1", "address"),
            ("0x", "address"),
            ("1.2.3.4 x", "address"),
            ("pve", "inventory"),
            ("localhost", "inventory"),
        ],
    )
    def test_a_name_ssh_would_take_for_another_host(self, name, said):
        assert said in checks.ssh_alias_refusal(name, ["pve", "localhost"])

    def test_a_list_gives_each_refused_name_with_why_in_order(self):
        vms = [{"name": "alpha"}, {"name": "github.com"}, {"name": "pve"}, {"name": "beta"}]
        refused = checks.ssh_alias_refusals(vms, ["pve"])
        assert [(r["name"], "dot" in r["why"] or "inventory" in r["why"], r["unnamed"]) for r in refused] == [
            # Named, github.com would get its alias; pve never.
            ("github.com", True, True),
            ("pve", True, False),
        ]
        assert checks.ssh_alias_refusals([{"name": "alpha"}], ["pve"]) == []

    @pytest.mark.parametrize("name", ["alpha.lab", "github.com", "dev.example.org"])
    def test_a_name_with_a_dot_is_an_alias_when_the_person_chose_it(self, name):
        # Proxmox allows a DNS name, and docs/guides/vms.md does: the person who typed it chose what it shadows.
        assert checks.ssh_alias_refusal(name, ["pve", "localhost"], ["alpha", name]) == ""
        assert checks.ssh_alias_refusals([{"name": name}], ["pve"], [name]) == []

    @pytest.mark.parametrize(
        ("name", "said"), [("192.0.2.10", "address"), ("pve.lan", "inventory"), ("pve", "inventory")]
    )
    def test_an_address_or_a_name_of_the_inventory_is_refused_even_when_chosen(self, name, said):
        # pve.lan as the way this machine reaches the Proxmox host: its root SSH would go to the VM.
        hosts = ["pve", "localhost", "pve.lan"]
        assert said in checks.ssh_alias_refusal(name, hosts, [name])
        assert checks.ssh_alias_refusals([{"name": name}], hosts, [name])[0]["unnamed"] is False


class TestSshHostname:
    """Where an alias points: the alias's block, the Include check and just code's connection read the one rule."""

    def test_the_address_the_agent_reported(self):
        assert checks.ssh_hostname({"name": "alpha", "address": "192.0.2.51"}) == "192.0.2.51"
        assert checks.ssh_hostname({"name": "alpha", "address": "192.0.2.51"}, "") == "192.0.2.51"

    def test_the_name_in_the_domain_when_the_network_resolves_vm_names(self):
        # Whatever the agent reported, or nothing at all.
        assert checks.ssh_hostname({"name": "alpha", "address": "192.0.2.51"}, "lan") == "alpha.lan"
        assert checks.ssh_hostname({"name": "alpha", "address": ""}, "lan") == "alpha.lan"

    def test_no_address_and_no_domain_is_nowhere(self):
        assert checks.ssh_hostname({"name": "alpha", "address": ""}) == ""
        assert checks.ssh_hostname({"name": "alpha"}) == ""

    def test_refuses_what_is_no_vm(self):
        with pytest.raises(ValueError):
            checks.ssh_hostname("alpha", "lan")


class TestSshdMatchSpec:
    def test_a_connection_gives_its_client_and_its_local_end(self):
        # The branch a real VM takes: SSH_CONNECTION is "client port local port".
        spec = checks.sshd_match_spec("192.0.2.5 50022 192.0.2.51 22", "dev")
        assert spec == "user=dev,host=192.0.2.5,addr=192.0.2.5,laddr=192.0.2.51,lport=22"

    @pytest.mark.parametrize("connection", ["", None, "192.0.2.5 50022"])
    def test_without_a_whole_connection_the_loopback_address_stands_in(self, connection):
        # A container or a local run has no SSH_CONNECTION.
        assert checks.sshd_match_spec(connection, "dev") == "user=dev,host=127.0.0.1,addr=127.0.0.1"


class TestProxmoxName:
    # just init writes what the host reported into local.yml, which every later run reads as a template: a node,
    # storage or bridge name that Proxmox would never give is neither offered nor written.
    @pytest.mark.parametrize(
        ("value", "kind"),
        [("pve", "node"), ("pve-2", "node"), ("local-lvm", "storage"), ("nfs.data_1", "storage"), ("vmbr0", "bridge")],
    )
    def test_a_name_proxmox_gives_is_kept(self, value, kind):
        assert checks.proxmox_name(value, kind) == value

    @pytest.mark.parametrize(
        ("value", "kind"),
        [
            ("{{ lookup('pipe', 'id') }}", "node"),
            ("-pve", "node"),
            ("a" * 64, "node"),
            ("pve.lan", "node"),
            ("1local", "storage"),
            ("local-", "storage"),
            ("vmbr0\n", "bridge"),
            ("vmbr 0", "bridge"),
            (None, "node"),
            (7, "storage"),
        ],
    )
    def test_anything_else_is_not(self, value, kind):
        assert checks.proxmox_name(value, kind) == ""

    def test_a_kind_it_does_not_know_is_refused(self):
        with pytest.raises(ValueError, match="no kind 'pool'"):
            checks.proxmox_name("tank", "pool")

    def test_the_lists_keep_the_names_in_order_and_say_the_rest_as_json(self):
        values = ["local-lvm", "{{ x }}", "nfs", "a b"]
        assert checks.proxmox_names(values, "storage") == ["local-lvm", "nfs"]
        assert checks.proxmox_names_refused(values, "storage") == ['"{{ x }}"', '"a b"']
        assert checks.proxmox_names(None, "storage") == [] == checks.proxmox_names_refused(None, "storage")


def block(name, origin=""):
    return {"name": name, "origin": origin}


class TestSshConfigBlocks:
    def test_reads_every_block_in_file_order(self):
        assert ssh_config_blocks(MANAGED) == [block("alpha"), block("beta"), block("old")]

    def test_reads_the_host_each_block_records(self):
        text = MANAGED.replace("Host alpha\n", "# origin: nuc\nHost alpha\n").replace(
            "Host old\n", "Host old\n# origin: pve\n"
        )
        assert ssh_config_blocks(text) == [block("alpha", "nuc"), block("beta"), block("old", "pve")]

    def test_a_record_outside_a_block_is_no_blocks(self):
        # Only a line inside the block's own markers: one after its END belongs to no VM.
        text = MANAGED.replace("# END agent-warehouse: alpha\n", "# END agent-warehouse: alpha\n# origin: nuc\n")
        assert ssh_config_blocks(text)[:2] == [block("alpha"), block("beta")]

    def test_empty_for_nothing(self):
        assert ssh_config_blocks("") == []
        assert ssh_config_blocks(None) == []
        assert ssh_config_blocks("Host *\n    ServerAliveInterval 30\n") == []

    def test_a_block_whose_end_marker_names_another_vm_is_not_a_block(self):
        text = "# BEGIN agent-warehouse: alpha\nHost alpha\n# END agent-warehouse: beta\n"
        assert ssh_config_blocks(text) == []

    def test_reads_the_blocks_of_a_file_as_1x_marked_them(self):
        # The managed file under its 1.x name, which the prune and just status read until a write moves it.
        assert ssh_config_blocks(MANAGED.replace("agent-warehouse", "claude-on-proxmox")) == ssh_config_blocks(MANAGED)

    def test_a_block_whose_markers_name_two_spellings_is_not_a_block(self):
        text = "# BEGIN claude-on-proxmox: alpha\nHost alpha\n# END agent-warehouse: alpha\n"
        assert ssh_config_blocks(text) == []

    def test_rejects_non_text(self):
        with pytest.raises(ValueError, match="file's text"):
            ssh_config_blocks(["Host alpha"])


NOTHING = {"prune": [], "held": [], "untagged": [], "nameless": [], "refused": [], "elsewhere": [], "unknown": []}


class TestSshConfigPrune:
    BLOCKS = [block("alpha"), block("beta"), block("old")]

    def test_prunes_only_a_name_no_vm_carries(self):
        fleet = [vm("alpha", 9001), vm("beta", 9002), vm("ubuntu-24.04-cloudinit", 9000, tags=None)]
        assert ssh_config_prune(self.BLOCKS, fleet, TAG) == {**NOTHING, "prune": ["old"]}

    def test_prunes_a_block_this_host_records(self):
        blocks = [block("alpha", "pve"), block("old", "pve")]
        assert ssh_config_prune(blocks, [vm("alpha", 9001)], TAG, None, "pve")["prune"] == ["old"]

    def test_never_prunes_a_block_another_host_records(self):
        # The NUC's VMs are in no listing of the homelab's, swapped in by hand or one of the checkout's hosts.
        blocks = [block("alpha", "pve"), block("gamma", "nuc"), block("delta", "lab")]
        result = ssh_config_prune(blocks, [vm("alpha", 9001)], TAG, None, "pve", ["nuc"])
        assert result["prune"] == []
        # The checkout's other host's is its own, unsaid; one of a host it does not have is said.
        assert result["elsewhere"] == [{"name": "delta", "origin": "lab"}]

    def test_a_block_another_host_records_whose_name_this_host_lists_is_left_for_the_write(self):
        blocks = [block("alpha", "lab")]
        assert ssh_config_prune(blocks, [vm("alpha", 9001)], TAG, None, "pve") == NOTHING

    def test_a_block_with_no_record_is_this_hosts_with_one_host_and_nobodys_with_several(self):
        fleet = [vm("alpha", 9001)]
        assert ssh_config_prune([block("old")], fleet, TAG, None, "pve")["prune"] == ["old"]
        result = ssh_config_prune([block("old")], fleet, TAG, None, "pve", ["nuc"])
        assert result["prune"] == [] and result["unknown"] == ["old"]

    def test_a_name_no_alias_may_have_goes_whatever_host_it_records(self):
        # It would take this machine's connections to that host, so it is no host's to keep.
        result = ssh_config_prune([block("pve", "nuc")], [], TAG, ["pve", "localhost"], "pve", ["nuc"])
        assert result["refused"] == ["pve"]

    def test_keeps_a_block_whose_vm_was_rebuilt_under_a_new_vmid(self):
        # The present pass rewrites it for the new VM; nothing to prune.
        fleet = [vm("alpha", 9044), vm("beta", 9002), vm("old", 9050)]
        assert ssh_config_prune(self.BLOCKS, fleet, TAG)["prune"] == []

    def test_leaves_alone_and_reports_a_name_an_untagged_vm_carries(self):
        fleet = [vm("alpha", 9001), vm("beta", 9002), vm("old", 9010, tags="claude;ansible")]
        result = ssh_config_prune(self.BLOCKS, fleet, TAG)
        assert result["prune"] == []
        assert result["untagged"] == ["old"]
        # The same for a VM with no tags key at all, as Proxmox lists one.
        fleet = [vm("alpha", 9001), vm("beta", 9002), vm("old", 9010, tags=None)]
        assert ssh_config_prune(self.BLOCKS, fleet, TAG)["untagged"] == ["old"]

    def test_a_lookalike_tag_does_not_make_a_vm_tagged(self):
        fleet = [vm("alpha", 9001), vm("beta", 9002), vm("old", 9011, tags="agent-warehouse-staging")]
        result = ssh_config_prune(self.BLOCKS, fleet, TAG)
        assert result["untagged"] == ["old"]
        assert result["prune"] == []

    def test_holds_everything_while_a_tagged_vm_is_listed_without_a_name(self):
        # A node that is down: Proxmox drops the name but keeps the tags.
        fleet = [vm("alpha", 9001), vm("beta", 9002), vm(None, 8001)]
        result = ssh_config_prune(self.BLOCKS, fleet, TAG)
        assert result == {**NOTHING, "held": ["old"], "nameless": ["8001"]}

    def test_a_nameless_untagged_vm_holds_nothing(self):
        fleet = [vm("alpha", 9001), vm("beta", 9002), vm(None, 8000, tags=None)]
        assert ssh_config_prune(self.BLOCKS, fleet, TAG)["prune"] == ["old"]

    def test_removes_an_alias_no_vm_may_have_whatever_carries_it(self):
        # An earlier version wrote these; tagged VMs still carry pve and 3232235777, a node is down, and still both go.
        blocks = [block("alpha"), block("pve"), block("3232235777"), block("alpha.lab")]
        fleet = [vm("alpha", 9001), vm("pve", 9002), vm("3232235777", 9003), vm("alpha.lab", 9004), vm(None, 8001)]
        result = ssh_config_prune(blocks, fleet, TAG, ["pve", "localhost"])
        assert result["refused"] == ["pve", "3232235777"]
        # A dotted name is not among them: a run that names it writes it, and it is still the VM's.
        assert result["held"] == [] and result["prune"] == []

    def test_a_container_does_not_count_as_a_vm(self):
        # A tagged lxc named "old" is not what the alias was written for.
        fleet = [vm("alpha", 9001), vm("beta", 9002), vm("old", 9003, kind="lxc")]
        assert ssh_config_prune(self.BLOCKS, fleet, TAG)["prune"] == ["old"]

    def test_tags_in_either_separator_count(self):
        fleet = [
            vm("alpha", 9001, tags="ansible,agent-warehouse"),
            vm("beta", 9002, tags="ansible;agent-warehouse"),
        ]
        result = ssh_config_prune(self.BLOCKS[:2], fleet, TAG)
        assert result == NOTHING

    def test_rejects_non_lists(self):
        with pytest.raises(ValueError, match="as lists"):
            ssh_config_prune({"name": "alpha"}, [], TAG)


class TestSshConfigElsewhere:
    BLOCKS = [block("alpha", "pve"), block("beta", "nuc"), block("gamma"), block("delta", "lab")]

    def test_every_block_another_host_records(self):
        assert ssh_config_elsewhere(self.BLOCKS, "pve") == [
            {"name": "beta", "origin": "nuc"},
            {"name": "delta", "origin": "lab"},
        ]

    def test_only_those_of_the_checkouts_other_hosts_when_given(self):
        assert ssh_config_elsewhere(self.BLOCKS, "pve", ["nuc"]) == [{"name": "beta", "origin": "nuc"}]
        assert ssh_config_elsewhere(self.BLOCKS, "pve", []) == []

    def test_a_block_with_no_record_is_nobodys(self):
        assert ssh_config_elsewhere([block("gamma")], "pve") == []
        # Nor does a run with no host of its own (a static inventory) make every block another's but those with none.
        assert ssh_config_elsewhere(self.BLOCKS, "") == [b for b in self.BLOCKS if b["origin"]]

    def test_rejects_non_lists(self):
        with pytest.raises(ValueError, match="as a list"):
            ssh_config_elsewhere({"name": "alpha"}, "pve")


# ------------------------------------------------------------------------------- the template and its record ---

import ast  # noqa: E402

import yaml  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
qm_config = proxmox_filters.qm_config
proxmox_template_record = proxmox_filters.proxmox_template_record
proxmox_template_drift = proxmox_filters.proxmox_template_drift
IMAGE = "https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img"
WANTED = {
    "cpu": "host",
    "ostype": "l26",
    "bridge": "vmbr0",
    "storage": "local-lvm",
    "scsihw": "virtio-scsi-pci",
    "agent": "1",
    "serial0": "socket",
    "vga": "serial0",
    "build": "1",
    "image": IMAGE,
    "packages": "qemu-guest-agent",
}
RECORD = proxmox_template_record({"build": 1, "image": IMAGE, "packages": "qemu-guest-agent", "sha256": "ab12"})
# As Proxmox VE 9.2's API gave a real template's config back, the record added.
BUILT = {
    "name": "ubuntu-24.04-cloudinit",
    "agent": "enabled=1",
    "cpu": "host",
    "ostype": "l26",
    "net0": "virtio=BC:24:11:40:CD:BD,bridge=vmbr0",
    "scsi0": "local-lvm:base-9000-disk-0,discard=on,size=3584M",
    "scsihw": "virtio-scsi-pci",
    "serial0": "socket",
    "vga": "serial0",
    "template": 1,
    "description": RECORD,
}


def test_a_template_its_settings_would_build_differs_in_nothing():
    assert RECORD.startswith("Built by agent-warehouse: build=1; image=https://")
    assert proxmox_template_drift(BUILT, WANTED) == []


def test_a_template_whose_record_1x_wrote_differs_in_nothing_for_that():
    # Every template 1.x built keeps its record under 1.x's name: read as the record, so none is rebuilt for it.
    old = RECORD.replace("Built by agent-warehouse:", "Built by claude-on-proxmox:")
    assert old != RECORD and proxmox_template_drift({**BUILT, "description": old}, WANTED) == []


def _qm(value) -> str:
    """A description as qm config prints it: encoded, on one line, as the VM's config file keeps it."""
    return str(value).replace(":", "%3A").replace("\n", "%0A")


def test_qm_config_reads_as_the_api_gives_it():
    text = "\n".join(f"{key}: {_qm(value) if key == 'description' else value}" for key, value in BUILT.items())
    assert qm_config(text) == {key: str(value) for key, value in BUILT.items()}
    assert proxmox_template_drift(qm_config(text), WANTED) == []


def test_qm_config_decodes_the_description_proxmox_ve_9_2_printed():
    # As qm config 9000 printed a template this role built through 1.x, on Proxmox VE 9.2.24; the API gave it decoded.
    line = (
        "description: Built by claude-on-proxmox%3A build=1; image=https%3A//cloud-images.ubuntu.com/minimal/releases/"
        "noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img; packages=qemu-guest-agent; sha256=3839f148c5a3"
    )
    config = qm_config(f"name: ubuntu-24.04-cloudinit\n{line}\ncpu: host")
    assert config["description"].startswith(f"Built by claude-on-proxmox: build=1; image={IMAGE}; packages=")
    assert "no record" not in " ".join(proxmox_template_drift({**BUILT, "description": config["description"]}, WANTED))


def test_the_record_is_found_among_notes_a_person_added():
    notes = f"Our homelab's template.\n{RECORD}\nDo not delete."
    assert proxmox_template_drift({**BUILT, "description": notes}, WANTED) == []
    # qm config prints the notes on one line, each newline as %0A.
    text = f"name: x\ndescription: {_qm(notes)}\ncpu: host"
    assert qm_config(text) == {"name": "x", "description": notes, "cpu": "host"}


@pytest.mark.parametrize(
    ("changed", "said"),
    [
        ({"net0": "virtio=BC:24:11:40:CD:BD,bridge=vmbr1"}, "bridge vmbr1 → vmbr0"),
        ({"cpu": "x86-64-v2-AES"}, "cpu x86-64-v2-AES → host"),
        ({"scsi0": "nfs-vms:9000/base-9000-disk-0.qcow2,size=3584M"}, "storage nfs-vms → local-lvm"),
        ({"vga": "std"}, "vga std → serial0"),
        ({"agent": "0"}, "agent 0 → 1"),
        ({"ostype": None}, "ostype unset → l26"),
        ({"description": RECORD.replace("build=1", "build=0")}, "build 0 → 1"),
        (
            {"description": RECORD.replace("packages=qemu-guest-agent", "packages=")},
            "packages unset → qemu-guest-agent",
        ),
        ({"description": RECORD.replace("/minimal/", "/server/")}, "image " + IMAGE.replace("/minimal/", "/server/")),
    ],
)
def test_each_difference_is_said_with_what_it_has_and_what_is_wanted(changed, said):
    config = {key: value for key, value in {**BUILT, **changed}.items() if value is not None}
    (difference,) = proxmox_template_drift(config, WANTED)
    assert difference.startswith(said), difference


def test_the_default_key_of_a_property_string_is_read_either_way():
    # Proxmox may give a property string's default key bare or named.
    assert proxmox_template_drift({**BUILT, "agent": "1", "cpu": "cputype=host"}, WANTED) == []


def test_a_template_with_no_record_is_one_an_earlier_version_built():
    assert proxmox_template_drift({**BUILT, "description": "Our homelab's template."}, WANTED) == [
        "no record of how it was built, so it was built by an earlier version of this project"
    ]
    without = {key: value for key, value in BUILT.items() if key != "description"}
    assert len(proxmox_template_drift(without, WANTED)) == 1


def test_the_image_digest_is_kept_but_never_compared():
    assert proxmox_template_drift({**BUILT, "description": RECORD.replace("ab12", "ffff")}, WANTED) == []


def test_the_role_wants_each_field_the_filter_compares():
    variables = yaml.safe_load((REPO / "roles" / "proxmox_template" / "vars" / "main.yml").read_text())
    assert tuple(variables["proxmox_template_wanted"]) == proxmox_filters.TEMPLATE_FIELDS


def test_the_fake_apis_template_is_what_the_role_builds_by_default():
    """The scenarios' deploys find their template already built: one that differed would be rebuilt."""
    tree = ast.parse((REPO / "tests" / "molecule" / "fake_pve_api.py").read_text())
    (vms,) = [
        node.value for node in tree.body if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "VMS"
    ]
    template = vms.values[[key.value for key in vms.keys].index(9000)]
    config = ast.literal_eval(template.values[[key.value for key in template.keys].index("config")])
    defaults = yaml.safe_load((REPO / "roles" / "proxmox_template" / "defaults" / "main.yml").read_text())
    variables = yaml.safe_load((REPO / "roles" / "proxmox_template" / "vars" / "main.yml").read_text())
    wanted = {
        "cpu": defaults["proxmox_template_cpu_type"],
        "ostype": defaults["proxmox_template_ostype"],
        "bridge": defaults["proxmox_template_bridge"],
        "storage": defaults["proxmox_template_storage"],
        "scsihw": variables["proxmox_template_scsihw"],
        "agent": "1",
        "serial0": variables["proxmox_template_serial0"],
        "vga": variables["proxmox_template_vga"],
        "build": variables["proxmox_template_build"],
        "image": defaults["proxmox_template_image_url"],
        "packages": ",".join(defaults["proxmox_template_customize_packages"]),
    }
    assert proxmox_template_drift(config, wanted) == []
