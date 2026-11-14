"""The checkout's hosts: the resolver every recipe asks for its run's host (tools/check host, lib/claude_vm/hosts/), and
the rule in the inventory defaults that sends each host its own token.

A host is an inventory source laid over the shared one: the first stays in inventory/, each other is a folder of its
own under hosts/, and a run against it gets ANSIBLE_INVENTORY=inventory/controller.yml,hosts/<name>. Everything here
runs against made-up layouts in tmp_path, with fake secrets only:

- the resolver: which host a run gets, and every refusal - a host not set up, a bad name, hosts/current naming no
  host, an exported ANSIBLE_INVENTORY, a host without a setting each host sets for itself, a setting of one network
  the shared files set and the host's do not, a token under the wrong name in either vault;
- the token rule, through ansible-playbook itself: the right secret per host however the sources are spelt, and a
  host whose vault has none failing undefined, before anything - never sent the first host's.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from harness import ANSIBLE_PLAYBOOK, REPO, _env

HOST_TOOL = REPO / "tools" / "host"
FIRST_SECRET = "first-host-fake-secret"
NUC_SECRET = "nuc-fake-secret"
PASSWORD = "fake-vault-password"
# Every setting a Proxmox host writes for itself (lib/claude_vm/hosts/kinds.py), as just init --host writes them.
ALWAYS = {
    "proxmox_node": "nuc",
    "proxmox_storage": "local-lvm",
    "proxmox_bridge": "vmbr0",
    "proxmox_api_user": "ansible@pve",
    "proxmox_api_token_id": "ansible",
    "proxmox_api_port": 8006,
    "proxmox_validate_certs": False,
    "proxmox_template_vmid": 9000,
}


def _hosts_yml(name: str, address: str = "192.0.2.20", extra: dict | None = None) -> str:
    hosts = {name: {"ansible_host": address, "ansible_user": "root"}, **(extra or {})}
    return yaml.safe_dump({"all": {"children": {"proxmox": {"hosts": hosts}}}})


def _encrypt(path: Path, values: dict, root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(values))
    subprocess.run(
        [
            str(ANSIBLE_PLAYBOOK.with_name("ansible-vault")),
            "encrypt",
            "--vault-password-file",
            str(root / ".vault_pass"),
            str(path),
        ],
        check=True,
        capture_output=True,
        env=_env(root),
    )


def _checkout(root: Path, *, first: bool = True, local: dict | None = None) -> Path:
    """A checkout's files as a resolver reads them: the shared inventory, the project's own defaults, a vault and its
    password; hosts/ empty. The first host is pve, as hosts.yml.example names it."""
    inventory = root / "inventory"
    (inventory / "group_vars" / "all").mkdir(parents=True)
    shutil.copy(REPO / "inventory" / "controller.yml", inventory / "controller.yml")
    (inventory / "group_vars" / "all" / "defaults.yml").symlink_to(
        REPO / "inventory" / "group_vars" / "all" / "defaults.yml"
    )
    if first:
        (inventory / "hosts.yml").write_text(_hosts_yml("pve", "192.0.2.10"))
    # The managed SSH config the resolver reads a VM's host from: the test's own, never the developer's.
    alias_file = {"claude_ssh_config_file": str(root / ".ssh" / "agent-warehouse.conf")}
    (inventory / "group_vars" / "all" / "local.yml").write_text(
        yaml.safe_dump(local or {"proxmox_storage": "shared", **alias_file})
    )
    (root / ".vault_pass").write_text(PASSWORD + "\n")
    (root / ".vault_pass").chmod(0o600)
    _encrypt(inventory / "group_vars" / "all" / "vault.yml", {"vault_proxmox_api_token_secret": FIRST_SECRET}, root)
    (root / "hosts").mkdir()
    return root


def _host(root: Path, name: str, *, local: dict | None = None, vault: dict | None = None, hosts_yml: str = "") -> Path:
    """hosts/<name>, set up as just init --host leaves it, unless told otherwise."""
    folder = root / "hosts" / name
    (folder / "group_vars" / "all").mkdir(parents=True)
    (folder / "hosts.yml").write_text(hosts_yml or _hosts_yml(name))
    (folder / "group_vars" / "all" / "local.yml").write_text(yaml.safe_dump(ALWAYS if local is None else local))
    _encrypt(
        folder / "group_vars" / "all" / "vault.yml",
        {"vault_host_proxmox_api_token_secret": NUC_SECRET} if vault is None else vault,
        root,
    )
    return folder


def _tool(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    """tools/host, from the checkout at root, as tools/check host runs it."""
    return subprocess.run(
        [sys.executable, "-I", str(HOST_TOOL), *args, f"--checkout={root}", f"--hosts-dir={root / 'hosts'}"],
        cwd=root,
        env={"PATH": os.environ["PATH"], "HOME": str(root), **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


def _resolve(root: Path, *args: str, env: dict | None = None) -> dict[str, str]:
    """What env prints for a recipe to eval, as the values the shell would set."""
    said = _tool(root, "env", f"--vault-pass={root / '.vault_pass'}", *args, env=env)
    assert said.returncode == 0, said.stderr
    names = "host hosts_many settings_files vault_files host_sources host_extra"
    script = (
        f'{said.stdout}\nfor v in {names}; do eval "printf \'%s=%s\\\\n\' \\"\\$v\\" \\"\\${{$v}}\\""; done\n'
        'printf "inventory=%s\\n" "${ANSIBLE_INVENTORY-unset}"'
    )
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True, env={"PATH": "/usr/bin:/bin"}
    )
    return dict(line.split("=", 1) for line in out.stdout.splitlines())


def _refused(root: Path, *args: str, env: dict | None = None) -> str:
    said = _tool(root, "env", f"--vault-pass={root / '.vault_pass'}", *args, env=env)
    assert said.returncode == 1, said.stdout + said.stderr
    assert said.stdout == "", said.stdout
    return said.stderr


# ---------------------------------------------------------------------------------------- which host ----


def test_one_host_runs_as_it_always_did(tmp_path: Path) -> None:
    # Nothing to choose, so nothing is read: the name is not needed.
    root = _checkout(tmp_path)
    got = _resolve(root)
    assert got["host"] == "" and got["hosts_many"] == "" and got["host_sources"] == "" and got["host_extra"] == ""
    assert got["settings_files"] == "inventory/hosts.yml inventory/group_vars/all/local.yml"
    assert got["vault_files"] == "inventory/group_vars/all/vault.yml"
    assert got["inventory"] == "unset"


def test_a_checkout_before_just_init_has_no_host_and_runs_as_it_always_did(tmp_path: Path) -> None:
    root = _checkout(tmp_path, first=False)
    assert _resolve(root)["host"] == ""
    # Asked for by name, the first host is read.
    assert _resolve(_checkout(tmp_path / "named"), "--host=pve")["host"] == "pve"


def test_the_files_given_for_the_first_host_are_its_own(tmp_path: Path) -> None:
    # VAULT_FILE and SETTINGS_FILES on the command line, as the unit tests give them.
    root = _checkout(tmp_path)
    got = _resolve(root, "--vault-file=/elsewhere/vault.yml", "--settings-files=/elsewhere/a.yml /elsewhere/b.yml")
    assert got["vault_files"] == "/elsewhere/vault.yml" and got["settings_files"] == "/elsewhere/a.yml /elsewhere/b.yml"


def test_a_host_in_hosts_is_an_inventory_source_over_the_shared_one(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    got = _resolve(root, "--host=nuc")
    assert got["host"] == "nuc" and got["hosts_many"] == "1"
    assert got["host_sources"] == "inventory/controller.yml,hosts/nuc" == got["inventory"]
    assert got["vault_files"] == "inventory/group_vars/all/vault.yml hosts/nuc/group_vars/all/vault.yml"
    assert got["settings_files"] == (
        "inventory/group_vars/all/local.yml hosts/nuc/hosts.yml hosts/nuc/group_vars/all/local.yml"
    )
    # The other hosts, which the guards keep out of its VMs' names and aliases.
    assert json.loads(got["host_extra"]) == {"claude_hosts_other": [{"name": "pve", "address": "192.0.2.10"}]}


def test_the_current_host_is_hosts_current_and_the_first_without_it(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    got = _resolve(root)
    assert got["host"] == "pve" and got["inventory"] == "unset"
    assert json.loads(got["host_extra"]) == {"claude_hosts_other": [{"name": "nuc", "address": "192.0.2.20"}]}
    (root / "hosts" / "current").write_text("nuc\n")
    assert _resolve(root)["host"] == "nuc"
    # --host outranks it, and names the first host as well as any other.
    assert _resolve(root, "--host=pve")["host"] == "pve"


def test_a_nested_recipe_runs_against_its_parents_host_and_takes_its_inventory_for_its_own(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    sources = "inventory/controller.yml,hosts/nuc"
    assert _resolve(root, "--nested=nuc", env={"ANSIBLE_INVENTORY": sources})["host"] == "nuc"
    said = _refused(root, "--nested=nuc", "--host=pve")
    assert "--host pve in a run against nuc: one host per run" in said


def test_hosts_current_naming_no_host_is_refused(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    (root / "hosts" / "current").write_text("lab\n")
    assert "hosts/current names lab, which is no host of this checkout's (pve, nuc)" in _refused(root)


@pytest.mark.parametrize("name", ["Nuc", "-nuc", "a" * 32, "nuc.lan"])
def test_a_name_that_is_no_hosts_is_refused_before_anything_is_read(tmp_path: Path, name: str) -> None:
    root = _checkout(tmp_path)
    assert "a host's name is lowercase letters, digits and -" in _refused(root, f"--host={name}")


def test_a_host_not_set_up_is_refused_naming_just_init(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    (root / "hosts" / "lab").mkdir()
    assert "--host names lab, which cannot be used: not set up: just init --host lab" in _refused(root, "--host=lab")


@pytest.mark.parametrize(
    ("hosts_yml", "said"),
    [
        (_hosts_yml("other"), "must define one host, lab, in the proxmox group, and no other (it defines other)"),
        # localhost there would make the host's folder its own, and the token rule would send it the first host's.
        (_hosts_yml("lab", extra={"localhost": {}}), "(it defines lab, localhost)"),
        (yaml.safe_dump({"all": {"children": {"proxmox": {"hosts": {"lab": {}}}}}}), "gives lab no ansible_host"),
    ],
)
def test_a_hosts_yml_that_is_not_one_host_of_the_folders_name_is_refused(
    tmp_path: Path, hosts_yml: str, said: str
) -> None:
    root = _checkout(tmp_path)
    _host(root, "lab", hosts_yml=hosts_yml)
    assert said in _refused(root, "--host=lab")


@pytest.mark.parametrize("name", ["current", "machines", "localhost", "configure", "pve"])
def test_a_folder_of_a_reserved_name_or_the_first_hosts_is_no_host(tmp_path: Path, name: str) -> None:
    root = _checkout(tmp_path)
    (root / "hosts" / name).mkdir()
    listed = json.loads(_tool(root, "list", "--json").stdout)
    row = next(row for row in listed["hosts"] if row["folder"] == f"hosts/{name}")
    assert row["problem"], row


def test_a_host_without_a_setting_each_host_sets_for_itself_is_refused(tmp_path: Path) -> None:
    # Inherited, the first host's node, storage or token user would be taken for the NUC's.
    root = _checkout(tmp_path)
    _host(root, "nuc", local={k: v for k, v in ALWAYS.items() if k not in ("proxmox_node", "proxmox_api_user")})
    said = _refused(root, "--host=nuc")
    assert "does not set proxmox_node, proxmox_api_user, which each host sets for itself" in said
    assert "just init --host nuc writes them" in said


def test_a_setting_of_one_network_the_shared_files_set_is_never_inherited(tmp_path: Path) -> None:
    # A static address on the homelab's network would be given to the NUC's VMs on another.
    root = _checkout(tmp_path, local={"vm_ipconfig": "ip=192.0.2.60/24,gw=192.0.2.1", "claude_ssh_host_domain": "lan"})
    _host(root, "nuc")
    said = _refused(root, "--host=nuc")
    assert "sets vm_ipconfig, claude_ssh_host_domain, which describe one network or one host" in said
    _host(root, "lab", local={**ALWAYS, "vm_ipconfig": "ip=dhcp", "claude_ssh_host_domain": ""})
    assert _resolve(root, "--host=lab")["host"] == "lab"


def test_an_exported_inventory_is_left_alone_for_the_first_host_and_refused_for_another(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    left = _tool(root, "env", env={"ANSIBLE_INVENTORY": "/somewhere/else"})
    assert left.returncode == 0 and "ANSIBLE_INVENTORY" not in left.stdout
    said = _refused(root, "--host=nuc", env={"ANSIBLE_INVENTORY": "/somewhere/else"})
    assert "ANSIBLE_INVENTORY is exported, and this run's host is nuc (--host)" in said


def test_the_first_hosts_files_given_beside_another_host_are_refused(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    said = _refused(root, "--host=nuc", "--vault-file=/elsewhere/vault.yml")
    assert "VAULT_FILE and SETTINGS_FILES name the first host's files" in said


@pytest.mark.parametrize(
    ("shared", "own", "said"),
    [
        # The host's own name in the shared vault: a host whose vault lacked it would be sent this one.
        (
            {"vault_proxmox_api_token_secret": FIRST_SECRET, "vault_host_proxmox_api_token_secret": FIRST_SECRET},
            {"vault_host_proxmox_api_token_secret": NUC_SECRET},
            "inventory/group_vars/all/vault.yml sets vault_host_proxmox_api_token_secret, which is a host's own",
        ),
        # The first host's name in a host's vault: the rule never reads it there.
        (
            {"vault_proxmox_api_token_secret": FIRST_SECRET},
            {"vault_proxmox_api_token_secret": NUC_SECRET},
            "hosts/nuc/group_vars/all/vault.yml sets vault_proxmox_api_token_secret, the first host's name",
        ),
        # None at all: said before the run, as the rule would fail it.
        (
            {"vault_proxmox_api_token_secret": FIRST_SECRET},
            {"vault_vm_password": "fake"},
            "does not set vault_host_proxmox_api_token_secret, nuc's secret",
        ),
    ],
    ids=["host-name-in-shared", "shared-name-in-host", "none-in-host"],
)
def test_a_token_under_the_other_vaults_name_is_refused(tmp_path: Path, shared: dict, own: dict, said: str) -> None:
    root = _checkout(tmp_path)
    _encrypt(root / "inventory" / "group_vars" / "all" / "vault.yml", shared, root)
    _host(root, "nuc", vault=own)
    refused = _refused(root, "--host=nuc")
    assert said in refused
    assert FIRST_SECRET not in refused and NUC_SECRET not in refused


def test_a_vault_check_that_passed_is_kept_until_a_vault_changes(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    assert _resolve(root, "--host=nuc")["host"] == "nuc"
    checked = json.loads((root / "hosts" / ".checked.json").read_text())
    assert set(checked) == {"nuc"} and FIRST_SECRET not in json.dumps(checked)
    # A vault rewritten with the wrong name: its size and time change, and it is read again.
    _encrypt(root / "hosts" / "nuc" / "group_vars" / "all" / "vault.yml", {"vault_proxmox_api_token_secret": "x"}, root)
    assert "the first host's name" in _refused(root, "--host=nuc")


# ------------------------------------------------------------------------- listing, switching, removing ----


def test_the_list_says_each_host_from_its_files_alone(tmp_path: Path) -> None:
    root = _checkout(tmp_path, local={"proxmox_node": "pve1"})
    _host(root, "nuc")
    (root / "hosts" / "lab").mkdir()
    (root / "hosts" / "current").write_text("nuc\n")
    listed = json.loads(_tool(root, "list", "--json").stdout)
    assert listed["current"] == "nuc"
    rows = {row["name"]: row for row in listed["hosts"]}
    assert rows["pve"]["settings"] == {"proxmox_node": "pve1"} and rows["pve"]["first"]
    assert rows["nuc"]["current"] and rows["nuc"]["address"] == "192.0.2.20" and rows["nuc"]["problem"] == ""
    assert rows["lab"]["problem"] == "not set up: just init --host lab"
    table = _tool(root, "list").stdout
    assert "▸ nuc" in table and "the current host" in table and "not set up: just init --host lab" in table


def test_switching_writes_hosts_current_whole_and_refuses_a_host_not_set_up(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    (root / "hosts" / "lab").mkdir()
    said = _tool(root, "switch", "nuc")
    assert said.returncode == 0 and (root / "hosts" / "current").read_text() == "nuc\n"
    refused = _tool(root, "switch", "lab")
    assert refused.returncode == 1 and "not set up: just init --host lab" in refused.stderr
    assert (root / "hosts" / "current").read_text() == "nuc\n"
    assert [p.name for p in (root / "hosts").iterdir() if p.name.startswith(".current")] == []


def test_removing_a_host_never_removes_the_current_or_the_first(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    _host(root, "lab")
    (root / "hosts" / "current").write_text("nuc\n")
    assert "is the current host" in _tool(root, "remove", "nuc").stderr
    assert "is the first host" in _tool(root, "remove", "pve").stderr
    said = _tool(root, "remove", "lab")
    assert said.returncode == 0 and "Its VMs are still there" in said.stdout
    assert not (root / "hosts" / "lab").exists() and (root / "hosts" / "nuc").exists()


# ---------------------------------------------------------------------------------------- the token rule ----


def _secrets(root: Path, sources: str | None, *args: str) -> subprocess.CompletedProcess[str]:
    """The secret a play at localhost and one at the run's Proxmox host would send, through ansible-playbook with the
    project's defaults.yml, the sources as given (None: ansible.cfg's inventory, the first host's)."""
    play = root / "secrets.yml"
    play.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "gather_facts": False,
                    "tasks": [{"ansible.builtin.debug": {"msg": "localhost={{ proxmox_api_token_secret }}"}}],
                },
                {
                    "hosts": "proxmox",
                    "gather_facts": False,
                    "tasks": [
                        {"ansible.builtin.debug": {"msg": "{{ inventory_hostname }}={{ proxmox_api_token_secret }}"}}
                    ],
                },
            ]
        )
    )
    env = _env(root, f"inventory = {root / 'inventory'}\n", ANSIBLE_NOCOLOR="1")
    if sources is not None:
        env["ANSIBLE_INVENTORY"] = sources
    return subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "--vault-password-file", str(root / ".vault_pass"), *args, str(play)],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "sources",
    [
        "inventory/controller.yml,hosts/nuc",
        "{root}/inventory/controller.yml,{root}/hosts/nuc/",
        # The host's hosts.yml as a file: the first draft of the rule matched the sources' spelling, and took this
        # for the first host.
        "inventory/controller.yml,hosts/nuc/hosts.yml",
    ],
)
def test_each_host_is_sent_its_own_secret_however_its_sources_are_spelt(tmp_path: Path, sources: str) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    first = _secrets(root, None)
    assert first.returncode == 0, first.stdout + first.stderr
    assert f"localhost={FIRST_SECRET}" in first.stdout and f"pve={FIRST_SECRET}" in first.stdout
    overlay = _secrets(root, sources.format(root=root))
    assert overlay.returncode == 0, overlay.stdout + overlay.stderr
    assert f"localhost={NUC_SECRET}" in overlay.stdout and f"nuc={NUC_SECRET}" in overlay.stdout
    assert FIRST_SECRET not in overlay.stdout


