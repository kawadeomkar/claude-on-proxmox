"""`just status`: read-only information that must never change anything (playbooks/status.yml).

- The filters (filter_plugins/status.py, one_line in text.py, proxmox_vm_drift in proxmox.py), on canned reads:
  every value a VM supplies is one line of plain text before anything keeps it, the report ranks what it found,
  and every line of it fits 100 columns.
- The playbook, run against the fake Proxmox (tests/molecule/fake_pve_api.py): what it reads, and what it says
  when Proxmox does not answer, refuses the token, or a name matches no VM.
- Every task status runs, and every task file and role entry point it reaches, held to a fixed set of reads: GETs
  through uri pinned to the API's URL, the readers, add_host, set_fact, and the report's one write.
- The callback (callback_plugins/claude_on_proxmox_status.py), driven in this process with canned results and a
  stand-in terminal: what it writes, and what it never does. test_status_live.py runs it at a pseudo-terminal.
"""

from __future__ import annotations

import importlib.util
import inspect
import io
import json
import re
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import ansible.utils.color  # noqa: F401 - imported at a stand-in terminal, it would ask curses for its fileno
import pytest
import yaml
from harness import FAKES, PVE_SECRET, REPO, _certificate, _pve, _run, _serve

sys.path.insert(0, str(REPO / "filter_plugins"))
import proxmox as proxmox_filters  # noqa: E402
import status as S  # noqa: E402
import text as text_filters  # noqa: E402

STATUS = REPO / "playbooks" / "status.yml"
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
    assert item["text"] == "local.yml asks for 8.0 GiB of memory; alpha has 4.0 GiB"
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
        "tags": "claude-on-proxmox",
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
        "tags": "claude-on-proxmox;claude-on-proxmox-unfinished",
        "maxcpu": 2,
        "maxmem": 4 * 1024**3,
    },
    {"vmid": 102, "name": "twin", "node": "pve", "type": "qemu", "status": "running", "tags": "claude-on-proxmox"},
    {"vmid": 103, "name": "twin", "node": "pve", "type": "qemu", "status": "running", "tags": "claude-on-proxmox"},
    {"vmid": 104, "node": "pve2", "type": "qemu", "status": "unknown", "tags": "claude-on-proxmox"},
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
        "tags": "claude-on-proxmox",
    },
]
SETTINGS = {
    "tag": "claude-on-proxmox",
    "template_name": "ubuntu-24.04-cloudinit",
    "storage": "local-lvm",
    "sections": "",
    "tailscale_enabled": True,
}


def _tagged(listing=LISTING):
    return [
        e
        for e in listing
        if e.get("type") == "qemu" and re.search(r"(^|[;,])claude-on-proxmox([;,]|$)", e.get("tags", ""))
    ]


def _fleet(names=(), **kw):
    tagged = _tagged()
    requested = [e for e in tagged if not names or e.get("name") in names]
    return S.status_fleet(
        LISTING, tagged=tagged, requested=requested, settings={**SETTINGS, "names": list(names)}, **kw
    )


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


def test_every_line_of_the_report_fits_100_columns() -> None:
    fleet = _fleet()
    for vm in fleet["vms"]:
        vm["name"] = (vm["name"] or "n") + "-with-a-very-long-name-indeed"
    model = S.status_report({"localhost": {"fleet": fleet}}, "2026-10-25 14:32 PDT", 1_800_000_000)
    lines = S.status_text(model, reading="read in 8.2 s · proxmox 0.9 s").split("\n")
    assert max(len(line) for line in lines) <= S.WIDTH, max(lines, key=len)


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
    rendered = S.status_text(model, color=False)
    assert not any(line.startswith(" ● evil") for line in rendered.split("\n"))
    assert "\x1b" not in rendered and "‮" not in rendered and "\r" not in rendered
    # And --json, built from the same cleaned model, holds none of it either.
    dumped = json.dumps(model, ensure_ascii=False)
    assert "\\n ● evil" not in dumped and "‮" not in dumped


