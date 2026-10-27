"""The Claude session `just code` starts in the window it opens, the window step around it, and what it may write.

`playbooks/code.yml` hands VS Code a workspace file whose one task runs
`bash -lc <code_terminal_script> just-code <not installed> <signed in, watched>
<signed in, unwatched> <the login's path unit> <signed in to GitHub>
<git protocol> <scopes> <vm name>` when the window opens. The script is run
here as it is, against stand-ins for `claude`, `gh` and the BROWSER helper that
record what they were asked: PATH holds the stand-ins alone, and the script
uses only shell builtins besides them, so nothing on this machine is reached.
One run has a pseudo-terminal of its own, to press Ctrl-C during GitHub's
device flow. The Molecule provision scenario checks the workspace file and the
task around it.

The window step is run too, a few of code.yml's own tasks at a time, inside
its block and with its rescue, its play vars, deploy.yml's for its import and
the inventory defaults: as just code and as just deploy's last step, for one
VM, none named and a fleet, a VM left stopped, no `code` command, no alias, a
refused SSH connection and an extension that would not install. `which` and
`code` are stand-ins there, first on a PATH that holds no other, so no VS Code
of this machine's is ever started.

The rest holds every task in `code.yml` to an allowlist, because the playbook
reads the user's own VS Code settings and must never write them. On this
machine a task may assert, fail, debug, set facts, add the host, import the
project's task files, or run `which`, `code` and rsync; on the VM, reached only
through `delegate_to`, it may stat, getent and find, make its directories and
files, write the session copies, and run git in --session's checkout. The session
handoff's own task files are followed and held to the same list; the shared
ones (the alias, the Proxmox reads) to their own tests, and `code
--install-extension` fills VS Code's own extension store, by design.

The session handoff is run too, between two directories standing for this
machine and the VM, with real git checkouts at both ends and a marker in the
transcripts that no run may print. The last tests keep every run here and the
provision scenario, which runs `code.yml` on this machine, away from the
developer's own VS Code settings and Claude Code.
"""

from __future__ import annotations

import ast
import copy
import fcntl
import importlib.util
import inspect
import json
import os
import pty
import pwd
import re
import select
import stat
import subprocess
import sys
import termios
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml
from harness import _default_signals, _drain, ansible_config

REPO = Path(__file__).resolve().parents[2]
CODE = REPO / "playbooks" / "code.yml"
DEPLOY = REPO / "deploy.yml"
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
PROVISION_VERIFY = REPO / "molecule" / "provision" / "verify.yml"
NOT_INSTALLED = "Claude Code is not installed here yet."
WATCHED = "Signed in; Remote Control starts by itself."
UNWATCHED = "Signed in; start Remote Control from your machine."
SIGNING_IN = "Signing in to Claude first. Your browser opens the sign-in page; if it shows a code, paste it here."
NOT_SIGNED_IN = 'Not signed in to Claude. To try again, reload the window or run "Tasks: Run Task", Claude.'
GITHUB_SIGNED_IN = "Signed in to GitHub; run just deploy --only configure to clone the private repositories."
GITHUB_SIGNING_IN = (
    "Signing in to GitHub next, so that git push and gh work here; Ctrl-C skips this. "
    "Your browser opens github.com/login/device; enter the code below there."
)
GITHUB_STATUS = "gh auth status --hostname github.com --active"
GITHUB_LOGIN = "gh auth login -h github.com -p https -w -s workflow --skip-ssh-key"
GITHUB_PAGE = "browser https://github.com/login/device"
GITHUB_FAILED = "Not signed in to GitHub. From your machine: just login alpha --only github"
GITHUB_CODE = "FAKE-C0DE"
GITHUB_EXPIRED = "failed to authenticate via web browser: This 'device_code' has expired. (expired_token)"

# --continue as Claude Code 2.1 answers it in a folder with nothing to continue,
# with CONTINUE_RC: at once, or with CONTINUE_SLEEP after a session of that long.
STAND_IN = """#!/bin/sh
echo "claude $*" >> "$CLAUDE_LOG"
case "$1 $2" in
  "auth status") exit "$STATUS_RC";;
  "auth login") exit "$LOGIN_RC";;
  "--continue "*)
    [ -n "$CONTINUE_RC" ] || exit 0
    [ -z "$CONTINUE_SLEEP" ] || /bin/sleep "$CONTINUE_SLEEP"
    echo "No conversation found to continue"
    exit "$CONTINUE_RC";;
  "--resume "*)
    [ -n "$RESUME_RC" ] || exit 0
    echo "No conversation found with session ID: $2"
    exit "$RESUME_RC";;
esac
exit 0
"""
# The device flow as gh prints it with no terminal: a clipboard warning on
# stderr, the code and the page on stdout, then the outcome - or, with
# GH_LOGIN_RC=wait, a minute's wait for an approval that never comes, in one
# process as gh's is: a shell looping over sleep can outlive a Ctrl-C that
# lands between its fork and the exec.
# The status answers the one question the script must ask, about the active
# github.com account alone, from GH_STATUS_RC, and anything broader from
# GH_STATUS_ANY_RC: in text mode gh answers for every host and account it holds.
GH_STAND_IN = """#!/bin/sh
echo "gh $*" >> "$CLAUDE_LOG"
case "$1 $2" in
  "auth status")
    case " $* " in
      *" --hostname github.com "*) case " $* " in *" --active "*) exit "$GH_STATUS_RC";; esac;;
    esac
    exit "$GH_STATUS_ANY_RC";;
  "auth login")
    echo "! Failed to copy one-time code to clipboard" >&2
    echo "! First copy your one-time code: FAKE-C0DE"
    echo "Open this URL to continue in your web browser: https://github.com/login/device"
    if [ "$GH_LOGIN_RC" = 0 ]; then echo "Logged in as octo"; exit 0; fi
    if [ "$GH_LOGIN_RC" = wait ]; then exec /bin/sleep 60; fi
    echo "failed to authenticate via web browser: This 'device_code' has expired. (expired_token)"
    exit 1;;
esac
exit 0
"""
BROWSER_STAND_IN = """#!/bin/sh
echo "browser $*" >> "$CLAUDE_LOG"
"""


def _play() -> dict:
    return yaml.safe_load(CODE.read_text())[0]


def _script() -> str:
    return _play()["vars"]["code_terminal_script"]


