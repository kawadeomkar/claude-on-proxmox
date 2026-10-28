"""SIGTERM and SIGHUP taken as a Ctrl-C, in every pytest process: the controller's and each xdist worker's.

Python ends a process at once on either, so a worker that got one - from `tools/live`, which sends its part's group
SIGTERM or SIGHUP when `just test` is killed or its terminal closes, from CI's cancel, from a kill by hand - died with
the playbook it was running and that playbook's own tree left behind: the harness ends them only on its way out of
the test (`_end`, in `_run`'s and the pseudo-terminal's `except BaseException`). Raised as a KeyboardInterrupt, the
signal takes that way out: the test's run ended with everything under it, the fixtures torn down, pytest ending as
interrupted; one ignored on the way in, SIGHUP under nohup, is left ignored. `tests/unit/conftest.py` installs it at
configure time; a test of it runs pytest with a conftest of its
own that does the same (`test_processes.py`).
"""

from __future__ import annotations

import signal
import threading

ENDINGS = (signal.SIGTERM, signal.SIGHUP)


def _as_a_ctrl_c(signum, frame):
    raise KeyboardInterrupt


def install() -> None:
    """The handlers, in the main thread only, where Python delivers signals and a KeyboardInterrupt can be raised; a
    signal ignored on the way in stays ignored, as nohup leaves SIGHUP, so a suite started under it still survives
    its terminal closing - an xdist worker inherits the controller's dispositions, so each decides the same."""
    if threading.current_thread() is not threading.main_thread():
        return
    for signum in ENDINGS:
        if signal.getsignal(signum) != signal.SIG_IGN:
            signal.signal(signum, _as_a_ctrl_c)
