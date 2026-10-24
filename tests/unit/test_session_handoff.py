"""The pieces of make code's session handoff: filter_plugins/sessions.py and the modules in playbooks/library/.

Which session goes to which checkout on the VM, what a copy is made of, and
when an existing copy is left alone. Every transcript, ~/.claude and checkout
here is built for the test in its own directory, never the developer's; the
git checkouts are real ones, with origins that only look like GitHub's. The
window step that runs these (playbooks/tasks/session_read.yml and
session_write.yml) is run in test_code_session.py.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest
from ansible.errors import AnsibleFilterError
from conftest import load_module, run_main

REPO = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("session_filters", REPO / "filter_plugins" / "sessions.py")
filters = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(filters)
info = load_module("playbooks/library/claude_sessions_info.py")
checkouts = load_module("playbooks/library/git_checkouts_info.py")
copy = load_module("playbooks/library/claude_sessions_copy.py")

ID = "0a608ad8-1b2c-4d3e-8f90-123456789abc"
OTHER = "bc0e637b-2c3d-4e4f-9a01-23456789abcd"
HERE = "/Users/me/.claude"
MAC_MEMORY = f"{HERE}/projects/-Users-me-demo/memory/"
VM_MEMORY = "/home/dev/.claude/projects/-home-dev-projects-demo/memory/"
CHECKOUT = "/home/dev/projects/demo"
# What a tool printed, as a transcript holds it: it must never reach a run's output.
MARKER = "TRANSCRIPT-MARKER-6f1d2c"


def transcript(
    sid=ID,
    cwd="/Users/me/demo",
    *,
    version="2.1.308",
    branch="main",
    memory=MAC_MEMORY,
    worktree=None,
    conversation=True,
    partial=False,
    snapshot=True,
):
    """A transcript as Claude Code 2.1.308 writes one, compact JSON per line."""
    base = {"cwd": cwd, "sessionId": sid, "version": version, "gitBranch": branch, "userType": "external"}
    entries = []
    if snapshot:
        prompt = f"You have a persistent file-based memory at `{memory}`. This directory already exists."
        entries.append(
            {**base, "type": "attachment", "attachment": {"type": "prompt_snapshot", "systemPrompt": [prompt]}}
        )
    if conversation:
        entries.append({**base, "type": "user", "message": {"role": "user", "content": f"run the tests {MARKER}"}})
        entries.append(
            {**base, "type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}}
        )
    else:
        entries.append({**base, "type": "summary", "summary": "nothing"})
    if worktree is not None:
        entries.append({"type": "worktree-state", "worktreeSession": worktree, "sessionId": sid})
    text = "".join(json.dumps(entry, separators=(",", ":")) + "\n" for entry in entries)
    return (text + '{"type":"user","cwd":"/elsewh').encode() if partial else text.encode()


@pytest.fixture(autouse=True)
def _own_git_config(monkeypatch):
    # Never the developer's own, which changes what git says: status.showUntrackedFiles, a hook, an alias.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def lines(data):
    return [json.loads(line) for line in data.decode().splitlines()]


# ------------------------------------------------------------------------------------------ the filters ---
@pytest.mark.parametrize(
    ("path", "folder"),
    [
        ("/home/dev/projects/homeserver", "-home-dev-projects-homeserver"),
        ("/home/dev/projects/my_app.v2", "-home-dev-projects-my-app-v2"),
        ("/Users/Omkar/claude-on-proxmox", "-Users-Omkar-claude-on-proxmox"),
    ],
)
def test_the_folder_name_is_claude_code_s(path, folder):
    assert filters.claude_project_folder(path) == folder


def test_a_folder_name_claude_code_would_shorten_is_refused():
    with pytest.raises(AnsibleFilterError, match="too long"):
        filters.claude_project_folder("/home/dev/projects/" + "x" * 190)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Octo/Demo.git",
        "https://github.com/octo/demo",
        "https://token@github.com/octo/demo/",
        "git@github.com:octo/demo.git",
        "ssh://git@github.com/octo/demo.git",
        "git@github-work:octo/demo",
        "/srv/git/octo/demo.git",
    ],
)
def test_an_origin_names_owner_and_repository_in_every_form(url):
    assert filters.git_repository(url) == "octo/demo"


@pytest.mark.parametrize("url", ["", "demo", "https://github.com/", "git@github.com:demo"])
def test_an_origin_with_no_owner_names_nothing(url):
    assert filters.git_repository(url) == ""


def test_the_dirs_asked_about_are_each_first_directory_and_its_worktree_s_checkout():
    sessions = [
        {"cwd": "/Users/me/demo"},
        {"cwd": "/Users/me/demo/.claude/worktrees/fix-x/src"},
        {"cwd": ""},
        {"cwd": "/Users/me/demo"},
        # A worktree of a checkout no session ran in: the checkout is asked about too.
        {"cwd": "/Users/me/other/.claude/worktrees/fix-y"},
    ]
    assert filters.claude_session_dirs(sessions) == [
        "/Users/me/demo",
        "/Users/me/demo/.claude/worktrees/fix-x/src",
        "/Users/me/other/.claude/worktrees/fix-y",
        "/Users/me/other",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Octo/Demo.git",
        "https://x-access-token@github.com/octo/demo/",
        "git@github.com:octo/demo.git",
        "github.com:octo/demo",
        "ssh://git@github.com/octo/demo.git",
        "ssh://git@ssh.github.com:443/octo/demo.git",
    ],
)
def test_on_the_vm_a_github_origin_names_its_repository(url):
    assert filters.github_repository(url) == "octo/demo"


@pytest.mark.parametrize(
    "url",
    [
        "/srv/git/octo/demo.git",  # a local path names folders, not a repository
        "file:///srv/git/octo/demo.git",
        "https://gitlab.com/octo/demo.git",  # another host's repository of the same name
        "git@github-work:octo/demo",  # an SSH alias: the VM's checkouts are make deploy's, which uses none
        "https://github.com.example.com/octo/demo",
        "https://github.com/octo/demo/tree/main",
        "https://github.com/../demo",
        "",
    ],
)
def test_on_the_vm_no_other_origin_names_a_repository(url):
    assert filters.github_repository(url) == ""


def test_a_vm_checkout_names_its_repository_only_by_a_github_origin():
    # The VM could otherwise claim any repository of this machine's by a path ending in its owner and name.
    here = [{"path": "/Users/me/demo", "exists": True, "origin": "git@github-work:octo/demo.git"}]
    claimed = [{"path": CHECKOUT, "name": "demo", "origin": "/srv/git/octo/demo.git"}]
    assert _plan([_session()], here, claimed)["sessions"] == []
    assert [item["checkout"] for item in _plan([_session()], here)["sessions"]] == [CHECKOUT]


def _session(sid=ID, cwd="/Users/me/demo", **extra):
    return {
        "id": sid,
        "folder": "-Users-me-demo",
        "cwd": cwd,
        "version": "2.1.308",
        "branch": "main",
        "conversation": True,
        "open": 0,
        "tools": False,
        **extra,
    }


THERE = [{"path": CHECKOUT, "name": "demo", "origin": "https://github.com/octo/demo.git"}]


def _plan(sessions, here, there=THERE, version="2.1.308", repo_paths=None, **kwargs):
    return filters.claude_sessions_plan(sessions, here, there, version, repo_paths or {}, "/home/dev/.claude", **kwargs)


def _placed(plan):
    """The sessions a plan copies, and those it leaves out for a reason make code says."""
    return plan["sessions"] + plan["newer"] + plan["ambiguous"] + plan["twice"] + plan["open"]


def test_a_session_goes_to_the_checkout_whose_origin_names_its_repository():
    here = [{"path": "/Users/me/demo", "exists": True, "origin": "git@github.com:Octo/Demo.git"}]
    plan = _plan([_session(tools=True)], here)
    assert plan["sessions"] == [{"id": ID, "folder": "-Users-me-demo", "checkout": CHECKOUT, "memory": VM_MEMORY}]
    assert plan["files"] == [f"-Users-me-demo/{ID}.jsonl", f"-Users-me-demo/{ID}/tool-results"]
    assert plan["repositories"] == ["octo/demo"]


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/someone-else/demo.git",  # another owner's repository of the same name
        "https://github.com/octo/demo-api.git",  # a name that only starts the same
        "",  # no origin
    ],
)
def test_another_repository_is_not_copied(origin):
    here = [{"path": "/Users/me/demo", "exists": True, "origin": origin}]
    assert _placed(_plan([_session()], here)) == []


def test_a_folder_named_like_a_checkout_of_another_repository_does_not_match():
    # The VM's demo folder holds someone else's demo: the name alone says nothing.
    here = [{"path": "/Users/me/demo", "exists": True, "origin": "https://github.com/octo/demo.git"}]
    there = [{"path": CHECKOUT, "name": "demo", "origin": "https://github.com/someone-else/demo.git"}]
    assert _plan([_session()], here, there)["sessions"] == []


def test_a_renamed_clone_matches_by_origin():
    here = [{"path": "/Users/me/discord-music-bot-2", "exists": True, "origin": "https://github.com/octo/demo"}]
    plan = _plan([_session(cwd="/Users/me/discord-music-bot-2")], here)
    assert [item["checkout"] for item in plan["sessions"]] == [CHECKOUT]


def test_a_gone_worktree_resolves_through_its_checkout():
    cwd = "/Users/me/demo/.claude/worktrees/fix-x"
    here = [
        {"path": cwd, "exists": False, "origin": ""},
        {"path": "/Users/me/demo", "exists": True, "origin": "https://github.com/octo/demo.git"},
    ]
    assert [item["id"] for item in _plan([_session(cwd=cwd)], here)["sessions"]] == [ID]


def test_a_gone_clone_resolves_through_github_repo_paths_and_nothing_else_does():
    here = [{"path": "/Users/me/old-demo", "exists": False, "origin": ""}]
    listed = {"Octo/Demo": ["/Users/me/old-demo"]}
    assert len(_plan([_session(cwd="/Users/me/old-demo")], here, repo_paths=listed)["sessions"]) == 1
    assert _placed(_plan([_session(cwd="/Users/me/old-demo")], here)) == []


def test_a_session_with_no_directory_or_no_message_is_not_copied():
    assert _placed(_plan([_session(cwd=""), _session(OTHER, conversation=False)], [])) == []


def test_a_session_newer_than_the_vm_s_claude_is_left_and_named():
    here = [{"path": "/Users/me/demo", "exists": True, "origin": "https://github.com/octo/demo.git"}]
    plan = _plan([_session(version="2.1.310"), _session(OTHER, version="2.1.9")], here, version="2.1.308")
    assert plan["newer"] == [ID] and [item["id"] for item in plan["sessions"]] == [OTHER]


def test_without_claude_on_the_vm_nothing_is_copied():
    here = [{"path": "/Users/me/demo", "exists": True, "origin": "https://github.com/octo/demo.git"}]
    plan = _plan([_session()], here, version="")
    assert plan["sessions"] == [] and plan["claude"] is False


def test_two_checkouts_of_one_repository_take_the_one_named_after_it_or_neither():
    here = [{"path": "/Users/me/demo", "exists": True, "origin": "https://github.com/octo/demo.git"}]
    both = [*THERE, {"path": "/home/dev/projects/demo-copy", "name": "demo-copy", "origin": "git@github.com:octo/demo"}]
    assert [item["checkout"] for item in _plan([_session()], here, both)["sessions"]] == [CHECKOUT]
    neither = [{**item, "name": item["name"] + "-x", "path": item["path"] + "-x"} for item in both]
    plan = _plan([_session()], here, neither)
    assert plan["sessions"] == [] and plan["ambiguous"] == [
        {"id": ID, "repository": "octo/demo", "names": "demo-copy-x, demo-x"}
    ]


HERE_DEMO = [{"path": "/Users/me/demo", "exists": True, "origin": "https://github.com/octo/demo.git"}]


def test_an_id_in_two_folders_here_is_left_out_and_named():
    # claude --resume <id> resumes from neither once both are on the VM.
    twice = {**_session(), "folder": "-Users-me-demo-src"}
    plan = _plan([_session(), twice, _session(OTHER)], HERE_DEMO)
    assert plan["twice"] == [ID] and [item["id"] for item in plan["sessions"]] == [OTHER]


def test_a_session_a_claude_on_the_vm_has_open_is_left_as_it_is():
    # Replaced under the claude holding it, the copy would take that claude's next lines onto other content.
    plan = _plan([_session(), _session(OTHER)], HERE_DEMO, open_there=[ID])
    assert plan["open"] == [ID] and [item["id"] for item in plan["sessions"]] == [OTHER]


# ------------------------------------------------------------------------ claude_sessions_info ---
def _claude_dir(tmp_path, files, *, old=()):
    """A ~/.claude with these transcripts: {(folder, id): bytes}; the ``old`` ones written 40 days ago."""
    root = tmp_path / ".claude"
    root.mkdir(parents=True, exist_ok=True)
    for (folder, sid), data in files.items():
        path = root / "projects" / folder / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if sid in old:
            then = time.time() - 40 * 86400
            os.utime(path, (then, then))
    return root


def test_a_transcript_is_summarized_and_nothing_it_says_is_kept(tmp_path):
    root = _claude_dir(tmp_path, {("-Users-me-demo", ID): transcript(branch="feat/x", partial=True)})
    (root / "projects" / "-Users-me-demo" / ID / "tool-results").mkdir(parents=True)
    found = info.list_sessions(str(root), 30)
    assert found == [
        {
            "id": ID,
            "folder": "-Users-me-demo",
            "mtime": found[0]["mtime"],
            "open": 0,
            "tools": True,
            "dir": True,
            "cwd": "/Users/me/demo",
            "version": "2.1.308",
            "branch": "feat/x",
            "conversation": True,
        }
    ]
    assert MARKER not in json.dumps(found)


def test_the_newest_version_is_kept_whatever_its_place(tmp_path):
    data = transcript(version="2.1.310") + transcript(version="2.1.9")
    root = _claude_dir(tmp_path, {("-Users-me-demo", ID): data})
    assert info.list_sessions(str(root), 30)[0]["version"] == "2.1.310"


def test_only_the_sessions_of_the_window_are_listed(tmp_path):
    root = _claude_dir(
        tmp_path,
        {("-Users-me-demo", ID): transcript(), ("-Users-me-demo", OTHER): transcript(OTHER), ("-x", "notes"): b"{}\n"},
        old=(OTHER,),
    )
    assert [item["id"] for item in info.list_sessions(str(root), 30)] == [ID]
    assert info.list_sessions(str(root), 0) == []
    # A stub with no message is listed, and says so; the plan leaves it out.
    root = _claude_dir(tmp_path / "stub", {("-Users-me-demo", ID): transcript(conversation=False)})
    assert info.list_sessions(str(root), 30)[0]["conversation"] is False


@pytest.fixture
def a_claude():
    """A process whose argv is claude's, as `ps` and /proc show one: its pid."""
    process = subprocess.Popen(["claude", "600"], executable="/bin/sleep")
    yield process.pid
    process.kill()
    process.wait()


