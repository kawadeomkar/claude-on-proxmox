# 11. Testing architecture

Three tiers. **No test ever talks to a real Proxmox, a real GitHub, a real Tailscale, or a real
hypervisor** - nor this machine's own `tailscale`, which the unit harness stands in for with one that
fails the test if it is called.

## 11.1 Unit — `just test unit`

`just test unit` runs it on pytest-xdist, a worker per CPU (`-n auto`; CI says 4, its vCPUs), with `--dist
loadgroup`. `tests/unit/conftest.py` marks every test of a module that has a module-scoped fixture of this
project's with that module's group, so the fixture - a whole playbook run - is made once, on one worker, and the
other modules go test by test to whichever worker is free. Whole modules, since a test can reach a fixture
through `request.getfixturevalue`, which no static look finds; and `tryfirst`, since xdist's own hook turns the
marks into groups and, run first, would see none and rebuild the fixtures test by test. Plain `pytest` runs one
test at a time, for one test or one file. Every child a test signals or types Ctrl-C at starts with SIGINT,
SIGQUIT and SIGHUP at their defaults (`harness._default_signals`), since a suite started with `&` or under
nohup has them ignored; and a pseudo-terminal's reader reads to its end after the run exits
(`harness._drain`), since on a loaded machine its last bytes arrive late, up to the test's deadline. Every fake
API a test starts binds a port the system picks and prints it (`harness._start`), so no port is probed, freed
and then taken by another worker's fake.

pytest over `tests/unit/`: 2064 tests. By file:

- **Filters** (`test_filters.py` 84, `test_vscode_filters.py` 41, `test_desktop_filter.py` 24,
  `test_text_filter.py` 32): the guest-agent readers, `guest_ipv4` among them refusing anything that
  does not parse as one IPv4 address, the managed SSH config's blocks, the VS Code settings reader, the
  sign-ins' `page_opener`, `plain_text`, and `banner`'s block.
