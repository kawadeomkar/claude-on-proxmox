"""Releasing a VM before just destroy deletes it: destroy.yml's release play and report, run for real.

Every host is the controller itself, over the local connection (harness.py),
with the stand-in claude, gh and tailscale of tests/molecule first on its PATH,
the fake GitHub API and a fake Tailscale API on loopback ports, and no systemd:
Remote Control's units and tailscaled's are absent here, so what systemd does
with them is the Molecule scenarios' to show. The release play and the report
are taken from destroy.yml as they stand.

What these guard:
- A released VM is signed out of Claude Code and of gh. No gh token is revoked
  at GitHub: revoking the one this project's device flow made ended every gh
  sign-in of the account (seen live), so the report says where to revoke it and
  what that does; the vault's token, which every VM shares, and a token pasted
  by hand are told apart from it.
- Its Tailscale node is logged out, and with an API credential its device is
  deleted from the tailnet, from this machine: by an API access token, or an
  OAuth client exchanged for one - and only a device named as this project
  asked the VM to join, since the VM reports the ID. A refusal is said with the
  device to delete by hand, and a device already gone as such.
- A VM with no address is left as it is, and the report names what is left of
  it to remove by hand.
- No token, key or secret reaches the output, module arguments included.
- destroy starts a stopped VM to release it or save its sessions, and releases
  before it deletes - finding the address of a VM it started though the cluster
  listing, seconds behind, still says it is stopped.
- Before anything is released, what of a VM's Claude Code sessions is only
  there is saved here, into the test's own directory: never a copy just code
  made still as written, never a link the VM holds or a name it made up, and
  without sudo, which a VM can refuse; a save that fails keeps the VM, neither
  released nor deleted, and destroy_release: false still saves.
"""

from __future__ import annotations

import hashlib
import json
import stat
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml
from harness import (
    PVE_SECRET,
    REPO,
    STAND_INS,
    _certificate,
    _failures,
    _left_to_do,
    _pve,
    _run,
    _run_at_a_terminal,
    _serve,
)

DESTROY = REPO / "playbooks" / "destroy.yml"
FAKES = REPO / "tests" / "molecule"
REPORT = "Say what each deleted VM was released from, and what is left of it"
ADMIN = "https://login.tailscale.com/admin/machines"

# Canned credentials, each searched for in the whole report.
DEVICE_TOKEN = "gho_device0000000000"
VAULT_TOKEN = "ghp_vault0000000000000000000000000000"
HAND_TOKEN = "gho_pastedbyhand000000000000"
API_TOKEN = "tskey-api-good-0000"
FORBIDDEN_TOKEN = "tskey-api-denied-0000"
CLIENT_SECRET = "tskey-client-good-0000"
REFUSED_CLIENT_SECRET = "tskey-client-refused-0000"
SECRETS = [
    DEVICE_TOKEN,
    VAULT_TOKEN,
    HAND_TOKEN,
    API_TOKEN,
    FORBIDDEN_TOKEN,
    CLIENT_SECRET,
    REFUSED_CLIENT_SECRET,
    "tskey-api-oauth-0000",
]

GETENT = """#!/bin/sh
[ "$1" = passwd ] || { echo "stand-in getent: unexpected database $1" >&2; exit 3; }
printf '%s:x:1000:1000:%s:%s:/bin/bash\\n' "$2" "$2" "$HOME"
"""

# One VM per way a release can go. "api" is the Tailscale API credential the
# vault holds, and "method" tailscale_api_auth_method where a case sets it:
# elsewhere the role's default, auto, decides from the credential alone.
CASES = {
    # Everything this project signs a VM in to, and an API access token.
    "everything": {"claude": True, "gh": DEVICE_TOKEN, "device_marker": True, "node": "nEVERYTHING", "api": "token"},
    # The vault's token in gh; Tailscale with no API credential.
    "vault-token": {"gh": VAULT_TOKEN, "vault": True, "node": "nVAULT"},
    # A token pasted by hand; Tailscale through an OAuth client.
    "by-hand": {"gh": HAND_TOKEN, "node": "nBYHAND", "api": "oauth"},
    # A credential Tailscale refuses for this device, named by the method.
    "refused": {"node": "nREFUSED", "api": "forbidden", "method": "token"},
    # An API access token in the vault, and none asked for: it stays unused.
    "kept": {"node": "nKEPT", "api": "token", "method": "none"},
    # An API access token and an OAuth client, one Tailscale would refuse.
    "both": {"node": "nBOTH", "api": "both"},
    # Half an OAuth client: its ID, and no secret.
    "half-oauth": {"node": "nHALF", "api": "half-oauth"},
    # A device the tailnet no longer has.
    "gone": {"node": "nGONE", "api": "token"},
    # A VM reporting another device's ID - a laptop's: never deleted.
    "liar": {"node": "nLAPTOP", "api": "token"},
    # A VM whose marker claims the vault token: it is every VM's, never taken for this project's sign-in.
    "marked-vault": {"gh": VAULT_TOKEN, "vault": True, "device_marker": True},
    # Two accounts in gh: with no terminal, its logout will not choose one.
    "two-accounts": {"gh": HAND_TOKEN, "two_accounts": True},
    # Nothing signed in at all.
    "nothing": {},
    # Claude Code sessions of its own, and copies just code made: only the first are saved.
    "sessions": {"sessions": True},
    # The same, with the save turned off: they go with the VM.
    "unsaved": {"sessions": True, "save": False},
    # The same, just code's own directory set elsewhere by vscode_workspaces_dir, its manifest there.
    "workspaces": {"sessions": True, "workspaces": True},
    # No address: stopped and would not start, or an agent that never answered.
    "unreached": {"address": ""},
}

# The VM's sessions in the "sessions" case: begun there, a copy just code wrote
# still as written, a copy continued there, and one with no message.
STARTED = "0a608ad8-1b2c-4d3e-8f90-123456789abc"
COPIED = "bc0e637b-2c3d-4e4f-9a01-23456789abcd"
CONTINUED = "c1d2e3f4-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
EMPTY = "d4e5f6a7-8b9c-4d0e-9f1a-2b3c4d5e6f70"
VM_FOLDER = "-home-dev-projects-demo"
MAC_FOLDER = "-Users-me-demo"
# A folder no Claude Code names so, and a session in it: the VM's to make up, and never saved.
ODD_FOLDER = "-home-dev-projects-a b"
ODD = "e5f6a7b8-9c0d-4e1f-8a2b-3c4d5e6f7a81"
# What a tool printed, as a transcript holds it: it must never reach a run's output.
MARKER = "TRANSCRIPT-MARKER-5e2a9c"


def _transcript(sid: str, cwd: str, conversation: bool = True) -> bytes:
    base = {"cwd": cwd, "sessionId": sid, "version": "2.1.308", "gitBranch": "main"}
    entries = (
        [{**base, "type": "user", "message": {"role": "user", "content": f"run it {MARKER}"}}]
        if conversation
        else [{**base, "type": "summary", "summary": "nothing"}]
    )
    return "".join(json.dumps(entry, separators=(",", ":")) + "\n" for entry in entries).encode()


