# Your first deploy

Before this, the Proxmox host needs an API token and your SSH key: see [Proxmox access](proxmox-access.md),
and [Requirements](requirements.md) for what the project assumes of the host.

## Set up the project

```bash
git clone https://github.com/kawadeomkar/claude-on-proxmox.git && cd claude-on-proxmox
pyenv virtualenv 3.12 claude-on-proxmox && pyenv local claude-on-proxmox   # a Python environment of its own
make init            # installs the tools, then asks for your Proxmox host, its API token and your GitHub username
```

The project keeps no environment of its own, and needs no particular tool to make one: pyenv, above, is a
suggestion. Any virtualenv of Python 3.12 or later works -
`python3 -m venv ~/.virtualenvs/claude-on-proxmox`, then activate it in each shell before `make`, or name
its Python on each line with `PYTHON=~/.virtualenvs/claude-on-proxmox/bin/python`. `make install`, which
`make init` runs first, installs the locked tools into the Python that `python3` is in this directory, or
into the one `PYTHON=` names, and every target runs them from there - never from a Python that is not a
virtualenv, which they refuse. It installs the versions `uv.lock` pins and removes nothing else. Run
`make install` again after a pull.

`make init` then copies the three git-ignored files below from their examples and asks, at the terminal,
for each value a first deploy needs that is still the example's:

1. **The Proxmox host's address**, as this machine reaches it: an IP address or a host name, without
   `https://` or a port.
2. **The API token's ID**, `user@realm!name`. Enter keeps `ansible@pve!ansible`, the name the `pveum`
   commands in [Proxmox access](proxmox-access.md) give it.
3. **The token's secret**, which `pveum` printed once. It is not shown as you type.
4. **Your GitHub username**, whose repositories are cloned onto each VM.

Enter at a question leaves it for the next `make init`, which asks again for what is still missing; an
answer that cannot be right (a URL for the address, a secret that is not one) is asked for once more.
What you answered is never asked again. The vault is written encrypted, and its password, which
`make init` makes, is in `.vault_pass`: keep that file, since the vault cannot be opened without it.

| File | Contents |
|------|----------|
| `inventory/hosts.yml` | The address of your Proxmox host, which `make init` asks for. VMs are **not** listed here |
| `inventory/group_vars/all/vault.yml` | Encrypted: the token secret and your GitHub username, which `make init` asks for. Optionally `vault_github_token`, `vault_anthropic_api_key` and `vault_tailscale_auth_key`, with `make vault` |
| `inventory/group_vars/all/local.yml` | Only what differs from the defaults: a token not named `ansible@pve!ansible` (`make init` asks), a storage or bridge that is not `local-lvm` or `vmbr0`, a node not named `pve`, `tailscale_enabled: false` without a Tailscale account, VM size, which repositories to clone |

Every setting the two files take, with its default, is in the [settings reference](../reference/settings.md),
and `make vault` opens the vault in your editor. Tailscale is on by default: without a Tailscale account,
set `tailscale_enabled: false` in `local.yml`, or every deploy opens Tailscale's sign-in page and waits
for it. `make init` reminds you while `local.yml` does not set it either way, and asks nothing about it:
`make init TAILSCALE=off` writes the setting.

### Without a terminal, or scripted

With no terminal, `make init` asks nothing and writes nothing but what its command line gives it, so it
can be scripted. Each answer is a variable on the command line - `make` takes no `--flags` - and the
token's secret comes from a file, or from the environment, never from the command line, which `make`
hands to every command it runs and your shell keeps in its history:

```console
$ (umask 077; printf %s "$PVE_SECRET" > ~/.config/pve-token)
$ make init HOST=pve.lan GITHUB_USER=octocat TOKEN_SECRET_FILE=~/.config/pve-token
```

| Variable | Answers |
|---|---|
| `HOST` | The Proxmox host's address |
| `TOKEN_USER`, `TOKEN_NAME` | The token's user (`ansible@pve`) and name (`ansible`), each only when it differs |
| `TOKEN_SECRET_FILE` | A file holding the token's secret; or `CLAUDE_ON_PROXMOX_TOKEN_SECRET` exported in the environment, from a CI secret or `read -rs`, never typed into a command |
| `GITHUB_USER` | Your GitHub username |
| `TAILSCALE` | `on` or `off`: `tailscale_enabled`, which is on by default |
| `PROMPT=no` | Ask nothing, even at a terminal |

A value given is held to the same rules as one typed, and fills only what is still the example's: one
equal to what is set already changes nothing, so the same command can run again, and one that differs
fails the run, naming both. The vault it went into is encrypted in the same run, and `.vault_pass`
made when there is none: keep it, or set `ANSIBLE_VAULT_PASSWORD_FILE` to a password file of your own
before the first run. A run that was given values but still lacks the address, the secret or the
username fails, naming what is missing; `PROMPT=no` makes a run at a terminal one of these. At a terminal,
the values given are taken and the rest asked, Enter still leaving one for later.

Or skip `make init`'s questions and edit the three files yourself, then run `make vault`, which encrypts
the vault (with the password in `.vault_pass`, if you made one).

