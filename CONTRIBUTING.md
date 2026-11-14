# Contributing

Thanks for taking a look. This project provisions a Proxmox VM and configures it for development with
Claude Code. Anything environment-specific - a Proxmox address, a GitHub username, a token - belongs in a
git-ignored file, never in a commit.

## Setup

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13.4 or newer (`brew install uv`, or its
standalone installer), [just](https://github.com/casey/just) 1.59 or newer (`brew install just`, or `cargo install
just`, from its own releases), and Docker Engine for the tests. Python comes with uv, or bring a virtualenv of
Python 3.12+ of your own (`PYTHON=`).

```bash
just init     # just install (the locked packages, the Galaxy collections), git hooks, the config, asked for
just test     # lint (syntax and hooks included), unit tests, every Molecule scenario
```

`just init` runs `just install`, installs the git hook (`tools/git-hook`), copies the example settings into place
where they are missing, and asks for the values a first deploy needs (`just init --no-prompt` asks nothing; a
checkout that only runs the tests needs none). `just install` puts the versions `uv.lock` locks into the project's
environment - a virtualenv of yours (`PYTHON=`, or `python3` here when it is one), or else the checkout's own,
outside the checkout - and the Galaxy collections into the checkout's own folder; run it again after a pull. Run
every tool through `just`, or `tools/env-run <tool>` as the git hooks do, never from `PATH`. How the environment is
chosen and where it lives: [Toolchain](docs/architecture/toolchain.md).

The pins live in `pyproject.toml`: the playbooks' own in `[project]`, the linters' and tests' in the
`dev` group. After changing one, run `just install --lock` and commit `uv.lock` with it; `just lint` fails on a
lock that no longer matches. `uv lock --upgrade-package <name>` moves one locked package without
touching a pin. Dependabot proposes the weekly updates itself.

## Testing a change

`just lint` runs every linter, and `just test` runs it, then the unit tests and every Molecule scenario: the
gate CI runs. Each takes a part - `just lint python`, `just test unit`, `just test --role common` - and
[Testing](docs/development/testing.md) lists them all, with what each runs. Every run of these that uses
Ansible is also written to `.logs/` (`just logs` lists them), git-ignored and refused by the tracked-file
guard, since logs name hosts and addresses.

Every role has a Molecule scenario, and a change to a role should keep its scenario passing - including the
idempotence stage, which reruns the converge and fails on any changed task.

The unit tests that run a playbook share `tests/unit/harness.py`: `_run`, or `_run_at_a_terminal` to type
at a prompt. Use it rather than a `subprocess` call of your own: a run inherits only the variables it
names, never reads your terminal, and fails its test if it calls a browser opener, a clipboard or this
machine's `tailscale`, `gh` or `claude`.

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
  `meta/argument_specs.yml`, with `no_log: true` if it can hold a secret. `defaults/main.yml` is the one place
  a default is written: the entry carries no `default:`, which Ansible never applies and the role's page fills
  from `defaults/main.yml`, unless the default there is an expression, when it says what that falls back to.
- **Task names** in an included task file are prefixed with the file name: `- name: node | Install Node.js`.
- **The controller's Python** goes in `lib/claude_vm/`, the one package: a file in `filter_plugins/` is a thin
  adapter whose filters import their core module only when called (`plugin.filters`); nothing outside
  `claude_vm/ansible/` imports `ansible`, and no module imports `yaml`, `asyncio` or `subprocess` when it is
  imported; a record is a frozen `Checked` dataclass and leaves the core only through `to_wire`. What runs on a VM
  (`playbooks/library/`, `playbooks/module_utils/`, `playbooks/files/`) never imports it and is handed data
  instead; what the modules in `playbooks/library/` share goes in `playbooks/module_utils/agent_warehouse.py`,
  standard library only, which Ansible sends with each
  ([docs/architecture/custom-code.md](docs/architecture/custom-code.md) §7.10). `tests/unit/test_claude_vm.py`
  holds these.
- **User-facing variables** are wired in `inventory/group_vars/all/defaults.yml` and documented in
  `local.yml.example` (or `vault.yml.example`), so a user sets `github_username` rather than
  `github_projects_username`. The comment above each one is its entry in the wiki's settings
  reference.
- **Documentation** lives in the wiki in `docs/`, and changes in the same pull request as the
  behaviour it describes. Its reference pages are generated: edit their sources - which
  [docs/development/docs.md](docs/development/docs.md) lists - and run `just docs`, which the commit hook
  runs too.
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
2. A Molecule scenario at `roles/<name>/molecule/default/` - it inherits the driver and the provisioner's
   settings from `.config/molecule/config.yml`, and names its own platform, copied from another role's
   `molecule.yml` with the container named after the role: Molecule replaces a list rather than merging it.
3. Add the role to the `justfile` - to `configure_roles` when `configure.yml` runs it, which `scenario_roles`
   takes in, and otherwise to `scenario_roles` itself - and to the matrix in `.github/workflows/ci.yml`. The one
   required check is `CI result`, which needs every job, so a job added or renamed changes no setting
   ([CI](docs/architecture/ci.md)).
4. Add it to the role table in `docs/reference/roles/README.md` and to the `nav` in `zensical.toml`,
   and run `just docs`, which writes its reference page from its files.

## Adding a member to a choice

The editor and the VPN are choices: a setting naming one row of a list in the inventory defaults
(`docs/architecture/custom-code.md` §7.11). A new editor or VPN is:

1. Its row, literal values only, in `claude_editors` or `claude_vpns`, and its value in the setting's description in
   `inventory/group_vars/all/defaults.yml` and `local.yml.example`.
2. Its code: an editor's check file (`playbooks/tasks/<editor>_check.yml`) and its family's plan
   (`lib/claude_vm/editors/<family>.py`); a VPN's role (Adding a role, above), imported in `playbooks/configure.yml`
   with a literal name and its `when`, its `status` and `release` entry points beside the others'.
3. Its cases in the choice's conformance tests (`tests/unit/test_editor_conformance.py`, `tests/unit/test_choices.py`),
   and its guide in `docs/guides/`.
4. `just docs`, and the guide in `docs/README.md` and `zensical.toml`.

## Pull requests

CI runs lint, unit tests, every Molecule scenario and the integration scenario on your PR, plus a
dependency review. No job uses a secret, so the full suite runs on pull requests from forks - please keep
it that way and do not add a job that needs one.

Before opening a PR, run `just test` - in full on a throwaway VM rather than the machine that holds your vault,
since its scenarios run what they download as root in privileged containers
([Testing](docs/development/testing.md)) - and check that nothing environment-specific slipped in: CI fails if a
`hosts.yml`, `local.yml`, `vault.yml` or `.vault_pass` is ever committed.

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
  `settings`, `logs`, `host`, `mcp` and `help` - and their options;
- the settings `local.yml.example` and `vault.yml.example` list, and the shape of `inventory/hosts.yml`: the
  `proxmox` group, its host and that host's `ansible_host`; and of a host in `hosts/<name>/` beside it (from 2.5):
  its `hosts.yml` of that shape, its `group_vars/all/local.yml` and `vault.yml`, and `hosts/current`;
- the role names `just deploy --tags` takes: `common`, `vscode_server`, `dev_tools`, `claude_code`,
  `github_projects` and `tailscale`;
- the settings every recipe takes from the environment or before its name: `ANSIBLE_ARGS`, `LOG_DIR`,
  `LOG_RETENTION_DAYS`, `PYTHON` and `UV`;
- the path of the helper `just code` copies to a VM for any editor's Claude task, which a task of your own may
  run (`docs/guides/zed.md`): `helpers/claude-start.sh` in just code's own directory there,
  `<projects>/.agent-warehouse/helpers/claude-start.sh` by default, run as `bash <path>` with no arguments (its 1.x
  path, `<projects>/.claude-on-proxmox/helpers/claude-start.sh`, is kept through 2.x as a link to it on every VM
  `just code` moved the directory on);
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
