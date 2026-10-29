# CLAUDE.md

Ansible that creates Ubuntu VMs on a Proxmox VE host and configures them as Claude Code
development boxes. The wiki in `docs/` is the documentation - the guides are the user manual,
`docs/architecture/` the design, numbered by section - `README.md` is the short getting-started
page, and `CONTRIBUTING.md` has the setup and conventions. This file is the part that is easy to
get wrong.

## Always run tooling through the project's Python environment

Every tool is pinned in `pyproject.toml`, locked with all it pulls in in `uv.lock`, and `just install`
puts it into the user's own virtualenv for the project - the one `python3` resolves to here or `PYTHON=`
names, made with whatever tool they like. The project keeps no environment of its own, never assumes a
`.venv`, and never assumes pyenv: the README suggests it, but no code, message or test may call it or
depend on it. Use `just`, whose every recipe asks `tools/env-run --bin` for that environment's `bin`
(refused, with how to make one, for a Python that is no virtualenv's), or `tools/env-run <tool>`, which
finds it the same way; the git hooks and the Vagrantfile use that. Never a system-wide `ansible`,
`ansible-lint` or `molecule`: a non-virtualenv Python's scripts directory is `/usr/local/bin` and the
like, which is why `BIN` is never taken from one. uv and just are the two tools on `PATH` by design, both
the user's to install: uv only installs into that environment (`just install`: `uv sync --locked
--inexact`, the locked versions in and nothing else of the user's removed) and writes the lock (`just
install --lock`, after editing a pin; never edit `uv.lock` by hand); just is 1.59.0 or later, the version
the justfile's `set minimum-version` and `just_pin` and CI's `.github/actions/setup` pin
(`test_justfile.py` holds the three to one), and CI's comes from upstream's release, checked by SHA256.

Molecule resolves `ansible-playbook` from `PATH` and only *appends* the virtualenv, so it must be
**prepended** or a different ansible-core is silently used. `just test` already does this;
a hand-rolled `molecule test` will not.

```
just install                         # sync your virtualenv to uv.lock (uv sync --locked) + the Galaxy collections; --lock after editing a pin
just init                            # once: just install + the git hook + the local config, asking for what a first deploy needs
just vault                           # encrypt the vault while it is plaintext, edit it once it is encrypted
just lint                            # uv lock --check, yamllint --strict, ansible-lint (production), ruff, just --fmt --check, --syntax-check, the other pre-commit hooks
just lint syntax                     # some of them: lock, yaml, ansible, python, just, syntax, hooks
just test                            # lint, then every test: unit and every Molecule scenario — the CI gate
just test unit                       # pytest over tests/unit/, a worker per CPU (pytest-xdist loadgroup)
just test unit --shard 2/3           # one of the three shards CI runs (tests/unit/durations.json balances them)
just test --role proxmox_vm          # one role scenario (role: every role; molecule: every scenario)
just test provision                  # one playbook scenario (integration is the configure scenario)
just deploy                          # template (if missing) + provision + configure + the three sign-ins + VS Code, all idempotent (just deploy alpha: that VM)
just deploy alpha --only configure   # one stage of it: template, provision, configure or ssh-config (fleet-wide also prunes); --check is the dry run
just deploy alpha --tags claude_code # re-run one role, e.g. to push an API key (--tags beside names means --only configure)
just destroy alpha                   # save the VM's own Claude sessions here, release it from Claude, GitHub and Tailscale, then delete it
just login alpha                     # Claude, GitHub, Tailscale in deploy's order, one VM after another (--only claude, github or tailscale: one)
just status                          # read-only, live: every VM, what needs doing (--only vms,storage,access,sign-ins,system,work; --json; exit 0/1/2, 3 failed); just list too
just settings tailscale              # a role's variables, each value and where it was set (a variable; --all; --changed; --json; none: usage)
just settings vm_cores --value 4     # set one setting in local.yml, held to its type; never a secret (just vault)
just settings vm_cores --reset       # back to its default: its line out of local.yml, its example's line back
just code alpha [--project repo] [--session <id>]   # refresh one alias, copy recent Claude sessions, open the VM in VS Code with a Claude terminal
just logs                            # the run logs, newest first: read these instead of asking for terminal output
just docs                            # regenerate the wiki's reference pages; serve previews the site, build builds it strictly
```

`just` lists every recipe, `just help <recipe>` one's options; each option's `[arg(..., help=)]` and each
recipe's `[doc()]` are that text, for the terminal and the wiki (`docs/reference/commands.md`, from `just
--json`). Options go after the recipe's name; settings (`ANSIBLE_ARGS`, `LOG_DIR`, `PYTHON`) are exported or
go before it (`just ANSIBLE_ARGS=-vvv deploy alpha`).

**How every recipe runs** (the justfile's header): a bash script with no dependencies - `JUST_NO_DEPS` or
`--no-deps` would skip one - whose first line is the checkout guard (`tools/check checkout`: just looks for a
justfile upward, so started in a make-era worktree under `.claude/worktrees/` it would run main's, against
main's inventory and vault), then its own rules with their messages, then what it needs (`tools/env-run
--bin`, `tools/check vault`, `tools/check collections`), then the work, its last command `exec`'d - but in a
recipe drawn in parts (`lint`, `init`, `test`), which exports its title and its state's path (`live_state`, the recipe's
name and the shell's PID), sets `trap 'rc=$?; live_end "$rc"; exit "$rc"' EXIT` - an end that fails changes no exit
status - only when `live_run` is empty, and runs each command as `{{ live }} run <stage> --step <label> -- …`
(lint's checks, test's parts) or each playbook as it is, joining the run through the exported title and state
(init's two), so the trap still runs to say the run's end. No
assignment starts a program: just evaluates what a recipe uses before any line runs, so it would start
before a refusal. Bash, not sh: Ubuntu's dash dies of a Ctrl-C that the playbook it runs survived
(Ansible's `pause`, Ctrl-C then C), and `[continue]` needs the script to live on; and only what macOS's
bash 3.2 has - no arrays, since `"${a[@]}"` of an empty one is fatal there under `-u`, which every script
has, so a variable that may be unset is read as `${VAR:-}`. A recipe that includes another's work runs it
as `{{ self }} <recipe>`, which passes on every setting (a recursive just begins without the command line's
`BIN=`), and, to make it a stage of its own run, `{{ self }} live_run=<its name> <recipe>`. `test_justfile.py` runs every recipe against stand-ins, under `/bin/bash` on macOS.

**A change to behaviour changes the wiki in the same commit.** The page for it is in `docs/`
(`docs/README.md` maps them; `docs/architecture/` for the design, cited by section number). The
reference pages are generated from `argument_specs.yml`, `local.yml.example`, `vault.yml.example`,
`inventory/group_vars/all/defaults.yml`, the roles' `defaults/main.yml`, `vars/main.yml` and task files
(rendered by docsible), and the justfile's `[doc]` and `[arg]` help: edit those and run `just docs` (the pre-commit
hook does too), never the generated section; a task change is a docs change. A new
setting goes in an example file and a new role default in `argument_specs.yml`, or the page misses it.
`tests/unit/test_docs.py` fails on a stale or incomplete page, a broken link or anchor, a page that
renders differently on GitHub and on the site, an unlinked cross-page `§`, or a cited `docs/` page or
`§` section that does not exist. Name repository files in backticks there, never by a relative link
out of `docs/`, which breaks on the site. The site's Markdown drops the rest of a page at a `</` in a
code span, so write `</dev/tty` as `< /dev/tty`; it also needs a blank line before a list and four
spaces before a nested one. Only `docs/README.md` and `docs/`'s folders are the wiki: anything else at
the top of `docs/` is a local working document, ignored and refused by the tracked-file guard.

Every recipe that runs Ansible, Molecule included, writes its whole run to
`.logs/<YYYYMMDD-HHMMSS>-<recipe>[-<part>].log` through `ANSIBLE_LOG_PATH` (`tools/run-logged`). When a user
says they ran something, read the newest log for that recipe before asking them to paste anything. Logs name the
Proxmox host and VMs by address, so `.logs/` is git-ignored and blocked by
`tests/check_no_local_files.sh`; a log file is never committed, quoted into a doc, or attached to a
pull request. The file name is built from the date, the recipe and a part its pattern has checked only,
never from a VM name.

Scenarios are slow and disk-hungry. Run the ones your change touches, one at a time, rather than
`just test`, until you are ready to finish.

`common`, `vscode_server`, `dev_tools`, `claude_code`, `github_projects`, `tailscale` and the `configure`
scenario install packages inside the container and need working Docker DNS. `proxmox_vm`, `proxmox_template` and
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
  `claude-on-proxmox` (`claude_vm_tag`). Discovery finds VMs by it, and both deletion and
  provisioning *refuse* an existing VM without it — provisioning also refuses a template. It is
  matched as a whole tag via `claude_vm_tag_pattern`, never as a substring.
- **Idempotence.** Molecule's idempotence stage re-runs converge and fails on any `changed` task.
- **An API key and Remote Control are mutually exclusive.** `ANTHROPIC_API_KEY` outranks the
  claude.ai login in Claude Code's credential precedence, and only that login can establish a
  Remote Control session. Remote Control is on by default and derives what to do from the
  credentials present, so do not add a flag for it — `claude_code_remote_control_wanted` decides
  once, and `roles/claude_code/tasks/main.yml` either includes `remote_control.yml` or removes a
  unit an earlier run installed. Both directions must converge on a VM that was on the other path:
  `settings.yml` removes a key it no longer manages, and eligibility rejects any `apiKeySource` in
  `claude auth status`. See docs/architecture/roles.md §5.5.
- **The GitHub sign-in converges both ways, and the listing uses whatever token the VM holds.**
  `github_projects` signs `gh` in from `github_token` when there is one, otherwise prints the
  device-flow command and uses the token `gh` stored at that login for the listing
  (`github_projects_listing_token`), so private repositories arrive on the run after the login with
  nothing in the vault. The vault token is put over no login but the role's own (or under
  `github_auth_method: token`): a login made by hand stays and the token only lists there, and a git
  identity set by hand is never replaced (`user.name` and `user.email` are each derived only where
  unset). A cleared vault token signs out only the login the role itself made, told
  by the marker `~/.config/claude-on-proxmox/gh-login` on the VM (the token's digest, written
  whenever the login is the vault token's), by account: a token the user pasted into gh by hand
  looks the same, so never decide a logout from the token's prefix alone. Under `auto`, a vault
token gh refuses for a missing scope (a classic token with `repo` alone, as earlier versions asked
for, lacks `read:org`) keeps listing and gh stays signed out, with the scopes to add said
(`github_projects_token_short`): failing it would fail every VM on upgrade, since they share the
token; `github_auth_method: token` still fails. The status JSON carries
  no token; the kind of login comes from `gh auth token` under `no_log`, and a gh older than
  2.81.0, which has no `--json`, is a named failure, never "signed out". gh is made git's
  credential helper by gh's own test for its entry, so a second run is `ok`.
  Every task that renders a token is `no_log`: `tests/unit/test_github_auth.py` holds that statically
  and greps every run, module arguments injected (`ansible_inject_invocation`), for every canned token;
  a message or task var asks `github_projects_token_set`, never the token. Do not key any of this off `github_auth` alone: with it off, a vault token
  still lists, and a private repository is cloned only where git has a credential for it - over ssh
  always, over https with gh signed in and git's helper - and otherwise skipped and named, not cloned
  and failed. See
  docs/architecture/roles.md §5.6.
- **Tests never open the developer's browser, touch their clipboard, or read their terminal.** The
  sign-ins - GitHub's device flow (`roles/github_projects/tasks/device_flow.yml`), Claude's browser
  sign-in (`roles/claude_code/tasks/browser_login.yml`) and Tailscale's web login
  (`roles/tailscale/tasks/web_login.yml`), run by `just login` and `just deploy` - open a page (and GitHub's copies its code) on `localhost`, which in
  the unit harness and every playbook scenario is the developer's machine. `login_desktop` (roles:
  `github_projects_login_desktop`, `claude_code_login_desktop`, `tailscale_login_desktop`, all wired from
  it in `inventory/group_vars/all/defaults.yml`) gates those steps; every scenario and the harnesses set
  it false, and `tests/unit/test_github_auth.py`, `test_claude_login.py` and `test_tailscale.py` fail if
  one stops, if the wiring goes, or if a desktop step runs. Behind that, `tests/unit/harness.py` puts stand-ins for
  `open`, `xdg-open`, `pbcopy`, `wl-copy` and `xclip` first on every run's PATH and points `BROWSER` at
  one: they record the call and fail, and a run that made one fails its test. A run inherits only the
  variables the harness names (`PASSED_ON`), and a stand-in host's modules get its own directory as
  HOME and XDG_CONFIG_HOME, with the git, gh and Claude config variables unset. Every playbook the
  harnesses run gets `/dev/null` for stdin and a process group of its own, since Claude's sign-in
  `pause`s for the code; the tests that type do so at a pseudo-terminal of their own, and
  `test_a_run_never_reads_the_terminal_pytest_runs_at` fails if a run can read pytest's. The delegated tasks are
  `become: false`, because the play's `become` is the VM's passwordless sudo and on the controller it
  is the developer's, which asks for a password; the harnesses run with become off, so the same tests
  hold that statically. Each flow unlinks its output file before starting, never truncating: an
  attempt a failed run left behind would share the file. The sign-in plays run `serial: 1` whenever
  they can prompt (names given, or `just deploy`) and all at once for a bare report, and the flows'
  one-VM guards read `ansible_play_batch` - `ansible_play_hosts` ignores serial. Under serial a failed
  VM is a whole batch failed, which ends the run, so the plays rescue a failure, say it, and raise it
  in a later non-serial play, which `just deploy` skips (`claude_vm_login_in_deploy`): deploy goes on
  past a failed sign-in. `just login` (`playbooks/login.yml`) skips it too
  (`claude_vm_login_raised_later`), so a VM goes on to its next sign-in as under deploy, and raises every
  failure in a last play of its own. `rescue` covers failed tasks only: an unreachable VM is caught by a non-serial
  play before the sign-in, which pings each VM with `ignore_unreachable` and records it as failed. The
  block's condition sits on the task inside it, never on the block: a block's `when` is its rescue's
  too, read again for each task, and the rescue sets the very fact it tests, which skipped the message
  after it. The rescue re-raises `pause`'s abort (Ctrl-C, then A), which ends the run. The work after a sign-in -
  Remote Control's start, the github_projects role - runs in a follow-on play after the last prompt;
  its failure is recorded in `claude_vm_configure_failures` and raised even under `just deploy`, by
  deploy's last play, since it is not a failed sign-in and the VM is not done.
  `tests/unit/test_sign_in_plays.py` runs both playbooks, and deploy's sign-ins as deploy.yml has them,
  against stand-ins, a VM whose ssh exits 255 among them; `claude_login.yml` run alone with such a VM is
  `test_claude_login.py`'s, on its one run of every sign-in case. `just deploy --only configure` never prompts.
- **Claude's sign-in comes back over a forward, or hands the code over a pipe, and nothing decides from a
  terminal probe.** Given `BROWSER`, `claude auth login` runs it with a page that redirects to
  `http://localhost:<port>/callback`, listens on the VM's `127.0.0.1:<port>`, and takes a code from that
  callback or from stdin, whichever comes first (one attempt: both pages share state and challenge).
  `browser_login.yml` gives it a `BROWSER` that only records the page, forwards that port from the
  controller with `ssh -f -L localhost:<port>:127.0.0.1:<port>` (`ExitOnForwardFailure`, the far end a
  `sleep` whose pid the VM records, killed when the sign-in ends, only while it is a sleep), opens the page
  here only when it matches `claude_code_login_page_pattern`, and waits on the async job
  (`claude_code_login_approval_timeout`) - no prompt. `claude_code_login_redirect` follows
  `login_desktop`; a forward that fails, a page that is not Claude's, a code given, no browser on this
  machine to open the page (`page_opener` finds none), or `just` itself run over SSH (`SSH_CONNECTION`: the
  page would send another machine's browser to its own localhost) falls back to the paste. Tests reach no ssh: `claude_code_login_ssh` is a stand-in, passed as an extra var, since an
  internal in `vars/main.yml` outranks a host var. `claude auth login` with no terminal prints the page
  and reads one line, `<code>#<state>`: a wrong code exits
  1, a right one writes `~/.claude/.credentials.json` and exits 0, and a line without `#` prints
  `Invalid code` and waits, so only the pasted code is ever written and an attempt nobody completes is
  ended by its recorded pid (`$$` is claude's because the shell exec's into it; never a subshell). The
  code comes from `pause` under `no_log` or `claude_code_login_code`, travels on stdin, and is never
  logged; the pipe is opened read-write, which never blocks, by a writer that refuses anything at that
  path but a pipe (`<>` would create a plain file). Before the hand-over `async_status` checks that the
  attempt still runs; the wait ends on any answer after claude's prompt; `Invalid code` (a code copied
  up to its `#`) is asked for once more; a sign-in of the same VM still waiting is refused by its
  pidfile; and what claude printed reaches a message only through the `plain_text` filter, which
  removes whole escape sequences and then every control character but tab and newline (gh's text and
  `just code`'s SSH error go through it too). A `/dev/tty` probe cannot tell an
  attended run - Ansible runs lookups in workers detached from the terminal - so `pause` returning
  nothing is the skip. `just deploy` decides GitHub's fifteen-minute device flow and Tailscale's web
  login VM by VM, from facts on each VM: a Claude sign-in nobody answered (`claude_code_login_skipped`:
  Enter, or a browser that never came back) leaves it. With `github_login_auto_start` /
  `tailscale_login_auto_start` (default true) every other VM starts at once; off, each is asked by
  `*_login_ask` - Enter signs in one whose Claude sign-in was answered (`claude_code_login_answered`) and
  `n` leaves it, while one that showed no Claude sign-in (signed in already, an API key) signs in on `y`
  only - never one fact for the whole run, which made one answer start every VM's flow. A run with no
  terminal gets `deploy_sign_in: false` from the justfile (`no_tty_args`), since sign-ins that start by
  themselves would otherwise wait for a browser nobody watches. The path unit that starts Remote
  Control when a login lands is `PathChanged=`, never `PathExists=`, which restarts a stopped service
  instantly, and exists only while the login is the one thing missing (docs/architecture/roles.md §5.5).
- **Tests never touch a real Proxmox, GitHub, Tailscale or hypervisor.** `tests/molecule/` holds the
  fakes: a stateful PVE API with a guest agent, a stateful `qm`, a `virt-customize`, a GitHub API over
  local bare repos (the authenticated endpoints included), a stand-in Messages API on loopback for the
  real `claude` the `configure` scenario resumes sessions with (`fake_anthropic_api.py`, which records
  what each request sent), a stand-in `gh` installed ahead of the real
  one on PATH, and a stand-in `tailscale` (`fake_tailscale.sh`, Tailscale's own output as research and a
  Headscale run recorded it). Extend a fake rather than reaching for the network.
- **Tailscale's auth key joins a VM once, and the role touches only the Tailscale it installed.** The key
  is used from a settled `NeedsLogin` with no `HaveNodeKey` only - a node that never joined, or one logged
  out by hand, which reads the same - and never sent to a node that has a login: one Tailscale no longer
  accepts logs the node out (tailscale#19501). It goes in as `--auth-key=file:/dev/stdin` with the key as
  the `command` module's `stdin`, under `no_log` - nothing on argv, no file, never `copy` (which stages
  its content in `~/.ansible/tmp`); only that task renders the key, and `tests/unit/test_tailscale.py`
  holds it so. Every decision comes from `tailscale status --json`, read until it is neither `NoState`
  nor `Starting` (tailscaled is ready for systemd before its backend has a state), never from human
  output. The web login runs one `up` per attempt and reads the page once - each keyless `up` mints a
  new one - and opens it here only when it is Tailscale's own page (or one under
  `tailscale_login_server`). It never runs over an expired login made by hand
  (`tailscale_login_expired_elsewhere`): its `up --reset` would put the role's settings over that login's
  and record it as the role's; an expired login of the role's keeps its node key, which the marker holds. `/var/lib/claude-on-proxmox/tailscale-installed`, written before the
  repository, marks the Tailscale as the role's: only then are the package, service, log switch and
  removal its business, and a Tailscale installed any other way - by dpkg, or as one dpkg does not know:
  static binaries, the snap - is left alone, off too, and reported while on (off, configure runs the role
  only where the marker is, so it says nothing of one). `tailscale-joined` holds the node key
  of the role's own login: only that login's settings are changed, by one `tailscale set` naming what
  differs. It holds `pending` while a sign-in the role started has not ended (written before the page is
  shown, and after a key join that ran out of time - that one only: `up`'s timeout line and no `invalid key:`;
  any other failed join removes the marker), and the next configure records the login it ends in,
  so a page approved after the run is still the role's. A login is recorded only once `Running` or
  `NeedsMachineAuth`, never from `HaveNodeKey` alone: an expired node keeps its old key while the page
  waits. A page still waiting is reported before an expired or refused key. Off starts a stopped
  `tailscaled` to log it out, and a logout that fails is said, not failed on. The log switch is a systemd drop-in written before the
  package - never a pre-created `/etc/default/tailscaled`, which dpkg keeps as the conffile without
  `PORT`, and the daemon fails. Tags are compared only when configured and never "fixed" by a web login.
  `playbooks/configure.yml` runs the role last, from `tasks:` through `import_role` in a block whose
  rescue appends to `claude_vm_configure_failures` - off, only where a `stat` before it finds the installed
  marker, to remove what it installed, said first: a VM without one gets nothing of the role, the condition
  on the import, never the block; a last play raises it, skipped under deploy
  (`claude_vm_configure_in_deploy`), whose own last play raises it after VS Code. A failed install takes
  its repository back out (an unreachable source fails every apt refresh, `common`'s first), and the role
  has no handlers (one would run outside the rescue). Check mode skips what needs the package: `apt`
  cannot find a package from a repository the same `--check` run adds. Tests reach no Tailscale service
  and never this machine's real CLI: `tailscale`, `gh` and `claude` are among the harness's failing stand-ins, every scenario
  that runs the role installs `fake_tailscale.sh` in `prepare.yml` and turns log upload off, the
  Vagrantfile is keyless with logs off, and a guard holds each - and the role's scenario to `scenario_roles` and
  CI's matrix. The template is never signed in to anything.
- **The project writes exactly two things outside the repo on the controller:** `~/.ssh/known_hosts`
  and the managed SSH config `~/.ssh/claude-on-proxmox.conf`, plus one `Include` line at the top of
  `~/.ssh/config` that is added once and never removed. Nothing else in `~/.ssh` is ever edited.
  (The Claude Code sessions `just destroy` saves go into the checkout's `.sessions/`, inside the repo,
  unless the user points `destroy_sessions_dir` elsewhere.)
  Everything goes through `playbooks/tasks/ssh_config.yml`, which validates each write with
  `ssh -G`, refuses an alias the user's own config already names, and refuses a name or address that
  is not one plain word. A VM's address is whatever its guest agent reports, which the VM chooses:
  `guest_ipv4` accepts only what `ipaddress.IPv4Address` parses, because a newline and a
  `ProxyCommand` after a valid address pass `ssh -G` and run on this machine. A block is only ever written
  for a tagged VM, holds only what ssh reads (no state of this project's - `just code` reads the
  workspace on the VM), and pruning (`ssh_config.yml`) removes blocks, never VMs. The `Include` is
  added only when `ssh -G` says the user's config does not reach the managed file yet (a `${HOME}`
  spelling counts on OpenSSH 9.9+ and not before; an `Include` inside a `Host` block never does),
  and never rewrites or changes the mode of the user's file.
- **`just code` writes in one directory of its own on the VM, the session copies and one fast-forward
  aside, and never answers VS Code's or Claude Code's security questions.** With `vscode_claude_terminal` it writes a `.code-workspace` file in
  `<projects>/.claude-on-proxmox/`, beside the repositories and never in one, whose `folderOpen` tasks
  run `claude` in a terminal and a login shell split to its left (one presentation group); with `vscode_session_notes` too, `session/<vm>-session.md` there (0600,
  0700 directory), and a second `folderOpen` task, "Session notes", run beside the Claude task, which
  rewrites only the notes' section between the live markers each time the window opens and opens them,
  its time in this machine's zone (`code_timezone`, an argument like everything variable; UTC without one).
  Never make it the Claude task's `dependsOn`: VS Code misses a workspace-file task when it looks a
  dependency up and asks every task provider instead (npm, gulp running `gulp`, grunt, jake), which held
  Claude back 7.5 s live; `test_code_session.py` fails on any `dependsOn`. Its bash runs the Python part
  in the background, waits 5 s at most and ends `exit 0`, so the notes always open; never `exec` into
  Python. The notes' directory is a
  second workspace folder only where the window's own folder contains it: VS Code stores the folders a
  user trusted, not their parents, so a folder added to a trusted workspace drops it to Restricted Mode.
  `just code` slurps the notes to carry their live section over, so an unchanged VM rewrites nothing.
  VS Code runs that task only in a trusted window and once the user has allowed automatic tasks;
  `code.yml` reads `task.allowAutomaticTasks` from the user's VS Code settings to say which to
  expect, and must never write it, set trust, or lower `security.workspace.trust.*` or
  `terminal.integrated.allowInUntrustedWorkspace`. The script is fixed text in `code.yml`'s vars;
  anything variable reaches it as an argument. Every name `code.yml` sets starts with `code_`, a prefix
  no role uses: a role's fact or register outranks a play var in the same `just deploy`, and no task var
  shares a fact's name. In `just deploy` (`code_in_deploy`) every task of the window step is in one
  `block` whose `rescue` says any failure with the `just code` line (`just deploy <vm>
  --start` for a VM the deploy left stopped) and ends the play; outside it the
  rescue fails again. A fleet's deploy ends at code.yml's first task with each VM's line.
  `tests/unit/test_code_session.py` runs it (once at a pseudo-terminal, for Ctrl-C), runs the window
  step's own tasks with stand-ins for `which` and `code` first on PATH, and holds
  every task in `code.yml`, and in the task files it imports, followed all the way down, to an
  allowlist: read-only on this machine (plus `which`/`code`, the session handoff's `rsync` to the VM and
  the alias through `tasks/ssh_config.yml`), the handoff's readers (`claude_sessions_info`,
  `git_checkouts_info`) here or on the VM, or `stat`/`getent`/`find`/`slurp`, --session's `git` and the six
  writes (the workspace and its directory, the notes and theirs, the sessions' directory and their copies)
  delegated to the VM; a `shell`, a writer in a `block`, or a `connection` fails it by name. The helper is run against stand-ins with nothing else
  on PATH: never a login shell there, which would put this machine's own `code` and `tailscale` back. The `provision`
  scenario, which runs `code.yml`, points `vscode_user_settings_file` into the ephemeral directory,
  since the message depends on it, and asserts that file's bytes and mtime after every run.
  With `vscode_claude_sessions` (on by default) it also copies this machine's Claude Code sessions of the
  last 30 days to the VM, those whose first directory's git origin names a repository the VM has a
  checkout of (`owner/repo`, matched against the VM's checkouts, never by folder name; the origin as
  configured, never as `url.<base>.insteadOf` rewrites it, and on the VM only a GitHub one, so a checkout
  there with a local path or another host's URL claims nothing of this machine's): into the VM's
  `~/.claude/projects/` under the folder names they have here, so `--continue` in a checkout never picks
  one up, through an rsync mirror and a manifest in `<workspaces>/claude-sessions/` (0700). Each copy
  gets exactly four edits (`claude_sessions_copy` in `playbooks/library/`): the memory folder in its
  `prompt_snapshot` entries, read from the entry and never computed; the session's `tool-results/` folder
  of this machine's, in user entries only (never an assistant's, which can carry signed thinking); a
  cleared `worktree-state` after a bound one; a `relocated` entry naming the checkout. A copy is replaced
  only while it matches the manifest's digest, written after each copy, so a session continued on the VM
  is never overwritten; it keeps this machine's file time only within 14 days, else the time of writing,
  since Claude Code sweeps a transcript at 30. `--session <id>`
  (`vm_session`, checked by its pattern and `tools/check session`) resumes that one with `claude --resume <id>`, never a
  fork and never a fresh `claude` after a failed resume, once its checkout here is clean and its branch
  pushed and the VM's checkout clean, on a `HEAD` some ref holds and free of a `claude`; the VM checkout
  then reaches the same commit by fast-forward only, never over a path git does not track there (an
  ignored file git would overwrite unasked), the one write `just code` makes in a repository. rsync's 23
  and 24, and any failure without `--session`, are said and the window opens anyway. The handoff reads this machine's
  `~/.claude` (`code_session_source_dir`) and checkout, with `git --no-optional-locks`, and writes
  nothing here; every task reading a transcript is `no_log` and no message carries more than IDs,
  folders, branches and counts. Claude Code's trust question for a checkout stays the user's. The
  allowlist follows `code.yml`'s own imports (`tasks/session_read.yml`, `tasks/session_write.yml`;
  only `cluster_vms.yml`, `vm_addresses.yml` and `ssh_config.yml` are shared and not followed) and names
  each reader, writer and command (docs/architecture/lifecycle.md §4.9). A copy gone while its ID is in
  another folder of the VM's moved there (`/cd`) and is never written again: an ID in two folders resumes
  from neither - unless that copy is still as written, when the session moved here and its copy follows.
  A copy a claude on the VM has open (its `sessions/` registry, at a pid that still runs claude) is left
  as it is, and so is an ID in two folders here; origins are reported without credentials, and no path or
  branch is expanded (`expand_user_and_vars=False`). The copies' records of each request sent (`api-request-shape`, `api-request-blob`) keep
  this machine's memory folder: resume sends the snapshot, never them, which the `configure` scenario holds.
