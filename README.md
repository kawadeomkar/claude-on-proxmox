# claude-on-proxmox

Ansible project that turns a Proxmox VE host into a ready-to-use
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) development VM:

1. **Template** – builds a cloud-init enabled Ubuntu 24.04 template on the Proxmox host (one time).
2. **Provision** – clones the template into a VM with a static IP, your SSH key and the sizing you chose.
3. **Configure** – installs a baseline of packages, developer tooling (Node.js, Docker, GitHub CLI, uv),
   Claude Code itself, and clones **every repository of your GitHub account** into `~/projects`.

Everything environment-specific (Proxmox IP, GitHub username, tokens) lives in git-ignored files, so the
repository can be shared as-is.

## Requirements

| Where | What |
|-------|------|
| Controller (your machine) | Python 3.12+ (required by the pinned ansible-core), `make`, SSH key at `~/.ssh/id_ed25519.pub` (or set `vm_ssh_public_keys`) |
| Proxmox VE 8.x | Root SSH access (template build only) and an API token |
| Testing (optional) | Docker Engine for Molecule; Vagrant + VirtualBox for a full VM test |

## Quick start

```bash
git clone <this repo> && cd claude-on-proxmox
make init            # venv + collections + copies the *.example files + pre-commit hook
```

Edit the three git-ignored files `make init` created:

| File | Contents |
|------|----------|
| `inventory/hosts.yml` | Proxmox host IP and one entry per VM: static IP, VMID, cores, memory, disk |
| `inventory/group_vars/all/local.yml` | Proxmox node/storage names, package choices, which repos to skip |
| `inventory/group_vars/all/vault.yml` | Proxmox API token, GitHub username/token, optional Anthropic API key |

Encrypt the secrets, then run the three stages. The `make` targets refuse to run while `vault.yml`
is still plaintext.

```bash
(umask 077; openssl rand -base64 32 > .vault_pass)
make vault-encrypt

make template        # once per Proxmox host  (playbooks/template.yml, over SSH)
make provision       # create + start the VM  (playbooks/provision.yml, via API)
make configure       # set the VM up          (playbooks/configure.yml, over SSH)
# or: make site     -> provision + configure
```

Then `ssh dev@<vm-ip>` and run `claude`. If you did not set `vault_anthropic_api_key`, log in
interactively the first time.

### Creating the Proxmox API token

Use a dedicated user with only the rights the `proxmox_vm` role needs, rather than a root token.
On the PVE shell:

```bash
pveum role add AnsibleVM -privs "VM.Allocate VM.Clone VM.Config.CDROM VM.Config.CPU VM.Config.Cloudinit \
  VM.Config.Disk VM.Config.Memory VM.Config.Network VM.Config.Options VM.PowerMgmt VM.Audit \
  Datastore.AllocateSpace Datastore.Audit SDN.Use"
pveum user add ansible@pve
pveum aclmod /vms -user ansible@pve -role AnsibleVM
pveum aclmod /storage/local-lvm -user ansible@pve -role AnsibleVM   # your VM storage
pveum aclmod /sdn/zones/localnetwork/vmbr0 -user ansible@pve -role AnsibleVM   # your bridge
pveum user token add ansible@pve ansible --privsep 0
```

Put the printed secret in `vault.yml` as `vault_proxmox_api_token_secret`. The defaults already use
`ansible@pve` / token ID `ansible`; change `proxmox_api_user` and `proxmox_api_token_id` in `local.yml`
if you named them differently.

### Trusting the Proxmox certificate

Proxmox ships a self-signed certificate, so `proxmox_validate_certs` defaults to `false`: the token is
sent to whatever answers on port 8006. To validate, copy the PVE CA once and point the HTTP client at it:

```bash
scp root@<pve-ip>:/etc/pve/pve-root-ca.pem ~/.config/pve-root-ca.pem
export REQUESTS_CA_BUNDLE=~/.config/pve-root-ca.pem
```

and set `proxmox_validate_certs: true` in `local.yml`.

## What gets installed