## Deploy

```bash
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
and a run with no terminal skips all three; either way the deploy finishes, `make login alpha` does
them later (`TYPE=claude`, `github` or `tailscale` for one of them), and `deploy_sign_in: false` leaves them out on purpose. A sign-in that fails is said, with the command
that retries it, and the rest of the deploy goes on. [During a run](../guides/runs.md) shows how
each of these waits looks.

## Running it again, and the stages one by one

Every stage is idempotent, so `make deploy` is also what you re-run after changing a variable: a VM
already signed in is only reported, and VS Code brings its window to the front. (VM hardware — cores,
memory and its minimum, disk — is applied only when the VM is created; changing it for a VM that already exists needs
`proxmox_vm_force_update`, see [Re-running](../guides/vms.md#re-running).) It works on the VMs named in `VM_NAME` and
nothing else: it configures exactly what it has just provisioned, never the rest of your VMs, and
signs them in one after another. For more than one VM it opens no window, and ends with each VM's
`make code` line. Each stage is also a command of its own, for when you want only that part -
`make deploy` with a `TYPE`, then `make login` and `make code`:

```bash
make deploy TYPE=template             # once per Proxmox host   (over SSH, as root)
make deploy alpha TYPE=provision      # create + start a VM     (via the API)
make deploy alpha TYPE=configure      # set it up               (over SSH, as the VM user)
make login alpha                      # sign Claude, gh and Tailscale in; TYPE=claude, github or tailscale for one
make code alpha                       # open it in VS Code
```

A second `make deploy` rebuilds nothing. It checks through the API whether the template exists and
skips that play when it does — and because a skipped import takes its fact-gathering step with it, a
run that does not need to build the template opens **no SSH connection to the Proxmox host at all**.
Provisioning finds the VMs by name instead of creating them, and configuration re-applies only what
has drifted.

## What the run reports

`make deploy TYPE=provision` prints the address each VM was given:

```
alpha is up at 192.0.2.51
```

A VM that already exists but has been shut down is left that way: provisioning does not start it or
wait for its address, `make deploy` does not configure it, and the run says so:

```
gamma is stopped and was left that way, so it is not configured. Start it, or re-run with proxmox_vm_start_existing=true.
```

That run exits 0: the job of `make deploy TYPE=provision` and `make deploy` is that the VMs exist, and a stopped
VM does, so leaving it alone is a success, reported. `make deploy VM_NAME=gamma TYPE=configure` for the same VM
fails instead — its job is the software on the VM you named, and it cannot do that job on a VM that is
off, so it stops rather than configure the rest and exit 0 with gamma silently skipped.

`make list` answers the same question later, for everything this project created. It is read-only,
and unlike the discovery that `make deploy TYPE=configure` runs, it shows a VM whether or not its guest agent
is answering:

```
NAME                        VMID  STATUS    ADDRESS
alpha                       9001  running   192.0.2.51
beta                        9002  running   192.0.2.52
gamma                       9003  stopped   -

3 VMs tagged "claude-on-proxmox".
VS Code: make code VM_NAME=<name> [PROJECT=<repo>] (make deploy TYPE=ssh-config refreshes the aliases).
No address for gamma: the guest agent has not reported one.
Those VMs may be stopped, still booting, built without the agent, or on a node that is down.
```

Later, `make code VM_NAME=alpha` opens the VM in VS Code again, and the alias the run wrote, named
after the VM, makes `ssh alpha` work too (see [VS Code](../guides/vscode.md)).
[VMs and fleets](../guides/vms.md) covers naming, several VMs at once, and re-running.

## If a run fails

A run that fails stops and says why, and `make logs` lists the log of the whole run. Fix the cause and run
the same `make deploy` again: every stage is idempotent, and a VM a failed run left half-built is finished
rather than skipped. `make vault` changes a secret in the encrypted vault. The usual snags on a first
run:

| What the run says | What to check |
|---|---|
| `make deploy TYPE=template` cannot refresh the package lists | An enterprise repository enabled on the host without a subscription: [Package repositories](requirements.md#package-repositories) |
| The token is refused (401 or 403) | `proxmox_api_user`, `proxmox_api_token_id` and `vault_proxmox_api_token_secret`, and the role's privileges: [Creating the Proxmox API token](proxmox-access.md#creating-the-proxmox-api-token) |
| A VM never reports an address | The token's guest-agent privilege (`VM.Monitor` on 8.x, `VM.GuestAgent.Audit` on 9.x), and a template built with the guest agent: [Upgrading](../guides/upgrading.md) |
| The VM is created untagged, or `make deploy TYPE=configure` finds no VM | Datacenter → Options → User Tag Access: [Creating the Proxmox API token](proxmox-access.md#creating-the-proxmox-api-token) |
| The clone fails, or the disk cannot be created | `proxmox_storage` (an install on ZFS has `local-zfs`, not `local-lvm`) and `proxmox_node`, which must be the node that holds the template: [Requirements](requirements.md#storage) |
| A sign-in fails | The run names the command that retries it - `make login` with the VM's name and the sign-in's `TYPE` - and the rest of the deploy goes on |
