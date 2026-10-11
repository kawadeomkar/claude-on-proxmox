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
- A vault token is never put over a login the user made by hand (unless
  github_auth_method is token): it only lists then, and the run says so.
- A gh too old for `gh auth status --json` (before 2.81.0) is a named
  failure, not "signed out" forever.
- gh becomes git's credential helper once, by gh's own test for its entry.
- A private repository is cloned only where git has a credential for it: over
  ssh always, over https with gh signed in and git asking gh. Otherwise it is
  skipped and named, not cloned and failed.
- The identity in ~/.gitconfig is the account's noreply address unless set,
  and one set by hand on the VM is never replaced; an organisation gives none.
- A token GitHub refuses fails the host with a reason, and no task ever
  prints a token: every task that renders one is no_log, and every report,
  with each module's arguments injected into it, is searched for every
  canned one. Text gh prints is quoted without control characters.
- `make github-login` says how a VM is signed in, and the command otherwise -
  only one that can do something there - and looks the user's home up
  itself: run without one, as the playbook is.
- Given names, `make github-login` runs GitHub's device flow itself, one VM
  after another: gh in the background with no terminal, the code read from
  its output and shown, the approval waited for, then the whole role. The
  outcome is gh's own login state. An expired code, a gh that shows no code
  and a gh that hangs are named failures with the retry command, and every
  ending ends gh and leaves no job file or output behind. The browser and
  clipboard steps never run in a test: every scenario and this harness
  switch them off, and a test here keeps it so.
- make deploy asks VM by VM: Enter goes where a Claude code was entered, and
  "n" skips; elsewhere only "y" goes.
