"""Ansible's filters of lib/claude_vm/proxmox.py and checks.py (docs/architecture/custom-code.md §7.1): an adapter,
which imports the core only when a filter is called (§7.10)."""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)

PROXMOX = (
    "qm_config",
    "proxmox_template_record",
    "proxmox_template_drift",
    "proxmox_vm_drift",
    "proxmox_disk_bytes",
    "ssh_config_blocks",
    "ssh_config_prune",
    "ssh_config_elsewhere",
    "vms_not_named",
    "built_template",
    "duplicate_names",
    "proxmox_tags",
    "net_mac",
    "tailnet_ipv4",
    "guest_addresses",
    "proxmox_access_denied",
    "proxmox_marks",
    "proxmox_tags_moved",
    "proxmox_notes_repos",
    "proxmox_notes_with_repos",
    "golden_record",
    "golden_read",
    "build_record",
    "build_read",
    "golden_base",
    "golden_pick",
    "golden_collectable",
    "build_leftovers",
    "golden_reads",
    "golden_choice",
    "golden_targets",
    "build_vms",
    "golden_templates",
    "build_vm_ours",
    "read_configs",
    "not_templates",
    "template_recorded",
    "os_release_debian",
    "custom_template_problems",
)
CHECKS = ("ssh_alias_refusal", "ssh_alias_refusals", "ssh_hostname", "guest_ipv4", "sshd_match_spec")


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        return plugin.filters(_LIB, "proxmox", PROXMOX) | plugin.filters(_LIB, "checks", CHECKS)
