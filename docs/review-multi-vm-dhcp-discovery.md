# Adversarial review — `feat/multi-vm-dhcp-discovery` (PR #6)

Five independent reviewers audited the 9-commit branch against `main` (41 files, +944/−202):
runtime correctness, convention/simplicity, test quality, security/CI, and maintainability.
Findings below are deduplicated and sorted by priority. Claims marked **[verified]** were
reproduced directly against the working tree or the installed collection source.

**Status: all 48 findings are fixed** — 3 P0, 13 P1, 18 P2, 14 P3 — except **P1-7**, an
architectural decision left to the maintainer, and **P3-14** (squashing two fixup commits), which
would require a force-push to the open PR.

Originally: CI was green, yet the branch carried three data-loss or broken-feature blockers that no
test covered — CI passing was itself one of the findings.

## Fixes applied

### P0 — blockers

| ID | Fix |
|---|---|
| P0-1 | Ownership assert refuses `state: absent` on any template or VM lacking `proxmox_vm_tags[0]`. Tags split on `;` and `,` and matched **whole-element**. Escape hatch: `proxmox_vm_allow_untagged_delete`. |
| P0-2 | `present.yml` asserts `proxmox_vm_clone is changed` **before** recording the VMID, so a collision fails loudly instead of returning the template's VMID. |
| P0-3 | `LIMIT` removed from Makefile and README; `VM_NAME` already narrows correctly. A comment records why `--limit` cannot work here. |

### P1 — high

| ID | Fix |
|---|---|
| P1-1 | `claude_vm_tag_pattern` anchors the discovery tag match on both separators with `regex_escape`, so `claude-on-proxmox-staging` and `not-claude-on-proxmox` are no longer claimed. |
| P1-2 | CI scans the **working tree** with a digest-pinned gitleaks image. `.gitleaks.toml` excludes gitignored dirs the tree scan would otherwise reach (`.vagrant`, `.venv`). Proven: a planted GitHub PAT now fails the build; before, it passed. |
| P1-3 | Guard patterns moved to `tests/check_no_local_files.sh` and widened; `.gitignore` matched to them. All six unprotected paths — incl. root `group_vars/all/vault.yml` and `inventory/proxmox.yml` — now covered, with Molecule fixtures and `tox.ini` deliberately not. |
| P1-4 | `claude_vm_discovery` declared in defaults (it was read but defined nowhere) and set `false` in the `Vagrantfile`. `make vagrant-up` works again. |
| P1-5 | Convergence keyed on the ownership tag, not existence: a VM whose first run died mid-configure is now repaired rather than skipped forever. Resize uses the same condition. `force_update` gained the `| bool` it needed to work from the CLI at all. |
| P1-6 | `provision.yml` registers one host per VM and runs the role against that group, restoring fan-out. The clone is `throttle: 1` — see below. |
| P1-7 | The false justification in `discover.yml`'s header is replaced with the real trade-off. **The architectural swap is left as a decision** — see *Still open*. |
| P1-8 | `dhcpcd-base` added alongside `libguestfs-tools`. |
| P1-9 | README gained an *Upgrading an existing install* section: `qm destroy 9000`, then `make template`. |
| P1-10 | `vm_cores`, `vm_memory_mb`, `vm_disk_size`, `vm_nameservers` and `proxmox_vm_ipconfig` documented in `local.yml.example`, stated plainly as fleet-wide. |
| P1-11 | Role table and intro rewritten; every claim re-checked against the code. |
| P1-12 | Fixed with the P0s (`argument_specs` tag default). |
| P1-13 | Provision scenario seeds three VMs discovery must not claim; `proxmox_vm` scenario proves the delete guard refuses an untagged VM and that no `DELETE` reaches the API. |

### P2 — medium

| ID | Fix |
|---|---|
| P2-1 | Landed with P1-5 (`\| bool` on `force_update`). |
| P2-2 | The post-start config read is retried and the `[0]` guarded; an unreadable NIC now names `proxmox_vm_nic`, which also makes a non-`net0` template work. |
| P2-3 | The agent wait breaks immediately on 401/403/permission errors instead of burning 305 s, and the assertion quotes Proxmox's actual response. |
| P2-4 | The host-key cleanup falls back to the address discovery or provisioning recorded, and warns explicitly when it has neither. |
| P2-5 | Documented in `SECURITY.md` alongside the `accept-new` trade-off. |
| P2-6 | `VM_NAME` is passed as JSON, so a value with spaces reaches a name check instead of silently provisioning half the fleet. |
| P2-7 | `destroy.yml` lists the project's VMs and refuses a name it did not create, naming the ones it did; the prompt defaults to `no` for non-interactive runs. |
| P2-8 | `claude_vm_discovery` declared and documented (with P1-4); discovery reports which of the three reasons applies when it finds nothing. |
| P2-9 | Auth and permission failures now fail the play rather than yielding zero hosts and exit 0. |
| P2-10 | Datacenter *User Tag Access* documented in the README. |
| P2-11 | Collections pinned to exact versions (which also makes the CI cache key correct); pre-commit hooks pinned to commit SHAs. |
| P2-12 | One MAC extractor: `absent.yml`'s duplicate regex replaced with `net_mac('')`. |
| P2-13 | A static `proxmox_vm_ipconfig` is now asserted to reach Proxmox verbatim. |
| P2-14 | The dead `'200'` assertion now checks `9001`, the VM converge actually creates. |
| P2-15 | The fake's agent counter is per-VM, so every VM in a fleet exercises the wait loop. |
| P2-16 | New coverage for deleting a nonexistent VM; `destroy.yml`'s existence check and safe prompt default added. |
| P2-17 | Bad-token test added; the fake now answers 401 with an empty body as real pveproxy does. |
| P2-18 | The fake normalises tags the way PVE does — `;`-joined, lowercased, deduped, sorted. |

