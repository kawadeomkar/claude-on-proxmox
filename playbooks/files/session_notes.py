"""The session notes' live section, rewritten from the VM: run by session-notes.sh, beside this file.

`python3 session_notes.py <notes> <vm> <Remote Control unit> <time zone>`, on the VM, as the VM user. It reads,
at once and 2 seconds each, claude auth status, the Remote Control unit, gh's login and tailscale status, and
rewrites only the part of the notes between the live markers, atomically, mode 0600, and only in a regular file
that has them. Every value is cleaned as plain_text cleans (filter_plugins/text.py), no command it runs prints a
token, and the Tailscale row carries the way in over Tailscale, which only the VM knows. Fixed text, copied to the
VM unchanged by just code (playbooks/code.yml); it runs on the VM's own Python 3.
"""

import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

START, END = "<!-- agent-warehouse:live -->", "<!-- /agent-warehouse:live -->"
# The section's markers through 1.x, in notes 1.x wrote: found too, and written over with the new ones.
START_1X, END_1X = "<!-- claude-on-proxmox:live -->", "<!-- /claude-on-proxmox:live -->"
# With more than one Proxmox host, the one the VM is on, which just code writes into the notes: every just line here
# ends with it, as the notes' own lines do. A host's name, and nothing else.
ON = re.compile(r"^<!-- agent-warehouse:on --host ([a-z0-9][a-z0-9-]{0,30}) -->$", re.M)
on = ""
# What plain_text removes (filter_plugins/text.py): whole escape sequences, then every control character.
SEQUENCES = re.compile(
    r"(?:\x1b[\]PX^_]|[\x9d\x90\x98\x9e\x9f])[^\x07\x1b\x9c\n]*(?:\x07|\x1b\\|\x9c)?"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]?"
    r"|\x1b[ -/]*[0-~]?"
)
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def clean(value):
    # One table cell: no control character, no column break, no code span to break out of.
    text = CONTROL.sub(" ", SEQUENCES.sub("", str(value))).strip()
    return text.replace("|", "\\|").replace("`", "'")


def run(*argv):
    # Its exit status and output; None for a tool that is not there, "timeout" for one that did not answer.
    if shutil.which(argv[0]) is None:
        return None
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    except OSError:
        return None
    return done.returncode, done.stdout


def parsed(answer):
    try:
        return json.loads(answer[1])
    except (TypeError, ValueError, IndexError):
        return None


def row(answer, missing, read, *args):
    # One row's value: missing for a tool that is not there, the same words for any that did not answer, and
    # otherwise what read makes of the answer. Each reader below is handed only an answer.
    if answer is None:
        return missing
    if answer == "timeout":
        return "unknown (no answer in 2 s)"
    return read(answer, *args)


def claude_row(answer):
    status = parsed(answer)
    if not isinstance(status, dict):
        return "unknown (claude auth status gave no status)"
    if not status.get("loggedIn"):
        return f"not signed in: just login {vm} --only claude{on} on your machine"
    if status.get("apiKeySource") or status.get("authMethod") == "api_key":
        return f"an API key ({clean(status.get('apiKeySource') or 'ANTHROPIC_API_KEY')})"
    plan = status.get("subscriptionType")
    return f"`{clean(status.get('email') or '?')}` · {clean(status.get('authMethod') or '?')}" + (
        f", {clean(plan)} plan" if plan else ""
    )


def remote_control_row(answer):
    state = clean(answer[1].strip() or "unknown")
    if state == "active":
        return f"live: open claude.ai/code or the Claude app, and pick **{clean(vm)}**"
    return f"not running ({state})"


def github_row(answer):
    # Signed out, gh prints {"hosts":{}}: no github.com entry. Only output that is
    # not that JSON at all is a gh too old for --json.
    report = parsed(answer)
    hosts = report.get("hosts") if isinstance(report, dict) else None
    if not isinstance(hosts, dict):
        return "unknown (gh auth status gave no status; it needs gh 2.81.0 or newer)"
    accounts = hosts.get("github.com") or []
    active = next((account for account in accounts if isinstance(account, dict) and account.get("active")), None)
    if active is None:
        return f"not signed in: just login {vm} --only github{on} on your machine"
    if active.get("state") != "success":
        return f"`{clean(active.get('login') or '?')}`, but its token is refused: just login {vm} --only github{on}"
    return f"`{clean(active.get('login') or '?')}` · git over {clean(active.get('gitProtocol') or '?')}"


def tailscale_row(answer, user):
    status = parsed(answer)
    if not isinstance(status, dict):
        return "unknown (tailscale status gave no status)"
    backend = clean(status.get("BackendState") or "unknown")
    if backend != "Running":
        return f"{backend}: just login {vm} --only tailscale{on} signs it in" if backend == "NeedsLogin" else backend
    node = status.get("Self") or {}
    name = clean(str(node.get("DNSName") or "").rstrip("."))
    addresses = [clean(address) for address in node.get("TailscaleIPs") or [] if ":" not in str(address)]
    row = "running" + (f" as `{name}`" if name else "") + (f" · `{addresses[0]}`" if addresses else "")
    return row + (f" · `ssh {clean(user)}@{name}`" if name else "")


def now():
    # In the zone of the machine just code ran on, the user's; UTC where the VM's tzdata lacks it.
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(zone))
    except Exception:
        return datetime.now(timezone.utc)


def main():
    info = os.lstat(notes)
    if not stat.S_ISREG(info.st_mode):
        return
    with open(notes, encoding="utf-8") as handle:
        text = handle.read()
    start, end, end_marker = text.find(START), text.find(END), END
    if start < 0 or end < start:
        start, end, end_marker = text.find(START_1X), text.find(END_1X), END_1X
    if start < 0 or end < start:
        return
    global on
    named = ON.search(text)
    on = f" --host {named.group(1)}" if named else ""
    user = pwd.getpwuid(os.getuid()).pw_name
    with ThreadPoolExecutor(max_workers=4) as pool:
        claude = pool.submit(run, "claude", "auth", "status")
        remote = pool.submit(run, "systemctl", "is-active", unit)
        github = pool.submit(run, "gh", "auth", "status", "--hostname", "github.com", "--json", "hosts")
        tailscale = pool.submit(run, "tailscale", "status", "--json")
        rows = [
            ("Claude Code", row(claude.result(), "not installed", claude_row)),
            ("Remote Control", row(remote.result(), "unknown (no systemctl)", remote_control_row)),
            ("GitHub (`gh`)", row(github.result(), "gh not installed", github_row)),
            ("Tailscale", row(tailscale.result(), "not installed", tailscale_row, user)),
        ]
    live = "\n".join(
        [START, "## Signed in", "", f"| | as of {now().strftime('%H:%M %Z on %d %b')} |", "|---|---|"]
        + [f"| {label} | {value} |" for label, value in rows]
        + [END]
    )
    updated = text[:start] + live + text[end + len(end_marker) :]
    if updated == text:
        return
    descriptor, temporary = tempfile.mkstemp(dir=os.path.dirname(notes), prefix=".session-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(updated)
        os.chmod(temporary, 0o600)
        os.replace(temporary, notes)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    notes, vm, unit, zone = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    main()
