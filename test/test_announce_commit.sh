#!/bin/sh
# tools/announce-commit.sh, driven over what it must refuse and what it must
# never do.
#
# WHY THIS FILE EXISTS. The script is called from a post-commit hook, which is
# the worst place in this repository for a script to be wrong: a hook that
# exits non-zero, hangs, or dies on a missing file is a hook that stops
# somebody committing - and it does it at the moment their attention is on the
# work rather than on the tooling. So what is asserted here is mostly the
# negative: no configuration, no curl, a bad revision and a webhook that
# refuses all have to print something and return 0.
#
# THE WEBHOOK IS NEVER REACHED. Every case below either has no webhook
# configured or points the script at a stub `curl` of this file's own, so
# running the suite posts nothing anywhere - which is also what keeps this
# runnable on a machine that has no network and no credential.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }

# A curl of this suite's own, which records what it was given instead of
# sending it. The script finds it through PATH.
mkdir -p "$WORK/bin"
cat > "$WORK/bin/curl" <<EOF
#!/bin/sh
# Everything, so the payload and the URL can both be read back.
printf '%s\n' "\$@" > "$WORK/curl.args"
exit \${CURL_RC:-0}
EOF
chmod +x "$WORK/bin/curl"

run() {   # <extra env assignments...>; output in OUT, status in RC
  set +e
  OUT=$(env HOME="$WORK/home" PATH="$WORK/bin:$PATH" "$@" \
        sh "$REPO/tools/announce-commit.sh" 2>&1)
  RC=$?
  set -e
}

echo "=== with nothing configured it says so, and does not fail the commit ==="
mkdir -p "$WORK/home"
run DISCORD_WEBHOOK=
[ "$RC" = 0 ] && ok "it returns 0" \
  || bad "a commit with no webhook configured exited $RC"
case "$OUT" in
  *"no webhook configured"*) ok "and says the commit was not announced" ;;
  *) bad "it said nothing about why: $OUT" ;;
esac
case "$OUT" in
  *"credential"*) ok "and says why the URL is not in this repository" ;;
  *) bad "nothing tells the next person where to put it: $OUT" ;;
esac
[ -f "$WORK/curl.args" ] && bad "it called curl with no webhook" \
  || ok "and it did not call curl at all"

echo
echo "=== the environment wins over the file ==="
printf '%s\n' "https://example.invalid/from-the-file" > "$WORK/home/.discord-webhook"
run DISCORD_WEBHOOK=https://example.invalid/from-the-environment
if grep -q "from-the-environment" "$WORK/curl.args" 2>/dev/null; then
  ok "DISCORD_WEBHOOK is used when it is set"
else
  bad "the environment did not win: $(cat "$WORK/curl.args" 2>/dev/null)"
fi
rm -f "$WORK/curl.args"
run DISCORD_WEBHOOK=
if grep -q "from-the-file" "$WORK/curl.args" 2>/dev/null; then
  ok "and ~/.discord-webhook is used when it is not"
else
  bad "the file was not read: $(cat "$WORK/curl.args" 2>/dev/null)"
fi

echo
echo "=== what it sends, and what it must not ==="
# The subject, the short hash and the repository - enough to know what landed.
SUBJECT=$(git -C "$REPO" log -1 --format=%s)
SHORT=$(git -C "$REPO" log -1 --format=%h)
if grep -qF "$SHORT" "$WORK/curl.args" 2>/dev/null; then
  ok "the short hash is in the payload"
else
  bad "a reader cannot find the commit: $(cat "$WORK/curl.args" 2>/dev/null)"
fi
# The subject can carry anything a person typed, so it is checked by its first
# word rather than whole: a quote or a newline in it is escaped, which is the
# next assertion's business.
FIRST=$(printf '%s' "$SUBJECT" | cut -d" " -f1)
if grep -qF "$FIRST" "$WORK/curl.args" 2>/dev/null; then
  ok "and so is the commit's own subject"
else
  bad "the message says nothing about what the commit was"
fi
# NEVER THE DIFF. A commit message is written to be read; a diff in a chat
# channel is neither read nor safe to assume is public.
if grep -qE '^(\+\+\+|---|@@)' "$WORK/curl.args" 2>/dev/null; then
  bad "the payload carries a diff"
else
  ok "and never a diff"
fi

