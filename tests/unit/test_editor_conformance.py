"""The editors' plans in the core (lib/claude_vm/editors/): every family conforms to the seam, every family's real plans
pass the registry, each rule of the registry bites on a plan built to break it, a refusal leaves nothing else, and the
filter takes Ansible's own values (docs/architecture/custom-code.md §7.10). The registry runs its rules on every call;
this shows they hold and that they bite."""

from __future__ import annotations

import inspect
import json
import re
import typing
from pathlib import Path

import pytest
import yaml
from harness import EDITOR_FILES_DIR as FD
from harness import EDITOR_PROBE as FOUND
from harness import EDITOR_ROOT as ROOT
from harness import VSCODE_CLI as CODE
from harness import ZEDITOR as ZED
from harness import _run
from harness import editor_inputs as given

from claude_vm.editors import common, model, registry
from claude_vm.editors.family import Family
from claude_vm.editors.model import FamilyName, OpenPlan, VmDirectory, VmFile
from claude_vm.refs import Action, NextStep, VmRef
from claude_vm.wire import to_wire

REPO = Path(__file__).resolve().parents[2]
ROWS = yaml.safe_load((REPO / "inventory/group_vars/all/defaults.yml").read_text())["claude_editors"]
# The inputs, as code.yml gives them: given(), harness.editor_inputs, ZFS's zed first on PATH (FOUND).


WINDOW_CASES = [
    given(),
    given(target=f"{ROOT}/bot", project="bot"),
    given(files_dir="/home/dev/.just-code", task_written=False),
    given(session_id="0" * 8 + "-0000-0000-0000-" + "0" * 12, session_branch="main", session_commit="a" * 40),
    given(terminal=False, task_written=False),
]
REFUSED = [
    ("zed", given(probe={"which": ["/usr/sbin/zed"]})),
    ("vscode", given(probe={})),
    ("zed", given(target=f"{ROOT}/my repo", project="my repo")),
]
SLIPS = [
    {"helpers": ("vault.yml",)},
    {"helpers": ("claude-start.sh",)},  # without its directory
    {"launch": ("/bin/sh",)},
    {"opens": "/etc"},
    {"next_steps": (NextStep(vm=VmRef(name="alpha", vmid=4014), matter="editor", action=None),)},  # another VM
    # A helper the YAML does not list, its directory made: the rule on the names alone, no other rule beside it.
    {"helpers": ("vault.yml",), "vm_writes": (VmDirectory(kind="directory", path="helpers"),)},
    # just code's own folders, which YAML writes: the session copies' manifest, the notes', a helper's file.
    {"vm_writes": (VmFile(kind="file", path="claude-sessions/manifest.json", mode="0600", content="{}"),)},
    {"vm_writes": (VmDirectory(kind="directory", path="session"),)},
    {
        "vm_writes": (
            VmDirectory(kind="directory", path="helpers"),
            VmFile(kind="file", path="helpers/claude-start.sh", mode="0644", content=""),
        )
    },
]
OUTSIDE = ["/etc/hosts", "../", "../repo/.zed/tasks.json", f"{FD}/../repo/x", "a/../../x", ""]
BAD_INPUTS = [
    {"files_dir": "/"},
    {"files_dir": ROOT},
    {"terminal": "false"},
    {"editor_flag": "--editor zed"},  # code_editor_choice's own spelling only: a space, then --editor and a key
    {"editor_flag": " --editor zed --host nuc"},  # the host is a flag of its own
    {"host_flag": "--host nuc"},  # claude_host_flag's spelling only: a space, then --host and a host's name
    {"host_flag": " --host nuc; rm -rf ~"},
    {"target": "/etc"},
    {"colour": "red"},
    {"vm": "alpha"},  # a bare name is no VmRef
]


def conforms(module: object, protocol: type) -> list[str]:
    """The members a type checker would refuse: missing, or another shape of parameters or return."""
    members = (
        typing.get_protocol_members(protocol)
        if hasattr(typing, "get_protocol_members")
        else protocol.__protocol_attrs__
    )

    def shape(fn, drop):
        params = list(inspect.signature(fn).parameters.values())[drop:]
        return [(p.name, p.kind) for p in params]

    return [
        m
        for m in sorted(members)
        if not callable(getattr(module, m, None)) or shape(getattr(protocol, m), 1) != shape(getattr(module, m), 0)
    ]


