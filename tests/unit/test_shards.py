"""tests/unit/conftest.py's --shard, as CI splits the unit tests (docs/architecture/ci.md): N shards that between them
run every test exactly once, each module whose tests share a fixture whole in one of them.

Each look is a collection of the suite in a pytest of its own, as each CI shard collects it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PYTEST = Path(sys.executable).with_name("pytest")


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


@pytest.mark.parametrize("count", [2, 3, 4])
def test_the_shards_run_every_test_exactly_once(everything, count) -> None:
    shards = [_collected("--shard", f"{index}/{count}") for index in range(1, count + 1)]
    assert all(shards), [len(shard) for shard in shards]
    ran = [test for shard in shards for test in shard]
    assert sorted(ran) == sorted(everything)


def test_a_module_whose_tests_share_a_fixture_is_never_split() -> None:
    shards = [_collected("--shard", f"{index}/3") for index in range(1, 4)]
    for module in ("test_github_auth.py", "test_tailscale.py", "test_sign_in_plays.py"):
        holding = [index for index, shard in enumerate(shards, 1) if any(f"/{module}::" in test for test in shard)]
        assert len(holding) == 1, (module, holding)
