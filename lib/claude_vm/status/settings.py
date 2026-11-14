"""What just status needs of the settings: asked of Ansible once, and kept until a file they are made from changes.

Ansible's own VariableManager and templar, in this process, as tools/settings asks them, the first time and whenever
inventory/, ansible.cfg, a role's defaults or vars, the filters or ansible-core have changed; never by a second reading
of its precedence here. The answer is kept in .cache/status/settings.json (0600, git-ignored, refused by
tests/check_no_local_files.sh), keyed by the name, size and time of every file it is made from, so a later run starts
without importing Ansible at all.

What is kept holds no secret. The API token is a mark there, and read from the vault at each run; every secret-derived
setting is kept only as whether it is set. A snapshot that would hold anything else the vault holds, but for its two
identifiers, is not kept, and the run's log says which name held it back. A run given extra vars by hand (-e) neither
reads nor writes one: it asks Ansible.

A setting per VM - Ansible applies inventory/host_vars/<name>.yml to the VM of that name - is rendered for that VM and
kept with the rest. Everything else is rendered once for a placeholder host, and its name replaced in what comes back.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from .. import vault, wiring
from ..hosts.kinds import KINDS
from . import model

REPO = Path(__file__).resolve().parents[3]
# The name a VM's settings are rendered under: replaced in what comes back. Unlike any DNS label, so that the label made
# from it (tailscale_hostname) is replaced by the VM's own.
PLACEHOLDER = "status_placeholder.vm"
SECRET_MARK = "@@status-proxmox-token@@"
TOKEN_VAR = KINDS["proxmox"].secret_shared
# Every name the vault may hold the run's token under: the first host's, and each kind's own for a host in hosts/.
TOKEN_VARS = tuple(dict.fromkeys(name for kind in KINDS.values() for name in (kind.secret_shared, kind.secret_host)))
PASSWORD_VAR = "vault_vm_password"
ROLES = ("proxmox_vm", "proxmox_template", "claude_code", "github_projects", "tailscale")

# How the Proxmox API is reached: only asked for with discovery on, since the token behind it is not there without.
API = {
    "url": "{{ proxmox_api_url }}",
    "headers": "{{ proxmox_api_headers }}",
    "validate": "{{ proxmox_validate_certs | bool }}",
    "ca_path": "{{ proxmox_api_ca_path | default('', true) }}",
    "timeout": "{{ status_api_timeout }}",
}
# The run's settings: what the fleet is read with, and the report's own settings, but for the names and sections, which
# each run has of its own.
FLEET = {
    "tag_pattern": "{{ claude_vm_tag_pattern }}",
    "tailscale_tag_pattern": "{{ claude_vm_tailscale_tag_pattern }}",
    # The template new VMs clone: one the user built (proxmox_template_custom), else the one the project builds.
    "template_name": "{{ proxmox_template_base }}",
    "storage": "{{ proxmox_storage }}",
    "nic": "{{ proxmox_nic }}",
    "ssh_config": "{{ claude_ssh_config | bool }}",
    "ssh_config_file": "{{ claude_ssh_config_file }}",
    "ssh_config_legacy": "{{ claude_ssh_config_legacy_file }}",
    "hosts": "{{ claude_vm_inventory_hosts }}",
    "template_wanted": "{{ proxmox_template_wanted }}",
    # No more VMs read at once than Ansible would reach at once.
    "forks": "{{ lookup('ansible.builtin.config', 'DEFAULT_FORKS') }}",
    "settings": {
        "tag": "{{ claude_vm_tag }}",
        # Every tag a VM of the project's carries as its own, its 1.x name too: what the unfinished tag is read under.
        "tags_owned": "{{ claude_vm_tags_owned }}",
        # The API token's user, whom capacity names in how to grant it what reading a node takes.
        "api_user": "{{ proxmox_api_user }}",
        "template_name": "{{ proxmox_template_base }}",
        # Whether that is one the user built, which the project never builds, compares or rebuilds.
        "template_custom": "{{ proxmox_template_custom | default('', true) | length > 0 }}",
        "template_rebuild": "{{ proxmox_template_rebuild | bool }}",
        "storage": "{{ proxmox_storage }}",
        "nic": "{{ proxmox_nic }}",
        # Whether Tailscale is the VPN (a promised key, from before the vpn setting), and the setting itself.
        "tailscale_enabled": "{{ tailscale_wanted | default(tailscale_enabled | default(false)) | bool }}",
        "vpn": "{{ claude_vpn_key | default('tailscale' if tailscale_enabled | default(false) | bool else 'none') }}",
        # The setting that turned Tailscale off, as the role's messages name it: tailscale_enabled, or vpn.
        "tailscale_off": "{{ (tailscale_off_setting.name ~ ' is ' ~ tailscale_off_setting.value)"
        " if tailscale_off_setting is defined else 'vpn is none' }}",
        "vm_user": "{{ vm_user }}",
        "discovery": "{{ claude_vm_discovery | bool }}",
        # The editor just code opens, by its key and its name, for the line under ACCESS.
        "editor": "{{ claude_editor_key }}",
        "editor_name": "{{ ((claude_editors | default({}))[claude_editor_key] | default({})).name"
        " | default(claude_editor_key, true) }}",
        # Every editor's name by its key, in the editors list's order, for the windows a VM has open of each.
        "editor_names": "{{ dict(claude_editors | default({}) | dict2items | map(attribute='key')"
        " | zip(claude_editors | default({}) | dict2items | map(attribute='value.name', default=''))) }}",
        # What ends every just line said of a VM, with more than one host: " --host <name>", as a run's own lines.
        "host_flag": "{{ claude_host_flag }}",
    },
}
# What the run was given on the command line, kept apart: no snapshot holds these.
ASKED = {"names": "{{ claude_vm_names if vm_name is defined else [] }}", "only": "{{ status_only | default('') }}"}

# What is known of one VM: rendered once for the placeholder, its name replaced in what comes back, or for the VM of a
# host_vars file. Never a secret: only whether each is set.
VM = {
    "drift": {
        "cores": "{{ proxmox_vm_cores | int }}",
        "memory": "{{ proxmox_vm_memory_mb | int }}",
        "balloon": "{{ proxmox_vm_memory_min_mb | int }}",
        "onboot": "{{ proxmox_vm_onboot | bool }}",
        "disk": "{{ proxmox_vm_disk_size }}",
        "ciuser": "{{ proxmox_vm_user }}",
        "sshkeys": "{{ proxmox_vm_ssh_public_keys }}",
        "ipconfig0": "{{ proxmox_vm_ipconfig }}",
        "nameserver": "{{ proxmox_vm_nameservers }}",
        "searchdomain": "{{ proxmox_vm_searchdomain }}",
    },
    # The tag of a VM sized when it was created (_wanted in the engine): its cores, memory and disk are no drift.
    "sized_tag": "{{ proxmox_vm_sized_tag }}",
    "claude": {
        "method": "{{ claude_code_install_method }}",
        "api_key": "{{ claude_code_anthropic_api_key | length > 0 }}",
        "remote_control_wanted": "{{ claude_code_remote_control_wanted | bool }}",
        "unit": "{{ claude_code_remote_control_service }}",
        "login_command": "{{ claude_code_login_command }}",
    },
    "github": {
        "user": "{{ github_projects_user }}",
        "auth": "{{ github_projects_auth | bool }}",
        "auth_method": "{{ github_projects_auth_method }}",
        "token_wanted": "{{ github_projects_token_wanted | bool }}",
        "token_set": "{{ github_projects_token_set | bool }}",
        "configure_command": "{{ github_projects_configure_command }}",
        "login_command": "{{ github_projects_login_command }}",
    },
    "tailscale": {
        "wanted": "{{ tailscale_wanted | bool }}",
        "hostname": "{{ tailscale_hostname }}",
        "ssh": "{{ tailscale_ssh | bool }}",
        "accept_routes": "{{ tailscale_accept_routes | bool }}",
        "accept_dns": "{{ tailscale_accept_dns | bool }}",
        "advertise_tags": "{{ tailscale_advertise_tags }}",
        "login_server": "{{ tailscale_login_server }}",
        "auth_method": "{{ tailscale_auth_method }}",
        "key_wanted": "{{ tailscale_key_wanted | bool }}",
        "key_refused_locally": "{{ tailscale_key_refused_locally | bool }}",
        "joined_tag": "{{ tailscale_joined_tag }}",
        "admin_page": "{{ tailscale_admin_page }}",
        "configure_command": "{{ tailscale_configure_command }}",
        "login_command": "{{ tailscale_login_command }}",
    },
    "vpn": "{{ claude_vpn_key | default('none') }}",
    "projects_dir": "{{ vm_projects_dir | default('', true) }}",
    # What the probe (playbooks/module_utils/status_probe.py) is asked for.
    "asked": {
        "user": "{{ vm_user }}",
        "limit": 25,
        "system": {
            "root": "{{ status_system_root }}",
            "upgrades": "{{ status_read_upgrades | bool }}",
            "timeout": 5,
            "editors": "{{ claude_editors | default({}) | editor_servers }}",
        },
        "claude": {"unit": "{{ claude_code_remote_control_service }}"},
        "github": {
            "marker": "{{ github_projects_marker_file }}",
            "marker_1x": "{{ github_projects_state_dir_1x }}/{{ github_projects_marker_file | basename }}",
        },
        "work": {"dir": "{{ vm_projects_dir | default('', true) }}"},
        "tailscale": {
            "wanted": "{{ tailscale_wanted | bool }}",
            "foreign": "{{ tailscale_foreign_paths }}",
            "marker": "{{ tailscale_installed_marker_path }}",
            "marker_1x": "{{ (tailscale_marker_dir_1x ~ '/' ~ (tailscale_installed_marker_path | basename))"
            " if tailscale_marker_dir_1x | length > 0 else '' }}",
            "joined": "{{ tailscale_joined_marker_path }}",
            "joined_1x": "{{ (tailscale_marker_dir_1x ~ '/' ~ (tailscale_joined_marker_path | basename))"
            " if tailscale_marker_dir_1x | length > 0 else '' }}",
        },
    },
}


def _ssh_option(variables: str, option: str) -> str:
    """A setting of Ansible's ssh connection as it resolves one: the inventory's variables, then ansible.cfg or the
    environment (ANSIBLE_PRIVATE_KEY_FILE, ANSIBLE_SSH_COMMON_ARGS and the like), then nothing."""
    found = f"lookup('ansible.builtin.config', '{option}', plugin_type='connection', plugin_name='ssh')"
    for variable in reversed(variables.split()):
        found = f"{variable} | default({found})"
    return "{{ " + found + " | default('', true) }}"


_SSH_MORE = (
    _ssh_option("ansible_ssh_common_args", "ssh_common_args")
    + " "
    + _ssh_option("ansible_ssh_extra_args", "ssh_extra_args")
)
# How a VM is reached. A VM of the listing: as add_host used to put it on the host, over ssh, with the host key held -
# status_ssh_args first, since ssh keeps the first value it is given - and whatever else every host is given: a jump
# host or other options, a port, a key, from the inventory or from Ansible's own configuration and environment.
REACH = {
    "connection": "ssh",
    "user": "{{ vm_user }}",
    "port": _ssh_option("ansible_port ansible_ssh_port", "port"),
    "key": _ssh_option("ansible_ssh_private_key_file ansible_private_key_file status_private_key", "private_key_file"),
    "python": "{{ claude_vm_python }}",
    "ssh_args": "{{ status_ssh_args }} " + _SSH_MORE,
    "ssh_executable": _ssh_option("ansible_ssh_executable", "ssh_executable"),
    "control_dir": "{{ ansible_control_path_dir | default('~/.ansible/cp') }}",
    "become": "{{ ansible_become | default(true) | bool }}",
    "become_exe": "{{ ansible_become_exe | default('sudo') }}",
}
# A host of the inventory, with discovery off: however it says it is reached.
REACH_INVENTORY = {
    **REACH,
    "connection": "{{ ansible_connection | default('ssh') }}",
    "host": "{{ ansible_host | default(inventory_hostname) }}",
    "user": "{{ ansible_user | default('') }}",
    "python": "{{ ansible_python_interpreter | default(claude_vm_python) }}",
    "ssh_args": _ssh_option("ansible_ssh_args", "ssh_args") + " " + _SSH_MORE,
}
# The VM user's password, for a sudo that asks for one.
BECOME = "{{ ansible_become_password | default('') }}"


class Stale(Exception):
    """A snapshot that cannot be used: the run asks Ansible instead."""


# ------------------------------------------------------------------------------------------- the vault, natively ----
# Read by the one reader the hosts' check shares (lib/claude_vm/vault.py): what it cannot open is Stale here, and the
# run asks Ansible instead.


def _vault_open(data: bytes, password: bytes) -> bytes:
    """An ansible-vault AES256 file's text, opened with the standard library and cryptography alone."""
    try:
        return vault.open_text(data, password)
    except vault.Unopened as error:
        raise Stale(str(error)) from error


