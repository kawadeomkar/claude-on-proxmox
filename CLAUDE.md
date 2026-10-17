# CLAUDE.md

Ansible that creates Ubuntu VMs on a Proxmox VE host and configures them as Claude Code
development boxes. The wiki in `docs/` is the documentation - the guides are the user manual,
`docs/architecture/` the design, numbered by section - `README.md` is the short getting-started
page, and `CONTRIBUTING.md` has the setup and conventions. This file is the part that is easy to
get wrong.

## Always run tooling through the virtualenv

Every tool is pinned in `pyproject.toml`, locked with all it pulls in in `uv.lock`, and lives in
`.venv/bin`. Use `make`, or call `.venv/bin/<tool>` directly. Never a system-wide `ansible`,
`ansible-lint` or `molecule`. uv is the one tool on `PATH` by design: it only builds `.venv`
(`make venv`) and the lock (`make lock`, after editing a pin; never edit `uv.lock` by hand).

Molecule resolves `ansible-playbook` from `PATH` and only *appends* the virtualenv, so it must be
**prepended** or a different ansible-core is silently used. The `molecule` targets already do this;
a hand-rolled `molecule test` will not.

```
make venv            # sync .venv to uv.lock (uv sync --locked); make lock after editing a pin
make lint            # uv lock --check, yamllint --strict, ansible-lint (production), ruff check + format
make syntax          # --syntax-check on deploy.yml and playbooks/*
make unit            # pytest, tests/unit/
make molecule MOLECULE_ROLES="proxmox_vm"      # one role scenario
make molecule-scenario SCENARIO=provision      # one playbook scenario
make test            # everything, including pre-commit — the CI gate
make deploy          # template (if missing) + provision + configure + the three sign-ins + VS Code, all idempotent
make destroy VM_NAME=alpha        # release the VM from Claude, GitHub and Tailscale, then delete it
make claude-login VM_NAME=alpha   # sign Claude in by the browser (page opens here, comes back over an SSH forward), start Remote Control
make github-login VM_NAME=alpha   # sign gh in to GitHub by the device flow (page opens here, code on the clipboard), then clone private repos
make tailscale-login VM_NAME=alpha   # sign the VM in to Tailscale by its web login (page opens here), one VM after another
make configure VM_NAME=alpha TAGS=claude_code   # re-run one role, e.g. to push an API key
make list            # read-only: the VMs this project created, and their addresses
make ssh-config      # refresh the SSH alias per VM on this machine; fleet-wide also prunes
make code VM_NAME=alpha [PROJECT=repo]   # refresh one alias, check the folder over SSH, open it in VS Code with a Claude terminal
make logs            # the run logs, newest first: read these instead of asking for terminal output
make docs            # regenerate the wiki's reference pages; make docs-serve previews the site
```

**A change to behaviour changes the wiki in the same commit.** The page for it is in `docs/`
(`docs/README.md` maps them; `docs/architecture/` for the design, cited by section number). The
reference pages are generated from `argument_specs.yml`, `local.yml.example`, `vault.yml.example`,
`inventory/group_vars/all/defaults.yml`, the roles' `defaults/main.yml`, `vars/main.yml` and task files
(rendered by docsible), and the Makefile's `## ` text: edit those and run `make docs` (the pre-commit
hook does too), never the generated section; a task change is a docs change. A new
setting goes in an example file and a new role default in `argument_specs.yml`, or the page misses it.
`tests/unit/test_docs.py` fails on a stale or incomplete page, a broken link or anchor, a page that
renders differently on GitHub and on the site, an unlinked cross-page `§`, or a cited `docs/` page or
`§` section that does not exist. Name repository files in backticks there, never by a relative link
out of `docs/`, which breaks on the site. The site's Markdown drops the rest of a page at a `</` in a
code span, so write `</dev/tty` as `< /dev/tty`; it also needs a blank line before a list and four
spaces before a nested one. Only `docs/README.md` and `docs/`'s folders are the wiki: anything else at
the top of `docs/` is a local working document, ignored and refused by the tracked-file guard.

Every target that runs Ansible, Molecule included, writes its whole run to
`.logs/<YYYYMMDD-HHMMSS>-<target>.log` through `ANSIBLE_LOG_PATH`. When a user says they ran
something, read the newest log for that target before asking them to paste anything. Logs name the
Proxmox host and VMs by address, so `.logs/` is git-ignored and blocked by
`tests/check_no_local_files.sh`; a log file is never committed, quoted into a doc, or attached to a
pull request. The file name is built from the date and the target only, never from `VM_NAME`,
which is the one value a user types that reaches the shell.

