"""Where a checkout's hosts are, read from their files alone: the first host in inventory/, each other in a folder of
its own under hosts/, and which one is current. The one place that knows the layout (docs/architecture/custom-code.md
§7.14): every recipe asks tools/check host, which asks this, for the host of its run and the files and inventory
sources that host is read from. Reads, writes only hosts/current, and needs neither Ansible nor the vault - but for
vault_names, which opens the vaults to read their names, never their values.

A host is an inventory source laid over the shared one: a run against hosts/<name> gets inventory/controller.yml, then
hosts/<name>, so its group_vars outrank the shared ones and its hosts.yml is the run's one Proxmox host.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .kinds import KINDS, Kind

# A host's name: a folder's under hosts/, a line of hosts/current, a word of the just line.
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,30}")
# Never a host's name: the selection's file, the listed machines' origin, this machine, a stage of just deploy.
RESERVED = frozenset({"current", "machines", "localhost", "template", "provision", "configure", "check", "ssh-config"})
SHARED = "inventory"
SHARED_HOSTS = f"{SHARED}/hosts.yml"
SHARED_LOCAL = f"{SHARED}/group_vars/all/local.yml"
SHARED_VAULT = f"{SHARED}/group_vars/all/vault.yml"
CONTROLLER = f"{SHARED}/controller.yml"
# The settings files and vault every recipe names for the first host: the justfile's VAULT_FILE and SETTINGS_FILES at
# their defaults. Another value given on the command line is the first host's alone.
DEFAULT_VAULT = SHARED_VAULT
DEFAULT_SETTINGS = f"{SHARED_HOSTS} {SHARED_LOCAL}"


class Refused(Exception):
    """What the resolver will not do, said as the recipe's error."""


def name_refusal(name: str) -> str:
    """Why ``name`` can be no host's name, or "": the rule every --host, folder and hosts/current is held to."""
    if NAME.fullmatch(name) and name not in RESERVED:
        return ""
    return f"a host's name is lowercase letters, digits and -, at most 31, and never {', '.join(sorted(RESERVED))}"


class _Loader:
    """YAML as Ansible writes it: a tag such as !vault is a value, never a key, and read as None."""

    loader = None

    @classmethod
    def load(cls, text: str):
        import yaml

        if cls.loader is None:

            class Loader(yaml.SafeLoader):
                pass

            Loader.add_multi_constructor("!", lambda loader, suffix, node: None)
            cls.loader = Loader
        return yaml.load(text, Loader=cls.loader)  # noqa: S506 - a SafeLoader, with tags read as None


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
        return _Loader.load(text)
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


