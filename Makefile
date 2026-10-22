# Developer entry points. Every target runs its tools from the Python
# environment you made for this project, into which make install puts exactly
# the versions locked in uv.lock - never a tool that merely happens to be on
# PATH.
#
# Few targets, each of which does all of its job by default; TYPE=<part>
# narrows one to a single part of it (make deploy TYPE=configure, make test
# TYPE=integration). `make help` lists them, with the TYPEs each one takes.

# The Python of that environment: a virtualenv of your own, wherever it lives -
# a pyenv virtualenv that this directory's .python-version names (pyenv local),
# one you activated, or PYTHON=/path/to/env/bin/python. The project keeps no
# environment of its own.
PYTHON     ?= python3
# Where the environment's tools are, and its prefix, asked of PYTHON the first
# time a recipe needs them and never again in the run, so make help and the
# refusals made while the Makefile is read start no Python. Empty when PYTHON
# is no virtualenv's - pyenv's own, the system's, whose scripts directory is
# /usr/local/bin and the like - which env-check, run before every tool, then
# refuses: the tools come from this project's environment or not at all, never
# from a system-wide install. BIN=<dir> on the command line points every target
# at other tools (the unit tests' stand-ins); a BIN in the environment, a name
# many shells use for ~/bin, does not.
BIN         = $(eval BIN := $(shell $(PYTHON) -c 'import sys, sysconfig; print(sysconfig.get_path("scripts") if sys.prefix != sys.base_prefix else "")' 2>/dev/null))$(BIN)
ABSBIN      = $(abspath $(BIN))
ENV_PREFIX  = $(eval ENV_PREFIX := $(shell $(PYTHON) -c 'import sys; print(sys.prefix)' 2>/dev/null))$(ENV_PREFIX)
ENV_PYTHON  = $(eval ENV_PYTHON := $(shell $(PYTHON) -c 'import sys; print(sys.executable)' 2>/dev/null))$(ENV_PYTHON)
VAULT_FILE := inventory/group_vars/all/vault.yml
# uv installs the locked packages into that environment. Install it yourself
# (https://docs.astral.sh/uv/getting-started/installation/, e.g. `brew install
# uv`). It never downloads a Python of its own here.
UV         ?= uv
UV_RUN      = UV_PYTHON_DOWNLOADS=never $(UV)
VAULT_PASS := .vault_pass
# What make init copies from its .example beside it, where it is missing.
INIT_FILES := inventory/hosts.yml inventory/group_vars/all/local.yml $(VAULT_FILE)
VAULT_ARGS := $(if $(wildcard $(VAULT_PASS)),--vault-password-file $(VAULT_PASS),)
ANSIBLE_ARGS ?=
comma      := ,
empty      :=
space      := $(empty) $(empty)

# The target this run is for: the first goal, or help, the default.
GOAL       := $(or $(firstword $(MAKECMDGOALS)),help)

# Targets this Makefile had before they were folded into the ones below, and
# what does their job now. Typed, one stops the run at once, naming the
# replacement, rather than make saying it has no rule for it - or, for a word
# after a target that takes none, running nothing that was meant.
RENAMED_venv                 := make install (into your own environment: the project keeps no .venv)
RENAMED_deps                 := make install
RENAMED_lock                 := make install TYPE=lock
RENAMED_vault-encrypt        := make vault
RENAMED_vault-edit           := make vault
RENAMED_logs-clean           := make clean TYPE=logs
RENAMED_template             := make deploy TYPE=template
RENAMED_provision            := make deploy TYPE=provision
RENAMED_configure            := make deploy TYPE=configure
RENAMED_check                := make deploy TYPE=check
RENAMED_ssh-config           := make deploy TYPE=ssh-config
RENAMED_claude-login         := make login TYPE=claude
RENAMED_github-login         := make login TYPE=github
RENAMED_tailscale-login      := make login TYPE=tailscale
RENAMED_docs-serve           := make docs TYPE=serve
RENAMED_docs-build           := make docs TYPE=build
RENAMED_syntax               := make lint TYPE=syntax
RENAMED_pre-commit           := make lint TYPE=hooks
RENAMED_unit                 := make test TYPE=unit
RENAMED_molecule             := make test TYPE=molecule (TYPE=role, or ROLE=<role>, for the role scenarios alone)
RENAMED_molecule-scenario    := make test TYPE=integration (the configure scenario) or TYPE=provision
RENAMED_molecule-integration := make test TYPE=integration
RENAMED_molecule-provision   := make test TYPE=provision
RENAMED_vagrant-up           := make vagrant
RENAMED_vagrant-destroy      := make vagrant TYPE=destroy

# Name(s) of the VM(s) to act on, comma-separated. Given, it narrows every
# target to those names, so `make deploy TYPE=configure VM_NAME=alpha` touches
# only alpha (as does `make deploy alpha TYPE=configure`: the names may also
# follow the target). Left out, what it means depends on what the run does:
#   deploy, deploy TYPE=provision, destroy   -> claude-on-proxmox-default
#                                               (claude_vm_default_name, in
#                                               inventory/group_vars/all/defaults.yml)
#   deploy TYPE=configure, check and
#   ssh-config, list, login                  -> every VM tagged claude-on-proxmox
# What creates or deletes VMs acts on the default one; what reads them or
# applies the configuration again reaches the fleet. `make deploy` configures
# only what it provisioned, never the rest of the fleet.
# There is deliberately no LIMIT: --limit is applied before the plays run, and
# these VMs only enter the inventory once discovery has found them, so it could
# never match.
VM_NAME    ?=
# Every target that takes VM_NAME also takes the names after it, as listed
# in NAMED_TARGETS: `make deploy alpha beta` is `make deploy VM_NAME=alpha,beta`
# and `make login alpha` is `make login VM_NAME=alpha`. make reads each of
# those words as a goal of its own, so a rule that does nothing answers each,
# and a word that is a target here is refused before anything runs: `make
# destroy test` would otherwise delete the default VM and then run the tests,
# and `make deploy list` deploy the default VM and list them - one target at a
# time, then. So is a word that is one of the target's own TYPEs, which `make
# deploy configure` would otherwise create a VM named after, and so are names
# given both ways. make never expands what MAKECMDGOALS holds, and the words go
# into no rule and no shell before vm-name-check, which refuses them as it
# would the same text in VM_NAME=.
NAMED_TARGETS := deploy code login list destroy
# Of those, the ones that act on exactly one VM, which make help shows so.
ONE_NAME_TARGETS := code

