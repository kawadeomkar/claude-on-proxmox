# 5. Roles

Eight roles, no inter-role dependencies (`dependencies: []` everywhere). Ordering is the playbook's
job.

## 5.1 `proxmox_template` — build the golden image

Runs on the PVE node over SSH. Everything is guarded by a `qm config <vmid>` probe: a VMID that
exists but is *not* the finished template (a half-built earlier run, or an unrelated VM) is an error
rather than something to skip silently. The probe reads this node's configs only, so a VMID held by
a sibling node looks free to it; before building, the role reads pmxcfs's cluster-wide
`/etc/pve/.vmlist` and refuses a VMID another node holds, naming that node. Without that the build
ran to `qm create` - after the download and `virt-customize` - and died there with
`VM 9000 already exists on node 'pve2'`.

1. Download Ubuntu Noble's minimal cloud image, checksummed against Ubuntu's published `SHA256SUMS`, into
   `proxmox_template_image_dir` (`/var/lib/vz/claude-on-proxmox`, created `0755`). Not a content
   directory of the `local` storage: under `template/iso` Proxmox listed the image as an ISO. The
   minimal image has 288 packages to the server image's 426: no multipathd, fwupd, ModemManager,
   udisks2, polkit or rsyslog, which a headless VM has no use for, and the same kernel package. What a
   development VM needs on top, `common` installs.
2. If `proxmox_template_install_guest_agent` (default true), **copy** the image and run
   `virt-customize --install qemu-guest-agent --truncate /etc/machine-id` on the copy. The pristine
   download is left untouched so `get_url` stays idempotent.
3. `qm create` with `--agent enabled=1`, virtio NIC, virtio-scsi, serial console.
4. `qm set --scsi0 <storage>:0,import-from=...,discard=on`, then add the cloud-init drive and boot order.
5. `qm template`, then remove the customised copy: `import-from` has copied the disk into the
   storage, so it is derived data a rebuild makes again, and it was ~600 MB per node otherwise.
   The download stays. Without the guest agent the import path *is* the download, which is kept.

Two non-obvious details, both load-bearing:

- **`dhcpcd-base` is installed alongside `libguestfs-tools`.** It is not pulled in on Proxmox 9, and
  without it the libguestfs appliance cannot DHCP, so `--install` fails with
  `Temporary failure resolving 'deb.debian.org'`.
- **`--truncate /etc/machine-id`.** Clones sharing a machine-id send the same DHCP client identifier
  and can be handed the same lease.

## 5.2 `proxmox_vm` — the core role

The only role that talks to the Proxmox API. All API parameters come from `module_defaults` on the
`group/community.proxmox.proxmox` action group, and the whole block is `delegate_to: localhost`.

**`tasks/main.yml`** — four validation asserts, split by condition on purpose (a single catch-all
message once sent someone with a mistyped VM name off to check their API token): connection
settings, VM name, login credentials, VM ID. `proxmox_vm_state` is *not* asserted here because
`meta/argument_specs.yml` declares its `choices`, validated before the first task runs. Then: look
the VM up by name (with its config), refuse a duplicated name, record existence, then either refuse
an existing VM that is a template or lacks the tag and branch to `present.yml`, or run the ownership
check and `absent.yml`.

**`tasks/present.yml`**

| Step | Notes |
|---|---|
| Clone | `throttle: 1` only when Proxmox picks the VMID; a fleet's VMIDs are pre-assigned — see [§9](failure-modes.md) |
| Assert `clone is changed` | a VMID collision returns `changed=False` and the *template's* VMID |
| Record VMID | `when: not proxmox_vm_exists` |
| Tag the new VM | its own call, straight after the clone — see [§8](convergence.md) |
| Apply hardware + cloud-init | cores, memory and its minimum (`balloon`, from `proxmox_vm_memory_min_mb`, equal to the memory unless set: Proxmox reads 0 as no balloon device, which would also stop the VM reporting its memory use); `when: proxmox_vm_needs_configuration`; to `proxmox_vm_found_node`, the node the lookup found the VM on, as is every call after the clone — a migrated VM is no longer on `proxmox_vm_node` |
| Resize root disk | same condition; `proxmox_disk` no-ops when already the right size |
| Start | `when: needs_configuration or proxmox_vm_start_existing` |
| Mark finished | removes `claude-on-proxmox-unfinished`, which the settings call added — see [§8](convergence.md) |
| Read NIC config | only for a VM this run cloned: `until` non-empty, `retries`/`delay`, `failed_when: false`; an existing VM's config is the one the lookup fetched (`proxmox_vm_found_config`); then a dedicated assert |
| Wait for guest agent | polls until an IPv4 appears, breaking early on 401/403 |
| Record address | `set_fact proxmox_vm_address`, then assert non-empty |

The read-then-assert pairs exist so a failure reports *what to fix* (check that the API token holds
`VM.Monitor` on PVE 8, `VM.GuestAgent.Audit` on PVE 9) instead of Ansible's generic retry timeout.