def _password_files(opts) -> list[str]:
    """The vault's password files, in the order Ansible tries them: the recipe's (.vault_pass), then the one
    ANSIBLE_VAULT_PASSWORD_FILE names. One that ansible.cfg names is Ansible's to read: no snapshot is kept then."""
    return list(vault.password_files(opts.vault_password_file, os.environ))


def _named_by_ansible_cfg() -> list[str]:
    """The password file ansible.cfg names, if it names one: Ansible's to read, looked at to see whether it may ask."""
    config = Path(os.environ.get("ANSIBLE_CONFIG") or REPO / "ansible.cfg")
    try:
        found = re.search(r"(?m)^\s*vault_password_file\s*=\s*(\S.*?)\s*$", config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(config.parent / os.path.expanduser(found.group(1)))] if found else []


def may_ask(opts) -> bool:
    """Whether a password script may ask at the terminal. Nothing is drawn until it has answered, or it would ask under
    the live region; a run with a plain password file, the usual one, draws at once."""
    return any(vault.is_script(path) for path in [*_password_files(opts), *_named_by_ansible_cfg()])


def _password(path: str) -> bytes:
    """A password file's password, as Ansible reads one: an executable file is run, once in a run, and says it. A vault
    client script, which is asked for a vault ID, is left to Ansible."""
    said = vault.password(path)
    if said is None:
        raise Stale("a vault password client script" if vault.is_client(path) else "the vault's password script failed")
    return said


