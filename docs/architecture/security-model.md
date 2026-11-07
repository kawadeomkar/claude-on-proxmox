# 10. Security model

[SECURITY.md](https://github.com/kawadeomkar/claude-on-proxmox/blob/main/SECURITY.md) is the policy and
its known trade-offs, and [What the VM trusts](../guides/what-the-vm-trusts.md) the defaults a user lives
with; this is the mechanism.

## Authentication

Authentication is API tokens only, never a password: a dedicated `ansible@pve` token rather than `root@pam`,
holding only the VM privileges the `proxmox_vm` role uses ([Proxmox access](../getting-started/proxmox-access.md)).
What the token reaches, what a shared host means and what to do about it, and the certificate it is sent to
unchecked by default are trade-offs
[SECURITY.md lists](https://github.com/kawadeomkar/claude-on-proxmox/blob/main/SECURITY.md#known-trade-offs-not-vulnerabilities),
with what to do in [Sharing the host](../getting-started/proxmox-access.md#sharing-the-host) and
[Trusting the Proxmox certificate](../getting-started/proxmox-access.md#trusting-the-proxmox-certificate).
`tools/proxmox-access.sh` makes the token: `just init` runs it on the Proxmox host over root SSH only after
showing what it would run and taking a `y`, it creates only what is missing and changes nothing that exists, and
the token's secret goes from the script's stdout into the encrypted vault under `no_log` - never on the screen or
a command line. A vault it cannot write deletes the token again, since its secret, shown once, would be lost.

## Tailscale

What a tailnet's default policy allows, the policy edit that bounds a tagged VM, and what sudo on the VM can still
do are trade-offs
[SECURITY.md lists](https://github.com/kawadeomkar/claude-on-proxmox/blob/main/SECURITY.md#known-trade-offs-not-vulnerabilities),
and [the guide's policy edit](../guides/tailscale.md#keeping-an-agent-vm-off-your-other-devices) is the edit
itself. How the auth key joins a VM once, and never a VM Proxmox records as having had a login, is
[§5.8](roles.md#58-tailscale--join-the-vm-to-a-tailnet); how it travels is under [Secrets](#secrets) below.

## Secrets

Secrets live in `inventory/group_vars/all/vault.yml`, ansible-vault encrypted. The vault check (`tools/check vault`)
runs before every recipe that runs a playbook, and refuses to run while that file is plaintext. Any variable that
can hold a secret carries `no_log: true` in its `argument_specs`.

`just init` writes the vault encrypted, never in plaintext: `playbooks/init_settings.yml` puts the token
secret and the GitHub username into the vault's text in memory and pipes it into `ansible-vault encrypt`'s
stdin under `no_log`, which shreds the old file and writes the new one `0600`. Nothing renders the secret
outside `no_log` tasks, and init's inventory turns pipelining on, so even the module arguments that carry
it never land in `~/.ansible/tmp`; a run with `ANSIBLE_KEEP_REMOTE_FILES`, which turns pipelining off and
keeps each module's file there, is refused before it reads anything. Every other run pipelines the modules
on this machine as well (`ansible.cfg`'s `[connection]`), so the Proxmox token every API call carries is not
written there, nor left there by a run killed part way - unless `ANSIBLE_KEEP_REMOTE_FILES` is set, which
only `just init` refuses. Its password goes in `.vault_pass`, made only when there is no password
anywhere (`.vault_pass` or `ANSIBLE_VAULT_PASSWORD_FILE`): `O_EXCL`, `0600`, and never read, rewritten or
re-moded once it exists. The secret is never taken from the command line, which the shell's history, the
process list and a CI log keep: `just init` has no option for it, refuses `--token-secret` as one it does
not have and `CLAUDE_ON_PROXMOX_TOKEN_SECRET=` before the recipe as a setting it does not have, and catches
any word after its options - make's spelling, `TOKEN_SECRET=...`, or the bare value - and refuses it with
a fixed message that never says it (an unknown recipe or a pattern refusal would print the word). A
scripted `just init` reads it from `--token-secret-file`'s file or from `CLAUDE_ON_PROXMOX_TOKEN_SECRET`
in the environment, inside the playbook, so it is on no command line at all.

### The credentials a run handles

Every task that hands the GitHub token on is `no_log`, which also keeps it out of the VM's journal: a module
logs the arguments its spec does not mark `no_log`, stdin included. `github_repos` strips the token from what it
returns and masks the stripped form as well ([§7.2](custom-code.md#72-rolesgithub_projectslibrarygithub_repospy)).
`tests/unit/test_github_auth.py` holds each such task to `no_log` and greps every run, module arguments
injected, for every canned token.

The Claude sign-in's one-time code (`roles/claude_code/tasks/browser_login.yml`) is read at a `pause` under
`no_log`, or given as `claude_code_login_code`, and travels to the VM on stdin into a `0600` named pipe only that
`claude auth login` reads. With pipelining on, as `ansible.cfg` sets it, it is never written to a file or a
command line; with pipelining off - which `ANSIBLE_KEEP_REMOTE_FILES=1`, Ansible's switch for debugging a
module, forces - the module that carries it is a file on the VM, and that switch keeps the file. The login it
produces is Claude Code's own `~/.claude/.credentials.json`, `0600`, which the project never reads.
`tests/unit/test_claude_login.py` holds both prompts and both writers to `no_log` and greps whole runs for the
code.

The Tailscale auth key goes to the VM as `tailscale up --auth-key=file:/dev/stdin`, the key the `command`
module's stdin under `no_log`, so it is on no command line and in no file, and it is never stored there:
tailscaled keeps the node's own keys, not the auth key. `tests/unit/test_tailscale.py` holds that task to be
the only one that renders the key, and greps every run, module arguments included, and every file, for every
canned key; the role's Molecule scenario searches the VM for it after the join. The Tailscale API credential
that deletes a VM's device is used only on this machine, in `uri` calls under `no_log`, and never reaches a VM.
`tests/unit/test_release.py` holds that destroy's release reaches no GitHub credential revocation endpoint, and
greps its whole output, module arguments included, for every canned credential.

### What a VM says, quoted here

What `claude`, `gh` and `ssh` print on the VM, which its user controls, is quoted in a message only through the
`plain_text` filter, every escape sequence and control character removed; so are the warnings, deprecations and
failure messages a module there returns, which ansible-core prints as they are, and a dry run's diffs, which the
stdout callbacks print through `plain_summary` ([§7.4](custom-code.md#74-filter_pluginsdesktoppy-and-filter_pluginstextpy)).
NEXT STEPS takes its lines from the project's own `set_stats` tasks only, and `just status` its report from its
own play on this machine, since any module's result can carry custom stats and facts. No sign-in page opens
through a `BROWSER` that is a shell or a Windows command interpreter, which would read the page's query string as
a command line.

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

Galaxy collections are pinned to exact versions, those the pinned ones depend on included (a range means every
run silently picks up the latest publish, and made the CI cache key useless since it hashes the file). Ansible never
reads `requirements.yml`, and each checkout's collections (`tools/env-run --collections`) stay as the last
`just install` left them, so every recipe that runs Ansible runs `tools/check collections` first: `ansible-galaxy collection install --offline`
against the pins, which changes nothing when they are installed and fails, naming `just install`, when one
is not. pre-commit's hook repositories are pinned to commit SHAs, not tags, because a tag can be moved; the
environments pre-commit builds for them are not. It installs each Python hook's own dependencies from PyPI
(`ruamel.yaml` for pre-commit-hooks, and the setuptools that builds it) unpinned and unhashed, outside
`uv.lock`, and, on a machine with no Go, downloads a Go toolchain for gitleaks with no checksum - once, when
the git hook or `just lint` first needs them, on the machine that holds the vault. apt signing
keys are vendored in-repo. The uv release is pinned by version *and* per-architecture SHA-256, stored
separately from the download, and is never older than the uv the controller's lock pins, which Dependabot
moves (`tests/unit/test_pins.py`): a pin nothing moved kept every VM on a uv with published advisories. The
Molecule base image is pinned by digest, with an unpinned weekly canary. What verifies each download a VM gets is listed in
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
[SECURITY.md](https://github.com/kawadeomkar/claude-on-proxmox/blob/main/SECURITY.md#known-trade-offs-not-vulnerabilities);
off, sudo takes `vault_vm_password` and cloud-init's own rule and root-equivalent groups go too,
[§5.3](roles.md#53-common--os-baseline)),
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
so a hardening drop-in of the user's own that turns them off is a named failure, not an editor
window that cannot connect. `common`'s PATH drop-in puts the VM user's `~/.local/bin` and `~/.cargo/bin`
first on the PATH of that user's ssh sessions, under `Match User`: what the user installs there can
shadow a system program in their own sessions, which their own shell profile could already do, while
root's sessions and sudo's `secure_path` are untouched. Ansible runs its modules with `/usr/bin/python3`
(`claude_vm_python`), never a Python it finds on that PATH, so `become` never runs one the user put
there as root.

## Run logs

Every recipe that runs Ansible runs it through `tools/run-logged`, which exports `ANSIBLE_LOG_PATH` as
`.logs/<YYYYMMDD-HHMMSS>-<recipe>[-<part>].log`, so Ansible mirrors its output, timestamped, into a file per
run while the terminal is untouched (prompts and colours included). `no_log` applies to the file as
to the screen. The file name never contains a VM name, or anything else a person typed but a part its
pattern limits to one listed word: the name is built from the recipe's own. `.logs/` is git-ignored, blocked
by the tracked-file guard (with `*.log`), created `0700`, and pruned of files older than
`LOG_RETENTION_DAYS` (14) by `tools/run-logged` at the start of each logged run, which deletes only files
named the way it names them, and only in `LOG_DIR`. Every command of one run shares the run's stamp and
name, so one `just test` is one file - but under `--jobs`, whose scenarios run side by side and would interleave
there: `tools/molecule-lanes` writes each one's whole output, without colour, to
`.logs/<stamp>-test-<scenario>.log`, the scenario a name from the justfile's own list, and takes
`ANSIBLE_LOG_PATH` out of its environment.

## On the controller

The project writes exactly three things outside the repository, and has VS Code's own CLI write a fourth:
`~/.ssh/known_hosts` and the managed SSH config, plus one `Include` line at the top of
`~/.ssh/config` ([§4.8](lifecycle.md#48-playbooksssh_configyml-and-playbookstasksssh_configyml)); and the
checkout's own folder, `${XDG_DATA_HOME:-~/.local/share}/claude-on-proxmox/checkouts/<folder>-<cksum>`
(`tools/env-run --data`), where `just install` puts the Galaxy collections and, when you bring no
virtualenv, the Python environment ([§13](toolchain.md)), and which `just clean env` deletes - and,
only where you point `destroy_sessions_dir` outside it, the Claude Code sessions `just destroy` saves
([§4.5](lifecycle.md#45-playbooksdestroyyml--confirm-release-delete)), which otherwise go into the
checkout's git-ignored `.sessions/`. Those are the VM's writing and are unpacked as plain files and
folders only, never a link the VM made. The fourth is VS Code's own: when VS Code lacks the Remote - SSH
extension, `just code` (and `just deploy`'s window step) runs
`code --install-extension ms-vscode-remote.remote-ssh`, and VS Code's CLI writes that extension, Microsoft's,
from VS Code's own marketplace into VS Code's extensions folder (`~/.vscode/extensions/`); nothing else of
VS Code's is changed. Every
alias write is validated by the real ssh client before it lands, an
alias the user's own config already names is refused, and the Molecule scenarios point the managed
file into the ephemeral directory with the `Include` off, guarded by a unit test, because the
`configure` scenario's `localhost` is the developer's machine. A bug there reaches every `ssh` on the machine,
and the VM chooses what goes in: its address is what its own guest agent reported, which `guest_ipv4` takes only
when it parses as one IPv4 address, and a name or an address that is not one plain word is refused, since a
newline in either would add a directive (`ProxyCommand` runs here). Its name is Proxmox's, chosen by whoever
made it, and the managed file is included ahead of the user's own config: a name ssh would take for another host
gets no alias, refused when named and left out of a fleet-wide refresh, said, and a name with a dot gets one only
when the user names it ([§4.8](lifecycle.md#48-playbooksssh_configyml-and-playbookstasksssh_configyml)). A VM
named after a host of the inventory is kept out wherever a name becomes a host
([§4.3](lifecycle.md#43-playbooksdiscoveryml)). SSH agent forwarding to the VM is off
and documented as a choice: the VM user is root-equivalent and runs an AI agent, and `ForwardAgent`
would hand it every key in the user's agent.

The sign-ins open a page in this machine's browser, and the VM chooses what its `claude`, `gh` and
`tailscale` print. Claude's page opens only when it matches `claude_code_login_page_pattern`
([§5.5](roles.md#55-claude_code--install-claude-code)) and Tailscale's only when it is Tailscale's own
([§5.8](roles.md#58-tailscale--join-the-vm-to-a-tailnet)). GitHub's device flow opens and shows
`https://github.com/login/device` whatever gh printed, since gh signs in to github.com alone, and says so
when gh named a page `github_projects_login_page_pattern` does not match, which no message quotes - a
failure quoting gh's own lines puts a placeholder in its place: a VM whose gh does that may not be yours
([§5.6](roles.md#56-github_projects--sign-in-to-github-clone-an-accounts-repositories)).

Claude's browser sign-in forwards one port of this machine's loopback to claude's callback on the VM
while it waits ([§5.5](roles.md#55-claude_code--install-claude-code)), by two forwards, one on `127.0.0.1`
and one on `::1`, each required by `ExitOnForwardFailure`. A browser tries both addresses for `localhost`,
whatever this machine's resolver says, and ssh makes a single forward on `localhost` once either address
binds, so a single forward would let a port another program holds here on one address take the callback,
or hand that program's connections to the VM. With two, a port in use on either address, or a machine with
no IPv6 loopback, falls back to the paste, and `AddressFamily=any` on the command line keeps an
`AddressFamily` in the user's ssh config from failing one of them every time. What the forward still
exposes - on macOS, the loopback connections at that port to a program of the user's own that listens on
every address, and the browser's `localhost` cookies that the redirect carries to the VM's port -
[SECURITY.md lists](https://github.com/kawadeomkar/claude-on-proxmox/blob/main/SECURITY.md#known-trade-offs-not-vulnerabilities).

## In the editor

In VS Code, `just code`'s workspace file starts a terminal running `claude` when the window
opens, which is exactly the kind of code-on-open VS Code guards with Workspace Trust and the
automatic-tasks question. Both stay the user's to answer ([§4.9](lifecycle.md#49-playbookscodeyml)):
the project reads `task.allowAutomaticTasks` to say what will happen and never writes it, sets no
trust, and lowers no VS Code setting. The task is the one written in the file, not one a repository
can supply: the file lives beside the repositories, never in one, and a repository's own
`.vscode/tasks.json` still needs the same trust and permission as it would without this project. What
the task runs is fixed: VS Code's helpers, files of `playbooks/files/` that `just code` copies to the VM
unchanged and the task runs by path, with the VM name and every other value passed as an argument rather
than spliced into a script, and the copy held by `tests/unit/test_code_session.py` to those files alone.

Wherever `just code` writes in its own directory on the VM, for any editor, it first refuses a directory
inside a checkout of the projects directory - a `.git` in any folder from the first below the projects directory
down to it, by the path as written - so neither a workspace file nor a task file lands in a repository there,
where git would track it or a later run would overwrite a file the repository holds. It looks nowhere else: not
outside the projects directory, where `editor_files_dir` may point, not in a projects directory that is itself a
checkout, and not where a symlink on the way leads, so an `editor_files_dir` in a repository is the user's to
avoid.

Zed runs no task as its window opens, so with `editor: zed` nothing starts by itself: `just code` runs
`zed ssh://<alias><folder>` - only a `zeditor` or `zed` that says it is Zed, never ZFS's daemon of the same
name - writes no Zed setting, keymap, task or trust of the user's, and never `trust_all_worktrees`. It writes
one task for the user to start, **Claude (just code)**, into `.zed/tasks.json` in its own directory on the VM,
never a repository's, and only where that directory is inside the projects directory: fixed text, with no tag,
which runs the shared helper `claude-start.sh` by path - the path the one value in it, held to a pattern of its
own - held to that by `tests/unit/test_code_session.py`. The file's first line is just code's ownership marker,
and a `tasks.json` there without it is someone else's: left as it is, and said. The helper starts a fresh
`claude` where a `claude` of a terminal already has the folder open, so two never write one transcript, and reads
Claude Code's registry with an isolated Python (`python3 -I`), so no `json.py` or other module of the folder - a
repository's root, often - runs in place of the standard library's.

The project binds no key and publishes no tag: Zed's spawn by tag starts every task that carries it, a
repository's in the window too, so a published tag would be a trigger any cloned repository, or a Claude talked
into it, could plant. Why the terminal comes before Zed's Agent Panel, whose Claude Agent runs on the VM with a
Claude Code of its own, is [§15.4](decisions.md#154-more-than-one-editor-and-hypervisor-one-of-each-at-a-time);
what the user decides in Zed - its trust, a key of their own, the files it shows - is
[What Zed decides](../guides/zed.md#what-zed-decides). Zed's remote server, in the VM's `~/.zed_server`, and that
agent, in `~/.local/share/zed/`, are the VM user's to replace, as VS Code Server's is.
