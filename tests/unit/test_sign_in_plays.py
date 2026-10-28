"""The sign-in playbooks as a whole, and just deploy's sign-in steps, run against stand-in VMs.

playbooks/claude_login.yml and playbooks/github_login.yml reach every VM first,
sign VMs in one at a time where a sign-in can prompt, then do what a sign-in
leads to - Remote Control's start, the repositories - for every VM at once, and
raise what failed in a last play. just deploy skips that play for a sign-in,
which is the person's to finish later, but not for the work after one: its own
last play raises that. Every VM is the controller itself, through the harness
in harness.py, except one that cannot be reached, whose ssh is a stand-in that
refuses the connection, so nothing leaves this machine and nothing reads
~/.ssh. claude is tests/molecule/fake_claude.sh. gh is
tests/molecule/fake_gh.sh where GitHub's sign-in is under test, against
tests/molecule/fake_github_api.py on a loopback port, and otherwise a stand-in
that refuses everything, so a real gh further along PATH never runs. The
browser and clipboard steps are off on every host (login_desktop), and stdin is
/dev/null, so no prompt waits for anyone.

What these guard:

- A VM that cannot be reached ends neither sign-in. rescue never sees an
  unreachable host, and under serial one is a whole batch failed, which ended
  the run before the VMs after it: now it is named, the VMs after it have their
  turn, the run fails at its end, and just deploy goes on and exits 0.
- A failed sign-in is said in its own turn. The sign-in block's condition was
  every rescue task's too, and the rescue's first record of the failure made
  the message after it skip, so just deploy, which raises no failed sign-in at
  its end, never said why.
- What a sign-in leads to is not the sign-in. Remote Control failing to start,
  or a clone failing, after a VM signed in is said with the task, the
  repository and the just deploy --only configure command that retries it, after a full
  stop; just login --only claude and just login --only github fail on it, and just deploy
  fails at its end rather than exit 0.
- just deploy's sign-in wiring, run from deploy.yml itself: with no terminal
  and no code nothing fails; a rejected code fails neither the run nor the VM
  after it; deploy_sign_in: false runs neither sign-in, and its default runs
  them.
- A report reads every VM in one batch, a sign-in takes one VM per batch, and
  the work after the sign-ins waits for the last prompt, then runs for every VM
  at once.
- github_login.yml refuses the two switches it no longer reads, and names the
  one that replaced them.
- NEXT STEPS keeps what each sign-in missed, as every report records it: a
  failed sign-in, a VM that could not be reached and a failure after a sign-in
  each record their line, with the command that retries it, under the
  sign-in's own matter; a sign-in left out for a VM whose Claude sign-in
  nobody answered is said once, with the command that starts it, and never in
  a report; a VM signed in has nothing left.
- just login (playbooks/login.yml) runs the three in deploy's order and by its
  rules: GitHub's device flow is not started for a VM whose Claude sign-in
  nobody answered, a VM whose Claude sign-in failed sits out the sign-ins after
  it and fails the run, and with no names every step reports, starting nothing.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from harness import LOGINS, REPO, STAND_INS, _left_to_do, _results, _run, _serve, _stand_in_hosts

CLAUDE_LOGIN = REPO / "playbooks" / "claude_login.yml"
GITHUB_LOGIN = REPO / "playbooks" / "github_login.yml"
LOGIN = REPO / "playbooks" / "login.yml"
DEPLOY = REPO / "deploy.yml"
FAKE_CLAUDE = REPO / "tests" / "molecule" / "fake_claude.sh"
FAKE_GH = REPO / "tests" / "molecule" / "fake_gh.sh"
FAKE_API = REPO / "tests" / "molecule" / "fake_github_api.py"
# just deploy reads deploy_sign_in from inventory/group_vars/all/defaults.yml,
# which the harness inventory does not load: the trimmed deploy below gets it
# from there, at its committed default, unless a test sets it.
DEFAULTS = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())

# The plays whose batches and order are under test.
CLAUDE_SIGN_IN = "Sign Claude Code in, or say how each VM is signed in"
CLAUDE_AFTER = "Start Remote Control on each VM signed in just now"
GITHUB_SIGN_IN = "Sign gh in to GitHub, or say how each VM is signed in"
GITHUB_AFTER = "Configure the repositories of each VM signed in"
TAILSCALE_SIGN_IN = "Sign each VM in to Tailscale, or say how it is on it"

# A code fake_claude.sh accepts, or refuses where the case says so.
CODE = "fake-code#fake-state"
USERNAME = "molecule"
DEVICE_TOKEN = "gho_device0000000000"
# Whoever runs the tests: the github_projects role's user, so its directory
# task finds the directories owned as it wants them.
ME = pwd.getpwuid(os.getuid()).pw_name

# The interpreter of every host here: the harness's, which gives each host its
# own HOME and config and puts the stand-ins first on PATH, with OWN_GROUP
# below on its PYTHONPATH.
_WRAPPER = STAND_INS["python"].splitlines(keepends=True)
_EXEC = next(index for index, line in enumerate(_WRAPPER) if line.startswith("exec "))
PYTHON = "".join(
    [*_WRAPPER[:_EXEC], 'PYTHONPATH="$(dirname "$(dirname "$0")")/pylib"\n', "export PYTHONPATH\n", *_WRAPPER[_EXEC:]]
)

# Every user on the Ubuntu VMs the github_projects role configures has a group
# of the same name, which its directory task sets; macOS has none for the
# developer. Asked for that group, the hosts' modules get the user's own
# primary group instead, so the role runs to its end here as on a VM.
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

# ssh, scp and sftp for a VM that is down: what ssh says when nothing listens,
# and the exit status the ssh plugin takes for "unreachable".
SSH_REFUSED = """#!/bin/sh
echo "$*" >> "$(dirname "$0")/ssh.log"
echo "ssh: connect to host 192.0.2.10 port 22: Connection refused" >&2
exit 255
"""

# gh where GitHub's sign-in is not under test: every command is refused, so
# that step only reports, and a real gh further along PATH is never run.
GH_REFUSING = """#!/bin/sh
mkdir -p "$HOME/.config/gh"
echo "$*" >> "$HOME/.config/gh/calls.log"
echo "stand-in gh: not part of this test" >&2
exit 1
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _hosts(root: Path, cases: dict, github: str | None = None) -> dict:
    """Stand-in VMs, one per case, signed out of Claude and gh; returns their hostvars.

    A case may give a Claude `code` (refused with `reject`), be `down`, have
    Remote Control fail after its sign-in (`after_fails`), and set any `vars`.
    With github, the fake GitHub API's URL, gh is fake_gh.sh against it, its
    device flow approved at once (or its code expired, with `expire`), and the
    whole github_projects role can run.
    """
    hosts = _stand_in_hosts(root, {name: LOGINS["logged-out"] for name in cases})
    bin_dir = root / "bin"
    _executable(bin_dir / "python", PYTHON)
    _executable(bin_dir / "claude", FAKE_CLAUDE.read_text())
    _executable(bin_dir / "gh", FAKE_GH.read_text() if github else GH_REFUSING)
    _executable(bin_dir / "ssh", SSH_REFUSED)
    (root / "pylib").mkdir()
    (root / "pylib" / "sitecustomize.py").write_text(OWN_GROUP)
    for name, case in cases.items():
        home = root / name
        (home / ".claude" / "fake").mkdir(parents=True)
        (home / ".config" / "gh").mkdir(parents=True)
        (home / "projects").mkdir()
        if case.get("reject"):
            (home / ".claude" / "fake" / "fake-reject").touch()
        (home / ".config" / "gh" / "fake-approve-after").write_text("0")
        if case.get("expire"):
            (home / ".config" / "gh" / "fake-expire").write_text("0")
        hostvars = hosts[name]
        hostvars.update(
            # Never this machine's browser or clipboard.
            login_desktop=False,
            claude_code_login_desktop=False,
            github_projects_login_desktop=False,
            # Never a unit in this machine's /etc/systemd.
            claude_code_remote_control=False,
            claude_code_login_timeout=8,
            # The async jobs of both sign-ins, under the host's HOME (see
            # test_github_auth.py).
            ansible_async_dir=str(home / ".ansible_async"),
            github_projects_user=ME,
            github_projects_home=str(home),
            github_projects_dir=str(home / "projects"),
        )
        if "code" in case:
            hostvars["claude_code_login_code"] = case["code"]
        if github:
            hostvars.update(github_projects_username=USERNAME, github_projects_api_url=github)
        if case.get("after_fails"):
            # Remote Control on, and its session directory under a plain file:
            # the first thing remote_control.yml changes fails, long before its
            # unit would be installed under /etc/systemd/system.
            (home / "a-file").write_text("")
            hostvars.update(claude_code_remote_control=True, claude_code_remote_control_dir=str(home / "a-file" / "x"))
        if case.get("down"):
            refused = str(bin_dir / "ssh")
            hostvars.update(
                ansible_connection="ssh",
                ansible_ssh_executable=refused,
                ansible_scp_executable=refused,
                ansible_sftp_executable=refused,
                # No ControlPersist, so no socket directory is made for it.
                ansible_ssh_args="",
                ansible_control_path_dir=str(root / "cp"),
            )
        hostvars.update(case.get("vars", {}))
    return hosts