def _terminal(
    tmp_path: Path,
    *,
    installed=True,
    status_rc=1,
    login_rc=0,
    watched=False,
    signed_in=(WATCHED, UNWATCHED),
    gh_installed=True,
    gh_status_rc=0,
    gh_status_any_rc=None,
    gh_login_rc=0,
    github_signed_in=GITHUB_SIGNED_IN,
    continue_rc=None,
    continue_sleep=None,
    session="",
    resume_rc=None,
) -> tuple[list[str], dict]:
    """The script's command line and environment, with the stand-ins in place."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text, there in (
        ("claude", STAND_IN, installed),
        ("gh", GH_STAND_IN, gh_installed),
        ("browser", BROWSER_STAND_IN, True),
    ):
        if there:
            stand_in = bin_dir / name
            stand_in.write_text(text)
            stand_in.chmod(0o755)
    # Where the claude_code role's path unit is on a VM that has one.
    watch = tmp_path / "claude-remote-control.path"
    if watched:
        watch.write_text("[Path]\n")
    argv = ["/bin/bash", "-c", _script(), "just-code", NOT_INSTALLED, *signed_in, str(watch), github_signed_in]
    env = {
        "PATH": str(bin_dir),
        "BROWSER": str(bin_dir / "browser"),
        "CLAUDE_LOG": str(tmp_path / "claude.log"),
        "STATUS_RC": str(status_rc),
        "LOGIN_RC": str(login_rc),
        "GH_STATUS_RC": str(gh_status_rc),
        "GH_STATUS_ANY_RC": str(gh_status_rc if gh_status_any_rc is None else gh_status_any_rc),
        "GH_LOGIN_RC": str(gh_login_rc),
        "CONTINUE_RC": "" if continue_rc is None else str(continue_rc),
        "CONTINUE_SLEEP": "" if continue_sleep is None else str(continue_sleep),
        "RESUME_RC": "" if resume_rc is None else str(resume_rc),
    }
    # Then the session notes' four, off here, and --session, last as the task passes them.
    return [*argv, "https", "workflow", "alpha", "", "", "claude-remote-control", "UTC", session], env


def _asked(tmp_path: Path) -> list[str]:
    log = tmp_path / "claude.log"
    return [line.strip() for line in log.read_text().splitlines()] if log.exists() else []


def _run(tmp_path: Path, **case):
    argv, env = _terminal(tmp_path, **case)
    result = subprocess.run(argv, env=env, capture_output=True, text=True, check=False, timeout=30)
    return result, _asked(tmp_path)


def _session(sid: int) -> str:
    """Each process left in a session, with its group, its terminal's foreground group and its signal masks.

    What a hang leaves running, from /proc (Linux): the evidence for why a Ctrl-C ended nothing.
    """
    lines = []
    for status in sorted(Path("/proc").glob("[0-9]*/status")):
        try:
            stat_text = (status.parent / "stat").read_text()
            # state ppid pgrp session tty_nr tpgid, after the parenthesised command name
            state, ppid, pgrp, session, _, tpgid = stat_text[stat_text.rindex(")") + 2 :].split()[:6]
            if int(session) != sid:
                continue
            fields = dict(line.split(":\t", 1) for line in status.read_text().splitlines() if ":\t" in line)
            command = (status.parent / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        except (OSError, ValueError):
            continue
        masks = " ".join(f"{name} {fields.get(name, '?').strip()}" for name in ("SigIgn", "SigBlk", "SigCgt", "SigPnd"))
        lines.append(f"pid {status.parent.name} ppid {ppid} pgrp {pgrp} tpgid {tpgid} {state} {masks}: {command}")
    return "\n".join(lines) or "(nothing, or no /proc)"


def _run_at_a_terminal(tmp_path: Path, keys: bytes, *, after: str, **case) -> tuple[int, str, list[str]]:
    """Run the script at a pseudo-terminal of its own; type `keys` once the stand-ins have logged `after`."""
    argv, env = _terminal(tmp_path, **case)
    master, slave = pty.openpty()

    def take_the_terminal() -> None:
        # A session of its own, with this terminal as its controlling one, so
        # that Ctrl-C reaches bash and everything it started, as in VS Code;
        # and SIGINT not ignored, whatever started the suite.
        _default_signals()
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    process = subprocess.Popen(argv, env=env, stdin=slave, stdout=slave, stderr=slave, preexec_fn=take_the_terminal)
    os.close(slave)
    output, typed, deadline = b"", False, time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            if not typed and after in _asked(tmp_path):
                os.write(master, keys)
                typed = True
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:  # Linux, once the terminal's other end has closed
                    break
                if not chunk:
                    break
                output += chunk
            elif process.poll() is not None:
                output += _drain(master, deadline)
                break
        else:
            pytest.fail(
                "the script did not finish within 30 seconds:\n"
                + output.decode(errors="replace")
                + f"\nthe terminal's ISIG is {'on' if termios.tcgetattr(master)[3] & termios.ISIG else 'off'};"
                + " what is left of its session:\n"
                + _session(process.pid)
            )
        rc = process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
        os.close(master)
    return rc, output.decode(errors="replace"), _asked(tmp_path)


def test_the_script_and_its_messages_are_not_templates():
    # Ansible templates play vars, so the script must reach the VM as written;
    # VS Code resolves ${...} in a task's arguments before bash sees them.
    script = _script()
    for marker in ("{{", "{%", "{#", "${"):
        assert marker not in script
    for message in (
        "code_not_installed",
        "code_signed_in_watched",
        "code_signed_in_unwatched",
        "code_login_watch",
        "code_github_signed_in",
    ):
        assert "${" not in _play()["vars"][message]


def test_with_nothing_to_continue_a_fresh_claude_starts(tmp_path):
    # Claude Code 2.1 exits 1 at once on --continue in a folder with no
    # conversation of its own; VS Code then printed the whole command line.
    result, asked = _run(tmp_path, status_rc=0, continue_rc=1)
    assert result.returncode == 0
    assert asked == ["claude auth status", GITHUB_STATUS, "claude --continue", "claude"]


SESSION_ID = "0a608ad8-1b2c-4d3e-8f90-123456789abc"


def test_a_session_handed_over_is_resumed_under_its_own_id(tmp_path):
    # --session: the copy this machine's session became, never a fork, and never --continue.
    result, asked = _run(tmp_path, status_rc=0, session=SESSION_ID)
    assert result.returncode == 0
    assert asked == ["claude auth status", GITHUB_STATUS, f"claude --resume {SESSION_ID}"]


def test_a_resume_that_fails_starts_no_other_claude(tmp_path):
    # Claude Code's own reason is said; a fresh claude would look like the session.
    result, asked = _run(tmp_path, status_rc=0, session=SESSION_ID, resume_rc=1)
    assert result.returncode == 0
    assert asked == ["claude auth status", GITHUB_STATUS, f"claude --resume {SESSION_ID}"]
    assert f"No conversation found with session ID: {SESSION_ID}" in result.stdout


def test_a_continued_session_that_ends_badly_is_not_started_again(tmp_path):
    # Only a --continue that fails at once means there was nothing to continue.
    # Five seconds is the least that is not at once: bash's SECONDS counts
    # whole seconds of the clock, so it says five or more after a sleep of five.
    result, asked = _run(tmp_path, status_rc=0, continue_rc=1, continue_sleep=5)
    assert result.returncode == 0
    assert asked[-1] == "claude --continue"


def test_without_claude_it_says_how_to_install_it_and_stops(tmp_path):
    # exit 0: a non-zero exit makes VS Code print the whole command line,
    # script and messages included, under the line that matters.
    result, asked = _run(tmp_path, installed=False)
    assert result.returncode == 0
    assert result.stdout == NOT_INSTALLED + "\n"
    assert asked == []


def test_a_signed_in_vm_picks_the_folder_s_conversation_up(tmp_path):
    # --continue: the folder's latest conversation, or a new one. A task
    # terminal does not survive a window reload, and the task then runs again.
    result, asked = _run(tmp_path, status_rc=0)
    assert result.returncode == 0
    assert asked == ["claude auth status", GITHUB_STATUS, "claude --continue"]
    assert result.stdout == ""


def test_a_sign_in_where_the_path_unit_watches_for_the_login_says_remote_control_starts_by_itself(tmp_path):
    result, asked = _run(tmp_path, status_rc=1, login_rc=0, watched=True)
    assert result.returncode == 0
    assert asked == ["claude auth status", "claude auth login", GITHUB_STATUS, "claude --continue"]
    assert result.stdout == SIGNING_IN + "\n" + WATCHED + "\n"


def test_a_sign_in_where_nothing_watches_for_the_login_names_the_run_that_starts_remote_control(tmp_path):
    # A VM with a setting that blocks Remote Control, or last configured
    # before the path unit existed: nothing starts it by itself.
    result, asked = _run(tmp_path, status_rc=1, login_rc=0, watched=False)
    assert result.returncode == 0
    assert asked == ["claude auth status", "claude auth login", GITHUB_STATUS, "claude --continue"]
    assert result.stdout == SIGNING_IN + "\n" + UNWATCHED + "\n"


def test_a_failed_sign_in_says_how_to_try_again_and_stops(tmp_path):
    result, asked = _run(tmp_path, status_rc=1, login_rc=1)
    assert result.returncode == 0
    assert asked == ["claude auth status", "claude auth login"]
    assert result.stdout == SIGNING_IN + "\n" + NOT_SIGNED_IN + "\n"


@pytest.mark.parametrize("watched", [True, False])
def test_no_remote_control_hint_when_there_is_none_to_give(tmp_path, watched):
    result, asked = _run(tmp_path, status_rc=1, login_rc=0, watched=watched, signed_in=("", ""))
    assert asked[-1] == "claude --continue"
    assert result.stdout == SIGNING_IN + "\n"


def test_a_vm_signed_out_of_github_signs_in_there_too_and_becomes_git_s_helper(tmp_path):
    # Claude first: the session is the point of the window. Then the device
    # flow with no terminal of its own, the page opened through BROWSER and the
    # code printed on a line of its own, then setup-git, so a push from this
    # terminal works at once.
    result, asked = _run(tmp_path, status_rc=0, gh_status_rc=1)
    assert result.returncode == 0
    assert asked == [
        "claude auth status",
        GITHUB_STATUS,
        GITHUB_LOGIN,
        GITHUB_PAGE,
        "gh auth setup-git -h github.com",
        "claude --continue",
    ]
    assert result.stdout == (
        GITHUB_SIGNING_IN + "\n" + "\n    " + GITHUB_CODE + "\n\n" + "Logged in as octo\n" + GITHUB_SIGNED_IN + "\n"
    )
    # gh's clipboard warning is not repeated into the terminal.
    assert "clipboard" not in result.stdout


def test_a_failed_github_sign_in_still_starts_claude(tmp_path):
    result, asked = _run(tmp_path, status_rc=0, gh_status_rc=1, gh_login_rc=1)
    assert result.returncode == 0
    assert asked == ["claude auth status", GITHUB_STATUS, GITHUB_LOGIN, GITHUB_PAGE, "claude --continue"]
    assert result.stdout == (
        GITHUB_SIGNING_IN + "\n" + "\n    " + GITHUB_CODE + "\n\n" + GITHUB_EXPIRED + "\n" + GITHUB_FAILED + "\n"
    )


def test_ctrl_c_during_github_s_device_flow_skips_it_and_starts_claude(tmp_path):
    # VS Code's terminal sends Ctrl-C to bash and everything bash started.
    # Without the trap bash dies with gh, and Claude never starts; with it, gh
    # and the read loop end, and claude starts as after a sign-in that failed,
    # long before the code would have expired.
    rc, output, asked = _run_at_a_terminal(
        tmp_path, b"\x03", after=GITHUB_PAGE, status_rc=0, gh_status_rc=1, gh_login_rc="wait"
    )
    assert rc == 0, output
    assert asked == ["claude auth status", GITHUB_STATUS, GITHUB_LOGIN, GITHUB_PAGE, "claude --continue"]
    assert GITHUB_FAILED in output
    assert GITHUB_EXPIRED not in output


def test_a_stale_second_github_account_starts_no_device_flow(tmp_path):
    # Asked about every account, gh fails over one with a dead token, though
    # the active one is signed in; the script asks about that one alone.
    result, asked = _run(tmp_path, status_rc=0, gh_status_rc=0, gh_status_any_rc=1)
    assert result.returncode == 0
    assert asked == ["claude auth status", GITHUB_STATUS, "claude --continue"]


def test_gh_signed_in_only_to_another_host_is_signed_in_to_github_com(tmp_path):
    # Asked about every host, gh is content with another one's account.
    result, asked = _run(tmp_path, status_rc=0, gh_status_rc=1, gh_status_any_rc=0)
    assert result.returncode == 0
    assert GITHUB_LOGIN in asked


def test_without_gh_the_github_step_is_skipped(tmp_path):
    result, asked = _run(tmp_path, status_rc=0, gh_installed=False)
    assert result.returncode == 0
    assert asked == ["claude auth status", "claude --continue"]
    assert result.stdout == ""


def test_github_auth_off_leaves_gh_alone(tmp_path):
    # An empty GitHub line is how code.yml says github_projects_auth is off.
    result, asked = _run(tmp_path, status_rc=0, gh_status_rc=1, github_signed_in="")
    assert result.returncode == 0
    assert asked == ["claude auth status", "claude --continue"]
    assert result.stdout == ""


def test_the_task_passes_the_script_and_its_messages_as_arguments():
    # The role-level names: the inventory wires them, the roles read them, and
    # an override of one reaches both.
    task = _play()["vars"]["code_claude_task"]
    assert task["command"] == "/bin/bash"
    assert task["args"][:3] == ["-lc", "{{ code_terminal_script }}", "just-code"]
    assert task["args"][3:] == [
        "{{ code_not_installed }}",
        "{{ code_signed_in_watched }}",
        "{{ code_signed_in_unwatched }}",
        "{{ code_login_watch }}",
        "{{ code_github_signed_in }}",
        "{{ github_projects_clone_protocol | default('https') }}",
        "{{ github_projects_login_scopes | default(['workflow']) | join(',') }}",
        "{{ claude_vm_names[0] }}",
        # The session notes' helper, their path, the Remote Control unit and
        # the time zone, for the refresh after a sign-in in the terminal.
        "{{ code_notes_script if code_notes | bool else '' }}",
        "{{ code_notes_file if code_notes | bool else '' }}",
        "{{ code_remote_control_unit }}",
        "{{ code_timezone }}",
        # --session, last: the script reads it as the last argument.
        "{{ code_session_id }}",
    ]
    assert task["runOptions"] == {"runOn": "folderOpen"}


# The window step runs on a PATH of its own: stand-ins for `which` and `code`
# first, so no VS Code of this machine's can be started, then the system's
# directories, which Ansible's local connection needs for its own work.
WINDOW_PATH = "/usr/bin:/bin"
ONE_VM = ["Refuse anything but one VM"]
VS_CODE = ["Look for VS Code on this machine", "Fail when VS Code's command is not on PATH"]
UP = ["Settle where the VM is reached", "Fail when the VM cannot be reached"]
EXTENSION = ["List VS Code's extensions", "Install the Remote - SSH extension when VS Code lacks it"]
STOPPED = [{"name": "beta", "vmid": 9004, "node": "pve", "status": "stopped", "address": ""}]
NO_ADDRESS = [{"name": "alpha", "vmid": 9001, "node": "pve", "status": "running", "address": ""}]
REFUSED = {
    "unreachable": True,
    "msg": "Failed to connect to the host via ssh: Warning: Permanently added 'alpha.lan'.\r\n"
    "\x1b[1mHost key verification failed.",
}
# What "Say what was opened" reads, as the tasks before it set it.
OPENED = {
    "code_target": "/home/dev/projects",
    "code_root": "/home/dev/projects",
    "code_ws_dir": "/home/dev/projects/.claude-on-proxmox",
    "code_project": "",
    "code_reach": "192.0.2.51",
    "code_notes_dir": "/home/dev/projects/.claude-on-proxmox/session",
    "code_notes_file": "/home/dev/projects/.claude-on-proxmox/session/alpha-session.md",
}
# The Claude task's arguments after the script, the session notes' three aside.
ARGS = "code_claude_task.args[3:11]"
NOTES = {**OPENED, "show": "code_workspace_file"}
SESSION = "/home/dev/projects/.claude-on-proxmox/session"
LATER = " VS Code was not opened; to open it later: just code alpha"


def _tagged(name, vmid):
    return {"type": "qemu", "vmid": vmid, "name": name, "node": "pve", "status": "running", "tags": "claude-on-proxmox"}


def _listing(*names):
    return {"json": {"data": [_tagged(name, 100 + i) for i, name in enumerate(names)]}}


def _the_window(play: dict) -> dict:
    return next(task for task in play["tasks"] if task.get("name") == "Open the VM in VS Code")


def _deploy_vars() -> dict:
    """The vars deploy.yml gives its import of code.yml."""
    imported = next(
        play
        for play in yaml.safe_load(DEPLOY.read_text())
        if play.get("ansible.builtin.import_playbook") == "playbooks/code.yml"
    )
    return imported.get("vars") or {}


def _said(output: str, task: str):
    """What a debug task printed, decoded; None when it did not run."""
    for part in output.split("TASK [")[1:]:
        if part.startswith(task + "]"):
            found = re.search(r"^ok: \[localhost\] => (\{$.*?^\})$", part, re.MULTILINE | re.DOTALL)
            return json.loads(found.group(1))["msg"] if found else None
    return None


def _recorded(output: str) -> dict:
    """What a run recorded for NEXT STEPS (callback_plugins/claude_on_proxmox.py), from its custom stats."""
    found = re.search(r"^\tRUN: (\{.*\})$", output, re.MULTILINE)
    return json.loads(found.group(1)).get("code_next", {}) if found else {}


def _failures(output: str) -> list[str]:
    """The message of every task that failed, rescued or not, decoded."""
    return [
        json.loads(found)["msg"] for found in re.findall(r"^fatal: \[localhost\]: FAILED! => (\{.*\})$", output, re.M)
    ]


def _window_extra(root: Path) -> dict:
    """What every window run is given before its case's own values.

    Never the developer's own VS Code settings, nor their time zone: what is said depends on them.
    With none of the zone's sources there, it is UTC. Nor the developer's Claude Code, at either
    end: both directories of sessions are the run's own, and the copying is off unless a case
    turns it on.
    """
    return {
        "ansible_python_interpreter": sys.executable,
        "vscode_user_settings_file": str(root / "none"),
        "code_tz_env": "",
        "code_localtime": str(root / "no-localtime"),
        "code_timezone_file": str(root / "no-timezone"),
        "code_session_source_dir": str(root / "here" / ".claude"),
        "code_session_dest_dir": str(root / "vm" / ".claude"),
        "vscode_claude_sessions": False,
    }


def _window(root: Path, keep: list[str], *, in_deploy: bool, code_here=False, local=None, show=None, **extra) -> dict:
    """Run the tasks of code.yml's window step named in `keep` as code.yml runs them, and say what came of it.

    In code.yml's order, inside its block and with its rescue, after its
    first task, which ends a fleet's deploy; with its play vars, and
    deploy.yml's for its import when `in_deploy`; over the inventory
    defaults, with `local` as a local.yml. Then one task that shows `show`,
    when given, and one that says the window was reached. No project config or
    inventory: a developer's own would bring in a vault this cannot open.
    """
    group_vars = root / "inventory" / "group_vars" / "all"
    group_vars.mkdir(parents=True)
    (group_vars / "defaults.yml").symlink_to(REPO / "inventory" / "group_vars" / "all" / "defaults.yml")
    if local:
        (group_vars / "local.yml").write_text(yaml.safe_dump(local))
    (root / "inventory" / "hosts.yml").write_text("all:\n  hosts:\n    localhost:\n      ansible_connection: local\n")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    # VS Code's command, found only where `code_here` says it is installed,
    # records what it is asked and cannot reach the marketplace.
    (bin_dir / "code").write_text(
        f'#!/bin/sh\necho "code $*" >> "{root}/code.log"\n'
        'if [ "$1" = --install-extension ]; then\n'
        '  echo "Error while installing extensions: getaddrinfo ENOTFOUND marketplace.visualstudio.com" >&2\n'
        '  echo "Failed Installing Extensions: $2" >&2\n'
        "  exit 1\n"
        "fi\n"
    )
    (bin_dir / "which").write_text(
        "#!/bin/sh\n" + (f'[ "$1" = code ] && echo "{bin_dir}/code" && exit 0\n' if code_here else "") + "exit 1\n"
    )
    for name in ("code", "which"):
        (bin_dir / name).chmod(0o755)

    play = copy.deepcopy(_play())
    del play["module_defaults"]
    if in_deploy:
        play["vars"].update(_deploy_vars())
    window = _the_window(play)
    window["block"] = [task for task in window["block"] if task["name"] in keep]
    assert [task["name"] for task in window["block"]] == keep, "keep names code.yml's tasks, in its order"
    if show:
        window["block"].append({"name": "Show it", "ansible.builtin.debug": {"msg": "{{ " + show + " }}"}})
    window["block"].append({"name": "Reach the window", "ansible.builtin.debug": {"msg": "reached"}})
    (root / "window.yml").write_text(yaml.safe_dump([play]))
    # What code.yml finds beside itself: the task files it imports, its modules and its templates.
    for name in ("tasks", "library", "templates"):
        (root / name).symlink_to(REPO / "playbooks" / name)

    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(
        ANSIBLE_CONFIG=ansible_config(root),
        ANSIBLE_FILTER_PLUGINS=str(REPO / "filter_plugins"),
        ANSIBLE_NOCOLOR="1",
        # The run's stats printed: what it recorded for NEXT STEPS.
        ANSIBLE_SHOW_CUSTOM_STATS="1",
        PATH=f"{bin_dir}:{WINDOW_PATH}",
        # Never the developer's git config: a url.<base>.insteadOf there rewrites the origins read.
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    extra = {**_window_extra(root), **extra}
    result = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", str(root / "inventory"), "-e", json.dumps(extra), str(root / "window.yml")],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    output = result.stdout + result.stderr
    asked = root / "code.log"
    return {
        "rc": result.returncode,
        "output": output,
        "reached": _said(output, "Reach the window") == "reached",
        "later": _said(output, "Say how to open the VM later"),
        "fleet": _said(output, "Say how to open each VM"),
        "shown": _said(output, "Show it"),
        "opened": _said(output, "Say what was opened"),
        "left": _recorded(output),
        "code": asked.read_text().splitlines() if asked.exists() else [],
    }


WINDOWS = {
    "deploy, code here": {"keep": ONE_VM + VS_CODE, "in_deploy": True, "code_here": True, "vm_name": "alpha"},
    "deploy, no code": {"keep": ONE_VM + VS_CODE, "in_deploy": True, "vm_name": "alpha"},
    "just code, code here": {"keep": ONE_VM + VS_CODE, "in_deploy": False, "code_here": True, "vm_name": "alpha"},
    "just code, no code": {"keep": ONE_VM + VS_CODE, "in_deploy": False, "vm_name": "alpha"},
    "deploy, no name": {"keep": ONE_VM, "in_deploy": True},
    "just code, no name": {"keep": ONE_VM, "in_deploy": False},
    "deploy, fleet": {"keep": ONE_VM, "in_deploy": True, "vm_name": "alpha,beta"},
    "just code, fleet": {"keep": ONE_VM, "in_deploy": False, "vm_name": "alpha,beta"},
    "deploy, stopped": {"keep": UP, "in_deploy": True, "vm_name": "beta", "claude_vm_addressed": STOPPED},
    "just code, stopped": {"keep": UP, "in_deploy": False, "vm_name": "beta", "claude_vm_addressed": STOPPED},
    "deploy, no address": {"keep": UP, "in_deploy": True, "vm_name": "alpha", "claude_vm_addressed": NO_ADDRESS},
    "deploy, no alias": {
        "keep": ["Refuse to run with the managed file switched off"],
        "in_deploy": True,
        "vm_name": "alpha",
        "claude_ssh_config": False,
    },
    "deploy, ssh refused": {
        "keep": ["Fail when SSH to the VM does not work"],
        "in_deploy": True,
        "vm_name": "alpha",
        "code_reach": "alpha.lan",
        "code_home": REFUSED,
    },
    "deploy, no extension": {"keep": EXTENSION, "in_deploy": True, "code_here": True, "vm_name": "alpha"},
    "just code, no extension": {"keep": EXTENSION, "in_deploy": False, "code_here": True, "vm_name": "alpha"},
    "deploy, no name, beside others": {
        "keep": ["Find the VM"],
        "in_deploy": True,
        "claude_vm_all": _listing("claude-on-proxmox-default", "alpha"),
    },
    "deploy, named": {
        "keep": ["Find the VM"],
        "in_deploy": True,
        "vm_name": "alpha",
        "claude_vm_all": _listing("claude-on-proxmox-default", "alpha"),
    },
    "deploy, twice": {
        "keep": ["Find the VM"],
        "in_deploy": True,
        "vm_name": "alpha",
        "claude_vm_all": _listing("alpha", "alpha"),
    },
    "just code, twice": {
        "keep": ["Find the VM"],
        "in_deploy": False,
        "vm_name": "alpha",
        "claude_vm_all": _listing("alpha", "alpha"),
    },
    "just code, missing": {
        "keep": ["Find the VM"],
        "in_deploy": False,
        "vm_name": "gamma",
        "claude_vm_all": _listing("alpha"),
    },
    "messages": {"keep": ["Say what was opened"], "in_deploy": False, "vm_name": "alpha", "show": ARGS, **OPENED},
    "messages, role-level switches": {
        "keep": ["Say what was opened"],
        "in_deploy": False,
        "vm_name": "alpha",
        "show": ARGS,
        "local": {
            "github_projects_auth": False,
            "claude_code_remote_control": False,
            "github_projects_login_scopes": ["workflow", "read:packages"],
            "github_projects_clone_protocol": "ssh",
            "claude_code_remote_control_service": "rc-test",
        },
        **OPENED,
    },
    "messages, inventory switches": {
        "keep": ["Say what was opened"],
        "in_deploy": False,
        "vm_name": "alpha",
        "show": ARGS,
        "local": {
            "github_auth": False,
            "claude_remote_control": False,
            "github_login_scopes": ["workflow", "read:packages"],
        },
        **OPENED,
    },
    "messages, token method": {
        "keep": ["Say what was opened"],
        "in_deploy": False,
        "vm_name": "alpha",
        "show": ARGS,
        "local": {"github_projects_auth_method": "token"},
        **OPENED,
    },
    # The session notes, by window: the workspace file as code.yml writes it, and what is said.
    "notes, whole directory": {"keep": ["Say what was opened"], "in_deploy": False, "vm_name": "alpha", **NOTES},
    "notes, one project": {
        "keep": ["Say what was opened"],
        "in_deploy": False,
        "vm_name": "alpha",
        **NOTES,
        "code_target": "/home/dev/projects/discord-music-bot",
        "code_project": "discord-music-bot",
    },
    "notes, workspaces elsewhere": {
        "keep": ["Say what was opened"],
        "in_deploy": False,
        "vm_name": "alpha",
        **NOTES,
        "code_ws_dir": "/home/dev/.vscode-workspaces",
        "code_notes_dir": "/home/dev/.vscode-workspaces/session",
        "code_notes_file": "/home/dev/.vscode-workspaces/session/alpha-session.md",
    },
    "notes, automatic tasks off": {
        "keep": ["Say what was opened"],
        "in_deploy": False,
        "vm_name": "alpha",
        **NOTES,
        "code_vscode_auto_tasks": "off",
    },
    "notes off": {
        "keep": ["Say what was opened"],
        "in_deploy": False,
        "vm_name": "alpha",
        "local": {"vscode_session_notes": False},
        **NOTES,
    },
}


@pytest.fixture(scope="module")
def windows(tmp_path_factory):
    """Every run in WINDOWS, side by side: each is a playbook run of its own, about a second long."""
    root = tmp_path_factory.mktemp("windows")
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = {name: pool.submit(_window, root / re.sub(r"\W+", "-", name), **case) for name, case in WINDOWS.items()}
    return {name: run.result() for name, run in runs.items()}


def test_a_deploy_for_one_vm_goes_on_to_the_window_named_or_not(windows):
    # Without vm_name, a deploy is for the default VM: one name all the same.
    for name in ("deploy, code here", "deploy, no name"):
        assert windows[name]["reached"] and windows[name]["rc"] == 0, windows[name]["output"]


def test_just_code_goes_on_to_the_window_where_vs_code_s_command_is(windows):
    run = windows["just code, code here"]
    assert run["reached"] and run["rc"] == 0, run["output"]


def test_just_code_without_vs_code_s_command_fails_and_says_how_to_get_it(windows):
    run = windows["just code, no code"]
    assert run["rc"] != 0 and not run["reached"]
    assert "`code` is not on this machine's PATH. In VS Code, run" in run["output"]
    assert run["later"] is None


def test_just_deploy_without_vs_code_s_command_says_so_with_the_just_code_line_and_goes_on(windows):
    run = windows["deploy, no code"]
    assert run["rc"] == 0 and not run["reached"], run["output"]
    assert run["later"].startswith("`code` is not on this machine's PATH. In VS Code, run")
    assert run["later"].endswith(LATER)
    assert run["left"] == {"alpha": {"vscode": run["later"]}}


def test_just_code_needs_one_vm_name(windows):
    for name in ("just code, fleet", "just code, no name"):
        run = windows[name]
        assert run["rc"] != 0 and "VS Code opens one VM at a time: just code <name>." in run["output"], name
        assert run["fleet"] is None, name


def test_a_deploy_for_a_fleet_ends_at_its_first_task_with_each_vm_s_just_code_line(windows):
    run = windows["deploy, fleet"]
    assert run["rc"] == 0 and not run["reached"], run["output"]
    assert run["fleet"] == [
        "VS Code opens one VM at a time, so this deploy for 2 VMs opened none. To open one:",
        "just code alpha",
        "just code beta",
    ]
    assert "TASK [Refuse anything but one VM]" not in run["output"]
    # Left to do for the fleet, once, as said.
    assert run["left"] == {"alpha, beta": {"vscode": "\n".join(run["fleet"])}}


def test_a_deploy_that_left_its_vm_stopped_names_the_deploy_that_starts_it(windows):
    # just code alone would open a VM this deploy never configured or signed in.
    run = windows["deploy, stopped"]
    assert run["rc"] == 0 and not run["reached"], run["output"]
    assert run["later"] == (
        "beta is stopped, and this deploy left it so: it was not configured, signed in or opened in VS Code. "
        "To start it and finish the deploy: just deploy beta --start"
    )
    # Recorded as the VM's own matter, in the place of provision's line about the same thing.
    assert run["left"] == {"beta": {"vm": run["later"]}}


def test_just_code_refuses_a_stopped_vm(windows):
    run = windows["just code, stopped"]
    assert run["rc"] != 0 and not run["reached"]
    assert "beta is stopped, so there is nothing to connect to. `just status` shows the fleet;" in run["output"]


@pytest.mark.parametrize(
    ("name", "said"),
    [
        ("deploy, no alias", "claude_ssh_config is false, so this project keeps no SSH alias for the VM"),
        (
            "deploy, no address",
            "alpha is running and reported no address, so there is nothing to connect to."
            " `just status` shows the fleet;",
        ),
        (
            "deploy, ssh refused",
            # The bold sequence goes whole (plain_text), not only its ESC.
            "alpha is running at alpha.lan, but SSH to it as dev failed, so VS Code could not connect either: "
            "Failed to connect to the host via ssh: Warning: Permanently added 'alpha.lan'. "
            "Host key verification failed.",
        ),
        (
            "deploy, no extension",
            "`code --install-extension ms-vscode-remote.remote-ssh` failed: Error while installing extensions: "
            "getaddrinfo ENOTFOUND marketplace.visualstudio.com "
            "Failed Installing Extensions: ms-vscode-remote.remote-ssh.",
        ),
        ("deploy, twice", 'More than one VM tagged "claude-on-proxmox" is named alpha'),
    ],
)
def test_just_deploy_says_any_failure_of_the_window_step_with_the_just_code_line_and_goes_on(windows, name, said):
    run = windows[name]
    assert run["rc"] == 0 and not run["reached"], run["output"]
    assert run["later"].startswith(said), run["later"]
    assert run["later"].endswith(LATER) and "\n" not in run["later"], run["later"]


def test_every_failure_of_the_window_step_is_said_on_one_line(windows):
    # A more-indented line in a folded scalar keeps its line break, and an
    # SSH error can carry one of its own, or the escapes of a VM's banner.
    failures = {name: _failures(run["output"]) for name, run in windows.items()}
    assert failures["just code, stopped"] and failures["deploy, ssh refused"]
    for name, said in failures.items():
        assert not [one for one in said if re.search(r"[\x00-\x1f\x7f]", one)], (name, said)


def test_just_code_fails_where_just_deploy_goes_on_and_names_the_command_that_failed(windows):
    run = windows["just code, no extension"]
    assert run["rc"] != 0 and not run["reached"]
    assert "`code --install-extension ms-vscode-remote.remote-ssh` failed: Error while installing" in run["output"]
    assert run["code"] == ["code --list-extensions", "code --install-extension ms-vscode-remote.remote-ssh"]


def test_a_bare_deploy_opens_the_default_vm_beside_other_tagged_ones(windows):
    # Without vm_name, vm_name is not defined, and claude_vm_requested is the
    # whole tagged fleet; the window step opens the one VM deploy was for.
    run = windows["deploy, no name, beside others"]
    assert run["reached"], run["output"]


def test_a_named_deploy_opens_that_vm(windows):
    run = windows["deploy, named"]
    assert run["reached"], run["output"]


def test_a_real_duplicate_or_a_missing_vm_is_still_refused(windows):
    twice, missing = windows["just code, twice"], windows["just code, missing"]
    assert twice["rc"] != 0 and "More than one VM tagged" in twice["output"], twice["output"]
    assert missing["rc"] != 0 and "No VM named gamma is tagged" in missing["output"], missing["output"]


def test_the_terminal_is_handed_the_lines_for_this_vm(windows):
    run = windows["messages"]
    not_installed, watched, unwatched, watch, github, protocol, scopes, vm = run["shown"]
    assert "just deploy alpha --only configure," in not_installed
    assert watched.startswith("Signed in. Remote Control starts by itself in a moment")
    assert watched.endswith("run on your machine: just deploy alpha --tags claude_code")
    assert unwatched == (
        "Signed in. To reach this VM from the Claude apps too, start Remote Control from your machine: "
        "just deploy alpha --tags claude_code"
    )
    assert watch == "/etc/systemd/system/claude-remote-control.path"
    assert github.endswith("run on your machine: just deploy alpha --tags github_projects")
    assert (protocol, scopes, vm) == ("https", "workflow", "alpha")
    assert "signing in to Claude and to GitHub first if alpha has not been" in run["opened"]


@pytest.mark.parametrize("name", ["messages, role-level switches", "messages, inventory switches"])
def test_the_terminal_follows_the_switches_the_roles_read(windows, name):
    # The inventory wires the role-level names to the user-facing ones, so an
    # override of either reaches the roles and the terminal alike.
    run = windows[name]
    _, watched, unwatched, _, github, _, scopes, _ = run["shown"]
    assert (watched, unwatched, github, scopes) == ("", "", "", "workflow,read:packages"), run["shown"]
    assert "signing in to Claude first if alpha has not been" in run["opened"]


def test_the_terminal_reads_the_role_s_own_service_and_protocol(windows):
    shown = windows["messages, role-level switches"]["shown"]
    assert (shown[3], shown[5]) == ("/etc/systemd/system/rc-test.path", "ssh")


def test_the_token_method_leaves_github_out_of_the_terminal(windows):
    run = windows["messages, token method"]
    assert run["shown"][4] == ""
    assert "signing in to Claude first if alpha has not been" in run["opened"]


# The task keywords code.yml uses; the one other key of a task is its action.
# A keyword missing here makes a task fail loudly, which is the point.
KEYWORDS = {
    "name",
    "vars",
    "when",
    "register",
    "delegate_to",
    "changed_when",
    "failed_when",
    "ignore_unreachable",
    "ignore_errors",
    "loop",
    "loop_control",
    "environment",
    "no_log",
    "tags",
    "until",
    "retries",
    "delay",
    "throttle",
    "run_once",
}
ON_VM = "{{ claude_vm_names[0] }}"
READS_HERE = {
    "ansible.builtin.assert",
    "ansible.builtin.fail",
    "ansible.builtin.debug",
    "ansible.builtin.set_fact",
    # What is left to do, recorded in the run's own stats for NEXT STEPS: no
    # machine is touched.
    "ansible.builtin.set_stats",
    "ansible.builtin.add_host",
    "ansible.builtin.import_tasks",
}
COMMANDS_HERE = {
    ("which", "code"),
    ("code", "--list-extensions"),
    ("code", "--install-extension"),
    ("code", "--remote"),
    # The sessions, sent to the VM's mirror: read here, written there.
    ("rsync", "-az"),
}
READS_ON_VM = {"ansible.builtin.getent", "ansible.builtin.stat", "ansible.builtin.find", "ansible.builtin.slurp"}
# The session handoff's readers (playbooks/library/), run here and on the VM alike: they change nothing.
READS_EITHER = {"claude_sessions_info", "git_checkouts_info"}
# On the VM: whether Remote Control runs, and with --session, git in the one checkout it moves.
COMMANDS_ON_VM = {("systemctl", "is-active"), ("git", "--no-optional-locks")}
WRITES_ON_VM = {"ansible.builtin.file", "ansible.builtin.copy", "claude_sessions_copy"}
# What each writer runs under: the workspace with the terminal; the notes with it and their own
# switch; the sessions' directory when there is a session to copy, and the copies with the sync or --session.
WRITERS_WHEN = (
    "code_terminal",
    "code_notes | bool",
    "code_session_plan.sessions | default([]) | length > 0",
    "code_session_on | bool",
)
# The task files code.yml imports that other playbooks import too, each held to its own tests: the
# Proxmox API reads and the alias (test_ssh_config.py). Every other import is code.yml's own, and
# its tasks are held to everything here as code.yml's are.
SHARED = {"tasks/cluster_vms.yml", "tasks/vm_addresses.yml", "tasks/ssh_config.yml"}


def _tasks(items, imports_from=None):
    # Every task, through block/rescue/always; a block itself has no action. With imports_from, the
    # directory imports are relative to, through code.yml's own imports too, and theirs in turn.
    for item in items:
        if "block" in item:
            for section in ("block", "rescue", "always"):
                yield from _tasks(item.get(section) or [], imports_from)
        elif imports_from and item.get("ansible.builtin.import_tasks") not in (None, *SHARED):
            path = imports_from / item["ansible.builtin.import_tasks"]
            yield from _tasks(yaml.safe_load(path.read_text()), imports_from)
        else:
            yield item


def _every_task():
    play = _play()
    for section in ("pre_tasks", "tasks", "post_tasks", "handlers"):
        yield from _tasks(play.get(section) or [], CODE.parent)


def test_no_import_escapes_the_allowlist(tmp_path):
    # Followed all the way down, an import that is left is one of the shared files: a task file of
    # code.yml's own that imports another is held to the allowlist through it.
    left = {task["ansible.builtin.import_tasks"] for task in _every_task() if "ansible.builtin.import_tasks" in task}
    assert left <= SHARED
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "outer.yml").write_text(
        yaml.safe_dump([{"name": "outer | Import", "ansible.builtin.import_tasks": "tasks/inner.yml"}])
    )
    (tmp_path / "tasks" / "inner.yml").write_text(
        yaml.safe_dump([{"name": "inner | Write", "ansible.builtin.shell": "touch /tmp/x"}])
    )
    found = list(_tasks([{"name": "Import", "ansible.builtin.import_tasks": "tasks/outer.yml"}], tmp_path))
    assert [task["name"] for task in found] == ["inner | Write"]


def test_code_yml_imports_its_own_task_files_and_the_shared_ones_alone():
    # A new import is followed above unless it is listed as shared, so it cannot pass unchecked.
    imported = {
        task["ansible.builtin.import_tasks"]
        for task in _tasks(_play()["tasks"])
        if "ansible.builtin.import_tasks" in task
    }
    assert imported == SHARED | {"tasks/session_read.yml", "tasks/session_write.yml"}


def _action(task):
    keys = set(task) - KEYWORDS
    assert len(keys) == 1, f"{task.get('name')}: {sorted(keys)}"
    return keys.pop()


@pytest.mark.parametrize("task", list(_every_task()), ids=lambda t: t.get("name", "?"))
def test_every_task_reads_this_machine_or_is_delegated_to_the_vm(task):
    action = _action(task)
    assert "connection" not in task
    assert "ansible_connection" not in (task.get("vars") or {})
    if action in READS_HERE:
        assert "delegate_to" not in task
    elif action == "ansible.builtin.command" and "delegate_to" in task:
        assert task["delegate_to"] == ON_VM
        assert tuple(task[action]["argv"][:2]) in COMMANDS_ON_VM
    elif action == "ansible.builtin.command":
        assert tuple(task[action]["argv"][:2]) in COMMANDS_HERE
    elif action in READS_ON_VM:
        assert task.get("delegate_to") == ON_VM
    elif action in READS_EITHER:
        assert task.get("delegate_to", ON_VM) == ON_VM
    elif action in WRITES_ON_VM:
        assert task.get("delegate_to") == ON_VM
        assert task.get("when") in WRITERS_WHEN
    elif action == "ansible.builtin.meta":
        # The one meta action: a deploy for a fleet ends the play.
        assert task[action] == "end_play", task.get("name")
    else:
        pytest.fail(f"{task.get('name')} uses {action}, which just code may not")


def test_every_task_of_the_window_step_is_in_the_block_just_deploy_rescues():
    # A task outside it would fail a deploy whose VM is done. Before it is
    # only the fleet's end, which opens nothing.
    tasks = _play()["tasks"]
    assert [task["name"] for task in tasks] == ["Leave the windows of a fleet for later", "Open the VM in VS Code"]
    assert [_action(task) for task in tasks[0]["block"]] == [
        "ansible.builtin.debug",
        "ansible.builtin.set_stats",
        "ansible.builtin.meta",
    ]
    assert set(tasks[1]) == {"name", "block", "rescue"}


def test_every_name_code_yml_sets_is_its_own():
    # code_, which no role uses: just deploy runs the roles in the same run,
    # and a fact or register of theirs outranks a play var. The inputs of the
    # project's task files keep those files' names. And no task var is also a
    # fact or a register, which would outrank it from then on.
    play = _play()
    facts, task_vars = set(), set()
    for task in _every_task():
        facts |= set(task.get("ansible.builtin.set_fact") or {}) | ({task["register"]} if "register" in task else set())
        if "ansible.builtin.import_tasks" not in task:
            task_vars |= set(task.get("vars") or {})
    names = set(play["vars"]) | facts | task_vars
    assert facts and task_vars
    assert sorted(name for name in names if not name.startswith("code_")) == []
    assert not (REPO / "roles" / "code").exists()
    assert sorted(facts & task_vars) == []


def test_just_code_writes_exactly_the_directory_and_the_file():
    # The allowlist must not pass by finding no writer.
    assert [t["name"] for t in _every_task() if _action(t) in WRITES_ON_VM] == [
        "session_write | Make the directory for the sessions on the VM",
        "session_write | Write the copies the VM resumes",
        "Make the directory for VS Code workspace files on the VM",
        "Write the VS Code workspace on the VM",
        "Make the directory for the session notes on the VM",
        "Write the session notes on the VM",
    ]


def test_git_on_the_vm_runs_only_for_session_and_moves_its_checkout_only_forward():
    # The one write just code makes in a repository: --session's checkout, fast-forward only. Every
    # git command on the VM is --session's, and none of them resets, rebases, stashes or pushes.
    on_vm = [t for t in _every_task() if _action(t) == "ansible.builtin.command" and "delegate_to" in t]
    git = [t for t in on_vm if t["ansible.builtin.command"]["argv"][0] == "git"]
    assert [t["ansible.builtin.command"]["argv"][4] for t in git] == [
        "fetch",
        "merge-base",
        "switch",
        "merge",
        "switch",
        "branch",
        "rev-parse",
    ]
    for task in git:
        when = task["when"] if isinstance(task["when"], list) else [task["when"]]
        assert when[0] == "code_session_id | length > 0", task["name"]
    merge = next(t for t in git if t["ansible.builtin.command"]["argv"][4] == "merge")
    assert "--ff-only" in merge["ansible.builtin.command"]["argv"]


def test_every_task_that_reads_a_transcript_is_no_log():
    # The readers return no content, but a transcript holds whatever its tools printed.
    for task in _every_task():
        if _action(task) in {"claude_sessions_info", "claude_sessions_copy"}:
            assert task.get("no_log") is True, task["name"]


def test_the_provision_scenario_never_reads_the_developer_s_vscode_settings():
    # Every code.yml run in the scenario passes verify_code_vars, which pins
    # vscode_user_settings_file into the ephemeral directory; a refusal case
    # that builds its own base must pin it too. What just code says depends
    # on that file, so a run that fell through to the developer's own would
    # pass or fail by the machine it runs on.
    plays = yaml.safe_load(PROVISION_VERIFY.read_text())
    play = next(p for p in plays if "verify_code_vars" in (p.get("vars") or {}))
    pinned = play["vars"]["verify_code_vars"]["vscode_user_settings_file"]
    assert "MOLECULE_EPHEMERAL_DIRECTORY" in pinned
    runs = [t for t in _tasks(play["tasks"]) if "verify_code_playbook" in str(t.get("ansible.builtin.command", ""))]
    assert len(runs) >= 6
    for task in runs:
        # On the command line, or on the stdin of the task that runs the refusals.
        assert "verify_code_vars" in str(task["ansible.builtin.command"]), task["name"]
    # The refusal cases, set on a task or on the block around them.
    cases = [case for item in _with_vars(play["tasks"]) for case in item["vars"].get("verify_code_cases", [])]
    assert len(cases) >= 12, cases
    for case in cases:
        if "base" in case:
            assert "vscode_user_settings_file" in case["base"], case


def _with_vars(items):
    """Every task and block with vars of its own, blocks' sections included."""
    for item in items:
        if item.get("vars"):
            yield item
        for section in ("block", "rescue", "always"):
            yield from _with_vars(item.get(section) or [])


