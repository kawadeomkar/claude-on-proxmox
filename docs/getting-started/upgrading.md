# Upgrading an existing install

Provisioning now needs `qemu-guest-agent` **inside** the image: it is what
reports the VM's DHCP address back. `make template` bakes it in, but a template
built before this change does not have it, and re-running `make template` will
**not** fix that - the role skips the whole build when the VMID already exists.
A VM cloned from such a template never reports an address, so `make provision`
polls its guest agent 60 times, five seconds apart (`proxmox_vm_agent_retries`,
`proxmox_vm_agent_delay`), and then fails. That is five minutes of waiting plus
the time each poll takes, and on a running VM with no agent Proxmox spends a few
seconds pinging the agent before it answers each one, so expect closer to nine
minutes before the failure.

Rebuild the template once, on the Proxmox host:

```bash
qm destroy 9000        # the template's VMID (proxmox_template_vmid)
```

```bash
make template          # rebuilds it, this time with the guest agent
```

The build keeps the pristine download under `/var/lib/vz/claude-on-proxmox` on the node
(`proxmox_template_image_dir`) and removes the customised copy it imports once the template is
finished. Earlier versions kept both under `local`'s `template/iso`, where the UI lists them as
ISO images; they are safe to delete from there once the template is built.

Existing VMs keep working; they are configured over SSH and are not re-cloned.
Your API token also needs the guest-agent privilege added - see
[Creating the Proxmox API token](proxmox-access.md#creating-the-proxmox-api-token).

## A vault GitHub token from an earlier version

Earlier versions asked for a token with `repo` alone, used only to list your repositories. This
version also signs `gh` in with it, and gh refuses a classic token without `read:org`. Such a token
keeps listing as before: the run says gh stayed signed out and which scopes to add (`read:org`, and
`workflow` for pushes that change `.github/workflows/`), and the next `make configure` signs gh in once
they are there. `github_auth_method: web` signs gh in by the device flow instead, the token listing only.

## A gh login or git identity you set up by hand

Earlier versions asked you to sign `gh` in on the VM yourself for pushes, beside a vault token used only
to list. This version signs gh in with `vault_github_token` only where gh is signed out or signed in by
this project, so such a login stays, and each run says the token only lists there; set
`github_auth_method: token` to have the token take over. A `user.name` or `user.email` you set on the VM
stays too: only a missing one is derived from your GitHub account, while `git_user_name` and
`git_user_email`, when set, are always written.

## Remote Control on a VM configured by an earlier version

A VM waiting for its first Claude login now gets a path unit that starts Remote Control the moment a
login lands, so a sign-in made in the Claude terminal of `make code` starts it by itself. A VM last
configured by an earlier version has no such unit: run `make configure VM_NAME=alpha TAGS=claude_code`
once (or `make deploy VM_NAME=alpha`), or sign it in with `make claude-login VM_NAME=alpha`, which
starts Remote Control itself. The Claude terminal of `make code` tells the two apart, and on such a VM
names that `make configure` line.

## Tailscale on VMs configured by an earlier version

Tailscale is on by default, so the next `make configure` installs it on every VM and reports each one
not signed in - or joins it, with `vault_tailscale_auth_key` set. Set `tailscale_enabled: false` in
`local.yml` first to keep your VMs as they are, and `make check` beforehand says what would be installed.
A VM where you installed Tailscale yourself is left exactly as it is either way.

## Adopting VMs created before tagging

This project now recognises its VMs by one Proxmox tag, `claude-on-proxmox`, and
nothing else. VMs created by an earlier version carry `claude;ansible` instead,
so `make list`, `make configure` and `make destroy` do not see them, and
`make provision` refuses to touch a VM of that name rather than take it over.

To adopt one, add the tag - in the Proxmox UI (the pencil beside the tags in the
VM's title bar), or on the Proxmox host:

```bash
qm config 105 | grep ^tags          # the VM's current tags, if any
qm set 105 --tags "claude;ansible;claude-on-proxmox"
```

`--tags` **replaces** the whole list rather than adding to it, so include every
tag the VM should keep. Once tagged it is this project's like any other.

If you only want to delete such a VM, you can skip the tagging and say so
instead, after checking in the Proxmox UI that the name is the VM you mean:

```bash
make destroy VM_NAME=old-dev ANSIBLE_ARGS="-e proxmox_vm_allow_untagged_delete=true"
```
