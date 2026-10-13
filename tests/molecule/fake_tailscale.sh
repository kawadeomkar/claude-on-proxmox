#!/bin/sh
# Stand-in for the tailscale CLI: the commands the tailscale role runs, against
# a node whose state is files in $TS_FAKE_DIR (default $HOME/.tailscale-fake),
# so each stand-in host in the unit harness has its own. Used by the Molecule
# scenarios (installed at /usr/local/bin/tailscale, ahead of the package's
# /usr/bin/tailscale) and by tests/unit/test_tailscale.py. It never reaches
# anything: there is no network code here, and that is the point.
#
# Its output is Tailscale 1.104.1's, as the research probe and Phase 0 recorded
# it (docs, FACTS §2, §4, §12): the stderr URL block and timeout line of `up`,
# `invalid key:`, the up-checker's refusal, the status JSON of a Running and a
# signed-out node, and a logout that leaves no node key and resets the prefs.
#
# Canned keys decide a join (the vault key arrives on stdin, as the role sends
# it - `--auth-key=file:/dev/stdin`): a key containing kGood joins, kTagged joins
# tagged tag:claude-vm, kExpired is refused as an expired key, kSlow takes
# longer than --timeout and is then refused, kLate is still under way when
# --timeout runs out, kDenied is refused the tags it asks for. Any key join
# ends a web login's pending page, as a new `up` does in tailscaled. A
# tskey-client- secret without --advertise-tags is refused locally, as
# tailscale does. A key is never written anywhere: calls.log
# shows KEY in its place, and a key on stdin as "[key on stdin]".
#
# Seeding a state, each a file in the state directory:
#   daemon-down     status and the rest fail as with no tailscaled running
#   real-daemon     the same whenever the package's tailscaled is not running
#                   (its socket is gone): for the Molecule scenarios, where it
#                   runs beside the stand-in
#   nostate-reads   N: the next N status reads answer NoState, as a daemon just
#                   started does
#   starting        a node that has joined reads Starting (no relay reached)
#   approve-after   seconds after a web login's URL is minted that it is
#                   approved, whether `up` is still waiting or long gone
#   needs-approval  a join waits for an admin (NeedsMachineAuth)
#   name-taken      a join gets <hostname>-1, the name being taken
#   tailnet         CurrentTailnet.Name; suffix, the MagicDNS suffix; health, one
#                   extra Health line. These three go into JSON strings as they
#                   are: write them JSON-escaped (\u001b for ESC).
#   joined          a node that has joined already: its prefs are those
#                   prefs-* says, and it reads Running
#   expired         a node whose key has expired: NeedsLogin, HaveNodeKey kept;
#                   the file holds its node key (hex), or nothing for a new one
#   authurl-base    where a web login's page is, in place of Tailscale's (a page
#                   that is not Tailscale's, which the role must not open)
#   node-id         the node's stable ID, Self.ID (nFAKE otherwise)
set -eu

dir="${TS_FAKE_DIR:-${HOME:?}/.tailscale-fake}"
mkdir -p "$dir"
cd "$dir"

# Every call, the key never: a literal key on the command line is logged as KEY.
printf '%s\n' "$*" | sed 's/tskey-[A-Za-z0-9_?=&-]*/KEY/g' >> calls.log

get() { cat "$1" 2>/dev/null || printf '%s' "${2:-}"; }
put() { printf '%s' "$2" > "$1"; }
hex() { od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'; }
now() { date +%s; }

daemon_down() {
  echo "failed to connect to local tailscaled; it doesn't appear to be running" >&2
  exit 1
}
[ -e daemon-down ] && daemon_down
[ -e real-daemon ] && [ ! -S /run/tailscale/tailscaled.sock ] && daemon_down

# A seeded node that has joined already.
if [ -e joined ] && [ ! -e nodekey ]; then
  put nodekey "$(hex 32)"
  put state Running
  rm -f joined
fi
if [ -e expired ] && [ ! -e nodekey ]; then
  key=$(get expired)
  put nodekey "${key:-$(hex 32)}"
  put state NeedsLogin
  rm -f expired
fi

# A pending web login is approved by the "control server" approve-after seconds
# after its URL was minted - checked whenever the node is asked, as tailscaled
# finishes a login on its own once it is approved.
approve_if_due() {
  [ -e authurl ] && [ -e approve-after ] || return 0
  [ "$(now)" -ge $(( $(get pending-since 0) + $(get approve-after 0) )) ] || return 0
  rm -f authurl pending-since loginerror
  put nodekey "$(hex 32)"
  if [ -e needs-approval ]; then put state NeedsMachineAuth; else put state Running; fi
  put prefs-loggedout false
  put prefs-wantrunning true
}

bool() { case "$1" in true | false) printf '%s' "$1" ;; *) printf 'true' ;; esac; }