### P3 — low

| ID | Fix |
|---|---|
| P3-1 | The phantom `inventory/proxmox.yml` references removed from `ansible.cfg` and `hosts.yml.example`. |
| P3-2 | `inventory_ignore_extensions` extends Ansible's defaults instead of replacing them. |
| P3-3 | `make syntax` docs corrected in README and CONTRIBUTING. |
| P3-4 | The catch-all assertion split into four, each naming what is wrong; the name pattern now rejects a trailing dot/hyphen and enforces 63 chars. |
| P3-5 | Discovery refuses duplicated VM names, as the role already did. |
| P3-6 | The conditional `set_fact` in `absent.yml` is unconditional, so nothing leaks between loop iterations. |
| P3-7 | `present \| Start the VM` is gated; `proxmox_vm_start_existing` opts in to booting a VM someone stopped. |
| P3-8 | The default SSH key is read with `errors='ignore'`, so an RSA-only user gets the helpful assertion. |
| P3-9 | `VM.Config.CDROM` dropped from the `pveum` role — nothing uses it. |
| P3-10 | Deleting a VM evicts its cached facts, so a reused name cannot inherit the dead VM's facts. |
| P3-11 | CI calls `make molecule-scenario`, so those targets are exercised; `make test` includes `pre-commit` and is now a superset of the gate. |
| P3-12 | Three vacuous unit tests rewritten to assert values and exercise the branches they name. |
| P3-13 | `guest_ipv4`'s no-MAC contract pinned, including that it can return a docker bridge. |
| P3-14 | **Deferred** — squashing `d0bb894` and `4e427a2` rewrites commits already pushed to PR #6, so it needs a force-push and is your call. |

### Three defects the fixes themselves uncovered

1. **Parallel clones race on `/cluster/nextid`.** Making provisioning parallel produced `Unable to
   clone vm beta` — two clones in flight are handed the same free ID. Proxmox offers no atomic
   reserve, so the clone is `throttle: 1` while the 305 s agent wait stays parallel, which is where
   the time actually goes.
2. **`add_host` bypasses the play's host loop**, running once per task regardless of host count, so
   the role published only the first VM. The playbook now re-publishes all of them from the role's
   facts.
3. **The first version of the P1-13 test passed with the bug reinstated.** The scenario sets
   `vm_name`, so discovery narrows to `alpha,beta` before the tag filter is reached. The filter is
   now also asserted directly against the live VM list, and the test was confirmed to fail when the
   pattern is reverted to a substring match.

**Verification:** yamllint `--strict`, ansible-lint (production), ruff check + format,
`--syntax-check` on 6 playbooks, **87 unit tests**, and all four Molecule scenarios
(`proxmox_vm`, `proxmox_template`, `configure`, `provision`) pass. Both new guards were
additionally confirmed load-bearing by reverting the fix and observing the test fail; the new
gitleaks step was confirmed by planting a GitHub PAT.

## Still open

- **P1-7's architectural question.** Replacing `discover.yml` with the `community.proxmox` inventory
  plugin is a genuine design fork, not a defect fix: it costs a second declaration of the API host
  outside `group_vars`, hits the API on every `ansible*` invocation, and degrades to "no hosts
  matched" instead of failing loudly. The false comment is corrected and the trade-off recorded;
  the swap is the maintainer's call.
- **P3-14**, squashing the two fixup commits, which rewrites history already pushed to PR #6.

---

## P0 — Blockers

### P0-1 · `make destroy VM_NAME=<name>` can delete any VM in the cluster **[verified — FIXED]**
`roles/proxmox_vm/tasks/main.yml:30-49` → `roles/proxmox_vm/tasks/absent.yml:30-48`

The name→VMID lookup is `proxmox_vm_info(type: qemu, name: <arbitrary CLI string>)` with **no
ownership check** — not the `claude-on-proxmox` tag, not `template: false`. Whatever matches is
stopped and deleted with `force: true`, disks included.

- `make destroy VM_NAME=nas` deletes the user's unrelated NAS VM.
- `make destroy VM_NAME=ubuntu-24.04-cloudinit` deletes **the project's own template** — the lookup
  does not exclude templates.
- `vars_prompt` fires *before* the lookup, so the confirmation shows the string the user typed,
  never the VMID or tags of what is about to die.

