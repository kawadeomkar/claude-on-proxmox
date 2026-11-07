# 11. Testing architecture

Three tiers. **No test ever talks to a real Proxmox, a real GitHub, a real Tailscale, or a real
hypervisor** - nor this machine's own `tailscale`, `gh` or `claude`, which the unit harness stands in for
with ones that fail the test if they are called.

## 11.1 Unit — `just test unit`

`just test unit` runs it on pytest-xdist, a worker per CPU (`-n auto`; CI says 4, its vCPUs), with `--dist
loadgroup`. `tests/unit/conftest.py` marks the tests that reach a module-scoped fixture of this project's - a
whole playbook run - directly or through the fixtures they ask for, with that fixture's group, so it is made once,
on one worker; two such fixtures one test reaches are one group, and every other test goes to whichever worker is
free. A test that asks for a fixture by name does so through a `fixture` parameter naming it, which the hook reads;
a module with any other `request.getfixturevalue` stays one group, since no static look finds what it asks for. A
module's `EACH_WORKER` names the fixtures cheap enough to make again on each worker that needs one - a fake server
no test reads back from, inventories nothing writes - which join nothing. And `tryfirst`, since xdist's own hook turns the marks into groups
and, run first, would see none and rebuild the fixtures test by test. Grouped by module until 2026-11, a shard's
last minutes were one worker's: `test_claude_login.py` alone held one for 500 s. Plain `pytest` runs one test at a
time, for one test or one file. `just test unit --shard <i>/<n>` runs one of n shards, each group whole in one,
balanced by `tests/unit/durations.json` (`tools/unit_durations.py`, from each shard's report as CI runs them); CI
runs six, `CI result` fails unless all six passed, and `test_shards.py` holds that the shards together collect
every test exactly once.
Every child a test signals or types Ctrl-C at starts with SIGINT,
SIGQUIT and SIGHUP at their defaults (`harness._default_signals`), since a suite started with `&` or under
nohup has them ignored; and a pseudo-terminal's reader reads to its end after the run exits
(`harness._drain`), since on a loaded machine its last bytes arrive late, up to the test's deadline. Keys typed
at a pause go in once it has held the terminal in raw mode for 10 ms, looked at every 20 ms while they wait, since
pause flushes the input just after it sets raw mode and then waits in silence; a `vars_prompt`'s, read by `input()`
in canonical mode (a step marked `canonical`), as soon as it asks. Every fake API a test starts binds a port the
system picks and prints it (`harness._start`), so no port is probed, freed and then taken by another worker's fake;
and starts with `socket.getfqdn` answering at once (`harness.FAKE_START`): for the fake Proxmox's `0.0.0.0`, macOS
spent five seconds on it.
A run a test gives up on - a timeout, a Ctrl-C, a failure on the way -
is ended whole (`harness._end`): its process group, and every process found under it before any is signalled
(`lib/claude_vm/proc.py`), since ansible-core's task worker detaches into a session of its own
(`WorkerProcess._detach`: `setsid`, stdio on `/dev/null`) - a SIGINT or SIGTERM the playbook sends on to it
itself, a SIGKILL or a SIGHUP it does not - and the group's SIGKILL alone left the task in flight behind,
reparented to PID 1 and idle for good, 46 of them after a suite that hung; though not an async job's wrapper,
which daemonises itself and leaves. The interrupts are held off while a tree is read and signalled, and a table
that cannot be read is a warning, the group still ended. And every pytest process, the
controller's and each worker's, takes SIGTERM and SIGHUP as a Ctrl-C (`tests/unit/interrupts.py`, installed by
`conftest.py`): `tools/live` sends its part's group one when `just test` is killed or its terminal closes, CI's
cancel sends one, and Python's default ended the worker at once, its run and that run's tree left behind. Once
only, since a second - a hangup moments after a kill - would end the harness's kill half way; and one ignored
on the way in stays ignored, so a suite under nohup still survives its terminal closing.

pytest over `tests/unit/`, file by file:

- **The package** (`test_claude_vm.py`): `lib/` holds `claude_vm` alone; every core module imports in a fresh
  `python -I` of its own without loading `ansible`, `yaml`, `asyncio` or `subprocess` (`proc.py` alone loads
  `subprocess`), and the package itself imports none of
  its modules; each record refuses a field its annotation cannot hold (`True` as an `int` among them), `plain`
  copies Ansible's values into exact types, `to_wire` puts `format_version` and `kind` first; an adapter's filter
  imports its module only when called and refuses another checkout's `claude_vm`, known by file, not by spelling;
  two writers through `store.py`, 300 changes each at once, lose none, and a replace keeps the file's mode;
  and every file in `filter_plugins/` is an adapter that loads nothing of the core when loaded, each of its filters
  the core's function of that name.
- **Filters** (`test_filters.py`, `test_vscode_filters.py`, `test_desktop_filter.py`,
  `test_text_filter.py`): the guest-agent readers, `guest_ipv4` among them refusing anything that
  does not parse as one IPv4 address, the managed SSH config's blocks, VS Code's settings reader (since 1.1
  its plan's own `vscode_setting`, not a filter, though its test file keeps the name), the sign-ins'
  `page_opener`, the run a Claude sign-in waits for (`controller_run` and its state: running, gone, a pid
  another process has since, another machine, a record the VM spoiled), `plain_text`, and
  `banner`'s block.
- **`just status`** (`test_status.py`, `test_status_live.py`, `test_status_vms.py`,
  `test_status_work.py`): the report's filters on canned
  reads - every value one line of plain text over a corpus of hostile ones, the drift between a VM and
  `local.yml`, the twelve buckets of a day at Proxmox VE 9's resolution and 8's, the ranking and exit code,
  every line within 100 columns, of a report holding every state at once, whose every `just` command the
  justfile accepts (`test_commands.py`'s own check); `status.yml` against the fake Proxmox, on VE 9 and 8, with a refused
  token, an API that does not answer, a name that matches nothing, and settings that differ, with every
  request it made a GET; statically, every task it runs and every file and entry point it reaches a read
  or the report's own write. Its callback, driven in process at a stand-in terminal, and the real playbook
  at a pseudo-terminal of the test's own, read back through `tests/unit/vt.py`, a small terminal: a window
  the report fits, in colour, one short and narrow, a resize each way, Ctrl-C, a dumb terminal, a pipe, and a
  warning between frames, the screen and its scrollback ending in exactly the finished report the log
  holds; the resize and Ctrl-C runs hold the VMs' stand-in `ssh` until the test has acted, since a Linux
  pty takes the rest of a run's output without blocking it, and the run could end before the frame it acts
  on is read. The Ctrl-C is sent only once each running VM waits in that `ssh`: a SIGINT that lands while
  Ansible forks a worker is lost (`CLAUDE.md`, things that have bitten this repo). Inside the VMs
  (`test_status_vms.py`): `vm_status_info` on fixture trees, in this process, each editor's windows by the rows'
  matches, a process under the first it meets, and the module's `editors` argument held to the core's `ProcessMatch`
  field for field; status over stand-in VMs with discovery off, each running the three roles' `status` entry points
  with the gh and tailscale fakes, a stand-in `systemctl` and a fixture tree, every file of each VM unchanged after
  and the fakes' logs holding only reads; the host key, `status_ssh_args` held to `ansible.cfg`'s, the options as
  `ssh -G` resolves them (and as they reach a stand-in `ssh`, first, in `test_status.py`'s run of the fleet), and
  ssh's two host-key refusals each said for what it is. Every status run in a test gets that refusing stand-in `ssh`
  (`status_root`), so none reaches a VM or this machine's ssh. WORK (`test_status_work.py`): `git_checkouts_info`'s
  `work` over real throwaway repositories - a worktree under `.claude/worktrees/` and one outside the projects
  directory, a branch with no upstream, a detached HEAD with commits of its own, a stash, untracked files - every file
  of them, `.git` included, as it was after. Each editor's windows are counted under the editors list's keys,
  `vscode_windows` kept as 1.0 has it, and the ACCESS line names the editor that is set.
- **`just settings`** (`test_settings.py`): `tools/settings` against inventories of its own, made from the
  example files with fake, low-entropy values and a vault under a throwaway password, never the checkout's
  settings; each run with a `HOME` of its own and the collections' path pinned beside it. Every role's rows
  are its `argument_specs`; every line within 100 columns. The attacks: a `pipe` lookup behind a group
  variable in `hosts.yml` (refused, never run), an executable in `inventory/` (never run), a secret vaulted
  inline in `local.yml` and a setting reading `vault_vm_password` (masked), a GitHub username of `local` (no report
  refused), a secret copied in under a computed name (nothing printed, exit 1), a control sequence in a
  value; a write started while another holds `local.yml`'s lock waits for it, then writes. Where values come
  from: `proxmox_vm` evaluated on `claude_vm_pending`, `proxmox_template` on the
  Proxmox host with its host variable, a short setting followed to the role variable it feeds, what `just
  deploy`, `just login` and `just destroy` set beside the value other runs use, a default that lives in a
  role's `default()`. The vault refused without a password and with a wrong one, opened from
  `ANSIBLE_VAULT_PASSWORD_FILE` as `ansible-playbook` opens it; the inventory's files unchanged after. Every
  run fails if any fake secret is in what it printed. `--value`, each write into a copy of its own: in place
  of the example's line, of the line that set it with its further lines, or at the end past an inline `!vault`
  that stays byte for byte; a wired role variable written as its setting; each type as the role's
  `argument_specs` take it, and refused when they do not; every secret refused for `just vault`, its value
  never said; a per-VM name, what a run sets, the host's address, an internal value, an expression (its
  `pipe` never run), a control character, a vault secret as a plain value, two lines of one name and an alias
  the edit would break all refused with the file unchanged; an unchanged value written over by nothing; what
  outranks `local.yml` said; the file's mode kept and no scratch file left. `--reset`: a value set and then reset
  leaves `local.yml` byte for byte as it was, in place or at the end; a role variable's setting and its own line
  both taken out; nothing written where nothing is set; a per-VM name set by hand taken out; a secret, the host's
  address and an inline `!vault` refused. From the review: `--changed` beside a name at its default, an internal
  name shown alone, an unknown name or a refused value never said back, a role variable set by hand written where
  it is and named after a reset, a quoted key never written twice, `hosts.yml`'s group vars ranked below
  `local.yml`, and no host-pattern warning.
  The 1.1 name pairs: the new name fed from 1.0's while it is unset, `--value` on either writing the new name and
  taking the old one's line out, `--reset` on either taking both.
- **The stdout callback** (`test_attention_callback.py`): `callback_plugins/claude_on_proxmox.py` run
  for real - untagged output byte for byte the default's, no hook failing, the browser block, one
  waiting line for a tagged retry, NEXT STEPS from `set_stats` records, colour on the screen and never in
  the log, `just init`'s two matters before a VM's, a probe filter file executed in one process however many
  tasks template it, under either stdout callback - and statically, the six tasks that carry
  `attention_browser`, every prompt drawn by `banner` (seven, `just init`'s two among them),
  and every report followed by its record, `just init`'s two among them. Each role's own tests then assert what it records, in every
  state they run.
- **What every live output draws with** (`test_live_region.py`, `test_screen.py`, `test_vt.py`): the
  region against a stand-in `Display` - what is printed above it, a frame cut to the screen, a pause and a
  vars prompt each with the screen to themselves, the route, a screen too small, the echo off on a
  pseudo-terminal of the test's own - whose `display`, `prompt_until`, `do_var_prompt` and lock it holds to
  ansible-core's; `Output`, the colour and the words printed to the screen and the log; the lines' cut,
  alignment, palette, bars and durations, none under an hour wider than five characters; and `vt.py`, the small
  terminal the live tests read back through, reading status's and deploy's streams the same cut anywhere. Every
  test that runs something at a terminal opens it through `harness._at_a_pseudo_terminal`, and every live test
  through `tests/unit/live_kit.py`, which gives each run a `HOME` of its own.
- **The live output** (`test_run_view.py`, `test_deploy_live.py`, `test_login_live.py`,
  `test_destroy_live.py`, `test_code_live.py`, `test_lint_live.py`, `test_init_live.py`,
  `test_test_live.py`, `test_live_recipes.py`, `test_live_commands.py`): the model, fed
  events with the time given - a running, a waiting, a failed, a dry and a fleet run's frame, a wait known
  by how long it runs, a step's time its own tasks', a running stage's ended steps whole, a report a later
  report on its matter made old left out of the summary, as under `just deploy`, no line wider than the
  terminal at any of ten widths, a frame folded to its height, the line per step, the summary; a stage by its
  play, a quiet one, an include counted once, a serial play's batches, a percentage that never falls, a run of
  one stage drawn as its steps, a prompt as the person's time, a machine's wait with its try, a fleet's marks,
  the three heads, a long report by its first sentence, a task never shown, a step by the blocks around its
  tasks and an include counted there, a `meta` task started for each host as one and the VMs an `end_host` left,
  every VM still in a play at its running step, a stage's row naming the VMs a fact names, a raise on this
  machine naming the VMs it repeats, an answer said as one, a step from the task it starts at and by its task
  file, a run of one stage's VM in its header and its steps' whole names - and the stage table against
  `deploy.yml`'s imports and `--only`'s playbooks. Then the callback for real: a stand-in
  deploy, its playbooks named as the real ones, at a pseudo-terminal of the test's own, read back through
  `vt.py` (which counts a frame's wrapped lines, the titles and the bells): the region drawn and
  nothing of it left, with Enter pressed mid-run, the plan and a long report whole, a fact a VM forged unused,
  the switch not inherited, the prompt whole and answered, the diffs of `--check`, 50 by 12, 200 by 60 and a
  resize, Ctrl-C; a failure in a rescue, a loop's failed item, a crashed module and a long output's end said; a
  failure a rescue takes through an import or include dim, and one a rescue filtered out by `--tags` said; a
  failure raised again drawn under its cause, a report's lines kept, NEXT STEPS line for line the stream's; a
  task's file the one the import nearest the play names;
  the log every line the stream logs, in order; a line per step through a pipe; `-v`, no variable and no log
  streamed; and no other tracked file naming the variable. `test_login_live.py` runs the real `login.yml` once, at a
  pseudo-terminal, against `test_sign_in_plays.py`'s stand-in VMs and fake GitHub: each failure under the sign-in it
  happened in, the raise naming none, the `SIGN-INS` table's marks, the head naming the VMs that failed, and the
  region gone. `test_destroy_live.py` runs the real `destroy.yml` against `test_release.py`'s fakes and stand-in
  VMs, every path it writes pinned: the question answered, the four stages, each VM's release by service and no step
  of a role's own, the VM it started, each VM's outcome; a VM Proxmox would not delete the one that failed, under
  its step, and the raise naming none; and a deletion not confirmed said as the answer, the title `declined`.
  `test_code_live.py` runs the real `code.yml` against the fake Proxmox and `test_code_session.py`'s world and pins:
  the ten steps, the VM in the header, the window opened, the summary's steps whole and no row of the fleet's
  skipped tasks; and a VM that is not there said under the step it failed in. `test_live_commands.py` runs
  `tools/live` in a stand-in recipe in parts, the justfile's trap at its top: passed through piped, without a title,
  with a `-v` or a bare `-vvv`, without the engine's packages and without a state (`LIVE_DIR=`); an ignored Ctrl-C
  kept, and 126 and 127 as a shell's; drawn as one run, the earlier part done above the next, a failed part's last
  40 lines whole, the head and title `failed` and then `stopped` after Ctrl-C, the child's every line in the log and
  the switch never in its environment; the state 0600 in a 0700 directory, another run's never taken over, and only
  a run's own states pruned; a playbook as a part, whose NEXT STEPS wait for the run's end, and one Ctrl-C ended
  before its stats said stopped; an end with no state saying nothing, and one that fails leaving the recipe's
  status; a Ctrl-C pending before any handler taken, the part running nothing; a second Ctrl-C ending a part whose
  child's group would not end, and a grandchild in a session of its own with it; SIGTERM and SIGHUP ending the
  child with everything it started, the echo back; a command that asks at the
  terminal failing at once; a passing part's warnings above; a command named past `env`, a shell's by its step; a
  signal's exit as a shell's; no bar falling between parts; ansible-playbook's 99 said stopped; a run that ended
  well before the steps it listed said stopped; and NEXT STEPS keeping a record's lines whole, as the stream does.
  `test_lint_live.py` runs the real `just lint` at a pseudo-terminal with `test_justfile.py`'s stand-in linters:
  each check to come until it runs, its last line, no counts, the `CHECKS` table, every line in the run's one log; a
  check that fails, its end whole, its exit code the run's and the checks it left unrun named; a just that is not
  the pinned one, its check left out and the run done; and `ANSIBLE_ARGS=-vvv` through the justfile passing every
  check through. `test_init_live.py` runs the real `init_settings.yml` and `init_check.yml` as one run in parts, the
  first as `test_init_settings.py` runs it at a terminal with its questions typed, the second as
  `test_init_check.py` runs it against the fake Proxmox: the questions as steps, the checks drawn as they are
  recorded and whole in the summary, one NEXT STEPS; and a check that fails drawn as the failure, NEXT STEPS keeping
  its record's lines. `test_test_live.py` runs the real `just test` with `test_justfile.py`'s stand-ins, but for the
  real pytest with `tests/unit/live_pytest.py` over a few tests of its own and a stand-in Molecule that says its
  matrix and actions: lint as the run's first stage, every stage to come listed, pytest's count, each scenario a
  step, every line in the run's log, each role's scenario named there and its run titled with the words typed; a
  test that fails, above as it fails by its whole ID and listed, and the stage it left unrun named; a converge that
  fails, its task and Ansible's words above while Molecule cleans up, and why it failed at the end, and an
  idempotence test that fails, the task that changed at the end, each in Molecule 26's order; a deprecation said and
  Molecule's routine notices left in the log; a part whose lines no reader knows ended by its exit code; the lines
  Molecule's own logger writes, which the reader must know; and the plugin writing nothing without its file, and
  ending a test whose xdist worker crashed. `test_live_recipes.py` holds every declaration to the playbooks it
  names - each play, block and task it matches is there, each fact it reads, a stage's checklist among them, is set
  by a task, and a VM's address and ID are where the engine reads them - and every live test that runs a real
  playbook to the paths its own tests pin, read from its code, so a pin in a comment is none.
- **The editors' plans** (`test_editor_conformance.py`): every family conforms to the seam and every row of
  `claude_editors` parses; every family's plan of every window case passes the registry, its launch never ZFS's `zed`;
  a refusal leaves nothing beside it; a foreign Zed task file is left and said, and none is written at a helper path a
  task cannot be handed as it is; VS Code opens its workspace, or the bare folder, over the alias, and a row of its
  family whose window starts no Claude terminal writes nothing; the paths are written and launched as `code.yml` built
  them, and with nothing written the window opens whatever `editor_files_dir` names; no write leaves just code's
  directory or goes into the folders YAML writes itself; each of the registry's rules bites, alone, on a plan built to
  break it; the inputs and rows are held to their types, a row's window match naming its program first; each row's
  match goes to `vm_status_info` as its fields alone; and both filters take Ansible's own values, through
  `ansible-playbook`, where `tasks/editor_open.yml`'s NEXT STEPS record is the action's own line, or `''` once nothing
  is left.
- **Processes** (`test_processes.py`): `lib/claude_vm/proc.py` listing a grandchild in a session of its own
  and ending it with its parent, where a kill of the group left it reparented; a child of pytest's own group
  ended alone, never the group; a pid remembered by its start signalled only while the process of that start is
  at it, never once another has it; a root exited, a zombie or reaped, its group still ended - its children moved
  on the moment it exited, and a zombie no longer answers for its group; a table that cannot be read said, the
  group still ended; `harness._end` on a real playbook with a task in flight, its detached worker gone with it;
  `interrupts.install` leaving a SIGHUP ignored on the way in, nohup's, as it is, and its handler raising once;
  and a pytest run with `interrupts.py`'s conftest sent SIGTERM and SIGHUP to its whole group mid-task - one
  process, and a controller with two xdist workers each holding a playbook - ending as interrupted with every
  playbook and its task's worker gone.
- **`github_repos`** (`test_github_repos.py`) and the **tracked-file guard** (`test_tracked_files.py`), both ways.
- **The CI gate** (`test_ci.py`): `CI result` needs every job of `ci.yml` but the canary, runs `if:
  always()`, and no job of the gate may `continue-on-error`; its script, run as GitHub runs it, passes a
  success or a skip and fails a failure or a cancel. Every Molecule scenario names the one image pinned by
  digest, the shared configuration none, and the canary runs that image's repository at `:latest`.
- **The shards** (`test_shards.py`): `--shard` collected as each CI shard collects it - two, three and six
  shards that between them run every test exactly once, and the tests that share a fixture's run in one; every
  name an `EACH_WORKER` lists a module-scoped fixture of its own module.
- **Remote Control** (`test_remote_control_playbooks.py`): `claude_login.yml` and `remote_control.yml`
  against canned logins and stand-ins, and the harness they share (`tests/unit/harness.py`: an
  allowlisted environment, desktop stand-ins - and this machine's tailscale, gh and claude - that fail a run
  that reaches for them, no terminal).
- **The Claude sign-in** (`test_claude_login.py`): `browser_login.yml` against the stand-in `claude`,
  one run of `claude_login.yml` for every sign-in case - a VM whose ssh refuses the connection among them,
  said at once and ending no other VM's turn, a failed sign-in said in its own, one VM per batch and the
  work after the sign-ins waiting for the last - with what each VM records of its answer read after it;
  the end of an attempt, a TERM and a KILL only for a pid that outlives it by a second, run traced;
  an attempt still waiting refused while its run is under way here, and with how to end it when that run is
  another machine's or unrecorded, and one a run stopped part way left - killed while it waited for the
  browser, the forward's far end up - ended by the next sign-in, claude and the far end each only while it is
  still that process, and the VM signed in;
  five runs at a pseudo-terminal of the test's own - the page before the prompt, a partly copied code,
  an expired attempt, Ctrl-C then A, and keys typed before the prompt is ready - with the code searched
  for in every run at `-vvv` with module arguments injected; and the browser coming back by itself, a
  stand-in `ssh` recording the forward - one from each loopback address, each required, both made under
  an `AddressFamily` the user's ssh config sets (the stand-in takes `-L` and `AddressFamily` as OpenSSH
  does) - and running its far end here, in a session of its own, and the test playing the browser at the
  stand-in's callback: no prompt, a forward refused or a port held here on one address only, a page that
  is not Claude's, a run over SSH or no browser here falling back
  to the paste, and a browser that never comes back skipping the VM's later sign-ins.
- **The GitHub sign-in** (`test_github_auth.py`): the `github_projects` role's `auth.yml`,
  `clone.yml`, `device_flow.yml` and `github_login.yml` for real against the stand-in `gh` and the fake
  GitHub API on a loopback port, one host per sign-in state, twice over for idempotence, with `git
  credential fill` proving that git itself gets the token through the config written, deploy's GitHub
  step per VM - starting by itself, and asking first with `github_login_auto_start: false`: Enter and `n`
  at a pseudo-terminal, and with no terminal (`claude_vm_login_terminal: false`) no question and no flow,
  each VM told why - every task that renders a token held to `no_log`, and the whole output, module arguments
  injected (`ansible_inject_invocation`), searched for every canned token; and two device flows with the desktop steps on, against recorders of the test's own
  first on PATH, opening GitHub's own page whether gh named it or another, which is said and never shown,
  nor quoted where a failure quotes gh's own lines - gh's status page among them.
- **The sign-in playbooks** (`test_sign_in_plays.py`): `claude_login.yml`, `github_login.yml` and
  `deploy.yml`'s own sign-in steps end to end - a VM whose ssh refuses the connection among VMs that
  sign in (for `claude_login.yml` alone, in `test_claude_login.py`'s run), a failed sign-in said in its
  turn, a failure after a sign-in (Remote Control's start, a clone) told apart from a failed one and
  raised at the end of `just deploy`, deploy with no terminal - GitHub's device flow started by itself for
  each VM whose Claude code was entered, refused or not, and for none whose sign-in nobody answered - a
  refused code and `deploy_sign_in: false`, and one VM per batch only where a sign-in prompts; and
  `login.yml` - `just login` - running the three in deploy's order and by its rules, the ones its sign-in
  plays read from `playbooks/vars/sign_in.yml` under deploy too: no device flow for a VM whose Claude
  sign-in nobody answered, said once for it and in no report, nor, with no terminal, for one a code signed
  in that `github_login_auto_start: false` would have asked about, a VM whose code was refused or whose
  Remote Control failed to start still given its GitHub and Tailscale turns and failed at the end, and a
  report without names; and NEXT STEPS holding each
  failed sign-in, unreachable VM and failure after a sign-in, with the command that retries it.
- **Tailscale** (`test_tailscale.py`): the role's files from `ownership.yml` on, and
  `tailscale_login.yml` whole, against the stand-in `tailscale`, with stand-ins for `dpkg-query` and
  `getent`: a first join once, with the key on stdin and in no report, argument or file; no key for a node
  whose key expired, and a node logged out by hand joined again - unless Proxmox records the VM as having
  joined (`tailscale_joined_before`, its tag matched whole in a `;`- or `,`-joined list), when the key is
  held back, under `key` by name; any login the role's
  Tailscale has had, or a sign-in under way, held to be recorded, by configure and by the sign-in alike, and
  the record (`tasks/tailscale_record.yml`) after each, against the fake Proxmox: tagging only the VM of that
  name and VMID that is the project's, its tags written back as read, never in check mode, and VM by VM -
  one Proxmox cannot answer for said and kept for NEXT STEPS while the others are written; a refused
  key and an OAuth secret without tags as reports; one `set` for drift; a login or a Tailscale made by hand only read; tags compared only
  when configured; `NoState` reads, `Starting` and a silent daemon; check mode; the web login's one `up`,
  its page said before the wait and opened only when it is Tailscale's, an approval after `up` exited, one
  that waits for an admin ending the wait at once, a login already waiting for one shown no page, a wait that
  runs out; a page approved after the run, recorded as the role's login by the next configure,
  and a join still under way marked pending - an earlier key's refusal still in Health included - a refused
  key or refused tags leaving nothing; a control server's page trusted however its address ends, and never
  one on another host; just deploy's sign-in step saying nothing its configure play did again; an expired
  login made by hand left to whoever made it, never signed in again by the web; a page still waiting reported
  before an expired or refused key, just deploy's two plays included; a Tailscale dpkg does not know only
  read; `tailscale_auth_method` `key` and `web`; deploy's step VM by VM, starting by itself and asking
  first with `tailscale_login_auto_start: false` - Enter and `n` at a pseudo-terminal, and with no terminal
  no question and no web login - and NEXT STEPS keeping the role's line for a refused key or a
  page still waiting where Claude's sign-in was skipped; `configure.yml`'s rescue and last play; and the guards
  that keep every scenario, the Vagrantfile and this harness off Tailscale, and the role in the justfile's `scenario_roles` and CI.
- **Destroy's release** (`test_release.py`): `destroy.yml`'s release play and report against the
  stand-in `claude`, `gh` and `tailscale`, the fake GitHub API's credential revocation and the fake
  Tailscale API: a VM released from everything, with no token revoked at GitHub; the vault token and one
  pasted by hand signed out and told apart, the vault token even under a marker claiming it, and a logout
  gh refuses said as such; a
  device deleted by an API token and by an OAuth client, refused, already gone, and never when the ID the
  VM reports names another device; a VM that could not be reached, with what is left of it named; the
  Claude Code sessions a VM alone holds saved first, byte for byte and 0700/0600 with their `sessions.json`,
  never a copy just code made still as written nor a transcript without a message, never a link the VM
  holds nor a folder not named as Claude Code names one - the file a link points at here left as it was -
  just code's manifest read where `editor_files_dir` (`vscode_workspaces_dir`, its 1.0 name, there) puts it, the save turned off, the save without
  the release under `destroy_release: false`, a VM whose sudo refuses saved and then deleted unreleased,
  said so, and one that fails keeping the VM neither released nor deleted, with no transcript in the
  output, the save's tasks held to an allowlist that keeps them off sudo and unreachable-safe; every
  canned credential searched for in the output; and, read from `destroy.yml`, a name two VMs share
  refused before anything starts, the wait for a started VM's address on its own NIC, and a VM it started
  stopped again however the run then fails - before it deletes anything, or at every deletion; and, run
  against the fake Proxmox API with its listing lagging a start, a VM destroy started given its address,
  and a relative `destroy_sessions_dir` refused before any VM is started.
- **`just code`** (`test_code_session.py`): the Claude terminal's script against stand-ins, once at a
  pseudo-terminal for Ctrl-C, the window step's own tasks in `just deploy` and out of it with stand-ins
  for `which` and `code`, and the allowlist that holds `code.yml` read-only on this machine, followed
  through every import its task files make. The
  session notes' helper with nothing but stand-ins on PATH - the live section rewritten and nothing else,
  exit 0 and the notes opened when its Python raises, hangs or is missing, a read that hangs costing
  2 s, a symlink neither followed nor opened, the time in the zone it is given or in UTC for one it
  cannot find - the refresh after a sign-in in the terminal, the zone `code.yml` finds (`TZ`, the
  `/etc/localtime` link on Linux and macOS, `/etc/timezone`, else UTC, shell syntax and paths refused),
  and the workspace each window gets: the notes' folder only where its own folder holds them.
  Zed's window as well, with a stand-in `zed` and `zeditor` first on PATH: ZFS's `zed` passed over by its
  `--version`, a folder an ssh:// URL cannot carry refused, the marked `.zed/tasks.json`, as `to_nice_json` writes
  it, written only where the window lists it and never over a file that is someone else's, and the editors list held
  to the task files and to `--editor`.
- **The session handoff** (`test_session_handoff.py`): the plan - by origin in every URL form here,
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
- **The session notes** (`test_session_notes.py`): `vm_session.md.j2` rendered as `code.yml`
  renders it - the placeholder, a live section carried over, the parts switched off, the same file
  from every window, and no time, template marker or token shape in it.
  In an editor whose window runs no task (Zed), the notes say what to do instead, and name the listed Claude task
  only where just code wrote it. Their `just code` lines carry `--editor` where the run's editor is not the
  setting's, in both directions.
- **`just init`'s questions** (`test_init_settings.py`, `test_settings_filter.py`):
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
  Tailscale question, `n` written and never asked again, `y` written and its auth key, hidden, written
  into the vault and in no output or log, Enter at the key asked again next time, a wrong key asked once
  more and left unsaid, no key asked with one in the vault or the web login only, Enter at the question
  writing nothing and saying Tailscale stays on, `--tailscale on` asking only for the key; the Tailscale
  notice, naming the auth key or the sign-in page, gone once `tailscale_enabled` is set, and
  `--tailscale` writing it; NEXT STEPS keeping each value still to set, as the report says it, with no
  terminal and with Enter at each, in a scripted run that then fails too, and a vault that cannot be opened;
  nothing once every value is set, even with the vault left plaintext, which the checks say, and no `just
  deploy` said there;
  the vault decrypted to the example with the answered lines replaced,
  `0600`, and the secret in neither the output nor the log, typed or given, with module arguments injected
  and the log at `-vvv`; a vault read and written as it is with `ANSIBLE_VERBOSITY` and `ANSIBLE_DEBUG` both in the
  environment, every `ansible-vault` run quiet; statically, every write under `init_ask` and every task that renders the secret
  or the vault's text `no_log`. Cases one run can show share it: every question answered wrong in one run, every
  value given in one, and the values that break their rules, or differ from what is set, each refused together; the tests
  read and write the vault themselves in process (`VaultLib`), and only the playbook starts `ansible-vault`.
  The offer to make the token, against a stand-in Proxmox host (the local
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
- **The Proxmox access script** (`test_proxmox_access.py`): `tools/proxmox-access.sh` under `/bin/sh` - dash
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
- **`just init`'s checks** (`test_init_check.py`): `init_check.yml` against the fake Proxmox on a
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
  bridge, no host in the `proxmox` group, and the playbook loading with duplicate keys made fatal. NEXT
  STEPS: nothing recorded while a value is the example's; `just deploy alpha` once every check passes, said
  last in the report too, and nothing while the GitHub username is the example's; each problem with its fix,
  one and three of them; and what could not be checked when nothing is wrong - the template on a node just
  gone down, still listed by name (the fake's `named=1`). Statically: every task a read or a NEXT STEPS record (a `uri` a GET that saves nothing), every `uri` with the CA
  path and `no_log`, the settings written only through `tasks/init_local_setting.yml` at a terminal or as
  given, and no run reaching a real `ssh`. Running
  a fake on a loopback port, the fake Proxmox and its certificate (`_fake_pve`), `_pve`, a VM made in it
  (`_pve_vm`), a run's `group_vars` pointed at it (`_pve_vars`), its request log (`_pve_requests`) and the
  fleet `just status`'s tests read (`fake_fleet`) live in `tests/unit/harness.py`, shared by every module that
  runs against the fake; each file keeps its own `fake_pve` fixture.
- **The managed SSH config** (`test_ssh_config.py`): `tasks/ssh_config.yml` for real against a
  temporary home, resolved with `ssh -G` - the refusal of a name or address that is not one plain word,
  the guard that every scenario and the Vagrantfile keep the managed file out of `~/.ssh`, that no
  playbook sets the task file's inputs as facts, and that the user's own Include and file mode are left
  alone, a group the user is not in included (macOS).
- **Smaller ones**: `test_configure_tags.py` (`--tags claude_code` still runs discovery and the alias
  play), `test_deploy_report.py` (deploy's opening message, rendered by its own tasks, naming only the
  sign-ins that open a page and the VMs as a list), `test_proxmox_vm_absent.py` (deleting a VM
  forgets its host key under its address and `<name>.<domain>`), `test_var_names.py` (no task var
  named like a register or fact, in any playbook or role), `test_cloud_init_wait.py` (a first boot
  that failed is said with what failed and how to build the VM again), `test_gathered_facts.py` (no
  tracked file reads a fact by its top-level name, and every `ansible.cfg` a run reads, the tests' own
  through `harness.ansible_config`, leaves facts out of the top level), `test_remote_tmp.py` (with
  pipelining, `tasks/remote_tmp.yml` makes the `remote_tmp` the run is configured with before a module
  would make it and warn, and both plays that run modules as root import it before their roles),
  `test_vm_names.py` (no VM may take an inventory host's name or one ssh reads as an address, refused before
  Proxmox is asked), `test_ansible_config.py` (pipelining for the local connection too, and no inventory
  plugin that runs code), `test_galaxy_install.py` (a failed Galaxy request is retried, and every dependency of
  a pinned collection is pinned), `test_pins.py` (the VM's uv never older than the controller's) and
  `test_init_local_setting.py` (`just init` refuses a value that holds a template delimiter), and
  `test_sudo.py` (passwordless sudo off takes away cloud-init's rule as well as the role's, keeps a new
  cloud-init instance from writing it again, takes the user out of the groups that reach root, and every play
  becomes root through `/usr/bin/sudo` with the VM's password).
- **The justfile** (`test_justfile.py`): the real `just`, at least the pinned version, run against
  stand-ins - on macOS under `/bin/bash`, 3.2, so the scripts keep to what it has. Statically, over `just
  --json`: no recipe has a dependency (which `JUST_NO_DEPS` would skip), no assignment starts a program,
  nothing is unstable and no `.env` is read, every public recipe has a `[doc]` and every parameter its
  help and a literal pattern, the VM recipes share one name pattern, no parameter is named like an
  assignment, `self` passes on every setting, and its
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
  `PYTHON`'s environment, or the checkout's own made with uv when none is brought, `--lock` alone, the
  stale-script repair, a Python that is no virtualenv refused by every recipe, the collections in the
  checkout's own folder, and `just clean env` deleting only that folder), and `init` from the questions to the checks - the `.vault_pass` its questions
  just made found, the values given as one JSON extra-var, a relative secret file where `just` was
  started, and the token secret refused in every spelling without being said; an exported name changing
  nothing; `tools/env-run`, which every local hook runs through, and its `--bin`; `vagrant`, `lint` (its
  parts, each linter's failure failing it, and the hooks skipping what lint runs itself), `test` (lint
  through `self` only without parts, each part, every role scenario, `--role`, and a failing part ending
  the run) and `docs`.
- **The commands named** (`test_commands.py`): every `just` command the playbooks, roles, wiki,
  README, CLAUDE.md, CI and the justfile's own messages tell a person to type, read with a placeholder
  where Jinja, an f-string or a shell variable splices a value in, is one the justfile takes, over `just
  --json` - the settings before it the justfile's, its options the recipe's own and their values ones they
  take, no `NAME=value` after the recipe, no misspelt part, no second recipe chained to it - and no `make`
  command, nor a name after one of its commands, no target only the Makefile had and no misspelt recipe
  is left outside the pages that tell the history, nor make's spelling of an option (`TAGS=`) on a line
  that does not say whose it is.
- **The wiki** (`test_docs.py`): the generated pages current, and the wiki whole and the same on GitHub as on
  the site; [This wiki](../development/docs.md#what-the-tests-hold) lists each check.
- **What a release must keep, and what may only shrink** (`test_persisted_names.py`, `test_ratchets.py`): the
  names runs have left on the VMs, on the Proxmox host and on this machine - tags, markers, drop-ins, the managed SSH
  config's blocks, the files `just code` keeps on a VM - each held whole, as a variable's value or its exact line, so
  a new name that contains the old one fails too; and how many lines of the code name one editor or one hypervisor
  outside its own files, file by file, against a table that a count above or below it fails.

## 11.2 Role scenarios — `just test role`

One Molecule scenario per role at `roles/<name>/molecule/default/`, inheriting the driver from
`.config/molecule/config.yml`, which turns pipelining on as `ansible.cfg` does: without it each task put its
module into the container, ran and removed it one `docker exec` at a time, and the ten scenarios took 16%
longer. Each names its own platform, a container named after the role, since Molecule replaces a list rather
than merging it - and the fake APIs a port of their own - so that `just test --jobs 4` can run them, and the
playbook scenarios, that many at a time (`tools/molecule-lanes`, the longest first): every scenario took 28
minutes one after another on an 8-core runner, and 7.5 four at a time, provision alone the floor. The
shared file turns Molecule's dependency step off: `just install` puts the collections in and `just test` checks
them first (`tools/check collections`). Each runs create → prepare → converge → **idempotence** → side_effect →
verify → destroy.

A role scenario's side effect runs the role where it must refuse through one task file,
`tests/molecule/refused.yml`: the role inside a `block`/`rescue`, then an assert that the failure was
the role's, at the task named where one is, with a message that matches every pattern the case gives.

`vscode_server`'s side effect takes the forwarding away three ways, each in a drop-in of its own,
runs the role over it, asserts the failure names the setting and the file, and removes the drop-in
whatever happened: `AllowTcpForwarding no` sorted before the role's drop-in, `DisableForwarding yes`,
and a `Match User dev` block sorted last that limits forwarding to the remote direction - the case a
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

Last, the side effect puts a line of the user's own into `/etc/tmux.conf` and runs the tmux tasks with
`common_tmux_settings: []`, which takes the block out and leaves that line alone, then as converge ran them.
Verify finds the block back beside the user's line, the file still 0644, and a tmux of its own loading the
file with no error and each setting in effect, the user's among them.

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

The `provision` scenario's checks are three files `verify.yml` imports in turn: `seeds.yml`, the fixtures
below; `fleet.yml`, discovery, `just status`, the playbooks where they must refuse or report, `destroy.yml` and
the SSH aliases; and `just_code.yml`, `just code`'s runs on the fleet as `fleet.yml` leaves it. CI runs the two
halves as jobs of their own, side by side: `verify_fleet.yml`, and `verify_code.yml`, which makes that fleet from
the seeds first - `molecule.yml` takes the one to run from `MOLECULE_PROVISION_VERIFY`, so
`MOLECULE_PROVISION_VERIFY=verify_code.yml just test provision` runs one half here, and `just test provision`
both. Whole, the scenario was CI's longest job, about eight minutes; its halves end in about five.

The `provision` scenario is deliberately adversarial. Before discovery runs, `seeds.yml` seeds VMs
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
- `discover.yml --check`, as `just deploy --check` runs it, must still find `alpha`;
- re-running `provision.yml` after shutting `beta` down must exit 0, publish `alpha`, report `beta` as
  stopped and leave it stopped;
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
  `vscode_workspaces_dir`, and one case over real SSH to an address nothing answers on. The twelve
  that `code.yml` refuses before it refreshes the alias run at once; the five it refuses after (the
  project's, the missing and the empty directory) write the shared SSH config, and run one by one beside
  them - all of them children of one task, so a Ctrl-C ends them with the verify. Every
  child run gets a 3 s SSH connect timeout, since the fake VMs' `192.0.2.x` drops packets. The user ssh
  config in the ephemeral directory never exists. The workspace files are read back and parsed: one
  folder, the project's path; one task, `folderOpen`, `bash -l` and a helper's path in that folder,
  the helpers written beside the files byte for byte as `playbooks/files/` has them; the whole
  directory's hides `.claude-on-proxmox`. With VS Code settings in the ephemeral directory (never the
  developer's) saying nothing, `"on"` behind a commented-out `"off"`, and `"off"`, the message asks
  for trust and *Allow*, trust only, and says the terminal will not start; reopening leaves the file's
  mtime and checksum alone; `editor_claude_terminal: false` opens the bare folder and writes no file,
  and no refusal writes one either.

## 11.4 The fakes — `tests/molecule/`

| Fake | Stands in for | Notes |
|---|---|---|
| `fake_pve_api.py` | the Proxmox REST API | TLS, token auth, stateful VMs, a guest agent, a request log; on request, a cluster listing that lags a start or stop, as a real one does; for `just status`, each VM's live figures, a day of `rrddata`, `pending`, the agent's `get-osinfo` and `get-fsinfo`, and the storages' fill and disks |
| `fake_qm.sh` | `qm` on the PVE node | stateful, one config file per VMID, logs every invocation |
| `fake_pve_cli.py` | `pveum`, `pvesh`, `pveversion`, `hostname` and `id` on the PVE node | for `tools/proxmox-access.sh`: the roles, users, ACLs and tokens in one file, answering the calls the script makes as a Proxmox VE 9.2 host was seen to - compact JSON, a refusal for what exists, the version's own guest-agent privilege - with every call logged and a write refused on cue |
| `fake_virt_customize.sh` | `virt-customize` | so `proxmox_template` runs in a container |
| `fake_github_api.py` | the GitHub REST API | serves local bare repositories, in paginated responses; `/user` and `/user/repos` answer a token, private repositories included; each repository's branches, none for a never-pushed one or a fresh one - listed as GitHub lists a repository created with no commit, with a push time and a size of 0, and served as a bare repository with no commit; `POST /credentials/revoke` takes credentials unauthenticated and refuses an authenticated call, as GitHub does, and `/fake/revoked` lists what it took, so a test can hold that nothing reaches it |
| `fake_tailscale_api.py` | Tailscale's API | the OAuth client exchange, and a device looked up and deleted by node ID, each device with the name it was started with; 403 and 404 on cue, and `/fake/deleted` lists what went |
| `fake_gh.sh` | `gh` | the auth commands only, installed ahead of the real one on PATH; one login per user, in a file under HOME, with every call logged; a login whose check never reached GitHub, and one GitHub refused, as gh 2.103.0 reports them; `fake-page` in its state directory makes the device flow name a page that is not GitHub's, and `fake-quit` end unapproved right after naming it |
| `fake_tailscale.sh` | `tailscale` | output that is Tailscale's own, as the research and a Headscale run recorded it, for the commands the role runs, against a node whose state is files under HOME: `status --json` and `debug prefs` shaped as a Running and a signed-out node really print, a join with a canned key on stdin, a refused or slow key, the web login's page and an approval that lands after `up` has exited (by the clock, `approve-after`, or at the Nth status read after the page, `approve-at-read`, which no loaded machine can outrun; `up-waits` shortens `up`'s own wait), `NeedsMachineAuth`, a taken name, `NoState` reads after a start, logout resetting the prefs, a node ID (`node-id`), and with `real-daemon` an answer only while the real daemon's socket exists; a key is never written, and `calls.log` shows `KEY` in its place |
| `fake_anthropic_api.py` | the Anthropic Messages API | for the real `claude` in the `configure` scenario: one short answer to every request, streamed or not, on 127.0.0.1 only, with each request's body logged, so a test reads the system prompt and the history a resume sent |
| `fake_claude.sh` | `claude` | `auth status` and `auth login` as probed against Claude Code: the page, the `Paste code here if prompted >` prompt, then `Login successful.`, `Login failed: ...` or `Invalid code` for the line read from stdin, writing `~/.claude/.credentials.json` on success; given `BROWSER`, it hands it the page that redirects to its own callback on `127.0.0.1` and takes a code from there as from stdin; `auth logout`; `fake-refusal`, `fake-no-page`, `fake-gone` and `fake-foreign-page` in its state directory make it refuse in other words, print an error in place of the page, exit before reading a code, or hand its browser a page that is not Claude's |

`fake_pve_api.py` is deliberately faithful where fidelity has caught bugs:

- `normalise_tags()` stores tags the way PVE does — `;`-joined, lowercased, deduplicated, sorted.
  Echoing the request back verbatim hid the `,` vs `;` difference from every test.
- The agent refuses `AGENT_READY_AFTER = 2` polls **per VM**. With a single global counter the first
  VM absorbed every refusal and the second's first poll succeeded, so a fleet never exercised the
  wait loop more than once. A test that is not about the wait turns it off
  (`PUT /api2/json/fake/agent-ready-after?polls=0`): `test_destroy_live.py`, whose two whole runs it
  saved ten seconds each. The `provision` scenario keeps the refusals and polls every 0.2 s instead
  (`verify_agent_delay`, and `claude_vm_release_agent_delay` passed to each `destroy.yml` run, whose
  play sets it to 5): 34 s of its time had been the clock's.
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
  can be shown to recover. `?status=offline&named=1` keeps it down with its guests' names and template
  flags still listed, as in the five minutes before the cluster drops a silent node's statistics.
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
  was made, which `PUT /api2/json/fake/disk-ctime?vmid=&ctime=` backdates. No change waits for a restart, and
  every VM's root disk is 11 GiB used of 31.

The fixture VMs the `proxmox_vm` and `provision` scenarios meet are made through the fake API by one task
file, `tests/molecule/pve_vm.yml`: each cloned from the template, then given a config, made a template or
started where its entry says. A config write of a fixture's carries no `name`, and every one the role makes
does, which is how both verifies tell the two apart. `tests/molecule/prepare_pve.yml` and
`tests/molecule/prepare_github.yml` start the fake APIs in a scenario's container.

Beside these, the `proxmox_template` scenario serves a "cloud image" locally, with a real `SHA256SUMS`
file, and a fake `/etc/pve/.vmlist` that places a VMID on a second node. The `tailscale` role scenario
and the `configure` scenario install the real Tailscale package and daemon - log upload off from its
first start - with the stand-in `tailscale` in front of it, `fake_tailscale.sh`, which each scenario that runs
the role installs in its `prepare.yml`, so no test reaches a Tailscale service.
Every scenario and both unit harnesses turn `login_desktop` off, so no test opens this machine's
browser or touches its clipboard.

## 11.5 Vagrant — `just vagrant`

`configure.yml` against a real VirtualBox VM, with `claude_vm_discovery: false`. Not part of CI. The box is
pinned to one version (`config.vm.box_version`), and Vagrant's share of the project folder at `/vagrant` is off: in a
checkout it would hand the VM, which runs an agent with sudo, `.vault_pass` and the vault, and the files this machine
runs next. `test_justfile.py` holds both.

## 11.6 Which scenarios need the network

`common`, `vscode_server`, `dev_tools`, `claude_code`, `github_projects`, `tailscale` and the `configure` scenario
install packages inside the container and need working Docker DNS. `proxmox_vm`, `proxmox_template` and the
`provision` scenario talk only to local fakes and still run offline.

The packages are real downloads, so those scenarios need internet access. The `configure` scenario
leaves the Docker Engine install out, since a daemon cannot run in the test container; the `dev_tools`
scenario installs it but does not start it.
