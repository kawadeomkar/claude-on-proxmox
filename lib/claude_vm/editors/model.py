"""The editors' records: a row of claude_editors, what a family's check found here, what a window is opened with, and
the plan of it a family returns. Stdlib only; every record holds its fields to their annotations as it is built
(docs/architecture/custom-code.md §7.10)."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Literal

from ..catalogue import rows_from
from ..records import Checked, plain
from ..refs import NextStep, VmRef

FamilyName = Literal["vscode", "zed"]  # one value per family module
ClaudeTerminal = Literal["started", "listed", "none"]  # run by the window / offered by it / neither
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # an editor's key, a command's name, a helper's


def vm_path(value: str, what: str) -> str:
    """An absolute path on the VM, normalised for comparing: a plan compares paths this way and writes them, and
    launches them, exactly as code.yml built them, so a "//" or a "." in a setting changes nothing it did."""
    if not value.startswith("/") or {"\0", "\n"} & set(value):
        raise ValueError(f"{what} is not an absolute path: {value!r}")
    return "/" + posixpath.normpath(value).lstrip("/")


def relative(path: str, mode: str) -> None:
    """A name under just code's directory, and an octal mode."""
    if not path or {"", ".", ".."} & set(path.split("/")) or {"\0", "\n"} & set(path):
        raise ValueError(f"{path!r} is not a name under just code's directory")
    if not re.fullmatch(r"0[0-7]{3}", mode):
        raise ValueError(f"{path}: {mode!r} is not an octal mode")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessMatch(Checked):
    """How just status knows one of the editor's windows on the VM: a process of the user's whose argv holds every
    field given. The status probe is sent these, since what runs on the VM cannot import the core."""

    argv0_contains: str = ""  # in the program's path
    argv0_name_prefix: str = ""  # the start of the program's own name
    argv1: str = ""  # its first argument, whole
    argv_has: tuple[str, ...] = ()  # arguments it has, anywhere

    def validate(self) -> None:
        # A match on the arguments alone would count any of the user's programs that takes them.
        if (self.argv1 or self.argv_has) and not (self.argv0_contains or self.argv0_name_prefix):
            raise ValueError("a window's process is matched by its program first, then by its arguments")


@dataclass(frozen=True, slots=True, kw_only=True)
class Editor(Checked):
    """One row of the inventory defaults' claude_editors; a fork of an editor is a row of its family, with a check file
    of its own and its key in just code's --editor."""

    key: str
    name: str
    family: FamilyName
    claude_terminal: ClaudeTerminal = "none"  # a row without it: neither
    window_tasks: bool
    cli_candidates: tuple[str, ...]
    version_re: str = ""
    server: ProcessMatch = ProcessMatch()  # none given: no window of it is counted

    def validate(self) -> None:
        if not self.cli_candidates or not all(map(WORD.fullmatch, (self.key, *self.cli_candidates))):
            raise ValueError(f"editor {self.key!r}: its key and command names are plain words")


