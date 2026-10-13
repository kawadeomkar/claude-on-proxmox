# Repository layout

```
README.md                   getting started; everything else is in docs/
docs/                       this wiki (docs/development/docs.md); zensical.toml builds it as a site
tools/docs.py               make docs: the wiki's generated reference sections (templates/role.md.j2 for aar-doc)
ansible.cfg                 project-wide Ansible settings (accept-new host keys, forks, yaml output)
.logs/                      one log per make run that used Ansible (git-ignored, pruned after 14 days)
deploy.yml                  template (if missing) + provision + configure + the three sign-ins + VS Code
playbooks/                  template.yml, provision.yml, discover.yml, configure.yml, list.yml, claude_login.yml,
                            github_login.yml and tailscale_login.yml (the three sign-ins), destroy.yml,
                            ssh_config.yml (the SSH aliases, fleet-wide), code.yml (one VM in VS Code)
playbooks/tasks/            cluster_vms.yml (the shared cluster listing), vm_addresses.yml (each VM's config and
                            agent reply) and ssh_config.yml (one alias per VM in ~/.ssh/claude-on-proxmox.conf),
                            imported by the playbooks above
filter_plugins/             net_mac, guest_ipv4, guest_address and guest_addresses, for reading guest agent
                            output; tailnet_ipv4, for a VM's tailnet address; proxmox_access_denied, for telling a refused token from a slow boot;
                            ssh_config_blocks and ssh_config_prune, for the managed SSH config (proxmox.py);
                            vscode_setting, for reading VS Code's settings without writing them (vscode.py);
                            page_opener, for opening a sign-in page in your browser (desktop.py);
                            plain_text, for quoting what a VM printed without its escape sequences (text.py)
inventory/                  controller.yml, hosts.yml.example, group_vars/all/{defaults.yml,local.yml.example,vault.yml.example}
roles/                      one role per concern, each with defaults, meta/argument_specs, molecule/
roles/github_projects/library/github_repos.py   custom module
tests/unit/                 pytest: filters, github_repos, tracked-file guard, configure tags, Remote Control, the
                            three sign-ins and deploy's sign-in steps, Tailscale, destroy's release, the managed SSH
                            config, the Claude terminal, Makefile guards, the wiki
tests/molecule/             shared fakes for Molecule scenarios
molecule/configure/         integration scenario for the full configure playbook
molecule/provision/         end-to-end scenario for provisioning + discovery
                            (in both, group_vars/all/defaults.yml is a symlink to the real one; keep it a git checkout)
.config/molecule/           Molecule settings shared by every scenario
.github/                    CI and release workflows, the shared setup action, Dependabot
pyproject.toml, uv.lock     Python tooling, pinned and locked; requirements.yml: Galaxy collections
```
