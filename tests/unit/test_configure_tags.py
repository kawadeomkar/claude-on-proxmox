"""`make configure TAGS=<role>` must still discover the VMs it is meant to configure.

VMs only enter the inventory when discovery adds them, and tags propagate to
the tasks of an imported playbook, so discovery keeps running under --tags only
because configure.yml tags its import "always". Without that, `--tags
claude_code` - the documented way to push a new API key - skipped discovery,
matched no hosts and exited 0 having done nothing.

Checked with --list-tasks, which needs no hosts and runs nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
DISCOVERY = "Discover VMs on Proxmox"


def _listed_tasks(tmp_path: Path, *args: str) -> dict[str, list[str]]:
    """The tasks `ansible-playbook --list-tasks` selects from configure.yml, by play name."""
    # No project config or inventory: nothing here needs them, and a
    # developer's own inventory would bring in a vault this cannot open.
    (tmp_path / "ansible.cfg").write_text("")
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(
        ANSIBLE_CONFIG=str(tmp_path / "ansible.cfg"), ANSIBLE_ROLES_PATH=str(REPO / "roles"), ANSIBLE_NOCOLOR="1"
    )
    result = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", "localhost,", "--list-tasks", *args, str(REPO / "playbooks" / "configure.yml")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    plays: dict[str, list[str]] = {}
    tasks: list[str] = []
    for line in result.stdout.splitlines():
        if line.strip().startswith("play #"):
            # "  play #1 (localhost): Discover VMs on Proxmox\tTAGS: [always]"
            tasks = plays.setdefault(line.split(": ", 1)[1].split("\t")[0], [])
        elif line.startswith("      ") and line.strip():
            tasks.append(line.strip().split("\t")[0])
    return plays


def test_a_role_tag_keeps_every_discovery_task(tmp_path):
    everything = _listed_tasks(tmp_path)[DISCOVERY]
    assert everything, "discover.yml lists no tasks at all"
    # The tag that pushes a new API key. Every role tag behaves the same way
    # here, so one stands for all four.
    assert _listed_tasks(tmp_path, "--tags", "claude_code")[DISCOVERY] == everything
