# Contributing

Thanks for taking a look. This project provisions a Proxmox VM and configures it for development with
Claude Code. Anything environment-specific - a Proxmox address, a GitHub username, a token - belongs in a
git-ignored file, never in a commit.

## Setup

You need Python 3.12+ (the pinned ansible-core requires it) in a virtualenv of its own for this project,
[uv](https://docs.astral.sh/uv/getting-started/installation/) 0.12 or newer (`brew install uv`, or its
standalone installer), [just](https://github.com/casey/just) 1.59 or newer (`brew install just`, or `cargo install
just`, from its own releases), and Docker Engine for the tests.

```bash
pyenv virtualenv 3.12 claude-on-proxmox && pyenv local claude-on-proxmox   # or any virtualenv: PYTHON=<its python>
just init     # just install (the locked packages, the Galaxy collections), git hooks, the config, asked for
just test     # lint (syntax and hooks included), unit tests, every Molecule scenario
```

`just init` runs `just install`, installs the pre-commit hooks, copies `inventory/hosts.yml.example`
and the two `group_vars` examples into place where they are missing, and asks for the values a first
deploy needs (`just init --no-prompt` asks nothing; a checkout that only runs the tests needs none).
`just install` puts the versions locked in `uv.lock` into your environment - the Python `python3` is
here (the one you activated, or the one `pyenv local` chose), or `PYTHON=/path/to/python` - leaving the
rest of it alone, and refuses a Python that is not a virtualenv; it changes nothing that is current, so
it is also the command to run after a pull. The project keeps no `.venv`. Please run the tools through
`just`, or `tools/env-run <tool>` as the git hooks do, which run them from that environment rather than
whatever is first on `PATH`, and refuse a Python that is not a virtualenv - Molecule in particular
resolves `ansible-playbook` from `PATH` and will silently use a different ansible-core if the
environment is not first.

The pins live in `pyproject.toml`: the playbooks' own in `[project]`, the linters' and tests' in the
`dev` group. After changing one, run `just install --lock` and commit `uv.lock` with it; `just lint` fails on a
lock that no longer matches. `uv lock --upgrade-package <name>` moves one locked package without
touching a pin. Dependabot proposes the weekly updates itself.

## Testing a change

| Command | What it runs |
|---------|--------------|
| `just lint` | every linter: `uv lock --check`, yamllint, ansible-lint (production profile), ruff, `just --fmt --check`, `--syntax-check` on every playbook against your `inventory/`, and the pre-commit hooks those do not cover. `yaml`, `ansible`, `python`, `just`, `syntax`... runs some |
| `just test` | `just lint`, then everything below but Vagrant: the local quality gate |
| `just test unit` | pytest: the custom filters (the VS Code settings reader, the sign-ins' page opener and `plain_text` among them), the `github_repos` module, the tracked-file guard, configure tags, the Remote Control playbooks, the Claude sign-in (`claude_login.yml` against a stand-in `claude`, and at a pseudo-terminal: the prompt, a partly copied code, an expired attempt, Ctrl-C then A), the three sign-in playbooks and deploy's sign-in steps end to end, an unreachable VM among them, Tailscale (the role's files, `tailscale_login.yml` and deploy's Tailscale step against a stand-in `tailscale`, check mode, `configure.yml`'s rescue, and the guards that keep every test setup off Tailscale), the GitHub sign-in (`auth.yml`, `clone.yml` and `github_login.yml` against a stand-in `gh` and the fake GitHub API), the managed SSH config (and that no scenario writes into `~/.ssh`), the Claude terminal's script (once at a pseudo-terminal), `code.yml`'s window step in `just deploy` and out of it, and its allowlist, the Claude Code session handoff (the plan, the copies, `--session`'s refusals and fast-forward between real checkouts) and `just destroy`'s save of a VM's sessions, and the justfile's guards (every recipe against stand-ins: the names as JSON, the patterns and the rules, the checkout guard, `code`'s one name, `init`'s secret never said) |
| `just test --role common,dev_tools` | one or more role scenarios (`role` for all of them, `molecule` for every scenario) |
| `just test integration` | `playbooks/configure.yml` end to end in a container |
| `just test provision` | `provision.yml` + `discover.yml` against a fake Proxmox API |
| `just vagrant` | `configure.yml` on a real VirtualBox VM |

Every run of these through `just` that uses Ansible, Molecule included, is also written to `.logs/`
(`just logs` lists them), which is the place to read a failure back once the terminal has scrolled.
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
  page's Default column), `local.yml.example`, `vault.yml.example` or a recipe's `[doc]` or `[arg]` help in the `justfile`,
  run `just docs` and commit the result
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
3. Add the role to `scenario_roles` in the `justfile` and to the matrix in `.github/workflows/ci.yml`. Renaming a
   CI job also means updating the required status checks on `main`, or pull requests can never merge.
4. Add it to the role table in `docs/reference/roles/README.md` and to the `nav` in `zensical.toml`,
   and run `just docs`, which writes its reference page from its files.

## Pull requests

CI runs lint, unit tests, every Molecule scenario and the integration scenario on your PR, plus a
dependency review. No job uses a secret, so the full suite runs on pull requests from forks - please keep
it that way and do not add a job that needs one.

Before opening a PR, run `just test` locally, and check that nothing environment-specific slipped in: CI
fails if a `hosts.yml`, `local.yml`, `vault.yml` or `.vault_pass` is ever committed.

**PR titles** start with a bracketed tag that says what kind of change the PR is, then a short
imperative summary: `[feat] create VMs by name, find them by tag, and let DHCP address them`,
`[fix] ...`, `[cleanup] ...`, `[docs] ...`, `[test] ...`, `[ci] ...`, `[build] ...`. The tag is
the same vocabulary as the commit types, in brackets rather than with a colon, so the PR list reads
as a changelog; pick one tag for the PR as a whole, and keep the summary in the same voice as the
commit subjects (what the change does, not what you did). `.github/workflows/labels.yml` labels each PR
from its tag - `enhancement`, `bug`, `cleanup`, `documentation`, `test`, `ci` or `build` - and labels it
again when the title changes, and a release's notes list each PR under its label: the title is what the
release notes say.

## Releasing

A release is an annotated tag on `main`, named `vMAJOR.MINOR.PATCH` by
[Semantic Versioning](https://semver.org). What a version number promises about is the project's interface,
and only that:

- the `just` commands people use - `init`, `install`, `vault`, `deploy`, `status`, `login`, `code`, `destroy`,
  `settings`, `logs` and `help` - and their options;
- the settings `local.yml.example` and `vault.yml.example` list, and the shape of `inventory/hosts.yml`: the
  `proxmox` group, its host and that host's `ansible_host`;
- the role names `just deploy --tags` takes: `common`, `vscode_server`, `dev_tools`, `claude_code`,
  `github_projects` and `tailscale`;
- the settings every recipe takes from the environment or before its name: `ANSIBLE_ARGS`, `LOG_DIR`,
  `LOG_RETENTION_DAYS`, `PYTHON` and `UV`;
- `just status`'s exit codes for a run that reads the fleet - 0 nothing is wrong, 1 a problem, 2 something
  could not be read, 3 the run failed - and the JSON `just status --json` and `just settings --json` print, to
  which a release may add keys but never removes or renames one (`test_status.py` and `test_settings.py` hold
  the keys there are). A command line or a setup that a recipe refuses before it reads anything prints an
  error on standard error and nothing on standard output, `--json` or not, and exits non-zero.

The contributor commands - `lint`, `test`, `docs`, `vagrant` and `clean` - the justfile's other settings, the
role variables the example files do not list, and the wording of what any command prints are not part of it,
and may change in any release. A release that removes or renames part of the interface, or changes what an
existing setup does, takes a new major version (a new minor one while the version is `0.y.z`); one that only
adds, a minor version; one that only fixes, a patch. A hyphen makes a prerelease: `v1.0.0-rc.1`.

Once CI has passed on the `main` commit you are releasing, tag it and push the tag:

```bash
git switch main && git pull --ff-only
git tag -a v0.2.0 -m "A summary of the release, above its notes"
git push origin v0.2.0
```

`.github/workflows/release.yml` then publishes the release: the tag's message first, then the PRs merged
since the previous release, in the sections `.github/release.yml` makes of their labels. It refuses a
lightweight tag, a name that is not a version, and a commit `main` does not have, before it publishes
anything; delete such a tag (`git push origin :refs/tags/<tag>`, then `git tag -d <tag>`) and tag again.
