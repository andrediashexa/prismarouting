#!/bin/sh
# Juniper-style commit and rollback.
#
# Proves the full cycle: edit a candidate, review the diff, validate, commit,
# then roll back to the previous revision and confirm the device followed.
set -u
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing /var/run/frr /etc/frr
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
use_binary_api
export PRISMA_ROLLBACK=/tmp/rollback PRISMA_CONFIG=/tmp/prisma-routing.conf
rm -rf /tmp/rollback

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
printf 'hostname prisma-routing\n' > /etc/frr/bgpd.conf
chown -R frr:frr /var/run/frr /etc/frr
pgrep zebra >/dev/null || start_frr bgpd
sleep 2

HX="python3 /src/src/prismash/prismash.py"

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
TRACE=$(PRISMA_TRACE=1 $HX -c "configure" -c "cgnat pool alpha" \
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
# A SECOND overlapping public prefix, and not a block size that is not a power
# of two, which is what this said. The VTY refuses that at the prompt now, so
# the device was never asked and this section demonstrated the wrong half of
# the product: the whole point here is that `commit check` carries a refusal
# back from the DEVICE without touching it, and a statement the VTY stops at
# the line never reaches the device to be refused by it.
#
# Overlapping prefixes in one address set is a refusal the VTY does not make
# and the device does: bpa_addr_set_* rejects the set before the pool is
# built. Chosen for that reason and checked for it - a change the VTY caught
# first would put this section back where it was.
$HX -c "configure" -c "cgnat pool alpha" -c "outside 198.19.24.0/25" \
    -c "exit" -c "commit check" 2>&1 | grep -vE "entering" | sed 's/^/  /'
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
# held none of the configuration. The candidate must survive that refusal, or
# the operator loses the edit they were trying to correct.
#
# WHAT MAKES THE DEVICE REFUSE, and it is worth saying because this case spent
# its whole life saying something else. The comment here read "the public
# prefix below collides with a local address, which the device refuses". No
# interface in this lab carries 10.0.0.0/30 and none ever did, so that was not
# the reason and never had been: what the device was refusing was
# `block-size 500`, which is not a power of two - bpa_pool.c's own second
# clause. The case was right about the product and wrong about itself, and it
# passed for four months on a refusal its comment did not name.
#
# It surfaced the moment the VTY started refusing that block size at the line
# rather than leaving it to the device, because then the pool committed
# cleanly and all three assertions below went red at once. A lab that goes red
# when a defect is FIXED is a lab that was measuring the defect.
#
# So the pool is one the VTY passes through and the device refuses: two
# overlapping prefixes in one address set. `bpa_addr_set_*` rejects the set
# before the pool is built, and the VTY has no check of its own for it -
# verified by driving the four statements through a Shell and seeing nothing
# refused at the prompt.
BADC=$($HX -c "configure" -c "cgnat pool doomed" -c "inside 100.64.32.0/22" \
             -c "outside 10.0.0.0/30" -c "outside 10.0.0.0/31" \
             -c "exit" -c "commit" 2>&1)
echo "$BADC" | tail -3 | sed 's/^/  /'
expect_in "a refused commit says so" "error" "$BADC"
expect_in "and names the pool" "doomed" "$BADC"
# THE CAUSE, and not only that something was refused. `error` is green for any
# refusal at all, and `doomed` is green for any refusal about this pool - which
# is how this case stayed green for four months while the device was refusing
# something the comment above did not mention. A case about a device refusal
# has to name the refusal, or the next change that moves the cause leaves it
# passing about the wrong thing again.
expect_in "and says WHICH refusal, so a cause that moves is seen" \
  "overlapping" "$BADC"
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

# --- and a reserve is one of those changes, reached the same way ------------
#
# A reserve is taken OUT of capacity, and capacity is the mapping, so enabling
# one has to go through this same refusal. Nothing here is special-cased for
# it: the VTY classifies `reserve` as a mapping field, which puts the pool in
# pool_replace, which is what makes the dataplane compute the remap. This
# section is the measurement that the classification actually lands there
# (issue #39).
#
# The BLOCKED policy on purpose. Under interleaved the divisor is the public
# address count, which a reserve does not touch, so a pool whose inside set is
# smaller than its reduced capacity moves nobody at all - a true and useless
# demonstration. Under blocked the divisor IS the mapped block count, so it
# re-divides and the number is large.
vppctl bpa pool reserved inside 100.64.65.0/24 outside 203.0.113.16/28 \
  block 1024 policy blocked >/dev/null
RES_REMAP=$($HX -c "configure" -c "cgnat pool reserved" -c "reserve 1" \
                -c "exit" -c "commit" 2>&1)
echo "$RES_REMAP" | tail -5 | sed 's/^/  /'
RES_CAP_MID=$(vppctl show bpa pool | grep -A14 "pool reserved" \
              | sed -n 's/^ *capacity *\([0-9]*\) subscribers.*/\1/p' | head -1)
RES_NAMED=$($HX -c "configure" -c "cgnat pool reserved" -c "reserve 1" \
                -c "exit" -c "commit remap" 2>&1)
RES_CAP_AFTER=$(vppctl show bpa pool | grep -A14 "pool reserved" \
                | sed -n 's/^ *capacity *\([0-9]*\) subscribers.*/\1/p' | head -1)
RES_MOVED=$(printf '%s\n' "$RES_REMAP" | grep -o "[0-9]* of [0-9]* subscribers" | head -1)
# The oracle, again from the specification rather than from the box.
RES_MODEL=$(python3 - <<'PY'
import ipaddress, sys
sys.path.insert(0, "/src/ref")
from bpa_model import PoolConfig, MapPolicy
args = dict(block_size=1024, policy=MapPolicy.BLOCKED)
a = PoolConfig("reserved", ["100.64.65.0/24"], ["203.0.113.16/28"], **args)
b = PoolConfig("reserved", ["100.64.65.0/24"], ["203.0.113.16/28"],
               block_reserve=1, **args)
base = int(ipaddress.IPv4Address("100.64.65.0"))
def key(p, v):
    try:
        f = p.forward(v)
    except Exception:
        return None
    return f["ip_pub"], f["port_lo"]
n = min(a.capacity, a.inside_count)
moved = sum(1 for i in range(n) if key(a, base + i) != key(b, base + i))
print(f"{moved} of {n} subscribers|{b.capacity}|{a.capacity}")
PY
)
RES_MODEL_MOVED=${RES_MODEL%%|*}
RES_MODEL_REST=${RES_MODEL#*|}
RES_MODEL_CAP=${RES_MODEL_REST%%|*}
RES_MODEL_CAP0=${RES_MODEL_REST##*|}
echo "  the box says: ${RES_MOVED:-<nothing>}"
echo "  the model says: ${RES_MODEL_MOVED}, capacity ${RES_MODEL_CAP0} -> ${RES_MODEL_CAP}"

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
# --- the reserve, through the same gate ---
expect_in "enabling a reserve is refused as a re-mapping change" \
  "re-maps subscribers" "$RES_REMAP"
expect_in "and says what to type" "commit remap" "$RES_REMAP"
# The count from the specification, not from the box agreeing with itself.
# Without this, "says how many move" passes on any number at all - and the
# whole point of a reserve under the blocked policy is that the number is
# large, because the divisor changed.
expect "and the count is the one the model computes" \
  test "${RES_MOVED:-x}" = "${RES_MODEL_MOVED:-y}"
# Still the capacity of a pool with NO reserve, which is the number a refused
# commit has to leave behind. Against the model's, not against a literal.
expect "and nothing was applied" \
  test "${RES_CAP_MID:-x}" = "${RES_MODEL_CAP0:-y}"
expect_in "asked for by name, it applies" "commit complete" "$RES_NAMED"
# The capacity the box reports afterwards IS the capacity the model predicts.
# A box that carved out a reserve and went on reporting the old capacity would
# promise an operator subscribers it is about to drop.
expect "and the capacity the box reports is the one the model predicts" \
  test "${RES_CAP_AFTER:-x}" = "${RES_MODEL_CAP:-y}"
# --- what the DATAPLANE says before a pool is replaced (#390) ---
#
# Everything above reads the remap through prismash, and prismash does not
# relay those lines: it takes the `remap:` prefix off and re-wraps them at 79
# columns, so no sentence of the dataplane's reaches the VTY whole. But
# `bpa pool ... check` is also typed at the dataplane's own console, by an
# operator the VTY has not reached yet, and there the lines are what the
# plugin prints. Three of them were held by nothing: the pool being valid, a
# subscriber the new definition does not serve at all, and the closing
# sentence that says the sessions go and the past answers do not.
#
# The change shrinks the inside set from its LOW end on purpose.
# bpa_pool_remap_count() keeps the first addresses that move as its
# examples, in order, so a pool that loses 100.64.66.0-127 names .0 and .1
# as the examples - and each of those is served by no block at all, which is
# the branch nothing else in this lab reaches. A shrink from the top would
# leave the examples on the "moves from" branch the resize above holds.
vppctl bpa pool shrunk inside 100.64.66.0/24 outside 203.0.113.32/28 \
  block 1024 >/dev/null
SHRINK=$(vppctl bpa pool shrunk inside 100.64.66.128/25 \
           outside 203.0.113.32/28 block 1024 check 2>&1 | tr -d '\r')
echo
echo "=========== the dataplane's own check, on a pool that shrinks ==========="
printf '%s\n' "$SHRINK" | sed 's/^/  /'
SHRINK_AFTER=$(vppctl show bpa pool shrunk 2>&1 | tr -d '\r')
# Where .0 is today, from the specification: the example line quotes the
# block the subscriber is losing, and "no block at all" is only half the
# claim if the block it names is not the one the subscriber holds.
SHRINK_WAS=$(python3 - <<'PY'
import sys
sys.path.insert(0, "/src/ref")
from bpa_model import PoolConfig
p = PoolConfig("shrunk", ["100.64.66.0/24"], ["203.0.113.32/28"],
               block_size=1024)
f = p.forward("100.64.66.0")
print("100.64.66.0 was %s:%d-%d" % (f["ip_pub"], f["port_lo"], f["port_hi"]))
PY
)
echo "  the model says: $SHRINK_WAS"
expect_in "the dataplane's check says the replacement pool is valid" \
  "pool 'shrunk' is valid" "$SHRINK"
expect_in "and names the first subscriber the new definition drops" \
  "$SHRINK_WAS, now served by no block at all" "$SHRINK"
expect_in "and closes with what applying it costs, and what it does not" \
  "remap: every session on this pool ends when it is applied. Past answers stay resolvable - the configuration trail records which definition was in force - but the subscribers do not." \
  "$SHRINK"
# The control. `check` is the half that must not act: a pool that shrank
# while being asked about it is the outage the sentence above describes,
# delivered by the command that exists to prevent it.
#
# Watched red against a pool that really shrank (deleted and made again as
# the /25), and NOT against the same command with `check` taken off: at this
# console a name that exists is refused with `pool already exists` - the
# replace is the API's - so dropping `check` changes nothing and this line
# stays green, correctly. A mutation that cannot make the pool change is not
# evidence that this line would see it.
expect_in "and the pool it was asked about still serves the whole /24" \
  "100.64.66.0+256" "$SHRINK_AFTER"
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
# And what the table IS, on the dataplane's own screen (#390): the size, and
# the sentence under it that says what the size means. "memory N MB per
# table" is read as a cost by whoever sizes a box, and it is a virtual
# reservation that the OOM killer counts as if it were resident - the one
# thing that sentence is there to say, and nothing held it. The refusal
# above is only a refusal if the size did not change, so that is asserted
# on the same screen.
SESS_SHOW=$(vppctl bpa session-table 2>&1 | tr -d '\r')
printf '%s\n' "$SESS_SHOW" | sed 's/^/  /'
expect_in "the session table's screen says what its size means" \
  "(two tables per thread; the size is a virtual reservation, and the OOM killer counts virtual)" \
  "$SESS_SHOW"
expect_in "under the size it has" " MB per table" "$SESS_SHOW"
expect_not_in "which is not the size the refused commit asked for" \
  "buckets 1024, memory 64 MB" "$SESS_SHOW"

verdict
