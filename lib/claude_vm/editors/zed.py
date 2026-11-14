"""Zed's family: a window over the alias, Claude a task just code writes where the window lists it. A module of free
functions, never a class (docs/architecture/decisions.md §15.4)."""

from __future__ import annotations

import posixpath
import re
from typing import Final

from ..refs import NextStep
from . import common
from .model import Editor, OpenInputs, OpenPlan, VmDirectory, VmFile, VmWrite

# What Zed's ssh:// URL carries as it is: a folder with anything else is said, never opened wrong.
URL_PATH: Final = re.compile(r"/[A-Za-z0-9._~+,=:@/-]*")
TASKS: Final = ".zed/tasks.json"
HELPER: Final = "claude-start.sh"
AGENT_PANEL: Final = (
    "Claude Agent in Zed's Agent Panel runs on the VM as well, signed in the same way, but as a Claude Code of its own"
    " that Zed installs and updates. Zed asks nothing about trust: Restricted Mode shows at the top left of the window"
    " once it holds something back, a language server, and trusting the projects window trusts every checkout in it."
)


def _refusal(editor: Editor, inputs: OpenInputs) -> str:
    """The first of Zed's refusals, as its checks said them one after another: its command here, then the folder."""
    vm = inputs.vm.name
    if not common.pick_cli(editor, inputs.probe):
        found = [line for line in inputs.probe.which if line]
        missing = (
            "Neither `zed` nor `zeditor` is on this machine's PATH."
            if not found
            else "No `zed` or `zeditor` on this machine's PATH is Zed: "
            + ", ".join(found)
            + " said otherwise when asked its version (ZFS's event daemon is `zed` too)."
        )
        return (
            f'{missing} In Zed, run "cli: install cli binary" ("cli: install" in earlier versions) from the command'
            " palette (macOS), or install Zed's package (Linux); then run this again. The alias is in place either"
            f" way: ssh {vm}."
        )
    if not URL_PATH.fullmatch(inputs.target):
        return (
            f"Zed is handed the folder in an ssh:// URL, which cannot carry {inputs.target} as it is: a URL takes"
            ' letters, digits, "/" and . _ ~ + , = : @ - there. Set vm_projects_dir to a path of only those, or open'
            f" it in VS Code: just code {vm} --editor vscode{inputs.host_flag}."
        )
    return ""


def _claude(editor: Editor, inputs: OpenInputs, there: bool, listed: bool, helper: str) -> str:
    vm = inputs.vm.name
    terminal = (
        "Open a terminal there, in the terminal panel, and run claude: the terminal is the VM's, and claude is signed"
        f" in there by just deploy (or just login {vm} --only claude{inputs.host_flag})."
    )
    if inputs.session_id:
        moved = ", where just code moved it" if inputs.session_moved else ""
        return (
            f"Open a terminal there, in the terminal panel, and run claude --resume {inputs.session_id} to continue"
            f" the session just code carried over, on {inputs.session_branch} at {inputs.session_commit[:12]}"
            f"{moved}.{inputs.session_cautions}"
        )
    if listed:
        return (
            'Claude is a task there: "task: spawn" in the command palette lists Claude (just code), which runs'
            " claude in a terminal of its own, the VM's: it picks the folder's last conversation up, or starts a fresh"
            " one where a claude already has it open, so a second one never ends or shares the first; claude is"
            f" signed in there by just deploy (or just login {vm} --only claude{inputs.host_flag}). docs/guides/zed.md:"
            " one key, or"
            " Claude with no key."
        )
    if there:
        return terminal + (
            f" The window of {common.just_code(inputs)} lists a task, Claude (just code), that does it;"
            " docs/guides/zed.md: one key, or Claude with no key."
        )
    below = common.below(inputs.files_dir, inputs.root)
    if inputs.terminal and editor.claude_terminal == "listed" and below and not common.HELPER_PATH.fullmatch(helper):
        return terminal + (
            f" No Claude task was written: the path of its helper, {helper}, holds a character a task is not handed as"
            ' it is (letters, digits, ".", "_", "-" and "/" are).'
        )
    return terminal


def plan_open(editor: Editor, inputs: OpenInputs) -> OpenPlan:
    refused = _refusal(editor, inputs)
    if refused:
        return OpenPlan(editor=editor.key, refusals=(refused,))
    helper = posixpath.join(inputs.files_dir, common.HELPERS, HELPER)
    # Zed joins a task's args into one bash -i -c command on the VM: the helper's path goes in only as a plain path,
    # whatever the inputs say, or no task is written at all.
    written = inputs.task_written and bool(common.HELPER_PATH.fullmatch(helper))
    # A tasks file there is just code's only where its first line is the marker: one without it is someone else's.
    head = inputs.probe.tasks_head
    foreign = head is not None and head not in (common.MARKER, common.MARKER_1X)
    there = written and not foreign
    listed = there and common.below(inputs.files_dir, inputs.target)
    writes: tuple[VmWrite, ...] = ()
    if written:
        writes = (VmDirectory(kind="directory", path=common.HELPERS),)
        if not foreign:
            # No cwd, so the task runs in the window's folder; a new terminal for every spawn, so a spawn never
            # replaces a Claude already running; no tag; a label no repository's own "Claude" shares.
            task = {
                "label": "Claude (just code)",
                "command": "bash",
                "args": [helper],
                "allow_concurrent_runs": True,
                "use_new_terminal": True,
                "show_command": False,
            }
            text = f"{common.MARKER}\n{common.nice_json([task])}\n"
            writes += (
                VmDirectory(kind="directory", path=".zed"),
                VmFile(kind="file", path=TASKS, mode="0644", content=text),
            )
    vm = inputs.vm.name
    report = (
        f"{editor.name} is opening {inputs.target} on {vm} ({inputs.reach}).",
        "The first connection installs Zed's remote server on the VM and takes a moment.",
        _claude(editor, inputs, there, listed, helper),
        f"{inputs.files_dir}/{TASKS} is not just code's, so it was left as it is, and Zed lists no Claude task of just"
        " code's there."
        if foreign
        else "",
        AGENT_PANEL,
        "If task: spawn lists no Claude (just code) and Restricted Mode shows, docs/guides/zed.md says what to do"
        " instead."
        if listed
        else "",
        f"The session notes about the VM are at {inputs.notes_file}." if inputs.notes else "",
    )
    return OpenPlan(
        editor=editor.key,
        vm_writes=writes,
        helpers=(HELPER,) if written else (),
        opens=inputs.target,
        report=report,
        claude_task=there,
        next_steps=(NextStep(vm=inputs.vm, matter="editor", action=None),),
    )
