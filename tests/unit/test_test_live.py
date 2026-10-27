"""`just test` drawn live: the real recipe at a pseudo-terminal, lint nested in it, pytest and Molecule as its parts.

The tools are test_justfile.py's stand-ins (_testing: the linters, BIN, LOG_DIR and LIVE_DIR in the test's own
directory) but two: pytest is the real one with tests/unit/live_pytest.py, over a few tests of the test's own, and
Molecule a stand-in that says its matrix and its actions as Molecule does. What a person sees: lint as the run's
first stage, the stages to come listed from the start, the tests as they run and each scenario's actions; a test and
an action that fail, said above as they fail and listed in the summary; the region gone, the state removed, and
every line the parts said in the run's log (docs/architecture/custom-code.md §7.8). The real suite runs only in the
smoke run, on a throwaway VM.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import live_kit as kit
import pytest
from harness import REPO
from test_justfile import JUST, _env, _merged, _stand_in, _testing

TESTS = """
import time


def test_one():
    time.sleep(0.2)


def test_two():
    time.sleep(0.2)


def test_three():
    assert {fails} == 0, "the stand-in suite was asked to fail"
"""
# Molecule's own lines, as 26.x writes them with NO_COLOR: the matrix up front, each action's start and end, and the
# Ansible tasks between.
MOLECULE = """
scenario=default
case "$*" in *"-s configure"*) scenario=configure ;; *"-s provision"*) scenario=provision ;; esac
echo "INFO     [$scenario > discovery] scenario test matrix: create, converge, verify, destroy" >&2
echo "WARNING  [$scenario > dependency] Missing roles requirements file: requirements.yml" >&2
for action in create converge verify destroy; do
  echo "INFO     [$scenario > $action] Executing" >&2
  echo "TASK [$action the instance] ***"
  sleep 0.1
  if [ "$action" = converge ] && [ "$scenario" = "{fails}" ]; then
    echo "fatal: [instance]: FAILED! => {{\\"msg\\": \\"the stand-in scenario was asked to fail\\"}}"
    echo "ERROR    [$scenario > $action] Executed: Failed" >&2
    failed=yes
    continue
  fi
  echo "INFO     [$scenario > $action] Executed: Successful" >&2
