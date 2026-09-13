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
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
use_binary_api
export HEXA_ROLLBACK=/tmp/rollback HEXA_CONFIG=/tmp/hexarouting.conf
rm -rf /tmp/rollback

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
printf 'hostname hexarouting\n' > /etc/frr/zebra.conf
printf 'hostname hexarouting\n' > /etc/frr/bgpd.conf
chown -R frr:frr /var/run/frr /etc/frr
pgrep zebra >/dev/null || start_frr bgpd
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
# Everything a check may do, listed; anything else is a write.
#
# This used to name the mutating commands instead - "bpa timeouts",
# "set interface bpa" - and it went blind the moment one of them moved to the
# binary API, because the trace line stopped starting with [device]. A test
# that names what is forbidden stops testing when the forbidden thing changes
# shape. Naming what is ALLOWED does not.
BAD=$(echo "$TRACE" \
      | grep -vE "^\[device\] show " \
      | grep -vE "^\[api\] [a-z0-9_]+_dump " \
      | grep -vE "^\[api\] sw_interface_get_table " \
      | grep -vE "^\[device\] bpa pool .* check$" \
      | grep -vE "^\[device\] bpa forward .* check$" \
      | grep -v "^$")
if [ -n "$BAD" ]; then
  echo "  FAIL: a check did something that is not a read:"
  echo "$BAD" | sed 's/^/    /'
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
# `commit check` FIRST, on the same candidate, because that is the order an
# operator works in and it is the whole of #278: the help promises check will
# report the re-mapping, and it printed only that validation passed. Whatever
# check says here has to be what commit says below - two answers to "how many
# move" is worse than one, and the numbers come from the same dataplane call.
CHECK=$($HX -c "configure" -c "cgnat pool resize" -c "block-size 1024" \
             -c "exit" -c "commit check" 2>&1)
echo "$CHECK" | tail -6 | sed 's/^/  /'
BLOCK_CHECK=$(vppctl show bpa pool | grep -A9 "pool resize" | grep -o "block *[0-9]*" | head -1)
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

# --- the arithmetic, against the specification rather than against itself ---
MOVED=$(printf '%s\n' "$REMAP" | grep -o "[0-9]* of [0-9]* subscribers" | head -1)
MODEL_MOVED=$(python3 - <<'PY'
import ipaddress, sys
sys.path.insert(0, "/src/ref")
from bpa_model import PoolConfig
a = PoolConfig("resize", ["100.64.64.0/24"], ["203.0.113.0/28"], block_size=512)
b = PoolConfig("resize", ["100.64.64.0/24"], ["203.0.113.0/28"], block_size=1024)
base = int(ipaddress.IPv4Address("100.64.64.0"))
def key(p, v):
    f = p.forward(v)
    return f["ip_pub"], f["port_lo"]
n = sum(1 for i in range(a.inside_count)
        if key(a, base + i) != key(b, base + i))
print("%d of %d subscribers" % (n, a.inside_count))
PY
)
# Whichever subscriber the box chose to show, the model is asked whether that
# claim is true - so the check survives the box picking a different example.
EX=$(printf '%s\n' "$REMAP" |
     grep -oE "[0-9.]+ moves from [0-9.]+:[0-9]+-[0-9]+ to [0-9.]+:[0-9]+-[0-9]+" |
     head -1)
EX_OK=$(python3 - "$EX" <<'PY'
import ipaddress, re, sys
sys.path.insert(0, "/src/ref")
from bpa_model import PoolConfig
m = re.match(r"(\S+) moves from (\S+):(\d+)-(\d+) to (\S+):(\d+)-(\d+)$",
             sys.argv[1] if len(sys.argv) > 1 else "")
if not m:
    print("no: the box printed no example to check"); raise SystemExit
sub, oip, olo, ohi, nip, nlo, nhi = m.groups()
a = PoolConfig("resize", ["100.64.64.0/24"], ["203.0.113.0/28"], block_size=512)
b = PoolConfig("resize", ["100.64.64.0/24"], ["203.0.113.0/28"], block_size=1024)
want = []
for p in (a, b):
    f = p.forward(sub)
    want.append((f["ip_pub"], str(f["port_lo"]), str(f["port_hi"])))
got = [(oip, olo, ohi), (nip, nlo, nhi)]
print("yes" if want == got else "no: model says %s, box says %s" % (want, got))
PY
)
echo "  model: $MODEL_MOVED / box: $MOVED"
echo "  example checks out: $EX_OK"

