"""`just destroy` drawn live: the real playbooks/destroy.yml through the stdout callback, as the justfile runs it.

Against the fakes test_release.py runs destroy's plays against - the fake Proxmox, GitHub and Tailscale APIs on
loopback, its stand-in VM homes (test_release._hosts) - with every path a run writes pinned into the test's own
directory: the managed SSH config and the user's, the saved sessions, and a HOME of the run's own (the live kit's),
so known_hosts is never the developer's. A refusing ssh is first on PATH. What a person sees: the confirmation
answered, the four stages by play, each VM's release by its blocks, the VM the run started said, each VM's outcome,
the region gone; a VM Proxmox would not delete, the one VM failed; and a deletion not confirmed, said as an answer
(docs/architecture/custom-code.md §7.5).
"""

from __future__ import annotations

import json
import os
import re
import shutil

import harness
import live_kit as kit
import pytest
import test_release as tr

PROMPT = "Type yes to continue"
FIRST_VMID = 9101
# A VM signed in to everything, with sessions of its own; and one the vault's token signed in, left stopped.
EVERYTHING = {"claude": True, "gh": tr.DEVICE_TOKEN, "device_marker": True, "api": "token", "sessions": True}
VMS = {
    "alpha": {"running": True, "case": {**EVERYTHING, "node": "nALPHA"}},
    "beta": {"running": False, "case": {"gh": tr.VAULT_TOKEN, "vault": True, "node": "nBETA"}},
}
REFUSING_SSH = '#!/bin/sh\necho "ssh: connect to host stand-in port 22: Connection refused" >&2\nexit 255\n'


def _seed(base, vms):
    """Clone each VM in the fake from the template, tag it, start the running ones."""
    for offset, (name, spec) in enumerate(vms.items()):
        vmid = FIRST_VMID + offset
        harness._pve(base, "POST", "nodes/pve/qemu/9000/clone", newid=vmid, name=name)
        harness._pve(base, "PUT", f"nodes/pve/qemu/{vmid}/config", tags="claude-on-proxmox")
        if spec["running"]:
            harness._pve(base, "POST", f"nodes/pve/qemu/{vmid}/status/start")
            # The agent of a VM already up answers: the fake refuses the first polls after a start.
            for _ in range(3):
                try:
                    harness._pve(base, "GET", f"nodes/pve/qemu/{vmid}/agent/network-get-interfaces")
                except Exception:  # noqa: BLE001 - the refusals are the point
                    pass


def _inventory(root, port, hostvars):
    """The project's controller inventory and defaults, the fake's endpoint, and every path a run writes pinned."""
    inventory = root / "inventory"
    (inventory / "group_vars" / "all").mkdir(parents=True)
    (inventory / "host_vars").mkdir()
    shutil.copy(harness.REPO / "inventory" / "controller.yml", inventory / "controller.yml")
    defaults = harness.REPO / "inventory" / "group_vars" / "all" / "defaults.yml"
    (inventory / "group_vars" / "all" / "defaults.yml").symlink_to(defaults)
    pinned = {
        "proxmox_api_host": "127.0.0.1",
        "proxmox_api_port": port,
        "vault_proxmox_api_token_secret": harness.PVE_SECRET,
        "vm_user": "dev",
        "vm_ssh_public_keys": [
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILiveKitKeyDoNotUse0000000000000000000000000 live@test"
        ],
        "claude_ssh_config_file": str(root / "ssh" / "claude-on-proxmox.conf"),
        "claude_ssh_user_config": str(root / "ssh" / "config"),
        "claude_ssh_config_include": False,
        "destroy_sessions_dir": str(root / "saved"),
        "login_desktop": False,
        "tailscale_upload_logs": False,
    }
    (inventory / "group_vars" / "all" / "pinned.yml").write_text(json.dumps(pinned, indent=2))
    for name, values in hostvars.items():
        # The address is add_host's, from the fake's agent.
        values = {key: value for key, value in values.items() if key != "claude_vm_release_address"}
        (inventory / "host_vars" / f"{name}.yml").write_text(json.dumps(values, indent=2))
    (root / "ssh").mkdir()
    return inventory


