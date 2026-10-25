#!/bin/sh
# Stand-in for gh: the auth commands the github_projects role and the terminal
# just code opens run, and nothing else. The login is a file in the calling
# user's home, so each user has their own, as with gh. Used by the Molecule
# scenarios (installed ahead of /usr/bin/gh on PATH) and by the unit tests.
#
# Seeding a state: write the token to $HOME/.config/gh/fake-token; a file
# fake-source beside it is reported as the token's source (GH_TOKEN, say),
# fake-login as the account's login (default: molecule), and fake-old makes
# `auth status --json` fail the way a gh older than 2.81.0 does (printing the
# file's content instead, when it has any). The device flow (`auth login ...
# --web`) records its pid in fake-pid, prints what gh prints and then approves
# itself after fake-approve-after seconds (default 2). With fake-expire it
# instead gives up as gh does once its code has expired, after the number of
# seconds in that file (default 1) standing in for the code's fifteen minutes;
# with fake-no-code it prints an error (the file's content, or gh's own when
# GitHub cannot be reached) and no code; with fake-hang it prints nothing and
# waits until it is ended; and fake-said is printed in place of its "Logged in
# as" line. With fake-two-accounts, `auth logout` without --user fails as gh
# does with two accounts on github.com and no terminal to ask which. With
# fake-unchecked the login's check never reached GitHub, and with fake-refused
# GitHub refused its token: both state "error", the first with Go's transport
# error and the second with GitHub's "HTTP 401", as gh 2.103.0 reports them
# offline and for a revoked token. Every call is appended to calls.log there.
#
# Faithful where it matters: the status JSON carries no token field (gh
# empties it without --show-token, and omits it), so the role has to read the
# token with `auth token`; signed out it exits 0 in JSON mode and 1 in text.
set -eu

# Waits the way gh does between polls: a TERM ends it at once, as it ends gh,
# rather than leaving a sleep behind.
nap() {
  sleep "$1" &
  nap_pid=$!
  trap 'kill "$nap_pid" 2>/dev/null; exit 143' TERM
  wait "$nap_pid"
  trap - TERM
}

state="${HOME:?}/.config/gh"
token_file="$state/fake-token"
mkdir -p "$state"
printf '%s\n' "$*" >> "$state/calls.log"
login=$(cat "$state/fake-login" 2>/dev/null || echo molecule)

case "$*" in
  "auth status --hostname github.com --json hosts")
    if [ -e "$state/fake-old" ]; then
      if [ -s "$state/fake-old" ]; then cat "$state/fake-old" >&2; else echo "unknown flag: --json" >&2; fi
      exit 1
    fi
    if [ -s "$token_file" ] && { [ -e "$state/fake-unchecked" ] || [ -e "$state/fake-refused" ]; }; then
      if [ -e "$state/fake-unchecked" ]; then
        error='Get \"https://api.github.com/\": dial tcp: lookup api.github.com: no such host'
      else
        error='HTTP 401: Bad credentials (https://api.github.com/)'
      fi
      printf '{"hosts":{"github.com":[{"state":"error","error":"%s","active":true,"host":"github.com",' "$error"
      printf '"login":"%s","tokenSource":"%s/hosts.yml","gitProtocol":"https"}]}}\n' "$login" "$state"
    elif [ -s "$token_file" ]; then
      source=$(cat "$state/fake-source" 2>/dev/null || echo "$state/hosts.yml")
      printf '{"hosts":{"github.com":[{"state":"success","active":true,"host":"github.com","login":"%s",' "$login"
      printf '"tokenSource":"%s","scopes":"gist, read:org, repo","gitProtocol":"https"}]}}\n' "$source"
    else
      printf '{"hosts":{}}\n'
      echo "You are not logged into any GitHub hosts. To log in, run: gh auth login" >&2
    fi ;;
  "auth status")
    [ -s "$token_file" ] || exit 1 ;;
  "auth token --hostname github.com")
    [ -s "$token_file" ] || { echo "no oauth token found for github.com" >&2; exit 1; }
    cat "$token_file" ;;
  "auth login --hostname github.com --git-protocol https --with-token --insecure-storage")
    token=$(head -n 1)
    [ -n "$token" ] || { echo "error validating token: no token on stdin" >&2; exit 1; }
    [ "$token" != "bad-token" ] || { echo "error validating token: HTTP 401: Bad credentials" >&2; exit 1; }
    # A classic token with repo alone: gh asks one for read:org as well.
    case "$token" in ghp_repoonly*) echo "error validating token: missing required scope 'read:org'" >&2; exit 1 ;; esac
    printf '%s\n' "$token" > "$token_file"
    chmod 600 "$token_file" ;;
  "auth login --hostname github.com --git-protocol "*" --web --skip-ssh-key"*)
    echo "$$" > "$state/fake-pid"
    if [ -e "$state/fake-no-code" ]; then
      if [ -s "$state/fake-no-code" ]; then
        cat "$state/fake-no-code" >&2
      else
        printf 'error connecting to api.github.com\ncheck your internet connection or https://githubstatus.com\n' >&2
      fi
      exit 1
    fi
    if [ -e "$state/fake-hang" ]; then
      nap 600
      exit 1
    fi
    echo "! Failed to copy one-time code to clipboard" >&2
    echo "  No clipboard utilities available. Please install xsel, xclip, wl-clipboard or Termux:API add-on for termux-clipboard-get/set." >&2
    echo "! First copy your one-time code: FAKE-C0DE"
    echo "Open this URL to continue in your web browser: https://github.com/login/device"
    if [ -e "$state/fake-expire" ]; then
      # gh polls until its own deadline, the code's lifetime, and says so.
      nap "$(grep . "$state/fake-expire" 2>/dev/null || echo 1)"
      echo "failed to authenticate via web browser: context deadline exceeded" >&2
      exit 1
    fi
    nap "$(cat "$state/fake-approve-after" 2>/dev/null || echo 2)"
    printf '%s\n' "gho_device0000000000" > "$token_file"
    chmod 600 "$token_file"
    # gh's last word, which a later gh may put otherwise (fake-said).
    if [ -e "$state/fake-said" ]; then cat "$state/fake-said" >&2; else echo "✓ Logged in as $login" >&2; fi ;;
  "auth logout --hostname github.com"|"auth logout --hostname github.com --user "*)
    if [ -e "$state/fake-two-accounts" ] && [ "$*" = "auth logout --hostname github.com" ]; then
      echo 'unable to determine which account to log out of, please specify `--hostname` and `--user`' >&2
      exit 1
    fi
    rm -f "$token_file" "$state/fake-source" ;;
  "auth setup-git --hostname github.com")
    [ -s "$token_file" ] || { echo "You are not logged into any GitHub hosts." >&2; exit 1; }
    for host in github.com gist.github.com; do
      git config --global --unset-all "credential.https://$host.helper" || true
      git config --global --add "credential.https://$host.helper" ""
      git config --global --add "credential.https://$host.helper" "!$0 auth git-credential"
    done ;;
  "auth git-credential get")
    [ -s "$token_file" ] || exit 1
    printf 'username=x-access-token\npassword=%s\n' "$(cat "$token_file")" ;;
  *)
    echo "stand-in gh: unexpected arguments: $*" >&2
    exit 99 ;;
esac