@dataclass(frozen=True, slots=True, kw_only=True)
class Probe(Checked):
    """What the editor's check tasks read, here and on the VM: read by YAML, decided by the plan."""

    which: tuple[str, ...] = ()  # which's lines
    versions: tuple[tuple[str, str], ...] = ()  # (command, what its --version printed), where that exited 0
    settings_file: str = ""  # the user's editor settings, read and never written: where they are
    settings_exists: bool = False
    settings_text: str | None = ""  # None where the file could not be read
    tasks_head: str | None = None  # the first line of the editor's tasks file in just code's directory, if there


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenInputs(Checked):
    """What a window is opened with: grows by fields with defaults, never by new parameters."""

    vm: VmRef  # the VM by name and VM ID; its name is the SSH alias
    target: str  # the folder to open on the VM
    root: str  # the projects directory
    files_dir: str  # just code's own directory on the VM
    project: str = ""  # --project's folder, or the whole directory's window
    reach: str = ""  # where the VM is reached, as the report says it
    terminal: bool
    notes: bool
    notes_file: str = ""
    notes_dir: str = ""
    task_written: bool = False  # the editors list's task file goes into just code's directory (code.yml's)
    editor_flag: str = ""  # code.yml's code_editor_choice: " --editor <key>" where the editor is not the setting's
    host_flag: str = ""  # claude_host_flag: " --host <name>" where the checkout has more than one Proxmox host
    session_id: str = ""
    session_branch: str = ""
    session_commit: str = ""
    session_moved: bool = False
    session_cautions: str = ""
    helpers: tuple[str, ...] = ()  # what YAML's helper lists hold: the only names a plan may copy
    probe: Probe = Probe()
    # What the Claude terminal is told of the VM's other sign-ins and services.
    remote_control: bool = True
    github_signed_in: bool = True
    clone_protocol: str = "https"
    login_scopes: str = "workflow"
    remote_control_unit: str = "claude-remote-control"
    timezone: str = "UTC"

    def validate(self) -> None:
        if self.editor_flag and not re.fullmatch(rf" --editor {WORD.pattern}", self.editor_flag):
            raise ValueError(f"not an --editor flag: {self.editor_flag!r}")
        if self.host_flag and not re.fullmatch(r" --host [a-z0-9][a-z0-9-]{0,30}", self.host_flag):
            raise ValueError(f"not a --host flag: {self.host_flag!r}")
        if not all(map(WORD.fullmatch, self.helpers)):
            raise ValueError(f"not a plain name among the helpers: {self.helpers!r}")
        target, root = vm_path(self.target, "target"), vm_path(self.root, "root")
        if posixpath.commonpath([root, target]) != root:
            raise ValueError(f"{self.target} is not in {self.root}")
        # just code's own directory is held to its rules only where something is written in it, as code.yml holds it:
        # with the terminal and the notes off, a window opens whatever the setting names.
        if self.terminal or self.notes or self.task_written:
            files_dir = vm_path(self.files_dir, "files_dir")
            if files_dir in ("/", root) or (self.terminal and files_dir == target):
                raise ValueError(f"files_dir cannot be {self.files_dir}")


@dataclass(frozen=True, slots=True, kw_only=True)
class VmDirectory(Checked):
    kind: Literal["directory"]
    path: str  # under just code's directory, which YAML joins it to
    mode_if_new: str = "0755"  # one that exists keeps its mode; the stat stays YAML

    def validate(self) -> None:
        relative(self.path, self.mode_if_new)


@dataclass(frozen=True, slots=True, kw_only=True)
class VmFile(Checked):
    kind: Literal["file"]
    path: str  # under just code's directory
    mode: str
    content: str  # the whole text; a helper goes by name, never here

    def validate(self) -> None:
        relative(self.path, self.mode)


VmWrite = VmDirectory | VmFile


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenPlan(Checked):
    """A window, as data: what it refuses, writes on the VM, copies there, opens and says. YAML applies it."""

    editor: str
    refusals: tuple[str, ...] = ()  # beside a refusal the registry keeps nothing else
    vm_writes: tuple[VmWrite, ...] = ()
    helpers: tuple[str, ...] = ()  # files of playbooks/files/ for helpers/, by name: YAML copies them
    opens: str = ""  # the folder, or one of vm_writes' files, that the window opens
    launch: tuple[str, ...] = ()  # the command run here: the registry's, never a family's
    report: tuple[str, ...] = ()  # said, then what YAML says of the session copies
    report_end: tuple[str, ...] = ()  # said after that
    claude_task: bool = False  # a Claude task of just code's is in the editor's list once the window opens
    next_steps: tuple[NextStep, ...] = ()


def editors_from(raw: object) -> dict[str, Editor]:
    """The inventory defaults' claude_editors, as the registry's filters are handed it: parsed on every call."""
    return rows_from(
        raw,
        "claude_editors",
        lambda key, row: Editor(key=key, **(row | {"server": ProcessMatch(**row.get("server", {}))})),
    )


def inputs_from(raw: object) -> OpenInputs:
    """code.yml's code_open_inputs, copied out of Ansible, its paths as code.yml built them."""
    fields = plain(raw, "inputs")
    records = {"vm": VmRef(**fields.get("vm", {})), "probe": Probe(**fields.get("probe", {}))}
    return OpenInputs(**(fields | records))