def test_no_window_run_reads_or_writes_the_developer_s_claude_code(tmp_path):
    # just code reads this machine's ~/.claude and writes the VM's; in every run here the VM is this
    # machine too. Both are the run's own, whatever a case adds, and so is the VM's claude.
    given = _window_extra(tmp_path)
    for key in ("code_session_source_dir", "code_session_dest_dir"):
        assert Path(given[key]).is_relative_to(tmp_path), key
    for case in WINDOWS.values():
        assert not {"code_session_source_dir", "code_session_dest_dir"} & set(case)
    # The session runs name theirs, inside their world.
    source = inspect.getsource(_handoff)
    assert 'code_session_source_dir=str(world["here"]' in source and 'code_session_dest_dir=str(world["vm"]' in source


def test_the_provision_scenario_never_reads_or_writes_the_developer_s_claude_code():
    # The scenario runs code.yml with this machine as the VM: both ends would be the developer's own.
    plays = yaml.safe_load(PROVISION_VERIFY.read_text())
    play = next(p for p in plays if "verify_code_vars" in (p.get("vars") or {}))
    for key in ("code_session_source_dir", "code_session_dest_dir"):
        assert "MOLECULE_EPHEMERAL_DIRECTORY" in play["vars"]["verify_code_vars"][key], key
    runs = [t for t in _tasks(play["tasks"]) if "verify_code_playbook" in str(t.get("ansible.builtin.command", ""))]
    for task in runs:
        for case in (task.get("vars") or {}).get("verify_code_cases", []):
            if "base" in case:
                assert {"code_session_source_dir", "code_session_dest_dir"} <= set(case["base"]), case
    deploys = [
        task
        for each in plays
        for task in _tasks(each.get("tasks") or [])
        if "deploy.yml" in str(task.get("ansible.builtin.command", "")) and "pve2" not in str(task)
    ]
    assert deploys
    for task in deploys:
        argv = str(task["ansible.builtin.command"]["argv"])
        assert "code_session_source_dir" in argv and "code_session_dest_dir" in argv, task["name"]


