"""Setting up a host, switching to one, removing one: each a plan, decided here and performed by tools/host
(docs/architecture/custom-code.md §7.14), which writes the files, hosts/current and the removal. Every refusal is
here, in its order and with its words; nothing here writes."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from .. import vault
from .kinds import KINDS, Kind
from .layout import Layout
from .model import RESERVED, SHARED, SHARED_HOSTS, SHARED_LOCAL, SHARED_VAULT, Host, InitPlan, NewFile, Refused

# Where just init reads the token's secret from, but for --token-secret-file: the name from 2.0, then 1.x's.
SECRET_VARIABLES = ("AGENT_WAREHOUSE_TOKEN_SECRET", "CLAUDE_ON_PROXMOX_TOKEN_SECRET")


def _first_host(kind: Kind) -> InitPlan:
    """The first host's: the shared files, which the recipe copies from their examples, its secret under the shared
    name. Nothing is read for it."""
    return InitPlan(first=True, payload={"init_host": {"name": "", "secret": kind.secret_shared}})


def plan_init(
    layout: Layout,
    name: str,
    *,
    address: str,
    asking: bool,
    shared: bool,
    secret_file: bool,
    vault_pass: str,
    environ: Mapping[str, str],
    templates: Path,
) -> InitPlan:
    """What just init does for the host named, or the current host's without a name - the first's until another is
    chosen - from tools/templates/host/: every refusal first, then each file it lacks. A host is never set up without
    an address given or a terminal to ask for one, nor at all while the checkout has no other: there, a bare word was
    2.4's address."""
    # One kind of host so far: the first host's, and every new one's.
    (kind,) = KINDS.values()
    # The current host's, as every command's: read only where hosts/current names one, so that a bare just init of one
    # host reads nothing, as before there were hosts.
    current = "" if name else layout.current_name()
    if not name and not current:
        return _first_host(kind)
    first = layout.first()
    folders = layout.folders()
    if not name:
        if first is not None and current == first.name:
            return _first_host(kind)
        if current not in folders:
            raise Refused(
                f"{layout.hosts_rel}/current names {current}, which is no host of this checkout's: just host <name> "
                "chooses another"
            )
        name = current
    elif first is not None and name == first.name:
        if not folders and not address:
            raise Refused(
                f"--host names a host from 2.5, and with no other host here, {name} may be 2.4's address of the "
                f"Proxmox host: give that as --address {name}. {name} is the name of this checkout's one host, which "
                "just init alone sets up"
            )
        return _first_host(kind)
    if shared:
        # Every host's, in the shared files, which the first host's init writes.
        raise Refused(
            f"the GitHub account and the VPN are every host's, in the shared files, and this init is {name}'s: give "
            f"--github-user and --tailscale to just init{' --host ' + first.name if first is not None else ''}"
        )
    if first is None:
        raise Refused(
            f"{SHARED_HOSTS} names no host yet: just init sets up the first one there - given as --address "
            f"{name}, if {name} is its address - then just init --host <name> another"
        )
    if name in RESERVED:
        raise Refused(f"{name} is no host's name: never {', '.join(sorted(RESERVED))}")
    if name not in folders and not address:
        if not folders:
            raise Refused(
                f"--host names a host from 2.5, and {name} is none of this checkout's ({first.name}). If {name} is "
                f"the Proxmox host's address, give it as --address {name}; to set up a second host named {name}, "
                f"give its address too: just init --host {name} --address <address>"
            )
        if not asking:
            raise Refused(
                f"{name} is no host of this checkout's yet, and there is no terminal to ask its address at: "
                f"just init --host {name} --address <address>"
            )
    if first.problem:
        raise Refused(f"the first host: {first.problem}")
    exported = next((variable for variable in SECRET_VARIABLES if environ.get(variable)), "")
    if exported and not secret_file:
        # The first deploy's guide exports it for the first host: left in the shell, it would be written to this
        # host's vault and sent to its API as this host's.
        raise Refused(
            f"{exported} is exported, and this init is {name}'s, whose token is its own: unset it, and give {name}'s "
            f"secret in a file only you can read, just init --host {name} --token-secret-file <file>"
        )
    host = Host(name=name, folder=f"{layout.hosts_rel}/{name}", first=False, kind=kind)
    password_files = [one for one in vault.password_files(vault_pass, environ) if os.path.isfile(one)]
    if (
        vault.encrypted(layout.checkout / SHARED_VAULT)
        and not password_files
        and not vault.encrypted(layout._path(host.vault_file))
    ):
        # just init would make .vault_pass anew for the host's vault, which then opens neither the shared one nor any
        # run that reads both.
        raise Refused(
            f"{SHARED_VAULT} is encrypted, and there is no {Path(vault_pass).name or '.vault_pass'} or "
            f"ANSIBLE_VAULT_PASSWORD_FILE to open it with: {name}'s vault is encrypted with the same password, so put "
            "it in a file only you can read, named by ANSIBLE_VAULT_PASSWORD_FILE or as .vault_pass, first"
        )
    files = []
    for file in (host.hosts_file, host.local_file, host.vault_file):
        path = layout._path(file)
        if path.exists():
            continue
        relative = path.relative_to(layout._path(host.folder))
        text = (templates / f"{relative}.example").read_text(encoding="utf-8")
        text = text.replace("\n        pve:\n", f"\n        {name}:\n").replace("pve-default", f"{name}-default")
        files.append(NewFile(path=str(path), said=file, text=text))
    payload = {
        "init_hosts_file": str(layout._path(host.hosts_file)),
        "init_local_file": str(layout._path(host.local_file)),
        "init_vault_file": str(layout._path(host.vault_file)),
        "init_host": {
            "name": name,
            "secret": kind.secret_host,
            "always": list(kind.always),
            "default_vm": f"{name}-default",
            # Whether it is the current host already, as a bare just init's is: then no switch is offered.
            "current": layout.current_name() == name,
            "hosts_said": host.hosts_file,
            "local_said": host.local_file,
            "vault_said": host.vault_file,
        },
    }
    return InitPlan(
        first=False,
        payload=payload,
        files=tuple(files),
        sources=host.sources,
        settings_files=(SHARED_LOCAL, host.hosts_file, host.local_file),
        vault_file=host.vault_file,
    )


def plan_switch(layout: Layout, name: str) -> Host:
    """The host just host makes the current one, set up; Refused otherwise. tools/host writes hosts/current."""
    return layout.named(name, "just host names")


def plan_remove(layout: Layout, name: str) -> Path:
    """The folder just host --remove deletes: never the first host's, whose settings are the shared files, nor the
    current host's, nor a folder that is no host's. tools/host deletes it, once the name was typed at a terminal."""
    first = layout.first()
    if first is not None and name == first.name:
        raise Refused(
            f"{name} is the first host, whose settings are {SHARED}/'s: just host --remove removes "
            "only a host in hosts/"
        )
    if name not in layout.folders():
        raise Refused(f"{layout.hosts_rel}/{name} is no host's folder: just host lists them")
    if name == layout.current_name():
        raise Refused(f"{name} is the current host: switch to another first, just host <name>")
    return layout.hosts_dir / name
