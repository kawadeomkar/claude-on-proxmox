# What the VM trusts

This is a development box, and the defaults reflect that:

- The `dev` user has passwordless sudo and is in the `docker` group, so anything running as that user,
  including Claude Code, is effectively root on the VM. The API key written to `~/.claude/settings.json`
  is therefore readable by any code the agent runs, and so is the GitHub token `gh` keeps in
  `~/.config/gh/hosts.yml` - the vault's, or the one a device-flow login issued to that VM. Set
  `common_user_passwordless_sudo: false` and `dev_tools_install_docker: false` for a tighter box.
- Downloads are verified where the vendor makes that possible: the uv release by a pinned SHA256, the
  Ubuntu cloud image against Ubuntu's `SHA256SUMS`, apt repositories by vendored signing keys. The Claude
  Code installer script is fetched over TLS from `claude.ai` and itself verifies the binary against a
  release manifest; there is no independently signed artifact to pin.
- sshd is restricted to key-based logins (`common_harden_ssh`), even if `vm_password` is set.
