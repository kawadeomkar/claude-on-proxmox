"""Enough of a VT100 to read back what `just status` drew at a terminal: test_status_live.py's screen.

Text wraps at the right edge with the pending-wrap rule terminals use; a line feed at the bottom row scrolls the
screen into the history (the scrollback); cursor up (CSI n A), carriage return, erase to the end of the line
(CSI K, CSI 2K) and erase below (CSI J) move and clear; colours are taken and ignored, and synchronized output
(CSI ?2026 h/l) marks what is drawn as a frame, where a line that wraps is counted (`frame_wraps`): the callback's
live region, which must never wrap. Any other sequence is recorded, so a test can say the callback wrote none. A
resize keeps lines as they are, as xterm does (no reflow), and a shorter window keeps its bottom rows, the rest going
to the history. Bytes arrive as a pseudo-terminal's reads cut them: a character or an escape sequence cut at the end
of one feed is kept until the next completes it, as a terminal does (on Linux a read once split a "→" in two).
"""

from __future__ import annotations

import re

CSI = re.compile(rb"\x1b\[([?0-9;]*)([A-Za-z])")
# An escape sequence not finished yet: what a read can end in.
CSI_PART = re.compile(rb"\x1b(\[[?0-9;]*)?\Z")


class VT:
    def __init__(self, cols: int, rows: int):
        self.cols, self.rows = cols, rows
        self.lines = [""] * rows
        self.history: list[str] = []
        self.r = self.c = 0
        self.wrap = False
        self.ups: list[int] = []
        self.unknown: set[str] = set()
        self.in_frame = False
        self.frame_wraps = 0
        self.pending = b""

    def resize(self, cols: int, rows: int) -> None:
        self.cols = cols
        while len(self.lines) > rows:
            if self.r > 0:
                self.history.append(self.lines.pop(0))
                self.r -= 1
            else:
                self.lines.pop()
        self.lines += [""] * (rows - len(self.lines))
        self.rows = rows

    def _line_feed(self) -> None:
        if self.r == self.rows - 1:
            self.history.append(self.lines.pop(0))
            self.lines.append("")
        else:
            self.r += 1

    def _put(self, ch: str) -> None:
        if self.wrap:
            self.c, self.wrap = 0, False
            self.frame_wraps += self.in_frame
            self._line_feed()
        line = self.lines[self.r].ljust(self.c)
        self.lines[self.r] = line[: self.c] + ch + line[self.c + 1 :]
        if self.c == self.cols - 1:
            self.wrap = True
        else:
            self.c += 1

    def feed(self, data: bytes) -> None:
        data, self.pending = self.pending + data, b""
        i = 0
        while i < len(data):
            if CSI_PART.match(data, i):
                self.pending = data[i:]
                return
            match = CSI.match(data, i)
            if match:
                arg, op = match.group(1).decode(), match.group(2).decode()
                if op == "A":
                    n = int(arg or 1)
                    self.ups.append(n)
                    self.r, self.wrap = max(0, self.r - n), False
                elif op == "K" and arg in ("", "0"):
                    self.lines[self.r] = self.lines[self.r][: self.c]
                elif op == "K" and arg == "2":
                    self.lines[self.r] = ""
                elif op == "J" and arg in ("", "0"):
                    self.lines[self.r] = self.lines[self.r][: self.c]
                    for k in range(self.r + 1, self.rows):
                        self.lines[k] = ""
                elif op in "hl" and arg == "?2026":
                    self.in_frame = op == "h"
                elif op == "m":
                    pass
                else:
                    self.unknown.add(match.group().decode())
                i = match.end()
                continue
            byte = data[i : i + 1]
            if byte == b"\r":
                self.c, self.wrap = 0, False
                i += 1
            elif byte == b"\n":
                self.wrap = False
                self._line_feed()
                i += 1
            elif byte == b"\x1b":
                self.unknown.add("ESC")
                i += 1
            else:
                lead = data[i]
                size = 4 if lead >= 0xF0 else 3 if lead >= 0xE0 else 2 if lead >= 0xC0 else 1
                if i + size > len(data):
                    self.pending = data[i:]
                    return
                self._put(data[i : i + size].decode("utf-8", "replace"))
                i += size

    def transcript(self) -> list[str]:
        """The scrollback and the screen, as lines, without the blank ones at the bottom."""
        out = self.history + self.lines
        while out and not out[-1].strip():
            out.pop()
        return out
