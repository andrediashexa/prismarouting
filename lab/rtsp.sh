#!/bin/sh
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# RTSP ALG: the Transport header carries the media ports, so behind a NAT they
# name nothing and the video never arrives.
#
# The control connection succeeds either way, which is why this failure is
# never attributed to the CGNAT: an IP camera or streaming box that works on
# the LAN and shows a black frame from outside.
#
# Everything here is asserted from the CAPTURED PACKET, never from the trace or
# a counter. lab/checkicmp.py exists because a translation with a bad checksum
# passes every counter and still fails on the wire, and an ALG has three more
# ways to do that: a payload that grew without the IP length growing with it, a
# TCP checksum computed over the old bytes, and sequence numbers that no longer
# describe where the bytes are.
#
# Every expectation below is written down BEFORE the run, from the pool's own
# arithmetic - not read back out of the packet the ALG produced. The first
# version took the rewritten ports from the capture and compared the rest of
# the lab against them, which passes whatever the ALG does, including nothing.
#
# To watch it fail, which is the only way to know it is a test:
#   BPA_RTSP_OFF=1 sh tools/lab.sh rtsp
set -u
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
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0
# Translated traffic comes back TO the subscriber over a connected network:
# with no neighbour VPP gleans and drops while ARP is unresolved.
for h in 5 6 7 8; do vppctl set ip neighbor pg0 100.64.0.$h 02:00:00:00:01:0$h; done

# Ports 10000-65535 on purpose. With the default 1024-65535 the subscriber's
# block starts at 1536 and the rewritten pair has the same number of digits as
# the one the client asked for - the payload length would never change, and the
# sequence correction, which is the part that fails LATER and looks unrelated,
# would never be exercised.
ALG_ARG=""
[ "${BPA_RTSP_OFF:-}" = "1" ] && ALG_ARG="alg rtsp disable"
vppctl bpa pool rtsp inside 100.64.0.0/22 outside 200.150.0.16/30 \
    block 512 ports 10000-65535 $ALG_ARG
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
# Long enough that nothing expires under us; the expiry test sets its own.
vppctl bpa timeouts udp 600 tcp-established 7440 alg-expect 600 >/dev/null

# What must happen, stated before it does.
#
# The mapping is deterministic: 100.64.0.5 is subscriber index 5, four public
# addresses interleaved put it on 200.150.0.17 with block 1, ports 10512-11023.
# The control connection takes the first port of that block, and the media pair
# takes the first free EVEN offset after it.
EXP_CTRL=10512
EXP_LO=10514
EXP_HI=10515
EXP_PAIR="$EXP_LO-$EXP_HI"
DELTA=2          # "6970-6971" is 9 characters, "10514-10515" is 11

echo "  pool: $(vppctl show bpa pool | grep -E 'alg|ports')"
echo "  subscriber 100.64.0.5 -> 200.150.0.17, ports 10512-11023"
echo "  expected: control on $EXP_CTRL, media pair $EXP_PAIR, payload +$DELTA bytes"

SETUP_LINES='SETUP rtsp://8.8.8.8/track1 RTSP/1.0|CSeq: 3|Transport: RTP/AVP;unicast;client_port=6970-6971'
CSEQ=1000
SSEQ=5000
SLEN=$(python3 /src/lab/mkrtsp.py 100.64.0.5 8.8.8.8 5000 554 \
         --lines "$SETUP_LINES" --payload-len)

