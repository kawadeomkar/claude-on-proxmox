"""The choices a checkout makes once - the editor and the VPN, and later the host's OS: each a setting naming one row of
a list in the inventory defaults (lib/claude_vm/catalogue.py, docs/architecture/custom-code.md §7.11).

Every place that knows a choice's members says the same: the core's CHOICES, tools/settings' choices and tools/check
settings' refusal. The rows are literal data. The vpn setting follows tailscale_enabled, the setting it replaces, when
it is not set, and the Tailscale role is handed what vpn decided, never tailscale_enabled itself.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from harness import REPO, _run

from claude_vm import catalogue

DEFAULTS_FILE = REPO / "inventory" / "group_vars" / "all" / "defaults.yml"
DEFAULTS = yaml.safe_load(DEFAULTS_FILE.read_text())


def _assigned(source: str, name: str):
    """The literal a module-level assignment of name holds, read without running the file."""
    for node in ast.parse(source).body:
        targets = (
            node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"no {name} assigned")


def _check_settings_python() -> str:
    """tools/check settings' Python, the here-document it runs."""
    text = (REPO / "tools" / "check").read_text()
    return re.search(r"<< 'PYTHON'\n(.*?)\nPYTHON\n", text, re.S).group(1)


def test_every_place_that_knows_a_choice_s_members_says_the_same():
    settings = (REPO / "tools" / "settings").read_text()
    choices_from, also = _assigned(settings, "CHOICES_FROM"), _assigned(settings, "CHOICES_ALSO")
    checked = _assigned(_check_settings_python(), "CHOICES")
    for setting, (rows, values) in catalogue.CHOICES.items():
        assert choices_from[setting] == rows
        assert tuple(also.get(setting, ())) == values
        assert isinstance(DEFAULTS[rows], dict) and DEFAULTS[rows]
        # The setting's default is a member, or a value that is none, or an expression the run decides.
        default = DEFAULTS[setting]
        assert default in [*DEFAULTS[rows], *values] or "{{" in default
    # tools/check refuses an unknown value only for a choice whose members are not the editor's: just code's own
    # assert and --editor's pattern hold that one.
    assert set(checked) <= set(catalogue.CHOICES)
    for setting, (rows, values) in checked.items():
        assert catalogue.CHOICES[setting] == (rows, tuple(values))


@pytest.mark.parametrize("setting", sorted(catalogue.CHOICES))
def test_a_choice_s_rows_are_literal_data(setting):
    rows, _ = catalogue.CHOICES[setting]
    assert "{{" not in json.dumps(DEFAULTS[rows]) and "{%" not in json.dumps(DEFAULTS[rows])


def test_each_vpn_s_row_names_its_role_which_configure_imports_statically():
    plays = yaml.safe_load((REPO / "playbooks" / "configure.yml").read_text())
    imported = json.dumps(plays)
    for key, row in DEFAULTS["claude_vpns"].items():
        assert set(row) == {"name", "family", "role", "sign_in"}, key
        assert (REPO / "roles" / row["role"] / "tasks" / "main.yml").is_file()
        # A literal name, so that the role's tag reaches every task of it (--tags <role>).
        assert json.dumps({"name": row["role"]}) in imported, key


def test_rows_parse_in_their_order_and_a_value_that_is_no_member_is_refused_naming_them():
    rows = catalogue.rows_from(DEFAULTS["claude_vpns"], "claude_vpns", lambda key, row: (key, row["name"]))
    assert list(rows) == list(DEFAULTS["claude_vpns"])
    assert catalogue.pick("tailscale", rows, "a VPN")[1] == "Tailscale"
    with pytest.raises(ValueError, match=r"^openvpn is not a VPN: tailscale$"):
        catalogue.pick("openvpn", rows, "a VPN")
    with pytest.raises(ValueError, match="not a mapping of rows"):
        catalogue.rows_from({"tailscale": "Tailscale"}, "claude_vpns", lambda key, row: row)


