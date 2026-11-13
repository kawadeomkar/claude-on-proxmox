#!/bin/sh
# Set up the API access agent-warehouse uses on a Proxmox VE host, and
# nothing else. Run it on the host, as root:
#
#   on the host's shell (the web UI: the node, then Shell):  sh proxmox-access.sh [options]
#   from your machine, over root SSH:                        ssh root@<proxmox-host> sh -s -- [options] < tools/proxmox-access.sh
#
# just init runs it the second way, once you answer y to its question; this is
# the whole of what it runs there (docs/getting-started/proxmox-access.md).
#
# It creates whichever of these is missing, and changes nothing that exists
# but the role, to which it adds a privilege it lacks, never taking one away:
#
#   the role AnsibleVM: the privileges below, and the guest agent's, which is
#     VM.Monitor on Proxmox VE 8 and VM.GuestAgent.Audit on 9 - a role an older
#     version made lacks what a newer one needs, as Sys.Audit, added for sizes
#   the user ansible@pve, with no password: it signs in by its token alone
#   that role for that user on /vms, on the storage for VM disks, on the
#     bridge and on the node, wherever the user does not hold what the role
#     gives there yet
#   the API token ansible@pve!ansible, without privilege separation, so it
#     has exactly the user's privileges
#
# and prints the token's secret, which Proxmox shows only once: just init asks
# for it. A token that exists already, or a grant that does not reach the VMs
# under its path, is named with what to do, and left as it is.
#
# Options:
#   --storage <id>       the storage for VM disks: local-lvm (an install on ZFS has local-zfs)
#   --bridge <name>      the bridge the VMs are on: vmbr0
#   --user <user@realm>  the token's user: ansible@pve
#   --token <name>       the token's name: ansible
#   --no-token           make no token: what just init runs while its vault holds a token's secret already,
#                        which a new token's could not take the place of. The token must be there, or nothing
#                        changes: that secret could be no other's
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
no_token=0
# The privileges each path needs; the guest agent's joins /vms's below.
vms_privileges='VM.Allocate VM.Clone VM.Config.CPU VM.Config.Cloudinit VM.Config.Disk VM.Config.Memory VM.Config.Network VM.Config.Options VM.PowerMgmt VM.Audit'
storage_privileges='Datastore.AllocateSpace Datastore.Audit'
bridge_privileges='SDN.Use'
# Read only: the node's CPU threads and memory, which just status's capacity
# and a sized just deploy weigh a VM's size against.
node_privileges='Sys.Audit'

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
  --no-token)
    no_token=1
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
# ---------------------------------------------------------- the token held ---
# With --no-token the secret just init holds can be only this token's. One
# that is not there - a host installed again under the old vault, a secret of a
# token named otherwise - leaves the secret no token's here, and nothing this
# adds would make it work: said, and nothing changed. One made with privilege
# separation holds none of the user's privileges, so what this gives the user
# never reaches it: said.
if [ "$no_token" = 1 ]; then
  held_token=$(pvesh get "/access/users/$user/token/$token" --output-format json 2>/dev/null) \
    || die "token $user!$token is not on node $node, so the secret just init holds is not its, and nothing was changed: put this host's token's secret in the vault (just vault), or set it back to its placeholder and run just init, which then offers to make the token"
  case "$held_token" in *'"privsep":1'* | *'"privsep":"1"'*)
    say "WARNING: token $user!$token was made with privilege separation, so it holds none of $user's privileges, and what $user is given here never reaches it: remove it - pveum user token delete $user $token - and make it again without (docs/getting-started/proxmox-access.md)"
    ;;
  esac
fi

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
wanted="$vms_privileges $storage_privileges $bridge_privileges $node_privileges"
lacking=''
if role_privileges=$(pvesh get "/access/roles/$role" --output-format json 2>/dev/null); then
  for privilege in $wanted; do
    has "$role_privileges" "$privilege" || lacking="$lacking${lacking:+,}$privilege"
  done
  if [ -n "$lacking" ]; then
    # Added, never replacing what it holds: a privilege a newer version needs, which the role an older one made lacks.
    run pveum role modify "$role" --append 1 --privs "$lacking"
  else
    say "role $role exists, with every privilege"
  fi
else
  run pveum role add "$role" --privs "$(commas $wanted)"
fi

# ------------------------------------------------------------------ the user ---
if pvesh get "/access/users/$user" --output-format json >/dev/null 2>&1; then
  user_exists=1
  user_was_there=1
  say "user $user exists"
else
  user_was_there=0
  run pveum user add "$user" --comment agent-warehouse
  # With --dry-run it is still not there.
  [ "$mode" = dry-run ] && user_exists=0 || user_exists=1