def _vm_sessions(home: Path, manifest_dir: Path | None = None) -> None:
    """A VM's ~/.claude/projects/ as just code and the VM's own sessions leave it, and just code's manifest.

    A link among them, to a file of this machine's outside the VM's home, and a session in a folder not
    named as Claude Code names one: neither may be saved.
    """
    projects = home / ".claude" / "projects"
    (projects / VM_FOLDER / STARTED / "subagents").mkdir(parents=True)
    (projects / VM_FOLDER / STARTED / "subagents" / "agent-1.jsonl").write_text("{}\n")
    outside = home.parent / f"{home.name}-outside.txt"
    outside.write_text("this machine's\n")
    outside.chmod(0o644)
    (projects / VM_FOLDER / STARTED / "subagents" / "link").symlink_to(outside)
    (projects / ODD_FOLDER).mkdir()
    (projects / ODD_FOLDER / f"{ODD}.jsonl").write_bytes(_transcript(ODD, "/home/dev/projects/a b"))
    (projects / VM_FOLDER / f"{STARTED}.jsonl").write_bytes(_transcript(STARTED, "/home/dev/projects/demo"))
    (projects / VM_FOLDER / f"{EMPTY}.jsonl").write_bytes(_transcript(EMPTY, "/home/dev/projects/demo", False))
    (projects / VM_FOLDER / "memory").mkdir()
    (projects / VM_FOLDER / "memory" / "MEMORY.md").write_text("- a note\n")
    (projects / MAC_FOLDER).mkdir()
    copied = _transcript(COPIED, "/Users/me/demo")
    (projects / MAC_FOLDER / f"{COPIED}.jsonl").write_bytes(copied)
    written = _transcript(CONTINUED, "/Users/me/demo")
    (projects / MAC_FOLDER / f"{CONTINUED}.jsonl").write_bytes(
        written + _transcript(CONTINUED, "/home/dev/projects/demo")
    )
    manifest = (manifest_dir or home / "projects" / ".claude-on-proxmox") / "claude-sessions" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "format": 1,
                "sessions": {
                    f"{MAC_FOLDER}/{COPIED}.jsonl": hashlib.sha256(copied).hexdigest(),
                    f"{MAC_FOLDER}/{CONTINUED}.jsonl": hashlib.sha256(written).hexdigest(),
                },
            }
        )
    )


def _get(url: str):
    with urllib.request.urlopen(url, timeout=10) as answer:
        return json.loads(answer.read())


def _hosts(root: Path, cases: dict, github: str, tailscale: str) -> dict:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name, text in (
        ("python", STAND_INS["python"]),
        ("bash", STAND_INS["bash"]),
        ("getent", GETENT),
        ("claude", (FAKES / "fake_claude.sh").read_text()),
        ("gh", (FAKES / "fake_gh.sh").read_text()),
        ("tailscale", (FAKES / "fake_tailscale.sh").read_text()),
    ):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    hosts = {}
    for name, case in cases.items():
        home = root / name
        (home / ".claude" / "fake").mkdir(parents=True)
        if case.get("claude"):
            (home / ".claude" / ".credentials.json").write_text('{"claudeAiOauth": {}}\n')
        if case.get("gh"):
            (home / ".config" / "gh").mkdir(parents=True)
            (home / ".config" / "gh" / "fake-token").write_text(case["gh"] + "\n")
        if case.get("two_accounts"):
            (home / ".config" / "gh" / "fake-two-accounts").write_text("")
        if case.get("device_marker"):
            (home / ".config" / "claude-on-proxmox").mkdir(parents=True)
            (home / ".config" / "claude-on-proxmox" / "gh-device-login").write_text(
                hashlib.sha256(case["gh"].encode()).hexdigest() + "\n"
            )
        state = home / ".tailscale-fake"
        state.mkdir()
        if case.get("node"):
            (state / "joined").write_text("")
            (state / "node-id").write_text(case["node"])
        if case.get("sessions"):
            _vm_sessions(home, home / "ws" if case.get("workspaces") else None)
        (home / "python").symlink_to(bin_dir / "python")
        api = case.get("api", "")
        hosts[name] = {
            "ansible_connection": "local",
            "ansible_python_interpreter": str(home / "python"),
            "vm_user": "dev",
            **({"destroy_save_sessions": case["save"]} if "save" in case else {}),
            **({"vscode_workspaces_dir": str(home / "ws")} if case.get("workspaces") else {}),
            "claude_vm_release_address": case.get("address", "192.0.2.10"),
            "github_projects_home": str(home),
            "github_projects_api_url": github,
            "github_projects_token": VAULT_TOKEN if case.get("vault") else "",
            "tailscale_api_url": tailscale,
            **({"tailscale_api_auth_method": case["method"]} if "method" in case else {}),
            "tailscale_api_token": {"token": API_TOKEN, "both": API_TOKEN, "forbidden": FORBIDDEN_TOKEN}.get(api, ""),
            "tailscale_oauth_client_id": {"oauth": "kOAUTHCLIENT", "both": "kBOTH", "half-oauth": "kHALF"}.get(api, ""),
            "tailscale_oauth_client_secret": {"oauth": CLIENT_SECRET, "both": REFUSED_CLIENT_SECRET}.get(api, ""),
            # Every module's arguments in its result, so that a credential handed
            # to a task without no_log would reach the report searched below.
            "ansible_inject_invocation": True,
        }
    return hosts


def _destroy_tail(root: Path, saved: Path | None = None) -> Path:
    """destroy.yml's release play, the deletion marked as done, and its report: as destroy.yml has them.

    The sessions a VM's save brings back go to ``saved``, root/saved without one: never the checkout's .sessions/.
    """
    plays = yaml.safe_load(DESTROY.read_text())
    release = next(play for play in plays if play.get("name") == "Release each VM from what it signed in to")
    # Its task files where they are: the play runs from a copy here.
    release = json.loads(json.dumps(release).replace('"tasks/', f'"{REPO / "playbooks" / "tasks"}/'))
    release["vars"] = {**release.get("vars", {}), "destroy_sessions_dir": str(saved or root / "saved")}
    tasks = next(play["tasks"] for play in plays if any(task.get("name") == REPORT for task in play.get("tasks", [])))
    at = next(index for index, task in enumerate(tasks) if task.get("name") == REPORT)
    # The report and its record for NEXT STEPS, then the kept aliases' and
    # theirs, which are skipped here, every deletion having been done: a
    # record's condition naming its report's task var failed every destroy.
    report = tasks[at : at + 4]
    assert [task.get("name") for task in report] == [
        REPORT,
        "Keep what is left to do for the end of the run",
        "Say which aliases were kept, and why",
        "Keep what is left to do for the end of the run",
    ]
    on_these = json.loads(json.dumps(report).replace("groups['claude_vm_doomed']", "groups['claude_vms']"))
    playbook = root / "destroy_tail.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {**release, "hosts": "claude_vms"},
                {
                    "name": "Mark each VM deleted, as destroy's deletion does",
                    "hosts": "claude_vms",
                    "gather_facts": False,
                    # But one the release play kept, as the deletion leaves it.
                    "tasks": [
                        {
                            "ansible.builtin.set_fact": {"claude_vm_deleted": True},
                            "when": "claude_vm_delete_failure is not defined",
                        }
                    ],
                },
                {"name": "Report", "hosts": "localhost", "gather_facts": False, "tasks": on_these},
            ]
        )
    )
    return playbook


