"""A run in parts, and a command drawn live: what tools/live does once it has decided a command is drawn.

Some recipes are more than one process - just lint runs a check after another, just init two playbooks, just test lint,
pytest and Molecule - and each is drawn as one run (docs/architecture/custom-code.md §7.8):

- The state. Each part takes over the run's model from the parts before it - every task, step, report and record, as
  run_view.Run.dump keeps it - draws them as done, works, and saves it for the next. It is a file in LIVE_DIR named
  for the run's stamp and its top recipe, both from checked parts only, made by the run's first part, 0600 in a 0700
  directory, and a part takes over only a state of its own run's title. The playbook engine
  (filter_plugins/live_ansible.py) does the same for a playbook that is a part.
- A command (run): the child's stdin is /dev/null, its stdout and stderr one pipe, its environment this one without the
  live switch, the state's path or the run log's, with colour off and Python unbuffered. Every line it prints goes to
  the run's log through Ansible's Display, in the log's format and order; the region shows the part's step running,
  with the child's last line dim after it. A part that fails prints its last lines above, whole. Ctrl-C reaches the
  child, which shares the terminal's process group; the part reads its pipe to the end, so the child's last words
  reach the log, and exits 130.
- The end (end): the summary and NEXT STEPS from the state, whatever ended the run - the recipe's trap runs it - and
  the state removed. A run with no state, every part passed through, ends with nothing said.

Loaded by path as claude_on_proxmox_live_commands, as every module here loads another; it has no filters of its own.
"""

from __future__ import annotations

import collections
import contextlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from ansible import constants as C
from ansible.utils.display import Display


def _sibling(stem):
    # filter_plugins/<stem>.py, loaded by path once as claude_on_proxmox_<stem> (docs/architecture/custom-code.md §7.8).
    name = f"claude_on_proxmox_{stem}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().with_name(f"{stem}.py"))
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


text = _sibling("text")
screen = _sibling("screen")
view = _sibling("run_view")
live = _sibling("live_region")

LIVE_ENV = "CLAUDE_ON_PROXMOX_LIVE"
STATE_ENV = "CLAUDE_ON_PROXMOX_LIVE_STATE"
STAGES_ENV = "CLAUDE_ON_PROXMOX_LIVE_STAGES"
# What a part's child never inherits: the switch, the state and the stages are the run's, the log is written here.
NOT_PASSED_ON = (LIVE_ENV, STATE_ENV, STAGES_ENV, "ANSIBLE_LOG_PATH")
CHILD_ENV = {"NO_COLOR": "1", "PYTHONUNBUFFERED": "1", "PRE_COMMIT_COLOR": "never"}
# How much of a failed part's output is printed above: the end, where its error is.
FAILURE_LINES = 40
INTERRUPTED = 130


# --- the state ---------------------------------------------------------------------------------------------------
def load_state(path, title):
    """The state an earlier part of this run saved at path, or None: none there yet, another version's, another run's
    title, or not a state at all - a part never takes over what it did not find under its own run's name."""
    try:
        state = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or state.get("version") != view.STATE_VERSION or state.get("title") != title:
        return None
    return state


def save_state(path, state):
    """The state, written whole or not at all: 0600 in a 0700 directory, never followed through a link."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(state, handle)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def merged_records(*records):
    """NEXT STEPS records of several parts, {stat: {vm: {matter: line}}}: a later part's line for a VM and matter
    wins, as a later play's does within a run."""
    out = {}
    for each in records:
        for stat, by_vm in (each or {}).items():
            if not isinstance(by_vm, dict):
                continue
            for vm, matters in by_vm.items():
                if isinstance(matters, dict):
                    out.setdefault(str(stat), {}).setdefault(str(vm), {}).update(
                        {str(matter): line for matter, line in matters.items() if isinstance(line, str)}
                    )
    return out


def _callback():
    """The stdout callback's module, for NEXT STEPS as it says them and its colour."""
    name = "claude_on_proxmox_stdout_callback"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "callback_plugins" / "claude_on_proxmox.py"
        spec = importlib.util.spec_from_file_location(name, path)
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


def say_end(out, display, run, now, failed_hosts, next_steps, width):
    """The summary and NEXT STEPS, as every drawn run ends: above where the region was, NEXT STEPS wrapped by its
    words to its block's width, so a command or a URL is never split."""
    summary = run.summary(now, width, out.color, failed_hosts=failed_hosts, next_steps=next_steps)
    plain = run.summary(now, width, False, failed_hosts=failed_hosts, next_steps=next_steps)
    out.print([("", ""), *zip(summary, plain, strict=True)])
    if next_steps:
        room = min(80, width) - 2
        wrapped = []
        for line in next_steps:
            # A record of several lines - just init's problems, each under the one that counts them - keeps each, with
            # its indent; a line wrapped goes on two further in.
            for number, part in enumerate(line.split("\n")):
                indent = " " * (len(part) - len(part.lstrip(" ")) if number else 0)
                pieces = screen.fill(part.strip(), room - len(indent)) or [""]
                wrapped += [indent + pieces[0], *(f"{indent}  {piece}" for piece in pieces[1:])]
        with out.say():
            display.display(text.banner(wrapped, "NEXT STEPS"), color=_callback().COLOR)


