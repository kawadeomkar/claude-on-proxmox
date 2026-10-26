"""Shared fixtures for testing custom Ansible modules without a live API."""

from __future__ import annotations

import importlib.util
import json
import re
import statistics
import sys
from pathlib import Path

import pytest
from ansible.module_utils import basic
from ansible.module_utils.testing import patch_module_args

ROOT = Path(__file__).resolve().parents[2]


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


def load_module(relative_path):
    """Import a module file from the repo by path (roles/*/library/*.py)."""
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


# Each test's serial duration, by node ID, that --shard balances by; a test it lacks counts as the mean.
DURATIONS = ROOT / "tests" / "unit" / "durations.json"


def pytest_addoption(parser):
    # Registered only when this conftest is an initial one: `pytest tests/unit`, as just test unit runs it.
    # A bare `pytest --shard` is refused as an unknown option, never run as the whole suite.
    parser.addoption("--shard", metavar="I/N", help="run only the I-th of N shards, each group of tests whole")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Keep each module whose tests share a module-scoped fixture on one xdist worker; with --shard, one shard.

    Such a fixture is a whole playbook run, made once for its module: split across workers or shards, each would
    make it again. The whole module goes, since a test may reach the fixture through request.getfixturevalue, which
    no static look finds. Only this project's fixtures count: pytest's own tmp_path rests on a session-scoped one,
    made per worker anyway. First, so that xdist's own hook, which turns the marks into groups, sees them.
    """
    shared = set()
    for item in items:
        info = getattr(item, "_fixtureinfo", None)
        if info and any(
            defs[-1].scope != "function" and not defs[-1].func.__module__.startswith("_pytest")
            for defs in info.name2fixturedefs.values()
        ):
            shared.add(item.module.__name__)
    units: dict[str, list] = {}
    for item in items:
        module = item.module.__name__
        if module in shared:
            item.add_marker(pytest.mark.xdist_group(module))
        units.setdefault(module if module in shared else item.nodeid, []).append(item)
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
