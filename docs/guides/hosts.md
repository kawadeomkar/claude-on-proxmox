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

## Setting up another host

```
$ just init --host nuc --address 192.0.2.20
```

makes `hosts/nuc/` from the examples in `tools/templates/host/`, then asks only what is that host's own: its token's
ID and secret, or offers to make the token on it, as `just init` does for the first. The GitHub account and the VPN
are every host's, and not asked again. Then it checks the host with its settings as a run against it reads them, and
once every check passes, writes into `hosts/nuc/group_vars/all/local.yml` each setting a host sets for itself - the
node, storage and bridge it found, the rest as the shared files have them - and `vm_default_name: nuc-default`, so
a bare `just deploy` there never makes a second VM of the first host's default name. At a terminal it offers to make
`nuc` the current host.

A host is created only with its address, given as `--address` or typed at the question. Scripted, with no terminal,
`--address` is needed; and in a checkout of one host, `just init --host <word>` without `--address` stops rather than
take the word for a host's name, as 2.4 took it for an address ([Upgrading](upgrading.md#more-than-one-host-from-25)).
`just init --host nuc` again asks for what is still missing, as `just init` does.

`just vault --host nuc` edits that host's vault, and `just settings` shows the run's host's values - the current
host's, or the one `--host` names - each host file a layer of its own above the shared ones. `just settings vm_cores
--value 2` writes the shared `local.yml`, as before, and says so when the host's file outranks it; with `--host nuc`
it writes the host's own.

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