def _width():
    try:
        return os.get_terminal_size(sys.stdout.fileno()).columns
    except (OSError, ValueError, AttributeError):
        return 100


def _log():
    path = os.environ.get("ANSIBLE_LOG_PATH", "")
    if not path:
        return ""
    relative = os.path.relpath(path)
    return relative if not relative.startswith("..") else path


# --- a command, as a part ------------------------------------------------------------------------------------------
class Plain:
    """The plain reader: the part is one task of its step, the child's last line dim after it."""

    def __init__(self, part):
        self.part = part
        self.task = None

    def env(self):
        return {}

    def start(self, now):
        self.task = self.part.run.command(self.part.stage, self.part.step, os.path.basename(self.part.argv[0]), now)

    def line(self, line, said, now):
        if said.strip():
            self.task.detail = said[:200]

    def poll(self, now):
        pass

    def ended(self, rc, now):
        """The part's end, its one task with it; whether a failure was said already, which none of this reader's is."""
        self.part.run.result(self.task.key, "localhost", "ok" if rc == 0 else "failed", now)
        self.task.detail = ""
        return False


class Pytest:
    """The pytest reader: each test a task of the step as live_pytest.py reports it - several running at once under
    xdist - the count once collected, and each failure's words above the moment it fails."""

    def __init__(self, part):
        self.part = part
        self.events = ""
        self.read = 0
        self.failed = False

    def env(self):
        descriptor, self.events = tempfile.mkstemp(prefix="live-pytest-", suffix=".jsonl")
        os.close(descriptor)
        return {"CLAUDE_ON_PROXMOX_LIVE_PYTEST": self.events}

    def start(self, now):
        self.part.run.part(self.part.stage, self.part.step)

    def line(self, line, said, now):
        self.poll(now)

    def poll(self, now):
        """The events pytest has written since the last look: whole lines only."""
        try:
            with open(self.events, "rb") as handle:
                handle.seek(self.read)
                chunk = handle.read()
        except OSError:
            return
        whole = chunk[: chunk.rfind(b"\n") + 1]
        self.read += len(whole)
        for raw in whole.splitlines():
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if isinstance(event, dict):
                self._event(event, now)

    def _event(self, event, now):
        run = self.part.run
        if isinstance(event.get("count"), int):
            run.expect(self.part.stage, self.part.step, event["count"])
        elif isinstance(event.get("start"), str):
            nodeid = view.one_line(text.plain_text(event["start"]))
            run.parallel(f"test:{nodeid}", nodeid.split("/")[-1], now)
        elif isinstance(event.get("end"), str):
            nodeid = view.one_line(text.plain_text(event["end"]))
            outcome = {"passed": "ok", "failed": "failed", "skipped": "skipped"}.get(event.get("outcome"), "ok")
            task = run.result(f"test:{nodeid}", "localhost", outcome, now)
            if outcome == "failed":
                self.failed = True
                said = text.plain_text(str(event.get("said") or "")).splitlines()
                self.part.said_failure(task, nodeid.split("/")[-1], said)

    def ended(self, rc, now):
        self.poll(now)
        with contextlib.suppress(OSError):
            os.unlink(self.events)
        run = self.part.run
        for task in run.order:
            if task.parallel and task.end is None:
                task.close(now)
        if rc not in (0, INTERRUPTED) and not self.failed:
            # pytest failed with no test failing - an error before any ran: the part's own failure.
            task = run.parallel(f"pytest:{len(run.order)}", os.path.basename(self.part.argv[0]), now)
            run.result(task.key, "localhost", "failed", now)
        return self.failed


