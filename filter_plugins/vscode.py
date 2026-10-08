"""Filters for reading VS Code's own files on this machine."""

from __future__ import annotations

import json


def jsonc_loads(text):
    """Parse JSON with comments and trailing commas, as VS Code writes its settings.

    One pass over the text: a string is copied whole, escapes and all, so a
    ``//`` in a URL is not a comment and a ``"`` in a comment does not start a
    string; ``//`` and ``/* */`` comments are dropped; a comma whose next
    character that is not blank or a comment closes an object or an array is
    dropped as well.
    """
    text = text.lstrip("﻿")
    out = []
    comma = None
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char == '"':
            end = i + 1
            while end < n and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(text[i : end + 1])
            comma = None
            i = end + 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if char in "}]" and comma is not None:
            out[comma] = ""
        if not char.isspace():
            comma = len(out) if char == "," else None
        out.append(char)
        i += 1
    return json.loads("".join(out))


def vscode_setting(text, key, default=None):
    """Return the value VS Code's settings.json ``text`` gives ``key``, or ``default``.

    Read-only and forgiving on purpose: ``default`` also comes back for empty
    text and for a file that does not parse, because a run that only wants to
    say what VS Code will do must not fail on the user's own editor settings.
    A key may be written flat (``"task.allowAutomaticTasks"``), as VS Code
    writes it, or nested (``"task": {"allowAutomaticTasks": ...}``), which VS
    Code also reads; the flat spelling wins when both are there.
    """
    if not text:
        return default
    try:
        settings = jsonc_loads(text)
    except ValueError:
        return default
    if not isinstance(settings, dict):
        return default
    if key in settings:
        return settings[key]
    node = settings
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


class FilterModule:
    def filters(self):
        return {
            "vscode_setting": vscode_setting,
        }
