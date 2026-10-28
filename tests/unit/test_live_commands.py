"""A run in parts, and a command drawn live: tools/live and filter_plugins/live_commands.py.

tools/live decides with the standard library alone whether a part is drawn, and passes it through otherwise: piped, no
title, a -v, no log, or no engine to draw with - a fresh virtualenv's. Drawn, each part takes over the run's state from
the parts before it, and the recipe's trap says the summary from it at the end, whatever ended the run. Each run here
is a stand-in recipe - a bash script with the justfile's trap and parts - at a pseudo-terminal of the test's own, its
state and log in the test's directory (docs/architecture/custom-code.md §7.8).
"""

from __future__ import annotations

import contextlib
import importlib.util
import itertools
import json
import os
import re
import signal
import stat
import subprocess
import sys
import termios
import time
from pathlib import Path

import live_kit as kit
import pytest
from harness import REPO

LIVE = REPO / "tools" / "live"
STAMP = "20261026-131050"


def _the_justfiles_trap():
    """just lint's end and trap, as the justfile writes them: its {{ live }} the stand-in's live."""
    lines = [line.strip() for line in (REPO / "justfile").read_text().splitlines()]
    start = lines.index(next(line for line in lines if line.startswith("lint ")))
    end = next(line for line in lines[start:] if line.startswith("live_end() {"))
    trap = next(line for line in lines[start:] if line.startswith("trap ") and "live_end" in line)
    assert "{{ live }}" in end, end
    return end.replace("{{ live }}", "live") + "\n" + trap


# A recipe in parts, as the justfile writes one: the justfile's own trap at its top, each command behind tools/live.
RECIPE = (
    """#!/bin/bash
set -eu
live() {{ "{python}" "{live}" --log-dir="{logs}" --stamp={stamp} --ansible-args="${{ARGS:-}}" "$@"; }}
"""
    + _the_justfiles_trap().replace("{", "{{").replace("}", "}}")
    + """
{parts}
"""
)
SAYS = "import os, sys; print('one'); print('two', os.environ.get('CLAUDE_ON_PROXMOX_LIVE', 'unset'))"
FAILS = "import sys; [print(f'line-{n}') for n in range(1, 51)]; sys.exit(3)"


def _recipe(root, parts, title="lint", **env):
    script = root / "recipe.sh"
    script.write_text(
        RECIPE.format(python=sys.executable, live=LIVE, logs=root / "logs", stamp=STAMP, parts="\n".join(parts))
    )
    script.chmod(0o755)
    state = root / "live" / f"{STAMP}-lint.json"
    environment = kit.environment(root, title, CLAUDE_ON_PROXMOX_LIVE_STATE=str(state), **env)
    return [str(script)], environment, state


def _part(step, code, stage="lint"):
    return f'live run {stage} --step {step} -- "{sys.executable}" -c "{code}"'


@pytest.fixture(scope="module")
def two_parts(tmp_path_factory):
    root = tmp_path_factory.mktemp("two_parts")
    # Between the parts, the state the first saved for the second.
    saved = f"test -f {root / 'live' / f'{STAMP}-lint.json'}"
    argv, env, state = _recipe(root, [_part("says", SAYS), saved, _part("fails", FAILS)])
    ran = kit.at_a_terminal(argv, root, env, timeout=120)
    ran.state = state
    return ran


def test_a_command_passed_through_is_exactly_what_it_printed_and_never_sees_the_switch(tmp_path):
    argv, env, state = _recipe(tmp_path, [_part("says", SAYS)])
    ran = kit.piped(argv, tmp_path, env)
    assert ran.rc == 0 and ran.text == "one\ntwo unset\n", ran.text
    assert not state.exists()


