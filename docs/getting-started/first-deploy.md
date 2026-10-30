# Your first deploy

Before this, the Proxmox host needs your SSH key for root - `just init` offers to make the API token over it -
see [Proxmox access](proxmox-access.md), and [Requirements](requirements.md) for what the project assumes of
the host.

## Set up the project

```bash
git clone https://github.com/kawadeomkar/claude-on-proxmox.git && cd claude-on-proxmox
pyenv virtualenv 3.12 claude-on-proxmox && pyenv local claude-on-proxmox   # a Python environment of its own
just init            # installs the tools, asks for your Proxmox host and GitHub username, and offers to make the API token
```

The project keeps no environment of its own, and needs no particular tool to make one: pyenv, above, is a
suggestion. Any virtualenv of Python 3.12 or later works -
`python3 -m venv ~/.virtualenvs/claude-on-proxmox`, then activate it in each shell before `just`, or export
its Python as `PYTHON=~/.virtualenvs/claude-on-proxmox/bin/python`. `just install`, which
`just init` runs first, installs the locked tools into the Python that `python3` is in this directory, or
into the one `PYTHON=` names, and every recipe runs them from there - never from a Python that is not a
virtualenv, which they refuse. It installs the versions `uv.lock` pins and removes nothing else. Run
`just install` again after a pull.

`just init` then copies the three git-ignored files below from their examples and asks, at the terminal,
for each value a first deploy needs that is still the example's:

1. **Your GitHub username**, whose repositories are cloned onto each VM.
2. **Whether to use Tailscale**, which puts each VM on your tailnet, so it is reached from anywhere and not
   only from this LAN ([Tailscale](../guides/tailscale.md)). `y` needs a Tailscale account; `n` keeps it off
   every VM. Either is written into `local.yml` as `tailscale_enabled`, which you can change there any time,
   and never asked again. Until you answer, it is on.
3. **A Tailscale auth key**, after a `y`, while the vault holds none: it joins each new VM by itself during
   `just deploy`. It is not shown as you type. Enter goes without one, and each VM signs in on Tailscale's
   page during `just deploy` instead; the next `just init` asks again. To always sign in on the page and
   never be asked, set `tailscale_auth_method: web` in `local.yml`.
4. **The Proxmox host's address**, as this machine reaches it: an IP address or a host name, without
   `https://` or a port.
5. **The API token's ID**, `user@realm!name` - its ID only: the secret is asked for after it, unless
   `just init` makes the token. Enter keeps `ansible@pve!ansible`, the name `tools/proxmox-access.sh` gives it.
