#!/bin/sh
# The show commands an operator arrives with, driven from the VTY.
#
# The oracle is ref/bpa_model.py, not the plugin. A test that asks the
# implementation what it should have said agrees with any mistake in it; the
# model is the executable specification and it does not share the plugin's
# arithmetic.
#
# Every command here is run through prismash rather than vppctl. An operator who
# has to drop to the dataplane CLI to troubleshoot is one for whom this VTY is
# decoration.
set -u
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
use_binary_api

HX="python3 /src/src/prismash/prismash.py"
SUB=100.64.0.5
vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }
vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl bpa pool nb inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

# ------------------------------------------------------------------
# THE SCREENS OF A BOX NOTHING HAS HAPPENED ON YET.
#
# Every one of these is a sentence an operator reads FIRST - on a box that
# was just configured, before any subscriber has sent a packet - and each was
# named by no lab, no suite, no wiki page and no document
# (test/test_plugin_cli_says.py's count). A screen nothing names is a screen
# whose wording can drift with nobody noticing, and these are the ones read
# at the worst moment: when somebody is deciding whether the box is working.
#
# vppctl and not prismash, for the three that are the dataplane's own
# screens: the VTY does not proxy them, and what is being held here is the
# wording the plugin prints. Everything else in this lab goes through
# prismash, for the reason in its header.
BARE_TOP=$(vppctl show bpa top 2>&1)
BARE_BLOCKS=$(vppctl show bpa blocks 2>&1)
BARE_LOGGING=$(vppctl show bpa logging 2>&1)
BARE_DRAIN=$(vppctl bpa stop-drain 2>&1)

expect_in "before any traffic, top says nobody holds a block" \
  "no subscriber holds a block right now" "$BARE_TOP"
# A deterministic pool with no reserve allocates nothing: every subscriber's
# block is the formula's answer and no allocator is involved. Saying so is
# the difference between "this box allocates nothing" and "this screen is
# broken".
# THE ROW WHOLE. The first version of this assertion carried the opening
# only, and test/test_plugin_cli_says.py went on counting the message as
# named by nothing - correctly: a slice holds the words it quotes and
# nothing about the rest of the sentence, which is where the reason lives.
BLOCKS_ROW="no pool on this box allocates blocks: no dynamic pool, and no deterministic pool with a reserve"
expect_in "and blocks says why there is nothing to show" \
  "$BLOCKS_ROW" "$BARE_BLOCKS"
expect_in "logging says nothing is sent over UDP, rather than nothing" \
  "exporter    none: nothing is sent over UDP" "$BARE_LOGGING"
# The legend belongs on the screen: an operator reading a collector's
# capture needs the numbers, and the box is where they are written down.
expect_in "and it names the event numbers a collector will see" \
  "natEvent    16 allocation, 17 de-allocation, 12 exhaustion" "$BARE_LOGGING"
# The drain is what vpp-stop-drain.conf runs on the way down (#217). With
# logging off there is nothing to write, and it says that instead of
# reporting a drain it did not do.
expect_in "and a drain with logging off says there is nothing to drain" \
  "logging is not enabled; nothing to drain" "$BARE_DRAIN"

# What the specification says this subscriber must get.
EXPECT=$(python3 - "$SUB" <<'PY'
import sys
sys.path.insert(0, "/src/ref")
from bpa_model import PoolConfig
p = PoolConfig("nb", ["100.64.0.0/22"], ["200.150.0.16/30"], block_size=512)
f = p.forward(sys.argv[1])
print(f["ip_pub"], f["port_lo"], f["port_hi"])
PY
)
E_IP=$(echo "$EXPECT" | cut -d' ' -f1)
E_LO=$(echo "$EXPECT" | cut -d' ' -f2)
E_HI=$(echo "$EXPECT" | cut -d' ' -f3)
echo "the model says $SUB -> $E_IP ports $E_LO-$E_HI"

send() {  # <source> <sport>
  vppctl packet-generator delete t >/dev/null 2>&1
  vppctl packet-generator new "name t limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: $1 -> 8.8.8.8 UDP: $2 -> 53 incrementing 8 }"
  vppctl packet-generator enable >/dev/null; sleep 0.3
  vppctl packet-generator delete t >/dev/null 2>&1
}

