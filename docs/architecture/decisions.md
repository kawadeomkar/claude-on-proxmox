# 15. Design decisions

Questions that were open, the answer each was given, and the facts that decided it, so that none has to
be worked out again. A question that comes up and is not settled yet goes here too, under a heading of
its own marked open, until it is.

## 15.1 Discovery stays a playbook, not the inventory plugin

*Decided October 2026, against `community.proxmox` 2.0.0, the pinned version, and 2.1.0, the latest, with
ansible-core 2.21: both read in their source and run against a fake Proxmox API.*

`community.proxmox` ships an inventory plugin, `community.proxmox.proxmox`, that can do the job of
`playbooks/discover.yml`. It lists the cluster and reads each VM's guest agent; its `compose` can take the
address on `net0`'s MAC, as `tasks/vm_addresses.yml` does, and a filter can keep the VMs tagged
`claude-on-proxmox` as a whole tag. It never sees `group_vars`, but its URL and token take lookups, so it
could read the address from `inventory/hosts.yml` and the token from the vault rather than have them
declared a second time. It is not used, because:

- **Little would go.** `tasks/cluster_vms.yml` and `tasks/vm_addresses.yml` stay whatever discovery
  does: `provision.yml`, `list.yml`, `ssh_config.yml`, `destroy.yml` and `code.yml` read the cluster at
  moments an inventory parsed as a run starts cannot see, such as a VM provisioning has just cloned or
  one `destroy` has just started. The plugin would replace `discover.yml` alone, and its checks would
  come back as a play after it: a VM named on the command line that was not found, two VMs with one name, a
  refused token, an empty fleet. Two ways of finding a VM's address would then have to agree.
- **A failure is a warning.** ansible-core turns any error in an inventory plugin, Proxmox not
  answering or the token refused among them, into "Unable to parse ... as an inventory source", keeps
  what was parsed before it, and the run goes on, its plays matching no hosts, to exit 0.
  `[inventory] unparsed_is_failed` fires only when no source parsed at all, and `inventory/controller.yml`
  always does. `any_unparsed_is_failed` makes the error fatal, but for every command, whenever Proxmox
  cannot be reached.
- **Some losses say nothing.** The VMs of a node that is offline are left out without a word. Two VMs
  with one name become one host, the later one's variables over the earlier one's, and on 2.1.0 one VM's
  ID can end up beside the other's address; `discover.yml` refuses to go on
  ([§9](failure-modes.md)).
- **Every command would call Proxmox.** Each `ansible-playbook` parses the inventory before anything
  else: `just lint syntax` would call the API once for each playbook it checks, ansible-lint would
  too, and so would CI, which has neither Proxmox nor the vault password. So would the commands that only
  need SSH to a VM. 2.0.0 sets no timeout on its requests; 2.1.0's `api_timeout` is 5 s.
- **It makes four API calls per VM** (its status, its configuration, its snapshots and its agent's
  interfaces), where `tasks/vm_addresses.yml` makes two.

Its one gain, a `--limit` that works, is not one here: the names after a recipe select the VMs, and are
checked before anything runs.

## 15.2 The guest agent starts where its port is

*Decided October 2026, against Ubuntu 24.04's `qemu-guest-agent` 8.2.2 and ansible-core 2.21.*

`common` used to start the services in `common_enabled_services` only where `virtualization_type` was
`kvm`, which was meant to say "a VM with a guest agent", and does not:

- **The agent needs its port, not KVM.** Proxmox gives a VM the virtio-serial port
  `org.qemu.guest_agent.0` while its agent is enabled, as the default `virtio` type, and the VM's udev
  links it as `/dev/virtio-ports/org.qemu.guest_agent.0`. The package's unit is static and bound to
  that device (`BindsTo=`): udev starts the agent when the port appears, and `systemctl start` without
  the port waits for the device, 90 s by systemd's `DefaultDeviceTimeoutSec`, then fails.
- **`kvm` is not the port.** ansible-core reports `kvm` on any guest whose firmware says QEMU, port or
  none, such as a Proxmox VM with its agent off or a cloud VM, and `kvm` with the role `host` on bare
  metal with the `kvm` module loaded, as it is on most machines with hardware virtualisation. Each of
  those failed the run after the wait. VirtualBox reports `virtualbox`, so the Vagrant VM started
  nothing from the list, a service a user added included.