@pytest.fixture(scope="module")
def fake_github(tmp_path_factory):
    # Its account and an empty directory of repositories: only the revocation is asked here.
    yield from _serve(FAKES / "fake_github_api.py", "dev", str(tmp_path_factory.mktemp("git")))


# The tailnet's devices, by node ID: each VM's under its own name - by-hand's
# as Tailscale names a taken one - and a laptop that is none of theirs.
DEVICES = {
    "nEVERYTHING": "everything",
    "nVAULT": "vault-token",
    "nBYHAND": "by-hand-1",
    "nREFUSED": "refused",
    "nKEPT": "kept",
    "nBOTH": "both",
    "nHALF": "half-oauth",
    "nLAPTOP": "laptop",
}


@pytest.fixture(scope="module")
def fake_tailscale():
    yield from _serve(FAKES / "fake_tailscale_api.py", *(f"{node}={name}" for node, name in DEVICES.items()))


@pytest.fixture(scope="module")
def released(tmp_path_factory, fake_github, fake_tailscale):
    root = tmp_path_factory.mktemp("release")
    hosts = _hosts(root, CASES, fake_github, fake_tailscale)
    # No sudo here. Never a connection: each host's own, local here, as
    # destroy's add_host gives each VM its SSH settings - forced with -e, a
    # connection set in the release play's vars would go unseen, and one went
    # to the VM with the Tailscale API's credential.
    extra = {
        "ansible_become": False,
        # An internal of the tailscale role (vars/main.yml): only -e outranks it.
        "tailscale_settle_seconds": 3,
    }
    report = _run(root, _destroy_tail(root), hosts, "-e", json.dumps(extra), ordered=True)
    return root, report, _get(f"{fake_github}/fake/revoked"), _get(f"{fake_tailscale}/fake/deleted")


def _said(report: dict) -> dict:
    lines = {}
    for name, _host, result in _results_of(report):
        if name.endswith(REPORT):
            for item in result.get("results", []):
                vm = item.get("claude_release_name")
                if vm:
                    lines[vm] = item["msg"]
    return lines


def _results_of(report: dict):
    for play in report["plays"]:
        for task in play["tasks"]:
            for host, result in task["hosts"].items():
                yield task["task"]["name"], host, result


def _calls(root: Path, host: str, *path: str) -> list[str]:
    log = root.joinpath(host, *path)
    return log.read_text().splitlines() if log.exists() else []


def test_a_vm_signed_in_to_everything_is_released_from_everything(released):
    root, report, revoked, deleted = released
    assert report["rc"] == 0, _failures(report)
    assert not (root / "everything" / ".claude" / ".credentials.json").exists()
    assert "auth logout" in _calls(root, "everything", ".claude", "fake", "calls.log")
    assert not (root / "everything" / ".config" / "gh" / "fake-token").exists()
    assert "logout" in _calls(root, "everything", ".tailscale-fake", "calls.log")
    assert "nEVERYTHING" in deleted
    assert _said(report)["everything"] == (
        "everything: released - Claude Code: signed out; GitHub: gh signed out; the token this project signed it in"
        " with stays valid until revoked at https://github.com/settings/applications, under GitHub CLI - which signs"
        " out every gh you have, so destroy does not; Tailscale: removed from your tailnet."
    )


def test_no_token_is_ever_revoked_at_github(released):
    # Revoking one GitHub CLI token through the credential revocation endpoint
    # ended every gh sign-in of the account - the developer's own included.
    _, _, revoked, _ = released
    assert revoked == []


def test_the_vault_token_and_a_token_pasted_by_hand_are_signed_out_and_told_apart(released):
    root, report, revoked, deleted = released
    for host in ("vault-token", "by-hand"):
        assert not (root / host / ".config" / "gh" / "fake-token").exists(), host
    said = _said(report)
    assert "GitHub: gh signed out (vault_github_token is every VM's, so it is not revoked)" in said["vault-token"]
    assert (
        "GitHub: gh signed out; its token is not one this project recorded making (a sign-in by hand, or one before"
        " this version), and stays valid until revoked - at https://github.com/settings/tokens if it is a personal"
        " access token, or under GitHub CLI at https://github.com/settings/applications, which signs out every gh"
        " you have;"
    ) in said["by-hand"]


def test_a_logout_gh_refuses_is_said_never_reported_as_done(released):
    root, report, revoked, deleted = released
    assert (root / "two-accounts" / ".config" / "gh" / "fake-token").exists()
    assert _said(report)["two-accounts"] == (
        "two-accounts: released - GitHub: gh could not sign out (unable to determine which account to log out of,"
        " please specify `--hostname` and `--user`); its token is not one this project recorded making (a sign-in"
        " by hand, or one before this version), and stays valid until revoked - at https://github.com/settings/tokens"
        " if it is a personal access token, or under GitHub CLI at https://github.com/settings/applications, which"
        " signs out every gh you have."
    )


def test_without_an_api_credential_the_node_is_logged_out_and_its_device_named(released):
    root, report, revoked, deleted = released
    assert "nVAULT" not in deleted
    assert "logout" in _calls(root, "vault-token", ".tailscale-fake", "calls.log")
    assert _said(report)["vault-token"].endswith(
        f"Tailscale: logged out; its device stays in your tailnet until you delete it at {ADMIN}, or a VM built again"
        " under its name joins as <name>-1."
    )


def test_an_oauth_client_is_exchanged_for_a_token_that_deletes_the_device(released):
    root, report, revoked, deleted = released
    assert "nBYHAND" in deleted
    assert _said(report)["by-hand"].endswith("Tailscale: removed from your tailnet.")


def test_a_refused_deletion_and_a_device_already_gone_are_said(released):
    _, report, _, deleted = released
    said = _said(report)
    assert "nREFUSED" not in deleted
    assert said["refused"] == (
        "refused: released - Tailscale: logged out, but its device was not deleted - Tailscale refused the call"
        " (HTTP 403): the API access token may not delete this device - an OAuth client only those with its tags:"
        f" delete it at {ADMIN}."
    )
    assert said["gone"] == (
        "gone: released - Tailscale: logged out; Tailscale had no such device (HTTP 404) - it was gone already, or is"
        " in a tailnet the API credential does not reach."
    )


