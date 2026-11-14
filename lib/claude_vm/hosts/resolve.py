"""Whether a run may go against the host chosen: every check, in their order, and what the run then reads - its settings
files, vaults, inventory sources and the other hosts (docs/architecture/custom-code.md §7.14). The vault check opens
the vaults in memory for their names, so a password script may run here: only a run asks this, never a read of which
host. Decides and writes nothing: a check that passed is kept by tools/host.

The token rule itself is the inventory defaults' (defaults.yml picks the vault's name by where the run's host was
defined); these checks refuse what would defeat it, before anything starts."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping

from .. import vault
from .choose import pick
from .layout import Layout, checked_digest
from .model import (
    DEFAULT_SETTINGS,
    DEFAULT_VAULT,
    SHARED_LOCAL,
    SHARED_VAULT,
    Choice,
    Host,
    HostRead,
    Note,
    Refused,
    Request,
    RunHost,
    VaultCheck,
)

# An address a host's hosts.yml gives it, as it may reach a run: a host name or an IP address, one word.
ADDRESS = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:_-]{0,252}")


def vault_check(layout: Layout, host: Host, password_files: tuple[str, ...], kept: Mapping[str, object]) -> VaultCheck:
    """Why the vaults would send a host the wrong secret, or "": the shared vault holds the first host's token under
    the kind's shared name and never another host's, and a host's vault its own under the host name and never the
    shared one; and neither sets what the inventory defaults make from the run's host. defaults.yml picks the name by
    where the run's host was defined, so either in the other's file would defeat it. Opened in memory for the names
    alone. ``kept`` is hosts/.checked.json as tools/host read it: a check kept under the files' key is not run again."""
    kind = host.kind
    shared = layout.checkout / SHARED_VAULT
    own = None if host.first else layout._path(host.vault_file)
    key = checked_digest([shared] if own is None else [shared, own])
    if key and kept.get(host.name) == key:
        return VaultCheck(key=key)
    shared_names = vault.names(shared, password_files)
    own_names = None if own is None else vault.names(own, password_files)
    for file, names in ((SHARED_VAULT, shared_names), (host.vault_file, own_names)):
        made = [name for name in kind.from_host if names is not None and name in names]
        if made:
            return VaultCheck(
                key=key,
                refusal=f"{file} sets {', '.join(made)}, which the inventory defaults make from the run's host - where "
                f"its API is, and the secret it is sent - and which a vault would set for "
                f"{'every host' if file == SHARED_VAULT else host.name} alike: take "
                f"{'it' if len(made) == 1 else 'them'} out with just vault"
                f"{'' if file == SHARED_VAULT else ' --host ' + host.name}",
            )
    if not host.first and shared_names is not None and kind.secret_host in shared_names:
        return VaultCheck(
            key=key,
            refusal=f"{SHARED_VAULT} sets {kind.secret_host}, which is a host's own and belongs in its vault: a host "
            f"whose vault lacked it would be sent this one. Move it with just vault, and just vault --host {host.name}",
        )
    if own_names is not None and kind.secret_shared in own_names:
        return VaultCheck(
            key=key,
            refusal=f"{host.vault_file} sets {kind.secret_shared}, the first host's name for its secret: {host.name}'s "
            f"is {kind.secret_host}. Rename it there with just vault --host {host.name}",
        )
    if own_names is not None and kind.secret_host not in own_names:
        return VaultCheck(
            key=key,
            refusal=f"{host.vault_file} does not set {kind.secret_host}, {host.name}'s secret: just init --host "
            f"{host.name}, or set it with just vault --host {host.name}",
        )
    # A vault no password here opens - a vault ID's, a client script's - is Ansible's to open: said, never kept as
    # checked, and the run goes on, the token rule still sending the host the secret of its own vault's name alone.
    unopened = tuple(
        file
        for file, path, names in ((SHARED_VAULT, shared, shared_names), (host.vault_file, own, own_names))
        if path is not None and names is None and vault.encrypted(path)
    )
    if unopened:
        return VaultCheck(key=key, unopened=unopened)
    return VaultCheck(key=key, keep=bool(key) and shared_names is not None and (own is None or own_names is not None))


def unopened_note(check: VaultCheck) -> Note:
    return Note(
        level="warning",
        text=f"{' and '.join(check.unopened)} could not be opened here (a vault ID's password, or a client script's), "
        "so the names of the secrets in it are not checked before the run",
    )


def reads(
    layout: Layout, host: Host, request: Request, environ: Mapping[str, str], kept: Mapping[str, object]
) -> tuple[str, VaultCheck | None]:
    """Why a run against a host, beside another, would read what is not its own, or "": a setting of one network the
    shared files set and a host in hosts/ does not, an internal made from the run's host that a settings file sets, a
    token under the other vault's name. With the vault check, where it ran."""
    lines = layout.never_inherited(host)
    if lines:
        return (
            f"{SHARED_LOCAL} sets {', '.join(lines)}, which describe{'s' if len(lines) == 1 else ''} one "
            f"network or one host, and {host.local_file} does not: add {'it' if len(lines) == 1 else 'them'} "
            f"there for {host.name}, the value or the setting's default to say none",
            None,
        )
    for file, made in layout.made_from_host(host).items():
        return (
            f"{file} sets {', '.join(made)}, which the inventory defaults make from the run's host - where its API is, "
            "and the secret it is sent - and which a settings file would set for "
            f"{'every host' if file == SHARED_LOCAL else host.name} alike: take "
            f"{'it' if len(made) == 1 else 'them'} out. A host's address is ansible_host in its hosts.yml, and its "
            f"secret {host.kind.secret_shared} in the shared vault for the first host, {host.kind.secret_host} in its "
            "own for another",
            None,
        )
    check = vault_check(layout, host, vault.password_files(request.vault_pass, environ), kept)
    return check.refusal, check


