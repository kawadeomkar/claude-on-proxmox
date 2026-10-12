"""plain_text: what a VM printed, safe to quote in a message on this machine. banner: the block
that sets a moment the run waits for the person apart."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("text_filters", REPO / "filter_plugins" / "text.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
plain_text = _module.plain_text
banner = _module.banner


@pytest.mark.parametrize(
    ("raw", "plain"),
    [
        ("Login failed: invalid code", "Login failed: invalid code"),
        ("Opening browser to sign in…\nLogin successful.\n", "Opening browser to sign in…\nLogin successful.\n"),
        ("a\tb", "a\tb"),
        # A clipboard write, a title and a colour: gone whole, payload included.
        ("\x1b]52;c;b3duZWQ=\x07\x1b]0;retitled\x07\x1b[31mError\x1b[0m", "Error"),
        ("\x1b]0;title\x1b\\text", "text"),
        ("\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\", "link"),
        ("\x1bPq#0;2;0;0;0\x1b\\after", "after"),
        ("\x1b[2J\x1b[1;1Htop", "top"),
        ("\x1b7saved\x1b8\x1bc", "saved"),
        # The 8-bit forms.
        ("\x9b31mred\x9b0m", "red"),
        ("\x9d0;title\x9cbody", "body"),
        # An unterminated string sequence ends at its line, not the text.
        ("\x1b]0;no end\nLogin failed", "\nLogin failed"),
        # Any other control character.
        ("bell\x07 nul\x00 del\x7f back\x08", "bell nul del back"),
        (None, ""),
        (42, "42"),
    ],
)
def test_plain_text(raw, plain):
    assert plain_text(raw) == plain


def test_nothing_a_terminal_acts_on_is_left():
    every = "".join(chr(c) for c in range(0x00, 0xA0)) + "x"
    left = plain_text(every)
    assert all(ch in "\t\n" or 0x20 <= ord(ch) < 0x7F for ch in left), repr(left)


def test_a_banner_is_a_titled_block_that_ends_in_a_newline():
    block = banner(
        "Paste the code the sign-in page shows.", "USER INPUT REQUIRED", host="alpha", topic="Claude Code sign-in"
    )
    assert block == (
        "#" * 80 + "\n"
        "##  USER INPUT REQUIRED  -  alpha  -  Claude Code sign-in\n" + "#" * 80 + "\n"
        "Paste the code the sign-in page shows.\n"
    )


def test_a_banner_without_a_host_or_topic_names_the_title_alone():
    assert banner("x", "NEXT STEPS", width=30).splitlines()[1] == "##  NEXT STEPS"


def test_a_banner_never_wraps_its_body():
    # A URL or a command stays whole to copy; the terminal soft-wraps it.
    url = "https://claude.com/cai/oauth/authorize?" + "a" * 200
    block = banner(f"sign in at {url}", "ACTION REQUIRED IN YOUR BROWSER", width=40)
    assert f"sign in at {url}" in block.splitlines()


def test_a_list_is_a_line_per_item_and_nothing_is_an_empty_body():
    assert banner(["one", "two"], "NEXT STEPS", width=20).splitlines()[3:] == ["one", "two"]
    assert banner("", "X", width=20) == "#" * 20 + "\n##  X\n" + "#" * 20 + "\n"
    assert banner(None, "X", width=20) == banner("", "X", width=20)


@pytest.mark.parametrize(("width", "rule"), [(40, 40), (120, 120), (5, 20)])
def test_a_banner_is_as_wide_as_asked_but_never_narrower_than_twenty(width, rule):
    assert banner("x", "T", width=width).splitlines()[0] == "#" * rule


@pytest.mark.parametrize(("columns", "rule"), [("200", 80), ("60", 60)])
def test_a_banner_is_eighty_columns_or_the_terminal_when_it_is_narrower(monkeypatch, columns, rule):
    monkeypatch.setenv("COLUMNS", columns)
    assert banner("x", "T").splitlines()[0] == "#" * rule


def test_a_banner_lets_no_control_character_through():
    block = banner("\x1b]52;c;b3duZWQ=\x07body\x1b[31m", "TITLE\x07", host="al\x1b[2Jpha", width=20)
    assert block == "#" * 20 + "\n##  TITLE  -  alpha\n" + "#" * 20 + "\nbody\n"


def test_a_banners_frame_is_ascii():
    lines = banner("…é", "USER INPUT REQUIRED", host="alpha", topic="GitHub sign-in").splitlines()
    assert all(line.isascii() for line in lines[:3])
