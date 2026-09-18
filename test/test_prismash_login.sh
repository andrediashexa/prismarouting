#!/bin/sh
# Does the login wrapper keep the operator out of a locked box?
#
# This is the risk the wrapper exists for. Pointing root's login shell straight
# at prismash means a VTY that fails to start leaves nobody able to reach the
# appliance, over console or SSH - a far worse failure than the one landing in
# the VTY solves.
set -u
fail=0
ok()  { echo "  $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
W=$(cd "$(dirname "$0")/.." && pwd)/appliance/prismash-login

d=$(mktemp -d); mkdir -p "$d/bin"
cat > "$d/bin/fakeshell" <<'EOF'
#!/bin/sh
echo "FELL-BACK-TO-SHELL"
EOF
chmod +x "$d/bin/fakeshell"

echo "=== prismash missing: must fall back, not fail the login ==="
# PRISMA_PRISMASH points at a path that is not there. It used to point at nothing:
# the wrapper read /usr/bin/prismash outright, so this case was arranged by the
# machine running the suite not having one - green on a build runner, red on
# the appliance itself, over a product nobody changed. And the other side of
# that branch could not be arranged at all, which is the three cases below.
out=$(PRISMA_PRISMASH="$d/bin/no-such-prismash" \
      PRISMA_FALLBACK_SHELL="$d/bin/fakeshell" sh "$W" 2>&1)
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
out=$(PRISMA_FALLBACK_SHELL=/bin/sh sh "$W" -c 'echo NONINTERACTIVE-OK' 2>&1)
case "$out" in
  *NONINTERACTIVE-OK*) ok "passed through to the shell" ;;
  *) bad "swallowed a non-interactive command; got: $out" ;;
esac

echo "=== prismash=off on the kernel command line bypasses the VTY ==="
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
# prismash is installed - both branches reach a shell, so what separates them is
# which reason the operator is given for being in one.
echo "prismash=off quiet ro root=/dev/sda2" > "$d/cmdline"
out=$(PATH="$d/bin:$PATH" PRISMA_FALLBACK_SHELL="$d/bin/fakeshell" sh "$W" 2>&1)
case "$out" in
  *FELL-BACK-TO-SHELL*) ok "prismash=off reached a shell" ;;
  *) bad "prismash=off did not reach a shell; got: $out" ;;
esac
case "$out" in
  *"kernel command line"*) ok "and named the kernel command line as the reason" ;;
  *) bad "the escape was not the branch taken; got: $out" ;;
esac

# And must NOT be taken otherwise, or the check above is satisfied by a
# wrapper that drops every login to a shell.
echo "quiet ro root=/dev/sda2" > "$d/cmdline"
out=$(PATH="$d/bin:$PATH" PRISMA_FALLBACK_SHELL="$d/bin/fakeshell" sh "$W" 2>&1)
case "$out" in
  *"kernel command line"*) bad "the escape fired without prismash=off on it" ;;
  *) ok "an ordinary command line does not take the escape" ;;
esac

# -w, not a substring. A kernel argument that merely contains the string is
# somebody else's option, and honouring it would drop the box to a shell on a
# boot nobody asked to bypass the VTY on.
echo "quiet noprismash=offline ro" > "$d/cmdline"
out=$(PATH="$d/bin:$PATH" PRISMA_FALLBACK_SHELL="$d/bin/fakeshell" sh "$W" 2>&1)
case "$out" in
  *"kernel command line"*) bad "prismash=off matched inside another argument" ;;
  *) ok "the match is a whole word, not a substring" ;;
esac

echo "=== a VTY that is THERE and dies: the lockout this wrapper exists for ==="
# THE WHOLE POINT OF THE SCRIPT, and nothing had ever run it. A bad edit, a
# missing Python module, a half-finished upgrade - the VTY exits non-zero and
# the operator has to land in a shell, or nobody reaches the box over console
# or ssh again. Every check in this file ran against a machine with no
# /usr/bin/prismash, so the wrapper had never once got as far as starting a VTY,
# and the fallback under it existed on paper only.
cat > "$d/bin/dying-prismash" <<'EOF'
#!/bin/sh
echo "ModuleNotFoundError: No module named 'config'" >&2
exit 1
EOF
chmod +x "$d/bin/dying-prismash"
out=$(PRISMA_PRISMASH="$d/bin/dying-prismash" \
      PRISMA_FALLBACK_SHELL="$d/bin/fakeshell" sh "$W" 2>&1)
case "$out" in
  *FELL-BACK-TO-SHELL*) ok "a VTY that dies leaves the operator in a shell" ;;
  *) bad "a VTY that died left no shell behind, so the box is unreachable \
over console and ssh both; got: $out" ;;
esac
# The status, not just the fallback. An operator who lands in a shell with no
# reason given cannot tell a broken VTY from a policy that put them there, and
# this is the one console where they have nothing else to read.
case "$out" in
  *"prismash exited with status 1"*) ok "and is told the VTY failed, and with what" ;;
  *) bad "it fell back silently: got: $out" ;;
esac
case "$out" in
  *"prismash=off"*) ok "and how to stop it trying, which is the way back in" ;;
  *) bad "the fallback does not name the escape, so the next login walks \
into the same VTY: $out" ;;
esac

echo "=== and a VTY the operator simply left: that is a logout, not a failure ==="
# The other side of the same branch. "exit" in the VTY must end the session,
# not drop the operator into a shell they did not ask for - a login shell that
# cannot be exited is a box whose ssh sessions never close.
cat > "$d/bin/clean-prismash" <<'EOF'
#!/bin/sh
echo "VTY-RAN"
exit 0
EOF
chmod +x "$d/bin/clean-prismash"
out=$(PRISMA_PRISMASH="$d/bin/clean-prismash" \
      PRISMA_FALLBACK_SHELL="$d/bin/fakeshell" sh "$W" 2>&1)
case "$out" in
  *VTY-RAN*) ok "the VTY is what a login lands in" ;;
  *) bad "the wrapper never started the VTY at all; got: $out" ;;
esac
case "$out" in
  *FELL-BACK-TO-SHELL*) bad "typing 'exit' in the VTY dropped the operator \
into a shell instead of logging them out; got: $out" ;;
  *) ok "and typing 'exit' logs out rather than opening a shell" ;;
esac

rm -rf "$d"
if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
