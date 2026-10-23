"""The tailscale role, run for real against a stand-in tailscale, and the guards that keep every test off Tailscale.

Nothing here reaches Tailscale, a VM or this machine's own tailscale. Every
inventory host is the controller, over the local connection, whose modules find
tests/molecule/fake_tailscale.sh first on their PATH as `tailscale`, with the
host's own directory as HOME - so each host's node is its own. dpkg-query and
getent are stand-ins too. The role's files run from ownership.yml on, as
main.yml runs them after the install; install.yml and absent.yml need apt and
systemd, which the role's own Molecule scenario covers.

What these guard:

- The vault key joins a node once, from a settled NeedsLogin with no node key,
  on standard input - never on argv, never in a file, never in a report - and
  is never handed to a node whose key expired: sent to a node that has a login,
  a key Tailscale no longer accepts logs it out (tailscale#19501). A node logged
  out by hand reads like a fresh one, and is joined again.
- A refused key is a report naming Tailscale's reason, not a failure; an OAuth
  secret without tags is refused before any `up`.
- Settings are held with one `tailscale set` naming only what differs, on a
  login this role made; a login made by hand, and a Tailscale installed by
  hand - dpkg's or not - keep theirs, and are only read.
- A sign-in the role started is its own however late it ends: a page approved
  after the run, a join still under way when its time ran out. A refused key
  leaves nothing pending, and an expired node's old key is never recorded.
- A page still waiting for its approval is what a report says, before an
  expired key or a refused one - make deploy's refusal from its configure play
  included.
- Tags are compared only when configured, and the fix named is the admin
  console - never a logout or a web login.
- An early NoState read does not cost a first join; a node still connecting is
  reported, not changed; a daemon this role started that does not answer fails
  by name.
- Each report is one line, names the tailnet (not under a control server of
  the user's own), and carries nothing the VM printed but plain text.
- configure.yml runs the role last, in a rescued block, so a failure leaves the
  VM its alias and fails the run at its end - or under make deploy, deploy's.
- Every test setup keeps off Tailscale's services and this machine's tailscale:
  this harness, the Molecule scenarios, the Vagrantfile; and the role's
  scenario is in the Makefile's ROLES and CI's matrix.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml
from harness import (
    GUARDED_TOOLS,
    STAND_INS,
    _env,
    _failures,
    _left_to_do,
    _messages,
    _results,
    _run,
    _run_at_a_terminal,
)

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "roles" / "tailscale"
FAKE = REPO / "tests" / "molecule" / "fake_tailscale.sh"
REPORT = "report | Say how this VM is on Tailscale"
JOIN = "connect | Join the tailnet with the key"

# Canned keys: the stand-in decides by the class in the middle. Every one is
# searched for in every report, argv and file.
KEYS = {
    "good": "tskey-auth-kGood0CNTRL-TestOnly00000000000000000",
    "tagged": "tskey-auth-kTagged0CNTRL-TestOnly000000000000000",
    "expired": "tskey-auth-kExpired0CNTRL-TestOnly00000000000000",
    "slow": "tskey-auth-kSlow0CNTRL-TestOnly0000000000000000",
    "late": "tskey-auth-kLate0CNTRL-TestOnly0000000000000000",
    "denied": "tskey-auth-kDenied0CNTRL-TestOnly00000000000000",
    "client": "tskey-client-kTestOnly0CNTRL-TestOnly0000000000000",
}
# The node key of a login the role made, as its joined marker holds it.
NODE = "1" * 64
# Control characters the VM's user could put in what tailscale reports: a title,
# a clipboard write (OSC 52) and a colour - JSON-escaped, as the stand-in writes
# them into its status JSON.
ESCAPES = r"\u001b]0;owned\u0007\u001b]52;c;b3duZWQ=\u0007\u001b[31m"
CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")

DPKG_QUERY = """#!/bin/sh
# Stand-in dpkg-query: what dpkg says about tailscale on this host.
cat "$STAND_IN/dpkg-status" 2>/dev/null || { echo "dpkg-query: no packages found matching tailscale" >&2; exit 1; }
"""
GETENT = """#!/bin/sh
[ "$1" = passwd ] || { echo "stand-in getent: unexpected database $1" >&2; exit 3; }
printf '%s:x:1000:1000:%s:%s:/bin/bash\\n' "$2" "$2" "$HOME"
"""


def _joined(name: str, **prefs: str) -> dict:
    """The stand-in's files for a node signed in by the role's own join: Running, its node key NODE."""
    return {
        "nodekey": NODE,
        "state": "Running",
        "prefs-touched": "",
        "prefs-loggedout": "false",
        "prefs-wantrunning": "true",
        "prefs-hostname": prefs.get("hostname", name),
        "prefs-ssh": prefs.get("ssh", "false"),
        "prefs-routes": prefs.get("routes", "false"),
        "prefs-dns": prefs.get("dns", "false"),
        **{key: value for key, value in prefs.items() if key not in {"hostname", "ssh", "routes", "dns"}},
    }


# One host per state the role can meet. "ts" seeds the stand-in, "ours" is the
# installed marker (default on), "marker" the joined marker's node key -
# "pending" a sign-in under way - "foreign" a Tailscale dpkg does not know, and
# tailscale_* keys are the host's own settings.
CASES = {
    "fresh-key": {"key": "good"},
    "fresh-no-key": {},
    "fresh-no-key-deploy": {"tailscale_sign_in_follows": True},
    "expired-key-set": {"key": "good", "ts": {"expired": NODE}, "marker": NODE},
    # A login made by hand on the role's Tailscale, whose key expired.
    "expired-by-hand": {"key": "good", "ts": {"expired": "f" * 64}, "marker": NODE},
    "logged-out-key": {"key": "good", "ts": {"prefs-loggedout": "true"}},
    "refused": {"key": "expired"},
    "slow": {"key": "slow"},
    "late": {"key": "late"},
    # An earlier key's refusal still in Health, and this key's join running out
    # of time: under way, not refused.
    "stale-error": {"key": "late", "ts": {"loginerror": "invalid key: API key does not exist"}},
    "denied-tags": {"key": "denied", "tailscale_advertise_tags": ["tag:claude-vm"]},
    "client-no-tags": {"key": "client"},
    "tagged-key": {"key": "tagged"},
    "missing-tag": {"ts": _joined("missing-tag"), "marker": NODE, "tailscale_advertise_tags": ["tag:claude-vm"]},
    "drift": {"ts": _joined("drift", hostname="drifted"), "marker": NODE},
    "ssh-on": {"ts": _joined("ssh-on"), "marker": NODE, "tailscale_ssh": True},
    "dns-on": {"ts": _joined("dns-on"), "marker": NODE, "tailscale_accept_dns": True},
    "by-hand-login": {"ts": _joined("by-hand-login", hostname="mine", ssh="true"), "marker": "f" * 64},
    "not-ours": {"ours": False, "ts": _joined("not-ours", hostname="mine")},
    "outside-dpkg": {"key": "good", "package": False, "foreign": True},
    "web-with-key": {"key": "good", "tailscale_auth_method": "web"},
    "key-method": {"key": "good", "tailscale_auth_method": "key"},
    "key-method-expired": {"key": "good", "ts": {"expired": NODE}, "marker": NODE, "tailscale_auth_method": "key"},
    "page-waiting-expired": {
        "ts": {"expired": "", "authurl": "https://login.tailscale.com/a/0123456789abcd"},
        "marker": "pending",
    },
    "nostate": {"key": "good", "ts": {"nostate-reads": "2"}},
    "starting": {"ts": _joined("starting", hostname="drifted", starting=""), "marker": NODE},
    "daemon-down": {"ts": {"daemon-down": ""}},
    "approval": {"key": "good", "ts": {"needs-approval": ""}},
    "name-taken": {"key": "good", "ts": {"name-taken": ""}},
    "stopped": {"ts": _joined("stopped", state="Stopped"), "marker": NODE},
    "headscale": {"key": "good", "tailscale_login_server": "https://headscale.example.test"},
    "escapes": {"ts": _joined("escapes", tailnet=f"evil{ESCAPES}net", health=f"look{ESCAPES}here"), "marker": NODE},
}
# What a run is told, beyond each host's own: short waits, become off - the
# controller has no sudo to give - and a Tailscale outside dpkg looked for in
# each host's own directory, never this machine's.
EXTRA = {
    "ansible_become": False,
    "tailscale_settle_seconds": 3,
    "tailscale_join_timeout": 2,
    "tailscale_foreign_paths": ["~/foreign/tailscaled"],
}


def _hosts(root: Path, cases: dict) -> dict:
    """Inventory hosts, one per case, whose modules run the stand-in tailscale with their own HOME; returns hostvars."""
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name, text in (
        ("python", STAND_INS["python"]),
        ("tailscale", FAKE.read_text()),
        ("dpkg-query", DPKG_QUERY),
        ("getent", GETENT),
    ):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    hosts = {}
    for name, case in cases.items():
        home = root / name
        (home / "markers").mkdir(parents=True)
        if case.get("package", True):
            (home / "dpkg-status").write_text("installed")
        if case.get("ours", True) and case.get("package", True):
            (home / "markers" / "tailscale-installed").write_text("installed by the role\n")
        if case.get("marker") == "pending":
            (home / "markers" / "tailscale-joined").write_text("pending\n")
        elif case.get("marker"):
            (home / "markers" / "tailscale-joined").write_text(f"nodekey:{case['marker']}\n")
        if case.get("foreign"):
            (home / "foreign").mkdir()
            (home / "foreign" / "tailscaled").write_text("a tailscaled dpkg does not know\n")
        state = home / ".tailscale-fake"
        state.mkdir()
        for file_name, content in case.get("ts", {}).items():
            (state / file_name).write_text(content)
        (home / "python").symlink_to(bin_dir / "python")
        hosts[name] = {
            "ansible_connection": "local",
            "ansible_python_interpreter": str(home / "python"),
            "ansible_host": "192.0.2.10",
            "tailscale_marker_dir": str(home / "markers"),
            "tailscale_auth_key": KEYS[case["key"]] if case.get("key") else "",
            # Never the developer's browser: the guard below holds every scenario to it.
            "tailscale_login_desktop": False,
            # Every module's arguments in its result, so that a key handed to a
            # task without no_log reaches the reports the tests search.
            "ansible_inject_invocation": True,
            **{key: value for key, value in case.items() if key.startswith("tailscale_")},
        }
    return hosts


def _role_play() -> dict:
    """ownership.yml on, as main.yml runs them once the package is in place."""
    return {
        "name": "Tailscale, from the state on",
        "hosts": "claude_vms",
        "gather_facts": False,
        "tasks": [
            {
                "name": f"Run the role's {tasks_from} tasks",
                "ansible.builtin.include_role": {"name": "tailscale", "tasks_from": tasks_from},
            }
            for tasks_from in ("ownership", "status", "connect", "settings", "report")
        ],
    }


def _role_run(root: Path, hosts: dict, *args: str) -> dict:
    playbook = root / "role.yml"
    playbook.write_text(json.dumps([_role_play()]))
    # One fork per host: every host is the same machine, and the stand-in waits.
    return _run(root, playbook, hosts, "-e", json.dumps(EXTRA), "--forks", str(len(hosts)), *args)


def _calls(root: Path, host: str) -> list[str]:
    log = root / host / ".tailscale-fake" / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def _ups(calls: list[str]) -> list[str]:
    return [call for call in calls if call.startswith("up ")]


@pytest.fixture(scope="module")
def configured(tmp_path_factory):
    """Every case, run twice: what the first run did, and that the second does nothing."""
    root = tmp_path_factory.mktemp("tailscale")
    hosts = _hosts(root, CASES)
    first = _role_run(root, hosts)
    first_calls = {host: _calls(root, host) for host in hosts}
    second = _role_run(root, hosts)
    return root, first, first_calls, second


def _said(report: dict) -> dict:
    return _messages(report, REPORT)


# ------------------------------------------------------------ the key path ---
def test_a_fresh_node_joins_once_with_the_key_on_stdin(configured):
    root, first, first_calls, second = configured
    ups = _ups(first_calls["fresh-key"])
    assert ups == [
        "up --auth-key=file:/dev/stdin --timeout=2s --reset --hostname=fresh-key --ssh=false"
        " --accept-routes=false --accept-dns=false"
    ], first_calls["fresh-key"]
    assert "[key on stdin]" in first_calls["fresh-key"]
    assert _said(first)["fresh-key"] == (
        "fresh-key: joined Tailscale in the tailnet you@example.com as fresh-key.tail1234.ts.net (100.101.102.103)."
    )
    assert _said(second)["fresh-key"].startswith("fresh-key: on Tailscale in the tailnet you@example.com as ")
    # The marker holds the node key the join produced, and the second run reads it as the role's own.
    marker = (root / "fresh-key" / "markers" / "tailscale-joined").read_text().strip()
    assert marker == "nodekey:" + (root / "fresh-key" / ".tailscale-fake" / "nodekey").read_text().strip()


# A refused key leaves the node as it was - no login - so the next run tries the
# key again: a new one put in the vault since joins it, and nothing is lost.
RETRIED = {"refused", "slow", "late", "denied-tags", "stale-error"}


def test_the_second_run_reads_and_changes_nothing(configured):
    root, first, first_calls, second = configured
    for host in CASES:
        if host == "daemon-down":
            continue
        later = _calls(root, host)[len(first_calls[host]) :]
        tried = [call for call in later if call.startswith("up ") or call == "[key on stdin]"]
        assert set(later) - set(tried) <= {"status --json", "debug prefs"}, (host, later)
        assert len(_ups(tried)) == (1 if host in RETRIED else 0), (host, later)
        changed = [name for name, h, result in _results(second) if h == host and result.get("changed")]
        # Tried again, the slow key runs out of time again with its first
        # refusal's words still in Health, which cannot be told from an earlier
        # key's: the join is taken as still under way.
        if host == "slow":
            assert [name.split(" : ")[-1] for name in changed] == [
                "connect | Mark a join still under way as this role's"
            ]
        else:
            assert changed == [], (host, changed)
    assert _said(second)["refused"] == _said(first)["refused"]


def test_a_node_whose_key_expired_is_never_handed_the_key(configured):
    # The #19501 trap: sent again to a node with a login, a key Tailscale no
    # longer accepts logs the node out.
    root, first, first_calls, second = configured
    assert _ups(_calls(root, "expired-key-set")) == []
    assert _said(first)["expired-key-set"] == (
        "expired-key-set: signed out of Tailscale (its key expired). make login VM_NAME=expired-key-set TYPE=tailscale"
        " signs it in again."
    )


def test_an_expired_login_made_by_hand_is_left_to_whoever_made_it(configured):
    # The web login's `up --reset` would put the role's settings over its own.
    root, first, first_calls, second = configured
    assert _ups(_calls(root, "expired-by-hand")) == []
    assert _said(first)["expired-by-hand"] == (
        "expired-by-hand: signed out of Tailscale (its key expired). It was signed in outside this project, so it is"
        " left as it is: sign it in again on the VM, the way it was signed in."
    )


def test_a_node_logged_out_by_hand_is_joined_again(configured):
    # Phase 0: after `tailscale logout` a node has no node key and reads like a
    # fresh one, so the vault key joins it again - it has no login to lose.
    root, first, first_calls, second = configured
    assert len(_ups(first_calls["logged-out-key"])) == 1
    assert _said(first)["logged-out-key"].startswith("logged-out-key: joined Tailscale ")


def test_a_refused_key_is_a_report_naming_tailscale_s_reason(configured):
    root, first, first_calls, second = configured
    assert "refused" not in _failures(first) and "slow" not in _failures(first)
    assert _said(first)["refused"] == (
        "refused: Tailscale refused vault_tailscale_auth_key (invalid key: API key does not exist) - it has expired,"
        " been used up or been revoked. Put a new one in the vault (reusable, pre-approved, tagged) and run make"
        " deploy VM_NAME=refused TAGS=tailscale, or sign in by the browser: make login VM_NAME=refused"
        " TYPE=tailscale"
    )
    # A refusal that came back as the timeout is named from the health message.
    assert "(invalid key: API key does not exist)" in _said(first)["slow"]


def test_a_join_still_under_way_is_this_role_s_and_a_refused_one_leaves_nothing(configured):
    # tailscaled goes on with a join whose --timeout ran out, and the login it
    # ends in is the role's; a refused key ends the sign-in.
    root, first, first_calls, second = configured
    assert (root / "late" / "markers" / "tailscale-joined").read_text() == "pending\n"
    assert _said(first)["late"] == (
        "late: Tailscale did not join with vault_tailscale_auth_key (timeout waiting for Tailscale service to enter a"
        ' Running state; check health with "tailscale status"). make deploy VM_NAME=late TAGS=tailscale tries'
        " again, or sign in by the browser: make login VM_NAME=late TYPE=tailscale"
    )
    for host in ("refused", "denied-tags"):
        assert not (root / host / "markers" / "tailscale-joined").exists(), host
    # Refused on the first run, by Health's word (above); the second run's join
    # ran out of time with only those words in Health again.
    assert (root / "slow" / "markers" / "tailscale-joined").read_text() == "pending\n"


def test_an_earlier_key_s_refusal_still_in_health_is_not_this_join_s(configured):
    # tailscaled keeps a login error until an attempt after it ends: read as
    # this join's refusal, a late join of a good key would be forgotten, and
    # the login it ends in taken for one made by hand.
    root, first, first_calls, second = configured
    assert (root / "stale-error" / "markers" / "tailscale-joined").read_text() == "pending\n"
    assert _said(first)["stale-error"].startswith(
        "stale-error: Tailscale did not join with vault_tailscale_auth_key (timeout waiting for Tailscale service"
    )


def test_a_join_refused_in_other_words_leaves_nothing_pending(configured):
    # Pending, a login made by hand after it would be taken for the role's, and
    # its settings changed: only a join that ran out of time is still under way.
    root, first, first_calls, second = configured
    assert "denied-tags" not in _failures(first)
    assert _said(first)["denied-tags"] == (
        "denied-tags: Tailscale did not join with vault_tailscale_auth_key (backend error: requested tags"
        " [tag:claude-vm] are invalid or not permitted). make deploy VM_NAME=denied-tags TAGS=tailscale tries"
        " again, or sign in by the browser: make login VM_NAME=denied-tags TYPE=tailscale"
    )


def test_an_oauth_secret_without_tags_is_refused_before_any_up(configured):
    root, first, first_calls, second = configured
    assert _ups(_calls(root, "client-no-tags")) == []
    assert _said(first)["client-no-tags"] == (
        "client-no-tags: Tailscale refused vault_tailscale_auth_key (oauth authkeys require --advertise-tags): an"
        " OAuth client secret needs the tags it may apply in tailscale_advertise_tags. Set them and run make deploy"
        " VM_NAME=client-no-tags TAGS=tailscale"
    )


def test_an_early_nostate_read_still_joins_in_the_same_run(configured):
    # tailscaled is ready for systemd before its backend has a state: without the
    # wait, the first join would be put off to the next configure.
    root, first, first_calls, second = configured
    assert len(_ups(first_calls["nostate"])) == 1
    assert _said(first)["nostate"].startswith("nostate: joined Tailscale ")


def test_waiting_for_an_admin_is_said(configured):
    root, first, first_calls, second = configured
    assert _said(first)["approval"] == (
        "approval: signed in to Tailscale and waiting for an admin to approve it at"
        " https://login.tailscale.com/admin/machines; it connects by itself once approved."
    )


def test_a_name_already_taken_is_said_with_the_admin_page(configured):
    root, first, first_calls, second = configured
    said = _said(first)["name-taken"]
    assert " as name-taken-1.tail1234.ts.net (100.101.102.103)." in said, said
    assert said.endswith(
        "A device named name-taken is already in your tailnet, likely the VM this one replaced: delete it at"
        " https://login.tailscale.com/admin/machines, then rename this one to name-taken there."
    ), said


def test_the_tailnet_is_named_but_not_under_a_control_server_of_the_user_s_own(configured):
    root, first, first_calls, second = configured
    assert " in the tailnet you@example.com " in _said(first)["fresh-key"]
    assert " in the tailnet " not in _said(first)["headscale"]
    assert _ups(first_calls["headscale"])[0].endswith(" --login-server=https://headscale.example.test")


# ------------------------------------------------------------ the settings ---
def test_drift_is_one_set_naming_only_what_differs(configured):
    root, first, first_calls, second = configured
    assert [call for call in first_calls["drift"] if call.startswith("set ")] == ["set --hostname=drift"]
    assert _said(first)["drift"].endswith(" Changed: tailscale set --hostname=drift.")
    assert [call for call in first_calls["ssh-on"] if call.startswith("set ")] == [
        "set --ssh=true --accept-risk=lose-ssh"
    ]
    assert [call for call in first_calls["dns-on"] if call.startswith("set ")] == ["set --accept-dns=true"]


def test_a_login_made_by_hand_keeps_its_settings(configured):
    root, first, first_calls, second = configured
    assert [call for call in _calls(root, "by-hand-login") if call.startswith(("set ", "up "))] == []
    assert _said(first)["by-hand-login"].endswith(
        "; signed in outside this project, so its settings are left as they are."
    )


def test_a_tailscale_installed_by_hand_is_only_read(configured):
    root, first, first_calls, second = configured
    assert set(_calls(root, "not-ours")) <= {"status --json", "debug prefs"}
    assert _said(first)["not-ours"] == (
        "not-ours: on Tailscale in the tailnet you@example.com as mine.tail1234.ts.net (100.101.102.103); installed"
        " outside this project, so it is left as it is."
    )
    # The snap, or Tailscale's static binaries: the package installed beside it
    # would start a second tailscaled. The key is not used either.
    assert set(_calls(root, "outside-dpkg")) <= {"status --json"}
    assert _said(first)["outside-dpkg"] == (
        "outside-dpkg: Tailscale is installed (NeedsLogin) outside this project, so it is left as it is."
    )


def test_a_page_still_waiting_is_said_before_an_expired_key(configured):
    # A fresh page would end the one waiting, so the report names that one.
    root, first, first_calls, second = configured
    assert _ups(_calls(root, "page-waiting-expired")) == []
    assert _said(first)["page-waiting-expired"] == (
        "page-waiting-expired: not signed in to Tailscale yet - the page stays valid for 7 days and"
        " page-waiting-expired joins by itself once you approve it; make login VM_NAME=page-waiting-expired"
        " TYPE=tailscale gives a fresh page."
    )
    # The old key is not the login the page will give.
    assert (root / "page-waiting-expired" / "markers" / "tailscale-joined").read_text() == "pending\n"


def test_tags_are_compared_only_when_configured_and_fixed_in_the_console(configured):
    root, first, first_calls, second = configured
    # A tagged key's node, no tags configured: nothing to say about tags.
    assert "tailscale_advertise_tags" not in _said(first)["tagged-key"]
    said = _said(first)["missing-tag"]
    assert said.endswith(
        "It lacks tag:claude-vm, which tailscale_advertise_tags asks for: set its tags in the admin console"
        " (Machines, missing-tag, Edit ACL tags)."
    ), said
    # Never a logout or a web login for tags: that would make a tagged VM the user's own device.
    for message in _said(first).values():
        if "tailscale_advertise_tags asks for" in message:
            assert "logout" not in message and "tailscale-login" not in message, message


def test_a_node_still_starting_is_reported_not_changed(configured):
    root, first, first_calls, second = configured
    assert [call for call in _calls(root, "starting") if call.startswith(("set ", "up "))] == []
    assert _said(first)["starting"] == (
        "starting: Tailscale is still connecting (Starting) after 3 seconds, so nothing was changed. make deploy"
        " VM_NAME=starting TAGS=tailscale reads it again."
    )


def test_a_daemon_this_role_started_that_does_not_answer_fails_by_name(configured):
    root, first, first_calls, second = configured
    assert _failures(first)["daemon-down"] == (
        "tailscale : status | Fail when the tailscaled this role started does not answer: tailscaled is not running"
        " on daemon-down (failed to connect to local tailscaled; it doesn't appear to be running); journalctl -u"
        " tailscaled on daemon-down says why."
    )


def test_a_stopped_node_is_said(configured):
    root, first, first_calls, second = configured
    assert _said(first)["stopped"] == (
        "stopped: Tailscale is down (tailscale down on the VM). sudo tailscale up there brings it back."
    )


def test_a_node_with_no_key_is_left_for_the_web_login(configured):
    root, first, first_calls, second = configured
    assert _ups(_calls(root, "fresh-no-key")) == []
    assert _said(first)["fresh-no-key"] == (
        "fresh-no-key: Tailscale is installed but not signed in. make login VM_NAME=fresh-no-key TYPE=tailscale"
        " signs it in from your browser; vault_tailscale_auth_key joins it on the next make deploy TYPE=configure."
    )
    assert _said(first)["fresh-no-key-deploy"] == (
        "fresh-no-key-deploy: Tailscale is installed; make deploy signs it in next, in this run."
    )


def test_tailscale_auth_method_decides_whether_the_key_is_used(configured):
    root, first, first_calls, second = configured
    # web: a key in the vault is left unused, and only the browser is named.
    assert _ups(_calls(root, "web-with-key")) == []
    assert _said(first)["web-with-key"] == (
        "web-with-key: Tailscale is installed but not signed in. make login VM_NAME=web-with-key TYPE=tailscale"
        " signs it in from your browser."
    )
    # key: the key joins a fresh node, as under auto.
    assert len(_ups(first_calls["key-method"])) == 1
    assert _said(first)["key-method"].startswith("key-method: joined Tailscale ")
    # key never offers the browser, and never resends the key to a node that had a login.
    assert _ups(_calls(root, "key-method-expired")) == []
    assert _said(first)["key-method-expired"] == (
        "key-method-expired: signed out of Tailscale (its key expired), and tailscale_auth_method key never signs a"
        " VM in by the browser nor sends the key to a node that had a login: run sudo tailscale logout on it, then"
        " make deploy VM_NAME=key-method-expired TAGS=tailscale joins it with the key."
    )


# ------------------------------------------------------- what reaches text ---
def test_next_steps_keep_what_is_left_to_do_and_nothing_where_it_runs_as_it_should(configured):
    # What the run's last lines list (callback_plugins/claude_on_proxmox.py):
    # the report itself while something is left, whether or not it names a make
    # command - a stopped node, missing tags, an admin's approval.
    root, first, first_calls, second = configured
    said = _said(first)
    left = {host: matters.get("tailscale") for host, matters in _left_to_do(first).items()}
    for host in ("stopped", "missing-tag", "approval", "name-taken", "refused", "starting", "expired-key-set"):
        assert left[host] == said[host], host
    for host in ("fresh-key", "logged-out-key", "not-ours", "tagged-key"):
        assert left[host] == "", host


def test_every_report_is_one_line_of_plain_text(configured):
    root, first, first_calls, second = configured
    for report in (first, second):
        for host, message in _said(report).items():
            assert "\n" not in message and not CONTROL.search(message), (host, message)
    assert _said(first)["escapes"].startswith("escapes: on Tailscale in the tailnet evil")


def test_no_key_reaches_a_report_an_argument_or_a_file(configured):
    root, first, first_calls, second = configured
    for report in (first, second):
        text = json.dumps(report)
        for key in KEYS.values():
            assert key not in text
    for host in CASES:
        assert not any("tskey" in call for call in _calls(root, host)), host
    # Only the inventory itself, which hands each host its key, holds one.
    holding = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.name != "hosts.yml"
        and any(key in path.read_text(errors="replace") for key in KEYS.values())
    )
    assert holding == [], holding


# ------------------------------------------------- the web login (playbook) ---
LOGIN = REPO / "playbooks" / "tailscale_login.yml"
SAY_PAGE = "web_login | Say where to sign in"

# One host per way make login TYPE=tailscale meets a VM. The stand-in approves a
# page `approve-after` seconds after minting it - here, after `up --timeout=5s`
# has exited, as Phase 0 saw tailscaled finish a login on its own.
WEB_CASES = {
    "approved-late": {"ts": {"approve-after": "7"}},
    "needs-admin": {"ts": {"approve-after": "1", "needs-approval": ""}},
    "waited-out": {"tailscale_login_timeout": 3},
    # Not Tailscale's page: said, with a warning, and never opened - here with
    # the desktop switch on, so that an opener reached would fail the run.
    "foreign-page": {
        "ts": {"authurl-base": "https://evil.example", "approve-after": "7"},
        "tailscale_login_desktop": True,
    },
    # Under a control server of the user's own: an older Headscale's page, with
    # a `:` in it, under an address given with its trailing `/`; and a page on
    # another host that merely starts with the server's name.
    "headscale-old": {
        "tailscale_login_server": "https://hs.example.test/",
        "ts": {"authurl-base": "https://hs.example.test/register/nodekey:0a1b", "approve-after": "7"},
    },
    "headscale-elsewhere": {
        "tailscale_login_server": "https://hs.example.test",
        "ts": {"authurl-base": "https://hs.example.test.evil.example", "approve-after": "7"},
    },
    "key-expired": {"ts": {"expired": NODE, "approve-after": "1"}, "marker": NODE},
    "key-expired-waited-out": {"ts": {"expired": NODE}, "marker": NODE, "tailscale_login_timeout": 2},
    # The role's login, replaced since by one made by hand, whose key expired.
    "expired-by-hand": {"ts": {"expired": "f" * 64, "approve-after": "1"}, "marker": NODE},
    "not-installed": {"package": False},
    "switched-off": {"tailscale_enabled": False},
    "installed-by-hand": {"ours": False},
    "installed-outside-dpkg": {"package": False, "foreign": True},
    # Under tailscale_auth_method key, configure joins with the key; the web login never starts.
    "key-only": {"key": "good", "tailscale_auth_method": "key"},
    "signed-in": {"ts": _joined("signed-in"), "marker": NODE},
}
WEB_EXTRA = {**EXTRA, "claude_vm_discovery": False, "tailscale_login_poll_seconds": 1}


@pytest.fixture(scope="module")
def web_logins(tmp_path_factory):
    """make login VM_NAME=<every host> TYPE=tailscale: one VM at a time, each signed in where it can be."""
    root = tmp_path_factory.mktemp("tailscale-web")
    hosts = _hosts(root, WEB_CASES)
    for name in hosts:
        hosts[name].setdefault("tailscale_login_timeout", 30)
    report = _run(root, LOGIN, hosts, "-e", json.dumps({**WEB_EXTRA, "vm_name": ",".join(hosts)}), ordered=True)
    return root, report


def test_a_web_login_runs_one_up_reads_the_page_once_and_joins_when_approved_after_it_exits(web_logins):
    root, report = web_logins
    calls = _calls(root, "approved-late")
    assert _ups(calls) == [
        "up --timeout=5s --reset --hostname=approved-late --ssh=false --accept-routes=false --accept-dns=false"
    ], calls
    said = _messages(report, SAY_PAGE)["approved-late"]
    assert re.fullmatch(
        r"approved-late: sign in to Tailscale at https://login\.tailscale\.com/a/[0-9a-f]{14} This run waits up to"
        r" 30 seconds; Ctrl-C ends the whole run, and the page stays valid for 7 days either way\. Joined this way,"
        r" approved-late is your own device, which your tailnet's policy lets reach the devices your other ones can:"
        r" \"Keeping an agent VM off your other devices\" in docs/guides/tailscale\.md says how to tag it\.",
        said,
    ), said
    assert _said(report)["approved-late"].startswith("approved-late: joined Tailscale in the tailnet you@example.com ")
    marker = (root / "approved-late" / "markers" / "tailscale-joined").read_text().strip()
    assert marker == "nodekey:" + (root / "approved-late" / ".tailscale-fake" / "nodekey").read_text().strip()


def test_the_page_is_said_before_the_wait(web_logins):
    root, report = web_logins
    order = [name for name, host, result in _results(report) if host == "approved-late" and not result.get("skipped")]
    assert order.index(f"tailscale : {SAY_PAGE}") < order.index(
        "tailscale : web_login | Wait for the page to be approved"
    )


def test_waiting_for_an_admin_ends_the_wait_and_is_said(web_logins):
    root, report = web_logins
    assert _said(report)["needs-admin"] == (
        "needs-admin: signed in to Tailscale and waiting for an admin to approve it at"
        " https://login.tailscale.com/admin/machines; it connects by itself once approved."
    )


def test_a_wait_that_runs_out_is_a_report_and_the_page_stays_pending(web_logins):
    root, report = web_logins
    assert "waited-out" not in _failures(report)
    assert _said(report)["waited-out"] == (
        "waited-out: not signed in to Tailscale yet - the page stays valid for 7 days and waited-out joins by itself"
        " once you approve it; make login VM_NAME=waited-out TYPE=tailscale gives a fresh page."
    )
    assert len(_ups(_calls(root, "waited-out"))) == 1
    # Marked before the page was shown: approved later, it is the role's login.
    assert (root / "waited-out" / "markers" / "tailscale-joined").read_text() == "pending\n"


def test_an_expired_node_s_wait_that_runs_out_says_the_page_and_keeps_no_old_key(web_logins):
    # The expired node keeps its old key while the page waits: neither the
    # report nor the marker may take that for the login to come.
    root, report = web_logins
    assert _said(report)["key-expired-waited-out"].startswith(
        "key-expired-waited-out: not signed in to Tailscale yet - the page stays valid for 7 days"
    )
    assert (root / "key-expired-waited-out" / "markers" / "tailscale-joined").read_text() == "pending\n"


def test_a_page_approved_after_the_run_is_the_role_s_login_and_keeps_its_settings(tmp_path):
    hosts = _hosts(tmp_path, {"late": {"tailscale_login_timeout": 2}})
    login = _run(tmp_path, LOGIN, hosts, "-e", json.dumps({**WEB_EXTRA, "vm_name": "late"}))
    assert login["rc"] == 0, _failures(login)
    state = tmp_path / "late" / ".tailscale-fake"
    # Approved after the run had ended, and renamed since.
    (state / "approve-after").write_text("0")
    (state / "prefs-hostname").write_text("drifted")
    first = _role_run(tmp_path, hosts)
    assert first["rc"] == 0, _failures(first)
    marker = tmp_path / "late" / "markers" / "tailscale-joined"
    assert marker.read_text() == f"nodekey:{(state / 'nodekey').read_text()}\n"
    assert [call for call in _calls(tmp_path, "late") if call.startswith("set ")] == ["set --hostname=late"]
    assert _said(first)["late"] == (
        "late: on Tailscale in the tailnet you@example.com as late.tail1234.ts.net (100.101.102.103). Changed:"
        " tailscale set --hostname=late."
    )
    second = _role_run(tmp_path, hosts)
    assert [name for name, host, result in _results(second) if result.get("changed")] == []


def test_make_deploy_says_a_page_left_waiting_after_its_configure_play_refused_the_key(tmp_path):
    # The configure play's refused join is still registered in the sign-in play.
    hosts = _hosts(tmp_path, {"deployed": {"key": "expired", "tailscale_login_timeout": 2}})
    playbook = tmp_path / "configure_then_sign_in.yml"
    playbook.write_text(json.dumps([_role_play(), {"ansible.builtin.import_playbook": str(LOGIN)}]))
    report = _run(tmp_path, playbook, hosts, "-e", json.dumps({**WEB_EXTRA, "vm_name": "deployed"}))
    assert report["rc"] == 0, _failures(report)
    said = [result["msg"] for name, host, result in _results(report) if name.endswith(REPORT) and "msg" in result]
    assert len(said) == 2, said
    assert said[0].startswith("deployed: Tailscale refused vault_tailscale_auth_key (invalid key: "), said
    assert said[1].startswith("deployed: not signed in to Tailscale yet - the page stays valid for 7 days"), said


def test_make_deploy_s_sign_in_step_never_says_again_what_its_configure_play_did(tmp_path):
    # Its configure play's join and set are still registered in the sign-in
    # play, which did neither.
    hosts = _hosts(
        tmp_path,
        {"by-key": {"key": "good"}, "drifted": {"ts": _joined("drifted", hostname="old"), "marker": NODE}},
    )
    playbook = tmp_path / "configure_then_sign_in.yml"
    playbook.write_text(json.dumps([_role_play(), {"ansible.builtin.import_playbook": str(LOGIN)}]))
    report = _run(tmp_path, playbook, hosts, "-e", json.dumps({**WEB_EXTRA, "vm_name": "by-key,drifted"}))
    assert report["rc"] == 0, _failures(report)
    said = {host: [] for host in hosts}
    for name, host, result in _results(report):
        if name.endswith(REPORT) and "msg" in result:
            said[host].append(result["msg"])
    configured, signed_in = said["by-key"]
    assert configured.startswith("by-key: joined Tailscale "), configured
    assert signed_in.startswith("by-key: on Tailscale "), signed_in
    configured, signed_in = said["drifted"]
    assert configured.endswith(" Changed: tailscale set --hostname=drifted."), configured
    assert "Changed:" not in signed_in, signed_in


def test_a_page_that_is_not_tailscale_s_is_never_opened(web_logins):
    # The harness's stand-in for open fails the run if it is called.
    root, report = web_logins
    said = _messages(report, SAY_PAGE)["foreign-page"]
    assert said.startswith("foreign-page: sign in to Tailscale at https://evil.example/a/"), said
    assert (
        "- not Tailscale's sign-in page, so it is not opened here: check where it leads before you follow it." in said
    )


def test_a_page_under_the_control_server_is_trusted_however_its_address_ends(web_logins):
    root, report = web_logins
    said = _messages(report, SAY_PAGE)
    assert said["headscale-old"].startswith(
        "headscale-old: sign in to Tailscale at https://hs.example.test/register/nodekey:0a1b/a/"
    ), said["headscale-old"]
    assert "not your control server's sign-in page" not in said["headscale-old"]
    assert (
        "- not your control server's sign-in page, so it is not opened here: check where it leads before you follow"
        in said["headscale-elsewhere"]
    )


def test_a_node_whose_key_expired_signs_in_again_by_the_web(web_logins):
    root, report = web_logins
    assert len(_ups(_calls(root, "key-expired"))) == 1
    assert "--auth-key" not in " ".join(_calls(root, "key-expired"))
    assert _said(report)["key-expired"].startswith("key-expired: joined Tailscale ")


def test_a_vm_it_cannot_sign_in_gets_one_line_and_nothing_else(web_logins):
    root, report = web_logins
    messages = _messages(report, "login | Say why there is nothing to sign in")
    assert messages["not-installed"] == (
        "not-installed: Tailscale is not installed; make deploy VM_NAME=not-installed TAGS=tailscale installs it,"
        " and make login VM_NAME=not-installed TYPE=tailscale then signs it in."
    )
    assert (
        messages["switched-off"]
        == "switched-off: Tailscale is off (tailscale_enabled: false), so there is nothing to sign in."
    )
    left = _left_to_do(report)
    assert left["not-installed"]["tailscale"] == messages["not-installed"]
    assert left["switched-off"]["tailscale"] == ""
    assert _said(report)["installed-by-hand"].endswith("outside this project, so it is left as it is.")
    assert _said(report)["installed-outside-dpkg"] == (
        "installed-outside-dpkg: Tailscale is installed (NeedsLogin) outside this project, so it is left as it is."
    )
    assert _said(report)["key-only"] == (
        "key-only: Tailscale is installed but not signed in. make deploy VM_NAME=key-only TAGS=tailscale joins it"
        " with vault_tailscale_auth_key (tailscale_auth_method: key)."
    )
    assert _said(report)["expired-by-hand"].startswith(
        "expired-by-hand: signed out of Tailscale (its key expired). It was signed in outside this project"
    )
    left = (
        "not-installed",
        "switched-off",
        "installed-by-hand",
        "installed-outside-dpkg",
        "key-only",
        "signed-in",
        "expired-by-hand",
    )
    for host in left:
        assert _ups(_calls(root, host)) == [], host
    assert report["rc"] == 0, _failures(report)


def test_a_vm_signed_in_already_is_only_reported(web_logins):
    root, report = web_logins
    assert _said(report)["signed-in"].startswith("signed-in: on Tailscale in the tailnet you@example.com as ")


def test_without_names_every_vm_is_reported_at_once_and_none_signed_in(tmp_path):
    hosts = _hosts(tmp_path, {"one": {}, "two": {"ts": _joined("two"), "marker": NODE}})
    report = _run(tmp_path, LOGIN, hosts, "-e", json.dumps(WEB_EXTRA))
    assert report["rc"] == 0, _failures(report)
    assert all(_ups(_calls(tmp_path, host)) == [] for host in hosts)
    assert _said(report)["one"].startswith(
        "one: Tailscale is installed but not signed in. make login VM_NAME=one TYPE=tailscale"
    )
    batches = [
        sorted({host for task in entry["tasks"] for host in task["hosts"]})
        for entry in report["plays"]
        if entry["play"]["name"] == "Sign each VM in to Tailscale, or say how it is on it"
    ]
    assert batches == [["one", "two"]], batches


# ---------------------------------------------------- make deploy's step ---
# What deploy.yml's Tailscale import decides, VM by VM, from what each VM's
# Claude step left. With tailscale_login_auto_start, the default, the web login
# starts by itself for every VM but one whose Claude sign-in was skipped. Off,
# every VM but that one is asked first: Enter - here, no terminal, which pause
# reads as nothing - signs in one whose Claude sign-in was answered, and one
# that showed no Claude prompt needs "y".
AFTER_CLAUDE = {
    "typed": {"claude_code_login_answered": True, "claude_code_login_skipped": False},
    "quiet": {"claude_code_login_answered": False, "claude_code_login_skipped": False},
    "skipped": {"claude_code_login_answered": False, "claude_code_login_skipped": True},
}


def _deploy_tailscale(root: Path) -> Path:
    """deploy.yml's Tailscale import, as it stands, with its vars and condition."""
    entries = [
        entry
        for entry in yaml.safe_load((REPO / "deploy.yml").read_text())
        if entry.get("ansible.builtin.import_playbook") == "playbooks/tailscale_login.yml"
    ]
    assert len(entries) == 1, entries
    playbook = root / "deploy_tailscale.yml"
    playbook.write_text(yaml.safe_dump([{**entries[0], "ansible.builtin.import_playbook": str(LOGIN)}]))
    return playbook


