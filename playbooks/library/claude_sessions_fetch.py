#!/usr/bin/python
# Copyright (c) claude-on-proxmox contributors
# MIT License
"""Bring a VM's Claude Code sessions here as plain files and folders, whatever the VM sends."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: claude_sessions_fetch
short_description: Bring entries of a VM's Claude Code projects folder here, as plain files and folders only
version_added: "1.0.0"
description:
  - Runs C(tar) on the VM, over SSH as O(ssh) says, for the entries O(names) of O(root), and unpacks what
    it sends into O(dest), a folder it creates here, 0700, with every folder in it 0700 and every file
    0600 and given the time the archive says. Nothing else of the archive is kept.
  - What the VM sends is not trusted. A file or folder is written only at a path inside O(dest) that is
    one of O(names) or inside one, named without C(..), an absolute path or an empty component, and
    never through a link: nothing here is a link, since a link, a hard link, a device or a pipe in the
    archive is left out and named. Nothing is ever written outside O(dest).
  - Fails, having written nothing outside O(dest), when SSH or tar fails, when the archive ends early, or
    when an entry asked for did not arrive. make destroy then keeps the VM (playbooks/tasks/session_save.yml).
  - Nothing of what the files hold is returned, only how many and the names left out.
options:
  dest:
    description: The folder to create here and unpack into. It must not exist; the folders above it are made 0700.
    type: path
    required: true
  root:
    description: The VM's Claude Code projects folder, C(~/.claude/projects), as an absolute path there.
    type: str
    required: true
  names:
    description:
      - The entries of O(root) to bring, each C(<folder>/<id>.jsonl), C(<folder>/<id>) or C(<folder>/memory),
        a folder brought whole. A folder is named as Claude Code names one, ASCII letters, digits and "-"
        only, and an ID is a session's.
    type: list
    elements: str
    required: true
  ssh:
    description:
      - How to reach the VM, as Ansible reaches it. Without it, the VM is this machine, as in the tests.
    type: dict
    suboptions:
      user:
        description: The user to log in as.
        type: str
        default: ""
      host:
        description: The address to connect to.
        type: str
        required: true
      port:
        description: The port, when the inventory gives one.
        type: str
        default: ""
      private_key_file:
        description: The key, when the inventory gives one.
        type: str
        default: ""
      args:
        description: Ansible's ssh_common_args and ssh_extra_args, split as Ansible splits them.
        type: str
        default: ""
author:
  - claude-on-proxmox contributors
"""

EXAMPLES = r"""
- name: Bring the VM's own sessions here
  claude_sessions_fetch:
    dest: /Users/me/claude-on-proxmox/.sessions/alpha-20261023-153000/projects
    root: /home/dev/.claude/projects
    names:
      - -home-dev-projects-demo/0a608ad8-1b2c-4d3e-8f90-123456789abc.jsonl
      - -home-dev-projects-demo/memory
    ssh:
      user: dev
      host: 192.0.2.10
  delegate_to: localhost
"""

RETURN = r"""
files:
  description: How many files were written.
  returned: always
  type: int
folders:
  description: How many folders were made, O(dest) among them.
  returned: always
  type: int
left_out:
  description: Each entry of the archive not written, with why, by its name in the archive; the first 50.
  returned: always
  type: list
  elements: dict
left_out_count:
  description: How many entries of the archive were not written.
  returned: always
  type: int
"""

import os
import re
import shlex
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading

from ansible.module_utils.basic import AnsibleModule

SESSION_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# What make destroy asks for: a transcript, the folder beside it, or a project's auto memory.
NAME = re.compile(rf"^[A-Za-z0-9-]+/(?:{SESSION_ID}(?:\.jsonl)?|memory)$")
# How many of the entries left out are named: a VM can send any number.
LISTED = 50
# A host or user that ssh could read as an option, or that is not one word.
WORD = re.compile(r"^[^\s-][^\s]*$")


