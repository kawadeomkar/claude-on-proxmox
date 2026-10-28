"""Every `just` command drawn live, its declaration held to the playbooks it names (filter_plugins/live_recipes.py).

A declaration matches plays, blocks and tasks by name, and reads facts by name: a name that no longer exists matches
nothing, and the run is drawn without a word of it - a stage with no plays, a failure raised again drawn as a
failure of its own, a VM never marked. So every name a declaration gives is looked for here, in the playbook or the
task files it names, and every fact it reads in a task that sets it. The live tests that run a real playbook do so
as that playbook's own tests run it, with every path those pin (docs/architecture/custom-code.md §7.8).
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SET_FACT = {"set_fact", "ansible.builtin.set_fact", "ansible.legacy.set_fact"}
# What Ansible never starts as a task of its own: a step that starts at one never begins.
IMPORTS = {f"{prefix}import_{kind}" for prefix in ("", "ansible.builtin.") for kind in ("tasks", "role", "playbook")}


def _load(name):
    spec = importlib.util.spec_from_file_location(f"test_{name}", REPO / "filter_plugins" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recipes = _load("live_recipes")
RECIPES = sorted(recipes.RECIPES)


def _yaml_files():
    for top in ("playbooks", "roles"):
        yield from sorted((REPO / top).rglob("*.yml"))
    yield REPO / "deploy.yml"


def _items(node):
    """Every mapping in a playbook or task file, at any depth: each play, block and task."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _items(value)
    elif isinstance(node, list):
        for value in node:
            yield from _items(value)


def _load_yaml(path):
    try:
        return yaml.safe_load(path.read_text()) or []
    except yaml.YAMLError:
        return []


TASKS = [(path, item) for path in _yaml_files() for item in _items(_load_yaml(path))]


def _playbook(name):
    """A playbook a declaration names, by its file name: deploy.yml at the top, the others in playbooks/."""
    for path in (REPO / name, REPO / "playbooks" / name):
        if path.is_file():
            return path
    pytest.fail(f"no playbook {name}")


def _task_file(name):
    """A task file a declaration names, by its file name: under playbooks/ or a role's tasks/."""
    found = [path for path in _yaml_files() if path.name == name]
    assert found, f"no task file {name}"
    return found


def _names(path, kind=None):
    """The names of the plays, blocks or tasks of a file: kind "play", "block", or None for any."""
    out = set()
    for item in _items(_load_yaml(path)):
        if not isinstance(item.get("name"), str):
            continue
        if kind == "play" and "hosts" not in item and "import_playbook" not in item:
            continue
        if kind == "block" and "block" not in item:
            continue
        out.add(item["name"])
    return out


def _everywhere(kind=None):
    return set().union(*(_names(path, kind) for path in _yaml_files()))


@pytest.mark.parametrize("name", RECIPES)
def test_every_play_a_declaration_names_is_in_its_playbook(name):
    recipe = recipes.RECIPES[name]
    for stage in recipe.stages:
        for play in stage.plays:
            file, _, play_name = play.partition(":")
            path = _playbook(file)
            if play_name:
                assert play_name in _names(path, "play"), f"{name}: {stage.key}: no play {play_name!r} in {file}"
    for file in recipe.plan:
        _playbook(file)
    for file in getattr(recipe, "addresses_from", ()):
        assert (REPO / "playbooks" / file).is_file() or _task_file(file)


@pytest.mark.parametrize("name", RECIPES)
def test_every_block_and_task_a_declaration_names_is_one(name):
    recipe = recipes.RECIPES[name]
    blocks, tasks = _everywhere("block"), _everywhere()
    for stage in recipe.stages:
        for step in stage.steps:
            for block in getattr(step, "blocks", ()):
                assert block in blocks, f"{name}: {step.label}: no block {block!r}"
    for task, causes in recipe.raises.items():
        assert task in tasks, f"{name}: no task {task!r} to raise"
        for block in causes.blocks:
            assert block in blocks, f"{name}: {task}: no block {block!r} for its causes"
    for task in getattr(recipe, "declines", {}):
        assert task in tasks, f"{name}: no task {task!r} to decline"


@pytest.mark.parametrize("name", RECIPES)
def test_every_task_a_step_starts_at_is_one_ansible_starts_and_every_task_file_is_there(name):
    recipe = recipes.RECIPES[name]
    files = [path.as_posix() for path in _yaml_files()]
    for stage in recipe.stages:
        items = [item for play in stage.plays for item in _items(_load_yaml(_playbook(play.partition(":")[0])))]
        tasks = {item["name"]: item for item in items if isinstance(item.get("name"), str) and "block" not in item}
        for step in stage.steps:
            for task in getattr(step, "starts", ()):
                assert task in tasks, f"{name}: {step.label}: no task {task!r} in {stage.key}'s playbooks"
                assert not IMPORTS & set(tasks[task]), f"{name}: {step.label}: {task!r} is an import"
            for end in getattr(step, "files", ()):
                assert any(path.endswith("/" + end) for path in files), f"{name}: {step.label}: no {end}"


@pytest.mark.parametrize("name", RECIPES)
def test_every_fact_a_declaration_reads_is_set_by_a_task(name):
    recipe = recipes.RECIPES[name]
    set_facts = set()
    for _, item in TASKS:
        for action in SET_FACT & set(item):
            if isinstance(item[action], dict):
                set_facts |= set(item[action])
    facts = set()
    for stage in recipe.stages:
        for part in (stage, *stage.steps):
            facts |= {outcome.fact for outcome in getattr(part, "outcomes", ())}
        info = getattr(stage, "info", "")
        if not isinstance(info, str):
            facts.add(info.fact)
        # The checks a stage's playbook keeps as it makes them (init's init_results), which its rows are drawn from.
        if getattr(stage, "checklist", ""):
            facts.add(stage.checklist)
    assert facts <= set_facts, f"{name}: no task sets {sorted(facts - set_facts)}"


