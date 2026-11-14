"""What the server knows of the checkout it runs: its branch and commit, said on every result; whether a run may
change anything from it; whether what the server runs changed since it started; whether its vault can be read.

The settings and the vault live in one checkout, whose branch other sessions switch, so a run that changes something
runs only on main with no tracked change, unless just mcp was given --any-branch (the branch guard). A pull, a branch
or an edit changes the justfile, and with it the tools' schemas, or the server's own code, which it imported at its
start and each job's supervisor imports anew: every tool then answers "restart" rather than run a schema that no
longer matches, or older code beside newer.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from claude_vm.mcp.config import Config


@dataclass(frozen=True)
class Head:
    branch: str
    commit: str
    dirty: bool

    def said(self) -> str:
        changes = ", with tracked changes" if self.dirty else ""
        return f"on {self.branch} at {self.commit}{changes}"


def _git(config: Config, env: dict[str, str], *args: str) -> str:
    out = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(config.checkout), *args],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return out.stdout.strip() if out.returncode == 0 else ""


def head(config: Config, env: dict[str, str]) -> Head:
    """The checkout's branch (HEAD where it is detached), its short commit, and whether a tracked file is changed."""
    return Head(
        branch=_git(config, env, "rev-parse", "--abbrev-ref", "HEAD") or "?",
        commit=_git(config, env, "rev-parse", "--short", "HEAD") or "?",
        dirty=bool(_git(config, env, "status", "--porcelain", "--untracked-files=no")),
    )


def run_refusal(config: Config, at: Head) -> str:
    """Why a run that changes something may not start from the checkout as it is, or nothing."""
    if config.any_branch or (at.branch == "main" and not at.dirty):
        return ""
    return (
        f"The checkout {config.checkout} is {at.said()}: these tools run only main, with no tracked change, since the "
        "settings and the vault are that checkout's. Switch it back, or run just mcp --any-branch to allow any branch."
    )


# What the server runs, from the checkout: the justfile its tools are made from, tools/mcp, which a job's supervisor
# runs anew, and the package, which the server imported at its start and a supervisor imports anew.
RUNS = ("justfile", "tools/mcp", "lib/claude_vm")


def code(checkout: Path) -> str:
    """A digest of what the server runs, as the checkout's files hold it now: each file's path and bytes, Python's
    caches and dot-names aside. Read from the files, never from git, so an edit not yet committed counts and a commit
    does not."""
    whole = hashlib.sha256()
    for part in RUNS:
        top = checkout / part
        found = [top] if top.is_file() else sorted(path for path in top.rglob("*") if path.is_file())
        for path in found:
            # No module is a dot-name: Finder's .DS_Store, or an editor's swap file beside a module it has open, is no
            # change. Named below top, since a checkout may itself be under one (.claude/worktrees/).
            if any(name == "__pycache__" or name.startswith(".") for name in path.relative_to(top).parts):
                continue
            try:
                data = path.read_bytes()
            except OSError:
                data = b""
            whole.update(f"{path.relative_to(checkout)}\0{len(data)}\0".encode())
            whole.update(data)
    return whole.hexdigest()


def changed_refusal(config: Config, at_start: str) -> str:
    """The restart notice, when what the server runs is no longer what it read at its start."""
    if code(config.checkout) == at_start:
        return ""
    return (
        f"The checkout {config.checkout} changed since this server started (a pull, a branch, an edit): restart this "
        "server with /mcp, so that it runs the checkout's code and its tools match the justfile again."
    )


VAULT_FILE = Path("inventory") / "group_vars" / "all" / "vault.yml"
VAULT_PASS = ".vault_pass"


def vault_refusal(config: Config) -> str:
    """Why a recipe that reads the vault cannot run without a terminal, or nothing: a vault ansible-vault would ask the
    password of, since a run has no terminal to ask at. The recipes refuse the rest (tools/check vault)."""
    vault = config.checkout / VAULT_FILE
    try:
        encrypted = vault.read_bytes()[:14] == b"$ANSIBLE_VAULT"
    except OSError:
        return ""
    if not encrypted or (config.checkout / VAULT_PASS).is_file() or config.vault_password_file:
        return ""
    return (
        f"The vault is encrypted and {config.checkout} has no {VAULT_PASS}, so a run here could not read it. just init "
        "makes one; or run just mcp --vault-password-file <file> to name where the password is."
    )
