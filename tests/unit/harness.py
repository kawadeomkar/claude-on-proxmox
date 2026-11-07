"""What the playbook tests share: a real ansible-playbook run against stand-in hosts, kept off this machine.

Every inventory host is the controller itself, over the local connection, with
an interpreter wrapper (STAND_INS["python"]) that puts the stand-ins in the
run's bin directory first on the module's PATH and makes the host's own
directory its HOME and its XDG_CONFIG_HOME. Whatever a module reads or writes -
~/.claude, ~/.gitconfig, gh's login - is that host's, never the developer's.

What a run inherits from the shell running pytest is a list of names
(PASSED_ON), not everything: a GIT_CONFIG_GLOBAL, an XDG_CONFIG_HOME or a
GH_TOKEN exported there pointed git and gh in the modules under test at the
developer's own config, and with XDG_CONFIG_HOME set the GitHub tests wrote the
test account's identity and credential helpers into it. The wrapper unsets
those again, in case a test passes them on itself.

Nothing a test runs may reach this machine's browser or clipboard. The
sign-ins' own switch (login_desktop) keeps those steps from running at all;
should one run anyway, every run's PATH starts with stand-ins for open,
xdg-open, pbcopy, wl-copy and xclip, and BROWSER names one of them. They record
their arguments and fail, and a run that called any of them fails its test.

Nor this machine's tailscale: the developer's Mac may have the real CLI (at
/usr/local/bin/tailscale, say), signed in to their own tailnet, and an `up` or a
`logout` that reached it would act on it. A failing stand-in for it is first on
every run's PATH too; tests/unit/test_tailscale.py puts
tests/molecule/fake_tailscale.sh ahead of it, in a host's bin/.

stdin is /dev/null for every run but those at a pseudo-terminal of the
harness's own, and each run has a process group of its own: with no terminal a
pause returns at once, where under `pytest -s` at a terminal it would wait on
the developer's keyboard, and a run cut short ends with its workers.
"""

from __future__ import annotations

import datetime
import fcntl
import functools
import importlib.util
import json
import os
import pty
import re
import select
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import sys
import termios
import time
import urllib.parse
import urllib.request
import warnings
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
# The ansible.cfg of every run a test makes. None of the project's settings but
# the one that changes what a play can read: gathered facts only under
# ansible_facts, as ansible.cfg has it (test_gathered_facts.py), so the plays only
# these tests run read facts as just's runs do.
ANSIBLE_CFG = "[defaults]\ninject_facts_as_vars = false\n"


def ansible_config(directory: Path, extra: str = "") -> str:
    """Write the ansible.cfg of a run in directory - ANSIBLE_CFG, then extra - and return its path, for ANSIBLE_CONFIG.

    Every ansible.cfg a test writes or points a run at is written here, and
    test_gathered_facts.py fails on one written or named any other way.
    """
    path = directory / "ansible.cfg"
    path.write_text(ANSIBLE_CFG + extra)
    return str(path)


# What `claude auth status` prints for each way of being signed in, and its
# exit status: 0 when logged in, 1 when not.
CLAUDE_AI = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}
LOGINS = {
    "logged-out": ({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}, 1),
    "claude-ai": ({**CLAUDE_AI, "email": "dev@example.com", "subscriptionType": "max"}, 0),
    # The login exists, but ANTHROPIC_API_KEY outranks it.
    "claude-ai-with-key": ({**CLAUDE_AI, "subscriptionType": "max", "apiKeySource": "ANTHROPIC_API_KEY"}, 0),
    # A Console login reports claude.ai too; only the key source tells it apart.
    "console": ({**CLAUDE_AI, "subscriptionType": None, "apiKeySource": "/login managed key"}, 0),
    "api-key": ({"loggedIn": True, "authMethod": "api_key", "apiKeySource": "ANTHROPIC_API_KEY"}, 0),
    "api-key-no-source": ({"loggedIn": True, "authMethod": "api_key"}, 0),
    # CLAUDE_CODE_OAUTH_TOKEN, from `claude setup-token`.
    "setup-token": ({"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"}, 0),
    # Signed in, but ~/.claude/settings.json switches the feature off.
    "claude-ai-telemetry": ({**CLAUDE_AI, "subscriptionType": "max"}, 0),
}

# One copy of each, shared by every host: macOS scans an executable the first
# time it runs, which made a copy per host cost seconds. Each host's interpreter
# is a symlink to the "python" wrapper, which finds the host's files beside it.
STAND_INS = {
    "python": f"""#!/bin/sh
STAND_IN=$(dirname "$0")
PATH="$(dirname "$(dirname "$0")")/bin:$PATH"
HOME=$STAND_IN
XDG_CONFIG_HOME=$STAND_IN/.config
GIT_CONFIG_NOSYSTEM=1
export STAND_IN PATH HOME XDG_CONFIG_HOME GIT_CONFIG_NOSYSTEM
unset GIT_CONFIG_GLOBAL GH_CONFIG_DIR GH_TOKEN GITHUB_TOKEN CLAUDE_CONFIG_DIR
exec "{sys.executable}" "$@"
""",
    "bash": """#!/bin/sh
echo "$*" >> "$STAND_IN/bash.log"
case "$*" in
  "-lc claude auth status") exec claude auth status ;;
  "-lc claude auth logout") exec claude auth logout ;;
  "-lc command -v claude") command -v claude ;;
  *) echo "stand-in bash: unexpected arguments: $*" >&2; exit 127 ;;
esac
""",
    "claude": """#!/bin/sh
if [ "$*" = "auth status" ]; then cat "$STAND_IN/status.json"; exit "$(cat "$STAND_IN/rc")"; fi
echo "stand-in claude: unexpected arguments: $*" >&2
exit 99
""",
    # systemd's answers for a unit that does not exist.
    "systemctl": """#!/bin/sh
echo "$*" >> "$STAND_IN/systemctl.log"
case "$1" in
  show) printf 'Id=%s.service\\nLoadState=not-found\\nActiveState=inactive\\nSubState=dead\\n' "$2" ;;
  is-enabled) echo "Failed to get unit file state for $2.service: No such file or directory" >&2; exit 1 ;;
  list-unit-files) exit 1 ;;
esac
""",
}

