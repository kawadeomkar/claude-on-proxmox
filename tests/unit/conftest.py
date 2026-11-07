"""Shared fixtures for testing custom Ansible modules without a live API."""

from __future__ import annotations

import functools
import inspect
import json
import os
import re
import statistics
import subprocess
from pathlib import Path

import ansible.module_utils
import interrupts
import pytest
from ansible.module_utils import basic
from ansible.module_utils.testing import patch_module_args

ROOT = Path(__file__).resolve().parents[2]
# What the modules in playbooks/library/ share, for the tests that load one by its path: Ansible puts a playbook's
# module_utils folder on the path it builds the playbook's modules from, and nothing does in this process.
ansible.module_utils.__path__.append(str(ROOT / "playbooks" / "module_utils"))
# The Galaxy collections every Ansible a test starts reads: this checkout's own, as every recipe points Ansible at
# them (tools/env-run --collections). just test exports the same; one set by hand is kept.
os.environ.setdefault(
    "ANSIBLE_COLLECTIONS_PATH",
    subprocess.run(
        [str(ROOT / "tools" / "env-run"), "--collections"], capture_output=True, text=True, check=True
    ).stdout.strip(),
)


class AnsibleExitJson(Exception):
    """Raised instead of sys.exit() when a module calls exit_json."""


class AnsibleFailJson(Exception):
    """Raised instead of sys.exit() when a module calls fail_json."""


def _exit_json(*args, **kwargs):
    kwargs.setdefault("changed", False)
    raise AnsibleExitJson(kwargs)


def _fail_json(*args, **kwargs):
    kwargs["failed"] = True
    raise AnsibleFailJson(kwargs)


@pytest.fixture
def patch_module(monkeypatch):
    """Make AnsibleModule raise instead of exiting so tests can inspect results."""
    monkeypatch.setattr(basic.AnsibleModule, "exit_json", _exit_json)
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", _fail_json)


def run_main(main, args):
    """Run a module's main() with the given args; return its exit_json payload."""
    with patch_module_args(args), pytest.raises(AnsibleExitJson) as exc:
        main()
    return exc.value.args[0]


def fail_main(main, args):
    """Run a module's main() with the given args; return its fail_json payload."""
    with patch_module_args(args), pytest.raises(AnsibleFailJson) as exc:
        main()
    return exc.value.args[0]


# Each test's serial duration, by node ID, that --shard balances by; a test it lacks counts as the mean.
DURATIONS = ROOT / "tests" / "unit" / "durations.json"


def pytest_configure(config):
    # A kill or a hangup ends this process as Ctrl-C would, in the controller and in every worker: the test running
    # gets a KeyboardInterrupt, its run and everything under it ended by the harness on the way out, where the default
    # ended the process at once and left them (tests/unit/interrupts.py).
    interrupts.install()


def pytest_addoption(parser):
    # Registered only when this conftest is an initial one: `pytest tests/unit`, as just test unit runs it.
    # A bare `pytest --shard` is refused as an unknown option, never run as the whole suite.
    parser.addoption("--shard", metavar="I/N", help="run only the I-th of N shards, each group of tests whole")


@functools.cache
def _asks_by_name(function) -> bool:
    """Whether a test function calls request.getfixturevalue; a function whose source cannot be read is taken to."""
    try:
        return "getfixturevalue" in inspect.getsource(function)
    except (OSError, TypeError):
        return True


def _shared(item) -> set[str] | None:
    """This project's module-scoped fixtures item reaches but its module's EACH_WORKER; None when no look can tell.

    A test reaches a fixture through request.getfixturevalue only by a `fixture` parameter naming it, this suite's one
    way; any other call to it leaves what it asks for unknown.
    """
    names = list(item._fixtureinfo.names_closure)
    if _asks_by_name(item.function):
        named = item.callspec.params.get("fixture") if hasattr(item, "callspec") else None
        if not isinstance(named, str):
            return None
        names.append(named)
    each_worker = getattr(item.module, "EACH_WORKER", frozenset())
    manager, found, seen = item.session._fixturemanager, set(), set()
    while names:
        name = names.pop()
        if name in seen:
            continue
        seen.add(name)
        defs = manager.getfixturedefs(name, item)
        if not defs or defs[-1].func.__module__.startswith("_pytest"):
            continue
        if defs[-1].scope != "function" and name not in each_worker:
            found.add(name)
        names.extend(defs[-1].argnames)
    return found


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Keep the tests that share a module-scoped fixture on one xdist worker; with --shard, in one shard.

    Such a fixture is a whole playbook run, made once for its module: split across workers or shards, each would make
    it again. The tests that reach one, directly or through the fixtures they ask for, are its group, and two fixtures
    one test reaches are one group, so every run is still made once; the module's other tests go wherever there is
    room. Its whole module was one group until 2026-11, which left a shard's last minutes to one worker. A module with
    a test that asks for a fixture by name any other way than a `fixture` parameter stays whole. A module's
    EACH_WORKER names the fixtures cheap enough to make again on each worker that needs one - a fake server - which
    join nothing. Only this project's fixtures count: pytest's own tmp_path rests on a session-scoped one, made per
    worker anyway. First, so that xdist's own hook, which turns the marks into groups, sees them.
    """
    reached = {id(item): _shared(item) for item in items}
    whole = {item.module.__name__ for item in items if reached[id(item)] is None}
    parent: dict[str, str] = {}

    def root(key: str) -> str:
        while parent.setdefault(key, key) != key:
            key = parent[key]
        return key

    def keys(item) -> list[str]:
        # Words only, as xdist's @<group> on a test's name must be for tools/unit_durations.py to take it off.
        module = item.module.__name__
        return [module] if module in whole else [f"{module}__{name}" for name in sorted(reached[id(item)])]

    for item in items:
        joined = keys(item)
        for key in joined[1:]:
            parent[root(key)] = root(joined[0])
    units: dict[str, list] = {}
    for item in items:
        group = root(keys(item)[0]) if keys(item) else None
        if group:
            item.add_marker(pytest.mark.xdist_group(group))
        units.setdefault(group or item.nodeid, []).append(item)
    shard = config.getoption("--shard", None)
    if not shard:
        return
    match = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", shard)
    if not match or int(match[1]) > int(match[2]):
        raise pytest.UsageError(f"--shard takes I/N, two whole numbers with 1 <= I <= N, not {shard}")
    index, count = int(match[1]), int(match[2])
    durations = json.loads(DURATIONS.read_text()) if DURATIONS.exists() else {}
    mean = statistics.mean(durations.values()) if durations else 1.0
    weight = {key: sum(durations.get(item.nodeid, mean) for item in members) for key, members in units.items()}
    loads, owner = [0.0] * count, {}
    for key in sorted(units, key=lambda key: (-weight[key], key)):
        slot = loads.index(min(loads))
        loads[slot] += weight[key]
        owner[key] = slot
    kept = {id(item) for key, members in units.items() if owner[key] == index - 1 for item in members}
    config.hook.pytest_deselected(items=[item for item in items if id(item) not in kept])
    items[:] = [item for item in items if id(item) in kept]
