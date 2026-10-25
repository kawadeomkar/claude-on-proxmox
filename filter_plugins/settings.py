"""Filters for just init: what an answer must look like, and writing one into a settings file's text.

Each question of playbooks/init_settings.yml has a kind, and an answer of that
kind is accepted in one normal form or refused. A value typed at a prompt and
one given on just init's command line go through the same filter, so both are held
to the same rule before they reach a file.
"""

from __future__ import annotations

import ipaddress
import json
import re

import yaml
from ansible.errors import AnsibleFilterError

# The documentation address hosts.yml.example ships with (RFC 5737), never a
# Proxmox host's.
EXAMPLE_HOST = "192.0.2.10"
_LABEL = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")
_TOKEN_ID = re.compile(r"[A-Za-z0-9._-]+@[A-Za-z][A-Za-z0-9._-]*![A-Za-z][A-Za-z0-9._-]*")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_GITHUB_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")

RULES = {
    "host": (
        "an IP address or a host name (letters, digits and hyphens, in labels separated by dots), without"
        f" https:// or a port, and not the example address {EXAMPLE_HOST}"
    ),
    "token_id": "a token ID, user@realm!name, as pveum printed it: ansible@pve!ansible, say",
    "uuid": "the 36-character secret pveum printed, like 01234567-89ab-cdef-0123-456789abcdef",
    "github_login": (
        "a GitHub username: at most 39 letters, digits and single hyphens, not starting or ending with a hyphen"
    ),
    "yes_no": "y or n",
    "choice": "one of the numbers listed",
}


def _host(text):
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        address = None
    if address is not None:
        # An IPv6 address with a zone (fe80::1%en0) parses, but a zone is this
        # machine's and means nothing in a URL the API is reached at.
        if "%" in text:
            return ""
        return "" if str(address) == EXAMPLE_HOST else str(address)
    labels = text.split(".")
    if len(text) > 253 or not all(_LABEL.fullmatch(label) for label in labels):
        return ""
    # A last label of digits alone makes a malformed IP address, not a name.
    return "" if labels[-1].isdigit() else text


def init_answer(value, kind, choices=None):
    """Return ``value`` in the normal form of ``kind``, or ``""`` when it is not one.

    host: an address or a DNS name, as typed (an IP address in its compressed
    form). token_id and github_login: as typed. uuid: lowercased. yes_no:
    ``y`` or ``n``. choice: the item of ``choices`` the number names. Leading
    and trailing whitespace is never part of an answer. An empty value is
    ``""``, the answer that decides later.
    """
    if kind not in RULES:
        raise AnsibleFilterError(f"init_answer: no kind {kind!r}; one of {', '.join(RULES)}")
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if kind == "host":
        return _host(text)
    if kind == "token_id":
        return text if _TOKEN_ID.fullmatch(text) else ""
    if kind == "uuid":
        text = text.lower()
        return text if _UUID.fullmatch(text) else ""
    if kind == "github_login":
        return text if _GITHUB_LOGIN.fullmatch(text) else ""
    if kind == "yes_no":
        return {"y": "y", "yes": "y", "n": "n", "no": "n"}.get(text.lower(), "")
    items = list(choices or [])
    if text.isdigit() and 1 <= int(text) <= len(items):
        return items[int(text) - 1]
    return ""


def init_answer_rule(kind):
    """The rule an answer of ``kind`` must follow, in words, for the prompt that asks again."""
    if kind not in RULES:
        raise AnsibleFilterError(f"init_answer_rule: no kind {kind!r}; one of {', '.join(RULES)}")
    return RULES[kind]


