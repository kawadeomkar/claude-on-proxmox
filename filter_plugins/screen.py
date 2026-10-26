"""What every live output draws with: lines of styled text, cut and padded to a width, rendered in one palette.

A line is a list of segments, (text, style): a style is one of PALETTE's keys, or "" for plain. `just status`'s
report (filter_plugins/status.py) and every live run (filter_plugins/run_view.py) are built as lines and rendered
here, so they cut, pad, colour and measure alike; filter_plugins/live_region.py puts them on the screen. PENDING in a
segment's text marks where the spinner's glyph goes, for a value still being read.

A line is measured in characters: one each, which East Asian ambiguous-width characters (█ ● ○ – · …) are not in a
terminal set to draw them double (docs/architecture/custom-code.md §7.8).

Pure, nothing read or written. Loaded by path as claude_on_proxmox_screen, as every module here loads another; it
has no filters of its own.
"""

from __future__ import annotations

import textwrap

# The palette, as ANSI codes: headings, bold, dim, and the colours of done, to do, failed, running and waiting.
PALETTE = {"h": "1;36", "b": "1", "d": "2", "g": "32", "y": "33", "r": "31", "c": "36", "m": "35"}
# Where a value still being read is drawn: the spinner's glyph in a live frame, "·" in a still one.
PENDING = "\x00"
SPARKS = "▁▂▃▄▅▆▇█"


def seg(text, style=""):
    return (str(text), style)


def plain(line):
    return "".join(text for text, _ in line)


def width(line):
    return len(plain(line))


def pad(line, cols):
    """line, padded with spaces to cols characters."""
    return line + [seg(" " * max(0, cols - width(line)))]


def cut(line, cols):
    """line cut to cols characters, ending in a dim "…" when cut: a live line that wrapped would break the count of
    lines drawn. cols None leaves it whole."""
    if cols is None or width(line) <= cols:
        return line
    out, used = [], 0
    for text, style in line:
        room = cols - 1 - used
        if room <= 0:
            break
        out.append((text[:room], style))
        used += len(text[:room])
    out.append(("…", "d"))
    return out


def right(left, tail, cols):
    """left, then tail aligned to the right edge at cols; without room for both, tail is dropped and left cut."""
    gap = cols - width(left) - width(tail)
    if tail and gap >= 1:
        return [*left, seg(" " * gap), *tail]
    return cut(left, cols)


def render(line, color, glyph=None):
    """line as text: in the palette's colours, or plain; PENDING as the glyph given, else "·"."""

    def piece(text, style):
        text = text.replace(PENDING, glyph or "·")
        if color and style and text:
            return f"\x1b[{PALETTE[style]}m{text}\x1b[0m"
        return text

    return "".join(piece(text, style) for text, style in line).rstrip()


def bar(fraction, cells=12, style=None):
    """A bar of cells: filled in style, or by how full - green, yellow from 80%, red from 90% - and dim the rest."""
    filled = max(0, min(cells, round(fraction * cells)))
    style = style or ("r" if fraction >= 0.9 else "y" if fraction >= 0.8 else "g")
    return [seg("█" * filled, style), seg("░" * (cells - filled), "d")]


def spark(values):
    """A sparkline of fractions, a space for each None."""
    out = []
    for value in values or []:
        out.append(" " if value is None else SPARKS[min(len(SPARKS) - 1, max(0, round(value * (len(SPARKS) - 1))))])
    return "".join(out)


def fill(text, cols):
    """text wrapped to cols, nothing cut: a word longer than a line, a URL, has a line of its own."""
    return textwrap.wrap(str(text), max(1, cols), break_long_words=False, break_on_hyphens=False) or [""]


def plural(count, word, words=None):
    return f"{count} {word if count == 1 else words or word + 's'}"


def duration(seconds):
    """A duration as a report says it: 0.6 s, 33 s, 3:58."""
    if seconds < 10:
        return f"{seconds:.1f} s"
    if seconds < 60:
        return f"{round(seconds)} s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}:{rest:02d}"


def clock(seconds):
    """A running timer: 0:07, 12:30."""
    minutes, rest = divmod(int(max(0, seconds)), 60)
    return f"{minutes}:{rest:02d}"


class FilterModule:
    """No filters: loaded by path. Here so the filter loader takes the file as a plugin."""

    def filters(self):
        return {}
