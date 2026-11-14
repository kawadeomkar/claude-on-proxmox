"""The kinds of host a checkout's hosts can be, as literal data: for each, the inventory group its hosts.yml fills, the
settings that are each host's own, the vault names of its secret, what just init --host asks and what just host shows.
The resolver, just host, just init and just settings read this table and name no kind's setting themselves: a second
kind is a row here and its roles' entry points (docs/architecture/custom-code.md §7.14). Stdlib only."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from ..records import Checked

SETTING = r"[a-z][a-z0-9_]*"


@dataclass(frozen=True, slots=True, kw_only=True)
class Kind(Checked):
    """One kind of host."""

    key: str
    name: str  # as the person reads it
    group: str  # the inventory group a host of this kind is in, and the one its hosts.yml fills
    # Settings a host of this kind always sets in its own local.yml - just init --host writes each, equal to the shared
    # value or not - and the resolver refuses a host without: each describes that one machine, and the shared one's
    # would otherwise fall through to it.
    always: tuple[str, ...]
    # Settings of one network or one machine: where the shared files set one and a host's do not, the run is refused,
    # naming the line to add - a shared static address on a second network is the silent case it closes.
    never_inherited: tuple[str, ...]
    # The vault's name for the secret a run reaches the host with: the first host's, in the shared vault, and every
    # other host's, in its own. Each is refused in the other's file.
    secret_shared: str
    secret_host: str
    # What just host shows of each host beside its address: a column's heading and the setting it shows (always's).
    shown: tuple[tuple[str, str], ...]


KINDS: Final[Mapping[str, Kind]] = {
    "proxmox": Kind(
        key="proxmox",
        name="Proxmox VE",
        group="proxmox",
        always=(
            "proxmox_node",
            "proxmox_storage",
            "proxmox_bridge",
            "proxmox_api_user",
            "proxmox_api_token_id",
            "proxmox_api_port",
            "proxmox_validate_certs",
            "proxmox_template_vmid",
        ),
        never_inherited=("vm_ipconfig", "vm_nameservers", "proxmox_template_custom", "claude_ssh_host_domain"),
        secret_shared="vault_proxmox_api_token_secret",
        secret_host="vault_host_proxmox_api_token_secret",
        shown=(("NODE", "proxmox_node"),),
    ),
}
