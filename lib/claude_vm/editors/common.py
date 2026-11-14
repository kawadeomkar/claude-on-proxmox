"""What more than one editor family does: the helpers' directory, the ownership marker, the command picked here, and
whether one path on the VM lies inside another."""

from __future__ import annotations

import json
import posixpath
import re
from typing import Final

from .model import Editor, OpenInputs, Probe, vm_path

# The helpers' directory in just code's own on the VM: code_helpers_dir, a promised name.
HELPERS: Final = "helpers"
# The first line of every file in JSON with comments just code writes for an editor to read: one there without it is
# someone else's, and left as it is. Written on VMs, so it stays (tests/unit/test_persisted_names.py).
MARKER: Final = "// Written by just code (agent-warehouse); edits here are overwritten."
# The same, as 1.x wrote it: a file that starts with it is just code's too, and is written again with MARKER.
MARKER_1X: Final = "// Written by just code (claude-on-proxmox); edits here are overwritten."
# The folders of just code's directory that YAML writes itself, the session copies and the notes: never a plan's.
KEPT: Final = ("claude-sessions", "session")
# What a helper's path may hold to be handed to an editor's task as it is, an argument no editor quotes for a shell.
HELPER_PATH: Final = re.compile(r"/[A-Za-z0-9._/-]+")


def pick_cli(editor: Editor, probe: Probe) -> str:
    """The first command which found with one of the row's names whose --version says what the row asks."""
    said = dict(probe.versions)
    for path in probe.which:
        if posixpath.isabs(path) and posixpath.basename(path) in editor.cli_candidates:
            # A row that asks no version takes its command as which found it.
            if not editor.version_re or (path in said and re.match(editor.version_re, said[path])):
                return path
    return ""


def below(path: str, top: str) -> bool:
    """Whether path lies strictly inside top on the VM, both normalised as the plan compares paths (vm_path): a "//" or
    a "." in a setting decides nothing. A path that is no absolute path, a setting left empty, is inside nothing."""
    try:
        path, top = vm_path(path, "path"), vm_path(top, "top")
    except ValueError:
        return False
    return path != top and posixpath.commonpath([top, path]) == top


def just_code(inputs: OpenInputs) -> str:
    """The just code line that opens the projects directory's window again: the VM's name, then code.yml's flag,
    which alone decides where a bare line, opening the setting's editor, is not this run's, and the VM's host where
    there is more than one."""
    return f"just code {inputs.vm.name}{inputs.editor_flag}{inputs.host_flag}"


def nice_json(value: object) -> str:
    """to_nice_json(indent=2)'s text, byte for byte: what the YAML that wrote these files wrote."""
    return json.dumps(value, indent=2, sort_keys=True, separators=(",", ": "))
