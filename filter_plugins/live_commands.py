"""A run in parts, and a command drawn live: what tools/live does once it has decided a command is drawn.

Some recipes are more than one process - lint's checks one after another, init's two playbooks, test's lint, pytest
and Molecule - and each is drawn as one run (docs/architecture/custom-code.md §7.8):

- The state. Each part takes over the run's model from the parts before it - every task, step, report and record, as
  run_view.Run.dump keeps it - draws them as done, works, and saves it for the next. It is a file in LIVE_DIR named
  for the run's stamp and its top recipe, both from checked parts only, made by the run's first part, 0600 in a 0700
  directory, and a part takes over only a state of its own run's title. The playbook engine
  (filter_plugins/live_ansible.py) does the same for a playbook that is a part.
- A command (run): the child's stdin is /dev/null, its stdout and stderr one pipe, its environment this one without the
  live switch, the state's path or the run log's, with colour off and Python unbuffered. Every line it prints goes to
  the run's log through Ansible's Display, in the log's format and order; the region shows the part's step running,
  with the child's last line dim after it. A part that fails prints its last lines above, whole. The child runs in a
  session of its own, with no terminal: what would ask at /dev/tty - git or ssh, as pre-commit fetches its hooks -
  fails at once and says so, where a process group of its own would be stopped by the terminal and hang the run.
  Ctrl-C reaches it through the part, which sends it on and reads the pipe to its end, so the child's last words
  reach the log, and exits 130. SIGTERM or SIGHUP - a kill, a timeout, the terminal closing - is sent on to the
  child's session, the region goes and the echo comes back, and the state is saved, said stopped, before the part
  dies of the same signal.
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
processes = _sibling("processes")
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
# ansible-playbook's exit code after Ctrl-C (its ExitCode.KEYBOARD_INTERRUPT): a part that stopped, not one that failed.
ANSIBLE_INTERRUPTED = 99
# A program that runs a script, which names nothing on the screen.
SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
# A line a part that passed may still need read: a warning, said above once the region is gone.
WARNING = re.compile(r"\bwarn(ing)?s?\b", re.IGNORECASE)
# What a check says of warnings every time, which nobody can act on: a syntax check's plays target groups only a run
# fills; ansible-lint runs offline as its config asks, says how to turn deprecations off, and counts "0 warning(s)".
# And Molecule's: a requirements file or a playbook a scenario has no need of, the remote temporary directory a fresh
# container makes, a scenario's prompt that takes its default, and ansible-core's own internal collection, which has
# no version to read.
ROUTINE = re.compile(
    r"Could not match supplied host pattern|running in offline mode|Deprecation warnings can be disabled|\b0 warning"
    r"|Missing \w+ requirements file|Missing playbook|Module remote_tmp \S+ did not exist and was created"
    r"|Not prompting as we are not in interactive mode|does not have a MANIFEST\.json file, nor has it galaxy\.yml"
)
# How long the state of a run that never said its end - a shell that died of a signal - is kept.
STALE_SECONDS = 24 * 3600
# What of LIVE_DIR is this project's to prune, which a LIVE_DIR exported for anything else must not lose: a state, as
# the justfile names one - the run's stamp, its top recipe, its shell's PID - the pytest reader's events beside it,
# and save_state's temporary.
OWN = re.compile(r"\d{8}-\d{6}-[a-z][a-z-]*-\d+(?:\.json|-pytest-\w+\.jsonl)|\.state-\w+")


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
    # A run whose shell died of a signal never said its end, and left its state: gone a day later. Only a state and
    # a temporary are pruned, and only as plain files: LIVE_DIR is whatever the environment says.
    for stale in path.parent.iterdir():
        with contextlib.suppress(OSError):
            if stale != path and OWN.fullmatch(stale.name) and stale.is_file() and not stale.is_symlink():
                if time.time() - stale.lstat().st_mtime > STALE_SECONDS:
                    stale.unlink()
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
    """The stdout callback's module, for the end of a run in parts, which tools/live says outside Ansible: NEXT STEPS
    as the callback says them, and its colour. Loaded by path as every module here loads another; a playbook's engine
    hands say_end its own callback's colour instead, and never loads the callback a second time."""
    name = "claude_on_proxmox_claude_on_proxmox"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "callback_plugins" / "claude_on_proxmox.py"
        spec = importlib.util.spec_from_file_location(name, path)
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