node_name() {
  name=$(get prefs-hostname)
  [ -n "$name" ] || name=$(get os-hostname fakehost)
  if [ -e name-taken ]; then printf '%s-1' "$name"; else printf '%s' "$name"; fi
}

status_json() {
  state=$(get state NeedsLogin)
  if [ "$state" = Running ] && [ -e starting ]; then state=Starting; fi
  if [ -n "$(get nostate-reads)" ] && [ "$(get nostate-reads 0)" -gt 0 ]; then
    put nostate-reads $(( $(get nostate-reads) - 1 ))
    state=NoState
  fi
  suffix=$(get suffix tail1234.ts.net)
  ip=$(get ip 100.101.102.103)
  key=$(get nodekey)
  health=""
  case "$state" in
    NeedsLogin | Stopped) health='"Tailscale is stopped."' ;;
  esac
  if [ -s loginerror ]; then health="${health:+$health, }\"The last login error was: $(get loginerror)\""; fi
  if [ -s health ]; then health="${health:+$health, }\"$(get health)\""; fi
  printf '{\n  "Version": "1.104.1-tFAKE",\n  "TUN": true,\n  "BackendState": "%s",\n' "$state"
  [ -n "$key" ] && printf '  "HaveNodeKey": true,\n'
  printf '  "AuthURL": "%s",\n' "$(get authurl)"
  case "$state" in
    Running | Starting | NeedsMachineAuth)
      tags=""
      [ -s tags ] && tags=$(sed 's/[^,][^,]*/"&"/g' tags)
      printf '  "TailscaleIPs": ["%s", "fd7a:115c:a1e0::1"],\n' "$ip"
      printf '  "Self": {\n    "ID": "%s", "PublicKey": "nodekey:%s", "HostName": "%s",\n' "$(get node-id nFAKE)" "$key" "$(node_name)"
      printf '    "DNSName": "%s.%s.", "OS": "linux",\n' "$(node_name)" "$suffix"
      printf '    "TailscaleIPs": ["%s", "fd7a:115c:a1e0::1"],%s "Online": true\n  },\n' "$ip" "${tags:+ \"Tags\": [$tags],}"
      printf '  "Health": [%s],\n  "MagicDNSSuffix": "%s",\n' "$health" "$suffix"
      printf '  "CurrentTailnet": {"Name": "%s", "StableID": "", "MagicDNSSuffix": "%s", "MagicDNSEnabled": true},\n' \
        "$(get tailnet you@example.com)" "$suffix"
      ;;
    *)
      zero=0000000000000000000000000000000000000000000000000000000000000000
      printf '  "TailscaleIPs": null,\n'
      printf '  "Self": {"ID": "", "PublicKey": "nodekey:%s", "HostName": "%s", "DNSName": "", "OS": "linux",' \
        "${key:-$zero}" "$(get os-hostname fakehost)"
      printf ' "TailscaleIPs": null, "Online": false},\n'
      printf '  "Health": [%s],\n  "MagicDNSSuffix": "",\n  "CurrentTailnet": null,\n' "$health"
      ;;
  esac
  printf '  "CertDomains": null,\n  "ExtraRecords": null,\n  "Peer": null,\n  "User": null,\n  "ClientVersion": null\n}'
}

prefs_json() {
  tags=$(get prefs-tags)
  [ -n "$tags" ] && tags="[$(printf '%s' "$tags" | sed 's/[^,][^,]*/"&"/g')]" || tags=null
  printf '{\n  "ControlURL": "%s",\n  "RouteAll": %s,\n  "ExitNodeID": "",\n  "CorpDNS": %s,\n' \
    "$(get prefs-login-server https://controlplane.tailscale.com)" "$(get prefs-routes false)" "$(get prefs-dns true)"
  printf '  "RunSSH": %s,\n  "WantRunning": %s,\n  "LoggedOut": %s,\n  "Hostname": "%s",\n  "AdvertiseTags": %s\n}\n' \
    "$(get prefs-ssh false)" "$(get prefs-wantrunning false)" "$(get prefs-loggedout true)" "$(get prefs-hostname)" "$tags"
}