def with_setting(text, key, value, placeholder=""):
    """Return YAML ``text`` with top-level ``key`` set to ``value``, written as JSON.

    Only a line that leaves the key empty or holds ``placeholder`` - the value
    the example file ships with - is replaced, in place, trailing comment and
    all; a text without the key gets the line at its end. A key set to
    anything else raises: just init writes only what is not set yet, and the
    caller has already said so.
    """
    pattern = re.compile(rf"^{re.escape(key)}:(?P<rest>.*)$", re.MULTILINE)
    line = f"{key}: {json.dumps(value)}"
    written = None
    for match in pattern.finditer(text):
        try:
            current = yaml.safe_load(match.group("rest") or "")
        except yaml.YAMLError:
            current = object()
        if current not in (None, "", placeholder):
            raise AnsibleFilterError(f"with_setting: {key} is already set; just init writes only a value not set yet")
        written = text[: match.start()] + line + text[match.end() :]
        break
    if written is None:
        written = text + ("" if not text or text.endswith("\n") else "\n") + line + "\n"
    # A value the line did not hold whole - a block scalar's first line, say -
    # leaves text that no longer parses, or reads back otherwise: never written.
    try:
        parsed = yaml.safe_load(written)
    except yaml.YAMLError:
        parsed = None
    if not isinstance(parsed, dict) or parsed.get(key) != value:
        raise AnsibleFilterError(f"with_setting: {key} cannot be written into this file in place: use just vault")
    return written


def _size(value):
    """Bytes as the Proxmox UI writes them: 412 GiB, 1.8 TiB."""
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024 or unit == "PiB":
            return f"{size:.0f} {unit}" if size >= 10 or unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def init_candidates(results, key, prefix, required):
    """What the token can use among a node's storages or bridges, from the permission reads made for each.

    ``results`` are the results of a looped ``uri`` of /access/permissions,
    one per listing entry, each with the entry as its ``item``. ``key`` names
    the entry's name (``storage``, ``iface``), ``prefix`` its permission path
    (``/storage/``), and ``required`` the privileges it takes. Each candidate
    is ``{"name", "type", "free", "lacks", "inactive"}``, in the listing's
    order: ``lacks`` lists the required privileges the token does not hold
    there, and ``inactive`` is a storage Proxmox lists but cannot reach (an NFS
    share that is down). A candidate is usable with neither. A permission read
    that failed lacks everything.
    """
    candidates = []
    for result in results or []:
        entry = result.get("item") or {}
        name = str(entry.get(key, ""))
        data = (result.get("json") or {}).get("data") or {}
        held = data.get(prefix + name) or {} if result.get("status") == 200 else {}
        candidates.append(
            {
                "name": name,
                "type": str(entry.get("type", "")),
                "free": _size(entry["avail"]) + " free" if "avail" in entry else "",
                "lacks": [privilege for privilege in required if privilege not in held],
                "inactive": str(entry.get("active", 1)) == "0",
            }
        )
    return candidates


def init_choose_rows(candidates, path):
    """The lines a choice shows: the usable candidates numbered, then the others with what the token lacks."""
    usable = [c for c in candidates if not c["lacks"] and not c.get("inactive")]
    unusable = [c for c in candidates if c["lacks"] or c.get("inactive")]
    name = max((len(c["name"]) for c in candidates), default=0)
    kind = max((len(c["type"]) for c in candidates), default=0)
    lines = []
    for number, c in enumerate(usable, 1):
        prefix = f"{number}) " if len(usable) > 1 else "-  "
        lines.append(f"  {prefix}{c['name']:<{name}}   {c['type']:<{kind}}   {c['free']}".rstrip())
    for c in unusable:
        why = f"the token lacks {', '.join(c['lacks'])} on {path}{c['name']}" if c["lacks"] else "inactive"
        lines.append(f"  -  {c['name']:<{name}}   {c['type']:<{kind}}   {why}")
    return lines


class FilterModule:
    def filters(self):
        return {
            "init_answer": init_answer,
            "init_answer_rule": init_answer_rule,
            "with_setting": with_setting,
            "init_candidates": init_candidates,
            "init_choose_rows": init_choose_rows,
        }
