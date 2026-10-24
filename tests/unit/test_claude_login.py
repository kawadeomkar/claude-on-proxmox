"""Claude Code's browser sign-in (roles/claude_code/tasks/browser_login.yml), run for real.

`just login ... --only claude` and `just deploy` run playbooks/claude_login.yml,
whose role starts `claude auth login` on each VM in the background, shows the
sign-in page, takes the code the page shows from a prompt on the controller,
hands it to claude through a named pipe and waits for its answer. Here every
host is the controller itself, over the local connection, through the harness
in harness.py, with the stand-in claude of tests/molecule/fake_claude.sh first
on PATH, which behaves the way Claude Code 2.1.296 was probed to
(docs/architecture/roles.md §5.5): a line without "#" is an invalid code and it reads again, a rejected
code exits 1, an accepted one writes .credentials.json and exits 0 - and, on
request, refuses in other words, fails before its page, or is gone before the
code comes.

Nothing here can reach a browser, a clipboard, the developer's terminal or
systemd: claude_code_login_desktop is off for every host (and the harness's
desktop stand-ins fail any test that reaches them anyway), stdin is /dev/null
except for the tests that type at a pseudo-terminal of their own, and Remote
Control is off, so the role never installs a unit on this machine - the
configure scenario's side effect starts it for real, in its container.

What these guard:
- A VM is signed in from a code given in place of the prompt, its files and
  async job are gone afterwards, the code reaches no output, and
  claude_login_args reach `claude auth login`.
- A rejected code and a malformed one are named failures that quote claude's
  answer, and fail the run only after every VM, those after them included, has
  had its turn.
- No code at all (Enter, or a run with no terminal) ends the attempt - claude is
  killed rather than left waiting on its pipe - and is not a failure.
- A VM whose API key is its login is never sent through the sign-in.
- Several names are signed in one after another, never together, and the steps
  on this machine never run in a test.
- claude's other answers - an account on hold, a refusal written with escape
  sequences - end the wait at once and are quoted without their control
  characters; an error where the page should be, and an attempt that ended
  before its code, are named failures that end with the command to retry.
- A sign-in started while another of the same VM still waits is refused, and
  leaves the other's files and process alone; the files of an attempt a dead
  run left behind are cleared, and nothing still writing to them reaches the
  new attempt. The code goes only into the pipe.
- At a terminal: the page comes before the prompt, the code stays out of the
  run log even at -vvv, a partly copied code is asked for again, a code typed
  after the attempt expired is said to be too late, and Ctrl-C then A at the
  prompt ends the run there.
- The browser coming back by itself: claude's callback port is forwarded from
  this machine, and once the browser brings the code back, the VM is signed in
  with no prompt shown; a forward that fails, and a page that is not Claude's,
  fall back to the code; a browser that never comes back ends the attempt and
  skips the VM's later sign-ins; and it is on only where the page opens here.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from harness import REPO, STAND_INS, _failures, _left_to_do, _messages, _results, _run, _run_at_a_terminal

ROLE = REPO / "roles" / "claude_code"
FAKE_CLAUDE = REPO / "tests" / "molecule" / "fake_claude.sh"
PLAYBOOK = REPO / "playbooks" / "claude_login.yml"
# The page the stand-in claude prints.
URL = "https://claude.com/cai/oauth/authorize?code=true&state=fake-state"

CODES = {
    "accepted": "fake-code-accepted#fake-state-1",
    "rejected": "fake-code-rejected#fake-state-2",
    # No "#": claude says the code is invalid and waits for another line.
    "invalid": "fake-code-without-its-state",
    "pty": "fake-code-typed#fake-state-3",
    "sso": "fake-code-sso#fake-state-4",
    # What a double-click on the page selects: up to the "#".
    "partial": "fake-code-partly-copied",
    "whole": "fake-code-partly-copied#fake-state-5",
    "late": "fake-code-late#fake-state-6",
}
CREDENTIALS = "fake-access-token-0000"
API_KEY = "sk-ant-api03-fake-key-0001"
TIMEOUT = 8
# The answer timeout of the cases that must end without waiting for it: a run
# that waited would take this long for that VM, far more than any of them takes.
LONG = 60

# claude's answer for an account on hold, which it writes after a bare newline
# and then exits 1 (Claude Code's auth login handler).
ON_HOLD = "Your account is on hold and can't sign in to Claude Code. View details or appeal: https://example.com/appeal"
# A clipboard write (OSC 52), a new window title and a colour, as a VM user
# could make claude print them: in a failure they reached the developer's
# terminal and the run log raw.
ESCAPES = "\x1b]52;c;b3duZWQ=\x07\x1b]0;retitled\x07\x1b[31m"
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _hosts(root: Path, cases: dict) -> dict:
    """Inventory hosts, one per case, whose modules run with the stand-ins and their own HOME; returns hostvars."""
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name in ("python", "bash"):
        (bin_dir / name).write_text(STAND_INS[name])
        (bin_dir / name).chmod(0o755)
    (bin_dir / "claude").write_text(FAKE_CLAUDE.read_text())
    (bin_dir / "claude").chmod(0o755)
    hosts = {}
    for name, case in cases.items():
        home = root / name
        fake = home / ".claude" / "fake"
        fake.mkdir(parents=True)
        if case.get("signed_in"):
            (home / ".claude" / ".credentials.json").write_text('{"claudeAiOauth": {}}\n')
        if case.get("reject"):
            (fake / "fake-reject").touch()
        if case.get("gone"):
            (fake / "fake-gone").touch()
        if "refusal" in case:
            (fake / "fake-refusal").write_text(case["refusal"])
        if "no_page" in case:
            (fake / "fake-no-page").write_text(case["no_page"])
        if case.get("foreign_page"):
            (fake / "fake-foreign-page").touch()
        (home / "python").symlink_to(bin_dir / "python")
        hosts[name] = {
            "ansible_connection": "local",
            "ansible_python_interpreter": str(home / "python"),
            "ansible_host": "192.0.2.10",
            "claude_code_home": str(home),
            # Never this machine's browser, and never a unit in its
            # /etc/systemd: the side effect of the configure scenario starts
            # Remote Control after a sign-in, in its container.
            "claude_code_login_desktop": False,
            "claude_code_remote_control": False,
            "claude_code_login_timeout": case.get("timeout", TIMEOUT),
            # The async job's directory, under the host's HOME: async_status
            # resolves "~" through the connection's shell, which here is the
            # developer's real home (see test_github_auth.py).
            "ansible_async_dir": str(home / ".ansible_async"),
            **({"claude_code_login_code": case["code"]} if "code" in case else {}),
            **({"claude_code_anthropic_api_key": case["api_key"]} if "api_key" in case else {}),
            **({"claude_code_login_args": case["args"]} if "args" in case else {}),
        }
    return hosts


def _calls(root: Path, host: str) -> list[str]:
    log = root / host / ".claude" / "fake" / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def _login_dir(root: Path, host: str) -> Path:
    return root / host / ".config" / "claude-on-proxmox"


def _leftovers(root: Path, host: str) -> list[str]:
    login_dir = _login_dir(root, host)
    async_dir = root / host / ".ansible_async"
    found = [p.name for p in login_dir.glob("claude-login.*")] if login_dir.exists() else []
    return found + ([p.name for p in async_dir.iterdir()] if async_dir.exists() else [])


# The ssh a forward to the VM would run, as an extra var: it is an internal of
# the role (vars/main.yml), which outranks a host's own. Nowhere, unless a test
# gives its stand-in: no test may reach this machine's ssh.
NO_SSH = "/nonexistent/claude-login-test-ssh"


def _extra(*names: str, ssh: str = NO_SSH) -> dict:
    """just login <names> --only claude, as the harness runs it: no discovery, no sudo to give, no real ssh."""
    return {
        "claude_vm_discovery": False,
        "ansible_become": False,
        "vm_name": ",".join(names),
        "claude_code_login_ssh": ssh,
    }


def _turns(report: dict) -> list[str]:
    """The VMs in the order they had their turn: under serial, each turn is a play of its own in the report."""
    turns = []
    for play in report["plays"]:
        hosts = {host for task in play["tasks"] for host in task["hosts"]}
        if len(hosts) == 1 and hosts != {"localhost"}:
            turns.extend(hosts)
    return turns


def _seconds_per_turn(report: dict) -> dict:
    took = {}
    for play in report["plays"]:
        hosts = {host for task in play["tasks"] for host in task["hosts"]}
        if len(hosts) == 1 and hosts != {"localhost"}:
            (host,) = hosts
            span = play["play"]["duration"]
            seconds = (datetime.fromisoformat(span["end"]) - datetime.fromisoformat(span["start"])).total_seconds()
            took[host] = took.get(host, 0) + seconds
    return took


def _strings(value):
    """Every string in a report, however deep."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


