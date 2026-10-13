"""What `make deploy` says before it starts, rendered by deploy.yml's own tasks.

The first play looks the template up through the API; here a canned lookup
stands in for it, set as a fact, which ranks with the register it replaces.
The tasks that follow run as deploy.yml has them.

What this guards: the opening message was built in a task var named like the
lookup's register, and a register outranks a task var, so the run opened by
printing the API's raw answer instead of the sentence.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
LOOKUP = "Look up the template by name"
REPORT = "Report what this run will do"


def _said(tmp_path: Path, built: bool, **extra) -> str:
    first = yaml.safe_load((REPO / "deploy.yml").read_text())[0]
    tasks = first["tasks"]
    assert [t["name"] for t in tasks][0] == LOOKUP
    lookup = tasks[0]["register"]
    template = {"name": "ubuntu-24.04-cloudinit", "node": "pve", "template": 1}
    canned = {"proxmox_vms": [template] if built else []}
    play = {
        "hosts": "localhost",
        "gather_facts": False,
        "tasks": [{"name": "Stand in for the lookup", "ansible.builtin.set_fact": {lookup: canned}}, *tasks[1:]],
    }
    playbook = tmp_path / "deploy_report.yml"
    playbook.write_text(yaml.safe_dump([play], sort_keys=False))
    (tmp_path / "ansible.cfg").write_text("")
    variables = {
        "proxmox_template_name": "ubuntu-24.04-cloudinit",
        "proxmox_node": "pve",
        "claude_vm_names": ["alpha"],
        "deploy_sign_in": True,
        "deploy_code": True,
        **extra,
    }
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_CONFIG=str(tmp_path / "ansible.cfg"), ANSIBLE_STDOUT_CALLBACK="ansible.builtin.json")
    result = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", "localhost,", "-c", "local", "-e", json.dumps(variables), str(playbook)],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    said = [
        host["msg"]
        for play in report["plays"]
        for task in play["tasks"]
        if task["task"]["name"] == REPORT
        for host in task["hosts"].values()
    ]
    assert len(said) == 1
    assert isinstance(said[0], str), said[0]
    return said[0]


def test_a_built_template_is_said_in_words(tmp_path):
    said = _said(tmp_path, built=True)
    assert said.startswith('Template "ubuntu-24.04-cloudinit" is already built, so this run needs no SSH'), said
    assert "proxmox_vms" not in said


def test_a_missing_template_is_said_to_be_built_first(tmp_path):
    said = _said(tmp_path, built=False)
    assert said.startswith('Template "ubuntu-24.04-cloudinit" does not exist yet; building it first'), said


@pytest.mark.parametrize("sign_in", [True, False])
def test_the_stop_for_the_sign_ins_is_said_up_front(tmp_path, sign_in):
    said = _said(tmp_path, built=True, deploy_sign_in=sign_in)
    assert ("the run stops for you at a prompt here" in said) is sign_in, said
    assert "\n" not in said
