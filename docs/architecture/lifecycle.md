# 4. Lifecycle and control flow

```mermaid
stateDiagram-v2
    [*] --> NoTemplate
    NoTemplate --> Template: playbooks/template.yml (once, over SSH)
    Template --> Cloned: provision.yml — clone (in parallel for a fleet)
    Cloned --> Configured: apply cloud-init + hardware, resize disk
    Configured --> Running: start
    Running --> Addressed: guest agent reports IPv4
    Addressed --> Ready: configure.yml — common, vscode_server, dev_tools, claude_code, github_projects
    Ready --> Addressed: re-run configure
    Ready --> [*]: destroy.yml (tag check, release, then stop + delete)
    Cloned --> Configured: re-run repairs a half-built VM
```

## 4.1 `playbooks/template.yml` — one-time

`hosts: proxmox`, over SSH, because importing a disk image needs `qm` on the node. Runs
`roles/proxmox_template`. `deploy.yml` runs it only when the template is missing, deciding that
through the API, and the import-level `when:` is what keeps a run that does not need to build it
from opening an SSH connection to the hypervisor: it skips the implicit fact gather along with every
task ([§4.7](#47-deployyml)). The play's `gather_facts: false` only saves a pointless gather on the
run that does build.

## 4.2 `playbooks/provision.yml` — three plays

Three plays rather than one loop, because the work parallelises. Most of the per-VM time is a
multi-minute wait for the guest agent to report a DHCP address, and the VMs are already booting on
the hypervisor, up to `forks` at a time. A loop over `include_role` would watch them one at a time.

```mermaid
sequenceDiagram
    participant U as just deploy alpha,beta --only provision
    participant P1 as Play 1 (localhost)
    participant P2 as Play 2 (claude_vm_pending)
    participant PX as Proxmox API
    participant P3 as Play 3 (localhost)

    U->>P1: vm_name="alpha,beta"
    P1->>P1: assert claude_vm_names is non-empty
    P1->>PX: /cluster/resources, /cluster/nextid (only for 2+ new VMs)
    P1->>P2: add_host alpha (proxmox_vm_id 9001), beta (9002), connection: local
    par fan out across forks
        P2->>PX: clone → VMID 9001
        P2->>PX: clone → VMID 9002
    end
    P2->>PX: apply cloud-init + hardware (tag unfinished), resize, start, untag
    P2->>PX: poll guest agent until IPv4 (retries × delay)
    P2-->>P3: facts proxmox_vm_address, proxmox_vm_stopped, proxmox_vm_resolved_id
    P3->>P3: add_host each VM with an address into claude_vms (connection: ssh)
    P3->>U: "alpha is up at 192.0.2.51"
```

**Play 1 — register.** Splits `vm_name` on commas into `claude_vm_names`, asserts the list is
non-empty (with no names the next play has no hosts and provisioning would report success having
done nothing), and turns each name into a placeholder host in `claude_vm_pending` with
`ansible_connection: local`. Nothing ever connects to those placeholders — every task in the role is
delegated to the controller.

When two or more of the names have no VM yet, play 1 also gives each of those a VMID, as the
placeholder's `proxmox_vm_id`, so play 2 can clone them concurrently (see [§9](failure-modes.md)).
The numbering starts at `/cluster/nextid`, so it begins where Proxmox would and honours the
datacenter's `next-id` range, and skips every VMID `/cluster/resources` lists, containers included.
Both are read with `uri` rather than `proxmox_vm_info`, which also queries every node holding a guest
and fails if one is offline. The header and CA path they send are `proxmox_api_headers` and
`proxmox_api_ca_path` from `group_vars/all`, shared with every other raw read; the token travels in
that header, so those tasks are `no_log`, and an assert after each reports a failed listing or
VMID read by status and reason, without it. A `proxmox_vm_id` set for the whole run is refused here
when more than one VM is new, before anything is created. A single new VM is left to Proxmox, as
before.

**Play 2 — create.** Runs `roles/proxmox_vm` across the placeholders so Ansible fans out over
`forks` (20 in `ansible.cfg`; a larger fleet proceeds in waves of that size, or pass
`ANSIBLE_ARGS="-f N"`). `proxmox_vm_name` defaults to `inventory_hostname`, which is the VM name.
An existing, configured VM that is stopped is left stopped unless `proxmox_vm_start_existing` is
set, and the role does not wait for its agent: it sets `proxmox_vm_stopped` and an empty
`proxmox_vm_address` and prints why. A VM an earlier run left unfinished is finished and started
whatever its status (see [§8](convergence.md)).

**Play 3 — publish and report.** Loops `add_host` from localhost over the facts the role left
behind, putting each VM that has an address into `claude_vms`, and prints one line per requested
VM: its address, that it was left stopped, or that it failed. The loop is over the names asked for,
not the hosts that survived play 2, so the address is read with a default — a VM whose run failed
has no fact at all, and reading it bare crashed the play, skipping configuration for every VM that
did come up.

> **Why publishing lives here and not in the role.** `add_host` sets `BYPASS_HOST_LOOP`, so it runs
> **once per task** regardless of how many hosts the play has. A copy inside the role publishes only
> the first VM of a fleet. Looping it from a single-host play is the only form that scales. The
> `ansible_connection: ssh` is explicit because `add_host` *updates* a host of the same name rather
> than replacing it, and the placeholders from play 1 are `connection: local`.

## 4.3 `playbooks/discover.yml`

Finds VMs by tag so `configure.yml` works standalone, not only straight after provisioning.

1. `playbooks/tasks/cluster_vms.yml` → `claude_vm_all`: every VM in the cluster, straight from
   `/cluster/resources` with `ansible.builtin.uri`. Not an unfiltered `proxmox_vm_info`, which goes on
   to ask every node that owns a VM for its VM list and fails outright if one node does not answer —
   so a single powered-off node in a cluster broke discovery for VMs on all the others. The listing
   already carries `name`, `vmid`, `node`, `status`, `template` and `tags`. The task is `no_log`
   because the request carries the token, so a second task asserts on the status and shows only it
   and the reason. `REQUESTS_CA_BUNDLE` is passed as `ca_path`, so the certificate recipe in
   [Proxmox access](../getting-started/proxmox-access.md#trusting-the-proxmox-certificate)
   covers this request too. It is `check_mode: false`: `uri` does not support check mode, so under
   `just deploy --check` it would skip itself and discovery would find nothing.
2. `claude_vm_tagged` (a lazily-evaluated group var) filters to the tagged QEMU VMs. Proxmox omits
   `tags` for an untagged guest, hence its `selectattr('tags', 'defined')`.
3. `claude_vm_discovered` is `claude_vm_requested`: narrowed to the names given (`vm_name`), else the whole
   fleet. `status.yml` narrows through the same group var.
4. Assert no duplicate names — Proxmox permits them, and the second `add_host` would silently
   overwrite the first, so `just deploy --only configure` would report success having touched one of two,
   unpredictably.
5. Per VM, its config and its agent's interfaces, two GETs to that VM's own node
   (`tasks/vm_addresses.yml`). A VM that is stopped, whose agent is not up or whose node is down is
   skipped; a refused token, a 401 or 403, is **fatal**, because it applies to every VM and swallowing
   it means configuring zero hosts and exiting 0, which looks like success. The role's agent wait reads
   the same refusal from its module's error with the `proxmox_access_denied` filter
   ([§7.1](custom-code.md#71-filter_pluginsproxmoxpy)).
6. `add_host` each VM for which `guest_address(proxmox_nic)`
   ([§7.1](custom-code.md#71-filter_pluginsproxmoxpy)) returns an address, with
   `ansible_python_interpreter` set to `claude_vm_python` ([§5.3](roles.md#53-common--os-baseline)), as
   provisioning's play 3 does. Without the MAC of that NIC
   the VM is skipped rather than guessed at — an address picked without one could be a bridge the
   guest grew later (`docker0`).
7. If nothing was found, print a diagnostic that distinguishes "no tagged VMs exist", "tagged VMs
   exist but none match the names given", and "tagged VMs exist but none reported an address yet" (stopped,
   still booting, or a token without guest-agent access).

Without names, step 3 keeps every tagged VM, so `just deploy --only configure`, `--check` and
`--only ssh-config`, `just login` and `just status` are fleet-wide by default.

## 4.4 `playbooks/configure.yml`

```yaml
import_playbook: discover.yml   when: claude_vm_discovery | default(true) | bool
hosts: claude_vms, become: true
  pre_tasks: wait_for_connection (300s), cloud-init status --wait (rc 0 or 2)
  roles: common → vscode_server → dev_tools → claude_code → github_projects   (each tagged)
  tasks: block (tagged tailscale): import_role tailscale
         rescue: append the failure to claude_vm_configure_failures, say it
```

```yaml
  post_tasks (tagged always): getent vm_user → claude_vm_workspace fact
hosts: localhost  (tagged always)
  collect {name, address, user, vmid, workspace} for every claude_vms host that has the fact
  import tasks/ssh_config.yml  state: present   → the connect line per VM
```

`claude_vm_discovery: false` skips the Proxmox API entirely, which is how the Vagrant and Molecule
paths configure a host that is already in a static inventory, and how `deploy.yml` configures only
the hosts provisioning published (§4.7). `cloud-init status --wait` accepts exit code 2 ("done with
recoverable errors").

The third play keeps an SSH alias per VM on the controller (§4.8), so `ssh alpha` and VS Code's host
picker follow the address DHCP gave it. It reads a fact the second play set *last*: `claude_vm_workspace`,
`vm_projects_dir` or `~/projects` of `vm_user` as `getent` resolves it on the VM. A host that failed a
role never reaches that `post_task`, so it never gets an alias; and the workspace is the VM user's real
home rather than a guess, because Ansible's own `user_dir` fact is the *connecting* user's home, which is
root's under Molecule. Both the lookup and the play are tagged `always`, like discovery, so a
`just deploy alpha --tags claude_code` after a lease moved still refreshes the alias. `claude_ssh_config: false`
keeps the whole project out of `~/.ssh`.

Tailscale runs last, from the play's `tasks:` - after `roles:`, before `post_tasks:` - in a block whose
rescue records a failure in `claude_vm_configure_failures`, with `just deploy <vm>
--tags tailscale`, and says it. The VM stays in the play, so it still records its workspace and gets its
alias, and under `just deploy` its sign-ins and VS Code; a fourth play, tagged `always`, then fails each
VM with such a failure - skipped when `claude_vm_configure_in_deploy` is set, as `deploy.yml`'s configure
import sets it, since deploy's own last play raises the same list (§4.7). `import_role`, not
`include_role`: a static import takes the block's tag, so `just deploy <vm> --tags tailscale` runs the role's
tasks, where an include's tag reaches only the include. Tailscale is optional, which is why only it gets
this treatment: a failure in it is the VM's last, not its first
([§5.8](roles.md#58-tailscale--join-the-vm-to-a-tailnet)).

> `when:` on an `import_playbook` propagates to the imported plays' tasks rather than skipping the
> import. Equivalent here because `discover.yml` is a single play.

## 4.5 `playbooks/destroy.yml` — confirm, release, delete

Destructive, so it is the most defensive playbook in the repository. It has `provision.yml`'s shape,
for the same reason: stopping a VM and removing its disks takes time, and none of it depends on
another VM. It used to loop `include_role` on localhost, which deleted the VMs one at a time: against
the fake, at 10 forks, 5 VMs took 40 s that way and take 14 s now, and 20 VMs 168 s
against 41 s.

**Play 1 — confirm and check (localhost).**

1. `vars_prompt` confirmation with `default: "no"` — a run with no terminal (CI, cron, a nested
   playbook) takes the default rather than hanging, so the default must be the safe answer. The
   question, under a `CONFIRM DELETION` block
   ([§7.5](custom-code.md#75-callback_pluginsclaude_on_proxmoxpy--the-waits-and-next-steps)), names the
   VMs from `vm_name` alone, through the play var `claude_vm_confirm_names`: a `vars_prompt` is
   templated before any inventory variable is in scope, `-e` answer or not, so naming
   `claude_vm_names` failed every run.
2. List the cluster (§4.3, step 1) and assert every requested name exists among this project's VMs.
   Asking for a name that was never created is an error naming the VMs that *do* exist, not a
   confirmation prompt followed by silence. A name that exists but is untagged gets a hint: tag it,
   or set `proxmox_vm_allow_untagged_delete`, which widens the check to untagged VMs — the role's
   escape hatch for VMs created before this project tagged its own, which this check used to make
   unreachable. A template is never admitted, with the flag or without.
3. With `destroy_release` or `destroy_save_sessions` (both the default: `claude_vm_reaching`), refuse a
   `destroy_sessions_dir` that is not an absolute path after `~` - the module that writes the saved
   sessions would put a relative one wherever it ran - then start each stopped VM through the API (`status/start`), wait
   for its agent to report an IPv4 address on `proxmox_nic` - by the MAC its config gives, as
   `vm_addresses.yml` pairs them, so a bridge the guest grew (`docker0`, `tailscale0`) cannot end the wait
   before DHCP has answered; asked 36 times at most, `claude_vm_release_agent_delay` (5) seconds apart, a
   play variable only `-e` changes, which `tests/unit/test_release.py` sets to 0.2 against the fake agent
   that answers on its third ask - list the cluster again and read every requested VM's address
   (`tasks/vm_addresses.yml`). The listing gives the status Proxmox last polled, up to ten seconds old,
   so a VM started seconds before is still "stopped" there: the VMs started are passed as
   `claude_vm_just_started` and asked whatever it says. Read by the listing alone, a VM that booted
   within those seconds got no address and was deleted unreleased (seen live). A VM that does not start
   or report one gets no address. The VMs started are
   kept in `claude_vm_release_started`, and stopped again (`tasks/vm_stop.yml`) by whatever ends the run
   without deleting one, so a failed destroy leaves a VM as it found it: a rescue around the listing, the
   addresses and the `add_host` - which can still fail - stops them and fails again; the last play stops
   each one whose deletion failed. Before any of it, a name two VMs carry is refused for the whole run:
   the role refuses to delete either, and the release finds a VM by name, so it could otherwise start, and
   reach over SSH, a VM that is not this project's.
4. `add_host` each name into `claude_vm_doomed` with `ansible_connection: local`, as provisioning's
   placeholders are, and `claude_vm_release_address`.

**Release (`claude_vm_doomed`).** Over SSH to that address as `vm_user` - host vars from the `add_host`
above, never play vars, which would apply to the release's delegated calls to Tailscale's API too and run
them, credential included, on the VM - all VMs at once: a VM with no address, or one a `ping` with
`ignore_unreachable` and no sudo finds unreachable or failing (`failed_when: false`: a failed host leaves
the run, and a play in which every host fails ends it), is ended and deleted as it is. A second `ping`, as
root, in a block: a sudo that asks for a password or refuses is an error `failed_when` never sees, and
would take the host out of the run unsaved and unreported, so the rescue records it
(`claude_vm_release_blocked`), and that VM is saved, then ended before any release, and deleted, the report
saying "sudo on it failed". Then, with `destroy_save_sessions`
(the default) and before anything is released, `claude_code`'s `release_pause` entry point stops Remote
Control, which writes the transcript of the session it serves as it works, where sudo works, and
`tasks/session_save.yml` saves what of the VM's Claude Code
sessions exists only there: Claude Code keeps a session on the disk of the machine it runs on, the only
copy it can resume, and auto memory is machine-local too, so the deletion would end them.
`claude_sessions_info` reads the VM's `~/.claude` with every session whatever its age and just code's
manifest, from just code's own directory as code.yml names it (the `just_code_dir` filter, which follows
`vscode_workspaces_dir`), and `claude_sessions_saved` keeps each session that holds a message and is not
a copy still as just code wrote it - this machine has that one - with the folder beside it (subagents,
tool results, MCP tasks), and every auto memory folder. What the VM reports is the VM's to make up, so
nothing it names is trusted: a folder not named as Claude Code names one (ASCII letters, digits and `-`)
or an ID that is not a session's is left out and counted, never handed on. `claude_sessions_fetch`,
delegated here, then runs `tar -cf - -T -` on the VM over SSH as Ansible reaches it - its user, address,
`ansible_port`, `ansible_ssh_private_key_file` and SSH arguments, with `BatchMode` - and unpacks the stream
itself into `destroy_sessions_dir` (`.sessions/` in the checkout, git-ignored and refused by the
tracked-file guard), `<vm>-<YYYYMMDD-HHMMSS>/projects/` laid out as on the VM and edited in nothing:
plain files and folders only, 0600 and 0700, at the names asked for or inside one, never through a link,
never outside that folder. A link, hard link, device or pipe in the archive is left out and counted;
rsync made the VM's links here as links, and the `chmod -R` after it followed them out of the folder. A
`sessions.json` beside them names each, the Claude Code version that wrote it and the newest of them,
says that what they hold is the VM's and unchecked, and gives the `claude --resume <path>` that continues
each here: given a
transcript's path, Claude Code resumes it where it lies and writes to it there, whatever this machine
holds of the same ID (the `configure` scenario holds that against the real claude). Not best effort like
the releases: a failed save - SSH or tar failing, an archive cut short, a name asked for that did not
arrive, or the VM no longer answering, which its VM tasks take with `ignore_unreachable` and an assert,
never leaving the run unseen - sets `claude_vm_delete_failure`, and `claude_vm_save_error` with the
failed task and its message alone, starts Remote Control again (`release_resume`) and ends the host, so
the VM is neither released nor deleted and the last play fails the run with why. Its VM tasks run as the VM user, never through sudo, whose files they read. With
`destroy_release: false` the host ends there, saved and not released. Then the `release` entry
point of `claude_code` (stop Remote Control's path unit and service, `claude auth logout`), `github_projects`
(`gh auth logout`, and no token revoked: revoking the one this project's device flow made, through
GitHub's `POST /credentials/revoke`, ended every GitHub CLI sign-in of the account, the controller's own
included - seen live - so the report says where to revoke it and what that does; the digest that flow
writes, `~/.config/claude-on-proxmox/gh-device-login`, tells its token from the vault token, whatever the
marker says, and from one pasted by hand) and
`tailscale` (start a stopped `tailscaled`, read `Self.ID`, `tailscale logout`, then with the API
credential the vault holds look the device up and delete it from the controller, only when its name is
`tailscale_hostname` or `<it>-<n>`, since the VM reports the ID). Each runs in a block whose rescue
records the failure: a release never stops a deletion. Each leaves one clause in a `*_release_said` fact,
set on every run of its file.

**Delete (`claude_vm_doomed`).** With play vars `ansible_connection: local` and this machine's Python
(`ansible_playbook_python`), since its hosts now carry SSH settings for the release. A VM the release
kept, its sessions unsaved, ends first. The role with `proxmox_vm_state: absent` across the
placeholders, fanning out over `forks`; it looks each VM up again and applies the ownership check per
VM. It runs in a block: `claude_vm_deleted` after it marks a VM gone, and a deletion that fails is
rescued - a play in which every VM fails ends the run - into `claude_vm_delete_failure` and
`claude_vm_delete_error`, its reason alone, raised by the last play. Its `known_hosts` task is `throttle: 1`, because parallel deletes would otherwise each rewrite
the same file and restore a key another had just removed.

**Play 3 — forget the alias (localhost).** `tasks/ssh_config.yml` with `state: absent` for the VMs
that are actually gone: the delete play sets `claude_vm_deleted` only after the role, which a host that
failed in it never reaches, and play 3 removes only the aliases of hosts that have it. A VM still there
keeps its alias, because it is there to connect to, and the line that says so names its reason: its
deletion failed - a stop that timed out, protection on, an API error (`claude_vm_delete_error`) - or a
save that failed kept it, its deletion never tried (`claude_vm_save_error`), or the run lost it before
its deletion, a task failing on it or the VM no longer answering. Said "its deletion failed" for every
VM kept, as it once was, it was wrong for a VM kept by its save. A run refused in play 1 never
reaches play 3; removing a block that is not there is a no-op. A localhost play rather than a task in the role, so the role stays free of the controller's
ssh config and the one writer per run needs no throttle. The same play says, in one line for every VM
of `claude_vm_doomed` and never two, what became of it. A VM deleted and released: what the release did -
joining the three clauses, and where its sessions were saved and how many entries of the VM's were left
out. A VM deleted unreleased: why - no address to reach it at, not answering, its sudo failing, or
`destroy_release: false` - and what is left of it to remove by hand and where: the Tailscale device
(which keeps its name, so a VM built again under it would join as `<name>-1`), a GitHub token its sign-in
made, and a Claude Code login, which Anthropic offers no way to revoke from outside - and where its
sessions were saved, or that they were not. A VM not deleted: "not deleted" and why, from the facts
above, never from a message's text, its SSH alias kept with it, then how far it was released first; and a
VM kept by its save, "kept, its SSH alias with it, neither released nor deleted" with the save's error.
Each line is recorded for NEXT STEPS straight after (`claude_vm_next`, matter `destroy`), `''` only for a
VM deleted and released with no GitHub token or Tailscale device left. The kept aliases' line is
recorded `''` while every VM it names has a line of its own there, which every VM still there has: it
named each VM and its rerun a second time. The report once looped over the deleted VMs alone, so a VM
released and then refused its deletion, or kept by its save, had no line of its own.

## 4.6 `playbooks/status.yml` — read-only, drawn live

What every VM this project created is doing, and what needs doing next. **Status changes nothing**: it
reads Proxmox through GETs and each VM over SSH, signs nothing in, starts and stops nothing, and adds
nothing on this machine, not a host key or an alias, and nothing to `~/.ssh` or `~/.claude`. It writes only its own run's files inside the checkout: the report, removed as
soon as the recipe has read it, and the log every recipe keeps. Proxmox's access log records its
requests and each VM its SSH login and its sudo, as for every run. `tests/unit/test_status.py` holds every task it runs, and every task
file and role entry point it reaches, to a fixed set:

- `uri` with the keys `url`, `headers`, `validate_certs`, `ca_path` and `timeout` only, a `url` that
  starts with `{{ proxmox_api_url }}/`, `check_mode: false` and `no_log`; no `method`, no `body`;
- the shared listing (`tasks/cluster_vms.yml`, `tasks/vm_addresses.yml`), `proxmox_vm`'s `settings_drift`
  entry point and the `status` entry points of `claude_code`, `github_projects` and `tailscale`, every file
  they include followed;
- `command` with one of a fixed list of argvs, the roles' reads (`gh auth status`, `tailscale status
  --json`, ...), and `systemd_service` with no state, which only reads a unit;
- `vm_status_info`, `git_checkouts_info` with `under` and `work` alone, `ping`, `getent`, `stat`, `slurp`,
  `set_fact`, `assert`, `add_host`, and the roles' `debug` and `set_stats`, which status's callback does not
  show;
- the report's `file` and `copy`, into `status_report_file`.

The recipe runs it as a dry run, which guards Ansible's other modules; `uri` never runs in check mode,
so every GET carries `check_mode: false` as the listing's does, and the allowlist guards the rest.

**Play 1 — list the fleet (localhost).** In one block whose rescue records why Proxmox could not be
read: the listing (§4.3, steps 1–3) and each VM's config and agent report (`tasks/vm_addresses.yml`),
both with `claude_vm_api_timeout` at `status_api_timeout`, 10 s; the version and the storages; the
template's config, whose `ctime` says when it was built; each of this project's storages' disks, whose
`ctime` dates each VM, since a clone's config keeps the template's. The
rescue tells a refused token from a host that did not answer by the listing's own status, and says what
to check for each. Then the managed SSH file's aliases, read only (§4.8). `status_fleet` turns all of it
into the fleet: this project's VMs, other VMs as totals only (never named, never read), the storages, the
template. A VM is read further only by a name no other tagged VM carries, that a recipe's pattern takes,
on a node that answers; the others are shown from the listing, and two VMs of one name are a problem.
A name given that matches no tagged VM is "not read", naming any nameless VM on a node that is down as
the one it may be. Each VM to read is added to `status_vms` by its name, with how to reach it on the
host itself and `ansible_python_interpreter: claude_vm_python`, as discovery adds them. With
`claude_vm_discovery` off, the inventory's `claude_vms` are read as they are: the configure scenario's
container, a Vagrant box.

**Play 2 — read the VMs (`status_vms`, `strategy: free`, `timeout: 30`).** Each VM's own GETs,
delegated to this machine, so the VMs are read at once: `status/current`, a day of `rrddata`, `pending`,
and from a running VM's agent `get-osinfo` and `get-fsinfo`. `proxmox_vm`'s `settings_drift` compares
the VM's config with what `just deploy` would send (`proxmox_vm_drift`,
[§7.1](custom-code.md#71-filter_pluginsproxmoxpy)); it needs no connection. Then, where a section asked
for needs it (`access`, `sign-ins`, `system`, `work`) and the VM runs and has an address, over SSH: one `ping`
with `ignore_unreachable` first, whose refusal is said for what it is - no host key held here, a host key
changed, a timeout, a refusal - and leaves the VM there; then each read in a block of its own whose rescue
records it as not read: `vm_status_info` (the OS, the kernels, a reboot waiting, the load, apt's
simulation, the user's claude processes and VS Code windows), `git_checkouts_info` with `work` over the
projects directory `just code` resolves, as the VM user (what of each checkout is on no remote or not
committed, as of its last fetch, [§7.6](custom-code.md#76-filter_pluginssessionspy-and-playbookslibrary--the-session-handoff)),
and the `status` entry points of
`claude_code`, `github_projects` and `tailscale` ([§5.5](roles.md#55-claude_code--install-claude-code),
[§5.6](roles.md#56-github_projects--sign-in-to-github-clone-an-accounts-repositories),
[§5.8](roles.md#58-tailscale--join-the-vm-to-a-tailnet)), the roles' own reads and reports, so a line
status shows is the one `just login` and NEXT STEPS give. Claude's never runs `claude`: whether `claude auth
status` can refresh a login's token is still to be checked on a real VM, so the login is the file it is
stored in being there, and the version where the installer's link points.

**Host keys.** Status adds and changes none. Each VM gets `ansible_ssh_args` from `add_host`
(`status_ssh_args`): `ansible.cfg`'s own `ssh_args` with `StrictHostKeyChecking=yes` and
`UpdateHostKeys=no`, OpenSSH's default being yes. As `ansible_ssh_args` and not the extra args, since ssh
keeps the first value it is given for an option and Ansible puts `ssh_args` first: extra args would come
after `accept-new`, and lose to it. A VM whose key this machine does not hold - rebuilt, renamed, its
address changed - is "not read", naming `just deploy <vm> --only configure`, which takes a new VM's key
as every run does, or the `ssh` that shows the fingerprint and asks.

**Play 3 — the report (localhost).** `status_report` ranks what every host found and works out the exit
code; the report's file is written when `status_report_file` is set.

**What a VM supplies is not trusted.** Each piece is a `status_found` fact, and `status_clean` makes
every string in it one line of plain text (`one_line`,
[§7.4](custom-code.md#74-filter_pluginsdesktoppy-and-filter_pluginstextpy)) before anything keeps it:
a newline, a carriage return, an escape sequence, a bidi control or a zero-width character never starts
a line of its own, and every value is capped. The text report and `--json` are both built from that
cleaned model.

**What counts.** Each item is "not read" (exit 2), a problem (exit 1) or a thing to do (exit 0), and the
code is the highest found:

| Level | When |
|---|---|
| not read | Proxmox did not answer, or refused the token. A name given that matches no tagged VM. A VM on a node that is down. A running VM whose agent reports no address on `proxmox_nic` after three minutes. A VM whose reads Proxmox did not answer. A VM that did not answer SSH, or whose host key is not held here or changed. A read inside a VM that failed or ran past 30 s. gh's login that could not be checked with GitHub |
| problem | Two tagged VMs of one name. A root disk or a storage 90% full. Remote Control failed or restarting. `tailscaled` not running where the role installed it |
| to do | The VM's settings differ from `local.yml`'s, with the `proxmox_vm_force_update` run that sends them and what else it does. A change waiting for a restart. A first deploy that did not finish (the unfinished tag). A VM with no SSH alias. A root disk or a storage 80% full. A reboot waiting, with the claude processes and VS Code windows open. Security upgrades waiting. Claude Code not signed in or not installed. Remote Control stopped or not set up. The GitHub and Tailscale roles' own lines, wherever NEXT STEPS would give them |

Stopped VMs, CPU pressure over a tenth of the last hour, other VMs, a thin pool provisioned beyond its
size, the template's age, Remote Control off on purpose, a gh left alone and work on no remote or not
committed are shown and not counted: a branch kept local is often on purpose, and WORK never says "clean". A run that failed or wrote no report exits 3.

**Live at a terminal.** The recipe chooses its own stdout callback,
`callback_plugins/claude_on_proxmox_status.py`
([§7.7](custom-code.md#77-filter_pluginsstatuspy-and-callback_pluginsclaude_on_proxmox_statuspy--just-status)),
the only callback of the run. At a terminal it draws the whole report at the bottom of the screen
within a second, a spinner wherever a value is still being read, and redraws it as each piece lands;
when the run ends the live region is erased and the finished report printed once. Piped, in the log,
under `--json` or at a terminal that cannot take it, only the finished report appears, or nothing. It is
drawn in the region every live output shares
([§7.8](custom-code.md#78-filter_pluginslive_regionpy-and-filter_pluginsscreenpy--every-live-output)), as
`just deploy`'s run is: nothing wraps, a resize pushes the screen into the scrollback and starts afresh,
the cursor is never hidden. The tab's title says `status · reading 3 VMs` while VMs are read, and `status · done`
at the end. `tests/unit/test_status_live.py` runs the real playbook at a pseudo-terminal of
its own and reads the bytes back through a small terminal (`tests/unit/vt.py`).

**The recipe.** `just status [names] [--only sections] [--json]` runs the playbook through
`tools/run-logged` with a report path of its own run in `STATUS_DIR` (`.cache/status/`, git-ignored and
refused by `tests/check_no_local_files.sh`, since the report holds addresses), then `tools/status-exit`,
which prints the report under `--json`, removes it and exits with its code. `--only` takes `vms`,
`storage`, `access`, `sign-ins`, `system` and `work`; `--only vms,storage` reads the API alone and never
opens SSH. `just list` is the same recipe by the name it had before.

## 4.7 `deploy.yml`

The whole thing: template, provision, configure, the three sign-ins, the editor - each stage the
standalone playbook, imported as is, so deploy is the sum of `just deploy --only template`, `--only provision`
and `--only configure`, the three sign-ins `just login` runs and `just code`, by construction. A first play on localhost asks the API whether a
template named `proxmox_template_name` exists and records it; the template play is imported with a
`when:` on that fact. The lookup is `proxmox_vm_info name=...`, so only the node holding that name is
asked for its VM list — unfiltered, one node that is down failed the run before anything was built.

`configure.yml` is imported with `vars: {claude_vm_discovery: false}`, so it configures exactly the
hosts provisioning's play 3 published. Running discovery there would disagree with provisioning
about what omitted names mean: `claude_vm_names` falls back to `claude_vm_default_name`, but
discovery tests `vm_name is defined` and falls back to the whole fleet, so a bare `just deploy`
created the default VM and then configured every tagged VM. It also repeated, one VM at a time, the
lookups provisioning had just made. Import `vars:` are play vars of every imported play, including
the nested `discover.yml` import, whose `when:` therefore sees them.

What keeps that from opening an SSH connection is the import-level `when:` itself. A `when:` on an
`import_playbook` propagates to every task of the imported play, and ansible-core counts the
implicit fact-gathering task as one of them - so a false condition skips the gather along with
everything else, and nothing connects, whether or not the play would have gathered facts.
`template.yml` still sets `gather_facts: false`, but only because the role reads no `ansible_*`
facts: that saves a pointless gather on the *first* run, when the play does execute. It is not what
prevents the SSH connection on later runs.

The result is that only the first run needs root SSH to the hypervisor. Every later `just deploy`
is API and VM traffic only.

After configuration come the sign-ins and the window, for the hosts configuration reached: a VM left
stopped is not in `claude_vms`, and a host that failed a role has been dropped from the plays after
it. `claude_login.yml` and `github_login.yml` are imported with discovery off,
`claude_vm_login_in_deploy` (a failed sign-in is said, not raised, so deploy goes on) and
`when: deploy_sign_in | bool`. The Claude one is interactive (`claude_vm_login_interactive: true`); the
GitHub one decides VM by VM, from what the Claude step left on each VM
([§5.5](roles.md#55-claude_code--install-claude-code)): a VM whose Claude sign-in
nobody answered (`claude_code_login_skipped` - Enter at the prompt, or a browser that never came back) is
left, and told so where the flow would have run (`github_projects_login_due`), with `just login <vm>
--only github`, the line NEXT STEPS keeps. With `github_login_auto_start` (the default) every other VM starts at once; off, each is asked
first (`claude_vm_login_ask`, the role's `github_projects_login_ask`): Enter signs in one whose Claude
sign-in was answered, `y` one that showed none (signed in already, or an API key). `tailscale_login.yml`
follows the same way with `tailscale_login_auto_start`, and `when: deploy_sign_in | bool and
tailscale_enabled | default(true) | bool` (§4.10): a VM an auth key joined during configuration is only
reported. A run with no terminal gets `deploy_sign_in: false` from the justfile's `no_tty_args`: sign-ins
that start by themselves would otherwise each wait for a browser nobody watches. A sign-in that failed,
or found its VM unreachable, is said with the `just login <vm> --only ...` that retries it and recorded
for NEXT STEPS under its own matter, since deploy raises it nowhere else.

Last, `code.yml` with `code_in_deploy`, when `deploy_code`. For several VMs its first task prints
each VM's `just code` line and ends the play. For one - the default VM of a bare deploy counts, so the
one-VM check does not need `vm_name`, and the VM is found by `claude_vm_names[0]` among the tagged
ones, not through `claude_vm_requested` - every task is in one `block` whose `rescue` says any failure
with the `just code` line and ends the play, so a deploy whose VM is done exits 0; outside deploy the
rescue fails again. A VM found stopped is said with `just deploy <vm>
--start`, since the deploy configured nothing on it. Ansible rescues no
unreachable host, so the first two connections `ignore_unreachable` and assert.

What a sign-in leads to - Remote Control's start, the private repositories' clones - runs in the
login playbooks' follow-on plays, and a failure there is not a failed sign-in: deploy's last play
fails each VM whose `claude_vm_configure_failures` is set, after VS Code has opened, with the command
that retries it - and so does a Tailscale failure during configuration, which `configure.yml` recorded
in the same list and, with `claude_vm_configure_in_deploy`, left to this play (§4.4). The configure
import passes `claude_code_sign_in_follows`, `github_projects_sign_in_follows` and
`tailscale_sign_in_follows` (`deploy_sign_in`), so configuration says that the sign-ins come next in
this run rather than naming `just login`, and the first play's message says up front that the run
will stop for them.

`just deploy --only ...` runs one stage's playbook on its own - `template.yml`, `provision.yml`,
`configure.yml` (`--check --diff` for `--check`, `--tags` for `--tags`) or `ssh_config.yml` - with the
arguments the whole deploy gives it and nothing of its own, so each stays the playbook deploy imports. The
recipe chooses the playbook from an `--only` its pattern has checked.

---

## 4.8 `playbooks/ssh_config.yml` and `playbooks/tasks/ssh_config.yml`

The aliases are the one thing this project writes on the controller besides `known_hosts`, and
every writer goes through one task file so the block format cannot drift. Inputs: a list of
`{name, address, user, workspace?}` and a state; `workspace` only feeds the connect line, and only
`configure`, which has read it on the VM, passes it. The managed file,
`~/.ssh/claude-on-proxmox.conf` (`claude_ssh_config_file`), holds one `blockinfile` block per VM,
with what ssh reads and nothing else - no state of this project's is kept in a user's ssh config:

```
# BEGIN claude-on-proxmox: alpha
Host alpha
    HostName 192.0.2.51          ← or alpha.<claude_ssh_host_domain>
    User dev
    ServerAliveInterval 30       ← claude_ssh_options, verbatim
# END claude-on-proxmox: alpha
```

The user's own `~/.ssh/config` gets exactly one line, `Include <managed file>`, inserted at the top
(`lineinfile`, `insertbefore: BOF`): an `Include` below a `Host` block is scoped to that block. It is
added only when the managed file is not reached already, which is ssh's to say rather than a regex's:
a line in a spelling ssh accepts for the same file - the absolute path, `~/...`, `${HOME}/...`, or
the bare name for a file in `~/.ssh` - counts only if `ssh -G -F <user config> <first VM>` comes
back with the `HostName` the managed file just gave that VM. `${HOME}/...` is expanded only from
OpenSSH 9.9 on (Ubuntu 24.04 ships 9.6, which reads it as a literal path), `$HOME/...` never, and
an `Include` inside a `Host` block reaches only that host; each of those includes nothing, and the
line is added above it. An existing line is left exactly as written, not rewritten into this form.
The file's mode is set only when this task creates it. The `Include` is never
removed, since that would mean editing the user's file on destroy, and an `Include` of a missing
file is harmless. Nothing else in `~/.ssh` is ever edited.

Three guards, in order: the alias is the VM's name, so a `Host` line in the user's config that
already names it is refused by file and line (the managed file is included first, so writing would
silently override the user's entry); every write is validated by the real client, `ssh -G -F %s
<alias>`, so a malformed option fails the task instead of every `ssh` on the machine; and a block is
only ever written for a VM the caller found by its tag. Before any of them, a name or address that is
not one plain word is refused: `ssh -G` accepts a well-formed `ProxyCommand`, so it cannot be the
check for a value the VM chose (see `guest_ipv4`, section 7).

`ssh_config.yml` (`just deploy --only ssh-config`) is the fleet-wide resync: the cluster listing, the addresses
(§4.3 steps 1 and 4), `present` for every tagged VM with an address, keeping a workspace an earlier
configure recorded, then pruning through `ssh_config_prune`
([§7.1](custom-code.md#71-filter_pluginsproxmoxpy)). Pruning removes blocks, never VMs, and only a block whose name no VM in the cluster carries any more. A name an untagged VM
carries is left alone and reported, as `destroy.yml` refuses such a VM. While any tagged VM is listed
without a name, which is what a node that is down looks like (§4.3), nothing is pruned, because any
block could be that VM's. With names, only those VMs are refreshed and nothing is pruned. Two
tagged VMs of one name are refused as discovery refuses them: an alias of that name would point at
one at random.

## 4.9 `playbooks/code.yml`

`just code alpha [--project discord-music-bot] [--session <id>]`: the alias step for one VM, then
the one step no other playbook does, launching the editor - with this machine's recent Claude Code
sessions copied to the VM on the way (**The session handoff**, below). In order:

1. Refuse anything but one name, and a `vm_project` (`--project`) that is not one GitHub repository
   name - up to 100 ASCII letters, digits, `.`, `-`, `_`, and not `.` or `..` - so nothing outside
   the projects directory, or nested in it, can be opened. The justfile's pattern and `tools/check
   project` refuse the same before a shell sees the value; this is for a direct `ansible-playbook` run.
   `vm_session` (`--session`) is refused unless it is a session ID, there by its pattern and `tools/check
   session` and here again.
2. Find the VM by tag, naming an untagged VM of that name as not this project's; ask its agent for
   the address. Settle where it is reached exactly as the alias's `HostName` does - the address, or
   `<name>.<claude_ssh_host_domain>` - so this run and VS Code connect to the same target, and refuse
   a VM that is not running, whatever its name resolves to.
3. Add it to `claude_vms` with `add_host`, the way configure reaches it: Ansible's own connection and
   `accept-new`, not the user's ssh config, and the group's connection variables from the inventory
   (a key, a port) apply as they do to `just deploy --only configure`.
4. Read the VM user's home on the VM with `getent`, delegated over SSH, unless `vm_projects_dir`
   says where the code is (and the sessions' directory there is given or not needed). Nothing is
   guessed on the controller, and nothing is read back out of the alias: the workspace is
   `vm_projects_dir` or `<home>/projects`. Then the session handoff's reads (`tasks/session_read.yml`),
   which with `--session` settle the project to open.
5. Refuse a `vm_projects_dir` that is not absolute (VS Code is handed it as written, from this
   machine, so `~` would not expand) and, with the terminal or the session copies on, a
   `vscode_workspaces_dir` that is not absolute or is the projects directory (its files would sit
   among the repositories), and with the terminal, one that is the folder being opened
   (`--project .claude-on-proxmox`). Each names the variable it is about.
6. Refresh the alias, then look for the folder to open - the workspace, or `<workspace>/<project>` -
   with `stat` on the VM. Read-only. Unreachable, missing and not-a-folder are each their own
   message: a missing project lists what the workspace holds and, for a name that differs only in
   case, suggests the right one, since the VM's paths are case-sensitive; a missing workspace means a
   VM never configured, and says to run `just deploy --only configure`. These are there so a typo is said in the
   terminal, not in a VS Code window opened on a folder that does not exist.
7. Check `code` is on `PATH`, and install `ms-vscode-remote.remote-ssh` if `code --list-extensions`
   lacks it. Then the session handoff's writes (`tasks/session_write.yml`), the first on the VM.
8. With `vscode_claude_terminal` (the default), write a workspace file on the VM and open that:
   `code --remote ssh-remote+alpha <workspaces>/project/<project>.code-workspace`, or
   `<workspaces>/<workspace's name>.code-workspace` for the whole directory, where `<workspaces>` is
   `vscode_workspaces_dir` or `<workspace>/.claude-on-proxmox`. One project's file is in `project/`
   so a repository that shares the workspace's name cannot collide with the whole directory's. Off,
   it opens the folder itself and writes nothing.
9. Say what VS Code will do, from the user's VS Code settings, read and never written: looked for
   where VS Code would put them (`VSCODE_APPDATA`, `XDG_CONFIG_HOME`, stock VS Code's place on macOS
   and Linux, the snap, the flatpak), and said as not found, not readable or not parsable when they
   are, since "unset" would promise a question that may not come; VS Code asks once per workspace,
   in a notification that goes quiet after a moment, so the message names *Tasks: Manage Automatic
   Tasks* as well.

In `just deploy` steps 1-9 are one `block` whose `rescue` says what failed with the line that opens
the window later (§4.7); outside it the failure is raised again, a command's with its stderr. Every
name `code.yml` sets starts with `code_`, a prefix no role uses, since a role's fact or register
outranks a play var, and no task var shares a fact's name.

**The workspace file** is built as data and written with `to_nice_json`, so no path breaks its JSON;
`copy` leaves an unchanged file alone. It names the folder by absolute path, hides its own directory
from the explorer of the window that shows it, and holds a `Shell` task - `/bin/bash -l` in the folder,
`folderOpen` too, in the same presentation group as Claude's so the two share a terminal as split panes,
the shell on the left because it is listed, and so started, first - and the `Claude` task: a `process` task,
`/bin/bash -lc <script> just-code` and eight arguments (the line without `claude`, the two lines a
sign-in ends with, the path unit's file, GitHub's line, the git protocol, the scopes, the VM's name),
the session notes' four (below) and `--session`, read as the last word by a `for` loop,
`cwd` the folder, `runOptions.runOn:
folderOpen`, presented in a dedicated, focused terminal. A login shell, because VS Code starts task
shells non-login, and on a VM configured before `common`'s sshd PATH drop-in `claude` is on the login
`PATH` only (`just login --only claude` uses `bash -lc` for the same reason). The script checks `claude` is installed, runs `claude auth login` when
`claude auth status` fails (a failed one says how to retry), signs gh in by the device flow when
`gh auth status --hostname github.com --active` fails (Ctrl-C skips it: `trap ':' INT`), and then,
given `--session`, `claude --resume <id>` and nothing after it, or else
`claude --continue` - the folder's latest conversation; where there is none, Claude Code 2.1 exits 1 at once ("No conversation found to continue"), and a `--continue` that fails within five seconds starts a fresh `claude` instead, the script ending `exit 0` either way - because a task terminal
does not survive a window reload, after which the task runs again; its messages arrive as arguments,
never as script text, and
`tests/unit/test_code_session.py` runs it against a stand-in `claude`. `${workspaceFolder}`
is not used: in a task that belongs to a workspace file rather than a folder it is ambiguous.

Sign-in is not scripted, because it cannot be: it is a browser flow. VS Code Server puts its
`browser.sh` helper in `BROWSER` for every terminal it starts, which opens the URL on the client,
and `claude` shows the URL as well and accepts a pasted code. A login made there starts Remote
Control through the path unit of [§5.5](roles.md#55-claude_code--install-claude-code) where one
waits, which the script finds by its file on the VM; elsewhere the terminal names `just deploy <name> --tags claude_code`, which starts it through
the role's own eligibility checks. The terminal never starts it itself.

**What VS Code decides, and what this does not touch.** VS Code runs a `folderOpen` task only in a
trusted window, and only once automatic tasks are allowed:

| Gate | Where VS Code keeps it | What `code.yml` does |
|---|---|---|
| Workspace Trust | client-side, by URI, per remote authority; a trusted folder covers its children. A workspace file is trusted when its folders *and* the file itself are | Puts the files under the projects directory, so trusting that directory once covers every project and every file. Says so. |
| `task.allowAutomaticTasks` | an application setting in the default profile's `settings.json`; unset, VS Code asks once and *Allow* writes `"on"`; only a user-set `"off"` silences both question and task | Reads it with the `vscode_setting` filter (JSONC: comments, trailing commas, flat or nested key) and says which question to expect, or that the terminal will not start. Never writes it. |

Both are the user's own security decisions, so there is no `security.workspace.trust.enabled:
false`, no `terminal.integrated.allowInUntrustedWorkspace`, no written setting and no user-level
`folderOpen` task, which would fire in every workspace they open. Checked against VS Code 1.140's
source: trust is computed over the folders plus the workspace file's own URI, automatic tasks
include a workspace file's tasks, and a path ending in `.code-workspace` given to `--remote` opens as a
workspace. The defaults moved twice in 2026 (1.109, 1.126); CI cannot watch the user's editor, so a
manual run of `just code` is the check when they move again.

The workspace file, the session notes (below) and the session copies (below) are all that is written
on the VM, last, as the VM user, so every refusal above leaves the VM as it was - with `--session`, so is
one checkout's fast-forward; a workspace directory that already exists keeps its mode, since
`vscode_workspaces_dir` may name one of the user's own. `tests/unit/test_code_session.py` holds
every task in `code.yml` to an allowlist - read-only here, or delegated to the VM - and the
`provision` scenario checks the settings file it planted is byte-for-byte untouched after every
run. `--project .claude-on-proxmox`, which names the files' own directory, is refused. Cloud-init placed the key, so a VM that was provisioned but never configured still
opens once it has a workspace, and its terminal says to run `just deploy --only configure`.

**The session notes.** With `vscode_session_notes` (the default, with the terminal), a page about the
VM opens beside the Claude terminal every time the window opens - from `just code`, *Open Recent*, a
restart that restores the window, or a reload - as a rendered preview, with its sign-in status read from
the VM at that moment. VS Code has no workspace setting that opens a file, and a file named in the
`code --remote` call opens only from `just code` and stops the window restoring its editors, so the trigger
is a second `folderOpen` task in the workspace file, `Session notes`, run beside the Claude task on the
same terms (a trusted window, automatic tasks allowed); VS Code's one-time automatic-tasks question lists
both. Not the Claude task's `dependsOn`, which was the first design: VS Code looks a dependency of a
workspace-file task up, misses it, and asks every task provider instead - npm, gulp (which runs `gulp`),
grunt, jake - which held Claude back about 7.5 s on a projects directory of 22 repositories, measured
live. Side by side, the notes open a moment after the Claude terminal and usually take the keyboard
focus from it. The helper's bash (`code_notes_script`) is in charge whatever its Python part does:
Python runs in the background, bash waits for it 5 s at most, runs `code -r <notes>` in the foreground
through the remote CLI VS Code Server puts on every task's `PATH`, and ends `exit 0`, so the notes always
open and VS Code never prints the command line. Python reads `claude auth status`, the Remote Control unit, gh's login and `tailscale status
--json` at once, 2 s each, and rewrites only the section between `<!-- claude-on-proxmox:live -->` and
its closing marker, atomically, 0600, in a regular file that has them; every value is cleaned as
`plain_text` cleans, no command it runs prints a token, and the Tailscale row carries the `ssh` command
over the MagicDNS name, which only the VM knows. Its time is in this machine's zone, which is the user's
where the VM's clock keeps `common_timezone` (UTC): `code_timezone` is `TZ`, then the zone `/etc/localtime`
links into a zoneinfo directory for (macOS, most Linux), then `/etc/timezone` (Debian, where
`/etc/localtime` may be a copy), a zone's name only, else UTC, read with `realpath` and lookups since the
play runs nothing here but `which` and `code`; the helper gets it as an argument and says UTC for a zone
the VM's tzdata lacks. The Claude task gets four more arguments - the
helper, the notes' path, the unit, the zone - and after a sign-in that succeeded in its terminal starts the helper
once more, `refresh-only`, in the background (`shift 8`: `${10}` would be VS Code's to resolve).

`just code` writes the rest from `playbooks/templates/vm_session.md.j2` into
`<workspaces>/session/<vm>-session.md` (0600; `session/` 0700): the VM's facts from the cluster listing,
its addresses and alias, and the commands with its name - nothing of one window's, since every window of
the VM opens the one file, and no time, so an unchanged VM gives an unchanged file. It `slurp`s the
notes first and carries their live section over. `workbench.editorAssociations` in the workspace's
settings opens them as a preview; the key holds a `/`, so VS Code matches it against the file's scheme
and path.

The notes' directory is a **second workspace folder only where the window's own folder contains it** -
the whole projects directory's window under the default `vscode_workspaces_dir`. VS Code computes trust
over every folder and the saved workspace file, each `isEqualOrParent` of a URI the user trusted, and
trusting stores exactly those URIs (`workspaceTrust.ts`): a folder added to a workspace trusted before
is untrusted, and the window drops to Restricted Mode without its Claude terminal. A one-project window,
and every window under a `vscode_workspaces_dir` outside the projects directory, keep their one folder;
they open the notes from outside it, which VS Code asks about once per workspace
(`requestOpenFilesTrust`, remembered on *Open*) unless a trusted folder covers them - the projects
directory, trusted in its own window - and `just code`'s message says so.

**The session handoff.** With `vscode_claude_sessions` (the default) every run copies this machine's
Claude Code sessions of the last 30 days - Claude Code's own `cleanupPeriodDays` by default, by their file
time, so Claude Desktop's, which Claude Code keeps here at any age, come over within it like the rest - to
the VM, those of the repositories it has checked out; `--session <id>` (`vm_session`) copies that one whatever its age, the
sync on or off, and has the Claude terminal resume it. It rests on how Claude Code 2.1.308 resumes:
by the folder a transcript is filed in under `~/.claude/projects/`, never by the directory recorded
inside it, `--continue` taking the newest file of the current folder only, and the `/resume` picker
listing other folders' sessions under Ctrl+A - never those started with `claude -p` or the Agent SDK, or
whose first prompt was `/loop`, which resume by ID alone. `claude --resume <id>` looks in the current directory's
folder and its worktrees' first, then in every other folder, and resolves an ID only one of those holds
(since 2.1.223): moving the transcript into any folder of the other machine is the way across machines
Claude Code's own Agent SDK documents. The rules:

- **Read-only on this machine.** `claude_sessions_info` (`playbooks/library/`) lists the transcripts
  in `code_session_source_dir` (`CLAUDE_CONFIG_DIR` or `~/.claude`) and returns what each says about
  itself - its first `cwd`, the newest `version`, the last `gitBranch`, whether it has a message, and
  from `sessions/` whether a live `claude` has it open: at a pid whose command line, from `/proc` or `ps`,
  is still claude's, since an entry a claude left behind can name a pid another program took since -
  never what was said; `git_checkouts_info` runs `git --no-optional-locks`, which takes no index lock,
  for each first directory and the checkout of a `.claude/worktrees/<name>` one, taking each path and
  branch as written (`expand_user_and_vars=False`, no `path`-typed option, which expand `~` and `$VAR`),
  its status with `--untracked-files=normal --ignore-submodules=none` whatever the checkout's config
  hides, and each origin without what precedes the host, where a token can sit. Nothing here is written, so the controller still writes only the
  SSH files of §4.8.
- **Selection by origin, decided against the VM.** `git_checkouts_info` reads the origin of every
  checkout in the VM's projects directory, as configured - never rewritten by a `url.<base>.insteadOf`,
  which names where git connects, not the repository - and `claude_sessions_plan`
  (`filter_plugins/sessions.py`) matches `owner/repo`, without regard to case, against each session's:
  never a folder's name, which renamed clones, subfolders and other owners' repositories of the same
  name get wrong, and never the GitHub listing, which differs from what is cloned (a private repository
  with gh signed out, one created since). On the VM only a GitHub origin names a repository
  (`github_repository`: https, ssh, scp-like, `ssh.github.com:443`), as just deploy clones them, so a
  checkout with a local path or another host's URL there claims nothing of this machine's; here any form
  does (`git_repository`), since a clone may reach GitHub through an SSH alias of the user's own. A gone directory falls back to its worktree's checkout, then to
  `githubRepoPaths` in Claude Code's `.claude.json`. `sessions/*.json` lists only running processes,
  so it serves the open-session check and nothing else. Of two checkouts of one repository the one
  named after it is taken, else neither.
- **Filed under this machine's folder names.** A copy keeps its folder (`-Users-you-repo`), so
  `--continue` in a checkout never picks it up and the window's default terminal stays the VM's own
  conversation. A session written by a Claude Code newer than the VM's (`claude --version` there,
  from the native installer's path or npm's, never `PATH`) is left here and named. So is a session in
  two folders here (`twice`), which `claude --resume <id>` would resume from neither once both were on
  the VM, and one a `claude` on the VM has open (its `sessions/` registry there, `registered`),
  whose copy is never replaced under the claude holding it.
- **Four edits, nothing else** (`claude_sessions_copy`): the memory folder in each `prompt_snapshot`
  attachment becomes `<VM ~/.claude>/projects/<checkout's folder>/memory/` - read from the entry's
  "persistent file-based memory at" path, never computed, because `--system-prompt-snapshot` is on by
  default and resume sends the recorded prompt until the conversation is compacted; a copy whose
  prompt still names its own memory folder here after that, in a form the edit does not know, is not
  written; another project's memory folder, which a prompt can quote, stays as text. The session's own
  `tool-results/` folder of this machine's, under any project folder (a session continued after `/cd`
  names the old one), becomes the copy's on the VM, in user entries only - the tool results that say
  where a large output was saved, and `toolUseResult` - so that a resumed session can read them; an
  assistant's entries, which can carry signed thinking, are never changed. When the last
  `worktree-state` entry binds a worktree, a cleared one is appended: Claude Code clears a binding to a
  worktree that is gone by itself, which is the VM's usual case, but refuses to resume one whose path is
  there and is not that worktree - the `configure` scenario's, where both "machines" hold the path. A
  `relocated` entry naming the checkout comes last, so
  that the picker opens it there and names the `cd` from anywhere else; `claude --resume <id>` itself
  is not guarded by it. Every other line is copied byte for byte, the last incomplete one left out -
  among them the records Claude Code keeps since 2.1.300 of each request it sent
  (`api-request-shape`, `api-request-blob`), which still name this machine's memory folder: a resume
  sends the recorded prompt, never them, as the `configure` scenario holds against the real `claude`.
- **Replace only what the last run wrote.** The manifest in `<workspaces>/claude-sessions/` holds the
  digest of each copy written. A copy that still matches is replaced; one that differs was continued
  on the VM, or was never this project's, and is left alone and named - as continued when nothing new
  came from here, as changed on both otherwise. The appended entries rule a byte-prefix check out. A
  copy that is gone while its ID is in another folder of the VM's moved there - `/cd` in a resumed
  session moves its transcript into the new directory's folder - and counts as continued the same way:
  written again, the ID would be in two folders, and `claude --resume <id>` from a third finds neither.
  Unless the copy in that other folder is this project's own, still as written: then the session moved
  here (`/cd` on this machine), and it is written in its new folder and the old copy removed, which
  otherwise reads as changed on both forever. The manifest is written after each copy, so a run that
  stops part way still records what it wrote; one copy written and not recorded would read as continued
  on the VM once the session grew here.
- **Kept from Claude Code's sweep.** A copy keeps this machine's file time while that is within 14
  days, and is otherwise given the time it is written, renewed once that is 14 days old: Claude Code
  deletes a transcript 30 days after its file time, which would take a `--session` copy of an older session
  before it was resumed, and a session copied at 29 days the next day.
- **Back before a destroy.** The sync only ever goes to the VM. What a VM holds of its own - sessions
  begun there, copies continued there, auto memory - `just destroy` saves here first (§4.5).
- **The transport.** `rsync -az --files-from=-` over the alias, as VS Code reaches the VM, with
  `BatchMode`, into a mirror in that directory (0700; macOS's `openrsync` has no `--chmod`), which
  later runs only add to; to a local path where Ansible reaches the VM locally, as in the `provision`
  scenario. Its 23 and 24 - a file it could not read, one that vanished - are not failures: the rest
  went, and the copy names a session that did not arrive. Any other failure, rsync missing on either
  machine among them, fails `--session` alone; the sync says it and the window opens with the copies there
  are. Subagent transcripts never go: resume sends the same history without them. Sessions that
  leave the window leave the mirror; their copies stay, for Claude Code's own clean-up on the VM.
- **No transcript content in output or logs.** Both modules that read transcripts are `no_log`, and
  the messages carry IDs, folders, branches and counts only.

With `--session`, `session_read.yml` refuses, before anything is written: a session not found or in two
folders, without a message, open here or on the VM; a directory here that is gone, not a checkout, or
holds uncommitted or untracked changes; a branch that is detached, gone, untracked, tracking another
remote than `origin`, or not where `origin`'s is; no VM checkout of the repository (`--project` must name
it when given); a VM checkout with changes, on a detached `HEAD` that no branch, remote-tracking branch
or tag holds (a switch away would leave its commits to garbage collection, and `--quiet` would not say
so), or with a `claude` of the VM user working in it (from `/proc`). `session_write.yml` then copies,
refuses a session changed on both machines since the last copy - naming the folder its copy is in -
fetches `+refs/heads/<upstream>` - a refspec, never an option - refuses a move that would replace a path
git does not track there (`git_checkouts_info`'s `target`: an ignored file the commit adds, which `git
status` does not show and git overwrites without asking, or a file where the commit has a folder), and
brings the checkout to the commit by fast-forward only: `merge-base --is-ancestor` first, so a VM branch with commits of its own
is never switched to or moved, then `switch` and `merge --ff-only`, or `switch --create` at the
commit and `--set-upstream-to`, and asserts `HEAD` is that commit. The terminal never forks: a fork
carries the recorded prompt too, and the window's task runs again on every reload, which would fork
afresh from the copy each time. `tests/unit/test_session_handoff.py` holds the filter and the modules,
`tests/unit/test_code_session.py` runs the tasks between two real checkouts with a marker string in
the transcripts that must reach no output, and the `configure` scenario resumes a copy with the real
`claude` before its stand-in replaces it, then a copy continued there, saved as `just destroy` saves one,
by its path.

## 4.10 `playbooks/tailscale_login.yml`

`github_login.yml`'s shape without its follow-on play, since nothing follows a Tailscale sign-in: the
reach play (every VM at once, `ignore_unreachable`, an unreachable one recorded as failed); the sign-in
play, `serial: 1` when names are given or under deploy and the fleet at once for a bare report, its
`block` including the role's `login` entry point with the condition on the include task and a `rescue`
that re-raises `pause`'s abort and records the failure; and a last play failing those VMs, which deploy
skips. The role decides everything ([§5.8](roles.md#58-tailscale--join-the-vm-to-a-tailnet)): a VM
with Tailscale off, not installed, or installed by hand gets one line and nothing else, a VM signed in already is reported, and only one this role installed that
is waiting in `NeedsLogin` is asked (under deploy) and signed in by the web login.

## 4.11 `playbooks/login.yml`

`just login` without `--only`: discovery once, then `claude_login.yml`, `github_login.yml` and
`tailscale_login.yml` imported as they are, with discovery off - so it is their sum and cannot drift from
them. As under deploy, a VM goes on to its next sign-in after one that failed, or after the work it leads
to failing (Remote Control's start): each playbook's last play leaves what failed
(`claude_vm_login_raised_later`), and `login.yml`'s own last play fails each such VM once all three have
had their turn, with every message. Between them it passes what deploy passes (§4.7), but only where names were given
(`claude_vm_login_named`, the play var both later sign-in plays keep): GitHub and Tailscale start by
themselves for a VM whose Claude sign-in was answered, are asked about first with
`github_login_auto_start` or `tailscale_login_auto_start` off, and are left for one whose Claude sign-in
nobody answered, which each says, with the command that starts it. Without names every step reports,
nothing starts, and nothing is said of a sign-in left. `claude_vm_login_in_deploy` is
not set, so GitHub's follow-on play runs the whole `github_projects` role for each VM named, as
`just login --only github` does, since no configuration ran just before it. `--only claude`, `github` or
`tailscale` runs that playbook alone.

## 4.12 `playbooks/init_settings.yml`

`just init`'s questions, after `just install`, the git hook and the copies of the three example files: one
play on `localhost`, run with an inventory of its own, `playbooks/inventory/init.yml` - the controller,
as `inventory/controller.yml` has it, with pipelining on, and a run that keeps the modules' files
(`ANSIBLE_KEEP_REMOTE_FILES`, which turns pipelining off) refused - so no `group_vars` load. It runs before the
vault can be opened and while every value is still the example's, and reads what it writes back from the
files, since a run does not reload `group_vars`. The inventory is not in `playbooks/` itself, where the
syntax check and ansible-lint would take it for a playbook.

It reads `hosts.yml` and `local.yml`, and the vault: as text when it is plaintext, through `ansible-vault
decrypt --output -` under `no_log` when it is encrypted and there is a password (`.vault_pass`, or
`ANSIBLE_VAULT_PASSWORD_FILE`); an encrypted vault with neither is left alone, and said. Every `ansible-vault`
it runs has `ANSIBLE_VERBOSITY` and `ANSIBLE_DEBUG` at 0 (`init_ansible_vault_env`), whatever the environment
or `ansible.cfg` says: either adds lines to the stdout the vault's text is read from. Then four
questions, each through `tasks/init_ask.yml` and only while its value is still the example's: the host's
address (written at once, by one `lineinfile` on the example's `ansible_host: 192.0.2.10` line, comment
kept, validated as YAML and read back); the token's ID, while there is no secret and `local.yml` names no
token (only the parts that differ from `ansible@pve!ansible` are written, by `tasks/init_local_setting.yml`,
in place of their commented example lines); the GitHub username; and last the secret, hidden - last so
that a token the offer below makes has its secret written into the vault with no question between. Each answer is held
to its kind by the `init_answer` filter (`filter_plugins/settings.py`); one that breaks the rule is asked
for once more, saying it, and Enter leaves the value for the next run. The address in effect is the first
`proxmox` host's `ansible_host`, else its name, as `defaults.yml` reads it. Tailscale is not asked about:
while `local.yml` does not set `tailscale_enabled`, a `NOTICE` says it is on, by the vault's auth key or the
sign-in page, and how to turn it off, writing nothing, so a later change to the default still reaches the
person; `--tailscale on` or `off` writes the setting, by `tasks/init_local_setting.yml`.

Before the secret, at a terminal, while the vault has none and none was given, `tasks/init_access.yml`
offers to make the token: the one change `just init` makes on Proxmox. It adds the first `proxmox` host as
`hosts.yml` has it (`add_host`, its connection settings passed on), and runs `tools/proxmox-access.sh` there
as root over SSH with the `command` module - `/bin/sh -s` with the script on stdin, never a copy left on the
host. `--list` names the node's storages for VM disks and its bridges, which the script reads from `pvesh`
and nothing changes; the storage and bridge in effect are kept when the host has them, a default it lacks is
asked for among what it has (`tasks/init_access_choose.yml`, written into `local.yml`), and one `local.yml`
sets that the host lacks is said and left. `--dry-run` prints the `pveum` commands it would run, which the
question shows with what exists already; on `y` the script runs them, creating only what is missing - the
role, the user, its grants where the user does not hold the role's privileges, and the token, last - and
changes nothing that exists. Its stdout is the token's secret and nothing else, read by a `no_log` task
into the hidden answers, as a typed secret would be; what it said, on stderr, is said. `n`, Enter, a root
SSH that fails or a step that cannot be done says how to run the script by hand, and the secret is asked
for. The offer and the secret are the last tasks before the vault's write, nothing between them, and that
write is a block whose rescue deletes a token this run made (`pveum user token delete`) while the vault is
unwritten, since its secret would be lost, and fails with what went wrong. Once `ansible-vault` has written
the vault, which then holds the secret, the token is kept whatever the read-back says. The connection settings
come from `hosts.yml` as the template build reads them: `all`'s vars, then the `proxmox` group's, then the
host's own.

`init_ask` is false unless the justfile sees a terminal and was not given `--no-prompt`, and
every write is under it, or under a value given on the command line: `init_given`, the JSON
`just init` built from its options, each held to its characters first, and the secret from
`--token-secret-file`'s file or
`CLAUDE_ON_PROXMOX_TOKEN_SECRET`, read under `no_log`. Every given value is held to its rule before
anything is written, so a run failed by its last one leaves the files as they were; it fills only what is
still the example's, and fails the run, naming both, when it differs from what is set (compared in its
normal form, a name or a GitHub login case-insensitively); equal, it is nothing to do, so the same command
runs again with nothing changed. The vault is written last and once: the `with_setting` filter puts the
answers in place of the example's lines in memory, and the text reaches `ansible-vault encrypt --output
<vault> -` on its stdin under `no_log`, never through `copy`, which decrypts what it writes. A plaintext
vault is always encrypted at a terminal, since every recipe that opens it refuses one, and a vault a given value went
into is encrypted whatever the run. The answers go in only where the line holds its value whole; a value
over several lines, which the filter's read-back catches, is said and left to `just vault`. `.vault_pass` is
made only when there is no password anywhere: `O_EXCL`, `0600`, by the run's own Python. `ansible-vault` is
told which password encrypts (`--encrypt-vault-id default`), since with `.vault_pass` and
`ANSIBLE_VAULT_PASSWORD_FILE` both it has two, and what it says when it fails is said, holding no secret.
The vault is decrypted again and compared with what was meant. The run ends with what it wrote and what is
still missing - a vault left plaintext among it - and a run with no terminal (or `--no-prompt`) that was
given values but still lacks the address, the secret or the username fails, naming them. What is still
missing, and a vault no recipe can use yet, are recorded for NEXT STEPS (`claude_vm_next`, under the key
`just init`, matter `init_settings`) while a value is missing or the vault cannot be opened - when the checks
below stop at once or do not run. Once every value is set it records nothing and names no `just deploy`: the
checks run next and say what comes after.

## 4.13 `playbooks/init_check.yml`

`just init`'s second step, a second `ansible-playbook` run with the project's inventory and the vault (and
`init_ask` and `init_given`, as the first had) - the
password looked for when the recipe runs, since the questions may have just made `.vault_pass` - so it sees
the settings exactly as `just deploy` will. The recipe skips it, with one line, when the vault is
encrypted and neither `.vault_pass` nor `ANSIBLE_VAULT_PASSWORD_FILE` can open it. One play on `localhost`,
every task a read (held by `tests/unit/test_init_check.py`):

1. **Configured yet?** With the address or the secret still the example's, it says what is missing and ends:
   nothing is checked, and that is no problem.
2. **The vault** is encrypted: only whether its text starts as a vault does is kept.
3. **The API**: `GET /version`, `no_log` since the header carries the token, as `tasks/cluster_vms.yml` does,
   with `ca_path`. 200 is `ok`, with the version; a 401 or 403 names the token; a connection that fails,
   the address; a certificate error, the CA steps.
4. **Privileges on `/vms`**: `GET /access/permissions?path=/vms`, against `init_token_privileges.vms` and the
   guest agent's, by `/version`'s major: `VM.Monitor` on 8, `VM.GuestAgent.Audit` on 9. A privilege is
   granted when it is listed, and reaches the VMs under `/vms`, where Proxmox checks it, only with 1, its
   propagate flag: a 0 is said with the `-propagate 1` that fixes it, and a token holding none at all with
   the privilege separation that usually explains it. Storage and bridge are not checked: their names may
   still be the defaults, wrong for the host.
5. **The template**, from the cluster listing, as `deploy.yml` finds it: a VM named `proxmox_template_name`
   that is a template. Found, it must be on `proxmox_node`, where the clones are made. Missing, deploy builds
   it at `proxmox_template_vmid`, so a guest there - any, on any node, one that is down included - is a
   problem naming it. A listing that fails is a problem said with the rest.
6. **Root SSH**, only while the template is missing (or the listing would not say): `hostname -s`, delegated
   to the `proxmox` group's host with `ignore_unreachable`, which proves the connection the build will use
   and reads the host's short name, its node name (`init_ssh_node`), which must be `proxmox_node`, since the
   template is built there. No host in the group is a problem too. `accept-new` may add its host key to
   `~/.ssh/known_hosts`, one of the two files the project writes there.
7. **The node**, worked out, never chosen: the template's node; without a template, the only node there is,
   or the node root SSH said the host is. Equal to `proxmox_node`, it is `ok`. While `proxmox_node` is the
   default, the node is written into `local.yml` in place of its commented line - at a terminal only, with no
   question, since there is nothing to choose - and set there, a node that differs is a problem naming both.
   A node SSH named that the cluster does not list, or several nodes and no SSH, is a problem too.
8. **The storage and the bridge**, on that node, through `tasks/init_choose.yml`: the storages for VM disks
   (`GET /nodes/<node>/storage?content=images&enabled=1`) and, while there is no template, the bridges
   (`GET /nodes/<node>/network?type=any_local_bridge`), both filtered by Proxmox to what the token may see,
   each with its own permission read (`Datastore.AllocateSpace` and `Datastore.Audit` on `/storage/<name>`,
   `SDN.Use` on `/sdn/zones/localnetwork/<name>`, by the `init_candidates` filter). The value in effect, listed
   and usable, is `ok`; set in `local.yml` and not listed, or lacking a privilege, a problem; the default, not
   listed, a question at a terminal - y or n for one usable candidate, a number for several, the unusable
   shown with what they lack, or `inactive` (listed, but not reachable) - whose answer is written. With a
   template, the bridge is its `net0`'s and only `SDN.Use` on it is checked; a template with none on `net0` is
   a problem. A token that sees no candidate at all is told which privileges make Proxmox list one, with the
   `pveum aclmod` line. Bridges are the local zone's: an SDN VNet is neither listed nor checked. Until the node
   is known, nothing is listed, asked or written for either, and each is noted as not checked; a listing
   Proxmox refuses is a problem. `--storage` and `--bridge` given are held to the same lists before anything is
   written, written whenever the token can use them, and refused while they cannot be checked; `--bridge` beside
   a template is nothing to do when it is the template's own, and refused otherwise.
9. **A key for the VMs**: `vm_ssh_public_keys`. `vm_password` alone is a problem - it is for the Proxmox
   console, and deploy configures the VMs over SSH, which takes a key; with neither, the public keys in
   `~/.ssh` are listed and the `local.yml` line suggested.

Each result is `ok`, `!!` with its fix, or `--` not checked, printed together at the end, and the run fails
when any is `!!`. NEXT STEPS (`claude_vm_next`, under the key `just init`, matter `init_check`) then gives
each `!!` line with its fix; with none, what could not be checked; with neither, `just deploy alpha`, which
the report's last line says too - unless the GitHub username is still the example's, which the questions'
NEXT STEPS names. A run that ended at step 1 records nothing. It writes `local.yml` alone, through
`tasks/init_local_setting.yml`, and only at a terminal or for a value given; without either, it reports.
