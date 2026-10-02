#!/bin/sh
# The block log as RFC 5424 syslog lines, with a real collector (#20).
#
#   sh tools/lab.sh syslog             -> LAB PASSED
#   sh tools/lab.sh syslog no-syslog   -> LAB FAILED
#
#   pg0/pg1  subscriber traffic, dynamic pool
#   veth     path to the collector, which runs in the "peer" netns
#
# WHAT THIS ASSERTS THAT THE UNIT SUITE CANNOT. test/test_bpa_log_syslog.c
# reads the line byte for byte and test/test_syslog_collector.py reads the
# framing, and both are right about a string. What neither can say is whether
# an allocation on a running box becomes a line on a collector's socket: the
# queue, the process that drains it, the stream, the socket and the framing
# all sit between those two facts, and every one of them has been the thing
# that was wrong before.
#
# THE STATEMENT IS TYPED THROUGH THE VTY, exactly as an operator types it, for
# the reason lab/ipfix.sh records at length: a lab that configures the box
# under test cannot notice the box failing to configure itself.
#
# OFF-RED-AT-LEAST: 3
#
# A FLOOR, not a target: the off run leaves the syslog statement out, so the
# collector receives nothing and the four assertions about what it received
# must go red. Losing one of them is what this number is here to catch.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api

MODE=${1:-with-syslog}
HX="python3 /src/src/prismash/prismash.py"

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create host-interface name veth-vpp >/dev/null
vppctl set interface state host-veth-vpp up
# THE STREAM IS A KERNEL SOCKET, not a packet the dataplane forwards, so the
# collector has to be reachable from the VPP PROCESS - which means a linux_cp
# pair mirroring this interface as a TAP. Made through the VTY, the way the
# box makes it, for the reason lab/ipfix-tcp.sh records: with `vppctl lcp
# create` here, the lab proved a transport that worked only on a box somebody
# had paired by hand (#182).
$HX -c "configure" -c "interface host-veth-vpp" -c "linux-name vppmgmt0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'
vppctl set interface mtu 1500 host-veth-vpp
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $PEERMAC

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1

vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.16/32 block 16384 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa block-holddown 4

# The collector listens FIRST, for the reason lab/ipfix.sh gives about
# templates: a collector that started after the exporter cannot tell a box
# that sent early from a collector that joined late.
REPORT=/tmp/syslog-report.txt
LINES_FILE=/tmp/syslog-lines.txt
rm -f "$REPORT" "$LINES_FILE"
# THE WINDOW IS THE COLLECTOR'S OWN, and the lab waits it out rather than
# killing it: the report is written when the window closes, so a lab that
# stopped the process to hurry it along read an empty file and reported that
# nothing arrived - which is exactly the failure it would report about a
# broken transport.
# The lines themselves, because the identity half of this lab is about WHAT a
# record says and not how many there are. The collector writes them only for a
# caller that names a file, which is this one.
ip netns exec peer python3 /src/tools/syslog_collector.py 601 60 "$REPORT" \
  "$LINES_FILE" > /tmp/syslog-collector.out 2>&1 &
COL=$!
sleep 2

echo
echo "=== the statement, refused without an enterprise number ==="
# The refusal an operator meets first, and it is at the prompt rather than in
# a year of lines a collector misparsed.
echo
echo "=== a syslog never enabled, disabled: the dataplane stays up (#551) ==="
# Through 0.149.7 the stream's descriptor began as 0 - stdin - and this closed
# it: the next vppctl session was handed descriptor 0, and when it ended VPP
# took it for its console closing and exited with status 0, which Docker does
# not restart. Measured on the legacy bench: fd 0 /dev/null before, gone after,
# VPP exited on the next `vppctl show version`.
vppctl bpa logging syslog disable >/dev/null 2>&1
vppctl show version >/dev/null 2>&1
vppctl show version >/dev/null 2>&1
sleep 1
if vppctl show version >/dev/null 2>&1; then NEVER_ON=up; else NEVER_ON=gone; fi
echo "  the dataplane after it: $NEVER_ON"

NO_PEN=$($HX -c "configure" \
    -c "nat" -c "cgnat" -c "logging syslog 10.0.0.2" \
    -c "commit" 2>&1)
printf '%s\n' "$NO_PEN" | sed 's/^/  /'

