"""Filters for the Claude Code sessions just code carries from this machine to a VM, and just destroy brings back.

playbooks/tasks/session_read.yml reads both ends with the modules in
playbooks/library/ and decides here, from what they read, which session goes
to which checkout on the VM. A session belongs to the repository its first
recorded directory is a checkout of, by that checkout's git origin: never by
a folder's name, which a renamed clone, a subfolder or another owner's
repository of the same name all get wrong (docs/architecture/lifecycle.md §4.9).
playbooks/tasks/session_save.yml decides here what of a VM's sessions exists
only there, before just destroy deletes it (§4.5).
Nothing here reads a file: the modules do, and only these values reach a filter.
"""

from __future__ import annotations

import re
import shlex
from datetime import datetime, timezone

from ansible.errors import AnsibleFilterError

# Claude Code names a directory's folder under ~/.claude/projects/ by turning every character but an ASCII
# letter or digit into "-", and shortens a name longer than this with a hash of its own.
_FOLDER_LENGTH = 200
# A Claude Code worktree: <checkout>/.claude/worktrees/<name>, or a folder inside one.
_WORKTREE = re.compile(r"^(?P<checkout>/.+?)/\.claude/worktrees/[^/]+(?:/.*)?$")
_VERSION = re.compile(r"^(\d+(?:\.\d+)*)")
# What just destroy saves by: a folder as Claude Code names one, and a session's ID. Anything else
# a VM reports is left out, since the names travel to tar and become paths on this machine.
_SAVED_FOLDER = re.compile(r"^[A-Za-z0-9-]+$")
_SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# GitHub as a clone's origin names it: https, ssh, scp-like, and ssh over port 443.
_GITHUB = re.compile(
    r"^(?:(?:https|ssh)://(?:[^@/]+@)?(?:ssh\.)?github\.com(?::\d+)?/|(?:[^@/:]+@)?github\.com:)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


def claude_project_folder(path):
    """Return the folder Claude Code files ``path``'s sessions and memory under.

    Every character but an ASCII letter or digit becomes "-": /home/dev/projects/my_app.v2 is
    -home-dev-projects-my-app-v2. A name over 200 characters is refused rather than guessed at,
    since Claude Code shortens it with a hash of its own.
    """
    folder = re.sub(r"[^A-Za-z0-9]", "-", str(path))
    if len(folder) > _FOLDER_LENGTH:
        raise AnsibleFilterError(
            f"{path} is too long a path for a Claude Code project folder ({len(folder)} characters, at most"
            f" {_FOLDER_LENGTH}): Claude Code shortens such a name in a way just code does not repeat."
        )
    return folder


def git_repository(url):
    """Return the ``owner/repo`` a git remote URL names, lowercased, or "" for none.

    https, ssh and scp-like URLs alike (https://github.com/Owner/Repo.git, git@github.com:owner/repo,
    ssh://git@github.com/owner/repo/), and a local path by its last two folders. The host is not
    compared: a clone here may reach GitHub through an SSH alias of the user's own. GitHub's names
    are not case-sensitive, so neither is the comparison.
    """
    url = str(url or "").strip()
    if "://" in url:
        path = url.split("://", 1)[1].partition("/")[2]
    elif ":" in url and "/" not in url.split(":", 1)[0]:
        path = url.split(":", 1)[1]
    else:
        path = url
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return ""
    owner, repo = parts[-2], parts[-1]
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    return f"{owner}/{repo}".lower() if repo else ""


def github_repository(url):
    """Return the ``owner/repo`` a GitHub origin names, lowercased, or "" for any other URL.

    The VM's checkouts are the ones just deploy cloned from GitHub, so on the VM nothing else names
    a repository: not a local path, which names only folders, nor another host's repository of the
    same owner and name, which is another repository.
    """
    found = _GITHUB.match(str(url or "").strip())
    if not found or found.group("owner") in (".", "..") or found.group("repo") in ("", ".", ".."):
        return ""
    return f"{found.group('owner')}/{found.group('repo')}".lower()


def make_code_dir(projects, given=""):
    """just code's own directory on a VM: ``given`` (vscode_workspaces_dir), else .claude-on-proxmox in ``projects``.

    code.yml keeps the workspace files and the session copies' manifest there, and just destroy's
    save reads the manifest from it: one definition, so the two cannot disagree. No trailing "/".
    """
    path = str(given or "") or f"{str(projects).rstrip('/')}/.claude-on-proxmox"
    return path.rstrip("/") or "/"


def _worktree_checkout(path):
    """The checkout a Claude Code worktree belongs to, or "" when ``path`` is in none."""
    found = _WORKTREE.match(str(path or ""))
    return found.group("checkout") if found else ""


def claude_session_dirs(sessions):
    """Return the directories to ask git about for ``sessions``: each first directory, and its worktree's checkout.

    The checkout is asked too because a worktree that is gone leaves its checkout, and the branch, behind.
    """
    dirs = []
    for session in sessions or []:
        cwd = session.get("cwd") or ""
        for path in (cwd, _worktree_checkout(cwd)):
            if path and path not in dirs:
                dirs.append(path)
    return dirs


def _version(text):
    found = _VERSION.match(str(text or "").strip())
    return tuple(int(part) for part in found.group(1).split(".")) if found else None


def _under(path, top):
    """Whether ``path`` is ``top`` or inside it, ignoring case: macOS paths are not case-sensitive."""
    path, top = path.rstrip("/").lower(), top.rstrip("/").lower()
    return bool(top) and (path == top or path.startswith(top + "/"))


def _repository(session, here, repo_paths):
    """The repository a session belongs to, the directory that says so, and how: (repo, dir, how).

    Its first directory's origin; for a directory that is gone, the origin of the checkout its worktree
    belonged to, then the repository githubRepoPaths in ~/.claude.json lists it under. ``how`` says
    why there is none when ``repo`` is "".
    """
    cwd = session.get("cwd") or ""
    if not cwd:
        return "", "", "no directory is recorded in it"
    for path in (cwd, _worktree_checkout(cwd)):
        found = here.get(path) if path else None
        if found and found.get("exists"):
            repo = git_repository(found.get("origin"))
            if repo:
                return repo, path, "origin"
            return "", path, f"{path} is not a git checkout with an origin"
    listed = sorted(
        {
            str(repo).lower()
            for repo, paths in (repo_paths or {}).items()
            for listed_path in paths or []
            if _under(cwd, str(listed_path))
        }
    )
    if len(listed) == 1:
        return listed[0], "", "githubRepoPaths"
    return "", "", f"{cwd} is gone"


def _checkout(repo, by_repo, project):
    """The VM's checkout of ``repo``, or None and why: the one, or of several the one named like the repository."""
    found = by_repo.get(repo, [])
    if project:
        found = [checkout for checkout in found if checkout["name"] == project]
    if len(found) == 1:
        return found[0], ""
    if not found:
        return None, "none"
    named = [checkout for checkout in found if checkout["name"].lower() == repo.split("/")[1]]
    if len(named) == 1:
        return named[0], ""
    return None, ", ".join(sorted(checkout["name"] for checkout in found))


def claude_sessions_plan(
    sessions, here, there, version, repo_paths=None, dest="", wanted="", project="", vm="the VM", open_there=None
):
    """Decide which of this machine's sessions go to which checkout on the VM.

    ``sessions`` is claude_sessions_info's list for this machine, ``here`` git_checkouts_info's for
    the directories claude_session_dirs named, ``there`` its list of the VM's projects - each of a
    repository only by a GitHub origin (github_repository): here any origin names one, since a
    clone here may reach GitHub through an SSH alias of the user's own - ``version``
    the VM's Claude Code ("" without one), ``repo_paths`` githubRepoPaths from this machine's
    ~/.claude.json, ``dest`` the VM's Claude configuration directory. ``wanted`` is SESSION, which is
    planned whatever its age, and ``project`` PROJECT, which with it must name the checkout.
    ``open_there`` holds the IDs a running claude on the VM has open (claude_sessions_info's
    ``registered`` there): such a copy is left, never replaced under the claude holding it.

    Returns ``sessions``, one item per session to copy, as claude_sessions_copy takes it: its ID, its
    folder here - kept on the VM, so that --continue in a checkout never picks a copy up - the
    checkout it continues in and the memory folder its recorded prompt is pointed at. ``files`` is
    what rsync sends for them, relative to the projects folder; ``repositories`` their repositories.
    ``newer``, ``ambiguous``, ``twice`` (an ID in more than one folder here, which ``claude --resume
    <id>`` could not choose between) and ``open`` are the sessions left out for a reason worth saying;
    ``unmatched`` counts the ones of no repository the VM has cloned. ``session`` is --session's own entry, with
    ``problem`` the reason it cannot be opened, or "".
    """
    here = {item["path"]: item for item in here or [] if item.get("path")}
    by_repo = {}
    for checkout in there or []:
        repo = github_repository(checkout.get("origin"))
        if repo:
            by_repo.setdefault(repo, []).append(checkout)
    vm_version = _version(version)
    plan = {
        "sessions": [],
        "files": [],
        "newer": [],
        "ambiguous": [],
        "twice": [],
        "open": [],
        "unmatched": 0,
        "session": {},
    }
    folders_of = {}
    for session in sessions or []:
        folders_of.setdefault(session.get("id"), set()).add(session.get("folder"))
    open_there = {str(sid) for sid in open_there or []}
    plan["claude"], repositories = vm_version is not None, set()
    for session in sorted(sessions or [], key=lambda item: (item.get("id", ""), item.get("folder", ""))):
        repo, path, how = _repository(session, here, repo_paths)
        own = session.get("id") == wanted
        checkout, why = _checkout(repo, by_repo, project if own else "") if repo else (None, "none")
        newer = (_version(session.get("version")) or ()) > (vm_version or ())
        if own:
            plan["session"] = {
                "id": wanted,
                "folder": session.get("folder", ""),
                "cwd": session.get("cwd") or "",
                "dir": path,
                "how": how,
                "repository": repo,
                "branch": session.get("branch") or "",
                "checkout": checkout["path"] if checkout else "",
                "name": checkout["name"] if checkout else "",
            }
        # Without Claude Code on the VM there is nothing to resume a copy with: none is made, and the run says why.
        if not session.get("conversation") or vm_version is None:
            continue
        # --session's own cases are refused with their reason instead (_session_problem, session_read.yml).
        if not own and len(folders_of[session["id"]]) > 1:
            if session["id"] not in plan["twice"]:
                plan["twice"].append(session["id"])
            continue
        if not own and session["id"] in open_there:
            plan["open"].append(session["id"])
            continue
        if checkout and not newer:
            plan["sessions"].append(
                {
                    "id": session["id"],
                    "folder": session["folder"],
                    "checkout": checkout["path"],
                    "memory": f"{dest}/projects/{claude_project_folder(checkout['path'])}/memory/",
                }
            )
            plan["files"].append(f"{session['folder']}/{session['id']}.jsonl")
            if session.get("tools"):
                plan["files"].append(f"{session['folder']}/{session['id']}/tool-results")
            repositories.add(repo)
        elif checkout:
            plan["newer"].append(session["id"])
        elif why != "none":
            plan["ambiguous"].append({"id": session["id"], "repository": repo, "names": why})
        else:
            plan["unmatched"] += 1
    if wanted:
        plan["session"]["problem"] = _session_problem(plan["session"], sessions, wanted, by_repo, project, version, vm)
    plan["repositories"] = sorted(repositories)
    return plan


def _session_problem(entry, sessions, wanted, by_repo, project, version, vm):
    """Why SESSION cannot be opened on the VM, or "". The git checks come after, in session_read.yml."""
    found = [session for session in sessions or [] if session.get("id") == wanted]
    if not found:
        return f"There is no session {wanted} on this machine: no {wanted}.jsonl in a folder of Claude Code's projects."
    if len(found) > 1:
        folders = ", ".join(sorted(session.get("folder", "") for session in found))
        return f"Session {wanted} is in more than one folder here ({folders}), so which one to continue is unclear."
    session, repo = found[0], entry.get("repository", "")
    names = ", ".join(sorted(checkout["name"] for checkout in by_repo.get(repo, [])))
    if not session.get("conversation"):
        return f"Session {wanted} has no messages to continue."
    if session.get("open"):
        return (
            f"Session {wanted} is open on this machine (claude, pid {session['open']}). Exit it here first: a session"
            " continues in one place at a time."
        )
    if not repo:
        return f"Session {wanted} belongs to no repository {vm} could have: {entry.get('how')}."
    if not entry.get("dir"):
        return (
            f"Session {wanted} ran in {entry.get('cwd')}, which is gone, so whether its work is committed and pushed"
            " cannot be checked."
        )
    if _version(version) is None:
        return f"Claude Code is not installed on {vm} yet: just deploy {vm} --only configure installs it."
    if not entry.get("checkout") and project and names:
        return f"--project {project} on {vm} is not a checkout of {repo}; {names} is."
    if not entry.get("checkout") and names:
        return f"{vm} has more than one checkout of {repo} ({names}): name the one to open with --project <folder>."
    if not entry.get("checkout"):
        return (
            f"{vm} has no checkout of {repo}. just deploy {vm} --only configure clones your own repositories; one of"
            " another owner's, or a fork, is not cloned."
        )
    if (_version(session.get("version")) or ()) > _version(version):
        return (
            f"Session {wanted} was last written by Claude Code {session.get('version')}, newer than {vm}'s {version}."
            " Update it there (claude update), then run this again."
        )
    if entry.get("branch") in ("", "HEAD"):
        return f"Session {wanted} last ran on no branch (a detached HEAD), so there is no branch to bring {vm} to."
    return ""


def _written(mtime):
    try:
        return datetime.fromtimestamp(float(mtime or 0), timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return ""


def claude_sessions_saved(sessions, memory, dest):
    """What of a VM's Claude Code sessions just destroy saves into ``dest``, and the list kept beside them.

    ``sessions`` and ``memory`` are claude_sessions_info's for the VM, read with its manifest. Saved:
    every session that holds a message and is not a copy just code wrote that is still as written -
    this machine has that one - with the folder beside it, and every auto memory folder. What the VM
    reports is not trusted: a session or folder not named as Claude Code names them is left out and
    counted, never handed on. Returns ``files``, relative to the VM's projects folder, for
    claude_sessions_fetch; ``sessions``, one item per session saved for sessions.json, with the
    command that continues it here and the newest Claude Code that wrote it; ``newest``, the newest of
    those, which resuming them here needs at least; ``started`` and ``continued``, how many began on the
    VM and how many are copies continued there; ``memory``, the folders saved; ``refused``, how many
    were left out.
    """
    refused = 0
    kept = []
    for session in sorted(sessions or [], key=lambda item: (str(item.get("folder", "")), str(item.get("id", "")))):
        if not session.get("conversation") or session.get("copied"):
            continue
        if not _SAVED_FOLDER.match(str(session.get("folder", ""))) or not _SESSION_ID.match(str(session.get("id", ""))):
            refused += 1
            continue
        kept.append(session)
    files, listed = [], []
    for session in kept:
        key = f"{session['folder']}/{session['id']}"
        files.append(f"{key}.jsonl")
        if session.get("dir"):
            files.append(key)
        transcript = f"{dest}/projects/{key}.jsonl"
        listed.append(
            {
                "id": session["id"],
                "folder": session["folder"],
                "cwd": str(session.get("cwd") or ""),
                "branch": str(session.get("branch") or ""),
                # Its digits alone: the VM reports it.
                "version": ".".join(map(str, _version(session.get("version")) or ())),
                "written": _written(session.get("mtime")),
                "started_on_the_vm": not session.get("recorded"),
                "transcript": transcript,
                "resume": f"claude --resume {shlex.quote(transcript)}",
            }
        )
    folders = sorted(str(folder) for folder in memory or [])
    refused += sum(1 for folder in folders if not _SAVED_FOLDER.match(folder))
    folders = [folder for folder in folders if _SAVED_FOLDER.match(folder)]
    files += [f"{folder}/memory" for folder in folders]
    started = sum(1 for item in listed if item["started_on_the_vm"])
    versions = [item["version"] for item in listed if item["version"]]
    return {
        "files": files,
        "sessions": listed,
        "newest": max(versions, key=_version) if versions else "",
        "started": started,
        "continued": len(listed) - started,
        "memory": folders,
        "refused": refused,
    }


class FilterModule:
    def filters(self):
        return {
            "claude_project_folder": claude_project_folder,
            "claude_session_dirs": claude_session_dirs,
            "claude_sessions_plan": claude_sessions_plan,
            "claude_sessions_saved": claude_sessions_saved,
            "git_repository": git_repository,
            "github_repository": github_repository,
            "make_code_dir": make_code_dir,
        }