# The parts a target can be narrowed to with TYPE=<part>. Without TYPE, each
# of these does every part (deploy, login, lint, test, clean) or its main
# one (install installs, docs regenerates the pages, vagrant brings the VM up). The `#: TYPE=`
# lines above each target say what each part is, for make help and the wiki;
# tests/unit/test_makefile.py holds the two to the same list.
TYPES_install  := lock
TYPES_deploy   := template provision configure check ssh-config
TYPES_login    := claude github tailscale
TYPES_lint     := lock yaml ansible python syntax hooks
TYPES_test     := unit molecule role integration provision
TYPES_docs     := serve build
TYPES_vagrant  := destroy
TYPES_clean    := logs
# From the command line only: TYPE, TAGS and ROLE are names a shell may well
# export for something else, and an exported TYPE=check would turn every make
# deploy into a dry run, or ROLE=common make test skip lint and the unit tests.
# A makefile assignment outranks the environment, and the command line it.
TYPE       :=

NAMED_GOAL := $(filter $(NAMED_TARGETS),$(GOAL))
GOAL_WORDS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
# The goals a replaced target's name could be among: every one, but after a
# target that takes VM names, where the words after it are names.
RENAMED_GOAL := $(firstword $(foreach goal,$(if $(NAMED_GOAL),$(GOAL),$(MAKECMDGOALS)),$(if $(RENAMED_$(goal)),$(goal))))
ifneq ($(RENAMED_GOAL),)
$(error make $(RENAMED_GOAL) is now: $(RENAMED_$(RENAMED_GOAL)))
endif
ifneq ($(NAMED_GOAL),)
ifneq ($(GOAL_WORDS),)
GOAL_TARGETS := $(sort $(filter $(shell sed -n 's/^\([A-Za-z][A-Za-z0-9_-]*\):.*/\1/p' $(firstword $(MAKEFILE_LIST))),$(GOAL_WORDS)))
ifneq ($(GOAL_TARGETS),)
$(error make target$(if $(word 2,$(GOAL_TARGETS)),s) among the VM names: $(GOAL_TARGETS). Run one target at a time, and name a VM so with make $(NAMED_GOAL) VM_NAME=$(subst $(space),$(comma),$(GOAL_TARGETS)))
endif
GOAL_TYPES := $(sort $(filter $(TYPES_$(NAMED_GOAL)),$(GOAL_WORDS)))
ifneq ($(GOAL_TYPES),)
$(error $(GOAL_TYPES) among the VM names $(if $(word 2,$(GOAL_TYPES)),are TYPEs,is a TYPE) of make $(NAMED_GOAL): give it as make $(NAMED_GOAL) TYPE=$(firstword $(GOAL_TYPES)), and name a VM so with make $(NAMED_GOAL) VM_NAME=$(subst $(space),$(comma),$(GOAL_TYPES)))
endif
# A target an earlier Makefile had, typed from habit: make deploy alpha syntax
# would otherwise create a VM named syntax, which the old Makefile refused as a
# target. Refused the same way, naming what replaced it.
GOAL_RENAMED := $(sort $(foreach word,$(GOAL_WORDS),$(if $(RENAMED_$(word)),$(word))))
ifneq ($(GOAL_RENAMED),)
$(error $(GOAL_RENAMED) among the VM names $(if $(word 2,$(GOAL_RENAMED)),were make targets,was a make target) - make $(firstword $(GOAL_RENAMED)) is now: $(RENAMED_$(firstword $(GOAL_RENAMED))). Run one target at a time, and name a VM so with make $(NAMED_GOAL) VM_NAME=$(subst $(space),$(comma),$(GOAL_RENAMED)))
endif
ifneq ($(filter command environment%,$(origin VM_NAME)),)
$(error give the VM names one way: make $(NAMED_GOAL) <name>... or make $(NAMED_GOAL) VM_NAME=<name>,..., not both)
endif
VM_NAME    := $(subst $(space),$(comma),$(GOAL_WORDS))
%:
	@:
endif
else ifneq ($(TYPES_$(GOAL)),)
# make test integration, make clean logs: a part given as a word, which
# make would take for a target of its own - or run one, as vagrant destroy and
# clean logs would.
GOAL_TYPES := $(sort $(filter $(TYPES_$(GOAL)),$(GOAL_WORDS)))
ifneq ($(GOAL_TYPES),)
$(error make $(GOAL) takes its part as TYPE=$(firstword $(GOAL_TYPES)), not as a word after it: make $(GOAL) TYPE=$(firstword $(GOAL_TYPES)))
endif
endif

# TYPE is checked here, before any rule runs: one word, among the target's
# TYPEs. Read with $(value), like VM_NAME, so TYPE='$(x)' is refused as the
# text it is instead of being expanded, and only a checked part is ever put in
# a recipe or a log file name - as KIND_<target>, so that a second goal on the
# same line (make lint unit) is not narrowed by the first one's TYPE.
ifneq ($(value TYPE),)
ifeq ($(TYPES_$(GOAL)),)
$(error make $(GOAL) takes no TYPE (got: [$(value TYPE)]))
endif
ifneq ($(words $(value TYPE)) $(filter $(TYPES_$(GOAL)),$(value TYPE)),1 $(value TYPE))
$(error make $(GOAL) takes TYPE=$(subst $(space),|,$(TYPES_$(GOAL))), not [$(value TYPE)])
endif
KIND_$(GOAL) := $(value TYPE)
endif