def _extra(**more) -> str:
    """Extra vars for every run here: no discovery, and no sudo on this machine."""
    return json.dumps({"claude_vm_discovery": False, "ansible_become": False, **more})


SIGN_INS = ("playbooks/claude_login.yml", "playbooks/github_login.yml", "playbooks/tailscale_login.yml")


def _deploy_sign_ins(root: Path) -> Path:
    """deploy.yml from its sign-ins on, as it stands: the three imports, their vars and conditions, then its own plays.

    The stand-in VMs here have no Tailscale (no dpkg-query answers for it), so
    its step only reports them: what that step decides is test_tailscale.py's.

    The template, provision, configure and code stages are left out: the
    stand-in VMs are configured already, and nothing here opens VS Code.
    """
    entries = []
    for entry in yaml.safe_load(DEPLOY.read_text()):
        target = entry.get("ansible.builtin.import_playbook")
        if target in SIGN_INS:
            entries.append({**entry, "ansible.builtin.import_playbook": str(REPO / target)})
        elif entries and "hosts" in entry:
            entries.append(entry)
    imported = [entry.get("ansible.builtin.import_playbook") for entry in entries]
    assert imported[:3] == [str(REPO / target) for target in SIGN_INS], imported
    assert len(entries) > 3, "deploy.yml has no play of its own after the sign-ins"
    playbook = root / "deploy_sign_ins.yml"
    playbook.write_text(yaml.safe_dump(entries, sort_keys=False))
    return playbook


