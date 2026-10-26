"""`just status`'s stdout callback: the report drawn live at a terminal, and printed whole when the run ends.

At a terminal, the whole report is on screen within a second of the start, at the bottom of it, with a spinner
wherever a value is still being read, and is redrawn as each piece lands (docs/architecture/lifecycle.md §4.6).
When the run ends, that live region is erased and the finished report printed once, through Display, so the run's
log has it. Piped, under --json, or without a terminal that can draw it, nothing is drawn: the finished report is
printed once at the end, or nothing at all under --json, whose report the recipe prints itself.

Every piece of the report arrives as a status_found fact, already one line of plain text a value; the report is
drawn by filter_plugins/status.py, the same functions as the finished report, loaded by path. Nothing of Ansible's
own task output is shown: a task that failed is said on stderr only if the run ends without a report, since the
report says what each failure meant, and an unreachable VM is the report's too. Task warnings, which since
ansible-core 2.19 travel on the task result, are printed in every hook, or a stdout callback of one's own drops them
without a word.

The live region (proven in the prototype docs/plan-status-2026-10-25-live-proto/ records): one write per redraw,
inside synchronized-output marks, the cursor moved up exactly the lines drawn, each line cut to the width so none
wraps, the region cut to the height. A resize can reach the terminal while a frame worked out for the old size is
on its way, and wrap it, after which no count of what is on screen holds: on SIGWINCH the screen is pushed up into
the scrollback, where nothing is lost, and the region drawn again from the top. The cursor is never hidden.
"""

from __future__ import annotations

DOCUMENTATION = """
    name: claude_on_proxmox_status
    type: stdout
    short_description: just status's report, drawn live at a terminal and printed whole when the run ends
    description:
      - Draws the report at the bottom of the terminal while the run reads, with a spinner wherever a value is still
        being read, and prints the finished report once when it ends. Piped, or with STATUS_QUIET set (just status
        --json), it draws nothing.
      - Says a task's failure on stderr only when the run ends without a report, and shows task warnings.
    requirements:
      - Chosen with ANSIBLE_STDOUT_CALLBACK=claude_on_proxmox_status, as just status does.
"""

import atexit
import importlib.util
import os
import signal
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ansible import constants as C
from ansible.plugins.callback import CallbackBase

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SYNC_ON, SYNC_OFF = "\x1b[?2026h", "\x1b[?2026l"
FRAME_SECONDS = 0.1


def _load_status():
    name = "claude_on_proxmox_status_filters"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "filter_plugins" / "status.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return sys.modules[name]