echo
echo "=========== 1. traffic from one subscriber, and one that has no pool ==========="
for pp in 40000 40001 40002; do send "$SUB" $pp; done
send 100.64.0.9 40000
send 192.0.2.7 40000          # outside every inside prefix: must be dropped

echo
echo "=========== 2. show cgnat subscriber ==========="
SUBOUT=$($HX -c "show cgnat subscriber $SUB" 2>&1)
printf '%s\n' "$SUBOUT" | sed 's/^/  /'

echo
echo "=========== 3. show cgnat translation, the inverse ==========="
TR=$($HX -c "show cgnat translation $E_IP $E_LO" 2>&1)
printf '%s\n' "$TR" | sed 's/^/  /'

echo
echo "=========== 3b. the same question against a DYNAMIC pool ==========="
# The two answers do not carry the same weight as evidence, and the command
# has to say which one it is giving. A dynamic block belonged to somebody else
# yesterday; only the RFC 8158 log says whom.
vppctl bpa pool dyn inside 10.0.0.0/24 outside 203.0.113.16/30 block 512 mode dynamic
vppctl set ip neighbor pg0 10.0.0.5 02:00:00:00:02:05 >/dev/null 2>&1
vppctl clear trace >/dev/null; vppctl trace add pg-input 3 >/dev/null
send 10.0.0.5 40000
DPUB=$(vc show trace | grep -m1 "BPA in2out 10.0.0.5" | grep -oE "> [0-9.]+:[0-9]+" | tail -1 | cut -d' ' -f2)
echo "  dynamic pool gave 10.0.0.5 -> ${DPUB:-nothing}"
DTR=$($HX -c "show cgnat translation ${DPUB%%:*} ${DPUB##*:}" 2>&1)
printf '%s\n' "$DTR" | sed 's/^/  /'

echo
echo "=========== 4. show cgnat top ==========="
TOP=$($HX -c "show cgnat top" 2>&1)
printf '%s\n' "$TOP" | sed 's/^/  /'

echo
echo "=========== 5. show cgnat drops ==========="
DROPS=$($HX -c "show cgnat drops" 2>&1)
printf '%s\n' "$DROPS" | sed 's/^/  /'
DROP_SECTION=$(printf '%s\n' "$DROPS" | sed -n '/packets that did not get through/,/everything else/p')

echo
# =========================================================================
# FOUR ANSWERS AN OPERATOR CAN GET AND NOTHING NAMED.
#
# test/test_plugin_cli_says.py counts a message as named only when a lab, a
# suite, a wiki page or a document carries it WHOLE - and it counted 28 that
# nothing did. A message nothing names is a branch nobody has checked the box
# still says, which is how the sentence an operator reads goes stale while
# every test stays green.
#
# These four are the ones this lab's own box can produce: it already has a
# deterministic pool with a capacity, a dynamic pool with free blocks, and no
# forwards at all. The other twenty-four need a box shaped differently and
# are somebody else's section, not a wider needle here.
echo
echo "=========== four answers, and the sentences they are ==========="
# BEYOND CAPACITY. `nb` has 4 public addresses of 126 blocks each, so it can
# serve 504 subscribers and the inside prefix holds 1024. The 505th is inside
# the prefix and outside the pool, which is a different answer from "not in
# any pool" and reads the same to anybody who does not know the arithmetic.
BEYOND=$($HX -c "show cgnat subscriber 100.64.1.250" 2>&1)
printf '%s\n' "$BEYOND" | sed 's/^/  /'
# A FREE DYNAMIC BLOCK. The dynamic pool's second public address has been
# allocated to nobody, so asking what holds it is the "nobody, right now"
# branch - the one that says the answer is about this instant only.
FREE=$($HX -c "show cgnat translation 203.0.113.18 20000" 2>&1)
printf '%s\n' "$FREE" | sed 's/^/  /'
# A PORT BELOW THE POOL'S RANGE. 500 is under the 1024 every pool here
# starts at, so the address is the pool's and the port belongs to no block.
NOBLOCK=$($HX -c "show cgnat translation 200.150.0.16 500" 2>&1)
printf '%s\n' "$NOBLOCK" | sed 's/^/  /'
# AND NO FORWARDS AT ALL, which is this box: nothing here configures one.
NOFWD=$($HX -c "show cgnat forward" 2>&1)
printf '%s\n' "$NOFWD" | sed 's/^/  /'

