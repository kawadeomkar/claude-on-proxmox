"""playbooks/init_check.yml, just init's read-only checks: run against the fake Proxmox, never a real one.

Each run's inventory is the test's own: group_vars/all/defaults.yml is the
project's, beside a file of the test's settings, and the Proxmox host is either
a stand-in host on the local connection, whose stand-in `hostname` prints its
node name, or a host on ssh with a stand-in `ssh` first on PATH that records
its call and exits 255. No run reaches a real ssh: ansible.cfg's accept-new
would add a host key to the developer's ~/.ssh/known_hosts, and
test_no_run_reaches_a_real_ssh holds every run to one of the two. The vault
file and the directory searched for a public key are the test's too.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from harness import (
    FAKES,
    LOGINS,
    PVE_SECRET,
    REPO,
    _certificate,
    _desktop,
    _failures,
    _free_port,
    _left_to_do,
    _messages,
    _pve,
    _results,
    _run,
    _run_at_a_terminal,
    _serve,
    _stand_in_hosts,
)

PLAYBOOK = REPO / "playbooks" / "init_check.yml"
CLUSTER_VMS = REPO / "playbooks" / "tasks" / "cluster_vms.yml"
VAULT_HEADER = "$ANSIBLE_VAULT;1.1;AES256\n"
ROLE_8 = [
    "VM.Allocate",
    "VM.Clone",
    "VM.Config.CPU",
    "VM.Config.Cloudinit",
    "VM.Config.Disk",
    "VM.Config.Memory",
    "VM.Config.Network",
    "VM.Config.Options",
    "VM.PowerMgmt",
    "VM.Audit",
    "Datastore.AllocateSpace",
    "Datastore.Audit",
    "SDN.Use",
]
SSH_STAND_IN = """#!/bin/sh
printf '%s\\n' "$*" >> "$(dirname "$0")/ssh-calls.log"
echo "ssh: connect to host 127.0.0.1 port 22: Connection refused" >&2
exit 255
"""
# The node it says it is: pve, or what hostname-is beside it holds.
HOSTNAME_STAND_IN = """#!/bin/sh
printf '%s\\n' "$*" >> "$(dirname "$0")/hostname-calls.log"
cat "$(dirname "$0")/hostname-is" 2>/dev/null || echo pve
"""
LOCAL_EXAMPLE = REPO / "inventory" / "group_vars" / "all" / "local.yml.example"
DEPLOY_NEXT = "Next: just deploy alpha, which creates a VM named alpha (any name will do)."


@pytest.fixture
def fake_pve(tmp_path):
    """The fake Proxmox on a loopback port: its base URL."""
    cert, key = _certificate(tmp_path)
    for base in _serve(FAKES / "fake_pve_api.py", str(cert), str(key), str(tmp_path / "pve"), PVE_SECRET):
        yield base.replace("http://", "https://")


def _port(base: str) -> int:
    return int(base.rsplit(":", 1)[1])


def _inventory_of(
    root: Path, base: str, pve: str, settings: dict | None, vault: str, host: str, proxmox_host: bool = True
) -> tuple[dict, dict]:
    """The test's inventory under root - the project's defaults.yml, and local.yml: the example, then the
    settings - and the Proxmox host; returns the extra vars pointing the playbook at root's files, and the
    environment of the run."""
    assert pve in ("local", "ssh"), pve
    group_vars = root / "group_vars" / "all"
    group_vars.mkdir(parents=True, exist_ok=True)
    if not (group_vars / "defaults.yml").exists():
        (group_vars / "defaults.yml").symlink_to(REPO / "inventory" / "group_vars" / "all" / "defaults.yml")
    local = group_vars / "local.yml"
    if not local.exists():
        test_settings = {
            "proxmox_api_port": _port(base),
            "vault_proxmox_api_token_secret": PVE_SECRET,
            "vault_github_username": "octocat",
            "vm_ssh_public_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyDoNotUse test@example"],
        }
        local.write_text(LOCAL_EXAMPLE.read_text() + yaml.safe_dump(test_settings | (settings or {})))
    vault_file = root / "vault.yml"
    vault_file.write_text(vault)
    (root / "ssh").mkdir(exist_ok=True)
    if pve == "local":
        if not (root / "bin").exists():
            hostvars = _stand_in_hosts(root, {"pve": LOGINS["logged-out"]})["pve"]
            (root / "bin" / "hostname").write_text(HOSTNAME_STAND_IN)
            (root / "bin" / "hostname").chmod(0o755)
        else:
            hostvars = {"ansible_connection": "local", "ansible_python_interpreter": str(root / "pve" / "python")}
        hostvars |= {"ansible_host": host, "ansible_user": "root"}
        env = {}
    else:
        stand_ins = root / "ssh-stand-in"
        stand_ins.mkdir(exist_ok=True)
        (stand_ins / "ssh").write_text(SSH_STAND_IN)
        (stand_ins / "ssh").chmod(0o755)
        hostvars = {"ansible_connection": "ssh", "ansible_host": host, "ansible_user": "root"}
        env = {"PATH": f"{stand_ins}{os.pathsep}{_desktop(root)}{os.pathsep}{os.environ.get('PATH', os.defpath)}"}
    hosts = {"pve": hostvars} if proxmox_host else {}
    (root / "proxmox.yml").write_text(yaml.safe_dump({"proxmox": {"hosts": hosts}}))
    paths = {"init_vault_file": str(vault_file), "init_ssh_dir": str(root / "ssh"), "init_local_file": str(local)}
    return paths, env


def _check(
    root: Path,
    base: str,
    pve: str,
    settings: dict | None = None,
    *args: str,
    vault: str = VAULT_HEADER,
    host: str = "127.0.0.1",
    extra: dict | None = None,
    env: dict | None = None,
    proxmox_host: bool = True,
) -> dict:
    """init_check.yml with the fake Proxmox at base, with no terminal; pve is "local" (a stand-in host) or "ssh"."""
    paths, run_env = _inventory_of(root, base, pve, settings, vault, host, proxmox_host)
    extra_vars = json.dumps(paths | (extra or {}))
    return _run(root, PLAYBOOK, {}, "-i", str(root / "proxmox.yml"), "-e", extra_vars, *args, env=(env or {}) | run_env)


def _check_at_a_terminal(root: Path, base: str, pve: str, settings: dict | None, steps, extra: dict | None = None):
    """init_check.yml at a pseudo-terminal, init_ask on, typing steps; rc, output, log."""
    paths, run_env = _inventory_of(root, base, pve, settings, VAULT_HEADER, "127.0.0.1")
    return _run_at_a_terminal(
        root,
        PLAYBOOK,
        {},
        paths | {"init_ask": True} | (extra or {}),
        steps=steps,
        args=["-i", str(root / "proxmox.yml")],
        env=run_env,
    )


def _said(report: dict) -> str:
    return "\n".join(str(message) for message in _messages(report, "Say what each check found").values())


def _next(report: dict) -> dict:
    """What the run recorded for NEXT STEPS, by matter: just init's only, under a key no VM can have."""
    left = _left_to_do(report)
    assert set(left) <= {"just init"}, left
    return left.get("just init", {})