def test_a_device_is_deleted_only_under_the_name_this_project_asked_the_vm_to_join_as(released):
    # The ID is the VM's to report, and the credential could delete any device.
    _, report, _, deleted = released
    assert "nLAPTOP" not in deleted
    assert _said(report)["liar"] == (
        "liar: released - Tailscale: logged out, but its device was not deleted - the device under the ID the VM"
        ' reported is named "laptop", not "liar", so it is not this VM\'s; find this VM\'s own device at'
        f" {ADMIN} and delete it."
    )


def test_a_credential_in_the_vault_is_enough_and_none_keeps_it_unused(released):
    # everything, by-hand, gone and liar set no method: auto took their credential.
    _, report, _, deleted = released
    assert {"nEVERYTHING", "nBYHAND"} <= set(deleted)
    assert "nKEPT" not in deleted
    assert _said(report)["kept"] == (
        f"kept: released - Tailscale: logged out; its device stays in your tailnet until you delete it at {ADMIN},"
        " or a VM built again under its name joins as <name>-1."
    )


def test_auto_takes_the_api_access_token_over_an_oauth_client(released, fake_tailscale):
    # both's OAuth client has a secret Tailscale refuses: its device goes only by the token.
    _, report, _, deleted = released
    assert "nBOTH" in deleted
    assert _said(report)["both"] == "both: released - Tailscale: removed from your tailnet."
    assert "kBOTH" not in _get(f"{fake_tailscale}/fake/exchanged")


def test_half_an_oauth_client_is_said_and_never_sent(released, fake_tailscale):
    _, report, _, deleted = released
    assert "nHALF" not in deleted
    assert _said(report)["half-oauth"] == (
        "half-oauth: released - Tailscale: logged out, but its device was not deleted - the OAuth client needs both"
        f" tailscale_oauth_client_id and tailscale_oauth_client_secret: delete it at {ADMIN}."
    )
    assert _get(f"{fake_tailscale}/fake/exchanged") == ["kOAUTHCLIENT"]


def test_the_vault_token_is_never_taken_for_this_project_s_sign_in_whatever_the_vm_s_marker_says(released):
    _, report, _, _ = released
    assert "GitHub: gh signed out (vault_github_token is every VM's" in _said(report)["marked-vault"]


def test_a_vm_with_nothing_signed_in_is_said_to_have_nothing_to_end(released):
    _, report, _, _ = released
    assert _said(report)["nothing"] == "nothing: released - it had nothing signed in to end."


def _saved(root: Path, vm: str) -> Path:
    (folder,) = (root / "saved").glob(f"{vm}-*")
    return folder


def test_what_only_the_vm_has_of_its_sessions_is_saved_as_it_was(released):
    # Begun on the VM, and a copy continued there, each with its folder, and
    # the auto memory: not a copy still as just code wrote it, which this
    # machine has, nor a transcript without a message, which resumes nothing.
    root, report, _, _ = released
    saved = _saved(root, "sessions")
    assert saved.name.removeprefix("sessions-").replace("-", "").isdigit()
    here = sorted(str(path.relative_to(saved)) for path in saved.rglob("*") if path.is_file())
    assert here == [
        f"projects/{MAC_FOLDER}/{CONTINUED}.jsonl",
        f"projects/{VM_FOLDER}/{STARTED}.jsonl",
        f"projects/{VM_FOLDER}/{STARTED}/subagents/agent-1.jsonl",
        f"projects/{VM_FOLDER}/memory/MEMORY.md",
        "sessions.json",
    ]
    vm = root / "sessions" / ".claude" / "projects"
    for name in here[:4]:
        assert (saved / name).read_bytes() == (vm / name.removeprefix("projects/")).read_bytes(), name
    # This machine's user's alone.
    for path in [saved, *saved.rglob("*")]:
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600), path
    listed = json.loads((saved / "sessions.json").read_text())
    assert listed["vm"] == "sessions"
    assert [(item["id"], item["started_on_the_vm"], item["cwd"]) for item in listed["sessions"]] == [
        (CONTINUED, False, "/Users/me/demo"),
        (STARTED, True, "/home/dev/projects/demo"),
    ]
    started = listed["sessions"][1]
    assert started["transcript"] == str(saved / "projects" / VM_FOLDER / f"{STARTED}.jsonl")
    assert started["resume"] == f"claude --resume {started['transcript']}"
    assert listed["memory"] == [str(saved / "projects" / VM_FOLDER / "memory")]
    assert _said(report)["sessions"] == (
        "sessions: released - Claude Code sessions: 2 saved (1 started there, 1 continued there), with the auto"
        f" memory of 1 project, in {saved} - its sessions.json has the `claude --resume <path>` that continues each"
        " here, with Claude Code 2.1.308 or newer, and what they hold is the VM's writing; left out 2 entries of the"
        " VM that were a link, not a plain file or folder, or not named as Claude Code names its own."
    )
    # What it is, and what resumes it.
    assert listed["note"].startswith("Written on sessions and saved as it sent them, plain files only")
    assert listed["newest_version"] == "2.1.308" and {item["version"] for item in listed["sessions"]} == {"2.1.308"}


def test_a_link_the_vm_holds_never_reaches_or_changes_anything_here(released):
    # rsync brought a VM's link over as a link, and the chmod after it followed it out of the folder.
    root, _, _, _ = released
    saved = _saved(root, "sessions")
    assert not any(path.is_symlink() for path in saved.rglob("*"))
    outside = root / "sessions-outside.txt"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644 and outside.read_text() == "this machine's\n"
    assert not any(ODD in str(path) for path in saved.rglob("*"))


def test_the_manifest_is_read_where_vscode_workspaces_dir_puts_just_code_s_directory(released):
    # Read from the projects directory instead, every copy just code made would be saved as continued.
    root, report, _, _ = released
    saved = _saved(root, "workspaces")
    assert sorted(path.name for path in (saved / "projects" / MAC_FOLDER).iterdir()) == [f"{CONTINUED}.jsonl"]
    assert "2 saved (1 started there, 1 continued there)" in _said(report)["workspaces"]


def test_a_vm_with_none_of_its_own_saves_nothing_and_says_nothing_of_it(released):
    root, report, _, _ = released
    assert sorted(path.name.rsplit("-", 2)[0] for path in (root / "saved").iterdir()) == ["sessions", "workspaces"]
    assert "Claude Code sessions" not in _said(report)["nothing"]


def test_with_the_save_off_the_sessions_go_with_the_vm_and_it_is_said(released):
    root, report, _, _ = released
    assert not list((root / "saved").glob("unsaved-*"))
    assert _said(report)["unsaved"] == (
        "unsaved: released - Claude Code sessions: not saved (destroy_save_sessions: false)."
    )


def test_no_transcript_reaches_the_output(released):
    _, report, _, _ = released
    assert MARKER not in json.dumps(report)