def _deploy(root: Path, hosts: dict, deploy_sign_in: bool | None = None) -> dict:
    """just deploy <every host> from its sign-ins on; deploy_sign_in at its default unless given.

    An extra var, so that it reaches every host as group_vars/all does on a
    real run, localhost too: the discovery play each sign-in imports runs there.
    """
    sign_in = DEFAULTS["deploy_sign_in"] if deploy_sign_in is None else deploy_sign_in
    return _run(root, _deploy_sign_ins(root), hosts, "-e", _extra(vm_name=",".join(hosts), deploy_sign_in=sign_in))


def _said(report: dict, task: str, host: str) -> list[str]:
    """Everything the tasks whose name ends with `task` said for host, in order."""
    return [
        result["msg"]
        for name, task_host, result in _results(report)
        if task_host == host and name.endswith(task) and "msg" in result and not result.get("skipped")
    ]


def _ran(report: dict, task: str, host: str) -> bool:
    return any(
        task_host == host and name.endswith(task) and not result.get("skipped")
        for name, task_host, result in _results(report)
    )


def _failed(report: dict) -> dict:
    """What the run failed each host with, rescued failures left out: the last failure of each host, if it stands."""
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


def _play_positions(report: dict, play: str) -> list[int]:
    return [index for index, entry in enumerate(report["plays"]) if entry["play"]["name"] == play]


def _claude_calls(root: Path, host: str) -> list[str]:
    log = root / host / ".claude" / "fake" / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def _gh_calls(root: Path, host: str) -> list[str]:
    log = root / host / ".config" / "gh" / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def _signed_in(root: Path, host: str) -> bool:
    return (root / host / ".claude" / ".credentials.json").exists()


@pytest.fixture(scope="module")
def github(tmp_path_factory):
    """The fake GitHub API on a loopback port: molecule/pub, and molecule/gone, which has no repository behind it."""
    git_root = tmp_path_factory.mktemp("git")
    src = git_root / "src"
    src.mkdir()
    (src / "README.md").write_text("# pub\n")
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": USERNAME,
        "GIT_AUTHOR_EMAIL": f"{USERNAME}@example.com",
        "GIT_COMMITTER_NAME": USERNAME,
        "GIT_COMMITTER_EMAIL": f"{USERNAME}@example.com",
    }
    for argv in (
        ["git", "init", "-q", "-b", "main", str(src)],
        ["git", "-C", str(src), "add", "README.md"],
        ["git", "-C", str(src), "commit", "-q", "-m", "init"],
        ["git", "clone", "-q", "--bare", str(src), str(git_root / "pub.git")],
    ):
        subprocess.run(argv, check=True, env=env, capture_output=True)
    yield from _serve(FAKE_API, USERNAME, str(git_root), "pub", "gone")


# ------------------------------------------------ a VM that cannot be reached


@pytest.fixture(scope="module")
def claude_named(tmp_path_factory):
    """just login alpha beta gamma --only claude: a code claude refuses, a VM that cannot be reached, a sign-in."""
    root = tmp_path_factory.mktemp("claude_named")
    cases = {"alpha": {"code": CODE, "reject": True}, "beta": {"down": True}, "gamma": {"code": CODE}}
    return root, _run(root, CLAUDE_LOGIN, _hosts(root, cases), "-e", _extra(vm_name=",".join(cases)))


@pytest.fixture(scope="module")
def github_named(tmp_path_factory, github):
    """just login alpha,beta,gamma --only github: a code that expires, a VM that cannot be reached, a sign-in."""
    root = tmp_path_factory.mktemp("github_named")
    cases = {"alpha": {"expire": True}, "beta": {"down": True}, "gamma": {}}
    for case in cases.values():
        case["vars"] = {"github_projects_exclude": ["gone"]}
    return root, _run(root, GITHUB_LOGIN, _hosts(root, cases, github), "-e", _extra(vm_name=",".join(cases)))


