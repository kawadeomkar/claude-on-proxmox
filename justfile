# Developer entry points. Every recipe runs its tools from the Python
# environment you made for this project, into which `just install` puts
# exactly the versions locked in uv.lock - never a tool that merely happens to
# be on PATH. `just` lists the recipes, `just help <recipe>` one's options.
#
# Few recipes, each of which does all of its job by default; an option or a
# word narrows one to a single part of it (just deploy --only configure, just
# test integration). Options go after the recipe's name.
#
# How every recipe runs (docs/architecture/decisions.md §15.3): a bash script with
# no dependencies. Its first lines refuse a run started in another checkout
# (tools/check checkout), then check its own arguments, then what it needs -
# the environment (tools/env-run --bin), the vault, the collections - and only
# then does it work, its last command exec'd - but in a recipe drawn in parts
# (lint, init), whose trap must still run to say the run's end (tools/live). Nothing
# is a dependency, which JUST_NO_DEPS or --no-deps would skip, and no
# assignment here starts a program, which just would do before any line ran. A value a person typed
# reaches a script as an argument ("$1"), never as its text: just holds it to
# its [arg] pattern first, and what goes into JSON is checked again there.
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

# The Python of that environment: a virtualenv of your own, wherever it lives
# and whatever made it - python3 as this directory's shell finds it (one you
# activated, or one a version manager picks here), or PYTHON=/path/to/python,
# exported or before the recipe (just PYTHON=... install). The project keeps
# no environment of its own, and calls no tool to make or choose one.
PYTHON := env('PYTHON', 'python3')
# uv installs the locked packages into that environment. Install it yourself
# (https://docs.astral.sh/uv/getting-started/installation/, e.g. `brew install
# uv`). It never downloads a Python of its own here.
UV := env('UV', 'uv')
# Anything else for ansible-playbook, as the shell words they are, exported
# or before the recipe: just ANSIBLE_ARGS='-vvv -f 30' deploy alpha.
ANSIBLE_ARGS := env('ANSIBLE_ARGS', '')
# The run logs (tools/run-logged): LOG_DIR= turns them off, and `just logs`
# lists them.
LOG_DIR := env('LOG_DIR', justfile_directory() / '.logs')
LOG_RETENTION_DAYS := env('LOG_RETENTION_DAYS', '14')
# Where just status's run writes its report, which the recipe reads and then
# removes: git-ignored, and refused by tests/check_no_local_files.sh.
STATUS_DIR := env('STATUS_DIR', justfile_directory() / '.cache' / 'status')
# Where a run drawn in parts keeps its model between its parts (tools/live), a file named for the run's stamp and its
# top recipe that its end removes: git-ignored, and refused by tests/check_no_local_files.sh. LIVE_DIR= keeps none.
LIVE_DIR := env('LIVE_DIR', justfile_directory() / '.cache' / 'live')
# From the command line only (just BIN=<dir> ...): the tools of another
# directory, which the unit tests point at stand-ins. A BIN in the
# environment, a name many shells use for ~/bin, is never read.
BIN := ''
VAULT_FILE := 'inventory/group_vars/all/vault.yml'
VAULT_PASS := '.vault_pass'
# What `just init` copies from its .example beside it, where it is missing.
INIT_FILES := 'inventory/hosts.yml inventory/group_vars/all/local.yml ' + VAULT_FILE