if [ "$MODE" != no-syslog ]; then
  echo
  echo "=== configuring the syslog collector, through the VTY ==="
  VTY_OUT=$($HX -c "configure" \
      -c "nat" -c "cgnat" -c "logging syslog 10.0.0.2 enterprise 44444 port 601" \
      -c "commit" 2>&1)
  printf '%s\n' "$VTY_OUT" | sed 's/^/  /'
else
  echo
  echo "=== no-syslog: the transport is never configured ==="
  VTY_OUT="(the syslog statement was left out)"
  printf '%s\n' "$VTY_OUT" | sed 's/^/  /'
fi

echo
echo "=== the running configuration the dataplane reads back ==="
vppctl show bpa config | tr -d '\r' | grep '^cgnat logging' | sed 's/^/  /'

echo
echo "=== who held the address, read from a file (#21) ==="
# THE ASSERTION THIS LAB EXISTS FOR, on the identity half: the record must
# carry the identity that was in force AT THE ALLOCATION, not the one that
# replaced it while the log was being drained. So the file is loaded, the
# subscribers allocate, and the file is then REWRITTEN with different names
# before the records go out.
cat > /tmp/subscribers.map <<'MAP'
# written by a provisioning system, one line per subscriber
100.64.0.5 joao.silva@isp
100.64.0.6 maria.souza@isp
100.64.0.9 pppoe-4471
MAP
IDENT_OUT=$($HX -c "configure" \
    -c "nat" -c "cgnat" -c "identity file /tmp/subscribers.map" \
    -c "commit" 2>&1)
printf '%s\n' "$IDENT_OUT" | sed 's/^/  /'
vppctl show bpa identity | tee /tmp/identity-show.txt | sed 's/^/  /'

echo
echo "=== 3 subscribers: 3 block allocations ==="
for sub in 5 6 9; do
  vppctl packet-generator new "name s$sub limit 1 node ip4-input interface pg0 size 60-60 \
      data { UDP: 100.64.0.$sub -> 8.8.8.8 \
             UDP: 40000 -> 53 \
             incrementing 8 }"
done
vppctl packet-generator enable >/dev/null; sleep 1
for sub in 5 6 9; do vppctl packet-generator delete s$sub >/dev/null 2>&1; done

echo
echo "=== and the same table from a DHCP lease file (#21) ==="
# The other grammar, on a box that already has the answer. Two leases that
# count and two that must not: one freed with the old client's name still in
# it, and one renewed - where the LAST lease is the current subscriber.
cat > /tmp/dhcpd.leases <<'LEASES'
lease 100.64.0.5 {
  starts 4 2026/09/18 06:00:00;
  binding state active;
  hardware ethernet bc:24:11:ea:db:12;
  client-hostname "cpe-OLD";
}
lease 100.64.0.5 {
  starts 4 2026/09/18 07:00:00;
  binding state active;
  hardware ethernet bc:24:11:ea:db:12;
  client-hostname "cpe-RENEWED";
}
lease 100.64.0.6 {
  binding state free;
  hardware ethernet 02:00:00:00:00:07;
  client-hostname "gone-last-week";
}
lease 100.64.0.9 {
  binding state active;
  hardware ethernet 02:00:00:00:00:09;
}
LEASES
$HX -c "configure" -c "nat" -c "cgnat" -c "identity dhcp-leases /tmp/dhcpd.leases" \
    -c "commit" 2>&1 | tail -1 | sed 's/^/  leases: /'
vppctl show bpa identity | tee /tmp/identity-leases.txt | sed 's/^/  /'

echo
echo "=== and from the accounting a BNG sends (#21) ==="
# THE THIRD SOURCE, end to end: a NAS that speaks RFC 2866 at this box, and a
# subscriber whose record then carries the name that arrived over the wire.
# tools/radius_nas.py builds those packets in Python from the RFC; the box
# reads them in C. Two implementations that agree is the evidence a fixture
# written beside the code cannot give.
$HX -c "configure" \
    -c "nat" -c "cgnat" -c "identity radius secret lab-s3cr3t port 1813" \
    -c "commit" 2>&1 | tail -1 | sed 's/^/  listener: /'

python3 /src/tools/radius_nas.py 127.0.0.1 1813 lab-s3cr3t start \
    100.64.0.11 radius-user@isp 2>&1 | sed 's/^/  start: /'