def test_claude_login_names_a_vm_it_cannot_reach_and_signs_in_the_vms_after_it(claude_named):
    root, report = claude_named
    # gamma's turn came after a refused code and a VM that cannot be reached.
    assert "accepted" in _claude_calls(root, "gamma"), _claude_calls(root, "gamma")
    assert _signed_in(root, "gamma")
    # beta was tried, by the stand-in ssh, said at once, and never handed a sign-in.
    assert (root / "bin" / "ssh.log").exists()
    assert _claude_calls(root, "beta") == []
    assert _said(report, "Say which VM could not be reached", "beta")
    assert report["rc"] != 0
    failures = _failed(report)
    assert set(failures) == {"alpha", "beta"}, failures
    assert failures["beta"].startswith("Fail where the sign-in failed: "), failures["beta"]
    assert "beta could not be reached, so it was not signed in" in failures["beta"]
    assert "Connection refused" in failures["beta"]


def test_github_login_names_a_vm_it_cannot_reach_and_signs_in_the_vms_after_it(github_named):
    root, report = github_named
    # gamma's turn came after an expired code and a VM that cannot be reached.
    calls = _gh_calls(root, "gamma")
    assert any(call.startswith("auth login ") and "--web" in call for call in calls), calls
    assert (root / "gamma" / ".config" / "gh" / "fake-token").read_text().strip() == DEVICE_TOKEN
    # And the work after its sign-in.
    assert (root / "gamma" / "projects" / "pub" / "README.md").exists()
    assert _gh_calls(root, "beta") == []
    assert _said(report, "Say which VM could not be reached", "beta")
    assert report["rc"] != 0
    failures = _failed(report)
    assert set(failures) == {"alpha", "beta"}, failures
    assert failures["beta"].startswith("Fail where the sign-in failed: "), failures["beta"]
    assert "beta could not be reached, so gh was not signed in there" in failures["beta"]
    assert "Connection refused" in failures["beta"]


@pytest.mark.parametrize("fixture", ["claude_named", "github_named"])
def test_a_failed_sign_in_is_said_in_its_turn_and_raised_once_every_vm_had_its_own(request, fixture):
    _, report = request.getfixturevalue(fixture)
    said = [
        (index, result["msg"])
        for index, entry in enumerate(report["plays"])
        for task in entry["tasks"]
        if task["task"]["name"] == "Say why, and go on with the next VM"
        for host, result in task["hosts"].items()
        if host == "alpha" and not result.get("skipped")
    ]
    assert len(said) == 1, said
    index, message = said[0]
    assert message.startswith("alpha: ") and "just login alpha" in message, message
    # Said in alpha's own turn, the batch that held alpha alone, which came
    # before the next VM's.
    turn = report["plays"][index]
    assert turn["play"]["name"] in (CLAUDE_SIGN_IN, GITHUB_SIGN_IN), turn["play"]["name"]
    assert sorted({host for task in turn["tasks"] for host in task["hosts"]}) == ["alpha"]
    assert _failed(report)["alpha"] == "Fail where the sign-in failed: " + message


@pytest.mark.parametrize(("fixture", "matter"), [("claude_named", "claude"), ("github_named", "github")])
def test_next_steps_keep_a_failed_sign_in_and_a_vm_not_reached_with_the_command_that_retries_each(
    request, fixture, matter
):
    # Said, and raised at the end, but recorded nowhere: NEXT STEPS forgot both.
    _, report = request.getfixturevalue(fixture)
    left = _left_to_do(report)
    (failed,) = _said(report, "Say why, and go on with the next VM", "alpha")
    assert left["alpha"][matter] == failed
    assert f"just login alpha --only {matter}" in failed, failed
    (unreached,) = _said(report, "Say which VM could not be reached", "beta")
    assert left["beta"][matter] == unreached
    assert "beta could not be reached" in unreached and "Connection refused." in unreached, unreached
    assert unreached.endswith(f"Retry it with: just login beta --only {matter}"), unreached


def test_next_steps_keep_nothing_of_a_claude_sign_in_that_went_through(claude_named):
    _, report = claude_named
    assert _left_to_do(report)["gamma"]["claude"] == ""


@pytest.fixture(scope="module")
def deployed(tmp_path_factory):
    """just deploy's sign-ins for a-rejected (a code claude refuses), b-down (unreachable) and c-accepted."""
    root = tmp_path_factory.mktemp("deployed")
    cases = {"a-rejected": {"code": CODE, "reject": True}, "b-down": {"down": True}, "c-accepted": {"code": CODE}}
    return root, _deploy(root, _hosts(root, cases))


def test_just_deploy_names_a_vm_it_cannot_reach_in_every_sign_in_and_goes_on(deployed):
    root, report = deployed
    assert report["rc"] == 0, _failed(report)
    assert _failed(report) == {}
    said = _said(report, "Say which VM could not be reached", "b-down")
    assert len(said) == 3, said
    assert "b-down could not be reached, so it was not signed in" in said[0]
    assert "b-down could not be reached, so gh was not signed in there" in said[1]
    assert "b-down could not be reached, so it was not signed in to Tailscale" in said[2]
    # The VM after it still had its turn.
    assert "accepted" in _claude_calls(root, "c-accepted")
    assert _signed_in(root, "c-accepted")


