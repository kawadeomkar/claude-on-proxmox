"""Releasing a VM before make destroy deletes it: destroy.yml's release play and report, run for real.

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
- destroy starts a stopped VM to release it, only with destroy_release, and
  releases before it deletes - finding the address of a VM it started though
  the cluster listing, seconds behind, still says it is stopped.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml
from harness import REPO, STAND_INS, _failures, _run

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
SECRETS = [DEVICE_TOKEN, VAULT_TOKEN, HAND_TOKEN, API_TOKEN, FORBIDDEN_TOKEN, CLIENT_SECRET, "tskey-api-oauth-0000"]

GETENT = """#!/bin/sh
[ "$1" = passwd ] || { echo "stand-in getent: unexpected database $1" >&2; exit 3; }
printf '%s:x:1000:1000:%s:%s:/bin/bash\\n' "$2" "$2" "$HOME"
"""

# One VM per way a release can go.
CASES = {
    # Everything this project signs a VM in to, and an API access token.
    "everything": {"claude": True, "gh": DEVICE_TOKEN, "device_marker": True, "node": "nEVERYTHING", "api": "token"},
    # The vault's token in gh; Tailscale with no API credential.
    "vault-token": {"gh": VAULT_TOKEN, "vault": True, "node": "nVAULT", "api": "none"},
    # A token pasted by hand; Tailscale through an OAuth client.
    "by-hand": {"gh": HAND_TOKEN, "node": "nBYHAND", "api": "oauth"},
    # A credential Tailscale refuses for this device.
    "refused": {"node": "nREFUSED", "api": "forbidden"},
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
    # No address: stopped and would not start, or an agent that never answered.
    "unreached": {"address": ""},
}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _serve(script: Path, *args: str):
    """A fake API on a loopback port, until the fixture ends; yields its base URL."""
    port = _free_port()
    command = [sys.executable, str(script), str(port), *args]
    server = subprocess.Popen(command, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            if server.poll() is not None:
                pytest.fail(f"{script.name} exited: {server.stderr.read()}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail(f"{script.name} did not start")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.terminate()
        server.wait(timeout=10)


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
        (home / "python").symlink_to(bin_dir / "python")
        api = case.get("api", "none")
        hosts[name] = {
            "ansible_connection": "local",
            "ansible_python_interpreter": str(home / "python"),
            "claude_vm_release_address": case.get("address", "192.0.2.10"),
            "github_projects_home": str(home),
            "github_projects_api_url": github,
            "github_projects_token": VAULT_TOKEN if case.get("vault") else "",
            "tailscale_api_url": tailscale,
            "tailscale_api_auth_method": {"forbidden": "token"}.get(api, api),
            "tailscale_api_token": {"token": API_TOKEN, "forbidden": FORBIDDEN_TOKEN}.get(api, ""),
            "tailscale_oauth_client_id": "kOAUTHCLIENT" if api == "oauth" else "",
            "tailscale_oauth_client_secret": CLIENT_SECRET if api == "oauth" else "",
            # Every module's arguments in its result, so that a credential handed
            # to a task without no_log would reach the report searched below.
            "ansible_inject_invocation": True,
        }
    return hosts


def _destroy_tail(root: Path) -> Path:
    """destroy.yml's release play, the deletion marked as done, and its report: as destroy.yml has them."""
    plays = yaml.safe_load(DESTROY.read_text())
    release = next(play for play in plays if play.get("name") == "Release each VM from what it signed in to")
    report = next(task for play in plays for task in play.get("tasks", []) if task.get("name") == REPORT)
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
                    "tasks": [{"ansible.builtin.set_fact": {"claude_vm_deleted": True}}],
                },
                {"name": "Report", "hosts": "localhost", "gather_facts": False, "tasks": [on_these]},
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


def test_the_vault_token_is_never_taken_for_this_project_s_sign_in_whatever_the_vm_s_marker_says(released):
    _, report, _, _ = released
    assert "GitHub: gh signed out (vault_github_token is every VM's" in _said(report)["marked-vault"]


