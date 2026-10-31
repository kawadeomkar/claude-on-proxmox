# VMs and fleets

## Naming and multiple VMs

The names after a recipe are the names the VMs get in Proxmox, and what you see in the Proxmox UI. Give
several to build a fleet in one run:

```bash
just deploy alpha beta gamma
just deploy alpha --only configure   # later, just one of them
just destroy beta
```

`deploy`, `login`, `status` and `destroy` take any number of names, `code` exactly one; comma-separated in
one word (`alpha,beta`) is the same. A name is 1-63 letters, digits, dots and hyphens, starting and ending with a
letter or a digit, as a host name is: Proxmox refuses an underscore. A word holding anything but letters, digits
and `. - ,`, or not starting with a letter or a digit, is refused before anything runs; `proxmox_vm` refuses
the rest. `deploy` also asks
about a name that is one of its stages, as in `just deploy configure`, which could mean the stage or a VM
to create: it runs whichever you say, and without a terminal to ask at runs nothing. A stage
is given as `--only configure`, and beside `--only` or `--check` such a word is a VM's name
(`just deploy --only provision configure` creates a VM named `configure`). A recipe's name is a VM name
like any other: `just deploy test` deploys the VM `test`. Options go after the recipe's name, before or after the names.

Given, the names narrow every recipe to those VMs. Left out, what it means depends on what the
command does - what creates or deletes VMs acts on the default one, and what reads them or applies the
configuration again reaches them all:

| Recipe | Without names |
|--------|-------------------|
| `just deploy`, `just deploy --only provision`, `just destroy` | `claude-on-proxmox-default` |
| `just deploy --only configure`, `--check` and `--only ssh-config` (and `--tags` with them), `just login`, `just status` | every VM this project created (tagged `claude-on-proxmox`) |
| `just deploy --only template` | no VM: it builds the template, and refuses names |
| `just code` | refused: it opens one VM, so it needs one name; `--project` optionally narrows it to one project |

So a bare `just deploy` creates and configures `claude-on-proxmox-default` and leaves any other VMs
alone, while a bare `just deploy --only configure` re-applies the configuration to all of them.

You never assign a VMID or an IP address. Proxmox picks the next free VMID; the DHCP server that
already serves your network assigns the address, exactly as it would for a laptop; and the QEMU
guest agent - baked into the template by `just deploy --only template` - reports that address back so Ansible can
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

## What just status shows

`just status` reads every VM this project created, or the ones you name, and changes nothing: not the
VMs, not Proxmox, not this machine. At a terminal the whole report is on screen within a second and fills
in as each value is read; piped, or in `just logs`, only the finished report appears.

- **VMS** - each VM with its uptime, vCPUs, CPU now and over the last 24 hours (since it was made: a VMID
  used again keeps the old VM's history in Proxmox, which is left out), memory in use (as the VM counts
  it where it was read over SSH; Proxmox's own figure counts the disk cache as used, so without SSH it
  reads higher), with the minimum the host may take it down to (`vm_memory_min_mb`), its root disk as the
  guest agent reports it, and when it was made - its disk's age, since a clone keeps the template's
  creation time; the other VMs on the cluster as one line of totals, never by name.
- **STORAGE** - the storage your VMs' disks are on, how full it is and this project's share of it, and
  the template and when it was built, or that the next `just deploy` rebuilds it: it differs from what its
  settings would build, each difference said under ATTENTION.
- **ACCESS** - each VM's LAN address, whether it has its SSH alias, and its tailnet name and address.
- **SIGN-INS** - Claude Code's version and whether it is signed in, Remote Control, gh's GitHub account,
  and Tailscale.
- **SYSTEM** - the OS, the kernel running and a newer one waiting for a reboot, the load, and the
  upgrades waiting, security ones counted.
- **WORK** - in the projects directory (`vm_projects_dir`, else `~/projects`), how many checkouts and
  extra worktrees, and what of each is on no remote or not committed - commits on branches no remote has,
  commits on a detached HEAD, stashes, changes in a worktree - as of each checkout's last fetch, with the
  claude processes and VS Code windows open. It is shown, never counted: a branch kept local is often on
  purpose.
