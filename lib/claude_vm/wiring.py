"""The settings' wiring, read from the checkout's own files: each role's argument_specs, which role variable each
setting reaches through defaults.yml and the roles' defaults, and the sections of the example files. tools/docs.py
renders the wiki's reference pages from it and tools/settings answers just settings with it, so the two cannot
differ. Read-only; yaml is imported inside the functions that parse a file."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GROUP_VARS = ROOT / "inventory" / "group_vars" / "all"
# "# ---------------------------------------------------------------- setup ----"
BANNER = re.compile(r"^# -{3,} (.+?) -{3,}$")
# A value that is one variable's and nothing else: "{{ vm_cores }}".
ALIAS = re.compile(r"^\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}$")


def roles() -> list[str]:
    return sorted(p.parent.parent.name for p in (ROOT / "roles").glob("*/meta/argument_specs.yml"))


def specs() -> dict[str, dict[str, dict]]:
    """Every role variable's argument_specs entry, by role: the main entry point's wins."""
    import yaml

    out: dict[str, dict[str, dict]] = {}
    for role in roles():
        entry_points = yaml.safe_load((ROOT / "roles" / role / "meta" / "argument_specs.yml").read_text())
        merged: dict[str, dict] = {}
        for _name, spec in sorted(entry_points["argument_specs"].items(), key=lambda kv: kv[0] != "main"):
            for var, details in (spec.get("options") or {}).items():
                merged.setdefault(var, details or {})
        out[role] = merged
    return out


def wiring() -> tuple[dict, dict[str, list[str]]]:
    """Every default a setting can have, and which variables each setting sets directly.

    The wiring is a variable whose whole value is one setting, as in defaults.yml
    (`claude_code_login_desktop: "{{ login_desktop }}"`) or a role's defaults
    (`proxmox_vm_cores: "{{ vm_cores | default(2) }}"`, which is where vm_cores' default lives), trimmed or not
    (`vault_vm_password`'s); or one of two by a rule, trimmed (`proxmox_api_token_secret: "{{ (vault_a if ... else
    vault_b) | trim }}"`, the first host's secret or a host's own), which both set.
    Role defaults load first and defaults.yml after, as Ansible ranks them.
    """
    import yaml

    either = re.compile(r"^\{\{\s*\((\w+)\s+if\b.*\belse\s+(\w+)\)\s*\|\s*trim\s*\}\}$", re.S)
    wire = re.compile(r"""^(\w+): "\{\{ (\w+)(?: \| trim)?(?: \| default\((.*)\))? \}\}"$""")
    defaults: dict = {}
    feeds: dict[str, list[str]] = {}
    files = sorted((ROOT / "roles").glob("*/defaults/main.yml")) + [GROUP_VARS / "defaults.yml"]
    for path in files:
        loaded = yaml.safe_load(path.read_text()) or {}
        defaults.update(loaded)
        for name, value in loaded.items():
            m = either.match(value.strip()) if isinstance(value, str) else None
            if m:
                for source in m.groups():
                    feeds.setdefault(source, []).append(name)
        for line in path.read_text().splitlines():
            m = wire.match(line)
            if m:
                feeds.setdefault(m.group(2), []).append(m.group(1))
                if m.group(3) is not None:
                    # default(value) or default(value, true): the first argument, as YAML reads it - or,
                    # a bare name, the variable it names, as Ansible reads it
                    # (`proxmox_vm_memory_min_mb: "{{ vm_memory_min_mb | default(proxmox_vm_memory_mb) }}"`).
                    first = m.group(3).split(",")[0].strip()
                    if re.fullmatch(r"[a-z_]\w*", first) and first not in ("true", "false", "none", "omit"):
                        defaults.setdefault(m.group(2), "{{ " + first + " }}")
                    else:
                        args = yaml.safe_load("[" + m.group(3).replace("'", '"') + "]")
                        defaults.setdefault(m.group(2), args[0])
    return defaults, feeds


def alias_of(value) -> str | None:
    """The variable a value is the value of and nothing else ("{{ name }}"), or None."""
    match = ALIAS.match(value) if isinstance(value, str) else None
    return match.group(1) if match else None


def named_anew(defaults: dict, role_specs: dict[str, dict[str, dict]]) -> dict[str, str]:
    """Each name a setting had in 1.0 that 1.1 gave a new one, with the new name: a default that is the old name's
    value and nothing else, neither of them a role's variable (editor_claude_terminal: "{{ vscode_claude_terminal }}").
    The new name is the one read, and wins whenever both are set. tools/check derives the same pairs from
    defaults.yml alone, without this package, since it runs before every recipe."""
    found = {}
    for name, default in defaults.items():
        old = alias_of(default)
        if old in defaults and not role_of(name, role_specs) and not role_of(old, role_specs):
            found[old] = name
    return found


def role_of(variable: str, role_specs: dict[str, dict[str, dict]]) -> str | None:
    """The role whose argument_specs list the variable, or None for a variable no role takes."""
    return next((role for role, options in role_specs.items() if variable in options), None)


def set_by(name: str, feeds: dict[str, list[str]], role_specs: dict) -> list[str]:
    """The role variables a setting ends up in, following settings that set other settings."""
    out, todo, seen = [], list(feeds.get(name, [])), set()
    while todo:
        var = todo.pop(0)
        if var in seen:
            continue
        seen.add(var)
        if role_of(var, role_specs):
            out.append(var)
        todo += feeds.get(var, [])
    return out


def identifier(vault_name: str, feeds: dict[str, list[str]], role_specs: dict) -> bool:
    """Whether what the vault holds under this name identifies rather than protects: it reaches role variables, none of
    them no_log - the GitHub username, an OAuth client's ID. just settings masks one in its own row and never looks for
    it elsewhere, and just status does not refuse to keep settings that hold one."""
    fed = set_by(vault_name, feeds, role_specs)
    return bool(fed) and not any(options.get(var, {}).get("no_log") for var in fed for options in role_specs.values())


def parse_example(path: Path, secret: bool) -> list[tuple[str, list[tuple[list[str], list[tuple[str, object]]]]]]:
    """Sections of groups: the prose comment above some settings, and those settings with the value
    the example gives each. Settings in local.yml.example are commented out (`# name: value`); in
    vault.yml.example only uncommented lines are settings, so a comment that happens to start
    `# word: ...` stays prose."""
    import yaml

    lines = path.read_text().splitlines()
    rel = path.relative_to(ROOT)
    # Everything above the first banner is the file's own header: how to use it, not a setting.
    start = next((i for i, line in enumerate(lines) if BANNER.match(line)), None)
    if start is None:
        raise SystemExit(f"{rel}: no `# --- Section ---` banner, so nothing marks where the settings start")
    setting = re.compile(r"^([a-z][a-z0-9_]*):(?: .*)?$" if secret else r"^# ([a-z][a-z0-9_]*):(?: .*)?$")
    sections: list = []
    prose: list[str] = []
    found: list[tuple[str, list[str]]] = []  # each setting with its lines of YAML

    def flush():
        nonlocal prose, found
        if found:
            parsed = [(name, yaml.safe_load("\n".join(text))[name]) for name, text in found]
            sections[-1][1].append((prose, parsed))
        prose, found = [], []

    for line in lines[start:]:
        banner = BANNER.match(line)
        uncommented = line[2:] if line.startswith("# ") else line
        if banner:
            flush()
            sections.append((banner.group(1).strip(), []))
        elif not line.strip():
            flush()
        elif setting.match(line):
            found.append((setting.match(line).group(1), [uncommented]))
        elif not secret and re.match(r"^#\s{3,}\S", line) and found:
            found[-1][1].append(uncommented)  # an indented continuation of the setting above
        elif line.startswith("#"):
            if found:
                flush()
            prose.append(line[1:].strip())
    flush()

    names = [name for _, groups in sections for _, found_ in groups for name, _ in found_]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise SystemExit(f"{rel} lists {', '.join(duplicates)} twice: a comment line read as a setting?")
    if secret:
        keys = sorted((yaml.safe_load(path.read_text()) or {}).keys())
        if sorted(names) != keys:
            raise SystemExit(f"{rel}: the settings read ({sorted(names)}) are not the file's keys ({keys})")
    return [s for s in sections if s[1]]
