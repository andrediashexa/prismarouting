#!/bin/sh
# Per-pool settings through the VTY, all the way to the dataplane.
#
# Four merged issues added settings that live on a pool - timeouts (#12),
# max-blocks (#13), the filtering mode (#15) and the ALG switches (#14) - and
# every one of them was unreachable through the VTY, which is the interface an
# operator actually uses. Their own labs pass because they drive vppctl.
#
# The oracle here is the DATAPLANE, never hexash. Asking hexash whether hexash
# stored something proves nothing: it stored it before, printed it back, and
# the device never heard about it.
set -u
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /etc/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
use_binary_api

HX="python3 /src/src/hexash/hexash.py"
vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }
vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up

echo "=========== 1. every per-pool setting, typed into the VTY ==========="
$HX -c "configure" \
    -c "cgnat pool lab" \
    -c "inside 100.64.0.0/22" \
    -c "outside 200.150.0.16/30" \
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
$HX -c "configure" -c "cgnat pool lab" -c "timeout icmp 90" -c "exit" \
    -c "commit" 2>&1 | sed 's/^/  /'
AFTER=$(vppctl show bpa config | grep -c "alg rtsp off")
ICMP=$(vppctl show bpa config | grep -c "timeout icmp 90")
echo "  'alg rtsp off' before the unrelated commit: $BEFORE, after: $AFTER"

echo
echo "=========== 4. a line the VTY cannot hold is refused, not trimmed ==========="
cat > /tmp/bad.conf <<'EOF'
! cgnat
cgnat pool lab
 inside 100.64.0.0/22
 outside 200.150.0.16/30
 block-size 512
 policy interleaved
 mode deterministic
 wibble 3
 exit
! routing
EOF
BAD=$($HX --load /tmp/bad.conf 2>&1); BADRC=$?
printf '%s\n' "$BAD" | sed 's/^/  /'

echo
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
expect_in "and says which line, by number" "line 8" "$BAD"
expect_in "and what it did not understand" "wibble" "$BAD"
verdict