- **ATTENTION** - what needs you, most pressing first, each with the command that deals with it: what
  could not be read (Proxmox did not answer or refused the token, a name that matches no VM, a node that
  is down, a running VM whose agent gives no address, a VM that did not answer SSH or whose host key this
  machine does not hold), then problems (two VMs of one name, a disk 90% full, Remote Control down,
  `tailscaled` not running), then things to do (settings in `local.yml` that differ from the VM, a change
  in Proxmox waiting for a restart, a first deploy that did not finish, a missing alias, a disk 80% full,
  a reboot waiting, security upgrades, a sign-in to make - in the words `just login` uses).

It reads each VM over SSH as the VM user, with the host key this machine already holds: a VM rebuilt or
renamed since is "not read", and status never takes its new key - `just deploy <vm> --only configure`
does. It never runs `claude` there, so a stored login shows as signed in, without its plan.

It exits 0 when nothing is wrong (things to do included), 1 for a problem, 2 when something could not be
read, and 3 when the run itself failed. `--only` takes `vms`, `storage`, `access`, `sign-ins`, `system` and
`work`, comma-separated; `--only vms,storage` asks Proxmox alone and never connects to a VM, the quick form for a
scheduled check. `--json` prints the report as JSON, with the same values the text shows, for a script.

## Re-running

All playbooks are idempotent. `just deploy --only configure` can be re-run at any time to pick up variable changes, and
`just deploy --check` shows what a run would change without touching the VM. Existing checkouts are never pulled
unless `github_projects_update_existing: true`. Hardware and cloud-init settings are applied when the VM is
created; set `proxmox_vm_force_update: true` to re-apply them. That re-applies the settings to every VM the
run covers, replaces each one's tag list with the project's (a tag you added in the Proxmox UI is dropped),
and starts each one, shut down or not — so narrow it with names. Otherwise a VM you have shut down stays
shut down when you re-run `just deploy` or `just deploy --only provision`: it is reported as stopped and skipped, and the
other VMs carry on. Start it in the Proxmox UI first, or give `--start`
to have the run start it (`just deploy alpha --start`). Two things are deliberately not reconciled on every run: `apt upgrade` (new distro packages
show up as changes) and the Claude Code binary, which updates itself on the `stable`/`latest` channels.

## Re-running one part of it

`just deploy` runs each of its stages on its own with `--only`: `template`, `provision`, `configure` or
`ssh-config` (the SSH aliases alone), and `--check` is a dry run of configure (`--check --diff`). The ones
that create nothing - `configure`, `--check` and `ssh-config` - never prompt and, without a name, reach
every VM.

`--tags` narrows configuration to one role, or several comma-separated, which is much faster than the
whole thing when you have changed a single variable. The tags are `common`, `vscode_server`, `dev_tools`,
`claude_code`, `github_projects` and `tailscale`, and anything else is refused; discovery always runs, so
the VMs are still found, and so does the step at the end that refreshes each VM's SSH alias. Beside VM
names `--tags` means `--only configure`, and `--check` takes it too; without names it is refused, since a
bare `just deploy` means the default VM and configuration without names means every VM, so the fleet
takes `--only configure` said out loud. The other stages carry no tags, so `--only template` or
`--only provision` with `--tags` is refused rather than run doing nothing.

```bash
just deploy alpha --tags claude_code                  # one role, on alpha
just deploy --only configure --tags claude_code       # the same role, on every VM
just deploy --check --tags tailscale                  # what that role would change, on every VM
```

Pushing an API key to a VM that was built without one is the common case: see
[An API key instead of a claude.ai login](remote-control.md#an-api-key-instead-of-a-claudeai-login).

## See also

- Settings: [VMs](../reference/settings.md#vms) and [Proxmox](../reference/settings.md#proxmox)
- Roles: [`proxmox_vm`](../reference/roles/proxmox_vm.md) and
  [`proxmox_template`](../reference/roles/proxmox_template.md)
- How it works: names, VMIDs, addresses and the tag in
  [§3](../architecture/README.md#3-the-identity-model), provisioning in
  [§4.2](../architecture/lifecycle.md#42-playbooksprovisionyml--three-plays), `just status` in
  [§4.6](../architecture/lifecycle.md#46-playbooksstatusyml--read-only-drawn-live), what a re-run repairs in
  [§8](../architecture/convergence.md)
