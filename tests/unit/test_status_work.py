"""`just status`'s WORK: what of each checkout on a VM is on no remote or not committed (git_checkouts_info, work).

git_checkouts_info runs in this process against real throwaway repositories, git with this machine's config shut
out (GIT_CONFIG_GLOBAL=/dev/null, as every test here runs git): a worktree under .claude/worktrees/ and one outside
the projects directory, a branch with no upstream, a detached HEAD with commits of its own, a stash, untracked files.
Nothing it runs changes a repository, which the last test holds: every ref, the index and the worktrees' files are as
they were.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import subprocess
from pathlib import Path

import pytest
from harness import REPO

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _module():
    spec = importlib.util.spec_from_file_location(
        "git_checkouts_info_under_test", REPO / "playbooks" / "library" / "git_checkouts_info.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = _module()


class Shim:
    """What the module asks of AnsibleModule: run_command, with git's environment as this test's."""

    def run_command(self, argv, environ_update=None, expand_user_and_vars=True):
        result = subprocess.run(
            argv, capture_output=True, text=True, env={**os.environ, **GIT_ENV, **(environ_update or {})}, check=False
        )
        return result.returncode, result.stdout, result.stderr


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env={**os.environ, **GIT_ENV}
    ).stdout.strip()


def _commit(cwd: Path, name: str) -> None:
    (cwd / name).write_text(name + "\n")
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", name)


@pytest.fixture
def projects(tmp_path):
    """~/projects with alpha, its work in every state, and beta, clean."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    projects = tmp_path / "projects"
    projects.mkdir()
    for name in ("alpha", "beta"):
        _git(projects, "clone", "-q", str(remote), name)
        _commit(projects / name, f"{name}-base")
        _git(projects / name, "push", "-q", "origin", "HEAD:main" if name == "alpha" else "HEAD:beta-main")
        _git(projects / name, "fetch", "-q")
    alpha = projects / "alpha"
    # A branch with no upstream and two commits on no remote.
    _git(alpha, "switch", "-q", "-c", "feature")
    _commit(alpha, "f1")
    _commit(alpha, "f2")
    _git(alpha, "switch", "-q", "main")
    # A stash, and an untracked file in the main tree.
    (alpha / "alpha-base").write_text("changed\n")
    _git(alpha, "stash", "-q")
    (alpha / "notes.txt").write_text("not committed\n")
    # A Claude worktree, on a branch of its own with one commit and a change not committed.
    _git(alpha, "worktree", "add", "-q", "-b", "wt", str(alpha / ".claude" / "worktrees" / "wt"))
    _commit(alpha / ".claude" / "worktrees" / "wt", "w1")
    (alpha / ".claude" / "worktrees" / "wt" / "w1").write_text("edited\n")
    # A worktree outside the projects directory, detached, with a commit only its HEAD holds.
    elsewhere = tmp_path / "elsewhere"
    _git(alpha, "worktree", "add", "-q", "--detach", str(elsewhere), "main")
    _commit(elsewhere, "d1")
    return projects, elsewhere


def _state(root: Path) -> dict:
    """Every file under root, .git included, by its content: what a read must leave as it was."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_work_says_what_is_on_no_remote_or_not_committed(projects) -> None:
    projects, elsewhere = projects
    found = {
        item["name"]: item
        for item in (M.read(Shim(), str(projects / n), False, "", None, "", True) for n in ("alpha", "beta"))
    }
    alpha = found["alpha"]["work"]
    trees = {Path(t["path"]).name: t for t in alpha["worktrees"]}
    assert set(trees) == {"alpha", "wt", "elsewhere"}
    assert trees["alpha"] == {"path": trees["alpha"]["path"], "branch": "main", "dirty": True, "own_commits": 0}
    assert trees["wt"]["branch"] == "wt" and trees["wt"]["dirty"] is True
    assert trees["elsewhere"]["branch"] == "" and trees["elsewhere"]["own_commits"] == 1
    assert trees["elsewhere"]["dirty"] is False
    assert sorted((u["branch"], u["commits"]) for u in alpha["unpushed"]) == [("feature", 2), ("wt", 1)]
    assert alpha["stashes"] == 1
    assert found["beta"]["work"] == {
        "worktrees": [
            {"path": found["beta"]["work"]["worktrees"][0]["path"], "branch": "main", "dirty": False, "own_commits": 0}
        ],
        "unpushed": [],
        "stashes": 0,
    }


def test_reading_the_work_changes_nothing(projects) -> None:
    projects, elsewhere = projects
    before = _state(projects.parent)
    for name in ("alpha", "beta"):
        M.read(Shim(), str(projects / name), True, "", None, "", True)
    assert _state(projects.parent) == before


def test_every_git_the_work_runs_is_a_read() -> None:
    source = (REPO / "playbooks" / "library" / "git_checkouts_info.py").read_text()
    start = source.index("def worktrees(")
    body = source[start : source.index("def _inside(")]
    commands = sorted(set(re.findall(r'_git\(\s*module,\s*[^,]+,\s*"([a-z-]+)"', body)))
    assert commands == ["for-each-ref", "rev-list", "stash", "status", "worktree"], commands
    assert '"stash", "list"' in body and '"worktree", "list", "--porcelain"' in body
