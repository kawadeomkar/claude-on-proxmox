# During a run

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
  `github_login_auto_start` or `tailscale_login_auto_start` off, whether to sign a VM in now.
- **`ACTION REQUIRED IN YOUR BROWSER`**: a page to approve - Claude's, GitHub's device code, Tailscale's.
  While the run waits for it, it says so once, with the time left, rather than a retry line every few
  seconds.
- **`CONFIRM DELETION`**: `make destroy` names the VMs it is about to delete.

At the end, after the recap, a `NEXT STEPS` block lists what is still left to do on each VM - a sign-in
you skipped, a VM left stopped, a Tailscale device to delete by hand - each once, and only what the run
did not settle: a "not signed in" that a later step of the same `make deploy` signed in is not listed.
Nothing is printed when nothing is left. The task-timing summary comes after it.

The blocks come from `callback_plugins/claude_on_proxmox.py`, which `ansible.cfg` names; put
`ANSIBLE_STDOUT_CALLBACK=default` in front of a `make` command for Ansible's own output (the prompts keep
their blocks). The run log below holds the same blocks without colour, but never `make destroy`'s
question, which Ansible does not log.

## Run logs

Every `make` target that runs Ansible keeps a copy of the whole run in `.logs/`, one file per run,
named after when it started, the target and its `TYPE`:

```bash
make deploy VM_NAME=beta
#   Logging this run to /home/you/claude-on-proxmox/.logs/20261007-203015-deploy.log
make deploy VM_NAME=beta TYPE=configure
#   Logging this run to /home/you/claude-on-proxmox/.logs/20261007-204510-deploy-configure.log
make logs                         # the newest 20, newest first
make clean TYPE=logs              # delete them all
```

Logging changes nothing in the terminal: Ansible copies its output to the file with a
timestamp on every line, so a run can be read back after the terminal has scrolled away or the
window is gone. Tasks marked `no_log` are hidden in the file as on screen, so the API token and
the GitHub token never reach it. The files do name the Proxmox host and every VM by address, so
`.logs/` is git-ignored, refused by the tracked-file guard even with `git add -f`, and readable only
by you. Logs older than 14 days are deleted whenever a logged run starts (`LOG_RETENTION_DAYS`).
`LOG_DIR=` turns logging off for one run, `LOG_DIR=/some/where` moves it, and an
`ANSIBLE_LOG_PATH` of your own is used as it is. Molecule runs (`make test`) are
logged the same way. A command you run with `ansible-playbook` directly is not; put
`ANSIBLE_LOG_PATH=.logs/<name>.log` in front of it for the same result.

## See also

- Settings: [Sign-ins and teardown](../reference/settings.md#sign-ins-and-teardown), for the sign-ins a
  run starts, and [Make targets](../reference/make-targets.md) for `make logs` and `LOG_DIR`
- How it works: the blocks and NEXT STEPS in
  [§7.5](../architecture/custom-code.md#75-callback_pluginsclaude_on_proxmoxpy--the-waits-and-next-steps),
  the run logs in [the security model](../architecture/security-model.md#run-logs)