# The flags `up` and `set` share. Sets reset, key_source, timeout and the
# wanted_* values; an unknown flag is refused as tailscale's flag package does.
parse_flags() {
  verb=$1
  shift
  reset=false key_source="" timeout=0 mention=" "
  for arg in "$@"; do
    case "$arg" in
      --reset) reset=true ;;
      --auth-key=* | --authkey=*) key_source=${arg#*=} ;;
      --timeout=*) timeout=${arg#*=}; timeout=${timeout%s} ;;
      --hostname=*) wanted_hostname=${arg#*=}; mention="$mention hostname" ;;
      --ssh) wanted_ssh=true; mention="$mention ssh" ;;
      --ssh=*) wanted_ssh=$(bool "${arg#*=}"); mention="$mention ssh" ;;
      --accept-routes=*) wanted_routes=$(bool "${arg#*=}"); mention="$mention routes" ;;
      --accept-dns=*) wanted_dns=$(bool "${arg#*=}"); mention="$mention dns" ;;
      --advertise-tags=*)
        if [ "$verb" = set ]; then
          echo "flag provided but not defined: -advertise-tags" >&2
          exit 2
        fi
        wanted_tags=${arg#*=}; mention="$mention tags" ;;
      --login-server=*) wanted_server=${arg#*=}; mention="$mention server" ;;
      --accept-risk=*) ;;
      *)
        echo "flag provided but not defined: -${arg#--}" >&2
        exit 2
        ;;
    esac
  done
}

apply_prefs() {
  if [ "$reset" = true ]; then
    put prefs-hostname "" ; put prefs-ssh false ; put prefs-routes false ; put prefs-dns true
    put prefs-tags "" ; rm -f prefs-login-server
  fi
  [ -z "${wanted_hostname+x}" ] || put prefs-hostname "$wanted_hostname"
  [ -z "${wanted_ssh+x}" ] || put prefs-ssh "$wanted_ssh"
  [ -z "${wanted_routes+x}" ] || put prefs-routes "$wanted_routes"
  [ -z "${wanted_dns+x}" ] || put prefs-dns "$wanted_dns"
  [ -z "${wanted_tags+x}" ] || put prefs-tags "$wanted_tags"
  [ -z "${wanted_server+x}" ] || put prefs-login-server "$wanted_server"
  return 0
}

# Once any `up` has run, an `up` that leaves out a non-default setting is refused
# unless it says --reset (FACTS §2).
up_checker() {
  [ -e prefs-touched ] && [ "$reset" = false ] || return 0
  missing=""
  [ -z "$(get prefs-hostname)" ] || case "$mention" in *hostname*) ;; *) missing=1 ;; esac
  [ "$(get prefs-ssh false)" = false ] || case "$mention" in *ssh*) ;; *) missing=1 ;; esac
  [ "$(get prefs-routes false)" = false ] || case "$mention" in *routes*) ;; *) missing=1 ;; esac
  [ "$(get prefs-dns true)" = true ] || case "$mention" in *dns*) ;; *) missing=1 ;; esac
  [ -z "$missing" ] && return 0
  cat >&2 <<'TEXT'
Error: changing settings via 'tailscale up' requires mentioning all
non-default flags. To proceed, either re-run your command with --reset or
use the command below to explicitly mention the current value of
all non-default settings:

	tailscale up --reset

TEXT
  exit 1
}

timed_out() {
  echo 'timeout waiting for Tailscale service to enter a Running state; check health with "tailscale status"' >&2
  exit 1
}

