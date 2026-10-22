"""The Makefile's safety guards must keep working, or a regression ships green.

Five guards stand between `make <target> VM_NAME=...` and the playbooks, and
nothing but `make` itself exercises them:

- `vault-check` refuses to run a playbook without vault.yml (with the `make
  init` hint; the playbook used to die on an undefined variable instead) or
  while it is still plaintext.
- `vm-name-check` refuses a VM_NAME that could break out of, or reshape, the
  JSON VM_ARGS passes with `-e` (a quote, whitespace, a shell metacharacter,
  an embedded newline that YAML would fold into a space).
- VM_ARGS passes the names as one JSON extra-var. `-e vm_name=$(VM_NAME)`
  splits on whitespace and once silently provisioned half a fleet.
- TYPE, TAGS and ROLE are checked against fixed lists while the Makefile is
  read, so only a listed word reaches a recipe or a log file's name, and a
  misspelt one is said instead of running nothing - or everything.
- A target the Makefile no longer has stops with the command that replaced
  it, and a TYPE written as a word after a target is refused, rather than
  becoming a VM name (make deploy configure) or a target of its own (make
  vagrant destroy, which would delete the default VM).

`make -n` prints a recipe without running it, which is enough to see what
`deploy TYPE=provision` would pass. The refusals and the prerequisite wiring
are shell lines, which -n does not run, so those tests run `make` for real with
BIN pointed at a directory holding only a stand-in ansible-galaxy and
VAULT_FILE at a stub that only looks encrypted: nothing could reach an
ansible-playbook even if a guard regressed, and vault-check and
collections-check, which every playbook target runs, let the run reach the
guard under test.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MAKEFILE = REPO / "Makefile"
MAKE = shutil.which("make")
# The targets whose recipe runs a playbook, in the Makefile's order.
PLAYBOOK_TARGETS = ["deploy", "code", "login", "list", "destroy"]
NAME_ERROR = "VM_NAME may contain only letters, digits and . _ - ,"

pytestmark = pytest.mark.skipif(MAKE is None, reason="make is not installed")


def _make(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # A clean environment: a developer's own VM_NAME or TAGS would leak into
    # the Makefile's `?=` defaults, and the locale must not be what makes the
    # non-ASCII case pass or fail. env adds to it.
    clean = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"), "LANG": "en_US.UTF-8"}
    return subprocess.run(
        [MAKE, *args], cwd=REPO, env=clean | (env or {}), capture_output=True, text=True, check=False, timeout=60
    )


VAULT_HEADER = "$ANSIBLE_VAULT;1.1;AES256\n"


def _stand_in_galaxy(bin_dir: Path, rc: int = 0) -> Path:
    """An ansible-galaxy in bin_dir for collections-check: it records its arguments and exits rc; returns the record."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    record = bin_dir / "galaxy-args"
    stand_in = bin_dir / "ansible-galaxy"
    stand_in.write_text(f'#!/bin/sh\nfor arg in "$@"; do printf "%s\\n" "$arg"; done > {record}\nexit {rc}\n')
    stand_in.chmod(0o755)
    return record


def _safe_overrides(tmp_path: Path) -> list[str]:
    """Make the run harmless: no ansible-playbook to execute, a vault stub vault-check accepts, and logs in tmp."""
    vault = tmp_path / "vault.yml"
    vault.write_text(VAULT_HEADER)
    _stand_in_galaxy(tmp_path / "no-venv" / "bin")
    return [f"BIN={tmp_path / 'no-venv' / 'bin'}", f"VAULT_FILE={vault}", f"LOG_DIR={tmp_path / 'logs'}"]


