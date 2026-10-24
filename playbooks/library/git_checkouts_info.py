#!/usr/bin/python
# Copyright (c) claude-on-proxmox contributors
# MIT License
"""Read git checkouts' origins without taking git's locks."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: git_checkouts_info
short_description: Read git checkouts without changing them
version_added: "1.0.0"
description:
  - Reports, for each directory asked about, whether it is in a git checkout, the checkout's top level and its
    C(origin) URL as configured.
  - Every git command runs as C(git --no-optional-locks), so none refreshes the index under C(index.lock), which
    could collide with a git, an editor or a Claude working in the same checkout. Nothing is changed.
  - Paths are taken as written: no C(~) or C($VAR) in them is expanded, since a directory a transcript names
    can hold either.
options:
  paths:
    description: The directories to report on, each as it is; one that does not exist is reported as such.
    type: list
    elements: str
    default: []
  under:
    description: Report on every git checkout directly inside this directory instead of on O(paths).
    type: str
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
  description: One item per directory, in O(paths)' order or O(under)'s checkouts by name.
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
"""

import os
import re

from ansible.module_utils.basic import AnsibleModule

GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
# What precedes the host in a URL with a scheme: https://<token>@github.com/... names a credential.
USERINFO = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/@]*@")


def _git(module, path, *args):
    """git's output in ``path``, stripped, or None when it failed."""
    rc, out, _ = module.run_command(
        ["git", "--no-optional-locks", "-C", path, *args], environ_update=GIT_ENV, expand_user_and_vars=False
    )
    return out.strip() if rc == 0 else None


def read(module, path):
    item = {"path": path, "name": os.path.basename(path.rstrip("/")), "exists": os.path.isdir(path)}
    toplevel = _git(module, path, "rev-parse", "--show-toplevel") if item["exists"] else None
    item.update(git=toplevel is not None, toplevel=toplevel or "")
    # The first URL, which is the one git fetches from, as `git remote get-url` gives it but unrewritten.
    origin = (_git(module, path, "config", "--get-all", "remote.origin.url") or "") if toplevel else ""
    # Never with what precedes the host: a token there would reach the run's output and its log.
    item["origin"] = USERINFO.sub(r"\1", origin.splitlines()[0]) if origin else ""
    return item


def main():
    module = AnsibleModule(
        argument_spec={
            # str, not path: a path option expands ~ and $VAR, and a directory a transcript names is as written.
            "paths": {"type": "list", "elements": "str", "default": []},
            "under": {"type": "str"},
        },
        mutually_exclusive=[["paths", "under"]],
        supports_check_mode=True,
    )
    params = module.params
    paths = params["paths"]
    if params["under"]:
        try:
            names = sorted(entry.name for entry in os.scandir(params["under"]) if entry.is_dir(follow_symlinks=False))
        except OSError:
            names = []
        paths = [os.path.join(params["under"], name) for name in names]
    found = [read(module, path) for path in paths]
    if params["under"]:
        # A checkout's own top level, compared resolved: git names it by its real path.
        found = [
            item
            for item in found
            if item["git"] and os.path.realpath(item["toplevel"]) == os.path.realpath(item["path"])
        ]
    module.exit_json(changed=False, checkouts=found)


if __name__ == "__main__":
    main()