def test_the_provision_scenario_s_deploy_runs_never_read_the_developer_s_vscode_settings_either():
    # just deploy ends with code.yml, so every deploy.yml run there pins the
    # file too, though none of them gets as far as reading it.
    plays = yaml.safe_load(PROVISION_VERIFY.read_text())
    runs = [
        task
        for play in plays
        for task in _tasks(play.get("tasks") or [])
        if "deploy.yml" in str(task.get("ansible.builtin.command", ""))
    ]
    assert runs
    for task in runs:
        argv = str(task["ansible.builtin.command"]["argv"])
        # The run that refuses to clone on another node stops long before code.yml.
        if "pve2" in argv:
            continue
        assert "vscode_user_settings_file" in argv and "MOLECULE_EPHEMERAL_DIRECTORY" in argv, task["name"]


# ---------------------------------------------------------------- the session notes in the window ---
def _workspace(run: dict) -> dict:
    return run["shown"]


def test_the_whole_directory_s_window_shows_the_notes_folder_and_opens_them_as_a_preview(windows):
    run = windows["notes, whole directory"]
    workspace = _workspace(run)
    # A second folder only where the window's own folder holds it: trust is unchanged.
    assert workspace["folders"] == [
        {"path": "/home/dev/projects"},
        {"path": SESSION, "name": "alpha · session"},
    ]
    assert workspace["settings"] == {
        "files.exclude": {".claude-on-proxmox": True},
        "workbench.editorAssociations": {
            "**/.claude-on-proxmox/session/*-session.md": "vscode.markdown.preview.editor"
        },
    }
    # Side by side when the window opens: the notes never hold Claude up. A
    # dependsOn made VS Code ask every task provider first, 7.5 s live.
    notes, shell, claude = workspace["tasks"]["tasks"]
    assert notes["label"] == "Session notes" and notes["runOptions"] == {"runOn": "folderOpen"}
    assert notes["args"][2:] == [
        "session-notes",
        f"{SESSION}/alpha-session.md",
        "alpha",
        "claude-remote-control",
        "UTC",
    ]
    assert notes["presentation"] == {
        "reveal": "never",
        "focus": False,
        "echo": False,
        "close": True,
        "showReuseMessage": False,
    }
    assert claude["label"] == "Claude" and "dependsOn" not in claude
    assert claude["runOptions"] == {"runOn": "folderOpen"}
    # Then --session, none here.
    assert claude["args"][-5:] == [notes["args"][1], f"{SESSION}/alpha-session.md", "claude-remote-control", "UTC", ""]
    # A shell beside Claude's terminal, on its left: one presentation group
    # splits a terminal in the order its tasks start, which is the order listed.
    assert (shell["label"], shell["command"], shell["args"]) == ("Shell", "/bin/bash", ["-l"])
    assert shell["options"] == {"cwd": "/home/dev/projects"} == claude["options"]
    assert shell["runOptions"] == {"runOn": "folderOpen"}
    assert shell["presentation"]["group"] == claude["presentation"]["group"] == "claude"
    assert shell["presentation"]["focus"] is False and claude["presentation"]["focus"] is True
    assert shell["presentation"]["close"] is True
    assert "A shell in the same folder opens to its left." in run["opened"]
    assert f"The session notes open beside it each time ({SESSION}/alpha-session.md)." in run["opened"]
    assert "may ask" not in run["opened"]
    assert run["opened"].endswith("vscode_session_notes: false turns them off.")


