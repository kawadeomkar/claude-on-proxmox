# 15. Design decisions

Questions that were open, the answer each was given, and the facts that decided it, so that none has to
be worked out again. A question that comes up and is not settled yet goes here too, under a heading of
its own marked open, until it is.

## 15.1 Discovery stays a playbook, not the inventory plugin

*Decided October 2026, against `community.proxmox` 2.0.0, the pinned version, and 2.1.0, the latest, with
ansible-core 2.21: both read in their source and run against a fake Proxmox API.*

`community.proxmox` ships an inventory plugin, `community.proxmox.proxmox`, that can do the job of
`playbooks/discover.yml`. It lists the cluster and reads each VM's guest agent; its `compose` can take the
address on `net0`'s MAC, as `tasks/vm_addresses.yml` does, and a filter can keep the VMs tagged
`agent-warehouse` as a whole tag. It never sees `group_vars`, but its URL and token take lookups, so it
could read the address from `inventory/hosts.yml` and the token from the vault rather than have them
declared a second time. It is not used, because:

- **Little would go.** `tasks/cluster_vms.yml` and `tasks/vm_addresses.yml` stay whatever discovery
  does: `provision.yml`, `ssh_config.yml`, `destroy.yml` and `code.yml` read the cluster at
  moments an inventory parsed as a run starts cannot see, such as a VM provisioning has just cloned or
  one `destroy` has just started. The plugin would replace `discover.yml` alone, and its checks would
  come back as a play after it: a VM named on the command line that was not found, two VMs with one name, a
  refused token, an empty fleet. Two ways of finding a VM's address would then have to agree.
- **A failure is a warning.** ansible-core turns any error in an inventory plugin, Proxmox not
  answering or the token refused among them, into "Unable to parse ... as an inventory source", keeps
  what was parsed before it, and the run goes on, its plays matching no hosts, to exit 0.
  `[inventory] unparsed_is_failed` fires only when no source parsed at all, and `inventory/controller.yml`
  always does. `any_unparsed_is_failed` makes the error fatal, but for every command, whenever Proxmox
  cannot be reached.
- **Some losses say nothing.** The VMs of a node that is offline are left out without a word. Two VMs
  with one name become one host, the later one's variables over the earlier one's, and on 2.1.0 one VM's
  ID can end up beside the other's address; `discover.yml` refuses to go on
  ([§9](failure-modes.md)).
- **Every command would call Proxmox.** Each `ansible-playbook` parses the inventory before anything
  else: `just lint syntax` would call the API once for each playbook it checks, ansible-lint would
  too, and so would CI, which has neither Proxmox nor the vault password. So would the commands that only
  need SSH to a VM. 2.0.0 sets no timeout on its requests; 2.1.0's `api_timeout` is 5 s.
- **It makes four API calls per VM** (its status, its configuration, its snapshots and its agent's
  interfaces), where `tasks/vm_addresses.yml` makes two.

Its one gain, a `--limit` that works, is not one here: the names after a recipe select the VMs, and are
checked before anything runs.

## 15.2 The guest agent starts where its port is

*Decided October 2026, against Ubuntu 24.04's `qemu-guest-agent` 8.2.2 and ansible-core 2.21.*

`common` used to start the services in `common_enabled_services` only where `virtualization_type` was
`kvm`, which was meant to say "a VM with a guest agent", and does not:

- **The agent needs its port, not KVM.** Proxmox gives a VM the virtio-serial port
  `org.qemu.guest_agent.0` while its agent is enabled, as the default `virtio` type, and the VM's udev
  links it as `/dev/virtio-ports/org.qemu.guest_agent.0`. The package's unit is static and bound to
  that device (`BindsTo=`): udev starts the agent when the port appears, and `systemctl start` without
  the port waits for the device, 90 s by systemd's `DefaultDeviceTimeoutSec`, then fails.
- **`kvm` is not the port.** ansible-core reports `kvm` on any guest whose firmware says QEMU, port or
  none, such as a Proxmox VM with its agent off or a cloud VM, and `kvm` with the role `host` on bare
  metal with the `kvm` module loaded, as it is on most machines with hardware virtualisation. Each of
  those failed the run after the wait. VirtualBox reports `virtualbox`, so the Vagrant VM started
  nothing from the list, a service a user added included.

