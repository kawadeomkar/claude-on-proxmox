# 15. Open design questions

## Replace `discover.yml` with the `community.proxmox` inventory plugin

The plugin does the same
job in roughly 200 fewer lines and can read a vault-encrypted token through a lookup. It is not used
because an inventory plugin has no access to `group_vars`, so `proxmox_api_host` would have to be
declared a second time outside the one file a user edits; it runs on *every* `ansible*` invocation;
and when the API is unreachable it degrades to "no hosts matched" rather than failing loudly. The two
reasons that once forced the issue — a broken `LIMIT` interface and an over-matching tag filter — have
both been fixed independently, so this is now a pure design choice. The header of `discover.yml`
records the same trade-offs.

## `common_enabled_services` and the `kvm` check

`Enable services` starts a service only when
`ansible_facts['virtualization_type'] == 'kvm'`, which is right for `qemu-guest-agent` (the only entry today)
but means anything a user adds to the list is enabled-but-not-started on Vagrant or bare metal. Two
tasks earlier, the hostname guard uses the more general `not in common_container_virt_types`.
