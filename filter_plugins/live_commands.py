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
import signal
import subprocess
import sys
import tempfile
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
# What a part's child never inherits: the switch and the state are the run's, the log is written here.
NOT_PASSED_ON = (LIVE_ENV, STATE_ENV, "ANSIBLE_LOG_PATH")
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
            pieces = screen.fill(line, room)
            wrapped += [pieces[0], *(f"  {piece}" for piece in pieces[1:])]
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
class Part:
    """One command of a run, drawn: its step's task, the child's lines to the log and the last of them on the
    screen, and the state saved for the next part, or the summary when the run is this one command."""

    def __init__(self, title, stage, step, argv, state_path="", plan=()):
        self.title, self.stage, self.step, self.argv, self.state_path = title, stage, step, list(argv), state_path
        self.plan = list(plan)
        self.display = Display()
        self.mode = "live" if live.can_draw() else "lines"
        self.out = live.Output(
            self.display, frame=self._frame, live=self.mode == "live", color=not C.ANSIBLE_NOCOLOR, to_log=True
        )
        self.color = self.out.color
        self.run = None
        self.task = None
        self.last = collections.deque(maxlen=FAILURE_LINES)
        self.interrupted = 0
        self.child = None
        self.records = {}
        self.failed_hosts = []

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
        state = load_state(self.state_path, self.title) if self.state_path else None
        if state is not None:
            self.run.adopt(state, now)
            self.records = state.get("next_records") or {}
            self.failed_hosts = list(state.get("failed_hosts") or [])
        self.out.start()
        name = os.path.basename(self.argv[0])
        with self.display._lock:
            self.run.planned(self.stage, self.plan)
            self.task = self.run.command(self.stage, self.step, name, now)
        self.out.redraw()
        self.out.spin()

    def _line(self, raw):
        line = raw.decode(errors="replace").rstrip("\r\n")
        self.display.display(line, log_only=True)
        self.last.append(line)
        said = view.one_line(text.plain_text(line))
        if said.strip():
            with self.display._lock:
                self.task.detail = said[:200]

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
        for raw in iter(child.stdout.readline, b""):
            self._line(raw)
        rc = child.wait()
        return self._ended(INTERRUPTED if self.interrupted else rc)

    def _ended(self, rc):
        now = self._now()
        outcome = "ok" if rc == 0 else "failed"
        with self.display._lock:
            self.run.result(self.task.key, "localhost", outcome, now)
            self.task.detail = ""
        self._after()
        if rc not in (0, INTERRUPTED):
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
        where = self.task.step or self.task.stage
        head = f"✗ {where}: {self.task.name} ({screen.duration(self.task.seconds(now))}) · exit {rc}"
        lines = [("", ""), (screen.render([(head, "r")], self.color), head)]
        lines += [(f"    {line}", f"    {line}") for line in (text.plain_text(line) for line in self.last)]
        if self.run.log:
            said = f"    the whole output: {self.run.log}"
            lines.append((screen.render([(said, "d")], self.color), said))
        self.out.print([*lines, ("", "")])


def run(title, stage, step, argv, state_path="", plan=()):
    part = Part(title, stage, step, argv, state_path, plan)
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