def test_a_vm_that_could_not_be_reached_is_left_as_it_is_and_what_is_left_named(released):
    root, report, _, _ = released
    assert _calls(root, "unreached", ".tailscale-fake", "calls.log") == []
    assert _said(report)["unreached"] == (
        "unreached: could not be reached, so it was not released: its Tailscale device, if it was on your tailnet,"
        f" stays until you delete it at {ADMIN} (or a VM built again under its name joins as <name>-1); a GitHub token"
        " its sign-in made stays valid until revoked at https://github.com/settings/applications, under GitHub CLI"
        " (which signs out every gh); and Anthropic has no way to revoke its Claude Code login from outside. Claude"
        " Code sessions: not saved, nor its auto memory, if it had any."
    )


def test_next_steps_keep_each_vm_with_something_left_to_do_and_nothing_for_one_released_clean(released):
    # What the run's last lines list (callback_plugins/claude_on_proxmox.py):
    # the report's own line where a GitHub token stays valid, a Tailscale
    # device stays in the tailnet, or nothing was released at all.
    _, report, _, _ = released
    # The run itself ended well: a record failing after its report fails the destroy.
    assert report["rc"] == 0, _failures(report)
    said = _said(report)
    left = {vm: matters["destroy"] for vm, matters in _left_to_do(report).items()}
    assert set(left) == set(CASES)
    for vm in (
        "everything",
        "vault-token",
        "by-hand",
        "refused",
        "kept",
        "half-oauth",
        "liar",
        "two-accounts",
        "unreached",
    ):
        assert left[vm] == said[vm], vm
    for vm in ("gone", "both", "marked-vault", "nothing", "sessions", "unsaved", "workspaces"):
        assert left[vm] == "", (vm, said[vm])


def test_no_token_key_or_secret_reaches_the_output(released):
    _, report, _, _ = released
    text = json.dumps(report)
    assert [secret for secret in SECRETS if secret in text] == []


@pytest.fixture(scope="module")
def kept(tmp_path_factory, fake_github, fake_tailscale):
    """A destroy whose save fails, its folder here not writable: the VM signed in to Claude, with a session."""
    root = tmp_path_factory.mktemp("kept")
    hosts = _hosts(root, {"kept": {"claude": True, "sessions": True}}, fake_github, fake_tailscale)
    locked = root / "locked"
    locked.mkdir(mode=0o500)
    try:
        report = _run(root, _destroy_tail(root, locked / "saved"), hosts, "-e", json.dumps({"ansible_become": False}))
    finally:
        locked.chmod(0o700)
    return root, report


def test_a_vm_whose_sessions_could_not_be_saved_is_neither_released_nor_deleted(kept):
    root, report = kept
    assert (root / "kept" / ".claude" / ".credentials.json").exists()
    assert "auth logout" not in _calls(root, "kept", ".claude", "fake", "calls.log")
    assert "kept" not in _said(report)
    (why,) = [
        result["ansible_facts"]["claude_vm_delete_failure"]
        for name, host, result in _results_of(report)
        if name == "Keep the VM, whose sessions could not be saved"
    ]
    assert why.startswith(
        "kept was neither released nor deleted: its Claude Code sessions could not be saved (session_save | Bring them"
        " here: The sessions could not be brought here: "
    )
    assert why.endswith(
        "Run just destroy again once that is fixed, or set destroy_save_sessions: false to delete it without them."
    )
    assert MARKER not in json.dumps(report)


@pytest.fixture(scope="module")
def unreleased(tmp_path_factory, fake_github, fake_tailscale):
    """destroy_release: false, the save on: a VM signed in to Claude, with sessions of its own."""
    root = tmp_path_factory.mktemp("unreleased")
    hosts = _hosts(root, {"held": {"claude": True, "sessions": True}}, fake_github, fake_tailscale)
    extra = {"ansible_become": False, "destroy_release": False}
    return root, _run(root, _destroy_tail(root), hosts, "-e", json.dumps(extra))


def test_without_the_release_the_save_still_reaches_the_vm_first(unreleased):
    root, report = unreleased
    assert report["rc"] == 0, _failures(report)
    saved = _saved(root, "held")
    assert (saved / "projects" / VM_FOLDER / f"{STARTED}.jsonl").exists()
    # Nothing is released: still signed in.
    assert (root / "held" / ".claude" / ".credentials.json").exists()
    assert "auth logout" not in _calls(root, "held", ".claude", "fake", "calls.log")
    said = _said(report)["held"]
    assert said.startswith("held: deleted without being released (destroy_release: false): its Tailscale device")
    assert said.endswith(
        f"Claude Code sessions: 2 saved (1 started there, 1 continued there), with the auto memory of 1 project, in"
        f" {saved} - its sessions.json has the `claude --resume <path>` that continues each here, with Claude Code"
        " 2.1.308 or newer, and what they hold is the VM's writing; left out 2 entries of the VM that were a link,"
        " not a plain file or folder, or not named as Claude Code names its own."
    )


@pytest.fixture(scope="module")
def unsudo(tmp_path_factory, fake_github, fake_tailscale):
    """A VM that answers as its user but whose sudo refuses, as one asking for a password does without a terminal."""
    root = tmp_path_factory.mktemp("unsudo")
    hosts = _hosts(root, {"unsudo": {"claude": True, "sessions": True}}, fake_github, fake_tailscale)
    sudo = root / "bin" / "sudo"
    sudo.write_text('#!/bin/sh\necho "sudo: a password is required" >&2\nexit 1\n')
    sudo.chmod(0o755)
    hosts["unsudo"]["ansible_become_exe"] = str(sudo)
    # The play's become stands: no -e ansible_become here.
    return root, _run(root, _destroy_tail(root), hosts)


def test_a_vm_whose_sudo_refuses_is_saved_and_deleted_unreleased_and_said_why(unsudo):
    # The save reads the VM user's own files and needs no sudo; the releases do.
    root, report = unsudo
    assert report["rc"] == 0, _failures(report)
    saved = _saved(root, "unsudo")
    assert (saved / "projects" / VM_FOLDER / f"{STARTED}.jsonl").exists()
    assert (root / "unsudo" / ".claude" / ".credentials.json").exists()
    assert "auth logout" not in _calls(root, "unsudo", ".claude", "fake", "calls.log")
    said = _said(report)["unsudo"]
    assert said.startswith("unsudo: sudo on it failed (")
    assert ", so it was not released: its Tailscale device" in said
    assert (
        "Claude Code sessions: 2 saved (1 started there, 1 continued there), with the auto memory of 1 project," in said
    )
    assert f" in {saved} - its sessions.json" in said


# ------------------------------------------------------------- destroy.yml ---
def _action(task: dict) -> str:
    (action,) = [key for key in task if "." in key]
    return action