# In the order they run, which the inventory keeps: the failures first, so that
# the VMs after them having their turn is something every run shows.
CASES = {
    "rejected": {"code": CODES["rejected"], "reject": True},
    "invalid": {"code": CODES["invalid"]},
    "accepted": {"code": CODES["accepted"]},
    # No code given and no terminal: the prompt returns empty at once.
    "skipped": {},
    "already": {"signed_in": True},
    # An API key and no claude.ai login: the key is the login on the API path.
    "keyed": {"api_key": API_KEY},
    # claude_login_args, such as --sso, for claude auth login.
    "sso": {"code": CODES["sso"], "args": ["--sso"]},
}
# The VMs that show a sign-in page: no login, and no key.
SIGNING_IN = ["rejected", "invalid", "accepted", "skipped", "sso"]


@pytest.fixture(scope="module")
def signed(tmp_path_factory):
    """claude_login.yml for every case at once, as just login ... --only claude runs it: root and report."""
    root = tmp_path_factory.mktemp("claude_login_flow")
    hosts = _hosts(root, CASES)
    report = _run(root, PLAYBOOK, hosts, "-e", json.dumps(_extra(*CASES)), ordered=True)
    return root, report


def test_a_given_code_signs_the_vm_in_and_leaves_nothing_behind(signed):
    root, report = signed
    assert (root / "accepted" / ".claude" / ".credentials.json").read_text().count(CREDENTIALS) == 1
    calls = _calls(root, "accepted")
    assert "auth login" in calls and "accepted" in calls, calls
    assert "TERM" not in calls, calls
    assert _leftovers(root, "accepted") == []
    said = _messages(report, "Report the login state and what to do about it")["accepted"]
    # Signed in by this run, not found so: "already signed in as" would match
    # a looser test.
    assert said.startswith("accepted: signed in as dev@example.com (claude.ai, max plan).")
    # Remote Control is off on these hosts; on a VM it starts next (below).
    assert "Remote Control is off on this VM." in said


