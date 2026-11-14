# 7. Custom code

## 7.1 `filter_plugins/proxmox.py`

Four filters for reading Proxmox API output, one for a VM's tailnet address, and two for the managed
SSH config. Their code is `lib/claude_vm/proxmox.py`'s, and the checks of what a VM or a person supplies
(`guest_ipv4`, `ssh_alias_refusal`, `sshd_match_spec`) `lib/claude_vm/checks.py`'s (§7.10); a refusal is a `ValueError`, which
ansible-core says with the filter's name.

**`net_mac(net_config, default=_RAISE)`** — extracts the MAC from `virtio=BC:24:11:0E:72:04,bridge=vmbr0`.
It **raises by default**, because a caller about to act on one specific VM wants to know its NIC is
unreadable. Passing a default turns that into a skip, which is what a caller sweeping the whole fleet
wants: one odd VM (NIC removed in the UI, `net1` but no `net0`) should not abort discovery for
everyone else. The sentinel object distinguishes "no default given" from "default is `''`".

**`guest_ipv4(interfaces, mac=None)`** — reads `network-get-interfaces` output. With a MAC, only the
matching interface is considered, which is what keeps bridges the guest grew later (`docker0`,
`podman`, `virbr0`) from being picked up on a re-run. Loopback and link-local are never returned,
nor an address no NIC answers at: `0.0.0.0`, which as a destination is the controller itself,
multicast, and the reserved `240.0.0.0/4` with the broadcast address in it.
Returns `None` when nothing is available yet, so a `until:` loop can poll on it. The agent runs in
the guest, so the VM chooses what it reports, and the result becomes `ansible_host` and an alias's
`HostName` on the controller: only a string that `ipaddress.IPv4Address` parses counts, and
anything else - a newline and a `ProxyCommand` after a valid address, a leading `-` - is no address.

**`tailnet_ipv4(addresses)`** — the first entry of `tailscale status --json`'s `Self.TailscaleIPs`
that parses as one IPv4 address inside `100.64.0.0/10`, else `""`: tailscaled on the VM reports it, so
it is held to the same rule as `guest_ipv4`.

**`guest_addresses(vms, configs, networks, nic="net0")`** — `net_mac` and `guest_ipv4`, composed the one
way every reader of the fleet's addresses needs (`tasks/vm_addresses.yml`, which discovery, `just status`,
`just code`, destroy and the alias refresh import): the cluster listing's VMs, each with an `address`, the
IPv4 its agent reports on the interface whose MAC is its config's `nic`, from the `/config` and
`/agent/network-get-interfaces` replies a `uri` loop registered, one per VM in the same order. A VM whose
request failed or was skipped, that is stopped, whose agent has not answered, that has no such NIC or
whose reply has another shape gets `""` — never an error — so a caller sweeping the fleet skips that VM,
and no address is guessed without the MAC; only lists that cannot be paired raise. `vm_addresses.yml`
passes `proxmox_nic`: reading `net0` regardless ignored the NIC the role honours, and left a VM whose LAN
NIC is `net1` out of the fleet.

**`proxmox_access_denied(msg)`** — whether a community.proxmox error means the token was refused:
`40[13]` only in proxmoxer's status position (`"<status> <reason>: <detail>"`, after the module's own
`"...: "` prefix), or the words Proxmox uses for it. The unanchored `40[13]` it replaces also matched
a VMID in the detail — `500 Internal Server Error: VM 4013 is not running` — which failed
the fleet listing and `just deploy --only configure` for the whole fleet over one stopped VM.

**`ssh_config_blocks(content)`** — the alias blocks in the managed file, as `{name, origin}`, in file order:
`origin` is the host the block records (`# origin: <host>`, from 2.5), `""` for none. **`ssh_config_elsewhere(blocks,
origin, others)`** — the blocks that record another host than the run's, `{name, origin}`; with `others`, only
those of the checkout's other hosts. **`ssh_config_prune(blocks, resources, tag_pattern, hosts, origin,
others)`** — the decision `just deploy --only ssh-config` makes fleet-wide, as data: `prune` (no VM of that name
anywhere on this host), `untagged` (a VM of that name exists without the tag; left alone), `nameless` (tagged VMs
the listing shows without a name, a node that is down), which when non-empty empties `prune` into `held`,
`refused` (a name no alias may have, an address or one of `hosts`, as `ssh_alias_refusal` says, which an earlier
version wrote: removed whatever the listing shows or the block records), `elsewhere` (blocks recording a host the
checkout does not have, whose VM this listing lacks: left, and said; another of `others`' is left unsaid) and
`unknown` (blocks with no record while the checkout has other hosts: left, and said). Pure, so every rule is a
unit test rather than a fake-API fixture.

**`ssh_alias_refusal(name, hosts, chosen)`** — why a VM's name cannot be its alias, or `""`: a name ssh reads
as an address (`192.0.2.10`, `3232235777`, `0x7f000001`: one to four parts between dots, each decimal, octal or
hex, of any size - more than any platform's `inet_aton` takes, so the answer is the same on every machine), one
of `hosts` (`claude_vm_inventory_names`: the inventory's hosts, their `ansible_host` and the API's address), or
a dot (it could be a host's own name, `github.com`) in a name that is not one of `chosen`
(`claude_vm_names_chosen`, the names the person gave). The managed file is included ahead of the user's own
config, so such an alias would take this machine's own connections to that host
([§4.8](lifecycle.md#48-playbooksssh_configyml-and-playbookstasksssh_configyml)).
**`ssh_alias_refusals(vms, hosts, chosen)`** is the same over a list, each refused VM as `{name, why,
unnamed}`, `unnamed` where only the dot stops it: what a fleet-wide refresh leaves out and says.
**`ssh_hostname(vm, domain)`** — where a VM's alias points, its `HostName`: `<name>.<domain>` where the network
resolves VM names (`claude_ssh_host_domain`), else the address its guest agent reported. The alias's block, the
check that the user's ssh config reaches the managed file, and `just code`'s own connection all read it, so the
editor and Ansible connect to the same place.
**`sshd_match_spec(ssh_connection, user)`** — what `sshd -T -C` evaluates its `Match` blocks for: the
user's sessions from where Ansible's own connection comes, read from its `SSH_CONNECTION` (client address
and port, local address and port), or the loopback address where there is none (a container, a local run).
`common` and `vscode_server` both ask sshd with it ([§5.7](roles.md#57-vscode_server--what-an-editors-remote-server-needs-on-the-host)).
**`vms_not_named(vms, names)`** — the listing without the VMs named one of `names`, in order, a VM
without a name kept: how discovery and the alias refresh leave out a VM named after a host of the
inventory, which `add_host` would merge into it ([§4.3](lifecycle.md#43-playbooksdiscoveryml)).
**`built_template(vms, name)`** — the built template named `name` in a listing, or `{}`: the first qemu VM
of that name whose `template` is set, never a VM that merely shares the name, nor a container's template.
How `just deploy` decides whether to build one and reads its node and config, how `just init` checks for
one, and which one `just status` reads and reports. **`duplicate_names(vms)`** — the names more than one VM
of a listing carries, sorted as Jinja's `sort` sorts them, a VM without a name none of them: Proxmox lets
two VMs share a name, so discovery and the alias refresh refuse such a name among the tagged VMs, and
destroy one it was given ([§4.5](lifecycle.md#45-playbooksdestroyyml--confirm-release-delete)).
**`proxmox_tags(tags)`** — a VM's tags as a list of whole tags, each trimmed, an empty one left out, their
case as given: split on `;`, as Proxmox joins them on the wire, or `,`, as `proxmox_kvm` sends them and
some versions echo them back ([§3](README.md#3-the-identity-model)). The one parser of them:
`proxmox_vm`'s lookup, the VMs' marks (`tasks/vm_marks.yml`, Tailscale's record among them), which write them back as
Proxmox gave them, and `just status`'s model; the whole-tag patterns in
`inventory/group_vars/all/defaults.yml` match the joined form.

**`proxmox_vm_drift(config, wanted)`** — what of a VM's settings differs from what `just deploy` would
send, from its config as Proxmox reads it and the settings the role works out (`present.yml`):
`hardware`, each `{setting, wanted, has}` (cores, memory, the balloon minimum, start at boot, and the
disk only when smaller, compared in bytes, since Proxmox stores 1024G as 1T and cannot shrink a disk),
and `cloud_init`, the names of the cloud-init settings that differ (user, SSH keys, network, DNS). A
config not read has none. `just status` says each as a thing to do, with the `proxmox_vm_force_update`
run that sends them and what else it does ([§4.6](lifecycle.md#46-toolsstatus--read-only-drawn-live)).

Covered by the unit tests in `tests/unit/test_filters.py`, and `proxmox_vm_drift` by
`tests/unit/test_status.py`.

## 7.2 `roles/github_projects/library/github_repos.py`

An Ansible module listing a GitHub account's repositories, following `Link`-header pagination - only to
the API's own scheme and host, since the token goes with every request: a next page anywhere else fails the
listing rather than carry it there. With a
token belonging to the queried user it uses the authenticated endpoint and includes private repos;
otherwise it falls back to the public listing, which GitHub documents as public repositories only.
Returns a compact, sorted list carrying `name`, `full_name`, `clone_url`, `ssh_url`,
`default_branch`, `fork`, `archived`, `private` and `empty` (no branch, so nothing to clone: never pushed to, or
created and given no commit yet, which GitHub lists with its creation's push time and a size of 0 - so a repository
GitHub sizes at 0 costs one more request, for its branches, and one with content none; an answer that is not a
list of branches leaves it to clone, as before),
and the account as `user` (`login`, `name`, `id`), from `/user` when the token is the user's own and
`/users/<login>` otherwise, for the git identity the role derives.

Written against the managed VM's Python — `ruff` targets `py310`, not the controller's 3.12+.
Covered by 32 unit tests in `tests/unit/test_github_repos.py`.

---

## 7.3 VS Code's settings reader, `lib/claude_vm/editors/vscode.py`

**`vscode_setting(text, key, default="", unparsable="unparsable")`** — the value VS Code's
`settings.json` gives `key`, for VS Code's plan to say whether the Claude terminal will start on its
own. The file is JSONC: `//` and `/* */` comments and trailing commas are dropped in one pass that
copies strings whole, so a `//` in a URL is not a comment and a commented-out
`"task.allowAutomaticTasks": "off"` does not count. A key is read flat, as VS Code writes it, or
nested, and the later spelling wins, as VS Code's own reader has it: dotted keys expand into a tree in
file order, a nested object replaces what the flat keys built before it, and a key whose path runs
into a value that is not an object is dropped. Empty text gives `default`; text that does not parse,
or is not an object, gives `unparsable`, which is how the plan says a file did not parse rather than
take it for unset. Either way a run that only reports what the editor will do must not fail on the
user's own settings. Read-only by construction; nothing in the project writes that file.

It is part of VS Code's family (§7.10), and no filter: the plan alone calls it, on the text the family's check read
(`code_probe`). Covered by unit tests in `tests/unit/test_vscode_filters.py`.

## 7.4 `filter_plugins/desktop.py` and `filter_plugins/text.py`

**`page_opener(url, browser, found)`** — the argv that opens a sign-in page on this machine, shared by
both sign-ins so the two cannot drift: the first command `BROWSER` names (a `:`-separated list, each
split as a shell would, `%s` replaced by the URL or the URL appended), else `open`, else `xdg-open`,
from what `which` found; `[]` when nothing can, and the task is skipped. Only the first `BROWSER`
command is taken, since an argv holds one. A `BROWSER` command that runs a shell or a Windows command
interpreter (`sh -c`, `cmd.exe /c start`, `powershell.exe`) gives `[]` too - any word before the one that holds
`%s` naming one, so no launcher ahead of it (`env` and its options, `nohup`, `sudo -u`) hides it: it reads
the URL as a command line of its own, and a page's query string - in a page the VM chose - would be a
command run here. The page is then printed for the person to open. Covered by
`tests/unit/test_desktop_filter.py`.

