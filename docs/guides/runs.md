# During a run

## What `just deploy` shows

At a terminal, `just deploy` draws the run instead of streaming every task: a few lines at the bottom of the
screen, redrawn in place, under what the run will do, which it says first.

```
  Template "ubuntu-24.04-cloudinit" is already built. Then provisioning and configuring alpha.
  alpha is up at 192.0.2.51
claude-on-proxmox  deploy alpha · started 18:22

 ✓ template     ████████████████████    3/3                    no changes                     0.7 s
 ✓ provision    ████████████████████    5/5    2 changed       VM 104 · 192.0.2.51             1:00
 ⠴ configure    █████████████░░░░░░░    8/12   3 changed                                       0:58
     ⠴ common            █████████████████░░░    6/7    3 changed                              0:38
         ● Install baseline packages                                                30 s
         – Set the time zone                                                     skipped
         ✓ Harden sshd                                                             0.6 s
         ● Mask each unit                                                          0.7 s
         ⠴ Write the motd                                                           0:02
     ○ dev_tools         3 tasks
 ○ sign in      4 tasks
 ○ VS Code      3 tasks

⠴ 1:59 · stage 3 of 5 · .logs/20261025-182200-deploy.log
```

- **Stages**: deploy's five - template, provision, configure, sign in, VS Code - each with a bar of the tasks
  it has reached, skipped ones included, of those it will reach, what it changed, and its time.
- **Steps**: the running stage's roles, or its sign-ins (Claude, GitHub, Tailscale), the same way, with the
  running step's last five tasks under it: the newest with its timer, a skipped one dim. A task that several
  VMs run says each VM's state, or how many have finished when they do not fit.
- **Above it, for good**: what the run reports, whole - each VM's address, a page to approve, a prompt, the
  diffs of `--check`, a warning, a failure with what it said and where the log has the rest. A prompt has the screen
  to itself while you answer it; anything else you type during the run is not echoed, so it cannot push the
  region out of place.
- **The tab's title** says where the run is (`deploy alpha · configure 59%`, `· waiting for you`, `· done`,
  `· failed`, `· stopped` after Ctrl-C),
  and the terminal's bell rings when the run starts to wait for you in the browser - Claude's page, GitHub's
  code, Tailscale's page - and when it ends.

When the run ends, the region gives way to a summary, in place of PLAY RECAP: each stage's time, tasks and
changes, with its steps' times (your time in the browser said apart); the last word each part of the run
had on each VM, a line each; the slowest tasks; the totals and the log. NEXT STEPS follows it.

```
claude-on-proxmox  deploy alpha · done in 6 min 12 s · 18:28

STAGES          TIME    TASKS  CHANGED
 ✓ template    0.7 s        3        —   proxmox_template 0.5 s
 ✓ provision    1:00        5        2   VM 104 · 192.0.2.51 · proxmox_vm 1:00
 ✓ configure    3:58       96       41   common 1:12 · vscode_server 9.8 s · dev_tools 1:40 · claude_code 52 s
                                         github_projects 8.1 s · tailscale 6.0 s
 ✓ sign in      1:13       12        2   Claude 1:05 (58 s yours) · GitHub 4.2 s · Tailscale 3.0 s
 ✓ VS Code     3.1 s        6        1

ALPHA  192.0.2.51 · VM 104 · new
 alpha is up at 192.0.2.51
 signed in as you@example.com (claude.ai, max plan).

SLOWEST  node | Install Node.js 48 s · present | Wait for the guest agent 45 s

122 tasks · 46 changed · 0 failed · 58 s of it yours · log .logs/20261025-182200-deploy.log
```

Every form of `just deploy` is drawn - `--only`, `--tags` and `--check` too - and only `just deploy`: the
other recipes print as before. Without a terminal - output piped into a file, CI - it prints a line as each
step ends, the reports, and the summary. For the stream as before, give any `-v` (`just ANSIBLE_ARGS=-v
deploy alpha`) or put `ANSIBLE_STDOUT_CALLBACK=default` in front; a run without a log (`LOG_DIR=`) streams too,
since the log is where the stream would go. Either way the run log keeps the whole stream: every task as
Ansible prints it and PLAY RECAP, then the summary, NEXT STEPS and the task timings.

