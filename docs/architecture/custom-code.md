# 7. Custom code

## 7.1 `filter_plugins/proxmox.py`

Five filters for reading Proxmox API output, one for a VM's tailnet address, and two for the managed
SSH config.

**`net_mac(net_config, default=_RAISE)`** — extracts the MAC from `virtio=BC:24:11:0E:72:04,bridge=vmbr0`.
It **raises by default**, because a caller about to act on one specific VM wants to know its NIC is
unreadable. Passing a default turns that into a skip, which is what a caller sweeping the whole fleet
wants: one odd VM (NIC removed in the UI, `net1` but no `net0`) should not abort discovery for
everyone else. The sentinel object distinguishes "no default given" from "default is `''`".

**`guest_ipv4(interfaces, mac=None)`** — reads `network-get-interfaces` output. With a MAC, only the
matching interface is considered, which is what keeps bridges the guest grew later (`docker0`,
`podman`, `virbr0`) from being picked up on a re-run. Loopback and link-local are never returned.
Returns `None` when nothing is available yet, so a `until:` loop can poll on it. The agent runs in
the guest, so the VM chooses what it reports, and the result becomes `ansible_host` and an alias's
`HostName` on the controller: only a string that `ipaddress.IPv4Address` parses counts, and
anything else - a newline and a `ProxyCommand` after a valid address, a leading `-` - is no address.

**`tailnet_ipv4(addresses)`** — the first entry of `tailscale status --json`'s `Self.TailscaleIPs`
that parses as one IPv4 address inside `100.64.0.0/10`, else `""`: tailscaled on the VM reports it, so
it is held to the same rule as `guest_ipv4`.

**`guest_address(vm_info, nic="net0")`** — the two above, composed the one way discovery and
`just status` need: given what `proxmox_vm_info` registered for one VM read with `config` and
`network`, the IPv4 the agent reports on the interface whose MAC is `config[nic]`. Returns `""` —
never raises — for a failed or skipped call, a stopped VM, a silent agent or a missing NIC, so a
caller sweeping the fleet skips that VM. Both playbooks pass `proxmox_nic`; they used to read
`config.net0` inline, which ignored the NIC the role honours.

**`proxmox_access_denied(msg)`** — whether a community.proxmox error means the token was refused:
`40[13]` only in proxmoxer's status position (`"<status> <reason>: <detail>"`, after the module's own
`"...: "` prefix), or the words Proxmox uses for it. The unanchored `40[13]` it replaces also matched
a VMID in the detail — `500 Internal Server Error: VM 4013 is not running` — which failed
the fleet listing and `just deploy --only configure` for the whole fleet over one stopped VM.

**`ssh_config_blocks(content)`** — the alias blocks in the managed file, as `{name}`, in file order. **`ssh_config_prune(blocks, resources,
tag_pattern)`** — the decision `just deploy --only ssh-config` makes fleet-wide, as data: `prune` (no VM of that
name anywhere), `untagged` (a VM of that name exists without the tag; left alone), and `nameless`
(tagged VMs the listing shows without a name, a node that is down), which when non-empty empties
`prune` into `held`. Pure, so every rule is a unit test rather than a fake-API fixture.

