#!/usr/bin/python
# Copyright (c) claude-on-proxmox contributors
# MIT License
"""Write the copies of another machine's Claude Code sessions that the VM resumes, from the files rsync brought."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: claude_sessions_copy
short_description: Write the copies of another machine's Claude Code sessions that this one resumes
version_added: "1.0.0"
description:
  - Reads each session from O(mirror), where rsync left this machine's copy of the other machine's
    C(projects/<folder>/<id>.jsonl), and writes it to C(<dest>/projects/<folder>/<id>.jsonl) with four
    edits and nothing else, its complete lines only, 0600 in a 0700 folder. Its C(tool-results/) folder
    comes along. Subagent transcripts never do.
  - The four edits. The memory folder named in each C(prompt_snapshot) attachment, which resume sends
    as the system prompt until the conversation is compacted, becomes O(sessions[].memory). The folder
    the other machine saved this session's large tool outputs in, as its tool results name it, becomes
    the copy's C(tool-results/) here, in user entries only - the tool results - so that a resumed session
    can read them; an assistant's entries, which may carry signed thinking, are never changed. When the
    last C(worktree-state) entry binds the session to a worktree, an entry clearing it is appended:
    Claude Code clears a binding to a worktree that is gone itself, but refuses to resume one whose path
    is there and is not that worktree, as where both machines have it. A C(relocated) entry naming
    O(sessions[].checkout) is appended last, so that the C(/resume) picker opens it there.
  - A copy keeps the other machine's file time while that is in the last 14 days, and is otherwise given
    the time it is written, renewed once that is 14 days old: Claude Code's sweep deletes a transcript
    here 30 days after its file time (cleanupPeriodDays), which would take a session asked for by ID,
    whatever its age, before it was resumed.
  - The records Claude Code keeps of each request it sent (C(api-request-shape), C(api-request-blob),
    since 2.1.300) name the other machine's memory folder too, and stay as they are: a resume sends the
    C(prompt_snapshot), never them (2.1.308, held by the configure scenario).
  - A copy is replaced only while it is exactly what this module last wrote, as O(manifest) records by
    digest. One that differs was continued here, or was never this module's, and is left as it is.
  - A copy that is gone while its session's ID is in another folder of C(<dest>/projects/) moved there,
    as C(/cd) moves a session it continues, and is left the same way: written again, the ID would be in
    two folders, and C(claude --resume <id>) from a third would find neither. But where that other copy
    is this module's own, still as written, the session moved on the other machine instead: it is written
    in its new folder, and the old copy removed.
  - O(manifest) is written after each copy, so a run that stops part way still records what it wrote.
  - Nothing of a session's content is returned.
options:
  mirror:
    description: The folder rsync copied the other machine's C(projects/) folders into.
    type: path
    required: true
  dest:
    description: Claude Code's configuration directory here, C(~/.claude).
    type: path
    required: true
  manifest:
    description: The file that records the digest of each copy this module wrote.
    type: path
    required: true
  here:
    description:
      - The other machine's Claude Code configuration directory, as its sessions name it. A copy whose
        recorded prompt still names a memory folder under it after the edit is not written.
    type: str
    default: ""
  sessions:
    description: The sessions to copy.
    type: list
    elements: dict
    required: true
    suboptions:
      id:
        description: The session's ID.
        type: str
        required: true
      folder:
        description: Its folder under C(projects/), kept as it is.
        type: str
        required: true
      checkout:
        description: The checkout here it continues in.
        type: str
        required: true
      memory:
        description: The memory folder here its recorded prompt is pointed at.
        type: str
        required: true
  prune:
    description: Remove from O(mirror) every session that is not in O(sessions).
    type: bool
    default: true
author:
  - claude-on-proxmox contributors
"""

EXAMPLES = r"""
- name: Write the copies
  claude_sessions_copy:
    mirror: /home/dev/projects/.claude-on-proxmox/claude-sessions/mirror
    dest: /home/dev/.claude
    manifest: /home/dev/projects/.claude-on-proxmox/claude-sessions/manifest.json
    sessions:
      - id: 0a608ad8-0c5e-4d8e-9a7e-2f6d1f0b9c11
        folder: -Users-me-homeserver
        checkout: /home/dev/projects/homeserver
        memory: /home/dev/.claude/projects/-home-dev-projects-homeserver/memory/
"""