def test_a_host_given_by_i_is_sent_its_own_secret(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    said = _secrets(root, None, "-i", "inventory/controller.yml", "-i", "hosts/nuc")
    assert said.returncode == 0 and f"nuc={NUC_SECRET}" in said.stdout and FIRST_SECRET not in said.stdout


def test_a_host_whose_vault_has_no_secret_fails_undefined_and_is_never_sent_the_first_hosts(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "bare", vault={"vault_vm_password": "fake"})
    said = _secrets(root, "inventory/controller.yml,hosts/bare")
    assert said.returncode != 0
    assert "vault_host_proxmox_api_token_secret" in said.stdout + said.stderr
    assert FIRST_SECRET not in said.stdout + said.stderr


def test_a_setting_in_a_git_ignored_file_cannot_switch_the_rule_off(tmp_path: Path) -> None:
    # The rule has no variable of its own to set: a host's local.yml naming one changes nothing.
    root = _checkout(tmp_path)
    _host(
        root,
        "bare",
        local={**ALWAYS, "claude_host_first": True, "claude_host_overlay": False},
        vault={"vault_vm_password": "fake"},
    )
    said = _secrets(root, "inventory/controller.yml,hosts/bare")
    assert said.returncode != 0 and FIRST_SECRET not in said.stdout + said.stderr


def test_the_rule_is_one_expression_with_no_variable_of_its_own() -> None:
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    rule = defaults["proxmox_api_token_secret"]
    assert "inventory_dir" in rule and "vault_host_proxmox_api_token_secret" in rule
    # Every name it reads is Ansible's own, or one of the two vault names: none a setting could set.
    import re

    names = set(re.findall(r"\b([a-z_][a-z0-9_]*)\b", rule)) - {"if", "not", "or", "else", "default", "trim", "none"}
    assert names == {
        "vault_proxmox_api_token_secret",
        "vault_host_proxmox_api_token_secret",
        "groups",
        "proxmox",
        "hostvars",
        "inventory_dir",
        "localhost",
    }, names


# ---------------------------------------------------------------------------------------- the recipes ----
# The real just, on a copy of the justfile in a checkout of its own whose shared files are made up (the tools, lib and
# playbooks linked from this one), with test_justfile.py's stand-ins for every tool but Python: never this checkout's
# inventory, vault or hosts/.


def _checkout_copy(tmp_path: Path) -> Path:
    root = _checkout(tmp_path / "checkout")
    shutil.copy(REPO / "justfile", root / "justfile")
    for name in ("tools", "lib", "playbooks", "deploy.yml", "requirements.yml", "filter_plugins", "roles"):
        (root / name).symlink_to(REPO / name)
    # Vaults that only look encrypted: the recipes check that much, and the resolver cannot open one, so leaves the
    # names to Ansible.
    for vault in (root / "inventory" / "group_vars" / "all" / "vault.yml",):
        vault.write_text("$ANSIBLE_VAULT;1.1;AES256\n")
    return root


def _host_copy(root: Path, name: str) -> None:
    _host(root, name)
    (root / "hosts" / name / "group_vars" / "all" / "vault.yml").write_text("$ANSIBLE_VAULT;1.1;AES256\n")


def _just_here(root: Path, *args: str, settings: tuple = ()) -> subprocess.CompletedProcess[str]:
    from test_justfile import _just, _python, _stand_in

    bin_dir = root.parent / "bin"
    _stand_in(bin_dir, "ansible-galaxy")
    _stand_in(
        bin_dir,
        "ansible-playbook",
        f'printf "%s\\n" "${{ANSIBLE_INVENTORY-unset}}" >> "{root.parent / "inventory.txt"}"',
    )
    _python(root.parent)
    given = [
        f"BIN={bin_dir}",
        f"LOG_DIR={root.parent / 'logs'}",
        f"STATUS_DIR={root.parent / 'status'}",
        f"LIVE_DIR={root.parent / 'live'}",
        *settings,
    ]
    return _just(*args, settings=given, cwd=root)


def test_a_run_against_a_host_in_hosts_gets_its_inventory_and_the_others_for_its_guards(tmp_path: Path) -> None:
    from test_justfile import _runs

    root = _checkout_copy(tmp_path)
    _host_copy(root, "nuc")
    lock = tmp_path / "run.lock"
    result = _just_here(root, "deploy", "--host", "nuc", "alpha", settings=(f"RUN_LOCK={lock}",))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "inventory.txt").read_text() == "inventory/controller.yml,hosts/nuc\n"
    (run,) = _runs(tmp_path, "ansible-playbook")
    others = run[run.index('{"claude_hosts_other": [{"name": "pve", "address": "192.0.2.10"}]}') - 1]
    assert others == "-e"
    # The lock says which host the run is against, with more than one.
    assert lock.read_text().splitlines()[2] == "deploy alpha --host nuc"