def say_end(out, display, run, now, failed_hosts, next_steps, width, color):
    """The summary and NEXT STEPS, as every drawn run ends: above where the region was, NEXT STEPS in the callback's
    colour, each line whole as the stream prints it - a command or a URL is copied from one line, and the terminal
    wraps it, never the run."""
    summary = run.summary(now, width, out.color, failed_hosts=failed_hosts, next_steps=next_steps)
    plain = run.summary(now, width, False, failed_hosts=failed_hosts, next_steps=next_steps)
    out.print([("", ""), *zip(summary, plain, strict=True)])
    if next_steps:
        with out.say():
            display.display(text.banner(next_steps, "NEXT STEPS"), color=color)


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
def command_name(argv):
    """What a command is called on the screen: its program, past env and its settings - a shell running a script is
    no name at all."""
    words = list(argv)
    if words and os.path.basename(words[0]) == "env":
        words = words[1:]
        while words and ("=" in words[0] or words[0].startswith("-")):
            words = words[1:]
    name = os.path.basename(words[0]) if words else ""
    return "" if name in SHELLS else name


def outcome_of(rc):
    """A command's outcome by its exit code: ok, stopped by Ctrl-C, or failed."""
    return "ok" if rc == 0 else "stopped" if rc == INTERRUPTED else "failed"


class Abandoned(Exception):
    """A second Ctrl-C: the part stops reading what its child's group still writes."""


class Ended(BaseException):
    """SIGTERM or SIGHUP: the run is ended from outside, by that signal. No handler of a part's catches it."""

    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


# What ends a run from outside: a kill or a timeout, and the terminal closing.
OUTSIDE = (signal.SIGTERM, signal.SIGHUP)


class Plain:
    """The plain reader: the part is one task of its step, the child's last line dim after it."""

    # A line that is a warning: any that says one.
    warning = WARNING

    def __init__(self, part):
        self.part = part
        self.task = None

    def env(self):
        return {}

    def start(self, now):
        part = self.part
        self.task = part.run.command(part.stage, part.step, command_name(part.argv) or part.step or part.stage, now)

    def line(self, line, said, now):
        if said.strip():
            self.task.detail = said[:200]

    def poll(self, now):
        pass

    def ended(self, rc, now):
        """The part's end, its one task with it; whether a failure was said already, which none of this reader's is."""
        self.part.run.result(self.task.key, "localhost", outcome_of(rc), now)
        self.task.detail = ""
        return False


def test_name(nodeid):
    """A test as the screen names it: its file's name and the rest of its ID, whose parameters may hold a "/" of their
    own (test_x.py::test_path[../etc/passwd])."""
    path, separator, rest = nodeid.partition("::")
    return os.path.basename(path) + separator + rest


