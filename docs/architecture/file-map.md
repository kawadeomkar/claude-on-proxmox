# 14. File map

```
ansible.cfg                       inventory dir, accept-new host keys, forks, the stdout callback, YAML results
callback_plugins/agent_warehouse.py   the default output, the waits set apart, NEXT STEPS after the recap, just deploy
                                  drawn live (§7.5)
.logs/                            one log per just run that used Ansible (git-ignored, 0700, pruned after 14 days)
.sessions/                        the Claude sessions just destroy saved from each VM (git-ignored, 0700, kept until deleted)
hosts/                            the checkout's hosts but the first, one folder each - hosts.yml, group_vars/all/local.yml
                                  and vault.yml - and current, the current host's name (git-ignored; just host, §7.14)
deploy.yml                        template (when missing) + provision + configure + the three sign-ins + the editor
CLAUDE.md                         agent-facing operating rules
README.md                         getting started; everything else is in docs/
CONTRIBUTING.md                   setting up, testing a change, the conventions, releasing
SECURITY.md                       the security policy: how to report, the scope, the known trade-offs
LICENSE                           MIT
Vagrantfile                       configure.yml on a real VirtualBox VM (just vagrant), with no real credentials
docs/                             the wiki (docs/development/docs.md): getting-started/, guides/, reference/
                                  (generated in part), architecture/ (these pages), development/
zensical.toml                     the wiki as a site: navigation, theme, GitHub-style anchors
justfile                          every entry point: few recipes, each narrowed by an option or a word (just help)
tools/docs.py                     just docs: the generated reference sections; templates/role.md.j2 for docsible
tools/env-run                     run a tool from the project's environment (the git hooks) - yours, or the checkout's own - or --bin: its bin (the justfile); --data, --collections: the checkout's folder
tools/check                       the justfile's checks: the checkout, the run's host, the vaults, the collections, names, a project
tools/git-hook                    the pre-commit git hook just init installs: pre-commit from the committing checkout's environment
tools/run-logged                  run a command with its Ansible output logged to .logs/ (every recipe that runs Ansible)
tools/live                        a recipe's command drawn as a part of one run, or passed through as it ran; the run's end
                                  from the recipe's trap (§7.8)
tools/status                      just status: the engine's command line (lib/claude_vm/status/engine.py, §7.7)
tools/host                        the checkout's hosts (tools/check host): the hosts core's one adapter, which prints
                                  the host of a run and the files it is read from, and performs just host's switch and
                                  removal and just init --host's files (lib/claude_vm/hosts/, §7.14)
tools/settings                    just settings: each setting's value and where it was set, from Ansible's own variables (§7.9)
tools/mcp                         the MCP server just mcp registers with Claude Code: its tools the recipes (§7.12)
tools/stale_scripts.py            just install: the locked packages whose commands start another Python, to reinstall
tools/unit_durations.py           tests/unit/durations.json from a run's JUnit report: what the unit tests' shards balance by
tools/molecule-lanes              just test molecule --jobs 4: the scenarios that many at a time, each in a log of its own
tools/proxmox-access.sh           on the Proxmox host, as root: the API role, user, grants and token, what is missing of them
pyproject.toml, uv.lock           the pinned and locked Python tooling (§13); requirements.yml: the Galaxy collections
.ansible-lint, .yamllint          the linters' settings: ansible-lint's production profile, and yamllint, which skips
                                  what git ignores
.pre-commit-config.yaml           the hooks run on commit (tools/git-hook) and by just lint, outside ones pinned by commit
.gitleaks.toml                    what CI's secret scans of the tree and of the commits leave out
.gitignore, .editorconfig         what git leaves out - your settings, the vault, logs, the sessions saved - and how
                                  files end their lines

playbooks/
  template.yml                    one-time: build the cloud-init template (SSH to PVE)
  golden.yml                      the template each new VM clones: a fresh golden of its key, else the base (§4.14)
  golden_build.yml                last of just deploy: builds a golden for the next VMs, deletes what earlier runs left (§4.14)
  provision.yml                   three plays: register → create in parallel → publish
  discover.yml                    find VMs by tag, resolve addresses via the guest agent
  claude_login.yml                sign Claude in by the browser, VM by VM, then start Remote Control; bare, report
  github_login.yml                sign gh in by the device flow, VM by VM, then run github_projects; bare, report
  tailscale_login.yml             sign the VM in to Tailscale by its web login, VM by VM; bare, report
  login.yml                       just login: discovery, then the three sign-ins above in deploy's order and by its rules
  init_settings.yml               just init's questions: the GitHub username, Tailscale, the host's address, the token,
                                  the offer to make it, its secret; the vault encrypted
  init_check.yml                  just init's checks: the API and token, privileges, the template or root SSH, node, storage, bridge, a key
  inventory/init.yml              init_settings.yml's own inventory: the controller alone, no group_vars
  vars/init.yml                   what init_settings.yml and init_check.yml share: init_ask, init_given, the files,
                                  the examples' placeholders, the reads of local.yml and the vault, the YAML validate
  configure.yml                   discover, then common → vscode_server → dev_tools → claude_code → github_projects,
                                  then tailscale in a rescued block, then the Proxmox record of each Tailscale login,
                                  then an SSH alias per configured VM on the controller, and a last play failing what
                                  the rescue recorded
  destroy.yml                     confirm, verify ownership, save each VM's own Claude sessions, release (Claude, GitHub,
                                  Tailscale), delete in parallel, forget each alias
  ssh_config.yml                  refresh every alias from the fleet; fleet-wide, prune the vanished
  code.yml                        refresh one alias, copy this machine's Claude sessions, write the editor's files
                                  (VS Code's workspace file, or Zed's task) and the session notes, open the window
  library/                        just code's session handoff and destroy's save: claude_sessions_info, git_checkouts_info
                                  (reads, here and on the VM), claude_sessions_copy (the VM's copies, their four
                                  edits, the manifest) and claude_sessions_fetch (destroy's tar from the VM, unpacked
                                  here as plain files and folders only)
  action_plugins/ask.py           just init's questions: pause's prompt, a yes or no's or a choice's answers carried
                                  for the menu the live engine asks it as (§7.8)
  module_utils/                   agent_warehouse.py, what those modules share, sent with each: the session ID,
                                  the manifest, a transcript's line, a folder's folders, a version's order, whether
                                  a process is claude; git_checkouts.py, reading a checkout without changing it;
                                  status_probe.py, what just status reads inside a VM, sent over stdin (§7.7)
  templates/vm_session.md.j2      the session notes: a page about one VM, opened beside VS Code's Claude terminal
  vars/vscode.yml                 code.yml's VS Code variables: its helpers, and where the user's VS Code settings
                                  are (read, never written)
  vars/sign_in.yml                the rules the three sign-in plays share, written once: names given, one VM at a
                                  time, whether GitHub's and Tailscale's start or ask, a sign-in held back or left unasked
  vars/controller_writes.yml      what the project writes on this machine outside the checkout: the one list the
                                  session notes show and the security model renders
  tasks/vscode_check.yml          just code's VS Code on this machine: code on PATH, the Remote - SSH extension
  tasks/zed_check.yml             just code's Zed on this machine: the zeditor or zed on PATH that says it is Zed
  tasks/editor_open.yml           every editor's window, its plan applied: the directories, helpers and files on the
                                  VM, the window opened, what is said (lib/claude_vm/editors/, §7.10)
  files/                          the helpers, copied to the VM unchanged and run there by the window's tasks:
                                  VS Code's claude-terminal.sh (the Claude terminal), session-notes.sh and
                                  session_notes.py (the notes' live section); any editor's claude-start.sh (a
                                  Claude task's terminal, never a second claude on one conversation)
  tasks/cluster_vms.yml           one /cluster/resources listing, shared by the playbooks above
  tasks/vm_addresses.yml          each VM's config and agent reply, two requests to its own node
  tasks/golden_generalize.yml     the scrub a golden template's build VM gets: its first boot checked, every identity
                                  deleted, nothing signed in left, cloud-init made to run afresh (§4.14)
  tasks/vm_stop.yml               stops again a VM destroy started for its release and then is not deleting
  tasks/vm_names.yml              refuses a VM name given that is a host of the inventory (pve, localhost) or cannot be its alias
  tasks/default_name_1x.yml       refuses a bare deploy or destroy while the default VM has its 1.x name alone (provision, destroy)
  tasks/vms_requested.yml         the tagged VMs asked for and their addresses, those named after a host of the
                                  inventory left out and said, two of one name refused (discover, ssh_config)
  tasks/vpn_members.yml           a VPN's plan of who is on its network (lib/claude_vm/vpn/), its refusals first
  tasks/vpn_apply.yml             the plan applied: each VM's marks (configure.yml, tailscale_login.yml)
  tasks/vm_marks.yml              each VM's marks as tags in Proxmox, VM by VM, the one writer of one: that its
                                  Tailscale has had a login, so the vault's key is never sent to it again
  tasks/sign_in_failed.yml        a VM a sign-in did not sign in - not reached, or its sign-in failed - remembered,
                                  said and kept for NEXT STEPS (claude_login.yml, github_login.yml, tailscale_login.yml)
  tasks/sign_in_held.yml          GitHub's or Tailscale's sign-in held back for a VM, said and kept for NEXT STEPS
  tasks/ssh_config.yml            one blockinfile block per VM in ~/.ssh/agent-warehouse.conf, Include once
  tasks/session_read.yml          which of this machine's sessions go to which VM checkout; --session's checks
  tasks/session_write.yml         rsync to the VM's mirror, the copies, --session's checks after the fetch and fast-forward
  tasks/session_save.yml          destroy's save of what of a VM's Claude sessions is only there, into .sessions/
  tasks/remote_tmp.yml            root's remote_tmp, made before a module makes it and warns (template, configure)
  tasks/init_ask.yml              one just init question: a value given, or asked, held to its kind, asked once more
  tasks/init_local_setting.yml    one setting written into local.yml in place of its commented example line
  tasks/init_choose.yml           one of just init's choices: a storage or bridge the node lists and the token can use
  tasks/init_access.yml           just init's offer: tools/proxmox-access.sh on the host over root SSH, shown, then run on y
  tasks/init_access_choose.yml    the offer's storage or bridge, among what the host lists to root

roles/                            one role per concern, each with defaults/, meta/argument_specs.yml, molecule/
  proxmox_template/               qm + virt-customize on the PVE node; tasks/golden_read.yml, golden_select.yml, golden_build.yml,
                                  golden_template.yml, golden_collect.yml and golden_discard.yml, a golden template's
                                  entry points, on this machine through the API (§4.14)
  proxmox_vm/                     the Proxmox API role (tasks/{main,present,absent}.yml, vars/main.yml)
  common/                         OS baseline, user, sudo, keys, sshd hardening, PATH for ssh commands; tasks/apt.yml,
                                  timezone.yml, user.yml, sshd.yml, profile.yml, shared by main.yml and image.yml, what
                                  a golden template carries, and image_options.yml, the options it is built with (§7.13)
  vscode_server/                  sshd forwarding drop-in checked with sshd -T, inotify limit
  dev_tools/                      Node.js, Docker, gh, uv, Rust  (files/*.asc are vendored signing keys); tasks/image.yml
                                  and image_options.yml, what a golden template carries and is built with (§7.13)
  claude_code/                    native or npm install, settings.json, Remote Control and the path unit that
                                  starts it when a login lands; tasks/login.yml, browser_login.yml (the sign-in),
                                  after_login.yml, home.yml (the user's home, for login and status),
                                  unit_absent.yml; release.yml, and release_pause.yml and
                                  release_resume.yml around destroy's save of the VM's sessions;
                                  install.yml, the binary, which image.yml bakes into a golden template, and
                                  image_options.yml, the options it is built with (§7.13)
  github_projects/                tasks/auth.yml (gh sign-in, credential helper), clone.yml, login.yml (login_read.yml,
                                  login_report.yml), device_flow.yml (the sign-in);
                                  library/github_repos.py, the listing, and git_clone_many.py, the clones, several at
                                  a time, each whole or not at all (§5.6)
  tailscale/                      tasks/ownership.yml, install.yml, state.yml, connect.yml (the key), settings.yml,
                                  report.yml, unmanaged.yml (off), login.yml and web_login.yml (the sign-in);
                                  files/tailscale.asc (the vendored signing key)

lib/claude_vm/                    the controller's Python as one package (§7.10): records.py, every record's base,
                                  and wire.py, its one way out; refs.py, a VM and a command as data; plugin.py, what
                                  every adapter in filter_plugins/ shares; text.py, what a VM said made plain (§7.4);
                                  tui/live_region.py, every live output's region: drawn in place at the bottom of a
                                  terminal, all else above, and tui/screen.py, its lines: styled segments, cut,
                                  aligned, coloured in one palette (§7.8); tui/choose.py, a question with a few
                                  answers asked as a menu picked with the arrow keys, its answers carried in
                                  built-in bytes (§7.8); proc.py, the processes under one, and
                                  their end: what a kill must reach beyond the group it was sent to - ansible-core's
                                  detached task worker, a nested run's parts (§7.8)
                                  run/recipes.py, every command drawn live, declared: its stages and steps, named
                                  by its title (§7.8); run/model.py, a live run as stages, steps and tasks (just
                                  deploy's first), and tui/run.py, it drawn: its frame, a line per step, the summary
                                  (§7.5)
                                  golden.py, a golden template's toolchain digest and key, and the walk of the files a
                                  role's image entry point reaches, which its static guard shares (§7.13)
                                  run/next_steps.py, NEXT STEPS from the reports' set_stats records, by VM and matter,
                                  as the callback and a run in parts say them (§7.5)
                                  ansible/parts.py, a run in parts: the driver every drawn run shares, a command drawn
                                  as a part, the state between parts, the run's end (§7.8)
                                  ansible/engine.py, the engine that draws a playbook run live: Ansible's events in,
                                  the region, the run's own words and the summary out; the stdout callback hands it
                                  every event (§7.5)
                                  status/, just status: engine.py the run, settings.py, reach.py and found.py, lines.py
                                  what the roles say too, model.py its model, hosts.py every host's run at once, a
                                  child each, and tui/status.py, its report drawn (§7.7)
                                  wiring.py, the settings' wiring, which tools/docs.py renders and tools/settings reads
                                  (§7.9)
                                  store.py, a file replaced atomically under a lock on its folder: tools/settings'
                                  write of local.yml (§7.9)
                                  vault.py, the vault read without Ansible: its names for the hosts' check, its values
                                  for just status, how Ansible finds and runs a password file (§7.10)
                                  hosts/, the hosts core, which reads and decides and never writes: layout.py the files
                                  as Host records, kinds.py the kinds of host, model.py the records, choose.py which
                                  host and the list, resolve.py a run's checks and every host for just status,
                                  setup.py init, the switch and the removal as plans (§7.14)
                                  mcp/, tools/mcp's server: config.py, its registered arguments; recipes.py, the
                                  tools' schemas from just --dump and a call's argv; environment.py, a run's
                                  environment from an allow list; checkout.py, the head, the branch guard, the
                                  change notice; results.py, what the model reads; jobs.py, a run as a job that
                                  outlives the call, under .cache/mcp/jobs/; runs.py, a call following its job, its
                                  early return at a browser block; cancels.py, a cancellation's reason, from the
                                  server's stdin; confirm.py, the confirmations' dialogs and code, and the settings
                                  an agent may change; server.py, the tools (§7.12)
                                  hosts/: kinds.py, the kinds of host as literal data - each one's group, the settings
                                  each host of it sets for itself, its secret's vault names; layout.py, where each of
                                  the checkout's hosts is, and which one a run gets (§7.14)
                                  catalogue.py, what the choices a checkout makes once share - the editor, the
                                  VPN: each a setting naming a row of a list of the defaults (§7.11)
                                  editors/: model.py, the editors' records; family.py, the seam; common.py, what
                                  more than one family uses, the helpers' directory and the marker among it; vscode.py
                                  and zed.py, each family's plan of its window; registry.py, which every plan passes
                                  (§7.10)
filter_plugins/proxmox.py         the adapter of lib/claude_vm/proxmox.py and checks.py, reading guest agent output:
                                  net_mac, guest_ipv4, guest_addresses;
                                  a VM's tailnet address: tailnet_ipv4; a refused token told from a slow boot:
                                  proxmox_access_denied; the managed SSH config: ssh_config_blocks, ssh_config_prune,
                                  ssh_alias_refusal; vms_not_named, the listing without a VM named like an inventory host;
                                  built_template, the template of that name in a listing; duplicate_names, the names
                                  two VMs of it carry; proxmox_tags, a VM's tags as a list
filter_plugins/golden.py          the adapter of lib/claude_vm/golden.py: toolchain_digest, what a golden template is
                                  built from, hashed, and golden_key, its key with a base's identity (§7.13)
filter_plugins/editors.py         the adapter of lib/claude_vm/editors/registry.py: editor_open_plan, just code's
                                  window as a plan, and editor_servers, each editor's window as just status's probe
                                  knows it (§7.10)
filter_plugins/desktop.py         the adapter of lib/claude_vm/desktop.py: page_opener, the sign-ins' browser command;
                                  controller_run and its state, the run a Claude sign-in waits for (§7.4)
filter_plugins/text.py            the adapter of lib/claude_vm/text.py: plain_text, what a VM printed made safe
                                  to quote;
                                  one_line, one line of it; full_stop, retry_line and and_list, the words messages
                                  are built with; banner, the block a prompt is set in; and plain_summary, a
                                  module's warnings and errors made plain, which the callbacks import
filter_plugins/status.py          the adapter of lib/claude_vm/status/lines.py: what the roles say of a VM's sign-ins (§7.7)
filter_plugins/settings.py        the adapter of lib/claude_vm/init.py and checks.py: init_answer, the rule each
                                  just init answer follows; with_setting, one written into YAML text;
                                  init_candidates and init_choose_rows, what the token can use among a node's storages or bridges;
                                  init_pick_question and init_pick, how one of them is asked for and what an answer picks;
                                  proxmox_name(s), a node's, storage's or bridge's name as Proxmox gives one
filter_plugins/sessions.py        the adapter of lib/claude_vm/sessions.py: claude_sessions_plan, which session goes to
                                  which VM checkout by origin, owner/repo of a remote URL here and of a GitHub one on
                                  the VM;
                                  claude_project_folder, Claude Code's folder for a path; claude_session_dirs;
                                  projects_dir, the projects directory on a VM; just_code_dir,
                                  just code's own directory on a VM; claude_sessions_saved, what of a VM's sessions destroy saves;
                                  claude_sessions_listed, how just code says the sessions it left out
inventory/
  hosts.yml.example               the only address anyone supplies
  controller.yml                  localhost, so it picks up group_vars/all
  group_vars/all/defaults.yml     shared defaults + the role wiring
  group_vars/all/local.yml.example   every setting, commented: the settings reference is rendered from it
  group_vars/all/vault.yml.example   every secret, for the vault

tests/
  check_no_local_files.sh         tracked-file guard (also runs in CI)
  unit/                           pytest: filters, github_repos, the guard, configure tags, Remote Control, the three
                                  sign-ins and deploy's sign-in steps, Tailscale, destroy's release, the managed SSH
                                  config, the Claude terminal's script, the justfile, just status and every live
                                  output - the model, each recipe drawn, tools/live's runs in parts (live_kit.py runs
                                  each at a terminal of its own; vt.py, a small terminal they read back through;
                                  interrupts.py, SIGTERM and SIGHUP taken as a Ctrl-C in every pytest process) -
                                  and the wiki (test_docs.py)
  molecule/                       shared fakes (§11.4): fake_pve_api.py, the PVE API and its guest agent;
                                  fake_qm.sh, fake_virt_customize.sh and fake_virt_cat.sh, the template's tools; fake_pve_cli.py,
                                  the host's commands tools/proxmox-access.sh runs; fake_github_api.py and
                                  fake_gh.sh; fake_claude.sh; fake_tailscale.sh and fake_tailscale_api.py;
                                  fake_anthropic_api.py, the Messages API for the real claude in configure.
                                  And the scenarios' shared task files: prepare_pve.yml and prepare_github.yml
                                  start the fake APIs, pve_vm.yml makes fixture VMs through the PVE one, and
                                  refused.yml runs a role where it must refuse and asserts why
molecule/
  provision/                      provision.yml + discover.yml against the fake API
  configure/                      configure.yml end to end in a container
                                  (in both, group_vars/all/defaults.yml is a symlink to the real one; keep it a git checkout)
.config/molecule/config.yml       driver, shared provisioner settings, the dependency step off
.config/ansible-lint-rules/       agent_warehouse_module_utils.py, no rule: puts playbooks/module_utils/ on the
                                  path in ansible-lint's own process, where its args rule imports the modules in
                                  playbooks/library/
.github/                          CI, releases and the sections of their notes (release.yml), the labels
                                  pull requests get from their titles, the docs site, the composite setup
                                  action, Dependabot (dependabot.yml) and the CODEOWNERS that asks for its pull
                                  requests' review
```