# A packet signed with the wrong secret, which must be counted and NOT
# applied: without that check anybody who reaches the port names anybody.
python3 /src/tools/radius_nas.py 127.0.0.1 1813 wrong-secret start \
    100.64.0.12 SHOULD-NOT-APPEAR 2>&1 | sed 's/^/  forged: /'
sleep 2
vppctl show bpa identity | tee /tmp/identity-radius.txt | sed 's/^/  /'

echo
echo "=== the file is rewritten before the records go out ==="
# Everything moves to a different name. A record naming one of THESE is a
# record resolved at drain time, which is the defect #21 is about.
cat > /tmp/subscribers.map <<'MAP'
100.64.0.5 SOMEBODY-ELSE-5
100.64.0.6 SOMEBODY-ELSE-6
100.64.0.9 SOMEBODY-ELSE-9
MAP
$HX -c "configure" -c "nat" -c "cgnat" -c "identity file /tmp/subscribers.map" \
    -c "commit" 2>&1 | tail -1 | sed 's/^/  reloaded: /'

echo
echo "=== forcing expiry to produce the 3 releases ==="
vppctl bpa timeouts udp 2
n=0
until vppctl show bpa blocks | grep -q "total: 0/3" || [ "$n" -ge 25 ]; do sleep 1; n=$((n+1)); done
echo "  blocks handed back in ${n}s"

echo
echo "=== the path the stream takes, which is the kernel's and not VPP's ==="
ip -br addr show vppmgmt0 2>&1 | sed 's/^/  /'
ip route get 10.0.0.2 2>&1 | head -1 | sed 's/^/  /'
ping -c1 -W2 10.0.0.2 2>&1 | tail -2 | sed 's/^/  /'
vppctl show bpa logging 2>&1 | tee /tmp/syslog-show.txt | sed 's/^/  /'

# The collector's window is waited out, not cut short.
wait "$COL" 2>/dev/null
C=$(cat "$REPORT" 2>/dev/null || cat /tmp/syslog-collector.out 2>/dev/null || echo "")
echo
echo "=== what the collector received ==="
printf '%s\n' "$C" | sed 's/^/  /'

# LAST, AND THAT IS NOT TIDINESS. This section configures secrets and sends
# accounting, so it leaves identities and sessions behind - and in the middle
# of the file it took four later checks red with it: the release lines, their
# identities and the collector's connection all read a box it had moved on.
# The same lab passes on master, which is how that was found. Here, after the
# collector has been drained and before the verdicts are computed, it changes
# nothing that is still to be measured.
echo "=== how long a secret this box keeps (#424) ==="
# THE ONE ENTRY IN config.TEXT_LIMITS WITH NO MEASUREMENT UNDER IT, and it
# cannot be asked the way the other two were: `name[64]` refuses a
# 64-character name with retval -7 and `tag[64]` keeps 63 of whatever it is
# given, both readable off the box - but the box does not print a secret.
# Correctly: a screen that echoes a shared secret is a screen somebody
# photographs.
#
# So it is asked with a PACKET. RFC 2866 signs the Accounting-Request with
# MD5 over the packet and the secret, so a NAS that is answered has proved
# the box holds the secret it signed with, and one met with silence has
# proved it does not.
#
# HERE, AND NOT IN A BOX OF ITS OWN: four attempts at a scaffolding for this
# ended with a dataplane that never answered a correctly-signed packet, so
# every case read "accepted" - including a control signed with a deliberately
# wrong secret. This lab already exchanges accounting with this box and
# already asserts that a forged packet is refused, which is what makes an
# answer here mean anything.
# BISECTED, because 127 fails too and the field is 128: whatever draws the
# line is not the API's declared width. Each length gets its own secret, its
# own subscriber and its own file, so the verdict can name the boundary.
# A LONG SECRET IS STILL ANSWERED (#425), which is what this box could not
# do: bpa_radius_acct_response() built the reply in a 128-byte scratch with
# the 20-byte header in front of the secret, so `20 + slen > 128` returned 0
# for every secret longer than 108 characters - and the caller reads 0 as
# "do not reply".
#
# Nothing said no anywhere. The packet VERIFIED and the accounting was
# APPLIED; what was lost was the Accounting-Response, so a NAS retransmits
# for ever and logs this box as dead while its records are arriving.
#
# Driven here as well as in test/test_bpa_radius.c because the harness calls
# the function and this calls the BOX: the fix is a buffer, and a buffer is
# exactly the kind of thing that is right in a harness and wrong in the
# process, if the caller hands it something else.
for _len in 108 109 127; do
  _sec=$(python3 -c "print('K' * $_len)")
  $HX -c "configure" \
      -c "nat" -c "cgnat" -c "identity radius secret $_sec port 1813" \
      -c "commit" >/dev/null 2>&1
  python3 /src/tools/radius_nas.py 127.0.0.1 1813 "$_sec" start \
      100.64.0.13 "s$_len@isp" > /tmp/sec-$_len.txt 2>&1
  if grep -q "accounting-response" /tmp/sec-$_len.txt; then
    echo "  a secret of $_len: answered"
  else
    echo "  a secret of $_len: SILENCE"
  fi
