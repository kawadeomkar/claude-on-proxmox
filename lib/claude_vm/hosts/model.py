"""The hosts' records, the layout's names for its files, and a refusal (docs/architecture/custom-code.md §7.14). Host
and Choice are the core's own: a Host holds its whole local.yml, which no output carries. What a command prints is
built from a record made for it: HostList for just host --json, which leaves through wire.to_wire, and HostRead for
every, a private pipe to just status's parent. Stdlib only."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from ..records import Checked
from .kinds import Kind

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


@dataclass(frozen=True, slots=True, kw_only=True)
class Note(Checked):
    """What a run says on standard error beside its answer: a warning, or a plain note."""

    level: Literal["warning", "note"]
    text: str


class Refused(Exception):
    """What the core will not do, said as the recipe's error; with the notes said before it, in their order."""

    def __init__(self, said: str, notes: tuple[Note, ...] = ()):
        super().__init__(said)
        self.notes = notes


def name_refusal(name: str) -> str:
    """Why ``name`` can be no host's name, or "": the rule every --host, folder and hosts/current is held to."""
    if NAME.fullmatch(name) and name not in RESERVED:
        return ""
    return f"a host's name is lowercase letters, digits and -, at most 31, and never {', '.join(sorted(RESERVED))}"


@dataclass(frozen=True, slots=True, kw_only=True)
class Host(Checked):
    """One of the checkout's hosts, as its files say it is."""

    name: str
    folder: str  # inventory, or hosts/<name>: where its files are, relative to the checkout or absolute
    first: bool  # the host in inventory/, whose files are the shared ones
    kind: Kind | None = None
    address: str = ""
    local: dict = field(default_factory=dict)  # its own local.yml as YAML read it (the shared one for the first host)
    problem: str = ""  # why it cannot be used yet: "" for a host that is set up
    # Its files are its, but it lacks a setting it sets for itself: just settings and just vault may still reach it, to
    # put that right.
    incomplete: bool = False

    @property
    def sources(self) -> tuple[str, ...]:
        """The inventory sources a run against it gets, in their order of precedence: none for the first host, whose
        are ansible.cfg's."""
        return () if self.first else (CONTROLLER, self.folder)

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
        return {name: self.local.get(name) for _, name in self.kind.shown} if self.kind else {}


@dataclass(frozen=True, slots=True, kw_only=True)
class OtherHost(Checked):
    """Another host of the checkout, as a run against one is handed it: its name and address keep it out of the run's
    VM names and aliases, and its known_hosts key out of a deletion's reach."""

    name: str
    address: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Request(Checked):
    """What a command asks of the hosts, from its options: the host given, a nested recipe's, the VMs named."""

    host: str = ""  # --host
    nested: str = ""  # the host of the recipe this one runs inside
    names: tuple[str, ...] = ()  # the VMs named, hyphenated as they are taken in
    lenient: bool = False  # just settings and just vault: a host that lacks a setting it sets for itself is taken
    read_only: bool = False  # just status: nothing is written, hosts/.checked.json included
    vault_file: str = DEFAULT_VAULT  # the justfile's VAULT_FILE
    settings_files: str = DEFAULT_SETTINGS  # the justfile's SETTINGS_FILES, as given
    vault_pass: str = ""  # the recipe's password file

    def __post_init__(self) -> None:
        # Proxmox takes no underscore in a VM's name, so the justfile takes my_vm as my-vm: so does every caller here.
        if type(self.names) is tuple:
            object.__setattr__(self, "names", tuple(name.replace("_", "-") for name in self.names))
        Checked.__post_init__(self)


@dataclass(frozen=True, slots=True, kw_only=True)
class Choice(Checked):
    """Which host a run is against, and why: no vault opened, nothing checked yet."""

    host: Host | None  # None with one host and nothing asked: nothing is read for it
    many: bool  # the checkout has more than one host
    routed: bool  # the VMs named are on it, as their aliases record, and no host was given
    current: str  # the current host's name, where the run was routed: said where they differ
    origins: tuple[tuple[str, str], ...] = ()  # each VM named that an alias places, and its host
    unplaced: tuple[str, ...] = ()  # the VMs named that no alias places, with more than one host
    notes: tuple[Note, ...] = ()  # said as soon as the host is chosen, before any check


