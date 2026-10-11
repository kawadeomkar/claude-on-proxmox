"""Filters for the steps the sign-ins take on this machine's desktop."""

from __future__ import annotations

import os
import shlex

# The openers tried when BROWSER names none, in this order: macOS's, then the
# freedesktop one.
OPENERS = ("open", "xdg-open")


def page_opener(url, browser, found):
    """Return the argv that opens ``url`` in a browser on this machine, or ``[]`` when nothing can.

    ``browser`` is the ``BROWSER`` environment variable, which is a list of
    commands separated by ``:``, each of which may hold ``%s`` where the URL
    goes; a command without one gets the URL as its last argument. Each command
    is split the way a shell would split it, so a quoted path with a space stays
    one argument. Python's ``webbrowser`` module tries the commands in turn
    until one starts; an argv holds only one, so only the first command (the
    first entry that is not empty) is taken, and the others are never tried.

    ``found`` is what ``which`` printed for the openers asked about, as paths or
    names: without a usable ``BROWSER``, ``open`` is used if it was found, then
    ``xdg-open``.
    """
    url = str(url)
    for entry in str(browser or "").split(":"):
        if not entry.strip():
            continue
        try:
            argv = shlex.split(entry)
        except ValueError:
            # Unbalanced quotes: no command can be read out of it.
            argv = []
        if argv:
            if any("%s" in arg for arg in argv):
                return [arg.replace("%s", url) for arg in argv]
            return [*argv, url]
        break
    names = {os.path.basename(str(path).strip()) for path in found or []}
    for opener in OPENERS:
        if opener in names:
            return [opener, url]
    return []


class FilterModule:
    def filters(self):
        return {"page_opener": page_opener}