def test_the_page_is_said_with_what_to_do_there(signed):
    # That it comes before the prompt is shown at a terminal, below.
    _, report = signed
    shown = _messages(report, "Say the page and what happens next")["accepted"]
    assert URL in shown
    assert "paste the code the page shows at the prompt below" in shown


def test_a_rejected_or_malformed_code_is_a_named_failure_after_every_vm_had_its_turn(signed):
    root, report = signed
    assert report["rc"] != 0
    failures = _failures(report)
    assert set(failures) == {"rejected", "invalid"}, failures
    assert "the Claude sign-in did not complete" in failures["rejected"]
    assert "Login failed: Request failed with status code 400" in failures["rejected"]
    assert "just login rejected --only claude again" in failures["rejected"]
    assert "Invalid code. Please make sure the full code was copied." in failures["invalid"]
    for host in ("rejected", "invalid"):
        assert not (root / host / ".claude" / ".credentials.json").exists()
        assert _leftovers(root, host) == []
    # The two failed first, and the VMs after them still had their turn.
    assert _turns(report) == list(CASES), _turns(report)
    for host in ("accepted", "sso"):
        assert (root / host / ".claude" / ".credentials.json").exists(), host


def test_claude_waiting_on_a_malformed_code_is_ended_not_left_waiting(signed):
    root, _ = signed
    calls = _calls(root, "invalid")
    assert "invalid" in calls and "TERM" in calls, calls


def test_no_code_ends_the_attempt_without_failing(signed):
    root, report = signed
    assert "skipped" not in _failures(report)
    said = _messages(report, "Say that no code was entered")["skipped"]
    assert "just login skipped --only claude signs it in later" in said
    calls = _calls(root, "skipped")
    # Killed, not handed a blank line: claude would answer that "Invalid code"
    # and go on waiting.
    assert "auth login" in calls and "TERM" in calls and "invalid" not in calls, calls
    assert not (root / "skipped" / ".claude" / ".credentials.json").exists()
    assert _leftovers(root, "skipped") == []


def test_next_steps_keep_a_sign_in_left_to_do_and_nothing_once_signed_in(signed):
    # What the run's last lines list (callback_plugins/claude_on_proxmox.py).
    _, report = signed
    left = _left_to_do(report)
    assert left["accepted"]["claude"] == ""
    assert left["already"]["claude"] == ""
    assert "just login skipped --only claude" in left["skipped"]["claude"]


def test_a_signed_in_vm_is_only_reported(signed):
    root, report = signed
    assert "auth login" not in _calls(root, "already")
    said = _messages(report, "Report the login state and what to do about it")["already"]
    assert "already signed in as dev@example.com" in said


def test_a_vm_whose_api_key_is_its_login_is_not_sent_through_the_sign_in(signed):
    root, report = signed
    calls = _calls(root, "keyed")
    # Asked how it is signed in, and nothing more.
    assert "auth status" in calls and "auth login" not in calls, calls
    assert "keyed" not in _failures(report)


def test_the_login_arguments_reach_claude(signed):
    root, _ = signed
    calls = _calls(root, "sso")
    assert "auth login --sso" in calls and "accepted" in calls, calls


def test_vms_are_signed_in_one_after_another(signed):
    # Under serial each batch is a play of its own in the report: every VM's
    # turn in one of its own, in the order given, is the proof.
    _, report = signed
    assert _turns(report) == list(CASES)


def test_no_code_and_no_secret_reach_the_output(signed):
    _, report = signed
    text = json.dumps(report)
    for code in CODES.values():
        assert code not in text
    assert CREDENTIALS not in text
    assert API_KEY not in text


def test_the_desktop_steps_never_run_in_a_test(signed):
    # Both steps on this machine - finding an opener, opening the page - for
    # every VM that showed its page: skipped by claude_code_login_desktop. The
    # harness's stand-ins would fail the run had either called an opener.
    _, report = signed
    on_here = [task["name"] for task in _flow() if task.get("delegate_to") == "localhost"]
    assert on_here
    for step in on_here:
        for host in SIGNING_IN:
            # The report names a role's task after its role.
            ran = [result for name, vm, result in _results(report) if vm == host and name.endswith(step)]
            assert ran and all(result.get("skipped") for result in ran), (step, host, ran)


def test_the_sign_in_reads_this_machine_s_desktop_switch():
    # Every scenario and harness switches login_desktop off; it reaches the
    # role only through this line.
    wiring = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert re.fullmatch(r"\{\{\s*login_desktop\s*\}\}", wiring["claude_code_login_desktop"]), wiring.get(
        "claude_code_login_desktop"
    )


def test_each_vm_records_whether_its_code_was_entered(tmp_path):
    # just deploy's GitHub step decides VM by VM from these: a code entered
    # signs that VM in, Enter there skips it, and a VM that showed no prompt
    # (signed in already, or an API key) has neither and is asked on its own.
    cases = {"answered": {"code": CODES["accepted"]}, "unanswered": {}, "already": {"signed_in": True}}
    hosts = _hosts(tmp_path, cases)
    playbook = tmp_path / "answered.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {"import_playbook": str(PLAYBOOK)},
                {
                    "hosts": "claude_vms",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "ansible.builtin.debug": {
                                "msg": "answered={{ claude_code_login_answered | default('unset') }}"
                                " skipped={{ claude_code_login_skipped | default('unset') }}"
                            }
                        }
                    ],
                },
                {
                    "hosts": "localhost",
                    "gather_facts": False,
                    "tasks": [
                        {"ansible.builtin.debug": {"msg": "here={{ claude_code_login_answered | default('unset') }}"}}
                    ],
                },
            ]
        )
    )
    report = _run(tmp_path, playbook, hosts, "-e", json.dumps(_extra(*cases)))
    said = {
        host: r["msg"] for _, host, r in _results(report) if str(r.get("msg", "")).startswith(("answered=", "here="))
    }
    assert said == {
        "answered": "answered=True skipped=False",
        "unanswered": "answered=False skipped=True",
        "already": "answered=unset skipped=unset",
        # The facts are the VM's own: nothing is written onto this machine.
        "localhost": "here=unset",
    }, said