# What a run inherits from the shell running pytest: what finds programs and
# reads text, where temporary files go, who the user is, and HOME, under which
# ansible-playbook keeps its own files and finds the Galaxy collections - or
# their path, when it is set somewhere else. Nothing else, ANSIBLE_* included.
PASSED_ON = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "LANG",
        "LANGUAGE",
        "ANSIBLE_COLLECTIONS_PATH",
        "ANSIBLE_COLLECTIONS_PATHS",
    }
)

# What opens a page or fills a clipboard on this machine, as the sign-ins look
# for them, and this machine's tailscale, gh and claude, which a run looking for
# them on PATH - `which gh` in a host's own directory that has none - would
# otherwise find, signed in as the developer: each stand-in records its call
# beside itself and fails. A host that needs one puts its own first.
DESKTOP_TOOLS = ("open", "xdg-open", "pbcopy", "wl-copy", "xclip")
GUARDED_TOOLS = ("tailscale", "gh", "claude")
DESKTOP_STAND_IN = """#!/bin/sh
printf '%s %s\\n' "${0##*/}" "$*" >> "$(dirname "$0")/calls.log"
exit 1
"""


# What the stand-ins record, each in a file of its own: read as it is, empty while the file is missing - nothing
# called that stand-in yet.
def _text(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _lines(path: Path) -> list[str]:
    return _text(path).splitlines()


def _json_lines(path: Path) -> list:
    return [json.loads(line) for line in _lines(path)]


# Where the stand-in gh, claude and tailscale (tests/molecule/fake_*.sh) record each call, under a host's directory.
GH_CALLS = (".config", "gh", "calls.log")
CLAUDE_CALLS = (".claude", "fake", "calls.log")
TAILSCALE_CALLS = (".tailscale-fake", "calls.log")


def _calls(root: Path, host: str, *path: str) -> list[str]:
    """What a stand-in recorded under host's directory, at path - GH_CALLS, say: a call a line."""
    return _lines(root.joinpath(host, *path))


def _executable(path: Path, text: str) -> Path:
    """Write text to path as a program anyone may run; returns path."""
    path.write_text(text)
    path.chmod(0o755)
    return path


# Every user on the Ubuntu VMs the github_projects role configures has a group
# of the same name, which its directory task sets; macOS has none for the
# developer. Asked for that group, the modules of a host made with own_group
# get the user's own primary group instead, so the role runs to its end here
# as on a VM. It is the sitecustomize of OWN_GROUP_PYTHON, the interpreter
# wrapper with root/pylib on its PYTHONPATH.
OWN_GROUP = """import grp
import os
import pwd

_getgrnam = grp.getgrnam
_me = pwd.getpwuid(os.getuid()).pw_name


def _getgrnam_or_own(name):
    try:
        return _getgrnam(name)
    except KeyError:
        if name != _me:
            raise
        return grp.getgrgid(os.getgid())


grp.getgrnam = _getgrnam_or_own
"""
_WRAPPER = STAND_INS["python"].splitlines(keepends=True)
_EXEC = next(index for index, line in enumerate(_WRAPPER) if line.startswith("exec "))
OWN_GROUP_PYTHON = "".join(
    [*_WRAPPER[:_EXEC], 'PYTHONPATH="$(dirname "$(dirname "$0")")/pylib"\n', "export PYTHONPATH\n", *_WRAPPER[_EXEC:]]
)

# ssh, scp and sftp for a VM that is down: what ssh says when nothing listens,
# and the exit status the ssh plugin takes for "unreachable".
SSH_REFUSED = """#!/bin/sh
echo "$*" >> "$(dirname "$0")/ssh.log"
echo "ssh: connect to host 192.0.2.10 port 22: Connection refused" >&2
exit 255
"""

# The ssh first on a run's PATH, for a run that would otherwise reach this machine's own (_refusing_ssh), where
# SSH_REFUSED is a host's connection: it records how it was called, a line each, in ssh-calls.log beside it, and
# refuses as an unreachable host does.
REFUSING_SSH = """#!/bin/sh
printf '%s\\n' "$*" >> "$(dirname "$0")/ssh-calls.log"
echo "ssh: connect to host stand-in port 22: Connection refused" >&2
exit 255
"""

# What `getent passwd <user>` answers on the stand-in hosts: the user's home is
# the host's own directory, as HOME already is for every module. macOS has no
# getent, and the roles' entry points look the home up with it.
GETENT = """#!/bin/sh
[ "$1" = passwd ] || { echo "stand-in getent: unexpected database $1" >&2; exit 3; }
printf '%s:x:1000:1000:%s:%s:/bin/bash\\n' "$2" "$2" "$HOME"
"""


def _local_hosts(
    root: Path, names: Iterable[str], tools: dict | None = None, *, own_group: bool = False, address: str = "192.0.2.10"
) -> dict:
    """Stand-in VMs over the local connection, one per name, each with a directory of its own under root; their
    hostvars, which the caller adds to.

    root/bin holds the interpreter wrapper (STAND_INS["python"], or with own_group OWN_GROUP_PYTHON) and each of
    tools, {name: text}, which the wrapper puts first on the modules' PATH; each host's interpreter is a link to it in
    its own directory, its HOME. address is the ansible_host each is given, none when empty.
    """
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    _executable(bin_dir / "python", OWN_GROUP_PYTHON if own_group else STAND_INS["python"])
    if own_group:
        (root / "pylib").mkdir()
        (root / "pylib" / "sitecustomize.py").write_text(OWN_GROUP)
    for name, text in (tools or {}).items():
        _executable(bin_dir / name, text)
    hosts = {}
    for name in names:
        home = root / name
        home.mkdir(exist_ok=True)
        (home / "python").symlink_to(bin_dir / "python")
        hosts[name] = {
            "ansible_connection": "local",
            "ansible_python_interpreter": str(home / "python"),
            **({"ansible_host": address} if address else {}),
        }
    return hosts


def _unreachable(hostvars: dict, root: Path) -> None:
    """Make a host of _local_hosts a VM that is down: reached over ssh, which the stand-in in root/bin refuses, so
    nothing leaves this machine and nothing reads ~/.ssh."""
    refused = str(_executable(root / "bin" / "ssh", SSH_REFUSED))
    hostvars.update(
        ansible_connection="ssh",
        ansible_ssh_executable=refused,
        ansible_scp_executable=refused,
        ansible_sftp_executable=refused,
        # No ControlPersist, so no socket directory is made for it.
        ansible_ssh_args="",
        ansible_control_path_dir=str(root / "cp"),
    )


def _stand_in_hosts(root: Path, logins: dict, **options) -> dict:
    """Inventory hosts, one per canned login, whose modules run with the stand-ins; returns their hostvars.

    options go to _local_hosts."""
    hosts = _local_hosts(root, logins, {name: text for name, text in STAND_INS.items() if name != "python"}, **options)
    for name, (status, rc) in logins.items():
        home = root / name
        (home / "status.json").write_text(json.dumps(status))
        (home / "rc").write_text(str(rc))
        # The home the role reads ~/.claude from: the host's directory,
        # never the developer's.
        hosts[name]["claude_code_home"] = str(home)
    return hosts


def _seed_settings(root: Path, host: str, settings: dict) -> None:
    """Put a settings.json where that host's modules read it: under its HOME, and under its claude_code_home."""
    claude_dir = root / host / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps(settings))