done
# AND WHAT A 128-CHARACTER SECRET BECOMES. With the response scratch fixed,
# 128 is still met with silence - so something else shortens it, and a NAS
# signing with the first 127 says so: if that one is answered, the box is
# holding 127 of the 128 it was given.
_sec128=$(python3 -c "print('K' * 128)")
_pre127=$(python3 -c "print('K' * 127)")
$HX -c "configure" -c "nat" -c "cgnat" -c "identity radius secret $_sec128 port 1813" \
    -c "commit" >/dev/null 2>&1
python3 /src/tools/radius_nas.py 127.0.0.1 1813 "$_pre127" start \
    100.64.0.23 pre127@isp > /tmp/pre127.txt 2>&1
grep -q "accounting-response" /tmp/pre127.txt \
  && echo "  configured 128, signed with its first 127: ANSWERED" \
  || echo "  configured 128, signed with its first 127: silence"
vppctl show bpa identity | grep -E "refused" | sed 's/^/    /'

# AND THROUGH THE API DOOR, which says whether the VTY is shortening it or
# the wire format is: vat2 encodes the same `string secret[128]` field.
_sec128b=$(python3 -c "print('Q' * 128)")
_pre127b=$(python3 -c "print('Q' * 127)")
vat2 bpa_identity_radius_set \
    "{\"enable\": true, \"port\": 1813, \"secret\": \"$_sec128b\"}" >/dev/null 2>&1
python3 /src/tools/radius_nas.py 127.0.0.1 1813 "$_sec128b" start \
    100.64.0.24 api128@isp > /tmp/api128.txt 2>&1
python3 /src/tools/radius_nas.py 127.0.0.1 1813 "$_pre127b" start \
    100.64.0.25 api127@isp > /tmp/api127.txt 2>&1
grep -q "accounting-response" /tmp/api128.txt \
  && echo "  API, configured 128, signed with 128: ANSWERED" \
  || echo "  API, configured 128, signed with 128: silence"
grep -q "accounting-response" /tmp/api127.txt \
  && echo "  API, configured 128, signed with its first 127: ANSWERED" \
  || echo "  API, configured 128, signed with its first 127: silence"

