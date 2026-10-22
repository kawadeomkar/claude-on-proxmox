# Contributing

Thanks for taking a look. This project provisions a Proxmox VM and configures it for development with
Claude Code. Anything environment-specific - a Proxmox address, a GitHub username, a token - belongs in a
git-ignored file, never in a commit.

## Setup

You need Python 3.12+ (the pinned ansible-core requires it) in a virtualenv of its own for this project,
[uv](https://docs.astral.sh/uv/getting-started/installation/) 0.12 or newer (`brew install uv`, or its
standalone installer), `make`, and Docker Engine for the tests.

```bash
pyenv virtualenv 3.12 claude-on-proxmox && pyenv local claude-on-proxmox   # or any virtualenv: PYTHON=<its python>
make init     # make install (the locked packages, the Galaxy collections), config from the .example files, git hooks
make test     # lint (syntax and hooks included), unit tests, every Molecule scenario
```

`make init` runs `make install`, then copies `inventory/hosts.yml.example` and the two `group_vars`
examples into place where they are missing and installs the pre-commit hooks. `make install` puts the
versions locked in `uv.lock` into your environment - the Python `python3` is here, which pyenv takes
from `.python-version`, or `PYTHON=/path/to/python` - leaving the rest of it alone, and refuses a Python
that is not a virtualenv; it changes nothing that is current, so it is also the command to run after a
pull. The project keeps no `.venv`. Please run the tools through `make`, or `tools/env-run <tool>` as the
git hooks do, which run them from that environment rather than whatever is first on `PATH`, and refuse
a Python that is not a virtualenv - Molecule in particular resolves `ansible-playbook` from `PATH` and
will silently use a different ansible-core if the environment is not first.

The pins live in `pyproject.toml`: the playbooks' own in `[project]`, the linters' and tests' in the
`dev` group. After changing one, run `make install TYPE=lock` and commit `uv.lock` with it; `make lint` fails on a
lock that no longer matches. `uv lock --upgrade-package <name>` moves one locked package without
touching a pin. Dependabot proposes the weekly updates itself.

## Testing a change

