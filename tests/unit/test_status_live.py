"""`just status` at a terminal: the live report, drawn and redrawn in place, then the finished one (lifecycle.md §4.6).

Each test runs status.yml against the fake Proxmox (test_status.fake_fleet) with the callback just status chooses,
at a pseudo-terminal of its own whose size the test sets, and reads the bytes back through vt.VT, a small terminal:
what is on the screen and in its scrollback when the run ends, and whether a frame ever wrapped. The finished report
is the run log's, which Display writes and the live frames never reach.
"""

from __future__ import annotations

import fcntl
import os
import re
import signal
import struct
import subprocess
import sys
import termios
from pathlib import Path

import pytest
import yaml
from harness import ANSIBLE_PLAYBOOK, REPO, _at_a_pseudo_terminal, _env, _fail_if_the_desktop_was_reached
from test_status import fake_fleet, status_root
from vt import VT

sys.path.insert(0, str(REPO / "callback_plugins"))

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
LIVE = {
    "ANSIBLE_CALLBACK_PLUGINS": str(REPO / "callback_plugins"),
    "ANSIBLE_STDOUT_CALLBACK": "claude_on_proxmox_status",
    # UTF-8 whatever this machine's locale: the callback draws only for a terminal that takes the spinner.
    "PYTHONUTF8": "1",
}


@pytest.fixture(scope="module")
def fleet(tmp_path_factory):
    # One fake for the module: status only reads, which test_status holds.
    yield from fake_fleet(tmp_path_factory.mktemp("fleet"))


def _inventory(root: Path) -> Path:
    """localhost alone, as Ansible's implicit one: an empty inventory is a warning, which a real run never prints."""
    inventory = root / "hosts.yml"
    inventory.write_text(
        yaml.safe_dump(
            {
                "all": {
                    "hosts": {
                        "localhost": {"ansible_connection": "local", "ansible_python_interpreter": sys.executable}
                    }
                }
            }
        )
    )
    return inventory