echo
echo "=== a subject with a quote in it is escaped, not broken ==="
# A payload assembled by hand is a payload that can be malformed by the one
# thing it interpolates. Driven on a repository of this suite's own, so the
# subject is this file's to choose.
mkdir -p "$WORK/repo"
(
  cd "$WORK/repo"
  git init -q .
  git config user.email t@example.invalid
  git config user.name "A Tester"
  mkdir -p tools
  cp "$REPO/tools/announce-commit.sh" tools/
  echo 0.0.1 > VERSION
  git add -A
  git commit -q -m 'a subject with "quotes" and a \backslash'
) >/dev/null 2>&1
rm -f "$WORK/curl.args"
set +e
OUT=$(cd "$WORK/repo" && env HOME="$WORK/home" PATH="$WORK/bin:$PATH" \
      DISCORD_WEBHOOK=https://example.invalid/x \
      sh tools/announce-commit.sh 2>&1)
RC=$?
set -e
[ "$RC" = 0 ] && ok "it returns 0" || bad "it exited $RC"
if grep -q '\\"quotes\\"' "$WORK/curl.args" 2>/dev/null; then
  ok "a quote in the subject is escaped for JSON"
else
  bad "the quote was not escaped: $(cat "$WORK/curl.args" 2>/dev/null)"
fi
if python3 -c "
import json, sys
args = open('$WORK/curl.args').read().splitlines()
for i, a in enumerate(args):
    if a == '-d':
        json.loads(args[i + 1]); sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
  ok "and the payload is JSON a parser accepts"
else
  bad "the payload is not valid JSON"
fi

echo
echo "=== esc(): the three things JSON cannot take raw ==="
# Called directly, because what it does is the difference between a payload a
# parser accepts and one Discord drops on the floor - and the case above can
# only see it through a whole run. The function is sourced out of the script
# rather than copied here: a copy is a second escaper that drifts.
# No placeholder definition: test_every_function.py skips a file that defines
# the name it is looking for, which is right - a test that calls its own copy
# names nothing about the product.
eval "$(sed -n '/^esc() {/,/^}/p' "$REPO/tools/announce-commit.sh")"
[ "$(esc 'a "quoted" word')" = 'a \"quoted\" word' ] \
  && ok "a double quote is escaped" \
  || bad "a quote came back as $(esc 'a "quoted" word')"
[ "$(esc 'a \ backslash')" = 'a \\ backslash' ] \
  && ok "and a backslash, which has to go first or it escapes the escape" \
  || bad "a backslash came back as $(esc 'a \ backslash')"
[ "$(printf '%s' "$(esc "$(printf 'two\nlines')")")" = 'two\nlines' ] \
  && ok "and a newline becomes the two characters JSON wants" \
  || bad "a newline came back as $(esc "$(printf 'two\nlines')")"
[ "$(esc 'plain')" = 'plain' ] && ok "and a plain word is left alone" \
  || bad "a plain word was changed"

echo
echo "=== a webhook that refuses does not fail the commit ==="
rm -f "$WORK/curl.args"
run DISCORD_WEBHOOK=https://example.invalid/x CURL_RC=7
[ "$RC" = 0 ] && ok "it returns 0 when curl fails" \
  || bad "a Discord that is down exited $RC, which would stop a commit"
case "$OUT" in
  *"did not take it"*) ok "and says so, rather than claiming it posted" ;;
  *) bad "a failed post was not reported: $OUT" ;;
esac
[ -f "$REPO/.announce.err" ] && bad "it left .announce.err in the tree" \
  || ok "and it leaves nothing behind in the repository"

echo
echo "=== no curl on the machine is a message, not a broken commit ==="
# An EMPTY PATH, not a directory with the stub removed: taking the stub away
# only uncovers the real curl behind it, and the case would then measure a DNS
# failure rather than a missing tool. The check the script makes is
# `command -v curl`, which is a shell builtin and answers with no PATH at all.
# /bin/sh by its absolute path for the same reason: `env` cannot find an
# interpreter either, through a PATH that names one empty directory.
mkdir -p "$WORK/empty"
set +e
OUT=$(env HOME="$WORK/home" PATH="$WORK/empty" \
      DISCORD_WEBHOOK=https://example.invalid/x \
      /bin/sh "$REPO/tools/announce-commit.sh" 2>&1)
RC=$?
set -e
[ "$RC" = 0 ] && ok "it returns 0" || bad "it exited $RC with no curl"
case "$OUT" in
  *"curl is not installed"*) ok "and names what is missing" ;;
  *) bad "it did not say why nothing was sent: $OUT" ;;
esac

echo
if [ "$fail" = 0 ]; then
  echo "ALL TESTS PASSED"
else
  echo "$fail FAILURES"
  exit 1
fi