def test_vault_check_accepts_an_encrypted_vault(tmp_path: Path) -> None:
    result = _make("vault-check", *_safe_overrides(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


def test_vault_check_refuses_a_plaintext_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault.yml"
    vault.write_text("vault_proxmox_api_token_secret: not-a-real-secret\n")
    result = _make("vault-check", f"VAULT_FILE={vault}")
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert f"{vault} is not encrypted. Run: make vault" in output


def test_vault_check_names_make_init_when_the_vault_is_missing(tmp_path: Path) -> None:
    vault = tmp_path / "no-vault.yml"
    result = _make("vault-check", f"VAULT_FILE={vault}")
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert f"{vault} missing. Run: make init" in output
    assert "not encrypted" not in output


def test_list_stops_at_the_missing_vault_before_the_playbook(tmp_path: Path) -> None:
    """A fresh clone that skipped `make init` gets the hint, not an Ansible stack trace."""
    vault = tmp_path / "no-vault.yml"
    result = _make("list", f"BIN={tmp_path / 'no-venv'}", f"VAULT_FILE={vault}", f"LOG_DIR={tmp_path / 'logs'}")
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "missing. Run: make init" in output
    assert "ansible-playbook" not in output


# collections-check: every checkout on a machine shares ~/.ansible/collections,
# so a run whose collections differ from requirements.yml's pins is refused,
# naming make install, before its playbook.
def test_collections_check_asks_galaxy_offline_whether_the_pins_are_installed(tmp_path: Path) -> None:
    record = _stand_in_galaxy(tmp_path / "venv" / "bin")
    result = _make("collections-check", f"BIN={tmp_path / 'venv' / 'bin'}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert record.read_text().splitlines() == ["collection", "install", "-r", "requirements.yml", "--offline"]


def test_a_playbook_target_stops_at_collections_that_are_not_the_pins(tmp_path: Path) -> None:
    overrides = _safe_overrides(tmp_path)
    _stand_in_galaxy(tmp_path / "no-venv" / "bin", rc=1)
    result = _make("list", *overrides)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "are not the ones requirements.yml pins. Run: make install" in output
    assert "ansible-playbook" not in output


def _prerequisites(target: str) -> list[str]:
    line = next(line for line in MAKEFILE.read_text().splitlines() if line.startswith(f"{target}: ") and "##" in line)
    return line.split(":", 1)[1].split("##", 1)[0].split()


@pytest.mark.parametrize("target", [*PLAYBOOK_TARGETS, "test"])
def test_every_target_that_runs_ansible_checks_the_collections_first(target: str) -> None:
    prerequisites = [word for word in _prerequisites(target) if not word.startswith("$(")]
    assert "collections-check" in prerequisites, prerequisites
    # After what checks the input, before the log of a run it may refuse.
    assert prerequisites[-2:] == ["collections-check", "log-setup"], prerequisites


def _public_targets() -> list[str]:
    return re.findall(r"(?m)^([a-z-]+):[^#\n]*## ", MAKEFILE.read_text())


def _types() -> dict[str, list[str]]:
    return {
        name: value.split() for name, value in re.findall(r"(?m)^TYPES_([a-z-]+)\s*:=\s*(.*)$", MAKEFILE.read_text())
    }


def _runs_a_playbook(*args: str) -> bool:
    result = _make("-n", *args, "VM_NAME=alpha")
    assert result.returncode == 0, result.stdout + result.stderr
    return any("ansible-playbook" in line and "--syntax-check" not in line for line in result.stdout.splitlines())


def test_the_targets_checked_are_every_playbook_target() -> None:
    # Every target, with each of its TYPEs, whose recipe runs ansible-playbook
    # from the virtualenv, but lint, which only parses the playbooks.
    types = _types()
    running = set()
    for target in _public_targets():
        if target in {"help", "init", "vault", "clean", "vagrant"}:
            continue
        for kind in ["", *types.get(target, [])]:
            if (target, kind) == ("deploy", "template"):
                continue
            if _runs_a_playbook(target, *([f"TYPE={kind}"] if kind else [])):
                running.add(target)
    assert running == set(PLAYBOOK_TARGETS), running


@pytest.mark.parametrize("value", ["alpha", "alpha,beta", "a.b-c_d", ""])
def test_vm_name_check_accepts_dns_like_names(value: str) -> None:
    result = _make("vm-name-check", f"VM_NAME={value}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert NAME_ERROR not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("a b", id="space"),
        pytest.param("a'b", id="single-quote"),
        pytest.param('a"b', id="double-quote"),
        pytest.param("a;b", id="semicolon"),
        pytest.param("$(x)", id="make-expansion"),
        pytest.param("a`b", id="backtick"),
        pytest.param("a\tb", id="tab"),
        pytest.param("alpha\nbeta", id="newline"),
        pytest.param("ålpha", id="non-ascii"),
    ],
)
def test_vm_name_check_rejects_anything_else(value: str) -> None:
    result = _make("vm-name-check", f"VM_NAME={value}")
    assert result.returncode != 0, "accepted " + repr(value)
    assert NAME_ERROR in result.stdout + result.stderr


def test_provision_passes_vm_name_as_one_json_extra_var() -> None:
    result = _make("-n", "deploy", "TYPE=provision", "VM_NAME=alpha,beta")
    assert result.returncode == 0, result.stdout + result.stderr
    assert """-e '{"vm_name": "alpha,beta"}'""" in result.stdout
    assert "playbooks/provision.yml" in result.stdout


def test_provision_without_vm_name_passes_no_vm_name_at_all() -> None:
    result = _make("-n", "deploy", "TYPE=provision")
    assert result.returncode == 0, result.stdout + result.stderr
    assert """-e '{"vm_name\"""" not in result.stdout
    assert "playbooks/provision.yml" in result.stdout


def test_provision_runs_the_name_check_before_the_playbook(tmp_path: Path) -> None:
    result = _make("deploy", "TYPE=provision", "VM_NAME=a;b", *_safe_overrides(tmp_path))
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert NAME_ERROR in output
    assert "ansible-playbook" not in output


# Every target that takes VM_NAME also takes the names after it: make reads
# each as a goal, which the Makefile answers with a rule that does nothing once
# it has refused a word that is a target, and hands the words to vm-name-check
# as VM_NAME. Each target, and the playbook it runs.
NAMED_TARGETS = {
    "deploy": "deploy.yml",
    "code": "playbooks/code.yml",
    "login": "playbooks/login.yml",
    "list": "playbooks/list.yml",
    "destroy": "playbooks/destroy.yml",
}


def _playbook_line(result: subprocess.CompletedProcess[str]) -> str:
    return next(line for line in result.stdout.splitlines() if "ansible-playbook" in line)


def test_every_target_that_takes_vm_name_takes_the_names_after_it() -> None:
    listed = re.search(r"(?m)^NAMED_TARGETS := ((?:.*\\\n)*.*)$", MAKEFILE.read_text()).group(1)
    takes_vm_name = [target for target in PLAYBOOK_TARGETS if "vm-name-check" in _prerequisites(target)]
    assert listed.replace("\\\n", " ").split() == takes_vm_name == list(NAMED_TARGETS)


@pytest.mark.parametrize("target", NAMED_TARGETS)
@pytest.mark.parametrize(
    ("words", "names"),
    [(["roam"], "roam"), (["alpha", "beta"], "alpha,beta"), (["alpha,beta"], "alpha,beta")],
)
def test_a_target_takes_the_names_after_it_as_vm_name(target: str, words: list[str], names: str) -> None:
    after = _make("-n", target, *words)
    given = _make("-n", target, f"VM_NAME={names}")
    assert after.returncode == 0, after.stdout + after.stderr
    assert f"""-e '{{"vm_name": "{names}"}}'""" in _playbook_line(after)
    assert _playbook_line(after).endswith(NAMED_TARGETS[target])
    assert _playbook_line(after) == _playbook_line(given)


@pytest.mark.parametrize("target", NAMED_TARGETS)
def test_a_target_without_names_after_it_passes_no_vm_name_at_all(target: str) -> None:
    result = _make("-n", target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "vm_name" not in _playbook_line(result)


# make would run such a word's target after the first - `make destroy test`
# the whole test suite once the default VM is gone - so the run is refused
# before anything starts.
@pytest.mark.parametrize("target", NAMED_TARGETS)
@pytest.mark.parametrize("words", [["test"], ["roam", "list"], ["deploy", "lint"]])
def test_a_target_refuses_a_name_that_is_a_make_target(tmp_path: Path, target: str, words: list[str]) -> None:
    result = _make(target, *words, *_safe_overrides(tmp_path))
    assert result.returncode != 0
    assert "among the VM names:" in result.stderr
    assert f"make {target} VM_NAME={','.join(w for w in words if w != 'roam')}" in result.stderr
    assert result.stdout == ""


def test_code_refuses_several_names_after_it(tmp_path: Path) -> None:
    result = _make("code", "alpha", "beta", *_safe_overrides(tmp_path))
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "code opens one VM at a time, not [alpha,beta]" in output
    assert "ansible-playbook" not in output


# Chaining a second target says so, VM_NAME given or not.
@pytest.mark.parametrize("target", NAMED_TARGETS)
def test_a_target_followed_by_another_is_refused_as_such(tmp_path: Path, target: str) -> None:
    result = _make(target, "list", "VM_NAME=alpha", *_safe_overrides(tmp_path))
    assert result.returncode != 0
    assert "among the VM names: list. Run one target at a time" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("target", NAMED_TARGETS)
@pytest.mark.parametrize(
    ("args", "env"),
    [
        pytest.param(["VM_NAME=alpha", "beta"], {}, id="command-line"),
        pytest.param(["beta"], {"VM_NAME": "alpha"}, id="environment"),
    ],
)
def test_a_target_refuses_names_given_both_ways(
    tmp_path: Path, target: str, args: list[str], env: dict[str, str]
) -> None:
    result = _make(target, *args, *_safe_overrides(tmp_path), env=env)
    assert result.returncode != 0
    assert f"give the VM names one way: make {target} <name>..." in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("target", NAMED_TARGETS)
@pytest.mark.parametrize("word", ["a;b", "a'b", "$(x)", "a%", "a:b"])
def test_a_target_runs_the_name_check_on_the_names_after_it(tmp_path: Path, target: str, word: str) -> None:
    result = _make(target, word, *_safe_overrides(tmp_path))
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert NAME_ERROR in output
    assert "ansible-playbook" not in output


@pytest.mark.parametrize("target", NAMED_TARGETS)
def test_a_name_after_a_target_is_never_expanded_by_make(tmp_path: Path, target: str) -> None:
    ran = tmp_path / "ran"
    result = _make(target, f"$(shell touch {ran})", *_safe_overrides(tmp_path))
    assert result.returncode != 0
    assert NAME_ERROR in result.stdout + result.stderr
    assert not ran.exists()


# TYPE: one word, among the target's own, checked while the Makefile is read.
# Each of deploy's runs the playbook its stage had as a target of its own, with
# the same arguments; login's run one sign-in, and without TYPE all three.
DEPLOY_TYPES = {
    "template": "playbooks/template.yml",
    "provision": "playbooks/provision.yml",
    "configure": "playbooks/configure.yml",
    "check": "playbooks/configure.yml",
    "ssh-config": "playbooks/ssh_config.yml",
}
LOGIN_TYPES = {
    "claude": "playbooks/claude_login.yml",
    "github": "playbooks/github_login.yml",
    "tailscale": "playbooks/tailscale_login.yml",
}


@pytest.mark.parametrize(("kind", "playbook"), DEPLOY_TYPES.items())
def test_each_deploy_type_runs_its_stage_s_playbook(kind: str, playbook: str) -> None:
    names = [] if kind == "template" else ["VM_NAME=alpha,beta"]
    line = _playbook_line(_make("-n", "deploy", f"TYPE={kind}", *names))
    assert line.endswith(" " + playbook), line
    assert ("--check --diff" in line) == (kind == "check"), line
    assert ("vm_name" in line) == (kind != "template"), line
    # Only the whole deploy runs sign-ins, so only it turns them off without a terminal.
    assert "login_desktop" not in line


def test_deploy_without_type_runs_deploy_yml_with_the_no_terminal_switch() -> None:
    line = _playbook_line(_make("-n", "deploy", "VM_NAME=alpha"))
    assert line.endswith(" deploy.yml"), line
    assert "login_desktop" in line


@pytest.mark.parametrize(("kind", "playbook"), [("", "playbooks/login.yml"), *LOGIN_TYPES.items()])
def test_each_login_type_runs_its_sign_in_and_none_all_three(kind: str, playbook: str) -> None:
    result = _make("-n", "login", "VM_NAME=alpha,beta", *([f"TYPE={kind}"] if kind else []))
    line = _playbook_line(result)
    assert line.endswith(" " + playbook), line
    assert """-e '{"vm_name": "alpha,beta"}'""" in line
    assert "login_desktop" in line


@pytest.mark.parametrize(
    ("args", "error"),
    [
        pytest.param(
            ["deploy", "TYPE=bogus"],
            "make deploy takes TYPE=template|provision|configure|check|ssh-config, not [bogus]",
            id="unknown",
        ),
        pytest.param(
            ["login", "TYPE=configure"],
            "make login takes TYPE=claude|github|tailscale, not [configure]",
            id="another-target-s",
        ),
        pytest.param(["deploy", "TYPE=configure check"], "not [configure check]", id="two-words"),
        pytest.param(
            ["test", "TYPE=lint"],
            "make test takes TYPE=unit|molecule|role|integration|provision, not [lint]",
            id="lint-is-its-own",
        ),
        pytest.param(["list", "TYPE=configure"], "make list takes no TYPE (got: [configure])", id="list"),
        pytest.param(["code", "TYPE=claude"], "make code takes no TYPE", id="code"),
        pytest.param(["TYPE=serve"], "make help takes no TYPE", id="no-target"),
    ],
)
def test_a_type_is_checked_before_anything_runs(tmp_path: Path, args: list[str], error: str) -> None:
    result = _make(*args, *_safe_overrides(tmp_path))
    assert result.returncode != 0
    assert error in result.stderr
    assert result.stdout == ""


def test_a_type_is_never_expanded_by_make(tmp_path: Path) -> None:
    ran = tmp_path / "ran"
    result = _make("deploy", f"TYPE=$(shell touch {ran})", *_safe_overrides(tmp_path))
    assert result.returncode != 0
    assert f"not [$(shell touch {ran})]" in result.stderr
    assert not ran.exists()


def test_a_type_narrows_only_the_target_it_was_given_for() -> None:
    # make docs clean TYPE=build: the build, and a whole clean.
    result = _make("-n", "docs", "clean", "TYPE=build")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "zensical build --strict --clean" in result.stdout
    assert "rm -rf" in result.stdout


def test_deploy_type_template_refuses_vm_names(tmp_path: Path) -> None:
    result = _make("deploy", "alpha", "TYPE=template", *_safe_overrides(tmp_path))
    assert result.returncode != 0
    assert "make deploy TYPE=template builds the template, which takes no VM names" in result.stderr


# TAGS narrows configuration, and nothing else: on its own it is TYPE=configure,
# TYPE=check takes it, and the stages that carry no tags refuse it rather than
# run doing nothing. Only a role's name gets through.
def test_tags_alone_means_type_configure() -> None:
    line = _playbook_line(_make("-n", "deploy", "VM_NAME=alpha", "TAGS=claude_code,tailscale"))
    assert line.endswith(" playbooks/configure.yml"), line
    assert "--tags claude_code,tailscale" in line
    assert "--check" not in line


def test_tags_without_names_is_refused_and_the_fleet_takes_type_configure(tmp_path: Path) -> None:
    # A bare make deploy means the default VM, configuration without names every VM:
    # an API key pushed with make deploy TAGS=claude_code would otherwise reach them all.
    refused = _make("deploy", "TAGS=claude_code", *_safe_overrides(tmp_path))
    assert refused.returncode != 0
    assert (
        "TAGS on its own configures the VMs named beside it: make deploy <name>... TAGS=claude_code" in refused.stderr
    )
    assert "make deploy TYPE=configure TAGS=claude_code" in refused.stderr
    assert refused.stdout == ""
    fleet = _playbook_line(_make("-n", "deploy", "TYPE=configure", "TAGS=claude_code"))
    assert "vm_name" not in fleet and "--tags claude_code" in fleet
    named = _playbook_line(_make("-n", "deploy", "alpha", "beta", "TAGS=claude_code"))
    assert """-e '{"vm_name": "alpha,beta"}'""" in named and named.endswith(" playbooks/configure.yml")


def test_tags_with_type_check_are_a_dry_run_of_those_roles() -> None:
    line = _playbook_line(_make("-n", "deploy", "TYPE=check", "TAGS=github_projects"))
    assert line.endswith("--check --diff playbooks/configure.yml"), line
    assert "--tags github_projects" in line


@pytest.mark.parametrize("kind", ["template", "provision", "ssh-config"])
def test_tags_with_a_stage_that_has_none_are_refused(tmp_path: Path, kind: str) -> None:
    result = _make("deploy", f"TYPE={kind}", "TAGS=common", *_safe_overrides(tmp_path))
    assert result.returncode != 0
    assert (
        f"TAGS narrows configuration, which TYPE={kind} does not run: make deploy TYPE=configure TAGS=common"
        in result.stderr
    )
    assert result.stdout == ""


@pytest.mark.parametrize(
    "tags", ["claude", "claude_code;rm -rf x", "$(shell touch x)", "common,always", "--skip-tags=common"]
)
def test_tags_may_name_only_the_roles(tmp_path: Path, tags: str) -> None:
    result = _make("deploy", f"TAGS={tags}", *_safe_overrides(tmp_path))
    assert result.returncode != 0
    assert (
        "TAGS may name only common, vscode_server, dev_tools, claude_code, github_projects, tailscale" in result.stderr
    )
    assert result.stdout == ""


def test_the_tags_are_configure_s_own() -> None:
    configure = (REPO / "playbooks" / "configure.yml").read_text()
    used = set(re.findall(r"tags: \[(\w+)\]", configure)) - {"always"}
    listed = re.search(r"(?m)^CONFIGURE_TAGS\s*:=\s*(.*)$", MAKEFILE.read_text()).group(1).split()
    assert used == set(listed), (used, listed)


def test_ssh_config_passes_vm_name_as_json() -> None:
    result = _make("-n", "deploy", "TYPE=ssh-config", "VM_NAME=alpha,beta")
    assert result.returncode == 0, result.stdout + result.stderr
    assert """-e '{"vm_name": "alpha,beta"}'""" in result.stdout
    assert "playbooks/ssh_config.yml" in result.stdout


# A TYPE written as a word after the target. After one that takes VM names it
# would be a VM's name - make deploy configure created a VM named configure -
# and after one that does not, a goal make has no rule for, or runs: make
# vagrant destroy would delete the default VM after the Vagrant one.
@pytest.mark.parametrize(
    ("args", "error"),
    [
        pytest.param(
            ["deploy", "configure"],
            "configure among the VM names is a TYPE of make deploy: give it as make deploy TYPE=configure,"
            " and name a VM so with make deploy VM_NAME=configure",
            id="deploy",
        ),
        pytest.param(
            ["login", "alpha", "github"],
            "github among the VM names is a TYPE of make login: give it as make login TYPE=github",
            id="login",
        ),
        pytest.param(
            ["test", "integration"], "make test takes its part as TYPE=integration, not as a word after it", id="test"
        ),
        pytest.param(
            ["vagrant", "destroy"], "make vagrant takes its part as TYPE=destroy, not as a word after it", id="vagrant"
        ),
        pytest.param(["clean", "logs"], "make clean takes its part as TYPE=logs, not as a word after it", id="clean"),
    ],
)
def test_a_type_after_a_target_is_refused(tmp_path: Path, args: list[str], error: str) -> None:
    result = _make(*args, *_safe_overrides(tmp_path))
    assert result.returncode != 0
    assert error in result.stderr
    assert result.stdout == ""


# The targets an earlier Makefile had stop at once with what replaced them,
# rather than make's "No rule to make target", or - after a VM target - becoming
# a VM's name.
RENAMED = {
    "venv": "make install",
    "deps": "make install",
    "lock": "make install TYPE=lock",
    "vault-encrypt": "make vault",
    "vault-edit": "make vault",
    "logs-clean": "make clean TYPE=logs",
    "template": "make deploy TYPE=template",
    "provision": "make deploy TYPE=provision",
    "configure": "make deploy TYPE=configure",
    "check": "make deploy TYPE=check",
    "ssh-config": "make deploy TYPE=ssh-config",
    "claude-login": "make login TYPE=claude",
    "github-login": "make login TYPE=github",
    "tailscale-login": "make login TYPE=tailscale",
    "docs-serve": "make docs TYPE=serve",
    "docs-build": "make docs TYPE=build",
    "syntax": "make lint TYPE=syntax",
    "pre-commit": "make lint TYPE=hooks",
    "unit": "make test TYPE=unit",
    "molecule": "make test TYPE=molecule",
    "molecule-scenario": "make test TYPE=integration",
    "molecule-integration": "make test TYPE=integration",
    "molecule-provision": "make test TYPE=provision",
    "vagrant-up": "make vagrant",
    "vagrant-destroy": "make vagrant TYPE=destroy",
}


def test_every_replaced_target_is_listed() -> None:
    assert set(re.findall(r"(?m)^RENAMED_([a-z-]+)\s*:=", MAKEFILE.read_text())) == set(RENAMED)


@pytest.mark.parametrize(("old", "new"), RENAMED.items())
def test_a_replaced_target_names_its_replacement(tmp_path: Path, old: str, new: str) -> None:
    result = _make(old, "alpha", *_safe_overrides(tmp_path))
    assert result.returncode != 0
    assert f"make {old} is now: {new}" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (["deploy", "alpha", "syntax"], "syntax among the VM names was a make target - make syntax is now: make lint"),
        (["destroy", "configure"], "configure among the VM names was a make target - make configure is now:"),
        (["login", "claude-login"], "claude-login among the VM names was a make target"),
        (["list", "unit", "provision"], "provision unit among the VM names were make targets"),
    ],
)
def test_a_replaced_target_among_vm_names_is_refused(tmp_path: Path, args: list[str], error: str) -> None:
    # make deploy alpha syntax would otherwise create a VM named syntax, as the
    # old Makefile, for which syntax was a target, never did.
    result = _make(*args, *_safe_overrides(tmp_path))
    assert result.returncode != 0
    assert error in result.stderr
    assert f"name a VM so with make {args[0]} VM_NAME=" in result.stderr
    assert result.stdout == ""


def test_a_replaced_target_after_another_is_refused_too(tmp_path: Path) -> None:
    result = _make("lint", "syntax", *_safe_overrides(tmp_path))
    assert result.returncode != 0
    assert "make syntax is now: make lint TYPE=syntax" in result.stderr


def test_no_replaced_target_is_a_target_again() -> None:
    assert not set(RENAMED) & set(_public_targets())


# make help and the wiki read the `#: TYPE=` lines above each target; the
# Makefile checks TYPE against TYPES_<target>. The two are one list.
def _documented_options(target: str) -> list[str]:
    lines = MAKEFILE.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith(f"{target}: ") and "##" in line)
    options = []
    for line in reversed(lines[:index]):
        if line.startswith("#: "):
            options.insert(0, line.split()[1])
        elif (re.match(r"^[a-z-]+:", line) and not line.startswith(f"{target}:")) or line.startswith("# ---"):
            break
    return options


