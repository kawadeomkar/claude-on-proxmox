"""Remote Control's decisions, run for real against canned `claude auth status` output.

Nothing here can reach a VM, a real `claude` or a real systemd. Every inventory
host is the controller itself, over the local connection, through the harness
in harness.py: an interpreter wrapper puts stand-ins for `bash`, `claude` and
`systemctl` first on the module's PATH and points them at that host's canned
login. The stand-in bash answers only the exact login-shell commands the role
runs, so nothing is handed to a profile that could find a real `claude` on the
developer's machine.

What these guard:

- `just login --only claude` decided "signed in" from `loggedIn` alone, while the role
  also requires a claude.ai login with no API key in the way, so a VM signed in
  any other way was sent back and forth between the two.
- The login command it printed ran `claude` in a non-login shell, where the
  native install is not on PATH.
- Its report named `just deploy --only configure` for every VM it did not sign in, and told
  a VM on the API path to remove the key the vault writes back; now configure
  is named only where it is what starts Remote Control, and Remote Control off
  on purpose is said as such.
- `just deploy --check` failed on every VM without the Remote Control unit, because
  systemd_service rejects a missing unit even in check mode.
- A settings.json that disables Remote Control (a telemetry opt-out, an
  apiKeyHelper, an ANTHROPIC_BASE_URL pointing elsewhere) failed the whole
  host, github_projects included, instead of leaving the service stopped and
  naming the keys and the off switch. Deleting that check, or inverting it,
  would ship a server that stays up and never appears in the app.
- `spawn: worktree` against a directory that is not a git repository is
  refused with a message that says so, rather than degrading to the generic
  "did not stay up" after the server has exited.
- settings.yml treats an empty or whitespace-only settings.json as `{}` once it
  has something to merge, names the file when it is not a JSON object, and
  leaves a broken file alone when nothing is managed.
- The harness itself: a run inherits only what it names from the shell running
  pytest, git and gh on a stand-in host never reach the developer's config, the
  Galaxy collections resolve from wherever ANSIBLE_COLLECTIONS_PATH says, and
  no run reads the terminal pytest runs at.

The harness's names are imported here for the test modules that still import
them from this one (test_github_auth.py among them).
"""

from __future__ import annotations

import grp
import json
import os
import pty
import pwd
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from harness import (
    DESKTOP_TOOLS,
    LOGINS,
    PASSED_ON,
    REPO,
    _env,
    _failures,
    _left_to_do,
    _messages,
    _results,
    _run,
    _run_at_a_terminal,
    _seed_settings,
    _stand_in_hosts,
    _take_the_terminal,
)

UNIT = Path("/etc/systemd/system/claude-remote-control.service")
LOGIN_COMMAND = "ssh -t dev@192.0.2.10 \"bash -lc 'claude auth login'\""
# A canned key for the hosts on the API path; never a real one.
API_KEY = "sk-ant-api03-fake-key-0000"

# The env keys the role refuses in settings.json, read from the role so every
# one of them is seeded below.
BLOCKING_ENV = yaml.safe_load((REPO / "roles" / "claude_code" / "vars" / "main.yml").read_text())[
    "claude_code_remote_control_blocking_env"
]
assert BLOCKING_ENV, "the role's blocking env list is empty"

# settings.json contents seeded on a signed-in VM, and the names the blocker
# message must list for each: exactly these, nothing else. The good
# ANTHROPIC_BASE_URL forms are the ones a user types: bare, trailing slash,
# port and path.
SETTINGS = {
    "clean": ({"theme": "dark", "env": {"KEEP_ME": "1"}}, []),
    **{f"env-{key.lower()}": ({"env": {key: "1"}}, [key]) for key in BLOCKING_ENV},
    "api-key-helper": ({"apiKeyHelper": "/home/dev/bin/key.sh"}, ["apiKeyHelper"]),
    "base-url": ({"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}}, []),
    "base-url-slash": ({"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com/"}}, []),
    "base-url-port-path": ({"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com:443/v1"}}, []),
    "base-url-proxy": ({"env": {"ANTHROPIC_BASE_URL": "http://proxy.internal:8080"}}, ["ANTHROPIC_BASE_URL"]),
    "everything": (
        {
            "apiKeyHelper": "/home/dev/bin/key.sh",
            "env": {
                "DO_NOT_TRACK": "1",
                "KEEP_ME": "1",
                "CLAUDE_CODE_USE_BEDROCK": "1",
                "ANTHROPIC_BASE_URL": "http://proxy.internal:8080",
            },
        },
        ["DO_NOT_TRACK", "CLAUDE_CODE_USE_BEDROCK", "apiKeyHelper", "ANTHROPIC_BASE_URL"],
    ),
}