# ------------------------------------------- the browser coming back by itself
# With claude_code_login_redirect, claude's callback port is forwarded from this
# machine and the page that redirects to it opens here, so the browser comes
# back to claude by itself and nothing is pasted. The VM is this machine here,
# so the callback is reachable as it is: a stand-in ssh records the forward it
# was asked for, and the test plays the browser, calling the callback with a
# code and the page's state - and never following its redirect to Anthropic.
FORWARD = """#!/bin/sh
printf '%s\\n' "$*" >> "$(dirname "$0")/forward.log"
[ -e "$(dirname "$0")/forward-refused" ] && exit 255
exit 0
"""


def _forwarding(root: Path, hosts: dict, refused: bool = False, wait: int = 30) -> Path:
    """A stand-in ssh for the forward, and every host set to come back by itself; returns the stand-in, for _extra."""
    ssh = root / "forward" / "ssh"
    ssh.parent.mkdir()
    ssh.write_text(FORWARD)
    ssh.chmod(0o755)
    if refused:
        (ssh.parent / "forward-refused").touch()
    for host in hosts.values():
        host.update(claude_code_login_redirect=True, claude_code_login_approval_timeout=wait)
    return ssh


def _browser(root: Path, host: str, answers: list) -> threading.Thread:
    """The browser, for one host: once claude has handed its browser the page, its callback gets a code."""

    def come_back():
        page_file = _login_dir(root, host) / "claude-login.page"
        end = time.time() + 60
        while time.time() < end:
            page = page_file.read_text().strip() if page_file.exists() else ""
            if page:
                # A moment, as a person takes, so the run is waiting by then.
                time.sleep(2)
                port = re.search(r"localhost%3A([0-9]+)%2Fcallback", page).group(1)
                state = re.search(r"[?&]state=([^&]+)", page).group(1)
                connection = http.client.HTTPConnection("127.0.0.1", int(port), timeout=10)
                connection.request("GET", f"/callback?code=fake-code-browser&state={state}")
                answers.append((port, connection.getresponse().status))
                return
            time.sleep(0.2)

    thread = threading.Thread(target=come_back, daemon=True)
    thread.start()
    return thread


def _task_ran(report: dict, host: str, step: str) -> bool:
    return any(vm == host and step in name and not result.get("skipped") for name, vm, result in _results(report))


def test_the_browser_coming_back_signs_the_vm_in_with_nothing_pasted(tmp_path):
    hosts = _hosts(tmp_path, {"browser": {}})
    ssh = _forwarding(tmp_path, hosts)
    answers: list = []
    thread = _browser(tmp_path, "browser", answers)
    report = _run(tmp_path, PLAYBOOK, hosts, "-e", json.dumps(_extra("browser", ssh=str(ssh))))
    thread.join(timeout=5)
    assert report["rc"] == 0, _failures(report)
    assert (tmp_path / "browser" / ".claude" / ".credentials.json").read_text().count(CREDENTIALS) == 1
    calls = _calls(tmp_path, "browser")
    assert "callback" in calls and "accepted" in calls, calls
    # The browser was answered with claude's redirect, which nobody follows here.
    ((port, status),) = answers
    assert status == 302
    # No prompt: the person's part was in the browser.
    assert not _task_ran(report, "browser", "Paste the code the browser shows for")
    said = _messages(report, "browser_login | Say the page and what happens next")["browser"]
    assert said.startswith("browser: sign Claude Code in at https://claude.com/cai/oauth/authorize?"), said
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A" + port in said
    assert said.endswith(
        "this run carries on by itself once your browser comes back, waiting up to 30 seconds;"
        " Ctrl-C ends the whole run."
    ), said
    # One forward, of that port, on this machine's loopback, to the VM user at the VM.
    (forwarded,) = (ssh.parent / "forward.log").read_text().splitlines()
    assert f"-L localhost:{port}:127.0.0.1:{port}" in forwarded, forwarded
    assert " -f " in f" {forwarded} " and "ExitOnForwardFailure=yes" in forwarded, forwarded
    assert "dev@192.0.2.10 " in forwarded and "exec sleep 90" in forwarded, forwarded
    assert _leftovers(tmp_path, "browser") == []


def test_a_forward_that_cannot_be_made_falls_back_to_the_code(tmp_path):
    # A port already taken on this machine fails the forward: the printed page
    # and its code then, as before - here with no terminal, so nothing is entered.
    hosts = _hosts(tmp_path, {"taken": {}})
    ssh = _forwarding(tmp_path, hosts, refused=True)
    report = _run(tmp_path, PLAYBOOK, hosts, "-e", json.dumps(_extra("taken", ssh=str(ssh))))
    assert report["rc"] == 0, _failures(report)
    assert _task_ran(report, "taken", "Paste the code the browser shows for")
    said = _messages(report, "browser_login | Say the page and what happens next")["taken"]
    assert (
        said == f"taken: sign Claude Code in at {URL} Authorise there, then paste the code the page shows at the"
        " prompt below. Enter alone skips this VM."
    ), said
    assert not (tmp_path / "taken" / ".claude" / ".credentials.json").exists()


