#!/usr/bin/python
# Copyright (c) claude-on-proxmox contributors
# MIT License
"""Read the Claude Code sessions in a Claude Code configuration directory, never their content."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: claude_sessions_info
short_description: Read what the Claude Code sessions in a configuration directory say about themselves
version_added: "1.0.0"
description:
  - Lists the session transcripts under C(<path>/projects/<folder>/<id>.jsonl) and reads, from each,
    the first directory it ran in, the newest Claude Code version that wrote it, the branch it last ran
    on and whether it holds a message at all. Nothing of what was said is returned, only those values.
  - A line still being written, the last one without its newline, is not read.
  - Read-only. make code runs it on this machine and on the VM (playbooks/tasks/session_read.yml).
  - A session is open while C(sessions/) registers it to a running claude: a pid that now names another
    program is an entry a claude left behind, never an open session.
options:
  path:
    description: Claude Code's configuration directory, C(~/.claude) by default.
    type: path
    required: true
  days:
    description: The sessions written in the last this many days. C(0) lists none.
    type: int
    default: 0
  repo_paths:
    description:
      - Also return C(githubRepoPaths) from Claude Code's C(.claude.json), the one in O(path) or the one beside
        it, which Claude Code keeps in the home directory. Nothing else of that file is returned.
    type: bool
    default: false
  claude:
    description:
      - Also return the version of the first of these Claude Code executables there is. Only these: never
        C(claude) on PATH, which on a machine standing in for the VM in a test would be the developer's.
    type: list
    elements: path
    default: []
author:
  - claude-on-proxmox contributors
"""

EXAMPLES = r"""
- name: Read the sessions of the last 30 days
  claude_sessions_info:
    path: ~/.claude
    days: 30
  register: found
"""

RETURN = r"""
sessions:
  description: One item per transcript, sorted by folder and ID.
  returned: always
  type: list
  elements: dict
  contains:
    id:
      description: The session's ID, its file's name.
      type: str
    folder:
      description: The folder under C(projects/) it is filed in.
      type: str
    mtime:
      description: When it was last written, in seconds since the epoch.
      type: float
    cwd:
      description: The first directory recorded in it, or "".
      type: str
    version:
      description: The newest Claude Code version recorded in it, or "".
      type: str
    branch:
      description: The git branch its last entry that names one ran on, or "".
      type: str
    conversation:
      description: Whether it holds a user or assistant message; resume refuses a transcript without one.
      type: bool
    open:
      description: The pid of a running claude that C(sessions/) says has it open, or 0.
      type: int
    tools:
      description: Whether it has a C(tool-results/) folder, the large tool outputs it refers to.
      type: bool
    dir:
      description: Whether it has a folder of its own beside it, for its tool results, subagents and MCP tasks.
      type: bool
registered:
  description: Every session C(sessions/) registers to a running claude, whatever its transcript, its ID to the pid.
  returned: always
  type: dict
repo_paths:
  description: githubRepoPaths, owner/repo to the local paths Claude Code has seen it in; {} without O(repo_paths).
  returned: always
  type: dict
version:
  description: The version O(claude) reports, or "" when there is none or it did not answer.
  returned: always
  type: str
"""

import json
import os
import re
import subprocess
import time

from ansible.module_utils.basic import AnsibleModule

# A session's ID, what Claude Code names a transcript.
SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
VERSION = re.compile(r"\d+(?:\.\d+)+")


def _version_key(text):
    return tuple(int(part) for part in text.split("."))


def summarize(path):
    """What the transcript at ``path`` says about itself: cwd, version, branch, conversation.

    Every complete line is parsed, and only those four values are kept.
    """
    found = {"cwd": "", "version": "", "branch": "", "conversation": False}
    with open(path, "rb") as handle:
        for line in handle:
            if not line.endswith(b"\n"):
                break
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            cwd, version, branch = entry.get("cwd"), entry.get("version"), entry.get("gitBranch")
            if not found["cwd"] and isinstance(cwd, str):
                found["cwd"] = cwd
            if isinstance(version, str) and VERSION.fullmatch(version):
                if not found["version"] or _version_key(version) > _version_key(found["version"]):
                    found["version"] = version
            if isinstance(branch, str) and branch:
                found["branch"] = branch
            if entry.get("type") in ("user", "assistant"):
                found["conversation"] = True
    return found


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    return True


def _is_claude(argv):
    """Whether a process's argv is Claude Code's: the native binary, or the npm package under node."""
    names = [os.path.basename(arg) for arg in argv[:2]]
    return "claude" in names or any("@anthropic-ai/claude-code" in arg for arg in argv[:2])