class Pytest:
    """The pytest reader: each test a task of the step as live_pytest.py reports it - several running at once under
    xdist - the count once collected, and each failure's words above the moment it fails."""

    warning = WARNING

    def __init__(self, part):
        self.part = part
        self.events = ""
        self.read = 0
        self.failed = False
        self.made = False

    def env(self):
        # Beside the run's state, 0600 in its 0700 directory, never the system's: what pytest writes there names every
        # test and quotes each failure, and the state's prune takes it should the run die before its end removes it.
        state = Path(self.part.state_path)
        state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, self.events = tempfile.mkstemp(dir=state.parent, prefix=f"{state.stem}-pytest-", suffix=".jsonl")
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
            run.parallel(f"test:{nodeid}", test_name(nodeid), now)
            self.made = True
        elif isinstance(event.get("end"), str):
            nodeid = view.one_line(text.plain_text(event["end"]))
            outcome = {"passed": "ok", "failed": "failed", "skipped": "skipped"}.get(event.get("outcome"), "ok")
            task = run.result(f"test:{nodeid}", "localhost", outcome, now)
            if outcome == "failed":
                self.failed = True
                said = text.plain_text(str(event.get("said") or "")).splitlines()
                self.part.said_failure(task, test_name(nodeid), said)

    def ended(self, rc, now):
        self.poll(now)
        with contextlib.suppress(OSError):
            os.unlink(self.events)
        run = self.part.run
        # A test still running when pytest ended was stopped, by Ctrl-C or with pytest.
        for task in run.order:
            if task.parallel and task.end is None:
                task.host_end("localhost", "stopped", now)
        if not self.made or (rc not in (0, INTERRUPTED) and not self.failed):
            # pytest failed with no test failing - an error before any ran - or said no test at all (a plugin that
            # never loaded): the command is the part's one task, its outcome its exit code's, never a step not run.
            name = command_name(self.part.argv) or self.part.step or self.part.stage
            task = run.parallel(f"pytest:{len(run.order)}", name, now)
            run.result(task.key, "localhost", outcome_of(rc), now)
        return self.failed


