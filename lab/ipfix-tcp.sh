#!/bin/sh
# TCP transport for the log: the test that matters is killing the collector.
#
# Over UDP, events produced while the collector is down vanish silently - and
# losing one natEvent 17 recreates the gap that makes a court order name the
# wrong subscriber. Here they have to go to disk and come back on reconnect.
set -u
mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out

# hexash reaches the dataplane over the binary API, and this lab now drives it
# to reload a saved configuration. Placed where vat2 looks, which is where a
# correctly installed box has it - see
# https://github.com/andrediashexa/hexarouting/issues/119, where it turned out
# the ISO does not put it there.
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
use_binary_api

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl create host-interface name veth-vpp >/dev/null
vppctl set interface state host-veth-vpp up
# The TCP exporter uses a kernel socket, not VPP's dataplane: the collector has
# to be reachable through the Linux stack. On a real box linux_cp provides that
# path, mirroring the interface as a TAP - and the pair is created here the way
# the box creates it, through the VTY over the binary API, rather than with
# `vppctl lcp create` (#182). While that line was here, this lab proved a
# transport that worked only on a box somebody had paired by hand.
python3 /src/src/hexash/hexash.py -c "configure" \
    -c "interface host-veth-vpp" -c "linux-name vppmgmt0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'
vppctl set interface mtu 1500 host-veth-vpp
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $PEERMAC

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/32 block 16384 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa block-holddown 3
vppctl bpa logging tcp 10.0.0.2 port 4740

burst() {
  for sub in "$@"; do
    vppctl packet-generator new "name b$sub limit 1 node ip4-input interface pg0 size 60-60 \
        data { UDP: 100.64.0.$sub -> 8.8.8.8 UDP: 40000 -> 53 incrementing 8 }"
  done
  vppctl packet-generator enable >/dev/null; sleep 1
  for sub in "$@"; do vppctl packet-generator delete b$sub >/dev/null 2>&1; done
}

echo "=========== PHASE 1: collector up ==========="
ip netns exec peer python3 /src/tools/ipfix_collector.py tcp 4740 12 > /tmp/c1.out 2>&1 &
C1=$!
sleep 3
burst 5
sleep 3
vppctl show bpa logging | grep -A5 "transporte TCP" | sed 's/^/  /'
wait $C1
echo "  --- received (raw collector output) ---"
cat /tmp/c1.out | sed 's/^/    /'

echo
echo "=========== PHASE 2: collector KILLED, events keep coming ==========="
echo "  (with no collector: the events have to go to the spill file)"
vppctl bpa timeouts udp 2
burst 6 9
sleep 8
# The needles were in Portuguese while the plugin prints in English: this line
# never showed anything, so the diagnosis came out empty in exactly the phase
# that kills the collector.
vppctl show bpa logging | grep -E "collector|spill|pending" | sed 's/^/  /'
ls -l /var/log/hexarouting/ipfix-spill.bin 2>/dev/null | sed 's/^/  /' || echo "  (nada em disco ainda: buffer em memoria ainda cabe)"

echo
echo "=========== PHASE 3: collector comes back ==========="
ip netns exec peer python3 /src/tools/ipfix_collector.py tcp 4740 15 > /tmp/c2.out 2>&1 &
C2=$!
wait $C2
echo "  --- recebido apos a reconexao (saida crua) ---"
cat /tmp/c2.out | sed 's/^/    /'
echo
vppctl show bpa logging | grep -A5 "transporte TCP" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
C2=$(cat /tmp/c2.out)
expect_in "records arrived after the collector came back" "DEALLOCATION" "$C2"
expect_in "the template was resent on reconnect" "TEMPLATE 256" "$C2"
# The VALUE, not one number it must not be. This asserted "270 pending" was
# absent, which is true of 0 pending, 1 pending and 4000 pending alike - so the
# only check that the spill queue drained was satisfied by a box that drained
# nothing.
PENDING=$(vppctl show bpa logging | tr -d '\r' \
          | sed -n 's/.*[^0-9]\([0-9][0-9]*\) pending.*/\1/p' | head -1)
echo "  still pending: ${PENDING:-0}"
expect "nothing was left pending" test "${PENDING:-0}" -eq 0

# The record's own timestamp, against the box's clock.
#
# A record stamped from VPP's boot clock reads as a plausible time of day and
# is not one, so counting records will never catch it. The skew grows with the
# machine's uptime, which is what makes this assertion impossible to satisfy by
# accident: it fails harder the longer the box has been running.
# https://github.com/andrediashexa/hexarouting/issues/117
worst_skew() {
  printf '%s\n' "$1" | sed -n 's/.*skew=\([+-]*[0-9]*\)s.*/\1/p' |
    tr -d '+-' | sort -n | tail -1
}
# --- the configuration can be read back and reloaded ---
#
# The collector address was applied and never reported, so the running
# configuration - which is read from the box, not remembered - did not carry
# it. Saving and reloading produced a box that looked identical and exported
# nothing, and nothing said so.
# https://github.com/andrediashexa/hexarouting/issues/116
CFG=$(vppctl show bpa config)
expect_in "the TCP collector is in the running configuration" \
  "cgnat logging tcp 10.0.0.2 port 4740" "$CFG"

# The round trip is the assertion that matters: reading it back is only useful
# if what comes back can be fed to the box. Turn the exporter off, hand the
# box its own saved configuration, and see the collector return.
printf '%s\n' "$CFG" | grep '^cgnat logging' > /tmp/logging.conf
vppctl bpa logging tcp disable
expect_not_in "the exporter really went away first" "10.0.0.2" \
  "$(vppctl show bpa logging)"
echo "  the saved configuration being reloaded:"
sed 's/^/    /' /tmp/logging.conf
python3 /src/src/hexash/hexash.py --load /tmp/logging.conf > /tmp/reload.log 2>&1
sed 's/^/    reload: /' /tmp/reload.log
expect_in "reloading the saved configuration brought the collector back" \
  "10.0.0.2:4740" "$(vppctl show bpa logging)"

# The pair is CONFIGURATION now (#182), and configuration here is declarative:
# a file that does not name it removes it, exactly as this reload removed the
# MTU from host-veth-vpp - the note is two lines above. Asserted rather than
# left to be discovered, because it is a real consequence of the pair having
# moved into the product: while `vppctl lcp create` made it, no reload could
# touch it. The fragment above is deliberately a slice, one grep of the logging
# lines; hexarouting.service reloads the whole saved file, which names the pair
# and therefore keeps it.
expect_not_in "and a file that does not name the pair takes the pair down" \
  "vppmgmt0" "$(vppctl show lcp)"

SKEW=$(worst_skew "$(cat /tmp/c1.out /tmp/c2.out)")
expect "the record carries the time of day, not time since boot (worst skew ${SKEW:-none}s)" \
  test "${SKEW:-99999}" -lt 120
verdict
