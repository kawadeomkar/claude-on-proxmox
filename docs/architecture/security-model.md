# 10. Security model

[SECURITY.md](https://github.com/kawadeomkar/agent-warehouse/blob/main/SECURITY.md) is the policy and
its known trade-offs, and [What the VM trusts](../guides/what-the-vm-trusts.md) the defaults a user lives
with; this is the mechanism.

## Authentication

Authentication is API tokens only, never a password: a dedicated `ansible@pve` token rather than `root@pam`,
holding only the VM privileges the `proxmox_vm` role uses ([Proxmox access](../getting-started/proxmox-access.md)).
What the token reaches, what a shared host means and what to do about it, and the certificate it is sent to
unchecked by default are trade-offs
[SECURITY.md lists](https://github.com/kawadeomkar/agent-warehouse/blob/main/SECURITY.md#known-trade-offs-not-vulnerabilities),
with what to do in [Sharing the host](../getting-started/proxmox-access.md#sharing-the-host) and
[Trusting the Proxmox certificate](../getting-started/proxmox-access.md#trusting-the-proxmox-certificate).
`tools/proxmox-access.sh` makes the token: `just init` runs it on the Proxmox host over root SSH only after
showing what it would run and taking a `y`, it creates only what is missing and changes nothing that exists, and
the token's secret goes from the script's stdout into the encrypted vault under `no_log` - never on the screen or
a command line. A vault it cannot write deletes the token again, since its secret, shown once, would be lost.

## Tailscale

What a tailnet's default policy allows, the policy edit that bounds a tagged VM, and what sudo on the VM can still
do are trade-offs
[SECURITY.md lists](https://github.com/kawadeomkar/agent-warehouse/blob/main/SECURITY.md#known-trade-offs-not-vulnerabilities),
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
not have and `AGENT_WAREHOUSE_TOKEN_SECRET=` before the recipe as a setting it does not have, and catches
any word after its options - make's spelling, `TOKEN_SECRET=...`, or the bare value - and refuses it with
a fixed message that never says it (an unknown recipe or a pattern refusal would print the word). A
scripted `just init` reads it from `--token-secret-file`'s file or from `AGENT_WAREHOUSE_TOKEN_SECRET`
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
NEXT STEPS takes its lines from the project's own `set_stats` tasks only, since any module's result can carry custom
stats. `just status` runs no playbook: what a VM sends it is data, held to a size, a time and a shape, each value one
line of plain text before anything keeps it. No sign-in page opens
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
is not. `just status` runs it from its engine instead, only when it asks Ansible for its settings
([§4.6](lifecycle.md#46-toolsstatus--read-only-drawn-live)). pre-commit's hook repositories are pinned to commit SHAs, not tags, because a tag can be moved; the
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
[SECURITY.md](https://github.com/kawadeomkar/agent-warehouse/blob/main/SECURITY.md#known-trade-offs-not-vulnerabilities);
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

## A golden template

A golden template is cloned into every new VM of its key, so nothing that belongs to one VM, or one person, may reach
it ([§4.14](lifecycle.md#414-playbooksgoldenyml--the-golden-template-new-vms-clone)).

- **No secret enters it.** The build VM gets no password, and the vault's become password is never sent to it. No
  `image` entry point renders a secret or signs anything in: `tests/unit/test_golden.py` holds what each may read. An
  image option that holds a vault value means no golden is built or used, said by the option's name and never its value.
- **The build VM is outside the fleet.** It carries `<tag>-build` and never the ownership tag, so no command that finds
  VMs by that tag can reach it or sign it in.
- **Nothing signed in survives the scrub.** Each identity is deleted (Docker's, tailscaled's state, SSH host keys, the
  user's `authorized_keys` and root's, `~/.claude`), and the build fails if any of those keys or a shell history, a gh
  login, a Claude credential or settings file, a joined Tailscale or a Remote Control unit is still there.
- **What is accepted.** The guest shutdown before templating writes `/var/lib/systemd/random-seed` back, so clones start
  from the same seed. Each mixes it with fresh entropy at boot and systemd does not credit it to the kernel, so it adds
  nothing an attacker can use, and deleting it would need the disk mounted on the Proxmox host, which the project never
  touches.
- **The key is not secret, and holds none.** It sits in the golden's notes, where anyone who can read the VM's config
  can see it, which is why no option that feeds it may hold a secret.

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

The project writes exactly three things outside the repository, and has VS Code's own CLI write a fourth and
Claude Code's own CLI a fifth:
`~/.ssh/known_hosts` and the managed SSH config, plus one `Include` line at the top of
`~/.ssh/config` ([§4.8](lifecycle.md#48-playbooksssh_configyml-and-playbookstasksssh_configyml)); and the
checkout's own folder (`tools/env-run --data`), where `just install` puts the Galaxy collections and, when you
bring no virtualenv, the Python environment ([§13](toolchain.md)) - and, only where you point
`destroy_sessions_dir` outside it, the Claude Code sessions `just destroy` saves
([§4.5](lifecycle.md#45-playbooksdestroyyml--confirm-release-delete)). The fifth is the MCP server's entry in
`~/.claude.json`, which `just mcp` has Claude Code's CLI add ([Agents through MCP](#agents-through-mcp)). The table is
the one list of them, which
the session notes of every VM show too (**Written on your machine**,
[§4.9](lifecycle.md#49-playbookscodeyml)), with what each editor keeps of the windows `just code` opens and what
the tools keep for themselves:

<!-- BEGIN_GENERATED: controller-writes -->
<!-- Generated by `just docs` from playbooks/vars/controller_writes.yml: edit that, not this section. -->

| Where | What | Written by | When | Taken out by |
|---|---|---|---|---|
| `~/.ssh/known_hosts` | each VM's SSH host key, and the Proxmox host's | ssh, on first contact (`StrictHostKeyChecking=accept-new`) | every run that reaches a VM or the Proxmox host | `just destroy`: the VM's lines, never the Proxmox host's |
| `~/.ssh/agent-warehouse.conf` (`claude_ssh_config_file`) | one `Host` block per VM, so `ssh <vm>` reaches it, recording the host the VM is on | `just deploy`, `just code` (`playbooks/tasks/ssh_config.yml`) | `claude_ssh_config` on (the default) | `just deploy --only ssh-config` prunes a VM that is gone from its host; `just destroy` the VM's |
| one `Include` line at the top of `~/.ssh/config` (`claude_ssh_user_config`) | makes ssh read the file above | the same, once | `claude_ssh_config_include` on, while ssh does not reach the file yet | nothing: it is yours to remove |
| `~/.local/share/agent-warehouse/checkouts/<folder>-<checksum>` (under `$XDG_DATA_HOME` when set) | this checkout's Galaxy collections, and its Python environment when you bring no virtualenv | `just install` | always | `just clean env` |
| VS Code's Remote - SSH extension, in `~/.vscode/extensions/` | Microsoft's extension, from VS Code's marketplace | VS Code's own CLI, when `just code` finds it missing | `editor: vscode` | VS Code |
| your editor's own records of the window | its recent windows and its remote connections' state, in its own data folder | the editor itself, when `just code` opens a window | every window | the editor |
| the `agent-warehouse` MCP server's entry in `~/.claude.json` | what lets Claude Code sessions in every project, or the one `--project-dir` names, run these commands | Claude Code's own CLI, when you run `just mcp` | only after `just mcp` | `just mcp --remove` |
| `destroy_sessions_dir` | the Claude Code sessions `just destroy` saved from a VM | `just destroy` | only where you point it outside the checkout (by default `.sessions/`, inside it) | you |

- Ansible's own `~/.ansible/tmp`, its SSH connection sockets in `~/.ansible/cp`, and `~/.ansible/galaxy_cache`.
- uv's cache, `~/.cache/uv`, and the Python it downloads for the checkout's own environment, in `~/.local/share/uv/python`.
- For contributors: pre-commit's `~/.cache/pre-commit`, ansible-lint's `~/.cache/ansible-compat`, Molecule's `~/.cache/molecule`, and the Docker images the scenarios build.
- Not files: the sign-in pages opened in your browser, and GitHub's device code put on the clipboard.

<!-- END_GENERATED: controller-writes -->

The sessions `just destroy` saves are the VM's writing and are unpacked as plain files and folders only, never a
link the VM made; without `destroy_sessions_dir` they go into the checkout's git-ignored `.sessions/`. The
fourth is VS Code's own: when VS Code lacks the Remote - SSH extension, `just code` (and `just deploy`'s window
step) runs `code --install-extension ms-vscode-remote.remote-ssh`, and VS Code's CLI writes that extension,
Microsoft's, from VS Code's own marketplace into VS Code's extensions folder; nothing else of VS Code's is
changed. `tests/unit/test_controller_writes.py` holds the list to the code: a new place that makes one of
these writes, or a write without a row, fails it. Every
alias write is validated by the real ssh client before it lands, an
alias the user's own config already names is refused, and the Molecule scenarios point the managed
file into the ephemeral directory with the `Include` off, guarded by a unit test, because the
`configure` scenario's `localhost` is the developer's machine. A bug there reaches every `ssh` on the machine,
and the VM chooses what goes in: its address is what its own guest agent reported, which `guest_ipv4` takes only
when it parses as one IPv4 address, and a name or an address that is not one plain word is refused, since a
newline in either would add a directive (`ProxyCommand` runs here). Its name is Proxmox's, chosen by whoever
made it, and the managed file is included ahead of the user's own config: a name ssh would take for another host
gets no alias, refused when named and left out of a fleet-wide refresh, said, and a name with a dot gets one only
when the user names it ([§4.8](lifecycle.md#48-playbooksssh_configyml-and-playbookstasksssh_configyml)). Each block
records the host its VM was found on, and a run against one host never points, prunes or removes another host's
block: one host's listing would otherwise take another's VMs for gone, and its prune delete their aliases. A VM
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
[SECURITY.md lists](https://github.com/kawadeomkar/agent-warehouse/blob/main/SECURITY.md#known-trade-offs-not-vulnerabilities).

## Agents through MCP

`just mcp` registers `tools/mcp` with Claude Code ([§7.12](custom-code.md#712-libclaude_vmmcp--the-mcp-server)), so an
agent in any of the user's projects - with whatever permissions the user gave it there - reads this project's output
and calls its commands. What that changes:

- **What a VM printed reaches another project's agent.** A VM's user is root-equivalent and runs an agent of its own,
  which a repository cloned there can prompt, and what the VM chose - its name, its checkouts' branches, a failure's
  words - now lands in the context of an agent that may hold Bash and edit rights elsewhere. Every value a VM supplies
  is already made one line of plain text before `just status` keeps it ([§7.7](custom-code.md#77-libclaude_vmstatus-and-toolsstatus--just-status));
  the server hands text back through `visible_text` too, puts the note that it is data, never instructions, inside the
  JSON the model reads (Claude Code gives the model only a result's structured part when it has one), and returns a
  run log only when asked for one by name.
- **Nothing of the calling project's environment reaches a run.** A project's `.claude/settings.json` `env` reaches
  every MCP server Claude Code starts there (verified on 2.1.309): inherited, `ANSIBLE_CONFIG` would outrank
  `ansible.cfg`, `PYTHONPATH` would decide what `ansible-playbook` imports with `.vault_pass` in reach, `PATH` which
  `git`, `ssh` and `uv` run, and `ANSIBLE_ARGS` would add any `-e`. So the server's configuration is the arguments of
  the command `just mcp` recorded, a run's environment is an allow list with the `PATH`, `HOME` and `XDG_DATA_HOME`
  recorded there - the last two decide which collections Ansible loads - and every
  setting the justfile reads with `env()` is given on `just`'s command line, where it outranks the environment.
- **A value never meets a shell.** Each goes to `just` as one argv word, an option as `--long=value`, and `just` holds
  it to the option's pattern before the recipe's first line, as at a terminal; a word that starts with `-`, which
  `just` would read as an option, is refused before `just` runs.
- **What an agent can have cloned.** `deploy`'s `repos` puts names on a VM's own list with no dialog, as its size
  does. A name is only compared with what GitHub lists for `github_username`, so an agent can bring no other owner's
  repository onto a VM, while changing the fleet's list is a `settings_set`, which asks twice. The list narrows what is
  cloned, not what the VM can reach: `gh` there holds the same login as on any other VM.
- **No secret passes through the model.** No tool takes or returns one: there is no tool for the vault, `settings`
  says a secret set or not set, and `status` carries no token by its invariant.
- **Stdio only.** One server per session, which Claude Code starts and stops; no socket another process or a web page
  could reach. The only path the server takes from a call is a run log's name, held to the pattern
  `tools/run-logged` names logs by and joined under `.logs/`.
- **The checkout's branch.** The settings and the vault are one checkout's, which other sessions switch. Every result
  names the branch and commit, and `just mcp` registers only `main` with no tracked change unless given
  `--any-branch`. A pull, a branch or an edit that changes the justfile or the server's own code makes every tool
  answer "restart" rather than run a schema that no longer matches, or older code beside newer.
- **A server that cannot run says so.** It never exits at its start - Claude Code pins a stdio server that dies
  within 3 s to the older protocol for 7 days - and answers each call with what is wrong instead: its command line, a
  checkout without a justfile, a vault it cannot read with no terminal to ask at.
- **What an agent may delete or change.** `destroy`, `settings_set`, `init` and `install` carry Claude Code's
  `requiresUserInteraction` prompt, which no hook of the calling project's can answer and which `-p` and `dontAsk`
  deny (verified), and run only for Claude Code 2.1.309 or later. `destroy` and `settings_set` then show what is at
  stake in a dialog that wants a code with the names: a project's `Elicitation` hook can answer a dialog, so the
  prompt is the gate and the code keeps a generic hook from passing it. `just destroy --confirm` answers the question
  only for the VMs it names. `settings_set` writes only an allow list's settings - never where a credential is sent,
  what runs on every VM, what reaches this machine, or who reaches the VMs; a deny list drawn from the examples had
  missed the URLs the vault's tokens go to. Claude Code's version is `stable`, `latest` or a version: npm installs any
  spec given as one, another package among them, as root.
- **What an agent may start.** In auto mode, without an `ask` rule, Claude Code's classifier alone decides whether an
  agent deploys, signs in or opens a VM (verified), so `just mcp` prints `ask` rules for `deploy`, `login`, `code` and
  `cancel`, and for `Bash(just destroy:*)` and `Bash(just settings:*)`: an agent with Bash in another project could
  `cd` to the checkout and run them itself, which MCP's own gates never see. `install` asks every time, in Claude
  Code's own prompt, which no hook of the calling project can answer, and runs for no other client. A run takes the
  same lock as a terminal's ([One run at a time](../guides/runs.md#one-run-at-a-time)), and starts only from `main`
  with no tracked change.
- **Pages open only for a person.** A run opens the sign-in pages here only with `ATTENDED=1`: an interactive session on
  this machine, not over SSH, or `just mcp --attended`. Headless `claude -p` runs as a run with no terminal always ran.
- **One more write on this machine.** `just mcp` has Claude Code's own CLI write the server's entry into
  `~/.claude.json`, the fifth row of [On the controller](#on-the-controller); the server itself writes nothing.

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