def _vault_files(opts) -> list[str]:
    """The vault files the run loads, in their order: the shared one, then a host's own in hosts/ (tools/check host)."""
    given = opts.vault_file
    return [given] if isinstance(given, str) and given else [one for one in given or [] if one]


def _vault_values(opts) -> dict:
    """What the vaults hold, merged as Ansible merges them: a later file's value of a name over an earlier one's."""
    try:
        return vault.values(_vault_files(opts), _password_files(opts))
    except vault.Unopened as error:
        raise Stale(str(error)) from error


# ---------------------------------------------------------------------------------------------------- Ansible ----


class Ansible:
    """The settings as a play sees them, without a play: the inventory, the vault, the roles' defaults and vars."""

    def __init__(self, opts, say=None):
        from ansible import constants as C
        from ansible import context
        from ansible.cli import CLI
        from ansible.inventory.manager import InventoryManager
        from ansible.parsing.dataloader import DataLoader
        from ansible.parsing.vault import VaultSecret
        from ansible.playbook.play import Play
        from ansible.plugins.loader import init_plugin_loader
        from ansible.template import Templar, trust_as_template
        from ansible.utils.display import Display
        from ansible.vars.hostvars import HostVars
        from ansible.vars.manager import VariableManager

        if say:
            # What Ansible warns of while it reads goes through the live region, or it would land inside the frame.
            Display().display = say
        init_plugin_loader([])
        extra = list(opts.extra)
        if getattr(opts, "host_extra", ""):
            extra.append(opts.host_extra)
        if opts.names:
            extra.append(json.dumps({"vm_name": opts.names}))
        if opts.only:
            extra.append(json.dumps({"status_only": opts.only}))
        context._init_global_context(argparse.Namespace(extra_vars=tuple(extra)))
        self.Templar, self.trust, self.Play = Templar, trust_as_template, Play
        self.loader = DataLoader()
        self.loader.set_basedir(str(REPO / "playbooks"))
        # What a password script already said is given to Ansible, which would run it again and have it ask again.
        scripts = [path for path in _password_files(opts) if vault.is_script(path) and not vault.is_client(path)]
        said = [(C.DEFAULT_VAULT_IDENTITY, VaultSecret(_password(path))) for path in scripts]
        named = C.DEFAULT_VAULT_PASSWORD_FILE
        if named and os.path.abspath(named) in map(os.path.abspath, scripts):
            C.DEFAULT_VAULT_PASSWORD_FILE = None
        # The other password files ansible-playbook would take: the recipe's, then ANSIBLE_VAULT_PASSWORD_FILE's or
        # ansible.cfg's, and the vault IDs configured. Never a prompt of Ansible's own.
        self.loader.set_vault_secrets(
            said
            + CLI.setup_vault_secrets(
                self.loader,
                vault_ids=list(C.DEFAULT_VAULT_IDENTITY_LIST),
                vault_password_files=[opts.vault_password_file]
                if opts.vault_password_file and opts.vault_password_file not in scripts
                else [],
                auto_prompt=False,
            )
        )
        self.private_key = opts.private_key
        self.inventory = InventoryManager(loader=self.loader, sources=opts.inventory or C.DEFAULT_HOST_LIST)
        # The extra vars come from the global context, set above, as VariableManager reads them.
        self.variables = VariableManager(loader=self.loader, inventory=self.inventory)
        # Each host as a play sees it, its group_vars and host_vars too: made, it hands itself to the VariableManager.
        HostVars(inventory=self.inventory, variable_manager=self.variables, loader=self.loader)
        self.host_names = {host.name for host in self.inventory.get_hosts("all")}
        self.inventory.add_group("status_vms")

    def _play(self, hosts, roles):
        return self.Play.load(
            {"name": "status", "hosts": hosts, "gather_facts": False, "roles": list(roles)},
            variable_manager=self.variables,
            loader=self.loader,
        )

    def trusted(self, value):
        """A value this engine supplies, trusted as a template as a playbook's own text is."""
        if isinstance(value, str):
            return self.trust(value)
        if isinstance(value, dict):
            return {key: self.trusted(one) for key, one in value.items()}
        if isinstance(value, list):
            return [self.trusted(one) for one in value]
        return value

    def render(self, value, variables):
        return self.Templar(loader=self.loader, variables=variables).template(self.trusted(value))

    def local(self):
        """Every variable a task on this machine sees."""
        play = self._play("localhost", ["proxmox_template"])
        host = self.inventory.get_host("localhost") or self.inventory.localhost
        return self.variables.get_vars(play=play, host=host)

    def on_host(self, name, inventory_host=False):
        """Every variable a task of a VM sees, the roles' own included. The placeholder and a VM of the listing are
        put in status's own group; a host of the inventory is taken as the inventory has it."""
        if not inventory_host:
            self.inventory.add_host(name, group="status_vms")
        host = self.inventory.get_host(name)
        play = self._play("status_vms" if not inventory_host else "claude_vms", ROLES)
        variables = dict(self.variables.get_vars(play=play, host=host))
        variables.update(
            ansible_check_mode=True,
            # The marker's path under the VM user's home, which the probe joins to it.
            github_projects_home_dir=".",
        )
        if self.private_key:
            # --private-key: under the inventory's own, over ansible.cfg's and the environment's, as Ansible ranks it.
            variables["status_private_key"] = self.private_key
        return variables