@pytest.mark.parametrize(
    ("why", "env"),
    [
        ("no title", {"CLAUDE_ON_PROXMOX_LIVE": ""}),
        ("a -v", {"ARGS": "-f 30 -vv"}),
        ("a bare -vvv", {"ARGS": "-vvv"}),
        # LIVE_DIR=: no state to carry the run between its parts, which would each say a run of their own.
        ("no state", {"CLAUDE_ON_PROXMOX_LIVE_STATE": ""}),
    ],
)
def test_at_a_terminal_a_part_is_passed_through_without_a_title_or_with_a_v(tmp_path, why, env):
    argv, environment, state = _recipe(tmp_path, [_part("says", SAYS)])
    environment.update(env)
    ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=60)
    assert ran.rc == 0, ran.text
    assert kit.SYNC_ON not in ran.raw and "one" in ran.text and "two unset" in ran.text, why
    assert not state.exists()


def test_a_command_passed_through_keeps_an_ignored_ctrl_c_and_says_one_it_cannot_run_as_a_shell_does(tmp_path):
    # nohup, or a script's background job: Ctrl-C ignored, as the command would have had it without tools/live.
    says = "import signal; print('ignored' if signal.getsignal(signal.SIGINT) == signal.SIG_IGN else 'default')"
    unrunnable = tmp_path / "unrunnable"
    unrunnable.write_text("#!/bin/sh\necho ran\n")
    unrunnable.chmod(0o644)
    argv, env, state = _recipe(tmp_path, [_part("says", says)])
    ignoring = subprocess.run(
        argv, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),  # noqa: PLW1509 - no threads here
    )  # fmt: skip
    assert ignoring.returncode == 0 and ignoring.stdout == "ignored\n", ignoring
    defaults = kit.piped(argv, tmp_path, env)
    assert defaults.rc == 0 and defaults.text == "default\n", defaults.text
    for command, rc in ((unrunnable, 126), (tmp_path / "missing", 127)):
        argv, env, state = _recipe(tmp_path, [f'live run lint --step cannot -- "{command}"'])
        ran = kit.piped(argv, tmp_path, env)
        assert ran.rc == rc and str(command) in ran.text, ran.text


def test_without_its_packages_a_part_is_passed_through(tmp_path):
    # A Python with no site-packages, as a fresh virtualenv before just install: neither PyYAML nor ansible-core.
    argv, environment, state = _recipe(tmp_path, [_part("says", SAYS)])
    script = tmp_path / "recipe.sh"
    script.write_text(script.read_text().replace(f'"{sys.executable}" "{LIVE}"', f'"{sys.executable}" -S "{LIVE}"'))
    ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=60)
    assert ran.rc == 0 and kit.SYNC_ON not in ran.raw and "two unset" in ran.text, ran.text


def test_parts_are_drawn_as_one_run_and_its_end_says_it_whatever_ended_it(two_parts):
    ran = two_parts
    assert ran.rc == 3, ran.text
    ran.assert_drawn_and_gone()
    # The second part drew the first, done.
    assert any(re.search(r"^ ✓ says .*\n ⠋|^ ✓ says ", frame, re.M) and " fails " in frame for frame in ran.frames)
    screen = ran.transcript
    # The failed part's end above, whole: its last 40 lines.
    assert re.search(r"^✗ fails: python\S* \(.*\) · exit 3$", screen, re.M), screen
    assert "line-11\n" in screen + "\n" and "line-10\n" not in screen + "\n", screen
    assert re.search(r"^claude-on-proxmox  lint · failed after \d+ s at fails · \d\d:\d\d$", screen, re.M), screen
    table = screen.split("CHECKS", 1)[1].split("\n\n", 1)[0]
    assert re.search(r"^ ✓ says ", table, re.M) and re.search(r"^ ✗ fails ", table, re.M), table
    assert ran.screen.titles[-1] == "lint · failed"
    assert not ran.state.exists(), "the end removes the state"


def test_every_line_a_part_printed_is_in_the_run_log_and_the_child_saw_no_switch(two_parts):
    log = two_parts.log
    assert re.search(r"n=ansible INFO\| one$", log, re.M), log
    assert re.search(r"n=ansible INFO\| two unset$", log, re.M), log
    assert all(f"| line-{n}\n" in log for n in range(1, 51)), log
    assert "claude-on-proxmox  lint · failed after" in log