def test_a_vm_with_nothing_signed_in_is_said_to_have_nothing_to_end(released):
    _, report, _, _ = released
    assert _said(report)["nothing"] == "nothing: released - it had nothing signed in to end."


def test_a_vm_that_could_not_be_reached_is_left_as_it_is_and_what_is_left_named(released):
    root, report, _, _ = released
    assert _calls(root, "unreached", ".tailscale-fake", "calls.log") == []
    assert _said(report)["unreached"] == (
        "unreached: could not be reached, so it was not released: its Tailscale device, if it was on your tailnet,"
        f" stays until you delete it at {ADMIN} (or a VM built again under its name joins as <name>-1); a GitHub token"
        " its sign-in made stays valid until revoked at https://github.com/settings/applications, under GitHub CLI"
        " (which signs out every gh); and Anthropic has no way to revoke its Claude Code login from outside."
    )


def test_no_token_key_or_secret_reaches_the_output(released):
    _, report, _, _ = released
    text = json.dumps(report)
    assert [secret for secret in SECRETS if secret in text] == []


# ------------------------------------------------------------- destroy.yml ---
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
    assert start["when"] == "claude_vm_releasing | bool"
    assert "selectattr('status', 'equalto', 'stopped')" in start["loop"]
    assert confirm["vars"]["claude_vm_releasing"] == "{{ destroy_release | default(true) | bool }}"


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
    (step,) = delete["tasks"]
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


def test_destroy_release_defaults_to_on():
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert defaults["destroy_release"] is True
    assert defaults["tailscale_api_auth_method"] == "none"


def test_every_api_call_is_made_on_this_machine_and_never_logged():
    api = yaml.safe_load((REPO / "roles" / "tailscale" / "tasks" / "api_delete.yml").read_text())
    for task in api:
        assert task["delegate_to"] == "localhost" and task["become"] is False, task["name"]
        assert task["no_log"] is True, task["name"]
    # GitHub's release calls no API at all: it signs gh out, and says the rest.
    github = yaml.safe_load((REPO / "roles" / "github_projects" / "tasks" / "release.yml").read_text())
    assert not [task["name"] for task in github if "ansible.builtin.uri" in task]


# ------------------------------------- destroy.yml against the fake Proxmox ---
PVE_SECRET = "unit-test-pve-secret"
PARKED = 9001


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


def _pve(base: str, method: str, path: str, **params) -> dict:
    """One request to the fake Proxmox, as a fixture makes it."""
    request = urllib.request.Request(
        f"{base}/api2/json/{path}",
        data=urllib.parse.urlencode(params).encode() if params else None,
        method=method,
        headers={"Authorization": f"PVEAPIToken=ansible@pve!ansible={PVE_SECRET}"},
    )
    unverified = ssl.create_default_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(request, timeout=10, context=unverified) as answer:
        return json.loads(answer.read() or b"{}")


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


def test_a_vm_destroy_starts_is_released_though_the_listing_still_says_it_is_stopped(tmp_path, fake_pve):
    # Proxmox lists the status it last polled, up to ten seconds old. A VM that
    # booted within those seconds of destroy's start was listed as stopped
    # after it, not asked for its address, and deleted without its release.
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
    report = _run(root, playbook, {}, "-e", json.dumps({"vm_name": "parked", "confirm_destroy": "yes"}), timeout=120)
    assert report["rc"] == 0, _failures(report)
    said = {
        host: result.get("claude_vm_release_address") for name, host, result in _results_of(report) if name == "Address"
    }
    assert said == {"parked": f"192.0.2.{PARKED % 200 + 50}"}
    # The listing did lag: the run's own relisting still showed it stopped.
    calls = [json.loads(line) for line in (tmp_path / "pve" / "calls.log").read_text().splitlines()]
    assert [c["path"] for c in calls].count("/api2/json/cluster/resources") >= 2
