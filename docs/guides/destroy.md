# Tearing down

```bash
make destroy                      # claude-on-proxmox-default
make destroy VM_NAME=alpha,beta   # several at once; they are deleted in parallel
make destroy alpha beta           # the same, the names after the target
```

The names after the target are `VM_NAME` by another spelling, as
[Naming and multiple VMs](vms.md#naming-and-multiple-vms) says.

It prompts for confirmation, then releases each VM from what it signed in to, then stops it and deletes it
with its disks, a disk that still carries its VMID but that its configuration no longer lists included.
Without a terminal to prompt on the answer is "no", so nothing is deleted. Every name must be a VM this
project created: a name that does not exist, or a VM without the `claude-on-proxmox` tag, is refused
before anything is deleted, and the error lists the VMs it did create. So is a name two VMs share, which
Proxmox allows: rename or remove one first. A template is never deleted. For a VM created before this
project tagged its VMs, see
[Adopting VMs created before tagging](upgrading.md#adopting-vms-created-before-tagging).

**Releasing a VM** happens on the VM, over SSH, since what it signed in with is on its disk; a stopped VM is
started for it (and stopped again should the run then fail to delete it), and the VMs are released at once,
as they are deleted:

| Service | What `make destroy` does first | What is left |
|---|---|---|
| Claude | stops Remote Control and the unit that starts it, then `claude auth logout` | the session stays in the Claude app's list, offline: Anthropic has no way to revoke a Claude Code login from outside |
| GitHub | `gh auth logout` | the token, valid until you revoke it. Revoking the one this project's sign-in made means revoking GitHub CLI under [Settings → Applications](https://github.com/settings/applications), which signs out every `gh` you have, this machine's included, so `make destroy` does not do it. `vault_github_token`, which every VM shares, is never revoked |
| Tailscale | logs the node out, then deletes its device with the API credential in the vault (see [Removing devices for you](tailscale.md#removing-devices-for-you)) | nothing with a credential; without one, the device, logged out, until you delete it |

Each step is best effort: one that fails is said, and the VM is deleted anyway, as you asked. The run ends
with one line per VM saying what it was released from and what is left of it - and for a VM that could not
be reached (stopped and not starting, or not answering over SSH), what to remove by hand and where.
`destroy_release: false` deletes the VMs as they are, and says the same.

## See also

- Settings: [Sign-ins and teardown](../reference/settings.md#sign-ins-and-teardown) for
  `destroy_release`, and [Secrets: Tailscale](../reference/settings.md#secrets-tailscale) for the API
  credential that deletes a device
- Roles: [`proxmox_vm`](../reference/roles/proxmox_vm.md), which deletes the VM, and the releases of
  [`claude_code`](../reference/roles/claude_code.md),
  [`github_projects`](../reference/roles/github_projects.md) and
  [`tailscale`](../reference/roles/tailscale.md)
- How it works: [§4.5](../architecture/lifecycle.md#45-playbooksdestroyyml--confirm-release-delete)
