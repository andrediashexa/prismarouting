#!/bin/sh
# appliance/set-hostname against a directory standing in for /etc (#516):
# the three places a rename reaches, the names it refuses whoever asks, and
# that its put() writes in place where a rename cannot replace the file.
#
# The kernel's name is the one thing not measured here - renaming the machine
# running the suite is not a test - so PRISMA_HOSTNAME_CMD records the call.
# lab/hostname.sh renames a real one.
#
# Mutations shown red, one at a time: the awk printing the old 127.0.1.1 line
# too (two lines, "exactly one" goes red); the leading-letter case removed
# ("9box" is taken); the `.-` pattern dropped ("a-.b" is taken).
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fail=0
ok()  { echo "  [ ok ] $1"; }
bad() { echo "  FAIL: $1"; fail=$((fail + 1)); }
PROG="$REPO/appliance/set-hostname"
E=$T/etc
mkdir -p "$E"
printf '#!/bin/sh\necho "$1" > %s/kernel\n' "$T" > "$T/fake-hostname"
chmod +x "$T/fake-hostname"
run() { PRISMA_ETC=$E PRISMA_HOSTNAME_CMD=$T/fake-hostname sh "$PROG" "$@"; }

echo "=== a rename, on a box as the installer left it ==="
echo prisma-routing > "$E/hostname"
printf '127.0.0.1 localhost\n127.0.1.1 prisma-routing\n::1 localhost ip6-localhost\n' > "$E/hosts"
run cgnat-sp1 && ok "it exits 0" || bad "it exited $?"
[ "$(cat "$E/hostname")" = cgnat-sp1 ] && ok "/etc/hostname says the new name" \
  || bad "/etc/hostname says '$(cat "$E/hostname")'"
[ "$(grep -c '^127\.0\.1\.1 ' "$E/hosts")" = 1 ] \
  && grep -qx '127.0.1.1 cgnat-sp1' "$E/hosts" \
  && ok "exactly one 127.0.1.1 line, with the new name" \
  || bad "/etc/hosts: $(tr '\n' '|' < "$E/hosts")"
grep -qx '127.0.0.1 localhost' "$E/hosts" && grep -q '^::1 ' "$E/hosts" \
  && ok "and every other line of /etc/hosts kept" || bad "a line was lost"
[ "$(cat "$T/kernel" 2>/dev/null)" = cgnat-sp1 ] && ok "and the kernel told" \
  || bad "the kernel was told '$(cat "$T/kernel" 2>/dev/null)'"
[ "$(stat -c %a "$E/hosts")" = 644 ] && ok "/etc/hosts stays 0644" \
  || bad "/etc/hosts is $(stat -c %a "$E/hosts")"
ls "$E" | grep -q prisma-new && bad "a temporary file was left" \
  || ok "no temporary file left behind"

echo "=== a hosts file with no 127.0.1.1 line gets one ==="
printf '127.0.0.1 localhost\n' > "$E/hosts"
run edge.example.net >/dev/null 2>&1
grep -qx '127.0.1.1 edge.example.net' "$E/hosts" && ok "added, a dotted name" \
  || bad "/etc/hosts: $(tr '\n' '|' < "$E/hosts")"

echo "=== refused, and nothing written ==="
echo keep > "$E/hostname"
for n in 9box -box box- "a;b" "a b" "a..b" "a-.b" "a.-b" "a." "" \
         "$(printf 'a%.0s' $(seq 65))" "a.$(printf 'b%.0s' $(seq 64))"; do
  if run "$n" >/dev/null 2>&1; then
    bad "'$n' was taken"
  fi
done
run a b >/dev/null 2>&1 && bad "two arguments were taken"
[ "$(cat "$E/hostname")" = keep ] && ok "every bad name refused, /etc/hostname untouched" \
  || bad "/etc/hostname became '$(cat "$E/hostname")'"

echo "=== put(): in place where the rename fails ==="
# A bind-mounted file (a container's /etc/hostname) refuses the rename with
# EBUSY, which a test cannot arrange without mounting; an mv that fails
# stands in for it.
mkdir -p "$T/bin"
printf '#!/bin/sh\nexit 1\n' > "$T/bin/mv"
chmod +x "$T/bin/mv"
PATH="$T/bin:$PATH" run inplace >/dev/null 2>&1
[ "$(cat "$E/hostname")" = inplace ] && ok "written in place when mv cannot" \
  || bad "/etc/hostname says '$(cat "$E/hostname")'"
ls "$E" | grep -q prisma-new && bad "a temporary file was left after the fallback" \
  || ok "and the temporary file removed"

echo
[ "$fail" -eq 0 ] && echo "ALL TESTS PASSED" || echo "$fail FAILURES"
exit $((fail != 0))
