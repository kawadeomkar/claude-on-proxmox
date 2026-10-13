"""The Claude session `make code` starts in the window it opens, the window step around it, and what it may write.

`playbooks/code.yml` hands VS Code a workspace file whose one task runs
`bash -lc <code_terminal_script> make-code <not installed> <signed in, watched>
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
the inventory defaults: as make code and as make deploy's last step, for one
VM, none named and a fleet, a VM left stopped, no `code` command, no alias, a
refused SSH connection and an extension that would not install. `which` and
`code` are stand-ins there, first on a PATH that holds no other, so no VS Code
of this machine's is ever started.

The rest holds every task in `code.yml` to an allowlist, because the playbook
reads the user's own VS Code settings and must never write them. On this
machine a task may assert, fail, debug, set facts, add the host, import the
project's task files, or run `which` and `code`; on the VM, reached only
through `delegate_to`, it may stat, getent and find, and make the one
directory and the one file. `tasks/ssh_config.yml` (the alias) is held to its
own rules by test_ssh_config.py, and `code --install-extension` fills VS
Code's own extension store, by design. The last tests keep the provision
scenario, which runs `code.yml` on this machine, away from the developer's own
VS Code settings.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import pty
import re
import select
import subprocess
import sys
import termios
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CODE = REPO / "playbooks" / "code.yml"
DEPLOY = REPO / "deploy.yml"
ANSIBLE_PLAYBOOK = REPO / ".venv" / "bin" / "ansible-playbook"
PROVISION_VERIFY = REPO / "molecule" / "provision" / "verify.yml"
NOT_INSTALLED = "Claude Code is not installed here yet."
WATCHED = "Signed in; Remote Control starts by itself."
UNWATCHED = "Signed in; start Remote Control from your machine."
SIGNING_IN = "Signing in to Claude first. Your browser opens the sign-in page; if it shows a code, paste it here."
NOT_SIGNED_IN = 'Not signed in to Claude. To try again, reload the window or run "Tasks: Run Task", Claude.'
GITHUB_SIGNED_IN = "Signed in to GitHub; run make configure to clone the private repositories."
GITHUB_SIGNING_IN = (
    "Signing in to GitHub next, so that git push and gh work here; Ctrl-C skips this. "
    "Your browser opens github.com/login/device; enter the code below there."
)
GITHUB_STATUS = "gh auth status --hostname github.com --active"
GITHUB_LOGIN = "gh auth login -h github.com -p https -w -s workflow --skip-ssh-key"
GITHUB_PAGE = "browser https://github.com/login/device"
GITHUB_FAILED = "Not signed in to GitHub. From your machine: make github-login VM_NAME=alpha"
GITHUB_CODE = "FAKE-C0DE"
GITHUB_EXPIRED = "failed to authenticate via web browser: This 'device_code' has expired. (expired_token)"

STAND_IN = """#!/bin/sh
echo "claude $*" >> "$CLAUDE_LOG"
case "$1 $2" in
  "auth status") exit "$STATUS_RC";;
  "auth login") exit "$LOGIN_RC";;
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
    argv = ["/bin/bash", "-c", _script(), "make-code", NOT_INSTALLED, *signed_in, str(watch), github_signed_in]
    env = {
        "PATH": str(bin_dir),
        "BROWSER": str(bin_dir / "browser"),
        "CLAUDE_LOG": str(tmp_path / "claude.log"),
        "STATUS_RC": str(status_rc),
        "LOGIN_RC": str(login_rc),
        "GH_STATUS_RC": str(gh_status_rc),
        "GH_STATUS_ANY_RC": str(gh_status_rc if gh_status_any_rc is None else gh_status_any_rc),
        "GH_LOGIN_RC": str(gh_login_rc),
    }
    return [*argv, "https", "workflow", "alpha"], env


def _asked(tmp_path: Path) -> list[str]:
    log = tmp_path / "claude.log"
    return [line.strip() for line in log.read_text().splitlines()] if log.exists() else []


def _run(tmp_path: Path, **case):
    argv, env = _terminal(tmp_path, **case)
    result = subprocess.run(argv, env=env, capture_output=True, text=True, check=False, timeout=30)
    return result, _asked(tmp_path)


