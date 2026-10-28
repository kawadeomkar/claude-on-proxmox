"""`just code` drawn live: the real playbooks/code.yml through the stdout callback, as the justfile runs it.

Against the fake Proxmox, its guest agent giving the VM's address, with test_code_session.py's world - a checkout and
its sessions here, the VM's home beside it, this machine standing in for the VM - and its pins (_window_extra): the
developer's VS Code settings, time zone and ~/.claude never read, the managed SSH config and the user's in the test's
own directory, stand-ins for `which` and `code` first on a PATH of the system's directories only, and the live kit's
HOME. What a person sees: each of the window's nine steps, the VM in the header, the region gone and the window
opened; and a VM that is not there, said under the step it failed in, the raise naming nothing of its own
(docs/architecture/custom-code.md §7.5).
"""

from __future__ import annotations

import json
import os
import re
import sys

import harness
import live_kit as kit
import pytest
import test_code_session as tcs

VMID = 9101
CODE_STAND_IN = """#!/bin/sh
echo "code $*" >> "{log}"
[ "$1" = --list-extensions ] && echo ms-vscode-remote.remote-ssh
exit 0
"""
STEPS = (
    "find the VM",
    "reach it",
    "read the sessions",
    "the folder",
    "alias",
    "VS Code here",
    "copy the sessions",
    "workspace",
    "window",
)


def _seed(base):
    """alpha, tagged and running, its agent answering as a VM long up does."""
    harness._pve(base, "POST", "nodes/pve/qemu/9000/clone", newid=VMID, name="alpha")
    harness._pve(base, "PUT", f"nodes/pve/qemu/{VMID}/config", tags="claude-on-proxmox")
    harness._pve(base, "POST", f"nodes/pve/qemu/{VMID}/status/start")
    for _ in range(3):
        try:
            harness._pve(base, "GET", f"nodes/pve/qemu/{VMID}/agent/network-get-interfaces")
        except Exception:  # noqa: BLE001 - the refusals are the point
            pass


def _code(root, title, vm="alpha", piped=False):
    world = tcs._world(root / "world")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "code").write_text(CODE_STAND_IN.format(log=root / "code.log"))
    (bin_dir / "which").write_text(f'#!/bin/sh\n[ "$1" = code ] && echo "{bin_dir}/code" && exit 0\nexit 1\n')
    for name in ("code", "which"):
        (bin_dir / name).chmod(0o755)
    ssh = root / "ssh"
    ssh.mkdir(mode=0o700)
    # As after a just deploy: the user's config reaches the managed file already.
    (ssh / "config").write_text(f"Include {ssh / 'claude-on-proxmox.conf'}\n")
    (ssh / "config").chmod(0o600)
    cert, key = harness._certificate(root)
    pve, port = harness._start(
        harness.FAKES / "fake_pve_api.py", str(cert), str(key), str(root / "pve"), harness.PVE_SECRET
    )
    try:
        _seed(f"https://127.0.0.1:{port}")
        inventory = root / "inventory"
        (inventory / "group_vars" / "all").mkdir(parents=True)
        defaults = harness.REPO / "inventory" / "group_vars" / "all" / "defaults.yml"
        (inventory / "group_vars" / "all" / "defaults.yml").symlink_to(defaults)
        api = {
            "proxmox_api_host": "127.0.0.1",
            "proxmox_api_port": port,
            "vault_proxmox_api_token_secret": harness.PVE_SECRET,
            "vm_user": "dev",
        }
        (inventory / "group_vars" / "all" / "pinned.yml").write_text(json.dumps(api))
        (inventory / "hosts.yml").write_text(
            f"all:\n  hosts:\n    localhost:\n      ansible_connection: local\n"
            f"      ansible_python_interpreter: {sys.executable}\n"
        )
        extra = {
            **tcs._window_extra(root),
            "vm_name": vm,
            "ansible_connection": "local",
            "vm_projects_dir": str(world["vm"] / "projects"),
            "code_session_source_dir": str(world["here"] / ".claude"),
            "code_session_dest_dir": str(world["vm"] / ".claude"),
            "vscode_claude_sessions": True,
            "claude_ssh_config_file": str(ssh / "claude-on-proxmox.conf"),
            "claude_ssh_user_config": str(ssh / "config"),
        }
        env = kit.environment(root, title, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        desktop = env["PATH"].split(os.pathsep)[0]
        # VS Code's command and `which` the stand-ins', then the system's directories alone: never this machine's own.
        env["PATH"] = os.pathsep.join([str(bin_dir), desktop, "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
        argv = kit.playbook(root, inventory, tcs.CODE, "-e", json.dumps(extra))
        ran = kit.piped(argv, root, env, timeout=300) if piped else kit.at_a_terminal(argv, root, env, timeout=300)
    finally:
        pve.terminate()
        pve.wait(timeout=10)
    ran.code = (root / "code.log").read_text().splitlines() if (root / "code.log").exists() else []
    return ran


@pytest.fixture(scope="module")
def opened(tmp_path_factory):
    return _code(tmp_path_factory.mktemp("code_opened"), "code alpha")


@pytest.fixture(scope="module")
def missing(tmp_path_factory):
    return _code(tmp_path_factory.mktemp("code_missing"), "code gamma", vm="gamma", piped=True)


def test_a_window_is_drawn_by_its_steps_with_its_vm_in_the_header_and_opens(opened):
    assert opened.rc == 0, opened.text
    opened.assert_drawn_and_gone()
    frames = "\n".join(opened.frames)
    assert re.search(rf"code alpha · started \d\d:\d\d +VM {VMID} · \d+\.\d+\.\d+\.\d+", frames), frames
    assert re.search(r"step \d of 9", frames), "the footer counts the steps"
    assert any(line.startswith("code --remote ssh-remote+") for line in opened.code), opened.code


def test_the_summary_says_each_step_and_no_row_of_the_fleets_skipped_tasks(opened):
    table = opened.transcript.split("STEPS", 1)[1].split("\n\n", 1)[0]
    # Each step by its whole name, in its order; just deploy's fleet, skipped, no row.
    said = [re.match(r"^ ✓ (.+?) +\d", line).group(1) for line in table.splitlines()[1:]]
    assert said == list(STEPS), table


def test_a_vm_that_is_not_there_is_said_under_the_step_it_failed_in(missing):
    text = missing.text
    assert missing.rc == 2, text
    assert re.search(r"^✗ find the VM · gamma: Find the VM", text, re.M), text
    assert "Fail as just code does, outside just deploy" not in text.split("claude-on-proxmox  code", 1)[0]
    assert re.search(r"^claude-on-proxmox  code gamma · failed after .* at find the VM", text, re.M), text