def _deploy_step(root: Path, **extra) -> dict:
    cases = {name: {"ts": {"approve-after": "7"}, "tailscale_login_timeout": 30} for name in AFTER_CLAUDE}
    hosts = _hosts(root, cases)
    for name, facts in AFTER_CLAUDE.items():
        hosts[name].update(facts)
    settings = {**WEB_EXTRA, "vm_name": ",".join(hosts), "deploy_sign_in": True, **extra}
    return _run(root, _deploy_tailscale(root), hosts, "-e", json.dumps(settings), ordered=True)


def _asked(report: dict) -> set:
    return {
        host
        for name, host, result in _results(report)
        if name.endswith("Ask whether to sign this VM in to Tailscale now") and not result.get("skipped")
    }


@pytest.fixture(scope="module")
def deployed(tmp_path_factory):
    root = tmp_path_factory.mktemp("tailscale-deploy")
    return root, _deploy_step(root)


def test_make_deploy_starts_the_web_login_by_itself_for_every_vm_but_one_whose_claude_sign_in_was_skipped(deployed):
    root, report = deployed
    assert _asked(report) == set()
    assert len(_ups(_calls(root, "typed"))) == 1 and len(_ups(_calls(root, "quiet"))) == 1
    assert _ups(_calls(root, "skipped")) == []
    assert _said(report)["typed"].startswith("typed: joined Tailscale ")
    assert _said(report)["quiet"].startswith("quiet: joined Tailscale ")
    assert report["rc"] == 0, _failures(report)


