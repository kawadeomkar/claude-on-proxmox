"""A live region at the bottom of a terminal, for a stdout callback: drawn in place, with Display's output above it.

Loaded by path by callback_plugins/claude_on_proxmox.py (`just deploy`'s live output, docs/architecture/
custom-code.md §7.5), as a callback loads a filter; it has no filters of its own, since a module in
callback_plugins/ that is no callback is warned about on every run. Nothing in it is deploy's: `just status`'s
live report is to draw with it too.

What it does, once started:

- Draws what its frame function returns at the bottom of the screen: one write per redraw, inside
  synchronized-output marks (CSI ?2026), the cursor moved up exactly the lines drawn, the region cut to the height,
  and ten times a second for a spinner. A frame's lines come already cut to the width, so none wraps.
- Wraps Display.display: anything printed erases the region first and redraws it after, so it stays above for
  good. A route function, given the call's keyword arguments, may send it to the log only (log_only), which leaves
  the screen alone.
- Wraps Display.prompt_until: a prompt prints with display() and only then takes Display's lock to read the keys
  (ansible-core 2.21), so the region is erased before it and drawn again only once it returns. The spinner takes
  the same lock, and draws nothing while a prompt is up.
- On a resize (SIGWINCH), which can wrap a frame worked out for the old size, pushes the screen into the
  scrollback and draws again from the top. The cursor is never hidden.
- Sets the terminal's title (OSC 0) when it changes, and rings the bell when asked.
- Erases the region at exit, Ctrl-C included.

Forked workers inherit the wrapped methods: only the process that started the region draws. Without a frame
function it draws nothing and only routes, for a run whose output is not a terminal.
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
import threading

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SYNC_ON, SYNC_OFF = "\x1b[?2026h", "\x1b[?2026l"
FRAME_SECONDS = 0.1


def can_draw(stream=None, environ=None) -> bool:
    """Whether a stream is a terminal that can take the region: one, not dumb, and UTF-8 for the glyphs."""
    stream = stream or sys.stdout
    environ = os.environ if environ is None else environ
    try:
        tty = stream.isatty()
    except (AttributeError, ValueError):
        tty = False
    encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
    return tty and environ.get("TERM", "") not in ("", "dumb") and encoding.startswith("utf8")


class LiveRegion:
    """The region. frame(cols, rows, glyph) returns its lines; route(kwargs) returns display()'s keyword arguments."""

    def __init__(self, display, frame=None, route=None, stream=None):
        self._display = display
        self._frame = frame
        self._route = route
        self._stream = stream or sys.stdout
        self._pid = os.getpid()
        self._drawn = 0
        self._size = None
        self._resized = False
        self._tick = 0
        self._paused = False
        self._started = False
        self._stop = threading.Event()
        self._thread = None
        self._title = None
        self.prompting = False

    # --- the screen -------------------------------------------------------------------------------------------
    def _write(self, text):
        self._stream.write(text)
        self._stream.flush()

    def _erase(self):
        if self._drawn:
            self._write((f"\x1b[{self._drawn - 1}A" if self._drawn > 1 else "") + "\r\x1b[J")
            self._drawn = 0

    def _terminal_size(self):
        return os.get_terminal_size(self._stream.fileno())

    def redraw(self):
        """Draw the frame again, in place. A no-op while stopped, paused for a prompt, or without a frame."""
        if not self._frame or self._stop.is_set() or self._paused or not self._started:
            return
        with self._display._lock:
            try:
                cols, rows = self._terminal_size()
            except (OSError, ValueError, AttributeError):
                return
            if self._resized or (self._size and self._size != (cols, rows)):
                # Everything on screen goes up into the scrollback, where nothing is lost, and the region is drawn
                # again from the top of an empty screen: no count of what was drawn before the resize holds.
                self._resized = False
                self._write("\n" * rows + f"\x1b[{rows - 1}A\r")
                self._drawn = 0
            self._size = (cols, rows)
            lines = self._frame(cols, rows, FRAMES[self._tick % len(FRAMES)])
            limit = max(3, rows - 1)
            if len(lines) > limit:
                lines = lines[: limit - 2] + [f"⋮ {len(lines) - limit + 1} more lines", lines[-1]]
            up = f"\x1b[{self._drawn - 1}A" if self._drawn > 1 else ""
            self._write(SYNC_ON + up + "\r" + "\x1b[K\n".join(lines) + "\x1b[K\x1b[J" + SYNC_OFF)
            self._drawn = len(lines)

    def title(self, text):
        """The terminal's title, written when it changes."""
        if self._frame and text != self._title and os.getpid() == self._pid:
            self._title = text
            with self._display._lock:
                self._write(f"\x1b]0;{text}\x07")

    def bell(self):
        if self._frame and os.getpid() == self._pid:
            with self._display._lock:
                self._write("\a")

    # --- the wrappers -----------------------------------------------------------------------------------------
    def _wrap(self):
        display = self._display
        original_display, original_prompt = display.display, display.prompt_until

        def wrapped_display(
            msg, color=None, stderr=False, screen_only=False, log_only=False, newline=True, caplevel=None
        ):
            kwargs = {"color": color, "stderr": stderr, "screen_only": screen_only, "log_only": log_only,
                      "newline": newline, "caplevel": caplevel}  # fmt: skip
            # A forked worker's display goes over the queue to this process, which draws.
            if os.getpid() != self._pid:
                return original_display(msg, **kwargs)
            # Routed even once the region is finished: what prints after it, a recap, follows the same rule.
            if self._route and not self.prompting:
                kwargs = self._route(kwargs)
            if kwargs.get("log_only") or not self._frame or self._stop.is_set():
                return original_display(msg, **kwargs)
            with display._lock:
                self._erase()
                original_display(msg, **kwargs)
                self.redraw()

        def wrapped_prompt(msg, *args, **kwargs):
            if os.getpid() != self._pid:
                return original_prompt(msg, *args, **kwargs)
            # The prompt prints through display(), and goes to the screen whatever the route says.
            with display._lock:
                self._erase()
                self._paused = True
                self.prompting = True
            try:
                return original_prompt(msg, *args, **kwargs)
            finally:
                with display._lock:
                    self._paused = False
                    self.prompting = False
                    self.redraw()

        display.display = wrapped_display
        display.prompt_until = wrapped_prompt

    def _spin(self):
        while not self._stop.wait(FRAME_SECONDS):
            with self._display._lock:
                self._tick += 1
                self.redraw()

    # --- the life of it ---------------------------------------------------------------------------------------
    def start(self):
        """Wrap Display, and, with a frame, draw: from the main thread, which alone may set a signal handler."""
        if self._started:
            return
        self._started = True
        self._wrap()
        if self._frame:
            signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "_resized", True))
            atexit.register(self.finish)
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
            self.redraw()

    def finish(self):
        """Erase the region and draw no more; Display's output goes on as it would without the region."""
        if self._started and not self._stop.is_set() and os.getpid() == self._pid:
            with self._display._lock:
                self._erase()
                self._stop.set()


class FilterModule:
    """No filters: a callback loads this module by path. Here so the filter loader takes the file as a plugin."""

    def filters(self):
        return {}