# One run, one moment: every log of the run is named after it.
stamp := datetime('%Y%m%d-%H%M%S')
# The top recipe of a run drawn in parts, which a recipe sets on another it runs as a stage of its own run
# ({{ self }} live_run=test lint): that one then draws into the parent's run, under its title. A setting, never the
# environment, so a variable left exported joins no run.
live_run := ''
# The same just on the same justfile, with every setting of this run: what a
# recipe that includes another's work (test lints, init installs) runs it by.
# A recursive just would otherwise start without the command line's BIN= and
# the rest; tests/unit/test_justfile.py holds this to every setting above.
self := quote(just_executable()) + ' --justfile ' + quote(justfile()) + ' PYTHON=' + quote(PYTHON) + ' UV=' + quote(UV) + ' ANSIBLE_ARGS=' + quote(ANSIBLE_ARGS) + ' LOG_DIR=' + quote(LOG_DIR) + ' LOG_RETENTION_DAYS=' + quote(LOG_RETENTION_DAYS) + ' STATUS_DIR=' + quote(STATUS_DIR) + ' BIN=' + quote(BIN) + ' VAULT_FILE=' + quote(VAULT_FILE) + ' VAULT_PASS=' + quote(VAULT_PASS) + ' INIT_FILES=' + quote(INIT_FILES) + ' LIVE_DIR=' + quote(LIVE_DIR) + ' stamp=' + quote(stamp) + ' live_run=' + quote(live_run)
# The first line of every recipe but help.
guard := quote(justfile_directory() / 'tools/check') + ' checkout ' + quote(invocation_directory())
# The scripts directory of PYTHON's environment, or BIN: refused, with how to
# make one, for a Python that is no virtualenv's.
find_bin := 'bin=' + quote(BIN) + '; [ -n "$bin" ] || bin=$(PYTHON=' + quote(PYTHON) + ' tools/env-run --bin)'
# tools/run-logged with the run's logging settings; its first argument is the
# log's name.
run_logged := 'env LOG_DIR=' + quote(if LOG_DIR == '' { '' } else { absolute_path(LOG_DIR) }) + ' LOG_RETENTION_DAYS=' + quote(LOG_RETENTION_DAYS) + ' CLAUDE_ON_PROXMOX_LOG_STAMP=' + quote(stamp) + ' ' + quote(justfile_directory() / 'tools/run-logged')
# tools/live with the environment's Python and the run's logging settings, each option in its = form so that a
# value starting with - (ANSIBLE_ARGS=-vvv) is still its value: `run <stage> [--step <label>] -- <command>` in front
# of each command of a recipe drawn in parts, `end "$?"` from its trap.
live := '"$bin/python" ' + quote(justfile_directory() / 'tools/live') + ' --log-dir=' + quote(if LOG_DIR == '' { '' } else { absolute_path(LOG_DIR) }) + ' --retention=' + quote(LOG_RETENTION_DAYS) + ' --stamp=' + quote(stamp) + ' --ansible-args=' + quote(ANSIBLE_ARGS)
# Where a run in parts keeps its state, but for the top recipe's name, its shell's PID - two runs started in one
# second keep two - and .json: none without LIVE_DIR.
live_state := if LIVE_DIR == '' { '' } else { absolute_path(LIVE_DIR) / stamp }
# --vault-password-file .vault_pass, where there is one, decided by the shell
# when the line runs (init's questions may make it), not when just reads this.
vault_args := '[ ! -f ' + quote(VAULT_PASS) + ' ] || set -- "$@" --vault-password-file ' + quote(VAULT_PASS)
# A run with no terminal on stdin has nobody to approve a sign-in: no page
# opens and nothing reaches the clipboard, since nobody is there to use them,
# and just deploy skips its sign-ins, which would otherwise start by themselves
# and wait for a browser. A login_desktop or deploy_sign_in given in
# ANSIBLE_ARGS still wins: it comes later on the command line.
no_tty_args := '[ -t 0 ] || set -- "$@" -e ' + quote('{"login_desktop":false,"deploy_sign_in":false}')