def test_a_project_s_window_keeps_its_one_folder_and_says_the_question_vs_code_may_ask(windows):
    # The notes' folder is outside the project's: added, it would put a window
    # trusted before back in Restricted Mode, without the Claude terminal.
    run = windows["notes, one project"]
    workspace = _workspace(run)
    assert workspace["folders"] == [{"path": "/home/dev/projects/discord-music-bot"}]
    assert [task["label"] for task in workspace["tasks"]["tasks"]] == ["Session notes", "Shell", "Claude"]
    assert workspace["tasks"]["tasks"][1]["options"] == {"cwd": "/home/dev/projects/discord-music-bot"}
    assert (
        "VS Code may ask once whether to open them, as a file outside this window's folder: Open, or trust the"
        " window of just code alpha, which covers them." in run["opened"]
    ), run["opened"]


def test_workspace_files_kept_elsewhere_give_no_window_the_notes_folder(windows):
    run = windows["notes, workspaces elsewhere"]
    workspace = _workspace(run)
    assert workspace["folders"] == [{"path": "/home/dev/projects"}]
    assert workspace["settings"] == {
        "workbench.editorAssociations": {"**/.vscode-workspaces/session/*-session.md": "vscode.markdown.preview.editor"}
    }
    assert "as a file outside this window's folder: Open." in run["opened"]
    assert "which covers them" not in run["opened"]


def test_with_automatic_tasks_off_the_notes_are_named_with_how_to_open_them(windows):
    run = windows["notes, automatic tasks off"]
    assert (
        f'The session notes are at {SESSION}/alpha-session.md: "Tasks: Run Task", Session notes opens them.'
        in run["opened"]
    ), run["opened"]
    # The shell does not start by itself either.
    assert '"Tasks: Run Task", Shell opens a shell in the same folder beside it.' in run["opened"]
    assert "opens to its left" not in run["opened"]


def test_no_task_of_the_workspace_depends_on_another():
    # VS Code looks a dependency of a workspace-file task up, misses it, and
    # asks every task provider instead: 7.5 s before Claude started, live.
    play = _play()
    for name in ("code_claude_task", "code_notes_task", "code_shell_task"):
        assert "dependsOn" not in play["vars"][name], name
    assert "dependsOn" not in str(play["vars"]["code_workspace_file"])


def test_with_the_notes_off_the_workspace_is_as_it_was(windows):
    run = windows["notes off"]
    workspace = _workspace(run)
    assert workspace["folders"] == [{"path": "/home/dev/projects"}]
    assert workspace["settings"] == {"files.exclude": {".claude-on-proxmox": True}}
    shell, claude = workspace["tasks"]["tasks"]
    assert (shell["label"], claude["label"]) == ("Shell", "Claude")
    assert "dependsOn" not in claude
    assert claude["args"][-5:] == ["", "", "claude-remote-control", "UTC", ""]
    assert "session notes" not in run["opened"]


# ---------------------------------------------------------------- the session notes' helper ---
# Run as the "Session notes" task runs it, with stand-ins for what it reads, and
# nothing else on PATH: never this machine's own claude, gh, tailscale or code.
LIVE_START, LIVE_END = "<!-- claude-on-proxmox:live -->", "<!-- /claude-on-proxmox:live -->"
NOTES_TEXT = (
    f"# alpha\n\nVM 101\n\n{LIVE_START}\n## Signed in\n\nOpening the window fills this in.\n{LIVE_END}\n"
    "\n## At a glance\n"
)
SIGNED_IN = {
    "claude": '{"loggedIn": true, "authMethod": "claude.ai", "email": "dev@example.com", "subscriptionType": "max"}',
    "systemctl": "active",
    "gh": '{"hosts":{"github.com":[{"active":true,"login":"octocat","state":"success","gitProtocol":"https",'
    '"token":"gho_NEVERSHOWN"}]}}',
    "tailscale": '{"BackendState":"Running","Self":{"DNSName":"alpha.tail1234.ts.net.",'
    '"TailscaleIPs":["100.64.0.7","fd7a:115c:a1e0::7"]}}',
}
SIGNED_OUT = {
    "claude": '{"loggedIn": false, "authMethod": "none"}',
    "systemctl": "inactive",
    # gh 2.81+ signed out: no github.com entry at all (tests/molecule/fake_gh.sh, from gh itself).
    "gh": '{"hosts":{}}',
    "tailscale": '{"BackendState":"NeedsLogin"}',
}


