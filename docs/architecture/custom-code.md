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
`just list` need: given what `proxmox_vm_info` registered for one VM read with `config` and
`network`, the IPv4 the agent reports on the interface whose MAC is `config[nic]`. Returns `""` —
never raises — for a failed or skipped call, a stopped VM, a silent agent or a missing NIC, so a
caller sweeping the fleet skips that VM. Both playbooks pass `proxmox_nic`; they used to read
`config.net0` inline, which ignored the NIC the role honours.

**`proxmox_access_denied(msg)`** — whether a community.proxmox error means the token was refused:
`40[13]` only in proxmoxer's status position (`"<status> <reason>: <detail>"`, after the module's own
`"...: "` prefix), or the words Proxmox uses for it. The unanchored `40[13]` it replaces also matched
a VMID in the detail — `500 Internal Server Error: VM 4013 is not running` — which failed
`just list` and `just deploy --only configure` for the whole fleet over one stopped VM.

**`ssh_config_blocks(content)`** — the alias blocks in the managed file, as `{name}`, in file order. **`ssh_config_prune(blocks, resources,
tag_pattern)`** — the decision `just deploy --only ssh-config` makes fleet-wide, as data: `prune` (no VM of that
name anywhere), `untagged` (a VM of that name exists without the tag; left alone), and `nameless`
(tagged VMs the listing shows without a name, a node that is down), which when non-empty empties
`prune` into `held`. Pure, so every rule is a unit test rather than a fake-API fixture.

Covered by 58 unit tests in `tests/unit/test_filters.py`.

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
through it. It subclasses the default callback and carries the
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
--check alpha`, the run's title), turns the callback's live mode on at verbosity 0, with a run log to send the
stream to; any `-v`, or no log (`LOG_DIR=`), leaves it the stream above. The callback takes the variable out of
its environment when the playbook starts, so nothing the run starts - the VS Code whose terminals a later
`just destroy` may run in - inherits it. It is `live` when stdout can take a region (`can_draw`: a terminal,
`TERM` not `dumb`, UTF-8) and `lines` otherwise. The model is `filter_plugins/deploy_view.py` and the region
`filter_plugins/live_region.py`, loaded by path like `text.py`: a module in `callback_plugins/` that is no
callback is warned about on every run, so each has an empty `FilterModule` instead.

- **Stages and steps.** A play's stage comes from its playbook's file (`play.get_path()`, `STAGE_FILES`):
  template, provision, configure, `SSH aliases`, the three sign-in playbooks as one stage `sign in`, VS Code.
  A play from a file the table does not name - `discover.yml`, which configure and each sign-in import, and
  `deploy.yml`'s own - takes the stage of the play after it, else of the one before, and a test holds the
  table to `deploy.yml`'s imports and `--only`'s playbooks. A task's step is its role; in the sign-in stage,
  the sign-in, for every play of its file.
- **How far a step is.** When the playbook starts, `play.compile()` of each play gives its static tasks with
  the very `_uuid`s they start with, blocks' rescues and always included, and only those `--tags` and
  `--skip-tags` leave (`evaluate_tags`, as the play's iterator filters them); each include adds its file's
  count when it announces itself (`v2_playbook_on_include`, `count_tasks`: a block's children, a static
  import's file, an include as one); a task beyond them counts as reached. A step that has ended is whole, in
  its row and in its stage's: a rescue that never ran was never left to do. A step's time is its own tasks'
  time added up, never its first task to its last: a role's handler runs at the end of the play, after every
  other role.
- **Where output goes.** The region wraps `Display.display`: each call erases the region, prints, and draws
  it again, every call under `Display`'s lock, so every write to the log takes the locks in one order. A
  route keeps the run's own words and anything on stderr - warnings, deprecations, errors - on the screen and
  sends the rest, every line the default callback and `profile_tasks` print, to the log alone (`log_only`).
  The run's own words print in colour to the screen and plain to the log, so the log is every line the stream
  would have logged, in order, with those added (`test_deploy_live.py` runs a stand-in deploy both ways and
  compares). They are the plan - the first play's report - and every report after it, whole: wrapped to the
  width and never cut, its VM first unless a play on this machine names it; the browser blocks and
  `--check`'s diffs; and each failure: the task, the VM, its message, the last 20 lines of its output - a
  crashed module's own - and for a loop, what each item that failed said. A failure among a block's own tasks,
  where the block has a `rescue`, is the block's to handle: dim in the window, nothing printed. One in a
  `rescue` or an `always` is the run's, unless a block further out has it among its own.
- **What a VM may set.** Only `proxmox_vm`'s tasks give the summary a VM's ID and address: a task on the VM
  could set any fact. The ID is a number or nothing, and the address goes through `plain_text`, as everything
  else a VM said does.
- **Prompts.** `Display.prompt_until` prints its prompt through `display` and only then takes `Display`'s
  lock to read the keys (ansible-core 2.21; a worker's prompt is sent to the main process, which prints it),
  and a `vars_prompt` reads with `input()` (`Display.do_var_prompt`). The region wraps both: it erases and
  pauses itself before the prompt and draws again once it returns; the spinner, a thread redrawing ten times
  a second under the same lock, draws nothing meanwhile. Only the process that started the region draws.
- **The rest of the screen.** One write per redraw, inside synchronized output (`CSI ?2026`), the cursor
  moved up exactly the lines drawn, each line cut to the width and the frame folded, then cut, to the height:
  no line wraps, which `vt.py` counts. A screen under 4 rows or 20 columns gets no region. The terminal's echo
  is off while the region is up, and on for a prompt and at the end: an Enter typed during the run would
  move the cursor under the region, and the next redraw would leave a copy of it behind. A resize pushes the
  screen into the scrollback and draws again. The tab's title (OSC 0) says where the run is, and the bell
  rings once per wait and at the end. A wait is a task tagged `attention_browser` that is not a block's
  `debug`: from its first retry, or once it has run two seconds (`WAIT_AFTER`) - a wait that never retries,
  GitHub's device flow, is known by that, and a skipped one is over first. Its time is counted as yours.
- **The end.** At the stats event the region is erased for good, PLAY RECAP goes to the log, and the summary
  (`Run.summary`) and NEXT STEPS print, the title `done`, or `failed` when a VM failed. The summary gives each
  VM a line per report, cut to the width, the report being whole above: a report run again in a later play -
  the repositories, configure's and then the sign-in's - by its last run, and none whose NEXT STEPS line a
  later record replaced (configure's "not signed in", once the sign-in's report clears it); a play on this
  machine's under the one VM it names. Ctrl-C has no stats event: the region's `atexit` erases it and the
  title says `stopped`. Without a terminal there is no region: a line as each step ends
  (`Run.ended_lines`), the reports, and the summary.

No test or scenario sets the variable - the harness passes on only `PASSED_ON`, and `test_deploy_live.py`
fails if a tracked file other than the callback, the justfile's `deploy` recipe and `test_justfile.py`, whose
`ansible-playbook` only records, names it - so nothing a test pins is drawn. Known limits: a line is measured
in characters, and a terminal set to draw East Asian ambiguous-width characters double (`█`, `●`, `·`) wraps
the frame; and `deploy.yml`'s last play, which raises what failed after the sign-ins, takes VS Code's stage.

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
the pids from `/proc` of this user's `claude` processes working inside. Every command is
`git --no-optional-locks`, which takes no index lock.

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
