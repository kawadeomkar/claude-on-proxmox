#!/usr/bin/env bash
# Fail if any environment-specific or secret file is tracked by git.
#
# .gitignore stops the accident; this stops `git add -f`. The two must agree,
# so every pattern here has a counterpart in .gitignore. Reads paths on stdin
# when given --stdin, which is how tests/unit/test_tracked_files.py exercises
# it without touching the repository.
set -euo pipefail

patterns=(
  # Real inventories.
  '^inventory/hosts\.ya?ml$'
  '^inventory/proxmox\.ya?ml$'
  '^inventory/(.+/)?[^/]+\.ini$'
  # Local and secret variable files, at any depth under inventory/.
  '^inventory/(.+/)?group_vars/(.+/)?(local|vault|secret)[^/]*\.(ya?ml|json)$'
  '^inventory/(.+/)?host_vars/'
  # Ansible also loads group_vars/ and host_vars/ beside a playbook, and
  # site.yml is at the repo root. Anchored to the root on purpose:
  # molecule/*/host_vars/ holds tracked test fixtures.
  '^group_vars/(.+/)?(local|vault|secret)[^/]*\.(ya?ml|json)$'
  '^host_vars/'
  '^(vault|local)\.(ya?ml|json)$'
  # Credentials.
  '^\.vault_pass'
  '^\.env'
)

pattern=$(IFS='|'; echo "${patterns[*]}")

if [ "${1:-}" = "--stdin" ]; then
  files=$(cat)
else
  files=$(git ls-files)
fi

# An .example file is a committed template with no real values in it.
leaked=$(printf '%s\n' "$files" | grep -Ev '\.example$' | grep -E "$pattern" || true)

if [ -n "$leaked" ]; then
  printf '%s\n' "$leaked"
  echo "::error::These files hold environment-specific or secret data and must not be committed."
  exit 1
fi
