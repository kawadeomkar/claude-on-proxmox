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
# Name(s) of the VM(s) to act on, comma-separated. Defaults to
# claude-on-proxmox-default (set in inventory/group_vars/all/defaults.yml).
# This selects for every target, so `make configure VM_NAME=alpha` narrows the
# run to alpha. There is deliberately no LIMIT: --limit is applied before the
# plays run, and these VMs only enter the inventory once discovery has found
# them, so it could never match.
VM_NAME    ?=
# Passed as JSON, not key=value. Ansible's key=value parser splits extra-vars
# on whitespace, so VM_NAME="alpha beta" silently became just alpha and only
# half the fleet was created. As JSON the value arrives whole and reaches the
# name check in provision.yml, which explains the problem.
VM_ARGS    := $(if $(VM_NAME),-e '{"vm_name": "$(VM_NAME)"}',)
ROLES      := common dev_tools claude_code github_projects proxmox_template proxmox_vm
MOLECULE_ROLES ?= $(ROLES)

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
	@echo "  inventory/group_vars/all/local.yml   - GitHub username, Proxmox node/storage, package choices"
	@echo "  inventory/group_vars/all/vault.yml   - secrets; then: make vault-encrypt"

.PHONY: vault-encrypt
vault-encrypt: ## Encrypt inventory/group_vars/all/vault.yml with ansible-vault
	$(BIN)/ansible-vault encrypt $(VAULT_ARGS) $(VAULT_FILE)

.PHONY: vault-edit
vault-edit: ## Edit the encrypted vault file
	$(BIN)/ansible-vault edit $(VAULT_ARGS) $(VAULT_FILE)

# Refuse to run playbooks while vault.yml is still plaintext.
.PHONY: vault-check
vault-check:
	@test ! -f $(VAULT_FILE) || head -c 14 $(VAULT_FILE) | grep -q '^\$$ANSIBLE_VAULT' \
	  || { echo "error: $(VAULT_FILE) is not encrypted. Run: make vault-encrypt"; exit 1; }

# ------------------------------------------------------------ run books ----
.PHONY: template
template: vault-check ## Build the cloud-init VM template on the Proxmox host (once)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(ANSIBLE_ARGS) playbooks/template.yml

.PHONY: provision
provision: vault-check ## Create and start VM(s): make provision VM_NAME=alpha,beta
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/provision.yml

.PHONY: configure
configure: vault-check ## Configure the VM(s): packages, tools, Claude Code, GitHub projects
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/configure.yml

.PHONY: site
site: vault-check ## Provision + configure (full run)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) site.yml

.PHONY: destroy
destroy: vault-check ## Stop and delete the VM(s) on Proxmox
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/destroy.yml
	@rm -rf .cache/facts

# Remote Control needs a claude.ai login on the VM, and Anthropic supports no
# non-interactive way to create one, so this prints the command rather than
# pretending to do it.
.PHONY: claude-login
claude-login: vault-check ## Show how to sign Claude Code in on the VM(s), for Remote Control
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/claude_login.yml

.PHONY: check
check: vault-check ## Dry-run configure against real hosts (--check --diff)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) --check --diff playbooks/configure.yml

# ------------------------------------------------------------ quality -----
.PHONY: lint
lint: ## Run yamllint + ansible-lint + ruff
	$(BIN)/yamllint --strict .
	$(BIN)/ansible-lint
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

.PHONY: syntax
syntax: ## ansible-playbook --syntax-check on every playbook
	@for pb in site.yml playbooks/*.yml; do \
	  echo "== $$pb"; \
	  $(BIN)/ansible-playbook --syntax-check $$pb || exit 1; \
	done

.PHONY: unit
unit: ## Run Python unit tests for custom modules
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

# Includes pre-commit so that `make test` really is a superset of the CI gate;
# it was possible to pass everything locally and still be failed by CI.
.PHONY: test
test: lint syntax unit pre-commit molecule molecule-integration molecule-provision ## Run everything

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