# Re-run part of configuration: make deploy alpha TAGS=claude_code
# Role tags are common, vscode_server, dev_tools, claude_code, github_projects
# and tailscale; several are comma-separated. TAGS narrows configuration, and
# nothing else, so beside VM names it is TYPE=configure, and TYPE=check takes
# it too: nothing that builds the template or creates a VM is tagged, so --tags
# on a whole deploy would skip all of that and exit 0 having done nothing.
# Discovery is tagged "always", so it runs whichever of these you pick. Checked
# against the role names, read like TYPE, so a misspelt tag is said rather
# than silently matching nothing, and only a role name reaches the shell.
TAGS       :=
CONFIGURE_TAGS := common vscode_server dev_tools claude_code github_projects tailscale
TAG_LIST   := $(strip $(subst $(comma),$(space),$(value TAGS)))
ifeq ($(GOAL),deploy)
ifneq ($(TAG_LIST),)
ifneq ($(filter-out $(CONFIGURE_TAGS),$(TAG_LIST)),)
$(error TAGS may name only $(subst $(space),$(comma)$(space),$(CONFIGURE_TAGS)) (got: [$(value TAGS)]))
endif
# Without names, configuration reaches every VM, while a bare make deploy
# means the default one: so TAGS stands for TYPE=configure only beside names,
# and the whole fleet takes the TYPE said out loud. An API key pushed with
# make deploy TAGS=claude_code would otherwise land on every VM, where it
# outranks the claude.ai login and stops Remote Control.
ifeq ($(KIND_deploy),)
ifeq ($(value VM_NAME),)
$(error TAGS on its own configures the VMs named beside it: make deploy <name>... TAGS=$(subst $(space),$(comma),$(TAG_LIST)), or every VM this project created with make deploy TYPE=configure TAGS=$(subst $(space),$(comma),$(TAG_LIST)))
endif
endif
KIND_deploy := $(or $(KIND_deploy),configure)
ifeq ($(filter configure check,$(KIND_deploy)),)
$(error TAGS narrows configuration, which TYPE=$(KIND_deploy) does not run: make deploy TYPE=configure TAGS=$(subst $(space),$(comma),$(TAG_LIST)))
endif
endif
endif
TAG_ARGS   := $(if $(TAG_LIST),--tags $(subst $(space),$(comma),$(TAG_LIST)),)

# TYPE=template builds the one template, whatever VMs are named.
ifeq ($(KIND_deploy),template)
ifneq ($(value VM_NAME),)
$(error make deploy TYPE=template builds the template, which takes no VM names)
endif
endif

# Passed as JSON, not key=value. Ansible's key=value parser splits extra-vars
# on whitespace, so VM_NAME="alpha beta" silently became just alpha and only
# half the fleet was created. As JSON the value arrives whole and reaches the
# name check in the proxmox_vm role, which explains the problem. vm-name-check
# has already rejected anything that could break out of this quoting.
VM_ARGS    := $(if $(VM_NAME),-e '{"vm_name": "$(VM_NAME)"}',)
# A run with no terminal on stdin has nobody to approve a sign-in: no page
# opens and nothing reaches the clipboard, since nobody is there to use them,
# and make deploy skips its sign-ins, which would otherwise start by themselves
# and wait for a browser. A login_desktop or deploy_sign_in given in
# ANSIBLE_ARGS still wins: it comes later on the command line.
NO_TTY_ARGS = $$([ -t 0 ] || printf '%s' '-e {"login_desktop":false,"deploy_sign_in":false}')
# One project to open with `make code`, rather than the whole projects
# directory: the name of a folder in it, which is the repository's name when
# github_projects cloned it. Only `make code` reads it. Passed as JSON for the
# same reason as VM_NAME, after project-name-check has refused anything that
# could break out of the quoting or out of the projects directory.
PROJECT    ?=
PROJECT_ARGS := $(if $(PROJECT),-e '{"vm_project": "$(PROJECT)"}',)
# The answers make init takes on the command line instead of asking for them
# (docs/getting-started/first-deploy.md, "Without a terminal, or scripted"),
# each checked by init-args-check and passed on as one JSON extra var,
# init_given, empty ones left out. From the command line only, like TYPE: a HOST or GITHUB_USER the
# shell exports for something else never answers make init. The token's secret
# is never one of them: make copies every variable given on its command line
# into MAKEFLAGS, which every command a recipe runs inherits, and the line
# itself stays in the shell's history, the process list and a CI log. make init
# reads it from TOKEN_SECRET_FILE's file, or from CLAUDE_ON_PROXMOX_TOKEN_SECRET
# in the environment, inside the playbook.
HOST              :=
TOKEN_USER        :=
TOKEN_NAME        :=
TOKEN_SECRET_FILE :=
GITHUB_USER       :=
STORAGE           :=
BRIDGE            :=
TAILSCALE         :=
PROMPT            :=
ifneq ($(filter command,$(origin TOKEN_SECRET) $(origin CLAUDE_ON_PROXMOX_TOKEN_SECRET)),)
$(error the token secret never goes on make's command line, which every command a recipe runs inherits (MAKEFLAGS) and the shell's history keeps: put it in a file only you can read, make init TOKEN_SECRET_FILE=<file>, or export CLAUDE_ON_PROXMOX_TOKEN_SECRET from wherever your secrets live - a CI secret, or read -rs at a prompt - never typed into the command)
endif
INIT_GIVEN := host=HOST token_user=TOKEN_USER token_name=TOKEN_NAME token_secret_file=TOKEN_SECRET_FILE github_user=GITHUB_USER storage=STORAGE bridge=BRIDGE tailscale=TAILSCALE
# "name":"value" for each one given. Read with $(value), never expanded, after
# init-args-check has refused anything that could leave the single quotes.
init_given_pair = $(if $(value $(2)),"$(1)":"$(value $(2))")
INIT_GIVEN_JSON = $(subst $(space),$(comma),$(strip $(foreach entry,$(INIT_GIVEN),$(call init_given_pair,$(firstword $(subst =, ,$(entry))),$(lastword $(subst =, ,$(entry)))))))
INIT_GIVEN_ARGS = $(if $(INIT_GIVEN_JSON),-e '{"init_given":{$(INIT_GIVEN_JSON)}}')
# make init's checks (playbooks/init_check.yml) run with the project's
# inventory and the vault, so the password is looked for when the recipe runs:
# the questions may have just made .vault_pass.
INIT_VAULT_ARGS = $$([ -f $(VAULT_PASS) ] && printf -- '--vault-password-file %s' $(VAULT_PASS))
# make init asks only at a terminal, and only when PROMPT is not no:
# init_settings.yml's init_ask is false otherwise, so a run with no terminal, or
# the playbook run by hand, asks nothing and writes only what was given.
INIT_TTY_ARGS = $(if $(filter no,$(value PROMPT)),,$$([ -t 0 ] && printf '%s' '-e {"init_ask":true}'))
# The roles with a Molecule scenario of their own. ROLE=<role>,... narrows
# make test to some of them, and so is TYPE=role on its own; checked against
# this list, like TAGS, before a recipe changes into roles/<role>.
ROLES      := common vscode_server dev_tools claude_code github_projects tailscale proxmox_template proxmox_vm
ROLE       :=
ROLE_LIST  := $(strip $(subst $(comma),$(space),$(value ROLE)))
ifeq ($(GOAL),test)
ifneq ($(ROLE_LIST),)
ifneq ($(filter-out $(ROLES),$(ROLE_LIST)),)
$(error ROLE may name only $(subst $(space),$(comma)$(space),$(ROLES)) (got: [$(value ROLE)]))
endif
KIND_test := $(or $(KIND_test),role)
ifneq ($(KIND_test),role)
$(error ROLE narrows the role scenarios, which TYPE=$(KIND_test) does not run: make test ROLE=$(subst $(space),$(comma),$(ROLE_LIST)))
endif
endif
endif
# override: the old MOLECULE_ROLES=... on the command line would otherwise
# outrank this and reach the recipe's cd unchecked.
override MOLECULE_ROLES := $(if $(filter test,$(GOAL)),$(or $(ROLE_LIST),$(ROLES)),$(ROLES))
# Hooks make lint already runs itself, so its hooks part skips them, the
# production-profile ansible-lint and ruff never running a second time (ruff-format
# even rewriting files). gitleaks is in it because its hook scans only what is
# staged, and CI scans the tree and the pushed commits separately. SKIP= on the
# command line runs every hook; a SKIP exported in the shell, for the hooks of
# another project, is not taken.
PRE_COMMIT_SKIP := yamllint,ansible-lint,ruff-check,ruff-format,gitleaks

