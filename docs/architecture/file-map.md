# 14. File map

```
ansible.cfg                       inventory dir, accept-new host keys, forks, the stdout callback, YAML results
callback_plugins/claude_on_proxmox.py   the default output, the waits set apart, NEXT STEPS after the recap, just deploy
                                  drawn live (§7.5)
callback_plugins/claude_on_proxmox_status.py   just status's own: the report drawn live at a terminal (§7.7)
.logs/                            one log per just run that used Ansible (git-ignored, 0700, pruned after 14 days)
.sessions/                        the Claude sessions just destroy saved from each VM (git-ignored, 0700, kept until deleted)
deploy.yml                        template (when missing) + provision + configure + the three sign-ins + VS Code
CLAUDE.md                         agent-facing operating rules
README.md                         getting started; everything else is in docs/
docs/                             the wiki (docs/development/docs.md): getting-started/, guides/, reference/
                                  (generated in part), architecture/ (these pages), development/
zensical.toml                     the wiki as a site: navigation, theme, GitHub-style anchors
justfile                          every entry point: few recipes, each narrowed by an option or a word (just help)
tools/docs.py                     just docs: the generated reference sections; templates/role.md.j2 for docsible
tools/env-run                     run a tool from the project's virtualenv (the git hooks), or --bin: its bin (the justfile)
tools/check                       the justfile's checks: the checkout, the vault, the collections, names, a project
tools/run-logged                  run a command with its Ansible output logged to .logs/ (every recipe that runs Ansible)
tools/live                        a recipe's command drawn as a part of one run, or passed through as it ran; the run's end
                                  from the recipe's trap (§7.8)
tools/status-exit                 just status's last step: the report's exit code, the report under --json, then no report
tools/settings                    just settings: each setting's value and where it was set, from Ansible's own variables (§7.9)
tools/stale_scripts.py            just install: the locked packages whose commands start another Python, to reinstall
tools/unit_durations.py           tests/unit/durations.json from a run's JUnit report: what the unit tests' shards balance by
tools/proxmox-access.sh           on the Proxmox host, as root: the API role, user, grants and token, what is missing of them
pyproject.toml, uv.lock           the pinned and locked Python tooling (§13); requirements.yml: the Galaxy collections

playbooks/
  template.yml                    one-time: build the cloud-init template (SSH to PVE)
  provision.yml                   three plays: register → create in parallel → publish
  discover.yml                    find VMs by tag, resolve addresses via the guest agent
  status.yml                      just status: read-only, what every VM is doing and what needs doing next
  claude_login.yml                sign Claude in by the browser, VM by VM, then start Remote Control; bare, report
  github_login.yml                sign gh in by the device flow, VM by VM, then run github_projects; bare, report
  tailscale_login.yml             sign the VM in to Tailscale by its web login, VM by VM; bare, report
  login.yml                       just login: discovery, then the three sign-ins above in deploy's order and by its rules
  init_settings.yml               just init's questions: the GitHub username, Tailscale, the host's address, the token,
                                  the offer to make it, its secret; the vault encrypted
  init_check.yml                  just init's checks: the API and token, privileges, the template or root SSH, node, storage, bridge, a key
  inventory/init.yml              init_settings.yml's own inventory: the controller alone, no group_vars
  configure.yml                   discover, then common → vscode_server → dev_tools → claude_code → github_projects,
                                  then tailscale in a rescued block, then an SSH alias per configured VM on the
                                  controller, and a last play failing what the rescue recorded
  destroy.yml                     confirm, verify ownership, save each VM's own Claude sessions, release (Claude, GitHub,
                                  Tailscale), delete in parallel, forget each alias
  ssh_config.yml                  refresh every alias from the fleet; fleet-wide, prune the vanished
  code.yml                        refresh one alias, copy this machine's Claude sessions, write the Claude workspace
                                  file and the session notes, open it
  library/                        just code's session handoff and destroy's save: claude_sessions_info, git_checkouts_info
                                  (reads, here and on the VM), claude_sessions_copy (the VM's copies, their four
                                  edits, the manifest) and claude_sessions_fetch (destroy's tar from the VM, unpacked
                                  here as plain files and folders only); vm_status_info (just status: the OS, kernel,
                                  load, upgrades and the user's processes, read only)
  templates/vm_session.md.j2      the session notes: a page about one VM, opened beside the Claude terminal
  tasks/cluster_vms.yml           one /cluster/resources listing, shared by the playbooks above
  tasks/vm_addresses.yml          each VM's config and agent reply, two requests to its own node
  tasks/ssh_config.yml            one blockinfile block per VM in ~/.ssh/claude-on-proxmox.conf, Include once
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
  proxmox_template/               qm + virt-customize on the PVE node
  proxmox_vm/                     the Proxmox API role (tasks/{main,present,absent}.yml, vars/main.yml)
  common/                         OS baseline, user, sudo, keys, sshd hardening, PATH for ssh commands
  vscode_server/                  sshd forwarding drop-in checked with sshd -T, inotify limit
  dev_tools/                      Node.js, Docker, gh, uv, Rust  (files/*.asc are vendored signing keys)
  claude_code/                    native or npm install, settings.json, Remote Control and the path unit that
                                  starts it when a login lands; tasks/login.yml, browser_login.yml (the sign-in),
                                  after_login.yml, path_unit_absent.yml; release.yml, and release_pause.yml and
                                  release_resume.yml around destroy's save of the VM's sessions; status.yml (just status)
  github_projects/                tasks/auth.yml (gh sign-in, credential helper), clone.yml, login.yml (login_read.yml,
                                  login_report.yml), device_flow.yml (the sign-in), status.yml (just status);
                                  library/github_repos.py, the custom module
  tailscale/                      tasks/ownership.yml, install.yml, state.yml, connect.yml (the key), settings.yml,
                                  report.yml, absent.yml, login.yml and web_login.yml (the sign-in), status.yml (just status);
                                  files/tailscale.asc (the vendored signing key)

filter_plugins/proxmox.py         reading guest agent output: net_mac, guest_ipv4, guest_address, guest_addresses;
                                  a VM's tailnet address: tailnet_ipv4; a refused token told from a slow boot:
                                  proxmox_access_denied; the managed SSH config: ssh_config_blocks, ssh_config_prune
filter_plugins/vscode.py          vscode_setting (JSONC, read-only): VS Code's settings, never written
filter_plugins/desktop.py         page_opener, the sign-ins' browser command
filter_plugins/text.py            plain_text, what a VM printed made safe to quote; one_line, one line of it; banner, the
                                  block a prompt is set in
filter_plugins/status.py          just status's model and text: status_fleet, status_vm_api, status_report, status_text (§7.7)
filter_plugins/settings.py        init_answer, the rule each just init answer follows; with_setting, one written into YAML text;
                                  init_candidates and init_choose_rows, what the token can use among a node's storages or bridges
filter_plugins/run_view.py        a live run as stages, steps and tasks (just deploy's first): its frame, a line per step,
                                  the summary (§7.5)
filter_plugins/live_ansible.py    the engine that draws a playbook run live: Ansible's events in, the region, the run's own
                                  words and the summary out; the stdout callback hands it every event (§7.5)
filter_plugins/live_commands.py   a run in parts: a command drawn as a part, the state between parts, the run's end (§7.8)
filter_plugins/live_recipes.py    every command drawn live, declared: its stages and steps, named by its title (§7.8)
filter_plugins/live_region.py     every live output's region: drawn in place at the bottom of a terminal, all else above (§7.8)
filter_plugins/screen.py          every live output's lines: styled segments, cut, aligned, coloured in one palette (§7.8)
filter_plugins/sessions.py        claude_sessions_plan, which session goes to which VM checkout by origin; git_repository
                                  and github_repository, owner/repo of a remote URL here and of a GitHub one on the VM;
                                  claude_project_folder, Claude Code's folder for a path; claude_session_dirs; just_code_dir,
                                  just code's own directory on a VM; claude_sessions_saved, what of a VM's sessions destroy saves;
                                  claude_sessions_listed, how just code says the sessions it left out
inventory/
  hosts.yml.example               the only address anyone supplies
  controller.yml                  localhost, so it picks up group_vars/all
  group_vars/all/defaults.yml     shared defaults + the role wiring
  group_vars/all/{local,vault}.yml.example

tests/
  check_no_local_files.sh         tracked-file guard (also runs in CI)
  unit/                           pytest: filters, github_repos, the guard, configure tags, Remote Control, the three
                                  sign-ins and deploy's sign-in steps, Tailscale, destroy's release, the managed SSH
                                  config, the Claude terminal's script, the justfile, just status and every live
                                  output - the model, each recipe drawn, tools/live's runs in parts (live_kit.py runs
                                  each at a terminal of its own; vt.py, a small terminal they read back through) -
                                  and the wiki (test_docs.py)
  molecule/                       shared fakes: PVE API, qm, virt-customize, GitHub API, gh, claude, tailscale,
                                  Tailscale API, the Anthropic Messages API (for the real claude in configure)
molecule/
  provision/                      provision.yml + discover.yml against the fake API
  configure/                      configure.yml end to end in a container
                                  (in both, group_vars/all/defaults.yml is a symlink to the real one; keep it a git checkout)
.config/molecule/config.yml       driver, pinned image, shared provisioner settings
.github/                          CI, releases and the sections of their notes (release.yml), the labels
                                  pull requests get from their titles, the docs site, the composite setup
                                  action, Dependabot and the CODEOWNERS that asks for its pull requests' review
```
