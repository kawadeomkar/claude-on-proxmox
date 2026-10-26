#!/bin/sh
# Set up the API access claude-on-proxmox uses on a Proxmox VE host, and
# nothing else. Run it on the host, as root:
#
#   on the host's shell (the web UI: the node, then Shell):  sh proxmox-access.sh [options]
#   from your machine, over root SSH:                        ssh root@<proxmox-host> sh -s -- [options] < tools/proxmox-access.sh
#
# just init runs it the second way, once you answer y to its question; this is
# the whole of what it runs there (docs/getting-started/proxmox-access.md).
#
# It creates whichever of these is missing, and changes nothing that exists:
#
#   the role AnsibleVM: the privileges below, and the guest agent's, which is
#     VM.Monitor on Proxmox VE 8 and VM.GuestAgent.Audit on 9
#   the user ansible@pve, with no password: it signs in by its token alone
#   that role for that user on /vms, on the storage for VM disks and on the
#     bridge, wherever the user does not hold what the role gives there yet
#   the API token ansible@pve!ansible, without privilege separation, so it
#     has exactly the user's privileges
#
# and prints the token's secret, which Proxmox shows only once: just init asks
# for it. A role that exists without a privilege, or a token that exists
# already, is named with what to do, and left as it is.
#
# Options:
#   --storage <id>       the storage for VM disks: local-lvm (an install on ZFS has local-zfs)
#   --bridge <name>      the bridge the VMs are on: vmbr0
#   --user <user@realm>  the token's user: ansible@pve
#   --token <name>       the token's name: ansible
#   --dry-run            print the commands it would run, and run none
#   --list               print this node's storages for VM disks and its bridges, and change nothing
#
# stdout carries only data, for just init: the commands with --dry-run, the
# lines "storage <id>" and "bridge <name>" with --list, and otherwise the
# secret of a token it made. Everything said to you goes to stderr.
set -euf

role=AnsibleVM
storage=local-lvm
bridge=vmbr0
user=ansible@pve
token=ansible
mode=apply
# The privileges each path needs; the guest agent's joins /vms's below.
vms_privileges='VM.Allocate VM.Clone VM.Config.CPU VM.Config.Cloudinit VM.Config.Disk VM.Config.Memory VM.Config.Network VM.Config.Options VM.PowerMgmt VM.Audit'
storage_privileges='Datastore.AllocateSpace Datastore.Audit'
bridge_privileges='SDN.Use'

say() { printf '%s\n' "$*" >&2; }
die() {
  say "error: $*"
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
  --storage | --bridge | --user | --token)
    [ $# -ge 2 ] || die "$1 takes a value"
    case "$1" in
    --storage) storage=$2 ;;
    --bridge) bridge=$2 ;;
    --user) user=$2 ;;
    --token) token=$2 ;;
    esac
    shift 2
    ;;
  --dry-run)
    mode=dry-run
    shift
    ;;
  --list)
    mode=list
    shift
    ;;
  *) die "no option $1: the options are at the top of this script" ;;
  esac
done

# Each value goes on pveum's command line, so each is one plain word. The
# sets are spelt out rather than written as ranges, which a locale can widen.
letters=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789
only() { # only <option> <value> <characters> <the characters, in words>
  case "$2" in '' | *[!$3]*) die "$1 may contain only $4 (got: [$2])" ;; esac
}
only --storage "$storage" "$letters._-" 'letters, digits and . _ -'
only --bridge "$bridge" "$letters._-" 'letters, digits and . _ -'
only --user "$user" "$letters._@-" 'letters, digits and . _ - @'
only --token "$token" "$letters._-" 'letters, digits and . _ -'
case "$user" in ?*@?*) ;; *) die "--user is user@realm (got: [$user])" ;; esac
case "$user" in *@*@*) die "--user is user@realm (got: [$user])" ;; esac
# A token of root@pam, made without privilege separation, would hold every privilege there is.
[ "$user" != root@pam ] || die "--user is a user of the project's own, never root@pam"
case "$token" in [!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz]*) die "--token starts with a letter (got: [$token])" ;; esac

