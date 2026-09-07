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
# "commit remap" and not "commit": changing a block size moves every
# subscriber that has a mapping, and since #23 that has to be asked for by
# name. This section is about rollback, not about the refusal - but it cannot
# skip past it, and a lab that quietly failed to apply its own change would
# leave the assertion below passing for the wrong reason.
$HX -c "configure" -c "cgnat pool alpha" -c "block-size 1024" -c "exit" \
    -c "commit remap comment \"wider blocks\"" 2>&1 | sed 's/^/  /'
vppctl show bpa pool | grep -E "block " | sed 's/^/    /'

echo
echo "=========== 5. revision history ==========="
$HX -c "show rollback" 2>&1 | sed 's/^/  /'

echo
echo "=========== 6. rollback 1 returns to the previous revision ==========="
# "commit remap" again, and for the same reason: undoing a change that moved
# every subscriber moves them all a second time. A rollback is not exempt from
# being a re-mapping - it is one - and an operator who typed the acknowledgement
# on the way in should type it on the way out.
ROLLBACK_OUT=$($HX -c "configure" -c "rollback 1" \
    -c "show configuration compare" -c "commit" 2>&1)
echo "$ROLLBACK_OUT" | tail -4 | sed 's/^/  /'
$HX -c "configure" -c "rollback 1" -c "commit remap" 2>&1 | sed 's/^/  /'
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
# Captured here, not read at verdict time: the sections after this one commit
# again, and an assertion about the revision history has to measure the moment
# it is about rather than whatever the lab did afterwards.
ROLLBACK_LIST=$($HX -c 'show rollback' 2>&1)

echo
echo "=========== VERDICT ==========="
R=$(vppctl show bpa pool)
expect_in "rollback took the device back to the earlier block size" "512 ports" "$R"
# A rollback that undoes a re-mapping is itself one, and is refused until it
# is asked for by name. Without this, "rollback" would be the way round the
# acknowledgement.
expect_in "a rollback that re-maps is refused too" \
  "re-maps subscribers" "$ROLLBACK_OUT"
expect_in "two revisions are on record" "1 commit(s) ago" "$ROLLBACK_LIST"

# --- MTU: committed, then rolled back to the value that was there before ---
# The hard half of the acceptance criteria. A rollback that can add a statement
# but not restore the previous value is not a rollback, and MTU is the case
# where that shows: the device holds one number and there is nothing to diff
# against unless the previous one was recorded.
BEFORE=$(vppctl show interface pg0 | awk 'NR==2 {split($4,a,"/"); print a[1]}')
$HX -c "configure" -c "interface pg0" -c "mtu 1400" -c "exit" -c "commit" \
    >/dev/null 2>&1 || true
AFTER=$(vppctl show interface pg0 | awk 'NR==2 {split($4,a,"/"); print a[1]}')
$HX -c "rollback 1" -c "commit" >/dev/null 2>&1 || true
BACK=$(vppctl show interface pg0 | awk 'NR==2 {split($4,a,"/"); print a[1]}')
echo "  pg0 mtu: $BEFORE -> $AFTER -> $BACK"
expect "the committed MTU reached the device" test "$AFTER" = "1400"
# Guarded: if the MTU never changed, "it went back" is true for the wrong
# reason. An assertion that holds when nothing happened is not an assertion.
expect "the MTU actually changed, so the rollback below means something" \
  test "$BEFORE" != "$AFTER"
expect "rollback restored the previous MTU" test "$BACK" = "$BEFORE"

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

# --- a change that re-maps existing subscribers must be asked for by name ---
#
# The sharpest edge in the design: in deterministic mode a subscriber's public
# address and port block are a function of the pool definition, so adding one
# public prefix moves every subscriber at that instant and ends every session
# on the pool. The box reports nothing unusual afterwards, because nothing
# unusual happened - it did what it was told. See docs/pool-resize.md.
vppctl bpa pool resize inside 100.64.64.0/24 outside 203.0.113.0/28 block 512 \
  >/dev/null
REMAP=$($HX -c "configure" -c "cgnat pool resize" -c "block-size 1024" \
            -c "exit" -c "commit" 2>&1)
echo "$REMAP" | tail -6 | sed 's/^/  /'
BLOCK_MID=$(vppctl show bpa pool | grep -A9 "pool resize" | grep -o "block *[0-9]*" | head -1)
NAMED=$($HX -c "configure" -c "cgnat pool resize" -c "block-size 1024" \
            -c "exit" -c "commit remap" 2>&1)
echo "$NAMED" | tail -2 | sed 's/^/  /'
BLOCK_AFTER=$(vppctl show bpa pool | grep -A9 "pool resize" | grep -o "block *[0-9]*" | head -1)
# And a change that moves nobody must not ask for anything.
QUIET=$($HX -c "configure" -c "cgnat pool resize" -c "alg rtsp off" \
            -c "exit" -c "commit" 2>&1)

expect_in "a re-mapping change is refused" "re-maps subscribers" "$REMAP"
expect_in "and says how many move" "subscribers change public address" "$REMAP"
expect_in "and shows one of them moving" "moves from" "$REMAP"
expect_in "and says what to type" "commit remap" "$REMAP"
expect "and nothing was applied" test "$BLOCK_MID" = "block        512"
expect_in "asked for by name, it applies" "commit complete" "$NAMED"
expect "and the dataplane took it" test "$BLOCK_AFTER" = "block        1024"
# A refusal that fires on every commit is one an operator routes around.
expect_not_in "a change that moves nobody is not questioned" \
  "re-maps subscribers" "$QUIET"
expect_in "and commits normally" "commit complete" "$QUIET"
verdict