So the agent starts only where its port exists, and every other listed service wherever systemd runs
([§5.3](roles.md#53-common--os-baseline)). The VMs this project creates always have the port:
`proxmox_vm` enables the agent.

## 15.3 The command line is just, not make

`make` takes no options of a target's own: `make init --host x` fails, and `make init -- --storage
test` runs the `test` target. So the `Makefile` grew an argument language of its own on make
variables - `TYPE=`, `VM_NAME=`, `TAGS=`, `ROLE=`, `PROJECT=`, eleven of `init`'s - and about two hundred
lines of parse-time guards against a stray word becoming a target, a VM name or an expansion. [`just`](https://github.com/casey/just)
recipes take options and words of their own (`just deploy alpha --tags claude_code`, `just init --host
pve.lan --no-prompt`), each held to a pattern before anything runs, with help that `just help <recipe>`
and the [Commands](../reference/commands.md) page read from the recipe itself. What each command does
did not change.

How the justfile is built, and why (each tried on just 1.59.0, on macOS and in `ubuntu:24.04`):

- **No recipe has a dependency.** `JUST_NO_DEPS` in the environment, or `--no-deps`, skips every one,
  so a check written as one could be switched off, and its refusal would come after just had evaluated
  what the recipe uses. Every check is a line of the recipe's own script instead: the checkout guard,
  then the recipe's rules, then `tools/env-run --bin`, `tools/check vault` and `tools/check collections`,
  then the work. A recipe that includes another's work (`test` lints, `init` installs) runs it as `{{ self
  }}`, which passes on every setting, since a recursive just starts without the command line's `BIN=`.
- **No assignment starts a program.** just evaluates every assignment a recipe uses, and the recipe's
  own `{{ }}`, before its first line runs: a `shell()` there would start before any refusal, and a
  `path_exists()` there reads the file as it was before the recipe ran - `init`'s checks would miss the
  `.vault_pass` its questions had just made. So those are decided by the shell, when the line runs.
- **Patterns for characters, the script for rules.** A pattern is checked against every value of a
  variadic parameter, and against its default, anchored at both ends; it is literal, since one naming an
  assignment could be loosened by overriding that assignment on the command line. A pattern refusal
  quotes the regex, so the rules between options - `--tags` beside names, `--check` with `--only` - and
  their messages are the script's. A value reaches the script as a positional argument, never as its
  text, and what goes into JSON is checked again there.
- **Bash, keeping to 3.2.** Ubuntu's `sh` is `dash`, which dies of a Ctrl-C that the playbook it waits on
  has caught and survived - Ansible's `pause`, Ctrl-C then C - so the run would end with exit 130 even
  under `[continue]`; bash waits for the playbook and carries on. macOS's `/bin/bash` is 3.2, under which
  `"${a[@]}"` of an empty array is fatal with `-u`, so the scripts build argument lists with `set --`.
- **The checkout guard.** just looks for a justfile upward and does not stop at a checkout's `.git`, so
  started in a worktree of a branch without one - under `.claude/worktrees/` - it would run this checkout's
  recipes, with its inventory and vault. `tools/check checkout` refuses that, comparing git's top level of
  where just was started with git's of this checkout: never the shell's `pwd`, which keeps a directory in
  the case it was typed in, while macOS's file system takes any case and git gives the disk's.
- **A catch-all on `init`.** A word after a recipe that takes none becomes the name of a recipe to run,
  and just prints it when there is none: `just init TOKEN_SECRET=...`, make's spelling, would print the
  secret. `init` takes the words after its options as `EXTRA`, which it refuses without saying them.
- **VM names start with a letter or a digit.** A recipe without options takes `--project` as a word, so a
  name may not start with `-`.
- **just is the user's, from its own releases.** PyPI's `rust-just` would have let `uv.lock` pin it, but
  it is a third party's own build of just: its binaries are not upstream's, and every bump would bring a
  new one onto the machine that holds the vault password. So just, like uv, is installed by the user, at
  least the version the justfile's `minimum-version` names, and CI installs upstream's archive checked
  against a pinned SHA256 ([§13](toolchain.md)).
- **What was dropped.** The `Makefile`, with its refusal of a target's name among VM names (just takes
  every word after a recipe with a variadic parameter as one of its values, and runs no second recipe),
  of names given both ways (there is no `VM_NAME`), of `$(...)` that make would expand (just expands
  nothing it is given), and the table of the targets an earlier version had: the project had one user, and
  [Upgrading](../guides/upgrading.md#make-became-just) maps every old command.
- **What just does not do.** Shell completion completes recipe names, not their options, and
  `just deploy --help` is an unknown option: `just help deploy` is the way in.
