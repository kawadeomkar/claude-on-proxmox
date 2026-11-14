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
`machines`, `localhost` or a stage of `just deploy`: the first host's too, as `inventory/hosts.yml` names it, once
there is another, since every line that names one of its VMs then says `--host <name>`. A host's folder holds its
`hosts.yml` and `group_vars/` and nothing else Ansible reads as inventory: another `.yml`, `.yaml` or `.json` file, or
another folder, is refused, since a Proxmox host there would be sent this host's token. A file of a host's that is
not YAML stops that host's runs alone, said in its place.

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
in the shared vault, is refused. Nor may any of the files set what a run makes from its host - `proxmox_api_host`,
`proxmox_api_token_secret`, `proxmox_api_url`, `proxmox_api_headers`, `proxmox_api_module_defaults` - which would
outrank that for every host: a host's address is the `ansible_host` of its `hosts.yml`, and its secret is in its
vault.

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
take the word for a host's name, as 2.4 took it for an address ([Upgrading](upgrading.md#more-than-one-host-from-25)),
the name of the one host included. `just init --host nuc` again asks for what is still missing, and `just init` alone
works on the current host, as every command does: the first host's until `just host` chooses another.

Its token's secret is typed at the question, or given in a file, `--token-secret-file`: an exported
`AGENT_WAREHOUSE_TOKEN_SECRET`, as [the first deploy](../getting-started/first-deploy.md) sets for the first host, is
refused, since left in the shell it would be the first host's. `--github-user` and `--tailscale` are refused beside
another host: they go to `just init --host <first>`. Its vault is encrypted with the shared vault's password, so
while that vault is encrypted with a password you type, put it in `.vault_pass` (readable by you alone) first.

The aliases written before 2.5 record no host, and a VM named by one runs against the current host, which may not be
its own once there are two. Run `just deploy --only ssh-config` once before setting up the second host, or with
`--host <first>` after it: every alias of a running VM the first host lists is written again, recording it. A VM that
is stopped is not, nor one whose name has a dot, which only a run that names it writes: start it, or run
`just deploy <name> --only ssh-config`. Until then its alias is the current host's, and `just status` says one whose VM
no host has any more, which no prune removes: delete its block from `~/.ssh/agent-warehouse.conf` by hand.

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

`just host --json` is the list for a script or an agent, one JSON document: `format_version` and `kind`
(`host_list`) first, then `current_host`, the current host's name; `hosts`, a row per host with its `name`,
`address`, `folder`, `settings`, `host_kind` (`proxmox`), `first`, `current` (whether it is the current host) and
`problem`; and `problem`, why the hosts' files cannot be read. A `problem` is `""` where there is none, at the top as
in a row. Only these can be `null`: `current_host` where there is no current host, a row's `host_kind` for a
folder not set up, and `where`, below, where the list was not asked about a run. A release adds keys to it, and never
removes or renames one.

`--for <vm,...>` and `--host <name>` beside `--json` ask which host a run given those names and that `--host` would
reach, as `where`: its `host`, whether the VMs' aliases `routed` it there, the names no alias places (`unplaced`),
what the run would warn of (`notes`), and the `refusal` it would meet before anything starts, said here rather than
refused. It reads the files alone and switches nothing; the run still decides for itself when it starts, so give the
host you mean as `--host` to the run.

## The VMs and their aliases

Each VM's SSH alias records the host it was found on (`# origin: nuc` in the managed SSH config), and a run against
one host prunes, re-points and removes only that host's aliases: a fleet-wide `just deploy --only ssh-config`
against the homelab leaves the NUC's alone. A VM is never named after another of your hosts, nor given an alias of
another host's address.

A VM name belongs to one host, so a VM you name is found wherever it is:

| You type | Runs against |
|---|---|
| `just code alpha`, `just login alpha`, `just destroy alpha`, `just deploy alpha` (it exists) | the host `alpha`'s alias records |
| `just deploy newvm` (no alias) | the current host |
| `just deploy --only configure`, `just login` (no names) | the current host |
| `just login alpha beta`, on two hosts | refused: one host per run |
| anything with `--host` | that host, even for a name another host's alias records: a warning, and the VM of the name on that host |