# Signed in, but in a way Remote Control cannot use: the one state in which
# just deploy --only configure, after the user has fixed the login, is what starts it.
INELIGIBLE = sorted(set(LOGINS) - {"claude-ai", "logged-out"})
# Remote Control off on purpose, beside the canned logins: on the API path (a
# key from the vault, which claude reports as its login), and switched off.
OFF_ON_PURPOSE = {
    "api-path": (LOGINS["api-key"], {"claude_code_anthropic_api_key": API_KEY}),
    "switched-off": (LOGINS["setup-token"], {"claude_code_remote_control": False}),
}


@pytest.fixture(scope="module")
def login(tmp_path_factory):
    """claude_login.yml, run once for every canned login: the stand-ins' root, and what it told each host."""
    root = tmp_path_factory.mktemp("claude_login")
    hosts = _stand_in_hosts(root, {**LOGINS, **{name: login for name, (login, _) in OFF_ON_PURPOSE.items()}})
    for name, (_, hostvars) in OFF_ON_PURPOSE.items():
        hosts[name].update(hostvars)
    _seed_settings(root, "claude-ai-telemetry", {"env": {"DISABLE_TELEMETRY": "1"}})
    report = _run(
        root,
        REPO / "playbooks" / "claude_login.yml",
        hosts,
        "-e",
        # become off only because the controller has no sudo to give; the role
        # reads the login as claude_code_user through it on a VM.
        '{"claude_vm_discovery": false, "ansible_become": false}',
    )
    assert report["rc"] == 0, _failures(report)
    assert API_KEY not in json.dumps(report)
    return root, _messages(report, "Report the login state and what to do about it")


def test_claude_login_calls_only_a_claude_ai_login_with_no_key_ready(login):
    _, messages = login
    assert set(messages) == set(LOGINS) | set(OFF_ON_PURPOSE)
    assert {host for host, msg in messages.items() if "already signed in" in msg} == {"claude-ai"}, messages
    assert "claude-ai: already signed in as dev@example.com (claude.ai, max plan)." in messages["claude-ai"]
    assert "auth login" not in messages["claude-ai"]


@pytest.mark.parametrize("host", sorted(set(LOGINS) - {"claude-ai"}))
def test_claude_login_prints_a_login_command_that_finds_claude(login, host):
    _, messages = login
    # Ends the message: an old one put a full stop after the closing quote,
    # which pasting turns into part of the remote command.
    assert messages[host].endswith(LOGIN_COMMAND), messages[host]


@pytest.mark.parametrize("host", sorted(set(LOGINS) | set(OFF_ON_PURPOSE)))
def test_make_configure_is_named_only_where_it_is_what_starts_remote_control(login, host):
    # After a login fixed by hand no path unit waits, so just deploy --only configure starts
    # Remote Control; anywhere else - signed in already, a sign-in that starts
    # it itself, or Remote Control off on purpose - naming it costs the user a
    # configure run that changes nothing.
    _, messages = login
    if host in INELIGIBLE:
        assert f"Then just deploy {host} --tags claude_code starts it." in messages[host]
    else:
        assert not re.search(r"just deploy \S+ (--only configure|--tags )", messages[host]), messages[host]
        assert "Then" not in messages[host], messages[host]


@pytest.mark.parametrize(
    ("host", "advice"),
    [
        ("logged-out", "not signed in"),
        ("claude-ai-with-key", 'an API key from "ANTHROPIC_API_KEY"'),
        ("console", '"claude auth logout" if it came from a Console login'),
        ("api-key", 'an API key from "ANTHROPIC_API_KEY"'),
        ("api-key-no-source", 'signed in through "api_key"'),
        ("setup-token", "CLAUDE_CODE_OAUTH_TOKEN"),
        ("claude-ai-telemetry", "disables Remote Control through DISABLE_TELEMETRY"),
    ],
)
def test_claude_login_says_what_is_in_the_way(login, host, advice):
    _, messages = login
    assert advice in messages[host]


