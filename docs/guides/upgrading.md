# Upgrading

What to do when you update an install made by an earlier version of this project. Each section names the
release, or before the first release the pull request, that brought the change; an install made after it needs
nothing.

After pulling a new version, run `just install` before anything else. It syncs the project's environment to
`uv.lock` and installs the Galaxy collections `requirements.yml` pins, and a pull changes neither.
Every target that runs Ansible compares the installed collections with those pins first and stops,
naming `just install`, while one differs. They live in the checkout's own folder
(`tools/env-run --collections`), so another clone or worktree keeps its own, on its own versions.

## More than one host, from 2.5

2.5 lets one checkout drive more than one Proxmox host ([More than one Proxmox host](hosts.md)). A checkout of one
host changes nothing, but:

- **`just init --host` names a host.** The Proxmox host's address is `--address` from 2.5:
  `just init --address pve.lan`. Through 2.x a `--host` value with a dot or a colon is still taken for the address,
  and said; a bare word is a host's name, and in a checkout of one host `just init --host pve` stops, naming
  `--address`, rather than set up a second host by accident.
- **Each SSH alias records its host** (`# origin: pve` in `~/.ssh/agent-warehouse.conf`). An alias written before
  2.4 has none, and the next write of its VM records it; until then it is the one host's.

## The project is called agent-warehouse

From 2.0 the project is called agent-warehouse, since it is to run more models than Claude Code and more
hypervisors than Proxmox VE. For an install made by 1.x:

- **The repository is `kawadeomkar/agent-warehouse`**, and the wiki is at
  <https://kawadeomkar.github.io/agent-warehouse/>. GitHub sends the old repository's web and git addresses on to
  the new one, but not the old wiki's. Point your clone at the new address:
  `git remote set-url origin https://github.com/kawadeomkar/agent-warehouse.git` (or `git@github.com:kawadeomkar/agent-warehouse.git`).
- **The token secret's variable is `AGENT_WAREHOUSE_TOKEN_SECRET`.** `CLAUDE_ON_PROXMOX_TOKEN_SECRET`, its name
  through 1.x, is still read while the new one is not set, and `just init` says so; set beside the new one to a
  different secret, it is refused, and neither is said. Rename it wherever you export it.
- **What the runs print says agent-warehouse:** the first line of every live run and of `just status`. Wording,
  which the promise leaves free, but a script that read those lines by the old name must follow.
- **The managed SSH config is `~/.ssh/agent-warehouse.conf`.** The first run that writes or removes an alias
  (`just deploy`, `just code`, `just destroy`, `just deploy --only ssh-config`) moves `~/.ssh/claude-on-proxmox.conf`
  there, every VM's alias with it, and adds one `Include` of the new file at the top of `~/.ssh/config`. The old
  `Include` line stays, since the project never rewrites that file, and does nothing once its file is gone: delete
  it when you like. `just status` reads the old file until then. Both files there is said, and the old one left to
  you.
- **The checkout's own folder is under `agent-warehouse/`** (`tools/env-run --data`), not `claude-on-proxmox/`. The
  first recipe after the pull stops, naming `just install`, which moves the folder, keeps its Galaxy collections, and
  makes its Python environment again when the checkout has one of its own (a virtualenv of yours is left as it is).
  `just clean env` deletes a folder left under the old name too.
- **`just init`'s git hook says agent-warehouse**; the one an earlier version installed is still known as the
  project's and replaced.
- **just code's own directory on a VM is `<projects>/.agent-warehouse/`.** The first `just code` or `just deploy`
  of a VM moves `<projects>/.claude-on-proxmox/` there, the session copies' manifest, the notes and the helpers with
  it, and keeps the shared helper's 1.x path, `.claude-on-proxmox/helpers/claude-start.sh`, as a link to the new one
  through 2.x, for a Zed task of your own; point that task at the new path when you like. A window VS Code reopens
  from its recent list by the old workspace file's path finds nothing there: open it with `just code` again. A
  directory you chose with `editor_files_dir` stays where it is. Zed's tasks file and the notes written by 1.x are
  known as just code's and written again.