# Run logs. Every target that runs Ansible also writes everything Ansible
# prints, with timestamps, to a file of its own in LOG_DIR, named after the
# moment, the target and its TYPE, so a run can be read back after the
# terminal has scrolled away:
#   .logs/20261007-203015-deploy.log
#   .logs/20261007-204510-deploy-configure.log
# The terminal shows what it always did - Ansible copies its output through
# ANSIBLE_LOG_PATH, so prompts and colours are untouched - and tasks marked
# no_log are hidden in the file as on screen. The files still name hosts and
# addresses, so LOG_DIR is git-ignored, refused by tests/check_no_local_files.sh
# and private to you (0700). Logs older than LOG_RETENTION_DAYS are deleted
# whenever a logged run starts; `make logs` lists them and `make clean
# TYPE=logs` deletes them all. `LOG_DIR=` (empty) turns logging off for a run,
# and an ANSIBLE_LOG_PATH of your own is left as it is.
#
# The file name is built from the date, the target and the checked TYPE only,
# never from VM_NAME, so nothing a user typed reaches a shell before
# vm-name-check has looked at it. A nested $(MAKE) inherits the exported path,
# so one run of `make test` is one file.
LOG_DIR            ?= $(CURDIR)/.logs
LOG_RETENTION_DAYS ?= 14
LOG_STAMP          := $(shell date +%Y%m%d-%H%M%S)
LOGGED_TARGETS     := init deploy code login list destroy test vagrant
ifneq ($(LOG_DIR),)
$(LOGGED_TARGETS): export ANSIBLE_LOG_PATH ?= $(LOG_DIR)/$(LOG_STAMP)-$(firstword $(MAKECMDGOALS) $@)$(if $(KIND_$(GOAL)),-$(KIND_$(GOAL))).log
endif

.DEFAULT_GOAL := help

