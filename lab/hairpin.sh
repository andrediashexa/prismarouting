#!/bin/sh
# Hairpinning (RFC 4787 REQ-9): one subscriber reaching another through its
# public address.
#
# Without it the packet leaves correctly translated and is routed to an address
# this box itself owns, with nothing to send it back in. The symptom is
# specific and hard to attribute: a camera that answers from the street and not
# from next door, a service that works for everyone except the neighbours.
set -u
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0
vppctl bpa pool hp inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa timeouts udp 600 >/dev/null

send() {   # source subscriber, source port, destination address, dest port
  vppctl clear trace >/dev/null; vppctl trace add pg-input 5 >/dev/null
  vppctl packet-generator delete s >/dev/null 2>&1
  vppctl packet-generator new "name s limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: 100.64.0.$1 -> $3 UDP: $2 -> $4 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.5
  vppctl packet-generator delete s >/dev/null 2>&1
}
pubport() {
  vppctl show trace | grep -oE "200\.150\.0\.1[0-9]:[0-9]+" | head -1 | cut -d: -f2
}

echo "=========== B opens a mapping, so it has a public address:port ==========="
send 6 5060 8.8.8.8 53
BPUB=$(pubport)
echo "  100.64.0.6 is reachable at 200.150.0.18:${BPUB:-?}"

echo
echo "=========== A reaches B through B's public address ==========="
send 5 40000 200.150.0.18 "${BPUB:-1536}"
# Captured NOW. The next send clears the trace, and asserting on it at verdict
# time would be reading the wrong packet - which is how this first reported a
# working hairpin as a failure.
HPTRACE=$(vppctl show trace)
echo "  --- trace ---"
printf '%s\n' "$HPTRACE" | grep -A4 "BPA in2out" | head -12 | sed 's/^/    /'
HP=$(vppctl show errors | grep -i "hairpinned" | awk '{print $1}' | head -1)
NOPEER=$(vppctl show errors | grep -i "hairpin to a public" | awk '{print $1}' | head -1)
echo "  hairpinned: ${HP:-0}   no-peer drops: ${NOPEER:-0}"

echo
echo "=========== A reaches a public address with nothing behind it ==========="
send 5 40001 200.150.0.18 9999
NOPEER2=$(vppctl show errors | grep -i "hairpin to a public" | awk '{print $1}' | head -1)
echo "  no-peer drops now: ${NOPEER2:-0}"

echo
echo "=========== VERDICT ==========="
expect "B had a public mapping to be reached at" test -n "${BPUB:-}"
expect "A's packet to B was hairpinned" test "${HP:-0}" -ge 1
expect_in "and B sees it arriving at its private address" "100.64.0.6" "$HPTRACE"
expect_in "with A's PUBLIC address as source, not its private one" \
  "200.150.0.17 -> 100.64.0.6" "$HPTRACE"
# RFC 4787 REQ-9: B must see A as external. A's private address reaching B
# would leak the inside topology and break B's reply path, because B would
# answer to an address it cannot route to.
expect_not_in "A's private address must not reach B" "100.64.0.5 -> 100.64.0.6" \
  "$HPTRACE"
expect "a hairpin to an unused port is dropped, not forwarded" \
  test "${NOPEER2:-0}" -ge 1
verdict
