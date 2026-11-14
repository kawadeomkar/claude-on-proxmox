"""VS Code's family: its settings reader - the user's own settings here, read and never written - and the plan of its
window, the workspace file and the helpers just code writes on the VM, and what VS Code will ask."""

from __future__ import annotations

import json
import posixpath

from ..refs import NextStep
from . import common
from .model import Editor, OpenInputs, OpenPlan, VmDirectory, VmFile, VmWrite


def jsonc_loads(text: str) -> object:
    """Parse JSON with comments and trailing commas, as VS Code writes its settings.

    One pass over the text: a string is copied whole, escapes and all, so a
    ``//`` in a URL is not a comment and a ``"`` in a comment does not start a
    string; ``//`` and ``/* */`` comments are dropped; a comma whose next
    character that is not blank or a comment closes an object or an array is
    dropped as well.
    """
    text = text.lstrip("\ufeff")
    out = []
    comma = None
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char == '"':
            end = i + 1
            while end < n and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(text[i : end + 1])
            comma = None
            i = end + 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if char in "}]" and comma is not None:
            out[comma] = ""
        if not char.isspace():
            comma = len(out) if char == "," else None
        out.append(char)
        i += 1
    return json.loads("".join(out))


def _values_tree(settings: dict) -> dict:
    """Expand dotted keys into nested objects the way VS Code's ``toValuesTree`` does.

    Keys apply in file order, so the later of a flat and a nested spelling
    wins, and a nested object replaces whatever the flat keys built before
    it. A key whose path runs into a value that is not an object is dropped,
    as VS Code drops it (with a logged conflict).
    """
    tree = {}
    for key, value in settings.items():
        node = tree
        parts = key.split(".")
        for part in parts[:-1]:
            if part not in node:
                node[part] = {}
            elif not isinstance(node[part], dict):
                break
            node = node[part]
        else:
            node[parts[-1]] = value
    return tree


def vscode_setting(text: str, key: str, default: object = "", unparsable: object = "unparsable") -> object:
    """Return the value VS Code's settings.json ``text`` gives ``key``, or ``default``.

    Read-only and forgiving on purpose: a run that only wants to say what VS
    Code will do must not fail on the user's own editor settings. Empty text
    gives ``default``. Text that does not parse, or is not an object, gives
    ``unparsable``, so the plan tells a broken file from an absent key. A key
    may be written flat (``"task.allowAutomaticTasks"``), as VS Code writes it,
    or nested (``"task": {"allowAutomaticTasks": ...}``); the later spelling
    wins, as VS Code reads it.
    """
    if not text or not text.strip():
        return default
    try:
        settings = jsonc_loads(text)
    except ValueError:
        return unparsable
    if not isinstance(settings, dict):
        return unparsable
    node = _values_tree(settings)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


# --- VS Code's family: the workspace file just code writes on the VM, its helpers, the window and what is said ------
# VS Code's own helpers, which the workspace's tasks run by path with `bash -l <helper> <arguments>`.
HELPERS = ("claude-terminal.sh", "session-notes.sh", "session_notes.py")
WORKSPACE_SUFFIX = ".code-workspace"


def _workspace_file(inputs: OpenInputs) -> str:
    """The workspace file, under just code's directory: one project's in project/, so a repository that shares the
    projects directory's name cannot collide with the whole directory's."""
    if inputs.project:
        return f"project/{inputs.project}{WORKSPACE_SUFFIX}"
    return f"{posixpath.basename(inputs.root) or 'projects'}{WORKSPACE_SUFFIX}"


def _claude_lines(inputs: OpenInputs) -> list[str]:
    """The lines the Claude terminal is handed after its helper, in the helper's order."""
    vm, on = inputs.vm.name, inputs.host_flag
    signed_in_watched = (
        "Signed in. Remote Control starts by itself in a moment, putting this VM in the Claude apps too; to keep it on"
        f" across reboots, run on your machine: just deploy {vm} --tags claude_code{on}"
        if inputs.remote_control
        else ""
    )
    signed_in_unwatched = (
        "Signed in. To reach this VM from the Claude apps too, start Remote Control from your machine: just deploy"
        f" {vm} --tags claude_code{on}"
        if inputs.remote_control
        else ""
    )
    return [
        f"Claude Code is not installed on {vm} yet. On your machine, run just deploy {vm} --only configure{on}, then"
        ' "Tasks: Run Task", Claude here.',
        signed_in_watched,
        signed_in_unwatched,
        f"/etc/systemd/system/{inputs.remote_control_unit}.path",
        _github_said(inputs),
        inputs.clone_protocol,
        inputs.login_scopes,
        vm,
    ]