RETURN = r"""
written:
  description: The sessions whose copy was written or replaced.
  returned: always
  type: list
  elements: str
unchanged:
  description: The sessions whose copy was already what it would be.
  returned: always
  type: list
  elements: str
held:
  description: Each session left as it is because its copy is in another folder here, to that folder.
  returned: always
  type: dict
continued:
  description: The sessions continued here since they were copied, and not changed on the other machine since.
  returned: always
  type: list
  elements: str
diverged:
  description: The sessions whose copy here differs from what was copied, and that changed on the other machine too.
  returned: always
  type: list
  elements: str
unreadable:
  description: The sessions not written, each with why.
  returned: always
  type: list
  elements: dict
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
import time

from ansible.module_utils.basic import AnsibleModule

# How Claude Code's system prompt names the auto-memory folder (2.1.308): read from each entry, never computed,
# since the folder is Claude Code's own name for the directory the session was started in.
MEMORY_AT = re.compile(r"persistent file-based memory at `([^`\n]+)`")
MANIFEST_FORMAT = 1
SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# A copy keeps the other machine's file time while it is this recent; Claude Code sweeps a transcript
# 30 days after its file time.
FRESH = 14 * 86400


def _escaped(text):
    """``text`` as it stands inside a JSON string in Claude Code's transcripts, which JSON.stringify writes."""
    return json.dumps(text, ensure_ascii=False)[1:-1].encode("utf-8")


def _line(entry):
    return json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def _entry(line):
    try:
        entry = json.loads(line)
    except ValueError:
        return {}
    return entry if isinstance(entry, dict) else {}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def complete_lines(data):
    """``data`` up to its last newline: a line still being written is left for the next copy."""
    return data[: data.rfind(b"\n") + 1]


def _left_behind(here, folders, found):
    """What a recorded prompt still naming the session's own memory folder here looks like, after the edit.

    The session's own: the folder of each memory path the prompt names, and ``folders``. Without any,
    every memory folder under ``here``. Another project's, which a prompt can quote, is left as text.
    """
    projects = here.rstrip("/") + "/projects/"
    own = set(folders)
    for path in found:
        named = re.match(re.escape(projects) + r"([^/]+)/memory", path, re.IGNORECASE)
        if named:
            own.add(named.group(1))
    which = b"|".join(re.escape(_escaped(name)) for name in sorted(own)) if own else rb'[^"/\\]+'
    return re.compile(re.escape(_escaped(projects)) + rb"(?:" + which + rb")/memory", re.IGNORECASE)


def edit_transcript(data, session, checkout, memory, here="", results="", folder=""):
    """The copy of transcript ``data`` this machine resumes: (bytes, "") or (b"", why it is not written).

    ``data`` holds complete lines only. Every line comes out byte for byte but a prompt_snapshot
    attachment's, where only the memory folder's path changes, and a user entry naming this session's
    tool-results folder under ``here``, which becomes ``results``; two entries may be appended after them.
    ``folder`` is the session's folder here, whose memory folder the prompt must no longer name.
    """
    out, bound = [], False
    memory_escaped = _escaped(memory)
    # Any folder of the other machine's: a session continued after /cd there names the old one.
    results_at = (
        re.compile(
            re.escape(_escaped(here.rstrip("/") + "/projects/"))
            + rb'[^"/\\]+/'
            + re.escape(session.encode())
            + rb"/tool-results/",
            re.IGNORECASE,
        )
        if here and results
        else None
    )
    results_escaped = _escaped(results.rstrip("/") + "/") if results else b""
    for line in data.splitlines(keepends=True):
        if results_at and b"/tool-results/" in line and results_at.search(line) and _entry(line).get("type") == "user":
            line = results_at.sub(lambda _: results_escaped, line)
        if b'"prompt_snapshot"' in line:
            entry = _entry(line)
            attachment = entry.get("attachment")
            if entry.get("type") == "attachment" and isinstance(attachment, dict):
                if attachment.get("type") == "prompt_snapshot":
                    found = sorted({found for text in _strings(attachment) for found in MEMORY_AT.findall(text)})
                    for path in found:
                        if _escaped(path) not in line:
                            return b"", "its recorded prompt names its memory folder in a form just code cannot rewrite"
                        line = line.replace(_escaped(path), memory_escaped)
                    if here and _left_behind(here, [folder] if folder else [], found).search(line):
                        return b"", "its recorded prompt names a memory folder in a form just code does not know"
        if b'"worktree-state"' in line:
            entry = _entry(line)
            if entry.get("type") == "worktree-state":
                bound = bool(entry.get("worktreeSession"))
        out.append(line)
    if bound:
        out.append(_line({"type": "worktree-state", "worktreeSession": None, "sessionId": session}))
    out.append(_line({"type": "relocated", "relocatedCwd": checkout, "sessionId": session}))
    return b"".join(out), ""


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _directory(path, created):
    """Make ``path`` and any parent missing, 0700 each; one that exists keeps its mode."""
    if os.path.isdir(path):
        return
    _directory(os.path.dirname(path), created)
    os.mkdir(path, 0o700)
    os.chmod(path, 0o700)
    created.append(path)