Scenarios are slow and disk-hungry. Run the ones your change touches, one at a time, rather than
`make test`, until you are ready to finish.

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
  (`roles/tailscale/tasks/web_login.yml`), run by `make github-login`, `make claude-login`, `make
  tailscale-login` and `make deploy` - open a page (and GitHub's copies its code) on `localhost`, which in
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
  they can prompt (names given, or `make deploy`) and all at once for a bare report, and the flows'
  one-VM guards read `ansible_play_batch` - `ansible_play_hosts` ignores serial. Under serial a failed
  VM is a whole batch failed, which ends the run, so the plays rescue a failure, say it, and raise it
  in a later non-serial play, which `make deploy` skips (`claude_vm_login_in_deploy`): deploy goes on
  past a failed sign-in. `rescue` covers failed tasks only: an unreachable VM is caught by a non-serial
  play before the sign-in, which pings each VM with `ignore_unreachable` and records it as failed. The
  block's condition sits on the task inside it, never on the block: a block's `when` is its rescue's
  too, read again for each task, and the rescue sets the very fact it tests, which skipped the message
  after it. The rescue re-raises `pause`'s abort (Ctrl-C, then A), which ends the run. The work after a sign-in -
  Remote Control's start, the github_projects role - runs in a follow-on play after the last prompt;
  its failure is recorded in `claude_vm_configure_failures` and raised even under `make deploy`, by
  deploy's last play, since it is not a failed sign-in and the VM is not done.
  `tests/unit/test_sign_in_plays.py` runs both playbooks, and deploy's sign-ins as deploy.yml has them,
  against stand-ins, a VM whose ssh exits 255 among them. `make configure` never prompts.
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
  machine to open the page (`page_opener` finds none), or `make` itself run over SSH (`SSH_CONNECTION`: the
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
  `make code`'s SSH error go through it too). A `/dev/tty` probe cannot tell an
  attended run - Ansible runs lookups in workers detached from the terminal - so `pause` returning
  nothing is the skip. `make deploy` decides GitHub's fifteen-minute device flow and Tailscale's web
  login VM by VM, from facts on each VM: a Claude sign-in nobody answered (`claude_code_login_skipped`:
  Enter, or a browser that never came back) leaves it. With `github_login_auto_start` /
  `tailscale_login_auto_start` (default true) every other VM starts at once; off, each is asked by
  `*_login_ask` - Enter signs in one whose Claude sign-in was answered (`claude_code_login_answered`) and
  `n` leaves it, while one that showed no Claude sign-in (signed in already, an API key) signs in on `y`
  only - never one fact for the whole run, which made one answer start every VM's flow. A run with no
  terminal gets `deploy_sign_in: false` from the Makefile (`NO_TTY_ARGS`), since sign-ins that start by
  themselves would otherwise wait for a browser nobody watches. The path unit that starts Remote
  Control when a login lands is `PathChanged=`, never `PathExists=`, which restarts a stopped service
  instantly, and exists only while the login is the one thing missing (docs/architecture/roles.md §5.5).
- **Tests never touch a real Proxmox, GitHub, Tailscale or hypervisor.** `tests/molecule/` holds the
  fakes: a stateful PVE API with a guest agent, a stateful `qm`, a `virt-customize`, a GitHub API over
  local bare repos (the authenticated endpoints included), a stand-in `gh` installed ahead of the real
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
  static binaries, the snap - is reported and left alone, off too. `tailscale-joined` holds the node key
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
  rescue appends to `claude_vm_configure_failures`; a last play raises it, skipped under deploy
  (`claude_vm_configure_in_deploy`), whose own last play raises it after VS Code. A failed install takes
  its repository back out (an unreachable source fails every apt refresh, `common`'s first), and the role
  has no handlers (one would run outside the rescue). Check mode skips what needs the package: `apt`
  cannot find a package from a repository the same `--check` run adds. Tests reach no Tailscale service
  and never this machine's real CLI: `tailscale` is among the harness's failing stand-ins, every scenario
  that runs the role installs `fake_tailscale.sh` in `prepare.yml` and turns log upload off, the
  Vagrantfile is keyless with logs off, and a guard holds each - and the role's scenario to `ROLES` and
  CI's matrix. The template is never signed in to anything.
- **The project writes exactly two things outside the repo on the controller:** `~/.ssh/known_hosts`
  and the managed SSH config `~/.ssh/claude-on-proxmox.conf`, plus one `Include` line at the top of
  `~/.ssh/config` that is added once and never removed. Nothing else in `~/.ssh` is ever edited.
  Everything goes through `playbooks/tasks/ssh_config.yml`, which validates each write with
  `ssh -G`, refuses an alias the user's own config already names, and refuses a name or address that
  is not one plain word. A VM's address is whatever its guest agent reports, which the VM chooses:
  `guest_ipv4` accepts only what `ipaddress.IPv4Address` parses, because a newline and a
  `ProxyCommand` after a valid address pass `ssh -G` and run on this machine. A block is only ever written
  for a tagged VM, holds only what ssh reads (no state of this project's - `make code` reads the
  workspace on the VM), and pruning (`ssh_config.yml`) removes blocks, never VMs. The `Include` is
  added only when `ssh -G` says the user's config does not reach the managed file yet (a `${HOME}`
  spelling counts on OpenSSH 9.9+ and not before; an `Include` inside a `Host` block never does),
  and never rewrites or changes the mode of the user's file.
- **`make code` writes in one directory of its own on the VM, and never answers VS Code's security
  questions.** With `vscode_claude_terminal` it writes a `.code-workspace` file in
  `<projects>/.claude-on-proxmox/`, beside the repositories and never in one, whose `folderOpen` tasks
  run `claude` in a terminal and a login shell split to its left (one presentation group); with `vscode_session_notes` too, `session/<vm>-session.md` there (0600,
  0700 directory), and a second `folderOpen` task, "Session notes", run beside the Claude task, which
  rewrites only the notes' section between the live markers each time the window opens and opens them.
  Never make it the Claude task's `dependsOn`: VS Code misses a workspace-file task when it looks a
  dependency up and asks every task provider instead (npm, gulp running `gulp`, grunt, jake), which held
  Claude back 7.5 s live; `test_code_session.py` fails on any `dependsOn`. Its bash runs the Python part
  in the background, waits 5 s at most and ends `exit 0`, so the notes always open; never `exec` into
  Python. The notes' directory is a
  second workspace folder only where the window's own folder contains it: VS Code stores the folders a
  user trusted, not their parents, so a folder added to a trusted workspace drops it to Restricted Mode.
  `make code` slurps the notes to carry their live section over, so an unchanged VM rewrites nothing.
  VS Code runs that task only in a trusted window and once the user has allowed automatic tasks;
  `code.yml` reads `task.allowAutomaticTasks` from the user's VS Code settings to say which to
  expect, and must never write it, set trust, or lower `security.workspace.trust.*` or
  `terminal.integrated.allowInUntrustedWorkspace`. The script is fixed text in `code.yml`'s vars;
  anything variable reaches it as an argument. Every name `code.yml` sets starts with `code_`, a prefix
  no role uses: a role's fact or register outranks a play var in the same `make deploy`, and no task var
  shares a fact's name. In `make deploy` (`code_in_deploy`) every task of the window step is in one
  `block` whose `rescue` says any failure with the `make code` line (`make deploy ...
  proxmox_vm_start_existing=true` for a VM the deploy left stopped) and ends the play; outside it the
  rescue fails again. A fleet's deploy ends at code.yml's first task with each VM's line.
  `tests/unit/test_code_session.py` runs it (once at a pseudo-terminal, for Ctrl-C), runs the window
  step's own tasks with stand-ins for `which` and `code` first on PATH, and holds
  every task in `code.yml` to an allowlist: read-only on this machine (plus `which`/`code` and the
  alias through `tasks/ssh_config.yml`), or `stat`/`getent`/`find`/`slurp` and the four writes (the
  workspace and its directory, the notes and theirs) delegated to the VM; a `shell`, a writer in a
  `block`, or a `connection` fails it by name. The helper is run against stand-ins with nothing else
  on PATH: never a login shell there, which would put this machine's own `code` and `tailscale` back. The `provision`
  scenario, which runs `code.yml`, points `vscode_user_settings_file` into the ephemeral directory,
  since the message depends on it, and asserts that file's bytes and mtime after every run.
- **Tests never write into the developer's `~/.ssh`.** The `configure` scenario runs the real
  playbook and its `localhost` is the developer's machine, so every playbook scenario points
  `claude_ssh_config_file` and `claude_ssh_user_config` into `MOLECULE_EPHEMERAL_DIRECTORY` with
  the `Include` off, the Vagrantfile sets `claude_ssh_config: false`, and
  `tests/unit/test_ssh_config.py` fails if any of them stops.

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
release finds a VM by name; with `destroy_release` it first starts each stopped VM through the API and
reads every VM's address from its agent - asking the ones it started whatever the cluster listing says
(`claude_vm_just_started`). A VM it started is stopped again (`tasks/vm_stop.yml`) when the
run then fails before deleting (a rescue in that play) or its deletion fails: the delete play rescues a
failed deletion and the last play raises it, after the stop and the report - a play in which every host
fails ends the run, which once left a lone VM running. A release play then connects to each VM over SSH
(`add_host` gives each VM its SSH settings as host vars; the delete play forces `ansible_connection: local`)
and runs the `release` entry point of
`claude_code`, `github_projects` and `tailscale`, each in a block whose rescue records a failure: a
release never stops a deletion. A VM with no address or that does not answer is ended and deleted as it
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
without it means the one default VM, so running both made a bare `make deploy` create the default VM
and then configure the whole fleet. Standalone `configure`, `check`, `list` and `claude-login` stay
fleet-wide without `VM_NAME`.

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
vars are not visible there. The
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
back. Keep the two drop-ins with two owners: `10-` is `common`'s hardening, `20-` is VS Code's
forwarding and keepalives.

**The alias play reads a fact the configure play set last.** `configure.yml`'s second play ends
with a `getent` of `vm_user` and a `claude_vm_workspace` fact, both tagged `always`; the third play
writes an alias only for hosts that have that fact, so a host that failed a role never gets one, and
the workspace is the VM user's real home rather than a guess from the controller (Ansible's own
`user_dir` fact is the *connecting* user's home, which under Molecule is root's).

**NEXT STEPS is recorded by each report, never read off the output.** `callback_plugins/claude_on_proxmox.py`,
the stdout callback `ansible.cfg` names, prints NEXT STEPS after the recap from `set_stats` records only -
`{vm: {matter: line}}` under five `*_next` stats - each made straight after its report from the facts the
message is built from: the line while something is left, `''` once nothing is, the last record of a VM and
matter winning across plays. Do not decide from a message's text: a stopped Tailscale names no `make`
command, and "make deploy signs it in next" names one with nothing to do. A new report needs its record, and
`tests/unit/test_attention_callback.py` fails without it. Tests never load the callback (an empty
`ansible.cfg` and the JSON callback); the harness shows custom stats, and `_left_to_do` reads the records.

**Discovery is a playbook, not the `community.proxmox` inventory plugin.** The header of
`discover.yml` records the trade-offs; the swap is a live design question, not an oversight.

**Fleet listings read `/cluster/resources`, never an unfiltered `proxmox_vm_info`.** Unfiltered, the
module asks every node that owns a VM for its VM list and fails on any error, so one node that is
down broke `deploy`, `configure`, `list` and `destroy` for every VM on the others.
`playbooks/tasks/cluster_vms.yml` fetches the listing with `uri` (`no_log`, since the request
carries the token); `proxmox_vm_info` is called only with a `name` or a `vmid`. The shared pieces
live in one place each, so the playbooks cannot drift: `proxmox_api_module_defaults` for
`module_defaults`, `claude_vm_requested` for `VM_NAME` narrowing, the `guest_address` filter with
`proxmox_nic` for a VM's address, and the `proxmox_access_denied` filter for "the token was
refused" - which anchors 401/403 to proxmoxer's status position, because Proxmox names the VM in
the error text and `VM 4013 is not running` otherwise reads as a 401.

## Things that have bitten this repo

- **ansible-core 2.21**: a non-boolean conditional is a fatal error, not a warning.
- **`-e key=value` splits extra-vars on whitespace.** `VM_NAME="alpha beta"` silently provisioned
  half the fleet. The Makefile passes JSON (`-e '{"vm_name": "..."}'`) to keep the value whole.
- **Proxmox tags** arrive `;`-joined and lowercased on the wire, but `proxmox_kvm` *sends* them
  comma-joined. Anything parsing tags must handle both.
- **`vars_prompt` silently takes its `default:` without a terminal.** It does not hang — ansible-core
  warns ("Not prompting as we are not in interactive mode") and uses the `default:`, or `None` when
  there is none. So every prompt needs a `default:`, and it must be the safe answer (`destroy.yml`
  defaults to `no`).
- **A `vars_prompt` sees the play's vars and the extra vars, nothing else.** It is templated before any
  host is in scope, even when `-e` answers it, so a prompt naming `claude_vm_names` (group_vars) failed
  every `make destroy` before it asked. `test_release.py` runs destroy with no inventory variables at all.
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
- **A skipped task still overwrites its register.** A `when` on destroy's second cluster listing left
  `claude_vm_all` without its listing whenever no VM had been started, and destroy failed before it
  began; register only in a task that always runs, or read it with a `default`.
- **A `set_fact` outranks the `vars:` of a later `import_tasks`.** `ssh_config.yml` built its
  refresh list as a `claude_ssh_vms` fact, then imported `tasks/ssh_config.yml` a second time with
  `claude_ssh_vms: <the prune list>` and `state: absent`. The fact won, and the prune removed the
  aliases it had just refreshed. Pass the task file's inputs only through `vars:` on the import;
  `tests/unit/test_ssh_config.py` fails if a playbook sets them as facts.
- **Every value typed on the `make` line is checked before a shell sees it.** `VM_NAME` and
  `PROJECT` each have a `*-name-check` target that reads the value through the environment with
  `$(value ...)` and refuses anything outside its character set with a shell `case`, then the value
  travels to Ansible as JSON. `PROJECT` is a GitHub repository name, so `/`, `.` and `..` are refused
  as well, and `code.yml` repeats the check for a direct `ansible-playbook` run.
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
