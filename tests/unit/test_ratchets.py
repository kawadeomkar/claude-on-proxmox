"""What one editor or one hypervisor is to the rest of the code, held to what it is now: never more.

The code is being made to take more than VS Code and more than Proxmox (docs/architecture/decisions.md §15.4). Each
editor's own files and each hypervisor's own roles may name it as they like; anywhere else, a mention is one more
place a second editor or hypervisor has to be threaded through. So each table below is how many lines name it in each
file outside its own, today. A file whose count grows, or a file new to the table, fails; one whose count falls fails
too, until its number here is lowered to match, so the table tightens by itself. Raising a number is a decision, made
in the commit that needs it and said in its message: the editors' rows and their registry name each editor once by
design, and a record that carries a VM's ID names it where the record is.
"""

from __future__ import annotations

import re
from pathlib import Path

from harness import repo_texts

REPO = Path(__file__).resolve().parents[2]
# The code: what runs, and what decides how it runs. Not the docs, the tests or the scenarios, which describe it.
CODE = [
    "playbooks",
    "roles",
    "lib",
    "filter_plugins",
    "callback_plugins",
    "tools",
    "justfile",
    "deploy.yml",
    "inventory",
]


def _lines_naming(pattern: str, own: re.Pattern, kept: str = "") -> dict[str, int]:
    """Each file of the code outside `own` that has lines matching pattern, and how many; a line's names that 1.x
    promises (kept), which can never go, are taken out of it before it is matched."""
    found = {}
    for file, text in repo_texts(*CODE):
        path = str(file.relative_to(REPO))
        if own.search(path) or "/molecule/" in path:
            continue
        count = sum(
            1 for line in text.splitlines() if re.search(pattern, re.sub(kept, "", line) if kept else line, re.I)
        )
        if count:
            found[path] = count
    return found


def _held(found: dict[str, int], allowed: dict[str, int], what: str) -> None:
    grown = {path: (count, allowed.get(path, 0)) for path, count in found.items() if count > allowed.get(path, 0)}
    fallen = {path: (found.get(path, 0), count) for path, count in allowed.items() if found.get(path, 0) < count}
    assert grown == {}, f"more lines name {what} than before, (now, allowed): {grown}"
    assert fallen == {}, f"fewer lines name {what} than the table allows, so lower it, (now, allowed): {fallen}"


# VS Code's own: its role, its check file of just code's and its variables, and its family's module. Elsewhere
# a line counts that names it or one of its own terms - its CLI's, its workspace files', its task runner's - once the
# names 1.x promises are taken out of it: the vscode_* settings, the vscode_server role and its variables, its
# files on every VM, the vscode_windows key of just status --json.
VSCODE_OWN = re.compile(
    r"^(roles/vscode_server/|playbooks/tasks/vscode_[a-z_]+\.yml$|playbooks/vars/vscode\.yml$|lib/claude_vm/editors/vscode\.py$)"
)
VSCODE_ELSEWHERE = {
    "inventory/group_vars/all/defaults.yml": 7,
    "inventory/group_vars/all/local.yml.example": 7,
    "justfile": 2,
    "lib/claude_vm/editors/model.py": 1,
    "lib/claude_vm/editors/registry.py": 5,
    "lib/claude_vm/editors/zed.py": 1,
    "lib/claude_vm/run/recipes.py": 1,
    "playbooks/code.yml": 14,
    "playbooks/configure.yml": 1,
    "playbooks/destroy.yml": 1,
    "playbooks/files/claude-terminal.sh": 2,
    "playbooks/files/session-notes.sh": 3,
    "playbooks/module_utils/status_probe.py": 1,
    "playbooks/tasks/session_read.yml": 1,
    "playbooks/tasks/ssh_config.yml": 1,
    "playbooks/templates/vm_session.md.j2": 6,
    "playbooks/vars/controller_writes.yml": 9,
    "roles/github_projects/vars/main.yml": 1,
}


VSCODE_TERMS = r"vscode|vs code|\.code-workspace|ssh-remote\+|code --remote|\bcode -r\b|tasks: run task|remote - ssh"
VSCODE_PROMISED = (
    r"vscode_(claude_terminal|workspaces_dir|session_notes|claude_sessions|remote|user_settings_file|windows)\b"
    r"|vscode_server\w*|[0-9]{2}-vscode-server\.conf"
)


def test_vs_code_is_named_outside_its_own_files_no_more_than_it_is_now() -> None:
    _held(_lines_naming(VSCODE_TERMS, VSCODE_OWN, VSCODE_PROMISED), VSCODE_ELSEWHERE, "VS Code")