def resolve(
    layout: Layout, choice: Choice, request: Request, environ: Mapping[str, str], kept: Mapping[str, object]
) -> RunHost:
    """The run's host once every check has passed, in today's order; Refused with the notes said before the refusal.
    ``environ`` is the run's environment, never this process's own read here; ``kept`` hosts/.checked.json."""
    said: list[Note] = []
    try:
        return _resolved(layout, choice, request, environ, kept, said)
    except Refused as refused:
        raise Refused(str(refused), notes=refused.notes or tuple(said)) from refused


def _resolved(layout, choice, request, environ, kept, said: list[Note]) -> RunHost:
    host, many = choice.host, choice.many
    overlay = host is not None and not host.first
    if many:
        first = layout.first()
        if first is not None and first.problem:
            raise Refused(f"the first host: {first.problem}")
        # A run's inventory is its host's, given as -i: an exported one would be the run's instead wherever none is
        # given - every run against the first host - and a host's sources a variable left exported could carry into
        # a later run against another, titled for it.
        if environ.get("ANSIBLE_INVENTORY"):
            raise Refused(
                f"ANSIBLE_INVENTORY is exported, and this checkout has more than one host: each run reads its host's "
                f"own inventory, {host.name}'s here, and nothing else. Unset it"
            )
    if overlay and (
        request.vault_file != DEFAULT_VAULT or " ".join(request.settings_files.split()) != DEFAULT_SETTINGS
    ):
        raise Refused(
            f"VAULT_FILE and SETTINGS_FILES name the first host's files, and this run's host is {host.name}: give "
            "them only for a run against the first host"
        )
    check = None
    if many and not request.lenient:
        why, check = reads(layout, host, request, environ, kept)
        if check is not None and check.unopened:
            said.append(unopened_note(check))
        if why:
            raise Refused(why)
    others = layout.others_of(host) if many else ()
    for other in others:
        if other.address and not ADDRESS.fullmatch(other.address):
            raise Refused(f"{other.name}'s ansible_host is no address or host name, one word: fix it in its hosts.yml")
    if overlay:
        settings = (SHARED_LOCAL, host.hosts_file, host.local_file)
        vaults = (SHARED_VAULT, host.vault_file)
    else:
        settings, vaults = tuple(request.settings_files.split()), (request.vault_file,)
    if choice.routed and choice.current and choice.current != host.name:
        placed = sorted(name for name, _ in choice.origins)
        said.append(
            Note(
                level="note",
                text=f"{', '.join(placed)} {'is' if len(placed) == 1 else 'are'} {host.name}'s, as "
                f"{'its' if len(placed) == 1 else 'their'} SSH alias records: this run is against {host.name}, not "
                f"the current host, {choice.current}.",
            )
        )
    return RunHost(
        choice=choice,
        settings_files=settings,
        vault_files=vaults,
        sources=host.sources if overlay else (),
        others=others,
        notes=tuple(said),
        check=check,
    )


def every(
    layout: Layout, request: Request, environ: Mapping[str, str], kept: Mapping[str, object], leave_out: str = ""
) -> Iterator[HostRead]:
    """Every host, for just status to read each: whether it is the current one, its sources and vaults, the other hosts
    its run is handed, and why it cannot be read, if anything does - said in its own row, never refusing the others'.
    It runs the vault check, so a password script may run: no front end reaches it but through just status.
    ``leave_out`` is one host's name: a creating deploy asks the others for the names it would make. One host at a
    time, so that what each says is said as it is reached."""
    current = pick(layout)
    for host in layout.every():
        if host.name == leave_out:
            continue
        notes: tuple[Note, ...] = ()
        problem = host.problem
        if not problem:
            problem, check = reads(layout, host, request, environ, kept)
            if check is not None and check.unopened:
                notes = (unopened_note(check),)
        if not problem:
            for file in [SHARED_VAULT] if host.first else [SHARED_VAULT, host.vault_file]:
                path = layout._path(file)
                if not path.is_file() or not vault.encrypted(path):
                    problem = (
                        f"{file} is missing or not encrypted: just vault{'' if host.first else ' --host ' + host.name}"
                    )
        yield HostRead(
            name=host.name,
            current=current is not None and host.name == current.name,
            sources=host.sources,
            vault_files=(request.vault_file,) if host.first else (SHARED_VAULT, host.vault_file),
            others=layout.others_of(host),
            problem=problem,
            notes=notes,
        )
