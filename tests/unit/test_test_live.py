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

import importlib.util
import io
import json
import logging
import os
import re
import subprocess
import sys

import live_kit as kit
import pytest
from harness import REPO
from test_justfile import JUST, _env, _merged, _stand_in, _testing

# The last test's ID has a "/" of its own, as a parameter's may.
TESTS = """
import time

import pytest


def test_one():
    time.sleep(0.2)


def test_two():
    time.sleep(0.2)


@pytest.mark.parametrize("where", ["Mars/Olympus"])
def test_three(where):
    assert {fails} == 0, "the stand-in suite was asked to fail"
"""
# Molecule's own lines, as 26.x writes them with NO_COLOR: the matrix up front, each action's start and end, and the
# Ansible tasks between - in the configure scenario a task that names a warning and a deprecation that is one. An
# action that fails ends coloured all the same, then Molecule cleans up and destroys, and only then says why: a converge
# whose task failed with more lines than a failure block holds, as callback_result_format yaml says a command's, or an
# idempotence test that names the task that changed.
MOLECULE = """
scenario=default
case "$*" in *"-s configure"*) scenario=configure ;; *"-s provision"*) scenario=provision ;; esac
say() {{ printf '%s\\n' "$*" >&2; }}
say "INFO     [$scenario > discovery] scenario test matrix: create, converge, idempotence, verify, destroy"
say "WARNING  [$scenario > dependency] Missing roles requirements file: requirements.yml"
for action in create converge idempotence verify destroy; do
  say "INFO     [$scenario > $action] Executing"
  echo "TASK [$action the instance] ***"
  sleep 0.1
  case "$scenario $action" in
  "configure converge")
    echo "TASK [proxmox_vm : absent | Warn when the address could not be determined] ***"
    echo "[DEPRECATION WARNING]: the stand-in's deprecation, as ansible-core says one."
    ;;
  "{fails} converge")
    echo "TASK [common : Run the step that breaks] ***"
    echo "fatal: [instance]: FAILED! => changed=true"
    echo "  msg: the stand-in scenario was asked to fail"
    for n in $(seq 1 45); do echo "  - line $n of what the command printed"; done
    echo "PLAY RECAP ***"
    reason="Ansible return code was 2, command was: ansible-playbook converge.yml"
    ;;
  "{changes} idempotence")
    echo "TASK [common : Write what changes every run] ***"
    echo "changed: [instance]"
    for n in $(seq 1 45); do echo "TASK [common : Task number $n] ***"; echo "ok: [instance]"; done
    reason="Idempotence test failed because of the following tasks:"
    ;;
  esac
  if [ -z "${{reason:-}}" ]; then
    say "INFO     [$scenario > $action] Executed: Successful"
    continue
  fi
  printf 'ERROR    [%s > %s] \\033[31mExecuted: Failed\\033[0m\\n' "$scenario" "$action" >&2
  say "WARNING  [$scenario > $action] An error occurred during the test sequence action: '$action'. Cleaning up."
  for after in cleanup destroy; do
    say "INFO     [$scenario > $after] Executing"
    echo "TASK [$after the instance] ***"
    say "INFO     [$scenario > $after] Executed: Successful"
  done
  say "ERROR    $reason"
  [ "$action" != idempotence ] || say "* [instance] => common : Write what changes every run"
  say "ERROR    Molecule executed 1 scenario (1 failed)"
  exit 1
done
"""


# The project's configuration - its pythonpath finds the plugin - over a suite outside the project.
_SUITE_ARGS = f"-c {REPO / 'pyproject.toml'} --rootdir {REPO} -p no:cacheprovider"


def _test(root, *args, fails_test=False, fails_scenario="", changes_scenario="", molecule=MOLECULE, pytest_run=""):
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
    # The real pytest, its plugin from the project's pythonpath, over the suite here rather than tests/unit, which the
    # recipe names: that word is dropped from its arguments and the rest kept (the plugin, a worker per CPU), so the
    # nested run is the recipe's own over three tests, never the whole suite inside a test of it.
    _stand_in(
        root / "bin",
        "pytest",
        pytest_run
        or 'for arg in "$@"; do shift; [ "$arg" = tests/unit ] || set -- "$@" "$arg"; done\n'
        f'exec "{sys.executable}" -m pytest "$@" {_SUITE_ARGS} "{suite}"',
    )
    _stand_in(root / "bin", "molecule", molecule.format(fails=fails_scenario, changes=changes_scenario))
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


@pytest.fixture(scope="module")
def not_idempotent(tmp_path_factory):
    return _test(tmp_path_factory.mktemp("not_idempotent"), "provision", changes_scenario="provision")