def test_every_family_conforms_and_every_row_parses():
    assert set(registry.FAMILIES) == set(typing.get_args(FamilyName))
    assert [name for name, module in registry.FAMILIES.items() if conforms(module, Family)] == []
    assert {row.family for row in model.editors_from(ROWS).values()} <= set(registry.FAMILIES)


@pytest.mark.parametrize("key", sorted(ROWS))
@pytest.mark.parametrize("case", WINDOW_CASES, ids=range(len(WINDOW_CASES)))
def test_every_family_s_plans_pass_the_core(key, case):
    plan = registry.editor_open_plan(key, ROWS, case)
    assert not plan["refusals"] and plan["launch"][0] in FOUND["which"][1:]
    assert plan["kind"] == "open_plan" and plan["next_steps"][0]["vm"] == case["vm"]
    # Every write tied to its switch: none without the terminal, or for Zed where no task is written.
    wrote = bool(plan["vm_writes"] or plan["helpers"])
    assert wrote == (case["terminal"] and (key == "vscode" or case["task_written"]))


@pytest.mark.parametrize(("key", "case"), REFUSED, ids=["zfs alone", "no code", "a folder no URL carries"])
def test_a_refusal_leaves_nothing_but_itself(key, case):
    plan = registry.editor_open_plan(key, ROWS, case)
    assert plan["refusals"]
    assert not any(plan[name] for name in ("vm_writes", "helpers", "opens", "launch", "report", "next_steps"))


def test_zfs_s_zed_is_never_the_command_run():
    plan = registry.editor_open_plan("zed", ROWS, given())
    assert plan["launch"] == [ZED, f"ssh://alpha{ROOT}"]


# Zed's task file, marked and written as to_nice_json writes it: test_code_session.py's
# test_zed_s_claude_task_is_fixed_text_in_just_code_s_own_directory, of the same plan, with the whole task.


def test_a_foreign_task_file_is_left_and_said():
    plan = registry.editor_open_plan("zed", ROWS, given(probe=FOUND | {"tasks_head": '[{"label": "build"}]'}))
    assert [w["path"] for w in plan["vm_writes"]] == ["helpers"]
    assert any("is not just code's, so it was left as it is" in line for line in plan["report"])


def test_with_nothing_written_the_window_opens_whatever_files_dir_names():
    # code.yml holds editor_files_dir to its rules only where something goes into it, and so does the core.
    for files_dir in ("/", ROOT, "relative"):
        for key in ROWS:
            plan = registry.editor_open_plan(
                key, ROWS, given(files_dir=files_dir, terminal=False, notes=False, task_written=False)
            )
            assert not plan["refusals"] and not plan["vm_writes"] and plan["launch"], (key, files_dir)


def test_the_paths_are_written_and_launched_as_code_yml_built_them():
    # A "//" or a "." in a setting reaches the window as it did before the plan: compared normalised, never rewritten.
    root, fd = "/home/dev//projects", "/home/dev//projects/./.agent-warehouse"
    notes = {"notes_file": f"{fd}/session/alpha-session.md", "notes_dir": f"{fd}/session"}
    plan = registry.editor_open_plan("vscode", ROWS, given(target=root, root=root, files_dir=fd, **notes))
    assert plan["launch"][-1] == f"{fd}/projects.code-workspace"
    (workspace,) = [w for w in plan["vm_writes"] if w["kind"] == "file"]
    assert [folder["path"] for folder in json.loads(workspace["content"])["folders"]] == [root, f"{fd}/session"]


def test_zed_writes_no_task_where_its_helper_s_path_could_not_be_handed_as_it_is():
    # Zed joins a task's args into one bash -i -c command: the core keeps such a path out, whatever the inputs say.
    fd = f"{ROOT}/claude files"
    plan = registry.editor_open_plan("zed", ROWS, given(files_dir=fd, notes_file=f"{fd}/n.md", notes_dir=f"{fd}/n"))
    assert plan["vm_writes"] == [] and plan["helpers"] == [] and not plan["claude_task"]
    assert any("No Claude task was written" in line for line in plan["report"])


def test_a_vs_code_row_whose_window_starts_no_claude_terminal_writes_nothing():
    # The Claude terminal is the row's to give, as code.yml's gates read it: claude_terminal other than started.
    rows = ROWS | {"vscode": ROWS["vscode"] | {"claude_terminal": "none"}}
    plan = registry.editor_open_plan("vscode", rows, given())
    assert plan["vm_writes"] == [] and plan["helpers"] == [] and plan["launch"][-1] == ROOT
    assert not any("A terminal named Claude" in line for line in plan["report"])