def _flat(tasks: list) -> list:
    """Every task, those in a block, its rescue and its always included."""
    flat = []
    for task in tasks:
        flat.append(task)
        for part in ("block", "rescue", "always"):
            flat += _flat(task.get(part, []))
    return flat


def test_destroy_releases_before_it_deletes_and_starts_a_stopped_vm_only_to_release_it():
    plays = yaml.safe_load(DESTROY.read_text())
    names = [play.get("name") for play in plays]
    assert names.index("Release each VM from what it signed in to") < names.index("Delete the VMs on Proxmox")
    release = plays[names.index("Release each VM from what it signed in to")]
    assert release["hosts"] == "claude_vm_doomed"
    # How it reaches each VM is the host's own (add_host), never the play's:
    # a play's vars apply to its delegated tasks too, and sent the Tailscale
    # API's credential to the VM.
    assert not any(name.startswith("ansible_") for name in release.get("vars", {})), release.get("vars")
    add = next(task for task in _flat(plays[0]["tasks"]) if task["name"] == "Add each VM as a host to delete")
    assert {"ansible_connection", "ansible_host", "ansible_user"} <= set(add["ansible.builtin.add_host"])
    delete = plays[names.index("Delete the VMs on Proxmox")]
    assert delete["vars"]["ansible_connection"] == "local"
    confirm = plays[0]
    start = next(
        task for task in confirm["tasks"] if task["name"] == "Start each stopped VM, to release it before it goes"
    )
    assert start["when"] == "claude_vm_reaching | bool"
    assert "selectattr('status', 'equalto', 'stopped')" in start["loop"]
    # Started for the release, or for the save alone, which destroy_release: false still makes.
    assert confirm["vars"]["claude_vm_reaching"] == (
        "{{ destroy_release | default(true) | bool or destroy_save_sessions | default(true) | bool }}"
    )
    release = plays[names.index("Release each VM from what it signed in to")]
    tasks = [task["name"] for task in release["tasks"]]
    assert tasks.index("Leave the VM signed in, as destroy_release asks") == (
        tasks.index("Save what of the VM's Claude Code sessions is only there") + 1
    )


def test_the_save_comes_before_every_release_and_a_failed_one_keeps_the_vm():
    plays = yaml.safe_load(DESTROY.read_text())
    release = next(play for play in plays if play.get("name") == "Release each VM from what it signed in to")
    names = [task["name"] for task in release["tasks"]]
    at = names.index("Save what of the VM's Claude Code sessions is only there")
    assert at == names.index("Leave a VM that did not answer as it is") + 2
    assert at < names.index("Sign Claude Code out, and stop Remote Control")
    save = release["tasks"][at]
    # The condition on the tasks inside the block, never the block, whose rescue it would also be.
    pause, run = save["block"]
    assert pause["ansible.builtin.include_role"] == {"name": "claude_code", "tasks_from": "release_pause"}
    assert run["ansible.builtin.import_tasks"] == "tasks/session_save.yml" and "when" not in save
    assert pause["when"][0] == run["when"] == "destroy_save_sessions | default(true) | bool"
    # A VM kept: the reason first, while ansible_failed_result is the save's, then Remote Control again.
    keep, resume, end = save["rescue"]
    assert "claude_vm_delete_failure" in keep["ansible.builtin.set_fact"]
    assert resume["ansible.builtin.include_role"] == {"name": "claude_code", "tasks_from": "release_resume"}
    assert end["ansible.builtin.meta"] == "end_host"
    # Reached without sudo for the save. Sudo is asked for before it, in a block: a sudo that fails is
    # an error failed_when never sees, which would take the VM out of the run unsaved and unreported.
    reach = release["tasks"][names.index("Reach the VM")]
    assert reach["become"] is False and release["become"] is True
    sudo = release["tasks"][names.index("Ask whether sudo works on the VM")]
    assert names.index("Ask whether sudo works on the VM") == at - 1
    assert [_action(task) for task in sudo["block"]] == ["ansible.builtin.ping"] and "become" not in sudo["block"][0]
    assert "claude_vm_release_blocked" in sudo["rescue"][0]["ansible.builtin.set_fact"]
    assert pause["when"][1] == "claude_vm_release_blocked is not defined"
    end = release["tasks"][names.index("Leave a VM whose sudo failed unreleased")]
    assert end["when"] == "claude_vm_release_blocked is defined"
    assert (
        at
        < names.index("Leave a VM whose sudo failed unreleased")
        < names.index("Sign Claude Code out, and stop Remote Control")
    )
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert defaults["destroy_save_sessions"] is True and defaults["destroy_sessions_dir"] == ""


# Every task session_save.yml may hold, by action: where it runs, and how.
SAVE_ON_VM = {"ansible.builtin.getent", "claude_sessions_info"}
SAVE_HERE = {"claude_sessions_fetch", "ansible.builtin.copy"}
SAVE_ANYWHERE = {"ansible.builtin.set_fact", "ansible.builtin.assert"}


def test_the_save_reads_the_vm_under_no_log_and_writes_only_here_as_this_user(tmp_path):
    tasks = yaml.safe_load((REPO / "playbooks" / "tasks" / "session_save.yml").read_text())
    for at, task in enumerate(tasks):
        (action,) = set(task) - {
            "name",
            "vars",
            "register",
            "when",
            "delegate_to",
            "become",
            "no_log",
            "ignore_unreachable",
        }
        assert action in SAVE_ON_VM | SAVE_HERE | SAVE_ANYWHERE, (task["name"], action)
        if action in SAVE_ON_VM:
            # As the VM user, never sudo; and one that stops answering fails into destroy's rescue, never
            # out of the run unseen, which an unreachable host does.
            assert "delegate_to" not in task and task["become"] is False, task["name"]
            assert task["ignore_unreachable"] is True, task["name"]
            after = tasks[at + 1]
            assert (
                "ansible.builtin.assert" in after
                and f"{task['register']}.unreachable" in after["ansible.builtin.assert"]["that"]
            )
        if action == "claude_sessions_info":
            assert task["no_log"] is True, task["name"]
        if action in SAVE_HERE:
            assert task["delegate_to"] == "localhost" and task["become"] is False, task["name"]
    # By default into the checkout's .sessions/, which git ignores and the tracked-file guard refuses;
    # in a test, always into its own directory.
    settle = next(task for task in tasks if task["name"] == "session_save | Settle where on each machine")
    assert "(playbook_dir | dirname) ~ '/.sessions'" in settle["ansible.builtin.set_fact"]["claude_vm_save_dir"]
    assert ".sessions/" in (REPO / ".gitignore").read_text().splitlines()
    tail = yaml.safe_load(_destroy_tail(tmp_path).read_text())
    assert tail[0]["vars"]["destroy_sessions_dir"] == str(tmp_path / "saved")
    # The scenario that runs destroy.yml, into Molecule's ephemeral directory.
    provision = yaml.safe_load((REPO / "molecule" / "provision" / "group_vars" / "all" / "fake_api.yml").read_text())
    assert provision["destroy_sessions_dir"].startswith(
        "{{ lookup('ansible.builtin.env', 'MOLECULE_EPHEMERAL_DIRECTORY') }}/"
    )