def test_remote_control_off_on_purpose_is_said_as_such(login):
    _, messages = login
    on_the_api_path = messages["api-path"]
    assert on_the_api_path.startswith("api-path: signed in. Remote Control is off on this VM on purpose:")
    assert "vault_anthropic_api_key" in on_the_api_path
    # Not the advice for a key the project did not write: the vault writes this
    # one back on the next run.
    assert "Remove it wherever it is set" not in on_the_api_path
    assert "auth login" not in on_the_api_path
    assert messages["switched-off"] == (
        "switched-off: signed in. Remote Control is off on this VM on purpose: claude_remote_control is false."
    )


def test_claude_login_reads_the_status_through_a_login_shell(login):
    root, _ = login
    for host in LOGINS:
        assert (root / host / "bash.log").read_text() == "-lc claude auth status\n"


def _require_daemon_user() -> None:
    """Skip unless the role can be pointed at a user that exists here and that check mode never chowns to."""
    # A user and group that exist under this name on both macOS and Ubuntu.
    try:
        pwd.getpwnam("daemon")
        grp.getgrnam("daemon")
    except KeyError:
        pytest.skip("no daemon user and group to stand in for the VM user")


def _check_run(root: Path, tasks_from: str, hosts: dict, **role_vars) -> dict:
    """One file of the claude_code role in check mode; each host's claude_code_home is its stand-in directory."""
    for name, hostvars in hosts.items():
        hostvars["claude_code_home"] = str(root / name)
    play = {
        "name": f"Dry-run {tasks_from}.yml",
        "hosts": "claude_vms",
        "gather_facts": False,
        "tasks": [
            {
                "name": f"Run the role's {tasks_from} tasks",
                "ansible.builtin.include_role": {"name": "claude_code", "tasks_from": tasks_from},
                "vars": {"claude_code_user": "daemon", **role_vars},
            }
        ],
    }
    playbook = root / "check.yml"
    playbook.write_text(yaml.safe_dump([play]))
    # become is off only because the controller has no sudo to give; nothing
    # under test depends on the user a task runs as.
    return _run(root, playbook, hosts, "--check", "-e", '{"ansible_become": false}')


@pytest.fixture(scope="module")
def check_mode(tmp_path_factory):
    """remote_control.yml in check mode, once for every login and seeded settings.json: root, hosts and the report."""
    if UNIT.exists():
        pytest.skip("this machine has a real claude-remote-control unit")
    _require_daemon_user()
    root = tmp_path_factory.mktemp("check_mode")
    logins = {
        "ready": LOGINS["claude-ai"],
        "not-ready": LOGINS["logged-out"],
        # just deploy's configure step, with its own sign-in next in the run.
        "not-ready-in-deploy": LOGINS["logged-out"],
        # No login, and a setting that would keep Remote Control off after one.
        "not-ready-blocked": LOGINS["logged-out"],
    }
    logins.update({name: LOGINS["claude-ai"] for name in SETTINGS})
    hosts = _stand_in_hosts(root, logins)
    for name, (settings, _) in SETTINGS.items():
        _seed_settings(root, name, settings)
    _seed_settings(root, "not-ready-blocked", {"env": {"DISABLE_TELEMETRY": "1"}})
    hosts["not-ready-in-deploy"]["claude_code_sign_in_follows"] = True
    (root / "projects").mkdir()
    report = _check_run(root, "remote_control", hosts, claude_code_remote_control_dir=str(root / "projects"))
    assert report["rc"] == 0, _failures(report)
    return root, hosts, report


def test_check_mode_on_a_vm_without_the_unit_does_not_fail(check_mode):
    root, hosts, report = check_mode
    assert "no Claude Code login is stored" in _messages(report, "Say exactly what to run")["not-ready"]
    for host in hosts:
        # systemd was asked about the unit at most, never told to change anything.
        log = root / host / "systemctl.log"
        calls = set(log.read_text().split()) if log.exists() else set()
        assert not calls & {"daemon-reload", "enable", "disable", "start", "stop", "restart"}
        assert not (root / host / ".claude.json").exists()


