"""What the person confirms before an agent deletes a VM or changes a setting, and which settings an agent may change.

Two gates, unequal (verified on 2.1.309 to 2.1.319): requiresUserInteraction's Yes/No prompt, which Claude Code shows
before every call whatever the permission rules say, which no hook of the calling project's can answer, and which -p
and dontAsk deny; and the server's own dialog, a form elicitation, which shows what is at stake but which a project's
Elicitation hook can answer. So the prompt is the human gate, and the dialog shows the person what they approve and
wants a code that changes with what is asked: a generic hook that accepts every dialog fails to match, and nothing
runs. A hook written to read the code would pass - but a project that ships one could run just destroy from a hook
directly, so the code guards against accidents, not against the project.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from claude_vm.status.model import work_said
from claude_vm.text import one_line

# A code the person can read and type: no 0/O or 1/I/L.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_KEY = secrets.token_bytes(32)


def code(*asked: str) -> str:
    """Four characters, the same for the same question for the life of this server - the SDK may ask a question again
    on each round of a call, and matches the answer to the question it asked - and unguessable from it."""
    digest = hmac.new(_KEY, "\0".join(asked).encode(), hashlib.sha256).digest()
    return "".join(ALPHABET[b % len(ALPHABET)] for b in digest[:4])


def destroy_message(names: list[str], report: dict, typed: str, host: str = "") -> str:
    """The deletion's dialog: what goes and what to type - first, since Claude Code shows a dialog's first lines and
    folds the rest - then a line per VM from just status's report, each value a VM supplied made one line, and what of
    its work exists nowhere else. With more than one host, the host it goes from."""
    on = f" on the Proxmox host {host}" if host else " on Proxmox"
    lines = [
        f"This deletes {', '.join(names)} and their disks{on}. It cannot be undone.",
        f"To delete, type: {typed}",
    ]
    by_name = {vm.get("name"): vm for vm in report.get("vms") or [] if isinstance(vm, dict)}
    for name in names:
        vm = by_name.get(name)
        if vm is None:
            lines.append(f"- {name}: not found among this project's VMs")
            continue
        gib = 1024**3
        size = ", ".join(
            part
            for part in (
                f"{vm['vcpus']} cores" if vm.get("vcpus") else "",
                f"{(vm.get('maxmem') or 0) / gib:.0f} GiB memory" if vm.get("maxmem") else "",
                f"{(vm.get('maxdisk') or 0) / gib:.0f} GiB disk" if vm.get("maxdisk") else "",
            )
            if part
        )
        said = f"- {name}: VM {vm.get('vmid')} on {one_line(vm.get('node'), 40)}, {one_line(vm.get('status'), 20)}"
        lines.append(said + (f", {size}" if size else ""))
        work = vm.get("work") if isinstance(vm.get("work"), dict) else {}
        for repository in work.get("repositories") or []:
            left = work_said(repository)
            if left:
                lines.append(f"    {one_line(left, 160)}, on this VM alone")
    lines.append(
        "It first saves each VM's own Claude Code sessions here, then signs it out of Claude, GitHub and Tailscale."
    )
    return "\n".join(lines)


def setting_message(name: str, current: str, new: str, typed: str, where: str = "local.yml") -> str:
    """The setting's dialog: the setting, the file it is written in, its value there now and the one it would have, and
    what to type - all in the first lines, which Claude Code shows unfolded."""
    return (
        f"This changes {name} in {where}, for every run from now on: now {one_line(current, 120) or '(its default)'},"
        f" new {one_line(new, 120) or '(its default)'}.\n"
        f"To change it, type: {typed}"
    )


# The settings an agent may change, after the person confirms: each changes what a run does, but none where a
# credential goes, what runs on a VM as root, what reaches this machine, or who reaches the VMs.
ALLOWED = {
    # The VMs' size, and the name a deploy without names creates.
    "vm_cores": "the VMs' size",
    "vm_memory_mb": "the VMs' size",
    "vm_memory_min_mb": "the VMs' size",
    "vm_disk_size": "the VMs' size",
    "vm_default_name": "the VM a deploy without names creates",
    # The editor.
    "editor": "the editor just code opens",
    "editor_claude_terminal": "the editor's Claude terminal",
    "editor_session_notes": "the session notes",
    # Which of the user's own repositories are cloned.
    "github_projects": "which repositories are cloned",
    "github_projects_include_forks": "which repositories are cloned",
    "github_projects_include_archived": "which repositories are cloned",
    "github_projects_exclude": "which repositories are cloned",
    # A run's switches.
    "deploy_sign_in": "whether a deploy signs in",
    "deploy_code": "whether a deploy opens the editor",
    "destroy_release": "whether a destroy signs the VM out first",
    "destroy_save_sessions": "whether a destroy saves the VM's sessions first",
    "claude_sessions_sync": "whether just code copies this machine's sessions",
    # The sign-ins' timing and starts.
    "claude_code_login_approval_timeout": "how long a sign-in waits",
    "github_login_auto_start": "whether GitHub's sign-in starts by itself",
    "tailscale_login_auto_start": "whether Tailscale's sign-in starts by itself",
    # The VMs' tools, from the project's own sources.
    "dev_tools_install_docker": "a tool on the VMs",
    "dev_tools_install_gh": "a tool on the VMs",
    "dev_tools_install_node": "a tool on the VMs",
    "dev_tools_install_rust": "a tool on the VMs",
    "dev_tools_install_uv": "a tool on the VMs",
    "dev_tools_node_major": "a tool on the VMs",
    "claude_code_version": "Claude Code's version on the VMs",
    "claude_code_remote_control": "Remote Control on the VMs",
}

# The settings of local.yml.example an agent may not change, each a decision: where the credentials go and how they
# are checked (the Proxmox API, GitHub, Tailscale), what reaches this machine (the SSH aliases'
# options and identity, where sessions are saved, the pages that open here), who and what reaches the VMs (keys, the
# user, the network, the VPN), and what the VMs' agent may do. Any role variable that is in no example is refused too.
REFUSED = frozenset(
    {
        "claude_code_login_args",
        "claude_code_login_timeout",
        "claude_code_remote_control_dir",
        "claude_code_remote_control_name",
        "claude_code_remote_control_permission_mode",
        "claude_code_remote_control_spawn",
        "claude_code_settings",
        "claude_ssh_config",
        "claude_ssh_config_include",
        "claude_ssh_forward_agent",
        "claude_ssh_host_domain",
        "claude_ssh_identity_file",
        "claude_ssh_options",
        "destroy_sessions_dir",
        "editor_files_dir",
        "git_user_email",
        "git_user_name",
        "github_auth",
        "github_auth_method",
        "github_login_scopes",
        "github_projects_clone_protocol",
        "login_desktop",
        "proxmox_api_port",
        "proxmox_api_token_id",
        "proxmox_api_user",
        "proxmox_bridge",
        "proxmox_nic",
        "proxmox_node",
        "proxmox_storage",
        "proxmox_template_custom",
        "proxmox_template_golden",
        "proxmox_template_name",
        "proxmox_template_rebuild",
        "proxmox_template_vmid",
        "proxmox_validate_certs",
        "tailscale_accept_dns",
        "tailscale_accept_routes",
        "tailscale_advertise_tags",
        "tailscale_api_auth_method",
        "tailscale_auth_method",
        "tailscale_daemon_flags",
        "tailscale_enabled",
        "tailscale_hostname",
        "tailscale_login_server",
        "tailscale_login_timeout",
        "tailscale_ssh",
        "tailscale_up_args",
        "tailscale_upload_logs",
        "vm_extra_packages",
        "vm_ipconfig",
        "vm_nameservers",
        "vm_projects_dir",
        "vm_searchdomain",
        "vm_ssh_public_keys",
        "vm_user",
        "vpn",
        "vscode_claude_sessions",
        "vscode_claude_terminal",
        "vscode_remote",
        "vscode_session_notes",
        "vscode_user_settings_file",
        "vscode_workspaces_dir",
    }
)


# What an allowed setting's value may be where its type lets more through: with npm as the install method,
# claude_code_version is the package's version for npm, which installs any spec given there - another package
# (npm:<name>), a repository, a tarball's address - as root on every VM.
VALUES = {
    "claude_code_version": (
        re.compile(r"stable|latest|[0-9]+\.[0-9]+\.[0-9]+"),
        "stable, latest or a version as 2.1.319",
    ),
}


def setting_refusal(name: str) -> str:
    if name in ALLOWED:
        return ""
    return (
        f"{name} is not a setting an agent may change: where credentials go, what runs on the VMs or reaches this "
        f"machine, and who reaches the VMs stay the user's. Ask the user to run it at a terminal: "
        f"just settings {name} --value=<value>"
    )


def value_refusal(name: str, value: str) -> str:
    """Why value is not one an agent may give the setting, or nothing - never the value said back, which may be a
    secret typed in the wrong place."""
    pattern, said = VALUES.get(name, (None, ""))
    if not value or pattern is None or pattern.fullmatch(value):
        return ""
    return (
        f"{name} takes {said} from an agent: any other value is a package npm installs as root on every VM. Ask the "
        f"user to run it at a terminal: just settings {name} --value=<value>"
    )
