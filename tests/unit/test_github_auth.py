"""The github_projects role's GitHub sign-in and private-repository listing, run for real against stand-ins.

Nothing here reaches GitHub, a VM or a real gh. Every inventory host is the
controller itself, over the local connection, with an interpreter wrapper
that puts tests/molecule/fake_gh.sh first on the module's PATH as `gh` and
makes the host's own directory its HOME, so gh's login, ~/.gitconfig and the
clones all land there. The listing goes to tests/molecule/fake_github_api.py
on a loopback port, serving bare repositories made here over file://. The
role's first tasks - the VM user's home and the projects directory - are
skipped by running auth.yml and clone.yml with both given, and
`become` is off: the controller has no sudo to give, and nothing under test
depends on the user a task runs as.

What these guard:

- Without a token in the vault, private repositories were not even listed:
  the public endpoint was used. The listing now uses the token gh stored at
  a login the user made, so the run after `make github-login` reaches them.
- A vault token signs gh in once, again when it changes, and is signed out
  when cleared - but only the login the role itself made, told by a marker
  holding the token's digest: a PAT the user pasted by hand, a device-flow
  login (gho_), a GH_TOKEN in the environment and a login that replaced the
  role's own all stay. gh's status JSON carries no token, so the kind of
  login is read from the token gh prints, under no_log.
- A gh too old for `gh auth status --json` (before 2.81.0) is a named
  failure, not "signed out" forever.
- gh becomes git's credential helper once, by gh's own test for its entry.
- A private repository that is listed while gh is signed out (github_auth
  off) is skipped and named, not cloned and failed.
- The identity in ~/.gitconfig is the account's noreply address unless set.
- A token GitHub refuses fails the host with a reason, and no task ever
  prints a token: the whole report is searched for every canned one.
- `make github-login` says how a VM is signed in, and the command otherwise,
  and looks the user's home up itself: run without one, as the playbook is.
- Given one VM, `make github-login` runs GitHub's device flow itself: gh in
  the background with no terminal, the code read from its output and shown,
  the approval waited for, then the whole role. An expired code is a named
  failure. The browser and clipboard steps never run in a test: every
  scenario and this harness switch them off, and a test here keeps it so.
"""

from __future__ import annotations

import getpass
import grp
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from test_remote_control_playbooks import STAND_INS, _failures, _messages, _results, _run

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "roles" / "github_projects"
FAKE_API = REPO / "tests" / "molecule" / "fake_github_api.py"
FAKE_GH = REPO / "tests" / "molecule" / "fake_gh.sh"
USERNAME = "molecule"
REPOS = ["pub-one", "pub-two", "secret:private", "fresh:empty"]
PUBLIC = {"pub-one", "pub-two"}
ALL = PUBLIC | {"secret"}
TOKENS = {
    "vault": "ghp_vault00000000",
    "old": "ghp_old0000000000",
    "oauth": "gho_user000000000",
    "env": "ghp_env0000000000",
}
DEVICE_LOGIN = "auth login --hostname github.com --git-protocol https --web --skip-ssh-key --scopes workflow"
NOREPLY = f"4242+{USERNAME}@users.noreply.github.com"

# One host per state the role can meet: the vault token, what gh holds
# already (and where it says it came from), and the switches.
CASES = {
    "signed-out-no-token": {"token": ""},
    "token-signed-out": {"token": TOKENS["vault"]},
    "token-changed": {"token": TOKENS["vault"], "stored": TOKENS["old"], "marker": TOKENS["old"]},
    # A PAT the user pasted by hand: no marker, so it is theirs and stays.
    "token-cleared-pat": {"token": "", "stored": TOKENS["old"]},
    # The login the role made, its vault token since cleared.
    "token-cleared-ours": {"token": "", "stored": TOKENS["old"], "marker": TOKENS["old"]},
    # The role's login replaced by the user's device-flow one: a stale marker.
    "token-cleared-stale-marker": {"token": "", "stored": TOKENS["oauth"], "marker": TOKENS["old"]},
    "token-cleared-oauth": {"token": "", "stored": TOKENS["oauth"]},
    "env-login": {"token": "", "stored": TOKENS["env"], "source": "GH_TOKEN"},
    "auth-off-with-token": {"token": TOKENS["vault"], "github_projects_auth": False},
    "identity-set": {
        "token": TOKENS["vault"],
        "github_projects_git_user_name": "Dev Eloper",
        "github_projects_git_user_email": "dev@example.com",
    },
    "refused-token": {"token": "bad-token"},
    "old-gh": {"token": "", "old": True},
}
EXPECTED_CLONES = {
    "signed-out-no-token": PUBLIC,
    "token-signed-out": ALL,
    "token-changed": ALL,
    "token-cleared-pat": ALL,
    "token-cleared-ours": PUBLIC,
    "token-cleared-stale-marker": ALL,
    "token-cleared-oauth": ALL,
    "env-login": ALL,
    "auth-off-with-token": PUBLIC,
    "identity-set": ALL,
    "refused-token": set(),
    "old-gh": set(),
}
SIGNED_IN_AFTER = {
    "token-signed-out",
    "token-changed",
    "token-cleared-pat",
    "token-cleared-stale-marker",
    "token-cleared-oauth",
    "env-login",
    "identity-set",
}
FAILING = {"refused-token", "old-gh"}
LOGOUT = f"auth logout --hostname github.com --user {USERNAME}"