[ "$(id -u)" = 0 ] || die "run it as root, on the Proxmox host"
command -v pveum >/dev/null 2>&1 && command -v pvesh >/dev/null 2>&1 \
  || die "there is no pveum here: run it on the Proxmox VE host"

major=$(pveversion | sed -n 's|^pve-manager/\([0-9][0-9]*\)\..*|\1|p')
[ -n "$major" ] || die "pveversion did not say which Proxmox VE this is"
if [ "$major" -ge 9 ]; then agent=VM.GuestAgent.Audit; else agent=VM.Monitor; fi
vms_privileges="$vms_privileges $agent"

# Proxmox names a node by its short host name.
node=$(hostname -s)

# names <key>: each value of "key" in the JSON on stdin, one per line. pvesh
# prints compact JSON, and these values are plain words.
names() { grep -o "\"$1\":\"[^\"]*\"" | cut -d'"' -f4; }
json=$(pvesh get "/nodes/$node/storage" --content images --enabled 1 --output-format json) \
  || die "Proxmox would not list the storages on node $node"
storages=$(printf '%s' "$json" | names storage)
json=$(pvesh get "/nodes/$node/network" --type any_local_bridge --output-format json) \
  || die "Proxmox would not list the bridges on node $node"
bridges=$(printf '%s' "$json" | names iface)

if [ "$mode" = list ]; then
  for name in $storages; do printf 'storage %s\n' "$name"; done
  for name in $bridges; do printf 'bridge %s\n' "$name"; done
  exit 0
fi

listed() { # listed <name> <names>
  for name in $2; do
    [ "$name" != "$1" ] || return 0
  done
  return 1
}
# commas <words>: one argument, as pveum takes a list; listing <words>: for a person.
commas() { printf '%s' "$*" | tr ' ' ','; }
listing() { printf '%s' "$*" | sed 's/ /, /g'; }
if ! listed "$storage" "$storages"; then
  [ -n "$storages" ] || die "node $node has no storage for VM disks enabled"
  die "storage $storage is not on node $node for VM disks: give one of $(listing $storages) with --storage"
fi
if ! listed "$bridge" "$bridges"; then
  [ -n "$bridges" ] || die "node $node has no bridge"
  die "bridge $bridge is not on node $node: give one of $(listing $bridges) with --bridge"
fi

# has <JSON object> <key>: the object has that key, as a privilege set does.
has() {
  case "$1" in *"\"$2\":"*) return 0 ;; esac
  return 1
}
changes=0
# run <command...>: printed with --dry-run; otherwise said, and run.
run() {
  changes=$((changes + 1))
  if [ "$mode" = dry-run ]; then
    printf '%s\n' "$*"
  else
    say "+ $*"
    "$@" >&2
  fi
}

# ------------------------------------------------------------------ the role ---
wanted="$vms_privileges $storage_privileges $bridge_privileges"
if role_privileges=$(pvesh get "/access/roles/$role" --output-format json 2>/dev/null); then
  role_exists=1
  lacking=''
  for privilege in $wanted; do
    has "$role_privileges" "$privilege" || lacking="$lacking${lacking:+,}$privilege"
  done
  if [ -n "$lacking" ]; then
    say "role $role exists without $lacking, and is left as it is; this adds them: pveum role modify $role --append 1 --privs $lacking"
  else
    say "role $role exists, with every privilege"
  fi
else
  role_exists=0
  run pveum role add "$role" --privs "$(commas $wanted)"
fi

# ------------------------------------------------------------------ the user ---
if pvesh get "/access/users/$user" --output-format json >/dev/null 2>&1; then
  user_exists=1
  user_was_there=1
  say "user $user exists"
else
  user_was_there=0
  run pveum user add "$user" --comment claude-on-proxmox
  # With --dry-run it is still not there.
  [ "$mode" = dry-run ] && user_exists=0 || user_exists=1