def _github_said(inputs: OpenInputs) -> str:
    return (
        "Signed in to GitHub. To clone your private repositories here too, run on your machine: just deploy"
        f" {inputs.vm.name} --tags github_projects{inputs.host_flag}"
        if inputs.github_signed_in
        else ""
    )


def _notes_in_window(inputs: OpenInputs) -> bool:
    """Whether the notes' folder lies in the window's own: only there is it a second folder of the workspace, since
    VS Code stores the folders a user trusted, not their parents, and a folder added to a trusted workspace drops it to
    Restricted Mode."""
    return inputs.notes and common.below(inputs.notes_dir, inputs.target)


def _workspace(inputs: OpenInputs) -> dict:
    """The workspace as data, written as to_nice_json writes it, so no path or message can break its JSON. Absolute
    paths throughout; the notes' folder a second one only where the window's own folder holds it."""
    vm, target, helpers = inputs.vm.name, inputs.target, posixpath.join(inputs.files_dir, common.HELPERS)
    notes_helper = f"{helpers}/session-notes.sh"
    folder = _notes_in_window(inputs)
    folders = [{"path": target}] + ([{"path": inputs.notes_dir, "name": f"{vm} · session"}] if folder else [])
    own = posixpath.basename(inputs.files_dir)
    settings = {"files.exclude": {own: True}} if posixpath.dirname(inputs.files_dir) == target else {}
    if inputs.notes:
        settings["workbench.editorAssociations"] = {f"**/{own}/session/*-session.md": "vscode.markdown.preview.editor"}
    shell = {
        "label": "Shell",
        "type": "process",
        "command": "/bin/bash",
        "args": ["-l"],
        "options": {"cwd": target},
        "runOptions": {"runOn": "folderOpen"},
        "presentation": {
            "reveal": "always",
            "focus": False,
            "panel": "dedicated",
            "group": "claude",
            "clear": True,
            "close": True,
            "showReuseMessage": False,
        },
        "problemMatcher": [],
    }
    claude = {
        "label": "Claude",
        "type": "process",
        "command": "/bin/bash",
        "args": [
            "-l",
            f"{helpers}/claude-terminal.sh",
            *_claude_lines(inputs),
            notes_helper if inputs.notes else "",
            inputs.notes_file if inputs.notes else "",
            inputs.remote_control_unit,
            inputs.timezone,
            inputs.session_id,
        ],
        "options": {"cwd": target},
        "runOptions": {"runOn": "folderOpen"},
        "presentation": {
            "reveal": "always",
            "focus": True,
            "panel": "dedicated",
            "group": "claude",
            "clear": True,
            "showReuseMessage": True,
        },
        "problemMatcher": [],
    }
    notes = {
        "label": "Session notes",
        "type": "process",
        "command": "/bin/bash",
        "args": ["-l", notes_helper, inputs.notes_file, vm, inputs.remote_control_unit, inputs.timezone],
        "runOptions": {"runOn": "folderOpen"},
        "presentation": {"reveal": "never", "focus": False, "echo": False, "close": True, "showReuseMessage": False},
        "problemMatcher": [],
    }
    tasks = ([notes] if inputs.notes else []) + [shell, claude]
    return {"folders": folders, "settings": settings, "tasks": {"version": "2.0.0", "tasks": tasks}}


