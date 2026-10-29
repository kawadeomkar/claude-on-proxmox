"""just settings changes nothing and prints no secret (tools/settings, docs/architecture/custom-code.md §7.9).

Each test runs the tool against an inventory of its own, made from the example files with fake, low-entropy values
and a vault encrypted with a throwaway password - never the checkout's own settings, which on a developer's machine
are real. Every run gets a HOME of its own, so nothing reads the developer's ~/.ssh (vm_ssh_public_keys reads
~/.ssh/id_ed25519.pub), with the Galaxy collections' path pinned beside it, read only, since no role loads without
them. No run inherits ANSIBLE_VAULT_PASSWORD_FILE, and every run fails if any fake secret is in what it printed.

The scenarios are attacks on what the tool promises: a pipe lookup behind a group variable in hosts.yml, an
executable in inventory/, a secret vaulted inline in local.yml, a setting reading a vault name without the vault_
prefix, a GitHub username that is a common word, a secret copied in under a computed name, a control sequence in a
value.
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "settings"
PASSWORD = "throwaway-password-0000\n"
FAKE = {
    "vault_tailscale_auth_key": "tskey-auth-fakefakefake-0000000000000000",
    "vault_github_token": "ghp_000000000000000000000000000000000000",
    "vm_password": "fake-vm-password-0000",
    # A username that is a common word, which a check of whole lines would find in every source column.
    "vault_github_username": "local",
}
INLINE = "INLINE-SECRET-0000-0000"
SECRETS = (FAKE["vault_tailscale_auth_key"], FAKE["vault_github_token"], FAKE["vm_password"], INLINE)
ROLES = sorted(path.parent.parent.name for path in (REPO / "roles").glob("*/meta/argument_specs.yml"))
QUIET = {"ANSIBLE_VERBOSITY": "0", "ANSIBLE_DEBUG": "0"}


def _collections() -> str:
    return os.environ.get("ANSIBLE_COLLECTIONS_PATH") or str(Path.home() / ".ansible" / "collections")


def _vault_tool(*args: str, env: dict) -> str:
    bin_dir = Path(sys.executable).parent
    return subprocess.run(
        [str(bin_dir / "ansible-vault"), *args], check=True, capture_output=True, text=True, env=env
    ).stdout


def _scenario(root: Path, name: str, local_extra: str, env: dict, address: str = "") -> Path:
    """An inventory from the examples, and the attacks; with an address, the Proxmox host's, else the example's."""
    inventory = root / name / "inventory"
    (inventory / "group_vars" / "all").mkdir(parents=True)
    (inventory / "host_vars").mkdir()
    (inventory / "host_vars" / "alpha.yml").write_text("tailscale_ssh: false\n")
    for each in ("controller.yml", "group_vars/all/defaults.yml"):
        (inventory / each).write_text((REPO / "inventory" / each).read_text())
    hosts = yaml.safe_load((REPO / "inventory" / "hosts.yml.example").read_text())
    hosts["all"]["children"]["claude_vms"]["vars"] = {
        # A pipe lookup a scan of expressions never sees, read by a local.yml setting below.
        "claude_probe": "{{ lookup('ansible.builtin.pipe', 'touch " + str(root / name / "pipe-ran") + "') }}",
        # The VMs' group: proxmox_vm runs on claude_vm_pending placeholders, so this must not reach it.
        "proxmox_vm_cores": 8,
    }
    hosts["all"]["children"]["proxmox"]["hosts"]["pve"]["proxmox_template_storage"] = "from-the-pve-host"
    if address:
        hosts["all"]["children"]["proxmox"]["hosts"]["pve"]["ansible_host"] = address
    (inventory / "hosts.yml").write_text(yaml.safe_dump(hosts, sort_keys=False))
    probe = inventory / "probe.sh"
    probe.write_text(f"#!/bin/sh\ntouch {root / name / 'inventory-script-ran'}\necho '{{}}'\n")
    probe.chmod(0o755)
    local = (REPO / "inventory" / "group_vars" / "all" / "local.yml.example").read_text()
    local += (
        "\nvm_cores: 4\nclaude_remote_control: false\ntailscale_ssh: true\n"
        'tailscale_daemon_flags: ["{{ claude_probe }}"]\n'
        'tailscale_api_url: "{{ vm_password }}"\n'
        'vm_searchdomain: "\\e]0;retitled\\a.lan"\n' + local_extra
    )
    (inventory / "group_vars" / "all" / "local.yml").write_text(local)
    vault = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "vault.yml.example").read_text())
    vault.update(FAKE)
    vault_file = inventory / "group_vars" / "all" / "vault.yml"
    vault_file.write_text(yaml.safe_dump(vault, sort_keys=False))
    _vault_tool("encrypt", "--vault-password-file", str(root / "pw"), str(vault_file), env=env)
    return inventory


@pytest.fixture(scope="module")
def scenarios(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("settings")
    (root / "pw").write_text(PASSWORD)
    (root / "home" / ".ssh").mkdir(parents=True)
    env = {"PATH": os.environ["PATH"], "HOME": str(root / "home"), "ANSIBLE_COLLECTIONS_PATH": _collections(), **QUIET}
    inline = _vault_tool(
        "encrypt_string", "--vault-password-file", str(root / "pw"), INLINE, "--name", "tailscale_login_server", env=env
    )
    copied = "tailscale_login_server: \"{{ lookup('ansible.builtin.vars', 'vault_' ~ 'github_token') }}\"\n"
    empty = root / "empty" / "inventory"
    empty.mkdir(parents=True)
    return {
        "root": root,
        "base": _scenario(root, "base", "# A secret vaulted inline in local.yml\n" + inline, env, "192.0.2.55"),
        "copied": _scenario(root, "copied", copied, env),
        # The example's own address and nothing added: what --all says of a host still the example's.
        "plain": _scenario(root, "plain", "", env),
        "empty": empty,
    }


def _run(scenarios: dict, inventory: str, *args: str, password: bool | str = True) -> subprocess.CompletedProcess:
    root = scenarios["root"]
    env = {
        "PATH": os.environ["PATH"],
        "LANG": "en_US.UTF-8",
        "HOME": str(root / "home"),
        "ANSIBLE_COLLECTIONS_PATH": _collections(),
        **QUIET,
    }
    given = []
    if password == "env":
        env["ANSIBLE_VAULT_PASSWORD_FILE"] = str(root / "pw")
    elif password:
        given = ["--vault-password-file", str(root / "pw")]
    result = subprocess.run(
        [sys.executable, str(TOOL), "--inventory", str(scenarios[inventory]), *given, *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    said = result.stdout + result.stderr
    assert not [secret for secret in SECRETS if secret in said], "a fake secret was printed"
    return result


@functools.cache
def _cached(scenarios_id: int, inventory: str, args: tuple) -> subprocess.CompletedProcess:
    return _run(_SCENARIOS[scenarios_id], inventory, *args)


_SCENARIOS: dict[int, dict] = {}


def _report(scenarios: dict, *args: str, inventory: str = "base") -> dict:
    """The JSON report of a run that works, its rows by name."""
    _SCENARIOS[id(scenarios)] = scenarios
    result = _cached(id(scenarios), inventory, ("--json=true", *args))
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads(result.stdout)
    report["rows"] = {row["name"]: row for row in report["settings"]}
    return report


def _text(scenarios: dict, *args: str) -> str:
    _SCENARIOS[id(scenarios)] = scenarios
    result = _cached(id(scenarios), "base", args)
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


# ------------------------------------------------------------------------------------ every role, every row ---


@pytest.mark.parametrize("role", ROLES)
def test_every_role_shows_every_variable_its_argument_specs_list(scenarios, role) -> None:
    options = set()
    for spec in yaml.safe_load((REPO / "roles" / role / "meta" / "argument_specs.yml").read_text())[
        "argument_specs"
    ].values():
        options |= set(spec.get("options") or {})
    assert set(_report(scenarios, "--", role)["rows"]) == options


@pytest.mark.parametrize("args", [("--", "tailscale"), ("--", "proxmox_vm"), ("--all=true",), (), ("--usage",)])
def test_every_line_fits_100_columns(scenarios, args) -> None:
    printed = _text(scenarios, *args)
    assert printed.strip()
    assert [line for line in printed.splitlines() if len(line) > 100] == []


# ------------------------------------------------------------------------------------ changes nothing (R1) ---


def test_a_pipe_lookup_is_refused_however_it_is_reached_and_never_runs(scenarios) -> None:
    row = _report(scenarios, "--", "tailscale")["rows"]["tailscale_daemon_flags"]
    assert row["kind"] == "at_run_start" and row["lookup"] == "ansible.builtin.pipe", row
    assert not (scenarios["root"] / "base" / "pipe-ran").exists()


def test_an_executable_in_the_inventory_is_never_run(scenarios) -> None:
    _report(scenarios, "--all=true")
    assert not (scenarios["root"] / "base" / "inventory-script-ran").exists()


def test_a_run_changes_no_file_of_its_inventory(scenarios) -> None:
    files = sorted(path for path in scenarios["base"].rglob("*") if path.is_file())
    before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in files}
    assert _run(scenarios, "base", "--all=true").returncode == 0
    assert {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in files} == before
    assert sorted(path for path in scenarios["base"].rglob("*") if path.is_file()) == files


# ------------------------------------------------------------------------------------- no secret (R2, R3) ---


def test_an_inline_vault_value_is_masked(scenarios) -> None:
    row = _report(scenarios, "--", "tailscale")["rows"]["tailscale_login_server"]
    assert (row["kind"], row["value"], row["secret"], row["change"]) == ("secret", None, "set", "just vault"), row


def test_a_setting_that_reads_any_name_the_vault_holds_is_masked(scenarios) -> None:
    # vm_password has no vault_ prefix.
    row = _report(scenarios, "--", "tailscale")["rows"]["tailscale_api_url"]
    assert row["kind"] == "secret" and row["value"] is None, row


def test_a_username_that_is_a_common_word_refuses_no_report_and_is_masked_in_its_own_row(scenarios) -> None:
    for args in (("--", "tailscale"), ("--all=true",), ("--", "github_projects")):
        _report(scenarios, *args)
    row = _report(scenarios, "--", "github_projects")["rows"]["github_projects_username"]
    assert row["kind"] == "secret" and row["value"] is None and row["secret"] == "set", row


def test_a_secret_copied_in_by_a_computed_name_prints_nothing(scenarios) -> None:
    result = _run(scenarios, "copied", "--", "tailscale")
    assert result.returncode == 1 and result.stdout == ""
    assert "refusing to print a secret: tailscale_login_server" in result.stderr


def test_a_secrets_row_says_set_or_not_set_or_the_examples_value(scenarios) -> None:
    rows = _report(scenarios, "--all=true")["rows"]
    assert rows["vault_tailscale_auth_key"]["secret"] == "set"
    assert rows["vault_tailscale_api_token"]["secret"] == "not set"
    # Still the example's, as just init decides it: compared, never matched by a pattern.
    assert rows["vault_proxmox_api_token_secret"]["secret"] == "placeholder"
    assert all(row["value"] is None for row in rows.values() if row["kind"] == "secret")


def test_a_control_sequence_in_a_value_never_reaches_the_terminal(scenarios) -> None:
    assert "\x1b" not in _text(scenarios, "--all=true") and "\x07" not in _text(scenarios, "--all=true")


# ----------------------------------------------------------------------------- the vault's password file ---


def test_the_vault_opens_from_ansible_vault_password_file_as_ansible_playbooks_does(scenarios) -> None:
    assert _run(scenarios, "base", "--", "tailscale", password="env").returncode == 0


def test_a_vault_with_no_password_is_refused_printing_nothing(scenarios) -> None:
    result = _run(scenarios, "base", "--", "tailscale", password=False)
    assert result.returncode == 1 and result.stdout == ""
    assert "there is no .vault_pass or ANSIBLE_VAULT_PASSWORD_FILE" in result.stderr


def test_a_wrong_password_is_said(scenarios, tmp_path) -> None:
    wrong = tmp_path / "wrong"
    wrong.write_text("not-the-password\n")
    result = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--inventory",
            str(scenarios["base"]),
            "--vault-password-file",
            str(wrong),
            "--",
            "tailscale",
        ],
        cwd=REPO,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(scenarios["root"] / "home"),
            "ANSIBLE_COLLECTIONS_PATH": _collections(),
            **QUIET,
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 1 and result.stdout == ""
    assert f"vault.yml could not be opened with {wrong}" in result.stderr


# ---------------------------------------------------------------------- where a value comes from (R4-R8) ---


def test_each_role_is_evaluated_where_its_playbook_runs_it(scenarios) -> None:
    vm = _report(scenarios, "--", "proxmox_vm")["rows"]["proxmox_vm_cores"]
    # provision.yml runs proxmox_vm on claude_vm_pending placeholders: the claude_vms group's 8 never reaches it.
    assert (vm["evaluated_as"], vm["value"], vm["via"]) == (
        "claude_vm_pending",
        4,
        [{"name": "vm_cores", "file": "local.yml"}],
    ), vm
    template = _report(scenarios, "--", "proxmox_template")["rows"]["proxmox_template_storage"]
    assert (template["evaluated_as"], template["value"], template["set_in"]) == (
        "proxmox",
        "from-the-pve-host",
        "hosts.yml",
    ), template
    assert _report(scenarios, "--", "tailscale")["rows"]["tailscale_ssh"]["evaluated_as"] == "claude_vms"


def test_a_role_variable_fed_by_a_short_setting_names_the_setting(scenarios) -> None:
    row = _report(scenarios, "--", "claude_code")["rows"]["claude_code_remote_control"]
    assert (row["value"], row["set_in"], row["via"]) == (
        False,
        "local.yml",
        [{"name": "claude_remote_control", "file": "local.yml"}],
    ), row


def test_what_a_run_sets_is_said_beside_the_value_runs_without_it_use(scenarios) -> None:
    rows = _report(scenarios, "--", "tailscale")["rows"]
    # just deploy --only configure runs configure.yml without deploy.yml's vars: false is what it uses.
    follows = rows["tailscale_sign_in_follows"]
    assert follows["value"] is False and "just deploy sets it from deploy_sign_in" in follows["set_by_run"], follows
    assert "just login sets it per run" in rows["tailscale_login_interactive"]["set_by_run"]
    vm = _report(scenarios, "--", "proxmox_vm")["rows"]
    assert vm["proxmox_vm_state"]["value"] == "present"
    assert 'just destroy sets it to "absent"' in vm["proxmox_vm_state"]["set_by_run"]
    assert "just deploy may pass true" in vm["proxmox_vm_start_existing"]["set_by_run"]


def test_a_value_from_the_vms_name_or_its_facts_is_said_as_such(scenarios) -> None:
    assert _report(scenarios, "--", "tailscale")["rows"]["tailscale_hostname"]["kind"] == "per_vm"
    dev_tools = _report(scenarios, "--", "dev_tools")["rows"]
    assert dev_tools["dev_tools_deb_arch"]["kind"] == "on_the_vm"


def test_all_starts_with_the_host_and_templates_defaults_that_live_in_a_role(scenarios) -> None:
    report = _report(scenarios, "--all=true")
    host = report["settings"][0]
    assert (host["name"], host["kind"], host["value"], host["set_in"]) == (
        "ansible_host",
        "value",
        "192.0.2.55",
        "hosts.yml",
    )
    # Printed too: Ansible's tagged str, which YAML cannot represent until it is made plain.
    assert "192.0.2.55" in _text(scenarios, "--all=true")
    still = _report(scenarios, "--all=true", inventory="plain")["settings"][0]
    assert (still["name"], still["kind"], still["value"]) == ("ansible_host", "placeholder", None), still
    rows = report["rows"]
    assert rows["vm_cores"]["value"] == 4 and rows["vm_cores"]["set_in"] == "local.yml"
    assert rows["vm_memory_min_mb"]["value"] == rows["vm_memory_mb"]["value"] == 4096, rows["vm_memory_min_mb"]


def test_home_is_the_tests_own(scenarios) -> None:
    assert _report(scenarios, "--all=true")["rows"]["vm_ssh_public_keys"]["value"] == []


def test_the_tool_asks_no_private_module_of_ansibles() -> None:
    assert "ansible._internal" not in TOOL.read_text()


# ------------------------------------------------------------------------------------ the forms and options ---


def test_changed_alone_lists_every_setting_changed_and_no_secret_left_unset(scenarios) -> None:
    rows = _report(scenarios, "--changed=true")["rows"]
    assert {"vm_cores", "claude_remote_control", "tailscale_ssh", "vault_tailscale_auth_key"} <= set(rows), sorted(rows)
    assert "vault_tailscale_api_token" not in rows and "vault_proxmox_api_token_secret" not in rows
    assert "vm_memory_mb" not in rows


def test_a_role_with_changed_lists_only_what_was_changed(scenarios) -> None:
    rows = _report(scenarios, "--changed=true", "--", "tailscale")["rows"]
    assert "tailscale_ssh" in rows and "tailscale_accept_dns" not in rows


def test_how_to_use_it_needs_no_vault_and_lists_every_role(scenarios) -> None:
    printed = _run(scenarios, "empty", "--usage", password=False)
    assert printed.returncode == 0
    for role in ROLES:
        assert re.search(rf"(?m)^  {role} ", printed.stdout), role
    listed = json.loads(_run(scenarios, "empty", "--usage", "--json=true", password=False).stdout)
    assert [each["role"] for each in listed["roles"]] == ROLES


def test_one_variable_names_the_chain_of_files_and_where_to_change_it(scenarios) -> None:
    printed = _text(scenarios, "--", "claude_code_remote_control")
    assert "claude_remote_control in local.yml" in printed
    # The wiring line wraps within 100 columns: read as one.
    wiring = 'claude_code_remote_control: "{{ claude_remote_control }}" in inventory/group_vars/all/defaults.yml'
    assert wiring in " ".join(printed.split())
    assert "change    inventory/group_vars/all/local.yml" in printed
    secret = _text(scenarios, "--", "tailscale_auth_key")
    assert "set (secret)" in secret and "change    just vault" in secret


def test_a_setting_of_local_yml_is_explained_too(scenarios) -> None:
    assert _report(scenarios, "--", "vm_cores")["rows"]["vm_cores"]["value"] == 4


def test_a_name_that_is_no_role_variable_or_setting_is_refused(scenarios) -> None:
    result = _run(scenarios, "base", "--", "nonsense")
    assert result.returncode == 1 and result.stdout == "" and "nonsense is neither a role" in result.stderr


def test_a_per_vm_file_is_noted_since_ansible_would_apply_it(scenarios) -> None:
    assert any("host_vars/ holds alpha.yml" in note for note in _report(scenarios, "--", "tailscale")["notes"])
    assert "host_vars/ holds alpha.yml" in _text(scenarios, "--", "tailscale").replace("\n", " ")


def test_the_text_and_the_json_hold_the_same_rows(scenarios) -> None:
    names = set(_report(scenarios, "--", "tailscale")["rows"])
    printed = {line.split()[0] for line in _text(scenarios, "--", "tailscale").splitlines() if line.startswith("  t")}
    assert printed == names
