# claude-on-proxmox

Ansible project that turns a Proxmox VE host into a ready-to-use
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) development VM:

1. **Template** – builds a cloud-init enabled Ubuntu 24.04 template on the Proxmox host (one time).
2. **Provision** – clones the template into a VM named by you, with your SSH key, and reports the address DHCP gave it.
3. **Configure** – installs a baseline of packages, developer tooling (Node.js, Docker, GitHub CLI, uv),
   Claude Code itself, and clones **every repository of your GitHub account** into `~/projects`.

Everything environment-specific (Proxmox IP, GitHub username, tokens) lives in git-ignored files, so the
repository can be shared as-is.

## Requirements

| Where | What |
|-------|------|
| Controller (your machine) | Python 3.12+ (required by the pinned ansible-core), `make`, SSH key at `~/.ssh/id_ed25519.pub` (or set `vm_ssh_public_keys`) |
| Proxmox VE 8.x or 9.x | Root SSH access (template build only) and an API token. A fresh install needs the setup below first |
| Testing (optional) | Docker Engine for Molecule; Vagrant + VirtualBox for a full VM test |

## Preparing a fresh Proxmox host

This project inherits your infrastructure. It creates VMs and reads back the addresses they were
given; it does not manage networking, storage, or address allocation, and it will not change
anything on the host outside the template it builds and the VMs it creates.

So a freshly installed host needs the ordinary post-install setup first. None of the following is
specific to this project — if your host is already running VMs, you have done all of it. Each step
links to the Proxmox documentation rather than repeating it.

### 1. Enable a package repository you can actually use

A fresh install is configured for **two enterprise** repositories, Proxmox VE's and Ceph's (the
Ceph one is there even if you never use Ceph). Both return `401 Unauthorized` without a
subscription, so `apt update` fails on the host. Either add a subscription, or in
*Node → Updates → Repositories*:

1. **Add** the **No-Subscription** repository.
2. **Disable** every entry whose URI is on `enterprise.proxmox.com` — the Proxmox VE one *and* the
   Ceph one.
3. If you do use Ceph, also **Add** the matching Ceph **No-Subscription** repository so its packages
   keep updating.

Use the UI rather than editing files: it works the same on 8.x and 9.x, while the files behind it
differ (one-line `.list` files on 8.x, deb822 `.sources` files on 9.x).

This matters here because `make template` installs `libguestfs-tools` on the host to bake the QEMU
guest agent into the image, and it refreshes the package lists first. That refresh fails if *any*
repository does, so a single enterprise entry left enabled is enough to stop the build.

