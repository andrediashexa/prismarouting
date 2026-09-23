#!/bin/sh
# NAT bypass: prefixes that leave untranslated, and allocate nothing.
#
#   sh tools/lab.sh bypass        with the lists configured
#   sh tools/lab.sh bypass off    without them - the bypass checks MUST fail
#
# Two halves, and the second is the one that matters: the packet leaves with
# its own source address, AND no session was created for it. A bypass
# implemented after the allocation passes the first half and fails the second,
# which is exactly the mistake this lab exists to catch.
#
# OFF-RED-AT-LEAST: 12
#
# A FLOOR, not a target, and the number is measured rather than chosen: it is
# what this lab's off run went red on in the release suite of 2026-09-13.
# tools/run-all-labs.sh refuses an off run that goes red on fewer, because a
# lab that used to fail eleven checks and now fails one has lost ten assertions
# without losing its verdict - which is the defect this whole sweep exists to
# find. Gaining assertions is fine and needs no edit here; losing them is what
# has to be deliberate.
set -u
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
use_binary_api
HX="python3 /src/src/prismash/prismash.py"

mkdir -p /var/run/frr /etc/frr
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr 2>/dev/null
start_frr

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
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
# The bypassed destination has to be routable, or the packet would be dropped
# after the bypass and the lab would be measuring the route and not the bypass.
vppctl ip route add 10.0.0.0/8 via 200.150.0.2 pg1
vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa timeouts udp 600

send() { # send <src> <dst> <sport>
  rm -f /tmp/cap.pcap
  vppctl packet-generator capture pg1 pcap /tmp/cap.pcap count 3 >/dev/null
  vppctl packet-generator new "name s limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: $1 -> $2 \
                        UDP: $3 -> 53 \
                        incrementing 8 }" >/dev/null
  vppctl clear trace >/dev/null
  vppctl trace add pg-input 5 >/dev/null
  vppctl packet-generator enable >/dev/null
  sleep 1
  vppctl packet-generator delete s >/dev/null 2>&1
}
trace() { vppctl show trace | grep "BPA in2out"; }
sess() { vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}'; }
# The source address as it left, read off the wire rather than off the trace.
wire_src() { python3 - <<'EOF'
import struct
try:
    d = open("/tmp/cap.pcap", "rb").read()
except FileNotFoundError:
    print("no-capture"); raise SystemExit
e = "<" if d[:4] in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
link = struct.unpack(e + "I", d[20:24])[0]
off = 24
while off + 16 <= len(d):
    _, _, caplen, _ = struct.unpack(e + "IIII", d[off:off+16])
    off += 16
    p = d[off:off+caplen]; off += caplen
    ip = p[14:] if link == 1 else p
    if len(ip) >= 20 and (ip[0] >> 4) == 4 and ip[9] == 17:
        print(".".join(str(b) for b in ip[12:16]))
        raise SystemExit
print("no-udp")
EOF
}

echo "=========== 1. before the lists: everything is translated ==========="
echo "the same two packets the bypass will later exempt, so that 'left"
echo "untranslated' below is a change and not the way this box always behaved."
B0=$(sess)
send 100.64.0.5 10.1.1.1 40001
BEFORE_DST=$(trace); BEFORE_DST_WIRE=$(wire_src)
send 100.64.1.7 8.8.8.8 40002
BEFORE_SRC=$(trace); BEFORE_SRC_WIRE=$(wire_src)
B1=$(sess)
echo "  to 10.1.1.1   : $BEFORE_DST_WIRE on the wire"
echo "  from 100.64.1.7: $BEFORE_SRC_WIRE on the wire"
echo "  sessions went from $B0 to $B1"

echo
echo "=========== 2. configuring the lists ==========="
if [ "$MODE" = off ]; then
  echo "  MODE=off: nothing is bypassed. Every check below that claims a"
  echo "  packet left untranslated MUST fail."
else
  BYP_DST=$(vppctl bpa bypass lab destination 10.0.0.0/8 2>&1)
  BYP_SRC=$(vppctl bpa bypass lab source 100.64.1.0/24 2>&1)
  printf '%s\n%s\n' "$BYP_DST" "$BYP_SRC" | sed 's/^/  /'
  vppctl show bpa config | grep bypass | sed 's/^/  /'
fi

echo
echo "=========== 3. a bypassed DESTINATION ==========="
echo "expected: 100.64.0.5 reaches 10.1.1.1 with its own address, and no"
echo "session is created for it."
S0=$(sess)
send 100.64.0.6 10.1.1.1 40003
DST_TRACE=$(trace); DST_WIRE=$(wire_src); S1=$(sess)
echo "$DST_TRACE" | sed 's/^/  /'
echo "  source on the wire: $DST_WIRE"
echo "  sessions before: $S0, after: $S1"

