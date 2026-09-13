#!/bin/sh
# Does the login wrapper keep the operator out of a locked box?
#
# This is the risk the wrapper exists for. Pointing root's login shell straight
# at hexash means a VTY that fails to start leaves nobody able to reach the
# appliance, over console or SSH - a far worse failure than the one landing in
# the VTY solves.
set -u
fail=0
ok()  { echo "  $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
W=$(cd "$(dirname "$0")/.." && pwd)/appliance/hexash-login

d=$(mktemp -d); mkdir -p "$d/bin"
cat > "$d/bin/fakeshell" <<'EOF'
#!/bin/sh
echo "FELL-BACK-TO-SHELL"
EOF
chmod +x "$d/bin/fakeshell"

echo "=== hexash missing: must fall back, not fail the login ==="
out=$(HEXA_FALLBACK_SHELL="$d/bin/fakeshell" sh "$W" 2>&1)
case "$out" in
  *FELL-BACK-TO-SHELL*) ok "fell back to a shell" ;;
  *) bad "no fallback; got: $out" ;;
esac
case "$out" in
  *"not installed"*) ok "and said why" ;;
  *) bad "fell back silently: the operator cannot tell a bug from a policy" ;;
esac

echo "=== non-interactive login (scp, rsync, git) must reach a real shell ==="
# A login shell is invoked as "-c <command>" for every file copy. If those land
# in the VTY, copying a file to the appliance fails in a way that looks like a
# network problem.
out=$(HEXA_FALLBACK_SHELL=/bin/sh sh "$W" -c 'echo NONINTERACTIVE-OK' 2>&1)
case "$out" in
  *NONINTERACTIVE-OK*) ok "passed through to the shell" ;;
  *) bad "swallowed a non-interactive command; got: $out" ;;
esac

echo "=== hexash=off on the kernel command line bypasses the VTY ==="
# This used to grep the wrapper's own source for the string. The string is in
# the wrapper's header comment and in the message it prints at the bottom, so
# the whole escape - the only way back into a box whose VTY the operator has
# broken - could be deleted and this test stayed green. It has to be run.
#
# /proc/cmdline cannot be written, and a mount namespace would make this test
# red on every machine that does not allow one, which TESTING.md forbids at
# this level. So the kernel's file is stood in for at the single point the
# wrapper reads it: a grep earlier on PATH that swaps that one path for a file
# written here and hands everything else to the real grep. Nothing about the
# wrapper is modified, and if the stand-in is never reached the real grep
# reads the real command line, finds nothing, and these checks go red rather
# than quietly passing.
REAL_GREP=$(command -v grep)
cat > "$d/bin/grep" <<EOF
#!/bin/sh
n=\$#; i=0
while [ \$i -lt \$n ]; do
  a=\$1; shift
  [ "\$a" = /proc/cmdline ] && a=$d/cmdline
  set -- "\$@" "\$a"
  i=\$((i+1))
done
exec $REAL_GREP "\$@"
EOF
chmod +x "$d/bin/grep"

# The escape must be taken, and must be taken BEFORE the wrapper asks whether
# hexash is installed - both branches reach a shell, so what separates them is
# which reason the operator is given for being in one.
echo "hexash=off quiet ro root=/dev/sda2" > "$d/cmdline"
out=$(PATH="$d/bin:$PATH" HEXA_FALLBACK_SHELL="$d/bin/fakeshell" sh "$W" 2>&1)
case "$out" in
  *FELL-BACK-TO-SHELL*) ok "hexash=off reached a shell" ;;
  *) bad "hexash=off did not reach a shell; got: $out" ;;
esac
case "$out" in
  *"kernel command line"*) ok "and named the kernel command line as the reason" ;;
  *) bad "the escape was not the branch taken; got: $out" ;;
esac

# And must NOT be taken otherwise, or the check above is satisfied by a
# wrapper that drops every login to a shell.
echo "quiet ro root=/dev/sda2" > "$d/cmdline"
out=$(PATH="$d/bin:$PATH" HEXA_FALLBACK_SHELL="$d/bin/fakeshell" sh "$W" 2>&1)
case "$out" in
  *"kernel command line"*) bad "the escape fired without hexash=off on it" ;;
  *) ok "an ordinary command line does not take the escape" ;;
esac

# -w, not a substring. A kernel argument that merely contains the string is
# somebody else's option, and honouring it would drop the box to a shell on a
# boot nobody asked to bypass the VTY on.
echo "quiet nohexash=offline ro" > "$d/cmdline"
out=$(PATH="$d/bin:$PATH" HEXA_FALLBACK_SHELL="$d/bin/fakeshell" sh "$W" 2>&1)
case "$out" in
  *"kernel command line"*) bad "hexash=off matched inside another argument" ;;
  *) ok "the match is a whole word, not a substring" ;;
esac

rm -rf "$d"
if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