def test_an_open_session_is_one_a_running_claude_registered(tmp_path, a_claude):
    third = "c1d2e3f4-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
    files = {("-Users-me-demo", sid): transcript(sid) for sid in (ID, OTHER, third)}
    root = _claude_dir(tmp_path, files)
    sessions = root / "sessions"
    sessions.mkdir()
    (sessions / f"{a_claude}.json").write_text(json.dumps({"pid": a_claude, "sessionId": ID}))
    # A pid nothing runs as: the registry entry of a claude that exited without removing it.
    dead = subprocess.run(["sh", "-c", "echo $$"], capture_output=True, text=True, check=True).stdout.strip()
    (sessions / f"{dead}.json").write_text(json.dumps({"pid": int(dead), "sessionId": OTHER}))
    # A pid another program took since: this very pytest.
    (sessions / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": third}))
    (sessions / "3.json").write_text("not json")
    found = {item["id"]: item["open"] for item in info.list_sessions(str(root), 30)}
    assert found == {ID: a_claude, OTHER: 0, third: 0}
    # The registry alone, whatever transcripts there are.
    assert info.open_sessions(str(root)) == {ID: a_claude}
    assert not info._is_claude(["python3", "-m", "pytest"]) and info._is_claude(
        ["node", "/x/@anthropic-ai/claude-code/cli.js"]
    )


def test_a_claude_s_command_line_is_read_from_proc_where_there_is_one(tmp_path):
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "42").mkdir()
    (proc / "42" / "cmdline").write_bytes(b"/home/dev/.local/bin/claude\0--resume\0x\0")
    assert info._argv(42, str(proc)) == ["/home/dev/.local/bin/claude", "--resume", "x"]
    assert info._argv(43, str(proc)) == []