@pytest.mark.parametrize(
    "here",
    [
        # make run over SSH: the person's browser is on another machine, and the
        # page would send it to that machine's localhost.
        {"SSH_CONNECTION": "192.0.2.1 50000 192.0.2.2 22"},
        # Nothing on this machine to open the page in.
        {"BROWSER": ""},
    ],
    ids=["over-ssh", "no-browser"],
)
def test_a_browser_that_could_not_come_back_falls_back_to_the_code(tmp_path, here):
    hosts = _hosts(tmp_path, {"elsewhere": {}})
    ssh = _forwarding(tmp_path, hosts)
    report = _run(tmp_path, PLAYBOOK, hosts, "-e", json.dumps(_extra("elsewhere", ssh=str(ssh))), env=here)
    assert report["rc"] == 0, _failures(report)
    assert not (ssh.parent / "forward.log").exists()
    assert _task_ran(report, "elsewhere", "Paste the code the browser shows for")
    said = _messages(report, "browser_login | Say the page and what happens next")["elsewhere"]
    assert said == (
        f"elsewhere: sign Claude Code in at {URL} Authorise there, then paste the code the page shows at the prompt"
        " below. Enter alone skips this VM."
    ), said


def test_a_page_that_is_not_claude_s_is_neither_forwarded_nor_opened(tmp_path):
    hosts = _hosts(tmp_path, {"foreign": {"foreign_page": True}})
    ssh = _forwarding(tmp_path, hosts)
    report = _run(tmp_path, PLAYBOOK, hosts, "-e", json.dumps(_extra("foreign", ssh=str(ssh))))
    assert report["rc"] == 0, _failures(report)
    assert not (ssh.parent / "forward.log").exists()
    assert _task_ran(report, "foreign", "Paste the code the browser shows for")


def test_a_browser_that_never_comes_back_ends_the_attempt_and_skips_the_vm(tmp_path):
    hosts = _hosts(tmp_path, {"away": {}})
    ssh = _forwarding(tmp_path, hosts, wait=6)
    playbook = tmp_path / "away.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {"import_playbook": str(PLAYBOOK)},
                {
                    "hosts": "claude_vms",
                    "gather_facts": False,
                    "tasks": [{"ansible.builtin.debug": {"msg": "skipped={{ claude_code_login_skipped }}"}}],
                },
            ]
        )
    )
    report = _run(tmp_path, playbook, hosts, "-e", json.dumps(_extra("away", ssh=str(ssh))))
    assert report["rc"] == 0, _failures(report)
    said = _messages(report, "browser_login | Say that the browser did not come back")["away"]
    assert said == (
        "away: the browser did not come back within 6 seconds, so Claude Code is left signed out there. just"
        " login away --only claude signs it in later."
    )
    assert "just login away --only claude" in _left_to_do(report)["away"]["claude"]
    # claude was ended, not left waiting on its callback.
    assert "TERM" in _calls(tmp_path, "away")
    assert {r["msg"] for _, host, r in _results(report) if str(r.get("msg", "")).startswith("skipped=")} == {
        "skipped=True"
    }
    assert _leftovers(tmp_path, "away") == []


def test_the_browser_comes_back_by_itself_only_where_the_page_opens_here():
    defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
    assert re.fullmatch(r"\{\{\s*claude_code_login_desktop\s*\}\}", defaults["claude_code_login_redirect"])


# ----------------------------------------------------- what else claude can do

REFUSED = {
    # The attempt had ended by the time the code came: claude gone.
    "gone": {"code": CODES["late"], "gone": True, "timeout": LONG},
    # claude's own refusal for an account on hold.
    "on-hold": {"code": CODES["accepted"], "refusal": f"\n{ON_HOLD}\n", "timeout": LONG},
    # A refusal written with escape sequences.
    "escapes": {
        "code": CODES["accepted"],
        "refusal": f"{ESCAPES}Login failed: this sign-in is not allowed\x1b[0m\n",
        "timeout": LONG,
    },
    # An error, with escape sequences, where the page should be, after a line
    # of its own and ending its sentence: said on one line, with one stop.
    "no-page": {
        "code": CODES["accepted"],
        "no_page": f"Opening browser to sign in…\n{ESCAPES}Error: managed settings forbid this sign-in.\n",
        "timeout": LONG,
    },
}


@pytest.fixture(scope="module")
def refused(tmp_path_factory):
    """claude_login.yml for claude's other outcomes, each given a code and LONG seconds to answer: root, report."""
    root = tmp_path_factory.mktemp("claude_login_refused")
    hosts = _hosts(root, REFUSED)
    report = _run(root, PLAYBOOK, hosts, "-e", json.dumps(_extra(*REFUSED)), ordered=True, timeout=len(REFUSED) * 150)
    return root, report


def test_claude_s_other_outcomes_end_the_wait_at_once(refused):
    # Waiting for an answer that had come in other words, or for a claude that
    # was gone, cost the whole answer timeout.
    _, report = refused
    took = _seconds_per_turn(report)
    assert set(took) == set(REFUSED), took
    assert all(seconds < LONG / 2 for seconds in took.values()), took