def test_just_deploy_keeps_each_sign_in_a_vm_missed_for_next_steps(deployed):
    # deploy raises no failed sign-in at its end: NEXT STEPS is where it stays.
    _, report = deployed
    left = _left_to_do(report)
    said = _said(report, "Say which VM could not be reached", "b-down")
    assert [left["b-down"][matter] for matter in ("claude", "github", "tailscale")] == said, (left["b-down"], said)
    for matter, line in zip(("claude", "github", "tailscale"), said, strict=True):
        assert line.endswith(f"Retry it with: just login b-down --only {matter}"), line
    assert left["a-rejected"]["claude"] == _said(report, "Say why, and go on with the next VM", "a-rejected")[0]
    assert "just login a-rejected --only claude" in left["a-rejected"]["claude"]
    assert left["c-accepted"]["claude"] == ""


# ------------------------------------------- the work after a sign-in fails


@pytest.fixture(scope="module")
def after_claude(tmp_path_factory):
    """just login epsilon --only claude, signed in by its code, whose Remote Control then fails to start."""
    if os.geteuid() == 0:
        pytest.skip("as root, a change to remote_control.yml could install a unit on this machine")
    root = tmp_path_factory.mktemp("after_claude")
    hosts = _hosts(root, {"epsilon": {"code": CODE, "after_fails": True}})
    return root, _run(root, CLAUDE_LOGIN, hosts, "-e", _extra(vm_name="epsilon"))


@pytest.fixture(scope="module")
def deployed_after(tmp_path_factory):
    """just deploy's sign-ins for a-rejected (a code claude refuses) and b-after (Remote Control fails after them)."""
    if os.geteuid() == 0:
        pytest.skip("as root, a change to remote_control.yml could install a unit on this machine")
    root = tmp_path_factory.mktemp("deployed_after")
    cases = {"a-rejected": {"code": CODE, "reject": True}, "b-after": {"code": CODE, "after_fails": True}}
    return root, _deploy(root, _hosts(root, cases))


@pytest.fixture(scope="module")
def after_github(tmp_path_factory, github):
    """just login delta --only github: the device flow signs gh in, then the clone of molecule/gone fails."""
    root = tmp_path_factory.mktemp("after_github")
    return root, _run(root, GITHUB_LOGIN, _hosts(root, {"delta": {}}, github), "-e", _extra(vm_name="delta"))


def _remote_control_failed(message: str, host: str) -> None:
    assert f"{host} is signed in to Claude, but starting Remote Control failed at " in message, message
    assert "remote_control | Ensure the session's working directory exists" in message, message
    assert f"just deploy {host} --tags claude_code" in message, message


def test_a_failure_after_the_claude_sign_in_is_said_as_such_and_fails_just_login_only_claude(after_claude):
    root, report = after_claude
    # Signed in: what failed came after.
    assert _signed_in(root, "epsilon")
    assert not _ran(report, "Remember why this VM's sign-in failed", "epsilon")
    assert report["rc"] != 0
    failures = _failed(report)
    assert set(failures) == {"epsilon"}, failures
    assert failures["epsilon"].startswith("Fail where the work after the sign-in failed: "), failures["epsilon"]
    _remote_control_failed(failures["epsilon"], "epsilon")
    # Never as far as a unit on this machine.
    assert not _ran(report, "remote_control | Install the systemd unit", "epsilon")


def test_just_deploy_fails_at_its_end_on_a_failure_after_a_sign_in_and_not_on_a_failed_sign_in(deployed_after):
    root, report = deployed_after
    assert _signed_in(root, "b-after")
    # The sign-in play caught a-rejected's refused code, and nothing of b-after.
    assert _ran(report, "Remember why this VM's sign-in failed", "a-rejected")
    assert not _ran(report, "Remember why this VM's sign-in failed", "b-after")
    recorded = [
        result["ansible_facts"]["claude_vm_configure_failures"]
        for name, host, result in _results(report)
        if host == "b-after" and name.endswith("Remember that the work after the sign-in failed")
    ]
    assert len(recorded) == 1 and len(recorded[0]) == 1, recorded
    _remote_control_failed(recorded[0][0], "b-after")
    # Raised by deploy.yml's own last play, after everything else ran.
    assert report["rc"] != 0
    failures = _failed(report)
    assert set(failures) == {"b-after"}, failures
    assert failures["b-after"].startswith("Fail where the work after a sign-in failed: "), failures["b-after"]
    _remote_control_failed(failures["b-after"], "b-after")


