# 6. Variable architecture

## 6.1 Layers

```
role defaults/main.yml                    lowest — every variable has one
  └─ inventory/group_vars/all/defaults.yml    committed, shared, safe
       └─ inventory/group_vars/all/local.yml  git-ignored, your environment
            └─ inventory/group_vars/all/vault.yml  git-ignored, ansible-vault
                 └─ host_vars / play vars
                      └─ role vars/main.yml     internals; deliberately un-overridable
                           └─ -e extra vars      highest
```

This is Ansible's own precedence with the project's files placed in it. The files a user edits, and
the alphabetical load of `group_vars/all/` that ranks `defaults.yml` < `local.yml` < `vault.yml`, are
on the user-facing [Settings](../reference/settings.md#variable-layering) page.

## 6.2 The wiring file

Roles are self-contained and prefix their variables (`common_user`, `dev_tools_node_major`) —
ansible-lint's `var-naming[no-role-prefix]` enforces this. `inventory/group_vars/all/defaults.yml`
wires project-level names onto role-level ones so a user sets one variable and it flows everywhere:

```yaml
vm_user: dev
common_user:            "{{ vm_user }}"
dev_tools_user:         "{{ vm_user }}"
claude_code_user:       "{{ vm_user }}"
github_projects_user:   "{{ vm_user }}"
proxmox_vm_user:        "{{ vm_user }}"
```

`proxmox_api_host` itself defaults to the `ansible_host` of the first host in the `proxmox` group,
so the address is written once, in `inventory/hosts.yml`.

The same file holds the few values the playbooks share, so that each is written once rather than
per playbook:

| Var | Used by |
|---|---|
| `proxmox_api_module_defaults` | the `module_defaults: {group/community.proxmox.proxmox: "{{ proxmox_api_module_defaults }}"}` of every localhost play that calls `community.proxmox` modules — a templated group entry, which ansible-core 2.21 accepts |
| `proxmox_api_url` | the `/cluster/resources` request in `playbooks/tasks/cluster_vms.yml`; brackets a bare IPv6 host, as proxmoxer does |
| `proxmox_nic` | discovery and `make list` directly, and the role through `proxmox_vm_nic` |
| `claude_vm_requested` | `discover.yml`, `list.yml`, `ssh_config.yml` and `code.yml`, for what `VM_NAME` selects |
| `vm_projects_dir` | where the code lives on a VM, read by `github_projects` (`github_projects_dir`), `claude_code` (`claude_code_remote_control_dir`) and the alias play; empty means `~/projects` of `vm_user`, resolved on the VM |
| `claude_ssh_config`, `claude_ssh_config_file`, `claude_ssh_user_config`, `claude_ssh_config_include`, `claude_ssh_host_domain`, `claude_ssh_identity_file`, `claude_ssh_forward_agent`, `claude_ssh_options` | `tasks/ssh_config.yml`, from every caller ([§4.8](lifecycle.md#48-playbooksssh_configyml-and-playbookstasksssh_configyml)) |

The role keeps its own `module_defaults`, built from the `proxmox_vm_api_*` wiring, since a role
cannot depend on a project's group vars.

`vm_ssh_public_keys` reads `~/.ssh/id_ed25519.pub` with `errors='ignore'`, so someone whose key is
RSA or ECDSA gets the role's actionable "set vm_ssh_public_keys" message rather than a bare
"could not locate file" from the lookup, which Jinja evaluates first.

## 6.3 Computed internals

`roles/proxmox_vm/vars/main.yml` holds twenty-two expressions, most of which read registers set by tasks. They are
**vars, not facts**, and that is load-bearing:

| Var | Derived from | Purpose |
|---|---|---|
| `proxmox_vm_found_config` | `proxmox_vm_lookup` for an existing VM, `proxmox_vm_config` for one just cloned | the VM's config, read once |
| `proxmox_vm_found_mac` | that config | the VM's own NIC MAC, `""` if unreadable |
| `proxmox_vm_found_node` | `proxmox_vm_lookup`, else `proxmox_vm_node` | where every call about an existing VM goes |
| `proxmox_vm_reported_address` | `proxmox_vm_net` + the MAC | the agent's IPv4 for that NIC |
| `proxmox_vm_found_tags` | `proxmox_vm_lookup` | tags as a list, lowercased, `;`/`,` both handled |
| `proxmox_vm_sent_tags` | `proxmox_vm_tags` | the same list lowercased, as Proxmox stores it |
| `proxmox_vm_unfinished_tag` | `proxmox_vm_sent_tags[0]` | `claude-on-proxmox-unfinished`, carried from the settings call until the start succeeds |
| `proxmox_vm_first_run` | existence + the lookup's `ciuser` and tags | the VM has not finished its first run; see [§8](convergence.md) |
| `proxmox_vm_needs_configuration` | `proxmox_vm_first_run` + `force_update` | the settings, resize and start are due |
| `proxmox_vm_gpu_config` | `proxmox_vm_lookup` alone, `{}` for a VM about to be cloned | the config every GPU decision reads, never the previous run's `proxmox_vm_config` |
| `proxmox_vm_gpu_mappings` | that config, through `hostpci_mappings` | `{slot: mapping id}`, `""` for a device of no mapping |
| `proxmox_vm_gpu_attached` | those + `proxmox_vm_gpu_mapping` | the VM has the mapping already |
| `proxmox_vm_gpu_changeable` | first run + the lookup's status | a GPU may be added or taken off: first run, stopped |
| `proxmox_vm_gpu_set`, `proxmox_vm_gpu_unset` | changeable + `proxmox_vm_gpu` (+ a mapping on `hostpci0`) | the settings call sends `hostpci0`, or a call of its own deletes it |
| `proxmox_vm_gpu_mismatch` | not changeable + `proxmox_vm_gpu` + not attached | `vm_gpu` asks for a GPU this run may not give: said |
| `proxmox_vm_has_gpu` | set, or a device kept | balloon equal to the memory, and the start through the API |
| `proxmox_vm_gpu_starts` | needs configuration or `start_existing`, a GPU, not running | the API start's condition |
| `proxmox_vm_api_url`, `proxmox_vm_api_headers` | the `proxmox_vm_api_*` role vars | that start's request; the header carries the token |
| `proxmox_vm_forget_address` | the agent's reported address | which host key to drop; empty for a stopped VM, which destroy cannot address |
| `proxmox_vm_forget_names` | that address + `proxmox_vm_known_hosts_domain` | every name the host key may be held under |

> **Do not convert these to `set_fact`.** The role can run more than once for the same host in one
> play — the role scenario's `side_effect.yml` includes it back to back — and a `set_fact` survives a
> run that skips it, so a fact would hand VM #1's MAC to VM #2. A skipped *register* is overwritten
> with a skip result; a skipped `set_fact` is not. Lazy vars re-evaluate against whatever the
> registers hold right now. (`destroy.yml` used to be the reason, looping the role on localhost; it
> now runs it once per host, like `provision.yml`.)

`claude_vm_tagged` and `claude_vm_requested` in `inventory/group_vars/all/defaults.yml` follow the
same pattern: they read the `claude_vm_all` register that `playbooks/tasks/cluster_vms.yml` sets, so
any play using them must include that first, and is meant to fail loudly if it has not. They are
defined once so `discover.yml`, `list.yml` and `destroy.yml` cannot drift on what "ours" means or on
what `VM_NAME` selects.

The four facts that *are* facts — `proxmox_vm_exists` and `proxmox_vm_resolved_id`, set in
`main.yml`, and `proxmox_vm_stopped` and `proxmox_vm_address`, set in `present.yml` — are each set
unconditionally on every run, so none can carry an earlier VM's answer the way a skipped `set_fact`
would (`present.yml` re-sets the ID after a clone, the one time it changes). They must be facts
because `provision.yml`'s third play reads `proxmox_vm_address`, `proxmox_vm_stopped` and
`proxmox_vm_resolved_id` through `hostvars`, and role vars are not visible outside the role.