| Role | Purpose | Key variables |
|------|---------|---------------|
| `common` | apt upgrade, baseline packages, login user with passwordless sudo and SSH keys, sshd hardening (key-only login), timezone, qemu-guest-agent, `~/.local/bin` on PATH | `common_packages`, `common_extra_packages`, `common_timezone`, `common_harden_ssh` |
| `dev_tools` | Node.js (NodeSource), Docker Engine, GitHub CLI, uv/uvx (release pinned by version and SHA256), pipx, build tools. Apt signing keys are vendored in `roles/dev_tools/files` | `dev_tools_install_*`, `dev_tools_node_major`, `dev_tools_uv_version`, `dev_tools_npm_global_packages` |
| `claude_code` | Claude Code via the official installer (or npm), `~/.claude/settings.json` merged with your settings and API key, optional global `CLAUDE.md` | `claude_code_version`, `claude_code_install_method`, `claude_code_settings`, `claude_code_global_instructions` |
| `github_projects` | Lists your repositories with a custom `github_repos` module (pagination, fork/archive/empty-repo filters) and clones them | `github_projects` (names; empty = all), `github_projects_include_forks`, `github_projects_exclude`, `github_projects_clone_protocol` |
| `proxmox_vm` | Looks up the VM by VMID, clones the template, applies cloud-init (user, keys, static IP, DNS), resizes the disk, starts it. `proxmox_vm_state: absent` deletes it | `vm_id`, `vm_cores`, `vm_memory_mb`, `vm_disk_size`, `vm_gateway`, `vm_nameservers` (per host in `hosts.yml`) |
| `proxmox_template` | Downloads the Ubuntu cloud image (SHA256 verified), creates the VM with `qm`, imports the disk, adds the cloud-init drive, converts to template | `proxmox_template_vmid`, `proxmox_template_image_url`, `proxmox_template_storage` |

Every role documents its full interface in `roles/<name>/meta/argument_specs.yml`, and Ansible validates
role arguments against it at runtime.

### Variable layering

```
roles/*/defaults/main.yml                 role defaults, role-prefixed names
inventory/group_vars/all/defaults.yml     project defaults + wiring of vm_user, github_username, … into role vars   (committed)
inventory/group_vars/all/local.yml        your overrides                                                            (git-ignored)
inventory/group_vars/all/vault.yml        secrets as vault_* variables                                              (git-ignored, encrypted)
inventory/hosts.yml                       hosts and per-VM sizing                                                   (git-ignored)
```

Files in `group_vars/all/` load alphabetically, so `defaults` < `local` < `vault`.

### Choosing repositories

`github_projects` (in `local.yml`) is a list of repository names. Leave it empty and every repository
owned by `github_username` is cloned; list names to clone only those. Forks and archived repositories are
skipped unless `github_projects_include_forks` / `github_projects_include_archived` are true, and
`github_projects_exclude` always wins.

### Private repositories

The GitHub token is used for the API listing only. To clone private repositories set
`github_projects_clone_protocol: ssh` and put an SSH key on the VM, or run `gh auth login && gh auth setup-git`
on the VM once and re-run `make configure` (the `gh` CLI is installed). With the ssh protocol the role
pre-seeds GitHub's published host keys, so there is no trust-on-first-use prompt.

### Re-running

All playbooks are idempotent. `make configure` can be re-run at any time to pick up variable changes, and
`make check` shows what a run would change without touching the VM. Existing checkouts are never pulled
unless `github_projects_update_existing: true`. Hardware and cloud-init settings are applied when the VM is
created; set `proxmox_vm_force_update: true` to re-apply them. Two things are deliberately not reconciled
on every run: `apt upgrade` (new distro packages show up as changes) and the Claude Code binary, which
updates itself on the `stable`/`latest` channels.

### What the VM trusts

This is a development box, and the defaults reflect that:

- The `dev` user has passwordless sudo and is in the `docker` group, so anything running as that user,
  including Claude Code, is effectively root on the VM. The API key written to `~/.claude/settings.json`
  is therefore readable by any code the agent runs. Set `common_user_passwordless_sudo: false` and
  `dev_tools_install_docker: false` for a tighter box.
- Downloads are verified where the vendor makes that possible: the uv release by a pinned SHA256, the
  Ubuntu cloud image against Ubuntu's `SHA256SUMS`, apt repositories by vendored signing keys. The Claude
  Code installer script is fetched over TLS from `claude.ai` and itself verifies the binary against a
  release manifest; there is no independently signed artifact to pin.
- sshd is restricted to key-based logins (`common_harden_ssh`), even if `vm_password` is set.

### Tearing down

```bash
make destroy          # prompts for confirmation; stops and deletes the VM and its disks
```

## Testing

```bash
make lint                 # yamllint + ansible-lint (production profile) + ruff
make syntax               # --syntax-check of every playbook against the example inventory
make unit                 # pytest for the github_repos module (no network)
make molecule             # Molecule (Docker) test of every role, incl. idempotence
make molecule-integration # playbooks/configure.yml end to end in a container
make test                 # all of the above
make check                # dry run of configure.yml against your real VM (--check --diff)
make vagrant-up           # configure.yml on a real VirtualBox VM
```