def test_the_registry_is_returned_whatever_the_transcripts(tmp_path, patch_module, a_claude):
    root = _claude_dir(tmp_path, {})
    (root / "sessions").mkdir()
    (root / "sessions" / f"{a_claude}.json").write_text(json.dumps({"pid": a_claude, "sessionId": ID}))
    result = run_main(info.main, {"path": str(root)})
    assert result["registered"] == {ID: a_claude} and result["sessions"] == []


def test_a_transcript_that_goes_as_it_is_listed_is_not_listed(tmp_path, monkeypatch):
    root = _claude_dir(tmp_path, {("-Users-me-demo", ID): transcript()})
    real = os.scandir

    class Gone:
        name, path = f"{OTHER}.jsonl", str(root / "projects" / "-Users-me-demo" / f"{OTHER}.jsonl")

        def is_file(self, follow_symlinks=True):
            return True

        def stat(self, follow_symlinks=True):
            raise FileNotFoundError(self.path)

    def scandir(path):
        entries = list(real(path))
        return entries + [Gone()] if str(path).endswith("-Users-me-demo") else entries

    monkeypatch.setattr(info.os, "scandir", scandir)
    assert [item["id"] for item in info.list_sessions(str(root), 30)] == [ID]


def test_github_repo_paths_come_from_the_claude_json_beside_the_directory_and_only_they(tmp_path):
    root = _claude_dir(tmp_path, {})
    (tmp_path / ".claude.json").write_text(
        json.dumps({"githubRepoPaths": {"octo/demo": ["/Users/me/demo", 3]}, "oauthAccount": {"emailAddress": "x"}})
    )
    assert info.repo_paths(str(root)) == {"octo/demo": ["/Users/me/demo"]}
    # CLAUDE_CONFIG_DIR keeps it inside the directory instead.
    (root / ".claude.json").write_text(json.dumps({"githubRepoPaths": {"octo/other": ["/x"]}}))
    assert info.repo_paths(str(root)) == {"octo/other": ["/x"]}
    # One there without the key is not the one Claude Code keeps it in: the next is read.
    (root / ".claude.json").write_text(json.dumps({"numStartups": 3}))
    assert info.repo_paths(str(root)) == {"octo/demo": ["/Users/me/demo"]}


