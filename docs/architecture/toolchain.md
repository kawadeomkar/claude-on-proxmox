# 13. Toolchain

Everything is pinned in `pyproject.toml`, locked with every package it pulls in (version and hash) in
`uv.lock`, and lives in your own virtualenv for the project: the project keeps none. `make install` runs
`uv sync --locked` into the environment of `$(PYTHON)` (`python3` as the shell finds it in the
checkout, unless `PYTHON=` names another), which refuses a lock that no longer matches
`pyproject.toml`, and `--inexact`, so the versions the lock pins go in and nothing else of the environment
is removed; Dependabot's uv ecosystem moves the pins and the lock. Every target runs the tools from that
environment's `bin`, never from `PATH`, and refuses a Python that is not a virtualenv (`env-check`), whose
scripts directory would be a system-wide one; the git hooks and the Vagrantfile find it the same way,
through `tools/env-run`. The playbooks' own pins are `[project]`'s dependencies, the
linters' and tests' the `dev` group, and the wiki's the `docs` group; `make install` installs all three. The versions are the ones
`pyproject.toml` pins - this page names what each tool is for, so that a bump cannot leave it stale:

| Tool | For |
|---|---|
| ansible-core | the playbooks and roles |
| ansible-lint, yamllint | `make lint`, with ruff |
| ruff | Python lint and format |
| molecule, with molecule-plugins' Docker driver and the `docker` package | the role and playbook scenarios |
| pytest | `make test TYPE=unit` |
| pre-commit | the hooks run on commit, and the file checks CI runs with them |
| proxmoxer, requests | the `community.proxmox` collection, on the controller |
| docsible | `make docs`: each role's reference page - its variables, internal values, tasks and a flowchart of each task file |
| Zensical | the wiki in `docs/` built as a site (`make docs TYPE=serve`, `make docs TYPE=build`) |
| Markdown, pymdown-extensions | the site's rendering and its heading anchors, kept the ones GitHub makes |
| markdown-it-py | the pages rendered as GitHub does, for the test that compares the two |
| uv | installing into your environment (`make install`) and the lock (`make install TYPE=lock`); the one tool on `PATH` by design, and pinned in the `dev` group for CI |

Collections: `community.proxmox`, `community.general`, `community.docker` and `ansible.posix`, pinned
to exact versions in `requirements.yml`.

> **Molecule resolves `ansible-playbook` from `PATH` and only appends the virtualenv**, so the
> environment's `bin` must be **prepended** or a different ansible-core is silently used. `make test`
> does this; a hand-rolled `molecule test` does not.

`ansible.cfg` sets the directory inventory, `roles_path`, the local `filter_plugins`, YAML callback
output, `profile_tasks`, `forks = 20`, pipelining, and `StrictHostKeyChecking=accept-new`. There is
deliberately no fact cache: the roles read only a few gathered facts, the one gather per configure run
costs seconds in a run that takes minutes, and a cached entry keyed by inventory name outlived the VM it
described, so a VM rebuilt under the same name within the hour was configured with the old VM's
`distribution_release` and `architecture`. Gathered facts are read only as `ansible_facts['<name>']`:
`inject_facts_as_vars = false`, set in Molecule's shared config too, stops Ansible copying each to a
top-level `ansible_architecture`, as ansible-core 2.24 will, so a read of one fails now rather than on
that upgrade; `tests/unit/test_gathered_facts.py` scans for one on a path no scenario takes. `forks`
is the number of VMs cloned, booted or configured at once - every `proxmox_vm` task is a
localhost-delegated API call, and `configure` is SSH to the fleet - so it is set above the default and
`ANSIBLE_ARGS="-f N"` raises it for a larger fleet. Pipelining sends each module over stdin, so a
module that transfers no file gets no per-task temporary directory, and the ones `copy` and `template`
make belong to the connecting user, made without become: the first module running as root that needs
one creates root's `remote_tmp` itself and warns that it did - `get_url` building the template on a new
Proxmox host, `deb822_repository` in `dev_tools` on a new VM. So `template.yml` and `configure.yml` make
it first (`playbooks/tasks/remote_tmp.yml`), as that warning asks, at the `remote_tmp` the run is
configured with. Setting `inventory_ignore_extensions` *replaces* Ansible's default ignore list rather
than extending it, so the value in `ansible.cfg` re-lists the defaults worth keeping and adds
`.example`; drop one and a stray `hosts.yml.bak` would be parsed as inventory.

Linting is the ansible-lint **production** profile with `args`, `empty-string-compare`,
`no-log-password`, `no-same-owner`, `name[prefix]` and `yaml` additionally enabled.