See [Package Repositories](https://pve.proxmox.com/wiki/Package_Repositories). Then update the host
and reboot if the kernel changed.

### 2. Confirm hardware virtualization is on

Proxmox installs happily without it and then cannot start a VM. Check the firmware setting
(Intel VT-x / AMD-V) and confirm on the host:

```bash
grep -Eoc '(vmx|svm)' /proc/cpuinfo   # 0 means it is off in firmware
```

### 3. Networking: a bridge, and a DHCP server with room in it

The installer creates `vmbr0`, bridged to your physical NIC. VMs attached to it sit on your LAN and
get their addresses from whatever already serves it — normally your router. That is the arrangement
this project expects, and it is the Proxmox default.

Two things are worth checking before you create a fleet, and both live in your router, not in
Proxmox:

- **The DHCP pool has enough free addresses** for the VMs you intend to run. Five VMs need five
  leases.
- **The pool does not overlap the static addresses** you gave the Proxmox node(s) at install time.

On the controller side, Ansible works on up to `forks` VMs at a time - 20, set in `ansible.cfg` -
so a fleet larger than that is cloned, booted and configured in waves. Raise it for one run with
`make provision ANSIBLE_ARGS="-f 30"`; the hypervisor, not Ansible, is the real limit.

Expect a VM's address to change when you rebuild it. A DHCP reservation in your router pins an
address to a MAC, and that only lasts as long as the VM: Proxmox gives every clone a new random MAC
and this project cannot choose one, so a destroyed and re-provisioned VM is a new MAC and a new
lease. You never need to know the address in advance anyway — `make list` shows it.

`proxmox_vm_ipconfig` in `local.yml` skips DHCP and assigns an address directly, for example
`ip=192.0.2.50/24,gw=192.0.2.1`. It is one value applied to **every VM a run provisions**, so with
more than one VM they would all be given the same address. Use it only for a single VM, or provision
one VM per run and change the value in between.

> **A bridge needs a wired NIC.** Bridging over Wi-Fi does not work, so a laptop or mini-PC joined
> to the network wirelessly cannot give VMs a LAN address this way.

See [Network Configuration](https://pve.proxmox.com/wiki/Network_Configuration).

### 4. Know which storage you have

A default LVM install gives you two: `local` for ISOs and templates, and `local-lvm` for VM disks.
**An install on ZFS has no `local-lvm`** — it has `local-zfs` instead. Check *Datacenter → Storage*,
and if yours is not `local-lvm`, set `proxmox_storage` in `local.yml`.

See [Storage](https://pve.proxmox.com/wiki/Storage).

### 5. Root SSH for the template build, and an API token for everything else

`make template` connects to the host over SSH as root, because importing a disk image needs `qm`.
Everything after that uses the API token instead. Install your key so the one SSH step is not
password-prompted:

```bash
ssh-copy-id root@<proxmox-host>
```

Then create the token — see [Creating the Proxmox API token](#creating-the-proxmox-api-token).

### If you have more than one node

Put **one** node's address in `inventory/hosts.yml`; the API proxies requests to whichever node
owns a VM. `proxmox_node` (in `local.yml`, default `pve`) decides where VMs are created, and it
**must be the node that holds the template**. The clone request goes to `proxmox_node`, and Proxmox
reads the template's configuration on the node it is sent to, so a template on any other node fails
to clone. Shared storage does not change that: it only matters to Proxmox's *target node* clone
option, which this project does not use.

`make template` builds the template on whichever node `inventory/hosts.yml` points at, over SSH, so
point it at the node you create VMs on and set `proxmox_node` to that same node — `make deploy`
does both in one run. To create VMs on several nodes, build a
template on each with its own `proxmox_template_vmid` and `proxmox_template_name` (VMIDs are unique
across the cluster, and cloning by name fails when two templates share one), then set
`proxmox_node` and `proxmox_template_name` together for each run. Only the clone cares about
`proxmox_node`: a VM you later migrate to another node is found by name wherever it is, and every
re-run — `make configure`, a repair, `proxmox_vm_force_update` — addresses the node it is on.

See [Cluster Manager](https://pve.proxmox.com/wiki/Cluster_Manager).

### What this project assumes when you are done

| Assumption | Where it comes from |
|---|---|
| `apt` works on the host | step 1 |
| The host can run VMs | step 2 |
| A bridge exists, and something on it hands out DHCP leases | step 3, your router |
| A storage for VM disks, named in `proxmox_storage` | step 4 |
| Root SSH once, then an API token that can read the guest agent (`VM.Monitor` on 8.x, `VM.GuestAgent.Audit` on 9.x) | step 5 |
| VMs may be created, tagged, started and deleted | the token's privileges |

## Quick start

```bash
git clone <this repo> && cd claude-on-proxmox
make init            # venv + collections + copies the *.example files + pre-commit hook
```

Edit the three git-ignored files `make init` created:

| File | Contents |
|------|----------|
| `inventory/hosts.yml` | The address of your Proxmox host. VMs are **not** listed here |
| `inventory/group_vars/all/local.yml` | Proxmox node/storage names, package choices, which repos to skip |
| `inventory/group_vars/all/vault.yml` | Proxmox API token, GitHub username/token, optional Anthropic API key |

Encrypt the secrets, then run the three stages. The `make` targets refuse to run while `vault.yml`
is still plaintext.

```bash
(umask 077; openssl rand -base64 32 > .vault_pass)
make vault-encrypt

make deploy VM_NAME=alpha          # template (if missing) + provision + configure
```

`make deploy` is the whole thing in one command, and every stage is idempotent, so it is also what
you re-run after changing a variable. (VM hardware — cores, memory, disk — is applied only when the
VM is created; changing it for a VM that already exists needs `proxmox_vm_force_update`, see
[Re-running](#re-running).) It works on the VMs named in `VM_NAME` and nothing else: it
configures exactly what it has just provisioned, never the rest of your VMs. The three stages are
available separately when you want them:

```bash
make template                      # once per Proxmox host   (over SSH, as root)
make provision VM_NAME=alpha       # create + start a VM     (via the API)
make configure VM_NAME=alpha       # set it up               (over SSH, as the VM user)
```

A second `make deploy` rebuilds nothing. It checks through the API whether the template exists and
skips that play when it does — and because a skipped import takes its fact-gathering step with it, a
run that does not need to build the template opens **no SSH connection to the Proxmox host at all**.
Provisioning finds the VMs by name instead of creating them, and configuration re-applies only what
has drifted.

`make provision` prints the address each VM was given:

```
alpha is up at 192.0.2.51
```

A VM that already exists but has been shut down is left that way: provisioning does not start it or
wait for its address, `make deploy` does not configure it, and the run says so:

```
gamma is stopped and was left that way, so it is not configured. Start it, or re-run with proxmox_vm_start_existing=true.
```

That run exits 0: the job of `make provision` and `make deploy` is that the VMs exist, and a stopped
VM does, so leaving it alone is a success, reported. `make configure VM_NAME=gamma` for the same VM
fails instead — its job is the software on the VM you named, and it cannot do that job on a VM that is
off, so it stops rather than configure the rest and exit 0 with gamma silently skipped.

`make list` answers the same question later, for everything this project created. It is read-only,
and unlike the discovery that `make configure` runs, it shows a VM whether or not its guest agent
is answering:

```
NAME                        VMID  STATUS    ADDRESS
alpha                       9001  running   192.0.2.51
beta                        9002  running   192.0.2.52
gamma                       9003  stopped   -

3 VMs tagged "claude-on-proxmox".
No address for gamma: the guest agent has not reported one.
Those VMs may be stopped, still booting, or built without the agent.
```

Then `ssh dev@192.0.2.51` and run `claude`. If you did not set `vault_anthropic_api_key`, log in
interactively the first time.

### Naming and multiple VMs

`VM_NAME` is the name the VM gets in Proxmox, and it is what you see in the Proxmox UI. Pass several,
comma-separated, to build a fleet in one run:

```bash
make deploy VM_NAME=alpha,beta,gamma
make configure VM_NAME=alpha       # later, just one of them
make destroy VM_NAME=beta
```

Given, `VM_NAME` narrows every target to those names. Left out, what it means depends on the target:

| Target | Without `VM_NAME` |
|--------|-------------------|
| `make deploy`, `make provision`, `make destroy` | `claude-on-proxmox-default` |
| `make configure`, `make check`, `make list`, `make claude-login` | every VM this project created (tagged `claude-on-proxmox`) |

So a bare `make deploy` creates and configures `claude-on-proxmox-default` and leaves any other VMs
alone, while a bare `make configure` re-applies the configuration to all of them.

You never assign a VMID or an IP address. Proxmox picks the next free VMID; the DHCP server that
already serves your network assigns the address, exactly as it would for a laptop; and the QEMU
guest agent - baked into the template by `make template` - reports that address back so Ansible can
reach the VM and print it for you. The only address you ever type is your Proxmox host's.

### Upgrading an existing install

Provisioning now needs `qemu-guest-agent` **inside** the image: it is what
reports the VM's DHCP address back. `make template` bakes it in, but a template
built before this change does not have it, and re-running `make template` will
**not** fix that - the role skips the whole build when the VMID already exists.
A VM cloned from such a template never reports an address, so `make provision`
polls its guest agent 60 times, five seconds apart (`proxmox_vm_agent_retries`,
`proxmox_vm_agent_delay`), and then fails. That is five minutes of waiting plus
the time each poll takes, and on a running VM with no agent Proxmox spends a few
seconds pinging the agent before it answers each one, so expect closer to nine
minutes before the failure.

Rebuild the template once, on the Proxmox host:

```bash
qm destroy 9000        # the template's VMID (proxmox_template_vmid)
```

```bash
make template          # rebuilds it, this time with the guest agent
```

The build keeps the pristine download under `/var/lib/vz/claude-on-proxmox` on the node
(`proxmox_template_image_dir`) and removes the customised copy it imports once the template is
finished. Earlier versions kept both under `local`'s `template/iso`, where the UI lists them as
ISO images; they are safe to delete from there once the template is built.

Existing VMs keep working; they are configured over SSH and are not re-cloned.
Your API token also needs the guest-agent privilege added - see
[Creating the Proxmox API token](#creating-the-proxmox-api-token).

#### Adopting VMs created before tagging

This project now recognises its VMs by one Proxmox tag, `claude-on-proxmox`, and
nothing else. VMs created by an earlier version carry `claude;ansible` instead,
so `make list`, `make configure` and `make destroy` do not see them, and
`make provision` refuses to touch a VM of that name rather than take it over.

To adopt one, add the tag - in the Proxmox UI (the pencil beside the tags in the
VM's title bar), or on the Proxmox host:

```bash
qm config 105 | grep ^tags          # the VM's current tags, if any
qm set 105 --tags "claude;ansible;claude-on-proxmox"
```

`--tags` **replaces** the whole list rather than adding to it, so include every
tag the VM should keep. Once tagged it is this project's like any other.

If you only want to delete such a VM, you can skip the tagging and say so
instead, after checking in the Proxmox UI that the name is the VM you mean:

```bash
make destroy VM_NAME=old-dev ANSIBLE_ARGS="-e proxmox_vm_allow_untagged_delete=true"
```

### Creating the Proxmox API token

Use a dedicated user with only the rights the `proxmox_vm` role needs, rather than a root token.
On the PVE shell:

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

The guest-agent privilege is what lets the token read the address DHCP gave the VM; without it
provisioning creates the VM but cannot report where it is, and the API answers every address query
with 403. On 9.x do not substitute `VM.GuestAgent.Unrestricted`: it also works, but lets the token
run arbitrary commands inside the VM.

If you created the role earlier without it, or have since upgraded the host from 8.x to 9.x (the
upgrade drops `VM.Monitor` from existing roles), add the privilege to the existing role instead:

```bash
pveum role modify AnsibleVM -append 1 -privs VM.GuestAgent.Audit   # VM.Monitor on 8.x
```

One datacenter setting can also block provisioning: if **Datacenter → Options → User Tag Access**
(a separate option from Tag Style) is set to `list` or `existing` rather than the default `free`, a
non-root token cannot apply the `claude-on-proxmox` tag. Since that tag is how this project recognises its own
VMs, provisioning fails or the VMs are created untagged and `make configure` then finds nothing.
Either leave the setting at `free`, or add both `claude-on-proxmox` and
`claude-on-proxmox-unfinished` to the allowed list. The second marks a VM whose first run has not
finished yet, and is removed once it has started.

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
| `claude_code` | Claude Code via the official installer (or npm), `~/.claude/settings.json` merged with your settings and API key, optional global `CLAUDE.md`, and optionally a Remote Control server so the session is reachable from claude.ai and the mobile app | `claude_code_version`, `claude_code_install_method`, `claude_code_settings`, `claude_code_global_instructions`, `claude_code_remote_control` |
| `github_projects` | Lists your repositories with a custom `github_repos` module (pagination, fork/archive/empty-repo filters) and clones them | `github_projects` (names; empty = all), `github_projects_include_forks`, `github_projects_exclude`, `github_projects_clone_protocol` |
| `proxmox_vm` | Looks the VM up **by name**, clones the template when it does not exist, applies cloud-init (user, keys, DHCP by default, DNS), resizes the disk, starts it, then waits for the guest agent to report an address. `proxmox_vm_state: absent` deletes it. Either way it refuses an existing VM of that name that is a template or is not tagged `claude-on-proxmox`, rather than provision over it or delete it | `vm_cores`, `vm_memory_mb`, `vm_disk_size`, `vm_nameservers` (fleet-wide, in `local.yml`); `proxmox_vm_ipconfig` for a fixed address, also fleet-wide, so single-VM runs only |
| `proxmox_template` | Downloads the Ubuntu cloud image (SHA256 verified), creates the VM with `qm`, imports the disk, adds the cloud-init drive, converts to template | `proxmox_template_vmid`, `proxmox_template_image_url`, `proxmox_template_storage` |

Every role documents its full interface in `roles/<name>/meta/argument_specs.yml`, and Ansible validates
role arguments against it at runtime.

### Variable layering

```
roles/*/defaults/main.yml                 role defaults, role-prefixed names
inventory/group_vars/all/defaults.yml     project defaults + wiring of vm_user, github_username, … into role vars   (committed)
inventory/group_vars/all/local.yml        your overrides                                                            (git-ignored)
inventory/group_vars/all/vault.yml        secrets as vault_* variables                                              (git-ignored, encrypted)
inventory/hosts.yml                       the Proxmox host's address, nothing else                                  (git-ignored)
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
created; set `proxmox_vm_force_update: true` to re-apply them. That re-applies the settings to every VM the
run covers, replaces each one's tag list with the project's (a tag you added in the Proxmox UI is dropped),
and starts each one, shut down or not — so narrow it with `VM_NAME`. Otherwise a VM you have shut down stays
shut down when you re-run `make deploy` or `make provision`: it is reported as stopped and skipped, and the
other VMs carry on. Start it in the Proxmox UI first, or pass `ANSIBLE_ARGS="-e proxmox_vm_start_existing=true"`
to have the run start it. Two things are deliberately not reconciled on every run: `apt upgrade` (new distro packages
show up as changes) and the Claude Code binary, which updates itself on the `stable`/`latest` channels.

#### Re-running one part of it

`TAGS` narrows a run to one role, which is much faster than the whole thing when you have changed a single
variable. The tags are `common`, `dev_tools`, `claude_code` and `github_projects`; discovery always runs, so
the VMs are still found. `TAGS` works with `make configure` and `make check`; `make deploy` refuses it,
because building the template and creating VMs carry no tags and a tagged deploy would do nothing.

```bash
make configure VM_NAME=alpha TAGS=claude_code
```

**Adding an API key to a VM that was built without one** is the common case. Put it in the vault, then push
just that role — no SSH:

```bash
make vault-edit                                   # set vault_anthropic_api_key
make configure VM_NAME=alpha TAGS=claude_code     # writes ~/.claude/settings.json on the VM
```

That makes `claude` on the VM authenticate against the Anthropic API. It does not start anything: Claude
Code is a terminal program, so you still `ssh dev@<address>` and run `claude`.

Note this also **turns Remote Control off** for that VM, and the run says so. The two credentials are
mutually exclusive: an API key outranks the claude.ai login, and only that login can establish a
session you can reach from claude.ai or the mobile app. If a previous run had installed the Remote
Control server, this run stops and disables it and removes its unit. If Remote Control is what you
want, leave the key empty and see [Reaching a VM from your phone](#reaching-a-vm-from-your-phone)
instead.

**Going back** works the same way: clear `vault_anthropic_api_key` and re-run. The role removes the
`ANTHROPIC_API_KEY` it wrote from `~/.claude/settings.json` and leaves the rest of the file alone. A
key set through `claude_code_settings.env` is yours, and stays.

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

### Reaching a VM from your phone

[Remote Control](https://code.claude.com/docs/en/remote-control) runs a server on the VM so its
Claude Code session appears at claude.ai/code and in the Claude mobile app, while the session itself
keeps running on the VM with the VM's filesystem and tools.

It is **on by default**, but there is no switch that makes it work — Anthropic supports exactly one
credential for it, an interactive claude.ai login on a Pro, Max, Team or Enterprise plan. API keys
are **not** supported, and neither is the long-lived token from `claude setup-token`, which can make
model requests but explicitly cannot establish a Remote Control session. On Team and Enterprise, an
organization Owner has to enable Remote Control for the organization before any member's login can
use it; on Pro and Max the login is enough.

So `make configure` derives what to do from the credentials it finds on the VM:

| On the VM | What happens |
|---|---|
| `vault_anthropic_api_key` is set | Off, and it says so. A key outranks the login, so the server would refuse to start. A server an earlier run installed is stopped and removed |
| No key, no login yet | Everything installed, service left stopped, and you are told the one command to run |
| No key, signed in | The session starts and shows up in the app |
| No key configured, but Claude Code on the VM still uses one | Service left stopped, and you are told where that key comes from (`claude auth status` reports it as `apiKeySource`) |
| No key, signed in, but `~/.claude/settings.json` switches the feature off | Service left stopped, and the keys are named. A telemetry opt-out (`DISABLE_TELEMETRY`, `DO_NOT_TRACK`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_GROWTHBOOK`), an `apiKeyHelper`, a token or provider in `env`, or an `ANTHROPIC_BASE_URL` pointing anywhere but `api.anthropic.com` each disable Remote Control. Remove it, or keep it and set `claude_remote_control: false` |

Activating it is therefore: leave the API key empty, log in once, re-run configure.

```bash
make configure VM_NAME=alpha     # prepares everything, then says what is missing
make claude-login VM_NAME=alpha  # prints the one command to run
#   ssh -t dev@<address> "bash -lc 'claude auth login'"
#   approve in the browser, paste the code back
make configure VM_NAME=alpha     # the service starts; the session appears on your phone
```

It goes through `bash -lc` because only a login shell puts `~/.local/bin`, where the native installer
puts `claude`, on `PATH`; a command given to `ssh` runs without one, so a bare `claude auth login` is
`command not found`. `make claude-login` decides from the same `claude auth status` fields as
`make configure`, so a VM signed in another way — a Console account, a `claude setup-token` token, an
API key — is told by both what is in the way.

Set `claude_remote_control: false` in `local.yml` to turn it off. Like adding a key, that stops,
disables and removes a server an earlier run installed. It is also the right answer when a privacy
setting in `claude_code_settings.env` is there on purpose: none of the above fails the run, so the
other roles still run, but the service stays stopped until one or the other changes.

The first time `claude remote-control` runs, Claude Code explains what Remote Control does and asks
`Enable Remote Control? (y/n)`. A systemd service has no terminal to answer it, so `make configure`
records that answer in `~/.claude.json` for you, alongside first-run onboarding and trust for
`~/projects`: leaving Remote Control on is taken as the yes.

The server runs as the `claude-remote-control` systemd unit, as the VM user, from `~/projects`.
systemd restarts it whenever it exits, whatever the exit status, which includes server mode giving up
after about ten minutes without a network. The first restart comes after 10 seconds and the delay
backs off to five minutes. There is no start limit, so the server comes back on its own after an
outage, and an expired login costs a retry every five minutes rather than a spin. `systemctl stop`
still stops it.

On a signed-in VM, `make configure` then waits 20 seconds and fails, with the `journalctl` command to
read why, unless the server process it started is still the one running. That is there to catch a
server refused at startup, for its login, plan or organisation settings, during the run rather than
when you reach for your phone. A re-run that changes nothing leaves a running server alone and skips
the wait; a changed unit (a new permission mode, say) restarts it, once, and waits again.

#### How much of the VM a remote session can reach

A Remote Control session *is* the VM, not a sandbox with a copy of your repo — it runs as the login
user, with that user's filesystem and the tools this project installed. Since `common` grants
passwordless sudo, shell commands already reach the whole operating system.

The one thing that is scoped is Claude Code's **file** tools, which stay inside the session's working
directory. Widen them where you want to, through the settings this role already manages:

```yaml
# inventory/group_vars/all/local.yml
claude_code_remote_control_dir: /home/dev/projects        # where sessions open
claude_code_remote_control_permission_mode: acceptEdits   # fewer approvals from the phone
claude_code_settings:
  permissions:
    additionalDirectories: ["/etc", "/var/log", "/home/dev"]
```

`claude_code_remote_control_permission_mode` is the mode remote sessions start in. The service
passes it as `--permission-mode`, which [outranks](https://code.claude.com/docs/en/permission-modes)
`permissions.defaultMode`, so setting `defaultMode` in `claude_code_settings` changes nothing for a
session from the app: it only sets the mode for `claude` you start on the VM yourself, such as over
SSH. Once a session is open, the app can switch it between Manual, Accept edits and Plan; Auto and
Bypass permissions cannot be selected there. The value is passed to Claude Code unvalidated, so any
mode it accepts works; one it does not stops the server at startup, and on a signed-in VM
`make configure` then fails with the command that shows why.

### Tearing down

```bash
make destroy                      # claude-on-proxmox-default
make destroy VM_NAME=alpha,beta   # several at once; they are deleted in parallel
```

It prompts for confirmation, then stops each VM and deletes it with its disks. Without a terminal to
prompt on the answer is "no", so nothing is deleted. Every name must be a VM this project created:
a name that does not exist, or a VM without the `claude-on-proxmox` tag, is refused before anything
is deleted, and the error lists the VMs it did create. A template is never deleted. For a VM created
before this project tagged its VMs, see
[Adopting VMs created before tagging](#adopting-vms-created-before-tagging).

## Testing

```bash
make lint                 # yamllint + ansible-lint (production profile) + ruff
make syntax               # --syntax-check of every playbook against your inventory/
make unit                 # pytest: filters, github_repos module, the tracked-file guard, configure tags, Remote Control, Makefile guards (no network)
make molecule             # Molecule (Docker) test of every role, incl. idempotence
make molecule-integration # playbooks/configure.yml end to end in a container
make molecule-provision   # provision.yml + discover.yml against a fake Proxmox API
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
  a destroy pass via Molecule's side-effect stage. The fake refuses the first guest-agent polls so the
  wait loop is exercised, and reports a docker0 interface the role has to ignore.
- The `provision` scenario runs `provision.yml` exactly as `make provision VM_NAME=alpha,beta` does,
  then rediscovers both VMs from their tag in a separate stage - proving a later `make configure` can
  find them without anything stored locally.
- `proxmox_template` runs against a stateful fake `qm` (`tests/molecule/fake_qm.sh`), a fake
  `virt-customize`, a fake `/etc/pve/.vmlist` that places a VMID on a second node, and a locally
  served "cloud image" with a real SHA256SUMS file.
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
   do not cover (private-key detection, the whitespace and large-file checks). `pre-commit` runs
   the same linters locally from the virtualenv. gitleaks scans the working tree and, on pushes and pull
   requests, every commit they bring in, so a secret committed and then deleted still fails the run. By
   then it has been pushed and is public: revoke it rather than only rewriting history.
2. **Molecule** - every role scenario as one matrix job, and the `configure` and `provision` playbook
   scenarios as a second. A separate dependency-review job runs on pull requests.

The weekly run is a canary. Most of what this project installs lives outside the repository - the Ubuntu
cloud image, the Docker, NodeSource and GitHub CLI apt repositories, the Claude Code installer and the uv
release - and any of it can break without a commit here. (The Galaxy collections are pinned to exact
versions, so they cannot.) The Molecule scenarios never fetch the real cloud image, so the canary also checks
that the image URL in `roles/proxmox_template/defaults/main.yml` still answers and that Ubuntu's `SHA256SUMS`
still lists it.

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
ansible.cfg                 project-wide Ansible settings (accept-new host keys, forks, yaml output)
deploy.yml                  template (if missing) + provision + configure
playbooks/                  template.yml, provision.yml, discover.yml, configure.yml, list.yml, claude_login.yml, destroy.yml
playbooks/tasks/            cluster_vms.yml (the shared cluster listing) and vm_addresses.yml (each VM's config and
                            agent reply), imported by the fleet-wide playbooks
filter_plugins/             net_mac, guest_ipv4, guest_address and guest_addresses, for reading guest agent
                            output; proxmox_access_denied, for telling a refused token from a slow boot
inventory/                  controller.yml, hosts.yml.example, group_vars/all/{defaults.yml,local.yml.example,vault.yml.example}
roles/                      one role per concern, each with defaults, meta/argument_specs, molecule/
roles/github_projects/library/github_repos.py   custom module
tests/unit/                 pytest: filters, github_repos, tracked-file guard, configure tags, Remote Control, Makefile guards
tests/molecule/             shared fakes for Molecule scenarios
molecule/configure/         integration scenario for the full configure playbook
molecule/provision/         end-to-end scenario for provisioning + discovery
                            (in both, group_vars/all/defaults.yml is a symlink to the real one; keep it a git checkout)
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
