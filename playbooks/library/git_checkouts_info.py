#!/usr/bin/python
# Copyright (c) claude-on-proxmox contributors
# MIT License
"""Read git checkouts - their origin, and on request their state - without taking git's locks."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: git_checkouts_info
short_description: Read git checkouts without changing them
version_added: "1.0.0"
description:
  - Reports, for each directory asked about, whether it is in a git checkout, the checkout's top level and its
    C(origin) URL as configured; with O(status), whether its tree is clean, what HEAD is and whether a ref
    holds its commit; with O(branch), that branch's commit and its upstream's; with O(target), what in the
    tree a move to that commit would overwrite; with O(busy), the claude processes working inside it.
  - Every git command runs as C(git --no-optional-locks), so none refreshes the index under C(index.lock), which
    could collide with a git, an editor or a Claude working in the same checkout. Nothing is changed.
  - Paths and the branch are taken as written: no C(~) or C($VAR) in them is expanded, since a directory a
    transcript names, or a branch, can hold either.
options:
  paths:
    description: The directories to report on, each as it is; one that does not exist is reported as such.
    type: list
    elements: str
    default: []
  under:
    description: Also report on every git checkout directly inside this directory.
    type: str
  status:
    description: Also report whether each tree is clean, the branch HEAD is on and HEAD's commit.
    type: bool
    default: false
  branch:
    description:
      - Also report this branch's commit, the remote and branch it tracks, and the commit its upstream is at.
      - Refused unless C(git check-ref-format --branch) accepts it and it does not start with "-".
    type: str
  target:
    description:
      - Also report the paths a move of the tree from HEAD to this commit, a full hex ID, would overwrite or
        remove that git does not track - an ignored file among them, which C(git status) does not show and
        git overwrites without asking.
    type: str
  busy:
    description: Also report the pids of this user's claude processes whose directory is inside each checkout (Linux).
    type: bool
    default: false
author:
  - claude-on-proxmox contributors
"""

EXAMPLES = r"""
- name: Read the origin of every project on the VM
  git_checkouts_info:
    under: /home/dev/projects
  register: found
"""

RETURN = r"""
checkouts:
  description: One item per directory, O(paths) first, then O(under)'s checkouts by name.
  returned: always
  type: list
  elements: dict
  contains:
    path:
      description: The directory as asked about, or found under O(under).
      type: str
    name:
      description: Its last component.
      type: str
    exists:
      description: Whether it is a directory.
      type: bool
    git:
      description: Whether it is inside a git checkout.
      type: bool
    toplevel:
      description: The checkout's top level, or "".
      type: str
    origin:
      description:
        - The URL of its C(origin) remote as configured, or "": never rewritten by a C(url.<base>.insteadOf),
          which names where git connects to, not the repository.
        - Without what precedes the host in a URL with a scheme, which can be a token.
      type: str
    clean:
      description: With O(status), whether C(git status --porcelain) prints nothing.
      type: bool
    head_branch:
      description: With O(status), the branch HEAD is on, or "" when it is detached.
      type: str
    head:
      description: With O(status), HEAD's commit, or "".
      type: str
    head_kept:
      description:
        - With O(status), whether HEAD is on a branch or a branch, remote-tracking branch or tag holds its commit:
          false for a detached HEAD whose commits a switch would leave to garbage collection.
      type: bool
    branch_head:
      description: With O(branch), that branch's commit here, or "" when there is no such branch.
      type: str
    upstream_remote:
      description: With O(branch), the remote it tracks, or "".
      type: str
    upstream_branch:
      description: With O(branch), the branch on that remote it tracks, or "".
      type: str
    upstream_head:
      description: With O(branch), the commit its upstream is at here, or "".
      type: str
    overwrites:
      description: With O(target), the paths git does not track that the move would overwrite or remove, sorted.
      type: list
      elements: str
    busy:
      description: With O(busy), the pids of this user's claude processes working inside it.
      type: list
      elements: int
"""

import os
import re

from ansible.module_utils.basic import AnsibleModule

GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
# What precedes the host in a URL with a scheme: https://<token>@github.com/... names a credential.
USERINFO = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/@]*@")
COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _git(module, path, *args):
    """git's output in ``path``, stripped, or None when it failed."""
    rc, out, _ = module.run_command(
        ["git", "--no-optional-locks", "-C", path, *args], environ_update=GIT_ENV, expand_user_and_vars=False
    )
    return out.strip() if rc == 0 else None


def _claude(argv):
    """Whether a process's argv is Claude Code's: the native binary, or the npm package under node."""
    names = [os.path.basename(arg) for arg in argv[:2]]
    return b"claude" in names or any(b"@anthropic-ai/claude-code" in arg for arg in argv[:2])


def claude_processes(proc="/proc"):
    """(pid, cwd) of every claude process of this user, from /proc; none where there is no /proc."""
    found = []
    try:
        names = os.listdir(proc)
    except OSError:
        return found
    for name in names:
        if not name.isdigit():
            continue
        base = os.path.join(proc, name)
        try:
            if os.stat(base).st_uid != os.getuid():
                continue
            with open(os.path.join(base, "cmdline"), "rb") as handle:
                argv = handle.read().split(b"\0")
            cwd = os.readlink(os.path.join(base, "cwd"))
        except OSError:
            continue
        if _claude(argv):
            found.append((int(name), cwd))
    return found


def _held(module, path):
    """Whether a branch, remote-tracking branch or tag holds HEAD's commit."""
    held = _git(
        module,
        path,
        "for-each-ref",
        "--count=1",
        "--contains",
        "HEAD",
        "--format=%(refname)",
        "refs/heads",
        "refs/remotes",
        "refs/tags",
    )
    return bool(held)


def overwrites(module, toplevel, target):
    """The paths of ``toplevel`` git does not track that moving its tree from HEAD to ``target`` would replace.

    Each path ``target`` adds where something is already there, and each folder on the way to one that
    is a file or a link here, unless HEAD tracks it and the move removes it: git replaces an ignored
    file without asking, and an untracked one would have made the tree unclean already. None when git
    cannot compare the two.
    """
    rc, out, _ = module.run_command(
        [
            "git",
            "--no-optional-locks",
            "-C",
            toplevel,
            "diff",
            "--name-status",
            "-z",
            "--no-renames",
            "HEAD",
            target,
            "--",
        ],
        environ_update=GIT_ENV,
        expand_user_and_vars=False,
    )
    if rc != 0:
        return None
    fields = out.split("\0")
    changes = list(zip(fields[0::2], fields[1::2], strict=False))
    added = [name for status, name in changes if status == "A"]
    removed = {name for status, name in changes if status == "D"}
    found = set()
    for name in added:
        parts = name.split("/")
        for depth in range(1, len(parts)):
            folder = "/".join(parts[:depth])
            here = os.path.join(toplevel, folder)
            if folder in removed:
                continue
            if os.path.islink(here) or (os.path.lexists(here) and not os.path.isdir(here)):
                found.add(folder)
                break
        else:
            here = os.path.join(toplevel, name)
            if os.path.islink(here) or (os.path.lexists(here) and not os.path.isdir(here)):
                found.add(name)
            elif os.path.isdir(here) and _untracked_under(here, name, removed):
                found.add(name)
    return sorted(found)


def _untracked_under(path, name, removed):
    """Whether the folder ``path`` (``name`` in the tree) holds a file the move does not remove."""
    for top, dirs, files in os.walk(path):
        for entry in files + [item for item in dirs if os.path.islink(os.path.join(top, item))]:
            relative = os.path.relpath(os.path.join(top, entry), path)
            if f"{name}/{relative}" not in removed:
                return True
    return False


def _inside(path, top):
    return path == top or path.startswith(top.rstrip("/") + "/")