# ------------------------------------------------------------------------------------------------- the snapshot ----


def _inventory_files(source: Path):
    """An inventory source and what Ansible reads beside it."""
    folder = source if source.is_dir() else source.parent
    yield from [source] if source.is_file() else folder.glob("*")
    yield from (folder / "group_vars").rglob("*")
    yield from (folder / "host_vars").rglob("*")


def _ansible_core():
    """The installed ansible-core's metadata, whose path names its version: found, never imported."""
    import importlib.util

    spec = importlib.util.find_spec("ansible")
    for folder in (spec.submodule_search_locations or []) if spec else []:
        yield from Path(folder).parent.glob("ansible_core-*.dist-info/METADATA")


def _collections():
    """The pins, and the manifest of every collection installed where the recipe points Ansible: a pull that bumps a
    pin, or a just install, gives another key, and the next run checks them again."""
    yield REPO / "requirements.yml"
    where = os.environ.get("ANSIBLE_COLLECTIONS_PATH") or os.environ.get("ANSIBLE_COLLECTIONS_PATHS", "")
    for folder in where.split(os.pathsep):
        if folder:
            yield from Path(os.path.expanduser(folder)).glob("ansible_collections/*/*/MANIFEST.json")


def _digest(opts) -> str:
    """Every file the settings are made from, by name, size and time, and what else they read - the filters and the
    package they are rendered through, ansible-core itself and the collections: a change to any gives another key, and
    the settings are asked of Ansible again."""
    digest = hashlib.sha256()
    ssh = Path.home() / ".ssh"
    sources = opts.inventory or [str(REPO / "inventory")]
    groups = [
        [REPO / "ansible.cfg", *map(Path, _password_files(opts))],
        *(_inventory_files(Path(source)) for source in sources),
        REPO.glob("roles/*/defaults/main.yml"),
        REPO.glob("roles/*/vars/main.yml"),
        REPO.glob("filter_plugins/*.py"),
        (REPO / "lib" / "claude_vm").rglob("*.py"),
        _ansible_core(),
        _collections(),
        ssh.glob("*.pub"),
    ]
    # The sources' order is their precedence: said as given, where the files below are sorted.
    digest.update(f"sources={json.dumps(sources)}\n".encode())
    # The other hosts, which the lines said name the run's host by.
    digest.update(f"host_extra={getattr(opts, 'host_extra', '')}\n".encode())
    for path in sorted(str(path) for group in groups for path in group):
        try:
            info = os.stat(path)
        except OSError:
            continue
        if not os.path.isdir(path):
            digest.update(f"{path} {info.st_size} {info.st_mtime_ns}\n".encode())
    seen = ("HOME", "REQUESTS_CA_BUNDLE", "PATH")
    seen += tuple(sorted(k for k in os.environ if k.startswith("ANSIBLE_") and k != "ANSIBLE_LOG_PATH"))
    for name in seen:
        digest.update(f"{name}={os.environ.get(name, '')}\n".encode())
    digest.update(f"--private-key={opts.private_key}\n".encode())
    return digest.hexdigest()