## What a run asks of you

A run stops for you in three ways, and each is set apart from the task output around it by a block of
`#`:

```
################################################################################
##  USER INPUT REQUIRED  -  alpha  -  Claude Code sign-in
################################################################################
Paste the code the sign-in page shows, then press Enter. Enter alone skips this VM.
Code:
```

- **`USER INPUT REQUIRED`**: a prompt to type at - the code Claude's sign-in page shows, or, with
  `github_login_auto_start` or `tailscale_login_auto_start` off, whether to sign a VM in now. `just init`
  asks this way too, for each value a first deploy needs that is still the example's (`just init` in the
  block's title); there, Enter leaves the value for the next `just init`.
- **`ACTION REQUIRED IN YOUR BROWSER`**: a page to approve - Claude's, GitHub's device code, Tailscale's.
  While the run waits for it, it says so once, with the time left, rather than a retry line every few
  seconds.
- **`CONFIRM DELETION`**: `just destroy` names the VMs it is about to delete.

At the end, after the recap (under `just deploy`, the summary), a `NEXT STEPS` block lists what is still
left to do on each VM - a sign-in you skipped, or one left out after it, a sign-in that failed or a VM it
could not reach, each with the command that retries it, a VM left stopped, a Tailscale device to delete
by hand - each once, and only what the run did not settle: a "not signed in" that a later step of the
same `just deploy` signed in is not listed. Nothing is printed when nothing is left. The task timings
come after it, and under `just deploy` in the run log only.

The blocks come from `callback_plugins/claude_on_proxmox.py`, which `ansible.cfg` names; put
`ANSIBLE_STDOUT_CALLBACK=default` in front of a `just` command for Ansible's own output (the prompts keep
their blocks). The run log below holds the same blocks without colour, but never `just destroy`'s
question, which Ansible does not log.

## Run logs

Every recipe that runs Ansible keeps a copy of the whole run in `.logs/`, one file per run,
named after when it started, the recipe and the part it was narrowed to:

```bash
just deploy beta
#   Logging this run to /home/you/claude-on-proxmox/.logs/20261007-203015-deploy.log
just deploy beta --only configure
#   Logging this run to /home/you/claude-on-proxmox/.logs/20261007-204510-deploy-configure.log
just logs                         # the newest 20, newest first
just clean logs                   # delete them all
```

Logging changes nothing in the terminal: Ansible copies its output to the file with a
timestamp on every line, so a run can be read back after the terminal has scrolled away or the
window is gone; under `just deploy` it is also the stream the screen no longer shows. Tasks marked
`no_log` are hidden in the file as on screen, so the API token and the GitHub token never reach it. The files do name the Proxmox host and every VM by address, so
`.logs/` is git-ignored, refused by the tracked-file guard even with `git add -f`, and readable only
by you. Logs older than 14 days are deleted whenever a logged run starts (`LOG_RETENTION_DAYS`).
`LOG_DIR=` turns logging off for one run, `LOG_DIR=/some/where` moves it, and an
`ANSIBLE_LOG_PATH` of your own is used as it is. Molecule runs (`just test`) are
logged the same way. A command you run with `ansible-playbook` directly is not; put
`ANSIBLE_LOG_PATH=.logs/<name>.log` in front of it for the same result.

## See also

- Settings: [Sign-ins and teardown](../reference/settings.md#sign-ins-and-teardown), for the sign-ins a
  run starts, and [Commands](../reference/commands.md) for `just logs` and `LOG_DIR`
- How it works: the blocks, NEXT STEPS and the live output in
  [§7.5](../architecture/custom-code.md#75-callback_pluginsclaude_on_proxmoxpy--the-waits-and-next-steps),
  the run logs in [the security model](../architecture/security-model.md#run-logs)
