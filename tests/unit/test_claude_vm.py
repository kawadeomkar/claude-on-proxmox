"""lib/claude_vm, the controller's Python as one package: what it holds, what importing it costs, its records, and the
adapters' one way in (docs/architecture/custom-code.md §7.10)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from harness import repo_files

from claude_vm import plugin, wire
from claude_vm.records import Checked, plain
from claude_vm.refs import Action, NextStep, VmRef, command_line

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "lib"
# Loaded only inside the function that needs one: each is milliseconds a client that is not Ansible would pay.
NEVER_AT_IMPORT = ("ansible", "yaml", "asyncio", "subprocess")
# What a module may load when imported all the same: the process table is what proc.py is for, and a child per host
# what status/hosts.py is for, which just status's engine imports only for --hosts. The MCP server's modules, which
# tools/mcp alone imports, are a process of its own whose work is to run just, over asyncio.
AT_IMPORT = {"claude_vm.proc": {"subprocess"}, "claude_vm.status.hosts": {"subprocess"}}
MCP_AT_IMPORT = {"asyncio", "subprocess"}


def _python(script: str) -> str:
    """What script prints, run by this Python isolated (-I): none of this process's modules or path, but what it
    puts on its path itself."""
    return subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, check=True).stdout


def _tracked(path: str) -> list[str]:
    return [str(file.relative_to(REPO)) for file in repo_files(path)]


def _core_modules() -> list[str]:
    names = []
    for path in sorted((LIB / "claude_vm").rglob("*.py")):
        parts = path.relative_to(LIB).with_suffix("").parts
        if "ansible" in parts[1:-1] or parts[1:2] == ("ansible",):
            continue  # the one subpackage that may import ansible
        names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


def test_lib_holds_the_one_package_claude_vm() -> None:
    # lib/ goes first on sys.path in every adapter's process: anything else there could shadow a module.
    assert _tracked("lib"), "lib/ holds nothing tracked"
    assert {Path(path).parts[1] for path in _tracked("lib")} == {"claude_vm"}


def test_what_runs_on_a_vm_never_imports_the_package() -> None:
    # A module goes to the VM with only what it imports from module_utils, and a helper runs there as plain python3 -I:
    # an import of claude_vm works here, where lib/ is on the path, and fails on every VM. They are handed data.
    vm_side = [
        path
        for path in _tracked("playbooks/library")
        + _tracked("playbooks/module_utils")
        + _tracked("playbooks/files")
        + _tracked("roles")
        if path.startswith("playbooks/") or "/library/" in path
    ]
    assert len(vm_side) >= 9, vm_side
    for path in vm_side:
        assert not re.search(r"^\s*(from|import)\s+claude_vm\b", (REPO / path).read_text(), re.M), path


@pytest.mark.parametrize("name", _core_modules())
def test_no_core_module_loads_ansible_yaml_asyncio_or_subprocess_when_imported(name) -> None:
    # Each in an interpreter of its own, so one module's imports never hide another's.
    script = f"""