# deploy's stages, which deploy asks about when one is given as a VM name
# (below). A recipe's name is a VM name there like any other: deploy takes
# every word after it as one, and just runs no second recipe.
stages := 'template|provision|configure|check|ssh-config'
# The roles configuration can be narrowed to (deploy --tags), as configure.yml
# tags them, and the roles with a Molecule scenario of their own (test --role).
configure_roles := 'common vscode_server dev_tools claude_code github_projects tailscale'
scenario_roles := 'common vscode_server dev_tools claude_code github_projects tailscale proxmox_template proxmox_vm'
# Hooks lint already runs itself, so its hooks part skips them, the
# production-profile ansible-lint and ruff never running a second time
# (ruff-format even rewriting files). gitleaks is in it because its hook scans
# only what is staged, and CI scans the tree and the pushed commits separately.
pre_commit_skip := 'yamllint,ansible-lint,ruff-check,ruff-format,gitleaks'
# What init says when a word follows its options - a bare word, or an
# answer as make took it, TOKEN_SECRET=... - without saying the word, which
# may be the secret.
init_extra_refused := "echo \"error: just init takes only its options (just help init), and nothing after them - never the token secret, which the command line would put in the shell's history and the process list: put it in a file only you can read, just init --token-secret-file <file>, or export CLAUDE_ON_PROXMOX_TOKEN_SECRET from wherever your secrets live - a CI secret, or read -rs at a prompt\" >&2; exit 1"

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
    echo "Without names, deploy and destroy act on claude-on-proxmox-default; status, login and deploy"
    echo "--only configure, --check or --only ssh-config act on every VM this project created."
    echo "Options go after the recipe's name, settings before it or exported: ANSIBLE_ARGS='...' passes"
    echo "anything else to ansible-playbook in init, deploy, code, login, status and destroy, and"
    echo "LOG_DIR=<dir> moves the run logs: just ANSIBLE_ARGS=-vvv deploy alpha."