"""

from __future__ import annotations

import getpass
import grp
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from harness import STAND_INS, _failures, _messages, _results, _run, _run_at_a_terminal

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
    # A classic token with repo alone, as earlier versions asked for.
    "repo_only": "ghp_repoonly00000",
}
# What the stand-in gh stores at a device-flow login, and a fine-grained
# token the login report meets.
DEVICE_TOKEN = "gho_device0000000000"
FINE_GRAINED = "github_pat_000000000000"
# Every token any fixture hands the role or gh holds: none may reach a report.
CANNED = (*TOKENS.values(), "bad-token", DEVICE_TOKEN, FINE_GRAINED)
DEVICE_LOGIN = "auth login --hostname github.com --git-protocol https --web --skip-ssh-key --scopes workflow"
NOREPLY = f"4242+{USERNAME}@users.noreply.github.com"
# Control characters a VM's user could put in what gh prints: a title, a
# clipboard write (OSC 52) and a colour.
ESCAPES = "\x1b]0;owned\x07\x1b]52;c;b3duZWQ=\x07\x1b[31m"
CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")

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
    # Set here, over an identity set by hand: what is set here wins.
    "identity-set": {
        "token": TOKENS["vault"],
        "github_projects_git_user_name": "Dev Eloper",
        "github_projects_git_user_email": "dev@example.com",
        "gitconfig": {"user.name": "Old Name", "user.email": "old@example.com"},
    },
    "refused-token": {"token": "bad-token"},
    "token-repo-only": {"token": TOKENS["repo_only"]},
    "old-gh": {"token": "", "old": True},
    # What gh printed, escape sequences and all, quoted in the failure.
    "old-gh-escapes": {"token": "", "old": ESCAPES + "unknown flag: --json\x1b[0m"},
    # A login the user made by hand beside a vault token - for pushing, as
    # earlier versions advised: the token is not put over it.
    "token-beside-oauth": {"token": TOKENS["vault"], "stored": TOKENS["oauth"]},
    "token-beside-pat": {"token": TOKENS["vault"], "stored": TOKENS["old"]},
    # Unless the method asks for the token whatever gh holds.
    "token-method-over-own": {
        "token": TOKENS["vault"],
        "stored": TOKENS["oauth"],
        "github_projects_auth_method": "token",
    },
    # An identity set by hand on the VM stays, each key on its own.
    "identity-by-hand": {
        "token": TOKENS["vault"],
        "gitconfig": {"user.name": "Hand Set", "user.email": "w@example.com"},
    },
    "email-by-hand": {"token": "", "gitconfig": {"user.email": "w@example.com"}},
    # An organisation as github_username, no token: no identity, said.
    "org-no-token": {"token": "", "github_projects_username": "acme-org"},
    # github_auth off with a login made by hand: a private repository clones
    # only if that login also made gh git's credential helper.
    "auth-off-own-login": {"token": "", "stored": TOKENS["oauth"], "github_projects_auth": False},
    "auth-off-own-helper": {"token": "", "stored": TOKENS["oauth"], "github_projects_auth": False, "helper": True},
    # Over ssh a key of the user's own clones it, gh or no gh.
    "ssh-auth-off-token": {
        "token": TOKENS["vault"],
        "github_projects_auth": False,
        "github_projects_clone_protocol": "ssh",
    },
    # make deploy's configure: the sign-in comes next in the same run.
    "deploy-signed-out": {"token": "", "github_projects_sign_in_follows": True},
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
    # It lists, as before; gh is signed out, so the private one waits.
    "token-repo-only": PUBLIC,
    "old-gh": set(),
    "old-gh-escapes": set(),
    "token-beside-oauth": ALL,
    "token-beside-pat": ALL,
    "token-method-over-own": ALL,
    "identity-by-hand": ALL,
    "email-by-hand": PUBLIC,
    "org-no-token": set(),
    "auth-off-own-login": PUBLIC,
    "auth-off-own-helper": ALL,
    "ssh-auth-off-token": ALL,
    "deploy-signed-out": PUBLIC,
}
# Signed in after the run, with gh made git's credential helper by it.
SIGNED_IN_AFTER = {
    "token-signed-out",
    "token-changed",
    "token-cleared-pat",
    "token-cleared-stale-marker",
    "token-cleared-oauth",
    "env-login",
    "identity-set",
    "token-beside-oauth",
    "token-beside-pat",
    "token-method-over-own",
    "identity-by-hand",
}
# Signed in by the vault token, by this run.
TOKEN_SIGNED_IN = {"token-signed-out", "token-changed", "identity-set", "token-method-over-own", "identity-by-hand"}
FAILING = {"refused-token", "old-gh", "old-gh-escapes"}
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
        # As on a VM, where the user's authorized_keys lives there: an ssh
        # clone's known_hosts goes into it.
        (home / ".ssh").mkdir(mode=0o700)
        state = home / ".config" / "gh"
        state.mkdir(parents=True)
        if case.get("stored"):
            (state / "fake-token").write_text(case["stored"] + "\n")
        if case.get("source"):
            (state / "fake-source").write_text(case["source"])
        # The stand-in gh's failure modes: True for its own text, a string
        # for that text instead.
        for key, file_name in (("old", "fake-old"), ("no_code", "fake-no-code")):
            if case.get(key):
                (state / file_name).write_text("" if case[key] is True else case[key])
        if case.get("hang"):
            (state / "fake-hang").touch()
        if case.get("said"):
            (state / "fake-said").write_text(case["said"] + "\n")
        if case.get("expire"):
            (state / "fake-expire").touch()
        if case.get("approve_after"):
            (state / "fake-approve-after").write_text(str(case["approve_after"]))
        # What the user set by hand in ~/.gitconfig on the VM.
        for key, value in case.get("gitconfig", {}).items():
            _git_config_set(home / ".gitconfig", key, value)
        if case.get("helper"):
            # gh made git's credential helper by hand, as gh writes it.
            for value in ("", f"!{bin_dir / 'gh'} auth git-credential"):
                _git_config_set(home / ".gitconfig", "credential.https://github.com.helper", value, add=True)
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
            # Every module's arguments in its result, so that a token handed
            # to a task without no_log reaches the report the tests search.
            "ansible_inject_invocation": True,
            **({"github_projects_home": str(home)} if home_given else {}),
            **{key: value for key, value in case.items() if key.startswith("github_projects_")},
        }
    return hosts


def _git_config_set(path: Path, key: str, value: str, *, add: bool = False) -> None:
    subprocess.run(
        ["git", "config", "--file", str(path), *(["--add"] if add else []), key, value],
        check=True,
        capture_output=True,
    )


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
    # One fork per host: every host is the same machine, and the fixture runs twice.
    return _run(root, playbook, hosts, "-e", '{"ansible_become": false}', "--forks", str(len(hosts)), *args)


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
    assert "make github-login VM_NAME=refused-token" in failures["refused-token"]
    assert '"Private repositories, and pushing from the VM"' in failures["refused-token"]
    # Not "signed out", which would print the sign-in command on every run
    # and never configure the helper: the flag the role needs, by version.
    assert "needs gh 2.81.0 or newer" in failures["old-gh"]
    assert "unknown flag: --json" in failures["old-gh"]
    assert "dev_tools_install_gh" in failures["old-gh"]


def test_text_gh_prints_is_quoted_without_control_characters(configured):
    # The VM's user controls what gh prints, and ansible-core 2.21 writes a
    # failure's [ERROR] line to this terminal and the run log unescaped: a
    # clipboard write or a retitle there would reach the developer's terminal.
    _, first, _ = configured
    said = _failures(first)["old-gh-escapes"]
    assert "unknown flag: --json" in said
    assert not CONTROL.search(said), repr(said)
    # Each sequence goes whole, its payload with it: a title, a clipboard write.
    assert "owned" not in said and "b3duZWQ" not in said and "[31m" not in said, repr(said)


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
        # A token gh would not sign in with for a scope is tried on each run (the
        # fixture makes two), so adding the scope is all the next configure needs.
        expected = 1 if host in TOKEN_SIGNED_IN | {"refused-token"} else 2 if host == "token-repo-only" else 0
        assert len(logins) == expected, (host, _calls(root, host))
    for host in TOKEN_SIGNED_IN:
        assert (root / host / ".config" / "gh" / "fake-token").read_text().strip() == TOKENS["vault"], host


def test_a_vault_token_is_not_put_over_a_login_made_by_hand(configured):
    # The login the user made - a device-flow one, or a token of their own
    # pasted into gh, for pushing beside a token that only lists - stays as
    # it is, unmarked, so that clearing the vault token later never signs
    # it out; the token only lists, and the run says so on every run.
    root, first, second = configured
    for host, token in (("token-beside-oauth", TOKENS["oauth"]), ("token-beside-pat", TOKENS["old"])):
        assert (root / host / ".config" / "gh" / "fake-token").read_text().strip() == token, host
        assert not _marker(root, host).exists(), host
    for report in (first, second):
        said = _messages(report, "Say that the token only lists beside a login of your own")
        assert set(said) == {"token-beside-oauth", "token-beside-pat"}, said
        for host, message in said.items():
            assert f"gh is signed in to GitHub as {USERNAME} by a login of your own" in message, message
            assert "vault_github_token only lists the repositories there" in message, message
            assert "github_auth_method: token" in message, message
            assert message.endswith(f"make configure VM_NAME={host} TAGS=github_projects"), message
    # github_auth_method: token asks for the token whatever gh holds.
    assert _marker(root, "token-method-over-own").read_text().strip() == _digest(TOKENS["vault"])


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
    for host in TOKEN_SIGNED_IN:
        assert _marker(root, host).read_text().strip() == _digest(TOKENS["vault"]), host
        assert oct(_marker(root, host).stat().st_mode & 0o777) == "0o600"
    for host in (
        "signed-out-no-token",
        "token-cleared-pat",
        "token-cleared-oauth",
        "env-login",
        "auth-off-with-token",
        "token-beside-oauth",
        "token-beside-pat",
    ):
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
    # One line, whatever it holds.
    for host, report in reports.items():
        assert "\n" not in report, (host, report)


def test_a_private_repository_listed_while_signed_out_is_skipped_and_named(configured):
    _, first, _ = configured
    report = _messages(first, "Report selection")["auth-off-with-token"]
    assert report.startswith("2 of 4 repositories"), report
    assert "private, skipped until gh is signed in: ['secret']" in report


def test_a_private_repository_is_cloned_only_with_a_credential_git_can_use(configured):
    # Over https the credential is gh's: signed in, and git asking gh. With
    # github_auth off, a login made by hand lists the private repository, and
    # without gh as git's helper it would fail to clone and fail the host -
    # so it is skipped and named, while one whose login made gh the helper
    # clones. Over ssh, a key of the user's own clones it, gh or no gh.
    root, first, _ = configured
    reports = _messages(first, "Report selection")
    report = reports["auth-off-own-login"]
    assert report.startswith("2 of 4 repositories"), report
    assert "github_auth is false, and gh is not its credential helper): ['secret']" in report
    for host in ("auth-off-own-helper", "ssh-auth-off-token"):
        assert reports[host].startswith("3 of 4 repositories"), reports[host]
        assert "skipped as git has no credential" not in reports[host] and "until gh is signed in" not in reports[host]
    # github_auth off: the helper was read, never written.
    assert "auth setup-git --hostname github.com" not in _calls(root, "auth-off-own-login")
    assert _git_config(root, "auth-off-own-login", "credential.https://github.com.helper") == []


def test_gh_becomes_git_s_credential_helper_once_and_only_when_signed_in(configured):
    root, _, _ = configured
    setup = "auth setup-git --hostname github.com"
    helper = ["", f"!{root / 'bin' / 'gh'} auth git-credential"]
    for host in CASES:
        assert _calls(root, host).count(setup) == (1 if host in SIGNED_IN_AFTER else 0), host
        helpers = _git_config(root, host, "credential.https://github.com.helper")
        # Made by this run, or by hand before it (and left as it was).
        assert helpers == (helper if host in SIGNED_IN_AFTER or CASES[host].get("helper") else []), host


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
        # The user's own login, under a vault token that only lists.
        ("token-beside-oauth", TOKENS["oauth"]),
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


# What ~/.gitconfig holds after the run, where it is not the account's own.
IDENTITIES = {
    # Set here, over what was set by hand.
    "identity-set": (["Dev Eloper"], ["dev@example.com"]),
    # Set by hand on the VM: kept, each key on its own.
    "identity-by-hand": (["Hand Set"], ["w@example.com"]),
    "email-by-hand": (["Molecule Tester"], ["w@example.com"]),
    # Nobody commits as an organisation.
    "org-no-token": ([], []),
    # github_auth off: ~/.gitconfig is not written.
    "auth-off-with-token": ([], []),
    "auth-off-own-login": ([], []),
    "auth-off-own-helper": ([], []),
    "ssh-auth-off-token": ([], []),
}


@pytest.mark.parametrize("host", sorted(set(CASES) - FAILING))
def test_the_identity_is_the_account_s_noreply_address_unless_set_here_or_by_hand(configured, host):
    root, _, _ = configured
    name, email = IDENTITIES.get(host, (["Molecule Tester"], [NOREPLY]))
    assert (_git_config(root, host, "user.name"), _git_config(root, host, "user.email")) == (name, email)


def test_github_auth_off_writes_no_gitconfig(configured):
    root, _, _ = configured
    assert not (root / "auth-off-with-token" / ".gitconfig").exists()


def test_an_organisation_gives_no_identity_and_says_so(configured):
    _, first, _ = configured
    said = _messages(first, "Say that an organisation gives no git identity")
    assert set(said) == {"org-no-token"}, said
    assert "acme-org is an organisation" in said["org-no-token"]
    assert said["org-no-token"].endswith("make github-login VM_NAME=org-no-token")


def test_a_token_gh_will_not_sign_in_with_still_lists_and_says_which_scopes_to_add(configured):
    # gh asks a classic token for read:org, which the repo-only token earlier
    # versions asked for lacks; under auto that is no reason to fail the VM.
    root, first, _ = configured
    assert "token-repo-only" not in _failures(first)
    said = _messages(first, "Say that the token only lists")
    assert set(said) == {"token-repo-only"}, said
    assert "missing required scope 'read:org'" in said["token-repo-only"]
    assert "Add read:org" in said["token-repo-only"]
    assert "make configure VM_NAME=token-repo-only TAGS=github_projects" in said["token-repo-only"]
    assert said["token-repo-only"].endswith("make github-login VM_NAME=token-repo-only")
    assert not (root / "token-repo-only" / ".config" / "gh" / "fake-token").exists()


def test_a_signed_out_vm_is_told_the_one_command(configured):
    _, first, _ = configured
    messages = _messages(first, "Say how to sign gh in")
    assert set(messages) == {
        "signed-out-no-token",
        "token-cleared-ours",
        "email-by-hand",
        "org-no-token",
        "deploy-signed-out",
    }, messages
    for host, message in messages.items():
        if host != "deploy-signed-out":
            assert f"Run make github-login VM_NAME={host} on your machine" in message, message
    # Signed out by its own switch, not for want of a login: nothing to run.
    assert "auth-off-with-token" not in messages


def test_under_deploy_a_signed_out_vm_is_told_the_sign_in_comes_next(configured):
    # make deploy signs gh in after configuration, in the same run: sending
    # the user to make github-login there would have them stop it to do so.
    _, first, _ = configured
    message = _messages(first, "Say how to sign gh in")["deploy-signed-out"]
    assert "make deploy signs it in next, in this run" in message, message
    assert "make github-login" not in message


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
    "fine-grained": {"stored": FINE_GRAINED},
    "ours": {"stored": TOKENS["old"], "marker": TOKENS["old"]},
    "oauth": {"stored": TOKENS["oauth"]},
    "env": {"stored": TOKENS["env"], "source": "GH_TOKEN"},
    "old-gh": {"old": True},
    # A vault token gh is not signed in with yet: the device flow does not
    # run for it, and make github-login is no answer.
    "pending-token": {"token": TOKENS["vault"]},
    # gh left alone: nothing to run, and no advice to turn off what is off.
    "auth-off": {"github_projects_auth": False},
    "old-gh-auth-off": {"old": True, "github_projects_auth": False},
}


@pytest.fixture(scope="module")
def login_run(tmp_path_factory, fake_github):
    """github_login.yml, run once for every canned login, as a bare make github-login runs it: the report."""
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
        "--forks",
        str(len(hosts)),
    )
    assert report["rc"] == 0, _failures(report)
    return report


@pytest.fixture(scope="module")
def login(login_run):
    """What github_login.yml told each canned login."""
    return _messages(login_run, "Report the login state and what to do about it")


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
    # "already": nothing in this run signed it in.
    assert f"already signed in to GitHub as {USERNAME} {how}" in login[host], login[host]
    assert "auth login" not in login[host]


def test_github_login_names_a_gh_too_old_to_report_its_state(login):
    assert "needs gh 2.81.0 or newer" in login["old-gh"]
    assert "unknown flag: --json" in login["old-gh"]
    assert "auth login" not in login["old-gh"]


def test_github_login_names_a_missing_workflow_scope_of_a_device_flow_login(login):
    # The stand-in reports gh's default scopes: gist, read:org and repo.
    assert "lacks the workflow scope" in login["oauth"]
    assert "gh auth refresh -h github.com -s workflow" in login["oauth"]
    assert "workflow scope" not in login["pat"]


def test_github_login_names_what_signs_a_pending_vault_token_in(login):
    # The device flow does not run with a token in the vault, so sending the
    # user to make github-login would only bring them back here.
    said = login["pending-token"]
    assert "vault_github_token is set" in said, said
    assert said.endswith("make configure VM_NAME=pending-token TAGS=github_projects"), said
    assert "make github-login" not in said


def test_github_login_leaves_gh_alone_with_github_auth_off(login):
    for host in ("auth-off", "old-gh-auth-off"):
        assert "(github_auth: false)" in login[host], login[host]
        assert "make " not in login[host], login[host]
        assert "set github_auth: false" not in login[host], login[host]
    assert "needs gh 2.81.0 or newer" in login["old-gh-auth-off"]


@pytest.fixture(scope="module")
def pending_named(tmp_path_factory, fake_github):
    """make github-login VM_NAME=pending with a vault token gh is not signed in with: the whole role follows."""
    root = tmp_path_factory.mktemp("github_login_pending")
    hosts = _hosts(root, {"pending": {"token": TOKENS["vault"]}}, fake_github, home_given=False)
    hosts["pending"]["github_projects_user"] = ME
    report = _run(
        root,
        REPO / "playbooks" / "github_login.yml",
        hosts,
        "-e",
        json.dumps({"claude_vm_discovery": False, "ansible_become": False, "vm_name": "pending"}),
    )
    return root, report


def test_github_login_says_the_role_signs_a_pending_vault_token_in_next(pending_named):
    root, report = pending_named
    said = _messages(report, "Report the login state and what to do about it")["pending"]
    assert "the role signs gh in with it next, in this run" in said, said
    assert "make " not in said
    assert DEVICE_LOGIN not in _calls(root, "pending")
    if _has_own_group():
        # And it does: the role run after the report signs gh in with it.
        assert (root / "pending" / ".config" / "gh" / "fake-token").read_text().strip() == TOKENS["vault"]


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


# The gh a run that never ended it left polling (Ctrl-C): it holds the output
# file open and, when its code expires, writes its expiry line and then the
# job's closing line through that descriptor - at any point in the new run's
# fifteen-minute wait. Here it does so once the new run's gh is up and
# waiting, its code in the file. After an unlink, which is what the flow does,
# the descriptor is the orphaned old file and the new wait never sees the
# lines; after a truncation, it is the very file the new wait reads, and the
# closing line ends the wait long before the new gh is approved, which then is
# ended unapproved. Writing any earlier - at the truncation itself - would let
# the start task's own `>` wipe it again.
STALE_GH = """
import os, sys, time
path = sys.argv[1]
fd = os.open(path, os.O_WRONLY | os.O_APPEND)
deadline = time.time() + 120
while time.time() < deadline:
    try:
        text = open(path).read()
    except FileNotFoundError:
        text = ""
    if "FAKE-C0DE" in text:
        break
    time.sleep(0.02)
