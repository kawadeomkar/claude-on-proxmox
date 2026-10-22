"""filter_plugins/settings.py: what make init accepts as each kind of answer, and how it writes one into a file."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml
from ansible.errors import AnsibleFilterError

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("settings_filters", REPO / "filter_plugins" / "settings.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
RULES = _module.RULES
init_answer = _module.init_answer
init_answer_rule = _module.init_answer_rule
with_setting = _module.with_setting

ACCEPTED = {
    "host": [
        ("10.0.0.5", "10.0.0.5"),
        (" pve.lan ", "pve.lan"),
        ("pve", "pve"),
        ("PVE-1.home.arpa", "PVE-1.home.arpa"),
        ("fd00::1", "fd00::1"),
        ("fd00:0:0:0:0:0:0:1", "fd00::1"),
        ("a" * 63 + ".lan", "a" * 63 + ".lan"),
    ],
    "token_id": [
        ("ansible@pve!ansible", "ansible@pve!ansible"),
        ("deploy.bot@pam!claude-1", "deploy.bot@pam!claude-1"),
    ],
    "uuid": [
        ("0123abcd-4567-89ef-0123-456789abcdef", "0123abcd-4567-89ef-0123-456789abcdef"),
        ("0123ABCD-4567-89EF-0123-456789ABCDEF\n", "0123abcd-4567-89ef-0123-456789abcdef"),
    ],
    "github_login": [("octocat", "octocat"), ("a", "a"), ("Mona-Lisa-1", "Mona-Lisa-1"), ("a" * 39, "a" * 39)],
    "yes_no": [("y", "y"), ("YES", "y"), ("n", "n"), ("No", "n")],
}

REFUSED = {
    "host": [
        "192.0.2.10",
        "https://pve.lan",
        "pve.lan:8006",
        "pve.lan; x",
        "1.2.3.4 ProxyCommand x",
        '"quoted"',
        "a#b",
        "-pve.lan",
        "pve-.lan",
        "pve..lan",
        "999.1.1.1",
        "1.2.3",
        "fe80::1%en0",
        "a" * 64 + ".lan",
        "a." * 127 + "a",
        "pve\nlan",
    ],
    "token_id": ["ansible@pve", "ansible!x", "@pve!x", "a@1pve!x", "a@pve!1x", "a@pve!x y", "a b@pve!x"],
    "uuid": ["0123abcd-4567", "0123abcd45678-9ef-0123-456789abcdef", "g123abcd-4567-89ef-0123-456789abcdef"],
    "github_login": ["-octocat", "octocat-", "octo--cat", "a" * 40, "octo cat", "octo_cat"],
    "yes_no": ["maybe", "1", "yy"],
}


@pytest.mark.parametrize(("kind", "value", "normal"), [(k, v, n) for k, cases in ACCEPTED.items() for v, n in cases])
def test_an_answer_of_each_kind_is_taken_in_its_normal_form(kind: str, value: str, normal: str) -> None:
    assert init_answer(value, kind) == normal


@pytest.mark.parametrize(("kind", "value"), [(k, v) for k, values in REFUSED.items() for v in values])
def test_anything_else_is_refused(kind: str, value: str) -> None:
    assert init_answer(value, kind) == ""


@pytest.mark.parametrize("kind", sorted(RULES))
def test_nothing_typed_is_nothing(kind: str) -> None:
    assert init_answer("", kind) == "" and init_answer("   ", kind) == "" and init_answer(None, kind) == ""


def test_a_choice_is_the_item_its_number_names() -> None:
    choices = ["local-zfs", "tank-vms"]
    assert init_answer("2", "choice", choices) == "tank-vms"
    assert [init_answer(value, "choice", choices) for value in ("0", "3", "x", "-1", "1.0")] == [""] * 5


def test_every_kind_has_its_rule_in_words() -> None:
    assert all(init_answer_rule(kind) for kind in RULES)
    with pytest.raises(AnsibleFilterError):
        init_answer("x", "colour")


EXAMPLE = '---\n# The secret.\nkey: "xxxx" # the example\nother: 1\n'


def test_with_setting_replaces_the_examples_line_in_place() -> None:
    assert with_setting(EXAMPLE, "key", "s3cret", "xxxx") == '---\n# The secret.\nkey: "s3cret"\nother: 1\n'


@pytest.mark.parametrize("line", ['key: ""', "key: ''", "key:", "key: ~"])
def test_with_setting_fills_an_empty_value(line: str) -> None:
    assert with_setting(f"{line}\nother: 1\n", "key", "v") == 'key: "v"\nother: 1\n'


def test_with_setting_appends_a_missing_key() -> None:
    assert with_setting("other: 1", "key", "v") == 'other: 1\nkey: "v"\n'
    assert with_setting("other: 1\n", "key", True) == "other: 1\nkey: true\n"


def test_with_setting_never_writes_over_a_value() -> None:
    with pytest.raises(AnsibleFilterError, match="already set"):
        with_setting('key: "real"\n', "key", "v", "xxxx")


def test_with_setting_leaves_a_commented_or_indented_key_alone() -> None:
    text = '# key: "xxxx"\nnested:\n  key: x\n'
    assert yaml.safe_load(with_setting(text, "key", "v")) == {"nested": {"key": "x"}, "key": "v"}


def test_a_value_written_is_read_back_whole() -> None:
    # JSON is YAML: what make init writes reads back as the same value.
    for value in ("pve.lan", "fd00::1", "ansible@pve", "0123abcd-4567-89ef-0123-456789abcdef", True):
        assert yaml.safe_load(with_setting("", "key", value)) == {"key": value}
