"""The justfile's recipes as tools: each tool's input schema made from its recipe's parameters in `just --dump`, and
the argv that runs the recipe from a call's arguments.

The schema is advice to the model; the check that counts is just's own. Every value goes to just as one argv word,
an option in its --long=value form so that a value starting with - is still a value, and just holds it to the [arg]
pattern before any line of the script runs. tests/unit/test_mcp_tools.py holds every recipe to a tool or to a reason
here, and every option to a property or to a reason.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

from claude_vm.mcp.config import Config


@dataclass(frozen=True)
class ToolRecipe:
    # The recipe the tool runs.
    recipe: str
    # Options the server always gives, by their long name: True for a flag.
    fixed: dict[str, str | bool] = field(default_factory=dict)
    # Properties of the server's own beside the recipe's (OWN).
    own: tuple[str, ...] = ()
    # Properties a call must give: an array of them at least one item long.
    required: tuple[str, ...] = ()


# The tools that run a recipe, by name.
TOOLS = {
    "status": ToolRecipe("status", {"json": True}),
    "settings": ToolRecipe("settings", {"json": True}),
    "logs": ToolRecipe("logs"),
    "deploy": ToolRecipe("deploy", own=("wait_seconds",)),
    "login": ToolRecipe("login", own=("wait_seconds",)),
    "code": ToolRecipe("code", own=("wait_seconds",)),
    "install": ToolRecipe("install", own=("wait_seconds",)),
    # A deletion names its VMs: the default VM is never implied through an agent.
    "destroy": ToolRecipe("destroy", own=("wait_seconds",), required=("names",)),
    # The one setting named, written into local.yml: the settings recipe's other half.
    "settings_set": ToolRecipe("settings", {"json": True}, required=("name",)),
    "init": ToolRecipe("init", {"no_prompt": True}, own=("wait_seconds",)),
    # The checkout's hosts, listed: switching or removing one is the person's, at a terminal.
    "hosts": ToolRecipe("host", {"json": True}),
}
# The properties a run tool has beside its recipe's.
OWN = {
    "wait_seconds": {
        "type": "integer",
        "minimum": 0,
        "description": "Return after this many seconds with the job still running, its ID to follow with job; "
        "none: at the run's end, or when it waits for the user in the browser",
    },
}

# A parameter's help says what the terminal does. Where the server refuses what that help offers, its own words: the
# default VM is never implied through an agent.
HELP = {
    ("deploy", "names"): "The VMs; a deploy that would create one must name it, since no default VM is implied here. "
    "Without names, only='configure' or 'ssh-config' and check reach every VM this project created",
    ("destroy", "names"): "The VMs to delete: at least one",
}

# The recipes no tool runs, and why.
NO_TOOL = {
    "help": "the tools' own descriptions say what just help says",
    "vault": "the vault is edited at a terminal, by the person: no tool takes or returns a secret",
    "lint": "a contributor's command",
    "test": "a contributor's command",
    "docs": "a contributor's command",
    "vagrant": "a contributor's command",
    "clean": "a contributor's command",
    "mcp": "it registers this server: the person's command",
}

# Options a tool's recipe has that the tool does not take, and why, by tool.
LEFT_OUT = {
    ("install", "lock"): "it rewrites uv.lock after a pin is edited: a contributor's, at a terminal",
    ("settings", "value"): "a write into local.yml: settings_set's, after the person confirms",
    ("settings", "reset"): "a write into local.yml: settings_set's, after the person confirms",
    ("settings_set", "all"): "settings_set changes the one setting named",
    ("settings_set", "changed"): "settings_set changes the one setting named",
    ("destroy", "confirm"): "the server gives it, the names the person typed in its dialog",
    ("init", "token_secret_file"): "a file's contents would go into the vault and to the Proxmox host as the token: "
    "any file this user can read, an SSH key among them",
    ("init", "extra"): "init refuses any word after its options",
    (
        "hosts",
        "name",
    ): "it makes a host the current one, which is the terminal's, as the default VM is: no agent changes "
    "it, and each run tool takes the host it runs against as host",
    ("hosts", "remove"): "it deletes a host's settings and token: the person's, at a terminal, after typing its name",
}

# The settings the justfile reads with env(), each given on just's command line at the server's value: a setting on
# the command line outranks its env(), so nothing of the environment the server was started in reaches a run.
# ANSIBLE_ARGS above all, which every recipe splices into its ansible-playbook line unquoted.
SETTINGS = ("PYTHON", "UV", "ANSIBLE_ARGS", "LOG_DIR", "LOG_RETENTION_DAYS", "STATUS_DIR", "LIVE_DIR")


def settings(config: Config) -> dict[str, str]:
    """Each of SETTINGS at the value the justfile gives it in an empty environment, but the Python and uv just mcp
    pinned."""
    checkout = config.checkout
    return {
        "PYTHON": config.python,
        "UV": config.uv,
        "ANSIBLE_ARGS": "",
        "LOG_DIR": str(checkout / ".logs"),
        "LOG_RETENTION_DAYS": "14",
        "STATUS_DIR": str(checkout / ".cache" / "status"),
        "LIVE_DIR": str(checkout / ".cache" / "live"),
    }


def dump(config: Config, env: dict[str, str]) -> dict:
    """The justfile as `just --dump` gives it, as JSON."""
    out = subprocess.run(
        [config.just, "--justfile", str(config.checkout / "justfile"), "--dump", "--dump-format", "json"],
        cwd=config.checkout,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if out.returncode:
        raise ValueError(out.stderr.strip() or f"exit {out.returncode}")
    return json.loads(out.stdout)


def prop(parameter: dict) -> str:
    """A parameter's property name: its long option's, or its own name's, lowercase with _ for -."""
    return (parameter.get("long") or parameter["name"]).lower().replace("-", "_")


