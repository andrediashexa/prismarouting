#!/bin/sh
# Two holes closed: ICMP errors (PMTUD) and TCP state (RST/FIN).
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
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
# The counter names are the ones bpa_errors.h prints, in English. This line
# used to grep for "erros ICMP|malformado", which no counter has ever been
# called, so the section's only diagnostic printed nothing on a working box
# and nothing on a broken one alike.
vppctl show errors | grep -iE "ICMP error|identifiable subscriber|owning worker|another worker" \
  | sed 's/^/    /'
# Where the out2in handoff sent it. Nothing else has crossed out2in yet in
# this lab, so these counters are about the ICMP error and nothing else.
#
# An ICMP error names its session in the EMBEDDED header - the outer one is a
# router talking to our public address, with no port anywhere in it. A handoff
# that cannot read that leaves the packet on the worker the NIC picked, and
# bpa-out2in then looks for the session in a table that never held it (#201).
NO_OWNER=$(vppctl show errors \
           | sed -n 's/^ *\([0-9][0-9]*\) *bpa-out2in-handoff *packets with no identifiable subscr.*/\1/p')
HANDED=$(vppctl show errors \
         | sed -n 's/^ *\([0-9][0-9]*\) *bpa-out2in-handoff *packets handed to another worker.*/\1/p')
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
# Captured where it is measured. The assertion below used to read the session
# table again from the VERDICT block, at the end of the file, so "the RST
# released the port" was really "nothing between here and the end of the lab
# changed the count" - and section 5 opening one session of its own was
# enough to fail it. A reading taken three sections away from what it is
# about is not evidence about it.
RSTSESS=$(vppctl show bpa session)
echo "  apos ${n}s: $(printf '%s\n' "$RSTSESS" | tail -1)"
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
vppctl bpa timeouts port-holddown 60 udp 2 tcp-syn 60 tcp-established 7440 >/dev/null
# The subscriber keeps one TCP session open through all of this, for two
# reasons, and neither is decoration.
#
# It gives the expiry wait below a baseline that does not move. Measured
# without it: the loop was satisfied on its first read, waited 0s, and the
# section went on to compare two ports while the first flow was still alive -
# which is a comparison about nothing, and is the shape the rest of this
# section exists to get rid of.
#
# And bpa_sub_put() frees a subscriber's port map when its LAST session ends,
# quarantined ports and all. Whatever that means for the hold-down is the
# plugin's business and not this section's subject; asserting across that
# boundary would be asserting the wrong thing about the wrong code.
anchor() {
  H=$(python3 /src/lab/mktcp.py 100.64.0.9 8.8.8.8 44499 443 "$1")
  L=$(( ${#H} / 2 - 1 ))
  vppctl packet-generator delete a >/dev/null 2>&1
  vppctl packet-generator new "name a limit 1 node ip4-input interface pg0 \
      size ${L}-${L} data { hex $H }"
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete a >/dev/null 2>&1
}
anchor syn; anchor ack
# The public port, off the trace, because there is nowhere else to read it:
# "show bpa session" prints a count per thread and a total and no rows at all.
# The line this replaces read the port out of that output with grep and awk
# and captured an empty string on every run there has ever been - and so did
# the loop below it, which waited for a subscriber to leave an output it was
# never in and therefore waited for nothing. This section has been reporting
# "session gone after 0s" and asserting neither the port nor the wait (#204).
qsend() {   # source port -> the public port it was translated to
  vppctl packet-generator delete q >/dev/null 2>&1
  vppctl packet-generator new "name q limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: 100.64.0.9 -> 8.8.8.8 UDP: $1 -> 53 incrementing 8 }"
  vppctl clear trace >/dev/null; vppctl trace add pg-input 3 >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete q >/dev/null 2>&1
  vppctl show trace \
    | sed -n 's/.*BPA in2out 100\.64\.0\.9:[0-9]* -> [0-9.]*:\([0-9][0-9]*\) .*/\1/p' \
    | head -1
}
HOLDING=$(sess)
QPORT=$(qsend 33333)
echo "  100.64.0.9:33333 was given public port ${QPORT:-<none>}"
n=0
until [ "$(sess)" -le "$HOLDING" ] || [ "$n" -ge 15 ]; do sleep 1; n=$((n+1)); done
echo "  that session expired after ${n}s; its port is in hold-down for 60s"
# The allocator hands out the lowest free offset in the block, so a port
# handed back the moment its session ends is handed straight out again to the
# next flow. A DIFFERENT port here is the hold-down; the same one is its
# absence.
QPORT2=$(qsend 33334)
echo "  the next flow was given ${QPORT2:-<none>}"
# "port-holddown [0-9]*" never captured the number: the column is printed
# with %6u, so the digits are several spaces away and grep -o returned the
# bare label. Anchored on the row and the width instead.
HELD=$(vppctl show bpa timeouts 2>/dev/null \
       | sed -n 's/^ *port-holddown  *\([0-9][0-9]*\).*/\1/p' | head -1)
echo "  port-holddown reported as: ${HELD:-<not reported>}"

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
expect "the translated ICMP error passed every checksum" sh -c "python3 /src/lab/checkicmp.py /tmp/cap.pcap 200.150.0.17 100.64.0.5 12345 >/dev/null 2>&1"

# This lab runs both ways - plain for a small box, where hexarouting-tune puts
# everything on the main thread, and LAB_WORKERS=1 for every appliance with
# more than one core. Neither configuration proves the other: #201 was a
# translation that was correct single-threaded and a drop with workers, and
# hardcoding workers here would only move the blind spot to the small box.
# tools/run-all-labs.sh is what runs it twice.
#
# Asked of the box and not of LAB_WORKERS: the variable is a request and this
# section is about what the request produced (#229).
if [ "$(box_workers)" -ge 1 ]; then
  # The defect itself. Unfixed, the out2in handoff finds no port in the outer
  # header of an ICMP error, counts it here and leaves it where it landed.
  expect "the out2in handoff identified the subscriber the ICMP error is about" \
    test "${NO_OWNER:-0}" = "0"
  # And the guard that keeps the check above from passing by luck. With one
  # worker, or with two where the session happens to belong to the worker the
  # packet arrived on, the UNFIXED plugin translates this correctly - measured,
  # with LAB_WORKER_COUNT=1 - so a worker run that never crossed a boundary
  # says nothing about #201. Subscriber 100.64.0.5 is index 5, which is odd,
  # so with two workers it is not the worker pg1's input node runs on.
  if [ "${LAB_WORKER_COUNT:-2}" -ge 2 ]; then
    expect "and it really did have to cross a worker to get there" \
      test "${HANDED:-0}" -ge 1
  fi
fi
expect_in "RST released the port" "total: 1 sessions" "$RSTSESS"

# Half-open sessions are on their own timer. Without the distinction they sit
# on the transitory timeout - 600s here - and are still there.
expect "three half-open sessions were created" test "$HALFOPEN" = "3"
# Guarded: if none were created, "they all expired" is true for the wrong
# reason - which is exactly how this assertion first passed.
expect "the half-open test actually created sessions to expire" \
  test "$HALFOPEN" != "0"
expect "and they expired on the SYN timer, not the transitory one" \
  test "$HALFLEFT" = "0"
# The label, not the value. "port-holddown" is a column heading that
# "bpa timeouts" prints on every box whatever the setting is, so this passed
# without ever looking at the value set above - the one thing the section is
# about. Asserted on the number the box reports back.
expect "the hold-down the lab set is the one the box reports" \
  test "${HELD:-}" = "60"
# The hold-down itself, and not the number it is configured with. Both flows
# have to have been translated at all, or "the two ports differ" is true
# because neither exists - which is how the assertion above first passed.
expect "the flow whose port goes into hold-down was translated" \
  test -n "${QPORT:-}"
expect "and so was the one that followed it" test -n "${QPORT2:-}"
expect "a port in hold-down is not handed straight back out" \
  test "${QPORT:-x}" != "${QPORT2:-y}"
verdict
