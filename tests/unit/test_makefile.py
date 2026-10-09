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
pointed at an empty directory and VAULT_FILE at a stub that only looks
encrypted: nothing could reach an ansible-playbook even if a guard regressed,
and vault-check, which every playbook target runs first, lets the run reach
the guard under test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MAKE = shutil.which("make")
NAME_ERROR = "VM_NAME may contain only letters, digits and . _ - ,"

pytestmark = pytest.mark.skipif(MAKE is None, reason="make is not installed")


def _make(*args: str) -> subprocess.CompletedProcess[str]:
    # A clean environment: a developer's own VM_NAME or TAGS would leak into
    # the Makefile's `?=` defaults, and the locale must not be what makes the
    # non-ASCII case pass or fail.
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"), "LANG": "en_US.UTF-8"}
    return subprocess.run([MAKE, *args], cwd=REPO, env=env, capture_output=True, text=True, check=False, timeout=60)


VAULT_HEADER = "$ANSIBLE_VAULT;1.1;AES256\n"


def _safe_overrides(tmp_path: Path) -> list[str]:
    """Make the run harmless: no ansible-playbook to execute, and a vault stub vault-check accepts."""
    vault = tmp_path / "vault.yml"
    vault.write_text(VAULT_HEADER)
    return [f"VENV={tmp_path / 'no-venv'}", f"VAULT_FILE={vault}"]


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
    result = _make("list", f"VENV={tmp_path / 'no-venv'}", f"VAULT_FILE={vault}")
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "missing. Run: make init" in output
    assert "ansible-playbook" not in output


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


def test_deploy_refuses_tags(tmp_path: Path) -> None:
    result = _make("deploy", "TAGS=claude_code", *_safe_overrides(tmp_path))
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "deploy takes no TAGS" in output
    assert "make configure VM_NAME=<name> TAGS=claude_code" in output
    assert "ansible-playbook" not in output
