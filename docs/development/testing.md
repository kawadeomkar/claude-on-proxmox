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
so the tests are deterministic: what each scenario proves is in
[§11.2](../architecture/testing.md#112-role-scenarios--make-molecule-molecule_roles) and [§11.3](../architecture/testing.md#113-playbook-scenarios--make-molecule-scenario-scenario), and every
fake and stand-in in [§11.4](../architecture/testing.md#114-the-fakes--testsmolecule). The scenarios that
install packages download them, so they need internet access
([§11.6](../architecture/testing.md#116-which-scenarios-need-the-network)).

Not covered by Molecule: the npm install method for Claude Code, reinstalling on an exact-version bump,
and `common_user_passwordless_sudo: false`. The Vagrant target exercises `configure.yml` on a real VM
including the Docker and qemu-guest-agent services.

## Continuous integration

`.github/workflows/ci.yml` runs lint and the unit tests, every Molecule scenario, a dependency review
and, weekly, a canary against what the project installs from outside the repository. What each job
does is in [§12](../architecture/ci.md). No job needs a secret, so the full suite runs on pull requests
from forks; keep it that way.
