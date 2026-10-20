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
  come back as a play after it: a VM named in `VM_NAME` that was not found, two VMs with one name, a
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
  else: `make syntax` would call the API once for each playbook it checks, ansible-lint would too, and
  so would CI, which has neither Proxmox nor the vault password. So would the commands that only need
  SSH to a VM. 2.0.0 sets no timeout on its requests; 2.1.0's `api_timeout` is 5 s.
- **It makes four API calls per VM** (its status, its configuration, its snapshots and its agent's
  interfaces), where `tasks/vm_addresses.yml` makes two.

Its one gain, a `--limit` that works, is not one here: `VM_NAME` selects the VMs, and is checked before
anything runs.

## 15.2 Open: where `common` starts its services

`common` starts the services in `common_enabled_services` only where `virtualization_type` is `kvm`,
which is meant to say "a VM with a guest agent", and does not. `qemu-guest-agent`, the one entry by
default, needs the virtio-serial port Proxmox gives a VM while its agent is enabled: its unit is bound to
that device, and a start without it waits 90 s and fails. ansible-core reports `kvm` on any guest whose
firmware says QEMU, port or none, and on bare metal with the `kvm` module loaded. VirtualBox reports
`virtualbox`, so the Vagrant VM starts nothing from the list, a service a user added included.