def _size(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _at_a_terminal(
    tmp_path: Path,
    base: str,
    cols: int,
    rows: int,
    *,
    term="xterm-256color",
    resize=None,
    interrupt=False,
    playbook=None,
    colour=False,
):
    """Run status.yml at a pseudo-terminal of cols x rows: its rc, the VT the bytes went through, the raw bytes and
    the run log. resize, (cols, rows), changes the size once the first frame with the VMs is drawn; interrupt sends
    Ctrl-C's SIGINT to the run's process group once both running VMs (test_status.fake_fleet) wait in ssh.

    Either holds every VM's ssh (test_status.SSH) until it is done: a Linux pty takes the rest of the run's output
    without blocking it, so the run could end before the test reads that frame. A resize lets the run go on once
    the region has pushed the screen up at the new size; an interrupt never does. The interrupt waits for the held
    ssh because a SIGINT that lands while Ansible forks a worker is lost: its handler then runs inside logging's
    after-fork hook, which prints the KeyboardInterrupt as "Exception ignored" and the run goes on. Once each VM's
    worker waits in ssh, nothing forks."""
    root, status = status_root(tmp_path, base)
    playbook = playbook or status
    log = root / "run.log"
    hold = root / "ssh-holds"
    if resize or interrupt:
        hold.touch()
    # The test's own module beside the project's, which the harness names alone.
    library = f"{tmp_path / 'library'}{os.pathsep}{REPO / 'playbooks' / 'library'}"
    env = _env(root, ANSIBLE_LOG_PATH=str(log), TERM=term, ANSIBLE_LIBRARY=library, **LIVE)
    if colour:
        # The harness turns colour off for every run; this one is at a colour terminal, as a person's is.
        env["ANSIBLE_NOCOLOR"] = "0"
    vt = VT(cols, rows)
    acted = []
    # The screen pushed up at the new size: a newline (the terminal's \r\n) for each of its rows, then up all but one.
    pushed = re.compile(rb"(\r?\n){%d}\x1b\[%dA" % (resize[1], resize[1] - 1)) if resize else None

    held = root / "ssh.log"

    def resize_or_interrupt(chunk, output, master, process):
        vt.feed(chunk)
        if not acted and resize and b"VMID" in output:
            acted.append(len(output))
            _size(master, *resize)
            os.killpg(process.pid, signal.SIGWINCH)
            vt.resize(*resize)
        if not acted and interrupt and held.exists() and len(held.read_text().splitlines()) >= 2:
            acted.append(len(output))
            os.killpg(process.pid, signal.SIGINT)
        if pushed and acted and hold.exists() and pushed.search(output, acted[0]):
            hold.unlink()

    rc, output = _at_a_pseudo_terminal(
        [str(ANSIBLE_PLAYBOOK), "-i", str(_inventory(root)), "--check", str(playbook)],
        root,
        env,
        size=(cols, rows),
        act=resize_or_interrupt,
        timeout=120,
        poll=0.05,
    )
    _fail_if_the_desktop_was_reached(root)
    return rc, vt, output, log.read_text() if log.exists() else ""


def _report(log: str) -> list[str]:
    """The finished report, as the run log holds it: from its title to its exit line."""
    lines = log.splitlines()
    start = next(i for i, line in enumerate(lines) if re.search(r"\| claude-on-proxmox  \d{4}-", line))
    end = next(i for i in range(start, len(lines)) if re.match(r"exit \d", lines[i]))
    return [lines[start].split("| ", 1)[1]] + lines[start + 1 : end + 1]


def _wrapped(lines: list[str], cols: int) -> list[str]:
    """lines as a terminal of cols shows them: each one longer than cols on as many rows as it takes."""
    out = []
    for line in lines:
        line = line.rstrip()
        out += [line[i : i + cols].rstrip() for i in range(0, len(line), cols)] or [""]
    return out


def _shown(vt: VT) -> list[str]:
    return [line.rstrip() for line in vt.transcript()]


def test_a_report_that_fits_is_redrawn_in_place_and_leaves_only_the_finished_one(tmp_path, fleet) -> None:
    rc, vt, output, log = _at_a_terminal(tmp_path, fleet, 100, 60)
    assert rc == 0, output.decode(errors="replace")
    assert output.count(b"\x1b[?2026h") >= 3, "the report was not drawn live"
    report = _report(log)
    # Everything the live frames drew was erased: the screen and its scrollback hold the finished report alone.
    assert _shown(vt) == [line.rstrip() for line in report]
    assert not vt.unknown and vt.frame_wraps == 0
    assert max(vt.ups) < 60
    assert not any(glyph in log for glyph in FRAMES) and "\x1b" not in log


def test_a_window_shorter_than_the_report_shows_its_top_and_says_how_much_more(tmp_path, fleet) -> None:
    rc, vt, output, log = _at_a_terminal(tmp_path, fleet, 100, 14)
    assert rc == 0, output.decode(errors="replace")
    assert b"more lines, shown when the run ends" in output
    # Cut to the height, a frame never scrolls: nothing of it is left in the scrollback.
    assert _shown(vt) == [line.rstrip() for line in _report(log)]
    assert max(vt.ups) <= 13 and vt.frame_wraps == 0


def test_a_narrow_window_never_wraps_a_frame(tmp_path, fleet) -> None:
    rc, vt, output, log = _at_a_terminal(tmp_path, fleet, 60, 40)
    assert rc == 0, output.decode(errors="replace")
    assert vt.frame_wraps == 0 and not vt.unknown
    # The finished report is printed whole, as text: the terminal wraps it, and nothing else is left.
    assert _shown(vt) == _wrapped(_report(log), 60)


@pytest.mark.parametrize(("before", "after"), [((100, 40), (70, 20)), ((70, 20), (100, 40))])
def test_a_resize_keeps_every_frame_in_the_scrollback_and_the_finished_report_whole(
    tmp_path, fleet, before, after
) -> None:
    rc, vt, output, log = _at_a_terminal(tmp_path, fleet, *before, resize=after)
    assert rc == 0, output.decode(errors="replace")
    report = _wrapped(_report(log), after[0])
    shown = _shown(vt)
    assert shown[-len(report) :] == report
    # The screen was pushed up rather than drawn over: what went before is whole lines, never a frame overwritten
    # part way, and the report is printed once.
    assert sum(1 for line in shown if line.startswith("claude-on-proxmox  ") and "·" in line) >= 2
    assert sum(1 for line in shown if re.match(r"exit \d", line)) == 1
    # A frame worked out for the old size can still be on its way when the window shrinks, and wrap: that is what
    # pushing the screen up and drawing afresh is for, so no count of wraps holds across a resize.


def test_ctrl_c_erases_the_live_region_and_ends_the_run(tmp_path, fleet) -> None:
    rc, vt, output, log = _at_a_terminal(tmp_path, fleet, 100, 60, interrupt=True)
    # 99 is ansible-core's for a run interrupted; any other is a run that ended on its own.
    assert rc == 99, output.decode(errors="replace")
    shown = "\n".join(_shown(vt))
    assert not any(glyph in shown for glyph in FRAMES), shown
    assert "interrupted" in shown.lower(), shown


def test_colour_reaches_the_screen_and_never_the_log(tmp_path, fleet) -> None:
    # A live smoke at a colour terminal found the run log full of the report's colour codes.
    rc, vt, output, log = _at_a_terminal(tmp_path, fleet, 100, 60, colour=True)
    assert rc == 0, output.decode(errors="replace")
    assert b"\x1b[1;36mVMS" in output
    assert "\x1b" not in log
    assert _shown(vt) == [line.rstrip() for line in _report(log)]


def test_a_dumb_terminal_is_given_the_finished_report_only(tmp_path, fleet) -> None:
    rc, vt, output, log = _at_a_terminal(tmp_path, fleet, 100, 60, term="dumb")
    assert rc == 0, output.decode(errors="replace")
    assert b"\x1b" not in output
    assert _shown(vt) == [line.rstrip() for line in _report(log)]


def test_piped_the_finished_report_is_all_there_is(tmp_path, fleet) -> None:
    root, playbook = status_root(tmp_path, fleet)
    log = root / "run.log"
    process = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", str(_inventory(root)), "--check", str(playbook)],
        cwd=root,
        env=_env(root, ANSIBLE_LOG_PATH=str(log), TERM="xterm-256color", **LIVE),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        process_group=0,
    )
    _fail_if_the_desktop_was_reached(root)
    assert process.returncode == 0, process.stderr
    assert process.stdout.splitlines() == _report(log.read_text())
    assert "\x1b" not in process.stdout