def test_configure_names_the_sign_in_that_comes_next(check_mode):
    _, _, report = check_mode
    said = _messages(report, "Say exactly what to run")
    # Standalone, the command that signs in and starts Remote Control.
    assert 'Sign in once: "just login not-ready --only claude" opens the sign-in page' in said["not-ready"]
    assert "page shows, and starts it." in said["not-ready"]
    assert "just deploy signs it in next" not in said["not-ready"]
    # Inside just deploy, whose own sign-in follows in the same run.
    assert "just deploy signs it in next, in this run." in said["not-ready-in-deploy"]
    assert "--only claude" not in said["not-ready-in-deploy"]
    # A sign-in leaves a VM with a blocking setting stopped: no promise to start it.
    assert 'Sign in once: "just login not-ready-blocked --only claude"' in said["not-ready-blocked"]
    assert "and starts it" not in said["not-ready-blocked"]


def test_next_steps_name_the_sign_in_unless_one_follows_and_a_blocker_where_signed_in(check_mode):
    # What the run's last lines list (callback_plugins/claude_on_proxmox.py).
    _, _, report = check_mode
    said = _messages(report, "Say exactly what to run")
    left = _left_to_do(report)
    # Signed out, the sign-in is the one thing left, under the sign-in's matter.
    # This once left it to the sign-in's own report, which a just deploy --only configure, or
    # a just deploy with no terminal, never runs, so theirs named no sign-in.
    for host in ("not-ready", "not-ready-blocked"):
        assert left[host] == {"remote_control": "", "claude": said[host]}, host
        assert "just login " + host + " --only claude" in left[host]["claude"]
    # Under just deploy its sign-in follows, and that report says what is left.
    assert left["not-ready-in-deploy"] == {"remote_control": "", "claude": ""}
    blocked = [host for host, (_, expected) in SETTINGS.items() if expected]
    assert blocked
    for host in blocked:
        # Signed in: the sign-in's matter is login.yml's alone.
        assert left[host] == {"remote_control": said[host]}, host


@pytest.mark.parametrize("host", sorted(SETTINGS))
def test_settings_that_disable_remote_control_are_named_rather_than_fatal(check_mode, host):
    _, _, report = check_mode
    messages = _messages(report, "Say exactly what to run")
    expected = SETTINGS[host][1]
    if not expected:
        # Eligible, exactly like "ready": nothing to explain, and (the fixture
        # already asserts) nothing failed.
        assert host not in messages, messages[host]
        return
    named = re.search(r"disables Remote Control through (.+?)\. Each ", messages[host])
    assert named, messages[host]
    assert set(named.group(1).split(", ")) == set(expected), messages[host]
    assert "claude_remote_control: false" in messages[host]
    # Signed in already, so no login step is suggested.
    assert "--only claude" not in messages[host]


def test_the_path_unit_is_retired_before_remote_control_starts():
    # A start the server refuses fails the host at the stay-up check, and a
    # path unit still installed then would start the service the user stops
    # next, on the first token refresh or logout. Told by what the tasks do,
    # not their names.
    tasks = yaml.safe_load((REPO / "roles" / "claude_code" / "tasks" / "remote_control.yml").read_text())

    def starts_the_service(task: dict) -> bool:
        return any(
            (step.get("ansible.builtin.systemd_service") or {}).get("enabled") is True
            and not str(step["ansible.builtin.systemd_service"]["name"]).endswith(".path")
            for step in [task, *task.get("block", [])]
        )

    retire = [i for i, task in enumerate(tasks) if task.get("ansible.builtin.include_tasks") == "path_unit_absent.yml"]
    start = [i for i, task in enumerate(tasks) if starts_the_service(task)]
    assert retire and start, (retire, start)
    assert max(retire) < min(start), (retire, start)


@pytest.fixture(scope="module")
def worktree_spawn(tmp_path_factory):
    """remote_control.yml in check mode with spawn: worktree, on a signed-in VM with and without a repository."""
    if UNIT.exists():
        pytest.skip("this machine has a real claude-remote-control unit")
    _require_daemon_user()
    root = tmp_path_factory.mktemp("worktree_spawn")
    hosts = _stand_in_hosts(root, {"repo": LOGINS["claude-ai"], "no-repo": LOGINS["claude-ai"]})
    for name, hostvars in hosts.items():
        # Each host's own session directory: a git repository for one, an empty
        # directory for the other. Set per host, not on the include, so the two
        # hosts of one play can differ.
        projects = root / name / "projects"
        projects.mkdir()
        hostvars["claude_code_remote_control_dir"] = str(projects)
        hostvars["claude_code_remote_control_spawn"] = "worktree"
    (root / "repo" / "projects" / ".git").mkdir()
    return _check_run(root, "remote_control", hosts)