@pytest.mark.parametrize("target", sorted(_types()))
def test_each_type_a_target_takes_is_documented_above_it(target: str) -> None:
    documented = [option.removeprefix("TYPE=") for option in _documented_options(target) if option.startswith("TYPE=")]
    assert documented == _types()[target]


def test_help_lists_every_target_with_its_types() -> None:
    result = _make("help")
    assert result.returncode == 0, result.stderr
    for target in _public_targets():
        if target != "help":
            assert re.search(rf"(?m)^  {re.escape(target)}\b", result.stdout), target
    for target, kinds in _types().items():
        assert "TYPE=" + "|".join(kinds) in result.stdout, target
    assert "deploy [<name>...]" in result.stdout
    assert "code <name>" in result.stdout


# The sign-ins take several names, one VM after another (serial in the
# playbook), unlike code, which opens one window.
@pytest.mark.parametrize(("kind", "playbook"), LOGIN_TYPES.items())
def test_the_sign_ins_pass_several_vm_names(kind: str, playbook: str) -> None:
    result = _make("-n", "login", f"TYPE={kind}", "VM_NAME=alpha,beta")
    assert result.returncode == 0, result.stdout + result.stderr
    assert """-e '{"vm_name": "alpha,beta"}'""" in result.stdout
    assert playbook in result.stdout


