"""The Claude session `just code` starts in the window it opens, the window step around it, and what it may write.

`playbooks/code.yml` hands VS Code a workspace file whose Claude task runs
`bash -l <helpers>/claude-terminal.sh <not installed> <signed in, watched>
<signed in, unwatched> <the login's path unit> <signed in to GitHub>
<git protocol> <scopes> <vm name> ...` when the window opens, the helper being
playbooks/files/claude-terminal.sh as just code copies it to the VM. It is run
here as it is, against stand-ins for `claude`, `gh` and the BROWSER helper that
record what they were asked: PATH holds the stand-ins alone, and the script
uses only shell builtins besides them, so nothing on this machine is reached.
One run has a pseudo-terminal of its own, to press Ctrl-C during GitHub's
device flow. The Molecule provision scenario checks the workspace file and the
task around it. Zed is handed a task file instead, whose Claude task runs the
shared playbooks/files/claude-start.sh, which the window cases here write and
check.

The window step is run too, a few of code.yml's own tasks at a time, inside
its block and with its rescue, its play vars, deploy.yml's for its import and
the inventory defaults: as just code and as just deploy's last step, for one
VM, none named and a fleet, a VM left stopped, no `code` command, no alias, a
refused SSH connection and an extension that would not install; and Zed's: no
Zed command, ZFS's `zed` in its place, a folder its URL cannot carry, a tasks
file that is not just code's. `which`, `code` and Zed's commands are stand-ins
there, first on a PATH that holds no other, so no editor of this machine's is
ever started.

The rest holds every task in `code.yml` to an allowlist, because the playbook
reads the user's own VS Code settings and must never write them. On this
machine a task may assert, fail, debug, set facts, add the host, import the
project's task files, or run `which`, an editor's `--version`, `code`, the
window's launch the plan built, and rsync; on the VM, reached only
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
import inspect
import json
import os
import pwd
import re
import select
import shutil
import stat
import subprocess
import sys
import termios
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml
from harness import EDITOR_FILES_DIR as PLANNED_FD
from harness import _at_a_pseudo_terminal, _env, _fail_if_the_desktop_was_reached, editor_inputs, verify_plays

from claude_vm.editors import common as editors_common
from claude_vm.editors import registry as editors_registry
from claude_vm.editors import vscode as editors_vscode
from claude_vm.editors import zed as editors_zed

REPO = Path(__file__).resolve().parents[2]
CODE = REPO / "playbooks" / "code.yml"
# The helpers just code copies to the VM unchanged, which the workspace's tasks run there by path.
HELPERS = REPO / "playbooks" / "files"
TERMINAL = HELPERS / "claude-terminal.sh"
NOTES_HELPER = HELPERS / "session-notes.sh"
NOTES_PYTHON = HELPERS / "session_notes.py"
DEPLOY = REPO / "deploy.yml"
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
PROVISION = REPO / "molecule" / "provision"
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


def _vars() -> dict:
    """code.yml's play vars, with each editor's own from the vars files it names."""
    play = _play()
    found = dict(play["vars"])
    for name in play.get("vars_files") or []:
        found.update(yaml.safe_load((CODE.parent / name).read_text()))
    return found


# Each editor's task files, as code.yml imports them: for that editor alone.
EDITOR_FILES = {
    "tasks/vscode_check.yml": "vscode",
    "tasks/zed_check.yml": "zed",
    # Every editor's window, from its plan.
    "tasks/editor_open.yml": None,
}


def _inlined(block: list) -> list:
    """code.yml's window step with each editor's imports replaced by their tasks, each under the import's condition.

    How Ansible runs a static import, made visible to the cases below, which keep the tasks they run by name.
    """
    tasks = []
    for task in block:
        imported = task.get("ansible.builtin.import_tasks")
        if imported not in EDITOR_FILES:
            tasks.append(task)
            continue
        for inner in yaml.safe_load((CODE.parent / imported).read_text()):
            own = inner.get("when")
            given = [task["when"]] if "when" in task else []
            inner["when"] = [*given, *(own if isinstance(own, list) else [own] if own else [])]
            tasks.append(inner)
    return tasks


def _script() -> str:
    return TERMINAL.read_text()


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
    argv = ["/bin/bash", str(TERMINAL), NOT_INSTALLED, *signed_in, str(watch), github_signed_in]
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


def _script_at_a_terminal(tmp_path: Path, keys: bytes, *, after: str, **case) -> tuple[int, str, list[str]]:
    """Run the script at a pseudo-terminal of its own, as VS Code does; type `keys` once the stand-ins logged `after`.

    The run leads a session of its own with that terminal as its controlling one (harness._at_a_pseudo_terminal), so
    a Ctrl-C reaches bash and everything it started, as in VS Code, with SIGINT not ignored whatever started the suite.
    """
    argv, env = _terminal(tmp_path, **case)
    typed = []

    def act(chunk, output, master, process):
        if not typed and after in _asked(tmp_path):
            os.write(master, keys)
            typed.append(keys)

    def explain(process, master):
        isig = "on" if termios.tcgetattr(master)[3] & termios.ISIG else "off"
        return f"the terminal's ISIG is {isig}; what is left of its session:\n" + _session(process.pid)

    rc, raw = _at_a_pseudo_terminal(argv, tmp_path, env, act=act, timeout=30, poll=0.1, explain=explain)
    return rc, raw.decode(errors="replace"), _asked(tmp_path)


def _planned(editor: str, **over) -> dict:
    """An editor's plan of a window, as the core makes it from inputs as code.yml gives them."""
    rows = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())["claude_editors"]
    return editors_registry.editor_open_plan(editor, rows, editor_inputs(**over))


def _workspace_planned(**over) -> dict:
    (written,) = [write for write in _planned("vscode", **over)["vm_writes"] if write["kind"] == "file"]
    return json.loads(written["content"])


def test_the_script_and_its_messages_are_not_templates():
    # Ansible templates play vars, so the script must reach the VM as written;
    # VS Code resolves ${...} in a task's arguments before bash sees them.
    script = _script()
    for marker in ("{{", "{%", "{#", "${"):
        assert marker not in script
    claude = _workspace_planned()["tasks"]["tasks"][-1]
    assert [arg for arg in claude["args"] if "${" in arg or "{{" in arg] == [], claude["args"]


SESSION_ID = "0a608ad8-1b2c-4d3e-8f90-123456789abc"
# The GitHub sign-in as the gh stand-in says it, through the script: the code on a line of its own.
GITHUB_FLOW = GITHUB_SIGNING_IN + "\n" + "\n    " + GITHUB_CODE + "\n\n"
SIGNED_OUT_OF_GITHUB = [
    "claude auth status",
    GITHUB_STATUS,
    GITHUB_LOGIN,
    GITHUB_PAGE,
    "gh auth setup-git -h github.com",
    "claude --continue",
]


class Holds(str):
    """A stdout compared by what it holds: the rest is the stand-in's to say."""


# Each case of the Claude terminal's script: the stand-ins' settings (_terminal), what was asked of claude and gh in
# order, and the script's stdout - exactly, or what it holds (Holds), or not compared (None). Every run exits 0: a
# non-zero exit makes VS Code print the whole command line, script and messages included, under the line that
# matters.
TERMINAL_CASES = [
    # Claude Code 2.1 exits 1 at once on --continue in a folder with no conversation of its own; VS Code then printed
    # the whole command line.
    pytest.param(
        {"status_rc": 0, "continue_rc": 1},
        ["claude auth status", GITHUB_STATUS, "claude --continue", "claude"],
        None,
        id="nothing-to-continue-starts-a-fresh-claude",
    ),
    # --session: the copy this machine's session became, never a fork, and never --continue.
    pytest.param(
        {"status_rc": 0, "session": SESSION_ID},
        ["claude auth status", GITHUB_STATUS, f"claude --resume {SESSION_ID}"],
        None,
        id="a-session-handed-over-is-resumed-under-its-own-id",
    ),
    # Claude Code's own reason is said; a fresh claude would look like the session.
    pytest.param(
        {"status_rc": 0, "session": SESSION_ID, "resume_rc": 1},
        ["claude auth status", GITHUB_STATUS, f"claude --resume {SESSION_ID}"],
        Holds(f"No conversation found with session ID: {SESSION_ID}"),
        id="a-resume-that-fails-starts-no-other-claude",
    ),
    # Only a --continue that fails at once means there was nothing to continue. Five seconds is the least that is not
    # at once: bash's SECONDS counts whole seconds of the clock, so it says five or more after a sleep of five.
    pytest.param(
        {"status_rc": 0, "continue_rc": 1, "continue_sleep": 5},
        ["claude auth status", GITHUB_STATUS, "claude --continue"],
        None,
        id="a-continued-session-that-ends-badly-is-not-started-again",
    ),
    pytest.param({"installed": False}, [], NOT_INSTALLED + "\n", id="without-claude-it-says-how-to-install-it"),
    # --continue: the folder's latest conversation, or a new one. A task terminal does not survive a window reload,
    # and the task then runs again.
    pytest.param(
        {"status_rc": 0},
        ["claude auth status", GITHUB_STATUS, "claude --continue"],
        "",
        id="a-signed-in-vm-picks-the-folder-s-conversation-up",
    ),
    pytest.param(
        {"status_rc": 1, "login_rc": 0, "watched": True},
        ["claude auth status", "claude auth login", GITHUB_STATUS, "claude --continue"],
        SIGNING_IN + "\n" + WATCHED + "\n",
        id="a-sign-in-the-path-unit-watches-says-remote-control-starts-by-itself",
    ),
    # A VM with a setting that blocks Remote Control, or last configured before the path unit existed: nothing
    # starts it by itself.
    pytest.param(
        {"status_rc": 1, "login_rc": 0, "watched": False},
        ["claude auth status", "claude auth login", GITHUB_STATUS, "claude --continue"],
        SIGNING_IN + "\n" + UNWATCHED + "\n",
        id="a-sign-in-nothing-watches-names-the-run-that-starts-remote-control",
    ),
    pytest.param(
        {"status_rc": 1, "login_rc": 1},
        ["claude auth status", "claude auth login"],
        SIGNING_IN + "\n" + NOT_SIGNED_IN + "\n",
        id="a-failed-sign-in-says-how-to-try-again-and-stops",
    ),
    *(
        pytest.param(
            {"status_rc": 1, "login_rc": 0, "watched": watched, "signed_in": ("", "")},
            ["claude auth status", "claude auth login", GITHUB_STATUS, "claude --continue"],
            SIGNING_IN + "\n",
            id=f"no-remote-control-hint-when-there-is-none-to-give-{watched}",
        )
        for watched in (True, False)
    ),
    # Claude first: the session is the point of the window. Then the device flow with no terminal of its own, the
    # page opened through BROWSER and the code printed on a line of its own, then setup-git, so a push from this
    # terminal works at once. gh's clipboard warning is not repeated into the terminal.
    pytest.param(
        {"status_rc": 0, "gh_status_rc": 1},
        SIGNED_OUT_OF_GITHUB,
        GITHUB_FLOW + "Logged in as octo\n" + GITHUB_SIGNED_IN + "\n",
        id="a-vm-signed-out-of-github-signs-in-there-and-makes-gh-git-s-helper",
    ),
    pytest.param(
        {"status_rc": 0, "gh_status_rc": 1, "gh_login_rc": 1},
        ["claude auth status", GITHUB_STATUS, GITHUB_LOGIN, GITHUB_PAGE, "claude --continue"],
        GITHUB_FLOW + GITHUB_EXPIRED + "\n" + GITHUB_FAILED + "\n",
        id="a-failed-github-sign-in-still-starts-claude",
    ),
    # Asked about every account, gh fails over one with a dead token, though the active one is signed in; the script
    # asks about that one alone.
    pytest.param(
        {"status_rc": 0, "gh_status_rc": 0, "gh_status_any_rc": 1},
        ["claude auth status", GITHUB_STATUS, "claude --continue"],
        "",
        id="a-stale-second-github-account-starts-no-device-flow",
    ),
    # Asked about every host, gh is content with another one's account.
    pytest.param(
        {"status_rc": 0, "gh_status_rc": 1, "gh_status_any_rc": 0},
        SIGNED_OUT_OF_GITHUB,
        GITHUB_FLOW + "Logged in as octo\n" + GITHUB_SIGNED_IN + "\n",
        id="gh-signed-in-only-to-another-host-is-signed-in-to-github-com",
    ),
    pytest.param(
        {"status_rc": 0, "gh_installed": False},
        ["claude auth status", "claude --continue"],
        "",
        id="without-gh-the-github-step-is-skipped",
    ),
    # An empty GitHub line is how code.yml says github_projects_auth is off.
    pytest.param(
        {"status_rc": 0, "gh_status_rc": 1, "github_signed_in": ""},
        ["claude auth status", "claude --continue"],
        "",
        id="github-auth-off-leaves-gh-alone",
    ),
]


