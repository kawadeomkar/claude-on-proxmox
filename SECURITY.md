# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's ["Report a vulnerability"][advisory] button on
this repository's Security tab, not in a public issue. You should get an acknowledgement within a week.

[advisory]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability

## Scope

This project provisions a VM and installs software on it, so the interesting surface is what it trusts and
what it stores:

- **The custom module** `roles/github_projects/library/github_repos.py`, which handles a GitHub token.
- **Supply chain**: the vendored apt signing keys in `roles/dev_tools/files/` and
  `roles/tailscale/files/`, and every other download a VM gets - the uv release, the Ubuntu cloud image,
  the Claude Code installer - each verified as [What the VM trusts](docs/guides/what-the-vm-trusts.md)
  says.
- **Credential handling**: the Proxmox API token, GitHub token and Anthropic API key, which live in an
  ansible-vault file and are marked `no_log`. Every task that hands the GitHub token on is `no_log` too,
  which also keeps it out of the VM's journal (a module logs the arguments its spec does not mark
  `no_log`, stdin included), and `tests/unit/test_github_auth.py` holds each to it and greps every run,
  module arguments included, for every canned token; `github_repos` strips the token and masks the
  stripped form as well. What `claude`, `gh` and `ssh` print on the VM, which its user controls, is
  quoted in messages only through the `plain_text` filter, with every escape sequence and control
  character removed. The GitHub token is also stored on the VM, by `gh`
  (`~/.config/gh/hosts.yml`, mode 0600), so that `git` and `gh` there can push; a device-flow login
  made on the VM stores its own token the same way. Either is readable by the VM user, and so by
  Claude Code ([What the VM trusts](docs/guides/what-the-vm-trusts.md)). The Claude sign-in's one-time code
  (`roles/claude_code/tasks/browser_login.yml`) is read at a `pause` under `no_log` and travels to the
  VM on stdin into a 0600 named pipe only that `claude auth login` reads. With pipelining on, as
  `ansible.cfg` sets it, it is never written to a file or a command line; with pipelining off - which
  `ANSIBLE_KEEP_REMOTE_FILES=1`, Ansible's switch for debugging a module, forces - the module that
  carries it is a file on the VM, and that switch keeps the file. `claude_code_login_code`, the way a
  script supplies the code, belongs in a vars file passed as `-e @file`: `-e key=value` puts it on this
  machine's command line for the whole run. `tests/unit/test_claude_login.py` holds both prompts and both
  writers to `no_log` and greps whole runs for the code. The login it
  produces is Claude Code's own `~/.claude/.credentials.json`, mode 0600, which the project never reads.
  The Tailscale auth key (`vault_tailscale_auth_key`) is used for a VM's first join only, and goes to the
  VM the same way: `tailscale up --auth-key=file:/dev/stdin`, the key as the `command` module's stdin
  under `no_log`, so it is on no command line and in no file, and never stored on the VM - tailscaled
  keeps the node's own keys, not the auth key. `tests/unit/test_tailscale.py` holds that task to be the
  only one that renders the key, and greps every run, module arguments included, and every file, for every
  canned key; the role's Molecule scenario searches the VM for it after the join.
  The Tailscale API credential that deletes a VM's device (`vault_tailscale_api_token`, or
  `vault_tailscale_oauth_client_id` and `_secret`) is used only on this machine, in `uri` calls under
  `no_log`, and never reaches a VM. When `make destroy` releases a VM, gh signs out there and no GitHub
  token is revoked: revoking one GitHub CLI token through GitHub's credential revocation endpoint ended
  every GitHub CLI sign-in of the account, the controller's included, so the report says where to revoke
  it. `tests/unit/test_release.py` holds that nothing reaches that endpoint, and greps the release's whole
  output, module arguments included, for every canned credential.
- **What the VM ends up trusting**: the SSH key material, the passwordless sudo rule and the docker group
  membership.
