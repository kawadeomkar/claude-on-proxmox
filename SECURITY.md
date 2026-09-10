# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's ["Report a vulnerability"][advisory] button on
this repository's Security tab, not in a public issue. You should get an acknowledgement within a week.

[advisory]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability

## Scope

This project provisions a VM and installs software on it, so the interesting surface is what it trusts and
what it stores:

- **The custom module** `roles/github_projects/library/github_repos.py`, which handles a GitHub token.
- **Supply chain**: the vendored apt signing keys in `roles/dev_tools/files/`, the checksum-pinned uv
  download, the Ubuntu cloud image verified against its published `SHA256SUMS`, and the Claude Code
  installer fetched over TLS.
- **Credential handling**: the Proxmox API token, GitHub token and Anthropic API key, which live in an
  ansible-vault file and are marked `no_log`.
- **What the VM ends up trusting**: the SSH key material, the passwordless sudo rule and the docker group
  membership.

Findings in any of those are in scope, as is anything that would cause a secret to be written to a log, a
fact cache or the repository.

## Known trade-offs, not vulnerabilities

Some defaults trade strictness for a working first run. These are documented in the README, and reports
that only restate them are not treated as vulnerabilities:

- `proxmox_validate_certs` defaults to `false`, because a stock Proxmox install presents a self-signed
  certificate. The README explains how to trust the Proxmox CA and turn it on.
- The VM's user gets passwordless sudo and docker group membership by default; both are toggles.
- SSH host keys are accepted on first contact (`StrictHostKeyChecking=accept-new`) so a freshly cloned VM
  can be reached without manual steps. Changed keys are still refused.
- Deleting a VM also removes its host key from `known_hosts`, which re-opens that first-contact window
  for its address. The address goes back into the DHCP pool, so whatever answers there next is trusted
  once - and `make configure` then sends it your SSH keys, GitHub token and Anthropic API key. Keeping
  the key instead would only trade this for a failure every time a lease is recycled, so the removal is
  deliberate. On a network where you do not trust every device, assign VMs a fixed address with
  `proxmox_vm_ipconfig` rather than relying on DHCP.

If you can show one of these is exploitable beyond the documented trade-off, please report it.