def _helper(
    tmp_path: Path,
    *,
    answers=SIGNED_IN,
    slow=(),
    python="real",
    text=NOTES_TEXT,
    refresh=False,
    errexit=False,
    zone="UTC",
):
    """Run the helper; its exit status, the seconds it took, the notes after, and what `code` was asked."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sleep").symlink_to("/bin/sleep")
    for name, answer in answers.items():
        stand_in = bin_dir / name
        # printf is the shell's own: nothing but the stand-ins is on PATH.
        wait = "exec /bin/sleep 30\n" if name in slow else ""
        stand_in.write_text(f"#!/bin/sh\n{wait}printf '%s\\n' '{answer}'\n")
        stand_in.chmod(0o755)
    (bin_dir / "code").write_text(f'#!/bin/sh\necho "code $*" >> "{tmp_path}/code.log"\n')
    (bin_dir / "code").chmod(0o755)
    if python == "real":
        (bin_dir / "python3").symlink_to(sys.executable)
    elif python is not None:
        body = {"raise": "exit 1", "hang": "exec /bin/sleep 30"}[python]
        (bin_dir / "python3").write_text(f"#!/bin/sh\n{body}\n")
        (bin_dir / "python3").chmod(0o755)
    notes = tmp_path / "alpha-session.md"
    if isinstance(text, bytes):
        notes.write_bytes(text)
    elif text is not None:
        notes.write_text(text)
    if notes.exists():
        notes.chmod(0o600)
    script = _play()["vars"]["code_notes_script"]
    argv = ["/bin/bash", *(["-e"] if errexit else []), "-c", script, "session-notes", str(notes), "alpha"]
    argv += ["claude-remote-control", zone, *(["refresh-only"] if refresh else [])]
    began = time.monotonic()
    result = subprocess.run(argv, env={"PATH": str(bin_dir)}, capture_output=True, text=True, check=False, timeout=60)
    took = time.monotonic() - began
    log = tmp_path / "code.log"
    after = notes.read_bytes() if notes.exists() and not notes.is_symlink() else None
    return result, took, after, (log.read_text().splitlines() if log.exists() else []), notes


def _live(text: bytes) -> str:
    decoded = text.decode()
    return decoded[decoded.index(LIVE_START) : decoded.index(LIVE_END) + len(LIVE_END)]


def test_the_helper_rewrites_the_live_section_only_and_opens_the_notes(tmp_path):
    result, took, after, asked, notes = _helper(tmp_path)
    assert result.returncode == 0 and result.stdout == "" and result.stderr == "", result
    before = NOTES_TEXT
    outside = before[: before.index(LIVE_START)], before[before.index(LIVE_END) + len(LIVE_END) :]
    text = after.decode()
    assert text.startswith(outside[0]) and text.endswith(outside[1])
    live = _live(after)
    user = pwd.getpwuid(os.getuid()).pw_name
    assert "| Claude Code | `dev@example.com` · claude.ai, max plan |" in live
    assert "| Remote Control | live: open claude.ai/code or the Claude app, and pick **alpha** |" in live
    assert "| GitHub (`gh`) | `octocat` · git over https |" in live
    assert (
        "| Tailscale | running as `alpha.tail1234.ts.net` · `100.64.0.7` · "
        f"`ssh {user}@alpha.tail1234.ts.net` |" in live
    ), live
    # Said with its zone, the one it was given.
    assert re.search(r"^\| \| as of \d\d:\d\d UTC on \d\d \w\w\w \|$", live, re.M), live
    assert "gho_" not in text
    assert stat.S_IMODE(notes.stat().st_mode) == 0o600
    assert asked == [f"code -r {notes}"]
    assert took < 4, took


# The time is said in the zone of the machine just code ran on, which the VM's clock (UTC) is not.
def test_the_helper_says_its_time_in_the_zone_it_is_given(tmp_path):
    _, _, after, _, _ = _helper(tmp_path, zone="Asia/Kolkata")
    assert re.search(r"^\| \| as of \d\d:\d\d IST on \d\d \w\w\w \|$", _live(after), re.M), _live(after)


@pytest.mark.parametrize("zone", ["Mars/Olympus", "../../etc/passwd", ""])
def test_the_helper_says_utc_for_a_zone_it_cannot_find(tmp_path, zone):
    # A zone the VM's tzdata lacks, or no zone: the time is still said, in UTC.
    _, _, after, _, _ = _helper(tmp_path, zone=zone)
    assert re.search(r"^\| \| as of \d\d:\d\d UTC on \d\d \w\w\w \|$", _live(after), re.M), _live(after)


def test_a_vm_signed_in_nowhere_names_each_sign_in(tmp_path):
    _, _, after, _, _ = _helper(tmp_path, answers=SIGNED_OUT)
    live = _live(after)
    assert "| Claude Code | not signed in: just login alpha --only claude on your machine |" in live
    assert "| Remote Control | not running (inactive) |" in live
    assert "| GitHub (`gh`) | not signed in: just login alpha --only github on your machine |" in live
    assert "| Tailscale | NeedsLogin: just login alpha --only tailscale signs it in |" in live


def test_what_a_tool_prints_is_read_as_utf_8_whatever_the_locale(tmp_path):
    # No LANG here: an account name outside ASCII is still read, never a failure that leaves the notes stale.
    answers = {**SIGNED_IN, "claude": SIGNED_IN["claude"].replace("dev@example.com", "dév@exämple.com")}
    _, _, after, _, _ = _helper(tmp_path, answers=answers)
    assert "| Claude Code | `dév@exämple.com` · claude.ai, max plan |" in _live(after)


def test_what_a_tool_prints_reaches_the_notes_as_plain_text_in_one_cell(tmp_path):
    # An account name is whoever chose it: a sequence that writes the clipboard (OSC 52) or clears the
    # screen when the notes are shown in a terminal, a column break, a code span closed early.
    answers = {
        **SIGNED_IN,
        "claude": SIGNED_IN["claude"].replace("dev@example.com", r"dev\u001b[2J\u009b31m@example.com\tx"),
        "gh": SIGNED_IN["gh"].replace('"octocat"', r'"octo\u001b]52;c;aGk=\u0007cat|`rm`\n"'),
    }
    _, _, after, _, _ = _helper(tmp_path, answers=answers)
    live = _live(after)
    assert "| Claude Code | `dev@example.com x` · claude.ai, max plan |" in live
    assert "| GitHub (`gh`) | `octocat\\|'rm'` · git over https |" in live
    assert all(ch == "\n" or (ord(ch) >= 0x20 and not 0x7F <= ord(ch) <= 0x9F) for ch in live), repr(live)
    for row in live.splitlines():
        if row.startswith("| "):
            assert len(re.findall(r"(?<!\\)\|", row)) == 3, row


def _helper_patterns() -> dict[str, str]:
    """The patterns the helper compiles, read from its Python."""
    script = _play()["vars"]["code_notes_script"]
    source = script.split("<<'PY' >/dev/null 2>&1 &\n", 1)[1].split("\nPY\n", 1)[0]
    patterns = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name in ("SEQUENCES", "CONTROL"):
                patterns[name] = ast.literal_eval(node.value.args[0])
    return patterns


def test_the_helper_removes_what_plain_text_removes():
    # It runs on the VM, so it has its own copy of plain_text's patterns, and this test keeps it one:
    # a sequence plain_text learns to remove, the notes must not keep.
    spec = importlib.util.spec_from_file_location("text_filters_for_code", REPO / "filter_plugins" / "text.py")
    text = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(text)
    helper = _helper_patterns()
    assert helper["SEQUENCES"] == text._SEQUENCES.pattern
    # A table cell takes no tab or newline either: every control character plain_text removes, and those two.
    every = "".join(map(chr, range(0x110000)))
    assert set(re.findall(helper["CONTROL"], every)) == set(text._CONTROL.findall(every)) | {"\t", "\n"}


@pytest.mark.parametrize(
    ("gh", "row"),
    [
        pytest.param('{"hosts":{"github.com":[]}}', "not signed in: just login ", id="an empty github.com"),
        pytest.param("", "unknown (gh auth status gave no status; it needs gh 2.81.0 or newer)", id="no JSON"),
        pytest.param('{"error":"unknown flag"}', "unknown (gh auth status gave no status", id="not gh's JSON"),
    ],
)
def test_the_github_row_tells_signed_out_from_a_gh_it_cannot_read(tmp_path, gh, row):
    _, _, after, _, _ = _helper(tmp_path, answers={**SIGNED_IN, "gh": gh})
    assert f"| GitHub (`gh`) | {row}" in _live(after)


def test_a_read_that_hangs_costs_two_seconds_and_is_said(tmp_path):
    # The four run at once, so a GitHub that does not answer delays the others by nothing.
    result, took, after, asked, _ = _helper(tmp_path, slow=("gh",))
    assert result.returncode == 0
    live = _live(after)
    assert "| GitHub (`gh`) | unknown (no answer in 2 s) |" in live
    assert "`dev@example.com`" in live and "alpha.tail1234.ts.net" in live
    assert asked and took < 5, took


@pytest.mark.parametrize("python", ["raise", "hang", None])
def test_the_helper_exits_0_and_opens_the_notes_whatever_its_python_does(tmp_path, python):
    # Whatever its Python does, the notes are left as they were and opened, and the helper ends 0,
    # so VS Code never reports the task failed with its whole command line.
    result, took, after, asked, notes = _helper(tmp_path, python=python)
    assert result.returncode == 0, result
    assert after == NOTES_TEXT.encode()
    assert asked == [f"code -r {notes}"]
    assert took < 8, took


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(NOTES_TEXT.encode().replace(b"VM 101", b"VM \xff\xfe"), id="not UTF-8"),
        pytest.param("# alpha\n\nno live section\n", id="no markers"),
    ],
)
def test_notes_it_cannot_read_or_has_no_section_in_are_left_as_they_are(tmp_path, text):
    result, _, after, asked, _ = _helper(tmp_path, text=text)
    assert result.returncode == 0
    assert after == (text if isinstance(text, bytes) else text.encode())
    assert asked


def test_a_symlink_at_the_notes_path_is_neither_followed_nor_opened(tmp_path):
    target = tmp_path / "elsewhere.md"
    target.write_text(NOTES_TEXT)
    (tmp_path / "alpha-session.md").symlink_to(target)
    result, _, _, asked, _ = _helper(tmp_path, text=None)
    assert result.returncode == 0
    assert target.read_text() == NOTES_TEXT
    assert asked == []


def test_errexit_from_a_login_profile_does_not_end_the_helper_early(tmp_path):
    # A login shell reads the user's profile, which may set -e; bash -e stands in for it.
    result, _, after, asked, _ = _helper(tmp_path, python="raise", errexit=True)
    assert result.returncode == 0 and asked, result


def test_refresh_only_waits_rewrites_and_opens_nothing(tmp_path):
    # After a sign-in in the Claude terminal: Remote Control is given a moment,
    # and the notes, open already, follow the file.
    result, took, after, asked, _ = _helper(tmp_path, refresh=True)
    assert result.returncode == 0
    assert "`dev@example.com`" in _live(after)
    assert asked == []
    assert took >= 5, took


def test_the_helper_hands_bash_nothing_it_could_lose_control_to():
    script = _play()["vars"]["code_notes_script"]
    for marker in ("{{", "{%", "{#", "${"):
        assert marker not in script
    assert not re.search(r"^\s*exec\b", script, re.M)
    assert script.splitlines()[0] == "set +e" and script.splitlines()[-1] == "exit 0"
    # Nothing waits on it any more, so no wait of its own; code -r in the
    # foreground, so the task's terminal does not close under it.
    assert "sleep 1" not in script
    assert re.search(r'^  code -r "\$1" </dev/null >/dev/null 2>&1$', script, re.M)
    assert re.search(r"""^python3 - "\$1" "\$2" "\$3" "\$4" <<'PY' >/dev/null 2>&1 &$""", script, re.M)


# ------------------------------------------------------------- the zone the notes say their time in ---
# Where /etc/localtime may point, under the run's own root: Linux's, macOS's (a versioned tzdata), a
# posix/ copy.
ZONES = {
    "TZ, a name": ({"tz": "Europe/Berlin"}, "Europe/Berlin"),
    "TZ, a zoneinfo path": ({"tz": ":/usr/share/zoneinfo/America/New_York"}, "America/New_York"),
    "the link, Linux": ({"link": "usr/share/zoneinfo/Asia/Kolkata"}, "Asia/Kolkata"),
    "the link, macOS": ({"link": "var/db/timezone/tz/2026d.1.0/zoneinfo/America/Los_Angeles"}, "America/Los_Angeles"),
    "the link, posix": ({"link": "usr/share/zoneinfo/posix/Europe/Paris"}, "Europe/Paris"),
    "TZ, a rule, then the link": (
        {"tz": "CET-1CEST,M3.5.0,M10.5.0/3", "link": "usr/share/zoneinfo/Asia/Tokyo"},
        "Asia/Tokyo",
    ),
    "a copy, then /etc/timezone": ({"copy": True, "file": "Australia/Sydney\n"}, "Australia/Sydney"),
    "nothing": ({}, "UTC"),
    "TZ, shell syntax": ({"tz": "a b;rm -rf ~"}, "UTC"),
    "a path out, both ways": ({"tz": "../../etc/passwd", "file": "../../etc/shadow\n"}, "UTC"),
}


def _zone(root: Path, *, tz="", link=None, copy=False, file=None) -> dict:
    root.mkdir(parents=True)
    localtime, timezone_file = root / "localtime", root / "timezone"
    if link:
        (root / link).parent.mkdir(parents=True)
        (root / link).write_bytes(b"TZif")
        localtime.symlink_to(root / link)
    elif copy:
        localtime.write_bytes(b"TZif")
    if file is not None:
        timezone_file.write_text(file)
    return _window(
        root / "run",
        [],
        in_deploy=False,
        vm_name="alpha",
        show="code_timezone",
        code_tz_env=tz,
        code_localtime=str(localtime),
        code_timezone_file=str(timezone_file),
    )


@pytest.fixture(scope="module")
def zones(tmp_path_factory):
    root = tmp_path_factory.mktemp("zones")
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = {
            name: pool.submit(_zone, root / re.sub(r"\W+", "-", name), **sources)
            for name, (sources, _) in ZONES.items()
        }
    return {name: run.result() for name, run in runs.items()}


@pytest.mark.parametrize("case", ZONES)
def test_the_notes_say_their_time_in_this_machine_s_zone_or_utc(zones, case):
    # TZ, then the zone /etc/localtime links to, then /etc/timezone; a zone's name only, else UTC.
    run = zones[case]
    assert run["rc"] == 0, run["output"]
    assert run["shown"] == ZONES[case][1]
    assert "[WARNING]" not in run["output"], run["output"]


# ------------------------------------------------- the Claude terminal refreshes them after a sign-in ---
def _signing_in(tmp_path: Path, helper: str | None, **case):
    """The Claude script with the notes' four arguments; what it printed, and what the helper was asked."""
    argv, env = _terminal(tmp_path, **case)
    (tmp_path / "bin" / "bash").symlink_to("/bin/bash")
    marker = tmp_path / "helper.log"
    script = "" if helper is None else f'printf "%s\\n" "$0 $*" >> "{marker}"'
    argv = [*argv[:-5], script, str(tmp_path / "alpha-session.md"), "claude-remote-control", "Asia/Kolkata", ""]
    # Every process the script starts inherits the pipe's write end, the helper it backgrounds too, so the pipe
    # ends only once they all have: the helper's log is whole when it is read, where a file that merely existed
    # could still be empty, and no helper can start after it.
    done, holder = os.pipe()
    try:
        result = subprocess.run(
            argv, env=env, capture_output=True, text=True, check=False, timeout=30, pass_fds=(holder,)
        )
    finally:
        os.close(holder)
    try:
        if not select.select([done], [], [], 30)[0]:
            pytest.fail("something the script started was still running 30 seconds after it")
    finally:
        os.close(done)
    return result, (marker.read_text().splitlines() if marker.exists() else [])