# make help: every target with a `## ` description, under its banner, with the
# names it takes and the `#: KEY=value` lines above it folded into one line of
# options. awk rather than a script of the project's, so it works before the
# virtualenv exists.
define HELP_PROGRAM
/^# -+ .+ -+$$/ { title = $$0; sub(/^# -+ /, "", title); sub(/ -+$$/, "", title); shown = 0; n = 0; next }
/^#: / {
  option = substr($$0, 4); sub(/ .*/, "", option)
  key = option; sub(/=.*/, "", key); val = option; sub(/^[^=]*=/, "", val)
  if (key in vals) vals[key] = vals[key] "|" val; else { vals[key] = val; keys[++n] = key }
  next
}
/^[a-zA-Z_-]+:.*## / {
  name = $$0; sub(/:.*/, "", name); text = $$0; sub(/^[^#]*## /, "", text)
  if (name != "help") {
    if (!shown) { printf "\n%s%s\n", toupper(substr(title, 1, 1)), substr(title, 2); shown = 1 }
    usage = name
    if (index(" " named " ", " " name " ")) usage = usage (index(" " one " ", " " name " ") ? " <name>" : " [<name>...]")
    printf "  %-20s %s\n", usage, text
    line = ""
    for (i = 1; i <= n; i++) line = line (i > 1 ? "  " : "") keys[i] "=" vals[keys[i]]
    if (line != "") printf "  %-20s   %s\n", "", line
  }
  split("", vals); n = 0
  next
}
endef

.PHONY: help
help: export HELP_PROGRAM_TEXT = $(HELP_PROGRAM)
help: ## Show this help
	@printf 'Usage: make <target> [<name>...] [TYPE=<part>] [VAR=value...]\n'
	@awk -v named="$(NAMED_TARGETS)" -v one="$(ONE_NAME_TARGETS)" "$$HELP_PROGRAM_TEXT" $(firstword $(MAKEFILE_LIST))
	@printf '\nWithout names, deploy and destroy act on claude-on-proxmox-default; list, login and\n'
	@printf 'deploy TYPE=configure, check or ssh-config act on every VM this project created.\n'
	@printf 'Every target that runs Ansible takes ANSIBLE_ARGS="..." and LOG_DIR=<dir>.\n'

# ------------------------------------------------------------------ use ----
# make install, the git hook, the local config files - copied from their
# examples only where they are missing - then playbooks/init_settings.yml,
# which asks, at a terminal, for each value a first deploy needs that is still
# the example's: the Proxmox host's address, the API token's ID and secret,
# the GitHub username. Enter leaves one for the next make init, and the vault
# is written encrypted. Without a terminal it asks nothing, and writes only the
# values given on the command line (below), so make init can be scripted. Then
# playbooks/init_check.yml checks what make deploy will need of Proxmox - the
# token, its privileges, the template or root SSH, the node, the storage and
# the bridge, an SSH key - suggests a storage or bridge where the default does
# not fit, and fails on a problem, so a scripted make init exits non-zero.
#: HOST=<address>             The Proxmox host's address, for inventory/hosts.yml
#: TOKEN_USER=<user@realm>    The API token's user, when it is not ansible@pve
#: TOKEN_NAME=<name>          The API token's name, when it is not ansible
#: TOKEN_SECRET_FILE=<file>   A file holding the token's secret (or CLAUDE_ON_PROXMOX_TOKEN_SECRET in the environment; never on the command line)
#: GITHUB_USER=<login>        The GitHub account whose repositories are cloned
#: STORAGE=<id>              The storage for VM disks, for local.yml: written when the token can use it there
#: BRIDGE=<name>             The bridge the template is built on, for local.yml (before the template exists)
#: TAILSCALE=on              Each VM joins your tailnet, the default: writes tailscale_enabled: true in local.yml
#: TAILSCALE=off             No Tailscale, for a setup without a Tailscale account: tailscale_enabled: false
#: PROMPT=no                  Ask nothing, even at a terminal
.PHONY: init
init: init-args-check log-setup install ## Set up this checkout, then ask for what a first deploy needs
	@$(BIN)/pre-commit install >/dev/null
	@copied=; \
	for file in $(INIT_FILES); do \
	  test -f "$$file" || { cp "$$file.example" "$$file" && copied="$$copied $$file"; }; \
	done; \
	if [ -n "$$copied" ]; then echo "Created$$copied from their .example files (git-ignored)."; fi
	@$(BIN)/ansible-playbook -i playbooks/inventory/init.yml $(INIT_TTY_ARGS) $(INIT_GIVEN_ARGS) $(ANSIBLE_ARGS) playbooks/init_settings.yml
	@if head -c 14 $(VAULT_FILE) | grep -q '^\$$ANSIBLE_VAULT' && [ ! -f $(VAULT_PASS) ] && [ -z "$$ANSIBLE_VAULT_PASSWORD_FILE" ]; then \
	  echo "Proxmox is not checked: $(VAULT_FILE) is encrypted, and there is no $(VAULT_PASS) or ANSIBLE_VAULT_PASSWORD_FILE to open it with."; \
	else \
	  $(BIN)/ansible-playbook $(INIT_VAULT_ARGS) $(INIT_TTY_ARGS) $(INIT_GIVEN_ARGS) $(ANSIBLE_ARGS) playbooks/init_check.yml; \
	fi

# uv's sync installs the versions the lock pins into PYTHON's environment,
# after checking the lock still matches pyproject.toml. --inexact: the
# environment is the user's, and a package the lock does not list - theirs, or
# another project's in an environment that serves both - stays where it is.
# A Python that is no virtualenv at all (pyenv's own, the system's) is refused
# by env-check before uv runs. --python names that environment's interpreter,
# so uv never swaps it for another. ansible-galaxy installs the collections
# requirements.yml pins. Both change nothing when they are current, so this is
# also the command to run after a pull, and what CI runs.
#: TYPE=lock  Update uv.lock after editing a pin in pyproject.toml, installing nothing (uv from PATH)
.PHONY: install
install: $(if $(filter lock,$(KIND_install)),,env-check) ## Install the locked Python packages and the Galaxy collections into your environment
ifeq ($(KIND_install),lock)
	$(UV_RUN) lock
else
	UV_PROJECT_ENVIRONMENT="$(ENV_PREFIX)" $(UV_RUN) sync --locked --inexact --python "$(ENV_PYTHON)"
	$(BIN)/ansible-galaxy collection install -r requirements.yml
endif

# A plaintext vault can only be encrypted and an encrypted one only edited, so
# the file says which: the first `make vault` after editing the example
# encrypts it, and every one after that opens it in $EDITOR.
.PHONY: vault
vault: env-check ## Encrypt the vault, or edit it once it is encrypted
	@test -f $(VAULT_FILE) || { echo "error: $(VAULT_FILE) missing. Run: make init"; exit 1; }
	@if head -c 14 $(VAULT_FILE) | grep -q '^\$$ANSIBLE_VAULT'; then \
	  echo "$(BIN)/ansible-vault edit $(VAULT_ARGS) $(VAULT_FILE)"; \
	  $(BIN)/ansible-vault edit $(VAULT_ARGS) $(VAULT_FILE); \
	else \
	  echo "$(BIN)/ansible-vault encrypt $(VAULT_ARGS) $(VAULT_FILE)"; \
	  $(BIN)/ansible-vault encrypt $(VAULT_ARGS) $(VAULT_FILE); \
	fi

# Each TYPE runs the playbook of that stage, as it would run inside the whole
# deploy, with nothing after it: deploy.yml imports each one as it is, so the
# whole is their sum by construction and cannot drift from them. What builds
# or creates acts on the default VM without names; configure, check and
# ssh-config create nothing, never prompt, and reach every tagged VM.
#: TYPE=template    Build the cloud-init VM template on the Proxmox host, if it does not exist yet
#: TYPE=provision   Create and start the VM(s), and nothing after
#: TYPE=configure   Configure VMs that exist: packages, tools, Claude Code, GitHub projects, Tailscale (every tagged VM, unless named)
#: TYPE=check       Dry-run configure against the VMs (--check --diff; every tagged VM, unless named)
#: TYPE=ssh-config  Refresh the SSH aliases of the VMs this project created; without names, also prune those of VMs that are gone
#: TAGS=<role>,...  Configure only these roles: beside VM names as TYPE=configure, else with TYPE=configure or check (make deploy alpha TAGS=claude_code)
.PHONY: deploy
deploy: vault-check vm-name-check collections-check log-setup ## Create, configure and sign in the VM(s), then open VS Code
ifeq ($(KIND_deploy),)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(NO_TTY_ARGS) $(ANSIBLE_ARGS) deploy.yml
else ifeq ($(KIND_deploy),template)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(ANSIBLE_ARGS) playbooks/template.yml
else ifeq ($(KIND_deploy),provision)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/provision.yml
else ifeq ($(KIND_deploy),configure)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(TAG_ARGS) $(ANSIBLE_ARGS) playbooks/configure.yml
else ifeq ($(KIND_deploy),check)
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(TAG_ARGS) $(ANSIBLE_ARGS) --check --diff playbooks/configure.yml
else
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/ssh_config.yml
endif

# One VM at a time: VS Code opens one window per workspace, and "open it in
# the editor" does not mean a fleet. Read through the environment like
# vm-name-check, so the value is never interpolated into the recipe.
#: PROJECT=<repo>  Open one project folder on the VM rather than the whole projects directory
.PHONY: code
code: export VM_NAME_CHECK := $(value VM_NAME)
code: vault-check vm-name-check project-name-check collections-check log-setup ## Open a VM in VS Code with a Claude terminal
	@test -n "$$VM_NAME_CHECK" || { echo "error: code opens one VM: make code VM_NAME=<name>"; exit 1; }
	@case "$$VM_NAME_CHECK" in *,*) echo "error: code opens one VM at a time, not [$$VM_NAME_CHECK]"; exit 1;; esac
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(PROJECT_ARGS) $(ANSIBLE_ARGS) playbooks/code.yml