# Injects one packet given as hex, capturing what leaves on <iface>.
shoot() {   # <capture-iface> <pcap> <hex>
  rm -f "$2"
  vppctl packet-generator delete p >/dev/null 2>&1
  vppctl packet-generator capture "$1" pcap "$2" count 1 >/dev/null
  L=$(( ${#3} / 2 - 1 ))
  IN=pg0; [ "$1" = pg0 ] && IN=pg1
  vppctl packet-generator new "name p limit 1 node ip4-input interface $IN \
      size ${L}-${L} data { hex $3 }"
  vppctl clear trace >/dev/null; vppctl trace add pg-input 5 >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete p >/dev/null 2>&1
}

# One UDP datagram from the internet to a public port, capturing what reaches
# the subscriber.
shoot_udp() {   # <pcap> <source port> <public port>
  rm -f "$1"
  vppctl packet-generator delete m >/dev/null 2>&1
  vppctl packet-generator capture pg0 pcap "$1" count 1 >/dev/null
  vppctl packet-generator new "name m limit 1 node ip4-input interface pg1 \
      size 60-60 data { UDP: 8.8.8.8 -> 200.150.0.17 \
                        UDP: $2 -> $3 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete m >/dev/null 2>&1
}

# Runs the packet checker, prints what it saw, and RETURNS ITS STATUS.
# Piping it into sed would return sed's status instead, and every assertion
# built on it would pass whatever the checker found.
check() {
  python3 /src/lab/checkrtsp.py "$@" > /tmp/chk.out 2>&1
  _rc=$?
  sed 's/^/  /' /tmp/chk.out
  return $_rc
}

errcount() {  # <node> <reason substring>
  vppctl show errors | awk -v n="$1" -v r="$2" \
    '$0 ~ n && index($0, r) { print $1; exit }' | head -1
}

echo
echo "=========== 1. SETUP: the announced media ports are rewritten ==========="
echo "  the client asks for client_port=6970-6971, which means nothing outside"
HEX=$(python3 /src/lab/mkrtsp.py 100.64.0.5 8.8.8.8 5000 554 \
        --seq $CSEQ --ack $SSEQ --lines "$SETUP_LINES")
shoot pg1 /tmp/setup.pcap "$HEX"
check /tmp/setup.pcap --client-port "$EXP_PAIR" --client-port-in 10512-11023 \
    --not-contains "client_port=6970-6971"
SETUP_OK=$?

echo
echo "=========== 2. inbound media on the announced ports is accepted ==========="
# The point of announcing a pair is that what arrives on it reaches the
# subscriber. Read from the delivered datagram, not from a counter.
shoot_udp /tmp/rtp.pcap 9000 $EXP_LO
check /tmp/rtp.pcap --udp-to 100.64.0.5:6970
RTP_OK=$?
shoot_udp /tmp/rtcp.pcap 9001 $EXP_HI
check /tmp/rtcp.pcap --udp-to 100.64.0.5:6971
RTCP_OK=$?

echo
echo "=========== 3. the 200 reply gives the client back its own ports ==========="
# The server echoes the PUBLIC pair. A client that checks the reply names the
# ports it asked for would refuse the session otherwise - and server_port
# belongs to the server, so rewriting it would break the stream it describes.
REPLY_LINES="RTSP/1.0 200 OK|CSeq: 3|Transport: RTP/AVP;unicast;client_port=${EXP_PAIR};server_port=9000-9001|Session: 12345678"
RLEN=$(python3 /src/lab/mkrtsp.py 8.8.8.8 100.64.0.5 554 5000 \
         --lines "$REPLY_LINES" --payload-len)
HEX=$(python3 /src/lab/mkrtsp.py 8.8.8.8 200.150.0.17 554 "$EXP_CTRL" \
        --seq $SSEQ --ack $(( CSEQ + SLEN + DELTA )) --lines "$REPLY_LINES")
shoot pg0 /tmp/reply.pcap "$HEX"
# Its acknowledgement covers the SETUP as the SERVER saw it - two bytes longer
# than the client sent - and has to reach the client in the client's own count.
check /tmp/reply.pcap --client-port 6970-6971 \
    --contains "server_port=9000-9001" --ack $(( CSEQ + SLEN ))
REPLY_OK=$?

echo
echo "=========== 4. sequence numbers after the rewrite ==========="
# This is the failure that does not look like an ALG failure. The control
# connection carries on for a moment and then stalls, because the peer keeps
# acknowledging byte counts that no longer line up.
echo "  the client's next request starts at $(( CSEQ + SLEN )) and must leave at $(( CSEQ + SLEN + DELTA ))"
PLAY_LINES='PLAY rtsp://8.8.8.8/track1 RTSP/1.0|CSeq: 4|Session: 12345678'
HEX=$(python3 /src/lab/mkrtsp.py 100.64.0.5 8.8.8.8 5000 554 \
        --seq $(( CSEQ + SLEN )) --ack $(( SSEQ + RLEN - DELTA )) \
        --lines "$PLAY_LINES")
shoot pg1 /tmp/play.pcap "$HEX"
# The client acknowledged the SHORTENED reply it received; the server has to
# see the number it actually sent.
check /tmp/play.pcap --seq $(( CSEQ + SLEN + DELTA )) --ack $(( SSEQ + RLEN ))
SEQ_OK=$?

echo
echo "=========== 5. a retransmitted SETUP carries the SAME ports ==========="
# One lost packet is enough to reach this. A retransmission passed through
# untouched would leave the server believing the last thing it read - the
# subscriber's private ports - and the media would go nowhere.
HEX=$(python3 /src/lab/mkrtsp.py 100.64.0.5 8.8.8.8 5000 554 \
        --seq $CSEQ --ack $SSEQ --lines "$SETUP_LINES")
shoot pg1 /tmp/rexmit.pcap "$HEX"
# Its own sequence number must NOT move again: the correction applies to what
# comes after the rewrite, not to the rewritten segment arriving twice.
check /tmp/rexmit.pcap --client-port "$EXP_PAIR" --seq $CSEQ
REXMIT_OK=$?

echo
echo "=========== 6. a header we do not understand is left alone ==========="
# An ALG that rewrites a payload it misparsed corrupts traffic that would
# otherwise merely have failed to connect, which is the worse outcome.
UNPARSED_BEFORE=$(errcount bpa-in2out "not understood")
BAD_LINES='SETUP rtsp://8.8.8.8/track1 RTSP/1.0|CSeq: 3|Transport: RTP/AVP;unicast;client_port=6970'
HEX=$(python3 /src/lab/mkrtsp.py 100.64.0.6 8.8.8.8 5000 554 \
        --seq 1000 --ack 1 --lines "$BAD_LINES")
shoot pg1 /tmp/bad.pcap "$HEX"
check /tmp/bad.pcap --contains "client_port=6970"
BAD_OK=$?
UNPARSED_AFTER=$(errcount bpa-in2out "not understood")
echo "  'not understood' counter: ${UNPARSED_BEFORE:-0} -> ${UNPARSED_AFTER:-0}"

echo
echo "=========== 7. a message split across segments is left alone ==========="
SPLIT_BEFORE=$(errcount bpa-in2out "spans segments")
HEX=$(python3 /src/lab/mkrtsp.py 100.64.0.7 8.8.8.8 5000 554 \
        --seq 1000 --ack 1 --no-end \
        --lines 'SETUP rtsp://8.8.8.8/track1 RTSP/1.0|CSeq: 3|Transport: RTP/AVP;unicast;client_p')
shoot pg1 /tmp/split.pcap "$HEX"
SPLIT_AFTER=$(errcount bpa-in2out "spans segments")
echo "  'spans segments' counter: ${SPLIT_BEFORE:-0} -> ${SPLIT_AFTER:-0}"

echo
echo "=========== 8. announced ports nobody uses go away on their own ==========="
# Two ports of a 512-port block per SETUP. A camera reconnecting in a loop
# would exhaust its own subscriber's block, which reaches support as "the
# internet stopped" with a healthy dataplane.
sess() { vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}'; }
vppctl bpa timeouts alg-expect 2 >/dev/null
BASE=$(sess)
HEX=$(python3 /src/lab/mkrtsp.py 100.64.0.8 8.8.8.8 5000 554 \
        --seq 1000 --ack 1 --lines "$SETUP_LINES")
shoot pg1 /tmp/expire.pcap "$HEX"
OPENED=$(( $(sess) - BASE ))
echo "  sessions opened by the SETUP: $OPENED (control + RTP + RTCP)"
# 60, not 20. The claim is that an announced port nobody used expires on the
# ALG's OWN short timer rather than on the TCP one, and the numbers either side
# of that claim are 2 seconds and 240. Any cap between them proves it; 20 does
# not survive a loaded runner, where this failed twice and passed alone - the
# expiry process runs once a second and takes the worker barrier to do it, so
# on a starved box it measures the runner. 60 is still two orders of magnitude
# below the timer it has to be distinguished from.
n=0
until [ "$(sess)" -le $(( BASE + 1 )) ] || [ "$n" -ge 60 ]; do sleep 1; n=$((n+1)); done
LEFT=$(( $(sess) - BASE ))
echo "  after ${n}s: $LEFT left (the control connection, which is not an expectation)"
# Per thread, because "two left" says nothing about WHICH two and the whole
# question with workers is which thread a session is on.
vppctl show bpa session | sed 's/^/    /'
vppctl show bpa top 2>/dev/null | head -8 | sed 's/^/    /'

echo
echo "=========== 9. the switch, and telling 'off' from 'saw nothing' ==========="
REWROTE=$(errcount bpa-in2out "RTSP Transport headers rewritten")
echo "  rewritten so far: ${REWROTE:-0}"
vppctl bpa alg rtsp rtsp disable
CFG=$(vppctl show bpa config)
POOL=$(vppctl show bpa pool)
HEX=$(python3 /src/lab/mkrtsp.py 100.64.0.6 8.8.8.8 6000 554 \
        --seq 1000 --ack 1 --lines "$SETUP_LINES")
shoot pg1 /tmp/off.pcap "$HEX"
check /tmp/off.pcap --client-port 6970-6971
OFF_OK=$?
REWROTE_OFF=$(errcount bpa-in2out "RTSP Transport headers rewritten")
echo "  rewritten after disabling: ${REWROTE_OFF:-0} (unchanged)"
vppctl bpa alg rtsp rtsp enable

echo
echo "=========== ALG counters ==========="
# Printed in full because "the ALG is off" and "the ALG saw nothing" are
# different diagnoses, and only these numbers plus the pool state separate them.
vppctl show errors | grep -i rtsp | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
expect "the SETUP left with the media pair the block dictates ($EXP_PAIR)" \
  test "$SETUP_OK" = "0"
expect "and it is consecutive with the RTP port even" \
  sh -c "python3 /src/lab/checkrtsp.py /tmp/setup.pcap --client-port-in 10512-11023 >/dev/null 2>&1"
expect "the subscriber's own ports did not reach the internet" \
  sh -c "python3 /src/lab/checkrtsp.py /tmp/setup.pcap --not-contains 'client_port=6970-6971' >/dev/null 2>&1"
expect "inbound RTP on the announced port reached the subscriber" \
  test "${RTP_OK:-1}" = "0"
expect "and RTCP on the one above it" test "${RTCP_OK:-1}" = "0"
expect "the reply gave the client back the ports it asked for" \
  test "${REPLY_OK:-1}" = "0"
expect "server_port was left alone: it belongs to the server" \
  sh -c "python3 /src/lab/checkrtsp.py /tmp/reply.pcap --contains 'server_port=9000-9001' >/dev/null 2>&1"
expect "the next request's sequence number moved with the rewrite" \
  test "${SEQ_OK:-1}" = "0"
expect "a retransmitted SETUP carried the same pair and did not move again" \
  test "${REXMIT_OK:-1}" = "0"
expect "a client_port we cannot parse was left untouched" test "${BAD_OK:-1}" = "0"
expect "and counted" test "${UNPARSED_AFTER:-0}" -gt "${UNPARSED_BEFORE:-0}"
expect "a message that does not end in this segment was counted, not rewritten" \
  test "${SPLIT_AFTER:-0}" -gt "${SPLIT_BEFORE:-0}"
expect "the SETUP opened the control session and a media pair" \
  test "${OPENED:-0}" = "3"
expect "announced ports nobody used expired on the short timer" \
  test "${LEFT:-9}" -le 1
expect "the ALG can be switched off per pool" test "${OFF_OK:-1}" = "0"
expect "and switching it off does not rewrite anything more" \
  test "${REWROTE_OFF:-0}" = "${REWROTE:-0}"
expect_in "the pool reports the ALG state, so 'off' and 'saw nothing' differ" \
  "rtsp off" "$POOL"
expect_in "and the running configuration carries it" "alg rtsp off" "$CFG"
verdict