import importlib, json, sys
sys.path.insert(0, {str(LIB)!r})
importlib.import_module({name!r})
loaded = {{key.split(".")[0] for key in sys.modules}}
print(json.dumps(sorted(m for m in {NEVER_AT_IMPORT!r} if m in loaded)))
"""
    allowed = MCP_AT_IMPORT if name.startswith("claude_vm.mcp.") else set()
    assert set(json.loads(_python(script))) <= AT_IMPORT.get(name, allowed)


# Modules that read without Ansible anywhere, not only when imported: the vault opened natively, which the hosts' check
# and just status's snapshot read through, would otherwise pay Ansible's import (159 ms on a homelab runner), and the
# hosts core, which tools/host runs before every recipe that has more than one host.
HOSTS_CORE = sorted(str(path.relative_to(LIB)) for path in (LIB / "claude_vm" / "hosts").glob("*.py"))
NO_ANSIBLE = ("claude_vm/vault.py", *HOSTS_CORE)
# What a module would call to write, rename or delete a file, or make a folder.
WRITES = re.compile(
    r"\.(write_text|write_bytes|mkdir|unlink|rmdir|touch|chmod)\(|\bos\.(replace|rename|remove|open|makedirs|chmod)\("
    r"|\brmtree\(|\bopen\([^)]*[\"'][wax]|\bstore\b"
)


@pytest.mark.parametrize("path", NO_ANSIBLE)
def test_a_module_that_reads_without_ansible_imports_none_of_it_anywhere(path) -> None:
    import ast

    tree = ast.parse((LIB / path).read_text())
    imported = [
        name
        for node in ast.walk(tree)
        for name in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else []
        )
    ]
    assert not [name for name in imported if name.split(".")[0] == "ansible"], imported


@pytest.mark.parametrize("path", HOSTS_CORE)
def test_the_hosts_core_decides_and_writes_nothing(path) -> None:
    # tools/host, its one adapter, performs every write the core plans: hosts/current, .checked.json, a host's files.
    assert [line for line in (LIB / path).read_text().splitlines() if WRITES.search(line)] == []


def test_the_mcp_server_imports_nothing_of_the_hosts() -> None:
    # A long-lived process, whose restart notice a pull that changes only lib/ may not trip: it asks just host --json.
    mcp = sorted((LIB / "claude_vm" / "mcp").glob("*.py"))
    assert (
        mcp
        and [path.name for path in mcp if re.search(r"\bclaude_vm\.hosts\b|from \.\.hosts\b", path.read_text())] == []
    )


def test_importing_the_package_imports_no_module_of_it() -> None:
    script = (
        f"import sys; sys.path.insert(0, {str(LIB)!r}); import claude_vm; "
        "print(sorted(m for m in sys.modules if m.startswith('claude_vm')))"
    )
    assert _python(script).strip() == "['claude_vm']"


@dataclass(frozen=True, slots=True, kw_only=True)
class _Sample(Checked):
    name: str
    count: int
    on: bool
    kind: Literal["a", "b"]
    names: tuple[str, ...]
    pair: tuple[str, int]
    either: int | None = None


GOOD = {"name": "x", "count": 1, "on": True, "kind": "a", "names": ("p", "q"), "pair": ("r", 2)}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", 1),
        ("count", True),  # True is no int
        ("count", "1"),
        ("on", 1),
        ("kind", "c"),
        ("names", ["p", "q"]),  # a list, not the tuple plain() makes
        ("names", ("p", 1)),
        ("pair", ("r",)),
        ("pair", ("r", "2")),
        ("either", "1"),
    ],
)
def test_a_record_refuses_a_field_its_annotation_cannot_hold(field, value) -> None:
    with pytest.raises(ValueError, match=rf"_Sample\.{field} cannot be"):
        _Sample(**(GOOD | {field: value}))


def test_a_record_takes_what_its_annotations_hold() -> None:
    assert _Sample(**GOOD).names == ("p", "q")
    assert _Sample(**GOOD, either=3).either == 3


def test_plain_copies_a_value_out_into_exact_types() -> None:
    class Tagged(str):
        pass

    copied = plain({Tagged("k"): [Tagged("v"), 1, True, None, {"n": (2,)}]}, "inputs")
    assert copied == {"k": ("v", 1, True, None, {"n": (2,)})}
    key, value = next(iter(copied.items()))
    assert type(key) is str and type(value[0]) is str


def test_plain_refuses_a_value_no_field_takes() -> None:
    with pytest.raises(ValueError, match=r"inputs\.vm\[0\] is a float"):
        plain({"vm": [1.5]}, "inputs")


@pytest.mark.parametrize(
    ("name", "vmid"), [("alpha", 99), ("alpha", 1_000_000_000), ("-alpha", 4013), ("al pha", 4013), ("", 4013)]
)
def test_a_vm_ref_refuses_a_name_just_would_not_take_or_an_id_proxmox_would_not_give(name, vmid) -> None:
    with pytest.raises(ValueError):
        VmRef(name=name, vmid=vmid)


def test_an_action_is_one_line_as_a_person_types_it() -> None:
    action = Action(recipe="code", args=("alpha", "--project", "my repo"))
    assert action.line == command_line(action) == "just code alpha --project 'my repo'"
    with pytest.raises(ValueError):
        Action(recipe="code", args=("alpha\n--x",))
    with pytest.raises(ValueError):
        Action(recipe="Code")


def test_to_wire_puts_format_version_and_kind_first_and_derived_values_after_the_fields() -> None:
    step = NextStep(vm=VmRef(name="alpha", vmid=4013), matter="editor", action=Action(recipe="code", args=("alpha",)))
    record = wire.to_wire(step)
    assert list(record)[:2] == ["format_version", "kind"]
    assert record == {
        "format_version": wire.FORMAT_VERSION,
        "kind": "next_step",
        "vm": {"name": "alpha", "vmid": 4013},
        "matter": "editor",
        "action": {"recipe": "code", "args": ["alpha"], "line": "just code alpha"},
        "text": "",
    }
    assert wire.to_wire(NextStep(vm=step.vm, matter="vm", action=None))["action"] is None


def test_a_model_imports_nothing_of_its_drawing() -> None:
    # The run's and just status's models are data; tui/ draws them, from public names. A second renderer (the events
    # of phase 3) then needs the model alone.
    lib = REPO / "lib" / "claude_vm"
    for model in ("run/model.py", "status/model.py"):
        source = (lib / model).read_text()
        assert not re.search(r"^from \.\.tui|^from claude_vm\.tui|^import claude_vm\.tui", source, re.M), model
    drawing = (lib / "tui" / "status.py").read_text()
    assert not re.search(r"from \.\.status\.model import [^\n]*\b_[a-z]", drawing), "a private name of the model"


def test_a_vm_name_is_matched_whole_wherever_the_core_checks_one() -> None:
    # refs.NAME has no anchors of its own: re.match would take any name that only starts like one.
    lib = REPO / "lib" / "claude_vm"
    partial = [
        str(path.relative_to(lib))
        for path in lib.rglob("*.py")
        if re.search(r"\bNAME\.(match|search)\(", path.read_text())
    ]
    assert partial == []


def test_to_wire_refuses_what_is_no_record() -> None:
    with pytest.raises(TypeError, match="no record"):
        wire.to_wire(object())


def test_an_adapter_s_filter_imports_its_module_only_when_called() -> None:
    script = f"""
