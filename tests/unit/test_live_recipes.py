"""Every `just` command drawn live, its declaration held to the playbooks it names (filter_plugins/live_recipes.py).

A declaration matches plays, blocks and tasks by name, and reads facts by name: a name that no longer exists matches
nothing, and the run is drawn without a word of it - a stage with no plays, a failure raised again drawn as a
failure of its own, a VM never marked. So every name a declaration gives is looked for here, in the playbook or the
task files it names, and every fact it reads in a task that sets it. The live tests that run a real playbook do so
as that playbook's own tests run it, with every path those pin (docs/architecture/custom-code.md §7.8).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SET_FACT = {"set_fact", "ansible.builtin.set_fact", "ansible.legacy.set_fact"}


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
    assert facts <= set_facts, f"{name}: no task sets {sorted(facts - set_facts)}"


# Each live test, and what it must name to run a real playbook as that playbook's own tests run it: the runner, or
# every path they pin. A live test that runs only stand-ins - deploy's, playbooks named as the real ones in a
# directory of its own - takes nothing from the repository's playbooks/.
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
    "test_status_live.py": ("status_root",),
}


def test_every_live_test_runs_a_real_playbook_only_as_its_own_tests_do():
    found = sorted(path.name for path in (REPO / "tests" / "unit").glob("test_*_live.py"))
    assert found == sorted(LIVE_TESTS), "a new live test is added to LIVE_TESTS, with what it must pin"
    for name, pins in LIVE_TESTS.items():
        source = (REPO / "tests" / "unit" / name).read_text()
        if not pins:
            assert not re.search(r"""REPO\s*/\s*["']playbooks["']""", source), f"{name} runs a real playbook"
        for pin in pins:
            assert pin in source, f"{name} does not pin {pin}"
