"""The pieces of make code's session handoff: filter_plugins/sessions.py and the modules in playbooks/library/.

Which session goes to which checkout on the VM. Every transcript, ~/.claude
and checkout here is built for the test in its own directory, never the
developer's; the git checkouts are real ones, with origins that only look like
GitHub's.
"""

from __future__ import annotations

import importlib.util
import json
import os
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
