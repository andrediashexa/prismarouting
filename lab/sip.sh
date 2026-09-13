#!/bin/sh
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# SIP ALG: the addresses and ports a phone puts in its own signalling, which
# behind a CGNAT name nothing the far end can reach.
#
# Two failures, and an ISP pays for both:
#
#   * the phone registers a Contact nobody can route to, so every inbound call
#     rings nowhere and the subscriber's complaint is "I can call out but
#     nobody can call me";
#   * the call connects and there is no audio, because the SDP announced a
#     private address and a port on it. This is the most expensive support
#     call an ISP takes, because it does not look like a network fault to
#     anybody: the signalling worked.
#
# Run it with the feature off to see it fail, which is the only way to know it
# can:
#
#   sh tools/lab.sh sip              # ALG on  -> LAB PASSED (18 checks)
#   sh tools/lab.sh sip off          # ALG off -> LAB FAILED, exactly 7
#   LAB_WORKERS=1 sh tools/lab.sh sip
#
# EXPECTED TO FAIL WITH "off", and nothing else. The list is written out
# because "some things fail" is not a statement anybody can check:
#
#   1. the INVITE left with the subscriber's public address and port
#        - one check covering Via, Contact, the SDP address, the SDP port,
#          and the four places the private address must no longer appear
#   2. inbound RTP on the announced port reached the phone
#   3. and RTCP on the one above it
#        - 2 and 3 fail because no mapping was ever opened for them, so the
#          media is dropped and nothing is captured
#   4. the response Via was put back the way the phone wrote it
#   5. and counted        (the body this parser cannot reach into)
#   6. and counted        (the lying Content-Length)
#        - 5 and 6 are the REFUSAL counters. With the ALG off nothing is
#          parsed, so nothing is refused, and a counter that ticks either way
#          would not be telling an operator anything
#   7. the INVITE opened the signalling session and a media pair
#
# Everything else passes either way ON PURPOSE. Those checks are about what
# this ALG must NOT do - leave the caller's headers alone, leave a message it
# cannot parse alone, never touch SIP over TCP - and a check that only holds
# when the feature is on is not a check on a refusal.
#
# Every assertion reads the CAPTURED PACKET. lab/checkicmp.py exists because a
# translation with a wrong checksum passes every counter in the dataplane and
# is dropped by the receiver's stack; a SIP rewrite has three more ways to do
# that, and lab/checksip.py checks all of them on every packet it is given: the
# IP total length, the UDP length, the UDP checksum, and Content-Length - which
# is a third length over the same bytes that neither IP nor UDP can see.
#
# Every expectation below is written down BEFORE the run, from the pool's own
# arithmetic, not read back out of the packet the ALG produced.
#
# OFF-RED-AT-LEAST: 7
#
# A FLOOR, not a target, and the number is measured rather than chosen: it is
# what this lab's off run went red on in the release suite of 2026-09-13.
# tools/run-all-labs.sh refuses an off run that goes red on fewer, because a
# lab that used to fail eleven checks and now fails one has lost ten assertions
# without losing its verdict - which is the defect this whole sweep exists to
# find. Gaining assertions is fine and needs no edit here; losing them is what
# has to be deliberate.
set -u
. /src/lab/verdict.sh

ALG=${1:-on}
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
for h in 5 6 7 8 9 10; do vppctl set ip neighbor pg0 100.64.0.$h 02:00:00:00:01:0$h; done

# Ports 10000-65535 on purpose, exactly as lab/rtsp.sh does. With the default
# 1024-65535 the subscriber's block starts at 1536 and the rewritten media port
# would have the same number of digits as the one the phone asked for - the
# body length would never change, and the Content-Length correction, which is
# the part that fails at the far end rather than here, would never be
# exercised.
vppctl bpa pool sip inside 100.64.0.0/22 outside 200.150.0.16/30 \
    block 512 ports 10000-65535
