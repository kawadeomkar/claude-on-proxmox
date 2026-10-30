# 9. Failure modes

The traps this design exists to avoid, and how each is handled.

## VMID allocation is racy

Proxmox offers no atomic reservation: `proxmox_kvm` asks
`/cluster/nextid` and then clones, so two concurrent clones are handed the same free ID and the
second fails with `Unable to clone vm beta`. A clone that asks Proxmox for its VMID is therefore
`throttle: 1`. That used to apply to every clone, and it cost more than it looked: under the linear
strategy every later task waits for every host's clone, so each VM's settings, start and boot waited
for the last clone of the fleet (5 VMs with 10 s clones and 30 s boots, against the fake: 100 s,
all five started at 68 s). So `provision.yml` gives each new VM of a fleet its own VMID in play 1
(see [§4.2](lifecycle.md#42-playbooksprovisionyml--three-plays)), and the throttle is 0 for a clone that has
one, and for a VM that exists and skips the clone, which would otherwise queue behind a running one.
The same fleet then takes 57 s. `strategy: free` is not an alternative: it does not shorten the
run, since the last VM still boots after every clone, and the throttle does not even hold under it,
because `present.yml` is included per batch of hosts and `throttle` only counts workers running the
same task object. A VMID taken by something outside the run between play 1 and the clone still fails
that clone: `proxmox_kvm` checks, and the role asserts the clone changed something. A token that
cannot see every guest could be handed a VMID in use by one it cannot see; that clone fails too,
and provisioning one VM at a time avoids it.

## A VMID collision reports success

`proxmox_kvm` returns `changed=False` and the *clone source's*
VMID. Taking that on trust points every subsequent task at the template and reconfigures it. Hence
the explicit `assert: proxmox_vm_clone is changed`.

## `add_host` bypasses the play's host loop

Covered in [§4.2](lifecycle.md#42-playbooksprovisionyml--three-plays).

## A 403 looks like a slow boot

A token without the guest-agent privilege (`VM.Monitor` on PVE 8,
`VM.GuestAgent.Audit` on PVE 9, which removed `VM.Monitor`) fails the agent poll identically to a
VM that has not finished booting. Waiting the full `60 × 5s` to say so helps nobody, so the `until:`
breaks early on an access failure, as recognised by the `proxmox_access_denied` filter
([§7.1](custom-code.md#71-filter_pluginsproxmoxpy)). In `discover.yml` the same condition is `failed_when`, because
there it applies to every VM and swallowing it means exiting 0 with zero hosts.

## A problem `just init` finds

`playbooks/init_check.yml`, `just init`'s second step, checks read-only what `just deploy` will need, and marks
each problem `!!` with its fix; `just init` then fails, so a scripted one exits non-zero:

| `!!` line | Cause, and the fix |
|---|---|
| `vault.yml is not encrypted` | every recipe that opens the vault refuses a plaintext one: `just vault` |
| `refused the token ... (401)` | a token named otherwise (`proxmox_api_user`, `proxmox_api_token_id` in `local.yml`), or a wrong secret (`just vault`) |
| `nothing answered at https://...` | the address in `inventory/hosts.yml`, or a firewall between this machine and port 8006 |
| `certificate is not trusted` | `proxmox_validate_certs` on without the CA trusted ([Proxmox access](../getting-started/proxmox-access.md#trusting-the-proxmox-certificate)) |
| `the token lacks ... on /vms` | the role is missing a privilege, often the guest agent's after an upgrade from 8.x: the `pveum role modify` line it prints |
| `the token holds no privilege on /vms at all` | a token made with privilege separation, which has none of its user's: `--privsep 0`, or an ACL for the token |
| `... on /vms but not on the VMs under it` | the ACL on `/vms` has Propagate off: `pveum aclmod /vms ... -propagate 1` |
| `the template is on X, but proxmox_node is Y` | VMs are cloned on the node that holds the template: `proxmox_node: X` (`just init` at a terminal writes it while `proxmox_node` is the default) |
| `VMID ..., where just deploy would build it, is taken` | `proxmox_template_vmid` names a guest that is not the template: a free VMID, or `proxmox_template_name` of the template there |
| `root SSH to ... failed` | the template is not built yet, and is built over root SSH: `ssh-copy-id root@<host>` |
| `<host> is node X, but proxmox_node is Y` | the template is built on the host `hosts.yml` reaches and cloned on `proxmox_node`: `proxmox_node: X` |
| `which node <host> is cannot be told` | several nodes, and root SSH failed: fix SSH, and `just init` works it out |
| `storage X is not on <node>` | the default `local-lvm` is not there (a ZFS install has `local-zfs`): pick one `just init` lists, or set `proxmox_storage` |
| `storage X: the token lacks ...` | the role is not granted on `/storage/X`: the `pveum aclmod` line it prints |
| `... the token can use no other there. Proxmox lists one only to a token that holds ...` | the token sees no storage for VM disks at all, often a ZFS host where the role was granted on `/storage/local-lvm`: grant it on yours |
| `storage X is inactive` | Proxmox lists it but cannot reach it (an NFS share that is down) |
| `the template's net0 has no bridge` | every clone inherits that NIC: rebuild the template with `proxmox_bridge` set |
| `--   ... is not checked` | not a problem in itself: what stopped it (the node, the API) is said on its own line |
| `bridge X, the template's ... lacks SDN.Use` | every clone inherits the template's NIC: grant `SDN.Use` on that bridge |
| `the VMs would get no SSH key`, or only `vault_vm_password` | `vm_ssh_public_keys` in `local.yml`: deploy configures the VMs over SSH, which takes a key |
| `vm_password is set, which is vault_vm_password from 1.0` | the vault still holds the console password under its old name, which nothing reads: rename it with `just vault` |

Storage and bridge are checked on the node worked out, among what it lists: a default `local-lvm` on a ZFS
host is a name to replace, which `just init` offers, not a missing privilege.

Before those checks, at a terminal, `just init` offers to make the token (`tasks/init_access.yml`). What
stops the offer is a `NOTICE` that ends in how to run `tools/proxmox-access.sh` by hand, and `just init` asks
for the secret instead:

| `NOTICE` | Cause, and the fix |
|---|---|
| `root SSH to ... failed` | the key is not installed for root: `ssh-copy-id root@<host>`, then `just init` again |
| `tools/proxmox-access.sh refused on ...: error: run it as root` | `hosts.yml` reaches the host as another user: the template build needs root too |
| `proxmox_storage is X in local.yml, and the Proxmox host has no storage of that name` | a storage set by hand that the host does not list for VM disks: fix `local.yml` |
| `the storage is not chosen yet` | Enter at the storage (or bridge) question: `just init` asks again |
| `The API access is set up on ... already` | not a problem: the role, the user, its grants and the token exist, so `just init` asks for the secret Proxmox showed when the token was made. Lost it? Delete the token (`pveum user token delete ansible@pve ansible`; every machine that uses it loses access), and `just init` makes a new one |

A failure while the script runs fails `just init` with what it said; what it did before that stays, and the
next `just init` does only what is still missing. A vault that cannot be written after the token was made
deletes the token again, and says so; one written but not read back as written keeps it, since it holds the
secret, and says how to delete it should the checks then refuse that secret.

## Duplicate names

Proxmox permits them. The role refuses to act on one; `discover.yml` refuses to
continue with one, because the second `add_host` would silently overwrite the first.

## Tag substring matching

An unanchored search for `claude-on-proxmox` also claims
`claude-on-proxmox-staging`. See [§3](README.md#3-the-identity-model).

## Stale host keys

The host key is removed in `absent.yml`. See
[§5.2](roles.md#52-proxmox_vm--the-core-role). There is no fact cache to go stale ([§13](toolchain.md)).

## `/cluster/resources` lags

`proxmox_vm_info` builds its answer from it, and it can trail a VM
cloned moments ago, returning nothing. The post-start config read retries rather than indexing into
an empty list.

## A migrated VM

`proxmox_kvm`'s `update` sends the settings call to the node it is given rather
than looking the VM up, so addressed to `proxmox_vm_node` it failed for a VM moved to another node
with `Configuration file 'nodes/pve/qemu-server/9001.conf' does not exist`. Every call about an
existing VM goes to `proxmox_vm_found_node`, the node the lookup returned; only the clone, which needs
the template, goes to `proxmox_vm_node`.

## `-e key=value` splits on whitespace

Under make, `VM_NAME="alpha beta"` silently provisioned half the fleet.
The justfile passes the names as JSON (`-e '{"vm_name": "..."}'`) so the value arrives whole and reaches
the name check, which explains the problem.

## `vars_prompt` silently takes its default without a terminal

It does not hang — ansible-core
warns and uses the `default:` (or `None` when there is none), so the risk is an unattended run
quietly taking the default. Every prompt has a `default:`, and it is the safe answer.

## An API key silently outranks the subscription login

Claude Code's credential precedence puts
`ANTHROPIC_API_KEY` above the `/login` credential, so a VM configured with both would authenticate
as the API account — and Remote Control, which needs the subscription login, would refuse to start
with no obvious cause. The role never runs both: a configured key turns Remote Control off and
removes a server an earlier run installed, clearing the key removes it from `settings.json` again,
and eligibility requires `claude auth status` to report no `apiKeySource`, which also catches a key
from somewhere the role does not manage. See [§5.5](roles.md#55-claude_code--install-claude-code).

## There is deliberately no `LIMIT`

`--limit` is applied before the plays run, and these VMs only
enter the inventory once discovery has found them, so it could never match. The names after a recipe
are the selector for every one (`vm_name`).
