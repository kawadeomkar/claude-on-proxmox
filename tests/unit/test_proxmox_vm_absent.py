"""Deleting a VM forgets its SSH host key under every name this machine may hold it by.

The task runs for real, delegated to localhost as in destroy.yml, against a
known_hosts file in a scratch HOME: never the developer's own.

What this guards: with claude_ssh_host_domain set, the SSH aliases and just code
connect by <name>.<domain>, so that is a name known_hosts records the key under.
Deleting the VM forgot only its address, and a VM created again under the same
name failed its first connection by name with "Host key verification failed".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from harness import ansible_config

REPO = Path(__file__).resolve().parents[2]
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
ROLE = REPO / "roles" / "proxmox_vm"
FORGET = "absent | Forget the VM's SSH host key"


def _forget(tmp_path: Path, **extra) -> set[str]:
    """Run absent.yml's forget task with the role's vars and `extra`; return the hosts left in known_hosts."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "key")], check=True)
    key = (tmp_path / "key.pub").read_text().split()[:2]
    known = home / ".ssh" / "known_hosts"
    known.write_text("".join(f"{host} {key[0]} {key[1]}\n" for host in ("192.0.2.10", "alpha.lan", "beta.lan")))

    task = next(t for t in yaml.safe_load((ROLE / "tasks" / "absent.yml").read_text()) if t["name"] == FORGET)
    playbook = tmp_path / "forget.yml"
    vars_files = [str(ROLE / "vars" / "main.yml")]
    play = {"hosts": "localhost", "gather_facts": False, "vars_files": vars_files, "tasks": [task]}
    playbook.write_text(yaml.safe_dump([play]))
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    # The module expands ~/.ssh/known_hosts from HOME: the scratch one.
    env.update(HOME=str(home), ANSIBLE_CONFIG=ansible_config(tmp_path), ANSIBLE_NOCOLOR="1")
    result = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", "localhost,", "-c", "local", "-e", json.dumps(extra), str(playbook)],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {line.split()[0] for line in known.read_text().splitlines() if line.strip()}


VM = {"proxmox_vm_exists": True, "proxmox_vm_name": "alpha", "proxmox_vm_forget_address": "192.0.2.10"}


def test_with_a_domain_the_name_is_forgotten_with_the_address(tmp_path):
    assert _forget(tmp_path, **VM, proxmox_vm_known_hosts_domain="lan") == {"beta.lan"}


def test_without_a_domain_only_the_address_is_forgotten(tmp_path):
    assert _forget(tmp_path, **VM, proxmox_vm_known_hosts_domain="") == {"alpha.lan", "beta.lan"}


@pytest.mark.parametrize("domain", ["lan", ""])
def test_a_vm_whose_address_is_unknown_still_loses_its_name(tmp_path, domain):
    left = _forget(tmp_path, **{**VM, "proxmox_vm_forget_address": ""}, proxmox_vm_known_hosts_domain=domain)
    assert left == ({"192.0.2.10", "beta.lan"} if domain else {"192.0.2.10", "alpha.lan", "beta.lan"})


def test_the_inventory_wires_the_domain_into_the_role():
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert defaults["proxmox_vm_known_hosts_domain"] == "{{ claude_ssh_host_domain }}"
