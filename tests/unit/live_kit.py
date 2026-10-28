"""What every live output's tests share: a run at a pseudo-terminal of the test's own, read back by vt.py, piped, and
the checks every drawn run must pass (docs/architecture/custom-code.md §7.8).

A run here is a real ansible-playbook with the stdout callback ansible.cfg names (or the one a test chooses), its
environment the harness's (PASSED_ON) and a HOME of the test's own, the Galaxy collections still found where they
are: a live test runs whole playbooks, whose localhost is this machine, so nothing it starts may reach the
developer's ~/.ssh, ~/.claude or ~/.ansible. Every playbook a live test runs is also run through the runner its own
tests use, with every path they pin (tests/unit/test_live_recipes.py holds that).

What a person sees is the VT's screen and scrollback; what the run keeps is its log. Ran checks a drawn run left
nothing of its region behind, never wrapped a frame and wrote nothing unknown, and helps compare its log with the
stream's.
"""

from __future__ import annotations

import fcntl
import os
import re
import struct
import subprocess
import termios
import time
from pathlib import Path

from harness import ANSIBLE_PLAYBOOK, REPO, _at_a_pseudo_terminal, _end, _env, _fail_if_the_desktop_was_reached
from harness import _in_raw_mode as in_raw_mode
from vt import VT

SYNC_ON = b"\x1b[?2026h"
# A region's header and footer, which a run must leave nowhere once it ends.
HEADER = re.compile(r"^claude-on-proxmox  \S+.* · started \d\d:\d\d")
FOOTER = re.compile(r"^. \d+:\d\d · (stage|step|\w+) \d+ of \d+")
# The callback ansible.cfg names, and the timings it is used beside.
CONFIG = f"""callback_result_format = yaml
stdout_callback = claude_on_proxmox
callback_plugins = {REPO / "callback_plugins"}
callbacks_enabled = ansible.posix.profile_tasks
"""


def collections_path() -> str:
    """Where this machine's Galaxy collections are, so a run with a HOME of its own still finds them."""
    for name in ("ANSIBLE_COLLECTIONS_PATH", "ANSIBLE_COLLECTIONS_PATHS"):
        if os.environ.get(name):
            return os.environ[name]
    return str(Path.home() / ".ansible" / "collections")


def environment(root: Path, title: str = "", config: str = CONFIG, **settings: str) -> dict:
    """A live run's environment: the harness's, CLAUDE_ON_PROXMOX_LIVE set to title (none for the stream), a log of
    its own, colour on, a terminal type, UTF-8, and a HOME under root."""
    home = root / "home"
    home.mkdir(exist_ok=True)
    live = {
        "ANSIBLE_LOG_PATH": str(root / "run.log"),
        "ANSIBLE_NOCOLOR": "0",
        "TERM": "xterm-256color",
        "PYTHONUTF8": "1",
        "HOME": str(home),
        "ANSIBLE_COLLECTIONS_PATH": collections_path(),
        **({"CLAUDE_ON_PROXMOX_LIVE": title} if title else {}),
    }
    return _env(root, config=config, **{**live, **settings})