def test_code_passes_one_vm_name(tmp_path: Path) -> None:
    result = _make("-n", "code", "VM_NAME=alpha")
    assert result.returncode == 0, result.stdout + result.stderr
    assert """-e '{"vm_name": "alpha"}'""" in result.stdout
    assert "playbooks/code.yml" in result.stdout


def test_code_refuses_no_vm_name(tmp_path: Path) -> None:
    result = _make("code", *_safe_overrides(tmp_path))
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "code opens one VM: make code VM_NAME=<name>" in output
    assert "ansible-playbook" not in output


def test_code_refuses_several_vm_names(tmp_path: Path) -> None:
    result = _make("code", "VM_NAME=alpha,beta", *_safe_overrides(tmp_path))
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "code opens one VM at a time, not [alpha,beta]" in output
    assert "ansible-playbook" not in output


def test_code_runs_the_name_check_before_anything_else(tmp_path: Path) -> None:
    result = _make("code", "VM_NAME=a;b", *_safe_overrides(tmp_path))
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert NAME_ERROR in output
    assert "ansible-playbook" not in output


# Run logs. A stand-in ansible-playbook reports the ANSIBLE_LOG_PATH it was
# given, which is all Ansible needs to write a log; Ansible itself is not run.
def _logging_overrides(tmp_path: Path) -> list[str]:
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    stub = venv / "ansible-playbook"
    stub.write_text('#!/bin/sh\necho "stub ANSIBLE_LOG_PATH=[$ANSIBLE_LOG_PATH]"\n')
    stub.chmod(0o755)
    _stand_in_galaxy(venv)
    vault = tmp_path / "vault.yml"
    vault.write_text(VAULT_HEADER)
    return [f"BIN={tmp_path / 'venv' / 'bin'}", f"VAULT_FILE={vault}", f"LOG_DIR={tmp_path / 'logs'}"]


def _stub_log_path(output: str) -> str:
    match = re.search(r"stub ANSIBLE_LOG_PATH=\[(.*)\]", output)
    assert match, output
    return match.group(1)


@pytest.mark.parametrize(
    ("args", "name"),
    [
        (["list", "VM_NAME=alpha"], "list"),
        (["deploy", "VM_NAME=alpha"], "deploy"),
        (["deploy", "VM_NAME=alpha", "TYPE=configure"], "deploy-configure"),
        (["deploy", "VM_NAME=alpha", "TAGS=claude_code"], "deploy-configure"),
        (["deploy", "TYPE=template"], "deploy-template"),
        (["login", "VM_NAME=alpha"], "login"),
        (["login", "VM_NAME=alpha", "TYPE=github"], "login-github"),
        (["destroy", "VM_NAME=alpha"], "destroy"),
    ],
)
def test_a_playbook_target_logs_its_run_to_its_own_file(tmp_path: Path, args: list[str], name: str) -> None:
    result = _make(*args, *_logging_overrides(tmp_path))
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    path = _stub_log_path(output)
    assert re.fullmatch(rf"{re.escape(str(tmp_path / 'logs'))}/\d{{8}}-\d{{6}}-{re.escape(name)}\.log", path), path
    assert f"Logging this run to {path}" in output
    # Never the VM name: nothing typed reaches a shell before vm-name-check.
    assert "alpha" not in Path(path).name
    assert oct((tmp_path / "logs").stat().st_mode & 0o777) == "0o700"


