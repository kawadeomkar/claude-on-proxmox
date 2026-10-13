# 8. Convergence and idempotence

Idempotence is enforced mechanically: Molecule's idempotence stage re-runs converge and fails on any
task reporting `changed`.

**Existence is not convergence.** A run that dies between the clone and the settings call leaves a VM
that exists but has no cloud-init user, no SSH keys and no agent. Keying the settings call off
existence would skip the repair forever and then wait out the whole agent poll budget for an agent
that was never enabled. So:

```jinja
proxmox_vm_needs_configuration =
     (not proxmox_vm_exists)
  or (proxmox_vm_force_update | bool)
  or (proxmox_vm_lookup.proxmox_vms[0].config.ciuser | default('', true) | length == 0)
  or (proxmox_vm_unfinished_tag in proxmox_vm_found_tags)   # "claude-on-proxmox-unfinished"
```

The cloud-init user is written only by the settings call, and the template has none, so its absence
is the marker that the call did not complete. That call is not the last step, though: the resize and
the start follow it, and either can fail — a `vm_disk_size` smaller than the image, which Proxmox
cannot shrink to, a host without the memory to start the VM, or Ctrl-C. Keyed off the cloud-init
user alone, such a VM looked finished, so every re-run skipped the resize and the start and reported
it as shut down on purpose. Its status cannot tell it apart from a VM someone did shut down, and
starting every stopped VM would break the rule below. So the settings call also adds
`proxmox_vm_unfinished_tag`, and a call after the start — the last step that changes the VM —
removes it. A re-run that finds the tag repeats the settings, the resize and the start, whatever the
VM's status.

The marker is removed at the end rather than added there, so that VMs created before it existed
count as finished, and it is added by the settings call rather than the claim, so that a token barred
from applying it (a restrictive *User Tag Access*) still claims the VM and fails visibly at the
settings call on every run. A half-built or unfinished VM is repaired; a finished one is left alone.

The resize stays behind the same condition rather than running on every re-run. `proxmox_disk`
compares size strings, not sizes, so a size Proxmox writes back in other units would resize and
report a change on every run, and a disk someone grew by hand would fail every run, because
Proxmox cannot shrink one.

**Ownership comes before repair.** The marker used to be the tag, which the settings call also
writes. That made "untagged" mean "ours, but unfinished" — so provisioning a name that belonged to an
unrelated VM rewrote it and stamped the tag, and deletion then accepted it. Now the role tags a new VM
in a call of its own the moment it is cloned, and refuses any existing VM that is a template or lacks
the tag. Only a failure in the instant between the clone and the tag leaves an untagged VM of this
project's, and the refusal says how to claim it: add the tag in the Proxmox UI and re-run.

Three related switches:

| Variable | Default | Why |
|---|---|---|
| `proxmox_vm_force_update` | `false` | Proxmox reports *every* update as a change, which would break idempotence |
| `proxmox_vm_start_existing` | `false` | re-running provision must not boot a VM someone shut down deliberately |
| `github_projects_update_existing` | `false` | a re-run must not disturb local work in a checkout |

A VM this run created or repaired, including one it found tagged unfinished, is always started,
regardless of `start_existing`. A finished VM that is stopped, with `start_existing` off, is
recognised from the lookup's `status` and not waited on:
its agent cannot answer, and Proxmox's HTTP 500 for a stopped VM is retried like a slow boot, so the
wait would run its full `60 × 5s` — a count of polls, not a clock: five minutes of delay plus what
each poll takes, which on a running VM with no agent is Proxmox's own ~3 s agent ping, so about nine
minutes — and then blame the template, DHCP and the token. The role records
`proxmox_vm_stopped` and an empty address instead, and provision's play 3 reports it unpublished.