def test_a_claude_sign_in_in_the_terminal_refreshes_the_notes_in_the_background(tmp_path):
    result, called = _signing_in(tmp_path, "log", status_rc=1, login_rc=0)
    assert result.returncode == 0
    assert called == [
        f"session-notes {tmp_path}/alpha-session.md alpha claude-remote-control Asia/Kolkata refresh-only"
    ]
    # Its terminal sees what it saw before: nothing from the helper.
    assert result.stdout == SIGNING_IN + "\n" + UNWATCHED + "\n"


def test_a_github_sign_in_in_the_terminal_refreshes_the_notes(tmp_path):
    _, called = _signing_in(tmp_path, "log", status_rc=0, gh_status_rc=1, gh_login_rc=0)
    assert called and called[0].endswith("refresh-only")


@pytest.mark.parametrize(
    "case",
    [
        pytest.param({"status_rc": 0}, id="signed in already"),
        pytest.param({"status_rc": 1, "login_rc": 1}, id="a sign-in that failed"),
    ],
)
def test_no_sign_in_here_leaves_the_notes_to_the_window(tmp_path, case):
    _, called = _signing_in(tmp_path, "log", **case)
    time.sleep(0.5)
    assert called == []


def test_without_notes_a_sign_in_starts_no_helper(tmp_path):
    result, called = _signing_in(tmp_path, None, status_rc=1, login_rc=0)
    assert result.returncode == 0 and called == []


# ------------------------------------------------------------- the session handoff in the window ---
# The window step's own session tasks, run as just code runs them, between two directories that stand
# for this machine and the VM: real git checkouts at both ends, cloned from one bare repository whose
# path ends like a GitHub URL, a ~/.claude with transcripts at each, and rsync to a local path, as
# Ansible reaches the VM locally here. Neither end is the developer's.
SESSION_KEEP = [
    "Reach the VM the way just deploy --only configure does",
    "Read the sessions to carry to the VM",
    "Settle the folder to open",
    "Carry the sessions to the VM",
    "Say what was opened",
]
OTHER_ID = "bc0e637b-2c3d-4e4f-9a01-23456789abcd"
STUB_ID = "178468f7-0000-4000-8000-000000000000"
# What a tool printed, as a transcript holds it: no run's output may carry it.
MARKER = "TRANSCRIPT-MARKER-6f1d2c"


def _git(path: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True, env=env
    ).stdout.strip()


def _commit(path: Path, name: str) -> str:
    (path / name).write_text(name + "\n")
    _git(path, "add", name)
    _git(path, "-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "--quiet", "-m", name)
    return _git(path, "rev-parse", "HEAD")


def _transcript(sid: str, cwd: Path, here: Path, branch: str, *, conversation=True) -> bytes:
    folder = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    base = {"cwd": str(cwd), "sessionId": sid, "version": "2.1.308", "gitBranch": branch}
    memory = f"{here}/projects/{folder}/memory/"
    entries = [
        {
            **base,
            "type": "attachment",
            "attachment": {
                "type": "prompt_snapshot",
                "systemPrompt": [f"You have a persistent file-based memory at `{memory}`."],
            },
        },
    ]
    if conversation:
        saved = f"{here}/projects/{folder}/{sid}/tool-results/toolu_1.txt"
        entries += [
            {**base, "type": "user", "message": {"role": "user", "content": f"print the token {MARKER}"}},
            {
                **base,
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "content": f"Saved to {saved}"}]},
            },
            {
                **base,
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": MARKER}]},
            },
        ]
    return "".join(json.dumps(entry, separators=(",", ":")) + "\n" for entry in entries).encode()


def _world(root: Path) -> dict:
    """This machine and a VM under root, the session ID's work committed on feat/x and pushed."""
    root = root.resolve()
    bare = root / "remotes" / "octo" / "demo.git"
    bare.mkdir(parents=True)
    _git(bare, "init", "--quiet", "--bare", "--initial-branch", "main")
    seed = root / "seed"
    seed.mkdir()
    _git(seed, "init", "--quiet", "--initial-branch", "main")
    _commit(seed, "README")
    _git(seed, "push", "--quiet", str(bare), "main")
    here, vm = root / "here", root / "vm"
    demo = here / "src" / "demo"
    demo.parent.mkdir(parents=True)
    _git(demo.parent, "clone", "--quiet", str(bare), "demo")
    _git(demo, "switch", "--quiet", "--create", "feat/x")
    commit = _commit(demo, "work")
    _git(demo, "push", "--quiet", "--set-upstream", "origin", "feat/x")
    other = here / "src" / "other"
    other.mkdir()
    _git(other, "init", "--quiet")
    _git(other, "remote", "add", "origin", "https://github.com/octo/other.git")
    (vm / "projects").mkdir(parents=True)
    _git(vm / "projects", "clone", "--quiet", str(bare), "demo")
    # Its origin GitHub's, as just deploy clones one, and only a rewrite of its own sends git to the bare
    # repository here: the VM's origins name a repository only as GitHub's.
    _git(vm / "projects" / "demo", "remote", "set-url", "origin", "https://github.com/octo/demo.git")
    _git(vm / "projects" / "demo", "config", f"url.{bare}.insteadOf", "https://github.com/octo/demo.git")
    claude = vm / ".local" / "bin" / "claude"
    claude.parent.mkdir(parents=True)
    claude.write_text("#!/bin/sh\necho '2.1.308 (Claude Code)'\n")
    claude.chmod(0o755)
    folder = here / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(demo))
    (folder / SESSION_ID / "tool-results").mkdir(parents=True)
    (folder / SESSION_ID / "subagents").mkdir()
    (folder / SESSION_ID / "tool-results" / "toolu_1.txt").write_text(f"{MARKER}\n")
    (folder / SESSION_ID / "subagents" / "agent-1.jsonl").write_text("{}\n")
    (folder / f"{SESSION_ID}.jsonl").write_bytes(_transcript(SESSION_ID, demo, here / ".claude", "feat/x"))
    (folder / f"{STUB_ID}.jsonl").write_bytes(
        _transcript(STUB_ID, demo, here / ".claude", "feat/x", conversation=False)
    )
    elsewhere = here / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(other))
    elsewhere.mkdir(parents=True)
    (elsewhere / f"{OTHER_ID}.jsonl").write_bytes(_transcript(OTHER_ID, other, here / ".claude", "main"))
    return {
        "root": root,
        "bare": bare,
        "here": here,
        "vm": vm,
        "demo": demo,
        "folder": folder,
        "commit": commit,
        "checkout": vm / "projects" / "demo",
    }


def _handoff(world: dict, run: str, sessions=True, **extra) -> dict:
    return _window(
        world["root"] / run,
        SESSION_KEEP,
        in_deploy=False,
        vm_name="alpha",
        code_reach="192.0.2.51",
        ansible_connection="local",
        vscode_claude_sessions=sessions,
        vm_projects_dir=str(world["vm"] / "projects"),
        code_session_source_dir=str(world["here"] / ".claude"),
        code_session_dest_dir=str(world["vm"] / ".claude"),
        show="[code_target, code_claude_task.args[-1]]",
        **extra,
    )


def _changed(run: dict) -> int:
    return int(re.search(r"^localhost\s*:\s*ok=\d+\s+changed=(\d+)", run["output"], re.MULTILINE).group(1))


def _sync_twice(root: Path) -> dict:
    world = _world(root)
    return {"world": world, "first": _handoff(world, "first"), "second": _handoff(world, "second")}


def _session_twice(root: Path) -> dict:
    world = _world(root)
    first = _handoff(world, "first", vm_session=SESSION_ID)
    return {"world": world, "first": first, "second": _handoff(world, "second", vm_session=SESSION_ID)}


def _off(root: Path) -> dict:
    world = _world(root)
    return {"world": world, "off": _handoff(world, "off", sessions=False)}