def _write(path, data, mtime):
    """Write ``data`` to ``path`` through a file beside it, 0600, with ``mtime``."""
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".claude-on-proxmox-")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.chmod(temporary, 0o600)
        os.utime(temporary, (mtime, mtime))
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def read_manifest(path):
    try:
        with open(path, encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return {}
    copies = found.get("sessions") if isinstance(found, dict) and found.get("format") == MANIFEST_FORMAT else None
    return {str(key): str(value) for key, value in copies.items()} if isinstance(copies, dict) else {}


def copy_time(source, current, now):
    """The file time a copy is given: ``source``'s while recent, else ``current``'s while that is, else ``now``."""
    if now - source < FRESH:
        return source
    if current is not None and now - current < FRESH and current >= source:
        return current
    return now


def _save_manifest(manifest, copies, created):
    _directory(os.path.dirname(manifest), created)
    text = json.dumps({"format": MANIFEST_FORMAT, "sessions": copies}, indent=2, sort_keys=True) + "\n"
    _write(manifest, text.encode("utf-8"), time.time())


def _remove_copy(dest, folder, session):
    """Remove this module's copy of ``session`` in ``folder``: its transcript, its tool results, what is left empty."""
    base = os.path.join(dest, "projects", folder)
    os.unlink(os.path.join(base, f"{session}.jsonl"))
    tools = os.path.join(base, session, "tool-results")
    if os.path.isdir(tools) and not os.path.islink(tools):
        shutil.rmtree(tools)
    for path in (os.path.join(base, session), base):
        try:
            os.rmdir(path)
        except OSError:
            break


def classify(current, recorded, digest, moved=False):
    """What to do with a copy here: ``current`` its digest (None: there is none), ``recorded`` the manifest's.

    "write" where there is none or it is still what was last written, "unchanged" where it is what
    would be written, and otherwise "continued" - changed here, not on the other machine since - or
    "diverged". ``moved``: there is none because the session moved to another folder here, which
    counts as changed here.
    """
    if moved:
        return "continued" if recorded == digest else "diverged"
    if current is None or (current == recorded and current != digest):
        return "write"
    if current == digest:
        return "unchanged"
    return "continued" if recorded == digest else "diverged"


def _folders_holding(dest, sessions):
    """{id: [folder, ...]}: the folders of ``dest``/projects that hold a transcript of each session ID asked about."""
    wanted, found = {session["id"] for session in sessions}, {}
    try:
        folders = [entry for entry in os.scandir(os.path.join(dest, "projects")) if entry.is_dir(follow_symlinks=False)]
    except OSError:
        return found
    for folder in folders:
        try:
            names = {entry.name for entry in os.scandir(folder.path)}
        except OSError:
            continue
        for sid in wanted:
            if f"{sid}.jsonl" in names:
                found.setdefault(sid, []).append(folder.name)
    return found


def _tool_results(source, target, check, created):
    """Copy what ``source`` holds and ``target`` lacks or holds otherwise; return how many files that was."""
    copied = 0
    for top, dirs, files in os.walk(source):
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(top, name))]
        for name in files:
            here = os.path.join(top, name)
            if os.path.islink(here) or not os.path.isfile(here):
                continue
            there = os.path.join(target, os.path.relpath(here, source))
            stat = os.stat(here)
            if os.path.isfile(there) and not os.path.islink(there):
                other = os.stat(there)
                if other.st_size == stat.st_size and int(other.st_mtime) == int(stat.st_mtime):
                    continue
            copied += 1
            if not check:
                _directory(os.path.dirname(there), created)
                with open(here, "rb") as handle:
                    _write(there, handle.read(), stat.st_mtime)
    return copied


def _prune(mirror, keep, check):
    """Remove from ``mirror`` every session that is not in ``keep``; return whether anything was."""
    removed = False
    try:
        folders = [entry for entry in os.scandir(mirror) if entry.is_dir(follow_symlinks=False)]
    except OSError:
        return removed
    for folder in folders:
        for entry in list(os.scandir(folder.path)):
            session = entry.name[: -len(".jsonl")] if entry.name.endswith(".jsonl") else entry.name
            if (folder.name, session) in keep:
                continue
            removed = True
            if check:
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)
        if not check and not os.listdir(folder.path):
            os.rmdir(folder.path)
    return removed


