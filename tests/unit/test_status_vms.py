"""`just status` inside the VMs: what playbooks/status.yml reads over SSH, and that it changes nothing there.

- vm_status_info (playbooks/library/), run in this process on fixture trees: the OS, the kernels, the reboot flag,
  the load, memory and uptime, apt's simulation, and the user's own claude processes and VS Code windows.
- status.yml with discovery off against stand-in VMs, as the configure scenario reaches its container: each runs
  the claude_code, github_projects and tailscale roles' status entry points and the module with the stand-ins of
  the harness (tests/unit/harness.py), the gh and tailscale fakes (tests/molecule/), and a fixture tree. What the
  report says of each, and that every file of each VM is as it was, but the fakes' own call logs.
- The host key: status's ssh options are ansible.cfg's with StrictHostKeyChecking=yes and UpdateHostKeys=no, they
  reach ssh as given and take effect (ssh -G), and ssh's two refusals of a host key are each said for what they are.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from harness import FAKES, REPO, STAND_INS, _run
from test_status import S, status_root
from test_tailscale import DPKG_QUERY, GETENT, NODE, _joined

LIBRARY = REPO / "playbooks" / "library"


def _module():
    spec = importlib.util.spec_from_file_location("vm_status_info_under_test", LIBRARY / "vm_status_info.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = _module()


# ------------------------------------------------------------------------------------------- the module ----


def _tree(root: Path, *, running="6.8.0-85-generic", installed=("6.8.0-85-generic",), reboot=(), processes=()):
    """A VM's files under root, as vm_status_info reads them."""
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "os-release").write_text('NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.3 LTS"\nVERSION_ID="24.04"\n')
    (root / "proc" / "sys" / "kernel").mkdir(parents=True)
    (root / "proc" / "sys" / "kernel" / "osrelease").write_text(running + "\n")
    (root / "proc" / "loadavg").write_text("0.42 0.31 0.20 1/123 4567\n")
    (root / "proc" / "meminfo").write_text("MemTotal:        4028228 kB\nMemFree: 1 kB\nMemAvailable:    2014114 kB\n")
    (root / "proc" / "uptime").write_text("187200.51 370000.00\n")
    (root / "boot").mkdir()
    for release in installed:
        (root / "boot" / f"vmlinuz-{release}").write_text("")
    if reboot:
        (root / "run").mkdir()
        (root / "run" / "reboot-required").write_text("*** System restart required ***\n")
        (root / "run" / "reboot-required.pkgs").write_text("".join(f"{p}\n" for p in reboot))
    for pid, argv in enumerate(processes, 100):
        (root / "proc" / str(pid)).mkdir()
        (root / "proc" / str(pid) / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")
    return root


def test_the_system_is_read_from_the_files_the_vm_keeps(tmp_path) -> None:
    root = _tree(
        tmp_path / "vm",
        installed=("6.8.0-85-generic", "6.8.0-87-generic", "6.8.0-9-generic"),
        reboot=("linux-image-6.8.0-87-generic", "linux-base", "linux-image-6.8.0-87-generic"),
        processes=(
            ["/home/dev/.local/share/claude/versions/2.1.310"],
            ["claude", "--resume", "x"],
            ["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"],
            ["/home/dev/.vscode-server/cli/servers/Stable-abc/server/node", "--type=extensionHost"],
            ["/home/dev/.vscode-server/cli/servers/Stable-abc/server/node", "out/server-main.js"],
            ["bash"],
        ),
    )
    assert M.os_release(str(root)) == "Ubuntu 24.04.3 LTS"
    # By version, not as text: 6.8.0-9 is older than 6.8.0-87.
    assert M.kernels(str(root)) == {"kernel": "6.8.0-85-generic", "kernel_newest": "6.8.0-87-generic"}
    assert M.reboot(str(root)) == {
        "reboot_required": True,
        "reboot_packages": ["linux-image-6.8.0-87-generic", "linux-base"],
    }
    assert M.load_memory_uptime(str(root)) == {
        "load": [0.42, 0.31, 0.2],
        "memory": {"total": 4028228 * 1024, "available": 2014114 * 1024},
        "uptime": 187200,
    }
    # The native install's binary is named by its version; claude itself, and the npm package under node.
    assert M.processes(str(root)) == {"claude": 2, "vscode_windows": 1}


def test_a_read_that_hangs_is_said_and_the_others_still_land() -> None:
    def hangs():
        import time

        time.sleep(5)

    def fails():
        raise OSError("no such file")

    found, unread = M.run_all({"slow": (hangs, 0.2), "bad": (fails, None), "ok": (lambda: {"os": "x"}, None)}, 1)
    assert found == {"os": "x"}
    assert unread == {"slow": "it took longer than 0.2 s", "bad": "no such file"}


APT = """#!/bin/sh
[ "$*" = "-s -q -o Debug::NoLocking=1 dist-upgrade" ] || { echo "unexpected: $*" >&2; exit 2; }
cat <<'OUT'
NOTE: This is only a simulation!
Inst libssl3t64 [3.0.13-0ubuntu3.5] (3.0.13-0ubuntu3.6 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
Inst curl [8.5.0-2ubuntu10.5] (8.5.0-2ubuntu10.6 Ubuntu:24.04/noble-updates [amd64])
Inst tzdata [2024a-3ubuntu1.1] (2025b-0ubuntu0.24.04 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [all])
Conf libssl3t64 (3.0.13-0ubuntu3.6 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
OUT
"""


def test_upgrades_are_apts_simulation_counted_by_pocket(tmp_path, monkeypatch) -> None:
    apt = tmp_path / "apt-get"
    apt.write_text(APT)
    apt.chmod(0o755)
    monkeypatch.setattr(M, "APT_GET", str(apt))
    assert M.upgrades(5) == {"upgrades": 3, "security": 2}
    apt.write_text("#!/bin/sh\necho 'E: Could not open lock file' >&2\nexit 100\n")
    with pytest.raises(OSError, match="Could not open lock file"):
        M.upgrades(5)


def test_the_module_runs_no_program_but_apt_and_that_by_path() -> None:
    source = (LIBRARY / "vm_status_info.py").read_text()
    assert source.count("subprocess.run(") == 1 and 'APT_GET = "/usr/bin/apt-get"' in source
    assert "run_command" not in source and "os.system" not in source and "Popen" not in source
    assert '"-s"' in source and "Debug::NoLocking=1" in source


# --------------------------------------------------------------------------------- the stand-in VMs ----

SYSTEMCTL = """#!/bin/sh
# Stand-in systemctl: a unit's state from units/<unit> in the host's own directory, as `systemctl show` gives it,
# and every call logged. Nothing else is taken.
echo "$*" >> "$STAND_IN/systemctl.log"
case "$1" in
  show)
    unit=$(printf %s "$2" | tr -d "'")
    if [ -f "$STAND_IN/units/$unit" ]; then cat "$STAND_IN/units/$unit"
    else printf 'Id=%s\\nLoadState=not-found\\nActiveState=inactive\\nSubState=dead\\n' "$unit"; fi ;;
  --version) echo "systemd 255 (255.4-1ubuntu8)" ;;
  *) echo "stand-in systemctl: unexpected arguments: $*" >&2; exit 1 ;;
esac
"""
RUNNING = "LoadState=loaded\nActiveState=active\nSubState=running\nNRestarts=0\nResult=success\n"
FAILED = (
    "LoadState=loaded\nActiveState=failed\nSubState=failed\nNRestarts=5\nResult=exit-code\n"
    "StateChangeTimestamp=Sat 2026-10-24 10:02:11 UTC\n"
)
WAITING = "LoadState=loaded\nActiveState=active\nSubState=waiting\n"
INACTIVE = "LoadState=loaded\nActiveState=inactive\nSubState=dead\nNRestarts=0\n"

# One VM per state status reports. "claude" is the link's version or None for no install, "login" a stored login,
# "units" Remote Control's service and path unit, "gh" the gh stand-in's files, "ts" the tailscale stand-in's.
CASES = {
    "ready": {
        "claude": "2.1.310",
        "login": True,
        "units": {"service": RUNNING},
        # A personal access token: a device-flow one without the workflow scope is a thing to do, rightly.
        "gh": {"fake-token": "ghp_" + "0" * 36, "fake-login": "octocat"},
        "ts": _joined("ready"),
        "marker": NODE,
        "tree": {},
    },
    "fresh": {
        "claude": "2.1.310",
        "login": False,
        "units": {"service": INACTIVE, "path": WAITING},
        "gh": {},
        "ts": {},
        "tree": {},
        "projects": True,
    },
    "down": {
        "claude": "2.1.309",
        "login": True,
        "units": {"service": FAILED},
        "gh": {"fake-token": "ghp_" + "1" * 36, "fake-unchecked": ""},
        "ts": {"daemon-down": ""},
        "tree": {
            "installed": ("6.8.0-85-generic", "6.8.0-87-generic"),
            "reboot": ("linux-image-6.8.0-87-generic",),
            "processes": (["claude"], ["claude", "-c"]),
        },
    },
}
# What a run is told beyond each host's own: become off, as every harness has it - the controller has no sudo to
# give - and Tailscale outside dpkg looked for in each host's own directory.
EXTRA = {
    "claude_vm_discovery": False,
    "ansible_become": False,
    "tailscale_foreign_paths": ["~/foreign/tailscaled"],
    "tailscale_login_desktop": False,
}


def _vms(root: Path, cases: dict) -> dict:
    """Inventory hosts, one per case, whose modules run with the stand-ins in their own HOME; returns hostvars."""
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    for name, text in (
        ("python", STAND_INS["python"]),
        ("systemctl", SYSTEMCTL),
        ("gh", (FAKES / "fake_gh.sh").read_text()),
        ("tailscale", (FAKES / "fake_tailscale.sh").read_text()),
        ("dpkg-query", DPKG_QUERY),
        ("getent", GETENT),
    ):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    hosts = {}
    for name, case in cases.items():
        home = root / name
        home.mkdir()
        (home / "python").symlink_to(bin_dir / "python")
        if case["claude"]:
            versions = home / ".local" / "share" / "claude" / "versions"
            versions.mkdir(parents=True)
            (versions / case["claude"]).write_text("")
            (home / ".local" / "bin").mkdir(parents=True)
            (home / ".local" / "bin" / "claude").symlink_to(versions / case["claude"])
        (home / ".claude").mkdir()
        if case["login"]:
            (home / ".claude" / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "never-read"}}')
        (home / "units").mkdir()
        for unit, text in case["units"].items():
            (home / "units" / f"claude-remote-control.{unit}").write_text(text)
        gh = home / ".config" / "gh"
        gh.mkdir(parents=True)
        for file_name, content in case["gh"].items():
            (gh / file_name).write_text(content)
        (home / "markers").mkdir()
        (home / "dpkg-status").write_text("installed")
        (home / "markers" / "tailscale-installed").write_text("installed by the role\n")
        if case.get("marker"):
            (home / "markers" / "tailscale-joined").write_text(f"nodekey:{case['marker']}\n")
        (home / ".tailscale-fake").mkdir()
        for file_name, content in case["ts"].items():
            (home / ".tailscale-fake" / file_name).write_text(content)
        _tree(home / "system", **case["tree"])
        if case.get("projects"):
            # A checkout of the VM user's with a commit on no remote: WORK's to say, never to change.
            from test_status_work import GIT_ENV

            checkout = home / "projects" / "proj"
            checkout.mkdir(parents=True)
            env = {**os.environ, **GIT_ENV}
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=checkout, check=True, env=env)
            (checkout / "README").write_text("proj\n")
            subprocess.run(["git", "add", "README"], cwd=checkout, check=True, env=env)
            subprocess.run(["git", "commit", "-q", "-m", "start"], cwd=checkout, check=True, env=env)
        hosts[name] = {
            "ansible_connection": "local",
            "ansible_python_interpreter": str(home / "python"),
            "ansible_host": "192.0.2.10",
            "claude_code_home": str(home),
            "github_projects_home": str(home),
            "tailscale_marker_dir": str(home / "markers"),
            "status_system_root": str(home / "system"),
            "ansible_inject_invocation": True,
        }
    return hosts


