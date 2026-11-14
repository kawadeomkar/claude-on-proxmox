# Developer entry points. Every recipe runs its tools from the Python
# environment you made for this project, into which `just install` puts
# exactly the versions locked in uv.lock - never a tool that merely happens to
# be on PATH. `just` lists the recipes, `just help <recipe>` one's options.
#
# Few recipes, each of which does all of its job by default; an option or a
# word narrows one to a single part of it (just deploy --only configure, just
# test integration). Options go after the recipe's name.
#
# How every recipe runs (docs/architecture/decisions.md §15.3): a bash script
# with no dependencies. Its first lines refuse a run started in another
# checkout (tools/check checkout), then check its own arguments - and, for a
# recipe that changes something, take the run lock (tools/check lock) - then
# what it needs - the environment (tools/env-run --bin), the vault, the collections,
# and where it reads the settings no name 1.0 renamed (tools/check settings) -
# and only then does it work, its last command exec'd - but in a recipe drawn
# in parts (lint, init, test), whose trap must still run to say the run's end
# (tools/live). Nothing is a dependency, which JUST_NO_DEPS or --no-deps would
# skip, and no assignment here starts a program, which just would do before
# any line ran. A value a person typed reaches a script as an argument ("$1"),
# never as its text: just holds it to its [arg] pattern first, and what goes
# into JSON is checked again there.
# A recipe's words are a * parameter even where it takes one: a parameter with
# a default lists as NAME='', make's spelling, which just refuses after the
# recipe, and leaves the word after it to run as a second recipe (just help
# deploy test ran every test). Named in the singular, it takes one word, and
# the script refuses a second.
# just itself is yours to install, as uv is: 1.59.0 or later.

set minimum-version := '1.59.0'
# bash rather than sh: Ubuntu's sh (dash) dies of a Ctrl-C that the playbook
# it runs has caught and survived - Ansible's pause, Ctrl-C then C - so the run
# would end with it, [continue] or not. bash waits for the playbook and carries
# on. The scripts keep to what macOS's bash 3.2 has: no arrays. -e ends a
# script at a command that fails, but not at one left of && or ||, nor at
# the if around it: a check or a tool whose failure must fail the recipe is
# a line of its own, or the last of its list.
set shell := ['bash', '-cu']
set script-interpreter := ['bash', '-eu']

# The version CI runs (.github/actions/setup) and the oldest this takes;
# minimum-version above can only be written as a literal, so the pin is
# written here too, and tests/unit/test_justfile.py holds the three to one.
just_pin := '1.59.0'

# The Python of that environment (tools/env-run decides): a virtualenv of your
# own when you bring one - PYTHON=/path/to/python, exported or before the
# recipe (just PYTHON=... install), or python3 as this directory's shell finds
# it, when that is a virtualenv (one you activated, or one a version manager
# picks here) - and otherwise this checkout's own, which just install makes
# with uv in its folder outside the checkout (tools/env-run --data).
PYTHON := env('PYTHON', '')
# uv installs the locked packages into that environment, and for this
# checkout's own downloads the Python it runs on (managed_python). Install it
# yourself (https://docs.astral.sh/uv/getting-started/installation/, e.g.
# `brew install uv`); it never downloads a Python for an environment of yours.
UV := env('UV', 'uv')
# The Python version this checkout's own environment runs on.
managed_python := '3.13'
# Anything else for ansible-playbook, as the shell words they are, exported
# or before the recipe: just ANSIBLE_ARGS='-vvv -f 30' deploy alpha.
ANSIBLE_ARGS := env('ANSIBLE_ARGS', '')
# The run logs (tools/run-logged): LOG_DIR= turns them off, and `just logs`
# lists them.
LOG_DIR := env('LOG_DIR', justfile_directory() / '.logs')
LOG_RETENTION_DAYS := env('LOG_RETENTION_DAYS', '14')
# Where just status keeps what it can work out again: the settings and each
# VM's upgrades (docs/architecture/lifecycle.md §4.6): git-ignored, and refused
# by tests/check_no_local_files.sh.
STATUS_DIR := env('STATUS_DIR', justfile_directory() / '.cache' / 'status')
# Where a run drawn in parts keeps its model between its parts (tools/live), a file named for the run's stamp and its
# top recipe that its end removes: git-ignored, and refused by tests/check_no_local_files.sh. LIVE_DIR= keeps none, and
# a run in parts is then not drawn: each part is passed through.
LIVE_DIR := env('LIVE_DIR', justfile_directory() / '.cache' / 'live')
# From the command line only (just BIN=<dir> ...): the tools of another
# directory, which the unit tests point at stand-ins. A BIN in the
# environment, a name many shells use for ~/bin, is never read.
BIN := ''
VAULT_FILE := 'inventory/group_vars/all/vault.yml'
VAULT_PASS := '.vault_pass'
# Where a person sets the settings: tools/check settings refuses a name 1.0
# renamed in them, which nothing would read.
SETTINGS_FILES := 'inventory/hosts.yml inventory/group_vars/all/local.yml'
# What `just init` copies from its .example beside it, where it is missing.
INIT_FILES := 'inventory/hosts.yml inventory/group_vars/all/local.yml ' + VAULT_FILE
# From the command line only, as BIN: where just init puts the git hook,
# tools/git-hook, which the unit tests point into a folder of their own. Empty
# is git's own place for it, the hooks folder every worktree of a clone shares.
GIT_HOOK := ''
# From the command line only, as BIN: someone is at this machine's screen, with no terminal - an agent's run, which
# tools/mcp starts. 1: the sign-ins run and open their pages here, and nothing asks a question (no_tty_args). Empty,
# a run with no terminal is taken for one nobody watches.
ATTENDED := ''
# One run that changes something at a time, from a terminal or an agent (tools/check lock): two deploys could take one
# VM ID, and runs share the managed SSH config. From the command line only, as BIN; RUN_LOCK= takes none, as LOG_DIR=
# keeps no log.
RUN_LOCK := justfile_directory() / '.cache' / 'run.lock'
# From the command line only, as BIN: the folder of the checkout's hosts but the first, one folder each, and the name of
# the current one (hosts/current), which tools/check host reads and just host writes. Git-ignored, and refused by
# tests/check_no_local_files.sh; the unit tests point it into a folder of their own.
HOSTS_DIR := justfile_directory() / 'hosts'

# One run, one moment: every log of the run is named after it.
stamp := datetime('%Y%m%d-%H%M%S')
# The top recipe of a run drawn in parts, which a recipe sets on another it runs as a stage of its own run
# ({{ self }} live_run=test lint): that one then draws into the parent's run, under its title. A setting, never the
# environment, so a variable left exported joins no run.
live_run := ''
# The host of a run, for a recipe run as a part of another's ({{ self }} nested_host="$host" <recipe>): its --host, so
# that the two are one run against one host. A setting, never the environment, as live_run; empty, the recipe resolves
# its own.
nested_host := ''
# The same just on the same justfile, with every setting of this run: what a
# recipe that includes another's work (test lints, init installs) runs it by.
# A recursive just would otherwise start without the command line's BIN= and
# the rest; tests/unit/test_justfile.py holds this to every setting above.
self := quote(just_executable()) + ' --justfile ' + quote(justfile()) + ' PYTHON=' + quote(PYTHON) + ' UV=' + quote(UV) + ' ANSIBLE_ARGS=' + quote(ANSIBLE_ARGS) + ' LOG_DIR=' + quote(LOG_DIR) + ' LOG_RETENTION_DAYS=' + quote(LOG_RETENTION_DAYS) + ' STATUS_DIR=' + quote(STATUS_DIR) + ' BIN=' + quote(BIN) + ' VAULT_FILE=' + quote(VAULT_FILE) + ' VAULT_PASS=' + quote(VAULT_PASS) + ' SETTINGS_FILES=' + quote(SETTINGS_FILES) + ' INIT_FILES=' + quote(INIT_FILES) + ' GIT_HOOK=' + quote(GIT_HOOK) + ' LIVE_DIR=' + quote(LIVE_DIR) + ' ATTENDED=' + quote(ATTENDED) + ' RUN_LOCK=' + quote(RUN_LOCK) + ' HOSTS_DIR=' + quote(HOSTS_DIR) + ' stamp=' + quote(stamp) + ' live_run=' + quote(live_run) + ' nested_host=' + quote(nested_host)
# The first line of every recipe but help.
guard := quote(justfile_directory() / 'tools/check') + ' checkout ' + quote(invocation_directory())
# The names given ("$@"), joined with commas into $names. A VM name with an underscore, which Proxmox refuses in a
# name, is taken with a hyphen in its place, and said. The names are held to their pattern, which has no space or
# wildcard, so splitting $names on , is safe.
hyphenate_names := 'names=$(IFS=,; printf %s "$*"); for name in $(printf %s "$names" | tr , " "); do case "$name" in *_*) echo "warning: Proxmox does not allow underscores in VM names, so $name was changed to $(printf %s "$name" | tr _ -)" >&2 ;; esac; done; names=$(printf %s "$names" | tr _ -)'
# The scripts directory of the environment tools/env-run finds, or BIN: refused, with how to make one, for a Python
# that is no virtualenv's; and this checkout's own Galaxy collections, which every Ansible the recipe runs is
# pointed at, so no other checkout's just install can change them.
find_bin := 'bin=' + quote(BIN) + '; [ -n "$bin" ] || bin=$(PYTHON=' + quote(PYTHON) + ' tools/env-run --bin); ANSIBLE_COLLECTIONS_PATH=$(tools/env-run --collections); export ANSIBLE_COLLECTIONS_PATH'
# tools/run-logged with the run's logging settings; its first argument is the
# log's name.
run_logged := 'env LOG_DIR=' + quote(if LOG_DIR == '' { '' } else { absolute_path(LOG_DIR) }) + ' LOG_RETENTION_DAYS=' + quote(LOG_RETENTION_DAYS) + ' AGENT_WAREHOUSE_LOG_STAMP=' + quote(stamp) + ' ' + quote(justfile_directory() / 'tools/run-logged')
# tools/live with the environment's Python and the run's logging settings, each option in its = form so that a
# value starting with - (ANSIBLE_ARGS=-vvv) is still its value: `run <stage> [--step <label>] -- <command>` in front
# of each command of a recipe drawn in parts, `end "$?"` from its trap.
live := '"$bin/python" ' + quote(justfile_directory() / 'tools/live') + ' --log-dir=' + quote(if LOG_DIR == '' { '' } else { absolute_path(LOG_DIR) }) + ' --retention=' + quote(LOG_RETENTION_DAYS) + ' --stamp=' + quote(stamp) + ' --ansible-args=' + quote(ANSIBLE_ARGS)
# Where a run in parts keeps its state, but for the top recipe's name, its shell's PID - two runs started in one
# second keep two - and .json: none without LIVE_DIR.
live_state := if LIVE_DIR == '' { '' } else { absolute_path(LIVE_DIR) / stamp }
# The playbook's or tool's arguments, started afresh ("$@" held the recipe's
# own): --vault-password-file .vault_pass, where there is one, decided by the
# shell when the line runs (init's questions may make it), not when just reads
# this.
vault_args := 'set --; [ ! -f ' + quote(VAULT_PASS) + ' ] || set -- "$@" --vault-password-file ' + quote(VAULT_PASS)
# A VM recipe's: the vault's, then the names ($names, from hyphenate_names), if
# any. Passed as JSON, not key=value: Ansible's key=value parser splits
# extra-vars on whitespace, so make's VM_NAME="alpha beta" once became alpha
# and only half the fleet was created. Then, with more than one host, the
# others' names and addresses ($host_extra, host_env's): the guards that keep
# them out of a VM's name, its alias and destroy's forgetting of a key.
vm_args := vault_args + '; [ -z "$names" ] || set -- "$@" -e "{\"vm_name\": \"$names\"}"; [ -z "$host_extra" ] || set -- "$@" -e "$host_extra"'
# A run with no terminal on stdin has nobody to approve a sign-in: no page
# opens and nothing reaches the clipboard, since nobody is there to use them,
# and just deploy skips its sign-ins, which would otherwise start by themselves
# and wait for a browser. Nor is a VM asked whether to sign in now (with
# github_login_auto_start or tailscale_login_auto_start off): pause would come
# back at once with nothing, which is Enter, so the VM is left and said instead
# (claude_vm_login_terminal, in github_login.yml and tailscale_login.yml). Any
# of the three given in ANSIBLE_ARGS still wins: it comes later on the command
# line. With ATTENDED=1 someone is at the screen with no terminal: the pages
# open and deploy signs in, and only the asking stays off.
no_tty_args := '[ -t 0 ] || if [ ' + quote(ATTENDED) + ' = 1 ]; then set -- "$@" -e ' + quote('{"claude_vm_login_terminal":false}') + '; else set -- "$@" -e ' + quote('{"login_desktop":false,"deploy_sign_in":false,"claude_vm_login_terminal":false}') + '; fi'
# The run lock (RUN_LOCK), taken by a recipe that changes something as the
# first line after its rules, with its run's title: a record of the recipe's
# script - its pid, which the script keeps as it execs into the run, and that
# process's start - never an fd lock, which ssh's ControlPersist masters and an
# editor a run starts would inherit and hold.
take_lock := 'tools/check lock ' + quote(RUN_LOCK) + ' "$$"'
# The run's host (tools/check host): the one --host names ($on), else nested_host's, else the one the VMs named ($names)
# are on, as their aliases record, else the one hosts/current names, else the first; refused as the resolver says - a host not set up, a setting of the shared files no host inherits, a token
# in the wrong vault. Then, for the lines after it: ANSIBLE_INVENTORY exported for a host in hosts/ - the shared
# controller.yml, then the host's folder, whose group_vars outrank the shared ones - and $host, $hosts_many (set with
# more than one host), $settings_files, $vault_files, $host_sources, $host_extra and $host_routed (set where the names
# chose the host). A line of the recipe's script after
# its own rules and before its lock, since the lock's title names the host; the environment's bin found first, whose
# Python reads the hosts' YAML. The assignment's own line, since a refusal in a command substitution under eval would
# go unseen. With one host - nothing in HOSTS_DIR - and none asked for, there is nothing to choose: the run is as it
# always was, the resolver not started.
host_env := find_bin + '; if [ -z "$on" ] && [ -z ' + quote(nested_host) + ' ] && [ -z "$(ls -A ' + quote(HOSTS_DIR) + ' 2>/dev/null)" ]; then host= hosts_many= settings_files=' + quote(SETTINGS_FILES) + ' vault_files=' + quote(VAULT_FILE) + ' host_sources= host_extra= host_routed=; else host_said=$(tools/check host "$bin" env${host_lenient:+ --lenient} --checkout=' + quote(justfile_directory()) + ' --hosts-dir=' + quote(HOSTS_DIR) + ' --vault-file=' + quote(VAULT_FILE) + ' --settings-files=' + quote(SETTINGS_FILES) + ' --vault-pass=' + quote(VAULT_PASS) + ' --nested=' + quote(nested_host) + ' --host="$on" --names="$names"); eval "$host_said"; fi'

