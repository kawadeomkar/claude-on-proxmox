"""A gathered fact is read through ansible_facts, never as a top-level ansible_* variable.

ansible-core deprecates injecting facts as top-level variables (INJECT_FACTS_AS_VARS)
and 2.24 stops: the first `just deploy` of a new VM printed a deprecation for each
of the ten reads common and dev_tools made that way, and each would have become an
undefined variable on that upgrade. ansible.cfg and Molecule's shared config turn the
injection off now, and so does every ansible.cfg these tests write, so a read that
comes back fails a run, a scenario or a test today; the scan catches one on a path
nothing runs.
"""

from __future__ import annotations

import ast
import configparser
import re
import subprocess
from pathlib import Path

import yaml
from harness import ANSIBLE_CFG

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
    "ansible_pipelining",
    "ansible_ssh_private_key_file",
    "ansible_private_key_file",
    "ansible_ssh_common_args",
    "ansible_ssh_extra_args",
    "ansible_ssh_timeout",
    "ansible_timeout",
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


# What only the test runs set: the file a run's settings come from, and the
# setting itself, which in the environment outranks any file.
RUN_SETTINGS = ("ANSIBLE_CONFIG", "ANSIBLE_INJECT_FACT_VARS")


def _through_the_helper(value: ast.expr) -> bool:
    return isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "ansible_config"


def _config_spellings(source: str) -> list[int]:
    """The lines of a test file that write or name an ansible.cfg, or set a run's settings, other than through
    harness.ansible_config: ANSIBLE_CONFIG set to its path, the project's own ansible.cfg read, a docstring."""
    tree = ast.parse(source)
    allowed = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                allowed.add(id(node.body[0].value))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if isinstance(node.left, ast.Name) and node.left.id == "REPO":
                allowed.add(id(node.right))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "ANSIBLE_CONFIG" and _through_the_helper(value):
                    allowed.add(id(key))
        elif isinstance(node, ast.Assign) and _through_the_helper(node.value):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
                    allowed.add(id(target.slice))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in RUN_SETTINGS:
            if node.arg != "ANSIBLE_CONFIG" or not _through_the_helper(node.value):
                found.add(node.value.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in allowed:
            if "ansible.cfg" in node.value or any(name in node.value for name in RUN_SETTINGS):
                found.add(node.lineno)
    return sorted(found)


def test_every_run_a_test_makes_leaves_facts_out_of_the_top_level():
    # Many paths of the sign-ins, destroy and the VS Code step run only here, so
    # a top-level read of a fact without the ansible_ prefix (getent_passwd)
    # shows only here. The harness, which defines the helper, and this file,
    # whose samples name what it refuses, are the two it does not read.
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(ANSIBLE_CFG)
    assert config.getboolean("defaults", "inject_facts_as_vars") is False
    elsewhere = [
        f"{path.name}:{line}"
        for path in sorted((REPO / "tests" / "unit").glob("*.py"))
        if path.name not in {"harness.py", Path(__file__).name}
        for line in _config_spellings(path.read_text())
    ]
    assert not elsewhere, "write and name the run's ansible.cfg with harness.ansible_config: " + ", ".join(elsewhere)


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


def test_the_config_guard_sees_what_it_guards():
    refused = [
        'cfg = tmp_path / "ansible.cfg"\ncfg.write_text("")',
        "open(tmp_path / 'ansible.cfg', 'w').write(ANSIBLE_CFG)",
        'path = f"{tmp_path}/ansible.cfg"',
        "env.update(ANSIBLE_CONFIG=os.devnull)",
        "env.update(ANSIBLE_CONFIG=str(tmp_path / name))",
        'env = {"ANSIBLE_CONFIG": str(tmp_path / name)}',
        'env["ANSIBLE_CONFIG"] = os.devnull',
        'monkeypatch.setenv("ANSIBLE_CONFIG", str(config))',
        'env.update(ANSIBLE_INJECT_FACT_VARS="True")',
        'env = {"ANSIBLE_INJECT_FACT_VARS": "True"}',
    ]
    for source in refused:
        assert _config_spellings(source), source
    accepted = [
        "env.update(ANSIBLE_CONFIG=ansible_config(tmp_path), ANSIBLE_NOCOLOR='1')",
        'env = {"ANSIBLE_CONFIG": ansible_config(tmp_path)}',
        'env["ANSIBLE_CONFIG"] = ansible_config(tmp_path, "forks = 1\\n")',
        'config.read(REPO / "ansible.cfg")',
        'def run():\n    """Writes no ansible.cfg of its own."""\n    # nor here: ansible.cfg',
    ]
    for source in accepted:
        assert not _config_spellings(source), source