def _desktop(root: Path) -> Path:
    """The directory of desktop stand-ins for runs under root, made on first use."""
    desktop = root / ".desktop"
    desktop.mkdir(exist_ok=True)
    for tool in (*DESKTOP_TOOLS, *GUARDED_TOOLS):
        _executable(desktop / tool, DESKTOP_STAND_IN)
    return desktop


def _desktop_calls(root: Path) -> str:
    """What the desktop stand-ins under root were asked to do: nothing, in a test that keeps to its rules."""
    return _text(root / ".desktop" / "calls.log")


def _refusing_ssh(root: Path, env: dict | None = None) -> dict:
    """env with REFUSING_SSH first on PATH, from root/ssh-stand-in, and the desktop stand-ins after it: a run given
    it as its env reaches no real ssh."""
    stand_in = root / "ssh-stand-in"
    stand_in.mkdir(exist_ok=True)
    _executable(stand_in / "ssh", REFUSING_SSH)
    return (env or {}) | {
        "PATH": f"{stand_in}{os.pathsep}{_desktop(root)}{os.pathsep}{os.environ.get('PATH', os.defpath)}"
    }


def _ssh_calls(root: Path) -> str:
    """What the refusing ssh under root was asked, a call a line: nothing, where no run reached for ssh."""
    return _text(root / "ssh-stand-in" / "ssh-calls.log")


def _fail_if_the_desktop_was_reached(root: Path) -> None:
    calls = _desktop_calls(root)
    if calls:
        pytest.fail(
            f"a run under {root} called this machine's browser or clipboard, or its tailscale, gh or claude"
            f" (a stand-in caught it):\n{calls}"
        )


def _env(root: Path, config: str = "", **settings: str) -> dict:
    """The environment of a run under root: PASSED_ON, the desktop stand-ins first on PATH, and settings.

    It writes the run's ansible.cfg, with config after ANSIBLE_CFG.
    """
    desktop = _desktop(root)
    env = {name: value for name, value in os.environ.items() if name in PASSED_ON or name.startswith("LC_")}
    env["PATH"] = f"{desktop}{os.pathsep}{os.environ.get('PATH', os.defpath)}"
    env["BROWSER"] = str(desktop / "open")
    env.update(
        ANSIBLE_CONFIG=ansible_config(root, config),
        ANSIBLE_ROLES_PATH=str(REPO / "roles"),
        ANSIBLE_FILTER_PLUGINS=str(REPO / "filter_plugins"),
        # As ansible.cfg's library and module_utils: playbooks a test writes elsewhere use just code's and destroy's
        # modules too, and those import what they share.
        ANSIBLE_LIBRARY=str(REPO / "playbooks" / "library"),
        ANSIBLE_MODULE_UTILS=str(REPO / "playbooks" / "module_utils"),
        ANSIBLE_NOCOLOR="1",
    )
    env.update(settings)
    return env


def _left_to_do(report: dict) -> dict:
    """What a run's reports recorded for NEXT STEPS: {vm: {matter: line}}, an empty line for nothing left."""
    records: dict = {}
    for stat, by_vm in (report.get("global_custom_stats") or {}).items():
        if stat.endswith("_next"):
            for vm, matters in by_vm.items():
                records.setdefault(vm, {}).update(matters)
    return records


def _inventory(root: Path, hosts: dict, ordered: bool) -> Path:
    """An inventory of hosts, by name or, with ordered, in the order given; never the project's."""
    inventory = root / "hosts.yml"
    inventory.write_text(
        yaml.safe_dump({"claude_vms": {"hosts": hosts, "vars": {"vm_user": "dev"}}}, sort_keys=not ordered)
    )
    return inventory


def _processes():
    """lib/claude_vm/proc.py, the module the parts' engine ends a tree with (docs/architecture/custom-code.md §7.10)."""
    from claude_vm import proc

    return proc


def _end(process: subprocess.Popen) -> None:
    """Kill a run and everything under it: its group, and what left the group - ansible-core's task worker, detached
    into a session of its own, which a kill of the group alone left behind idle for good, and a nested run's parts -
    each found before any is signalled (lib/claude_vm/proc.py); though not an async job's wrapper, which
    daemonises itself before any test could call this, and leaves."""
    _processes().end(
        process.pid, signum=signal.SIGKILL, blind=lambda said: warnings.warn(said, RuntimeWarning, stacklevel=3)
    )
    process.wait()


