"""Remote Control's decisions, run for real against canned `claude auth status` output.

Nothing here can reach a VM, a real `claude` or a real systemd. Every inventory
host is the controller itself, over the local connection, with an interpreter
wrapper that puts stand-ins for `bash`, `claude` and `systemctl` first on the
module's PATH and points them at that host's canned login. The stand-in bash
answers only the exact login-shell commands the role runs, so nothing is handed
to a profile that could find a real `claude` on the developer's machine.

What these guard:

- `make claude-login` decided "signed in" from `loggedIn` alone, while the role
  also requires a claude.ai login with no API key in the way, so a VM signed in
  any other way was sent back and forth between the two.
- The login command it printed ran `claude` in a non-login shell, where the
  native install is not on PATH.
- `make check` failed on every VM without the Remote Control unit, because
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
"""

from __future__ import annotations

import grp
import json
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
UNIT = Path("/etc/systemd/system/claude-remote-control.service")
LOGIN_COMMAND = "ssh -t dev@192.0.2.10 \"bash -lc 'claude auth login'\""

# What `claude auth status` prints for each way of being signed in, and its
# exit status: 0 when logged in, 1 when not.
CLAUDE_AI = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}
LOGINS = {
    "logged-out": ({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}, 1),
    "claude-ai": ({**CLAUDE_AI, "email": "dev@example.com", "subscriptionType": "max"}, 0),
    # The login exists, but ANTHROPIC_API_KEY outranks it.
    "claude-ai-with-key": ({**CLAUDE_AI, "subscriptionType": "max", "apiKeySource": "ANTHROPIC_API_KEY"}, 0),
    # A Console login reports claude.ai too; only the key source tells it apart.
    "console": ({**CLAUDE_AI, "subscriptionType": None, "apiKeySource": "/login managed key"}, 0),
    "api-key": ({"loggedIn": True, "authMethod": "api_key", "apiKeySource": "ANTHROPIC_API_KEY"}, 0),
    "api-key-no-source": ({"loggedIn": True, "authMethod": "api_key"}, 0),
    # CLAUDE_CODE_OAUTH_TOKEN, from `claude setup-token`.
    "setup-token": ({"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"}, 0),
    # Signed in, but ~/.claude/settings.json switches the feature off.
    "claude-ai-telemetry": ({**CLAUDE_AI, "subscriptionType": "max"}, 0),
}

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

# One copy of each, shared by every host: macOS scans an executable the first
# time it runs, which made a copy per host cost seconds. Each host's interpreter
# is a symlink to the "python" wrapper, which finds the host's files beside it.
# HOME is the host's directory too, so a module that reads ~/.claude/settings.json
# finds what the test seeded there and never the developer's own.
STAND_INS = {
    "python": f"""#!/bin/sh
STAND_IN=$(dirname "$0")
PATH="$(dirname "$(dirname "$0")")/bin:$PATH"
HOME=$STAND_IN
export STAND_IN PATH HOME
exec "{sys.executable}" "$@"
""",
    "bash": """#!/bin/sh
echo "$*" >> "$STAND_IN/bash.log"
case "$*" in
  "-lc claude auth status") exec claude auth status ;;
  "-lc command -v claude") command -v claude ;;
  *) echo "stand-in bash: unexpected arguments: $*" >&2; exit 127 ;;
esac
""",
    "claude": """#!/bin/sh
if [ "$*" = "auth status" ]; then cat "$STAND_IN/status.json"; exit "$(cat "$STAND_IN/rc")"; fi
echo "stand-in claude: unexpected arguments: $*" >&2
exit 99
""",
    # systemd's answers for a unit that does not exist.
    "systemctl": """#!/bin/sh
echo "$*" >> "$STAND_IN/systemctl.log"
case "$1" in
  show) printf 'Id=%s.service\\nLoadState=not-found\\nActiveState=inactive\\nSubState=dead\\n' "$2" ;;
  is-enabled) echo "Failed to get unit file state for $2.service: No such file or directory" >&2; exit 1 ;;
  list-unit-files) exit 1 ;;
esac
""",
}