def test_vs_code_opens_its_workspace_or_the_folder_over_the_alias():
    plan = registry.editor_open_plan("vscode", ROWS, given())
    assert plan["launch"] == [CODE, "--remote", "ssh-remote+alpha", f"{FD}/projects.code-workspace"]
    bare = registry.editor_open_plan("vscode", ROWS, given(terminal=False))
    assert bare["launch"] == [CODE, "--remote", "ssh-remote+alpha", ROOT]


@pytest.mark.parametrize("other", [False, True], ids=["the setting's editor", "--editor's"])
def test_the_just_code_line_a_plan_says_names_the_editor_a_run_chose_against_the_setting(other):
    # A bare just code opens the setting's editor: where --editor chose another, code.yml's code_editor_flag
    # (editor_flag) follows the name in the line that opens the projects directory's window again, as in the notes.
    def report(key):
        one = given(target=f"{ROOT}/bot", project="bot", editor_flag=f" --editor {key}" if other else "")
        return " ".join(registry.editor_open_plan(key, ROWS, one)["report"])

    zed, vscode = report("zed"), report("vscode")
    assert f"The window of just code alpha{' --editor zed' if other else ''} lists a task," in zed, zed
    assert f"Trusting the window of just code alpha{' --editor vscode' if other else ''} instead" in vscode, vscode


def test_with_more_than_one_host_every_just_line_a_plan_says_or_writes_names_the_vms_host():
    # claude_host_flag (host_flag), after the --editor where there is one: the report's lines, and the Claude
    # terminal's, which VS Code's tasks file carries onto the VM.
    one = given(target=f"{ROOT}/bot", project="bot", editor_flag=" --editor zed", host_flag=" --host nuc")
    zed = " ".join(registry.editor_open_plan("zed", ROWS, one)["report"])
    assert "The window of just code alpha --editor zed --host nuc lists a task," in zed, zed
    plan = registry.editor_open_plan("vscode", ROWS, given(host_flag=" --host nuc"))
    written = json.dumps(plan)
    assert "just deploy alpha --tags claude_code --host nuc" in written, written
    assert "just deploy alpha --only configure --host nuc" in written, written
    for line in re.findall(r"just (?:deploy|login|code) alpha[^\"]*", written):
        assert " --host nuc" in line, line


def test_the_folders_no_plan_writes_are_the_ones_code_yml_writes_itself():
    code = (REPO / "playbooks" / "code.yml").read_text()
    assert common.KEPT == ("claude-sessions", "session") and all(f"/{name}" in code for name in common.KEPT)


@pytest.mark.parametrize("name", OUTSIDE)
def test_no_write_leaves_just_code_s_directory(name):
    with pytest.raises(ValueError):
        VmFile(kind="file", path=name, mode="0644", content="")
    with pytest.raises(ValueError):
        registry.inside(FD, name)


@pytest.mark.parametrize(
    ("path", "top", "inside"),
    [
        (f"{FD}/session", ROOT, True),
        ("/home/dev//projects/./.agent-warehouse", "/home/dev/projects/", True),  # as a setting may spell them
        (ROOT, ROOT, False),  # a folder is not inside itself
        (f"{ROOT}/.", ROOT, False),
        (f"{ROOT}-old/x", ROOT, False),  # a sibling whose name starts the same
        (f"{FD}/../../x", ROOT, False),
        ("relative", ROOT, False),  # a setting left relative or empty is inside nothing, never an error
        ("", ROOT, False),
    ],
)
def test_one_rule_says_whether_a_path_lies_inside_another(path, top, inside):
    # The families' Claude terminal and notes, and the registry's writes, all ask common.below.
    assert common.below(path, top) is inside


@pytest.mark.parametrize(
    "slip",
    SLIPS,
    ids=[
        "a helper not listed",
        "no helpers directory",
        "a launch",
        "opens /etc",
        "another VM",
        "a helper not listed, its directory made",
        "the sessions' manifest",
        "the notes' folder",
        "over a helper",
    ],
)
def test_the_core_refuses_a_family_s_slip(slip):
    editor, inputs = model.editors_from(ROWS)["zed"], model.inputs_from(given())
    with pytest.raises(ValueError):
        registry.checked(editor, inputs, OpenPlan(editor="zed", **({"opens": ROOT} | slip)))


