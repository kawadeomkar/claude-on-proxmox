#!/usr/bin/env bash
# Fail if any environment-specific or secret file is tracked by git.
#
# .gitignore stops the accident; this stops `git add -f`. The two must agree,
# so every pattern here has a counterpart in .gitignore. Reads paths on stdin
# when given --stdin, which is how tests/unit/test_tracked_files.py exercises
# it without touching the repository.
#
# The rules match at any depth. Ansible loads group_vars/ and host_vars/ beside
# every inventory source and beside every playbook - deploy.yml at the root,
# the rest in playbooks/ - and -i accepts a hosts file from anywhere. Anchoring
# the rules to inventory/ and the root to spare a Molecule fixture once let
# playbooks/group_vars/all/vault.yml and inventory/prod/hosts.yml through, so
# tracked fixtures are exempted by name in `allowed` instead.
set -euo pipefail

patterns=(
  # Real inventories: a hosts file wherever -i may point, and under inventory/,
  # which Ansible parses recursively, file by file, any hosts file or directory
  # (backups included), any .ini, and the community.proxmox plugin's
  # *proxmox.yml at any depth: inventory/prod/proxmox.yml is loaded like
  # inventory/prod/hosts.yml, and carries the Proxmox URL and usually the token.
  '(^|/)hosts\.(ya?ml|json|ini)$'
  '^inventory/(.+/)?hosts([./]|$)'
  '^inventory/(.+/)?[^/]+\.ini$'
  '^inventory/(.+/)?[^/]*proxmox\.ya?ml$'
  # Anything named local*, vault* or secret* in a group_vars tree, whatever its
  # extension: Ansible loads extensionless files there too, and a plaintext
  # vault.yml.bak leaks as surely as vault.yml.
  '(^|/)group_vars/(.+/)?(local|vault|secret)[^/]*(/|$)'
  # host_vars/ holds nothing but per-host data - the Proxmox host's address, or
  # overrides for one VM - and no host is ever committed to an inventory.
  '(^|/)host_vars/'
  # The names this project gives local and secret values, which vars_files,
  # include_vars and -e @file can load from anywhere. Exact names outside
  # group_vars/, because "local" also begins ordinary files such as locale.yml.
  '(^|/)(local|secrets?|vault[^/]*)\.(ya?ml|json)$'
  # Credentials.
  '(^|/)\.vault_pass'
  '(^|/)\.env'
)

# Tracked files a rule above matches that hold no real values.
allowed=(
  # A committed template.
  '\.example$'
  # Molecule scenario fixtures. They point the scenario at its own container
  # and local fakes, and only Molecule loads them.
  '^molecule/[^/]+/host_vars/[^/]+\.ya?ml$'
  # A vars file that happens to be named proxmox.yml. Ansible skips group_vars/
  # when it walks an inventory directory, so the plugin never reads one from
  # there. Exactly that name: this list is applied before the rules above, so
  # a wider match here would also exempt a vault-proxmox.yml.
  '(^|/)group_vars/(.+/)?proxmox\.ya?ml$'
)

pattern=$(IFS='|'; echo "${patterns[*]}")
exempt=$(IFS='|'; echo "${allowed[*]}")

if [ "${1:-}" = "--stdin" ]; then
  files=$(cat)
else
  files=$(git ls-files)
fi

leaked=$(printf '%s\n' "$files" | grep -Ev "$exempt" | grep -E "$pattern" || true)

if [ -n "$leaked" ]; then
  printf '%s\n' "$leaked"
  echo "::error::These files hold environment-specific or secret data and must not be committed."
  exit 1
fi