def _stand_in_hosts(root: Path, logins: dict) -> dict:
    """Inventory hosts, one per canned login, whose modules run with the stand-ins; returns their hostvars."""
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name, text in STAND_INS.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    hosts = {}
    for name, (status, rc) in logins.items():
        home = root / name
        home.mkdir()
        (home / "status.json").write_text(json.dumps(status))
        (home / "rc").write_text(str(rc))
        (home / "python").symlink_to(bin_dir / "python")
        hosts[name] = {
            "ansible_connection": "local",
            "ansible_python_interpreter": str(home / "python"),
            "ansible_host": "192.0.2.10",
        }
    return hosts


def _seed_settings(root: Path, host: str, settings: dict) -> None:
    """Put a settings.json where that host's modules read it: under its HOME, and under its claude_code_home."""
    claude_dir = root / host / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps(settings))


def _run(root: Path, playbook: Path, hosts: dict, *args: str) -> dict:
    """Run a playbook against hosts, without the project's config or inventory; return the json callback's report."""
    (root / "ansible.cfg").write_text("")
    inventory = root / "hosts.yml"
    inventory.write_text(yaml.safe_dump({"claude_vms": {"hosts": hosts, "vars": {"vm_user": "dev"}}}))
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(
        ANSIBLE_CONFIG=str(root / "ansible.cfg"),
        ANSIBLE_ROLES_PATH=str(REPO / "roles"),
        ANSIBLE_FILTER_PLUGINS=str(REPO / "filter_plugins"),
        ANSIBLE_STDOUT_CALLBACK="ansible.builtin.json",
        ANSIBLE_NOCOLOR="1",
    )
    result = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", str(inventory), *args, str(playbook)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"ansible-playbook exited {result.returncode}:\n{result.stdout}\n{result.stderr}")
    report["rc"] = result.returncode
    return report


def _results(report: dict):
    for play in report["plays"]:
        for task in play["tasks"]:
            for host, result in task["hosts"].items():
                yield task["task"]["name"], host, result


def _messages(report: dict, task: str) -> dict:
    """What each host printed from the task whose name ends with `task`."""
    return {
        host: result["msg"]
        for name, host, result in _results(report)
        if name.endswith(task) and "msg" in result and not result.get("skipped")
    }


def _failures(report: dict) -> dict:
    return {host: f"{name}: {result.get('msg')}" for name, host, result in _results(report) if result.get("failed")}


@pytest.fixture(scope="module")
def login(tmp_path_factory):
    """claude_login.yml, run once for every canned login: the stand-ins' root, and what it told each host."""
    root = tmp_path_factory.mktemp("claude_login")
    hosts = _stand_in_hosts(root, LOGINS)
    _seed_settings(root, "claude-ai-telemetry", {"env": {"DISABLE_TELEMETRY": "1"}})
    report = _run(root, REPO / "playbooks" / "claude_login.yml", hosts, "-e", '{"claude_vm_discovery": false}')
    assert report["rc"] == 0, _failures(report)
    return root, _messages(report, "Report the login state and what to do about it")


def test_claude_login_calls_only_a_claude_ai_login_with_no_key_ready(login):
    _, messages = login
    assert set(messages) == set(LOGINS)
    assert {host for host, msg in messages.items() if "already signed in" in msg} == {"claude-ai"}, messages
    assert "dev@example.com (claude.ai, max plan)" in messages["claude-ai"]
    assert "auth login" not in messages["claude-ai"]


@pytest.mark.parametrize("host", sorted(set(LOGINS) - {"claude-ai"}))
def test_claude_login_prints_a_login_command_that_finds_claude(login, host):
    _, messages = login
    # Ends its line: an old message put a full stop after the closing quote,
    # which pasting turns into part of the remote command.
    assert LOGIN_COMMAND + "\n" in messages[host]
    assert f"Then: make configure VM_NAME={host}" in messages[host]


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
    logins = {"ready": LOGINS["claude-ai"], "not-ready": LOGINS["logged-out"]}
    logins.update({name: LOGINS["claude-ai"] for name in SETTINGS})
    hosts = _stand_in_hosts(root, logins)
    for name, (settings, _) in SETTINGS.items():
        _seed_settings(root, name, settings)
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
    assert "make claude-login" not in messages[host]


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
