# More than one Proxmox host

One checkout can drive more than one Proxmox host - a homelab and a NUC, say. Every command runs against one host
at a time: the current one, which `just host` lists and switches, or the one `--host` names for a single run. A
checkout with one host works as it always has, and none of this needs doing.

## Where each host's settings are

The first host is the one `just init` set up, in `inventory/`: its address in `inventory/hosts.yml`, and its
settings and token in `inventory/group_vars/all/local.yml` and `vault.yml`. Those two files are every host's too:
the GitHub account, the VPN, the VMs' size and user. Each other host is a folder of its own:

```
hosts/
  current                          the current host's name; none means the first host
  nuc/
    hosts.yml                      its address: the one host of the proxmox group, named nuc
    group_vars/all/local.yml       its own settings, which outrank the shared ones for a run against it
    group_vars/all/vault.yml       its token, as vault_host_proxmox_api_token_secret, encrypted with .vault_pass
```

`hosts/` is git-ignored. A host's name is lowercase letters, digits and `-`, at most 31, and none of `current`,
`machines`, `localhost` or a stage of `just deploy`.

A run against `nuc` reads the shared settings, then `nuc`'s over them, so any setting can differ per host: a
smaller `vm_cores`, a `claude_ssh_host_domain` for its LAN, `proxmox_template_golden` on one host only. Two kinds of
setting are refused rather than taken from the shared files, since each describes one machine or one network:

- **What each host sets for itself**: `proxmox_node`, `proxmox_storage`, `proxmox_bridge`, `proxmox_api_user`,
  `proxmox_api_token_id`, `proxmox_api_port`, `proxmox_validate_certs` and `proxmox_template_vmid`. A host whose
  `local.yml` lacks one is refused, naming them.
- **What is never inherited**: `vm_ipconfig`, `vm_nameservers`, `proxmox_template_custom` and
  `claude_ssh_host_domain`. Where the shared `local.yml` sets one and the host's does not, a run is refused, naming
  the line to add - the value, or the setting's default to say none here. A static address of the homelab's network
  would otherwise be given to the NUC's VMs.

The token is never the first host's: a host whose vault has no `vault_host_proxmox_api_token_secret` fails before
anything is asked of it, and the first host's `vault_proxmox_api_token_secret` in a host's vault, or a host's name
in the shared vault, is refused.

## Switching

```
$ just host
  HOST     ADDRESS        NODE  SETTINGS
  homelab  192.0.2.10     pve   inventory/
▸ nuc      192.0.2.20     pve   hosts/nuc/   the current host

just host <name> switches; --host <name> on a command reaches another host once.
$ just host homelab
$ just deploy alpha --host nuc
```

`just host <name>` makes a host the current one; it waits, refused, while a run that changes something is under way,
so a run never ends on another host than the one it started on. `--host` on `deploy`, `code`, `login`, `destroy`
and `status` reaches another host for that run alone. `just host <name> --remove` deletes a host's folder after you
type its name: never the current host's or the first's, and never its VMs, which stay on it.

## The VMs and their aliases

Each VM's SSH alias records the host it was found on (`# origin: nuc` in the managed SSH config), and a run against
one host prunes, re-points and removes only that host's aliases: a fleet-wide `just deploy --only ssh-config`
against the homelab leaves the NUC's alone. A VM is never named after another of your hosts, nor given an alias of
another host's address.

`just status` keeps each host's settings apart (`.cache/status/<host>/`), and with `--host` reads that host.
