"""plain_text: what a VM printed, safe to quote in a message on this machine."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("text_filters", REPO / "filter_plugins" / "text.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
plain_text = _module.plain_text


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