- **What is written on the controller**: `~/.ssh/known_hosts`, the managed SSH config
  `~/.ssh/claude-on-proxmox.conf` and the one `Include` line at the top of `~/.ssh/config`
  (`playbooks/tasks/ssh_config.yml`). A bug there reaches every `ssh` on the user's machine. The VM's
  address in it is what the VM's own guest agent reported, so the VM chooses it: the `guest_ipv4`
  filter takes only a string that parses as one IPv4 address, and the task file refuses a name or
  address that is not one plain word, since a newline in either would add a directive (`ProxyCommand`
  runs on this machine).
  Inside the checkout, `.logs/` holds a copy of every Ansible run: `no_log` output is hidden there
  as on screen, but the files name hosts and addresses, so the directory is git-ignored, blocked by
  `tests/check_no_local_files.sh`, created `0700`, and pruned after `LOG_RETENTION_DAYS`. A secret
  that appears in a log is a `no_log` that is missing, and in scope.

Findings in any of those are in scope, as is anything that would cause a secret to be written to a log or
the repository.

## Known trade-offs, not vulnerabilities

Some defaults trade strictness for a working first run. These are documented in the wiki - the defaults
a VM lives with in [What the VM trusts](docs/guides/what-the-vm-trusts.md), how each is enforced in
[docs/architecture/security-model.md](docs/architecture/security-model.md) - and reports that only
restate them are not treated as vulnerabilities:

- `proxmox_validate_certs` defaults to `false`, because a stock Proxmox install presents a self-signed
  certificate. [Proxmox access](docs/getting-started/proxmox-access.md#trusting-the-proxmox-certificate)
  explains how to trust the Proxmox CA and turn it on.
- The VM's user gets passwordless sudo and docker group membership by default; both are toggles, and
  [What the VM trusts](docs/guides/what-the-vm-trusts.md) says what they mean.
- SSH host keys are accepted on first contact (`StrictHostKeyChecking=accept-new`) so a freshly cloned VM
  can be reached without manual steps. Changed keys are still refused.
- The `vscode_server` role keeps `AllowTcpForwarding` on, which VS Code's Remote - SSH needs and OpenSSH
  defaults to anyway; the VM user can forward ports, as a user with passwordless sudo always could.
  SSH agent forwarding to the VM is off by default and documented as a choice, because the VM user is
  root-equivalent and runs an AI agent.
- `make code` opens a workspace file whose tasks start `claude` in a terminal, and a shell beside it, when the window opens
  (`vscode_claude_terminal`, on by default). The file is written by this project beside the
  repositories, never in one, and its command is fixed in `playbooks/code.yml`. VS Code still runs
  it only in a window you have trusted and once you have allowed automatic tasks; the project reads
  that setting to explain what will happen, and never writes it, sets trust, or relaxes either check.
  Allowing automatic tasks is a VS Code-wide choice: it also lets a trusted repository's own
  `folderOpen` tasks run, as it would without this project.
- The same file's "Session notes" task writes a page about the VM next to it (`vscode_session_notes`,
  on by default): its addresses, and the Claude, GitHub and Tailscale accounts signed in there, read
  from the VM as the window opens. It is the VM user's alone (0600, in a 0700 directory), never in a
  repository; no command the task runs prints a token, and what the VM reports is stripped of escape
  sequences and control characters before it is written. A window it would put back in Restricted
  Mode never gets the notes' folder: the project does not change what a window you trusted covers.
