#!/bin/sh
# Stand-in for `claude`, for the browser sign-in's tests: tests/unit/
# test_claude_login.py runs it on the controller, and the configure scenario's
# side effect puts it in place of the real one on the container. Its state is
# under $HOME/.claude/fake/, and every call is appended to calls.log there - the
# arguments, and for the sign-in what happened, but never a line it read.
#
#   auth status     the JSON `claude auth status` prints: signed in through
#                   claude.ai once .credentials.json exists, signed out before
#   auth login      what Claude Code 2.1.296 does with no terminal: prints the
#                   sign-in page and its prompt, then reads lines from stdin. A
#                   line without "#" prints "Invalid code" and it reads again;
#                   with fake-reject present it prints "Login failed" and exits
#                   1; otherwise it writes .credentials.json, prints "Login
#                   successful." and exits 0. A TERM is logged as "TERM".
#                   Three files stand in for what else claude can do, each
#                   printed as it is, escape sequences included:
#                     fake-refusal  its other answers to a code, such as an
#                                   account on hold: printed, then exit 1
#                     fake-no-page  an error before the page: printed in its
#                                   place, then exit 1
#                     fake-gone     an attempt that ended: exit 1 right after
#                                   the prompt, reading nothing
#   remote-control  stays up, as the server does
#   --version       a version
state="$HOME/.claude/fake"
mkdir -p "$state"
printf '%s\n' "$*" >> "$state/calls.log"
credentials="$HOME/.claude/.credentials.json"

case "$1 $2" in
  "auth status")
    if [ -s "$credentials" ]; then
      echo '{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty", "email": "dev@example.com", "subscriptionType": "max"}'
      exit 0
    fi
    echo '{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty"}'
    exit 1
    ;;
  "auth login")
    trap 'echo TERM >> "$state/calls.log"; exit 143' TERM
    if [ -e "$state/fake-no-page" ]; then
      cat "$state/fake-no-page" >&2
      exit 1
    fi
    echo "Opening browser to sign in…"
    echo "If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&state=fake-state"
    printf 'Paste code here if prompted > '
    if [ -e "$state/fake-gone" ]; then
      echo "gone" >> "$state/calls.log"
      exit 1
    fi
    while IFS= read -r line; do
      code=${line%%#*}
      rest=${line#*#}
      if [ "$line" = "$code" ] || [ -z "$code" ] || [ -z "$rest" ]; then
        echo "invalid" >> "$state/calls.log"
        echo "Invalid code. Please make sure the full code was copied." >&2
        continue
      fi
      if [ -e "$state/fake-reject" ]; then
        echo "rejected" >> "$state/calls.log"
        echo "Login failed: Request failed with status code 400" >&2
        exit 1
      fi
      if [ -e "$state/fake-refusal" ]; then
        echo "refused" >> "$state/calls.log"
        cat "$state/fake-refusal" >&2
        exit 1
      fi
      (umask 077 && echo '{"claudeAiOauth": {"accessToken": "fake-access-token-0000"}}' > "$credentials")
      echo "accepted" >> "$state/calls.log"
      echo "Login successful."
      exit 0
    done
    echo "eof" >> "$state/calls.log"
    exit 1
    ;;
esac

case "$1" in
  --version) echo "2.1.296 (Claude Code)"; exit 0 ;;
  # Long enough for any check, and a plain number: BSD sleep has no infinity.
  remote-control) exec sleep 2147483647 ;;
esac
echo "fake claude: unexpected arguments: $*" >&2
exit 99