def test_a_code_given_after_the_attempt_ended_is_said_to_be_too_late(refused):
    root, report = refused
    failure = _failures(report)["gone"]
    assert "gone: the sign-in had ended before the code was pasted (claude had exited)." in failure
    assert failure.endswith("Run just login gone --only claude again for a fresh sign-in page.")
    # It went before any code was handed over.
    assert _calls(root, "gone")[-1] == "gone"
    assert _leftovers(root, "gone") == []


def test_an_account_on_hold_is_quoted(refused):
    _, report = refused
    failure = _failures(report)["on-hold"]
    assert "the Claude sign-in did not complete." in failure
    assert ON_HOLD in failure
    assert failure.endswith("Run just login on-hold --only claude again for a fresh sign-in page.")


def test_an_error_in_place_of_the_page_ends_with_the_command_to_retry(refused):
    _, report = refused
    failure = _failures(report)["no-page"]
    assert "claude on no-page did not start the sign-in:" in failure
    assert "Opening browser to sign in… Error: managed settings forbid this sign-in. Run" in failure
    assert ".." not in failure
    assert failure.endswith("Run just login no-page --only claude again.")


def test_what_claude_prints_reaches_no_message_with_its_control_characters(refused):
    _, report = refused
    failures = _failures(report)
    assert "Login failed: this sign-in is not allowed" in failures["escapes"]
    for name, host, result in _results(report):
        for text in _strings(result):
            assert not CONTROL.search(text), (name, host, text)
    assert not CONTROL.search(report["stderr"]), report["stderr"]
    # Each sequence goes whole, its payload with it: a clipboard write, a title.
    for failure in failures.values():
        assert "b3duZWQ" not in failure and "retitled" not in failure and "[31m" not in failure, failure
    # One line each, as every message the project prints.
    assert all("\n" not in failure for failure in failures.values()), failures


# ------------------------------------------------- other attempts on the same VM


def _seed_attempt(root: Path, host: str, pid: int) -> dict:
    """What an earlier attempt leaves on a VM: claude's output, the pipe, and the pidfile naming pid."""
    login_dir = _login_dir(root, host)
    login_dir.mkdir(parents=True, exist_ok=True)
    files = {suffix: login_dir / f"claude-login.{suffix}" for suffix in ("out", "in", "pid")}
    files["out"].write_text(
        "Opening browser to sign in…\n"
        "If the browser didn't open, visit: https://stale.example/an-earlier-page\n"
        "Paste code here if prompted > "
    )
    os.mkfifo(files["in"], 0o600)
    files["pid"].write_text(f"{pid}\n")
    return files


def _state(files: dict) -> dict:
    """Each file's identity and, for those that are not the pipe, its bytes."""
    return {
        name: (path.stat().st_ino, path.stat().st_mtime_ns, None if name == "in" else path.read_bytes())
        for name, path in files.items()
    }


def test_a_sign_in_is_refused_while_another_of_the_vm_still_waits(tmp_path):
    hosts = _hosts(tmp_path, {"busy": {"code": CODES["accepted"]}})
    # The other run's claude, still waiting: what its command line says is
    # what tells it from a pid reused since.
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)", "auth", "login"])
    try:
        files = _seed_attempt(tmp_path, "busy", other.pid)
        before = _state(files)
        report = _run(tmp_path, PLAYBOOK, hosts, "-e", json.dumps(_extra("busy")))
        assert other.poll() is None, "the waiting sign-in's claude was ended"
        assert _state(files) == before
    finally:
        other.kill()
        other.wait()
    failure = _failures(report)["busy"]
    assert f"another sign-in of this VM is still waiting for its code (claude, pid {other.pid})" in failure
    assert failure.endswith("run just login busy --only claude again.")
    assert "auth login" not in _calls(tmp_path, "busy")


# Holds the pipe and the output of an earlier attempt, as a process a dead run
# left behind could: reads whatever reaches that pipe into its third argument,
# and writes a page of its own at the start of that output, over and over.
STALE = """
import os, select, sys
pipe, output, taken = sys.argv[1:]
reader = os.open(pipe, os.O_RDWR | os.O_NONBLOCK)
writer = os.open(output, os.O_WRONLY)
with open(taken, "ab", buffering=0) as kept:
    while True:
        os.pwrite(writer, b"https://stale.example/a-page-from-before\\n", 0)
        if select.select([reader], [], [], 0.02)[0]:
            kept.write(os.read(reader, 4096))
"""


def test_a_sign_in_over_what_a_dead_run_left_behind_starts_afresh(tmp_path):
    hosts = _hosts(tmp_path, {"stale": {"code": CODES["accepted"]}})
    taken = tmp_path / "taken"
    taken.touch()
    files = _seed_attempt(tmp_path, "stale", 0)
    stale = subprocess.Popen([sys.executable, "-c", STALE, str(files["in"]), str(files["out"]), str(taken)])
    try:
        # Not a sign-in, by its command line: nothing to refuse for.
        files["pid"].write_text(f"{stale.pid}\n")
        deadline = time.monotonic() + 10
        while b"a-page-from-before" not in files["out"].read_bytes():
            assert time.monotonic() < deadline and stale.poll() is None, "the stale process did not start"
            time.sleep(0.05)
        report = _run(tmp_path, PLAYBOOK, hosts, "-e", json.dumps(_extra("stale")))
        assert stale.poll() is None, "a process that was not the sign-in was ended"
    finally:
        stale.kill()
        stale.wait()
    assert report["rc"] == 0, _failures(report)
    assert (tmp_path / "stale" / ".claude" / ".credentials.json").exists()
    # The page said is the new attempt's, and nothing the stale process wrote
    # was read as this attempt's output, nor did the code reach its pipe.
    assert URL in _messages(report, "Say the page and what happens next")["stale"]
    assert "stale.example" not in json.dumps(report)
    assert taken.read_bytes() == b""
    assert _leftovers(tmp_path, "stale") == []