class Ran:
    """A run's rc, every byte it wrote, its log and, at a terminal, the VT it was read through."""

    def __init__(self, rc, raw, log, screen=None):
        self.rc, self.raw, self.log, self.screen = rc, raw, log, screen
        self.text = raw.decode(errors="replace")

    @property
    def transcript(self):
        return "\n".join(line.rstrip() for line in self.screen.transcript())

    @property
    def frames(self):
        """Each region drawn, its escapes left out: what the run showed while it went."""
        frames = re.findall(rb"\x1b\[\?2026h(.*?)\x1b\[\?2026l", self.raw, re.S)
        return [re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", f.decode(errors="replace")).replace("\r", "") for f in frames]

    def assert_drawn_and_gone(self, visible_only=False):
        """A region was drawn, never wrapped, wrote nothing unknown, swallowed nothing, and left nothing of itself:
        no footer and no header, on the screen or above it (visible_only: on the screen, after a resize, which
        pushes the screen into the history by design)."""
        assert SYNC_ON in self.raw, self.text
        assert self.screen.unknown == set() and self.screen.pending == b"", (self.screen.unknown, self.screen.pending)
        lines = self.screen.lines if visible_only else self.screen.transcript()
        left = [line for line in lines if FOOTER.match(line) or HEADER.match(line)]
        assert left == [], "\n".join(self.screen.transcript())
        if not visible_only:
            assert self.screen.frame_wraps == 0


def resize(master: int, screen: VT, cols: int, rows: int) -> None:
    """The terminal's new size, which sends the run SIGWINCH; the screen keeps its lines, as xterm does."""
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    screen.resize(cols, rows)


def at_a_terminal(
    argv, root, env, *, cols=100, rows=30, prompts=None, answers=None, interrupt=None, at=None, act=None, timeout=120
) -> Ran:
    """argv at a pseudo-terminal of cols and rows, read back by a VT.

    prompts maps a pause's text to the keys typed once it has the terminal (typed again after two seconds if pause
    had not yet taken them); answers maps a vars_prompt's text, which reads a line, to the keys typed once it shows;
    interrupt sends Ctrl-C once the title says it; at, (words of the title, bytes typed or
    a new (cols, rows)), is done once the title says them; act(chunk, output, master, process, screen) does anything
    else after each read."""
    screen = VT(cols, rows)
    state = {"typed": {}, "answered": set(), "interrupt": interrupt, "at": at}

    def drive(chunk, output, master, process):
        screen.feed(chunk)
        for prompt, keys in (prompts or {}).items():
            typed = state["typed"].get(prompt)
            if prompt.encode() in output and in_raw_mode(master):
                # Typed again while pause has not taken them: keys in the moment before it flushed are lost.
                if typed is None or (time.monotonic() - typed[0] > 2 and output.count(prompt.encode()) == typed[1]):
                    os.write(master, keys)
                    state["typed"][prompt] = (time.monotonic(), output.count(prompt.encode()))
        for text, keys in (answers or {}).items():
            if text not in state["answered"] and text.encode() in output:
                state["answered"].add(text)
                os.write(master, keys)
        title = screen.titles[-1] if screen.titles else ""
        if state["interrupt"] and state["interrupt"] in title:
            state["interrupt"] = None
            os.write(master, b"\x03")
        if state["at"] and state["at"][0] in title:
            then, state["at"] = state["at"][1], None
            if isinstance(then, bytes):
                os.write(master, then)
            else:
                resize(master, screen, *then)
        if act is not None:
            act(chunk, output, master, process, screen)

    rc, raw = _at_a_pseudo_terminal(
        [str(part) for part in argv], root, env, size=(cols, rows), act=drive, timeout=timeout, poll=0.1
    )
    _fail_if_the_desktop_was_reached(root)
    log = Path(env.get("ANSIBLE_LOG_PATH") or root / "run.log")
    return Ran(rc, raw, log.read_text() if log.exists() else "", screen)


def piped(argv, root, env, timeout=120) -> Ran:
    """argv with no terminal at all: stdin from /dev/null, stdout and stderr one pipe, a process group of its own."""
    process = subprocess.Popen(
        [str(part) for part in argv],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        process_group=0,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except BaseException:
        # The run's whole group: its workers, and what they started.
        _end(process)
        raise
    _fail_if_the_desktop_was_reached(root)
    log = Path(env.get("ANSIBLE_LOG_PATH") or root / "run.log")
    return Ran(process.returncode, output, log.read_text() if log.exists() else "")


def playbook(root, inventory, path, *args):
    """The argv of ansible-playbook on a playbook."""
    return [ANSIBLE_PLAYBOOK, "-i", inventory, *args, path]


def logged(log: str) -> list[str]:
    """A run log's lines without what changes from run to run: the time and process each starts with, every number
    (profile_tasks' times, a command's), and profile_tasks' recap, which it sorts by time. Every line of the recap,
    however long its task took: one of ten seconds or more reads 00.00s once its digits are zeroed, and a task that
    took 10 s in one run and 3 in the other - a retried task on a loaded machine - kept its line in one log alone."""
    lines = []
    for line in log.splitlines():
        line = re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ p=\d+ u=\S+ n=ansible \w+\| ", "", line)
        line = re.sub(r"\d", "0", line)
        if not re.search(r" -{3,} 0+\.00s$", line):
            lines.append(line)
    return lines


def in_order(part: list[str], whole: list[str]) -> list[str]:
    """What of part is not in whole in the same order: [] when part is whole's with lines added."""
    position = 0
    for index, line in enumerate(part):
        try:
            position = whole.index(line, position) + 1
        except ValueError:
            return part[index:]
    return []


def seconds(said: str) -> float:
    """A duration as a summary says it - 0.6 s, 33 s, 3:58 - in seconds."""
    if ":" in said:
        minutes, rest = said.split(":")
        return int(minutes) * 60 + int(rest)
    return float(said.removesuffix(" s"))
