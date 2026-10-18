# 13. Toolchain

Everything is pinned in `pyproject.toml`, locked with every package it pulls in (version and hash) in
`uv.lock`, and lives in `.venv`. Use `make`, or `.venv/bin/<tool>`. `make venv` is `uv sync --locked`,
which refuses a lock that no longer matches `pyproject.toml`; Dependabot's uv ecosystem moves the pins
and the lock. The playbooks' own pins are `[project]`'s dependencies, the linters' and tests' the `dev`
group, and the wiki's the `docs` group; `make venv` installs all three. The versions are the ones
`pyproject.toml` pins - this page names what each tool is for, so that a bump cannot leave it stale:

| Tool | For |
|---|---|
| ansible-core | the playbooks and roles |
| ansible-lint, yamllint | `make lint`, with ruff |
| ruff | Python lint and format |
| molecule, with molecule-plugins' Docker driver and the `docker` package | the role and playbook scenarios |
| pytest | `make unit` |
| pre-commit | the hooks run on commit, and the file checks CI runs with them |
| proxmoxer, requests | the `community.proxmox` collection, on the controller |
| docsible | `make docs`: each role's reference page - its variables, internal values, tasks and a flowchart of each task file |
| Zensical | the wiki in `docs/` built as a site (`make docs-serve`, `make docs-build`) |
| Markdown, pymdown-extensions | the site's rendering and its heading anchors, kept the ones GitHub makes |
| markdown-it-py | the pages rendered as GitHub does, for the test that compares the two |
| uv | `.venv` (`make venv`) and the lock (`make lock`); the one tool on `PATH` by design, and pinned in the `dev` group for CI |

Collections: `community.proxmox`, `community.general`, `community.docker` and `ansible.posix`, pinned
to exact versions in `requirements.yml`.

> **Molecule resolves `ansible-playbook` from `PATH` and only appends the virtualenv**, so `.venv/bin`
> must be **prepended** or a different ansible-core is silently used. The `molecule` targets do this;
> a hand-rolled `molecule test` does not.

`ansible.cfg` sets the directory inventory, `roles_path`, the local `filter_plugins`, YAML callback
output, `profile_tasks`, `forks = 20`, pipelining, and `StrictHostKeyChecking=accept-new`. There is
deliberately no fact cache: only `dev_tools` reads a gathered fact, the one gather per configure run costs
seconds in a run that takes minutes, and a cached entry keyed by inventory name outlived the VM it
described, so a VM rebuilt under the same name within the hour was configured with the old VM's
`ansible_distribution_release` and `ansible_architecture`. `forks` is the number of VMs cloned,
booted or configured at once - every `proxmox_vm` task is a localhost-delegated API call, and
`configure` is SSH to the fleet - so it is set above the default and `ANSIBLE_ARGS="-f N"` raises
it for a larger fleet. Setting `inventory_ignore_extensions` *replaces* Ansible's
default ignore list rather than extending it, so the value in `ansible.cfg` re-lists the defaults
worth keeping and adds `.example`; drop one and a stray `hosts.yml.bak` would be parsed as inventory.

Linting is the ansible-lint **production** profile with `args`, `empty-string-compare`,
`no-log-password`, `no-same-owner`, `name[prefix]` and `yaml` additionally enabled.