# The sign-ins only the person can make, each a page in the browser: Claude's
# (Remote Control needs a claude.ai login on the VM, and Anthropic supports no
# non-interactive way to create one), GitHub's device flow where the vault has
# no token, and Tailscale's web login where it has no auth key. Given VM names,
# each runs as far as it goes - the page opens here, and the browser comes back
# to the VM or the code is pasted at a prompt - one VM after another, and what
# it leads to follows: Remote Control, the private repositories. Without a name
# it reports every VM's state. Without TYPE, playbooks/login.yml runs all three
# in make deploy's order and by its rules; make deploy runs them for its VMs.
#: TYPE=claude     Sign Claude Code in by the browser, then start Remote Control
#: TYPE=github     Sign gh in to GitHub by the device flow, then clone the private repositories
#: TYPE=tailscale  Join Tailscale by its web login
.PHONY: login
login: vault-check vm-name-check collections-check log-setup ## Sign the VM(s) in to Claude, GitHub and Tailscale, or report how each is signed in
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(NO_TTY_ARGS) $(ANSIBLE_ARGS) playbooks/$(if $(KIND_login),$(KIND_login)_login,login).yml

.PHONY: list
list: vault-check vm-name-check collections-check log-setup ## Show the VMs this project created, and their addresses
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/list.yml

.PHONY: destroy
destroy: vault-check vm-name-check collections-check log-setup ## Release the VM(s) from Claude, GitHub and Tailscale, then delete them
	$(BIN)/ansible-playbook $(VAULT_ARGS) $(VM_ARGS) $(ANSIBLE_ARGS) playbooks/destroy.yml

.PHONY: logs
logs: export LOG_DIR_CHECK := $(LOG_DIR)
logs: ## List the run logs in .logs/, newest first
	@set -- "$$LOG_DIR_CHECK"/[0-9]*-*.log; \
	if [ -e "$$1" ]; then ls -1t "$$@" | head -20; else echo "No run logs in $$LOG_DIR_CHECK."; fi

# -------------------------------------------------------------- develop ----
# Every linter, in the order of their speed; the hooks last, since they can
# fix what they find - whitespace, the wiki's generated pages - and then fail.
# The first run of the hooks fetches the repositories .pre-commit-config.yaml
# names.
#: TYPE=lock     Check uv.lock against pyproject.toml
#: TYPE=yaml     yamllint --strict
#: TYPE=ansible  ansible-lint, production profile
#: TYPE=python   ruff check and ruff format --check
#: TYPE=syntax   ansible-playbook --syntax-check on every playbook
#: TYPE=hooks    The pre-commit hooks the others do not cover (SKIP= on the line runs every hook)
lint_runs = $(if $(KIND_lint),$(filter $(1),$(KIND_lint)),$(1))
.PHONY: lint
lint: export SKIP := $(PRE_COMMIT_SKIP)
lint: env-check ## Every linter: the lock, YAML, Ansible, Python, playbook syntax, pre-commit hooks
ifneq ($(call lint_runs,lock),)
	$(UV_RUN) lock --check
endif
ifneq ($(call lint_runs,yaml),)
	$(BIN)/yamllint --strict .
endif
ifneq ($(call lint_runs,ansible),)
	$(BIN)/ansible-lint