def _calls(tmp_path: Path) -> list[dict]:
    log = tmp_path / "pve" / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _ssh_calls(root: Path) -> str:
    log = root / "ssh-stand-in" / "ssh-calls.log"
    return log.read_text() if log.exists() else ""


def _hostname_calls(root: Path) -> str:
    log = root / "bin" / "hostname-calls.log"
    return log.read_text() if log.exists() else ""


# ------------------------------------------------------------------- the checks ---
def test_nothing_is_checked_while_a_value_is_the_examples(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", host="192.0.2.10")
    assert report["rc"] == 0, _failures(report)
    said = "\n".join(str(m) for m in _messages(report, "Say what is not set yet").values())
    assert "Proxmox is not checked yet: the Proxmox host address (inventory/hosts.yml) is still" in said
    assert _calls(tmp_path) == [] and _ssh_calls(root) == ""
    # Nothing new for NEXT STEPS: init_settings.yml's, printed before, names what is missing.
    assert _left_to_do(report) == {}


def test_every_check_passes_on_a_host_with_its_template_and_every_default_fits(tmp_path: Path, fake_pve: str) -> None:
    # As just init runs it at a terminal: every default fits, so nothing is
    # asked or written.
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", extra={"init_ask": True})
    assert report["rc"] == 0, _failures(report)
    said = _said(report)
    assert f"ok   Proxmox VE 8.2.4 at 127.0.0.1:{_port(fake_pve)} accepted ansible@pve!ansible" in said
    assert "ok   the token holds every privilege the project uses on /vms" in said
    assert "ok   the template ubuntu-24.04-cloudinit is on pve, so just deploy needs no root SSH" in said
    assert "ok   the VMs get 1 SSH key" in said and "!!" not in said
    # The template exists: no SSH at all.
    assert _ssh_calls(root) == ""
    assert "ok   node pve: it holds the template, and just deploy creates the VMs there" in said
    assert "ok   storage local-lvm: the token can use it" in said
    assert "ok   bridge vmbr0, the template's: the token has SDN.Use on it" in said
    assert "written" not in said
    asked = [
        name for name, _, result in _results(report) if name.startswith("init_ask | Ask") and not result.get("skipped")
    ]
    assert asked == []
    # The one step left, said last and kept for NEXT STEPS.
    assert said.rstrip().endswith("\n" + DEPLOY_NEXT)
    assert _next(report) == {"init_check": DEPLOY_NEXT}


def test_a_wrong_secret_names_the_token(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {"vault_proxmox_api_token_secret": "11111111-2222-3333-4444-555555555555"})
    assert report["rc"] != 0
    assert "!!   Proxmox at 127.0.0.1:" in _said(report) and "refused the token ansible@pve!ansible (401)" in _said(
        report
    )
    assert "just init found" in "\n".join(_failures(report).values())


def test_nothing_listening_names_the_address(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    closed = _free_port()
    report = _check(root, fake_pve, "ssh", {"proxmox_api_port": closed})
    assert report["rc"] != 0
    assert f"nothing answered at https://127.0.0.1:{closed}" in _said(report)


@pytest.mark.parametrize(("version", "agent"), [("8.2.4", "VM.Monitor"), ("9.2.13", "VM.GuestAgent.Audit")])
def test_a_missing_privilege_is_named_with_its_path(tmp_path: Path, fake_pve: str, version: str, agent: str) -> None:
    _pve(fake_pve, "PUT", "fake/version", version=version)
    _pve(
        fake_pve,
        "PUT",
        "fake/privileges",
        token="ansible",
        path="/vms",
        privs=",".join(p for p in ROLE_8 if p != "VM.Clone"),
    )
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh")
    assert report["rc"] != 0
    said = _said(report)
    assert f"!!   the token lacks VM.Clone, {agent} on /vms" in said
    assert "pveum role modify AnsibleVM -append 1 -privs 'VM.Clone " + agent + "'" in said


NO_TEMPLATE = {"proxmox_template_name": "not-built-yet", "proxmox_template_vmid": 9999}


def test_without_the_template_root_ssh_is_checked_and_reads_the_node(tmp_path: Path, fake_pve: str) -> None:
    # With the log at -vvv and every module's arguments injected, as the most a
    # run can print or log: the token's secret appears nowhere.
    root = tmp_path / "run"
    env = {"ANSIBLE_LOG_PATH": str(tmp_path / "run.log"), "ANSIBLE_LOG_VERBOSITY": "3"}
    report = _check(root, fake_pve, "local", NO_TEMPLATE, "-e", '{"ansible_inject_invocation": true}', env=env)
    assert report["rc"] == 0, _failures(report)
    assert PVE_SECRET not in json.dumps(report) + (tmp_path / "run.log").read_text()
    said = _said(report)
    assert "ok   there is no template not-built-yet yet: just deploy builds it at VMID 9999, over root SSH" in said
    assert "ok   root SSH to 127.0.0.1 works, and it is node pve (the template is not built yet)" in said
    assert _hostname_calls(root).splitlines() == ["-s"]
    facts = [r.get("ansible_facts", {}) for name, _, r in _results(report) if name == "Record root SSH's result"]
    assert facts[0]["init_ssh_node"] == "pve"


# The fake's template is one this version builds; another CPU type makes it one the settings would build otherwise.
DIFFERS = {"proxmox_template_cpu_type": "x86-64-v2-AES"}


def test_a_template_that_differs_from_its_settings_is_said_and_root_ssh_checked(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "local", DIFFERS)
    assert report["rc"] == 0, _failures(report)
    said = _said(report)
    assert (
        "ok   the template ubuntu-24.04-cloudinit on pve differs from its settings (cpu host → x86-64-v2-AES):"
        " just deploy rebuilds it, over root SSH" in said
    ), said
    assert "ok   root SSH to 127.0.0.1 works, and it is node pve (just deploy rebuilds the template)" in said
    assert _hostname_calls(root).splitlines() == ["-s"]


def test_a_template_kept_as_it_is_needs_no_root_ssh(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {**DIFFERS, "proxmox_template_rebuild": False})
    assert report["rc"] == 0, _failures(report)
    said = _said(report)
    assert (
        "ok   the template ubuntu-24.04-cloudinit on pve differs from its settings (cpu host → x86-64-v2-AES), and is"
        " kept as it is since proxmox_template_rebuild is false, so just deploy needs no root SSH" in said
    ), said
    assert not _ssh_calls(root)


def test_root_ssh_that_fails_says_how_to_install_the_key(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", NO_TEMPLATE)
    assert report["rc"] != 0
    said = _said(report)
    assert "!!   root SSH to 127.0.0.1 failed" in said and "ssh-copy-id root@127.0.0.1" in said
    assert _ssh_calls(root)


def test_a_vmid_taken_where_the_template_would_be_built_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "POST", "nodes/pve/qemu/9000/clone", newid=9050, name="just-a-vm")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "local", {"proxmox_template_name": "not-built-yet", "proxmox_template_vmid": 9050})
    assert report["rc"] != 0
    said = _said(report)
    assert "!!   there is no template not-built-yet yet, and VMID 9050, where just deploy would build it" in said
    assert "is taken by just-a-vm on pve:" in said


def test_a_template_of_another_name_at_the_template_vmid_is_no_template(tmp_path: Path, fake_pve: str) -> None:
    # just deploy finds the template by its name: one of another name at
    # proxmox_template_vmid is no template of this project's, and in the way.
    root = tmp_path / "run"
    report = _check(root, fake_pve, "local", {"proxmox_template_name": "the-one-deploy-looks-for"})
    assert report["rc"] != 0
    assert "is taken by ubuntu-24.04-cloudinit on pve" in _said(report)


@pytest.mark.parametrize(("vmid", "holder"), [(8000, "a qemu on pve2, which is down"), (9003, "ours-container on pve")])
def test_a_vmid_another_guest_holds_is_named_with_its_node(
    tmp_path: Path, fake_pve: str, vmid: int, holder: str
) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "local", {"proxmox_template_name": "not-built-yet", "proxmox_template_vmid": vmid})
    assert report["rc"] != 0
    assert f"is taken by {holder}:" in _said(report)