def test_tailscale_login_auto_start_defaults_to_on():
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert defaults["tailscale_login_auto_start"] is True
    assert defaults["tailscale_auth_method"] == "auto"


def test_asking_first_signs_in_only_where_someone_is(tmp_path):
    report = _deploy_step(tmp_path, tailscale_login_auto_start=False)
    assert _asked(report) == {"typed", "quiet"}
    assert len(_ups(_calls(tmp_path, "typed"))) == 1
    assert _ups(_calls(tmp_path, "quiet")) == [] and _ups(_calls(tmp_path, "skipped")) == []
    assert report["rc"] == 0, _failures(report)


def test_asking_first_at_a_terminal_asks_under_a_block(tmp_path):
    # Enter at a VM that showed no Claude prompt leaves it: the question is the
    # one place this step waits for the person, set apart from the run.
    hosts = _hosts(tmp_path, {"quiet": {"ts": {"approve-after": "7"}, "tailscale_login_timeout": 30}})
    hosts["quiet"].update(AFTER_CLAUDE["quiet"])
    settings = {**WEB_EXTRA, "vm_name": "quiet", "deploy_sign_in": True, "tailscale_login_auto_start": False}
    rc, output, _ = _run_at_a_terminal(
        tmp_path, _deploy_tailscale(tmp_path), hosts, settings, "", prompt=b"or Enter alone to skip"
    )
    assert rc == 0, output
    block = "##  USER INPUT REQUIRED  -  quiet  -  Tailscale sign-in"
    assert block in output and output.index(block) < output.index("or Enter alone to skip") < output.index("Answer:")
    assert _ups(_calls(tmp_path, "quiet")) == []