# ------------------------------------------------------------------ use ----
# just install, the git hook, the local config files - copied from their
# examples only where they are missing - then playbooks/init_settings.yml,
# which asks, at a terminal, for each value a first deploy needs that is still
# the example's: the Proxmox host's address, the API token's ID and secret,
# the GitHub username. Enter leaves one for the next just init, and the vault
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
# CLAUDE_ON_PROXMOX_TOKEN_SECRET in the environment. A word after the options
# is caught by EXTRA and refused without being said.
[arg('EXTRA', help='Nothing: any word after the options is refused, unsaid')]
[arg('PROMPT', long='no-prompt', value='no', help='Ask nothing, even at a terminal')]
[arg('HOST', long='host', pattern='[A-Za-z0-9.:-]*', help="The Proxmox host's address, for inventory/hosts.yml")]
[arg('BRIDGE', long='bridge', pattern='[A-Za-z0-9._-]*', help='The bridge the template is built on, for local.yml (before the template exists)')]
[arg('TAILSCALE', long='tailscale', pattern='(on|off)?', help='on: each VM joins your tailnet, the default; off: no Tailscale, for a setup without an account (tailscale_enabled in local.yml)')]
[arg('STORAGE', long='storage', pattern='[A-Za-z0-9._-]*', help='The storage for VM disks, for local.yml: written when the token can use it there')]
[arg('TOKEN_NAME', long='token-name', pattern='[A-Za-z0-9._-]*', help="The API token's name, when it is not ansible")]
[arg('GITHUB_USER', long='github-user', pattern='[A-Za-z0-9-]*', help='The GitHub account whose repositories are cloned')]
[arg('TOKEN_USER', long='token-user', pattern='[A-Za-z0-9._@-]*', help="The API token's user, when it is not ansible@pve")]
[arg('TOKEN_SECRET_FILE', long='token-secret-file', pattern='[A-Za-z0-9._/~+-]*', help="A file holding the token's secret (or CLAUDE_ON_PROXMOX_TOKEN_SECRET in the environment; never on the command line)")]
[continue]
[doc('Set up this checkout, then ask for what a first deploy needs')]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
init HOST='' TOKEN_USER='' TOKEN_NAME='' TOKEN_SECRET_FILE='' GITHUB_USER='' STORAGE='' BRIDGE='' TAILSCALE='' PROMPT='' *EXTRA:
    {{ guard }}
    {{ if EXTRA == '' { '' } else { init_extra_refused } }}
    host=$1 token_user=$2 token_name=$3 token_secret_file=$4 github_user=$5 storage=$6 bridge=$7 tailscale=$8 prompt=$9
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
    only host "$host" "$letters.:-" 'letters, digits and . : -'
    only token-user "$token_user" "$letters._@-" 'letters, digits and . _ - @'
    only token-name "$token_name" "$letters._-" 'letters, digits and . _ -'
    only token-secret-file "$token_secret_file" "$letters._/~+-" 'letters, digits and . _ - / ~ + (give an absolute path where the directory has others)'
    only github-user "$github_user" "$letters-" 'letters, digits and -'
    only storage "$storage" "$letters._-" 'letters, digits and . _ -'
    only bridge "$bridge" "$letters._-" 'letters, digits and . _ -'
    given=
    for pair in host="$host" token_user="$token_user" token_name="$token_name" token_secret_file="$token_secret_file" \
      github_user="$github_user" storage="$storage" bridge="$bridge" tailscale="$tailscale"; do
      [ -z "${pair#*=}" ] || given="$given${given:+,}\"${pair%%=*}\":\"${pair#*=}\""
    done
    set --
    [ -z "$given" ] || set -- -e "{\"init_given\":{$given}}"
    # init asks only at a terminal, and never with --no-prompt: init_ask is
    # false otherwise, so a run with no terminal, or the playbook run by hand,
    # asks nothing and writes only what was given.
    [ "$prompt" = no ] || ! [ -t 0 ] || set -- -e '{"init_ask":true}' "$@"
    {{ self }} install
    {{ find_bin }}
    "$bin/pre-commit" install >/dev/null
    copied=
    for file in {{ INIT_FILES }}; do
      test -f "$file" || { cp "$file.example" "$file"; copied="$copied $file"; }
    done
    [ -z "$copied" ] || echo "Created$copied from their .example files (git-ignored)."
    # Drawn live from here, after just install, as one run in two parts - the questions, then the checks - unless it
    # is a stage of another recipe's run (live_run): its title, which no value given goes into, the state the two
    # playbooks share, and a trap that says the run's end however it ends, so neither is exec'd.
    if [ -z {{ quote(live_run) }} ]; then
      CLAUDE_ON_PROXMOX_LIVE=init
      state={{ quote(live_state) }}
      CLAUDE_ON_PROXMOX_LIVE_STATE=${state:+$state-init-$$.json}
      export CLAUDE_ON_PROXMOX_LIVE CLAUDE_ON_PROXMOX_LIVE_STATE
      live_end() { {{ live }} end "$1" || :; }
      trap 'rc=$?; live_end "$rc"; exit "$rc"' EXIT
    fi
    {{ run_logged }} init "$bin/ansible-playbook" -i playbooks/inventory/init.yml "$@" {{ ANSIBLE_ARGS }} playbooks/init_settings.yml
    # The checks run with the project's inventory and the vault, whose password
    # is looked for now: the questions may have just made .vault_pass.
    if head -c 14 {{ quote(VAULT_FILE) }} | grep -q '^\$ANSIBLE_VAULT' && [ ! -f {{ quote(VAULT_PASS) }} ] && [ -z "${ANSIBLE_VAULT_PASSWORD_FILE:-}" ]; then
      echo "Proxmox is not checked: {{ VAULT_FILE }} is encrypted, and there is no {{ VAULT_PASS }} or ANSIBLE_VAULT_PASSWORD_FILE to open it with."
      exit 0
    fi
    [ ! -f {{ quote(VAULT_PASS) }} ] || set -- --vault-password-file {{ quote(VAULT_PASS) }} "$@"
    {{ run_logged }} init "$bin/ansible-playbook" "$@" {{ ANSIBLE_ARGS }} playbooks/init_check.yml