def test_a_clone_failure_after_the_device_flow_names_the_repository_and_how_to_retry(after_github):
    root, report = after_github
    assert (root / "delta" / ".config" / "gh" / "fake-token").read_text().strip() == DEVICE_TOKEN
    assert (root / "delta" / "projects" / "pub" / "README.md").exists()
    assert not _ran(report, "Remember why this VM's sign-in failed", "delta")
    assert report["rc"] != 0
    failures = _failed(report)
    assert set(failures) == {"delta"}, failures
    message = failures["delta"]
    assert message.startswith("Fail where the work after the sign-in failed: "), message
    assert 'configuring its GitHub repositories failed at "clone | Clone repositories"' in message, message
    assert f"{USERNAME}/gone" in message and f"{USERNAME}/pub" not in message, message
    assert "just deploy delta --tags github_projects" in message, message


@pytest.mark.parametrize("fixture", ["after_claude", "after_github"])
def test_the_reason_ends_its_sentence_and_the_message_ends_with_the_command_that_retries_it(request, fixture):
    # A module's message often has no full stop of its own: "One or more items
    # failed" ran on into "Retry it with".
    _, report = request.getfixturevalue(fixture)
    (message,) = _failed(report).values()
    assert re.search(r"[.!?] Retry it with: just deploy \S+ --tags \w+$", message), message


@pytest.mark.parametrize(
    ("fixture", "host", "matter"),
    [
        ("after_claude", "epsilon", "remote_control"),
        ("after_github", "delta", "github"),
        ("deployed_after", "b-after", "remote_control"),
    ],
)
def test_next_steps_keep_a_failure_after_a_sign_in_with_the_command_that_retries_it(request, fixture, host, matter):
    _, report = request.getfixturevalue(fixture)
    (said,) = _said(report, "Say why", host)
    assert _left_to_do(report)[host][matter] == said
    assert re.search(rf"Retry it with: just deploy {host} --tags \w+$", said), said


# ------------------------------------------------ just deploy's switches


@pytest.fixture(scope="module")
def unattended(tmp_path_factory):
    """just deploy </dev/null from the sign-ins on, for a signed-out VM: no code, and no terminal to ask at."""
    root = tmp_path_factory.mktemp("unattended")
    return root, _deploy(root, _hosts(root, {"alpha": {}}))


@pytest.fixture(scope="module")
def switched_off(tmp_path_factory):
    """just deploy with deploy_sign_in: false, for a VM whose code would sign it in."""
    root = tmp_path_factory.mktemp("switched_off")
    return root, _deploy(root, _hosts(root, {"alpha": {"code": CODE}}), deploy_sign_in=False)


def test_an_unattended_deploy_signs_nothing_in_and_fails_nothing(unattended):
    root, report = unattended
    assert report["rc"] == 0, _failed(report)
    assert _failed(report) == {}
    calls = _claude_calls(root, "alpha")
    # The sign-in ran, at deploy_sign_in's default; with nothing entered its
    # claude was ended rather than left waiting.
    assert "auth login" in calls and "TERM" in calls and "accepted" not in calls, calls
    assert not _signed_in(root, "alpha")
    assert _said(report, "Say that no code was entered", "alpha")
    assert not any(call.startswith("auth login") for call in _gh_calls(root, "alpha"))


def test_deploy_signs_in_by_default():
    # The unattended test above runs at this default, and needs the sign-in
    # to run; just deploy is documented as ending signed in.
    assert DEFAULTS["deploy_sign_in"] is True


def test_deploy_sign_in_false_runs_neither_sign_in(switched_off):
    root, report = switched_off
    assert report["rc"] == 0, _failed(report)
    assert _claude_calls(root, "alpha") == []
    assert _gh_calls(root, "alpha") == []
    assert not _signed_in(root, "alpha")


def test_just_deploy_names_a_refused_code_and_signs_in_the_vm_after_it(deployed):
    root, report = deployed
    assert report["rc"] == 0, _failed(report)
    said = _said(report, "Say why, and go on with the next VM", "a-rejected")
    assert len(said) == 1, said
    assert "a-rejected: the Claude sign-in did not complete" in said[0]
    assert "just login a-rejected --only claude again" in said[0]
    assert not _signed_in(root, "a-rejected")
    assert _signed_in(root, "c-accepted")


# --------------------------------------------- just login: all three in turn


@pytest.fixture(scope="module")
def logged_in(tmp_path_factory, github):
    """just login for a-unanswered (no code), b-accepted, c-rejected (a code claude refuses) and
    d-after (signed in, then Remote Control fails to start)."""
    if os.geteuid() == 0:
        pytest.skip("as root, a change to remote_control.yml could install a unit on this machine")
    root = tmp_path_factory.mktemp("logged_in")
    cases = {
        "a-unanswered": {},
        "b-accepted": {"code": CODE},
        "c-rejected": {"code": CODE, "reject": True},
        "d-after": {"code": CODE, "after_fails": True},
    }
    for case in cases.values():
        case["vars"] = {**case.get("vars", {}), "github_projects_exclude": ["gone"]}
    hosts = _hosts(root, cases, github)
    return root, _run(root, LOGIN, hosts, "-e", _extra(vm_name=",".join(cases)))