@dataclass(frozen=True, slots=True, kw_only=True)
class VaultCheck(Checked):
    """The vaults' names held to the token rule: decided here, kept in hosts/.checked.json by tools/host."""

    key: str  # the vault files as they are now, which a check that passed is kept under
    refusal: str = ""
    unopened: tuple[str, ...] = ()  # the vaults nothing here opens: said, and left to Ansible
    keep: bool = False  # passed with every vault opened, and not kept already: tools/host may keep it


@dataclass(frozen=True, slots=True, kw_only=True)
class RunHost(Checked):
    """A run's host once every check has passed: what the recipe evals."""

    choice: Choice
    settings_files: tuple[str, ...]
    vault_files: tuple[str, ...]
    sources: tuple[str, ...]  # a host in hosts/: each a -i of the run's
    others: tuple[OtherHost, ...] = ()
    notes: tuple[Note, ...] = ()  # said once the host is chosen, in their order: the routing note last
    check: VaultCheck | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class HostRead(Checked):
    """How just status reads one host: every's row, printed bare for its parent, one child per host."""

    name: str
    current: bool
    sources: tuple[str, ...]
    vault_files: tuple[str, ...]
    others: tuple[OtherHost, ...]
    problem: str  # why it cannot be read, said in its own block
    notes: tuple[Note, ...] = ()  # said on standard error, never in the row


@dataclass(frozen=True, slots=True, kw_only=True)
class HostRow(Checked):
    """One host as just host --json shows it: a row of HostList, whose keys a release only adds to. No property: each
    would be a key."""

    name: str
    address: str
    folder: str
    settings: dict  # the kind's shown settings, as its own files set them
    host_kind: str | None  # a key of KINDS: kind is the document's alone
    first: bool
    current: bool  # current is a boolean wherever it appears; the current host's name is HostList's current_host
    problem: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Where(Checked):
    """Which host a run would reach, from the names and the --host it would be given: a Choice's public face, read with
    no vault opened. A preview, never a reservation: a person confirming a host passes it to the run as --host. No
    property: each would be a key."""

    host: str  # the host the run would be against; "" with one host, which nothing names
    routed: bool  # the VMs named are on it, as their aliases record, and no host was given
    unplaced: tuple[str, ...]  # the VMs named that no alias places, with more than one host
    notes: tuple[str, ...]  # what the run would warn of as soon as its host is chosen
    refusal: str  # why the run would be refused before anything starts, its host unchosen; "" for none


@dataclass(frozen=True, slots=True, kw_only=True)
class HostList(Checked):
    """just host --json: the hosts, from their files alone, no vault opened. Leaves through wire.to_wire as host_list,
    the class's name: renaming it renames the document. No property: each would be a key."""

    current_host: str | None
    hosts: tuple[HostRow, ...]
    problem: str  # what keeps the hosts from being read: "" where nothing does, as a row's problem is
    where: Where | None = None  # asked for with --for or --host: which host that run would reach


@dataclass(frozen=True, slots=True, kw_only=True)
class NewFile(Checked):
    """A file just init --host makes for a host, which tools/host writes O_EXCL, readable by its owner alone."""

    path: str  # where it is written
    said: str  # as a run names it
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class InitPlan(Checked):
    """What just init does for a host: the files it lacks, made by tools/host, and what its playbooks are pointed at."""

    first: bool  # the first host's: the shared files, which the recipe copies from their examples
    payload: dict  # init_host_json: the files, the vault's name for its token, what it sets for itself
    files: tuple[NewFile, ...] = ()
    sources: tuple[str, ...] = ()
    settings_files: tuple[str, ...] = ()
    vault_file: str = ""
