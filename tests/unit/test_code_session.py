"""The Claude session `make code` starts in the window it opens, and what it may write.

`playbooks/code.yml` hands VS Code a workspace file whose one task runs
`bash -lc <claude_code_session> make-code <not installed> <signed in>` when the
window opens. The script is run here as it is, against a stand-in `claude` that
records what it was asked: PATH holds the stand-in alone, and the script uses
only shell builtins besides it, so nothing on this machine is reached. The
Molecule provision scenario checks the workspace file and the task around it.

The second half holds every task in `code.yml` to an allowlist, because the
playbook reads the user's own VS Code settings and must never write them. On
this machine a task may assert, debug, set facts, add the host, import the
project's task files, or run `which` and `code`; on the VM, reached only
through `delegate_to`, it may stat, getent and find, and make the one
directory and the one file. `tasks/ssh_config.yml` (the alias) is held to its
own rules by test_ssh_config.py, and `code --install-extension` fills VS
Code's own extension store, by design.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CODE = REPO / "playbooks" / "code.yml"
NOT_INSTALLED = "Claude Code is not installed here yet."
SIGNED_IN = "Signed in; start Remote Control from your machine."

STAND_IN = """#!/bin/sh
echo "claude $*" >> "$CLAUDE_LOG"
case "$1 $2" in
  "auth status") exit "$STATUS_RC";;
  "auth login") exit "$LOGIN_RC";;
esac
exit 0
"""


def _play() -> dict:
    return yaml.safe_load(CODE.read_text())[0]


def _session() -> str:
    return _play()["vars"]["claude_code_session"]


def _run(tmp_path: Path, *, installed=True, status_rc=1, login_rc=0, signed_in=SIGNED_IN):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "claude.log"
    if installed:
        stand_in = bin_dir / "claude"
        stand_in.write_text(STAND_IN)
        stand_in.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", "-c", _session(), "make-code", NOT_INSTALLED, signed_in],
        env={
            "PATH": str(bin_dir),
            "CLAUDE_LOG": str(log),
            "STATUS_RC": str(status_rc),
            "LOGIN_RC": str(login_rc),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    asked = [line.strip() for line in log.read_text().splitlines()] if log.exists() else []
    return result, asked


def test_the_script_is_not_a_template():
    # Ansible templates play vars; this one must reach the VM as written.
    script = _session()
    assert "{{" not in script
    assert "{%" not in script


def test_without_claude_it_says_how_to_install_it_and_fails(tmp_path):
    result, asked = _run(tmp_path, installed=False)
    assert result.returncode == 1
    assert result.stdout == NOT_INSTALLED + "\n"
    assert asked == []


def test_a_signed_in_vm_picks_the_folder_s_conversation_up(tmp_path):
    # --continue: the folder's latest conversation, or a new one. A task
    # terminal does not survive a window reload, and the task then runs again.
    result, asked = _run(tmp_path, status_rc=0)
    assert result.returncode == 0
    assert asked == ["claude auth status", "claude --continue"]
    assert result.stdout == ""


def test_a_vm_not_signed_in_signs_in_then_starts_claude(tmp_path):
    result, asked = _run(tmp_path, status_rc=1, login_rc=0)
    assert result.returncode == 0
    assert asked == ["claude auth status", "claude auth login", "claude --continue"]
    assert "Signing in to Claude first" in result.stdout
    assert result.stdout.rstrip().endswith(SIGNED_IN)


def test_a_failed_sign_in_stops_there(tmp_path):
    result, asked = _run(tmp_path, status_rc=1, login_rc=1)
    assert result.returncode == 1
    assert asked == ["claude auth status", "claude auth login"]
    assert SIGNED_IN not in result.stdout


def test_no_remote_control_hint_when_there_is_none_to_give(tmp_path):
    result, asked = _run(tmp_path, status_rc=1, login_rc=0, signed_in="")
    assert asked[-1] == "claude --continue"
    assert result.stdout.strip().splitlines() == [
        "Signing in to Claude first. Your browser opens the sign-in page; if it shows a code, paste it here."
    ]


def test_the_task_passes_the_script_and_its_messages_as_arguments():
    task = _play()["vars"]["claude_code_workspace_file"]["tasks"]["tasks"][0]
    assert task["command"] == "/bin/bash"
    assert task["args"][:3] == ["-lc", "{{ claude_code_session }}", "make-code"]
    assert task["args"][3:] == ["{{ claude_code_not_installed }}", "{{ claude_code_signed_in }}"]
    assert task["runOptions"] == {"runOn": "folderOpen"}


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
    "ansible.builtin.debug",
    "ansible.builtin.set_fact",
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
        assert task.get("when") == "claude_code_terminal"
    else:
        pytest.fail(f"{task.get('name')} uses {action}, which make code may not")


def test_make_code_writes_exactly_the_directory_and_the_file():
    # The allowlist must not pass by finding no writer.
    assert [t["name"] for t in _every_task() if _action(t) in WRITES_ON_VM] == [
        "Make the directory for VS Code workspace files on the VM",
        "Write the VS Code workspace on the VM",
    ]