So the agent starts only where its port exists, and every other listed service wherever systemd runs
([§5.3](roles.md#53-common--os-baseline)). The VMs this project creates always have the port:
`proxmox_vm` enables the agent.

## 15.3 The command line is just, not make

`make` takes no options of a target's own: `make init --host x` fails, and `make init -- --storage
test` runs the `test` target. So the `Makefile` grew an argument language of its own on make
variables - `TYPE=`, `VM_NAME=`, `TAGS=`, `ROLE=`, `PROJECT=`, eleven of `init`'s - and about two hundred
lines of parse-time guards against a stray word becoming a target, a VM name or an expansion. [`just`](https://github.com/casey/just)
recipes take options and words of their own (`just deploy alpha --tags claude_code`, `just init --address
pve.lan --no-prompt`), each held to a pattern before anything runs, with help that `just help <recipe>`
and the [Commands](../reference/commands.md) page read from the recipe itself. What each command does
did not change.

How the justfile is built, and why (each tried on just 1.59.0, on macOS and in `ubuntu:24.04`):

- **No recipe has a dependency.** `JUST_NO_DEPS` in the environment, or `--no-deps`, skips every one,
  so a check written as one could be switched off, and its refusal would come after just had evaluated
  what the recipe uses. Every check is a line of the recipe's own script instead: the checkout guard,
  then the recipe's rules, then `tools/env-run --bin`, `tools/check vault` and `tools/check collections` (which
  `status`'s engine runs instead, only when it asks Ansible, [§4.6](lifecycle.md#46-toolsstatus--read-only-drawn-live)) -
  and `tools/check settings` where the recipe reads the settings - then the work. A recipe that includes another's work (`test` lints, `init` installs) runs it as `{{ self
  }}`, which passes on every setting, since a recursive just starts without the command line's `BIN=`.
- **No assignment starts a program.** just evaluates every assignment a recipe uses, and the recipe's
  own `{{ }}`, before its first line runs: a `shell()` there would start before any refusal, and a
  `path_exists()` there reads the file as it was before the recipe ran - `init`'s checks would miss the
  `.vault_pass` its questions had just made. So those are decided by the shell, when the line runs.
- **Patterns for characters, the script for rules.** A pattern is checked against every value of a
  variadic parameter, and against its default, anchored at both ends; it is literal, since one naming an
  assignment could be loosened by overriding that assignment on the command line. A pattern refusal
  quotes the regex, so the rules between options - `--tags` beside names, `--check` with `--only` - and
  their messages are the script's. A value reaches the script as a positional argument, never as its
  text, and what goes into JSON is checked again there.
- **Bash, keeping to 3.2.** Ubuntu's `sh` is `dash`, which dies of a Ctrl-C that the playbook it waits on
  has caught and survived - Ansible's `pause`, Ctrl-C then C - so the run would end with exit 130 even
  under `[continue]`; bash waits for the playbook and carries on. macOS's `/bin/bash` is 3.2, under which
  `"${a[@]}"` of an empty array is fatal with `-u`, so the scripts build argument lists with `set --`.
- **The checkout guard.** just looks for a justfile upward and does not stop at a checkout's `.git`, so
  started in a worktree of a branch without one - under `.claude/worktrees/` - it would run this checkout's
  recipes, with its inventory and vault. `tools/check checkout` refuses that, comparing git's top level of
  where just was started with git's of this checkout: never the shell's `pwd`, which keeps a directory in
  the case it was typed in, while macOS's file system takes any case and git gives the disk's.
- **A catch-all on `init`.** A word after a recipe that takes none becomes the name of a recipe to run,
  and just prints it when there is none: `just init TOKEN_SECRET=...`, make's spelling, would print the
  secret. `init` takes the words after its options as `EXTRA`, which it refuses without saying them.
- **Every word is a variadic parameter.** A parameter with a default lists as `help RECIPE=''` - make's
  spelling, which just refuses after the recipe - and leaves the word after it to run as a second
  recipe: `just help deploy test` showed `deploy`'s options and then ran every test. So a recipe's words
  are a `*` parameter, named in the plural where it takes several and in the singular where it takes one
  (`help`, `code`, `docs`, `vagrant`, `clean`), whose script refuses a second; the listing reads
  `docs *PART`, and [Commands](../reference/commands.md) `just docs [<part>]`.
- **VM names start with a letter or a digit.** A recipe without options takes `--project` as a word, so a
  name may not start with `-`.
- **just is the user's, from its own releases.** PyPI's `rust-just` would have let `uv.lock` pin it, but
  it is a third party's own build of just: its binaries are not upstream's, and every bump would bring a
  new one onto the machine that holds the vault password. So just, like uv, is installed by the user, at
  least the version the justfile's `minimum-version` names, and CI installs upstream's archive checked
  against a pinned SHA256 ([§13](toolchain.md)).
- **What was dropped.** The `Makefile`, with its refusal of a target's name among VM names (just takes
  every word after a recipe with a variadic parameter as one of its values, and runs no second recipe),
  of names given both ways (there is no `VM_NAME`), of `$(...)` that make would expand (just expands
  nothing it is given), and the table of the targets an earlier version had: the project had one user, and
  [Upgrading](../guides/upgrading.md#make-became-just) maps every old command.
- **What just does not do.** Shell completion completes recipe names, not their options, and
  `just deploy --help` is an unknown option: `just help deploy` is the way in.

## 15.4 More than one editor and hypervisor, one of each at a time

*Decided November 2026, after 1.0, for 1.x releases: each step only adds, as the promise in `CONTRIBUTING.md`
asks of a minor version.*

The project is to open more than VS Code (Zed first) and to run on more than Proxmox VE (Unraid later), each the
user's choice. What was decided, and why:

- **One editor at a time**, VS Code by default: the `editor` setting, or `just code <vm> --editor <editor>` for one
  run. Each editor's work on this machine is a check of `code.yml`'s own (`tasks/vscode_check.yml`, before anything
  is written on the VM), imported statically for that editor alone, with its variables in a vars file
  (`playbooks/vars/vscode.yml`), and its family's plan of the window in the core (`lib/claude_vm/editors/vscode.py`),
  which `tasks/editor_open.yml` applies for every editor
  ([§7.10](custom-code.md#710-libclaude_vm--the-controllers-python-as-one-package)). Static, so `--tags` reaches
  them and the allowlist in
  `tests/unit/test_code_session.py` follows them; every name an editor's file registers starts with `code_<editor>_`,
  since a skipped task still overwrites its register. The editors are one internal list, `claude_editors` in the
  inventory defaults - each one's name, whether its window runs just code's tasks as it opens, and how it gets its
  Claude terminal (`claude_terminal`: `started` as the window opens, `listed` for the user to pick, or `none`), every
  value a literal - which `code.yml`, `deploy.yml` and the notes read, and a test holds to the editors' files and to
  `--editor`'s pattern. VS Code's helpers (`playbooks/files/`) are VS Code's: they take what only its tasks carry.
  Making them any editor's would need a notes refresh `just code` runs on the VM, a new command there, worth it once
  a second editor can run them. One helper is every editor's: `claude-start.sh`, the Claude terminal of a task the
  user starts, which never starts a second `claude` on a conversation one already has open.
- **Zed opens the folder, and Claude is a task the user starts** (decided 3 November 2026, D11). `just code` runs
  `zed ssh://<alias><folder>` and writes two files for Zed, in its own directory on the VM, only where that
  directory is inside the projects directory: `.zed/tasks.json`, a **Claude (just code)** task that Zed lists in a
  window whose folder holds the directory, the projects directory's by default, and the shared helper it runs. A
  spawn opens a terminal of its own, so it never replaces a running Claude. Zed runs no task when a window opens
  (its one hook is a Git worktree's creation), and its other tasks live in a repository's `.zed/` or in the user's
  own settings, where the project writes nothing. Two costs, both disclosed in the guide: the projects window no
  longer lists the repositories' `.vscode/tasks.json`, which Zed leaves out of a window with a `.zed` tasks file
  (a `--project` window still lists them); and once Zed holds a folder's tasks back until it is trusted (its change
  #66284, seen on Nightly 1.28.0), the task needs the projects folder trusted, which trusts every checkout in it -
  so the guide offers a task of the user's own, which needs no trust, and never advises trusting the projects
  window for the task alone. Zed's Agent Panel can host Claude (Claude Agent, over ACP).
  Run on this machine, that agent would have this machine's files and logins in reach rather than the VM's, so the
  panel was to be named only once it was seen to run on the VM, and it was (Zed 1.26.2): Zed's remote server installs
  the agent there and starts it with the VM's `~/.claude` and claude.ai login. The terminal still comes first, since
  the agent's Claude Code is its npm package's own, at the version Zed's registry installs.
- **No key and no tag of the project's** (D12). Zed's `task::Spawn` with a `task_tag` starts every task that
  carries the tag, from the user's own tasks file and from every `.zed/tasks.json` in the window, and Zed 1.26.2
  lists a folder's tasks in Restricted Mode: a tag the project published would be a trigger any cloned repository,
  or a Claude talked into it, could plant beside the project's task, run by one key outside Claude Code's
  permission prompts. So the generated task has no tag, the project binds no key, and the guide shows a task, a tag
  and a key the user keeps in their own Zed files, the tag a name of their own.
- **No timed second `zed` call for the notes** (D13). Zed's command takes one `ssh://` path, so the notes are
  opened by hand once, and Zed reopens the tab with the window. A second call once Zed's server runs on the VM
  has no signal to wait for - another window of the VM satisfies any check at once - and a lost race leaves a
  window of the notes alone, which Zed restores for good. The fix is Zed's: one command taking two `ssh://` paths of
  one host, an issue drafted for the user to file.
- **What any editor's code does, from 1.1 on**, so nothing written before the editors become a package of their
  own goes the other way: an editor's code returns data and the playbook's tasks perform every effect - no Python
  writes a file or starts a program around the allowlist `tests/unit/test_code_session.py` walks; no editor gets a
  key of its own in `just status --json`, every editor's windows one map by the editors list's keys beside the
  `vscode_windows` 1.0 promised; and the shared helper (`claude-start.sh`) and the ownership marker - the first
  line of every file in JSON with comments that `just code` writes for an editor to read from 1.1 on, a tasks or
  settings file (`.zed/tasks.json` today, any other editor's later),
  `// Written by just code (agent-warehouse); edits here are overwritten.`, without which such a file there is
  someone else's and left as it is - are any editor's, never one editor's names. The helpers and the notes, a
  script and a page, carry no marker: they are `just code`'s by where they live, its helpers' and its notes'
  directories.
- **Every VM is made ready for any editor alike.** What `vscode_server` sets up is not VS Code's alone: Claude's
  sign-in in the browser forwards its callback to the VM over `ssh -L` whichever editor is chosen, and the role's
  `sshd -T` check is what says when forwarding is off; Zed's port forwards are `ssh -L` too. It keeps its name and its
  files' names through 1.x.
- **What `just code` does for any editor is named so**: `editor_claude_terminal`, `editor_files_dir`,
  `editor_session_notes`, `claude_sessions_sync`. 1.0's `vscode_*` names stay read through 1.x, the new names fed from
  them, and `tools/check settings` names the new name while one is set ([Upgrading](../guides/upgrading.md#names-11-added)).
  The new name wins wherever both are set, `-e` of the old one included; `just settings` writes the new name and
  takes the old one out, so the two are never left side by side, one of them doing nothing.
- **One provider per run** (one hypervisor per checkout until 2.5, [§15.8](#158-a-host-is-an-inventory-source-not-a-variable)).
  A second hypervisor is a provider of its own, its VMs found and listed through entry points of its role, read by
  static imports as the editors' files are; the `just status --json` keys 1.0 promises, some of them Proxmox's
  (`vmid`, `node`, `proxmox`), stay for every provider. Every run reaches exactly one host, so one kind of host:
  one provider's plays, imported statically, still hold, and a checkout may hold hosts of more than one kind.
- **The names runs left behind move only with a migration** (decided November 2026, for 2.0, when the project
  became agent-warehouse): the tag, the markers and drop-ins on each VM, just code's helpers there - the shared one's
  path is in a task of the user's own - and its ownership marker, the managed SSH config's file and blocks, the
  checkout's folder. Each is read under its old name and written under its new one, the old one still read through
  2.x, so no deployed VM or machine is stranded. `tests/unit/test_persisted_names.py` lists them: a change to one
  fails it, and is the place to write that name's migration.
- **Coupling only shrinks.** `tests/unit/test_ratchets.py` holds how many lines name VS Code outside its own files,
  and how many read Proxmox's API outside its own roles, file by file, to what they are, and fails when one grows.

## 15.5 The VPN as a choice, beside the editor

*Decided 5 November 2026, for 1.x releases: each step only adds.*

The VPN each VM joins becomes the user's choice the way the editor did, and in the long term the project has three
such choices: the host's OS (Proxmox VE, and Unraid later), the VPN and the editor
([§7.11](custom-code.md#711-the-choices-the-editor-the-vpn-and-later-the-hosts-os)).

- **One VPN at a time**, the `vpn` setting: Tailscale or none. Unset, it follows `tailscale_enabled`, which 1.0
  promised and which stays read through 1.x; `vpn: none` is a value, not a member, since there is nothing to plan for
  it.
- **The pattern is the editor's, in two halves.** What a VPN decides on this machine is a plan of who is on the
  network, made by a family module and held by a registry to the ownership rules; what it does on a VM stays its
  role, imported statically per member. Rejected: a class hierarchy per choice (a module cannot inherit a default,
  so a new member of the seam fails every family at once, which is the check wanted); finding members at run time,
  by an entry point or a templated role name (the static walks of the tests need literal names, and a templated
  `include_role`'s tasks never run under `--tags`); one engine for every choice (a window, a service and a fleet
  share a shape, not a lifecycle); and the Tailscale role's VM-side decisions moved into Python, which read the VM's
  live state and keep the auth key from ever reaching a node that joined.
- **What a VM must not change about itself is kept by the host's OS**: on Proxmox a tag beside the ownership tag
  (`<tag>-tailscale` today), on Unraid later in the domain's metadata. A VPN says which marks to keep; it never
  calls the hypervisor's API itself.
- **The host's OS will be the setting `os`**, the third choice, `proxmox` by default.
- **No VPN unless one is chosen, and choosing one never removes another** (decided 9 November 2026, 2.1). A VPN
  the user runs that routes the LAN reaches the VMs with nothing here, so `none` is the default, which `just init`
  says before it asks, and `tools/check` warns of while unset. Taking Tailscale off a VM is by hand: through 2.0,
  `tailscale_enabled: false` logged each VM out and removed the package, and with the default turned to none every
  VM of a user who relied on the old default would have lost Tailscale at the next run - and the auth key is never
  sent to such a VM again, so each would have needed the web login. `just destroy` still releases a VM's
  Tailscale: the VM itself is going.
- **WireGuard was built in 2.0 and removed in 2.1** (decided 9 November 2026): each VM a peer of a WireGuard server
  the user ran, its peer added by hand or written through an OPNsense router's API. A VPN that already routes the
  user's LAN - WireGuard on the router, a Tailscale subnet router, any other - reaches every VM at its LAN address
  with `vpn: none`, at home and away, and every `just` command needs that route anyway, since Proxmox's API and
  Ansible use LAN addresses. WireGuard has no control plane: joining and removing peers without the user takes an
  adapter per server, each asking setup of the user, and by hand a peer added for every VM. Tailscale stays, since its
  per-VM join gives what a subnet route does not - a name per VM, reach where nothing routes the LAN, the VM a device
  of its own - for one auth key. Not to be built again unless a VM must be reached where no route to the LAN can be
  had.

## 15.6 Agents run the recipes through MCP

The question: how an agent in another project - "use agent-warehouse to deploy a VM for this repository" - runs this
project's commands, with every command behaving as it does at a terminal.

**Decided: a stdio MCP server that is a thin adapter over the justfile** (`tools/mcp`,
[§7.12](custom-code.md#712-libclaude_vmmcp--the-mcp-server)).

- **Over the justfile, not beside it.** Each tool runs one recipe as `just` would, its schema made from the recipe's
  own parameters, so every check, refusal and message stays in one place and a tool cannot drift from its command; a
  test fails on a recipe or an option added with no decision. Re-implementing the recipes in Python would be a second
  copy of every rule, and driving them at a pseudo-terminal, answering their prompts by scraping, would put a
  model's guess where the person's answer was.
- **Stdio, one server per session.** No socket another process, a browser page or a DNS-rebinding trick could reach,
  and nothing to run or keep up between sessions.
- **Registered once, for every project.** `claude mcp add --scope user` (local scope for one project with
  `--project-dir`), every value an argument of the recorded command, since a project's settings reach a server's
  environment. The entry in `~/.claude.json` is the one write it adds on this machine, made by Claude Code's own CLI,
  as VS Code's CLI writes its extension.
- **No tool for the vault, nor any secret.** The vault, the token secret, the Tailscale auth key and Claude's pasted
  code stay at the terminal, so no secret passes through the model.
- **The person confirms a deletion and a setting, in Claude Code's own prompt.** A dialog alone could be answered by
  a hook of the project the agent works in; `requiresUserInteraction`'s prompt cannot be, so it is the gate, and the
  dialog shows what is at stake. A client whose prompt was never seen gets the terminal command instead.
- **An allow list of settings, not a deny list.** `tools/settings` can set any role variable; a deny list drawn from
  the example files missed the URLs the vault's tokens are sent to and the installer every VM runs as root. What an
  agent may change is listed, and every other setting of the example is refused by name.

## 15.7 `just status` runs no playbook

Decided on measurements, in 190 runs of 19 versions on a homelab runner against a real Proxmox and two VMs, over a LAN
and a 28 ms tunnel, cold SSH each time. Today's playbook drew its first VM rows after 5.0 s and finished at 21 s, using
14.6 s of CPU; the proposal that replaced it draws rows at 0.23 s and finishes at 2.2 s, using 0.4.

- **Why not a faster playbook.** Ansible spends 0.3 to 0.6 s on every task of every host, whatever the task does, and
  the report is a few dozen small reads: at its best, one module per VM reading everything, with the fleet read in
  parallel, a playbook ended at 7.8 s. `async` made quick reads 2.6 times slower and writes on the VM; Mitogen gained
  nothing once the reads were one module, and third-party strategy plugins are deprecated in ansible-core; several
  Ansible processes gave worse rows than one. The floor is `ansible-playbook`'s own start plus 25 tasks per VM.
- **What it costs.** The roles' state is worked out a second way in `lib/claude_vm/status/found.py`, held equal to
  the roles' own by `test_status_vms.py`, state by state, in check mode; the lines the roles say became one function
  both call (`lib/claude_vm/status/lines.py`). The report's reads no longer pass through Ansible's `no_log`, so what
  guards a secret is what it never reads: the probe holds a token only to say its kind, inside the VM.
- **What was kept of Ansible.** The settings, asked of its own `VariableManager` and templar in the process, so no
  precedence is written again, and every other recipe.
- **Not to be built again** unless a report grows reads that need a module's guarantees (an idempotent write, a
  `become` with a fresh password prompt); `just status` is read-only by rule, so it should not.

## 15.8 A host is an inventory source, not a variable

*Decided 11 November 2026, for 2.5: each step only adds, and a checkout with one host sees no change.*

One checkout drives more than one Proxmox host - a homelab and a NUC - and moving between them was swapping
`inventory/hosts.yml` and the vault's token by hand, while a prune against one deleted the other's SSH aliases.

- **A host is a folder of its own settings, laid over the shared ones** ([§7.14](custom-code.md#714-libclaude_vmhosts-and-toolshost--the-checkouts-hosts)):
  `hosts/<name>/`, a second inventory source after `inventory/controller.yml`. With it, `groups['proxmox']` holds
  that host alone, so no playbook or role changes: Ansible's own precedence does the rest. Rejected: a variable
  naming the host in every playbook, which every read of the API, every `hosts: proxmox` play and every guard would
  have had to learn; and one run over several hosts, which every interlock - the name refusals, the ownership tag's
  checks, the token - would have had to learn per host.
- **The first host stays where it is**, in `inventory/`: no migration, and a checkout of one host sees nothing new.
  Moving every host under `hosts/` is a change to the resolver alone, should it be wanted.
- **One resolver knows the layout** (`tools/check host`): every recipe asks it for its run's host and the files and
  sources that host is read from, never a path of its own.
- **Nothing of the first host's falls through to another.** Its token's secret by a committed rule keyed on where
  Ansible recorded the host, with no variable to switch it off - run on fake values against two fake APIs before it
  was written - and every setting that describes one machine or one network required in, or refused missing from,
  the host's own files.
- **The current host is the terminal's**: `just host <name>` switches it, under the run lock, and no agent changes it;
  `--host` on a command reaches another once.
- **Each alias records its host** (`# origin: <host>`), and a run prunes, re-points and removes only its own host's,
  so a listing of one host never takes another's VMs for gone.