def test_the_state_is_the_run_s_alone(tmp_path):
    state_path = '"$CLAUDE_ON_PROXMOX_LIVE_STATE"'
    modes = f'ls -ld "$(dirname {state_path})" {state_path} > {tmp_path / "modes"}'
    argv, environment, state = _recipe(tmp_path, [_part("says", SAYS), modes])
    ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=60)
    assert ran.rc == 0, ran.text
    modes = (tmp_path / "modes").read_text().splitlines()
    assert modes[0].startswith("drwx------") and modes[1].startswith("-rw-------"), modes
    # Another run's state, under this run's name, is never taken over.
    state.parent.mkdir(exist_ok=True)
    state.write_text(json.dumps({"version": 1, "title": "test", "tasks": []}))
    ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=60)
    assert ran.rc == 0 and "CHECKS" in ran.transcript and "test ·" not in ran.transcript, ran.transcript


def _commands():
    """filter_plugins/live_commands.py, loaded by path as tools/live loads it."""
    name = "claude_on_proxmox_live_commands"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, REPO / "filter_plugins" / "live_commands.py")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


def test_a_save_prunes_only_the_states_a_run_left_behind(tmp_path):
    # LIVE_DIR comes from the environment, so it may be a folder of another tool's: its files are never touched.
    live = tmp_path / "live"
    live.mkdir()
    left = live / "20261020-101010-lint-4242.json"
    events = live / "20261020-101010-test-4243-pytest-a1b2c3d4.jsonl"
    temporary = live / ".state-k2j3h4g5"
    fresh = live / "20261026-101010-test-77.json"
    theirs = [live / "settings.json", live / "20261020-101010-lint.json", live / "notes.state-x", live / "cache"]
    for path in (left, events, temporary, fresh, *theirs[:-1]):
        path.write_text("{}")
    theirs[-1].mkdir()
    (live / "20261020-101011-lint-5.json").symlink_to(theirs[0])
    two_days_ago = time.time() - 2 * 24 * 3600
    for path in (left, events, temporary, *theirs):
        os.utime(path, (two_days_ago, two_days_ago))
    _commands().save_state(live / "20261027-090000-lint-1.json", {"version": 1})
    assert not left.exists() and not events.exists() and not temporary.exists()
    assert (
        fresh.exists() and all(path.exists() for path in theirs) and (live / "20261020-101011-lint-5.json").is_symlink()
    )
    assert json.loads((live / "20261027-090000-lint-1.json").read_text()) == {"version": 1}


def test_the_pytest_reader_keeps_its_events_beside_the_run_s_state(tmp_path):
    # Every test's ID and each failure's words: in the run's own 0700 directory, named so the prune knows them, never
    # in the system's temporary directory.
    commands = _commands()
    state = tmp_path / "live" / "20261027-090000-test-77.json"
    part = type("Part", (), {"state_path": str(state)})()
    reader = commands.Pytest(part)
    events = Path(reader.env()["CLAUDE_ON_PROXMOX_LIVE_PYTEST"])
    assert events.parent == state.parent and commands.OWN.fullmatch(events.name), events
    assert stat.S_IMODE(events.stat().st_mode) == 0o600 and stat.S_IMODE(state.parent.stat().st_mode) == 0o700