os.write(fd, b"failed to authenticate via web browser: context deadline exceeded\\ngh exited: 1\\n")
"""


def _device_flow(tmp_path_factory, fake_github, name: str, case: dict, extra: dict | None = None) -> tuple[Path, dict]:
    """make github-login VM_NAME=<name> for one signed-out host; extra vars (the flow's limits) on top."""
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
        json.dumps({"claude_vm_discovery": False, "ansible_become": False, "vm_name": name, **(extra or {})}),
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
    # Approved late enough that the stale gh's lines land while the run waits,
    # and later than the run would wait for a gh whose wait had ended.
    return _device_flow(tmp_path_factory, fake_github, "stale", {"stale": True, "approve_after": 8})


@pytest.fixture(scope="module")
def nocode(tmp_path_factory, fake_github):
    """gh that cannot reach GitHub, printing its error - escape sequences and all - and no code."""
    said = (
        ESCAPES
        + "error connecting to api.github.com\x1b[0m\ncheck your internet connection or https://githubstatus.com\n"
    )
    return _device_flow(tmp_path_factory, fake_github, "nocode", {"no_code": said})


@pytest.fixture(scope="module")
def hang(tmp_path_factory, fake_github):
    """gh that never shows a code, and never exits, with two seconds for it to show one."""
    return _device_flow(
        tmp_path_factory, fake_github, "hang", {"hang": True}, {"github_projects_login_start_seconds": 2}
    )


@pytest.fixture(scope="module")
def timed_out(tmp_path_factory, fake_github):
    """A code nobody approves, with Ansible's async limit (scaled down) ending gh before the wait ends."""
    return _device_flow(
        tmp_path_factory,
        fake_github,
        "timed-out",
        {"approve_after": 60},
        {"github_projects_login_async_seconds": 3, "github_projects_login_wait_seconds": 8},
    )


@pytest.fixture(scope="module")
def abandoned(tmp_path_factory, fake_github):
    """A code nobody approves, with the wait for the approval (scaled down) over while gh still polls."""
    return _device_flow(
        tmp_path_factory, fake_github, "abandoned", {"approve_after": 60}, {"github_projects_login_wait_seconds": 2}
    )


@pytest.fixture(scope="module")
def reworded(tmp_path_factory, fake_github):
    """An approval after which gh says something other than "Logged in as", as a later gh may."""
    return _device_flow(tmp_path_factory, fake_github, "reworded", {"said": "✓ Authentication complete."})


@pytest.fixture(scope="module")
def two(tmp_path_factory, fake_github):
    """github_login.yml given two names, as make github-login VM_NAME=first,second runs it; the first expires."""
    root = tmp_path_factory.mktemp("device_two")
    cases = {"first": {"token": "", "expire": True}, "second": {"token": ""}}
    hosts = _hosts(root, cases, fake_github, home_given=False)
    for host in hosts.values():
        host["github_projects_user"] = ME
    report = _run(
        root,
        REPO / "playbooks" / "github_login.yml",
        hosts,
        "-e",
        json.dumps({"claude_vm_discovery": False, "ansible_become": False, "vm_name": "first,second"}),
    )
    return root, report


def test_several_names_are_signed_in_one_after_another(two):
    # serial: 1 holds every VM in the play while only one is in the batch, so
    # the device flow's one-VM guard reads the batch, not the play.
    _, report = two
    starts = [
        sorted(task["hosts"])
        for play in report["plays"]
        for task in play["tasks"]
        if task["task"]["name"].endswith("Start gh's device flow in the background")
    ]
    assert starts == [["first"], ["second"]], starts


def test_a_failed_sign_in_does_not_stop_the_vms_after_it_and_fails_the_run_once_all_had_their_turn(two):
    root, report = two
    # The VM after the one that failed was still signed in.
    assert (root / "second" / ".config" / "gh" / "fake-token").read_text().strip() == DEVICE_TOKEN
    assert report["rc"] != 0
    failures = _failures(report)
    assert "failed to authenticate" in failures["first"]
    assert "make github-login VM_NAME=first again" in failures["first"]
    if _has_own_group():
        assert set(failures) == {"first"}, failures
    else:
        # The whole role that runs after the sign-in stops at its directory
        # task here, which wants a group named after this user.
        assert set(failures) <= {"first", "second"}, failures


def _task(report: dict, name: str, host: str) -> dict | None:
    for task_name, task_host, result in _results(report):
        if task_host == host and task_name.endswith(name):
            return result
    return None


def _flow_files_are_gone(root: Path, host: str) -> None:
    """The job file, gh's output and its pid: removed, whatever the ending."""
    async_dir = root / host / ".ansible_async"
    leftovers = list(async_dir.glob("*")) if async_dir.exists() else []
    assert not leftovers, leftovers
    for name in ("gh-login.out", "gh-login.pid"):
        assert not (root / host / ".config" / "claude-on-proxmox" / name).exists(), name


def _gh_still_runs(root: Path, host: str) -> bool:
    """Whether the stand-in gh the flow started, by the pid it recorded, is alive."""
    pid = int((root / host / ".config" / "gh" / "fake-pid").read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_the_device_flow_runs_gh_with_no_terminal_and_shows_the_code(approved):
    root, report = approved
    assert DEVICE_LOGIN in _calls(root, "approved"), _calls(root, "approved")
    shown = _messages(report, "Say the code and the page")["approved"]
    assert "FAKE-C0DE" in shown
    assert "https://github.com/login/device" in shown
    # The choices there are: approve, wait for the code to expire, or Ctrl-C.
    assert "the wait ends by itself when the code expires" in shown
    assert "Ctrl-C ends the whole run" in shown
    # One line, a sentence: a more-indented line in a folded scalar would break it.
    assert "\n" not in shown and shown.endswith("."), repr(shown)


def test_an_approved_code_signs_gh_in_and_cleans_up(approved):
    root, report = approved
    assert (root / "approved" / ".config" / "gh" / "fake-token").read_text().strip() == DEVICE_TOKEN
    # No marker: a device-flow login is the user's own, never signed out.
    assert not _marker(root, "approved").exists()
    _flow_files_are_gone(root, "approved")
    # Said as what this run did, not as found.
    said = _messages(report, "Report the login state and what to do about it")["approved"]
    assert said.startswith(f"approved: signed in to GitHub as {USERNAME} through a device-flow login."), said
    # The playbook then went on to the whole role.
    ran = _task(report, "Run the role now that gh is signed in", "approved")
    assert ran is not None and not ran.get("skipped"), ran


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
    # gh's own words once its code has expired, which the wait outlasts.
    root, report = expired
    assert report["rc"] != 0
    failures = _failures(report)
    assert set(failures) == {"expired"}, failures
    assert "did not complete" in failures["expired"]
    assert "failed to authenticate via web browser: context deadline exceeded." in failures["expired"]
    assert failures["expired"].endswith("Run make github-login VM_NAME=expired again for a fresh code.")
    assert not (root / "expired" / ".config" / "gh" / "fake-token").exists()
    _flow_files_are_gone(root, "expired")


def test_a_gh_that_shows_no_code_is_a_named_failure_with_the_retry_command(nocode):
    # It exits at once: its closing line ends the wait for the code, and what
    # it said is quoted on one line, without its control characters.
    root, report = nocode
    failure = _failures(report)["nocode"]
    assert "gh on nocode did not start the device flow:" in failure
    assert "error connecting to api.github.com" in failure
    assert "; check your internet connection or https://githubstatus.com." in failure
    assert "gh exited" not in failure
    assert failure.endswith("Run make github-login VM_NAME=nocode again."), failure
    assert not CONTROL.search(failure), repr(failure)
    _flow_files_are_gone(root, "nocode")


def _seconds(report: dict, task: str, host: str) -> float:
    """How long the task whose name ends with `task` ran for the host."""
    for play in report["plays"]:
        for entry in play["tasks"]:
            if entry["task"]["name"].endswith(task) and host in entry["hosts"]:
                duration = entry["task"]["duration"]
                start, end = (datetime.fromisoformat(duration[key].replace("Z", "+00:00")) for key in ("start", "end"))
                return (end - start).total_seconds()
    raise AssertionError(f"no task {task!r} ran for {host}")


def test_a_gh_that_exits_ends_the_wait_at_once(nocode):
    # gh exits however it ends - here an error before any code - and the
    # job's closing line ends the wait then, not thirty seconds (or, waiting
    # for the approval, fifteen minutes) later.
    _, report = nocode
    assert _seconds(report, "Wait for gh to show the code", "nocode") < 15


def test_the_outcome_is_gh_s_login_state_not_its_wording(reworded):
    # Read from `gh auth status`, as everywhere else: a gh that words its
    # success otherwise still signs the VM in.
    root, report = reworded
    assert not {host: text for host, text in _failures(report).items() if "sign-in did not complete" in text}
    assert (root / "reworded" / ".config" / "gh" / "fake-token").read_text().strip() == DEVICE_TOKEN
    said = _messages(report, "Report the login state and what to do about it")["reworded"]
    assert said.startswith("reworded: signed in to GitHub"), said


def test_the_flow_s_limits_outlast_the_code_in_order():
    # gh polls for the fifteen minutes its code lives; the wait outlasts that,
    # so gh's own word on an expiry ends it, and Ansible's async limit outlasts
    # the wait, so its watchdog never kills gh before gh has said why.
    limits = yaml.safe_load((ROLE / "vars" / "main.yml").read_text())
    start, wait, limit = (limits[f"github_projects_login_{name}_seconds"] for name in ("start", "wait", "async"))
    assert start < 15 * 60 < wait < limit, (start, wait, limit)


def test_the_listing_asks_for_the_account_only_when_an_identity_is_derived():
    # Without a token that is one more request against GitHub's sixty an hour.
    tasks = _all_tasks(yaml.safe_load((ROLE / "tasks" / "clone.yml").read_text()))
    listing = next(task for task in tasks if "github_repos" in task)
    assert listing["github_repos"]["account"] == "{{ github_projects_git_identity_needed }}"


def test_no_task_quotes_what_gh_wrote_to_stderr_unsanitised():
    # Only the vars that strip control characters (vars/main.yml) read it.
    for path in sorted((ROLE / "tasks").glob("*.yml")):
        for task in _all_tasks(yaml.safe_load(path.read_text())):
            quoted = {
                key: value for key, value in task.items() if key not in NOT_LOGGED | {"block", "rescue", "always"}
            }
            assert ".stderr" not in json.dumps(quoted), (path.name, task["name"])


def test_a_gh_that_hangs_is_ended_and_its_job_cleaned_up(hang):
    root, report = hang
    failure = _failures(report)["hang"]
    assert "did not start the device flow: no output in 2 seconds." in failure, failure
    assert failure.endswith("Run make github-login VM_NAME=hang again.")
    assert not _gh_still_runs(root, "hang")
    _flow_files_are_gone(root, "hang")


def test_a_code_the_async_limit_ended_is_said_to_have_expired(timed_out):
    # Killed, gh never says why it stopped: its last line would be the page
    # to open, which by then serves an expired code.
    root, report = timed_out
    failure = _failures(report)["timed-out"]
    assert "the code expired after fifteen minutes without an approval." in failure, failure
    assert "Open this URL" not in failure
    _flow_files_are_gone(root, "timed-out")


def test_a_gh_still_polling_when_the_wait_ends_is_ended_and_said_to_have_expired(abandoned):
    root, report = abandoned
    failure = _failures(report)["abandoned"]
    assert "the code expired after fifteen minutes without an approval." in failure, failure
    assert not _gh_still_runs(root, "abandoned")
    assert not (root / "abandoned" / ".config" / "gh" / "fake-token").exists()
    _flow_files_are_gone(root, "abandoned")


def test_a_run_over_the_output_of_an_earlier_failed_run_reads_only_the_new_code(stale):
    # The earlier run's gh is still polling and writing to that file, so it
    # is unlinked, not truncated, before gh starts: the stale gh keeps its own
    # inode, and the new wait sees neither the old code nor its closing lines.
    root, report = stale
    # The role after the login may stop at its directory task on macOS, as
    # above; the flow itself must have gone through.
    flow_failures = {
        host: text
        for host, text in _failures(report).items()
        if "sign-in did not complete" in text or "did not start the device flow" in text
    }
    assert not flow_failures, flow_failures
    shown = _messages(report, "Say the code and the page")["stale"]
    assert "FAKE-C0DE" in shown and "0LDC-0DE1" not in shown, shown
    assert (root / "stale" / ".config" / "gh" / "fake-token").read_text().strip() == DEVICE_TOKEN
    _flow_files_are_gone(root, "stale")
    assert _task(report, "Remove the files of an earlier run", "stale") is not None


def _all_tasks(tasks: list) -> list:
    """Every task of a task file, those inside a block, rescue or always included."""
    found = []
    for task in tasks or []:
        found.append(task)
        for section in ("block", "rescue", "always"):
            found.extend(_all_tasks(task.get(section)))
    return found


def test_the_desktop_steps_never_use_the_controller_s_sudo():
    # The play's become is the VM's passwordless sudo; inherited by a task
    # delegated to the controller it is the developer's, which asks for a
    # password and fails the run. The harness runs with become off, so this
    # is held statically.
    tasks = _all_tasks(yaml.safe_load((ROLE / "tasks" / "device_flow.yml").read_text()))
    delegated = [task for task in tasks if task.get("delegate_to") == "localhost"]
    assert len(delegated) == 3, [task["name"] for task in delegated]
    for task in delegated:
        assert task.get("become") is False, task["name"]


def test_the_page_is_opened_by_the_shared_opener():
    # BROWSER is a list of commands, each with an optional %s: one filter
    # reads it (filter_plugins/desktop.py, tested on its own), for both sign-ins.
    tasks = _all_tasks(yaml.safe_load((ROLE / "tasks" / "device_flow.yml").read_text()))
    opener = next(task for task in tasks if task["name"].endswith("Open the page in your browser"))
    assert "page_opener(" in opener["vars"]["github_projects_login_opener"]
    assert opener["ansible.builtin.command"]["argv"] == "{{ github_projects_login_opener }}"
    assert "github_projects_login_opener | length > 0" in opener["when"]


# Every task argument that can carry a token: the vault's, the one gh stored,
# and the one the listing is made with. Not a condition, which is never logged.
TOKEN_VARS = re.compile(r"\bgithub_projects_(token|stored_token|listing_token)\b")
NOT_LOGGED = {"name", "when", "changed_when", "failed_when", "until", "register", "no_log", "tags"}


def test_every_task_that_renders_a_token_is_no_log():
    # Without no_log the token goes to the VM's journal (a module logs the
    # arguments its spec does not mark no_log, stdin included) and into
    # results; the report search below sees only what reaches the controller.
    rendering = {}
    for path in sorted((ROLE / "tasks").glob("*.yml")):
        for task in _all_tasks(yaml.safe_load(path.read_text())):
            if "block" in task:
                continue
            args = {key: value for key, value in task.items() if key not in NOT_LOGGED}
            if TOKEN_VARS.search(json.dumps(args)):
                rendering[task["name"]] = task.get("no_log")
    # Not vacuous: the two tasks known to hand a token on are found.
    assert {"auth | Sign gh in with the token", "clone | List repositories of {{ github_projects_username }}"} <= set(
        rendering
    ), rendering
    assert all(no_log is True for no_log in rendering.values()), rendering


# Every fixture that runs a playbook, and so every report a token could reach.
REPORT_FIXTURES = [
    "configured",
    "login_run",
    "pending_named",
    "approved",
    "reworded",
    "expired",
    "stale",
    "nocode",
    "hang",
    "timed_out",
    "abandoned",
    "two",
    "deployed",
]


def _reports(value) -> list[dict]:
    if isinstance(value, dict):
        return [value] if "plays" in value else []
    if isinstance(value, (list, tuple)):
        return [report for item in value for report in _reports(item)]
    return []


@pytest.mark.parametrize("fixture", REPORT_FIXTURES)
def test_no_token_ever_reaches_the_output(request, fixture):
    reports = _reports(request.getfixturevalue(fixture))
    assert reports, fixture
    for report in reports:
        text = json.dumps(report)
        # The search sees module arguments only with invocation injected.
        assert '"invocation"' in text, fixture
        for token in CANNED:
            assert token not in text, (fixture, token)


def _scenario_vars(paths: list[Path]) -> dict:
    """What the given vars files set, merged in Ansible's order: later files win."""
    merged = {}
    for path in paths:
        merged.update(yaml.safe_load(path.read_text()) or {})
    return merged


def _group_vars_all(scenario: Path) -> dict:
    # A directory's files load in sorted order, defaults.yml (the committed
    # wiring, linked in) before the scenario's own.
    return _scenario_vars(
        sorted([*scenario.glob("group_vars/all/*.yml"), *scenario.glob("group_vars/all.yml")], key=lambda p: p.name)
    )


@pytest.mark.parametrize("scenario", sorted((REPO / "molecule").glob("*/")), ids=lambda p: p.name)
def test_every_playbook_scenario_keeps_the_device_flow_off_the_developer_s_desktop(scenario):
    # The browser and clipboard steps of both sign-ins run on localhost, which
    # in a scenario is the developer's machine, and the sign-ins read the
    # switch on each VM: where that VM's vars come from is where it must be
    # false. A VM provisioning or deploy adds at runtime has only group_vars/all;
    # one listed among the platforms has its host_vars on top.
    molecule = yaml.safe_load((scenario / "molecule.yml").read_text())
    platforms = [p["name"] for p in molecule.get("platforms", []) if "claude_vms" in p.get("groups", [])]
    runtime = any(
        "provision.yml" in path.read_text() or "deploy.yml" in path.read_text() for path in scenario.glob("*.yml")
    )
    assert platforms or runtime, f"{scenario.name} has no VM to hold the switch for"
    group_all = _group_vars_all(scenario)
    if runtime:
        assert group_all.get("login_desktop") is False, f"{scenario.name}: group_vars/all must set login_desktop: false"
    for host in platforms:
        host_vars = _scenario_vars(sorted(scenario.glob(f"host_vars/{host}.yml")))
        assert {**group_all, **host_vars}.get("login_desktop") is False, (scenario.name, host)


def test_the_inventory_wires_the_desktop_switch_into_the_role():
    # The scenarios set the user-facing login_desktop; the role reads its own
    # variable, which defaults to true. Without this line the switch reaches
    # nothing, and every guard above passes while the page opens.
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert re.fullmatch(r"\{\{\s*login_desktop\s*\}\}", defaults["github_projects_login_desktop"]), defaults[
        "github_projects_login_desktop"
    ]


@pytest.mark.parametrize("converge", sorted((REPO / "roles" / "github_projects" / "molecule").glob("*/converge.yml")))
def test_the_role_scenario_keeps_the_device_flow_off_the_developer_s_desktop(converge):
    text = converge.read_text()
    assert text.count("github_projects_login_desktop: false") == text.count("role: github_projects"), converge


def test_the_harness_keeps_the_device_flow_off_the_developer_s_desktop(tmp_path):
    hosts = _hosts(tmp_path, {"probe": {"token": ""}}, "http://127.0.0.1:1")
    assert hosts["probe"]["github_projects_login_desktop"] is False


# ------------------------------------------------------- make deploy's step ---
# deploy.yml decides GitHub VM by VM, from what its Claude step left on each VM:
# a VM whose Claude code was entered is asked, and Enter signs it in; a VM
# that showed no Claude prompt (signed in already, or an API key) is asked,
# and "y" signs it in; Enter at a VM's Claude prompt leaves it unasked.
AFTER_CLAUDE = {
    "typed": {"claude_code_login_answered": True, "claude_code_login_skipped": False},
    "skipped": {"claude_code_login_answered": False, "claude_code_login_skipped": True},
    "quiet": {},
}


def _deploy_github_step(root: Path) -> Path:
    """make deploy's GitHub step, imported as deploy.yml imports it: the same playbook, vars and condition."""
    imported = next(
        play
        for play in yaml.safe_load((REPO / "deploy.yml").read_text())
        if play.get("ansible.builtin.import_playbook") == "playbooks/github_login.yml"
    )
    playbook = root / "deploy_github.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": imported["name"],
                    "ansible.builtin.import_playbook": str(REPO / "playbooks" / "github_login.yml"),
                    "vars": imported["vars"],
                    "when": imported["when"],
                }
            ]
        )
    )
    return playbook