# AND HOW LONG A PATH THIS BOX OPENS (#424, then #553). #424 saw the box take
# 257 when the VTY sent over vat2, which carried more than the declared
# `string path[256]`. Since #527 the VTY sends over vpp_papi, which refuses a
# string longer than its field less one, and the dataplane copies the field
# into char[256]: 255 is what both ends hold. On 0.149.7 this measured 255
# taken and 256 refused at SEND ("cannot be sent over the binary API") while
# the VTY's own limit still said 256 - the refusal at the wrong place.
#
# Built out of nested directories, because a single component is capped at
# 255 bytes by the filesystem and that cap is not the one being measured.
mkdir -p /tmp/deep
_dir=/tmp/deep
while [ ${#_dir} -lt 1100 ]; do
  _dir="$_dir/dddddddddddddddddddddddddddddddddddddddddddddddddd"
  mkdir -p "$_dir" 2>/dev/null || break
done
for _want in 254 255 256 1000; do
  # A path of about _want characters, ending in a file that exists and holds
  # one mapping: a path cut short names a file that is not there, and the
  # load then brings in no entries - which is the reading, not the retval.
  _p=$(python3 -c "
import os, sys
want = $_want
base = '$_dir'
parts = base.split('/')
p = ''
for part in parts:
    if len(p) + len(part) + 1 > want - 12:
        break
    p = p + '/' + part if part else p
if not p:
    p = '/tmp'
pad = 'f' * max(1, want - len(p) - 1)
print(p + '/' + pad[:200])")
  printf '100.64.0.40 path-%s\n' "$_want" > "$_p" 2>/dev/null || {
    echo "  a path of $_want: the filesystem would not make it"; continue; }
  $HX -c "configure" -c "nat" -c "cgnat" -c "identity file $_p" -c "commit" \
      > /tmp/path-$_want.txt 2>&1
  _n=$(vppctl show bpa identity | sed -n 's/identity table: \([0-9]*\) entry.*/\1/p')
  # "commit complete" is NOT the reading: a statement refused at the prompt
  # leaves an unchanged candidate, and committing that completes too. What
  # says the path was taken is an error line, and the mapping this file
  # holds appearing on the box.
  if grep -qi "error" /tmp/path-$_want.txt; then
    echo "  a path of $(printf '%s' "$_p" | wc -c): REFUSED - $(grep -i error /tmp/path-$_want.txt | head -1 | cut -c1-64)"
  elif vppctl show bpa identity | grep -q "entry"; then
    _who=$(vppctl show bpa subscriber 100.64.0.40 2>/dev/null | grep -o "path-[0-9]*" | head -1)
    echo "  a path of $(printf '%s' "$_p" | wc -c): taken, and the box read ${_n:-0} entry(s) [${_who:-no name}]"
  else
    echo "  a path of $(printf '%s' "$_p" | wc -c): no error and no entries"
  fi
done
$HX -c "configure" -c "nat" -c "cgnat" -c "identity file /tmp/subscribers.map" -c "commit" \
    >/dev/null 2>&1

# And back to a short one, so the section leaves the box as it found it and
# the next section's own secret is the one it expects.
$HX -c "configure" -c "nat" -c "cgnat" -c "identity radius secret lab-s3cr3t port 1813" \
    -c "commit" >/dev/null 2>&1

sleep 2
vppctl show bpa identity | tee /tmp/identity-secret.txt | sed 's/^/  /'


. /src/lab/verdict.sh

count_of() {   # <MSGID>; how many the collector counted, or 0
  printf '%s\n' "$C" | awk -v k="$1" '$1 == k { print $2 }' | head -1
}

ALLOC=$(count_of BLOCK-ALLOC); ALLOC=${ALLOC:-0}
REL=$(count_of BLOCK-RELEASE); REL=${REL:-0}
MARK=$(count_of BOOT-MARKER);  MARK=${MARK:-0}
BAD=$(count_of malformed);     BAD=${BAD:-0}

printf '%s\n' "$NO_PEN" > /tmp/syslog-no-pen.txt
expect "disabling a syslog that was never enabled leaves the dataplane running (#551)" \
  test "$NEVER_ON" = up
expect "the refusal without an enterprise number names what is missing" \
  grep -q "enterprise number is required" /tmp/syslog-no-pen.txt
expect "3 allocations reached the collector as BLOCK-ALLOC lines (got $ALLOC)" \
  test "$ALLOC" = 3
expect "and 3 releases as BLOCK-RELEASE lines (got $REL)" \
  test "$REL" = 3
expect "the boot marker arrived too, so a collector knows the incarnation (got $MARK)" \
  test "$MARK" -ge 1
expect "and nothing was malformed: every frame was a length and that many bytes (got $BAD)" \
  test "$BAD" = 0

# AND THE SCREEN AN OPERATOR READS ABOUT IT. A transport whose state nobody
# can see is one they diagnose by guessing, and the two states have to be
# distinguishable: "disabled" and "connected" are different diagnoses.
if [ "$MODE" != no-syslog ]; then
  expect "'show bpa logging' names the syslog transport and its RFC" \
    grep -q "syslog transport (RFC 5424 over TCP)" /tmp/syslog-show.txt
  expect "and says the collector it is connected to" \
    grep -qE "collector +10\.0\.0\.2:601 +connected" /tmp/syslog-show.txt
  expect "and the enterprise number every line carries" \
    grep -q "enterprise  44444" /tmp/syslog-show.txt
  # THE IDENTITY, and the instant it belongs to.
  expect "the allocation lines name who held the address then" \
    grep -q 'subscriberId="joao.silva@isp"' "$LINES_FILE"
  # ON THE ALLOCATION LINES, and that is the whole precision of it: the
  # identity in force AT THE ALLOCATION, never the one that replaced it.
  grep BLOCK-ALLOC "$LINES_FILE" > /tmp/alloc-lines.txt 2>/dev/null || true
  expect "and no allocation line names the identity that replaced it" \
    sh -c "! grep -q 'SOMEBODY-ELSE' /tmp/alloc-lines.txt"

  # AND ON THE RELEASE LINES, which is #397 and was a consequence until the
  # maintainer decided it: the release repeats what the allocation said,
  # rather than naming whoever holds the private address when the block goes
  # back. The map was rewritten between the two, so a release resolved at its
  # own instant names SOMEBODY-ELSE and one carried with the block does not -
  # and this lab is the only place the difference is visible.
  grep BLOCK-RELEASE "$LINES_FILE" > /tmp/release-lines.txt 2>/dev/null || true
  expect "the release lines name the subscriber the ALLOCATION named (#397)" \
    grep -q 'subscriberId="joao.silva@isp"' /tmp/release-lines.txt
  expect "and no release line names the identity that replaced it" \
    sh -c "! grep -q 'SOMEBODY-ELSE' /tmp/release-lines.txt"
  expect "all three releases carry an identity, not just the first" \
    sh -c "test $(grep -c 'subscriberId=' /tmp/release-lines.txt) = 3"
  # The pair, which is what a court order is answered from: one block, two
  # records, one subscriber.
  expect "and the two records of one block agree about who held it" \
    sh -c "test $(grep -o 'subscriberId=\"[^\"]*\"' /tmp/alloc-lines.txt \
                  /tmp/release-lines.txt | cut -d: -f2- | sort -u | wc -l) = 3"
  expect "the allocation lines are the three subscribers, each named" \
    sh -c "test $(grep -c 'subscriberId=' /tmp/alloc-lines.txt) = 3"
  # THE LEASE FILE'S OWN RULES, read off the box: two of the four leases
  # count - the renewed one and the one with no hostname - and the freed
  # lease's name is not among them.
  expect "a lease file yields one entry per active lease, renewals folded" \
    grep -q "2 entry(s)" /tmp/identity-leases.txt
  expect "and no lease that is not active is in it" \
    sh -c "! grep -q 'gone-last-week' /tmp/identity-leases.txt"
  # THE ACCOUNTING HALF, read off the box.
  expect "the accounting Start added the subscriber it named" \
    grep -qE "identity table: [0-9]+ entry" /tmp/identity-radius.txt
  expect "and the listener answered the NAS" \
    grep -qE "packets +[0-9]+ received, [1-9][0-9]* applied, [1-9]" \
      /tmp/identity-radius.txt
  expect "a packet signed with another secret is counted and not applied" \
    grep -qE "refused +[1-9][0-9]* bad authenticator" /tmp/identity-radius.txt
  # A LONG SECRET IS ANSWERED (#425). The NAS prints "accounting-response"
  # only when the box replied, and the box replies only to a packet it
  # verified - so these three are the box saying it can both read and answer
  # a secret of that length. 108 is the boundary the defect drew: it is here
  # so that a scratch sized back down is caught on the box and not only in
  # the harness.
  expect "a secret of 108 characters is answered" \
    grep -q "accounting-response" /tmp/sec-108.txt
  expect "and one of 109, which used to be met with silence" \
    grep -q "accounting-response" /tmp/sec-109.txt
  expect "and one of 127, which is what a 128-byte field holds" \
    grep -q "accounting-response" /tmp/sec-127.txt
  # THE LAST ROW OF config.TEXT_LIMITS (#424, #553). A path of 255 is taken
  # and the box reads its entry; one of 256 is refused AT THE PROMPT, naming
  # the limit - not at the send, where it was refused on 0.149.7 by vpp_papi
  # with a sentence about the binary API.
  expect "an identity path of 255 characters is taken" \
    sh -c "! grep -qi error /tmp/path-255.txt"
  expect "and one of 256 is refused at the prompt, naming the limit" \
    grep -q "and the limit is 255" /tmp/path-256.txt
  expect "'show bpa identity' counts the entries and prints none of them" \
    sh -c "grep -q '3 entry(s)' /tmp/identity-show.txt && ! grep -q '@isp' /tmp/identity-show.txt"
else
  expect "'show bpa logging' says the syslog transport is disabled" \
    grep -q "syslog transport: disabled" /tmp/syslog-show.txt
fi

verdict