def test_the_gate_is_one_run_lint_its_first_stage_and_every_stage_to_come_listed(passed):
    assert passed.rc == 0, passed.text
    passed.assert_drawn_and_gone()
    first = next(frame for frame in passed.frames if " lint " in frame)
    for stage in ("unit", "role", "integration", "provision"):
        assert re.search(rf"^ ○ {stage}\b", first, re.M), (stage, first)
    assert any(re.search(r"^ \S unit .* \d/3 ", frame, re.M) for frame in passed.frames), "pytest's count"
    assert any(re.search(r"^     \S common ", frame, re.M) for frame in passed.frames), "a scenario a step"
    # The matrix the count, from the start of the scenario.
    assert any(re.search(r"^     \S common .* 1/5 ", frame, re.M) for frame in passed.frames), passed.frames[-1]
    # A deprecation in a scenario's Ansible is said above once it passed; Molecule's routine notices stay in the log,
    # and a task that names a warning is none.
    assert passed.transcript.count("passed with warnings") == 1, passed.transcript
    warned = passed.transcript.split("! configure: molecule passed with warnings\n", 1)[1].split("\n\n", 1)[0]
    assert "the stand-in's deprecation" in warned and "Warn when" not in warned, warned
    assert "Missing roles requirements file" not in warned, warned
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


def test_a_role_s_scenario_is_titled_with_the_words_typed_and_named_in_the_log(tmp_path):
    ran = _test(tmp_path, "--role", "common")
    assert ran.rc == 0, ran.text
    ran.assert_drawn_and_gone()
    # --role stands for the role part, which nobody typed.
    assert ran.screen.titles[-1] == "test --role common · done", ran.screen.titles
    # The role's scenario named in the log, beside the row that names it on the screen, and never above the region.
    assert "| ===== molecule: common\n" in ran.log and "===== molecule" not in ran.transcript


def test_a_test_that_fails_is_said_as_it_fails_listed_and_what_it_left_unrun_named(failed):
    assert failed.rc != 0, failed.text
    failed.assert_drawn_and_gone()
    screen = failed.transcript
    # Named by its file and its whole ID, the "/" in its parameter its own.
    assert re.search(r"^✗ unit: test_tiny\.py::test_three\[Mars/Olympus\]$", screen, re.M), screen
    assert "the stand-in suite was asked to fail" in screen
    assert "test_tiny.py::test_three[Mars/Olympus]" in screen.split("\nFAILED\n", 1)[1].split("\n\n", 1)[0]
    assert re.search(r"^claude-on-proxmox  test unit provision · failed after .* at unit", screen, re.M), screen
    # bash -e ends the run at the failed part: the stage it never reached is named.
    assert re.search(r"^ not run  provision$", screen, re.M), screen


def test_an_action_that_fails_is_said_with_its_task_and_ansible_s_words_as_molecule_goes_on(scenario_failed):
    assert scenario_failed.rc != 0, scenario_failed.text
    scenario_failed.assert_drawn_and_gone()
    screen = scenario_failed.transcript
    # The task that failed in the head, and what Ansible said of it from its start, however long it ran.
    assert re.search(r"^✗ provision: converge · common : Run the step that breaks$", screen, re.M), screen
    said = screen.split("✗ provision: converge · ", 1)[1].split("\n\n", 1)[0]
    assert "    fatal: [instance]: FAILED! => changed=true" in said, said
    assert "the stand-in scenario was asked to fail" in said, said
    assert re.search(r"^    … \d+ more lines in the run's log$", said, re.M), said
    assert "converge" in screen.split("\nFAILED\n", 1)[1].split("\n\n", 1)[0]
    # Molecule went on to clean up and destroy, drawn as their actions ran, and said why last: on the screen too.
    assert "Executed: Successful" in scenario_failed.log.split("[provision > destroy] Executing", 1)[1]
    assert re.search(r"^    ERROR    Ansible return code was 2, command was: ansible-playbook", screen, re.M), screen


def test_an_idempotence_test_that_fails_names_the_task_that_changed(not_idempotent):
    assert not_idempotent.rc != 0, not_idempotent.text
    not_idempotent.assert_drawn_and_gone()
    screen = not_idempotent.transcript
    assert re.search(r"^✗ provision: idempotence$", screen, re.M), screen
    # Molecule names the task only after its cleanup and destroy: the scenario's end says it.
    end = screen.split("✗ provision: idempotence (", 1)[1]
    assert "Idempotence test failed because of the following tasks:" in end, screen
    assert "* [instance] => common : Write what changes every run" in end, screen
    assert "idempotence" in screen.split("\nFAILED\n", 1)[1].split("\n\n", 1)[0]


# Molecule in lines the reader does not know - its colour mode's arrows - passing.
ARROWS = r"""
say() {{ printf '%s\n' "$*" >&2; }}
say "INFO     default ➜ discovery: scenario test matrix: create, converge, destroy"
for action in create converge destroy; do
  say "INFO     default ➜ $action: Executing"
  echo "TASK [$action the instance] ***"
  say "INFO     default ➜ $action: Executed: Successful"
done
"""


