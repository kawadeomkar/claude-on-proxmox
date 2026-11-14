"""The session notes just code writes on the VM: playbooks/templates/vm_session.md.j2, rendered as code.yml renders it.

Everything outside the live markers is just code's and must not change from one
run to the next for an unchanged VM, so it holds no time; the live section is
the window's "Session notes" task's, carried over as it is. The VM's own state
- who is signed in, its MagicDNS name - is only ever in that section.
"""

from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml
from harness import REPO, _failures, _run

TEMPLATE = REPO / "playbooks" / "templates" / "vm_session.md.j2"
# What the project writes on this machine outside the checkout, which code.yml loads from its vars_files.
WRITES = yaml.safe_load((REPO / "playbooks" / "vars" / "controller_writes.yml").read_text())
START, END = "<!-- agent-warehouse:live -->", "<!-- /agent-warehouse:live -->"
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
    "code_notes_dir": "/home/dev/projects/.agent-warehouse/session",
    "code_remote_control_unit": "claude-remote-control",
    "code_notes_live": "",
    **WRITES,
}
SECTIONS = [
    "# alpha",
    START,
    "## At a glance",
    "## Reach this VM",
    "## Start here",
    "## From your machine",
    "## Written on your machine",
    "## When something is off",
    "## About this file",
]


def _render(root: Path, overrides: dict) -> str:
    """The notes as code.yml's "Write the session notes on the VM" writes them, with FACTS and overrides."""
    root.mkdir(parents=True)
    out = root / "notes.md"
    playbook = root / "render.yml"
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
    local = {"localhost": {"ansible_connection": "local", "ansible_python_interpreter": sys.executable}}
    report = _run(root, playbook, local, "-e", json.dumps({**FACTS, **overrides}), timeout=120)
    assert report["rc"] == 0, (_failures(report), report["stderr"][-2000:])
    return out.read_text()


def _outside(text: str) -> str:
    return text[: text.index(START)] + text[text.index(END) + len(END) :]


# An editor whose window runs no task, and lists its Claude task (Zed), as code.yml says it.
LISTED = {"code_editor": "zed", "code_editor_name": "Zed", "code_editor_tasks": False, "code_editor_claude": "listed"}
# Every set of facts a test renders the notes with, by name: each rendered once, for the module (notes).
VARIANTS = {
    "default": {},
    # What the window wrote in the live section, carried over.
    "live": {"code_notes_live": LIVE},
    # VS Code's notes, the editor said rather than left to the template's default.
    "vscode said": {"code_editor_name": "VS Code", "code_editor_tasks": True, "code_editor_claude": "started"},
    # Zed's Claude task: written, the window of another folder, not written, unset.
    "zed, task": {**LISTED, "code_claude_task_there": True},
    "zed, task, one project": {**LISTED, "code_claude_task_there": True, "code_target": "/home/dev/projects/repo"},
    "zed, no task": {**LISTED, "code_claude_task_there": False},
    "zed, task unset": LISTED,
    # A run whose --editor is not the setting's editor (code.yml's code_editor_flag).
    "zed, --editor zed": {**LISTED, "code_claude_task_there": True, "code_editor_flag": " --editor zed"},
    "vscode, --editor vscode": {"code_editor": "vscode", "code_editor_flag": " --editor vscode"},
    # A VM on another of more than one host: claude_host_flag, which code.yml's code_editor_flag ends with too.
    "on nuc": {"claude_host_flag": " --host nuc", "claude_ssh_origin": "nuc", "code_editor_flag": " --host nuc"},
    "switched off": {"tailscale_enabled": False, "claude_code_remote_control": False, "github_auth": False},
    "no sizes": {"claude_vm_addressed": [{key: value for key, value in VM.items() if not key.startswith("max")}]},
    # A window of one repository.
    "one project": {"code_target": "/home/dev/projects/discord-music-bot"},
    # The settings that move a row's place: under this machine's home, said with ~; elsewhere, as set.
    "moved": {
        "code_here_home": "/Users/somebody",
        "claude_ssh_config_file": "/Users/somebody/.ssh/elsewhere.conf",
        "claude_ssh_user_config": "/Users/somebody/.ssh/config",
        "destroy_sessions_dir": "/srv/sessions",
    },
}


@pytest.fixture(scope="module")
def notes(tmp_path_factory) -> dict[str, str]:
    """Every variant's notes, side by side: each is a playbook run of its own, about two seconds long."""
    root = tmp_path_factory.mktemp("notes")
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = {name: pool.submit(_render, root / re.sub(r"\W+", "-", name), given) for name, given in VARIANTS.items()}
    return {name: run.result() for name, run in runs.items()}