# Proxmox's own: its two roles, its filters and the script just init runs on the host as root. Anywhere else, a line
# that names its API's settings, its collection or its raw cluster listing reads Proxmox directly - the reads a
# second hypervisor would have to be put behind (docs/architecture/decisions.md §15.4).
PROXMOX_OWN = re.compile(
    r"^(roles/proxmox_vm/|roles/proxmox_template/|filter_plugins/proxmox\.py$|lib/claude_vm/proxmox\.py$|tools/proxmox-access\.sh$)"
)
PROXMOX_ELSEWHERE = {
    "deploy.yml": 2,
    # The token's rule names its two vault names (a host's own, from 2.5: docs/architecture/custom-code.md §7.14).
    "inventory/group_vars/all/defaults.yml": 32,
    "inventory/group_vars/all/local.yml.example": 4,
    "inventory/group_vars/all/vault.yml.example": 2,
    "lib/claude_vm/ansible/engine.py": 4,
    "lib/claude_vm/mcp/confirm.py": 4,
    "lib/claude_vm/refs.py": 3,
    "lib/claude_vm/run/model.py": 4,
    # The kinds of host, as literal data, each naming its own settings once by design: the resolver, just host and just
    # status read them there. With them, the internals made from the run's host that no settings file may set.
    "lib/claude_vm/hosts/kinds.py": 10,
    "lib/claude_vm/status/engine.py": 16,
    "lib/claude_vm/status/model.py": 18,
    "lib/claude_vm/status/settings.py": 5,
    "lib/claude_vm/tui/run.py": 3,
    "lib/claude_vm/tui/status.py": 3,
    "lib/claude_vm/wiring.py": 1,
    "playbooks/code.yml": 2,
    "playbooks/configure.yml": 1,
    "playbooks/destroy.yml": 14,
    "playbooks/discover.yml": 2,
    "playbooks/init_check.yml": 37,
    "playbooks/init_settings.yml": 12,
    "playbooks/provision.yml": 26,
    "playbooks/ssh_config.yml": 2,
    "playbooks/tailscale_login.yml": 1,
    "playbooks/tasks/cluster_vms.yml": 17,
    "playbooks/tasks/init_choose.yml": 2,
    "playbooks/tasks/init_local_setting.yml": 1,
    "playbooks/tasks/vm_addresses.yml": 11,
    "playbooks/tasks/vm_marks.yml": 6,
    "playbooks/tasks/vpn_members.yml": 2,
    "playbooks/tasks/vm_stop.yml": 4,
    # The token secret's vault name for the first host, the default where just init sets up no other.
    "playbooks/vars/init.yml": 1,
    "playbooks/templates/vm_session.md.j2": 2,
    # The examples just init --host copies for another host: the settings it sets for itself, and its token.
    "tools/templates/host/group_vars/all/local.yml.example": 3,
    "tools/templates/host/group_vars/all/vault.yml.example": 3,
}


# Its API's settings, its collection, its raw listing and the paths of its API, and the VMID, Proxmox's own ID of a
# VM, which the fleet's records are to carry as a provider's id (plan §7.1).
PROXMOX_TERMS = r"proxmox_api_|community\.proxmox|claude_vm_all|proxmox_vm_id|/cluster/|/qemu/|\bvmid\b"


def test_proxmox_is_read_outside_its_own_files_no_more_than_it_is_now() -> None:
    _held(_lines_naming(PROXMOX_TERMS, PROXMOX_OWN), PROXMOX_ELSEWHERE, "Proxmox's API")


# Zed's own: its two task files of just code's, and its family's module in the core. Elsewhere, a line that names it.
ZED_OWN = re.compile(r"^(playbooks/tasks/zed_[a-z_]+\.yml|lib/claude_vm/editors/zed\.py)$")
ZED_ELSEWHERE = {
    "inventory/group_vars/all/defaults.yml": 9,
    "inventory/group_vars/all/local.yml.example": 3,
    "justfile": 1,
    "lib/claude_vm/editors/model.py": 1,
    "lib/claude_vm/editors/registry.py": 4,
    "lib/claude_vm/run/recipes.py": 1,
    "playbooks/code.yml": 2,
    "playbooks/tasks/session_read.yml": 2,
    "roles/vscode_server/meta/argument_specs.yml": 1,
    "roles/vscode_server/tasks/main.yml": 1,
}


def test_zed_is_named_outside_its_own_files_no_more_than_it_is_now() -> None:
    _held(_lines_naming(r"\bzed\b|zeditor", ZED_OWN), ZED_ELSEWHERE, "Zed")