def test_claude_s_version_comes_from_the_first_executable_there_is(tmp_path):
    claude = tmp_path / "claude"
    claude.write_text("#!/bin/sh\necho '2.1.308 (Claude Code)'\n")
    claude.chmod(0o755)
    assert info.claude_version([str(tmp_path / "missing"), str(claude)]) == "2.1.308"
    claude.write_text("#!/bin/sh\nexit 3\n")
    assert info.claude_version([str(claude)]) == ""


def test_the_module_reports_and_changes_nothing(tmp_path, patch_module):
    root = _claude_dir(tmp_path, {("-Users-me-demo", ID): transcript()})
    result = run_main(info.main, {"path": str(root), "days": 30})
    assert result["changed"] is False and [item["id"] for item in result["sessions"]] == [ID]
    assert result["version"] == "" and result["repo_paths"] == {}


# --------------------------------------------------------------------------- git_checkouts_info ---
def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=True).stdout.strip()


def _repository(path, origin, branch="main"):
    path.mkdir(parents=True)
    _git(path, "init", "--quiet", "--initial-branch", branch)
    _git(path, "config", "user.email", "dev@example.com")
    _git(path, "config", "user.name", "Dev")
    (path / "README").write_text("hello\n")
    _git(path, "add", "README")
    _git(path, "commit", "--quiet", "-m", "first")
    if origin:
        _git(path, "remote", "add", "origin", origin)
    return path


