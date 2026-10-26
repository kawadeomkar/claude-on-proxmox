"""tools/proxmox-access.sh, the API access just init offers to set up: run against stand-in commands, never a real host.

The stand-ins (tests/molecule/fake_pve_cli.py) keep a host's roles, users, ACLs
and tokens in a file of the test's own and answer as pveum and pvesh do, and
the script runs under /bin/sh with them first on PATH - dash on a Linux runner,
as on the Proxmox host.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from harness import REPO, _pve_cli, _pve_cli_calls, _pve_cli_state

SCRIPT = REPO / "tools" / "proxmox-access.sh"
SECRET = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CHECKED = yaml.safe_load((REPO / "playbooks" / "init_check.yml").read_text())[0]["vars"]["init_token_privileges"]
ROLE = [*CHECKED["vms"], "VM.GuestAgent.Audit", *CHECKED["storage"], *CHECKED["bridge"]]
PLAN = [
    f"pveum role add AnsibleVM --privs {','.join(ROLE)}",
    "pveum user add ansible@pve --comment claude-on-proxmox",
    "pveum acl modify /vms --users ansible@pve --roles AnsibleVM",
    "pveum acl modify /storage/local-lvm --users ansible@pve --roles AnsibleVM",
    "pveum acl modify /sdn/zones/localnetwork/vmbr0 --users ansible@pve --roles AnsibleVM",
    "pveum user token add ansible@pve ansible --privsep 0 --comment claude-on-proxmox",
]
# The calls that change the host.
WRITES = (["pveum", "role"], ["pveum", "user", "add"], ["pveum", "acl"], ["pveum", "user", "token"])


def _access(tmp_path: Path, *args: str, state: dict | None = None, path: str | None = None):
    """The script with args, the stand-ins in tmp_path/pve - made with state on the first call - first on PATH."""
    host = tmp_path / "pve"
    if not (host / "pve-state.json").exists():
        _pve_cli(host, {"next_secret": SECRET} | (state or {}))
    return subprocess.run(
        ["/bin/sh", str(SCRIPT), *args],
        env={"PATH": path or f"{host}{os.pathsep}/usr/bin{os.pathsep}/bin", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _writes(tmp_path: Path) -> list[str]:
    return [
        " ".join(call)
        for call in _pve_cli_calls(tmp_path / "pve")
        if any(call[: len(write)] == write for write in WRITES) and call[1:3] != ["user", "permissions"]
    ]


def _acls(tmp_path: Path) -> list[tuple]:
    return sorted((a["path"], a["ugid"], a["roleid"], a["propagate"]) for a in _pve_cli_state(tmp_path / "pve")["acls"])


# --------------------------------------------------------------- a fresh host ---
def test_a_fresh_host_gets_the_role_user_grants_and_token_and_the_secret_alone_on_stdout(tmp_path: Path) -> None:
    result = _access(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == SECRET + "\n"
    assert SECRET not in result.stderr
    state = _pve_cli_state(tmp_path / "pve")
    assert state["roles"]["AnsibleVM"] == ROLE
    assert state["users"]["ansible@pve"] == {"comment": "claude-on-proxmox"}
    assert _acls(tmp_path) == [
        ("/sdn/zones/localnetwork/vmbr0", "ansible@pve", "AnsibleVM", 1),
        ("/storage/local-lvm", "ansible@pve", "AnsibleVM", 1),
        ("/vms", "ansible@pve", "AnsibleVM", 1),
    ]
    assert state["tokens"]["ansible@pve!ansible"]["privsep"] == 0
    # Each command is said as it runs, and the token is made last.
    assert [line[2:] for line in result.stderr.splitlines() if line.startswith("+ ")] == PLAN
    assert _writes(tmp_path)[-1].startswith("pveum user token add")


def test_the_dry_run_prints_the_commands_and_changes_nothing(tmp_path: Path) -> None:
    result = _access(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == PLAN
    assert _writes(tmp_path) == []
    assert "AnsibleVM" not in _pve_cli_state(tmp_path / "pve")["roles"]


def test_a_second_run_changes_nothing_and_says_the_token_exists(tmp_path: Path) -> None:
    assert _access(tmp_path).returncode == 0
    before = len(_writes(tmp_path))
    for args in ((), ("--dry-run",)):
        result = _access(tmp_path, *args)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        assert "token ansible@pve!ansible exists" in result.stderr and "pveum user token delete" in result.stderr
        assert "Nothing to do: the access is set up." in result.stderr
    assert len(_writes(tmp_path)) == before


@pytest.mark.parametrize(("version", "agent"), [("8.2.4", "VM.Monitor"), ("9.2.13", "VM.GuestAgent.Audit")])
def test_the_guest_agents_privilege_is_the_versions(tmp_path: Path, version: str, agent: str) -> None:
    # The stand-in refuses a privilege its version does not have, as Proxmox does.
    result = _access(tmp_path, state={"version": version})
    assert result.returncode == 0, result.stderr
    role = _pve_cli_state(tmp_path / "pve")["roles"]["AnsibleVM"]
    assert agent in role and ({"VM.Monitor", "VM.GuestAgent.Audit"} - {agent}).isdisjoint(role)


def test_list_prints_the_storages_and_bridges_and_changes_nothing(tmp_path: Path) -> None:
    state = {
        "storages": [
            {"storage": "local-zfs", "type": "zfspool", "content": "images,rootdir"},
            {"storage": "local", "type": "dir", "content": "iso,backup"},
            {"storage": "tank", "type": "nfs", "content": "images"},
        ],
        "bridges": ["vmbr0", "vmbr1"],
    }
    result = _access(tmp_path, "--list", state=state)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["storage local-zfs", "storage tank", "bridge vmbr0", "bridge vmbr1"]
    assert _writes(tmp_path) == []


# ------------------------------------------------------- the storage and bridge ---
def test_a_storage_the_node_lacks_is_refused_naming_those_it_has(tmp_path: Path) -> None:
    storages = [{"storage": "local-zfs", "type": "zfspool", "content": "images"}]
    storages += [{"storage": "tank", "type": "nfs", "content": "images"}]
    result = _access(tmp_path, state={"storages": storages})
    assert result.returncode == 1
    assert "storage local-lvm is not on node pve for VM disks: give one of local-zfs, tank with --storage" in (
        result.stderr
    )
    assert result.stdout == "" and _writes(tmp_path) == []
    result = _access(tmp_path, "--storage", "tank")
    assert result.returncode == 0, result.stderr
    assert ("/storage/tank", "ansible@pve", "AnsibleVM", 1) in _acls(tmp_path)


def test_a_bridge_the_node_lacks_is_refused(tmp_path: Path) -> None:
    result = _access(tmp_path, state={"bridges": ["vmbr1"]})
    assert result.returncode == 1
    assert "bridge vmbr0 is not on node pve: give one of vmbr1 with --bridge" in result.stderr
    assert _writes(tmp_path) == []
    assert _access(tmp_path, "--bridge", "vmbr1").returncode == 0
    assert ("/sdn/zones/localnetwork/vmbr1", "ansible@pve", "AnsibleVM", 1) in _acls(tmp_path)


def test_a_node_with_no_storage_for_vm_disks_says_so(tmp_path: Path) -> None:
    result = _access(tmp_path, state={"storages": [{"storage": "local", "type": "dir", "content": "iso"}]})
    assert result.returncode == 1 and "node pve has no storage for VM disks enabled" in result.stderr


# ------------------------------------------------------------ what exists ---
def test_a_role_that_exists_without_a_privilege_is_named_and_left(tmp_path: Path) -> None:
    lacking = [p for p in ROLE if p != "VM.Clone"]
    result = _access(tmp_path, state={"roles": {"AnsibleVM": lacking}})
    assert result.returncode == 0, result.stderr
    assert "role AnsibleVM exists without VM.Clone, and is left as it is" in result.stderr
    assert "pveum role modify AnsibleVM --append 1 --privs VM.Clone" in result.stderr
    assert _pve_cli_state(tmp_path / "pve")["roles"]["AnsibleVM"] == lacking
    assert not [write for write in _writes(tmp_path) if write.startswith("pveum role")]


def test_a_user_that_holds_the_privileges_another_way_gets_no_grant(tmp_path: Path) -> None:
    # A host whose owner granted the user a role of their own, on / - the
    # homeserver's storage grant, say: nothing is added beside it.
    state = {
        "roles": {"Owners": ROLE},
        "users": {"root@pam": {}, "ansible@pve": {}},
        "acls": [{"path": "/", "ugid": "ansible@pve", "roleid": "Owners", "propagate": 1, "type": "user"}],
    }
    result = _access(tmp_path, state=state)
    assert result.returncode == 0, result.stderr
    assert not [write for write in _writes(tmp_path) if write.startswith("pveum acl")]
    assert "ansible@pve holds what it needs on /storage/local-lvm" in result.stderr
    assert result.stdout == SECRET + "\n"


def test_a_grant_that_does_not_propagate_from_above_is_no_grant(tmp_path: Path) -> None:
    state = {
        "roles": {"AnsibleVM": ROLE},
        "users": {"root@pam": {}, "ansible@pve": {}},
        "acls": [{"path": "/", "ugid": "ansible@pve", "roleid": "AnsibleVM", "propagate": 0, "type": "user"}],
    }
    result = _access(tmp_path, state=state)
    assert result.returncode == 0, result.stderr
    assert len([write for write in _writes(tmp_path) if write.startswith("pveum acl")]) == 3


def test_a_grant_on_vms_that_does_not_propagate_is_named_and_left(tmp_path: Path) -> None:
    # Proxmox checks each VM's own path, /vms/<id>: what is held on /vms alone is not enough, and the
    # grant there is the person's to change.
    state = {
        "roles": {"AnsibleVM": ROLE},
        "users": {"root@pam": {}, "ansible@pve": {}},
        "acls": [{"path": "/vms", "ugid": "ansible@pve", "roleid": "AnsibleVM", "propagate": 0, "type": "user"}],
    }
    result = _access(tmp_path, state=state)
    assert result.returncode == 0, result.stderr
    assert "ansible@pve holds VM.Allocate," in result.stderr and "on /vms but not on the VMs under it" in result.stderr
    assert "pveum acl modify /vms --users ansible@pve --roles AnsibleVM --propagate 1" in result.stderr
    grants = [write for write in _writes(tmp_path) if write.startswith("pveum acl")]
    assert [grant.split()[3] for grant in grants] == ["/storage/local-lvm", "/sdn/zones/localnetwork/vmbr0"]
    assert ("/vms", "ansible@pve", "AnsibleVM", 0) in _acls(tmp_path)


def test_a_privilege_the_role_lacks_is_never_said_to_be_held(tmp_path: Path) -> None:
    # A role made without SDN.Use: granting it on the bridge would give nothing, and the user lacks it there.
    state = {"roles": {"AnsibleVM": [p for p in ROLE if p != "SDN.Use"]}}
    result = _access(tmp_path, state=state)
    assert result.returncode == 0, result.stderr
    assert "role AnsibleVM exists without SDN.Use" in result.stderr
    assert "lacks SDN.Use on /sdn/zones/localnetwork/vmbr0, which role AnsibleVM does not give either" in (
        result.stderr
    )
    assert "holds what it needs on /sdn" not in result.stderr
    assert not [write for write in _writes(tmp_path) if write.startswith("pveum acl modify /sdn")]


def test_a_user_that_holds_privileges_on_root_is_warned_of_before_its_token(tmp_path: Path) -> None:
    state = {
        "roles": {"Everything": [*ROLE, "Sys.Modify", "Permissions.Modify"]},
        "users": {"root@pam": {}, "admin@pve": {}},
        "acls": [{"path": "/", "ugid": "admin@pve", "roleid": "Everything", "propagate": 1, "type": "user"}],
    }
    for args in (("--dry-run",), ()):
        result = _access(tmp_path, "--user", "admin@pve", *args, state=state)
        assert result.returncode == 0, result.stderr
        assert "WARNING: admin@pve holds" in result.stderr and "Permissions.Modify" in result.stderr
    # A user this run makes holds nothing on /.
    assert "WARNING" not in _access(tmp_path / "fresh").stderr


def test_a_token_with_privilege_separation_is_named_and_left(tmp_path: Path) -> None:
    state = {"users": {"root@pam": {}, "ansible@pve": {}}, "tokens": {"ansible@pve!ansible": {"privsep": 1}}}
    result = _access(tmp_path, state=state)
    assert result.returncode == 0, result.stderr
    assert "It was made with privilege separation" in result.stderr
    assert result.stdout == ""
    assert _pve_cli_state(tmp_path / "pve")["tokens"]["ansible@pve!ansible"] == {"privsep": 1}


def test_another_user_and_token_are_used_as_given(tmp_path: Path) -> None:
    result = _access(tmp_path, "--user", "deploy@pve", "--token", "claude")
    assert result.returncode == 0, result.stderr
    assert "deploy@pve!claude" in _pve_cli_state(tmp_path / "pve")["tokens"]
    assert ("/vms", "deploy@pve", "AnsibleVM", 1) in _acls(tmp_path)


# ------------------------------------------------------------- refusals ---
def test_it_runs_only_as_root(tmp_path: Path) -> None:
    result = _access(tmp_path, state={"uid": 1000})
    assert result.returncode == 1 and "run it as root, on the Proxmox host" in result.stderr
    assert [call[0] for call in _pve_cli_calls(tmp_path / "pve")] == ["id"]


def test_it_runs_only_where_pveum_is(tmp_path: Path) -> None:
    host = _pve_cli(tmp_path / "pve")
    alone = tmp_path / "id-alone"
    alone.mkdir()
    shutil.copy(host / "id", alone / "id")
    shutil.copy(host / "pve-state.json", alone / "pve-state.json")
    result = _access(tmp_path, path=f"{alone}{os.pathsep}/usr/bin{os.pathsep}/bin")
    assert result.returncode == 1 and "there is no pveum here" in result.stderr


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (["--storage", "local lvm"], "--storage may contain only"),
        (["--storage", "x;reboot"], "--storage may contain only"),
        (["--bridge", "$(reboot)"], "--bridge may contain only"),
        (["--bridge", ""], "--bridge may contain only"),
        (["--user", "ansible"], "--user is user@realm"),
        (["--user", "a@b@c"], "--user is user@realm"),
        (["--user", "@pve"], "--user is user@realm"),
        (["--user", "root@pam"], "never root@pam"),
        (["--token", "1st"], "--token starts with a letter"),
        (["--token"], "--token takes a value"),
        (["--force"], "no option --force"),
    ],
)
def test_a_value_that_is_not_one_plain_word_is_refused_before_anything_runs(
    tmp_path: Path, args: list[str], said: str
) -> None:
    result = _access(tmp_path, *args)
    assert result.returncode == 1 and said in result.stderr
    assert _pve_cli_calls(tmp_path / "pve") == []


def test_a_refused_write_stops_it_before_the_token(tmp_path: Path) -> None:
    result = _access(tmp_path, state={"fail": "acl modify /storage"})
    assert result.returncode != 0
    assert result.stdout == ""
    assert _pve_cli_state(tmp_path / "pve")["tokens"] == {}


# --------------------------------------------------------------- static ---
def test_the_privileges_are_the_ones_just_init_checks() -> None:
    text = SCRIPT.read_text()
    granted = {
        name: re.search(rf"^{name}_privileges='([^']+)'$", text, re.MULTILINE).group(1).split()
        for name in ("vms", "storage", "bridge")
    }
    assert granted == CHECKED
    assert "agent=VM.GuestAgent.Audit; else agent=VM.Monitor" in text


def test_the_commands_the_docs_show_are_the_ones_it_runs(tmp_path: Path) -> None:
    page = (REPO / "docs" / "getting-started" / "proxmox-access.md").read_text()
    shown = re.search(r"it runs:\n\n```text\n(.*?)```", page, re.S).group(1).splitlines()
    assert shown == PLAN
    assert _access(tmp_path, "--dry-run").stdout.splitlines() == shown


def test_its_stdout_is_data_and_its_talk_is_stderr() -> None:
    # What a person reads goes through say or die, to stderr. What reaches
    # stdout, which just init reads, is a printf not piped on: the dry run's
    # commands (run's), --list's lines and the secret.
    text = SCRIPT.read_text()
    to_stdout = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"\b(?:printf|echo)\b", line) and "|" not in line and ">&2" not in line
    ]
    assert to_stdout == [
        "for name in $storages; do printf 'storage %s\\n' \"$name\"; done",
        "for name in $bridges; do printf 'bridge %s\\n' \"$name\"; done",
        "printf '%s\\n' \"$*\"",
        "printf '%s\\n' \"$secret\"",
    ], to_stdout