def test_a_plan_s_helpers_need_their_directory_and_pass_with_it():
    editor, inputs = model.editors_from(ROWS)["zed"], model.inputs_from(given())
    made = VmDirectory(kind="directory", path="helpers")
    plan = registry.checked(
        editor, inputs, OpenPlan(editor="zed", opens=ROOT, helpers=("claude-start.sh",), vm_writes=(made,))
    )
    assert plan.launch == (ZED, f"ssh://alpha{ROOT}")


@pytest.mark.parametrize("bad", BAD_INPUTS, ids=lambda bad: next(iter(bad)))
def test_inputs_and_rows_are_held_to_their_types(bad):
    with pytest.raises((ValueError, TypeError)):
        model.inputs_from(given(**bad))
    with pytest.raises(ValueError):
        model.editors_from({"emacs": ROWS["zed"] | {"family": "emacs"}})


@pytest.mark.parametrize(
    "server", [{"argv1": "run"}, {"argv_has": ["--type=extensionHost"]}, {"argv0_contains": 1}, {"argv0": "zed"}]
)
def test_a_row_s_window_match_names_its_program_and_no_more(server):
    # A match on the arguments alone would count any of the user's programs that takes them (the status probe).
    with pytest.raises((ValueError, TypeError)):
        model.editors_from({"zed": ROWS["zed"] | {"server": server}})


def test_each_row_s_window_match_goes_to_the_module_as_its_fields_alone():
    assert [match["key"] for match in registry.editor_servers(ROWS)] == list(ROWS)
    assert registry.editor_servers({"x": ROWS["zed"] | {"server": {}}}) == [
        {"key": "x", "argv0_contains": "", "argv0_name_prefix": "", "argv1": "", "argv_has": []}
    ]


def test_the_filter_takes_ansible_s_own_values(tmp_path):
    # Lazy and tagged values, as code.yml hands them; the plan kept as a fact, as the window step keeps none. And the
    # apply's NEXT STEPS record, as tasks/editor_open.yml writes it, from steps with an action and without: the line
    # is the action's own, on the wire, and '' once nothing is left.
    inputs = given(terminal="{{ switch }}", target="{{ where }}", vm={"name": "alpha", "vmid": "{{ listed.vmid }}"})
    apply = yaml.safe_load((REPO / "playbooks/tasks/editor_open.yml").read_text())
    (record,) = [
        task["ansible.builtin.set_stats"]["data"]["code_next"] for task in apply if "ansible.builtin.set_stats" in task
    ]
    vm = VmRef(name="alpha", vmid=4013)
    steps = (
        NextStep(vm=vm, matter="editor", action=Action(recipe="code", args=("alpha", "--project", "my repo"))),
        NextStep(vm=vm, matter="window", action=None),
    )
    play = {
        "hosts": "localhost",
        "gather_facts": False,
        "vars": {
            "rows": ROWS,
            "inputs": inputs,
            "switch": True,
            "where": ROOT,
            "listed": {"vmid": 4013},
            "claude_vm_names": ["alpha"],
            "code_plan": {"next_steps": [to_wire(step) for step in steps]},
        },
        "tasks": [
            {"ansible.builtin.set_fact": {"kept": "{{ 'zed' | editor_open_plan(rows, inputs) }}"}},
            {"name": "Say", "ansible.builtin.debug": {"msg": "{{ [kept.kind] + kept.launch }}"}},
            {"name": "Servers", "ansible.builtin.debug": {"msg": "{{ rows | editor_servers | map(attribute='key') }}"}},
            {"name": "Next", "ansible.builtin.debug": {"msg": record}},
        ],
    }
    (tmp_path / "plan.yml").write_text(yaml.safe_dump([play]))
    report = _run(tmp_path, tmp_path / "plan.yml", {}, timeout=120)
    assert report["rc"] == 0, report["stderr"][-2000:]
    tasks = report["plays"][0]["tasks"]
    said = {task["task"]["name"]: task["hosts"]["localhost"].get("msg") for task in tasks}
    assert said["Say"] == ["open_plan", ZED, f"ssh://alpha{ROOT}"] and said["Servers"] == list(ROWS)
    assert said["Next"] == {"alpha": {"editor": "just code alpha --project 'my repo'", "window": ""}}
