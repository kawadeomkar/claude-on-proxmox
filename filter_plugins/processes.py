"""The processes under one, and their end: what a kill must reach beyond the process group it was sent to.

A playbook's task runs in a worker ansible-core forks and detaches - `WorkerProcess._detach` (2.21): `setsid`, its
stdio on `/dev/null` - a session and process group of its own. A signal to the playbook's group therefore ends the
playbook and leaves the task in flight behind, reparented to PID 1 and idle for good; a part's child (`tools/live`)
runs in a session of its own for the same reason, and what it starts may again. `descendants` reads the table `ps`
prints, which macOS and Linux both have, and must be read before anything is signalled: a dead parent's children
move to PID 1, where nothing leads to them. `end` signals a process, every process under it and each of their
groups but this process's own, so a child started in this group is ended alone, never this process with it.

Loaded by path as claude_on_proxmox_processes (filter_plugins/live_commands.py, tests/unit/harness.py); no filters.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess


def descendants(pid: int) -> list[int]:
    """Every process under pid as ps lists them now, each before its own children; [] when none, or ps fails."""
    try:
        table = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    children: dict[int, list[int]] = {}
    for line in table.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0].isdigit() and fields[1].isdigit():
            children.setdefault(int(fields[1]), []).append(int(fields[0]))
    found: list[int] = []
    queue = [pid]
    while queue:
        for child in children.get(queue.pop(0), []):
            if child not in found and child != pid:
                found.append(child)
                queue.append(child)
    return found


def end(*roots: int, signum: int = signal.SIGKILL) -> list[int]:
    """Signal each process, every process under it and their groups other than this one's; the pids signalled.

    Several roots for a tree remembered before part of it ended: a child that exits at a Ctrl-C leaves its own
    children, those in sessions of their own, under PID 1, where a walk from the child no longer finds them.
    """
    pids: list[int] = []
    for root in roots:
        pids.extend(each for each in (root, *descendants(root)) if each not in pids)
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