# ------------------------------------------------------------- at a terminal

# The most a run can log of a code: -vvv logs every result in full, the
# prompt's typed text among them, and invocation injection - off by default in
# ansible-core 2.21 - adds each module's arguments, a hand-over's stdin
# included. Only no_log keeps the code out then.
VERBOSE = ("-vvv",)
INJECTED = {"ansible_inject_invocation": True}


def test_the_prompt_reads_the_terminal_and_keeps_the_code_out_of_the_log(tmp_path):
    hosts = _hosts(tmp_path, {"pty": {}})
    extra = {**_extra("pty"), **INJECTED}
    rc, output, log = _run_at_a_terminal(tmp_path, PLAYBOOK, hosts, extra, CODES["pty"], args=VERBOSE)
    assert rc == 0, output
    assert "accepted" in _calls(tmp_path, "pty")
    assert (tmp_path / "pty" / ".claude" / ".credentials.json").exists()
    # The page first: at the prompt the person needs the code it shows. The
    # prompt under its block, which says nothing that looks like the prompt.
    block = "##  USER INPUT REQUIRED  -  pty  -  Claude Code sign-in"
    assert URL in output and output.index(URL) < output.index(block) < output.index("Code:"), output
    assert "Paste the code the sign-in page shows, then press Enter. Enter alone skips this VM." in output
    assert log, "no run log was written"
    assert CODES["pty"] not in log
    assert _leftovers(tmp_path, "pty") == []


def test_a_partly_copied_code_is_asked_for_again(tmp_path):
    hosts = _hosts(tmp_path, {"pty": {}})
    steps = [(b"Code:", CODES["partial"].encode() + b"\n"), (b"Code:", CODES["whole"].encode() + b"\n")]
    extra = {**_extra("pty"), **INJECTED}
    rc, output, log = _run_at_a_terminal(tmp_path, PLAYBOOK, hosts, extra, steps=steps, args=VERBOSE)
    assert rc == 0, output
    assert "That was part of the code only: paste all of it, what follows # included." in output
    calls = _calls(tmp_path, "pty")
    # The same attempt and page: claude said "Invalid code", went on reading,
    # and took the whole code.
    assert calls.count("auth login") == 1 and "TERM" not in calls, calls
    assert calls.index("invalid") < calls.index("accepted"), calls
    assert (tmp_path / "pty" / ".claude" / ".credentials.json").exists()
    for code in (CODES["partial"], CODES["whole"]):
        assert code not in log
    assert _leftovers(tmp_path, "pty") == []


def test_a_code_typed_after_the_attempt_expired_is_said_to_be_too_late(tmp_path):
    # An attempt of two seconds, and a code typed six seconds after the
    # prompt: Ansible's async limit has ended claude by then.
    hosts = _hosts(tmp_path, {"late": {"timeout": 2}})
    extra = {**_extra("late"), "claude_code_login_attempt_seconds": 0}
    steps = [(b"Code:", CODES["late"].encode() + b"\n", 6)]
    rc, output, _ = _run_at_a_terminal(tmp_path, PLAYBOOK, hosts, extra, steps=steps)
    assert rc != 0, output
    assert "late: the sign-in had ended before the code was pasted (its page expired" in output, output
    assert "accepted" not in _calls(tmp_path, "late")
    assert _leftovers(tmp_path, "late") == []


def test_ctrl_c_then_a_at_the_prompt_ends_the_run_there(tmp_path):
    hosts = _hosts(tmp_path, {"first": {}, "second": {}})
    steps = [(b"Code:", b"\x03"), (b"Press 'C' to continue the play or 'A' to abort", b"a")]
    rc, output, _ = _run_at_a_terminal(
        tmp_path, PLAYBOOK, hosts, _extra("first", "second"), steps=steps, ordered=True, timeout=120
    )
    assert rc != 0, output
    assert "user requested abort" in output, output
    first = _calls(tmp_path, "first")
    # Its claude was ended and its files removed, as for any other end.
    assert "auth login" in first and "TERM" in first, first
    assert _leftovers(tmp_path, "first") == []
    # And the next VM's sign-in never started.
    assert "auth login" not in _calls(tmp_path, "second"), output


# --------------------------------------------------------- the files themselves


def _tasks(items):
    for item in items:
        if "block" in item:
            for section in ("block", "rescue", "always"):
                yield from _tasks(item.get(section) or [])
        else:
            yield item


def _flow():
    return list(_tasks(yaml.safe_load((ROLE / "tasks" / "browser_login.yml").read_text())))


def _writers():
    """The tasks that hand claude a code: the commands given a stdin."""
    return [task for task in _flow() if "stdin" in (task.get("ansible.builtin.command") or {})]


