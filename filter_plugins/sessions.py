"""Filters for the Claude Code sessions make code carries from this machine to a VM.

playbooks/tasks/session_read.yml reads both ends with the modules in
playbooks/library/ and decides here, from what they read, which session goes
to which checkout on the VM. A session belongs to the repository its first
recorded directory is a checkout of, by that checkout's git origin: never by
a folder's name, which a renamed clone, a subfolder or another owner's
repository of the same name all get wrong (docs/architecture/lifecycle.md §4.9).
Nothing here reads a file: the modules do, and only these values reach a filter.
"""

from __future__ import annotations

import re

from ansible.errors import AnsibleFilterError

# Claude Code names a directory's folder under ~/.claude/projects/ by turning every character but an ASCII
# letter or digit into "-", and shortens a name longer than this with a hash of its own.
_FOLDER_LENGTH = 200
# A Claude Code worktree: <checkout>/.claude/worktrees/<name>, or a folder inside one.
_WORKTREE = re.compile(r"^(?P<checkout>/.+?)/\.claude/worktrees/[^/]+(?:/.*)?$")
_VERSION = re.compile(r"^(\d+(?:\.\d+)*)")
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
            f" {_FOLDER_LENGTH}): Claude Code shortens such a name in a way make code does not repeat."
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

    The VM's checkouts are the ones make deploy cloned from GitHub, so on the VM nothing else names
    a repository: not a local path, which names only folders, nor another host's repository of the
    same owner and name, which is another repository.
    """
    found = _GITHUB.match(str(url or "").strip())
    if not found or found.group("owner") in (".", "..") or found.group("repo") in ("", ".", ".."):
        return ""
    return f"{found.group('owner')}/{found.group('repo')}".lower()


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
    """The repository a session belongs to, or "".

    Its first directory's origin; for a directory that is gone, the origin of the checkout its worktree
    belonged to, then the repository githubRepoPaths in ~/.claude.json lists it under.
    """
    cwd = session.get("cwd") or ""
    if not cwd:
        return ""
    for path in (cwd, _worktree_checkout(cwd)):
        found = here.get(path) if path else None
        if found and found.get("exists"):
            return git_repository(found.get("origin"))
    listed = sorted(
        {
            str(repo).lower()
            for repo, paths in (repo_paths or {}).items()
            for listed_path in paths or []
            if _under(cwd, str(listed_path))
        }
    )
    return listed[0] if len(listed) == 1 else ""


def _checkout(repo, by_repo):
    """The VM's checkout of ``repo``, or None and why: the one, or of several the one named like the repository."""
    found = by_repo.get(repo, [])
    if len(found) == 1:
        return found[0], ""
    if not found:
        return None, "none"
    named = [checkout for checkout in found if checkout["name"].lower() == repo.split("/")[1]]
    if len(named) == 1:
        return named[0], ""
    return None, ", ".join(sorted(checkout["name"] for checkout in found))


def claude_sessions_plan(sessions, here, there, version, repo_paths=None, dest="", open_there=None):
    """Decide which of this machine's sessions go to which checkout on the VM.

    ``sessions`` is claude_sessions_info's list for this machine, ``here`` git_checkouts_info's for
    the directories claude_session_dirs named, ``there`` its list of the VM's projects - each of a
    repository only by a GitHub origin (github_repository): here any origin names one, since a
    clone here may reach GitHub through an SSH alias of the user's own - ``version``
    the VM's Claude Code ("" without one), ``repo_paths`` githubRepoPaths from this machine's
    ~/.claude.json, ``dest`` the VM's Claude configuration directory. ``open_there`` holds the IDs a
    running claude on the VM has open (claude_sessions_info's ``registered`` there): such a copy is
    left, never replaced under the claude holding it.

    Returns ``sessions``, one item per session to copy, as claude_sessions_copy takes it: its ID, its
    folder here - kept on the VM, so that --continue in a checkout never picks a copy up - the
    checkout it continues in and the memory folder its recorded prompt is pointed at. ``files`` is
    what rsync sends for them, relative to the projects folder; ``repositories`` their repositories.
    ``newer``, ``ambiguous``, ``twice`` (an ID in more than one folder here, which ``claude --resume
    <id>`` could not choose between) and ``open`` are the sessions left out for a reason worth saying;
    one of no repository the VM has cloned is in none.
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
    }
    folders_of = {}
    for session in sessions or []:
        folders_of.setdefault(session.get("id"), set()).add(session.get("folder"))
    open_there = {str(sid) for sid in open_there or []}
    plan["claude"], repositories = vm_version is not None, set()
    for session in sorted(sessions or [], key=lambda item: (item.get("id", ""), item.get("folder", ""))):
        repo = _repository(session, here, repo_paths)
        checkout, why = _checkout(repo, by_repo) if repo else (None, "none")
        newer = (_version(session.get("version")) or ()) > (vm_version or ())
        # Without Claude Code on the VM there is nothing to resume a copy with: none is made, and the run says why.
        if not session.get("conversation") or vm_version is None:
            continue
        if len(folders_of[session["id"]]) > 1:
            if session["id"] not in plan["twice"]:
                plan["twice"].append(session["id"])
            continue
        if session["id"] in open_there:
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
    plan["repositories"] = sorted(repositories)
    return plan


def claude_sessions_listed(rows):
    """make code's words for the sessions it left out: each ``[before, ids, after]`` with IDs, the IDs between.

    An ID is said by its first eight characters, as /resume lists it; nothing of a transcript is.
    """
    return "".join(
        f"{before}{', '.join(str(sid).split('-')[0] for sid in ids)}{after}" for before, ids, after in rows or [] if ids
    )


class FilterModule:
    def filters(self):
        return {
            "claude_project_folder": claude_project_folder,
            "claude_session_dirs": claude_session_dirs,
            "claude_sessions_listed": claude_sessions_listed,
            "claude_sessions_plan": claude_sessions_plan,
            "git_repository": git_repository,
            "github_repository": github_repository,
        }
