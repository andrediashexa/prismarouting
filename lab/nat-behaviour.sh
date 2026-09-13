#!/bin/sh
#
# OFF-RED-AT-LEAST: 2
#
# A FLOOR, not a target, and the number is measured rather than chosen: it is
# what this lab's off run went red on in the release suite of 2026-09-13.
# tools/run-all-labs.sh refuses an off run that goes red on fewer, because a
# lab that used to fail eleven checks and now fails one has lost ten assertions
# without losing its verdict - which is the defect this whole sweep exists to
# find. Gaining assertions is fine and needs no edit here; losing them is what
# has to be deliberate.
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# RFC 4787 mapping and filtering, asserted rather than reasoned about.
#
# docs/roadmap.md claims this implementation is endpoint-independent in both
# mapping and filtering, and the evidence offered was a hash key: neither the
# in2out nor the out2in key carries a remote address. That is a correct reading
# and it is still only a reading. This project has been wrong before about
# things that were plausible and untested.
#
# The mapping assertions here are expected to pass on master. That is the
# point: they confirm existing behaviour rather than prove new work, and from
# now on a change that breaks it fails a test instead of shipping.
#
# "sh tools/lab.sh nat-behaviour address-dependent" runs it with the restricted
# cone selected from the start. The full-cone assertions MUST fail in that run:
# an assertion that passes under both filtering modes is not testing filtering.
set -u
MODE=${1:-endpoint-independent}
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
# pg1 needs an address of its own, and not for the sake of tidiness: without
# one, IPv4 is not enabled on the interface the packet arrived on, and a
# correctly translated inbound packet dies in ip4-not-enabled AFTER the NAT
# has done its work. The counters say "translated" and the subscriber gets
# nothing - which is exactly the shape of failure this lab exists to catch.
vppctl set interface ip address pg1 200.150.0.1/24
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0
# Translated traffic comes back TO the subscriber over a connected network, so
# without a neighbour VPP gleans and drops while ARP is unresolved - and the
# capture below would be empty for a reason that has nothing to do with NAT.
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
vppctl set ip neighbor pg0 100.64.0.5 02:00:00:00:01:05
vppctl bpa pool nb inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa timeouts udp 600 >/dev/null
[ "$MODE" = "endpoint-independent" ] || vppctl bpa filter nb "$MODE"

# Returns the public port the dataplane actually chose. Computing it here
# would duplicate the mapping the test is supposed to be checking, and
# guessing 1024 is how this first looked like a filtering bug when the packet
# was simply aimed at a port nobody was listening on.
pubport() {
  vppctl show trace | grep -oE "200\\.150\\.0\\.1[0-9]:[0-9]+" | head -1 \
    | cut -d: -f2
}

out() {   # source port, destination
  vppctl clear trace >/dev/null; vppctl trace add pg-input 3 >/dev/null
  vppctl packet-generator delete o >/dev/null 2>&1
  vppctl packet-generator new "name o limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: 100.64.0.5 -> $2 UDP: $1 -> 53 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.4
  vppctl packet-generator delete o >/dev/null 2>&1
}
inbound() {  # source address -> our public:port
  vppctl clear trace >/dev/null; vppctl trace add pg-input 3 >/dev/null
  vppctl packet-generator delete i >/dev/null 2>&1
  vppctl packet-generator new "name i limit 1 node ip4-input interface pg1 \
      size 60-60 data { UDP: $1 -> 200.150.0.17 UDP: 53 -> $2 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.4
  vppctl packet-generator delete i >/dev/null 2>&1
}

echo "=========== 1. mapping: one socket, two remotes, ONE external port ==========="
# Endpoint-independent MAPPING (RFC 4787 REQ-1). If the external port changed
# per destination, every peer-to-peer application would break.
out 40000 8.8.8.8
PORT1=$(pubport)
out 40000 1.1.1.1
PORT2=$(pubport)
P1=$(vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}')
echo "  8.8.8.8 -> public port ${PORT1:-?}, 1.1.1.1 -> public port ${PORT2:-?}"
echo "  sessions in total: $P1"

echo
echo "=========== 2. filtering: endpoint-independent (the default) ==========="
# This is the property that makes it a FULL cone: a host the subscriber never
# spoke to reaches an open mapping. Read off the packet delivered to the
# subscriber, not off the trace - the node traces a packet it dropped exactly
# as it traces one it translated, so counting trace lines cannot tell the two
# apart, and the count was previously measured and never asserted at all.
rm -f /tmp/ei.pcap
vppctl packet-generator capture pg0 pcap /tmp/ei.pcap count 1 >/dev/null
inbound 9.9.9.9 "${PORT1:-1536}"
EI_DELIVERED=$(tcpdump -nr /tmp/ei.pcap 2>/dev/null | head -1)
echo "  unsolicited from 9.9.9.9 to an OPEN mapping: ${EI_DELIVERED:-nothing arrived}"
[ -n "${EI_DELIVERED:-}" ] || vppctl show trace | tail -20 | sed "s/^/    /"

echo
echo "=========== 3. filtering: address-dependent ==========="
# Changed in place. Recreating the pool would drop every session, and the
# acceptance criterion is that switching modes does not disconnect anyone.
vppctl bpa filter nb address-dependent
SESS_KEPT=$(vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}')
echo "  sessions surviving the mode change: $SESS_KEPT"
out 40001 8.8.8.8
PORT3=$(pubport)
FILT_BEFORE=$(vppctl show errors | grep -i "address-dependent" | awk '{print $1}' | head -1)
echo "  session on public port ${PORT3:-?} spoke only to 8.8.8.8"
inbound 9.9.9.9 "${PORT3:-1536}"
FILT_AFTER=$(vppctl show errors | grep -i "address-dependent" | awk '{print $1}' | head -1)
echo "  filtered counter: ${FILT_BEFORE:-0} -> ${FILT_AFTER:-0}"

echo
echo "=========== VERDICT ==========="
expect "the mapping test measured a real public port" test -n "${PORT1:-}"
expect "one internal socket keeps one external port across two remotes" \
  test "$PORT1" = "$PORT2"
# Full cone, asserted rather than reasoned about. A change that turned the
# default into a restricted cone would break every peer-to-peer application
# behind this box and, until this line existed, pass the whole suite.
expect "endpoint-independent filtering lets an uninvited remote through" \
  test -n "${EI_DELIVERED:-}"
expect_in "and it arrives at the subscriber's own address and port" \
  "100.64.0.5.40000" "${EI_DELIVERED:-}"
expect "address-dependent filtering drops an uninvited remote" \
  test "${FILT_AFTER:-0}" != "${FILT_BEFORE:-0}"
expect_in "the pool reports its filtering mode" "address-dependent" \
  "$(vppctl show bpa config)"
expect "changing the mode kept the existing sessions" test "${SESS_KEPT:-0}" -ge 1
verdict
