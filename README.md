# claude-on-proxmox

Ansible project that turns a Proxmox VE host into a ready-to-use
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) development VM:

1. **Template** – builds a cloud-init enabled Ubuntu 24.04 template on the Proxmox host (one time).
2. **Provision** – clones the template into a VM named by you, with your SSH key, and reports the address DHCP gave it.
3. **Configure** – installs a baseline of packages, developer tooling (Node.js, Docker, GitHub CLI, uv),
   Claude Code itself and Tailscale, and clones **every repository of your GitHub account** into
   `~/projects`: the private ones too once `gh` is signed in, by a token in the vault or the sign-in below.
4. **Sign in** – signs each VM in to Claude, which starts Remote Control, `gh` in to GitHub, ready to
   push from, and the VM in to your Tailscale network, so you can reach it from anywhere: each by a page in
   your browser, the one step each vendor leaves to you. A token or an auth key in the vault does GitHub's
   and Tailscale's without one.
5. **Open** – opens the VM in VS Code, where a terminal starts a Claude session.

`make deploy` does all five in one run.

Everything environment-specific (Proxmox IP, GitHub username, tokens) lives in git-ignored files, so the
repository can be shared as-is.

## Requirements

| Where | What |
|-------|------|
| Controller (your machine) | Python 3.12+ (required by the pinned ansible-core), `make`, SSH key at `~/.ssh/id_ed25519.pub` (or set `vm_ssh_public_keys`); VS Code with the `code` command on `PATH` for `make code` (optional) |
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

The same setting is how a storage of this project's own is handed to it: an admin who provisions a
dedicated pool for these VMs (on a NAS, say) gives you its id, you put it in `proxmox_storage` and
grant it to the token with the `pveum aclmod /storage/<id>` line in step 5. The id is the whole
interface. This project only ever names a storage and puts the template and every clone on it; it
never creates or changes one, never asks for more than `Datastore.AllocateSpace` and
`Datastore.Audit` on that one path, and needs the `images` content type and nothing else.

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
| `inventory/group_vars/all/vault.yml` | Proxmox API token, GitHub username/token, optional Anthropic API key and Tailscale auth key |

Encrypt the secrets, then deploy. The `make` targets refuse to run while `vault.yml` is still
plaintext.

```bash
(umask 077; openssl rand -base64 32 > .vault_pass)
make vault-encrypt

make deploy VM_NAME=alpha
```

`make deploy` is the whole thing in one command. It builds the template if it is missing, creates
and configures the VM, signs it in to Claude, GitHub and Tailscale, and opens it in VS Code, where a
terminal of the window starts a Claude session. The sign-ins are yours, because no vendor lets anything
else take them: each opens a page in your browser, and the run waits for you there.

1. **Claude.** Authorise Claude Code on the page. Your browser then comes back to the VM by itself, with
   nothing to paste: the run forwards Claude's sign-in port from this machine to the VM over SSH for the
   length of the sign-in, and waits up to five minutes (`claude_login_approval_timeout`). Where that port
   is taken here, no browser can open on this machine, or you run `make` over SSH from another one, the
   page shows a code instead, which you paste at the run's `Code:` prompt. Remote Control starts in the same run, so the session is on your phone
   too.
2. **GitHub**, when there is no `vault_github_token` in the vault. It starts by itself: the page opens,
   the code is on your clipboard, you paste it on the page and authorise, and the private repositories
   are cloned in the same run. The run waits until the code is approved or expires, after fifteen
   minutes, and Ctrl-C there ends the whole deploy, its VS Code window included.