@pytest.mark.parametrize(("case", "asked", "stdout"), TERMINAL_CASES)
def test_the_claude_terminal(tmp_path, case: dict, asked: list[str], stdout: str | None) -> None:
    result, ran = _run(tmp_path, **case)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ran == asked
    if isinstance(stdout, Holds):
        assert stdout in result.stdout, result.stdout
    elif stdout is not None:
        assert result.stdout == stdout


def test_ctrl_c_during_github_s_device_flow_skips_it_and_starts_claude(tmp_path):
    # VS Code's terminal sends Ctrl-C to bash and everything bash started.
    # Without the trap bash dies with gh, and Claude never starts; with it, gh
    # and the read loop end, and claude starts as after a sign-in that failed,
    # long before the code would have expired.
    rc, output, asked = _script_at_a_terminal(
        tmp_path, b"\x03", after=GITHUB_PAGE, status_rc=0, gh_status_rc=1, gh_login_rc="wait"
    )
    assert rc == 0, output
    assert asked == ["claude auth status", GITHUB_STATUS, GITHUB_LOGIN, GITHUB_PAGE, "claude --continue"]
    assert GITHUB_FAILED in output
    assert GITHUB_EXPIRED not in output


def test_the_task_runs_the_helper_and_passes_its_messages_as_arguments():
    # The helper by path, then the lines it says (test_the_terminal_is_handed_the_lines_for_this_vm), then the
    # session notes' helper, their path, the Remote Control unit and the time zone, for the refresh after a sign-in
    # in the terminal, and --session last: the script reads it as the last argument.
    claude = _workspace_planned(session_id=SESSION_ID)["tasks"]["tasks"][-1]
    assert claude["command"] == "/bin/bash"
    assert claude["args"][:2] == ["-l", f"{PLANNED_FD}/helpers/claude-terminal.sh"]
    assert claude["args"][10:] == [
        f"{PLANNED_FD}/helpers/session-notes.sh",
        f"{PLANNED_FD}/session/alpha-session.md",
        "claude-remote-control",
        "UTC",
        SESSION_ID,
    ]
    assert claude["runOptions"] == {"runOn": "folderOpen"}


# just code's switches as code.yml reads them, through the inventory defaults: each by its name from 1.1, or its
# name in 1.0 where the new one is not set; the new one wins where both are (and over -e of the old, which every
# window run is given for the sessions: vscode_claude_sessions false). Zed's notes do not need the terminal.
SWITCHES = "[code_terminal, code_notes, code_sessions]"
SWITCH_CASES = {
    "1.0's names": ({"vscode_claude_terminal": False, "vscode_session_notes": False}, [False, False, False]),
    "1.1's names": (
        {"editor_claude_terminal": True, "editor_session_notes": False, "claude_sessions_sync": True},
        [True, False, True],
    ),
    "both, the new one wins": (
        {"vscode_claude_terminal": True, "editor_claude_terminal": False, "editor_session_notes": True},
        [False, False, False],
    ),
    "zed, notes without the terminal": ({"editor": "zed", "editor_claude_terminal": False}, [False, True, False]),
}
# just code's own directory on the VM, by editor_files_dir, vscode_workspaces_dir where it is not set at all, else
# the default: an empty new name is the default, never the old name's.
FILES_CASES = {
    "1.1's name": ({"editor_files_dir": "/srv/new"}, "/srv/new"),
    "1.0's name": ({"vscode_workspaces_dir": "/srv/old"}, "/srv/old"),
    "both": ({"editor_files_dir": "/srv/new", "vscode_workspaces_dir": "/srv/old"}, "/srv/new"),
    "the new one empty": (
        {"editor_files_dir": "", "vscode_workspaces_dir": "/srv/old"},
        "/home/dev/projects/.agent-warehouse",
    ),
}
# The window step runs on a PATH of its own: stand-ins for `which` and `code`
# first, so no VS Code of this machine's can be started, then the system's
# directories, which Ansible's local connection needs for its own work.
WINDOW_PATH = "/usr/bin:/bin"
ONE_VM = ["Refuse anything but one VM"]
EDITOR = ["Refuse an editor just code cannot open"]
VS_CODE = [
    "vscode_check | Look for VS Code on this machine",
    "vscode_check | Fail when VS Code's command is not on PATH",
    "vscode_check | Keep what was found",
]
UP = ["Settle where the VM is reached", "Fail when the VM cannot be reached"]
EXTENSION_TASKS = [
    "vscode_check | List VS Code's extensions",
    "vscode_check | Install the Remote - SSH extension when VS Code lacks it",
]
# The editor's report of the window it opened.
OPENED_REPORT = "editor_open | Say what was opened"
ZED = [
    "zed_check | Look for Zed's commands on this machine",
    "zed_check | Ask each whether it is Zed",
    "zed_check | Keep what was found",
]
# The plan's refusals, every editor's: the task right after the editor's check.
PLAN_REFUSED = "Refuse what the editor's plan refuses"
# Zed's read on the VM, whether the tasks file there is just code's: run only where the "VM" is a directory of the
# test's own (ZED_WRITES).
ZED_READ = "zed_check | Read Zed's tasks already on the VM"
# The window, without its writes on the VM, whose paths these cases give as a VM's: what is said, and run.
OPENED_WINDOW = [
    "editor_open | Open the window",
    OPENED_REPORT,
    "editor_open | Keep what is left to do for the end of the run",
]
WINDOW_WRITES = [
    "editor_open | Look for just code's directories on the VM",
    "editor_open | Make just code's directories on the VM",
    "editor_open | Write the helpers the plan names on the VM",
    "editor_open | Write just code's files on the VM",
]
CHECKOUT = [
    "Look for a checkout around just code's own directory on the VM",
    "Refuse just code's own directory inside a checkout",
]
OWN_DIR = ["Look for just code's own directory on the VM", "Make just code's own directory on the VM"]
# The move of just code's directory from its 1.x name, which only a case that gives that name runs.
MOVE_1X = [
    "Look for just code's directory by its 1.x name on the VM",
    "Move just code's directory to its name from 2.0 on the VM",
    "Make the shared helper's 1.x directory on the VM",
    "Keep the shared helper's 1.x path as a link to its new one on the VM",
]
NOTES_WRITTEN = [
    "Read the session notes already on the VM",
    "Make the directory for the session notes on the VM",
    "Write the session notes on the VM",
]
# The editors' own tasks in the window step, grouped by what each group does. The cases name groups, never an editor's
# task, and a case runs its groups' tasks in code.yml's order: so the files that hold an editor's tasks can be reshaped
# beneath the cases - this table following them - while every case's outputs hold: what it wrote on the VM, ran here,
# said, and left for NEXT STEPS.
FIND, EXTENSION, REFUSE, VM_READS, WRITES, OPEN, SAID = (
    f"<the editor's {group}>" for group in ("find", "extension", "refuse", "vm reads", "writes", "open", "said")
)
EDITOR_TASKS = {
    "vscode": {
        FIND: VS_CODE,
        EXTENSION: EXTENSION_TASKS,
        REFUSE: [PLAN_REFUSED],
        VM_READS: [],
        WRITES: WINDOW_WRITES,
        OPEN: OPENED_WINDOW,
        SAID: [OPENED_REPORT],
    },
    "zed": {
        FIND: ZED,
        REFUSE: [PLAN_REFUSED],
        VM_READS: [ZED_READ],
        WRITES: WINDOW_WRITES,
        OPEN: OPENED_WINDOW,
        SAID: [OPENED_REPORT],
    },
}
# The workspace file VS Code's window is handed, as the window step has it before anything is written: its plan's.
WORKSPACE = "code_plan.vm_writes | selectattr('kind', 'eq', 'file') | map(attribute='content') | first | from_json"
ZED_WRITTEN = [*CHECKOUT, FIND, REFUSE, VM_READS, *OWN_DIR, *NOTES_WRITTEN, WRITES, OPEN]
VSCODE_WRITTEN = [*CHECKOUT, *OWN_DIR, WRITES]
STOPPED = [{"name": "beta", "vmid": 9004, "node": "pve", "status": "stopped", "address": ""}]
ADDRESSED = [{"name": "alpha", "vmid": 9001, "node": "pve", "status": "running", "address": "192.0.2.51"}]
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
    "code_ws_dir": "/home/dev/projects/.agent-warehouse",
    "code_project": "",
    "code_reach": "192.0.2.51",
    "code_notes_dir": "/home/dev/projects/.agent-warehouse/session",
    "code_notes_file": "/home/dev/projects/.agent-warehouse/session/alpha-session.md",
    "claude_vm_addressed": ADDRESSED,
}
# The Claude task's arguments after the helper, the session notes' four and --session aside: the workspace's last task.
ARGS = f"({WORKSPACE}).tasks.tasks[-1].args[2:10]"
NOTES = {**OPENED, "show": WORKSPACE, "code_here": True}
SESSION = "/home/dev/projects/.agent-warehouse/session"
LATER = " VS Code was not opened; to open it later: just code alpha"
# The check of just code's own directory: absolute, not the projects directory, not the folder opened.
WS_CHECK = "Refuse a directory for just code's files that is not absolute, is the projects directory, or is the folder"
# Zed with the terminal and the sessions off, so the notes are all that goes into just code's own directory.
NOTES_ONLY = {
    "keep": [WS_CHECK],
    "in_deploy": False,
    "vm_name": "alpha",
    "vm_editor": "zed",
    "local": {"editor_claude_terminal": False},
    **OPENED,
}


def _tagged(name, vmid):
    return {"type": "qemu", "vmid": vmid, "name": name, "node": "pve", "status": "running", "tags": "agent-warehouse"}


def _listing(*names):
    return {"json": {"data": [_tagged(name, 100 + i) for i, name in enumerate(names)]}}


def _the_window(play: dict) -> dict:
    return next(task for task in play["tasks"] if task.get("name") == "Open the VM in the editor")


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


def _opened(output: str):
    """What the editor's window step said it opened, by what the task is: its report, whichever file holds it."""
    for part in output.split("TASK [")[1:]:
        name = part.split("]", 1)[0]
        if name.endswith("| Say what was opened"):
            return _said(output, name)
    return None


def _recorded(output: str) -> dict:
    """What a run recorded for NEXT STEPS (callback_plugins/agent_warehouse.py), from its custom stats."""
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


