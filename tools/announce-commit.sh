#!/bin/sh
# Announces a commit on Discord.
#
#   sh tools/announce-commit.sh            the commit at HEAD
#   sh tools/announce-commit.sh <rev>      a named one
#
# WHY THIS EXISTS AS A SCRIPT rather than as a line somebody types: the rule is
# that every commit is announced, and a step that is retyped is a step that is
# eventually skipped - which is the same argument tools/release-gate.sh makes
# about the release procedure, and the reason that file exists.
#
# THE WEBHOOK IS A CREDENTIAL AND IS NOT IN THIS REPOSITORY. Anyone holding the
# URL can post into the channel, and `appliance/build-iso.sh` copies `docs/`
# into the medium - so a webhook written into a document would be published
# inside every ISO, which is precisely what test/test_no_credentials.sh exists
# to prevent. It is read from the environment or from a file in $HOME, the way
# tools/wiki-publish.sh already reads its token:
#
#   DISCORD_WEBHOOK=...            the environment wins
#   ~/.discord-webhook             one line, mode 600
#
# WHAT IT SENDS is the subject, the short hash, the branch and the repository:
# enough for somebody watching to know what landed and to go and read it.
# Never the diff - a commit message is written to be read by people, and a
# diff in a chat channel is neither read nor safe to assume is public.
#
# IT NEVER FAILS THE COMMIT. It is called from a post-commit hook, and a hook
# that can fail is a hook that stops somebody committing when Discord is down
# or the network is out. Every refusal below prints and returns 0.
set -u

REPO=$(cd "$(dirname "$0")/.." && pwd)
REV=${1:-HEAD}

hook=${DISCORD_WEBHOOK:-}
if [ -z "$hook" ] && [ -r "$HOME/.discord-webhook" ]; then
  hook=$(head -1 "$HOME/.discord-webhook")
fi
if [ -z "$hook" ]; then
  echo "announce: no webhook configured, so this commit was not announced."
  echo "          Put it in ~/.discord-webhook (one line, chmod 600), or pass"
  echo "          DISCORD_WEBHOOK. It is a credential: it does not go in this"
  echo "          repository, which ships docs/ inside the medium."
  exit 0
fi

command -v curl >/dev/null 2>&1 || {
  echo "announce: curl is not installed, so this commit was not announced."
  exit 0
}

SUBJECT=$(git -C "$REPO" log -1 --format=%s "$REV" 2>/dev/null) || {
  echo "announce: $REV is not a commit in this repository."
  exit 0
}
SHORT=$(git -C "$REPO" log -1 --format=%h "$REV")
AUTHOR=$(git -C "$REPO" log -1 --format=%an "$REV")
BRANCH=$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")
NAME=$(basename "$REPO")

# The version this tree says it is, because a commit on master between two
# releases and a commit on a branch are different things to be told about.
VERSION=$(cat "$REPO/VERSION" 2>/dev/null || echo "?")

# JSON by hand, so the only thing that has to be escaped is what the message
# carries: a quote, a backslash and a newline. jq is not installed on the
# appliance's build host and this is not worth a dependency.
esc() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' \
                         -e ':a' -e 'N' -e '$!ba' -e 's/\n/\\n/g'
}

PAYLOAD=$(printf '{"content":"**%s** `%s` on `%s` (%s)\\n%s\\n— %s"}' \
  "$(esc "$NAME")" "$(esc "$SHORT")" "$(esc "$BRANCH")" "$(esc "$VERSION")" \
  "$(esc "$SUBJECT")" "$(esc "$AUTHOR")")

# --max-time, because a hook that hangs is a hook that holds a commit open for
# as long as the network is sulking. -s -S so that silence means success and a
# failure still says what it was.
if curl -s -S --max-time 10 -H "Content-Type: application/json" \
        -X POST -d "$PAYLOAD" "$hook" >/dev/null 2>"$REPO/.announce.err"; then
  echo "announce: $SHORT posted"
else
  echo "announce: Discord did not take it, and the commit stands anyway:"
  sed 's/^/          /' "$REPO/.announce.err" 2>/dev/null | head -3
fi
rm -f "$REPO/.announce.err"
exit 0
