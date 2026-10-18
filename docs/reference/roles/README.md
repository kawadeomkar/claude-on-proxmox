# Roles

One role per concern. `make deploy` builds the template with `proxmox_template` over root SSH when it is
missing, creates and starts the VMs with `proxmox_vm` through the Proxmox API, then runs the rest on each
VM, in the order `playbooks/configure.yml` lists them. Each role's page is generated from the role's
files: every variable it takes, from its `meta/argument_specs.yml`; its internal values, from
`vars/main.yml`; and every task, with a flowchart of each task file. Many of the variables you set
through a shorter name in `local.yml`, which the [settings reference](../settings.md) maps to the role
variable it feeds.

| Role | Purpose | Key variables |
|------|---------|---------------|
| [`common`](common.md) | apt upgrade, baseline packages, login user with passwordless sudo and SSH keys, sshd hardening (key-only login), timezone, qemu-guest-agent, `~/.local/bin` on PATH | `common_packages`, `common_extra_packages`, `common_timezone`, `common_harden_ssh` |
| [`vscode_server`](vscode_server.md) | What VS Code's Remote - SSH extension needs on the host: an sshd drop-in that keeps the port forward the VS Code Server is reached through enabled, plus keepalives, checked against what sshd actually runs with; and a higher inotify limit for the file watcher. Off removes both files | `vscode_remote`, `vscode_server_client_alive_interval`, `vscode_server_inotify_watches` |
| [`dev_tools`](dev_tools.md) | Node.js (NodeSource), Docker Engine, GitHub CLI, uv/uvx (release pinned by version and SHA256), pipx, build tools. Apt signing keys are vendored in `roles/dev_tools/files` | `dev_tools_install_*`, `dev_tools_node_major`, `dev_tools_uv_version`, `dev_tools_npm_global_packages` |
| [`claude_code`](claude_code.md) | Claude Code via the official installer (or npm), `~/.claude/settings.json` merged with your settings and API key, optional global `CLAUDE.md`, and optionally a Remote Control server so the session is reachable from claude.ai and the mobile app | `claude_code_version`, `claude_code_install_method`, `claude_code_settings`, `claude_code_global_instructions`, `claude_code_remote_control` |
| [`github_projects`](github_projects.md) | Signs `gh` in to GitHub - from `github_token`, or by a device-flow login you make once - and makes it git's credential helper; lists your repositories with a custom `github_repos` module (pagination, fork/archive/empty-repo filters, private ones with a login) and clones them; writes a git identity where none is set | `github_projects` (names; empty = all), `github_projects_include_forks`, `github_projects_exclude`, `github_auth`, `git_user_name`, `git_user_email`, `github_projects_clone_protocol` |
| [`tailscale`](tailscale.md) | Tailscale from its apt repository (signing key vendored in `roles/tailscale/files`); joins the tailnet with `vault_tailscale_auth_key` on a VM's first join, or by the web login `make tailscale-login` drives; keeps the hostname, Tailscale SSH, routes and DNS settings of its own login as configured with `tailscale set`; reports the VM's tailnet, name and address. Off logs out and removes what it installed; a Tailscale installed by hand is only reported | `tailscale_enabled`, `vault_tailscale_auth_key`, `tailscale_ssh`, `tailscale_advertise_tags`, `tailscale_accept_dns`, `tailscale_upload_logs` |
| [`proxmox_vm`](proxmox_vm.md) | Looks the VM up **by name**, clones the template when it does not exist, applies cloud-init (user, keys, DHCP by default, DNS), resizes the disk, starts it, then waits for the guest agent to report an address. `proxmox_vm_state: absent` deletes it. Either way it refuses an existing VM of that name that is a template or is not tagged `claude-on-proxmox`, rather than provision over it or delete it | `vm_cores`, `vm_memory_mb`, `vm_disk_size`, `vm_nameservers` (fleet-wide, in `local.yml`); `proxmox_vm_ipconfig` for a fixed address, also fleet-wide, so single-VM runs only |
| [`proxmox_template`](proxmox_template.md) | Downloads the Ubuntu cloud image (SHA256 verified), creates the VM with `qm`, imports the disk, adds the cloud-init drive, converts to template | `proxmox_template_vmid`, `proxmox_template_image_url`, `proxmox_template_storage` |

Every role documents its full interface in `roles/<name>/meta/argument_specs.yml`, and Ansible validates
role arguments against it at runtime. How each role works inside is in
[Architecture § 5](../../architecture/roles.md).