def test_a_host_not_set_up_is_refused_before_any_tool_runs(tmp_path: Path) -> None:
    from test_justfile import _runs

    root = _checkout_copy(tmp_path)
    (root / "hosts" / "lab").mkdir()
    result = _just_here(root, "login", "--host", "lab")
    assert result.returncode != 0 and "not set up: just init --host lab" in result.stderr
    assert _runs(tmp_path, "ansible-playbook") == [] and _runs(tmp_path, "ansible-galaxy") == []


def test_with_one_host_a_run_is_as_it_was(tmp_path: Path) -> None:
    from test_justfile import _runs

    root = _checkout_copy(tmp_path)
    lock = tmp_path / "run.lock"
    result = _just_here(root, "destroy", "alpha", settings=(f"RUN_LOCK={lock}",))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "inventory.txt").read_text() == "unset\n"
    (run,) = _runs(tmp_path, "ansible-playbook")
    assert not any("claude_hosts_other" in arg for arg in run)
    assert lock.read_text().splitlines()[2] == "destroy alpha"


def test_status_reads_a_host_through_its_sources_and_both_vaults_and_keeps_its_settings_apart(tmp_path: Path) -> None:
    from test_justfile import _runs

    root = _checkout_copy(tmp_path)
    _host_copy(root, "nuc")
    # --host, one host's run: the others' are not read.
    result = _just_here(root, "status", "--host", "nuc")
    assert result.returncode == 0, result.stdout + result.stderr
    (run,) = _runs(tmp_path, "python")
    assert run[run.index("--cache") + 1] == str(tmp_path / "status" / "nuc")
    assert [run[at + 1] for at, arg in enumerate(run) if arg == "--vault-file"] == [
        "inventory/group_vars/all/vault.yml",
        "hosts/nuc/group_vars/all/vault.yml",
    ]
    assert [run[at + 1] for at, arg in enumerate(run) if arg == "-i"] == ["inventory/controller.yml", "hosts/nuc"]