def test_the_sections_in_order_with_the_placeholder_until_the_window_has_opened(notes):
    text = notes["default"]
    at = [text.index(section) for section in SECTIONS]
    assert at == sorted(at), at
    live = text[text.index(START) : text.index(END) + len(END)]
    placeholder = 'Opening the window fills this in; "Tasks: Run Task", **Session notes** refreshes it.'
    assert live == f"{START}\n## Signed in\n\n{placeholder}\n{END}"
    assert text.startswith("# alpha\n\nVM 101 on Proxmox node **pve** · written by `just code` on your machine")


LISTED_TASK = (
    "- In the window of `just code alpha`, the task **Claude (just code)** runs Claude Code in a terminal of Zed's,"
    " which is this VM's: it picks the folder's conversation up, or starts a fresh one where a `claude` already has it"
    " open; `/clear` starts afresh. In any other window, run `claude` in its terminal. How to start the task:"
    " `docs/guides/zed.md` in the project."
)


def test_in_an_editor_whose_window_runs_no_task_the_notes_say_what_to_do_instead(notes):
    # Zed runs nothing as its window opens: no Claude terminal below, no Session notes task to refresh the live
    # section or to run by hand, and none of VS Code's own fixes.
    zed = notes["zed, no task"]
    live = zed[zed.index(START) : zed.index(END) + len(END)]
    placeholder = "Zed does not fill this in: `just status alpha` on your machine says what is signed in."
    assert live == f"{START}\n## Signed in\n\n{placeholder}\n{END}"
    assert "| Zed | `just code alpha`, or one repository: `just code alpha --project <repo>` |" in zed
    assert "- Run `claude` in Zed's terminal, which is this VM's:" in zed
    for vs_code in ("Tasks:", "VS Code", "The terminal below", "Restricted Mode", "Session notes**"):
        assert vs_code not in zed, vs_code
    # VS Code's notes, as before, whether the editor is said or left to the template's default.
    assert notes["vscode said"] == notes["default"]


def test_the_notes_say_a_listed_claude_task_only_where_just_code_wrote_it(notes):
    # There - written, just code's directory inside the projects directory, and over no tasks file of someone else's
    # - the task is named by its label, with the editor's guide for how to start it and no key; every window of the
    # VM opens these notes, so they say which window lists it, and nothing of one window's.
    written = notes["zed, task"]
    assert LISTED_TASK in written
    assert "Run `claude` in Zed's terminal" not in written
    assert notes["zed, task, one project"] == written
    # Not there - the directory outside the projects directory, the terminal's switch off, a path the task could
    # not be handed, a tasks file of someone else's - no word of a task.
    for name in ("zed, no task", "zed, task unset"):
        start_here = notes[name].split("## Start here", 1)[1].split("##", 1)[0]
        assert "task" not in start_here and "Run `claude` in Zed's terminal" in start_here, name
    # No key of the project's, and nothing of Zed's own words in the template: the editor's name and guide come in.
    assert "ctrl-" not in written and "cmd-" not in written
    assert not re.search(r"\bzed\b", TEMPLATE.read_text(), re.I)


# A bare just code opens the editor setting's editor: where --editor chose another, code.yml's code_editor_flag puts
# it on every just code line of the notes, so they open this run's window again.
RUNS = {
    "no --editor": ("default", "just code alpha", "trusting the window of `just code alpha` once"),
    "the setting vscode, --editor zed": (
        "zed, --editor zed",
        "just code alpha --editor zed",
        "- In the window of `just code alpha --editor zed`, the task **Claude (just code)**",
    ),
    "the setting zed, --editor vscode": (
        "vscode, --editor vscode",
        "just code alpha --editor vscode",
        "trusting the window of `just code alpha --editor vscode` once",
    ),
}


@pytest.mark.parametrize("run", RUNS)
def test_the_notes_just_code_lines_open_the_window_of_the_run_s_editor(notes, run):
    variant, line, also = RUNS[run]
    text = notes[variant]
    assert f"`{line}`, or one repository: `{line} --project <repo>` |" in text, text
    assert f"| `{line}` | open this window again |" in text, text
    assert also in text, text
    if line != "just code alpha":
        assert "`just code alpha`" not in text, text


def test_with_more_than_one_host_the_notes_name_the_vms_host_on_every_just_line(notes):
    text = notes["on nuc"]
    assert text.startswith("# alpha\n\nVM 101 on Proxmox host **nuc**, node **pve** · written by `just code`")
    # The marker the window's notes task reads, so the rows it writes name the host too (session_notes.py).
    assert "\n<!-- agent-warehouse:on --host nuc -->\n" in text
    lines = re.findall(r"`(just (?:code|login|deploy|destroy) alpha[^`]*)`", text)
    assert lines and all(line.endswith(" --host nuc") or " --host nuc " in line for line in lines), lines
    assert "--host" not in notes["default"]