- **The default VM is `agent-warehouse-default`.** A bare `just deploy` or `just destroy`, which acts on it, is refused
  while a VM of this project's is named `claude-on-proxmox-default`, 1.x's default, and none has the new name: the
  deploy would make a second VM beside it. Keep yours as the default with
  `just settings vm_default_name --value claude-on-proxmox-default`, or name the VMs. Renaming the VM in Proxmox is
  yours to do: renamed, it is a new cloud-init instance, with new SSH host keys at its next start.
- **On the Proxmox host**, a template's notes say "Built by agent-warehouse:" from its next build, and one 1.x
  built, "Built by claude-on-proxmox:", is still read as its record, so no template is rebuilt for that alone. The
  next build moves `/var/lib/vz/claude-on-proxmox`, where the image is kept, to `/var/lib/vz/agent-warehouse`; a
  directory you set with `proxmox_template_image_dir` stays where it is.
- **On each VM**, the next `just deploy` or `just login` moves what 1.x named claude-on-proxmox: the sshd drop-ins
  (`10-agent-warehouse.conf`, `15-agent-warehouse-path.conf`), apt's (`90agent-warehouse-downloads`), the cloud-init
  file a VM with passwordless sudo off keeps (`99-agent-warehouse-user.cfg`), the tmux settings' block, and
  `~/.config/agent-warehouse`, where the GitHub and Claude sign-ins keep their state - a login the project made is
  still known as its own. sshd is not restarted for the move. `just status` reads the 1.x names until then.
- **The Tailscale marker** moves from `/var/lib/claude-on-proxmox` to `/var/lib/agent-warehouse` with the next
  `just deploy` or `just login`, and tailscaled's drop-in from `10-claude-on-proxmox.conf` to
  `10-agent-warehouse.conf`, without restarting tailscaled. A Tailscale the project set up stays the project's, and
  one turned off since is still found and removed. `just status` reads the 1.x names until then.
- **The ownership tag is `agent-warehouse`.** A VM 1.x made, tagged `claude-on-proxmox`, is this project's through
  2.x as before, and so are the tags 1.x made from it: `claude-on-proxmox-tailscale` keeps the vault's auth key from
  that VM as `agent-warehouse-tailscale` does, and `claude-on-proxmox-unfinished` still gets it finished. The next
  `just deploy` that names the VM writes them under the new tag, in their places, and keeps every other tag; until
  then, a message may name the new spelling of a tag the VM carries under the old. While the ownership tag is one you
  set in `local.yml`, nothing changes.
- **Before you upgrade, if Datacenter, Options, User Tag Access is `list` or `existing`,** add `agent-warehouse`,
  `agent-warehouse-unfinished` and `agent-warehouse-tailscale` to its allowed list
  ([Proxmox access](../getting-started/proxmox-access.md)), or a token that is not root's cannot write them.
- **Upgrade every checkout that shares the Proxmox host at once.** A 1.x checkout does not know the new tag, so it
  no longer sees a VM 2.0 has written it on, and refuses to change or delete it.

## An environment and collections of the checkout's own

From 1.0, `just install` makes the project's Python environment itself, with uv, when you bring none: a
Python uv downloads, in the checkout's folder outside it (`tools/env-run --data`). A virtualenv of yours
is still used whenever `PYTHON=` names it or `python3` is one here - pyenv's, as `README.md` used to
suggest, included - so an install that has one goes on using it. The Galaxy collections move, for every
checkout, from `~/.ansible/collections` into that folder: the first recipe after pulling stops, naming
`just install`, which fetches them there. `~/.ansible/collections` can then go, unless another project of
yours uses it. `just init` now installs a git hook of the project's own, which runs pre-commit from the
environment of the checkout being committed in; the one `pre-commit install` wrote names your virtualenv's
Python, and goes on working while that virtualenv is there. To move from your own virtualenv to the
checkout's own, deactivate it (or drop the `.python-version` that `pyenv local` wrote, and unset
`PYTHON`) and run `just init`, which installs into the checkout's own and replaces that hook.

