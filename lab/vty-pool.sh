#!/bin/sh
# Per-pool settings through the VTY, all the way to the dataplane.
#
# Four merged issues added settings that live on a pool - timeouts (#12),
# max-blocks (#13), the filtering mode (#15) and the ALG switches (#14) - and
# every one of them was unreachable through the VTY, which is the interface an
# operator actually uses. Their own labs pass because they drive vppctl.
#
# The oracle here is the DATAPLANE, never prismash. Asking prismash whether prismash
# stored something proves nothing: it stored it before, printed it back, and
# the device never heard about it.
set -u
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /etc/prisma-routing /var/log/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
use_binary_api

HX="python3 /src/src/prismash/prismash.py"
vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }
vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up

echo "=========== 1. every per-pool setting, typed into the VTY ==========="
$HX -c "configure" \
    -c "nat" -c "cgnat" -c "pool lab" \
    -c "prefix-inside 100.64.0.0/22" \
    -c "prefix-outside 200.150.0.16/30" \
    -c "block-size 512" \
    -c "mode dynamic" \
    -c "max-blocks 3" \
    -c "filter address-dependent" \
    -c "timeout udp 600" \
    -c "timeout gre 900" \
    -c "alg ftp off" \
    -c "exit" \
    -c "commit" 2>&1 | sed 's/^/  /'

echo
echo "=========== 2. what the DATAPLANE holds ==========="
CFG=$(vppctl show bpa config)
POOL=$(vppctl show bpa pool)
ALG=$(vppctl show bpa alg)
printf '%s\n' "$CFG" | sed 's/^/  |/'

echo
echo "=========== 3. a setting made with vppctl must survive a commit ==========="
# The failure this catches is not "cannot set it" - it is worse. The candidate
# is built FROM the running configuration, so a setting the model could not
# hold was dropped on the way in and REMOVED on the way out: committing any
# unrelated change silently reverted it.
vppctl bpa alg lab rtsp off >/dev/null
BEFORE=$(vppctl show bpa config | grep -c "alg rtsp off")
$HX -c "configure" -c "nat" -c "cgnat" -c "pool lab" -c "timeout icmp 90" -c "exit" \
    -c "commit" 2>&1 | sed 's/^/  /'
AFTER=$(vppctl show bpa config | grep -c "alg rtsp off")
ICMP=$(vppctl show bpa config | grep -c "timeout icmp 90")
echo "  'alg rtsp off' before the unrelated commit: $BEFORE, after: $AFTER"

echo
echo "=========== 4. a line the VTY cannot hold is refused, not trimmed ==========="
cat > /tmp/bad.conf <<'EOF'
! cgnat
nat
 cgnat
  pool lab
   prefix-inside 100.64.0.0/22
   prefix-outside 200.150.0.16/30
   block-size 512
   policy interleaved
   mode deterministic
   wibble 3
   exit
  exit
 exit
! routing
EOF
BAD=$($HX --load /tmp/bad.conf 2>&1); BADRC=$?
printf '%s\n' "$BAD" | sed 's/^/  /'

echo
echo "=========== 5. a static port forward, through the VTY ==========="
# A deterministic pool of its own, so this section measures the VTY path and
# not the block pin a forward on a dynamic pool also takes out (lab/forward.sh
# section 11 covers that); pool 'lab' above is dynamic.
#
#   10.0.0.5 is subscriber 5 of this pool -> 203.0.113.17, block 1088-1151
$HX -c "configure" \
    -c "nat" -c "cgnat" -c "pool fwd" \
    -c "prefix-inside 10.0.0.0/24" \
    -c "prefix-outside 203.0.113.16/30" \
    -c "block-size 64" \
    -c "forward tcp 1100 to 10.0.0.5 80" \
    -c "exit" \
    -c "commit" 2>&1 | sed 's/^/  /'
