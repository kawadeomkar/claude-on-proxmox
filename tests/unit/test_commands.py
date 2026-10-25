"""Every just command the project names is one the justfile accepts.

The playbooks' messages, the roles' NEXT STEPS lines, the session notes, the wiki, the README,
CLAUDE.md, CI and the justfile's own messages tell a person what to type. Each `just <recipe> ...` in
them is checked against `just --json`:

- every setting before the recipe is one of the justfile's;
- every option is one of its recipe's own, and its value one the option takes;
- no word after the recipe is a `NAME=value` (a setting goes before the recipe, or just reads it as a VM name);
- no word is a misspelt part (a word that is no part and close to none is the prose after the command);
- no word is a second recipe, which just would run after the first, or refuse as a part.

A command split by Jinja (`'just deploy ' ~ name ~ ' --tags x'`), an f-string or a shell variable is read
with a placeholder for what it splices in.

And no `make <recipe>` is left anywhere but in the pages that tell the history - nor a make run, nor a name
after a make command (`make-code`) - no variable the Makefile read (`TAGS=`) but on a line that says it
was make's, no `just <target>` of a target only the Makefile had, and no misspelt recipe.
"""

from __future__ import annotations

import difflib
import functools
import json
import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JUST = shutil.which("just")
# Where a command is told to a person.
SOURCES = [
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CLAUDE.md",
    "deploy.yml",
    "*.cfg",
    "Vagrantfile",
    "docs/**/*.md",
    "playbooks/**/*.yml",
    "playbooks/**/*.j2",
    "roles/*/tasks/*.yml",
    "roles/*/vars/*.yml",
    "roles/*/defaults/*.yml",
    "roles/*/templates/*",
    "inventory/group_vars/all/*",
    "filter_plugins/*.py",
    "callback_plugins/*.py",
    "tools/*",
    "tests/unit/*.py",
    "justfile",
    ".github/**/*.yml",
    ".pre-commit-config.yaml",
]
# What tells the history: the make commands of earlier versions.
HISTORY = {"docs/guides/upgrading.md", "docs/architecture/decisions.md"}
# What is about refusals, and so names commands that are refused on purpose.
REFUSED_ON_PURPOSE = {"tests/unit/test_justfile.py", "tests/unit/test_commands.py", "docs/architecture/decisions.md"}
PLACEHOLDER = "X"


def _files() -> list[Path]:
    # Tracked, or new and not ignored: a file is checked before its first commit too.
    tracked = set(
        subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    )
    found = set()
    for pattern in SOURCES:
        for path in REPO.glob(pattern):
            name = str(path.relative_to(REPO))
            if path.is_file() and name in tracked and not name.startswith("docs/plan"):
                found.add(path)
    return sorted(found)


@functools.cache
def _json() -> dict:
    return json.loads(subprocess.run([JUST, "--json"], cwd=REPO, capture_output=True, text=True, check=True).stdout)


def _recipes() -> dict[str, dict]:
    return {name: recipe for name, recipe in _json()["recipes"].items() if not recipe["private"]}


def _settings() -> set[str]:
    """The assignments a person sets: the upper-case ones. The rest are the justfile's own workings."""
    return {name for name in _json()["assignments"] if name.isupper()}


def _normalized(text: str) -> str:
    """Text with what Jinja or an f-string splices into a command read as one placeholder word."""
    text = re.sub(r"\{\{.*?\}\}", PLACEHOLDER, text)
    text = re.sub(r"(['\"])\s*~\s*[^~'\"\n]+?\s*~\s*\1", PLACEHOLDER, text)
    text = re.sub(r"(['\"])\s*~\s*[A-Za-z_][\w.\[\]]*", PLACEHOLDER + r"\1", text)
    text = re.sub(r"\{[a-z_][\w.\[\]'\"]*\}", PLACEHOLDER, text)
    # A message split over string literals: "... just" " deploy x".
    return re.sub(r"(['\"])\n\s*f?\1", "", text)


# just, the settings before the recipe, the recipe, and its words, which run to the end of their code span,
# string or line: a second just starts a command of its own.
SETTING = r"[A-Z][A-Z_]*=(?:'[^'\n]*'|\"[^\"\n]*\"|[^\s`'\"]+)"
COMMAND = re.compile(
    rf"\bjust((?:[ \t]+{SETTING})*)[ \t]+([a-z][a-z-]*)\b(?![-\w])((?:[ \t]+(?!just\b)[^\s`'\"|)(;\\]+)*)"
)


