"""Filters for text a VM printed, before a message on this machine quotes it.

The VM's user controls what claude, gh and ssh print there, and ansible-core
2.21 writes a failure's [ERROR] line to the terminal and the run log raw, so an
escape sequence quoted in a message would retitle the terminal, recolour it, or
write the clipboard (OSC 52). Removing only the control characters left the
sequences' payload behind as text: a base64 clipboard write, a window title.

banner draws the block that sets a moment the run waits for the person apart
from the task output around it: in a pause or vars_prompt prompt, which
ansible-core prints as written, and in the stdout callback
(callback_plugins/claude_on_proxmox.py), so both look the same.

one_line keeps a value a VM chose to one line of a report: `just status`
(filter_plugins/status.py) draws its report from them, and a newline in a
directory's name would otherwise start a line of its own - a forged item with a
command to run, here.
"""

from __future__ import annotations

import re
import shutil
import unicodedata

# Each escape sequence whole: a string sequence (OSC, DCS, SOS, PM, APC) up to
# its terminator, or the end of its line when it has none; a CSI with its
# parameters and final byte; any other ESC sequence; and the 8-bit forms.
_SEQUENCES = re.compile(
    r"(?:\x1b[\]PX^_]|[\x9d\x90\x98\x9e\x9f])[^\x07\x1b\x9c\n]*(?:\x07|\x1b\\|\x9c)?"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]?"
    r"|\x1b[ -/]*[0-~]?"
)
# What is left that a terminal acts on: every C0 and C1 control but tab and newline.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def plain_text(text):
    """Return ``text`` without escape sequences or control characters; tab and newline stay."""
    if text is None:
        return ""
    return _CONTROL.sub("", _SEQUENCES.sub("", str(text)))


# What one_line replaces with a space after plain_text: every control character
# left (tab, newline, carriage return), and Unicode's format, line and paragraph
# separators - the bidi overrides and isolates, zero-width characters - which can
# make a line read in an order other than its own.
_SPACE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
_SPACES = re.compile(r" {2,}")


def one_line(text, limit=120):
    """Return ``text`` as one line of plain text, at most ``limit`` characters, ending in "…" when cut.

    plain_text first, so whole escape sequences go; then each newline, carriage
    return, tab, and Unicode format or separator character becomes a space, and
    runs of spaces one. None is "".
    """
    if text is None:
        return ""
    cleaned = "".join(" " if unicodedata.category(ch) in _SPACE_CATEGORIES else ch for ch in plain_text(text))
    cleaned = _SPACES.sub(" ", cleaned).strip()
    limit = max(int(limit), 1)
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


# The block's width: these columns, or the terminal's when it is narrower,
# never less than the narrowest that still reads as a block.
_WIDTH = 80
_NARROWEST = 20


def banner(body, title, host="", topic="", width=None):
    """Return ``body`` under a ``#`` block titled ``title``, ending in a newline.

    The title line names ``host`` and ``topic`` after the title where given.
    Body lines are never wrapped, so a URL or a command in one stays whole to
    copy: the terminal soft-wraps it. A list body is one line per item. The
    block ends in a newline because a prompt's last word must start a line of
    its own, and a "\n" inside the Jinja of a folded YAML scalar reaches the
    screen as a backslash and an n. Every part goes through plain_text.
    """
    if width is None:
        width = min(_WIDTH, shutil.get_terminal_size((_WIDTH, 24)).columns)
    rule = "#" * max(int(width), _NARROWEST)
    # plain_text gives "" for None, so a part that is missing or cleans to nothing is left out.
    parts = (plain_text(part).strip() for part in (title, host, topic))
    heading = "  -  ".join(part for part in parts if part)
    lines = body if isinstance(body, (list, tuple)) else [body]
    text = "\n".join(plain_text(line) for line in lines if line is not None).strip("\n")
    return f"{rule}\n##  {heading}\n{rule}\n" + (f"{text}\n" if text else "")


class FilterModule:
    def filters(self):
        return {"plain_text": plain_text, "one_line": one_line, "banner": banner}
