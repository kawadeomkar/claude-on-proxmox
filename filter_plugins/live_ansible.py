"""A playbook run drawn live: Ansible's events turned into the region, the words above it, and the summary.

The stdout callback (callback_plugins/claude_on_proxmox.py) hands every event to an Engine when a recipe has exported
CLAUDE_ON_PROXMOX_LIVE, its title, and keeps only its classic output for itself. Everything a live run does is here:

- live, at a terminal that can draw (filter_plugins/live_region.py): the run's model (filter_plugins/run_view.py)
  drawn in place at the bottom of the screen, with the reports, the blocks, the diffs, warnings and failures printed
  above it for good;
- lines, any other output: a line as each step ends, and the same reports, blocks and failures.

Either way the default callback still renders every task, sent to the run's log alone (display's log_only), so the
log is what it would be without the engine; the run's own words go to the screen in colour and to the log without;
and a summary replaces PLAY RECAP on the screen, which the log keeps, followed by NEXT STEPS. What the run is made
of - its stages and steps - is its recipe's declaration (filter_plugins/live_recipes.py); nothing here names a
command (docs/architecture/custom-code.md §7.5, §7.8).

Loaded by path as claude_on_proxmox_live_ansible, as every module here loads another; it has no filters of its own.
"""

from __future__ import annotations

import atexit
import importlib.util
import os
import sys
import time
from pathlib import Path

from ansible import constants as C
from ansible import context
from ansible.utils import display as ansible_display


def _sibling(stem):
    # filter_plugins/<stem>.py, loaded by path once as claude_on_proxmox_<stem> (docs/architecture/custom-code.md §7.8).
    name = f"claude_on_proxmox_{stem}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().with_name(f"{stem}.py"))
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


text = _sibling("text")
plain_text = text.plain_text
screen = _sibling("screen")
view = _sibling("run_view")
live = _sibling("live_region")

LIVE_ENV = "CLAUDE_ON_PROXMOX_LIVE"
WAITING_TAG = "attention_browser"
DEBUG_ACTIONS = frozenset({"debug", "ansible.builtin.debug", "ansible.legacy.debug"})
STATS_ACTIONS = frozenset({"set_stats", "ansible.builtin.set_stats", "ansible.legacy.set_stats"})
# The facts the provision stage says a VM by.
VM_FACTS = ("proxmox_vm_resolved_id", "proxmox_vm_address", "proxmox_vm_exists")
FAILURE_LINES = 20


def from_environment(callback, playbook):
    """The engine for this run, or None for the stream: CLAUDE_ON_PROXMOX_LIVE set, verbosity 0, and a log to send
    the stream to (LOG_DIR= turns it off). The variable is taken out of the environment either way, so nothing the
    run starts - VS Code, whose terminals a later just destroy may run in - inherits it."""
    title = os.environ.pop(LIVE_ENV, "").strip()
    if not title or callback._display.verbosity != 0 or ansible_display.logger is None:
        return None
    engine = Engine(callback, title)
    engine.start(playbook)
    return engine


# --- what a play compiled to -------------------------------------------------------------------------------------
def tasks_of(blocks, out, play=None):
    """Each task a play compiled to, as (its ID, its role's name or None), blocks' rescue and always included, and
    only those --tags and --skip-tags leave, as the play's iterator filters them."""
    for block in blocks:
        for part in (block.block, block.rescue, block.always):
            for item in part or []:
                if hasattr(item, "block"):
                    tasks_of([item], out, play)
                elif not getattr(item, "implicit", False) and tagged_in(item, play):
                    out.append((item._uuid, role_of(item)))
    return out


def tagged_in(task, play):
    if play is None:
        return True
    try:
        return bool(task.evaluate_tags(play.only_tags, play.skip_tags, all_vars={}))
    except Exception:  # noqa: BLE001 - tags that need a variable to say: counted, as a run may reach them
        return True


def role_of(task):
    return task._role.get_name(include_role_fqcn=False) if getattr(task, "_role", None) else None


