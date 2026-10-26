# Tearing down

```bash
just destroy                      # claude-on-proxmox-default
just destroy alpha beta           # several at once; they are deleted in parallel
```

The names are given as [Naming and multiple VMs](vms.md#naming-and-multiple-vms) says.

It prompts for confirmation, then saves here the Claude Code sessions that exist only on each VM, releases
each VM from what it signed in to, then stops it and deletes it with its disks, a disk that still carries
its VMID but that its configuration no longer lists included.
Without a terminal to prompt on the answer is "no", so nothing is deleted. Every name must be a VM this
project created: a name that does not exist, or a VM without the `claude-on-proxmox` tag, is refused
before anything is deleted, and the error lists the VMs it did create. So is a name two VMs share, which
Proxmox allows: rename or remove one first. A template is never deleted. For a VM created before this
project tagged its VMs, see
[Adopting VMs created before tagging](upgrading.md#adopting-vms-created-before-tagging).

## Its Claude Code sessions

Claude Code keeps a session on the disk of the machine it runs on, the only copy it can resume, and its
auto memory too, so deleting a VM would end them for good. Before anything else, `just destroy` copies to this
machine what of them exists only on the VM:

- the sessions begun there, Remote Control's included;
- the sessions `just code` copied there that you continued;
- the folder beside each, with its subagents and tool results;
- each project's auto memory.

A copy `just code` made that nobody continued is left: this machine has that session. Only
`~/.claude/projects/` is saved: auto memory kept elsewhere through `autoMemoryDirectory`, and subagents'
memory in `~/.claude/agent-memory/`, go with the VM. Remote Control stops while they are copied, so the
session it serves arrives whole, and starts again on a VM that is then kept. The VM user's own files
need no sudo, so a VM whose sudo now asks for a password is still saved - and then deleted without being
released, which the run says.

They go into `.sessions/<vm>-<YYYYMMDD-HHMMSS>/` in this checkout (`destroy_sessions_dir`, an absolute
path or one starting with `~` when you set it), which git ignores, laid out as the VM's
`~/.claude/projects/` and changed in nothing, readable by you alone. What arrives is what the VM sends,
so nothing of it is trusted: only plain files and folders are written, under the names Claude Code gives
its own, never a link or anything outside that folder, and the run counts what it left out. The run's
line for the VM says how many it saved and where, and the `sessions.json` there names each session with
its directory, branch, last write and the Claude Code version that wrote it - resuming it needs one at
least as new here - and the command that continues it, quoted where its path needs that. Run that command
in a checkout of its repository:

```bash
cd ~/demo
claude --resume /Users/you/claude-on-proxmox/.sessions/alpha-20261023-153000/projects/-home-dev-projects-demo/0a608ad8-1b2c-4d3e-8f90-123456789abc.jsonl
```

Given a transcript's path, Claude Code resumes it where it lies and goes on writing to that file. The
prompt it recorded names the VM's memory folder; that memory is saved beside it, in
`projects/<folder>/memory/`. The transcripts hold whatever their tools printed, secrets included, and
stay until you delete them. They are the VM's writing, saved as it sent them: what one says, and what
resuming it here asks to run, deserves the trust you give the VM, no more.

The save is not best effort. A save that fails - this machine's disk full, the folder not writable, the
connection lost part way - keeps that VM, neither released nor deleted, and the run fails saying why; its
line at the end, and its kept SSH alias, say it was kept for that, its deletion never tried.
`destroy_save_sessions: false` deletes the sessions with the VM. A VM that could not be reached is deleted
as before, its sessions with it, and the run says so.

## Releasing a VM

Releasing a VM happens on the VM, over SSH, since what it signed in with is on its disk; a stopped VM is
started for it (and stopped again should the run then fail to delete it), and the VMs are released at once,
as they are deleted:

| Service | What `just destroy` does first | What is left |
|---|---|---|
| Claude | stops Remote Control and the unit that starts it, then `claude auth logout` | the session stays in the Claude app's list, offline: Anthropic has no way to revoke a Claude Code login from outside |
| GitHub | `gh auth logout` | the token, valid until you revoke it. Revoking the one this project's sign-in made means revoking GitHub CLI under [Settings → Applications](https://github.com/settings/applications), which signs out every `gh` you have, this machine's included, so `just destroy` does not do it. `vault_github_token`, which every VM shares, is never revoked |
| Tailscale | logs the node out, then deletes its device with the API credential in the vault (see [Removing devices for you](tailscale.md#removing-devices-for-you)) | nothing with a credential; without one, the device, logged out, until you delete it |

Each step is best effort: one that fails is said, and the VM is deleted anyway, as you asked. The run ends
with one line for every VM you named, saying what became of it:

- deleted and released: what it was released from, and what is left, such as a GitHub token still valid;
- deleted without being released, and why - it had no address to reach it at (stopped and not starting,
  or its guest agent reporting none), it could not be reached over SSH, or its sudo failed - with what to
  remove by hand and where;
- not deleted, its deletion having failed (protection on, a stop that timed out), with why, and how far it
  was released first;
- kept, neither released nor deleted, because its sessions could not be saved, with why.

A VM that is still there keeps its SSH alias, and the line that says so gives the same reason; run `just
destroy` for it again once the cause is fixed, and the run fails while any VM was not deleted, naming each.
At a terminal the run is drawn live once you have answered, a row for each VM's release from each service
and each VM's state on it ([During a run](runs.md)); the lines above print whole above it. Each VM's
line is in NEXT STEPS too, except a VM deleted and released with nothing left to do by hand.
`destroy_release: false` deletes the VMs without releasing them, and says the same; their sessions are
still saved first, a stopped VM started for it, unless `destroy_save_sessions` is off too.

## See also

- Settings: [Sign-ins and teardown](../reference/settings.md#sign-ins-and-teardown) for
  `destroy_release`, `destroy_save_sessions` and `destroy_sessions_dir`, and
  [Secrets: Tailscale](../reference/settings.md#secrets-tailscale) for the API credential that deletes a
  device
- Sessions going the other way: [This machine's Claude sessions on the VM](vscode.md#this-machines-claude-sessions-on-the-vm)
- Roles: [`proxmox_vm`](../reference/roles/proxmox_vm.md), which deletes the VM, and the releases of
  [`claude_code`](../reference/roles/claude_code.md),
  [`github_projects`](../reference/roles/github_projects.md) and
  [`tailscale`](../reference/roles/tailscale.md)
- How it works: [§4.5](../architecture/lifecycle.md#45-playbooksdestroyyml--confirm-release-delete)
