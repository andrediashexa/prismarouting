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

echo
echo "=== the ISO filename carries the version ==="
# A file called prisma-routing.iso on a USB stick says nothing about what it is,
# and two of them are indistinguishable at exactly the moment it matters.
d2=$(mktemp -d); echo "0.9.9" > "$d2/VERSION"
git -C "$d2" init -q 2>/dev/null
git -C "$d2" config user.email t@t; git -C "$d2" config user.name t
git -C "$d2" add -A >/dev/null 2>&1; git -C "$d2" commit -qm x >/dev/null 2>&1

echo "--- a clean tree standing on its tag: the release name ---"
git -C "$d2" tag -a v0.9.9 -m r >/dev/null 2>&1
out=$(sh "$STAMP" --filename "$d2")
case "$out" in
  "prisma-routing-0.9.9.iso") ok "$out" ;;
  *) bad "the released tree must produce the bare versioned name, got '$out'" ;;
esac

echo "--- a clean tree that is NOT the release: names its commit ---"
# One commit past the tag is a different image with the same version. Sharing
# the release's filename is how it gets installed instead of the release.
echo x > "$d2/other"; git -C "$d2" add -A >/dev/null 2>&1
git -C "$d2" commit -qm y >/dev/null 2>&1
out=$(sh "$STAMP" --filename "$d2")
case "$out" in
  "prisma-routing-0.9.9-"*.iso)
    [ "${out#*dirty}" = "$out" ] && ok "$out" || bad "a clean tree marked dirty: $out" ;;
  "prisma-routing-0.9.9.iso")
    bad "a tree that is not the release took the release's name: $out" ;;
  *) bad "expected a versioned name with a commit, got '$out'" ;;
esac

echo "--- a dirty tree has to say so ---"
# The image nobody must mistake for a release is the one built from
# uncommitted changes.
echo "0.9.9-wip" > "$d2/VERSION"
out=$(sh "$STAMP" --filename "$d2")
case "$out" in
  *dirty.iso) ok "$out" ;;
  *) bad "a dirty build produced '$out': it does not declare itself" ;;
esac

echo "--- every form still carries the version ---"
for form in "$d" "$d2"; do
  out=$(sh "$STAMP" --filename "$form")
  v=$(cat "$form/VERSION")
  case "$out" in
    *"$v"*) ;;
    *) bad "'$out' does not contain the version '$v'" ;;
  esac
done
ok "the version is in the name in every case"

rm -rf "$d2"
echo "=== running it twice does not grow the account's git configuration ==="
# This script is run by every build, every release gate, every lab that stamps
# an image and every make test. It marks the repository as a safe directory so
# git inside the build container will answer at all, and it used to do that
# with "--add" unconditionally: one line per run, for ever, in a file that
# belongs to whoever runs the build rather than to this product. Measured
# before the fix on the machine this was found on: 2325 copies of the same
# entry in a ~/.gitconfig with four other lines in it (#220).
#
# HOME is redirected so this reads the copy the test made and never the real
# one. GIT_CONFIG_GLOBAL is set too, because git honours it ahead of HOME and a
# test that only moved HOME would quietly measure the developer's own file -
# and then pass, because that file already carries the entry.
h=$(mktemp -d)
d3=$(mktemp -d); echo "0.4.2" > "$d3/VERSION"
git -C "$d3" init -q 2>/dev/null
git -C "$d3" config user.email t@t; git -C "$d3" config user.name t
git -C "$d3" add -A >/dev/null 2>&1; git -C "$d3" commit -qm x >/dev/null 2>&1
HOME="$h" GIT_CONFIG_GLOBAL="$h/.gitconfig" sh "$STAMP" "$d3" >/dev/null 2>&1
first=$(grep -c . "$h/.gitconfig" 2>/dev/null || echo 0)
for _ in 1 2 3 4 5; do
  HOME="$h" GIT_CONFIG_GLOBAL="$h/.gitconfig" sh "$STAMP" "$d3" >/dev/null 2>&1
done
after=$(grep -c . "$h/.gitconfig" 2>/dev/null || echo 0)
# The first run is allowed to write the entry; that is what it is for. What
# must not happen is the five after it writing five more.
if [ "$first" -eq 0 ]; then
  bad "the first run wrote no safe.directory at all, so this measures nothing"
elif [ "$after" -eq "$first" ]; then
  ok "six runs, $after line(s): the entry is written once and then found"
else
  bad "the configuration grew from $first line(s) to $after over five more runs: $(( after - first )) duplicate(s) per five runs, in a file this product does not own"
fi
rm -rf "$h" "$d3"

rm -rf "$d" "$empty"
echo "=== iso_name, on its own ==="
# The three spellings of an image's name, from the function that owns them.
# It is cut out of the script and called alone, as test/test_run_all_labs.sh
# does with _jobs_by_mem: the script goes to git on the line after it.
u=$(mktemp -d)
sed -n '/^iso_name() {/,/^}/p' "$STAMP" > "$u/iso_name.sh"
got=$(. "$u/iso_name.sh"
      iso_name 0.26.0 ""
      iso_name 0.26.0 a1b2c3d
      iso_name 0.26.0 a1b2c3d-dirty)
[ "$got" = "hexarouting-0.26.0.iso
hexarouting-0.26.0-a1b2c3d.iso
hexarouting-0.26.0-a1b2c3d-dirty.iso" ] \
  && ok "iso_name: the bare name for a release, the commit for another tree, \
and -dirty for one built from uncommitted changes" \
  || bad "iso_name gave:
$got"
rm -rf "$u"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