This is a **regression this branch introduced**. On `main`, `destroy.yml` was `hosts: claude_vms`,
so only VMs declared in inventory could be targeted. The project's own model
(`inventory/group_vars/all/defaults.yml:33`: *"Applied to every VM this project creates, and how
they are found again"*) makes the tag the register of ownership; `discover.yml` honours it, the
destructive path does not.

**Fix** — after the duplicate-name assert in `main.yml`:
```yaml
- name: Refuse to delete a VM this project does not own
  ansible.builtin.assert:
    that:
      - proxmox_vm_tags[0] in (proxmox_vm_lookup.proxmox_vms[0].tags | default('') | split(';'))
      - not (proxmox_vm_lookup.proxmox_vms[0].template | default(false))
    fail_msg: >-
      VM "{{ proxmox_vm_name }}" (VMID {{ proxmox_vm_resolved_id }}) is not tagged
      {{ proxmox_vm_tags[0] }}; this project did not create it. Refusing to delete.
    quiet: true
  when: proxmox_vm_state == 'absent' and proxmox_vm_exists
```

### P0-2 · A VMID collision makes the role rename and overwrite the template **[verified — FIXED]**
`roles/proxmox_vm/tasks/present.yml:3-19`; `community/proxmox/plugins/modules/proxmox_kvm.py:1411`

```python
if proxmox.get_vm(newid, ignore_missing=True):
    module.exit_json(changed=False, vmid=vmid, msg=f"vmid {newid} with VM name {name} already exists")
```

`vmid` there is **the clone source's** VMID (set at `:1391` as `get_vmid(clone or name)`) — i.e.
the template, 9000. `present.yml:19` records `proxmox_vm_clone.vmid` unconditionally, so the role
then runs against the template:

1. `present.yml:21-39` `update: true, name: alpha` → **renames the template**, overwrites `ciuser`,
   `sshkeys`, `ipconfig0`, `tags`, `cores`, `memory`, `agent`.
2. `present.yml:41-47` resizes the template's root disk.
3. `present.yml:49-54` tries to start it; Proxmox refuses and the run dies — after the damage.

Triggers: `proxmox_vm_id` is a single scalar while `provision.yml` loops, so pinning it and running
`VM_NAME=alpha,beta` collides on iteration 2 **every time**; also reachable from two concurrent
`make provision` runs, since Proxmox staff confirm `/cluster/nextid` offers no atomic reserve
(the same race has broken packer-plugin-proxmox #42 and terraform-provider-proxmox #23).

The only test for the pinned-VMID path was **deleted by this branch**
(`roles/proxmox_vm/molecule/default/molecule.yml:43`, removed `proxmox_vm_id: 200`).

**Fix** — immediately after the clone:
```yaml
- name: present | Fail when the clone made no new VM
  ansible.builtin.assert:
    that:
      - proxmox_vm_clone is changed
      - proxmox_vm_resolved_id | int != proxmox_template_vmid | int
    fail_msg: >-
      Clone of "{{ proxmox_vm_name }}" changed nothing - VMID {{ proxmox_vm_id }} is
      already in use. Pick a free proxmox_vm_id or leave it empty.
    quiet: true
  when: not proxmox_vm_exists
```

### P0-3 · `make configure LIMIT=alpha` is documented and cannot work **[verified — FIXED]**
`README.md:66`, `Makefile:15-16`, `playbooks/discover.yml:13`, `playbooks/provision.yml:13`

```
$ .venv/bin/ansible-playbook --limit alpha --list-hosts playbooks/configure.yml
[WARNING]: Could not match supplied host pattern, ignoring: alpha
[ERROR]: Specified inventory, host pattern and/or --limit leaves us with no hosts to target.
$ echo $?
1
```

`--limit` is a global host subset applied before any play runs. `alpha` is never in static
inventory — that is the entire point of the branch — and both `discover.yml` and `provision.yml`
begin `hosts: localhost`, which `--limit alpha` also filters out. `--limit` is fundamentally
incompatible with a runtime-discovered inventory. All five targets that take `VM_ARGS`
(`provision`, `configure`, `site`, `destroy`, `check`) are affected.

`README.md:66` advertises it as *the* fleet workflow — "later, just one of them". Nothing tests the
Makefile argument surface at all; the provision scenario passes names via `host_vars`, never via
`-e` or `--limit`.

**Fix** — drop `LIMIT` entirely. `discover.yml:39-43` already narrows on `vm_name`, so
`make configure VM_NAME=alpha` is correct *today* and `LIMIT` is redundant. If `--limit` must
survive, it has to apply only to the `claude_vms` play, never as a global CLI flag.

---

## P1 — High

### P1-1 · Tag matching is an unanchored substring regex; `configure` can target someone else's VM **[verified]** **[FIXED]**
`playbooks/discover.yml:30-36`

`selectattr('tags', 'search', claude_vm_tag)` is `re.search` against Proxmox's semicolon-delimited
tag string:

| tags | `search` | correct |
|---|---|---|
| `claude-on-proxmox` | match | match |
| `prod;claude-on-proxmox-staging` | **match** | no |
| `not-claude-on-proxmox` | **match** | no |
| `web;db` | no | no |

`make configure` then runs `common`, `dev_tools`, `claude_code`, `github_projects` with
`become: true` against that VM — installing Docker, adding SSH keys, writing the **Anthropic API
key** and **GitHub token**, cloning repos. `claude_vm_tag` is also interpreted as a regex, so any
`.` or `+` in a custom tag widens the match further.

**Fix** (yields exactly the one correct match):
```yaml
| selectattr('tags', 'defined')
| selectattr('tags', 'regex', '(^|;)' ~ (claude_vm_tag | regex_escape) ~ '(;|$)')
```

### P1-2 · The CI secret scan scans nothing **[verified empirically]** **[FIXED]**
`.github/workflows/ci.yml:72-75`, `.pre-commit-config.yaml:17-20`

The step's comment says gitleaks runs in CI because otherwise it "only ever runs for a developer
who installed the git hook". That is exactly what still happens — the hook is
`gitleaks git --pre-commit --redact --staged --verbose` with `pass_filenames: false`. `--staged`
scans the git **index**; in a CI checkout the index equals HEAD, so the diff is empty.

Proven with the real binary against this repo with a live AWS key and GitHub PAT planted in the tree:

```
== as pre-commit runs it (--pre-commit --staged) ==
INF 0 commits scanned. INF no leaks found          <-- rc=0
== gitleaks dir mode (same tree) ==
WRN leaks found: 3        <-- github-pat, generic-api-key, private-key
```

Any secret merged via PR passes CI green. `detect-private-key` and `check-added-large-files` still
work (they take filenames); only the actual secret scanner is dead.

**Fix:** add a real CI step — `gitleaks dir --redact --exit-code 1 .` — and keep the `--staged`
hook for local commits.

### P1-3 · The tracked-file guard regressed; six leak paths are protected by neither guard nor `.gitignore` **[verified]** **[FIXED]**
`.github/workflows/ci.yml:38-49`

Fixing a false positive on `molecule/*/host_vars/localhost.yml` anchored every rule under
`^inventory/`, discarding the old path-anywhere matching. Measured:

| path | guard | `.gitignore` |
|---|---|---|
| `inventory/hosts.yml` | BLOCK | ignored |
| `inventory/group_vars/all/vault.yml` | BLOCK | ignored |
| `.vault_pass.txt` | BLOCK | ignored |
| `inventory/group_vars/local.yml` | **pass** | ignored |
| `inventory/group_vars/vault.yml` | **pass** | ignored |
| `inventory/group_vars/all/secrets.yml` | **pass** | **NOT ignored** |
| `inventory/group_vars/all/vault.json` | **pass** | **NOT ignored** |
| `inventory/proxmox.yml` | **pass** | **NOT ignored** |
| `inventory/prod/host_vars/vm1.yml` | **pass** | **NOT ignored** |
| `group_vars/all/vault.yml` | **pass** | **NOT ignored** |
| `vault.yml` | **pass** | **NOT ignored** |

Two of these are sharp:

- **`group_vars/all/vault.yml` at the repo root.** `site.yml` lives at the root, so Ansible
  genuinely loads this path — it is the most natural place for a user to drop secrets, and it is
  now neither ignored nor blocked. `main`'s pattern caught it.
- **`inventory/proxmox.yml`.** This branch changed `ansible.cfg:2` to advertise
  "`proxmox.yml` (dynamic)" and `inventory/hosts.yml.example:19` repeats it. A
  `community.proxmox.proxmox` inventory config at that path holds `token_secret:` in plaintext.
  **The branch points users at a filename it does not protect.**

Commit `d0bb894`'s message claims "every ignored path still trips the guard" — demonstrably false.

**Fix:** `^inventory/group_vars/(.+/)?(local|vault|secret)[^/]*\.(ya?ml|json)$`,
`^inventory/(.+/)?host_vars/`, `^inventory/(.+/)?[^/]+\.ini$`, plus an anywhere-rule
`(^|/)group_vars/.+/(local|vault)[^/]*\.ya?ml$` and `^inventory/proxmox\.ya?ml$`; add the same to
`.gitignore`. Note the `.example` filter is global, so `.example` is a universal escape hatch.

### P1-4 · `make vagrant-up` is broken by this branch **[verified]** **[FIXED]**
`playbooks/configure.yml:5-7`, `Vagrantfile:34-42`, `README.md:190`

`configure.yml` now unconditionally imports `discover.yml`. The escape hatch `claude_vm_discovery`
exists but is set in exactly one place — `molecule/configure/host_vars/localhost.yml:4` — and
**not** in the Vagrantfile, which supplies its own inventory and does not load
`inventory/group_vars`. Reproduced:

```
TASK [List every QEMU VM] ***
[ERROR]: Error while resolving value for 'api_host': 'proxmox_api_host' is undefined
```

The play runs because Ansible always supplies an implicit `localhost`. `claude_vm_discovery` is
declared in no defaults file and documented nowhere.

**Fix:** add `claude_vm_discovery: false` to `Vagrantfile` `extra_vars`; declare the flag in
`inventory/group_vars/all/defaults.yml` with a comment and mention it in `local.yml.example`.

### P1-5 · A half-provisioned VM is never repaired on re-run **[FIXED]**
`roles/proxmox_vm/tasks/present.yml:21-47`

Idempotence is keyed on *existence*, not on *converged state*. `Apply hardware and cloud-init
settings` is gated `when: not proxmox_vm_exists or proxmox_vm_force_update`; `Resize the root disk`
on `when: not proxmox_vm_exists`.

If the clone succeeds and the run then dies before the config apply completes — Ctrl-C, a 500, an
expiring token — the VM exists. On re-run the role **skips the cloud-init apply and the resize
entirely**, starts a VM with no `ciuser`, no `sshkeys`, no `agent=1` and no tag, then burns the
full ~305 s agent poll before failing. Every subsequent run repeats. The VM is untagged, so
`discover.yml` will never find it either. `proxmox_vm_force_update` recovers the config but
**never the disk** (no force path on resize) — and per P2-1 that flag fails outright from the CLI.

**Fix:** put the resize under the same condition as the apply, and treat an existing-but-untagged
VM as needing configuration.

### P1-6 · Serial provisioning is a 4–5× wall-clock regression against `main` **[verified]** **[FIXED]**
`playbooks/provision.yml:23-31`, `playbooks/destroy.yml:22-31`, `ansible.cfg:16`

On `main`, `provision.yml` was `hosts: claude_vms` with `delegate_to: localhost` inside the role —
Ansible forked across VMs honouring `forks = 10`. The branch collapsed that to a single-host loop
over `include_role`, so **max concurrency is 1 and `forks = 10` is inert**.

Per-VM serial cost: clone (20–90 s) + config + resize + start + agent poll up to `1 + 60 × 5 s = 305 s`.

| N VMs | branch (serial) | `main`'s shape (forks=10) |
|---|---|---|
| 1 | ~1–2.5 min | same |
| 5 | **~5–12 min typical, up to ~27 min** | ~1.5–3 min |
| 10 | ~10–25 min | ~1.5–3 min |

It bites hardest because the dominant term — waiting for DHCP and the agent — is exactly the part
that is naturally concurrent: all N VMs are already booting on the hypervisor while Ansible
watches them one at a time.

**Fix (minimal):** hoist the wait out of the loop — clone/config/start all N, then poll all N.
**Fix (proper):** `add_host` the names in a first play, then run the role in a second play with
`hosts: claude_vms_pending`, `gather_facts: false`, `delegate_to: localhost`.

### P1-7 · `discover.yml` re-implements a supported inventory plugin, and its stated reason is false **[verified live]** **[FIXED]**
`playbooks/discover.yml:9-11`

The header comment justifies the hand-rolled play: *"an inventory plugin runs before vault
decryption and would need its own copy of the API token."* Both halves are wrong. The plugin's own
EXAMPLES document `token_secret: !vault |`
(`community/proxmox/plugins/inventory/proxmox.py:148-156`), and `parse()` templates
`url/user/password/token_id/token_secret` through `self.templar`, so a `lookup()` against the
**existing** `group_vars/all/vault.yml` works with no second copy of the token.

Demonstrated end-to-end against this repo's own fake PVE API, with a ~20-line
`inventory/proxmox.yml`:

```yaml
claude_vms:
  hosts: {alpha: {}, beta: {}}
alpha: ansible_host: 192.0.2.51   # lo, docker0 and fe80:: correctly skipped
beta:  ansible_host: 192.0.2.52
```

`proxmox_tags_parsed` is a real list, so it fixes **P1-1** for free; and `--limit` works against a
dynamic inventory, so it fixes **P0-3** for free. Net **≈ −200 lines** including
`filter_plugins/proxmox.py` and its 88 lines of unit tests.

Honest costs: `proxmox_api_host` must be declared a second time outside `group_vars`; every
`ansible*` invocation parses inventory and hits the API (a warning, not fatal, when unreachable —
mitigate with the plugin's `cache: true`); a down API yields "no hosts matched" instead of a loud
failure; and the fake needs ~6 more lines (`"type": "node"` on `/nodes`, `/pools`, `/nodes/N/lxc`,
`/nodes/N/qemu/N/snapshot`).

**This is the answer to "are we doing anything hacky?" — yes, this is the one substantial piece.**

### P1-8 · Missing `dhcpcd-base` breaks `make template` on Proxmox 9 **[FIXED]**
`roles/proxmox_template/tasks/main.yml` (apt task)

On PVE 9 `virt-customize --install` fails with `Temporary failure resolving 'deb.debian.org'`
because `dhcpcd-base` is no longer pulled in and the libguestfs appliance cannot DHCP
([Proxmox forum 169355](https://forum.proxmox.com/threads/proxmox-9-upgrade-virt-customize-no-longer-has-internet-access.169355/)).
The role installs only `libguestfs-tools`. The fake `virt-customize` can never catch this.
**Fix: one line.**

### P1-9 · No upgrade note; a plain `make template` re-run is a no-op **[FIXED]**
`README.md`, `roles/proxmox_template/meta/argument_specs.yml:8`

Provisioning now hard-depends on `qemu-guest-agent` being baked into the image. Anyone who ran
`make template` before this branch has a template without it — and the role skips the whole build
when the VMID already exists, so re-running `make template` **does not fix it**. The failure mode
is a 5-minute hang per VM. No README, CONTRIBUTING or PR-body text says "`qm destroy 9000`, then
`make template`".

### P1-10 · Per-VM sizing was removed outright, with no replacement and no mention **[FIXED]**
`inventory/hosts.yml.example` (−29 lines), `README.md:118`

The removed block dropped `vm_id`, `vm_prefix_length`, `vm_gateway` (correctly — obsolete) **and**
`vm_cores`, `vm_memory_mb`, `vm_disk_size`, `vm_nameservers` (not obsolete —
`roles/proxmox_vm/defaults/main.yml:29,30,32,47` still read them). Those four are now documented in
**no user-facing file**: not in `hosts.yml.example`, never in `local.yml.example`, and
`README.md:118` still points at `hosts.yml`.

Net effect: every VM is 2 cores / 4 GB / 32 GB and there is no discoverable way to change it.
Setting `vm_cores` in `local.yml` (undocumented) applies fleet-wide. The same applies to
`proxmox_vm_ipconfig`, documented as the static-IP escape hatch but now settable only globally.

### P1-11 · The README role table is wholly false **[FIXED]**
`README.md:118` describes `proxmox_vm` as *"Looks up the VM by **VMID** … applies cloud-init (user,
keys, **static IP**, DNS)"* with keys *"`vm_id`, `vm_gateway`, `vm_nameservers` (per host in
`hosts.yml`)"*. Lookup is by name; the default is DHCP; `vm_gateway`/`vm_prefix_length` no longer
exist anywhere in the repo; `hosts.yml` no longer holds VMs. Every fact in the row is wrong.
`README.md:7` likewise still promises "a static IP … and the sizing you chose".

### P1-12 · `argument_specs.yml` has drifted on the discovery key **[verified]** **[FIXED]**
`roles/proxmox_vm/meta/argument_specs.yml:83` declares `proxmox_vm_tags` default `[claude, ansible]`;
`defaults/main.yml:34` says `[claude-on-proxmox]`. `README.md:121` presents argument_specs as the
documented interface, so a user who follows it gets VMs `discover.yml:35` can never find — and it
is the exact value P0-1's fix depends on. The tag string now lives in three places and only two
agree. `argument_specs.yml:6` also still says "Looks the VM up by VMID".
`proxmox_template`'s spec is clean (16/16). Nothing lints spec-vs-defaults parity, though
`CONTRIBUTING.md:47-49` mandates it.

### P1-13 · Three untested paths that the branch's own safety depends on **[FIXED]**
- **The tag filter is not load-bearing in any test** (`molecule/provision/verify.yml:12-21`). Delete
  `| selectattr('tags','search', claude_vm_tag)` and every test still passes: the only untagged VM
  the fake knows is the stopped template, which is excluded by the *agent* filter anyway. Over-selection
  — i.e. P1-1 — is unproven. **Test:** seed a running, untagged VM; assert it is not discovered.
- **The agent-timeout path has never executed** (`present.yml:70-95`). The wait loop is
  `failed_when: false`, so the assert at `:86` is the only thing stopping `ansible_host: ""` from
  reaching `configure.yml`. Its 5-line `fail_msg` has never been rendered.
- **`net_mac` raises inside a `when:`** (`discover.yml:70`, `filter_plugins/proxmox.py:19-21`). One
  tagged running VM with no `net0` — a NIC removed in the UI, or `net1` only — aborts the whole
  discover play, so **no VM in the fleet becomes reachable**. The unit test proves the filter
  raises; nothing proves the caller survives it.

---

## P2 — Medium

| # | Finding | Location |
|---|---|---|
| P2-1 | **`proxmox_vm_force_update` from the CLI is a fatal non-boolean conditional.** `-e proxmox_vm_force_update=true` arrives as `str`; role argument_specs do not coerce, and ansible-core 2.21 rejects a non-boolean `when:` result. The documented recovery for P1-5 dies with an opaque error. Fix: `\| bool`. Only offender in the tree. | `present.yml:39` |
| P2-2 | **Unguarded `[0]` immediately after start, no retry.** `proxmox_vm_info` drops anything absent from the `/cluster/resources` snapshot; a lag or blip on a VM cloned seconds earlier makes `proxmox_vms[0].config.net0 \| net_mac` a hard failure — at exactly the point where P1-5 makes the re-run unrepairable. Also fails if the template's NIC is `net1`. Every other read in the role has a retry or a default. | `present.yml:56-65` |
| P2-3 | **The agent poll swallows every distinguishable error for 5 min/VM.** A 401, a missing `VM.Monitor`, an unreachable node and a genuinely agent-less template are indistinguishable and each cost 61 × 5 s; the underlying `msg` is never surfaced. With `VM_NAME=a,b,c` and a bad token that is ~15 min to a message that only guesses. Fix: surface `proxmox_vm_net.msg` in the `fail_msg` and fast-fail on 403. | `present.yml:70-95` |
| P2-4 | **Stale-host-key cleanup no-ops on the common destroy path.** The address comes from the guest agent, which cannot answer for a stopped/hung/agent-less VM — i.e. exactly the VMs people destroy. The `known_hosts` removal silently skips, and the next VM at that address fails `HOST KEY VERIFICATION FAILED` with no hint. Pre-branch code read `ansible_host` from inventory and always worked. | `absent.yml:15-23,50-57` |
| P2-5 | **Conversely, when it *does* fire it re-opens TOFU on a recycled address.** `SECURITY.md` justifies `accept-new` precisely because "changed keys are still refused". After destroy forgets the key, whatever next answers at that IP is silently trusted and handed `vm_ssh_public_keys`, the GitHub token and the Anthropic API key. Necessary for the workflow, but beyond the documented trade-off — pin the key from the cloud-init serial console, or document the window. | `absent.yml:50-57`, `ansible.cfg:22` |
| P2-6 | **`VM_NAME` is interpolated unquoted; a space silently provisions half the fleet.** `VM_NAME="alpha beta"` → Ansible's `key=value` extra-vars parser discards the bare word, **no warning**. `VM_NAME="alpha, beta"` → `ERROR: the playbook: beta could not be found`. (`alpha,,beta` and empty are handled correctly.) Fix: quote `-e 'vm_name=$(VM_NAME)'`, then reject whitespace in the assert. | `Makefile:16,72-89` |
| P2-7 | **Bare `make destroy` and bare `make site` are asymmetric.** Without `VM_NAME`, destroy targets only `claude-on-proxmox-default` → on a fleet the user types `yes` at a scary prompt and gets a **silent no-op**; discovery, meanwhile, narrows only `if vm_name is defined`, so bare `make configure`/`site` reconfigures the **entire fleet**. Same missing flag, opposite blast radius, neither documented. | `destroy.yml:28`, `discover.yml:42-43` |
| P2-8 | **`configure` now hard-fails when the Proxmox API is unreachable.** Previously it needed only SSH to the VMs; now an outage, an expired token or a VPN-less laptop kills a run that could have proceeded. The `claude_vm_discovery` escape hatch is undeclared and undocumented (see P1-4). | `discover.yml:25-28` |
| P2-9 | **`discover.yml`'s `failed_when: false` swallows auth failures.** Miss `VM.Monitor` and `make configure` runs against zero hosts and **exits 0**. `present.yml` handles this correctly with a following assert; discover does not. | `discover.yml:52` |
| P2-10 | **Undocumented new dependency: datacenter tag ACLs.** `present.yml:31` now sets `tags`, and discovery depends on it. On PVE ≥ 7.3 with `user-tag-access` set to `list`/`existing` (not the default `free`), a non-root token cannot set `claude-on-proxmox` — provisioning fails, or the tag silently never lands and `configure` finds nothing. | `README.md:91` |
| P2-11 | **Galaxy collections are version *ranges*, not pins**, and the CI cache key is `hashFiles('requirements.yml')` — which does not change when the resolved version does, so CI can run a stale tree indefinitely while a malicious 2.x release would be auto-consumed by every user. CI runs these in `privileged: true` containers. Also: remote pre-commit hooks are pinned to **mutable tags**, not SHAs. | `requirements.yml:5-13`, `.github/actions/setup/action.yml:18-21`, `.pre-commit-config.yaml:7,18` |
| P2-12 | **Two MAC extractors with different contracts.** The branch adds `net_mac` for this, then hand-rolls the same regex inline at `absent.yml:27` — and the two differ (`net_mac` raises, the inline one returns `''`). If deliberate, that wants a `default` parameter and a comment, not a copy. Given P1-7, `net_mac` is redundant with the stock one-liner outright. | `filter_plugins/proxmox.py:12-22`, `absent.yml:27` |
| P2-13 | **`ipconfig0` assertion is now vacuous.** It asserts `ipconfig0 == 'ip=dhcp'`, the literal role default. Hardcode the value at `present.yml:36` and the test still passes — `proxmox_vm_ipconfig`, the documented static-IP escape hatch, silently dies. The prior scenario asserted a *composed* `ip=…/24,gw=…`. | `roles/proxmox_vm/molecule/default/verify.yml:53` |
| P2-14 | **Dead assertion.** `'200' not in proxmox_vm_verify_state` — the VMID was renumbered 200 → 9001 everywhere in this diff except here, so it was already true before the delete ran. It is the only assertion on *persisted state* rather than the request log. | `roles/proxmox_vm/molecule/default/verify.yml:79` |
| P2-15 | **`AGENT_READY_AFTER` is process-global**, so alpha absorbs both refusals and **beta's first poll succeeds immediately** — the multi-VM retry loop is never exercised. Relatedly `polls >= 3` accumulates across converge + idempotence + side_effect, so three stages polling once each also passes. Key it by VMID. | `tests/molecule/fake_pve_api.py:46-47` |
| P2-16 | **`destroy.yml` is never executed by any test** — only `make syntax` touches it. The confirmation gate (accepts `YES`, rejects `y`), the name loop, `state: absent` against a nonexistent VM, and the `known_hosts` removal are all unasserted. This is the destructive entry point. Duplicate-name refusal (`main.yml:37-44`) is likewise untested and ~10 lines to cover. | `playbooks/destroy.yml` |
| P2-17 | **No test covers a bad API token, and the fake's 401 is the wrong shape** — it returns a well-formed `{"data": null}`; real pveproxy returns 401 with an **empty body**, the reason surviving only in the HTTP reason phrase. The README devotes a section to token creation, making this the likeliest first-run failure; users currently get a raw proxmoxer traceback. | `tests/molecule/fake_pve_api.py:126-127` |
| P2-18 | **Fake tag encoding diverges and normalization is version-dependent.** The module sends `",".join(tags)`; the fake echoes it verbatim. Real PVE stores **semicolon-separated**, deduped, ordered and case-normalized — and case normalization applied only on *update* until qemu-server 9.1.17 (#7480), so create and update stored different strings for the same input. No test uses a mixed-case or multi-tag value. Fix: `";".join(sorted(set(t.lower() for t in tags)))`. | `tests/molecule/fake_pve_api.py:88,161` |

---

## P3 — Low

| # | Finding | Location |
|---|---|---|
| P3-1 | Dangling references to `inventory/proxmox.yml`, a file that does not exist and (per the PR's own rationale) never will. Two committed files point users at a phantom — and it is the unprotected path in P1-3. | `ansible.cfg:2`, `inventory/hosts.yml.example:18-19` |
| P3-2 | `inventory_ignore_patterns` **replaces** Ansible's defaults rather than extending them, so a stray `hosts.yml.bak`/`.orig`/`.retry` in `inventory/` is now parsed as inventory. | `ansible.cfg:4` |
| P3-3 | `make syntax` no longer runs "against the example inventory" — the temp-copy dance was removed, so it uses whatever is in `inventory/`, and results differ between CI and a developer's machine. Two docs still describe the old behaviour. | `README.md:183`, `CONTRIBUTING.md:26` |
| P3-4 | Name-validation error blames the wrong thing: seven unrelated conditions share one catch-all `fail_msg`, so a typo'd `VM_NAME` sends the user off to check their API token. The offending value is never echoed, and the regex permits a trailing `-`/`.` with no 63-char limit — not quite "DNS-safe" as advertised. | `roles/proxmox_vm/tasks/main.yml:3-17` |
| P3-5 | `discover.yml` has no duplicate-name guard, unlike the role. Two tagged VMs named `alpha` → the second `add_host` silently overwrites the first and `configure` reports success having touched one of them non-deterministically. | `discover.yml:59-74` |
| P3-6 | Conditional `set_fact` leaks across loop iterations (latent). A skipped `set_fact` keeps the prior iteration's value, whereas `register` is overwritten. Inert today; one edit from applying VM #1's MAC to VM #2. | `absent.yml:25-28` |
| P3-7 | `present \| Start the VM` has no `when:`, so re-running provision boots a VM the user deliberately stopped. | `present.yml:49-54` |
| P3-8 | `vm_ssh_public_keys` reads `~/.ssh/id_ed25519.pub` unconditionally, and Jinja evaluates the left operand of `or` first — a user with only an RSA key gets "could not locate file" instead of the assert's helpful message. | `inventory/group_vars/all/defaults.yml:44-45` |
| P3-9 | `pveum` privilege list is correct and complete for every call the branch makes (verified call-by-call, `VM.Monitor` genuinely required) but is **not minimal**: `VM.Config.CDROM` has no corresponding call — the cloud-init drive is created by `qm set` over SSH as root, not with this token. Either drop it or reword `README.md:77`. | `README.md:77-92` |
| P3-10 | Fact cache + reusable names is a new stale-facts hazard: destroy `alpha` in the UI, re-provision it at a different address, and within the hour `gathering = smart` skips gathering and every `ansible_*` fact describes the dead VM. `make destroy` clears the cache but only via make, and `rm -rf`s the whole fleet's. A stale `.cache/facts/s1_claude-dev` is already in the working tree. | `ansible.cfg:12-15`, `Makefile:85` |
| P3-11 | CI **inlines** `molecule test -s ${{ matrix.scenario }}` instead of calling `make molecule-integration` / `make molecule-provision`, so those targets are never executed by CI — break one and CI stays green. Conversely `make pre-commit` runs in CI but is not in `make test`, so a local `make test` is not a superset of the gate. | `.github/workflows/ci.yml:136`, `Makefile:121-129` |
| P3-12 | Weak unit tests: `test_lowercases` asserts `.islower()` — **verified False** for a digits-only MAC, and a truncated `"aa:bb"` also satisfies it; `test_skips_loopback_without_a_mac` passes even with the `lo` branch deleted, because `127.` is already filtered (the branch only matters when `lo` carries a non-127 secondary); `test_none_when_the_nic_has_no_address_yet` passes `ip-addresses: []`, a shape a real QGA never sends — the key is **absent**, which is exactly the DHCP-window state the retry loop exists for. | `tests/unit/test_filters.py:35,63,70` |
| P3-13 | `guest_ipv4`'s no-MAC contract is unpinned: `guest_ipv4([DOCKER0, ETH0])` returns `172.17.0.1`. The docstring promises docker0 is excluded; that holds only when a MAC is passed. | `filter_plugins/proxmox.py`, `tests/unit/test_filters.py:55` |
| P3-14 | Commit hygiene: seven of nine commits are model incremental history. `d0bb894` fixes a guard that `c43287e` broke (and its body contains the false verification claim in P1-3); `4e427a2` fixes a yamllint violation `d0bb894` introduced one commit earlier and ends with process chatter addressed to the author. Squash both into `c43287e` → 7 standalone commits. Separately, `7fa94d1` is missing the blank line before its `Co-Authored-By:` trailer, so GitHub will not attribute the co-author. | `git log main..HEAD` |

---

## Verified clean

Worth recording, because several of these looked wrong and are not:

- **No multi-VM fact leakage on the provision path.** Every `set_fact`/`register` was traced and
  checked empirically: `proxmox_vm_clone`, `proxmox_vm_config` and `proxmox_vm_net` are overwritten
  with the skip result on a skipped iteration; `proxmox_vm_exists`, `proxmox_vm_resolved_id`,
  `proxmox_vm_mac` and `proxmox_vm_address` are set unconditionally each pass. The `.51`/`.52`
  per-VM split in the provision scenario genuinely covers this. Only P3-6 is latent.
- **`until` + `failed_when: false` + a following assert is not the dead-code trap it resembles** —
  `UnifiedTaskResult.failed` checks `failed_when_result` before `_failed`, so the assert is reached
  and the message does print. (The defect is diagnosis, P2-3, not control flow.)
- **No secret is committed on this branch.** Every IP is RFC 5737, `127.0.0.1`, or the deliberate
  `172.17.0.1` docker0 decoy; `git grep -iE 'kawade|omkar21'` over tracked files returns zero hits;
  `inventory/controller.yml` is generic; the molecule `group_vars` are symlinks, not copies.
- **No runtime credential leak.** `module_utils/proxmox.py:41` declares `api_token_secret` with
  `no_log=True`, so Ansible censors it even at `-vvvv`; both roles' argument_specs mark the token
  and cloud-init password `no_log: true`; no new `debug`/`fail_msg` echoes a credential.
- **GitHub Actions supply chain is clean.** All four third-party actions pinned to full 40-char
  SHAs, each verified to resolve to a real tagged commit; no `pull_request_target`; no
  `github.event.*` interpolated into any `run:`; workflow-level `contents: read`; `release.yml`'s
  `contents: write` is correctly scoped and uses `"$GITHUB_REF_NAME"` as a quoted env var, so a
  hostile tag name cannot inject. `MOLECULE_IMAGE` is digest-pinned everywhere except the
  deliberate schedule-only canary.
- **No shell injection in the virt-customize block.** All six root-privileged calls use
  `command:` with `argv:` list form → `execve`, no shell. No `shell:`/`raw` anywhere in a
  production task path.
- **Baking the guest agent is the right call.** Ubuntu cloud images genuinely do **not** ship
  `qemu-guest-agent` — verified against the noble and jammy manifests (664 and 600 packages, zero
  matches). `LIBGUESTFS_BACKEND: direct` is the correct Proxmox workaround, and
  `--truncate /etc/machine-id` is right (though the comment's framing is off: virt-customize is
  itself what creates machine-id — RH Bugzilla 1554546). The cloud-init `--cicustom` alternative is
  ~90 lines cheaper but needs snippet-capable storage on every node and pushes agent availability
  behind first-boot apt. **Keep it.**
- **The `prepare.yml` refactor dropped nothing.** `tests/molecule/prepare_pve.yml` reproduces all
  five tasks with port and source parameterized; the only deltas are name prefixes. The regressions
  in this diff came from the VMID renumbering and the CI-guard rewrite, not from this extraction.
- **`newid: omit` is safe within a serial run** — the clone waits for the Proxmox task before the
  loop advances; a concurrent run loses the race loudly rather than silently.
- **`when:` on `import_playbook`, `module_defaults` on the block, `update: true` not clobbering the
  template's `--cpu host`, `proxmox_disk` resize semantics, and `proxmox_vms[0].vmid | default('')`
  on an empty list** were each checked and are correct.
- **`filter_plugins/` is old-style, not deprecated** — `DEFAULT_FILTER_PLUGIN_PATH` carries no
  deprecation marker in ansible-core 2.21.3, and the `ansible.cfg` entry is genuinely required
  because plugin adjacency resolves relative to `playbooks/`.

---

## Suggested order of work

1. ~~**P0-1, P0-2, P0-3**~~ — done.
2. **P1-1** — decide the discovery question first (P1-7). Adopting the inventory plugin fixes the
   tag over-match for free; keeping `discover.yml` means anchoring the tag regex there too, the
   same way `vars/main.yml` now does for the delete guard.
3. **P1-2, P1-3** — the repo is public and its secret scanner does not run.
4. **P1-4, P1-8, P1-9** — three one-to-few-line fixes that each break a fresh user's first run.
5. **P1-10, P1-11, P1-12** — docs and argument_specs now actively mislead.
6. **P1-13** and the P2 test items — close the gaps that let P0-2 and P1-1 ship green.
7. **P1-5, P1-6** — re-run repair and parallelism; both are structural and deserve their own commits.