def test_ctrl_c_ends_the_part_with_the_child_s_last_words_logged_and_the_run_said_stopped(tmp_path):
    waits = (
        "import signal, sys, time; print('waiting', flush=True); "
        "signal.signal(signal.SIGINT, lambda *_: (print('child got INT', flush=True), sys.exit(130))); time.sleep(60)"
    )
    argv, environment, state = _recipe(tmp_path, [_part("waits", waits)])
    sent = []

    def interrupt(chunk, output, master, process, screen):
        # Once the child said it waits - its last line, dim after the step's task - and not before it has started.
        if not sent and b"waiting" in output:
            sent.append(os.write(master, b"\x03"))

    ran = kit.at_a_terminal(argv, tmp_path, environment, act=interrupt, timeout=60)
    assert ran.rc == 130, ran.text
    ran.assert_drawn_and_gone()
    assert "child got INT" in ran.log
    head = r"^claude-on-proxmox  lint · stopped after \d+ s at waits · "
    assert re.search(head, ran.transcript, re.M), ran.transcript
    assert ran.screen.titles[-1] == "lint · stopped"


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP], ids=["SIGTERM", "SIGHUP"])
def test_a_run_ended_from_outside_ends_its_child_and_gives_the_echo_back(tmp_path, signum):
    # A kill, a timeout or the tab closing, while a silent command runs: it has no terminal to be signalled through.
    beats = tmp_path / "beats"
    script = tmp_path / "silent.py"
    script.write_text(
        "import time\nprint('waiting', flush=True)\nfor _ in range(100):\n"
        f"    with open({str(beats)!r}, 'a') as out:\n        out.write(f'{{time.time()}}\\n')\n    time.sleep(0.2)\n"
    )
    argv, environment, state = _recipe(tmp_path, [f'live run lint --step silent -- "{sys.executable}" "{script}"'])
    sent, echo = [], []

    def signal_the_run(chunk, output, master, process, screen):
        if not sent and b"waiting" in output:
            sent.append(time.time())
            os.killpg(process.pid, signum)
        elif sent:
            with contextlib.suppress(termios.error, OSError):
                echo.append(bool(termios.tcgetattr(master)[3] & termios.ECHO))

    ran = kit.at_a_terminal(argv, tmp_path, environment, act=signal_the_run, timeout=60)
    assert sent, ran.text
    time.sleep(1)
    assert float(beats.read_text().split()[-1]) - sent[0] < 1, "the command ended with the run"
    assert echo and echo[-1], echo
    # Said stopped: by the recipe's end where its shell lived to run its trap, else in the state it left.
    if state.exists():
        assert json.loads(state.read_text())["stopped"] is True
    else:
        assert re.search(r"lint · stopped after", ran.transcript), ran.transcript


# A Ctrl-C that came while nothing could take it: blocked, as tools/live blocks it across its re-run, and pending.
BLOCKED = (
    "import os, signal, sys; signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT}); "
    "os.kill(os.getpid(), signal.SIGINT); os.execv(sys.executable, [sys.executable, *sys.argv[1:]])"
)


def test_a_ctrl_c_before_the_part_s_handler_is_taken_once_it_is_in_and_the_part_runs_nothing(tmp_path):
    # The re-run through tools/run-logged, its Ctrl-C held across the exec: the part says it stopped, and its
    # command never runs.
    logged = (
        f'"{sys.executable}" -c "{BLOCKED}" "{LIVE}" --log-dir="{tmp_path / "logs"}" --stamp={STAMP} --logged '
        f'run lint --step held -- "{sys.executable}" -c "print(\'ran\')"'
    )
    argv, environment, state = _recipe(tmp_path, [_part("says", SAYS), logged])
    ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=60)
    assert ran.rc == 130, ran.text
    assert "| ran" not in ran.log and re.search(r"^claude-on-proxmox  lint · stopped after", ran.transcript, re.M)


def test_a_part_whose_command_a_signal_ended_or_that_cannot_run_exits_as_a_shell_says_it(tmp_path):
    killed = "import os, signal; print('ending', flush=True); os.kill(os.getpid(), signal.SIGTERM)"
    for parts, rc, said in (
        ([_part("killed", killed)], 143, r"^✗ killed: python\S* \(.*\) · exit 143$"),
        ([f'live run lint --step missing -- "{tmp_path / "missing"}"'], 127, r"^✗ missing: missing \(.*\) · exit 127$"),
    ):
        argv, environment, state = _recipe(tmp_path, parts)
        ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=60)
        assert ran.rc == rc and re.search(said, ran.transcript, re.M), ran.transcript