fi

# ---------------------------------------------------------------- the grants ---
# grant <path> <privileges>: the role, for the user, on the path, where the
# user lacks a privilege there that the role gives. The permissions read give
# each privilege its propagate flag: on /vms one that does not propagate is
# none, since Proxmox checks each VM's own path, and its grant is the person's
# to change. A privilege the role lacks is said, as the role is.
grant() {
  if [ "$user_exists" = 1 ]; then
    held=$(pveum user permissions "$user" --path "$1" --output-format json)
  else
    held='{}'
  fi
  missing='' unpropagated='' beyond_role=''
  for privilege in $2; do
    if has "$held" "$privilege"; then
      case "$1 $held" in "/vms "*"\"$privilege\":0"*) unpropagated="$unpropagated${unpropagated:+,}$privilege" ;; esac
    elif [ "$role_exists" = 0 ] || has "$role_privileges" "$privilege"; then
      missing="$missing${missing:+,}$privilege"
    else
      beyond_role="$beyond_role${beyond_role:+,}$privilege"
    fi
  done
  if [ -n "$unpropagated" ]; then
    say "$user holds $unpropagated on $1 but not on the VMs under it, and its grant is left as it is; this makes it propagate: pveum acl modify $1 --users $user --roles $role --propagate 1"
  elif [ -n "$missing" ]; then
    run pveum acl modify "$1" --users "$user" --roles "$role"
  elif [ -n "$beyond_role" ]; then
    say "$user lacks $beyond_role on $1, which role $role does not give either: add it to the role, as above, and run this again"
  else
    say "$user holds what it needs on $1"
  fi
}
grant /vms "$vms_privileges"
grant "/storage/$storage" "$storage_privileges"
grant "/sdn/zones/localnetwork/$bridge" "$bridge_privileges"

# ----------------------------------------------------------------- the token ---
# broad: say what a user that was there already holds on /, for every guest,
# storage and setting. A token made without privilege separation holds it too,
# and a user of the project's own holds nothing there.
broad() {
  [ "$user_was_there" = 1 ] || return 0
  above=$(pveum user permissions "$user" --path / --output-format json | grep -o '"[A-Za-z][A-Za-z.]*":[01]' | cut -d'"' -f2)
  [ -z "$above" ] || say "WARNING: $user holds $(listing $above) on /, which its token will hold too: a user of the project's own holds only role $role, on the paths above. Give --user another user to keep the token to that."
}
# Last, so that nothing after it can fail with its secret unsaid.
if existing=$(pvesh get "/access/users/$user/token/$token" --output-format json 2>/dev/null); then
  say "token $user!$token exists: Proxmox showed its secret once, when it was made, and never again, so just init asks for it. If it is lost, remove the token - pveum user token delete $user $token, and every machine that uses it loses access - and run this again."
  case "$existing" in *'"privsep":1'* | *'"privsep":"1"'*)
    say "It was made with privilege separation, so it holds none of $user's privileges: remove it and run this again, which makes it without."
    ;;
  esac
elif [ "$mode" = dry-run ]; then
  broad
  run pveum user token add "$user" "$token" --privsep 0 --comment claude-on-proxmox
else
  broad
  changes=$((changes + 1))
  say "+ pveum user token add $user $token --privsep 0 --comment claude-on-proxmox"
  made=$(pveum user token add "$user" "$token" --privsep 0 --comment claude-on-proxmox --output-format json)
  secret=$(printf '%s' "$made" | sed -n 's/.*"value":"\([0-9a-f-]*\)".*/\1/p')
  [ -n "$secret" ] || die "pveum made the token $user!$token but printed no secret: remove it - pveum user token delete $user $token - and run this again"
  say "The token's secret, which Proxmox shows only this once: just init asks for it."
  printf '%s\n' "$secret"
fi

[ "$changes" -gt 0 ] || say "Nothing to do: the access is set up."
