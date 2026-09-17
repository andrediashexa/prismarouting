#!/bin/sh
# tools/iso-ready.sh, and the rule it exists to keep.
#
# THE DEFECT (#307). `make test-install` depended on the phony `iso` target,
# so tools/release-gate.sh built a medium, booted it, and then built a SECOND
# medium over the same path and installed that. Measured on the v0.74.0 gate:
# the boot finished at 18:02:23 and the file was rewritten at 18:07:51. The
# gate's argument for the install phase is that booting a medium and installing
# it are different claims about THE SAME MEDIUM, and they were claims about two
# builds. The sha256 the gate printed for publication described the one it had
# never booted.
#
# Three states, and the middle one is the one worth the care. Absent: build.
# Present and current: use it, which is the fix. Present and STALE: build, and
# do not quietly test yesterday's image, because reusing the wrong medium is
# the same defect with the sign flipped.
#
# No ISO is built here and no container is started. The script decides from a
# file's existence and its mtime, and that is what is driven.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# A tree shaped like this one, so version-stamp.sh answers and the find walks
# the same directories the real script walks.
T=$WORK/tree
mkdir -p "$T/tools" "$T/appliance" "$T/src"
cp "$REPO/tools/iso-ready.sh" "$REPO/tools/version-stamp.sh" "$T/tools/"
cat "$REPO/VERSION" > "$T/VERSION"
: > "$T/appliance/build-iso.sh"
: > "$T/src/anything.c"
NAME=$(sh "$T/tools/version-stamp.sh" --filename "$T")

echo "=== the name it looks for is the one this tree would build ==="
case "$NAME" in
  prisma-routing-*.iso) ok "$NAME" ;;
  *) bad "version-stamp.sh --filename answered '$NAME'" ;;
esac

echo
echo "=== no medium here: say so, so the caller builds one ==="
out=$(sh "$T/tools/iso-ready.sh" 2>&1); rc=$?
[ "$rc" != 0 ] \
  && ok "refused, and the caller's '|| make iso' runs" \
  || bad "it claimed a medium that does not exist: $out"
case "$out" in
  *"$NAME"*) ok "and it named the file it was looking for" ;;
  *) bad "it did not say which medium is missing: $out" ;;
esac

echo
echo "=== a medium newer than the tree is used, not rebuilt (#307) ==="
: > "$T/$NAME"
out=$(sh "$T/tools/iso-ready.sh" 2>&1); rc=$?
[ "$rc" = 0 ] \
  && ok "the medium already here is the one that gets installed" \
  || bad "it rebuilt over a current medium, which is the defect: $out"

echo
echo "=== a medium older than the tree is refused, not quietly reused ==="
# The other half. Reusing a stale image trades one wrong medium for another,
# and this one has no boot behind it at all.
touch "$T/appliance/build-iso.sh"
out=$(sh "$T/tools/iso-ready.sh" 2>&1); rc=$?
[ "$rc" != 0 ] \
  && ok "a tree edited since the build gets a new medium" \
  || bad "it reused a medium older than the tree: $out"
case "$out" in
  *build-iso.sh*) ok "and it names the file that moved" ;;
  *) bad "it refused without saying what had changed: $out" ;;
esac

echo
echo "=== the Makefile does not reintroduce the rebuild ==="
# The holder is the recipe itself. A `test-install: iso` prerequisite is the
# defect, whatever iso-ready.sh does, because make runs the prerequisite first.
# EVERY TARGET THAT CONSUMES A MEDIUM, derived rather than listed.
#
# test-mirror kept its `iso` prerequisite through the first fix, which is the
# same defect one target over and the shape that had the release gate refuse
# 0.72.0 twice: two readers of one fact and only one of them corrected. A list
# typed here would be a list somebody forgets to extend.
#
# "Consumes a medium" is not "calls ISO_RUN": hw-down does that and reads no
# image, it just wants the container. It is "runs a script that opens the ISO",
# and the scripts that do are the ones naming the image through
# version-stamp.sh --filename, which is the single place that calculation
# lives. So: find those scripts, then find the Makefile targets that invoke
# them, directly or through tools/test-install.sh.
READERS=$(grep -rlE 'ISO=\$\{ISO:-.*version-stamp\.sh --filename' \
            "$REPO/lab" "$REPO/tools" 2>/dev/null \
          | while read -r f; do basename "$f"; done | sort -u)
