#!/bin/sh
# appliance/schedule-vty-refresh, against a stubbed systemd-run (#529): the
# upgrade artifact's own request for the VTY's refresh, made only onto a box
# systemd runs, and said on the one line run_upgrade() reads.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)
BIN=$REPO/appliance/schedule-vty-refresh
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
mkdir -p "$W/bin" "$W/systemd"
printf '#!/bin/sh\necho refresh\n' > "$W/refresh"
chmod +x "$W/refresh"
stub() {  # <exit status of systemd-run>
  printf '#!/bin/sh\necho "$*" >> %s/argv\nexit %s\n' "$W" "$1" > "$W/bin/systemd-run"
  chmod +x "$W/bin/systemd-run"
  : > "$W/argv"
}
run() {  # [DESTDIR]
  DESTDIR="${1:-}" PATH="$W/bin:$PATH" PRISMA_REFRESH_VTY="$W/refresh" \
    PRISMA_SYSTEMD_DIR="$W/systemd" sh "$BIN" 2>&1
}
WANT=$(python3 -c "import sys; sys.path.insert(0, '$REPO/src/prismash'); import prismash; print(prismash.REFRESH_SCHEDULED)" 2>/dev/null)

echo "=== onto a box systemd runs: scheduled, detached, and said ==="
stub 0
OUT=$(run)
case "$(cat "$W/argv")" in
  *"--on-active=3 --unit=prisma-routing-refresh-vty-upgrade $W/refresh"*)
    ok "refresh-the-vty is scheduled a few seconds on, under its own unit" ;;
  *) bad "systemd-run was asked: '$(cat "$W/argv")'" ;;
esac
case "$OUT" in
  "$WANT"*) ok "and the line run_upgrade() reads is printed" ;;
  *) bad "printed '$OUT', where run_upgrade() looks for '$WANT'" ;;
esac

echo "=== and nowhere else, saying nothing ==="
stub 0
OUT=$(run /tmp/elsewhere)
[ -z "$OUT" ] && [ ! -s "$W/argv" ] \
  && ok "installing into a DESTDIR asks for nothing" \
  || bad "a DESTDIR install asked: '$(cat "$W/argv")' '$OUT'"
stub 1
OUT=$(run)
[ -z "$OUT" ] && ok "a systemd-run that refused is not reported as scheduled" \
  || bad "a refused schedule printed: '$OUT'"
rmdir "$W/systemd"
stub 0
OUT=$(run)
[ -z "$OUT" ] && [ ! -s "$W/argv" ] \
  && ok "a box systemd does not run asks for nothing" \
  || bad "with no systemd it asked: '$(cat "$W/argv")'"

echo "=== appliance/upgrade runs it, after install.sh ==="
UP=$(cat "$REPO/appliance/upgrade")
I=$(printf '%s\n' "$UP" | grep -n 'appliance/install.sh"' | tail -1 | cut -d: -f1)
S=$(printf '%s\n' "$UP" | grep -n 'appliance/schedule-vty-refresh"' | tail -1 | cut -d: -f1)
[ -n "$I" ] && [ -n "$S" ] && [ "$S" -gt "$I" ] \
  && ok "the upgrade asks once the new release is installed" \
  || bad "appliance/upgrade does not run it after install.sh (install $I, schedule $S)"
printf '%s\n' "$UP" | grep -q 'install.sh" || exit' \
  && ok "and not when install.sh failed" \
  || bad "install.sh's failure no longer stops the upgrade before the request"

echo
[ "$fail" = 0 ] && echo "ALL TESTS PASSED" || echo "$fail FAILURES"
exit "$fail"