def test_the_projects_on_the_vm_are_its_checkouts_with_their_origins(tmp_path, patch_module):
    projects = tmp_path / "projects"
    _repository(projects / "demo", "https://github.com/octo/demo.git")
    _repository(projects / "local", "")
    (projects / ".claude-on-proxmox").mkdir()
    (projects / "plain").mkdir()
    result = run_main(checkouts.main, {"under": str(projects)})
    assert [(item["name"], item["origin"]) for item in result["checkouts"]] == [
        ("demo", "https://github.com/octo/demo.git"),
        ("local", ""),
    ]


def test_a_directory_asked_about_is_reported_from_a_subfolder_or_gone(tmp_path, patch_module):
    repo = _repository(tmp_path / "demo", "git@github.com:octo/demo.git")
    (repo / "src").mkdir()
    result = run_main(checkouts.main, {"paths": [str(repo / "src"), str(tmp_path / "gone")]})
    first, gone = result["checkouts"]
    assert first["git"] and first["origin"] == "git@github.com:octo/demo.git"
    assert Path(first["toplevel"]).resolve() == repo.resolve()
    assert gone == {
        "path": str(tmp_path / "gone"),
        "name": "gone",
        "exists": False,
        "git": False,
        "toplevel": "",
        "origin": "",
    }


def test_the_origin_is_the_url_configured_never_one_insteadof_rewrote(tmp_path, patch_module):
    # A url.<base>.insteadOf says where git connects to; the repository is the URL the remote names.
    repo = _repository(tmp_path / "demo", "https://github.com/octo/demo.git")
    _git(repo, "config", f"url.{tmp_path}/bare.git.insteadOf", "https://github.com/octo/demo.git")
    assert _git(repo, "remote", "get-url", "origin") == f"{tmp_path}/bare.git"
    assert run_main(checkouts.main, {"paths": [str(repo)]})["checkouts"][0]["origin"] == (
        "https://github.com/octo/demo.git"
    )


@pytest.mark.parametrize(
    ("configured", "reported"),
    [
        ("https://x-access-token:ghp_canned@github.com/octo/demo.git", "https://github.com/octo/demo.git"),
        ("https://ghp_canned@github.com/octo/demo.git", "https://github.com/octo/demo.git"),
        ("ssh://git@github.com/octo/demo.git", "ssh://github.com/octo/demo.git"),
        ("git@github.com:octo/demo.git", "git@github.com:octo/demo.git"),
    ],
)
def test_no_credential_in_an_origin_is_reported(tmp_path, patch_module, configured, reported):
    # The module's result reaches -v output and the run's log.
    repo = _repository(tmp_path / "demo", configured)
    found = run_main(checkouts.main, {"paths": [str(repo)]})
    assert found["checkouts"][0]["origin"] == reported and "ghp_canned" not in json.dumps(found)
    assert filters.github_repository(reported) == "octo/demo"


def test_a_path_holding_a_dollar_or_tilde_is_taken_as_written(tmp_path, patch_module):
    repo = _repository(tmp_path / "$HOME-demo", "")
    found = run_main(checkouts.main, {"paths": [str(repo)]})["checkouts"][0]
    assert found["exists"] and found["git"]


# ------------------------------------------------------------------------- claude_sessions_copy ---
def test_the_four_edits_and_nothing_else():
    data = transcript(worktree={"worktreePath": "/Users/me/demo/.claude/worktrees/fix-x"})
    edited, why = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, HERE)
    assert why == ""
    before, after = data.decode().splitlines(), edited.decode().splitlines()
    # The memory folder changes in the snapshot, and only there.
    assert MAC_MEMORY in before[0] and after[0] == before[0].replace(MAC_MEMORY, VM_MEMORY)
    assert after[1 : len(before)] == before[1:]
    # A cleared worktree binding, then where the session continues.
    assert [json.loads(line) for line in after[len(before) :]] == [
        {"type": "worktree-state", "worktreeSession": None, "sessionId": ID},
        {"type": "relocated", "relocatedCwd": CHECKOUT, "sessionId": ID},
    ]


def test_a_session_bound_to_no_worktree_gets_no_cleared_binding():
    # Never bound, or bound and then cleared by the session itself (ExitWorktree).
    cleared = json.dumps({"type": "worktree-state", "worktreeSession": None, "sessionId": ID}).encode() + b"\n"
    for data in (transcript(snapshot=False), transcript(worktree={"worktreePath": "/x"}, snapshot=False) + cleared):
        edited, _ = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY)
        assert (
            edited
            == data
            + b'{"type":"relocated","relocatedCwd":"/home/dev/projects/demo","sessionId":"'
            + ID.encode()
            + b'"}\n'
        )


def test_the_records_of_requests_sent_keep_the_memory_folder_they_name():
    # A resume sends the snapshot, never these (the configure scenario holds that against the real claude).
    shape = {"type": "api-request-shape", "sessionId": ID, "shapeHash": "ab12", "shape": {"system": [MAC_MEMORY]}}
    record = json.dumps(shape, separators=(",", ":")).encode() + b"\n"
    data = transcript() + record
    edited, why = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, HERE)
    assert why == "" and record in edited and edited.count(MAC_MEMORY.encode()) == 1