# deploy's stages, which deploy asks about when one is given as a VM name
# (below). A recipe's name is a VM name there like any other: deploy takes
# every word after it as one, and just runs no second recipe.
stages := 'template|provision|configure|check|ssh-config'
# The roles configuration can be narrowed to (deploy --tags), as configure.yml
# tags them, and the roles with a Molecule scenario of their own (test --role):
# those, and the two that build the template and the VMs.
configure_roles := 'common vscode_server dev_tools claude_code github_projects tailscale'
scenario_roles := configure_roles + ' proxmox_template proxmox_vm'
# Every Molecule scenario, the longest first, as test --jobs starts them, so that the short ones fill in beside it:
# their order of time on CI's runners (2026-11). test_justfile.py holds it to the scenarios.
molecule_order := 'provision configure image tailscale claude_code proxmox_vm common dev_tools vscode_server github_projects proxmox_template'
# Hooks lint already runs itself, so its hooks part skips them, the
# production-profile ansible-lint and ruff never running a second time
# (ruff-format even rewriting files). gitleaks is in it because its hook scans
# only what is staged, and CI scans the tree and the pushed commits separately.
pre_commit_skip := 'yamllint,ansible-lint,ruff-check,ruff-format,gitleaks'
# What init says when a word follows its options - a bare word, or an
# answer as make took it, TOKEN_SECRET=... - without saying the word, which
# may be the secret.
init_extra_refused := "echo \"error: just init takes only its options (just help init), and nothing after them - never the token secret, which the command line would put in the shell's history and the process list: put it in a file only you can read, just init --token-secret-file <file>, or export AGENT_WAREHOUSE_TOKEN_SECRET from wherever your secrets live - a CI secret, or read -rs at a prompt\" >&2; exit 1"

[arg('RECIPE', pattern='[a-z]+', help='The one recipe whose options to show')]
[default]
[doc('Show the recipes, or the options of one: just help <recipe>')]
[no-exit-message]
[positional-arguments]
[script]
help *RECIPE:
    [ "$#" -le 1 ] || { echo "error: just help shows one recipe's options at a time (got: [$*])" >&2; exit 1; }
    if [ "$#" -eq 1 ]; then exec {{ quote(just_executable()) }} --justfile {{ quote(justfile()) }} --usage "$1"; fi
    {{ quote(just_executable()) }} --justfile {{ quote(justfile()) }} --list --unsorted
    echo
    echo "Without names, deploy and destroy act on agent-warehouse-default; status, login and deploy"
    echo "--only configure, --check or --only ssh-config act on every VM this project created."
    echo "Options go after the recipe's name, settings before it or exported: ANSIBLE_ARGS='...' passes"
    echo "anything else to ansible-playbook in init, deploy, code, login and destroy (status takes -e and -i), and"
    echo "LOG_DIR=<dir> moves the run logs: just ANSIBLE_ARGS=-vvv deploy alpha."
    echo "With more than one Proxmox host, every command runs against the current one (just host lists them), or the"
    echo "one --host names."