def test_an_empty_log_dir_turns_logging_off(tmp_path: Path) -> None:
    overrides = [o for o in _logging_overrides(tmp_path) if not o.startswith("LOG_DIR=")]
    result = _make("list", *overrides, "LOG_DIR=")
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert _stub_log_path(output) == ""
    assert "Logging this run" not in output


def test_an_ansible_log_path_of_your_own_is_kept(tmp_path: Path) -> None:
    mine = tmp_path / "elsewhere" / "ansible.log"
    result = _make("list", *_logging_overrides(tmp_path), f"ANSIBLE_LOG_PATH={mine}")
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert _stub_log_path(output) == str(mine)


def test_old_run_logs_are_pruned_and_nothing_else_is(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    old, recent, notes = logs / "20260101-000000-deploy.log", logs / "20261001-000000-list.log", logs / "notes.log"
    for path in (old, recent, notes):
        path.write_text("x")
    month_ago = time.time() - 30 * 86400
    for path in (old, notes):
        os.utime(path, (month_ago, month_ago))

    result = _make("list", *_logging_overrides(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert not old.exists()
    assert recent.exists()
    # Not named the way the Makefile names its logs, so not its to delete.
    assert notes.exists()


def test_a_retention_that_is_not_a_number_prunes_nothing(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    old = logs / "20260101-000000-deploy.log"
    old.write_text("x")
    month_ago = time.time() - 30 * 86400
    os.utime(old, (month_ago, month_ago))
    result = _make("list", *_logging_overrides(tmp_path), "LOG_RETENTION_DAYS=14;rm")
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "LOG_RETENTION_DAYS must be a number of days" in output
    assert old.exists()


def test_logs_lists_and_clean_type_logs_deletes_only_run_logs(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    assert "No run logs in" in _make("logs", f"LOG_DIR={logs}").stdout
    logs.mkdir()
    run_log, notes = logs / "20261007-203015-deploy.log", logs / "notes.txt"
    run_log.write_text("x")
    notes.write_text("x")

    assert _make("logs", f"LOG_DIR={logs}").stdout.strip() == str(run_log)
    cleaned = _make("clean", "TYPE=logs", f"LOG_DIR={logs}")
    assert cleaned.returncode == 0, cleaned.stdout + cleaned.stderr
    assert "Deleted 1 run log(s)" in cleaned.stdout
    assert not run_log.exists()
    assert notes.exists()


# PROJECT: one folder name, as GitHub names a repository - up to 100 ASCII
# letters, digits, ".", "-" and "_". Only `make code` reads it.
PROJECT_ERROR = "PROJECT may contain only letters, digits and . _ -"


@pytest.mark.parametrize(
    "value", ["discord-music-bot", "ParkBnb", "djangoTest", "omkar_kv", "site.github.io", "x", "a" * 100, ""]
)
def test_project_name_check_accepts_repository_names(value: str) -> None:
    result = _make("project-name-check", f"PROJECT={value}")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("value", "error"),
    [
        pytest.param("../etc", PROJECT_ERROR, id="parent-path"),
        pytest.param("org/repo", PROJECT_ERROR, id="slash"),
        pytest.param("/etc", PROJECT_ERROR, id="absolute"),
        pytest.param(".", "PROJECT names one project folder, not [.]", id="dot"),
        pytest.param("..", "PROJECT names one project folder, not [..]", id="dot-dot"),
        pytest.param("a b", PROJECT_ERROR, id="space"),
        pytest.param("a'b", PROJECT_ERROR, id="single-quote"),
        pytest.param('a"b', PROJECT_ERROR, id="double-quote"),
        pytest.param("a;b", PROJECT_ERROR, id="semicolon"),
        pytest.param("$(x)", PROJECT_ERROR, id="make-expansion"),
        pytest.param("a,b", PROJECT_ERROR, id="comma"),
        pytest.param("repo\nother", PROJECT_ERROR, id="newline"),
        pytest.param("répo", PROJECT_ERROR, id="non-ascii"),
        pytest.param("a" * 101, "longer than the 100 characters", id="too-long"),
    ],
)
def test_project_name_check_rejects_anything_else(value: str, error: str) -> None:
    result = _make("project-name-check", f"PROJECT={value}")
    assert result.returncode != 0, "accepted " + repr(value)
    assert error in result.stdout + result.stderr


def test_code_passes_project_as_its_own_json_extra_var() -> None:
    result = _make("-n", "code", "VM_NAME=alpha", "PROJECT=discord-music-bot")
    assert result.returncode == 0, result.stdout + result.stderr
    assert """-e '{"vm_name": "alpha"}' -e '{"vm_project": "discord-music-bot"}'""" in result.stdout


def test_code_without_project_passes_none() -> None:
    result = _make("-n", "code", "VM_NAME=alpha")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "vm_project" not in result.stdout


def test_code_refuses_a_bad_project_before_the_playbook(tmp_path: Path) -> None:
    result = _make("code", "VM_NAME=alpha", "PROJECT=../etc", *_safe_overrides(tmp_path))
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert PROJECT_ERROR in output
    assert "ansible-playbook" not in output


@pytest.mark.parametrize("args", [["deploy"], ["deploy", "TYPE=configure"], ["login"], ["list"]])
def test_only_code_reads_project(args: list[str]) -> None:
    result = _make("-n", *args, "VM_NAME=alpha", "PROJECT=discord-music-bot")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "vm_project" not in result.stdout


# A run with no terminal - `make deploy </dev/null`, an IDE task, an agent's
# shell - has nobody to approve a sign-in: the sign-ins' browser and clipboard
# steps are turned off there, and make deploy's sign-ins, which start by
# themselves, are skipped rather than wait for a browser nobody watches. The
# recipe runs for real, with VENV pointed at a stand-in ansible-playbook that
# only records its arguments.
def _stand_in(venv: Path, tool: str, record: Path) -> None:
    """A tool in venv/bin that only records its arguments, one per line, into record."""
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    stand_in = venv / "bin" / tool
    stand_in.write_text(f'#!/bin/sh\nfor arg in "$@"; do printf "%s\\n" "$arg"; done > {record}\n')
    stand_in.chmod(0o755)


def _recorded_args(tmp_path: Path, args: list[str], stdin) -> list[str]:
    venv = tmp_path / "venv"
    record = tmp_path / "args"
    _stand_in(venv, "ansible-playbook", record)
    _stand_in_galaxy(venv / "bin")
    overrides = [f"BIN={venv / 'bin'}", *_safe_overrides(tmp_path)[1:]]
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"), "LANG": "en_US.UTF-8"}
    result = subprocess.run(
        [MAKE, *args, "VM_NAME=alpha", *overrides],
        cwd=REPO,
        env=env,
        stdin=stdin,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return record.read_text().splitlines()


SIGNING_IN = [["deploy"], ["login"], ["login", "TYPE=claude"], ["login", "TYPE=github"], ["login", "TYPE=tailscale"]]


@pytest.mark.parametrize("args", SIGNING_IN)
def test_a_run_with_no_terminal_turns_the_desktop_steps_and_deploy_s_sign_ins_off(
    tmp_path: Path, args: list[str]
) -> None:
    recorded = _recorded_args(tmp_path, args, subprocess.DEVNULL)
    off = '{"login_desktop":false,"deploy_sign_in":false}'
    assert off in recorded and recorded[recorded.index(off) - 1] == "-e", recorded


@pytest.mark.parametrize("args", SIGNING_IN)
def test_a_run_at_a_terminal_leaves_the_desktop_steps_on(tmp_path: Path, args: list[str]) -> None:
    import pty

    primary, secondary = pty.openpty()
    try:
        recorded = _recorded_args(tmp_path, args, secondary)
    finally:
        os.close(primary)
        os.close(secondary)
    assert not any("login_desktop" in arg for arg in recorded), recorded


# make vault: the file's state says which - a plaintext vault is encrypted, an
# encrypted one edited, a missing one sent to make init. A stand-in
# ansible-vault records what it was asked.
@pytest.mark.parametrize(
    ("content", "action"), [("vault_proxmox_api_token_secret: x\n", "encrypt"), (VAULT_HEADER, "edit")]
)
def test_vault_encrypts_a_plaintext_vault_and_edits_an_encrypted_one(tmp_path: Path, content: str, action: str) -> None:
    venv, record, vault = tmp_path / "venv", tmp_path / "args", tmp_path / "vault.yml"
    _stand_in(venv, "ansible-vault", record)
    vault.write_text(content)
    result = _make("vault", f"BIN={venv / 'bin'}", f"VAULT_FILE={vault}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert record.read_text().splitlines() == [action, str(vault)]


def test_vault_names_make_init_when_the_vault_is_missing(tmp_path: Path) -> None:
    result = _make("vault", f"BIN={tmp_path}", f"VAULT_FILE={tmp_path / 'none.yml'}")
    assert result.returncode != 0
    assert "missing. Run: make init" in result.stdout


# make install: the locked packages into PYTHON's own environment, then the
# collections; TYPE=lock the lock alone. make init: make install, then the local
# config and the git hook. The project keeps no environment of its own.
ENVIRONMENT = sysconfig.get_path("scripts")


def test_install_syncs_the_lock_into_python_s_own_environment() -> None:
    result = _make("-n", "install", f"PYTHON={sys.executable}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'UV_PROJECT_ENVIRONMENT="{sys.prefix}"' in result.stdout
    # --inexact: the environment is the user's, and nothing in it the lock does not list is removed.
    assert f'sync --locked --inexact --python "{sys.executable}"' in result.stdout
    assert f"{ENVIRONMENT}/ansible-galaxy collection install -r requirements.yml" in result.stdout
    assert "pre-commit install" not in result.stdout


def test_install_type_lock_only_writes_the_lock() -> None:
    result = _make("-n", "install", "TYPE=lock", f"PYTHON={sys.executable}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"(?m) lock$", result.stdout), result.stdout
    assert "sync" not in result.stdout and "ansible-galaxy" not in result.stdout


def test_init_installs_first_then_the_config_the_git_hook_and_the_questions() -> None:
    result = _make("-n", "init", f"PYTHON={sys.executable}")
    assert result.returncode == 0, result.stdout + result.stderr
    parts = ("sync --locked", "pre-commit install", "hosts.yml", "playbooks/init_settings.yml")
    sync, hook, config, questions = (result.stdout.index(part) for part in parts)
    assert sync < hook < config < questions
    assert "-i playbooks/inventory/init.yml" in result.stdout


def test_init_checks_its_values_first_and_never_the_vault() -> None:
    # The vault is plaintext on a fresh checkout: init encrypts it.
    prerequisites = _prerequisites("init")
    assert prerequisites[0] == "init-args-check" and "vault-check" not in prerequisites, prerequisites
    # The log's directory before install, whose ansible-galaxy writes to the log too.
    assert prerequisites.index("log-setup") < prerequisites.index("install"), prerequisites
    assert "init" in re.search(r"(?m)^LOGGED_TARGETS\s*:=(.*)$", MAKEFILE.read_text()).group(1).split()


# make init runs for real here, every tool a stand-in that records its
# arguments: uv, pre-commit, ansible-galaxy and ansible-playbook. INIT_FILES
# points the copy at files in tmp_path, never the checkout's.
def _init_run(tmp_path: Path, *args: str, stdin=subprocess.DEVNULL, env: dict | None = None):
    venv = tmp_path / "venv"
    record = tmp_path / "args"
    _stand_in(venv, "ansible-playbook", record)
    _stand_in(venv, "pre-commit", tmp_path / "pre-commit-args")
    _stand_in(venv, "uv", tmp_path / "uv-args")
    _stand_in_galaxy(venv / "bin")
    copies = []
    for name in ("hosts.yml", "local.yml", "vault.yml"):
        (tmp_path / f"{name}.example").write_text(f"# {name}\n")
        copies.append(str(tmp_path / name))
    overrides = [f"BIN={venv / 'bin'}", f"UV={venv / 'bin' / 'uv'}", f"INIT_FILES={' '.join(copies)}"]
    overrides.append(f"LOG_DIR={tmp_path / 'logs'}")
    clean = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"), "LANG": "en_US.UTF-8"}
    result = subprocess.run(
        [MAKE, "init", *overrides, *args],
        cwd=REPO,
        env=clean | (env or {}),
        stdin=stdin,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return result, (record.read_text().splitlines() if record.exists() else None)


def _at_a_terminal(tmp_path: Path, *args: str, env: dict | None = None):
    import pty

    primary, secondary = pty.openpty()
    try:
        return _init_run(tmp_path, *args, stdin=secondary, env=env)
    finally:
        os.close(primary)
        os.close(secondary)


ASK = '{"init_ask":true}'


def test_init_asks_only_at_a_terminal(tmp_path: Path) -> None:
    result, recorded = _init_run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert recorded[-1] == "playbooks/init_settings.yml" and ASK not in recorded, recorded
    assert "Created" in result.stdout and all((tmp_path / name).exists() for name in ("hosts.yml", "vault.yml"))
    result, recorded = _at_a_terminal(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert recorded[recorded.index(ASK) - 1] == "-e", recorded
    # Copied once, said once.
    assert "Created" not in result.stdout


def test_prompt_no_asks_nothing_even_at_a_terminal(tmp_path: Path) -> None:
    result, recorded = _at_a_terminal(tmp_path, "PROMPT=no")
    assert result.returncode == 0, result.stdout + result.stderr
    assert ASK not in recorded, recorded


def test_init_logs_its_run(tmp_path: Path) -> None:
    result, _ = _init_run(tmp_path)
    assert re.search(r"Logging this run to .*/logs/\d{8}-\d{6}-init\.log", result.stdout), result.stdout


def test_the_values_given_reach_the_playbook_as_one_json_extra_var(tmp_path: Path) -> None:
    given = ["HOST=pve.lan", "TOKEN_USER=deploy@pam", "TOKEN_NAME=claude", "GITHUB_USER=octo-cat", "TAILSCALE=off"]
    result, recorded = _init_run(tmp_path, *given, "TOKEN_SECRET_FILE=~/.config/pve-token")
    assert result.returncode == 0, result.stdout + result.stderr
    passed = [json.loads(arg) for arg in recorded if arg.startswith('{"init_given"')]
    assert passed == [
        {
            "init_given": {
                "host": "pve.lan",
                "token_user": "deploy@pam",
                "token_name": "claude",
                "token_secret_file": "~/.config/pve-token",
                "github_user": "octo-cat",
                "tailscale": "off",
            }
        }
    ]


def test_no_value_given_passes_no_init_given(tmp_path: Path) -> None:
    result, recorded = _init_run(tmp_path, "HOST=")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any("init_given" in arg for arg in recorded), recorded


def test_an_exported_value_is_not_given(tmp_path: Path) -> None:
    result, recorded = _init_run(tmp_path, env={"HOST": "exported.lan", "GITHUB_USER": "exported"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any("exported" in arg for arg in recorded), recorded


@pytest.mark.parametrize("name", ["HOST", "TOKEN_USER", "TOKEN_NAME", "TOKEN_SECRET_FILE", "GITHUB_USER"])
@pytest.mark.parametrize("value", ["a'b", 'a"b', "a b", "$(x)", "a`b", "a;b", "a\nb", "ålpha"])
def test_a_given_value_outside_its_characters_is_refused_before_anything_runs(
    tmp_path: Path, name: str, value: str
) -> None:
    result, recorded = _init_run(tmp_path, f"{name}={value}")
    assert result.returncode != 0
    assert f"error: {name} may contain only" in result.stdout
    assert recorded is None and not (tmp_path / "uv-args").exists()


def test_prompt_is_yes_or_no(tmp_path: Path) -> None:
    result, recorded = _init_run(tmp_path, "PROMPT=maybe")
    assert result.returncode != 0 and "PROMPT is yes or no" in result.stdout and recorded is None


def test_tailscale_is_on_or_off(tmp_path: Path) -> None:
    result, recorded = _init_run(tmp_path, "TAILSCALE=yes")
    assert result.returncode != 0 and "TAILSCALE is on or off" in result.stdout and recorded is None


@pytest.mark.parametrize("name", ["TOKEN_SECRET", "CLAUDE_ON_PROXMOX_TOKEN_SECRET"])
def test_the_token_secret_is_refused_on_the_command_line(tmp_path: Path, name: str) -> None:
    result, recorded = _init_run(tmp_path, f"{name}=11111111-2222-3333-4444-555555555555")
    assert result.returncode != 0
    assert "the token secret never goes on make's command line" in result.stderr
    assert "11111111-2222" not in result.stdout + result.stderr and recorded is None


def test_the_secret_in_the_environment_reaches_no_argument(tmp_path: Path) -> None:
    secret = "11111111-2222-3333-4444-555555555555"
    result, recorded = _init_run(tmp_path, "HOST=pve.lan", env={"CLAUDE_ON_PROXMOX_TOKEN_SECRET": secret})
    assert result.returncode == 0, result.stdout + result.stderr
    assert secret not in "\n".join(recorded) + result.stdout + result.stderr
    dry = _make("-n", "init", f"PYTHON={sys.executable}", env={"CLAUDE_ON_PROXMOX_TOKEN_SECRET": secret})
    assert secret not in dry.stdout + dry.stderr


def test_install_refuses_a_python_that_is_no_virtualenv(tmp_path: Path) -> None:
    # The sync makes the environment match the lock, removing anything else:
    # never pyenv's own Python, or the system's.
    not_a_venv = tmp_path / "python3"
    not_a_venv.write_text("#!/bin/sh\nexit 1\n")
    not_a_venv.chmod(0o755)
    uv = tmp_path / "uv"
    uv.write_text(f"#!/bin/sh\ntouch {tmp_path / 'uv-ran'}\n")
    uv.chmod(0o755)
    result = _make("install", f"PYTHON={not_a_venv}", f"UV={uv}")
    assert result.returncode != 0
    assert f"error: [{not_a_venv}] is not the Python of a virtualenv" in result.stdout
    assert not (tmp_path / "uv-ran").exists()


def test_install_refuses_the_base_python_of_the_tests_virtualenv() -> None:
    base = Path(sys.base_prefix) / "bin" / "python3"
    if sys.prefix == sys.base_prefix or not base.exists():
        pytest.skip("the tests do not run in a virtualenv with a base python3 beside it")
    refused = _make("install", f"PYTHON={base}", "UV=false")
    assert refused.returncode != 0
    assert "is not the Python of a virtualenv" in refused.stdout


def test_targets_run_the_tools_of_python_s_environment() -> None:
    result = _make("-n", "list", f"PYTHON={sys.executable}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{ENVIRONMENT}/ansible-playbook " in _playbook_line(result)


def test_a_bin_in_the_environment_is_not_the_tools(tmp_path: Path) -> None:
    result = _make("-n", "list", f"PYTHON={sys.executable}", env={"BIN": str(tmp_path)})
    assert f"{ENVIRONMENT}/ansible-playbook " in _playbook_line(result)


def test_help_and_a_refusal_start_no_python(tmp_path: Path) -> None:
    ran = tmp_path / "ran"
    python = tmp_path / "python3"
    python.write_text(f"#!/bin/sh\ntouch {ran}\n")
    python.chmod(0o755)
    assert _make("help", f"PYTHON={python}").returncode == 0
    assert _make("deploy", "TYPE=bogus", f"PYTHON={python}").returncode != 0
    assert not ran.exists()


def test_a_target_without_the_tools_says_make_install(tmp_path: Path) -> None:
    overrides = [o for o in _safe_overrides(tmp_path) if not o.startswith("BIN=")]
    result = _make("list", f"BIN={tmp_path / 'empty'}", *overrides)
    assert result.returncode != 0
    assert f"no ansible-galaxy in [{tmp_path / 'empty'}]" in result.stdout
    assert "Run: make install" in result.stdout


def _not_a_virtualenv(tmp_path: Path) -> Path:
    python = tmp_path / "python3"
    python.write_text("#!/bin/sh\nexit 1\n")
    python.chmod(0o755)
    return python


@pytest.mark.parametrize("args", [["list"], ["lint", "TYPE=yaml"], ["docs"], ["vault"], ["test", "TYPE=unit"]])
def test_every_target_that_runs_a_tool_refuses_a_python_that_is_no_virtualenv(tmp_path: Path, args: list[str]) -> None:
    # Its scripts directory would be /usr/local/bin or the like, where a system-wide Ansible lives.
    overrides = [o for o in _safe_overrides(tmp_path) if not o.startswith("BIN=")]
    python = _not_a_virtualenv(tmp_path)
    result = _make(*args, f"PYTHON={python}", *overrides)
    assert result.returncode != 0
    assert (
        f"error: [{python}] is not the Python of a virtualenv, and this project runs its tools only from"
        in result.stdout
    )
    for tool in ("ansible-playbook", "yamllint", "zensical", "ansible-vault", "pytest"):
        assert f"bin/{tool}" not in result.stdout


def test_a_non_virtualenv_python_gives_no_bin() -> None:
    base = Path(sys.base_prefix) / "bin" / "python3"
    if sys.prefix == sys.base_prefix or not base.exists():
        pytest.skip("the tests do not run in a virtualenv with a base python3 beside it")
    line = _playbook_line(_make("-n", "list", f"PYTHON={base}"))
    assert line.startswith("/ansible-playbook "), line


# TYPE, TAGS, ROLE and lint's SKIP come from the command line only: names a
# shell may export for something else.
@pytest.mark.parametrize(
    ("args", "env", "expect", "never"),
    [
        (["-n", "deploy", "VM_NAME=alpha"], {"TYPE": "check"}, " deploy.yml", "--check"),
        (["-n", "deploy", "VM_NAME=alpha"], {"TAGS": "claude_code"}, " deploy.yml", "--tags"),
        (["-n", "test"], {"ROLE": "common"}, "yamllint --strict", "for role in common; do"),
    ],
)
def test_an_exported_type_tags_or_role_changes_nothing(args, env, expect, never) -> None:
    result = _make(*args, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert expect in result.stdout and never not in result.stdout


def test_an_exported_skip_is_not_lint_s(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    record = tmp_path / "skip"
    stand_in = venv / "bin" / "pre-commit"
    stand_in.write_text(f'#!/bin/sh\nprintf "%s" "$SKIP" > {record}\n')
    stand_in.chmod(0o755)
    result = _make("lint", "TYPE=hooks", f"BIN={venv / 'bin'}", env={"SKIP": "gitleaks"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert record.read_text() == "yamllint,ansible-lint,ruff-check,ruff-format,gitleaks"


def test_molecule_roles_on_the_command_line_is_not_the_role_list() -> None:
    result = _make("-n", "test", "TYPE=role", "MOLECULE_ROLES=common bogus;echo")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "bogus" not in result.stdout
    every = re.search(r"(?m)^ROLES\s*:=\s*(.*)$", MAKEFILE.read_text()).group(1).strip()
    assert f"for role in {every}; do" in result.stdout


# tools/env-run: what the git hooks and the Vagrantfile run the tools through.
ENV_RUN = REPO / "tools" / "env-run"


def _env_run(*args: str, python: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"), "PYTHON": python}
    return subprocess.run([str(ENV_RUN), *args], env=env, capture_output=True, text=True, check=False, timeout=60)


def test_env_run_runs_a_tool_of_python_s_environment() -> None:
    result = _env_run("python", "-c", "import sys; print(sys.prefix)", python=sys.executable)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == sys.prefix


def test_env_run_refuses_a_python_that_is_no_virtualenv(tmp_path: Path) -> None:
    python = _not_a_virtualenv(tmp_path)
    result = _env_run("yamllint", "--version", python=str(python))
    assert result.returncode != 0
    assert f"error: [{python}] is not the Python of a virtualenv" in result.stderr


def test_env_run_names_a_tool_the_environment_lacks() -> None:
    result = _env_run("no-such-tool", python=sys.executable)
    assert result.returncode != 0
    assert f"error: no no-such-tool in [{ENVIRONMENT}]. Run: make install" in result.stderr


def test_every_local_hook_runs_through_env_run() -> None:
    import yaml

    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text())
    local = [hook for repo in config["repos"] if repo["repo"] == "local" for hook in repo["hooks"]]
    assert local
    assert all(hook["entry"].startswith("tools/env-run ") for hook in local), [hook["entry"] for hook in local]
    assert os.access(ENV_RUN, os.X_OK)


def test_the_vagrantfile_never_falls_back_to_path_s_ansible_playbook() -> None:
    text = (REPO / "Vagrantfile").read_text()
    assert 'ENV.fetch("ANSIBLE_PLAYBOOK") do' in text
    assert "sys.prefix != sys.base_prefix" in text
    assert 'ENV.fetch("ANSIBLE_PLAYBOOK", "ansible-playbook")' not in text


def test_make_vagrant_hands_the_vagrantfile_the_environment_s_ansible_playbook(tmp_path: Path) -> None:
    stand_ins = tmp_path / "stand-ins"
    stand_ins.mkdir()
    record = tmp_path / "playbook"
    vagrant = stand_ins / "vagrant"
    vagrant.write_text(f'#!/bin/sh\nprintf "%s" "$ANSIBLE_PLAYBOOK" > {record}\n')
    vagrant.chmod(0o755)
    env = {"PATH": f"{stand_ins}:{os.environ['PATH']}"}
    result = _make("vagrant", f"BIN={tmp_path / 'env' / 'bin'}", "LOG_DIR=", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert record.read_text() == f"{tmp_path / 'env' / 'bin'}/ansible-playbook"


def test_clean_never_touches_the_python_environment() -> None:
    result = _make("-n", "clean", f"PYTHON={sys.executable}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert ".venv" not in result.stdout and sys.prefix not in result.stdout


# make lint runs every linter; TYPE runs one. The hooks part skips the hooks
# lint runs itself, unless SKIP is given.
LINTERS = {
    "lock": "lock --check",
    "yaml": "yamllint --strict .",
    "ansible": "/ansible-lint",
    "python": "ruff format --check .",
    "syntax": "--syntax-check",
    "hooks": "pre-commit run --all-files",
}


@pytest.mark.parametrize("kind", ["", *LINTERS])
def test_lint_runs_every_linter_or_the_one_its_type_names(kind: str) -> None:
    result = _make("-n", "lint", *([f"TYPE={kind}"] if kind else []))
    assert result.returncode == 0, result.stdout + result.stderr
    for name, command in LINTERS.items():
        assert (command in result.stdout) == (not kind or name == kind), (name, result.stdout)


@pytest.mark.parametrize(
    ("given", "skip"), [([], "yamllint,ansible-lint,ruff-check,ruff-format,gitleaks"), (["SKIP="], "")]
)
def test_lint_s_hooks_skip_what_lint_runs_itself(tmp_path: Path, given: list[str], skip: str) -> None:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    record = tmp_path / "skip"
    stand_in = venv / "bin" / "pre-commit"
    stand_in.write_text(f'#!/bin/sh\nprintf "%s" "$SKIP" > {record}\n')
    stand_in.chmod(0o755)
    result = _make("lint", "TYPE=hooks", f"BIN={venv / 'bin'}", *given)
    assert result.returncode == 0, result.stdout + result.stderr
    assert record.read_text() == skip


# make test: lint, then every test; TYPE runs one kind and skips lint, and
# ROLE some roles' scenarios.
TESTS = {
    "unit": "/pytest",
    "role": "===== molecule:",
    "integration": "molecule test -s configure",
    "provision": "molecule test -s provision",
}


@pytest.mark.parametrize(
    ("args", "runs"),
    [
        ([], ["lint", "unit", "role", "integration", "provision"]),
        (["TYPE=unit"], ["unit"]),
        (["TYPE=molecule"], ["role", "integration", "provision"]),
        (["TYPE=role"], ["role"]),
        (["TYPE=integration"], ["integration"]),
        (["TYPE=provision"], ["provision"]),
        (["ROLE=common"], ["role"]),
    ],
)
def test_test_runs_lint_and_every_test_or_what_its_type_names(args: list[str], runs: list[str]) -> None:
    result = _make("-n", "test", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("yamllint --strict" in result.stdout) == ("lint" in runs)
    for name, command in TESTS.items():
        assert (command in result.stdout) == (name in runs), (name, result.stdout)


@pytest.mark.parametrize(
    ("role", "roles"), [("common", "common"), ("common,claude_code", "common claude_code"), ("", None)]
)
def test_role_narrows_the_role_scenarios(role: str, roles: str | None) -> None:
    result = _make("-n", "test", "TYPE=role", *([f"ROLE={role}"] if role else []))
    assert result.returncode == 0, result.stdout + result.stderr
    every = re.search(r"(?m)^ROLES\s*:=\s*(.*)$", MAKEFILE.read_text()).group(1).strip()
    assert f"for role in {roles or every}; do" in result.stdout


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (["ROLE=bogus"], "ROLE may name only common, vscode_server"),
        (["ROLE=../x"], "ROLE may name only"),
        (
            ["ROLE=common", "TYPE=unit"],
            "ROLE narrows the role scenarios, which TYPE=unit does not run: make test ROLE=common",
        ),
    ],
)
def test_role_is_checked_before_anything_runs(tmp_path: Path, args: list[str], error: str) -> None:
    result = _make("test", *args, *_safe_overrides(tmp_path))
    assert result.returncode != 0
    assert error in result.stderr
    assert result.stdout == ""


def test_clean_type_logs_removes_nothing_but_the_logs() -> None:
    result = _make("-n", "clean", "TYPE=logs")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "rm -f" in result.stdout
    assert "rm -rf" not in result.stdout


@pytest.mark.parametrize(
    ("args", "command"), [([], "vagrant up --provision"), (["TYPE=destroy"], "vagrant destroy -f")]
)
def test_vagrant_brings_the_vm_up_or_destroys_it(args: list[str], command: str) -> None:
    result = _make("-n", "vagrant", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert command in result.stdout
    assert ("Logging this run" in result.stdout or "ANSIBLE_LOG_PATH" in result.stdout) == (not args)


@pytest.mark.parametrize(
    ("args", "command"),
    [([], "tools/docs.py"), (["TYPE=serve"], "zensical serve"), (["TYPE=build"], "zensical build --strict")],
)
def test_docs_regenerates_the_pages_or_serves_or_builds_the_site(args: list[str], command: str) -> None:
    result = _make("-n", "docs", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert command in result.stdout