def test_a_run_whose_end_fails_exits_with_its_part_s_status_all_the_same(tmp_path):
    # The justfile's trap: an end that cannot say the run - here a state it cannot read - changes nothing of how
    # the recipe ends.
    unreadable = json.dumps({"version": _commands().view.STATE_VERSION, "title": "lint", "tasks": 1})
    broken = f"printf %s '{unreadable}' > \"$CLAUDE_ON_PROXMOX_LIVE_STATE\""
    argv, environment, state = _recipe(tmp_path, [_part("says", SAYS), broken, "exit 3"])
    ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=60)
    assert ran.rc == 3, ran.text
    assert "Traceback" in ran.text, "the end did fail"


def test_no_bar_falls_from_one_part_to_the_next():
    view = _commands().view
    run = view.Run("lint", 0.0)
    run.parts = True
    assert run._clamped("title", 0.6) == 0.6
    later = view.Run("lint", 5.0)
    later.adopt(run.dump(5.0, {}, []), 5.0)
    assert later._clamped("title", 0.2) == 0.6


PAUSED = """
- name: A playbook part a Ctrl-C ends before its stats
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Wait on something held
      ansible.builtin.command: sleep 30
      changed_when: false
"""


def test_a_playbook_part_ctrl_c_ended_before_its_stats_saves_what_it_did_said_stopped(tmp_path):
    (tmp_path / "part.yml").write_text(PAUSED)
    (tmp_path / "hosts").write_text(f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n")
    playbook = f'"{kit.ANSIBLE_PLAYBOOK}" -i "{tmp_path / "hosts"}" "{tmp_path / "part.yml"}"'
    argv, environment, state = _recipe(tmp_path, [_part("says", SAYS), playbook])
    seen = []

    def interrupt(chunk, output, master, process, screen):
        # Once the task has run a moment, its worker forked: a Ctrl-C while Ansible forks one is lost.
        if b"Wait on something held" in output and not seen:
            seen.append(time.monotonic())
        elif len(seen) == 1 and time.monotonic() - seen[0] > 1.5:
            seen.append(os.write(master, b"\x03"))

    ran = kit.at_a_terminal(argv, tmp_path, environment, act=interrupt, timeout=90)
    assert len(seen) == 2 and ran.rc != 0, ran.text
    screen = ran.transcript
    assert re.search(r"^claude-on-proxmox  lint · stopped after", screen, re.M), screen
    # What it did is said: the first part done, and the playbook's stage, which Ctrl-C stopped.
    assert re.search(r"^ ✓ lint .* says ", screen, re.M) and re.search(r"^ ■ run ", screen, re.M), screen
    assert "Wait on something held" in screen and not state.exists(), screen


def test_without_a_state_the_end_says_nothing(tmp_path):
    env = {name: value for name, value in os.environ.items() if name != "CLAUDE_ON_PROXMOX_LIVE_STATE"}
    env["CLAUDE_ON_PROXMOX_LIVE_STATE"] = str(tmp_path / "none.json")
    ran = subprocess.run([sys.executable, str(LIVE), "end", "0"], capture_output=True, text=True, env=env, check=False)
    assert ran.returncode == 0 and ran.stdout == ran.stderr == ""


PLAYBOOK = """
- name: A playbook as a part
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Say what is left
      ansible.builtin.debug:
        msg: "alpha: something is left to do"
    - name: Keep it for the end of the run
      ansible.builtin.set_stats:
        data:
          claude_vm_next:
            alpha:
              vm: "alpha: something is left to do"
"""


def test_a_playbook_is_a_part_too_and_its_next_steps_wait_for_the_end(tmp_path):
    (tmp_path / "part.yml").write_text(PLAYBOOK)
    (tmp_path / "hosts").write_text(f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n")
    playbook = f'"{kit.ANSIBLE_PLAYBOOK}" -i "{tmp_path / "hosts"}" "{tmp_path / "part.yml"}"'
    argv, environment, state = _recipe(tmp_path, [_part("says", SAYS), playbook])
    ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=120)
    assert ran.rc == 0, ran.text
    ran.assert_drawn_and_gone()
    screen = ran.transcript
    # The playbook's NEXT STEPS once, after the run's one summary, which has both parts.
    assert screen.count("##  NEXT STEPS") == 1 and screen.count("claude-on-proxmox  lint · done in") == 1, screen
    assert screen.index("claude-on-proxmox  lint · done in") < screen.index("##  NEXT STEPS")
    assert "says" in screen.split("claude-on-proxmox  lint · done in", 1)[1]
    assert stat.S_IMODE(os.stat(tmp_path).st_mode) and not state.exists()


def test_a_second_ctrl_c_ends_a_part_whose_childs_group_will_not(tmp_path):
    # The child ends at the first Ctrl-C, which the part sends its group; a grandchild that ignores it still holds
    # the pipe, until the second ends everything the child started: its group, and a grandchild in a session of its
    # own - ansible-core's task worker, a nested run's part - which outlives the group.
    detached = tmp_path / "detached"
    holds = (
        "import signal, subprocess, sys; print('waiting', flush=True); "
        "subprocess.Popen(['sh', '-c', 'trap \\\"\\\" INT; sleep 60']); "
        "apart = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True); "
        f"open('{detached}', 'w').write(str(apart.pid)); "
        "signal.signal(signal.SIGINT, lambda *_: sys.exit(130)); signal.pause()"
    )
    argv, environment, state = _recipe(tmp_path, [_part("holds", holds)])
    sent = []

    def interrupt(chunk, output, master, process, screen):
        if b"waiting" in output and len(sent) < 2 and time.monotonic() - (sent[-1] if sent else 0) > 1:
            os.write(master, b"\x03")
            sent.append(time.monotonic())

    started = time.monotonic()
    ran = kit.at_a_terminal(argv, tmp_path, environment, act=interrupt, timeout=60)
    assert ran.rc == 130 and time.monotonic() - started < 30, ran.text
    assert re.search(r"lint · stopped after", ran.transcript), ran.transcript
    apart = int(detached.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(apart, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(apart, signal.SIGKILL)
        pytest.fail("the grandchild in a session of its own outlived the second Ctrl-C")


def test_a_command_that_asks_at_the_terminal_fails_at_once_rather_than_stopping(tmp_path):
    # git or ssh asking for a password at /dev/tty, as pre-commit fetches a hook: the part has no terminal to give it.
    asks = "import sys; print('asking', flush=True); open('/dev/tty').read(1); print('answered')"
    argv, environment, state = _recipe(tmp_path, [_part("asks", asks)])
    started = time.monotonic()
    ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=30)
    assert ran.rc == 1 and time.monotonic() - started < 20, ran.text
    assert re.search(r"^✗ asks: python\S* \(.*\) · exit 1$", ran.transcript, re.M), ran.transcript
    assert "/dev/tty" in ran.transcript and "| answered\n" not in ran.log


def test_a_part_that_passed_with_warnings_says_them_above(tmp_path):
    warns = (
        "print('checked 3 files'); print('WARNING  2 rules were skipped'); "
        "print('[WARNING]: Could not match supplied host pattern, ignoring: claude_vms'); "
        "print('WARNING  Skipped installing collection dependencies due to running in offline mode.'); "
        "print('Passed: 0 failure(s), 0 warning(s) in 223 files processed.'); print('done')"
    )
    argv, environment, state = _recipe(tmp_path, [_part("warns", warns)])
    ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=60)
    assert ran.rc == 0, ran.text
    assert re.search(r"^! warns: python\S* passed with warnings$", ran.transcript, re.M), ran.transcript
    assert "    WARNING  2 rules were skipped" in ran.transcript and "checked 3 files" not in ran.transcript
    # What every lint says of warnings, which nobody can act on, is left in the log.
    assert "Could not match" not in ran.transcript and "offline mode" not in ran.transcript
    assert "0 warning(s)" not in ran.transcript


def test_a_command_is_named_by_its_program_past_env(tmp_path):
    named = _part("named", SAYS).replace("-- ", "-- env NO_PROMPT=1 ", 1)
    # A shell running the command names nothing: the step does.
    shelled = _part("fails", FAILS).replace("-- ", '-- bash -c \'exec "$0" "$@"\' ', 1)
    argv, environment, state = _recipe(tmp_path, [named, shelled])
    ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=60)
    table = ran.transcript.split("TIME", 1)[1].split("\n\n", 1)[0]
    assert "env" not in table and "SLOWEST  env" not in ran.transcript, table
    assert re.search(r"^✗ fails: fails \(", ran.transcript, re.M), ran.transcript


