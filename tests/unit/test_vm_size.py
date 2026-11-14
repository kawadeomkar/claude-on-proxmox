"""A VM's size given when it is created - just deploy alpha --cores 8 --memory 16G --disk 64G - and what the nodes
have for it.

The size reaches the role as vm_size, in place of the vm_cores, vm_memory_mb and vm_disk_size settings, for each VM the
run creates, which is tagged <ownership tag>-sized: its size is its own from then on, so just status calls no
difference from the settings drift, and a repair keeps it. A VM that exists is never resized, and refused when given
another size. A size larger than the node it is created on is refused before anything is created, where the token may
read the node (Sys.Audit). just status's capacity section says each node's CPU threads and memory, and what its VMs
hold. The runs go against the fake Proxmox (tests/molecule/fake_pve_api.py), as test_vm_names.py's do.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from harness import PVE_SECRET, _failures, _pve, _results, fake_fleet
from test_justfile import REPO, _just, _output, _playbook, _recording
from test_status import _status_run
from test_vm_names import FROM_THE_HOST, PLAYBOOKS
from test_vm_names import _playbook as _run_playbook

from claude_vm import proxmox
from claude_vm.status import model as S
from claude_vm.tui.status import WIDTH, status_text

CHECK = REPO / "tools" / "check"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyDoNotUse test"
REFUSED = "present | Refuse a size for a VM that exists with another"
SETTINGS = "present | Apply hardware and cloud-init settings"
GIB = 1024**3


@pytest.fixture
def fleet(tmp_path):
    yield from fake_fleet(tmp_path)


# --------------------------------------------------------------- the recipe's options ---
@pytest.mark.parametrize(
    ("args", "size"),
    [
        (["--cores", "8"], {"cores": 8}),
        (["--memory", "16G"], {"memory_mb": 16384}),
        (["--memory", "3072"], {"memory_mb": 3072}),
        (["--memory", "512M", "--disk", "64G", "--cores", "2"], {"cores": 2, "memory_mb": 512, "disk": "64G"}),
    ],
)
def test_deploy_passes_the_size_as_json_memory_in_mib(tmp_path: Path, args: list[str], size: dict) -> None:
    run = _playbook(tmp_path, "deploy", "alpha", *args)
    assert json.loads(run[run.index('{"vm_name": "alpha"}') + 2]) == {"vm_size": size}, run


@pytest.mark.parametrize(
    "args",
    [
        ["alpha", "--cores", "4", "--check"],
        ["alpha", "--cores", "4", "--tags", "claude_code"],
        ["alpha", "--memory", "8G", "--only", "configure"],
        ["--disk", "64G", "--only", "template"],
    ],
)
def test_a_size_is_refused_where_no_vm_is_created(tmp_path: Path, args: list[str]) -> None:
    result = _just("deploy", *args, settings=_recording(tmp_path))
    assert result.returncode != 0 and "size a VM the run creates" in result.stderr, _output(result)


@pytest.mark.parametrize("args", [["--cores", "0"], ["--memory", "8T"], ["--disk", "64"], ["--cores", "-4"]])
def test_a_size_just_refuses_by_its_pattern(tmp_path: Path, args: list[str]) -> None:
    result = _just("deploy", "alpha", *args, settings=_recording(tmp_path))
    assert result.returncode != 0, _output(result)


@pytest.mark.parametrize(
    ("size", "ok"),
    [
        (("8", "16G", "64G"), True),
        (("", "3072", ""), True),
        (("999", "512M", "1T"), True),
        (("", "", ""), True),
        (("0", "", ""), False),
        (("1000", "", ""), False),
        (('8, "x": 1', "", ""), False),
        (("", "8T", ""), False),
        (("", "12345678", ""), False),
        (("", "", "64"), False),
        (("", "", "064G"), False),
        (("", "", "1234567G"), False),
    ],
)
def test_tools_check_holds_the_size_again_before_it_goes_into_json(size: tuple[str, str, str], ok: bool) -> None:
    # just holds each to its pattern first; this is what deploy's script runs before the JSON, as names has.
    result = subprocess.run([str(CHECK), "size", *size], capture_output=True, text=True)
    assert (result.returncode == 0) == ok, result.stderr


# ------------------------------------------------------------ the role, on the fake ---
def _provision(tmp_path: Path, base: str, name: str, size: dict | None = None, **extra) -> dict:
    settings = {**FROM_THE_HOST, "vm_ssh_public_keys": [KEY], **extra.pop("settings", {})}
    given = {"vm_name": name, "deploy_sign_in": False, **({"vm_size": size} if size else {}), **extra}
    return _run_playbook(tmp_path, base, PLAYBOOKS / "provision.yml", "-e", json.dumps(given), settings=settings)


def _vm(base: str, name: str) -> dict:
    listed = next(vm for vm in _pve(base, "GET", "cluster/resources")["data"] if vm.get("name") == name)
    config = _pve(base, "GET", f"nodes/{listed['node']}/qemu/{listed['vmid']}/config")["data"]
    return {"vmid": listed["vmid"], **config}


def _ran(report: dict, name: str, vm: str) -> dict:
    return next((result for task, host, result in _results(report) if task.endswith(name) and host == vm), {})


def test_a_vm_is_created_with_the_size_given_and_tagged_as_sized(tmp_path: Path, fleet: str) -> None:
    report = _provision(tmp_path, fleet, "sized", {"cores": 4, "memory_mb": 8192, "disk": "40G"})
    assert report["rc"] == 0, _failures(report)
    vm = _vm(fleet, "sized")
    assert (int(vm["cores"]), int(vm["memory"]), int(vm["balloon"])) == (4, 8192, 8192)
    assert "size=40G" in vm["scsi0"]
    assert "agent-warehouse-sized" in vm["tags"].split(";") and "agent-warehouse-unfinished" not in vm["tags"]
    # Again with the same size: nothing to refuse, and nothing changed.
    again = _provision(tmp_path, fleet, "sized", {"cores": 4, "memory_mb": 8192})
    assert again["rc"] == 0, _failures(again)
    assert _ran(again, SETTINGS, "sized").get("skipped") is True


def test_a_vm_that_exists_is_never_resized_and_another_size_is_refused(tmp_path: Path, fleet: str) -> None:
    assert _provision(tmp_path, fleet, "kept", {"cores": 2})["rc"] == 0
    report = _provision(tmp_path, fleet, "kept", {"cores": 8, "memory_mb": 2048})
    assert report["rc"] != 0
    said = " ".join(_failures(report).values())
    assert "kept exists, with 2 cores" in said and "change none that exists" in said, said
    assert int(_vm(fleet, "kept")["cores"]) == 2
    # Given no size, an existing VM is left as it is.
    assert _provision(tmp_path, fleet, "kept")["rc"] == 0


def test_a_repair_keeps_a_sized_vm_s_size(tmp_path: Path, fleet: str) -> None:
    assert _provision(tmp_path, fleet, "repaired", {"cores": 6, "memory_mb": 6144})["rc"] == 0
    report = _provision(tmp_path, fleet, "repaired", proxmox_vm_force_update=True)
    assert report["rc"] == 0, _failures(report)
    vm = _vm(fleet, "repaired")
    assert (int(vm["cores"]), int(vm["memory"])) == (6, 6144)
    assert "agent-warehouse-sized" in vm["tags"].split(";")


def test_a_repair_given_part_of_a_size_keeps_the_rest_of_the_vm_s_own(tmp_path: Path, fleet: str) -> None:
    assert _provision(tmp_path / "made", fleet, "partial", {"cores": 6, "memory_mb": 6144, "disk": "40G"})["rc"] == 0
    report = _provision(tmp_path / "repaired", fleet, "partial", {"cores": 8}, proxmox_vm_force_update=True)
    assert report["rc"] == 0, _failures(report)
    vm = _vm(fleet, "partial")
    # Never the settings' 4096 MiB and 32G: the VM's own, but for the cores given.
    assert (int(vm["cores"]), int(vm["memory"]), int(vm["balloon"])) == (8, 6144, 6144) and "size=40G" in vm["scsi0"]


def test_a_vm_whose_run_stopped_before_it_was_finished_is_finished_with_the_next_run_s_size(
    tmp_path: Path, fleet: str
) -> None:
    # Proxmox never shrinks a disk: a size below the template's 3584M fails at the resize, after the settings call.
    for name in ("halted", "resumed"):
        assert _provision(tmp_path / f"{name}-1", fleet, name, {"cores": 6, "disk": "1G"})["rc"] != 0
        tags = _vm(fleet, name)["tags"].split(";")
        assert "agent-warehouse-unfinished" in tags and "agent-warehouse-sized" not in tags, tags
    # Given no size, the settings': its disk resized, and not sized - nothing says the size of a run that stopped.
    report = _provision(tmp_path / "halted-2", fleet, "halted")
    assert report["rc"] == 0, _failures(report)
    vm = _vm(fleet, "halted")
    assert (int(vm["cores"]), int(vm["memory"])) == (2, 4096) and "size=32G" in vm["scsi0"]
    assert not {"agent-warehouse-sized", "agent-warehouse-unfinished"} & set(vm["tags"].split(";"))
    # Given a size, that size, and sized.
    report = _provision(tmp_path / "resumed-2", fleet, "resumed", {"cores": 6, "disk": "40G"})
    assert report["rc"] == 0, _failures(report)
    vm = _vm(fleet, "resumed")
    assert int(vm["cores"]) == 6 and "size=40G" in vm["scsi0"]
    assert "agent-warehouse-sized" in vm["tags"].split(";") and "agent-warehouse-unfinished" not in vm["tags"]


def test_the_settings_minimum_memory_stays_below_a_larger_size(tmp_path: Path, fleet: str) -> None:
    report = _provision(tmp_path, fleet, "ballooned", {"memory_mb": 8192}, settings={"vm_memory_min_mb": 2048})
    assert report["rc"] == 0, _failures(report)
    assert (int(_vm(fleet, "ballooned")["memory"]), int(_vm(fleet, "ballooned")["balloon"])) == (8192, 2048)


def test_a_size_larger_than_the_node_is_refused_before_anything_is_created_where_the_node_is_read(
    tmp_path: Path, fleet: str
) -> None:
    # The token cannot read the node: not checked, and created.
    assert _provision(tmp_path / "blind", fleet, "huge-unread", {"cores": 64})["rc"] == 0
    _pve(fleet, "PUT", "fake/privileges", token="ansible", path="/nodes/pve", privs="Sys.Audit")
    for size in ({"cores": 64}, {"memory_mb": 128 * 1024}):
        report = _provision(tmp_path / str(len(size)), fleet, "huge", size)
        assert report["rc"] != 0
        said = " ".join(_failures(report).values())
        assert "pve has 16 CPU threads and 64.0 GiB of memory" in said and "none was created" in said, said
    assert not [vm for vm in _pve(fleet, "GET", "cluster/resources")["data"] if vm.get("name") == "huge"]
    assert _provision(tmp_path / "fits", fleet, "fits", {"cores": 16, "memory_mb": 64 * 1024})["rc"] == 0


# -------------------------------------------------------------- drift and capacity ---
def test_a_sized_vm_s_size_is_no_drift_from_the_settings() -> None:
    config = {"cores": "8", "memory": "16384", "balloon": "16384", "scsi0": "local-lvm:vm-1-disk-0,size=64G"}
    wanted = {"cores": 2, "memory": 4096, "balloon": 4096, "disk": "32G"}
    drifted = {d["setting"] for d in proxmox.proxmox_vm_drift(config, wanted)["hardware"]}
    assert drifted == {"cores", "memory", "balloon"}
    assert proxmox.proxmox_vm_drift(config, {**wanted, "sized": True})["hardware"] == []
    assert proxmox.proxmox_disk_bytes("local-lvm:vm-1-disk-0,size=64G") == proxmox.proxmox_disk_bytes("64G") == 64 * GIB
    assert proxmox.proxmox_disk_bytes("") == 0


def test_just_status_calls_a_sized_vm_s_size_no_drift(tmp_path: Path, fleet: str) -> None:
    # The tag is read from the config status keeps of the VM: kept without its tags, every sized VM was said to drift.
    assert _provision(tmp_path / "sized", fleet, "sized", {"cores": 4, "memory_mb": 8192})["rc"] == 0
    assert _provision(tmp_path / "plain", fleet, "plain", settings={"vm_cores": 3})["rc"] == 0
    report, model = _status_run(tmp_path / "status", fleet, "-e", json.dumps({"status_only": "vms"}))
    assert report["rc"] in (0, 1, 2), report["stderr"][-2000:]
    vms = {vm["name"]: vm for vm in model["vms"]}
    assert vms["sized"]["drift"]["hardware"] == [], vms["sized"]["drift"]
    assert [d["setting"] for d in vms["plain"]["drift"]["hardware"]] == ["cores"], vms["plain"]["drift"]


NODES = [
    {"node": "pve", "status": "online", "maxcpu": 16, "maxmem": 64 * GIB, "mem": 24 * GIB, "cpu": 0.12},
    {"node": "pve2", "status": "online"},
    {"node": "pve3", "status": "offline"},
]
VMS = [
    {"vmid": 101, "node": "pve", "status": "running", "maxcpu": 4, "maxmem": 8 * GIB},
    # A container: what the node holds, as a VM is.
    {"vmid": 301, "type": "lxc", "node": "pve", "status": "running", "maxcpu": 2, "maxmem": 4 * GIB},
    {"vmid": 102, "node": "pve", "status": "running", "maxcpu": 2, "maxmem": 4 * GIB},
    {"vmid": 103, "node": "pve", "status": "stopped", "maxcpu": 2, "maxmem": 2 * GIB},
    {"vmid": 9000, "node": "pve", "status": "stopped", "maxcpu": 2, "maxmem": 2 * GIB, "template": 1},
    {"vmid": 201, "node": "pve2", "status": "running", "maxcpu": 8, "maxmem": 16 * GIB},
]


def test_capacity_is_each_node_s_threads_and_memory_beside_what_its_vms_hold() -> None:
    pve, pve2, pve3 = S.status_capacity(NODES, VMS)
    assert pve["read"] and (pve["threads"], pve["memory"], pve["memory_free"]) == (16, 64 * GIB, 40 * GIB)
    assert pve["running"] == {"vms": 2, "containers": 1, "vcpus": 8, "memory": 16 * GIB}
    assert pve["unallocated"] == 48 * GIB
    assert pve["stopped"] == {"vms": 1, "containers": 0, "vcpus": 2, "memory": 2 * GIB}
    assert not pve2["read"] and pve2["threads"] is None and pve2["running"]["vcpus"] == 8
    assert pve3["status"] == "offline"
    # How to read a node it cannot, for the token's user - in the JSON an agent reads, too - and on the node: the role
    # alone is not enough where it is granted elsewhere. Nothing for a node it read, or one that is down.
    assert pve["grant"] == [] and pve3["grant"] == []
    # A cluster: just init's run grants it on the one node root SSH reaches, so the lines are the commands, which hold
    # for any node (RP-2).
    assert pve2["grant"] == [
        "to read them, ansible@pve needs Sys.Audit on /nodes/pve2, read-only; as root on a node of the cluster:",
        "pveum role modify AnsibleVM --append 1 --privs Sys.Audit",
        "pveum acl modify /nodes/pve2 --users ansible@pve --roles AnsibleVM",
    ]
    assert S.status_capacity(NODES, VMS, "ops@pam")[1]["grant"][2].endswith("--users ops@pam --roles AnsibleVM")


def test_a_node_of_its_own_is_offered_by_just_init_for_its_host() -> None:
    # A host of one node: just init's run, which adds both by itself, never the commands it runs over root SSH - and
    # with more than one host, the host's, so that it reaches this one whichever is current (RP-1).
    (alone,) = S.status_capacity([{"node": "nuc", "status": "online"}], [], "ansible@pve", " --host nuc")
    assert alone["grant"] == [
        "to read them, ansible@pve needs Sys.Audit on /nodes/nuc, read-only:",
        "just init --host nuc, at a terminal and with root SSH to the host, offers to add it to the role AnsibleVM"
        " and grant it there",
    ]
    (current,) = S.status_capacity([{"node": "pve", "status": "online"}], [])
    assert current["grant"][1].startswith("just init, at a terminal")
    # However long the host's name, the report wraps the line to its width.
    long = S.status_capacity([{"node": "pve", "status": "online"}], [], "", " --host " + "n" * 60)
    model = {
        "title": "agent-warehouse",
        "proxmox": {"nodes": ["pve"]},
        "settings": {},
        "vms": [],
        "sections": ["capacity"],
        "attention": [],
        "counts": {"problem": 0, "unread": 0, "todo": 0},
        "exit": 0,
        "capacity": long,
    }
    text = status_text(model)
    assert all(len(line) <= WIDTH for line in text.splitlines()), text
    assert "just init --host " + "n" * 60 in " ".join(text.split())


def test_the_capacity_section_fits_the_report_and_says_how_to_read_a_node_it_cannot() -> None:
    model = {
        "title": "agent-warehouse",
        "proxmox": {"nodes": ["pve", "pve2"]},
        "settings": {},
        "vms": [],
        "sections": ["capacity"],
        "attention": [],
        "counts": {"problem": 0, "unread": 0, "todo": 0},
        "exit": 0,
        "capacity": S.status_capacity(NODES, VMS),
    }
    text = status_text(model)
    assert all(len(line) <= WIDTH for line in text.splitlines()), text
    assert "16 CPU threads, 12% busy · memory" in text and "24 / 64 GiB, 40 free" in text
    assert "its running VMs and containers hold 8 vCPUs and 16 GiB (25% of its memory)" in text
    assert "its stopped VMs would take 2 vCPUs" in text
    assert "pveum acl modify /nodes/pve2 --users ansible@pve --roles AnsibleVM" in text
    assert "offline: nothing on it can be read" in text


def test_just_status_reads_the_node_where_the_token_may_and_changes_nothing(tmp_path: Path, fleet: str) -> None:
    # Before the grant: the node unread, and how to grant it named for the token's user, proxmox_api_user.
    report, model = _status_run(tmp_path / "before", fleet, "-e", json.dumps({"status_only": "capacity"}))
    assert report["rc"] in (0, 1, 2), report["stderr"][-2000:]
    capacity = {node["node"]: node for node in model["capacity"]}
    assert not capacity["pve"]["read"]
    # The fake lists a cluster, so the lines are the commands, which hold for any node.
    assert "pveum acl modify /nodes/pve --users ansible@pve --roles AnsibleVM" in capacity["pve"]["grant"]
    _pve(fleet, "PUT", "fake/privileges", token="ansible", path="/nodes/pve", privs="Sys.Audit")
    report, model = _status_run(tmp_path / "after", fleet, "-e", json.dumps({"status_only": "vms,capacity"}))
    assert report["rc"] in (0, 1, 2), report["stderr"][-2000:]
    capacity = {node["node"]: node for node in model["capacity"]}
    assert capacity["pve"]["read"] and capacity["pve"]["threads"] == 16 and capacity["pve"]["grant"] == []
    assert capacity["pve"]["running"]["vms"] >= 1
    assert PVE_SECRET not in json.dumps(model)