fi

# ---------------------------------------------------------------- the grants ---
# grant <path> <privileges>: the role, for the user, on the path, where the
# user lacks a privilege there - every one the role gives, once a privilege it
# lacked was given it above. The permissions read give each privilege its
# propagate flag: on /vms one that does not propagate is none, since Proxmox
# checks each VM's own path, and its grant is the person's to change.
#
# granted <path>: the role is granted to the user itself on the path, or above
# it and propagating, so a privilege the role is given above reaches the user
# there with no grant of its own. Only --dry-run asks, since the role has not
# changed yet when it reads what the user holds; the run reads it after. A grant
# through a group is not looked for: the dry run may then show a grant the run
# finds made already, more than it runs, never less.
acls=''
granted() {
  if [ -z "$acls" ]; then
    acl_json=$(pvesh get /access/acl --output-format json) || die "Proxmox would not list the ACLs"
    acls=$(printf '%s' "$acl_json" | tr '{' '\n')
  fi
  printf '%s\n' "$acls" | {
    while IFS= read -r entry; do
      case "$entry" in *"\"ugid\":\"$user\""*) ;; *) continue ;; esac
      case "$entry" in *"\"roleid\":\"$role\""*) ;; *) continue ;; esac
      case "$entry" in *'"type":"user"'*) ;; *) continue ;; esac
      at=$(printf '%s' "$entry" | grep -o '"path":"[^"]*"' | cut -d'"' -f4)
      [ "$at" != "$1" ] || exit 0
      case "$entry" in *'"propagate":1'*) ;; *) continue ;; esac
      case "$1" in "${at%/}"/*) exit 0 ;; esac
    done
    exit 1
  }
}
lacking_words=$(printf '%s' "$lacking" | tr ',' ' ')
grant() {
  if [ "$user_exists" = 1 ]; then
    held=$(pveum user permissions "$user" --path "$1" --output-format json)
  else
    held='{}'
  fi
  missing='' unpropagated=''
  for privilege in $2; do
    if has "$held" "$privilege"; then
      case "$1 $held" in "/vms "*"\"$privilege\":0"*) unpropagated="$unpropagated${unpropagated:+,}$privilege" ;; esac
    elif [ "$mode" = dry-run ] && [ "$user_exists" = 1 ] && listed "$privilege" "$lacking_words" && granted "$1"; then
      : # Given to the role above, it reaches the user here through the role's grant once the role has it.
    else
      # The role gives it: made with it, holding it, or given it above.
      missing="$missing${missing:+,}$privilege"
    fi
  done
  if [ -n "$unpropagated" ]; then
    say "$user holds $unpropagated on $1 but not on the VMs under it, and its grant is left as it is; this makes it propagate: pveum acl modify $1 --users $user --roles $role --propagate 1"
  elif [ -n "$missing" ]; then
    run pveum acl modify "$1" --users "$user" --roles "$role"
  else
    say "$user holds what it needs on $1"
  fi
}
grant /vms "$vms_privileges"
grant "/storage/$storage" "$storage_privileges"
grant "/sdn/zones/localnetwork/$bridge" "$bridge_privileges"
grant "/nodes/$node" "$node_privileges"

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
if [ "$no_token" = 1 ]; then
  : # just init's vault holds the secret of the token it uses: no other is made.
elif existing=$(pvesh get "/access/users/$user/token/$token" --output-format json 2>/dev/null); then
  say "token $user!$token exists: Proxmox showed its secret once, when it was made, and never again, so just init asks for it. If it is lost, remove the token - pveum user token delete $user $token, and every machine that uses it loses access - and run this again."
  case "$existing" in *'"privsep":1'* | *'"privsep":"1"'*)
    say "It was made with privilege separation, so it holds none of $user's privileges: remove it and run this again, which makes it without."
    ;;
  esac
elif [ "$mode" = dry-run ]; then
  broad
  run pveum user token add "$user" "$token" --privsep 0 --comment agent-warehouse
else
  broad
  changes=$((changes + 1))
  say "+ pveum user token add $user $token --privsep 0 --comment agent-warehouse"
  made=$(pveum user token add "$user" "$token" --privsep 0 --comment agent-warehouse --output-format json)
  secret=$(printf '%s' "$made" | sed -n 's/.*"value":"\([0-9a-f-]*\)".*/\1/p')
  [ -n "$secret" ] || die "pveum made the token $user!$token but printed no secret: remove it - pveum user token delete $user $token - and run this again"
  say "The token's secret, which Proxmox shows only this once: just init asks for it."
  printf '%s\n' "$secret"
fi

[ "$changes" -gt 0 ] || say "Nothing to do: the access is set up."