def _digest(token: str) -> str:
    """What the role's marker holds for a token: Ansible's hash('sha256') of it."""
    return hashlib.sha256(token.encode()).hexdigest()


def _marker(root: Path, host: str) -> Path:
    return root / host / ".config" / "claude-on-proxmox" / "gh-login"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _bare_repos(git_root: Path) -> None:
    """One bare repository per listed name, with a commit on main, as the Molecule prepare makes them."""
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "molecule",
        "GIT_AUTHOR_EMAIL": "molecule@example.com",
        "GIT_COMMITTER_NAME": "molecule",
        "GIT_COMMITTER_EMAIL": "molecule@example.com",
    }
    for spec in REPOS:
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


@pytest.fixture(scope="module")
def fake_github(tmp_path_factory):
    """The fake GitHub API on a loopback port, listing bare repositories made here; yields its base URL."""
    git_root = tmp_path_factory.mktemp("git")
    _bare_repos(git_root)
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, str(FAKE_API), str(port), USERNAME, str(git_root), *REPOS],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            if server.poll() is not None:
                pytest.fail(f"fake GitHub API exited: {server.stderr.read()}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("fake GitHub API did not start")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.terminate()
        server.wait(timeout=10)


# What `getent passwd <user>` answers on the stand-in hosts: the user's home is
# the host's own directory, as HOME already is for every module. macOS has no
# getent, and the role's entry points look the home up with it.
GETENT = """#!/bin/sh
[ "$1" = passwd ] || { echo "stand-in getent: unexpected database $1" >&2; exit 3; }
printf '%s:x:1000:1000:%s:%s:/bin/bash\\n' "$2" "$2" "$HOME"
"""


def _hosts(root: Path, cases: dict, api_url: str, *, home_given: bool = True) -> dict:
    """Inventory hosts, one per case, whose modules run with the stand-in gh and their own HOME; returns hostvars.

    With home_given, each host is told its home the way main.yml's clone would
    have been; without, the playbook under test has to look it up, as it must
    on a VM, and the stand-in getent answers.
    """
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").write_text(STAND_INS["python"])
    (bin_dir / "python").chmod(0o755)
    (bin_dir / "gh").write_text(FAKE_GH.read_text())
    (bin_dir / "gh").chmod(0o755)
    (bin_dir / "getent").write_text(GETENT)
    (bin_dir / "getent").chmod(0o755)
    hosts = {}
    for name, case in cases.items():
        home = root / name
        (home / "projects").mkdir(parents=True)
        state = home / ".config" / "gh"
        state.mkdir(parents=True)
        if case.get("stored"):
            (state / "fake-token").write_text(case["stored"] + "\n")
        if case.get("source"):
            (state / "fake-source").write_text(case["source"])
        if case.get("old"):
            (state / "fake-old").touch()
        if case.get("expire"):
            (state / "fake-expire").touch()
        if case.get("approve_after"):
            (state / "fake-approve-after").write_text(str(case["approve_after"]))
        if case.get("stale"):
            # What a run that failed after starting gh leaves behind: gh's
            # output up to the old code (the gh still polling is STALE_GH).
            out = home / ".config" / "claude-on-proxmox" / "gh-login.out"
            out.parent.mkdir(parents=True)
            out.write_text(
                "! First copy your one-time code: 0LDC-0DE1\n"
                "Open this URL to continue in your web browser: https://github.com/login/device\n"
            )
        if case.get("marker"):
            marker = home / ".config" / "claude-on-proxmox" / "gh-login"
            marker.parent.mkdir(parents=True)
            marker.write_text(_digest(case["marker"]) + "\n")
        (home / "python").symlink_to(bin_dir / "python")
        hosts[name] = {
            "ansible_connection": "local",
            "ansible_python_interpreter": str(home / "python"),
            "ansible_host": "192.0.2.10",
            "github_projects_username": USERNAME,
            "github_projects_token": case["token"],
            "github_projects_api_url": api_url,
            "github_projects_target_dir": str(home / "projects"),
            # Never the developer's browser or clipboard: the guard test
            # below holds every scenario to the same.
            "github_projects_login_desktop": False,
            # The async job directory of the device flow: the wrapper on the
            # "VM" writes it under the module's HOME (this host's directory),
            # while async_status resolves "~" through the connection's shell,
            # which here is the developer's real home. On a VM the two agree;
            # here the shell plugin is told the directory outright.
            "ansible_async_dir": str(home / ".ansible_async"),
            **({"github_projects_home": str(home)} if home_given else {}),
            **{key: value for key, value in case.items() if key.startswith("github_projects_")},
        }
    return hosts


def _role_run(root: Path, hosts: dict, *args: str) -> dict:
    """auth.yml then clone.yml of the role, as main.yml runs them once the directory is known."""
    playbook = root / "role.yml"
    playbook.write_text(
        json.dumps(
            [
                {
                    "name": "Sign in and clone",
                    "hosts": "claude_vms",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "name": f"Run the role's {tasks_from} tasks",
                            "ansible.builtin.include_role": {"name": "github_projects", "tasks_from": tasks_from},
                        }
                        for tasks_from in ("auth", "clone")
                    ],
                }
            ]
        )
    )
    return _run(root, playbook, hosts, "-e", '{"ansible_become": false}', *args)