def _device_flow(root: Path, host: str) -> bool:
    return any(call.startswith("auth login ") and "--web" in call for call in _gh_calls(root, host))


def test_just_login_signs_in_to_github_after_claude(logged_in):
    root, report = logged_in
    assert _signed_in(root, "b-accepted")
    assert _device_flow(root, "b-accepted")
    assert (root / "b-accepted" / "projects" / "pub" / "README.md").exists()
    # Claude first, then GitHub, then Tailscale, every VM's turn of one before the next.
    claude, github, tailscale = (
        _play_positions(report, play) for play in (CLAUDE_SIGN_IN, GITHUB_SIGN_IN, TAILSCALE_SIGN_IN)
    )
    assert claude and github and tailscale, (claude, github, tailscale)
    assert max(claude) < min(github) and max(github) < min(tailscale)


def test_just_login_starts_no_device_flow_for_a_vm_whose_claude_sign_in_nobody_answered(logged_in):
    root, report = logged_in
    assert not _signed_in(root, "a-unanswered")
    assert _said(report, "Say that no code was entered", "a-unanswered")
    assert not any(call.startswith("auth login") for call in _gh_calls(root, "a-unanswered"))
    # Its GitHub step ran, and only reported.
    assert _ran(report, "Report the login state, and sign the VM in when asked to", "a-unanswered")


def test_just_login_says_once_why_github_s_sign_in_was_not_started_and_only_for_that_vm(logged_in):
    # The role's report says only "not signed in"; this says why nothing
    # signed it in, and what does.
    _, report = logged_in
    said = {host: _said(report, "Say why GitHub's sign-in was not started", host) for host in report["stats"]}
    assert {host: lines for host, lines in said.items() if lines} == {
        "a-unanswered": [
            "a-unanswered: GitHub's sign-in was not started, since its Claude sign-in was skipped:"
            " just login a-unanswered --only github starts it."
        ]
    }, said
    # NEXT STEPS holds one GitHub line for it, naming that command; the whole
    # role, which just login with names runs after, may say it last.
    assert "just login a-unanswered --only github" in _left_to_do(report)["a-unanswered"]["github"]


def test_just_login_keeps_what_failed_for_next_steps(logged_in):
    _, report = logged_in
    left = _left_to_do(report)
    assert "just login c-rejected --only claude" in left["c-rejected"]["claude"]
    assert left["d-after"]["remote_control"].endswith("Retry it with: just deploy d-after --tags claude_code")
    assert left["b-accepted"]["claude"] == "" and left["d-after"]["claude"] == ""


def test_just_login_goes_on_past_a_failure_as_deploy_does_and_fails_at_the_end(logged_in):
    # A refused code, or Remote Control failing to start after a sign-in, is no
    # reason to leave the VM signed out of GitHub and Tailscale: under just
    # deploy neither is. Both are raised by login.yml's own last play, once
    # every VM has had all three turns, with what each step said.
    root, report = logged_in
    assert report["rc"] != 0
    failures = _failed(report)
    assert set(failures) == {"c-rejected", "d-after"}, failures
    prefix = "Fail where a sign-in, or the work after one, failed: "
    assert failures["c-rejected"].startswith(prefix + "c-rejected: "), failures["c-rejected"]
    assert "starting Remote Control failed" in failures["d-after"], failures["d-after"]
    assert failures["d-after"].endswith("Retry it with: just deploy d-after --tags claude_code"), failures["d-after"]
    assert not _signed_in(root, "c-rejected") and _signed_in(root, "d-after")
    for host in ("c-rejected", "d-after"):
        assert _device_flow(root, host), host
        assert any(host in batch for batch in _batches(report, TAILSCALE_SIGN_IN)), host
    # Raised once, at the end, and by no sign-in's own last play.
    assert _play_positions(report, "Fail each VM whose sign-in, or the work after one, failed") == [
        len(report["plays"]) - 1
    ]


def test_just_login_without_names_reports_every_vm_and_starts_nothing(tmp_path, github):
    cases = {"alpha": {"code": CODE}, "beta": {}}
    for case in cases.values():
        case["vars"] = {"github_projects_exclude": ["gone"]}
    # As if nobody had answered its Claude sign-in: a report still says nothing of it.
    cases["beta"]["vars"]["claude_code_login_skipped"] = True
    report = _run(tmp_path, LOGIN, _hosts(tmp_path, cases, github), "-e", _extra())
    assert report["rc"] == 0, _failed(report)
    for play in (CLAUDE_SIGN_IN, GITHUB_SIGN_IN, TAILSCALE_SIGN_IN):
        assert _batches(report, play) == [["alpha", "beta"]], play
    for host in cases:
        assert not _signed_in(tmp_path, host)
        assert "auth login" not in _claude_calls(tmp_path, host)
        assert not any(call.startswith("auth login") for call in _gh_calls(tmp_path, host))
        # The role's work after a sign-in follows only a sign-in, or names.
        assert not _ran(report, "Run the role now that gh is signed in", host)
        assert not _ran(report, "Say why GitHub's sign-in was not started", host)