def test_just_host_lists_switches_under_the_lock_and_refuses_a_removal_without_a_terminal(tmp_path: Path) -> None:
    root = _checkout_copy(tmp_path)
    _host_copy(root, "nuc")
    listed = _just_here(root, "host", "--json")
    assert listed.returncode == 0, listed.stderr
    assert [row["name"] for row in json.loads(listed.stdout)["hosts"]] == ["pve", "nuc"]
    lock = tmp_path / "run.lock"
    switched = _just_here(root, "host", "nuc", settings=(f"RUN_LOCK={lock}",))
    assert switched.returncode == 0, switched.stderr
    assert (root / "hosts" / "current").read_text() == "nuc\n"
    assert lock.read_text().splitlines()[2] == "host nuc"
    refused = _just_here(root, "host", "nuc", "--remove")
    assert refused.returncode != 0 and "there is no terminal to ask at" in refused.stderr
    assert (root / "hosts" / "nuc").is_dir()


def test_a_removal_names_the_host_to_type_and_deletes_only_on_it(tmp_path: Path) -> None:
    from harness import _at_a_pseudo_terminal
    from test_justfile import JUST, _python, _stand_in
    from test_justfile import _env as _just_env

    root = _checkout_copy(tmp_path)
    _host_copy(root, "nuc")
    bin_dir = tmp_path / "bin"
    _stand_in(bin_dir, "ansible-galaxy")
    _python(tmp_path)
    argv = [JUST, "RUN_LOCK=", f"BIN={bin_dir}", f"LOG_DIR={tmp_path / 'logs'}", "host", "nuc", "--remove"]

    def typing(answer: bytes):
        typed = []

        def act(chunk, output, master, process):
            if not typed and output.endswith(b"Type nuc to delete it: "):
                os.write(master, answer + b"\n")
                typed.append(answer)

        return act

    # The prompt names what to type; anything else deletes nothing.
    rc, said = _at_a_pseudo_terminal(argv, root, _just_env(), act=typing(b"nope"), timeout=60)
    assert rc != 0 and b"Type nuc to delete it: " in said and b"Nothing was deleted." in said, said
    assert (root / "hosts" / "nuc").is_dir()
    rc, said = _at_a_pseudo_terminal(argv, root, _just_env(), act=typing(b"nuc"), timeout=60)
    assert rc == 0, said
    assert not (root / "hosts" / "nuc").exists()


