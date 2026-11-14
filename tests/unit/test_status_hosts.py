"""just status across the checkout's hosts (lib/claude_vm/status/hosts.py): one child per host - today's engine, with
--emit - and one report, against two fake Proxmox hosts, each with its own token's secret, and a refusing ssh.

What it holds: every host's VMs read and said under their host, the JSON's promised keys still the current host's;
a host that does not answer costs only its own block, and fails the run only as the current one (plan D4); a name two
hosts each have, and an alias recorded for a host that lists no VM of its name, said under ATTENTION; each host's
settings kept in a folder of its own; and the creating deploy's question, which names each host has (--taken).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import harness
import pytest
import yaml
from harness import REPO, _env, _executable, _fail_if_the_desktop_was_reached, _free_port
from test_status import SSH, STATUS

FIRST_SECRET, NUC_SECRET = "first-fake-status-secret", "nuc-fake-status-secret"


def _fake(directory: Path, secret: str):
    """A fake Proxmox with its own secret; a generator of its base URL, closed to stop it."""
    directory.mkdir(parents=True, exist_ok=True)
    cert, key = harness._certificate(directory)
    served = harness._serve(harness.FAKES / "fake_pve_api.py", str(cert), str(key), str(directory / "pve"), secret)
    return served, next(served).replace("http://", "https://")


@pytest.fixture
def two(tmp_path, monkeypatch):
    """Two fake hosts: the first with alpha and shared, nuc with beta and shared - one name on both."""
    made = []
    bases = {}
    try:
        for name, secret, vms in (
            ("first", FIRST_SECRET, [(9010, "alpha"), (9012, "shared")]),
            ("nuc", NUC_SECRET, [(9020, "beta"), (9022, "shared")]),
        ):
            served, base = _fake(tmp_path / f"fake-{name}", secret)
            made.append(served)
            monkeypatch.setattr(harness, "PVE_SECRET", secret)
            # The fake's node that is down, and its guests, are another test's: up, and empty, here.
            harness._pve(base, "PUT", "fake/nodes/pve2", status="online")
            for vmid in (8000, 8001):
                harness._pve(base, "DELETE", f"nodes/pve2/qemu/{vmid}")
            for vmid, vm in vms:
                harness._pve_vm(base, vmid, vm, running=False, tags="agent-warehouse", ciuser="dev")
            bases[name] = int(base.rsplit(":", 1)[1])
            # What a run asks is logged from here on: the fixture's own requests are not the run's.
            (tmp_path / f"fake-{name}" / "pve" / "calls.log").unlink()
        yield bases
    finally:
        for served in made:
            served.close()


def _checkout(root: Path, ports: dict) -> list[dict]:
    """The first host in root/inventory, nuc in root/hosts/nuc, at the fakes' ports; the hosts as tools/check host
    every gives them."""
    inventory = root / "inventory"
    (inventory / "group_vars" / "all").mkdir(parents=True)
    shutil.copy(REPO / "inventory" / "controller.yml", inventory / "controller.yml")
    (inventory / "group_vars" / "all" / "defaults.yml").symlink_to(
        REPO / "inventory" / "group_vars" / "all" / "defaults.yml"
    )
    proxmox = {"all": {"children": {"proxmox": {"hosts": {"pve": {"ansible_host": "127.0.0.1"}}}}}}
    (inventory / "hosts.yml").write_text(yaml.safe_dump(proxmox))
    shared = {
        "proxmox_api_port": ports["first"],
        "vault_proxmox_api_token_secret": FIRST_SECRET,
        "vm_user": "dev",
        "vm_ssh_public_keys": [],
        "claude_ssh_config_file": str(root / "ssh.conf"),
        "ansible_ssh_executable": str(root / "ssh"),
        "ansible_control_path_dir": str(root / "cp"),
        "status_read_upgrades": False,
    }
    (inventory / "group_vars" / "all" / "fake_api.yml").write_text(yaml.safe_dump(shared))
    nuc = root / "hosts" / "nuc"
    (nuc / "group_vars" / "all").mkdir(parents=True)
    proxmox["all"]["children"]["proxmox"]["hosts"] = {"nuc": {"ansible_host": "127.0.0.1"}}
    (nuc / "hosts.yml").write_text(yaml.safe_dump(proxmox))
    own = {"proxmox_api_port": ports["nuc"], "vault_host_proxmox_api_token_secret": NUC_SECRET}
    (nuc / "group_vars" / "all" / "fake_api.yml").write_text(yaml.safe_dump(own))
    _executable(root / "ssh", SSH)
    if not (root / "python").exists():
        (root / "python").symlink_to(sys.executable)
    return [
        {"name": "pve", "current": True, "sources": [str(inventory)], "vault_files": [], "problem": ""},
        {
            "name": "nuc",
            "current": False,
            "sources": [str(inventory / "controller.yml"), str(nuc)],
            "vault_files": [],
            "problem": "",
        },
    ]


def _status(root: Path, hosts: list[dict], *extra: str) -> tuple[subprocess.CompletedProcess, dict | None]:
    done = subprocess.run(
        [sys.executable, str(STATUS), "--hosts", json.dumps(hosts), "--json", *extra],
        cwd=root,
        env=_env(root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    _fail_if_the_desktop_was_reached(root)
    try:
        return done, json.loads(done.stdout)
    except ValueError:
        return done, None


def test_every_host_is_read_by_a_child_of_its_own_and_said_under_its_name(tmp_path, two) -> None:
    root = tmp_path / "run"
    hosts = _checkout(root, two)
    done, report = _status(root, hosts)
    assert report is not None, done.stderr[-2000:]
    assert FIRST_SECRET not in done.stdout + done.stderr and NUC_SECRET not in done.stdout + done.stderr
    assert [(one["host"], one["current"], one["failed"]) for one in report["hosts"]] == [
        ("pve", True, None),
        ("nuc", False, None),
    ]
    assert sorted((vm["host"], vm["name"]) for vm in report["vms"]) == [
        ("nuc", "beta"),
        ("nuc", "shared"),
        ("pve", "alpha"),
        ("pve", "shared"),
    ]
    # What 1.0 promised of a single object is the current host's.
    assert report["proxmox"] == report["hosts"][0]["report"]["proxmox"]
    # Each fake was asked by its own host's run alone, with its own secret: the other's would be refused.
    for fake in ("first", "nuc"):
        calls = [
            json.loads(line) for line in (tmp_path / f"fake-{fake}" / "pve" / "calls.log").read_text().splitlines()
        ]
        assert calls and all(call["method"] == "GET" for call in calls)


def test_a_name_two_hosts_have_is_a_problem_said_under_each(tmp_path, two) -> None:
    root = tmp_path / "run"
    done, report = _status(root, _checkout(root, two))
    assert report is not None, done.stderr[-2000:]
    shared = [it for it in report["attention"] if it["vm"] == "shared" and "a VM of this name too" in it["text"]]
    assert sorted(it["host"] for it in shared) == ["nuc", "pve"] and all(it["level"] == "problem" for it in shared)
    assert report["exit"] == 1 and [one["exit"] for one in report["hosts"]] == [1, 1]


def test_an_alias_recorded_for_a_host_that_lists_no_such_vm_is_a_line_with_its_prune(tmp_path, two) -> None:
    root = tmp_path / "run"
    hosts = _checkout(root, two)
    (root / "ssh.conf").write_text(
        "# BEGIN agent-warehouse: gone\n# origin: nuc\nHost gone\n    HostName 192.0.2.9\n# END agent-warehouse: gone\n"
    )
    done, report = _status(root, hosts)
    assert report is not None, done.stderr[-2000:]
    (stale,) = [it for it in report["attention"] if it["vm"] == "gone"]
    assert stale["host"] == "nuc" and stale["command"] == "just deploy --only ssh-config --host nuc"


def test_a_host_that_does_not_answer_costs_its_block_alone(tmp_path, two) -> None:
    root = tmp_path / "run"
    hosts = _checkout(root, {**two, "nuc": _free_port()})
    done, report = _status(root, hosts)
    assert report is not None, done.stderr[-2000:]
    nuc = report["hosts"][1]
    assert nuc["report"]["proxmox"].get("unread")
    # Another than the current host: a line and a note, never the run's exit.
    assert [vm["name"] for vm in report["vms"]] == ["alpha", "shared"]
    assert report["exit"] == 0 and done.returncode == 0
    # The current host not answering is the run's: 2.
    hosts[0]["current"], hosts[1]["current"] = False, True
    done, report = _status(root, hosts)
    assert report is not None and report["exit"] == 2 and done.returncode == 2


def test_a_host_that_cannot_be_read_is_said_in_its_block(tmp_path, two) -> None:
    root = tmp_path / "run"
    hosts = _checkout(root, two)
    hosts[1]["problem"] = "not set up: just init --host nuc"
    done, report = _status(root, hosts)
    assert report is not None, done.stderr[-2000:]
    assert report["hosts"][1]["failed"] == "not set up: just init --host nuc"
    text = subprocess.run(
        [sys.executable, str(STATUS), "--hosts", json.dumps(hosts)],
        cwd=root,
        env=_env(root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    ).stdout
    assert "nuc · cannot be read: not set up: just init --host nuc" in text
    assert "▸ pve · pve, Proxmox VE" in text and "2 hosts" in text.splitlines()[0]


def test_each_child_reads_its_host_alone_and_keeps_what_it_can_in_a_folder_of_its_own() -> None:
    import argparse

    from claude_vm.status import hosts as status_hosts

    opts = argparse.Namespace(
        names="", only="vms", cache="/c", vault_password_file=".vault_pass", extra=[], private_key=""
    )
    others = {"claude_hosts_other": [{"name": "pve", "address": "192.0.2.10"}]}
    host = {
        "name": "nuc",
        "sources": ["inventory/controller.yml", "hosts/nuc"],
        "vault_files": ["a.yml", "b.yml"],
        "extra": others,
    }
    argv = status_hosts.command(opts, host)
    assert argv[1:] == [
        str(STATUS),
        "--emit",
        "--only",
        "vms",
        "--cache",
        "/c/nuc",
        "--vault-file",
        "a.yml",
        "--vault-file",
        "b.yml",
        "--vault-password-file",
        ".vault_pass",
        # The other hosts, as every run against nuc is handed them: its lines then say --host nuc. Never as -e, which
        # would keep no settings.
        "--host-extra",
        json.dumps(others),
        "-i",
        "inventory/controller.yml",
        "-i",
        "hosts/nuc",
    ]


def test_each_line_says_its_host_where_its_command_ends_before_what_it_does() -> None:
    from claude_vm.status import model

    settings = {"host_flag": " --host nuc", "vm_user": "dev", "aliases_enabled": True}
    service = {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead"}
    vm = {
        "name": "alpha",
        "unfinished": True,
        "state": "running",
        "address": "192.0.2.5",
        "alias": False,
        "api": {},
        "needs_ssh": True,
        "ssh": {
            "tailscale": {"enabled": True, "case": "check-daemon-down"},
            "claude": {"installed": True, "login_stored": True, "remote_control_wanted": True, "service": service},
        },
    }
    items = model._vm_items(vm, settings, False) + model._ssh_items(vm, settings, False, 0)
    commands = [item["command"] for item in items]
    assert "just deploy alpha --host nuc" in commands
    assert "just deploy --only ssh-config --host nuc" in commands
    assert "just deploy alpha --tags tailscale --host nuc starts it" in commands
    assert "just deploy alpha --tags claude_code --host nuc starts it, or says what stops it" in commands
    # With one host, as they always were.
    alone = {**settings, "host_flag": ""}
    items = model._vm_items(vm, alone, False) + model._ssh_items(vm, alone, False, 0)
    commands = [item["command"] for item in items]
    assert "just deploy alpha --tags tailscale starts it" in commands and not any("--host" in c for c in commands)


def test_a_host_said_on_one_line_is_wrapped_to_the_reports_width_and_cut_to_the_frames() -> None:
    from claude_vm.status import hosts as status_hosts

    said = "nuc · cannot be read: " + "a reason that goes on " * 12
    whole = status_hosts._said(said, None).split("\n")
    assert len(whole) > 1 and all(len(line) <= 100 for line in whole)
    assert " ".join(whole) == said.strip()
    assert "\n" not in status_hosts._said(said, 80) and len(status_hosts._said(said, 80)) <= 80


def test_an_alias_that_records_no_host_whose_vm_no_host_lists_is_said_on_the_current_host() -> None:
    from claude_vm.status import hosts as status_hosts

    def report(*names, **proxmox):
        return {"vms": [{"name": n} for n in names], "proxmox": proxmox, "settings": {"names": []}, "attention": []}

    blocks = [{"name": "old", "origin": ""}, {"name": "alpha", "origin": ""}]
    reports = {"pve": report("alpha"), "nuc": report("beta")}
    status_hosts.reconcile(reports, blocks, "nuc")
    (said,) = reports["nuc"]["attention"]
    assert said["vm"] == "old" and "its SSH alias records no host" in said["text"] and reports["pve"]["attention"] == []
    # A host not read: it may have that VM, so nothing is said.
    reports = {"pve": report("alpha"), "nuc": report(unread="it did not answer")}
    status_hosts.reconcile(reports, blocks, "pve")
    assert not any(item["vm"] == "old" for item in reports["pve"]["attention"])


def test_a_creating_deploy_is_told_which_host_has_each_name(tmp_path, two) -> None:
    root = tmp_path / "run"
    hosts = _checkout(root, {**two, "nuc": two["nuc"]})
    hosts.append({**hosts[1], "name": "lab", "current": False})
    hosts[2]["sources"] = hosts[1]["sources"]
    done = subprocess.run(
        [
            sys.executable,
            str(STATUS),
            "--taken",
            "--hosts",
            json.dumps(hosts[1:]),
            "--names",
            "beta,new",
            "--only",
            "vms",
        ],
        cwd=root,
        env=_env(root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert sorted(done.stdout.splitlines()) == ["taken beta lab", "taken beta nuc"]