6. **Whether to set up the API access.** `just init` reaches the host over root SSH, shows the `pveum`
   commands `tools/proxmox-access.sh` would run there to make the role, the user, its grants and the token,
   and runs them on `y`, putting the token's secret straight into the vault
   ([Letting `just init` set it up](proxmox-access.md#letting-just-init-set-it-up)). It asks first which
   storage or bridge to use when the host has no `local-lvm` or `vmbr0`. It changes nothing on the host on `n`
   or Enter, and asks nothing when the host is set up already or root SSH fails.
7. **The token's secret**, unless `just init` just made the token: the one `tools/proxmox-access.sh` printed,
   when you ran it yourself. It is not shown as you type.

Enter at a question leaves it for the next `just init`, which asks again for what is still missing; an
answer that cannot be right (a URL for the address, a secret that is not one) is asked for once more.
What you answered is never asked again. The vault is written encrypted, and its password, which
`just init` makes, is in `.vault_pass`: keep that file, since the vault cannot be opened without it.

Then `just init` checks what `just deploy` will need of Proxmox: that the API answers and takes the token,
that the token holds every privilege the project uses on `/vms`, that the template exists or, while it does
not, that root SSH to the host works, and that the VMs will get an SSH key. It works out the node - the one
that holds the template, the only one, or the one `inventory/hosts.yml` reaches - and lists the storages for
VM disks and the bridges the token can use there. Where a default does not fit (`pve`, `local-lvm`,
`vmbr0`), it writes the node, and asks which storage or bridge to use, into `local.yml`; the ones the token
cannot use are shown with what it lacks. Each check prints `ok`, or `!!` with what to fix, and `just init`
fails when one finds a problem, so run it again once it is fixed. While the address or the secret is still
the example's, nothing is checked.

At a terminal, after `just install`'s own output, the questions and the checks are drawn live as one run: the
questions a row each, with no frame between one and the next, and the checks listed as they are made, a `✗`
for each problem. It ends with one summary, the checks whole in it, and one `NEXT STEPS` block of what is
left: the values still to set, each problem with its fix or, once there is none, `just deploy alpha` - the
command below. Without a terminal it prints a line as each step ends instead of the live rows, then the same one
summary and one `NEXT STEPS` block.

| File | Contents |
|------|----------|
| `inventory/hosts.yml` | The address of your Proxmox host, which `just init` asks for. VMs are **not** listed here |
| `inventory/group_vars/all/vault.yml` | Encrypted: your GitHub username, which `just init` asks for, and the token's secret, which it writes when it makes the token and asks for otherwise; a Tailscale auth key, which it asks for after a `y` to Tailscale. Optionally `vault_github_token` and `vault_anthropic_api_key`, with `just vault` |
| `inventory/group_vars/all/local.yml` | Only your preferences - VM size, which repositories to clone - and what `just init` writes there: `tailscale_enabled` from your answer, a token not named `ansible@pve!ansible`, and the node, storage and bridge when the defaults (`pve`, `local-lvm`, `vmbr0`) do not fit your host |

Every setting the two files take, with its default, is in the [settings reference](../reference/settings.md),
and `just vault` opens the vault in your editor. Tailscale stays on, the default, while `local.yml` does
not set `tailscale_enabled` - every deploy then opens Tailscale's sign-in page and waits for it - so
`just init` says so until you answer its question; `just init --tailscale off` answers it without one.

### Without a terminal, or scripted

With no terminal, `just init` asks nothing and writes nothing but what its command line gives it, so it
can be scripted. Each answer is an option of `just init`'s, and the token's secret comes from a file, or
from the environment, never from the command line, which your shell keeps in its history and the process
list shows (a word after the options, make's `TOKEN_SECRET=` among them, is refused without being said).
Nor does it offer to set up the API access: run `tools/proxmox-access.sh` on the host yourself
([Setting it up yourself](proxmox-access.md#setting-it-up-yourself)) and hand over the secret it prints:

```console
$ (umask 077; printf %s "$PVE_SECRET" > ~/.config/pve-token)
$ just init --host pve.lan --github-user octocat --token-secret-file ~/.config/pve-token
```

| Option | Answers |
|---|---|
| `--host` | The Proxmox host's address |
| `--token-user`, `--token-name` | The token's user (`ansible@pve`) and name (`ansible`), each only when it differs |
| `--token-secret-file` | A file holding the token's secret, a relative path from where you run `just`; or `CLAUDE_ON_PROXMOX_TOKEN_SECRET` exported in the environment, from a CI secret or `read -rs`, never typed into a command |
| `--github-user` | Your GitHub username |
| `--storage` | The storage for VM disks: written whenever the token can use it, the default's fitting or not |
| `--bridge` | The bridge the template is built on, before it is: clones take the template's |
| `--tailscale` | `on` or `off`: whether to use Tailscale, written as `tailscale_enabled`. Its auth key is asked for at a terminal only; scripted, put it in the vault with `just vault` |
| `--no-prompt` | Ask nothing, even at a terminal |

A value given is held to the same rules as one typed, and fills only what is still the example's: one
equal to what is set already changes nothing, so the same command can run again, and one that differs
fails the run, naming both. The vault it went into is encrypted in the same run, and `.vault_pass`
made when there is none: keep it, or set `ANSIBLE_VAULT_PASSWORD_FILE` to a password file of your own
before the first run. A run that was given values but still lacks the address, the secret or the
username fails, naming what is missing; `--no-prompt` makes a run at a terminal one of these. At a terminal,
the values given are taken and the rest asked, Enter still leaving one for later.

Or skip `just init`'s questions and edit the three files yourself, then run `just vault`, which encrypts
the vault (with the password in `.vault_pass`, if you made one).

## Deploy

```bash
just deploy alpha
```

`just deploy` is the whole thing in one command. It builds the template if it is missing, creates
and configures the VM, signs it in to Claude, GitHub and Tailscale, and opens it in VS Code, where a
terminal of the window starts a Claude session. The sign-ins are yours, because no vendor lets anything
else take them: each opens a page in your browser, and the run waits for you there.

## The three sign-ins

1. **Claude.** Authorise Claude Code on the page. Your browser then comes back to the VM by itself, with
   nothing to paste: the run forwards Claude's sign-in port from this machine to the VM over SSH for the
   length of the sign-in, and waits up to five minutes (`claude_login_approval_timeout`). Where that port
   is taken here, no browser can open on this machine, or you run `just` over SSH from another one, the
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
each saying so with the command that starts it, and a run with no terminal skips all three; either way the
deploy finishes, `just login alpha` does them later (`--only claude`, `github` or `tailscale` for one of
them), and `deploy_sign_in: false` leaves them out on purpose. A sign-in that fails is said, with the
command that retries it, and the rest of the deploy goes on; NEXT STEPS at the end lists it again, with
every sign-in left. [During a run](../guides/runs.md) shows how each of these waits looks.

## Running it again, and the stages one by one

Every stage is idempotent, so `just deploy` is also what you re-run after changing a variable: a VM
already signed in is only reported, and VS Code brings its window to the front. (VM hardware — cores,
memory and its minimum, disk — is applied only when the VM is created; changing it for a VM that already exists needs
`proxmox_vm_force_update`, see [Re-running](../guides/vms.md#re-running).) It works on the VMs it is given and
nothing else: it configures exactly what it has just provisioned, never the rest of your VMs, and
signs them in one after another. For more than one VM it opens no window, and ends with each VM's
`just code` line. Each stage is also a command of its own, for when you want only that part -
`just deploy` with `--only`, then `just login` and `just code`:

```bash
just deploy --only template             # once per Proxmox host   (over SSH, as root)
just deploy alpha --only provision      # create + start a VM     (via the API)
just deploy alpha --only configure      # set it up               (over SSH, as the VM user)
just login alpha                        # sign Claude, gh and Tailscale in; --only claude, github or tailscale for one
just code alpha                         # open it in VS Code
```

A second `just deploy` rebuilds nothing. It checks through the API whether the template exists and
skips that play when it does — and because a skipped import takes its fact-gathering step with it, a
run that does not need to build the template opens **no SSH connection to the Proxmox host at all**.
Provisioning finds the VMs by name instead of creating them, and configuration re-applies only what
has drifted.

## What the run reports

`just deploy --only provision` prints the address each VM was given:

```
alpha is up at 192.0.2.51
```

A VM that already exists but has been shut down is left that way: provisioning does not start it or
wait for its address, `just deploy` does not configure it, and the run says so:

```
gamma is stopped and was left that way, so it is not configured. Start it, or re-run with: just deploy gamma --start
```

That run exits 0: the job of `just deploy --only provision` and `just deploy` is that the VMs exist, and a stopped
VM does, so leaving it alone is a success, reported. `just deploy gamma --only configure` for the same VM
fails instead — its job is the software on the VM you named, and it cannot do that job on a VM that is
off, so it stops rather than configure the rest and exit 0 with gamma silently skipped.

`just status` answers the same question later, for everything this project created, and says what needs
doing next. It is read-only, changes nothing anywhere, and unlike the discovery that `just deploy --only
configure` runs, it shows a VM whether or not its guest agent is answering. At a terminal it draws the
report within a second and fills it in as each value is read:

```
$ just status
claude-on-proxmox  2026-10-25 14:32 PDT · pve, Proxmox VE 9.2.21 · 3 VMs

VMS  pve · this project: 3 VMs, 6 vCPUs, 12 GiB, 96 GiB of disk
 VMID  NAME     UP        vCPU  CPU  LAST 24 H     MEM GiB             ROOT GiB   CREATED
 9001  ● alpha  2 d 4 h      2  12%  ▁▁▁▁▁▁▁▂▂▁▁▁  3.1 / 4.0           11 / 31    13 d ago
 9002  ● beta   6 h 2 m      2   3%  ▁▁▁▁▁▁▁▁▁▁▁▁  1.2 / 4.0, min 2.0  9.8 / 31   13 d ago
 9003  ○ gamma  stopped      2                     — / 4.0             — / 32     13 d ago
       + 4 other VMs on pve, not this project's: 8 vCPUs, 24 GiB, 4 running

STORAGE
 local-lvm  lvmthin  ███░░░░░░░░░  24%  34 / 141 GiB · this project's disks 96 GiB
 template   ubuntu-24.04-cloudinit (9000) · built 40 d ago

ACCESS   LAN           SSH ALIAS   TAILNET
 alpha   192.0.2.51    alpha       alpha.tailnet-example.ts.net  100.64.0.5
 beta    192.0.2.52    beta        beta.tailnet-example.ts.net   100.64.0.6
 gamma   stopped       gamma       —
 VS Code: just code <name> [--project <repo>] [--session <id>]

SIGN-INS  CLAUDE CODE              REMOTE CONTROL           GITHUB            TAILSCALE
 alpha    ● 2.1.310 signed in      ● running                ● octocat         ● running
 beta     ● 2.1.310 signed in      ● running                ● octocat         ● running

SYSTEM    OS              KERNEL                     LOAD 1 / 15 MIN   UPDATES
 alpha    Ubuntu 24.04.3  6.8.0-87                   0.42 / 0.20       none
 beta     Ubuntu 24.04.3  6.8.0-87                   0.02 / 0.00       none

WORK  in ~/projects, as of each checkout's last fetch
          CHECKED                 NOT ON ANY REMOTE, OR NOT COMMITTED             CLAUDE   VS CODE
 alpha    2 checkouts, 1 tree     claude-on-proxmox: 2 commits on 1 branch,       1        1 window
                                  changes in 1 worktree
 beta     1 checkout              nothing                                         —        —

read in 7.9 s · proxmox 0.9 s · vms 3 of 3 6.1 s
exit 0 · nothing is wrong · --only, --json: just help status
```

It exits 0 when nothing is wrong, 1 for a problem, 2 when something could not be read, so a script can
ask it; `--json` prints the report as JSON, and `--only vms,storage` the sections named. `just status
alpha` reads one VM. [VMs and fleets](../guides/vms.md#what-just-status-shows) says what each part means.

Later, `just code alpha` opens the VM in VS Code again, and the alias the run wrote, named
after the VM, makes `ssh alpha` work too (see [VS Code](../guides/vscode.md)).
[VMs and fleets](../guides/vms.md) covers naming, several VMs at once, and re-running.

## If a run fails

A run that fails stops and says why, and `just logs` lists the log of the whole run. Fix the cause and run
the same `just deploy` again: every stage is idempotent, and a VM a failed run left half-built is finished
rather than skipped. `just vault` changes a secret in the encrypted vault. The usual snags on a first
run:

| What the run says | What to check |
|---|---|
| `just init` marks a check `!!` | The line says what to fix - an address, the token's name or secret, a privilege for the role, root SSH: [Failure modes](../architecture/failure-modes.md#a-problem-just-init-finds) |
| `just deploy --only template` cannot refresh the package lists | An enterprise repository enabled on the host without a subscription: [Package repositories](requirements.md#package-repositories) |
| The token is refused (401 or 403) | `proxmox_api_user`, `proxmox_api_token_id` and `vault_proxmox_api_token_secret`, and the role's privileges: [The API token](proxmox-access.md#the-api-token) |
| A VM never reports an address | The token's guest-agent privilege (`VM.Monitor` on 8.x, `VM.GuestAgent.Audit` on 9.x), and a template built with the guest agent: [Upgrading](../guides/upgrading.md) |
| The VM is created untagged, or `just deploy --only configure` finds no VM | Datacenter → Options → User Tag Access: [User Tag Access](proxmox-access.md#user-tag-access) |
| The clone fails, or the disk cannot be created | `proxmox_storage` (an install on ZFS has `local-zfs`, not `local-lvm`) and `proxmox_node`, which must be the node that holds the template: [Requirements](requirements.md#storage) |
| A sign-in fails | The run names the command that retries it - `just login` with the VM's name and `--only` the sign-in - and the rest of the deploy goes on |