# ------------------------------------------------- the vpn setting, as a run reads it ---
def _read(tmp_path: Path, local: dict) -> dict:
    """The vpn setting and what it decides, as a run with the inventory defaults and local.yml reads them."""
    # Beside the harness's inventory, so that the run reads them as the project's group_vars.
    group_vars = tmp_path / "group_vars" / "all"
    group_vars.mkdir(parents=True)
    (group_vars / "defaults.yml").symlink_to(DEFAULTS_FILE)
    (group_vars / "local.yml").write_text(yaml.safe_dump(local))
    names = ["claude_vpn_key", "tailscale_wanted", "tailscale_off_setting"]
    playbook = tmp_path / "read.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "gather_facts": False,
                    "tasks": [{"name": "Say", "ansible.builtin.debug": {"msg": {n: "{{ " + n + " }}" for n in names}}}],
                }
            ]
        )
    )
    report = _run(tmp_path, playbook, {"localhost": {"ansible_connection": "local"}})
    assert report["rc"] == 0, report["stderr"][-2000:]
    return report["plays"][0]["tasks"][0]["hosts"]["localhost"]["msg"]


@pytest.mark.parametrize(
    ("local", "key", "off"),
    [
        # No VPN unless one is chosen (2.1): the setting that turned Tailscale off is named as vpn, whichever form
        # of it chose, or none did.
        pytest.param({}, "none", {"name": "vpn", "value": "none"}, id="nothing-set"),
        pytest.param({"tailscale_enabled": True}, "tailscale", None, id="tailscale-on"),
        pytest.param({"tailscale_enabled": False}, "none", {"name": "vpn", "value": "none"}, id="tailscale-off"),
        pytest.param({"vpn": "none"}, "none", {"name": "vpn", "value": "none"}, id="vpn-none"),
        pytest.param({"vpn": "tailscale", "tailscale_enabled": False}, "tailscale", None, id="vpn-wins"),
        pytest.param({"vpn": ""}, "none", {"name": "vpn", "value": "none"}, id="vpn-empty"),
    ],
)
def test_the_vpn_setting_follows_tailscale_enabled_until_it_is_set_and_the_role_is_handed_what_it_decided(
    tmp_path, local, key, off
):
    read = _read(tmp_path, local)
    assert read["claude_vpn_key"] == key
    assert read["tailscale_wanted"] is (key == "tailscale")
    if off:
        assert read["tailscale_off_setting"] == off


def test_tailscale_is_off_unless_chosen_wherever_the_default_is_said():
    # Never by taking tailscale_enabled out of defaults.yml: the role's own default would then be what vpn reads
    # while the role runs, and a fallback that said on would turn it on where the inventory is not loaded.
    assert DEFAULTS["tailscale_enabled"] is False
    role = REPO / "roles" / "tailscale"
    assert yaml.safe_load((role / "defaults" / "main.yml").read_text())["tailscale_enabled"] is False
    specs = yaml.safe_load((role / "meta" / "argument_specs.yml").read_text())["argument_specs"]
    said = [
        spec["options"]["tailscale_enabled"]["default"]
        for spec in specs.values()
        if "tailscale_enabled" in spec["options"]
    ]
    assert said and set(said) == {False}
    for path in [REPO / "deploy.yml", *(REPO / "playbooks").rglob("*.yml")]:
        assert "tailscale_enabled | default(true)" not in path.read_text(), path


def test_the_tailscale_role_reads_what_the_vpn_setting_decided_never_tailscale_enabled():
    role = REPO / "roles" / "tailscale"
    for path in [*(role / "tasks").glob("*.yml"), role / "vars" / "main.yml"]:
        assert "tailscale_enabled" not in path.read_text(), path
    # A playbook reads the role's input, or tailscale_enabled only as its fallback where no inventory defaults are
    # loaded (the harnesses), as the role's own default does.
    for path in [REPO / "deploy.yml", *(REPO / "playbooks").rglob("*.yml"), *(REPO / "playbooks").rglob("*.j2")]:
        if path.name.startswith("init_"):
            continue  # just init reads and writes local.yml's tailscale_enabled itself
        for number, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0] if path.suffix == ".yml" else line
            if "tailscale_enabled" in code:
                assert "tailscale_wanted | default(tailscale_enabled" in code or "claude_vpn_key | default(" in code, (
                    f"{path}:{number}"
                )