# The RUNTIME switch, not "alg sip off" on the pool line above.
#
# Not a preference. "bpa pool <name> ... alg <x> off" is accepted by the CLI
# and has no effect: bpa_pool_add_command_fn carries TWO "alg <name> on|off"
# branches, and the first one reached sets cfg.alg without setting cfg.alg_set,
# so bpa_pool_init() then resolves the bit back to the shipped default. Found
# while writing this lab, reported separately, and deliberately not fixed here.
#
# It matters to this file because an "off" mode that quietly ran with the ALG
# ON would report a pass for a configuration nobody tested - which is the exact
# shape of the defect the off mode exists to rule out.
[ "$ALG" = off ] && vppctl bpa alg sip sip disable
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
# Long enough that nothing expires under us; the expiry check sets its own.
vppctl bpa timeouts udp 600 tcp-established 7440 tcp-transitory 600 \
    alg-expect 600 >/dev/null

# What must happen, stated before it does.
#
# The mapping is deterministic: 100.64.0.5 is subscriber index 5, four public
# addresses interleaved put it on 200.150.0.17 with block 1, ports 10512-11023.
# The phone's SIP port 5060 is outside that block, so port preservation cannot
# apply and the allocator hands out the first free offset: 10512. The media
# pair takes the first free EVEN offset after it.
PUB=200.150.0.17
EXP_SIG=10512
EXP_RTP=10514
EXP_RTCP=10515

echo "  pool: $(vppctl show bpa pool | grep -E 'alg|ports')"
echo "  subscriber 100.64.0.5 -> $PUB, ports 10512-11023"
echo "  expected: signalling on $EXP_SIG, media $EXP_RTP/$EXP_RTCP"