# ------------------------------------------------------------------ use ----
# just install, the git hook, the local config files - copied from their
# examples only where they are missing - then playbooks/init_settings.yml,
# which asks, at a terminal, for each value a first deploy needs that is still
# the example's: the GitHub username, whether to use Tailscale (with its auth
# key after a y, which no deploy needs), the Proxmox host's address, and the
# API token's ID and secret. Enter leaves one for the next just init, and the vault
# is written encrypted. Without a terminal it asks nothing, and writes only the
# values given as options, so just init can be scripted. Then
# playbooks/init_check.yml checks what just deploy will need of Proxmox - the
# token, its privileges, the template or root SSH, the node, the storage and
# the bridge, an SSH key - suggests a storage or bridge where the default does
# not fit, and fails on a problem, so a scripted just init exits non-zero.
#
# The values given reach both playbooks as one JSON extra var, init_given,
# empty ones left out. The token's secret is never one of them: it is never
# on the command line, which the shell's history keeps and the process list
# shows. init_settings.yml reads it from --token-secret-file's file, or from
# AGENT_WAREHOUSE_TOKEN_SECRET in the environment. A word after the options
# is caught by EXTRA and refused without being said.
#
# --host <name> sets up another host of the checkout's, in hosts/<name>/
# (tools/check host init): its address, its token and what the checks find of
# it, written into its own files, with every setting it sets for itself; the
# GitHub account and the VPN are every host's, and not asked again. A host is
# never created without --address or a terminal to ask for one, nor while the
# checkout has no other: there, a bare word was 2.4's address, which is
# --address from 2.5. Through 2.x a --host value with a dot or a colon is still
# taken for the address, and said.
[arg('EXTRA', help='Nothing: any word after the options is refused, unsaid')]
[arg('PROMPT', long='no-prompt', value='no', help='Ask nothing, even at a terminal')]
[arg('ON', long='host', pattern='[A-Za-z0-9.:-]*', help='Set up another host of this checkout, by its name (just host lists them); a value with a dot or a colon is taken for --address through 2.x')]
[arg('BRIDGE', long='bridge', pattern='[A-Za-z0-9._-]*', help='The bridge the template is built on, for local.yml (before the template exists)')]
[arg('TAILSCALE', long='tailscale', pattern='(on|off)?', help="Answers init's VPN question: on, each VM joins your tailnet; off, none - a VPN of yours that routes the LAN, or none at all (vpn in local.yml)")]
[arg('ADDRESS', long='address', pattern='[A-Za-z0-9.:-]*', help="The Proxmox host's address, for its hosts.yml")]
[arg('STORAGE', long='storage', pattern='[A-Za-z0-9._-]*', help='The storage for VM disks, for local.yml: written when the token can use it there')]
[arg('TOKEN_ID', long='token-id', pattern='[A-Za-z0-9._-]*', help="The API token's ID, the part after its user's !, when it is not ansible")]
[arg('GITHUB_USER', long='github-user', pattern='[A-Za-z0-9-]*', help='The GitHub account whose repositories are cloned')]
[arg('TOKEN_USER', long='token-user', pattern='[A-Za-z0-9._@-]*', help="The API token's user, when it is not ansible@pve")]
[arg('TOKEN_SECRET_FILE', long='token-secret-file', pattern='[A-Za-z0-9._/~+-]*', help="A file holding the token's secret (or AGENT_WAREHOUSE_TOKEN_SECRET in the environment; never on the command line)")]
[continue]
[doc('Set up this checkout, then ask for what a first deploy needs')]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
init ADDRESS='' ON='' TOKEN_USER='' TOKEN_ID='' TOKEN_SECRET_FILE='' GITHUB_USER='' STORAGE='' BRIDGE='' TAILSCALE='' PROMPT='' *EXTRA:
    {{ guard }}
    {{ if EXTRA == '' { '' } else { init_extra_refused } }}
    address=$1 on=$2 token_user=$3 token_name=$4 token_secret_file=$5 github_user=$6 storage=$7 bridge=$8 tailscale=$9 prompt=${10}
    # --host names a host from 2.5; through 2.x a value with a dot or a colon is an address, as it was.
    host_name=
    case "$on" in
    '') ;;
    *[.:]*)
      [ -z "$address" ] || { echo "error: --host $on and --address $address: --host names a host of this checkout's, and its address is --address" >&2; exit 1; }
      echo "warning: --host names a host of this checkout's from 2.5, so $on, an address, is taken for --address $on, as before: give it so" >&2
      address=$on ;;
    *)
      case "$on" in [!abcdefghijklmnopqrstuvwxyz0123456789]* | *[!abcdefghijklmnopqrstuvwxyz0123456789-]*)
        echo "error: a host's name is lowercase letters, digits and -, from a letter or a digit (got: [$on])" >&2; exit 1 ;;
      esac
      host_name=$on ;;
    esac
    if [ -n "$host_name" ] && [ -n "$github_user$tailscale" ]; then
      echo "error: the GitHub account and the VPN are every host's, in the shared files: give --github-user and --tailscale to just init alone" >&2
      exit 1
    fi
    # A relative file is where just was started, not this directory, where the
    # recipe runs; ~ is left for the playbook to expand.
    case "$token_secret_file" in '' | /* | '~'*) ;; *) token_secret_file={{ quote(invocation_directory()) }}/$token_secret_file ;; esac
    # Each value's characters again, as its pattern has them: each sits whole
    # inside the JSON below. The playbook then holds each to its full rule
    # (filter_plugins/settings.py).
    only() {
      case "$2" in *[!$3]*) echo "error: --$1 may contain only $4 (got: [$2])" >&2; exit 1 ;; esac
    }
    letters=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789
    only address "$address" "$letters.:-" 'letters, digits and . : -'
    only token-user "$token_user" "$letters._@-" 'letters, digits and . _ - @'
    only token-id "$token_name" "$letters._-" 'letters, digits and . _ -'
    only token-secret-file "$token_secret_file" "$letters._/~+-" 'letters, digits and . _ - / ~ + (give an absolute path where the directory has others)'
    only github-user "$github_user" "$letters-" 'letters, digits and -'
    only storage "$storage" "$letters._-" 'letters, digits and . _ -'
    only bridge "$bridge" "$letters._-" 'letters, digits and . _ -'
    given=
    for pair in host="$address" token_user="$token_user" token_name="$token_name" token_secret_file="$token_secret_file" \
      github_user="$github_user" storage="$storage" bridge="$bridge" tailscale="$tailscale"; do
      [ -z "${pair#*=}" ] || given="$given${given:+,}\"${pair%%=*}\":\"${pair#*=}\""
    done
    set --
    [ -z "$given" ] || set -- -e "{\"init_given\":{$given}}"
    # init asks only at a terminal, and never with --no-prompt: init_ask is
    # false otherwise, so a run with no terminal, or the playbook run by hand,
    # asks nothing and writes only what was given.
    asking=
    [ "$prompt" = no ] || ! [ -t 0 ] || { asking=1; set -- -e '{"init_ask":true}' "$@"; }
    {{ take_lock }} init${host_name:+ --host $host_name}
    {{ self }} install
    {{ find_bin }}
    # The git hook names no environment (tools/git-hook), so each worktree commits through its own: pre-commit install
    # would name this one's Python in the hooks folder they share. Another tool's hook moves aside to pre-commit.legacy,
    # which the hook runs first, as pre-commit install does; a core.hooksPath folder, which may be every repository's,
    # is left alone.
    hook={{ quote(GIT_HOOK) }}
    if [ -z "$hook" ] && git config core.hooksPath >/dev/null; then
      echo "warning: git's core.hooksPath is set, so just init put no git hook there: copy tools/git-hook into that folder as pre-commit to have one" >&2
    else
      [ -n "$hook" ] || hook=$(git rev-parse --git-path hooks/pre-commit)
      if ! cmp -s tools/git-hook "$hook" || ! [ -x "$hook" ]; then
        said="Installed git's pre-commit hook."
        if [ -e "$hook" ] || [ -L "$hook" ]; then
          if grep -q -e 'File generated by pre-commit' -e "agent-warehouse's pre-commit hook" -e "claude-on-proxmox's pre-commit hook" "$hook" 2>/dev/null; then
            rm -f "$hook"
            said="Updated git's pre-commit hook to this version's."
          else
            mv "$hook" "$hook.legacy"
            said="Installed git's pre-commit hook; another tool's, now pre-commit.legacy, still runs first."
          fi
        fi
        mkdir -p "$(dirname "$hook")"
        cp tools/git-hook "$hook"
        chmod 755 "$hook"
        echo "$said"
      fi
    fi
    # Readable by you alone, as .vault_pass is: the vault is plaintext until just vault encrypts it, and is
    # filled in by hand before that.
    copied=
    for file in {{ INIT_FILES }}; do
      test -f "$file" || { (umask 077 && cp "$file.example" "$file"); copied="$copied $file"; }
    done
    [ -z "$copied" ] || echo "Created$copied from their .example files (git-ignored)."
    # The host's own files, for another host: its folder, and each file from tools/templates/host/ where it is missing.
    # Then what both playbooks are pointed at - its files, the vault's name for its token, what it sets for itself -
    # as JSON ($init_host_json), and its inventory sources.
    init_settings_files={{ quote(SETTINGS_FILES) }} init_vault={{ quote(VAULT_FILE) }} init_made=
    host_said=$(tools/check host "$bin" init "$host_name" --checkout={{ quote(justfile_directory()) }} --hosts-dir={{ quote(HOSTS_DIR) }} --address="$address" ${asking:+--asking})
    eval "$host_said"
    [ -z "$init_made" ] || echo "Created $init_made from tools/templates/host/ (git-ignored)."
    set -- "$@" -e "$init_host_json"
    # --asking: no VPN chosen yet is this run's question, not a warning.
    tools/check settings "$bin" --asking $init_settings_files
    # Drawn live from here, after just install, as one run in two parts - the questions, then the checks - unless it
    # is a stage of another recipe's run (live_run), or there is no state to carry the run between them (LIVE_DIR=,
    # when each playbook prints as it did): its title, which no value given goes into, the state the two playbooks
    # share, and a trap that says the run's end however it ends, so neither is exec'd.
    if [ -z {{ quote(live_run) }} ] && [ -n {{ quote(live_state) }} ]; then
      AGENT_WAREHOUSE_LIVE=init
      AGENT_WAREHOUSE_LIVE_STATE={{ quote(live_state) }}-init-$$.json
      export AGENT_WAREHOUSE_LIVE AGENT_WAREHOUSE_LIVE_STATE
      live_end() { {{ live }} end "$1" || :; }
      trap 'rc=$?; live_end "$rc"; exit "$rc"' EXIT
    fi
    {{ run_logged }} init "$bin/ansible-playbook" -i playbooks/inventory/init.yml "$@" {{ ANSIBLE_ARGS }} playbooks/init_settings.yml
    # The checks run with the project's inventory and the vault, whose password
    # is looked for now: the questions may have just made .vault_pass. Another
    # host's, through its sources over the shared ones, as every run against it.
    if head -c 14 "$init_vault" | grep -q '^\$ANSIBLE_VAULT' && [ ! -f {{ quote(VAULT_PASS) }} ] && [ -z "${ANSIBLE_VAULT_PASSWORD_FILE:-}" ]; then
      echo "Proxmox is not checked: $init_vault is encrypted, and there is no {{ VAULT_PASS }} or ANSIBLE_VAULT_PASSWORD_FILE to open it with."
      exit 0
    fi
    [ ! -f {{ quote(VAULT_PASS) }} ] || set -- --vault-password-file {{ quote(VAULT_PASS) }} "$@"
    [ -z "$host_sources" ] || { ANSIBLE_INVENTORY=$host_sources; export ANSIBLE_INVENTORY; }
    {{ run_logged }} init "$bin/ansible-playbook" "$@" {{ ANSIBLE_ARGS }} playbooks/init_check.yml
    # A host set up and checked, at a terminal: the offer to make it the current one.
    if [ -n "$host_name" ] && [ -n "$asking" ] && [ "$(tools/check host "$bin" current --checkout={{ quote(justfile_directory()) }} --hosts-dir={{ quote(HOSTS_DIR) }})" != "$host_name" ]; then
      printf 'Make %s the current host, which every command runs against? y, or Enter for no: ' "$host_name"
      read -r answer || answer=
      case "$answer" in
      y | Y) tools/check host "$bin" switch "$host_name" --checkout={{ quote(justfile_directory()) }} --hosts-dir={{ quote(HOSTS_DIR) }} ;;
      *) echo "just host $host_name makes it the current host, and --host $host_name on a command reaches it once." ;;
      esac
    fi

# uv's sync installs the versions the lock pins into the environment
# tools/env-run finds, after checking the lock still matches pyproject.toml.
# This checkout's own it makes the first time, on a Python uv downloads
# (managed_python), and keeps exactly the lock's. One of yours it syncs
# --inexact: a package the lock does not list - yours, or another project's in
# an environment that serves both - stays where it is; --python names its
# interpreter, so uv never swaps it for another, and downloads none. Before the sync, tools/stale_scripts.py names each locked package
# with a command that starts a Python outside the environment's bin - uv
# writes the path the environment was synced by into every command, so a sync
# through a link into it (the .venv make venv once made) left them naming the
# link, and a sync that installs nothing rewrites none - and the sync
# reinstalls those, then checks that none is left. ansible-galaxy installs the
# collections requirements.yml pins, into this checkout's own folder for them,
# with --no-cache: its response cache writes an empty entry before each request,
# and the retry after one that failed (a 502, a timeout) reads that entry and
# stops with "Missing expected 'results' in ansible-galaxy cache" (ansible-core
# 2.21), never asking Galaxy again. Both change nothing when they are
# current, so this is also the command to run after a pull, and what CI runs.
[arg('LOCK', long='lock', value='lock', help='Update uv.lock after editing a pin in pyproject.toml, installing nothing (uv from PATH)')]
[doc("Install the locked Python packages and the Galaxy collections: into your virtualenv, or this checkout's own")]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
install LOCK='':
    {{ guard }}
    if [ -n "$1" ]; then UV_PYTHON_DOWNLOADS=never exec {{ quote(UV) }} lock; fi
    {{ take_lock }} install
    # The checkout's folder had another name through 1.x: moved once, its collections kept. An environment in it is
    # made again, since uv wrote the folder's old path into each of its commands.
    data=$(tools/env-run --data)
    legacy=$(tools/env-run --legacy-data)
    if [ ! -e "$data" ] && [ -d "$legacy" ]; then
      mkdir -p "$(dirname "$data")"
      mv "$legacy" "$data"
      rm -rf "$data/venv"
      echo "Moved this checkout's own folder from $legacy to $data, its name from 2.0."
    fi
    which=$(PYTHON={{ quote(PYTHON) }} tools/env-run --which)
    case "$which" in
    own\ *)
      {{ find_bin }}
      python=$("${which#own }" -c 'import sys; print(sys.executable)')
      prefix=$("$python" -c 'import sys; print(sys.prefix)')
      reinstall=$("$python" -I tools/stale_scripts.py)
      UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT="$prefix" {{ quote(UV) }} sync --locked --inexact --python "$python" $reinstall
      [ -z "$reinstall" ] || "$python" -I tools/stale_scripts.py --check
      ;;
    *)
      venv=${which#managed }
      venv=${venv%/bin/python}
      echo "Installing into this checkout's own environment, $venv: PYTHON is unset and python3 is no virtualenv."
      UV_PYTHON_DOWNLOADS=automatic UV_PROJECT_ENVIRONMENT="$venv" {{ quote(UV) }} sync --locked --managed-python --python {{ managed_python }}
      {{ find_bin }}
      ;;
    esac
    # Collections already at their pins are said here: ansible-galaxy's own "Nothing to do" for them advises --force,
    # an option of its own that just install does not take. tools/check collections asks it offline, as every recipe
    # that runs Ansible does, and it installs only when one is not.
    if tools/check collections "$bin" 2>/dev/null; then
      echo "The Galaxy collections are already the ones requirements.yml pins: nothing to install."
      exit 0
    fi
    exec "$bin/ansible-galaxy" collection install -r requirements.yml -p "$ANSIBLE_COLLECTIONS_PATH" --no-cache

# A plaintext vault can only be encrypted and an encrypted one only edited, so
# the file says which: the first `just vault` after editing the example
# encrypts it, and every one after that opens it in $EDITOR. The shared vault,
# every host's; --host <name>, a host's own in hosts/, which holds its token -
# reached even where the host lacks what a run needs, since this is how its
# token is put right (the resolver's --lenient).
[arg('ON', long='host', pattern='([a-z0-9][a-z0-9-]{0,30})?', help="That host's own vault, which holds its token, rather than the shared one (just host lists them)")]
[doc('Encrypt the vault, or edit it once it is encrypted')]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
vault ON='':
    {{ guard }}
    on=$1 names= host_lenient=1
    shift
    file={{ quote(VAULT_FILE) }}
    if [ -n "$on" ]; then
      {{ host_env }}
      file=${vault_files##* }
    else
      {{ find_bin }}
    fi
    test -f "$file" || { echo "error: $file missing. Run: just init${on:+ --host $on}" >&2; exit 1; }
    {{ vault_args }}
    if head -c 14 "$file" | grep -q '^\$ANSIBLE_VAULT'; then action=edit; else action=encrypt; fi
    echo "$bin/ansible-vault $action $* $file"
    exec "$bin/ansible-vault" "$action" "$@" "$file"

# Each --only runs the playbook of that stage, as it would run inside the
# whole deploy, with nothing after it: deploy.yml imports each one as it is,
# so the whole is their sum by construction and cannot drift from them. What
# builds or creates acts on the default VM without names; configure, --check
# and ssh-config create nothing, never prompt, and reach every tagged VM.
#
# --tags narrows configuration, and nothing else, so beside VM names it is
# --only configure, and --check takes it too: nothing that builds the template
# or creates a VM is tagged, so --tags on a whole deploy would skip all of that
# and exit 0 having done nothing. Without names, configuration reaches every
# VM while a bare just deploy means the default one, so the fleet takes the
# stage said out loud: an API key pushed with just deploy --tags claude_code
# would otherwise land on every VM, where it outranks the claude.ai login and
# stops Remote Control. Discovery is tagged "always", so it runs whichever
# roles are picked.
#
# A name that is a stage is asked about first: just deploy configure could
# mean either. just deploy test is the VM test.
#
# --cores, --memory and --disk size each VM this run creates in place of the
# vm_cores, vm_memory_mb and vm_disk_size settings, and tag it as sized: its
# size is its own from then on (roles/proxmox_vm). A VM that exists is never
# resized; one given another size is refused. Memory is MiB, or with a suffix
# M or G; disk G or T.
#
# --repos gives each VM named a list of repositories of its own, in place of the
# github_projects setting, kept in the VM's notes in Proxmox, where the VM cannot
# change it, for every later run of it. Another --repos adds to a VM's list, and
# nothing removes a checkout. A VM made without one is refused it.
[arg('START', long='start', value='true', help='Start a VM this run would leave stopped (proxmox_vm_start_existing)')]
[arg('CHECK', long='check', value='check', help='Dry-run configuration: ansible-playbook --check --diff (every tagged VM, unless named)')]
[arg('NAMES', pattern='[A-Za-z0-9][A-Za-z0-9._,-]*', help='The VMs; none means agent-warehouse-default, or every VM this project created for --only configure, --check and --only ssh-config')]
[arg('CORES', long='cores', pattern='([1-9][0-9]{0,2})?', help="Each new VM's vCPU cores, in place of the vm_cores setting")]
[arg('ROLES', long='tags', short='t', pattern='[a-z_,]*', help='Configure only these roles, comma-separated: beside VM names it means --only configure (just deploy alpha --tags claude_code)')]
[arg('DISK', long='disk', pattern='([1-9][0-9]{0,5}[GT])?', help="Each new VM's root disk, as 64G or 1T, in place of the vm_disk_size setting")]
[arg('ON', long='host', pattern='([a-z0-9][a-z0-9-]{0,30})?', help='Run against this host rather than the current one (just host lists them)')]
[arg('MEMORY', long='memory', pattern='([1-9][0-9]{0,6}[MG]?)?', help="Each new VM's memory: MiB, or 16G, in place of the vm_memory_mb setting")]
[arg('STAGE', long='only', pattern='(template|provision|configure|ssh-config)?', help='Run one stage and nothing after it: template (the cloud-init template, if missing), provision (create and start), configure (every tagged VM, unless named) or ssh-config (refresh the SSH aliases; without names, also prune those of VMs that are gone)')]
[arg('REPOS', long='repos', pattern='([A-Za-z0-9_][A-Za-z0-9._-]{0,99}(,[A-Za-z0-9_][A-Za-z0-9._-]{0,99})*)?', help="Only these of your GitHub repositories on each VM named, comma-separated, in place of the github_projects setting: kept for that VM, and added to by a later --repos")]
[continue]
[doc('Create, configure and sign in the VM(s), then open them in your editor')]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
deploy STAGE='' CHECK='' ON='' ROLES='' START='' CORES='' MEMORY='' DISK='' REPOS='' *NAMES:
    {{ guard }}
    stage=$1 check=$2 on=$3 roles=$4 start=$5 cores=$6 memory=$7 disk=$8 repos=$9
    shift 9
    tools/check names "$@"
    tools/check size "$cores" "$memory" "$disk"
    tools/check repos "$repos"
    {{ hyphenate_names }}
    # A stage typed where the names go, by habit from just test unit, would
    # create a VM so named: at a terminal deploy asks which was meant, and
    # without one takes it for neither. Beside --only or --check the stage is
    # said, so the word is a name. The answer meets the rules below as if typed.
    words='' rest=''
    for name in $(printf %s "$names" | tr , ' '); do
      case "$name" in
      {{ stages }}) words="$words${words:+, }$name" ;;
      *) rest="$rest${rest:+,}$name" ;;
      esac
    done
    if [ -n "$words" ] && [ -z "$stage$check" ]; then
      word=${words%%,*}
      case "$word" in check) hint=--check ;; *) hint="--only $word" ;; esac
      case "$words" in
      *,*) meant='' as_vm='VMs so named, each' what='are stages of just deploy and also names VMs could have' ;;
      *) meant=$hint as_vm="a VM named $word," what='is a stage of just deploy and also a name a VM could have' ;;
      esac
      options=${roles:+ --tags $roles}${start:+ --start}${cores:+ --cores $cores}${memory:+ --memory $memory}${disk:+ --disk $disk}${repos:+ --repos $repos}
      if [ ! -t 0 ]; then
        echo "error: $words $what, and there is no terminal to ask which was meant: give a stage as just deploy $hint, or a VM so named beside one, as just deploy --only provision $word" >&2
        exit 1
      fi
      echo "$words $what. Did you mean" >&2
      [ -z "$meant" ] || echo "  s  the stage: just deploy $meant$options${rest:+ $rest}" >&2
      echo "  v  $as_vm created if there is none: just deploy $names$options" >&2
      printf '%s' "${meant:+s or }v, or Enter to run nothing: " >&2
      read -r answer || answer=
      case "$answer" in
      v | V) ;;
      s | S)
        if [ -z "$meant" ]; then
          echo "One stage runs at a time, given with --only: nothing was run." >&2
          exit 1
        fi
        names=$rest
        if [ "$meant" = --check ]; then check=check; else stage=$word; fi ;;
      *) echo "Nothing was run." >&2; exit 1 ;;
      esac
    fi
    if [ -n "$roles" ]; then
      case ",$roles," in *,,*) echo "error: --tags names roles separated by single commas (got: [$roles])" >&2; exit 1 ;; esac
      for role in $(printf %s "$roles" | tr , ' '); do
        case " {{ configure_roles }} " in
        *" $role "*) ;;
        *) echo "error: --tags may name only {{ replace(configure_roles, ' ', ', ') }} (got: [$roles])" >&2; exit 1 ;;
        esac
      done
      case "$stage" in template | provision | ssh-config)
        echo "error: --tags narrows configuration, which --only $stage does not run: just deploy --only configure --tags $roles" >&2
        exit 1 ;;
      esac
      if [ -z "$names" ] && [ -z "$stage$check" ]; then
        echo "error: --tags on its own configures the VMs named beside it: just deploy <name>... --tags $roles, or every VM this project created with just deploy --only configure --tags $roles" >&2
        exit 1
      fi
    fi
    if [ -n "$check" ] && [ -n "$stage" ]; then
      echo "error: --check is a dry run of configuration, which --only $stage is not: just deploy --check" >&2
      exit 1
    fi
    if [ "$stage" = template ] && [ -n "$names" ]; then
      echo "error: just deploy --only template builds the template, which takes no VM names" >&2
      exit 1
    fi
    if [ -n "$start" ] && { [ -n "$check$roles" ] || { [ -n "$stage" ] && [ "$stage" != provision ]; }; }; then
      echo "error: --start starts a VM the run would leave stopped, which only just deploy and just deploy --only provision start" >&2
      exit 1
    fi
    sized=${cores:+ --cores $cores}${memory:+ --memory $memory}${disk:+ --disk $disk}
    if [ -n "$sized" ] && { [ -n "$check$roles" ] || { [ -n "$stage" ] && [ "$stage" != provision ]; }; }; then
      echo "error: --cores, --memory and --disk size a VM the run creates, which only just deploy and just deploy --only provision do" >&2
      exit 1
    fi
    sized=$sized${repos:+ --repos $repos}
    if [ -n "$repos" ] && { [ -n "$check$roles" ] || { [ -n "$stage" ] && [ "$stage" != provision ]; }; }; then
      echo "error: --repos gives a VM its list as it is provisioned, which only just deploy and just deploy --only provision do" >&2
      exit 1
    fi
    {{ host_env }}
    # A VM name is one host's: a deploy that may make the VMs named, which no alias here records, asks every other host
    # for each name first (tools/status --taken: GETs only, each host with its own token), and refuses one found there.
    # A host that does not answer is said, and the run goes on: the alias its next write makes still catches it.
    if [ -n "$hosts_many" ] && [ -n "$names" ] && [ -z "$host_routed" ] && [ -z "$check$roles" ] && { [ -z "$stage" ] || [ "$stage" = provision ]; }; then
      others=$(tools/check host "$bin" every --except="$host" --checkout={{ quote(justfile_directory()) }} --hosts-dir={{ quote(HOSTS_DIR) }} --vault-file={{ quote(VAULT_FILE) }} --vault-pass={{ quote(VAULT_PASS) }})
      {{ vault_args }}
      taken=$("$bin/python" tools/status --taken --hosts "$others" --names "$names" --only vms "$@")
      for name in $(printf %s "$names" | tr , ' '); do
        on_host=$(printf '%s\n' "$taken" | awk -v n="$name" '$1 == "taken" && $2 == n { print $3; exit }')
        [ -z "$on_host" ] || { echo "error: $name is a VM of $on_host already, and a VM name belongs to one host: just deploy $name runs it there, or give this one another name" >&2; exit 1; }
      done
      for unread in $(printf '%s\n' "$taken" | awk '$1 == "unread" { print $2 }'); do
        echo "warning: $unread did not answer, so whether it has a VM of these names is not known: its alias, once written, records it" >&2
      done
    fi
    [ -n "$check" ] || {{ take_lock }} "deploy${stage:+ --only $stage}${roles:+ --tags $roles}$sized${names:+ $(printf %s "$names" | tr , ' ')}${hosts_many:+ --host $host}"
    tools/check vault $vault_files
    tools/check collections "$bin"
    tools/check settings "$bin" $settings_files
    {{ vm_args }}
    [ -z "$start" ] || set -- "$@" -e '{"proxmox_vm_start_existing": true}'
    # The size as JSON, memory in MiB: just held each value to its pattern, and tools/check size again, so each sits
    # whole in it.
    size=
    [ -z "$cores" ] || size="\"cores\": $cores"
    case "$memory" in
    '') ;;
    *G) size="$size${size:+, }\"memory_mb\": $(( ${memory%G} * 1024 ))" ;;
    *M) size="$size${size:+, }\"memory_mb\": ${memory%M}" ;;
    *) size="$size${size:+, }\"memory_mb\": $memory" ;;
    esac
    [ -z "$disk" ] || size="$size${size:+, }\"disk\": \"$disk\""
    [ -z "$size" ] || set -- "$@" -e "{\"vm_size\": {$size}}"
    # The list as JSON: just held it to its pattern, and tools/check repos again, so each name sits whole in it.
    [ -z "$repos" ] || set -- "$@" -e "{\"vm_repos\": [\"$(printf %s "$repos" | sed 's/,/", "/g')\"]}"
    if [ -n "$check" ]; then
      name=deploy-check playbook=playbooks/configure.yml
    elif [ -n "$stage" ]; then
      name=deploy-$stage playbook=playbooks/$(printf %s "$stage" | tr - _).yml
    elif [ -n "$roles" ]; then
      name=deploy-configure playbook=playbooks/configure.yml
    else
      name=deploy playbook=deploy.yml
      {{ no_tty_args }}
    fi
    [ -z "$roles" ] || set -- "$@" --tags "$roles"
    # The live output (callback_plugins/agent_warehouse.py): set, the run is drawn rather than streamed, under
    # this title. Any -v in ANSIBLE_ARGS streams it as before.
    AGENT_WAREHOUSE_LIVE="deploy${check:+ --check}${stage:+ --only $stage}${roles:+ --tags $roles}${start:+ --start}$sized${names:+ $(printf %s "$names" | tr , ' ')}"
    export AGENT_WAREHOUSE_LIVE
    exec {{ run_logged }} "$name" "$bin/ansible-playbook" "$@" {{ ANSIBLE_ARGS }} ${check:+--check --diff} "$playbook"

# One VM at a time: an editor opens one window per workspace, and "open it in
# the editor" does not mean a fleet. --editor opens it in another editor than
# the editor setting names, for this run. --session is one of this machine's Claude
# Code sessions to continue there: its ID, the name of its transcript in
# ~/.claude/projects/, lowercase hex in groups of 8-4-4-4-12 and nothing else.
# Without it the folder's latest conversation continues, as before.
[arg('IDE', long='editor', pattern='(vscode|zed)?', help='Open it in this editor for this run, rather than the one the editor setting names: vscode or zed')]
[arg('NAME', pattern='[A-Za-z0-9][A-Za-z0-9._,-]*', help='The one VM to open')]
[arg('ON', long='host', pattern='([a-z0-9][a-z0-9-]{0,30})?', help='Run against this host rather than the current one (just host lists them)')]
[arg('PROJECT', long='project', pattern='([A-Za-z0-9._-]{1,100})?', help='Open one project folder on the VM rather than the whole projects directory')]
[arg('SESSION', long='session', pattern='([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?', help="Continue one of this machine's Claude Code sessions there, by its ID as /resume lists it: its checkout brought to the session's pushed commit, ready for claude --resume")]
[continue]
[doc("Open a VM in your editor, VS Code by default, with Claude on the VM and this machine's recent Claude sessions copied there")]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
code PROJECT='' IDE='' ON='' SESSION='' *NAME:
    {{ guard }}
    project=$1 ide=$2 on=$3 session=$4
    shift 4
    [ "$#" -gt 0 ] || { echo "error: code opens one VM: just code <name>" >&2; exit 1; }
    tools/check names "$@"
    {{ hyphenate_names }}
    case "$names" in *,*) echo "error: code opens one VM at a time, not [$*]" >&2; exit 1 ;; esac
    tools/check project "$project"
    tools/check session "$session"
    {{ host_env }}
    {{ take_lock }} "code${project:+ --project $project} $names${hosts_many:+ --host $host}"
    tools/check vault $vault_files
    tools/check collections "$bin"
    tools/check settings "$bin" $settings_files
    {{ vm_args }}
    [ -z "$project" ] || set -- "$@" -e "{\"vm_project\": \"$project\"}"
    [ -z "$session" ] || set -- "$@" -e "{\"vm_session\": \"$session\"}"
    [ -z "$ide" ] || set -- "$@" -e "{\"vm_editor\": \"$ide\"}"
    # Drawn live (callback_plugins/agent_warehouse.py, its declaration in lib/claude_vm/run/recipes.py), under
    # this title; a session's ID, which its report names, is left out of it. Any -v in ANSIBLE_ARGS streams it as
    # before.
    AGENT_WAREHOUSE_LIVE="code${project:+ --project $project} $names"
    export AGENT_WAREHOUSE_LIVE
    exec {{ run_logged }} code "$bin/ansible-playbook" "$@" {{ ANSIBLE_ARGS }} playbooks/code.yml

# The sign-ins only the person can make, each a page in the browser: Claude's
# (Remote Control needs a claude.ai login on the VM, and Anthropic supports no
# non-interactive way to create one), GitHub's device flow where the vault has
# no token, and Tailscale's web login where it has no auth key. Given VM names,
# each runs as far as it goes - the page opens here, and the browser comes back
# to the VM or the code is pasted at a prompt - one VM after another, and what
# it leads to follows: Remote Control, the private repositories. Without a name
# it reports every VM's state. Without --only, playbooks/login.yml runs all
# three in just deploy's order and by its rules; just deploy runs them for its
# VMs.
[arg('NAMES', pattern='[A-Za-z0-9][A-Za-z0-9._,-]*', help='The VMs to sign in, one after another; none reports every VM this project created')]
[arg('ON', long='host', pattern='([a-z0-9][a-z0-9-]{0,30})?', help='Run against this host rather than the current one (just host lists them)')]
[arg('SIGN_IN', long='only', pattern='(claude|github|tailscale)?', help='One sign-in: claude (by the browser, then Remote Control), github (the device flow, then the private repositories) or tailscale (its web login)')]
[continue]
[doc('Sign the VM(s) in to Claude, GitHub and Tailscale, or report how each is signed in')]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
login ON='' SIGN_IN='' *NAMES:
    {{ guard }}
    on=$1 only=$2
    shift 2
    tools/check names "$@"
    {{ hyphenate_names }}
    {{ host_env }}
    {{ take_lock }} "login${only:+ --only $only}${names:+ $(printf %s "$names" | tr , ' ')}${hosts_many:+ --host $host}"
    tools/check vault $vault_files
    tools/check collections "$bin"
    tools/check settings "$bin" $settings_files
    {{ vm_args }}
    {{ no_tty_args }}
    # Drawn live (callback_plugins/agent_warehouse.py, its declaration in lib/claude_vm/run/recipes.py), under
    # this title. Any -v in ANSIBLE_ARGS streams it as before.
    AGENT_WAREHOUSE_LIVE="login${only:+ --only $only}${names:+ $(printf %s "$names" | tr , ' ')}"
    export AGENT_WAREHOUSE_LIVE
    exec {{ run_logged }} "login${only:+-$only}" "$bin/ansible-playbook" "$@" {{ ANSIBLE_ARGS }} "playbooks/${only:+${only}_}login.yml"

# What every VM this project created is doing, and what needs doing next, read
# without changing anything: tools/status, no ansible-playbook, so the VMs are
# on screen in a fraction of a second (docs/architecture/lifecycle.md §4.6).
# The engine draws the report live at a terminal and prints it whole at the
# end, the JSON alone on standard output under --json, and exits with the
# report's own code: 0 nothing is wrong, 1 a problem, 2 something could not be
# read, 3 the run itself failed. It keeps what it can work out again in
# STATUS_DIR: the settings, while the files they are made from stay as they
# are, and each VM's upgrades, while its package state does. From ANSIBLE_ARGS
# it takes -e and -i; given -e, it keeps nothing and asks Ansible.
[arg('JSON', long='json', value='true', help='The report as JSON, alone on standard output, with the same exit code')]
[arg('NAMES', pattern='[A-Za-z0-9][A-Za-z0-9._,-]*', help='Only these VMs; none reads every VM this project created')]
[arg('ON', long='host', pattern='([a-z0-9][a-z0-9-]{0,30})?', help='Run against this host rather than the current one (just host lists them)')]
[arg('ONLY', long='only', pattern='((vms|storage|capacity|access|sign-ins|system|work)(,(vms|storage|capacity|access|sign-ins|system|work))*)?', help='Only these sections, comma-separated: vms storage capacity access sign-ins system work; vms, storage and capacity read only the Proxmox API, never a VM')]
[doc('What every VM this project created is doing, and what needs doing next, read without changing anything')]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
status ON='' ONLY='' JSON='' *NAMES:
    {{ guard }}
    on=$1 only=$2 json=$3
    shift 3
    tools/check names "$@"
    tools/check sections "$only"
    {{ hyphenate_names }}
    # More than one host, and neither one nor a VM named: every host, each read by a child of tools/status's own
    # (lib/claude_vm/status/hosts.py), each host's settings and upgrades kept in a folder of its own. A host that cannot
    # be read - not set up, a token in the wrong vault - is a block of its own, never the others' refusal.
    if [ -z "$on" ] && [ -z "$names" ] && [ -n "$(find {{ quote(HOSTS_DIR) }} -mindepth 1 -maxdepth 1 -type d ! -name '.*' 2>/dev/null)" ]; then
      {{ find_bin }}
      hosts_said=$(tools/check host "$bin" every --checkout={{ quote(justfile_directory()) }} --hosts-dir={{ quote(HOSTS_DIR) }} --vault-file={{ quote(VAULT_FILE) }} --vault-pass={{ quote(VAULT_PASS) }})
      tools/check settings "$bin" {{ SETTINGS_FILES }}
      {{ vault_args }}
      set -- "$@" --hosts "$hosts_said" --cache {{ quote(absolute_path(STATUS_DIR)) }}
    else
      {{ host_env }}
      tools/check vault $vault_files
      tools/check settings "$bin" $settings_files
      {{ vault_args }}
      for file in $vault_files; do set -- "$@" --vault-file "$file"; done
      # One host's, from a checkout of more: its settings and upgrades kept apart from the others'. A host in hosts/ is
      # read through its sources as -i, so that its files key the settings kept.
      set -- "$@" --cache {{ quote(absolute_path(STATUS_DIR)) }}${hosts_many:+/$host}
      for source in $(printf %s "$host_sources" | tr , ' '); do set -- "$@" -i "$source"; done
    fi
    [ -z "$names" ] || set -- "$@" --names "$names"
    [ -z "$only" ] || set -- "$@" --only "$only"
    [ -z "$json" ] || set -- "$@" --json
    STATUS_QUIET=$json exec {{ run_logged }} status "$bin/python" tools/status "$@" {{ ANSIBLE_ARGS }}

# Each setting's value and where it was set (tools/settings): Ansible's own
# variables for a play of the role, on the group its playbook runs it on, with
# what a run sets beside them. A secret is said set or not set, never shown,
# and changes with just vault. Read without changing anything, but for
# --value, which writes the one setting named into local.yml, and --reset,
# which takes it out again - never a secret.
# How to use it needs no vault, so it comes before the vault's check; the
# rules before any Python. The vault's password file is the one every recipe
# passes. A value is free text, refused only for a control character, so just
# never says it back: it reaches the script as a positional argument, and the
# tool holds it to the setting's type. No refusal here says the words given,
# in case a secret was typed as one.
[arg('ALL', long='all', value='true', help='Every setting of local.yml, and which secrets of vault.yml are set')]
[arg('JSON', long='json', value='true', help='The report as JSON, alone on standard output')]
[arg('NAME', pattern='[a-z][a-z0-9_]*', help='A role (tailscale) or one variable or setting (tailscale_ssh); none shows how to use this')]
[arg('RESET', long='reset', value='true', help="Put the one setting named back to its default: its line out of local.yml, its example's back")]
[arg('CHANGED', long='changed', value='true', help='Only what local.yml or vault.yml changes from the defaults; alone, all of it')]
[arg('VALUE', long='value', pattern='[^\x00-\x1f\x7f]*', help='Set the one setting named to this value, in local.yml (never a secret: just vault; an empty value is none: --reset)')]
[arg('ON', long='host', pattern='([a-z0-9][a-z0-9-]{0,30})?', help="That host's values rather than the current host's; beside --value or --reset, its own local.yml is the one changed")]
[doc("Each setting's value and where it was set, for a role, a variable or all of them; --value sets one in local.yml, --reset puts it back")]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
settings ALL='' CHANGED='' JSON='' ON='' RESET='' VALUE='' *NAME:
    {{ guard }}
    all=$1 changed=$2 json=$3 on=$4 reset=$5 value=$6
    shift 6
    [ "$#" -le 1 ] || { echo "error: just settings takes one role or variable, and --value its value" >&2; exit 1; }
    [ -z "$all" ] || [ "$#" -eq 0 ] || { echo "error: just settings --all lists every setting: give it no role or variable" >&2; exit 1; }
    [ -z "$reset$value" ] || { [ "$#" -eq 1 ] && [ -z "$all$changed" ]; } || { echo "error: --value and --reset each change the one setting named beside it: just settings <name> --value <value>, or --reset" >&2; exit 1; }
    [ -z "$reset" ] || [ -z "$value" ] || { echo "error: --value sets a value and --reset takes one out: one or the other" >&2; exit 1; }
    {{ find_bin }}
    if [ "$#" -eq 0 ] && [ -z "$all$changed" ]; then exec "$bin/python" tools/settings --usage --json="$json"; fi
    # The run's host, as every recipe's, but taken where it lacks a setting it sets for itself: this is how that is
    # put right.
    names= host_lenient=1
    {{ host_env }}
    tools/check vault $vault_files
    tools/check collections "$bin"
    tools/check settings "$bin" $settings_files
    name=${1:-}
    {{ vault_args }}
    # A host in hosts/ is read over the shared inventory, its own files a layer each; written only when named.
    if [ -n "$host_sources" ]; then
      set -- "$@" --host-folder="${host_sources##*,}"
      [ -z "$on" ] || set -- "$@" --write-host=true
    fi
    [ -z "$value" ] || set -- "$@" --value="$value"
    [ -z "$reset" ] || set -- "$@" --reset=true
    exec "$bin/python" tools/settings "$@" --all="$all" --changed="$changed" --json="$json" -- ${name:+"$name"}

# --confirm answers the deletion's question (CONFIRM DELETION) for the VMs it
# names, and only when they are the VMs given: compared as a set, after the
# same hyphenation. The MCP server passes it once the person confirmed in
# Claude Code; a person may too, in place of ANSIBLE_ARGS='-e
# confirm_destroy=yes', which no name bound.
[arg('NAMES', pattern='[A-Za-z0-9][A-Za-z0-9._,-]*', help='The VMs; none means agent-warehouse-default')]
[arg('ON', long='host', pattern='([a-z0-9][a-z0-9-]{0,30})?', help='Run against this host rather than the current one (just host lists them)')]
[arg('CONFIRM', long='confirm', pattern='([A-Za-z0-9][A-Za-z0-9._,-]*)?', help='Delete without asking, these VMs only: the same names as given, comma-separated')]
[continue]
[doc("Save the VM(s)' own Claude sessions here, release them from Claude, GitHub and Tailscale, then delete them")]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
destroy CONFIRM='' ON='' *NAMES:
    {{ guard }}
    confirm=$1 on=$2
    shift 2
    tools/check names "$@"
    {{ hyphenate_names }}
    if [ -n "$confirm" ]; then
      [ -n "$names" ] || { echo "error: --confirm answers for the VMs named beside it: just destroy --confirm=<names> <names>" >&2; exit 1; }
      tools/check names "$confirm"
      set_of() { printf %s "$1" | tr _ - | tr , '\n' | sort -u | tr '\n' ,; }
      [ "$(set_of "$confirm")" = "$(set_of "$names")" ] || { echo "error: --confirm names $confirm, but the VMs given are $names: nothing was deleted" >&2; exit 1; }
    fi
    {{ host_env }}
    {{ take_lock }} "destroy${names:+ $(printf %s "$names" | tr , ' ')}${hosts_many:+ --host $host}"
    tools/check vault $vault_files
    tools/check collections "$bin"
    tools/check settings "$bin" $settings_files
    {{ vm_args }}
    [ -z "$confirm" ] || set -- "$@" -e '{"confirm_destroy": "yes"}'
    # Drawn live (callback_plugins/agent_warehouse.py, its declaration in lib/claude_vm/run/recipes.py), under
    # this title. Any -v in ANSIBLE_ARGS streams it as before.
    AGENT_WAREHOUSE_LIVE="destroy${names:+ $(printf %s "$names" | tr , ' ')}"
    export AGENT_WAREHOUSE_LIVE
    exec {{ run_logged }} destroy "$bin/ansible-playbook" "$@" {{ ANSIBLE_ARGS }} playbooks/destroy.yml

# The checkout's hosts (tools/check host): the first in inventory/, each other
# in a folder of its own under hosts/ (HOSTS_DIR), made by just init --host.
# Without a name, the list, from the files alone: nothing is asked of a host.
# With one, that host becomes the current one, hosts/current written anew and
# renamed over the old, under the run lock, so a run under way finishes on the
# host it started on. --remove deletes a host's folder, after its name is typed
# at a terminal: never the current host's or the first's, and never its VMs,
# which stay on it with their aliases here.
[arg('JSON', long='json', value='true', help='The list as JSON, alone on standard output')]
[arg('REMOVE', long='remove', value='true', help="Delete the host's folder in hosts/, after you type its name: its VMs and their SSH aliases stay")]
[arg('NAME', pattern='[a-z0-9][a-z0-9-]{0,30}', help='The host to make the current one: every command runs against it from now on')]
[doc('List the hosts, or make one the current host, which every command runs against unless --host names another')]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
host JSON='' REMOVE='' *NAME:
    {{ guard }}
    json=$1 remove=$2
    shift 2
    [ "$#" -le 1 ] || { echo "error: just host takes one host's name (got: [$*])" >&2; exit 1; }
    [ -z "$json" ] || [ "$#" -eq 0 ] || { echo "error: --json lists the hosts: give it no name" >&2; exit 1; }
    [ -z "$remove" ] || [ "$#" -eq 1 ] || { echo "error: --remove deletes the host named beside it: just host <name> --remove" >&2; exit 1; }
    if [ -n "$remove" ] && [ ! -t 0 ]; then
      echo "error: just host --remove asks you to type the host's name, and there is no terminal to ask at" >&2
      exit 1
    fi
    {{ find_bin }}
    set -- "${1:-}" --checkout={{ quote(justfile_directory()) }} --hosts-dir={{ quote(HOSTS_DIR) }}
    if [ -z "$1" ]; then exec tools/check host "$bin" list "$2" "$3" ${json:+--json}; fi
    {{ take_lock }} "host $1${remove:+ --remove}"
    if [ -z "$remove" ]; then exec tools/check host "$bin" switch "$@"; fi
    printf 'Delete %s/%s/, its settings and its token here? Its VMs stay. Type %s to delete it: ' {{ quote(HOSTS_DIR) }} "$1" "$1" >&2
    read -r answer || answer=
    [ "$answer" = "$1" ] || { echo "Nothing was deleted." >&2; exit 1; }
    exec tools/check host "$bin" remove "$@"

# Only files named the way tools/run-logged names its logs, and only in
# LOG_DIR, which as a justfile setting goes into the script's text rather than its
# environment, so `just LOG_DIR=<dir> logs` reads that directory.
[doc('List the run logs in .logs/, newest first')]
[group('use')]
[no-exit-message]
[script]
logs:
    {{ guard }}
    dir={{ quote(LOG_DIR) }}
    if [ -z "$dir" ]; then echo "LOG_DIR is empty: run logs are off."; exit 0; fi
    set -- "$dir"/[0-9]*-*.log
    if [ -e "$1" ]; then ls -1t "$@" | head -20; else echo "No run logs in $dir."; fi

# Registers tools/mcp, the MCP server through which a Claude Code session in any
# project runs these recipes: `claude mcp add --scope user agent-warehouse`
# (local scope, that one project's, with --project-dir), Claude Code's own CLI
# writing ~/.claude.json. Every value the server needs is an argument of the
# command it records - this checkout, its environment's Python, this just and
# uv, and this shell's PATH, HOME, XDG_DATA_HOME and CLAUDE_CONFIG_DIR - never
# an environment variable, which a project's .claude/settings.json could set. The checks first: claude new
# enough, main and clean (but --any-branch), a vault readable with no terminal,
# the MCP SDK in the environment. The entry is read back with claude mcp get,
# since before Claude Code 2.1.318 an add could be lost to another session's
# save of the same file; --show prints the command and registers nothing.
[arg('SHOW', long='show', value='true', help='Print the command, and an entry for Claude Desktop, and register nothing')]
[arg('EXTRA', pattern='[^\x00-\x1f\x7f]*', help='Nothing: any word after the options is refused')]
[arg('REMOVE', long='remove', value='true', help='Take the server out of Claude Code again')]
[arg('SCREEN', long='attended', pattern='(0|1)?', help="Whether someone is at this machine's screen for the sign-ins: 1, as for Claude Desktop, or 0; unset, the server decides per session")]
[arg('ANY_BRANCH', long='any-branch', value='true', help='Let the server run this checkout on any branch, or with tracked changes; without it, main with no tracked change only')]
[arg('BROWSER', long='browser', pattern='[^\x00-\x1f\x7f]*', help='What opens the sign-in pages for the runs it starts, when the default does not (BROWSER)')]
[arg('PROJECT_DIR', long='project-dir', pattern='([/~][^\x00-\x1f\x7f]*)?', help="Register it for this one project's folder only (Claude Code's local scope), not for every project")]
[arg('VAULT_PASSWORD_FILE', long='vault-password-file', pattern='([/~][^\x00-\x1f\x7f]*)?', help="Where the vault's password is, when it is not this checkout's .vault_pass")]
[doc('Let Claude Code sessions in any project run these commands, through an MCP server')]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
mcp ANY_BRANCH='' SCREEN='' BROWSER='' PROJECT_DIR='' REMOVE='' SHOW='' VAULT_PASSWORD_FILE='' *EXTRA:
    {{ guard }}
    any_branch=$1 attended=$2 browser=$3 project_dir=$4 remove=$5 show=$6 vault_password_file=$7
    shift 7
    [ "$#" -eq 0 ] || { echo "error: just mcp takes only its options (just help mcp), and nothing after them" >&2; exit 1; }
    [ -z "$remove" ] || [ -z "$show" ] || { echo "error: --remove takes the server out and --show prints how it is added: one or the other" >&2; exit 1; }
    case "$project_dir" in "~") project_dir=$HOME ;; "~/"*) project_dir=$HOME/${project_dir#"~/"} ;; esac
    case "$vault_password_file" in "~") vault_password_file=$HOME ;; "~/"*) vault_password_file=$HOME/${vault_password_file#"~/"} ;; esac
    name=agent-warehouse
    scope=user
    if [ -n "$project_dir" ]; then
      [ -d "$project_dir" ] || { echo "error: --project-dir $project_dir is not a folder" >&2; exit 1; }
      # Claude Code keeps a project's entries under its repository's root: run from below it, one before 2.1.311
      # named another project's entry.
      project_dir=$(git -C "$project_dir" rev-parse --show-toplevel 2>/dev/null || (cd "$project_dir" && pwd -P))
      scope=local
    fi
    [ -z "$vault_password_file" ] || [ -f "$vault_password_file" ] || { echo "error: --vault-password-file $vault_password_file is not a file" >&2; exit 1; }
    claude=$(command -v claude || true)
    [ -n "$claude" ] || [ -n "$show" ] || { echo "error: no claude on PATH: install Claude Code first, then run just mcp again" >&2; exit 1; }
    if [ -n "$remove" ]; then
      [ "$scope" = user ] || cd "$project_dir"
      "$claude" mcp remove --scope "$scope" "$name"
      exit 0
    fi
    {{ find_bin }}
    python=$bin/python
    "$python" -I -c 'import mcp.server.mcpserver' 2> /dev/null || { echo "error: the environment in $bin has no MCP SDK. Run: just install" >&2; exit 1; }
    if [ -n "$claude" ]; then
      version=$("$claude" --version 2> /dev/null | awk 'NR == 1 { print $1 }')
      "$python" -I -c 'import sys; v = [int(p) for p in sys.argv[1].split(".")]; sys.exit(v < [2, 1, 309])' "$version" 2> /dev/null \
        || { echo "error: Claude Code ${version:-of unknown version} is older than 2.1.309, the oldest this server was tried with. Run: claude update" >&2; exit 1; }
    fi
    if [ -z "$any_branch" ]; then
      branch=$(git rev-parse --abbrev-ref HEAD)
      if [ "$branch" != main ] || [ -n "$(git status --porcelain --untracked-files=no)" ]; then
        echo "error: this checkout is on $branch$( [ -z "$(git status --porcelain --untracked-files=no)" ] || echo ', with tracked changes'): the server runs only main with no tracked change, since the settings and the vault are this checkout's. Switch back to main, or allow any branch: just mcp --any-branch" >&2
        exit 1
      fi
    fi
    if head -c 14 {{ quote(VAULT_FILE) }} 2> /dev/null | grep -q '^\$ANSIBLE_VAULT' && [ ! -f {{ quote(VAULT_PASS) }} ] && [ -z "$vault_password_file" ]; then
      echo "error: the vault is encrypted and there is no {{ VAULT_PASS }}, so a run the server starts, with no terminal, could not read it. just init makes one; or name the file: just mcp --vault-password-file <file>" >&2
      exit 1
    fi
    uv=$(command -v {{ quote(UV) }} || echo {{ quote(UV) }})
    set -- "$python" -I {{ quote(justfile_directory() / 'tools/mcp') }} --checkout={{ quote(justfile_directory()) }} --python="$python" --just={{ quote(just_executable()) }} --uv="$uv" --path="$PATH" --home="$HOME"
    [ -z "${XDG_DATA_HOME:-}" ] || set -- "$@" --data-home="$XDG_DATA_HOME"
    [ -z "${CLAUDE_CONFIG_DIR:-}" ] || set -- "$@" --claude-config-dir="$CLAUDE_CONFIG_DIR"
    [ -z "$attended" ] || set -- "$@" --attended="$attended"
    [ -z "$any_branch" ] || set -- "$@" --any-branch
    [ -z "$vault_password_file" ] || set -- "$@" --vault-password-file="$vault_password_file"
    [ -z "$browser" ] || set -- "$@" --browser="$browser"
    if [ -n "$show" ]; then
      printf 'claude mcp add --scope %s --transport stdio %s --' "$scope" "$name"
      printf ' %q' "$@"
      printf '\n\nFor Claude Desktop, in claude_desktop_config.json (a person is at its screen, so --attended=1):\n'
      "$python" -I -c 'import json, sys; args = sys.argv[2:]; args += [] if any(a.startswith("--attended=") for a in args) else ["--attended=1"]; print(json.dumps({"mcpServers": {"agent-warehouse": {"command": sys.argv[1], "args": args}}}, indent=2))' "$@"
      exit 0
    fi
    [ "$scope" = user ] || cd "$project_dir"
    # A registration of this name in this scope, this checkout's or another's, is replaced. mcp get finds one in any
    # scope and mcp remove only in the one it is told, so one in another scope stays, and is not said to be replaced.
    if "$claude" mcp get "$name" > /dev/null 2>&1 && "$claude" mcp remove --scope "$scope" "$name" > /dev/null 2>&1; then
      replaced=yes
    fi
    # Claude Code answers with the whole command it added, every directory of the PATH among it (--show prints that):
    # its words are said only if the add fails.
    added=$("$claude" mcp add --scope "$scope" --transport stdio "$name" -- "$@" 2>&1) || { printf '%s\n' "$added" >&2; exit 1; }
    "$claude" mcp get "$name" > /dev/null 2>&1 \
      || { echo "error: claude mcp add said it added $name, but claude mcp get does not find it: another Claude Code session may have saved ~/.claude.json at the same moment. Run just mcp again." >&2; exit 1; }
    echo "The $name MCP server runs {{ justfile_directory() }}${replaced:+, in place of the one registered before}, for $( [ "$scope" = user ] && echo 'every project' || echo "$project_dir" ). Sessions started from now on have it; /mcp in one shows it."
    echo
    echo "Claude Code's permission rules decide what runs unasked: without an ask rule, auto mode alone decides whether"
    echo "an agent deploys, signs in or opens a VM. These let the tools that only read run, ask before the rest, and ask"
    echo "before an agent runs just destroy or just settings itself through Bash; add them to ~/.claude/settings.json:"
    echo
    echo '  "permissions": {'
    echo '    "allow": ["mcp__agent-warehouse__status", "mcp__agent-warehouse__settings", "mcp__agent-warehouse__logs",'
    echo '              "mcp__agent-warehouse__log", "mcp__agent-warehouse__jobs", "mcp__agent-warehouse__job"],'
    echo '    "ask": ["mcp__agent-warehouse__deploy", "mcp__agent-warehouse__login", "mcp__agent-warehouse__code",'
    echo '            "mcp__agent-warehouse__cancel", "Bash(just destroy:*)", "Bash(just settings:*)"]'
    echo '  }'

# ------------------------------------------------------------------ develop ----
# Every linter, in the order of their speed; the hooks last, since they can
# fix what they find - whitespace, the wiki's generated pages - and then fail.
# The first run of the hooks fetches the repositories .pre-commit-config.yaml
# names. The justfile's own formatting is checked only by the just CI pins,
# since another version may format it otherwise; lint says so when it skips.
[arg('ALL_HOOKS', long='all-hooks', value='yes', help="hooks: run every pre-commit hook, the ones lint's other parts cover too")]
[arg('PARTS', pattern='lock|yaml|ansible|python|just|syntax|hooks', help='lock (uv.lock against pyproject.toml), yaml (yamllint --strict), ansible (ansible-lint, production profile), python (ruff check and format --check), just (just --fmt --check), syntax (--syntax-check on every playbook), hooks (the pre-commit hooks the others do not cover)')]
[doc('Every linter: the lock, YAML, Ansible, Python, the justfile, playbook syntax, pre-commit hooks')]
[group('develop')]
[no-exit-message]
[positional-arguments]
[script]
lint ALL_HOOKS='' *PARTS:
    {{ guard }}
    all_hooks=$1
    shift
    parts=" $* "
    runs() { [ "$parts" = "  " ] || case "$parts" in *" $1 "*) ;; *) return 1 ;; esac; }
    {{ find_bin }}
    # The parts that load the playbooks, ansible-lint among the hooks under --all-hooks: without the collections the
    # pins name they fail on a module they cannot resolve, rather than naming just install.
    if runs ansible || runs syntax || { runs hooks && [ -n "$all_hooks" ]; }; then tools/check collections "$bin"; fi
    # Drawn live, as a run in parts (tools/live), unless it is a stage of another recipe's run (live_run): its title,
    # the state its checks share, and a trap that says the run's end however it ends, so no check is exec'd. Each
    # check is a command through tools/live, which passes it through unchanged where nothing is drawn.
    if [ -z {{ quote(live_run) }} ]; then
      AGENT_WAREHOUSE_LIVE="lint${all_hooks:+ --all-hooks}${*:+ $*}"
      state={{ quote(live_state) }}
      AGENT_WAREHOUSE_LIVE_STATE=${state:+$state-lint-$$.json}
      export AGENT_WAREHOUSE_LIVE AGENT_WAREHOUSE_LIVE_STATE
      live_end() { {{ live }} end "$1" || :; }
      trap 'rc=$?; live_end "$rc"; exit "$rc"' EXIT
    fi
    plan=
    # The checks in the order they run, the line broken after "just" so that no line reads as a just command; the
    # justfile's format only where it is checked, by the just CI pins - a step listed and never run reads as a stop.
    for part in lock yaml ansible python just \
      syntax hooks; do
      if [ "$part" = just ] && [ {{ quote(just_version()) }} != {{ quote(just_pin) }} ]; then continue; fi
      if runs "$part"; then plan="$plan $part"; fi
    done
    check() {
      step=$1
      shift
      {{ live }} run lint --step "$step" --plan "$plan" -- "$@"
    }
    if runs lock; then check lock env UV_PYTHON_DOWNLOADS=never {{ quote(UV) }} lock --check; fi
    if runs yaml; then check yaml "$bin/yamllint" --strict .; fi
    if runs ansible; then check ansible "$bin/ansible-lint"; fi
    if runs python; then
      check python "$bin/ruff" check .
      check python "$bin/ruff" format --check .
    fi
    if runs just; then
      if [ {{ quote(just_version()) }} = {{ quote(just_pin) }} ]; then
        check just {{ quote(just_executable()) }} --justfile {{ quote(justfile()) }} --fmt --check
      else
        echo "just: not checked: the justfile's format is checked only by just {{ just_pin }}, the one CI runs, and this is {{ just_version() }}"
      fi
    fi
    if runs syntax; then
      # One check over every playbook, each said as it starts: the first that fails ends it.
      check syntax bash -c 'for pb in deploy.yml playbooks/*.yml; do echo "== $pb"; "$1/ansible-playbook" --syntax-check "$pb" || exit; done' syntax "$bin"
    fi
    if runs hooks; then
      absbin=$(cd "$bin" && pwd -P)
      SKIP={{ quote(pre_commit_skip) }}
      [ -z "$all_hooks" ] || SKIP=
      check hooks env SKIP="$SKIP" PATH="$absbin:$PATH" "$bin/pre-commit" run --all-files
    fi

# Molecule resolves ansible-playbook from PATH (and only appends the
# environment), so the environment's bin must come first or a system-wide
# Ansible would be used instead of the pinned one. One entry point for every
# test, so CI runs exactly what a developer runs.
#
# Without parts this is the local quality gate: lint first, which includes
# pre-commit's hooks, so a local pass covers the hooks CI runs too. Not a full
# superset of CI: CI also runs a gitleaks scan over the working tree and the
# commits a push brings in, which a local `just test` cannot reproduce from the
# index alone. The Molecule scenarios each run a Docker container and build
# images into one small disk, so they run one after another - but with --jobs,
# for a machine with the room, which runs them that many at a time
# (tools/molecule-lanes): every scenario took 28 minutes one after another and
# 7.5 four at a time on an 8-core runner.
[arg('JOBS', long='jobs', pattern='[1-9]?', help="Run the Molecule scenarios this many at a time, the longest first, each one's whole output in a log of its own and a line as each ends; one at a time without it, as their images share one disk (just test molecule --jobs 4)")]
[arg('ROLES', long='role', pattern='[a-z_,]*', help="Only these roles' scenarios, comma-separated: on its own it means the role part (just test --role common,claude_code)")]
[arg('SHARD', long='shard', pattern='([1-9][0-9]*/[1-9][0-9]*)?', help='Only the I-th of N shards of the unit tests, each group of tests whole, as CI splits them: on its own it means the unit part (just test unit --shard 2/3)')]
[arg('PARTS', pattern='unit|molecule|role|integration|provision', help="unit (pytest over tests/unit/, a worker per CPU, no network), molecule (every Molecule scenario: role, integration, provision), role (the roles' own scenarios), integration (the full configure playbook against a Docker container), provision (provision.yml and discover.yml against a fake Proxmox API)")]
[doc('Lint, then every test: unit and Molecule (the local quality gate)')]
[group('develop')]
[no-exit-message]
[positional-arguments]
[script]
test ROLES='' SHARD='' JOBS='' *PARTS:
    {{ guard }}
    roles=$1 shard=$2 jobs=$3
    shift 3
    # The words as typed, for the run's title: --role and --shard below stand for the parts they choose.
    asked=$*
    if [ -n "$shard" ]; then
      [ "${shard%/*}" -le "${shard#*/}" ] || { echo "error: --shard takes I/N with I no more than N (got: [$shard])" >&2; exit 1; }
      [ -z "$roles" ] || { echo "error: --shard splits the unit tests and --role narrows the role scenarios: one at a time" >&2; exit 1; }
      for part in "$@"; do
        [ "$part" = unit ] || { echo "error: --shard splits the unit tests, which $part is not: just test unit --shard $shard" >&2; exit 1; }
      done
      set -- unit
    fi
    if [ -n "$roles" ]; then
      case ",$roles," in *,,*) echo "error: --role names roles separated by single commas (got: [$roles])" >&2; exit 1 ;; esac
      for role in $(printf %s "$roles" | tr , ' '); do
        case " {{ scenario_roles }} " in
        *" $role "*) ;;
        *) echo "error: --role may name only {{ replace(scenario_roles, ' ', ', ') }} (got: [$roles])" >&2; exit 1 ;;
        esac
      done
      for part in "$@"; do
        [ "$part" = role ] || { echo "error: --role narrows the role scenarios, which $part does not run: just test --role $roles" >&2; exit 1; }
      done
      set -- role
    fi
    if [ "$#" -eq 0 ]; then name=test lint=yes; set -- unit role integration provision; else name="test-$(IFS=-; printf %s "$*")" lint=; fi
    parts=" $* "
    case "$parts" in *" molecule "*) parts="$parts role integration provision " ;; esac
    runs() { case "$parts" in *" $1 "*) ;; *) return 1 ;; esac; }
    if [ -n "$jobs" ] && ! runs role && ! runs integration && ! runs provision; then
      echo "error: --jobs runs Molecule scenarios side by side, and unit has none: just test molecule --jobs $jobs" >&2
      exit 1
    fi
    # The scenarios --jobs runs side by side, the longest first: the parts' own, where that is more than one.
    lanes=
    if [ "${jobs:-1}" -gt 1 ]; then
      chosen={{ quote(scenario_roles) }}
      [ -z "$roles" ] || chosen=$(printf %s "$roles" | tr , ' ')
      for scenario in {{ molecule_order }}; do
        case "$scenario" in
        configure | image) part=integration ;;
        provision) part=provision ;;
        *) part=role; case " $chosen " in *" $scenario "*) ;; *) continue ;; esac ;;
        esac
        if runs "$part"; then lanes="$lanes $scenario"; fi
      done
      # shellcheck disable=SC2086 # the scenarios' names, one word each
      set -- $lanes
      [ "$#" -gt 1 ] || lanes=
    fi
    {{ find_bin }}
    tools/check collections "$bin"
    absbin=$(cd "$bin" && pwd -P)
    # The roles' parts run in their directories: every tool, and tools/live's Python, by its absolute path.
    bin=$absbin
    # Drawn live, as a run in parts (tools/live), unless it is a stage of another recipe's run (live_run): its title,
    # the state its parts share, the stages it will run, and a trap that says the run's end however it ends, so no
    # part is exec'd. Each part is a command through tools/live, which passes it through as it ran before - logged
    # under the run's name - where nothing is drawn; lint draws into the run as its first stage.
    if [ -z {{ quote(live_run) }} ]; then
      stages=
      [ -z "$lint" ] || stages=lint
      for part in unit role integration provision; do
        if runs "$part" && { [ -z "$lanes" ] || [ "$part" = unit ]; }; then stages="$stages $part"; fi
      done
      [ -z "$lanes" ] || stages="$stages molecule"
      AGENT_WAREHOUSE_LIVE="test${roles:+ --role $roles}${shard:+ --shard $shard}${asked:+ $asked}"
      state={{ quote(live_state) }}
      AGENT_WAREHOUSE_LIVE_STATE=${state:+$state-test-$$.json}
      AGENT_WAREHOUSE_LIVE_STAGES=$stages
      export AGENT_WAREHOUSE_LIVE AGENT_WAREHOUSE_LIVE_STATE AGENT_WAREHOUSE_LIVE_STAGES
      live_end() { {{ live }} --log="$name" end "$1" || :; }
      trap 'rc=$?; live_end "$rc"; exit "$rc"' EXIT
    fi
    part() { {{ live }} --log="$name" run "$@"; }
    [ -z "$lint" ] || {{ self }} live_run=test lint
    # A worker per CPU, the tests that share a fixture's run kept on one (tests/unit/conftest.py); tests/unit
    # named, so that conftest.py, which adds --shard, is read before the options are.
    if runs unit; then
      part unit --read pytest -- "$bin/pytest" -p live_pytest tests/unit -n auto --dist loadgroup ${shard:+--shard "$shard"}
    fi
    if [ -n "$lanes" ]; then
      # shellcheck disable=SC2086 # the scenarios' names, one word each
      part molecule --read plain -- {{ quote(justfile_directory() / 'tools/molecule-lanes') }} "$jobs" "$absbin" \
        {{ quote(if LOG_DIR == '' { '' } else { absolute_path(LOG_DIR) }) }} {{ quote(stamp) }} $lanes
      exit
    fi
    if runs role; then
      list={{ quote(scenario_roles) }}
      [ -z "$roles" ] || list=$(printf %s "$roles" | tr , ' ')
      # Each role's scenario says which it is first, as a line of its own output: on the screen as it runs where it is
      # passed through, in the log beside the row that names it where it is drawn.
      for role in $list; do
        (cd "roles/$role" && part role --step "$role" --read molecule -- bash -c 'echo "===== molecule: $1" && shift && exec "$@"' \
          molecule "$role" env PATH="$absbin:$PATH" "$absbin/molecule" test)
      done
    fi
    if runs integration; then
      part integration --step configure --read molecule -- env PATH="$absbin:$PATH" "$absbin/molecule" test -s configure
      part integration --step image --read molecule -- env PATH="$absbin:$PATH" "$absbin/molecule" test -s image
    fi
    if runs provision; then
      part provision --step provision --read molecule -- env PATH="$absbin:$PATH" "$absbin/molecule" test -s provision
    fi

