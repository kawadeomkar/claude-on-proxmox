"""`just login` drawn live: the real playbooks/login.yml through the stdout callback, as the justfile runs it.

The VMs are test_sign_in_plays.py's stand-ins, run as its own tests run them (its _hosts and _extra: no discovery,
no sudo, never this machine's browser or clipboard, a refusing ssh for the VM that cannot be reached), with the
live kit's environment (tests/unit/live_kit.py): a HOME of the run's own and CLAUDE_ON_PROXMOX_LIVE as the recipe
sets it. alpha signs in, beta cannot be reached and gamma's code is refused: what a person sees of each, in the
region and at the end (docs/architecture/custom-code.md §7.5, docs/guides/runs.md). One run at a terminal serves every
test: a whole login.yml takes half a minute, and what it prints above the region - each failure, the summary - is
what a pipe gets too, whose line per step test_deploy_live.py holds.
"""

from __future__ import annotations

import re

import live_kit as kit
import pytest
from harness import _inventory
from test_sign_in_plays import CODE, LOGIN, _extra, _hosts, fake_github

TITLE = "login alpha beta gamma"
CASES = {"alpha": {"code": CODE}, "beta": {"down": True}, "gamma": {"code": CODE, "reject": True}}
# GitHub's device flow, approved at once by the fake GitHub API, and its one repository cloned.
VARS = {"github_projects_exclude": ["gone"]}


@pytest.fixture(scope="module")
def github(tmp_path_factory):
    yield from fake_github(tmp_path_factory)


def _argv(root, github):
    cases = {name: {**case, "vars": VARS} for name, case in CASES.items()}
    inventory = _inventory(root, _hosts(root, cases, github), ordered=True)
    return kit.playbook(root, inventory, LOGIN, "-e", _extra(vm_name=",".join(CASES)))


@pytest.fixture(scope="module")
def drawn(tmp_path_factory, github):
    root = tmp_path_factory.mktemp("login_drawn")
    env = kit.environment(root, TITLE)
    return kit.at_a_terminal(_argv(root, github), root, env, cols=100, rows=30, timeout=300)


def test_each_failure_is_drawn_under_the_sign_in_it_happened_in_and_the_raise_names_none(drawn):
    # What stays on the screen once the region is gone: every line printed above it.
    text = drawn.transcript
    assert drawn.rc == 2, drawn.text
    # gamma's refused code is Claude's, and beta, which could not be reached, is so in each sign-in it missed.
    assert re.search(r"^✗ Claude · gamma: ", text, re.M), text
    assert re.search(r"^✗ Claude · beta: Reach the VM", text, re.M), text
    # login.yml's last play raises them again: drawn under them, never as a failure of its own.
    assert "Fail where a sign-in, or the work after one, failed" not in text.split("claude-on-proxmox  login", 1)[0]


def test_the_summary_says_which_vms_failed_and_each_vms_outcome_in_each_sign_in(drawn):
    text = drawn.transcript
    head = re.search(rf"^claude-on-proxmox  {TITLE} · done in .* · 2 VMs failed: beta, gamma$", text, re.M)
    assert head, text
    table = text.split("SIGN-INS", 1)[1].split("\n\n", 1)[0]
    claude = next(line for line in table.splitlines() if " Claude " in line)
    assert claude.startswith(" ✗ Claude") and "alpha ✓" in claude and "beta ✗" in claude and "gamma ✗" in claude
    # Discovery and login.yml's last play are no sign-ins: counted, never drawn.
    assert not re.search(r"^ . (discover|finish)", text, re.M), text


def test_a_drawn_login_is_one_row_a_sign_in_and_leaves_nothing_of_its_region(drawn):
    assert drawn.rc == 2, drawn.text
    drawn.assert_drawn_and_gone()
    titles = drawn.screen.titles
    assert titles[0] == TITLE and titles[-1] == f"{TITLE} · failed", titles
    frames = drawn.text
    assert re.search(r"sign-in \d of 3", frames), "the footer counts the sign-ins"
    assert "stage 1 of" not in frames