def test_a_vm_started_for_its_release_is_stopped_again_when_its_deletion_fails():
    plays = yaml.safe_load(DESTROY.read_text())
    tasks = [task for play in plays for task in _flat(play.get("tasks", []))]
    remember = next(task for task in tasks if task["name"] == "Remember which VMs this run started")
    assert "claude_vm_release_started" in remember["ansible.builtin.set_fact"]
    again = next(task for task in tasks if task["name"] == "Stop again each VM this run started but could not delete")
    assert again["ansible.builtin.import_tasks"] == "tasks/vm_stop.yml"
    assert "rejectattr('name', 'in', claude_vm_gone)" in again["vars"]["claude_vm_stop_query"]
    assert "selectattr('claude_vm_deleted', 'defined')" in again["vars"]["claude_vm_gone"]
    stop = yaml.safe_load((REPO / "playbooks" / "tasks" / "vm_stop.yml").read_text())[0]
    assert stop["ansible.builtin.uri"]["url"].endswith("/status/stop") and stop["no_log"] is True
    # After the deletion, and first in its play: nothing there that fails
    # before it can keep a VM running.
    names = [play.get("name") for play in plays]
    holder = next(play for play in plays if again in play.get("tasks", []))
    assert names.index(holder["name"]) > names.index("Delete the VMs on Proxmox")
    assert holder["tasks"][0] is again


def test_a_deletion_that_fails_is_raised_only_after_the_stop_and_the_report():
    # A play in which every host fails ends the run: a deletion raised there,
    # for the one VM asked for, left that VM running and said nothing of it.
    plays = yaml.safe_load(DESTROY.read_text())
    delete = next(play for play in plays if play.get("name") == "Delete the VMs on Proxmox")
    assert "roles" not in delete and "post_tasks" not in delete
    # A VM the release play kept, its sessions unsaved, ends here; every other is deleted.
    kept, step = delete["tasks"]
    assert kept["ansible.builtin.meta"] == "end_host" and kept["when"] == "claude_vm_delete_failure is defined"
    assert step["block"][0]["ansible.builtin.import_role"] == {"name": "proxmox_vm"}
    assert step["block"][0]["vars"] == {"proxmox_vm_state": "absent"}
    assert step["block"][-1]["ansible.builtin.set_fact"] == {"claude_vm_deleted": True}
    assert "claude_vm_delete_failure" in step["rescue"][0]["ansible.builtin.set_fact"]
    last = plays[-1]["tasks"][-1]
    assert last["name"] == "Fail for each VM that was not deleted"
    assert "claude_vm_delete_failure" in last["vars"]["claude_vm_not_deleted"]
    # The release play leaves a VM it cannot use as it is, failing it never.
    release = next(play for play in plays if play.get("name") == "Release each VM from what it signed in to")
    reach = next(task for task in release["tasks"] if task["name"] == "Reach the VM")
    assert reach["failed_when"] is False and reach["ignore_unreachable"] is True


def test_a_run_that_fails_before_deleting_stops_again_what_it_started():
    plays = yaml.safe_load(DESTROY.read_text())
    tasks = plays[0]["tasks"]
    names = [task["name"] for task in tasks]
    guarded = tasks[names.index("Read where each VM is now, and add each as a host")]
    assert names.index("Remember which VMs this run started") < names.index(guarded["name"])
    inside = [task["name"] for task in guarded["block"]]
    assert inside == [
        "Read the configuration of each VM started",
        "Wait for each started VM's guest agent and its NIC's address",
        "List the VMs again, with the ones just started",
        "Read the address of each VM to release",
        "Add each VM as a host to delete",
    ]
    # Straight after the start: nothing between them that could fail.
    assert names.index("Remember which VMs this run started") == (
        names.index("Start each stopped VM, to release it before it goes") + 1
    )
    stop, fail = guarded["rescue"]
    assert stop["ansible.builtin.import_tasks"] == "tasks/vm_stop.yml"
    assert stop["vars"]["claude_vm_stop_query"] == "{{ claude_vm_release_started }}"
    assert "ansible.builtin.fail" in fail


def test_the_wait_for_a_started_vm_is_for_the_address_of_its_own_nic():
    # Any address would do before: a bridge the guest grew (docker0,
    # tailscale0) could end the wait before DHCP, and vm_addresses.yml, which
    # pairs by the NIC's MAC, then found none and the VM went unreleased.
    tasks = _flat(yaml.safe_load(DESTROY.read_text())[0]["tasks"])
    wait = next(
        task for task in tasks if task["name"] == "Wait for each started VM's guest agent and its NIC's address"
    )
    assert "guest_ipv4(claude_vm_release_mac)" in wait["until"]
    assert "get(proxmox_nic, '')" in wait["vars"]["claude_vm_release_mac"]
    assert "net_mac('')" in wait["vars"]["claude_vm_release_mac"]
    assert wait["no_log"] is True and wait["failed_when"] is False
    # Three minutes for a VM to boot, asked every five seconds; a test asks faster, with -e.
    assert wait["retries"] == 36 and wait["delay"] == "{{ claude_vm_release_agent_delay }}"
    assert yaml.safe_load(DESTROY.read_text())[0]["vars"]["claude_vm_release_agent_delay"] == 5


def test_destroy_refuses_a_duplicated_name_before_it_starts_anything():
    # The release finds a VM by its name: of two, one could be a VM that is
    # not this project's, started and reached over SSH.
    names = [task["name"] for task in yaml.safe_load(DESTROY.read_text())[0]["tasks"]]
    assert names.index("Refuse a name that more than one VM carries") < names.index(
        "Start each stopped VM, to release it before it goes"
    )


def test_destroy_lists_the_cluster_again_whatever_it_started():
    # A skipped task still overwrites its register: listing again only when a
    # VM was started left claude_vm_all without its listing whenever none was,
    # and destroy failed before it began.
    plays = yaml.safe_load(DESTROY.read_text())
    again = next(
        task for task in _flat(plays[0]["tasks"]) if task["name"] == "List the VMs again, with the ones just started"
    )
    assert "when" not in again


def test_no_play_sets_how_to_reach_a_host_in_its_vars():
    # A play's vars apply to every task it delegates, so SSH settings there
    # send a task meant for this machine - and whatever it carries - to the
    # VM. Hosts get them from add_host or the inventory; a play may only force
    # the local connection, which is this machine's anyway.
    reaching = {
        "ansible_host",
        "ansible_user",
        "ansible_port",
        "ansible_ssh_private_key_file",
        "ansible_ssh_common_args",
    }
    for path in [REPO / "deploy.yml", *sorted((REPO / "playbooks").glob("*.yml"))]:
        for play in yaml.safe_load(path.read_text()) or []:
            play_vars = play.get("vars") or {}
            assert not reaching & set(play_vars), (path.name, play.get("name"), sorted(reaching & set(play_vars)))
            assert play_vars.get("ansible_connection", "local") == "local", (path.name, play.get("name"))