| Command | What it runs |
|---------|--------------|
| `make lint` | every linter: `uv lock --check`, yamllint, ansible-lint (production profile), ruff, `--syntax-check` on every playbook against your `inventory/`, and the pre-commit hooks those do not cover. `TYPE=yaml`, `ansible`, `python`, `syntax`... runs one |
| `make test` | `make lint`, then everything below but Vagrant: the local quality gate |
| `make test TYPE=unit` | pytest: the custom filters (the VS Code settings reader, the sign-ins' page opener and `plain_text` among them), the `github_repos` module, the tracked-file guard, configure tags, the Remote Control playbooks, the Claude sign-in (`claude_login.yml` against a stand-in `claude`, and at a pseudo-terminal: the prompt, a partly copied code, an expired attempt, Ctrl-C then A), the three sign-in playbooks and deploy's sign-in steps end to end, an unreachable VM among them, Tailscale (the role's files, `tailscale_login.yml` and deploy's Tailscale step against a stand-in `tailscale`, check mode, `configure.yml`'s rescue, and the guards that keep every test setup off Tailscale), the GitHub sign-in (`auth.yml`, `clone.yml` and `github_login.yml` against a stand-in `gh` and the fake GitHub API), the managed SSH config (and that no scenario writes into `~/.ssh`), the Claude terminal's script (once at a pseudo-terminal), `code.yml`'s window step in `make deploy` and out of it, and its allowlist, and the Makefile's guards (`vm-name-check`, the JSON `VM_NAME`, the checked `TYPE`, `TAGS` and `ROLE`, `code`'s one name) |
| `make test ROLE=common,dev_tools` | one or more role scenarios (`TYPE=role` for all of them, `TYPE=molecule` for every scenario) |
| `make test TYPE=integration` | `playbooks/configure.yml` end to end in a container |
| `make test TYPE=provision` | `provision.yml` + `discover.yml` against a fake Proxmox API |
| `make vagrant` | `configure.yml` on a real VirtualBox VM |

Every run of these through `make` that uses Ansible, Molecule included, is also written to `.logs/`
(`make logs` lists them), which is the place to read a failure back once the terminal has scrolled.
The directory is git-ignored and blocked by the tracked-file guard: logs name hosts and addresses.

Every role has a Molecule scenario, and a change to a role should keep its scenario passing - including the
idempotence stage, which reruns the converge and fails on any changed task.

The unit tests that run a playbook share `tests/unit/harness.py`: `_run`, or `_run_at_a_terminal` to type
at a prompt. Use it rather than a `subprocess` call of your own: a run inherits only the variables it
names, never reads your terminal, and fails its test if it calls a browser opener, a clipboard or this
machine's `tailscale`.

Tests never talk to a real Proxmox, a real GitHub, a real Tailscale or a real hypervisor:
`tests/molecule/` holds a fake or a stand-in for each, listed in
[docs/architecture/testing.md](docs/architecture/testing.md#114-the-fakes--testsmolecule). If your change
needs a new external interaction, extend a fake rather than reaching for the network.

Secrets in tests are canned and searched for. `tests/unit/test_github_auth.py` holds every task that
renders a GitHub token to `no_log` (`test_every_task_that_renders_a_token_is_no_log`) and greps every
run's output, module arguments injected, for every canned token (`test_no_token_ever_reaches_the_output`);
`tests/unit/test_claude_login.py` does the same for the Claude sign-in's code
(`test_the_code_is_never_logged`, and the runs at a pseudo-terminal, at `-vvv` with module arguments injected);
`tests/unit/test_tailscale.py` holds the Tailscale join to be the only task that renders the auth key, and
searches every run and every file for every canned key; `tests/unit/test_release.py` searches destroy's
release for every canned GitHub token and Tailscale API credential. A task that handles a new
secret gets a canned value and a place in those searches.

## Conventions

- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/): `feat(role): ...`,
  `fix: ...`, `docs: ...`, `test: ...`, `ci: ...`, `build: ...`, `chore: ...`. Write the message body to
  explain *why*; the diff already shows what.
- **Role variables** are prefixed with the role name (`common_user`, `dev_tools_node_major`). ansible-lint
  enforces this. Every variable needs a default in `defaults/main.yml` and an entry in
  `meta/argument_specs.yml`, with `no_log: true` if it can hold a secret.
- **Task names** in an included task file are prefixed with the file name: `- name: node | Install Node.js`.
- **User-facing variables** are wired in `inventory/group_vars/all/defaults.yml` and documented in
  `local.yml.example` (or `vault.yml.example`), so a user sets `github_username` rather than
  `github_projects_username`. The comment above each one is its entry in the wiki's settings
  reference.
- **Documentation** lives in the wiki in `docs/`, and changes in the same pull request as the
  behaviour it describes. Its reference pages are generated: after changing a role's
  `argument_specs.yml`, `defaults/main.yml`, `vars/main.yml` or task files,
  `inventory/group_vars/all/defaults.yml` (it and the roles' `defaults/main.yml` feed the settings
  page's Default column), `local.yml.example`, `vault.yml.example` or a Makefile target's `## ` text,
  run `make docs` and commit the result
  ([docs/development/docs.md](docs/development/docs.md)).
- **Idempotence** is not optional. A task that reports `changed` on a second run will fail CI.
- **A moment the run waits for the person is set apart.** A `pause` or `vars_prompt` passes its text
  through the `banner` filter (`'…' | banner('USER INPUT REQUIRED', host=inventory_hostname,
  topic='…') ~ 'Code'`), and a debug task that shows a page to approve, or a retried task that waits on
  one, carries `tags: [attention_browser]`. `tests/unit/test_attention_callback.py` lists both.
- **A report that says what to do after the run records it for NEXT STEPS.** Register its message and
  follow it with `ansible.builtin.set_stats` under the owner's `*_next` stat - the message while something
  is left to do, `''` once nothing is - decided from the facts the message is built from, never from its
  text ([docs/architecture/custom-code.md](docs/architecture/custom-code.md) §7.5). The same test fails on a report without its record.

## Adding a role

1. `roles/<name>/` with `defaults/main.yml`, `meta/main.yml`, `meta/argument_specs.yml` and `tasks/`.
2. A Molecule scenario at `roles/<name>/molecule/default/` - it inherits the driver and platform from
   `.config/molecule/config.yml`, so it only needs what differs.
3. Add the role to `ROLES` in the `Makefile` and to the matrix in `.github/workflows/ci.yml`. Renaming a
   CI job also means updating the required status checks on `main`, or pull requests can never merge.
4. Add it to the role table in `docs/reference/roles/README.md` and to the `nav` in `zensical.toml`,
   and run `make docs`, which writes its reference page from its files.

## Pull requests

CI runs lint, unit tests, every Molecule scenario and the integration scenario on your PR, plus a
dependency review. No job uses a secret, so the full suite runs on pull requests from forks - please keep
it that way and do not add a job that needs one.

Before opening a PR, run `make test` locally, and check that nothing environment-specific slipped in: CI
fails if a `hosts.yml`, `local.yml`, `vault.yml` or `.vault_pass` is ever committed.

**PR titles** start with a bracketed tag that says what kind of change the PR is, then a short
imperative summary: `[feat] create VMs by name, find them by tag, and let DHCP address them`,
`[fix] ...`, `[cleanup] ...`, `[docs] ...`, `[test] ...`, `[ci] ...`, `[build] ...`. The tag is
the same vocabulary as the commit types, in brackets rather than with a colon, so the PR list reads
as a changelog; pick one tag for the PR as a whole, and keep the summary in the same voice as the
commit subjects (what the change does, not what you did).
