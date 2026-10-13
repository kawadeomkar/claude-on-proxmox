# Testing

What the suite runs, and how to run it. How it is built - the harness, each scenario, the fakes - is
[Architecture § 11](../architecture/testing.md); the workflow around a change is in
[CONTRIBUTING.md](https://github.com/kawadeomkar/claude-on-proxmox/blob/main/CONTRIBUTING.md).

```bash
make lint                 # yamllint + ansible-lint (production profile) + ruff
make syntax               # --syntax-check of every playbook against your inventory/
make unit                 # pytest: filters, github_repos module, the tracked-file guard, configure tags, Remote Control, the three sign-ins and deploy's sign-in steps, Tailscale, the managed SSH config, the Claude terminal, Makefile guards, the wiki (no network)
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

## Continuous integration

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
actions are pinned to commit SHAs, and Dependabot updates them along with the uv lock every week. The
setup steps shared by every job live in the composite action at `.github/actions/setup`.

Pushing a `v*` tag runs `.github/workflows/release.yml`, which publishes a GitHub release with notes
generated from the commits since the previous tag.