def test_a_playbook_part_ctrl_c_stopped_is_said_stopped(tmp_path):
    # ansible-playbook exits 99 after Ctrl-C: the run stopped, it did not fail.
    argv, environment, state = _recipe(tmp_path, [_part("says", SAYS), "exit 99"])
    ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=60)
    assert ran.rc == 99, ran.text
    assert re.search(r"^claude-on-proxmox  lint · stopped after \d+ s at says", ran.transcript, re.M), ran.transcript


def test_a_run_that_ends_well_before_its_listed_steps_is_said_stopped(tmp_path):
    # A Ctrl-C between two parts can end the recipe with bash saying 0: the steps it listed and never reached say so.
    listed = _part("says", SAYS).replace("--step says", '--step says --plan "says next"', 1)
    argv, environment, state = _recipe(tmp_path, [listed, "exit 0"])
    ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=60)
    assert ran.rc == 0, ran.text
    assert re.search(r"^claude-on-proxmox  lint · stopped after \d+ s at says", ran.transcript, re.M), ran.transcript


RECORDED = """
- name: A part that records what is left as just init does
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Keep what is left to do for the end of the run
      ansible.builtin.set_stats:
        data:
          claude_vm_next:
            just init:
              init_settings: "{{ settings }}"
              init_check: "{{ check }}"
      vars:
        settings: |-
          Still to set:
            the Proxmox host address: just init asks again, or edit inventory/hosts.yml
          %s
        check: |-
          just init found 2 problems for just deploy: fix them, then run just init again.
            !!   the token was refused
            !!   root SSH failed
"""
LONG = (
    "Nothing is asked without a terminal, or with --no-prompt: run just init at one, give the values on its command"
    " line (just init --host ... in docs/getting-started/first-deploy.md), or edit the files and run just vault."
)
# Colour, a title and a bell: what a terminal acts on and does not show.
ESCAPES = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x07")