# uv's sync installs the versions the lock pins into PYTHON's environment,
# after checking the lock still matches pyproject.toml. --inexact: the
# environment is the user's, and a package the lock does not list - theirs, or
# another project's in an environment that serves both - stays where it is.
# --python names that environment's interpreter, so uv never swaps it for
# another. Before the sync, tools/stale_scripts.py names each locked package
# with a command that starts a Python outside the environment's bin - uv
# writes the path the environment was synced by into every command, so a sync
# through a link into it (the .venv make venv once made) left them naming the
# link, and a sync that installs nothing rewrites none - and the sync
# reinstalls those, then checks that none is left. ansible-galaxy installs the
# collections requirements.yml pins. Both change nothing when they are
# current, so this is also the command to run after a pull, and what CI runs.
[arg('LOCK', long='lock', value='lock', help='Update uv.lock after editing a pin in pyproject.toml, installing nothing (uv from PATH)')]
[doc('Install the locked Python packages and the Galaxy collections into your environment')]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
install LOCK='':
    {{ guard }}
    export UV_PYTHON_DOWNLOADS=never
    if [ -n "$1" ]; then exec {{ quote(UV) }} lock; fi
    {{ find_bin }}
    python=$({{ quote(PYTHON) }} -c 'import sys; print(sys.executable)')
    prefix=$({{ quote(PYTHON) }} -c 'import sys; print(sys.prefix)')
    reinstall=$("$python" -I tools/stale_scripts.py)
    UV_PROJECT_ENVIRONMENT="$prefix" {{ quote(UV) }} sync --locked --inexact --python "$python" $reinstall
    [ -z "$reinstall" ] || "$python" -I tools/stale_scripts.py --check
    exec "$bin/ansible-galaxy" collection install -r requirements.yml

# A plaintext vault can only be encrypted and an encrypted one only edited, so
# the file says which: the first `just vault` after editing the example
# encrypts it, and every one after that opens it in $EDITOR.
[doc('Encrypt the vault, or edit it once it is encrypted')]
[group('use')]
[no-exit-message]
[script]
vault:
    {{ guard }}
    {{ find_bin }}
    file={{ quote(VAULT_FILE) }}
    test -f "$file" || { echo "error: $file missing. Run: just init" >&2; exit 1; }
    set --
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
[arg('START', long='start', value='true', help='Start a VM this run would leave stopped (proxmox_vm_start_existing)')]
[arg('CHECK', long='check', value='check', help='Dry-run configuration: ansible-playbook --check --diff (every tagged VM, unless named)')]
[arg('NAMES', pattern='[A-Za-z0-9][A-Za-z0-9._,-]*', help='The VMs; none means claude-on-proxmox-default, or every VM this project created for --only configure, --check and --only ssh-config')]
[arg('ROLES', long='tags', short='t', pattern='[a-z_,]*', help='Configure only these roles, comma-separated: beside VM names it means --only configure (just deploy alpha --tags claude_code)')]
[arg('STAGE', long='only', pattern='(template|provision|configure|ssh-config)?', help='Run one stage and nothing after it: template (the cloud-init template, if missing), provision (create and start), configure (every tagged VM, unless named) or ssh-config (refresh the SSH aliases; without names, also prune those of VMs that are gone)')]
[continue]
[doc('Create, configure and sign in the VM(s), then open VS Code')]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
deploy STAGE='' CHECK='' ROLES='' START='' *NAMES:
    {{ guard }}
    stage=$1 check=$2 roles=$3 start=$4
    shift 4
    tools/check names "$@"
    names=$(IFS=,; printf %s "$*")
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
      options=${roles:+ --tags $roles}${start:+ --start}
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
    {{ find_bin }}
    tools/check vault {{ quote(VAULT_FILE) }}
    tools/check collections "$bin"
    set --
    {{ vault_args }}
    # Passed as JSON, not key=value: Ansible's key=value parser splits
    # extra-vars on whitespace, so make's VM_NAME="alpha beta" once became alpha
    # and only half the fleet was created.
    [ -z "$names" ] || set -- "$@" -e "{\"vm_name\": \"$names\"}"
    [ -z "$start" ] || set -- "$@" -e '{"proxmox_vm_start_existing": true}'
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
    # The live output (callback_plugins/claude_on_proxmox.py): set, the run is drawn rather than streamed, under
    # this title. Any -v in ANSIBLE_ARGS streams it as before.
    CLAUDE_ON_PROXMOX_LIVE="deploy${check:+ --check}${stage:+ --only $stage}${roles:+ --tags $roles}${start:+ --start}${names:+ $(printf %s "$names" | tr , ' ')}"
    export CLAUDE_ON_PROXMOX_LIVE
    if [ -n "$check" ]; then
      exec {{ run_logged }} "$name" "$bin/ansible-playbook" "$@" {{ ANSIBLE_ARGS }} --check --diff "$playbook"
    fi
    exec {{ run_logged }} "$name" "$bin/ansible-playbook" "$@" {{ ANSIBLE_ARGS }} "$playbook"