## Names 1.0 changed

1.0 settles the names the project promises to keep (`CONTRIBUTING.md`, Releasing), and changed these before it did:

| Before | From 1.0 | Where |
|---|---|---|
| `claude_remote_control` | `claude_code_remote_control` | `local.yml` |
| `claude_login_args` | `claude_code_login_args` | `local.yml` |
| `claude_login_timeout` | `claude_code_login_timeout` | `local.yml` |
| `claude_login_approval_timeout` | `claude_code_login_approval_timeout` | `local.yml` |
| `claude_vm_default_name` | `vm_default_name` | `local.yml` |
| `vm_password` | `vault_vm_password` | `vault.yml`, with `just vault` |
| `proxmox_vm_ipconfig` | `vm_ipconfig` | `local.yml`; the old name still works, as the role's own variable |
| `common_extra_packages` | `vm_extra_packages` | `local.yml`; the old name still works, as the role's own variable |
| `--token-name`, of `just init` | `--token-id` | the command line |
| the `list` recipe | `just status` | the command line |

Rename each in its file. Until you do, every recipe that reads the settings stops at any of the first six still
set in `local.yml` or `hosts.yml`, naming the file and the new name (`tools/check settings`), and `just init`'s
checks and provisioning stop at a `vm_password` in the vault that still holds a password: nothing would read them
under the old name. One set to `""` or left empty, as `vault.yml.example` had `vm_password`, sets nothing and is
left alone.

## Names 1.1 added

1.1 gives the settings of `just code` that are about any editor, or about none, names of their own, beside the
`editor` setting that picks the editor (VS Code by default):

| 1.0's name, still read | From 1.1 |
|---|---|
| `vscode_claude_terminal` | `editor_claude_terminal` |
| `vscode_workspaces_dir` | `editor_files_dir` |
| `vscode_session_notes` | `editor_session_notes` |
| `vscode_claude_sessions` | `claude_sessions_sync` |

Each 1.0 name still works through 1.x: the new name is fed from it, so a `local.yml` or `hosts.yml` that sets one
keeps doing what it did, and so does `-e` of one, while the new name is not set. Every recipe that reads the settings
says so while one is set (`tools/check settings`), naming the new name. Rename it there to quiet it:

- **The new name wins.** Once it is set - in `local.yml`, in `hosts.yml` - it is the one read, and the old name
  beside it changes nothing, even given with `-e` (`ANSIBLE_ARGS='-e vscode_claude_terminal=false'` then does
  nothing: give `-e editor_claude_terminal=false` instead). The warning says so while both are set.
- **`just settings` moves it for you.** `--value` on either name writes the new name and takes the old name's line
  out; `--reset` on either takes both out.
- **An empty `editor_files_dir` is the default** (`.agent-warehouse` in the projects directory from 2.0,
  `.claude-on-proxmox` before), never the old
  name's value: the old name is read only while the new one is not set at all.

`vscode_remote` and `vscode_user_settings_file` keep their names: the first still sets up the VM for any editor, the
second is VS Code's own.

## The vpn setting

The VPN each VM joins is the `vpn` setting from 1.x: `tailscale` or `none`. `tailscale_enabled`, which `just init`
wrote into `local.yml`, keeps working: while `vpn` is not set, `true` is `vpn: tailscale` and `false` is `vpn: none`.
Once `vpn` is set it is the one read, and `tailscale_enabled` beside it changes nothing: `just init` then asks no
Tailscale question, and refuses a `--tailscale` that `vpn` contradicts. `just status --json` keeps
`settings.tailscale_enabled`, now whether Tailscale is the VPN, and adds `settings.vpn` and `settings.tailscale_off`,
the setting that turned it off.