@dataclass
class Host:
    """One of the checkout's hosts, as its files say it is."""

    name: str
    folder: str  # inventory, or hosts/<name>: where its files are, relative to the checkout or absolute
    first: bool  # the host in inventory/, whose files are the shared ones
    kind: Kind | None = None
    address: str = ""
    local: dict = field(default_factory=dict)  # its own local.yml (the shared one for the first host)
    problem: str = ""  # why it cannot be used yet: "" for a host that is set up
    # Its files are its, but it lacks a setting it sets for itself: just settings and just vault may still reach it, to
    # put that right.
    incomplete: bool = False

    @property
    def sources(self) -> list[str]:
        """The inventory sources a run against it gets, in their order of precedence: none for the first host, whose
        are ansible.cfg's."""
        return [] if self.first else [CONTROLLER, self.folder]

    @property
    def local_file(self) -> str:
        return SHARED_LOCAL if self.first else f"{self.folder}/group_vars/all/local.yml"

    @property
    def vault_file(self) -> str:
        return SHARED_VAULT if self.first else f"{self.folder}/group_vars/all/vault.yml"

    @property
    def hosts_file(self) -> str:
        return SHARED_HOSTS if self.first else f"{self.folder}/hosts.yml"

    def shown(self) -> dict:
        """What just host shows of it, the kind's settings among it: the values as its own files set them."""
        settings = {name: self.local.get(name) for _, name in self.kind.shown} if self.kind else {}
        return {"name": self.name, "address": self.address, "folder": self.folder, "settings": settings}


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
        """The host in hosts/<name>, with what keeps it from being used, if anything."""
        host = Host(name=name, folder=f"{self.hosts_rel}/{name}", first=False)
        if not NAME.fullmatch(name) or name in RESERVED:
            host.problem = (
                f"{self.hosts_rel}/{name} is no host's folder: a host's name is lowercase letters, digits and -, "
                f"at most 31, and never {', '.join(sorted(RESERVED))}"
            )
            return host
        first = self.first()
        if first is not None and first.name == name:
            host.problem = f"{self.hosts_rel}/{name} has the name of the host in {SHARED_HOSTS}: rename the folder"
            return host
        # A file of its that is not YAML costs this host alone: said in its place, as any other reason it is unusable.
        try:
            data = _yaml(self._path(host.hosts_file))
            local = _yaml(self._path(host.local_file))
        except Refused as refused:
            host.problem = str(refused)
            return host
        if data is None:
            host.problem = f"not set up: just init --host {name}"
            return host
        strays = self._strays(host)
        if strays:
            host.problem = (
                f"{host.folder}/ holds {', '.join(strays)} beside hosts.yml, and Ansible reads every inventory file in "
                f"a host's folder: any host there would be this run's too. Move {'it' if len(strays) == 1 else 'them'} "
                "out of the folder"
            )
            return host
        found = _kind_of(data)
        defined = sorted(_hosts_of(data))
        if found is None or found[1] != [name] or defined != [name]:
            kinds = " or ".join(kind.group for kind in KINDS.values())
            host.problem = (
                f"{host.hosts_file} must define one host, {name}, in the {kinds} group, and no other (it defines "
                f"{', '.join(defined) or 'none'}): any other host there would be this run's too"
            )
            return host
        host.kind = found[0]
        values = _hosts_of(data)[name]
        if not values.get("ansible_host"):
            host.problem = f"{host.hosts_file} gives {name} no ansible_host, its address: just init --host {name}"
            return host
        host.address = str(values["ansible_host"])
        host.local = local if isinstance(local, dict) else {}
        missing = [setting for setting in host.kind.always if host.local.get(setting) in (None, "")]
        if missing:
            host.incomplete = True
            host.problem = (
                f"{host.local_file} does not set {', '.join(missing)}, which each host sets for itself rather than "
                f"take {SHARED_LOCAL}'s: just init --host {name} writes them"
            )
        return host

    def _strays(self, host: Host) -> list[str]:
        """What in a host's folder Ansible would read as inventory beside its hosts.yml: a YAML or JSON file - the
        extensions the yaml plugin takes, the one ansible.cfg enables - or a folder, which it reads through, but for
        group_vars and host_vars."""
        try:
            entries = sorted(os.scandir(self._path(host.folder)), key=lambda entry: entry.name)
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

    # ------------------------------------------------------------------------------------------- choosing ----

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

    def resolve(self, given: str = "", lenient: bool = False) -> Host | None:
        """The host of a run: the one given (--host, or a nested recipe's host=), else the one hosts/current names,
        else the first. None for a checkout with no host at all - before just init - which runs as it always did."""
        if given:
            return self.named(given, "--host names", lenient)
        current = self.current_name()
        if current:
            return self.named(current, f"{self.hosts_rel}/current names", lenient)
        first = self.first()
        if first is None and self.many():
            raise Refused(
                f"{SHARED_HOSTS} names no host, and {self.hosts_rel}/current none of {', '.join(self.folders())}: "
                "choose one with just host <name>"
            )
        return first

    def others_of(self, host: Host | None) -> list[dict]:
        """Every other host of the checkout, as {name, address}: the guards of a run against ``host`` keep them out
        of its VMs' names and aliases, and its destroy from their keys. A host not set up has the address its files
        give, or none."""
        found = []
        for other in self.every():
            if host is not None and other.name == host.name:
                continue
            if not NAME.fullmatch(other.name) and not other.first:
                continue
            found.append({"name": other.name, "address": other.address})
        return found

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

    # ------------------------------------------------------------------------------------------- writing ----

    def switch(self, name: str) -> Host:
        """Make ``name`` the current host: hosts/current written anew and renamed over the old one, never edited in
        place, so a run reading it reads the old name or the new one, whole."""
        host = self.named(name, "just host names")
        self.hosts_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.hosts_dir / f".current.{os.getpid()}"
        with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
            handle.write(f"{host.name}\n")
        os.replace(temporary, self.hosts_dir / "current")
        return host


# What each password script said in this run: run once, as it may ask a person.
_said: dict[str, bytes] = {}


def _password(path: str) -> bytes | None:
    """A password file's password, as Ansible reads one: an executable file is run, its standard input and error left to
    it so that it can ask, and says it on its output. None for a vault client script, which takes a vault ID Ansible
    alone knows, or a script that fails."""
    if not (os.access(path, os.X_OK) and not os.path.isdir(path)):
        return Path(path).read_bytes().strip()
    if Path(path).stem.endswith("-client"):
        return None
    whole = os.path.abspath(path)
    if whole not in _said:
        import subprocess

        try:
            _said[whole] = subprocess.run([whole], stdout=subprocess.PIPE, check=True).stdout.strip(b"\r\n")
        except (OSError, subprocess.SubprocessError):
            return None
    return _said[whole]


def vault_names(path: Path, password_files: list[str]) -> set[str] | None:
    """The names a vault file sets, never its values: decrypted in memory with the first password file that opens it,
    as Ansible opens it, a password script run as Ansible runs it. None where it cannot be read or opened here - a
    vault ID's password, a client script's - which the caller says."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data.startswith(b"$ANSIBLE_VAULT"):
        from ansible.parsing.vault import VaultLib, VaultSecret

        for password_file in password_files:
            try:
                password = _password(password_file)
            except OSError:
                continue
            if password is None:
                continue
            try:
                data = VaultLib([("default", VaultSecret(password))]).decrypt(data)
                break
            except Exception:  # noqa: BLE001 - any refusal: the next file, as Ansible tries them
                continue
        else:
            return None
    try:
        values = _Loader.load(data.decode("utf-8"))
    except Exception:  # noqa: BLE001 - not YAML: Ansible says so
        return None
    return {str(name) for name in values} if isinstance(values, dict) else set()


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
