"""The session notes make code writes on the VM: playbooks/templates/vm_session.md.j2, rendered as code.yml renders it.

Everything outside the live markers is make code's and must not change from one
run to the next for an unchanged VM, so it holds no time; the live section is
the window's "Session notes" task's, carried over as it is. The VM's own state
- who is signed in, its MagicDNS name - is only ever in that section.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from harness import ansible_config

REPO = Path(__file__).resolve().parents[2]
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
TEMPLATE = REPO / "playbooks" / "templates" / "vm_session.md.j2"
START, END = "<!-- claude-on-proxmox:live -->", "<!-- /claude-on-proxmox:live -->"
LIVE = f"{START}\n## Signed in\n\n| | as of 14:02 on 12 Oct |\n|---|---|\n| Claude Code | `dev@example.com` |\n{END}"
VM = {
    "name": "alpha",
    "vmid": 101,
    "node": "pve",
    "status": "running",
    "maxcpu": 2,
    "maxmem": 4 * 1024**3,
    "maxdisk": 32 * 1024**3,
    "address": "192.0.2.51",
}
FACTS = {
    "claude_vm_names": ["alpha"],
    "claude_vm_addressed": [VM],
    "vm_user": "dev",
    "code_root": "/home/dev/projects",
    "code_target": "/home/dev/projects",
    "code_notes_dir": "/home/dev/projects/.claude-on-proxmox/session",
    "code_remote_control_unit": "claude-remote-control",
    "code_notes_live": "",
}
SECTIONS = [
    "# alpha",
    START,
    "## At a glance",
    "## Reach this VM",
    "## Start here",
    "## From your machine",
    "## When something is off",
    "## About this file",
]


def _render(tmp_path: Path, **overrides) -> str:
    """The notes as code.yml's "Write the session notes on the VM" writes them, with FACTS and overrides."""
    out = tmp_path / "notes.md"
    playbook = tmp_path / "render.yml"
    playbook.write_text(
        json.dumps(
            [
                {
                    "hosts": "localhost",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "ansible.builtin.copy": {
                                "dest": str(out),
                                "content": "{{ lookup('ansible.builtin.template', '" + str(TEMPLATE) + "') }}",
                            }
                        }
                    ],
                }
            ]
        )
    )
    env = {
        "ANSIBLE_CONFIG": ansible_config(tmp_path),
        "ANSIBLE_NOCOLOR": "1",
        "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
        "HOME": str(tmp_path),
    }
    extra = {**FACTS, **overrides, "ansible_connection": "local", "ansible_python_interpreter": sys.executable}
    result = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", "localhost,", "-e", json.dumps(extra), str(playbook)],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return out.read_text()


def _outside(text: str) -> str:
    return text[: text.index(START)] + text[text.index(END) + len(END) :]


def test_the_sections_in_order_with_the_placeholder_until_the_window_has_opened(tmp_path):
    text = _render(tmp_path)
    at = [text.index(section) for section in SECTIONS]
    assert at == sorted(at), at
    live = text[text.index(START) : text.index(END) + len(END)]
    placeholder = 'Opening the window fills this in; "Tasks: Run Task", **Session notes** refreshes it.'
    assert live == f"{START}\n## Signed in\n\n{placeholder}\n{END}"
    assert text.startswith("# alpha\n\nVM 101 on Proxmox node **pve** · written by `make code` on your machine")


def test_what_the_window_wrote_is_carried_over_as_it_is(tmp_path):
    text = _render(tmp_path, code_notes_live=LIVE)
    assert LIVE in text and text.count(START) == 1
    # Nothing outside it depends on when it was written: an unchanged VM, an unchanged file.
    assert _outside(text) == _outside(_render(tmp_path))


def test_the_vm_s_facts_and_the_commands_with_its_name(tmp_path):
    text = _render(tmp_path)
    assert "| Machine | 2 cores · 4 GiB memory · 32 GiB disk |" in text
    assert "| LAN address | `192.0.2.51` (DHCP, as the guest agent reported it) |" in text
    for command in (
        "`ssh alpha`",
        "`ssh dev@192.0.2.51`",
        "`make code VM_NAME=alpha`",
        "`make claude-login VM_NAME=alpha`",
        "`make github-login VM_NAME=alpha`",
        "`make tailscale-login VM_NAME=alpha`",
        "`make destroy VM_NAME=alpha`",
        "`ssh-keygen -R 192.0.2.51`",
        "`systemctl status claude-remote-control`",
    ):
        assert command in text, command
    # The way in over Tailscale is the VM's to know: a pointer here, the command in the live section.
    assert "| SSH, over Tailscale | the command in the Tailscale row of **Signed in**" in text
    assert ".ts.net" not in _outside(text)


def test_switched_off_parts_are_left_out(tmp_path):
    text = _render(tmp_path, tailscale_enabled=False, claude_code_remote_control=False, github_auth=False)
    assert "Tailscale" not in _outside(text)
    assert "Remote Control" not in text and "claude-remote-control" not in text
    assert "make github-login" not in text


def test_a_listing_without_the_vm_s_sizes_leaves_the_machine_row_out(tmp_path):
    bare = {key: value for key, value in VM.items() if not key.startswith("max")}
    text = _render(tmp_path, claude_vm_addressed=[bare])
    assert "| Machine |" not in text and "| User | `dev` · projects in `/home/dev/projects` |" in text


def test_every_window_of_the_vm_writes_the_same_notes(tmp_path):
    # One file per VM, opened by each of its windows: nothing of one window's in it.
    assert _render(tmp_path, code_target="/home/dev/projects/discord-music-bot") == _render(tmp_path)


@pytest.mark.parametrize("marker", ["${", "{{", "{%", "gho_", "ghp_", "tskey-", "sk-ant-"])
def test_nothing_a_template_or_a_secret_would_leave(tmp_path, marker):
    assert marker not in _render(tmp_path, code_notes_live=LIVE)


def test_no_time_is_written_outside_the_live_section():
    # Only the window's task writes "as of": the template itself names no clock.
    source = TEMPLATE.read_text()
    assert not re.search(r"now\(|strftime|ansible_date_time|lookup\('pipe'", source)
