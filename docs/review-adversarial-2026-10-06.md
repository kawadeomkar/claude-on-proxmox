# Adversarial review: `feat/multi-vm-dhcp-discovery` and the Proxmox host

Reviewed 2026-10-06. Branch at `1b2a739 + uncommitted working tree`, compared against local `main` (6167131): 34 commits plus the uncommitted fixes from the previous review, 75 files, +6677 / −320. **That base is stale.** `origin/main (9f4aec1, merge of PR #6 on 2026-10-03)` already contains 29 of the 34 commits; the true delta is five commits (`94c457e`, `86e8e83`, `2c3b20b`, `d4f1471`, `1b2a739`, 21 files) plus the working tree (53 files vs. `origin/main`). Each finding says where the defect lives today.

Seven independent reviewers each audited one area, read-only, with instructions to disprove every finding before reporting it: provisioning correctness and the safety invariants; the `claude_code` role, Remote Control and secret hygiene; conventions, docs and commits; performance (timed against the repo's fake API run as a local process at N=20); test coverage (with real mutation testing of the filters); the template role, Makefile and CI; and a read-only audit of the real Proxmox host `pve (10.0.25.100), Proxmox VE 9.2.2`. Their reports were merged, duplicates collapsed, and every P0–P2 claim re-checked by the aggregator against the code and, where relevant, the host itself. **44 findings.**

_Status updated 2026-10-06: **44 of 44 fixed**, 0 open._

| Priority | Meaning | Count |
|---|---|---|
| **P0** | The project cannot work, data loss, or a security exposure | 1 |
| **P1** | A likely bug in a realistic scenario, or a test that passes while the thing it guards is broken | 2 |
| **P2** | An edge-case bug, a measurable regression, or docs that lead to a wrong action | 13 |
| **P3** | Minor correctness, cleanup, staleness or a small test gap | 28 |

| Where it lives | Count |
|---|---|
| on main — Already merged into origin/main through PR #6; the branch inherits it | 24 |
| branch commit — In one of the five unmerged commits (94c457e..1b2a739) | 1 |
| uncommitted — Only in the uncommitted working tree | 16 |
| host — On the Proxmox host, not in the repository | 3 |

## What matters most

_Updated after the fix passes on 2026-10-06: every finding is fixed and verified, and the work is committed as a readable sequence (P3-1)._

- **The host could not be used at review time, and now can.** README step 5 (API user, role, token, ACLs) had not been done and the controller had no `vault.yml` or `local.yml`. Both were resolved the same day and `make list` runs against the host (P0-1).
- **One offline cluster node broke the fleet again.** `/cluster/resources` drops `name` for guests whose RRD entry is stale, and four consumers indexed it without a default; the fake hid this because its down-node VM had a name. Fixed with defaults at every consumer and an honest fixture (P1-1).
- **The safety interlock was right but under-tested.** The role's whole-tag compare is now driven against two lookalike tags and one tag-not-first VM (P1-2); the other test gaps where a defence existed without a test that would notice its removal are closed too (P2-7 to P2-12).
- **Remote Control's two sharp edges are gone**: a privacy setting in `settings.json` takes the stopped-with-a-message path instead of failing the run (P2-1), and a healthy server no longer costs 20 s per batch on every run (P2-2).
- **Discovery and `make list` make two requests per VM** to the node that holds it instead of five plus a module spawn (P2-3).
- **Nothing from the previous review regressed.** All 44 fixed findings spot-checked are real; P1-9, P1-10 and P3-11 are load-bearing under mutation.

## Index

| ID | Status | Finding | Area | Lives |
|---|---|---|---|---|
| [P0-1](#p0-1) | Fixed | The Proxmox host has no API user, role, token or ACLs, so every target except `make template` fails with 401 | Host | host |
| [P1-1](#p1-1) | Fixed | A guest on a node that has been offline for more than five minutes has no `name` in `/cluster/resources`, which crashes `make destroy` cluster-wide and `make configure`/`list`/`check`/`claude-login` for the whole fleet | Provisioning, Discovery, Tests | uncommitted |
| [P1-2](#p1-2) | Fixed | The role's whole-tag ownership check is never exercised against a lookalike tag, so a substring regression ships green | Tests, Safety | uncommitted |
| [P2-1](#p2-1) | Fixed | With Remote Control on by default, a privacy setting in `settings.json` hard-fails the whole configure run, and the error omits the off switch | Remote Control | uncommitted |
| [P2-2](#p2-2) | Fixed | Every `make configure` sleeps 20 s per batch of VMs on a healthy Remote Control server | Remote Control, Performance | uncommitted |
| [P2-3](#p2-3) | Fixed | Discovery and `make list` are serial and pull the whole cluster listing twice per VM | Discovery, Performance | on main |
| [P2-4](#p2-4) | Fixed | The three 'does the template exist' checks disagree on what 'exist' means, and none checks the node | Provisioning, Docs | branch commit |
| [P2-5](#p2-5) | Fixed | The `/cluster/nextid` request is `no_log` with no error path, and duplicates the one-place request pattern | Provisioning, Conventions | on main |
| [P2-6](#p2-6) | Fixed | ARCHITECTURE.md still documents the fact-cache eviction P3-1 removed and the `gather_facts` explanation P3-8 corrected | Docs | uncommitted |
| [P2-7](#p2-7) | Fixed | No test runs the role against a VM whose NIC cannot be read; dropping the assert silently publishes the docker bridge | Tests | uncommitted |
| [P2-8](#p2-8) | Fixed | The 'fail fast on 403' wiring is never exercised, because the fake never returns 403 | Tests, Fakes | uncommitted |
| [P2-9](#p2-9) | Fixed | Re-running configure against a live Remote Control server is never tested, so a regression that restarts it every run ships green | Tests, Remote Control | uncommitted |
| [P2-10](#p2-10) | Fixed | The `settings.json` blocker preflight (P3-6's fix) has no test at all | Tests, Remote Control | uncommitted |
| [P2-11](#p2-11) | Fixed | Fleet VMID pre-allocation's 'skip VMIDs already in use' branch is never load-bearing, and the pinned-VMID refusal is untested | Tests, Fakes, Provisioning | on main |
| [P2-12](#p2-12) | Fixed | The Makefile's safety guards have no test; a regression to `-e vm_name=$(VM_NAME)` would ship green | Tests, Build | uncommitted |
| [P2-13](#p2-13) | Fixed | Root SSH on the hypervisor allows password authentication | Host, Security | host |
| [P3-1](#p3-1) | Fixed | The commit-history cleanup (old P3-12) is now mostly impossible: 29 of the 34 commits are already on `main` | Conventions, Commits | on main |
| [P3-2](#p3-2) | Fixed | `roles/proxmox_vm/vars/main.yml`'s header justifies the lazy-var rule with a `destroy.yml` loop that no longer exists | Conventions | on main |
| [P3-3](#p3-3) | Fixed | CLAUDE.md, ARCHITECTURE.md and the code disagree on how many facts `present.yml` publishes | Docs | uncommitted |
| [P3-4](#p3-4) | Fixed | The Makefile comment misdescribes how far `SKIP` propagates, and `make -j test` is not prevented | Conventions | uncommitted |
| [P3-5](#p3-5) | Fixed | `--permission-mode` is interpolated into `ExecStart` unquoted while `--name` is quoted | Remote Control, Conventions | on main |
| [P3-6](#p3-6) | Fixed | `vm-name-check` is line-oriented, so a VM_NAME with an embedded newline passes | Build, Security | uncommitted |
| [P3-7](#p3-7) | Fixed | The fact cache survives a failed `make destroy` and a VM deleted outside it, and can hand a rebuilt VM the old VM's facts | Build, Provisioning | on main |
| [P3-8](#p3-8) | Fixed | The template build leaves ~1.2 GB of images in `local`'s ISO directory, where the UI lists them as ISO images | Template, Build | on main |
| [P3-9](#p3-9) | Fixed | The template role's one safety check, 'Refuse to reuse VMID', has no test | Tests, Template | on main |
| [P3-10](#p3-10) | Fixed | `vault-check` guards the plaintext case only; a missing `vault.yml` falls through to an Ansible 'undefined variable' error | Build | on main |
| [P3-11](#p3-11) | Fixed | A nested `inventory/<dir>/proxmox.yml` inventory-plugin file passes both the guard and `.gitignore` | Security, CI | on main |
| [P3-12](#p3-12) | Fixed | `claude_code_settings` is a documented carrier for a key but is not `no_log` in the arg spec | Conventions, Security | on main |
| [P3-13](#p3-13) | Fixed | 'A hand-broken `settings.json` does not fail the run' holds only when nothing is managed, and an empty file is unguarded | Remote Control, Docs | on main |
| [P3-14](#p3-14) | Fixed | `forks = 10` turns a fleet above 10 into two waves, while the docs say every VM boots concurrently | Performance, Docs | on main |
| [P3-15](#p3-15) | Fixed | The worst-case agent wait is about nine minutes, not the 'five minutes' the docs and messages promise | Provisioning, Docs | on main |
| [P3-16](#p3-16) | Fixed | `absent.yml` and the existing-VM path re-read a config the lookup already holds | Performance | on main |
| [P3-17](#p3-17) | Fixed | `proxmox_vm_force_update` starts a VM that was shut down and replaces its tags, contradicting the README's re-run guarantee | Provisioning, Docs | on main |
| [P3-18](#p3-18) | Fixed | Update calls go to `proxmox_vm_node`, not the node the lookup found the VM on, so repairing a migrated VM fails with a misleading error | Provisioning | on main |
| [P3-19](#p3-19) | Fixed | `make deploy VM_NAME=x` exits 0 having configured nothing when `x` is stopped, while `make configure VM_NAME=x` fails for the same state | Provisioning, Docs | uncommitted |
| [P3-20](#p3-20) | Fixed | The single-name `make provision` path, the README's first command, is never run by Molecule | Tests | on main |
| [P3-21](#p3-21) | Fixed | Tag case is unhandled: Proxmox lowercases tags, the matching does not | Provisioning, Discovery | on main |
| [P3-22](#p3-22) | Fixed | `guest_ipv4`'s agent-side `.lower()` on the MAC is dead to the tests | Tests | on main |
| [P3-23](#p3-23) | Fixed | The fake has no `lxc` row in `/cluster/resources`, so the `type == 'qemu'` filter and the container VMID skip are untested | Tests, Fakes | on main |
| [P3-24](#p3-24) | Fixed | The worktree-spawn git-repository check (P3-7's fix) is untested | Tests, Remote Control | uncommitted |
| [P3-25](#p3-25) | Fixed | A running VM still carrying the unfinished tag (death between the start and the untag) has no fixture | Tests, Provisioning | on main |
| [P3-26](#p3-26) | Fixed | The weekly canary cannot see the Ubuntu cloud image break; the docs say it does | Docs, CI | on main |
| [P3-27](#p3-27) | Fixed | Grouped documentation staleness | Docs | uncommitted |
| [P3-28](#p3-28) | Fixed | Host notes that need no change to the project defaults | Host | host |

## P0: The project cannot work, data loss, or a security exposure

<a id="p0-1"></a>
### P0-1. The Proxmox host has no API user, role, token or ACLs, so every target except `make template` fails with 401

- **Area:** Host
- **Found by:** Host audit
- **Confidence:** Reproduced on the host (`pveum user list`, `pveum acl list`, `pveum user token list ansible@pve`), re-checked by the aggregator
- **Lives:** host — On the Proxmox host, not in the repository
- **Status:** Fixed
- **Where:** `host: `pveum user list`, `pveum role list`, `pveum acl list``, `inventory/group_vars/all/defaults.yml:13-15`, `README.md:290 (Creating the Proxmox API token)`

**What is wrong**

- The project authenticates every API call as `ansible@pve!ansible` with `vault_proxmox_api_token_secret`. On the host only `root@pam` exists, there is no `AnsibleVM` role, no token, and `pvesh get /access/acl` returns `[]`.
- On the controller `inventory/group_vars/all/vault.yml` and `local.yml` do not exist either, so there is no secret to send even once the token is created.
- Everything else about the host is ready: no-subscription repo enabled and both enterprise repos disabled, VT-x on, `vmbr0` bridged to a wired NIC with a reachable gateway and DNS, `local-lvm` active with 141 GB free, NTP synced, tag access unrestricted, API listening on 8006, no VMs and no template yet (expected before `make template`).

**Failure scenario.** `make template` works (root SSH + `qm`). `make provision`, `make list`, `make configure`, `make check`, `make destroy`, `make claude-login` and the second half of `make deploy` fail at the first API call; `playbooks/tasks/cluster_vms.yml` reports a 401 with the token hint, and `roles/proxmox_vm/tasks/main.yml` first asserts the secret is non-empty.

**Fix.** Run the README recipe as root on the host (PVE 9, so the agent privilege is `VM.GuestAgent.Audit`, and `SDN.Use` is needed to attach a NIC to `vmbr0`): `pveum role add AnsibleVM -privs "VM.Allocate VM.Clone VM.Config.CPU VM.Config.Cloudinit VM.Config.Disk VM.Config.Memory VM.Config.Network VM.Config.Options VM.PowerMgmt VM.Audit Datastore.AllocateSpace Datastore.Audit SDN.Use VM.GuestAgent.Audit"`, `pveum user add ansible@pve`, `pveum aclmod /vms -user ansible@pve -role AnsibleVM`, `pveum aclmod /storage/local-lvm -user ansible@pve -role AnsibleVM`, `pveum aclmod /sdn/zones/localnetwork/vmbr0 -user ansible@pve -role AnsibleVM`, `pveum user token add ansible@pve ansible --privsep 0`. Put the printed secret in `vault.yml` and `make vault-encrypt`; verify with `pveum user token permissions ansible@pve ansible`. If VM disks move to TrueNAS later, add the same ACL on `/storage/<name>` and set `proxmox_storage` in `local.yml`.

**Resolution (fixed).** Applied 2026-10-06 following the README recipe on the host: role `AnsibleVM` (with `VM.GuestAgent.Audit` and `SDN.Use`), user `ansible@pve`, ACLs on `/vms`, `/storage/local-lvm` and `/sdn/zones/localnetwork/vmbr0` with propagation, token `ansible` with `privsep 0`. On the controller: `local.yml` created from the example (defaults), a random `.vault_pass` generated, `vault.yml` written with the secret and encrypted with `make vault-encrypt`; all three git-ignored. Verified: `pveum user token permissions ansible@pve ansible` shows the full privilege set on all three paths, and `make list` ran against the real host and returned "No VMs tagged claude-on-proxmox exist" with `failed=0`.

## P1: A likely bug in a realistic scenario, or a test that passes while the thing it guards is broken

<a id="p1-1"></a>
### P1-1. A guest on a node that has been offline for more than five minutes has no `name` in `/cluster/resources`, which crashes `make destroy` cluster-wide and `make configure`/`list`/`check`/`claude-login` for the whole fleet

- **Area:** Provisioning, Discovery, Tests
- **Found by:** Provisioning
- **Confidence:** Confirmed: PVE's own `/usr/share/perl5/PVE/API2Tools.pm` on the host sets `name` only inside `if ($rrd->{$key})`; the Jinja failure was reproduced under ansible-core 2.21.3
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `playbooks/destroy.yml:54-56`, `playbooks/discover.yml:44`, `playbooks/list.yml:26`, `inventory/group_vars/all/defaults.yml:86 (`claude_vm_requested`)`, `playbooks/tasks/cluster_vms.yml:12-13`, `tests/molecule/fake_pve_api.py:52-59`

**What is wrong**

- pve-manager's `extract_vm_stats` sets only `id`, `vmid`, `node`, `type` and `status: 'unknown'` unconditionally; `name`, `template` and the real `status` come from the pmxcfs RRD cache, which pmxcfs expires after 300 s. An offline node cannot refresh it, so after five minutes every guest on that node is listed without `name`. The same shape appears for ~10 s after a clone.
- `provision.yml:30` uses `map(attribute='name', default='')` and `claude_vm_tagged` guards `tags` with `selectattr('tags', 'defined')`, so the missing-key case was known; the four other consumers index `name` with no default and fail the task with `object of type 'dict' has no attribute 'name'`.
- `destroy.yml:56` maps `name` over every qemu guest in the cluster, not just this project's, so any VM at all on an offline node breaks `make destroy`.
- The fake lists its down-node VM *with* `name` and `template`, so the provision scenario's 'an unreachable node does not break commands about VMs on the others' check passes while the thing it guards is broken. This is the P2-10 outage one layer up.

**Failure scenario.** Two-node homelab, `pve2` off for the evening with any VM on it: `make destroy VM_NAME=alpha` fails at 'Check that every VM asked for exists' and nothing on `pve` can be destroyed. If one of this project's VMs is on the offline node, `make configure`, `check`, `claude-login` fail at `claude_vm_dupes` and `make list` at the sort, so the healthy node's VMs are not configured or listed. `make deploy` is unaffected (discovery off; provision is defended).

**Fix.** Treat a missing `name` as 'not addressable by name': add `| selectattr('name', 'defined')` to `claude_vm_tagged`, use `map(attribute='name', default='')` at destroy.yml:54/56, discover.yml:44 and list.yml:26, and guard `claude_vm_requested` the same way. Then make the fake honest: give the `pve2` VM no `name`/`template` key, add a *tagged* VM on `pve2`, and assert destroy's pre-check, discovery and the listing still work. Correct the comment in `cluster_vms.yml:12-13`.

**Resolution (fixed).** `claude_vm_requested` only considers entries that have a name; `destroy.yml`'s templates, fleet and untagged lists, `discover.yml`'s duplicate check and the "tagged VMs exist" message, and `list.yml`'s sort, row format and footnote all read `name` with a default, so a tagged VM on a node that is down stays in `claude_vm_tagged` (and in `make list`, last, as `(no name)` with status `unknown`) without failing anything. The fake now lists both guests on its down node the way a real cluster does once the node's statistics have expired - no `name`, no `template` - and one of them carries this project's tag. The provision scenario asserts that shape, that fleet-wide discovery admitted the nameless VM (VMID 8001) and configured nothing on it, that `make list` shows it last with a blank name, and that every destroy pre-check still works with it present. `cluster_vms.yml`'s header says which keys the listing drops.

<a id="p1-2"></a>
### P1-2. The role's whole-tag ownership check is never exercised against a lookalike tag, so a substring regression ships green

- **Area:** Tests, Safety
- **Found by:** Tests
- **Confidence:** Reproduced with a mutation (whole-tag match collapsed to a substring test; both scenarios stay green)
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/vars/main.yml:30-33 (`proxmox_vm_found_tags`)`, `roles/proxmox_vm/tasks/main.yml:96-109, 135-147`, `roles/proxmox_vm/molecule/default/side_effect.yml:14-24, 62-134`, `molecule/provision/verify.yml:21-23`

**What is wrong**

- Every VM the role scenario refuses for ownership carries no tags at all (`someones-nas`). The lookalike fixtures that exist (`claude-on-proxmox-staging`, `not-claude-on-proxmox`) are only ever reached through `destroy.yml`'s pre-check and discovery, which use `claude_vm_tag_pattern`, never through the role's own compare.
- Mutation: replace `proxmox_vm_found_tags` with the raw tag string so `proxmox_vm_tags[0] in ...` becomes a substring test. `someones-nas` is still refused (empty), `half-built`/`died-after-settings` still pass (`'claude-on-proxmox-unfinished'` contains the tag as a substring), `legacy` is deleted through the escape hatch, `stranger` never reaches the role. Both scenarios green.

**Failure scenario.** `make provision VM_NAME=<a VM someone tagged claude-on-proxmox-staging>`: provisioning has no playbook pre-check, so the role's assert is the only guard. The VM's cores, memory, cloud-init user, keys and disk are rewritten, it is stamped `claude-on-proxmox`, and `make destroy` then deletes it. P0-2's shape, one character of refactoring away.

**Fix.** In the role side-effect's 'Refuse to change a VM this project does not own' play, clone two more fixtures and PUT `tags=claude-on-proxmox-staging` and `tags=not-claude-on-proxmox`; run the role against each with `state: present` and `state: absent`, rescue, assert `'did not create it'`, and extend `verify.yml:62-67` so no PUT/DELETE/stop reached their VMIDs. Add one fixture with `tags=ansible;claude-on-proxmox` so element matching (not prefix matching) is what passes.

**Resolution (fixed).** `roles/proxmox_vm/molecule/default/side_effect.yml` clones three more fixtures: `lookalike-suffix` tagged `claude-on-proxmox-staging`, `lookalike-prefix` tagged `not-claude-on-proxmox`, and `tagged-second` tagged `ansible;claude-on-proxmox`. `tasks/lookalike.yml` runs the role against each lookalike with `state: present` and `state: absent` inside block/rescue and asserts the ownership refusal names the VM; the role is then run with `state: absent` against `tagged-second`, which a whole-element match admits and a prefix match would refuse. `verify.yml` asserts no write, stop or DELETE ever reached 9301 or 9302, that both survive in the fake's state, and that exactly one DELETE reached 9303 and it is gone. A substring compare now fails the scenario.

## P2: An edge-case bug, a measurable regression, or docs that lead to a wrong action

<a id="p2-1"></a>
### P2-1. With Remote Control on by default, a privacy setting in `settings.json` hard-fails the whole configure run, and the error omits the off switch

- **Area:** Remote Control
- **Found by:** Security
- **Confidence:** Confirmed by reading `remote_control.yml:32-52` and `vars/main.yml:49-51`
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `roles/claude_code/tasks/remote_control.yml:32-52`, `roles/claude_code/vars/main.yml:13-21, 49-51`, `roles/claude_code/tasks/main.yml:61-63`

**What is wrong**

- `claude_code_remote_control_wanted` is true whenever `claude_remote_control` (default `true`) is set and no key is configured. The settings preflight is a plain `assert`, so a `settings.json` env holding `DISABLE_TELEMETRY`, `DO_NOT_TRACK`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN` and friends fails the host; `github_projects`, which runs after `claude_code`, never runs for it.
- The three sibling conditions (a configured key, a key Claude Code finds on its own via `apiKeySource`, a key set through `claude_code_settings.env`) all converge gracefully: unit left stopped plus a message. Only this one aborts.
- The `fail_msg` says to remove the setting 'to use Remote Control' and never mentions `claude_remote_control: false`, which is the right answer for someone who set `DO_NOT_TRACK` deliberately. README and `local.yml.example` do not warn about it.

**Failure scenario.** A user on `main` had `claude_code_settings: {env: {DISABLE_TELEMETRY: '1'}}` in `local.yml`. They pull this branch and run `make configure`: every VM fails at 'Refuse settings that silently disable Remote Control', repositories are not cloned, and the message tells them to drop their privacy setting.

**Fix.** Fold the three checks into `claude_code_remote_control_eligible`/`claude_code_remote_control_blocker` (vars/main.yml:86-111) so the VM takes the 'unit installed, service stopped, explain why' branch. If the hard assert is kept on purpose, add 'or set `claude_remote_control: false`' to the message and say in README/`local.yml.example` that a telemetry opt-out in `claude_code_settings.env` needs that switch.

**Resolution (fixed).** The `assert` is gone. `roles/claude_code/vars/main.yml` turns the settings read from disk into `claude_code_remote_control_settings_blockers` — the names of the offending env keys, `apiKeyHelper`, and `ANTHROPIC_BASE_URL` when its host is not `api.anthropic.com`; names only, never values — and `claude_code_remote_control_eligible` now also requires that list to be empty, so a VM with a blocker takes the existing not-eligible path in `remote_control.yml`: unit installed, service stopped and disabled, one message. `claude_code_remote_control_blocker` puts the settings case first and names the keys, says to remove them from `claude_code_settings` and the file on the VM, or to set `claude_remote_control: false`; the "Say exactly what to run" task no longer suggests a login step for a VM that is signed in and only blocked by settings. `login.yml` (`make claude-login`) reads `~/.claude/settings.json` over SSH too, so the two commands still decide from the same vars. The slurp stays `no_log`, runs in check mode, and the eligible-and-live report is skipped in check mode. README (table row and the off-switch paragraph), ARCHITECTURE §5.5 steps 2 and 5, and `local.yml.example` (a commented `claude_remote_control` entry) describe the new behaviour. Verified by the P2-10 harness: every seeded blocker leaves the host at rc 0 with the message naming exactly the offending keys and the off switch; the `claude-ai-telemetry` login case shows `make claude-login` naming `DISABLE_TELEMETRY`. The `claude_code` Molecule scenario was not run (Docker disk).

<a id="p2-2"></a>
### P2-2. Every `make configure` sleeps 20 s per batch of VMs on a healthy Remote Control server

- **Area:** Remote Control, Performance
- **Found by:** Performance
- **Confidence:** Confirmed by reading `remote_control.yml:213-251`; the start task is not registered
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `roles/claude_code/tasks/remote_control.yml:213-222 (start), 236-251 (stay-up check)`

**What is wrong**

- The 'Confirm the session stayed up' block's only condition is `not ansible_check_mode`; the enclosing block checks eligibility and that the unit exists. The enable/start task is not registered, so nothing distinguishes a restart from `state: started` on an already-active unit, which changes nothing.
- Cost per run: 20 s wall for up to 10 VMs, 40 s for 11-20 (`forks = 10`, the `wait_for` holds a worker). README.md:498 documents the wait only for the failure case. CI never pays it because no container is signed in.

**Failure scenario.** `make configure` or `make configure TAGS=claude_code` on a 20-VM fleet that is already configured and signed in: 40 s of dead time every run.

**Fix.** `register: claude_code_remote_control_start` on the enable/start task and add `claude_code_remote_control_start is changed` to the stay-up block's `when:`. A restarted unit still gets the full check.

**Resolution (fixed).** `remote_control.yml` registers the enable/start task as `claude_code_remote_control_start`, and the "Confirm the session stayed up" block now runs only when `not ansible_check_mode` and `claude_code_remote_control_start is changed`. A `started` against an already-active unit with an unchanged unit file changes nothing, so a live fleet no longer pays the 20 seconds; a restart (changed unit, or a unit found inactive/activating) still gets the full check. The "Report where to reach the session" debug moved out of that block so a server left alone is still reported live (skipped in check mode). README's wait paragraph and ARCHITECTURE §5.5 step 7 say so. Verified by lint and syntax; the behaviour itself is what the P2-9 side effect now asserts (`claude_code_remote_control_start is not changed` on the unchanged re-run), to be exercised when the scenario runs.

<a id="p2-3"></a>
### P2-3. Discovery and `make list` are serial and pull the whole cluster listing twice per VM

- **Area:** Discovery, Performance
- **Found by:** Performance
- **Confidence:** Measured against the repo's fake API run as a local process (N=20: 4.3 s on a 22-guest cluster, 8.8 s on 222 guests)
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `playbooks/discover.yml:55-71`, `playbooks/list.yml:30-46`, `community.proxmox `proxmox_vm_info.py:164-209``

**What is wrong**

- After `cluster_vms.yml` has fetched `/cluster/resources` once, the per-VM `proxmox_vm_info ... vmid:` loop on localhost does, per VM: `GET /version`, `GET /cluster/resources`, `GET /nodes/{node}/qemu` (the node's full VM list), `config`, then the agent call. Five requests and a module spawn per VM, serially.
- Measured: 0.21 s/VM on a small cluster, 0.44 s/VM at 222 guests. On a real PVE where `/cluster/resources` costs 100-300 ms, a 20-VM discovery is 10-20 s before configure starts. New relative to main, which had a static inventory.

**Failure scenario.** `make configure TAGS=claude_code` on 20 VMs spends longer discovering than the tag's work takes; `make list` on a busy homelab cluster is noticeably slower than `qm list`.

**Fix.** The listing already carries `node` and `vmid`. Replace the two loops with `uri` calls to `/nodes/{{ node }}/qemu/{{ vmid }}/config` and `.../agent/network-get-interfaces` (two requests per VM, same `proxmox_access_denied` check on `status`), optionally fanned out as a per-VM play like `provision.yml`. Keep `cluster_vms.yml` as the single listing.

**Resolution (fixed).** Discovery and `make list` no longer call `proxmox_vm_info` per VM. `playbooks/tasks/vm_addresses.yml`, shared by both, makes two `uri` requests per VM - `/nodes/<node>/qemu/<vmid>/config` and `.../agent/network-get-interfaces` - to the node the listing already names, skipping the agent call for a VM that is not running and both calls for one on a node that is down. A new `guest_addresses` filter (unit-tested) pairs the listing entries with the replies through the same MAC-matching rule as before, so the docker bridge is still never picked. A 401/403 in any reply fails the run with the VM named and the privilege spelled out; everything else leaves that one VM without an address. Five requests and a module spawn per VM became two requests and none.

<a id="p2-4"></a>
### P2-4. The three 'does the template exist' checks disagree on what 'exist' means, and none checks the node

- **Area:** Provisioning, Docs
- **Found by:** Template/CI
- **Confidence:** Confirmed by reading `deploy.yml:31-47`, the template role's probe and `proxmox_kvm.py` (clone POSTs to `nodes(<proxmox_vm_node>)`)
- **Lives:** branch commit — In one of the five unmerged commits (94c457e..1b2a739)
- **Status:** Fixed
- **Where:** `deploy.yml:31-47`, `roles/proxmox_template/tasks/main.yml:2-25`, `roles/proxmox_vm/tasks/present.yml:3-13`, `README.md:113-126`

**What is wrong**

- `deploy.yml` looks the template up by name cluster-wide and tests only `template`, never `node`. `proxmox_kvm` resolves the template's VMID cluster-wide and then POSTs the clone to `proxmox_vm_node`, which Proxmox rejects when the config lives elsewhere.
- The template role's probe is `qm config <vmid>` on the SSH target, which exits 2 for a VMID that exists on a sibling node, so the `when: rc != 0` block runs `qm create 9000` and Proxmox refuses with 'config file already exists'.
- README documents the constraint ('must be the node that holds the template') but nothing enforces it, and `hosts.yml`'s SSH target is never cross-checked against `proxmox_node`.

**Failure scenario.** Template built on node B, `proxmox_node: A`. `make deploy` prints 'Template is already built, so this run needs no SSH', then every clone fails with `Configuration file 'nodes/A/qemu-server/9000.conf' does not exist`, right after the playbook said the template exists. `make template` against A fails with 'config file already exists'.

**Fix.** In `deploy.yml`, after the lookup, assert the template's `node` equals `proxmox_node` with a message naming both (the data is already in the register). In the role, treat a `qm config` rc 2 whose stderr names another node's path as 'VMID exists on node X' rather than falling into the build.

**Resolution (fixed).** The `qm config` stderr could not be made to carry the answer: `PVE::AbstractConfig::load_config` dies with the *local* node's path (`nodes/<this node>/qemu-server/<vmid>.conf does not exist`) whatever node holds the VMID, so the stderr shape was not used. Instead `roles/proxmox_template/tasks/main.yml` reads pmxcfs's cluster-wide `/etc/pve/.vmlist` (what `PVE::Cluster::get_vmlist` reads) when the probe fails, and asserts the VMID has no `node` there, failing with "VMID 8000 exists on node pve2, not on <inventory host>, where qm cannot see it. Build the template on that node (point inventory/hosts.yml at it) or choose another proxmox_template_vmid." - before the download and `virt-customize`, where the old run spent its minutes before `qm create` refused. The arg spec's description says what is skipped and what is refused. The Molecule scenario's `prepare.yml` writes a fake `/etc/pve/.vmlist` placing 8000 on `pve2` (the same second node `fake_pve_api.py` models), a new `side_effect.yml` includes the role with `proxmox_template_vmid: 8000` inside block/rescue and asserts the refusal names 8000 and `pve2` and that no `8000.conf` was created, and `verify.yml` asserts the fake `qm` saw exactly one `config 8000` probe. The assert logic was exercised locally with ansible-core against a free VMID, a sibling-held VMID and an empty `ids` map (which caught an eagerly-rendered `fail_msg` indexing a missing entry); the Molecule scenario itself has not been run. `deploy.yml` now asserts, when the template exists, that the node holding it is `proxmox_node`, naming both nodes and the two ways out (set `proxmox_node`, or build a template on that node with its own VMID and name), before reporting what the run will do. The provision scenario runs `deploy.yml` with `proxmox_node: pve2` as a child process and asserts it fails with that message and never reports the template as built.

<a id="p2-5"></a>
### P2-5. The `/cluster/nextid` request is `no_log` with no error path, and duplicates the one-place request pattern

- **Area:** Provisioning, Conventions
- **Found by:** Conventions
- **Confidence:** Confirmed by reading `provision.yml:93-106` and `cluster_vms.yml:27-43`
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `playbooks/provision.yml:93-106`, `playbooks/tasks/cluster_vms.yml:17-22, 27-43`, `ARCHITECTURE.md:208-211`

**What is wrong**

- `cluster_vms.yml` hides its request and then checks the outcome with `failed_when: false` plus an assert that prints status and reason. The nextid task has `no_log: true` and nothing else: on a 401, a 595 from a node that is down or a TLS failure the user sees only 'the output has been hidden due to the fact that no_log: true was specified'.
- The `PVEAPIToken=...` header and `ca_path` construction are now written twice. CLAUDE.md says the shared request pieces live in one place so the playbooks cannot drift. ARCHITECTURE §4.2 says 'an assert reports a failed listing', which is true for the listing only.

**Failure scenario.** `make provision VM_NAME=a,b` on a host whose token or CA setup is wrong fails at 'Ask Proxmox where to start numbering' with a censored message; the hint `cluster_vms.yml` gives for exactly that case is bypassed. A later change to the header or `ca_path` in one file silently leaves the other behind.

**Fix.** Give the nextid task `failed_when: false` and an assert on `status == 200` mirroring `cluster_vms.yml`'s message; factor the header and `ca_path` into shared vars (`proxmox_api_headers`, `proxmox_api_ca_path`) beside `proxmox_api_url` in `defaults.yml` and use them from both tasks. Then make §4.2 true.

**Resolution (fixed).** `proxmox_api_headers` and `proxmox_api_ca_path` in `inventory/group_vars/all/defaults.yml` are now the one definition of how a raw request is authenticated and verified; `cluster_vms.yml`, the nextid read and the new `vm_addresses.yml` all use them. The nextid task is `failed_when: false` and followed by an assert that reports the status and reason, as the listing's does. ARCHITECTURE §4.2 says so.

<a id="p2-6"></a>
### P2-6. ARCHITECTURE.md still documents the fact-cache eviction P3-1 removed and the `gather_facts` explanation P3-8 corrected

- **Area:** Docs
- **Found by:** Conventions, Template/CI
- **Confidence:** Confirmed by grep: `absent.yml` has no cache task; §4.1 still credits `gather_facts: false`
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `ARCHITECTURE.md:147-150 (§4.1)`, `ARCHITECTURE.md:393-404 (§5.2)`, `ARCHITECTURE.md:808 (§9)`

**What is wrong**

- §5.2 says `absent.yml` ends with 'delete the VM's jsonfile fact cache entry' and §9 says stale facts are 'handled in `absent.yml`'. The task was deleted (P3-1); `roles/proxmox_vm/vars/main.yml:71-73` says the opposite, and §4.5 already says `make destroy` clears `.cache/facts` wholesale. The document contradicts itself.
- §4.1 still says 'the play gathers no facts - so a run that does not need to build it never opens an SSH connection', the exact mechanism P3-8 corrected in §4.7 and in `playbooks/template.yml:7-11` (it is the import-level `when:`).

**Failure scenario.** ARCHITECTURE.md calls itself the source of truth when documents disagree. A contributor either re-adds the broken per-host cache task, relies on an eviction that never happens from `ansible-playbook playbooks/destroy.yml`, or 'preserves' `gather_facts: false` as load-bearing.

**Fix.** Reword §4.1 to match §4.7. In §5.2 and §9 replace the fact-cache sentences with: the Makefile's `destroy` target removes `.cache/facts` wholesale; the role does not touch the cache because the jsonfile backend prefixes entries, so a per-host path never matched.

**Resolution (fixed).** §4.1 now credits the import-level `when:` (which skips the implicit gather with every other task) and demotes `gather_facts: false` to "saves a pointless gather on the run that builds", with a link to §4.7. §5.2's `absent.yml` paragraph describes one housekeeping step, `known_hosts`, and states why the role leaves the cache alone (the jsonfile backend's internal prefix meant the per-host path never matched) and who clears it (`make destroy`, wholesale, after the playbook; a bare `ansible-playbook playbooks/destroy.yml` does not). §9's "Stale host keys and stale facts" says the same in one line. §4.5's "`make destroy` also removes `.cache/facts`" was already right and now agrees with both. A grep of the file for "fact cache", "gathers no facts" and "evict" finds no other claim of per-VM eviction.

<a id="p2-7"></a>
### P2-7. No test runs the role against a VM whose NIC cannot be read; dropping the assert silently publishes the docker bridge

- **Area:** Tests
- **Found by:** Tests
- **Confidence:** Reproduced by tracing: with the assert deleted, `guest_ipv4(interfaces, '')` returns the fake's `docker0` address
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/tasks/present.yml:154-164`, `roles/proxmox_vm/vars/main.yml:19-23`, `filter_plugins/proxmox.py:59`, `molecule/provision/verify.yml:27, 34, 204-206`

**What is wrong**

- `nonic`/`netone` are only discovered and listed, never provisioned. With the 'Fail when the VM's NIC could not be read' assert removed, `proxmox_vm_found_mac` is `''`, `guest_ipv4` gets `wanted = None` and returns the first non-`lo` IPv4, which in the fake's agent reply is `docker0`'s `172.17.0.1`. `proxmox_vm_address` is non-empty and the address assert passes.
- The fake lists `docker0` first precisely to catch this, but only the MAC-matching path is ever exercised.

**Failure scenario.** A template with its LAN NIC on `net1` and `proxmox_nic` left at `net0`: provisioning 'succeeds', publishes `172.17.0.1`, and `configure` hangs on `wait_for_connection` with no hint.

**Fix.** Side-effect case: run the role with `proxmox_vm_name: claude-dev` and `proxmox_vm_nic: net9`; rescue; assert the message contains `Could not read a MAC address` and `set proxmox_vm_nic`; assert zero `/agent/network-get-interfaces` polls for 9001 after the marker.

**Resolution (fixed).** A side-effect play runs the role against the running `claude-dev` with `proxmox_vm_nic: net9`, asserts the refusal names `net9` and says to set `proxmox_vm_nic`, and - by reading the fake's request log before and after - that the guest agent was never asked. Removing the assert now publishes the docker bridge and fails the scenario.

<a id="p2-8"></a>
### P2-8. The 'fail fast on 403' wiring is never exercised, because the fake never returns 403

- **Area:** Tests, Fakes
- **Found by:** Tests
- **Confidence:** Confirmed: the fake's auth is binary (`fake_pve_api.py:170-176`); no scenario produces a 403
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/tasks/present.yml:179-181`, `playbooks/discover.yml:66`, `playbooks/list.yml:41`, `tests/molecule/fake_pve_api.py:170-176`, `tests/unit/test_filters.py:190-220`

**What is wrong**

- `proxmox_access_denied` is unit-tested on strings only. Mutating `discover.yml:66` to `failed_when: false`, or deleting the `or proxmox_access_denied` clause in `present.yml:181`, passes every scenario. The provision scenario covers only the false-positive direction (VM 4013).

**Failure scenario.** A token with `VM.Audit` but not the guest-agent privilege (the README's own recipe went wrong this way on PVE 9, P1-3): `make configure` fleet-wide skips every VM and exits 0 with 'none reported an address yet'; `make provision` waits the full 60 x 5 s per VM before failing.

**Fix.** Give the fake a second token id (e.g. `noagent`) that is accepted everywhere except `/agent/*`, where it answers 403 `Permission check failed (/vms/<id>, VM.Monitor)`. Verify: a child `discover.yml` run with that token exits non-zero with `Permission check failed` and exactly one agent poll per VM; a role side-effect asserts the same single poll.

**Resolution (fixed).** The fake accepts a second token id, `noagent`, everywhere except at `/agent/*`, where it answers 403 `Permission check failed (/vms/<id>, VM.GuestAgent.Audit)` as Proxmox does, and logs which token made each request. The role scenario runs the role with that token against `claude-dev`, asserts the failure names the privilege, and that exactly one agent poll was made (no retries). The provision scenario runs `discover.yml` with it as a child process and asserts it failed naming the privilege, did not print "No VMs to configure", and polled each running VM's agent once.

<a id="p2-9"></a>
### P2-9. Re-running configure against a live Remote Control server is never tested, so a regression that restarts it every run ships green

- **Area:** Tests, Remote Control
- **Found by:** Tests
- **Confidence:** Reproduced by tracing: unconditional `state: restarted` passes every stage because the PID is recorded after the restart
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `roles/claude_code/tasks/remote_control.yml:208-222, 239-242`, `roles/claude_code/molecule/default/side_effect.yml:219-241`

**What is wrong**

- Side-effect step 4 starts the server once and step 5 removes it; the scenario's idempotence stage runs with an API key (Remote Control off) and the configure scenario's idempotence runs ineligible (no login). No stage runs the role twice with the server up.
- `claude_code_remote_control_started` is recorded *after* the (re)start, so the stay-up check compares a new PID with itself.

**Failure scenario.** Every `make configure` kills the user's live sessions (`KillSignal=SIGINT`), which is exactly the fleet-wide re-run the branch encourages.

**Fix.** In side-effect step 4, after the live assert, record `ExecMainPID`/`NRestarts`, include the role again unchanged, assert both unchanged; then include it with `claude_code_remote_control_permission_mode: acceptEdits` and assert the PID and the unit text changed.

**Resolution (fixed).** `roles/claude_code/molecule/default/side_effect.yml` gained step 4b after the live assert: it includes the role again with unchanged inputs and asserts `ExecMainPID` and `NRestarts` equal the values read before, and that the role's own `claude_code_remote_control_start` register is neither changed nor skipped; then includes it with `claude_code_remote_control_permission_mode: acceptEdits` and asserts the unit is active/running on a new PID, the start task reported changed, and the unit text carries `--permission-mode acceptEdits`. Nothing later depends on the mode: step 5 removes the unit. Verified by yamllint, ansible-lint and syntax; the scenario itself was not run (Docker disk ~99% full) and is for the coordinator to run.

<a id="p2-10"></a>
### P2-10. The `settings.json` blocker preflight (P3-6's fix) has no test at all

- **Area:** Tests, Remote Control
- **Found by:** Tests
- **Confidence:** Confirmed: no test seeds a `settings.json` with any blocker
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `roles/claude_code/tasks/remote_control.yml:21-52`, `roles/claude_code/vars/main.yml:13-43`, `tests/unit/test_remote_control_playbooks.py`

**What is wrong**

- Deleting the assert, or flipping `| length == 0` to `>= 0`, passes. The `ANTHROPIC_BASE_URL` host check via `urlsplit('hostname')` is never evaluated against `https://api.anthropic.com/`, a port/path variant, or `http://proxy.internal:8080`.

**Failure scenario.** A VM whose `settings.json` has `DISABLE_TELEMETRY` or an `apiKeyHelper` gets a server that starts, stays active, passes the stay-up check, and never appears in the app: the silent failure the preflight exists to name.

**Fix.** Extend `test_remote_control_playbooks.py`'s check-mode harness (it already points `claude_code_home` at a tmp dir): parametrize seeded `settings.json` contents (each blocking env key, `apiKeyHelper`, good and bad `ANTHROPIC_BASE_URL` hosts), include `tasks_from: remote_control`, and assert the failure message lists exactly the offending names and that `https://api.anthropic.com:443/v1` passes.

**Resolution (fixed).** `tests/unit/test_remote_control_playbooks.py`'s check-mode run is now a module-scoped `check_mode` fixture (one `ansible-playbook` run, about 5 s) over `ready`, `not-ready` and one signed-in host per seeded `settings.json` in `SETTINGS`: each key of the role's `claude_code_remote_control_blocking_env` (read from `vars/main.yml`, so a new key is tested automatically), `apiKeyHelper`, `ANTHROPIC_BASE_URL` at `https://api.anthropic.com`, `https://api.anthropic.com/` and `https://api.anthropic.com:443/v1` (pass), `http://proxy.internal:8080` (blocked), a clean file, and one with everything at once. `test_settings_that_disable_remote_control_are_named_rather_than_fatal` asserts, per host, that the blocker message lists exactly the expected names, mentions `claude_remote_control: false`, and suggests no login step — or, for the good hosts, that no message is printed at all; the fixture asserts rc 0 for every host, which is the P2-1 behaviour. The interpreter wrapper now sets `HOME` to the host's stand-in directory, so `login.yml`'s new settings read sees the seeded file and never the developer's own, and a `claude-ai-telemetry` login case covers `make claude-login`. 32 tests in the file, 10 s.

<a id="p2-11"></a>
### P2-11. Fleet VMID pre-allocation's 'skip VMIDs already in use' branch is never load-bearing, and the pinned-VMID refusal is untested

- **Area:** Tests, Fakes, Provisioning
- **Found by:** Tests
- **Confidence:** Confirmed: `fake_pve_api.py:185-186` returns `max(VMS) + 1`; taken IDs are always below it
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `playbooks/provision.yml:36-41, 81-91`, `tests/molecule/fake_pve_api.py:185-186`, `molecule/provision/verify.yml:267-286`

**What is wrong**

- Proxmox's `/cluster/nextid` returns the lowest free ID; the fake returns `max + 1`, with 8000 placed 'below 9000 so nextid still hands out 9001'. So `reject('in', claude_vm_taken_ids)` never removes anything, and deleting it passes converge and verify.
- No scenario passes `proxmox_vm_id` with two new names, so 'Refuse one pinned VMID for several new VMs' has no test.

**Failure scenario.** Real PVE: `nextid` = 100 with 101 in use; a fleet of two is numbered 100, 101 without the `reject`, and the second clone fails 'changed nothing' (safe thanks to the collision assert, but the headline fleet feature breaks on any cluster with a gap). A regressed pinned-ID refusal creates VM 1 and fails VM 2 part-way.

**Fix.** Make the fake return the lowest free ID >= 100 as PVE does, seed a taken ID inside the range (clone 9002 in `prepare`), and assert the fleet's `newid`s skip it. Add a child `provision.yml` run with `{"vm_name": "x,y", "proxmox_vm_id": 9500}` asserting rc != 0, the message, and no `/clone` for either.

**Resolution (fixed).** The fake's `/cluster/nextid` now returns the lowest free VMID above a floor (`NEXTID_LOWER`, standing in for the datacenter's next-id range), as Proxmox does. The provision scenario's converge first clones a VM onto 9002, so the fleet's second VMID must skip it: alpha is 9001 and beta 9003, which the converge, clone-request and listing assertions all pin. A child `provision.yml` run with `vm_name: ex,why` and `proxmox_vm_id: 9500` is asserted to fail with the shared-VMID refusal and to have sent no clone for either name. The role scenario's `verify.yml` reads 9001's history only up to the DELETE that ended `claude-dev`, since the lowest-free numbering now hands that VMID to the next unpinned clone.

<a id="p2-12"></a>
### P2-12. The Makefile's safety guards have no test; a regression to `-e vm_name=$(VM_NAME)` would ship green

- **Area:** Tests, Build
- **Found by:** Tests
- **Confidence:** Confirmed: `tests/unit/` only runs pytest; nothing invokes `make`
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `Makefile:22-37, 96-100, 119-122`

**What is wrong**

- `vm-name-check`, the JSON `VM_ARGS` and `deploy`'s refusal of `TAGS` all work today (verified by hand), but nothing in `make unit` or CI runs them. P3-3's fix (quote breaks out of the single-quoted JSON) could be reverted without a failing test.

**Failure scenario.** A refactor of the regex or of `VM_ARGS` silently re-opens the quoting hole or splits `alpha,beta` into two extra-vars.

**Fix.** `tests/unit/test_makefile.py`: run `make vm-name-check VM_NAME=...` for an accept list and a reject list (space, quote, `;`, `$(...)`, newline), `make -n provision VM_NAME=alpha,beta` asserting the exact `-e` JSON argument, and `make deploy TAGS=claude_code` asserting rc != 0 with the 'takes no TAGS' line and no `ansible-playbook` in the output.

**Resolution (fixed).** `tests/unit/test_makefile.py` (17 tests, skipped cleanly when `make` is absent) runs the real Makefile from the repository root: `vm-name-check` accepts `alpha`, `alpha,beta`, `a.b-c_d` and empty, and refuses a space, both quotes, `;`, `$(x)`, a backtick, a tab, an embedded newline and `ålpha`, each with the "VM_NAME may contain only" line; `make -n provision VM_NAME=alpha,beta` prints exactly `-e '{"vm_name": "alpha,beta"}'` and `make -n provision` prints no `-e '{"vm_name"` at all; `make provision VM_NAME=a;b` and `make deploy TAGS=claude_code` exit non-zero with their refusal lines and no `ansible-playbook` in the output. `-n` does not run shell lines, so those two run `make` for real with `VENV` pointed at an empty directory and `VAULT_FILE` at a missing file, which makes a regression fail loudly rather than reach a hypervisor. Two cases failed against the Makefile as found and drove fixes: the newline (P3-6) and `$(x)`, which make expanded to nothing before the check saw it, so `make provision VM_NAME='$(x)'` silently provisioned the default VM - the check now reads `$(value VM_NAME)`. The `unit` help text, CONTRIBUTING's `make unit` row and README's two mentions of `tests/unit/` now list what the suite runs.

<a id="p2-13"></a>
### P2-13. Root SSH on the hypervisor allows password authentication

- **Area:** Host, Security
- **Found by:** Host audit
- **Confidence:** Confirmed on the host (`sshd -T`, `passwd -S root`)
- **Lives:** host — On the Proxmox host, not in the repository
- **Status:** Fixed
- **Where:** `host: /etc/ssh/sshd_config:33 (`PermitRootLogin yes`), effective `PasswordAuthentication yes``

**What is wrong**

- Root has a password set, sshd listens on `0.0.0.0:22` and `[::]:22`, and password login for root is enabled. Key auth is already in place (3 authorized keys; the audit itself used it), so the password path is only an exposure.

**Failure scenario.** None for the make targets. A brute-force surface on the hypervisor's root account; the LAN is private, which lowers but does not remove it (OPNsense's sshguard protects the gateway, not this host).

**Fix.** After confirming key login from the controller: `printf 'PermitRootLogin prohibit-password\nPasswordAuthentication no\n' > /etc/ssh/sshd_config.d/10-hardening.conf && sshd -t && systemctl reload ssh`. Keep `prohibit-password`, not `no`: `make template` connects as root.

**Resolution (fixed).** A drop-in `/etc/ssh/sshd_config.d/10-hardening.conf` on the host sets `PermitRootLogin prohibit-password` and `PasswordAuthentication no`; `sshd -t` passed and the service was reloaded. Verified on a fresh key-authenticated connection: `sshd -T` reports `permitrootlogin without-password` and `passwordauthentication no`, and `ssh` is active. Applied by hand as the README's host-preparation steps are; it is host bootstrap, not project state.

## P3: Minor correctness, cleanup, staleness or a small test gap

<a id="p3-1"></a>
### P3-1. The commit-history cleanup (old P3-12) is now mostly impossible: 29 of the 34 commits are already on `main`

- **Area:** Conventions, Commits
- **Found by:** Conventions
- **Confidence:** Confirmed with `git merge-base --is-ancestor`
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `git log origin/main..HEAD`

**What is wrong**

- Local `main` is stale at 6167131. `origin/main` is 9f4aec1, the merge of PR #6 (2026-10-03), which contains everything up to d09d82a. The four trailer-less commits (3ee7563, 7364bfd, 65215e4, 7fa94d1) and the fixups d0bb894, 4e427a2, ed41205 are merged and cannot be rewritten without rewriting `main`.
- Still unmerged: 94c457e, 86e8e83, 2c3b20b, d4f1471, 1b2a739. Of these, 86e8e83 is a fixup of 11c6454 (merged) and e6d1075's body (merged) describes behaviour 94c457e reversed, so a squash of 94c457e into it is no longer possible either.
- The untracked `docs/review-*.md` files are not in ARCHITECTURE §14's file map and would be committed with the working tree as-is.

**Failure scenario.** None at runtime. `git bisect` on `main` already lands on commits that delete unowned VMs; that is history now.

**Fix.** `git fetch` and rebase the five unmerged commits plus the working tree onto `origin/main`; commit the fixes as their own Conventional Commits. Drop the rest of the old P3-12 plan. Decide whether `docs/review-*.md` belong in the repository (and add them to §14 if so).

**Resolution (fixed).** Local `main` was fast-forwarded to `origin/main` (9f4aec1). The working tree was then committed on top of the five unmerged commits as fourteen Conventional Commits, in dependency order so the history reads as a flow: the CI guard and secret scans; the filters; the fake Proxmox API; ansible.cfg; the shared inventory pieces; the proxmox_vm role; the playbooks; the template role; the claude_code role; the two Molecule scenarios; the Makefile; the docs; and the review documents themselves (`docs/`, now listed in both file maps). Every commit has a *why* body and the attribution trailers after a blank line. lint, syntax and the 204 unit tests pass on the result, and no local file is tracked. The trailer-less and fixup commits the original finding named are already on `main` and were left alone.

<a id="p3-2"></a>
### P3-2. `roles/proxmox_vm/vars/main.yml`'s header justifies the lazy-var rule with a `destroy.yml` loop that no longer exists

- **Area:** Conventions
- **Found by:** Conventions
- **Confidence:** Confirmed by reading
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/vars/main.yml:7-11`, `playbooks/destroy.yml:13-17`, `ARCHITECTURE.md:571-573`

**What is wrong**

- The comment says 'destroy.yml runs this role in a loop'. destroy.yml is two plays, one host per VM. The real reason (the role scenario's `side_effect.yml` includes the role back to back) is already in CLAUDE.md and ARCHITECTURE §6.3.

**Failure scenario.** A contributor checks destroy.yml, sees no loop, concludes the 'do not convert to set_fact' rule is obsolete, converts, and breaks the side-effect scenario in exactly the way the comment was meant to prevent.

**Fix.** Replace the sentence with the side_effect.yml justification.

**Resolution (fixed).** `roles/proxmox_vm/vars/main.yml`'s header now gives the real reason: the role can run more than once for the same host in one play (the role scenario's `side_effect.yml` includes it back to back), a `set_fact` survives a run that skips it, and a skipped register is overwritten. The `destroy.yml` loop is no longer mentioned. ARCHITECTURE §6.3 says the same, with the table extended for the vars P3-16, P3-18 and P3-21 added.

<a id="p3-3"></a>
### P3-3. CLAUDE.md, ARCHITECTURE.md and the code disagree on how many facts `present.yml` publishes

- **Area:** Docs
- **Found by:** Conventions
- **Confidence:** Confirmed by reading the four `set_fact`s
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `CLAUDE.md:102-105`, `ARCHITECTURE.md:206, 580-582`, `roles/proxmox_vm/tasks/main.yml:76-79`, `roles/proxmox_vm/tasks/present.yml:43-46, 130-136, 188-190`, `playbooks/provision.yml:153-172`

**What is wrong**

- Code sets four facts (`proxmox_vm_exists`, `proxmox_vm_resolved_id`, `proxmox_vm_stopped`, `proxmox_vm_address`); provision.yml's publish play reads three through `hostvars`. CLAUDE.md lists two, ARCHITECTURE §6.3 lists three and omits `proxmox_vm_stopped`, the one whose no-`when:` design CLAUDE.md explains.

**Failure scenario.** Someone adds a `when:` to the `resolved_id` set_fact (not on CLAUDE.md's 'outputs' list) and the publish step reads a stale VMID for the next VM of a fleet.

**Fix.** State once in both files: four facts, of which `address`, `stopped` and `resolved_id` are read by `provision.yml`, all set with no `when:`.

**Resolution (fixed).** CLAUDE.md's "lazily-evaluated internals" paragraph and ARCHITECTURE §6.3 now both say: four facts (`proxmox_vm_exists` and `proxmox_vm_resolved_id` in `main.yml`, `proxmox_vm_stopped` and `proxmox_vm_address` in `present.yml`), each set unconditionally on every run (`present.yml` re-sets the ID after a clone, the one time it changes), of which `provision.yml`'s third play reads `proxmox_vm_address`, `proxmox_vm_stopped` and `proxmox_vm_resolved_id` through `hostvars`. The §4.2 sequence diagram already listed those three and is unchanged.

<a id="p3-4"></a>
### P3-4. The Makefile comment misdescribes how far `SKIP` propagates, and `make -j test` is not prevented

- **Area:** Conventions
- **Found by:** Conventions, Template/CI
- **Confidence:** Reproduced with a throwaway Makefile on GNU Make 3.81
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `Makefile:186-193`

**What is wrong**

- 'SKIP is exported to the pre-commit prerequisite (and only there)' is wrong: a target-specific `export` applies to every prerequisite `test` builds (lint, syntax, unit, all molecule targets). Harmless today because only pre-commit reads `SKIP`.
- There is no `.NOTPARALLEL`, so `make -j test` runs six role scenarios and two playbook scenarios concurrently in Docker, against the project's own guidance to serialise them.

**Failure scenario.** A future prerequisite that reads `SKIP` behaves differently under `make test`, and the comment sends the debugger the wrong way; `make -j` fills the Docker disk.

**Fix.** Reword to 'exported to every prerequisite of `test`; only pre-commit reads it', or pass `SKIP=$(PRE_COMMIT_SKIP) $(MAKE) pre-commit` in the recipe. Add `.NOTPARALLEL:` for `test`.

**Resolution (fixed).** The `test` comment now says a target-specific `export` reaches every prerequisite of `test` and that only pre-commit reads `SKIP`; `.NOTPARALLEL:` is declared beside it, with the reason (one Docker disk, scenarios serialised on purpose), so `make -j test` runs the Molecule scenarios one at a time. The recipe and prerequisites are unchanged. Verified: `make -n -j4 test` lists the same prerequisites in the same order, and `make lint`/`make unit` pass.

<a id="p3-5"></a>
### P3-5. `--permission-mode` is interpolated into `ExecStart` unquoted while `--name` is quoted

- **Area:** Remote Control, Conventions
- **Found by:** Conventions
- **Confidence:** Confirmed by reading the template
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `roles/claude_code/templates/claude-remote-control.service.j2:27`, `README.md:526-528`

**What is wrong**

- README says the mode 'is passed to Claude Code unvalidated' and `argument_specs` deliberately declines `choices`, yet the template does `--name {{ ... | quote }} --permission-mode {{ mode }}`. systemd splits `ExecStart` on whitespace, so `accept edits` becomes two argv entries and the server fails for a reason the journal hint does not name.

**Failure scenario.** A mode containing a space produces 'did not stay up' with a stray argument rather than Claude Code's own rejection message.

**Fix.** Apply `| quote` to the mode (and `--spawn`, for consistency).

**Resolution (fixed).** `roles/claude_code/templates/claude-remote-control.service.j2` now renders `--permission-mode {{ ... | quote }} --spawn {{ ... | quote }}`, with a comment above `ExecStart` saying why every value is quoted and that the mode is still not validated here. `quote` is `shlex.quote`, which leaves a plain word alone (`acceptEdits`, `same-dir`) and single-quotes anything with a space, so the P2-9 side-effect assertion `'--permission-mode acceptEdits' in <unit>` still holds and a mode such as `accept edits` now reaches Claude Code as one argument, to be rejected with its own message. Verified by `make lint`, `make syntax` and the check-mode harness (which templates the unit); the `claude_code` Molecule scenario was not run (apt mirror).

<a id="p3-6"></a>
### P3-6. `vm-name-check` is line-oriented, so a VM_NAME with an embedded newline passes

- **Area:** Build, Security
- **Found by:** Template/CI
- **Confidence:** Reproduced: `make vm-name-check "VM_NAME=$(printf 'alpha\nbeta')"` exits 0
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `Makefile:28, 96-100`

**What is wrong**

- `grep -E` matches each line separately, so a multi-line value passes if every line is clean, and the newline reaches the JSON `-e` payload. No shell breakout (it stays inside the single quotes), but YAML folds it to `alpha beta`. Tab, quote, `;`, space and non-ASCII are correctly rejected.

**Failure scenario.** `make destroy` or `make configure` silently targets a name nobody typed; the Makefile comment's promise that anything that could reshape the payload is rejected is not quite true.

**Fix.** Use a non-line-based test: `case "$$VM_NAME_CHECK" in *[!A-Za-z0-9._,-]*) ...; exit 1;; esac` (keep `LC_ALL=C`), or `grep -qzE`.

**Resolution (fixed).** `vm-name-check` is now the `case` form, which tests the whole value, and exports `LC_ALL=C` to the recipe as a target-specific variable - needed, because under a UTF-8 locale the shell's `A-Z` range collated `ålpha` as clean, which the old `LC_ALL=C grep` had been hiding. `tests/unit/test_makefile.py` holds the newline, tab and non-ASCII cases (P2-12); the newline case failed against the old recipe.

<a id="p3-7"></a>
### P3-7. The fact cache survives a failed `make destroy` and a VM deleted outside it, and can hand a rebuilt VM the old VM's facts

- **Area:** Build, Provisioning
- **Found by:** Template/CI, Performance
- **Confidence:** Confirmed by reading the Makefile and ansible.cfg
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `Makefile:125-127`, `ansible.cfg:14-18`, `roles/dev_tools/tasks/docker.yml:6-7`, `roles/dev_tools/tasks/uv.yml:17, 30`

**What is wrong**

- `rm -rf .cache/facts` is a second recipe line, so make skips it whenever destroy.yml exits non-zero, including a partial fleet delete (one failed host exits 2 after the others were deleted).
- Facts are keyed by inventory name with `gathering = smart` and a 3600 s jsonfile cache. A VM deleted in the Proxmox UI and re-provisioned under the same name within the hour is configured with the old VM's `ansible_distribution_release`/`ansible_architecture`. Only bites when the template changed release or architecture in between; the cache's benefit is one `setup` per host (~2-4 s, parallel).

**Failure scenario.** Switch `proxmox_template_name` to a different Ubuntu release, delete `alpha` in the UI, `make deploy VM_NAME=alpha` within the hour: Docker's apt `suites:` is the old release.

**Fix.** Either drop `fact_caching` (the saving is negligible in a run that takes minutes), or run the eviction regardless of exit status (`...; rc=$$?; rm -rf .cache/facts; exit $$rc`) and also in `provision`/`deploy`.

**Resolution (fixed).** The fact cache is gone: `ansible.cfg` drops `gathering`, `fact_caching`, `fact_caching_connection` and `fact_caching_timeout` (implicit gathering, one `setup` per VM per configure run; the comment there says why), the Makefile's `destroy` target no longer removes `.cache/facts`, and the README layout, ARCHITECTURE §2 (table and diagram), §4.5, §5.2, §9, §13, §14, SECURITY.md and the `proxmox_vm_forget_address` comment in `roles/proxmox_vm/vars/main.yml` no longer describe it. §13 records the reasoning: only `dev_tools` reads a gathered fact, the gather costs seconds in a run that takes minutes, and an entry keyed by inventory name outlived the VM it described. `.gitignore` keeps `.cache/` because the lint configs exclude it and `make clean` removes it. Verified: `.venv/bin/ansible-config dump --only-changed` shows no gathering or caching key and `DEFAULT_FORKS = 20`; `make syntax` passes; `grep -rn 'cache/facts\|fact_caching\|jsonfile'` finds nothing outside this document.

<a id="p3-8"></a>
### P3-8. The template build leaves ~1.2 GB of images in `local`'s ISO directory, where the UI lists them as ISO images

- **Area:** Template, Build
- **Found by:** Template/CI
- **Confidence:** Confirmed by reading the role
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `roles/proxmox_template/tasks/main.yml:38-47, 75-82, 128-136`, `roles/proxmox_template/defaults/main.yml:9`

**What is wrong**

- Both the pristine download and the customised copy are kept after `import-from` has copied the disk, and nothing removes them. `proxmox_template_image_dir` is `/var/lib/vz/template/iso`, the `local` storage's `iso` content directory, so both `.img` files show up as ISO images in the UI. The 'left untouched so get_url stays idempotent' rationale only matters within one failed-and-retried build. Related, not a bug: no `format=` on the import, so on dir/NFS storage the template disk is raw while full clones are qcow2.

**Failure scenario.** 1.2 GB on the root filesystem of every node a template is built on, forever, plus two confusing ISO entries.

**Fix.** Remove the import copy after `qm template` (and optionally the download), or point `proxmox_template_image_dir` at a non-content path such as `/var/lib/vz/claude-on-proxmox`.

**Resolution (fixed).** `proxmox_template_image_dir` defaults to `/var/lib/vz/claude-on-proxmox`, which is not a content directory of any storage, so the download no longer appears as an ISO; the existing `file` task creates it `0755` before `get_url`. After `qm template` a new task removes the customised copy (`proxmox_template_import_path`), guarded by `when: proxmox_template_import_path != proxmox_template_image.dest` so a build without the guest agent, whose import path is the download itself, keeps it. The download stays for `get_url`'s idempotence. `defaults/main.yml`, `meta/argument_specs.yml`, README (Upgrading) and ARCHITECTURE §5.1 describe the new path and the cleanup; the Molecule verify asserts the download exists under the new directory (mode `0755`), the copy does not, and `import-from` and `virt-customize --add` used the new path. The `format=` question is left as the finding notes it: not added. Verified by `make lint`; the coordinator runs the `proxmox_template` scenario.

<a id="p3-9"></a>
### P3-9. The template role's one safety check, 'Refuse to reuse VMID', has no test

- **Area:** Tests, Template
- **Found by:** Template/CI
- **Confidence:** Confirmed: the scenario has no `side_effect.yml`
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `roles/proxmox_template/tasks/main.yml:15-25`, `roles/proxmox_template/molecule/default/`

**What is wrong**

- P1-10 closed the gap for `proxmox_vm` but this role's refusal is still unexercised. The fake `qm` already supports `create`/`set` without `template`, so a half-built VMID is one `prepare` step away.

**Failure scenario.** A future edit to the `stdout_lines` assertions (a `qm config` format change, a 'simplified' name check) ships green.

**Fix.** Add a `side_effect.yml` that creates VMID 9000 with the fake `qm` without converting it, includes the role, and asserts the 'is not the finished template' message; and one with a template of another name.

**Resolution (fixed).** `roles/proxmox_template/molecule/default/side_effect.yml` gains two plays after the sibling-node one: VMID 9002 is created with the fake `qm` as a plain VM (`qm create 9002 --name other`, never converted), the role is included with `proxmox_template_vmid: 9002` inside block/rescue, and the rescue asserts the message `VMID 9002 exists but is not the finished template "ubuntu-24.04-cloudinit"` with its `qm destroy 9002` hint; VMID 9003 is created and converted as `other-template`, and the same refusal is asserted for the name mismatch. Both plays then slurp the fake's config and fail if the role changed a line of it. `verify.yml` now counts calls per VMID: one `create`/`template` for 9000, one `create` each for 9002 and 9003 (the scenario's own), two `set` calls in all, two `template` calls in all (9000 and the scenario's 9003), and still one line in the `virt-customize` log, so a second build anywhere fails the scenario. The fake `qm` already supported `template`, so it was not extended. Verified by `make lint` (ansible-lint production profile on the scenario); the coordinator runs the scenario.

<a id="p3-10"></a>
### P3-10. `vault-check` guards the plaintext case only; a missing `vault.yml` falls through to an Ansible 'undefined variable' error

- **Area:** Build
- **Found by:** Template/CI
- **Confidence:** Reproduced on this machine (no vault.yml): `make vault-check` exits 0
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `Makefile:86-89`, `inventory/group_vars/all/defaults.yml:15`

**What is wrong**

- `test ! -f $(VAULT_FILE) || ...` passes when the file is absent, so `make list` runs and dies inside `cluster_vms.yml` with `'vault_proxmox_api_token_secret' is undefined`. This is the state of this controller today.

**Failure scenario.** A fresh clone where someone skipped `make init` gets an Ansible stack trace instead of 'run make init'.

**Fix.** Add `@test -f $(VAULT_FILE) || { echo "error: $(VAULT_FILE) missing. Run: make init"; exit 1; }` before the encryption test.

**Resolution (fixed).** `vault-check` tests `-f $(VAULT_FILE)` first and fails with `error: <VAULT_FILE> missing. Run: make init` before the encryption test. `tests/unit/test_makefile.py` now creates a stub vault that only looks encrypted for `_safe_overrides` (the old missing-path override would have stopped every playbook target at `vault-check`, so the name-check and TAGS tests still reach the guard they test), and adds four tests: an encrypted stub passes, a plaintext file is refused with the `make vault-encrypt` hint, a missing file is refused with the `make init` hint and not the encryption message, and `make list` with the vault missing stops with the hint before any `ansible-playbook`. Verified: `make unit` (204 passed).

<a id="p3-11"></a>
### P3-11. A nested `inventory/<dir>/proxmox.yml` inventory-plugin file passes both the guard and `.gitignore`

- **Area:** Security, CI
- **Found by:** Security
- **Confidence:** Reproduced with the guard on 50 adversarial paths
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `tests/check_no_local_files.sh:24`, `.gitignore:14`, `tests/unit/test_tracked_files.py:21-76`

**What is wrong**

- The rule for the `community.proxmox` inventory plugin's config (`^inventory/[^/]*proxmox\.ya?ml$`) is the only inventory rule still anchored to one depth; P1-2 made the hosts/ini rules depth-independent. `ansible.cfg` sets `inventory = inventory`, parsed recursively, so `inventory/prod/proxmox.yml` is loaded like the `inventory/prod/hosts.yml` P1-2 added to `MUST_BLOCK`, and it carries the Proxmox URL and usually `token_secret`.

**Failure scenario.** A second inventory dir `inventory/prod/` with a `proxmox.yml` is added, both guards stay green, and the address is in git even if gitleaks catches the token.

**Fix.** `'^inventory/(.+/)?[^/]*proxmox\.ya?ml$'` in the guard, `inventory/**/*proxmox.y*ml` in `.gitignore`, and `inventory/prod/proxmox.yml` in `MUST_BLOCK`.

**Resolution (fixed).** The guard rule is `'^inventory/(.+/)?[^/]*proxmox\.ya?ml$'` and `.gitignore` has `inventory/**/*proxmox.y*ml`. Both except exactly `proxmox.y*ml` under a `group_vars/` tree, because `MUST_ALLOW` already documents `inventory/group_vars/all/proxmox.yml` as a split-out vars file (Ansible skips `group_vars/` when it walks an inventory directory, so the plugin never reads one from there): the guard's `allowed` list gets `'(^|/)group_vars/(.+/)?proxmox\.ya?ml$'` and `.gitignore` a `!inventory/**/group_vars/**/proxmox.y*ml` negation placed before the `local*`/`vault*`/`secret*` rules so those still win. The exemption is exact on purpose - a first draft used `*proxmox` and let `group_vars/all/vault-proxmox.yml` through, since `allowed` is applied before the block rules. `MUST_BLOCK` adds `inventory/prod/proxmox.yml`, `inventory/prod/pve.proxmox.yaml`, `inventory/group_vars/all/vault-proxmox.yml` and `inventory/host_vars/proxmox.yml`; `MUST_ALLOW` adds `inventory/prod/group_vars/all/proxmox.yml`. Verified: `make unit`, including the both-ways guard/`.gitignore` agreement tests, and `git check-ignore` on all seven paths agreeing with the guard.

<a id="p3-12"></a>
### P3-12. `claude_code_settings` is a documented carrier for a key but is not `no_log` in the arg spec

- **Area:** Conventions, Security
- **Found by:** Security
- **Confidence:** Confirmed by reading; no leak path exists today (every renderer is `no_log`)
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `roles/claude_code/meta/argument_specs.yml:27-29`, `ARCHITECTURE.md:464-465, 848`

**What is wrong**

- ARCHITECTURE §10 promises every variable that can hold a secret carries `no_log: true`, and §5.5 says a key set through `claude_code_settings.env` is accepted and kept, yet the spec entry has no `no_log`.

**Failure scenario.** A future task that `debug`s or templates `claude_code_settings` without `no_log` passes review on the strength of the §10 statement.

**Fix.** Add `no_log: true` to `claude_code_settings`, or amend §10 to name it as the exception.

**Resolution (fixed).** `claude_code_settings` in `roles/claude_code/meta/argument_specs.yml` carries `no_log: true` and a description saying its env block can hold a key or a token. A grep over `roles/`, `playbooks/`, `molecule/` and `tests/` found no task that prints or templates it outside `settings.yml`, where every task is already `no_log`, so §10's promise holds without an exception. Verified by `make lint` (the production profile checks the spec) and the check-mode harness, which passes the variable through the role's `settings.yml`.

<a id="p3-13"></a>
### P3-13. 'A hand-broken `settings.json` does not fail the run' holds only when nothing is managed, and an empty file is unguarded

- **Area:** Remote Control, Docs
- **Found by:** Security
- **Confidence:** Confirmed by reading; same parse existed on main
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `roles/claude_code/tasks/settings.yml:27-29, 40, 46-48`, `roles/claude_code/vars/main.yml:29-33`, `ARCHITECTURE.md:462-463`

**What is wrong**

- The text-before-parse trick protects only the stale-key computation. Once `claude_code_managed_settings` is non-empty (the README's own `permissions.defaultMode` example), `settings.yml:47` runs `from_json` on whatever is on disk. A 0-byte file is guarded by `trim | length > 0` in `vars/main.yml` but not here (`content is defined` is true for an empty file).

**Failure scenario.** With `claude_code_settings` set, a truncated or hand-edited `settings.json` fails with a raw `from_json` traceback under `no_log`, while the comment two lines above says this cannot happen.

**Fix.** Reword the comment to 'when nothing is managed' and add the same `trim | length > 0` guard (treat empty as `{}`), optionally an `assert` naming the file.

**Resolution (fixed).** `roles/claude_code/vars/main.yml` gains `claude_code_settings_existing_text` (the slurped file as text, `''` when absent). `settings.yml` parses it exactly once, in a new block that runs only when something is managed or the text holds `ANTHROPIC_API_KEY`: the parse treats an empty or whitespace-only file as `{}` with the same `trim | length > 0` guard `vars/main.yml` uses, an `assert` requires a mapping, and the block's `rescue` fails with `<home>/.claude/settings.json on <host> is not a JSON object, and this run has settings to merge into it` instead of a `from_json` traceback censored by `no_log`. The stale-key and merge tasks read the parsed fact, every task that renders settings is still `no_log`, and the comment plus ARCHITECTURE §5 now say the hand-broken-file tolerance holds when nothing is managed. Verified by a new `settings_on_disk` run in `tests/unit/test_remote_control_playbooks.py`: `settings.yml` in check mode with `permissions.defaultMode` managed succeeds on an empty and on a whitespace-only `settings.json`, fails only the host whose file is `{"theme": ` with the rescue's message naming the file, and leaves a broken file alone when nothing is managed.

<a id="p3-14"></a>
### P3-14. `forks = 10` turns a fleet above 10 into two waves, while the docs say every VM boots concurrently

- **Area:** Performance, Docs
- **Found by:** Performance
- **Confidence:** Measured against the fake: 20 new VMs 18.8 s at forks=10 vs 9.9 s at `-f 20`
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `ansible.cfg:19`, `playbooks/provision.yml:125-130`, `playbooks/destroy.yml:93-97`, `CLAUDE.md, ARCHITECTURE.md (fan-out claims)`

**What is wrong**

- Under the linear strategy each role task runs in ceil(N/10) waves, so for 11-20 VMs the clone step (30-120 s on real storage) runs twice end to end. The agent wait is less affected because all starts complete before it. Not a regression against main; the docs overstate the fan-out.

**Failure scenario.** `make provision` with 20 names: wave 2's clones begin only when wave 1's finish, about one clone-time longer than necessary.

**Fix.** Raise `forks` (20-25 is harmless for localhost-delegated API calls; the hypervisor is the real limit) or document `ANSIBLE_ARGS="-f 20"` beside the fleet guidance.

**Resolution (fixed).** `ansible.cfg` sets `forks = 20` with a comment saying what it bounds (VMs cloned, booted or configured at once; every `proxmox_vm` task is a localhost-delegated API call and configure is SSH to the fleet) and that `ANSIBLE_ARGS="-f N"` raises it for a run. CLAUDE.md's provision paragraph, ARCHITECTURE §4.2 (both the intro and play 2) and §13, and the README's fleet section now say the VMs boot up to `forks` at a time rather than all concurrently, each pointing at `ANSIBLE_ARGS`. The §4.5 timing is kept as a measurement 'at 10 forks' rather than 'the default'. Verified: `ansible-config dump --only-changed` shows `DEFAULT_FORKS = 20`.

<a id="p3-15"></a>
### P3-15. The worst-case agent wait is about nine minutes, not the 'five minutes' the docs and messages promise

- **Area:** Provisioning, Docs
- **Found by:** Performance
- **Confidence:** Estimated from PVE's ~3 s `guest-ping` timeout; not measured on a real host
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/tasks/present.yml:122, 169-186`, `roles/proxmox_vm/defaults/main.yml:65-68`, `README.md:249`, `ARCHITECTURE.md:713`

**What is wrong**

- The loop is bounded by `retries: 60`, not elapsed time; each poll is a full `proxmox_vm_info` run (~1 s) plus, for a *running* VM whose guest has no agent, PVE's ~3 s agent ping before it answers 'not running'. 60 x 9 s is about nine minutes.

**Failure scenario.** A template built without `qemu-guest-agent` (README's upgrade path) makes `make provision` sit ~9 min, not 5, before the diagnostic.

**Fix.** Size the retries from a wall-clock budget, or reduce `retries` to ~36 and say 'about five minutes' consistently.

**Resolution (fixed).** The retry numbers are unchanged; the wording is now honest everywhere it said "five minutes": `proxmox_vm_agent_retries` is a count of polls, 60 polls 5 s apart is five minutes of delay plus the time each poll takes, and a running VM with no agent answers each poll only after Proxmox's ~3 s agent ping, so about nine minutes. Said in `roles/proxmox_vm/defaults/main.yml` and `meta/argument_specs.yml` (the retries' descriptions), the README upgrade paragraph ("waits five minutes and then fails"), ARCHITECTURE §8 (both the "wait five minutes" sentence and the `60 × 5s` one), and the comments in `present.yml` and `vars/main.yml`. Documentation only; nothing for Molecule to exercise.

<a id="p3-16"></a>
### P3-16. `absent.yml` and the existing-VM path re-read a config the lookup already holds

- **Area:** Performance
- **Found by:** Performance
- **Confidence:** Measured: destroy of 20 VMs issues 40 `GET .../config`, two per VM
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/tasks/absent.yml:5-13`, `roles/proxmox_vm/tasks/present.yml:141-152`, `roles/proxmox_vm/tasks/main.yml:56-63`

**What is wrong**

- The lookup already uses `config: current`, yet both paths issue a second `proxmox_vm_info` (three requests plus a module spawn) for the same data. About 1 s per VM on a real host, in parallel.

**Failure scenario.** Pure overhead, 1-3 s wall per run.

**Fix.** Let `proxmox_vm_found_mac` fall back to `proxmox_vm_lookup.proxmox_vms[0].config` when the re-read is skipped; condition the re-read on `not proxmox_vm_exists` (present) and drop it in absent. Keep the retry for the just-cloned case.

**Resolution (fixed).** A lazy `proxmox_vm_found_config` in `vars/main.yml` is the lookup's `proxmox_vms[0].config` when `proxmox_vm_exists` and the retried re-read's otherwise; `proxmox_vm_found_mac` reads it. It keys off the `proxmox_vm_exists` fact rather than off whether `proxmox_vm_config` holds anything, because a register a run never reaches keeps a previous run's answer for another VM. `present.yml`'s re-read is now `when: not proxmox_vm_exists` (the `/cluster/resources` lag case for a VM this run cloned), its NIC assert's message keys off `proxmox_vm_found_config` so the net9 refusal still says "set proxmox_vm_nic", and `absent.yml`'s re-read is gone: `proxmox_vm_forget_address` reaches the lookup's config through the same var. The stopped-VM path still skips the NIC assert and the agent wait. Verified by the coordinator's `proxmox_vm` scenario: `side_effect.yml`'s "Refuse to guess an address when the NIC cannot be read" (now served from the lookup's config, still zero agent polls) and the known_hosts cleanup, plus a new `verify.yml` assertion that counts `GET .../qemu/9001/config` during claude-dev's lifetime: exactly one in the idempotence run (between the two converge markers) and four in the side-effect runs before its DELETE (net9, noagent, the mixed-case tag run P3-21 added, the delete), where each used to make two.

<a id="p3-17"></a>
### P3-17. `proxmox_vm_force_update` starts a VM that was shut down and replaces its tags, contradicting the README's re-run guarantee

- **Area:** Provisioning, Docs
- **Found by:** Provisioning
- **Confidence:** Confirmed by reading `vars/main.yml:59-64` and `present.yml:75, 107`
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/vars/main.yml:59-64`, `roles/proxmox_vm/tasks/present.yml:75, 107`, `README.md:391-394`

**What is wrong**

- README says hardware changes need `proxmox_vm_force_update: true` and, in the same paragraph, that a VM you shut down stays shut down on re-run. With `force_update`, `needs_configuration` is true, the VM is started, and the settings call sends the whole tag list, so hand-added tags are gone.

**Failure scenario.** User changes `vm_memory_mb` with `force_update` on a fleet; `gamma`, which they had shut down, boots, and its UI-added tags vanish.

**Fix.** One sentence in README 'Re-running' and in the variable's description: `force_update` re-applies the settings to every VM, replaces its tag list, and starts it, stopped or not.

**Resolution (fixed).** README "Re-running" now says that `proxmox_vm_force_update` re-applies the settings to every VM the run covers, replaces each one's tag list with the project's (a tag added in the Proxmox UI is dropped) and starts each one, shut down or not, so narrow it with `VM_NAME`; the "stays shut down" sentence is now explicitly the default-case one. `roles/proxmox_vm/defaults/main.yml` and `meta/argument_specs.yml` carry the same sentence on the variable. Documentation only.

<a id="p3-18"></a>
### P3-18. Update calls go to `proxmox_vm_node`, not the node the lookup found the VM on, so repairing a migrated VM fails with a misleading error

- **Area:** Provisioning
- **Found by:** Provisioning
- **Confidence:** Confirmed in `proxmox_kvm.py:1248-1251` (update uses the `node` parameter verbatim)
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/tasks/present.yml:56, 65, 103, 114`, `roles/proxmox_vm/tasks/main.yml:56-63`

**What is wrong**

- For an existing VM every `proxmox_kvm update: true` call is addressed to `/nodes/{{ proxmox_vm_node }}/qemu/{{ vmid }}/config`, while the lookup already returns `node`. `proxmox_disk`, start/stop/delete use the resources listing's node, so only the four update calls are affected.

**Failure scenario.** A VM live-migrated to `pve2`, then a run with `force_update` or while it still carries the unfinished tag: `Configuration file 'nodes/pve/qemu-server/9001.conf' does not exist`, with no hint that the node is the problem.

**Fix.** A lazy `proxmox_vm_found_node` from the lookup (defaulting to `proxmox_vm_node` for the clone path) in the three post-clone update calls. The clone must stay on `proxmox_vm_node`.

**Resolution (fixed).** A lazy `proxmox_vm_found_node` in `vars/main.yml` is `proxmox_vm_lookup.proxmox_vms[0].node`, defaulting to `proxmox_vm_node` when the VM does not exist yet. The tag, settings, start and finish calls in `present.yml` use it; the clone stays on `proxmox_vm_node`. `proxmox_disk` finds the node itself (`vm = proxmox.get_vm(vmid)`, then `nodes(vm["node"])`), as do `proxmox_kvm`'s start, stop and delete, so the resize and `absent.yml` are untouched. The README multi-node section and ARCHITECTURE §5.2/§9 record that only the clone cares about `proxmox_node` and a migrated VM is addressed on the node it is on. Not exercised by Molecule: the fake's second node refuses every request, so the lookup's node is always `pve` there; the change is verified by reading `proxmox_kvm.py` (`update` uses the `node` parameter verbatim) and by the scenarios still passing with the lookup's node.

<a id="p3-19"></a>
### P3-19. `make deploy VM_NAME=x` exits 0 having configured nothing when `x` is stopped, while `make configure VM_NAME=x` fails for the same state

- **Area:** Provisioning, Docs
- **Found by:** Provisioning
- **Confidence:** Confirmed by reading `provision.yml:149-177` and `discover.yml:101-116`
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `playbooks/provision.yml:149-177`, `playbooks/discover.yml:101-116`, `README.md:191-196`

**What is wrong**

- discover.yml asserts (P3-5) when a named VM got no address; provision.yml only `debug`s 'is stopped and was left that way', play 3 publishes nothing, configure matches no hosts, rc 0. README documents the message but not the exit status.

**Failure scenario.** `make deploy VM_NAME=gamma` in a script after someone shut gamma down: green run, nothing applied.

**Fix.** Add the same assert to provision.yml's third play when `vm_name is defined` and a requested VM has no address (stopped-VM wording), or state in the README that `make deploy` reports but does not fail for a stopped VM.

**Resolution (fixed).** Documentation only, as the finding's second option: the README paragraph under the "gamma is stopped and was left that way" output now states that `make provision` and `make deploy` exit 0 for a stopped VM, because their job is that the VMs exist and a stopped one does, while `make configure VM_NAME=gamma` fails for the same state, because its job is the software on the named VM and it must not configure the rest and exit 0 with gamma silently skipped. The provision scenario's existing stopped-beta re-run (rc 0) and `VM_NAME=parked` discovery (rc != 0) already pin both behaviours.

<a id="p3-20"></a>
### P3-20. The single-name `make provision` path, the README's first command, is never run by Molecule

- **Area:** Tests
- **Found by:** Provisioning
- **Confidence:** Confirmed: every provision.yml run in the scenario passes `alpha,beta`; the single-name logic was reproduced with skipped registers
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `molecule/provision/converge.yml:5-8`, `molecule/provision/verify.yml:460-467`, `playbooks/provision.yml:26-41, 75-107`

**What is wrong**

- The branch where `cluster_vms.yml` and `/cluster/nextid` are skipped and `claude_vm_first_id`/`claude_vm_new_ids` must default from skipped registers, plus `default(omit)` on `proxmox_vm_id` in `add_host`, is untested. It works today; a future edit of those lazy vars would break `make provision` and a bare `make deploy` while the fleet test stays green.

**Failure scenario.** Dropping a `default(..., true)` in play 1 breaks the single-VM path with the suite green.

**Fix.** One extra child run in verify.yml with `vm_name: solo`, asserting one clone with no `newid` chosen by the playbook and no extra `/cluster/nextid` request.

**Resolution (fixed).** `molecule/provision/verify.yml`'s "Run the playbooks where they must refuse or report" play now runs `provision.yml` as a child with `{"vm_name": "solo"}` between two marker lines in the fake's request log, asserting rc 0 and `solo is up at 192.0.2.63` (VMID 9013, the fake's lowest free one at that point). The fake now logs each request's `User-Agent`, which tells a playbook's own `uri` request (`ansible-httpget`) from a module's (`python-requests`). The final "Verify what the refused runs sent to the API" play slices the log between the markers and asserts exactly one `/clone`, with `params.name == 'solo'` and `newid == '9013'`, exactly one `/cluster/nextid` request in the run, and no request at all from `ansible-httpget` — play 1 listed nothing and chose nothing for one name, so that nextid was `proxmox_kvm`'s own. Exercised by the coordinator's `provision` scenario.

<a id="p3-21"></a>
### P3-21. Tag case is unhandled: Proxmox lowercases tags, the matching does not

- **Area:** Provisioning, Discovery
- **Found by:** Tests
- **Confidence:** Confirmed by reading; the fake lowercases tags so a scenario would catch it, but none sets a mixed-case tag
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `inventory/group_vars/all/defaults.yml:63-66, 74-79`, `roles/proxmox_vm/vars/main.yml:30-33`, `roles/proxmox_vm/tasks/main.yml:98, 137`

**What is wrong**

- `claude_vm_tag: Claude-On-Proxmox` in `local.yml`: provisioning stamps it, PVE stores `claude-on-proxmox`, the next run refuses the VM as not tagged, discovery finds nothing, destroy refuses.

**Failure scenario.** A mixed-case tag makes every VM unreachable to the project after the run that created it.

**Fix.** `| lower` on `claude_vm_tag` in the pattern and on both sides of the role compare, or assert it is lowercase in the role's validation; one provision-scenario case with a mixed-case tag.

**Resolution (fixed).** `claude_vm_tag_pattern` is now `(?i)(^|[;,])<tag | lower | regex_escape>([;,]|$)`, still anchored. In the role, `proxmox_vm_found_tags` lowercases its elements, a new lazy `proxmox_vm_sent_tags` is `proxmox_vm_tags | map('lower')`, `proxmox_vm_unfinished_tag` derives from it, the three tag-writing calls in `present.yml` send it, and both ownership asserts in `main.yml` compare `proxmox_vm_sent_tags[0]`. ARCHITECTURE §3 shows the new pattern and says why. Exercised by the coordinator's runs: the provision scenario runs `discover.yml` as a child with `{"claude_vm_tag": "Claude-On-Proxmox", "vm_name": "alpha"}` and asserts rc 0 with neither "No VMs to configure" nor "VM_NAME asked for"; the role scenario's `side_effect.yml` runs the role against the existing claude-dev with `proxmox_vm_tags: [Claude-On-Proxmox]` and asserts it was accepted, reported up at 192.0.2.51 and not written to (so the lowercased unfinished marker was compared correctly too).

<a id="p3-22"></a>
### P3-22. `guest_ipv4`'s agent-side `.lower()` on the MAC is dead to the tests

- **Area:** Tests
- **Found by:** Tests
- **Confidence:** Mutation survived in a real run of `pytest` against a mutated copy
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `filter_plugins/proxmox.py:63`, `tests/unit/test_filters.py:69-70`

**What is wrong**

- The test upper-cases only the *argument*, never `hardware-address`. Windows guest agents report uppercase MACs; the fake's `docker0` is uppercase but never the wanted NIC.

**Failure scenario.** Removing the `.lower()` on the agent side passes `make unit`.

**Fix.** `assert guest_ipv4([iface('eth0', LAN_MAC.upper(), ...)], LAN_MAC) == '192.0.2.51'`.

**Resolution (fixed).** `tests/unit/test_filters.py` gained `test_an_uppercase_hardware_address_from_the_agent_still_matches`, which passes an uppercase `hardware-address` and a lowercase wanted MAC; removing the agent-side `.lower()` now fails `make unit`.

<a id="p3-23"></a>
### P3-23. The fake has no `lxc` row in `/cluster/resources`, so the `type == 'qemu'` filter and the container VMID skip are untested

- **Area:** Tests, Fakes
- **Found by:** Tests
- **Confidence:** Confirmed: the fake's resources are qemu-only
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `inventory/group_vars/all/defaults.yml:76`, `playbooks/provision.yml:32, 63-68`, `tests/molecule/fake_pve_api.py:35-60, 111-126`

**What is wrong**

- Real `?type=vm` returns LXC rows too. Removing the `type` filter passes everything.

**Failure scenario.** A tagged container is listed, configured over SSH as if it were a VM, or its VMID is handed to a clone.

**Fix.** Add an `lxc` resource tagged `claude-on-proxmox` and assert discovery, list and destroy ignore it and a fleet's VMIDs skip its ID.

**Resolution (fixed).** `tests/molecule/fake_pve_api.py`'s `VMS` has a guest 9003 `ours-container` of type `lxc`, running on `pve` and tagged `claude-on-proxmox`; `resource()` lists it with `type`/`id` `lxc`, `/nodes/pve/qemu` leaves it out, and any `/nodes/pve/qemu/9003/...` request fails as it would for a VMID that is not a VM. 9003 sits inside the provision fleet's numbering, so beta now lands on 9004 (address 192.0.2.54) and the `bystander` seed moved from 9004 to 9012; converge, the clone-request and listing assertions pin the new VMIDs and addresses. The provision scenario asserts the container is in the cluster listing the API returned and not in what discovery considered or published, not in `make list`'s rows, and that `destroy.yml` refuses `VM_NAME=ours-container` even with `proxmox_vm_allow_untagged_delete` and leaves it in place. The role scenario needs no change: its lookups are `proxmox_vm_info type: qemu` by name, which skips non-qemu resources before asking any node, and its own fixtures use other VMIDs. Exercised by the coordinator's `provision` scenario.

<a id="p3-24"></a>
### P3-24. The worktree-spawn git-repository check (P3-7's fix) is untested

- **Area:** Tests, Remote Control
- **Found by:** Tests
- **Confidence:** Confirmed: nothing sets `claude_code_remote_control_spawn: worktree`
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `roles/claude_code/tasks/remote_control.yml:182-200`

**What is wrong**

- No scenario or unit test sets `spawn: worktree`. A regression degrades to the generic 'did not stay up' message.

**Failure scenario.** The actionable 'is not a git repository' message is lost without a failing test.

**Fix.** One case in the same harness as P2-10 with `spawn: worktree` and an empty dir, asserting `is not a git repository`.

**Resolution (fixed).** The gate was unreachable in check mode: it sat inside the Start block, which also requires the unit file to exist, and in check mode on a VM without the unit the template writes nothing - so `make check` passed a configuration whose real run fails. `remote_control.yml` now runs the `Require a git repository for worktree spawn` block before the Start block, gated on eligibility and `spawn == 'worktree'` only; a real run is unchanged (the unit always exists by then), and check mode now previews the failure. `tests/unit/test_remote_control_playbooks.py` adds a `worktree_spawn` run: two signed-in hosts with `claude_code_remote_control_spawn: worktree`, one whose session directory holds a `.git` and one whose directory is empty; the run exits non-zero, exactly the empty one fails, and its message contains `is not a git repository` and `same-dir or session`. The check-mode play builder was factored into `_check_run` so the three runs share it.

<a id="p3-25"></a>
### P3-25. A running VM still carrying the unfinished tag (death between the start and the untag) has no fixture

- **Area:** Tests, Provisioning
- **Found by:** Tests
- **Confidence:** Confirmed: `died-after-settings` covers only the stopped variant
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/tasks/present.yml:101-119`, `roles/proxmox_vm/vars/main.yml:59-64`, `roles/proxmox_vm/molecule/default/side_effect.yml:721-741`

**What is wrong**

- A running VM with `claude-on-proxmox-unfinished` goes down the repair path (settings, no-op resize, no-op start, untag) but nothing asserts it is not reported stopped and does get untagged.

**Failure scenario.** A regression that treats a running unfinished VM as finished leaves the tag on forever, so every later run re-applies settings.

**Fix.** Start 9011 before the first role run in a second copy of that play; assert the tag is removed and the writes are the same set.

**Resolution (fixed).** `roles/proxmox_vm/molecule/default/side_effect.yml` has a new play after the stopped variant: clone 9105 `died-before-untag`, apply the settings with `claude-on-proxmox,claude-on-proxmox-unfinished` and the cloud-init user, start it, then run the role. It asserts `proxmox_vm_stopped` is false, the address is reported, the tag list is back to `claude-on-proxmox`, the disk was resized, the role's two config writes (settings marking it unfinished, then the untag with keys `name`, `tags`) carry the same parameter sets as `died-after-settings`'s, and no start, stop or shutdown beyond the fixture's own start was sent. `verify.yml`'s fixture list now includes the clone's name so it is not counted as claude-dev's. Exercised by the coordinator's `proxmox_vm` scenario.

<a id="p3-26"></a>
### P3-26. The weekly canary cannot see the Ubuntu cloud image break; the docs say it does

- **Area:** Docs, CI
- **Found by:** Template/CI
- **Confidence:** Confirmed: nothing in the suite fetches `cloud-images.ubuntu.com`; the canary runs only `configure`
- **Lives:** on main — Already merged into origin/main through PR #6; the branch inherits it
- **Status:** Fixed
- **Where:** `.github/workflows/ci.yml:8-12, 154-165`, `README.md:597-599`, `requirements.yml:5-12`

**What is wrong**

- The template scenario serves a random 64 KiB file from `127.0.0.1:8080`. The canary job runs only the `configure` scenario. The same sentence lists 'the Galaxy collections (pinned to exact versions)' among things that 'break without a commit here', which `requirements.yml` itself says is impossible for pinned versions.

**Failure scenario.** The first signal of a moved image URL or SHA256SUMS format is a user's failed first `make deploy`, not a Monday CI failure.

**Fix.** Drop the two claims, or add a scheduled `curl -fsSI` on the image URL plus a `grep` of SHA256SUMS for the filename to the canary job.

**Resolution (fixed).** The canary job gains a first step, before setup and the Molecule run, that reads `proxmox_template_image_url` and the URL inside `proxmox_template_image_checksum` from `roles/proxmox_template/defaults/main.yml` with `sed` (both are literal scalars there; the step fails if either read is empty), `curl -fsSIL`s the image and `curl -fsSL`s `SHA256SUMS` and greps it for the image's basename - nothing is downloaded. It runs only when the job does (schedule or dispatch), under the job's existing `permissions: contents: read`, with no secret. The schedule comment in `ci.yml` and the README's canary paragraph no longer list the Galaxy collections among things that break without a commit (they say the pins make that impossible), and the README says what the canary checks about the image. Verified: `make lint` (yamllint on the workflow); the step itself runs on the next scheduled or dispatched run.

<a id="p3-27"></a>
### P3-27. Grouped documentation staleness

- **Area:** Docs
- **Found by:** Conventions
- **Confidence:** Each checked against the code
- **Lives:** uncommitted — Only in the uncommitted working tree
- **Status:** Fixed
- **Where:** `README.md:612-627`, `Makefile:156`, `ARCHITECTURE.md:505-507, 552`, `inventory/group_vars/all/local.yml.example`, `playbooks/discover.yml:111-114`, `.github/workflows/ci.yml:131`

**What is wrong**

- README Layout: `filter_plugins/` lists `guest_ipv4 / net_mac` and omits `guest_address` and `proxmox_access_denied`; `tests/unit/` says 'pytest for custom modules'; `inventory/` omits the tracked, load-bearing `controller.yml`; the symlink note is attached only to `molecule/provision/` though `molecule/configure/group_vars/all/defaults.yml` is a symlink too.
- `make help`'s `unit` line still says 'custom modules' (CONTRIBUTING/README were fixed in P3-13, the Makefile was not).
- ARCHITECTURE §5.5 step 2 lists four blocking env keys; `vars/main.yml:13-21` checks eight plus `apiKeyHelper`. §6.2 says `proxmox_api_module_defaults` is 'every playbook's `module_defaults`'; provision.yml and destroy.yml set none.
- `local.yml.example` never mentions `claude_remote_control`, the `claude_code_remote_control_*` knobs or `proxmox_nic`, though README:489, 508-513 say to set them 'in `local.yml`' and `defaults.yml:22-25` says to change `proxmox_nic` when the template differs.
- `discover.yml`'s fail message says the VM 'was not configured', but `claude_login.yml` imports the same play, so `make claude-login VM_NAME=gamma` on a stopped VM reports it 'not configured'.
- `ci.yml:131` comments `# v5` where the repository tag is `v5.0.0` (the SHA resolves correctly).

**Failure scenario.** A reader looks for a filter, a knob or a file in the wrong place, or is told the wrong reason for a failure.

**Fix.** Update the listed lines; point §5.5's list at `vars/main.yml`; add commented entries to `local.yml.example`; neutral wording ('was skipped') in discover.yml.

**Resolution (fixed).** README Layout: `filter_plugins/` lists `net_mac`, `guest_ipv4`, `guest_address`, `guest_addresses` and `proxmox_access_denied`; `inventory/` lists `controller.yml`; the symlink note says it applies to both `molecule/configure/` and `molecule/provision/`; `tests/unit/` and the Makefile's `unit` help text were already current and are unchanged. ARCHITECTURE §5.5 step 2 points at `claude_code_remote_control_blocking_env` in `roles/claude_code/vars/main.yml` instead of enumerating keys, and names the two checked separately (`apiKeyHelper`, `ANTHROPIC_BASE_URL`); §6.2 says `proxmox_api_module_defaults` is the `module_defaults` of every localhost play that calls `community.proxmox` modules. `local.yml.example` gains commented entries for `proxmox_nic`, `claude_code_remote_control_name`, `claude_code_remote_control_dir`, `claude_code_remote_control_permission_mode` and `claude_code_remote_control_spawn`, each with the role default. `discover.yml`'s fail message says the named VMs were 'skipped' rather than 'not configured', with a comment that `claude_login.yml` imports the play; no test asserted the old wording. `ci.yml`'s dependency-review pin is commented `# v5.0.0`, which `git ls-remote --tags` confirms the SHA resolves to. Verified: `make lint`, `make syntax`, `make unit`.

<a id="p3-28"></a>
### P3-28. Host notes that need no change to the project defaults

- **Area:** Host
- **Found by:** Host audit
- **Confidence:** Read-only inspection of the host
- **Lives:** host — On the Proxmox host, not in the repository
- **Status:** Fixed
- **Where:** `host: `dpkg -l libguestfs-tools`, `/etc/pve/storage.cfg`, `ip neigh`, OPNsense DHCP`

**What is wrong**

- `libguestfs-tools` is not installed; `make template` installs it (~400 MB with the appliance) on first run and the no-subscription repo is reachable. `dhcpcd-base` is already present.
- TrueNAS NFS storages `truenas-persistent` (7.3 TB) and `truenas-ephemeral` (1.0 TB quota) are active, `shared`, `images`, qcow2, exported to `10.0.25.100` only, no root_squash. The default `local-lvm` is valid, so `local.yml` is needed only to put VM disks on TrueNAS; if so, add the token ACL on `/storage/<name>` and build the template on the same storage.
- The DHCP pool cannot be verified from the host: no VM exists to exercise the agent path. Nine live neighbours in 10.0.25.58-.148 suggest a lease range on that LAN. Confirm in OPNsense that the range excludes `.100` (pve) and `.114` (TrueNAS) and has room for the fleet.

**Failure scenario.** None expected. If the pool is exhausted, `make provision` waits the full agent timeout and fails 'did not report an address'.

**Fix.** Optional: pre-install `libguestfs-tools`; check the DHCP range; decide on storage before the first `make template`.

**Resolution (fixed).** Checked read-only on the gateway (ISC dhcpd is the running server; Kea disabled): the LAN pool is **10.0.25.50-10.0.25.150**, 101 addresses, with no static mappings - so it **contains both `pve` (.100) and TrueNAS (.114)**, which README step 3 says it must not. ISC dhcpd's ping-before-offer makes a collision unlikely but not impossible, and the VMs will take leases from the same pool. Recommended, in OPNsense -> Services -> DHCPv4 -> LAN: move the range to one that excludes both (for example 10.0.25.120-10.0.25.150, or .50-.99), or add static mappings for the two hosts' MACs. Not changed here: it is the gateway's configuration, not the project's or the Proxmox host's. `libguestfs-tools` is left for `make template` to install, and `local-lvm` stays the default storage.

## Notes outside the diff

- **Stale local `main`.** Local `main` is 6167131; `origin/main` is 9f4aec1 (PR #6 merged 2026-10-03, containing 29 of these 34 commits). Every reviewer compared against the stale base, so their coverage is a superset; each finding above says where the defect lives today. Run `git fetch && git branch -f main origin/main` before rebasing.
- **A live credential in the working directory.** A git-ignored, untracked `.env` at the repo root holds a live-looking `TRUENAS_CLAUDE_API_KEY`. `git check-ignore` confirms `.gitignore:31` covers it, `git log --all -- .env` is empty, and nothing references it; CI's `dir .` gitleaks scan would fail the moment it were force-added. Move it out of the project.
- **Stale comment in your `inventory/hosts.yml`.** Your git-ignored `hosts.yml` still says 'inventory/proxmox.yml discovers already-created ones', copied from an older example; the tracked `hosts.yml.example` is correct. Cosmetic.
- **Controller setup.** `inventory/group_vars/all/vault.yml` and `local.yml` did not exist on this controller at review time; both were created while resolving P0-1 (`local.yml` is the untouched example, so every default applies). Back up `.vault_pass`: it is the only way to read `vault.yml`.
- **First real run of the gitleaks range step.** P2-14's commit-range scan has never run on GitHub (the last green CI on 09-11 predates every uncommitted change). Watch the first run for a 'dubious ownership' refusal from the gitleaks image's `git`, which runs as a different uid than the checkout.
- **Every Molecule scenario passes.** After the fix passes, `proxmox_vm`, `provision`, `proxmox_template` and - once the Docker disk had room again on 2026-10-07 - `claude_code` pass end to end, alongside lint, syntax and the 204 unit tests. The `claude_code` failure seen earlier was its `prepare` stage's apt download being truncated on a full Docker disk (31.0 of 31.3 GB), not the mirror and not the change.

## Checked and found sound

- Ownership interlock: whole-tag matching both ways, templates refused before the untagged check on both paths, `proxmox_vm_allow_untagged_delete` cannot reach a template, duplicate names refused by role and discovery, repair marker is `ciuser` (which the template never sets). The gap is test coverage (P1-2), not behaviour.
- Unfinished-tag repair converges after a death at the settings, resize, start and finish calls; a finished-but-stopped VM is left alone; every output `set_fact` is unconditional; every conditional is boolean under ansible-core 2.21.
- community.proxmox 2.0.0 semantics: name+tags calls do not reset hardware; a VMID collision returns `changed=False` with the template's VMID (the `is changed` assert is load-bearing); `proxmox_disk` compares size strings; `proxmox_vm_info` supports check mode and fails only for the node of the matched VM.
- Clone throttle: `'0'` is templated to the integer 0 per host; the fleet side-effect proves unpinned clones serialise and pinned ones run concurrently. `deploy.yml` lists the cluster at most once.
- All 44 previously 'fixed' findings spot-checked in each area are real fixes. P1-8, P1-9, P1-10, P3-10 and P3-11 are load-bearing under mutation.
- Secrets: every task touching the key or the two JSON files is `no_log`; managed settings are not `cacheable`; files are 0600; the unit carries no credential; gitleaks 8.30.0 tree and `main..HEAD` scans are clean; the diff contains only RFC 5737 addresses and test fixtures. Remote Control re-verified against Claude Code 2.1.285 (consent gate still keyed on `remoteDialogSeen` only; `auth status` schema matches).
- Role variable hygiene: `defaults/main.yml` and `meta/argument_specs.yml` agree key-for-key for all six roles; no dead variables; `file |` prefixes everywhere; every `failed_when: false` is paired with an assert.
- CI and pins: all six role scenarios and both playbook scenarios are in the matrix; Molecule's default sequence runs `side_effect`; every `actions/*` SHA, pre-commit rev and the gitleaks image digest resolve to the tag their comment claims; `requirements.txt` and `requirements.yml` are exact pins; `make syntax` works without `hosts.yml`.
- Performance: lazy Jinja is cheap at scale (57 ms/item at 222 guests); 52 requests per new VM is the collection's design, fanned out over forks; the Remote Control unit cannot spin (`RestartSteps`/`RestartMaxDelaySec`); Molecule runtime growth is bounded.
- Host: repos, virtualisation, bridge, storage, DNS, NTP, services and tag access all pass; see P0-1 for the one blocker.

## Method and limits

Read-only throughout: no repository file was edited by a reviewer, no Molecule scenario was run (Docker disk is near full), and nothing on the host was created, modified, started or installed. `make lint`, `make syntax` and `make unit` (151 tests) pass on the working tree. Performance numbers come from the fake API as a plain local process and are a floor. Reviewers compared against the stale local `main`; the aggregator re-labelled each finding by where the code lives today.
