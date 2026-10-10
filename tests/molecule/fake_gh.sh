#!/bin/sh
# Stand-in for gh: the auth commands the github_projects role and the terminal
# make code opens run, and nothing else. The login is a file in the calling
# user's home, so each user has their own, as with gh. Used by the Molecule
# scenarios (installed ahead of /usr/bin/gh on PATH) and by the unit tests.
#
# Seeding a state: write the token to $HOME/.config/gh/fake-token; a file
# fake-source beside it is reported as the token's source (GH_TOKEN, say),
# fake-login as the account's login (default: molecule), and fake-old makes
# `auth status --json` fail the way a gh older than 2.81.0 does. The device
# flow (`auth login ... --web`) prints what gh prints and then approves itself
# after fake-approve-after seconds (default 2), or expires the way gh does when
# fake-expire exists. Every call is appended to calls.log there.
#
# Faithful where it matters: the status JSON carries no token field (gh
# empties it without --show-token, and omits it), so the role has to read the
# token with `auth token`; signed out it exits 0 in JSON mode and 1 in text.
set -eu
state="${HOME:?}/.config/gh"
token_file="$state/fake-token"
mkdir -p "$state"
printf '%s\n' "$*" >> "$state/calls.log"
login=$(cat "$state/fake-login" 2>/dev/null || echo molecule)

case "$*" in
  "auth status --hostname github.com --json hosts")
    if [ -e "$state/fake-old" ]; then
      echo "unknown flag: --json" >&2
      exit 1
    fi
    if [ -s "$token_file" ]; then
      source=$(cat "$state/fake-source" 2>/dev/null || echo "$state/hosts.yml")
      printf '{"hosts":{"github.com":[{"state":"success","active":true,"host":"github.com","login":"%s",' "$login"
      printf '"tokenSource":"%s","scopes":"repo, read:org","gitProtocol":"https"}]}}\n' "$source"
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
    printf '%s\n' "$token" > "$token_file"
    chmod 600 "$token_file" ;;
  "auth login --hostname github.com --git-protocol "*" --web --skip-ssh-key"*)
    echo "! Failed to copy one-time code to clipboard" >&2
    echo "  No clipboard utilities available. Please install xsel, xclip, wl-clipboard or Termux:API add-on for termux-clipboard-get/set." >&2
    echo "! First copy your one-time code: FAKE-C0DE"
    echo "Open this URL to continue in your web browser: https://github.com/login/device"
    if [ -e "$state/fake-expire" ]; then
      sleep 1
      echo "failed to authenticate via web browser: This 'device_code' has expired. (expired_token)"
      exit 1
    fi
    sleep "$(cat "$state/fake-approve-after" 2>/dev/null || echo 2)"
    printf '%s\n' "gho_device0000000000" > "$token_file"
    chmod 600 "$token_file"
    echo "✓ Logged in as $login" ;;
  "auth logout --hostname github.com"|"auth logout --hostname github.com --user "*)
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