def test_what_the_window_wrote_is_carried_over_as_it_is(notes):
    text = notes["live"]
    assert LIVE in text and text.count(START) == 1
    # Nothing outside it depends on when it was written: an unchanged VM, an unchanged file.
    assert _outside(text) == _outside(notes["default"])


def test_the_vm_s_facts_and_the_commands_with_its_name(notes):
    text = notes["default"]
    assert "| Machine | 2 cores · 4 GiB memory · 32 GiB disk |" in text
    assert "| LAN address | `192.0.2.51` (DHCP, as the guest agent reported it) |" in text
    for command in (
        "`ssh alpha`",
        "`ssh dev@192.0.2.51`",
        "`just code alpha`",
        "`just login alpha --only claude`",
        "`just login alpha --only github`",
        "`just login alpha --only tailscale`",
        "`just destroy alpha`",
        "`ssh-keygen -R 192.0.2.51`",
        "`systemctl status claude-remote-control`",
    ):
        assert command in text, command
    # The way in over Tailscale is the VM's to know: a pointer here, the command in the live section.
    assert "| SSH, over Tailscale | the command in the Tailscale row of **Signed in**" in text
    assert ".ts.net" not in _outside(text)


def test_switched_off_parts_are_left_out(notes):
    text = notes["switched off"]
    assert "Tailscale" not in _outside(text)
    assert "Remote Control" not in text and "claude-remote-control" not in text
    assert "--only github" not in text


def test_a_listing_without_the_vm_s_sizes_leaves_the_machine_row_out(notes):
    text = notes["no sizes"]
    assert "| Machine |" not in text and "| User | `dev` · projects in `/home/dev/projects` |" in text


def test_every_window_of_the_vm_writes_the_same_notes(notes):
    # One file per VM, opened by each of its windows: nothing of one window's in it.
    assert notes["one project"] == notes["default"]


@pytest.mark.parametrize("marker", ["${", "{{", "{%", "gho_", "ghp_", "tskey-", "sk-ant-"])
def test_nothing_a_template_or_a_secret_would_leave(notes, marker):
    assert marker not in notes["live"]


def test_no_time_is_written_outside_the_live_section():
    # Only the window's task writes "as of": the template itself names no clock.
    source = TEMPLATE.read_text()
    assert not re.search(r"now\(|strftime|ansible_date_time|lookup\('pipe'", source)


def _written(text: str) -> list[str]:
    """The rows of the notes' Written on your machine table, its head left out."""
    section = text.split("## Written on your machine", 1)[1].split("\n## ", 1)[0]
    return [line for line in section.splitlines() if line.startswith("| ")][1:]


def test_the_notes_list_every_write_on_this_machine(notes):
    # One row per entry of playbooks/vars/controller_writes.yml, in its order, each with what writes it and what takes
    # it out, then a line for each of what the tools keep for themselves.
    rows = _written(notes["default"])
    for row, entry in zip(rows, WRITES["code_controller_writes"], strict=True):
        cells = (entry[key] for key in ("path", "what", "writer", "when", "removed_by"))
        assert row == "| " + " | ".join(cells) + " |"
    for line in WRITES["code_controller_also"]:
        assert f"\n- {line}\n" in notes["default"]


def test_a_moved_place_is_said_under_the_home_as_a_tilde(notes):
    # The notes go to the VM: where the settings put a row's file is said, never this machine's home itself.
    rows = {row.split(" | ")[0]: row for row in _written(notes["moved"])}
    assert rows["| `~/.ssh/agent-warehouse.conf`: here `~/.ssh/elsewhere.conf`"]
    assert rows["| `destroy_sessions_dir`: here `/srv/sessions`"]
    # The user config is where the row already says: nothing added.
    assert rows["| one `Include` line at the top of `~/.ssh/config`"]
    assert "/Users/somebody" not in notes["moved"]
    assert _written(notes["default"]) == [
        row.replace(": here `~/.ssh/elsewhere.conf`", "").replace(": here `/srv/sessions`", "")
        for row in _written(notes["moved"])
    ]


def test_another_editor_s_notes_leave_out_a_row_of_vs_code_s(notes):
    # VS Code's extension is installed only for VS Code's windows: Zed's notes have every other row.
    vscode = {row.split(" | ")[0] for row in _written(notes["default"])}
    zed = {row.split(" | ")[0] for row in _written(notes["zed, no task"])}
    assert vscode - zed == {"| VS Code's Remote - SSH extension, in `~/.vscode/extensions/`"}
    assert zed < vscode