def _argv(pid, proc="/proc"):
    """The argv of process ``pid``, from /proc where there is one, else ps; None when it cannot be read."""
    if os.path.isdir(os.path.join(proc, "self")):
        try:
            with open(os.path.join(proc, str(pid), "cmdline"), "rb") as handle:
                return [part.decode("utf-8", "replace") for part in handle.read().split(b"\0") if part]
        except FileNotFoundError:
            return []
        except OSError:
            return None
    try:
        found = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return found.stdout.split()


def _running_claude(pid, proc="/proc"):
    """Whether ``pid`` is a running claude. A pid reused by another program since is not; one unreadable is."""
    if not _alive(pid):
        return False
    argv = _argv(pid, proc)
    return argv is None or _is_claude(argv)


def open_sessions(path, proc="/proc"):
    """Session ID to the pid of a running claude that has it open, from Claude Code's sessions/ registry.

    Each file is named for its claude's pid, so a claude that starts under a pid a stale entry names
    replaces that entry: only a pid another program took since can be stale, and that one is checked.
    """
    found = {}
    try:
        names = os.listdir(os.path.join(path, "sessions"))
    except OSError:
        return found
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(path, "sessions", name), encoding="utf-8") as handle:
                entry = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        pid, session = entry.get("pid"), entry.get("sessionId")
        if isinstance(pid, int) and pid > 0 and isinstance(session, str) and _running_claude(pid, proc):
            found[session] = pid
    return found


def list_sessions(path, days, now=None, opened=None):
    """Every transcript under ``path``/projects written in the last ``days`` days.

    ``opened`` is open_sessions'.
    """
    now = time.time() if now is None else now
    projects, sessions = os.path.join(path, "projects"), []
    opened = open_sessions(path) if opened is None else opened
    try:
        folders = sorted(
            (entry for entry in os.scandir(projects) if entry.is_dir(follow_symlinks=False)),
            key=lambda entry: entry.name,
        )
    except OSError:
        return sessions
    for folder in folders:
        try:
            files = sorted(os.scandir(folder.path), key=lambda entry: entry.name)
        except OSError:
            continue
        for item in files:
            session = item.name[: -len(".jsonl")] if item.name.endswith(".jsonl") else ""
            try:
                if not SESSION_ID.match(session) or not item.is_file(follow_symlinks=False):
                    continue
                # A transcript Claude Code's sweep, or the user, removes as this lists them is not listed.
                stat = item.stat(follow_symlinks=False)
            except OSError:
                continue
            if not (days > 0 and now - stat.st_mtime <= days * 86400):
                continue
            try:
                summary = summarize(item.path)
            except OSError:
                continue
            sessions.append(
                {
                    "id": session,
                    "folder": folder.name,
                    "mtime": stat.st_mtime,
                    "open": opened.get(session, 0),
                    "tools": os.path.isdir(os.path.join(folder.path, session, "tool-results")),
                    "dir": os.path.isdir(os.path.join(folder.path, session)),
                    **summary,
                }
            )
    return sessions


def repo_paths(path):
    """githubRepoPaths from the .claude.json Claude Code keeps in ``path``, or beside it in the home directory."""
    for candidate in (
        os.path.join(path, ".claude.json"),
        os.path.join(os.path.dirname(path.rstrip("/")), ".claude.json"),
    ):
        try:
            with open(candidate, encoding="utf-8") as handle:
                listed = json.load(handle).get("githubRepoPaths")
        except (OSError, ValueError, AttributeError):
            continue
        # One without the key is not the one Claude Code keeps it in: the next is asked.
        if isinstance(listed, dict):
            return {
                str(repo): [str(item) for item in paths if isinstance(item, str)]
                for repo, paths in listed.items()
                if isinstance(paths, list)
            }
    return {}


def claude_version(candidates):
    """The version the first Claude Code there is reports, or ""."""
    found = next((path for path in candidates if os.path.isfile(path) and os.access(path, os.X_OK)), None)
    if not found:
        return ""
    try:
        answer = subprocess.run(
            [found, "--version"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    version = VERSION.search(answer.stdout or "")
    return version.group(0) if answer.returncode == 0 and version else ""


def main():
    module = AnsibleModule(
        argument_spec={
            "path": {"type": "path", "required": True},
            "days": {"type": "int", "default": 0},
            "repo_paths": {"type": "bool", "default": False},
            "claude": {"type": "list", "elements": "path", "default": []},
        },
        supports_check_mode=True,
    )
    params = module.params
    opened = open_sessions(params["path"])
    module.exit_json(
        changed=False,
        sessions=list_sessions(params["path"], params["days"], opened=opened),
        registered=opened,
        repo_paths=repo_paths(params["path"]) if params["repo_paths"] else {},
        version=claude_version(params["claude"]) if params["claude"] else "",
    )


if __name__ == "__main__":
    main()
