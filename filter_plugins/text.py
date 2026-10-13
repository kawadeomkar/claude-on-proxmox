"""Filters for text a VM printed, before a message on this machine quotes it.

The VM's user controls what claude, gh and ssh print there, and ansible-core
2.21 writes a failure's [ERROR] line to the terminal and the run log raw, so an
escape sequence quoted in a message would retitle the terminal, recolour it, or
write the clipboard (OSC 52). Removing only the control characters left the
sequences' payload behind as text: a base64 clipboard write, a window title.
"""

from __future__ import annotations

import re

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


class FilterModule:
    def filters(self):
        return {"plain_text": plain_text}