# ---------------------------------------------------------------------------------------- a run through an overlay ----
# The project's own playbooks against two fake Proxmox hosts with different token secrets, a VM on each: the first host
# in inventory/, the second in hosts/nuc/. What the overlay must hold for every playbook: the run asks one host only,
# with that host's secret; its VMs take the host's settings over the shared ones; their aliases record it.


@pytest.fixture
def two_hosts(tmp_path, monkeypatch):
    import harness

    servers, ports = [], {}
    try:
        for name, secret, vmid, vm in (("first", FIRST_SECRET, 9010, "alpha"), ("nuc", NUC_SECRET, 9020, "beta")):
            folder = tmp_path / f"fake-{name}"
            folder.mkdir()
            cert, key = harness._certificate(folder)
            served = harness._serve(harness.FAKES / "fake_pve_api.py", str(cert), str(key), str(folder / "pve"), secret)
            base = next(served).replace("http://", "https://")
            servers.append(served)
            # The fixture's own requests carry the secret of the fake they go to.
            monkeypatch.setattr(harness, "PVE_SECRET", secret)
            harness._pve_vm(base, vmid, vm, running=True, tags="agent-warehouse", ciuser="dev")
            ports[name] = int(base.rsplit(":", 1)[1])
            (folder / "pve" / "calls.log").unlink()
        yield ports
    finally:
        for served in servers:
            served.close()