# The wiki in docs/ (docs/development/docs.md). Its reference pages are
# generated from the files they describe - this justfile's recipes among them,
# through `just --json` - and tests/unit/test_docs.py, part of `just test
# unit`, fails when one is out of date or a link is broken.
[arg('PART', pattern='serve|build', help='serve (the wiki as a site at http://127.0.0.1:8000, rebuilt as you save) or build (the site in site/, failing on a broken link or anchor)')]
[doc("Regenerate the wiki's reference pages from the files they describe")]
[group('develop')]
[no-exit-message]
[positional-arguments]
[script]
docs *PART:
    {{ guard }}
    [ "$#" -le 1 ] || { echo "error: just docs takes one part, serve or build (got: [$*])" >&2; exit 1; }
    {{ find_bin }}
    case "${1:-}" in
    serve) exec "$bin/zensical" serve -a 127.0.0.1:8000 ;;
    build) exec "$bin/zensical" build --strict --clean ;;
    *)
      export JUST={{ quote(just_executable()) }}
      exec "$bin/python" tools/docs.py ;;
    esac

# The Vagrantfile's Ansible provisioner runs the ansible-playbook it is given
# here, the environment's, rather than the first one on PATH.
[arg('PART', pattern='destroy', help='destroy: destroy the Vagrant test VM')]
[doc('End-to-end test of configure.yml on a VirtualBox VM')]
[group('develop')]
[no-exit-message]
[positional-arguments]
[script]
vagrant *PART:
    {{ guard }}
    [ "$#" -le 1 ] || { echo "error: just vagrant takes one part, destroy (got: [$*])" >&2; exit 1; }
    if [ "${1:-}" = destroy ]; then exec vagrant destroy -f; fi
    {{ find_bin }}
    tools/check collections "$bin"
    export ANSIBLE_PLAYBOOK="$bin/ansible-playbook"
    exec {{ run_logged }} vagrant vagrant up --provision

