#!/bin/sh
# Does the version stamp degrade VISIBLY?
#
# The failure this test exists to prevent has already happened: inside the
# build container git answers "dubious ownership", the calculation swallowed
# the error, and a dirty tree came out stamped as a clean release.
set -u
fail=0
ok()  { echo "  $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
STAMP=$(cd "$(dirname "$0")/.." && pwd)/tools/version-stamp.sh

echo "=== a tree that is not git ==="
d=$(mktemp -d); echo "0.4.2" > "$d/VERSION"
out=$(sh "$STAMP" "$d")
case "$out" in
  "0.4.2 (not a git tree)") ok "$out" ;;
  *) bad "expected 'not a git tree', got '$out'" ;;
esac

echo "=== a clean git tree ==="
git -C "$d" init -q 2>/dev/null
git -C "$d" config user.email t@t; git -C "$d" config user.name t
git -C "$d" add -A >/dev/null 2>&1; git -C "$d" commit -qm x >/dev/null 2>&1
out=$(sh "$STAMP" "$d")
case "$out" in
  "0.4.2 ("*")") [ "${out#*dirty}" = "$out" ] && ok "$out" || bad "a clean tree marked dirty: $out" ;;
  *) bad "expected a version with a hash, got '$out'" ;;
esac

echo "=== a dirty tree has to say it is dirty ==="
echo "0.4.3" > "$d/VERSION"
out=$(sh "$STAMP" "$d")
case "$out" in
  *dirty*) ok "$out" ;;
  *) bad "a dirty tree came out as '$out': such an ISO declares itself a release without being one" ;;
esac

echo "=== with no git on PATH ==="
# Emptying PATH takes sh with it. What is wanted is a PATH without git, not a
# PATH without a shell.
empty=$(mktemp -d)
for t in sh cat dirname mktemp printf; do
  p=$(command -v "$t" 2>/dev/null) && ln -sf "$p" "$empty/$t"
done
out=$(PATH="$empty" sh "$STAMP" "$d")
case "$out" in
  *"no git at build time"*) ok "$out" ;;
  *) bad "with no git it should say so, got '$out'" ;;
esac

rm -rf "$d" "$empty"
if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