def _window(
    root: Path,
    keep: list[str],
    *,
    in_deploy: bool,
    code_here=False,
    zed_here=None,
    local=None,
    show=None,
    vscode_settings=None,
    **extra,
) -> dict:
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
    # `zed_here` names the commands on PATH as Zed's (zed, zeditor) or ZFS's event daemon's (zfs), which shares
    # the name zed: Zed's says it is Zed when asked its version and records anything else it is asked; ZFS's records
    # everything it is asked and knows no --version. which answers as which does: each name it finds, in the order
    # asked, -a for every one, and a failure when any is missing.
    zed_here = zed_here or {}
    stand_ins = {
        "zed": 'if [ "$1" = --version ]; then echo "Zed 0.230.1 – /Applications/Zed.app"; exit 0; fi\n'
        'echo "$(basename "$0") $*" >> "{log}"\n',
        "zfs": 'echo "zfs-zed $*" >> "{log}"\necho "zed: unrecognized option \'$1\'" >&2\nexit 1\n',
    }
    for name, kind in zed_here.items():
        (bin_dir / name).write_text("#!/bin/sh\n" + stand_ins[kind].format(log=root / "code.log"))
        (bin_dir / name).chmod(0o755)
    found = (["code"] if code_here else []) + list(zed_here)
    (bin_dir / "which").write_text(
        '#!/bin/sh\n[ "$1" = -a ] && shift\nmissing=0\nfor name; do\n  case "$name" in\n'
        + "".join(f'    {name}) echo "{bin_dir}/{name}" ;;\n' for name in found)
        + "    *) missing=1 ;;\n  esac\ndone\nexit $missing\n"
    )
    for name in ("code", "which"):
        (bin_dir / name).chmod(0o755)

    play = copy.deepcopy(_play())
    if in_deploy:
        play["vars"].update(_deploy_vars())
    window = _the_window(play)
    # keep names code.yml's own tasks, and groups of the editor's (EDITOR_TASKS); all of them run in code.yml's order.
    editor = extra.get("vm_editor") or (local or {}).get("editor") or "vscode"
    names = {task for name in keep for task in EDITOR_TASKS.get(editor, {}).get(name, [name])}
    window["block"] = [task for task in _inlined(window["block"]) if task["name"] in names]
    assert {task["name"] for task in window["block"]} == names, "keep names code.yml's tasks"
    if show:
        window["block"].append({"name": "Show it", "ansible.builtin.debug": {"msg": "{{ " + show + " }}"}})
    window["block"].append({"name": "Reach the window", "ansible.builtin.debug": {"msg": "reached"}})
    (root / "window.yml").write_text(yaml.safe_dump([play]))
    # What code.yml finds beside itself: the task files it imports, its modules and what they share, its templates,
    # the vars files it names and the helpers it copies.
    for name in ("tasks", "library", "module_utils", "templates", "vars", "files"):
        (root / name).symlink_to(REPO / "playbooks" / name)

    # The harness's environment, its PATH the stand-ins' alone.
    env = _env(
        root,
        # The run's stats printed: what it recorded for NEXT STEPS.
        ANSIBLE_SHOW_CUSTOM_STATS="1",
        PATH=f"{bin_dir}:{WINDOW_PATH}",
        # Never the developer's git config: a url.<base>.insteadOf there rewrites the origins read.
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        # A log of the run's own, which takes what Ansible writes to the log alone.
        ANSIBLE_LOG_PATH=str(root / "run.log"),
    )
    extra = {**_window_extra(root), **extra}
    if vscode_settings is not None:
        # The user's own VS Code settings, as VS Code keeps them: a file of the run's own, read and never written.
        (root / "settings.json").write_text(vscode_settings)
        extra["vscode_user_settings_file"] = str(root / "settings.json")
    result = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", str(root / "inventory"), "-e", json.dumps(extra), str(root / "window.yml")],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        process_group=0,
    )
    _fail_if_the_desktop_was_reached(root)
    output = result.stdout + result.stderr
    asked = root / "code.log"
    return {
        "rc": result.returncode,
        "output": output,
        "log": (root / "run.log").read_text() if (root / "run.log").exists() else "",
        "reached": _said(output, "Reach the window") == "reached",
        "later": _said(output, "Say how to open the VM later"),
        "fleet": _said(output, "Say how to open each VM"),
        "shown": _said(output, "Show it"),
        "opened": _opened(output),
        "left": _recorded(output),
        "code": asked.read_text().splitlines() if asked.exists() else [],
    }