def _can_draw():
    """Whether stdout is a terminal that can take the live region: one, not dumb, and UTF-8 for the spinner."""
    stream = sys.stdout
    try:
        tty = stream.isatty()
    except (AttributeError, ValueError):
        tty = False
    encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
    return tty and os.environ.get("TERM", "") not in ("", "dumb") and encoding.startswith("utf8")


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "claude_on_proxmox_status"

    def __init__(self):
        super().__init__()
        self._status = _load_status()
        self._pid = os.getpid()
        self._quiet = bool(os.environ.get("STATUS_QUIET"))
        self._live = _can_draw() and not self._quiet
        # Ansible's own colour setting: ANSIBLE_NOCOLOR, or NO_COLOR.
        self._color = self._live and not C.ANSIBLE_NOCOLOR
        self._found = {}
        self._started = time.monotonic()
        self._fleet_at = None
        self._done_at = {}
        self._reported = False
        self._failures = []
        self._drawn = 0
        self._size = None
        self._resized = False
        self._frame = 0
        self._stop = threading.Event()
        self._thread = None
        if self._live:
            original = self._display.display

            def display(msg, *args, **kwargs):
                # Forked workers inherit this instance: only this process owns the terminal.
                if os.getpid() != self._pid:
                    return original(msg, *args, **kwargs)
                with self._display._lock:
                    self._erase()
                    original(msg, *args, **kwargs)
                    self._redraw()

            self._display.display = display
            signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "_resized", True))
            atexit.register(self._finish)

    # --- the report, from what has been found so far ------------------------------------------------------------
    def _now_said(self):
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z").strip()

    def _model(self, live):
        return self._status.status_report(dict(self._found), self._now_said(), time.time(), live=live)

    def _pending(self, model):
        api = [vm["name"] for vm in model["vms"] if vm.get("pending_api")]
        ssh = [vm["name"] for vm in model["vms"] if vm.get("pending_ssh")]
        return api, ssh

    def _reading(self, model):
        """The line on what was read, and how long it took: proxmox, then the VMs."""
        seconds = time.monotonic() - self._started
        parts = [f"read in {seconds:.1f} s"]
        proxmox = model["proxmox"]
        if proxmox.get("off"):
            parts.append("proxmox not asked (claude_vm_discovery is off)")
        elif proxmox.get("unread"):
            parts.append("proxmox did not answer")
        elif self._fleet_at is not None:
            parts.append(f"proxmox {self._fleet_at:.1f} s")
        read = [vm for vm in model["vms"] if vm.get("read")]
        if read:
            done = [vm for vm in read if vm["name"] in self._done_at]
            last = max((self._done_at[vm["name"]] for vm in done), default=0.0)
            parts.append(f"vms {len(done)} of {len(read)} {last:.1f} s")
        return " · ".join(parts)

    # --- the live region -------------------------------------------------------------------------------------
    def _write(self, text):
        sys.stdout.write(text)
        sys.stdout.flush()

    @staticmethod
    @contextmanager
    def _uninterrupted():
        """Ctrl-C held off in the main thread for one write to the terminal and the count of the lines it drew.

        A KeyboardInterrupt between the two left the count stale: the erase then missed the region, which stayed
        on the screen above the run's last words. The signal is delivered as soon as the count is right again.
        """
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        held = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
        try:
            yield
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, held)

    def _erase(self):
        if self._drawn:
            with self._uninterrupted():
                self._write((f"\x1b[{self._drawn - 1}A" if self._drawn > 1 else "") + "\r\x1b[J")
                self._drawn = 0

    def _frame_lines(self, cols):
        glyph = FRAMES[self._frame % len(FRAMES)]
        elapsed = f"{time.monotonic() - self._started:.1f} s"
        if "localhost" not in self._found:
            return [f"{glyph} listing the VMs · {elapsed}"]
        model = self._model(live=True)
        text = self._status.status_text(model, self._color, glyph=glyph, cols=cols - 1)
        api, ssh = self._pending(model)
        if api:
            doing = f"reading {', '.join(api)}"
        elif ssh:
            doing = f"reading {', '.join(ssh)} over SSH"
        else:
            doing = "ranking what needs attention"
        return text.split("\n") + [self._status.one_line(f"{glyph} {doing} · {elapsed}", cols - 1)]

    def _terminal_size(self):
        return os.get_terminal_size(sys.stdout.fileno())

    def _redraw(self):
        if not self._live or self._stop.is_set():
            return
        try:
            cols, rows = self._terminal_size()
        except (OSError, ValueError, AttributeError):
            return
        if self._resized or (self._size and self._size != (cols, rows)):
            # Push everything on screen up into the scrollback, where nothing is lost, and draw again from the
            # top of an empty screen: no count of what was drawn before the resize holds.
            self._resized = False
            self._write("\n" * rows + f"\x1b[{rows - 1}A\r")
            self._drawn = 0
        self._size = (cols, rows)
        lines = self._frame_lines(cols)
        limit = max(3, rows - 1)
        if len(lines) > limit:
            hidden = len(lines) - limit + 1
            more = self._status.one_line(f"⋮ {hidden} more lines, shown when the run ends", cols - 1)
            lines = lines[: limit - 2] + [more, lines[-1]]
        with self._uninterrupted():
            up = f"\x1b[{self._drawn - 1}A" if self._drawn > 1 else ""
            self._write(SYNC_ON + up + "\r" + "\x1b[K\n".join(lines) + "\x1b[K\x1b[J" + SYNC_OFF)
            self._drawn = len(lines)

    def _spin(self):
        while not self._stop.wait(FRAME_SECONDS):
            with self._display._lock:
                self._frame += 1
                self._redraw()

    def _finish(self):
        if self._live and not self._stop.is_set():
            with self._display._lock:
                self._erase()
                self._stop.set()

    # --- Ansible's events --------------------------------------------------------------------------------------
    def v2_playbook_on_start(self, playbook):
        if self._live and not self._thread:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()

    def v2_runner_on_ok(self, result):
        self._handle_warnings_and_exception(result)
        facts = result.result.get("ansible_facts") or {}
        host = result.host.get_name()
        if isinstance(facts.get("status_found"), dict):
            with self._display._lock:
                self._found[host] = facts["status_found"]
                now = time.monotonic() - self._started
                if host == "localhost" and self._fleet_at is None:
                    self._fleet_at = now
                elif host != "localhost":
                    self._done_at[host] = now
                self._redraw()
        if isinstance(facts.get("status_report"), dict):
            self._finish()
            self._reported = True
            if not self._quiet:
                model = facts["status_report"]
                reading = self._reading(model)
                plain = self._status.status_text(model, False, reading=reading)
                if self._color:
                    # The colours are for the screen: Display logs what it is given, so the log gets the plain text.
                    self._display.display(self._status.status_text(model, True, reading=reading), screen_only=True)
                    self._display.display(plain, log_only=True)
                else:
                    self._display.display(plain)

    # A failure or an unreachable VM is the report's to say - a rescue records it, or the ping's register does - so
    # neither prints Ansible's error: only its warnings, and its message at the end of a run that left no report.
    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._handle_warnings(result.result)
        if not ignore_errors:
            message = result.result.get("msg") or result.result.get("stderr") or "failed"
            self._failures.append(f"{result.host.get_name()}: {result.task.get_name()}: {message}")

    def v2_runner_on_unreachable(self, result):
        self._handle_warnings(result.result)

    def v2_runner_on_skipped(self, result):
        pass

    def v2_playbook_on_stats(self, stats):
        self._finish()
        if not self._reported:
            for failure in self._failures:
                self._display.display(self._status.one_line(failure, 2000), stderr=True)