def _run(
    root: Path,
    playbook: Path,
    hosts: dict,
    *args: str,
    ordered: bool = False,
    timeout: float | None = None,
    env: dict | None = None,
) -> dict:
    """Run a playbook against hosts, without the project's config or inventory; return the json callback's report.

    The report gains the run's rc and stderr. ordered keeps the hosts in the
    order given, where the inventory otherwise lists them by name; a run that
    takes longer than timeout seconds fails the test. env sets variables of
    this machine's own on top of the harness's - a run over SSH, no BROWSER.
    """
    inventory = _inventory(root, hosts, ordered)
    process = subprocess.Popen(
        [str(ANSIBLE_PLAYBOOK), "-i", str(inventory), *args, str(playbook)],
        cwd=root,
        # Custom stats shown: the report then carries what the run recorded
        # for NEXT STEPS (callback_plugins/claude_on_proxmox.py), _left_to_do.
        env=_env(root, ANSIBLE_STDOUT_CALLBACK="ansible.builtin.json", ANSIBLE_SHOW_CUSTOM_STATS="1", **(env or {})),
        # Never the developer's terminal: a pause would wait on it. With no
        # terminal the sign-in's prompt returns at once, as in an unattended run.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # A group of its own, so that a run cut short ends with its workers.
        process_group=0,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _end(process)
        pytest.fail(f"ansible-playbook {playbook.name} did not finish within {timeout} seconds")
    except BaseException:
        _end(process)
        raise
    _fail_if_the_desktop_was_reached(root)
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError:
        pytest.fail(f"ansible-playbook exited {process.returncode}:\n{stdout}\n{stderr}")
    report["rc"] = process.returncode
    report["stderr"] = stderr
    return report


# What a Ctrl-C a test sends must reach. A process starts with the signals its
# parent ignored still ignored, and a job a shell without job control starts with
# `&` - under nohup, a CI step's background command, a script - starts with
# SIGINT and SIGQUIT ignored, nohup adding SIGHUP: a run of the suite started so
# sat through every Ctrl-C a test sent, and the device-flow test hung for good.
INTERRUPTS = (signal.SIGINT, signal.SIGQUIT, signal.SIGHUP)


def _default_signals() -> None:
    """In a child, before it starts: the interrupts back to their defaults, whatever the run's launcher ignored."""
    for signum in INTERRUPTS:
        signal.signal(signum, signal.SIG_DFL)


def _drain(master: int, deadline: float) -> bytes:
    """What a pseudo-terminal still holds once its process has ended: read until the terminal says it is closed.

    Linux passes a terminal's output to its reader through a buffer it empties asynchronously, so on a loaded
    machine the last bytes a run wrote - a live region's erase, a final message - can arrive after the run has
    exited, and a reader that stopped at the first read that found nothing missed them. Two quiet seconds end it
    too, should something the run started still hold the terminal open; and the test's own deadline, should it
    still be writing there, fails the test rather than hang it.
    """
    data = b""
    while select.select([master], [], [], 2)[0]:
        if time.monotonic() > deadline:
            pytest.fail(
                "the terminal was still being written to after its run ended:\n" + data.decode(errors="replace")
            )
        try:
            chunk = os.read(master, 65536)
        except OSError:  # Linux, once every holder of the other end has closed it and nothing is left
            break
        if not chunk:
            break
        data += chunk
    return data


def _take_the_terminal() -> None:
    """In the child: a session of its own, with its stdin as the controlling terminal, and the interrupts live.

    pause reads only from the foreground process group of a terminal, which a
    session leader's group is once the terminal is its controlling one.
    """
    _default_signals()
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _in_raw_mode(master: int) -> bool:
    """Whether the terminal behind a pseudo-terminal's master is in raw mode, as pause puts it to read keys."""
    try:
        return not termios.tcgetattr(master)[3] & termios.ICANON
    except termios.error:
        return False


def _at_a_pseudo_terminal(
    argv: list[str],
    cwd: Path,
    env: dict,
    *,
    size: tuple[int, int] | None = None,
    act=None,
    timeout: float = 180,
    poll: float | Callable[[], float] = 0.2,
    explain: Callable[[subprocess.Popen, int], str] | None = None,
) -> tuple[int, bytes]:
    """Run argv at a pseudo-terminal of its own, and read what it writes until it ends. Its rc and every byte.

    The run leads a session of its own whose controlling terminal it is
    (_take_the_terminal), so pause can read keys from it and a Ctrl-C there is
    the run's alone; never pytest's terminal. size, (cols, rows), sets the
    window. act(chunk, output, master, process), called after each read with
    the bytes read and after each idle tick with b"", may type, resize or
    signal. An idle tick comes after poll seconds without output, or after
    what poll returns, when it is a function, asked before each wait. A run
    that outlives timeout fails the test, its group killed, with what
    explain(process, master) says of it after its output, when given.
    Every test that runs something at a terminal goes through here: the
    sign-ins' and init's prompts (_run_at_a_terminal), the live outputs
    (test_status_live.py, test_deploy_live.py), and just code's Claude
    terminal (test_code_session.py).
    """
    master, slave = pty.openpty()
    if size:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", size[1], size[0], 0, 0))
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        preexec_fn=_take_the_terminal,  # noqa: PLW1509 - only signal, setsid and ioctl, safe beside xdist's thread
    )
    os.close(slave)
    output, deadline = b"", time.monotonic() + timeout
    try:
        while True:
            if time.monotonic() > deadline:
                said = output.decode(errors="replace") + (f"\n{explain(process, master)}" if explain else "")
                pytest.fail(f"the run did not finish within {timeout} seconds:\n{said}")
            ready, _, _ = select.select([master], [], [], poll() if callable(poll) else poll)
            chunk = b""
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
            elif process.poll() is not None:
                rest = _drain(master, deadline)
                output += rest
                if act is not None and rest:
                    act(rest, output, master, process)
                break
            if act is not None:
                act(chunk, output, master, process)
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            _end(process)
        os.close(master)
    return process.returncode, output


