# Testing

What the suite runs, and how to run it. How it is built - the harness, each scenario, the fakes - is
[Architecture § 11](../architecture/testing.md); the workflow around a change is in
[CONTRIBUTING.md](https://github.com/kawadeomkar/claude-on-proxmox/blob/main/CONTRIBUTING.md).

```bash
just lint                     # every linter: the lock, yamllint, ansible-lint (production), ruff, just --fmt --check, --syntax-check, the other pre-commit hooks
just lint syntax              # one of them: lock, yaml, ansible, python, just, syntax or hooks
just test                     # just lint, then everything below but the last two: the local quality gate
just test unit                # pytest over tests/unit/, a worker per CPU (plain pytest: one test at a time), no network
just test unit --shard 2/6    # one of six shards of those, as CI splits them: the tests that share a run together
just test molecule            # every Molecule (Docker) scenario: the three below
just test molecule --jobs 4   # the same, four at a time: each in a log of its own (needs the disk and memory for four)
just test role                # each role's scenario, incl. idempotence; --role common,dev_tools for some of them
just test integration         # playbooks/configure.yml end to end in a container
just test provision           # provision.yml + discover.yml against a fake Proxmox API
MOLECULE_PROVISION_VERIFY=verify_code.yml just test provision   # one half of it, as one of CI's two jobs
just deploy --check           # dry run of configure.yml against your real VM (--check --diff)
just vagrant                  # configure.yml on a real VirtualBox VM (just vagrant destroy removes it)
```

What each unit test file holds is in [§11.1](../architecture/testing.md#111-unit--just-test-unit), and how
CI splits them into shards beside it.

At a terminal, `just lint` is drawn live, a row for each check, the running one's last line beside it and the
ones to come listed; a check that fails prints its last lines above, and the summary names the checks it left
unrun. Every line the checks print goes to `.logs/<stamp>-lint.log`. `just test` is drawn the same way, as one run:
lint as its first stage, then the unit tests - a count once pytest has collected them, the tests running now, each
failure's words above the moment it fails - then each Molecule scenario a row, its actions beneath, a failed
action's task and Ansible's words above as Molecule goes on through its cleanup, and why it failed once Molecule
says so at the end; the summary lists what failed by name, and the stages a failure left unrun. Piped or in CI, or with a `-v` in `ANSIBLE_ARGS`, each part runs as before
([During a run](../guides/runs.md)).

Run Molecule through `just test`. Molecule picks `ansible-playbook` from `PATH` and only appends the project's
environment, so a system-wide Ansible would silently be used instead of the pinned one. By hand, put the
environment's `bin` first: `PATH="$(tools/env-run --bin):$PATH" tools/env-run molecule test`
([§13](../architecture/toolchain.md)).

Molecule scenarios use `geerlingguy/docker-ubuntu2404-ansible` (pinned by digest) with systemd, and share
`.config/molecule/config.yml`. Everything that would need a hypervisor or a third-party account is faked
so the tests are deterministic: what each scenario proves is in
[§11.2](../architecture/testing.md#112-role-scenarios--just-test-role) and [§11.3](../architecture/testing.md#113-playbook-scenarios--just-test-integration-and-provision), and every
fake and stand-in in [§11.4](../architecture/testing.md#114-the-fakes--testsmolecule). The scenarios that
install packages download them, so they need internet access
([§11.6](../architecture/testing.md#116-which-scenarios-need-the-network)). They also run what they
download as root - the real installers and apt repositories the roles use - inside containers that are
`privileged: true` with the host's cgroups, which systemd in them needs. A privileged container is no
boundary, so an upstream that served something malicious would reach the machine running Docker - on
macOS, Docker Desktop's VM, which mounts your home folder. Run `just test` in full on a throwaway VM rather
than on the machine that holds your vault and keys.

Not covered by Molecule: the npm install method for Claude Code, reinstalling on an exact-version bump,
and `common_user_passwordless_sudo: false`. `just vagrant` exercises `configure.yml` on a real VM
including the Docker service; qemu-guest-agent stays stopped there, since VirtualBox gives the VM no
port for it.

## Continuous integration

`.github/workflows/ci.yml` runs lint and the unit tests, every Molecule scenario, a dependency review
and, weekly, a canary against what the project installs from outside the repository. What each job
does is in [§12](../architecture/ci.md). No job needs a secret, so the full suite runs on pull requests
from forks; keep it that way.