# One VM at a time: VS Code opens one window per workspace, and "open it in
# the editor" does not mean a fleet. --session is one of this machine's Claude
# Code sessions to continue there: its ID, the name of its transcript in
# ~/.claude/projects/, lowercase hex in groups of 8-4-4-4-12 and nothing else.
# Without it the folder's latest conversation continues, as before.
[arg('NAME', pattern='[A-Za-z0-9][A-Za-z0-9._,-]*', help='The one VM to open')]
[arg('PROJECT', long='project', pattern='([A-Za-z0-9._-]{1,100})?', help='Open one project folder on the VM rather than the whole projects directory')]
[arg('SESSION', long='session', pattern='([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?', help="Continue one of this machine's Claude Code sessions there, by its ID as /resume lists it: its checkout brought to the session's pushed commit, then claude --resume")]
[continue]
[doc("Open a VM in VS Code with a Claude terminal, this machine's recent Claude sessions copied there")]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
code PROJECT='' SESSION='' *NAME:
    {{ guard }}
    project=$1 session=$2
    shift 2
    [ "$#" -gt 0 ] || { echo "error: code opens one VM: just code <name>" >&2; exit 1; }
    tools/check names "$@"
    names=$(IFS=,; printf %s "$*")
    case "$names" in *,*) echo "error: code opens one VM at a time, not [$*]" >&2; exit 1 ;; esac
    tools/check project "$project"
    tools/check session "$session"
    {{ find_bin }}
    tools/check vault {{ quote(VAULT_FILE) }}
    tools/check collections "$bin"
    set --
    {{ vault_args }}
    set -- "$@" -e "{\"vm_name\": \"$names\"}"
    [ -z "$project" ] || set -- "$@" -e "{\"vm_project\": \"$project\"}"
    [ -z "$session" ] || set -- "$@" -e "{\"vm_session\": \"$session\"}"
    # Drawn live (callback_plugins/claude_on_proxmox.py, its declaration in filter_plugins/live_recipes.py), under
    # this title; a session's ID, which its report names, is left out of it. Any -v in ANSIBLE_ARGS streams it as
    # before.
    CLAUDE_ON_PROXMOX_LIVE="code${project:+ --project $project} $names"
    export CLAUDE_ON_PROXMOX_LIVE
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
[arg('SIGN_IN', long='only', pattern='(claude|github|tailscale)?', help='One sign-in: claude (by the browser, then Remote Control), github (the device flow, then the private repositories) or tailscale (its web login)')]
[continue]
[doc('Sign the VM(s) in to Claude, GitHub and Tailscale, or report how each is signed in')]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
login SIGN_IN='' *NAMES:
    {{ guard }}
    only=$1
    shift
    tools/check names "$@"
    names=$(IFS=,; printf %s "$*")
    {{ find_bin }}
    tools/check vault {{ quote(VAULT_FILE) }}
    tools/check collections "$bin"
    set --
    {{ vault_args }}
    [ -z "$names" ] || set -- "$@" -e "{\"vm_name\": \"$names\"}"
    {{ no_tty_args }}
    # Drawn live (callback_plugins/claude_on_proxmox.py, its declaration in filter_plugins/live_recipes.py), under
    # this title. Any -v in ANSIBLE_ARGS streams it as before.
    CLAUDE_ON_PROXMOX_LIVE="login${only:+ --only $only}${names:+ $(printf %s "$names" | tr , ' ')}"
    export CLAUDE_ON_PROXMOX_LIVE
    exec {{ run_logged }} "login${only:+-$only}" "$bin/ansible-playbook" "$@" {{ ANSIBLE_ARGS }} "playbooks/${only:+${only}_}login.yml"