def test_a_live_frame_draws_a_spinner_where_a_value_is_pending_and_the_final_one_none() -> None:
    model = _model(live=True)
    frame = S.status_text(model, glyph="⠼")
    assert "⠼" in frame and "ranked once every VM is read" in frame
    final = S.status_text(_model())
    assert S.PENDING not in final and "⠼" not in final and final.split("\n")[-1].startswith("exit ")


# ---------------------------------------------------------------------------------- the playbook, faked ----


def fake_fleet(directory: Path, version: str = "9.2.21"):
    """The fake Proxmox, on VE 9 unless told, with alpha and beta running and their agents up, and gamma stopped;
    its URL."""
    cert, key = _certificate(directory)
    for base in _serve(FAKES / "fake_pve_api.py", str(cert), str(key), str(directory / "pve"), PVE_SECRET):
        base = base.replace("http://", "https://")
        _pve(base, "PUT", "fake/version", version=version)
        for vmid, name, running in [(9010, "alpha", True), (9011, "beta", True), (9012, "gamma", False)]:
            _pve(base, "POST", "nodes/pve/qemu/9000/clone", newid=vmid, name=name)
            _pve(base, "PUT", f"nodes/pve/qemu/{vmid}/config", tags="claude-on-proxmox", ciuser="dev", onboot="1")
            if name == "alpha":
                # Made two days ago, so its day of history is its own; beta and gamma were made just now.
                _pve(base, "PUT", "fake/disk-ctime", vmid=vmid, ctime=int(time.time()) - 2 * 86400)
            if running:
                _pve(base, "POST", f"nodes/pve/qemu/{vmid}/status/start")
                for _ in range(3):
                    try:
                        _pve(base, "GET", f"nodes/pve/qemu/{vmid}/agent/network-get-interfaces")
                    except Exception:  # noqa: BLE001 - the fake refuses the first polls on purpose
                        pass
        yield base


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


def status_root(tmp_path: Path, base: str, settings: dict | None = None) -> tuple[Path, Path]:
    """A run directory for status.yml against the fake at base, with settings on top: the directory and playbook."""
    root = tmp_path / "run"
    (root / "group_vars" / "all").mkdir(parents=True, exist_ok=True)
    (root / "ssh").write_text(SSH)
    (root / "ssh").chmod(0o755)
    if not (root / "python").exists():
        (root / "python").symlink_to(sys.executable)
    defaults = root / "group_vars" / "all" / "defaults.yml"
    if not defaults.exists():
        defaults.symlink_to(REPO / "inventory" / "group_vars" / "all" / "defaults.yml")
    port = int(base.rsplit(":", 1)[1])
    (root / "group_vars" / "all" / "fake_api.yml").write_text(
        yaml.safe_dump(
            {
                "proxmox_api_host": "127.0.0.1",
                "proxmox_api_port": port,
                "vault_proxmox_api_token_secret": PVE_SECRET,
                "vm_user": "dev",
                "vm_ssh_public_keys": [],
                # Never the developer's ~/.ssh: the managed file is the test's own.
                "claude_ssh_config_file": str(tmp_path / "ssh.conf"),
                # Never a real ssh: the stand-in refuses as an unreachable VM does.
                "ansible_ssh_executable": str(root / "ssh"),
                # apt's simulation is the VM's: this machine has none.
                "status_read_upgrades": False,
                "status_report_file": "",
                **(settings or {}),
            }
        )
    )
    playbook = root / "status.yml"
    playbook.write_text(STATUS.read_text().replace("tasks/", f"{REPO / 'playbooks' / 'tasks'}/"))
    return root, playbook


def _status_run(tmp_path, base, *extra, settings=None):
    """status.yml from a run directory of its own, against the fake at base; returns the report and its model."""
    root, playbook = status_root(tmp_path, base, settings)
    report = _run(root, playbook, {}, "--check", *extra, timeout=180)
    model = None
    for play in report["plays"]:
        for task in play["tasks"]:
            if task["task"]["name"] == "Rank what needs attention":
                model = task["hosts"]["localhost"]["ansible_facts"]["status_report"]
    return report, model