# How often a run at a terminal is looked at while keys wait for their prompt, and how long the prompt's raw mode
# must have held before they go in. pause sets raw mode, flushes the terminal's input within microseconds, and then
# reads in silence, so no output wakes the reader: looked at every 0.2 seconds, as the rest of a run is, each
# prompt waited up to that long for its keys. A key the flush takes all the same is typed again two seconds on.
PENDING_POLL = 0.02
RAW_HELD = 0.01


def _run_at_a_terminal(
    root: Path,
    playbook: Path,
    hosts: dict,
    extra: dict,
    answer: str | None = None,
    prompt: bytes = b"Code:",
    *,
    steps: Iterable[tuple] | None = None,
    args: Iterable[str] = (),
    ordered: bool = False,
    timeout: float = 180,
    env: dict | None = None,
) -> tuple[int, str, str]:
    """Run a playbook whose stdin is a pseudo-terminal it controls, and answer its prompts. rc, output, run log.

    Either `answer` and a line end are typed at `prompt`, or each of steps,
    (prompt, keys), (prompt, keys, seconds) or (prompt, keys, seconds,
    "canonical"), types its keys as they are at its prompt's next appearance,
    that many seconds later. Keys go in once the prompt has held the terminal
    in raw mode for RAW_HELD seconds - the terminal is looked at every
    PENDING_POLL seconds while they wait - and again if it has not taken them
    within two seconds. A "canonical" step is a vars_prompt's, which reads a
    line with input() and never leaves canonical mode: its keys go in once,
    as soon as its prompt shows. args go on the command line, before the
    playbook; env sets variables on top of the harness's, as _run's does.
    """
    plan = [(prompt, answer.encode() + b"\n")] if steps is None else list(steps)
    for step in plan:
        assert len(step) < 4 or step[3] == "canonical", step
    plan = [(step[0], step[1], step[2] if len(step) > 2 else 0, len(step) > 3) for step in plan]
    inventory = _inventory(root, hosts, ordered)
    log = root / "run.log"
    env = _env(root, ANSIBLE_LOG_PATH=str(log), TERM="dumb", **(env or {}))
    state = {"searched": 0, "typing": None}

    def type_at_each_prompt(chunk, output, master, process):
        typing, now = state["typing"], time.monotonic()
        if typing is not None and typing["mark"] is not None:
            if b"\x1b" in output[typing["mark"] :]:
                # pause clears its line, an escape sequence, once it has
                # taken an answer or an interrupt.
                typing = state["typing"] = None
            elif now - typing["at"] > 2 and typing["tries"] < 5:
                # Keys typed in the moment before pause flushed the
                # terminal's input are lost, and echoed all the same, so
                # the echo proves nothing: typed again.
                typing.update(at=now, tries=typing["tries"] + 1)
                os.write(master, typing["keys"])
        if typing is None and plan:
            wanted, keys, delay, canonical = plan[0]
            found = output.find(wanted, state["searched"])
            if found >= 0:
                plan.pop(0)
                state["searched"] = found + len(wanted)
                typing = state["typing"] = {"keys": keys, "from": now + delay, "mark": None, "at": 0.0, "tries": 0}
                typing.update(canonical=canonical, raw_since=None)
        if typing is not None and typing["mark"] is None and now >= typing["from"]:
            if typing["canonical"]:
                # input() reads the line whenever it comes: nothing flushes
                # the keys, and nothing clears the line to say they were taken.
                state["typing"] = None
                os.write(master, typing["keys"])
                return
            # Only once pause has the terminal in raw mode: before, a Ctrl-C
            # would be a signal to the whole run, not a key. And only once
            # raw mode has held a moment: pause sets it, then flushes the
            # input the terminal holds, which takes keys typed in between.
            raw = _in_raw_mode(master)
            if not raw:
                typing["raw_since"] = None
            elif typing["raw_since"] is None:
                typing["raw_since"] = now
            if (raw and now - typing["raw_since"] >= RAW_HELD) or now > typing["from"] + 5:
                typing.update(mark=len(output), at=now)
                os.write(master, typing["keys"])

    def poll() -> float:
        # Often while keys wait for their prompt's raw mode, which pause sets
        # and then reads in silence: no output wakes the reader then.
        typing = state["typing"]
        return PENDING_POLL if typing is not None and typing["mark"] is None else 0.2

    rc, output = _at_a_pseudo_terminal(
        [str(ANSIBLE_PLAYBOOK), "-i", str(inventory), *args, "-e", json.dumps(extra), str(playbook)],
        root,
        env,
        act=type_at_each_prompt,
        timeout=timeout,
        poll=poll,
    )
    _fail_if_the_desktop_was_reached(root)
    assert not plan, f"no {plan[0][0]!r} prompt appeared:\n" + output.decode(errors="replace")
    return rc, output.decode(errors="replace"), log.read_text() if log.exists() else ""


def _results(report: dict):
    for play in report["plays"]:
        for task in play["tasks"]:
            for host, result in task["hosts"].items():
                yield task["task"]["name"], host, result


def _messages(report: dict, task: str) -> dict:
    """What each host printed from the task whose name ends with `task`."""
    return {
        host: result["msg"]
        for name, host, result in _results(report)
        if name.endswith(task) and "msg" in result and not result.get("skipped")
    }


def _ran(report: dict, task: str, host: str | None = None) -> dict:
    """The results of the tasks whose name ends with `task`, skipped ones left out: {host: [result, ...]}, in the
    order they ran, for host alone when given."""
    ran: dict = {}
    for name, where, result in _results(report):
        if name.endswith(task) and not result.get("skipped") and host in (None, where):
            ran.setdefault(where, []).append(result)
    return ran


def _failures(report: dict) -> dict:
    """What failed each host, by its last failed task: rescued failures too, and never a host unreachable."""
    return {host: f"{name}: {result.get('msg')}" for name, host, result in _results(report) if result.get("failed")}