def test_the_steps_on_this_machine_never_use_its_sudo():
    on_here = [t for t in _flow() if t.get("delegate_to") == "localhost"]
    # The opener is looked for before the forward, which only a browser here needs.
    assert [t["name"] for t in on_here] == [
        "browser_login | Look for a browser opener on this machine",
        "browser_login | Forward the sign-in's callback from this machine to the VM",
        "browser_login | Open the sign-in page in your browser",
    ]
    for task in on_here:
        assert task.get("become") is False, task["name"]


def test_every_step_on_the_vm_runs_as_the_vm_user():
    on_vm = [t for t in _flow() if t.get("become") is True]
    assert on_vm
    for task in on_vm:
        assert task.get("become_user") == "{{ claude_code_user }}", task["name"]


def test_the_code_is_never_logged():
    prompts = [task for task in _flow() if "ansible.builtin.pause" in task]
    writers = _writers()
    # The prompt, the one asking again after a partly copied code, and the
    # hand-over after each.
    assert len(prompts) == 2 and len(writers) == 2, [t["name"] for t in prompts + writers]
    for task in prompts + writers:
        assert task.get("no_log") is True, task["name"]
    for task in writers:
        # On stdin, never on a command line another user could read.
        assert "claude_code_login_entered" not in json.dumps(task["ansible.builtin.command"]["argv"]), task["name"]


def test_no_blank_line_is_ever_handed_to_claude():
    for task in _writers():
        handed = re.fullmatch(r"\{\{\s*(\w+)\s*\}\}", task["ansible.builtin.command"]["stdin"]).group(1)
        conditions = task["when"] if isinstance(task["when"], list) else [task["when"]]
        assert f"{handed} | length > 0" in conditions, task["name"]


def test_the_code_is_handed_only_into_the_pipe(tmp_path):
    # Another run may have replaced the pipe, or taken it away: `<>` alone
    # would create a plain file and write the code into it.
    line = "fake-code-handed#fake-state-7\n"
    for task in _writers():
        pipe = tmp_path / "claude-login.in"
        argv = [
            str(pipe) if arg == "{{ claude_code_login_pipe }}" else arg
            for arg in task["ansible.builtin.command"]["argv"]
        ]
        assert str(pipe) in argv, task["name"]

        def hand(argv=argv):
            return subprocess.run(argv, input=line, text=True, capture_output=True, check=False).returncode

        assert hand() != 0 and not pipe.exists(), task["name"]
        pipe.write_text("")
        assert hand() != 0 and pipe.read_text() == "", task["name"]
        pipe.unlink()
        os.mkfifo(pipe, 0o600)
        reader = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
        try:
            assert hand() == 0, task["name"]
            assert os.read(reader, 4096) == line.encode(), task["name"]
        finally:
            os.close(reader)
            pipe.unlink()


def test_the_path_unit_watches_for_a_change_never_for_existence():
    lines = [
        line
        for line in (ROLE / "templates" / "claude-remote-control.path.j2").read_text().splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert any(line.startswith("PathChanged=") for line in lines)
    assert not any(line.startswith("PathExists") for line in lines)


def test_the_page_and_the_failure_are_said_on_one_line():
    # As in code.yml: a more-indented line in a folded scalar keeps its break.
    flow = {t["name"]: t for t in _flow()}
    assert "\n" not in flow["browser_login | Say the page and what happens next"]["ansible.builtin.debug"]["msg"]
    assert "\n" not in flow["browser_login | Fail when the sign-in did not complete"]["ansible.builtin.fail"]["msg"]


def test_a_sign_in_then_starts_remote_control_as_root():
    # Not from the sign-in's own play, which prompts VM by VM: the next VM's
    # page would wait on this one's stay-up check.
    assert "remote_control.yml" not in json.dumps(yaml.safe_load((ROLE / "tasks" / "login.yml").read_text()))
    starts = [
        t
        for t in yaml.safe_load((ROLE / "tasks" / "after_login.yml").read_text())
        if "ansible.builtin.include_tasks" in t
    ]
    assert len(starts) == 1, starts
    assert starts[0]["ansible.builtin.include_tasks"] == {"file": "remote_control.yml", "apply": {"become": True}}
    assert "claude_code_remote_control_wanted | bool" in json.dumps(starts[0]["when"])
    # For a VM this run signed in, and not one whose sign-in failed.
    runs = [
        task
        for play in yaml.safe_load(PLAYBOOK.read_text())
        for task in play.get("tasks", [])
        if "after_login" in json.dumps(task.get("block", []))
    ]
    assert len(runs) == 1, runs
    assert "claude_code_login_done | default(false) | bool" in runs[0]["when"]
    assert "claude_vm_claude_login_failure is not defined" in runs[0]["when"]


def test_nothing_decides_from_a_terminal_probe():
    # Ansible runs a lookup in a worker detached from the terminal, so a
    # /dev/tty probe says "no terminal" even on just deploy at one; whether the
    # run is attended is learnt from the prompt alone (docs/architecture/roles.md §5.5).
    for path in (
        ROLE / "tasks" / "browser_login.yml",
        REPO / "deploy.yml",
        PLAYBOOK,
        REPO / "playbooks" / "github_login.yml",
    ):
        assert "/dev/tty" not in path.read_text(), path


def test_the_harness_keeps_the_sign_in_off_this_machine(tmp_path):
    hosts = _hosts(tmp_path, {"probe": {}})
    assert hosts["probe"]["claude_code_login_desktop"] is False
    assert hosts["probe"]["claude_code_remote_control"] is False
