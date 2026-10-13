"""A gathered fact is read through ansible_facts, never as a top-level ansible_* variable.

ansible-core deprecates injecting facts as top-level variables (INJECT_FACTS_AS_VARS)
and 2.24 stops: the first `make deploy` of a new VM printed a deprecation for each
of the ten reads common and dev_tools made that way, and each would have become an
undefined variable on that upgrade. ansible.cfg and Molecule's shared config turn the
injection off now, so a read that comes back fails a run or a scenario today; the
scan catches one on a path no scenario takes.
"""

from __future__ import annotations

import configparser
import re
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]

# The ansible_* names this project uses that are not gathered facts: the facts
# themselves, connection and inventory variables, and magic variables.
NOT_FACTS = {
    "ansible_facts",
    "ansible_host",
    "ansible_port",
    "ansible_user",
    "ansible_connection",
    "ansible_python_interpreter",
    "ansible_ssh_private_key_file",
    "ansible_ssh_timeout",
    "ansible_check_mode",
    "ansible_play_batch",
    "ansible_play_hosts",
    "ansible_failed_task",
    "ansible_failed_result",
    "ansible_playbook_python",
    "ansible_inventory_sources",
    "ansible_managed",
}
# A variable read at the top level, or through hostvars[host]., vars. or
# ansible_facts., none of which holds a fact under its ansible_ name with the
# injection off; not a key read off a register (job.ansible_job_id) or a path
# (~/.ansible_async).
NAME = re.compile(r"(?:(?<![\w./])|(?<=\]\.)|(?<=\bvars\.)|(?<=\bansible_facts\.))ansible_\w+")


def _tracked() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.yml", "*.yaml", "*.j2", "*.yml.example"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO / path for path in listed.split("\0") if path]


def test_no_tracked_file_reads_a_fact_as_a_top_level_variable():
    reads = [
        (f"{path.relative_to(REPO)}:{number}", name)
        for path in _tracked()
        for number, line in enumerate(path.read_text().splitlines(), 1)
        for name in NAME.findall(line)
    ]
    found = sorted(f"{where}: {name}" for where, name in reads if name not in NOT_FACTS)
    assert not found, (
        "read a fact as ansible_facts['<name>'], or add a name that is not one to NOT_FACTS:\n" + "\n".join(found)
    )
    # NOT_FACTS holds only names something reads, so a name in it is one in use.
    unread = NOT_FACTS - {name for _, name in reads}
    assert not unread, "nothing reads these any more; drop them from NOT_FACTS: " + ", ".join(sorted(unread))


def test_ansible_and_molecule_leave_facts_out_of_the_top_level():
    config = configparser.ConfigParser(interpolation=None)
    config.read(REPO / "ansible.cfg")
    assert config.getboolean("defaults", "inject_facts_as_vars") is False
    molecule = yaml.safe_load((REPO / ".config" / "molecule" / "config.yml").read_text())
    assert molecule["provisioner"]["config_options"]["defaults"]["inject_facts_as_vars"] is False


def test_the_scan_sees_what_it_guards():
    assert NAME.findall("when: ansible_service_mgr == 'systemd'") == ["ansible_service_mgr"]
    assert NAME.findall("{{ dev_tools_uv_arch_map[ansible_architecture] }}") == ["ansible_architecture"]
    assert NAME.findall("when: ansible_facts['service_mgr'] == 'systemd'") == ["ansible_facts"]
    assert NAME.findall('jid: "{{ claude_code_login_job.ansible_job_id }}"') == []
    assert NAME.findall("paths: /home/dev/.ansible_async") == []
    # Through hostvars, vars or ansible_facts, a fact under its ansible_ name is
    # as undefined as at the top level.
    release = "{{ hostvars[inventory_hostname].ansible_distribution_release }}"
    assert NAME.findall(release) == ["ansible_distribution_release"]
    assert NAME.findall("{{ vars.ansible_architecture }}") == ["ansible_architecture"]
    assert NAME.findall("{{ ansible_facts.ansible_architecture }}") == ["ansible_facts", "ansible_architecture"]
    assert NAME.findall("{{ ansible_facts['ansible_architecture'] }}") == ["ansible_facts", "ansible_architecture"]
    assert REPO / "roles" / "common" / "handlers" / "main.yml" in _tracked()
    assert REPO / "inventory" / "group_vars" / "all" / "local.yml.example" in _tracked()