def copy_sessions(mirror, dest, manifest, sessions, here="", prune=True, check=False, now=None):
    """Write every session's copy; return the result and whether anything changed."""
    now = time.time() if now is None else now
    recorded = read_manifest(manifest)
    copies, saved = dict(recorded), dict(recorded)
    result = {
        "written": [],
        "held": {},
        "unchanged": [],
        "continued": [],
        "diverged": [],
        "unreadable": [],
    }
    created, changed = [], False
    holding = _folders_holding(dest, sessions)
    try:
        for session in sessions:
            sid, folder = session["id"], session["folder"]
            key = f"{folder}/{sid}.jsonl"
            source = os.path.join(mirror, key)
            try:
                with open(source, "rb") as handle:
                    data = complete_lines(handle.read())
                mtime = os.stat(source).st_mtime
            except OSError:
                result["unreadable"].append({"id": sid, "why": "rsync did not bring it"})
                continue
            results = os.path.join(dest, "projects", folder, sid, "tool-results")
            edited, why = (
                edit_transcript(data, sid, session["checkout"], session["memory"], here, results, folder)
                if data
                else (b"", "")
            )
            if not edited:
                result["unreadable"].append({"id": sid, "why": why or "it holds no complete line"})
                continue
            digest, target = _digest(edited), os.path.join(dest, "projects", key)
            current = _file_digest(target) if os.path.isfile(target) and not os.path.islink(target) else None
            if os.path.lexists(target) and current is None:
                result["diverged"].append(sid)
                continue
            others = [other for other in holding.get(sid, []) if other != folder] if current is None else []
            # The one other folder holding it, with the very copy this module wrote there: the session moved on
            # the other machine (/cd there), not here.
            old = f"{others[0]}/{sid}.jsonl" if len(others) == 1 else ""
            old_path = os.path.join(dest, "projects", old)
            mac_move = (
                bool(old)
                and recorded.get(old) is not None
                and os.path.isfile(old_path)
                and not os.path.islink(old_path)
                and _file_digest(old_path) == recorded[old]
            )
            if others and not mac_move:
                result["held"][sid] = others[0]
            action = classify(current, recorded.get(key), digest, bool(others) and not mac_move)
            if action in ("continued", "diverged"):
                result[action].append(sid)
                continue
            if action == "write":
                result["written"].append(sid)
                changed = True
                if not check:
                    _directory(os.path.dirname(target), created)
                    _write(target, edited, copy_time(mtime, None, now))
                # Recorded once written, never before: a record of a copy that is not there would make the
                # one that is read as continued here.
                copies[key] = digest
                if mac_move:
                    copies.pop(old, None)
                    if not check:
                        _remove_copy(dest, others[0], sid)
            else:
                result["unchanged"].append(sid)
                copies[key] = digest
                there = os.stat(target).st_mtime
                wanted = copy_time(mtime, there, now)
                if int(there) != int(wanted):
                    changed = True
                    if not check:
                        os.utime(target, (wanted, wanted))
            tools = os.path.join(mirror, folder, sid, "tool-results")
            if os.path.isdir(tools):
                changed = _tool_results(tools, results, check, created) > 0 or changed
            # After each copy, so that a run stopped part way still records what it wrote: a copy written
            # and not recorded would read as continued here once the session changed on the other machine.
            if copies != saved and not check:
                _save_manifest(manifest, copies, created)
                saved = dict(copies)
    finally:
        if copies != saved and not check:
            _save_manifest(manifest, copies, created)
    if copies != recorded:
        changed = True
    if prune:
        changed = _prune(mirror, {(item["folder"], item["id"]) for item in sessions}, check) or changed
    return result, changed or bool(created)


def main():
    module = AnsibleModule(
        argument_spec={
            "mirror": {"type": "path", "required": True},
            "dest": {"type": "path", "required": True},
            "manifest": {"type": "path", "required": True},
            "here": {"type": "str", "default": ""},
            "sessions": {
                "type": "list",
                "elements": "dict",
                "required": True,
                "options": {
                    "id": {"type": "str", "required": True},
                    "folder": {"type": "str", "required": True},
                    "checkout": {"type": "str", "required": True},
                    "memory": {"type": "str", "required": True},
                },
            },
            "prune": {"type": "bool", "default": True},
        },
        supports_check_mode=True,
    )
    params = module.params
    for session in params["sessions"]:
        if "/" in session["folder"] or session["folder"] in ("", ".", "..") or not SESSION_ID.fullmatch(session["id"]):
            module.fail_json(msg=f"{session['folder']}/{session['id']} is not a session's folder and ID.")
    result, changed = copy_sessions(
        params["mirror"],
        params["dest"],
        params["manifest"],
        params["sessions"],
        here=params["here"],
        prune=params["prune"],
        check=module.check_mode,
    )
    module.exit_json(changed=changed, **result)


if __name__ == "__main__":
    main()
