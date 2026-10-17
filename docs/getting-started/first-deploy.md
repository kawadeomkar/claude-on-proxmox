# Your first deploy

Before this, the Proxmox host needs an API token and your SSH key: see [Proxmox access](proxmox-access.md),
and [Requirements](requirements.md) for what the project assumes of the host.

## Set up the project

```bash
git clone https://github.com/kawadeomkar/claude-on-proxmox.git && cd claude-on-proxmox
make init            # venv + collections + copies the *.example files + pre-commit hook
```

Edit the three git-ignored files `make init` created:

| File | Contents |
|------|----------|
| `inventory/hosts.yml` | The address of your Proxmox host. VMs are **not** listed here |
| `inventory/group_vars/all/vault.yml` | `vault_proxmox_api_token_secret`: the token secret `pveum` printed, shown only once; `vault_github_username`. Optionally `vault_github_token`, `vault_anthropic_api_key` and `vault_tailscale_auth_key` |
| `inventory/group_vars/all/local.yml` | Only what differs from the defaults: a storage or bridge that is not `local-lvm` or `vmbr0`, a node not named `pve`, `tailscale_enabled: false` without a Tailscale account, VM size, which repositories to clone |

Every setting the two files take, with its default, is in the [settings reference](../reference/settings.md).
Tailscale is on by default: without a Tailscale account, set `tailscale_enabled: false` in `local.yml`,
or every deploy opens Tailscale's sign-in page and waits for it.

## Encrypt the secrets, then deploy

The `make` targets refuse to run while `vault.yml` is still plaintext.

```bash
(umask 077; openssl rand -base64 32 > .vault_pass)
make vault-encrypt

make deploy VM_NAME=alpha
```

`make deploy` is the whole thing in one command. It builds the template if it is missing, creates
and configures the VM, signs it in to Claude, GitHub and Tailscale, and opens it in VS Code, where a
terminal of the window starts a Claude session. The sign-ins are yours, because no vendor lets anything
else take them: each opens a page in your browser, and the run waits for you there.

## The three sign-ins

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
   [Tailscale](../guides/tailscale.md).

With `github_login_auto_start: false` or `tailscale_login_auto_start: false` the run asks first, VM by VM,
so a VM can be left for later: Enter signs in a VM whose Claude sign-in you just answered, `y` one that
needed none (signed in already, or on an API key), and `n` leaves it. A VM whose Claude sign-in nobody
answered - Enter at its `Code:` prompt, or a browser that never came back - is left out of the other two,
and a run with no terminal skips all three; either way the deploy finishes, `make claude-login
VM_NAME=alpha`, `make github-login VM_NAME=alpha` and `make tailscale-login VM_NAME=alpha` do them later,
and `deploy_sign_in: false` leaves them out on purpose. A sign-in that fails is said, with the command
that retries it, and the rest of the deploy goes on. [During a run](../guides/runs.md) shows how
each of these waits looks.

## Running it again, and the stages one by one

Every stage is idempotent, so `make deploy` is also what you re-run after changing a variable: a VM
already signed in is only reported, and VS Code brings its window to the front. (VM hardware — cores,
memory, disk — is applied only when the VM is created; changing it for a VM that already exists needs
`proxmox_vm_force_update`, see [Re-running](../guides/vms.md#re-running).) It works on the VMs named in `VM_NAME` and
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

## What the run reports

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
after the VM, makes `ssh alpha` work too (see [VS Code](../guides/vscode.md)).
[VMs and fleets](../guides/vms.md) covers naming, several VMs at once, and re-running.

## If a run fails

A run that fails stops and says why, and `make logs` lists the log of the whole run. Fix the cause and run
the same `make deploy` again: every stage is idempotent, and a VM a failed run left half-built is finished
rather than skipped. `make vault-edit` changes a secret in the encrypted vault. The usual snags on a first
run:

| What the run says | What to check |
|---|---|
| `make template` cannot refresh the package lists | An enterprise repository enabled on the host without a subscription: [Package repositories](requirements.md#package-repositories) |
| The token is refused (401 or 403) | `proxmox_api_user`, `proxmox_api_token_id` and `vault_proxmox_api_token_secret`, and the role's privileges: [Creating the Proxmox API token](proxmox-access.md#creating-the-proxmox-api-token) |
| A VM never reports an address | The token's guest-agent privilege (`VM.Monitor` on 8.x, `VM.GuestAgent.Audit` on 9.x), and a template built with the guest agent: [Upgrading](../guides/upgrading.md) |
| The VM is created untagged, or `make configure` finds no VM | Datacenter → Options → User Tag Access: [Creating the Proxmox API token](proxmox-access.md#creating-the-proxmox-api-token) |
| The clone fails, or the disk cannot be created | `proxmox_storage` (an install on ZFS has `local-zfs`, not `local-lvm`) and `proxmox_node`, which must be the node that holds the template: [Requirements](requirements.md#storage) |
| A sign-in fails | The run names the command that retries it - `make claude-login`, `make github-login` or `make tailscale-login` with `VM_NAME` - and the rest of the deploy goes on |
