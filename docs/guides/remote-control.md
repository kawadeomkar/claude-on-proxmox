# Remote Control

Remote Control lets you reach a VM's Claude Code session from claude.ai and from your phone.
[Remote Control](https://code.claude.com/docs/en/remote-control) runs a server on the VM so its
Claude Code session appears at claude.ai/code and in the Claude mobile app, while the session itself
keeps running on the VM with the VM's filesystem and tools.

## What it needs

It is **on by default**, but there is no switch that makes it work — Anthropic supports exactly one
credential for it, an interactive claude.ai login on a Pro, Max, Team or Enterprise plan. API keys
are **not** supported, and neither is the long-lived token from `claude setup-token`, which can make
model requests but explicitly cannot establish a Remote Control session. On Team and Enterprise, an
organization Owner has to enable Remote Control for the organization before any member's login can
use it; on Pro and Max the login is enough.

## What a run does on each VM

So `just deploy --only configure` derives what to do from the credentials it finds on the VM:

| On the VM | What happens |
|---|---|
| `vault_anthropic_api_key` is set | Off, and it says so. A key outranks the login, so the server would refuse to start. A server an earlier run installed is stopped and removed |
| No key, no login yet | Everything installed, service left stopped with a path unit waiting to start it when you sign in, and you are told how |
| No key, signed in | The session starts and shows up in the app |
| No key configured, but Claude Code on the VM still uses one | Service left stopped, and you are told where that key comes from (`claude auth status` reports it as `apiKeySource`) |
| No key, signed in, but `~/.claude/settings.json` switches the feature off | Service left stopped, and the keys are named. A telemetry opt-out (`DISABLE_TELEMETRY`, `DO_NOT_TRACK`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_GROWTHBOOK`), an `apiKeyHelper`, a token or provider in `env`, or an `ANTHROPIC_BASE_URL` pointing anywhere but `api.anthropic.com` each disable Remote Control. Remove it, or keep it and set `claude_remote_control: false` |

How each of these is decided is in
[Remote Control, step by step](../architecture/roles.md#remote-control-step-by-step).

## Signing a VM in

Activating it is therefore: leave the API key empty and sign in once. `just deploy` asks for that
itself, after configuring the VM; for a VM that exists, `just login --only claude` does the same:

```bash
just login alpha --only claude     # the sign-in page opens in your browser: authorise,
                                 # and the browser comes back to the VM by itself
                                 # ... and Remote Control starts; the session appears on your phone
```

The run starts `claude auth login` on the VM, forwards the one port it listens on from this machine to
the VM over SSH, and opens Claude's sign-in page here. You authorise, the browser comes back to claude
through the forward, and the run carries on by itself, waiting up to `claude_login_approval_timeout`
(300 seconds). The page opens only when it is Claude's own sign-in page. How the page and the forward
work is in [the browser sign-in](../architecture/roles.md#the-browser-sign-in).

Once every VM named has had its turn, the run starts the service on each one signed in, in the same run.
Given several names it signs them in one after another; given none it only reports each VM's state.

### When the browser cannot come back

Where the browser could not come back - the port is already taken on this machine, `login_desktop:
false`, nothing here to open a browser with, or `just` run over SSH, where the browser is on another
machine and the page would send it to that machine's own localhost - claude's other page opens instead,
which shows a code once you authorise: paste it at the `Code:` prompt and the run hands it to claude
through a pipe. The code is read with the run log's no_log, so it never lands in `.logs/`. Enter alone
at the prompt skips a VM, and a code claude refuses fails that VM by name once the others have had their
turn; a code copied only up to its `#` (a double-click stops there) is asked for again, and Ctrl-C then
`A` at the prompt ends the run. A sign-in page lasts `claude_login_timeout` plus half an hour: a code
pasted later is said to be too late at once, with the command for a fresh page.

### Sign-in settings

Three variables tune it: `claude_login_args` passes extra arguments to `claude auth login` (`[--sso]`
for a single sign-on login, say), `claude_login_approval_timeout` (300) is how long the run waits for
your browser to come back, and `claude_login_timeout` (120) is how many seconds claude may take to answer
a code you paste. A VM whose Remote Control is off on purpose - `vault_anthropic_api_key`
set, or `claude_remote_control: false` - is reported as such, with nothing to fix.

## Signing in another way

A sign-in made any other way starts the service by itself too. While a VM waits for its first login,
`just deploy --only configure` leaves a systemd path unit, `claude-remote-control.path`, watching for the login file;
the moment one lands - from `just login --only claude`, the Claude terminal of `just code`, or by hand with
`ssh -t dev@<address> "bash -lc 'claude auth login'"` - it starts the server. The next `just deploy --only configure`
enables the server for boot, runs the stay-up check below, and removes the path unit, so a server you
stop later stays stopped ([the path unit](../architecture/roles.md#the-path-unit) says why). By hand,
`bash -lc` works on any VM. On a VM last configured by an earlier version it is needed: only a login
shell put `~/.local/bin`, where the native installer puts `claude`, on `PATH` there, so a bare
`ssh -t dev@<address> claude auth login` was `command not found`. sshd now puts it on the PATH of every
ssh session of the VM user ([§5.3](../architecture/roles.md#53-common--os-baseline)).

`just login --only claude` decides from the same `claude auth status` fields as `just deploy --only configure`, so a VM
signed in another way — a Console account, a `claude setup-token` token, an API key — is told by both
what is in the way, and never signed in over it.

## Turning it off

Set `claude_remote_control: false` in `local.yml` to turn it off. Like adding a key, that stops,
disables and removes a server an earlier run installed. It is also the right answer when a privacy
setting in `claude_code_settings.env` is there on purpose: none of the above fails the run, so the
other roles still run, but the service stays stopped until one or the other changes.

## Keeping the server running

The first time `claude remote-control` runs, Claude Code explains what Remote Control does and asks
`Enable Remote Control? (y/n)`. A systemd service has no terminal to answer it, so `just deploy --only configure`
records that answer in `~/.claude.json` for you, alongside first-run onboarding and trust for
`~/projects`: leaving Remote Control on is taken as the yes.

The server runs as the `claude-remote-control` systemd unit, as the VM user, from `~/projects`.
systemd restarts it whenever it exits, whatever the exit status - server mode gives up after about
ten minutes without a network - first after 10 seconds, backing off to five minutes, with no start
limit: the server comes back on its own after an outage, and an expired login costs a retry every five
minutes rather than a spin. `systemctl stop` still stops it. The unit's settings, and why each, are in
[the service unit](../architecture/roles.md#the-service-unit).

On a signed-in VM, `just deploy --only configure` then waits 20 seconds and fails, with the `journalctl` command to
read why, unless the server process it started is still the one running. That is there to catch a
server refused at startup, for its login, plan or organisation settings, during the run rather than
when you reach for your phone. A re-run that changes nothing leaves a running server alone and skips
the wait; a changed unit (a new permission mode, say) restarts it, once, and waits again. After a
sign-in, `just login --only claude` and `just deploy` run the same check; a server that does not stay up
fails the run at its end - after VS Code has opened, in a deploy - with
`just deploy alpha --tags claude_code` to retry it, while the login itself is kept.

## How much of the VM a remote session can reach

A Remote Control session *is* the VM, not a sandbox with a copy of your repo — it runs as the login
user, with that user's filesystem and the tools this project installed. Since `common` grants
passwordless sudo, shell commands already reach the whole operating system.

The one thing that is scoped is Claude Code's **file** tools, which stay inside the session's working
directory. Widen them where you want to, through the settings this role already manages:

```yaml
# inventory/group_vars/all/local.yml
claude_code_remote_control_dir: /home/dev/projects        # where sessions open
claude_code_remote_control_permission_mode: acceptEdits   # fewer approvals from the phone
claude_code_settings:
  permissions:
    additionalDirectories: ["/etc", "/var/log", "/home/dev"]
```

`claude_code_remote_control_permission_mode` is the mode remote sessions start in. The service
passes it as `--permission-mode`, which [outranks](https://code.claude.com/docs/en/permission-modes)
`permissions.defaultMode`, so setting `defaultMode` in `claude_code_settings` changes nothing for a
session from the app: it only sets the mode for `claude` you start on the VM yourself, such as over
SSH. Once a session is open, the app can switch it between Manual, Accept edits and Plan; Auto and
Bypass permissions cannot be selected there. The value is passed to Claude Code unvalidated, so any
mode it accepts works; one it does not stops the server at startup, and on a signed-in VM
`just deploy --only configure` then fails with the command that shows why.

## An API key instead of a claude.ai login

**Adding an API key to a VM that was built without one** is the common case. Put it in the vault, then push
just that role — no SSH:

```bash
just vault                                 # set vault_anthropic_api_key
just deploy alpha --tags claude_code       # writes ~/.claude/settings.json on the VM
```

That makes `claude` on the VM authenticate against the Anthropic API. It does not start anything: Claude
Code is a terminal program, so open it with `just code alpha`, whose terminal starts `claude`, or
`ssh alpha` and run `claude` there.

Note this also **turns Remote Control off** for that VM, and the run says so. The two credentials are
mutually exclusive: an API key outranks the claude.ai login, and only that login can establish a
session you can reach from claude.ai or the mobile app. If a previous run had installed the Remote
Control server, this run stops and disables it and removes its unit. If Remote Control is what you
want, leave the key empty and sign the VM in, as at the top of this page.

**Going back** works the same way: clear `vault_anthropic_api_key` and re-run. The role removes the
`ANTHROPIC_API_KEY` it wrote from `~/.claude/settings.json` and leaves the rest of the file alone. A
key set through `claude_code_settings.env` is yours, and stays. The VM then needs its claude.ai login:
`just login alpha --only claude` signs it in and starts Remote Control (`just deploy` does both too).

## See also

- Settings: [Claude Code](../reference/settings.md#claude-code) and
  [Secrets: Claude Code](../reference/settings.md#secrets-claude-code)
- Role: [`claude_code`](../reference/roles/claude_code.md)
- How it works: [§5.5](../architecture/roles.md#55-claude_code--install-claude-code), from
  [Remote Control](../architecture/roles.md#remote-control) on