**`tasks/absent.yml`** — ask the agent for the address *while the VM still exists* (the NIC's MAC comes
from the config the lookup already holds), stop, delete
with `force: true` (which also covers a VM restarted between the two calls) and
`destroy_unreferenced_disks: true` (a disk that still carries the VMID but that the configuration no
longer lists goes with the VM - these VMs are deleted often, and the tag check has already
established the VMID is this project's), then one housekeeping
step: remove the address from `known_hosts`. It matters because names and DHCP leases are reused,
and a stale host key produces `HOST KEY VERIFICATION FAILED` with no hint why. If no address could
be determined, a warning says so and names the `ssh-keygen -R` fix. There are no cached facts to
clear: the project has no fact cache ([§13](toolchain.md)).

**`vars/main.yml`** holds the role's lazily-evaluated internals — see [§6.3](variables.md#63-computed-internals).

## 5.3 `common` — OS baseline

apt deletes each package it downloads once installed → apt cache refresh → optional safe upgrade →
baseline packages (~26, including `qemu-guest-agent` and `python3-debian`) → empty apt's download
cache of what was left → timezone → hostname → user, groups and passwordless sudo → authorised keys
→ sshd hardening → `~/.local/bin` on PATH for login shells → the same directories on PATH for ssh
commands, through an sshd drop-in checked with `sshd -T -C` → enable and start services → mask the
units a headless VM has no use for.

Notable choices:

- **Timezone via `/etc/localtime` + `/etc/timezone`**, not `community.general.timezone`, so it also
  works in containers with no `timedatectl`/dbus.
- **Every toggle is a pair of tasks** (`Grant`/`Revoke` sudo, `Harden`/`Remove` sshd). `template` has
  no `state: absent`, so this is the idiom for a reversible setting.
- **`validate:` on the sudoers and sshd templates** — `visudo -cf` and `sshd -t -f`, so a bad render
  cannot lock anyone out. `sshd -t` needs `/run/sshd`, which is created first because it does not exist in a
  fresh container.
- **`lock_timeout: 600`** on every apt call: fresh cloud images run `unattended-upgrades` right after
  first boot, and the module's 60-second default is not enough.
- **apt keeps no package it downloads** (`common_apt_keep_downloads: false`). apt-get keeps every one -
  only the interactive `apt` deletes them - and a VM just deployed held 414 MB of them, cached in its
  memory as well. A drop-in in `/etc/apt/apt.conf.d` sets `APT::Keep-Downloaded-Packages "false"` before
  the role's first apt call, so every apt-get after it, the later roles' too, deletes each package once
  installed, and `APT::Periodic::CleanInterval "1"` empties the cache daily of what `unattended-upgrades`
  leaves, which installs through python-apt and keeps them regardless. What a first boot left goes once,
  through `apt-get clean` for its lock, after the installs: the clean makes apt's lock file in
  `/var/lib/apt/lists`, which dates lists that are not there as fresh, so before the refresh it made the
  refresh skip them.
- **Units are masked, not removed** (`common_masked_units`). A masked unit stops, and no socket, timer or
  D-Bus activation starts it again. Removing the packages is not safe: `multipath-tools` is a dependency
  of `ubuntu-server`, so removing it takes the metapackage too, and `apt autoremove` then what came with
  it. Only the units the VM has are masked, so the minimal image, which has snapd alone of them, gets no
  masks for the rest; and snapd only while no snap is installed, since a snap installed by hand stops
  working without it. Each is stopped before it is masked, timers and sockets before the services they
  start: `systemd_service` masks before it stops, a timer masked while it waits fails ("Unit to trigger
  vanished"), and the stop then skips it, leaving the VM `degraded`. A unit among them left failed is
  cleared with `systemctl reset-failed`.
- **The guest agent starts only where the VM has its port** (`common_guest_agent_port`,
  `/dev/virtio-ports/org.qemu.guest_agent.0`). Proxmox gives a VM that port while its agent is enabled,
  and the agent's unit is bound to it (`BindsTo=`), so a start without it waits 90 s for the device and
  fails. The machine type cannot tell: `virtualization_type` is `kvm` on a KVM guest without the port
  and on bare metal with the `kvm` module loaded, and not `kvm` under VirtualBox, which has no port. The
  other `common_enabled_services` start wherever systemd runs, containers included. See
  [§15.2](decisions.md#152-the-guest-agent-starts-where-its-port-is).
- **`~/.local/bin` is on PATH before it exists** (`/etc/profile.d/local-bin-path.sh`). Ubuntu's own
  `~/.profile` adds it only when it is there at login, so the first tool pipx or `uv tool` installs into
  it would not run until the next login - on a VM whose Claude Code came from npm, which installs into
  `/usr/bin`, nothing else makes it. Claude Code takes its shell snapshot with a login shell (`$SHELL -c -l`) when a
  session starts, so the same goes for a session Remote Control already started. A directory on PATH that
  is not there costs nothing. `dev_tools` does the same for `~/.cargo/bin` ([§5.4](#54-dev_tools--developer-tooling)),
  and ssh commands get both from the drop-in below.
- **`ssh vm <command>` gets the same directories from sshd** (`common_ssh_path_dirs`,
  `/etc/ssh/sshd_config.d/15-claude-on-proxmox-path.conf`). A command given to ssh runs in a shell that is
  neither login nor interactive: Debian's bash reads `/etc/bash.bashrc` and `~/.bashrc` for it, both return at
  once, so `/etc/profile.d` never runs and PATH is `/etc/environment`'s. sshd applies `SetEnv` after PAM's
  environment, so `SetEnv PATH=` under `Match User <vm_user>` wins for that user's sessions alone - root's, and
  sudo's `secure_path`, are untouched - and edits nothing of the user's. A block at the top of `~/.bashrc` would
  work too, but a dotfiles manager can remove it, the user can move it below the early return, and a switch to
  zsh skips it, each without a word. `SetEnv` takes no variables, so the drop-in holds the whole PATH: the
  directories in the user's home from `getent`, then the PATH line of `/etc/environment` as it is. sshd keeps the
  first `SetEnv` that applies, a `Match` block's replacing any global one, so the role asks `sshd -T -C` what
  applies to the user's connection, as `vscode_server` does ([§5.7](#57-vscode_server--what-vs-codes-remote---ssh-needs-on-the-host)),
  and fails naming the file that sets another. `~/.cargo/bin` is listed whether or not `dev_tools` installs
  Rust.
- **Ansible's modules run with the VM's `/usr/bin/python3`** (`claude_vm_python`, given to each VM by the
  `add_host` that publishes it), never a discovered interpreter. Discovery runs `command -v python3.14`,
  `python3.13` and so on over the connection and takes the first hit, and with `~/.local/bin` first on that
  PATH it would take a Python `uv python install` put there: one without `python3-apt`, run as root under
  `become`.
- Host-level settings are skipped under `common_container_virt_types`.

## 5.4 `dev_tools` — developer tooling

Feature-toggled: Node.js (NodeSource), Docker Engine, GitHub CLI, uv, and Rust (rustup), the last off by
default (`dev_tools_install_rust`). Each
apt-repository task follows the same shape:

```yaml
deb822_repository:
  signed_by: "{{ lookup('ansible.builtin.file', 'docker.asc') }}"   # vendored, not downloaded
register: dev_tools_docker_repo
apt: { update_cache: "{{ dev_tools_docker_repo.changed }}" }        # refresh only when it changed
```

**Signing keys are vendored** in `roles/dev_tools/files/`, so a swapped download endpoint cannot
supply its own key. **uv is pinned by version and SHA-256 per architecture**, with the digests stored
in `defaults/main.yml` rather than fetched alongside the archive, so a compromised release endpoint
cannot swap both. Node.js is apt-pinned to NodeSource over the distro package.

**Rust, where `dev_tools_install_rust` turns it on, comes from rustup, never Ubuntu's `rustc` and
`cargo`.** Noble's `rustup` package conflicts with
both (its proxies in `/usr/bin` take their names), and its `rustc` is 1.75. rustup installs
`dev_tools_rust_toolchain` for `dev_tools_user`, under `~/.rustup`, with the minimal profile and
`dev_tools_rust_components`, as that user with `HOME` set from `getent`. The role decides from
`rustup component list --installed`, which reads the installed toolchain's manifest offline: the toolchain
is installed only when it is missing, and only components the list lacks are added. It never re-runs
`rustup toolchain install`, which syncs the channel over the network and updates the toolchain - that is
`rustup update`, the user's to run. A new toolchain becomes the default only when none is set, so a
default chosen by hand stays. `/etc/profile.d/cargo-bin-path.sh` puts `~/.cargo/bin`, where
`cargo install` puts what it builds, on PATH for login shells, before it exists, as `common` does for
`~/.local/bin` ([§5.3](#53-common--os-baseline)): rustup from Ubuntu's archive never makes it. ssh commands
get it from `common`'s sshd drop-in.

## 5.5 `claude_code` — install Claude Code

`getent` resolves the target user's home (tilde expansion for another user is not reliable), then one
of two install paths:

- **native** (default): probe `claude --version`; install when missing, or when `claude_code_version`
  is an exact `x.y.z` that differs from what is installed. `stable`/`latest` self-update, so they are
  installed once and never reinstalled. The installer script is downloaded to a tempfile in a `block`
  with an `always:` cleanup.
- **npm**: `community.general.npm` with `version: omit` for `stable`/`latest`.

### Managed settings

`settings.yml` merges managed settings *over* whatever is on the VM (`slurp` with
`failed_when: false`, `combine(recursive=True)`), so a user's own edits to `~/.claude/settings.json`
survive a re-run. Every task there is `no_log: true` because the API key passes through.

A merge can only add, so the role owns `env.ANTHROPIC_API_KEY` explicitly: when nothing managed
sets it, a key found on disk is stale and is removed, along with an `env` block it leaves empty.
Without that, clearing `claude_code_anthropic_api_key` changed nothing on the VM — the key an earlier
run wrote went on outranking the claude.ai login. The file is therefore read on every run, even with
nothing to manage, but parsed only when something is managed or its text contains the key — so with
nothing managed, a hand-broken file the role has no business with does not fail the run. Once
something is managed the file has to be parsed: an empty or whitespace-only one counts as `{}`, and
one that is not a JSON object fails in a `block` whose `rescue` names the file, rather than as a
`from_json` traceback hidden by `no_log`. A key set through `claude_code_settings.env` counts as
managed and stays.

### Remote Control

`claude_code_remote_control: true` additionally runs a Remote Control server on the VM, so its
session appears at claude.ai/code and in the Claude mobile app while executing on the VM.

This is the one part of the project that cannot be fully automated, and the reason is a hard
constraint rather than an omission. Anthropic supports exactly one credential for Remote Control:

| Credential | Model requests | Remote Control |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | **no** — "API keys are not supported" |
| `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` | yes | **no** — "can only make model requests, so it can't establish Remote Control sessions" |
| Interactive claude.ai `/login` (Pro, Max, Team, Enterprise) | yes | **yes** |

It is on by default, and what happens is **derived from the credentials on the VM** rather than from
a switch — because no switch can create the one credential that works. `just deploy --only configure` therefore
does one of three things:

| Credentials found | Outcome |
|---|---|
| `ANTHROPIC_API_KEY` configured | Off, with a message. A key outranks the login |
| No key, no claude.ai login | Unit installed, service left stopped, a path unit waiting for the login (below), the sign-in command printed |
| No key, claude.ai login present | Service enabled and started; the session appears in the app |

A login is what activates it: `just login --only claude` and `just deploy` start the server in the run that
signs the VM in, and a login made any other way starts it through the path unit.

### Remote Control, step by step

The role automates everything around the login and stops at it:

1. **Turn it off when an API key is configured**, or when `claude_code_remote_control` is false.
   `ANTHROPIC_API_KEY` outranks the subscription login in Claude Code's credential precedence, so
   configuring both would silently authenticate as the API account and the server would refuse to
   start. This is not a failure: with Remote Control on by default, a VM on the API path is a normal
   configuration, not a mistake. Off is enforced rather than skipped. `main.yml` decides once
   (`claude_code_remote_control_wanted`) and either includes `remote_control.yml` or, if the unit
   file this role installs exists, stops and disables the service, removes the file and reloads
   systemd. Skipping alone left an earlier run's server taking sessions after the user opted out,
   and restarting on every boot with the key it could not use. Gating on the file keeps a VM that
   never had the unit untouched, and keeps check mode from asking `systemd_service` about a unit
   that does not exist.
2. **Read what `settings.json` disables.** The file is read back from disk after `settings.yml`
   wrote it, because that is what the server starts with and it keeps keys an earlier run left
   there. The `env` keys that block are the list `claude_code_remote_control_blocking_env` in
   `roles/claude_code/vars/main.yml`, which says why each does: the telemetry opt-outs switch off
   the feature-flag evaluation Remote Control depends on, and the token and provider switches route
   it to a credential that outranks the login. Checked separately, as the same file explains: an
   `apiKeyHelper`, which is a top-level settings key, and an `ANTHROPIC_BASE_URL` pointing anywhere
   but `api.anthropic.com`. The names found
   (`claude_code_remote_control_settings_blockers`, never the values) are part of eligibility in
   step 5, so a VM with one takes the same path as a VM with an unusable credential: unit installed,
   service stopped, the keys and the `claude_remote_control: false` switch named. It used to be an
   `assert`, which failed the host for a privacy setting its owner had chosen and kept
   `github_projects` from running behind it.
3. **Resolve the binary.** `command -v claude` under a login shell, because the native installer and
   npm put it in different places and systemd does not read a login shell.
4. **Pre-answer the three prompts** that would stall a headless server — first-run onboarding,
   workspace trust for the session directory, and Remote Control's one-time `Enable Remote Control?
   (y/n)` confirmation (`remoteDialogSeen`) — by seeding `~/.claude.json`. The confirmation is the
   one that matters most: it reads stdin, which systemd points at `/dev/null`, and anything but `y`
   exits 0, so without the seed the unit starts, prints the question to the journal and stops without
   ever registering. Enabling `claude_remote_control` is the user's answer to it. Written only when a
   flag is actually missing: Claude Code owns that file and rewrites it constantly, so reasserting the
   whole document every run would report `changed` forever.
5. **Check `claude auth status`**, which returns JSON carrying `loggedIn`, `authMethod`,
   `subscriptionType` and, when a key is in use, `apiKeySource`. Eligibility is
   `loggedIn && authMethod == "claude.ai" && !apiKeySource` and no blocker from step 2, defined once
   in `vars/main.yml` as vars that read the registered results, and shared with `just login --only claude`
   (below), which reads the same two things over SSH. `authMethod` alone
   is not enough: it reads `claude.ai` whenever that login exists, even while an `ANTHROPIC_API_KEY`
   outranks it, and also for a Console login, whose key is reported as `/login managed key`. Only
   `apiKeySource` shows either. The *plan* is deliberately not checked — hard-coding the strings for
   Pro/Max/Team/Enterprise would turn a new plan name into a false refusal, and an ineligible plan
   already fails at service start with Claude Code's own message.
6. **Install the unit either way**, then start it only when eligible. When it is not, the service is
   left disabled and stopped rather than crash-looping, and the play prints what to do — the login
   command, or what is in the way and how to remove it. The tasks that enable, start or stop the
   service also require the unit file to exist. In a real run it always does; in check mode on a VM
   that never had it, `template` wrote nothing and `systemd_service` fails on a missing unit whether
   or not it is in check mode, so without that `just deploy --check` failed on every such VM.
7. **Confirm the server stayed up.** The service is restarted unless the unit file is unchanged and
   the service already active: a server waiting out its restart delay reports `activating`, which
   `started` treats as running. One `active` reading afterwards proves nothing, because `Type=simple` is active as soon
   as the process forks, before the server has tried to register. So the role records the main PID
   and `NRestarts` right after the start, waits `claude_code_remote_control_stay_up_seconds` (20), and requires the unit to be
   `active`/`running` with the same PID and count. An exit shows up either as `auto-restart` or as a
   new PID and a higher count. The count is compared, not required to be 0, because systemd keeps
   it for as long as the unit is not fully stopped, so a healthy server that once restarted through
   an outage has a non-zero count. The check runs only when the start task reports `changed`: a
   `started` against a server that was already running changes nothing, has nothing to prove, and
   would otherwise cost 20 seconds per batch of forks on every configure run of a live fleet. After a
   sign-in it always runs: the service waited disabled for the login, so enabling it for boot reports
   a change even when the path unit started it moments before. It is skipped in check mode too, where
   nothing was started. The role's Molecule side effect re-runs
   the role against the live server and asserts the PID and `NRestarts` are untouched, then changes
   the permission mode and asserts a new PID and the new unit text.

### The service unit

The unit runs as the VM user from `~/projects`, with `Restart=always`, `RestartSec=10`,
`RestartSteps=5` and `RestartMaxDelaySec=300`, and no start limit (`StartLimitIntervalSec=0`).
`always` because the server stops by itself for reasons that are not failures — server mode exits
after about ten minutes without a network — and `on-failure` restarts only an unclean exit. No start
limit because once reached it left the unit failed until someone intervened, after an outage or a
boot that raced the network. The delay backing off from 10 seconds to five minutes is what keeps an
expired login, which cannot be renewed without a browser, from spinning. `RestartSteps` needs
systemd 254; the VMs run Ubuntu 24.04 with 255.

### The sign-in playbook

`playbooks/claude_login.yml` (`just login --only claude`, and the sign-in step of `just deploy`) discovers
the VMs and runs the role's `login` entry point (`tasks_from: login`) on them, one VM at a time
(`serial: 1`). It reads `claude auth status` as `claude_code_user` through a login shell and decides
from the same vars as step 5, so the two cannot disagree: when the playbook decided from `loggedIn`
alone, a VM signed in with a Console account, a `setup-token` token or an API key was told by each
command to run the other. Bare, it reports each VM, with `just login <vm> --only claude` and the
by-hand `ssh -t dev@<address> "bash -lc 'claude auth login'"` for a VM with no login. The `bash -lc`
puts `~/.local/bin` on `PATH` on any VM: on one configured before `common`'s sshd PATH drop-in
([§5.3](#53-common--os-baseline)), `ssh` runs a command in a non-login shell without it, and Ubuntu's
`.bashrc` returns early for one.

Given names, or from `just deploy`, it signs a VM with no login and no API key in by the browser
(`browser_login.yml`). Once every VM has had its turn at the prompt, a follow-on play includes
`after_login.yml` for each VM signed in just now, which runs `remote_control.yml` as root, as `just deploy
--only configure` would, to start the server in the same run. The report says how each VM is signed in, and a
VM whose Remote Control is off on purpose (an API key from the vault, or `claude_remote_control: false`)
is said to be so, with nothing to fix; `just deploy <vm> --tags claude_code` is named only for a
VM signed in in a way Remote Control cannot use, which a fix by hand then needs started.

### The browser sign-in

What it rests on was probed against Claude Code 2.1.296 in throwaway containers, never against a real
account. With no terminal, `claude auth login` prints `Opening browser to sign in…`, `If the browser
didn't open, visit: <url>` and `Paste code here if prompted >`, then reads one line from stdin. The
printed URL is the manual flow - `code=true`, redirecting to `platform.claude.com/oauth/code/callback`,
the page that shows the code after Authorize. Given `BROWSER`, claude also runs it with a second URL of
the same attempt (the same `state` and `code_challenge`) that redirects to
`http://localhost:<port>/callback`, and listens on `127.0.0.1:<port>`, a random port; a code arriving
there ends the attempt as a pasted one does (exit 0 signed in, 1 refused; probed 2026-10-11 with a
recording `BROWSER` and a bogus code through the callback). The first version of this flow saw only the
printed URL and concluded no tunnel could complete the login; the second URL is what makes one possible.
The line is `<code>#<state>`, split on `#`. A wrong code prints `Login failed: ...` and exits 1; a right
one writes `~/.claude/.credentials.json`, prints `Login successful.` and exits 0; a line without `#`
prints `Invalid code. Please make sure the full code was copied.` and waits for another. So the flow:
mkfifo a pipe under `~/.config/claude-on-proxmox/`; start `exec 0<>pipe && echo $$ >pid && exec claude
auth login` with `async`, so claude holds its own input open and never sees an early end of it, and the
recorded pid is claude's because the shell exec's into it (in a `( ... )` subshell `$$` is the parent's);
`wait_for` the URL. With `claude_code_login_redirect` (which follows `login_desktop`), claude's
`BROWSER` is a script that only records the page it is handed; the run reads it, takes the callback
port from its `redirect_uri` (1024-65535), and forwards that port from the controller with `ssh -f -n -T
-o ExitOnForwardFailure=yes -L localhost:<port>:127.0.0.1:<port> <user>@<vm> 'echo $$ >pidfile && exec
sleep <wait+60>'` on a connection of its own, from `delegate_to: localhost`. Up, it opens that page
here - only when it matches `claude_code_login_page_pattern`, Claude's own authorize URL, since the VM
chooses it - and waits on the async job with no prompt (`claude_code_login_approval_timeout`); the
`always` section kills the far end's `sleep` by its pid, only while `ps` names it a sleep, which ends the
forward. The opener is looked for first, and the redirect is taken only where `page_opener` finds one
(`BROWSER`, or `open` / `xdg-open` under `login_desktop`) and `SSH_CONNECTION` is unset on the controller:
a page opened on another machine sends that machine's browser to its own localhost, and the run would wait
out the approval for nothing. That, a forward that fails (the port taken here), a page that is not
Claude's, or a code given in `claude_code_login_code` falls back to the paste: say the printed URL; open it on the controller
(`login_desktop`, off in every test); `pause` for the code under `no_log`, which reads the terminal through the main `ansible-playbook` process and,
with no terminal, returns nothing at once - the skip; check with `async_status` that the attempt still
runs, since past `claude_code_login_timeout + claude_code_login_attempt_seconds` Ansible's async limit
has ended claude and a late code would go into a pipe nobody reads (said at once instead); hand the code
over on stdin to a writer that opens the pipe read-write, which never blocks even if claude is gone, and
refuses anything at that path but a pipe, since `<>` would otherwise create a plain file and write the
code into it; `wait_for` any answer after claude's prompt (`prompted > \s*\S`), not three known ones, so
a refusal nobody listed (an account on hold, a managed policy) ends the wait at once; on `Invalid code`,
which a code copied only up to its `#` gets while claude goes on waiting on the same page, ask once more
for the whole code; then, in an `always`, give claude a moment, end the recorded pid if the job still
runs (an attempt nobody completes, or a malformed code it is still waiting on) - a TERM, on which claude
exits at once, and a KILL only if `kill -0`, asked every tenth of a second, still finds the pid a second
later, so none goes to a pid claude no longer holds and the attempt ends the moment claude has - collect
and clean up the job, and remove the files. The outcome is `claude auth status` again, not what claude
printed. A pasted code is never written as a bare newline, which claude would answer `Invalid code` and
go on waiting.
Before any of it, a sign-in of the same VM still waiting - a run left at its prompt, a second `just
login --only claude` - is refused by name: its pidfile names a live process whose command line `ps` shows as
`auth login`, and taking over its pipe and pidfile would hand one run's code to the other's claude. What
claude printed reaches a message only without its control characters (`claude_code_login_said`): the VM
user controls that text, and 2.21 writes a failure's `[ERROR]` line to this terminal and the run log
raw.

### No terminal probe

Nothing decides from a terminal probe. A `( : < /dev/tty )` in a `pipe` lookup runs in an Ansible
worker detached from the controlling terminal, so it says "no terminal" even on `just deploy` at one,
where `pause` reads fine; the main process reads only when it owns the terminal's foreground process
group. An answer is the one proof of an attended run, and it is per VM: the sign-in sets
`claude_code_login_answered` (a code entered, or the browser come back) or `claude_code_login_skipped`
(Enter, no terminal, or a browser that never came back) on the VM itself, and `just deploy` runs GitHub's
device flow and Tailscale's web login, which wait for a browser, never for the second
([§4.7](lifecycle.md#47-deployyml), §5.6).

### Failures under `serial: 1`

The sign-in play is `serial: 1` only when it can prompt (names given, or `just deploy`); a bare report
reads the fleet at once. Under serial a VM whose sign-in fails is a whole batch failed, which
`PlaybookExecutor` turns into the end of the run - every later VM and every later play. So the sign-in
play rescues a failure, says it with the `just login <vm> --only claude` that retries it (added where the
failure does not name it already), records it - for NEXT STEPS too, under `claude`, so a deploy that goes
on past it still lists it at the end - and a last play without serial fails each such VM, unless
`just deploy` imported the playbook (`claude_vm_login_in_deploy`): deploy has done its job and goes on to
GitHub and VS Code. `rescue` handles failed tasks, never an unreachable host, so a play before it pings
every VM with `ignore_unreachable` and records one that does not answer the same way. The rescued
block's condition is on its task, not on the block, whose `when` would also skip the rescue's message
once the failure it tests for is recorded. The rescue's first
task re-raises `pause`'s abort (Ctrl-C, then A): the person asked for the run to end, not for the next
VM's page. The work after a sign-in - Remote Control's start and its stay-up check - is not a sign-in,
so it runs in the follow-on play, after the last prompt, and a failure there is recorded in
`claude_vm_configure_failures` with the command that retries it, and for NEXT STEPS under
`remote_control`, failing the VM at the end of `just login --only claude` and at the end of `just deploy`
alike.

### The path unit

A VM that waits for its first login also gets a path unit, `claude-remote-control.path`, with
`PathChanged=` on the credentials file and `Unit=` the service, so a login made any other way - the
Claude terminal of `just code`, or by hand - starts the server by itself. Probed in a systemd
container: `PathChanged=` starts the service when the file is created, by a plain write or an atomic
rename, and leaves a stopped service stopped; `PathExists=` restarts it instantly whenever it stops
while the file exists, which with no start limit would spin on a refused login and undo every
`systemctl stop`. `PathChanged=` fires on any change to the file - created, rewritten, renamed into
place, removed, its mode changed - so a token refresh or a logout fires it again, and the unit lives
only while the login is the one thing missing (`claude_code_remote_control_bridge_wanted`): the run
that finds the VM signed in, or a settings blocker, removes it (`tasks/path_unit_absent.yml`, shared
with turning Remote Control off) before it starts the service, so a start the server refuses - an
organisation that disabled Remote Control - leaves no path unit behind to start it again later.

### Under `just status`

The `status` entry point (`tasks/status.yml`) reads, and never runs `claude`: whether `claude auth status`
can refresh a login's token, and so write, is still to be checked on a real VM, and `just status` changes
nothing ([§4.6](lifecycle.md#46-playbooksstatusyml--read-only-drawn-live)). It reads where the native
installer's `~/.local/bin/claude` link points - its version, the target's name - whether
`~/.claude/.credentials.json` is there (the file's being there, never what it holds), and Remote Control's
service and path unit with `systemd_service`, no state asked for. `just status` then says the login as
stored or not, and Remote Control as running, down (failed or restarting, with its restarts), waiting for
the login, stopped, not set up, or off on purpose.

### How it is tested

`tests/unit/test_remote_control_playbooks.py` runs the playbook against canned `claude auth status`
output, and the check-mode path against a stand-in `systemctl`; `tests/unit/test_claude_login.py` runs
the sign-in against `tests/molecule/fake_claude.sh`, which behaves as probed, for every outcome claude
has (accepted, refused, malformed, refused in other words, an error in place of the page, an attempt
that ended, a sign-in still waiting, a stale earlier attempt) and five times at a pseudo-terminal;
`tests/unit/test_sign_in_plays.py` runs the playbooks' shape - the reach play, the follow-on play, the
batches - and deploy's own sign-in steps; the claude_code role scenario lands a login with only the
path unit waiting and sees the server start; and the configure scenario's side effect signs its
container in with the stand-in, after which the real role starts Remote Control under systemd.

## 5.6 `github_projects` — sign in to GitHub, clone an account's repositories

`main.yml` validates the username, resolves the target directory (the user's home is looked up only
when `github_projects_dir` is empty), then includes `auth.yml` and `clone.yml`.

**Two credentials, one token.** A private repository is *listed* with an API token and *cloned*
with a git credential, and a push or `gh pr create` later needs the latter again. The role gives
all three jobs the same token through `gh` on the VM, which stores the token it is signed in with
(`~/.config/gh/hosts.yml`, 0600), prints it back with `gh auth token`, and answers git's credential
helper protocol with it (`gh auth git-credential`). Which token that is follows the same shape as
Remote Control's decision (§5.5): derived from what the VM holds, never from a switch alone.

### Signing gh in: `auth.yml`

`auth.yml`, as `github_projects_user`:

1. **Read gh's state**: `which gh` (a VM built with `dev_tools_install_gh` off gets a message, not a
   failure), then `auth_status.yml`: `gh auth status --hostname github.com --json hosts`, which exits
   0 signed in or out and keeps its prose on stderr, and, when signed in, `gh auth token` under
   `no_log`, and the role's own marker (step 3). `vars/main.yml` turns the JSON into
   `github_projects_signed_in` and `github_projects_login_kind` - `env` from `tokenSource`, else
   `pat` (`ghp_`, `github_pat_`) or `oauth` (`gho_`) from the prefix of the token `gh auth token`
   printed, because the status JSON carries no token without `--show-token`. The kind is for
   `just login --only github`'s report; the logout in step 3 does not rely on it. A non-zero exit from the
   status command is gh refusing `--json`, which came in gh 2.81.0 (2025-10-01), and fails the host
   naming the version: read as "signed out" it would print the sign-in command on every run and
   never configure the helper. `auth_status.yml` is a file because it is included again after a
   change: a skipped include leaves the registers alone, where a skipped copy of the tasks would
   overwrite them with a skipped result.
2. **Sign in from the vault token** when there is one and the stored token differs, and only over a
   login that is not the user's own - gh signed out, the role's own by step 3's marker, or
   `github_auth_method: token` (`github_projects_token_sign_in`); a login made by hand stays, and each
   run says the token only lists there: `gh auth login --with-token --insecure-storage`, the token on
   stdin, `no_log`. A headless VM
   has no credential store and gh would fall back to the plain-text file anyway; asking for it keeps
   a future gh that finds a store from putting the token where the role cannot read or remove it.
   A refused token fails the host with gh's own line, which names the cause and never the token.
3. **Sign out the login the role made when the vault token is cleared**, the other direction of
   the same rule, as `settings.yml` removes the API key it wrote. gh cannot tell the role's token
   from one the user pasted by hand, so the role leaves a marker whenever the login is the vault
   token's - the token's SHA-256 digest in `~/.config/claude-on-proxmox/gh-login`, 0600, rewritten
   if it goes missing - and signs out, by account (`--user`), only a stored token whose digest the
   marker holds, removing the marker with it. No marker, a stale one, a device-flow login, a
   `GH_TOKEN`: each stays whatever the vault says. A device-flow login the role made leaves a marker
   of its own, `gh-device-login` (the digest of the token that sign-in stored), apart from this one so
   that its logout rules stay the vault token's: `just destroy`'s `release` entry point reads it to say
   where that token is revoked ([§4.5](lifecycle.md#45-playbooksdestroyyml--confirm-release-delete)).
   It does not revoke it: GitHub ends every GitHub CLI sign-in of the account with it.
4. **Make gh git's credential helper** with `gh auth setup-git --hostname github.com`, once:
   gh writes `credential.https://github.com.helper` twice, an empty entry that cuts any inherited
   helper chain and then `!/usr/bin/gh auth git-credential`, and the role looks for an entry whose
   command is `gh` the way gh itself does, so a second run changes nothing. The helpers are read
   whenever gh is signed in, `github_auth` or not.
5. **Say what is missing** on a signed-out VM: the device-flow command (or, under `just deploy`,
   `github_projects_sign_in_follows`, that the deploy signs it in next), which adds `-s workflow` to
   gh's default scopes (`repo`, `read:org`, `gist`) so that a push changing `.github/workflows/` is
   not refused, and that private repositories are not listed until then.

### Listing and cloning: `clone.yml`

`clone.yml` lists with `github_projects_listing_token` - the vault token, else whatever gh holds -
so the run after a device-flow login reaches the private repositories with nothing in the vault.
The identity is read first, key by key: a configured `git_user_name`/`git_user_email` is written
always, otherwise each is derived only where `~/.gitconfig` has none - the display name or login, and
GitHub's noreply address `<id>+<login>@users.noreply.github.com`. `github_repos` returns the account
only when something is left to derive (`account`): the token's own from `/user`, which the token is
checked against anyway, else `github_username`'s public profile (one more unauthenticated request); an
organisation gives none, which is said. Written with `community.general.git_config` before the helper,
so the two writers of `~/.gitconfig` each check before writing. The listing is `no_log`; its failure
is said by the next task in the module's words, which the module masks (it strips the token and masks
the stripped form too). The filters (`exclude`, then `include` if non-empty, never-pushed repositories
dropped as `empty`) are vars in `vars/main.yml`; a private repository is cloned only where git has a
credential for it - over SSH always, over HTTPS with gh signed in and git's helper (made here, or by
hand) - else skipped and named rather than cloned and failed. The clone runs as the user
under `sudo -H`, so git reads that user's `~/.gitconfig` and the helper reads that user's
`hosts.yml`, with `GIT_TERMINAL_PROMPT=0` so a repository the credential cannot reach fails with
"terminal prompts disabled" and its name instead of git's attempt to prompt. For SSH clones the
role first fetches GitHub's published host keys from `<api_url>/meta` and seeds them into the
user's `known_hosts`. `update_existing` defaults to **false** so a re-run never disturbs local work.

### `just login --only github`

`playbooks/github_login.yml` (`just login --only github`) runs the role's `login` entry point, which looks
the user's home up for itself (the marker lives under it, and only `main.yml` had resolved it, which
the first real run found), decides from the same vars and reports how a VM is signed in - and, for a
device-flow login that lacks the `workflow` scope, the `gh auth refresh` that adds it. It names only
what can run there: with `github_auth: false` nothing, and for a vault token not applied yet the
`just deploy ... --tags github_projects` that applies it. Given names, any number, it runs the device
flow itself (`device_flow.yml`) for each VM signed out, then, after the last approval, the whole role
for every VM named, signed in now or before, so the private repositories arrive in that run (and a
vault token is applied); under `just deploy`, whose configure has just run the role, only for the VMs
signed in just now.

`login.yml` is three parts: `login_read.yml` (the home, `which gh`, `auth_status.yml`), the prompt and the
device flow, both on `github_projects_login_due` (gh the role's, signed out, and the device flow its way
in: a fact, which `github_login.yml` reads after the role to say, for a VM whose Claude sign-in nobody
answered, why the flow was not started), and `login_report.yml` (the report, and its NEXT STEPS record,
decided by `github_projects_login_left`). The `status` entry point, which `just status` runs, is the first and the
last of them and nothing between, so it says what `just login --only github` would and signs nothing in.
A check gh could not make with GitHub - no network - is `github_projects_auth_unchecked`: gh says both it
and a refused token as state `error`, and only a refusal carries GitHub's `HTTP 401` or `403`, so status
says "not checked", never "not signed in".

### The device flow

The flow, as far as GitHub allows it to go: gh is started on the VM
in the background with no terminal (`async`, stdin closed, both streams to a file under the user's
config; `async_status` resolves `~/.ansible_async` through the connection's shell rather than the
task's environment, which on a VM is the same home the wrapper used and in the unit harness is not,
so the harness sets `ansible_async_dir` per host), which is what makes it print the code and the
page at once and poll. That file is unlinked, never truncated, before gh starts: a run that failed
after starting gh leaves gh polling for up to fifteen minutes, and truncating would share the file
with it, whose expiry line would then end the new wait early, while an unlinked file leaves it its
own inode to die on. gh runs in the job's background, its pid recorded, and the job writes
`gh exited: N` once gh ends however it ends; both waits end on it. A `wait_for` reads the code out of
the file, the run prints it, and on the controller the page is opened by the `page_opener` filter
(`filter_plugins/desktop.py`, shared with the Claude sign-in: the first `BROWSER` command, `%s`
substituted, else `open`, else `xdg-open`) and the code copied (`pbcopy`, `wl-copy`, `xclip`), both
best effort and both gated by `github_projects_login_desktop`, which every
scenario and the unit harness turn off because their `localhost` is the developer's machine, with a
test that keeps it so. Those controller tasks carry `become: false`: the play's `become` is the VM's
passwordless sudo, and inherited by a task delegated to the controller it is the developer's, which
asks for a password and fails the run (the first real run found this; the harness runs with become
off, so a static test holds it). Control characters are stripped from gh's text before any message
quotes it. Everything after gh's start is a block whose `always` gives gh a moment, ends it by pid if
it still runs, collects the job (`until`, 10 × 1 s), and removes the job file, the output and the
pidfile. The outcome is `gh auth status`, never gh's wording. The wait (930 s) outlasts gh's fifteen
minutes and the async limit (960 s) outlasts the wait, so gh's own expiry line is quoted; a job the
limit ended, or a gh the run ended, is said to have expired. The user's part is the one GitHub
insists on: the code entered on the page. `github_projects_auth_method` picks the credential: `auto`
takes the token when set and the device flow otherwise, `web` the device flow always with a token
used for the listing only, `token` the token only with an empty vault a named failure. The device
flow asks for `github_projects_login_scopes` on top of gh's own and tells gh the clone protocol.

### Under `just deploy`, one VM at a time

`just deploy` asks first: Enter goes for a VM whose Claude code was entered and `n` skips it; any other
VM goes on `y` only, and with no terminal a question returns nothing at once. One VM at a time:
`github_login.yml` runs its sign-in play `serial: 1` where it can prompt (names given, or `just
deploy`), and the fleet at once for a report, and `device_flow.yml` refuses a batch of
more than one, since the browser step would otherwise run for each before any waiting began. The
guard reads `ansible_play_batch`: `ansible_play_hosts` is every host of the play, serial or not. A
failed VM is rescued, said and raised again by a later play without serial, so it neither stops the
VMs after it nor, under `just deploy`, the run (§5.5); an unreachable one is caught by the reach play
before it, and a failure of the role run after the sign-in is a configure failure, raised at the end
of `just deploy` too, naming the failed repositories and `--tags github_projects`. The flow runs from
`just login --only github` and from `just deploy`, never from `just deploy --only configure`.

## 5.7 `vscode_server` — what VS Code's Remote - SSH needs on the host

Runs right after `common`, which owns the user and sshd. VS Code's documented requirements for a
Remote - SSH host are bash, tar and curl or wget, a glibc x86_64 or aarch64 system, an sshd that
allows the port forward the VS Code Server is reached through, and an inotify limit large enough to
watch a workspace. The image and `common` give the first two; this role checks them, writes the other
two, and proves the sshd one.

- **A drop-in of its own, `20-vscode-server.conf`**, with `AllowTcpForwarding yes`,
  `AllowStreamLocalForwarding yes` and `ClientAliveInterval`/`CountMax`, validated with `sshd -t`.
  Two files, two owners: `10-` is `common`'s hardening, `20-` is this.
- **`sshd -T -C` for the editor's connection.** sshd keeps the first value it reads for a keyword,
  and a `Match` block sets its own for the connections it matches, so a drop-in sorted before `20-`,
  `sshd_config` itself or a `Match User dev` block can each silently override the file this role
  just wrote. The role reads where the editor will connect from out of Ansible's own
  `SSH_CONNECTION` (without `become`, which drops it), asks `sshd -T -C user=<vm_user>,host=,addr=,
  laddr=,lport=` what applies to that connection - plain `sshd -T` skips `Match` blocks - and fails
  on forwarding that is off, limited to the remote direction, or removed by `DisableForwarding`,
  naming the file that does it (a `find` over `sshd_config` and the drop-ins, indented lines
  included, since that is how a `Match` block's settings are written). `sshd -T` parses the files
  on disk, so this checks the configuration the end-of-play restart will load; nothing is restarted
  first.
- **`fs.inotify.max_user_watches` = 524288** through `ansible.posix.sysctl`, written for the record
  everywhere and applied only where `/proc/sys` is writable (`vscode_server_container_virt_types`).
- **Off removes both files**, as Remote Control's off path removes its unit. The live sysctl stays
  raised until the next boot: a limit is harmless to leave high, and lowering it under a running
  watcher is not.

Deliberately absent: a pre-installed server (its build is tied to the client's exact commit, so a
pre-warmed copy is stale on the first VS Code update), a `.code-workspace` file (opening `~/projects`
already shows every clone as a nested repository), and extensions (`remote.SSH.defaultExtensions` is
a client setting).

## 5.8 `tailscale` — join the VM to a tailnet

Runs last in `configure.yml`, in a rescued block ([§4.4](lifecycle.md#44-playbooksconfigureyml)).
The design rests on facts established against Tailscale 1.104.1 - its source, a probe that never
logged in, and a Headscale run on an internal Docker network - and recorded with their sources in the
plan's research record.

- **Ownership.** `/var/lib/claude-on-proxmox/tailscale-installed` is written before the repository: a
  Tailscale with it, or none at all, is the role's to install, maintain and remove; a package dpkg has
  without it, or a Tailscale dpkg does not know (a `tailscaled` of Tailscale's static binaries in
  `/usr/sbin` or under `/usr/local`, the snap's `/snap/bin/tailscale`), was installed some other way and
  is left alone, on and off, and reported while on - off, `configure.yml` runs the role only where the
  marker is: the package beside it would start a second `tailscaled`. Written first, so
  a run that fails halfway leaves something the role knows to finish or remove. `tailscale-joined` holds
  the node key of the login the role's own join produced (`Self.PublicKey`): only that login's settings are
  changed, so a login made by hand keeps its own, as a gh login made by hand does (§5.6). It holds
  `pending` while a sign-in the role started has not ended - written before the web login shows its page,
  and after a join with the key that ran out of time, which tailscaled goes on with - and the next
  configure records the login that sign-in ended in by its node key, so a page approved after the run is
  still the role's; a join that failed any other way - a refused key, tags the key may not apply - leaves
  nothing pending (`tailscale_join_under_way`: `up`'s timeout line, and no `invalid key:`), since a login
  made by hand after it would otherwise be taken for the role's, and its settings changed. A login is recorded only once it is `Running` or
  `NeedsMachineAuth`: an expired node keeps `HaveNodeKey`, and its old key, while the page that replaces
  it waits. dpkg is asked with `dpkg-query`, not `package_facts`, which needs python3-apt and so cannot run
  on the developer's Mac, where the unit tests run these files.
- **Install.** A systemd drop-in (`TS_NO_LOGS_NO_SUPPORT=true`, when `tailscale_upload_logs` is false)
  before the package: its postinst reloads systemd before starting `tailscaled`, so the drop-in covers the
  first start. Never a pre-created `/etc/default/tailscaled` - a conffile, which dpkg keeps when it exists,
  without `PORT`, and `--port=${PORT}` would fail the daemon. The legacy `tailscale.list` goes (beside a
  deb822 source with another keyring it breaks every apt update), the deb822 source carries the vendored
  key, the package is `present`, not `latest` (an upgrade restarts tailscaled). A failed install takes the
  repository back out before it fails: a source whose host cannot be reached fails every apt refresh on
  the VM, `common`'s first. `tailscale_daemon_flags` edit `FLAGS` after the package; restarts are tasks
  gated on a change, never handlers, which would run outside `configure.yml`'s rescue. In check mode on a
  VM without the package the package, flags and service are skipped: `apt` fails in check mode on a
  package from a repository the same run adds, and `systemd_service` on a unit that does not exist.
- **State** (`state.yml`). `tailscale status --json`, read `until` the state is neither `NoState` nor `Starting`, 30 s
  at most: tailscaled tells systemd it is ready before its backend has a state, and a joined node waits in
  `Starting` for a relay. Still unsettled is a report. A daemon the role just started that does not answer
  is a failure, named with the CLI's own line. Everything is decided from this JSON and `debug prefs`,
  never from human output.
- **The key.** Used from a settled `NeedsLogin` with no `HaveNodeKey` only - a node that never joined, or
  one logged out by hand, which reads the same - because a key Tailscale no longer accepts, sent to a node
  with a login, logs it out (tailscale#19501). It goes in as `--auth-key=file:/dev/stdin`, the key as the
  `command` module's stdin under `no_log`: nothing on argv, no file - `copy` would stage one in
  `~/.ansible/tmp` - and with pipelining never on the VM's disk. The flags are every managed setting
  after `--reset`, so tailscale's "requires mentioning all non-default flags" never fires. A refusal is a
  report naming Tailscale's `invalid key:` reason, not a failure: the key is shared by every VM. The
  reason is `up`'s own line, else a Health message that was not there before the join
  (`tailscale_health_before_join`): tailscaled keeps a login error until an attempt after it ends, so an
  earlier key's refusal is still there when a new key's join runs out of time.
- **Settings.** On a Running login the role made, one `tailscale set` naming only what differs from
  `debug prefs` (`Hostname`, `RunSSH` with `--accept-risk=lose-ssh`, `RouteAll`, `CorpDNS`). Tags are
  compared only when `tailscale_advertise_tags` is set, reported with the admin console as the fix, and
  never changed: `set` has no `--advertise-tags`, and a web login without tags would make a tagged VM the
  user's own device.
- **The web login** (`tasks/login.yml`, `web_login.yml`), where `tailscale_login_possible` holds, as the
  fact `tailscale_login_due` that the question and the login go on, and that `tailscale_login.yml` reads
  after the role to say, for a VM whose Claude sign-in nobody answered, why it was not started - in NEXT STEPS
  in place of the report's line only where the VM is merely signed out (`tailscale_login_signed_out`, the
  report's `NeedsLogin` case), never over a page still waiting, an expired login or a refused key. One `up --timeout=5s` per attempt - each keyless
  `up` mints a new page - then the page is read from `AuthURL` once and opened here, only when it is
  Tailscale's own sign-in page (or one under `tailscale_login_server`), and the state is polled until
  `Running` or `NeedsMachineAuth`, up to `tailscale_login_timeout`. tailscaled finishes a login approved
  after that on its own - seen in the Headscale run - so a wait that runs out is a report, and a later one
  finds the page still pending in the state and says so, before an expired key or a refused one: a fresh
  page would end the one waiting. An expired login is signed in again only when it is the role's (the
  marker holds its node key, which an expired node keeps) or a sign-in of the role's is pending
  (`tailscale_login_expired_elsewhere`): `up --reset` over one made by hand would replace its settings and
  make it the role's, so that one is reported, to be signed in again the way it was made. `just deploy`'s sign-in play still holds its configure play's join, so
  without that order a refusal from earlier in the run hid the page.
- **The report.** One line per VM, built in `vars:`, everything the VM reports through `plain_text`, and
  the tailnet named (`CurrentTailnet.Name`), so a login someone else approved into their tailnet shows -
  but not under `tailscale_login_server`, where Headscale sends a constant. Which line applies
  (`tailscale_report_case`) and whether it leaves something to do (`tailscale_report_left`, NEXT STEPS'
  record) are in `vars/main.yml`, read by `just status` too.
- **Under `just status`** (`status.yml`, the `status` entry point): `ownership.yml`, `state.yml` read once
  (`tailscale_settle_seconds: 0`: "connecting" is said, not waited out) and `report.yml`, and nothing else -
  no install, join, setting or sign-in. Run under `--check`, as status runs, the report's check-mode cases
  say what a configure would do, which status words as what is missing.
- **The join method** (`tailscale_auth_method`): `auto` joins with the key when there is one and leaves the
  VM for the web login otherwise; `key` is the key only - an empty one fails at the role's first task by
  name, the web login is never offered or started (`tailscale_login_possible`), and an expired node is told
  to be logged out by hand, since the key is never sent to a node that had a login; `web` leaves the key
  unused (`tailscale_key_wanted`).
- **Deleting the device** (`api_delete.yml`), from the controller with `tailscale_api_credential`, which
  `tailscale_api_auth_method` decides: `auto`, the default, takes whatever the vault holds - a credential
  there is the opt-in, as the auth key is for the join - so `token` when there is an API access token (it
  reaches every device; an OAuth client only its tags'), else `oauth` when either half of an OAuth client
  is set (half of one is said by name and never sent), else `none`; `none`, `token` and `oauth` name one.
  `token` is an API access token, `oauth` an OAuth client, exchanged at `/api/v2/oauth/token` for a
  one-hour token (it needs `devices:core`). `GET /api/v2/device/<Self.ID>` first, then `DELETE` only when
  the device's MagicDNS name's first label is `tailscale_hostname` or `<it>-<n>`: the VM reports the ID,
  and the credential can delete any device. A 404 is a device already gone. Every call is `no_log` and
  `delegate_to: localhost`; the file is always included and its tasks carry the condition, so no register
  is an earlier run's. Run by the off path after its logout, and by the `release` entry point, which
  `destroy.yml` runs before deleting a VM: start a stopped `tailscaled`, read the node's ID, log it out,
  delete the device, and record a clause in `tailscale_release_said` - whatever Tailscale the VM has,
  since the VM itself is going.
- **Off** (`absent.yml`), only with the installed marker - `configure.yml` runs the role at all, off, only
  where its `stat` finds the marker ([§4.4](lifecycle.md#44-playbooksconfigureyml)) - said first, as removing
  what this project installed: `tailscaled` started where the unit exists -
  logging out takes the daemon, and one stopped by hand would leave the node's key valid in the tailnet -
  then logout where it answers with a node key, stop - never disable: the package's postinst re-enables
  tailscaled on a reinstall only while its enable link survives, so a disable kept a Tailscale installed
  again by hand from ever starting - remove (not purge) where dpkg
  has the package, then the repository, the drop-in and both markers. Each step only where its object exists,
  since a failed install leaves only some of them. A daemon that does not answer, or a logout that fails
  (the control server out of reach), is said, not failed on - failed, it would keep Tailscale on until
  the network came back - and the joined marker stays with the login that may be left: turned on again,
  the daemon starts on that state, and the login is still the role's. A device the API no longer has
  (404) is said as gone already, as the release says it.
