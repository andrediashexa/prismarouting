#!/bin/sh
#
# OFF-RED-AT-LEAST: 13
#
# A FLOOR, not a target, and the number is measured rather than chosen: it is
# what this lab's off run went red on in the release suite of 2026-09-13.
# tools/run-all-labs.sh refuses an off run that goes red on fewer, because a
# lab that used to fail eleven checks and now fails one has lost ten assertions
# without losing its verdict - which is the defect this whole sweep exists to
# find. Gaining assertions is fine and needs no edit here; losing them is what
# has to be deliberate.
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# FTP ALG: the address FTP carries in its payload, and the sequence numbers
# that have to move when rewriting it changes the payload's length.
#
# Run it with the feature off to see it fail, which is the only way to know it
# can:
#
#   sh tools/lab.sh ftp-alg          # ALG on  -> LAB PASSED
#   sh tools/lab.sh ftp-alg off      # ALG off -> LAB FAILED
#
# Every assertion about a rewrite reads the CAPTURED PACKET, never the trace.
# lab/checkicmp.py exists because a translation with a wrong checksum passes
# every counter in the dataplane and is dropped by the receiver's stack; the
# same is true here, with one more failure a counter cannot see - a payload
# that changed length while the IP header still claims the old one.
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
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0
# Translated traffic comes back TO the subscriber over a connected network, so
# without a neighbour VPP gleans and drops while ARP is unresolved.
for h in 5 6 7 8 9 10; do vppctl set ip neighbor pg0 100.64.0.$h 02:00:00:00:01:0$h; done

vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512 \
  alg ftp "$ALG"
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
# Long enough that nothing expires while the lab is running: the expiry of an
# unanswered announcement is a separate check, further down.
vppctl bpa timeouts tcp-syn 600 tcp-transitory 600 udp 600 >/dev/null

echo "  pool: 100.64.0.0/22 -> 200.150.0.16/30, 512-port blocks, alg ftp $ALG"
echo "  100.64.0.5 therefore holds 200.150.0.17:1536-2047"

