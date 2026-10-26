"""The default stdout callback, with the moments a run waits for the person set apart, and just deploy's live output.

A debug task tagged attention_browser - the page to approve in the browser -
is drawn as a block titled ACTION REQUIRED IN YOUR BROWSER instead of an
"ok: [host] =>" and a YAML dump, and a retried task with that tag says once
that it is waiting instead of a FAILED - RETRYING line every few seconds.
After PLAY RECAP it prints NEXT STEPS: what the report tasks recorded with
set_stats as still left to do, per VM and matter, the last record of each
winning (docs/architecture/custom-code.md §7.5).

Everything else is the default callback's, options included: a subclass
without DOCUMENTATION has no options, every hook then fails on its first
get_option, and Ansible only warns while each task's result disappears. Only
ansible.cfg wires this in; the tests and Molecule run their own callbacks.

just deploy sets CLAUDE_ON_PROXMOX_LIVE to what it runs (deploy alpha, say), and with it, at verbosity 0, the run
is drawn rather than streamed (docs/guides/runs.md):

- live, at a terminal that can draw (filter_plugins/live_region.py): the stages, the running stage's steps and the
  running step's last five tasks, redrawn in place at the bottom of the screen, with the reports, the blocks, the
  diffs, warnings and failures printed above it for good;
- lines, any other output: a line as each step ends, and the same reports, blocks and failures.

Either way the default callback still renders every task, sent to the run's log alone (display's log_only), so the
log is what it would be without them; the run's own words go to the screen in colour and to the log without; and a
summary replaces PLAY RECAP on the screen, which the log keeps, followed by NEXT STEPS. Prompts print whole, the
region stepping aside (live_region). Any -v, or ANSIBLE_STDOUT_CALLBACK=default, is the stream as before.
"""

from __future__ import annotations

DOCUMENTATION = """
    name: claude_on_proxmox
    type: stdout
    short_description: the default output, with banners for the waits and NEXT STEPS at the end
    description:
      - The default callback, drawing debug results tagged attention_browser as a block, saying once that a
        retried task with that tag is waiting, and printing what set_stats recorded as left to do after the
        play recap.
      - With CLAUDE_ON_PROXMOX_LIVE set, as just deploy sets it, and at verbosity 0, the run drawn live at a
        terminal, or a line per step otherwise, every task's output in the log alone.
    extends_documentation_fragment:
      - default_callback
      - result_format_callback
    requirements:
      - set as stdout_callback in ansible.cfg
"""

import atexit
import importlib.util
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from ansible import constants as C
from ansible import context
from ansible.plugins.callback.default import CallbackModule as DefaultCallback

FILTERS = Path(__file__).resolve().parents[1] / "filter_plugins"


def _load(module, name):
    # A filter module, loaded by path, since a callback cannot ask for one by name.
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, FILTERS / f"{module}.py")
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        sys.modules[name] = loaded
    return sys.modules[name]


def _load_banner():
    # The filter the prompts use, so a prompt's block and a message's look the same.
    return _load("text", "claude_on_proxmox_text_filters").banner


banner = _load_banner()
plain_text = _load("text", "claude_on_proxmox_text_filters").plain_text
view = _load("deploy_view", "claude_on_proxmox_deploy_view")
live = _load("live_region", "claude_on_proxmox_live_region")

WAITING_TAG = "attention_browser"
DEBUG_ACTIONS = frozenset({"debug", "ansible.builtin.debug", "ansible.legacy.debug"})
# A wait's block names the sign-in it belongs to, by the role the task is in.
TOPICS = {
    "claude_code": "Claude Code sign-in",
    "github_projects": "GitHub sign-in",
    "tailscale": "Tailscale sign-in",
}
# The set_stats names the report tasks record under, each owner's own prefix,
# and the order a VM's matters are listed in.
NEXT_STATS = ("claude_vm_next", "claude_code_next", "github_projects_next", "tailscale_next", "code_next")
MATTERS = ("vm", "claude", "remote_control", "github", "github_identity", "tailscale", "vscode", "destroy")
# Bright, so the blocks stand out from ok's green and changed's yellow.
COLOR = "bright yellow"
# The facts the provision stage says a VM by.
VM_FACTS = ("proxmox_vm_resolved_id", "proxmox_vm_address", "proxmox_vm_exists")
LIVE_ENV = "CLAUDE_ON_PROXMOX_LIVE"
FAILURE_LINES = 20


