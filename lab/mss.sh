#!/bin/sh
# TCP MSS clamping: the handshake is lowered so segments fit the path.
#
#   sh tools/lab.sh mss        with clamping configured
#   sh tools/lab.sh mss off    without it - the clamp checks MUST fail
#
# Every value below is read off a CAPTURED packet, never off the trace. A
# clamp with a wrong TCP checksum fails silently - the peer drops the segment
# and the connection hangs exactly as it did before - so the checksum is
# asserted on the same bytes as the value.
#
# OFF-RED-AT-LEAST: 6
#
# A FLOOR, not a target, and the number is measured rather than chosen: it is
# what this lab's off run went red on in the release suite of 2026-09-13.
# tools/run-all-labs.sh refuses an off run that goes red on fewer, because a
# lab that used to fail eleven checks and now fails one has lost ten assertions
# without losing its verdict - which is the defect this whole sweep exists to
# find. Gaining assertions is fine and needs no edit here; losing them is what
# has to be deliberate.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
# The subscriber itself, because pg0's own /22 covers it: without an
# adjacency the translated packet is dropped waiting for ARP, and the capture
# collects the ARP request instead of the packet under test.
vppctl set ip neighbor pg0 100.64.0.5 02:00:00:00:00:05
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0
vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

# 100.64.0.5 is subscriber 5 -> 200.150.0.17, block 1536-2047