import sys
sys.path.insert(0, {str(LIB)!r})
from claude_vm import plugin
made = plugin.filters({str(LIB)!r}, "wire", ["to_wire"])
before = "claude_vm.wire" in sys.modules
from claude_vm.refs import VmRef
made["to_wire"](VmRef(name="alpha", vmid=4013))
print(before, "claude_vm.wire" in sys.modules, made["to_wire"].__name__)
"""
    assert _python(script).split() == ["False", "True", "to_wire"]


def test_an_adapter_refuses_another_checkout_s_claude_vm(tmp_path) -> None:
    other = tmp_path / "lib"
    other.mkdir()
    with pytest.raises(ImportError, match="not this checkout's"):
        plugin.filters(str(other), "wire", ["to_wire"])["to_wire"](VmRef(name="alpha", vmid=4013))


def test_an_adapter_knows_its_own_lib_by_file_not_by_spelling(tmp_path) -> None:
    link = tmp_path / "lib"
    os.symlink(LIB, link)
    plugin.own(str(link))  # the same directory, spelled another way


ADAPTERS = sorted((REPO / "filter_plugins").glob("*.py"))


@pytest.mark.parametrize("path", ADAPTERS, ids=lambda path: path.name)
def test_every_adapter_loads_no_core_module_and_each_filter_is_the_core_s_of_its_name(path) -> None:
    # Every worker executes every file in filter_plugins/ at its first filter lookup: an adapter loads nothing of the
    # core there, and each filter it names calls the core's function of that name when it is called.
    script = f"""
import importlib, importlib.util, inspect, json, sys
spec = importlib.util.spec_from_file_location("adapter", {str(path)!r})
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
# After filters() too, which a worker calls at its first lookup: the plugin module it uses, and nothing of the core.
filters = adapter.FilterModule().filters()
loaded = sorted(m for m in sys.modules if m.split(".")[0] == "claude_vm" and m not in ("claude_vm", "claude_vm.plugin"))
found = {{}}
for name, call in filters.items():
    where = inspect.getclosurevars(call).nonlocals
    core = getattr(importlib.import_module("claude_vm." + where["module"]), where["name"], None)
    found[name] = [call.__name__, where["name"], callable(core)]
print(json.dumps({{"loaded": loaded, "found": found}}))
"""
    said = json.loads(_python(script))
    assert said["loaded"] == []
    assert said["found"] and all(made == [name, name, True] for name, made in said["found"].items()), said["found"]


def test_two_writers_through_the_store_lose_none_of_each_other_s_changes(tmp_path) -> None:
    # Read, change, replace, 300 times in each of two processes at once, as tools/settings writes local.yml:
    # unlocked, about half were lost.
    counter = tmp_path / "counter"
    counter.write_text("0")
    script = f"""
import sys
sys.path.insert(0, {str(LIB)!r})
from pathlib import Path
from claude_vm import store
for _ in range(300):
    with store.locked({str(counter)!r}):
        store.replace({str(counter)!r}, str(int(Path({str(counter)!r}).read_text()) + 1))
"""
    writers = [subprocess.Popen([sys.executable, "-I", "-c", script]) for _ in range(2)]
    assert [writer.wait(timeout=120) for writer in writers] == [0, 0]
    assert counter.read_text() == "600"


def test_a_replace_keeps_the_file_s_mode_and_leaves_no_scratch_file(tmp_path) -> None:
    from claude_vm import store

    path = tmp_path / "local.yml"
    path.write_text("a: 1\n")
    path.chmod(0o640)
    store.replace(path, "a: 2\n")
    assert path.read_text() == "a: 2\n" and (path.stat().st_mode & 0o777) == 0o640
    assert sorted(p.name for p in tmp_path.iterdir()) == ["local.yml"]


def test_a_replace_makes_a_new_file_only_with_the_mode_it_is_given(tmp_path) -> None:
    from claude_vm import store

    path = tmp_path / "current"
    with pytest.raises(FileNotFoundError):
        store.replace(path, "nuc\n")
    store.replace(path, "nuc\n", mode_if_new=0o600)
    assert path.read_text() == "nuc\n" and (path.stat().st_mode & 0o777) == 0o600
    path.chmod(0o640)
    store.replace(path, "pve\n", mode_if_new=0o600)
    assert (path.stat().st_mode & 0o777) == 0o640


def test_a_replace_through_a_symlink_replaces_the_file_it_names(tmp_path) -> None:
    from claude_vm import store

    (tmp_path / "kept").mkdir()
    target = tmp_path / "kept" / "local.yml"
    target.write_text("a: 1\n")
    link = tmp_path / "local.yml"
    link.symlink_to(target)
    store.replace(link, "a: 2\n")
    assert link.is_symlink() and target.read_text() == "a: 2\n"
    assert sorted(p.name for p in (tmp_path / "kept").iterdir()) == ["local.yml"]