def test_worktree_spawn_without_a_repository_says_so(worktree_spawn):
    report = worktree_spawn
    assert report["rc"] != 0
    failures = _failures(report)
    assert set(failures) == {"no-repo"}, failures
    assert "is not a git repository" in failures["no-repo"]
    assert "same-dir or session" in failures["no-repo"]


@pytest.fixture(scope="module")
def settings_on_disk(tmp_path_factory):
    """settings.yml in check mode against an empty, a whitespace-only and a broken settings.json."""
    _require_daemon_user()
    root = tmp_path_factory.mktemp("settings_on_disk")
    managed = {"permissions": {"defaultMode": "acceptEdits"}}
    cases = {
        # Something to merge, so the file has to be parsed.
        "empty": ("", managed),
        "whitespace": ("\n  \n", managed),
        "broken": ('{"theme": ', managed),
        # Nothing managed: the role has no business with the file, and never
        # parses it.
        "broken-unmanaged": ('{"theme": ', {}),
    }
    hosts = _stand_in_hosts(root, {name: LOGINS["claude-ai"] for name in cases})
    for name, (text, settings) in cases.items():
        claude_dir = root / name / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(text)
        hosts[name]["claude_code_settings"] = settings
    return _check_run(root, "settings", hosts)


def test_an_empty_settings_json_is_merged_into_rather_than_a_parse_error(settings_on_disk):
    failures = _failures(settings_on_disk)
    assert set(failures) == {"broken"}, failures


def test_a_settings_json_that_is_not_json_is_named(settings_on_disk):
    report = settings_on_disk
    assert report["rc"] != 0
    # The parse itself fails under no_log, so its message is censored; the
    # rescue that follows is the one that must name the file.
    messages = [
        result.get("msg", "")
        for name, host, result in _results(report)
        if host == "broken" and result.get("failed") and name.endswith("Fail naming the file that could not be parsed")
    ]
    assert messages, _failures(report)
    assert "/broken/.claude/settings.json on broken is not a JSON object" in messages[0]


# ------------------------------------------------------------ the harness itself

# What the developer's shell may hold that would point a module under test at
# their own git, gh or Claude Code, with values that must never arrive.
DEVELOPER_ONLY = {
    "XDG_CONFIG_HOME": "/developer/.config",
    "GIT_CONFIG_GLOBAL": "/developer/.gitconfig",
    "GH_CONFIG_DIR": "/developer/.config/gh",
    "GH_TOKEN": "gho_fake-developer-token-0000",
    "GITHUB_TOKEN": "ghp_fake-developer-token-0000",
    "CLAUDE_CONFIG_DIR": "/developer/.claude",
}


def test_a_run_inherits_only_what_the_harness_names(tmp_path, monkeypatch):
    for name, value in DEVELOPER_ONLY.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GIT_ASKPASS", "/developer/askpass.sh")
    monkeypatch.setenv("ANSIBLE_STDOUT_CALLBACK", "developer.own.callback")
    monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", "/developer/collections")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    env = _env(tmp_path)
    assert not set(env) & {*DEVELOPER_ONLY, "GIT_ASKPASS", "ANSIBLE_STDOUT_CALLBACK"}, env
    inherited = {name for name in env if not name.startswith("ANSIBLE_") and name != "BROWSER"}
    assert all(name in PASSED_ON or name.startswith("LC_") for name in inherited), inherited
    # Passed on by name: where the Galaxy collections are, and the locale.
    assert env["ANSIBLE_COLLECTIONS_PATH"] == "/developer/collections"
    assert env["LC_ALL"] == "C.UTF-8"
    # The desktop stand-ins come first, and BROWSER is one of them.
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / ".desktop")
    assert env["BROWSER"] == str(tmp_path / ".desktop" / "open")