def write_json(path, value) -> None:
    """Replaced whole, only this machine's user may read it. A file that cannot be written is left unwritten."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}")
        with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
            handle.write(value if isinstance(value, str) else json.dumps(value))
        os.replace(temporary, path)
    except OSError:
        pass


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _usable(opts):
    """Whether this run may read or write a snapshot: no -e, and a folder to keep it in."""
    return bool(opts.cache and _vault_files(opts) and not opts.extra)


def _from_snapshot(opts) -> dict:
    kept = read_json(Path(opts.cache) / "settings.json")
    if not isinstance(kept, dict) or kept.get("key") != _digest(opts):
        raise Stale("no snapshot for these files")
    config = kept["config"]
    values = _vault_values(opts)
    # The vault's name for this host's token, as the snapshot was kept: a host in hosts/ has its own (kinds.py).
    token = kept.get("token", TOKEN_VAR)
    if token not in TOKEN_VARS:
        raise Stale("no token of a host's kind")
    secret = str(values[token]).strip()
    config["fleet"]["headers"] = {k: v.replace(SECRET_MARK, secret) for k, v in config["fleet"]["headers"].items()}
    config["run"] = {"names": opts.names.split(",") if opts.names else [], "only": opts.only}
    config["password"] = lambda name="": str(values.get(PASSWORD_VAR) or "")
    return config


def _keep(opts, config, secret, password, say=None) -> None:
    """The settings, without the token, unless anything else the vault holds would be written: the run's log says
    which of the vault's names held them back, never what it holds."""
    try:
        values = _vault_values(opts)
    except (OSError, KeyError, ValueError, Stale):
        return
    # Which of the vault's names the run's token is: the first host's, or a host's own in hosts/. Neither, and the
    # token is no vault value read as it is: nothing is kept.
    token = next((name for name in TOKEN_VARS if secret and str(values.get(name, "")).strip() == secret), "")
    if not token or str(values.get(PASSWORD_VAR) or "") != password:
        return
    kept = {key: value for key, value in config.items() if key not in ("run", "password")}
    # Looked for as JSON writes each: a quote or a backslash in one is escaped there.
    text = json.dumps({"key": _digest(opts), "token": token, "config": kept}, default=str)
    text = text.replace(json.dumps(secret)[1:-1], SECRET_MARK)
    held = sorted(
        str(name)
        for name, value in values.items()
        if isinstance(value, str) and len(value.strip()) >= 6 and json.dumps(value.strip())[1:-1] in text
    )
    if held:
        # Read only now: the roles' specs, for a run that found something, and just settings' own rule.
        specs, (_, feeds) = wiring.specs(), wiring.wiring()
        held = [name for name in held if not wiring.identifier(name, feeds, specs)]
    if held:
        if say:
            say(
                f"just status: the settings are not kept for the next run, since what the vault's {', '.join(held)}"
                " holds is in them; each run asks Ansible for them",
                log_only=True,
            )
        return
    write_json(Path(opts.cache) / "settings.json", text)