- **`just destroy` saves what of a VM's Claude Code sessions exists only there before releasing it.**
  `tasks/session_save.yml`, under `destroy_save_sessions` (on by default): `claude_sessions_info` on the VM
  with `all` and just code's manifest (in `just_code_dir`, as code.yml names it), `claude_sessions_saved`
  keeping each session with a message that is not a copy still as written, with the folder beside it, and
  every auto memory folder. Nothing the VM sends is trusted: a folder or ID not named as Claude Code names
  them is left out, and `claude_sessions_fetch` (delegated here) runs `tar` there over SSH as Ansible
  reaches the VM - port, key and SSH arguments included - and unpacks the stream itself, plain files and
  folders only, at the names asked for, never through a link or outside the folder: never rsync, which
  made the VM's links here as links, nor a `file` with `recurse`, whose `follow` chmodded their targets.
  Into `destroy_sessions_dir` (absolute or `~`, refused otherwise before any VM starts;
  `.sessions/<vm>-<time>/` in the checkout: git-ignored, refused by `tests/check_no_local_files.sh`, inside
  the repo, so the controller still writes only the two SSH files outside it), edited in nothing,
  0700/0600, with `sessions.json` naming the `claude --resume <path>` that continues each, its version and
  that its content is the VM's. The save runs as the VM user, never through sudo, Remote Control paused
  for it (`claude_code`'s `release_pause`, `release_resume` on a VM kept), and a VM that stops answering
  part way fails into the rescue (`ignore_unreachable` and an assert), never out of the run unseen. A
  failed save keeps the VM, neither released nor deleted; an unreachable VM is deleted as before and said
  to have gone unsaved; `destroy_release: false` still saves, and releases nothing; a VM whose sudo fails
  is saved, then deleted unreleased, said so: sudo is asked for in a block before the save, since a failed
  sudo is an error `failed_when` never sees (docs/architecture/lifecycle.md §4.5).
- **Tests never read or write the developer's `~/.claude`.** `just code` reads this machine's sessions
  and, in every scenario and harness, the "VM" is this machine too. Every run of `code.yml` points
  `code_session_source_dir` and `code_session_dest_dir` into its own directory (`_window_extra` in
  `tests/unit/test_code_session.py`, `verify_code_vars` and every `deploy.yml` run in the `provision`
  scenario), destroy's save reads only stand-in homes and writes into the test's own directory
  (`_destroy_tail` in `tests/unit/test_release.py` pins `destroy_sessions_dir`), the VM's `claude` is a stand-in beside the latter - looked for only at the installers'
  paths, never on `PATH` - and git runs there with `GIT_CONFIG_GLOBAL=/dev/null`, since the developer's
  config (a `status.showUntrackedFiles`, an `insteadOf`, a hook) changes what git says and does. The guards in
  `test_code_session.py`, and for destroy's pin `test_release.py`, fail if one stops.
- **Tests never write into the developer's `~/.ssh`.** The `configure` scenario runs the real
  playbook and its `localhost` is the developer's machine, so every playbook scenario points
  `claude_ssh_config_file` and `claude_ssh_user_config` into `MOLECULE_EPHEMERAL_DIRECTORY` with
  the `Include` off, the Vagrantfile sets `claude_ssh_config: false`, and
  `tests/unit/test_ssh_config.py` fails if any of them stops.

- **Every live output draws through one region and one way of drawing.** `filter_plugins/live_region.py`'s
  `LiveRegion` puts a frame at the bottom of the terminal and `filter_plugins/screen.py` builds its lines
  (segments cut to the width, one palette): `just status`'s callback and every live run's, and each recipe
  drawn live after them - a playbook run joins through `CLAUDE_ON_PROXMOX_LIVE` and its declaration in
  `filter_plugins/live_recipes.py`, a report hands an `Output` a frame of its own; never a second region, palette
  or cut. A command's live output is its declaration: the engine (`filter_plugins/live_ansible.py`), the model
  (`run_view.py`) and the region know no command, and what a declaration cannot say becomes a new field there,
  never code of one command's own. A recipe of several processes is one run in parts: each command goes through
  `tools/live`, which passes it through unchanged, without the variable or the state's path, unless it can draw -
  a terminal, a title, no `-v`, a log, a state, the engine's packages, decided with the standard library alone -
  while a playbook part (init's) joins through the exported variable itself, drawn a line per step without a
  terminal; each part takes over the run's state in `LIVE_DIR` (`.cache/live/`, refused by
  `tests/check_no_local_files.sh`), whose end, the recipe's trap, says the summary; a nested recipe joins its
  parent's run through the `live_run` setting, never the environment. The region draws one
  write per redraw in synchronized output, up exactly the lines drawn, so no line may wrap (`tests/unit/vt.py`
  counts them); steps aside for every prompt (`prompt_until`, and a `vars_prompt`'s `do_var_prompt`), back only
  half a second after it; keeps
  the echo off while it is up; and never hides the cursor. Modules load each other by path, once, as
  `claude_on_proxmox_<stem>`, and every test that runs something at a terminal opens it through
  `harness._at_a_pseudo_terminal`. See docs/architecture/custom-code.md §7.8.

- **`just deploy` is drawn live, and nothing a person must read is drawn only there.** `CLAUDE_ON_PROXMOX_LIVE`,
  which only the recipes drawn live export - `deploy`, `login`, `destroy`, `code`, and `lint`, `init` and `test`
  for their parts - (the run's title) and the engine takes out of its environment at once, so nothing the run starts
  inherits it, hands the stdout callback's every event to the engine at verbosity 0 with
  a run log: at a terminal, the region drawn from `filter_plugins/run_view.py`'s model; otherwise a line per step.
  Every line the stream would print still reaches the run log, in order, through `Display`'s `log_only` - so
  without a log (`LOG_DIR=`) the run is the stream; the reports, browser blocks, diffs, warnings and failures
  print above the region for good, whole - a report is wrapped, never cut, a failure says its output's end and
  each failed loop item, and only a failure among a rescued block's own tasks - at any depth, through imports and
  includes too - is the block's. What a VM said reaches the screen through `plain_text`, and only `proxmox_vm`
  gives the summary a VM's address - or, for a recipe whose declaration names discovery (`login`) or
  `vm_addresses.yml` (`code`), the address and VM ID discovery's `add_host` or `claude_vm_addressed` read from
  Proxmox and the guest agent, and nothing else of them. Any `-v` or another
  stdout callback is the stream. No test or scenario sets
  the variable: `test_deploy_live.py` runs the callback at a pseudo-terminal of its own against a stand-in deploy,
  holds the log to the stream's, and fails if a tracked file other than the callback, the engines
  (`filter_plugins/live_ansible.py`, `filter_plugins/live_commands.py` and `tools/live`), the declarations
  (`filter_plugins/live_recipes.py`), the recipes drawn live, the live tests' kit (`tests/unit/live_kit.py`) and
  pytest's plugin (`tests/unit/live_pytest.py`), the live tests that draw a playbook through the engine or a
  command through `tools/live`, each listed by name, and `test_justfile.py`'s recording stand-in names it;
  `tools/live` takes it, and the state's path, out of every command's environment, passed through or drawn. A
  failure is headed by its step and its VM, and one a declared
  task raises again is drawn under the failure a rescue took; a declared refusal (destroy's unconfirmed deletion)
  is said as the person's answer, never as a failure. See docs/architecture/custom-code.md §7.5.

- **`just init` writes only what is still a placeholder, only at a terminal or as given, and the vault
  only encrypted.** `playbooks/init_settings.yml` runs with its own inventory, `playbooks/inventory/init.yml`
  (the controller alone, pipelining on, no `group_vars`; never in `playbooks/` itself, where the syntax
  check and ansible-lint take it for a playbook), and asks through `tasks/init_ask.yml` only while a value
  is the example's or unset; Enter is "later", and an answer is held to its kind by `init_answer`
  (`filter_plugins/settings.py`). `init_ask` is false unless the justfile sees a terminal and was not given
  `--no-prompt`, and every write is under it or under a value given on the command line
  (`init_given`), which never overwrites: one that differs from what is set fails the run, naming both.
  `hosts.yml` and `local.yml` are changed by one validated `lineinfile` each, on the example's own line.
  The vault is re-encrypted by piping its text into `ansible-vault encrypt`'s stdin under `no_log`, never
  through `copy`, which decrypts. `.vault_pass` is created `O_EXCL`, 0600, and never rewritten. Every
  test points the four `init_*_file` vars into its own directory, through `_init` and
  `_init_at_a_terminal` in `tests/unit/test_init_settings.py`, which holds that statically. The token
  secret is never taken from the command line (`just init` refuses any word after its options without
  printing it, make's `TOKEN_SECRET=...` among them): only from `--token-secret-file`'s file or `CLAUDE_ON_PROXMOX_TOKEN_SECRET`, read in the playbook, so it is on no
  argv. Tailscale is asked about while `local.yml` does not set `tailscale_enabled`, the answer written
  either way, and its auth key only after a `y` (or `on`, or `true` set), hidden, into the vault with the
  other answers - never from the command line. `playbooks/init_check.yml`, its second run, changes nothing but `local.yml`, through
  `tasks/init_local_setting.yml` and under `init_ask` or a value given: the node when it is the default and
  not the template's (worked out, never chosen), the storage or bridge chosen from what the node lists and
  the token can use (`tasks/init_choose.yml`). Every other task is a read, its one command `hostname -s` over
  root SSH, and its tests give the Proxmox host a local stand-in or a refusing `ssh`, never a real one
  (`tests/unit/test_init_check.py` holds all three).
- **`just init`'s offer to make the API token is the one change the project makes on Proxmox outside its
  template and VMs, and only on a `y`.** `tasks/init_access.yml` (from `init_settings.yml`, at a terminal,
  while the vault has no secret and none was given) runs `tools/proxmox-access.sh` on the first `proxmox`
  host over root SSH - the `command` module, `/bin/sh -s` with the script on stdin, never a copy left there:
  `--list` and `--dry-run` change nothing, and the question shows the dry run's `pveum` commands before the
  script runs. It creates only what is missing - the role `AnsibleVM`, the user, a grant where the user
  lacks the role's privileges (so a grant the host's owner made, the homeserver's storage, stands), the
  token last - and changes nothing that exists. Its stdout is the token's secret alone, read `no_log` into
  the hidden answers; its talk is stderr. The offer and the secret are the last tasks before the vault's
  block, with nothing between, and the block's rescue deletes the token the run made while the vault is
  unwritten - never once `ansible-vault` wrote it, when it holds the secret. `n` is the same script by hand, and
  its privileges are `init_check.yml`'s (`tests/unit/test_proxmox_access.py`, against `fake_pve_cli.py`).
  Every `init_settings.yml` test run has a refusing `ssh` first on PATH, and the offer's a local stand-in
  host (`test_init_settings.py` holds both).

- **`just status` is read-only information, and must never change anything.** Not the VMs (reads over
  SSH only: no sign-in, no start or stop, no install, never `claude` itself until a real VM shows `claude
  auth status` refreshes nothing), not Proxmox (GETs only), not GitHub or Tailscale, not this machine: no
  host key - each VM gets `status_ssh_args` through `add_host`, `ansible.cfg`'s with
  `StrictHostKeyChecking=yes` and `UpdateHostKeys=no`, as `ansible_ssh_args` since ssh keeps an option's
  first value - no alias, nothing in `~/.ssh` or `~/.claude`. The roles are read through `status` entry
  points of their own (`claude_code`, `github_projects`, `tailscale`), which reuse their reads and reports
  (`login_read.yml`/`login_report.yml`, `ownership.yml`/`state.yml`/`report.yml`), so a line status shows
  is NEXT STEPS' own. It writes only its run's report into `.cache/status/` (git-ignored, refused by
  `tests/check_no_local_files.sh`; the recipe removes it once read) and the log. `tests/unit/test_status.py`
  holds every task in `playbooks/status.yml`, and in every task file and role entry point it reaches, to
  an allowlist - a `uri` only with `url` (under `{{ proxmox_api_url }}/`), `headers`, `validate_certs`,
  `ca_path` and `timeout`, `check_mode: false` and `no_log`; the shared listing; `proxmox_vm`'s
  `settings_drift`; `stat`, `slurp`, `set_fact`, `assert`, `add_host`; the report's `file` and `copy` -
  and checks that a run against the fake sent nothing but GETs. The recipe runs it as `--check`, which
  `uri` ignores, hence `check_mode: false` on every GET and the allowlist over everything. Every value a
  VM or Proxmox supplies is made one line of plain text (`one_line`, `status_clean`) before the model
  keeps it, so none starts a line of its own in the report or in `--json`; every line of the report fits
  100 columns. Its stdout callback (`callback_plugins/claude_on_proxmox_status.py`, chosen by the recipe,
  the run's only callback) draws the report live at a terminal in the shared region (above), shows task
  warnings (ansible-core 2.19+ carries them on the result), and prints only the finished report piped, in the
  log, or under `--json` (`STATUS_QUIET`). Every status run in a test pins `status_report_file` and `claude_ssh_config_file`
  into its own directory and gets a refusing stand-in `ssh` (`status_root`); the reads inside a VM run
  against stand-in VMs (`tests/unit/test_status_vms.py`), which hold that every file there is unchanged.
  See docs/architecture/lifecycle.md §4.6.

- **`just settings` reads without changing anything, but for the one setting `--value` writes into `local.yml` or
  `--reset` takes out, and never prints or writes a secret.** `tools/settings` asks Ansible's own `VariableManager`
  and templar, in-process, for a play of the role on a host of the group its playbook runs it on (read from the
  playbooks), and never writes Ansible's precedence again by hand. Every lookup goes through `lookup_loader.get`,
  wrapped to load only `env`, `file`, `config`, `vars`, `first_found` and `fileglob` - never decided by scanning
  expressions, which miss one reached through a variable in `hosts.yml` - and the inventory is read by the `yaml`
  plugin alone, never `script`. A value is masked when `vault.yml` holds it, when Ansible marks it decrypted
  (`VaultHelper.get_ciphertext`: an inline `!vault`, which the file's values do not carry), when its role marks the
  variable `no_log`, or when its expression reads a name the vault holds or a `no_log` variable; then every value
  about to be printed is checked against every secret decrypted, and a match prints nothing and exits 1 - the GitHub
  username and an OAuth client's ID masked in their rows but never looked for elsewhere. It never changes the vault:
  a secret's row points at `just vault`. `--value` writes one setting into `local.yml` and nothing else: what a
  name can be set as comes from one function (`settable`), which the read view's hint uses too; anything the
  vault holds or feeds, a `no_log` variable, an inline `!vault`, a name made from each VM's own, what a run sets
  for itself, the host's address, an internal value, an expression (`{{ }}`) and a value equal to a vault secret
  are refused, a secret's value never said back. The rest is held to its `argument_specs` type by Ansible's
  `ArgumentSpecValidator`, written as `just init` writes (JSON in place of its line or its example's line), read
  back with every other key unchanged before it replaces the file atomically, or nothing is written. `--reset`
  takes the setting's line out and puts its example's commented line back (set then reset leaves the file byte for
  byte as it was), refused only a secret and the host's address, since taking a line out makes no value wrong. The vault's
  password file is the recipe's (`vault_args`),
  never looked for by the tool. Its tests (`tests/unit/test_settings.py`) run it only against inventories made from
  the example files with fake secrets, each run with a `HOME` of its own and the collections' path pinned, and fail
  if any fake secret is printed, every write into a copy of its own; the recipe's run against a stand-in `python`,
  since the tool in the checkout would read, and write, the developer's real settings. See
  docs/architecture/custom-code.md §7.9.

## Architecture, and why it looks like this

**Playbooks own inventory; roles do not.** `add_host` sets `BYPASS_HOST_LOOP`, so it runs *once per
task* no matter how many hosts the play has. A copy inside a role publishes only the first VM of a
fleet. `provision.yml` therefore ends with a third play that loops `add_host` from localhost over
the facts the role left behind. Do not move it back into `proxmox_vm`. That loop is over the names
asked for, so it publishes only a VM whose `proxmox_vm_address` is non-empty: a VM the role left
stopped has none on purpose, and one whose run failed has no fact at all.

**`provision.yml` is three plays on purpose.** Most of the per-VM time is a multi-minute wait for
the guest agent, and the VMs boot on the hypervisor up to `forks` at a time (20 in `ansible.cfg`;
`ANSIBLE_ARGS="-f N"` for a larger fleet). Play 1 turns each name into a placeholder host, play 2
runs the role across them so Ansible fans out over `forks`, play 3 publishes. A loop over
`include_role` would serialise the waiting.

**`destroy.yml` has the same shape.** A localhost play prompts, lists the cluster, checks every name
and adds each as a placeholder host - refusing, before anything starts, a name two VMs carry, since the
release finds a VM by name; with `destroy_release` or `destroy_save_sessions` (`claude_vm_reaching`) it first starts each stopped VM through the API and
reads every VM's address from its agent - asking the ones it started whatever the cluster listing says
(`claude_vm_just_started`). A VM it started is stopped again (`tasks/vm_stop.yml`) when the
run then fails before deleting (a rescue in that play) or its deletion fails: the delete play rescues a
failed deletion and the last play raises it, after the stop and the report - a play in which every host
fails ends the run, which once left a lone VM running. A release play then connects to each VM over SSH
(`add_host` gives each VM its SSH settings as host vars; the delete play forces `ansible_connection: local`),
saves first what of its Claude Code sessions exists only there (`tasks/session_save.yml`, below), and runs the
`release` entry point of `claude_code`, `github_projects` and `tailscale`, each in a block whose rescue
records a failure: a release never stops a deletion. A save does: one that fails sets
`claude_vm_delete_failure` and ends the host, so the VM is neither released nor deleted. A VM with no address or that does not answer is ended and deleted as it
is. Each entry point leaves one clause in a `*_release_said` fact, and the last play says per VM what it
was released from or what is left of it. GitHub's release only signs gh out and revokes no token:
revoking the one this project's device flow made, through `POST /credentials/revoke`, ended every
GitHub CLI sign-in of the account, the developer's own included, so the report says where to revoke it
and what that does (the digest in `~/.config/claude-on-proxmox/gh-device-login` tells that token from
the vault's and from one pasted by hand). Tailscale's device
is deleted with the API credential the vault holds (`tailscale_api_auth_method` `auto`: the token, else an
OAuth client with `devices:core`; a credential's presence is the opt-in, so no flag must be set beside it)
from the controller, by the node's `Self.ID`, read before the logout. Another play then runs the role with
`proxmox_vm_state: absent` across them, so VMs are stopped and deleted in parallel. It used to loop `include_role` on
localhost: against the fake, 20 VMs took 168 s that way and take 41 s now. The pre-check honours
`proxmox_vm_allow_untagged_delete` but never admits a template. Deleting in parallel is why
`absent.yml`'s `known_hosts` task is `throttle: 1`.

**`deploy.yml` configures only what provisioning published.** It imports `configure.yml` with
`claude_vm_discovery: false`. Discovery without `vm_name` means every tagged VM, while provisioning
without it means the one default VM, so running both made a bare `just deploy` create the default VM
and then configure the whole fleet. `just deploy --only configure`, `--check` and `--only ssh-config`, `just status`
and `just login` stay fleet-wide without names: each runs its own playbook, discovery on.

**A stopped VM is left stopped, and not waited on.** An existing, configured VM is started only
with `proxmox_vm_start_existing`. Otherwise `present.yml` sets `proxmox_vm_stopped` from the
lookup's `status` and skips the NIC read, the agent wait and the address assert: its agent cannot
answer, and Proxmox's 500 for that is retried like a slow boot.

**A clone without a VMID is `throttle: 1`.** Proxmox has no atomic VMID reservation — `proxmox_kvm`
asks `/cluster/nextid` and then clones, so two concurrent clones get the same ID and one fails. But
under the linear strategy a throttled clone holds every VM's settings, start and boot back until the
fleet's last clone is done, so `provision.yml`'s play 1 gives each new VM of a fleet its own
`proxmox_vm_id` (from `/cluster/nextid`, skipping what `/cluster/resources` lists), and the throttle
is 0 for a clone with a VMID and for a VM that exists. Keep both halves: dropping the throttle makes
unallocated concurrent clones collide every time, and `strategy: free` neither shortens the run nor
enforces the throttle across included tasks. On a VMID collision `proxmox_kvm` returns
`changed=False` and *the clone source's* VMID, so the role asserts `is changed` rather than trusting
the returned ID — without that, every later task reconfigures the template.

**`roles/proxmox_vm/vars/main.yml` holds lazily-evaluated internals** that read registers set by
tasks (`proxmox_vm_lookup`, `proxmox_vm_config`, `proxmox_vm_net`). They are vars, not facts, on
purpose: the role can run more than once for the same host in one play - the role scenario's
`side_effect.yml` includes it back to back - and a `set_fact` survives a run that skips it, so a
fact would hand VM #1's MAC to VM #2. A skipped register is overwritten; a skipped `set_fact` is
not. Do not convert these to `set_fact`. The role sets exactly four facts, each unconditionally on
every run so none can go stale that way: `proxmox_vm_exists` and `proxmox_vm_resolved_id` in
`main.yml` (`present.yml` re-sets the ID after a clone, the one time it changes), `proxmox_vm_stopped`
and `proxmox_vm_address` in `present.yml`. They are outputs rather than internals: `provision.yml`'s third play reads
`proxmox_vm_address`, `proxmox_vm_stopped` and `proxmox_vm_resolved_id` through `hostvars`, and role
vars are not visible there. The `settings_drift` entry point, which only `status.yml` runs, sets a fifth,
`proxmox_vm_settings_drift`, unconditionally, from the config passed in. The
same pattern gives `claude_vm_tagged` and `claude_vm_requested` in
`inventory/group_vars/all/defaults.yml`, which need `claude_vm_all` registered first by
`playbooks/tasks/cluster_vms.yml`.

**Existence is not convergence.** A run that died between the clone and the settings call leaves a
VM with no cloud-init user, no keys and no agent. `present.yml` tags a new VM in a call of its own
straight after the clone, so that VM is still recognisably this project's, and
`proxmox_vm_needs_configuration` keys off the *cloud-init user*, which only the settings call writes,
so it is repaired instead of skipped. Do not key it off the ownership tag again: "untagged" must mean
"not ours", because an untagged VM with a matching name is someone else's and is refused. A run that
died *after* the settings call — at the resize or the start — is caught by a second marker: the
settings call adds `claude-on-proxmox-unfinished` (`proxmox_vm_unfinished_tag`) and only the call
after a successful start removes it, so a re-run finishes that VM while a finished VM someone shut
down stays stopped. Do not decide the start from `status` alone, and do not run the resize on every
run: `proxmox_disk` compares size strings, and Proxmox cannot shrink a hand-grown disk.

**Assert what sshd will apply to the editor's connection, not the file we wrote.** sshd keeps the
first value it reads for a keyword, and a `Match` block sets its own for the connections it matches,
so a drop-in sorted before `20-vscode-server.conf`, `/etc/ssh/sshd_config` itself or a `Match User`
block can each override it silently. `roles/vscode_server` therefore reads
`sshd -T -C user=<vm_user>,addr=...` - for the VM user, from the address in Ansible's own
`SSH_CONNECTION` - and fails on forwarding that is off, limited to the remote direction, or removed
by `DisableForwarding`, naming the file. Plain `sshd -T` skips `Match` blocks entirely. `sshd -T`
parses the files on disk, so nothing is restarted before the check; do not add a `flush_handlers`
back. Keep each drop-in with its owner: `10-` is `common`'s hardening, `15-` `common`'s PATH for the
VM user's ssh sessions (`Match User` + `SetEnv`, checked the same way, since sshd keeps the first
`SetEnv` that applies), `20-` VS Code's forwarding and keepalives. That PATH starts with `~/.local/bin`,
so every `add_host` that publishes a VM gives it `ansible_python_interpreter: "{{ claude_vm_python }}"`:
discovery would take a `python3.13` that `uv python install` put there, and run it as root.

**The alias play reads a fact the configure play set last.** `configure.yml`'s second play ends
with a `getent` of `vm_user` and a `claude_vm_workspace` fact, both tagged `always`; the third play
writes an alias only for hosts that have that fact, so a host that failed a role never gets one, and
the workspace is the VM user's real home rather than a guess from the controller (Ansible's own
`user_dir` fact is the *connecting* user's home, which under Molecule is root's).

**NEXT STEPS is recorded by each report, never read off the output.** `callback_plugins/claude_on_proxmox.py`,
the stdout callback `ansible.cfg` names, prints NEXT STEPS after the recap (after the summary, under `just deploy`) from `set_stats` records only -
`{vm: {matter: line}}` under five `*_next` stats - each made straight after its report from the facts the
message is built from: the line while something is left, `''` once nothing is, the last record of a VM and
matter winning across plays. Do not decide from a message's text: a stopped Tailscale names no `just`
command, and "just deploy signs it in next" names one with nothing to do. A new report needs its record, and
`tests/unit/test_attention_callback.py` fails without it. Tests never load the callback (an
`ansible.cfg` holding only `inject_facts_as_vars = false`, and the JSON callback); the harness shows custom stats, and `_left_to_do` reads the records.

**Discovery is a playbook, not the `community.proxmox` inventory plugin.** Decided, not open: the plugin
turns an unreachable API or a refused token into a warning and an exit 0, merges two VMs of one name, calls
Proxmox on every `ansible*` command (`--syntax-check` and ansible-lint included), and would replace only
`discover.yml`, since five playbooks read the cluster through `tasks/` anyway. See
docs/architecture/decisions.md §15.1.

**Fleet listings read `/cluster/resources`, never an unfiltered `proxmox_vm_info`.** Unfiltered, the
module asks every node that owns a VM for its VM list and fails on any error, so one node that is
down broke `deploy`, `configure`, the fleet listing and `destroy` for every VM on the others.
`playbooks/tasks/cluster_vms.yml` fetches the listing with `uri` (`no_log`, since the request
carries the token); `proxmox_vm_info` is called only with a `name` or a `vmid`. The shared pieces
live in one place each, so the playbooks cannot drift: `proxmox_api_module_defaults` for
`module_defaults`, `claude_vm_requested` for narrowing to the names given (`vm_name`), the `guest_address` filter with
`proxmox_nic` for a VM's address, and the `proxmox_access_denied` filter for "the token was
refused" - which anchors 401/403 to proxmoxer's status position, because Proxmox names the VM in
the error text and `VM 4013 is not running` otherwise reads as a 401.

## Things that have bitten this repo

- **ansible-core 2.21**: a non-boolean conditional is a fatal error, not a warning.
- **An Ansible tool's stdout is more than its output once verbosity or debugging is on.** With
  `ANSIBLE_VERBOSITY` or `ANSIBLE_DEBUG` exported (or set in `ansible.cfg`), `ansible-vault decrypt --output -`
  prints `Using ... as config file` and more after the vault's text, which `just init` read as the vault's:
  an encrypted vault was "not a YAML mapping", and every read-back differed. Each `ansible-vault` init runs
  has `environment: "{{ init_ansible_vault_env }}"` (both 0); `test_init_settings.py` runs it verbose and
  debugging, and holds every call to it.
- **A gathered fact is `ansible_facts['architecture']`, never `ansible_architecture`.** ansible-core 2.24
  stops injecting facts as top-level variables, and `ansible.cfg`, Molecule's shared config and every
  `ansible.cfg` a unit test writes (`harness.ansible_config`) already set `inject_facts_as_vars = false`, so
  the top-level name is undefined here. The first `just deploy` of a new VM printed a deprecation per read
  until they moved; `tests/unit/test_gathered_facts.py` scans for one and holds all three settings.
- **Every checkout on a machine shares `~/.ansible/collections`.** `make deps` in a worktree that still
  pinned ansible.posix 2.1.0 put it back under every other checkout, whose runs went on without a word.
  Every recipe that runs Ansible now runs `tools/check collections` first (`ansible-galaxy collection install --offline`
  against `requirements.yml`), and refuses, naming `just install`, while a collection differs
  from its pin.
- **uv writes the environment's path, as it was given, into every command it installs.** `make venv`
  synced through the checkout's `.venv` link to the environment, so 31 commands started their Python by
  that link and would have broken with it, and a sync that finds every package current rewrites none.
  `just install` runs `tools/stale_scripts.py` first and reinstalls each locked package with a command
  whose first line names a Python outside the environment's `bin`, compared as spelled, never resolved
  (the link resolves to the right Python), then `--check`s that none is left.
- **`-e key=value` splits extra-vars on whitespace.** make's `VM_NAME="alpha beta"` silently provisioned
  half the fleet. The justfile passes the names as JSON (`-e '{"vm_name": "..."}'`) to keep them whole.
- **Proxmox tags** arrive `;`-joined and lowercased on the wire, but `proxmox_kvm` *sends* them
  comma-joined. Anything parsing tags must handle both.
- **`vars_prompt` silently takes its `default:` without a terminal.** It does not hang — ansible-core
  warns ("Not prompting as we are not in interactive mode") and uses the `default:`, or `None` when
  there is none. So every prompt needs a `default:`, and it must be the safe answer (`destroy.yml`
  defaults to `no`).
- **A `vars_prompt` sees the play's vars and the extra vars, nothing else.** It is templated before any
  host is in scope, even when `-e` answers it, so a prompt naming `claude_vm_names` (group_vars) failed
  every `just destroy` before it asked. `test_release.py` runs destroy with no inventory variables at all.
- **A stdout callback that subclasses the default needs the default's `DOCUMENTATION`.** Without the
  `default_callback` and `result_format_callback` fragments it has no options, every hook fails, Ansible
  only warns, and every task's result disappears in a run that exits 0. `--syntax-check` loads no
  callback; `test_attention_callback.py` compares the output with the default's.
- **A play's `vars:` apply to the tasks it delegates.** destroy's release play set `ansible_connection:
  ssh` and `ansible_host` as play vars, so its `delegate_to: localhost` calls to Tailscale's API ran on
  the VM, credential included - caught on a live run only because this machine's Python path does not
  exist there; the unit test had forced every connection local with `-e` and hid it. How to reach a host
  goes on the host (`add_host`, the inventory); a play may only force `ansible_connection: local`.
  `tests/unit/test_release.py` holds every playbook to that.
- **`/cluster/resources` lags a start by up to ten seconds.** It lists the status pvestatd last polled,
  so a VM destroy had just started, booted and reporting its address, was still "stopped" there;
  `vm_addresses.yml`, which asks only running VMs, skipped it, and destroy deleted it unreleased (live,
  2026-10-12). Never decide from the listing's status what a run itself just changed;
  `test_release.py` runs destroy's first play against the fake with its lag on
  (`PUT /api2/json/fake/listing-lag`).
- **A Ctrl-C that lands while Ansible forks a worker is lost.** Python runs ansible-core's SIGINT handler
  inside logging's after-fork hook (`_releaseLock`), which prints its KeyboardInterrupt as "Exception
  ignored" and goes on; the handler has already killed the workers and set SIGINT back to the default, so
  the run carries on or hangs, and a second Ctrl-C kills it outright. CI lost `test_status_live.py`'s
  Ctrl-C this way, sent the moment the VMs were drawn, just as the next task's workers were forked. A test
  that interrupts a run sends it while the run waits on something held and nothing forks; a Linux pty also
  buffers about 64 KB without blocking the run, so a run can end before its test reads the frame it acts on.
  Filed upstream as ansible/ansible#87812. A Ctrl-C can still hang a run there now and then (CI's deploy
  Ctrl-C test once sat in a browser wait for two minutes after one), so that test runs a hung run once more,
  until the pinned ansible-core has the fix.
- **pytest-xdist's groups are made in xdist's own `pytest_collection_modifyitems`.** The hook in
  `tests/unit/conftest.py` that marks them is `tryfirst`; run after xdist's, it marked nothing and loadgroup
  quietly became plain load, rebuilding every module-scoped fixture test by test (37.5 minutes against 9.8). It
  marks whole modules, since a test can reach a fixture through `request.getfixturevalue`, which a look at its
  arguments misses. And a suite started with `&` or under nohup has SIGINT, SIGQUIT and SIGHUP ignored in every
  child: a test that sends Ctrl-C starts its child through `harness._default_signals`, or the Ctrl-C reaches
  nothing (the device-flow test hung that way on a VM, never on CI).
- **Ansible's loader caches a file without its trust as a template.** `tools/settings` read `local.yml` through the
  same `DataLoader` the inventory uses, to say which file set what; the inventory, reading the file after, got the
  cached, untrusted copy, and no value of `local.yml` or `defaults.yml` was templated - nor any lookup in them run,
  so the checks of the lookup guard and of a copied secret passed without ever being reached. A read of a file the
  inventory also loads is `cache="none"`, and a value a tool supplies is `trust_as_template`d.
- **A skipped task still overwrites its register.** A `when` on destroy's second cluster listing left
  `claude_vm_all` without its listing whenever no VM had been started, and destroy failed before it
  began; register only in a task that always runs, or read it with a `default`.
- **A `set_fact` outranks the `vars:` of a later `import_tasks`.** `ssh_config.yml` built its
  refresh list as a `claude_ssh_vms` fact, then imported `tasks/ssh_config.yml` a second time with
  `claude_ssh_vms: <the prune list>` and `state: absent`. The fact won, and the prune removed the
  aliases it had just refreshed. Pass the task file's inputs only through `vars:` on the import;
  `tests/unit/test_ssh_config.py` fails if a playbook sets them as facts.
- **Every value typed on the `just` line is checked before a shell sees it, and none can be switched
  off.** Each recipe parameter has a literal `[arg(..., pattern=)]` (a pattern naming an assignment could
  be loosened by overriding it), which just holds every value to - every name of a variadic one -
  before anything runs, and an option or word a recipe does not have is refused. A value reaches its
  script only as a positional argument (`[positional-arguments]`), never as its text, and what goes into
  JSON is checked again there (`tools/check names`, `project` and `session`, `init`'s `only`), then
  travels to Ansible as JSON. The one free-text value is `just settings --value`'s, refused by its pattern only
  for a control character, so just never says a value back, and held to the setting's type by `tools/settings`;
  no refusal of `just settings` says the words given back, in case a secret was typed as one. VM names start with a letter or a digit: a recipe without options takes a
  word that starts with `-` as a name, so `--project x` after `just destroy` would otherwise name a VM
  `--project`. A recipe's words are a `*` parameter, even where it takes one: a parameter with a default
  lists as `help RECIPE=''`, make's spelling, which just refuses, and leaves the next word to run as a
  second recipe (`just help deploy` with `test` after it ran every test). Named in the singular (`help`,
  `code`, `docs`, `vagrant`, `clean`), it takes one word and the script refuses a second;
  `test_justfile.py` holds both. The rules between options - `--tags` beside names means `--only configure`, and without names needs it,
  since configuration without names reaches the fleet and a bare deploy does not; `--check` takes no
  `--only`; `--start` only where something is started - are the first lines of each script, said before
  any check or tool. `just deploy configure`, a stage where the names go, asks at a terminal whether the
  stage or a VM so named was meant, and is refused without one, since a bare deploy creates the VMs it
  names; beside `--only` or `--check` it is a name, and a recipe's name is a VM's like any other
  (`just deploy test`). A recipe parameter never comes from the environment: an exported `TAGS` or
  `TYPE` changes nothing. `just init` takes `--token-secret-file` and the exported
  `CLAUDE_ON_PROXMOX_TOKEN_SECRET`, never the secret itself: `--token-secret` is no option, and a word
  after its options - make's `TOKEN_SECRET=...` - is caught by `EXTRA` and refused without being said
  (an unknown recipe or a pattern refusal would print it). `--project` is a GitHub repository name, so
  `/`, `.` and `..` are refused, and `code.yml` repeats the check for a direct `ansible-playbook` run.
  `--session` is a Claude Code session ID, lowercase hex in groups of 8-4-4-4-12 and nothing else, and
  only `just code` takes it.
- **There is deliberately no `LIMIT`.** `--limit` is applied before the plays run, and these VMs
  only enter the inventory once discovery has found them, so it could never match.
- **`gitleaks --staged` scans the index**, which equals HEAD in a CI checkout — i.e. nothing. CI
  scans the tree *and* the commits a push or pull request brings in, both scoped by `.gitleaks.toml`:
  the tree alone misses a secret committed and then deleted, which stays in the published history.
  gitleaks exits 0 when git fails to read a range, so the workflow checks the range with `git` first.

## Conventions

`CONTRIBUTING.md` is authoritative. The ones ansible-lint's production profile enforces and that
catch people out: role variables must be prefixed with the role name (`var-naming[no-role-prefix]`,
including vars set inside a role's own Molecule scenario), task names in an included file are
prefixed with the file name (`- name: node | Install Node.js`), and every role variable needs both
a `defaults/main.yml` entry and a `meta/argument_specs.yml` entry, with `no_log: true` if it can
hold a secret. Commits follow Conventional Commits; the body explains *why*. Pull request titles
start with the kind of change in brackets - `[feat]`, `[fix]`, `[cleanup]`, `[docs]`, `[test]`,
`[ci]`, `[build]` - then a short imperative summary, never a `feat:` prefix.
