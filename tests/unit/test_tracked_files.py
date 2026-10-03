"""The tracked-file guard must catch every path .gitignore protects.

The guard is the backstop for `git add -f`, so a gap in it is a secret that
reaches a public repository. It was silently narrowed once already while
fixing a false positive on molecule/*/host_vars/localhost.yml, which is why
both directions are asserted here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "tests" / "check_no_local_files.sh"

MUST_BLOCK = [
    "inventory/hosts.yml",
    "inventory/hosts.yaml",
    "inventory/proxmox.yml",
    "inventory/group_vars/all/vault.yml",
    "inventory/group_vars/all/local.yml",
    "inventory/group_vars/all/secrets.yml",
    "inventory/group_vars/all/vault.json",
    "inventory/group_vars/local.yml",
    "inventory/group_vars/vault.yml",
    "inventory/group_vars/prod/local.yaml",
    "inventory/host_vars/pve.yml",
    "inventory/prod/host_vars/vm1.yml",
    "inventory/prod/hosts.ini",
    "inventory/hosts.ini",
    # site.yml is at the repo root, so Ansible loads these.
    "group_vars/all/vault.yml",
    "group_vars/all/local.yml",
    "host_vars/pve.yml",
    "vault.yml",
    ".vault_pass",
    ".vault_pass.txt",
    ".env",
    ".env.local",
]

MUST_ALLOW = [
    # Committed templates carry no real values.
    "inventory/hosts.yml.example",
    "inventory/group_vars/all/vault.yml.example",
    "inventory/group_vars/all/local.yml.example",
    # Tracked test fixtures that merely live in a host_vars directory.
    "molecule/configure/host_vars/localhost.yml",
    "molecule/configure/host_vars/claude-dev.yml",
    "molecule/provision/host_vars/localhost.yml",
    "molecule/provision/group_vars/all/defaults.yml",
    # Ordinary project files.
    "inventory/controller.yml",
    "roles/proxmox_vm/defaults/main.yml",
    "roles/proxmox_vm/vars/main.yml",
    "playbooks/discover.yml",
    "site.yml",
    "tox.ini",
    "README.md",
]


def run_guard(paths: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GUARD), "--stdin"],
        input="\n".join(paths),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("path", MUST_BLOCK)
def test_guard_blocks_environment_specific_files(path):
    result = run_guard([path])
    assert result.returncode == 1, f"{path} was not caught by the guard"
    assert path in result.stdout


@pytest.mark.parametrize("path", MUST_ALLOW)
def test_guard_allows_committed_files(path):
    result = run_guard([path])
    assert result.returncode == 0, f"{path} was wrongly flagged:\n{result.stdout}"


def test_guard_reports_every_leak_not_just_the_first():
    result = run_guard(["README.md", *MUST_BLOCK])
    assert result.returncode == 1
    for path in MUST_BLOCK:
        assert path in result.stdout


def test_guard_passes_on_the_real_repository():
    result = subprocess.run(["bash", str(GUARD)], cwd=REPO, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"tracked files flagged:\n{result.stdout}"


def test_every_blocked_path_is_also_gitignored():
    """The guard and .gitignore must agree, or one of them is load-bearing alone."""
    not_ignored = [
        path
        for path in MUST_BLOCK
        if subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", path],
            cwd=REPO,
            check=False,
        ).returncode
        != 0
    ]
    assert not not_ignored, f"guarded but not gitignored: {not_ignored}"
