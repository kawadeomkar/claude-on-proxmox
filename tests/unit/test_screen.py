"""What every live output draws with: filter_plugins/screen.py's lines of styled segments.

`just status`'s report and every live run cut, pad, align, colour and measure their lines here, so each rule is
checked once: a cut line ends in a dim "…" at exactly the width, a tail is dropped before it would crowd the line,
colour is the palette's or none and PENDING becomes the spinner's glyph, a bar fills by its fraction, and a
duration reads as the reports say it (docs/architecture/custom-code.md §7.8).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("test_screen_module", REPO / "filter_plugins" / "screen.py")
screen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(screen)


def test_a_line_is_cut_to_its_width_with_a_dim_ellipsis_and_left_whole_when_it_fits():
    line = [("abcdef", "b"), ("gh", "d")]
    assert screen.cut(line, 4) == [("abc", "b"), ("…", "d")]
    assert screen.width(screen.cut(line, 4)) == 4
    assert screen.cut(line, 8) == line and screen.cut(line, None) == line
    assert screen.cut([("ab", ""), ("cd", "y")], 3) == [("ab", ""), ("…", "d")]


def test_a_tail_is_aligned_to_the_edge_or_dropped_before_it_crowds_the_line():
    assert screen.right([("left", "")], [("tail", "d")], 12) == [("left", ""), ("    ", ""), ("tail", "d")]
    assert screen.right([("left side", "")], [("tail", "")], 12) == [("left side", "")]
    assert screen.right([("a long left side", "")], [("tail", "")], 8) == [("a long ", ""), ("…", "d")]
    assert screen.pad([("ab", "")], 5) == [("ab", ""), ("   ", "")]


def test_a_line_renders_in_the_palette_or_plain_with_the_spinner_where_a_value_is_pending():
    line = [("VMS", "h"), (" alpha ", ""), (screen.PENDING, "c"), ("  ", "")]
    assert screen.render(line, False) == "VMS alpha ·"
    assert screen.render(line, False, glyph="⠋") == "VMS alpha ⠋"
    assert screen.render(line, True, glyph="⠋") == "\x1b[1;36mVMS\x1b[0m alpha \x1b[36m⠋\x1b[0m"
    assert set(screen.PALETTE) >= {"h", "b", "d", "g", "y", "r", "c", "m"}


@pytest.mark.parametrize(
    ("fraction", "style", "filled", "colour"),
    [(0.5, None, 6, "g"), (0.85, None, 10, "y"), (0.95, None, 11, "r"), (1.4, None, 12, "r"), (0.5, "c", 6, "c")],
)
def test_a_bar_fills_by_its_fraction_in_its_style_or_by_how_full(fraction, style, filled, colour):
    bar = screen.bar(fraction, 12, style)
    assert bar == [("█" * filled, colour), ("░" * (12 - filled), "d")]


def test_a_sparkline_has_a_mark_per_value_and_a_space_for_none():
    assert screen.spark([0, 0.5, 1, None]) == "▁▅█ " and screen.spark([]) == ""


@pytest.mark.parametrize(
    ("seconds", "said"), [(0.04, "0.0 s"), (0.6, "0.6 s"), (9.94, "9.9 s"), (33.4, "33 s"), (238, "3:58")]
)
def test_a_duration_reads_as_the_reports_say_it(seconds, said):
    assert screen.duration(seconds) == said


def test_a_clock_counts_minutes_and_seconds_from_zero():
    assert [screen.clock(s) for s in (-3, 7, 750)] == ["0:00", "0:07", "12:30"]


def test_a_count_is_said_with_its_word():
    assert screen.plural(1, "task") == "1 task" and screen.plural(0, "task") == "0 tasks"
    assert screen.plural(2, "VM is", "VMs are") == "2 VMs are"


def test_fill_wraps_and_never_cuts():
    text = "Remote Control is live for alpha. Open https://claude.ai/code/a-very-long-path-that-will-not-fit there."
    lines = screen.fill(text, 30)
    assert " ".join(lines) == text and "…" not in "".join(lines)
    assert all(len(line) <= 30 for line in lines if " " in line)