def _calls(root: Path, host: str) -> list[str]:
    log = root / host / ".config" / "gh" / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def _git_config(root: Path, host: str, key: str) -> list[str]:
    result = subprocess.run(
        ["git", "config", "--file", str(root / host / ".gitconfig"), "--get-all", key],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.splitlines()


@pytest.fixture(scope="module")
def configured(tmp_path_factory, fake_github):
    """The role run once for every case, then again for every case but the refused token: root, both reports."""
    root = tmp_path_factory.mktemp("github_auth")
    hosts = _hosts(root, CASES, fake_github)
    first = _role_run(root, hosts)
    second = _role_run(root, hosts, "--limit", "!" + ":!".join(sorted(FAILING)))
    return root, first, second


def test_only_a_refused_token_and_an_old_gh_fail_and_say_why(configured):
    _, first, _ = configured
    failures = _failures(first)
    assert set(failures) == FAILING, failures
    assert "GitHub refused the token" in failures["refused-token"]
    assert "Bad credentials" in failures["refused-token"]
    assert "make github-login" in failures["refused-token"]
    # Not "signed out", which would print the sign-in command on every run
    # and never configure the helper: the flag the role needs, by version.
    assert "needs gh 2.81.0 or newer" in failures["old-gh"]
    assert "unknown flag: --json" in failures["old-gh"]
    assert "dev_tools_install_gh" in failures["old-gh"]


@pytest.mark.parametrize("host", sorted(CASES))
def test_what_is_cloned_follows_the_credential(configured, host):
    root, _, _ = configured
    cloned = {path.name for path in (root / host / "projects").iterdir() if path.is_dir()}
    assert cloned == EXPECTED_CLONES[host]


def test_a_vault_token_signs_gh_in_once_and_again_only_when_it_changes(configured):
    root, _, _ = configured
    login = "auth login --hostname github.com --git-protocol https --with-token --insecure-storage"
    for host in CASES:
        logins = [call for call in _calls(root, host) if call == login]
        expected = 1 if host in {"token-signed-out", "token-changed", "identity-set", "refused-token"} else 0
        assert len(logins) == expected, (host, _calls(root, host))
    for host in ("token-signed-out", "token-changed", "identity-set"):
        assert (root / host / ".config" / "gh" / "fake-token").read_text().strip() == TOKENS["vault"]


def test_a_cleared_token_signs_out_only_the_login_the_role_made(configured):
    root, _, _ = configured
    # Ours, by the marker: signed out, by account, and the marker goes with it.
    assert _calls(root, "token-cleared-ours").count(LOGOUT) == 1
    assert not (root / "token-cleared-ours" / ".config" / "gh" / "fake-token").exists()
    assert not _marker(root, "token-cleared-ours").exists()
    # The user's own, whatever the token looks like: a PAT pasted by hand (no
    # marker), a device-flow login, one from the environment, and one that
    # replaced the role's login (a marker that no longer matches). Each stays,
    # and the stale marker is left where it was.
    for host, token in (
        ("token-cleared-pat", TOKENS["old"]),
        ("token-cleared-oauth", TOKENS["oauth"]),
        ("env-login", TOKENS["env"]),
        ("token-cleared-stale-marker", TOKENS["oauth"]),
    ):
        assert not any(call.startswith("auth logout") for call in _calls(root, host)), host
        assert (root / host / ".config" / "gh" / "fake-token").read_text().strip() == token
    assert _marker(root, "token-cleared-stale-marker").read_text().strip() == _digest(TOKENS["old"])


def test_the_role_marks_the_login_it_made_with_the_token_s_digest(configured):
    root, _, _ = configured
    for host in ("token-signed-out", "token-changed", "identity-set"):
        assert _marker(root, host).read_text().strip() == _digest(TOKENS["vault"]), host
        assert oct(_marker(root, host).stat().st_mode & 0o777) == "0o600"
    for host in ("signed-out-no-token", "token-cleared-pat", "token-cleared-oauth", "env-login", "auth-off-with-token"):
        assert not _marker(root, host).exists(), host
    # The digest is not the token, and the token is nowhere in the marker.
    text = _marker(root, "token-signed-out").read_text()
    assert TOKENS["vault"] not in text


def test_a_login_the_user_made_lists_the_private_repositories(configured):
    # The listing token is what gh holds when the vault has none: the fake
    # answers /user/repos, private included, only to a token it knows.
    _, first, _ = configured
    reports = _messages(first, "Report selection")
    for host in ("token-cleared-oauth", "env-login", "token-cleared-pat", "token-cleared-stale-marker"):
        assert reports[host].startswith("3 of 4 repositories"), reports[host]
    assert reports["signed-out-no-token"].startswith("2 of 3 repositories")
    assert "Private repositories are not listed until gh is signed in." in reports["signed-out-no-token"]
    assert "Private repositories are not listed" not in reports["token-cleared-oauth"]


def test_a_private_repository_listed_while_signed_out_is_skipped_and_named(configured):
    _, first, _ = configured
    report = _messages(first, "Report selection")["auth-off-with-token"]
    assert report.startswith("2 of 4 repositories"), report
    assert "private, skipped until gh is signed in: ['secret']" in report


def test_gh_becomes_git_s_credential_helper_once_and_only_when_signed_in(configured):
    root, _, _ = configured
    setup = "auth setup-git --hostname github.com"
    for host in CASES:
        assert _calls(root, host).count(setup) == (1 if host in SIGNED_IN_AFTER else 0), host
        helpers = _git_config(root, host, "credential.https://github.com.helper")
        if host in SIGNED_IN_AFTER:
            assert helpers == ["", f"!{root / 'bin' / 'gh'} auth git-credential"], host
        else:
            assert helpers == [], host


def _credential_fill(root: Path, host: str) -> subprocess.CompletedProcess:
    """What git itself hands a push to github.com from that host's config: the helper chain, run by git."""
    return subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        env={
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(root / host),
            "GIT_CONFIG_GLOBAL": str(root / host / ".gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_git_itself_gets_the_token_from_gh_through_the_written_config(configured):
    # Not the role's reading of ~/.gitconfig but git's: the helper entries are
    # what `git push` runs, and the stand-in answers them from the login file.
    root, _, _ = configured
    for host, token in (
        ("token-signed-out", TOKENS["vault"]),
        ("token-cleared-oauth", TOKENS["oauth"]),
        ("env-login", TOKENS["env"]),
    ):
        result = _credential_fill(root, host)
        assert result.returncode == 0, (host, result.stderr)
        assert "username=x-access-token" in result.stdout.splitlines(), (host, result.stdout)
        assert f"password={token}" in result.stdout.splitlines(), host
    # Signed out, with prompts off as the clone task runs: git fails fast,
    # saying why, rather than waiting for a password no terminal can give.
    result = _credential_fill(root, "token-cleared-ours")
    assert result.returncode != 0
    assert "terminal prompts disabled" in result.stderr


def test_the_identity_is_the_account_s_noreply_address_unless_set(configured):
    root, _, _ = configured
    for host in set(CASES) - {"auth-off-with-token", "identity-set"} - FAILING:
        assert _git_config(root, host, "user.name") == ["Molecule Tester"], host
        assert _git_config(root, host, "user.email") == [NOREPLY], host
    assert _git_config(root, "identity-set", "user.name") == ["Dev Eloper"]
    assert _git_config(root, "identity-set", "user.email") == ["dev@example.com"]
    # github_auth off: ~/.gitconfig is not written at all.
    assert not (root / "auth-off-with-token" / ".gitconfig").exists()


def test_a_signed_out_vm_is_told_the_one_command(configured):
    _, first, _ = configured
    messages = _messages(first, "Say how to sign gh in")
    assert set(messages) == {"signed-out-no-token", "token-cleared-ours"}, messages
    for host, message in messages.items():
        assert f"Run make github-login VM_NAME={host} on your machine" in message, message
    # Signed out by its own switch, not for want of a login: nothing to run.
    assert "auth-off-with-token" not in messages


def test_no_token_ever_reaches_the_output(configured):
    _, first, second = configured
    for report in (first, second):
        text = json.dumps(report)
        for token in (*TOKENS.values(), "bad-token"):
            assert token not in text, token


def test_a_second_run_changes_nothing(configured):
    root, _, second = configured
    assert second["rc"] == 0, _failures(second)
    for host, stats in second["stats"].items():
        assert stats["changed"] == 0, (host, stats)
        assert stats["failures"] == 0
    login = "auth login --hostname github.com --git-protocol https --with-token --insecure-storage"
    for host in SIGNED_IN_AFTER:
        calls = _calls(root, host)
        assert calls.count(login) <= 1, (host, calls)
        assert calls.count("auth setup-git --hostname github.com") == 1, (host, calls)


LOGINS = {
    "signed-out": {},
    "pat": {"stored": TOKENS["old"]},
    "fine-grained": {"stored": "github_pat_000000000000"},
    "ours": {"stored": TOKENS["old"], "marker": TOKENS["old"]},
    "oauth": {"stored": TOKENS["oauth"]},
    "env": {"stored": TOKENS["env"], "source": "GH_TOKEN"},
    "old-gh": {"old": True},
}


@pytest.fixture(scope="module")
def login(tmp_path_factory, fake_github):
    """github_login.yml, run once for every canned login: what it told each host."""
    root = tmp_path_factory.mktemp("github_login")
    # No home given: `make github-login` gives none, and the playbook once
    # failed on a VM for exactly that, reading the marker before anything had
    # looked the home up.
    hosts = _hosts(root, {name: {"token": "", **case} for name, case in LOGINS.items()}, fake_github, home_given=False)
    report = _run(
        root,
        REPO / "playbooks" / "github_login.yml",
        hosts,
        "-e",
        '{"claude_vm_discovery": false, "ansible_become": false}',
    )
    assert report["rc"] == 0, _failures(report)
    return _messages(report, "Report the login state and what to do about it")


def test_github_login_prints_the_command_for_a_signed_out_vm(login):
    assert set(login) == set(LOGINS)
    assert "not signed in to GitHub" in login["signed-out"]
    assert "Run make github-login VM_NAME=signed-out to sign in by the device flow" in login["signed-out"]


@pytest.mark.parametrize(
    ("host", "how"),
    [
        ("pat", "with a personal access token of your own"),
        ("fine-grained", "with a personal access token of your own"),
        ("ours", "with the personal access token this project put there"),
        ("oauth", "through a device-flow login"),
        ("env", "through a GH_TOKEN or GITHUB_TOKEN in its environment"),
    ],
)
def test_github_login_says_how_a_vm_is_signed_in(login, host, how):
    assert f"already signed in to GitHub as {USERNAME} {how}" in login[host], login[host]
    assert "auth login" not in login[host]


def test_github_login_names_a_gh_too_old_to_report_its_state(login):
    assert "needs gh 2.81.0 or newer" in login["old-gh"]
    assert "unknown flag: --json" in login["old-gh"]
    assert "auth login" not in login["old-gh"]


def test_github_login_names_a_missing_workflow_scope_of_a_device_flow_login(login):
    # The stand-in reports gh's default scopes, repo and read:org.
    assert "lacks the workflow scope" in login["oauth"]
    assert "gh auth refresh -h github.com -s workflow" in login["oauth"]
    assert "workflow scope" not in login["pat"]


# ------------------------------------------------------- the device flow ----
# `make github-login VM_NAME=<one>`: github_login.yml with vm_name set, against
# a signed-out host. The stand-in gh prints the code and the page as gh does
# with no terminal, then approves itself after two seconds, or expires.
# github_projects_user is this user: after the sign-in the playbook runs the
# whole role, whose directory task sets owner and group to that user, and on a
# machine without a group of that name (macOS) that task fails after the
# sign-in has already happened, which the assertions allow for.
ME = getpass.getuser()


def _has_own_group() -> bool:
    try:
        grp.getgrnam(ME)
    except KeyError:
        return False
    return True


# The gh a failed run left polling: it holds the output file open and, when
# its code expires, writes the expiry line through that descriptor. Here it
# does so as soon as the new run has touched the file (truncated it, or
# replaced it), which is the worst moment: before the new code is approved.
STALE_GH = """
import os, sys, time
path = sys.argv[1]
fd = os.open(path, os.O_WRONLY | os.O_APPEND)
ino = os.fstat(fd).st_ino
deadline = time.time() + 120
while time.time() < deadline:
    try:
        untouched = os.stat(path).st_ino == ino and "0LDC-0DE1" in open(path).read()
    except FileNotFoundError:
        untouched = False
    if not untouched:
        break
    time.sleep(0.02)
os.write(fd, b"failed to authenticate via web browser: This 'device_code' has expired. (expired_token)\\n")
"""


def _device_flow(tmp_path_factory, fake_github, name: str, case: dict) -> tuple[Path, dict]:
    root = tmp_path_factory.mktemp("device_" + name)
    hosts = _hosts(root, {name: {"token": "", **case}}, fake_github, home_given=False)
    hosts[name]["github_projects_user"] = ME
    if case.get("stale"):
        out = root / name / ".config" / "claude-on-proxmox" / "gh-login.out"
        subprocess.Popen([sys.executable, "-c", STALE_GH, str(out)], stdin=subprocess.DEVNULL)
    report = _run(
        root,
        REPO / "playbooks" / "github_login.yml",
        hosts,
        "-e",
        json.dumps({"claude_vm_discovery": False, "ansible_become": False, "vm_name": name}),
    )
    return root, report


@pytest.fixture(scope="module")
def approved(tmp_path_factory, fake_github):
    return _device_flow(tmp_path_factory, fake_github, "approved", {})


@pytest.fixture(scope="module")
def expired(tmp_path_factory, fake_github):
    return _device_flow(tmp_path_factory, fake_github, "expired", {"expire": True})


@pytest.fixture(scope="module")
def stale(tmp_path_factory, fake_github):
    # Approved late enough that the stale gh's line lands while the run waits.
    return _device_flow(tmp_path_factory, fake_github, "stale", {"stale": True, "approve_after": 8})


def _task(report: dict, name: str, host: str) -> dict | None:
    for task_name, task_host, result in _results(report):
        if task_host == host and task_name.endswith(name):
            return result
    return None


def test_the_device_flow_runs_gh_with_no_terminal_and_shows_the_code(approved):
    root, report = approved
    assert DEVICE_LOGIN in _calls(root, "approved"), _calls(root, "approved")
    shown = _messages(report, "Say the code and the page")["approved"]
    assert "FAKE-C0DE" in shown
    assert "https://github.com/login/device" in shown
    assert "waits here" in shown


def test_an_approved_code_signs_gh_in_and_cleans_up(approved):
    root, report = approved
    assert (root / "approved" / ".config" / "gh" / "fake-token").read_text().strip() == "gho_device0000000000"
    # No marker: a device-flow login is the user's own, never signed out.
    assert not _marker(root, "approved").exists()
    assert not (root / "approved" / ".config" / "claude-on-proxmox" / "gh-login.out").exists()
    async_dir = root / "approved" / ".ansible_async"
    leftovers = list(async_dir.glob("*")) if async_dir.exists() else []
    assert not leftovers, leftovers
    # The playbook then went on to the whole role.
    assert _task(report, "Configure the repositories now that gh is signed in", "approved") is not None


def test_after_the_sign_in_the_whole_role_runs_and_clones_the_private_repository(approved):
    root, report = approved
    if not _has_own_group():
        pytest.skip("the role's directory task needs a group named after this user, which macOS lacks")
    assert report["rc"] == 0, _failures(report)
    assert "auth setup-git --hostname github.com" in _calls(root, "approved")
    cloned = {path.name for path in (root / "approved" / "projects").iterdir() if path.is_dir()}
    assert cloned == ALL


def test_the_desktop_steps_never_run_in_a_test(approved):
    _, report = approved
    for name in (
        "Look for a browser opener and a clipboard on this machine",
        "Open the page in your browser",
        "Copy the code to your clipboard",
    ):
        result = _task(report, name, "approved")
        assert result is not None and result.get("skipped"), (name, result)


def test_an_expired_code_is_a_named_failure(expired):
    root, report = expired
    assert report["rc"] != 0
    failures = _failures(report)
    assert set(failures) == {"expired"}, failures
    assert "did not complete" in failures["expired"]
    assert "expired_token" in failures["expired"]
    assert "make github-login VM_NAME=expired again" in failures["expired"]
    assert not (root / "expired" / ".config" / "gh" / "fake-token").exists()
    assert not (root / "expired" / ".config" / "claude-on-proxmox" / "gh-login.out").exists()


def test_a_run_over_the_output_of_an_earlier_failed_run_reads_only_the_new_code(stale):
    # The earlier run's gh is still polling and writing to that file, so it
    # is unlinked, not truncated, before gh starts: the stale gh keeps its own
    # inode, and the new wait sees neither the old code nor its expiry line.
    root, report = stale
    # The role after the login may stop at its directory task on macOS, as
    # above; the flow itself must have gone through.
    flow_failures = {host: text for host, text in _failures(report).items() if "device_flow |" in text}
    assert not flow_failures, flow_failures
    shown = _messages(report, "Say the code and the page")["stale"]
    assert "FAKE-C0DE" in shown and "0LDC-0DE1" not in shown, shown
    assert (root / "stale" / ".config" / "gh" / "fake-token").read_text().strip() == "gho_device0000000000"
    assert not (root / "stale" / ".config" / "claude-on-proxmox" / "gh-login.out").exists()
    assert _task(report, "Remove the output of an earlier run", "stale") is not None


def test_the_desktop_steps_never_use_the_controller_s_sudo():
    # The play's become is the VM's passwordless sudo; inherited by a task
    # delegated to the controller it is the developer's, which asks for a
    # password and fails the run. The harness runs with become off, so this
    # is held statically.
    tasks = yaml.safe_load((ROLE / "tasks" / "device_flow.yml").read_text())
    delegated = [task for task in tasks if task.get("delegate_to") == "localhost"]
    assert len(delegated) == 3, [task["name"] for task in delegated]
    for task in delegated:
        assert task.get("become") is False, task["name"]


@pytest.mark.parametrize("scenario", sorted((REPO / "molecule").glob("*/")), ids=lambda p: p.name)
def test_every_playbook_scenario_keeps_the_device_flow_off_the_developer_s_desktop(scenario):
    # The browser and clipboard steps run on localhost, which in a scenario is
    # the developer's machine. The switch is read on the VM host the role runs
    # on, so any host_vars file of the scenario may carry it, but one must.
    texts = [path.read_text() for path in scenario.glob("host_vars/*.yml")]
    assert any("github_login_desktop: false" in text for text in texts), scenario


@pytest.mark.parametrize("converge", sorted((REPO / "roles" / "github_projects" / "molecule").glob("*/converge.yml")))
def test_the_role_scenario_keeps_the_device_flow_off_the_developer_s_desktop(converge):
    text = converge.read_text()
    assert text.count("github_projects_login_desktop: false") == text.count("role: github_projects"), converge


def test_the_harness_keeps_the_device_flow_off_the_developer_s_desktop(tmp_path):
    hosts = _hosts(tmp_path, {"probe": {"token": ""}}, "http://127.0.0.1:1")
    assert hosts["probe"]["github_projects_login_desktop"] is False