Run Molecule through `make` (or with the virtualenv activated): Molecule picks `ansible-playbook` from
`PATH`, and a system-wide Ansible would silently be used instead of the pinned one.

Molecule scenarios use `geerlingguy/docker-ubuntu2404-ansible` (pinned by digest) with systemd, and share
`.config/molecule/config.yml`. Everything that would need a hypervisor or a third-party account is faked
so the tests are deterministic:

- `proxmox_vm` runs against a stateful fake Proxmox API (`tests/molecule/fake_pve_api.py`, TLS, token
  checked) and asserts the exact clone / config / resize / start / shutdown / delete requests, including
  a destroy pass via Molecule's side-effect stage.
- `proxmox_template` runs against a stateful fake `qm` (`tests/molecule/fake_qm.sh`) and a locally served
  "cloud image" with a real SHA256SUMS file.
- `github_projects` and the integration scenario talk to a fake GitHub API (`tests/molecule/fake_github_api.py`)
  that serves paginated responses and points clone URLs at local bare repositories.
- `claude_code`, `dev_tools`, and the apt steps of every scenario download real packages, so the tests
  need internet access. The integration scenario disables the Docker Engine install (a daemon cannot run
  in the test container); the `dev_tools` scenario installs it but does not start it.

Not covered by Molecule: the npm install method for Claude Code, reinstalling on an exact-version bump,
and `common_user_passwordless_sudo: false`. The Vagrant target exercises `configure.yml` on a real VM
including the Docker and qemu-guest-agent services.

### Continuous integration

`.github/workflows/ci.yml` runs on pushes to `main`/`master`, on pull requests and weekly:

1. **Lint and unit tests** - `make lint`, `make syntax`, `make unit`, a guard that fails if a `hosts.yml`,
   `local.yml`, `vault.yml` or `.vault_pass` is ever committed, and the pre-commit hooks the other targets
   do not cover (gitleaks, private-key detection, the whitespace and large-file checks). `pre-commit` runs
   the same linters locally from the virtualenv.
2. **Molecule** - every role scenario as a matrix job, plus the integration scenario.

The weekly run is a canary. Most of what this project installs lives outside the repository - the Ubuntu
cloud image, the Docker, NodeSource and GitHub CLI apt repositories, the Claude Code installer, the uv
release and the Galaxy collections (pinned as ranges) - and any of it can break without a commit here.

The scheduled run adds one more job that runs the integration scenario against the *unpinned* container
image (`MOLECULE_IMAGE=...:latest`) instead of the digest the other jobs use. It never runs on a push or a
pull request, and it is allowed to fail the weekly run: that failure is how you learn the current Ubuntu
image no longer works before you bump the digest.

No job needs a secret, so the full suite runs on pull requests from forks; keep it that way. Third-party
actions are pinned to commit SHAs, and Dependabot updates them along with the pip pins every week. The
setup steps shared by every job live in the composite action at `.github/actions/setup`.

Pushing a `v*` tag runs `.github/workflows/release.yml`, which publishes a GitHub release with notes
generated from the commits since the previous tag.

## Layout

```
ansible.cfg                 project-wide Ansible settings (accept-new host keys, fact cache, yaml output)
site.yml                    provision + configure
playbooks/                  template.yml, provision.yml, configure.yml, destroy.yml
inventory/                  hosts.yml.example, group_vars/all/{defaults.yml,local.yml.example,vault.yml.example}
roles/                      one role per concern, each with defaults, meta/argument_specs, molecule/
roles/github_projects/library/github_repos.py   custom module
tests/unit/                 pytest for custom modules
tests/molecule/             shared fakes for Molecule scenarios
molecule/configure/         integration scenario for the full configure playbook
                            (group_vars/all/defaults.yml is a symlink to the real one; keep it a git checkout)
.config/molecule/           Molecule settings shared by every scenario
.github/                    CI and release workflows, the shared setup action, Dependabot
requirements.txt            pinned Python tooling; requirements.yml: Galaxy collections
```

## Contributing

Bug reports and pull requests are welcome - see [CONTRIBUTING.md](CONTRIBUTING.md) for the setup, the test
commands and the conventions this repository follows. Security issues go through the process in
[SECURITY.md](SECURITY.md) rather than a public issue.

## License

MIT