class Molecule:
    """The Molecule reader: the scenario a step, its actions its tasks from Molecule's own lines - "[default >
    converge] Executing", then "Executed: Successful" - the Ansible task running said after the action, and a failed
    action's lines above as it fails, while Molecule goes on through its cleanup and destroy."""

    ACTION = re.compile(r"\[(?P<scenario>[^\]>]+?) > (?P<action>[\w-]+)\] (?P<what>Executing|Executed: (?P<how>.+))$")
    TASK = re.compile(r"^(?:TASK|RUNNING HANDLER) \[(?P<task>.+?)\]")
    MATRIX = re.compile(r"^\s*[├└]─+\s*(?P<action>[\w-]+)")

    def __init__(self, part):
        self.part = part
        self.action = None
        self.lines = collections.deque(maxlen=FAILURE_LINES)
        self.matrix = None
        self.failed = False

    def env(self):
        return {}

    def start(self, now):
        self.part.run.part(self.part.stage, self.part.step)

    def line(self, line, said, now):
        run = self.part.run
        self.lines.append(line)
        if said.strip().endswith("matrix") and self.matrix is None:
            self.matrix = 0
        elif self.matrix is not None and self.MATRIX.match(said):
            self.matrix += 1
            run.expect(self.part.stage, self.part.step, self.matrix)
        found = self.ACTION.search(said)
        if found and found.group("what") == "Executing":
            self.lines.clear()
            key = f"action:{found.group('scenario')}:{found.group('action')}:{len(run.order)}"
            self.action = run.task(key, found.group("action"), None, now)
            run.host_start(key, "localhost", now)
        elif found and self.action is not None:
            how = found.group("how").strip().lower()
            outcome = "ok" if how.startswith("success") else "failed" if how.startswith("fail") else "skipped"
            run.result(self.action.key, "localhost", outcome, now)
            self.action.detail = ""
            if outcome == "failed":
                self.failed = True
                self.part.said_failure(self.action, self.action.name, [text.plain_text(item) for item in self.lines])
        elif self.action is not None and self.action.end is None:
            task = self.TASK.match(said)
            if task:
                self.action.detail = task.group("task")[:200]

    def poll(self, now):
        pass

    def ended(self, rc, now):
        run = self.part.run
        if self.action is not None and self.action.end is None:
            run.result(self.action.key, "localhost", "ok" if rc == 0 else "failed", now)
        if rc not in (0, INTERRUPTED) and not self.failed:
            task = run.task(f"molecule:{len(run.order)}", os.path.basename(self.part.argv[0]), None, now)
            run.host_start(task.key, "localhost", now)
            run.result(task.key, "localhost", "failed", now)
        return self.failed


READERS = {"plain": Plain, "pytest": Pytest, "molecule": Molecule}


