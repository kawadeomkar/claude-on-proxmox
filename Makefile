# Developer entry points. Every target uses the project-local virtualenv so
# the exact tool versions pinned in requirements.txt are used.
VENV       ?= .venv
BIN        := $(VENV)/bin
ABSBIN     := $(abspath $(BIN))
VAULT_FILE := inventory/group_vars/all/vault.yml
PYTHON     ?= python3
VAULT_PASS := .vault_pass
VAULT_ARGS := $(if $(wildcard $(VAULT_PASS)),--vault-password-file $(VAULT_PASS),)
ANSIBLE_ARGS ?=
# Name(s) of the VM(s) to act on, comma-separated. Given, it narrows every
# target to those names, so `make configure VM_NAME=alpha` touches only alpha.
# Left out, what it means depends on the target:
#   deploy, provision, destroy            -> claude-on-proxmox-default
#                                            (claude_vm_default_name, in
#                                            inventory/group_vars/all/defaults.yml)
#   configure, check, list, claude-login  -> every VM tagged claude-on-proxmox
# `make deploy` configures only what it provisioned, never the rest of the fleet.
# There is deliberately no LIMIT: --limit is applied before the plays run, and
# these VMs only enter the inventory once discovery has found them, so it could
# never match.
VM_NAME    ?=
# Passed as JSON, not key=value. Ansible's key=value parser splits extra-vars
# on whitespace, so VM_NAME="alpha beta" silently became just alpha and only
# half the fleet was created. As JSON the value arrives whole and reaches the
# name check in the proxmox_vm role, which explains the problem. vm-name-check
# has already rejected anything that could break out of this quoting.
VM_ARGS    := $(if $(VM_NAME),-e '{"vm_name": "$(VM_NAME)"}',)
# Re-run part of configure: make configure VM_NAME=alpha TAGS=claude_code
# Role tags are common, vscode_server, dev_tools, claude_code and
# github_projects. Discovery is
# tagged "always", so it runs whichever of these you pick. Only configure and
# check take TAGS. deploy refuses it: nothing that builds the template or
# creates a VM is tagged, so --tags would skip all of that and exit 0 having
# done nothing, and ignoring it instead would turn a one-role push into a full
# deploy, template build over root SSH included.
TAGS       ?=
TAG_ARGS   := $(if $(TAGS),--tags $(TAGS),)
ROLES      := common vscode_server dev_tools claude_code github_projects proxmox_template proxmox_vm
MOLECULE_ROLES ?= $(ROLES)
# Hooks make lint already runs. make test skips them in pre-commit so the
# production-profile ansible-lint and ruff do not run a second time (ruff-format
# even rewriting files). This is exactly what CI passes as SKIP; gitleaks is in
# it because CI scans the tree and the pushed commits separately.
PRE_COMMIT_SKIP := yamllint,ansible-lint,ruff-check,ruff-format,gitleaks

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- setup ----
$(BIN)/activate:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip

.PHONY: venv
venv: $(BIN)/activate ## Create the virtualenv and install Python tooling
	$(BIN)/pip install -r requirements.txt

.PHONY: deps
deps: venv ## Install Ansible Galaxy collections
	$(BIN)/ansible-galaxy collection install -r requirements.yml

.PHONY: init
init: deps ## One-time local setup: venv, collections, local config from examples, pre-commit hooks
	@test -f inventory/hosts.yml || cp inventory/hosts.yml.example inventory/hosts.yml
	@test -f inventory/group_vars/all/local.yml || cp inventory/group_vars/all/local.yml.example inventory/group_vars/all/local.yml
	@test -f inventory/group_vars/all/vault.yml || cp inventory/group_vars/all/vault.yml.example inventory/group_vars/all/vault.yml
	@$(BIN)/pre-commit install >/dev/null
	@echo ""
	@echo "Now edit these (they are git-ignored):"
	@echo "  inventory/hosts.yml                  - the address of your Proxmox host"
	@echo "  inventory/group_vars/all/local.yml   - Proxmox node/storage, package choices, which repos to skip"
	@echo "  inventory/group_vars/all/vault.yml   - secrets: API token, GitHub username/token; then: make vault-encrypt"

.PHONY: vault-encrypt
vault-encrypt: ## Encrypt inventory/group_vars/all/vault.yml with ansible-vault
	$(BIN)/ansible-vault encrypt $(VAULT_ARGS) $(VAULT_FILE)