class Molecule:
    """The Molecule reader: the scenario a step, its actions its tasks from Molecule's own lines - its "scenario test
    matrix" the count, "[default > converge] Executing", then "Executed: Successful" - the Ansible task running said
    after the action. A failed action is said above as it fails, while Molecule goes on through its cleanup and
    destroy: the task that failed in its head, and what Ansible said of it from its "fatal:" on, or the action's last
    lines where no task failed. Molecule says why a scenario failed - an idempotence test's changed tasks, a
    playbook's exit code - only after those, at the end, so a scenario that failed prints too what Molecule said
    after the action that failed. Its warnings and Ansible's are said like a check's, but for Molecule's routine
    (ROUTINE): a requirements file or a playbook a scenario has no need of."""

    ACTION = re.compile(r"\[(?P<scenario>[^\]>]+?) > (?P<action>[\w-]+)\] (?P<what>Executing|Executed: (?P<how>.+))$")
    TASK = re.compile(r"^(?:TASK|RUNNING HANDLER) \[(?P<task>.+?)\]")
    MATRIX = re.compile(r"\] scenario test matrix: (?P<actions>[\w, -]+)$")
    # Ansible's line for a task that failed on a host, in any result format, and the lines that end what it said.
    FAILED = re.compile(r"^(?:fatal|failed): \[")
    PLAY = re.compile(r"^(?:PLAY|TASK|RUNNING HANDLER) ")
    # A line that is a warning: Molecule's (its level first) or Ansible's, never a task that names one.
    warning = re.compile(r"^(?:WARNING\s|\[(?:DEPRECATION )?WARNING\]:)")

    def __init__(self, part):
        self.part = part
        self.action = None
        self.lines = collections.deque(maxlen=FAILURE_LINES)
        self.matrix = None
        self.failed = False
        self.made = False
        # The Ansible task running, by its TASK line and its place in the action; the last that failed, from its
        # "fatal:" on, while those lines go on.
        self.task, self.tasks = "", 0
        self.failure, self.failure_task, self.failure_at, self.dropped, self.collecting = [], "", -1, 0, False

    def env(self):
        return {}

    def start(self, now):
        self.part.run.part(self.part.stage, self.part.step)

    def line(self, line, said, now):
        run = self.part.run
        self.lines.append(line)
        matrix = self.MATRIX.search(said)
        if matrix and self.matrix is None:
            self.matrix = len([action for action in matrix.group("actions").split(",") if action.strip()])
            run.expect(self.part.stage, self.part.step, self.matrix)
        found = self.ACTION.search(said)
        if found and found.group("what") == "Executing":
            self.lines.clear()
            self.task, self.tasks = "", 0
            self.failure, self.failure_task, self.failure_at, self.dropped, self.collecting = [], "", -1, 0, False
            key = f"action:{found.group('scenario')}:{found.group('action')}:{len(run.order)}"
            self.action = run.task(key, found.group("action"), None, now)
            run.host_start(key, "localhost", now)
            self.made = True
        elif found and self.action is not None:
            how = found.group("how").strip().lower()
            outcome = "ok" if how.startswith("success") else "failed" if how.startswith("fail") else "skipped"
            run.result(self.action.key, "localhost", outcome, now)
            self.action.detail = ""
            if outcome == "failed":
                self.failed = True
                self._said_failure()
                # The failure said, the end the part prints when it fails is what Molecule says from here: its
                # cleanup, its destroy, then why.
                self.part.last.clear()
        elif self.action is not None and self.action.end is None:
            self._ansible(line, said)

    def _ansible(self, line, said):
        """A line of the Ansible run an action is: the task running, and what a task that failed said."""
        task = self.TASK.match(said)
        if task:
            self.task, self.tasks = task.group("task"), self.tasks + 1
            self.action.detail = self.task[:200]
        if self.PLAY.match(said):
            self.collecting = False
        elif said == "...ignoring" and self.collecting:
            # A failure the task ignores is none.
            self.failure, self.failure_task, self.failure_at, self.collecting = [], "", -1, False
        elif self.FAILED.match(said):
            if not (self.collecting and self.failure_at == self.tasks):
                # The task's first failure: a later task's replaces an earlier one's, ignored or rescued.
                self.failure, self.failure_task, self.failure_at, self.dropped = [], self.task, self.tasks, 0
            self.collecting = True
            self._keep(line)
        elif self.collecting:
            self._keep(line)

    def _keep(self, line):
        # From its start, which names what failed and why; what runs past the room is in the log.
        if len(self.failure) < FAILURE_LINES - 1:
            self.failure.append(line)
        else:
            self.dropped += 1

    def _said_failure(self):
        """The failed action above, at once: the task that failed and what Ansible said of it, or its last lines."""
        name = self.action.name
        if self.failure:
            name = f"{name} · {view.one_line(text.plain_text(self.failure_task))}" if self.failure_task else name
            said = [text.plain_text(item) for item in self.failure]
            if self.dropped:
                said.append(f"… {self.dropped} more line{'' if self.dropped == 1 else 's'} in the run's log")
        else:
            said = [text.plain_text(item) for item in self.lines]
        self.part.said_failure(self.action, name, said)

    def poll(self, now):
        pass

    def ended(self, rc, now):
        """The scenario's end; never a failure said already, since Molecule says why one failed last."""
        run = self.part.run
        if self.action is not None and self.action.end is None:
            # An action Molecule never said the end of: it is the part's failure, or Ctrl-C stopped it.
            run.result(self.action.key, "localhost", outcome_of(rc), now)
            self.failed = self.failed or rc not in (0, INTERRUPTED)
        if not self.made or (rc not in (0, INTERRUPTED) and not self.failed):
            # Molecule failed with no action failing, or said no action this reader knows - a format it no longer
            # writes: the command is the part's one task, its outcome its exit code's, never a scenario not run.
            name = command_name(self.part.argv) or self.part.step or self.part.stage
            task = run.task(f"molecule:{len(run.order)}", name, None, now)
            run.host_start(task.key, "localhost", now)
            run.result(task.key, "localhost", outcome_of(rc), now)
        return False


READERS = {"plain": Plain, "pytest": Pytest, "molecule": Molecule}