class Refused(Exception):
    """An entry of the archive that is not written; the run goes on without it."""


def ssh_argv(ssh, command):
    """The ssh that runs ``command`` on the VM, never asking for anything."""
    host, user = str(ssh.get("host") or ""), str(ssh.get("user") or "")
    if not WORD.match(host) or (user and not WORD.match(user)):
        raise ValueError(f"{user}@{host} is not a user and a host ssh can be given")
    argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new"]
    argv += ["-o", "Compression=yes"]
    if ssh.get("port"):
        argv += ["-p", str(int(ssh["port"]))]
    if ssh.get("private_key_file"):
        argv += ["-i", str(ssh["private_key_file"])]
    argv += shlex.split(str(ssh.get("args") or ""))
    return [*argv, f"{user}@{host}" if user else host, command]


def tar_command(root):
    """tar on the VM: the names come on stdin, one per line, each after "./" so none reads as an option."""
    return f"tar -cf - -C {shlex.quote(root)} -T -"


def _relative(name):
    """The archive name ``name`` as a path inside the projects folder, or Refused."""
    path = name
    while path.startswith("./"):
        path = path[2:]
    path = path.rstrip("/")
    if not path or path.startswith("/") or "\0" in path:
        raise Refused("not a path inside the folder")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise Refused("not a path inside the folder")
    return parts


def _folder(top, parts, made):
    """Make each folder of ``parts`` under ``top`` that is missing, 0700; one that is there must be a folder."""
    path = top
    for part in parts:
        path = os.path.join(path, part)
        try:
            os.mkdir(path, 0o700)
            os.chmod(path, 0o700)
            made.append(path)
        except FileExistsError:
            if not stat.S_ISDIR(os.lstat(path).st_mode):
                raise Refused("a file stands where a folder of it is") from None
    return path


def unpack(stream, dest, names):
    """Unpack the tar ``stream`` into the folder ``dest`` (there already, empty): (files, folders, left_out, arrived).

    Only plain files and folders, and only at ``names`` or inside one of the folders among them: a
    transcript is a file, and nothing is inside it. Everything else is left out.
    """
    wanted = {tuple(name.split("/")): name.endswith(".jsonl") for name in names}
    files, made, left_out, arrived = 0, [], [], set()
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            try:
                parts = _relative(member.name)
                top = next((key for key in wanted if tuple(parts[: len(key)]) == key), None)
                if top is None or (wanted[top] and len(parts) > len(top)):
                    raise Refused("not asked for")
                exact = len(parts) == len(top)
                if member.isdir():
                    if exact and wanted[top]:
                        raise Refused("a folder where a transcript is asked for")
                    _folder(dest, parts, made)
                elif member.isreg():
                    if exact and not wanted[top]:
                        raise Refused("a file where a folder is asked for")
                    _write(_folder(dest, parts[:-1], made), parts[-1], archive.extractfile(member), member.mtime)
                    files += 1
                elif member.issym() or member.islnk():
                    raise Refused("a link")
                else:
                    raise Refused("not a plain file or folder")
                if exact:
                    arrived.add("/".join(top))
            except Refused as why:
                left_out.append({"name": member.name, "why": str(why)})
    return files, len(made), left_out, arrived