def _files(root: Path) -> dict:
    """Every file under root and what it holds, but the stand-ins' own logs and state, which they keep themselves."""
    kept = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_dir() or {".tailscale-fake", "calls.log", "systemctl.log", "ssh.log"} & set(relative.parts):
            continue
        kept[str(relative)] = path.readlink() if path.is_symlink() else path.read_bytes()
    return kept


@pytest.fixture(scope="module")
def read(tmp_path_factory):
    """status.yml over the stand-in VMs: its report, the model, the VMs' files before and after, and the root."""
    tmp_path = tmp_path_factory.mktemp("status-vms")
    root, playbook = status_root(tmp_path, "https://127.0.0.1:9")
    hosts = _vms(tmp_path / "vms", CASES)
    before = _files(tmp_path / "vms")
    report = _run(root, playbook, hosts, "--check", "-e", json.dumps(EXTRA), "--forks", str(len(hosts)), timeout=300)
    model = None
    for play in report["plays"]:
        for task in play["tasks"]:
            if task["task"]["name"] == "Rank what needs attention":
                model = task["hosts"]["localhost"]["ansible_facts"]["status_report"]
    return report, model, before, _files(tmp_path / "vms"), tmp_path / "vms"


def _by(model, name):
    return next(vm for vm in model["vms"] if vm["name"] == name)