done
[ -z "${{failed:-}}" ] || exit 1
"""


# The project's configuration - its pythonpath finds the plugin - over a suite outside the project.
_SUITE_ARGS = f"-c {REPO / 'pyproject.toml'} --rootdir {REPO} -p no:cacheprovider"


def _test(root, *args, fails_test=False, fails_scenario=""):
    settings = _testing(root)
    # The environment's Python itself, its packages with it, for tools/live.
    (root / "bin" / "python").unlink()
    (root / "bin" / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (root / "bin" / "python").chmod(0o755)
    for tool in ("yamllint", "ansible-lint", "ruff", "ansible-playbook", "pre-commit"):
        _stand_in(root / "bin", tool, f'echo "{tool}: checked"')
    suite = root / "suite"
    suite.mkdir()
    (suite / "test_tiny.py").write_text(TESTS.format(fails=1 if fails_test else 0))
    # The real pytest, its plugin from the project's pythonpath, over the suite here rather than tests/unit.
    _stand_in(root / "bin", "pytest", f'exec "{sys.executable}" -m pytest "$@" {_SUITE_ARGS} "{suite}"')
    _stand_in(root / "bin", "molecule", MOLECULE.format(fails=fails_scenario))
    env = kit.environment(root, "", **_env())
    env.pop("ANSIBLE_LOG_PATH")
    ran = kit.at_a_terminal([JUST, *_merged(settings), "test", *args], REPO, env, timeout=240)
    logs = sorted((root / "logs").glob("*-test*.log"))
    ran.log = "".join(log.read_text() for log in logs)
    ran.state = list((root / "live").glob("*.json")) if (root / "live").exists() else []
    return ran


@pytest.fixture(scope="module")
def passed(tmp_path_factory):
    return _test(tmp_path_factory.mktemp("test_passed"))


@pytest.fixture(scope="module")
def failed(tmp_path_factory):
    return _test(tmp_path_factory.mktemp("test_failed"), "unit", "provision", fails_test=True)


@pytest.fixture(scope="module")
def scenario_failed(tmp_path_factory):
    return _test(tmp_path_factory.mktemp("scenario_failed"), "provision", fails_scenario="provision")


def test_the_gate_is_one_run_lint_its_first_stage_and_every_stage_to_come_listed(passed):
    assert passed.rc == 0, passed.text
    passed.assert_drawn_and_gone()
    first = next(frame for frame in passed.frames if " lint " in frame)
    for stage in ("unit", "role", "integration", "provision"):
        assert re.search(rf"^ ○ {stage}\b", first, re.M), (stage, first)
    assert any(re.search(r"^ \S unit .* \d/3 ", frame, re.M) for frame in passed.frames), "pytest's count"
    assert any(re.search(r"^     \S common ", frame, re.M) for frame in passed.frames), "a scenario a step"
    # The matrix the count, from the start of the scenario; Molecule's own warnings in the log alone.
    assert any(re.search(r"^     \S common .* 1/4 ", frame, re.M) for frame in passed.frames), passed.frames[-1]
    assert "passed with warnings" not in passed.transcript
    screen = passed.transcript
    assert re.search(r"^claude-on-proxmox  test · done in .*$", screen, re.M), screen
    table = screen.split("STAGES", 1)[1].split("\n\n", 1)[0]
    assert [line.split()[1] for line in table.splitlines() if line.startswith((" ✓ ", " ✗ "))] == [
        "lint",
        "unit",
        "role",
        "integration",
        "provision",
    ], table
    assert passed.screen.titles[-1] == "test · done" and passed.state == []


def test_every_line_the_parts_said_is_in_the_run_s_log(passed):
    assert "| yamllint: checked\n" in passed.log
    assert "Executed: Successful" in passed.log and "| TASK [converge the instance] ***\n" in passed.log
    assert "3 passed" in passed.log


def test_a_test_that_fails_is_said_as_it_fails_listed_and_what_it_left_unrun_named(failed):
    assert failed.rc != 0, failed.text
    failed.assert_drawn_and_gone()
    screen = failed.transcript
    assert re.search(r"^✗ unit: test_tiny\.py::test_three$", screen, re.M), screen
    assert "the stand-in suite was asked to fail" in screen
    assert "test_tiny.py::test_three" in screen.split("\nFAILED\n", 1)[1].split("\n\n", 1)[0]
    assert re.search(r"^claude-on-proxmox  test unit provision · failed after .* at unit", screen, re.M), screen
    # bash -e ends the run at the failed part: the stage it never reached is named.
    assert re.search(r"^ not run  provision$", screen, re.M), screen


def test_an_action_that_fails_is_said_with_its_lines_as_molecule_goes_on(scenario_failed):
    assert scenario_failed.rc != 0, scenario_failed.text
    scenario_failed.assert_drawn_and_gone()
    screen = scenario_failed.transcript
    assert re.search(r"^✗ provision: converge$", screen, re.M), screen
    assert "the stand-in scenario was asked to fail" in screen
    assert "converge" in screen.split("\nFAILED\n", 1)[1].split("\n\n", 1)[0]
    # Molecule went on to verify and destroy: drawn, as their actions ran.
    assert "Executed: Successful" in scenario_failed.log.split("[provision > destroy] Executing", 1)[1]


def test_the_plugin_writes_nothing_without_its_file_and_each_event_with_it(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_tiny.py").write_text(TESTS.format(fails=1))
    events = tmp_path / "events.jsonl"

    def pytest_run(**env):
        argv = [sys.executable, "-m", "pytest", "-p", "live_pytest", *_SUITE_ARGS.split(), str(suite)]
        environment = {name: value for name, value in os.environ.items() if not name.startswith("CLAUDE_ON_")}
        return subprocess.run(argv, cwd=REPO, env={**environment, **env}, capture_output=True, text=True, check=False)

    assert pytest_run().returncode == 1 and not events.exists()
    assert pytest_run(CLAUDE_ON_PROXMOX_LIVE_PYTEST=str(events)).returncode == 1
    said = [json.loads(line) for line in events.read_text().splitlines()]
    assert said[0] == {"count": 3}
    assert len([event for event in said if "start" in event]) == 3
    ends = {event["end"].split("::")[-1]: event for event in said if "end" in event}
    assert ends["test_one"]["outcome"] == "passed" and ends["test_three"]["outcome"] == "failed"
    assert "the stand-in suite was asked to fail" in ends["test_three"]["said"]