@pytest.mark.parametrize("extra", [{}, {"vm_name": "alpha"}, {"vm_name": "alpha, beta,"}])
def test_with_no_terminal_destroy_takes_no_for_an_answer_whatever_names_it_is_given(tmp_path, extra):
    # The prompt is templated before any host is in scope, even when -e
    # answers it: one naming an inventory variable failed every run here,
    # where the harness has none, before anything was asked.
    report = _run(tmp_path, DESTROY, {}, "-e", json.dumps(extra))
    assert report["rc"] != 0
    assert list(_failures(report).values()) == ["Abort unless confirmed: Destroy not confirmed."], report


def test_destroy_asks_under_a_block_that_names_the_vms_and_never_logs_it(tmp_path):
    # A vars_prompt never puts the terminal in raw mode: its answer is typed as soon as it asks.
    steps = [(b"Type yes to continue", b"no\n", 0, "canonical")]
    rc, output, log = _run_at_a_terminal(tmp_path, DESTROY, {}, {"vm_name": "alpha,beta"}, steps=steps)
    assert rc != 0
    block = "##  CONFIRM DELETION\r\n" + "#" * 80 + "\r\nThis deletes alpha, beta and their disks.\r\n"
    assert block in output, output
    assert "Type yes to continue [no]: " in output
    assert "Destroy not confirmed." in output
    # vars_prompt reads with input(): neither its question nor the answer reach the run log.
    assert "This deletes" not in log


def test_destroy_release_defaults_to_on():
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert defaults["destroy_release"] is True
    # A credential in the vault is enough: nothing else to set, in either defaults file.
    role = yaml.safe_load((REPO / "roles" / "tailscale" / "defaults" / "main.yml").read_text())
    assert defaults["tailscale_api_auth_method"] == role["tailscale_api_auth_method"] == "auto"


def test_every_api_call_is_made_on_this_machine_and_never_logged():
    api = yaml.safe_load((REPO / "roles" / "tailscale" / "tasks" / "api_delete.yml").read_text())
    for task in api:
        assert task["delegate_to"] == "localhost" and task["become"] is False, task["name"]
        assert task["no_log"] is True, task["name"]
    # GitHub's release calls no API at all: it signs gh out, and says the rest.
    github = yaml.safe_load((REPO / "roles" / "github_projects" / "tasks" / "release.yml").read_text())
    assert not [task["name"] for task in github if "ansible.builtin.uri" in task]


# ------------------------------------- destroy.yml against the fake Proxmox ---
PARKED = 9001


@pytest.fixture
def fake_pve(tmp_path):
    """The fake Proxmox, holding one stopped VM of this project's, its listing lagging a start by one listing."""
    cert, key = _certificate(tmp_path)
    for base in _serve(FAKES / "fake_pve_api.py", str(cert), str(key), str(tmp_path / "pve"), PVE_SECRET):
        base = base.replace("http://", "https://")
        _pve(base, "POST", "nodes/pve/qemu/9000/clone", newid=PARKED, name="parked")
        _pve(base, "PUT", f"nodes/pve/qemu/{PARKED}/config", tags="claude-on-proxmox")
        _pve(base, "PUT", "fake/listing-lag", listings=1)
        yield base


def _head(tmp_path, fake_pve):
    """A directory to run destroy.yml's first play from against the fake, and that play with the addresses after it."""
    root = tmp_path / "run"
    (root / "group_vars" / "all").mkdir(parents=True)
    (root / "group_vars" / "all" / "defaults.yml").symlink_to(
        REPO / "inventory" / "group_vars" / "all" / "defaults.yml"
    )
    port = int(fake_pve.rsplit(":", 1)[1])
    (root / "group_vars" / "all" / "fake_api.yml").write_text(
        yaml.safe_dump(
            {
                "proxmox_api_host": "127.0.0.1",
                "proxmox_api_port": port,
                "vault_proxmox_api_token_secret": PVE_SECRET,
                "vm_user": "dev",
            }
        )
    )
    plays = yaml.safe_load(DESTROY.read_text())
    # Its task files where they are: the play runs from a copy here.
    confirm = json.loads(json.dumps(plays[0]).replace('"tasks/', f'"{REPO / "playbooks" / "tasks"}/'))
    playbook = root / "destroy_head.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                confirm,
                {
                    "name": "Say where each VM would be released",
                    "hosts": "claude_vm_doomed",
                    "gather_facts": False,
                    "tasks": [{"name": "Address", "ansible.builtin.debug": {"var": "claude_vm_release_address"}}],
                },
            ]
        )
    )
    return root, playbook


def test_a_vm_destroy_starts_is_released_though_the_listing_still_says_it_is_stopped(tmp_path, fake_pve):
    # Proxmox lists the status it last polled, up to ten seconds old. A VM that
    # booted within those seconds of destroy's start was listed as stopped
    # after it, not asked for its address, and deleted without its release.
    root, playbook = _head(tmp_path, fake_pve)
    # The fake's agent answers on its third ask: asked every fifth of a second
    # rather than every five, as a VM booting is.
    extra = {"vm_name": "parked", "confirm_destroy": "yes", "claude_vm_release_agent_delay": 0.2}
    report = _run(root, playbook, {}, "-e", json.dumps(extra), timeout=120)
    assert report["rc"] == 0, _failures(report)
    said = {
        host: result.get("claude_vm_release_address") for name, host, result in _results_of(report) if name == "Address"
    }
    assert said == {"parked": f"192.0.2.{PARKED % 200 + 50}"}
    # The listing did lag: the run's own relisting still showed it stopped.
    calls = [json.loads(line) for line in (tmp_path / "pve" / "calls.log").read_text().splitlines()]
    assert [c["path"] for c in calls].count("/api2/json/cluster/resources") >= 2


@pytest.mark.parametrize("folder", ["saved-sessions", "./saved"])
def test_a_relative_folder_for_the_saved_sessions_is_refused_before_anything_starts(tmp_path, folder):
    # The module that writes them ran in playbooks/, where such a folder is not git-ignored. Refused
    # before Proxmox is asked anything: nothing listens at the API's address.
    root, playbook = _head(tmp_path, "https://127.0.0.1:9")
    extra = {"vm_name": "parked", "confirm_destroy": "yes", "destroy_sessions_dir": folder}
    report = _run(root, playbook, {}, "-e", json.dumps(extra), timeout=120)
    assert report["rc"] != 0
    (failure,) = _failures(report).values()
    assert failure.startswith(
        "Refuse a folder for the saved sessions that is not an absolute path: destroy_sessions_dir"
    )
    assert not [name for name, _, _ in _results_of(report) if name.startswith("cluster_vms |")]