def read(module, path, status, branch, processes, target=""):
    item = {"path": path, "name": os.path.basename(path.rstrip("/")), "exists": os.path.isdir(path)}
    toplevel = _git(module, path, "rev-parse", "--show-toplevel") if item["exists"] else None
    item.update(git=toplevel is not None, toplevel=toplevel or "")
    # The first URL, which is the one git fetches from, as `git remote get-url` gives it but unrewritten.
    origin = (_git(module, path, "config", "--get-all", "remote.origin.url") or "") if toplevel else ""
    # Never with what precedes the host: a token there would reach the run's output and its log.
    item["origin"] = USERINFO.sub(r"\1", origin.splitlines()[0]) if origin else ""
    if status:
        # Whatever the checkout's own config says: status.showUntrackedFiles=no and a submodule's
        # ignore would hide what a move could lose.
        item["clean"] = (
            bool(toplevel)
            and _git(module, path, "status", "--porcelain", "--untracked-files=normal", "--ignore-submodules=none")
            == ""
        )
        item["head_branch"] = (
            (_git(module, path, "symbolic-ref", "--quiet", "--short", "HEAD") or "") if toplevel else ""
        )
        item["head"] = (
            (_git(module, path, "rev-parse", "--verify", "--quiet", "HEAD^{commit}") or "") if toplevel else ""
        )
        item["head_kept"] = bool(item["head_branch"]) or (bool(item["head"]) and _held(module, path))
    if branch:
        item["branch_head"] = upstream = ""
        item["upstream_remote"] = item["upstream_branch"] = item["upstream_head"] = ""
        if toplevel:
            item["branch_head"] = (
                _git(module, path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}") or ""
            )
            item["upstream_remote"] = _git(module, path, "config", "--get", f"branch.{branch}.remote") or ""
            merge = _git(module, path, "config", "--get", f"branch.{branch}.merge") or ""
            item["upstream_branch"] = merge[len("refs/heads/") :] if merge.startswith("refs/heads/") else ""
            if item["upstream_remote"] and item["upstream_branch"]:
                upstream = f"refs/remotes/{item['upstream_remote']}/{item['upstream_branch']}^{{commit}}"
                item["upstream_head"] = _git(module, path, "rev-parse", "--verify", "--quiet", upstream) or ""
    if target:
        found = overwrites(module, toplevel, target) if toplevel else None
        if found is None:
            module.fail_json(msg=f"git could not compare HEAD with {target} in {path}.")
        item["overwrites"] = found
    if processes is not None:
        top = os.path.realpath(toplevel) if toplevel else ""
        item["busy"] = sorted(pid for pid, cwd in processes if top and _inside(cwd, top))
    return item


def main():
    module = AnsibleModule(
        argument_spec={
            # str, not path: a path option expands ~ and $VAR, and a directory a transcript names is as written.
            "paths": {"type": "list", "elements": "str", "default": []},
            "under": {"type": "str"},
            "status": {"type": "bool", "default": False},
            "branch": {"type": "str"},
            "target": {"type": "str"},
            "busy": {"type": "bool", "default": False},
        },
        supports_check_mode=True,
    )
    params = module.params
    branch = params["branch"] or ""
    if branch:
        rc, _, _ = module.run_command(
            ["git", "check-ref-format", "--branch", branch], environ_update=GIT_ENV, expand_user_and_vars=False
        )
        if rc != 0 or branch.startswith("-"):
            module.fail_json(msg=f"{branch!r} is not a branch name git accepts.")
    target = params["target"] or ""
    if target and not COMMIT.match(target):
        module.fail_json(msg=f"{target!r} is not a full commit ID.")
    processes = claude_processes() if params["busy"] else None
    paths = list(params["paths"])
    if params["under"]:
        try:
            names = sorted(entry.name for entry in os.scandir(params["under"]) if entry.is_dir(follow_symlinks=False))
        except OSError:
            names = []
        paths += [os.path.join(params["under"], name) for name in names]
    found = [read(module, path, params["status"], branch, processes, target) for path in paths]
    if params["under"]:
        under = len(params["paths"])
        # A checkout's own top level, compared resolved: git names it by its real path.
        found = found[:under] + [
            item
            for item in found[under:]
            if item["git"] and os.path.realpath(item["toplevel"]) == os.path.realpath(item["path"])
        ]
    module.exit_json(changed=False, checkouts=found)


if __name__ == "__main__":
    main()
