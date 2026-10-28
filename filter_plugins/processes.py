"""The processes under one, and their end: what a kill must reach beyond the process group it was sent to.

A playbook's task runs in a worker ansible-core forks and detaches - `WorkerProcess._detach` (2.21): `setsid`, its
stdio on `/dev/null` - a session and process group of its own. A signal to the playbook's group therefore ends the
playbook and leaves the task in flight behind, reparented to PID 1 and idle for good; a part's child (`tools/live`)
runs in a session of its own for the same reason, and what it starts may again. `descendants` reads the table `ps`
prints, which macOS and Linux both have, and must be read before anything is signalled: a dead parent's children
move to PID 1, where nothing leads to them. `end` signals a process, every process under it and each of their
groups but this process's own, so a child started in this group is ended alone, never this process with it; a pid
remembered from before part of the tree ended is signalled only while it is under PID 1, never once another
process has it.

Loaded by path as claude_on_proxmox_processes (filter_plugins/live_commands.py, tests/unit/harness.py); no filters.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess


def _parents() -> dict[int, int]:
    """Every process by pid, with its parent's, as ps lists them now; {} when ps fails."""
    try:
        table = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return {}
    parents = {}
    for line in table.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0].isdigit() and fields[1].isdigit():
            parents[int(fields[0])] = int(fields[1])
    return parents


def descendants(pid: int, parents: dict[int, int] | None = None) -> list[int]:
    """Every process under pid as ps lists them now, each before its own children; [] when none, or ps fails."""
    parents = _parents() if parents is None else parents
    children: dict[int, list[int]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)
    found: list[int] = []
    queue = [pid]
    while queue:
        for child in children.get(queue.pop(0), []):
            if child not in found and child != pid:
                found.append(child)
                queue.append(child)
    return found


def end(pid: int, *remembered: int, signum: int = signal.SIGKILL) -> list[int]:
    """Signal a process, every process under it and their groups other than this one's; the pids signalled.

    remembered: what was under pid before part of it ended - a child that exits at a Ctrl-C leaves its own children,
    those in sessions of their own, under PID 1, where a walk from the child no longer finds them. Each is signalled,
    with what is under it, only while it is under PID 1 or still under pid: any other parent means the pid is another
    process's by now, reused, and none of this tree's.
    """
    parents = _parents()
    pids = [pid, *descendants(pid, parents)]
    for each in remembered:
        if each not in pids and parents.get(each) == 1:
            pids.extend(found for found in (each, *descendants(each, parents)) if found not in pids)
    own = os.getpgrp()
    groups = set()
    for each in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            groups.add(os.getpgid(each))
    for group in groups - {own}:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(group, signum)
    for each in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(each, signum)
    return pids


class FilterModule:
    """No filters: the engine and the harness load this module by path. Here so the filter loader takes the file."""

    def filters(self):
        return {}