**`proxmox_vm_drift(config, wanted)`** — what of a VM's settings differs from what `just deploy` would
send, from its config as Proxmox reads it and the settings the role works out (`settings_drift.yml`):
`hardware`, each `{setting, wanted, has}` (cores, memory, the balloon minimum, start at boot, and the
disk only when smaller, compared in bytes, since Proxmox stores 1024G as 1T and cannot shrink a disk),
and `cloud_init`, the names of the cloud-init settings that differ (user, SSH keys, network, DNS). A
config not read has none. `just status` says each as a thing to do, with the `proxmox_vm_force_update`
run that sends them and what else it does ([§4.6](lifecycle.md#46-playbooksstatusyml--read-only-drawn-live)).

Covered by 58 unit tests in `tests/unit/test_filters.py`, and `proxmox_vm_drift` by
`tests/unit/test_status.py`.

## 7.2 `roles/github_projects/library/github_repos.py`

An Ansible module listing a GitHub account's repositories, following `Link`-header pagination. With a
token belonging to the queried user it uses the authenticated endpoint and includes private repos;
otherwise it falls back to the public listing, which GitHub documents as public repositories only.
Returns a compact, sorted list carrying `name`, `full_name`, `clone_url`, `ssh_url`,
`default_branch`, `fork`, `archived`, `private` and `empty` (never pushed to, so nothing to clone),
and the account as `user` (`login`, `name`, `id`), from `/user` when the token is the user's own and
`/users/<login>` otherwise, for the git identity the role derives.

Written against the managed VM's Python — `ruff` targets `py310`, not the controller's 3.12+.
Covered by 28 unit tests in `tests/unit/test_github_repos.py`.

---

## 7.3 `filter_plugins/vscode.py`

**`vscode_setting(text, key, default)`** — the value VS Code's `settings.json` gives `key`, for
`code.yml` to say whether the Claude terminal will start on its own. The file is JSONC: `//` and
`/* */` comments and trailing commas are dropped in one pass that copies strings whole, so a `//`
in a URL is not a comment and a commented-out `"task.allowAutomaticTasks": "off"` does not count.
A key is read flat, as VS Code writes it, or nested, and the later spelling wins, as VS Code's own
reader has it: dotted keys expand into a tree in file order, a nested object replaces what the flat
keys built before it, and a key whose path runs into a value that is not an object is dropped.
Empty text gives `default`; text that does not parse, or is not an object, gives `unparsable` when
the caller passes one, which is how `code.yml` says a file did not parse rather than take it for
unset. Either way a run that only reports what the editor will do must not fail on the user's own
settings. Read-only by construction; nothing in the project writes that file.

Covered by unit tests in `tests/unit/test_vscode_filters.py`.

## 7.4 `filter_plugins/desktop.py` and `filter_plugins/text.py`

**`page_opener(url, browser, found)`** — the argv that opens a sign-in page on this machine, shared by
both sign-ins so the two cannot drift: the first command `BROWSER` names (a `:`-separated list, each
split as a shell would, `%s` replaced by the URL or the URL appended), else `open`, else `xdg-open`,
from what `which` found; `[]` when nothing can, and the task is skipped. Only the first `BROWSER`
command is taken, since an argv holds one. Covered by `tests/unit/test_desktop_filter.py`.

**`plain_text(text)`** — what a VM printed, safe to quote in a message on this machine: every escape
sequence removed whole (OSC up to its terminator or the end of its line, DCS and the other string
sequences, CSI with its parameters, two-byte ESC sequences, the 8-bit forms), then every control
character but tab and newline. The VM's user controls what `claude`, `gh` and `ssh` print there, and
ansible-core 2.21 writes a failure's `[ERROR]` line to the terminal and the run log raw; removing only
the control characters left a clipboard write's base64 or a window title behind as text. Every message
that quotes the VM's text goes through it: the Claude sign-in's (`claude_code_login_said`), gh's
(`github_projects_login_text`, `_status_said`, `_token_said`) and `just code`'s SSH failure. Covered by
`tests/unit/test_text_filter.py`, and by the sign-in tests, which feed both flows escape sequences.

**`one_line(text, limit=120)`** — `plain_text`, then every newline, every other control character and
every Unicode format, line or paragraph separator (bidi controls and zero-width characters among them)
replaced by a space, runs of spaces collapsed, and the whole cut to `limit`, ending in "…". What
`just status` keeps of every value a VM or Proxmox supplies, so none starts a line of its own in the
report or in `--json` (§7.7). Covered by `tests/unit/test_status.py`, over a corpus of hostile values.

**`banner(body, title, host='', topic='')`** — the `#` block that sets a moment the run waits for the
person apart from the task output around it: a rule 80 columns wide (the terminal's width when that is
narrower), a title line naming the VM and the sign-in, the rule again, then the body, never wrapped so a
URL or a command stays whole to copy, and a last newline, so that a prompt's last word (`Code`,
`Answer`) starts a line of its own: a `'\n'` written in the Jinja of a folded YAML scalar reaches the
screen as a backslash and an `n`. Every part goes through `plain_text`. The prompts call it as a filter,
the stdout callback (§7.5) by path. Covered by `tests/unit/test_text_filter.py`.

## 7.5 `callback_plugins/claude_on_proxmox.py` — the waits, and NEXT STEPS

The stdout callback `ansible.cfg` names (`stdout_callback`, `callback_plugins`), and nothing else does: the
unit harness runs the JSON callback with an `ansible.cfg` that holds only `inject_facts_as_vars = false`
(`harness.ANSIBLE_CFG`) and Molecule sets an `ANSIBLE_CONFIG` of its own, so no output a test pins goes
through it. `just status` chooses one of its own for its run instead (§7.7). It subclasses the default callback and carries the
default's `DOCUMENTATION` fragments (`default_callback`, `result_format_callback`). Without them the
subclass has no options: every hook fails on its first `get_option`, Ansible only warns, and every task's
result disappears from the screen and the log in a run that still exits 0. `--syntax-check` loads no
callback, so `just lint syntax` would not notice; `tests/unit/test_attention_callback.py` runs real playbooks
through it and compares its untagged output with the default's, byte for byte. It also draws `just deploy`
live (below).

A run waits for the person in three ways, and each is set apart:

- **A prompt** - every `pause`, and destroy's `vars_prompt` - passes its own text through `banner`, titled
  `USER INPUT REQUIRED` or `CONFIRM DELETION`: Ansible prints a prompt as written, before any callback
  sees it. A `vars_prompt` is templated before any host is in scope, with the play's vars and the extra
  vars only, even when `-e` answers it, so destroy's names its VMs from `vm_name` through a play var
  (`claude_vm_confirm_names`); one naming `claude_vm_names` failed every run before it asked.
- **A page to approve**: a debug result tagged `attention_browser` is drawn as a block titled `ACTION
  REQUIRED IN YOUR BROWSER`, with the VM and the sign-in (from the task's role), instead of `ok: [host]
  =>` and a YAML dump. A retried task with the tag - Claude's and Tailscale's browser waits - says once
  per VM that it is waiting, with the time left, instead of a `FAILED - RETRYING` line every few seconds
  that pushed the page off the screen. Six tasks carry the tag, none looped; the device flow's
  `wait_for` has no retries to fold, and carries it for `just deploy`'s live output (below), which tells a
  wait that never retries by how long it runs.
- **What is left after the run**: NEXT STEPS, after PLAY RECAP, from what the report tasks recorded.
  Each of the 18 reports that can say what to do later registers its message and records it straight
  after with `ansible.builtin.set_stats` under its owner's stat - `claude_code_next`,
  `github_projects_next`, `tailscale_next`, `claude_vm_next` (provision and destroy) and `code_next` - as
  `{vm: {matter: line}}`: the message itself while something is left, `''` once nothing is. `aggregate`
  merges the records, so the last one of a VM and matter wins across plays: configure's "not signed in"
  is cleared by the sign-in's own report later in the same `just deploy`. The decision is made in Jinja
  beside the message, from the same facts, because the text cannot carry it: a stopped Tailscale or a
  missing workflow scope names no `just` command, and "just deploy signs it in next" names one with
  nothing to do. A looped report records each VM in a looped `set_stats` keyed by the loop var, so the
  callback reads no task result for this at all. Printed by VM name, the matters in a fixed order (`vm`,
  `claude`, `remote_control`, `github`, `github_identity`, `tailscale`, `vscode`, `destroy`), each line
  once, nothing when nothing is left, and nothing after Ctrl-C, which has no stats event. `profile_tasks`'
  TASKS RECAP follows it: Ansible calls collection callbacks after the stdout one.

The blocks are bright yellow through `Display`, plain under `ANSIBLE_NOCOLOR` or with no terminal; the run
log gets them plain, but never destroy's question, which `vars_prompt` reads with `input()`. The harness
shows custom stats (`ANSIBLE_SHOW_CUSTOM_STATS=1`), so each role's own tests read what it recorded from the
JSON report (`_left_to_do`), in every state they already run.

### `just deploy`, drawn live

`CLAUDE_ON_PROXMOX_LIVE`, which the justfile's `deploy` recipe alone exports, set to what was run (`deploy
--check alpha`, the run's title), hands every event of the run to an engine, `filter_plugins/live_ansible.py`, at
verbosity 0 and with a run log to send the stream to; any `-v`, or no log (`LOG_DIR=`), leaves it the stream above.
The callback then keeps only its classic output, which the engine prints through it too. The engine takes the
variable out of the environment when the playbook starts, so nothing the run starts - the VS Code whose terminals a
later `just destroy` may run in - inherits it. It is `live` when stdout can take the region every live output
shares (§7.8, `can_draw`), and `lines` otherwise. The model is `filter_plugins/run_view.py`, which any playbook run
can be drawn with, and what a run is made of is its recipe's declaration in `filter_plugins/live_recipes.py`, named
by the title's first word: `just deploy` is the first, and §7.8 says how another recipe joins it.

- **Stages and steps.** A play's stage is the first of the declaration's stages that names it: by its file and
  play name (`deploy.yml:Fail each VM whose work after a sign-in failed`, deploy's `finish`), else by its file
  (`play.get_path()`): template, provision, configure, `SSH aliases`, the three sign-in playbooks as one stage
  `sign in`, VS Code. A play the declaration does not name - `discover.yml`, which configure and each sign-in
  import, and `deploy.yml`'s first - takes the stage of the play after it, else of the one before, and a test
  holds the declaration to `deploy.yml`'s imports and `--only`'s playbooks. A task's step is its role; in the
  sign-in stage, the sign-in, for every play of its file. A quiet stage - `finish`, deploy's last play, which only
  raises again what failed after the sign-ins - is counted but never drawn, unless one of its tasks fails; a step
  whose every task was skipped reads `–`, never `✓`. A run of one stage (`--only configure`) is drawn as that
  stage's steps, with no row of its own: the footer says `step 2 of 6`, the summary's table is `STEPS`.
- **How far a step is.** When the playbook starts, `play.compile()` of each play gives its static tasks with
  the very `_uuid`s they start with, blocks' rescues and always included, and only those `--tags` and
  `--skip-tags` leave (`evaluate_tags`, as the play's iterator filters them). An include whose file is literal -
  `include_tasks` of a named file, `include_role` of a named role - is counted then too, from its file
  (`count_tasks`: a block's children, a static import's file, an include as one), and that count is kept against
  the include's ID: when the include announces itself (`v2_playbook_on_include`) it uses that count up rather than
  adding to it. Any other include adds its file's count then. A serial play - the sign-ins, one VM at a time - runs
  every task again for each batch, under the same IDs: once it starts, its counts are multiplied by its batches,
  cut as Ansible cuts them (`run_view.batches`). A task beyond them counts as reached. A step that has ended is
  whole, in its row and in its stage's: a rescue that never ran was never left to do. No bar and no percentage in
  the title ever falls: each is held at the highest it has shown. A step's time is its own tasks' time added up,
  never its first task to its last: a role's handler runs at the end of the play, after every other role.
- **Where output goes.** The region wraps `Display.display`: each call erases the region, prints, and draws
  it again, every call under `Display`'s lock, so every write to the log takes the locks in one order. A
  route keeps the run's own words and anything on stderr - warnings, deprecations, errors - on the screen and
  sends the rest, every line the default callback and `profile_tasks` print, to the log alone (`log_only`).
  The run's own words print in colour to the screen and plain to the log, so the log is every line the stream
  would have logged, in order, with those added (`test_deploy_live.py` runs a stand-in deploy both ways and
  compares). They are the plan - the first play's report - and every report after it, whole: each of its lines
  kept as a line, with its indent, wrapped to the width under that indent and never cut, its VM first unless a
  play on this machine names it; the browser blocks and `--check`'s diffs; and each failure, headed by its step
  (or its stage) and its VM - `✗ common · alpha: Install baseline packages` - with its message, the last 20 lines
  of its output - a crashed module's own - and for a loop, what each item that failed said. A task's name is
  drawn without its role's prefix, which its step says, and without its file's, such as `session_read |`. A failure
  among a block's own tasks, where the block has a `rescue`, is the block's to handle: dim in the window, nothing
  printed. That holds at any depth and through any import or include: a static import puts its file's block
  under a `TaskInclude` that no block lists, so every link between the task and the block is looked at, not only
  the one under it. One in a `rescue` or an `always` is the run's, unless a block further out has it among its
  own, and a rescue the play's `--tags` leave no task of takes nothing, as Ansible then fails the host.
- **A failure raised again.** A task the declaration names under `raises` - deploy's and configure's last plays'
  `fail` of `claude_vm_configure_failures` - repeats failures rescues took earlier, which the engine keeps per
  host with the names of the blocks around them. It is drawn under each cause the declaration's blocks name, by
  the cause's step - `✗ tailscale · alpha: Join the tailnet` and what it said - followed by the raise's own words
  but those that only repeat a cause's; the cause's step counts the failure, and the raise is `↑` in the window.
  A failing task the declaration does not name is an ordinary failure.
- **What a VM may set.** Only `proxmox_vm`'s tasks give the summary a VM's ID and address: a task on the VM
  could set any fact. The ID is a number or nothing, and the address goes through `plain_text`, as everything
  else a VM said does. The VMs the run was given come from its `vm_name` extra var, each held to a VM name's
  pattern.
- **The screen.** The region (§7.8) draws the frame, which `run_view` folds to the height before the region
  cuts it, and steps aside for every prompt. The running step's last five tasks show (the declaration's
  `window`), never the check of an included role's arguments, and a `meta` task Ansible starts once per host once.
  A run of two or more VMs says each VM's state on each step's row - `alpha ✓ · beta ⠴` - and the VM on each task
  line. The tab's title says where the run is, and the bell rings once per wait and at the end. A wait is a task
  tagged `attention_browser` that is not a block's `debug`: from its first retry, or once it has run two seconds
  (`WAIT_AFTER`) - a wait that never retries, GitHub's device flow, is known by that, and a skipped one is over
  first. Its time is counted as yours, and so is a prompt's: the region tells the engine when a prompt takes the
  screen and when it is answered (`on_prompt`), the title says `waiting for you`, and the bell rings for a
  prompt only when nothing has asked for the person for ten seconds. A retried task that waits for a machine -
  the guest agent's address - says its try and its limit on its line (`try 3 of 37 · up to 2:55`).
- **The end.** At the stats event the region is erased for good, PLAY RECAP goes to the log, and the summary
  (`Run.summary`) and NEXT STEPS print, the title `done`, or `failed` when a VM failed. The summary's head is
  one of three: `done in 6 min 12 s`; `done in 6 min 12 s · 1 VM failed: beta`, when the run started its last
  play and only some of its two or more VMs failed; or `failed after 2 min 4 s at common`, naming where the first
  failure was drawn. The summary gives each VM a line per report, cut to the width, the report being whole above:
  one of several lines, or longer than a line, by its first sentence; a report run again in a later play - the
  repositories, configure's and then the sign-in's - by its last run; none on a matter a later report recorded
  again (each report's NEXT STEPS record, the `set_stats` task straight after it, names its matters:
  configure's "not signed in", which records nothing left under `just deploy` since the sign-in follows, goes
  once the sign-in's report on that matter comes), nor one whose line a later record replaced, nor one NEXT
  STEPS says whole; and a play on this machine's under the one VM it names. SLOWEST lists only tasks of a second
  or more, never a person's time. NEXT STEPS is wrapped to its block's width with every word whole, so a command
  or a URL is never split. Ctrl-C has no stats event: the region's `atexit` erases it and the title says
  `stopped`. Without a terminal there is no region: a line as each step ends (`Run.ended_lines`), the reports,
  and the summary.

No test or scenario sets the variable - the harness passes on only `PASSED_ON`, and `test_deploy_live.py`
fails if a tracked file other than the callback, the engine, the declarations, the justfile's `deploy` recipe,
the live tests' kit and `test_justfile.py`, whose `ansible-playbook` only records, names it - so nothing a test
pins is drawn.

## 7.6 `filter_plugins/sessions.py` and `playbooks/library/` — the session handoff

What `just code` carries of this machine's Claude Code sessions to a VM
([§4.9](lifecycle.md#49-playbookscodeyml), **The session handoff**), and what `just destroy` brings
back of a VM's own before deleting it ([§4.5](lifecycle.md#45-playbooksdestroyyml--confirm-release-delete)).
Four modules, each in `playbooks/library/` beside the playbook that runs them, read and write the
files; the filters decide from what they read, and touch no file.

**`claude_sessions_info`** — the transcripts under `<dir>/projects/<folder>/<id>.jsonl` of a Claude
Code configuration directory, written in the last `days`, named in `ids`, or with `all` every one: per
session its folder and time, its first `cwd`, the newest `version`, the last `gitBranch`, whether
it holds a user or assistant message, whether it has `tool-results/` or any folder beside it, and the
pid of a live `claude` that `sessions/` says has it open - one whose command line, from `/proc` or `ps`,
is still claude's, never a pid another program took since its entry was left; and that registry
whole, whatever the transcripts (`registered`): the VM's, for which copies a `claude` there has open.
Every complete line is parsed and only those values kept, so nothing that was said comes back. With
`manifest`, just code's on that machine, whether it was copied there (by ID, in whatever folder it is
now) and whether it is still the copy as written (by digest); with `memory`, the folders whose auto
memory folder holds a file; with `repo_paths`, `githubRepoPaths` from Claude Code's `.claude.json`, and
nothing else of that file; with `claude`, the version of the first of those executables there is - only
those, never `PATH`. A manifest of another format than `claude_sessions_copy` writes reads as none. Run
here and on the VM.

**`git_checkouts_info`** — for each directory asked about, or else each checkout directly inside one,
the checkout's top level and `origin` URL as configured, never as a `url.<base>.insteadOf` rewrites it,
and without what precedes the host, which can be a token; each path and branch as written, never with
its `~` or `$VAR` expanded; with `status`, whether `git status --porcelain --untracked-files=normal
--ignore-submodules=none` prints nothing, whatever the checkout's config would hide, the branch and `HEAD`, and whether a
branch, remote-tracking branch or tag holds `HEAD`'s commit (`head_kept`); with `branch`, that branch's
commit and the remote, branch and commit it tracks, after `git check-ref-format --branch` and a refusal
of a leading `-`, since the name comes from a transcript; with `target`, a full commit ID, the paths git
does not track that a move of the tree to it would replace (`overwrites`: what it adds where something
is, or a file where it has a folder, unless `HEAD` tracks that and the move removes it); with `busy`,
the pids from `/proc` of this user's `claude` processes working inside; with `work`, for `just status`, what
of the checkout's work is on no remote or not committed: its worktrees (`git worktree list --porcelain`,
wherever they are, the Claude worktrees under `.claude/worktrees/` among them), each dirty or not,
untracked files included, and the commits only a detached `HEAD` holds (`rev-list --count HEAD --not
--remotes --branches`); each local branch's commits on no remote (`rev-list --count <branch> --not
--remotes`), which covers a branch with no upstream; and its stashes. Nothing is fetched, so "no remote" is
as of the checkout's last fetch. Every command is `git --no-optional-locks`, which takes no index lock.

**`claude_sessions_copy`** — each session's copy on the VM, from the mirror rsync left: its complete
lines with the four edits and nothing else (the memory folder in `prompt_snapshot` attachments, the
session's `tool-results/` folder in user entries, a cleared `worktree-state` after a bound one, a
`relocated` entry last), 0600 in 0700 folders, with the mirror's time while that is within 14 days and
the time of writing otherwise, renewed at 14 days (Claude Code sweeps a transcript at 30), its
`tool-results/` after it, and never a subagent's transcript. Claude Code's records of the requests it
sent stay as they are: a resume sends the recorded prompt, never them. A copy is written where there is
none, or where it is still what the manifest says the last run wrote; one that is already what would be
written is left, and any other is the VM's and left too, said as continued when nothing new came from
the other machine and as diverged otherwise - and so is one that is gone while its ID is in another
folder of the VM's, where `/cd` moved it (`held` names that folder), unless the copy there is the
module's own, still as written: the session moved on the other machine, and its copy moves with it.
The manifest is written after each copy. Sessions that are no longer asked for leave the
mirror. Check mode writes nothing.

**`claude_sessions_fetch`** — run on the controller, for `just destroy`'s save: `tar -cf - -T -` on the
VM over SSH (`ssh` given as user, host, port, key and Ansible's SSH arguments, with `BatchMode`; none
where the VM is this machine, as in the tests), the names on stdin each after `./`, so that none reads
as an option, and the stream unpacked by Python's `tarfile` into a folder it creates: plain files and
folders only, at the names asked for - `<folder>/<id>.jsonl`, `<folder>/<id>`, `<folder>/memory` -
or inside one of the folders, with no `..`, absolute path or empty component, never through a link
(`O_EXCL | O_NOFOLLOW`, in folders it made), 0600 and 0700, with the archive's times. A link, hard link,
device or pipe is left out and named in `left_out`. It fails on SSH or tar failing, an archive cut short
(a byte count that is no number of 512-byte blocks: `tarfile` reads one cut after its first header as
one that ended) or a name asked for that did not arrive. Nothing of the content is returned.

**`claude_sessions_plan(sessions, here, there, version, repo_paths, dest, wanted, project, vm)`** —
the decision as data: `sessions` to copy, each with the checkout it continues in and the memory folder
its prompt is pointed at; `files` for rsync; `newer`, `ambiguous`, `twice` (an ID in two folders here)
and `open` (one a `claude` on the VM has open, given as `open_there`), one of no repository the VM
has in none; and with
`wanted` (`--session`), its entry and the `problem` that refuses it, or `""`. A session's repository is
its first directory's origin, else its gone worktree's checkout's, else `githubRepoPaths`; the VM's
checkout of it is the one whose GitHub origin names it, of two the one named after it.
**`git_repository(url)`** is `owner/repo`, lowercased, of https, ssh and scp-like URLs and local paths
alike, the host not compared: this machine's side, where a clone may reach GitHub by an SSH alias.
**`github_repository(url)`** is the same of a GitHub origin only, and `""` of any other: the VM's side,
whose checkouts just deploy cloned from GitHub, so that a local path or another host's repository there
claims nothing. **`just_code_dir(projects, given)`** is just code's own directory on a VM -
`vscode_workspaces_dir`, else `.claude-on-proxmox` in the projects directory - for code.yml and for
destroy's save, which reads the manifest there. **`claude_project_folder(path)`** is Claude Code's folder name for a path - every character
but an ASCII letter or digit as `-` - refusing one over 200 characters, which Claude Code shortens with
a hash of its own. **`claude_session_dirs(sessions)`** is the directories to ask git about for them:
each first directory, and the checkout of a Claude worktree's, which a gone worktree leaves behind.
**`claude_sessions_listed(rows)`** is just code's words for the sessions it left out, each
`[before, ids, after]` that has IDs, each ID by its first eight characters as `/resume` lists them.

**`claude_sessions_saved(sessions, memory, dest)`** — what of a VM's sessions `just destroy` saves into
`dest`, from `claude_sessions_info`'s reading of the VM with its manifest: every session that holds a
message and is not a copy still as just code wrote it, with the folder beside it, and every auto memory
folder. A folder not named as Claude Code names one or an ID that is not a session's - the VM's to make
up - is left out and counted in `refused`, never handed to tar. Returns `files` for
`claude_sessions_fetch`; `sessions` for `sessions.json`, each with its first directory, branch, last
write, the version that wrote it (its digits alone), whether it began on the VM, and the `claude --resume
<path>` that continues it, the path quoted; `newest`, the newest version, which resuming them needs at
least; `started` and `continued`; and the `memory` folders.

Covered by `tests/unit/test_session_handoff.py`, with real git checkouts and canned transcripts, by
the window step's runs in `tests/unit/test_code_session.py`, and by destroy's release play, run in
`tests/unit/test_release.py`.

## 7.7 `filter_plugins/status.py` and `callback_plugins/claude_on_proxmox_status.py` — just status

**`status_fleet(listing, tagged, requested, ...)`** — the fleet `playbooks/status.yml`'s first play
found, as data: this project's VMs with what the listing, their config and their agent said, each marked
`read` or with why it is not (`node`, `nameless`, `duplicate`, `name`); the other VMs as totals only; the
storages with this project's share of each (`status_storages` picks them); the template; the aliases in
the managed SSH file (`status_aliases`); the names given that match no VM; and the settings the report
needs. A VM is dated by its disk's `ctime` in the storage's content: a clone's config carries the
template's meta `ctime`, as a live Proxmox VE 9.2 showed. **`status_vm_api(results, now, since)`** — one
VM's own GETs as the report keeps them: its state, a day of CPU in twelve buckets (Proxmox VE 9 keeps a
point a minute, 8 one every twenty) and the last hour's CPU pressure (9 only), each from `since`, when the
VM was made, on - Proxmox keeps a VMID's history, and an ID used again carries the deleted VM's - the root
filesystem from the agent, the OS, and the settings waiting for a restart. `status_report` takes memory in
use from the VM's own `/proc/meminfo` where it was read over SSH: Proxmox's figure counts the page cache as
used, twice the VM's own on a live VM.
**`status_system_read(result)`** — what `vm_status_info` read of a VM, its fields alone, or none when it
was not read. **`status_work_read(result, directory)`** — what `git_checkouts_info`'s `work` read of a VM's
projects directory, per checkout: its worktrees, its commits and branches on no remote, the commits on a
detached `HEAD`, the worktrees with changes not committed and its stashes; WORK says them per checkout and
never "clean", since a checkout outside the directory, and not a worktree of one in it, is not seen. **`status_refusal(message)`** — which refusal ssh's message is, `changed` (a host key other
than the one held here), `unknown` (none held), `timeout`, `refused` or `denied`, read from the whole
message before it is cut: ssh's banner for a changed key runs past the 120 characters a value keeps.
**`status_clean(value)`** — every string in a value `one_line` (§7.4), the roles' lines to 400
characters and every other field to 120.

**`status_report(found, read_at, now, live=False)`** — the report's model from what every host found:
the VMs, the storages, the template, what each VM said over SSH, the attention items ranked "not read",
problem, to do, and the exit code, 2, 1 or 0, from the highest. A role's own line is an item of that
role's (`source`), drawn after its name as NEXT STEPS draws it. With `live` a value not read yet is pending, and nothing is ranked: the
model of a frame the callback draws while the run goes on. **`status_text(model, color, reading, glyph,
cols)`** — the report as text, every line within 100 columns, or within `cols` for a live frame, where
`glyph` marks each pending value. The same functions draw the frames and the finished report.

**The callback** is the stdout callback `just status` chooses (`ANSIBLE_STDOUT_CALLBACK`, and
`ANSIBLE_CALLBACKS_ENABLED` naming it alone, so `ansible.cfg`'s `profile_tasks` prints nothing into the
report). It loads `status.py` by path, keeps each `status_found` fact as it lands, and draws the report
live in the region every live output shares (§7.8), redrawn as each fact lands, the lines its frame leaves
out said to be "shown when the run ends"; `status.py` draws its lines with `screen.py`, as the live runs do.
The region is taken when the callback is made, so a warning before the first frame is drawn above it, and
spins from the playbook's start. When the report fact arrives, the region is erased and the finished report
printed through `Display`, so the log has it and nothing else - in colour on the screen, plain in the log,
since `Display` logs the text it is given; under `--json` (`STATUS_QUIET`) nothing is
printed. It shows each task's warnings, which since ansible-core 2.19 travel on the result and which a
stdout callback of one's own otherwise drops, and a task's failure on stderr only when the run ends
without a report.

**`vm_status_info`** (`playbooks/library/`), the one module status runs on a VM, as the VM user and
never through sudo: the OS and the running and newest kernels, the reboot flag and the packages that set
it, the load, memory and uptime, the upgrades apt's simulation (`apt-get -s`, which takes no lock and
refreshes nothing) would make and how many are security ones, and the user's own claude processes and VS
Code extension hosts, one per window. Every read is fixed in its code, runs in a thread of its own with
its own time limit - one that runs out is said by name and the rest still land - and the one program it
runs, `/usr/bin/apt-get`, by its absolute path: the VM user's PATH over SSH starts with directories the user
owns. Its `root` option reads a fixture tree in the tests.

Covered by `tests/unit/test_status.py` (the filters, a hostile corpus, the 100-column limit, the callback
driven in process with a stand-in terminal, and the playbook against the fake Proxmox) and
`tests/unit/test_status_live.py` (the real playbook at a pseudo-terminal: a window it fits, a short one, a
narrow one, a resize each way, Ctrl-C, a dumb terminal, a pipe, and a warning between frames), and
`tests/unit/test_status_vms.py` (the module on fixture trees, the reads inside stand-in VMs and that they
change nothing there, and the host key's options and refusals), and `tests/unit/test_status_work.py`
(`git_checkouts_info`'s `work` over real throwaway repositories, which it leaves as they were).

## 7.8 `filter_plugins/live_region.py` and `filter_plugins/screen.py` — every live output

`just status`'s report (§7.7) and every live run (§7.5) are drawn the same way, with the same code, so each
new live output adds only what it shows. Two modules hold what they share, and `filter_plugins/text.py`'s
`plain_text` and `one_line` (§7.4) clean everything a VM said before any of it is kept.

**`live_region.py`** — `LiveRegion(display, frame, route, more)`, the region at the bottom of a terminal
that a stdout callback hands a frame function, `frame(cols, rows, glyph)`, returning its lines already cut to
the width. `can_draw()` says whether stdout can take one: a terminal, `TERM` not `dumb`, UTF-8 for the
glyphs. `start()` takes the terminal, from the main thread, which alone may set a signal handler; `redraw()`
draws; `spin()` redraws ten times a second for the spinner, under `Display`'s lock; `finish()` erases it for
good, and runs at exit, Ctrl-C included.

- **Drawing.** One write per redraw, inside synchronized-output marks (`CSI ?2026`), the cursor moved up
  exactly the lines drawn and nothing else, so no line may wrap: a frame taller than the screen keeps its
  top and its last line, with `more` saying how many it leaves out. A screen under 4 rows or 20 columns
  gets no region, since one as tall as the screen would scroll each redraw. The cursor is never hidden.
- **Everything else above it.** It wraps `Display.display`: each call erases the region, prints, and draws
  it again, under `Display`'s lock, so every write to the log takes the locks in one order. A route, given
  the call's keyword arguments, may send it to the log alone (`log_only`), which a live run does with the
  stream. A forked worker's display goes over the queue to the process that started the region, which alone
  draws.
- **Prompts.** `Display.prompt_until` (a `pause`) prints its prompt through `display` and only then takes
  `Display`'s lock to read the keys (ansible-core 2.21), and a `vars_prompt` reads with `input()`
  (`Display.do_var_prompt`). Both are wrapped: the region erases and pauses itself before the prompt and draws
  again once it returns, and the spinner draws nothing meanwhile.
- **The terminal.** Its echo is off while the region is up, and on for a prompt and at the end: an Enter
  typed during a run would move the cursor under the region, and the next redraw would leave a copy of it
  behind. A resize (`SIGWINCH`) pushes the screen into the scrollback and draws afresh, since no count of
  what was drawn holds once a frame meant for the old size has wrapped. `title()` sets the tab's title (OSC
  0) when it changes, and `bell()` rings.

**`Output`**, beside it, is what every live output does around its region, whatever it draws: whether to draw
(`can_draw()`, unless the caller decided), whether to colour (the caller's `ANSIBLE_NOCOLOR`), the region, and the
output's own words printed coloured to the screen and plain to the log (`print`). With `to_log`, everything
`Display` prints goes to the log alone - the stream a drawn run replaces - but what is printed inside `say()` and
anything on stderr. The live run's engine and `just status`'s callback both draw through one, so neither keeps a
copy of that logic.

**`screen.py`** — a line is a list of `(text, style)` segments, the style one of `PALETTE`'s: headings,
bold, dim, and the colours of done, to do, failed, running and waiting. `cut` (to a width, ending in a dim
"…"), `pad`, `right` (a tail aligned to the edge, dropped before it would crowd the line), `render` (in the
palette or plain, `PENDING` drawn as the spinner's glyph), `bar`, `spark`, `fill` (wrapped, never cut),
`duration` and `clock`, and `plural`. A line is measured in characters, one each: a terminal set to draw
East Asian ambiguous-width characters double-wide (`█`, `●`, `·`) wraps a frame drawn with them.

**Loading.** A filter plugin is no module another can import, and a callback cannot ask for one by name, so
each module that needs another loads it by path, once, as `claude_on_proxmox_<stem>` (`_sibling`): the
callbacks, `status.py`, `run_view.py` and `live_ansible.py` share one copy of each.

**Another recipe, drawn live.** A recipe whose playbook is a run - `just login`, `just destroy`, `just code`,
`just init` - joins `just deploy` without code of its own: its recipe exports `CLAUDE_ON_PROXMOX_LIVE` with its
title, as `deploy`'s does; its declaration in `filter_plugins/live_recipes.py`, named by the title's first word,
names its playbooks' stages (the sign-in playbooks already are deploy's) and the playbook whose first play reports
what the run will do; its reports, waits and NEXT STEPS records follow §7.5's conventions, which the engine already
reads. A command that is a report rather than a run, as `just status` is, keeps a model and a frame function of
its own and hands the frame to an `Output`, drawing its lines with `screen.py`. Either way its test runs it through
`tests/unit/live_kit.py`: at a pseudo-terminal of its own (`harness._at_a_pseudo_terminal`) with a `HOME` of its
own, read back through `vt.py`, or piped, with the checks every drawn run must pass (`Ran`); and
`test_deploy_live.py`'s guard names each tracked file that may name the variable.

Covered by `tests/unit/test_live_region.py` (the region against a stand-in `Display`, whose `display`,
`prompt_until`, `do_var_prompt` and lock it holds to ansible-core's), `tests/unit/test_screen.py`,
`tests/unit/test_vt.py` (the small terminal, a stream cut anywhere), and each output's own live tests.
