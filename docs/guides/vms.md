# VMs and fleets

## Naming and multiple VMs

`VM_NAME` is the name the VM gets in Proxmox, and it is what you see in the Proxmox UI. Pass several,
comma-separated, to build a fleet in one run:

```bash
make deploy VM_NAME=alpha,beta,gamma
make configure VM_NAME=alpha       # later, just one of them
make destroy VM_NAME=beta
```

Given, `VM_NAME` narrows every target to those names. Left out, what it means depends on the target:

| Target | Without `VM_NAME` |
|--------|-------------------|
| `make deploy`, `make provision`, `make destroy` | `claude-on-proxmox-default` |
| `make configure`, `make check`, `make list`, `make claude-login`, `make github-login`, `make tailscale-login`, `make ssh-config` | every VM this project created (tagged `claude-on-proxmox`) |
| `make code` | refused: it opens one VM, so it needs one name; `PROJECT` optionally narrows it to one project |

So a bare `make deploy` creates and configures `claude-on-proxmox-default` and leaves any other VMs
alone, while a bare `make configure` re-applies the configuration to all of them.

You never assign a VMID or an IP address. Proxmox picks the next free VMID; the DHCP server that
already serves your network assigns the address, exactly as it would for a laptop; and the QEMU
guest agent - baked into the template by `make template` - reports that address back so Ansible can
reach the VM and print it for you. The only address you ever type is your Proxmox host's.

## Memory

Proxmox counts a VM's memory as used unless the VM reports it free, so the disk cache Linux keeps in idle
memory reads as used: a VM just deployed shows close to its whole allocation in the Proxmox UI while its
programs use about half a gigabyte. The cache gives way the moment a program needs the memory.

What the host can take back depends on the VM's minimum memory. By default a VM keeps all of
`vm_memory_mb`. With `vm_memory_min_mb` below it in `local.yml`, Proxmox's ballooning takes the difference
back - the VM's cache first - once the host's memory use passes its ballooning target, 80% unless whoever
runs the host changed it, and gives it back as that use falls. The target is a setting of the host, which
this project never changes. Keep the minimum above what the VM runs at its busiest: past it, a VM with no
swap has programs killed. Like the rest of a VM's hardware, it is applied when the VM is created
([Re-running](#re-running)).

Each VM also has less to hold. Its template is built from Ubuntu's minimal cloud image, `common` masks the
services a headless VM has no use for (`common_masked_units`: fwupd, multipathd, ModemManager, udisks2,
and snapd while no snap is installed), and apt deletes each package it downloads once it is installed
(`common_apt_keep_downloads`).

## Re-running

All playbooks are idempotent. `make configure` can be re-run at any time to pick up variable changes, and
`make check` shows what a run would change without touching the VM. Existing checkouts are never pulled
unless `github_projects_update_existing: true`. Hardware and cloud-init settings are applied when the VM is
created; set `proxmox_vm_force_update: true` to re-apply them. That re-applies the settings to every VM the
run covers, replaces each one's tag list with the project's (a tag you added in the Proxmox UI is dropped),
and starts each one, shut down or not — so narrow it with `VM_NAME`. Otherwise a VM you have shut down stays
shut down when you re-run `make deploy` or `make provision`: it is reported as stopped and skipped, and the
other VMs carry on. Start it in the Proxmox UI first, or pass `ANSIBLE_ARGS="-e proxmox_vm_start_existing=true"`
to have the run start it. Two things are deliberately not reconciled on every run: `apt upgrade` (new distro packages
show up as changes) and the Claude Code binary, which updates itself on the `stable`/`latest` channels.

## Re-running one part of it

`TAGS` narrows a run to one role, which is much faster than the whole thing when you have changed a single
variable. The tags are `common`, `vscode_server`, `dev_tools`, `claude_code`, `github_projects` and `tailscale`; discovery
always runs, so the VMs are still found, and so does the step at the end that refreshes each VM's SSH alias. `TAGS` works with `make configure` and `make check`; `make deploy` refuses it,
because building the template and creating VMs carry no tags and a tagged deploy would do nothing.

```bash
make configure VM_NAME=alpha TAGS=claude_code
```

Pushing an API key to a VM that was built without one is the common case: see
[An API key instead of a claude.ai login](remote-control.md#an-api-key-instead-of-a-claudeai-login).

## See also

- Settings: [VMs](../reference/settings.md#vms) and [Proxmox](../reference/settings.md#proxmox)
- Roles: [`proxmox_vm`](../reference/roles/proxmox_vm.md) and
  [`proxmox_template`](../reference/roles/proxmox_template.md)
- How it works: names, VMIDs, addresses and the tag in
  [§3](../architecture/README.md#3-the-identity-model), provisioning in
  [§4.2](../architecture/lifecycle.md#42-playbooksprovisionyml--three-plays), what a re-run repairs in
  [§8](../architecture/convergence.md)
