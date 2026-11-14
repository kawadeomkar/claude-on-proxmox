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
        return Host(
            name=names[0],
            folder=SHARED,
            first=True,
            kind=kind,
            address=str(values.get("ansible_host") or names[0]),
            local=local if isinstance(local, dict) else {},
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
        data = _yaml(self._path(host.hosts_file))
        if data is None:
            host.problem = f"not set up: just init --host {name}"
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
        local = _yaml(self._path(host.local_file))
        host.local = local if isinstance(local, dict) else {}
        missing = [setting for setting in host.kind.always if host.local.get(setting) in (None, "")]
        if missing:
            host.problem = (
                f"{host.local_file} does not set {', '.join(missing)}, which each host sets for itself rather than "
                f"take {SHARED_LOCAL}'s: just init --host {name} writes them"
            )
        return host

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

    def named(self, name: str, why: str) -> Host:
        """The host called ``name``, set up; Refused otherwise, ``why`` saying where the name came from."""
        first = self.first()
        if first is not None and name == first.name:
            return first
        if name in self.folders():
            host = self.other(name)
            if host.problem:
                raise Refused(f"{why} {name}, which cannot be used: {host.problem}")
            return host
        known = [host.name for host in self.every()]
        raise Refused(
            f"{why} {name}, which is no host of this checkout's ({', '.join(known) or 'none'}): "
            f"just init --host {name} sets one up"
        )

    def resolve(self, given: str = "") -> Host | None:
        """The host of a run: the one given (--host, or a nested recipe's host=), else the one hosts/current names,
        else the first. None for a checkout with no host at all - before just init - which runs as it always did."""
        if given:
            return self.named(given, "--host names")
        current = self.current_name()
        if current:
            return self.named(current, f"{self.hosts_rel}/current names")
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

    # ------------------------------------------------------------------------------------------- the rules ----

    def never_inherited(self, host: Host) -> list[str]:
        """The lines a host must add before a run: each setting of its kind that is one network's or one machine's,
        which the shared local.yml sets and the host's does not."""
        if host.first or host.kind is None:
            return []
        shared = _yaml(self.checkout / SHARED_LOCAL)
        shared = shared if isinstance(shared, dict) else {}
        return [name for name in host.kind.never_inherited if name in shared and name not in host.local]

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


def vault_names(path: Path, password_files: list[str]) -> set[str] | None:
    """The names a vault file sets, never its values: decrypted in memory with the first password file that opens it,
    as Ansible opens it. None where it cannot be read or opened here - Ansible says why when the run reads it."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data.startswith(b"$ANSIBLE_VAULT"):
        from ansible.parsing.vault import VaultLib, VaultSecret

        for password_file in password_files:
            try:
                password = Path(password_file).read_bytes().strip()
            except OSError:
                continue
            if os.access(password_file, os.X_OK):
                return None  # a script, which may ask: left to Ansible
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
