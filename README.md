# claude-on-proxmox

Ansible that turns a Proxmox VE host into [Claude Code](https://code.claude.com/docs) development VMs.
One command, `make deploy VM_NAME=alpha`:

1. **Template** – builds a cloud-init Ubuntu 24.04 template on the Proxmox host, once.
2. **Provision** – clones it into a VM named `alpha`, with your SSH key; your DHCP server gives it an
   address, and the guest agent reports it back.
3. **Configure** – installs a baseline, developer tooling (Node.js, Docker, GitHub CLI, uv), Claude Code
   and Tailscale, and clones every repository of your GitHub account (or the ones you list) into
   `~/projects`.
4. **Sign in** – to Claude, which starts Remote Control so the session is on your phone too; `gh` to
   GitHub, ready to push; and Tailscale, to reach the VM from anywhere. Each is a page in your browser.
5. **Open** – the VM in VS Code, with a terminal running Claude.

Every step is idempotent, so the same command is how you apply a change later. Everything specific to
your environment - the Proxmox address, tokens - lives in git-ignored files.

**Documentation:** [`docs/`](docs/README.md) covers every feature, setting and design decision, and
reads as a site with search at <https://kawadeomkar.github.io/claude-on-proxmox/>. This page gets you to
a first deploy.

## What you need

- **A Proxmox VE 8.x or 9.x host** that already runs VMs: `apt` working, a bridge with a DHCP server on
  it, and a storage for VM disks ([what the project assumes](docs/getting-started/requirements.md)).
- **On your machine** (macOS or Linux): Python 3.12+ in a virtualenv of its own for this project (pyenv's,
  or any other: `PYTHON=` names it), [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.12+,
  `make`, an SSH key at `~/.ssh/id_ed25519.pub`, and VS Code with the `code` command (optional).
- **Accounts:** a Claude Pro, Max, Team or Enterprise login, which Remote Control needs - on Team and
  Enterprise an Owner turns Remote Control on for the organisation first - or an Anthropic API key, with
  Remote Control off; GitHub; and Tailscale, which is on by default: without an account, set
  `tailscale_enabled: false` in `local.yml`.

## Quick start

**1. Get the project.**

```bash
git clone https://github.com/kawadeomkar/claude-on-proxmox.git && cd claude-on-proxmox
pyenv virtualenv 3.12 claude-on-proxmox && pyenv local claude-on-proxmox   # a Python environment of its own
make init            # make install (packages + collections) + copies the *.example files + pre-commit hook
```

pyenv, with its pyenv-virtualenv plugin, is the suggestion: `pyenv local` makes that environment this
directory's `python3` from then on. Any other virtualenv of Python 3.12 or later works -
`python3 -m venv ~/.virtualenvs/claude-on-proxmox`, then activate it in each shell, or put
`PYTHON=~/.virtualenvs/claude-on-proxmox/bin/python` on each `make` line.

**2. Give it access to Proxmox.** Install your key for the one root SSH step (the template build):

```bash
ssh-copy-id root@<proxmox-host>
```

Then, on the Proxmox host's shell, create a user and API token with only the rights this project needs
(swap in your storage and bridge if they are not `local-lvm` and `vmbr0`):

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

[Proxmox access](docs/getting-started/proxmox-access.md) explains the guest-agent privilege, a datacenter
setting that can block the project's tag, and validating the host's certificate.

**3. Fill in the three git-ignored files** that `make init` created:

| File | Contents |
|------|----------|
| `inventory/hosts.yml` | The address of your Proxmox host. VMs are **not** listed here |
| `inventory/group_vars/all/vault.yml` | `vault_proxmox_api_token_secret`: the token secret `pveum` printed, shown only once; `vault_github_username`. Optionally `vault_github_token`, `vault_anthropic_api_key` and `vault_tailscale_auth_key` |
| `inventory/group_vars/all/local.yml` | Only what differs from the defaults: a storage or bridge that is not `local-lvm` or `vmbr0`, a node not named `pve`, `tailscale_enabled: false` without a Tailscale account, VM size, which repositories to clone |

Every setting the two files take, with its default, is in the [settings reference](docs/reference/settings.md).

**4. Encrypt the vault and deploy.**

```bash
(umask 077; openssl rand -base64 32 > .vault_pass)
make vault

make deploy VM_NAME=alpha
```

The run stops for you three times, each at a page it opens in your browser:

1. **Claude** - authorise Claude Code; your browser comes back to the VM by itself. Skipped when the vault
   has an Anthropic API key.
2. **GitHub** - the one-time code is on your clipboard: paste it and authorise. Skipped when the vault
   has a GitHub token.
3. **Tailscale** - sign in and connect the VM. Skipped when the vault has a Tailscale auth key, or with
   `tailscale_enabled: false`.

Then VS Code opens on the VM, in `~/projects`, with Claude running in a terminal. The first time, VS Code
asks you to trust the folder and to allow automatic tasks; both are yours to answer.
[Your first deploy](docs/getting-started/first-deploy.md) walks through each stop, and what to do when
one is skipped or fails.

**If a run fails**, it stops and says why, and `make logs` lists the whole run. Fix the cause and run the
same `make deploy` again: every stage is idempotent, and a VM a failed run left half-built is finished
rather than skipped. `make vault` changes a secret. The usual first-run snags are in
[Your first deploy](docs/getting-started/first-deploy.md#if-a-run-fails).

## Everyday commands

| Command | What it does |
|---|---|
| `make deploy alpha beta` | Create and configure VMs and sign them in, then open VS Code on a lone one. Re-run it after changing a setting (VM size applies when a VM is created) |
| `make code alpha [PROJECT=repo]` | Open a VM, or one repository on it, in VS Code with a Claude terminal |
| `make login alpha` | Sign the VM in to Claude, GitHub and Tailscale where a sign-in was skipped; `TYPE=claude`, `github` or `tailscale` for one of them |
| `make list` | The VMs this project created, and their addresses |
| `make destroy alpha` | Sign the VM out of Claude, GitHub and Tailscale, then delete it |
| `make logs` | The log of every run, newest first |
| `ssh alpha` | The alias `make deploy` and `make code` keep pointed at the VM's address, in `~/.ssh/claude-on-proxmox.conf` (one `Include` line added to `~/.ssh/config`) |

`make deploy` does each of its parts on its own too: `make deploy alpha TAGS=claude_code` re-applies one
role, and `make deploy TYPE=configure` re-applies the whole configuration to every VM, creating none.
`make help` lists every target and the `TYPE` each one takes, and [the reference](docs/reference/make-targets.md)
says what each does.

## Help and contributing

Questions and bugs go to [Issues](https://github.com/kawadeomkar/claude-on-proxmox/issues); security
issues through [SECURITY.md](SECURITY.md) instead. Pull requests are welcome - see
[CONTRIBUTING.md](CONTRIBUTING.md) for the setup and the conventions, and [Testing](docs/development/testing.md)
for the test suite.

## License

[MIT](LICENSE)