def _standing(report: dict) -> dict:
    """What the run failed each host with, unlike _failures with a rescued failure left out and an unreachable host
    in: the last failure of each host, if it stands."""
    failures = {}
    for name, host, result in _results(report):
        if result.get("failed") or result.get("unreachable"):
            failures[host] = f"{name}: {result.get('msg')}"
    return {host: text for host, text in failures.items() if report["stats"][host]["failures"]}


def _batches(report: dict, play: str) -> list[list[str]]:
    """The hosts of each batch the play ran: the json callback reports each batch of a serial play as a play."""
    return [
        sorted({host for task in entry["tasks"] for host in task["hosts"]})
        for entry in report["plays"]
        if entry["play"]["name"] == play
    ]


def _task_seconds(report: dict, task: str, host: str) -> float:
    """How long the task whose name ends with `task` ran for the host: the first that ran for it."""
    for play in report["plays"]:
        for entry in play["tasks"]:
            if entry["task"]["name"].endswith(task) and host in entry["hosts"]:
                duration = entry["task"]["duration"]
                start, end = (
                    datetime.datetime.fromisoformat(duration[key].replace("Z", "+00:00")) for key in ("start", "end")
                )
                return (end - start).total_seconds()
    raise AssertionError(f"no task {task!r} ran for {host}")


def _init_next(report: dict) -> dict:
    """What a run of just init's recorded for NEXT STEPS, by matter: just init's only, under a key no VM can have."""
    left = _left_to_do(report)
    assert set(left) <= {"just init"}, left
    return left.get("just init", {})


def _leaked(report: dict, secrets) -> list:
    """Each of `secrets` the report holds. Module arguments reach a report only with ansible_inject_invocation on,
    and a search of a run without them would find nothing whatever its tasks logged: the report must hold them."""
    text = json.dumps(report)
    assert '"invocation"' in text, "no module arguments in the report: run it with ansible_inject_invocation"
    return [secret for secret in secrets if secret in text]


