"""tests/unit/conftest.py's --shard, as CI splits the unit tests (docs/architecture/ci.md): N shards that between them
run every test exactly once, the tests that share a fixture's run in one of them.

Each look is a collection of the suite in a pytest of its own, as each CI shard collects it.
"""

from __future__ import annotations

import ast
import functools
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PYTEST = Path(sys.executable).with_name("pytest")


# Made once for the module: its tests read the three shards of 3, each a collection of the whole suite. Every test
# takes everything, which makes them one xdist group (conftest.py), so they share one worker and this cache.
@functools.cache
def _collected(*args: str) -> list[str]:
    """The node IDs a run of tests/unit with args would run."""
    # Nothing of this run's own pytest, an xdist worker's included.
    env = {name: value for name, value in os.environ.items() if not name.startswith("PYTEST")}
    result = subprocess.run(
        [str(PYTEST), "-p", "no:cacheprovider", "-o", "addopts=", "--collect-only", "-q", "tests/unit", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if "::" in line]


@pytest.fixture(scope="module")
def everything() -> list[str]:
    return _collected()


@pytest.mark.parametrize("count", [2, 3, 6])
def test_the_shards_run_every_test_exactly_once(everything, count) -> None:
    shards = [_collected("--shard", f"{index}/{count}") for index in range(1, count + 1)]
    assert all(shards), [len(shard) for shard in shards]
    ran = [test for shard in shards for test in shard]
    assert sorted(ran) == sorted(everything)


def _taking(module: str, fixture: str) -> set[str]:
    """The test functions of module that ask for fixture by argument."""
    tree = ast.parse((REPO / "tests" / "unit" / module).read_text())
    tests = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
    return {node.name for node in tests if fixture in [argument.arg for argument in node.args.args]}


@pytest.mark.parametrize(
    ("module", "fixture"),
    [("test_github_auth.py", "approved"), ("test_tailscale.py", "configured"), ("test_sign_in_plays.py", "deployed")],
)
def test_the_tests_that_share_a_fixture_are_never_split(everything, module, fixture) -> None:
    shards = [_collected("--shard", f"{index}/3") for index in range(1, 4)]
    names = _taking(module, fixture)
    assert names, (module, fixture)
    holding = [
        index
        for index, shard in enumerate(shards, 1)
        if any(f"/{module}::" in test and test.split("::")[1].split("[")[0] in names for test in shard)
    ]
    assert len(holding) == 1, (module, fixture, holding)


def _module_fixtures(tree: ast.Module) -> set[str]:
    def module_scoped(decorator: ast.expr) -> bool:
        keywords = decorator.keywords if isinstance(decorator, ast.Call) else []
        return any(keyword.arg == "scope" and getattr(keyword.value, "value", None) == "module" for keyword in keywords)

    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    return {node.name for node in functions if any(module_scoped(decorator) for decorator in node.decorator_list)}


def test_every_fixture_made_on_each_worker_is_a_module_fixture_of_its_own_module() -> None:
    # conftest.py looks EACH_WORKER's names up and never checks them: a name that is no module-scoped fixture of its
    # module would hold nothing apart, and say so nowhere.
    declared = {}
    for path in sorted((REPO / "tests" / "unit").glob("test_*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            names = [getattr(target, "id", "") for target in node.targets] if isinstance(node, ast.Assign) else []
            if names == ["EACH_WORKER"]:
                declared[path.name] = (ast.literal_eval(node.value), _module_fixtures(tree))
    assert set(declared) >= {"test_github_auth.py", "test_settings.py", "test_sign_in_plays.py"}, sorted(declared)
    for module, (names, fixtures) in declared.items():
        assert names and names <= fixtures, (module, sorted(names - fixtures))