def test_a_stand_in_host_never_sees_the_developer_s_git_gh_or_claude_config(tmp_path):
    hosts = _stand_in_hosts(tmp_path, {"probe": LOGINS["claude-ai"]})
    # Run the interpreter wrapper as a module would be, with everything a test
    # might pass on itself.
    seen = json.loads(
        subprocess.run(
            [
                hosts["probe"]["ansible_python_interpreter"],
                "-c",
                "import json, os; print(json.dumps(dict(os.environ)))",
            ],
            env={"PATH": os.environ["PATH"], "HOME": "/developer", **DEVELOPER_ONLY},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    home = tmp_path / "probe"
    assert seen["HOME"] == str(home)
    assert seen["XDG_CONFIG_HOME"] == str(home / ".config")
    assert seen["GIT_CONFIG_NOSYSTEM"] == "1"
    assert not set(seen) & (set(DEVELOPER_ONLY) - {"XDG_CONFIG_HOME"}), seen
    assert seen["PATH"].split(os.pathsep)[0] == str(tmp_path / "bin")


def test_git_on_a_stand_in_host_writes_inside_that_host_only(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("no git on this machine")
    # The developer's own global config, where git would write it: through
    # XDG_CONFIG_HOME, and through GIT_CONFIG_GLOBAL. Both are exported in the
    # shell running pytest.
    developer = tmp_path / "developer"
    xdg_config = developer / "xdg" / "git" / "config"
    xdg_config.parent.mkdir(parents=True)
    xdg_config.write_text("[user]\n\tname = The Developer\n")
    global_config = developer / "gitconfig"
    global_config.write_text("[user]\n\temail = developer@example.com\n")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (xdg_config, global_config)}
    monkeypatch.setenv("XDG_CONFIG_HOME", str(developer / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    root = tmp_path / "run"
    root.mkdir()
    hosts = _stand_in_hosts(root, {"probe": LOGINS["claude-ai"]})
    playbook = root / "git.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Write the global git config, as clone.yml and gh setup-git do, and read it back",
                    "hosts": "claude_vms",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "name": "Write",
                            "ansible.builtin.command": {"argv": ["git", "config", "--global", "user.name", "stand-in"]},
                        },
                        {
                            "name": "Read",
                            "ansible.builtin.command": {
                                "argv": ["git", "config", "--global", "--show-origin", "--list"]
                            },
                        },
                    ],
                }
            ]
        )
    )
    report = _run(root, playbook, hosts)
    assert report["rc"] == 0, _failures(report)
    listed = next(result["stdout_lines"] for name, _, result in _results(report) if name == "Read")
    assert listed, report
    for line in listed:
        origin = Path(line.split("\t")[0].removeprefix("file:")).resolve()
        assert origin.is_relative_to((root / "probe").resolve()), line
    assert "user.name=stand-in" in "\n".join(listed)
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before}


def test_the_galaxy_collections_resolve_from_where_ansible_is_told(tmp_path, monkeypatch):
    # ansible-playbook finds them under HOME by default; a developer who keeps
    # them elsewhere says so with ANSIBLE_COLLECTIONS_PATH, which an earlier
    # harness dropped along with every other ANSIBLE_* variable.
    found = [
        Path(path).expanduser()
        for path in (
            *os.environ.get("ANSIBLE_COLLECTIONS_PATH", "").split(os.pathsep),
            "~/.ansible/collections",
            "/usr/share/ansible/collections",
        )
        if path and (Path(path).expanduser() / "ansible_collections" / "community" / "proxmox").is_dir()
    ]
    if not found:
        pytest.skip("the Galaxy collections are not installed (just init)")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", str(found[0]))
    playbook = tmp_path / "collections.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Resolve a module from a Galaxy collection",
                    "hosts": "localhost",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "name": "Never run, only resolved",
                            "community.proxmox.proxmox_vm_info": {"name": "nothing"},
                            "when": False,
                        }
                    ],
                }
            ]
        )
    )
    report = _run(tmp_path, playbook, {})
    assert report["rc"] == 0, report["stderr"]