TRAIL=/var/log/prisma-routing/config-history.log
MARK=$(wc -l < $TRAIL 2>/dev/null || echo 0)
# A second forward, on a pool that already exists. This one must NOT redefine
# the pool: redefining it drops every session it holds, which is the whole
# subscriber base disconnected to give one customer an inbound port.
#
# The third forward is the same public PORT as the first, for a subscriber on
# a different public address of the same pool: 10.0.0.4 sends from
# 203.0.113.16 and 10.0.0.5 from 203.0.113.17, so 1100 on each of them is two
# sockets the pool has already given to two customers. The VTY has to let it
# through - it cannot judge which address a subscriber sends from, and
# refusing here made a port sellable once per pool.
$HX -c "configure" -c "nat" -c "cgnat" -c "pool fwd" \
    -c "forward udp 1101 to 10.0.0.5 53" \
    -c "forward tcp 1100 to 10.0.0.4 8080" -c "exit" -c "commit" 2>&1 |
  sed 's/^/  /'
FWD=$(vppctl show bpa forward)
CFG2=$(vppctl show bpa config)
NEW=$(tail -n +$((MARK + 1)) $TRAIL 2>/dev/null)
printf '%s\n' "$FWD" | sed 's/^/  /'
echo "  what the trail recorded for the second commit:"
printf '%s\n' "$NEW" | sed 's/^/    /'

echo
echo "=========== 6. a forward the device would refuse ==========="
# 2000 is in subscriber 61's block, not subscriber 5's. The check has to say
# so BEFORE the commit: after it, the operator has an inbound port that
# belongs to a customer who did not ask for one.
BADF=$($HX -c "configure" -c "nat" -c "cgnat" -c "pool fwd" \
           -c "forward tcp 2000 to 10.0.0.5 80" \
           -c "exit" -c "commit check" 2>&1); BADFRC=$?
printf '%s\n' "$BADF" | sed 's/^/  /'

echo
echo "=========== 7. a timeout under the floor its RFC sets ==========="
echo "accepted, because the operator may have a reason - and never silently."
FLOOR_SET=$(vppctl bpa timeouts tcp-established 600 2>&1)
printf '%s\n' "$FLOOR_SET" | sed 's/^/  /'
FLOOR_SHOW=$(vppctl show bpa timeouts)
printf '%s\n' "$FLOOR_SHOW" | sed -n '1,6p' | sed 's/^/  /'
echo "  the value was applied, not refused:"
vppctl show bpa timeouts | grep tcp-established | head -1 | sed 's/^/    /'
echo "  and back at the floor there is nothing to say:"
FLOOR_OK=$(vppctl bpa timeouts tcp-established 7440 2>&1)
printf '%s\n' "${FLOOR_OK:-  (silence)}" | sed 's/^/  /'
echo "  through the VTY, where the operator cannot see the dataplane log:"
VTY_WARN=$($HX -c "configure" -c "nat" -c "cgnat" -c "pool lab" -c "timeout udp 30" \
    -c "exit" -c "commit" 2>&1)
printf '%s\n' "$VTY_WARN" | sed 's/^/  /'
LOGGED=$(vppctl show logging 2>/dev/null | grep -c "below the")

echo
echo
echo "=========== 9. a pool with nowhere for packets to enter (#299) ==========="
# THE STATE A BOX IS IN ON THE DAY IT IS RACKED, and the one this lab has been
# in all along without saying so: a pool is configured and no interface is
# attached to CGNAT, so nothing can reach it. Measured on a real appliance
# whose dataplane had only local0 - it committed cleanly, the boot self-test
# gave it a green tick, and this command reported capacity for 504 subscribers
# it would never serve.
#
# Both directions are driven here rather than only the broken one. A caveat
# that appears on a correctly attached box is worse than none, because it is
# the one people learn to skip past - and then they skip the real one too.
NOATTACH=$(vppctl show bpa pool)
printf '%s\n' "$NOATTACH" | grep -E "capacity|nobody yet" | sed 's/^/  /'

vppctl set interface bpa in2out pg0 >/dev/null
vppctl set interface bpa out2in pg1 >/dev/null
BOTH=$(vppctl show bpa pool)
echo "  with both sides attached:"
printf '%s\n' "$BOTH" | grep -E "capacity|nobody yet" | sed 's/^/  /'

# And one side only, because the two are different mistakes with different
# fixes and a message that said "not attached" for both would send half the
# readers to the wrong interface.
vppctl set interface bpa out2in pg1 del >/dev/null
INONLY=$(vppctl show bpa pool)
echo "  with the outside detached again:"
printf '%s\n' "$INONLY" | grep -E "capacity|nobody yet" | sed 's/^/  /'
vppctl set interface bpa in2out pg0 del >/dev/null