def test_a_snapshot_without_a_memory_folder_is_copied_as_it_is():
    # Auto memory off: the recorded prompt names none, and nothing needs rewriting.
    data = transcript(snapshot=False)
    edited, why = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, HERE)
    assert why == "" and edited.startswith(data)


def test_a_memory_folder_named_in_a_form_make_code_does_not_know_is_not_copied():
    # A Claude Code that words the prompt otherwise: the folder of this machine's would be sent as it is.
    data = transcript().replace(b"persistent file-based memory at", b"memory, kept at")
    edited, why = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, HERE)
    assert edited == b"" and "a form make code does not know" in why


VM_RESULTS = f"/home/dev/.claude/projects/-Users-me-demo/{ID}/tool-results"


def test_tool_results_are_named_where_the_copy_keeps_them_in_the_tool_results_alone():
    # As Claude Code names a large output it saved: in the tool result, in any folder of this machine's (a
    # session continued after /cd names the old one), and in what the assistant then asked for.
    saved = f"{HERE}/projects/-Users-me-old/{ID}/tool-results/toolu_1.txt"
    other = f"{HERE}/projects/-Users-me-demo/{OTHER}/tool-results/toolu_2.txt"
    result = {
        "type": "user",
        "sessionId": ID,
        "message": {"role": "user", "content": [{"type": "tool_result", "content": f"Output saved to: {saved}"}]},
        "toolUseResult": {"stdout": f"{saved} and {other}"},
    }
    asked = {
        "type": "assistant",
        "sessionId": ID,
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": f"read {saved}", "signature": "c2lnbmVk"},
                {"type": "tool_use", "name": "Read", "input": {"file_path": saved}},
            ],
        },
    }
    lines_in = [json.dumps(entry, separators=(",", ":")).encode() + b"\n" for entry in (result, asked)]
    data = transcript() + b"".join(lines_in)
    edited, why = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, HERE, VM_RESULTS)
    assert why == ""
    out = edited.splitlines(keepends=True)
    tool_result, assistant = out[-3], out[-2]
    assert tool_result == lines_in[0].replace(saved.encode(), f"{VM_RESULTS}/toolu_1.txt".encode())
    # Another session's saved output is that session's; the assistant's entry, signed thinking and all, is as it was.
    assert other.encode() in tool_result and assistant == lines_in[1]
    # Without the other machine's directory, no tool result is rewritten.
    assert lines_in[0] in copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, "", VM_RESULTS)[0]


def test_another_project_s_memory_folder_a_prompt_quotes_is_left_as_text():
    # A CLAUDE.md or MEMORY.md in the prompt can name another project's memory folder; only the session's
    # own must be rewritten.
    quoted = f"{HERE}/projects/-Users-me-elsewhere/memory/notes.md"
    data = transcript().replace(b"This directory already exists.", f"See {quoted}.".encode())
    edited, why = copy.edit_transcript(data, ID, CHECKOUT, VM_MEMORY, HERE, folder="-Users-me-demo")
    assert why == "" and quoted.encode() in edited and MAC_MEMORY.encode() not in edited
    # The session's own, named otherwise than the rewrite knows, is still refused.
    other_form = transcript().replace(b"persistent file-based memory at", b"memory, kept at")
    edited, why = copy.edit_transcript(other_form, ID, CHECKOUT, VM_MEMORY, HERE, folder="-Users-me-demo")
    assert edited == b"" and "a form make code does not know" in why


def test_only_complete_lines_are_copied():
    assert copy.complete_lines(transcript(partial=True)) == transcript()
    assert copy.complete_lines(b'{"type":"us') == b""


@pytest.mark.parametrize(
    ("current", "recorded", "digest", "action"),
    [
        (None, None, "new", "write"),  # no copy yet
        ("old", "old", "new", "write"),  # the last copy, untouched: replaced
        ("new", "old", "new", "unchanged"),
        ("new", None, "new", "unchanged"),
        ("vm", "old", "old", "continued"),  # continued on the VM; nothing new here
        ("vm", "old", "new", "diverged"),  # continued on both
        ("vm", None, "new", "diverged"),  # never this module's
    ],
)
def test_a_copy_is_replaced_only_while_it_is_what_was_last_written(current, recorded, digest, action):
    assert copy.classify(current, recorded, digest) == action


@pytest.mark.parametrize(
    ("recorded", "digest", "action"),
    [("old", "old", "continued"), ("old", "new", "diverged"), (None, "new", "diverged")],
)
def test_a_copy_that_moved_to_another_folder_counts_as_changed_on_the_vm(recorded, digest, action):
    assert copy.classify(None, recorded, digest, moved=True) == action


def _mirror(tmp_path, sessions):
    """The mirror rsync leaves: {(folder, id): bytes}, written an hour ago, with a tool result and a subagent each."""
    mirror = tmp_path / "mirror"
    then = time.time() - 3600
    for (folder, sid), data in sessions.items():
        path = mirror / folder / f"{sid}.jsonl"
        (mirror / folder / sid / "tool-results").mkdir(parents=True)
        (mirror / folder / sid / "subagents").mkdir()
        (mirror / folder / sid / "tool-results" / "toolu_1.txt").write_text("output\n")
        (mirror / folder / sid / "subagents" / "agent-1.jsonl").write_text("{}\n")
        path.write_bytes(data)
        os.utime(path, (then, then))
    return mirror, then