def test_a_template_on_another_node_than_a_proxmox_node_set_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    before = (LOCAL_EXAMPLE.read_text(), None)
    report = _check(root, fake_pve, "ssh", {"proxmox_node": "nuc"})
    assert report["rc"] != 0
    said = _said(report)
    assert "!!   the template is on pve, but proxmox_node is nuc, and VMs are cloned on the node that holds" in said
    assert "or build a template on nuc with a proxmox_template_name of its own" in said
    assert before[0] in (root / "group_vars" / "all" / "local.yml").read_text()


def test_a_host_that_is_another_node_than_a_proxmox_node_set_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "local", NO_TEMPLATE | {"proxmox_node": "nuc"})
    assert report["rc"] != 0
    # pve2 is down: pve is the only node there is.
    assert "!!   pve is the only node, but proxmox_node is nuc" in _said(report)


def test_no_key_suggests_one_from_the_ssh_directory(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    (root / "ssh").mkdir(parents=True)
    (root / "ssh" / "id_test.pub").write_text("ssh-ed25519 AAAA test\n")
    report = _check(root, fake_pve, "ssh", {"vm_ssh_public_keys": [], "vm_password": ""})
    assert report["rc"] != 0
    assert f'vm_ssh_public_keys: ["<the line in {root / "ssh" / "id_test.pub"}>"]' in _said(report)


def test_a_password_alone_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    # The console takes it; SSH, which just deploy configures the VMs over, does not.
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {"vm_ssh_public_keys": [], "vm_password": "a-console-password"})
    assert report["rc"] != 0
    assert "!!   the VMs get only vm_password, which is for the Proxmox console" in _said(report)
    assert "a-console-password" not in json.dumps(report)


