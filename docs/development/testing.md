# Testing

What the suite runs, and how to run it. How it is built - the harness, each scenario, the fakes - is
[Architecture § 11](../architecture/testing.md); the workflow around a change is in
[CONTRIBUTING.md](https://github.com/kawadeomkar/claude-on-proxmox/blob/main/CONTRIBUTING.md).

```bash
just lint                     # every linter: the lock, yamllint, ansible-lint (production), ruff, --syntax-check, the other pre-commit hooks
just lint syntax              # one of them: lock, yaml, ansible, python, syntax or hooks
just test                     # just lint, then everything below but the last two: the local quality gate
just test unit                # pytest: filters, github_repos module, the tracked-file guard, configure tags, Remote Control, the three sign-ins and deploy's sign-in steps, Tailscale, the managed SSH config, the Claude terminal, the justfile's guards, the wiki (no network)
just test molecule            # every Molecule (Docker) scenario: the three below
just test role                # each role's scenario, incl. idempotence; --role common,dev_tools for some of them
just test integration         # playbooks/configure.yml end to end in a container
just test provision           # provision.yml + discover.yml against a fake Proxmox API
just deploy --check           # dry run of configure.yml against your real VM (--check --diff)
just vagrant                  # configure.yml on a real VirtualBox VM (just vagrant destroy removes it)
```

At a terminal, `just lint` is drawn live, a row for each check, the running one's last line beside it and the
ones to come listed; a check that fails prints its last lines above, and the summary names the checks it left
unrun. Every line the checks print goes to `.logs/<stamp>-lint.log`. Piped or in CI, or with a `-v` in
`ANSIBLE_ARGS`, each check runs as before ([During a run](../guides/runs.md)).

Run Molecule through `just test` (or with the virtualenv activated): Molecule picks `ansible-playbook` from
`PATH`, and a system-wide Ansible would silently be used instead of the pinned one.

Molecule scenarios use `geerlingguy/docker-ubuntu2404-ansible` (pinned by digest) with systemd, and share
`.config/molecule/config.yml`. Everything that would need a hypervisor or a third-party account is faked
so the tests are deterministic: what each scenario proves is in
[§11.2](../architecture/testing.md#112-role-scenarios--just-test-role) and [§11.3](../architecture/testing.md#113-playbook-scenarios--just-test-integration-and-provision), and every
fake and stand-in in [§11.4](../architecture/testing.md#114-the-fakes--testsmolecule). The scenarios that
install packages download them, so they need internet access
([§11.6](../architecture/testing.md#116-which-scenarios-need-the-network)).

Not covered by Molecule: the npm install method for Claude Code, reinstalling on an exact-version bump,
and `common_user_passwordless_sudo: false`. `just vagrant` exercises `configure.yml` on a real VM
including the Docker service; qemu-guest-agent stays stopped there, since VirtualBox gives the VM no
port for it.

## Continuous integration

`.github/workflows/ci.yml` runs lint and the unit tests, every Molecule scenario, a dependency review
and, weekly, a canary against what the project installs from outside the repository. What each job
does is in [§12](../architecture/ci.md). No job needs a secret, so the full suite runs on pull requests
from forks; keep it that way.