def _items(model, name):
    return [(it["level"], it["source"], it["text"], it["command"]) for it in model["attention"] if it["vm"] == name]


def test_status_reads_inside_every_vm_and_changes_nothing_there(read) -> None:
    report, model, before, after, vms = read
    assert report["rc"] == 0, report["stderr"][-3000:]
    assert [vm["name"] for vm in model["vms"]] == ["down", "fresh", "ready"]
    assert after == before
    # The stand-ins' own logs: the reads asked for, and nothing else.
    for name in CASES:
        gh = (vms / name / ".config" / "gh" / "calls.log").read_text().splitlines()
        assert set(gh) <= {"auth status --hostname github.com --json hosts", "auth token --hostname github.com"}, gh
        tailscale = (vms / name / ".tailscale-fake" / "calls.log").read_text().splitlines()
        assert set(tailscale) <= {"status --json", "debug prefs"}, tailscale
        assert {line.split()[0] for line in (vms / name / "systemctl.log").read_text().splitlines()} <= {
            "show",
            "--version",
        }


def test_a_vm_signed_in_to_everything_has_nothing_to_do(read) -> None:
    _, model, *_ = read
    ssh = _by(model, "ready")["ssh"]
    assert ssh["reached"] and ssh["claude"]["version"] == "2.1.310" and ssh["claude"]["login_stored"]
    assert ssh["github"]["account"] == "octocat" and ssh["github"]["signed_in"]
    assert ssh["tailscale"]["case"] == "Running"
    assert ssh["system"]["os"] == "Ubuntu 24.04.3 LTS" and ssh["system"]["load"] == [0.42, 0.31, 0.2]
    assert _items(model, "ready") == []
    # And no token, no login link, no key reached the report, nor anything the run said: every module's arguments
    # are in its result (ansible_inject_invocation), so a token rendered outside no_log would be here.
    report = read[0]
    for case in CASES.values():
        token = case["gh"].get("fake-token")
        if token:
            assert token not in json.dumps(model) and token not in json.dumps(report), token
    assert "never-read" not in json.dumps(model) and "never-read" not in json.dumps(report)