echo "=========== VERDICT ==========="
expect_in "the dataplane has the pool" "cgnat pool lab" "$CFG"
expect_in "max-blocks reached the dataplane" "max-blocks 3" "$CFG"
expect_in "the filtering mode reached the dataplane" \
  "filter address-dependent" "$CFG"
expect_in "the UDP timeout reached the dataplane" "timeout udp 600" "$CFG"
expect_in "the GRE timeout reached the dataplane" "timeout gre 900" "$CFG"
expect_in "the ALG switch reached the dataplane" "alg ftp off" "$CFG"
# Read from a different command, so this is not the same string twice: show
# bpa alg asks the pool's own mask rather than re-printing the configuration.
expect_in "and 'show bpa alg' agrees with it" "ftp    off" "$ALG"
expect_in "show bpa pool reports the mode the VTY set" "dynamic" "$POOL"
expect "a vppctl setting survives an unrelated commit through the VTY" \
  test "${AFTER:-0}" -ge 1
expect "and the commit that had to survive it did take effect" \
  test "${ICMP:-0}" -ge 1
expect "loading a file with an unknown line fails" test "$BADRC" -ne 0
expect_in "and says which line, by number" "line 10" "$BAD"
expect_in "and what it did not understand" "wibble" "$BAD"

# #299: the capacity figure with the reason beside it, in both directions.
expect_said "a pool nothing is attached to says it serves nobody yet" \
  "nobody yet: no interface is attached to CGNAT" "$NOATTACH"
expect_said "and why, in both directions" \
  "no packet can reach this pool in either direction" "$NOATTACH"
expect_not_said "and once both sides are attached it says nothing" \
  "nobody yet" "$BOTH"
expect_in "while still reporting the capacity it always did" \
  "capacity" "$BOTH"
expect_said "with only the inside attached it names THAT half" \
  "no interface is attached on the outside" "$INONLY"
expect_not_said "and does not blame the inside, which is attached" \
  "attached on the inside" "$INONLY"

expect_in "a forward typed into the VTY reached the dataplane" \
  "203.0.113.17:1100 -> 10.0.0.5:80" "$FWD"
expect_in "a forward added to an existing pool reached it too" \
  "203.0.113.17:1101 -> 10.0.0.5:53" "$FWD"
expect_in "the same port for a subscriber on another public address reached it" \
  "203.0.113.16:1100 -> 10.0.0.4:8080" "$FWD"
expect_in "and both are in the running configuration" \
  " forward tcp 1100 to 10.0.0.5 80" "$CFG2"
expect_in "including the one that shares a public port" \
  " forward tcp 1100 to 10.0.0.4 8080" "$CFG2"
expect_in "adding one was recorded in the configuration trail" \
  "forward-add pool fwd" "$NEW"
# The one that matters: a pool carrying traffic must not be torn down to add
# an inbound port for one customer.
expect_not_in "and the pool was not deleted and recreated to do it" \
  "del pool fwd" "$NEW"
expect "a forward in another subscriber's block fails the check" \
  test "$BADFRC" -ne 0
expect_in "and the check names the subscriber it would have taken it from" \
  "10.0.0.61" "$BADF"

# Issue #54. Accepted, and never silently.
expect_in "a timeout under the floor names the RFC" "RFC 5382" "$FLOOR_SET"
expect_in "and the floor it is under" "7440" "$FLOOR_SET"
expect_in "and what breaks below it" "idle but live connection" "$FLOOR_SET"
expect_in "and says it was applied rather than refused" \
  "not a refusal" "$FLOOR_SET"
expect_in "show bpa timeouts marks it" "BELOW the floor" "$FLOOR_SHOW"
# A warning that always fires is one an operator learns to skip.
expect_not_in "a value at the floor says nothing" "below the" "$FLOOR_OK"
expect_in "the VTY relays the warning to whoever committed it" \
  "BELOW the floor" "$VTY_WARN"
expect "and the log has it for whoever reads it weeks later" \
  test "${LOGGED:-0}" -ge 1
verdict