@pytest.mark.parametrize("switch", ["deploy_sign_in", "tailscale_enabled"])
def test_make_deploy_s_tailscale_step_is_off_with_either_switch(tmp_path, switch):
    hosts = _hosts(tmp_path, {"typed": {}})
    hosts["typed"].update(AFTER_CLAUDE["typed"])
    extra = {**WEB_EXTRA, "vm_name": "typed", "deploy_sign_in": True, switch: False}
    report = _run(tmp_path, _deploy_tailscale(tmp_path), hosts, "-e", json.dumps(extra))
    assert report["rc"] == 0, _failures(report)
    assert _calls(tmp_path, "typed") == []
    assert not any(not result.get("skipped") for name, host, result in _results(report) if "tailscale :" in name)


# --------------------------------------------------------------- check mode ---
CHECK_CASES = {
    "check-no-package": {"package": False},
    "check-would-join": {"key": "good"},
    "check-would-join-over-a-page": {"key": "good", "ts": {"authurl": "https://login.tailscale.com/a/0123456789abcd"}},
    "check-drift": {"ts": _joined("check-drift", hostname="drifted"), "marker": NODE},
}


def test_check_mode_says_what_a_run_would_do_and_does_none_of_it(tmp_path):
    hosts = _hosts(tmp_path, CHECK_CASES)
    report = _role_run(tmp_path, hosts, "--check")
    assert _failures(report) == {}
    said = _said(report)
    assert said["check-no-package"] == (
        "check-no-package: Tailscale is not installed, and check mode installs nothing; make deploy"
        " VM_NAME=check-no-package TYPE=configure installs it."
    )
    assert _calls(tmp_path, "check-no-package") == []
    assert said["check-would-join"] == (
        "check-would-join: Tailscale would join with vault_tailscale_auth_key; check mode sends nothing. make"
        " deploy VM_NAME=check-would-join TAGS=tailscale joins it."
    )
    assert _ups(_calls(tmp_path, "check-would-join")) == []
    assert said["check-would-join-over-a-page"].startswith(
        "check-would-join-over-a-page: Tailscale would join with vault_tailscale_auth_key; check mode sends nothing."
    )
    assert said["check-drift"].endswith(" Check mode: tailscale set --hostname=check-drift would run.")
    assert not any(call.startswith("set ") for call in _calls(tmp_path, "check-drift"))