def next_steps(run_stats):
    """The lines left to do, from the run's custom stats: by VM name, then matter, each text once."""
    records = {}
    for stat in NEXT_STATS:
        for vm, matters in (run_stats.get(stat) or {}).items():
            if isinstance(matters, dict):
                records.setdefault(str(vm), {}).update(matters)
    rank = {matter: index for index, matter in enumerate(MATTERS)}
    lines = []
    for vm in sorted(records):
        for matter in sorted(records[vm], key=lambda m: (rank.get(m, len(MATTERS)), m)):
            line = records[vm][matter]
            if isinstance(line, str) and line.strip() and line not in lines:
                lines.append(line)
    return lines


def _tasks_of(blocks, out):
    """Each task a play compiled to, as (its ID, its role's name or None), blocks' rescue and always included."""
    for block in blocks:
        for part in (block.block, block.rescue, block.always):
            for item in part or []:
                if hasattr(item, "block"):
                    _tasks_of([item], out)
                elif not getattr(item, "implicit", False):
                    out.append((item._uuid, _role(item)))
    return out


def _role(task):
    return task._role.get_name(include_role_fqcn=False) if getattr(task, "_role", None) else None


def _rescued(task):
    """Whether a block around the task has a rescue: its failure is that block's to handle, not the run's."""
    parent = getattr(task, "_parent", None)
    while parent is not None:
        if getattr(parent, "rescue", None):
            return True
        parent = getattr(parent, "_parent", None)
    return False