def _overlay_checkout(tmp_path: Path, ports: dict) -> Path:
    from harness import SSH_REFUSED, _executable

    root = tmp_path / "checkout"
    _checkout(
        root,
        local={
            "proxmox_api_port": ports["first"],
            "proxmox_validate_certs": False,
            "proxmox_storage": "shared-store",
            "vm_user": "dev",
            "vm_ssh_public_keys": [],
            "login_desktop": False,
            "claude_ssh_config_file": str(tmp_path / "ssh.conf"),
            "claude_ssh_user_config": str(tmp_path / "config"),
            "claude_ssh_config_include": False,
            "ansible_ssh_executable": str(tmp_path / "ssh"),
            "vpn": "none",
        },
    )
    (root / "inventory" / "hosts.yml").write_text(_hosts_yml("pve", "127.0.0.1"))
    _executable(tmp_path / "ssh", SSH_REFUSED)
    _host(
        root,
        "nuc",
        hosts_yml=_hosts_yml("nuc", "127.0.0.1"),
        local={**ALWAYS, "proxmox_node": "pve", "proxmox_api_port": ports["nuc"], "proxmox_storage": "nuc-store"},
    )
    return root


def _requests(tmp_path: Path, name: str) -> list[dict]:
    log = tmp_path / f"fake-{name}" / "pve" / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _through(root: Path, playbook: Path, sources: str | None) -> dict:
    """playbook from the checkout at root, its sources as ANSIBLE_INVENTORY (None: ansible.cfg's, the first host's);
    the json callback's report."""
    env = _env(
        root, f"inventory = {root / 'inventory'}\n", ANSIBLE_STDOUT_CALLBACK="ansible.builtin.json", ANSIBLE_NOCOLOR="1"
    )
    if sources is not None:
        env["ANSIBLE_INVENTORY"] = sources
    done = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "--vault-password-file", str(root / ".vault_pass"), str(playbook)],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    report = json.loads(done.stdout)
    report["rc"], report["stderr"] = done.returncode, done.stderr
    return report


def test_a_run_through_an_overlay_asks_its_host_alone_and_its_vms_take_its_settings(tmp_path: Path, two_hosts) -> None:
    from harness import _messages

    root = _overlay_checkout(tmp_path, two_hosts)
    # Discovery as every VM recipe runs it, then a play at the VMs it found - on this machine, since no VM answers
    # here - saying what each VM's run would read.
    play = root / "vms.yml"
    play.write_text(
        yaml.safe_dump(
            [
                {"name": "Discover", "ansible.builtin.import_playbook": str(REPO / "playbooks" / "discover.yml")},
                {
                    "name": "Each VM's settings",
                    "hosts": "claude_vms",
                    "gather_facts": False,
                    "vars": {"ansible_connection": "local"},
                    "tasks": [
                        {
                            "name": "Say them",
                            "ansible.builtin.debug": {
                                "msg": "{{ proxmox_storage }} {{ claude_ssh_origin }} {{ proxmox_api_port }} "
                                "{{ 'first' if proxmox_api_token_secret == '" + FIRST_SECRET + "' else 'own' }}"
                            },
                        }
                    ],
                },
            ]
        )
    )
    overlay = _through(root, play, "inventory/controller.yml,hosts/nuc")
    assert overlay["rc"] == 0, overlay["stderr"][-3000:]
    assert _messages(overlay, "Say them") == {"beta": f"nuc-store nuc {two_hosts['nuc']} own"}
    assert _requests(tmp_path, "first") == [] and _requests(tmp_path, "nuc")
    first = _through(root, play, None)
    assert first["rc"] == 0, first["stderr"][-3000:]
    assert _messages(first, "Say them") == {"alpha": f"shared-store pve {two_hosts['first']} first"}

    # The alias refresh of each: alpha's alias records the first host, and the refresh through the overlay - which
    # takes the first host's VMs for gone from its listing - leaves it, writing beta's for nuc.
    for sources in (None, "inventory/controller.yml,hosts/nuc"):
        refreshed = _through(root, REPO / "playbooks" / "ssh_config.yml", sources)
        assert refreshed["rc"] == 0, refreshed["stderr"][-3000:]
    text = (tmp_path / "ssh.conf").read_text()
    assert (
        "# BEGIN agent-warehouse: alpha\n# origin: pve\n" in text
        and "# BEGIN agent-warehouse: beta\n# origin: nuc\n" in text
    )