3. **Tailscale**, when there is no `vault_tailscale_auth_key` (with one, configuration joined the VM
   already). It starts by itself too: the page opens, you sign in and connect the VM, and the run waits up
   to five minutes (`tailscale_login_timeout`). A VM you approve later joins by itself. See
   [Reaching a VM over Tailscale](#reaching-a-vm-over-tailscale).

With `github_login_auto_start: false` or `tailscale_login_auto_start: false` the run asks first, VM by VM,
so a VM can be left for later: Enter signs in a VM whose Claude sign-in you just answered, `y` one that
needed none (signed in already, or on an API key), and `n` leaves it. A VM whose Claude sign-in nobody
answered - Enter at its `Code:` prompt, or a browser that never came back - is left out of the other two,
and a run with no terminal skips all three; either way the deploy finishes, `make claude-login
VM_NAME=alpha`, `make github-login VM_NAME=alpha` and `make tailscale-login VM_NAME=alpha` do them later,
and `deploy_sign_in: false` leaves them out on purpose. A sign-in that fails is said, with the command
that retries it, and the rest of the deploy goes on.

Every stage is idempotent, so `make deploy` is also what you re-run after changing a variable: a VM
already signed in is only reported, and VS Code brings its window to the front. (VM hardware — cores,
memory, disk — is applied only when the VM is created; changing it for a VM that already exists needs
`proxmox_vm_force_update`, see [Re-running](#re-running).) It works on the VMs named in `VM_NAME` and
nothing else: it configures exactly what it has just provisioned, never the rest of your VMs, and
signs them in one after another. For more than one VM it opens no window, and ends with each VM's
`make code` line. The stages are available separately when you want them:

```bash
make template                      # once per Proxmox host   (over SSH, as root)
make provision VM_NAME=alpha       # create + start a VM     (via the API)
make configure VM_NAME=alpha       # set it up               (over SSH, as the VM user)
make claude-login VM_NAME=alpha    # sign Claude in, start Remote Control
make github-login VM_NAME=alpha    # sign gh in, clone the private repositories
make tailscale-login VM_NAME=alpha # join your Tailscale network by its web login
make code VM_NAME=alpha            # open it in VS Code
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
VS Code: make code VM_NAME=<name> [PROJECT=<repo>] (make ssh-config refreshes the aliases).
No address for gamma: the guest agent has not reported one.
Those VMs may be stopped, still booting, built without the agent, or on a node that is down.
```

Later, `make code VM_NAME=alpha` opens the VM in VS Code again, and the alias the run wrote, named
after the VM, makes `ssh alpha` work too (see [Using VS Code on the VM](#using-vs-code-on-the-vm)).

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
| `make configure`, `make check`, `make list`, `make claude-login`, `make github-login`, `make ssh-config` | every VM this project created (tagged `claude-on-proxmox`) |
| `make code` | refused: it opens one VM, so it needs one name; `PROJECT` optionally narrows it to one project |

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

#### A vault GitHub token from an earlier version

Earlier versions asked for a token with `repo` alone, used only to list your repositories. This
version also signs `gh` in with it, and gh refuses a classic token without `read:org`. Such a token
keeps listing as before: the run says gh stayed signed out and which scopes to add (`read:org`, and
`workflow` for pushes that change `.github/workflows/`), and the next `make configure` signs gh in once
they are there. `github_auth_method: web` signs gh in by the device flow instead, the token listing only.

#### A gh login or git identity you set up by hand

Earlier versions asked you to sign `gh` in on the VM yourself for pushes, beside a vault token used only
to list. This version signs gh in with `vault_github_token` only where gh is signed out or signed in by
this project, so such a login stays, and each run says the token only lists there; set
`github_auth_method: token` to have the token take over. A `user.name` or `user.email` you set on the VM
stays too: only a missing one is derived from your GitHub account, while `git_user_name` and
`git_user_email`, when set, are always written.

#### Remote Control on a VM configured by an earlier version

A VM waiting for its first Claude login now gets a path unit that starts Remote Control the moment a
login lands, so a sign-in made in the Claude terminal of `make code` starts it by itself. A VM last
configured by an earlier version has no such unit: run `make configure VM_NAME=alpha TAGS=claude_code`
once (or `make deploy VM_NAME=alpha`), or sign it in with `make claude-login VM_NAME=alpha`, which
starts Remote Control itself. The Claude terminal of `make code` tells the two apart, and on such a VM
names that `make configure` line.

#### Tailscale on VMs configured by an earlier version

Tailscale is on by default, so the next `make configure` installs it on every VM and reports each one
not signed in - or joins it, with `vault_tailscale_auth_key` set. Set `tailscale_enabled: false` in
`local.yml` first to keep your VMs as they are, and `make check` beforehand says what would be installed.
A VM where you installed Tailscale yourself is left exactly as it is either way.

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
| `vscode_server` | What VS Code's Remote - SSH extension needs on the host: an sshd drop-in that keeps the port forward the VS Code Server is reached through enabled, plus keepalives, checked against what sshd actually runs with; and a higher inotify limit for the file watcher. Off removes both files | `vscode_remote`, `vscode_server_client_alive_interval`, `vscode_server_inotify_watches` |
| `dev_tools` | Node.js (NodeSource), Docker Engine, GitHub CLI, uv/uvx (release pinned by version and SHA256), pipx, build tools. Apt signing keys are vendored in `roles/dev_tools/files` | `dev_tools_install_*`, `dev_tools_node_major`, `dev_tools_uv_version`, `dev_tools_npm_global_packages` |
| `claude_code` | Claude Code via the official installer (or npm), `~/.claude/settings.json` merged with your settings and API key, optional global `CLAUDE.md`, and optionally a Remote Control server so the session is reachable from claude.ai and the mobile app | `claude_code_version`, `claude_code_install_method`, `claude_code_settings`, `claude_code_global_instructions`, `claude_code_remote_control` |
| `github_projects` | Signs `gh` in to GitHub - from `github_token`, or by a device-flow login you make once - and makes it git's credential helper; lists your repositories with a custom `github_repos` module (pagination, fork/archive/empty-repo filters, private ones with a login) and clones them; writes a git identity where none is set | `github_projects` (names; empty = all), `github_projects_include_forks`, `github_projects_exclude`, `github_auth`, `git_user_name`, `git_user_email`, `github_projects_clone_protocol` |
| `tailscale` | Tailscale from its apt repository (signing key vendored in `roles/tailscale/files`); joins the tailnet with `vault_tailscale_auth_key` on a VM's first join, or by the web login `make tailscale-login` drives; keeps the hostname, Tailscale SSH, routes and DNS settings of its own login as configured with `tailscale set`; reports the VM's tailnet, name and address. Off logs out and removes what it installed; a Tailscale installed by hand is only reported | `tailscale_enabled`, `vault_tailscale_auth_key`, `tailscale_ssh`, `tailscale_advertise_tags`, `tailscale_accept_dns`, `tailscale_upload_logs` |
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

### Private repositories, and pushing from the VM

A private repository takes two credentials: a token to *list* it and one for `git` to *clone* it, and a
push or a `gh pr create` later needs the second again. `github_projects` covers all of them through `gh`
on the VM, one of two ways:

- **With `vault_github_token` set**, the token lists your repositories, private ones included, and signs
  `gh` in on the VM where gh is signed out or signed in by this project, so one `make deploy` ends with
  everything cloned and a VM that pushes. A login you made on the VM yourself stays, and the token only
  lists there; each run says so, and `github_auth_method: token` puts the token over it. As a classic
  token it needs `repo`, `workflow` and `read:org` (with `repo` alone it only lists, and the run says which
  scopes to add); as a fine-grained one, Metadata (read), Contents,
  Pull requests and Workflows (read and write) on all repositories - a token limited to selected
  repositories lists and clones only those. The same token serves every VM.
- **Without one**, `make deploy` signs the VM in itself, with no question first (`github_login_auto_start`;
  `make github-login VM_NAME=alpha` does the same for a VM that exists). It runs GitHub's device flow for
  you, as far as GitHub allows: gh
  starts on the VM in the background, the page github.com/login/device opens in your browser, the
  one-time code is on your clipboard and printed, and the run waits up to fifteen minutes while you paste
  the code and authorise GitHub CLI; then it makes gh git's credential helper, lists with the token gh just
  stored and clones the private repositories, all in that one run. Nothing is ever in the vault. The
  paste and the click are the floor: GitHub's device flow never pre-fills the code, and gh has no other
  browser flow for github.com.

```bash
make deploy VM_NAME=alpha        # ... the GitHub page opens, the code is on your clipboard: paste,
                                 # authorise, and the private repositories are cloned in the same run
make github-login VM_NAME=alpha  # the same, later, for a VM make configure left signed out
```

  Without a name, `make github-login` only reports each VM's state; given several, it signs them in one
  after another, since the browser takes one sign-in at a time. A code left unapproved expires after
  fifteen minutes and that VM's sign-in fails saying so, after the others have had their turn; run it
  again for a fresh code. `make deploy` starts it for every VM but one whose Claude sign-in nobody
  answered, and a run with no terminal skips it, as nobody is there to approve it. Once the code is shown
  the run waits until it is approved or expires, and Ctrl-C there ends the whole run; with
  `github_login_auto_start: false` it asks first, and `n` leaves a VM for later. `make configure` never
  runs it. On a controller with no browser or clipboard the run prints the page and the
  code and waits all the same, and `login_desktop: false` makes it never try. The terminal that `make
  code` opens runs the same flow after the Claude sign-in, with the page opening by itself and the code
  printed to copy (a Remote-SSH terminal cannot fill your clipboard); Ctrl-C there skips it and starts
  Claude. The private repositories then take one `make configure`, or `make github-login VM_NAME=alpha`,
  which on a VM already signed in runs the clone straight away.

`github_auth_method` picks between the two: `auto` (the default) is the token when there is one and the
device flow otherwise, `web` is the device flow always, with a token used for the listing only, and
`token` is the token only, where an empty vault fails by name instead of falling back. `github_login_scopes`
is what the device flow asks for on top of gh's own, `workflow` by default.

Either way `gh` becomes git's credential helper for github.com (`gh auth setup-git`), so the checkouts
keep their HTTPS remotes and `git push` and `gh pr create` work, in a terminal, in the `make code` window
and in a Remote Control session alike. The token lives in `~/.config/gh/hosts.yml` on the VM, readable by
the VM user and so by Claude Code (see [What the VM trusts](#what-the-vm-trusts)). The role marks the login
it made - a digest of the token, in `~/.config/claude-on-proxmox/` on the VM - and removes only that one
when you clear `vault_github_token`; a login you made yourself is never replaced by the vault token nor
removed, a token of your own pasted into `gh` included. `gh auth status --json`, which the role reads, needs gh 2.81 or newer: GitHub's apt repository,
which `dev_tools` installs from, provides it, and an older gh is a named failure rather than a VM reported
as signed out on every run. `github_auth: false` in `local.yml` leaves `gh` and `~/.gitconfig` alone: a
token then only lists. A private repository is cloned over HTTPS only where git can ask gh for it - gh
signed in and made git's credential helper, by this role or by you (`gh auth setup-git`) - and is
otherwise skipped and named rather than failed; over SSH your key clones it either way.

Commits need an identity. `git_user_name` and `git_user_email` (in `local.yml`) are written to
`~/.gitconfig` on the VM when set; left empty, each is derived only where `~/.gitconfig` has none, so an
identity you set on the VM stays: the display name (or login) and GitHub's noreply address,
`<id>+<login>@users.noreply.github.com`, of the account the token belongs to, or of `github_username`
without one, so no private email lands in commits by default. An organisation gives none, which the run
says: set the two, or sign gh in as yourself.

`github_projects_clone_protocol: ssh` clones over SSH instead, with a key of your own on the VM; the role
pre-seeds GitHub's published host keys, so there is no trust-on-first-use prompt. That covers `git` only:
`gh pr create` still needs the login above.

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
variable. The tags are `common`, `vscode_server`, `dev_tools`, `claude_code`, `github_projects` and `tailscale`; discovery
always runs, so the VMs are still found, and so does the step at the end that refreshes each VM's SSH alias. `TAGS` works with `make configure` and `make check`; `make deploy` refuses it,
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
Code is a terminal program, so open it with `make code VM_NAME=alpha`, whose terminal starts `claude`, or
`ssh alpha` and run `claude` there.

Note this also **turns Remote Control off** for that VM, and the run says so. The two credentials are
mutually exclusive: an API key outranks the claude.ai login, and only that login can establish a
session you can reach from claude.ai or the mobile app. If a previous run had installed the Remote
Control server, this run stops and disables it and removes its unit. If Remote Control is what you
want, leave the key empty and see [Reaching a VM from your phone](#reaching-a-vm-from-your-phone)
instead.

**Going back** works the same way: clear `vault_anthropic_api_key` and re-run. The role removes the
`ANTHROPIC_API_KEY` it wrote from `~/.claude/settings.json` and leaves the rest of the file alone. A
key set through `claude_code_settings.env` is yours, and stays. The VM then needs its claude.ai login:
`make claude-login VM_NAME=alpha` signs it in and starts Remote Control (`make deploy` does both too).

### What the VM trusts

This is a development box, and the defaults reflect that:

- The `dev` user has passwordless sudo and is in the `docker` group, so anything running as that user,
  including Claude Code, is effectively root on the VM. The API key written to `~/.claude/settings.json`
  is therefore readable by any code the agent runs, and so is the GitHub token `gh` keeps in
  `~/.config/gh/hosts.yml` - the vault's, or the one a device-flow login issued to that VM. Set
  `common_user_passwordless_sudo: false` and `dev_tools_install_docker: false` for a tighter box.
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
| No key, no login yet | Everything installed, service left stopped with a path unit waiting to start it when you sign in, and you are told how |
| No key, signed in | The session starts and shows up in the app |
| No key configured, but Claude Code on the VM still uses one | Service left stopped, and you are told where that key comes from (`claude auth status` reports it as `apiKeySource`) |
| No key, signed in, but `~/.claude/settings.json` switches the feature off | Service left stopped, and the keys are named. A telemetry opt-out (`DISABLE_TELEMETRY`, `DO_NOT_TRACK`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_GROWTHBOOK`), an `apiKeyHelper`, a token or provider in `env`, or an `ANTHROPIC_BASE_URL` pointing anywhere but `api.anthropic.com` each disable Remote Control. Remove it, or keep it and set `claude_remote_control: false` |

Activating it is therefore: leave the API key empty and sign in once. `make deploy` asks for that
itself, after configuring the VM; for a VM that exists, `make claude-login` does the same:

```bash
make claude-login VM_NAME=alpha  # the sign-in page opens in your browser: authorise,
                                 # and the browser comes back to the VM by itself
                                 # ... and Remote Control starts; the session appears on your phone
```

The run starts `claude auth login` on the VM with no terminal. On a machine with a browser, Claude signs
in by sending the browser back to a port it listens on, on its own `127.0.0.1`; the VM has no browser,
so the run hands claude a stand-in that records that page, forwards that one port from this machine to
the VM over SSH (`ssh -L`, bound to this machine's loopback, closed when the sign-in ends), and opens the
page here. You authorise, the browser comes back to claude through the forward, and the run carries on
by itself, waiting up to `claude_login_approval_timeout` (300 seconds). The page opens only when it is
Claude's own sign-in page.

Where the browser could not come back - the port is already taken on this machine, `login_desktop:
false`, nothing here to open a browser with, or `make` run over SSH, where the browser is on another machine
and the page would send it to that machine's own localhost - claude's other page opens instead, which shows a code once you authorise:
paste it at the `Code:` prompt and the run hands it to claude through a pipe. The code is read with the
run log's no_log, so it never lands in `.logs/`. Enter alone at the prompt skips a VM, and a code claude
refuses fails that VM by name once the others have had their turn; a code copied only up to its `#` (a
double-click stops there) is asked for again, and Ctrl-C then `A` at the prompt ends the run. A sign-in
page lasts `claude_login_timeout` plus half an hour: a code pasted later is said to be too late at once,
with the command for a fresh page.

Once every VM named has had its turn, the run starts the service on each one signed in, in the same run.
Given several names it signs them in one after another; given none it only reports each VM's state.

Three variables tune it: `claude_login_args` passes extra arguments to `claude auth login` (`[--sso]`
for a single sign-on login, say), `claude_login_approval_timeout` (300) is how long the run waits for
your browser to come back, and `claude_login_timeout` (120) is how many seconds claude may take to answer
a code you paste. A VM whose Remote Control is off on purpose - `vault_anthropic_api_key`
set, or `claude_remote_control: false` - is reported as such, with nothing to fix.

A sign-in made any other way starts the service by itself too. While a VM waits for its first login,
`make configure` leaves a systemd path unit, `claude-remote-control.path`, watching for the login file;
the moment one lands - from `make claude-login`, the Claude terminal of `make code`, or by hand with
`ssh -t dev@<address> "bash -lc 'claude auth login'"` - it starts the server. The next `make configure`
enables the server for boot, runs the stay-up check below, and removes the path unit, so a server you
stop later stays stopped. By hand, it goes through `bash -lc` because only a login shell puts
`~/.local/bin`, where the native installer puts `claude`, on `PATH`; a command given to `ssh` runs
without one, so a bare `claude auth login` is `command not found`.

`make claude-login` decides from the same `claude auth status` fields as `make configure`, so a VM
signed in another way — a Console account, a `claude setup-token` token, an API key — is told by both
what is in the way, and never signed in over it.

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
the wait; a changed unit (a new permission mode, say) restarts it, once, and waits again. After a
sign-in, `make claude-login` and `make deploy` run the same check; a server that does not stay up
fails the run at its end - after VS Code has opened, in a deploy - with
`make configure VM_NAME=alpha TAGS=claude_code` to retry it, while the login itself is kept.

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

### Reaching a VM over Tailscale

[Tailscale](https://tailscale.com) puts each VM on your tailnet - the private network your other devices
are on - so `ssh`, VS Code's Remote - SSH and a dev server on any port reach the VM from anywhere, not only
from the LAN Proxmox sits on. It is on by default; `tailscale_enabled: false` in `local.yml` turns it off.

There are two ways in. `tailscale_auth_method` picks: `auto` (the default) is the key when the vault holds
one and the web login otherwise, `key` is the key only (an empty one fails by name, and no VM is ever sent
to a browser), and `web` is the web login only, with a key in the vault left unused.

- **An auth key** (`vault_tailscale_auth_key`). `make configure` - and so `make deploy` - joins each new VM
  by itself, with nothing to click. Make the key in Tailscale's admin console (Settings, Keys):
  **reusable** for a fleet, or one-off for one VM; **pre-approved** if your tailnet has device approval
  on; and **tagged** (`tag:claude-vm`, below). Never ephemeral: Tailscale removes an ephemeral node within
  the hour of it going offline, and a VM left stopped would be. The key is only used for a VM's first
  join - a VM that has joined keeps working after the key expires - and it reaches the VM on standard
  input, never on a command line or in a file. An OAuth client secret (`tskey-client-...`) works too, with
  `tailscale_advertise_tags` set and `?ephemeral=false` on the secret.
- **The web login**, with no secret at all. `make tailscale-login VM_NAME=alpha` - and `make deploy`'s
  step, which starts by itself (`tailscale_login_auto_start`; `false` asks first) - opens Tailscale's
  sign-in page here and waits up to five minutes (`tailscale_login_timeout`) for you to sign in and
  approve the VM. A VM you approve later joins by itself: the page stays valid for 7 days.

Each run says each VM's name and address on the tailnet, and the tailnet itself - so a VM that someone
else's approval put in their tailnet shows:

```
alpha: on Tailscale in the tailnet you@example.com as alpha.tail1234.ts.net (100.101.102.103).
```

**Connecting.** From any device on the tailnet: `ssh dev@alpha.tail1234.ts.net` (or its `100.x` address),
VS Code's Remote - SSH to the same host, `http://alpha.tail1234.ts.net:3000` for a dev server. It is the
same sshd and the same keys as on the LAN (`vm_ssh_public_keys`). With `claude_ssh_host_domain:
tail1234.ts.net`, the aliases this project writes and `make code` use `<VM name>.tail1234.ts.net` in place
of the LAN address; the machine running Ansible must be on the tailnet then, and Ansible itself keeps
using the LAN. That is the VM's tailnet name only when the VM joined under its own name: a VM name that is
not one DNS label joins under a cleaned-up one (`alpha.lab` as `alpha-lab`), and a name already taken gets
a suffix (`alpha-1`). The report shows the name each VM has.

**Keeping an agent VM off your other devices.** A new tailnet's policy lets every device reach every other
- its one rule has `"src": ["*"]`, and `*` matches tagged devices too - and these VMs run an AI agent with
passwordless sudo. With a tagged key, one edit to the policy (Access controls in the admin console) keeps
your devices reaching the VMs while a VM can open no connection at all. **Merge** it into your policy;
never paste it over the whole file, and never as `"acls": []`, which cuts off every other device you have:

```jsonc
{
  // The VMs' tag. An admin owns it, and so can also apply it at a web login.
  "tagOwners": { "tag:claude-vm": ["autogroup:admin"] },
  // The default rule, its source narrowed from "*" to your own devices: they still reach
  // everything, the VMs and every port on them included; a tagged VM is no source, so it reaches nothing.
  "acls": [ { "action": "accept", "src": ["autogroup:member"], "dst": ["*:*"] } ],
  // Run on every save: an edit that lets a VM reach your devices, or another VM, is refused.
  "tests": [ { "src": "tag:claude-vm", "deny": ["you@example.com:22", "tag:claude-vm:22"] } ]
}
```

Put your own address in the test. Any other rule whose source is `*`, or names the tag, undoes the edit,
and the test makes the console refuse such a change when you save it. A policy that already narrowed the
default rule needs only the tag owner and the test. A VM joined by the web login is your own device and
reaches what your other devices reach; to isolate one, set `tailscale_advertise_tags: [tag:claude-vm]`
before signing it in - as an admin, you can apply the tag at the login. On the VM itself, sudo can always
log it out or read its node keys: only the tag and the policy bound what it can reach.

**Tailscale SSH** (`tailscale_ssh: true`) answers port 22 on the tailnet address instead of sshd,
authorised by your policy, which then needs an `ssh` rule:
`{"action": "accept", "src": ["autogroup:member"], "dst": ["tag:claude-vm"], "users": ["dev"]}`. Every
configure holds each VM to the value you set, both ways.

**DNS.** The VM keeps the resolver DHCP gave it (`tailscale_accept_dns: false`), and your devices still
reach it by its tailnet name. With `true` it takes your tailnet's DNS settings - and a tailnet that
overrides local DNS then answers every lookup on the VM, from nameservers that, if they are on the tailnet,
a VM the policy above isolates cannot reach.

**What the role touches.** Only the Tailscale it installed. A Tailscale you installed on a VM yourself -
the package, the snap or Tailscale's static binaries - is reported and left exactly as it is,
`tailscale_enabled: false` included. To hand one to this project, run `sudo tailscale logout` on the VM
and remove it the way you installed it; for the package that is `sudo apt purge tailscale` - purge, so the
next install does not start under the old login. A login you made by hand on a Tailscale this project
installed keeps its own settings, and when its key expires `make tailscale-login` and `make deploy` leave it
for you to sign in again the way you made it, rather than put this project's settings over it. A sign-in
this project started is its own, however late it ends - a page you approve after the run, or a join with
the key that ran out of time - and so is a login made by hand while that page still waits, which cannot be
told from it; a join with the key that Tailscale refused is over, and leaves nothing waiting. Log upload to Tailscale is on, as Tailscale ships it; `tailscale_upload_logs: false` turns
it off from the daemon's first start, which also ends Tailscale's technical support for the VMs. If
installing Tailscale fails - its package site out of reach, say - the VM is configured otherwise and keeps
its alias, `make deploy` still signs it in and opens it, and the run fails at its end with `make configure
VM_NAME=alpha TAGS=tailscale`; Tailscale's repository is taken back out, so `apt` on the VM works as
before.

**Turning it off.** `tailscale_enabled: false`, then `make configure`: each VM is logged out - a
`tailscaled` you stopped is started for that - and `tailscaled`, the package and its repository are
removed. The device stays in your tailnet, expired, until you delete it in the admin console (Machines),
unless the project can delete it for you (below). A logout that fails - Tailscale's servers out of the VM's
reach - is said and the rest removed anyway; its login then stays valid until you delete the device.

**Removing devices for you.** A logged-out device stays in the admin console until it is deleted, and
holds its name: a VM built again as `alpha` joins as `alpha-1`, for good. With an API credential the
project deletes the device itself, when `make destroy` deletes the VM and when `tailscale_enabled: false`
takes Tailscale off it. `tailscale_api_auth_method` picks the credential, and every call is made from this
machine, so it never reaches a VM:

| `tailscale_api_auth_method` | In the vault | Made in the admin console |
|---|---|---|
| `none` (the default) | nothing: the device is only logged out, and the run names it to delete by hand | |
| `token` | `vault_tailscale_api_token` | Settings, Keys, *Generate access token*: a `tskey-api-` token, which lasts at most 90 days and can delete any device you can |
| `oauth` | `vault_tailscale_oauth_client_id`, `vault_tailscale_oauth_client_secret` | Settings, OAuth clients, with the `devices:core` scope: it never expires, but Tailscale scopes it to the tags you give it, which may keep it from deleting a device without them, such as one joined by the web login |

A deletion Tailscale refuses is said, with the device to delete by hand, and the rest of the run goes on.

**Rebuilding a VM.** Delete the old device first - `make destroy` does with a credential above, and says
so otherwise - or the new VM joins as `alpha-1`; a configure that finds the name taken says so.

| Variable | Default | Meaning |
|---|---|---|
| `tailscale_enabled` | `true` | the feature |
| `vault_tailscale_auth_key` | `""` | join without a browser; empty is the web login |
| `tailscale_ssh` | `false` | Tailscale SSH on the tailnet address |
| `tailscale_accept_routes` | `false` | use the subnet routes other devices advertise |
| `tailscale_accept_dns` | `false` | take the tailnet's DNS settings |
| `tailscale_advertise_tags` | `[]` | tags asked for at a join |
| `tailscale_login_server` | `""` | another control server, such as Headscale |
| `tailscale_upload_logs` | `true` | Tailscale's log upload |
| `tailscale_auth_method` | `auto` | how a VM joins: `auto`, `key` or `web` |
| `tailscale_login_auto_start` | `true` | `make deploy` starts the web login without asking first |
| `tailscale_api_auth_method` | `none` | how a device is deleted from the tailnet: `none`, `token` or `oauth` |
| `tailscale_login_timeout` | `300` | seconds the web login waits for your approval |
| `tailscale_hostname` | the VM name | the machine name asked for, made one DNS label |

### Using VS Code on the VM

VS Code's [Remote - SSH](https://code.visualstudio.com/docs/remote/ssh) extension runs the editor's
back end on the VM, over the same key-only SSH everything else uses: the window is on your machine,
the files, the terminal, the extensions and `claude` are on the VM. Nothing new is opened, relayed or
logged into. The one thing it needs that `ssh dev@<address>` does not have is a name that follows the
address DHCP gave the VM, and that is what this project keeps for you.

```bash
make deploy VM_NAME=alpha          # ends by opening alpha in VS Code; configuration printed
#   alpha: ssh alpha   |   make code VM_NAME=alpha   (opens /home/dev/projects)
make code VM_NAME=alpha            # later: refresh the alias from Proxmox, then open the VM in VS Code
make code VM_NAME=alpha PROJECT=discord-music-bot   # just that project, in a window of its own
```

`make deploy` runs `make code` itself as its last step, for a run for one VM - the one `VM_NAME`
names, or the default VM of a bare `make deploy`, whatever other VMs exist (`deploy_code: false`
leaves it out). There, whatever keeps the window from opening - no alias, no `code` command, an SSH
connection refused, a Remote - SSH extension that would not install - is said with the `make code`
line instead of failing a deploy that is otherwise done; a VM the deploy found stopped, and so did not
configure or sign in, is said with
`make deploy VM_NAME=alpha ANSIBLE_ARGS="-e proxmox_vm_start_existing=true"`. A deploy for several VMs
opens no window and ends with each VM's `make code` line.

`make code` looks the VM up on Proxmox, rewrites its alias in case the lease moved, checks over SSH
that the folder it is about to open is there, installs the Remote - SSH extension if VS Code does
not have it, and opens the folder with `code --remote ssh-remote+alpha`, by way of a workspace file
that also starts `claude` in a terminal of the window ([A Claude session in the
window](#a-claude-session-in-the-window)).

`PROJECT` opens one folder of `~/projects` instead: the repository's name, as `github_projects`
cloned it. A window rooted at the project is what most editor features expect - the source control
view, search, a debugger's launch configuration and the Claude Code extension all start from the
repository, where with `~/projects` open they see every repository at once. Names are checked
before anything runs: a GitHub repository name is letters, digits, `.`, `-` and `_`, so a `/`, a
`..` or a quote is refused. A name that is not there is reported with what is, and a name that
differs only in case gets a suggestion, because folder names on the VM are case-sensitive:

```
There is no project named parkbnb in /home/dev/projects on alpha. Did you mean ParkBnb? Folder names are case-sensitive.
```

The same check catches a VM that was never configured, which has no `~/projects` yet; the message
says to run `make configure` first. The first connection installs VS Code Server on the VM and takes about a minute;
the VM downloads it from Microsoft over HTTPS, so it needs that route out. You can also pick `alpha`
in the extension's host picker (*Remote-SSH: Connect to Host…*) and open any folder.

**Where the alias lives.** In `~/.ssh/claude-on-proxmox.conf`, one block per VM, included from the
top of `~/.ssh/config` with a single `Include` line. That file and that line are the only things this
project writes in `~/.ssh` besides `known_hosts`; the body of `~/.ssh/config` is never touched, its
permissions are left as they are, and the `Include` stays once added (an `Include` of a missing file
is harmless). An `Include` of the same file that you already have, as `~/.ssh/claude-on-proxmox.conf`,
`${HOME}/...` or the bare name, counts if your ssh reads it - the question is put to `ssh -G` rather
than guessed from the spelling, since `${HOME}/...` is expanded only from OpenSSH 9.9 on (Ubuntu
24.04 ships 9.6) and an `Include` below a `Host` block is scoped to that block - and your line is
left exactly as you wrote it either way. The alias is the VM's
name. If your own `~/.ssh/config` already has a `Host` entry of that name, the run refuses to write
the alias and names the line: rename the VM, or the entry, since the managed file is included first
and would otherwise override yours silently. Set `claude_ssh_config: false` to keep the project out
of `~/.ssh` altogether, or `claude_ssh_config_include: false` to write the file but add the `Include`
yourself.

```
# ~/.ssh/claude-on-proxmox.conf
# BEGIN claude-on-proxmox: alpha
Host alpha
    HostName 192.0.2.51
    User dev
    ServerAliveInterval 30
# END claude-on-proxmox: alpha
```

**Keeping it current.** `make deploy` and `make configure` write the alias of each VM they finish,
`make destroy` removes it with the VM (as it already forgets the host key; a VM whose deletion
fails keeps its alias, since it is still there), and `make ssh-config`
brings the whole fleet back in step at any time: a lease that moved, a VM deleted in the Proxmox UI,
a second machine. Fleet-wide it also prunes the aliases of VMs that no longer exist. Pruning only
ever removes blocks from the managed file, never VMs, and a block is only ever written for a VM
tagged `claude-on-proxmox`, so a VM you run outside this project cannot be affected. Two cases are
left alone and reported instead: a name that an *untagged* VM still carries (not this project's to
reason about, exactly as `make destroy` refuses it), and anything at all while a tagged VM is listed
without a name, which is what a node that is down looks like. `make ssh-config VM_NAME=alpha`
refreshes one VM and never prunes.

On a network whose DNS resolves VM names (a router that registers DHCP leases, or avahi with
`local`), set `claude_ssh_host_domain: lan` and every alias points at `alpha.lan` instead of the
address; a lease that moves then never matters.

**Agent forwarding is off**, and should stay off: the VM user has passwordless sudo and runs Claude
Code, and `ForwardAgent yes` would hand every key in your agent to both for the life of the
connection. Pushes go through `gh`, signed in on the VM itself - by `make configure` from a vault
token, or by `make deploy` and `make github-login` through GitHub's device flow (see
[Private repositories, and pushing from the VM](#private-repositories-and-pushing-from-the-vm)).
`claude_ssh_forward_agent: true` turns it on if you have decided otherwise.

**What the VM gets.** The `vscode_server` role writes `/etc/ssh/sshd_config.d/20-vscode-server.conf`,
which keeps `AllowTcpForwarding` and `AllowStreamLocalForwarding` on (the server is reached through
a port forward over the SSH connection) and adds a keepalive so an editor left open on a laptop that
went to sleep is reaped in minutes. Then it asks sshd what it will apply to your connection - as
the VM user, from your machine's address, so `Match` blocks count - and fails, naming the file, if
forwarding ends up off, limited to the remote direction, or removed by `DisableForwarding`: sshd
keeps the first value it reads, so a `05-*.conf` of yours wins over ours without a word, and a
`Match User dev` block applies whatever sorts first. It also raises
`fs.inotify.max_user_watches` to the 524288 VS Code recommends, or the editor warns that it cannot
watch a large workspace. `vscode_remote: false` removes both files. The Claude Code extension
(`anthropic.claude-code`) installs on the VM's side of the connection like any other extension and
uses the VM's `claude` and its login; `remote.SSH.defaultExtensions` in your VS Code settings
installs it on every VM you open.

**When things go wrong.** A connection that hangs or a server that will not start is reset with
*Remote-SSH: Kill VS Code Server on Host* (or `rm -rf ~/.vscode-server` on the VM); a `HOST KEY
VERIFICATION FAILED` after a rebuild means a lease was reused by a VM that `make destroy` did not
delete, and `ssh-keygen -R <address>` clears it. If Ansible runs somewhere other than the machine
VS Code is on, copy the managed file and the key there; a VS Code on Windows reads
`%USERPROFILE%\.ssh\config`, not WSL's, so point `remote.SSH.configFile` at the managed file
(`\\wsl.localhost\<distro>\home\<you>\.ssh\claude-on-proxmox.conf`) and keep the key readable from
Windows.

#### A Claude session in the window

`make code` does not stop at the window. VS Code is handed a small workspace file that names the
folder and holds one task, so once the window has connected a terminal named **Claude** opens in it
running `claude` in the folder: `make deploy` ends in a Claude session in the repository, and so does
`make code` later. `make deploy` has signed the VM in already; on a VM that has not signed in to Claude
yet, the terminal runs `claude auth login` first. The sign-in page opens in the browser on your
machine - VS Code Server sends every URL a terminal opens there - and if the page shows a code, paste
it into the terminal. Remote Control then starts by itself where the `claude_code` role left a unit
waiting for the login; on a VM configured before that unit existed, or whose settings block Remote
Control, the terminal names `make configure VM_NAME=alpha TAGS=claude_code` instead. A sign-in that
fails says how to try again: *Tasks: Run Task*, *Claude*, or a window reload. Then GitHub the same
way, on a VM where `gh` is not signed in yet: the terminal shows a one-time code and opens the page to
enter it on, and `gh` becomes git's credential helper, so a push from that terminal works at once;
Ctrl-C skips GitHub, and Claude starts. The
private repositories follow on the next `make configure` (see
[Private repositories, and pushing from the VM](#private-repositories-and-pushing-from-the-vm));
`github_auth: false`, or `github_auth_method: token`, leaves GitHub out of the terminal too.

The first time, VS Code waits for two answers that are yours to give:

1. **Trust.** VS Code opens a folder it has not seen before in Restricted Mode, which starts nothing
   on its own, terminals and tasks included. Choose *Manage* on the banner, then *Trust*, and the
   terminal starts. Trusting the whole projects directory - the window `make code VM_NAME=alpha`
   opens, without `PROJECT` - trusts every project on alpha and every workspace file `make code`
   writes there, at once. VS Code keeps trust by alias (`ssh-remote+alpha`), so a lease that moves
   does not undo it, but a VM you rename is trusted again.
2. **Automatic tasks.** VS Code then asks whether to allow automatic tasks in trusted workspaces,
   once per workspace, in a notification at the bottom right that goes quiet after a moment (the
   bell keeps it): choose *Allow*, which writes `"task.allowAutomaticTasks": "on"` to your VS Code
   user settings, so it is asked once. If you missed the question, *Tasks: Manage Automatic Tasks*
   in the Command Palette offers the same choice.
3. **Claude's own check.** In the terminal, `claude` asks once per repository whether to trust the
   folder it was started in, as it does anywhere; the whole projects directory is pre-trusted on a
   VM with Remote Control, one project's clone is not.

After that, `make code` opens straight into Claude. It reads your VS Code user settings, and never
writes them, to say which of the two VS Code answers to expect; with `task.allowAutomaticTasks` set
to `"off"` it says the terminal will not start, and *Tasks: Run Task*, *Claude* starts it by hand in
a trusted window. The settings are looked for where VS Code keeps them - `VSCODE_APPDATA` or
`XDG_CONFIG_HOME` if set, else stock VS Code's place on macOS and Linux, the snap's and the
flatpak's - and a file not found, not readable or not parsable is said as such rather than taken
for "not answered yet". Neither answer is given for you, on purpose: Restricted Mode and the
automatic-tasks question are VS Code's guard against code that runs because a folder was opened,
and this project does not turn trust off, allow terminals in untrusted windows, or write the setting.

**What it writes.** The workspace files live on the VM beside the repositories, never in one, so
`git status` stays clean: `~/projects/.claude-on-proxmox/<directory's name>.code-workspace` for the
whole directory (`projects.code-workspace` with the default `vm_projects_dir`) and
`~/projects/.claude-on-proxmox/project/<repo>.code-workspace` for one project, hidden from the
explorer. They are the one thing `make code` writes on the VM, and only when they would change. A
repository named `.claude-on-proxmox` would land on the same path; `make code` refuses to open that
name, and `github_projects` would clone it there, so leave it out of `github_projects`.

**After the first sign-in.** Remote Control ([Reaching a VM from your phone](#reaching-a-vm-from-your-phone))
waits for exactly that login. The terminal says it starts by itself, and `make configure VM_NAME=alpha
TAGS=claude_code` keeps it on across reboots; where nothing waits for the login, it says that same
command starts it. A VM configured with an API key is already signed in, and goes straight to `claude`.

**Every window picks the folder's conversation up.** The terminal runs `claude --continue`: the
folder's most recent conversation, or a new one when there is none. A window reload (*Developer:
Reload Window*, or the *Reload Required* an extension install can ask for) ends the task's process
and runs the task again, so nothing is lost but the reply in flight; `/clear` starts a fresh
conversation. Opening a window that is already open focuses it rather than starting a second
session; two windows on one project would share the conversation. Quitting `claude` ends the task;
*Tasks: Run Task*, *Claude* starts another.

**Switches.** `vscode_claude_terminal: false` opens the bare folder, as before, and writes nothing
on the VM; for one run, `make code VM_NAME=alpha ANSIBLE_ARGS="-e vscode_claude_terminal=false"`.
`vscode_workspaces_dir` moves the workspace files to an absolute path of its own on the VM (outside
the projects directory, each window is trusted on its own; a directory that exists keeps its
mode). Switching off, or moving them, leaves the files written before where they were: harmless,
and `rm -r ~/projects/.claude-on-proxmox` on the VM removes them. `vscode_user_settings_file`
points the check at another settings file: VS Code Insiders'
`~/Library/Application Support/Code - Insiders/User/settings.json`, VSCodium's
`~/.config/VSCodium/User/settings.json`, or, for a VS Code on Windows driven from WSL,
`/mnt/c/Users/<you>/AppData/Roaming/Code/User/settings.json`.

**The Claude Code extension.** The first `claude` in the terminal installs `anthropic.claude-code`
on the VM's side of the connection by itself, as it does in any VS Code terminal, and the install
may ask for a reload (which the task survives, as above). It uses the VM's `claude` and the same
login; its panel is there if you would rather have it than the terminal. To keep it out, set
`autoInstallIdeExtension` to `false` in `claude_code_settings`. Written against VS Code 1.140,
unchanged in 1.141: both gates changed in 2026 (automatic tasks off by default since 1.109,
Restricted Mode with only a banner since 1.126), so a VS Code that changes them again may ask
differently.

### What a run asks of you

A run stops for you in three ways, and each is set apart from the task output around it by a block of
`#`:

```
################################################################################
##  USER INPUT REQUIRED  -  alpha  -  Claude Code sign-in
################################################################################
Paste the code the sign-in page shows, then press Enter. Enter alone skips this VM.
Code:
```

- **`USER INPUT REQUIRED`**: a prompt to type at - the code Claude's sign-in page shows, or, with
  `github_login_auto_start` or `tailscale_login_auto_start` off, whether to sign a VM in now.
- **`ACTION REQUIRED IN YOUR BROWSER`**: a page to approve - Claude's, GitHub's device code, Tailscale's.
  While the run waits for it, it says so once, with the time left, rather than a retry line every few
  seconds.
- **`CONFIRM DELETION`**: `make destroy` names the VMs it is about to delete.

At the end, after the recap, a `NEXT STEPS` block lists what is still left to do on each VM - a sign-in
you skipped, a VM left stopped, a Tailscale device to delete by hand - each once, and only what the run
did not settle: a "not signed in" that a later step of the same `make deploy` signed in is not listed.
Nothing is printed when nothing is left. The task-timing summary comes after it.

The blocks come from `callback_plugins/claude_on_proxmox.py`, which `ansible.cfg` names; put
`ANSIBLE_STDOUT_CALLBACK=default` in front of a `make` command for Ansible's own output (the prompts keep
their blocks). The run log below holds the same blocks without colour, but never `make destroy`'s
question, which Ansible does not log.

### Run logs

Every `make` target that runs Ansible keeps a copy of the whole run in `.logs/`, one file per run,
named after when it started and the target:

```bash
make deploy VM_NAME=beta
#   Logging this run to /home/you/claude-on-proxmox/.logs/20261007-203015-deploy.log
make logs                         # the newest 20, newest first
make logs-clean                   # delete them all
```

The terminal shows exactly what it did before; Ansible copies its output to the file with a
timestamp on every line, so a run can be read back after the terminal has scrolled away or the
window is gone. Tasks marked `no_log` are hidden in the file as on screen, so the API token and
the GitHub token never reach it. The files do name the Proxmox host and every VM by address, so
`.logs/` is git-ignored, refused by the tracked-file guard even with `git add -f`, and readable only
by you. Logs older than 14 days are deleted whenever a logged run starts (`LOG_RETENTION_DAYS`).
`LOG_DIR=` turns logging off for one run, `LOG_DIR=/some/where` moves it, and an
`ANSIBLE_LOG_PATH` of your own is used as it is. Molecule runs (`make molecule`, `make test`) are
logged the same way. A command you run with `.venv/bin/ansible-playbook` directly is not; put
`ANSIBLE_LOG_PATH=.logs/<name>.log` in front of it for the same result.

### Tearing down

```bash
make destroy                      # claude-on-proxmox-default
make destroy VM_NAME=alpha,beta   # several at once; they are deleted in parallel
```

It prompts for confirmation, then releases each VM from what it signed in to, then stops it and deletes it
with its disks, a disk that still carries its VMID but that its configuration no longer lists included.
Without a terminal to prompt on the answer is "no", so nothing is deleted. Every name must be a VM this
project created: a name that does not exist, or a VM without the `claude-on-proxmox` tag, is refused
before anything is deleted, and the error lists the VMs it did create. So is a name two VMs share, which
Proxmox allows: rename or remove one first. A template is never deleted. For a VM created before this
project tagged its VMs, see
[Adopting VMs created before tagging](#adopting-vms-created-before-tagging).

**Releasing a VM** happens on the VM, over SSH, since what it signed in with is on its disk; a stopped VM is
started for it (and stopped again should the run then fail to delete it), and the VMs are released at once,
as they are deleted:

| Service | What `make destroy` does first | What is left |
|---|---|---|
| Claude | stops Remote Control and the unit that starts it, then `claude auth logout` | the session stays in the Claude app's list, offline: Anthropic has no way to revoke a Claude Code login from outside |
| GitHub | `gh auth logout` | the token, valid until you revoke it. Revoking the one this project's sign-in made means revoking GitHub CLI under [Settings → Applications](https://github.com/settings/applications), which signs out every `gh` you have, this machine's included, so `make destroy` does not do it. `vault_github_token`, which every VM shares, is never revoked |
| Tailscale | logs the node out, then deletes its device with `tailscale_api_auth_method` (see [Removing devices for you](#reaching-a-vm-over-tailscale)) | nothing with a credential; without one, the device, logged out, until you delete it |

Each step is best effort: one that fails is said, and the VM is deleted anyway, as you asked. The run ends
with one line per VM saying what it was released from and what is left of it - and for a VM that could not
be reached (stopped and not starting, or not answering over SSH), what to remove by hand and where.
`destroy_release: false` deletes the VMs as they are, and says the same.

## Testing

```bash
make lint                 # yamllint + ansible-lint (production profile) + ruff
make syntax               # --syntax-check of every playbook against your inventory/
make unit                 # pytest: filters, github_repos module, the tracked-file guard, configure tags, Remote Control, the three sign-ins and deploy's sign-in steps, Tailscale, the managed SSH config, the Claude terminal, Makefile guards (no network)
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
  find them without anything stored locally. It ends with `make ssh-config` and `make code` against
  the fake fleet, with a stand-in `code` that records what it was asked, and checks the workspace
  file `make code` writes, what it says for each `task.allowAutomaticTasks`, and that it writes
  nothing with `vscode_claude_terminal: false`. The script the Claude terminal runs is run by
  `tests/unit/test_code_session.py` against a stand-in `claude`.
- `vscode_server` proves the role refuses an sshd that a drop-in sorted before its own has turned
  forwarding off on, naming the file, and removes what it wrote when switched off.
- Everything the playbooks write on the controller - the SSH aliases - goes into Molecule's ephemeral
  directory, never the developer's `~/.ssh`; `tests/unit/test_ssh_config.py` checks every scenario.
- `proxmox_template` runs against a stateful fake `qm` (`tests/molecule/fake_qm.sh`), a fake
  `virt-customize`, a fake `/etc/pve/.vmlist` that places a VMID on a second node, and a locally
  served "cloud image" with a real SHA256SUMS file.
- `github_projects` and the integration scenario talk to a fake GitHub API (`tests/molecule/fake_github_api.py`)
  that serves paginated responses, the authenticated endpoints that list private repositories, and points
  clone URLs at local bare repositories; a stand-in `gh` (`tests/molecule/fake_gh.sh`) ahead of the real one
  on PATH keeps the sign-in off the network. The unit tests run the role's sign-in and clone tasks against
  the same two, so the token, device-flow, cleared-token and signed-out paths are covered without Docker.
- The Claude sign-in runs against a stand-in `claude` (`tests/molecule/fake_claude.sh`) that prints the
  page, hands its browser the page that comes back to its own callback, and answers a code - pasted, or
  brought back through that callback - as Claude Code does; a stand-in `ssh` records the forward the run
  asks for, and the test plays the browser. In `tests/unit/test_claude_login.py`, once at a
  pseudo-terminal of the test's own, and in the integration scenario's side effect, whose real role then
  starts Remote Control under systemd. `tests/unit/test_sign_in_plays.py` runs both login playbooks, and
  `make deploy`'s sign-in steps as `deploy.yml` has them, with a VM that cannot be reached among them.
  Every scenario and both harnesses turn `login_desktop` off, so no test opens this machine's browser or
  touches its clipboard.
- Tailscale runs against a stand-in `tailscale` (`tests/molecule/fake_tailscale.sh`) whose output is
  Tailscale's own, as the research and a Headscale run recorded it: a join with a key on standard input, the
  web login's page and approval, an expired or refused key, logout. `tests/unit/test_tailscale.py` runs the
  role and `make tailscale-login` against it; the `tailscale` role scenario and the integration scenario
  install the real package and daemon - log upload off from its first start - with the stand-in in front of
  it, so no test reaches a Tailscale service. The unit harness puts a failing `tailscale` first on every
  run's PATH, since your machine may have the real one, signed in to your own tailnet.
- `make destroy`'s release runs in `tests/unit/test_release.py` against the stand-in `claude`, `gh` and
  `tailscale`, a fake Tailscale API (`tests/molecule/fake_tailscale_api.py`: an OAuth client's exchange and
  a device's deletion) and the fake GitHub API's credential revocation, with every canned token searched
  for in the output.
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
.logs/                      one log per make run that used Ansible (git-ignored, pruned after 14 days)
deploy.yml                  template (if missing) + provision + configure + the three sign-ins + VS Code
playbooks/                  template.yml, provision.yml, discover.yml, configure.yml, list.yml, claude_login.yml,
                            github_login.yml and tailscale_login.yml (the three sign-ins), destroy.yml,
                            ssh_config.yml (the SSH aliases, fleet-wide), code.yml (one VM in VS Code)
playbooks/tasks/            cluster_vms.yml (the shared cluster listing), vm_addresses.yml (each VM's config and
                            agent reply) and ssh_config.yml (one alias per VM in ~/.ssh/claude-on-proxmox.conf),
                            imported by the playbooks above
filter_plugins/             net_mac, guest_ipv4, guest_address and guest_addresses, for reading guest agent
                            output; tailnet_ipv4, for a VM's tailnet address; proxmox_access_denied, for telling a refused token from a slow boot;
                            ssh_config_blocks and ssh_config_prune, for the managed SSH config (proxmox.py);
                            vscode_setting, for reading VS Code's settings without writing them (vscode.py);
                            page_opener, for opening a sign-in page in your browser (desktop.py);
                            plain_text, for quoting what a VM printed without its escape sequences (text.py)
inventory/                  controller.yml, hosts.yml.example, group_vars/all/{defaults.yml,local.yml.example,vault.yml.example}
roles/                      one role per concern, each with defaults, meta/argument_specs, molecule/
roles/github_projects/library/github_repos.py   custom module
tests/unit/                 pytest: filters, github_repos, tracked-file guard, configure tags, Remote Control, the
                            three sign-ins and deploy's sign-in steps, Tailscale, destroy's release, the managed SSH
                            config, the Claude terminal, Makefile guards
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