echo "=========== VERDICT ==========="
expect_in "the subscriber view names the public address the model predicts" \
  "$E_IP" "$SUBOUT"
expect_in "and the port range the model predicts" "$E_LO-$E_HI" "$SUBOUT"
expect_in "and the three sessions it actually opened" "sessions 3" "$SUBOUT"
expect_in "a subscriber in no pool is refused by name" \
  "not inside any configured pool" "$($HX -c 'show cgnat subscriber 192.0.2.7' 2>&1)"

expect_in "the translation inverts back to the subscriber" "$SUB" "$TR"
# The point of the command: the two sources are not equal as evidence.
expect_in "and says the answer comes from the formula" "FORMULA" "$TR"
expect_in "which holds for a past instant, and says so" "past or present" "$TR"
# The SENTENCE, not its two keywords. It is what an operator reads before
# attributing a past complaint to a subscriber, and a keyword survives a
# rewrite that changes what the sentence promises; test/test_plugin_cli_says.py
# counts a message as named only when something carries it whole (#366).
expect_in "in the sentence the operator reads, whole" \
  "from the FORMULA. It holds for any instant, past or present, as long as this pool's addresses and block size have not changed since. No allocation record is involved." "$TR"

# THE FOUR SENTENCES, whole. A keyword survives a rewrite that changes what
# the sentence promises, which is why this file already carries two of them
# in full (#366).
expect_in "an address inside the prefix and beyond the pool's capacity says so" \
  "BEYOND CAPACITY: the pool has no block for this address, so it is not translated at all" \
  "$BEYOND"
expect_in "a free dynamic block answers for this instant only" \
  "no subscriber holds this block at the moment" "$FREE"
expect_in "a port below the pool's range is not a block" \
  "the port is outside the pool's usable range, so nothing was ever translated here" \
  "$NOBLOCK"
expect_in "and a box with no forwards says that, rather than printing a heading" \
  "no forwards configured" "$NOFWD"

expect "the dynamic subscriber was translated at all" test -n "${DPUB:-}"
expect_in "a dynamic answer says it is dynamic" "DYNAMIC" "$DTR"
expect_in "and refuses to be read as evidence about the past" \
  "NOW ONLY" "$DTR"
expect_in "and says where the past answer is instead, whole" \
  "this answers for NOW ONLY. The block belonged to somebody else before and will again; for a past instant read the RFC 8158 log, which records when each block was assigned and released." "$DTR"
expect_not_in "so it must not claim the formula" "FORMULA" "$DTR"

expect_in "top lists the busiest subscriber" "$SUB" "$TOP"
expect_in "with the ports it holds" "3/512" "$TOP"
expect_in "and closes with how many hold a block: the three this lab arranged" \
  "3 subscriber(s) holding blocks" "$TOP"

# AND THE TWO SCREENS THAT ONLY EXIST ONCE TRAFFIC HAS BEEN THROUGH.
#
# `show bpa session` is the per-worker count an operator reads when they are
# asking whether the box is doing anything at all, and the reverse lookup of
# a port inside a deterministic pool names the block rather than a session.
# Both were named by nothing.
SESS=$(vppctl show bpa session 2>&1)
REV=$(vppctl show bpa translation "$E_IP" "$E_LO" 2>&1)
expect_in "the session screen counts subscribers per worker" \
  "active subscribers," "$SESS"
# DETERMINISTIC in the answer is the whole difference between "this port is
# whoever the formula says" and "this port was handed out": in the first, the
# answer is true whether or not a session exists, which is what a court order
# asks about a time nobody was watching.
expect_in "and a port of a deterministic pool answers with its block" \
  ", DETERMINISTIC" "$REV"

expect_in "drops names the packet with no pool" "no pool for source address" \
  "$DROP_SECTION"
# The reason this command exists rather than "show errors": VPP counts a
# success and a drop as the same kind of thing, and during an incident they
# are not.
expect_not_in "and does NOT count translated packets as drops" \
  "packets translated" "$DROP_SECTION"
expect_in "which are still reported, under their own heading" \
  "packets translated" "$DROPS"
expect_in "and the heading says what they are: counted, not dropped" \
  "everything else it counted" "$DROPS"
verdict