def _head(path: Path) -> tuple[str, str]:
    """Where a checkout is: its commit, and its branch or "" for a detached HEAD."""
    branch = subprocess.run(
        ["git", "-C", str(path), "symbolic-ref", "--quiet", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    return _git(path, "rev-parse", "HEAD"), branch


def _refused(root: Path, spoil) -> dict:
    world = _world(root)
    spoil(world)
    before = _head(world["checkout"])
    return {"world": world, "before": before, "run": _handoff(world, "run", vm_session=SESSION_ID)}


def _moved(root: Path, place) -> dict:
    world = _world(root)
    place(world)
    return {"world": world, "run": _handoff(world, "run", vm_session=SESSION_ID)}


def _sync_failing(root: Path) -> dict:
    world = _world(root)
    _no_mirror(world)
    return {"world": world, "run": _handoff(world, "run")}


def _untracked(path: Path) -> None:
    (path / "notes.txt").write_text("not committed\n")


def _no_mirror(world: dict) -> None:
    # Where rsync sends the sessions, a file: it fails, as one missing on either machine does.
    sessions = world["vm"] / "projects" / ".claude-on-proxmox" / "claude-sessions"
    sessions.mkdir(parents=True)
    (sessions / "mirror").write_text("in the way\n")


def _changed_on_both(world: dict) -> None:
    _handoff(world, "before")
    line = b'{"type":"user","message":{"role":"user","content":"more"}}\n'
    for path in (world["vm"] / ".claude" / "projects" / world["folder"].name, world["folder"]):
        with open(path / f"{SESSION_ID}.jsonl", "ab") as handle:
            handle.write(line)


# A process whose argv is claude's, for the registry entries below: a session is open only while one of
# those runs at the pid its entry names. Started by the handoffs fixture.
CLAUDE: dict = {}


def _register(claude_dir: Path) -> None:
    """Register SESSION_ID in claude_dir's sessions/ as a running claude does, under that claude's pid."""
    (claude_dir / "sessions").mkdir(parents=True, exist_ok=True)
    (claude_dir / "sessions" / f"{CLAUDE['pid']}.json").write_text(
        json.dumps({"pid": CLAUDE["pid"], "sessionId": SESSION_ID})
    )


def _open_on_the_vm(world: dict) -> None:
    # Copied by an earlier run, and resumed there by a claude still running.
    _handoff(world, "before")
    _register(world["vm"] / ".claude")


def _fetch_fails(world: dict) -> None:
    checkout = world["checkout"]
    _git(checkout, "config", "--remove-section", f"url.{world['bare']}")
    _git(checkout, "config", f"url.{world['root']}/nowhere.git.insteadOf", "https://github.com/octo/demo.git")


def _other_remote(world: dict) -> None:
    _git(world["demo"], "remote", "add", "fork", str(world["bare"]))
    _git(world["demo"], "fetch", "--quiet", "fork")
    _git(world["demo"], "branch", "--quiet", "--set-upstream-to=fork/feat/x")


def _ignored_on_the_vm(world: dict) -> None:
    # The session's commit adds "work"; the VM's git ignores the one there, so its tree reads clean.
    (world["checkout"] / ".git" / "info" / "exclude").write_text("work\n")
    (world["checkout"] / "work").write_text("the VM's own\n")


REFUSALS = {
    "dirty here": (lambda w: _untracked(w["demo"]), "has changes that are not committed, which alpha would not see"),
    "not pushed": (lambda w: _commit(w["demo"], "more"), "feat/x in {demo} is not where origin/feat/x is here"),
    "dirty on the vm": (lambda w: _untracked(w["checkout"]), "{checkout} on alpha has changes of its own"),
    "vm branch ahead": (
        lambda w: (_git(w["checkout"], "switch", "--quiet", "--create", "feat/x"), _commit(w["checkout"], "vm-only")),
        "feat/x in {checkout} on alpha has commits that are not on this machine's",
    ),
    "open here": (
        lambda w: _register(w["here"] / ".claude"),
        f"Session {SESSION_ID} is open on this machine (claude, pid {{pid}})",
    ),
    "unknown": (
        lambda w: (w["folder"] / f"{SESSION_ID}.jsonl").rename(w["folder"] / "moved.jsonl"),
        f"There is no session {SESSION_ID} on this machine",
    ),
    "open on the vm": (_open_on_the_vm, f"Session {SESSION_ID} is open on alpha (claude, pid {{pid}})"),
    "changed on both": (
        _changed_on_both,
        f"Session {SESSION_ID} was continued on alpha and here since just code last copied it",
    ),
    "fetch fails": (_fetch_fails, "alpha could not fetch feat/x from origin in {checkout}"),
    "never pushed": (
        lambda w: _git(w["demo"], "branch", "--quiet", "--unset-upstream"),
        "feat/x in {demo} has never been pushed: git push -u origin feat/x",
    ),
    "other remote": (_other_remote, "feat/x in {demo} tracks a branch of fork, not of origin"),
    "branch gone here": (
        lambda w: (_git(w["demo"], "switch", "--quiet", "main"), _git(w["demo"], "branch", "--quiet", "-D", "feat/x")),
        f"The branch session {SESSION_ID} last ran on, feat/x, is not in {{demo}} any more.",
    ),
    "detached on the vm": (
        lambda w: (_git(w["checkout"], "switch", "--quiet", "--detach"), _commit(w["checkout"], "lost")),
        "{checkout} on alpha is on a detached HEAD at",
    ),
    "ignored on the vm": (
        _ignored_on_the_vm,
        "would overwrite what git does not track there, ignored files among them: work.",
    ),
    "rsync fails": (_no_mirror, "`rsync -az -r --files-from=- --itemize-changes"),
}
# The VM checkout brought forward by a fast-forward: on the session's branch behind its commit, or
# on main with that branch behind it.
MOVES = {
    "behind on it": lambda w: _git(w["checkout"], "switch", "--quiet", "--create", "feat/x"),
    "behind elsewhere": lambda w: _git(w["checkout"], "branch", "--quiet", "feat/x"),
}


def _sync_with(root: Path, place) -> dict:
    world = _world(root)
    place(world)
    return {"world": world, "run": _handoff(world, "run")}


def _in_two_folders(world: dict) -> None:
    # The same session here under another folder as well, as a copy by hand leaves it.
    twice = world["here"] / ".claude" / "projects" / "-Users-me-demo-copy"
    twice.mkdir()
    (twice / f"{SESSION_ID}.jsonl").write_bytes((world["folder"] / f"{SESSION_ID}.jsonl").read_bytes())


# Syncs without --session whose one session is left out, each for what the run says of it.
SYNCS = {
    "open there": (lambda w: _register(w["vm"] / ".claude"), "Left as they are, open in a claude on alpha: 0a608ad8."),
    "in two folders": (
        _in_two_folders,
        "Not copied, in more than one folder here, so claude --resume could not tell which: 0a608ad8.",
    ),
    "diverged only": (
        _changed_on_both,
        "Left as they are, changed both there and here since they were copied: 0a608ad8;",
    ),
}


@pytest.fixture(scope="module")
def handoffs(tmp_path_factory):
    """Every session run, side by side: the sync twice, --session twice, and each refusal, in worlds of their own."""
    root = tmp_path_factory.mktemp("handoffs")
    claude = subprocess.Popen(["claude", "3600"], executable="/bin/sleep")
    CLAUDE["pid"] = claude.pid
    try:
        yield _every_handoff(root)
    finally:
        claude.kill()
        claude.wait()


def _every_handoff(root: Path) -> dict:
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = {
            "sync": pool.submit(_sync_twice, root / "sync"),
            "session": pool.submit(_session_twice, root / "session"),
            "off": pool.submit(_off, root / "off"),
        }
        for name, (spoil, _) in REFUSALS.items():
            runs[name] = pool.submit(_refused, root / re.sub(r"\W+", "-", name), spoil)
        for name, place in MOVES.items():
            runs[name] = pool.submit(_moved, root / re.sub(r"\W+", "-", name), place)
        runs["sync fails"] = pool.submit(_sync_failing, root / "sync-fails")
        for name, (place, _) in SYNCS.items():
            runs[name] = pool.submit(_sync_with, root / re.sub(r"\W+", "-", name), place)
    return {name: run.result() for name, run in runs.items()}


def test_the_sync_copies_the_vm_s_repositories_sessions_with_their_edits(handoffs):
    world, run = handoffs["sync"]["world"], handoffs["sync"]["first"]
    assert run["reached"] and run["rc"] == 0, run["output"]
    folder = world["folder"].name
    copies = world["vm"] / ".claude" / "projects"
    # The session of the VM's repository, under the folder it has here; not the other repository's, nor the stub.
    assert sorted(path.name for path in copies.iterdir()) == [folder]
    copied = copies / folder / f"{SESSION_ID}.jsonl"
    entries = [json.loads(line) for line in copied.read_text().splitlines()]
    vm_memory = f"{world['vm']}/.claude/projects/{re.sub(r'[^A-Za-z0-9]', '-', str(world['checkout']))}/memory/"
    assert entries[0]["attachment"]["systemPrompt"] == [f"You have a persistent file-based memory at `{vm_memory}`."]
    assert entries[-1] == {"type": "relocated", "relocatedCwd": str(world["checkout"]), "sessionId": SESSION_ID}
    # The output the tool saved is named where the copy keeps it.
    vm_results = copies / folder / SESSION_ID / "tool-results"
    assert entries[2]["message"]["content"][0]["content"] == f"Saved to {vm_results}/toolu_1.txt"
    assert (vm_results / "toolu_1.txt").exists()
    assert sorted(path.name for path in (copies / folder / SESSION_ID).iterdir()) == ["tool-results"]
    assert int(copied.stat().st_mtime) == int((world["folder"] / f"{SESSION_ID}.jsonl").stat().st_mtime)
    assert stat.S_IMODE(copied.stat().st_mode) == 0o600
    assert stat.S_IMODE((world["vm"] / "projects" / ".claude-on-proxmox" / "claude-sessions").stat().st_mode) == 0o700
    assert run["opened"].endswith(
        "1 of this machine's Claude Code sessions of the last 30 days, of octo/demo, is on alpha (1 copied now): in"
        " claude there, /resume and then Ctrl+A lists them. vscode_claude_sessions: false turns the copying off."
    )
    # The default sync changes no git state, and opens the whole directory.
    assert _git(world["checkout"], "symbolic-ref", "--short", "HEAD") == "main"
    assert run["shown"] == [str(world["vm"] / "projects"), ""]


def test_a_second_sync_of_unchanged_sessions_changes_nothing(handoffs):
    run = handoffs["sync"]["second"]
    assert run["rc"] == 0 and _changed(run) == 0, run["output"]
    assert "(0 copied now)" in run["opened"]


def test_with_the_sync_off_nothing_is_read_or_copied(handoffs):
    world, run = handoffs["off"]["world"], handoffs["off"]["off"]
    assert run["reached"] and run["rc"] == 0, run["output"]
    assert not (world["vm"] / ".claude").exists()
    assert not (world["vm"] / "projects" / ".claude-on-proxmox" / "claude-sessions").exists()
    assert "copied" not in run["opened"] and "vscode_claude_sessions" not in run["opened"]


def test_no_run_says_anything_a_transcript_holds(handoffs):
    for name, runs in handoffs.items():
        for key, run in runs.items():
            if key not in ("world", "before"):
                assert MARKER not in run["output"], (name, key)


def test_session_brings_the_vm_checkout_to_its_commit_and_the_terminal_resumes_it(handoffs):
    world, run = handoffs["session"]["world"], handoffs["session"]["first"]
    assert run["rc"] == 0, run["output"]
    assert _git(world["checkout"], "symbolic-ref", "--short", "HEAD") == "feat/x"
    assert _git(world["checkout"], "rev-parse", "HEAD") == world["commit"]
    assert _git(world["checkout"], "rev-parse", "--abbrev-ref", "feat/x@{upstream}") == "origin/feat/x"
    # The project's window, its Claude terminal handed the ID.
    assert run["shown"] == [str(world["checkout"]), SESSION_ID]
    assert f"The Claude terminal resumes session {SESSION_ID} in {world['checkout']}, on feat/x at" in run["opened"]
    assert "where just code moved it" in run["opened"] and "reload it" in run["opened"]


def test_session_again_changes_nothing(handoffs):
    run = handoffs["session"]["second"]
    assert run["rc"] == 0 and _changed(run) == 0, run["output"]
    assert "where just code moved it" not in run["opened"]


@pytest.mark.parametrize("name", REFUSALS)
def test_session_refuses_what_it_cannot_carry_and_leaves_the_vm_checkout_as_it_was(handoffs, name):
    world, run = handoffs[name]["world"], handoffs[name]["run"]
    said = REFUSALS[name][1].format(demo=world["demo"], checkout=world["checkout"], pid=CLAUDE["pid"])
    assert run["rc"] != 0 and any(said in failure for failure in _failures(run["output"])), run["output"]
    assert not run["reached"]
    assert _head(world["checkout"]) == handoffs[name]["before"]
    assert MARKER not in run["output"]


@pytest.mark.parametrize("name", MOVES)
def test_session_fast_forwards_a_vm_branch_behind_its_commit(handoffs, name):
    world, run = handoffs[name]["world"], handoffs[name]["run"]
    assert run["rc"] == 0, run["output"]
    assert _head(world["checkout"]) == (world["commit"], "feat/x")
    assert "where just code moved it" in run["opened"]


@pytest.mark.parametrize("name", SYNCS)
def test_a_sync_leaves_out_what_it_cannot_copy_safely_and_says_so(handoffs, name):
    world, run = handoffs[name]["world"], handoffs[name]["run"]
    assert run["rc"] == 0 and run["reached"], run["output"]
    # The one session of the VM's repository, left: so nothing is copied, and the run says that, never
    # that no session is of a repository the VM has.
    assert run["opened"].count("No Claude Code session of this machine was copied to alpha.") == 1
    assert "is of a repository alpha has checked out" not in run["opened"]
    assert SYNCS[name][1] in run["opened"]
    if name != "diverged only":
        assert not (world["vm"] / ".claude" / "projects" / world["folder"].name / f"{SESSION_ID}.jsonl").exists()


def test_a_sync_whose_rsync_fails_says_so_and_still_opens_the_window(handoffs):
    world, run = handoffs["sync fails"]["world"], handoffs["sync fails"]["run"]
    assert run["rc"] == 0 and run["reached"], run["output"]
    assert "rsync could not send them all to alpha (it exited " in run["opened"]
    assert f"Not copied: {SESSION_ID[:8]}, rsync did not bring it." in run["opened"]
    assert not (world["vm"] / ".claude" / "projects" / world["folder"].name / f"{SESSION_ID}.jsonl").exists()