# -------------------------------------------------- configure.yml's rescue ---
def _configure(root: Path, hosts: dict, **extra) -> dict:
    return _run(
        root,
        REPO / "playbooks" / "configure.yml",
        hosts,
        "--tags",
        "tailscale",
        "-e",
        json.dumps(
            {
                **EXTRA,
                "claude_vm_discovery": False,
                "configure_wait_for_cloud_init": False,
                "claude_ssh_config": False,
                **extra,
            }
        ),
    )


def test_a_tailscale_failure_leaves_the_vm_its_alias_and_fails_the_run_at_its_end(tmp_path):
    # A hostname Tailscale would refuse fails the role at its first task. The
    # rescue records it, post_tasks still record the workspace - which the alias
    # play writes from - and the last play fails the VM with the retry command.
    hosts = _hosts(tmp_path, {"badname": {"tailscale_hostname": "bad_name!"}})
    report = _configure(tmp_path, hosts)
    assert report["rc"] != 0
    workspace = [
        result
        for name, host, result in _results(report)
        if name.endswith("Record where the code lives, for the alias play below") and not result.get("skipped")
    ]
    assert workspace and workspace[0]["ansible_facts"]["claude_vm_workspace"].endswith("/projects"), workspace
    line = (
        'badname: Tailscale failed at "Fail when tailscale_hostname is not one DNS label": tailscale_hostname is'
        ' "bad_name!", which Tailscale refuses as a machine name: it must be one DNS label - letters, digits and'
        " hyphens, at most 63, not starting or ending with a hyphen. Retry it with: make deploy VM_NAME=badname"
        " TAGS=tailscale"
    )
    assert _failures(report) == {"badname": f"Fail where Tailscale failed: {line}"}, _failures(report)