**`controller_run(_)`, `controller_run_state(recorded)`** — the run a Claude sign-in waits for, which the
attempt records on the VM beside its pid, and whether that run is still under way
([§5.5](roles.md#the-browser-sign-in)): a run stopped while it waits (Ctrl-C) runs no `always`, so the next
sign-in of the VM tells a run still at its prompt from one that is gone by this record alone. `controller_run`
ignores its input and gives `{host, boot, pid, start}`: this machine's name; this start of it (Linux's
`/proc/sys/kernel/random/boot_id`, or the start of PID 1 elsewhere); the pid of the run's own
`ansible-playbook` - `multiprocessing.parent_process()` of the worker ansible-core forks for each task, this
process where there is none - and that process's start (ticks since boot from `/proc/<pid>/stat` on Linux,
which a clock set since leaves alone; elsewhere `ps -o lstart=` with `LC_ALL=C` and `TZ=UTC0`, since lstart is
local time in the locale's words, and runs under another zone would read one process's start as two).
`controller_run_state` reads the record back as JSON text the VM may have written anything into, and gives
`{state, host, pid}`: `running` - this machine's, and its process still the one of that start; `gone` - this
machine's, since it last started, and no process of that start at its pid, none or another; `elsewhere` -
another machine's name or another start of this one, which cannot be asked about; `unknown` - nothing
recorded, a record that does not parse or lacks a start, or a start that cannot be read now. Only `gone`
lets the sign-in end what that run left; `host` and `pid` are said only when they are a host name and a pid
(`''` and `0` otherwise). Covered by `tests/unit/test_desktop_filter.py`, and by `test_claude_login.py`,
which stops a waiting run and signs the VM in again.

**`plain_text(text)`** — what a VM printed, safe to quote in a message on this machine: every escape
sequence removed whole (OSC up to its terminator or the end of its line, DCS and the other string
sequences, CSI with its parameters, two-byte ESC sequences, the 8-bit forms), then every control
character but tab and newline. The VM's user controls what `claude`, `gh` and `ssh` print there, and
ansible-core 2.21 writes a failure's `[ERROR]` line to the terminal and the run log raw; removing only
the control characters left a clipboard write's base64 or a window title behind as text. Every message
that quotes the VM's text goes through it: the Claude sign-in's (`claude_code_login_said`), gh's
(`github_projects_login_text`, `_status_said`, `_token_said`) and `just code`'s SSH failure. Covered by
`tests/unit/test_text_filter.py`, and by the sign-in tests, which feed both flows escape sequences.

**`plain_summary(summary)`, `plain_events(callback)`** — what a module returns as warnings or
deprecations, and a failed module's message, are its to choose, and a VM's root makes every module there
answer what it likes; ansible-core turns them into summaries that Display prints as `[WARNING]`,
`[DEPRECATION WARNING]` and `[ERROR]` lines, raw, on the screen - stderr, which the live region lets
through - and in the run log. `plain_summary` rebuilds such a summary (a frozen dataclass of events) with
every string through `visible_text`, so no bidi override reorders a `[WARNING]` line either; inside
`plain_events`, a stdout callback's `_handle_warnings` and `_handle_exception` read the current result's
summaries that way. Both stdout callbacks wrap the two methods in it, so every hook prints them plain;
`agent_warehouse` also passes a diff's text - a file as the VM holds it - through `plain_text`, which
keeps the file's own characters. Covered by `test_attention_callback.py`, which runs a stand-in module returning escape sequences through both
stdout callbacks, by `test_deploy_live.py`, which runs it in a live deploy, and by `test_status.py`'s
test of the status callback's hooks.

**`visible_text(text)`** — `plain_text`, then every Unicode format, line or paragraph separator (bidi
controls and zero-width characters among them) replaced by a space, its lines, tabs and indents kept: a
line that reads in the order it was written. What every live run keeps of a report's lines
(`lib/claude_vm/ansible/engine.py`, §7.5). Covered by `tests/unit/test_text_filter.py`.

**`one_line(text, limit=120)`** — `visible_text`, which removes every control character but tab and
newline (a carriage return is removed, not made a space), then each newline and tab replaced by a space,
runs of spaces collapsed, and the whole cut to `limit`, ending in "…", or never cut with `limit` none; what
a VM printed is passed to it as it is. What `just status` keeps of every value a VM or Proxmox supplies, so
none starts a line of its own in the report or in `--json` (§7.7), and what every live run keeps of a
command's line or a test's name. Uncut, `one_line(none)`, it is how the playbooks and the roles quote what
a VM or a tool printed - a failed module's message, a logout's stderr - in a message of their own.
Covered by `tests/unit/test_status.py`, over a corpus of hostile values, and by `tests/unit/test_text_filter.py`.

**`full_stop(text)`, `retry_line(text, command)`, `and_list(items)`** — the words those messages are built
with, each written once. `full_stop` ends a quoted reason's sentence: as it is when it ends in `.`, `!` or
`?`, else with a `.`, since a module's message often has none of its own ("One or more items failed") and
one that has must not get a second. `retry_line` is that sentence, then `Retry it with: <command>`, the
command ending the line with nothing after it, which would be pasted along with it; a sign-in's failure that
already names its command is said as it is, which the sign-in playbooks decide before they call it.
`and_list` says a list as words do: `alpha`, `alpha and beta`, `Claude, GitHub and Tailscale`, `""` for
none, in `just deploy`'s opening message, `just init`'s report and `just status`'s items. Covered by
`tests/unit/test_text_filter.py`, and the messages by the sign-in tests, which pin them.

**`banner(body, title, host='', topic='')`** — the `#` block that sets a moment the run waits for the
person apart from the task output around it: a rule 80 columns wide (the terminal's width when that is
narrower), a title line naming the VM and the sign-in, the rule again, then the body, never wrapped so a
URL or a command stays whole to copy, and a last newline, so that a prompt's last word (`Code`,
`Answer`) starts a line of its own: a `'\n'` written in the Jinja of a folded YAML scalar reaches the
screen as a backslash and an `n`. Every part goes through `plain_text`. The prompts call it as a filter,
the stdout callback (§7.5) by importing it. Covered by `tests/unit/test_text_filter.py`.

These are `lib/claude_vm/text.py`'s (§7.10), stdlib only; `filter_plugins/text.py` is the adapter that gives
Ansible `plain_text`, `one_line`, `banner`, `full_stop`, `retry_line` and `and_list` as filters, and the
callbacks, the engines, `just status`'s model and `tools/settings` import the module itself. `page_opener` and
`controller_run` with its state are `lib/claude_vm/desktop.py`'s, and `filter_plugins/desktop.py` their adapter.

## 7.5 `callback_plugins/agent_warehouse.py` — the waits, and NEXT STEPS

The stdout callback `ansible.cfg` names (`stdout_callback`, `callback_plugins`), and nothing else does: the
unit harness runs the JSON callback with an `ansible.cfg` that holds only `inject_facts_as_vars = false`
(`harness.ANSIBLE_CFG`) and Molecule sets an `ANSIBLE_CONFIG` of its own, so no output a test pins goes
through it. `just status` runs no playbook (§7.7). It subclasses the default callback and carries the
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
  per VM that it is waiting, with the time left - every check still to come and the delay before each, one
  more than Ansible's own `retries left` counts, as the live run counts it too - instead of a
  `FAILED - RETRYING` line every few seconds that pushed the page off the screen. Six tasks carry the tag, none looped; the device flow's
  `wait_for` has no retries to fold, and carries it for `just deploy`'s live output (below), which tells a
  wait that never retries by how long it runs.