## WireGuard, removed in 2.1

`vpn: wireguard` - each VM a peer of a WireGuard server you run, by hand or through an OPNsense router's API - came
in 2.0 and went in 2.1 ([§15.5](../architecture/decisions.md#155-the-vpn-as-a-choice-beside-the-editor)). A VPN that
already routes your LAN - WireGuard on your router, a Tailscale subnet router, any other - reaches every VM at its
LAN address: set `vpn: none`. Or set `vpn: tailscale` to join each VM to your tailnet ([Tailscale](tailscale.md)).

Every recipe that reads the settings refuses a `local.yml` or `hosts.yml` that still sets `vpn: wireguard`,
`vpn_reach: tunnel` or a `wireguard_*` setting, naming it: take it out. `vpn_reach: lan`, which changed nothing, is
only warned of. Then, on each VM 2.0 set WireGuard up on:

```
sudo networkctl delete claude-wg
sudo rm -f /etc/systemd/network/50-claude-wg.netdev /etc/systemd/network/50-claude-wg.network \
  /etc/systemd/network/claude-wg.key
grep -qx 'installed wireguard-tools' /var/lib/agent-warehouse/wireguard-installed && sudo apt-get remove -y wireguard-tools
sudo networkctl reload
sudo rm -f /var/lib/agent-warehouse/wireguard-installed
```

And outside the VMs:

- each VM's peer, `agent-warehouse-<vm>`, removed from your WireGuard server;
- if you like, each VM's `agent-warehouse-wg-<address>` tag taken off in Proxmox: nothing reads it now, and a VM
  repaired loses it;
- with the OPNsense hub: the router's API user deleted, `vault_wireguard_hub_api_key` and
  `vault_wireguard_hub_api_secret` taken out of the vault (`just vault`), and the `wireguard_hub` group out of
  `hosts.yml`.

## A VPN only when chosen

From 2.1 no VPN is chosen until one is: a `local.yml` that sets neither `vpn` nor `tailscale_enabled` is `vpn:
none`, where through 2.0 it was Tailscale, and every command but `just init` warns of it. A VPN of yours that
routes your LAN reaches the VMs with `none` ([Reaching your VMs from away](remote-access.md)); to keep
Tailscale, set `vpn: tailscale`, or answer `just init`'s question, before your next `just deploy`.

Nothing is taken off a VM for it: from 2.1, choosing a VPN never removes one. A VM this project installed
Tailscale on stays on the tailnet under `none`, unmanaged - the run says so, and `just status` shows it - where
through 2.0 `tailscale_enabled: false` logged it out and removed it. Taking it off is by hand
([Turning it off](tailscale.md#turning-it-off)); `just destroy` still releases a VM's Tailscale, its device
deleted with an API credential.

`just init` writes its answer as `vpn: tailscale` or `vpn: none`; a `tailscale_enabled` an earlier one wrote is
still read.

## What just code writes and says from 1.1

The first `just code` (or `just deploy`) of a VM after the upgrade rewrites its workspace files and notes, and puts
VS Code's helpers beside them (`helpers/` in just code's directory there), each `changed` once. A window opened from
VS Code's recent list before that still runs 1.0's workspace file, which works as it did. An `editor_files_dir`
inside a checkout of the projects directory - a folder there with a `.git` - is refused from then on, before
anything is written: `just code` writes beside the repositories, never in one, so move it out of the repository.

The live output says the window's stage as "editor", where it said "VS Code", and two of its steps as "the editor
here" and "the notes" ("VS Code here" and "workspace" before); `just deploy`'s first line says "opening alpha in VS
Code". Wording, which the promise leaves free, but a script that read the line-per-step output by those names must
follow them.

## A template built before its record

From PR #126, `just deploy` compares the template with what its settings would build, and rebuilds it when it
differs: its CPU type, OS type, bridge or storage, and the image, packages and build steps it was made with,
which the build now records in the template's notes. A template with no such record is refused and left, never
rebuilt, since it may be one you made by hand. If it is one an earlier version of this project built, remove it
(`qm destroy <vmid>` on the Proxmox host) and run `just deploy` again: a few minutes, at the same VMID. That rebuild
also brings it up to date with the two sections below.
The VMs you have are full clones, so they do not depend on it and are not touched. A linked clone
(`proxmox_vm_full_clone: false`) does: Proxmox will not remove its template, and the run says so - set
`proxmox_template_rebuild: false` in `local.yml` to keep the template as it is.

## A template built before the guest agent

From PR #6, which finds each VM's address through its guest agent. Provisioning now needs `qemu-guest-agent` **inside** the image: it is what
reports the VM's DHCP address back. `just deploy --only template` bakes it in, but a template
built before this change does not have it. The first `just deploy` after upgrading rebuilds it, with
the guest agent ([A template built before its record](#a-template-built-before-its-record)).
A VM cloned from such a template never reports an address, so `just deploy --only provision`
polls its guest agent 60 times, five seconds apart (`proxmox_vm_agent_retries`,
`proxmox_vm_agent_delay`), and then fails. That is five minutes of waiting plus
the time each poll takes, and on a running VM with no agent Proxmox spends a few
seconds pinging the agent before it answers each one, so expect closer to nine
minutes before the failure.

The build keeps the pristine download under `/var/lib/vz/agent-warehouse` on the node (`/var/lib/vz/claude-on-proxmox`
through 1.x, which the next build moves there)
(`proxmox_template_image_dir`) and removes the customised copy it imports once the template is
finished. Earlier versions kept both under `local`'s `template/iso`, where the UI lists them as
ISO images; they are safe to delete from there once the template is built.

Existing VMs keep working; they are configured over SSH and are not re-cloned.
Your API token also needs the guest-agent privilege added - see
[The guest agent's privilege](../getting-started/proxmox-access.md#the-guest-agents-privilege).

## A vault GitHub token from an earlier version

From PR #22, which signs `gh` in on the VMs. Earlier versions asked for a token with `repo` alone, used only to list your repositories. This
version also signs `gh` in with it, and gh refuses a classic token without `read:org`. Such a token
keeps listing as before: the run says gh stayed signed out and which scopes to add (`read:org`, and
`workflow` for pushes that change `.github/workflows/`), and the next `just deploy --only configure` signs gh in once
they are there. `github_auth_method: web` signs gh in by the device flow instead, the token listing only.

## A gh login or git identity you set up by hand

From PR #22 as well. Earlier versions asked you to sign `gh` in on the VM yourself for pushes, beside a vault token used only
to list. This version signs gh in with `vault_github_token` only where gh is signed out or signed in by
this project, so such a login stays, and each run says the token only lists there; set
`github_auth_method: token` to have the token take over. A `user.name` or `user.email` you set on the VM
stays too: only a missing one is derived from your GitHub account, while `git_user_name` and
`git_user_email`, when set, are always written.

## Remote Control on a VM configured by an earlier version

From PR #23, which starts Remote Control when a login lands. A VM waiting for its first Claude login now gets a path unit that starts Remote Control the moment a
login lands, so a sign-in made in the Claude terminal of `just code` starts it by itself. A VM last
configured by an earlier version has no such unit: run `just deploy alpha --tags claude_code`
once (or `just deploy alpha`), or sign it in with `just login alpha --only claude`, which
starts Remote Control itself. The Claude terminal of `just code` tells the two apart, and on such a VM
names that `just deploy --only configure` line.

## Programs in ~/.local/bin and ~/.cargo/bin over ssh

From PR #73. `ssh <vm> <command>` now finds what is in `~/.local/bin` and `~/.cargo/bin` (Claude Code,
pipx and uv tools, anything you `cargo install`): the next `just deploy --only configure` gives sshd the
PATH for the VM user, and every ssh command after it is a new session that gets it. The same pull request
adds Rust to `dev_tools`, off unless you set `dev_tools_install_rust: true` in `local.yml`; `cargo`, `rustc`
and `rustup` then live in `/usr/bin`, so they work everywhere at once.

What was already running on the VM keeps the PATH it started with. `~/.local/bin` was on it already, so
this matters only for a program you `cargo install` after turning Rust on:

- In a terminal that was already open, run `. /etc/profile.d/cargo-bin-path.sh`, or open a new one.
- In VS Code, run **Remote-SSH: Kill VS Code Server on Host** and reconnect, before relying on an
  extension or a new terminal to find such a program. A window `just code` opens again gets it anyway: its
  shell pane is a login shell, and its Claude pane starts a new session.
- In a Claude Code session that was already running, including one served by Remote Control, call the
  program by its full path, `~/.cargo/bin/<name>`, or start a new session. In a terminal, quitting and
  running `claude --continue` keeps the conversation.

New sessions get it by themselves, and nothing needs restarting.

## Passwordless sudo turned off on a VM configured by an earlier version

`common_user_passwordless_sudo: false` used to take away only the `common` role's own sudo rule. cloud-init
writes one of its own for the user it creates, `NOPASSWD:ALL` in `/etc/sudoers.d/90-cloud-init-users`, so
the user stayed root. The setting now takes that rule away too, and the `lxd`, `cdrom` and `adm` groups
([What the VM trusts](what-the-vm-trusts.md)), and so needs a password for sudo to ask for:
`vault_vm_password`, which the playbooks become root with. A configure with the setting off and no
password fails before it changes anything. Set it with `just vault`, then run
`just deploy --only configure`. Likewise `dev_tools_install_docker: false` now takes the user out of the
`docker` group an earlier run put it in.

What was already running on the VM - a Remote Control session, a terminal, VS Code's server - keeps the
groups it started with until it restarts; restarting the VM ends them all.

## VM names an SSH alias cannot have

From 1.0. A VM's SSH alias is its name, in a file ssh reads ahead of your own config, so no alias is written
any more for a name ssh would take for another host: one it reads as an address (`192.0.2.10`, or a bare
number such as `42` or `0xbeef`), or one the inventory reaches a host by (`pve`, or the address of the Proxmox
host). `just deploy` and `just code` refuse such a name, and the next fleet-wide
`just deploy --only ssh-config` removes an alias an earlier version wrote for one, and says so. Rename such a
VM in Proxmox: renamed, it is a new cloud-init instance, which gives it new SSH host keys at its next start,
so `just deploy <new name> --only configure` takes the new key ([What just status shows](vms.md#what-just-status-shows)).

A name with a dot, as in `alpha.lab`, keeps its alias, but only a run that names it refreshes it -
`just deploy alpha.lab --only ssh-config`, `just code alpha.lab` or any run given that name. A fleet-wide
refresh leaves its alias as it was, and says so: on a shared host, a tagged VM named `github.com` could be
anyone's.

## Tailscale on VMs configured by an earlier version

From PR #24, which joins the VMs to a tailnet. Through 2.0 Tailscale was on by default; from 2.1 it is off
unless chosen ([A VPN only when chosen](#a-vpn-only-when-chosen)), so the next `just deploy` installs nothing of
Tailscale's unless `local.yml` sets `vpn: tailscale`, and `just deploy --check` beforehand says what would be
installed. A VM where you installed Tailscale yourself is left exactly as it is either way.

The next `just init` asks which VPN, `none` or `tailscale`, of a checkout whose `local.yml` sets neither `vpn` nor
`tailscale_enabled`, and writes the answer as `vpn` - `none` takes Tailscale off no VM. Left unanswered (Esc at the
menu, Enter when typed), it is none.

## Adopting VMs created before tagging

From PR #6 as well, which recognises this project's VMs by their tag. This project now recognises its VMs by one Proxmox tag, `agent-warehouse`, and
nothing else. VMs created by an earlier version carry `claude;ansible` instead,
so `just status`, `just deploy --only configure` and `just destroy` do not see them, and
`just deploy --only provision` refuses to touch a VM of that name rather than take it over.

To adopt one, add the tag - in the Proxmox UI (the pencil beside the tags in the
VM's title bar), or on the Proxmox host:

```bash
qm config 105 | grep ^tags          # the VM's current tags, if any
qm set 105 --tags "claude;ansible;agent-warehouse"
```

`--tags` **replaces** the whole list rather than adding to it, so include every
tag the VM should keep. Once tagged it is this project's like any other.

If you only want to delete such a VM, you can skip the tagging and say so
instead, after checking in the Proxmox UI that the name is the VM you mean:

```bash
ANSIBLE_ARGS='-e proxmox_vm_allow_untagged_delete=true' just destroy old-dev
```

## A template built from Ubuntu's server image

From PR #35, the template is built from Ubuntu's minimal cloud image. A template built before keeps the
server image, and so do the VMs cloned from it; they work as before, and the next `just deploy --only configure` masks
the services the minimal image leaves out anyway (fwupd, multipathd, ModemManager, udisks2, and snapd
while no snap is installed) and empties apt's download cache. New VMs are built from the minimal image
once the template is: the first `just deploy` after upgrading rebuilds it from that image
([A template built before its record](#a-template-built-before-its-record)). The VMs you have are full
clones, so they do not depend on it. VMs built from the minimal image have no manual pages or package
documentation, as on Ubuntu's other minimal images; `sudo unminimize` puts them back.

The minimum memory a VM may be left with (`vm_memory_min_mb`, from the same PR) is applied when a VM is
created; `proxmox_vm_force_update: true` applies it to the VMs you have
([Re-running](vms.md#re-running)).

## make became just

The command line moved from `make` to [`just`](https://github.com/casey/just), for options of each
command's own: `make` takes no flags of a target's, so the old Makefile read `TYPE=`, `VM_NAME=`, `TAGS=`
and the rest as variables. Install `just` 1.59.0 or later, as you installed uv ([Requirements](../getting-started/requirements.md)):
`brew install just`, `cargo install just`, or the release archive from github.com/casey/just, checked
against its `SHA256SUMS`. The `Makefile` is gone, so `make deploy` now gets make's own "No rule to make
target"; `just` lists the recipes, and `just help <recipe>` a recipe's options.

What each command does is unchanged; only its spelling moved. Names come after the recipe, options after
its name, and settings (`ANSIBLE_ARGS`, `LOG_DIR`, `PYTHON`) are exported or go before it:

| Before | Now |
|---|---|
| `make deploy VM_NAME=alpha,beta`, `make deploy alpha beta` | `just deploy alpha beta` |
| `make deploy TYPE=template`, `TYPE=provision`, `TYPE=configure`, `TYPE=ssh-config` | `just deploy --only template`, `--only provision`, `--only configure`, `--only ssh-config` |
| `make deploy TYPE=check` | `just deploy --check` |
| `make deploy alpha TAGS=claude_code` | `just deploy alpha --tags claude_code` (or `-t`) |
| `make deploy alpha ANSIBLE_ARGS="-e proxmox_vm_start_existing=true"` | `just deploy alpha --start` |
| `make code alpha PROJECT=repo` | `just code alpha --project repo` |
| `make code alpha SESSION=<id>` | `just code alpha --session <id>` |
| `make login alpha TYPE=github` | `just login alpha --only github` |
| `make list`, `make destroy alpha`, `make logs`, `make vault` | `just status`, `just destroy alpha`, `just logs`, `just vault` |
| `make init HOST=pve.lan GITHUB_USER=octocat PROMPT=no` | `just init --address pve.lan --github-user octocat --no-prompt` (each `NAME=` is a `--name`; `HOST=` is `--address` from 2.5) |
| `make install TYPE=lock` | `just install --lock` |
| `make lint TYPE=yaml`, `make test TYPE=unit`, `make docs TYPE=serve` | `just lint yaml`, `just test unit`, `just docs serve` |
| `make test ROLE=common` | `just test --role common` |
| `make lint TYPE=hooks SKIP=` | `just lint hooks --all-hooks` |
| `make vagrant TYPE=destroy`, `make clean TYPE=logs` | `just vagrant destroy`, `just clean logs` |
| `make deploy alpha ANSIBLE_ARGS="-vvv"` | `ANSIBLE_ARGS=-vvv just deploy alpha` |

The targets of the version before, thirty-six of them:

| Before | Now |
|---|---|
| `make venv`, `make deps` | `just install`: see below |
| `make lock` | `just install --lock` |
| `make vault-encrypt`, `make vault-edit` | `just vault`, which encrypts a plaintext vault and edits an encrypted one |
| `make template`, `make provision` | `just deploy --only template`, `just deploy --only provision` |
| `make configure`, `make check` | `just deploy --only configure`, `just deploy --check`; `just deploy <name> --tags <role>` is `--only configure` for that role |
| `make ssh-config` | `just deploy --only ssh-config` |
| `make claude-login`, `make github-login`, `make tailscale-login` | `just login --only claude`, `--only github`, `--only tailscale`; `just login` alone runs all three in deploy's order |
| `make syntax`, `make pre-commit` | `just lint syntax`, `just lint hooks`; `just lint` alone runs both, after the other linters |
| `make unit` | `just test unit` |
| `make molecule MOLECULE_ROLES=...` | `just test --role ...`; `just test role` for every role, `molecule` for every scenario |
| `make molecule-integration`, `make molecule-provision`, `make molecule-scenario SCENARIO=...` | `just test integration`, `just test provision` |
| `make docs-serve`, `make docs-build` | `just docs serve`, `just docs build` |
| `make vagrant-up`, `make vagrant-destroy` | `just vagrant`, `just vagrant destroy` |
| `make logs-clean` | `just clean logs` |

The project keeps no `.venv` either: `just install` makes the checkout's own environment outside it, or
installs into a virtualenv of yours - activated, or named with `PYTHON=<dir>/bin/python`. If `.venv` was a link to that environment, `just install` also reinstalls,
once, each package whose commands still start their Python by the link - uv wrote it into every one when
`make venv` synced through it - so run it first; the old `.venv` can then go - `rm .venv` if it is that
link, which leaves the environment itself alone - and `just clean` never deletes it. The VMs keep nothing
of the old names; what they print, and their session notes, name the new commands from the next `just
deploy` on. So that run, or the next `just code`, reports `changed` once for files that only name a
command: the VS Code workspace file and the session notes, and, on a VM whose Claude sign-in is still
missing, Remote Control's path unit, whose comment names `just login --only claude` (systemd is reloaded
for it). The run after that changes none of them.

## A BROWSER that runs a shell, cmd.exe or PowerShell

From PR #147. A `BROWSER` whose command runs a shell or a Windows command interpreter - `sh -c '...'`, or
WSL's usual `cmd.exe /c start` or `powershell.exe Start-Process` - reads the page as a command line of its own,
and a sign-in page's query string, which a VM's root can choose, would then be a command run on this machine.
Such a `BROWSER` no longer opens any page: the sign-ins print it for you to open, and Claude's asks for the
code at the `Code:` prompt ([Remote Control](remote-control.md#when-the-browser-cannot-come-back)). To have
pages open by themselves again, point `BROWSER` at a program that takes the page as one argument.

## See also

- [Requirements](../getting-started/requirements.md) and [Proxmox access](../getting-started/proxmox-access.md),
  for what a host and a token need today.
- [Settings](../reference/settings.md), for every setting and its default.
- [Tearing down](destroy.md), for deleting a VM this project did not tag.