def test_tailscale_auth_method_key_without_a_key_fails_by_name(tmp_path):
    hosts = _hosts(tmp_path, {"nokey": {"tailscale_auth_method": "key"}})
    report = _configure(tmp_path, hosts)
    assert report["rc"] != 0
    assert "tailscale_auth_method is key, but vault_tailscale_auth_key is empty" in _failures(report)["nokey"]


def test_under_deploy_the_failure_waits_for_deploy_s_own_last_play(tmp_path):
    hosts = _hosts(tmp_path, {"badname": {"tailscale_hostname": "bad_name!"}})
    report = _configure(tmp_path, hosts, claude_vm_configure_in_deploy=True)
    assert report["rc"] == 0, _failures(report)
    recorded = [
        result["ansible_facts"]["claude_vm_configure_failures"]
        for name, host, result in _results(report)
        if name.endswith("Remember that Tailscale failed, for the end of the run")
    ]
    assert len(recorded) == 1 and recorded[0][0].startswith('badname: Tailscale failed at "'), recorded


# ------------------------------------------------------------ static guards ---
def _all_tasks(tasks: list) -> list:
    """Every task, those inside blocks and rescues included."""
    found = []
    for task in tasks or []:
        found.append(task)
        for key in ("block", "rescue", "always"):
            found.extend(_all_tasks(task.get(key, [])))
    return found


