"""Where a checkout's hosts are, read from their files alone: the first host in inventory/, each other in a folder of
its own under hosts/, and which one is current, as Host records (docs/architecture/custom-code.md §7.14). The core's
reading half: choose.py and resolve.py decide from what it reads, and nothing here writes or opens a vault.

A host is an inventory source laid over the shared one: a run against hosts/<name> gets inventory/controller.yml, then
hosts/<name>, so its group_vars outrank the shared ones and its hosts.yml is the run's one Proxmox host.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

from ..vault import lenient_yaml
from .kinds import KINDS, Kind
from .model import (  # noqa: F401 - the layout's names, which its callers take from here
    CONTROLLER,
    DEFAULT_SETTINGS,
    DEFAULT_VAULT,
    NAME,
    RESERVED,
    SHARED,
    SHARED_HOSTS,
    SHARED_LOCAL,
    SHARED_VAULT,
    Host,
    OtherHost,
    Refused,
    name_refusal,
)


def _yaml(path: Path) -> object:
    """The file's YAML, None where it is missing; Refused where it is not YAML, saying where, never what."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Refused(f"{path} could not be read ({error.strerror})") from error
    import yaml

    try:
        return lenient_yaml(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        raise Refused(f"{path} is not YAML{f' at line {mark.line + 1}' if mark else ''}") from error


def _groups(data: object) -> Iterator[tuple[str, dict]]:
    """Every group of an inventory file's YAML, by name, at any depth: all's children and theirs."""
    if not isinstance(data, dict):
        return
    for name, group in data.items():
        if isinstance(group, dict):
            yield str(name), group
            children = group.get("children")
            if isinstance(children, dict):
                yield from _groups(children)


def _hosts_of(data: object) -> dict[str, dict]:
    """Every host an inventory file's YAML defines, in any group, with its variables there."""
    found: dict[str, dict] = {}
    for _, group in _groups(data):
        hosts = group.get("hosts")
        if isinstance(hosts, dict):
            for name, values in hosts.items():
                found.setdefault(str(name), values if isinstance(values, dict) else {})
    return found


def _kind_of(data: object) -> tuple[Kind, list[str]] | None:
    """The kind whose group the file fills, and that group's hosts in their order; None for none."""
    for kind in KINDS.values():
        for name, group in _groups(data):
            if name == kind.group and isinstance(group.get("hosts"), dict) and group["hosts"]:
                return kind, [str(host) for host in group["hosts"]]
    return None


class Layout:
    """The hosts of the checkout at ``checkout``, the others' folders in ``hosts_dir``."""

    def __init__(self, checkout: Path, hosts_dir: Path):
        self.checkout = checkout
        self.hosts_dir = hosts_dir
        # The folder as a run names it: relative where it is the checkout's own, as every other path is.
        try:
            self.hosts_rel = str(hosts_dir.resolve().relative_to(checkout.resolve()))
        except ValueError:
            self.hosts_rel = str(hosts_dir)

    # ------------------------------------------------------------------------------------------- reading ----

    def first(self) -> Host | None:
        """The host in inventory/hosts.yml: the first of its kind's group, named as the inventory names it. None
        without the file, or a host in it - a checkout before just init, or a test's."""
        data = _yaml(self.checkout / SHARED_HOSTS)
        found = _kind_of(data)
        if found is None:
            return None
        kind, names = found
        values = _hosts_of(data).get(names[0], {})
        local = _yaml(self.checkout / SHARED_LOCAL)
        # Its name as the inventory gives it, which a second host's runs say as --host <name>: held to the rule beside
        # another host, and nothing with one host alone, which no command names.
        why = name_refusal(names[0])
        return Host(
            name=names[0],
            folder=SHARED,
            first=True,
            kind=kind,
            address=str(values.get("ansible_host") or names[0]),
            local=local if isinstance(local, dict) else {},
            problem=f"{SHARED_HOSTS} names it {names[0]}, and {why}: rename it there, its address staying its "
            "ansible_host, to have another host beside it"
            if why
            else "",
        )

    def folders(self) -> list[str]:
        """The names of the folders under hosts/, sorted: every entry but a file (hosts/current) and a dot-file."""
        try:
            entries = sorted(os.scandir(self.hosts_dir), key=lambda entry: entry.name)
        except FileNotFoundError:
            return []
        return [entry.name for entry in entries if entry.is_dir() and not entry.name.startswith(".")]

    def other(self, name: str) -> Host:
        """The host in hosts/<name>, with what keeps it from being used, if anything: what is known of it, each field
        worked out first, then the record built once."""
        folder = f"{self.hosts_rel}/{name}"

        def unusable(problem: str, **known) -> Host:
            return Host(name=name, folder=folder, first=False, problem=problem, **known)

        if not NAME.fullmatch(name) or name in RESERVED:
            return unusable(
                f"{self.hosts_rel}/{name} is no host's folder: a host's name is lowercase letters, digits and -, "
                f"at most 31, and never {', '.join(sorted(RESERVED))}"
            )
        first = self.first()
        if first is not None and first.name == name:
            return unusable(f"{self.hosts_rel}/{name} has the name of the host in {SHARED_HOSTS}: rename the folder")
        bare = Host(name=name, folder=folder, first=False)
        # A file of its that is not YAML costs this host alone: said in its place, as any other reason it is unusable.
        try:
            data = _yaml(self._path(bare.hosts_file))
            local = _yaml(self._path(bare.local_file))
        except Refused as refused:
            return unusable(str(refused))
        if data is None:
            return unusable(f"not set up: just init --host {name}")
        strays = self._strays(folder)
        if strays:
            return unusable(
                f"{folder}/ holds {', '.join(strays)} beside hosts.yml, and Ansible reads every inventory file in "
                f"a host's folder: any host there would be this run's too. Move {'it' if len(strays) == 1 else 'them'} "
                "out of the folder"
            )
        found = _kind_of(data)
        defined = sorted(_hosts_of(data))
        if found is None or found[1] != [name] or defined != [name]:
            kinds = " or ".join(kind.group for kind in KINDS.values())
            return unusable(
                f"{bare.hosts_file} must define one host, {name}, in the {kinds} group, and no other (it defines "
                f"{', '.join(defined) or 'none'}): any other host there would be this run's too"
            )
        kind = found[0]
        values = _hosts_of(data)[name]
        if not values.get("ansible_host"):
            return unusable(
                f"{bare.hosts_file} gives {name} no ansible_host, its address: just init --host {name}", kind=kind
            )
        own = local if isinstance(local, dict) else {}
        known = {"kind": kind, "address": str(values["ansible_host"]), "local": own}
        missing = [setting for setting in kind.always if own.get(setting) in (None, "")]
        if missing:
            return unusable(
                f"{bare.local_file} does not set {', '.join(missing)}, which each host sets for itself rather than "
                f"take {SHARED_LOCAL}'s: just init --host {name} writes them",
                incomplete=True,
                **known,
            )
        return Host(name=name, folder=folder, first=False, **known)

    def _strays(self, folder: str) -> list[str]:
        """What in a host's folder Ansible would read as inventory beside its hosts.yml: a YAML or JSON file - the
        extensions the yaml plugin takes, the one ansible.cfg enables - or a folder, which it reads through, but for
        group_vars and host_vars."""
        try:
            entries = sorted(os.scandir(self._path(folder)), key=lambda entry: entry.name)
        except OSError:
            return []
        return [
            f"{entry.name}{'/' if entry.is_dir() else ''}"
            for entry in entries
            if entry.name != "hosts.yml"
            and not (entry.name in ("group_vars", "host_vars") and entry.is_dir())
            and (entry.is_dir() or os.path.splitext(entry.name)[1] in (".yml", ".yaml", ".json"))
        ]

    def others(self) -> list[Host]:
        return [self.other(name) for name in self.folders()]

    def every(self) -> list[Host]:
        """Every host: the first, where there is one, then each folder's, by name."""
        first = self.first()
        return ([first] if first else []) + self.others()

    def many(self) -> bool:
        """Whether the checkout has more than one host: any folder under hosts/ at all."""
        return bool(self.folders())

    def current_name(self) -> str:
        """The name hosts/current holds, "" with none."""
        try:
            text = (self.hosts_dir / "current").read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except OSError as error:
            raise Refused(f"{self.hosts_rel}/current could not be read ({error.strerror})") from error
        return text.strip()

    def _path(self, path: str) -> Path:
        return Path(path) if Path(path).is_absolute() else self.checkout / path

    def named(self, name: str, why: str, lenient: bool = False) -> Host:
        """The host called ``name``, set up; Refused otherwise, ``why`` saying where the name came from. ``lenient``
        takes one that lacks a setting it sets for itself, for just settings and just vault to put it right."""
        first = self.first()
        if first is not None and name == first.name:
            return first
        if name in self.folders():
            host = self.other(name)
            if host.problem and not (lenient and host.incomplete):
                raise Refused(f"{why} {name}, which cannot be used: {host.problem}")
            return host
        known = [host.name for host in self.every()]
        raise Refused(
            f"{why} {name}, which is no host of this checkout's ({', '.join(known) or 'none'}): "
            f"just init --host {name} --address <address> sets one up"
        )

    def others_of(self, host: Host | None) -> tuple[OtherHost, ...]:
        """Every other host of the checkout: the guards of a run against ``host`` keep them out of its VMs' names and
        aliases, and its destroy from their keys. A host not set up has the address its files give, or none."""
        found = []
        for other in self.every():
            if host is not None and other.name == host.name:
                continue
            if not NAME.fullmatch(other.name) and not other.first:
                continue
            found.append(OtherHost(name=other.name, address=other.address))
        return tuple(found)

    def defaults(self) -> dict:
        """The inventory defaults, as YAML reads them: a setting a host's files leave unset shows its default."""
        try:
            found = _yaml(self.checkout / "inventory" / "group_vars" / "all" / "defaults.yml")
        except Refused:
            return {}
        return found if isinstance(found, dict) else {}

    # ------------------------------------------------------------------------------------------- the names ----

    def alias_file(self) -> Path | None:
        """The managed SSH config, where each VM's alias records its host: as the shared local.yml names it, where it
        names it as a path, else the default - by its 1.x name while that is the one there. None with the aliases off,
        when nothing records where a VM is."""
        local = _yaml(self.checkout / SHARED_LOCAL)
        local = local if isinstance(local, dict) else {}
        if local.get("claude_ssh_config") is False:
            return None
        named = local.get("claude_ssh_config_file")
        if isinstance(named, str) and named and "{" not in named:
            return Path(named).expanduser()
        default = Path("~/.ssh/agent-warehouse.conf").expanduser()
        legacy = default.with_name("claude-on-proxmox.conf")
        return legacy if not default.exists() and legacy.exists() else default

    def origins(self, names: list[str]) -> dict[str, str]:
        """Of ``names``, those whose alias records one of the checkout's hosts, and that host: where each VM is. A
        name with no alias, or one recording a host the checkout does not have, is none of them."""
        path = self.alias_file()
        try:
            text = path.read_text(encoding="utf-8") if path else ""
        except OSError:
            text = ""
        from ..proxmox import ssh_config_blocks

        known = {host.name for host in self.every()}
        recorded = {block["name"]: block["origin"] for block in ssh_config_blocks(text)}
        return {name: recorded[name] for name in names if recorded.get(name) in known}

    # ------------------------------------------------------------------------------------------- the rules ----

    def never_inherited(self, host: Host) -> list[str]:
        """The lines a host must add before a run: each setting of its kind that is one network's or one machine's,
        which the shared local.yml sets and the host's does not."""
        if host.first or host.kind is None:
            return []
        shared = _yaml(self.checkout / SHARED_LOCAL)
        shared = shared if isinstance(shared, dict) else {}
        return [name for name in host.kind.never_inherited if name in shared and name not in host.local]

    def made_from_host(self, host: Host) -> dict[str, list[str]]:
        """Of the internals the inventory defaults make from the run's host, those a local.yml the run reads sets, by
        file: the shared one, and a host's own."""
        if host.kind is None:
            return {}
        files = [SHARED_LOCAL] if host.first else [SHARED_LOCAL, host.local_file]
        found = {}
        for file in files:
            values = _yaml(self._path(file))
            names = [name for name in host.kind.from_host if isinstance(values, dict) and name in values]
            if names:
                found[file] = names
        return found


def checked_digest(paths: list[Path]) -> str:
    """A key for the vault files as they are now - name, size and time - under which a check that passed is kept."""
    lines = []
    for path in paths:
        try:
            info = path.stat()
        except OSError:
            return ""
        lines.append(f"{path} {info.st_size} {info.st_mtime_ns}")
    return json.dumps(lines)