def test_just_login_imports_the_three_sign_ins_as_they_are():
    entries = yaml.safe_load(LOGIN.read_text())
    imported = [
        entry["ansible.builtin.import_playbook"] for entry in entries if "ansible.builtin.import_playbook" in entry
    ]
    assert imported == ["discover.yml", "claude_login.yml", "github_login.yml", "tailscale_login.yml"]
    for entry in entries[1:4]:
        assert entry["vars"]["claude_vm_login_raised_later"] is True, entry["name"]
    assert "hosts" in entries[-1], "login.yml raises what the sign-ins left in a play of its own, last"


def _flat(expression: str) -> str:
    """A Jinja expression as one line, without its braces."""
    text = " ".join(expression.split())
    assert text.startswith("{{ ") and text.endswith(" }}"), text
    return text[3:-3]


def _vars_of_import(path: Path, playbook: str) -> dict:
    """The vars a playbook gives its import of playbook (named relative to the importing file)."""
    return next(e for e in yaml.safe_load(path.read_text()) if e.get("ansible.builtin.import_playbook") == playbook)[
        "vars"
    ]


@pytest.mark.parametrize("step", ["github_login.yml", "tailscale_login.yml"])
@pytest.mark.parametrize("decision", ["claude_vm_login_interactive", "claude_vm_login_ask"])
def test_just_login_decides_github_and_tailscale_as_just_deploy_does(step, decision):
    """login.yml's decisions are deploy.yml's own, behind the names given, so the two cannot drift."""
    deploy = _flat(_vars_of_import(DEPLOY, f"playbooks/{step}")[decision])
    login = _flat(_vars_of_import(LOGIN, step)[decision])
    assert login == f"claude_vm_login_named | bool and ({deploy})", (login, deploy)


# ------------------------------------------------------------- batches


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    """Both playbooks with no names, as just login --only claude and just login --only github report on the fleet."""
    out = {}
    for playbook in (CLAUDE_LOGIN, GITHUB_LOGIN):
        root = tmp_path_factory.mktemp("report_" + playbook.stem)
        hosts = _hosts(root, {"alpha": {}, "beta": {}, "gamma": {}})
        out[playbook] = _run(root, playbook, hosts, "-e", _extra())
    return out


@pytest.mark.parametrize(("playbook", "play"), [(CLAUDE_LOGIN, CLAUDE_SIGN_IN), (GITHUB_LOGIN, GITHUB_SIGN_IN)])
def test_a_report_reads_every_vm_in_one_batch(reports, playbook, play):
    report = reports[playbook]
    assert report["rc"] == 0, _failed(report)
    assert _batches(report, play) == [["alpha", "beta", "gamma"]]


@pytest.mark.parametrize(
    ("fixture", "play"),
    [
        ("claude_named", CLAUDE_SIGN_IN),
        ("github_named", GITHUB_SIGN_IN),
        ("deployed", CLAUDE_SIGN_IN),
        ("deployed", GITHUB_SIGN_IN),
    ],
)
def test_a_sign_in_takes_one_vm_per_batch(request, fixture, play):
    _, report = request.getfixturevalue(fixture)
    vms = sorted(set(report["stats"]) - {"localhost"})
    assert len(vms) == 3, vms
    assert _batches(report, play) == [[vm] for vm in vms]


@pytest.mark.parametrize(
    ("fixture", "sign_in", "after"),
    [("claude_named", CLAUDE_SIGN_IN, CLAUDE_AFTER), ("github_named", GITHUB_SIGN_IN, GITHUB_AFTER)],
)
def test_the_work_after_the_sign_ins_waits_for_the_last_one_then_runs_for_every_vm(request, fixture, sign_in, after):
    _, report = request.getfixturevalue(fixture)
    assert _batches(report, after) == [["alpha", "beta", "gamma"]]
    assert _play_positions(report, after)[0] > max(_play_positions(report, sign_in))


# ------------------------------------------------- switches no longer read


@pytest.mark.parametrize(("switch", "value"), [("github_login_desktop", False), ("github_login_prompt", True)])
def test_github_login_refuses_a_switch_it_no_longer_reads(tmp_path, switch, value):
    hosts = _hosts(tmp_path, {"alpha": {}, "beta": {}})
    report = _run(tmp_path, GITHUB_LOGIN, hosts, "-e", _extra(**{switch: value}))
    assert report["rc"] != 0
    failures = _failed(report)
    assert len(failures) == 1, failures
    message = next(iter(failures.values()))
    assert switch in message, message
    # The switch that replaced it, as a name of its own and not only inside
    # github_login_desktop.
    assert re.search(r"(?<![\w])login_desktop\b", message), message
    # Refused before any VM was asked anything.
    for host in hosts:
        assert _gh_calls(tmp_path, host) == [], host