def _copy(tmp_path, mirror, sessions, check=False):
    return copy.copy_sessions(
        str(mirror),
        str(tmp_path / "vm" / ".claude"),
        str(tmp_path / "ws" / "manifest.json"),
        [{"id": sid, "folder": folder, "checkout": CHECKOUT, "memory": VM_MEMORY} for folder, sid in sessions],
        here=HERE,
        check=check,
    )


def test_copies_are_written_with_their_time_and_modes_and_a_second_run_changes_nothing(tmp_path):
    keys = [("-Users-me-demo", ID), ("-Users-me-demo--claude-worktrees-x", OTHER)]
    mirror, then = _mirror(tmp_path, {keys[0]: transcript(partial=True), keys[1]: transcript(OTHER)})
    result, changed = _copy(tmp_path, mirror, keys)
    assert changed and sorted(result["written"]) == sorted([ID, OTHER])
    target = tmp_path / "vm" / ".claude" / "projects" / "-Users-me-demo"
    copied = target / f"{ID}.jsonl"
    assert copied.read_bytes() == copy.edit_transcript(transcript(), ID, CHECKOUT, VM_MEMORY)[0]
    assert int(copied.stat().st_mtime) == int(then)
    assert stat.S_IMODE(copied.stat().st_mode) == 0o600 and stat.S_IMODE(target.stat().st_mode) == 0o700
    assert (target / ID / "tool-results" / "toolu_1.txt").read_text() == "output\n"
    assert not (target / ID / "subagents").exists()
    manifest = json.loads((tmp_path / "ws" / "manifest.json").read_text())
    assert sorted(manifest["sessions"]) == sorted(f"{folder}/{sid}.jsonl" for folder, sid in keys)
    assert stat.S_IMODE((tmp_path / "ws" / "manifest.json").stat().st_mode) == 0o600
    # Again, with nothing new: nothing written, nothing changed.
    again, changed = _copy(tmp_path, mirror, keys)
    assert not changed and sorted(again["unchanged"]) == sorted([ID, OTHER])


