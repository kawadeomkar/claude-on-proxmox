# claude-on-proxmox

Ansible that turns a Proxmox VE host into [Claude Code](https://code.claude.com/docs) development VMs.
One command, `just deploy alpha`:

1. **Template** – builds a cloud-init Ubuntu 24.04 template on the Proxmox host, once.
2. **Provision** – clones it into a VM named `alpha`, with your SSH key; your DHCP server gives it an
   address, and the guest agent reports it back.
3. **Configure** – installs a baseline, developer tooling (Node.js, Docker, GitHub CLI, uv, and optionally Rust),
   Claude Code and Tailscale, and clones every repository of your GitHub account (or the ones you list)
   into `~/projects`.
4. **Sign in** – to Claude, which starts Remote Control so the session is on your phone too; `gh` to
   GitHub, ready to push; and Tailscale, to reach the VM from anywhere. Each is a page in your browser.
5. **Open** – the VM in VS Code, with a terminal running Claude.

Every step is idempotent, so the same command is how you apply a change later. Everything specific to
your environment - the Proxmox address, tokens - lives in git-ignored files.

Then `just status` shows the whole fleet on one screen: each VM's size and the last day of its CPU, the
storage, each VM's addresses on the LAN and the tailnet, its sign-ins and Remote Control, its system and
updates, and the work in its checkouts that is not pushed yet. What needs you comes last, ranked, each with
the command that fixes it. It only reads, and changes nothing.

![just status: the whole report drawn at once, each value filling in as it is read](.github/assets/just-status.svg)

**Documentation:** [`docs/`](docs/README.md) covers every feature, setting and design decision, and
reads as a site with search at <https://kawadeomkar.github.io/claude-on-proxmox/>. This page gets you to
a first deploy.

## What you need

- **A Proxmox VE 8.x or 9.x host** that already runs VMs: `apt` working, a bridge with a DHCP server on
  it, and a storage for VM disks ([what the project assumes](docs/getting-started/requirements.md)).
- **On your machine** (macOS or Linux): Python 3.12+ in a virtualenv of its own for this project (pyenv's,
  or any other: `PYTHON=` names it), [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.12+,
  [just](https://github.com/casey/just) 1.59+ (`brew install just`, or `cargo install just`), an SSH key at
  `~/.ssh/id_ed25519.pub`, and VS Code with the `code` command (optional).
- **Accounts:** a Claude Pro, Max, Team or Enterprise login, which Remote Control needs - on Team and
  Enterprise an Owner turns Remote Control on for the organisation first - or an Anthropic API key, with
  Remote Control off; GitHub; and Tailscale, which is optional: `just init` asks whether to use it, and
  `n` keeps it off every VM, reached on the LAN as ever.

## Quick start

**1. Give it root SSH to Proxmox.** Install your key on the Proxmox host:

```bash
ssh-copy-id root@<proxmox-host>
```

The template build uses it. `just init` uses it too, once: it offers to create the API user, role and token
this project uses for everything else, with only the rights it needs, shows the commands it would run on the
host, and runs them only if you answer `y`. To create them yourself instead, answer `n` and run
[`tools/proxmox-access.sh`](tools/proxmox-access.sh) on the host. [Proxmox access](docs/getting-started/proxmox-access.md)
says how, and explains a datacenter setting that can block the project's tag and validating the host's
certificate.

**2. Get the project, and answer `just init`.**

```bash
git clone https://github.com/kawadeomkar/claude-on-proxmox.git && cd claude-on-proxmox
pyenv virtualenv 3.12 claude-on-proxmox && pyenv local claude-on-proxmox   # a Python environment of its own
just init            # installs the tools, asks for your Proxmox host and GitHub username, and offers to make the API token
```

pyenv, with its pyenv-virtualenv plugin, is the suggestion: `pyenv local` makes that environment this
directory's `python3` from then on. Any other virtualenv of Python 3.12 or later works -
`python3 -m venv ~/.virtualenvs/claude-on-proxmox`, then activate it in each shell, or export
`PYTHON=~/.virtualenvs/claude-on-proxmox/bin/python`.

Enter at a question leaves it for the next `just init`. What you answer goes into three git-ignored files,
the vault encrypted, with its password in `.vault_pass`: keep that file.

| File | Contents |
|------|----------|
| `inventory/hosts.yml` | The address of your Proxmox host, which `just init` asks for. VMs are **not** listed here |
| `inventory/group_vars/all/vault.yml` | Encrypted: your GitHub username, which `just init` asks for, and the token's secret, which it writes when it makes the token and asks for otherwise; a Tailscale auth key, which it asks for after a `y` to Tailscale. Optionally `vault_github_token` and `vault_anthropic_api_key`, with `just vault` |
| `inventory/group_vars/all/local.yml` | Only your preferences - VM size, which repositories to clone - and what `just init` writes there: `tailscale_enabled` from your answer, a token not named `ansible@pve!ansible`, and the node, storage and bridge when the defaults (`pve`, `local-lvm`, `vmbr0`) do not fit your host |

Every setting the two files take, with its default, is in the [settings reference](docs/reference/settings.md),
and `just vault` edits the vault. [Your first deploy](docs/getting-started/first-deploy.md) has the same
setup with no terminal, every answer an option of `just init`'s.

**3. Deploy.**

```bash
just deploy alpha
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

**If a run fails**, it stops and says why, and `just logs` lists the whole run. Fix the cause and run the
same `just deploy` again: every stage is idempotent, and a VM a failed run left half-built is finished
rather than skipped. `just vault` changes a secret. The usual first-run snags are in
[Your first deploy](docs/getting-started/first-deploy.md#if-a-run-fails).

## Everyday commands

| Command | What it does |
|---|---|
| `just deploy alpha beta` | Create and configure VMs and sign them in, then open VS Code on a lone one. Re-run it after changing a setting (VM size applies when a VM is created) |
| `just code alpha [--project repo] [--session <id>]` | Open a VM, or one repository on it, in VS Code with a Claude terminal; this machine's recent Claude sessions are copied there, and `--session` goes on with one of them |
| `just login alpha` | Sign the VM in to Claude, GitHub and Tailscale where a sign-in was skipped; `--only claude`, `github` or `tailscale` for one of them |
| `just status` | What every VM this project created is doing, and what needs doing next: read-only, live at a terminal, `--json` for scripts |
| `just settings tailscale` | Each of a role's settings, its value and where it was set; one setting (`just settings tailscale_ssh`), or every one (`--all`, `--changed` for yours). `just settings tailscale_ssh --value true` sets one in `local.yml`; a secret is only said set or not set, and changes with `just vault` |
| `just destroy alpha` | Save the Claude sessions only the VM has, sign it out of Claude, GitHub and Tailscale, then delete it |
| `just logs` | The log of every run, newest first |
| `ssh alpha` | The alias `just deploy` and `just code` keep pointed at the VM's address, in `~/.ssh/claude-on-proxmox.conf` (one `Include` line added to `~/.ssh/config`) |

`just deploy` does each of its parts on its own too: `just deploy alpha --tags claude_code` re-applies one
role, and `just deploy --only configure` re-applies the whole configuration to every VM, creating none.
`just` lists every recipe and `just help deploy` one's options; [the reference](docs/reference/commands.md)
says what each does.

## Help and contributing

Questions and bugs go to [Issues](https://github.com/kawadeomkar/claude-on-proxmox/issues); security
issues through [SECURITY.md](SECURITY.md) instead. Pull requests are welcome - see
[CONTRIBUTING.md](CONTRIBUTING.md) for the setup and the conventions, and [Testing](docs/development/testing.md)
for the test suite.

## License

[MIT](LICENSE)