def words(parameter: dict) -> bool:
    """A recipe's words, its * parameter: a list where its name is plural, one word where it is singular (just code's
    NAME), as the justfile names them."""
    return parameter["kind"] in ("star", "plus") and parameter["name"].endswith("S")


def _pattern(parameter: dict) -> dict:
    """just's pattern matches the whole value: anchored the same way here, each of them where a parameter has two."""
    patterns = [f"^(?:{pattern})$" for pattern in parameter.get("pattern") or []]
    if not patterns:
        return {}
    if len(patterns) == 1:
        return {"pattern": patterns[0]}
    return {"allOf": [{"pattern": pattern} for pattern in patterns]}


def schema(recipe: dict, tool: str) -> dict:
    """The input schema of the tool that runs recipe: a property for each parameter but those LEFT_OUT and FIXED."""
    properties = {}
    for parameter in recipe["parameters"]:
        name = prop(parameter)
        if (tool, name) in LEFT_OUT or name in TOOLS[tool].fixed:
            continue
        description = HELP.get((tool, name)) or parameter.get("help") or ""
        if words(parameter):
            item = {"type": "string", **_pattern(parameter)}
            properties[name] = {"type": "array", "items": item, "description": description}
        elif parameter.get("value") is not None:
            properties[name] = {"type": "boolean", "description": description}
        else:
            properties[name] = {"type": "string", "description": description, **_pattern(parameter)}
    properties |= {name: OWN[name] for name in TOOLS[tool].own}
    schema = {"type": "object", "properties": properties, "additionalProperties": False}
    for name in TOOLS[tool].required:
        schema.setdefault("required", []).append(name)
        if properties[name]["type"] == "array":
            properties[name]["minItems"] = 1
    return schema


def command(config: Config, recipe: dict, tool: str, arguments: dict, extra: dict[str, str] | None = None) -> list[str]:
    """just's argv for one call: the justfile, every setting at the server's value and then extra (ATTENDED), the
    recipe, then its options as --long=value (a flag bare), in the recipe's order, the fixed ones among them, then its
    words, each one word."""
    given = {**arguments, **TOOLS[tool].fixed}
    argv = [config.just, "--justfile", str(config.checkout / "justfile")]
    argv += [f"{name}={value}" for name, value in {**settings(config), **(extra or {})}.items()]
    argv.append(recipe["name"])
    tail: list[str] = []
    for parameter in recipe["parameters"]:
        name = prop(parameter)
        value = given.get(name)
        if value in (None, "", False, []):
            continue
        if parameter["kind"] in ("star", "plus"):
            given_words = list(value) if isinstance(value, list) else [str(value)]
            # just reads a word that starts with - as an option, before any pattern: --only=x as a VM's name would
            # be the option. The schema's pattern says so too, but a client need not hold a call to it.
            refused = [word for word in given_words if word.startswith("-")]
            if refused:
                raise ValueError(f"{', '.join(refused)}: {prop(parameter)} never start with -")
            tail += given_words
        elif value is True:
            argv.append(f"--{parameter['long']}")
        else:
            argv.append(f"--{parameter['long']}={value}")
    return argv + tail


def describe(recipe: dict, extra: str = "") -> str:
    """A tool's description: the recipe's [doc], then what the server adds."""
    doc = (recipe.get("doc") or "").rstrip(".")
    return f"{doc}. {extra}".strip() if extra else f"{doc}."


def recipe_of(dumped: dict, tool: str) -> dict:
    return dumped["recipes"][TOOLS[tool].recipe]