# A run of the harness from a process whose controlling terminal is a pseudo-
# terminal of the test's own, as pytest's is under `pytest -s`. It prints what
# the prompts took.
RUN_AT_A_TERMINAL = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from harness import _results, _run
report = _run(Path(sys.argv[2]), Path(sys.argv[3]), {}, timeout=30)
print(json.dumps({"rc": report["rc"], "said": [r.get("msg") for _, _, r in _results(report) if "msg" in r]}))
"""


def test_a_run_never_reads_the_terminal_pytest_runs_at(tmp_path):
    # pytest's own capture points fd 0 at /dev/null, so this matters only under
    # `pytest -s` at a terminal, where a run that inherited it would wait at a
    # prompt and take the developer's keystrokes. Here keys are typed at the
    # terminal throughout the run: a run reading it would take them, or wait.
    playbook = tmp_path / "prompts.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Ask, as the sign-ins do",
                    "hosts": "localhost",
                    "gather_facts": False,
                    "vars_prompt": [{"name": "answer", "prompt": "Type here", "private": False, "default": "untyped"}],
                    "tasks": [
                        {"name": "Ask again", "ansible.builtin.pause": {"prompt": "Type here"}, "register": "paused"},
                        {"name": "Say", "ansible.builtin.debug": {"msg": "{{ answer }}|{{ paused.user_input }}"}},
                    ],
                }
            ]
        )
    )
    master, slave = pty.openpty()
    helper = subprocess.Popen(
        [sys.executable, "-c", RUN_AT_A_TERMINAL, str(Path(__file__).parent), str(tmp_path), str(playbook)],
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=_take_the_terminal,  # noqa: PLW1509 - nothing else runs threads here
    )
    os.close(slave)
    os.set_blocking(master, False)
    deadline = time.monotonic() + 90
    try:
        while helper.poll() is None and time.monotonic() < deadline:
            for act in (lambda: os.write(master, b"typed-at-the-terminal\n"), lambda: os.read(master, 65536)):
                try:
                    act()
                except OSError:
                    pass
            time.sleep(0.5)
    finally:
        if helper.poll() is None:
            os.killpg(helper.pid, signal.SIGKILL)
        stdout, stderr = helper.communicate()
        os.close(master)
    assert helper.returncode == 0, stderr.decode(errors="replace")
    assert json.loads(stdout) == {"rc": 0, "said": ["untyped|"]}, stdout


# Calls every opener and clipboard in TOOLS, then BROWSER, each only if it
# resolves into DESKTOP: a stand-in.
REACH_FOR_THE_DESKTOP = """
for tool in $TOOLS; do
  found=$(command -v "$tool") || exit 3
  case $found in "$DESKTOP"/*) "$found" https://example.invalid/page ;; *) exit 4 ;; esac
done
case $BROWSER in "$DESKTOP"/*) "$BROWSER" https://example.invalid/page ;; *) exit 5 ;; esac
"""


def test_a_run_that_reaches_for_the_desktop_fails_its_test(tmp_path):
    # Every opener and clipboard the sign-ins look for, and BROWSER, resolve to
    # a stand-in, and a run that calls one fails its test. Only a stand-in is
    # ever called here, whatever PATH holds.
    playbook = tmp_path / "desktop.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Reach for the desktop, as a sign-in with its switch on would",
                    "hosts": "localhost",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "name": "Call each opener and clipboard, if it is a stand-in",
                            "ansible.builtin.shell": REACH_FOR_THE_DESKTOP,
                            "environment": {"DESKTOP": str(tmp_path / ".desktop"), "TOOLS": " ".join(DESKTOP_TOOLS)},
                            "changed_when": False,
                        }
                    ],
                }
            ]
        )
    )
    with pytest.raises(pytest.fail.Exception, match="called this machine's browser or clipboard"):
        _run(tmp_path, playbook, {})
    calls = (tmp_path / ".desktop" / "calls.log").read_text().splitlines()
    assert calls == [f"{tool} https://example.invalid/page" for tool in (*DESKTOP_TOOLS, "open")], calls


def test_keys_typed_before_a_prompt_is_ready_still_reach_it(tmp_path):
    # pause flushes the terminal's input as it starts, so keys typed a moment
    # early are lost; the terminal driver waits for the prompt's raw mode, and
    # types again when the prompt has not taken its keys. Here the keys are due
    # three seconds before the prompt.
    playbook = tmp_path / "early.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Say a prompt is coming, then ask",
                    "hosts": "localhost",
                    "gather_facts": False,
                    "tasks": [
                        {"name": "Say", "ansible.builtin.debug": {"msg": "a prompt comes in three seconds"}},
                        {"name": "Wait", "ansible.builtin.wait_for": {"timeout": 3}},
                        {"name": "Ask", "ansible.builtin.pause": {"prompt": "Type here"}, "register": "paused"},
                        {"name": "Answer", "ansible.builtin.debug": {"msg": "took {{ paused.user_input }}"}},
                    ],
                }
            ]
        )
    )
    steps = [(b"a prompt comes in three seconds", b"typed-early\n")]
    rc, output, log = _run_at_a_terminal(tmp_path, playbook, {}, {}, steps=steps, timeout=60)
    assert rc == 0, output
    assert "took typed-early" in log, output
