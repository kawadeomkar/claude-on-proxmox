# 14. File map

```
ansible.cfg                       inventory dir, accept-new host keys, forks, the stdout callback, YAML results
callback_plugins/claude_on_proxmox.py   the default output, the waits set apart, NEXT STEPS after the recap (§7.5)
.logs/                            one log per make run that used Ansible (git-ignored, 0700, pruned after 14 days)
deploy.yml                        template (when missing) + provision + configure + the three sign-ins + VS Code
CLAUDE.md                         agent-facing operating rules
README.md                         getting started; everything else is in docs/
docs/                             the wiki (docs/development/docs.md): getting-started/, guides/, reference/
                                  (generated in part), architecture/ (these pages), development/
zensical.toml                     the wiki as a site: navigation, theme, GitHub-style anchors
Makefile                          every entry point: few targets, each narrowed to one part by TYPE (make help)
tools/docs.py                     make docs: the generated reference sections; templates/role.md.j2 for docsible
tools/env-run                     run a tool from the project's virtualenv, found as the Makefile finds it (the git hooks)
pyproject.toml, uv.lock           the pinned and locked Python tooling (§13); requirements.yml: the Galaxy collections

playbooks/
  template.yml                    one-time: build the cloud-init template (SSH to PVE)
  provision.yml                   three plays: register → create in parallel → publish
  discover.yml                    find VMs by tag, resolve addresses via the guest agent
  list.yml                        read-only: what exists, and where it is
  claude_login.yml                sign Claude in by the browser, VM by VM, then start Remote Control; bare, report
  github_login.yml                sign gh in by the device flow, VM by VM, then run github_projects; bare, report
  tailscale_login.yml             sign the VM in to Tailscale by its web login, VM by VM; bare, report
  login.yml                       make login: discovery, then the three sign-ins above in deploy's order and by its rules
  init_settings.yml               make init's questions: the host's address, the token, the GitHub username; the vault encrypted
  init_check.yml                  make init's checks: the API and token, privileges, the template or root SSH, node, storage, bridge, a key
  inventory/init.yml              init_settings.yml's own inventory: the controller alone, no group_vars
  configure.yml                   discover, then common → vscode_server → dev_tools → claude_code → github_projects,
                                  then tailscale in a rescued block, then an SSH alias per configured VM on the
                                  controller, and a last play failing what the rescue recorded
  destroy.yml                     confirm, verify ownership, release (Claude, GitHub, Tailscale), delete in parallel, forget each alias
  ssh_config.yml                  refresh every alias from the fleet; fleet-wide, prune the vanished
  code.yml                        refresh one alias, write the Claude workspace file and the session notes, open it
  templates/vm_session.md.j2      the session notes: a page about one VM, opened beside the Claude terminal
  tasks/cluster_vms.yml           one /cluster/resources listing, shared by the playbooks above
  tasks/vm_addresses.yml          each VM's config and agent reply, two requests to its own node
  tasks/ssh_config.yml            one blockinfile block per VM in ~/.ssh/claude-on-proxmox.conf, Include once
  tasks/remote_tmp.yml            root's remote_tmp, made before a module makes it and warns (template, configure)
  tasks/init_ask.yml              one make init question: a value given, or asked, held to its kind, asked once more
  tasks/init_local_setting.yml    one setting written into local.yml in place of its commented example line
  tasks/init_choose.yml           one of make init's choices: a storage or bridge the node lists and the token can use

roles/                            one role per concern, each with defaults/, meta/argument_specs.yml, molecule/
  proxmox_template/               qm + virt-customize on the PVE node
  proxmox_vm/                     the Proxmox API role (tasks/{main,present,absent}.yml, vars/main.yml)
  common/                         OS baseline, user, sudo, keys, sshd hardening
  vscode_server/                  sshd forwarding drop-in checked with sshd -T, inotify limit
  dev_tools/                      Node.js, Docker, gh, uv  (files/*.asc are vendored signing keys)
  claude_code/                    native or npm install, settings.json, Remote Control and the path unit that
                                  starts it when a login lands; tasks/login.yml, browser_login.yml (the sign-in),
                                  after_login.yml, path_unit_absent.yml
  github_projects/                tasks/auth.yml (gh sign-in, credential helper), clone.yml, login.yml,
                                  device_flow.yml (the sign-in); library/github_repos.py, the custom module
  tailscale/                      tasks/ownership.yml, install.yml, status.yml, connect.yml (the key), settings.yml,
                                  report.yml, absent.yml, login.yml and web_login.yml (the sign-in);
                                  files/tailscale.asc (the vendored signing key)

filter_plugins/proxmox.py         reading guest agent output: net_mac, guest_ipv4, guest_address, guest_addresses;
                                  a VM's tailnet address: tailnet_ipv4; a refused token told from a slow boot:
                                  proxmox_access_denied; the managed SSH config: ssh_config_blocks, ssh_config_prune
filter_plugins/vscode.py          vscode_setting (JSONC, read-only): VS Code's settings, never written
filter_plugins/desktop.py         page_opener, the sign-ins' browser command
filter_plugins/text.py            plain_text, what a VM printed made safe to quote; banner, the block a prompt is set in
filter_plugins/settings.py        init_answer, the rule each make init answer follows; with_setting, one written into YAML text;
                                  init_candidates and init_choose_rows, what the token can use among a node's storages or bridges
inventory/
  hosts.yml.example               the only address anyone supplies
  controller.yml                  localhost, so it picks up group_vars/all
  group_vars/all/defaults.yml     shared defaults + the role wiring
  group_vars/all/{local,vault}.yml.example

tests/
  check_no_local_files.sh         tracked-file guard (also runs in CI)
  unit/                           pytest: filters, github_repos, the guard, configure tags, Remote Control, the three
                                  sign-ins and deploy's sign-in steps, Tailscale, destroy's release, the managed SSH
                                  config, the Claude terminal's script, Makefile guards, the wiki (test_docs.py)
  molecule/                       shared fakes: PVE API, qm, virt-customize, GitHub API, gh, claude, tailscale,
                                  Tailscale API
molecule/
  provision/                      provision.yml + discover.yml against the fake API
  configure/                      configure.yml end to end in a container
                                  (in both, group_vars/all/defaults.yml is a symlink to the real one; keep it a git checkout)
.config/molecule/config.yml       driver, pinned image, shared provisioner settings
.github/                          CI, release, the docs site, the composite setup action, Dependabot
```