class Part:
    """One command of a run, drawn: its step's tasks as its reader makes them, the child's lines to the log, and the
    state saved for the next part and the run's end, which says the summary."""

    def __init__(self, title, stage, step, argv, state_path, plan=(), reader="plain", stages=(), interrupted=False):
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
        self.warnings = []
        self.interrupted = int(bool(interrupted))
        self.child = None
        self.under = []
        self.reading = False
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

    def _after(self, final=False):
        """In lines mode, a line for each step that ended - at the part's end, its own too; live, a redraw."""
        if self.mode == "lines":
            with self.display._lock:
                ended = self.run.ended_lines(self._now(), final=final)
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
        # The child runs in a process group of its own, which the terminal's Ctrl-C does not reach: it is sent there,
        # and the child says its last and ends. A second one ends everything the child started - its group and every
        # process under it, since one in a session of its own, ansible-core's task worker, a nested run's part, outlives
        # the group - and what it still writes goes unread.
        self.interrupted += 1
        if self.child is None:
            return
        if self.interrupted > 1:
            processes.end(self.child.pid, *self.under, signum=signal.SIGKILL)
        else:
            # What the child has started, remembered before it is signalled: a child that ends at the Ctrl-C leaves
            # them under PID 1, where a second Ctrl-C could no longer find them from it.
            self.under = processes.descendants(self.child.pid)
            with contextlib.suppress(OSError):
                os.killpg(self.child.pid, signal.SIGINT)
        if self.interrupted > 1 and self.reading:
            raise Abandoned

    def _outside(self, signum, _frame):
        # The child's session has no terminal to signal it: it gets the signal from here, and the part unwinds.
        if self.child is not None:
            with contextlib.suppress(OSError):
                os.killpg(self.child.pid, signum)
        raise Ended(signum)

    def abandon(self):
        """The run ended from outside: the region gone and the echo back, and the state saved, said stopped, for the
        recipe's end - each as far as a terminal that may be gone allows."""
        with contextlib.suppress(Exception):
            self.out.finish()
        if self.run is not None:
            with contextlib.suppress(Exception):
                state = self.run.dump(self._now(), self.records, self.failed_hosts)
                state["stopped"] = True
                save_state(self.state_path, state)

    def start(self):
        now = self._now()
        self.run = view.Run(self.title, now, log=_log())
        self.run.parts = True
        state = load_state(self.state_path, self.title)
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
        said = view.one_line(text.plain_text(line))
        if self.reader.warning.search(said) and not ROUTINE.search(said) and len(self.warnings) < FAILURE_LINES:
            self.warnings.append(text.plain_text(line).rstrip())
        with self.display._lock:
            self.reader.line(line, said, self._now())

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
                # A session of its own, with no terminal: a prompt at /dev/tty fails at once rather than stopping the
                # child, and the session is its process group too, which the part signals.
                start_new_session=True,
            )
        except OSError as error:
            self._line(f"{self.argv[0]}: {error.strerror}".encode())
            # As a shell says it: 127 for a command it cannot find, 126 for one it found and cannot run.
            return self._ended(127 if isinstance(error, FileNotFoundError) else 126)
        poller = threading.Thread(target=self._polling, daemon=True)
        poller.start()
        try:
            self.reading = True
            for raw in iter(child.stdout.readline, b""):
                self._line(raw)
            self.reading = False
            rc = child.wait()
        except Abandoned:
            self.reading = False
            rc = INTERRUPTED
            with contextlib.suppress(Exception):
                child.wait(timeout=5)
        self.stop.set()
        poller.join()
        # Killed by a signal, as the shell says it: 128 and the signal's number.
        rc = 128 - rc if rc < 0 else rc
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
        self._after(final=True)
        if rc not in (0, INTERRUPTED) and not said:
            self._failure(now, rc)
        elif rc == 0 and self.warnings:
            self._warned()
        self.out.finish()
        # The run's end says the summary, from the state: a part never says one of its own.
        failed = self.failed_hosts + (["localhost"] if rc not in (0, INTERRUPTED) else [])
        state = self.run.dump(now, self.records, failed)
        state["stopped"] = rc == INTERRUPTED
        save_state(self.state_path, state)
        # A Ctrl-C that came while the part was ending ends the run all the same.
        return INTERRUPTED if self.interrupted else rc

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

    def _warned(self):
        """A part that passed with warnings says them above, whole: once the region is gone they would be in the log
        alone."""
        where = self.step or self.stage
        name = command_name(self.argv) or where
        head = f"! {where}: {name} passed with warnings"
        lines = [("", ""), (screen.render([(head, "y")], self.color), head)]
        lines += [(f"    {line}", f"    {line}") for line in self.warnings]
        if self.run.log:
            said = f"    the whole output: {self.run.log}"
            lines.append((screen.render([(said, "d")], self.color), said))
        self.out.print([*lines, ("", "")])