A run an alias sends to another host than the current one says so, and `just destroy`'s question names the host it
deletes from.

The lookup is the managed SSH config on this machine, so it needs no host to answer; with `claude_ssh_config: false`
nothing records where a VM is, and every run is the current host's. A VM whose name another host's alias holds is
refused before anything is created, and the next `just init --host` writes `vm_default_name: <host>-default`, so a
bare `just deploy` never makes a second VM of another host's default name. Two VMs of one name, made before - or
while the aliases were off - still answer `just status`, `just login` and `just destroy` with `--host`; the alias stays
with the host that recorded it, and a `just deploy` or `just code` that would point it at the other is refused until
one of them has another name.

## Status

```
$ just status
agent-warehouse  2026-11-11 10:02 · 2 hosts · 4 VMs

▸ homelab · pve, Proxmox VE 9.2.21 · 3 VMs
VMS
  ...

nuc · not read
...
```

`just status` reads every host at once, each in a block of its own under its name, `▸` marking the current one;
a host that does not answer costs only its own block, and makes the run's exit 2 only when it is the current host.
Under ATTENTION each host says, beside its own, an alias recorded for it whose VM it no longer lists - deleted in
Proxmox's own UI, say - with `just deploy --only ssh-config --host <name>`, which prunes it, and a VM name two hosts
each have. `--host` reads one host, and a VM named is read where it is. `--json` keeps every key it had, the current
host's, and adds `hosts`, a row per host - its `name`, as `just host --json` names a host's row, `current`, `exit`,
`failed` and its own `report` - and a `host` on every other row, naming the host it is from. Each host's settings are
kept apart, in `.cache/status/<host>/`.

`just deploy newvm`, about to make a VM no alias records, first asks every other host whether it has a VM of that
name, and refuses one that does; a host that does not answer is said, and the run goes on.

## What hosts do not do

- **A VM stays on the host it was made on.** To move one, `just destroy` it, which saves what of its Claude Code
  sessions exists only there, then `just deploy <name> --host <other>`, with its `--repos`, `--cores`, `--memory`
  and `--disk` again: they were kept in Proxmox, on the old host. The sessions saved go on on this machine
  ([Its Claude Code sessions](destroy.md#its-claude-code-sessions)); the new VM starts without them.
- **The GitHub account and the VPN are set up once, for every host.** `just init --host` asks neither again, and
  Tailscale's auth key joins each host's VMs to the same tailnet. A setting in a host's own `local.yml`, or a secret
  in its `vault.yml`, outranks the shared one for its runs, where one host's VMs need another.
- **One run that changes something at a time, per checkout**, whichever host it is against: a deploy on the NUC is
  refused while one on the homelab runs, as a second on one host is, the lock naming the host of the run that holds
  it ([One run at a time](runs.md#one-run-at-a-time)).
- **The hosts are separate Proxmox hosts.** The nodes of one Proxmox cluster are one host, `proxmox_node` picking the
  node.

## Where the host is said

With more than one host, every run says which host it is against, where a mistake would hurt:

- the live run's first line, `agent-warehouse  deploy alpha · nuc · started 10:02`, and its last;
- the run lock, so a second run is told which host the one under way is on;
- `just destroy`'s question, CONFIRM DELETION: `This deletes alpha on nuc and its disks.`;
- a run that a VM's alias sends to another host than the current one, before anything starts;
- every line a run prints or writes onto a VM that tells you what to run - NEXT STEPS, a retry, the session notes
  and their "Signed in" rows, the Claude terminal's lines, `just status`'s ATTENTION - ends with `--host <name>`, so a
  line pasted later reaches that VM whichever host is current, and the notes' first line names the host;
- `just destroy`'s saved `sessions.json`, which records the host the VM was on;
- through [MCP](agents.md), the `hosts` tool lists the hosts and every run tool takes `host`, and names it in the
  job's title when it is not given; no tool changes the current host, a deploy that may create a VM must name one,
  `destroy`'s dialog names the host and its code holds it, and `settings_set`'s names the `local.yml` it writes.