def _matches(text: str, name: str = ""):
    """Each just command in text: the file, its line, the settings before it, the recipe, and its words."""
    text = _normalized(text)
    for match in COMMAND.finditer(text):
        line = text[: match.start()].count("\n") + 1
        settings = re.findall(rf"(?<!\S){SETTING}", match.group(1))
        yield name, line, [s.split("=", 1)[0] for s in settings], match.group(2), match.group(3).split()


def _commands_in(text: str, name: str = ""):
    recipes = _recipes()
    for match in _matches(text, name):
        if match[3] in recipes:
            yield match


def _sources():
    for path in _files():
        name = str(path.relative_to(REPO))
        if name in REFUSED_ON_PURPOSE:
            continue
        try:
            yield name, path.read_text()
        except UnicodeDecodeError:
            continue


def _commands():
    for name, text in _sources():
        yield from _commands_in(text, name)


def _placeholder(word: str) -> bool:
    return PLACEHOLDER in word or "$" in word or word.startswith(("<", "[")) or word == "..."


def _refused(parameter: dict, value: str) -> str | None:
    """Why a value is not one the parameter takes: outside its pattern, and close to a word the pattern
    names (a misspelling), or any value outside it when the pattern names no words."""
    pattern = parameter["pattern"][0] if parameter["pattern"] else None
    if pattern is None or _placeholder(value) or re.fullmatch(pattern, value):
        return None
    words = re.findall(r"[a-z][a-z-]+", pattern)
    if not re.fullmatch(r"\(?[a-z|-]+\)?\??", pattern):
        return f"{value} is not what {parameter['name']} takes ({pattern})"
    if difflib.get_close_matches(value, words, n=1, cutoff=0.75):
        return f"{value} is none of {', '.join(words)}"
    return None


def _one_word(parameter: dict) -> bool:
    """A * parameter named in the singular takes one word, and its recipe refuses a second (justfile, header)."""
    return parameter["kind"] == "star" and not parameter["name"].endswith("S")


def _problem(recipe: dict, recipes: dict, words: list[str]) -> str | None:
    longs = {p["long"]: p for p in recipe["parameters"] if p["long"]}
    shorts = {p["short"]: p for p in recipe["parameters"] if p["short"]}
    positional = [p for p in recipe["parameters"] if not p["long"] and not p["short"] and p["name"] != "EXTRA"]
    # init's EXTRA takes every other word, to refuse it: none is taken here.
    takes_extra = any(p["name"] == "EXTRA" for p in recipe["parameters"])
    took_its_one_word = False
    i = 0
    while i < len(words):
        raw = words[i]
        word = raw.rstrip(".,:;")
        option = None
        if word.startswith("#"):
            return None  # a comment
        if word.startswith("--"):
            name, given, value = word[2:].partition("=")
            if name not in longs:
                return f"no option --{name}"
            option = longs[name]
            if option["value"] is None and not given and i + 1 < len(words):
                i += 1
                raw = words[i]
                value = raw.rstrip(".,:;")
        elif re.fullmatch(r"-[a-z]", word):
            if word[1] not in shorts:
                return f"no option {word}"
            option = shorts[word[1]]
            if i + 1 < len(words):
                i += 1
                raw = words[i]
                value = raw.rstrip(".,:;")
        elif re.fullmatch(r"[A-Z][A-Z_]*=\S*", word):
            return f"{word} after the recipe: a setting goes before it"
        elif positional and positional[0]["name"] in ("NAMES", "NAME"):
            # Names are any words, prose's too: deploy refuses a stage's as it runs.
            pass
        elif positional and positional[0]["pattern"]:
            # Parts: a word that is none of them, nor close to one, ends the command, and is prose.
            refused = _refused(positional[0], word)
            if refused:
                return refused
            if re.fullmatch(positional[0]["pattern"][0], word) or _placeholder(word):
                if positional[0]["kind"] == "singular" or _one_word(positional[0]):
                    took_its_one_word = _one_word(positional[0])
                    positional = positional[1:]
            elif word in recipes:
                return f"{word} is none of {recipe['name']}'s parts, and just refuses it: run one recipe at a time"
            else:
                return None
        elif word in recipes and took_its_one_word:
            return f"{word} is a second word, which {recipe['name']} refuses: run one recipe at a time"
        elif word in recipes and not takes_extra:
            # A recipe that takes no more words takes the next for a recipe to run after it.
            return f"{word} is a second recipe, which just runs after {recipe['name']}: run one recipe at a time"
        elif not _placeholder(word):
            return None
        if option is not None and option["value"] is None:
            refused = _refused(option, value)
            if refused:
                return f"--{option['long'] or option['short']} {refused}"
        # A word that ends a sentence or a clause ends the command: what follows is prose.
        if raw != raw.rstrip(".,:;"):
            return None
        i += 1
    return None


