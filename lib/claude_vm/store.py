"""The core's one module that writes a file that persists: a file replaced whole and atomically, under a lock held
across the read and the write, so that of two writers neither loses the other's change. Only tools/ import it, never
a plugin (docs/architecture/custom-code.md §7.10)."""

from __future__ import annotations

import contextlib
import fcntl
import os
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path


@contextlib.contextmanager
def locked(path: Path | str) -> Iterator[None]:
    """An exclusive lock on path's folder for the block: every writer through this module waits for the one before.
    The folder rather than a lock file beside path, so nothing is left in it; closing releases it."""
    folder = os.open(Path(path).parent, os.O_RDONLY)
    try:
        fcntl.flock(folder, fcntl.LOCK_EX)
        yield
    finally:
        os.close(folder)


def replace(path: Path | str, text: str, mode_if_new: int | None = None) -> None:
    """path replaced by text atomically: written beside it, flushed to disk, then renamed over it, with path's own
    mode, never leaving a scratch file behind. Through a symlink, the file it names is the one replaced, so the link
    stays a link. A path that does not exist yet is made with ``mode_if_new``, where it is given."""
    path = Path(os.path.realpath(path))
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        if mode_if_new is None:
            raise
        mode = mode_if_new
    handle, scratch = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(scratch, mode)
        os.replace(scratch, path)
    except BaseException:
        Path(scratch).unlink(missing_ok=True)
        raise