ADD_HOST = {"add_host", "ansible.builtin.add_host", "ansible.legacy.add_host"}


def test_a_vms_address_and_id_are_where_the_engine_reads_them():
    # The engine takes a VM's address and ID from two names alone (live_ansible.Engine._discovered): discovery's
    # add_host, its ansible_host and proxmox_vm_id, and vm_addresses.yml's claude_vm_addressed. A rename in either
    # would leave every summary without its VM's address and ID, and nothing would fail.
    files = {name for recipe in recipes.RECIPES.values() for name in recipe.addresses_from}
    assert files == {"discover.yml", "vm_addresses.yml"}, files
    added = [
        item[action]
        for item in _items(_load_yaml(REPO / "playbooks" / "discover.yml"))
        for action in ADD_HOST & set(item)
    ]
    assert added and all({"ansible_host", "proxmox_vm_id"} <= set(each) for each in added), added
    set_facts = {
        fact
        for item in _items(_load_yaml(REPO / "playbooks" / "tasks" / "vm_addresses.yml"))
        for action in SET_FACT & set(item)
        if isinstance(item[action], dict)
        for fact in item[action]
    }
    assert "claude_vm_addressed" in set_facts, set_facts


# Each live test, and what it must name to run a real playbook as that playbook's own tests run it: the runner, or
# every path they pin. A live test that runs only stand-ins - deploy's, playbooks named as the real ones in a
# directory of its own - takes nothing from the repository's playbooks/. Each pin is read from the test's code
# (_pinned), never its text: a module imported from (`from x import`), a call (`name(`), a name used, a string given
# as a dict's key or a call's argument (`"key"`), or a key given a constant (`"key": False`).
LIVE_TESTS = {
    "test_deploy_live.py": (),
    # login.yml as test_sign_in_plays.py runs it: its stand-in VMs and extra vars, which keep every sign-in off this
    # machine's browser, clipboard and terminal.
    "test_login_live.py": ("from test_sign_in_plays import", "_extra(", "_hosts("),
    # destroy.yml as test_release.py runs it: its stand-in VMs, and every path a run writes - the saved sessions, the
    # managed SSH config and the user's - in the test's own directory.
    "test_destroy_live.py": (
        "tr._hosts(",
        '"destroy_sessions_dir"',
        '"claude_ssh_config_file"',
        '"claude_ssh_user_config"',
        '"claude_ssh_config_include": False',
    ),
    # init_settings.yml as test_init_settings.py runs it, its files copies in the test's own directory, and
    # init_check.yml as test_init_check.py does, with its own inventory; a refusing ssh for both.
    "test_init_live.py": ("tis._files(", "tic._inventory_of(", '"ssh"'),
    # just lint as test_justfile.py runs it: its stand-in linters, its log and its state in the test's own directory.
    "test_lint_live.py": ("_linters(",),
    # code.yml as test_code_session.py runs it: its pins, never the developer's VS Code settings, time zone or
    # ~/.claude, and both SSH configs in the test's own directory.
    "test_code_live.py": (
        "tcs._window_extra(",
        '"code_session_source_dir"',
        '"code_session_dest_dir"',
        '"claude_ssh_config_file"',
        '"claude_ssh_user_config"',
    ),
    "test_status_live.py": ("status_root",),
}


def _pinned(source):
    """What a test's code does, in LIVE_TESTS' terms: a pin in a comment or a docstring is no pin."""
    pinned = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            pinned.add(f"from {node.module} import")
            pinned |= {alias.asname or alias.name for alias in node.names}
        elif isinstance(node, ast.Call):
            pinned.add(f"{ast.unparse(node.func)}(")
            strings = (arg.value for arg in node.args if isinstance(arg, ast.Constant))
            pinned |= {f'"{value}"' for value in strings if isinstance(value, str)}
        elif isinstance(node, ast.Name):
            pinned.add(node.id)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    pinned.add(f'"{key.value}"')
                    if isinstance(value, ast.Constant):
                        pinned.add(f'"{key.value}": {value.value!r}')
    return pinned


def test_a_pin_counts_only_in_a_tests_code():
    source = '''"""Runs it with "destroy_sessions_dir" pinned."""
# "claude_ssh_config_file", _hosts(
from test_release import _extra

run({"destroy_sessions_dir": str(root), "claude_ssh_config_include": False}, "ssh")
'''
    pinned = _pinned(source)
    assert {'"destroy_sessions_dir"', '"claude_ssh_config_include": False', '"ssh"', "run(", "_extra"} <= pinned
    assert "from test_release import" in pinned
    assert not {'"claude_ssh_config_file"', "_hosts(", '"claude_ssh_config_include": True'} & pinned
    assert '"destroy_sessions_dir"' not in _pinned(source.replace('{"destroy_sessions_dir": str(root), ', "{"))


def test_every_live_test_runs_a_real_playbook_only_as_its_own_tests_do():
    found = sorted(path.name for path in (REPO / "tests" / "unit").glob("test_*_live.py"))
    assert found == sorted(LIVE_TESTS), "a new live test is added to LIVE_TESTS, with what it must pin"
    for name, pins in LIVE_TESTS.items():
        source = (REPO / "tests" / "unit" / name).read_text()
        if not pins:
            assert not re.search(r"""REPO\s*/\s*["']playbooks["']""", source), f"{name} runs a real playbook"
        pinned = _pinned(source)
        for pin in pins:
            assert pin in pinned, f"{name} does not pin {pin} in its code"