# ------------------------------------------------------ the files themselves ---
@functools.cache
def _listed(pathspecs: tuple[str, ...], untracked: bool) -> tuple[Path, ...]:
    flags = ["--cached", "--others", "--exclude-standard"] if untracked else []
    listed = subprocess.run(
        ["git", "ls-files", "-z", *flags, "--", *pathspecs], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return tuple(sorted({REPO / name for name in listed.split("\0") if name and (REPO / name).is_file()}))


def repo_files(*pathspecs: str, untracked: bool = False) -> list[Path]:
    """The checkout's files that match git's pathspecs - all of them, given none - regular files only, sorted: the
    tracked ones, and with untracked the new ones git does not ignore too, so a file is checked before its first
    commit, while a working document git ignores (one at the top of docs/) never is. Listed once per process."""
    return list(_listed(pathspecs, untracked))


def repo_texts(*pathspecs: str, untracked: bool = False) -> list[tuple[Path, str]]:
    """repo_files' files, each with its text: a file that is not UTF-8 text left out."""
    texts = []
    for path in repo_files(*pathspecs, untracked=untracked):
        try:
            texts.append((path, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue
    return texts


def load_module(path: str | Path, name: str | None = None, *, register: bool = True):
    """A Python file imported by its path, relative to the checkout or absolute, as name - its file's stem unless
    given - and in sys.modules under it unless register is false: a copy of its own, as a test that loads a module
    afresh wants, which nothing else can find."""
    path = REPO / path
    spec = importlib.util.spec_from_file_location(name or path.stem, path)
    module = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _every_task(node):
    """Every task in a play list or a task list, in order: a play's pre_tasks, tasks, post_tasks and handlers, and
    each block yielded before its block, rescue and always."""
    for item in node if isinstance(node, list) else []:
        if not isinstance(item, dict):
            continue
        if "tasks" in item or "pre_tasks" in item or "post_tasks" in item or "handlers" in item:
            for key in ("pre_tasks", "tasks", "post_tasks", "handlers"):
                yield from _every_task(item.get(key))
            continue
        yield item
        for key in ("block", "rescue", "always"):
            yield from _every_task(item.get(key))


# What of a task is never logged: its name, its conditions, what it registers, no_log itself and its tags.
NOT_LOGGED = frozenset({"name", "when", "changed_when", "failed_when", "until", "register", "no_log", "tags"})


def _rendering(tasks: Iterable[dict], pattern: re.Pattern, *, ignore: Iterable[str] = NOT_LOGGED) -> dict:
    """Each of tasks that renders what pattern matches, in anything but ignore's keys, by name: its no_log. A block
    is its tasks, each looked at on its own."""
    rendering = {}
    for task in tasks:
        if "block" in task:
            continue
        if pattern.search(json.dumps({key: value for key, value in task.items() if key not in ignore})):
            rendering[task["name"]] = task.get("no_log")
    return rendering


def _deploy_import(root: Path, target: str) -> Path:
    """deploy.yml's import of target - playbooks/github_login.yml, say - as it stands, its vars and its condition with
    it, in a playbook of root's own that imports target by its whole path; returns that playbook."""
    entries = [
        entry
        for entry in yaml.safe_load((REPO / "deploy.yml").read_text())
        if entry.get("ansible.builtin.import_playbook") == target
    ]
    assert len(entries) == 1, entries
    playbook = root / f"deploy_{Path(target).stem}.yml"
    playbook.write_text(yaml.safe_dump([{**entries[0], "ansible.builtin.import_playbook": str(REPO / target)}]))
    return playbook


def _role_tasks_play(role: str, tasks_from: Iterable[str], name: str) -> dict:
    """A play named name that runs each of a role's task files of tasks_from, in order, on every stand-in VM: the
    part of the role under test, as its main.yml runs it."""
    return {
        "name": name,
        "hosts": "claude_vms",
        "gather_facts": False,
        "tasks": [
            {
                "name": f"Run the role's {tasks} tasks",
                "ansible.builtin.include_role": {"name": role, "tasks_from": tasks},
            }
            for tasks in tasks_from
        ],
    }


def _scenario_vars(paths: Iterable[Path]) -> dict:
    """What the given vars files set, merged in Ansible's order: later files win."""
    merged: dict = {}
    for path in paths:
        merged.update(yaml.safe_load(path.read_text()) or {})
    return merged


def verify_plays(scenario: Path) -> list[tuple[Path, dict]]:
    """Every play a scenario's verify runs, with its file: each verify*.yml, and each file of the scenario's own one
    imports (the provision scenario's checks are in three, which CI runs as two jobs), every file once."""
    found, seen = [], set()

    def read(path: Path) -> None:
        if path in seen:
            return
        seen.add(path)
        for play in yaml.safe_load(path.read_text()) or []:
            target = str(play.get("ansible.builtin.import_playbook", ""))
            if target and "/" not in target:
                read(scenario / target)
            else:
                found.append((path, play))

    for path in sorted(scenario.glob("verify*.yml")):
        read(path)
    return found


# --------------------------------------------------------------------- just ---
JUST = shutil.which("just")


@functools.cache
def just_json() -> dict:
    """The justfile as just reads it (just --json): its recipes, assignments and settings, read once per process."""
    return json.loads(subprocess.run([JUST, "--json"], cwd=REPO, capture_output=True, text=True, check=True).stdout)


def public_recipes() -> dict[str, dict]:
    """The recipes a person runs: every one but the private ones."""
    return {name: recipe for name, recipe in just_json()["recipes"].items() if not recipe["private"]}


def just_settings() -> set[str]:
    """The assignments a person sets: the upper-case ones. The rest are the justfile's own workings."""
    return {name for name in just_json()["assignments"] if name.isupper()}


def _version(text: str) -> tuple[int, ...]:
    """A version's numbers, to compare: 1.59.0 is (1, 59, 0)."""
    return tuple(int(part) for part in text.split("."))


# ---------------------------------------------------------------------- git ---
# git as a test runs it: none of this machine's config, global or system - an insteadOf, a hook or
# status.showUntrackedFiles there changes what git says and does - and an identity of its own to commit as.
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "molecule",
    "GIT_AUTHOR_EMAIL": "molecule@example.com",
    "GIT_COMMITTER_NAME": "molecule",
    "GIT_COMMITTER_EMAIL": "molecule@example.com",
}


def _bare_repos(git_root: Path, specs: Iterable[str]) -> None:
    """One bare repository in git_root per spec - a name, or name:visibility as the fake GitHub takes it - with a
    commit on main, as the Molecule prepare makes them: what tests/molecule/fake_github_api.py serves from there."""
    env = {**os.environ, **GIT_ENV}
    for spec in specs:
        name = spec.split(":")[0]
        src = git_root / "src" / name
        src.mkdir(parents=True)
        (src / "README.md").write_text(f"# {name}\n")
        for argv in (
            ["git", "init", "-q", "-b", "main", str(src)],
            ["git", "-C", str(src), "add", "README.md"],
            ["git", "-C", str(src), "commit", "-q", "-m", "init"],
            ["git", "clone", "-q", "--bare", str(src), str(git_root / f"{name}.git")],
        ):
            subprocess.run(argv, check=True, env=env, capture_output=True)


# ------------------------------------------------------------ the editors ---
# An editor's plan inputs as code.yml hands them to the core (lib/claude_vm/editors/), for the tests that make a plan
# themselves: alpha's window of its projects directory, every switch on, both editors' CLIs found - ZFS's event
# daemon, zed, first on PATH, as on Ubuntu, which a plan never runs.
EDITOR_ROOT = "/home/dev/projects"
EDITOR_FILES_DIR = f"{EDITOR_ROOT}/.claude-on-proxmox"
ZEDITOR, VSCODE_CLI = "/usr/local/bin/zeditor", "/usr/local/bin/code"
EDITOR_PROBE = {"which": ["/usr/sbin/zed", ZEDITOR, VSCODE_CLI], "versions": [[ZEDITOR, "Zed 0.210.4"]]}


def editor_inputs(**over) -> dict:
    """The inputs of an editor's plan, as code.yml gives them, with over in place of any."""
    return {
        "vm": {"name": "alpha", "vmid": 4013},
        "target": EDITOR_ROOT,
        "root": EDITOR_ROOT,
        "files_dir": EDITOR_FILES_DIR,
        "reach": "192.0.2.51",
        "terminal": True,
        "notes": True,
        "notes_file": f"{EDITOR_FILES_DIR}/session/alpha-session.md",
        "notes_dir": f"{EDITOR_FILES_DIR}/session",
        "task_written": True,
        "helpers": sorted(path.name for path in (REPO / "playbooks" / "files").iterdir()),
        "probe": EDITOR_PROBE,
    } | over


# ------------------------------------------------------------ the fake APIs ---
# The fakes in tests/molecule/ on a loopback port of their own, for the tests
# that run a playbook against one: the fake Proxmox is TLS, with a certificate
# made for the run, and accepts the token whose secret is PVE_SECRET.
PVE_SECRET = "unit-test-pve-secret"
FAKES = REPO / "tests" / "molecule"


def _pve_cli(directory: Path, state: dict | None = None) -> Path:
    """The stand-in pveum, pvesh, pveversion, hostname and id (fake_pve_cli.py) in directory, the host's state given
    on top of its default; returns directory, to put first on PATH."""
    return load_module(FAKES / "fake_pve_cli.py", register=False).install(directory, state)


def _pve_cli_state(directory: Path) -> dict:
    return json.loads((directory / "pve-state.json").read_text())


def _pve_cli_calls(directory: Path) -> list[list[str]]:
    """Every call the stand-ins in directory took, each its command and words."""
    return _json_lines(directory / "calls.log")


def _pve_cli_writes(directory: Path) -> list[str]:
    """The calls the stand-ins in directory took that change the host: every pveum but its one read, `user
    permissions` (tools/proxmox-access.sh lists with pvesh)."""
    return [
        " ".join(call)
        for call in _pve_cli_calls(directory)
        if call[0] == "pveum" and call[1:3] != ["user", "permissions"]
    ]


def _free_port() -> int:
    # On every address, as the fakes bind: a port free on loopback alone can be another interface's.
    with socket.socket() as probe:
        probe.bind(("0.0.0.0", 0))
        return probe.getsockname()[1]


# How every fake starts: as `python script args`, but with socket.getfqdn answering at once. http.server's bind asks
# for the full name of the address it binds, and for fake_pve_api.py's 0.0.0.0 macOS looks this machine's name up
# over mDNS and gives up after five seconds: every fake Proxmox a test started waited them out before it listened.
# The fakes use that name for nothing.
FAKE_START = (
    "import os, runpy, socket, sys\n"
    "socket.getfqdn = lambda name='': name\n"
    "sys.argv = sys.argv[1:]\n"
    "sys.path[0] = os.path.dirname(sys.argv[0])\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


def _start(script: Path, *args: str) -> tuple[subprocess.Popen, int]:
    """A fake API on a port the system picked, listening once it says which. Given port 0 the fake binds a free one
    and prints it: a port probed here and freed could be taken before the fake bound it - by the runs' own
    connections, or by another xdist worker's fake, which a test would then talk to."""
    server = subprocess.Popen(
        [sys.executable, "-c", FAKE_START, str(script), "0", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    said = server.stdout.readline() if select.select([server.stdout], [], [], 30)[0] else ""
    if not said.strip().isdigit():
        server.kill()
        server.wait(timeout=10)
        pytest.fail(f"{script.name} did not start: {said}{server.stderr.read()}")
    return server, int(said)


def _serve(script: Path, *args: str):
    """A fake API on a loopback port, until the fixture ends; yields its base URL."""
    server, port = _start(script, *args)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.terminate()
        server.wait(timeout=10)


def _certificate(directory: Path) -> tuple[Path, Path]:
    """A self-signed certificate for the fake Proxmox's TLS; returns the certificate's file and the key's."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert, key_file = directory / "cert.pem", directory / "key.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return cert, key_file


def _pve(base: str, method: str, endpoint: str, **params) -> dict:
    """One request to the fake Proxmox (tests/molecule/fake_pve_api.py), as a fixture makes it."""
    request = urllib.request.Request(
        f"{base}/api2/json/{endpoint}",
        data=urllib.parse.urlencode(params).encode() if params else None,
        method=method,
        headers={"Authorization": f"PVEAPIToken=ansible@pve!ansible={PVE_SECRET}"},
    )
    unverified = ssl.create_default_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(request, timeout=10, context=unverified) as answer:
        return json.loads(answer.read() or b"{}")


def _fake_pve(directory: Path):
    """The fake Proxmox on a loopback port, until the fixture ends, its state and its request log in directory/pve;
    yields its https base URL."""
    cert, key = _certificate(directory)
    for base in _serve(FAKES / "fake_pve_api.py", str(cert), str(key), str(directory / "pve"), PVE_SECRET):
        yield base.replace("http://", "https://")


def _pve_vm(base: str, vmid: int, name: str, *, running: bool = False, agent_polls: int = 3, **config) -> None:
    """A VM in the fake at base, cloned from the template, given config - this project's tag when none is given - and,
    when running, started, its agent asked agent_polls times: the fake refuses the first two polls, as an agent still
    starting does, and answers from the third."""
    _pve(base, "POST", "nodes/pve/qemu/9000/clone", newid=vmid, name=name)
    _pve(base, "PUT", f"nodes/pve/qemu/{vmid}/config", **(config or {"tags": "claude-on-proxmox"}))
    if running:
        _pve(base, "POST", f"nodes/pve/qemu/{vmid}/status/start")
        for _ in range(agent_polls):
            try:
                _pve(base, "GET", f"nodes/pve/qemu/{vmid}/agent/network-get-interfaces")
            except Exception:  # noqa: BLE001 - the fake refuses the first polls on purpose
                pass


def _pve_vars(directory: Path, base: str, pinned: dict | None = None) -> Path:
    """directory/group_vars/all for a run against the fake at base: the project's defaults.yml, linked, and
    fake_api.yml - the fake's address, the token's secret, the VM user, then pinned. Returns that directory."""
    group_vars = directory / "group_vars" / "all"
    group_vars.mkdir(parents=True, exist_ok=True)
    defaults = group_vars / "defaults.yml"
    if not defaults.is_symlink():
        defaults.symlink_to(REPO / "inventory" / "group_vars" / "all" / "defaults.yml")
    api = {
        "proxmox_api_host": "127.0.0.1",
        "proxmox_api_port": int(base.rsplit(":", 1)[1]),
        "vault_proxmox_api_token_secret": PVE_SECRET,
        "vm_user": "dev",
    }
    (group_vars / "fake_api.yml").write_text(yaml.safe_dump({**api, **(pinned or {})}))
    return group_vars


def _pve_requests(directory: Path) -> list[dict]:
    """Every request the fake Proxmox in directory/pve was sent, in order: its method, path and params."""
    return _json_lines(directory / "pve" / "calls.log")


def fake_fleet(directory: Path, version: str = "9.2.21"):
    """The fake Proxmox, on VE 9 unless told, with alpha and beta running and their agents up, and gamma stopped;
    its URL."""
    for base in _fake_pve(directory):
        _pve(base, "PUT", "fake/version", version=version)
        for vmid, name, running in [(9010, "alpha", True), (9011, "beta", True), (9012, "gamma", False)]:
            _pve_vm(base, vmid, name, running=running, tags="claude-on-proxmox", ciuser="dev", onboot="1")
            if name == "alpha":
                # Made two days ago, so its day of history is its own; beta and gamma were made just now.
                _pve(base, "PUT", "fake/disk-ctime", vmid=vmid, ctime=int(time.time()) - 2 * 86400)
        yield base
