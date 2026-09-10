# Contributing

Thanks for taking a look. This project provisions a Proxmox VM and configures it for development with
Claude Code. Anything environment-specific - a Proxmox address, a GitHub username, a token - belongs in a
git-ignored file, never in a commit.

## Setup

You need Python 3.12+ (the pinned ansible-core requires it), `make`, and Docker Engine for the tests.

```bash
make init     # virtualenv, Galaxy collections, config from the .example files, git hooks
make test     # lint, syntax, unit tests, every Molecule scenario
```

`make init` copies `inventory/hosts.yml.example` and the two `group_vars` examples into place and installs
the pre-commit hooks. All tooling lives in `.venv` at the versions pinned in `requirements.txt`, so please
run it through `make` rather than from a system-wide install - Molecule in particular resolves
`ansible-playbook` from `PATH` and will silently use a different ansible-core if the virtualenv is not
first.

## Testing a change

| Command | What it runs |
|---------|--------------|
| `make lint` | yamllint, ansible-lint (production profile), ruff |
| `make syntax` | `--syntax-check` on every playbook, against the example inventory |
| `make unit` | pytest for `roles/github_projects/library/github_repos.py` |
| `make molecule MOLECULE_ROLES="common dev_tools"` | one or more role scenarios |
| `make molecule-integration` | `playbooks/configure.yml` end to end in a container |
| `make molecule-provision` | `provision.yml` + `discover.yml` against a fake Proxmox API |
| `make vagrant-up` | `configure.yml` on a real VirtualBox VM |

Every role has a Molecule scenario, and a change to a role should keep its scenario passing - including the
idempotence stage, which reruns the converge and fails on any changed task.

Tests never talk to a real Proxmox, a real GitHub or a real hypervisor. `tests/molecule/` holds fakes for
all of them: a stateful Proxmox API (including the guest agent), a stateful `qm`, a `virt-customize`, and
a GitHub API serving local bare repositories. If
your change needs a new external interaction, extend a fake rather than reaching for the network.

## Conventions

- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/): `feat(role): ...`,
  `fix: ...`, `docs: ...`, `test: ...`, `ci: ...`, `build: ...`, `chore: ...`. Write the message body to
  explain *why*; the diff already shows what.
- **Role variables** are prefixed with the role name (`common_user`, `dev_tools_node_major`). ansible-lint
  enforces this. Every variable needs a default in `defaults/main.yml` and an entry in
  `meta/argument_specs.yml`, with `no_log: true` if it can hold a secret.
- **Task names** in an included task file are prefixed with the file name: `- name: node | Install Node.js`.
- **User-facing variables** are wired in `inventory/group_vars/all/defaults.yml` and documented in the
  README table, so a user sets `github_username` rather than `github_projects_username`.
- **Idempotence** is not optional. A task that reports `changed` on a second run will fail CI.

## Adding a role

1. `roles/<name>/` with `defaults/main.yml`, `meta/main.yml`, `meta/argument_specs.yml` and `tasks/`.
2. A Molecule scenario at `roles/<name>/molecule/default/` - it inherits the driver and platform from
   `.config/molecule/config.yml`, so it only needs what differs.
3. Add the role to `ROLES` in the `Makefile` and to the matrix in `.github/workflows/ci.yml`. Renaming a
   CI job also means updating the required status checks on `main`, or pull requests can never merge.
4. Add it to the role table in the README.

## Pull requests

CI runs lint, unit tests, every Molecule scenario and the integration scenario on your PR, plus a
dependency review. No job uses a secret, so the full suite runs on pull requests from forks - please keep
it that way and do not add a job that needs one.

Before opening a PR, run `make test` locally, and check that nothing environment-specific slipped in: CI
fails if a `hosts.yml`, `local.yml`, `vault.yml` or `.vault_pass` is ever committed.
