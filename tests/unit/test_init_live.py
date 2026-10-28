"""`just init` drawn live: its two playbooks as one run in parts, as the recipe runs them after just install.

A stand-in recipe with the justfile's trap runs the real init_settings.yml as test_init_settings.py runs it at a
terminal - its four files copies in the test's own directory, a refusing ssh first on PATH - typing its questions'
answers, then the real init_check.yml as test_init_check.py runs it, against the fake Proxmox with its own inventory
and the same refusing ssh. What a person sees: the questions with no frame flashed between them, the settings stage's
steps, the checks drawn as they are recorded and said whole in the summary, one NEXT STEPS at the end; and a check
that fails drawn as the failure, the last task naming nothing of its own (docs/architecture/custom-code.md §7.8).
"""

from __future__ import annotations

import json
import re
import sys

import harness
import live_kit as kit
import pytest
import test_init_check as tic
import test_init_settings as tis

LIVE = harness.REPO / "tools" / "live"
STAMP = "20261026-131050"
RECIPE = """#!/bin/bash
set -eu
live_end() {{ "{python}" "{live}" --log-dir "{logs}" --stamp {stamp} end "$1"; }}
trap 'live_end $?' EXIT
"{playbook}" -i "{init_inventory}" -e '{settings}' "{settings_playbook}"
"{playbook}" -i "{proxmox}" -e '{checks}' "{check_playbook}"
"""
# The questions, each typed once it has the terminal: the host, the token ID as it is, a GitHub user, the secret.
ANSWERS = {
    tis.HOST_PROMPT.decode(): b"pve.lan\r",
    tis.TOKEN_PROMPT.decode(): b"\r",
    tis.GITHUB_PROMPT.decode(): b"octocat\r",
    tis.SECRET_PROMPT.decode(): tis.SECRET.encode() + b"\r",
}


@pytest.fixture(scope="module")
def pve(tmp_path_factory):
    root = tmp_path_factory.mktemp("init_pve")
    cert, key = harness._certificate(root)
    fake = tic.FAKES / "fake_pve_api.py"
    for base in harness._serve(fake, str(cert), str(key), str(root / "pve"), harness.PVE_SECRET):
        yield base.replace("http://", "https://")


def _init(root, pve, check_settings=None):
    (root / "settings").mkdir()
    files = tis._files(root / "settings")
    paths, env = tic._inventory_of(root / "checks", pve, "ssh", check_settings, tic.VAULT_HEADER, "127.0.0.1")
    state = root / "live" / f"{STAMP}-init.json"
    script = root / "recipe.sh"
    script.write_text(
        RECIPE.format(
            python=sys.executable,
            live=LIVE,
            logs=root / "logs",
            stamp=STAMP,
            playbook=kit.ANSIBLE_PLAYBOOK,
            init_inventory=tis.INVENTORY,
            settings=json.dumps(files | {"init_ask": True}),
            settings_playbook=tis.PLAYBOOK,
            proxmox=root / "checks" / "proxmox.yml",
            checks=json.dumps(paths),
            check_playbook=tic.PLAYBOOK,
        )
    )
    script.chmod(0o755)
    environment = kit.environment(root, "init", CLAUDE_ON_PROXMOX_LIVE_STATE=str(state), **env)
    ran = kit.at_a_terminal([script], root, environment, prompts=ANSWERS, timeout=240)
    ran.state = state
    return ran


@pytest.fixture(scope="module")
def passed(tmp_path_factory, pve):
    return _init(tmp_path_factory.mktemp("init_passed"), pve)


@pytest.fixture(scope="module")
def refused(tmp_path_factory, pve):
    return _init(tmp_path_factory.mktemp("init_refused"), pve, {"vault_proxmox_api_token_secret": "not-the-secret"})


def test_init_is_one_run_its_questions_steps_and_its_checks_drawn_as_they_are_made(passed):
    assert passed.rc == 0, passed.text
    passed.assert_drawn_and_gone()
    frames = "\n".join(passed.frames)
    for step in ("Proxmox host", "API token", "GitHub", "secret", "vault"):
        assert re.search(rf"^     \S {step} ", frames, re.M), step
    assert re.search(r"^ ✓ settings ", frames, re.M), "the checks drew the settings, done"
    assert re.search(r"^     ✓ Proxmox VE \S+ at ", frames, re.M), "a check, as it was recorded"
    screen = passed.transcript
    assert screen.count("claude-on-proxmox  init · done in") == 1 and screen.count("##  NEXT STEPS") == 1, screen
    assert screen.index("claude-on-proxmox  init · done in") < screen.index("##  NEXT STEPS")
    assert "just deploy alpha" in screen.split("##  NEXT STEPS", 1)[1]
    checks = screen.split("\nCHECKS\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert checks and all(line.startswith(" ✓ ") for line in checks), checks
    assert passed.screen.titles[-1] == "init · done"
    assert not passed.state.exists()


def test_a_check_that_fails_is_the_failure_and_the_last_task_names_nothing_of_its_own(refused):
    assert refused.rc == 2, refused.text
    refused.assert_drawn_and_gone()
    screen = refused.transcript
    assert re.search(r"^✗ checks · this machine: \S", screen, re.M), screen
    assert "Fail when a check found a problem" not in screen.split("claude-on-proxmox  init", 1)[0]
    assert re.search(r"^claude-on-proxmox  init · failed after .* at checks", screen, re.M), screen
    checks = screen.split("\nCHECKS\n", 1)[1].split("\n\n", 1)[0]
    assert re.search(r"^ ✗ ", checks, re.M), checks
    # NEXT STEPS keeps the record's lines: each problem under the line that counts them, with its indent.
    steps = screen.split("##  NEXT STEPS", 1)[1]
    assert re.search(r"^just init found 2 problems", steps, re.M) and re.search(r"^  !!   Proxmox at ", steps, re.M)
