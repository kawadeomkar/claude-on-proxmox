# CLAUDE.md

Ansible that creates Ubuntu VMs on a Proxmox VE host and configures them as Claude Code
development boxes. `README.md` is the user manual and `CONTRIBUTING.md` has the setup and
conventions — this file is the part that is easy to get wrong.

## Always run tooling through the virtualenv

Every tool is pinned in `requirements.txt` and lives in `.venv/bin`. Use `make`, or call
`.venv/bin/<tool>` directly. Never a system-wide `ansible`, `ansible-lint` or `molecule`.

Molecule resolves `ansible-playbook` from `PATH` and only *appends* the virtualenv, so it must be
**prepended** or a different ansible-core is silently used. The `molecule` targets already do this;
a hand-rolled `molecule test` will not.

```
make lint            # yamllint --strict, ansible-lint (production), ruff check + format
make syntax          # --syntax-check on site.yml and playbooks/*
make unit            # pytest, tests/unit/
make molecule MOLECULE_ROLES="proxmox_vm"      # one role scenario
make molecule-scenario SCENARIO=provision      # one playbook scenario
make test            # everything, including pre-commit — the CI gate
make claude-login    # print the one Remote Control step Ansible cannot do
```

Scenarios are slow and disk-hungry. Run the ones your change touches, one at a time, rather than
`make test`, until you are ready to finish.

`common`, `dev_tools`, `claude_code`, `github_projects` and the `configure` scenario install
packages inside the container and need working Docker DNS. `proxmox_vm`, `proxmox_template` and
the `provision` scenario talk only to local fakes, so they still run when the network is down —
if apt fails to resolve a mirror, that is the machine, not the change.

## Invariants — do not break these

- **No addresses in git.** The Proxmox host's address is the only one anyone supplies, and it lives
  in git-ignored `inventory/hosts.yml`. VM addresses are always DHCP, read back from the QEMU guest
  agent. Never add a VM to a committed inventory, and never hardcode a VM address in a default.
- **No secrets in git.** Secrets go in `inventory/group_vars/all/vault.yml` (ansible-vault). CI runs
  gitleaks plus `tests/check_no_local_files.sh`, which blocks `hosts.yml`, `local.yml`, `vault.yml`,
  `.vault_pass`, `host_vars/` and friends. `tests/unit/test_tracked_files.py` tests that guard both
  ways — extend it when you change the patterns.
- **The ownership tag is the safety interlock.** Every VM this project creates carries
  `claude-on-proxmox` (`claude_vm_tag`). Discovery finds VMs by it and deletion *refuses* a VM
  without it. It is matched as a whole tag via `claude_vm_tag_pattern`, never as a substring.
- **Idempotence.** Molecule's idempotence stage re-runs converge and fails on any `changed` task.
- **An API key and Remote Control are mutually exclusive.** `ANTHROPIC_API_KEY` outranks the
  claude.ai login in Claude Code's credential precedence, and only that login can establish a
  Remote Control session. Never configure both; `roles/claude_code/tasks/remote_control.yml`
  asserts it. See ARCHITECTURE.md §5.5.
- **Tests never touch a real Proxmox, GitHub or hypervisor.** `tests/molecule/` holds the fakes:
  a stateful PVE API with a guest agent, a stateful `qm`, a `virt-customize`, and a GitHub API over
  local bare repos. Extend a fake rather than reaching for the network.

## Architecture, and why it looks like this

**Playbooks own inventory; roles do not.** `add_host` sets `BYPASS_HOST_LOOP`, so it runs *once per
task* no matter how many hosts the play has. A copy inside a role publishes only the first VM of a
fleet. `provision.yml` therefore ends with a third play that loops `add_host` from localhost over
the facts the role left behind. Do not move it back into `proxmox_vm`.

**`provision.yml` is three plays on purpose.** Most of the per-VM time is a multi-minute wait for
the guest agent, and every VM boots concurrently on the hypervisor. Play 1 turns each name into a
placeholder host, play 2 runs the role across them so Ansible fans out over `forks`, play 3
publishes. A loop over `include_role` would serialise the waiting.

**The clone is `throttle: 1`.** Proxmox has no atomic VMID reservation — `proxmox_kvm` asks
`/cluster/nextid` and then clones, so two concurrent clones get the same ID and one fails. Only the
clone is throttled; everything after it still runs in parallel. On a VMID collision `proxmox_kvm`
returns `changed=False` and *the clone source's* VMID, so the role asserts `is changed` rather than
trusting the returned ID — without that, every later task reconfigures the template.

**`roles/proxmox_vm/vars/main.yml` holds lazily-evaluated internals** that read registers set by
tasks (`proxmox_vm_lookup`, `proxmox_vm_config`, `proxmox_vm_net`). They are vars, not facts, on
purpose: `destroy.yml` runs the role in a loop, and a `set_fact` survives an iteration that skips
it, so a fact would hand VM #1's MAC to VM #2. A skipped register is overwritten; a skipped
`set_fact` is not. Do not convert these to `set_fact`. The same pattern gives
`claude_vm_tagged` in `inventory/group_vars/all/defaults.yml`, which needs `claude_vm_all`
registered by whichever play uses it.

**Existence is not convergence.** A run that died between the clone and the settings call leaves a
VM with no cloud-init user, no keys, no agent and no tag. `proxmox_vm_needs_configuration` keys off
the *tag*, written by that same settings call, so a half-built VM is repaired instead of skipped.

**Discovery is a playbook, not the `community.proxmox` inventory plugin.** The header of
`discover.yml` records the trade-offs; the swap is a live design question, not an oversight.

## Things that have bitten this repo

- **ansible-core 2.21**: a non-boolean conditional is a fatal error, not a warning.
- **`-e key=value` splits extra-vars on whitespace.** `VM_NAME="alpha beta"` silently provisioned
  half the fleet. The Makefile passes JSON (`-e '{"vm_name": "..."}'`) to keep the value whole.
- **Proxmox tags** arrive `;`-joined and lowercased on the wire, but `proxmox_kvm` *sends* them
  comma-joined. Anything parsing tags must handle both.
- **`vars_prompt` needs a `default:`**, or a run with no terminal hangs. The default must be the
  safe answer (`destroy.yml` defaults to `no`).
- **There is deliberately no `LIMIT`.** `--limit` is applied before the plays run, and these VMs
  only enter the inventory once discovery has found them, so it could never match.
- **`gitleaks --staged` scans the index**, which equals HEAD in a CI checkout — i.e. nothing. CI
  scans the tree, scoped by `.gitleaks.toml`.

## Conventions

`CONTRIBUTING.md` is authoritative. The ones ansible-lint's production profile enforces and that
catch people out: role variables must be prefixed with the role name (`var-naming[no-role-prefix]`,
including vars set inside a role's own Molecule scenario), task names in an included file are
prefixed with the file name (`- name: node | Install Node.js`), and every role variable needs both
a `defaults/main.yml` entry and a `meta/argument_specs.yml` entry, with `no_log: true` if it can
hold a secret. Commits follow Conventional Commits; the body explains *why*.