@pytest.fixture(scope="module")
def deployed(tmp_path_factory, fake_github):
    """make deploy's GitHub step for three signed-out VMs, with no terminal to ask at."""
    root = tmp_path_factory.mktemp("deploy_github")
    hosts = _hosts(root, {name: {"token": ""} for name in AFTER_CLAUDE}, fake_github, home_given=False)
    for name, facts in AFTER_CLAUDE.items():
        hosts[name].update(facts, github_projects_user=ME)
    report = _run(
        root, _deploy_github_step(root), hosts, "-e", json.dumps({"ansible_become": False, "deploy_sign_in": True})
    )
    return root, report


def test_deploy_signs_gh_in_only_where_a_claude_code_was_entered(deployed):
    # With no terminal the question comes back with nothing: Enter, for the
    # VM someone entered a Claude code for; no "y", for the other.
    root, report = deployed
    # A failure after the sign-in (the role's directory task, without a group
    # named after this user) is rescued under deploy, so the run still ends 0.
    assert report["rc"] == 0, _failures(report)
    started = {name for name in AFTER_CLAUDE if DEVICE_LOGIN in _calls(root, name)}
    assert started == {"typed"}, started


def test_deploy_asks_every_vm_but_one_whose_claude_prompt_got_enter(deployed):
    _, report = deployed
    asked = {
        host
        for name, host, result in _results(report)
        if "login | Ask whether to sign" in name and not result.get("skipped")
    }
    assert asked == {"typed", "quiet"}, asked


