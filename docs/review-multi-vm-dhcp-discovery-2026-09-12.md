# Adversarial review: `feat/multi-vm-dhcp-discovery`

Reviewed 2026-09-12. Branch `feat/multi-vm-dhcp-discovery` at `1b2a739` against `origin/main` (`6167131`): 34 commits, 72 files, +3967 / -282.

Seven independent reviewers each audited one area, read-only, with instructions to try to disprove every finding before reporting it. Their reports were merged, duplicates collapsed, and the high-severity claims re-checked before inclusion. **45 findings.**

_Status updated 2026-10-06: **44 of 45 fixed**, 1 deferred (P3-12, a git-history rewrite that needs a clean tree and interactive rebase). Fixes are uncommitted on the branch; each fixed finding says what changed and how it was verified._

| Priority | Meaning | Count | Fixed |
|---|---|---|---|
| **P0** Critical | Data loss, a security exposure, or a headline feature that does not work | 2 | 2 |
| **P1** High | A likely bug in a realistic scenario, or a test that passes while the thing it guards is broken | 10 | 10 |
| **P2** Medium | An edge-case bug, a measurable regression, or docs that lead to a wrong action | 20 | 20 |
| **P3** Low | Minor correctness, cleanup or staleness | 13 | 0 |

## What matters most

- **Remote Control does not work end to end.** The server stops at a one-time consent prompt nothing answers (P0-1). Around it: a leftover API key blocks it (P1-1), opting out leaves it running (P1-4), the health check passes on a crash (P1-5), and a network outage stops it for good (P1-6).
- **The ownership tag no longer protects VMs this project did not create.** Provisioning treats any untagged VM with a matching name as half-built, rewrites it and stamps the tag, after which `make destroy` deletes it (P0-2).
- **The tests could not have caught either.** The provision scenario's negative checks pass for the wrong reasons, and none of the safety checks added by the last review is exercised (P1-9, P1-10).
- **Several fixes from the previous review do not hold.** The tracked-file guard regressed against main, `net0` is still hard-coded, and the fact-cache and host-key cleanups are no-ops (see the table below).

## Index