def _run_at_a_terminal(tmp_path: Path, keys: bytes, *, after: str, **case) -> tuple[int, str, list[str]]:
    """Run the script at a pseudo-terminal of its own; type `keys` once the stand-ins have logged `after`."""
    argv, env = _terminal(tmp_path, **case)
    master, slave = pty.openpty()

    def take_the_terminal() -> None:
        # A session of its own, with this terminal as its controlling one, so
        # that Ctrl-C reaches bash and everything it started, as in VS Code.
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
                break
        else:
            pytest.fail("the script did not finish within 30 seconds:\n" + output.decode(errors="replace"))
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
    task = _play()["vars"]["code_workspace_file"]["tasks"]["tasks"][0]
    assert task["command"] == "/bin/bash"
    assert task["args"][:3] == ["-lc", "{{ code_terminal_script }}", "make-code"]
    assert task["args"][3:] == [
        "{{ code_not_installed }}",
        "{{ code_signed_in_watched }}",
        "{{ code_signed_in_unwatched }}",
        "{{ code_login_watch }}",
        "{{ code_github_signed_in }}",
        "{{ github_projects_clone_protocol | default('https') }}",
        "{{ github_projects_login_scopes | default(['workflow']) | join(',') }}",
        "{{ claude_vm_names[0] }}",
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
}
ARGS = "code_workspace_file.tasks.tasks[0].args[3:]"
LATER = " VS Code was not opened; to open it later: make code VM_NAME=alpha"


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
    (root / "ansible.cfg").write_text("")
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

    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(
        ANSIBLE_CONFIG=str(root / "ansible.cfg"),
        ANSIBLE_FILTER_PLUGINS=str(REPO / "filter_plugins"),
        ANSIBLE_NOCOLOR="1",
        # The run's stats printed: what it recorded for NEXT STEPS.
        ANSIBLE_SHOW_CUSTOM_STATS="1",
        PATH=f"{bin_dir}:{WINDOW_PATH}",
    )
    # Never the developer's own VS Code settings: what is said depends on them.
    extra = {"ansible_python_interpreter": sys.executable, "vscode_user_settings_file": str(root / "none"), **extra}
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
    "make code, code here": {"keep": ONE_VM + VS_CODE, "in_deploy": False, "code_here": True, "vm_name": "alpha"},
    "make code, no code": {"keep": ONE_VM + VS_CODE, "in_deploy": False, "vm_name": "alpha"},
    "deploy, no name": {"keep": ONE_VM, "in_deploy": True},
    "make code, no name": {"keep": ONE_VM, "in_deploy": False},
    "deploy, fleet": {"keep": ONE_VM, "in_deploy": True, "vm_name": "alpha,beta"},
    "make code, fleet": {"keep": ONE_VM, "in_deploy": False, "vm_name": "alpha,beta"},
    "deploy, stopped": {"keep": UP, "in_deploy": True, "vm_name": "beta", "claude_vm_addressed": STOPPED},
    "make code, stopped": {"keep": UP, "in_deploy": False, "vm_name": "beta", "claude_vm_addressed": STOPPED},
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
    "make code, no extension": {"keep": EXTENSION, "in_deploy": False, "code_here": True, "vm_name": "alpha"},
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
    "make code, twice": {
        "keep": ["Find the VM"],
        "in_deploy": False,
        "vm_name": "alpha",
        "claude_vm_all": _listing("alpha", "alpha"),
    },
    "make code, missing": {
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
}


@pytest.fixture(scope="module")
def windows(tmp_path_factory):
    """Every run in WINDOWS, side by side: each is a playbook run of its own, about a second long."""
    root = tmp_path_factory.mktemp("windows")
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = {name: pool.submit(_window, root / re.sub(r"\W+", "-", name), **case) for name, case in WINDOWS.items()}
    return {name: run.result() for name, run in runs.items()}


def test_a_deploy_for_one_vm_goes_on_to_the_window_named_or_not(windows):
    # Without VM_NAME, a deploy is for the default VM: one name all the same.
    for name in ("deploy, code here", "deploy, no name"):
        assert windows[name]["reached"] and windows[name]["rc"] == 0, windows[name]["output"]


def test_make_code_goes_on_to_the_window_where_vs_code_s_command_is(windows):
    run = windows["make code, code here"]
    assert run["reached"] and run["rc"] == 0, run["output"]


def test_make_code_without_vs_code_s_command_fails_and_says_how_to_get_it(windows):
    run = windows["make code, no code"]
    assert run["rc"] != 0 and not run["reached"]
    assert "`code` is not on this machine's PATH. In VS Code, run" in run["output"]
    assert run["later"] is None