def _next_steps(text):
    """NEXT STEPS' lines as printed, colour aside: under its block's rule, up to the first blank line."""
    after = ESCAPES.sub("", text).replace("\r\n", "\n").split("##  NEXT STEPS\n", 1)[1].split("\n", 1)[1]
    return list(itertools.takewhile(str.strip, after.splitlines()))


def test_next_steps_says_each_record_line_for_line_as_the_stream_does(tmp_path):
    # just init's records: bullets under the line that counts them, and a paragraph longer than any terminal, whole.
    (tmp_path / "part.yml").write_text(RECORDED % LONG)
    (tmp_path / "hosts").write_text(f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n")
    playbook = f'"{kit.ANSIBLE_PLAYBOOK}" -i "{tmp_path / "hosts"}" "{tmp_path / "part.yml"}"'
    argv, environment, state = _recipe(tmp_path, [_part("says", SAYS), playbook])
    ran = kit.at_a_terminal(argv, tmp_path, environment, timeout=120)
    assert ran.rc == 0, ran.text
    expected = [
        "Still to set:",
        "  the Proxmox host address: just init asks again, or edit inventory/hosts.yml",
        LONG,
        "just init found 2 problems for just deploy: fix them, then run just init again.",
        "  !!   the token was refused",
        "  !!   root SSH failed",
    ]
    assert _next_steps(ran.text) == _next_steps(ran.log) == expected, ran.text