# send <inject-if> <capture-if> <pcap> <hex>
send() {
  _inj=$1; _cap=$2; _pcap=$3; _hex=$4
  _len=$(( ${#_hex} / 2 - 1 ))
  rm -f "$_pcap"
  vppctl packet-generator capture "$_cap" pcap "$_pcap" count 1 >/dev/null
  vppctl packet-generator delete s >/dev/null 2>&1
  vppctl packet-generator new "name s limit 1 node ip4-input interface $_inj \
      size ${_len}-${_len} data { hex $_hex }" >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 1
  vppctl packet-generator delete s >/dev/null 2>&1
}

# A counter that has never fired is absent from "show errors", not zero.
errc() {
  _v=$(vppctl show errors | grep -F "$1" | awk '{print $1}' | head -1)
  echo "${_v:-0}"
}

echo
echo "=========== 1. PORT: the client announces where to connect back ==========="
# "PORT 100,64,0,5,1,1" announces 100.64.0.5:257 - unroutable from the server.
# The rewrite is two bytes LONGER than the original, which is the direction
# that can overrun a buffer and the direction that shifts every later byte of
# the stream.
HEX=$(python3 /src/lab/mkftp.py 100.64.0.5 8.8.8.8 40000 21 1000 5000 psh,ack \
      'PORT 100,64,0,5,1,1\r\n')
send pg0 pg1 /tmp/ftp-port.pcap "$HEX"
PORTOUT=$(python3 /src/lab/checkftp.py /tmp/ftp-port.pcap 2>&1); PORTRC=$?
printf '%s\n' "$PORTOUT" | sed 's/^/  /'
REWROTE=$(errc "FTP ALG payloads rewritten")
echo "  rewrites so far: $REWROTE"

echo
echo "=========== 2. the next command in the same stream is renumbered ==========="
# The PORT segment was 21 bytes and left as 23, so the subscriber's next byte
# is at 1021 and has to leave as 1023. Without this the server acknowledges
# bytes the client never sent and the control connection stalls a few commands
# later - a failure that does not look like FTP and does not look like NAT.
HEX=$(python3 /src/lab/mkftp.py 100.64.0.5 8.8.8.8 40000 21 1021 5000 psh,ack \
      'TYPE I\r\n')
send pg0 pg1 /tmp/ftp-seq.pcap "$HEX"
SEQOUT=$(python3 /src/lab/checkftp.py /tmp/ftp-seq.pcap 2>&1); SEQRC=$?
printf '%s\n' "$SEQOUT" | sed 's/^/  /'

echo
echo "=========== 2b. the PORT segment RETRANSMITTED does not double the shift ==========="
# The one behaviour that changed when the two sequence-correction mechanisms
# became one (#48), and the reason that one was kept.
#
# A client that does not hear its PORT acknowledged sends it again, byte for
# byte, at the same sequence number. The ALG rewrites it again - correctly, the
# packet on the wire has to carry the public address either way - and must NOT
# record a second correction for it: the rewrite it is accounting for has
# already been accounted for. The mechanism deleted by #48 had no guard for
# this and would have pushed the offset to 4, after which the stream is two
# bytes further out with every later segment and the connection stalls looking
# like something else entirely.
#
# Asserted on the segment AFTER it, because the retransmission itself looks
# identical in both worlds. Still 1023, not 1025.
HEX=$(python3 /src/lab/mkftp.py 100.64.0.5 8.8.8.8 40000 21 1000 5000 psh,ack \
      'PORT 100,64,0,5,1,1\r\n')
send pg0 pg1 /tmp/ftp-port2.pcap "$HEX"
HEX=$(python3 /src/lab/mkftp.py 100.64.0.5 8.8.8.8 40000 21 1021 5000 psh,ack \
      'TYPE I\r\n')
send pg0 pg1 /tmp/ftp-seq2.pcap "$HEX"
SEQ2OUT=$(python3 /src/lab/checkftp.py /tmp/ftp-seq2.pcap 2>&1)
printf '%s\n' "$SEQ2OUT" | sed 's/^/  /'

echo
echo "=========== 3. and the server's acknowledgement is renumbered back ==========="
# The subscriber sent 21 bytes of PORT and 8 of TYPE, so it believes it is at
# 1029. The server sees 23 and 8 and acknowledges 1031, which has to arrive as
# 1029 or the subscriber is told it sent two bytes it never wrote.
HEX=$(python3 /src/lab/mkftp.py 8.8.8.8 200.150.0.17 21 1536 5000 1031 psh,ack \
      '200 Type set to I\r\n')
send pg1 pg0 /tmp/ftp-ack.pcap "$HEX"
ACKOUT=$(python3 /src/lab/checkftp.py /tmp/ftp-ack.pcap 2>&1); ACKRC=$?
printf '%s\n' "$ACKOUT" | sed 's/^/  /'

echo
echo "=========== 4. the data connection the client announced is accepted ==========="
# The server connects back from port 20 to the address and port the ALG put in
# the payload. Nothing outbound ever went to that port, so without a session
# created by the ALG this is unsolicited inbound and is dropped.
HEX=$(python3 /src/lab/mkftp.py 8.8.8.8 200.150.0.17 20 1537 7000 0 syn)
send pg1 pg0 /tmp/ftp-data.pcap "$HEX"
DATAOUT=$(python3 /src/lab/checkftp.py /tmp/ftp-data.pcap 2>&1); DATARC=$?
printf '%s\n' "$DATAOUT" | sed 's/^/  /'

echo
echo "=========== 5. EPRT (RFC 2428) ==========="
# 100.64.0.8 is subscriber index 8, so its block is 200.150.0.16:2048-2559.
HEX=$(python3 /src/lab/mkftp.py 100.64.0.8 8.8.8.8 40000 21 1000 5000 psh,ack \
      'EPRT |1|100.64.0.8|4444|\r\n')
send pg0 pg1 /tmp/ftp-eprt.pcap "$HEX"
EPRTOUT=$(python3 /src/lab/checkftp.py /tmp/ftp-eprt.pcap 2>&1); EPRTRC=$?
printf '%s\n' "$EPRTOUT" | sed 's/^/  /'

echo
echo "=========== 6. 227 and 229, from a subscriber that is the server ==========="
# The 227/229 replies carry the SENDER's address. Rewriting them is therefore
# the in2out job too: a 227 arriving from the internet announces a public
# address that already works, and rewriting that would break a session that
# was fine. See the comment on issue #42.
HEX=$(python3 /src/lab/mkftp.py 100.64.0.6 8.8.8.8 21 41000 1000 5000 psh,ack \
      '227 Entering Passive Mode (100,64,0,6,1,2)\r\n')
send pg0 pg1 /tmp/ftp-227.pcap "$HEX"
P227OUT=$(python3 /src/lab/checkftp.py /tmp/ftp-227.pcap 2>&1); P227RC=$?
printf '%s\n' "$P227OUT" | sed 's/^/  /'

HEX=$(python3 /src/lab/mkftp.py 100.64.0.7 8.8.8.8 21 41000 1000 5000 psh,ack \
      '229 Entering Extended Passive Mode (|||4445|)\r\n')
send pg0 pg1 /tmp/ftp-229.pcap "$HEX"
P229OUT=$(python3 /src/lab/checkftp.py /tmp/ftp-229.pcap 2>&1); P229RC=$?
printf '%s\n' "$P229OUT" | sed 's/^/  /'

echo
echo "=========== 7. a payload that does not parse is left alone ==========="
# Five fields instead of six. An ALG that rewrites what it misparsed corrupts
# traffic that would otherwise merely fail to connect, which is worse than the
# failure it exists to prevent.
BEFORE=$(errc "FTP payloads left untouched")
HEX=$(python3 /src/lab/mkftp.py 100.64.0.9 8.8.8.8 40000 21 1000 5000 psh,ack \
      'PORT 100,64,0,9,195\r\n')
send pg0 pg1 /tmp/ftp-bad.pcap "$HEX"
BADOUT=$(python3 /src/lab/checkftp.py /tmp/ftp-bad.pcap 2>&1); BADRC=$?
printf '%s\n' "$BADOUT" | sed 's/^/  /'
AFTER=$(errc "FTP payloads left untouched")
echo "  untouched counter: $BEFORE -> $AFTER"

echo
echo "=========== 8. the switch ==========="
# "off" and "saw nothing" must be distinguishable, which is what the counter
# is for.
vppctl bpa alg lab ftp off
OFFBEFORE=$(errc "FTP ALG payloads rewritten")
HEX=$(python3 /src/lab/mkftp.py 100.64.0.10 8.8.8.8 40000 21 1000 5000 psh,ack \
      'PORT 100,64,0,10,1,1\r\n')
send pg0 pg1 /tmp/ftp-off.pcap "$HEX"
OFFOUT=$(python3 /src/lab/checkftp.py /tmp/ftp-off.pcap 2>&1); OFFRC=$?
printf '%s\n' "$OFFOUT" | sed 's/^/  /'
OFFAFTER=$(errc "FTP ALG payloads rewritten")
echo "  rewrite counter with the ALG off: $OFFBEFORE -> $OFFAFTER"
# The OTHER spelling of the same switch, and the one that was broken.
#
# There are two ways to turn an ALG off - on the `bpa pool` line when the pool
# is created, and with `bpa alg <pool>` afterwards - and nothing asserted that
# they agree. They did not: the pool line was parsed by a duplicate branch that
# set the bit and not the "an opinion was stated" flag, so bpa_pool_init()
# resolved it straight back to the default and the pool came up with the ALG ON
# (#206). It was accepted and it printed nothing.
#
# That took this lab's own off mode with it - the header says `ftp-alg off`
# must FAIL and it passed, because the mode is exactly that spelling. One lab
# used it and it is the one that went quiet.
#
# A pool of its own, so the assertion does not depend on which mode this run is
# in, and read off `show bpa pool` rather than the configuration emitter: the
# emitter prints what was asked for, and what is being checked here is what the
# box DID with it.
vppctl bpa pool algline inside 100.64.8.0/24 outside 200.150.1.0/30 block 512 \
  alg ftp off >/dev/null
# The alg line of THAT pool. `show bpa pool` prints every pool, so a grep for
# "alg" across the whole output answers about whichever pool came first.
POOLLINE=$(vppctl show bpa pool | awk '/^pool algline/{p=1;next} /^pool /{p=0} p && /alg/{print; exit}')
echo "  pool line said 'alg ftp off', box says:${POOLLINE:- <nothing>}"
# Removed again as soon as it has answered. Every assertion below reads
# `show bpa config` across ALL pools - the duplicate-emitter check counts
# occurrences of one line - so a second pool carrying the same setting would
# make this section break the next one, which is a lab testing itself.
vppctl bpa pool algline del >/dev/null 2>&1

CFG=$(vppctl show bpa config)
# Counted, not merely found. Two branches each added the ALG block to this
# emitter and the merge kept both, so "alg ftp off" came out TWICE - which a
# grep for its presence cannot see, and which puts a duplicate line into every
# saved configuration the VTY reads back.
ALG_LINES=$(printf '%s\n' "$CFG" | grep -c "alg ftp off")
echo "  'alg ftp off' in the running configuration: $ALG_LINES time(s)"
SHOWPOOL=$(vppctl show bpa pool)
vppctl bpa alg lab ftp on >/dev/null

echo
echo "=========== VERDICT ==========="
# Every capture, checked for the things a counter cannot see: both checksums,
# and an IP total length that matches the bytes actually on the wire. This is
# the assertion the ICMP work bought: a rewrite with a wrong checksum is
# dropped by the receiver in silence while the CGNAT reports success.
expect "the rewritten PORT segment is a valid packet" test "$PORTRC" = "0"
expect "the renumbered segment is a valid packet" test "$SEQRC" = "0"
expect "the renumbered acknowledgement is a valid packet" test "$ACKRC" = "0"
expect "the data connection's SYN is a valid packet" test "$DATARC" = "0"
expect "the rewritten EPRT is a valid packet" test "$EPRTRC" = "0"
expect "the rewritten 227 is a valid packet" test "$P227RC" = "0"
expect "the rewritten 229 is a valid packet" test "$P229RC" = "0"
expect "the untouched packet is still a valid packet" test "$BADRC" = "0"
expect "the packet passed through with the ALG off is valid" test "$OFFRC" = "0"

# The rewrite itself, read off the wire.
expect_in "PORT announces the subscriber's public address and a port from its block" \
  "payload='PORT 200,150,0,17,6,1\\r\\n'" "$PORTOUT"
expect_not_in "and the private address does not leave the box" "100,64,0,5" \
  "$PORTOUT"
expect_in "EPRT announces the public address in RFC 2428 form" \
  "payload='EPRT |1|200.150.0.16|2049|\\r\\n'" "$EPRTOUT"
expect_in "227 announces the public address" \
  "(200,150,0,18,6,1)" "$P227OUT"
expect_in "229 announces a port from the block, not the one the server chose" \
  "(|||1537|)" "$P229OUT"

# The length change, and everything that follows from it.
expect_in "the PORT rewrite changed the payload's length, 21 bytes to 23" \
  "paylen=23" "$PORTOUT"
expect_in "so the next segment of the stream leaves renumbered" "seq=1023" \
  "$SEQOUT"
# A retransmitted PORT is rewritten again and must not be counted again.
expect_in "a retransmitted rewrite does not shift the stream twice" "seq=1023" \
  "$SEQ2OUT"
expect_not_in "and in particular not by double the rewrite" "seq=1025" \
  "$SEQ2OUT"
expect_in "and the server's acknowledgement comes back renumbered" "ack=1029" \
  "$ACKOUT"
# Without the shift the subscriber would see 1031 - two bytes it never sent.
expect_not_in "the subscriber is never told it sent bytes it did not" "ack=1031" \
  "$ACKOUT"

# The data connection.
expect_in "the announced data connection reaches the subscriber" \
  "dst=100.64.0.5 sport=20 dport=257" "$DATAOUT"

# Refusing to guess, and being visible about it.
expect_in "a PORT with five fields is passed through byte for byte" \
  "payload='PORT 100,64,0,9,195\\r\\n'" "$BADOUT"
expect "and it is counted, so it is not silent" test "$AFTER" -gt "$BEFORE"

# The switch.
expect_in "with the ALG off the payload is untouched" \
  "payload='PORT 100,64,0,10,1,1\\r\\n'" "$OFFOUT"
expect "and nothing is counted as rewritten" test "$OFFAFTER" = "$OFFBEFORE"
expect "the running configuration records it exactly once, not twice" \
  test "${ALG_LINES:-0}" -eq 1
expect_in "the running configuration records the pool's ALG being off" \
  "alg ftp off" "$CFG"
expect_in "an ALG turned off on the pool line is really off" \
  "ftp off" "$POOLLINE"
expect_in "and show bpa pool reports the switch either way" "alg          ftp" \
  "$SHOWPOOL"
verdict