echo
echo "=========== 4. a bypassed SOURCE ==========="
S2=$(sess)
send 100.64.1.8 8.8.8.8 40004
SRC_TRACE=$(trace); SRC_WIRE=$(wire_src); S3=$(sess)
echo "$SRC_TRACE" | sed 's/^/  /'
echo "  source on the wire: $SRC_WIRE"
echo "  sessions before: $S2, after: $S3"

echo
echo "=========== 5. everything else still translates ==========="
echo "the control: without it, a box that translated nothing at all would"
echo "pass every check above."
S4=$(sess)
send 100.64.0.7 8.8.8.8 40005
OK_TRACE=$(trace); OK_WIRE=$(wire_src); S5=$(sess)
echo "$OK_TRACE" | sed 's/^/  /'
echo "  source on the wire: $OK_WIRE"
echo "  sessions before: $S4, after: $S5"

echo
echo "=========== 6. counted, and counted apart ==========="
ERR=$(vppctl show errors | grep -i "bypassed")
echo "$ERR" | sed 's/^/  /'
POOL=$(vppctl show bpa pool | grep -i "bypass")
echo "$POOL" | sed 's/^/  /'

echo
echo "=========== 7. through the VTY, without recreating the pool ==========="
TRAIL=/var/log/prisma-routing/config-history.log
MARK=$(wc -l < $TRAIL 2>/dev/null || echo 0)
V0=$(sess)
if [ "$MODE" != off ]; then
  $HX -c "configure" -c "cgnat pool lab" \
      -c "bypass destination 192.0.2.0/24" -c "exit" -c "commit" 2>&1 |
    sed 's/^/  /'
fi
VTY_CONF=$(vppctl show bpa config | grep bypass)
NEW=$(tail -n +$((MARK + 1)) $TRAIL 2>/dev/null)
V1=$(sess)
echo "$VTY_CONF" | sed 's/^/  /'
echo "  sessions before the commit: $V0, after: $V1"

echo
echo "=========== 8. none clears it ==========="
if [ "$MODE" != off ]; then
  vppctl bpa bypass lab source none | sed 's/^/  /'
fi
send 100.64.1.9 8.8.8.8 40006
CLEARED=$(wire_src)
echo "  100.64.1.9 now leaves as: $CLEARED"

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

# The precondition. Without it, "left untranslated" could be true on a box
# that never translated those packets in the first place.
expect_in "before the lists, the destination case was translated" \
  "200.150.0." "$BEFORE_DST_WIRE"
expect_in "and so was the source case" "200.150.0." "$BEFORE_SRC_WIRE"

# WHAT THE COMMAND SAID WHEN IT TOOK THE LIST. An operator types this and
# reads one line back; until now nothing named that line, so it could have
# said a different number of prefixes, or the wrong direction, with every
# packet assertion below still green.
if [ "$MODE" != off ]; then
  expect_in "the command confirms how many prefixes it took, and which side" \
    "1 destination prefix(es) bypassed" "$BYP_DST"
  expect_in "and the same for the source list" \
    "1 source prefix(es) bypassed" "$BYP_SRC"
fi

expect_in "a bypassed destination keeps the real source on the wire" \
  "100.64.0.6" "$DST_WIRE"
expect_in "and the trace says so rather than showing a failed translation" \
  "bypassed, not translated" "$DST_TRACE"
# The half that catches a bypass implemented after the allocation.
expect "and no session was created for it" test "${S0:-0}" -eq "${S1:-99}"

expect_in "a bypassed source keeps its own address" "100.64.1.8" "$SRC_WIRE"
expect "and allocates nothing either" test "${S2:-0}" -eq "${S3:-99}"

expect_in "traffic outside the lists is still translated" \
  "200.150.0." "$OK_WIRE"
expect "and does create a session" test "${S5:-0}" -gt "${S4:-99}"

expect_in "source bypasses are counted" "bypassed source" "$ERR"
expect_in "and destination bypasses separately" "bypassed destination" "$ERR"
expect_in "the pool reports what it let past" "left untranslated" "$POOL"

expect_in "the VTY added a bypass on the dataplane" "192.0.2.0/24" "$VTY_CONF"
# Both of these were GREEN in `sh tools/lab.sh bypass off`, the run this
# file's header says the bypass checks MUST fail in: with no commit made, the
# trail is empty - so it carries no "del pool lab" - and no session was
# dropped because nothing happened. Predicated on the trail's record that the
# change was made, which is the only thing that makes either a statement.
case "$NEW" in *"bypass-destination pool lab"*) APPLIED=yes ;; *) APPLIED=no ;; esac
applied_and() { [ "$APPLIED" = yes ] && "$@"; }
DEL_IN=$(printf '%s\n' "$NEW" | grep -c "del pool lab" || true)
expect "the bypass change was applied at all, and recorded" \
  test "$APPLIED" = yes
expect "without recreating the pool" applied_and test "${DEL_IN:-1}" -eq 0
expect "and without dropping the sessions it held" \
  applied_and test "${V1:-0}" -ge "${V0:-0}"

expect_in "'none' put the source back to being translated" \
  "200.150.0." "$CLEARED"

verdict