# Injects one packet given as hex, capturing what leaves on <iface>.
shoot() {   # <capture-iface> <pcap> <hex>
  rm -f "$2"
  vppctl packet-generator delete p >/dev/null 2>&1
  vppctl packet-generator capture "$1" pcap "$2" count 1 >/dev/null
  L=$(( ${#3} / 2 - 1 ))
  IN=pg0; [ "$1" = pg0 ] && IN=pg1
  vppctl packet-generator new "name p limit 1 node ip4-input interface $IN \
      size ${L}-${L} data { hex $3 }"
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete p >/dev/null 2>&1
}

# One UDP datagram from the internet to a public port, capturing what reaches
# the subscriber. This is the media itself, not a counter about it.
shoot_udp() {   # <pcap> <source port> <public port>
  rm -f "$1"
  vppctl packet-generator delete m >/dev/null 2>&1
  vppctl packet-generator capture pg0 pcap "$1" count 1 >/dev/null
  vppctl packet-generator new "name m limit 1 node ip4-input interface pg1 \
      size 60-60 data { UDP: 8.8.8.8 -> $PUB \
                        UDP: $2 -> $3 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete m >/dev/null 2>&1
}

# Runs the packet checker, prints what it saw, and RETURNS ITS STATUS. Piping
# it into sed would return sed's status instead, and every assertion built on
# it would pass whatever the checker found.
check() {
  python3 /src/lab/checksip.py "$@" > /tmp/chk.out 2>&1
  _rc=$?
  sed 's/^/  /' /tmp/chk.out
  return $_rc
}

# A counter that has never fired is absent from "show errors", not zero.
errc() {
  _v=$(vppctl show errors | grep -F "$1" | awk '{print $1}' | head -1)
  echo "${_v:-0}"
}

OUT_HDRS='Via: SIP/2.0/UDP 100.64.0.5:5060;branch=z9hG4bKnashds8|Max-Forwards: 70|From: "Ana" <sip:1000@8.8.8.8>;tag=1928301774|To: <sip:2000@8.8.8.8>|Call-ID: a84b4c76e66710@100.64.0.5|CSeq: 314159 INVITE|Contact: <sip:1000@100.64.0.5:5060>'
OUT_SDP='v=0|o=- 1 1 IN IP4 100.64.0.5|s=-|c=IN IP4 100.64.0.5|t=0 0|m=audio 8000 RTP/AVP 0|a=rtpmap:0 PCMU/8000'

echo
echo "=========== 1. INVITE: where to reach this phone, and its media ==========="
echo "  the phone announces 100.64.0.5:5060 and RTP on 8000, all meaningless outside"
HEX=$(python3 /src/lab/mksip.py 100.64.0.5 8.8.8.8 5060 5060 \
        --start 'INVITE sip:2000@8.8.8.8 SIP/2.0' \
        --headers "$OUT_HDRS" --body "$OUT_SDP")
shoot pg1 /tmp/invite.pcap "$HEX"
check /tmp/invite.pcap --from "$PUB:$EXP_SIG" --to 8.8.8.8:5060 \
    --via "$PUB:$EXP_SIG" --contact "$PUB:$EXP_SIG" \
    --sdp-conn "$PUB" --sdp-media "$EXP_RTP" \
    --not-contains 'Via: SIP/2.0/UDP 100.64.0.5' \
    --not-contains 'sip:1000@100.64.0.5' \
    --not-contains 'c=IN IP4 100.64.0.5' \
    --not-contains 'm=audio 8000'
INVITE_OK=$?
# The headers this ALG deliberately does not touch, asserted one by one. A
# rewrite that went further than it should is as much a defect as one that did
# not go far enough, and it is the one nothing else would notice.
check /tmp/invite.pcap \
    --contains 'From: "Ana" <sip:1000@8.8.8.8>;tag=1928301774' \
    --contains 'To: <sip:2000@8.8.8.8>' \
    --contains 'Call-ID: a84b4c76e66710@100.64.0.5' \
    --contains 'o=- 1 1 IN IP4 100.64.0.5' \
    --contains 'CSeq: 314159 INVITE' \
    --contains 'branch=z9hG4bKnashds8' >/dev/null 2>&1
LEFT_OK=$?
echo "  identities and the SDP origin left alone: $([ $LEFT_OK = 0 ] && echo yes || echo NO)"

echo
echo "=========== 2. the announced media ports carry audio ==========="
# The point of rewriting SDP is that what the far end sends to the port it was
# given reaches the phone. Read from the delivered datagram, not from a counter.
shoot_udp /tmp/rtp.pcap 40000 $EXP_RTP
check /tmp/rtp.pcap --to 100.64.0.5:8000
RTP_OK=$?
shoot_udp /tmp/rtcp.pcap 40001 $EXP_RTCP
check /tmp/rtcp.pcap --to 100.64.0.5:8001
RTCP_OK=$?

echo
echo "=========== 3. the 200 OK, and the Via the phone insists on ==========="
# RFC 3261 s18.1.2: a client discards a response whose topmost Via is not the
# one it sent. Having rewritten that Via on the way out, this box has to undo
# it on the way back or the phone throws away its own answers.
IN_HDRS="Via: SIP/2.0/UDP $PUB:$EXP_SIG;branch=z9hG4bKnashds8;received=$PUB;rport=$EXP_SIG|From: \"Ana\" <sip:1000@8.8.8.8>;tag=1928301774|To: <sip:2000@8.8.8.8>;tag=a6c85cf|Call-ID: a84b4c76e66710@100.64.0.5|CSeq: 314159 INVITE|Contact: <sip:2000@8.8.8.8:5060>"
IN_SDP='v=0|o=- 2 2 IN IP4 8.8.8.8|s=-|c=IN IP4 8.8.8.8|t=0 0|m=audio 40000 RTP/AVP 0'
HEX=$(python3 /src/lab/mksip.py 8.8.8.8 "$PUB" 5060 "$EXP_SIG" \
        --start 'SIP/2.0 200 OK' --headers "$IN_HDRS" --body "$IN_SDP")
shoot pg0 /tmp/ok.pcap "$HEX"
check /tmp/ok.pcap --to 100.64.0.5:5060 --via 100.64.0.5:5060 \
    --contact 8.8.8.8:5060 --sdp-conn 8.8.8.8 --sdp-media 40000 \
    --contains ";received=$PUB;rport=$EXP_SIG"
OK_OK=$?
echo "  received= and rport= are the far end stating a fact; rewriting them"
echo "  would replace it with a lie, so they are left as they arrived"

echo
echo "=========== 4. an inbound call: the caller's headers are not ours ==========="
# Somebody ringing this subscriber. Every address in the message belongs to the
# caller, and the Request-URI names this box's public address because that is
# what the Contact above published. None of it is this box's to rewrite.
CALL_HDRS='Via: SIP/2.0/UDP 8.8.8.8:5060;branch=z9hG4bKinbound|Max-Forwards: 70|From: <sip:3000@8.8.8.8>;tag=in1|To: <sip:1000@'"$PUB"'>|Call-ID: inbound-1@8.8.8.8|CSeq: 1 INVITE|Contact: <sip:3000@8.8.8.8:5060>'
CALL_SDP='v=0|o=- 3 3 IN IP4 8.8.8.8|s=-|c=IN IP4 8.8.8.8|t=0 0|m=audio 41000 RTP/AVP 0'
HEX=$(python3 /src/lab/mksip.py 8.8.8.8 "$PUB" 5060 "$EXP_SIG" \
        --start "INVITE sip:1000@$PUB:$EXP_SIG SIP/2.0" \
        --headers "$CALL_HDRS" --body "$CALL_SDP" \
        --emit-payload /tmp/inbound.txt)
shoot pg0 /tmp/inbound.pcap "$HEX"
check /tmp/inbound.pcap --to 100.64.0.5:5060 \
    --payload-equals /tmp/inbound.txt
INBOUND_OK=$?

echo
echo "=========== 5. a phone that already knows its public address ==========="
# The failure operators know as "the ALG broke my phone": a CPE doing its own
# traversal puts the PUBLIC address in Via, Contact and SDP, and a second NAT
# helper rewrites it to something else. This one rewrites only a literal
# address equal to the subscriber's own private one, so there is nothing here
# it will touch.
STUN_HDRS="Via: SIP/2.0/UDP $PUB:$EXP_SIG;branch=z9hG4bKstun|From: <sip:1000@8.8.8.8>;tag=s1|To: <sip:2000@8.8.8.8>|Call-ID: stun-1@8.8.8.8|CSeq: 1 INVITE|Contact: <sip:1000@$PUB:$EXP_SIG>"
STUN_SDP="v=0|o=- 4 4 IN IP4 $PUB|s=-|c=IN IP4 $PUB|t=0 0|m=audio 10514 RTP/AVP 0"
HEX=$(python3 /src/lab/mksip.py 100.64.0.6 8.8.8.8 5060 5060 \
        --start 'INVITE sip:2000@8.8.8.8 SIP/2.0' \
        --headers "$STUN_HDRS" --body "$STUN_SDP" \
        --emit-payload /tmp/stun.txt)
shoot pg1 /tmp/stun.pcap "$HEX"
check /tmp/stun.pcap --payload-equals /tmp/stun.txt
STUN_OK=$?

echo
echo "=========== 6. a message we cannot parse is left alone and counted ==========="
# A multipart body has SDP somewhere inside it that this parser cannot reach.
# Rewriting the signalling and not the media would deliver a call with no audio
# and nothing to say why, so the whole message is refused.
# From the SUBSCRIBER THE HEADERS NAME, on a fresh port. Sent from anybody else
# the message would be left untouched because no address in it is that
# subscriber's - and the check would pass without the refusal having been
# reached at all, which is the shape of assertion this repository keeps
# finding. Every "left untouched" message below carries 100.64.0.5's own
# address, so it is one the ALG would certainly have rewritten had it accepted
# it.
BAD_BEFORE=$(errc "SIP message not understood")
HEX=$(python3 /src/lab/mksip.py 100.64.0.5 8.8.8.8 5062 5060 \
        --start 'INVITE sip:2000@8.8.8.8 SIP/2.0' \
        --headers "$OUT_HDRS" --ctype 'multipart/mixed;boundary=x' \
        --body '--x|v=0|c=IN IP4 100.64.0.5|--x--' \
        --emit-payload /tmp/bad.txt)
shoot pg1 /tmp/bad.pcap "$HEX"
check /tmp/bad.pcap --payload-equals /tmp/bad.txt
BAD_OK=$?
BAD_AFTER=$(errc "SIP message not understood")
echo "  'not understood' counter: $BAD_BEFORE -> $BAD_AFTER"

# And the other refusal, which is the one that protects the far end: a
# Content-Length that disagrees with the datagram is a truncated or crafted
# message, and the body this box would walk is not the body the endpoints read.
CLEN_BEFORE=$BAD_AFTER
HEX=$(python3 /src/lab/mksip.py 100.64.0.5 8.8.8.8 5063 5060 \
        --start 'INVITE sip:2000@8.8.8.8 SIP/2.0' \
        --headers "$OUT_HDRS" --body "$OUT_SDP" --clen 40 \
        --emit-payload /tmp/clen.txt)
shoot pg1 /tmp/clen.pcap "$HEX"
check /tmp/clen.pcap --clen-lies --payload-equals /tmp/clen.txt
CLEN_OK=$?
CLEN_AFTER=$(errc "SIP message not understood")
echo "  after a lying Content-Length: $CLEN_BEFORE -> $CLEN_AFTER"

echo
echo "=========== 7. SIP over TCP is never touched ==========="
# This ALG binds to UDP only, and says so. A TCP SIP message is a stream whose
# end one segment cannot show, and an INVITE with SDP is regularly larger than
# one segment - so the honest TCP answer is reassembly, which is different
# work. What must NOT happen is a TCP message half rewritten by a parser that
# could not see the end of it.
HEX=$(python3 /src/lab/mksip.py 100.64.0.5 8.8.8.8 5060 5060 --tcp \
        --start 'INVITE sip:2000@8.8.8.8 SIP/2.0' \
        --headers "$OUT_HDRS" --body "$OUT_SDP" \
        --emit-payload /tmp/tcp.txt)
shoot pg1 /tmp/tcp.pcap "$HEX"
check /tmp/tcp.pcap --tcp --payload-equals /tmp/tcp.txt
TCP_OK=$?

echo
echo "=========== 8. announced ports nobody uses go away on their own ==========="
# Two ports of a 512-port block per offer. A phone re-registering in a loop
# would exhaust its own subscriber's block, which reaches support as "the
# internet stopped" with a healthy dataplane.
sess() { vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}'; }
vppctl bpa timeouts alg-expect 2 >/dev/null
BASE=$(sess)
EXP_HDRS='Via: SIP/2.0/UDP 100.64.0.9:5060;branch=z9hG4bKexp|From: <sip:1@8.8.8.8>;tag=e1|To: <sip:2@8.8.8.8>|Call-ID: exp-1@8.8.8.8|CSeq: 1 INVITE|Contact: <sip:1@100.64.0.9:5060>'
HEX=$(python3 /src/lab/mksip.py 100.64.0.9 8.8.8.8 5060 5060 \
        --start 'INVITE sip:2@8.8.8.8 SIP/2.0' --headers "$EXP_HDRS" \
        --body 'v=0|o=- 5 5 IN IP4 100.64.0.9|s=-|c=IN IP4 100.64.0.9|t=0 0|m=audio 9000 RTP/AVP 0')
shoot pg1 /tmp/expire.pcap "$HEX"
OPENED=$(( $(sess) - BASE ))
echo "  sessions opened by the INVITE: $OPENED (signalling + RTP + RTCP)"
# 60, not 20, for the reason lab/rtsp.sh records: the expiry process takes the
# worker barrier once a second, so on a loaded runner a tight cap measures the
# runner rather than the timer. The claim is that an announced port nobody used
# expires on the ALG's own short timer rather than on the UDP one, and the
# numbers either side of that claim are 2 seconds and 600.
n=0
until [ "$(sess)" -le $(( BASE + 1 )) ] || [ "$n" -ge 60 ]; do sleep 1; n=$((n+1)); done
LEFT=$(( $(sess) - BASE ))
echo "  after ${n}s: $LEFT left (the signalling session, which is not an expectation)"
vppctl show bpa session | sed 's/^/    /'
vppctl bpa timeouts alg-expect 600 >/dev/null

echo
echo "=========== 9. the switch, and telling 'off' from 'saw nothing' ==========="
REWROTE=$(errc "SIP messages rewritten")
echo "  rewritten so far: $REWROTE"
vppctl bpa alg sip sip disable
CFG=$(vppctl show bpa config)
POOL=$(vppctl show bpa pool)
OFF_HDRS='Via: SIP/2.0/UDP 100.64.0.10:5060;branch=z9hG4bKoff|From: <sip:1@8.8.8.8>;tag=o1|To: <sip:2@8.8.8.8>|Call-ID: off-1@8.8.8.8|CSeq: 1 INVITE|Contact: <sip:1@100.64.0.10:5060>'
HEX=$(python3 /src/lab/mksip.py 100.64.0.10 8.8.8.8 5060 5060 \
        --start 'INVITE sip:2@8.8.8.8 SIP/2.0' --headers "$OFF_HDRS" \
        --body 'v=0|o=- 6 6 IN IP4 100.64.0.10|s=-|c=IN IP4 100.64.0.10|t=0 0|m=audio 9100 RTP/AVP 0' \
        --emit-payload /tmp/off.txt)
shoot pg1 /tmp/off.pcap "$HEX"
check /tmp/off.pcap --payload-equals /tmp/off.txt
OFF_OK=$?
REWROTE_OFF=$(errc "SIP messages rewritten")
echo "  rewritten after disabling: $REWROTE_OFF (unchanged)"
vppctl bpa alg sip sip enable

echo
echo "=========== ALG counters ==========="
# Printed in full because "the ALG is off" and "the ALG saw nothing" are
# different diagnoses, and only these numbers plus the pool state separate them.
vppctl show errors | grep -i "SIP" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
expect "the INVITE left with the subscriber's public address and port" \
  test "${INVITE_OK:-1}" = "0"
expect "and the identities and SDP origin were left alone" \
  test "${LEFT_OK:-1}" = "0"
expect "inbound RTP on the announced port reached the phone" \
  test "${RTP_OK:-1}" = "0"
expect "and RTCP on the one above it" test "${RTCP_OK:-1}" = "0"
expect "the response Via was put back the way the phone wrote it" \
  test "${OK_OK:-1}" = "0"
expect "an inbound call's own headers reached the phone untouched" \
  test "${INBOUND_OK:-1}" = "0"
expect "a phone that already knows its public address was not interfered with" \
  test "${STUN_OK:-1}" = "0"
expect "a body this parser cannot reach into left the message untouched" \
  test "${BAD_OK:-1}" = "0"
expect "and counted" test "${BAD_AFTER:-0}" -gt "${BAD_BEFORE:-0}"
expect "a Content-Length that disagrees with the datagram was refused" \
  test "${CLEN_OK:-1}" = "0"
expect "and counted" test "${CLEN_AFTER:-0}" -gt "${CLEN_BEFORE:-0}"
expect "SIP over TCP was carried through with no ALG anywhere near it" \
  test "${TCP_OK:-1}" = "0"
expect "the INVITE opened the signalling session and a media pair" \
  test "${OPENED:-0}" = "3"
expect "announced ports nobody used expired on the short timer" \
  test "${LEFT:-9}" -le 1
expect "the ALG can be switched off per pool" test "${OFF_OK:-1}" = "0"
expect "and switching it off does not rewrite anything more" \
  test "${REWROTE_OFF:-0}" = "${REWROTE:-0}"
expect_in "the pool reports the ALG state, so 'off' and 'saw nothing' differ" \
  "sip off" "$POOL"
expect_in "and the running configuration carries it" "alg sip off" "$CFG"
verdict