def test_a_scenario_whose_lines_the_reader_does_not_know_ends_by_its_exit_code(tmp_path):
    ran = _test(tmp_path, "--role", "common", molecule=ARROWS)
    assert ran.rc == 0, ran.text
    ran.assert_drawn_and_gone()
    # Done: the command is the role's one task, never a scenario not run.
    assert ran.screen.titles[-1] == "test --role common · done", ran.screen.titles
    assert "not run" not in ran.transcript


def test_a_unit_run_that_said_no_test_ends_by_its_exit_code(tmp_path):
    # A pytest that never loaded the plugin: the command is the stage's one task, never a stage not run.
    ran = _test(tmp_path, "unit", pytest_run='echo "no tests ran"')
    assert ran.rc == 0, ran.text
    ran.assert_drawn_and_gone()
    assert ran.screen.titles[-1] == "test unit · done", ran.screen.titles
    assert "not run" not in ran.transcript


def test_the_molecule_reader_knows_the_lines_molecule_s_own_logger_writes(monkeypatch):
    """Molecule's lines as its own logger writes them under NO_COLOR, which every part's child has: a Molecule that
    writes them otherwise fails here, rather than each scenario reading as one command."""
    from molecule.ansi_output import AnsiOutput
    from molecule.constants import ANSICodes
    from molecule.reporting.definitions import CompletionState

    from molecule import logger as molecule_logger

    monkeypatch.setenv("NO_COLOR", "1")
    written = io.StringIO()
    monkeypatch.setattr(molecule_logger, "original_stderr", written)
    log = logging.getLogger("claude_on_proxmox.test_test_live.molecule")
    handler = molecule_logger.MoleculeConsoleHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    monkeypatch.setattr(log, "handlers", [handler])
    monkeypatch.setattr(log, "propagate", False)
    log.setLevel(logging.INFO)

    def say(step, level, message):
        adapter = molecule_logger.ScenarioLoggerAdapter(log, {"scenario_name": "provision", "step_name": step})
        getattr(adapter, level)(message)

    # As command/base.py says the matrix, section_logger an action's start and end, and a failed one's.
    say("discovery", "info", "scenario test matrix: create, converge, destroy")
    say("converge", "info", "[exec_executing]Executing[/]")
    done = CompletionState.successful
    say("converge", done.log_level, AnsiOutput().format_completion_message(done.message, done.color))
    failed = CompletionState.failed
    say("converge", "error", f"{failed.color}{failed.message}{ANSICodes.RESET}")
    reader = _live_commands().Molecule
    said = [_said(line) for line in written.getvalue().splitlines()]
    assert len(said) == 4, said
    assert reader.MATRIX.search(said[0]).group("actions") == "create, converge, destroy", said
    actions = [reader.ACTION.search(line) for line in said[1:]]
    assert all(actions), said
    assert [(found.group("scenario"), found.group("action")) for found in actions] == [("provision", "converge")] * 3
    assert [found.group("what") for found in actions] == ["Executing", "Executed: Successful", "Executed: Failed"]


def _live_commands():
    name = "claude_on_proxmox_live_commands"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, REPO / "filter_plugins" / "live_commands.py")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


def _said(line):
    """A line as the readers take it: plain text, one line."""
    commands = _live_commands()
    return commands.view.one_line(commands.text.plain_text(line))


def test_a_test_whose_worker_crashed_ends_failed(tmp_path):
    # As xdist 3.8 reports a test whose worker crashed under it (DSession.handle_crashitem): neither setup, call nor
    # teardown. The plugin is run in a Python of its own, where no live run of this suite already loaded it.
    events = tmp_path / "events.jsonl"
    code = (
        "import pytest, live_pytest\n"
        "live_pytest._run['config'] = object()\n"
        "live_pytest.pytest_runtest_logstart('tests/unit/test_x.py::test_crash', None)\n"
        "live_pytest.pytest_runtest_logreport(pytest.TestReport(nodeid='tests/unit/test_x.py::test_crash',"
        " location=('tests/unit/test_x.py', None, 'test_crash'), keywords={}, outcome='failed',"
        " longrepr=\"worker 'gw0' crashed while running 'tests/unit/test_x.py::test_crash'\", when='???'))\n"
    )
    environment = {name: value for name, value in os.environ.items() if not name.startswith("CLAUDE_ON_")}
    environment.update(CLAUDE_ON_PROXMOX_LIVE_PYTEST=str(events), PYTHONPATH=str(REPO / "tests" / "unit"))
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=environment, check=True, timeout=60)
    said = [json.loads(line) for line in events.read_text().splitlines()]
    assert said[-1]["end"] == "tests/unit/test_x.py::test_crash" and said[-1]["outcome"] == "failed", said
    assert "crashed" in said[-1]["said"]


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
    assert ends["test_one"]["outcome"] == "passed" and ends["test_three[Mars/Olympus]"]["outcome"] == "failed"
    assert "the stand-in suite was asked to fail" in ends["test_three[Mars/Olympus]"]["said"]
