"""The Claude session `make code` starts in the window it opens, and what it may write.

`playbooks/code.yml` hands VS Code a workspace file whose one task runs
`bash -lc <claude_code_session> make-code <not installed> <signed in>` when the
window opens. The script is run here as it is, against a stand-in `claude` that
records what it was asked: PATH holds the stand-in alone, and the script uses
only shell builtins besides it, so nothing on this machine is reached. The
Molecule provision scenario checks the workspace file and the task around it.
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


def test_a_signed_in_vm_goes_straight_to_claude(tmp_path):
    result, asked = _run(tmp_path, status_rc=0)
    assert result.returncode == 0
    assert asked == ["claude auth status", "claude"]
    assert result.stdout == ""


def test_a_vm_not_signed_in_signs_in_then_starts_claude(tmp_path):
    result, asked = _run(tmp_path, status_rc=1, login_rc=0)
    assert result.returncode == 0
    assert asked == ["claude auth status", "claude auth login", "claude"]
    assert "Signing in to Claude first" in result.stdout
    assert result.stdout.rstrip().endswith(SIGNED_IN)


def test_a_failed_sign_in_stops_there(tmp_path):
    result, asked = _run(tmp_path, status_rc=1, login_rc=1)
    assert result.returncode == 1
    assert asked == ["claude auth status", "claude auth login"]
    assert SIGNED_IN not in result.stdout


def test_no_remote_control_hint_when_there_is_none_to_give(tmp_path):
    result, asked = _run(tmp_path, status_rc=1, login_rc=0, signed_in="")
    assert asked[-1] == "claude"
    assert result.stdout.strip().splitlines() == [
        "Signing in to Claude first. Your browser opens the sign-in page; if it shows a code, paste it here."
    ]


def test_the_task_passes_the_script_and_its_messages_as_arguments():
    task = _play()["vars"]["claude_code_workspace_file"]["tasks"]["tasks"][0]
    assert task["command"] == "/bin/bash"
    assert task["args"][:3] == ["-lc", "{{ claude_code_session }}", "make-code"]
    assert task["args"][3:] == ["{{ claude_code_not_installed }}", "{{ claude_code_signed_in }}"]
    assert task["runOptions"] == {"runOn": "folderOpen"}


WRITERS = {
    "ansible.builtin.copy",
    "ansible.builtin.file",
    "ansible.builtin.template",
    "ansible.builtin.lineinfile",
    "ansible.builtin.blockinfile",
}


@pytest.mark.parametrize(
    "task",
    [t for t in _play()["tasks"] if WRITERS & t.keys()],
    ids=lambda t: t["name"],
)
def test_make_code_writes_only_on_the_vm(task):
    # On this machine, code.yml writes the SSH alias through
    # tasks/ssh_config.yml and nothing else: never the user's VS Code
    # settings, whose automatic-tasks answer is theirs to give.
    assert task.get("delegate_to") == "{{ claude_vm_names[0] }}"
    assert task.get("when") == "claude_code_terminal"


def test_make_code_writes_something():
    # The parametrised test above must not pass by finding nothing.
    assert [t["name"] for t in _play()["tasks"] if WRITERS & t.keys()] == [
        "Make the directory for VS Code workspace files on the VM",
        "Write the VS Code workspace on the VM",
    ]
