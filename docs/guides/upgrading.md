# Upgrading

What to do when you update an install made by an earlier version of this project. There are no release
versions yet, so each section names the pull request that brought the change; an install made after it
needs nothing.

After pulling a new version, run `just install` before anything else. It syncs your environment to
`uv.lock` and installs the Galaxy collections `requirements.yml` pins, and a pull changes neither.
Every target that runs Ansible compares the installed collections with those pins first and stops,
naming `just install`, while one differs. They live in `~/.ansible/collections`, which every checkout
on the machine shares, so another clone or worktree on a different version needs `just install` again
when you go back to it.

## A template built before its record

From PR #126, `just deploy` compares the template with what its settings would build, and rebuilds it when it
differs: its CPU type, OS type, bridge or storage, and the image, packages and build steps it was made with,
which the build now records in the template's notes. A template an earlier version built keeps no such record,
so the first `just deploy` after upgrading says so and rebuilds it once, over SSH as root, as your first deploy
built it: a few minutes, at the same VMID. That rebuild also brings it up to date with the two sections below.
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

The build keeps the pristine download under `/var/lib/vz/claude-on-proxmox` on the node
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

## Tailscale on VMs configured by an earlier version

From PR #24, which joins the VMs to a tailnet. Tailscale is on by default, so the next `just deploy --only configure` installs it on every VM and reports each one
not signed in - or joins it, with `vault_tailscale_auth_key` set. Set `tailscale_enabled: false` in
`local.yml` first to keep your VMs as they are, and `just deploy --check` beforehand says what would be installed.
A VM where you installed Tailscale yourself is left exactly as it is either way.

Since `just init` asks whether to use Tailscale, the next `just init` asks it of a checkout whose `local.yml`
does not set `tailscale_enabled`: `y` keeps your VMs as they are, `n` takes Tailscale off them at the next
`just deploy`, which says so VM by VM, and Enter leaves it on, unanswered.

## Adopting VMs created before tagging

From PR #6 as well, which recognises this project's VMs by their tag. This project now recognises its VMs by one Proxmox tag, `claude-on-proxmox`, and
nothing else. VMs created by an earlier version carry `claude;ansible` instead,
so `just status`, `just deploy --only configure` and `just destroy` do not see them, and
`just deploy --only provision` refuses to touch a VM of that name rather than take it over.

To adopt one, add the tag - in the Proxmox UI (the pencil beside the tags in the
VM's title bar), or on the Proxmox host:

```bash
qm config 105 | grep ^tags          # the VM's current tags, if any
qm set 105 --tags "claude;ansible;claude-on-proxmox"
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
| `make list`, `make destroy alpha`, `make logs`, `make vault` | `just status` (`just list` still works), `just destroy alpha`, `just logs`, `just vault` |
| `make init HOST=pve.lan GITHUB_USER=octocat PROMPT=no` | `just init --host pve.lan --github-user octocat --no-prompt` (each `NAME=` is a `--name`) |
| `make install TYPE=lock` | `just install --lock` |
| `make lint TYPE=yaml`, `make test TYPE=unit`, `make docs TYPE=serve` | `just lint yaml`, `just test unit`, `just docs serve` |
| `make test ROLE=common` | `just test --role common` |
| `make lint TYPE=hooks SKIP=` | `just lint hooks --all-hooks` |
| `make vagrant TYPE=destroy`, `make clean TYPE=logs` | `just vagrant destroy`, `just clean logs` |
| `make deploy alpha ANSIBLE_ARGS="-vvv"` | `ANSIBLE_ARGS=-vvv just deploy alpha` |

The targets of the version before, thirty-six of them:

| Before | Now |
|---|---|
| `make venv`, `make deps` | `just install`, into your own virtualenv: see below |
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

The project keeps no `.venv` either. Make a virtualenv of its own for it, with any tool - `README.md`
suggests pyenv's, and `python3 -m venv <dir>`, activated or named with `PYTHON=<dir>/bin/python`, does as
well - and run `just install`. If `.venv` was a link to that environment, `just install` also reinstalls,
once, each package whose commands still start their Python by the link - uv wrote it into every one when
`make venv` synced through it - so run it first; the old `.venv` can then go - `rm .venv` if it is that
link, which leaves the environment itself alone - and `just clean` never deletes it. The VMs keep nothing
of the old names; what they print, and their session notes, name the new commands from the next `just
deploy` on. So that run, or the next `just code`, reports `changed` once for files that only name a
command: the VS Code workspace file and the session notes, and, on a VM whose Claude sign-in is still
missing, Remote Control's path unit, whose comment names `just login --only claude` (systemd is reloaded
for it). The run after that changes none of them.

## See also

- [Requirements](../getting-started/requirements.md) and [Proxmox access](../getting-started/proxmox-access.md),
  for what a host and a token need today.
- [Settings](../reference/settings.md), for every setting and its default.
- [Tearing down](destroy.md), for deleting a VM this project did not tag.
