# What the VM trusts

This is a development box, and the defaults reflect that. This page is what they mean for you;
[SECURITY.md](https://github.com/kawadeomkar/claude-on-proxmox/blob/main/SECURITY.md) is the policy -
how to report a vulnerability, what is in scope, and the known trade-offs it does not treat as one -
and the [security model](../architecture/security-model.md) is how each is enforced.

- The `dev` user has passwordless sudo and is in the `docker` group, so anything running as that user,
  including Claude Code, is effectively root on the VM. The API key written to `~/.claude/settings.json`
  is therefore readable by any code the agent runs, and so is the GitHub token `gh` keeps in plain text
  in `~/.config/gh/hosts.yml` (mode 0600) - the vault's, or the one a device-flow login issued to that
  VM. Set `common_user_passwordless_sudo: false` and `dev_tools_install_docker: false` for a tighter box.
- A device-flow login keeps nothing in the vault shared by every VM, but the token it leaves on the VM
  does not expire and carries gh's scopes (`repo`, `read:org`, `gist`) plus `workflow`, broader than a
  fine-grained token limited to chosen repositories with an expiry. It is revoked under GitHub's
  Authorized OAuth Apps, which ends every GitHub CLI sign-in of the account, so `just destroy` leaves
  that to you ([Tearing down](destroy.md)).
- Downloads are verified where the vendor makes that possible: the uv release by a pinned SHA256, the
  Ubuntu cloud image against Ubuntu's `SHA256SUMS`, apt repositories by vendored signing keys. The Claude
  Code installer script is fetched over TLS from `claude.ai` and itself verifies the binary against a
  release manifest; there is no independently signed artifact to pin.
- sshd is restricted to key-based logins (`common_harden_ssh`), even if `vm_password` is set.

## See also

- Settings: [Packages and tools](../reference/settings.md#packages-and-tools) for
  `dev_tools_install_docker`
- Roles: [`common`](../reference/roles/common.md) for `common_user_passwordless_sudo` and
  `common_harden_ssh`, [`dev_tools`](../reference/roles/dev_tools.md) for the downloads it verifies
- How it works: [§10](../architecture/security-model.md); the policy and known trade-offs:
  [SECURITY.md](https://github.com/kawadeomkar/claude-on-proxmox/blob/main/SECURITY.md)
