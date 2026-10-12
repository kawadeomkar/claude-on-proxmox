"""The tracked-file guard must catch every path .gitignore protects.

The guard is the backstop for `git add -f`, so a gap in it is a secret that
reaches a public repository. It was silently narrowed once already while
fixing a false positive on molecule/*/host_vars/localhost.yml: anchoring the
rules to inventory/ and the repository root let playbooks/group_vars/all/vault.yml
and inventory/prod/hosts.yml through. That is why both directions are asserted
here, and why MUST_BLOCK lists paths at every depth Ansible loads from.
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
    "inventory/pve.proxmox.yml",
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
    # Extensionless, as Ansible's own vault docs name it; Ansible loads it.
    "inventory/group_vars/all/vault",
    # Backups are not loaded, but carry the same values.
    "inventory/hosts.yml.bak",
    "inventory/group_vars/all/vault.yml.bak",
    # A second inventory. ansible.cfg points at the whole inventory/ directory,
    # which Ansible parses recursively, so its hosts file is read even without
    # -i; its group_vars/ load with -i inventory/prod.
    "inventory/prod/hosts.yml",
    "inventory/prod/hosts.yaml",
    "inventory/prod/hosts",
    # The community.proxmox inventory plugin's config, loaded from any depth
    # like the hosts file beside it; it carries the URL and usually the token.
    "inventory/prod/proxmox.yml",
    "inventory/prod/pve.proxmox.yaml",
    # The group_vars/ exemption for a vars file named proxmox.yml must not widen
    # to a vault file with that suffix, nor reach host_vars/.
    "inventory/group_vars/all/vault-proxmox.yml",
    "inventory/host_vars/proxmox.yml",
    "inventory/prod/group_vars/all/vault.yml",
    "inventory/prod/group_vars/all/local.yml",
    "inventory/prod/.vault_pass",
    # Every playbook in playbooks/ loads group_vars/ and host_vars/ beside it.
    "playbooks/group_vars/all/vault.yml",
    "playbooks/group_vars/all/local.yml",
    "playbooks/group_vars/claude_vms/secret.json",
    "playbooks/host_vars/alpha.yml",
    "playbooks/hosts.yml",
    "playbooks/.env",
    # deploy.yml is at the repo root, so Ansible loads these.
    "hosts.yml",
    "group_vars/all/vault.yml",
    "group_vars/all/local.yml",
    "group_vars/claude_vms/secret.json",
    "group_vars/vault/main.yml",
    "host_vars/pve.yml",
    "vault.yml",
    # Loadable from anywhere with vars_files, include_vars or -e @file.
    "playbooks/vault.yml",
    "playbooks/local.yml",
    "vars/secrets.yml",
    # The fixture exemption covers Molecule's host_vars, not its group_vars.
    "molecule/provision/group_vars/all/vault.yml",
    ".vault_pass",
    ".vault_pass.txt",
    ".env",
    ".env.local",
    # Run logs name the Proxmox host and every VM by address.
    ".logs/20261007-203015-deploy.log",
    ".logs/notes.txt",
    "roles/common/.logs/20261007-203015-molecule.log",
    "ansible.log",
    "molecule/provision/verify.log",
]

MUST_ALLOW = [
    # Committed templates carry no real values.
    "inventory/hosts.yml.example",
    "inventory/group_vars/all/vault.yml.example",
    "inventory/group_vars/all/local.yml.example",
    ".env.example",
    # Tracked test fixtures that merely live in a host_vars directory.
    "molecule/configure/host_vars/localhost.yml",
    "molecule/configure/host_vars/claude-dev.yml",
    "molecule/provision/host_vars/localhost.yml",
    "molecule/provision/group_vars/all/defaults.yml",
    "molecule/provision/group_vars/all/fake_api.yml",
    "molecule/configure/group_vars/all/defaults.yml",
    # Ordinary project files.
    "inventory/controller.yml",
    "inventory/group_vars/all/defaults.yml",
    # A split-out defaults file is not the proxmox inventory plugin's config:
    # Ansible skips group_vars/ and host_vars/ when it walks inventory/.
    "inventory/group_vars/all/proxmox.yml",
    "inventory/prod/group_vars/all/proxmox.yml",
    # "local" begins ordinary names; only exact names are blocked outside group_vars/.
    "roles/common/tasks/locale.yml",
    "roles/common/templates/local-bin-path.sh.j2",
    "roles/proxmox_vm/defaults/main.yml",
    "roles/proxmox_vm/vars/main.yml",
    "playbooks/discover.yml",
    "deploy.yml",
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


def is_gitignored(path: str) -> bool:
    # Only the repository's own .gitignore: a developer's global excludes file
    # must not decide whether this test passes.
    return (
        subprocess.run(
            ["git", "-c", "core.excludesFile=/dev/null", "check-ignore", "-q", "--no-index", path],
            cwd=REPO,
            check=False,
        ).returncode
        == 0
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
    not_ignored = [path for path in MUST_BLOCK if not is_gitignored(path)]
    assert not not_ignored, f"guarded but not gitignored: {not_ignored}"


def test_no_allowed_path_is_gitignored():
    """A pattern broad enough to swallow a fixture needs its re-include in .gitignore too."""
    ignored = [path for path in MUST_ALLOW if is_gitignored(path)]
    assert not ignored, f"allowed by the guard but gitignored: {ignored}"


def test_no_tracked_file_is_gitignored():
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--ignored", "--exclude-per-directory=.gitignore"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    assert not result.stdout, f"tracked but gitignored:\n{result.stdout}"