endif
ifneq ($(call lint_runs,python),)
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .
endif
ifneq ($(call lint_runs,syntax),)
	@for pb in deploy.yml playbooks/*.yml; do \
	  echo "== $$pb"; \
	  $(BIN)/ansible-playbook --syntax-check $$pb || exit 1; \
	done
endif
ifneq ($(call lint_runs,hooks),)
	PATH="$(ABSBIN):$$PATH" $(BIN)/pre-commit run --all-files
endif

# Molecule resolves ansible-playbook from PATH (and only appends the venv), so
# the venv must come first or a system-wide Ansible would be used instead of
# the pinned one. One entry point for every test, so CI runs exactly what a
# developer runs: CI used to inline the molecule call, which meant these could
# break without CI noticing.
#
# Without TYPE this is the local quality gate: lint first, which includes
# pre-commit's hooks, so a local pass covers the hooks CI runs too - it was
# possible to pass everything locally and still be failed by CI. Not a full
# superset of CI: CI also runs a gitleaks scan over the working tree and the
# commits a push brings in, which a local `make test` cannot reproduce from the
# index alone.
#
# .NOTPARALLEL: the Molecule scenarios each run a Docker container and build
# images into one small disk, so `make -j test` must not run them at once.
# They are serialised on purpose, as they are in the recipe itself.
#: TYPE=unit         pytest over tests/unit/: the filters, the github_repos module, the playbooks against stand-ins, the guards and the wiki (no network)
#: TYPE=molecule     Every Molecule (Docker) scenario: each role's, then the configure and provision playbooks'
#: TYPE=role         The roles' own scenarios: every role, or ROLE=<role>,... (make test ROLE=common,claude_code)
#: TYPE=integration  The full configure playbook against a Docker container
#: TYPE=provision    provision.yml and discover.yml against a fake Proxmox API
TEST_PARTS := $(if $(KIND_test),$(if $(filter molecule,$(KIND_test)),role integration provision,$(KIND_test)),unit role integration provision)
test_runs   = $(filter $(1),$(TEST_PARTS))
TEST_LINT  := $(if $(KIND_test),,lint)
.NOTPARALLEL:
.PHONY: test
test: collections-check log-setup $(TEST_LINT) ## Lint, then every test: unit and Molecule (the local quality gate)
ifneq ($(call test_runs,unit),)
	$(BIN)/pytest
endif
ifneq ($(call test_runs,role),)
	@for role in $(MOLECULE_ROLES); do \
	  echo "===== molecule: $$role"; \
	  (cd roles/$$role && PATH="$(ABSBIN):$$PATH" $(ABSBIN)/molecule test) || exit 1; \
	done
endif
ifneq ($(call test_runs,integration),)
	PATH="$(ABSBIN):$$PATH" $(BIN)/molecule test -s configure
endif
ifneq ($(call test_runs,provision),)
	PATH="$(ABSBIN):$$PATH" $(BIN)/molecule test -s provision
endif

# The wiki in docs/ (docs/development/docs.md). Its reference pages are
# generated from the files they describe, and tests/unit/test_docs.py, part of
# make unit, fails when one is out of date or a link is broken.
#: TYPE=serve  Serve the wiki as a site at http://127.0.0.1:8000, rebuilt as you save
#: TYPE=build  Build the wiki as a site in site/, failing on a broken link or anchor
.PHONY: docs
docs: env-check ## Regenerate the wiki's reference pages from the files they describe
ifeq ($(KIND_docs),serve)
	$(BIN)/zensical serve -a 127.0.0.1:8000
else ifeq ($(KIND_docs),build)
	$(BIN)/zensical build --strict --clean
else
	$(BIN)/python tools/docs.py
endif

# The Vagrantfile's Ansible provisioner runs the ansible-playbook it is given
# here, the environment's, rather than the first one on PATH.
#: TYPE=destroy  Destroy the Vagrant test VM
.PHONY: vagrant
vagrant: export ANSIBLE_PLAYBOOK = $(BIN)/ansible-playbook
vagrant: $(if $(KIND_vagrant),,env-check log-setup) ## End-to-end test of configure.yml on a VirtualBox VM
ifeq ($(KIND_vagrant),destroy)
	vagrant destroy -f
else
	vagrant up --provision
endif

# Only files named the way this Makefile names its logs, and only in LOG_DIR,
# read through the environment rather than interpolated into the recipe.
#: TYPE=logs  Delete only the run logs in LOG_DIR
.PHONY: clean
clean: export LOG_DIR_CHECK := $(LOG_DIR)
clean: ## Remove caches, the run logs and the built site (never your Python environment)
	@set -- "$$LOG_DIR_CHECK"/[0-9]*-*.log; \
	if [ -e "$$1" ]; then rm -f "$$@" && echo "Deleted $$# run log(s) from $$LOG_DIR_CHECK."; else echo "No run logs in $$LOG_DIR_CHECK."; fi; \
	rmdir "$$LOG_DIR_CHECK" 2>/dev/null || true
ifneq ($(KIND_clean),logs)
	rm -rf .cache .pytest_cache .ruff_cache .ansible .molecule collections site
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
endif

# --------------------------------------------------------------- checks ----
# What the targets above run first. None is meant to be run on its own.

# Refuse to run playbooks without vault.yml, or while it is still plaintext.
# A missing file used to fall through to the playbook, which died inside the
# cluster listing with "'vault_proxmox_api_token_secret' is undefined".
.PHONY: vault-check
vault-check:
	@test -f $(VAULT_FILE) || { echo "error: $(VAULT_FILE) missing. Run: make init"; exit 1; }
	@head -c 14 $(VAULT_FILE) | grep -q '^\$$ANSIBLE_VAULT' \
	  || { echo "error: $(VAULT_FILE) is not encrypted. Run: make vault"; exit 1; }

# Refuse to run a tool from anything but a virtualenv's bin: BIN is empty when
# PYTHON is no virtualenv's, which would otherwise have made it the scripts
# directory of pyenv's own Python or the system's - /usr/local/bin, where a
# system-wide Ansible lives. Every target that runs a tool runs this first, or
# collections-check, which does. Read through the environment, like VM_NAME.
.PHONY: env-check
env-check: export PYTHON_CHECK := $(value PYTHON)
env-check:
	@test -n "$(BIN)" || { \
	  echo "error: [$$PYTHON_CHECK] is not the Python of a virtualenv, and this project runs its tools only from"; \
	  echo "one of its own, never from a system-wide install. Make one, name it here, then run make install:"; \
	  echo "  pyenv virtualenv 3.12 claude-on-proxmox && pyenv local claude-on-proxmox"; \
	  echo "or: python3 -m venv ~/.virtualenvs/claude-on-proxmox, then PYTHON=~/.virtualenvs/claude-on-proxmox/bin/python"; \
	  exit 1; }

# Refuse to run Ansible with Galaxy collections other than the ones
# requirements.yml pins. Nothing else compares them: Ansible never reads that
# file, only make install does, and every checkout on this machine - a
# worktree, an older clone - shares ~/.ansible/collections, so a pull that
# bumped a pin, or make install in a checkout pinning an older one, left the
# runs on another version without a word. --offline resolves against what is
# installed and contacts nothing: with every pin installed it changes nothing,
# and otherwise it fails. First, that the environment has the tools at all.
.PHONY: collections-check
collections-check: env-check
	@test -x "$(BIN)/ansible-galaxy" \
	  || { echo "error: no ansible-galaxy in [$(BIN)], the bin of PYTHON's environment. Run: make install"; exit 1; }
	@$(BIN)/ansible-galaxy collection install -r requirements.yml --offline </dev/null >/dev/null 2>&1 \
	  || { echo "error: the installed Galaxy collections are not the ones requirements.yml pins. Run: make install"; exit 1; }

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

# PROJECT is one folder name, and a GitHub repository name is at most 100
# ASCII letters, digits, ".", "-" and "_". So no "/" - nothing outside the
# projects directory, nothing nested - and not "." or "..", which name the
# directory itself or its parent. Read the same way as VM_NAME, for the same
# reasons. Empty (no PROJECT) is fine: the whole directory opens.
.PHONY: project-name-check
project-name-check: export PROJECT_CHECK := $(value PROJECT)
project-name-check: export LC_ALL := C
project-name-check:
	@case "$$PROJECT_CHECK" in \
	  *[!A-Za-z0-9._-]*) echo "error: PROJECT may contain only letters, digits and . _ - (got: [$$PROJECT_CHECK])"; exit 1;; \
	  .|..) echo "error: PROJECT names one project folder, not [$$PROJECT_CHECK]"; exit 1;; \
	esac
	@test "$${#PROJECT_CHECK}" -le 100 || { echo "error: PROJECT is longer than the 100 characters a repository name can be"; exit 1; }

# What make init takes on the command line, each read through the environment
# like VM_NAME and refused, before anything is installed, when it holds a
# character outside its own set: none lets a quote, a space, a $ or a backtick
# through, so each sits whole inside the single-quoted JSON of INIT_GIVEN_ARGS.
# The playbook then holds each to its full rule (filter_plugins/settings.py).
.PHONY: init-args-check
init-args-check: export INIT_HOST_CHECK := $(value HOST)
init-args-check: export INIT_TOKEN_USER_CHECK := $(value TOKEN_USER)
init-args-check: export INIT_TOKEN_NAME_CHECK := $(value TOKEN_NAME)
init-args-check: export INIT_TOKEN_SECRET_FILE_CHECK := $(value TOKEN_SECRET_FILE)
init-args-check: export INIT_GITHUB_USER_CHECK := $(value GITHUB_USER)
init-args-check: export INIT_STORAGE_CHECK := $(value STORAGE)
init-args-check: export INIT_BRIDGE_CHECK := $(value BRIDGE)
init-args-check: export INIT_TAILSCALE_CHECK := $(value TAILSCALE)
init-args-check: export INIT_PROMPT_CHECK := $(value PROMPT)
init-args-check: export LC_ALL := C
init-args-check:
	@case "$$INIT_HOST_CHECK" in *[!A-Za-z0-9.:-]*) \
	  echo "error: HOST may contain only letters, digits and . : - (got: [$$INIT_HOST_CHECK])"; exit 1;; esac
	@case "$$INIT_TOKEN_USER_CHECK" in *[!A-Za-z0-9._@-]*) \
	  echo "error: TOKEN_USER may contain only letters, digits and . _ - @ (got: [$$INIT_TOKEN_USER_CHECK])"; exit 1;; esac
	@case "$$INIT_TOKEN_NAME_CHECK" in *[!A-Za-z0-9._-]*) \
	  echo "error: TOKEN_NAME may contain only letters, digits and . _ - (got: [$$INIT_TOKEN_NAME_CHECK])"; exit 1;; esac
	@case "$$INIT_TOKEN_SECRET_FILE_CHECK" in *[!A-Za-z0-9._/~+-]*) \
	  echo "error: TOKEN_SECRET_FILE may contain only letters, digits and . _ - / ~ + (got: [$$INIT_TOKEN_SECRET_FILE_CHECK])"; exit 1;; esac
	@case "$$INIT_GITHUB_USER_CHECK" in *[!A-Za-z0-9-]*) \
	  echo "error: GITHUB_USER may contain only letters, digits and - (got: [$$INIT_GITHUB_USER_CHECK])"; exit 1;; esac
	@case "$$INIT_STORAGE_CHECK" in *[!A-Za-z0-9._-]*) \
	  echo "error: STORAGE may contain only letters, digits and . _ - (got: [$$INIT_STORAGE_CHECK])"; exit 1;; esac
	@case "$$INIT_BRIDGE_CHECK" in *[!A-Za-z0-9._-]*) \
	  echo "error: BRIDGE may contain only letters, digits and . _ - (got: [$$INIT_BRIDGE_CHECK])"; exit 1;; esac
	@case "$$INIT_TAILSCALE_CHECK" in ''|on|off) ;; *) \
	  echo "error: TAILSCALE is on or off (got: [$$INIT_TAILSCALE_CHECK])"; exit 1;; esac
	@case "$$INIT_PROMPT_CHECK" in ''|yes|no) ;; *) \
	  echo "error: PROMPT is yes or no (got: [$$INIT_PROMPT_CHECK])"; exit 1;; esac

# Make LOG_DIR, private, and prune it. Only files named the way this Makefile
# names them, and only in LOG_DIR: an ANSIBLE_LOG_PATH of your own pointing
# elsewhere is used as it is and its directory never touched. Read through the
# environment rather than interpolated into the recipe.
.PHONY: log-setup
log-setup: export LOG_DIR_CHECK := $(LOG_DIR)
log-setup: export LOG_RETENTION_CHECK := $(LOG_RETENTION_DAYS)
log-setup:
	@if [ -n "$$ANSIBLE_LOG_PATH" ]; then \
	  mkdir -p "$$(dirname "$$ANSIBLE_LOG_PATH")"; \
	  if [ -n "$$LOG_DIR_CHECK" ] && [ -d "$$LOG_DIR_CHECK" ]; then \
	    chmod 700 "$$LOG_DIR_CHECK"; \
	    case "$$LOG_RETENTION_CHECK" in \
	      ''|*[!0-9]*) echo "warning: LOG_RETENTION_DAYS must be a number of days; nothing pruned";; \
	      *) find "$$LOG_DIR_CHECK" -maxdepth 1 -type f -name '[0-9]*-*.log' -mtime +"$$LOG_RETENTION_CHECK" -delete;; \
	    esac; \
	  fi; \
	  echo "Logging this run to $$ANSIBLE_LOG_PATH"; \
	fi