def test_a_vm_just_made_is_told_its_three_sign_ins(read) -> None:
    _, model, *_ = read
    items = _items(model, "fresh")
    sources = [source for _, source, _, _ in items]
    assert sources == ["claude_code", "github_projects", "tailscale"], items
    assert all(level == "todo" for level, *_ in items)
    assert "just login fresh --only claude" in items[0][2]
    # The roles' own lines, as just login says them.
    assert "just login fresh --only github" in items[1][2]
    assert "just login fresh --only tailscale" in items[2][2]
    text = S.status_text(model)
    row = next(line for line in text.splitlines() if line.startswith(" fresh") and "not signed in" in line)
    assert "waits for the login" in row and "needs a login" in row
    # Its checkout's commit is on no remote: shown in WORK, and never counted.
    work = _by(model, "fresh")["ssh"]["work"]
    assert work["checkouts"] == 1 and work["repositories"][0]["commits_on_no_remote"] == 1
    row = next(line for line in text.splitlines() if line.startswith(" fresh") and "1 checkout" in line)
    assert "proj: 1 commit on 1 branch" in row
    assert all("proj" not in it["text"] for it in model["attention"])


def test_what_is_down_and_what_was_not_checked_are_said_for_what_they_are(read) -> None:
    _, model, *_ = read
    items = _items(model, "down")
    levels = {text.split(":")[0]: level for level, _, text, _ in items}
    assert levels["Remote Control is not running"] == "problem"
    assert levels["gh could not check its login with GitHub"] == "unread"
    assert levels["tailscaled is not running"] == "problem"
    reboot = next(text for _, _, text, _ in items if text.startswith("waits for a reboot"))
    assert (
        reboot == "waits for a reboot: kernel 6.8.0-87 is installed, 6.8.0-85 runs; 2 claude processes are open there"
    )
    assert model["exit"] == 2
    row = next(line for line in S.status_text(model).splitlines() if line.startswith(" down") and "restarts" in line)
    assert "5 restarts" in row and "? not checked" in row


# ------------------------------------------------------------------------------------------- host keys ----