def _destroy(root, answer, cols=100, rows=30, piped=False, protected=()):
    (root / "saved").mkdir()
    sshbin = root / "sshbin"
    sshbin.mkdir()
    (sshbin / "ssh").write_text(REFUSING_SSH)
    (sshbin / "ssh").chmod(0o755)
    cert, key = harness._certificate(root)
    servers = []
    try:
        pve, port = harness._start(
            tr.FAKES / "fake_pve_api.py", str(cert), str(key), str(root / "pve"), harness.PVE_SECRET
        )
        servers.append(pve)
        (root / "git").mkdir()
        github, github_port = harness._start(tr.FAKES / "fake_github_api.py", "dev", str(root / "git"))
        servers.append(github)
        devices = [f"{spec['case']['node']}={vm}" for vm, spec in VMS.items()]
        tailscale, tailscale_port = harness._start(tr.FAKES / "fake_tailscale_api.py", *devices)
        servers.append(tailscale)
        base = f"https://127.0.0.1:{port}"
        _seed(base, VMS)
        for offset, name in enumerate(VMS):
            if name in protected:
                # Proxmox refuses to remove a VM with protection on: its deletion fails, and the rescue records why.
                harness._pve(base, "PUT", f"nodes/pve/qemu/{FIRST_VMID + offset}/config", protection=1)
        # As a real cluster lists them: a start shows a listing later.
        harness._pve(base, "PUT", "fake/listing-lag", listings=1)
        cases = {vm: spec["case"] for vm, spec in VMS.items()}
        hostvars = tr._hosts(root, cases, f"http://127.0.0.1:{github_port}", f"http://127.0.0.1:{tailscale_port}")
        inventory = _inventory(root, port, hostvars)
        title = "destroy " + " ".join(VMS)
        env = kit.environment(root, title, ANSIBLE_ROLES_PATH=str(harness.REPO / "roles"))
        env["PATH"] = f"{sshbin}{os.pathsep}{env['PATH']}"
        extra = {"vm_name": ",".join(VMS), "ansible_become": False, "tailscale_settle_seconds": 3}
        if piped:
            extra["confirm_destroy"] = "yes"
        argv = kit.playbook(root, inventory, tr.DESTROY, "-e", json.dumps(extra))
        if piped:
            return kit.piped(argv, root, env, timeout=300)
        return kit.at_a_terminal(argv, root, env, cols=cols, rows=rows, answers={PROMPT: answer}, timeout=300)
    finally:
        # As harness._serve stops them: a fake has no process group of its own.
        for server in servers:
            server.terminate()
            server.wait(timeout=10)


@pytest.fixture(scope="module")
def confirmed(tmp_path_factory):
    return _destroy(tmp_path_factory.mktemp("destroy_confirmed"), b"yes\r")


@pytest.fixture(scope="module")
def refused(tmp_path_factory):
    return _destroy(tmp_path_factory.mktemp("destroy_refused"), None, piped=True, protected=("beta",))


@pytest.fixture(scope="module")
def declined(tmp_path_factory):
    return _destroy(tmp_path_factory.mktemp("destroy_declined"), b"\r")


def _frames(ran):
    """Each region drawn, its escapes left out: what the run showed while it went."""
    frames = re.findall(rb"\x1b\[\?2026h(.*?)\x1b\[\?2026l", ran.raw, re.S)
    return [re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", f.decode(errors="replace")).replace("\r", "") for f in frames]


def test_a_destroy_is_drawn_by_its_four_stages_and_each_vms_release_by_its_blocks(confirmed):
    assert confirmed.rc == 0, confirmed.text
    confirmed.assert_drawn_and_gone()
    seen = "\n".join(_frames(confirmed))
    for stage in ("check", "release", "delete", "aliases"):
        assert re.search(rf"^ \S {stage} ", seen, re.M), stage
    # The release's steps are its blocks, in their order; claude_code's pause before the save is the sessions', and
    # the include of its resume in the save's rescue, never reached, counted there too: no step of the role's own.
    for step in ("sessions", "Claude", "GitHub", "Tailscale"):
        assert re.search(rf"^     \S {step} ", seen, re.M), step
    assert not re.search(r"^     \S claude_code ", seen, re.M), seen
    assert "started beta" in seen, "the check says the VM it started to release"


def test_the_summary_says_each_vms_outcome_and_the_run_done(confirmed):
    screen = confirmed.transcript
    assert re.search(r"^claude-on-proxmox  destroy alpha beta · done in .*$", screen, re.M), screen
    for stage in ("release", "delete"):
        row = next(line for line in screen.splitlines() if line.startswith(f" ✓ {stage}"))
        assert "alpha ✓ · beta ✓" in row, row
    assert "started beta" in screen.split("STAGES", 1)[1]


def test_a_vm_proxmox_would_not_delete_is_the_failure_the_last_play_raises_and_the_other_is_done(refused):
    text = refused.text
    assert refused.rc == 2, text
    # The deletion's failure, under its stage and VM; the last play's raise of it, on this machine, adds only its words.
    assert re.search(r"^✗ proxmox_vm · beta: Delete the VM", text, re.M) and "protection mode enabled" in text, text
    # Its stage failed as the rescue recorded why, before the raise: so said as the stage ended.
    assert re.search(r"^✗ delete +\d+ tasks", text, re.M), text
    assert "Fail for each VM that was not deleted" not in text.split("claude-on-proxmox  destroy", 1)[0]
    head = re.search(r"^claude-on-proxmox  destroy alpha beta · done in .* · 1 VM failed: beta$", text, re.M)
    assert head, text
    delete = next(line for line in text.splitlines() if line.startswith(" ✗ delete"))
    assert "alpha ✓ · beta ✗" in delete, delete


def test_a_deletion_not_confirmed_is_said_as_an_answer_not_a_failure(declined):
    screen = declined.transcript
    assert declined.rc == 2, declined.text
    assert "Nothing was deleted: the deletion was not confirmed." in screen
    assert re.search(r"^claude-on-proxmox  destroy alpha beta · declined · \d+ s", screen, re.M), screen
    assert "✗" not in screen and "STAGES" not in screen
    assert declined.screen.titles[-1] == "destroy alpha beta · declined"
