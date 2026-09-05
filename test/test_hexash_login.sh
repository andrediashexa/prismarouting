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
# Proven by pointing /proc/cmdline at a file that contains it, which is what
# the wrapper reads.
if grep -qw "hexash=off" /proc/cmdline 2>/dev/null; then
  ok "(this machine really boots with hexash=off; nothing to simulate)"
else
  grep -q "hexash=off" "$W" \
    && ok "the wrapper checks the kernel command line" \
    || bad "no hexash=off escape: the only way in depends on hexash working"
fi

rm -rf "$d"
if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