def rescued(task, play=None):
    """Whether a block's rescue takes the task's failure: the task is among that block's own tasks, at any depth, and
    the rescue has a task the play's tags leave. One in a rescue or an always is not, unless a block further out has
    it among its own.

    Any link between the task and the block may be the one the block lists: a static import puts the file's own block
    there, under a TaskInclude that no block lists, so the link right under the block is not always it."""
    below, node = [], task
    while node is not None:
        below.append(getattr(node, "_uuid", None))
        parent = getattr(node, "_parent", None)
        if parent is not None and getattr(parent, "rescue", None) and _keeps(parent.rescue, play):
            ids = set(below)
            if not ids & (_uuids(parent.rescue) | _uuids(parent.always)) and ids & _uuids(parent.block):
                return True
        node = parent
    return False


def _uuids(items):
    return {getattr(item, "_uuid", None) for item in items or []}


def _keeps(items, play):
    """Whether any task among items, at any depth, is one the play's --tags and --skip-tags leave: a rescue they filter
    out entirely rescues nothing, and Ansible fails the host."""
    for item in items or []:
        if hasattr(item, "block"):
            if any(_keeps(part, play) for part in (item.block, item.rescue, item.always)):
                return True
        elif tagged_in(item, play):
            return True
    return False


class Engine:
    """One live run: the callback's events in; the region, the run's own words and the summary out."""

    def __init__(self, callback, title):
        self.cb = callback
        self.display = callback._display
        self.mode = "live" if live.can_draw() else "lines"
        self.run = None
        self.out = live.Output(
            self.display, frame=self._frame, live=self.mode == "live", color=not C.ANSIBLE_NOCOLOR, to_log=True
        )
        self.color = self.out.color
        self.region = self.out.region
        self.plan_play = None
        self.title = title
        self._failed_items = {}  # (task ID, host): the results of a loop's items that failed, said with the task

    # --- what the run says, and where it goes ------------------------------------------------------------------
    # The run's own words and anything on stderr - warnings, deprecations, errors - stay on the screen; the rest,
    # the default callback's every task and profile_tasks' timings, goes to the log alone (live_region.Output).
    def say(self):
        return self.out.say()

    def print(self, lines):
        """Lines of the run's own, (coloured, plain) each: the colour to the screen, the plain text to the log."""
        self.out.print(lines)

    def _width(self):
        try:
            return os.get_terminal_size(sys.stdout.fileno()).columns
        except (OSError, ValueError, AttributeError):
            return 100

    def _now(self):
        return time.monotonic()

    def _wrapped(self, head, lines, style=""):
        """head, then lines wrapped under where head ends, whole - never cut: (coloured, plain) pairs for print."""
        column = screen.width(head)
        room = max(20, self._width() - 1 - column)
        out = []
        pieces = [piece for line in lines for piece in screen.fill(line, room)]
        for index, piece in enumerate(pieces):
            runs = [*(head if index == 0 else [(" " * column, "")]), (piece, style)]
            out.append((screen.render(runs, self.color), screen.render(runs, False)))
        return out

    def _frame(self, cols, rows, glyph):
        with self.display._lock:
            if self.run is None:
                return []
            now = self._now()
            # A tagged wait that never retries, GitHub's device flow, is known by how long it runs.
            if self.run.settle(now):
                self.region.bell()
            self.region.title(self.run.title_text(now))
            return self.run.frame(now, cols, rows, glyph, self.color)

    def _after(self):
        """After each event: in lines mode, a line for each step that ended; live, a redraw now."""
        if self.mode == "lines":
            ended = self.run.ended_lines(self._now())
            if ended:
                self.print([(line, line) for line in ended])
        else:
            self.region.redraw()

    def _log_path(self):
        path = os.environ.get("ANSIBLE_LOG_PATH", "")
        if not path:
            return ""
        try:
            return os.path.relpath(path) if not os.path.relpath(path).startswith("..") else path
        except ValueError:
            return path

    # --- the playbook ------------------------------------------------------------------------------------------
    def start(self, playbook):
        check = bool((context.CLIARGS or {}).get("check"))
        self.run = view.Run(self.title, self._now(), check=check, log=self._log_path())
        plays = []
        for play in playbook.get_plays():
            try:
                tasks = tasks_of(play.compile(), [], play)
            except Exception:  # noqa: BLE001 - a count is a nicety: a play that will not compile here still runs
                tasks = []
            plays.append((play._uuid, play.get_path(), tasks))
        self.run.plays(plays)
        first = playbook.get_plays()[:1]
        if first and Path(str(first[0].get_path()).split(":", 1)[0]).name in self.run.recipe.plan:
            self.plan_play = first[0]._uuid
        self.out.start()
        self.out.redraw()
        self.out.spin()
        if self.mode == "live":
            # Registered after the region's own, so it runs first: a run that ends without its stats, at Ctrl-C,
            # does not leave the tab saying it is still at work.
            atexit.register(self._stopped_title)

    def _stopped_title(self):
        if self.run.finished is None:
            self.region.title(f"{self.run.title} · stopped")

    def play_start(self, play):
        with self.display._lock:
            self.run.play_start(play._uuid, play.get_name(), play.get_path())

    def task_start(self, task):
        with self.display._lock:
            name = task.get_name().strip()
            role = role_of(task)
            if role and name.startswith(role + " : "):
                name = name[len(role) + 3 :]
            waits = WAITING_TAG in task.tags and task.action not in DEBUG_ACTIONS
            self.run.task(task._uuid, name, role, self._now(), waits=waits)
            self._after()

    def include(self, included_file):
        with self.display._lock:
            task = included_file._task
            if getattr(included_file, "_is_role", False):
                role = getattr(task, "_role_name", None) or str(included_file._filename)
                search = [os.path.join(os.getcwd(), "roles"), *C.DEFAULT_ROLES_PATH]
                tasks_from = (getattr(task, "_from_files", None) or {}).get("tasks", "main")
                path = view.role_tasks_file(role, tasks_from, search)
            else:
                role, path = role_of(task), str(included_file._filename)
            self.run.include(role, view.count_tasks(path) if path else 0)

    # --- results -----------------------------------------------------------------------------------------------
    def runner_start(self, host, task):
        with self.display._lock:
            self.run.host_start(task._uuid, host.get_name(), self._now())

    def _result(self, result, outcome):
        with self.display._lock:
            self.run.result(result.task._uuid, result.host.get_name(), outcome, self._now())
            self._after()

    def ok(self, result):
        """A task that ended well. False when the callback should render nothing more of it: a browser block."""
        self._result(result, "changed" if result.result.get("changed") else "ok")
        host = result.host.get_name()
        # The VM's ID and address as proxmox_vm sets them, and only then: a task on the VM could set any fact.
        facts = (result.result.get("ansible_facts") or {}) if role_of(result.task) == "proxmox_vm" else {}
        with self.display._lock:
            for name in VM_FACTS:
                if name in facts:
                    value = facts[name]
                    self.run.fact(host, name, plain_text(str(value)) if name == "proxmox_vm_address" else value)
        if result.task.action in STATS_ACTIONS:
            self._records(result)
        msg = result.result.get("msg")
        if WAITING_TAG in result.task.tags and result.task.action in DEBUG_ACTIONS and isinstance(msg, str):
            with self.say():
                self.cb._banner_or_default(result)
            return False
        # A looped report's words are its items'; its own msg is the loop's "All items completed".
        if result.task.action in DEBUG_ACTIONS and "results" not in result.result:
            self._report(result)
        return True

    def item_ok(self, result):
        if result.task.action in DEBUG_ACTIONS:
            self._report(result)

    def _records(self, result):
        """What a report recorded for NEXT STEPS, as it goes: a report on a matter a later report recorded again is
        not repeated in the summary."""
        data = (result.result.get("ansible_stats") or {}).get("data") or {}
        with self.display._lock:
            task = self.run.find(result.task._uuid)
            for stat in self.cb.NEXT_STATS:
                for vm, matters in (data.get(stat) or {}).items() if isinstance(data.get(stat), dict) else ():
                    for matter, line in matters.items() if isinstance(matters, dict) else ():
                        if isinstance(line, str):
                            self.run.record(str(vm), str(matter), plain_text(line), task=task)

    def _report(self, result):
        """A debug message, a report: whole above the region, wrapped, and a line of the summary, under its VM."""
        msg = result.result.get("msg")
        if msg in (None, ""):
            return
        host = result.host.get_name()
        parts = msg if isinstance(msg, list) else str(msg).splitlines()
        lines = [said for said in (view.one_line(str(part)) for part in parts) if said]
        if not lines:
            return
        plan = self.plan_play is not None and self.run.play == self.plan_play
        with self.display._lock:
            self.run.note(host, " ".join(lines), plan=plan, task=self.run.find(result.task._uuid))
        # A play on this machine names the VM in its words; anywhere else the VM leads, once.
        if lines[0].startswith(host + ": "):
            lines[0] = lines[0][len(host) + 2 :]
        who = [] if host == "localhost" or lines[0].startswith(host + " ") else [(host, "b"), ("  ", "")]
        self.print(self._wrapped([("  ", ""), *who], lines))

    def skipped(self, result):
        self._result(result, "skipped")

    def failed(self, result, ignore_errors=False):
        outcome = "ignored" if ignore_errors else "rescued" if rescued(result.task, self.cb._play) else "failed"
        self._result(result, outcome)
        if outcome == "failed":
            self._failure(result, "failed")

    def item_failed(self, result):
        with self.display._lock:
            self._failed_items.setdefault((result.task._uuid, result.host.get_name()), []).append(result.result)

    def unreachable(self, result):
        ignored = bool(getattr(result.task, "ignore_unreachable", False))
        self._result(result, "ignored" if ignored else "unreachable")
        if not ignored:
            self._failure(result, "could not be reached")

    def _failure(self, result, how):
        """A failed task, for good above the region: the task, the VM, what it said, and where the log has it."""
        with self.display._lock:
            task = self.run.find(result.task._uuid)
            where = (task.step or task.stage) if task is not None else ""
            took = f" ({screen.duration(task.seconds(self._now()))})" if task is not None else ""
            name = task.name if task is not None else result.task.get_name()
        said = self._said(result.result)
        # A loop's own msg is "One or more items failed": what each failed item said is the failure.
        for item in self._failed_items.pop((result.task._uuid, result.host.get_name()), []):
            said.append(f"{view.one_line(self.cb._get_item_label(item))}:")
            said += [f"  {line}" for line in self._said(item)]
        said_how = "" if how == "failed" else f" · {how}"
        out = [("", "")]
        out += self._wrapped([("✗ ", "r")], [f"{where} on {result.host.get_name()}: {name}{took}{said_how}"], "r")
        out += self._wrapped([("    ", "")], [line for line in said if line.strip()][: FAILURE_LINES * 2])
        if self.run.log:
            out += self._wrapped([("    ", "")], [f"the whole output: {self.run.log}"], "d")
        self.print([*out, ("", "")])

    @staticmethod
    def _said(res):
        """What a failed result says: its message, then the last lines of its output - a module's own, should it
        have crashed."""
        said = []
        for key in ("msg", "reason"):
            if res.get(key):
                said += plain_text(str(res[key])).splitlines()
        for key in ("stderr_lines", "module_stderr", "stdout_lines", "module_stdout"):
            value = res.get(key)
            if value:
                lines = value if isinstance(value, list) else str(value).splitlines()
                said += [plain_text(str(line)) for line in lines][-FAILURE_LINES:]
                break
        return said

    def retry(self, result):
        """A retried task. False when the callback should render nothing more of it: a wait, said once."""
        if WAITING_TAG not in result.task.tags:
            return True
        left = result.result.get("retries", 0) - result.result.get("attempts", 0) + 1
        try:
            seconds = left * int(result.task.delay)
        except (TypeError, ValueError):
            seconds = 0
        with self.display._lock:
            first = self.run.retry(result.task._uuid, result.host.get_name(), self._now(), seconds)
        if first and self.mode == "live":
            self.region.bell()
        if self.mode == "lines":
            with self.say():
                self.cb._retry_once(result)
        else:
            self.cb._retry_once(result)
        self._after()
        return False

    # --- the end -----------------------------------------------------------------------------------------------
    def stats(self, stats, recap):
        """The end: the region gone, PLAY RECAP (recap) to the log alone, the summary and NEXT STEPS on the screen."""
        now = self._now()
        failed = sorted(set(stats.failures) | set(stats.dark))
        with self.display._lock:
            self.run.end(now, failed=bool(failed))
            if self.mode == "live":
                self.region.title(self.run.title_text(now))
        if self.mode == "lines":
            self._after()
        self.region.finish()
        recap(stats)
        width = self._width()
        summary = self.run.summary(now, width, self.color, failed_hosts=failed)
        plain = self.run.summary(now, width, False, failed_hosts=failed)
        self.print([("", ""), *zip(summary, plain, strict=True)])
        lines = self.cb.next_steps_of(stats)
        if lines:
            with self.say():
                self.display.display(text.banner(lines, "NEXT STEPS"), color=self.cb.COLOR)
        if self.mode == "live":
            self.region.bell()


class FilterModule:
    """No filters: the stdout callback loads this module by path. Here so the filter loader takes the file."""

    def filters(self):
        return {}
