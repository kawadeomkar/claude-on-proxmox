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

The live region is every live output's (filter_plugins/live_region.py, docs/architecture/custom-code.md §7.8): one
write per redraw in synchronized-output marks, nothing wrapped, a resize pushing the screen into the scrollback,
every other message erasing it first and drawing it after, the echo off while it is up, the cursor never hidden.
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

import importlib.util
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from ansible import constants as C
from ansible.plugins.callback import CallbackBase


def _sibling(stem):
    # filter_plugins/<stem>.py, loaded by path once as claude_on_proxmox_<stem>, since a callback cannot ask for a
    # filter module by name (docs/architecture/custom-code.md §7.8).
    name = f"claude_on_proxmox_{stem}"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "filter_plugins" / f"{stem}.py"
        spec = importlib.util.spec_from_file_location(name, path)
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


live = _sibling("live_region")


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "claude_on_proxmox_status"

    def __init__(self):
        super().__init__()
        self._status = _sibling("status")
        self._quiet = bool(os.environ.get("STATUS_QUIET"))
        self._live = live.can_draw() and not self._quiet
        self._found = {}
        self._started = time.monotonic()
        self._fleet_at = None
        self._done_at = {}
        self._reported = False
        self._failures = []
        # The region and the colour every live output shares (live_region.Output), the colour Ansible's own setting:
        # ANSIBLE_NOCOLOR, or NO_COLOR. Taken now, from the main thread, so a warning before the first frame is drawn
        # above it; spun from the playbook's start.
        more = "⋮ {} more lines, shown when the run ends"
        self._out = live.Output(
            self._display, frame=self._frame_lines, live=self._live, color=not C.ANSIBLE_NOCOLOR, more=more
        )
        self._color = self._out.color
        if self._live:
            self._out.start()

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

    # --- the live frame --------------------------------------------------------------------------------------
    def _frame_lines(self, cols, rows, glyph):
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
        reading = len(api) + len(ssh)
        self._out.title(f"status · reading {reading} VM{'' if reading == 1 else 's'}" if reading else "status")
        return text.split("\n") + [self._status.one_line(f"{glyph} {doing} · {elapsed}", cols - 1)]

    def _redraw(self):
        if self._live:
            self._out.redraw()

    def _finish(self):
        if self._live:
            self._out.title("status · done")
            self._out.finish()

    # --- Ansible's events --------------------------------------------------------------------------------------
    def v2_playbook_on_start(self, playbook):
        if self._live:
            self._out.spin()

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
                # The colours are for the screen: Display logs what it is given, so the log gets the plain text.
                coloured = self._status.status_text(model, True, reading=reading) if self._color else plain
                self._out.print([(coloured, plain)])

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