def _write(folder, name, data, mtime):
    """Write ``data`` to a new file ``name`` in ``folder``, 0600, with ``mtime``; never through a link."""
    try:
        handle = os.open(os.path.join(folder, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise Refused("named twice, or a folder of that name came first") from None
    with os.fdopen(handle, "wb") as out:
        shutil.copyfileobj(data, out, 1 << 20)
        out.flush()
        os.fchmod(out.fileno(), 0o600)
        try:
            os.utime(out.fileno(), (mtime, mtime))
        except (OverflowError, ValueError, OSError):
            pass


class _Counted:
    """A stream that counts what is read from it: a whole archive is a number of 512-byte blocks."""

    def __init__(self, stream):
        self.stream, self.count = stream, 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.count += len(data)
        return data


def _feed(handle, text):
    try:
        handle.write(text)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            handle.close()
        except OSError:
            pass


def _said(errors):
    """What ssh and tar printed on stderr, as ": <text>", or ""."""
    errors.seek(0)
    text = errors.read().decode("utf-8", "replace").strip()
    return f": {text}" if text else ""


def fetch(dest, root, names, ssh=None):
    """Bring ``names`` of ``root`` into ``dest``: the result, or raise RuntimeError with what went wrong."""
    for name in names:
        if not NAME.match(name):
            raise RuntimeError(f"{name!r} is not a Claude Code transcript, a session's folder or a memory folder.")
    if not root.startswith("/"):
        raise RuntimeError(f"{root} is not an absolute path on the VM.")
    command = tar_command(root)
    argv = ssh_argv(ssh, command) if ssh else ["sh", "-c", command]
    parent = os.path.dirname(dest.rstrip("/"))
    if not os.path.isdir(parent):
        os.makedirs(parent, 0o700)
        os.chmod(parent, 0o700)
    os.mkdir(dest, 0o700)
    os.chmod(dest, 0o700)
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        feeder = threading.Thread(
            target=_feed, args=(process.stdin, "".join(f"./{name}\n" for name in names).encode()), daemon=True
        )
        feeder.start()
        stream = _Counted(process.stdout)
        try:
            files, folders, left_out, arrived = unpack(stream, dest, names)
            # What follows the archive's end is tar's padding: read to the end, so that ssh never
            # writes to a pipe already closed, but no further than a record's worth would need.
            extra = 0
            while extra <= 1 << 20:
                block = stream.read(1 << 16)
                if not block:
                    break
                extra += len(block)
            if extra > 1 << 20:
                process.kill()
                process.wait()
                raise RuntimeError("the VM sent more than the archive")
            # tarfile reads an archive cut short after its first header as one that ended.
            if stream.count % 512:
                raise tarfile.ReadError("it stops part way through a block")
        except tarfile.TarError as error:
            process.kill()
            process.wait()
            raise RuntimeError(f"what came back is not a whole archive ({error}){_said(errors)}") from None
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            process.stdout.close()
            feeder.join()
        rc = process.wait()
        said = _said(errors)
    # 255 is ssh's own failure. GNU tar, the VM's, exits 2 for a name it cannot find and sends the
    # rest, so that is said as the name; tar's 1 is a file that changed as it was read, still sent.
    if rc == 255 and ssh:
        raise RuntimeError(f"ssh exited {rc}{said}")
    missing = sorted(set(names) - arrived)
    if missing:
        raise RuntimeError(f"{', '.join(missing)} did not arrive{said}")
    if rc not in (0, 1):
        raise RuntimeError(f"tar exited {rc}{said}")
    return {"files": files, "folders": folders + 1, "left_out": left_out[:LISTED], "left_out_count": len(left_out)}


def main():
    module = AnsibleModule(
        argument_spec={
            "dest": {"type": "path", "required": True},
            "root": {"type": "str", "required": True},
            "names": {"type": "list", "elements": "str", "required": True},
            "ssh": {
                "type": "dict",
                "options": {
                    "user": {"type": "str", "default": ""},
                    "host": {"type": "str", "required": True},
                    "port": {"type": "str", "default": ""},
                    "private_key_file": {"type": "str", "default": ""},
                    "args": {"type": "str", "default": ""},
                },
            },
        },
        supports_check_mode=True,
    )
    params = module.params
    if module.check_mode:
        module.exit_json(changed=True, files=0, folders=0, left_out=[], left_out_count=0)
    try:
        result = fetch(params["dest"], params["root"], params["names"], params["ssh"])
    except (RuntimeError, ValueError, OSError) as error:
        module.fail_json(msg=f"The sessions could not be brought here: {error}")
    module.exit_json(changed=True, **result)


if __name__ == "__main__":
    main()