# ------------------------------------------------------------------------------------- setting a host up ----
# tools/host init, as just init --host runs it: the host's folder from tools/templates/host/, and what the playbooks
# are pointed at. Never a host made from 2.4's habit, a bare word that was an address.


def _init_said(root: Path, *args: str) -> dict:
    said = _tool(root, "init", *args)
    assert said.returncode == 0, said.stderr
    values = {}
    for line in said.stdout.splitlines():
        name, value = line.split("=", 1)
        values[name] = subprocess.run(["bash", "-c", f"printf %s {value}"], capture_output=True, text=True).stdout
    return values


def test_init_of_the_first_host_reads_nothing_and_names_the_shared_secret(tmp_path: Path) -> None:
    root = _checkout(tmp_path, first=False)
    said = _init_said(root)
    assert json.loads(said["init_host_json"]) == {"init_host": {"name": "", "secret": "vault_proxmox_api_token_secret"}}
    assert said["host_sources"] == ""


def test_a_bare_word_with_no_other_host_is_2_4_s_address_and_makes_nothing(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    refused = _tool(root, "init", "nuc", "--asking")
    assert refused.returncode == 1 and "give it as --address nuc" in refused.stderr
    assert list((root / "hosts").iterdir()) == []


def test_a_new_host_needs_an_address_or_a_terminal_to_ask_one_at(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "lab")
    refused = _tool(root, "init", "nuc")
    assert refused.returncode == 1 and "no terminal to ask its address at" in refused.stderr
    assert not (root / "hosts" / "nuc").exists()
    assert _init_said(root, "nuc", "--asking")["init_made"]


def test_init_makes_the_hosts_files_from_its_examples_and_points_the_playbooks_there(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    said = _init_said(root, "nuc", "--address=192.0.2.20")
    folder = root / "hosts" / "nuc"
    assert said["init_made"].split() == [
        "hosts/nuc/hosts.yml",
        "hosts/nuc/group_vars/all/local.yml",
        "hosts/nuc/group_vars/all/vault.yml",
    ]
    hosts = yaml.safe_load((folder / "hosts.yml").read_text())
    assert list(hosts["all"]["children"]["proxmox"]["hosts"]) == ["nuc"]
    assert "# vm_default_name: nuc-default" in (folder / "group_vars" / "all" / "local.yml").read_text()
    for path in folder.rglob("*.yml"):
        assert oct(path.stat().st_mode & 0o777) == "0o600", path
    given = json.loads(said["init_host_json"])
    assert given["init_vault_file"] == str(folder / "group_vars" / "all" / "vault.yml")
    assert given["init_host"]["secret"] == "vault_host_proxmox_api_token_secret"
    assert given["init_host"]["default_vm"] == "nuc-default" and "proxmox_node" in given["init_host"]["always"]
    assert said["host_sources"] == "inventory/controller.yml,hosts/nuc"
    # Run again, it makes nothing it has, and the host is not set up until just init has written its settings.
    assert _init_said(root, "nuc")["init_made"] == ""
    assert "does not set proxmox_node" in _refused(root, "--host=nuc")


def test_a_host_that_lacks_its_own_settings_is_still_reached_leniently(tmp_path: Path) -> None:
    # just settings and just vault are how such a host is put right.
    root = _checkout(tmp_path)
    _host(root, "nuc", local={})
    assert "does not set proxmox_node" in _refused(root, "--host=nuc")
    said = _tool(root, "env", f"--vault-pass={root / '.vault_pass'}", "--host=nuc", "--lenient")
    assert said.returncode == 0 and "hosts/nuc/group_vars/all/vault.yml" in said.stdout


def test_the_current_host_is_said_for_just_inits_offer(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    assert _tool(root, "current").stdout == "\n"
    _host(root, "nuc")
    (root / "hosts" / "current").write_text("nuc\n")
    assert _tool(root, "current").stdout == "nuc\n"


# ---------------------------------------------------------------------------------------- a named VM's host ----
# A VM name is one host's, and the managed SSH config records which (# origin): a VM named is found where it is, never
# on whichever host is current.


def _aliases(root: Path, **origins: str) -> None:
    """The managed SSH config in the run's home, a block for each VM recording its host ("" for none)."""
    ssh = root / ".ssh"
    ssh.mkdir(exist_ok=True)
    text = ""
    for name, origin in origins.items():
        record = f"# origin: {origin}\n" if origin else ""
        body = f"{record}Host {name}\n    HostName 192.0.2.99\n"
        text += f"# BEGIN agent-warehouse: {name}\n{body}# END agent-warehouse: {name}\n"
    (ssh / "agent-warehouse.conf").write_text(text)


def test_a_named_vm_runs_against_the_host_its_alias_records(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    _aliases(root, alpha="nuc", beta="pve", gamma="", delta="lab")
    assert _resolve(root, "--names=alpha")["host"] == "nuc"
    (root / "hosts" / "current").write_text("nuc\n")
    assert _resolve(root, "--names=beta")["host"] == "pve"
    # No record, or one of a host the checkout does not have: the current host, as for a VM to be made.
    assert _resolve(root, "--names=gamma")["host"] == "nuc"
    assert _resolve(root, "--names=delta,newvm")["host"] == "nuc"
    # A name with a record and one without: the recorded one's host.
    assert _resolve(root, "--names=beta,newvm")["host"] == "pve"


def test_names_on_two_hosts_are_one_run_too_many(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _host(root, "nuc")
    _aliases(root, alpha="nuc", beta="pve")
    said = _refused(root, "--names=alpha,beta")
    assert "the VMs named are on 2 hosts (alpha on nuc; beta on pve)" in said


def test_host_given_beside_a_name_another_host_has_reaches_its_own_vm_of_the_name_and_says_so(
    tmp_path: Path,
) -> None:
    # A name two hosts each have, from before a name was one host's, keeps working with --host (plan §5); the run that
    # would write its alias is refused by tasks/vm_names.yml, test_vm_names.py's.
    root = _checkout(tmp_path)
    _host(root, "nuc")
    _aliases(root, alpha="nuc")
    said = _tool(root, "env", f"--vault-pass={root / '.vault_pass'}", "--host=pve", "--names=alpha")
    assert said.returncode == 0, said.stderr
    assert said.stderr.startswith(
        "warning: alpha is nuc's, as its SSH alias records, and this run is against pve: it reaches the VM of the name "
        "on pve."
    )
    assert _resolve(root, "--host=pve", "--names=alpha")["host"] == "pve"
    quiet = _tool(root, "env", f"--vault-pass={root / '.vault_pass'}", "--host=nuc", "--names=alpha")
    assert quiet.returncode == 0 and quiet.stderr == "", quiet.stderr


def test_with_the_aliases_off_a_name_records_nothing(tmp_path: Path) -> None:
    root = _checkout(tmp_path, local={"claude_ssh_config": False})
    _host(root, "nuc")
    _aliases(root, alpha="nuc")
    assert _resolve(root, "--names=alpha")["host"] == "pve"


def test_a_recipe_given_a_vm_another_host_has_runs_against_that_host(tmp_path: Path) -> None:
    from test_justfile import _runs

    root = _checkout_copy(tmp_path)
    _host_copy(root, "nuc")
    _aliases(root, alpha="nuc")
    result = _just_here(root, "login", "alpha")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "inventory.txt").read_text() == "inventory/controller.yml,hosts/nuc\n"
    assert len(_runs(tmp_path, "ansible-playbook")) == 1


def test_status_with_more_than_one_host_reads_every_host(tmp_path: Path) -> None:
    from test_justfile import _runs

    root = _checkout_copy(tmp_path)
    _host_copy(root, "nuc")
    (root / "hosts" / "lab").mkdir()
    result = _just_here(root, "status")
    assert result.returncode == 0, result.stdout + result.stderr
    (run,) = _runs(tmp_path, "python")
    every = json.loads(run[run.index("--hosts") + 1])
    assert [(one["name"], one["current"], bool(one["problem"])) for one in every] == [
        ("pve", True, False),
        ("lab", False, True),
        ("nuc", False, False),
    ]
    assert every[2]["sources"] == ["inventory/controller.yml", "hosts/nuc"]
    assert run[run.index("--cache") + 1] == str(tmp_path / "status") and "-i" not in run


def _status_says(tmp_path: Path, said: str) -> None:
    """The environment's Python, but tools/status's run, which prints said: what a creating deploy's question gets."""
    python = tmp_path / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    record = tmp_path / "python.args"
    python.write_text(
        '#!/bin/sh\nif [ "$1" = tools/status ]; then\n'
        f'  {{ for arg in "$@"; do printf "%s\\n" "$arg"; done; echo --end--; }} >> "{record}"\n'
        f"  printf '%s\\n' {said!r}\n  exit 0\nfi\n"
        f'exec "{sys.executable}" "$@"\n'
    )
    python.chmod(0o755)


def test_a_deploy_that_may_make_a_vm_asks_the_other_hosts_for_its_name(tmp_path: Path) -> None:
    from test_justfile import _runs

    root = _checkout_copy(tmp_path)
    _host_copy(root, "nuc")
    _status_says(tmp_path, "taken newvm nuc")
    refused = _just_here(root, "deploy", "newvm")
    assert refused.returncode != 0
    assert "newvm is a VM of nuc already, and a VM name belongs to one host" in refused.stderr
    assert _runs(tmp_path, "ansible-playbook") == []
    (asked,) = _runs(tmp_path, "python")
    assert "--taken" in asked and asked[asked.index("--names") + 1] == "newvm"
    # Only the hosts the run is not against: here every one but the first, the current.
    assert [one["name"] for one in json.loads(asked[asked.index("--hosts") + 1])] == ["nuc"]


def test_a_host_that_does_not_answer_the_question_is_said_and_the_deploy_goes_on(tmp_path: Path) -> None:
    from test_justfile import _runs

    root = _checkout_copy(tmp_path)
    _host_copy(root, "nuc")
    _status_says(tmp_path, "unread nuc")
    result = _just_here(root, "deploy", "newvm")
    assert result.returncode == 0, result.stderr
    assert "nuc did not answer, so whether it has a VM of these names is not known" in result.stderr
    assert len(_runs(tmp_path, "ansible-playbook")) == 1


def test_a_deploy_of_a_vm_an_alias_places_or_of_the_fleet_asks_no_other_host(tmp_path: Path) -> None:
    from test_justfile import _runs

    root = _checkout_copy(tmp_path)
    _host_copy(root, "nuc")
    _aliases(root, alpha="pve")
    _status_says(tmp_path, "taken alpha nuc")
    for args in (["alpha"], ["--only", "configure"], ["--check"]):
        result = _just_here(root, "deploy", *args)
        assert result.returncode == 0, result.stderr
    assert _runs(tmp_path, "python") == []
