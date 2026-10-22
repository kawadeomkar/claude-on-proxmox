# Proxmox access

The project reaches the Proxmox host two ways: over SSH as root for the one-time template build, and
through the API, with a token, for everything after it.

## Root SSH for the template build

`make deploy TYPE=template` connects to the host over SSH as root, because importing a disk image needs `qm`.
Everything after that uses the API token instead. Install your key so the one SSH step is not
password-prompted:

```bash
ssh-copy-id root@<proxmox-host>
```

## Creating the Proxmox API token

Use a dedicated user with only the rights the `proxmox_vm` role needs, rather than a root token.
On the PVE shell:

```bash
# The guest-agent privilege is the one name that differs by version (`pveversion` shows yours):
# VM.Monitor on Proxmox VE 8, VM.GuestAgent.Audit on 9, which removed VM.Monitor.
AGENT_PRIV=VM.GuestAgent.Audit
pveversion | grep -q '^pve-manager/8\.' && AGENT_PRIV=VM.Monitor

pveum role add AnsibleVM -privs "VM.Allocate VM.Clone VM.Config.CPU VM.Config.Cloudinit \
  VM.Config.Disk VM.Config.Memory VM.Config.Network VM.Config.Options VM.PowerMgmt VM.Audit \
  Datastore.AllocateSpace Datastore.Audit SDN.Use $AGENT_PRIV"
pveum user add ansible@pve
pveum aclmod /vms -user ansible@pve -role AnsibleVM
pveum aclmod /storage/local-lvm -user ansible@pve -role AnsibleVM   # your VM storage
pveum aclmod /sdn/zones/localnetwork/vmbr0 -user ansible@pve -role AnsibleVM   # your bridge
pveum user token add ansible@pve ansible --privsep 0
```

The guest-agent privilege is what lets the token read the address DHCP gave the VM; without it
provisioning creates the VM but cannot report where it is, and the API answers every address query
with 403. On 9.x do not substitute `VM.GuestAgent.Unrestricted`: it also works, but lets the token
run arbitrary commands inside the VM.

If you created the role earlier without it, or have since upgraded the host from 8.x to 9.x (the
upgrade drops `VM.Monitor` from existing roles), add the privilege to the existing role instead:

```bash
pveum role modify AnsibleVM -append 1 -privs VM.GuestAgent.Audit   # VM.Monitor on 8.x
```

One datacenter setting can also block provisioning: if **Datacenter → Options → User Tag Access**
(a separate option from Tag Style) is set to `list` or `existing` rather than the default `free`, a
non-root token cannot apply the `claude-on-proxmox` tag. Since that tag is how this project recognises its own
VMs, provisioning fails or the VMs are created untagged and `make deploy TYPE=configure` then finds nothing.
Either leave the setting at `free`, or add both `claude-on-proxmox` and
`claude-on-proxmox-unfinished` to the allowed list. The second marks a VM whose first run has not
finished yet, and is removed once it has started.

Put the printed secret in `vault.yml` as `vault_proxmox_api_token_secret`. The defaults already use
`ansible@pve` / token ID `ansible`; change `proxmox_api_user` and `proxmox_api_token_id` in `local.yml`
if you named them differently.

## Trusting the Proxmox certificate

Proxmox ships a self-signed certificate, so `proxmox_validate_certs` defaults to `false`: the token is
sent to whatever answers on port 8006. To validate, copy the PVE CA once and point the HTTP client at it:

```bash
scp root@<pve-ip>:/etc/pve/pve-root-ca.pem ~/.config/pve-root-ca.pem
export REQUESTS_CA_BUNDLE=~/.config/pve-root-ca.pem
```

and set `proxmox_validate_certs: true` in `local.yml`.