# What every VM this project created is doing, and what needs doing next,
# read without changing anything (playbooks/status.yml): a dry run, with its
# own stdout callback (callback_plugins/claude_on_proxmox_status.py) the only
# one, so ansible.cfg's profile_tasks prints nothing on stdout (naming a stdout
# callback in callbacks_enabled loads nothing more, and an empty one is an
# error). No VM to read leaves a play's group empty, which Ansible warns of
# above the report: status says it in the report instead. The callback draws the
# report live at a terminal and prints it whole at the end. The run writes the report to a file of its own, named after the
# run, which the last line reads its exit code from, prints under --json, and
# removes: 0 nothing is wrong, 1 a problem, 2 something could not be read, 3
# the run itself failed or wrote no report. ansible-playbook's own code is
# caught, since -e would end the script at it: its 2 would read as "not read".
# just list is the same recipe, by the name it had before.
[arg('JSON', long='json', value='true', help='The report as JSON, alone on standard output, with the same exit code')]
[arg('NAMES', pattern='[A-Za-z0-9][A-Za-z0-9._,-]*', help='Only these VMs; none reads every VM this project created')]
[arg('ONLY', long='only', pattern='((vms|storage|access|sign-ins|system|work)(,(vms|storage|access|sign-ins|system|work))*)?', help='Only these sections, comma-separated: vms storage access sign-ins system work; vms,storage reads only the Proxmox API, never a VM')]
[doc('What every VM this project created is doing, and what needs doing next, read without changing anything')]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
status ONLY='' JSON='' *NAMES:
    {{ guard }}
    only=$1 json=$2
    shift 2
    tools/check names "$@"
    tools/check sections "$only"
    names=$(IFS=,; printf %s "$*")
    {{ find_bin }}
    tools/check vault {{ quote(VAULT_FILE) }}
    tools/check collections "$bin"
    set --
    {{ vault_args }}
    [ -z "$names" ] || set -- "$@" -e "{\"vm_name\": \"$names\"}"
    [ -z "$only" ] || set -- "$@" -e "{\"status_only\": \"$only\"}"
    report={{ quote(absolute_path(STATUS_DIR)) }}/{{ stamp }}-$$.json
    mkdir -p {{ quote(absolute_path(STATUS_DIR)) }}
    chmod 700 {{ quote(absolute_path(STATUS_DIR)) }}
    rm -f "$report"
    rc=0
    STATUS_REPORT_FILE=$report STATUS_QUIET=$json ANSIBLE_STDOUT_CALLBACK=claude_on_proxmox_status \
      ANSIBLE_HOST_PATTERN_MISMATCH=ignore \
      ANSIBLE_CALLBACKS_ENABLED=claude_on_proxmox_status \
      {{ run_logged }} status "$bin/ansible-playbook" "$@" --check {{ ANSIBLE_ARGS }} playbooks/status.yml || rc=$?
    if [ "$rc" -ne 0 ] || [ ! -s "$report" ]; then
      rm -f "$report"
      echo "just status: the run failed (ansible-playbook exited $rc) and wrote no report" >&2
      exit 3
    fi
    exec "$bin/python" tools/status-exit "$report" "$json"

alias list := status

[arg('NAMES', pattern='[A-Za-z0-9][A-Za-z0-9._,-]*', help='The VMs; none means claude-on-proxmox-default')]
[continue]
[doc("Save the VM(s)' own Claude sessions here, release them from Claude, GitHub and Tailscale, then delete them")]
[group('use')]
[no-exit-message]
[positional-arguments]
[script]
destroy *NAMES:
    {{ guard }}
    tools/check names "$@"
    names=$(IFS=,; printf %s "$*")
    {{ find_bin }}
    tools/check vault {{ quote(VAULT_FILE) }}
    tools/check collections "$bin"
    set --
    {{ vault_args }}
    [ -z "$names" ] || set -- "$@" -e "{\"vm_name\": \"$names\"}"
    # Drawn live (callback_plugins/claude_on_proxmox.py, its declaration in filter_plugins/live_recipes.py), under
    # this title. Any -v in ANSIBLE_ARGS streams it as before.
    CLAUDE_ON_PROXMOX_LIVE="destroy${names:+ $(printf %s "$names" | tr , ' ')}"
    export CLAUDE_ON_PROXMOX_LIVE
    exec {{ run_logged }} destroy "$bin/ansible-playbook" "$@" {{ ANSIBLE_ARGS }} playbooks/destroy.yml

