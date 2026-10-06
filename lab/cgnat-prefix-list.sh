#!/bin/sh
# CGNAT pool sides as prefix-lists: deny inside is untranslated, deny outside is never used (#589).
#
#   sh tools/lab.sh cgnat-prefix-list        the pool's sides are prefix-lists
#   sh tools/lab.sh cgnat-prefix-list off    the same pool typed as prefixes,
#                                            no lists - the list checks MUST fail
#
# Through the VTY end to end, because the feature is the VTY's: FRR holds the
# lists, the VTY resolves them, and the dataplane is handed the effective sets
# - permits less denies - with a new set of its own for what an inside deny
# lets past. So the lab asks three things and the third is what a unit test
# cannot: what the dataplane holds, what the VTY refuses, and what a packet
# from a denied source looks like on the wire.
#
# The off run types the same addresses as plain prefixes. Every packet then
# translates - 100.64.1.0/24 included, since it is inside 100.64.0.0/22 - and
# 200.150.0.17 is a public address like the others, so each check that says a
# list did something goes red, and the controls stay green.
#
# OFF-RED-AT-LEAST: 22
#
# A FLOOR, measured on the first off run of this lab (#589); see lab/bypass.sh
# for why it is a floor and not a target.
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
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

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

echo "=========== 1. the lists, and a pool that names them ==========="
if [ "$MODE" = off ]; then
  echo "  MODE=off: the same pool typed as prefixes, no lists."
  SIDE_IN="prefix-inside 100.64.0.0/22"
  SIDE_OUT="prefix-outside 200.150.0.16/30"
else
  SIDE_IN="prefix-list-inside CGN-IN"
  SIDE_OUT="prefix-list-outside CGN-OUT"
fi
C1=$($HX -c "configure" \
    -c 'ip prefix-list CGN-IN seq 5 deny 100.64.1.0/24 description "management range"' \
    -c "ip prefix-list CGN-IN seq 10 permit 100.64.0.0/22 le 32" \
    -c "ip prefix-list CGN-OUT seq 5 deny 200.150.0.17/32" \
    -c "ip prefix-list CGN-OUT seq 10 permit 200.150.0.16/30" \
    -c "nat" -c "cgnat" -c "pool lab" -c "$SIDE_IN" -c "$SIDE_OUT" \
    -c "block-size 4096" \
    -c "mode deterministic" -c "exit" -c "exit" -c "exit" -c "commit" 2>&1)
echo "$C1" | sed 's/^/  /'
CONF=$(vppctl show bpa config)
echo "$CONF" | sed -n '/cgnat pool lab/,/exit/p' | sed 's/^/  /'
RUN=$($HX -c "show running-config" 2>&1)
echo "$RUN" | grep -E "pool lab|prefix-list|prefix-inside|prefix-outside" | sed 's/^/  /'

echo
echo "=========== 2. a denied source leaves with its own address ==========="
S0=$(sess)
send 100.64.1.7 8.8.8.8 40001
DENY_TRACE=$(trace); DENY_WIRE=$(wire_src); S1=$(sess)
echo "$DENY_TRACE" | sed 's/^/  /'
echo "  source on the wire: $DENY_WIRE; sessions $S0 -> $S1"

echo
echo "=========== 3. permitted sources translate, never to the excluded one ==="
WIRES=""
for h in 1 2 3 4 5 6; do
  send "100.64.0.$h" 8.8.8.8 $((41000 + h))
  WIRES="$WIRES $(wire_src)"
done
echo "  sources on the wire:$WIRES"

echo
echo "=========== 4. show cgnat pool lab ==========="
SHOWP=$($HX -c "show cgnat pool lab" 2>&1)
echo "$SHOWP" | sed 's/^/  /'
ERR=$(vppctl show errors | grep -i "untranslated")
vppctl show errors | grep -i "bpa-in2out" | sed 's/^/  /'
TRAIL=/var/log/prisma-routing/config-history.log
LAST=$(grep "pool lab" $TRAIL 2>/dev/null | tail -1)
echo "  trail: $LAST" | cut -c1-300

echo
echo "=========== 5. a list change shows the pools it moves, and moves them ==="
MARK=$(wc -l < $TRAIL 2>/dev/null || echo 0)
CMP=$($HX -c "configure" -c "ip prefix-list CGN-OUT seq 10 permit 200.150.0.16/29" \
      -c "show configuration compare" -c "commit remap" 2>&1)
echo "$CMP" | sed 's/^/  /'
NEW=$(tail -n +$((MARK + 1)) $TRAIL 2>/dev/null)
CONF2=$(vppctl show bpa config)