WINDOWS = {
    "deploy, code here": {
        "keep": [*ONE_VM, FIND, REFUSE],
        "in_deploy": True,
        "code_here": True,
        "vm_name": "alpha",
        **OPENED,
    },
    "deploy, no code": {"keep": [*ONE_VM, FIND, REFUSE], "in_deploy": True, "vm_name": "alpha", **OPENED},
    "just code, code here": {
        "keep": [*ONE_VM, FIND, REFUSE],
        "in_deploy": False,
        "code_here": True,
        "vm_name": "alpha",
        **OPENED,
    },
    "just code, no code": {"keep": [*ONE_VM, FIND, REFUSE], "in_deploy": False, "vm_name": "alpha", **OPENED},
    "deploy, no name": {"keep": ONE_VM, "in_deploy": True},
    # An editor the setting or --editor names that code.yml cannot open, said first; the setting itself, by name.
    "just code, another editor": {"keep": EDITOR, "in_deploy": False, "vm_name": "alpha", "vm_editor": "emacs"},
    "deploy, editor setting": {"keep": EDITOR, "in_deploy": True, "vm_name": "alpha", "local": {"editor": "nano"}},
    "just code, zed here": {
        "keep": [*ONE_VM, FIND, REFUSE],
        **OPENED,
        "in_deploy": False,
        "zed_here": {"zed": "zed"},
        "vm_name": "alpha",
        "vm_editor": "zed",
    },
    "deploy, no zed": {
        "keep": [*ONE_VM, FIND, REFUSE],
        **OPENED,
        "in_deploy": True,
        "vm_name": "alpha",
        "local": {"editor": "zed"},
    },
    "deploy, ZFS's zed alone": {
        "keep": [*ONE_VM, FIND, REFUSE],
        **OPENED,
        "in_deploy": True,
        "zed_here": {"zed": "zfs"},
        "vm_name": "alpha",
        "local": {"editor": "zed"},
    },
    "zed, a folder its URL cannot carry": {
        "keep": [FIND, REFUSE],
        **OPENED,
        "in_deploy": False,
        "zed_here": {"zed": "zed"},
        "vm_name": "alpha",
        "vm_editor": "zed",
        "code_root": "/home/dev/my projects",
        "code_target": "/home/dev/my projects",
    },
    "zed, opened": {
        "keep": [FIND, REFUSE, OPEN],
        "in_deploy": False,
        "zed_here": {"zed": "zed"},
        "vm_name": "alpha",
        "vm_editor": "zed",
        **OPENED,
    },
    # With editor_claude_terminal false no window has a Claude task; one project's window, which does not hold just
    # code's directory, lists none either: ZED_WRITES["one project"], its window with its writes.
    "zed, no Claude task": {
        "keep": [FIND, REFUSE, OPEN],
        "in_deploy": False,
        "zed_here": {"zed": "zed"},
        "vm_name": "alpha",
        "vm_editor": "zed",
        "local": {"editor_claude_terminal": False},
        **OPENED,
    },
    "zed, zeditor beside ZFS's zed": {
        "keep": [FIND, REFUSE, OPEN],
        "in_deploy": False,
        "zed_here": {"zeditor": "zed", "zed": "zfs"},
        "vm_name": "alpha",
        "vm_editor": "zed",
        **OPENED,
    },
    "just code, the setting's editor": {
        "keep": EDITOR,
        "in_deploy": False,
        "vm_name": "alpha",
        "local": {"editor": "vscode"},
    },
    "just code, no name": {"keep": ONE_VM, "in_deploy": False},
    "deploy, fleet": {"keep": ONE_VM, "in_deploy": True, "vm_name": "alpha,beta"},
    "just code, fleet": {"keep": ONE_VM, "in_deploy": False, "vm_name": "alpha,beta"},
    "deploy, fleet, --editor zed": {"keep": ONE_VM, "in_deploy": True, "vm_name": "alpha,beta", "vm_editor": "zed"},
    "just code, fleet, --editor zed": {"keep": ONE_VM, "in_deploy": False, "vm_name": "alpha,beta", "vm_editor": "zed"},
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
    "a home with a control character": {
        "keep": ["Refuse a home on the VM that is not one plain path"],
        "in_deploy": False,
        "vm_name": "alpha",
        "code_home": {"ansible_facts": {"getent_passwd": {"dev": ["x", "1000", "1000", "", "/home/de\x1b]0;x\x07v"]}}},
    },
    "deploy, no extension": {"keep": [EXTENSION], "in_deploy": True, "code_here": True, "vm_name": "alpha"},
    "just code, no extension": {"keep": [EXTENSION], "in_deploy": False, "code_here": True, "vm_name": "alpha"},
    "deploy, no name, beside others": {
        "keep": ["Find the VM"],
        "in_deploy": True,
        "claude_vm_all": _listing("agent-warehouse-default", "alpha"),
    },
    "deploy, named": {
        "keep": ["Find the VM"],
        "in_deploy": True,
        "vm_name": "alpha",
        "claude_vm_all": _listing("agent-warehouse-default", "alpha"),
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
    "messages": {
        "keep": [FIND, SAID],
        "code_here": True,
        "in_deploy": False,
        "vm_name": "alpha",
        "show": ARGS,
        **OPENED,
    },
    "messages, role-level switches": {
        "keep": [FIND, SAID],
        "code_here": True,
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
        "keep": [FIND, SAID],
        "code_here": True,
        "in_deploy": False,
        "vm_name": "alpha",
        "show": ARGS,
        "local": {
            "github_auth": False,
            "claude_code_remote_control": False,
            "github_login_scopes": ["workflow", "read:packages"],
        },
        **OPENED,
    },
    "messages, token method": {
        "keep": [FIND, SAID],
        "code_here": True,
        "in_deploy": False,
        "vm_name": "alpha",
        "show": ARGS,
        "local": {"github_projects_auth_method": "token"},
        **OPENED,
    },
    # The session notes, by window: the workspace file as code.yml writes it, and what is said.
    "notes, whole directory": {"keep": [FIND, SAID], "in_deploy": False, "vm_name": "alpha", **NOTES},
    "notes, one project": {
        "keep": [FIND, SAID],
        "in_deploy": False,
        "vm_name": "alpha",
        **NOTES,
        "code_target": "/home/dev/projects/discord-music-bot",
        "code_project": "discord-music-bot",
    },
    "notes, workspaces elsewhere": {
        "keep": [FIND, SAID],
        "in_deploy": False,
        "vm_name": "alpha",
        **NOTES,
        "code_ws_dir": "/home/dev/.vscode-workspaces",
        "code_notes_dir": "/home/dev/.vscode-workspaces/session",
        "code_notes_file": "/home/dev/.vscode-workspaces/session/alpha-session.md",
    },
    "notes, automatic tasks off": {
        "keep": [FIND, SAID],
        "in_deploy": False,
        "vm_name": "alpha",
        **NOTES,
        "vscode_settings": '{"task.allowAutomaticTasks": "off"}\n',
    },
    "notes off": {
        "keep": [FIND, SAID],
        "in_deploy": False,
        "vm_name": "alpha",
        "local": {"vscode_session_notes": False},
        **NOTES,
    },
    "notes off, by 1.1's name": {
        "keep": [FIND, SAID],
        "in_deploy": False,
        "vm_name": "alpha",
        "local": {"editor_session_notes": False},
        **NOTES,
    },
    "notes only, the projects directory": {**NOTES_ONLY, "code_ws_dir": "/home/dev/projects"},
    "notes only, not absolute": {**NOTES_ONLY, "code_ws_dir": "claude-files"},
    "notes only, the VM's root": {**NOTES_ONLY, "code_ws_dir": "/"},
    "nothing written, not absolute": {
        **NOTES_ONLY,
        "local": {"editor_claude_terminal": False, "editor_session_notes": False},
        "code_ws_dir": "claude-files",
    },
    # The same through the editor's plan and the window: nothing goes into just code's own directory, so the plan
    # holds it to nothing either, whatever it names - the VM's root here.
    "nothing written, the VM's root, through the plan": {
        "keep": [FIND, REFUSE, OPEN],
        "in_deploy": False,
        "zed_here": {"zed": "zed"},
        "vm_name": "alpha",
        "vm_editor": "zed",
        "local": {"editor_claude_terminal": False, "editor_session_notes": False},
        **OPENED,
        "code_ws_dir": "/",
    },
    **{
        f"switches, {case}": {"keep": [], "in_deploy": False, "vm_name": "alpha", "show": SWITCHES, "local": local}
        for case, (local, _) in SWITCH_CASES.items()
    },
    **{
        f"files, {case}": {
            "keep": ["Settle the folder to open"],
            "in_deploy": False,
            "vm_name": "alpha",
            "vm_projects_dir": "/home/dev/projects",
            "show": "code_ws_dir",
            "local": local,
        }
        for case, (local, _) in FILES_CASES.items()
    },
}


@pytest.fixture(scope="module")
def windows(tmp_path_factory):
    """Every run in WINDOWS, side by side: each is a playbook run of its own, about a second long."""
    root = tmp_path_factory.mktemp("windows")
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = {name: pool.submit(_window, root / re.sub(r"\W+", "-", name), **case) for name, case in WINDOWS.items()}
    return {name: run.result() for name, run in runs.items()}


@pytest.mark.parametrize(
    "name",
    [
        # A deploy for one VM goes on to the window, named or not: without vm_name, a deploy is for the default VM,
        # one name all the same.
        "deploy, code here",
        "deploy, no name",
        # just code goes on to the window where VS Code's command is.
        "just code, code here",
        # Without vm_name, vm_name is not defined, and claude_vm_requested is the whole tagged fleet; the window step
        # opens the one VM deploy was for.
        "deploy, no name, beside others",
        # A named deploy opens that VM.
        "deploy, named",
    ],
)
def test_the_window_is_reached(windows, name):
    run = windows[name]
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
    assert run["left"] == {"alpha": {"editor": run["later"]}}


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
    assert run["left"] == {"alpha, beta": {"editor": "\n".join(run["fleet"])}}


def test_a_fleet_s_lines_and_the_one_vm_refusal_name_the_editor_a_run_chose_against_the_setting(windows):
    # The setting is VS Code's (unset) and the run's editor Zed: a bare just code alpha would open VS Code.
    run = windows["deploy, fleet, --editor zed"]
    assert run["rc"] == 0 and not run["reached"], run["output"]
    assert run["fleet"] == [
        "Zed opens one VM at a time, so this deploy for 2 VMs opened none. To open one:",
        "just code alpha --editor zed",
        "just code beta --editor zed",
    ]
    run = windows["just code, fleet, --editor zed"]
    assert run["rc"] != 0 and "Zed opens one VM at a time: just code <name> --editor zed." in run["output"]


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
        ("deploy, twice", 'More than one VM tagged "agent-warehouse" is named alpha'),
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


def test_a_home_the_vm_reports_with_a_control_character_is_refused(windows):
    # The home begins every path just code says or hands the editor: refused before any of them is said.
    run = windows["a home with a control character"]
    assert run["rc"] != 0 and not run["reached"], run["output"]
    assert any("holds a control character" in failure for failure in _failures(run["output"])), run["output"]


def test_just_code_fails_where_just_deploy_goes_on_and_names_the_command_that_failed(windows):
    run = windows["just code, no extension"]
    assert run["rc"] != 0 and not run["reached"]
    assert "`code --install-extension ms-vscode-remote.remote-ssh` failed: Error while installing" in run["output"]
    assert run["code"] == ["code --list-extensions", "code --install-extension ms-vscode-remote.remote-ssh"]


def test_an_editor_just_code_cannot_open_is_refused_first_and_said_by_name(windows):
    # --editor beats the setting for one run, and either way an editor code.yml has no files for is said, never opened.
    other, setting = windows["just code, another editor"], windows["deploy, editor setting"]
    assert other["rc"] != 0 and not other["reached"]
    said = '--editor is "emacs", which just code cannot open: it opens vscode or zed.'
    assert said in other["output"], other["output"]
    assert not setting["reached"] and setting["later"].startswith(
        'The editor setting is "nano" (just settings editor says where it is set), which just code cannot open'
    ), setting
    assert windows["just code, the setting's editor"]["reached"]
    assert other["code"] == [] == setting["code"]


def test_zed_is_looked_for_as_zed_or_zeditor_and_named_when_neither_is_there(windows):
    here, missing = windows["just code, zed here"], windows["deploy, no zed"]
    assert here["reached"], here["output"]
    assert not missing["reached"]
    assert missing["later"].startswith("Neither `zed` nor `zeditor` is on this machine's PATH."), missing["later"]
    assert missing["later"].endswith(" Zed was not opened; to open it later: just code alpha"), missing["later"]
    assert here["code"] == [] == missing["code"]


def test_zed_is_never_handed_a_folder_its_url_cannot_carry_as_it_is(windows):
    run = windows["zed, a folder its URL cannot carry"]
    assert not run["reached"] and run["code"] == []
    assert (
        "Zed is handed the folder in an ssh:// URL, which cannot carry /home/dev/my projects as it is" in run["output"]
    )


def test_zfs_s_event_daemon_is_asked_its_version_and_never_handed_the_vm(windows):
    # ZFS's zed shares Zed's name (Ubuntu's /usr/sbin/zed, Arch's /usr/bin/zed): only a command that says it is Zed
    # runs, zeditor before zed; ZFS's is asked nothing but --version.
    alone, beside = windows["deploy, ZFS's zed alone"], windows["zed, zeditor beside ZFS's zed"]
    assert not alone["reached"]
    assert alone["later"].startswith("No `zed` or `zeditor` on this machine's PATH is Zed: "), alone["later"]
    assert alone["code"] == ["zfs-zed --version"]
    assert beside["reached"], beside["output"]
    assert beside["code"] == ["zfs-zed --version", "zeditor ssh://alpha/home/dev/projects"]


def test_zed_opens_the_folder_through_the_alias_and_claude_is_a_task_of_it(windows):
    run = windows["zed, opened"]
    assert run["reached"], run["output"]
    # Over the alias, which Zed reads from ~/.ssh/config, at the folder's absolute path, and nothing else: Zed
    # refuses a second ssh:// path, so the notes are not handed over.
    assert run["code"] == ["zed ssh://alpha/home/dev/projects"]
    said = _opened(run["output"])
    assert said.startswith("Zed is opening /home/dev/projects on alpha (192.0.2.51)."), said
    # The window holds just code's directory, and so the task Zed lists from it, which the user starts: by its
    # label, a new terminal each time, and the guide's own routes by name only - no key of the project's.
    assert 'Claude is a task there: "task: spawn" in the command palette lists Claude (just code), which runs' in said
    assert "so a second one never ends or shares the first;" in said
    assert "docs/guides/zed.md: one key, or Claude with no key." in said
    assert "The session notes about the VM are at /home/dev/projects/.agent-warehouse/session/alpha-session.md." in said
    # The panel's agent runs on the VM too (seen on Zed 1.26.2), and Zed asks nothing about trust; trusting the
    # projects window is said by what it trusts, and a Zed that holds the task back is said by behaviour.
    assert "Claude Agent in Zed's Agent Panel runs on the VM as well" in said
    assert "Zed asks nothing about trust: Restricted Mode shows at the top left of the window" in said
    assert "trusting the projects window trusts every checkout in it." in said
    assert "If task: spawn lists no Claude (just code) and Restricted Mode shows, docs/guides/zed.md" in said
    assert "workspace" not in said.lower() and "1.2" not in said
    assert run["left"] == {"alpha": {"editor": ""}}


@pytest.mark.parametrize(
    ("name", "refused"),
    [
        (
            "notes only, the projects directory",
            "editor_files_dir is the projects directory, /home/dev/projects, whose folders are the projects; name a"
            " directory of its own.",
        ),
        (
            "notes only, not absolute",
            'editor_files_dir is a path on the VM, and must be an absolute one, not "claude-files": Zed is handed it'
            " as written.",
        ),
        (
            "notes only, the VM's root",
            "editor_files_dir is /, the whole of the VM: name a directory of its own for the files just code keeps.",
        ),
    ],
    ids=["the projects directory", "not absolute", "the VM's root"],
)
def test_just_code_s_own_directory_is_checked_wherever_anything_goes_into_it_the_notes_alone_too(
    windows, name, refused
):
    # An editor whose window runs no tasks gets the notes without the terminal: with it and the sessions off, they
    # are still written into editor_files_dir, which is held to the same rules.
    run = windows[name]
    assert run["rc"] != 0 and not run["reached"], run["output"]
    assert any(failure.startswith(refused) for failure in _failures(run["output"])), _failures(run["output"])


def test_just_code_s_own_directory_is_not_checked_where_nothing_goes_into_it(windows):
    run = windows["nothing written, not absolute"]
    assert run["reached"] and run["rc"] == 0, run["output"]
    run = windows["nothing written, the VM's root, through the plan"]
    assert run["rc"] == 0 and run["code"] == ["zed ssh://alpha/home/dev/projects"], run["output"]


def _claude_said(said: str) -> str:
    """What Zed's report says of Claude in the window: before what it says of the Agent Panel and of trust."""
    return said.split("Claude Agent in Zed's Agent Panel")[0]


def test_zed_says_to_run_claude_in_its_terminal_where_the_window_lists_no_claude_task(windows):
    # One project's window, which lists none and says the projects window does:
    # test_a_one_project_window_has_the_task_written_and_says_the_projects_window_lists_it.
    said = _opened(windows["zed, no Claude task"]["output"])
    assert "run claude: the terminal is the VM's" in said
    assert "task" not in _claude_said(said), said


# Zed's Claude task: one file in just code's own directory, of fixed text but for the shared helper's path, an
# argument; no tag (a trigger anyone could plant), and a new terminal for every spawn.
ZED_TASK = {
    "label": "Claude (just code)",
    "command": "bash",
    "args": ["{{ code_start_helper }}"],
    "allow_concurrent_runs": True,
    "use_new_terminal": True,
    "show_command": False,
}
# The core's: test_persisted_names.py holds its text, which a tasks file already on a VM carries.
MARKER_LINE = editors_common.MARKER


def test_zed_s_claude_task_is_fixed_text_in_just_code_s_own_directory():
    plan = _planned("zed")
    (written,) = [write for write in plan["vm_writes"] if write["kind"] == "file"]
    assert (written["path"], written["mode"]) == (".zed/tasks.json", "0644")
    # just code's marker first, which Zed reads as a comment, then the list as data, as to_nice_json writes it.
    head, body = written["content"].split("\n", 1)
    assert head == MARKER_LINE
    assert json.loads(body) == [ZED_TASK | {"args": [f"{PLANNED_FD}/helpers/claude-start.sh"]}]
    assert body == json.dumps(json.loads(body), indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    # No cwd, so the window's folder; no tag; nothing Zed would substitute, its variables all starting ZED_.
    assert "ZED_" not in json.dumps(ZED_TASK)
    # The helper's path reaches Zed only held to a pattern of its own, never the folder's, which takes more: the
    # one code.yml decides the task's writing by, and the core's.
    assert _play()["vars"]["code_helper_path_pattern"] == "^/[A-Za-z0-9._/-]+$"
    assert _play()["vars"]["code_helper_path_pattern"] == f"^{editors_common.HELPER_PATH.pattern}$"


# The Zed window with its writes, on a "VM" that is a directory of the test's own, reached locally: what it writes
# there, what it leaves, and what it says. Each case's function makes its VM and runs the window on it.
FOREIGN_TASKS = '[{"label": "build", "command": "make"}]\n'


def _vm_vars(vm: Path, *, files_dir=None, project="", moves_1x=False) -> dict:
    """A window run's extra vars for the VM at vm: its projects directory, and just code's in it unless files_dir;
    with moves_1x, just code's directory by its 1.x name too, as code.yml's settling names it for the default."""
    projects = vm / "projects"
    (projects / project).mkdir(parents=True, exist_ok=True)
    ws = files_dir or projects / ".agent-warehouse"
    return ({"code_ws_dir_1x": str(projects / ".claude-on-proxmox")} if moves_1x else {}) | {
        "ansible_connection": "local",
        "code_root": str(projects),
        "code_target": str(projects / project) if project else str(projects),
        "code_project": project,
        "code_ws_dir": str(ws),
        "code_notes_dir": f"{ws}/session",
        "code_notes_file": f"{ws}/session/alpha-session.md",
        "code_reach": "192.0.2.51",
    }


def _zed_written(root: Path, run: str, vm: Path, keep=ZED_WRITTEN, **given) -> dict:
    case = {
        "in_deploy": False,
        "vm_name": "alpha",
        "vm_editor": "zed",
        "zed_here": {"zed": "zed"},
        "claude_vm_addressed": ADDRESSED,
    }
    return {**_window(root / run, keep, **case, **_vm_vars(vm, **given)), "vm": vm}


def _zed_twice(root: Path) -> dict:
    vm = root / "vm"
    return {"first": _zed_written(root, "first", vm), "second": _zed_written(root, "second", vm)}


def _zed_foreign(root: Path) -> dict:
    vm = root / "vm"
    (vm / "projects" / ".agent-warehouse" / ".zed").mkdir(parents=True)
    (vm / "projects" / ".agent-warehouse" / ".zed" / "tasks.json").write_text(FOREIGN_TASKS)
    return _zed_written(root, "run", vm)


def _zed_earlier(root: Path) -> dict:
    vm = root / "vm"
    (vm / "projects" / ".agent-warehouse" / ".zed").mkdir(parents=True)
    (vm / "projects" / ".agent-warehouse" / ".zed" / "tasks.json").write_text(MARKER_LINE + "\n[]\n")
    return _zed_written(root, "run", vm)


def _zed_on_a_1x_vm(root: Path) -> dict:
    # just code's directory as 1.x left it: its 1.x name, a tasks file with 1.x's marker, the session copies' manifest.
    vm = root / "vm"
    old = vm / "projects" / ".claude-on-proxmox"
    (old / ".zed").mkdir(parents=True)
    (old / ".zed" / "tasks.json").write_text(editors_common.MARKER_1X + "\n[]\n")
    (old / "claude-sessions").mkdir()
    (old / "claude-sessions" / "manifest.json").write_text("{}\n")
    keep = [*ZED_WRITTEN, *MOVE_1X]
    return {
        "first": _zed_written(root, "first", vm, keep, moves_1x=True),
        "second": _zed_written(root, "second", vm, keep, moves_1x=True),
    }


def _in_a_checkout(root: Path, editor: str) -> dict:
    vm = root / "vm"
    (vm / "projects" / "repo" / ".git").mkdir(parents=True)
    files_dir = vm / "projects" / "repo" / ".agent-warehouse"
    if editor == "zed":
        return _zed_written(root, "run", vm, files_dir=files_dir)
    case = {"in_deploy": False, "vm_name": "alpha"}
    return {**_window(root / "run", VSCODE_WRITTEN, **case, **_vm_vars(vm, files_dir=files_dir)), "vm": vm}


def _home_in_git(root: Path) -> dict:
    vm = root / "vm"
    (vm / ".git").mkdir(parents=True)
    return _zed_written(root, "run", vm)


ZED_WRITES = {
    "twice": _zed_twice,
    "one project": lambda root: _zed_written(root, "run", root / "vm", project="repo"),
    "outside the projects directory": lambda root: _zed_written(
        root, "run", root / "vm", files_dir=root / "vm" / "elsewhere"
    ),
    "a helper path a task cannot be handed": lambda root: _zed_written(
        root, "run", root / "vm", files_dir=root / "vm" / "projects" / "odd files"
    ),
    "someone else's tasks file": _zed_foreign,
    "just code's earlier tasks file": _zed_earlier,
    "a VM as 1.x left it": _zed_on_a_1x_vm,
    "zed, in a checkout": lambda root: _in_a_checkout(root, "zed"),
    "vscode, in a checkout": lambda root: _in_a_checkout(root, "vscode"),
    "a home kept in git": _home_in_git,
}


@pytest.fixture(scope="module")
def zed_writes(tmp_path_factory):
    """Every case of ZED_WRITES, side by side, each in a directory of its own."""
    root = tmp_path_factory.mktemp("zed_writes").resolve()
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = {name: pool.submit(run, root / re.sub(r"\W+", "-", name)) for name, run in ZED_WRITES.items()}
    return {name: run.result() for name, run in runs.items()}


def _ws(run: dict) -> Path:
    return run["vm"] / "projects" / ".agent-warehouse"


def test_zed_s_window_writes_the_marked_task_file_and_the_shared_helper_and_a_second_run_rewrites_nothing(
    zed_writes,
):
    first, second = zed_writes["twice"]["first"], zed_writes["twice"]["second"]
    assert first["reached"] and first["rc"] == 0, first["output"]
    tasks = _ws(first) / ".zed" / "tasks.json"
    marker, listed = tasks.read_text().split("\n", 1)
    helper = _ws(first) / "helpers" / "claude-start.sh"
    assert marker == MARKER_LINE
    assert json.loads(listed) == [{**ZED_TASK, "args": [str(helper)]}]
    assert stat.S_IMODE(tasks.stat().st_mode) == 0o644
    # The shared helper, as it is in playbooks/files/, beside nothing of VS Code's.
    assert helper.read_bytes() == START_HELPER.read_bytes() and stat.S_IMODE(helper.stat().st_mode) == 0o644
    assert sorted(path.name for path in helper.parent.iterdir()) == ["claude-start.sh"]
    assert not list(_ws(first).glob("**/*.code-workspace"))
    said = _opened(first["output"])
    assert "lists Claude (just code), which runs claude in a terminal of its own" in said, said
    # Opening the window again changes nothing on the VM: the window's command is the one change.
    assert second["reached"] and _changed(second) == 1, second["output"]


def test_a_vm_as_1x_left_it_has_just_code_s_directory_moved_once_and_the_shared_helper_s_1x_path_kept(zed_writes):
    # Everything in it moves with it, the tasks file 1.x marked is just code's and written again, and the 1.x path of
    # the shared helper, which a task of the user's own may run, is a link to the new one: the only thing left there.
    first, second = zed_writes["a VM as 1.x left it"]["first"], zed_writes["a VM as 1.x left it"]["second"]
    assert first["reached"] and first["rc"] == 0, first["output"]
    old, new = first["vm"] / "projects" / ".claude-on-proxmox", _ws(first)
    assert (new / "claude-sessions" / "manifest.json").read_text() == "{}\n"
    assert (new / ".zed" / "tasks.json").read_text().split("\n", 1)[0] == MARKER_LINE
    link = old / "helpers" / "claude-start.sh"
    assert link.is_symlink() and link.resolve() == (new / "helpers" / "claude-start.sh").resolve()
    assert [path.name for path in old.iterdir()] == ["helpers"]
    assert [path.name for path in (old / "helpers").iterdir()] == ["claude-start.sh"]
    # And the next run moves nothing: the window's command is its one change.
    assert second["reached"] and _changed(second) == 1, _what_changed(second)
    assert link.is_symlink() and (new / "claude-sessions" / "manifest.json").exists()


def test_a_one_project_window_has_the_task_written_and_says_the_projects_window_lists_it(zed_writes):
    # Zed opens the project's folder, which does not hold just code's directory: it lists no Claude task there.
    run = zed_writes["one project"]
    assert run["reached"], run["output"]
    assert run["code"] == [f"zed ssh://alpha{run['vm']}/projects/repo"], run["output"]
    assert (_ws(run) / ".zed" / "tasks.json").is_file()
    said = _claude_said(_opened(run["output"]))
    assert "run claude: the terminal is the VM's" in said
    # --editor zed, the setting VS Code's: the line that opens the projects window again names Zed.
    assert "The window of just code alpha --editor zed lists a task, Claude (just code), that does it;" in said


def test_the_notes_of_a_run_whose_editor_is_not_the_setting_s_name_it(zed_writes):
    # The setting is VS Code's (unset) and the run's --editor zed: a bare just code alpha would open VS Code, so the
    # notes' lines that open this window again name Zed.
    run = zed_writes["one project"]
    (notes,) = run["vm"].rglob("session/alpha-session.md")
    text = notes.read_text()
    assert (
        "| Zed | `just code alpha --editor zed`, or one repository: `just code alpha --editor zed --project <repo>` |"
        in text
    ), text
    assert "| `just code alpha --editor zed` | open this window again |" in text, text
    assert "`just code alpha`" not in text, text


def test_just_code_s_directory_outside_the_projects_directory_gets_no_task_and_no_word_of_one(zed_writes):
    run = zed_writes["outside the projects directory"]
    assert run["reached"], run["output"]
    ws = run["vm"] / "elsewhere"
    assert not (ws / ".zed").exists() and not (ws / "helpers").exists()
    said = _claude_said(_opened(run["output"]))
    assert "run claude: the terminal is the VM's" in said and "task" not in said, said


def test_a_helper_path_a_task_cannot_be_handed_as_it_is_writes_no_task_and_says_why(zed_writes):
    run = zed_writes["a helper path a task cannot be handed"]
    assert run["reached"], run["output"]
    ws = run["vm"] / "projects" / "odd files"
    assert not (ws / ".zed").exists() and not (ws / "helpers").exists()
    said = _opened(run["output"])
    assert f"No Claude task was written: the path of its helper, {ws}/helpers/claude-start.sh, holds a" in said


def test_a_tasks_file_that_is_not_just_code_s_is_left_byte_for_byte_and_said(zed_writes):
    run = zed_writes["someone else's tasks file"]
    assert run["reached"], run["output"]
    tasks = _ws(run) / ".zed" / "tasks.json"
    assert tasks.read_text() == FOREIGN_TASKS
    said = _opened(run["output"])
    assert (
        f"{tasks} is not just code's, so it was left as it is, and Zed lists no Claude task of just code's there."
        in said
    ), said
    assert "run claude: the terminal is the VM's" in _claude_said(said)
    # The helper is written all the same: a task of the user's own runs it.
    assert (_ws(run) / "helpers" / "claude-start.sh").is_file()


def test_a_tasks_file_with_just_code_s_marker_is_rewritten(zed_writes):
    run = zed_writes["just code's earlier tasks file"]
    assert run["reached"], run["output"]
    marker, listed = (_ws(run) / ".zed" / "tasks.json").read_text().split("\n", 1)
    assert marker == MARKER_LINE and json.loads(listed)[0]["label"] == "Claude (just code)"


LISTED_SAID = 'Claude is a task there: "task: spawn" in the command palette lists Claude (just code)'


@pytest.mark.parametrize(
    ("name", "written", "listed"),
    [
        # Written, and the window's folder holds just code's directory: the file, and the line that it lists Claude.
        ("twice", True, True),
        ("just code's earlier tasks file", True, True),
        # Written, but another folder's window: the file, and no line that this window lists it.
        ("one project", True, False),
        # Not written - a file that is not just code's, a directory outside the projects directory, a helper path
        # the task cannot be handed - and no line that a window lists it.
        ("someone else's tasks file", False, False),
        ("outside the projects directory", False, False),
        ("a helper path a task cannot be handed", False, False),
    ],
)
def test_the_task_is_written_and_said_listed_exactly_where_it_is_just_code_s_to_write(
    zed_writes, name, written, listed
):
    found = zed_writes[name]
    run = found["first"] if "first" in found else found
    assert run["reached"] and run["rc"] == 0, run["output"]
    lines = [path.read_text().splitlines()[0] for path in run["vm"].rglob(".zed/tasks.json")]
    assert (MARKER_LINE in lines) == written, (name, lines)
    said = _opened(run["output"])
    assert (LISTED_SAID in said) == listed, name
    # The trust advice names the task only in a window that lists it: elsewhere a missing task is no trust problem.
    assert ("If task: spawn lists no Claude (just code)" in said) == listed, name
    # The notes, which every window of the VM opens, name the task exactly where it was written: written before the
    # window, they know of a tasks file of someone else's all the same.
    (notes,) = run["vm"].rglob("session/alpha-session.md")
    assert ("the task **Claude (just code)**" in notes.read_text()) == written, name


def test_no_task_var_of_zed_s_window_is_put_through_bool_by_another_of_the_same_task():
    # A task's vars are rendered lazily in a run, but ansible-lint renders each apart, and a var put through bool in
    # another is then its unrendered text, coerced to False: a warning today, an error from ansible-core 2.23.
    for path in EDITOR_FILES:
        for task in yaml.safe_load((CODE.parent / path).read_text()):
            own = task.get("vars") or {}
            for name, value in own.items():
                for other in set(own) - {name}:
                    assert not re.search(rf"\b{other}\s*\|\s*bool\b", str(value)), (task["name"], name, other)


@pytest.mark.parametrize("name", ["zed, in a checkout", "vscode, in a checkout"])
def test_just_code_s_directory_inside_a_checkout_is_refused_before_any_write(zed_writes, name):
    run = zed_writes[name]
    assert run["rc"] != 0 and not run["reached"], run["output"]
    checkout = run["vm"] / "projects" / "repo"
    said = (
        f"{checkout}/.agent-warehouse, just code's own directory on alpha (editor_files_dir), is inside the checkout"
        f" {checkout}: just code writes beside the repositories, never in one."
    )
    assert any(failure.startswith(said) for failure in _failures(run["output"])), _failures(run["output"])
    assert sorted(path.name for path in checkout.iterdir()) == [".git"]
    assert run["code"] == []


def test_a_git_above_the_projects_directory_refuses_nothing(zed_writes):
    # Only the directories below the projects directory are looked in: a home directory kept in git is no checkout
    # of the projects'.
    run = zed_writes["a home kept in git"]
    assert run["reached"] and run["rc"] == 0, run["output"]
    assert (_ws(run) / ".zed" / "tasks.json").is_file()


# The shared helper, run as the task runs it - `bash <helper>`, in the window's folder - against a stand-in claude
# and a registry of Claude Code's in the test's own directory, with a claude of a terminal running where a case has
# one: a stand-in named claude, which the helper takes for one as the modules do.
START_HELPER = HELPERS / "claude-start.sh"
NOT_RUNNING = 99999999


def _start(tmp_path: Path, *, entry=None, continue_rc=0, installed=True, files=None) -> dict:
    folder = tmp_path / "projects"
    folder.mkdir()
    for name, text in (files or {}).items():
        (folder / name).write_text(text)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "claude.log"
    if installed:
        (bin_dir / "claude").write_text(
            f'#!/bin/sh\necho "claude $*" >> "{log}"\n[ "$1" = --continue ] && exit {continue_rc}\nexit 0\n'
        )
        (bin_dir / "claude").chmod(0o755)
    # The helper's PATH is this directory alone: python3, and ps, which its Python asks where there is no /proc
    # (macOS). Nothing else of the system's, so no claude of this machine's - a /usr/bin/claude - runs in place of
    # the stand-in, or where there is none; the shell part needs only its builtins.
    (bin_dir / "python3").symlink_to(sys.executable)
    ps = shutil.which("ps", path="/bin:/usr/bin")
    if ps:
        (bin_dir / "ps").symlink_to(ps)
    config = tmp_path / "config"
    running = None
    try:
        if entry is not None:
            (config / "sessions").mkdir(parents=True)
            pid = entry.get("pid")
            if pid is None:
                other = tmp_path / "running" / "claude"
                other.parent.mkdir()
                other.write_text("#!/bin/sh\n/bin/sleep 60\n")
                other.chmod(0o755)
                running = subprocess.Popen([str(other)], start_new_session=True, stdin=subprocess.DEVNULL)
                pid = running.pid
            record = {"pid": pid, "sessionId": SESSION_ID, "cwd": str(entry.get("cwd", folder)), **entry}
            record["pid"] = pid
            (config / "sessions" / f"{pid}.json").write_text(json.dumps(record))
        run = subprocess.run(
            ["/bin/bash", str(START_HELPER)],
            cwd=folder,
            env={"PATH": str(bin_dir), "HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(config)},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    finally:
        if running:
            os.killpg(running.pid, 9)
            running.wait(timeout=10)
    return {
        "rc": run.returncode,
        "output": run.stdout + run.stderr,
        "asked": [line.rstrip() for line in log.read_text().splitlines()] if log.exists() else [],
    }


@pytest.mark.parametrize(
    ("entry", "continue_rc", "asked"),
    [
        # No registry: nothing open, so the folder's last conversation is picked up.
        (None, 0, ["claude --continue"]),
        # Nothing to continue: Claude Code says so and exits 1 at once, and a fresh claude starts.
        (None, 1, ["claude --continue", "claude"]),
        # A claude of a terminal has this folder open: never a second one on its conversation.
        ({"entrypoint": "cli"}, 0, ["claude"]),
        # Remote Control's sessions are not a terminal's; nor is a claude in another folder, or one gone.
        ({"entrypoint": "sdk-cli"}, 0, ["claude --continue"]),
        ({"entrypoint": "cli", "cwd": "/elsewhere"}, 0, ["claude --continue"]),
        ({"entrypoint": "cli", "pid": NOT_RUNNING}, 0, ["claude --continue"]),
        # An entry no path can be the folder of, a running claude's: passed over, not the end of the check.
        ({"entrypoint": "cli", "cwd": "/else\0where"}, 0, ["claude --continue"]),
    ],
    ids=["no registry", "nothing to continue", "open here", "remote control", "another folder", "gone", "a NUL"],
)
def test_the_shared_helper_continues_the_folder_or_starts_a_fresh_claude_where_one_has_it_open(
    tmp_path, entry, continue_rc, asked
):
    run = _start(tmp_path, entry=entry, continue_rc=continue_rc)
    assert (run["rc"], run["asked"]) == (0, asked), run["output"]


def test_the_shared_helper_without_claude_says_so_and_exits_0(tmp_path):
    run = _start(tmp_path, installed=False)
    assert run["rc"] == 0 and run["asked"] == []
    assert run["output"].startswith("Claude Code is not installed on this VM yet"), run["output"]


def test_the_shared_helper_runs_no_module_of_the_folder_it_starts_in(tmp_path):
    # The folder is often a repository's root, and its files are not the standard library: a json.py there that
    # would run, and then say the check found no claude here, and so ask for a --continue beside the claude a
    # terminal has open there.
    ran = tmp_path / "ran"
    hostile = f"open({str(ran)!r}, 'w').close()\nraise SystemExit(0)\n"
    run = _start(tmp_path, entry={"entrypoint": "cli"}, files={"json.py": hostile})
    assert (run["rc"], run["asked"]) == (0, ["claude"]), run["output"]
    assert not ran.exists()


def _start_python() -> str:
    text = START_HELPER.read_text()
    return text.split("<<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0]


def test_the_shared_helper_counts_a_running_claude_as_the_modules_do():
    # Its copy of the modules' test, function by function: the native binary or the npm package under node, from
    # /proc or ps, one whose argv cannot be read counted as running.
    def functions(source: str) -> dict:
        return {node.name: ast.dump(node) for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}

    helper = functions(_start_python())
    shared = functions((REPO / "playbooks" / "module_utils" / "agent_warehouse.py").read_text())
    for name in ("alive", "is_claude", "process_argv", "running_claude"):
        assert helper[name] == shared[name], name


def test_the_shared_helper_is_fixed_text_run_with_no_arguments():
    # Any editor's: run as `bash <helper>`, with nothing variable in it and nothing to hand it, ending exit 0 with
    # no exec, and naming no editor.
    text = START_HELPER.read_text()
    shell = text.replace(_start_python(), "")
    assert "{{" not in text and "{%" not in text
    assert not re.search(r"\$[1-9@*#]|\$\{[1-9@*#]", shell), shell
    assert not re.search(r"^\s*exec\b", shell, re.M)
    assert shell.rstrip().endswith("exit 0")
    # Its Python isolated: no module of the folder it runs in, nor of a PYTHONPATH or the user's site.
    assert shell.count("python3") == 1 and re.search(r"^python3 -I - ", shell, re.M), shell
    assert not re.search(r"\bzed\b|vs ?code", text, re.I)


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
    # Only on a read, which --check would otherwise skip and so find nothing (the test below holds it there).
    "check_mode",
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
    # Zed: its command as which found it, given the alias and the folder, and nothing else.
    ("which", "-a"),
    ("{{ code_zed_candidate }}", "--version"),
    # The sessions, sent to the VM's mirror: read here, written there.
    ("rsync", "-az"),
}
READS_ON_VM = {"ansible.builtin.getent", "ansible.builtin.stat", "ansible.builtin.find", "ansible.builtin.slurp"}
# The session handoff's readers (playbooks/library/), run here and on the VM alike: they change nothing.
READS_EITHER = {"claude_sessions_info", "git_checkouts_info"}
# On the VM: whether Remote Control runs, with --session git in the one checkout it moves, and once, the move of just
# code's directory from its 1.x name, which never replaces anything (-n).
COMMANDS_ON_VM = {("systemctl", "is-active"), ("git", "--no-optional-locks"), ("mv", "-n")}
WRITES_ON_VM = {"ansible.builtin.file", "ansible.builtin.copy", "claude_sessions_copy"}
# What each writer runs under: VS Code's workspace and helpers with the terminal; just code's own directory
# when anything goes into it; the notes with their own switch; the sessions' directory when there is a session to
# copy, and the copies with the sync or --session; a listed task's shared helper where the task is written at all,
# and its tasks file there unless a file that is not just code's is in its place.
WRITERS_WHEN = (
    "code_writes_its_directory | bool",
    "code_notes | bool",
    "code_session_plan.sessions | default([]) | length > 0",
    "code_session_on | bool",
    # The editor's plan: what it writes and which of the helpers it names.
    "code_plan.vm_writes | length > 0",
    "code_helper in code_plan.helpers",
    # The run that moves just code's directory from its 1.x name: the shared helper's 1.x path, as a link.
    "code_ws_dir_moves | bool",
)
# The one command whose argv is no literal list: the window's, which the core built over the editor's command as its
# check found it, run here.
LAUNCH = ("editor_open | Open the window", "{{ code_plan.launch }}")
# Every place an editor's writer writes, whole: the plan's names joined under just code's directory, a helper under the
# helpers' directory. A prefix alone would pass "{{ code_ws_dir }}/../".
EDITOR_WRITES = {
    "{{ code_ws_dir }}/{{ code_plan_dir.code_plan_dir.path }}",
    "{{ code_helpers_dir }}/{{ code_helper }}",
    "{{ code_ws_dir }}/{{ code_plan_file.path }}",
}
# The task files code.yml imports that other playbooks import too, each held to its own tests: the
# Proxmox API reads, the alias (test_ssh_config.py) and the refusal of a VM named after an inventory
# host (test_vm_names.py). Every other import is code.yml's own, and its tasks are held to everything
# here as code.yml's are.
SHARED = {"tasks/cluster_vms.yml", "tasks/vm_addresses.yml", "tasks/ssh_config.yml", "tasks/vm_names.yml"}


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
    # Followed all the way down: a task file of code.yml's own that imports another is held to the allowlist
    # through it, and only a shared file is left as an import (_tasks yields no other).
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
    assert imported == SHARED | {"tasks/session_read.yml", "tasks/session_write.yml"} | set(EDITOR_FILES)


def test_the_editors_are_one_list_their_files_and_just_code_s_option_agree_on():
    # claude_editors (the inventory defaults) is what code.yml opens and deploy.yml says: each editor in it has its
    # two task files, every task file is an editor's, and --editor takes exactly its names.
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    editors = defaults["claude_editors"]
    assert set(editors) == {editor for editor in EDITOR_FILES.values() if editor}
    assert {f"tasks/{editor}_check.yml" for editor in editors} | {"tasks/editor_open.yml"} == set(EDITOR_FILES)
    assert all(
        {"name", "family", "window_tasks", "claude_terminal", "cli_candidates"} <= set(entry)
        and set(entry)
        <= {"name", "family", "window_tasks", "claude_terminal", "cli_candidates", "version_re", "server"}
        for entry in editors.values()
    ), editors
    # How each gets its Claude terminal: started as its window opens, listed for the user to pick, or none. Every
    # value is a literal - the rows are read as data, by tools/settings and the docs too - never an expression.
    assert all(entry["claude_terminal"] in {"started", "listed", "none"} for entry in editors.values()), editors
    assert all(
        isinstance(value, (str, bool, list, dict)) and "{{" not in str(value) and "{%" not in str(value)
        for entry in editors.values()
        for value in entry.values()
    ), editors
    assert (editors["vscode"]["claude_terminal"], editors["vscode"]["window_tasks"]) == ("started", True)
    assert (editors["zed"]["claude_terminal"], editors["zed"]["window_tasks"]) == ("listed", False)
    pattern = re.search(r"\[arg\('IDE', long='editor', pattern='\(([a-z|]+)\)\?'", (REPO / "justfile").read_text())
    assert pattern and set(pattern.group(1).split("|")) == set(editors), pattern
    assert defaults["editor"] in editors
    # deploy.yml says the editor by the list, never by a name of its own.
    assert "claude_editors[" in (REPO / "deploy.yml").read_text()


def test_each_editor_s_files_are_imported_for_that_editor_alone():
    # Static imports, so --tags and the walks above reach them, each editor's check under its editor's condition and
    # the window's apply for every editor; and every name an editor's file registers is its own, since a skipped
    # task still overwrites its register.
    imports = [
        t for t in _tasks(_the_window(_play())["block"]) if t.get("ansible.builtin.import_tasks") in EDITOR_FILES
    ]
    assert {t["ansible.builtin.import_tasks"]: t.get("when") for t in imports} == {
        path: f"code_editor == '{editor}'" if editor else None for path, editor in EDITOR_FILES.items()
    }
    # A name, the file and its condition, nothing else: _inlined, which the window cases run them through, carries
    # the condition alone, and a vars, tags, become or delegate_to here would make those cases run another play.
    assert all(set(t) <= {"name", "ansible.builtin.import_tasks", "when"} for t in imports), imports
    for path, editor in EDITOR_FILES.items():
        registers = [
            task["register"] for task in _tasks(yaml.safe_load((CODE.parent / path).read_text())) if "register" in task
        ]
        assert all(name.startswith(f"code_{editor or 'plan'}_") for name in registers), (path, registers)


def _action(task):
    keys = set(task) - KEYWORDS
    assert len(keys) == 1, f"{task.get('name')}: {sorted(keys)}"
    return keys.pop()


@pytest.mark.parametrize("task", list(_every_task()), ids=lambda t: t.get("name", "?"))
def test_every_task_reads_this_machine_or_is_delegated_to_the_vm(task):
    action = _action(task)
    if "check_mode" in task:
        # Run under --check too, and so only a read here: which, or a command asked its version.
        argv = (task.get("ansible.builtin.command") or {}).get("argv") or []
        assert task["check_mode"] is False and (argv[:1] == ["which"] or argv[1:] == ["--version"]), task["name"]
    assert "connection" not in task
    assert "ansible_connection" not in (task.get("vars") or {})
    if action in READS_HERE:
        assert "delegate_to" not in task
    elif action == "ansible.builtin.command" and isinstance(task[action]["argv"], str):
        assert (task["name"], task[action]["argv"]) == LAUNCH and "delegate_to" not in task
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
        if action == "ansible.builtin.copy":
            # What a copy sends to the VM: text the play built, or the helpers of playbooks/files/ - VS Code's
            # own, or the ones every editor shares - by their names alone, never a file of this machine's that a
            # list could be made to name.
            args = task[action]
            assert "remote_src" not in args, task["name"]
            if "src" in args:
                copied = (args["src"], task.get("loop"), (task.get("loop_control") or {}).get("loop_var"))
                assert copied == ("{{ code_helper }}", "{{ code_shared_helpers + code_helpers }}", "code_helper"), task[
                    "name"
                ]
            else:
                assert "content" in args, task["name"]
    elif action == "ansible.builtin.meta":
        # The one meta action: a deploy for a fleet ends the play.
        assert task[action] == "end_play", task.get("name")
    else:
        pytest.fail(f"{task.get('name')} uses {action}, which just code may not")


def test_every_task_of_the_window_step_is_in_the_block_just_deploy_rescues():
    # A task outside it would fail a deploy whose VM is done. Before it is
    # only the fleet's end, which opens nothing.
    tasks = _play()["tasks"]
    assert [task["name"] for task in tasks] == ["Leave the windows of a fleet for later", "Open the VM in the editor"]
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
    facts, task_vars = set(), set()
    for task in _every_task():
        facts |= set(task.get("ansible.builtin.set_fact") or {}) | ({task["register"]} if "register" in task else set())
        if "ansible.builtin.import_tasks" not in task:
            task_vars |= set(task.get("vars") or {})
    names = set(_vars()) | facts | task_vars
    assert facts and task_vars
    assert sorted(name for name in names if not name.startswith("code_")) == []
    assert not (REPO / "roles" / "code").exists()
    assert sorted(facts & task_vars) == []


def test_every_file_of_playbooks_files_is_shared_or_in_exactly_one_editor_s_list():
    # The lists a copy reads from this machine, by plain names: the helpers every editor shares, code.yml's own,
    # and each editor's own, in its vars file - VS Code's, the one editor with helpers of its own. Every file there is
    # in exactly one of them, and nothing else is.
    shared = _play()["vars"]["code_shared_helpers"]
    own = {"vscode": yaml.safe_load((CODE.parent / "vars" / "vscode.yml").read_text())["code_helpers"]}
    # And they are the helpers the families name in the core, whose plans the copy takes them for: held equal, so a
    # helper added to one list alone is neither copied without a word nor refused at the window.
    assert shared == [editors_zed.HELPER] and own["vscode"] == list(editors_vscode.HELPERS)
    listed = [*shared, *(name for names in own.values() for name in names)]
    assert sorted(listed) == sorted(path.name for path in HELPERS.iterdir() if path.is_file())
    assert len(listed) == len(set(listed)), listed
    assert all(re.fullmatch(r"[A-Za-z0-9_.-]+", name) and not name.startswith(".") for name in listed), listed
    # The helpers' directory and the shared list are code.yml's, never an editor's vars file's.
    assert {"code_helpers_dir", "code_shared_helpers"} <= set(_play()["vars"])
    assert not {"code_helpers_dir", "code_shared_helpers"} & set(
        yaml.safe_load((CODE.parent / "vars" / "vscode.yml").read_text())
    )


def test_just_code_writes_exactly_the_directory_and_the_file():
    # The allowlist must not pass by finding no writer.
    assert [t["name"] for t in _every_task() if _action(t) in WRITES_ON_VM] == [
        "Make the shared helper's 1.x directory on the VM",
        "Keep the shared helper's 1.x path as a link to its new one on the VM",
        "Make just code's own directory on the VM",
        "session_write | Make the directory for the sessions on the VM",
        "session_write | Write the copies the VM resumes",
        "Make the directory for the session notes on the VM",
        "Write the session notes on the VM",
        "editor_open | Make just code's directories on the VM",
        "editor_open | Write the helpers the plan names on the VM",
        "editor_open | Write just code's files on the VM",
    ]


def test_an_editor_s_writers_write_in_just_code_s_directory_by_the_plan_s_names():
    # The core holds the plan's names inside just code's directory; the writers join them under it, and nothing
    # else: no editor's writer takes a path of its own.
    writers = [t for t in _every_task() if t["name"].startswith("editor_open |") and _action(t) in WRITES_ON_VM]
    assert writers
    for task in writers:
        args = task[_action(task)]
        assert str(args.get("path") or args.get("dest")) in EDITOR_WRITES, task["name"]


def _before_the_first_write() -> tuple[list[dict], list[str]]:
    """Every task of the window step, followed down, and the names of those before its first write on the VM."""
    tasks = list(_every_task())
    first_write = next(i for i, task in enumerate(tasks) if _action(task) in WRITES_ON_VM)
    return tasks, [task["name"] for task in tasks[:first_write]]


def test_the_editor_s_plan_is_refused_before_anything_is_written_on_the_vm():
    tasks, before = _before_the_first_write()
    names = [task["name"] for task in tasks]
    refusal = names.index(PLAN_REFUSED)
    assert PLAN_REFUSED in before and tasks[refusal]["ansible.builtin.assert"]["that"] == (
        "code_plan.refusals | length == 0"
    )
    # Right after the editors' checks: the last task of each check file keeps what the plan reads.
    assert names[refusal - 1] == ZED[-1] and VS_CODE[-1] in names[:refusal]


def test_a_checkout_around_just_code_s_directory_is_refused_before_anything_is_written_on_the_vm():
    # Whichever editor: the refusal comes before the first writer, and looks only below the projects directory.
    _, before = _before_the_first_write()
    assert "Refuse just code's own directory inside a checkout" in before
    assert "Look for a checkout around just code's own directory on the VM" in before
    assert "code_root | normpath" in _play()["vars"]["code_ws_below"]


def test_no_claude_task_is_decided_from_window_tasks_alone():
    # window_tasks says whether the window runs just code's tasks as it opens - the notes' refresh, --session's
    # terminal - and claude_terminal how it gets a Claude terminal: only the former is read as the window's tasks.
    readers = sorted(
        (path.relative_to(REPO).as_posix(), line.strip())
        for path in [CODE, *(CODE.parent / "tasks").glob("*.yml"), CODE.parent / "templates" / "vm_session.md.j2"]
        for line in path.read_text().splitlines()
        if "code_editor_tasks" in line
    )
    assert readers == [
        (
            "playbooks/code.yml",
            'code_editor_tasks: "{{ (code_editors[code_editor] | default({})).window_tasks | default(false) | bool }}"',
        ),
        ("playbooks/code.yml", "{{ (code_terminal or not code_editor_tasks)"),
        ("playbooks/tasks/session_read.yml", "that: code_terminal | bool or not code_editor_tasks | bool"),
        ("playbooks/templates/vm_session.md.j2", "{%- set window_tasks = code_editor_tasks | default(true) | bool -%}"),
    ], readers
    start_here = (
        (CODE.parent / "templates" / "vm_session.md.j2").read_text().split("## Start here", 1)[1].split("##", 1)[0]
    )
    assert "window_tasks" not in start_here and "claude_terminal ==" in start_here


def test_the_session_s_checkout_folder_is_held_to_what_project_takes():
    # The VM names it, and it becomes the window's folder in an editor's task or Zed's URL: the same rule as
    # --project, a GitHub repository's name, before anything is handed over.
    read = yaml.safe_load((CODE.parent / "tasks" / "session_read.yml").read_text())
    check = next(
        t for t in read if t["name"] == "session_read | Refuse a checkout folder an editor cannot be handed as it is"
    )
    project = next(
        t for t in _tasks(_the_window(_play())["block"]) if t["name"] == "Refuse a project that is not one folder name"
    )
    pattern = "'^[A-Za-z0-9._-]{1,100}$'"
    assert any(pattern in str(that) for that in check["ansible.builtin.assert"]["that"])
    assert any(pattern in str(that) for that in project["ansible.builtin.assert"]["that"])


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
    # Zed's window runs take only paths of the "VM" they are given, and the shared helper reads a registry of the
    # test's own: its HOME and CLAUDE_CONFIG_DIR are the test's.
    assert not {"code_session_source_dir", "code_session_dest_dir"} & set(_vm_vars(tmp_path / "vm"))
    source = inspect.getsource(_start)
    assert '"HOME": str(tmp_path)' in source and '"CLAUDE_CONFIG_DIR": str(config)' in source


# What the provision scenario pins into its ephemeral directory on every run of code.yml: the VS Code settings file,
# on which what just code says depends, and both ends of the session handoff, since the VM is this machine there.
PINNED = {
    "vscode settings": ("vscode_user_settings_file",),
    "claude code": ("code_session_source_dir", "code_session_dest_dir"),
}


@pytest.mark.parametrize("keys", PINNED.values(), ids=PINNED.keys())
def test_the_provision_scenario_never_reads_or_writes_the_developer_s_own(keys):
    # Every code.yml run passes verify_code_vars, which pins them; a refusal case that builds its own base pins them
    # too; and every deploy.yml run, which ends with code.yml, has them on its command line.
    plays = [play for _, play in verify_plays(PROVISION)]
    play = next(p for p in plays if "verify_code_vars" in (p.get("vars") or {}))
    for key in keys:
        assert "MOLECULE_EPHEMERAL_DIRECTORY" in play["vars"]["verify_code_vars"][key], key
    runs = [t for t in _tasks(play["tasks"]) if "verify_code_playbook" in str(t.get("ansible.builtin.command", ""))]
    assert len(runs) >= 6
    for task in runs:
        # On the command line, or on the stdin of the task that runs the refusals.
        assert "verify_code_vars" in str(task["ansible.builtin.command"]), task["name"]
    # The refusal cases sit on the block around the task that runs them, which a walk of the tasks never yields.
    cases = [case for item in _with_vars(play["tasks"]) for case in item["vars"].get("verify_code_cases", [])]
    assert len(cases) >= 12, cases
    for case in cases:
        if "base" in case:
            assert set(keys) <= set(case["base"]), case
    # The run that refuses to clone on another node stops long before code.yml.
    deploys = [
        task
        for each in plays
        for task in _tasks(each.get("tasks") or [])
        if "deploy.yml" in str(task.get("ansible.builtin.command", "")) and "pve2" not in str(task)
    ]
    assert deploys
    for task in deploys:
        argv = str(task["ansible.builtin.command"]["argv"])
        assert all(key in argv for key in keys) and "MOLECULE_EPHEMERAL_DIRECTORY" in argv, task["name"]


# ---------------------------------------------------------------- the session notes in the window ---
def test_the_whole_directory_s_window_shows_the_notes_folder_and_opens_them_as_a_preview(windows):
    run = windows["notes, whole directory"]
    workspace = run["shown"]
    # A second folder only where the window's own folder holds it: trust is unchanged.
    assert workspace["folders"] == [
        {"path": "/home/dev/projects"},
        {"path": SESSION, "name": "alpha · session"},
    ]
    assert workspace["settings"] == {
        "files.exclude": {".agent-warehouse": True},
        "workbench.editorAssociations": {"**/.agent-warehouse/session/*-session.md": "vscode.markdown.preview.editor"},
    }
    # Side by side when the window opens: the notes never hold Claude up. A
    # dependsOn made VS Code ask every task provider first, 7.5 s live.
    notes, shell, claude = workspace["tasks"]["tasks"]
    assert notes["label"] == "Session notes" and notes["runOptions"] == {"runOn": "folderOpen"}
    # The helpers by path, in just code's own directory on the VM: no script text in the workspace.
    assert notes["args"] == [
        "-l",
        "/home/dev/projects/.agent-warehouse/helpers/session-notes.sh",
        f"{SESSION}/alpha-session.md",
        "alpha",
        "claude-remote-control",
        "UTC",
    ]
    assert claude["args"][:2] == ["-l", "/home/dev/projects/.agent-warehouse/helpers/claude-terminal.sh"]
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
    assert run["opened"].endswith("editor_session_notes: false turns them off.")


def test_a_project_s_window_keeps_its_one_folder_and_says_the_question_vs_code_may_ask(windows):
    # The notes' folder is outside the project's: added, it would put a window
    # trusted before back in Restricted Mode, without the Claude terminal.
    run = windows["notes, one project"]
    workspace = run["shown"]
    assert workspace["folders"] == [{"path": "/home/dev/projects/discord-music-bot"}]
    assert [task["label"] for task in workspace["tasks"]["tasks"]] == ["Session notes", "Shell", "Claude"]
    assert workspace["tasks"]["tasks"][1]["options"] == {"cwd": "/home/dev/projects/discord-music-bot"}
    assert (
        "VS Code may ask once whether to open them, as a file outside this window's folder: Open, or trust the"
        " window of just code alpha, which covers them." in run["opened"]
    ), run["opened"]


def test_workspace_files_kept_elsewhere_give_no_window_the_notes_folder(windows):
    run = windows["notes, workspaces elsewhere"]
    workspace = run["shown"]
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
    tasks = _workspace_planned()["tasks"]["tasks"]
    assert [task["label"] for task in tasks] == ["Session notes", "Shell", "Claude"]
    assert all("dependsOn" not in task for task in tasks)


@pytest.mark.parametrize("case", SWITCH_CASES)
def test_each_switch_is_read_by_its_new_name_or_its_old_one(windows, case):
    run = windows[f"switches, {case}"]
    assert run["shown"] == SWITCH_CASES[case][1], run["output"]


@pytest.mark.parametrize("case", FILES_CASES)
def test_just_code_s_directory_is_read_by_its_new_name_or_its_old_one(windows, case):
    run = windows[f"files, {case}"]
    assert run["shown"] == FILES_CASES[case][1], run["output"]


@pytest.mark.parametrize("name", ["notes off", "notes off, by 1.1's name"])
def test_with_the_notes_off_the_workspace_is_as_it_was(windows, name):
    run = windows[name]
    workspace = run["shown"]
    assert workspace["folders"] == [{"path": "/home/dev/projects"}]
    assert workspace["settings"] == {"files.exclude": {".agent-warehouse": True}}
    shell, claude = workspace["tasks"]["tasks"]
    assert (shell["label"], claude["label"]) == ("Shell", "Claude")
    assert "dependsOn" not in claude
    assert claude["args"][-5:] == ["", "", "claude-remote-control", "UTC", ""]
    assert "session notes" not in run["opened"]


# ---------------------------------------------------------------- the session notes' helper ---
# Run as the "Session notes" task runs it, with stand-ins for what it reads, and
# nothing else on PATH: never this machine's own claude, gh, tailscale or code.
LIVE_START, LIVE_END = "<!-- agent-warehouse:live -->", "<!-- /agent-warehouse:live -->"
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
    argv = ["/bin/bash", *(["-e"] if errexit else []), str(NOTES_HELPER), str(notes), "alpha"]
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


def test_the_helper_finds_the_live_section_as_1x_marked_it_and_writes_it_with_2_0_s_markers(tmp_path):
    # Notes 1.x wrote, read before just code writes them again: the section is still found, and written back marked
    # as from 2.0, so the next run finds it by either.
    old = NOTES_TEXT.replace("agent-warehouse:live", "claude-on-proxmox:live")
    result, _, after, _, _ = _helper(tmp_path, text=old)
    assert result.returncode == 0, result
    text = after.decode()
    assert text.count(LIVE_START) == 1 and text.count(LIVE_END) == 1 and "claude-on-proxmox:live" not in text
    assert "| Claude Code | `dev@example.com` · claude.ai, max plan |" in _live(after)


# The time is said in the zone of the machine just code ran on, which the VM's clock (UTC) is not; for a zone the VM's
# tzdata lacks, or no zone, it is still said, in UTC.
@pytest.mark.parametrize(
    ("zone", "label"), [("Asia/Kolkata", "IST"), ("Mars/Olympus", "UTC"), ("../../etc/passwd", "UTC"), ("", "UTC")]
)
def test_the_helper_says_its_time_in_the_zone_it_is_given_or_in_utc(tmp_path, zone, label):
    _, _, after, _, _ = _helper(tmp_path, zone=zone)
    assert re.search(rf"^\| \| as of \d\d:\d\d {label} on \d\d \w\w\w \|$", _live(after), re.M), _live(after)


def test_a_vm_signed_in_nowhere_names_each_sign_in(tmp_path):
    _, _, after, _, _ = _helper(tmp_path, answers=SIGNED_OUT)
    live = _live(after)
    assert "| Claude Code | not signed in: just login alpha --only claude on your machine |" in live
    assert "| Remote Control | not running (inactive) |" in live
    assert "| GitHub (`gh`) | not signed in: just login alpha --only github on your machine |" in live
    assert "| Tailscale | NeedsLogin: just login alpha --only tailscale signs it in |" in live


def test_with_more_than_one_host_each_sign_in_line_names_the_vms_host(tmp_path):
    # The host just code wrote into the notes (vm_session.md.j2's marker), on every just line of the rows, as on the
    # notes' own lines; anything at the marker but a host's name is nothing.
    marked = NOTES_TEXT.replace("VM 101\n", "VM 101\n<!-- agent-warehouse:on --host nuc -->\n")
    (tmp_path / "nuc").mkdir()
    (tmp_path / "odd").mkdir()
    _, _, after, _, _ = _helper(tmp_path / "nuc", answers=SIGNED_OUT, text=marked)
    live = _live(after)
    assert "| Claude Code | not signed in: just login alpha --only claude --host nuc on your machine |" in live
    assert "| GitHub (`gh`) | not signed in: just login alpha --only github --host nuc on your machine |" in live
    assert "| Tailscale | NeedsLogin: just login alpha --only tailscale --host nuc signs it in |" in live
    odd = NOTES_TEXT.replace("VM 101\n", "VM 101\n<!-- agent-warehouse:on --host nuc; rm -rf ~ -->\n")
    _, _, after, _, _ = _helper(tmp_path / "odd", answers=SIGNED_OUT, text=odd)
    assert "| Claude Code | not signed in: just login alpha --only claude on your machine |" in _live(after)


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
    source = NOTES_PYTHON.read_text()
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
    from claude_vm import text

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
    script = NOTES_HELPER.read_text()
    assert not re.search(r"^\s*exec\b", script, re.M)
    lines = [line for line in script.splitlines() if not line.startswith("#")]
    assert lines[0] == "set +e" and lines[-1] == "exit 0"
    # Nothing waits on it any more, so no wait of its own; code -r in the
    # foreground, so the task's terminal does not close under it.
    assert "sleep 1" not in script
    assert re.search(r'^  code -r "\$1" </dev/null >/dev/null 2>&1$', script, re.M)
    # Its Python is the file beside it, found from bash's own $0: nothing else on PATH but python3.
    python = r"""^python3 "\$\{0%/\*\}/session_notes.py" "\$1" "\$2" "\$3" "\$4" >/dev/null 2>&1 &$"""
    assert re.search(python, script, re.M)


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
    # Nor in the log alone, where a lookup's ignored error goes: macOS has no /etc/timezone.
    assert "occurred while running the lookup plugin" not in run["log"], run["log"]


# ------------------------------------------------- the Claude terminal refreshes them after a sign-in ---
def _signing_in(tmp_path: Path, helper: str | None, **case):
    """The Claude script with the notes' four arguments; what it printed, and what the helper was asked."""
    argv, env = _terminal(tmp_path, **case)
    (tmp_path / "bin" / "bash").symlink_to("/bin/bash")
    marker = tmp_path / "helper.log"
    stand_in = tmp_path / "session-notes.sh"
    stand_in.write_text(f'printf "%s\\n" "session-notes $*" >> "{marker}"\n')
    script = "" if helper is None else str(stand_in)
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
    "Look for just code's own directory on the VM",
    "Make just code's own directory on the VM",
    "Carry the sessions to the VM",
    FIND,
    SAID,
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
        show=f"[code_target, ({WORKSPACE}).tasks.tasks[-1].args[-1]]",
        code_here=True,
        claude_vm_addressed=ADDRESSED,
        **extra,
    )


def _changed(run: dict) -> int:
    return int(re.search(r"^localhost\s*:\s*ok=\d+\s+changed=(\d+)", run["output"], re.MULTILINE).group(1))


def _what_changed(run: dict) -> str:
    """A run's exit status, the tasks it changed and the end of its output: what an assertion on _changed says, since
    pytest cuts a long message short."""
    changed = re.findall(r"^TASK \[(.+?)\] \**\n(?:(?!TASK \[).*\n)*?changed: ", run["output"], re.MULTILINE)
    return f"rc {run.get('rc')}, reached {run.get('reached')}, changed: {changed}\n...{run['output'][-1500:]}"


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
    sessions = world["vm"] / "projects" / ".agent-warehouse" / "claude-sessions"
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
    # just code's own directory, made before the sessions' 0700 one inside it, is 0755, not that one's mode.
    assert stat.S_IMODE((world["vm"] / "projects" / ".agent-warehouse").stat().st_mode) == 0o755
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
    assert stat.S_IMODE((world["vm"] / "projects" / ".agent-warehouse" / "claude-sessions").stat().st_mode) == 0o700
    assert run["opened"].endswith(
        "1 of this machine's Claude Code sessions of the last 30 days, of octo/demo, is on alpha (1 copied now): in"
        " claude there, /resume and then Ctrl+A lists them. claude_sessions_sync: false turns the copying off."
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
    assert not (world["vm"] / "projects" / ".agent-warehouse" / "claude-sessions").exists()
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