# Only files named the way tools/run-logged names its logs, and only in
# LOG_DIR, which a just setting puts into the script's text rather than its
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
    # Drawn live, as a run in parts (tools/live), unless it is a stage of another recipe's run (live_run): its title,
    # the state its checks share, and a trap that says the run's end however it ends, so no check is exec'd. Each
    # check is a command through tools/live, which passes it through unchanged where nothing is drawn.
    if [ -z {{ quote(live_run) }} ]; then
      CLAUDE_ON_PROXMOX_LIVE="lint${all_hooks:+ --all-hooks}${*:+ $*}"
      state={{ quote(live_state) }}
      CLAUDE_ON_PROXMOX_LIVE_STATE=${state:+$state-lint-$$.json}
      export CLAUDE_ON_PROXMOX_LIVE CLAUDE_ON_PROXMOX_LIVE_STATE
      live_end() { {{ live }} end "$1" || :; }
      trap 'rc=$?; live_end "$rc"; exit "$rc"' EXIT
    fi
    plan=
    for part in lock yaml ansible python just syntax hooks; do
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
# images into one small disk, so they run one after another.
[arg('ROLES', long='role', pattern='[a-z_,]*', help="Only these roles' scenarios, comma-separated: on its own it means the role part (just test --role common,claude_code)")]
[arg('PARTS', pattern='unit|molecule|role|integration|provision', help="unit (pytest over tests/unit/, no network), molecule (every Molecule scenario: role, integration, provision), role (the roles' own scenarios), integration (the full configure playbook against a Docker container), provision (provision.yml and discover.yml against a fake Proxmox API)")]
[doc('Lint, then every test: unit and Molecule (the local quality gate)')]
[group('develop')]
[no-exit-message]
[positional-arguments]
[script]
test ROLES='' *PARTS:
    {{ guard }}
    roles=$1
    shift
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
    {{ find_bin }}
    tools/check collections "$bin"
    absbin=$(cd "$bin" && pwd -P)
    [ -z "$lint" ] || {{ self }} lint
    if runs unit; then {{ run_logged }} "$name" "$bin/pytest"; fi
    if runs role; then
      list={{ quote(scenario_roles) }}
      [ -z "$roles" ] || list=$(printf %s "$roles" | tr , ' ')
      for role in $list; do
        echo "===== molecule: $role"
        (cd "roles/$role" && PATH="$absbin:$PATH" {{ run_logged }} "$name" "$absbin/molecule" test)
      done
    fi
    if runs integration; then PATH="$absbin:$PATH" {{ run_logged }} "$name" "$absbin/molecule" test -s configure; fi
    if runs provision; then PATH="$absbin:$PATH" {{ run_logged }} "$name" "$absbin/molecule" test -s provision; fi

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
    export ANSIBLE_PLAYBOOK="$bin/ansible-playbook"
    exec {{ run_logged }} vagrant vagrant up --provision

# Only files named the way tools/run-logged names its logs, and only in
# LOG_DIR.
[arg('PART', pattern='logs', help='logs: delete only the run logs in LOG_DIR')]
[doc('Remove caches, the run logs and the built site (never your Python environment)')]
[group('develop')]
[no-exit-message]
[positional-arguments]
[script]
clean *PART:
    {{ guard }}
    [ "$#" -le 1 ] || { echo "error: just clean takes one part, logs (got: [$*])" >&2; exit 1; }
    part=${1:-}
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
      rm -rf .cache .pytest_cache .ruff_cache .ansible .molecule collections site
      find . -name __pycache__ -type d -prune -exec rm -rf {} +
    fi
