"""`just lint` drawn live: the real recipe, each check behind tools/live, at a pseudo-terminal of the test's own.

The checks are test_justfile.py's stand-ins (_linters: BIN, UV, LOG_DIR and LIVE_DIR in the test's directory), each
saying a few lines; tools/live runs with the environment's Python. What a person sees: the checks as rows, each to
come until it runs, the running one's last line; a check that fails, its end whole above and the checks it left
unrun named; the region gone, the state removed, and every line the checks said in the run's log
(docs/architecture/custom-code.md §7.8).
"""

from __future__ import annotations

import re
import sys

import live_kit as kit
import pytest
from test_justfile import JUST, _env, _linters, _merged, _stand_in

SAYS = 'for n in 1 2 3; do echo "{tool}: checked file-$n"; sleep 0.1; done'


def _lint(root, *args, fails=""):
    settings = _linters(root)
    # The environment's Python itself, its packages with it: a link to it elsewhere finds no virtualenv.
    (root / "bin" / "python").unlink()
    (root / "bin" / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (root / "bin" / "python").chmod(0o755)
    for tool in ("yamllint", "ansible-lint", "ruff", "ansible-playbook", "pre-commit"):
        body = SAYS.format(tool=tool) + (f'\necho "{tool}: something is wrong"; exit 2' if tool == fails else "")
        _stand_in(root / "bin", tool, body)
    env = kit.environment(root, "", **_env())
    env.pop("ANSIBLE_LOG_PATH")
    ran = kit.at_a_terminal([JUST, *_merged(settings), "lint", *args], kit.REPO, env, timeout=120)
    logs = sorted((root / "logs").glob("*-lint.log"))
    ran.log = logs[-1].read_text() if logs else ""
    return ran


@pytest.fixture(scope="module")
def passed(tmp_path_factory):
    return _lint(tmp_path_factory.mktemp("lint_passed"), "yaml", "ansible", "python")


@pytest.fixture(scope="module")
def failed(tmp_path_factory):
    return _lint(tmp_path_factory.mktemp("lint_failed"), "yaml", "ansible", "python", "syntax", fails="ansible-lint")


def test_lint_is_drawn_as_its_checks_each_to_come_until_it_runs(passed):
    assert passed.rc == 0, passed.text
    passed.assert_drawn_and_gone()
    first = next(frame for frame in passed.frames if " yaml " in frame)
    assert re.search(r"^ ○ ansible\s*$", first, re.M) and re.search(r"^ ○ python\s*$", first, re.M), first
    assert any("yamllint: checked file-" in frame for frame in passed.frames), "the running check's last line"
    assert not any(re.search(r"\d+/\d+", frame) for frame in passed.frames), "a check is one command: no counts"
    screen = passed.transcript
    assert re.search(r"^claude-on-proxmox  lint yaml ansible python · done in \d+ s · ", screen, re.M), screen
    table = screen.split("CHECKS", 1)[1].split("\n\n", 1)[0]
    assert [line.split()[1] for line in table.splitlines()[1:]] == ["yaml", "ansible", "python"], table


def test_every_line_a_check_said_is_in_the_run_s_one_log(passed):
    for tool in ("yamllint", "ansible-lint", "ruff"):
        assert f"| {tool}: checked file-3\n" in passed.log, passed.log
    assert "claude-on-proxmox  lint yaml ansible python · done in" in passed.log


def test_a_check_that_fails_is_said_whole_and_what_it_left_unrun_named(failed):
    assert failed.rc != 0, failed.text
    failed.assert_drawn_and_gone()
    screen = failed.transcript
    assert re.search(r"^✗ ansible: ansible-lint \(.*\) · exit 2$", screen, re.M), screen
    assert "    ansible-lint: something is wrong" in screen
    head = r"^claude-on-proxmox  lint yaml ansible python syntax · failed after \d+ s at ansible"
    assert re.search(head, screen, re.M), screen
    assert re.search(r"^ not run  python · syntax$", screen, re.M), screen
    assert failed.screen.titles[-1] == "lint yaml ansible python syntax · failed"