- Deleting a VM also removes its host key from `known_hosts`, which re-opens that first-contact window
  for its address. The address goes back into the DHCP pool, so whatever answers there next is trusted
  once - and the next `make deploy` then sends it your SSH keys, GitHub token and Anthropic API key. The
  sign-ins hand it more: `make deploy` and `make login` run `claude auth login` and `gh auth login`
  there and ask you to authorise what they print, so an impostor that prints
  a genuine sign-in page or device code of its own receives a claude.ai login, or a GitHub OAuth token
  with gh's scopes (`repo`, `read:org`, `gist`) and `workflow` that does not expire until revoked - with
  no GitHub token in the vault as much as with one. Authorise only a page or code for a VM you have just
  created or know to be yours. Keeping
  the key instead would only trade this for a failure every time a lease is recycled, so the removal is
  deliberate. On a network where you do not trust every device, put the VMs on a bridge that only
  trusted devices can reach (`proxmox_bridge`, set before `make deploy TYPE=template`, since every clone inherits
  the template's NIC). A fixed address from `proxmox_vm_ipconfig`, outside the DHCP pool, keeps the
  address from being leased to anything else, but only for a single VM: that one value applies to every
  VM a run provisions, so a second VM would be given the same address.

- **Tailscale reach.** A tailnet's default policy lets every device reach every other, tagged devices
  included, and these VMs run an AI agent with passwordless sudo. The [policy edit](docs/guides/tailscale.md#keeping-an-agent-vm-off-your-other-devices) narrows the
  default rule to the people's own devices, so a tagged VM can be reached but can open no connection, and
  adds a test the admin console runs on every save; a VM joined by the web login without a tag is the
  user's own device, and reaches what the user's devices reach. On the VM, sudo can always log the node
  out, use `tailscale serve` or `funnel` where the policy allows them, or read
  `/var/lib/tailscale/tailscaled.state`: only the tag and the policy bound what the node can reach. By
  default the VM keeps its own resolver (`tailscale_accept_dns: false`): a tailnet's DNS settings that
  override local DNS would otherwise answer every lookup on the VM. Tailscale's log upload is on, as
  Tailscale ships it; `tailscale_upload_logs: false` turns it off.
- **What a destroyed VM tells the controller.** `make destroy` releases a VM before deleting it, from
  what the VM reports - and the VM runs an agent with sudo. The Tailscale node ID the device is deleted by
  is the VM's to report, and the API credential can delete any device in the tailnet, so the device is
  looked up first and deleted only when its name is the one this project asked the VM to join as
  (`tailscale_hostname`, or that name with Tailscale's `-<n>`); anything else is said, not deleted. No
  GitHub token is revoked, so nothing the VM reports can get one revoked. A VM whose address was recycled
  since is released wherever that address
  now answers - an SSH host key it does not hold fails the connection first, and nothing is sent to it but
  the release's own commands.
- **The Claude sign-in's forward.** While Claude's browser sign-in waits, one port on this machine's
  loopback (`ssh -L localhost:<port>`) reaches claude's callback on the VM's own `127.0.0.1`, so any process
  on this machine can reach that callback for those minutes - as any process on the VM always can. The
  port is the VM's to choose, read from the page its claude handed its browser: it must be between 1024 and
  65535, the forward fails rather than share a port already in use here, and its far end ends with the
  sign-in. The page opens here only when it is Claude's own sign-in page; the recycled-address trade-off
  above applies to it as to the code it replaces.
- **A pending web login.** While Tailscale's web login waits for an approval, its page is readable by
  every local user on the VM (`tailscale status --json`), and whoever approves it first decides which
  tailnet the VM joins. Each report names the tailnet joined, so one that is not yours shows; the key path
  has no page at all. The page is opened on this machine only when it is Tailscale's own sign-in page.
  The recycled-address trade-off above extends to it: an impostor's page, approved, joins the impostor to
  your tailnet.

If you can show one of these is exploitable beyond the documented trade-off, please report it.

## Secrets committed by mistake

CI runs gitleaks over the working tree and over every commit a push or pull request brings in, so a
token committed and then deleted in a later commit still fails the run
([docs/architecture/security-model.md](docs/architecture/security-model.md#secret-scanning) lists what
it cannot see). A failing scan means the secret has already been pushed to a public repository, and
rewritten commits stay reachable by SHA, from pull requests and in forks. Revoke or rotate the
credential first, as GitHub's guide to [removing sensitive data][sensitive] advises; rewriting history
comes second.

[sensitive]: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository
