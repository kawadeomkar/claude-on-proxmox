# Proxmox access

The project reaches the Proxmox host two ways: over SSH as root for the one-time template build, and
through the API, with a token, for everything after it. The token is one `just init` offers to make for
you, over that same root SSH.

## Root SSH for the template build

`just deploy --only template` connects to the host over SSH as root, because importing a disk image needs `qm`.
Everything after that uses the API token instead. Install your key so the one SSH step is not
password-prompted:

```bash
ssh-copy-id root@<proxmox-host>
```

## The API token

The token belongs to a dedicated user rather than root, with only the privileges the `proxmox_vm` role uses -
but on every VM of the host, not only this project's: the grant on `/vms` propagates to all of them, so the
token can set the SSH keys cloud-init installs on any VM there, restart it, and delete it. Keep the vault, which
holds it, as you would a root password for those VMs. A later release will scope it to a Proxmox pool
([Sharing the host](#sharing-the-host)). Four things make it up, and `tools/proxmox-access.sh` creates whichever
of them is missing:

- the role `AnsibleVM`, with the privileges below;
- the user `ansible@pve`, with no password: it signs in by its token alone;
- the role, for that user, on `/vms`, on the storage for VM disks, and on the bridge the VMs are on, wherever
  the user does not already hold what the role gives there;
- the token `ansible@pve!ansible`, without privilege separation, so it has exactly the user's privileges.

The script changes nothing that exists but the role: one that lacks a privilege - as a role an older version
made lacks what a newer one needs - is given it with `pveum role modify AnsibleVM --append 1`, which keeps every
privilege it holds. A grant on `/vms` that does not propagate to the VMs under it, where Proxmox checks it, is
named with the line that fixes it, and so is a token that exists already, since Proxmox shows its secret only
once. A user that was there already and holds privileges
on `/` is warned of before its token is made, since a token without privilege separation holds them too.
Nothing else on the host is touched.

### Letting `just init` set it up

Once the host's address is known, `just init` at a terminal reaches the host over root SSH, lists what it has,
and shows what the script would run there before asking:

```text
just init can set up the API access this project uses on pve.lan, as root over SSH.
It would run there, adding only what is missing:
  pveum role add AnsibleVM --privs VM.Allocate,VM.Clone,...,SDN.Use,Sys.Audit
  ...
› yes
  no
↑ ↓ to move · Enter to choose · Esc to decide later
```

On yes it runs the script, and the token's secret goes straight into the encrypted vault: it is never on the
screen or on a command line. This is the one change `just init` makes on Proxmox, and it makes it only on that
yes. When the host has no `local-lvm` (an install on ZFS has `local-zfs`) or no `vmbr0`, `just init` first asks
which of the host's own to use, and writes the answer into `local.yml`. No, Esc, a failed root SSH or a run
with no terminal leave the host alone, and `just init` asks for the secret instead.

### Setting it up yourself

Run `tools/proxmox-access.sh` on the Proxmox host as root: from this checkout over root SSH, or copied to the
host and run in its shell (the web UI: the node, then *Shell*). `--dry-run` shows what it would run and runs
nothing:

```bash
ssh root@<proxmox-host> sh -s -- --dry-run < tools/proxmox-access.sh   # what it would run
ssh root@<proxmox-host> sh -s < tools/proxmox-access.sh                # run it
```

| Option | For |
|---|---|
| `--storage <id>` | The storage for VM disks, when it is not `local-lvm`: `local-zfs` on an install on ZFS. It must match `proxmox_storage` in `local.yml` |
| `--bridge <name>` | The bridge the VMs are on, when it is not `vmbr0`. It must match `proxmox_bridge` |
| `--user <user@realm>`, `--token <name>` | Another user or token name than `ansible@pve` and `ansible`, never `root@pam`; `just init`'s token question takes the same |
| `--dry-run` | Print the commands it would run, and run none |
| `--list` | Print the node's storages for VM disks and its bridges |

On a fresh Proxmox VE 9 host with the defaults, it runs:

```text
pveum role add AnsibleVM --privs VM.Allocate,VM.Clone,VM.Config.CPU,VM.Config.Cloudinit,VM.Config.Disk,VM.Config.Memory,VM.Config.Network,VM.Config.Options,VM.PowerMgmt,VM.Audit,VM.GuestAgent.Audit,Datastore.AllocateSpace,Datastore.Audit,SDN.Use,Sys.Audit
pveum user add ansible@pve --comment agent-warehouse
pveum acl modify /vms --users ansible@pve --roles AnsibleVM
pveum acl modify /storage/local-lvm --users ansible@pve --roles AnsibleVM
pveum acl modify /sdn/zones/localnetwork/vmbr0 --users ansible@pve --roles AnsibleVM
pveum acl modify /nodes/pve --users ansible@pve --roles AnsibleVM
pveum user token add ansible@pve ansible --privsep 0 --comment agent-warehouse
```

`Sys.Audit` on the node is read only: the node's CPU threads and memory, which `just status --only capacity` shows
and a sized `just deploy` checks a VM's size against ([Size](../guides/vms.md#size)). Nothing else needs it, and a
token without it works as before. A role made before it was added is offered it by the next `just init` at a
terminal, with the node's grant, as `pveum role modify AnsibleVM --append 1 --privs Sys.Audit` and `pveum acl
modify /nodes/<node> --users ansible@pve --roles AnsibleVM`: while the vault holds the token's secret, the offer
makes no token (the script's `--no-token`), whose secret could not take its place, and says nothing where nothing
is missing. It needs root SSH to the host, and grants on the node that SSH reaches: in a cluster, `just status`
gives each other node the two lines above, run as root on any node.

It prints the token's secret last, once: give it to `just init`, which asks for it (or to
`--token-secret-file`, [Your first deploy](first-deploy.md#without-a-terminal-or-scripted)).

### Sharing the host

A second person who deploys onto the same host gets a user of their own with `--user`, and a token of their own.
Their token holds the same role on `/vms`, so it can list, reconfigure and delete your VMs as yours can theirs.
The role is one for every user it is granted to: a privilege one person's `just init` adds to it - as a newer
version needs, on their `y` - is every holder's, on every path it is granted on.
The ownership tag does not separate you either. Anyone who can create a VM can tag it `agent-warehouse`, and
every tagged VM a token lists is treated as that person's own:

- a fleet-wide `just deploy --only configure` sends your secrets to every one of them that takes your SSH key;
- `just destroy <name>` deletes another person's VM of that name when you have none.

Until tokens can be scoped to a Proxmox pool of each person's own, which a later release will do:

- give each person a tag of their own, `claude_vm_tag: <tag>` in their `local.yml`, before their first deploy
  (VMs made under one tag are not found under another, and [User Tag Access](#user-tag-access) must allow the
  tag and its `-unfinished`, `-tailscale` and `-sized` forms);
- name the VMs a run is for rather than running fleet-wide.

That keeps each person's runs off the others' VMs by accident, but it does not stop someone who copies your tag
on purpose: share a host only with people you would trust with your VMs
([SECURITY.md's known trade-offs](https://github.com/kawadeomkar/agent-warehouse/blob/main/SECURITY.md#known-trade-offs-not-vulnerabilities)).

### The guest agent's privilege

`VM.GuestAgent.Audit` is what lets the token read the address DHCP gave the VM; without it provisioning
creates the VM but cannot report where it is, and the API answers every address query with 403. Do not
substitute `VM.GuestAgent.Unrestricted`: it also works, but lets the token run arbitrary commands inside the VM.

A role made on 8.x loses `VM.Monitor` when the host is upgraded to 9.x. The script adds what the role lacks,
and the next `just init` at a terminal offers to run it; by hand, add the privilege to the role:

```bash
pveum role modify AnsibleVM --append 1 --privs VM.GuestAgent.Audit
```

### User Tag Access

One datacenter setting can also block provisioning: if **Datacenter → Options → User Tag Access**
(a separate option from Tag Style) is set to `list` or `existing` rather than the default `free`, a
non-root token cannot apply the `agent-warehouse` tag. Since that tag is how this project recognises its own
VMs, provisioning fails or the VMs are created untagged and `just deploy --only configure` then finds nothing.
Either leave the setting at `free`, or add `agent-warehouse`, `agent-warehouse-unfinished`,
`agent-warehouse-tailscale` and `agent-warehouse-sized` to the allowed list. The second marks a VM whose first run
has not finished yet, and is removed once it has started. The third records that a VM has joined Tailscale, so
the vault's auth key is never sent to it again; without it, configure says it could not write the record. The fourth
marks a VM created with a size of its own (`just deploy alpha --cores 8 --memory 16G`); without it, such a run
fails at its settings call.

### What `just init` checks

`just init` checks, read-only, that the token holds every privilege of the role on `/vms`, naming any it
lacks and the `pveum role modify` line that adds them. It asks for the token's ID (Enter keeps
`ansible@pve!ansible`) and writes a token named otherwise into `local.yml` as `proxmox_api_user` and
`proxmox_api_token_id`; the secret goes into the encrypted `vault.yml` as `vault_proxmox_api_token_secret`
([Your first deploy](first-deploy.md#set-up-the-project)).

## Trusting the Proxmox certificate

Proxmox ships a self-signed certificate, so `proxmox_validate_certs` defaults to `false`: the token is
sent to whatever answers on port 8006. That includes the project's own VMs: they sit on `proxmox_bridge`,
by default `vmbr0`, usually the bridge the host's own address is on, so root on any of them - an agent with
passwordless sudo - can try to answer for the host there, and the token it reads reaches every VM on the
host. A later release will have `just init` fetch the CA and turn validation on by default. Until then, to
validate, copy the PVE CA once and point the HTTP client at it:

```bash
scp root@<pve-ip>:/etc/pve/pve-root-ca.pem ~/.config/pve-root-ca.pem
export REQUESTS_CA_BUNDLE=~/.config/pve-root-ca.pem
```

and set `proxmox_validate_certs: true` in `local.yml`.