def _role_tasks() -> dict:
    return {path.name: _all_tasks(yaml.safe_load(path.read_text())) for path in sorted((ROLE / "tasks").glob("*.yml"))}


NOT_LOGGED = {"name", "when", "changed_when", "failed_when", "until", "register", "no_log", "tags"}
KEY_VAR = re.compile(r"\btailscale_auth_key\b")


def test_only_the_join_renders_the_key_and_it_is_no_log():
    # Without no_log the key goes to the VM's journal - a module logs the
    # arguments its spec does not mark no_log, stdin included - and into results.
    rendering = {}
    for tasks in _role_tasks().values():
        for task in tasks:
            if "block" in task:
                continue
            args = {key: value for key, value in task.items() if key not in NOT_LOGGED}
            if KEY_VAR.search(json.dumps(args)):
                rendering[task["name"]] = task.get("no_log")
    assert rendering == {JOIN: True}, rendering


def test_the_key_goes_in_on_standard_input_and_never_through_a_file():
    join = next(task for task in _role_tasks()["connect.yml"] if task["name"] == JOIN)
    command = join["ansible.builtin.command"]
    assert "'--auth-key=file:/dev/stdin'" in command["argv"]
    assert command["stdin"] == "{{ tailscale_auth_key }}"
    writers = ("copy", "template", "lineinfile", "blockinfile")
    for name, tasks in _role_tasks().items():
        for task in tasks:
            for writer in writers:
                assert not KEY_VAR.search(json.dumps(task.get(f"ansible.builtin.{writer}", ""))), (name, task["name"])


def test_tailscale_is_called_by_name_so_a_stand_in_ahead_on_path_wins():
    text = "\n".join(path.read_text() for path in (ROLE / "tasks").glob("*.yml"))
    assert "/usr/bin/tailscale" not in text and "/usr/sbin/tailscale " not in text


def test_the_log_switch_is_a_drop_in_written_before_the_package():
    tasks = _role_tasks()["install.yml"]
    names = [task["name"] for task in tasks]
    drop_in = names.index("install | Keep tailscaled from uploading its logs, from its first start")
    package = names.index("install | Install Tailscale")
    assert drop_in < package
    # /etc/default/tailscaled is the package's conffile: created before it, dpkg
    # would keep it without PORT. Touched once, after the package, and only FLAGS.
    touching = [task["name"] for task in tasks if "/etc/default/tailscaled" in json.dumps(task)]
    assert touching == ["install | Hand tailscaled its extra flags"], touching
    assert names.index("install | Hand tailscaled its extra flags") > package