def test_every_just_command_named_is_one_the_justfile_accepts() -> None:
    recipes = _recipes()
    problems = []
    seen = 0
    for name, line, settings, recipe, words in _commands():
        seen += 1
        problem = _problem(recipes[recipe], recipes, words)
        unknown = [setting for setting in settings if setting not in _settings()]
        if unknown:
            problem = f"no setting {', '.join(unknown)}: the justfile's are {', '.join(sorted(_settings()))}"
        if problem:
            problems.append(f"{name}:{line}: just {recipe} {' '.join(words)} - {problem}")
    assert seen > 200, seen
    assert problems == [], "\n".join(problems)


def _misspelt(word: str) -> bool:
    """A word after just that is close to a recipe's name and is not one, nor an inflection of one (a VM just
    deployed)."""
    close = difflib.get_close_matches(word, _recipes(), n=1, cutoff=0.8)
    return bool(close) and word not in _recipes() and not re.fullmatch(rf"{close[0]}(s|es|d|ed|ing)", word)


def test_no_recipe_named_is_misspelt() -> None:
    misspelt = [
        f"{name}:{line}: just {recipe}"
        for source, text in _sources()
        for name, line, _, recipe, _ in _matches(text, source)
        if _misspelt(recipe)
    ]
    assert misspelt == [], "\n".join(misspelt)
    assert _misspelt("deplyo") and _misspelt("destory") and _misspelt("instal")
    assert not _misspelt("deployed") and not _misspelt("lists") and not _misspelt("deploy")


def test_the_check_catches_what_it_is_for() -> None:
    recipes = _recipes()
    assert _problem(recipes["deploy"], recipes, ["alpha", "--type", "configure"]) == "no option --type"
    assert "a setting goes before it" in _problem(recipes["deploy"], recipes, ["alpha", "TAGS=claude_code"])
    assert _problem(recipes["vagrant"], recipes, ["destroy"]) is None
    assert _problem(recipes["deploy"], recipes, ["alpha", "--tags", "claude_code", "--check"]) is None
    assert _problem(recipes["test"], recipes, ["unit", "before", "pushing"]) is None
    assert (
        _problem(recipes["test"], recipes, ["integartion"])
        == "integartion is none of unit, molecule, role, integration, provision"
    )
    assert _problem(recipes["lint"], recipes, ["synatx"]) is not None
    assert _problem(recipes["docs"], recipes, ["serv"]) is not None
    assert _problem(recipes["deploy"], recipes, ["--only", "configre"]) is not None
    assert _problem(recipes["login"], recipes, ["alpha", "--only", "gihub"]) is not None
    assert _problem(recipes["deploy"], recipes, ["alpha", "--tags", "Claude-Code"]) is not None
    assert _problem(recipes["deploy"], recipes, ["--tags", "<role>"]) is None
    assert _problem(recipes["test"], recipes, ["#", "every", "test"]) is None
    assert [words for *_, words in _commands_in("just deploy and just login --only github")] == [
        ["and"],
        ["--only", "github"],
    ]
    # Chained: a second recipe after one that takes no more words, or among parts.
    assert "second recipe" in _problem(recipes["logs"], recipes, ["clean"])
    assert "second word" in _problem(recipes["docs"], recipes, ["serve", "lint"])
    assert "second word" in _problem(recipes["help"], recipes, ["deploy", "test"])
    assert "just refuses it" in _problem(recipes["lint"], recipes, ["docs"])
    assert _problem(recipes["logs"], recipes, ["lists", "them"]) is None
    assert _problem(recipes["help"], recipes, ["deploy"]) is None
    assert _problem(recipes["init"], recipes, ["--host", "pve.lan", "--no-prompt"]) is None
    assert _problem(recipes["deploy"], recipes, ["--tags", "$roles"]) is None
    # Settings before the recipe, which the test holds to the justfile's.
    [(_, _, settings, recipe, words)] = _matches("just ANSIBLE_ARGS='-f 30' LOG_DIR=x deploy alpha")
    assert (settings, recipe, words) == (["ANSIBLE_ARGS", "LOG_DIR"], "deploy", ["alpha"])
    assert {"ANSIBLE_ARGS", "LOG_DIR", "PYTHON", "BIN"} <= _settings() and "TYPE" not in _settings()


