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

The token belongs to a dedicated user with only the rights the `proxmox_vm` role needs, rather than root.
Four things make it up, and `tools/proxmox-access.sh` creates whichever of them is missing:

- the role `AnsibleVM`, with the privileges below;
- the user `ansible@pve`, with no password: it signs in by its token alone;
- the role, for that user, on `/vms`, on the storage for VM disks, and on the bridge the VMs are on, wherever
  the user does not already hold what the role gives there;
- the token `ansible@pve!ansible`, without privilege separation, so it has exactly the user's privileges.

The script changes nothing that exists. A role that lacks a privilege is named, with the line that adds it;
so is a grant on `/vms` that does not propagate to the VMs under it, where Proxmox checks it, and a token that
exists already, since Proxmox shows its secret only once. A user that was there already and holds privileges
on `/` is warned of before its token is made, since a token without privilege separation holds them too.
Nothing else on the host is touched.

### Letting `just init` set it up

Once the host's address is known and the vault has no token secret, `just init` at a terminal reaches the host
over root SSH, lists what it has, and shows what the script would run there before asking:

```text
just init can set up the API access this project uses on pve.lan, as root over SSH.
It would run there, creating only what is missing:
  pveum role add AnsibleVM --privs VM.Allocate,VM.Clone,...,SDN.Use
  ...
Set it up now? y or n
```

On `y` it runs the script, and the token's secret goes straight into the encrypted vault: it is never on the
screen or on a command line. This is the one change `just init` makes on Proxmox, and it makes it only on that
`y`. When the host has no `local-lvm` (an install on ZFS has `local-zfs`) or no `vmbr0`, `just init` first asks
which of the host's own to use, and writes the answer into `local.yml`. `n`, Enter, a failed root SSH or a run
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
pveum role add AnsibleVM --privs VM.Allocate,VM.Clone,VM.Config.CPU,VM.Config.Cloudinit,VM.Config.Disk,VM.Config.Memory,VM.Config.Network,VM.Config.Options,VM.PowerMgmt,VM.Audit,VM.GuestAgent.Audit,Datastore.AllocateSpace,Datastore.Audit,SDN.Use
pveum user add ansible@pve --comment claude-on-proxmox
pveum acl modify /vms --users ansible@pve --roles AnsibleVM
pveum acl modify /storage/local-lvm --users ansible@pve --roles AnsibleVM
pveum acl modify /sdn/zones/localnetwork/vmbr0 --users ansible@pve --roles AnsibleVM
pveum user token add ansible@pve ansible --privsep 0 --comment claude-on-proxmox
```

It prints the token's secret last, once: give it to `just init`, which asks for it (or to
`--token-secret-file`, [Your first deploy](first-deploy.md#without-a-terminal-or-scripted)).

### The guest agent's privilege

`VM.GuestAgent.Audit` is what lets the token read the address DHCP gave the VM; without it provisioning
creates the VM but cannot report where it is, and the API answers every address query with 403. Do not
substitute `VM.GuestAgent.Unrestricted`: it also works, but lets the token run arbitrary commands inside the VM.

A role made on 8.x loses `VM.Monitor` when the host is upgraded to 9.x. The script leaves an existing role as
it is and names what it lacks, and `just init` names it too; add the privilege to the role:

```bash
pveum role modify AnsibleVM --append 1 --privs VM.GuestAgent.Audit
```

### User Tag Access

One datacenter setting can also block provisioning: if **Datacenter → Options → User Tag Access**
(a separate option from Tag Style) is set to `list` or `existing` rather than the default `free`, a
non-root token cannot apply the `claude-on-proxmox` tag. Since that tag is how this project recognises its own
VMs, provisioning fails or the VMs are created untagged and `just deploy --only configure` then finds nothing.
Either leave the setting at `free`, or add both `claude-on-proxmox` and
`claude-on-proxmox-unfinished` to the allowed list. The second marks a VM whose first run has not
finished yet, and is removed once it has started.

### What `just init` checks

`just init` checks, read-only, that the token holds every privilege of the role on `/vms`, naming any it
lacks and the `pveum role modify` line that adds them. It asks for the token's ID (Enter keeps
`ansible@pve!ansible`) and writes a token named otherwise into `local.yml` as `proxmox_api_user` and
`proxmox_api_token_id`; the secret goes into the encrypted `vault.yml` as `vault_proxmox_api_token_secret`
([Your first deploy](first-deploy.md#set-up-the-project)).

## Trusting the Proxmox certificate

Proxmox ships a self-signed certificate, so `proxmox_validate_certs` defaults to `false`: the token is
sent to whatever answers on port 8006. To validate, copy the PVE CA once and point the HTTP client at it:

```bash
scp root@<pve-ip>:/etc/pve/pve-root-ca.pem ~/.config/pve-root-ca.pem
export REQUESTS_CA_BUNDLE=~/.config/pve-root-ca.pem
```

and set `proxmox_validate_certs: true` in `local.yml`.
