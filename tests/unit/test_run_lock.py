"""The run lock: one run that changes something at a time, from a terminal or an agent through MCP.

deploy (but --check), login, code, destroy, init and install take it, as the first line after their rules, through
tools/check lock: a record of the recipe's script - its pid, which the script keeps as it execs into the run, that
process's start, in C and UTC, and the run's title - written O_EXCL. It is the holder's while that pid runs the process
of that start, and taken over once it does not, never by age; a recipe run inside the holder's takes none; nothing is an
fd lock a child could inherit. just clean refuses while a live run holds it.

These run the real just against test_justfile.py's stand-ins, the lock in tmp_path (RUN_LOCK=); the lock is off in
every other test of a recipe, which would otherwise take turns in this checkout's .cache.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from test_justfile import JUST, REPO, _env, _just, _output, _recording, _runs, _stand_in

CHECK = REPO / "tools" / "check"


def _lock(tmp_path: Path) -> str:
    return f"RUN_LOCK={tmp_path / 'run.lock'}"


def _holding(tmp_path: Path, *args: str) -> subprocess.Popen:
    """A recipe run whose stand-in ansible-playbook waits until <tmp>/release exists: it holds the lock meanwhile."""
    release = tmp_path / "release"
    settings = [*_recording(tmp_path, f'while [ ! -e "{release}" ]; do sleep 0.1; done'), _lock(tmp_path)]
    process = subprocess.Popen(
        [JUST, *settings, *args],
        cwd=REPO,
        env=_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(300):
        if _runs(tmp_path, "ansible-playbook"):
            return process
        time.sleep(0.1)
    process.kill()
    raise AssertionError(f"{args} never reached its playbook: {process.communicate()}")


def _release(tmp_path: Path, process: subprocess.Popen) -> None:
    (tmp_path / "release").touch()
    out, err = process.communicate(timeout=60)
    assert process.returncode == 0, out + err


def test_a_second_run_that_changes_something_is_refused_with_what_holds_the_lock(tmp_path):
    held = _holding(tmp_path, "deploy", "alpha")
    try:
        # Each beside it in a folder of its own: the holder's stand-in is a script still running.
        for n, args in enumerate((["login", "beta"], ["deploy", "beta"], ["destroy", "beta"], ["code", "beta"])):
            result = _just(*args, settings=[*_recording(tmp_path / f"run{n}"), _lock(tmp_path)])
            assert result.returncode != 0 and "just deploy alpha is running (pid " in result.stderr, args
            # The pid is the recipe's script, just's child, which it keeps as it execs into the run.
            holder = int(result.stderr.split("(pid ")[1].split(",")[0])
            assert _parent(holder) == held.pid, _output(result)
        # Nothing that only reads, or only tries, takes it.
        for n, args in enumerate((["deploy", "--check", "alpha"], ["status"], ["logs"])):
            result = _just(*args, settings=[*_recording(tmp_path / f"read{n}"), _lock(tmp_path)])
            assert "is running (pid" not in result.stderr, (args, _output(result))
    finally:
        _release(tmp_path, held)
    # Its process gone, the record is stale: the next run takes it over.
    result = _just("login", "beta", settings=[*_recording(tmp_path / "after"), _lock(tmp_path)])
    assert result.returncode == 0, _output(result)
    assert (tmp_path / "run.lock").read_text().splitlines()[2] == "login beta"


def _parent(pid: int) -> int:
    return int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout)


def _record(path: Path, pid: int, since: str, title: str = "deploy gamma") -> None:
    path.write_text(f"{pid}\n{since}\n{title}\n\n")


def _started(pid: int) -> str:
    """A start as tools/check lock writes it: in C and UTC, its spaces collapsed."""
    env = {**os.environ, "LC_ALL": "C", "TZ": "UTC0"}
    return " ".join(
        subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, env=env).stdout.split()
    )


def test_a_record_whose_process_is_gone_or_was_another_is_taken_over(tmp_path):
    lock = tmp_path / "run.lock"
    gone = subprocess.Popen(["true"])
    gone.wait()
    _record(lock, gone.pid, "Thu Jan  1 00:00:00 1970")
    result = _just("login", "beta", settings=[*_recording(tmp_path), _lock(tmp_path)])
    assert result.returncode == 0, _output(result)
    # A live pid, but not the process that wrote the record: another start. Not this test's own pid, an ancestor of
    # the run, which would be the holder's own recipe.
    other = subprocess.Popen(["sleep", "60"])
    try:
        _record(lock, other.pid, "Thu Jan  1 00:00:00 1970")
        result = _just("login", "beta", settings=[*_recording(tmp_path), _lock(tmp_path)])
        assert result.returncode == 0, _output(result)
        # The same pid and start: held.
        _record(lock, other.pid, _started(other.pid))
        result = _just("login", "beta", settings=[*_recording(tmp_path), _lock(tmp_path)])
        assert result.returncode != 0 and "just deploy gamma is running" in result.stderr
    finally:
        other.kill()
        other.wait()


def test_a_lock_is_held_whatever_the_zone_and_language_of_the_run_that_reads_it(tmp_path):
    # ps says a start in local time and the locale's words, which a terminal and an agent's run need not share: the MCP
    # server passes the session's LANG on, and no TZ. Read as they read it, a held lock was free to the other.
    lock = tmp_path / "run.lock"
    holder = subprocess.Popen(["sleep", "60"])
    try:
        taken = subprocess.run(
            [str(CHECK), "lock", str(lock), str(holder.pid), "deploy"],
            env={**os.environ, "TZ": "XYZ-5:30", "LC_ALL": "C"},
            capture_output=True,
            text=True,
        )
        assert taken.returncode == 0, taken.stderr
        assert lock.read_text().splitlines()[1] == _started(holder.pid)
        for zone in ("UTC0", "ABC+11"):
            env = {**os.environ, "TZ": zone, "LANG": "C.UTF-8"}
            unheld = subprocess.run([str(CHECK), "unheld", str(lock)], env=env, capture_output=True, text=True)
            assert unheld.returncode != 0 and "just deploy is running" in unheld.stderr, unheld.stderr
    finally:
        holder.kill()
        holder.wait()


def _gone(lock: Path) -> None:
    gone = subprocess.Popen(["true"])
    gone.wait()
    _record(lock, gone.pid, "Thu Jan  1 00:00:00 1970")


def test_a_stale_record_is_taken_over_by_one_run_alone(tmp_path):
    # Runs that start together all find the record stale: one takes it over and the rest find it held - never two
    # holding it, as when one removed the record another had just written in the stale one's place.
    lock = tmp_path / "run.lock"
    holders = [subprocess.Popen(["sleep", "60"]) for _ in range(8)]
    try:
        for _ in range(5):
            _gone(lock)
            racing = [
                subprocess.Popen(
                    [str(CHECK), "lock", str(lock), str(h.pid), "deploy"], stderr=subprocess.PIPE, text=True
                )
                for h in holders
            ]
            results = [(each.communicate(timeout=30)[1], each.returncode) for each in racing]
            assert [rc for _, rc in results].count(0) == 1, results
            assert not (tmp_path / "run.lock.takeover").exists()
            lock.unlink()
    finally:
        for holder in holders:
            holder.kill()
            holder.wait()


def test_a_takeover_left_half_way_is_said_and_a_record_being_written_never_taken_over(tmp_path):
    lock = tmp_path / "run.lock"
    _gone(lock)
    (tmp_path / "run.lock.takeover").mkdir()
    result = subprocess.run([str(CHECK), "lock", str(lock), str(os.getpid()), "deploy"], capture_output=True, text=True)
    assert result.returncode != 0 and "run.lock.takeover" in result.stderr
    # An empty record is one whose run has created it and not yet written it.
    (tmp_path / "run.lock.takeover").rmdir()
    lock.write_text("")
    result = subprocess.run([str(CHECK), "lock", str(lock), str(os.getpid()), "deploy"], capture_output=True, text=True)
    assert result.returncode != 0 and lock.read_text() == ""


def test_a_recipe_inside_the_holder_s_takes_none_and_a_child_that_outlives_it_holds_nothing(tmp_path):
    lock = tmp_path / "run.lock"
    # The holder's own child - just init's install, run through self - finds its parent holding it.
    script = f'"{CHECK}" lock "{lock}" "$$" init && sh -c \'"{CHECK}" lock "{lock}" "$$" install\''
    assert subprocess.run(["sh", "-c", script], capture_output=True, text=True).returncode == 0
    assert lock.read_text().splitlines()[2] == "init"
    # A holder that leaves a child running past its own end: the record names the holder, so its end frees it.
    script = f'"{CHECK}" lock "{lock}" "$$" deploy && (sleep 30 > /dev/null 2>&1 &)'
    subprocess.run(["sh", "-c", script], check=True, capture_output=True, timeout=20)
    result = subprocess.run([str(CHECK), "lock", str(lock), str(os.getpid()), "login"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_an_empty_run_lock_takes_none(tmp_path):
    assert subprocess.run([str(CHECK), "lock", "", str(os.getpid()), "deploy"]).returncode == 0


def test_clean_refuses_while_a_live_run_holds_the_lock(tmp_path):
    lock = tmp_path / "run.lock"
    _record(lock, os.getpid(), _started(os.getpid()))
    _stand_in(tmp_path / "bin", "ansible-galaxy")
    result = _just("clean", settings=[_lock(tmp_path), f"LOG_DIR={tmp_path / 'logs'}"])
    assert result.returncode != 0 and "is running (pid" in result.stderr
    # Free, tools/check unheld says so: just clean itself is not run here, which would empty this checkout's .cache.
    lock.unlink()
    assert subprocess.run([str(CHECK), "unheld", str(lock)]).returncode == 0


def _body(text: str, recipe: str) -> str:
    """A recipe's script, from its line in the justfile to the blank line after it: never an assignment of its name."""
    match = re.search(rf"^{recipe}( [A-Z*][^\n]*)?:\n", text, flags=re.M)
    return text[match.end() :].split("\n\n", 1)[0] if match else ""


def test_every_recipe_that_changes_something_takes_the_lock_after_its_rules():
    text = (REPO / "justfile").read_text()
    for recipe in ("deploy", "login", "code", "destroy", "init", "install", "host"):
        body = _body(text, recipe)
        assert "{{ take_lock }}" in body, recipe
        if "{{ host_env }}" in body:
            # After the run's host, which its title names, and before every check of what the run needs.
            assert body.index("{{ host_env }}") < body.index("{{ take_lock }}") < body.index("tools/check vault"), (
                recipe
            )
        else:
            assert body.index("{{ take_lock }}") < body.index("{{ find_bin }}") or recipe in ("install", "host"), recipe
    for recipe in ("status", "settings", "logs", "lint", "test", "docs", "vault", "help", "mcp", "vagrant"):
        body = _body(text, recipe)
        assert body and "take_lock" not in body, recipe