def run(title, stage, step, argv, state_path, plan=(), reader="plain", stages=(), interrupted=False):
    """A part, drawn: its exit code. interrupted is tools/live's record of a Ctrl-C it took before the part's own
    handler was in, read once it is."""
    part = Part(title, stage, step, argv, state_path, plan, reader, stages)
    # Ctrl-C from the first moment: a handler, never SIG_IGN, which the child would inherit. SIGTERM and SIGHUP end
    # the run, its child's session with it.
    previous = {signal.SIGINT: signal.signal(signal.SIGINT, part._interrupt)}
    previous.update((signum, signal.signal(signum, part._outside)) for signum in OUTSIDE)
    try:
        if interrupted:
            part.interrupted = max(part.interrupted, 1)
        part.start()
        return part.work()
    except Ended as ended:
        part.abandon()
        # Ended as the signal ends a process, which is what the recipe's shell expects to see.
        signal.signal(ended.signum, signal.SIG_DFL)
        os.kill(os.getpid(), ended.signum)
        return 128 + ended.signum
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


# --- the end of a run in parts -------------------------------------------------------------------------------------
def end(title, state_path, rc):
    """The end of a run in parts, whatever ended it: its summary and NEXT STEPS from the state, then the state gone.
    rc is the recipe's exit status: 130 after Ctrl-C, or ansible-playbook's 99, says the run stopped, as a part that
    saved itself stopped does."""
    state = load_state(state_path, title)
    if state is None:
        return
    display = Display()
    now = time.monotonic()
    run = view.Run(title, now, log=_log())
    run.parts = True
    run.adopt(state, now)
    failed_hosts = list(state.get("failed_hosts") or [])
    # Stopped: Ctrl-C at the recipe (130), at a playbook (ansible-playbook's 99), a part that says it stopped, or a
    # run that ended well with steps it listed never reached - a Ctrl-C between its parts, which bash may say as 0.
    stopped = rc in (INTERRUPTED, ANSIBLE_INTERRUPTED) or bool(state.get("stopped")) or (rc == 0 and bool(run.unrun()))
    run.end(now, failed=rc != 0 or bool(failed_hosts), stopped=stopped)
    out = live.Output(display, frame=None, live=live.can_draw(), color=not C.ANSIBLE_NOCOLOR, to_log=True)
    if out.live:
        # The tab says how the run ended: drawn here as the region would, the region itself long gone.
        sys.stdout.write(f"\x1b]0;{run.title_text(now)}\x07")
        sys.stdout.flush()
    callback = _callback()
    next_steps = callback.next_steps(merged_records(state.get("next_records")))
    say_end(out, display, run, now, failed_hosts, next_steps, _width(), callback.COLOR)
    if out.live:
        sys.stdout.write("\a")
        sys.stdout.flush()
    with contextlib.suppress(OSError):
        os.unlink(state_path)


class FilterModule:
    """No filters: tools/live loads this module by path. Here so the filter loader takes the file."""

    def filters(self):
        return {}
