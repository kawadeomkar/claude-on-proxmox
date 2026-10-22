# 10. Security model

[SECURITY.md](https://github.com/kawadeomkar/claude-on-proxmox/blob/main/SECURITY.md) is the policy and
its known trade-offs, and [What the VM trusts](../guides/what-the-vm-trusts.md) the defaults a user lives
with; this is the mechanism.

## Authentication

Authentication is API tokens only, never a password. [Proxmox access](../getting-started/proxmox-access.md) documents creating a
dedicated, VM-scoped `ansible@pve` token rather than using `root@pam`. `validate_certs` defaults to `false`
because Proxmox ships a self-signed certificate, and the same page covers trusting the PVE CA to turn it
on.

## Tailscale

Tailscale ([§5.8](roles.md#58-tailscale--join-the-vm-to-a-tailnet)) widens what a VM can reach and what
can reach it. What a tailnet's default policy allows, the policy edit that bounds a tagged VM, and what
sudo on the VM can still do are trade-offs
[SECURITY.md lists](https://github.com/kawadeomkar/claude-on-proxmox/blob/main/SECURITY.md#known-trade-offs-not-vulnerabilities),
and [the guide's policy edit](../guides/tailscale.md#keeping-an-agent-vm-off-your-other-devices) is the
edit itself. The mechanism: the auth key is used for a first join only, on stdin, under `no_log`; the
web login's page is opened only when it is Tailscale's own, and the report names the tailnet joined,
since a pending page is readable by every local user on the VM. The VM keeps its own resolver
(`tailscale_accept_dns: false`).

## Secrets

Secrets live in `inventory/group_vars/all/vault.yml`, ansible-vault encrypted. `make vault-check`
runs before every playbook target and refuses to run while that file is plaintext. Any variable that
can hold a secret carries `no_log: true` in its `argument_specs`.

`make init` writes the vault encrypted, never in plaintext: `playbooks/init_settings.yml` puts the token
secret and the GitHub username into the vault's text in memory and pipes it into `ansible-vault encrypt`'s
stdin under `no_log`, which shreds the old file and writes the new one `0600`. Nothing renders the secret
outside `no_log` tasks, and init's inventory turns pipelining on, so even the module arguments that carry
it never land in `~/.ansible/tmp`; a run with `ANSIBLE_KEEP_REMOTE_FILES`, which turns pipelining off and
keeps each module's file there, is refused before it reads anything. Its password goes in `.vault_pass`, made only when there is no password
anywhere (`.vault_pass` or `ANSIBLE_VAULT_PASSWORD_FILE`): `O_EXCL`, `0600`, and never read, rewritten or
re-moded once it exists. The secret is never taken from `make`'s command line, which `make` copies into
`MAKEFLAGS` for every command a recipe runs, and which the shell's history, the process list and a CI log
keep: `TOKEN_SECRET=` or `CLAUDE_ON_PROXMOX_TOKEN_SECRET=` there stops `make` at once. A scripted `make
init` reads it from `TOKEN_SECRET_FILE`'s file or from `CLAUDE_ON_PROXMOX_TOKEN_SECRET` in the
environment, inside the playbook, so it is on no command line at all.

## Nothing environment-specific is committed

Two independent mechanisms:

- `.gitignore` stops the accident.
- `tests/check_no_local_files.sh` stops `git add -f`. It runs in CI and as the first step of the lint
  job. Its rules match at any depth, because Ansible loads `group_vars/` and `host_vars/` beside every
  inventory source and every playbook (`deploy.yml` at the root, the rest in `playbooks/`), and reads
  every file under `inventory/` as an inventory source. It blocks `hosts.yml`, `.yaml`, `.json` and
  `.ini` anywhere; under `inventory/`, any `hosts` file (backups included), any `.ini` and
  `*proxmox.yml`; anything named `local*`, `vault*` or `secret*` in a `group_vars/` tree; every
  `host_vars/`; `local`, `secret(s)` and `vault*` YAML or JSON anywhere; `.vault_pass*` and `.env*`.
  Only `.example` templates and the YAML fixtures in `molecule/*/host_vars/` are exempt, by name:
  anchoring the rules away from those fixtures is what once let `playbooks/group_vars/all/vault.yml`
  through. `tests/unit/test_tracked_files.py` asserts both directions — every blocked path is also
  gitignored, no fixture trips the guard or is gitignored, and no tracked file is gitignored.

## Supply chain

Galaxy collections are pinned to exact versions (a range means every run silently
picks up the latest publish, and made the CI cache key useless since it hashes the file). Ansible never
reads `requirements.yml`, and every checkout on a machine shares `~/.ansible/collections`, so every
target that runs Ansible runs `collections-check` first: `ansible-galaxy collection install --offline`
against the pins, which changes nothing when they are installed and fails, naming `make install`, when one
is not. pre-commit hooks are pinned to commit SHAs, not tags, because a tag can be moved. apt signing
keys are vendored in-repo. The uv release is pinned by version *and* per-architecture SHA-256, stored
separately from the download. The Molecule base image is pinned by digest, with an unpinned weekly
canary. What verifies each download a VM gets is listed in
[What the VM trusts](../guides/what-the-vm-trusts.md).

## Secret scanning

gitleaks runs as a pre-commit hook and, separately, in CI. The hook form
(`gitleaks git --pre-commit --staged`) scans the git index, which in a CI checkout equals HEAD — i.e.
an empty diff that always passes. CI therefore runs two scans, both with the digest-pinned image and
`.gitleaks.toml`. `dir` mode reads the working tree, with local build artifacts excluded. `git` mode
reads the patch of every commit the event brings in: `base..head` for a pull request, `before..after`
for a push, and all of head's history when there is no base in the clone (a new branch's all-zero
`before`, or a force push that orphaned it). The tree scan alone passed a token committed and then
deleted within a pull request, which leaves it in the published history. The script checks the range
with the image's own git before scanning, because gitleaks logs a git error and still exits 0.
Scheduled and manual runs bring in no commits and get the tree scan only.

Limits: a scan reports a secret that has already been pushed, and a pushed secret is public, so the
remedy is to revoke it, not rewrite history. `git log -p` shows no patch for a merge commit, so a
secret added only in a conflict resolution and removed later is missed. A pull request run is
cancelled when the branch is pushed again, so a commit force-pushed away before its own run finished
is never scanned.

## On the VM

Key-only SSH via a validated sshd drop-in, root password login disabled, passwordless
sudo for the dev user (a deliberate trade-off, recorded in
[SECURITY.md](https://github.com/kawadeomkar/claude-on-proxmox/blob/main/SECURITY.md#known-trade-offs-not-vulnerabilities)),
and GitHub's host keys pre-seeded from the API rather than trusted on first use. The GitHub token
sits in `~/.config/gh/hosts.yml` (0600) and the Anthropic key in `settings.json`; what that means for
the VM, and for the token a device-flow login leaves there, is
[What the VM trusts](../guides/what-the-vm-trusts.md). Every task that holds a token is `no_log`. `claude_code` reads
and writes everything under the VM user's home as that user, never as root: the user controls those
paths, and root following a symlink there (`~/.claude.json`, `~/.claude/settings.json`) would read or
overwrite a file the user cannot. `ansible.cfg` uses
`StrictHostKeyChecking=accept-new`: trust a freshly provisioned VM on first contact, but still refuse
if a known host's key changes later. The `vscode_server` drop-in keeps TCP and Unix-socket
forwarding on, which OpenSSH defaults to anyway; the role then asserts the values sshd runs with,
so a hardening drop-in of the user's own that turns them off is a named failure, not a VS Code
window that cannot connect.

## Run logs

Every Makefile target that runs Ansible exports `ANSIBLE_LOG_PATH` to
`.logs/<YYYYMMDD-HHMMSS>-<target>[-<TYPE>].log`, so Ansible mirrors its output, timestamped, into a file per
run while the terminal is untouched (prompts and colours included). `no_log` applies to the file as
to the screen. The file name never contains `VM_NAME`, because the path is in the environment of a
recipe that runs before `vm-name-check` has looked at that value; the `TYPE` in it is one the Makefile
checked against its fixed list while it was read, before any recipe. `.logs/` is git-ignored, blocked
by the tracked-file guard (with `*.log`), created `0700`, and pruned of files older than
`LOG_RETENTION_DAYS` (14) by `log-setup` at the start of each logged run, which deletes only files
named the way the Makefile names them, and only in `LOG_DIR`. A nested `$(MAKE)` inherits the
exported path, so one `make test` is one file.

## On the controller

The project writes exactly two things outside the repository,
`~/.ssh/known_hosts` and the managed SSH config, plus one `Include` line at the top of
`~/.ssh/config` ([§4.8](lifecycle.md#48-playbooksssh_configyml-and-playbookstasksssh_configyml)). Every
alias write is validated by the real ssh client before it lands, an
alias the user's own config already names is refused, and the Molecule scenarios point the managed
file into the ephemeral directory with the `Include` off, guarded by a unit test, because the
`configure` scenario's `localhost` is the developer's machine. SSH agent forwarding to the VM is off
and documented as a choice: the VM user is root-equivalent and runs an AI agent, and `ForwardAgent`
would hand it every key in the user's agent.

## In the editor

`make code`'s workspace file starts a terminal running `claude` when the window
opens, which is exactly the kind of code-on-open VS Code guards with Workspace Trust and the
automatic-tasks question. Both stay the user's to answer ([§4.9](lifecycle.md#49-playbookscodeyml)):
the project reads `task.allowAutomaticTasks` to say what will happen and never writes it, sets no
trust, and lowers no VS Code setting. The task is the one written in the file, not one a repository
can supply: the file lives beside the repositories, never in one, and a repository's own
`.vscode/tasks.json` still needs the same trust and permission as it would without this project. What
the task runs is fixed in `code.yml`, with the VM name passed as an argument rather than spliced into
the script.