# send <src> <dst> <sport> <dport> <flags> <mss|none> <in-interface> <capture-interface>
send() {
  H=$(python3 /src/lab/mksyn.py "$1" "$2" "$3" "$4" "$5" "$6" pad)
  L=$(( ${#H} / 2 - 1 ))
  rm -f /tmp/cap.pcap
  vppctl packet-generator capture "$8" pcap /tmp/cap.pcap count 3 >/dev/null
  vppctl packet-generator new "name m limit 1 node ip4-input interface $7 \
      size ${L}-${L} data { hex $H }" >/dev/null
  vppctl packet-generator enable >/dev/null
  sleep 1
  vppctl packet-generator delete m >/dev/null 2>&1
}
check() { python3 /src/lab/checkmss.py /tmp/cap.pcap "$1" 2>&1; }

echo "=========== 1. before the clamp: the option passes through ==========="
echo "a SYN announcing MSS 1460, which is what a host on a 1500-byte link"
echo "sends. Nothing is configured yet, so it must arrive unchanged - and if"
echo "it does not, every check below is measuring something else."
send 100.64.0.5 8.8.8.8 40001 80 syn 1460 pg0 pg1
BEFORE=$(check 1460)
echo "$BEFORE" | sed 's/^/  /'

echo
echo "=========== 2. configuring the clamp ==========="
if [ "$MODE" = off ]; then
  echo "  MODE=off: no clamp is configured. Every check that claims one was"
  echo "  applied MUST fail."
else
  vppctl bpa tcp-mss lab 1400
  vppctl show bpa pool | grep -i "tcp-mss" | sed 's/^/  /'
  vppctl show bpa pool | grep -qi "tcp-mss      1400" || {
    echo "the clamp was refused; nothing below would mean anything"
    exit 1
  }
fi

echo
echo "=========== 3. outbound: the subscriber's SYN ==========="
echo "expected: mss 1400 on the wire, both checksums valid"
send 100.64.0.5 8.8.8.8 40002 80 syn 1460 pg0 pg1
OUT=$(check 1400)
echo "$OUT" | sed 's/^/  /'

echo
echo "=========== 4. inbound: the server's SYN/ACK ==========="
echo "the other half. This one limits what the SUBSCRIBER sends, and without"
echo "it half the path is still unprotected."
send 8.8.8.8 200.150.0.17 80 1536 syn,ack 1460 pg1 pg0
IN=$(check 1400)
echo "$IN" | sed 's/^/  /'

echo
echo "=========== 5. it only ever lowers ==========="
echo "a SYN already announcing 1200 must arrive as 1200. Raising it would"
echo "announce a segment size the sender never agreed to receive."
send 100.64.0.5 8.8.8.8 40003 80 syn 1200 pg0 pg1
LOWER=$(check 1200)
echo "$LOWER" | sed 's/^/  /'

echo
echo "=========== 6. only the handshake ==========="
echo "an ACK carrying an MSS option is not a handshake and must be left"
echo "alone: rewriting mid-connection changes bytes the peer already has a"
echo "checksum for."
send 100.64.0.5 8.8.8.8 40004 80 ack 1460 pg0 pg1
ACKED=$(check 1460)
echo "$ACKED" | sed 's/^/  /'

echo
echo "=========== 7. a SYN with no MSS option ==========="
echo "left alone on purpose: no option means the sender accepts the 536-byte"
echo "default, which is already smaller than any clamp worth setting."
send 100.64.0.5 8.8.8.8 40005 80 syn none pg0 pg1
NOOPT=$(check none)
echo "$NOOPT" | sed 's/^/  /'

echo
echo "=========== 8. auto, from the interface MTUs ==========="
vppctl set interface mtu packet 1400 pg1 >/dev/null 2>&1
if [ "$MODE" != off ]; then
  vppctl bpa tcp-mss lab auto
fi
# The resolved number is recomputed by the process node, so give it a tick.
sleep 2
AUTO_SHOW=$(vppctl show bpa pool | grep -i "tcp-mss")
echo "  smallest CGNAT interface MTU is 1400, so the clamp must be 1360:"
echo "$AUTO_SHOW" | sed 's/^/  /'
send 100.64.0.5 8.8.8.8 40006 80 syn 1460 pg0 pg1
AUTO=$(check 1360)
echo "$AUTO" | sed 's/^/  /'

echo
echo "=========== 9. what has to be refused ==========="
R_LOW=$(vppctl bpa tcp-mss lab 40 2>&1)
R_HIGH=$(vppctl bpa tcp-mss lab 70000 2>&1)
R_POOL=$(vppctl bpa tcp-mss nosuch 1400 2>&1)
echo "  $R_LOW"
echo "  $R_HIGH"
echo "  $R_POOL"

echo
echo "=========== 10. in the running configuration ==========="
CONF=$(vppctl show bpa config)
echo "$CONF" | grep -E "tcp-mss|cgnat pool" | sed 's/^/  /'

echo
echo "=========== counters ==========="
ERR=$(vppctl show errors)
echo "$ERR" | grep -iE "MSS" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

# The precondition. Without it every "was clamped" check could pass on a box
# that mangles the option for some other reason, and every "was left alone"
# check could pass on a box that cannot see options at all.
expect_in "the option arrived unchanged before anything was configured" \
  "mss=1460" "$BEFORE"
expect_in "and its checksums were valid to begin with" "tcpsum=OK" "$BEFORE"

expect_in "the subscriber's SYN was clamped" "mss=1400" "$OUT"
expect_in "and the TCP checksum survived the rewrite" "tcpsum=OK" "$OUT"
expect_in "and so did the IP checksum" "ipsum=OK" "$OUT"
expect_in "the server's SYN/ACK was clamped too" "mss=1400" "$IN"
expect_in "with valid checksums" "tcpsum=OK" "$IN"

expect_in "a smaller MSS was not raised" "mss=1200" "$LOWER"
expect_in "a mid-connection ACK was left alone" "mss=1460" "$ACKED"
expect_in "a SYN with no MSS option was left alone" "mss=None" "$NOOPT"

expect_in "auto resolved to the smallest interface MTU less 40" \
  "auto -> 1360" "$AUTO_SHOW"
expect_in "and the packet was clamped to it" "mss=1360" "$AUTO"
expect_in "auto kept the checksums valid" "tcpsum=OK" "$AUTO"

expect_in "a clamp below the RFC 1122 floor is refused" "536-9000" "$R_LOW"
expect_in "a clamp above a jumbo frame is refused" "536-9000" "$R_HIGH"
expect_in "an unknown pool is refused" "no such pool" "$R_POOL"

expect_in "the clamp is in the running configuration" " tcp-mss auto" "$CONF"
expect_in "clamped options were counted" "MSS options lowered" "$ERR"

verdict