# ----------------------------------------------------------------------------------------------------- loading ----


def _collections_pinned() -> None:
    """tools/check collections, which every recipe that runs Ansible runs first: Ansible resolves the roles'
    module_defaults groups from the collections installed. Run here, where Ansible is asked, and not by the recipe,
    where it would hold back the outline: a snapshot is keyed by requirements.yml and the collections installed, so
    one that holds was made where this passed."""
    import subprocess

    done = subprocess.run(
        [str(REPO / "tools" / "check"), "collections", str(Path(sys.executable).parent)],
        cwd=REPO,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        said = done.stderr.strip().removeprefix("error: ")
        raise RuntimeError(said or "the Galaxy collections could not be checked. Run: just install")


def _from_ansible(opts, say=None) -> dict:
    """The settings, as Ansible gives them, once the collections are the ones pinned."""
    _collections_pinned()
    ansible = Ansible(opts, say)
    local = ansible.local()
    fleet = ansible.render(FLEET, local)
    secret = ""
    if fleet["settings"]["discovery"]:
        fleet.update(ansible.render(API, local))
        fleet["validate"], fleet["timeout"] = bool(fleet["validate"]), float(fleet["timeout"])
        secret = str(ansible.render("{{ proxmox_api_token_secret }}", local))
    run = ansible.render(ASKED, local)
    config = {"fleet": fleet, "run": run}
    placeholder = ansible.on_host(PLACEHOLDER)
    config["vm"] = ansible.render(VM, placeholder)
    config["reach"] = ansible.render(REACH, placeholder)
    password = str(ansible.render(BECOME, placeholder))
    # The VMs whose sudo password is not every VM's. Never kept: a run with one asks Ansible.
    own = {}
    config["password"] = lambda name="": own.get(name, password)
    # A VM with a host_vars file of its own: rendered as itself, and reached as it says.
    config["per_vm"] = {}
    host_vars = _host_vars_folder(opts)
    if host_vars is not None:
        for path in sorted(host_vars.iterdir()):
            name = path.stem if path.is_file() else path.name
            if name not in ansible.host_names and name != "localhost":
                variables = ansible.on_host(name)
                vm, how = ansible.render(VM, variables), ansible.render(REACH, variables)
                config["per_vm"][name] = {"vm": vm, "reach": how}
                if str(ansible.render(BECOME, variables)) != password:
                    own[name] = str(ansible.render(BECOME, variables))
    # With discovery off, the inventory's claude_vms, each reached as the inventory says.
    config["inventory"] = {}
    if not fleet["settings"]["discovery"]:
        for name in sorted(ansible.inventory.get_groups_dict().get("claude_vms", [])):
            variables = ansible.on_host(name, inventory_host=True)
            config["inventory"][name] = {
                "vm": ansible.render(VM, variables),
                "reach": ansible.render(REACH_INVENTORY, variables),
            }
            if str(ansible.render(BECOME, variables)) != password:
                own[name] = str(ansible.render(BECOME, variables))
    elif _usable(opts) and own:
        if say:
            say(
                "just status: the settings are not kept for the next run, since a VM has a sudo password of its own;"
                " each run asks Ansible for them",
                log_only=True,
            )
    elif _usable(opts):
        _keep(opts, config, secret, password, say)
    return config


def _host_vars_folder(opts):
    for source in opts.inventory or [str(REPO / "inventory")]:
        folder = Path(source)
        folder = folder if folder.is_dir() else folder.parent
        if (folder / "host_vars").is_dir():
            return folder / "host_vars"
    return None


def load(opts, say=None) -> dict:
    """{"fleet", "vm", "reach", "per_vm", "inventory", "run", "password"}: a snapshot, where one holds, or Ansible."""
    if _usable(opts):
        try:
            return _from_snapshot(opts)
        except (KeyError, OSError, TypeError, ValueError, Stale):
            pass
    return _from_ansible(opts, say)


def dns_label(name: str) -> str:
    """A VM's name made one DNS label, as tailscale_hostname makes it."""
    label = re.sub(r"[^a-z0-9-]+", "-", name.lower())
    return re.sub(r"-+$", "", re.sub(r"^-+|-+$", "", label)[:63])


def _named(value, name: str, label: str):
    if isinstance(value, str):
        return value.replace(PLACEHOLDER, name).replace(dns_label(PLACEHOLDER), label)
    if isinstance(value, dict):
        return {key: _named(one, name, label) for key, one in value.items()}
    if isinstance(value, list):
        return [_named(one, name, label) for one in value]
    return value


def for_vm(config: dict, name: str) -> dict:
    """What is known of the VM called ``name``."""
    if name in config["per_vm"]:
        return config["per_vm"][name]["vm"]
    return _named(config["vm"], name, dns_label(name))


def reach_for(config: dict, name: str) -> dict:
    """How the VM called ``name`` is reached, but for its address."""
    if name in config["per_vm"]:
        return config["per_vm"][name]["reach"]
    return config["reach"]


def sections(only) -> list[str]:
    return model.status_sections(only or None)
