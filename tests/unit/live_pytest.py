"""pytest's run as `just test` draws it: each test's start and end, as JSON lines, for tools/live's pytest reader.

`just test` runs pytest with `-p live_pytest`, which pytest finds on its pythonpath (tests/unit). With
CLAUDE_ON_PROXMOX_LIVE_PYTEST naming a file - tools/live sets it for a drawn run, and nothing else does - each event is
a line appended to it: how many tests there are, once collected; each test as it starts; each test's outcome as it
ends, with what a failure said. Without it the plugin does nothing, so a run piped, in CI or by hand is pytest's own.
Under pytest-xdist the workers write nothing: the controller collects nothing itself, so the count comes from the
first worker's collection (pytest_xdist_node_collection_finished, a hook only xdist has), and xdist relays each
test's start and report to it (docs/architecture/custom-code.md §7.8).
"""

from __future__ import annotations

import json
import os

import pytest

EVENTS = "CLAUDE_ON_PROXMOX_LIVE_PYTEST"
_run = {"out": None, "counted": False, "config": None}


def _emit(**event):
    config = _run["config"]
    path = os.environ.get(EVENTS, "")
    # Only the process that reports: under xdist a worker's events reach the controller, which writes them.
    if not path or config is None or hasattr(config, "workerinput"):
        return
    if _run["out"] is None:
        _run["out"] = open(path, "a", buffering=1)  # noqa: SIM115 - one file for the session, a line at a time
    _run["out"].write(json.dumps(event) + "\n")


def _count(count):
    if not _run["counted"]:
        _run["counted"] = True
        _emit(count=count)


def _distributed(config):
    """Whether xdist runs the tests in workers, so that this process collects none of them."""
    try:
        return bool(config.getoption("numprocesses"))
    except ValueError:
        return False


def pytest_sessionstart(session):
    _run["config"] = session.config


def pytest_collection_finish(session):
    if not _distributed(session.config):
        _count(len(session.items))


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids):
    _count(len(ids))


def pytest_runtest_logstart(nodeid, location):
    _emit(start=nodeid)


def pytest_runtest_logreport(report):
    # A test ends at its call, or at a setup that failed or skipped it; a teardown that fails fails it too.
    failed = report.outcome == "failed"
    ends = report.when == "call" or (report.when == "setup" and report.outcome != "passed")
    if ends or (failed and report.when == "teardown"):
        _emit(end=report.nodeid, outcome=report.outcome, said=report.longreprtext if failed else "")