echo
echo "=========== 6. what the commit refuses ==========="
BOTH=$($HX -c "configure" -c "nat" -c "cgnat" -c "pool lab" \
      -c "prefix-inside 100.64.8.0/24" -c "exit" -c "exit" -c "exit" \
      -c "commit check" 2>&1)
echo "$BOTH" | sed 's/^/  /'
GONE=$($HX -c "configure" -c "nat" -c "cgnat" -c "pool lab" \
      -c "prefix-list-inside NO-SUCH" -c "exit" -c "exit" -c "exit" \
      -c "commit check" 2>&1)
echo "$GONE" | sed 's/^/  /'
EMPTY=$($HX -c "configure" -c "ip prefix-list CGN-OUT seq 1 deny 200.150.0.0/24 le 32" \
      -c "commit check" 2>&1)
echo "$EMPTY" | sed 's/^/  /'
INERT=$($HX -c "configure" -c "ip prefix-list CGN-IN seq 20 permit 100.64.0.0/22 le 24" \
      -c "commit check" 2>&1)
echo "$INERT" | sed 's/^/  /'

echo
echo "=========== 7. insert and resequence, Junos-style ==========="
MOVE=$($HX -c "configure" -c "ip prefix-list CGN-IN insert seq 10 before seq 5" \
      -c "show configuration" -c "ip prefix-list CGN-IN resequence" \
      -c "show configuration" 2>&1)
echo "$MOVE" | grep "prefix-list CGN-IN" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

expect_in "the commit was taken" "commit complete" "$C1"
# What the dataplane holds: the effective sets, the names and the excluded.
expect_in "the dataplane names the inside list" "prefix-list-inside CGN-IN" "$CONF"
expect_in "and the outside list" "prefix-list-outside CGN-OUT" "$CONF"
expect_in "its inside is the permits less the deny" " inside 100.64.2.0/23" "$CONF"
expect "and the denied /24 is not in it" sh -c "! printf '%s' \"\$1\" | grep -q ' inside 100.64.1.0/24'" _ "$CONF"
expect_in "the deny is held as untranslated" "untranslated 100.64.1.0/24" "$CONF"
expect_in "the excluded public address is held as such" \
  "outside-excluded 200.150.0.17/32" "$CONF"
expect_in "and the outside is what is left of the /30" \
  " outside 200.150.0.18/31" "$CONF"
expect_in "show running-config writes the list, not the prefixes it gave" \
  "prefix-list-inside CGN-IN" "$RUN"
expect_in "and keeps the entry's description" \
  'deny 100.64.1.0/24 description "management range"' "$RUN"

expect_in "a denied source leaves with its own address" "100.64.1.7" "$DENY_WIRE"
expect_in "and the trace says it was not translated" "bypassed, not translated" \
  "$DENY_TRACE"
expect "and no session was created for it" test "${S0:-0}" -eq "${S1:-99}"
expect_in "the counter names why" "prefix-list deny, left untranslated" "$ERR"
expect_in "permitted sources translate (control)" "200.150.0." "$WIRES"
expect "and none of them to the excluded 200.150.0.17" \
  sh -c "! printf '%s' \"\$1\" | grep -q '200.150.0.17\\b'" _ "$WIRES"

expect_in "show cgnat pool names the inside list" "prefix-list CGN-IN" "$SHOWP"
expect_in "and what it leaves untranslated" "untranslated" "$SHOWP"
expect_in "and what the outside list excluded" "excluded" "$SHOWP"
expect_in "the trail records the untranslated range" \
  "untranslated 100.64.1.0+256" "$LAST"
expect_in "and the excluded public address" \
  "outside-excluded 200.150.0.17+1" "$LAST"

expect_in "show configuration compare shows the pool a list change moves" \
  "! lab translates to 200.150.0.20/30" "$CMP"
expect_in "the list change rebuilt the pool" "del pool lab" "$NEW"
expect_in "and the dataplane has the wider outside" \
  " outside 200.150.0.20/30" "$CONF2"

expect_said "both forms on one side are refused, naming the pool and side" \
  "pool 'lab': prefix-inside and prefix-list-inside both given" "$BOTH"
expect_said "a list that does not exist is refused" \
  "pool 'lab': prefix-list-inside NO-SUCH does not exist" "$GONE"
expect_said "an outside deny that empties the pool is refused" \
  "pool 'lab': prefix-list-outside CGN-OUT leaves no public address" "$EMPTY"
expect_said "an entry that can match no address is refused, by name" \
  "seq 20 permit 100.64.0.0/22 le 24 matches no address" "$INERT"
expect_in "insert moved seq 10 ahead of seq 5" "CGN-IN seq 2 permit" "$MOVE"
expect_in "resequence renumbered by 10" "CGN-IN seq 20 deny 100.64.1.0/24" "$MOVE"

verdict
