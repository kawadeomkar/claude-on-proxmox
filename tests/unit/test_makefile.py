"""The Makefile's safety guards must keep working, or a regression ships green.

Four guards stand between `make <target> VM_NAME=...` and the playbooks, and
nothing but `make` itself exercises them:

- `vault-check` refuses to run a playbook without vault.yml (with the `make
  init` hint; the playbook used to die on an undefined variable instead) or
  while it is still plaintext.
- `vm-name-check` refuses a VM_NAME that could break out of, or reshape, the
  JSON VM_ARGS passes with `-e` (a quote, whitespace, a shell metacharacter,
  an embedded newline that YAML would fold into a space).
- VM_ARGS passes the names as one JSON extra-var. `-e vm_name=$(VM_NAME)`
  splits on whitespace and once silently provisioned half a fleet.
- `deploy` refuses TAGS, which would skip the untagged template and VM work
  and exit 0 having done nothing.

`make -n` prints a recipe without running it, which is enough to see what
`provision` would pass. The TAGS refusal and the prerequisite wiring are shell
lines, which -n does not run, so those tests run `make` for real with VENV
pointed at a directory holding only a stand-in ansible-galaxy and VAULT_FILE at
a stub that only looks encrypted: nothing could reach an ansible-playbook even
if a guard regressed, and vault-check and collections-check, which every
playbook target runs, let the run reach the guard under test.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MAKEFILE = REPO / "Makefile"
MAKE = shutil.which("make")
PLAYBOOK_TARGETS = [
    "template",
    "provision",
    "list",
    "configure",
    "deploy",
    "destroy",
    "claude-login",
    "github-login",
    "tailscale-login",
    "check",
    "ssh-config",
    "code",
]
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
    return [f"VENV={tmp_path / 'no-venv'}", f"VAULT_FILE={vault}", f"LOG_DIR={tmp_path / 'logs'}"]


def test_vault_check_accepts_an_encrypted_vault(tmp_path: Path) -> None:
    result = _make("vault-check", *_safe_overrides(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


def test_vault_check_refuses_a_plaintext_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault.yml"
    vault.write_text("vault_proxmox_api_token_secret: not-a-real-secret\n")
    result = _make("vault-check", f"VAULT_FILE={vault}")
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert f"{vault} is not encrypted. Run: make vault-encrypt" in output


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
    result = _make("list", f"VENV={tmp_path / 'no-venv'}", f"VAULT_FILE={vault}", f"LOG_DIR={tmp_path / 'logs'}")
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "missing. Run: make init" in output
    assert "ansible-playbook" not in output


# collections-check: every checkout on a machine shares ~/.ansible/collections,
# so a run whose collections differ from requirements.yml's pins is refused,
# naming make deps, before its playbook.
def test_collections_check_asks_galaxy_offline_whether_the_pins_are_installed(tmp_path: Path) -> None:
    record = _stand_in_galaxy(tmp_path / "venv" / "bin")
    result = _make("collections-check", f"VENV={tmp_path / 'venv'}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert record.read_text().splitlines() == ["collection", "install", "-r", "requirements.yml", "--offline"]


def test_a_playbook_target_stops_at_collections_that_are_not_the_pins(tmp_path: Path) -> None:
    overrides = _safe_overrides(tmp_path)
    _stand_in_galaxy(tmp_path / "no-venv" / "bin", rc=1)
    result = _make("list", *overrides)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "are not the ones requirements.yml pins. Run: make deps" in output
    assert "ansible-playbook" not in output


def _prerequisites(target: str) -> list[str]:
    line = next(line for line in MAKEFILE.read_text().splitlines() if line.startswith(f"{target}: ") and "##" in line)
    return line.split(":", 1)[1].split("##", 1)[0].split()


@pytest.mark.parametrize("target", [*PLAYBOOK_TARGETS, "molecule", "molecule-scenario"])
def test_every_target_that_runs_ansible_checks_the_collections_first(target: str) -> None:
    prerequisites = _prerequisites(target)
    assert "collections-check" in prerequisites, prerequisites
    # After what checks the input, before the log of a run it may refuse.
    assert prerequisites[-2:] == ["collections-check", "log-setup"], prerequisites


def test_the_targets_checked_are_every_playbook_target() -> None:
    # Every target whose recipe runs ansible-playbook from the virtualenv, but
    # syntax, which only parses the playbooks.
    text = MAKEFILE.read_text()
    running = set(re.findall(r"(?m)^([a-z-]+):[^\n]*\n(?:\t[^\n]*\n)*?\t[^\n]*\$\(BIN\)/ansible-playbook", text))
    assert running - {"syntax"} == set(PLAYBOOK_TARGETS), running


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
    result = _make("-n", "provision", "VM_NAME=alpha,beta")
    assert result.returncode == 0, result.stdout + result.stderr
    assert """-e '{"vm_name": "alpha,beta"}'""" in result.stdout
    assert "playbooks/provision.yml" in result.stdout


def test_provision_without_vm_name_passes_no_vm_name_at_all() -> None:
    result = _make("-n", "provision")
    assert result.returncode == 0, result.stdout + result.stderr
    assert """-e '{"vm_name\"""" not in result.stdout
    assert "playbooks/provision.yml" in result.stdout


def test_provision_runs_the_name_check_before_the_playbook(tmp_path: Path) -> None:
    result = _make("provision", "VM_NAME=a;b", *_safe_overrides(tmp_path))
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert NAME_ERROR in output
    assert "ansible-playbook" not in output