def test_make_deploy_without_vs_code_s_command_says_so_with_the_make_code_line_and_goes_on(windows):
    run = windows["deploy, no code"]
    assert run["rc"] == 0 and not run["reached"], run["output"]
    assert run["later"].startswith("`code` is not on this machine's PATH. In VS Code, run")
    assert run["later"].endswith(LATER)
    assert run["left"] == {"alpha": {"vscode": run["later"]}}


def test_make_code_needs_one_vm_name(windows):
    for name in ("make code, fleet", "make code, no name"):
        run = windows[name]
        assert run["rc"] != 0 and "VS Code opens one VM at a time: make code VM_NAME=<name>." in run["output"], name
        assert run["fleet"] is None, name


def test_a_deploy_for_a_fleet_ends_at_its_first_task_with_each_vm_s_make_code_line(windows):
    run = windows["deploy, fleet"]
    assert run["rc"] == 0 and not run["reached"], run["output"]
    assert run["fleet"] == [
        "VS Code opens one VM at a time, so this deploy for 2 VMs opened none. To open one:",
        "make code VM_NAME=alpha",
        "make code VM_NAME=beta",
    ]
    assert "TASK [Refuse anything but one VM]" not in run["output"]
    # Left to do for the fleet, once, as said.
    assert run["left"] == {"alpha, beta": {"vscode": "\n".join(run["fleet"])}}


def test_a_deploy_that_left_its_vm_stopped_names_the_deploy_that_starts_it(windows):
    # make code alone would open a VM this deploy never configured or signed in.
    run = windows["deploy, stopped"]
    assert run["rc"] == 0 and not run["reached"], run["output"]
    assert run["later"] == (
        "beta is stopped, and this deploy left it so: it was not configured, signed in or opened in VS Code. "
        'To start it and finish the deploy: make deploy VM_NAME=beta ANSIBLE_ARGS="-e proxmox_vm_start_existing=true"'
    )
    # Recorded as the VM's own matter, in the place of provision's line about the same thing.
    assert run["left"] == {"beta": {"vm": run["later"]}}


def test_make_code_refuses_a_stopped_vm(windows):
    run = windows["make code, stopped"]
    assert run["rc"] != 0 and not run["reached"]
    assert "beta is stopped, so there is nothing to connect to. `make list` shows the fleet;" in run["output"]