- **`just status`** (`test_status.py` 50, `test_status_live.py` 10, `test_status_vms.py` 13,
  `test_status_work.py` 3): the report's filters on canned
  reads - every value one line of plain text over a corpus of hostile ones, the drift between a VM and
  `local.yml`, the twelve buckets of a day at Proxmox VE 9's resolution and 8's, the ranking and exit code,
  every line within 100 columns, of a report holding every state at once, whose every `just` command the
  justfile accepts (`test_commands.py`'s own check); `status.yml` against the fake Proxmox, on VE 9 and 8, with a refused
  token, an API that does not answer, a name that matches nothing, and settings that differ, with every
  request it made a GET; statically, every task it runs and every file and entry point it reaches a read
  or the report's own write. Its callback, driven in process at a stand-in terminal, and the real playbook
  at a pseudo-terminal of the test's own, read back through `tests/unit/vt.py`, a small terminal: a window
  the report fits, a short one, a narrow one, a resize each way, Ctrl-C, a dumb terminal, a pipe, and a
  warning between frames, the screen and its scrollback ending in exactly the finished report the log
  holds; the resize and Ctrl-C runs hold the VMs' stand-in `ssh` until the test has acted, since a Linux
  pty takes the rest of a run's output without blocking it, and the run could end before the frame it acts
  on is read. The Ctrl-C is sent only once each running VM waits in that `ssh`: a SIGINT that lands while
  Ansible forks a worker is lost (`CLAUDE.md`, things that have bitten this repo). Inside the VMs (`test_status_vms.py`): `vm_status_info` on fixture trees, in this process; status
  over stand-in VMs with discovery off, each running the three roles' `status` entry points with the gh and
  tailscale fakes, a stand-in `systemctl` and a fixture tree, every file of each VM unchanged after and the
  fakes' logs holding only reads; the host key, `status_ssh_args` held to `ansible.cfg`'s, the options as
  they reach a stand-in `ssh` and as `ssh -G` resolves them, and ssh's two host-key refusals each said for
  what it is. Every status run in a test gets that refusing stand-in `ssh` (`status_root`), so none reaches
  a VM or this machine's ssh. WORK (`test_status_work.py`): `git_checkouts_info`'s `work` over real throwaway
  repositories - a worktree under `.claude/worktrees/` and one outside the projects directory, a branch with
  no upstream, a detached HEAD with commits of its own, a stash, untracked files - every file of them, `.git`
  included, as it was after.
- **The stdout callback** (`test_attention_callback.py` 15): `callback_plugins/claude_on_proxmox.py` run
  for real - untagged output byte for byte the default's, no hook failing, the browser block, one
  waiting line for a tagged retry, NEXT STEPS from `set_stats` records, colour on the screen and never in
  the log - and statically, the six tasks that carry `attention_browser`, every prompt drawn by `banner` (seven, `just init`'s two among them),
  and every report followed by its record. Each role's own tests then assert what it records, in every
  state they run.
- **What every live output draws with** (`test_live_region.py` 10, `test_screen.py` 17, `test_vt.py` 4): the
  region against a stand-in `Display` - what is printed above it, a frame cut to the screen, a pause and a
  vars prompt each with the screen to themselves, the route, a screen too small, the echo off on a
  pseudo-terminal of the test's own - whose `display`, `prompt_until`, `do_var_prompt` and lock it holds to
  ansible-core's; the lines' cut, alignment, palette, bars and durations; and `vt.py`, the small terminal
  the live tests read back through, reading status's and deploy's streams the same cut anywhere. Every test
  that runs something at a terminal opens it through `harness._at_a_pseudo_terminal`.
- **`just deploy`'s live output** (`test_run_view.py` 80, `test_deploy_live.py` 13): the model, fed
  events with the time given - a running, a waiting, a failed, a dry and a fleet run's frame, a wait known
  by how long it runs, a step's time its own tasks', a running stage's ended steps whole, a report a later
  report on its matter made old left out of the summary, as under `just deploy`, no line wider than the
  terminal at any of ten widths, a frame folded to its height, the line per step, the summary - and the stage
  table against `deploy.yml`'s imports and `--only`'s playbooks. Then the callback for real: a stand-in
  deploy, its playbooks named as the real ones, at a pseudo-terminal of the test's own, read back through
  `vt.py` (which counts a frame's wrapped lines, the titles and the bells): the region drawn and
  nothing of it left, with Enter pressed mid-run, the plan and a long report whole, a fact a VM forged unused,
  the switch not inherited, the prompt whole and answered, the diffs of `--check`, 50 by 12, 200 by 60 and a
  resize, Ctrl-C; a failure in a rescue, a loop's failed item, a crashed module and a long output's end said;
  the log every line the stream logs, in order; a line per step through a pipe; `-v`, no variable and no log
  streamed; and no other tracked file naming the variable.
- **`github_repos`** (`test_github_repos.py` 32) and the **tracked-file guard** (`test_tracked_files.py`
  96), both ways.
- **Remote Control** (`test_remote_control_playbooks.py` 56): `claude_login.yml` and `remote_control.yml`
  against canned logins and stand-ins, and the harness they share (`tests/unit/harness.py`: an
  allowlisted environment, desktop stand-ins - and this machine's tailscale, gh and claude - that fail a run
  that reaches for them, no terminal).
- **The Claude sign-in** (`test_claude_login.py` 42): `browser_login.yml` against the stand-in `claude`,
  five runs at a pseudo-terminal of the test's own - the page before the prompt, a partly copied code,
  an expired attempt, Ctrl-C then A, and keys typed before the prompt is ready - with the code searched
  for in every run at `-vvv` with module arguments injected; and the browser coming back by itself, a
  stand-in `ssh` recording the forward and the test playing the browser at the stand-in's callback: no
  prompt, a forward refused, a page that is not Claude's, a run over SSH or no browser here falling back
  to the paste, and a browser that never comes back skipping the VM's later sign-ins.
- **The GitHub sign-in** (`test_github_auth.py` 119): the `github_projects` role's `auth.yml`,
  `clone.yml`, `device_flow.yml` and `github_login.yml` for real against the stand-in `gh` and the fake
  GitHub API on a loopback port, one host per sign-in state, twice over for idempotence, with `git
  credential fill` proving that git itself gets the token through the config written, deploy's GitHub
  step per VM - starting by itself, and asking first with `github_login_auto_start: false` - every task
  that renders a token held to `no_log`, and the whole output searched for every canned token.
- **The sign-in playbooks** (`test_sign_in_plays.py` 33): `claude_login.yml`, `github_login.yml` and
  `deploy.yml`'s own sign-in steps end to end - a VM whose ssh refuses the connection among VMs that
  sign in, a failed sign-in said in its turn, a failure after a sign-in (Remote Control's start, a
  clone) told apart from a failed one and raised at the end of `just deploy`, deploy with no terminal,
  a refused code and `deploy_sign_in: false`, and one VM per batch only where a sign-in prompts; and
  `login.yml` - `just login` - running the three in deploy's order and by its rules, held to deploy.yml's
  own decisions: no device flow for a VM whose Claude sign-in nobody answered, a VM whose code was refused
  or whose Remote Control failed to start still given its GitHub and Tailscale turns and failed at the
  end, and a report without names.
- **Tailscale** (`test_tailscale.py` 67): the role's files from `ownership.yml` on, and
  `tailscale_login.yml` whole, against the stand-in `tailscale`, with stand-ins for `dpkg-query` and
  `getent`: a first join once, with the key on stdin and in no report, argument or file; no key for a node
  whose key expired, and a node logged out by hand joined again; a refused key and an OAuth secret without
  tags as reports; one `set` for drift; a login or a Tailscale made by hand only read; tags compared only
  when configured; `NoState` reads, `Starting` and a silent daemon; check mode; the web login's one `up`,
  its page said before the wait and opened only when it is Tailscale's, an approval after `up` exited, a
  wait that runs out; a page approved after the run, recorded as the role's login by the next configure,
  and a join still under way marked pending - an earlier key's refusal still in Health included - a refused
  key or refused tags leaving nothing; a control server's page trusted however its address ends, and never
  one on another host; just deploy's sign-in step saying nothing its configure play did again; an expired
  login made by hand left to whoever made it, never signed in again by the web; a page still waiting reported
  before an expired or refused key, just deploy's two plays included; a Tailscale dpkg does not know only
  read; `tailscale_auth_method` `key` and `web`; deploy's step VM by VM, starting by itself and asking
  first with `tailscale_login_auto_start: false`; `configure.yml`'s rescue and last play; and the guards
  that keep every scenario, the Vagrantfile and this harness off Tailscale, and the role in the justfile's `scenario_roles` and CI.
- **Destroy's release** (`test_release.py` 44): `destroy.yml`'s release play and report against the
  stand-in `claude`, `gh` and `tailscale`, the fake GitHub API's credential revocation and the fake
  Tailscale API: a VM released from everything, with no token revoked at GitHub; the vault token and one
  pasted by hand signed out and told apart, the vault token even under a marker claiming it, and a logout
  gh refuses said as such; a
  device deleted by an API token and by an OAuth client, refused, already gone, and never when the ID the
  VM reports names another device; a VM that could not be reached, with what is left of it named; the
  Claude Code sessions a VM alone holds saved first, byte for byte and 0700/0600 with their `sessions.json`,
  never a copy just code made still as written nor a transcript without a message, never a link the VM
  holds nor a folder not named as Claude Code names one - the file a link points at here left as it was -
  just code's manifest read where `vscode_workspaces_dir` puts it, the save turned off, the save without
  the release under `destroy_release: false`, a VM whose sudo refuses saved and then deleted unreleased,
  said so, and one that fails keeping the VM neither released nor deleted, with no transcript in the
  output, the save's tasks held to an allowlist that keeps them off sudo and unreachable-safe; every
  canned credential searched for in the output; and, read from `destroy.yml`, a name two VMs share
  refused before anything starts, the wait for a started VM's address on its own NIC, and a VM it started
  stopped again however the run then fails - before it deletes anything, or at every deletion; and, run
  against the fake Proxmox API with its listing lagging a start, a VM destroy started given its address,
  and a relative `destroy_sessions_dir` refused before any VM is started.
- **`just code`** (`test_code_session.py` 192): the Claude terminal's script against stand-ins, once at a
  pseudo-terminal for Ctrl-C, the window step's own tasks in `just deploy` and out of it with stand-ins
  for `which` and `code`, and the allowlist that holds `code.yml` read-only on this machine, followed
  through every import its task files make. The
  session notes' helper with nothing but stand-ins on PATH - the live section rewritten and nothing else,
  exit 0 and the notes opened when its Python raises, hangs or is missing, a read that hangs costing
  2 s, a symlink neither followed nor opened, the time in the zone it is given or in UTC for one it
  cannot find - the refresh after a sign-in in the terminal, the zone `code.yml` finds (`TZ`, the
  `/etc/localtime` link on Linux and macOS, `/etc/timezone`, else UTC, shell syntax and paths refused),
  and the workspace each window gets: the notes' folder only where its own folder holds them.
- **The session handoff** (`test_session_handoff.py` 151): the plan - by origin in every URL form here,
  a GitHub one only on the VM, a renamed clone, a gone worktree's checkout, `githubRepoPaths`, another
  owner's repository of the same name refused, `--session`'s reasons - the transcripts read without their
  content, an open session only at a pid that is still a claude's, the registry whatever the
  transcripts, a manifest of another format read as none, a transcript gone as it is listed, the git
  reads against real checkouts (the origin as configured and without its
  credential, a detached `HEAD` no ref holds, the ignored files a move would replace, untracked files a
  config hides, a path or branch holding `$` or `~`) and a branch name or commit git would take as an
  option refused, an ID in two folders here or open on the VM left out,
  the four edits byte for byte with the records of requests sent, an assistant's signed thinking and
  another project's memory folder left as they are, the manifest's rule and its write after each copy, a copy moved to another folder on the VM
  never written again and one moved here following, the time a copy is given against the 30-day sweep,
  check mode, and what of a VM's sessions destroy saves - every one with `all`, just code's copies told
  apart by the manifest, the memory folders, names the VM made up left out, the command that continues
  each, its path quoted - and how: archives with absolute paths, `..`, links, hard links, devices and
  names not asked for unpacked into nothing outside the folder, an archive cut short, GNU tar's exit 2
  for a name it cannot find said as that name, a failing or a stand-in `ssh` given the port, key and
  arguments. In `test_code_session.py`, the window step's session tasks between two real checkouts,
  the VM's with a GitHub origin: the copies and their edits, a second run changing nothing, the sync
  off, a sync whose rsync fails opening the window all the same, syncs
  that leave their one session out - open on the VM, in two folders here, changed on both - saying so
  and never that none is of the VM's repositories,
  `--session`'s fast-forward from a VM branch behind it, on it or not, and each refusal - open on either
  machine, changed on both, unpushed, tracking another remote, gone, a fetch that fails, a detached `HEAD`,
  an ignored file in the way, an rsync that fails - leaving the VM's checkout where it was, and no run
  printing the marker the transcripts carry.
- **The session notes** (`test_session_notes.py` 14): `vm_session.md.j2` rendered as `code.yml`
  renders it - the placeholder, a live section carried over, the parts switched off, the same file
  from every window, and no time, template marker or token shape in it.
- **`just init`'s questions** (`test_init_settings.py` 77, `test_settings_filter.py` 73):
  `init_settings.yml` against copies of the example files in the test's directory - never the checkout's,
  which a static test holds - with no terminal (nothing asked; nothing written but the values given) and at
  a pseudo-terminal of the test's own: each answer typed, Enter at every question, an answer that breaks
  its rule asked for once more and then left, a set value never asked, an encrypted vault opened with
  `.vault_pass` and encrypted again, one with no password left alone; the values given on the command line,
  written, the same command again changing nothing (in another case too), one that differs from a set value
  failing, one that breaks its rule failing before any file is written, a scripted run still missing a value
  failing and a run at a terminal not; `ANSIBLE_VAULT_PASSWORD_FILE` alone and beside `.vault_pass`, an
  encryption that fails said, a host named without an `ansible_host` taken as set, a vault line over several
  lines left alone, a vault left plaintext said, and a run that keeps the modules' files refused; the
  Tailscale notice, naming the auth key or the sign-in page, gone once `tailscale_enabled` is set, and
  `--tailscale` writing it; the vault decrypted to the example with the answered lines replaced,
  `0600`, and the secret in neither the output nor the log, typed or given, with module arguments injected
  and the log at `-vvv`; a vault read and written as it is with `ANSIBLE_VERBOSITY` or `ANSIBLE_DEBUG` in the
  environment, every `ansible-vault` run quiet; statically, every write under `init_ask` and every task that renders the secret
  or the vault's text `no_log`. The offer to make the token, against a stand-in Proxmox host (the local
  connection, `fake_pve_cli.py` first on its `PATH`): `y` running `tools/proxmox-access.sh` and the vault
  taking the secret it made, never asked for, said or logged at `-vvv`; `n` changing nothing and saying how
  to run it by hand; root SSH that fails said, with `ssh-copy-id`; a storage the host has asked for by
  number and written, one left undecided, and a host set up already asking only for the secret; the
  `proxmox` group's connection settings used; a vault that cannot be written deleting the token just made,
  and one written but not read back keeping it; and no terminal reaching no host. Statically, nothing
  between the offer and the vault's block. Every run has a refusing
  `ssh` first on `PATH`; statically, no run without it, the offer only under `init_ask`, the script the only
  thing run on the host - `--list` and `--dry-run` changing nothing, the run itself on a `y` and `no_log` -
  and the token's delete the one other command there. The filter: each kind's accepted answers in their
  normal form, and what each refuses.
- **The Proxmox access script** (`test_proxmox_access.py` 34): `tools/proxmox-access.sh` under `/bin/sh` - dash
  on a Linux runner, as on the host - against `fake_pve_cli.py`: a fresh host given the role, the user, its
  three grants and the token, made last, with the secret alone on stdout; the dry run printing the commands
  and changing nothing; a second run changing nothing and naming the token; the guest agent's privilege by
  version; `--list`; a storage or bridge the node lacks refused, naming its own, then given; a role without a
  privilege named and left, and never said to give it; a user that holds the privileges another way given no
  grant, one whose grant from above does not propagate given one, and one whose grant on `/vms` does not
  propagate named and left; a user that holds privileges on `/` warned of before its token; a token with privilege separation named; another user and token; root and
  `pveum` required; a value that is not one plain word refused before anything runs; a refused write stopping
  it before the token. Statically: its privileges the ones `init_check.yml` checks, the commands
  `docs/getting-started/proxmox-access.md` shows the ones its dry run prints, and stdout carrying data alone.
- **`just init`'s checks** (`test_init_check.py` 50): `init_check.yml` against the fake Proxmox on a
  loopback port, in an inventory of the test's own with the project's `defaults.yml`: nothing asked of
  Proxmox while a value is the example's; every check `ok` on a host with its template, on 8.x and on 9.x,
  and no SSH then; a wrong secret, a closed port (an IPv6 address said bracketed), a missing privilege on
  8.x and on 9.x, one without Propagate, a token with none at all - each named with its fix and failing the
  run; the template found by its name as deploy finds it, on another node than `proxmox_node`, and its VMID
  taken by a VM, a template of another name, a container or a guest on a node that is down; without the
  template, root SSH to a stand-in host that reads its node name, one that is another node, and one whose
  `ssh` is refused; no key, and a password alone; a plaintext vault; and the secret nowhere in the output or
  the `-vvv` log. The node, storage and bridge: every default fitting and nothing written; the template's
  node written in place of the default at a terminal and reported without one; two nodes taking the one SSH
  reaches, or none without SSH; a storage chosen by number among those the token can use, an Audit-only one
  shown and not offered, one offered y or n, Enter leaving it, none written without a terminal, one set
  that is not listed; the template's bridge without `SDN.Use`; a bridge offered before the template; and
  `--storage` and `--bridge` given - written even where the default fits, refused when the token cannot use
  them or they differ from what is set, or while the node is unknown, `--bridge` refused beside a template
  unless it is the template's own, before anything is written. Also: nothing asked or written while the node
  is unknown, a token that sees no storage told what lists one, an inactive storage, a template without a
  bridge, no host in the `proxmox` group, and the playbook loading with duplicate keys made fatal. Statically: every task a read (a `uri` a GET that saves nothing), every `uri` with the CA
  path and `no_log`, the settings written only through `tasks/init_local_setting.yml` at a terminal or as
  given, and no run reaching a real `ssh`. Running
  a fake on a loopback port, the fake Proxmox's certificate and `_pve` live in `tests/unit/harness.py`, shared
  with `test_release.py`; each file keeps its own `fake_pve` fixture.
- **The managed SSH config** (`test_ssh_config.py` 29): `tasks/ssh_config.yml` for real against a
  temporary home, resolved with `ssh -G` - the refusal of a name or address that is not one plain word,
  the guard that every scenario and the Vagrantfile keep the managed file out of `~/.ssh`, that no
  playbook sets the task file's inputs as facts, and that the user's own Include and file mode are left
  alone, a group the user is not in included (macOS).
- **Smaller ones**: `test_configure_tags.py` 2 (`--tags claude_code` still runs discovery and the alias
  play), `test_deploy_report.py` 18 (deploy's opening message, rendered by its own tasks, naming only the
  sign-ins that open a page and the VMs as a list), `test_proxmox_vm_absent.py` 5 (deleting a VM
  forgets its host key under its address and `<name>.<domain>`), `test_var_names.py` 24 (no task var
  named like a register or fact, in any playbook or role), `test_cloud_init_wait.py` 3 (a first boot
  that failed is said with what failed and how to build the VM again), `test_gathered_facts.py` 5 (no
  tracked file reads a fact by its top-level name, and every `ansible.cfg` a run reads, the tests' own
  through `harness.ansible_config`, leaves facts out of the top level), `test_remote_tmp.py` 4 (with
  pipelining, `tasks/remote_tmp.yml` makes the `remote_tmp` the run is configured with before a module
  would make it and warn, and both plays that run modules as root import it before their roles).
- **The justfile** (`test_justfile.py` 456): the real `just`, at least the pinned version, run against
  stand-ins - on macOS under `/bin/bash`, 3.2, so the scripts keep to what it has. Statically, over `just
  --json`: no recipe has a dependency (which `JUST_NO_DEPS` would skip), no assignment starts a program,
  nothing is unstable and no `.env` is read, every public recipe has a `[doc]` and every parameter its
  help and a literal pattern, the VM recipes share one name pattern, no parameter is named like an
  assignment, `self` passes on every setting, the justfile is as `just --fmt` leaves it, and its
  `minimum-version`, `just_pin` and CI's `JUST_VERSION` are one version. Run: the vault and collections
  checks, each before any playbook, and the rules with `JUST_NO_DEPS` set; a run from another checkout
  refused, and run from a subdirectory, from the checkout spelt in another case and from a copy inside
  another work tree; every VM recipe passing its names as one JSON extra-var and none
  without, refusing a name outside its characters (quotes, `$(...)`, `{{ }}`, a newline, non-ASCII, a
  leading `-` or `.`) before anything runs, and `deploy`'s question for a stage where the names go,
  each answer run as if typed and none taken without a terminal, but none for a recipe's name (`test`); `code` one VM, its
  `--project` after `tools/check project` and its `--session` after `tools/check session`, each as its own
  JSON extra-var, refused outside its characters and never run as code, and taken by `code` alone; each
  stage, `--check`, the sign-ins and `--start` running their
  playbooks, `deploy` alone exporting the live output's title, an option before or after the names, and anything a recipe does not take - an unknown option,
  a stage, a setting, make's `TAGS=` - refused; `--tags` beside names and refused without them, with the
  stages that have none, and outside the six roles; `just` and `just help` listing every recipe and
  option, every word a `*` parameter (never `NAME=''` in the listing) and a second word after a recipe
  that takes one refused, never run as a recipe; each run logged to its own file, never named after a VM, `LOG_DIR=` off, an `ANSIBLE_LOG_PATH`
  of your own kept, old logs pruned and nothing else; a run with no terminal turning the desktop steps
  off, and Ctrl-C that the playbook survives ending the run well, `init`'s first playbook included, on
  the five recipes whose playbooks prompt, which carry `[continue]` (and the control: without it, just exits
  130); `vault`, `install` (the lock into
  `PYTHON`'s environment, `--lock` alone, the stale-script repair, and a Python that is no virtualenv
  refused by every recipe), and `init` from the questions to the checks - the `.vault_pass` its questions
  just made found, the values given as one JSON extra-var, a relative secret file where `just` was
  started, and the token secret refused in every spelling without being said; an exported name changing
  nothing; `tools/env-run`, which every local hook runs through, and its `--bin`; `vagrant`, `lint` (its
  parts, each linter's failure failing it, and the hooks skipping what lint runs itself), `test` (lint
  through `self` only without parts, each part, every role scenario, `--role`, and a failing part ending
  the run) and `docs`.
- **The commands named** (`test_commands.py` 6): every `just` command the playbooks, roles, wiki,
  README, CLAUDE.md, CI and the justfile's own messages tell a person to type, read with a placeholder
  where Jinja, an f-string or a shell variable splices a value in, is one the justfile takes, over `just
  --json` - the settings before it the justfile's, its options the recipe's own and their values ones they
  take, no `NAME=value` after the recipe, no misspelt part, no second recipe chained to it - and no `make`
  command, nor a name after one of its commands, no target only the Makefile had and no misspelt recipe
  is left outside the pages that tell the history, nor make's spelling of an option (`TAGS=`) on a line
  that does not say whose it is.
- **The wiki** (`test_docs.py` 20): the generated reference sections are up to date, and every setting
  and role default is documented; the site builds strictly from the pages git knows, every heading
  reaches its built page, and every page renders the same on GitHub as on the site, its diagrams
  included; no page repeats a heading, holds a closing tag in a code span or shows a character reference
  such as `&emsp;` as text outside code; the navigation lists every page and only pages; no relative link
  leaves `docs/`; the root files' links, the pages the repository names and the architecture sections it
  cites all exist, a section cited from another page is a link, and the README's quick start matches the
  wiki.

## 11.2 Role scenarios — `just test role`

One Molecule scenario per role at `roles/<name>/molecule/default/`, inheriting driver and platform
from `.config/molecule/config.yml`. Each runs create → prepare → converge → **idempotence** →
side_effect → verify → destroy.

`vscode_server`'s side effect takes the forwarding away three ways, each in a drop-in of its own,
runs the role inside a `block`/`rescue`, and asserts the failure names the setting and the file:
`AllowTcpForwarding no` sorted before the role's drop-in, `DisableForwarding yes`, and a
`Match User dev` block sorted last that limits forwarding to the remote direction - the case a
global `sshd -T` cannot see. Then it runs the role switched off and asserts both files are gone, and
switched on again for verify, which reads the drop-in, the sysctl file and `sshd -T` (forwarding on,
converge's keepalive values in effect).

`tailscale`'s scenario runs two VMs - one that never had Tailscale and one where it was installed and
signed in by hand - with the real package and daemon, log upload off, and the stand-in `tailscale` in front
of it from `prepare` on. Molecule's `check` stage runs converge in check mode before converge, as every
VM meets the first `just deploy --check` after the upgrade. The side effect sets back a hostname changed behind the
role's back with one `set`, turns Tailscale off twice (removed once, then nothing) and on again, and makes
`pkgs.tailscale.com` unreachable: the install fails at the package, the repository comes back out, `apt`
still works, and turning Tailscale off removes what the failed run left. Verify reads the conffile -
`PORT` kept beside the role's `FLAGS` - the daemon's flags and the drop-in in effect, finds the key in no
file (searched for with the needle on stdin, since sudo logs a command's environment), no connection of
`tailscaled`'s, and the hand-installed VM as it was.

`common`'s prepare installs stand-ins for four of the units `common_masked_units` names - a running
service and a waiting timer, a listening socket and its service, and snapd with no snap installed - takes
away the image's own apt cleanup (`docker-clean`), and leaves a package in apt's download cache. Verify
finds the stand-ins masked and stopped, none of them failed, no mask made for the two units the container
never had, and no package left in the cache, `sl`, which converge installs, included. The side effect
finds nothing in the timer's journal about a failure (converge stopped it before it masked it), masks the
timer while it waits, which fails it, gives snapd a snap and runs the masking again: the timer is cleared
and snapd is left running.

The same scenario installs a stand-in for a service a user adds to `common_enabled_services`, and one
for `qemu-guest-agent` in `/etc/systemd/system`, not bound to the guest-agent port as the package's unit
is. Converge starts the first and, with no port in the container, leaves the agent stopped; the side
effect finds it stopped, gives the role a stand-in for the port and runs its services again, and verify
finds both running.

`proxmox_vm`'s scenario is the interesting one: it runs against the fake PVE API and asserts the VMID
came from Proxmox (9001, the next free one) and that the address came from the VM's own NIC —
`192.0.2.51`, not `172.17.0.1`, which would mean the docker bridge was picked up. It asserts the
exact clone, config, resize, start, shutdown and delete requests, the deletes in a destroy pass of
its side effect. That side effect also proves the ownership guards: an untagged VM and the template are refused by both provisioning and
deletion without a single config write reaching either, and a VM left tagged but unconfigured — what
an interrupted run leaves — is repaired; and a minimum memory of 0, or one above the memory, is refused
before any request is made, while converge's 2048 reaches the settings call as `balloon`.
`proxmox_template`'s side effect runs the role against a
VMID the fake `/etc/pve/.vmlist` places on a second node and asserts the refusal names that node and
that no build started.

## 11.3 Playbook scenarios — `just test integration` and `provision`

| Scenario | What it proves |
|---|---|
| `provision` | `provision.yml`, `discover.yml` and `status.yml` end to end against the fake API, for a two-VM fleet, plus `destroy.yml` and `deploy.yml`'s template check as child runs; discovery runs in a stage of its own, finding the VMs by their tag, so a later `just deploy --only configure` needs nothing stored locally |
| `configure` | `configure.yml` end to end against a container standing in for a provisioned VM; then the sign-ins, `just code`'s session handoff with the real `claude` against a stand-in Messages API - copies resumed as the VM's own, `--continue` never picking one up, the records of requests sent left unsent, a saved copy resumed by its path - and `just destroy`'s pause of Remote Control around its save, with the real systemd |

Both symlink the **real** `inventory/group_vars/all/defaults.yml` into the scenario inventory, so the
actual variable wiring is exercised rather than a copy. Test overrides live in `host_vars/`. The
symlink must survive checkout — clone the repo, do not download a zip.

The `provision` scenario is deliberately adversarial. Before discovery runs, `verify.yml` seeds VMs
that must not be claimed, or that are this project's but once broke a fleet-wide command:

| Fixture | Why |
|---|---|
| `stranger`, tagged `claude-on-proxmox-staging` | a substring match would configure someone else's staging box |
| `bystander`, tagged `not-claude-on-proxmox` | the same trap from the other side |
| `nonic`, correctly tagged but with its `net0` removed (`delete=net0`) | `net_mac` used to raise and take discovery down for the fleet |
| `commas`, tagged `ansible,claude-on-proxmox` and kept comma-joined | exercises the `,` separator of `claude_vm_tag_pattern`; every other VM has the `;` form |
| `netone`, tagged, LAN NIC on `net1` and no `net0` | skipped with `proxmox_nic: net0`, found at `192.0.2.59` with `net1` — discovery and the listing used to hard-code `net0` |
| `parked`, tagged, **stopped**, VMID **4013** | its agent answers `VM 4013 is not running`, which the unanchored `40[13]` took for a 401 and failed discovery and the fleet listing |
| `legacy`, tagged `claude;ansible` as `main` tagged VMs, stopped | `destroy.yml` must refuse it, and delete it with `proxmox_vm_allow_untagged_delete` |

Every running fixture's agent is waited on, and a check before discovery confirms the fake really
serves what each fixture depends on — `nonic` has no `net0`, `commas`' tags come back comma-joined,
the untagged template has no `tags` key, `parked`'s agent error carries Proxmox's reason text, and an
unrelated VM sits on a node that answers every request with 595 — so the only thing a fixture tests
is the code under test.

That unrelated VM on a node that is down is always there (§11.4), so every fleet-wide command in both
Proxmox scenarios runs against a cluster with one unreachable node.

`vm_name` is passed only to converge's import of `provision.yml`, not set in `group_vars`, so
discovery and `status.yml` each run three times: once without it, as a bare `just deploy --only configure` or
`just status` does, where the ownership filter is all that stands between them and every running VM;
once with it, to prove the narrowing; and once for `netone` with `proxmox_nic: net1`. The fleet-wide
passes assert that discovery considered exactly `alpha`, `beta`, `commas`, `netone`, `nonic` and
`parked`, published only `alpha`, `beta` and `commas`, and that the report shows all six, with a
blank address for `netone`, `nonic` and `parked`, and says that `netone` and `nonic`, running with no
address on `net0`, and the VM on the node that is down were not read. The request log is checked for one clone per VM,
by name, with VMIDs from `/cluster/nextid`.

Several outcomes are failures or partial results, which an imported playbook cannot express, so the
scenario's last play runs `ansible-playbook` as a child process against its own inventory, searching
stdout and stderr for a failure message (Molecule prints task failures to stderr):

- fleet-wide discovery with two tagged VMs named `twin` must exit non-zero with the duplicate-name
  refusal;
- re-running `provision.yml` after shutting `beta` down must exit 0, publish `alpha`, report `beta` as
  stopped and leave it stopped;
- `discover.yml --check`, as `just deploy --check` runs it, must still find `alpha`;
- `deploy.yml` for the stopped `beta` must report the template as built, and with
  `proxmox_template_name` pointing at nothing, as about to be built;
- `destroy.yml` must refuse without confirmation (the prompt's non-interactive default), refuse
  `legacy` with the hint to tag it, and refuse the template even with
  `proxmox_vm_allow_untagged_delete` — deleting nothing — then delete `commas` and `parked` in one
  run and `legacy` with the flag, leaving every other VM in place. Aliases seeded for all of them
  beforehand survive every refusal, and each deletion takes exactly its own. `guarded`, tagged and
  with protection on, joins the `commas,parked` run: Proxmox refuses to delete it (the fake does as
  Proxmox does), the run exits non-zero, the other two are still deleted, and `guarded` keeps both
  its VM and its alias.
- `ssh_config.yml` fleet-wide, with aliases seeded for a VM that exists nowhere and for the untagged
  `stranger`: with the node that is down still down it prunes nothing and says which VMID holds it;
  with that node switched back on (§11.4) it prunes the vanished one, leaves `stranger` alone and
  reports it, and refreshes `alpha` with the workspace the destroy fixtures recorded; with names given
  it prunes nothing.
- `code.yml` against a stand-in `code` on `PATH` that records its arguments and lists what a file
  says. The VM side is read with Ansible's local connection, against a projects directory made in
  the ephemeral directory and given as `vm_projects_dir`, because the fake fleet's addresses
  answer nothing: the first run installs the Remote - SSH extension and opens the workspace, the
  second only opens it, the third opens `discord-music-bot` in it. Refused, each with its own
  message and nothing opened: `stranger`, `nonic` (no address), a missing name, two names, the
  stopped `beta` with `claude_ssh_host_domain` set (its name would still resolve), a
  mistyped project (the projects are listed, the file among them is not), `parkbnb` for `ParkBnb`
  (suggested; only where the filesystem is case-sensitive, as on CI, since macOS's is not), a file,
  `../etc` and `..` (the playbook's own check), a VM with no projects directory,
  an empty one, `.claude-on-proxmox` (the workspace files' own directory), a relative
  `vscode_workspaces_dir`, and one case over real SSH to an address nothing answers on. The user ssh
  config in the ephemeral directory never exists. The workspace files are read back and parsed: one
  folder, the project's path; one task, `folderOpen`, `bash -lc` in that folder; the whole
  directory's hides `.claude-on-proxmox`. With VS Code settings in the ephemeral directory (never the
  developer's) saying nothing, `"on"` behind a commented-out `"off"`, and `"off"`, the message asks
  for trust and *Allow*, trust only, and says the terminal will not start; reopening leaves the file's
  mtime and checksum alone; `vscode_claude_terminal: false` opens the bare folder and writes no file,
  and no refusal writes one either.

## 11.4 The fakes — `tests/molecule/`

| Fake | Stands in for | Notes |
|---|---|---|
| `fake_pve_api.py` | the Proxmox REST API | TLS, token auth, stateful VMs, a guest agent, a request log; on request, a cluster listing that lags a start or stop, as a real one does; for `just status`, each VM's live figures, a day of `rrddata`, `pending`, the agent's `get-osinfo` and `get-fsinfo`, and the storages' fill and disks |
| `fake_qm.sh` | `qm` on the PVE node | stateful, one config file per VMID, logs every invocation |
| `fake_pve_cli.py` | `pveum`, `pvesh`, `pveversion`, `hostname` and `id` on the PVE node | for `tools/proxmox-access.sh`: the roles, users, ACLs and tokens in one file, answering the calls the script makes as a Proxmox VE 9.2 host was seen to - compact JSON, a refusal for what exists, the version's own guest-agent privilege - with every call logged and a write refused on cue |
| `fake_virt_customize.sh` | `virt-customize` | so `proxmox_template` runs in a container |
| `fake_github_api.py` | the GitHub REST API | serves local bare repositories, in paginated responses; `/user` and `/user/repos` answer a token, private repositories included; `POST /credentials/revoke` takes credentials unauthenticated and refuses an authenticated call, as GitHub does, and `/fake/revoked` lists what it took, so a test can hold that nothing reaches it |
| `fake_tailscale_api.py` | Tailscale's API | the OAuth client exchange, and a device looked up and deleted by node ID, each device with the name it was started with; 403 and 404 on cue, and `/fake/deleted` lists what went |
| `fake_gh.sh` | `gh` | the auth commands only, installed ahead of the real one on PATH; one login per user, in a file under HOME, with every call logged; a login whose check never reached GitHub, and one GitHub refused, as gh 2.103.0 reports them |
| `fake_tailscale.sh` | `tailscale` | output that is Tailscale's own, as the research and a Headscale run recorded it, for the commands the role runs, against a node whose state is files under HOME: `status --json` and `debug prefs` shaped as a Running and a signed-out node really print, a join with a canned key on stdin, a refused or slow key, the web login's page and an approval that lands after `up` has exited (by the clock, `approve-after`, or at the Nth status read after the page, `approve-at-read`, which no loaded machine can outrun; `up-waits` shortens `up`'s own wait), `NeedsMachineAuth`, a taken name, `NoState` reads after a start, logout resetting the prefs, a node ID (`node-id`), and with `real-daemon` an answer only while the real daemon's socket exists; a key is never written, and `calls.log` shows `KEY` in its place |
| `fake_anthropic_api.py` | the Anthropic Messages API | for the real `claude` in the `configure` scenario: one short answer to every request, streamed or not, on 127.0.0.1 only, with each request's body logged, so a test reads the system prompt and the history a resume sent |
| `fake_claude.sh` | `claude` | `auth status` and `auth login` as probed against Claude Code: the page, the `Paste code here if prompted >` prompt, then `Login successful.`, `Login failed: ...` or `Invalid code` for the line read from stdin, writing `~/.claude/.credentials.json` on success; given `BROWSER`, it hands it the page that redirects to its own callback on `127.0.0.1` and takes a code from there as from stdin; `auth logout`; `fake-refusal`, `fake-no-page`, `fake-gone` and `fake-foreign-page` in its state directory make it refuse in other words, print an error in place of the page, exit before reading a code, or hand its browser a page that is not Claude's |

`fake_pve_api.py` is deliberately faithful where fidelity has caught bugs:

- `normalise_tags()` stores tags the way PVE does — `;`-joined, lowercased, deduplicated, sorted.
  Echoing the request back verbatim hid the `,` vs `;` difference from every test.
- The agent refuses `AGENT_READY_AFTER = 2` polls **per VM**. With a single global counter the first
  VM absorbed every refusal and the second's first poll succeeded, so a fleet never exercised the
  wait loop more than once.
- `agent_interfaces()` always reports `lo`, a `docker0` bridge the role must ignore, and the VM's own
  `eth0` — which is what makes the "address came from the right NIC" assertion meaningful.
- 401 replies have an empty body, as the real API does.
- Failures carry Proxmox's reason phrase — `VM 4013 is not running` for a stopped VM's agent,
  `QEMU guest agent is not running` while it boots — because proxmoxer copies it into the error
  message that callers pattern-match.
- `/cluster/resources` leaves `tags` out for an untagged guest, as PVE does. Sending `tags: ""`
  hid that `claude_vm_tagged` needs `selectattr('tags', 'defined')`.
- A second node, `pve2`, is listed as offline and holds an unrelated VM (8000, below the template so
  `/cluster/nextid` is unaffected); every request to it gets a 595, as pveproxy returns for a node it
  cannot reach. `PUT /api2/json/fake/nodes/pve2?status=online` - not a PVE endpoint - brings it back:
  its guests are then listed with their names and as stopped, the way Proxmox lists guests on a node
  that has just come back, which is the only way the "nothing is pruned while a node is down" path
  can be shown to recover.
- A config write carrying `fake-verbatim-tags` keeps its tags as sent, for the comma-joined fixture.
  It is not a PVE parameter.
- Each node lists its storages for VM disks and its bridges, each only where the token may see it -
  `Datastore.Audit` or `AllocateSpace`, `SDN.Audit` or `Use` - as Proxmox filters them: `local-lvm` and
  `vmbr0` on `pve` until `PUT /api2/json/fake/storages?node=&storages=<name>:<type>,...` or
  `/fake/bridges?node=&bridges=` replaces them, and `PUT /api2/json/fake/vm-node?vmid=&node=` moves a VM, the
  template included, to `pve2` once it is up; per-VM requests are served on the node the VM is on.
- `GET /access/permissions` answers each token's privileges by path, the privilege's name as the key and
  its propagate flag as the value, as Proxmox does: what `PUT /api2/json/fake/privileges?token=&path=&privs=`
  set (`VM.Clone:0` granted without Propagate), else the whole role `tools/proxmox-access.sh`
  makes, with the guest-agent privilege of the version `/version` reports - 8.2.4 until
  `PUT /api2/json/fake/version?version=` changes it - and for the `noagent` token, without it.
- What `just status` reads is shaped by that version, as Proxmox VE's own: from 9 the listing carries
  `memhost` and `rrddata` the `pressure*` fields, and a day is 1440 points; on 8 it is 70. A clone's config
  carries the template's meta `ctime`, as a real one does; its disk's `ctime` in the storage's content is when it
  was made, which `PUT /api2/json/fake/disk-ctime?vmid=&ctime=` backdates. `PUT /api2/json/fake/pending?vmid=&key=&value=`
  makes a change wait for a restart, and `PUT /api2/json/fake/root?vmid=&used=` sets how full a VM's root
  disk is.

Beside these, the `proxmox_template` scenario serves a "cloud image" locally, with a real `SHA256SUMS`
file, and a fake `/etc/pve/.vmlist` that places a VMID on a second node. The `tailscale` role scenario
and the `configure` scenario install the real Tailscale package and daemon - log upload off from its
first start - with the stand-in `tailscale` in front of it, so no test reaches a Tailscale service.
Every scenario and both unit harnesses turn `login_desktop` off, so no test opens this machine's
browser or touches its clipboard.

## 11.5 Vagrant — `just vagrant`

`configure.yml` against a real VirtualBox VM, with `claude_vm_discovery: false`. Not part of CI.

## 11.6 Which scenarios need the network

`common`, `vscode_server`, `dev_tools`, `claude_code`, `github_projects`, `tailscale` and the `configure` scenario
install packages inside the container and need working Docker DNS. `proxmox_vm`, `proxmox_template` and the
`provision` scenario talk only to local fakes and still run offline.

The packages are real downloads, so those scenarios need internet access. The `configure` scenario
leaves the Docker Engine install out, since a daemon cannot run in the test container; the `dev_tools`
scenario installs it but does not start it.
