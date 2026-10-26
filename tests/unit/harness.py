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
import json
import os
import pty
import select
import signal
import socket
import ssl
import subprocess
import sys
import termios
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable
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


def _stand_in_hosts(root: Path, logins: dict) -> dict:
    """Inventory hosts, one per canned login, whose modules run with the stand-ins; returns their hostvars."""
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name, text in STAND_INS.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    hosts = {}
    for name, (status, rc) in logins.items():
        home = root / name
        home.mkdir()
        (home / "status.json").write_text(json.dumps(status))
        (home / "rc").write_text(str(rc))
        (home / "python").symlink_to(bin_dir / "python")
        hosts[name] = {
            "ansible_connection": "local",
            "ansible_python_interpreter": str(home / "python"),
            "ansible_host": "192.0.2.10",
            # The home the role reads ~/.claude from: the host's directory,
            # never the developer's.
            "claude_code_home": str(home),
        }
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
        (desktop / tool).write_text(DESKTOP_STAND_IN)
        (desktop / tool).chmod(0o755)
    return desktop


def _desktop_calls(root: Path) -> str:
    """What the desktop stand-ins under root were asked to do: nothing, in a test that keeps to its rules."""
    calls = root / ".desktop" / "calls.log"
    return calls.read_text() if calls.exists() else ""


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
        # As ansible.cfg's library: playbooks a test writes elsewhere use just code's and destroy's modules too.
        ANSIBLE_LIBRARY=str(REPO / "playbooks" / "library"),
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


def _end(process: subprocess.Popen) -> None:
    """Kill a run and its process group: its workers and what they started, though not an async job, which leaves it."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
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


def _take_the_terminal() -> None:
    """In the child: a session of its own, with its stdin as the controlling terminal.

    pause reads only from the foreground process group of a terminal, which a
    session leader's group is once the terminal is its controlling one.
    """
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _in_raw_mode(master: int) -> bool:
    """Whether the terminal behind a pseudo-terminal's master is in raw mode, as pause puts it to read keys."""
    try:
        return not termios.tcgetattr(master)[3] & termios.ICANON
    except termios.error:
        return False


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
    (prompt, keys) or (prompt, keys, seconds), types its keys as they are at
    its prompt's next appearance, that many seconds later. Keys go in once the
    prompt has the terminal in raw mode, and again if it has not taken them
    within two seconds. args go on the command line, before the playbook; env
    sets variables on top of the harness's, as _run's does.
    """
    plan = [(prompt, answer.encode() + b"\n")] if steps is None else list(steps)
    plan = [(step[0], step[1], step[2] if len(step) > 2 else 0) for step in plan]
    inventory = _inventory(root, hosts, ordered)
    log = root / "run.log"
    env = _env(root, ANSIBLE_LOG_PATH=str(log), TERM="dumb", **(env or {}))
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [str(ANSIBLE_PLAYBOOK), "-i", str(inventory), *args, "-e", json.dumps(extra), str(playbook)],
        cwd=root,
        env=env,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        preexec_fn=_take_the_terminal,  # noqa: PLW1509 - nothing else runs threads here
    )
    os.close(slave)
    output, searched, typing, deadline = b"", 0, None, time.monotonic() + timeout
    try:
        while True:
            if time.monotonic() > deadline:
                pytest.fail(
                    f"the playbook did not finish within {timeout} seconds:\n" + output.decode(errors="replace")
                )
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
            elif process.poll() is not None:
                break
            if typing is None and plan:
                wanted, keys, delay = plan[0]
                found = output.find(wanted, searched)
                if found >= 0:
                    plan.pop(0)
                    searched = found + len(wanted)
                    typing = {"keys": keys, "from": time.monotonic() + delay, "mark": None, "at": 0.0, "tries": 0}
            if typing is not None:
                now = time.monotonic()
                if typing["mark"] is None:
                    # Only once pause has the terminal in raw mode: before, a
                    # Ctrl-C would be a signal to the whole run, not a key.
                    if now >= typing["from"] and (_in_raw_mode(master) or now > typing["from"] + 5):
                        typing.update(mark=len(output), at=now)
                        os.write(master, typing["keys"])
                elif b"\x1b" in output[typing["mark"] :]:
                    # pause clears its line, an escape sequence, once it has
                    # taken an answer or an interrupt.
                    typing = None
                elif now - typing["at"] > 2 and typing["tries"] < 5:
                    # Keys typed in the moment before pause flushed the
                    # terminal's input are lost, and echoed all the same, so
                    # the echo proves nothing: typed again.
                    typing.update(at=now, tries=typing["tries"] + 1)
                    os.write(master, typing["keys"])
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            _end(process)
        os.close(master)
    _fail_if_the_desktop_was_reached(root)
    assert not plan, f"no {plan[0][0]!r} prompt appeared:\n" + output.decode(errors="replace")
    return process.returncode, output.decode(errors="replace"), log.read_text() if log.exists() else ""


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


def _failures(report: dict) -> dict:
    return {host: f"{name}: {result.get('msg')}" for name, host, result in _results(report) if result.get("failed")}


# ------------------------------------------------------------ the fake APIs ---
# The fakes in tests/molecule/ on a loopback port of their own, for the tests
# that run a playbook against one: the fake Proxmox is TLS, with a certificate
# made for the run, and accepts the token whose secret is PVE_SECRET.
PVE_SECRET = "unit-test-pve-secret"
FAKES = REPO / "tests" / "molecule"


def _free_port() -> int:
    # On every address, as the fakes bind: a port free on loopback alone can be another interface's.
    with socket.socket() as probe:
        probe.bind(("0.0.0.0", 0))
        return probe.getsockname()[1]


def _start(script: Path, *args: str) -> tuple[subprocess.Popen, int]:
    """A fake API on a free port, once it answers. A port can still be taken between the probe and the fake's bind
    - the runs' own connections take local ports too - so a fake that lost it is started again on another."""
    for _ in range(5):
        port = _free_port()
        server = subprocess.Popen([sys.executable, str(script), str(port), *args], stderr=subprocess.PIPE, text=True)
        for _ in range(100):
            if server.poll() is not None:
                said = server.stderr.read()
                if "Address already in use" in said:
                    break
                pytest.fail(f"{script.name} exited: {said}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return server, port
            except OSError:
                time.sleep(0.05)
        else:
            server.terminate()
            server.wait(timeout=10)
            pytest.fail(f"{script.name} did not start")
    pytest.fail(f"{script.name} found no free port in five tries")


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