def test_a_privilege_without_propagate_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    privs = [p if p != "VM.Clone" else "VM.Clone:0" for p in ROLE_8] + ["VM.Monitor"]
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/vms", privs=",".join(privs))
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh")
    assert report["rc"] != 0
    assert "!!   the token holds VM.Clone on /vms but not on the VMs under it" in _said(report)
    assert "-propagate 1" in _said(report)


def test_a_token_with_no_privileges_is_told_about_privilege_separation(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/vms", privs="")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh")
    assert report["rc"] != 0
    assert "has none of its user's: make it with --privsep 0" in _said(report)
    assert "pveum aclmod /vms -token 'ansible@pve!ansible'" in _said(report)


def test_every_check_passes_on_proxmox_ve_9(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/version", version="9.2.13")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh")
    assert report["rc"] == 0, _failures(report)
    assert "ok   Proxmox VE 9.2.13" in _said(report) and "!!" not in _said(report)


def test_an_ipv6_address_is_said_as_a_url_says_it(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    closed = _free_port()
    report = _check(root, fake_pve, "ssh", {"proxmox_api_port": closed}, host="::1")
    assert report["rc"] != 0
    assert f"nothing answered at https://[::1]:{closed}" in _said(report)


def test_a_plaintext_vault_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", vault="vault_proxmox_api_token_secret: x\n")
    assert report["rc"] != 0
    assert "!!   vault.yml is not encrypted, and every recipe refuses it: just vault encrypts it" in _said(report)
    assert _next(report) == {
        "init_check": "just init found 1 problem for just deploy: fix it, then run just init again.\n"
        "  !!   vault.yml is not encrypted, and every recipe refuses it: just vault encrypts it"
    }


# The token's secret nowhere in the output or the log: test_without_the_template_root_ssh_is_checked_and_reads_the_node.


# ------------------------------------------------- the node, storage and bridge ---
def _local(root: Path) -> str:
    return (root / "group_vars" / "all" / "local.yml").read_text()


# Every default fitting, nothing written: test_every_check_passes_on_a_host_with_its_template_and_every_default_fits.


@pytest.mark.parametrize("terminal", [True, False], ids=["terminal", "no-terminal"])
def test_the_templates_node_is_written_in_place_of_the_default(tmp_path: Path, fake_pve: str, terminal: bool) -> None:
    _pve(fake_pve, "PUT", "fake/nodes/pve2", status="online")
    _pve(fake_pve, "PUT", "fake/vm-node", vmid="9000", node="pve2")
    _pve(fake_pve, "PUT", "fake/storages", node="pve2", storages="local-lvm:lvmthin")
    _pve(fake_pve, "PUT", "fake/bridges", node="pve2", bridges="vmbr0")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", extra={"init_ask": True} if terminal else None)
    said = _said(report)
    if terminal:
        assert report["rc"] == 0, _failures(report)
        assert "ok   node pve2: written into local.yml as proxmox_node, since the template is on pve2" in said
        assert '\nproxmox_node: "pve2"\n' in _local(root) and "# proxmox_node: pve\n" not in _local(root)
        assert "ok   storage local-lvm: the token can use it" in said
    else:
        assert report["rc"] != 0
        assert (
            "!!   the template is on pve2, but proxmox_node is pve" in said
            and "(just init at a terminal writes it)" in said
        )
        assert "# proxmox_node: pve\n" in _local(root)


def test_without_a_template_two_nodes_take_the_node_ssh_reaches(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/nodes/pve2", status="online")
    _pve(fake_pve, "PUT", "fake/storages", node="pve2", storages="local-lvm:lvmthin")
    _pve(fake_pve, "PUT", "fake/bridges", node="pve2", bridges="vmbr0")
    root = tmp_path / "run"
    _inventory_of(root, fake_pve, "local", NO_TEMPLATE, VAULT_HEADER, "127.0.0.1")
    (root / "bin" / "hostname-is").write_text("pve2\n")
    report = _check(root, fake_pve, "local", extra={"init_ask": True})
    assert report["rc"] == 0, _failures(report)
    assert "ok   node pve2: written into local.yml as proxmox_node, since 127.0.0.1 is node pve2" in _said(report)
    assert '\nproxmox_node: "pve2"\n' in _local(root)


def test_two_nodes_and_no_ssh_cannot_tell_the_node_and_nothing_is_asked_for_storage(
    tmp_path: Path, fake_pve: str
) -> None:
    # And while the node is unknown, a storage the default is not is neither
    # asked about nor written.
    _pve(fake_pve, "PUT", "fake/nodes/pve2", status="online")
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-zfs:zfspool")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", NO_TEMPLATE, extra={"init_ask": True})
    assert report["rc"] != 0
    said = _said(report)
    assert "!!   which node 127.0.0.1 is cannot be told until root SSH to it works" in said
    assert "proxmox_node:" not in _local(root).replace("# proxmox_node:", "")
    assert "--   the storage is not checked: the node is not known yet" in said
    assert "--   the bridge is not checked: the node is not known yet" in said
    assert "\nproxmox_storage:" not in _local(root)
    asked = [
        name for name, _, result in _results(report) if name.startswith("init_ask | Ask") and not result.get("skipped")
    ]
    assert asked == []


def test_a_storage_is_chosen_among_those_the_token_can_use(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-zfs:zfspool,tank-vms:nfs,local:dir")
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/storage/local", privs="Datastore.Audit")
    root = tmp_path / "run"
    rc, output, _ = _check_at_a_terminal(root, fake_pve, "ssh", None, [(b"Number, Enter to decide later:", b"2\n")])
    assert rc == 0, output
    assert "1) local-zfs" in output and "2) tank-vms" in output
    assert "-  local       dir       the token lacks Datastore.AllocateSpace on /storage/local" in output
    assert '\nproxmox_storage: "tank-vms"\n' in _local(root) and "# proxmox_storage: local-lvm\n" not in _local(root)
    assert "storage tank-vms: written into local.yml as proxmox_storage" in output


def test_one_usable_storage_is_offered_yes_or_no(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-zfs:zfspool")
    root = tmp_path / "run"
    rc, output, _ = _check_at_a_terminal(root, fake_pve, "ssh", None, [(b"Use local-zfs? y or n", b"y\n")])
    assert rc == 0, output
    assert '\nproxmox_storage: "local-zfs"\n' in _local(root)


def test_enter_at_the_storage_leaves_it_and_says_so(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-zfs:zfspool")
    root = tmp_path / "run"
    rc, output, _ = _check_at_a_terminal(root, fake_pve, "ssh", None, [(b"Use local-zfs? y or n", b"\n")])
    assert rc != 0
    assert "storage local-lvm is not on pve: set proxmox_storage in local.yml" in output
    assert "\nproxmox_storage:" not in _local(root)


def test_without_a_terminal_a_storage_that_does_not_fit_is_reported(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-zfs:zfspool,tank-vms:nfs")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh")
    assert report["rc"] != 0
    said = _said(report)
    assert "!!   storage local-lvm is not on pve: set proxmox_storage in local.yml to one the token can use" in said
    assert "- local-zfs, tank-vms - or run just init at a terminal" in said
    assert "\nproxmox_storage:" not in _local(root)


def test_a_storage_set_that_is_not_listed_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {"proxmox_storage": "gone"}, extra={"init_ask": True})
    assert report["rc"] != 0
    assert "!!   proxmox_storage is gone in local.yml, which the token cannot see on pve: local-lvm instead" in _said(
        report
    )


def test_the_templates_bridge_without_sdn_use_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/sdn/zones/localnetwork/vmbr0", privs="SDN.Audit")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", extra={"init_ask": True})
    assert report["rc"] != 0
    assert "!!   bridge vmbr0, the template's, which every clone inherits: the token lacks SDN.Use" in _said(report)


def test_without_a_template_a_bridge_is_offered_and_one_it_cannot_use_shown(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/bridges", node="pve", bridges="vmbr1,vmbr2")
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/sdn/zones/localnetwork/vmbr2", privs="SDN.Audit")
    root = tmp_path / "run"
    rc, output, _ = _check_at_a_terminal(root, fake_pve, "local", NO_TEMPLATE, [(b"Use vmbr1? y or n", b"y\n")])
    assert rc == 0, output
    assert "the token lacks SDN.Use on /sdn/zones/localnetwork/vmbr2" in output
    assert '\nproxmox_bridge: "vmbr1"\n' in _local(root)


def test_a_storage_given_is_written_even_where_the_default_fits(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", extra={"init_given": {"storage": "local-lvm"}})
    assert report["rc"] == 0, _failures(report)
    assert '\nproxmox_storage: "local-lvm"\n' in _local(root)


def test_a_storage_given_that_will_not_do_fails_and_writes_nothing(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-lvm:lvmthin,local:dir")
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/storage/local", privs="Datastore.Audit")
    root = tmp_path / "run"
    for storage, said in (("local", "--storage local: the token lacks Datastore.AllocateSpace on /storage/local"),
                          ("nfs", "--storage nfs: the token cannot see it on pve (local-lvm, local)")):  # fmt: skip
        report = _check(root, fake_pve, "ssh", extra={"init_given": {"storage": storage}})
        assert report["rc"] != 0
        assert said in "\n".join(_failures(report).values())
        assert "\nproxmox_storage:" not in _local(root)


def test_a_storage_given_that_differs_from_the_one_set_fails(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-lvm:lvmthin,local-zfs:zfspool")
    root = tmp_path / "run"
    report = _check(
        root, fake_pve, "ssh", {"proxmox_storage": "local-lvm"}, extra={"init_given": {"storage": "local-zfs"}}
    )
    assert report["rc"] != 0
    assert "already sets proxmox_storage: local-lvm" in "\n".join(_failures(report).values())


def test_a_bridge_given_while_the_template_exists_is_refused(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", extra={"init_given": {"bridge": "vmbr1", "storage": "local-lvm"}})
    assert report["rc"] != 0
    assert "--bridge vmbr1: the template ubuntu-24.04-cloudinit is built" in "\n".join(_failures(report).values())
    # Refused before anything was written: the storage given too.
    assert "\nproxmox_storage:" not in _local(root)


def test_a_bridge_given_equal_to_the_templates_is_nothing_to_do(tmp_path: Path, fake_pve: str) -> None:
    # A scripted just init that gave --bridge runs again once deploy built the template.
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", extra={"init_given": {"bridge": "vmbr0"}})
    assert report["rc"] == 0, _failures(report)
    assert "\nproxmox_bridge:" not in _local(root)


# Nothing asked or written for storage while the node is unknown:
# test_two_nodes_and_no_ssh_cannot_tell_the_node_and_nothing_is_asked_for_storage.


def test_a_storage_given_while_the_node_is_unknown_is_refused(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/nodes/pve2", status="online")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", NO_TEMPLATE, extra={"init_given": {"storage": "local-lvm"}})
    assert report["rc"] != 0
    assert "--storage local-lvm cannot be checked yet (the node is not known yet)" in "\n".join(
        _failures(report).values()
    )
    assert "\nproxmox_storage:" not in _local(root)


def test_a_token_that_sees_no_storage_is_told_what_lists_one(tmp_path: Path, fake_pve: str) -> None:
    # The common ZFS case: the role granted on /storage/local-lvm, which the host does not have.
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="local-zfs:zfspool")
    _pve(fake_pve, "PUT", "fake/privileges", token="ansible", path="/storage/local-zfs", privs="")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh")
    assert report["rc"] != 0
    said = _said(report)
    assert "Proxmox lists one only to a token that holds Datastore.Audit or Datastore.AllocateSpace on it" in said
    assert "pveum aclmod /storage/<yours> -user ansible@pve -role AnsibleVM" in said


def test_an_inactive_storage_is_not_used(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "fake/storages", node="pve", storages="nas:nfs:inactive,local-zfs:zfspool")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {"proxmox_storage": "nas"})
    assert report["rc"] != 0
    assert "!!   storage nas is inactive on pve" in _said(report)


def test_a_template_with_no_bridge_is_a_problem(tmp_path: Path, fake_pve: str) -> None:
    _pve(fake_pve, "PUT", "nodes/pve/qemu/9000/config", delete="net0")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh")
    assert report["rc"] != 0
    assert "!!   the template's net0 has no bridge" in _said(report)


def test_no_host_in_the_proxmox_group_is_said_not_a_crash(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", NO_TEMPLATE | {"proxmox_api_host": "127.0.0.1"}, proxmox_host=False)
    assert report["rc"] != 0
    assert "which has none" in _said(report)


# ------------------------------------------------------------------ NEXT STEPS ---
def test_next_steps_keep_each_problem_with_its_fix_and_no_deploy(tmp_path: Path, fake_pve: str) -> None:
    root = tmp_path / "run"
    keyless = {"vm_ssh_public_keys": [], "vm_password": ""}
    report = _check(root, fake_pve, "ssh", keyless, vault="vault_proxmox_api_token_secret: x\n")
    assert report["rc"] != 0
    line = _next(report)["init_check"]
    problems = [text for text in _said(report).splitlines() if text.startswith("  !!   ")]
    assert len(problems) == 2, problems
    assert line == "\n".join(
        ["just init found 2 problems for just deploy: fix them, then run just init again.", *problems]
    )
    assert "ssh-keygen -t ed25519" in line and "just deploy alpha" not in line + _said(report)


def test_next_steps_leave_just_deploy_to_the_github_username(tmp_path: Path, fake_pve: str) -> None:
    # Every check passes, but the username is the example's: init_settings.yml's
    # NEXT STEPS names it, and just deploy is not next yet.
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {"vault_github_username": "your-github-username"})
    assert report["rc"] == 0, _failures(report)
    assert "!!" not in _said(report) and "just deploy alpha" not in _said(report)
    assert _next(report) == {"init_check": ""}


def test_next_steps_say_what_could_not_be_checked_when_nothing_is_wrong(tmp_path: Path, fake_pve: str) -> None:
    # The template on a node just gone down, still listed by name: its storage
    # and bridge cannot be read, nothing is wrong yet, and just deploy is not next.
    _pve(fake_pve, "PUT", "fake/vm-node", vmid="9000", node="pve2")
    _pve(fake_pve, "PUT", "fake/nodes/pve2", status="offline", named="1")
    root = tmp_path / "run"
    report = _check(root, fake_pve, "ssh", {"proxmox_node": "pve2"})
    assert report["rc"] == 0, _failures(report)
    assert "ok   node pve2: it holds the template" in _said(report) and "just deploy alpha" not in _said(report)
    assert _next(report) == {
        "init_check": "just init could not check all that just deploy needs: run it again once it can.\n"
        "  --   the storage is not checked: node pve2 is not up\n"
        "  --   the template's bridge is not checked: node pve2 is not up"
    }


# ------------------------------------------------------------------- static ---
def _tasks(node):
    for item in node if isinstance(node, list) else []:
        if isinstance(item, dict):
            if "tasks" in item:
                yield from _tasks(item["tasks"])
                continue
            yield item
            for key in ("block", "rescue", "always"):
                yield from _tasks(item.get(key))


READ_ONLY = {
    "ansible.builtin.uri",
    "ansible.builtin.command",
    "ansible.builtin.find",
    "ansible.builtin.slurp",
    "ansible.builtin.stat",
    "ansible.builtin.set_fact",
    "ansible.builtin.set_stats",
    "ansible.builtin.debug",
    "ansible.builtin.assert",
    "ansible.builtin.fail",
    "ansible.builtin.import_tasks",
    "ansible.builtin.include_tasks",
    "ansible.builtin.include_role",
    "ansible.builtin.meta",
}
# The one role entry point included: the template's comparison, which only records what differs.
ROLE_ENTRY_POINTS = {("proxmox_template", "drift")}
TASK_KEYS = {
    "name", "when", "register", "vars", "no_log", "failed_when", "changed_when", "check_mode", "delegate_to",
    "ignore_unreachable", "loop", "loop_control", "until", "retries", "delay", "tags",
}  # fmt: skip
CHOOSE = REPO / "playbooks" / "tasks" / "init_choose.yml"
INCLUDED = {"tasks/cluster_vms.yml", "tasks/init_choose.yml", "tasks/init_local_setting.yml", "init_ask.yml",
            "init_local_setting.yml"}  # fmt: skip


def test_every_task_is_a_read_but_the_settings_written():
    # A task reads, records for NEXT STEPS, or includes the cluster listing, a
    # choice, a question or local.yml's writer - the one thing that writes, a
    # setting at a time.
    files = (PLAYBOOK, CLUSTER_VMS, CHOOSE)
    tasks = [task for path in files for task in _tasks(yaml.safe_load(path.read_text()))]
    for task in tasks:
        if "block" in task:
            assert set(task) <= TASK_KEYS | {"block", "rescue", "always"}, task["name"]
            continue
        modules = set(task) - TASK_KEYS
        assert len(modules) == 1 and modules <= READ_ONLY, (task["name"], modules)
        module = modules.pop()
        if module == "ansible.builtin.uri":
            # A GET, and nothing saved: uri's dest writes a file.
            assert task[module].get("method", "GET") == "GET" and "dest" not in task[module], task["name"]
        if module == "ansible.builtin.command":
            assert task[module] == {"argv": ["hostname", "-s"]}, task["name"]
        if module in ("ansible.builtin.import_tasks", "ansible.builtin.include_tasks"):
            assert task[module] in INCLUDED, task["name"]
        if module == "ansible.builtin.include_role":
            entry = (task[module]["name"], task[module]["tasks_from"])
            assert set(task[module]) == {"name", "tasks_from"} and entry in ROLE_ENTRY_POINTS, task["name"]
            path = REPO / "roles" / entry[0] / "tasks" / f"{entry[1]}.yml"
            assert {next(iter(set(t) - TASK_KEYS)) for t in _tasks(yaml.safe_load(path.read_text()))} == {
                "ansible.builtin.set_fact"
            }, path


def test_a_setting_is_written_only_at_a_terminal_or_as_given():
    # Each writer's condition, as it opens: a terminal (init_ask), or a terminal
    # or a value given; whatever follows is and-ed to it.
    writers = {
        task["name"]: " ".join(str(task["when"]).split())
        for path in (PLAYBOOK, CHOOSE)
        for task in _tasks(yaml.safe_load(path.read_text()))
        if str(task.get("ansible.builtin.include_tasks", "")).endswith("init_local_setting.yml")
    }
    assert sorted(writers) == [
        "Write proxmox_node into local.yml",
        "init_choose | Write into local.yml {{ init_choose_setting }}",
    ]
    assert writers["Write proxmox_node into local.yml"].startswith("(init_ask | bool) and ")
    assert writers["init_choose | Write into local.yml {{ init_choose_setting }}"].startswith(
        "(init_ask | bool or init_choose_given | length > 0) and "
    )


def test_the_playbook_has_no_duplicate_key():
    # A YAML merge that redefines a key loads with a warning on every just init.
    result = subprocess.run(
        [str(Path(sys.executable).with_name("ansible-playbook")), "--syntax-check", str(PLAYBOOK)],
        env=os.environ | {"ANSIBLE_DUPLICATE_YAML_DICT_KEY": "error"},
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_uri_call_passes_the_ca_and_hides_the_token():
    for task in _tasks(yaml.safe_load(PLAYBOOK.read_text())):
        if "ansible.builtin.uri" in task:
            uri = task["ansible.builtin.uri"]
            assert uri["ca_path"] == "{{ proxmox_api_ca_path | default(omit, true) }}", task["name"]
            assert uri["headers"] == "{{ proxmox_api_headers }}" and task["no_log"] is True, task["name"]


def test_no_run_reaches_a_real_ssh():
    # Every run goes through _check, which gives the Proxmox host the local
    # connection or puts the refusing ssh first on PATH, and points the vault
    # and the key directory into the test's own.
    tree = ast.parse(Path(__file__).read_text())
    for function in ast.walk(tree):
        if isinstance(function, ast.FunctionDef):
            for call in ast.walk(function):
                if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "_run":
                    assert function.name == "_check", function.name
    assert SSH_STAND_IN.rstrip().endswith("exit 255") and "ssh-calls.log" in SSH_STAND_IN