class CallbackModule(DefaultCallback):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "claude_on_proxmox"

    def __init__(self):
        super().__init__()
        self._waiting = set()
        # classic, live or lines: decided when the playbook starts, once the verbosity is known.
        self._mode = "classic"
        self._run = None
        self._region = None
        self._plan_play = None
        self._speaking = threading.local()
        self._color = False

    # --- the classic hooks, as before --------------------------------------------------------------------------
    # Every block is banner's own width - 80 columns, or the terminal's when
    # narrower - the same as the prompts', which are drawn before any callback.

    def _banner_or_default(self, result):
        msg = result.result.get("msg")
        if WAITING_TAG in result.task.tags and result.task.action in DEBUG_ACTIONS and isinstance(msg, str):
            if self._last_task_banner != result.task._uuid:
                self._print_task_banner(result.task)
            name = result.task.get_name()
            role = name.split(" : ", 1)[0] if " : " in name else ""
            self._display.display(
                banner(
                    msg,
                    "ACTION REQUIRED IN YOUR BROWSER",
                    host=result.host.get_name(),
                    topic=TOPICS.get(role, ""),
                ),
                color=COLOR,
            )
            return True
        return False

    def _retry_once(self, result):
        # One line per wait and host, not one per retry: the instruction above
        # it stays on the screen.
        key = (result.task._uuid, result.host.get_name())
        if key in self._waiting:
            return
        self._waiting.add(key)
        left = result.result.get("retries", 0) - result.result.get("attempts", 0)
        try:
            seconds = left * int(result.task.delay)
        except (TypeError, ValueError):
            seconds = 0
        said = (
            f"about {round(seconds / 60)} minutes"
            if seconds >= 120
            else f"about {seconds} seconds"
            if seconds > 0
            else f"{left} more checks"
        )
        self._display.display(
            f"Waiting for you in the browser for {result.host.get_name()} ({said} left; Ctrl-C ends the run) ...",
            color=COLOR,
        )

    # --- the drawn modes: what the run says, and where it goes ---------------------------------------------------
    @contextmanager
    def _say(self):
        """What is printed inside goes to the screen, where the route would send it to the log alone."""
        previous = getattr(self._speaking, "on", False)
        self._speaking.on = True
        try:
            yield
        finally:
            self._speaking.on = previous

    def _route(self, kwargs):
        # The run's own words and anything on stderr - warnings, deprecations, errors - stay on the screen; the
        # rest, the default callback's every task and profile_tasks' timings, goes to the log alone.
        if getattr(self._speaking, "on", False) or kwargs.get("stderr"):
            return kwargs
        return {**kwargs, "log_only": True}

    def _print(self, lines):
        """Lines of the run's own, (coloured, plain) each: the colour to the screen, the plain text to the log."""
        with self._say():
            for coloured, plain in lines:
                self._display.display(coloured, screen_only=True)
                self._display.display(plain, log_only=True)

    def _lines(self, runs_list):
        width = self._width()
        return [
            (view.ansi(view.cut(runs, width - 1), self._color), view.ansi(view.cut(runs, width - 1), False))
            for runs in runs_list
        ]

    def _width(self):
        try:
            return os.get_terminal_size(sys.stdout.fileno()).columns
        except (OSError, ValueError, AttributeError):
            return 100

    def _now(self):
        return time.monotonic()

    def _frame(self, cols, rows, glyph):
        with self._display._lock:
            if self._run is None:
                return []
            self._region.title(self._run.title_text(self._now()))
            return self._run.frame(self._now(), cols, rows, glyph, self._color)

    def _drawn(self):
        return self._mode in ("live", "lines")

    def _after(self):
        """After each event: in lines mode, a line for each step that ended; live, a redraw now."""
        if self._mode == "lines":
            ended = self._run.ended_lines(self._now())
            if ended:
                self._print([(line, line) for line in ended])
        elif self._mode == "live":
            self._region.redraw()

    def _log_path(self):
        path = os.environ.get("ANSIBLE_LOG_PATH", "")
        if not path:
            return ""
        try:
            return os.path.relpath(path) if not os.path.relpath(path).startswith("..") else path
        except ValueError:
            return path

    # --- the playbook -----------------------------------------------------------------------------------------
    def v2_playbook_on_start(self, playbook):
        title = os.environ.get(LIVE_ENV, "").strip()
        if title and self._display.verbosity == 0:
            self._mode = "live" if live.can_draw() else "lines"
            self._color = self._mode == "live" and not C.ANSIBLE_NOCOLOR
            check = bool((context.CLIARGS or {}).get("check"))
            self._run = view.Run(title, self._now(), check=check, log=self._log_path())
            plays = []
            for play in playbook.get_plays():
                try:
                    tasks = _tasks_of(play.compile(), [])
                except Exception:  # noqa: BLE001 - a count is a nicety: a play that will not compile here still runs
                    tasks = []
                plays.append((play._uuid, play.get_path(), tasks))
            self._run.plays(plays)
            first = playbook.get_plays()[:1]
            if first and Path(str(first[0].get_path()).split(":", 1)[0]).name == "deploy.yml":
                self._plan_play = first[0]._uuid
            self._region = live.LiveRegion(
                self._display, frame=self._frame if self._mode == "live" else None, route=self._route
            )
            self._region.start()
            if self._mode == "live":
                # Registered after the region's own, so it runs first: a run that ends without its stats, at Ctrl-C,
                # does not leave the tab saying it is still at work.
                atexit.register(self._stopped_title)
        super().v2_playbook_on_start(playbook)

    def _stopped_title(self):
        if self._run.finished is None:
            self._region.title(f"{self._run.title} · stopped")

    def v2_playbook_on_play_start(self, play):
        if self._drawn():
            with self._display._lock:
                self._run.play_start(play._uuid, play.get_name(), play.get_path())
        super().v2_playbook_on_play_start(play)

    def _live_task_start(self, task):
        if self._drawn():
            with self._display._lock:
                name = task.get_name().strip()
                role = _role(task)
                if role and name.startswith(role + " : "):
                    name = name[len(role) + 3 :]
                self._run.task(task._uuid, name, role, self._now())
                self._after()

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._live_task_start(task)
        super().v2_playbook_on_task_start(task, is_conditional)

    def v2_playbook_on_handler_task_start(self, task):
        self._live_task_start(task)
        super().v2_playbook_on_handler_task_start(task)

    def v2_playbook_on_include(self, included_file):
        if self._drawn():
            with self._display._lock:
                task = included_file._task
                if getattr(included_file, "_is_role", False):
                    role = getattr(task, "_role_name", None) or str(included_file._filename)
                    search = [os.path.join(os.getcwd(), "roles"), *C.DEFAULT_ROLES_PATH]
                    tasks_from = (getattr(task, "_from_files", None) or {}).get("tasks", "main")
                    path = view.role_tasks_file(role, tasks_from, search)
                else:
                    role, path = _role(task), str(included_file._filename)
                self._run.include(role, view.count_tasks(path) if path else 0)
        super().v2_playbook_on_include(included_file)

    # --- results ----------------------------------------------------------------------------------------------
    def v2_runner_on_start(self, host, task):
        if self._drawn():
            with self._display._lock:
                self._run.host_start(task._uuid, host.get_name(), self._now())
        super().v2_runner_on_start(host, task)

    def _result(self, result, outcome):
        with self._display._lock:
            self._run.result(result.task._uuid, result.host.get_name(), outcome, self._now())
            self._after()

    def v2_runner_on_ok(self, result):
        if not self._drawn():
            if not self._banner_or_default(result):
                super().v2_runner_on_ok(result)
            return
        self._result(result, "changed" if result.result.get("changed") else "ok")
        host = result.host.get_name()
        facts = result.result.get("ansible_facts") or {}
        with self._display._lock:
            for name in VM_FACTS:
                if name in facts:
                    self._run.fact(host, name, facts[name])
        msg = result.result.get("msg")
        if WAITING_TAG in result.task.tags and result.task.action in DEBUG_ACTIONS and isinstance(msg, str):
            with self._say():
                self._banner_or_default(result)
            return
        # A looped report's words are its items'; its own msg is the loop's "All items completed".
        if result.task.action in DEBUG_ACTIONS and "results" not in result.result:
            self._report(result)
        super().v2_runner_on_ok(result)

    def v2_runner_item_on_ok(self, result):
        if self._drawn() and result.task.action in DEBUG_ACTIONS:
            self._report(result)
        super().v2_runner_item_on_ok(result)

    def _report(self, result):
        """A debug message, a report: a line above the region, and the summary's, under its VM."""
        msg = result.result.get("msg")
        if msg in (None, ""):
            return
        host = result.host.get_name()
        text = msg if isinstance(msg, str) else " ".join(map(str, msg)) if isinstance(msg, list) else str(msg)
        plan = self._plan_play is not None and self._run.play == self._plan_play
        with self._display._lock:
            self._run.note(host, plain_text(text), plan=plan)
        if not plan or self._mode == "lines":
            # A play on this machine names the VM in its words; anywhere else the VM leads, once.
            said = view.one_line(plain_text(text))
            if said.startswith(host + ": "):
                said = said[len(host) + 2 :]
            who = [] if host == "localhost" or said.startswith(host + " ") else [(host, "b"), ("  ", "")]
            self._print(self._lines([[("  ", ""), *who, (said, "")]]))

    def v2_runner_on_skipped(self, result):
        if self._drawn():
            self._result(result, "skipped")
        super().v2_runner_on_skipped(result)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        if not self._drawn():
            super().v2_runner_on_failed(result, ignore_errors)
            return
        outcome = "ignored" if ignore_errors else "rescued" if _rescued(result.task) else "failed"
        self._result(result, outcome)
        if outcome == "failed":
            self._failure(result, "failed")
        super().v2_runner_on_failed(result, ignore_errors)

    def v2_runner_on_unreachable(self, result):
        if not self._drawn():
            super().v2_runner_on_unreachable(result)
            return
        ignored = bool(getattr(result.task, "ignore_unreachable", False))
        self._result(result, "ignored" if ignored else "unreachable")
        if not ignored:
            self._failure(result, "could not be reached")
        super().v2_runner_on_unreachable(result)

    def _failure(self, result, how):
        """A failed task, for good above the region: the task, the VM, what it said, and where the log has it."""
        with self._display._lock:
            task = self._run.find(result.task._uuid)
            where = (task.step or task.stage) if task is not None else ""
            took = f" ({view.fmt(task.seconds(self._now()))})" if task is not None else ""
            name = task.name if task is not None else result.task.get_name()
        said = []
        for key in ("msg", "reason"):
            if result.result.get(key):
                said += plain_text(str(result.result[key])).splitlines()
        for key in ("stderr_lines", "stdout_lines"):
            if result.result.get(key):
                said += [plain_text(str(line)) for line in result.result[key]][-FAILURE_LINES:]
                break
        said_how = "" if how == "failed" else f" · {how}"
        lines = [[("✗ ", "r"), (f"{where} on {result.host.get_name()}: {name}{took}{said_how}", "r")]]
        lines += [[("    ", ""), (line, "")] for line in said[:FAILURE_LINES] if line.strip()]
        if self._run.log:
            lines.append([("    ", ""), (f"the whole output: {self._run.log}", "d")])
        self._print(self._lines([[], *lines, []]))

    def v2_runner_retry(self, result):
        if not self._drawn():
            if WAITING_TAG not in result.task.tags:
                super().v2_runner_retry(result)
            else:
                self._retry_once(result)
            return
        if WAITING_TAG in result.task.tags:
            left = result.result.get("retries", 0) - result.result.get("attempts", 0) + 1
            try:
                seconds = left * int(result.task.delay)
            except (TypeError, ValueError):
                seconds = 0
            with self._display._lock:
                first = self._run.retry(result.task._uuid, result.host.get_name(), self._now(), seconds)
            if first and self._mode == "live":
                self._region.bell()
            if self._mode == "lines":
                with self._say():
                    self._retry_once(result)
            else:
                self._retry_once(result)
            self._after()
            return
        super().v2_runner_retry(result)

    def v2_on_file_diff(self, result):
        # A dry run is read for its diffs: they go above the region, to the screen and the log.
        if self._drawn():
            with self._say():
                super().v2_on_file_diff(result)
            return
        super().v2_on_file_diff(result)

    # --- the end ----------------------------------------------------------------------------------------------
    def v2_playbook_on_stats(self, stats):
        if not self._drawn():
            super().v2_playbook_on_stats(stats)
            lines = next_steps(stats.custom.get("_run", {}))
            if lines:
                self._display.display(banner(lines, "NEXT STEPS"), color=COLOR)
            return
        now = self._now()
        with self._display._lock:
            self._run.end(now)
            if self._mode == "live":
                self._region.title(self._run.title_text(now))
        if self._mode == "lines":
            self._after()
        self._region.finish()
        # PLAY RECAP, for the log alone; the summary is what the screen gets.
        DefaultCallback.v2_playbook_on_stats(self, stats)
        failed = sorted(set(stats.failures) | set(stats.dark))
        width = self._width()
        summary = self._run.summary(now, width, self._color, failed_hosts=failed)
        plain = self._run.summary(now, width, False, failed_hosts=failed)
        self._print([("", ""), *zip(summary, plain, strict=True)])
        lines = next_steps(stats.custom.get("_run", {}))
        if lines:
            with self._say():
                self._display.display(banner(lines, "NEXT STEPS"), color=COLOR)
        if self._mode == "live":
            self._region.bell()