.PHONY: vault-edit
vault-edit: ## Edit the encrypted vault file
	$(BIN)/ansible-vault edit $(VAULT_ARGS) $(VAULT_FILE)

# Refuse to run playbooks without vault.yml, or while it is still plaintext.
# A missing file used to fall through to the playbook, which died inside the
# cluster listing with "'vault_proxmox_api_token_secret' is undefined".
.PHONY: vault-check
vault-check:
	@test -f $(VAULT_FILE) || { echo "error: $(VAULT_FILE) missing. Run: make init"; exit 1; }
	@head -c 14 $(VAULT_FILE) | grep -q '^\$$ANSIBLE_VAULT' \
	  || { echo "error: $(VAULT_FILE) is not encrypted. Run: make vault-encrypt"; exit 1; }

# VM names are DNS-like, with commas separating a list. A quote, space or shell
# metacharacter in VM_NAME would break out of the single-quoted JSON in VM_ARGS
# or reshape it, so refuse those before any VM target runs. Read through the
# environment, never interpolated into the recipe, so the check is safe for the
# very values it rejects. $(value) hands the check what was typed rather than
# make's expansion of it, so VM_NAME='$(x)' is refused instead of silently
# becoming empty. A shell `case` rather than grep: grep tests line by line, so
# VM_NAME='alpha<newline>beta' passed and YAML folded it to `alpha beta`.
# LC_ALL=C so A-Z is ASCII; in a UTF-8 locale the range can collate accented
# letters. Empty (no VM_NAME) is fine.
.PHONY: vm-name-check
vm-name-check: export VM_NAME_CHECK := $(value VM_NAME)
vm-name-check: export LC_ALL := C
vm-name-check:
	@case "$$VM_NAME_CHECK" in \
	  *[!A-Za-z0-9._,-]*) echo "error: VM_NAME may contain only letters, digits and . _ - , (got: [$$VM_NAME_CHECK])"; exit 1;; \
	esac

# ------------------------------------------------------------ run books ----
.PHONY: template
template: vault-check ## Build the cloud-init VM template on the Proxmox host (once)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(ANSIBLE_ARGS) playbooks/template.yml

.PHONY: provision
provision: vault-check vm-name-check ## Create and start VM(s): make provision VM_NAME=alpha,beta (default: claude-on-proxmox-default)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/provision.yml

.PHONY: list
list: vault-check vm-name-check ## Show the VMs this project created, and their addresses (all, unless VM_NAME)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/list.yml

.PHONY: configure
configure: vault-check vm-name-check ## Configure the VM(s): packages, tools, Claude Code, GitHub projects (every tagged VM, unless VM_NAME)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(TAG_ARGS) $(ANSIBLE_ARGS) playbooks/configure.yml

.PHONY: deploy
deploy: vault-check vm-name-check ## Everything: template (if missing) + provision + configure, for VM_NAME only (default: claude-on-proxmox-default)
	@test -z "$(TAGS)" || { echo "error: deploy takes no TAGS. To re-run one role: make configure VM_NAME=<name> TAGS=$(TAGS)"; exit 1; }
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) deploy.yml

.PHONY: destroy
destroy: vault-check vm-name-check ## Stop and delete the VM(s) on Proxmox (default: claude-on-proxmox-default)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/destroy.yml

# Remote Control needs a claude.ai login on the VM, and Anthropic supports no
# non-interactive way to create one, so this prints the command rather than
# pretending to do it.
.PHONY: claude-login
claude-login: vault-check vm-name-check ## Show how to sign Claude Code in on the VM(s), for Remote Control (every tagged VM, unless VM_NAME)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/claude_login.yml

.PHONY: check
check: vault-check vm-name-check ## Dry-run configure against real hosts (--check --diff; every tagged VM, unless VM_NAME)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(TAG_ARGS) $(ANSIBLE_ARGS) --check --diff playbooks/configure.yml

# The SSH aliases this project keeps in ~/.ssh/claude-on-proxmox.conf, so
# `ssh <name>` and VS Code's host picker follow each VM's address. deploy and
# configure refresh the VMs they touch; this one refreshes the fleet and,
# without VM_NAME, prunes the aliases of VMs that no longer exist.
.PHONY: ssh-config
ssh-config: vault-check vm-name-check ## Refresh the SSH alias of every VM this project created (all, unless VM_NAME; fleet-wide also prunes)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/ssh_config.yml