- **What is left after the run**: NEXT STEPS, after PLAY RECAP, from what the report tasks recorded.
  Each of the 26 reports that can say what to do later registers its message and records it straight
  after with `ansible.builtin.set_stats` under its owner's stat - `claude_code_next`,
  `github_projects_next`, `tailscale_next`, `vpn_next` (a VPN's marks, `tasks/vm_marks.yml`), `claude_vm_next`
  (provision, destroy and `just init`) and `code_next` - as `{vm: {matter: line}}`: the message itself while something is left, `''` once nothing is.
  A record about no VM is keyed by words no VM is named, a VM's name being one plain word: destroy's `kept
  SSH aliases`, and `just init`. Its two playbooks each record under a matter of their own: `init_settings`,
  the values still to set or a vault that cannot be opened, and nothing once every value is set, the checks
  deciding what comes next; and `init_check`, each problem with its fix, what could not be checked when
  nothing is wrong, or else `just deploy alpha` - nothing while the GitHub username, which the first names, is
  still the example's - and nothing at all when it ends before checking, a value still the example's.
  The callback keeps them from those tasks' own results, merged as Ansible merges custom stats, never from
  the run's custom stats themselves: ansible-core adds any module result's `ansible_stats` to those, so a
  module on a VM could write another VM's line, or blank one. So no record runs under `no_log`, its own or
  one from above it: `no_log` censors the result the record is read from, and its line would go unsaid.
  `aggregate` merges the records, so the last one of a VM and matter wins across plays: configure's "not
  signed in" is cleared by the sign-in's own report later in the same `just deploy`. The sign-in playbooks
  record what their own plays say, under the role's stat and the sign-in's matter: a VM that could not be
  reached and a sign-in that failed, each with the `just login <vm> --only claude`, `github` or `tailscale`
  that retries it, and the work after a sign-in failing (`remote_control`, or `github` for the
  repositories), with its `just deploy <vm> --tags ...` - so a failure recorded after the role's own report
  replaces that report's line. The first two are one report the three share,
  `playbooks/tasks/sign_in_failed.yml`, and the one below another, `playbooks/tasks/sign_in_held.yml`: the
  only records whose stat is a template - the sign-in's, chosen from a map written out on the record, each
  of whose stats `test_attention_callback.py` holds to the five as it holds a written-out one.
  `github_login.yml` and `tailscale_login.yml` also say, for a VM whose Claude
  sign-in nobody answered, that theirs was not started and what starts it, only where the role would have
  started it (`github_projects_login_due`, `tailscale_login_due`, facts the role's question and sign-in go on
  too): that line takes the place of the role's report of the same sign-out, the one before it - for Tailscale
  only where the VM is merely signed out (`tailscale_login_signed_out`): a page still waiting, an expired login
  or a refused key keeps the role's own line, which says what to do about it. The decision is made in Jinja
  beside the message, from the same facts, because the text cannot carry it: a stopped Tailscale or a missing
  workflow scope names no `just` command, and "just deploy signs it in next" names one with nothing to do. A
  looped report records each VM in a looped `set_stats` keyed by the loop var, so the callback reads no task
  result for this at all. Printed by VM name, the matters in a fixed order (`init_settings`, `init_check`,
  `vm`, `claude`, `remote_control`, `github`, `github_identity`, `tailscale`, `tailscale_record`, `editor`,
  `destroy`), each line once, nothing when nothing is left, and nothing after Ctrl-C, which has no stats event. `profile_tasks`'
  TASKS RECAP follows it: Ansible calls collection callbacks after the stdout one.

The blocks are bright yellow through `Display`, plain under `ANSIBLE_NOCOLOR` or with no terminal; the run
log gets them plain, but never destroy's question, which `vars_prompt` reads with `input()`. The harness
shows custom stats (`ANSIBLE_SHOW_CUSTOM_STATS=1`), so each role's own tests read what it recorded from the
JSON report (`_left_to_do`), in every state they already run.

### `just deploy`, drawn live

`AGENT_WAREHOUSE_LIVE`, which the justfile's recipes drawn live export - `deploy`, `login`, `destroy`, `code` -
set to what was run (`deploy --check alpha`, `login alpha beta`, the run's title), hands every event of the run
to an engine, `lib/claude_vm/ansible/engine.py`, at
verbosity 0 and with a run log to send the stream to; any `-v`, or no log (`LOG_DIR=`), leaves it the stream above.
The callback then keeps only its classic output, which the engine prints through it too. With more than one host the
recipe exports `AGENT_WAREHOUSE_LIVE_HOST` beside it, the run's host, which the header and the summary say after the
title (`deploy alpha · nuc · started 10:02`) and never inside it, whose first word names the declaration. The engine
takes both variables out of the environment when the playbook starts, so nothing the run starts - the editor whose
terminals a later `just destroy` may run in - inherits them. It is `live` when stdout can take the region every live output
shares (§7.8, `can_draw`), and `lines` otherwise. The model is `lib/claude_vm/run/model.py`, which `tui/run.py`
draws and any playbook run can be drawn with, and what a run is made of is its recipe's declaration in
`lib/claude_vm/run/recipes.py`, named
by the title's first word: `just deploy` is the first, and §7.8 says how another recipe joins it.

What the run says above the region is the model's too, as data, in the order said: each report (its VM, its lines
and their marks), each failure (the step, the VM, the task, how long, how, what it said and its failed loop items;
or the causes a raise repeats), what each rescue took, the diffs of a dry run and the warnings and deprecations
modules returned, as the callback prints them, with the log's path. The engine records each and prints what
`tui/run.py` draws from the record, so another renderer of the model reads the same.

- **Stages and steps.** A play's stage is the first of the declaration's stages that names it: by its file and
  play name (`deploy.yml:Fail each VM whose work after a sign-in failed`, deploy's `finish`), else by its file
  (`play.get_path()`): template, provision, configure, `SSH aliases`, the three sign-in playbooks as one stage
  `sign in`, `editor`. A play the declaration does not name - `discover.yml`, which configure and each sign-in
  import, and `deploy.yml`'s first - takes the stage of the play after it, else of the one before, and a test
  holds the declaration to `deploy.yml`'s imports and `--only`'s playbooks. A task's step is the declared step
  whose `blocks` name a block around it, at any depth and whatever role or file it comes from (destroy's release
  of each service, where `claude_code`'s entry points fall in two blocks, the save's and the sign-out's); else the
  declared step whose `files` its task file ends with (`tasks/session_read.yml`: the file the import or include
  nearest the play names, so a file another includes stays its includer's); else, in the sign-in stage, the
  sign-in, for every play of its file; else the step the last task a declared step `starts` at began, in this play
  (`code.yml`'s phases, runs of its own tasks between its imports, a rescue running in the step that failed); else
  its role. An include's tasks are counted in the step they will run in, by the same rule, when the playbook
  starts and when it announces them. `editor`, `just code`'s one stage and deploy's, is declared so: find the VM,
  reach it, read the sessions, the folder (here and then on the VM), alias, the editor here, the directory
  (just code's own on the VM), copy the sessions, the notes, window - the editor's two task files each a step
  from its first task, named as drawn, without its file's prefix. A stage's row may say the VMs a fact of the run names (`Info`: destroy's check, `started
  beta`, from `claude_vm_release_started`). A quiet stage - `finish`, the deploy play that only
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
  cut as Ansible cuts them (`model.batches`). A task beyond them counts as reached. A step that has ended is
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
  `fail` of `claude_vm_configure_failures`, destroy's `Fail for each VM that was not deleted` - repeats failures
  rescues took earlier, which the engine keeps per host with the names of the blocks around them: on its own host,
  or with `hosts="any"` on every host, as destroy's last play, on this machine, raises what failed on each VM; the
  VMs it repeats a failure of are then the ones the summary's head names. It is drawn under each cause the declaration's blocks name, by
  the cause's step - `✗ tailscale · alpha: Join the tailnet` and what it said - followed by the raise's own words
  but those that only repeat a cause's; the cause's step counts the failure, and the raise is `↑` in the window.
  A failing task the declaration does not name is an ordinary failure, unless it is one the declaration names
  under `declines` - destroy's `Abort unless confirmed` - which is the person's answer, not a failure: its line
  is said once (`Nothing was deleted: the deletion was not confirmed.`), the summary is its head alone,
  `declined`, and so is the title.
- **What a VM may set.** Only `proxmox_vm`'s tasks give the summary a VM's ID and address: a task on the VM
  could set any fact. A declaration may also name the playbooks and task files whose `add_host` does
  (`addresses_from`: discovery, for `just login`), and then only the host's `ansible_host`, kept when it parses as
  an IPv4 address, and `proxmox_vm_id`; or whose `claude_vm_addressed` does (`vm_addresses.yml`, for `just
  code`), and then only each entry's `name`, `address` and `vmid`, held to the same rules. The ID is a number or nothing, and the address goes through `plain_text`, as everything
  else a VM said does. The VMs the run was given come from its `vm_name` extra var, each held to a VM name's
  pattern. Each VM's outcome in a declared step comes from the facts its declaration names (`outcomes`:
  `claude_code_login_done`, `claude_code_login_skipped`, `claude_vm_claude_login_failure` and the like), of
  which the engine keeps only whether each holds, never its text; a fact that says a VM failed fails the step of
  the task that set it, as the failure its rescue took, and a stage may declare them for its steps
  (destroy's `delete`: `claude_vm_delete_error`, `claude_vm_save_error`, `claude_vm_deleted`). A VM a task could
  not reach though it ignored that (a sign-in's ping) is marked unreachable, and a failure raised again marks its
  VM in its cause's step, once however often it is raised.
- **The screen.** The region (§7.8) draws the frame, which `tui/run.py` folds to the height before the region
  cuts it, and steps aside for every prompt. The running step's last five tasks show (the declaration's
  `window`), never the check of an included role's arguments, and a `meta` task, which Ansible starts once for
  each host, each just before that host's result, once: a host an `end_host` ends sends none, and is `–`, left as
  it is. A run of two or more VMs says each VM's state on each step's row - `alpha ✓ · beta ⠴` - and the VM on
  each task line: while a step of a play that is not serial runs, every VM still in the play is at it, and one
  the play left or failed is so; in a serial play, whose turn it is (`alpha ⠴ (1 of 3)`, counted in the order the
  play reaches the VMs - the cluster listing's, not the order they were named in), the VMs whose turn has not come
  `○`. The tab's title says where the run is, and the bell rings once per wait and at the end. A wait is a task
  tagged `attention_browser` that is not a block's `debug`: from its first retry, or once it has run two seconds
  (`WAIT_AFTER`) - a wait that never retries, GitHub's device flow, is known by that, and a skipped one is over
  first. Its time is counted as yours, and so is a prompt's: the region tells the engine when a prompt takes the
  screen and when it is answered (`on_prompt`), the title says `waiting for you`, and the bell rings for a
  prompt only when nothing has asked for the person for ten seconds. A retried task that waits for a machine -
  the guest agent's address - says its try and its limit on its line (`try 3 of 37 · up to 2:55`).
- **The end.** At the stats event the region is erased for good, PLAY RECAP goes to the log, and the summary
  (`Run.summary`) and NEXT STEPS print, the title `done`, or `failed` when a VM failed. The summary's head is,
  but for an answer that declined the run, one of three: `done in 6 min 12 s`; `done in 6 min 12 s · 1 VM failed: beta`, when the run started its last
  play and only some of its two or more VMs failed; or `failed after 2 min 4 s at common`, naming where the first
  failure was drawn. The summary gives each VM a line per report, cut to the width, the report being whole above:
  one of several lines, or longer than a line, by its first sentence; a report run again in a later play - the
  repositories, configure's and then the sign-in's - by its last run; none on a matter a later report recorded
  again (each report's NEXT STEPS record, the `set_stats` task straight after it, names its matters:
  configure's "not signed in", which records nothing left under `just deploy` since the sign-in follows, goes
  once the sign-in's report on that matter comes), nor one whose line a later record replaced, nor one NEXT
  STEPS says whole; and a play on this machine's under the one VM it names. A run of one stage, whose table is
  its steps, names each whole up to 18 characters, says its VM at the header's right when its stage says the VM
  (`info="vm"`, `just code`'s), and has a row for its stage's own tasks only when they did more than skip.
  SLOWEST lists only tasks of a second or more, never a person's time. NEXT STEPS prints each line whole, as the
  stream does: a command or a URL is copied from one line, and the terminal wraps it, never the run. Ctrl-C has
  no stats event: the region's `atexit` erases it and the title says `stopped`. Without a terminal there is no
  region: a line as each step ends (`Run.ended_lines`), the reports, and the summary.

No test or scenario sets the variable - the harness passes on only `PASSED_ON`, and `test_deploy_live.py`
fails if a tracked file other than the callback, the engines (a playbook's, a command's, and `tools/live`), the
declarations, the justfile's recipes drawn live, the live tests' kit, the live tests that draw a playbook through
the engine or a command through `tools/live` - each by name - and `test_justfile.py`, whose `ansible-playbook`
only records, names it - so nothing a test pins is drawn.

## 7.6 `filter_plugins/sessions.py` and `playbooks/library/` — the session handoff

What `just code` carries of this machine's Claude Code sessions to a VM
([§4.9](lifecycle.md#49-playbookscodeyml), **The session handoff**), and what `just destroy` brings
back of a VM's own before deleting it ([§4.5](lifecycle.md#45-playbooksdestroyyml--confirm-release-delete)).
The filters'
code is `lib/claude_vm/sessions.py`'s (§7.10), and a refusal is a `ValueError`.
Four modules, each in `playbooks/library/` beside the playbook that runs them, read and write the
files; the filters decide from what they read, and touch no file. What the modules have in common is
`playbooks/module_utils/agent_warehouse.py`'s, sent with each (§7.10): a session's ID, matched whole;
the manifest's format and its reading, and a file's digest as the manifest records it; a transcript's line
as the object it holds; the folders directly in a folder, never through a link; a version's order; the
user's own processes, from `/proc`; and the one rule of what is a `claude`, which `claude_sessions_info`'s
open sessions, `git_checkouts_info`'s `busy` and the probe's count (§7.7) all ask: the program or its
first argument named `claude`, or either one Claude Code's npm package, so that npm's bin, run through its
`node` shebang, is one too.

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
checkout of it is the one whose GitHub origin names it, of two the one named after it. The plan reads
an origin through two functions of `sessions.py` that no template calls, so neither is a filter:
**`git_repository(url)`** is `owner/repo`, lowercased, of https, ssh and scp-like URLs and local paths
alike, the host not compared: this machine's side, where a clone may reach GitHub by an SSH alias.
**`github_repository(url)`** is the same of a GitHub origin only, and `""` of any other: the VM's side,
whose checkouts just deploy cloned from GitHub, so that a local path or another host's repository there
claims nothing. **`projects_dir(given, home)`** is the projects directory on a VM - `vm_projects_dir`, else
`projects` in the VM user's home, without a trailing `/` - as configure's alias, just code, just status,
destroy's save and the roles that clone into it and start Remote Control there all name it.
**`just_code_dir(projects, given)`** is just code's own directory on a VM -
`editor_files_dir`, else `.agent-warehouse` in the projects directory - for code.yml and for
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

## 7.7 `lib/claude_vm/status/` and `tools/status` — just status

**`status_fleet(listing, tagged, requested, ...)`** — the fleet the engine
found, as data: this project's VMs with what the listing, their config and their agent said, each marked
`read` or with why it is not (`node`, `nameless`, `duplicate`, `name`); the other VMs as totals only; the
storages with this project's share of each (`status_storages` picks them); the template; the aliases in
the managed SSH file (`status_aliases`); the names given that match no VM; and the settings the report
needs. A VM is dated by its disk's `ctime` in the storage's content: a clone's config carries the
template's meta `ctime`, as a live Proxmox VE 9.2 showed. The template is dated the same way, since one made from a
clone - a golden, or one of your own - carries its source's `ctime` too; its config's own is kept to know a VM that
carries it. **`status_vm_api(results, now, since)`** — one
VM's own GETs as the report keeps them: its state, a day of CPU in twelve buckets (Proxmox VE 9 keeps a
point a minute, 8 one every twenty) and the last hour's CPU pressure (9 only), each from `since`, when the
VM was made, on - Proxmox keeps a VMID's history, and an ID used again carries the deleted VM's - the root
filesystem from the agent, the OS, and the settings waiting for a restart. `status_report` takes memory in
use from the VM's own `/proc/meminfo` where it was read over SSH: Proxmox's figure counts the page cache as
used, twice the VM's own on a live VM.
**`status_system_read(result)`** — what the probe read of a VM's system, its fields alone, or none when it
was not read. **`status_work_read(result, directory)`** — what the probe read of a VM's
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
model of a frame drawn while the run goes on. **`status_text(model, color, reading, glyph,
cols)`**, `tui/status.py`'s — the report
as text, every line within 100 columns, or within `cols` for a live frame, where
`glyph` marks each pending value. A VM's name longer than its column - 13 characters, 11 under ATTENTION - is
cut to end in "…" in every section, so a cut name is never read as another VM's whole one. The same functions
draw the frames and the finished report.

**`status/lines.py`** — what the roles say of a VM's sign-ins: `github_login_report` and `tailscale_report`, each
the line and whether it leaves something to do, called by the roles' own reports (`login_report.yml`, `report.yml`,
through `filter_plugins/status.py`) and by `just status`, which works out the same state its own way
(`status/found.py`), so NEXT STEPS and the report never say two things of one VM.

**`status/engine.py`** — the run: the screen (`Screen`, the outline, then each VM as what is read of it lands, in the
region every live output shares, §7.8, the lines its frame leaves out said to be "shown when the run ends"), the
Proxmox GETs (`Proxmox`, eight at a time, each result in the shape a `uri` task registers, so the model reads either),
and the order of it all. `Display` is the three things the region asks of Ansible's: a lock, `display()`, and the
run's log in Ansible's own format, so `just logs` reads it as any other; a warning Ansible gives while it reads the
settings goes through it too, or it would land inside the frame. When the run ends the region is erased and the
finished report printed once - in colour on the screen, plain in the log - or, under `--json`, the JSON alone on
standard output and the report to the log, without even `tools/run-logged`'s line naming the log (`STATUS_QUIET`),
since whatever reads the report reads standard error for what the run warned of.

**`status/settings.py`** — the settings, asked of Ansible in this process (its `VariableManager` and templar, as
`tools/settings` asks them), or from the snapshot [§4.6](lifecycle.md#46-toolsstatus--read-only-drawn-live) describes: nothing of Ansible's precedence is written again.
What is the same for every VM is rendered once for a placeholder host, whose name is replaced in what comes back; a
VM with a file in `inventory/host_vars/` is rendered as itself.

**`status/reach.py`** — one connection per VM: `ssh` with `status_ssh_args` first, then the inventory's own ssh
options, port and key, BatchMode, the same ControlPersist, or `docker exec` or the local interpreter for a host of the
inventory that says so. Once a run has ended (`end_all`), every connection still open is killed and none is made. The script sent is
the probe and the two files of `playbooks/module_utils/` it imports, held in memory under their own names. Root
through `ansible_become_exe` by its path: `sudo -n`, and only where that is refused the VM user's password on stdin.
The first line of what is sent after a password is a mark the bootstrap looks for, so a `sudo` that asks for none,
leaving the password unread, never hands it to Python, whose syntax error would say the line.

**The probe** (`playbooks/module_utils/status_probe.py`), the one program of status's on a VM, started as root, a
child that has become the VM user making that user's reads: the OS and the running and newest kernels, the reboot flag
and the packages that set it, the load, memory and uptime, the upgrades apt's simulation (`apt-get -s`, which takes no
lock and refreshes nothing) would make and how many are security ones, the user's own claude processes (by the
session modules' rule, §7.6) and each editor's windows, a process of the user's counted once, under the first editor
whose `server` match in the editors list it meets: the probe cannot import the core, so the engine hands it the rows'
matches as its `editors` (`editor_servers`, §7.10). They are kept by the editors list's keys in `editor_windows_by` -
not among the keys `just status --json` promises, which keep `vscode_windows` and `editor_windows`, every editor's
together - so the reboot item names each editor's windows as the editors list names it. Claude Code's state, gh's
login and each checkout's work (`module_utils/git_checkouts.py`, the reads `git_checkouts_info` makes too); Tailscale's
as root, the role's markers being root's. Every read runs in a thread of its own with its own time limit - one that
runs out is said by name and the rest still land. One JSON document a line on standard output: a first line that it
started, what it read but apt's upgrades the moment only that is left, and the whole.

Covered by `tests/unit/test_status.py` (the model, a hostile corpus, the 100-column limit, the screen driven in
process with a stand-in terminal, and the engine against the fake Proxmox) and `tests/unit/test_status_live.py` (the
real engine at a pseudo-terminal: a window it fits, a short one, a narrow one, a resize each way, Ctrl-C, a dumb
terminal, a pipe, and a warning of Ansible's above the frame), and `tests/unit/test_status_vms.py` (the probe on
fixture trees and held to its reads, the reads inside stand-in VMs and that they change nothing there, the engine
held to the roles' own tasks state by state, and the host key's options and refusals), and
`tests/unit/test_status_work.py` (the checkouts' `work` over real throwaway repositories, which it leaves as they
were), and `tests/unit/test_status_settings.py` (what is kept, what it may not hold, and when it is believed).

## 7.8 `lib/claude_vm/tui/live_region.py` and `screen.py` — every live output

`just status`'s report (§7.7) and every live run (§7.5) are drawn the same way, with the same code, so each
new live output adds only what it shows. Two modules hold what they share, and `lib/claude_vm/text.py`'s
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
  Each write and its count of the lines it drew hold a Ctrl-C off between them, by a SIGINT handler of their
  own that hands the signal to the one in place right after: a `KeyboardInterrupt` in that gap left the count
  stale, so the erase that followed missed the region, which stayed on the screen above the run's last words.
  A signal mask would not do: it holds the signal in one thread, the kernel hands it to another, and Python
  raises it in the main thread all the same.
- **Everything else above it.** It wraps `Display.display`: each call erases the region, prints, and draws
  it again, under `Display`'s lock, so every write to the log takes the locks in one order. A route, given
  the call's keyword arguments, may send it to the log alone (`log_only`), which a live run does with the
  stream. A forked worker's display goes over the queue to the process that started the region, which alone
  draws.
- **Prompts.** `Display.prompt_until` (a `pause`) prints its prompt through `display` and only then takes
  `Display`'s lock to read the keys (ansible-core 2.21), and a `vars_prompt` reads with `input()`
  (`Display.do_var_prompt`). Both are wrapped: the region erases and pauses itself before the prompt and draws
  again once it returns and the run has worked half a second more (`SETTLE_SECONDS`), so questions one after
  another - `just init`'s - show no frame between them; the spinner draws nothing meanwhile.
- **The terminal.** Its echo is off while the region is up, and on for a prompt and at the end: an Enter
  typed during a run would move the cursor under the region, and the next redraw would leave a copy of it
  behind. A resize (`SIGWINCH`) pushes the screen into the scrollback and draws afresh, since no count of
  what was drawn holds once a frame meant for the old size has wrapped. `title()` sets the tab's title (OSC
  0) when it changes, and `bell()` rings.

**`choose.py`** — a question with a few answers, asked as a menu picked with the arrow keys: `just init`'s yes or no
and its choices of a storage or a bridge. A prompt drawn with `screen.py`'s lines, palette and cut, while the region
has stepped aside for it, so there is still one way of drawing: every line cut short of the width, one write per
redraw in synchronized output, the cursor never hidden, the menu erased once answered and the answer said on one
line (`collapsed`), to the screen and the log.

- **The answers ride along in built-in types.** `playbooks/action_plugins/ask.py` asks as `pause` does - the same
  text, the same `user_input`, Ctrl-C then C or A - and hands `prompt_until` a `complete_input` of Enter's two bytes
  and one entry more: `MARKER`, two NULs about a name, and the menu as JSON (`marked`). The prompt crosses from
  Ansible's worker to its main process by pickle, and a class of the project's would need the main process to
  import the project's code: where it cannot - a run without the live display, the unit harness's among them -
  Ansible's results thread, which catches only `OSError` and `EOFError` around its read, dies on the message, and
  the worker waits for its answer for good. Where nothing hooks the prompt, Ansible compares one key at a time with
  the list, which never equals the marked entry: the typed answer works as before. A hidden question carries no
  menu, whatever it is handed: a menu shows its answers.
- **The engine asks it** (`engine.choosing`, around `Display.prompt_until` under the region's own wrapper): a marked,
  shown prompt with no timeout, at a terminal of the run's foreground that can draw (`can_choose`), is asked as a
  menu under `Display`'s lock, as `prompt_until` reads under it; anything else - no menu, a hidden question, no
  terminal, `TERM=dumb`, a run with `-v`, which has no engine - goes to Ansible's own prompt. A Ctrl-C at the menu
  raises what `prompt_until` raises, so `pause`'s continue-or-abort follows.
- **The keys**, read from stdin, never `/dev/tty`, with the terminal's echo, line editing and signal keys off and
  set back however the menu ends: up and down in both cursor modes, Home and End, a digit for the answer it numbers,
  an answer's first letter (`y` and `n` for a yes or no), Enter to choose, Esc to decide later. A long list scrolls
  in a window, the hint saying where the marked answer is; the terminal's size is read at each redraw, since
  `prompt_until` runs in Ansible's results thread, where no SIGWINCH handler can be set.

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
what the callbacks, the engines, `tools/` and the filters share is `lib/claude_vm/` (§7.10): each imports it with
its checkout's `lib/` first on `sys.path`, one module object however it is reached.

**Another recipe, drawn live.** A recipe whose playbook is a run joins `just deploy` without code of its own, as
`just login`, `just destroy` and `just code` have: its recipe exports `AGENT_WAREHOUSE_LIVE` with its title, as `deploy`'s
does, and its declaration in `lib/claude_vm/run/recipes.py`, named by the title's first word, says what its run is
made of - its stages, by playbook or by play, a quiet one counted but never drawn (discovery, a last play that only
raises), one whose hosts are not VMs (`vms=False`: the template's, on the Proxmox host), and the VMs a fact names on a
stage's row (`info`); each stage's steps, by playbook, by the blocks around
their tasks, by task file (`files`) or by the task each begins at (`starts`); each step's `outcomes`, or its stage's, the facts that mark each VM; the tasks that raise again what
rescues took (`raises`, with their causes' blocks, their hosts, and the unreachable VMs a ping ignored); the tasks
whose failure is a person's answer (`declines`, with the line said instead); how many tasks a window shows; the word for a step of a run of one stage (`noun`: `sign-in 2 of 3`) and the summary's columns;
the playbooks whose `add_host` gives a VM's address and ID; and the playbook whose first play reports what the run
will do. Its reports, waits and NEXT STEPS records follow §7.5's conventions, which the engine already reads.
Anything a declaration cannot say becomes a new field and new code in the engine, never code of one command's
own. A command that is a report rather than a run, as `just status` is, keeps a model and a frame function of
its own and hands the frame to an `Output`, drawing its lines with `screen.py`. Either way its test runs it through
`tests/unit/live_kit.py`: at a pseudo-terminal of its own (`harness._at_a_pseudo_terminal`) with a `HOME` of its
own, read back through `vt.py`, or piped, with the checks every drawn run must pass (`Ran`); and
`test_deploy_live.py`'s guard names each tracked file that may name the variable.

**Runs in parts, and commands.** A recipe that is more than one process - checks one after another, two playbooks -
is drawn as one run, its parts in turn:

- **`tools/live`.** The justfile's `live` runs it with the environment's Python and the run's logging settings:
  `run <stage> [--step <label>] -- <command>` in front of each command of the recipe, `end "$?"` from the recipe's
  trap. It decides first, with the standard library alone, since a fresh virtualenv's first command must work before
  `just install` has put the engine's packages there: it passes the command through - `exec`'d unchanged, the switch
  and the state's path taken out of its environment and nothing else changed, Ctrl-C ignored where it was, and a
  command that cannot run exiting 127 or 126 as a shell's - when there is no terminal, no title, a `-v` in
  `ANSIBLE_ARGS`, no run log (`LOG_DIR=`), no state to carry the run between its parts (`LIVE_DIR=`), or no PyYAML
  and ansible-core. Otherwise it runs itself again
  through `tools/run-logged` under the title's recipe, so a command's run has a log under its rules, and hands the
  command to `lib/claude_vm/ansible/parts.py`.
- **A command, drawn.** Its child gets `/dev/null` for stdin, one pipe for stdout and stderr, a session of its own
  with no terminal - what would ask at `/dev/tty`, git or ssh as pre-commit fetches its hooks, fails at once and
  says so, where a process group of its own was stopped by the terminal and hung the run - and this environment
  without `AGENT_WAREHOUSE_LIVE`, the state's path or `ANSIBLE_LOG_PATH`, with `NO_COLOR`, `PYTHONUNBUFFERED` and
  `PRE_COMMIT_COLOR=never`. It is one task of its stage's step (`Run.command`), on this machine, named by its
  program past `env` and its settings - a shell running a script by its step; every line it prints goes to the run's
  log through Ansible's `Display`, in the log's format and order, and the last one is drawn dim after the task. A
  part that fails prints its last 40 lines above, whole; one that passes prints its lines that warn, which would
  otherwise be in the log alone. `tools/live` takes Ctrl-C from its first line - a handler, never `SIG_IGN`, which
  the child would inherit; blocked across the re-run through `tools/run-logged`, and taken the moment the handler is
  in again, so none is lost there (where `/bin/sh` is dash, which unblocks it at its first command, it ends the
  re-run instead, and the run's end says it stopped) - and the part reads it once its own handler is in, so a part
  interrupted before its command runs none and says it stopped. The part sends Ctrl-C to its child's group, reads
  the pipe to its end, so the child's last words reach the log, marks its task stopped (`■`) and exits 130; a second
  Ctrl-C ends everything the child started and reads no more of it: its group, and every process the part found
  under the child before the first Ctrl-C went out (`lib/claude_vm/proc.py`, which reads the table `ps`
  prints, each process with its start, so a pid another process has by then is left alone), since one in a
  session of its own - ansible-core's task worker, detached by `WorkerProcess._detach`; a nested run's part -
  outlives the group, and a child that ended at the first leaves them under PID 1 or a subreaper, where no walk
  from it finds them. SIGTERM or SIGHUP - a kill, a timeout, the tab closing - is sent on to the child's session,
  which no terminal signals, and to everything under it, since a playbook sends its task worker a SIGTERM on but
  not a SIGHUP, which would leave the worker behind; the region goes,
  the echo comes back, the state is saved said stopped, and the part dies of the same signal. A child a signal ended
  exits as the shell says it, 128 and the signal.
- **The state.** Each part takes over the run's model from the parts before it (`Run.dump` and `Run.adopt`: every
  task, step, report and NEXT STEPS record, each time from the run's start, so one clock runs through the parts),
  draws them done above its own, works and saves it again. It is a file in `LIVE_DIR` (`.cache/live/`: git-ignored,
  refused by `tests/check_no_local_files.sh`) named for the run's stamp and its top recipe, which the recipe exports
  as `AGENT_WAREHOUSE_LIVE_STATE`: made by the run's first part, written whole, 0600 in a 0700 directory, and
  taken over only when it is of this version and this run's title; one a run that never said its end left behind is
  removed a day later - a state by its name, the pytest reader's events beside it, or a temporary of one, and
  nothing else: `LIVE_DIR` is whatever the environment names. A playbook that is a part does the same in the engine:
  it draws the parts before it, and at its end saves its state, PLAY RECAP to the log, instead of saying the
  summary; its NEXT STEPS records join the earlier parts', the later winning for a VM and matter. One that Ctrl-C
  ends before its stats saves what it did, said stopped, at exit. No bar or percentage falls from one part to the
  next: the highest each has shown is kept.
- **The end.** `end` says the summary and NEXT STEPS from the state, whatever ended the run - a part that failed, a
  playbook that died before its callbacks ran, a line between parts, Ctrl-C (130, ansible-playbook's 99, or a part
  that saved itself stopped), after which the head says `stopped after … at` the last part - then removes the
  state. A run whose parts all passed through has none, and ends with
  nothing said, as it did.
- **`just init`** is two playbooks in turn, after `just install`, which is never drawn - it would install the
  engine's own packages from under it - and only with a state to carry the run between them: under `LIVE_DIR=` each
  prints as it did. The settings stage's steps are its questions, each `starts` at the include that asks it; the
  checks stage has a `checklist`, `init_results`, which the engine takes in each time
  `init_check.yml` records a check - whether it passed, and its text as plain text - and draws under the running
  stage, a `✗` for each problem, and says whole in the summary. The last task, "Fail when a check found a
  problem", is a raise whose causes are the checklist's failed checks (`Causes(checklist=True)`); a report line
  that starts with one of the declaration's `marks` (`ok`, `!!`, `--`, `NOTICE`) takes its style, and the checks'
  report leaves no line in the summary, whose checklist says it. A part of a run in parts whose declaration has
  several stages is never drawn as one stage's steps: the next part would draw them otherwise.
- **`just lint`** is the first: one stage, `lint`, each check a step (`--plan` lists the ones the run will run, to
  come until each starts, each counted as one task till then so that no percentage reads whole too soon; a run
  that ends well with one never reached was stopped between its parts, and says so), its rows with no bar or
  count (`counts=False`), its summary `CHECKS` and the checks a failure left unrun; its totals count the checks,
  not the commands. Its state's name carries its shell's PID, so two runs started in one second keep two. The
  syntax check is one command over every playbook, each said as it starts.
- **`just test`** is lint, nested (`{{ self }} live_run=test lint`), then pytest and each Molecule scenario, each a
  command through `tools/live --log <name>`, which passes it through `tools/run-logged` as it was logged before. The
  recipe exports the stages it will run (`AGENT_WAREHOUSE_LIVE_STAGES`), so each is listed to come from the first
  part, lint's. Two readers make the parts' tasks: **pytest**, from the events `tests/unit/live_pytest.py` writes
  (`-p live_pytest`, which pytest finds on its `pythonpath`) to the file `AGENT_WAREHOUSE_LIVE_PYTEST` names,
  beside the run's state, 0600 in its 0700 directory and never the system's temporary one, which the end removes -
  the count once collected, from the first xdist worker's collection when there are workers, each test's start and
  end, several running at once under xdist, a test whose worker crashed ended as failed, each named by its file and
  the rest of its ID (a parameter's `/` is its own), and a failure's words above as it fails - the plugin writing
  nothing without that file, so a run piped, in CI or by hand is pytest's own; and **molecule**, from Molecule's own
  lines - its one `scenario test matrix: …` line the count, `[scenario > action] Executing` and `Executed: …` each
  action, the Ansible `TASK [...]` the running action's last line, and a failed action above as it fails, while
  Molecule goes on through its cleanup and destroy: the task that failed in its head and what Ansible said of it
  from its `fatal:` on, or the action's last lines where no task failed. Molecule says why a scenario failed - an
  idempotence test's changed tasks, a playbook's exit code - only after its cleanup and destroy, so the part's
  failure prints what it said after the action that failed. A warning is a line at Molecule's `WARNING` level or
  Ansible's `[WARNING]` or `[DEPRECATION WARNING]`, never a task that names one, said above once the scenario passed
  but for Molecule's routine - a requirements file or a playbook a scenario has no need of, the temporary directory
  a fresh container makes, a prompt that takes its default - which stays in the log. A reader that made no task for
  its part - a Molecule whose lines it no longer knows, a pytest that never loaded the plugin - makes one for the
  command, its outcome its exit code's, never a stage not run. The summary lists what failed by name
  (`failures=True`). The count is the tests' own: a test that runs for minutes counts as one. A stage the recipe
  lists is one task until a part reaches it, as a listed step is, and the tab shows where the run is with no
  percentage while one is still to come, since its size is unknown; a test or an action Ctrl-C ended is stopped, not
  failed; and the roles' parts, which run in their directories, find every tool by its absolute path, each saying
  which role it is as the first line of its own output, so that the log names it where the screen's row does.
  With `--jobs N` (more than one scenario, N above one) the scenarios are instead one part of a stage of their own,
  `molecule`, read plain: `tools/molecule-lanes`, which starts them N at a time through `xargs -P`, the longest first
  (the justfile's `molecule_order`), each one's output into a log of its own, and says a line as each ends, its
  log's path and its output's last lines where it failed; the part's one task shows the last of those lines. Every
  scenario runs, failed or not, and they share the part's process group, so Ctrl-C reaches each as it would one.
- **NEXT STEPS** prints each record's lines as they are, as the stream does: a record of several lines - just
  init's problems, each under the line that counts them - keeps each line with its indent, and each paragraph is one
  line, which the terminal wraps, never the run.
- **Nested recipes.** A recipe in parts that runs another runs it as `{{ self }} live_run=<its name> <recipe>`: that
  one exports no title or state of its own, sets no trap and takes the parent's, so its parts are a stage of the
  parent's run. `live_run` is a justfile setting, which `self` passes on, never the environment, so a variable left
  exported joins no run.

Covered by `tests/unit/test_live_region.py` (the region against a stand-in `Display`, whose `display`,
`prompt_until`, `do_var_prompt` and lock it holds to ansible-core's), `tests/unit/test_screen.py`,
`tests/unit/test_vt.py` (the small terminal, a stream cut anywhere), `tests/unit/test_live_commands.py` (a stand-in
recipe in parts at a pseudo-terminal: passed through piped, without a title, with a `-v` and without the engine's
packages; drawn as one run, its failed part's end whole, its log every line, its state the run's alone, Ctrl-C, a
playbook as a part, and an end with no state), and each output's own live tests.

## 7.9 `tools/settings` — just settings

`just settings` says what each setting is and where it was set, for one role (`just settings tailscale`), one
variable or setting (`just settings tailscale_ssh`), or all of them (`--all`), and changes nothing but the one
setting `--value` writes into `local.yml` and `--reset` takes out (below). Without a word
it shows how to use it: the roles, read from their `argument_specs`, and the options; that needs no vault, so the
recipe runs it before the vault's check.

**The values are Ansible's.** Nothing of Ansible's precedence is written again: the tool loads the inventory, adds a
host to the group the role's playbook runs it on - read from the playbooks, the play that runs the role's `main` and
passes it nothing: `claude_vms` for the roles `configure.yml` runs, `claude_vm_pending` for `proxmox_vm` (from
`provision.yml`, not `destroy.yml`, which passes `proxmox_vm_state`), the first `proxmox` host for
`proxmox_template` - and asks `VariableManager` for a play naming the role, so its defaults sit at the bottom, then
the templar for each value. `--all` adds a default that lives only in a role's `default()` (`vm_cores`), marked
trusted as a template. What a setting is - each role's `argument_specs`, main's first, the wiring, the sections of
`local.yml.example` - comes from `lib/claude_vm/wiring.py`, the reference pages' own model.

**Where a value was set** is the highest layer that holds the name - `hosts.yml`, `vault.yml`, `local.yml`, or a
default; for a host in `hosts/` (`--host-folder`, from the recipe's resolver), its own `vault.yml` and `local.yml`
above the shared ones, the inventory its sources over the shared `controller.yml` as a run against it reads them, and
`--value` and `--reset` writing its `local.yml` only with `--write-host`, when `--host` named it - and, for a default that is an expression, the names it reads, followed through the wiring (Jinja's own
parse) to the file a person edits: `claude_code_login_desktop` is false because of `login_desktop` in
`local.yml`. What a run sets above the inventory is read from the playbooks: a playbook no other imports is the run
of the recipe of the same name, and the `vars:` it passes - on an import, a play, a role, an `add_host` - through
every import; the justfile's `-e` objects too. The row keeps the value a run that sets nothing uses (`just deploy
--only configure` runs `configure.yml` without `deploy.yml`'s vars) and adds "just deploy sets it from
deploy_sign_in". A value made from the VM's name says so; one made from its facts is "worked out on the VM".

**Reading changes nothing.** Every lookup goes through `lookup_loader.get`, which the tool wraps to load only `env`,
`file`, `config`, `vars`, `first_found` and `fileglob`: `pipe` runs a command and `password` writes a file, and a
scan of expressions misses one reached through a variable it does not read (a `hosts.yml` group var). The
inventory is read by the `yaml` plugin alone (`ANSIBLE_INVENTORY_ENABLED`, set before Ansible reads its config):
the default list has `script`, which runs an executable it finds. The tool's own reads of the files, to say which
set what, use `cache="none"`: the loader caches a file without its trust as a template, and the inventory, reading
the same file after, would template none of its values.

**No secret is printed.** A value is masked when `vault.yml` holds it; when Ansible marks it as decrypted from a
vault (`VaultHelper.get_ciphertext`), an inline `!vault` anywhere included - values of the whole-file vault carry no
mark, hence the first rule; when its role marks the variable `no_log`; or when its expression reads a name the
vault holds, whatever its name, or a `no_log` variable. A masked row says set, not set, or the
example's placeholder - compared with `vault.yml.example`, as `just init` decides it - and that it changes with
`just vault`. Last, every value about to be printed, in the text or the JSON, is checked against every secret
Ansible decrypted; a match prints nothing and exits 1. The GitHub username and an OAuth client's ID are masked in
their own rows but not looked for elsewhere, so a username that is a common word never refuses a report. The
vault's password file is the one the recipe passes (`.vault_pass`, where there is one), else what
`ansible-playbook` would take; the tool never looks for one, and a vault it cannot open is refused before anything
is printed.

**`--value` writes one setting into `local.yml`, and nothing else.** One function, `settable`, decides what a name
can be set as, or why not, and the read view's `change` comes from it, so the report never names a command the
writer refuses. A role variable a setting of the examples feeds is written as that setting
(`claude_code_login_desktop` as `login_desktop`). Refused, with nothing written: anything `vault.yml`
holds, feeds or a role marks `no_log` - for `just vault`, the value given never said back, though the shell's
history keeps it - a value vaulted inline in `local.yml`, a name made from each VM's own (one setting would give
every VM the same), what a run sets for itself and no example names, the Proxmox host's address (`hosts.yml`),
anything internal to the project, and a value equal to a secret the vault holds. The value is free text on the
`just` line but for a control character, so just never says it back; the tool refuses an expression (`{{ }}`,
`{% %}`, `{# #}`, which Ansible would template and a lookup run) - in the text given, and in the value it parses
to, since YAML decodes escapes and `["\x7b{ ... }}"]` is a list holding `{{ ... }}`; a vault secret is looked for
in both the same way - and holds the rest to the type the role's
`argument_specs` give, through Ansible's own `ArgumentSpecValidator`, as the role checks its arguments - a list as
`a,b` or YAML, a path kept as typed, `~` and all. It is written as `just init` writes: JSON on one line, in place of
the line that set it (with its value's further lines), else of its example's commented line, else at the end. The
result is read back with every tag kept as text before it replaces the file - every other key as it was, an inline
`!vault` included, or nothing is written (an alias whose anchor the edit removes, two lines of one name) - and it
replaces it atomically, with its mode (`lib/claude_vm/store.py`): the read and the replace hold one lock, on the
file's folder rather than a lock file beside it, so of two writes at once neither is lost. Then the files are
read again: what outranks `local.yml` (a `hosts.yml`
variable, a run) is said, with what applies the change - `just deploy --only configure --tags <role>`, or a VM's
creation for `proxmox_vm`.

**`--reset` takes one setting out of `local.yml`.** Its line goes, with its value's further lines - a role
variable's own too, where one is set by hand beside the setting that feeds it - and the example's commented line
comes back in its place, as `just init` found it, unless the file still has one: a value set and then reset
leaves a `local.yml` that has its example's line, as `just init` makes it, byte for byte as it was. Taking a line out can make no value wrong, so `settable` refuses it
only what is secret (for `just vault`, or an inline `!vault`) and the host's address; a name made from each VM's
own, set by hand, may go; a role variable still set by hand beside the setting is said to outrank it. A
role variable set by hand is what `--value` writes too, in place of its line, since the setting would change
nothing under it, and a key not on a line of its own (a quoted key) is refused rather than set twice. An empty
`--value` is no value: just cannot tell it from none given, so `--reset` is the way back to a default. The
same read back holds it, and the same second look says the value the setting has
now and what applies it.

**The report** is a line per variable under its role or section, every line within 100 columns, a value cut with
"…" in the table and shown whole in the one-variable view, colour only at a terminal (`screen.py`'s palette and
cut, §7.8), and each value one line of plain text (`one_line`, §7.4). `--json` gives the same rows, with `kind`
(`value`, `secret`, `per_vm`, `on_the_vm`, `at_run_start`, `placeholder`), `set_in`, `via`, `set_by_run`,
`evaluated_as` and `change`. A file in `inventory/host_vars/` is noted, since Ansible applies it to the VM of its
name although no setting is per VM.

Covered by `tests/unit/test_settings.py`, against inventories made from the example files with fake values, and the
recipe by `tests/unit/test_justfile.py` against a stand-in `python`.

## 7.10 `lib/claude_vm/` — the controller's Python as one package

The Python that runs on this machine is moving into one package, `claude_vm`, in `lib/`, under one import name, so a
module is one module object however it is reached, and the parts a future client needs import nothing of Ansible.
The modules a VM runs stay where they are (`playbooks/library/`, `roles/*/library/`, `playbooks/files/`): Ansible
ships a module only with what it imports from `module_utils`, so they never import `lib/` and get what they need as
arguments. What the modules in `playbooks/library/` have in common is in `playbooks/module_utils/agent_warehouse.py`,
standard library only and no `AnsibleModule`, imported as `ansible.module_utils.agent_warehouse`: Ansible puts
each playbook's `module_utils` folder on the path it builds that playbook's modules from, as it does its `library`
folder, and `ansible.cfg`, the unit tests' environment (`tests/unit/harness.py`, and `conftest.py` for a module
loaded in pytest's own process) and the `configure` scenario name it for what runs from elsewhere. ansible-lint's
`args` rule imports each module in ansible-lint's own process, where no setting reaches, so `.ansible-lint` names a
rules folder, `.config/ansible-lint-rules/`, whose one file holds no rule and puts the folder on the path. A helper in
`playbooks/files/` runs as plain `python3 -I`, not as a module, so `claude-start.sh` keeps a copy of the shared
test of a running `claude`, held equal to it by `tests/unit/test_code_session.py`.

- **Nothing outside `claude_vm/ansible/` imports `ansible`**, and no module imports `yaml`, `subprocess` or `asyncio`
  when it is imported, only inside the function that needs one - but `proc.py`, the process table, whose every user
  runs programs anyway: a client that is not Ansible would pay about 30 ms for `ansible.errors` alone.
  ansible-core 2.21 names the filter that raised, so a core `ValueError` goes out as it
  is. The package's `__init__.py` imports nothing.
- **`records.py`** holds `plain`, which copies a value out of Ansible's lazy containers and tagged strings, and
  `Checked`, the base of every record: a frozen slots dataclass whose fields are held to their annotations whenever
  one is built (an exact `str`, `int` or `bool`, a `Literal` by membership, a tuple item by item), then by the
  record's own `validate`. **`wire.py`**'s `to_wire` is the one way a record leaves: `format_version` and `kind`
  first, then its fields and the values its properties derive, through JSON, so a tuple arrives as a list.
- **`store.py`** is the core's one module that writes a file that persists, and only `tools/` import it:
  `replace` swaps a file for new text atomically, keeping its mode (through a symlink, the file it names), and
  `locked`, around a read and a replace, holds an exclusive `flock` on the file's folder, so two writers never
  lose one's change.
- **`refs.py`** names a VM and a command: `VmRef` (a name as `just` takes one, and a VM ID in Proxmox's range),
  `Action` (a recipe and its words, `line` its one display form, `command_line`) and `NextStep`.
- **The adapters.** Each file in `filter_plugins/` is an adapter: it puts its checkout's `lib/` first on `sys.path`,
  relative to its own file (never `PYTHONPATH`, a `.pth` file or an editable install, which would point every
  checkout sharing a virtualenv at one checkout's code), and its `FilterModule` returns `plugin.filters`, functions
  that import their core module only when one is called: every worker executes every file in `filter_plugins/` at
  its first lookup of any filter. `plugin.own` checks, once per process, that the `claude_vm` imported is the
  adapter's own checkout's, by `os.path.samefile`, since APFS ignores case and a resolved path keeps the case it was
  given. pytest finds the package through `pythonpath` in `pyproject.toml`; ruff checks `lib/` against Python 3.12.
- **The editors' plans** (`editors/`). A window of `just code` is a plan, data: what it refuses, the directories and
  files it writes in just code's directory on the VM (by names relative to it), the helpers it copies (by name), the
  folder or file it opens, and what it says. Each editor family is a module of free functions with one,
  `plan_open(editor, inputs)` (`vscode.py`, `zed.py`), the seam a `typing.Protocol` of that one member
  (`family.py`), never `@runtime_checkable`. `model.py` holds the records: a row of `claude_editors`, what the
  family's check found here (`Probe`), the inputs (`OpenInputs`, which grow by fields with defaults) and the plan.
  Every plan passes `registry.py` on every call: a refusal leaves alone; every write lies strictly inside just
  code's directory (`common.below`, `commonpath` on the normalised join), outside the folders YAML writes there
  itself, the session copies' and the notes' (`common.KEPT`), and over no helper's file; every helper is one YAML
  lists, with its directory; every NEXT STEPS record names the inputs' VM; and the launch is the registry's own, in
  the family's fixed shape over the first command `which` printed with one of the row's names whose `--version` says
  what the row asks, so never ZFS's `zed`. Anything else is a `ValueError`. The plan compares paths normalised,
  every "inside" by `common.below`, and writes and launches them exactly as `code.yml` built them, and holds just
  code's directory to its rules only where it writes there, as `code.yml` does; a family gives the Claude terminal
  only where its row says the window has one (VS Code's, `started`; Zed's task file only at a helper path a task can
  be handed as it is). A row's `server` (`ProcessMatch`) is how `just status` knows one of the editor's windows on
  the VM, a program named first, since a match on the arguments alone would count any of the user's programs.
  `filter_plugins/editors.py` gives Ansible `editor_open_plan` and `editor_servers`, the rows' matches as
  the probe's `editors` (§7.7).
- **One filter lookup in the controller.** Both stdout callbacks look a filter up (`bool`) when the playbook
  starts, before the first worker is forked: the loader keeps the non-collection filters it found, and each worker
  inherits them rather than executing every file in `filter_plugins/` again at its first lookup, a few milliseconds
  a task. It rests on ansible-core forking its workers (not `forkserver`) and on the loader's cache; the adapters stay
  thin all the same, for the runs no callback of the project's warms (the unit harness, Molecule).

Covered by `tests/unit/test_claude_vm.py`: `lib/` holds the one package, every core module imports in a fresh
`python -I` without `ansible`, `yaml` or `asyncio`, each record refuses what its fields cannot hold, and an adapter's
filter imports its module only when called and refuses another checkout's `claude_vm`.

## 7.11 The choices: the editor, the VPN, and later the host's OS

A checkout makes a few choices once, each a setting naming one row of a list in the inventory defaults: `editor`
(`claude_editors`), `vpn` (`claude_vpns`), and, with Unraid, the host's OS. Each is read by every playbook through
one internal key (`claude_editor_key`, `claude_vpn_key`), never the setting itself, so an empty value is the
default's. `lib/claude_vm/catalogue.py` holds what they share and nothing more: `CHOICES`, each setting with its list
and the values it takes that are no row (`vpn: none`); `rows_from`, the one way a list's rows are parsed into their
records; and `pick`, the one refusal of a value that is none of them, naming them. `tools/settings` (`CHOICES_FROM`,
`CHOICES_ALSO`) and `tools/check settings` (`CHOICES`), which cannot import the package before a run, say the same,
and `tests/unit/test_choices.py` holds the three equal.

- **A choice splits in two, each half with its pattern.** What is decided on this machine is data: a family module
  turns a row and the run's inputs into a plan, a registry holds every plan to the choice's rules, and YAML applies
  it - the editors' windows ([§7.10](#710-libclaude_vm--the-controllers-python-as-one-package)). What converges on a
  host stays a role with named entry points, imported statically with a literal name and a `when`, so a role's tag
  reaches every task of it. The editor uses the first half; the VPN uses the second, its role per member, and gains
  the first as its members' plan.
- **No choice imports another's code.** What one gives another travels through the playbook as data.
- **The rows are literal data**, read as they are by `tools/settings` and the tests: a row names its role, its
  family and whether it has a sign-in, and no behaviour is decided from such a field alone.
- **The VPN.** `vpn: tailscale | none`. Unset, it follows `tailscale_enabled`, the setting it replaces, still read:
  `tailscale` while that is true. The Tailscale role reads `tailscale_wanted`, which the inventory sets from the key
  (`claude_vpn_key == 'tailscale'`): the role cannot be handed `tailscale_enabled` from the key, since the key's
  default reads it, and alone it follows `tailscale_enabled`, as do the playbooks run without the inventory's
  defaults. `tailscale_off_setting` names, in its messages, the setting that turned Tailscale off. Each member's role
  runs where it is chosen, or where its installed marker says it put something there - then only to say it is left
  as it is, since choosing a VPN never removes one; its release runs at `just destroy` whatever is chosen.
- **The VPN's members** (`vpn/`). Who is on a VPN's network is a plan, data: the marks to keep on each VM in
  Proxmox, and what is said. Each VPN family is a module of free functions with one, `plan_members(vpn, inputs)`
  (`tailscale.py`), the seam a one-member `typing.Protocol` (`family.py`). `model.py` holds the records: a row of
  `claude_vpns`, each VM a run plans (`Member`: its name and VM ID from Proxmox, its marks, what its roles saw), the
  inputs (`MemberInputs`, which grow by fields with defaults) and the plan (`MemberPlan`). Every plan passes
  `registry.py` on every call: a refusal holds nothing else; marks only for the VMs it was given, only of the
  family's own (`common.marks_of`), never Tailscale's record taken off; NEXT STEPS only under the family's own
  matters, for the VMs it was given. Tailscale's record plays run in a block whose rescue says a failure and goes on,
  as `tasks/vm_marks.yml` never failed a run. `tasks/vpn_members.yml` builds the inputs from what Proxmox and the
  roles left and refuses what the plan refuses; `tasks/vpn_apply.yml` applies it: each VM's marks through
  `tasks/vm_marks.yml`, what it says, and its NEXT STEPS records (`vpn_next`), a step outside the project said as it
  is (`NextStep.text`).
- **Marks** are what a VM must not be able to change about itself, kept by the host's OS: on Proxmox, a tag beside
  the ownership tag, `<ownership tag>-<mark>` - `tailscale` once a VM's Tailscale has held a login; any other tag
  under it, such as the WireGuard address 2.0 kept (`wg-<address>`), is no mark and is read as none. Discovery,
  provisioning's publish and destroy read them from the listing (`proxmox_marks`) as `claude_vm_marks`, under every
  owned tag (`claude_vm_tags_owned`: the tag, and its 1.x name while it is the default), and `proxmox_vm` keeps them
  whenever it replaces a VM's tags (`proxmox_vm_kept_tags`: a repair, `proxmox_vm_force_update`), a 1.x VM's under
  the new tag (`proxmox_tags_moved`). `tasks/vm_marks.yml` is the one writer of a mark: VM by VM, its tags read and
  written back as read with the marks added under the tag or taken off under either, only on the VM of that name and
  VM ID that carries an owned tag, never in check mode, a
  VM Proxmox could not answer for said and kept for NEXT STEPS under its change's matter (`vpn_next`).

## 7.12 `lib/claude_vm/mcp/` — the MCP server

`tools/mcp` is a stdio MCP server, built on the Model Context Protocol's Python SDK (the `mcp` group in
`pyproject.toml`), through which a Claude Code session in any project runs this checkout's recipes. `just mcp`
registers it with `claude mcp add`, one server per session, which Claude Code starts in the project's folder and the
server leaves for the checkout at once ([Agents in other projects](../guides/agents.md)). It is a thin adapter over the
justfile, not a second implementation: every rule and refusal stays the recipe's own, and the server adds only the few
of its own its modules name. Why: [§15.6](decisions.md#156-agents-run-the-recipes-through-mcp).

- **`config.py`: the configuration is the registered command's arguments.** The checkout, the Python `tools/env-run`
  found there, the absolute `just` and `uv`, and the `PATH` of the shell `just mcp` ran in, with `--attended`,
  `--any-branch`, `--vault-password-file` and `--browser`. A project's `.claude/settings.json` `env` reaches every
  server Claude Code starts there, so nothing is read from the environment; a command line `just mcp` never writes
  is kept as the refusal every tool answers with.
- **`recipes.py`: the tools are the recipes.** Each tool's input schema is made from its recipe's parameters in `just
  --dump --dump-format json`, the source `docs/reference/commands.md` is generated from too: a recipe's words are a
  list where their name is plural, one word where it is singular; a flag is a boolean; any other option a string,
  each with the `[arg]` help as its description (`HELP`'s words instead for `deploy`'s and `destroy`'s `names`, whose
  help offers the default VM the server refuses) and its pattern anchored as `just` matches it, whole. `TOOLS`,
  `NO_TOOL` and `LEFT_OUT` say which recipe and option is a tool's, and why the rest are not: `vault` has no tool, so
  no secret passes through the model. The argv is `just --justfile <checkout>/justfile`, every setting the justfile
  reads with `env()` at the server's value (`SETTINGS`, `ANSIBLE_ARGS` empty), the recipe, its options in their
  `--long=value` form, then its words, each one argv word. `just` holds each value to its pattern before the script's
  first line; a word that starts with `-`, which it would read as an option, is refused first.
- **`environment.py`: a run's environment is built, never inherited.** The few variables a login needs (`USER`,
  `SSH_AUTH_SOCK`, the locale, what `xdg-open` needs on Linux), the `PATH`, `HOME`, `XDG_DATA_HOME` and
  `CLAUDE_CONFIG_DIR` `just mcp` recorded - `HOME` and `XDG_DATA_HOME` decide where `tools/env-run` finds the
  checkout's folder, and the collections Ansible loads from it; `CLAUDE_CONFIG_DIR`, where the Claude Code the server
  is registered in keeps its sessions, which `code`'s session is looked for among and `just code` copies - `TERM=dumb` and `NO_COLOR=1`, and the vault password file and browser the arguments name:
  nothing of Ansible's, Python's, `just`'s or Claude Code's - its messaging token among them, which an editor's
  terminals would inherit.
- **`checkout.py`: the head, the branch guard, the change notice, the vault.** Every result names the checkout's
  branch and commit. `run_refusal` is the guard a run that changes something checks: `main` with no tracked change,
  unless `--any-branch`. A digest of what the server runs, from its start: the justfile its tools are made from,
  `tools/mcp`, which every job's supervisor runs anew, and `lib/claude_vm/`, some of which it imports only when a tool
  first needs it, read from the files, Python's caches and dot-names (`.DS_Store`, an editor's swap file) aside. Once
  one changes - a pull, a branch, an edit - every tool answers "restart this server with /mcp" rather than run a
  schema that no longer matches, or older code beside newer. The environment is not compared: after an install that
  moves a package the server runs on, the SDK above all, it is restarted by hand. A recipe that
  reads the vault is refused, before anything runs, when the vault is encrypted and there is no `.vault_pass` or
  recorded file, since a run has no terminal to ask the password at.
- **`results.py`: what the model reads.** Claude Code gives the model only a result's structured part when it has one
  (verified on 2.1.309), so a JSON tool (`status`, `settings`) returns its report as structured content with the head
  and the note that what the VMs said is data inside it, and a text tool (`logs`, `log`) text alone, registered with
  `structured_output=False` since the SDK would wrap a `str` as `{"result": ...}`. Text goes through `visible_text`.
  What a recipe prints on standard error beside a report it still made - `tools/check settings`' warnings, Ansible's -
  goes into the JSON as `warnings`, plain and at most 4 000 characters, and stays when the report is cut: the model
  reads nothing else of the run. Every result stays under 40 000 characters, a text cut from the middle and a report
  cut to the keys that say the most and how to read the rest: over about 50 000, Claude Code saves a result to a file
  in the session's folder, which `just code` copies to the VMs. So that a cut leaves nothing out of reach, `log` takes
  the line to `start` from, and says which lines of how many it returned.
- **`jobs.py`: a run that changes something is a job.** A deploy takes minutes, its sign-ins wait in the person's
  browser, Claude Code moves a call into the background after two minutes and ends its server with the session. So
  `deploy`, `login`, `code` and `install` start a supervisor - `tools/mcp --supervise=<folder>`, in a session of its
  own with the signals at their defaults - which runs the recipe in a process group of its own and writes
  `.cache/mcp/jobs/<id>/` (0700, the ID random hex): `job.json`, what ran and how it ended, and `output.txt`, what it
  printed through `visible_text`. Any server, this session's or a later one's, follows a job there. A job is running
  while its supervisor's pid runs the process of the start it recorded, never by pid alone - a start read as
  `tools/check lock` reads one, in C and UTC with its spaces collapsed - and jobs are pruned after
  `LOG_RETENTION_DAYS`, as the logs are. The server and the supervisor both write `job.json`, so each write is made
  under a lock (`job.lock`). A stop is SIGINT to the run's group, as a terminal's Ctrl-C, and the whole tree ended
  through `proc.py` 30 s later if it is still there: ansible-core's task workers leave the group. The tree is read
  before the SIGINT, since a run that ends at it leaves a worker under PID 1, where nothing leads to it; once the run
  has gone, what it left is ended by each process's start, never by the run's pid, which another may have by then
  (`proc.end_left`). A stop that comes
  before the run has a pid is recorded, and the supervisor sends it once the run has one: both write under the lock,
  so exactly one of them finds the stop and the pid. `cancel` follows the job from where its output ends, so a
  browser block an earlier call returned is not taken for the run waiting again.
- **`runs.py`: the call follows its job.** Each line the run prints goes as `notifications/progress`, which Claude
  Code shows the person under the running call (never the model), with a line a minute while the run is silent, which
  also keeps Claude Code's idle limit from ending the call. The call returns at the run's end, after `wait_seconds`,
  or the first time the run prints a browser block (`ACTION REQUIRED IN YOUR BROWSER`): by then the call is in the
  background, where nothing the person sees says a page waits, and only the agent can tell them. It returns the
  block, the job's ID and where the block ended; `job(id, since)` goes on from there, so no block is returned twice,
  and a call's result holds only what the run printed since it began following.
- **`cancels.py`: why a call was cancelled.** The SDK cancels a tool's task without the reason Claude Code gives, so
  the server reads its stdin through a pipe a thread feeds, noting each `notifications/cancelled` before passing the
  line on. A person's stop - Esc, `AbortError: user-cancel`; `x` in `/tasks`, `AbortError: The operation was
  aborted.` (verified on 2.1.309 to 2.1.319) - stops the run; anything else - the wall clock's `SdkError: Request
  timed out`, a reason not on the list, the session closing - leaves it to `job`, recorded. A later Claude Code that
  words a stop otherwise errs on the safe side: `cancel` still stops it.
- **A deploy that creates names its VMs.** One of the server's own refusals (`deploy_refusal`): `deploy` with no names,
  or `only="provision"` with none, would make the default VM, a convenience of the terminal's that an agent's forgotten
  argument must not reach. It is no `required` in the schema, since the stages that reach every VM, or none, take no
  names. With more than one host it wants `host` too, for a deploy that may create a VM - a name no alias places on a
  host: the current host is the terminal's, as the default VM is. A named VM that exists is found where it is.
- **The hosts** ([§7.14](#714-libclaude_vmhosts-and-toolshost--the-checkouts-hosts)). The `hosts` tool lists them
  (`just host --json`); none switches or removes one. Every run tool takes `host`. `destroy`'s dialog names the host it
  deletes from, and its code holds the host, so an answer for one host's `alpha` is none for another's; the run is
  then given that host, whatever is current by the time it starts (`run_host`: the host given, else where the VMs'
  aliases place them, else the current one). Every run tool names the host it is against the same way
  (`with_host`), so the job's title, and every line that shows it, says the host.
- **`ATTENDED`.** A run tool passes `ATTENDED=1` - the pages open and the sign-ins run, and nothing asks - exactly when
  the session is an interactive one on this machine: Claude Code gives its servers `CLAUDE_CODE_ENTRYPOINT=cli` then,
  `sdk-cli` for `claude -p`, and the server reads `SSH_CONNECTION` from its own environment. `just mcp --attended`
  overrides it. A Remote Control session gives its server the same environment as a plain one, so the early return,
  not the attended decision, is what keeps a person from missing a page.
- **The gated tools.** `destroy`, `settings_set`, `init` and `install` carry `anthropic/requiresUserInteraction`,
  which makes Claude Code ask before every call whatever the permission rules say (no hook of the calling project's
  answers it; `-p` and `dontAsk` deny the tool), and run only for Claude Code 2.1.309 or later, the one client whose
  prompt was seen - Claude Desktop most likely runs such a tool unasked.
- **`confirm.py`: the dialog.** `destroy` and `settings_set` also ask in a form elicitation, which shows what is at
  stake - each VM's ID, node, state and size and its work on no remote, from `just status --only=vms,work`; the
  setting's value now and the new one, from `just settings` - and wants a code with the names. The question is the
  tool's first answer, an SDK `Resolve` whose resolver returns `Elicit`: on the 2026-07-28 revision a request sent
  mid-call fails, so the SDK answers the call with an input request, Claude Code shows the dialog and calls again with
  the answer, and only then does the body run - the one place that starts `just destroy --confirm=<names>` or `just
  settings --value`. A project's own `Elicitation` hook can answer a dialog (verified), so the prompt is the human
  gate; the code, an HMAC of the question under a key of the server's, the same each time the SDK asks it again,
  makes a generic accept-every-dialog hook fail. A decline, a cancel - every dialog in `claude -p` - or other words
  run nothing, and the result is the command to run at a terminal. `destroy` takes its names as required, and says a
  held run lock before it asks anything. `settings_set` changes only the settings on `ALLOWED`; `REFUSED` names every
  other setting of `local.yml.example`, so a new one is a decision, and a role variable in no example is refused
  too - an agent steered by what it read must not move `github_projects_api_url`, which the vault's token is sent
  to, or `claude_code_installer_url`, which every VM runs as root. Where a setting's type lets more through, `VALUES`
  holds its value: `claude_code_version` is `stable`, `latest` or a version, since with npm as the install method it
  is the version npm installs, and npm takes any spec there - another package, a repository - as root on every VM.
  What `tools/settings` refuses still applies under it. `init` takes no `--token-secret-file`: the file's contents would go into the vault and to the Proxmox host as
  the token, any file this user can read among them.
- **`server.py`: it always starts.** Claude Code pins a stdio server that dies within 3 s of its start to the older
  protocol revision for 7 days (2.1.318's changelog), which would outlast the fix, so a bad command line, a checkout
  without a justfile or a `just` that cannot read it is each tool's refusal, never an exit. A recipe runs with stdin
  `/dev/null`, in a session of its own (`start_new_session`) with SIGINT, SIGTERM, SIGHUP and SIGQUIT at their
  defaults, cwd the checkout; a call cancelled ends it and everything it started. The tools are async, so a call that
  waits on a run never holds up another. Its instructions, which every session on the machine carries, stay a few
  lines; what a tool needs is in its description, which tool search loads only when the agent picks the tool.

`tests/unit/test_mcp_tools.py` holds every recipe and option to a tool or a reason, each schema to JSON Schema
2020-12 and to `just`'s patterns, every `env()` setting to the server's command line at the justfile's default, and
each tool's function to its schema's properties. `test_mcp_server.py` drives the server over stdio with the SDK's own
client, against a copy of the checkout and stand-in Ansible tools; `test_mcp_jobs.py` the same for the runs - a job's
argv and `ATTENDED`, the early return and `job`, a job outliving its server, `cancel`, the branch and the lock refused,
`install` for no other client - and the cancellation reasons; `test_mcp_confirm.py` the confirmations - the prompt on
every gated tool, the dialog's lines, `destroy --confirm` only for the code and the names, nothing for a decline, a
cancel, a generic hook's answer or another client, the allow list; `test_mcp_register.py` runs `just mcp` with a
stand-in `claude`.

## 7.13 `lib/claude_vm/golden.py` — what a golden template is built from

A golden template is a VM template with the toolchain already installed, cloned in place of the base template so that
configure finds most of its work done. Configure still runs every task on such a VM: the image is a head start, never
trusted, so a mistake in it can leave a VM less ready, never wrong.

- **What it carries.** The `image` entry point of `common`, `dev_tools` and `claude_code` (`BAKED`), each doing less
  than its role's `main`, never more: `common`'s apt, packages, timezone, user, sshd hardening, PATH, tmux and
  services, without the hostname, the keys, sudo, the PATH drop-in for ssh commands or the masked units, which baked
  in failed every clone's first-boot upgrade; all of `dev_tools`, under its switches; `claude_code`'s binary and an
  empty `~/.claude`, without settings, Remote Control or a sign-in. The VPN's and the editors' roles have no `image`
  entry point: a golden never carries them, so they stay independent of it.
- **The toolchain digest** (`toolchain_digest`) hashes every file the three entry points reach - the task files they
  include by literal name, the templates and files those name, each role's defaults and internals, a module of its
  library - with the scrub the build runs, and the options each role's `image_options` entry point resolves, its
  defaults included. `reached` is that walk: a pull that changes only a role's sign-in makes no new golden. An option
  that holds a vault value of 4 characters or more (`secret_options`) means no golden is built or used, said by the
  option's name and never its value.
- **The key** (`golden_key`) is the build number, the base's identity and the digest, hashed. The base's identity is
  the provider's to give, so the digest knows no hypervisor.
- **The static guard** (`tests/unit/test_golden.py`) holds every expression in the files an `image` entry point
  reaches to that entry point's options, the role's internals that read only those, values the entry point makes
  itself (registers, facts it sets, loop variables), and the OS facts. It fails on `inventory_hostname`, `hostvars`,
  `groups`, a vault name, a `no_log` option, and any lookup but a literal `ansible.builtin.file` of the role's own
  `files/`, which the digest hashes. No `image` option is `no_log`, and each role's `image_options` records exactly its
  `image` entry point's options, but those that read the VM's facts.

## 7.14 `lib/claude_vm/hosts/` and `tools/host` — the checkout's hosts

A checkout can drive more than one Proxmox host. A host is an inventory source laid over the shared one, never a
variable: the first host stays in `inventory/` (`inventory/hosts.yml` and the shared `group_vars/all/`), and each
other is a folder of its own, `hosts/<name>/` - its `hosts.yml`, the one host of the `proxmox` group named as the
folder, and its `group_vars/all/local.yml` and `vault.yml`. A run against it gets
`ANSIBLE_INVENTORY=inventory/controller.yml,hosts/<name>`: `inventory/controller.yml` as a file source loads the shared
`group_vars/all/` beside it but not `inventory/hosts.yml`, so `groups['proxmox']` holds that host alone, and the
host's `group_vars/all/` come second, so they outrank the shared ones. Every playbook and role then works as written:
`proxmox_api_host`, `hosts: proxmox`, the template's build, init's checks and the name refusals all read the run's one
host. `hosts/current` names the current host; `hosts/` is git-ignored and refused by `tests/check_no_local_files.sh`.

- **One resolver** (`tools/check host`, which runs `tools/host`; the layout in `layout.py`) is the one place that
  knows a path. Each VM recipe asks it, in the line `{{ host_env }}`, after its own rules and before its lock: the
  host of the run - `--host`, else a nested recipe's `nested_host=`, else the host the VMs named are on, as their
  aliases record (`# origin`; names on two hosts are refused, one host per run, and beside `--host` a name another
  host's alias records is warned of and reached on the host given), else `hosts/current`'s, else the first - and, for the
  lines after it, `ANSIBLE_INVENTORY` exported for a host in `hosts/`, `$settings_files` and `$vault_files` (the
  shared ones first) for the checks, `$host_sources` for `tools/status`'s `-i`, and `$host_extra`, the other hosts'
  names and addresses, which `vm_args` passes as `claude_hosts_other`. With one host, and none asked for, it reads
  nothing and a run is as it always was. `VAULT_FILE`, `SETTINGS_FILES` and `INIT_FILES` stay the first host's, and
  given beside another host are refused; an `ANSIBLE_INVENTORY` you exported is left alone for the first host and
  refused beside another.
- **The kinds table** (`kinds.py`): for each kind of host, as literal data, its inventory group, the settings each
  host of that kind always sets for itself, those of one network that are never inherited, its secret's two vault
  names and what `just host` shows. The resolver, `just host` and `just status` read it and name no Proxmox setting.
- **What never falls through.** The first host's own settings live in the shared files, so a host in `hosts/` would
  inherit whatever it leaves unset. Its token's secret: `proxmox_api_token_secret` in the inventory defaults reads the
  shared `vault_proxmox_api_token_secret` only while the run's Proxmox host was defined in the shared inventory's own
  folder (its `inventory_dir` is localhost's), and `vault_host_proxmox_api_token_secret` otherwise - one expression,
  keyed on where Ansible recorded the host, with no variable a git-ignored file could set to switch it off. A host
  whose vault lacks it is an undefined variable before any request. The resolver refuses either name in the other's
  vault, reading the vaults' names in memory with the recipe's password file and keeping a check that passed in
  `hosts/.checked.json`, by the files' sizes and times. The kind's always-written settings (for Proxmox, the node,
  storage, bridge, API user, token ID, port, certificate checking and template VMID) are refused missing from the
  host's `local.yml`, naming `just init --host`; and a setting of one network (`vm_ipconfig`, `vm_nameservers`,
  `proxmox_template_custom`, `claude_ssh_host_domain`) the shared `local.yml` sets and the host's does not is refused,
  naming the line to add.
- **The other hosts stay protected.** With one host in each run's inventory, three guards would lose sight of the
  others: `claude_vm_inventory_hosts`, `claude_vm_inventory_addresses` and with them `proxmox_vm_known_hosts_kept`
  add `claude_hosts_other`, so no VM is named after another host, no alias takes its address, and destroy never
  forgets its key.
- **`just host`** lists the hosts from their files alone, writes `hosts/current` anew and renames it over the old
  under the run lock, so a run under way ends on the host it started on, and `--remove` deletes a host's folder after
  its name is typed at a terminal - never the current host's or the first's, and never its VMs.
- **`just init --host <name>`** sets one up (`tools/check host init`, [§4.12](lifecycle.md#412-playbooksinit_settingsyml)),
  and **`just vault --host`** and **`just settings`** reach a host leniently (`--lenient`): one that lacks a setting
  it sets for itself, since they are how that is put right.
- **`just status`** reads every host at once, a child process each, drawn as one report
  ([§4.6](lifecycle.md#46-toolsstatus--read-only-drawn-live)); with `--host`, one host. It reads a host in `hosts/`
  through its sources as `-i`, so that its files key the settings kept,
  every vault loaded (`--vault-file` each), and keeps each host's settings in a folder of its own,
  `.cache/status/<host>/`; the token's vault name is kept with the snapshot.
- **A line that says what to run names the host.** With more than one host, `claude_host_flag` (`" --host <name>"`,
  empty with one) ends every `just ... <vm>` line a run prints or writes onto a VM: the retries (`retry_line`,
  `sign_in_failed.yml`); the roles' commands, which NEXT STEPS and their messages say, through `claude_code_host_flag`,
  `github_projects_host_flag` and `tailscale_host_flag`, wired from it; `code.yml`'s `code_editor_flag`, after any
  `--editor`, and the editor's plan, which takes the two apart (`editor_flag`, `host_flag`, each held to its form) for
  its report and the Claude terminal's lines; and the session notes, whose header names the host and which carry it
  in a marker (`<!-- agent-warehouse:on --host <name> -->`) that `session_notes.py`, the window's notes task, reads
  for the rows it writes - a host's name only, so no helper's arguments changed. A VM named is found by its alias
  anyway; the flag reaches it where no alias records it. `just status`'s ATTENTION adds it in the parent, and
  destroy's `sessions.json` records the host.