expect_in "a re-mapping change is refused" "re-maps subscribers" "$REMAP"
expect_in "and says how many move" "subscribers change public address" "$REMAP"
expect_in "and shows one of them moving" "moves from" "$REMAP"
# The number and the example, checked against ref/bpa_model.py rather than
# against themselves. "says how many move" passes on any number at all; the
# oracle is what makes it 240 and not 239, and it computes the mapping from
# the specification instead of asking the implementation what it did.
expect "and the count is the one the model computes" \
  test "$MOVED" = "$MODEL_MOVED"
expect "and the subscriber it names moves exactly where the model says" \
  test "$EX_OK" = yes
expect_in "and says what to type" "commit remap" "$REMAP"

# #278. The operator asks before applying, and until this the answer was
# "configuration check succeeds" and nothing else - over a change that moves
# every subscriber on the pool.
expect_in "'commit check' says the change re-maps subscribers" \
  "RE-MAPS" "$CHECK"
expect_in "and gives the same count 'commit' gives" "$MOVED" "$CHECK"
expect_in "and names a subscriber moving, as the refusal does" \
  "moves from" "$CHECK"
expect_in "and says what to type" "commit remap" "$CHECK"
# It must not read as a refusal: check applied nothing, so there is nothing to
# have refused, and an operator who reads it as a failure runs it again.
expect "and does not report it as an error" \
  test "$(printf '%s' "$CHECK" | grep -c '^error:')" = 0
# The half a lab can prove and a unit test cannot: that check really did not
# touch the box. The block size has to be what it was before any of this.
expect "and the box was not touched" test "$BLOCK_CHECK" = "block        512"
expect "and nothing was applied" test "$BLOCK_MID" = "block        512"
expect_in "asked for by name, it applies" "commit complete" "$NAMED"
expect "and the dataplane took it" test "$BLOCK_AFTER" = "block        1024"
# A refusal that fires on every commit is one an operator routes around.
expect_not_in "a change that moves nobody is not questioned" \
  "re-maps subscribers" "$QUIET"
expect_in "and commits normally" "commit complete" "$QUIET"
# --- the global settings, over the binary API (#76) ---
#
# Timeouts, the block hold-down and the session table sizing used to reach the
# dataplane through `vppctl`, which meant every account that could change a
# timeout needed the socket - and an account with that socket can delete a
# pool and disconnect everybody behind it.
#
# Asserted by reading the value back from the dataplane, not by the commit
# saying "complete": a message the dataplane accepted and did nothing with
# would report success either way.
echo
echo "=========== global settings over the API ==========="
$HX -c "configure" -c "cgnat timeout udp 137" \
    -c "cgnat block-holddown 11" -c "commit" 2>&1 | tail -2 | sed 's/^/  /'
TIMEOUTS=$(vppctl show bpa timeouts)
echo "  --- what the dataplane reports ---"
printf '%s\n' "$TIMEOUTS" | sed 's/^/    /'
# The value from the GLOBAL section, read as a field.
#
# `show bpa timeouts` prints two sections - the global one and the resolved
# overrides for each pool - so every timeout name appears more than once. The
# earlier attempt compared against every match at once and failed on a box
# where all of them were 137. Stopping at the first is the fix, and printing
# the table above is what made it visible.
# tr -d '\r' is not decoration: vppctl ends its lines with CR+LF, so the value
# extracted here is "137\r". The terminal shows it as "137" and `test` compares
# it as different, which is a failure with no visible cause - the assertion
# even printed "(udp 137)" in its own description while failing.
UDP=$(vppctl show bpa timeouts | tr -d '\r' | awk '$1=="udp"{print $2; exit}')
expect "the timeout reached the dataplane (udp ${UDP:-none})" \
  test "${UDP:-0}" = "137"
expect_in "and so did the block hold-down" "block-holddown 11" \
  "$(vppctl bpa block-holddown)"
expect_in "and both are in the running configuration" "cgnat timeout udp 137" \
  "$($HX -c 'show running-config')"

# The refusal has to survive the change of transport. The tables are allocated
# with the first pool, and a new size afterwards would describe a table nobody
# is using - so the dataplane refuses, and this lab has pools.
SESS=$($HX -c "configure" -c "cgnat session-table buckets 1024 memory 64" \
       -c "commit" 2>&1)
echo "$SESS" | tail -2 | sed 's/^/  /'
expect_in "resizing the session table with pools configured is refused" \
  "session-table" "$SESS"

verdict
