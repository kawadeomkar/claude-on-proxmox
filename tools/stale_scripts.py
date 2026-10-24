"""The locked packages whose commands start a Python other than this environment's own.

uv writes the Python into each command it installs - the first line,
`#!<environment>/bin/python` - spelled as the environment was named at that
install. A sync through another path to the same environment, such as the
`.venv` link into it that `make venv` once synced through, leaves every command
naming that path, and each one breaks when the path goes; a later sync that
finds every package current rewrites none of them. just install runs this with
the environment's own Python before its sync, and reinstalls what it prints,
so those commands name the environment itself again.

Only the packages uv.lock lists are looked at, since only those can just
install reinstall: a command of anything else in the environment - pip, which
made it - stays as it is, or it would be reinstalled on every run and never
change. A command is read only when its first line starts a Python by its
path, so `#!/usr/bin/env python3`, and uv's relocatable form, which names the
Python relative to the command, never count.

Prints `--reinstall-package <name>` for each such package, for uv sync, and
says on stderr which commands named which Python; prints nothing when there
are none. With --check, which just install runs after a reinstall, it fails
instead, naming them: a reinstall that changed nothing would otherwise run
again on every just install.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import re
import sys
import sysconfig
from pathlib import Path

# Python's own since 3.11; ruff, set for the VMs' 3.10, files it apart.
import tomllib

LOCK = Path(__file__).resolve().parent.parent / "uv.lock"
# A Python, by the name a command's first line starts it by.
PYTHON_NAME = re.compile(r"python[0-9.]*")
# The form uv gives a first line too long for one, or with a space in it:
#   #!/bin/sh
#   '''exec' '/path/to/python' "$0" "$@"
SH_EXEC = re.compile(rb"'''exec' '([^']*)' ")


def normalized(name: str) -> str:
    """A package name as uv.lock spells it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def locked(lock: Path) -> set[str]:
    """The names of the packages a uv.lock pins."""
    with lock.open("rb") as handle:
        return {normalized(package["name"]) for package in tomllib.load(handle).get("package", [])}


def interpreter(command: Path) -> str | None:
    """The Python a command's first line starts by its path, or None when it starts none that way."""
    try:
        with command.open("rb") as handle:
            head = handle.read(1024)
    except OSError:
        return None
    if not head.startswith(b"#!"):
        return None
    lines = head.split(b"\n")
    first = lines[0][2:].split()
    if first == [b"/bin/sh"] and len(lines) > 1:
        match = SH_EXEC.match(lines[1])
        path = match.group(1) if match else b""
    else:
        path = first[0] if first else b""
    path = os.fsdecode(path)
    return path if os.path.isabs(path) and PYTHON_NAME.fullmatch(os.path.basename(path)) else None


def stale(scripts: Path, site: list[str], names: set[str]) -> dict[str, list[tuple[str, str]]]:
    """{package: [(command, the Python it starts)]}, for each package of names installed in site with a
    command in scripts that starts a Python outside scripts.

    Paths are compared as they are spelled, never resolved: a path through a link to this
    environment is the very thing that breaks when the link goes."""
    scripts = Path(os.path.normpath(scripts))
    found: dict[str, list[tuple[str, str]]] = {}
    for dist in importlib.metadata.distributions(path=site):
        name = normalized(dist.metadata["Name"] or "")
        if name not in names:
            continue
        for file in dist.files or ():
            command = Path(os.path.normpath(dist.locate_file(file)))
            if command.parent != scripts:
                continue
            python = interpreter(command)
            if python and Path(os.path.normpath(python)).parent != scripts:
                found.setdefault(name, []).append((command.name, python))
    return found


def _said(found: dict[str, list[tuple[str, str]]]) -> str:
    """Which commands start which Python, a line per Python."""
    by_python: dict[str, list[str]] = {}
    for commands in found.values():
        for command, python in commands:
            by_python.setdefault(python, []).append(command)
    return "\n".join(f"  {python}: {', '.join(sorted(commands))}" for python, commands in sorted(by_python.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail, naming them, if there are any")
    parser.add_argument("--lock", type=Path, default=LOCK, help="the uv.lock whose packages to look at")
    args = parser.parse_args(argv)
    paths = sysconfig.get_paths()
    scripts = Path(paths["scripts"])
    found = stale(scripts, sorted({paths["purelib"], paths["platlib"]}), locked(args.lock))
    if not found:
        return 0
    count = sum(len(each) for each in found.values())
    commands = f"{count} command{'s' if count > 1 else ''} in [{scripts}]"
    start = "start" if count > 1 else "starts"
    if args.check:
        print(f"error: after the reinstall, {commands} still {start} a Python outside it:", file=sys.stderr)
        print(_said(found), file=sys.stderr)
        return 1
    print(
        f"{commands} {start} a Python outside it - by another path to it, or another environment's. Reinstalling"
        f" {', '.join(sorted(found))} points {'them' if count > 1 else 'it'} at the environment's own:\n{_said(found)}",
        file=sys.stderr,
    )
    for name in sorted(found):
        print(f"--reinstall-package {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