def test_a_run_never_reaches_this_machines_gh_or_claude() -> None:
    from harness import GUARDED_TOOLS

    # A VM's `which gh` in a stand-in host without one would find this machine's own, signed in as the developer.
    assert {"gh", "claude", "tailscale"} <= set(GUARDED_TOOLS)


def test_status_ssh_options_are_ansible_cfgs_with_the_host_key_held() -> None:
    import configparser

    cfg = configparser.ConfigParser()
    cfg.read(REPO / "ansible.cfg")
    base = cfg["ssh_connection"]["ssh_args"].split()
    defaults = (REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text()
    expression = yaml.safe_load(defaults)["status_ssh_args"]
    assert "lookup('ansible.builtin.config', 'ssh_args'" in expression
    kept = [o for o in " ".join(base).split("-o ") if o.strip() and not o.startswith("StrictHostKeyChecking")]
    expected = " ".join(f"-o {o.strip()}" for o in kept) + " -o StrictHostKeyChecking=yes -o UpdateHostKeys=no"
    # The same text the expression makes, as Jinja makes it, worked out the same way here.
    import re

    made = re.sub(r"-o\s*StrictHostKeyChecking=\S+", "", " ".join(base))
    made = re.sub(r"\s+", " ", re.sub(r"-o\s*UpdateHostKeys=\S+", "", made)).strip()
    assert made + " -o StrictHostKeyChecking=yes -o UpdateHostKeys=no" == expected


@pytest.mark.skipif(shutil.which("ssh") is None, reason="needs ssh -G")
def test_the_options_reach_ssh_and_take_effect(tmp_path, fake_pve) -> None:
    # The fake's VMs refuse through the stand-in ssh, which records how it was called.
    from test_status import _status_run

    report, model = _status_run(tmp_path, fake_pve)
    assert report["rc"] == 0, report["stderr"][-2000:]
    calls = [json.loads(line) for line in (tmp_path / "run" / "ssh.log").read_text().splitlines()]
    assert calls, "status never called ssh"
    for argv in calls:
        options = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-o"]
        strict = [o for o in options if o.lower().startswith("stricthostkeychecking=")]
        # ssh keeps the first value it is given: status's comes first, whatever Ansible adds after it.
        assert strict and strict[0] == "StrictHostKeyChecking=yes", argv
        assert "UpdateHostKeys=no" in options, argv
        given = [arg for option in options for arg in ("-o", option)]
        resolved = subprocess.run(
            ["ssh", "-G", "-F", "/dev/null", *given, "192.0.2.60"], capture_output=True, text=True, check=True
        ).stdout
        assert "stricthostkeychecking true" in resolved and "updatehostkeys false" in resolved


REFUSALS = {
    "unknown": (
        "No ED25519 host key is known for 192.0.2.60 and you have requested strict checking.\r\n"
        "Host key verification failed.",
        "holds no SSH host key for 192.0.2.60, and status adds none",
    ),
    "changed": (
        "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\r\n"
        "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\r\n"
        "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\r\n"
        "IT IS POSSIBLE THAT SOMEONE IS DOING SOMETHING NASTY!\r\nHost key verification failed.",
        "the SSH host key at 192.0.2.60 is not the one this machine holds",
    ),
}


@pytest.mark.parametrize("refusal", sorted(REFUSALS))
def test_each_host_key_refusal_is_said_for_what_it_is(tmp_path, fake_pve, refusal) -> None:
    from test_status import _status_run, status_root

    said, expected = REFUSALS[refusal]
    root, _ = status_root(tmp_path, fake_pve)
    (root / "ssh-says").write_text(said + "\n")
    report, model = _status_run(tmp_path, fake_pve, "-e", json.dumps({"vm_name": "alpha"}))
    assert report["rc"] == 0, report["stderr"][-2000:]
    item = next(it for it in model["attention"] if it["vm"] == "alpha" and it["text"].startswith("not read"))
    assert item["level"] == "unread" and expected in item["text"], item
    assert ("ssh-keygen -R 192.0.2.60" in item["command"]) == (refusal == "changed")


@pytest.fixture
def fake_pve(tmp_path):
    from test_status import fake_fleet

    yield from fake_fleet(tmp_path)


def test_only_the_api_sections_never_open_ssh(tmp_path, fake_pve) -> None:
    from test_status import _status_run

    report, model = _status_run(tmp_path, fake_pve, settings={"status_only": "vms,storage"})
    assert report["rc"] == 0, report["stderr"][-2000:]
    assert not (tmp_path / "run" / "ssh.log").exists()
    assert all(not vm.get("needs_ssh") for vm in model["vms"])
