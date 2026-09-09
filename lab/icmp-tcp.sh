#!/bin/sh
# Two holes closed: ICMP errors (PMTUD) and TCP state (RST/FIN).
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0
# Translated traffic comes back TO the subscriber, and 100.64.0.0/22 is a
# connected network on pg0: with no neighbour, VPP gleans and drops while ARP
# is unresolved.
for h in 5 6; do vppctl set ip neighbor pg0 100.64.0.$h 02:00:00:00:01:0$h; done
vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

echo "=========== 1. outbound UDP session, for the ICMP error to refer to ==========="
vppctl packet-generator new "name out limit 1 node ip4-input interface pg0 size 60-60 \
    data { UDP: 100.64.0.5 -> 8.8.8.8 \
           UDP: 12345 -> 53 \
           incrementing 8 }"
vppctl clear trace >/dev/null; vppctl trace add pg-input 5 >/dev/null
vppctl packet-generator enable >/dev/null; sleep 1
vppctl show trace | grep "BPA in2out" | sed 's/^/  /'
vppctl packet-generator delete out >/dev/null 2>&1

echo
echo "=========== 2. ICMP frag-needed da internet ==========="
HEX=$(python3 /src/lab/mkicmperr.py 4.4.4.4 200.150.0.17 8.8.8.8 1536 53)
LEN=$(( ${#HEX} / 2 - 1 ))
echo "  ${LEN}-byte packet; the embedded one says 200.150.0.17:1536 -> 8.8.8.8:53"
echo "  esperado: destino externo e origem embutida viram 100.64.0.5:12345"
# Captures what goes out to the subscriber: the trace shows the fields, but
# only the real packet proves the checksums came out valid.
rm -f /tmp/cap.pcap
vppctl packet-generator capture pg0 pcap /tmp/cap.pcap count 1
vppctl packet-generator new "name icmperr limit 1 node ip4-input interface pg1 \
    size ${LEN}-${LEN} data { hex $HEX }"
vppctl clear trace >/dev/null; vppctl trace add pg-input 5 >/dev/null
vppctl packet-generator enable >/dev/null; sleep 2
vppctl packet-generator delete icmperr >/dev/null 2>&1
vppctl show errors | grep -iE "erros ICMP|malformado" | sed 's/^/    /'
echo "  --- diagnostico da captura ---"
ls -l /tmp/cap.pcap 2>&1 | sed 's/^/    /'
vppctl show trace | grep -E "bpa-out2in|ip4-lookup|pg0|error-drop" | head -5 | sed 's/^/    /'
echo "  --- checking the captured packet ---"
python3 /src/lab/checkicmp.py /tmp/cap.pcap 200.150.0.17 100.64.0.5 12345 2>&1 | sed "s/^/  /"

echo
echo "=========== 3. TCP state: an RST has to release the port ==========="
vppctl bpa timeouts tcp-established 7440 tcp-transitory 240
send_tcp() {
  H=$(python3 /src/lab/mktcp.py 100.64.0.6 8.8.8.8 44444 443 "$1")
  L=$(( ${#H} / 2 - 1 ))
  vppctl packet-generator new "name t limit 1 node ip4-input interface pg0 \
      size ${L}-${L} data { hex $H }"
  vppctl clear trace >/dev/null; vppctl trace add pg-input 3 >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete t >/dev/null 2>&1
  printf "  %-8s -> %s\n" "$1" "$(vppctl show bpa session | tail -1)"
}

echo "  timeouts: estabelecida 7440s, transitoria 240s"
send_tcp syn
send_tcp ack
echo "  (so far the session is established: without handling RST it would last ~2h)"
send_tcp rst
echo "  aguardando a varredura de expiracao..."
n=0
# "sessions", not "sessoes". Line 145 of this same file spells it correctly,
# so the two loops disagreed inside one lab.
until vppctl show bpa session | grep -q "^total: 1 sessions" || [ "$n" -ge 15 ]; do
  sleep 1; n=$((n+1))
done
echo "  apos ${n}s: $(vppctl show bpa session | tail -1)"
echo "  (the UDP session from test 1 remains; the TCP one left via the RST)"

echo
echo "=========== 4. half-open sessions must not hold a port ==========="
# A scan produces one SYN-without-establish per probe. Held for the transitory
# timeout they exhaust the subscriber's own block, and the complaint reaches
# support as "the internet stopped" with a healthy dataplane.
# Created under a long SYN timer, expired under a short one. Sending them at
# 2s would race the first session's expiry against the third send - which is
# how this first measured two of three - and lowering the timer afterwards
# also proves it applies to sessions that already exist.
vppctl bpa timeouts tcp-syn 60 tcp-transitory 600 tcp-established 7440 >/dev/null
echo "  timeouts: syn 60s while creating, transitory 600s"
send_syn() {
  H=$(python3 /src/lab/mktcp.py 100.64.0.7 8.8.8.8 "$1" 80 syn)
  L=$(( ${#H} / 2 - 1 ))
  vppctl packet-generator new "name h$1 limit 1 node ip4-input interface pg0 \
      size ${L}-${L} data { hex $H }"
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete "h$1" >/dev/null 2>&1
}
# The session table reports counts, not rows, so the measurement is the delta.
sess() { vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}'; }
BASE=$(sess)
send_syn 40001; send_syn 40002; send_syn 40003
HALFOPEN=$(( $(sess) - BASE ))
echo "  half-open sessions created: $HALFOPEN (table went $BASE -> $(sess))"
# Now shorten the SYN timer. They must go on it, not on the transitory one -
# without the distinction they sit on 600s and are still here when this gives
# up.
vppctl bpa timeouts tcp-syn 2 >/dev/null
echo "  syn timer lowered to 2s"
n=0
until [ "$(sess)" -le "$BASE" ] || [ "$n" -ge 25 ]; do sleep 1; n=$((n+1)); done
HALFLEFT=$(( $(sess) - BASE ))
echo "  after ${n}s: $HALFLEFT still open"

echo
echo "=========== 5. a released port waits out its hold-down ==========="
# A port freed and handed out again immediately can collide with a delayed
# segment from the connection that just ended: a reset nobody can reproduce.
vppctl bpa timeouts port-holddown 6 udp 2 >/dev/null
vppctl packet-generator new "name q limit 1 node ip4-input interface pg0 \
    size 60-60 data { UDP: 100.64.0.9 -> 8.8.8.8 UDP: 33333 -> 53 incrementing 8 }"
vppctl packet-generator enable >/dev/null; sleep 1
vppctl packet-generator delete q >/dev/null 2>&1
QPORT=$(vppctl show bpa session | grep "100.64.0.9" | head -1 | awk '{print $NF}')
echo "  session created, waiting for it to expire (udp 2s)"
n=0
until [ "$(vppctl show bpa session | grep -c '100.64.0.9' || true)" = 0 ] \
      || [ "$n" -ge 15 ]; do sleep 1; n=$((n+1)); done
echo "  session gone after ${n}s; the port is now in hold-down for 6s"
HELD=$(vppctl show bpa timeouts 2>/dev/null | grep -o "port-holddown [0-9]*" || true)
echo "  ${HELD:-port-holddown not reported}"

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
expect "the translated ICMP error passed every checksum" sh -c "python3 /src/lab/checkicmp.py /tmp/cap.pcap 200.150.0.17 100.64.0.5 12345 >/dev/null 2>&1"
expect_in "RST released the port" "total: 1 sessions" "$(vppctl show bpa session)"

# Half-open sessions are on their own timer. Without the distinction they sit
# on the transitory timeout - 600s here - and are still there.
expect "three half-open sessions were created" test "$HALFOPEN" = "3"
# Guarded: if none were created, "they all expired" is true for the wrong
# reason - which is exactly how this assertion first passed.
expect "the half-open test actually created sessions to expire" \
  test "$HALFOPEN" != "0"
expect "and they expired on the SYN timer, not the transitory one" \
  test "$HALFLEFT" = "0"
expect_in "the hold-down is configurable and reported" "port-holddown" \
  "$(vppctl bpa timeouts 2>&1)"
verdict