# One VM at a time: VS Code opens one window per workspace, and "open it in
# the editor" does not mean a fleet. Read through the environment like
# vm-name-check, so the value is never interpolated into the recipe.
.PHONY: code
code: export VM_NAME_CHECK := $(value VM_NAME)
code: vault-check vm-name-check ## Refresh one VM's SSH alias and open its workspace in VS Code: make code VM_NAME=alpha
	@test -n "$$VM_NAME_CHECK" || { echo "error: code opens one VM: make code VM_NAME=<name>"; exit 1; }
	@case "$$VM_NAME_CHECK" in *,*) echo "error: code opens one VM at a time, not [$$VM_NAME_CHECK]"; exit 1;; esac
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/code.yml

# ------------------------------------------------------------ quality -----
.PHONY: lint
lint: ## Run yamllint + ansible-lint + ruff
	$(BIN)/yamllint --strict .
	$(BIN)/ansible-lint
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

.PHONY: syntax
syntax: ## ansible-playbook --syntax-check on every playbook
	@for pb in deploy.yml playbooks/*.yml; do \
	  echo "== $$pb"; \
	  $(BIN)/ansible-playbook --syntax-check $$pb || exit 1; \
	done

.PHONY: unit
unit: ## pytest over tests/unit/: filters, github_repos module, tracked-file guard, configure tags, Remote Control, Makefile guards
	$(BIN)/pytest

# Molecule resolves ansible-playbook from PATH (and only appends the venv), so
# the venv must come first or a system-wide Ansible would be used instead of
# the pinned one.
.PHONY: molecule
molecule: ## Run Molecule (Docker) tests for every role: make molecule MOLECULE_ROLES="common claude_code"
	@for role in $(MOLECULE_ROLES); do \
	  echo "===== molecule: $$role"; \
	  (cd roles/$$role && PATH="$(ABSBIN):$$PATH" $(ABSBIN)/molecule test) || exit 1; \
	done

# One entry point for the playbook-level scenarios so CI runs exactly what a
# developer runs. CI used to inline the molecule call, which meant these
# targets could break without CI noticing.
.PHONY: molecule-scenario
molecule-scenario: ## Run one playbook scenario: make molecule-scenario SCENARIO=provision
	@test -n "$(SCENARIO)" || { echo "error: set SCENARIO, e.g. make molecule-scenario SCENARIO=provision"; exit 1; }
	PATH="$(ABSBIN):$$PATH" $(BIN)/molecule test -s $(SCENARIO)

.PHONY: molecule-integration
molecule-integration: ## Run the full configure playbook against a Docker container
	$(MAKE) molecule-scenario SCENARIO=configure

.PHONY: molecule-provision
molecule-provision: ## Run provision.yml + discover.yml against a fake Proxmox API
	$(MAKE) molecule-scenario SCENARIO=provision

# Includes pre-commit so a local pass covers the hooks CI runs too; it was
# possible to pass everything locally and still be failed by CI. A
# target-specific export reaches every prerequisite of test, not just
# pre-commit; only pre-commit reads SKIP, and it skips the hooks lint already
# ran so they do not run twice. Not a full superset of CI: CI also runs a
# gitleaks scan over the working tree and the commits a push brings in, which a
# local `make test` cannot reproduce from the index alone.
#
# .NOTPARALLEL: the Molecule scenarios each run a Docker container and build
# images into one small disk, so `make -j test` must not run them at once.
# They are serialised on purpose, as they are in the molecule target's loop.
.NOTPARALLEL:
.PHONY: test
test: export SKIP := $(PRE_COMMIT_SKIP)
test: lint syntax unit pre-commit molecule molecule-integration molecule-provision ## Run everything (the local quality gate)

.PHONY: vagrant-up
vagrant-up: ## End-to-end test of configure.yml on a real VirtualBox VM
	vagrant up --provision

.PHONY: vagrant-destroy
vagrant-destroy: ## Destroy the Vagrant test VM
	vagrant destroy -f

.PHONY: pre-commit
pre-commit: ## Run all pre-commit hooks against the whole tree
	$(BIN)/pre-commit run --all-files

.PHONY: clean
clean: ## Remove caches and the virtualenv
	rm -rf $(VENV) .cache .pytest_cache .ruff_cache .ansible .molecule collections
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