# Only files named the way tools/run-logged names its logs, and only in
# LOG_DIR.
[arg('PART', pattern='logs|env', help="logs: delete only the run logs in LOG_DIR; env: delete only this checkout's own environment and collections, which just install makes again")]
[doc('Remove caches, the run logs and the built site (never your Python environment)')]
[group('develop')]
[no-exit-message]
[positional-arguments]
[script]
clean *PART:
    {{ guard }}
    [ "$#" -le 1 ] || { echo "error: just clean takes one part, logs or env (got: [$*])" >&2; exit 1; }
    part=${1:-}
    if [ "$part" = env ]; then
      # Only this checkout's own folder outside it, as tools/env-run names it, and its name through 1.x if one is
      # left: never a virtualenv of yours.
      data=$(tools/env-run --data)
      legacy=$(tools/env-run --legacy-data)
      case "$data" in */agent-warehouse/checkouts/?*) ;; *) echo "error: not this checkout's own folder: [$data]" >&2; exit 1 ;; esac
      case "$legacy" in */claude-on-proxmox/checkouts/?*) ;; *) echo "error: not this checkout's own folder: [$legacy]" >&2; exit 1 ;; esac
      if [ -d "$legacy" ]; then
        rm -rf "$legacy"
        echo "Deleted this checkout's own folder by its name through 1.x, $legacy."
      fi
      if [ -d "$data" ]; then
        rm -rf "$data"
        echo "Deleted this checkout's own environment and collections, $data. just install makes them again."
      else
        echo "This checkout has no environment of its own, in $data."
      fi
      exit 0
    fi
    dir={{ quote(LOG_DIR) }}
    if [ -n "$dir" ]; then
      set -- "$dir"/[0-9]*-*.log
      if [ -e "$1" ]; then
        rm -f "$@"
        echo "Deleted $# run log(s) from $dir."
      else
        echo "No run logs in $dir."
      fi
      rmdir "$dir" 2>/dev/null || true
    fi
    if [ "$part" != logs ]; then
      # .cache holds the run lock and the MCP server's jobs: never removed under a run that holds the lock.
      tools/check unheld {{ quote(RUN_LOCK) }}
      rm -rf .cache .pytest_cache .ruff_cache .ansible .molecule site
      find . -name __pycache__ -type d -prune -exec rm -rf {} +
    fi