def _said(inputs: OpenInputs, auto: object, terminal: bool, again: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """What is said of the window, from what the user's own settings say VS Code will ask; again is the just code
    line that opens the whole projects directory's window."""
    vm, root, probe = inputs.vm.name, inputs.root, inputs.probe
    below = common.below(inputs.files_dir, root)
    trust_once = (
        ""
        if not below
        else f"Trusting this window trusts every project in {root}."
        if not inputs.project
        else f"Trusting the window of {again} instead trusts every project in {root} at once."
    )
    if not probe.settings_file:
        settings_note = (
            "No VS Code settings file was found where stock VS Code keeps one on this OS, so whether you have answered"
            " before is unknown; a VS Code that keeps its settings elsewhere is pointed at with"
            " vscode_user_settings_file. "
        )
    elif not probe.settings_exists:
        settings_note = (
            f"No VS Code settings file at {probe.settings_file}, so whether you have answered before is unknown. "
        )
    elif probe.settings_text is None:
        settings_note = (
            f"Your VS Code settings, {probe.settings_file}, could not be read, so whether you have answered before is"
            " unknown. "
        )
    elif auto == "unparsable":
        settings_note = (
            f"Your VS Code settings, {probe.settings_file}, did not parse, so whether you have answered before is"
            " unknown; VS Code itself reads past the error. "
        )
    else:
        settings_note = ""
    if auto == "off":
        first_time = (
            f'It will not start on its own: task.allowAutomaticTasks is "off" in {probe.settings_file}. Choose Allow'
            ' under "Tasks: Manage Automatic Tasks" to change that, or start it each time with "Tasks: Run Task",'
            " Claude, once you have trusted the window (the Restricted Mode banner, Manage, Trust). " + trust_once
        )
    else:
        asked = (
            ""
            if auto == "on"
            else ", then choose Allow when VS Code asks about automatic tasks: once per workspace, in a notification"
            ' that goes quiet after a moment ("Tasks: Manage Automatic Tasks" offers the same choice later)'
        )
        first_time = (
            "The first time, it waits for you: trust the window (the Restricted Mode banner, Manage, Trust)"
            + asked
            + ". "
            + settings_note
            + trust_once
        )
    if not inputs.notes:
        notes_said = ""
    else:
        notes_said = (
            f'The session notes are at {inputs.notes_file}: "Tasks: Run Task", Session notes opens them.'
            if auto == "off"
            else f"The session notes open beside it each time ({inputs.notes_file})."
        )
        if not _notes_in_window(inputs):
            covers = f", or trust the window of {again}, which covers them" if below else ""
            notes_said += (
                " VS Code may ask once whether to open them, as a file outside this window's folder: Open"
                + covers
                + "."
            )
        notes_said += " editor_session_notes: false turns them off."
    github = " and to GitHub" if _github_said(inputs) else ""
    said = (
        f"VS Code is opening {inputs.target} on {vm} ({inputs.reach}).",
        "The first connection installs VS Code Server on the VM and takes a minute.",
        f"A terminal named Claude then starts claude in it, signing in to Claude{github} first if {vm} has not been. "
        + first_time
        if terminal
        else "",
        (
            '"Tasks: Run Task", Shell opens a shell in the same folder beside it.'
            if auto == "off"
            else "A shell in the same folder opens to its left."
        )
        if terminal
        else "",
        notes_said,
    )
    if inputs.session_id:
        moved = ", where just code moved it" if inputs.session_moved else ""
        session = (
            f"The Claude terminal resumes session {inputs.session_id} in {inputs.target}, on {inputs.session_branch}"
            f" at {inputs.session_commit[:12]}{moved}. If that window is open already, VS Code only brings it to the"
            ' front: reload it ("Developer: Reload Window") to resume the session.' + inputs.session_cautions
        )
    else:
        session = ""
    return said, (session,)


def plan_open(editor: Editor, inputs: OpenInputs) -> OpenPlan:
    vm = inputs.vm.name
    # The Claude terminal is the row's to give: a window that starts none (claude_terminal other than started) gets
    # the bare folder, and nothing is written for it, as code.yml's own gates decide from the same row.
    terminal = inputs.terminal and editor.claude_terminal == "started"
    if not common.pick_cli(editor, inputs.probe):
        return OpenPlan(
            editor=editor.key,
            refusals=(
                "`code` is not on this machine's PATH. In VS Code, run \"Shell Command: Install 'code' command in"
                ' PATH" from the Command Palette (macOS), or install VS Code; then run this again. The alias is in'
                f" place either way: ssh {vm}.",
            ),
        )
    auto = vscode_setting(inputs.probe.settings_text or "", "task.allowAutomaticTasks")
    writes: tuple[VmWrite, ...] = ()
    opens = inputs.target
    if terminal:
        # The helpers before the workspace file that runs them: one written first, the copy then failing, would run a
        # file that is not there.
        workspace = _workspace_file(inputs)
        writes = (VmDirectory(kind="directory", path=common.HELPERS),)
        if posixpath.dirname(workspace):
            writes += (VmDirectory(kind="directory", path=posixpath.dirname(workspace)),)
        writes += (
            VmFile(kind="file", path=workspace, mode="0644", content=common.nice_json(_workspace(inputs)) + "\n"),
        )
        # VS Code opens a path ending in .code-workspace as a workspace, not as a file to edit.
        opens = posixpath.join(inputs.files_dir, workspace)
    report, end = _said(inputs, auto, terminal, common.just_code(inputs))
    return OpenPlan(
        editor=editor.key,
        vm_writes=writes,
        helpers=HELPERS if terminal else (),
        opens=opens,
        report=report,
        report_end=end,
        next_steps=(NextStep(vm=inputs.vm, matter="editor", action=None),),
    )
