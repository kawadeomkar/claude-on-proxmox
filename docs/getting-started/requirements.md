# Requirements

| Where | What |
|-------|------|
| Controller (your machine) | Python 3.12+ (required by the pinned ansible-core) in a virtualenv of its own for this project, [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.12+, `make`, SSH key at `~/.ssh/id_ed25519.pub` (or set `vm_ssh_public_keys`); VS Code with the `code` command on `PATH` for `make code` (optional) |
| Proxmox VE 8.x or 9.x | Root SSH access (template build only) and an API token, see [Proxmox access](proxmox-access.md) |
| Testing (optional) | Docker Engine for Molecule; Vagrant + VirtualBox for a full VM test |

## What this project assumes of your Proxmox host

This project inherits your infrastructure. It creates VMs and reads back the addresses they were
given; it does not manage networking, storage, or address allocation, and it will not change
anything on the host outside the template it builds and the VMs it creates. A host that already runs
VMs usually has everything it needs; check the first and third rows in particular, since a host can run
VMs with a repository that fails `apt update`, or with static addresses and no DHCP server:

| Assumption | Why |
|---|---|
| `apt` works on the host | `make deploy TYPE=template` installs `libguestfs-tools` |
| The host can run VMs | hardware virtualization is on |
| A bridge exists, and something on it hands out DHCP leases | every VM takes its address from DHCP |
| A storage for VM disks, named in `proxmox_storage` | the template and every clone go there |
| Root SSH once, then an API token that can read the guest agent (`VM.Monitor` on 8.x, `VM.GuestAgent.Audit` on 9.x) | [Proxmox access](proxmox-access.md) |
| VMs may be created, tagged, started and deleted | the token's privileges |

A freshly installed host needs the ordinary post-install setup first; the
[Proxmox VE documentation](https://pve.proxmox.com/wiki/Main_Page) covers it. The sections below are
the parts of it this project depends on.

## Package repositories

`make deploy TYPE=template` installs `libguestfs-tools` on the host to bake the QEMU guest agent into the image,
and it refreshes the package lists first. That refresh fails if *any* repository does. A fresh install
enables two enterprise repositories, Proxmox VE's and Ceph's (Ceph's even if you never use Ceph), and
without a subscription both return `401 Unauthorized`, so one left enabled is enough to stop the build.
In *Node → Updates → Repositories*, disable every entry whose URI is on `enterprise.proxmox.com`, and add
the No-Subscription repository so the host keeps getting updates - see
[Package Repositories](https://pve.proxmox.com/wiki/Package_Repositories).

## Networking: a bridge, and a DHCP server with room in it

The installer creates `vmbr0`, bridged to your physical NIC. VMs attached to it sit on your LAN and
get their addresses from whatever already serves it — normally your router. That is the arrangement
this project expects, and it is the Proxmox default.

Two things are worth checking before you create a fleet, and both live in your router, not in
Proxmox:

- **The DHCP pool has enough free addresses** for the VMs you intend to run. Five VMs need five
  leases.
- **The pool does not overlap the static addresses** you gave the Proxmox node(s) at install time.

On the controller side, Ansible works on up to `forks` VMs at a time - 20, set in `ansible.cfg` -
so a fleet larger than that is cloned, booted and configured in waves. Raise it for one run with
`make deploy TYPE=provision ANSIBLE_ARGS="-f 30"`; the hypervisor, not Ansible, is the real limit.

Expect a VM's address to change when you rebuild it. A DHCP reservation in your router pins an
address to a MAC, and that only lasts as long as the VM: Proxmox gives every clone a new random MAC
and this project cannot choose one, so a destroyed and re-provisioned VM is a new MAC and a new
lease. You never need to know the address in advance anyway — `make list` shows it.

`proxmox_vm_ipconfig` in `local.yml` skips DHCP and assigns an address directly, for example
`ip=192.0.2.50/24,gw=192.0.2.1`. It is one value applied to **every VM a run provisions**, so with
more than one VM they would all be given the same address. Use it only for a single VM, or provision
one VM per run and change the value in between.

See [Network Configuration](https://pve.proxmox.com/wiki/Network_Configuration).

## Storage

A default LVM install gives you two: `local` for ISOs and templates, and `local-lvm` for VM disks.
**An install on ZFS has no `local-lvm`** — it has `local-zfs` instead. Check *Datacenter → Storage*,
and if yours is not `local-lvm`, set `proxmox_storage` in `local.yml`.

The same setting is how a storage of this project's own is handed to it: an admin who provisions a
dedicated pool for these VMs (on a NAS, say) gives you its id, you put it in `proxmox_storage` and
grant it to the token with the `pveum aclmod /storage/<id>` line in
[Creating the Proxmox API token](proxmox-access.md#creating-the-proxmox-api-token). The id is the whole
interface. This project only ever names a storage and puts the template and every clone on it; it
never creates or changes one, never asks for more than `Datastore.AllocateSpace` and
`Datastore.Audit` on that one path, and needs the `images` content type and nothing else.

See [Storage](https://pve.proxmox.com/wiki/Storage).

## If you have more than one node

Put **one** node's address in `inventory/hosts.yml`; the API proxies requests to whichever node
owns a VM. `proxmox_node` (in `local.yml`, default `pve`) decides where VMs are created, and it
**must be the node that holds the template**. The clone request goes to `proxmox_node`, and Proxmox
reads the template's configuration on the node it is sent to, so a template on any other node fails
to clone. Shared storage does not change that: it only matters to Proxmox's *target node* clone
option, which this project does not use.

`make deploy TYPE=template` builds the template on whichever node `inventory/hosts.yml` points at, over SSH, so
point it at the node you create VMs on and set `proxmox_node` to that same node — `make deploy`
does both in one run. To create VMs on several nodes, build a
template on each with its own `proxmox_template_vmid` and `proxmox_template_name` (VMIDs are unique
across the cluster, and cloning by name fails when two templates share one), then set
`proxmox_node` and `proxmox_template_name` together for each run. Only the clone cares about
`proxmox_node`: a VM you later migrate to another node is found by name wherever it is, and every
re-run — `make deploy TYPE=configure`, a repair, `proxmox_vm_force_update` — addresses the node it is on.

See [Cluster Manager](https://pve.proxmox.com/wiki/Cluster_Manager).