WARNS = """\
import time

from ansible.module_utils.basic import AnsibleModule

module = AnsibleModule(argument_spec={}, supports_check_mode=True)
time.sleep(1)  # long enough for a frame first
module.warn("the stand-in module warns")
module.exit_json(changed=False)
"""


def test_a_warning_is_shown_between_frames_and_kept(tmp_path, fleet) -> None:
    # Since ansible-core 2.19 a task's warnings travel on its result, and a stdout callback of one's own that does
    # not show them drops them without a word.
    (tmp_path / "library").mkdir()
    (tmp_path / "library" / "warns.py").write_text(WARNS)
    playbook = tmp_path / "warns.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Warn while the report is drawn",
                    "hosts": "localhost",
                    "gather_facts": False,
                    "tasks": [
                        {"name": "Warn", "warns": {}},
                        {
                            "name": "Report",
                            "ansible.builtin.set_fact": {
                                "status_report": "{{ {} | status_report('2026-10-25 12:00 UTC') }}"
                            },
                        },
                    ],
                }
            ]
        )
    )
    rc, vt, output, log = _at_a_terminal(tmp_path, fleet, 100, 40, playbook=playbook)
    assert rc == 0, output.decode(errors="replace")
    assert b"\x1b[?2026h" in output.split(b"the stand-in module warns")[0], "nothing was drawn before the warning"
    shown = _shown(vt)
    assert shown[0] == "[WARNING]: the stand-in module warns"
    assert shown[1:] == [line.rstrip() for line in _report(log)]
