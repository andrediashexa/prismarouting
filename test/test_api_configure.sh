#!/bin/sh
# appliance/api-configure (#532): the listener on the management network,
# written from api.env and never from anything api.env could inject.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
fail=0
ok()  { echo "  [ ok ] $1"; }
bad() { echo "  FAIL: $1"; fail=$((fail + 1)); }
mkdir -p "$W/bin" "$W/units"
cat > "$W/bin/systemctl" <<'SH'
#!/bin/sh
echo "$*" >> "$CALLS"
SH
chmod +x "$W/bin/systemctl"
run() {  # run <api.env text>
  printf '%s' "$1" > "$W/api.env"
  : > "$W/calls"
  OUT=$(CALLS="$W/calls" PRISMA_API_ENV="$W/api.env" PRISMA_API_UNIT_DIR="$W/units" \
        PRISMA_SYSTEMCTL="$W/bin/systemctl" sh "$REPO/appliance/api-configure" 2>&1)
  RC=$?
}
U="$W/units/prisma-routing-api-proxy.socket"

echo "=== enabled: a socket on the management address ==="
run "API_ENABLED=yes
API_LISTEN=10.40.200.166
API_PORT=9443
"
[ "$RC" = 0 ] && ok "it applies" || bad "exit $RC: $OUT"
grep -qx "ListenStream=10.40.200.166:9443" "$U" 2>/dev/null \
  && ok "the socket listens on the address and port the VTY named" \
  || bad "no ListenStream=10.40.200.166:9443 in $(cat "$U" 2>/dev/null)"
grep -q "restart prisma-routing-api-proxy.socket" "$W/calls" \
  && grep -q "daemon-reload" "$W/calls" && ok "reloaded and restarted" \
  || bad "systemctl was asked: $(cat "$W/calls")"
# #584: systemd refuses to restart a socket whose service runs, so the proxy
# is stopped FIRST - on .166 every change of a running API's address rolled
# back without it.
_stop=$(grep -n "stop prisma-routing-api-proxy.service" "$W/calls" | head -1 | cut -d: -f1)
_rst=$(grep -n "restart prisma-routing-api-proxy.socket" "$W/calls" | head -1 | cut -d: -f1)
[ -n "$_stop" ] && [ -n "$_rst" ] && [ "$_stop" -lt "$_rst" ] \
  && ok "the proxy is stopped before its socket is restarted (#584)" \
  || bad "the proxy was not stopped before the socket restart: $(cat "$W/calls")"
case "$OUT" in *"listens on 10.40.200.166:9443"*) ok "and says where" ;; *) bad "said: $OUT" ;; esac

echo "=== an IPv6 address is bracketed ==="
run "API_ENABLED=yes
API_LISTEN=2001:db8::1
API_PORT=50051
"
grep -qx "ListenStream=\[2001:db8::1\]:50051" "$U" && ok "[2001:db8::1]:50051" \
  || bad "$(grep ListenStream "$U")"

echo "=== IPv4 and IPv6 together: one ListenStream each (#582) ==="
# 10.40.200.166: the API had to answer on the management interface's IPv6
# address as well as its IPv4 one, and api.env carried one address.
run "API_ENABLED=yes
API_LISTEN=10.40.200.166 2804:4ed4:1000:6::166
API_PORT=50051
"
_ls=$(grep -c "^ListenStream=" "$U" 2>/dev/null)
grep -qx "ListenStream=10.40.200.166:50051" "$U" \
  && grep -qx "ListenStream=\[2804:4ed4:1000:6::166\]:50051" "$U" && [ "$_ls" = 2 ] \
  && ok "10.40.200.166:50051 and [2804:4ed4:1000:6::166]:50051" \
  || bad "the listeners: $(grep ListenStream "$U" 2>/dev/null)"
case "$OUT" in *"10.40.200.166:50051 [2804:4ed4:1000:6::166]:50051"*) ok "and says both" ;;
  *) bad "said: $OUT" ;; esac
run "API_ENABLED=yes
API_LISTEN=10.40.200.166 10.0.0.1;reboot
API_PORT=9443
"
[ ! -e "$U" ] && ok "one untrustworthy word in the list is no listener at all" \
  || bad "a socket from: $(cat "$U")"

echo "=== off, or anything it cannot trust, is no listener ==="
run "API_ENABLED=no
API_LISTEN=10.40.200.166
API_PORT=9443
"
[ ! -e "$U" ] && grep -q "stop prisma-routing-api-proxy.socket" "$W/calls" \
  && ok "off removes the socket and stops the three units" || bad "off left $(ls "$W/units")"
run "API_ENABLED=yes
API_LISTEN=10.0.0.1;reboot
API_PORT=9443
"
[ ! -e "$U" ] && ok "an address with anything but an address in it is no listener" \
  || bad "a socket from: $(cat "$U")"
run "API_ENABLED=yes
API_LISTEN=10.0.0.1
API_PORT=99999
"
[ ! -e "$U" ] && ok "a port no TCP has is no listener" || bad "a socket on port 99999"
run "API_ENABLED=yes
API_PORT=9443
"
[ ! -e "$U" ] && ok "enabled with no address is no listener" || bad "a socket with no address"
printf 'API_ENABLED=yes\n$(touch %s/pwned)\n' "$W" > "$W/api.env"
CALLS="$W/calls" PRISMA_API_ENV="$W/api.env" PRISMA_API_UNIT_DIR="$W/units" \
  PRISMA_SYSTEMCTL="$W/bin/systemctl" sh "$REPO/appliance/api-configure" >/dev/null 2>&1
[ ! -e "$W/pwned" ] && ok "api.env is read as data, never run" || bad "a line of api.env ran"

echo "=== --wait gives up, and says so ==="
# The script waits on the fixed backplane address; here the check is that a
# missing server is said, not hung on: 128.0.0.2 does not exist on this host.
timeout 30 sh "$REPO/appliance/api-configure" --wait > "$W/wait" 2>&1
RC=$?
[ "$RC" != 0 ] && grep -q "nothing answers on 128.0.0.2:50051" "$W/wait" \
  && ok "a server that never answers is said after 20 s, not waited on for ever" \
  || bad "exit $RC: $(cat "$W/wait")"

echo
[ "$fail" = 0 ] && echo "ALL TESTS PASSED" || echo "$fail FAILURES"
exit "$fail"