[ -n "$READERS" ] \
  || bad "no script was found that opens the medium, so the target list below"\
" is derived from nothing"
# tools/test-install.sh is a wrapper around one of them and a Makefile recipe
# names the wrapper, not the lab.
#
# A WRAPPER RUNS A READER. It does not merely name one, and the difference is
# not academic: tools/release-gate.sh's console-log refusal says "lab/boot-iso.sh
# copies the guest's console there", which is a sentence for whoever reads the
# refusal and not a call. Counting it made `release-gate` a target that opens
# the medium, and the loop below then demanded it consult iso-ready.sh - a
# guard about a PREBUILT image, which the gate does not use because it builds
# its own inside its clone. A needle that matches a label rather than a call is
# one of the shapes docs/TESTING.md names.
#
# So comment lines and message lines are dropped before looking. What is left
# is lines that could execute something.
WRAPPERS=$(for r in $READERS; do
             grep -rl "$r" "$REPO/tools"/*.sh 2>/dev/null \
             | while read -r f; do
                 grep -vE '^[[:space:]]*#|^[[:space:]]*(echo|printf)[[:space:]]' \
                   "$f" | grep -q "$r" && basename "$f"
               done
           done | sort -u)
# Anchored on the path separator and with the dot escaped, because a bare
# `lab.sh` matches `/src/lab/hw-lab.sh` as a substring - hw-lab opens no medium
# and was reported as a target that does. The same substring trap cost a
# document check eleven pages earlier today.
PAT=$(printf '%s\n' $READERS $WRAPPERS | sort -u \
      | sed 's/\./\\./g; s|^|/|' | paste -sd'|')
TARGETS=$(awk -v pat="$PAT" '
  /^[a-z][a-z0-9-]*:/ { t = $0; sub(/:.*/, "", t); next }
  t != "" && t != "iso" && $0 ~ pat { print t; t = "" }
' "$REPO/Makefile" | sort -u)
[ -n "$TARGETS" ] \
  && ok "$(echo "$TARGETS" | wc -l) target(s) open the medium: $(echo $TARGETS)" \
  || bad "no Makefile target was found to open the medium, so the loop below"\
" runs over nothing and this section asserts about no target at all"
for target in $TARGETS; do
  line=$(grep -E "^$target:" "$REPO/Makefile")
  case "$line" in
    "$target:") ok "$target has no ISO prerequisite" ;;
    *) bad "$target depends on '${line#$target:}': make builds that before the"\
" recipe runs, so the medium installed is not the medium booted" ;;
  esac
  grep -A1 -E "^$target:" "$REPO/Makefile" | grep -q "iso-ready.sh" \
    && ok "and asks iso-ready.sh instead" \
    || bad "$target does not consult tools/iso-ready.sh"
done

echo
echo "=== and the gate checks it tested one medium ==="
# Belt and braces, deliberately: the Makefile rule above is the cause this
# knows about, and the gate's own comparison catches any other. A check that
# only knows one cause stops working the day there is a second.
grep -q "MEDIUM_SHA=" "$REPO/tools/release-gate.sh" \
  && grep -q 'the medium was rebuilt between the boot and now' \
       "$REPO/tools/release-gate.sh" \
  && ok "the gate records the medium's sha before the boot and compares it" \
  || bad "tools/release-gate.sh does not compare the medium it booted against"\
" the one it installed"

echo
if [ "$fail" = 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "$fail FAILURES"
exit 1