# Every target that takes VM_NAME also takes the names after it: make reads
# each as a goal, which the Makefile answers with a rule that does nothing once
# it has refused a word that is a target, and hands the words to vm-name-check
# as VM_NAME. Each target, and the playbook it runs.
NAMED_TARGETS = {
    "provision": "playbooks/provision.yml",
    "list": "playbooks/list.yml",
    "configure": "playbooks/configure.yml",
    "deploy": "deploy.yml",
    "destroy": "playbooks/destroy.yml",
    "claude-login": "playbooks/claude_login.yml",
    "github-login": "playbooks/github_login.yml",
    "tailscale-login": "playbooks/tailscale_login.yml",
    "check": "playbooks/configure.yml",
    "ssh-config": "playbooks/ssh_config.yml",
    "code": "playbooks/code.yml",
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


def test_deploy_refuses_tags(tmp_path: Path) -> None:
    result = _make("deploy", "TAGS=claude_code", *_safe_overrides(tmp_path))
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "deploy takes no TAGS" in output
    assert "make configure VM_NAME=<name> TAGS=claude_code" in output
    assert "ansible-playbook" not in output


# ssh-config takes VM_NAME like every fleet target; code takes exactly one.
def test_ssh_config_passes_vm_name_as_json() -> None:
    result = _make("-n", "ssh-config", "VM_NAME=alpha,beta")
    assert result.returncode == 0, result.stdout + result.stderr
    assert """-e '{"vm_name": "alpha,beta"}'""" in result.stdout
    assert "playbooks/ssh_config.yml" in result.stdout


# The sign-ins take several names, one VM after another (serial in the
# playbook), unlike code, which opens one window.
@pytest.mark.parametrize(
    ("target", "playbook"),
    [("claude-login", "claude_login"), ("github-login", "github_login"), ("tailscale-login", "tailscale_login")],
)
def test_the_sign_ins_pass_several_vm_names(target: str, playbook: str) -> None:
    result = _make("-n", target, "VM_NAME=alpha,beta")
    assert result.returncode == 0, result.stdout + result.stderr
    assert """-e '{"vm_name": "alpha,beta"}'""" in result.stdout
    assert f"playbooks/{playbook}.yml" in result.stdout


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
    return [f"VENV={tmp_path / 'venv'}", f"VAULT_FILE={vault}", f"LOG_DIR={tmp_path / 'logs'}"]


def _stub_log_path(output: str) -> str:
    match = re.search(r"stub ANSIBLE_LOG_PATH=\[(.*)\]", output)
    assert match, output
    return match.group(1)


@pytest.mark.parametrize(
    "target", ["list", "deploy", "ssh-config", "claude-login", "github-login", "tailscale-login", "template"]
)
def test_a_playbook_target_logs_its_run_to_its_own_file(tmp_path: Path, target: str) -> None:
    result = _make(target, "VM_NAME=alpha", *_logging_overrides(tmp_path))
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    path = _stub_log_path(output)
    assert re.fullmatch(rf"{re.escape(str(tmp_path / 'logs'))}/\d{{8}}-\d{{6}}-{re.escape(target)}\.log", path), path
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


def test_logs_lists_and_logs_clean_deletes_only_run_logs(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    assert "No run logs in" in _make("logs", f"LOG_DIR={logs}").stdout
    logs.mkdir()
    run_log, notes = logs / "20261007-203015-deploy.log", logs / "notes.txt"
    run_log.write_text("x")
    notes.write_text("x")

    assert _make("logs", f"LOG_DIR={logs}").stdout.strip() == str(run_log)
    cleaned = _make("logs-clean", f"LOG_DIR={logs}")
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


@pytest.mark.parametrize("target", ["deploy", "configure", "list"])
def test_only_code_reads_project(target: str) -> None:
    result = _make("-n", target, "VM_NAME=alpha", "PROJECT=discord-music-bot")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "vm_project" not in result.stdout


# A run with no terminal - `make deploy </dev/null`, an IDE task, an agent's
# shell - has nobody to approve a sign-in: the sign-ins' browser and clipboard
# steps are turned off there, and make deploy's sign-ins, which start by
# themselves, are skipped rather than wait for a browser nobody watches. The
# recipe runs for real, with VENV pointed at a stand-in ansible-playbook that
# only records its arguments.
def _recorded_args(tmp_path: Path, target: str, stdin) -> list[str]:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    record = tmp_path / "args"
    stand_in = venv / "bin" / "ansible-playbook"
    stand_in.write_text(f'#!/bin/sh\nfor arg in "$@"; do printf "%s\\n" "$arg"; done > {record}\n')
    stand_in.chmod(0o755)
    _stand_in_galaxy(venv / "bin")
    overrides = [f"VENV={venv}", *_safe_overrides(tmp_path)[1:]]
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"), "LANG": "en_US.UTF-8"}
    result = subprocess.run(
        [MAKE, target, "VM_NAME=alpha", *overrides],
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


@pytest.mark.parametrize("target", ["deploy", "claude-login", "github-login", "tailscale-login"])
def test_a_run_with_no_terminal_turns_the_desktop_steps_and_deploy_s_sign_ins_off(tmp_path: Path, target: str) -> None:
    args = _recorded_args(tmp_path, target, subprocess.DEVNULL)
    off = '{"login_desktop":false,"deploy_sign_in":false}'
    assert off in args and args[args.index(off) - 1] == "-e", args


@pytest.mark.parametrize("target", ["deploy", "claude-login", "github-login", "tailscale-login"])
def test_a_run_at_a_terminal_leaves_the_desktop_steps_on(tmp_path: Path, target: str) -> None:
    import pty

    primary, secondary = pty.openpty()
    try:
        args = _recorded_args(tmp_path, target, secondary)
    finally:
        os.close(primary)
        os.close(secondary)
    assert not any("login_desktop" in arg for arg in args), args
