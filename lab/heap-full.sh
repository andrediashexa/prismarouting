# OFF-RED-AT-LEAST: 3
# A dataplane whose heap fills with sessions refuses the next ones and stays
# up (#460).
#
# Every session, both session hashes and every subscriber record live in
# VPP's main heap, and an allocation there that fails ABORTS the process.
# Before #460 that was the whole box: measured with a 512 MB heap and two
# million sessions offered, VPP was gone within ten seconds, the backtrace
# ending in _vec_realloc_internal from bpa_session_bind, and every subscriber
# lost service together. The heap here is 512 MB so the lab reaches it in
# seconds; on a box it is sized by the tuner and reached by traffic.
#
# The packet generator offers 2,000,000 sessions from 4,000 subscribers at 500
# ports each, which is four times what this heap holds.
#
# MODE=off builds the plugin with BPA_HEAP_GUARD_OFF, the product before the
# guard, and the dataplane must be gone.
. /src/lab/lib.sh
. /src/lab/verdict.sh
set -u
MODE=${1:-on}
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing
if [ "$MODE" = off ]; then
  mkdir -p /tmp/build-off && cd /tmp/build-off \
    && cmake -G Ninja -DCMAKE_C_FLAGS=-DBPA_HEAP_GUARD_OFF /src >/dev/null && ninja >/dev/null
else
  (cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
    mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja >/dev/null
  }
fi
cd /src
sed 's/main-heap-size [0-9]*[MG]/main-heap-size 512M/' lab/startup-lcp.conf > /tmp/startup.conf
vpp -c /tmp/startup.conf > /tmp/vpp.log 2>&1 &
wait_vpp /tmp/vpp.log || exit 1
ip link add vout type veth peer name up0; ip link set vout up; ip link set up0 up
vppctl create host-interface name vout >/dev/null
vppctl set interface name host-vout ens19
vppctl set interface state ens19 up
vppctl set interface ip address ens19 198.51.100.2/24
vppctl ip route add 0.0.0.0/0 via 198.51.100.1 ens19
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface ip address pg0 100.64.0.1/10
vppctl set interface state pg0 up
vppctl bpa pool p1 inside 100.64.0.0/10 outside 203.0.113.0/21 block 512 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in ens19

echo "=========== 2,000,000 sessions offered to a 512 MB heap, MODE=$MODE ==========="
vppctl packet-generator new "{ name ses limit 2000000 size 64-64 node ip4-input interface pg0 data { UDP: 100.64.1.0 - 100.64.16.160 -> 8.8.8.8 UDP: 20000 - 20499 -> 53 incrementing 8 } }" >/dev/null
vppctl packet-generator enable-stream ses
ALIVE=yes
for t in 10 20 30 40; do
  sleep 10
  if ! vppctl show version >/dev/null 2>&1; then ALIVE=no; echo "  t=${t}s: the dataplane is gone"; break; fi
  echo "  t=${t}s: $(vppctl show bpa session 2>&1 | tr -d "\r" | tail -1) | heap $(vppctl show memory main-heap 2>&1 | tr -d "\r" | grep -o 'used: [^,]*, free: [^,]*')"
done
[ "$ALIVE" = no ] && tail -8 /tmp/vpp.log | sed 's/^/  vpp: /'
ERRS=$(vppctl show errors 2>&1 | tr -d "\r" | grep bpa-in2out)
printf '%s\n' "$ERRS" | sed 's/^/  /'
REFUSED=$(printf '%s\n' "$ERRS" | awk '/no memory for a new session/ {print $1}')
CREATED=$(printf '%s\n' "$ERRS" | awk '/sessions created/ {print $1}')

echo; echo "=========== a session made before the heap filled still translates ==========="
vppctl packet-generator new "{ name again limit 10 size 64-64 node ip4-input interface pg0 data { UDP: 100.64.1.0 -> 8.8.8.8 UDP: 20000 -> 53 incrementing 8 } }" >/dev/null 2>&1
BEFORE=$(vppctl show errors 2>&1 | tr -d "\r" | awk '/bpa-in2out.*packets translated/ {print $1}')
vppctl packet-generator enable-stream again >/dev/null 2>&1; sleep 2
AFTER=$(vppctl show errors 2>&1 | tr -d "\r" | awk '/bpa-in2out.*packets translated/ {print $1}')
echo "  packets translated: ${BEFORE:-none} before, ${AFTER:-none} after"

echo; echo "=========== VERDICT ==========="
expect "the dataplane is still up with its heap full" test "$ALIVE" = yes
expect "  and the sessions beyond it were refused and counted" test "${REFUSED:-0}" -gt 0
expect "  while the ones that fit were made" test "${CREATED:-0}" -gt 100000
expect "an existing session still translates with the heap full" \
  test "${AFTER:-0}" -ge $(( ${BEFORE:-0} + 10 ))
verdict