RECIPES = "init|install|vault|deploy|code|login|list|destroy|logs|lint|test|docs|vagrant|clean|help"
MAKE = re.compile(
    rf"\bmake ({RECIPES})\b(?![-\w])"
    # A run of make, make's name in a sentence, and a name after a make command: make-code, make_code_dir.
    rf"|\bmake runs?\b|\bruns? `?make\b|(?<![A-Za-z])make[-_]({RECIPES})(?![a-z])"
)
# The variables the Makefile read, which are options now (--tags, --session) or a word (just test unit).
MAKE_VARIABLE = re.compile(
    r"(?<![\w$-])(TYPE|VM_NAME|TAGS|ROLE|PROJECT|SESSION|MOLECULE_ROLES|SCENARIO|HOST|TOKEN_USER|TOKEN_NAME"
    r"|TOKEN_SECRET|TOKEN_SECRET_FILE|GITHUB_USER|STORAGE|BRIDGE|TAILSCALE|PROMPT)="
)
# A justfile line that declares a recipe, whose parameters may share make's names.
SIGNATURE = re.compile(r"^[a-z][a-z-]* .*:$")


def test_no_make_command_is_left() -> None:
    left = []
    for path in _files():
        name = str(path.relative_to(REPO))
        if name in HISTORY or name in REFUSED_ON_PURPOSE:
            continue
        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        left += [f"{name}:{number}: {line.strip()}" for number, line in enumerate(lines, 1) if MAKE.search(line)]
    assert left == [], "\n".join(left)
    assert MAKE.search("the one you run `make` on") and MAKE.search("one log per make run")
    assert MAKE.search("bash -lc <script> make-code") and MAKE.search("def test_make_deploy_goes_on():")
    assert not MAKE.search("a make-era worktree") and not MAKE.search("roles_make_it_before_their_roles")


def test_no_make_variable_is_left() -> None:
    # `TAGS=dev_tools` outlived the Makefile on a role's page, and SESSION in --session's refusals: a line
    # that tells the history says whose spelling it was.
    left = []
    for path in _files():
        name = str(path.relative_to(REPO))
        if name in HISTORY or name in REFUSED_ON_PURPOSE:
            continue
        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        left += [
            f"{name}:{number}: {line.strip()}"
            for number, line in enumerate(lines, 1)
            if MAKE_VARIABLE.search(line)
            and not re.search(r"\bmake\b", line)
            and not (name == "justfile" and SIGNATURE.match(line))
        ]
    assert left == [], "\n".join(left)
    assert MAKE_VARIABLE.search("`TAGS=dev_tools` runs it alone") and MAKE_VARIABLE.search('VM_NAME="a b"')
    assert not MAKE_VARIABLE.search("just ANSIBLE_ARGS=-vvv deploy") and not MAKE_VARIABLE.search("--host=pve")
    assert not MAKE_VARIABLE.search("CLAUDE_ON_PROXMOX_TOKEN_SECRET=x")


# The targets only an earlier Makefile had, whose names no sentence would put after "just".
REMOVED = [
    "venv",
    "deps",
    "vault-encrypt",
    "vault-edit",
    "logs-clean",
    "ssh-config",
    "claude-login",
    "github-login",
    "tailscale-login",
    "docs-serve",
    "docs-build",
    "syntax",
    "pre-commit",
    "unit",
    "molecule",
    "molecule-scenario",
    "molecule-integration",
    "molecule-provision",
    "vagrant-up",
    "vagrant-destroy",
]
OLD_TARGET = re.compile(rf"\bjust ({'|'.join(map(re.escape, REMOVED))})\b(?![-\w])")


def test_no_target_only_the_makefile_had_is_named() -> None:
    assert not set(REMOVED) & set(_recipes())
    left = []
    for path in _files():
        name = str(path.relative_to(REPO))
        if name in HISTORY or name in REFUSED_ON_PURPOSE:
            continue
        try:
            lines = _normalized(path.read_text()).splitlines()
        except UnicodeDecodeError:
            continue
        left += [f"{name}:{number}: {line.strip()}" for number, line in enumerate(lines, 1) if OLD_TARGET.search(line)]
    assert left == [], "\n".join(left)
    assert OLD_TARGET.search("run just claude-login alpha") and not OLD_TARGET.search("just unit-ish")