| ID | Status | Finding | Area | Introduced |
|---|---|---|---|---|
| [P0-1](#p0-1) | Fixed | Remote Control never starts under systemd: its one-time consent prompt is never answered | Remote Control | e6d1075 |
| [P0-2](#p0-2) | Fixed | Provisioning takes over any existing VM with the same name, and the tag it stamps makes that VM deletable | Provisioning, Safety | 7fa3229 |
| [P1-1](#p1-1) | Fixed | Clearing the API key leaves it on the VM, so the documented switch to Remote Control cannot work | Remote Control, Security | Merge pre-dates the branch; made load-bearing by e6d1075 and 94c457e |
| [P1-2](#p1-2) | Fixed | The tracked-file guard regressed: paths main blocked now pass, and .gitignore misses them | Security, CI | 2b3c4d0 |
| [P1-3](#p1-3) | Fixed | The README's API token recipe fails on Proxmox VE 9, which the README now says is supported | Docs, Provisioning | d4f1471 (the 9.x claim) on 7364bfd (the VM.Monitor recipe) |
| [P1-4](#p1-4) | Fixed | Opting out of Remote Control leaves a running, enabled server behind | Remote Control, Security | 94c457e |
| [P1-5](#p1-5) | Fixed | The "session stayed up" check passes on the first `active` reading, which systemd reports while the server is still starting | Remote Control, Tests | e6d1075 |
| [P1-6](#p1-6) | Fixed | `Restart=on-failure` never restarts the server after it gives up on the network | Remote Control | e6d1075 |
| [P1-7](#p1-7) | Fixed | A bare `make deploy` clones a new default VM and then reconfigures the whole fleet | Provisioning, Discovery | 1b2a739, on 11c6454's discovery default |
| [P1-8](#p1-8) | Fixed | Re-running against a VM someone shut down waits about six minutes, then fails with the wrong diagnosis | Provisioning, Performance | e949eca |
| [P1-9](#p1-9) | Fixed | The provision scenario's "must not touch other VMs" checks pass for the wrong reasons | Tests | ccf9968, c43287e |
| [P1-10](#p1-10) | Fixed | None of the safety checks added by the last review is exercised by a test | Tests | 6ef6982, fcd1632, 7fa3229, c43287e |
| [P2-1](#p2-1) | Fixed | The auth-failure pattern `40[13]` also matches VMIDs | Discovery, Provisioning | e949eca |
| [P2-2](#p2-2) | Fixed | provision.yml's publish play assumes every requested VM succeeded | Provisioning | 7fa3229 |
| [P2-3](#p2-3) | Fixed | `make deploy TAGS=...` silently skips building the template and creating VMs | Conventions | 1b2a739 |
| [P2-4](#p2-4) | Fixed | Discovery and `make list` hard-code `net0` and ignore `proxmox_vm_nic`; the duplicated logic has already drifted | Discovery, Conventions | 76be4fb, 2c3b20b |
| [P2-5](#p2-5) | Fixed | `make claude-login` and the role disagree on what "signed in" means, sending the user in a loop | Remote Control | e6d1075 |
| [P2-6](#p2-6) | Fixed | The login command `make claude-login` prints fails with "command not found" | Remote Control | e6d1075 |
| [P2-7](#p2-7) | Fixed | `make check` fails on any VM that does not have the Remote Control unit yet | Remote Control | e6d1075 |
| [P2-8](#p2-8) | Fixed | VMs created before this branch cannot be listed or destroyed, and the documented escape hatch is unreachable | Provisioning, Docs | 172b930, fcd1632 |
| [P2-9](#p2-9) | Fixed | A run that dies after the settings call is never repaired, because the tag is written first | Provisioning | 7fa3229 |
| [P2-10](#p2-10) | Fixed | One offline cluster node breaks deploy, configure, list and destroy | Discovery, Provisioning | 11c6454, 1b2a739 |
| [P2-11](#p2-11) | Fixed | The clone throttle holds back every VM's boot until the last clone finishes | Performance | 7fa3229 |
| [P2-12](#p2-12) | Fixed | destroy.yml deletes VMs one at a time, where main deleted them in parallel | Performance | 65215e4 |
| [P2-13](#p2-13) | Fixed | `make deploy` repeats, one VM at a time, the discovery that provisioning just did | Performance | 1b2a739 |
| [P2-14](#p2-14) | Fixed | CI's gitleaks scan misses a secret committed and then removed within a pull request | Security, CI | 2b3c4d0 |
| [P2-15](#p2-15) | Fixed | The README's Remote Control permissions example has no effect on remote sessions | Docs, Remote Control | 94c457e |
| [P2-16](#p2-16) | Fixed | The multi-node guidance is wrong: shared storage does not let this role clone onto another node | Docs | d4f1471 |
| [P2-17](#p2-17) | Fixed | The fixed-address advice breaks as soon as there is more than one VM | Docs, Security | 6d94361, 5628b98, d4f1471 |
| [P2-18](#p2-18) | Fixed | The fresh-host repository step leaves the Ceph enterprise repository enabled | Docs | d4f1471 |
| [P2-19](#p2-19) | Fixed | The fake always sends `tags: ""`, hiding a guard every real cluster needs | Tests | 11c6454 |
| [P2-20](#p2-20) | Fixed | `--tags`, destroy.yml, deploy.yml and claude_login.yml are never run by a test | Tests | 86e8e83, 1b2a739, e6d1075 |
| [P3-1](#p3-1) | Fixed | Fact-cache eviction and the host-key fallback in destroy never take effect | Provisioning | e949eca |
| [P3-2](#p3-2) | Fixed | The `~/.claude.json` tasks are not `no_log`, so `make check` prints account data | Security | e6d1075 |
| [P3-3](#p3-3) | Fixed | A quote in `VM_NAME` breaks out of the Makefile's quoting | Security | 172b930 |
| [P3-4](#p3-4) | Fixed | The new `proxmox_template` booleans are bare conditionals, which is fatal when set with `-e` | Conventions | 3ee7563 |
| [P3-5](#p3-5) | Fixed | Discovery silently drops a VM the user asked for by name | Discovery | 11c6454 |
| [P3-6](#p3-6) | Fixed | Remote Control's pre-flight check misses real blockers and includes a false one | Remote Control | e6d1075 |
| [P3-7](#p3-7) | Fixed | Two Remote Control misconfigurations fail with unhelpful errors | Remote Control | e6d1075 |
| [P3-8](#p3-8) | Fixed | The explanation of why `make deploy` opens no SSH connection is wrong | Docs | 1b2a739 |
| [P3-9](#p3-9) | Fixed | CLAUDE.md, ARCHITECTURE.md and ansible.cfg describe Ansible behaviour incorrectly | Docs | d09d82a and earlier comments |
| [P3-10](#p3-10) | Fixed | `make test` runs every linter twice, and can rewrite files | Performance, Conventions | 172b930 |
| [P3-11](#p3-11) | Fixed | The guest-agent poll assertion is not limited to the converge stage it describes | Tests | 7f33ea3 |
| [P3-12](#p3-12) | Deferred | The commit history is not ready to merge | Conventions | Branch history |
| [P3-13](#p3-13) | Fixed | Stale statements across docs and comments | Docs | Various |

## P0: Critical

_Data loss, a security exposure, or a headline feature that does not work._

<a id="p0-1"></a>
### P0-1. Remote Control never starts under systemd: its one-time consent prompt is never answered

- **Area:** Remote Control
- **Found by:** Claude Code
- **Confidence:** Confirmed against docs and binary (Re-checked: official docs and the installed 2.1.270 binary)
- **Introduced:** e6d1075
- **Status:** Fixed
- **Where:** `roles/claude_code/tasks/remote_control.yml:88-102`, `roles/claude_code/vars/main.yml:47-51`, `roles/claude_code/templates/claude-remote-control.service.j2`, `molecule/configure/verify.yml:45-48`

**What is wrong**

- The Remote Control docs say server mode asks a one-time `Enable Remote Control? (y/n)` confirmation, and exits without starting the server unless it is accepted.
- In the binary that prompt is gated only on `remoteDialogSeen` in `~/.claude.json`, with no TTY check: a readline question, then `process.exit(0)` unless the answer is y or yes.
- The role seeds `hasCompletedOnboarding` and `hasTrustDialogAccepted`, never `remoteDialogSeen`. `claude auth login` does not set it either, and systemd gives the unit `/dev/null` as stdin.
- The configure scenario asserts the dialogs that would stall the server are pre-accepted, but checks only the two keys that are seeded, so it passes.

**Failure scenario.** Follow the README exactly: `make configure`, `make claude-login`, `make configure`. The unit starts, prints the prompt to the journal, reads end-of-file and exits 0, which `Restart=on-failure` does not restart. No session ever appears in the app, and depending on timing the run can still print "Remote Control is live" (P1-5).

**Fix.** Seed `remoteDialogSeen: true` with the other keys, include it in `claude_code_remote_control_dialogs_seeded`, and assert it in the configure scenario.

**Resolution (fixed).** `remote_control.yml` seeds `remoteDialogSeen: true` alongside onboarding and workspace trust, and `claude_code_remote_control_dialogs_seeded` requires it, so existing VMs get it written once. The README and ARCHITECTURE §5.5 say the confirmation is answered on the user's behalf.

**Verified.** configure scenario passed: converge wrote the seed, idempotence skipped it, and verify asserts `remoteDialogSeen`. No claude.ai login exists in CI, so the started server itself is not exercised.

<a id="p0-2"></a>
### P0-2. Provisioning takes over any existing VM with the same name, and the tag it stamps makes that VM deletable

- **Area:** Provisioning, Safety
- **Found by:** Provisioning, Docs, Tests
- **Confidence:** Reproduced against the fake API (Re-checked by reading, against main's present.yml)
- **Introduced:** 7fa3229
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/vars/main.yml:41-44`, `roles/proxmox_vm/tasks/present.yml:42-81`, `roles/proxmox_vm/tasks/main.yml:85-99`

**What is wrong**

- `proxmox_vm_needs_configuration` is true whenever the VM lacks the ownership tag, which is how a half-built VM gets repaired. The ownership check only runs for `state: absent`, so on the present path any existing untagged VM with that name, or the template itself, counts as half-built.
- On main an existing VM was only reconfigured with `proxmox_vm_force_update`.
- Reproduced: an unrelated `nas` (8 cores, 16 GB, static IP, tags `prod;backup`, 16G disk). `provision.yml` with `vm_name: nas` reset it to 2 cores and 4 GB, `ip=dhcp`, user `dev` with injected keys, a 32G disk and tag `claude-on-proxmox`. `destroy.yml` then passed both guards and deleted it with its disks.

**Failure scenario.** `make deploy VM_NAME=dev` on a host that already runs a VM called `dev`, or `VM_NAME` set to the template's name. Its network settings are replaced, its disk grows irreversibly, and it becomes deletable by `make destroy`. This defeats the invariant CLAUDE.md calls the safety interlock.

**Fix.** On the present path, refuse templates and untagged VMs. Repair only a VM that is evidently an unconfigured clone (no `ciuser` or `sshkeys` in its config), or behind an explicit opt-in.

**Resolution (fixed).** The role refuses to configure an existing template or an existing VM without the ownership tag, before anything is changed. A new VM is tagged in a call of its own straight after the clone, and the half-built marker is now the cloud-init user read from the lookup (`config: current`), so a VM a run left unfinished is still repaired.

**Verified.** proxmox_vm scenario passed: an untagged VM and the template were both refused with no config write reaching either, a tagged-but-unconfigured VM was repaired, and idempotence held. The provision scenario passed.

## P1: High

_A likely bug in a realistic scenario, or a test that passes while the thing it guards is broken._

<a id="p1-1"></a>
### P1-1. Clearing the API key leaves it on the VM, so the documented switch to Remote Control cannot work

- **Area:** Remote Control, Security
- **Found by:** Claude Code, Security, Docs, Tests
- **Confidence:** Reproduced (Re-checked: settings.yml and the Remote Control troubleshooting docs)
- **Introduced:** Merge pre-dates the branch; made load-bearing by e6d1075 and 94c457e
- **Status:** Fixed
- **Where:** `roles/claude_code/tasks/settings.yml:2-37`, `roles/claude_code/tasks/main.yml:60-78`, `roles/claude_code/vars/main.yml:25-27`

**What is wrong**

- settings.yml only ever adds `env.ANTHROPIC_API_KEY`. The existing file is merged underneath with `combine(recursive=True)`, and the whole block is skipped when there is nothing to manage, which is the default. Reproduced: key set, then written; key cleared, then every task skipped and the key still in `settings.json`.
- The gate reads the controller variable, not the VM. `claude auth status` reports `authMethod: claude.ai` whenever a claude.ai login exists, even while a key is in use (that is reported separately as `apiKeySource`), so the eligibility test cannot see the leftover key.
- The Remote Control docs name an `ANTHROPIC_API_KEY` in a settings file's `env` block as something that must be removed.
- The role's own message says to clear `vault_anthropic_api_key` and re-run, and the README says the decision comes from the credentials found on the VM. No test starts from a VM that previously had a key.

**Failure scenario.** A VM built with a key. The user clears it, runs `make claude-login` and `make configure`. The stale key still outranks the login, so Remote Control is either reported ineligible or started and refused. The key the user believes is gone stays in a 0600 file on the VM.

**Fix.** When the key is empty, delete `env.ANTHROPIC_API_KEY` from the on-disk settings. Gate on `authMethod == 'claude.ai'` with no `apiKeySource`. Add a scenario stage that re-converges without the key.

**Resolution (fixed).** settings.yml reads settings.json on every run and removes an `env.ANTHROPIC_API_KEY` that no managed setting puts there (a key set deliberately through `claude_code_settings.env` is kept). Eligibility now also requires no `apiKeySource`, which `claude auth status` reports whenever a key is in use, and the message names the key's source instead of sending the user to log in again.

**Verified.** claude_code and configure scenarios passed. claude_code scenario side effect: clearing the key removed it from settings.json and left other settings intact. The `apiKeySource` field was confirmed in the installed Claude Code 2.1.270.

<a id="p1-2"></a>
### P1-2. The tracked-file guard regressed: paths main blocked now pass, and .gitignore misses them

- **Area:** Security, CI
- **Found by:** Security, Conventions
- **Confidence:** Reproduced (Re-checked against the guard, main's guard and git check-ignore)
- **Introduced:** 2b3c4d0
- **Status:** Fixed
- **Where:** `tests/check_no_local_files.sh:10-27`, `.gitignore:1-25`, `tests/unit/test_tracked_files.py`

**What is wrong**

- Main's guard matched `hosts`, `local*` and `vault*` YAML at any depth. The rewrite anchors them under `inventory/` or the repository root.
- `playbooks/group_vars/all/vault.yml` and `local.yml` now pass, and main blocked them; Ansible loads them for every playbook in `playbooks/`. `inventory/prod/hosts.yml` passes and is parsed as an inventory source. None of them is git-ignored.
- gitleaks flags high-entropy tokens, not an IP address or a username, so it does not backstop this. `MUST_BLOCK` does not list these paths, so the unit test passes.

**Failure scenario.** A user puts `proxmox_api_host` and `github_username` in `playbooks/group_vars/all/local.yml` and commits it. The guard, .gitignore and gitleaks all stay green, breaking both the no-addresses and no-username rules.

**Fix.** Restore depth-independent matching, for example `(^|/)group_vars/.+/(local|vault|secret)...` and `(^|/)hosts\.ya?ml$` with an exception for Molecule fixtures, mirror it in .gitignore, and add these paths to `MUST_BLOCK`.

**Resolution (fixed).** Guard rules match at any depth again, with the Molecule host_vars fixtures and `.example` templates exempted by name; .gitignore mirrors the rules. `playbooks/group_vars/`, `playbooks/host_vars/`, nested `inventory/*/hosts.*`, extensionless and backup vault files are blocked.

**Verified.** 119 unit tests pass (MUST_BLOCK grew from 22 to 50 paths); the new tests fail 23 times against the old patterns; the guard passes on the tracked tree.

<a id="p1-3"></a>
### P1-3. The README's API token recipe fails on Proxmox VE 9, which the README now says is supported

- **Area:** Docs, Provisioning
- **Found by:** Provisioning, Security, Docs
- **Confidence:** Confirmed against PVE release notes and source (Re-checked: PVE 9.0 release notes list the removal as a breaking change)
- **Introduced:** d4f1471 (the 9.x claim) on 7364bfd (the VM.Monitor recipe)
- **Status:** Fixed
- **Where:** `README.md:19`, `README.md:228-240`, `roles/proxmox_vm/tasks/present.yml:119,140`, `playbooks/discover.yml:119`, `ARCHITECTURE.md:327,609`

**What is wrong**

- Proxmox VE 9.0 removed the `VM.Monitor` privilege. Guest-agent reads such as `network-get-interfaces` now need `VM.GuestAgent.Audit`, which does not exist on 8.x.
- On 9.x, `pveum role add ... VM.Monitor ...` rejects the unknown privilege, so the role, the ACLs and the token are never created.
- A user who deletes the word gets a token without guest-agent access. Provisioning clones, then fails the agent poll with a 403 and an error that names a privilege that does not exist.
- The fakes enforce no privileges, so CI cannot see this.

**Failure scenario.** On a fresh PVE 9 host the first command of "Creating the Proxmox API token" fails. Working around it breaks address discovery, which is the headline feature.

**Fix.** Give a privilege list per version (8.x: `VM.Monitor`; 9.x: `VM.GuestAgent.Audit`) and name both in the error messages.

**Resolution (fixed).** The token recipe picks `VM.Monitor` (PVE 8) or `VM.GuestAgent.Audit` (PVE 9) from `pveversion` and gives a one-line fix for an existing role or an 8-to-9 upgrade. The error messages in present.yml and discover.yml name both privileges.

**Verified.** Privilege names checked against the PVE 9.0 release notes, pveum docs and qemu-server source; lint and syntax pass. Not run against a real PVE 8 or 9 host.

<a id="p1-4"></a>
### P1-4. Opting out of Remote Control leaves a running, enabled server behind

- **Area:** Remote Control, Security
- **Found by:** Claude Code, Docs
- **Confidence:** Confirmed by reading (Re-checked by reading)
- **Introduced:** 94c457e
- **Status:** Fixed
- **Where:** `roles/claude_code/tasks/main.yml:60-78`, `README.md:333`, `inventory/group_vars/all/defaults.yml:97-99`

**What is wrong**

- With a key set, or `claude_remote_control: false`, remote_control.yml is skipped entirely, and the only task that stops and disables the unit lives inside it.
- The README says adding a key turns Remote Control off, and the defaults say `false` opts out entirely. Neither touches a unit that already exists.

**Failure scenario.** The README's common case: push an API key with `TAGS=claude_code`. The server keeps accepting sessions from the phone. On the next reboot the enabled unit starts with the key in settings.json, is refused, and crash-loops to the start limit on every boot. Setting the flag false leaves a remote shell into the VM that the user believes is off.

**Fix.** Add the off branch: when Remote Control is off or a key is set, stop and disable the unit (tolerating its absence) and remove it.

**Resolution (fixed).** main.yml decides once (`claude_code_remote_control_wanted`) and, when Remote Control is off or a key is set, stops and disables an existing unit, deletes it and reloads systemd. A VM that never had the unit only gets a stat, so check mode cannot fail on it.

**Verified.** claude_code and configure scenarios passed. claude_code scenario side effect: switching the flag off removed the unit, and adding a key stopped a running server and removed it.

<a id="p1-5"></a>
### P1-5. The "session stayed up" check passes on the first `active` reading, which systemd reports while the server is still starting

- **Area:** Remote Control, Tests
- **Found by:** Claude Code, Tests, Performance
- **Confidence:** Reproduced (Re-checked by reading)
- **Introduced:** e6d1075
- **Status:** Fixed
- **Where:** `roles/claude_code/tasks/remote_control.yml:145-175`

**What is wrong**

- `until: stdout == 'active'` with 6 retries 5 s apart stops at the first sample, taken right after `systemctl start`. A `Type=simple` unit is active as soon as its process forks.
- Reproduced with a stub that reports `active` once and `inactive` afterwards: the assert passed and the play printed that Remote Control is live.
- The started path has no test at all; commit e6d1075 notes that no fake covers it.

**Failure scenario.** An ineligible plan, a Team organisation whose Owner has not enabled Remote Control, or P0-1 and P1-1. The server exits within seconds, while the run reports the session live.

**Fix.** Wait past startup, then require `ActiveState=active` with `NRestarts=0` (or the registration line in the journal). Cover it with a fake `claude` whose `remote-control` exits 1.

**Resolution (fixed).** The start restarts unless the unchanged unit is already active, then records the service's PID and restart count, waits 20 seconds and requires the same running process. The failure message shows both readings and the journalctl command.

**Verified.** claude_code and configure scenarios passed. claude_code scenario side effect: a stand-in server that exits after 3 seconds is caught at the stay-up check; one that stays up passes. The old check passed the exiting server.

<a id="p1-6"></a>
### P1-6. `Restart=on-failure` never restarts the server after it gives up on the network

- **Area:** Remote Control
- **Found by:** Claude Code
- **Confidence:** Confirmed against docs and binary (Re-checked the documented give-up behaviour; the exit code is from the reviewer's reading of the binary)
- **Introduced:** e6d1075
- **Status:** Fixed
- **Where:** `roles/claude_code/templates/claude-remote-control.service.j2:7-11,24-25`, `README.md:383-385`

**What is wrong**

- The docs say server mode gives up after roughly 10 minutes without a network, and the process exits. The reviewer traced that path, and the fatal-credential path, to `process.exit(0)`.
- `Restart=on-failure` restarts only on a non-zero exit or a signal. With `--spawn session` the server also exits cleanly when that session ends.
- Non-zero failures at boot hit `StartLimitBurst=5` within 300 s and the unit then stays failed.

**Failure scenario.** An overnight router or ISP outage longer than about ten minutes. The unit stops for good and the VM shows offline in the app until a reboot or the next `make configure`.

**Fix.** Use `Restart=always` with a backoff (`RestartSec`, plus `RestartSteps` and `RestartMaxDelaySec` on systemd 254 or later) instead of a hard start limit.

**Resolution (fixed).** The unit uses `Restart=always` with `RestartSec=10`, `RestartSteps=5` and `RestartMaxDelaySec=300`, and no start limit, so a clean exit after an outage is restarted with a growing delay. `systemctl stop` and the off branch still stop it for good.

**Verified.** claude_code and configure scenarios passed. claude_code scenario side effect: systemd restarted the exiting stand-in server (NRestarts > 0).

<a id="p1-7"></a>
### P1-7. A bare `make deploy` clones a new default VM and then reconfigures the whole fleet

- **Area:** Provisioning, Discovery
- **Found by:** Performance, Conventions, Docs
- **Confidence:** Reproduced
- **Introduced:** 1b2a739, on 11c6454's discovery default
- **Status:** Fixed
- **Where:** `deploy.yml:59-63`, `inventory/group_vars/all/defaults.yml:48-49`, `playbooks/discover.yml:39-43`, `Makefile:11-16`

**What is wrong**

- Provisioning falls back to `claude-on-proxmox-default`. Discovery falls back to every tagged VM when `vm_name` is undefined. `make deploy` runs both.
- Reproduced with a tagged fleet vm00 to vm02: one clone, then configure ran against the new default VM plus all three.
- The Makefile, README:186 and deploy.yml:5 all say an omitted VM_NAME means the default VM. `configure`, `check` and `claude-login` are silently fleet-wide.

**Failure scenario.** The README calls `make deploy` what you re-run after changing a variable. Run without VM_NAME on an existing fleet, it creates, boots and fully configures an unwanted VM (onboot, 4 GB, 32G), and pushes the change to every VM.

**Fix.** Without VM_NAME, have deploy refuse, or provision only when no tagged VM exists, and configure only what provisioning just published. Document what an omitted VM_NAME means for each target.

**Resolution (fixed).** deploy.yml imports configure.yml with `claude_vm_discovery: false`, so it configures exactly the VMs provisioning just published. What an omitted VM_NAME means is stated per target in the Makefile, deploy.yml, configure.yml and the README.

**Verified.** provision scenario passed on the merged tree. Local replay against the fake: a bare deploy on a tagged fleet configured only the default VM.

<a id="p1-8"></a>
### P1-8. Re-running against a VM someone shut down waits about six minutes, then fails with the wrong diagnosis

- **Area:** Provisioning, Performance
- **Found by:** Performance, Provisioning
- **Confidence:** Reproduced
- **Introduced:** e949eca
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/tasks/present.yml:75-81`, `roles/proxmox_vm/tasks/present.yml:112-142`

**What is wrong**

- The start is correctly gated so a deliberately stopped VM is not booted, but the guest-agent wait still runs. Proxmox answers "not running" with HTTP 500, which the early-exit pattern does not match, so all 60 retries are spent.
- Measured: 369.6 s, 61 polls, exit code 2. The failure blames the template, DHCP and the token; `proxmox_vm_start_existing` is not in the README.
- On main the VM was simply started.

**Failure scenario.** One VM of a fleet is shut down to free memory. `make deploy VM_NAME=<fleet>` hangs for six minutes and fails the whole run.

**Fix.** Use the `status` the lookup already returns. For a stopped VM this run did not start, skip the wait, report it as stopped and leave it unpublished.

**Resolution (fixed).** present.yml records `proxmox_vm_stopped` for an existing, configured VM left stopped; the NIC read, agent wait and address assert are skipped, with a message naming `proxmox_vm_start_existing`. provision.yml publishes only VMs with an address and reports stopped or failed ones.

**Verified.** proxmox_vm and provision scenarios passed: a stopped VM is reported and left stopped with no agent polls, and the opt-in starts it. Local replay: 8 s and exit 0, against 68 s and exit 2 before.

<a id="p1-9"></a>
### P1-9. The provision scenario's "must not touch other VMs" checks pass for the wrong reasons

- **Area:** Tests
- **Found by:** Tests
- **Confidence:** Reproduced with mutations
- **Introduced:** ccf9968, c43287e
- **Status:** Fixed
- **Where:** `molecule/provision/verify.yml:7-8,22-24,49-50,88-98`, `tests/molecule/fake_pve_api.py:117-122,214-218`, `molecule/provision/group_vars/all/fake_api.yml:13`

**What is wrong**

- `nonic` has a NIC. The fixture sends `net0=`, but the fake parses the body with `parse_qs`, which drops blank values; real Proxmox removes a NIC with `delete=net0` anyway.
- Discovery never reaches the intruder VMs: the scenario sets `vm_name: alpha,beta`, so narrowing happens first. And the fake refuses each VM's first two agent polls, so intruders are skipped for having no address.
- These mutations still pass converge and verify: reverting the `net_mac('')` default; replacing `claude_vm_tagged` with the unfiltered listing in discover.yml and list.yml; the same with `vm_name` removed.

**Failure scenario.** A refactor drops the ownership filter from discovery. CI stays green, and a fleet-wide `make configure` installs SSH keys, the GitHub token and the API key on every running VM in the cluster.

**Fix.** Give the fake per-VM agent readiness with intruders ready immediately, honour `delete=` so `nonic` really has no NIC, and run one discovery pass with `vm_name` unset.

**Resolution (fixed).** The fake honours `delete=`, nonic really loses its NIC, intruders' agents answer, and verify runs discovery fleet-wide and narrowed plus list.yml, asserting only this project's addressable VMs are published.

**Verified.** provision scenario passed (after making its duplicate-name check read stderr as well as stdout, where Molecule prints task failures). Mutation replay: removing the ownership filter or the `net_mac` default now fails verify.

<a id="p1-10"></a>
### P1-10. None of the safety checks added by the last review is exercised by a test

- **Area:** Tests
- **Found by:** Tests
- **Confidence:** Reproduced with a mutation
- **Introduced:** 6ef6982, fcd1632, 7fa3229, c43287e
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/tasks/present.yml:27-35`, `roles/proxmox_vm/vars/main.yml:41-44`, `roles/proxmox_vm/tasks/main.yml:63-70,88`, `playbooks/discover.yml:48-60`, `roles/proxmox_vm/molecule/default/molecule.yml`

**What is wrong**

- No scenario pins a VMID, runs `present` against an existing untagged VM, creates a duplicate name, or deletes a tagged template. Main's pinned `proxmox_vm_id: 200` was removed from the role scenario.
- Mutation: with the clone-collision assert deleted, provisioning `gamma` with `proxmox_vm_id: 9001` rewrote template 9000 to name `gamma`, tag `claude-on-proxmox`, user `dev`. Both scenarios still pass.
- After that, only the untested template clause of the ownership assert stands between `make destroy VM_NAME=gamma` and the template.

**Failure scenario.** A refactor reorders or drops one of these checks, CI stays green, and a pinned or racing clone rewrites the template. The same gap is why P0-2 went unnoticed.

**Fix.** Add side-effect cases: a colliding pinned VMID (assert the failure and an unchanged template); an existing untagged VM with `state: present`; two VMs with one name; `state: absent` on a tagged template.

**Resolution (fixed).** New side-effect cases: a colliding pinned VMID fails and leaves the template unchanged, duplicate names are refused for present and absent and by discovery, a tagged template is refused for absent, a stopped VM is left alone; a free pinned VMID is used.

**Verified.** proxmox_vm and provision scenarios passed. Mutation replay: removing each guarded check fails its test.

## P2: Medium

_An edge-case bug, a measurable regression, or docs that lead to a wrong action._

<a id="p2-1"></a>
### P2-1. The auth-failure pattern `40[13]` also matches VMIDs

- **Area:** Discovery, Provisioning
- **Found by:** Conventions, Provisioning, Tests
- **Confidence:** Reproduced (Re-checked the pattern)
- **Introduced:** e949eca
- **Status:** Fixed
- **Where:** `playbooks/discover.yml:73-74`, `playbooks/list.yml:51-52`, `roles/proxmox_vm/tasks/present.yml:121-123`

**What is wrong**

- `failed_when: msg is search('40[13]|[Pp]ermission|[Aa]uthentication')`. proxmoxer puts Proxmox's reason in the message, such as `500 Internal Server Error: VM 4013 is not running`, which matches.
- Reproduced with a copy of the fake that returns Proxmox's reason text: `make list` for a stopped VM 4013 went from exit 0 to exit 2.
- `40[13]` adds nothing: a real 403 already says "Permission check failed", and a 401 fails earlier at the first listing.

**Failure scenario.** Any stopped tagged VM whose VMID contains 401 or 403 (401, 1403, 4010 to 4039) fails `make list` and aborts `make configure` for the whole fleet.

**Fix.** Drop `40[13]` or anchor it to the status position (`': 40[13] '`), and keep the pattern in one shared place.

**Resolution (fixed).** A new `proxmox_access_denied` filter matches 401/403 only where proxmoxer puts the HTTP status (`"<status> <reason>: <content>"`), and discover.yml, list.yml and the role's agent wait all use it.

**Verified.** provision scenario passed with a stopped VM 4013 whose agent answers "VM 4013 is not running"; the old pattern fails discovery on it. Unit tests cover the VMID cases.

<a id="p2-2"></a>
### P2-2. provision.yml's publish play assumes every requested VM succeeded

- **Area:** Provisioning
- **Found by:** Conventions, Provisioning
- **Confidence:** Reproduced
- **Introduced:** 7fa3229
- **Status:** Fixed
- **Where:** `playbooks/provision.yml:67-87`

**What is wrong**

- Play 3 loops over `claude_vm_names`, not over the VMs that survived play 2.
- A VM that failed before recording its address crashes the loop with `'HostVarsVars' has no attribute 'proxmox_vm_address'`. A VM that timed out on the agent is announced as "up at" an empty address.

**Failure scenario.** In `make deploy`, one failed VM crashes the localhost publish play, so discovery and configuration are skipped for every VM that did succeed.

**Fix.** Add `when: hostvars[item].proxmox_vm_address | default('') | length > 0` to both loops.

**Resolution (fixed).** Fixed with P1-8: the publish play adds only VMs with an address and reports stopped or failed ones instead of crashing on a missing fact.

**Verified.** provision scenario passed. Local replay: with one VM failing, play 3 still published the other.

<a id="p2-3"></a>
### P2-3. `make deploy TAGS=...` silently skips building the template and creating VMs

- **Area:** Conventions
- **Found by:** Conventions
- **Confidence:** Reproduced (Re-checked the Makefile; introduced in this session)
- **Introduced:** 1b2a739
- **Status:** Fixed
- **Where:** `Makefile:95`

**What is wrong**

- The deploy target passes `$(TAG_ARGS)`. Only configure's roles are tagged, so `--tags claude_code` selects no template or provision tasks, while discovery still runs because it is tagged `always`.
- `--list-tasks --tags claude_code deploy.yml` confirms it. `make site` never passed tags, and the Makefile documents TAGS only for configure.

**Failure scenario.** `make deploy VM_NAME=newvm TAGS=claude_code` creates nothing, reports that no tagged VM matches, and exits 0.

**Fix.** Remove `$(TAG_ARGS)` from the deploy target.

**Resolution (fixed).** `make deploy` refuses TAGS with a pointer to `make configure VM_NAME=<name> TAGS=...`, and no longer passes them to ansible-playbook. Ignoring them instead would have turned a one-role push into a full deploy, template build included.

**Verified.** Makefile target checked; lint passes.

<a id="p2-4"></a>
### P2-4. Discovery and `make list` hard-code `net0` and ignore `proxmox_vm_nic`; the duplicated logic has already drifted

- **Area:** Discovery, Conventions
- **Found by:** Conventions, Provisioning
- **Confidence:** Confirmed by reading
- **Introduced:** 76be4fb, 2c3b20b
- **Status:** Fixed
- **Where:** `playbooks/discover.yml:88-90`, `playbooks/list.yml:71-74`, `roles/proxmox_vm/vars/main.yml:12-23`

**What is wrong**

- The role resolves the address through `proxmox_vm_nic`; both playbooks read `config.net0` directly. The previous review's P2‑2 was only half fixed.
- The same pattern repeats elsewhere: the Proxmox `module_defaults` block five times, the auth pattern three times (P2-1), VM_NAME narrowing twice. A templated group var for `module_defaults` works (reproduced).
- list.yml renders JSON in a `{% for %}` loop and parses it with `from_json` only because no filter returns the address.

**Failure scenario.** With a template whose LAN NIC is `net1` and `proxmox_vm_nic: net1`, provisioning succeeds, but `make configure` and `make claude-login` skip the VM and blame booting or the token, and `make list` shows `-`.

**Fix.** One filter (for example `guest_address(vm_info, nic)`) fed by a project-level NIC variable and used by the role and both playbooks, plus one group var for the API defaults.

**Resolution (fixed).** One project-level `proxmox_nic` feeds both the role and a new `guest_address(vm_info, nic)` filter that discovery and make list use; the API connection is one `proxmox_api_module_defaults` group var (a templated module_defaults entry was confirmed to work on ansible-core 2.21); VM_NAME narrowing lives in `claude_vm_requested`. list.yml no longer renders JSON in a loop.

**Verified.** provision scenario passed with a fixture whose LAN NIC is net1: found on net1 when proxmox_nic is net1. Hard-coding net0 in either playbook fails it.

<a id="p2-5"></a>
### P2-5. `make claude-login` and the role disagree on what "signed in" means, sending the user in a loop

- **Area:** Remote Control
- **Found by:** Claude Code, Conventions
- **Confidence:** Reproduced (Re-checked by reading)
- **Introduced:** e6d1075
- **Status:** Fixed
- **Where:** `playbooks/claude_login.yml:40-48`, `roles/claude_code/vars/main.yml:25-36`

**What is wrong**

- claude_login.yml checks `loggedIn` only; the role requires `loggedIn` and `authMethod == 'claude.ai'`.
- For `{"loggedIn": true, "authMethod": "api_key"}`, or a `setup-token` login, the role says to run `make claude-login`, which replies that the VM is already signed in and prints no command.

**Failure scenario.** A VM logged in with a Console account or a long-lived token can never be moved to Remote Control by following the output.

**Fix.** Define eligibility once and use it in both places. When a VM is logged in but ineligible, print `claude auth logout` followed by `claude auth login`.

**Resolution (fixed).** Eligibility and the login command are defined once in the claude_code role. `make claude-login` runs a new `login` entry point of the role instead of deciding for itself, so both say the same thing; a VM signed in but unable to use Remote Control is told what is in the way (an API key source, a setup-token, a provider setting) and what to run.

**Verified.** claude_code and configure scenarios passed. New unit tests run the real claude_login.yml against seven canned `claude auth status` outputs; 13 of 15 fail against the old code.

<a id="p2-6"></a>
### P2-6. The login command `make claude-login` prints fails with "command not found"

- **Area:** Remote Control
- **Found by:** Claude Code
- **Confidence:** Confirmed by reading (Re-checked: ~/.local/bin is added only by /etc/profile.d, for login shells)
- **Introduced:** e6d1075
- **Status:** Fixed
- **Where:** `playbooks/claude_login.yml:45-48`, `README.md:376`

**What is wrong**

- `ssh -t dev@<addr> "claude auth login"` runs a non-login, non-interactive shell. `~/.local/bin` is added only by `/etc/profile.d/local-bin-path.sh`, and Ubuntu's `.bashrc` returns early for non-interactive shells.
- The playbook checks status with `bash -lc`, and so does the role, for exactly this reason.

**Failure scenario.** The one manual step fails with `claude: command not found` on the default native install.

**Fix.** Print `ssh -t dev@<addr> "bash -lc 'claude auth login'"`, or the absolute path.

**Resolution (fixed).** The printed command is `ssh -t dev@<addr> "bash -lc 'claude auth login'"`, which puts the native install on PATH, and ends its line so no punctuation is pasted into it. The README shows the same command.

**Verified.** Unit tests assert the exact command for every login state that needs one.

<a id="p2-7"></a>
### P2-7. `make check` fails on any VM that does not have the Remote Control unit yet

- **Area:** Remote Control
- **Found by:** Claude Code
- **Confidence:** Confirmed by reading module source
- **Introduced:** e6d1075
- **Status:** Fixed
- **Where:** `roles/claude_code/tasks/remote_control.yml:126-143,180-185`

**What is wrong**

- In check mode `template` does not write the unit, and `systemd_service` then runs with `enabled` and `state`. The module's missing-unit check fails whether or not it is in check mode.
- The status and binary lookups before it are `check_mode: false`, so the play reaches that point.

**Failure scenario.** After pulling the branch, `make check` against an existing fleet fails on every VM, which is when a dry run is most wanted.

**Fix.** Skip both `systemd_service` tasks in check mode when the unit task reports a change.

**Resolution (fixed).** remote_control.yml stats the unit after templating it, and both systemd_service blocks require the file, so `make check` on a VM without the unit reports what would change instead of failing. A real run always has the file, so it behaves as before.

**Verified.** claude_code scenario side effect: dry runs with and without a login installed and started nothing. A unit test runs remote_control.yml in check mode against stand-ins; the old code failed with "Could not find the requested service".

<a id="p2-8"></a>
### P2-8. VMs created before this branch cannot be listed or destroyed, and the documented escape hatch is unreachable

- **Area:** Provisioning, Docs
- **Found by:** Provisioning, Docs
- **Confidence:** Reproduced
- **Introduced:** 172b930, fcd1632
- **Status:** Fixed
- **Where:** `playbooks/destroy.yml:44-55`, `roles/proxmox_vm/defaults/main.yml:22-25`, `roles/proxmox_vm/tasks/main.yml:94`

**What is wrong**

- Main tagged VMs `claude;ansible`. Discovery, list and destroy select only `claude-on-proxmox`.
- The role advertises `proxmox_vm_allow_untagged_delete` for VMs created before tagging, but destroy.yml's existence check rejects any untagged name first. Reproduced: "No VM named claude-dev was created by this project".
- The only route that works is P0-2: provision the old name to take it over, then destroy it.

**Failure scenario.** Someone upgrading finds their existing VMs invisible to `make list`, `make configure` and `make destroy`, with no documented way to adopt them.

**Fix.** Honour the flag in destroy.yml's existence check, and document tagging an existing VM with `qm set <vmid> --tags claude-on-proxmox`.

**Resolution (fixed).** destroy.yml's existence check honours `proxmox_vm_allow_untagged_delete` for untagged VMs and never admits a template, and its refusal says how to adopt a VM (add the tag) or use the flag. The README has an "Adopting VMs created before tagging" section. While proving this, a further bug surfaced and was fixed: the role itself deleted a template when the flag was set, because the template and tag checks shared one assert the flag skipped; the template check is now its own task.

**Verified.** provision scenario passed: a main-era VM tagged claude;ansible is refused, then deleted with the flag; the template is refused even with the flag. proxmox_vm scenario passed with the split checks.

<a id="p2-9"></a>
### P2-9. A run that dies after the settings call is never repaired, because the tag is written first

- **Area:** Provisioning
- **Found by:** Provisioning
- **Confidence:** Reproduced
- **Introduced:** 7fa3229
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/vars/main.yml:35-44`, `roles/proxmox_vm/tasks/present.yml:62-81`, `ARCHITECTURE.md:566-579`, `CLAUDE.md:85`

**What is wrong**

- The tag is written by the settings call. Resize and start are later, separate calls gated on the same condition.
- Simulated "settings applied, died before resize": every re-run skipped resize and start and failed at the agent wait, with the disk still 3.5 GB.

**Failure scenario.** A first run with `vm_disk_size` smaller than the image fails at resize after tagging, or is interrupted there. After the cause is fixed, re-runs never resize or start the VM.

**Fix.** Run the resize unconditionally (`proxmox_disk` does nothing when the size already matches) and decide the start from observed status, or write the tag in a final call.

**Resolution (fixed).** The settings call also writes a `claude-on-proxmox-unfinished` tag, removed in a call after the start succeeds, and `proxmox_vm_needs_configuration` stays true while it is present. A run that dies at the resize or the start is finished by the next run, while a finished VM someone shut down is still left stopped. The resize stays conditional, because proxmox_disk compares size strings and cannot shrink. The README's User Tag Access note lists the new tag.

**Verified.** proxmox_vm scenario passed: a VM with the unfinished tag, not resized and stopped, was resized, started and untagged, and a second run sent it only reads. The old code left it stopped at 3.5 GB.

<a id="p2-10"></a>
### P2-10. One offline cluster node breaks deploy, configure, list and destroy

- **Area:** Discovery, Provisioning
- **Found by:** Provisioning
- **Confidence:** Reproduced against the fake (Proxmox's real offline-node error was not reproduced)
- **Introduced:** 11c6454, 1b2a739
- **Status:** Fixed
- **Where:** `deploy.yml:30-34`, `playbooks/discover.yml:30-33`, `playbooks/list.yml:24-28`, `playbooks/destroy.yml:38-42`

**What is wrong**

- An unfiltered `proxmox_vm_info` queries every node that owns a listed VM and turns any exception into a module failure.
- A fake with an unrelated VM on an erroring node failed deploy.yml's first play and list.yml.
- On main, configure never called the API.

**Failure scenario.** In a homelab cluster with one node powered off, `make deploy`, `make configure`, `make list` and `make destroy` all fail before doing anything.

**Fix.** Filter deploy.yml's check by `name`. In discovery, list and destroy, take name, tags, status and template from `/cluster/resources` and query only the tagged VMs.

**Resolution (fixed).** Fleet listings read `/cluster/resources` once through a shared tasks file (token kept out of logs, TLS governed by proxmox_validate_certs, runs in check mode) instead of an unfiltered proxmox_vm_info that queries every node; deploy.yml looks the template up by name.

**Verified.** provision scenario passed with a second fake node that answers everything with 595; the old code fails discovery on it.

<a id="p2-11"></a>
### P2-11. The clone throttle holds back every VM's boot until the last clone finishes

- **Area:** Performance
- **Found by:** Performance
- **Confidence:** Reproduced with simulated clone times
- **Introduced:** 7fa3229
- **Status:** Fixed
- **Where:** `roles/proxmox_vm/tasks/present.yml:3-22`

**What is wrong**

- `throttle: 1` is on the clone only, as documented, but under the linear strategy the settings, resize and start tasks wait for every host's clone. Measured with 10 s clones and 30 s boots for 5 VMs: 110 s against 56 s without the throttle, with all five starts at 70 s.
- Estimated for 20 VMs with 10 s clones: 258 s throttled against 26 s in parallel. Disk contention on a real host narrows the gap.
- The throttle prevents a real VMID race; the cost comes from the fix's shape, not its existence.

**Failure scenario.** First-time provisioning of a fleet adds roughly (N - 1) x clone time before any VM starts to boot.

**Fix.** Allocate distinct VMIDs once in provision.yml's first play and pass `proxmox_vm_id` to each host, then drop the throttle; the `is changed` assert still catches outside collisions.

**Resolution (fixed).** provision.yml numbers a fleet's new VMs once, from `/cluster/nextid` and skipping every VMID in use (read through the same shared cluster listing discovery uses), so the clones run at the same time; the role throttles a clone only when Proxmox picks its VMID. One pinned VMID for several new VMs is refused up front. `strategy: free` was measured and rejected: the throttle is not enforced there and clones collided.

**Verified.** proxmox_vm and provision scenarios passed; two unpinned and two pinned VMs created concurrently each got their own VMID. Measured against a fake with 10 s clones: 5 VMs in 57 s with all starts at 25 s, against 100 s with starts at 69 s.

<a id="p2-12"></a>
### P2-12. destroy.yml deletes VMs one at a time, where main deleted them in parallel

- **Area:** Performance
- **Found by:** Performance
- **Confidence:** Reproduced (Re-checked main's destroy.yml (hosts: claude_vms))
- **Introduced:** 65215e4
- **Status:** Fixed
- **Where:** `playbooks/destroy.yml:57-66`

**What is wrong**

- The role runs in an `include_role` loop on localhost, so each VM's lookup, stop, delete and cleanup run in sequence. Main's destroy.yml ran `hosts: claude_vms` across forks.
- Measured against the fake, which finishes tasks instantly: 39 s for 5 VMs and 170 s for 20, against 55 s for 20 with provision.yml's per-host shape. Real stop and disk removal add to each VM in turn.

**Failure scenario.** Destroy time grows linearly with fleet size.

**Fix.** Use provision.yml's shape: a localhost play for the prompt and checks that adds placeholder hosts, then the role across those hosts. That also removes the need for loop-safe lazy vars in the role.

**Resolution (fixed).** destroy.yml is two plays, like provision.yml: a localhost play confirms, lists, checks and add_hosts, then the role runs across those hosts in parallel. Writes to known_hosts are throttled to one at a time.

**Verified.** provision scenario passed: two VMs deleted in one run, refusals delete nothing. Measured against the fake: 20 VMs in 41 s, against 168 s before.

<a id="p2-13"></a>
### P2-13. `make deploy` repeats, one VM at a time, the discovery that provisioning just did

- **Area:** Performance
- **Found by:** Performance
- **Confidence:** Reproduced
- **Introduced:** 1b2a739
- **Status:** Fixed
- **Where:** `deploy.yml:62-63`, `playbooks/configure.yml:5-12`, `playbooks/discover.yml:62-79`, `playbooks/list.yml:41-57`

**What is wrong**

- Provisioning's third play already publishes the requested VMs with addresses. configure.yml then imports discovery, which lists everything again and makes one detail call per VM in a localhost loop. Each `proxmox_vm_info` call re-fetches `/version`, `/cluster/resources` and the node's VM list.
- A converged deploy makes 16N + 6 HTTP calls and 4N + 2 cluster listings. For 20 VMs, 23.6 s of a 75 s run was repeated discovery. list.yml's loop over running agents also polls stopped VMs.

**Failure scenario.** About a second per VM of duplicated sequential work on every deploy, more over TLS on a real network; a hung agent adds its timeout to each run.

**Fix.** Skip discovery in deploy.yml, since provisioning has published the hosts (this also shrinks P1-7). Fan out the detail calls, and filter list.yml's loop to running VMs.

**Resolution (fixed).** Fixed with P1-7: deploy.yml no longer re-runs discovery before configuring.

**Verified.** provision scenario passed. Local replay: a converged deploy made no per-VM agent polls for discovery.

<a id="p2-14"></a>
### P2-14. CI's gitleaks scan misses a secret committed and then removed within a pull request

- **Area:** Security, CI
- **Found by:** Security
- **Confidence:** Reproduced with gitleaks 8.30.0
- **Introduced:** 2b3c4d0
- **Status:** Fixed
- **Where:** `.github/workflows/ci.yml:72-78`, `SECURITY.md (Secret scanning)`

**What is wrong**

- CI runs `gitleaks dir .`, which scans the files as they are at the end. A token added in one commit and deleted in the next: `dir` found nothing, `gitleaks git HEAD~2..HEAD` found it.
- SECURITY.md presents the tree scan as CI's defence without that caveat.

**Failure scenario.** A contributor commits a token and removes it before merging. It stays in the public history, and CI is green.

**Fix.** Add a `gitleaks git` scan over the pull request's commit range alongside the tree scan.

**Resolution (fixed).** CI keeps the tree scan and adds a gitleaks scan of the commits a push or pull request brings in, with full history checked out. The range is computed from event values passed through `env:`, falls back to all of head's history when the base is unavailable, and is checked with git first because gitleaks exits 0 when git fails. SECURITY.md and ARCHITECTURE describe both scans.

**Verified.** Dry run of both steps with the pinned gitleaks 8.30.0: a token added then removed in a PR now fails; clean PRs, pushes, new branches and force pushes behave as intended. Not yet run on GitHub.

<a id="p2-15"></a>
### P2-15. The README's Remote Control permissions example has no effect on remote sessions

- **Area:** Docs, Remote Control
- **Found by:** Docs
- **Confidence:** Confirmed against Claude Code docs
- **Introduced:** 94c457e
- **Status:** Fixed
- **Where:** `README.md:396-407`, `roles/claude_code/templates/claude-remote-control.service.j2:23`

**What is wrong**

- The example sets `permissions.defaultMode`, suggesting `bypassPermissions`. The unit always passes `--permission-mode`, and the permission-modes docs give the flag precedence over `defaultMode`.
- According to the same docs, Remote Control sessions can be switched between manual, accept-edits and plan modes from the app; bypass is not among them.

**Failure scenario.** A user raises `defaultMode` to cut down approvals from the phone and sees no change.

**Fix.** Point the example at `claude_code_remote_control_permission_mode`, and say `defaultMode` affects interactive `claude` only.

**Resolution (fixed).** The README example sets `claude_code_remote_control_permission_mode` for remote sessions and says `permissions.defaultMode` only affects a `claude` started without `--permission-mode`. The role's descriptions no longer suggest bypassPermissions.

**Verified.** Checked against the Claude Code permission-modes and Remote Control docs.

<a id="p2-16"></a>
### P2-16. The multi-node guidance is wrong: shared storage does not let this role clone onto another node

- **Area:** Docs
- **Found by:** Docs
- **Confidence:** Confirmed by reading module and PVE source
- **Introduced:** d4f1471
- **Status:** Fixed
- **Where:** `README.md:99-106`, `roles/proxmox_vm/tasks/present.yml:4-13`

**What is wrong**

- The role never passes `target`. The clone is posted to `proxmox_node`, and Proxmox loads the source VM's config on that node.
- So `proxmox_node` must be the node that holds the template, whatever the storage. Shared storage only matters when cloning with a `target`.

**Failure scenario.** A template on shared storage on node A and `proxmox_node: B`: the clone fails.

**Fix.** State that `proxmox_node` must hold the template, or pass `target` to the clone.

**Resolution (fixed).** The multi-node guidance says `proxmox_node` must be the node holding the template, because the clone is sent there; shared storage only matters for a target-node clone, which this project does not use.

**Verified.** Checked against the Proxmox clone API docs, qemu-server source and the installed proxmox_kvm.

<a id="p2-17"></a>
### P2-17. The fixed-address advice breaks as soon as there is more than one VM

- **Area:** Docs, Security
- **Found by:** Docs
- **Confidence:** Confirmed by reading
- **Introduced:** 6d94361, 5628b98, d4f1471
- **Status:** Fixed
- **Where:** `README.md:67-70,271`, `SECURITY.md:41-42`, `inventory/group_vars/all/local.yml.example:25-28`

**What is wrong**

- `proxmox_vm_ipconfig` is a single fleet-wide string. SECURITY.md recommends it as hardening and the README offers it as the alternative to DHCP; only local.yml.example admits it suits a single-VM run.
- The README also suggests a DHCP reservation for the VM's MAC to keep an address across rebuilds, but Proxmox generates a new MAC on every clone and the project cannot set one.

**Failure scenario.** Following SECURITY.md gives every later VM the same address, and a reservation stops matching after the first destroy and provision.

**Fix.** Describe both as lasting only for one VM's lifetime, or add per-VM MAC and ipconfig inputs.

**Resolution (fixed).** The README and local.yml.example say `proxmox_vm_ipconfig` applies to every VM a run provisions and a MAC-keyed DHCP reservation lasts only for that VM. SECURITY.md no longer recommends it as fleet hardening.

**Verified.** MAC randomisation on clone confirmed in the qm manual; lint passes.

<a id="p2-18"></a>
### P2-18. The fresh-host repository step leaves the Ceph enterprise repository enabled

- **Area:** Docs
- **Found by:** Docs
- **Confidence:** Confirmed against the Proxmox wiki
- **Introduced:** d4f1471
- **Status:** Fixed
- **Where:** `README.md:34-40`, `roles/proxmox_template/tasks/main.yml:65-72`

**What is wrong**

- The step says to disable the enterprise entry, singular. A default install configures both the PVE and the Ceph enterprise repositories.
- The template role runs apt with `update_cache: true`, which fails on any failed fetch.

**Failure scenario.** A user follows step 1 and `make template` still fails at "Install libguestfs-tools", the failure the step promises to prevent.

**Fix.** Say to disable both enterprise entries, or to add Ceph's no-subscription repository.

**Resolution (fixed).** Fresh-host step 1 says to disable every enterprise repository (PVE and Ceph) in the UI and add the no-subscription ones, which works on both 8.x and 9.x.

**Verified.** Checked against the Proxmox Package Repositories docs for 9.x and 8.x.

<a id="p2-19"></a>
### P2-19. The fake always sends `tags: ""`, hiding a guard every real cluster needs

- **Area:** Tests
- **Found by:** Tests
- **Confidence:** Reproduced with a mutation
- **Introduced:** 11c6454
- **Status:** Fixed
- **Where:** `tests/molecule/fake_pve_api.py:101`, `inventory/group_vars/all/defaults.yml:44-47`

**What is wrong**

- Real Proxmox omits `tags` for an untagged guest, and on ansible-core 2.21 the filter fails without `selectattr('tags', 'defined')`.
- Removing that guard still passes converge and verify. The comma-separated tag format CLAUDE.md warns about is never exercised either.

**Failure scenario.** A simplified `claude_vm_tagged` ships green, then `make configure`, `make list` and `make destroy` crash on any cluster with one untagged VM.

**Fix.** Omit `tags` in the fake when a VM has none, and add a comma-tagged fixture.

**Resolution (fixed).** The fake omits `tags` for an untagged VM, as Proxmox does, and a fixture keeps comma-joined tags, so both the defined-guard and both separators are exercised.

**Verified.** provision scenario passed. Removing the defined-guard or making the tag pattern ';'-only fails it.

<a id="p2-20"></a>
### P2-20. `--tags`, destroy.yml, deploy.yml and claude_login.yml are never run by a test

- **Area:** Tests
- **Found by:** Tests
- **Confidence:** Reproduced
- **Introduced:** 86e8e83, 1b2a739, e6d1075
- **Status:** Fixed
- **Where:** `playbooks/configure.yml:12`, `playbooks/destroy.yml`, `deploy.yml`, `playbooks/claude_login.yml`

**What is wrong**

- No scenario passes `--tags`. Removing `tags: [always]` makes `--list-tasks --tags claude_code` show discovery with no tasks, which is the bug 86e8e83 fixed, and nothing would notice.
- The three playbooks are only syntax-checked. They work against the fake today: the confirmation default, refusal of an untagged name, and the template check both ways.

**Failure scenario.** A regression in the API-key workflow (`make configure TAGS=claude_code`) or in destroy's confirmation ships green.

**Fix.** Run destroy.yml in the provision scenario's verify (untagged refused, tagged deleted) and add a `--tags claude_code` stage to the configure scenario.

**Resolution (fixed).** A unit test asserts `--tags claude_code` still lists discovery, claude_login.yml runs against canned logins, and the provision scenario now runs destroy.yml (unconfirmed, untagged, template, flag, two VMs at once), deploy.yml with the template present and absent, and discovery in check mode.

**Verified.** claude_code, configure, proxmox_vm and provision scenarios passed; each new check fails against a mutation of the code it guards.

## P3: Low

_Minor correctness, cleanup or staleness._

<a id="p3-1"></a>1
### P3-1. Fact-cache eviction and the host-key fallback in destroy never take effect

- **Area:** Provisioning
- **Found by:** Conventions, Provisioning, Security, Performance
- **Confidence:** Reproduced (Re-checked: .cache/facts holds s1_-prefixed files)
- **Introduced:** e949eca
- **Status:** Fixed
- **Resolution (fixed).** Removed the broken per-VM fact-cache task and the `proxmox_vm_fact_cache` var (the jsonfile backend prefixes entries, e.g. `s1_<host>`, so the computed `<cache>/<host>` path never matched; `make destroy` already clears `.cache/facts` wholesale), and dropped the host-key fallback to `hostvars[...].ansible_host` that destroy's missing discovery could never populate. `proxmox_vm_forget_address` is now just the agent's reported address. Verified: `proxmox_vm` molecule scenario green through converge, idempotence, side_effect and verify.
- **Where:** `roles/proxmox_vm/vars/main.yml:50-58`, `roles/proxmox_vm/tasks/absent.yml:48-67`

**What is wrong**

- ansible-core 2.21 names jsonfile cache entries `s1_<host>`. The role deletes `<cache>/<host>`, which never exists; this checkout's `.cache/facts/` holds `s1_claude-dev` and similar.
- The host-key fallback reads `hostvars[name].ansible_host`, but destroy.yml runs no discovery, so the name is never in the inventory.
- Only the Makefile's `rm -rf .cache/facts` works, and it discards every VM's facts. These were the previous review's P3‑10 and P2‑4 fixes.

**Failure scenario.** A VM deleted outside `make destroy` whose name is reused within an hour gets the dead VM's facts. Impact is low: the facts the roles read are the same for every clone of one template.

**Fix.** Clear facts through Ansible (`meta: clear_facts` in a per-host play, which fits P2-12) or delete the task; make the fallback real or remove it and its documentation.

<a id="p3-2"></a>2
### P3-2. The `~/.claude.json` tasks are not `no_log`, so `make check` prints account data

- **Area:** Security
- **Found by:** Conventions, Security
- **Confidence:** Reproduced
- **Introduced:** e6d1075
- **Status:** Fixed
- **Resolution (fixed).** Added `no_log: true` to the slurp, the parse and the whole-file write of `~/.claude.json` in remote_control.yml. Verified: lint and the Remote Control unit tests.
- **Where:** `roles/claude_code/tasks/remote_control.yml:70-102`

**What is wrong**

- Every settings.yml task is `no_log`; the read, parse and whole-file write of `~/.claude.json` are not.
- Under `--diff`, the first seeding run printed a planted `oauthAccount` email and `primaryApiKey`.

**Failure scenario.** Account identity, and on some Claude Code versions credential material, lands in terminal or CI logs.

**Fix.** Add `no_log: true` to those three tasks.

<a id="p3-3"></a>3
### P3-3. A quote in `VM_NAME` breaks out of the Makefile's quoting

- **Area:** Security
- **Found by:** Security
- **Confidence:** Reproduced
- **Introduced:** 172b930
- **Status:** Fixed
- **Resolution (fixed).** A `vm-name-check` Make target rejects anything but `[A-Za-z0-9._,-]` in VM_NAME, reading the value from the environment so the check itself is safe for the values it rejects; wired into every VM target. Verified: `make list VM_NAME="a'b"` and `VM_NAME="alpha; rm -rf x"` now error, while a valid name and an empty one still pass.
- **Where:** `Makefile:16`

**What is wrong**

- `VM_ARGS := -e '{"vm_name": "$(VM_NAME)"}'`. A single quote ends the shell string and the rest runs as a command; a double quote reshapes the JSON and adds extra variables, such as `proxmox_vm_allow_untagged_delete`.
- It needs the user's own command line, so no privilege boundary is crossed. The comment's claim that the value arrives whole is untrue for such values.

**Failure scenario.** A copied VM name containing a quote runs part of it as a shell command or silently sets unrelated variables.

**Fix.** Reject quotes and shell metacharacters in the Makefile, or pass the value through an environment variable.

<a id="p3-4"></a>4
### P3-4. The new `proxmox_template` booleans are bare conditionals, which is fatal when set with `-e`

- **Area:** Conventions
- **Found by:** Conventions
- **Confidence:** Reproduced (same mechanism)
- **Introduced:** 3ee7563
- **Status:** Fixed
- **Resolution (fixed).** Added `| bool` to all three `proxmox_template_install_guest_agent` / `proxmox_template_manage_libguestfs` conditionals. Verified: lint and syntax.
- **Where:** `roles/proxmox_template/tasks/main.yml:56,59,73`

**What is wrong**

- `if proxmox_template_install_guest_agent` has no `| bool`. Argument specs do not coerce, so `-e ...=false` arrives as a string and ansible-core 2.21 rejects the conditional.
- The branch fixed exactly this for `proxmox_vm_force_update`.

**Failure scenario.** `make template ANSIBLE_ARGS='-e proxmox_template_install_guest_agent=false'` fails.

**Fix.** Add `| bool` in all three places.

<a id="p3-5"></a>5
### P3-5. Discovery silently drops a VM the user asked for by name

- **Area:** Discovery
- **Found by:** Provisioning
- **Confidence:** Reproduced
- **Introduced:** 11c6454
- **Status:** Fixed
- **Resolution (fixed).** discover.yml now fails, when VM_NAME is given, naming every requested VM that got no address (stopped, still booting, unreachable, or untagged), rather than configuring the rest and exiting 0. Verified: a new `provision` scenario assertion drives `vm_name=parked` (a tagged but stopped VM) as a child run and confirms the loud failure; the existing `vm_name=beta`/`netone` runs still pass.
- **Where:** `playbooks/discover.yml:98-120`

**What is wrong**

- A named VM with no address is skipped, and the explanation only prints when the whole group is empty.
- `vm_name: delta,gamma` with gamma stopped configured delta only, reported no failures, and never mentioned gamma.

**Failure scenario.** `make configure VM_NAME=alpha,beta TAGS=claude_code` to push a key reports success while beta was never touched.

**Fix.** When `vm_name` is set, warn or fail with the requested VMs that were not added.

<a id="p3-6"></a>6
### P3-6. Remote Control's pre-flight check misses real blockers and includes a false one

- **Area:** Remote Control
- **Found by:** Claude Code
- **Confidence:** Confirmed by reading
- **Introduced:** e6d1075
- **Status:** Fixed
- **Resolution (fixed).** The preflight now reads the on-disk settings.json (what Claude Code actually starts with, not the `claude_code_settings` variable), allows `ANTHROPIC_BASE_URL` only when its host is `api.anthropic.com`, and extends the blocker list with `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, the Bedrock and Vertex switches, and the top-level `apiKeyHelper`. Verified: lint, syntax and the Remote Control unit tests; the claude_code molecule scenario could not run here because its prepare stage hit an apt GPG error on the arm64 Ubuntu ports mirror (environmental).
- **Where:** `roles/claude_code/tasks/remote_control.yml:19-31`, `roles/claude_code/vars/main.yml:8-12`

**What is wrong**

- It rejects any `ANTHROPIC_BASE_URL`, including `https://api.anthropic.com`; the docs only block other hosts.
- It does not check `ANTHROPIC_AUTH_TOKEN`, `apiKeyHelper`, `CLAUDE_CODE_OAUTH_TOKEN` or the Bedrock and Vertex switches, which the docs name as blockers.
- It reads the Ansible variable rather than the `env` block on disk, so a removed variable still blocks, as in P1-1.

**Failure scenario.** A blocked VM passes the check and fails at startup, or an allowed configuration is refused.

**Fix.** Compare the URL's host, extend the list, and check the merged on-disk `env`.

<a id="p3-7"></a>7
### P3-7. Two Remote Control misconfigurations fail with unhelpful errors

- **Area:** Remote Control
- **Found by:** Claude Code
- **Confidence:** Reproduced / confirmed by reading
- **Introduced:** e6d1075
- **Status:** Fixed
- **Resolution (fixed).** `failed_when: false` on the `command -v claude` lookup makes the friendly 'not on PATH' assert reachable, and a new assert requires a git repository at the session directory when spawn is `worktree`, failing with a clear message instead of the server exiting silently. Verified: lint, syntax and the Remote Control unit tests (the claude_code scenario was blocked by the same apt mirror issue as P3-6).
- **Where:** `roles/claude_code/tasks/remote_control.yml:44-64`, `roles/claude_code/meta/argument_specs.yml:61-65`

**What is wrong**

- The friendly "claude is not on PATH" assert is unreachable: `command -v` exits 1 and the lookup has no `failed_when`, so it fails first with a bare exit code.
- `claude_code_remote_control_spawn: worktree` needs a git repository at startup. The default `~/projects` is never one, so the server exits immediately.

**Failure scenario.** Both misconfigurations surface as unexplained failures instead of the intended messages.

**Fix.** Add `failed_when: false` to the lookup, and assert a git repository when spawn is `worktree`.

<a id="p3-8"></a>8
### P3-8. The explanation of why `make deploy` opens no SSH connection is wrong

- **Area:** Docs
- **Found by:** Docs
- **Confidence:** Reproduced (Re-checked with a local reproduction; introduced in this session)
- **Introduced:** 1b2a739
- **Status:** Fixed
- **Resolution (fixed).** ARCHITECTURE.md §4.7, the template.yml comment and README now credit the import-level `when:` (which ansible-core applies to the implicit fact-gather too) for the skipped SSH connection, and describe `gather_facts: false` as a first-run optimisation rather than the mechanism. The stale sentence in the 1b2a739 commit message is left for the history rewrite (P3-12).
- **Where:** `ARCHITECTURE.md:262-266`, `playbooks/template.yml:7-8`, `README.md:156-157`, commit message of 1b2a739

**What is wrong**

- The docs say the guard works only because `template.yml` sets `gather_facts: false`, since a play that gathers facts would connect before any condition is evaluated.
- ansible-core also applies an import's `when:` to the implicit fact-gathering task. With `gather_facts: true` and a false condition, fact gathering is skipped and nothing is unreachable; with a true condition it connects.
- The behaviour is right. The mechanism contributors are told is load-bearing is not.

**Failure scenario.** A contributor preserves or "fixes" the wrong thing when changing the template play.

**Fix.** Credit the import-level `when:`, and describe `gather_facts: false` as harmless rather than required.

<a id="p3-9"></a>9
### P3-9. CLAUDE.md, ARCHITECTURE.md and ansible.cfg describe Ansible behaviour incorrectly

- **Area:** Docs
- **Found by:** Docs, Claude Code
- **Confidence:** Reproduced
- **Introduced:** d09d82a and earlier comments
- **Status:** Fixed
- **Resolution (fixed).** CLAUDE.md and ARCHITECTURE.md now say `vars_prompt` *silently takes its default* (not hangs) without a terminal — confirmed by running a prompt under `</dev/null`, which warns and uses the default or None. ansible.cfg and ARCHITECTURE.md now say setting `inventory_ignore_extensions` *replaces* the defaults — confirmed: the effective list lacks ansible's own `.ini`/`.toml`. The §9 API-key claim already said the role 'skips' rather than asserts.
- **Where:** `CLAUDE.md:99-100`, `ARCHITECTURE.md:235-236,630,632-635,803-805`, `ansible.cfg:5-6`

**What is wrong**

- "`vars_prompt` needs a default or a run with no terminal hangs": without a terminal, ansible-core warns and uses the default, or None. The real risk is silently taking the default.
- "`inventory_ignore_extensions` extends Ansible's defaults": setting it replaces that list. Reproduced.
- ARCHITECTURE section 9 still says the role asserts an API key and Remote Control are never configured together; since 94c457e it skips instead.

**Failure scenario.** These files are what agents treat as ground truth, so a wrong rule gets applied to future changes.

**Fix.** Correct each statement.

<a id="p3-10"></a>10
### P3-10. `make test` runs every linter twice, and can rewrite files

- **Area:** Performance, Conventions
- **Found by:** Performance
- **Confidence:** Confirmed by reading
- **Introduced:** 172b930
- **Status:** Fixed
- **Resolution (fixed).** `make test` exports the same `SKIP=` list CI uses to its `pre-commit` prerequisite (a target-specific `export`, confirmed to reach the prerequisite on Make 3.81), so the linters `make lint` already ran do not run a second time and ruff-format cannot rewrite files; standalone `make pre-commit` still runs every hook.
- **Where:** `Makefile:161,172`, `.pre-commit-config.yaml`

**What is wrong**

- `make test` runs `make lint`, then `pre-commit run --all-files`, which runs yamllint, ansible-lint and ruff again, the second ruff pass with `--fix` and `format`.
- CI avoids this with a `SKIP=` list; the Makefile does not.

**Failure scenario.** The production-profile ansible-lint pass runs twice locally, and a test run can modify files.

**Fix.** Pass the same `SKIP=` list in the Makefile.

<a id="p3-11"></a>11
### P3-11. The guest-agent poll assertion is not limited to the converge stage it describes

- **Area:** Tests
- **Found by:** Tests
- **Confidence:** Reproduced
- **Introduced:** 7f33ea3
- **Status:** Fixed
- **Resolution (fixed).** converge.yml writes a boundary marker (a path-bearing log line whose `/converge-end` path no assertion's pattern matches, so every `selectattr('path', ...)` across verify.yml and side_effect.yml skips it) at the end of each converge run, and verify.yml counts only 9001's agent polls before the first marker, asserting exactly `AGENT_READY_AFTER + 1 = 3`. Verified: `proxmox_vm` scenario green.
- **Where:** `roles/proxmox_vm/molecule/default/verify.yml:72-86`

**What is wrong**

- It counts polls by VMID over the whole run. With the fake's retry disabled, polls from idempotence and side-effect still bring the count to 3, so `>= 3` passes.
- Converge would still fail if the role lost its retry, so the damage is limited.

**Failure scenario.** A fake change stops exercising the wait loop and this assertion stays green.

**Fix.** Count only converge's polls, up to the first idempotence marker in the call log, and assert the exact number.

<a id="p3-12"></a>12
### P3-12. The commit history is not ready to merge

- **Area:** Conventions
- **Found by:** Conventions
- **Confidence:** Reproduced
- **Introduced:** Branch history
- **Status:** Deferred
- **Resolution (deferred).** Not done. Squashing the fixup commits and fixing the trailer blank lines is a history rewrite that needs a clean working tree (the fixes here are uncommitted) and interactive rebase, which this environment does not provide, and the review's 'while splitting the branch' intent is a call for the author. Left for a manual `git rebase -i` once the fixes are committed.
- **Where:** git log origin/main..HEAD

**What is wrong**

- Several commits repair earlier commits on the same branch: d0bb894 and 4e427a2 fix c43287e; 86e8e83 fixes 11c6454; 94c457e reverses e6d1075's default; ed41205 fixes 3ee7563; fcd1632 and 6ef6982 close states that 7364bfd and 65215e4 introduced. `git bisect` on main could land on commits known to reconfigure the template or delete unowned VMs.
- 3ee7563, 7364bfd, 65215e4 and 7fa94d1 lack the blank line before their trailers, so the co-author trailer does not parse.
- All 34 subjects follow Conventional Commits.

**Failure scenario.** Main's linear history would carry known-broken intermediate states and lose attribution.

**Fix.** Squash each fixup into the commit it repairs while splitting the branch.

<a id="p3-13"></a>13
### P3-13. Stale statements across docs and comments

- **Area:** Docs
- **Found by:** Docs, Conventions, Claude Code
- **Confidence:** Confirmed by reading
- **Introduced:** Various
- **Status:** Fixed
- **Resolution (fixed).** Corrected each line: the Galaxy pins (ci.yml, README) and the canary's non-role in bumps (requirements.yml); `make unit`'s real coverage (README, CONTRIBUTING); the CI job list including the playbook/provision job (README); the playbook list now lists list.yml, claude_login.yml and playbooks/tasks/ (README); the computed-internals count is now six (ARCHITECTURE §6.3); `make test` is no longer called a strict CI superset (ARCHITECTURE, Makefile); `make init` points GitHub credentials at vault.yml (Makefile); the re-run/hardware caveat, the Team/Enterprise Owner note and the User Tag Access path (README); destroy.yml's prompt-runs-before-the-check order; the systemd unit comment (a key arrives via settings.json, not the unit); and the configure scenario's api-key-wiring note.
- **Where:** See each item

**What is wrong**

- "Galaxy collections (pinned as ranges)" at ci.yml:11 and README:467; requirements.yml pins exact versions.
- requirements.yml:10 says the canary flags collection bumps; the canary uses the same pins and Dependabot has no Galaxy ecosystem, so nothing does.
- Makefile:20-21 says VM_NAME reaches the name check in provision.yml; 7468bd5 moved it into the role.
- `make unit` is described as github_repos tests only (CONTRIBUTING:28, README:420); it runs 87 tests across three files.
- README:461-463 describes CI without the provision job and still mentions gitleaks through pre-commit.
- README:486's playbook list omits list.yml and claude_login.yml.
- ARCHITECTURE:337 and 501 say five computed internals; there are six.
- ARCHITECTURE:781 and Makefile:158 call `make test` a superset of CI; it does not run CI's tree gitleaks scan.
- `make init` (Makefile:59) says the GitHub username goes in local.yml; local.yml.example says vault.yml.
- README:146-147 says to re-run `make deploy` after changing a variable; hardware variables are not reapplied to existing VMs, as README:307-308 says.
- README:358-359 omits that Team and Enterprise need an Owner to enable Remote Control first.
- README:241 gives "Tag Style, User Tag Access" as one path; they are separate options.
- destroy.yml:8-10 says existence is checked first; the prompt runs before it.
- The unit template's comment (lines 20-22) implies the key could reach the server through the unit; it arrives only through settings.json.
- The configure scenario no longer exercises the `anthropic_api_key` to `claude_code_anthropic_api_key` wiring.

**Failure scenario.** Readers and agents act on outdated descriptions of dependencies, CI and behaviour.

**Fix.** Update each line.

## Fixes from the previous review that do not hold

`docs/review-multi-vm-dhcp-discovery.md` marked these fixed.

| Previous ID | Claimed | Actually | Now |
|---|---|---|---|
| P1-3 | Tracked-file guard gaps closed | Regressed against main for nested paths | [P1-2](#p1-2) |
| P2-2 | Discovery no longer assumes net0 | Discovery and list still hard-code net0 | [P2-4](#p2-4) |
| P2-4 | Host key forgotten even for stopped VMs | The fallback address is never set in destroy | [P3-1](#p3-1) |
| P2-7 | VM_NAME defaults made consistent | Only destroy changed; deploy combines two defaults | [P1-7](#p1-7) |
| P3-10 | Destroy evicts the VM's cached facts | Deletes a filename ansible-core 2.21 does not use | [P3-1](#p3-1) |

## Method

Each reviewer was read-only on the repository and was not allowed to contact the Proxmox host, GitHub or any VM, read the vault or local inventory, or run Molecule or Docker. Reproductions ran in a scratch directory against copies of the repository's own fake Proxmox API, with throwaway playbooks, or against the installed community.proxmox and Claude Code sources.

| Reviewer | Scope |
|---|---|
| Provisioning | Idempotence, concurrency, the deploy.yml guard, discovery edge cases and community.proxmox module behaviour |
| Claude Code | The claude_code role, Remote Control, the systemd unit, `claude auth status`, TAGS |
| Security | no_log coverage, where secrets land, the tracked-file guard, CI permissions and pinning, committed addresses |
| Tests | Molecule scenarios, the fakes, unit tests, CI coverage, regressions against main |
| Conventions | Role interfaces, Ansible idioms, duplication, stale comments, lint skips, commit hygiene |
| Performance | API calls per run, serialisation, fact caching, repeated work |
| Docs | Every checkable claim in README, ARCHITECTURE, CLAUDE.md, CONTRIBUTING and SECURITY |

Confidence levels: **Reproduced** means a reviewer ran something that shows it; **Confirmed** means traced end to end in code, module source or official docs; the lead's own re-checks are noted alongside.

## Checked and found sound

- yamllint --strict, ansible-lint production (0 findings) and ruff are clean; 87 unit tests pass; all 8 playbooks pass --syntax-check.
- defaults/main.yml and meta/argument_specs.yml agree for all six roles, and every secret variable has no_log.
- The Proxmox token secret does not leak at -vvvv; the API key is never in the systemd unit, process environment or shell history; settings.json is 0600.
- Re-running provision on converged, tagged VMs reports changed=0; guest-agent waits run in parallel; the throttle applies to the clone only.
- The clone-collision assert matches proxmox_kvm's real behaviour (changed=False with the source VMID).
- deploy.yml's template guard skips every template task without connecting, and a failed listing stops the run before the template play.
- destroy.yml leaks no state between loop iterations, defaults the prompt to no, and refuses untagged VMs and templates.
- Tag matching is whole-tag, handles both separators and excludes -staging; VM_NAME parsing handles spaces and empty entries.
- guest_ipv4 skips loopback, docker0, link-local and IPv6; the filter unit tests kill 10 of 13 mutants, and the 3 survivors are harmless.
- --tags claude_code keeps discovery running, and role tags reach tasks inside include_tasks.
- The Remote Control CLI flags and `claude auth status` fields match the docs and binary; the four blocking variables are real; the trust-seeding key is correct; a logged-out VM gets its service stopped; the ExecStart symlink survives self-updates.
- The claude_code role is idempotent on re-run and adds only seconds per VM.
- CI uses contents: read, SHA-pinned actions, a digest-pinned gitleaks image, no pull_request_target and no untrusted interpolation; every scenario runs with idempotence.
- No real addresses, usernames or tokens are committed; every IP is a documentation range, loopback or a test decoy.
- No site.yml references remain; every README and ARCHITECTURE anchor resolves; the make list sample matches the playbook's output.
- The coverage removed from the proxmox_vm scenario's prepare.yml moved to tests/molecule/prepare_pve.yml rather than being lost.
