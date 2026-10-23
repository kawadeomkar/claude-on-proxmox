"""configure.yml's wait for cloud-init, run for real against a stand-in cloud-init.

A VM's first boot can fail - a package upgrade cut short by a mirror, or by a
download corrupted on its way to the disk - and `cloud-init status --wait`
then exits 1 with `status: error`, which once failed configure with no more
than "The command exited with a non-zero return code". These hold that the
failure names what cloud-init said failed and how to get past it, that what
the VM printed reaches the message only as plain text, and that "done with
recoverable errors" (exit 2) and success go on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from harness import REPO, STAND_INS, _failures, _results, _run

PLAYBOOK = REPO / "playbooks" / "configure.yml"
WAIT = ("Wait for cloud-init to finish", "Read what cloud-init reported", "Fail with what cloud-init reported")

# What `cloud-init status --long` printed on a VM whose first-boot upgrade
# failed (Ubuntu 24.04, cloud-init 26.1), with a control sequence a VM's user
# could add.
LONG = (
    "status: error\n"
    "extended_status: error - done\n"
    "detail: DataSourceNoCloud [seed=/dev/sr0]\n"
    "errors:\n"
    "\t- ('package_update_upgrade_install', ProcessExecutionError(\"Unexpected error while running command.\\n"
    "Command: ['eatmydata', 'apt-get', 'dist-upgrade']\\nExit code: 100\"))\n"
    "recoverable_errors:\n"
    "WARNING:\n"
    "\t- Package upgrade failed \x1b]0;retitled\x07\n"
)


# Each host's exit code is in its own directory ($STAND_IN, the harness's).
CLOUD_INIT = """#!/bin/sh
rc=$(cat "$STAND_IN/rc")
case "$*" in
  "status --wait") [ "$rc" = 1 ] && echo '...status: error' || echo '...status: done'; exit "$rc" ;;
  "status --long") cat "$STAND_IN/long.txt"; exit "$rc" ;;
esac
echo "stand-in cloud-init: unexpected arguments: $*" >&2
exit 99
"""


def _wait_tasks(root: Path) -> Path:
    """The cloud-init tasks of configure.yml's pre_tasks, as configure.yml has them, in a play of their own."""
    play = next(p for p in yaml.safe_load(PLAYBOOK.read_text()) if p.get("name") == "Configure Claude development VMs")
    tasks = [t for t in play["pre_tasks"] if t.get("name") in WAIT]
    assert [t["name"] for t in tasks] == list(WAIT)
    playbook = root / "wait.yml"
    playbook.write_text(yaml.safe_dump([{"hosts": "claude_vms", "gather_facts": False, "tasks": tasks}]))
    return playbook


def _hosts(root: Path, rcs: dict) -> dict:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name, text in (("python", STAND_INS["python"]), ("cloud-init", CLOUD_INIT)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    hosts = {}
    for name, rc in rcs.items():
        home = root / name
        home.mkdir()
        (home / "long.txt").write_text(LONG)
        (home / "rc").write_text(str(rc))
        (home / "python").symlink_to(bin_dir / "python")
        hosts[name] = {"ansible_connection": "local", "ansible_python_interpreter": str(home / "python")}
    return hosts


@pytest.fixture(scope="module")
def waited(tmp_path_factory):
    root = tmp_path_factory.mktemp("cloud_init")
    hosts = _hosts(root, {"failed": 1, "recoverable": 2, "done": 0})
    return _run(root, _wait_tasks(root), hosts, "-e", json.dumps({"ansible_become": False}))


def test_a_failed_first_boot_names_what_failed_and_how_to_get_past_it(waited):
    assert _failures(waited)["failed"] == (
        "Fail with what cloud-init reported: failed: cloud-init's first boot ended in an error (in"
        " package_update_upgrade_install), so the VM may be half set up. /var/log/cloud-init-output.log on it says"
        " why. Building it again is the sure way past a failure that will not repeat - a mirror, a corrupted"
        " download: make destroy VM_NAME=failed, then make deploy VM_NAME=failed. Or repair it by hand, then make"
        ' deploy VM_NAME=failed TYPE=configure ANSIBLE_ARGS="-e configure_wait_for_cloud_init=false".'
    )


def test_recoverable_errors_and_success_go_on(waited):
    assert set(_failures(waited)) == {"failed"}
    read = {host for name, host, result in _results(waited) if name.endswith(WAIT[1]) and not result.get("skipped")}
    assert read == {"failed"}


def test_what_the_vm_printed_reaches_no_message_with_its_control_characters(waited):
    assert "\x1b" not in json.dumps(_failures(waited))