# --------------------------------------------- tools/check settings, for the vpn setting ---
def _check(tmp_path: Path, text: str) -> subprocess.CompletedProcess:
    local = tmp_path / "local.yml"
    local.write_text(text)
    return subprocess.run(
        [str(REPO / "tools" / "check"), "settings", str(Path(sys.executable).parent), str(local)],
        cwd=REPO,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("value", ["tailscale", "none", "", "{{ 'none' }}"])
def test_check_settings_takes_a_vpn_that_is_one_of_its_choices(tmp_path, value):
    result = _check(tmp_path, f"vpn: {json.dumps(value)}\n")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_check_settings_refuses_a_vpn_that_is_none_of_them_naming_them_and_never_the_value(tmp_path):
    result = _check(tmp_path, "vpn: s3cret-typed-here\n")
    assert result.returncode == 1
    assert "sets vpn to something that is none of its choices: tailscale, none" in result.stderr
    assert "s3cret" not in result.stderr


@pytest.mark.parametrize("value", ["on", "yes", "true", "off", "1"])
def test_check_settings_refuses_a_vpn_yaml_reads_as_no_word(tmp_path, value):
    # Unquoted, YAML reads these as a boolean or a number: the setting would be "True", no VPN's, and Tailscale off.
    result = _check(tmp_path, f"vpn: {value}\n")
    assert result.returncode == 1
    assert "sets vpn to something that is none of its choices" in result.stderr


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("vpn: wireguard\n", "sets vpn to wireguard, which 2.1 removed: a VPN that routes your LAN"),
        ("vpn_reach: tunnel\n", "sets vpn_reach, which 2.1 removed with WireGuard: take it out"),
        ("wireguard_hub: opnsense\n", "sets wireguard_hub, which 2.1 removed with WireGuard: take it out"),
        ('wireguard_hub_endpoint: "192.0.2.1:51820"\n', "sets wireguard_hub_endpoint, which 2.1 removed"),
    ],
)
def test_check_settings_refuses_what_2_1_removed_with_wireguard_pointing_at_the_upgrade(tmp_path, text, said):
    result = _check(tmp_path, text)
    assert result.returncode == 1
    assert said in result.stderr and "docs/guides/upgrading.md" in result.stderr
    # Said once, as removed: never also as a value that is none of vpn's choices.
    assert "none of its choices" not in result.stderr


@pytest.mark.parametrize("text", ["vpn_reach: lan\n", 'wireguard_vm_prefix: ""\n'])
def test_check_settings_lets_a_removed_setting_that_changed_nothing_pass(tmp_path, text):
    result = _check(tmp_path, "vpn: none\n" + text)
    assert result.returncode == 0, result.stderr
    assert ("warning:" in result.stderr) is text.startswith("vpn_reach")


@pytest.mark.parametrize(
    ("text", "warned"),
    [("vm_cores: 4\n", True), ("vpn: none\n", False), ("tailscale_enabled: true\n", False), ("vpn: ''\n", False)],
)
def test_check_settings_says_when_no_vpn_is_chosen(tmp_path, text, warned):
    # From 2.1 a local.yml that chooses none is vpn: none, where it was Tailscale: said, never refused.
    result = _check(tmp_path, text)
    assert result.returncode == 0, result.stderr
    assert ("sets no vpn: since 2.1 that is none" in result.stderr) is warned
    if warned:
        assert "docs/guides/upgrading.md" in result.stderr


def test_just_init_asks_the_question_rather_than_warn_of_it(tmp_path):
    local = tmp_path / "local.yml"
    local.write_text("vm_cores: 4\n")
    result = subprocess.run(
        [str(REPO / "tools" / "check"), "settings", str(Path(sys.executable).parent), "--asking", str(local)],
        cwd=REPO,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and result.stderr == "", result.stderr
    assert 'tools/check settings "$bin" --asking $init_settings_files' in (REPO / "justfile").read_text()


def test_check_settings_finds_the_defaults_whatever_cdpath_says(tmp_path):
    # just runs tools/check by a relative path: an exported CDPATH would have cd print the directory, or go to
    # another checkout's, and every choice but none be refused.
    (tmp_path / "elsewhere" / "tools").mkdir(parents=True)
    local = tmp_path / "local.yml"
    local.write_text("vpn: tailscale\n")
    result = subprocess.run(
        ["tools/check", "settings", str(Path(sys.executable).parent), str(local)],
        cwd=REPO,
        env={"PATH": "/usr/bin:/bin", "CDPATH": f".:{tmp_path / 'elsewhere'}"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