def test_off_never_disables_the_unit_of_a_package_it_then_removes():
    # deb-systemd-helper remembers a disable, and a Tailscale installed again by
    # hand afterwards came up disabled and never started.
    absent = _all_tasks(yaml.safe_load((ROLE / "tasks" / "absent.yml").read_text()))
    stop = next(task for task in absent if task.get("name") == "absent | Stop tailscaled")
    enabled = stop["ansible.builtin.systemd_service"]["enabled"]
    assert enabled == "{{ omit if tailscale_package_present | bool else false }}", enabled


def test_the_role_has_no_handlers():
    # A handler runs after configure.yml's block, out of its rescue's reach.
    assert not (ROLE / "handlers").exists()
    for name, tasks in _role_tasks().items():
        assert not any("notify" in task for task in tasks), name


def test_ownership_asks_dpkg_rather_than_package_facts():
    # package_facts needs python3-apt, which this machine - where these tests run the role's files - lacks.
    text = "\n".join(path.read_text() for path in (ROLE / "tasks").glob("*.yml"))
    assert "package_facts" not in text.replace("# dpkg-query rather than package_facts", "").replace(
        "package_facts needs", ""
    )
    assert "dpkg-query" in (ROLE / "tasks" / "ownership.yml").read_text()


def test_configure_runs_the_role_last_in_a_rescued_block_and_deploy_defers_its_failure():
    plays = yaml.safe_load((REPO / "playbooks" / "configure.yml").read_text())
    play = next(play for play in plays if play.get("name") == "Configure Claude development VMs")
    block = play["tasks"][0]
    assert block["tags"] == ["tailscale"]
    assert block["block"][0]["ansible.builtin.import_role"] == {"name": "tailscale"}
    assert any("claude_vm_configure_failures" in json.dumps(task) for task in block["rescue"])
    assert "tailscale" not in [role["role"] for role in play["roles"]]
    last = plays[-1]
    assert last["tags"] == ["always"]
    assert "not (claude_vm_configure_in_deploy | default(false) | bool)" in last["tasks"][0]["when"]
    deploy = yaml.safe_load((REPO / "deploy.yml").read_text())
    imported = next(play for play in deploy if play.get("ansible.builtin.import_playbook") == "playbooks/configure.yml")
    assert imported["vars"]["claude_vm_configure_in_deploy"] is True
    assert imported["vars"]["tailscale_sign_in_follows"] == "{{ deploy_sign_in }}"


# ------------------------------------------- every test setup kept off Tailscale ---
def _scenario_vars(paths) -> dict:
    merged: dict = {}
    for path in paths:
        merged.update(yaml.safe_load(path.read_text()) or {})
    return merged


def _runs_the_role(scenario: Path) -> bool:
    return any(
        re.search(r"configure\.yml|deploy\.yml|role: tailscale|name: tailscale", path.read_text())
        for path in scenario.glob("*.yml")
        if path.name != "molecule.yml"
    )


@pytest.mark.parametrize("scenario", sorted((REPO / "molecule").glob("*/")), ids=lambda path: path.name)
def test_every_playbook_scenario_that_runs_the_role_keeps_tailscale_off_the_network(scenario):
    # As login_desktop's guard reads it: a VM provisioning or deploy adds at
    # runtime has only group_vars/all; one listed among the platforms has its
    # host_vars on top, and a container prepare.yml can put the stand-in on.
    if not _runs_the_role(scenario):
        return
    molecule = yaml.safe_load((scenario / "molecule.yml").read_text())
    platforms = [p["name"] for p in molecule.get("platforms", []) if "claude_vms" in p.get("groups", [])]
    runtime = any(re.search(r"provision\.yml|deploy\.yml", path.read_text()) for path in scenario.glob("*.yml"))
    group_all = _scenario_vars(sorted([*scenario.glob("group_vars/all/*.yml"), *scenario.glob("group_vars/all.yml")]))
    if runtime:
        assert group_all.get("tailscale_upload_logs") is False, f"{scenario.name}: group_vars/all"
    for host in platforms:
        found = {**group_all, **_scenario_vars(sorted(scenario.glob(f"host_vars/{host}.yml")))}
        assert found.get("tailscale_upload_logs") is False, (scenario.name, host)
    if platforms:
        prepare = (scenario / "prepare.yml").read_text()
        assert "fake_tailscale.sh" in prepare and "dest: /usr/local/bin/tailscale" in prepare, scenario.name


def test_the_role_scenario_keeps_tailscale_off_the_network():
    scenario = ROLE / "molecule" / "default"
    converge = yaml.safe_load((scenario / "converge.yml").read_text())
    for play in converge:
        for role in play.get("roles", []):
            assert role["vars"]["tailscale_upload_logs"] is False
            assert role["vars"]["tailscale_login_desktop"] is False
    prepare = (scenario / "prepare.yml").read_text()
    assert "fake_tailscale.sh" in prepare and "dest: /usr/local/bin/tailscale" in prepare


def test_the_vagrantfile_keeps_tailscale_off_the_network():
    # Vagrant loads no group_vars, so the role's own defaults - logs on - would apply.
    text = (REPO / "Vagrantfile").read_text()
    assert re.search(r"^\s*tailscale_upload_logs: false,", text, flags=re.M)
    assert re.search(r'^\s*tailscale_auth_key: "",', text, flags=re.M)


def test_every_role_scenario_is_in_the_makefile_and_ci():
    # Both lists are written by hand: a role left out of them is never tested.
    with_scenarios = {path.parent.parent.name for path in (REPO / "roles").glob("*/molecule/default")}
    makefile = (REPO / "Makefile").read_text()
    roles = set(re.search(r"^ROLES\s*:=\s*(.*)$", makefile, flags=re.M).group(1).split())
    ci = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
    matrix = set(ci["jobs"]["molecule"]["strategy"]["matrix"]["role"])
    assert with_scenarios == roles == matrix, (with_scenarios, roles, matrix)


def test_a_harness_run_finds_a_stand_in_tailscale_never_this_machine_s(tmp_path):
    # This Mac may have the real CLI on the PATH a run inherits. A run's own
    # PATH resolves tailscale to the failing stand-in, and a host's bin/ to the fake.
    path = _env(tmp_path)["PATH"]
    assert "tailscale" in GUARDED_TOOLS
    assert shutil.which("tailscale", path=path) == str(tmp_path / ".desktop" / "tailscale")
    hosts = _hosts(tmp_path, {"probe": {}})
    assert hosts["probe"]["tailscale_login_desktop"] is False
    assert shutil.which("tailscale", path=f"{tmp_path / 'bin'}:{path}") == str(tmp_path / "bin" / "tailscale")


def test_the_inventory_wires_the_desktop_switch_into_the_role():
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert re.fullmatch(r"\{\{\s*login_desktop\s*\}\}", defaults["tailscale_login_desktop"])


def test_destroy_names_the_device_left_of_each_vm_it_could_not_release():
    # logout frees no name: a VM built again under it would join as <name>-1, for good.
    # tests/unit/test_release.py runs the report; this holds its wording for an
    # unreleased VM, and that it reads only the VMs actually deleted.
    plays = yaml.safe_load((REPO / "playbooks" / "destroy.yml").read_text())
    tasks = [task for play in plays for task in play.get("tasks", [])]
    report = next(
        task for task in tasks if task["name"] == "Say what each deleted VM was released from, and what is left of it"
    )
    assert "selectattr('claude_vm_deleted', 'defined')" in report["loop"]
    assert "https://login.tailscale.com/admin/machines" in report["vars"]["claude_release_where"]
    assert "<name>-1" in report["vars"]["claude_release_left"]