@pytest.mark.parametrize(
    ("name", "said"),
    [
        ("deploy, no alias", "claude_ssh_config is false, so this project keeps no SSH alias for the VM"),
        (
            "deploy, no address",
            "alpha is running and reported no address, so there is nothing to connect to. `make list` shows the fleet;",
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
def test_make_deploy_says_any_failure_of_the_window_step_with_the_make_code_line_and_goes_on(windows, name, said):
    run = windows[name]
    assert run["rc"] == 0 and not run["reached"], run["output"]
    assert run["later"].startswith(said), run["later"]
    assert run["later"].endswith(LATER) and "\n" not in run["later"], run["later"]


def test_every_failure_of_the_window_step_is_said_on_one_line(windows):
    # A more-indented line in a folded scalar keeps its line break, and an
    # SSH error can carry one of its own, or the escapes of a VM's banner.
    failures = {name: _failures(run["output"]) for name, run in windows.items()}
    assert failures["make code, stopped"] and failures["deploy, ssh refused"]
    for name, said in failures.items():
        assert not [one for one in said if re.search(r"[\x00-\x1f\x7f]", one)], (name, said)


def test_make_code_fails_where_make_deploy_goes_on_and_names_the_command_that_failed(windows):
    run = windows["make code, no extension"]
    assert run["rc"] != 0 and not run["reached"]
    assert "`code --install-extension ms-vscode-remote.remote-ssh` failed: Error while installing" in run["output"]
    assert run["code"] == ["code --list-extensions", "code --install-extension ms-vscode-remote.remote-ssh"]


def test_a_bare_deploy_opens_the_default_vm_beside_other_tagged_ones(windows):
    # Without VM_NAME, vm_name is not defined, and claude_vm_requested is the
    # whole tagged fleet; the window step opens the one VM deploy was for.
    run = windows["deploy, no name, beside others"]
    assert run["reached"], run["output"]


def test_a_named_deploy_opens_that_vm(windows):
    run = windows["deploy, named"]
    assert run["reached"], run["output"]


def test_a_real_duplicate_or_a_missing_vm_is_still_refused(windows):
    twice, missing = windows["make code, twice"], windows["make code, missing"]
    assert twice["rc"] != 0 and "More than one VM tagged" in twice["output"], twice["output"]
    assert missing["rc"] != 0 and "No VM named gamma is tagged" in missing["output"], missing["output"]


def test_the_terminal_is_handed_the_lines_for_this_vm(windows):
    run = windows["messages"]
    not_installed, watched, unwatched, watch, github, protocol, scopes, vm = run["shown"]
    assert "make configure VM_NAME=alpha," in not_installed
    assert watched.startswith("Signed in. Remote Control starts by itself in a moment")
    assert watched.endswith("run on your machine: make configure VM_NAME=alpha TAGS=claude_code")
    assert unwatched == (
        "Signed in. To reach this VM from the Claude apps too, start Remote Control from your machine: "
        "make configure VM_NAME=alpha TAGS=claude_code"
    )
    assert watch == "/etc/systemd/system/claude-remote-control.path"
    assert github.endswith("run on your machine: make configure VM_NAME=alpha TAGS=github_projects")
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
}
READS_ON_VM = {"ansible.builtin.getent", "ansible.builtin.stat", "ansible.builtin.find"}
WRITES_ON_VM = {"ansible.builtin.file", "ansible.builtin.copy"}


def _tasks(items):
    # Every task, through block/rescue/always; a block itself has no action.
    for item in items:
        if "block" in item:
            for section in ("block", "rescue", "always"):
                yield from _tasks(item.get(section) or [])
        else:
            yield item


def _every_task():
    play = _play()
    for section in ("pre_tasks", "tasks", "post_tasks", "handlers"):
        yield from _tasks(play.get(section) or [])


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
    elif action == "ansible.builtin.command":
        assert "delegate_to" not in task
        assert tuple(task[action]["argv"][:2]) in COMMANDS_HERE
    elif action in READS_ON_VM:
        assert task.get("delegate_to") == ON_VM
    elif action in WRITES_ON_VM:
        assert task.get("delegate_to") == ON_VM
        assert task.get("when") == "code_terminal"
    elif action == "ansible.builtin.meta":
        # The one meta action: a deploy for a fleet ends the play.
        assert task[action] == "end_play", task.get("name")
    else:
        pytest.fail(f"{task.get('name')} uses {action}, which make code may not")


def test_every_task_of_the_window_step_is_in_the_block_make_deploy_rescues():
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
    # code_, which no role uses: make deploy runs the roles in the same run,
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


def test_make_code_writes_exactly_the_directory_and_the_file():
    # The allowlist must not pass by finding no writer.
    assert [t["name"] for t in _every_task() if _action(t) in WRITES_ON_VM] == [
        "Make the directory for VS Code workspace files on the VM",
        "Write the VS Code workspace on the VM",
    ]


def test_the_provision_scenario_never_reads_the_developer_s_vscode_settings():
    # Every code.yml run in the scenario passes verify_code_vars, which pins
    # vscode_user_settings_file into the ephemeral directory; a refusal case
    # that builds its own base must pin it too. What make code says depends
    # on that file, so a run that fell through to the developer's own would
    # pass or fail by the machine it runs on.
    plays = yaml.safe_load(PROVISION_VERIFY.read_text())
    play = next(p for p in plays if "verify_code_vars" in (p.get("vars") or {}))
    pinned = play["vars"]["verify_code_vars"]["vscode_user_settings_file"]
    assert "MOLECULE_EPHEMERAL_DIRECTORY" in pinned
    runs = [t for t in _tasks(play["tasks"]) if "verify_code_playbook" in str(t.get("ansible.builtin.command", ""))]
    assert len(runs) >= 6
    for task in runs:
        argv = str(task["ansible.builtin.command"]["argv"])
        assert "verify_code_vars" in argv, task["name"]
        for case in (task.get("vars") or {}).get("verify_code_cases", []):
            if "base" in case:
                assert "vscode_user_settings_file" in case["base"], case


def test_the_provision_scenario_s_deploy_runs_never_read_the_developer_s_vscode_settings_either():
    # make deploy ends with code.yml, so every deploy.yml run there pins the
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