def test_status_reads_the_fleet_from_the_api_and_changes_nothing(tmp_path, fake_pve) -> None:
    (tmp_path / "ssh.conf").write_text(
        "# BEGIN claude-on-proxmox: alpha\nHost alpha\n  HostName 192.0.2.60\n# END claude-on-proxmox: alpha\n"
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
    # Read only: GETs, every one of them, and not one write.
    calls = [json.loads(line) for line in (tmp_path / "pve" / "calls.log").read_text().splitlines()]
    after_fixture = calls[[c["path"] for c in calls].index("/api2/json/cluster/resources") :]
    assert {c["method"] for c in after_fixture} == {"GET"}, {
        (c["method"], c["path"]) for c in after_fixture if c["method"] != "GET"
    }


def test_a_proxmox_that_refuses_the_token_is_said_not_failed(tmp_path, fake_pve) -> None:
    report, model = _status_run(tmp_path, fake_pve, settings={"vault_proxmox_api_token_secret": "wrong"})
    assert report["rc"] == 0, report["stderr"][-2000:]
    assert model["exit"] == 2
    item = model["attention"][0]
    assert item["level"] == "unread" and item["text"].startswith("Proxmox refused the token: ")
    assert "proxmox_api_token_id" in item["command"]


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
    items = [it for it in model["attention"] if it["vm"] == "alpha" and "local.yml asks for 4 cores" in it["text"]]
    assert items and "proxmox_vm_force_update" in items[0]["command"]


# ---------------------------------------------------------------------------- every task, held to reads ----

READS = {
    "ansible.builtin.set_fact",
    "ansible.builtin.assert",
    "ansible.builtin.add_host",
    "ansible.builtin.stat",
    "ansible.builtin.slurp",
    "ansible.builtin.getent",
    "ansible.builtin.ping",
    # The roles' reports, which status's callback does not show: a message, and NEXT STEPS' record.
    "ansible.builtin.debug",
    "ansible.builtin.set_stats",
    "vm_status_info",
}
# Every command a task status reaches runs, whole: the roles' reads, and none of their writes.
COMMANDS = {
    ("which", "gh"),
    ("gh", "auth", "status", "--hostname", "github.com", "--json", "hosts"),
    ("gh", "auth", "token", "--hostname", "github.com"),
    ("dpkg-query", "--show", "--showformat=${db:Status-Status}", "tailscale"),
    ("tailscale", "status", "--json"),
    ("tailscale", "debug", "prefs"),
}
SHARED = {"tasks/cluster_vms.yml", "tasks/vm_addresses.yml"}
ENTRY_POINTS = {
    ("proxmox_vm", "settings_drift"),
    ("claude_code", "status"),
    ("github_projects", "status"),
    ("tailscale", "status"),
}
URI_KEYS = {"url", "headers", "validate_certs", "ca_path", "timeout"}
TASK_KEYS = {
    "name",
    "loop",
    "loop_control",
    "register",
    "delegate_to",
    "when",
    "check_mode",
    "no_log",
    "failed_when",
    "vars",
    "changed_when",
    "ignore_unreachable",
    "become",
    "become_user",
    "until",
    "retries",
    "delay",
}


def _tasks(items, base):
    """Every task in ``items``, through blocks, imports, a role's entry point and the files it includes."""
    for item in items:
        if "block" in item:
            for section in ("block", "rescue", "always"):
                yield from _tasks(item.get(section) or [], base)
        elif item.get("ansible.builtin.import_tasks") and item["ansible.builtin.import_tasks"] not in SHARED:
            yield from _tasks(yaml.safe_load((base / item["ansible.builtin.import_tasks"]).read_text()), base)
        elif "ansible.builtin.include_tasks" in item:
            yield item
            yield from _tasks(yaml.safe_load((base / item["ansible.builtin.include_tasks"]).read_text()), base)
        elif "ansible.builtin.include_role" in item:
            role = item["ansible.builtin.include_role"]
            yield item
            path = REPO / "roles" / role["name"] / "tasks" / f"{role['tasks_from']}.yml"
            yield from _tasks(yaml.safe_load(path.read_text()), path.parent)
        else:
            yield item


def _every_task():
    for play in yaml.safe_load(STATUS.read_text()):
        yield from _tasks(play.get("tasks") or [], STATUS.parent)


def _action(task):
    return next(key for key in task if "." in key or key in ("vm_status_info", "git_checkouts_info"))


def test_every_task_status_runs_is_a_read_or_the_reports_one_write() -> None:
    seen = set()
    for task in _every_task():
        action = _action(task)
        seen.add(action)
        if action == "ansible.builtin.uri":
            args = task[action]
            assert set(args) <= URI_KEYS, (task["name"], set(args) - URI_KEYS)
            assert args["url"].startswith("{{ proxmox_api_url }}/"), task["name"]
            assert task.get("check_mode") is False and task.get("no_log") is True, task["name"]
            assert "method" not in args and "body" not in args
        elif action == "ansible.builtin.include_role":
            assert (task[action]["name"], task[action]["tasks_from"]) in ENTRY_POINTS, task["name"]
        elif action == "ansible.builtin.import_tasks":
            assert task[action] in SHARED, task["name"]
        elif action == "ansible.builtin.include_tasks":
            pass  # followed, and every task in it held here
        elif action == "ansible.builtin.command":
            assert tuple(task[action]["argv"]) in COMMANDS, (task["name"], task[action])
            assert set(task[action]) == {"argv"} and task.get("changed_when") is False, task["name"]
        elif action == "git_checkouts_info":
            # The checkouts' work and nothing else of the module's: no target, no branch, no busy.
            assert set(task[action]) == {"under", "work"} and task[action]["work"] is True, task["name"]
        elif action == "ansible.builtin.systemd_service":
            # With no state, enabled or daemon_reload asked for, it only reads the unit.
            assert set(task[action]) == {"name"}, (task["name"], task[action])
        elif action in ("ansible.builtin.file", "ansible.builtin.copy"):
            # The report, and its directory: the one write, into the file the recipe names.
            assert "status_report_file" in json.dumps(task[action]), task["name"]
        else:
            assert action in READS, (task["name"], action)
        assert set(task) - {action} <= TASK_KEYS, (task["name"], set(task) - {action} - TASK_KEYS)
    # The walk reaches the roles' reads, so the allowlist holds what they run.
    assert {"ansible.builtin.command", "ansible.builtin.systemd_service", "vm_status_info"} <= seen


def test_the_roles_reports_are_the_ones_configure_gives() -> None:
    # status shows the line a role's own report gives and records for NEXT STEPS, never one of its own making.
    for role, report in (("github_projects", "login_report.yml"), ("tailscale", "report.yml")):
        entry = yaml.safe_load((REPO / "roles" / role / "tasks" / "status.yml").read_text())
        assert any(task.get("ansible.builtin.include_tasks") == report for task in entry), role
    login = yaml.safe_load((REPO / "roles" / "github_projects" / "tasks" / "login.yml").read_text())
    assert [t.get("ansible.builtin.include_tasks") for t in login if "ansible.builtin.include_tasks" in t][-1] == (
        "login_report.yml"
    )


def test_status_never_runs_claude() -> None:
    # Whether `claude auth status` can refresh a login's token, and so write, is still to be checked on a real VM:
    # until then status reads the login file's being there and the installer's link, and runs no claude at all.
    for task in _every_task():
        assert "claude" not in json.dumps(task.get("ansible.builtin.command", {})), task["name"]
        assert "claude auth" not in json.dumps(task), task["name"]


def test_every_status_run_in_a_test_writes_into_its_own_directory() -> None:
    # Never the developer's ~/.ssh, and no report anywhere but where the test says: the unit runs go through
    # status_root, and every import in a scenario empties status_report_file whatever the environment says.
    source = inspect.getsource(status_root)
    assert '"claude_ssh_config_file": str(tmp_path' in source and '"status_report_file": ""' in source
    assert '"ansible_ssh_executable": str(root / "ssh")' in source
    for path in (REPO / "tests" / "unit").glob("test_*.py"):
        text = path.read_text()
        if path.name not in ("test_status.py", "test_justfile.py") and "status.yml" in text:
            assert "status_root" in text, path.name
    imports = 0
    for path in (REPO / "molecule").glob("*/*.yml"):
        for play in yaml.safe_load(path.read_text()) or []:
            if isinstance(play, dict) and str(play.get("ansible.builtin.import_playbook", "")).endswith("status.yml"):
                imports += 1
                assert play.get("vars", {}).get("status_report_file") == "", path
    assert imports == 4


def test_no_play_of_status_sets_how_to_reach_a_host() -> None:
    # A play's vars reach the tasks it delegates: how to reach a host goes on the host, through add_host.
    for play in yaml.safe_load(STATUS.read_text()):
        assert not any(key.startswith("ansible_") for key in play.get("vars") or {}), play["name"]


def test_every_vm_status_adds_gets_the_vm_python() -> None:
    adds = [
        t
        for t in _every_task()
        if "ansible.builtin.add_host" in t and t["ansible.builtin.add_host"].get("name") == "{{ status_vm.name }}"
    ]
    assert adds and all(
        t["ansible.builtin.add_host"]["ansible_python_interpreter"] == "{{ claude_vm_python }}" for t in adds
    )


# ----------------------------------------------------------------------------------------------- the callback ----


class _Terminal(io.StringIO):
    encoding = "utf-8"

    def isatty(self):
        return True


@pytest.fixture
def callback(monkeypatch):
    """Makes the callback at a stand-in terminal. Called from the test itself: pytest puts its own sys.stdout back
    between a fixture's setup and the test, and the callback writes where sys.stdout is."""
    made = []
    # The region turns the echo off on stdin when it is a terminal: under pytest -s it would be the developer's.
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    handler = signal.getsignal(signal.SIGWINCH)

    def make(quiet=False):
        monkeypatch.setenv("TERM", "xterm-256color")
        if quiet:
            monkeypatch.setenv("STATUS_QUIET", "true")
        else:
            monkeypatch.delenv("STATUS_QUIET", raising=False)
        terminal = _Terminal()
        monkeypatch.setattr(sys, "stdout", terminal)
        path = REPO / "callback_plugins" / "claude_on_proxmox_status.py"
        spec = importlib.util.spec_from_file_location("claude_on_proxmox_status_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cb = module.CallbackModule()
        if cb._region is not None:
            cb._region._terminal_size = lambda: (100, 60)
        # Plain, to read back: Ansible reads its colour setting once, when its constants are first imported.
        cb._color = False
        made.append(cb)
        return cb, terminal

    yield make
    for cb in made:
        if cb._region is not None:
            cb._region._stop.set()
        # The region's wraps of Display live on the singleton: put them back for the next test.
        for name in ("display", "prompt_until", "do_var_prompt"):
            vars(cb._display).pop(name, None)
    signal.signal(signal.SIGWINCH, handler)


def _ok(host, facts):
    return SimpleNamespace(
        result={"ansible_facts": facts},
        host=SimpleNamespace(get_name=lambda: host),
        task=SimpleNamespace(get_name=lambda: "t"),
    )


def test_the_live_region_is_redrawn_in_place_and_erased_for_the_finished_report(callback) -> None:
    cb, terminal = callback()
    fleet = _fleet()
    cb.v2_runner_on_ok(_ok("localhost", {"status_found": {"fleet": fleet}}))
    first = terminal.getvalue()
    assert "VMS" in first and "⠋" in first and first.startswith("\x1b[?2026h")
    cb.v2_runner_on_ok(
        _ok("alpha", {"status_found": {"api": {"read": True, "state": "running", "root": None, "pending": []}}})
    )
    # Moved up exactly the lines drawn before, then the whole region again, in one write.
    drawn = first.count("\n") + 1
    assert terminal.getvalue()[len(first) :].startswith(f"\x1b[?2026h\x1b[{drawn - 1}A\r")
    model = S.status_report({"localhost": {"fleet": fleet}}, "now")
    terminal.seek(0)
    terminal.truncate()
    cb.v2_runner_on_ok(_ok("localhost", {"status_report": model}))
    out = terminal.getvalue()
    assert out.startswith("\x1b[") and "\r\x1b[J" in out.split("claude-on-proxmox")[0]
    assert out.rstrip().split("\n")[-1].startswith("exit 2")
    assert "⠋" not in out.split("\x1b[J", 1)[1]


def test_a_live_line_is_cut_to_the_width_and_the_region_to_the_height(callback) -> None:
    cb, terminal = callback()
    cb._region._terminal_size = lambda: (40, 8)
    fleet = _fleet()
    cb.v2_runner_on_ok(_ok("localhost", {"status_found": {"fleet": fleet}}))
    frame = terminal.getvalue().replace("\x1b[?2026h", "").replace("\x1b[?2026l", "")
    lines = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", frame).replace("\r", "").split("\n")
    assert len(lines) <= 7
    assert max(len(line) for line in lines) <= 39
    assert any(line.startswith("⋮") and "more lines, shown when" in line for line in lines)


def test_a_resize_pushes_the_screen_into_the_scrollback_and_starts_afresh(callback) -> None:
    cb, terminal = callback()
    cb.v2_runner_on_ok(_ok("localhost", {"status_found": {"fleet": _fleet()}}))
    terminal.seek(0)
    terminal.truncate()
    cb._region._resized = True
    cb._region._terminal_size = lambda: (80, 30)
    cb._region.redraw()
    assert terminal.getvalue().startswith("\n" * 30 + "\x1b[29A\r")


def test_the_callback_writes_no_other_escape_than_its_own(callback) -> None:
    cb, terminal = callback()
    cb._color = True
    fleet = _fleet()
    cb.v2_runner_on_ok(_ok("localhost", {"status_found": {"fleet": fleet}}))
    cb._region._resized = True
    cb._region.redraw()
    cb.v2_runner_on_ok(_ok("localhost", {"status_report": S.status_report({"localhost": {"fleet": fleet}}, "now")}))
    assert "\x1b[1;36mVMS" in terminal.getvalue()
    escapes = set(re.findall(r"\x1b\[[0-9;?]*[A-Za-z]", terminal.getvalue())) - set(
        re.findall(r"\x1b\[[0-9;]*m", terminal.getvalue())
    )
    # Colours, up, erase to the end of the line, erase below, synchronized output: never one that hides the cursor.
    assert escapes <= {"\x1b[K", "\x1b[J", "\x1b[?2026h", "\x1b[?2026l"} | {
        e for e in escapes if re.fullmatch(r"\x1b\[\d+A", e)
    }


def test_under_json_nothing_is_drawn(callback) -> None:
    cb, terminal = callback(quiet=True)
    fleet = _fleet()
    cb.v2_runner_on_ok(_ok("localhost", {"status_found": {"fleet": fleet}}))
    cb.v2_runner_on_ok(_ok("localhost", {"status_report": S.status_report({"localhost": {"fleet": fleet}}, "now")}))
    cb.v2_playbook_on_stats(None)
    assert terminal.getvalue() == ""


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
                "tags": "claude-on-proxmox",
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
    text = S.status_text(model, reading="read in 8.2 s · proxmox 0.9 s · vms 20 of 20 6.4 s")
    widest = max(text.splitlines(), key=len)
    assert len(widest) <= S.WIDTH, widest
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
        "local.yml asks for",
    ):
        assert words in said, words


def test_the_longest_footer_and_kernel_fit_100_columns() -> None:
    # Nothing wrong and things to do is the footer's longest; a live smoke found it cut, and the kernel cell too.
    fleet = _fleet()
    found = {"localhost": {"fleet": fleet}}
    for vm in fleet["vms"][:2]:
        vm["address"] = "192.0.2.60"
    model = S.status_report(found, "2026-10-25 14:32 PDT", 1_800_000_000)
    model["attention"] = [S._item("todo", "alpha", "alpha", "x")] * 12
    model["counts"] = {"unread": 0, "problem": 0, "todo": 12}
    model["exit"] = 0
    model["proxmox"]["unread"] = ""
    footer = S.status_text(model).splitlines()[-1]
    assert footer == "exit 0 · nothing is wrong; 12 things wait for you · --only, --json: just help status"
    system = S._system_lines({**model, "vms": [{"name": "alpha", "needs_ssh": True, "ssh": {"system": SYSTEM}}]})
    kernel = S.plain(system[1])
    assert "6.8.0-142 · 6.8.0-147 waits" in kernel and len(kernel) <= S.WIDTH, kernel


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
    row = next(line for line in S.status_text(model).splitlines() if "● alpha" in line)
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