join_with() {
  key=$1
  case "$key" in
    tskey-client-*)
      if [ -z "${wanted_tags:-}" ]; then
        echo "oauth authkeys require --advertise-tags" >&2
        exit 1
      fi
      ;;
  esac
  if [ -n "$(get nodekey)" ]; then
    # The key sent to a node that has a login: tailscale#19501. One the server
    # no longer accepts logs the node out.
    printf '%s\n' "[key resent to a node with a login]" >> calls.log
  fi
  rm -f authurl pending-since
  case "$key" in
    *kExpired*)
      apply_prefs
      put loginerror "invalid key: API key does not exist"
      put state NeedsLogin
      echo "backend error: invalid key: API key does not exist" >&2
      exit 1
      ;;
    *kDenied*)
      apply_prefs
      put loginerror "requested tags [${wanted_tags:-}] are invalid or not permitted"
      put state NeedsLogin
      echo "backend error: requested tags [${wanted_tags:-}] are invalid or not permitted" >&2
      exit 1
      ;;
    *kSlow*)
      sleep "${timeout:-1}"
      put loginerror "invalid key: API key does not exist"
      timed_out
      ;;
    *kLate*)
      apply_prefs
      put prefs-touched ""
      put prefs-wantrunning true
      sleep "${timeout:-1}"
      timed_out
      ;;
    *kGood* | *kTagged*)
      apply_prefs
      rm -f loginerror authurl pending-since
      put prefs-touched ""
      put prefs-loggedout false
      put prefs-wantrunning true
      put nodekey "$(hex 32)"
      case "$key" in *kTagged*) put tags "tag:claude-vm" ;; *) put tags "${wanted_tags:-}" ;; esac
      if [ -e needs-approval ]; then
        put state NeedsMachineAuth
        printf '\nTo approve your machine, visit (as admin):\n\n\thttps://login.tailscale.com/admin\n\n' >&2
        sleep "${timeout:-1}"
        timed_out
      fi
      put state Running
      exit 0
      ;;
    *)
      apply_prefs
      put loginerror "invalid key: unable to validate API key"
      put state NeedsLogin
      echo "backend error: invalid key: unable to validate API key" >&2
      exit 1
      ;;
  esac
}

web_login() {
  apply_prefs
  put prefs-touched ""
  put prefs-wantrunning true
  url="$(get authurl-base "$(get prefs-login-server https://login.tailscale.com)")/a/$(hex 7)"
  put authurl "$url"
  put pending-since "$(now)"
  put state NeedsLogin
  printf '\nTo authenticate, visit:\n\n\t%s\n\n' "$url" >&2
  waited=0
  while [ "$waited" -lt "${timeout:-1}" ]; do
    approve_if_due
    if [ "$(get state)" = Running ]; then
      echo "Success." >&2
      exit 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  approve_if_due
  [ "$(get state)" = Running ] && { echo "Success." >&2; exit 0; }
  timed_out
}

approve_if_due
case "${1:-}" in
  status)
    [ "${2:-}" = --json ] || { echo "stand-in tailscale: only status --json" >&2; exit 2; }
    status_json
    ;;
  debug)
    [ "${2:-}" = prefs ] || { echo "stand-in tailscale: only debug prefs" >&2; exit 2; }
    prefs_json
    ;;
  up)
    shift
    parse_flags up "$@"
    up_checker
    if [ -n "$key_source" ]; then
      case "$key_source" in
        file:*)
          path=${key_source#file:}
          key=$(cat "$path" 2>/dev/null) || { echo "open $path: no such file or directory" >&2; exit 1; }
          printf '%s\n' "[key on stdin]" >> calls.log
          ;;
        *) key=$key_source ;;
      esac
      join_with "$(printf '%s' "$key" | tr -d ' \t\r\n')"
    fi
    if [ -n "$(get nodekey)" ] && [ "$(get state)" != NeedsLogin ]; then
      # Same flags, no key, a node that has a login: a quiet settings edit.
      apply_prefs
      exit 0
    fi
    web_login
    ;;
  set)
    shift
    parse_flags set "$@"
    reset=false
    apply_prefs
    ;;
  logout)
    if [ -n "$(get nodekey)" ]; then
      rm -f nodekey authurl pending-since loginerror tags prefs-touched
      put state NeedsLogin
      put prefs-hostname "" ; put prefs-ssh false ; put prefs-routes false ; put prefs-dns true
      put prefs-loggedout true ; put prefs-wantrunning false
    fi
    ;;
  down)
    if [ "$(get state)" = Running ]; then put state Stopped; put prefs-wantrunning false;
    elif [ "$(get state)" = Stopped ]; then echo "Tailscale was already stopped." >&2; fi
    ;;
  version)
    printf '1.104.1\n  tailscale commit: stand-in\n  go version: stand-in\n'
    ;;
  *)
    echo "stand-in tailscale: unexpected arguments: $*" >&2
    exit 2
    ;;
esac