def test_a_copy_continued_on_the_vm_is_left_and_one_that_grew_here_too_is_named(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    _copy(tmp_path, mirror, [key])
    copied = tmp_path / "vm" / ".claude" / "projects" / "-Users-me-demo" / f"{ID}.jsonl"
    continued = copied.read_bytes() + b'{"type":"user","message":{"content":"on the VM"}}\n'
    copied.write_bytes(continued)
    result, changed = _copy(tmp_path, mirror, [key])
    assert result["continued"] == [ID] and not changed and copied.read_bytes() == continued
    # The session grew here as well: both left, and said apart.
    (mirror / "-Users-me-demo" / f"{ID}.jsonl").write_bytes(transcript() + transcript())
    result, _ = _copy(tmp_path, mirror, [key])
    assert result["diverged"] == [ID] and copied.read_bytes() == continued


def test_a_copy_moved_to_another_folder_on_the_vm_is_never_written_again(tmp_path):
    # /cd in the resumed session moves its transcript to the new directory's folder.
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    _copy(tmp_path, mirror, [key])
    projects = tmp_path / "vm" / ".claude" / "projects"
    manifest = (tmp_path / "ws" / "manifest.json").read_bytes()
    (projects / "-home-dev-projects-other").mkdir()
    moved = projects / "-home-dev-projects-other" / f"{ID}.jsonl"
    (projects / "-Users-me-demo" / f"{ID}.jsonl").rename(moved)
    result, changed = _copy(tmp_path, mirror, [key])
    assert result["continued"] == [ID] and not changed
    assert not (projects / "-Users-me-demo" / f"{ID}.jsonl").exists()
    assert (tmp_path / "ws" / "manifest.json").read_bytes() == manifest
    # The session grew here as well: still one folder, and said apart.
    (mirror / "-Users-me-demo" / f"{ID}.jsonl").write_bytes(transcript() + transcript())
    result, _ = _copy(tmp_path, mirror, [key])
    assert result["diverged"] == [ID] and not (projects / "-Users-me-demo" / f"{ID}.jsonl").exists()


def test_a_copy_still_as_written_is_replaced_when_the_session_grows_here(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    _copy(tmp_path, mirror, [key])
    (mirror / "-Users-me-demo" / f"{ID}.jsonl").write_bytes(transcript() + transcript())
    result, changed = _copy(tmp_path, mirror, [key])
    assert result["written"] == [ID] and changed


def test_a_session_moved_to_another_folder_here_takes_its_copy_along(tmp_path):
    # /cd on this machine: the session's folder changed here, and the VM's copy in the old one is still as written.
    old, new = ("-Users-me-demo", ID), ("-Users-me-demo-src", ID)
    mirror, _ = _mirror(tmp_path, {old: transcript()})
    _copy(tmp_path, mirror, [old])
    os.renames(mirror / old[0] / f"{ID}.jsonl", mirror / new[0] / f"{ID}.jsonl")
    os.renames(mirror / old[0] / ID, mirror / new[0] / ID)
    result, changed = _copy(tmp_path, mirror, [new])
    projects = tmp_path / "vm" / ".claude" / "projects"
    assert changed and result["written"] == [ID] and result["held"] == {}
    assert (projects / new[0] / f"{ID}.jsonl").exists() and not (projects / old[0]).exists()
    assert sorted(json.loads((tmp_path / "ws" / "manifest.json").read_text())["sessions"]) == [f"{new[0]}/{ID}.jsonl"]
    again, changed = _copy(tmp_path, mirror, [new])
    assert not changed and again["unchanged"] == [ID]


def test_a_session_moved_here_whose_old_copy_was_continued_on_the_vm_is_left_and_its_folder_named(tmp_path):
    old, new = ("-Users-me-demo", ID), ("-Users-me-demo-src", ID)
    mirror, _ = _mirror(tmp_path, {old: transcript()})
    _copy(tmp_path, mirror, [old])
    projects = tmp_path / "vm" / ".claude" / "projects"
    continued = projects / old[0] / f"{ID}.jsonl"
    continued.write_bytes(continued.read_bytes() + b'{"type":"user","message":{"content":"on the VM"}}\n')
    os.renames(mirror / old[0] / f"{ID}.jsonl", mirror / new[0] / f"{ID}.jsonl")
    result, _ = _copy(tmp_path, mirror, [new])
    assert result["diverged"] == [ID] and result["held"] == {ID: old[0]}
    assert continued.exists() and not (projects / new[0] / f"{ID}.jsonl").exists()


def test_each_copy_is_recorded_as_it_is_written_so_a_run_that_stops_records_what_it_wrote(tmp_path):
    keys = [("-Users-me-demo", ID), ("-Users-me-other", OTHER)]
    mirror, _ = _mirror(tmp_path, {keys[0]: transcript(), keys[1]: transcript(OTHER)})
    # The second copy's folder is a file: its write fails, after the first was written.
    (tmp_path / "vm" / ".claude" / "projects").mkdir(parents=True)
    (tmp_path / "vm" / ".claude" / "projects" / keys[1][0]).write_text("in the way\n")
    with pytest.raises(OSError):
        _copy(tmp_path, mirror, keys)
    recorded = json.loads((tmp_path / "ws" / "manifest.json").read_text())["sessions"]
    assert sorted(recorded) == [f"{keys[0][0]}/{ID}.jsonl"]
    # The session then grows here: the copy is the module's own, and replaced, never "diverged".
    (tmp_path / "vm" / ".claude" / "projects" / keys[1][0]).unlink()
    (mirror / keys[0][0] / f"{ID}.jsonl").write_bytes(transcript() + transcript())
    result, _ = _copy(tmp_path, mirror, keys)
    assert sorted(result["written"]) == sorted([ID, OTHER])


@pytest.mark.parametrize(
    ("age", "current", "kept"),
    [
        (3600, None, "source"),  # recent: the other machine's time
        (40 * 86400, None, "now"),  # older than the sweep's 30 days: the time it is written
        (40 * 86400, 86400, "current"),  # given that a day ago: kept
        (40 * 86400, 20 * 86400, "now"),  # given that 20 days ago: renewed
    ],
)
def test_a_copy_keeps_the_other_machine_s_time_only_while_the_sweep_would_spare_it(age, current, kept):
    now = 2_000_000_000.0
    source, there = now - age, None if current is None else now - current
    assert copy.copy_time(source, there, now) == {"source": source, "now": now, "current": there}[kept]


def test_an_old_session_s_copy_is_given_the_time_it_is_written_and_kept_on_the_next_run(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    then = time.time() - 40 * 86400
    os.utime(mirror / key[0] / f"{ID}.jsonl", (then, then))
    _copy(tmp_path, mirror, [key])
    copied = tmp_path / "vm" / ".claude" / "projects" / key[0] / f"{ID}.jsonl"
    assert time.time() - copied.stat().st_mtime < 60
    _, changed = _copy(tmp_path, mirror, [key])
    assert not changed


def test_check_mode_writes_nothing(tmp_path):
    key = ("-Users-me-demo", ID)
    mirror, _ = _mirror(tmp_path, {key: transcript()})
    result, changed = _copy(tmp_path, mirror, [key], check=True)
    assert changed and result["written"] == [ID]
    assert not (tmp_path / "vm").exists() and not (tmp_path / "ws").exists()


def test_sessions_that_left_the_selection_leave_the_mirror(tmp_path):
    keys = [("-Users-me-demo", ID), ("-Users-me-other", OTHER)]
    mirror, _ = _mirror(tmp_path, {keys[0]: transcript(), keys[1]: transcript(OTHER)})
    _, changed = _copy(tmp_path, mirror, keys[:1])
    assert changed
    assert sorted(path.name for path in mirror.iterdir()) == ["-Users-me-demo"]


def test_the_module_takes_only_a_session_s_folder_and_id(tmp_path, patch_module):
    from conftest import fail_main

    args = {
        "mirror": str(tmp_path / "mirror"),
        "dest": str(tmp_path / ".claude"),
        "manifest": str(tmp_path / "manifest.json"),
        "sessions": [{"id": ID, "folder": "../../etc", "checkout": CHECKOUT, "memory": VM_MEMORY}],
    }
    assert "is not a session's folder and ID" in fail_main(copy.main, args)["msg"]
