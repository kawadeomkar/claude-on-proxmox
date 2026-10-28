"""filter_plugins/processes.py, and what ends a run's whole tree: the harness's _end, and a pytest process ended by
SIGTERM or SIGHUP (tests/unit/interrupts.py).

ansible-core's task worker detaches into a session of its own (WorkerProcess._detach: setsid, stdio on /dev/null), so
a kill of a playbook's process group ended the playbook and left the task in flight behind, reparented to PID 1 and
idle for good - 46 of them after a suite that hung (2026-10-27). A kill must find the tree before it signals anything.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from harness import ANSIBLE_PLAYBOOK, REPO, _default_signals, _end, _env, _inventory, _processes

processes = _processes()

# A child in a group of its own, with a grandchild in a session of its own: the shape of a playbook and its task worker.
# Each pid to the file named, then both wait.
HOLDS = (
    "import os, subprocess, sys, time\n"
    "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True)\n"
    "open(sys.argv[1], 'w').write(f'{os.getpid()} {grandchild.pid}')\n"
    "time.sleep(120)\n"
)
# A playbook with a task in flight: a mark that it got there, then a pause - the task whose worker a kill must reach.
WAITS = """- hosts: all
  gather_facts: false
  tasks:
    - name: mark the start
      ansible.builtin.file:
        path: {marker}
        state: touch
    - name: a task in flight when the run is killed
      ansible.builtin.pause:
        seconds: 120
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _until(condition, what: str, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail(f"not within {timeout} s: {what}")
        time.sleep(0.1)


def _child_and_grandchild(tmp_path: Path) -> tuple[subprocess.Popen, int]:
    pids = tmp_path / "pids"
    child = subprocess.Popen([sys.executable, "-c", HOLDS, str(pids)], process_group=0)
    _until(lambda: pids.exists() and len(pids.read_text().split()) == 2, "the grandchild's pid written")
    return child, int(pids.read_text().split()[1])


def _leads(pid: int) -> bool:
    """Whether pid leads a session of its own, as a detached worker does; False once gone, as the first task's is."""
    try:
        return os.getpgid(pid) == pid
    except ProcessLookupError:
        return False


def _in_flight(playbook: int, marker: Path) -> int:
    """The pause's worker, once the playbook has marked its start: the process under it that leads a session, and
    still does a moment later - the mark's own worker ends within that moment, the pause's waits."""
    _until(marker.exists, "the playbook's first task done")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        leaders = [pid for pid in processes.descendants(playbook) if _leads(pid)]
        time.sleep(0.3)
        if leaders and _leads(leaders[-1]):
            return leaders[-1]
    pytest.fail("the task's worker never forked")


def _by_argv(word: str) -> list[int]:
    """The pids of every process whose command line holds word, this one and ps aside."""
    table = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    found = []
    for line in table.splitlines():
        pid, _, command = line.strip().partition(" ")
        if word in command and int(pid) != os.getpid() and not command.startswith("ps "):
            found.append(int(pid))
    return found


def test_descendants_lists_a_grandchild_in_a_session_of_its_own(tmp_path):
    child, grandchild = _child_and_grandchild(tmp_path)
    try:
        assert processes.descendants(child.pid) == [grandchild]
        assert os.getpgid(grandchild) == grandchild != os.getpgid(child.pid)
    finally:
        processes.end(child.pid)
        child.wait()
    _until(lambda: not _alive(grandchild), "the grandchild gone")


def test_a_kill_of_the_group_leaves_the_grandchild_behind_and_end_does_not(tmp_path):
    # What the harness did: the child's group ends, the grandchild lives on under PID 1.
    child, grandchild = _child_and_grandchild(tmp_path)
    os.killpg(child.pid, signal.SIGKILL)
    assert child.wait() == -signal.SIGKILL
    try:
        time.sleep(0.5)
        assert _alive(grandchild)
        parent = subprocess.run(["ps", "-o", "ppid=", "-p", str(grandchild)], capture_output=True, text=True).stdout
        assert parent.strip() == "1", parent
    finally:
        processes.end(grandchild)
    _until(lambda: not _alive(grandchild), "the grandchild gone")
    # And the same tree, ended whole: nothing of it is left.
    (tmp_path / "again").mkdir()
    child, grandchild = _child_and_grandchild(tmp_path / "again")
    signalled = processes.end(child.pid)
    child.wait()
    assert signalled == [child.pid, grandchild]
    _until(lambda: not _alive(grandchild), "the grandchild gone with the child")


def test_end_signals_a_child_of_this_group_alone_and_never_the_group(tmp_path):
    # A fake started as _start starts one, in this process's own group: ending it must not end this process.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    assert os.getpgid(child.pid) == os.getpgrp()
    assert processes.end(child.pid) == [child.pid]
    assert child.wait() == -signal.SIGKILL


def test_end_of_a_process_that_is_gone_signals_nothing_and_raises_nothing(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert processes.end(child.pid) == [child.pid]


def test_the_harness_end_leaves_no_task_worker_of_a_killed_playbook_behind(tmp_path):
    (tmp_path / "waits.yml").write_text(WAITS.format(marker=tmp_path / "started"))
    inventory = _inventory(tmp_path, {"alpha": {"ansible_connection": "local"}}, False)
    process = subprocess.Popen(
        [str(ANSIBLE_PLAYBOOK), "-i", str(inventory), str(tmp_path / "waits.yml")],
        cwd=tmp_path,
        env=_env(tmp_path),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        process_group=0,
    )
    try:
        # The task's worker, forked and detached into a session of its own: the shape a kill of the group misses.
        worker = _in_flight(process.pid, tmp_path / "started")
        assert os.getpgid(worker) != process.pid
    finally:
        _end(process)
    assert process.returncode == -signal.SIGKILL
    _until(lambda: not _alive(worker), "the task's worker gone with the playbook")
    assert not _by_argv(str(tmp_path / "waits.yml"))


def test_install_takes_a_signal_as_a_ctrl_c_but_leaves_one_ignored_on_the_way_in(tmp_path):
    # nohup's SIGHUP: a suite started under it must still survive its terminal closing.
    import interrupts

    before = {signum: signal.getsignal(signum) for signum in interrupts.ENDINGS}
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        interrupts.install()
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
        assert signal.getsignal(signal.SIGTERM) is interrupts._as_a_ctrl_c
    finally:
        for signum, handler in before.items():
            signal.signal(signum, handler)


def test_a_remembered_pid_is_signalled_only_while_it_is_an_orphan(tmp_path):
    # What a part remembered of its child's tree: a pid under PID 1 is ended; one another process has by now - any
    # other parent - is left alone, this process's own child standing in for it.
    child, grandchild = _child_and_grandchild(tmp_path)
    os.killpg(child.pid, signal.SIGKILL)
    child.wait()
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        signalled = processes.end(child.pid, grandchild, bystander.pid)
        assert grandchild in signalled and bystander.pid not in signalled
        _until(lambda: not _alive(grandchild), "the orphaned grandchild gone")
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()


SUITE_CONFTEST = "import interrupts\n\n\ndef pytest_configure(config):\n    interrupts.install()\n"
SUITE_TEST = """from pathlib import Path

import pytest
from harness import _run


@pytest.mark.parametrize("n", [1, 2])
def test_waits(tmp_path, n):
    _run(tmp_path, Path(__file__).with_name(f"waits{n}.yml"), {"alpha": {"ansible_connection": "local"}}, timeout=120)
"""


@pytest.mark.parametrize("workers", [0, 2], ids=["one process", "xdist, two workers"])
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP], ids=["SIGTERM", "SIGHUP"])
def test_a_pytest_process_ended_by_a_kill_or_a_hangup_ends_its_run_whole(tmp_path, signum, workers):
    # pytest with a conftest that takes the signal as interrupts.install() has it, sent to its whole group as
    # tools/live sends it: each test's playbook, mid-task, and that task's detached worker are gone once pytest has
    # ended, as interrupted - one process running the tests, or a controller and two workers each holding one.
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "conftest.py").write_text(SUITE_CONFTEST)
    (suite / "test_waits.py").write_text(SUITE_TEST)
    for n in (1, 2):
        (suite / f"waits{n}.yml").write_text(WAITS.format(marker=suite / f"started{n}"))
    env = dict(os.environ, PYTHONPATH=str(REPO / "tests" / "unit"))
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-c",
            str(REPO / "pyproject.toml"),
            "--rootdir",
            str(REPO),
            "-q",
            *(["-n", str(workers)] if workers else []),
            str(suite),
        ],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        preexec_fn=_default_signals,  # noqa: PLW1509 - only signal, safe beside xdist's thread
    )
    held = []
    try:
        # Both playbooks mid-task under xdist, where each worker holds one; the first alone in one process.
        for n in (1, 2) if workers else (1,):
            name = str(suite / f"waits{n}.yml")
            _until(lambda name=name: _by_argv(name), f"playbook {n} started")
            playbook = _by_argv(name)[0]
            held += [playbook, _in_flight(playbook, suite / f"started{n}")]
        os.killpg(process.pid, signum)
        output, _ = process.communicate(timeout=90)
    finally:
        if process.poll() is None:
            processes.end(process.pid)
            process.wait()
    text = output.decode(errors="replace")
    assert process.returncode == 2 and "KeyboardInterrupt" in text, text
    _until(lambda: not any(_alive(pid) for pid in held), "every playbook and its task's worker gone")
    assert not _by_argv(str(suite / "waits"))
