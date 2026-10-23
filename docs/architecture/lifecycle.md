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
    participant U as make deploy VM_NAME=alpha,beta TYPE=provision
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
   `make deploy TYPE=check` it would skip itself and discovery would find nothing.
2. `claude_vm_tagged` (a lazily-evaluated group var) filters to the tagged QEMU VMs. Proxmox omits
   `tags` for an untagged guest, hence its `selectattr('tags', 'defined')`.
3. `claude_vm_discovered` is `claude_vm_requested`: narrowed to `VM_NAME` when given, else the whole
   fleet. `list.yml` narrows through the same group var.
4. Assert no duplicate names — Proxmox permits them, and the second `add_host` would silently
   overwrite the first, so `make deploy TYPE=configure` would report success having touched one of two,
   unpredictably.
5. Per VM, `proxmox_vm_info vmid=...` with `config: current` + `network: true`, which asks only that
   VM's node. A VM that is stopped, whose agent is not up or whose node is down is skipped; a refused
   token is **fatal**, because it applies to every VM and swallowing it means configuring zero hosts
   and exiting 0, which looks like success. "Refused" is the `proxmox_access_denied` filter
   ([§7.1](custom-code.md#71-filter_pluginsproxmoxpy)), shared with `list.yml` and the role's agent wait.
6. `add_host` each VM for which `guest_address(proxmox_nic)`
   ([§7.1](custom-code.md#71-filter_pluginsproxmoxpy)) returns an address. Without the MAC of that NIC
   the VM is skipped rather than guessed at — an address picked without one could be a bridge the
   guest grew later (`docker0`).
7. If nothing was found, print a diagnostic that distinguishes "no tagged VMs exist", "tagged VMs
   exist but none match `VM_NAME`", and "tagged VMs exist but none reported an address yet" (stopped,
   still booting, or a token without guest-agent access).

Without `VM_NAME`, step 3 keeps every tagged VM, so `make deploy TYPE=configure`, `TYPE=check` and
`TYPE=ssh-config`, `make login` and `make list` are fleet-wide by default.

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
`make deploy TAGS=claude_code` after a lease moved still refreshes the alias. `claude_ssh_config: false`
keeps the whole project out of `~/.ssh`.

Tailscale runs last, from the play's `tasks:` - after `roles:`, before `post_tasks:` - in a block whose
rescue records a failure in `claude_vm_configure_failures`, with `make deploy VM_NAME=<vm>
TAGS=tailscale`, and says it. The VM stays in the play, so it still records its workspace and gets its
alias, and under `make deploy` its sign-ins and VS Code; a fourth play, tagged `always`, then fails each
VM with such a failure - skipped when `claude_vm_configure_in_deploy` is set, as `deploy.yml`'s configure
import sets it, since deploy's own last play raises the same list (§4.7). `import_role`, not
`include_role`: a static import takes the block's tag, so `make deploy TAGS=tailscale` runs the role's
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
3. With `destroy_release` (the default), start each stopped VM through the API (`status/start`), wait
   for its agent to report an IPv4 address on `proxmox_nic` - by the MAC its config gives, as
   `vm_addresses.yml` pairs them, so a bridge the guest grew (`docker0`, `tailscale0`) cannot end the wait
   before DHCP has answered - list the cluster again and read every requested VM's address
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
`ignore_unreachable` finds unreachable or failing (`failed_when: false`: a failed host leaves the run, and
a play in which every host fails ends it), is ended and deleted as it is. Then the `release` entry point of
`claude_code` (stop Remote Control's path unit and service, `claude auth logout`), `github_projects`
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
(`ansible_playbook_python`), since its hosts now carry SSH settings for the release. The role with `proxmox_vm_state: absent` across the
placeholders, fanning out over `forks`; it looks each VM up again and applies the ownership check per
VM. Its `known_hosts` task is `throttle: 1`, because parallel deletes would otherwise each rewrite
the same file and restore a key another had just removed.

**Play 3 — forget the alias (localhost).** `tasks/ssh_config.yml` with `state: absent` for the VMs
that are actually gone: play 2 ends with a `post_tasks` fact, `claude_vm_deleted`, which a host that
failed in the role never reaches, and play 3 removes only the aliases of hosts that have it. A VM
whose deletion failed - a stop that timed out, protection on, an API error - keeps its alias,
because it is still there to connect to, and the run names it. A run refused in play 1 never
reaches play 3; removing a block that is not there is a no-op. A localhost play rather than a task in the role, so the role stays free of the controller's
ssh config and the one writer per run needs no throttle. The same play says, for every VM it deleted,
what the release did - joining the three clauses - or, for a VM it could not reach or with
`destroy_release: false`, what is left of it to remove by hand and where: the Tailscale device (which keeps
its name, so a VM built again under it would join as `<name>-1`), a GitHub token its sign-in made, and a
Claude Code login, which Anthropic offers no way to revoke from outside.

## 4.6 `playbooks/list.yml`

Read-only. Lists the cluster and narrows it exactly as `discover.yml` does (§4.3, steps 1–3), then
asks each VM for its config and agent report, by VMID, and prints a table.

It differs from `discover.yml` in one deliberate way: it lists a VM **whether or not** its agent
answers. Discovery skips an unaddressable VM because it cannot configure it; a listing that hid one
would be answering a different question than the one asked. A VM with no address shows `-`, and a
footnote names it.

Rows are built once, in `claude_list_rows` — each VM's cluster-listing entry combined with the
address `guest_address(proxmox_nic)` finds for it — so the table and that footnote cannot disagree
about which VMs have an address.

## 4.7 `deploy.yml`

The whole thing: template, provision, configure, the three sign-ins, the editor - each stage the
standalone playbook, imported as is, so deploy is the sum of `make deploy TYPE=template`, `TYPE=provision`
and `TYPE=configure`, the three sign-ins `make login` runs and `make code`, by construction. A first play on localhost asks the API whether a
template named `proxmox_template_name` exists and records it; the template play is imported with a
`when:` on that fact. The lookup is `proxmox_vm_info name=...`, so only the node holding that name is
asked for its VM list — unfiltered, one node that is down failed the run before anything was built.

`configure.yml` is imported with `vars: {claude_vm_discovery: false}`, so it configures exactly the
hosts provisioning's play 3 published. Running discovery there would disagree with provisioning
about what an omitted `VM_NAME` means: `claude_vm_names` falls back to `claude_vm_default_name`, but
discovery tests `vm_name is defined` and falls back to the whole fleet, so a bare `make deploy`
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

The result is that only the first run needs root SSH to the hypervisor. Every later `make deploy`
is API and VM traffic only.

After configuration come the sign-ins and the window, for the hosts configuration reached: a VM left
stopped is not in `claude_vms`, and a host that failed a role has been dropped from the plays after
it. `claude_login.yml` and `github_login.yml` are imported with discovery off,
`claude_vm_login_in_deploy` (a failed sign-in is said, not raised, so deploy goes on) and
`when: deploy_sign_in | bool`. The Claude one is interactive (`claude_vm_login_interactive: true`); the
GitHub one decides VM by VM, from what the Claude step left on each VM
([§5.5](roles.md#55-claude_code--install-claude-code)): a VM whose Claude sign-in
nobody answered (`claude_code_login_skipped` - Enter at the prompt, or a browser that never came back) is
left. With `github_login_auto_start` (the default) every other VM starts at once; off, each is asked
first (`claude_vm_login_ask`, the role's `github_projects_login_ask`): Enter signs in one whose Claude
sign-in was answered, `y` one that showed none (signed in already, or an API key). `tailscale_login.yml`
follows the same way with `tailscale_login_auto_start`, and `when: deploy_sign_in | bool and
tailscale_enabled | default(true) | bool` (§4.10): a VM an auth key joined during configuration is only
reported. A run with no terminal gets `deploy_sign_in: false` from the Makefile's `NO_TTY_ARGS`: sign-ins
that start by themselves would otherwise each wait for a browser nobody watches.

Last, `code.yml` with `code_in_deploy`, when `deploy_code`. For several VMs its first task prints
each VM's `make code` line and ends the play. For one - the default VM of a bare deploy counts, so the
one-VM check does not need `vm_name`, and the VM is found by `claude_vm_names[0]` among the tagged
ones, not through `claude_vm_requested` - every task is in one `block` whose `rescue` says any failure
with the `make code` line and ends the play, so a deploy whose VM is done exits 0; outside deploy the
rescue fails again. A VM found stopped is said with `make deploy VM_NAME=<vm> ANSIBLE_ARGS="-e
proxmox_vm_start_existing=true"`, since the deploy configured nothing on it. Ansible rescues no
unreachable host, so the first two connections `ignore_unreachable` and assert.

What a sign-in leads to - Remote Control's start, the private repositories' clones - runs in the
login playbooks' follow-on plays, and a failure there is not a failed sign-in: deploy's last play
fails each VM whose `claude_vm_configure_failures` is set, after VS Code has opened, with the command
that retries it - and so does a Tailscale failure during configuration, which `configure.yml` recorded
in the same list and, with `claude_vm_configure_in_deploy`, left to this play (§4.4). The configure
import passes `claude_code_sign_in_follows`, `github_projects_sign_in_follows` and
`tailscale_sign_in_follows` (`deploy_sign_in`), so configuration says that the sign-ins come next in
this run rather than naming `make login`, and the first play's message says up front that the run
will stop for them.

`make deploy TYPE=...` runs one stage's playbook on its own - `template.yml`, `provision.yml`,
`configure.yml` (`--check --diff` for `TYPE=check`, `--tags` for `TAGS`) or `ssh_config.yml` - with the
arguments the old stage target passed, so each stays the playbook deploy imports and nothing else. The
Makefile chooses the playbook while it is read, from a `TYPE` it has checked.

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

`ssh_config.yml` (`make deploy TYPE=ssh-config`) is the fleet-wide resync: the cluster listing, the addresses
(§4.3 steps 1 and 4), `present` for every tagged VM with an address, keeping a workspace an earlier
configure recorded, then pruning through `ssh_config_prune`
([§7.1](custom-code.md#71-filter_pluginsproxmoxpy)). Pruning removes blocks, never VMs, and only a block whose name no VM in the cluster carries any more. A name an untagged VM
carries is left alone and reported, as `destroy.yml` refuses such a VM. While any tagged VM is listed
without a name, which is what a node that is down looks like (§4.3), nothing is pruned, because any
block could be that VM's. With `VM_NAME`, only those VMs are refreshed and nothing is pruned. Two
tagged VMs of one name are refused as discovery refuses them: an alias of that name would point at
one at random.

## 4.9 `playbooks/code.yml`

`make code VM_NAME=alpha [PROJECT=discord-music-bot]`: the alias step for one VM, then the one step
no other playbook does, launching the editor. In order:

1. Refuse anything but one name, and a `vm_project` (`PROJECT`) that is not one GitHub repository
   name - up to 100 ASCII letters, digits, `.`, `-`, `_`, and not `.` or `..` - so nothing outside
   the projects directory, or nested in it, can be opened. The Makefile's `project-name-check` refuses
   the same before a shell sees the value; this is for a direct `ansible-playbook` run.
2. Find the VM by tag, naming an untagged VM of that name as not this project's; ask its agent for
   the address. Settle where it is reached exactly as the alias's `HostName` does - the address, or
   `<name>.<claude_ssh_host_domain>` - so this run and VS Code connect to the same target, and refuse
   a VM that is not running, whatever its name resolves to.
3. Add it to `claude_vms` with `add_host`, the way configure reaches it: Ansible's own connection and
   `accept-new`, not the user's ssh config, and the group's connection variables from the inventory
   (a key, a port) apply as they do to `make deploy TYPE=configure`.
4. Read the VM user's home on the VM with `getent`, delegated over SSH, unless `vm_projects_dir`
   says where the code is. Nothing is guessed on the controller, and nothing is read back out of the
   alias: the workspace is `vm_projects_dir` or `<home>/projects`.
5. Refuse a `vm_projects_dir` that is not absolute (VS Code is handed it as written, from this
   machine, so `~` would not expand) and, with the terminal on, a `vscode_workspaces_dir` that is
   not absolute, is the projects directory (its files would sit among the repositories), or is the
   folder being opened (`PROJECT=.claude-on-proxmox`). Each names the variable it is about.
6. Refresh the alias, then look for the folder to open - the workspace, or `<workspace>/<project>` -
   with `stat` on the VM. Read-only. Unreachable, missing and not-a-folder are each their own
   message: a missing project lists what the workspace holds and, for a name that differs only in
   case, suggests the right one, since the VM's paths are case-sensitive; a missing workspace means a
   VM never configured, and says to run `make deploy TYPE=configure`. These are there so a typo is said in the
   terminal, not in a VS Code window opened on a folder that does not exist.
7. Check `code` is on `PATH`, and install `ms-vscode-remote.remote-ssh` if `code --list-extensions`
   lacks it.
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

In `make deploy` steps 1-9 are one `block` whose `rescue` says what failed with the line that opens
the window later (§4.7); outside it the failure is raised again, a command's with its stderr. Every
name `code.yml` sets starts with `code_`, a prefix no role uses, since a role's fact or register
outranks a play var, and no task var shares a fact's name.

**The workspace file** is built as data and written with `to_nice_json`, so no path breaks its JSON;
`copy` leaves an unchanged file alone. It names the folder by absolute path, hides its own directory
from the explorer of the window that shows it, and holds a `Shell` task - `/bin/bash -l` in the folder,
`folderOpen` too, in the same presentation group as Claude's so the two share a terminal as split panes,
the shell on the left because it is listed, and so started, first - and the `Claude` task: a `process` task,
`/bin/bash -lc <script> make-code` and eight arguments (the line without `claude`, the two lines a
sign-in ends with, the path unit's file, GitHub's line, the git protocol, the scopes, the VM's name),
`cwd` the folder, `runOptions.runOn:
folderOpen`, presented in a dedicated, focused terminal. A login shell, because VS Code starts task
shells non-login and `claude` is on the login `PATH` only (`make login TYPE=claude` uses `bash -lc` for
the same reason). The script checks `claude` is installed, runs `claude auth login` when
`claude auth status` fails (a failed one says how to retry), signs gh in by the device flow when
`gh auth status --hostname github.com --active` fails (Ctrl-C skips it: `trap ':' INT`), and then
`claude --continue` - the folder's latest conversation; where there is none, Claude Code 2.1 exits 1 at once ("No conversation found to continue"), and a `--continue` that fails within five seconds starts a fresh `claude` instead, the script ending `exit 0` either way - because a task terminal
does not survive a window reload, after which the task runs again; its messages arrive as arguments,
never as script text, and
`tests/unit/test_code_session.py` runs it against a stand-in `claude`. `${workspaceFolder}`
is not used: in a task that belongs to a workspace file rather than a folder it is ambiguous.

Sign-in is not scripted, because it cannot be: it is a browser flow. VS Code Server puts its
`browser.sh` helper in `BROWSER` for every terminal it starts, which opens the URL on the client,
and `claude` shows the URL as well and accepts a pasted code. A login made there starts Remote
Control through the path unit of [§5.5](roles.md#55-claude_code--install-claude-code) where one
waits, which the script finds by its file on the VM; elsewhere the terminal names `make deploy VM_NAME=<name> TAGS=claude_code`, which starts it through
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
manual run of `make code` is the check when they move again.

The workspace file and the session notes (below) are all that is written on the VM, last, as the VM
user, so every refusal above leaves the VM as it was; a workspace directory that already exists keeps its mode, since
`vscode_workspaces_dir` may name one of the user's own. `tests/unit/test_code_session.py` holds
every task in `code.yml` to an allowlist - read-only here, or delegated to the VM - and the
`provision` scenario checks the settings file it planted is byte-for-byte untouched after every
run. `PROJECT=.claude-on-proxmox`, which names the files' own directory, is refused. Cloud-init placed the key, so a VM that was provisioned but never configured still
opens once it has a workspace, and its terminal says to run `make deploy TYPE=configure`.

**The session notes.** With `vscode_session_notes` (the default, with the terminal), a page about the
VM opens beside the Claude terminal every time the window opens - from `make code`, *Open Recent*, a
restart that restores the window, or a reload - as a rendered preview, with its sign-in status read from
the VM at that moment. VS Code has no workspace setting that opens a file, and a file named in the
`code --remote` call opens only from `make` and stops the window restoring its editors, so the trigger
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

`make code` writes the rest from `playbooks/templates/vm_session.md.j2` into
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
directory, trusted in its own window - and `make code`'s message says so.

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

`make login` without a `TYPE`: discovery once, then `claude_login.yml`, `github_login.yml` and
`tailscale_login.yml` imported as they are, with discovery off - so it is their sum and cannot drift from
them. As under deploy, a VM goes on to its next sign-in after one that failed, or after the work it leads
to failing (Remote Control's start): each playbook's last play leaves what failed
(`claude_vm_login_raised_later`), and `login.yml`'s own last play fails each such VM once all three have
had their turn, with every message. Between them it passes what deploy passes (§4.7), but only where names were given
(`claude_vm_login_named`, the play var both later sign-in plays keep): GitHub and Tailscale start by
themselves for a VM whose Claude sign-in was answered, are asked about first with
`github_login_auto_start` or `tailscale_login_auto_start` off, and are left for one whose Claude sign-in
nobody answered. Without names every step reports, and nothing starts. `claude_vm_login_in_deploy` is
not set, so GitHub's follow-on play runs the whole `github_projects` role for each VM named, as
`make login TYPE=github` does, since no configuration ran just before it. `TYPE=claude`, `github` or
`tailscale` runs that playbook alone.