def test_deploy_signs_a_vm_that_showed_no_claude_prompt_in_on_a_typed_y(tmp_path, fake_github):
    hosts = _hosts(tmp_path, {"quiet": {"token": ""}}, fake_github, home_given=False)
    hosts["quiet"]["github_projects_user"] = ME
    rc, output, log = _run_at_a_terminal(
        tmp_path,
        _deploy_github_step(tmp_path),
        hosts,
        {"ansible_become": False, "deploy_sign_in": True},
        "y",
        prompt=b"or Enter alone to skip",
    )
    assert rc == 0, output
    assert DEVICE_LOGIN in _calls(tmp_path, "quiet"), output
    # The token that sign-in stored reaches neither this terminal nor the run log.
    assert not [token for token in CANNED if token in output + log]


def test_deploy_skips_a_vm_whose_claude_code_was_entered_on_a_typed_n(tmp_path, fake_github):
    # Someone is here, but cannot approve GitHub now: "n" spares that VM the
    # fifteen-minute wait, and the run goes on.
    hosts = _hosts(tmp_path, {"typed": {"token": ""}}, fake_github, home_given=False)
    hosts["typed"].update(AFTER_CLAUDE["typed"], github_projects_user=ME)
    rc, output, _ = _run_at_a_terminal(
        tmp_path,
        _deploy_github_step(tmp_path),
        hosts,
        {"ansible_become": False, "deploy_sign_in": True},
        "n",
        prompt=b"type n and Enter to skip it",
    )
    assert rc == 0, output
    assert "Enter signs it in now" in output
    assert DEVICE_LOGIN not in _calls(tmp_path, "typed"), output
