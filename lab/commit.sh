#!/bin/sh
# Juniper-style commit and rollback.
#
# Proves the full cycle: edit a candidate, review the diff, validate, commit,
# then roll back to the previous revision and confirm the device followed.
set -u
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /etc/hexarouting /var/run/frr /etc/frr
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
use_binary_api
export HEXA_ROLLBACK=/tmp/rollback HEXA_CONFIG=/tmp/hexarouting.conf
rm -rf /tmp/rollback

vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
printf 'hostname hexarouting\n' > /etc/frr/zebra.conf
printf 'hostname hexarouting\n' > /etc/frr/bgpd.conf
chown -R frr:frr /var/run/frr /etc/frr
pgrep zebra >/dev/null || /usr/lib/frr/zebra -d -A 127.0.0.1 -f /etc/frr/zebra.conf
pgrep bgpd  >/dev/null || /usr/lib/frr/bgpd  -d -A 127.0.0.1 -f /etc/frr/bgpd.conf
sleep 2

HX="python3 /src/src/hexash/hexash.py"

echo "=========== 1. candidate edits do NOT touch the device ==========="
$HX -c "configure" \
    -c "cgnat pool alpha" -c "inside 100.64.0.0/22" \
    -c "outside 198.19.24.0/24" -c "block-size 512" -c "exit" \
    -c "show configuration compare" 2>&1 | sed 's/^/  /'
echo "  -- device still has:"
vppctl show bpa pool | head -1 | sed 's/^/    /'

echo
echo "=========== 2. commit check validates without applying ==========="
$HX -c "configure" -c "cgnat pool bad" -c "inside 100.64.0.0/22" -c "exit" \
    -c "commit check" 2>&1 | sed 's/^/  /'

echo
echo "=========== 3. commit applies ==========="
$HX -c "configure" \
    -c "cgnat pool alpha" -c "inside 100.64.0.0/22" \
    -c "outside 198.19.24.0/24" -c "block-size 512" -c "exit" \
    -c "cgnat interface pg0 inside" \
    -c "commit comment \"first pool\"" 2>&1 | sed 's/^/  /'
echo "  -- device now has:"
vppctl show bpa pool | head -2 | sed 's/^/    /'

echo
echo "=========== 4. a second commit, changing the block size ==========="
$HX -c "configure" -c "cgnat pool alpha" -c "block-size 1024" -c "exit" \
    -c "commit comment \"wider blocks\"" 2>&1 | sed 's/^/  /'
vppctl show bpa pool | grep -E "block " | sed 's/^/    /'

echo
echo "=========== 5. revision history ==========="
$HX -c "show rollback" 2>&1 | sed 's/^/  /'

echo
echo "=========== 6. rollback 1 returns to the previous revision ==========="
$HX -c "configure" -c "rollback 1" -c "show configuration compare" -c "commit" 2>&1 | sed 's/^/  /'
echo "  -- device followed the rollback:"
vppctl show bpa pool | grep -E "block " | sed 's/^/    /'

echo
echo "=========== 7. a check MUST NOT mutate the device ==========="
# An earlier version deleted and recreated the running pool to work around a
# name check. It "worked", and put a production pool one failed command away
# from disappearing during a read-only operation.
TRACE=$(HEXA_TRACE=1 $HX -c "configure" -c "cgnat pool alpha" \
        -c "block-size 2048" -c "exit" -c "commit check" 2>&1 >/dev/null \
        | grep "^\[device\]")
echo "$TRACE" | sed 's/^/  /'
if echo "$TRACE" | grep -qE "\[device\] (bpa pool [^ ]+ del|set interface bpa|bpa timeouts|bpa block-holddown)"; then
  echo "  FAIL: a check issued a mutating command"
  exit 1
fi
if echo "$TRACE" | grep -qE "\[device\] bpa pool .* [^k]$"; then
  echo "  FAIL: a check issued a pool creation"
  exit 1
fi
echo "  OK: read-only commands only"

echo
echo "=========== 8. the check still catches what the device refuses ==========="
$HX -c "configure" -c "cgnat pool alpha" -c "block-size 500" -c "exit" \
    -c "commit check" 2>&1 | grep -vE "entering" | sed 's/^/  /'
echo "  -- device untouched:"
vppctl show bpa pool | grep -E "block " | sed 's/^/    /'

echo
echo "=========== VERDICT ==========="
R=$(vppctl show bpa pool)
expect_in "rollback took the device back to the earlier block size" "512 ports" "$R"
expect_in "two revisions are on record" "1 commit(s) ago" "$($HX -c 'show rollback' 2>&1)"

# --- a commit the dataplane refuses must FAIL ---
# This is the case that could not pass before the binary API: vppctl exits 0
# even when the command failed, so commit reported success while the dataplane
# held none of the configuration. The public prefix below collides with a local
# address, which the device refuses - and the candidate must survive it, or the
# operator loses the edit they were trying to correct.
BADC=$($HX -c "configure" -c "cgnat pool doomed" -c "inside 100.64.32.0/22" \
             -c "outside 10.0.0.0/30" -c "block-size 500" -c "exit" -c "commit" 2>&1)
echo "$BADC" | tail -3 | sed 's/^/  /'
expect_in "a refused commit says so" "error" "$BADC"
expect_in "and names the pool" "doomed" "$BADC"
expect_not_in "and does not claim success" "commit complete" "$BADC"
expect_not_in "and the pool is not in the dataplane" "pool doomed" "$(vppctl show bpa pool)"
verdict
