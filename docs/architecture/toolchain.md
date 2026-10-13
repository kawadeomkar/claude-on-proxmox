# 13. Toolchain

Everything is pinned in `pyproject.toml`, locked with every package it pulls in (version and hash) in
`uv.lock`, and lives in `.venv`. Use `make`, or `.venv/bin/<tool>`. `make venv` is `uv sync --locked`,
which refuses a lock that no longer matches `pyproject.toml`; Dependabot's uv ecosystem moves the pins
and the lock.

Collections: `community.proxmox 2.0.0`, `community.general 12.4.0`, `community.docker 5.3.0`,
`ansible.posix 2.1.0`.

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