class Part:
    """One command of a run, drawn: its step's tasks as its reader makes them, the child's lines to the log, and the
    state saved for the next part, or the summary when the run is this one command."""

    def __init__(self, title, stage, step, argv, state_path="", plan=(), reader="plain", stages=()):
        self.title, self.stage, self.step, self.argv, self.state_path = title, stage, step, list(argv), state_path
        self.plan, self.stages = list(plan), list(stages)
        self.reader = READERS[reader](self)
        self.display = Display()
        self.mode = "live" if live.can_draw() else "lines"
        self.out = live.Output(
            self.display, frame=self._frame, live=self.mode == "live", color=not C.ANSIBLE_NOCOLOR, to_log=True
        )
        self.color = self.out.color
        self.run = None
        self.last = collections.deque(maxlen=FAILURE_LINES)
        self.interrupted = 0
        self.child = None
        self.records = {}
        self.failed_hosts = []
        self.stop = threading.Event()

    def _now(self):
        return time.monotonic()

    def _frame(self, cols, rows, glyph):
        with self.display._lock:
            if self.run is None:
                return []
            self.out.title(self.run.title_text(self._now()))
            return self.run.frame(self._now(), cols, rows, glyph, self.color)

    def _after(self):
        if self.mode == "lines":
            ended = self.run.ended_lines(self._now())
            if ended:
                self.out.print([(line, line) for line in ended])
        else:
            self.out.redraw()

    def _child_env(self):
        env = {name: value for name, value in os.environ.items() if name not in NOT_PASSED_ON}
        env.update(CHILD_ENV)
        env.update(self.reader.env())
        return env

    def _interrupt(self, *_):
        # The child is in the terminal's process group, so the Ctrl-C reached it too: it says its last and ends. A
        # second one ends a child that would not.
        self.interrupted += 1
        if self.interrupted > 1 and self.child is not None:
            self.child.kill()

    def start(self):
        now = self._now()
        self.run = view.Run(self.title, now, log=_log())
        self.run.parts = bool(self.state_path)
        state = load_state(self.state_path, self.title) if self.state_path else None
        if state is not None:
            self.run.adopt(state, now)
            self.records = state.get("next_records") or {}
            self.failed_hosts = list(state.get("failed_hosts") or [])
        self.out.start()
        with self.display._lock:
            self.run.planned_stages(self.stages)
            self.run.planned(self.stage, self.plan)
            self.reader.start(now)
        self.out.redraw()
        self.out.spin()

    def _line(self, raw):
        line = raw.decode(errors="replace").rstrip("\r\n")
        self.display.display(line, log_only=True)
        self.last.append(line)
        with self.display._lock:
            self.reader.line(line, view.one_line(text.plain_text(line)), self._now())

    def _polling(self):
        # A reader that reads more than the child's lines - pytest's events - looks again ten times a second.
        while not self.stop.wait(0.1):
            with self.display._lock:
                self.reader.poll(self._now())
            self._after()

    def work(self):
        """Run the command, its every line to the log; its exit code, or 130 after Ctrl-C."""
        if self.interrupted:
            return self._ended(INTERRUPTED)
        try:
            self.child = child = subprocess.Popen(
                self.argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=self._child_env(),
            )
        except OSError as error:
            self._line(f"{self.argv[0]}: {error.strerror}".encode())
            return self._ended(127)
        poller = threading.Thread(target=self._polling, daemon=True)
        poller.start()
        for raw in iter(child.stdout.readline, b""):
            self._line(raw)
        rc = child.wait()
        self.stop.set()
        poller.join()
        return self._ended(INTERRUPTED if self.interrupted else rc)

    def said_failure(self, task, name, said):
        """A task of the part that failed, above at once: where, which, and the end of what it said."""
        where = (task.step or task.stage) if task is not None else (self.step or self.stage)
        head = f"✗ {where}: {name}"
        lines = [("", ""), (screen.render([(head, "r")], self.color), head)]
        lines += [(f"    {line}", f"    {line}") for line in said[-FAILURE_LINES:]]
        self.out.print([*lines, ("", "")])

    def _ended(self, rc):
        now = self._now()
        with self.display._lock:
            said = self.reader.ended(rc, now)
        self._after()
        if rc not in (0, INTERRUPTED) and not said:
            self._failure(now, rc)
        self.out.finish()
        if self.state_path:
            failed = self.failed_hosts + (["localhost"] if rc != 0 else [])
            save_state(self.state_path, self.run.dump(now, self.records, failed))
        else:
            self.run.end(now, failed=rc != 0, stopped=rc == INTERRUPTED)
            self.out.title(self.run.title_text(now))
            say_end(self.out, self.display, self.run, now, ["localhost"] if rc else [], [], _width())
        return rc

    def _failure(self, now, rc):
        """The failed part's end, whole above: where, what it ran, its last lines and where the log has the rest."""
        task = next((task for task in reversed(self.run.order) if task.outcome == "failed"), None)
        where = (task.step or task.stage) if task is not None else (self.step or self.stage)
        name = task.name if task is not None else os.path.basename(self.argv[0])
        took = f" ({screen.duration(task.seconds(now))})" if task is not None else ""
        head = f"✗ {where}: {name}{took} · exit {rc}"
        lines = [("", ""), (screen.render([(head, "r")], self.color), head)]
        lines += [(f"    {line}", f"    {line}") for line in (text.plain_text(line) for line in self.last)]
        if self.run.log:
            said = f"    the whole output: {self.run.log}"
            lines.append((screen.render([(said, "d")], self.color), said))
        self.out.print([*lines, ("", "")])


def run(title, stage, step, argv, state_path="", plan=(), reader="plain", stages=()):
    part = Part(title, stage, step, argv, state_path, plan, reader, stages)
    # Ctrl-C from the first moment: a handler, never SIG_IGN, which the child would inherit.
    previous = signal.signal(signal.SIGINT, part._interrupt)
    try:
        part.start()
        return part.work()
    finally:
        signal.signal(signal.SIGINT, previous)


# --- the end of a run in parts -------------------------------------------------------------------------------------
def end(title, state_path, rc):
    """The end of a run in parts, whatever ended it: its summary and NEXT STEPS from the state, then the state gone.
    rc is the recipe's exit status: 130 after Ctrl-C, which says the run stopped."""
    state = load_state(state_path, title)
    if state is None:
        return
    display = Display()
    now = time.monotonic()
    run = view.Run(title, now, log=_log())
    run.parts = True
    run.adopt(state, now)
    failed_hosts = list(state.get("failed_hosts") or [])
    run.end(now, failed=rc != 0 or bool(failed_hosts), stopped=rc == INTERRUPTED)
    out = live.Output(display, frame=None, live=live.can_draw(), color=not C.ANSIBLE_NOCOLOR, to_log=True)
    if out.live:
        # The tab says how the run ended: drawn here as the region would, the region itself long gone.
        sys.stdout.write(f"\x1b]0;{run.title_text(now)}\x07")
        sys.stdout.flush()
    next_steps = _callback().next_steps(merged_records(state.get("next_records")))
    say_end(out, display, run, now, failed_hosts, next_steps, _width())
    if out.live:
        sys.stdout.write("\a")
        sys.stdout.flush()
    with contextlib.suppress(OSError):
        os.unlink(state_path)


class FilterModule:
    """No filters: tools/live loads this module by path. Here so the filter loader takes the file."""

    def filters(self):
        return {}
