#!/bin/sh
# show log: the box's record of events, with each restart's cause (#588).
#
#   sh tools/lab.sh show-log        commits, a rollback, an expiry, two
#                                   restarts and why, read back with show log
#   sh tools/lab.sh show-log off    the record cannot be kept - every check on
#                                   what show log says MUST fail
#
# Through prismash against a running VPP and FRR, because what the issue asks
# is about the box: a commit an operator made, a `commit confirmed` the box
# put back by itself, a `system dataplane restart` and the start it caused, a
# dataplane that died and came back with nothing asking for it - each read
# back from `show log` with its cause, and from `show system uptime`.
#
# THE RESTART IS THE BOX'S, ITS TOOL A STAND-IN. `system dataplane restart`
# runs prisma-hot-card on a box, which needs systemd and the containers; the
# lab's PRISMA_HOT_CARD_TOOL does to the processes what that tool does to the
# stack - the dataplane and the routing daemons down, both started again, and
# the boot's apply (`prismash --load`) run over them, which is where the start
# is recorded. The unexpected one is a VPP killed and started again with
# nothing asking - Docker's restart - and `prismash --reapply`, what
# watch-the-dataplane runs then. The routing daemons' start is read off
# zebra's pid file (PRISMA_FRR_STARTED), as a box reads watchfrr's: there is
# no watchfrr in a lab.
#
# The off run points the record at a path nothing can write - the box with
# nowhere to keep it. The VTY and every action run as before; the controls
# (the dataplane restarted, holds the pool, FRR's `show logging` answers) stay
# green.
#
# OFF-RED-AT-LEAST: 19
#
# A FLOOR, measured on the off run through tools/run-all-labs.sh (#588,
# 2026-10-06): 19 of its 24 checks red. Green in that run and rightly: the
# three controls, and `since` a date after everything and a refused `since`,
# which say "nothing" and refuse whether or not anything was kept. See
# lab/bypass.sh for why it is a floor and not a target.
set -u
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api, wait_vpp
use_binary_api
export PRISMA_ROLLBACK=/tmp/rollback PRISMA_CONFIG=/tmp/prisma-routing.conf
export PRISMA_CONFIRM=/tmp/confirm-pending
export PRISMA_FRR_STARTED=/var/run/frr/zebra.pid
export PRISMA_EVENTS=/var/log/prisma-routing/events.log
export PRISMA_EVENTS_STATE=/var/log/prisma-routing/events.state
# No doors daemon here: every write is local, as on a box whose daemon is
# not listening. A stray socket from the image would be a door to nowhere.
export PRISMA_DOORS=/tmp/no-doors.sock
rm -rf /tmp/rollback /tmp/confirm-pending "$PRISMA_EVENTS" "$PRISMA_EVENTS.1" \
       "$PRISMA_EVENTS_STATE"
if [ "$MODE" = off ]; then
  echo "  MODE=off: the record of events points at a path nothing can write."
  export PRISMA_EVENTS=/proc/no-such-dir/events.log
  export PRISMA_EVENTS_STATE=/proc/no-such-dir/events.state
fi

mkdir -p /var/run/frr /etc/frr
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr 2>/dev/null
start_frr

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp

# The lab's prisma-hot-card: the stack down and up, then the boot's apply.
# VPP by its command line, anchored: `pgrep -x vpp` found nothing in the
# lab's container, and the first runs of this lab restarted no dataplane at
# all - the kill missed, and the second VPP found the first one's socket.
# `[-]c` so the pattern is not itself read as a VPP start: test/test_lab_
# workers.sh holds every `vpp -c` to a wait_vpp after it.
cat > /tmp/hot-card <<'EOF'
#!/bin/sh
[ "${1:-}" = restart-dataplane ] || exit 2
exec > /tmp/hot-card.log 2>&1
. /src/lab/lib.sh
for p in $(pgrep -f '^vpp [-]c'); do kill "$p"; done
for d in zebra mgmtd staticd; do
  for p in $(pgrep -x "$d"); do kill "$p"; done
done
n=0
while pgrep -f '^vpp [-]c' >/dev/null && [ "$n" -lt 20 ]; do sleep 1; n=$((n + 1)); done
sleep 1
setsid vpp -c "$(startup_conf test)" > /tmp/vpp-2.log 2>&1 < /dev/null &
start_frr < /dev/null
wait_vpp /tmp/vpp-2.log
python3 /src/src/prismash/prismash.py --load "$PRISMA_CONFIG"
echo "the lab's stack is back: --load exited $?"
EOF
chmod 0755 /tmp/hot-card
export PRISMA_HOT_CARD_TOOL=/tmp/hot-card

HX="python3 /src/src/prismash/prismash.py"

echo "=========== 1. a commit, and the host's first reading ==========="
C1=$($HX -c "configure" -c "nat" -c "cgnat" -c "pool alpha" \
    -c "prefix-inside 100.64.0.0/22" -c "prefix-outside 198.19.24.0/24" \
    -c "block-size 512" -c "exit" -c "exit" -c "exit" \
    -c 'commit comment "first pool"' 2>&1)
# Saved, as a box's boot and restart load the saved file.
$HX -c "save" > /dev/null 2>&1
echo "$C1" | tail -3 | sed 's/^/  /'
# The first reading on a box whose record is new: the boot is recorded, and
# a start long after it only as seen - its cause predates the record.
$HX --reapply > /tmp/reapply-0.log 2>&1
tail -2 /tmp/reapply-0.log | sed 's/^/  /'

echo
echo "=========== 2. a second commit, and a rollback of it ==========="
C2=$($HX -c "configure" -c "nat" -c "cgnat" -c "pool beta" \
    -c "prefix-inside 100.64.8.0/22" -c "prefix-outside 198.19.25.0/24" \
    -c "block-size 512" -c "exit" -c "exit" -c "exit" -c "commit" 2>&1)
echo "$C2" | tail -1 | sed 's/^/  /'
RB=$($HX -c "configure" -c "rollback 1" -c "commit" 2>&1)
echo "$RB" | tail -2 | sed 's/^/  /'

echo
echo "=========== 3. a commit confirmed nobody confirms ==========="
CC=$($HX -c "configure" -c "nat" -c "cgnat" -c "pool gamma" \
    -c "prefix-inside 100.64.16.0/22" -c "prefix-outside 198.19.26.0/24" \
    -c "block-size 512" -c "exit" -c "exit" -c "exit" \
    -c "commit confirmed 1" 2>&1)
echo "$CC" | tail -2 | sed 's/^/  /'
# The minute, gone by: the deadline moved back as waiting would move it.
python3 - <<'EOF'
import sys
sys.path.insert(0, "/src/src/prismash")
import prismash
c = prismash.ConfirmPending(prismash.CONFIRM_PATH)
p = c.read()
c.arm(p["deadline"] - 61, p["config"], p["minutes"], p["attempts"])
EOF
EXP=$($HX --confirm-expired 2>&1)
echo "$EXP" | head -2 | sed 's/^/  /'
# The timer that finds nothing to do, three times: noise, which the record
# must not get (the journal's "skipped because of an unmet condition").
# Counted as events, rows that start with their time: "no event recorded"
# is a line too, and a long event folds onto a second one.
N_BEFORE=$($HX -c "show log last 1000" 2>/dev/null | grep -c '^[0-9][0-9][0-9][0-9]-')
for _ in 1 2 3; do $HX --confirm-expired > /dev/null 2>&1; done
N_AFTER=$($HX -c "show log last 1000" 2>/dev/null | grep -c '^[0-9][0-9][0-9][0-9]-')
echo "  events before and after three idle timer runs: $N_BEFORE $N_AFTER"

echo
echo "=========== 4. system dataplane restart, and the start it caused ==========="
DPR=$($HX -c "system dataplane restart" 2>&1)
echo "$DPR" | sed 's/^/  /'
tail -4 /tmp/hot-card.log 2>/dev/null | sed 's/^/  the tool: /'
POOL_BACK=$(vppctl show bpa pool 2>&1 | head -3)
echo "$POOL_BACK" | sed 's/^/  /'

echo
echo "=========== 5. the dataplane dies, and comes back with nothing asking ==========="
sleep 5    # past the slack two readings of one start may differ by (3 s)
echo "  before: vpp $(pgrep -f '^vpp [-]c' | tr '\n' ' ')- $(vppctl show clock 2>&1 | head -1)"
for p in $(pgrep -f '^vpp [-]c'); do kill -9 "$p"; done
n=0
while pgrep -f '^vpp [-]c' >/dev/null && [ "$n" -lt 20 ]; do sleep 1; n=$((n + 1)); done
setsid vpp -c "$(startup_conf test)" > /tmp/vpp-3.log 2>&1 < /dev/null &
wait_vpp /tmp/vpp-3.log
echo "  after:  vpp $(pgrep -f '^vpp [-]c' | tr '\n' ' ')- $(vppctl show clock 2>&1 | head -1)"
$HX --reapply > /tmp/reapply-3.log 2>&1
tail -2 /tmp/reapply-3.log | sed 's/^/  /'

echo
echo "=========== 6. show log, and its filters ==========="
ALL=$($HX -c "show log" 2>&1)
echo "$ALL" | sed 's/^/  /'
echo "  -- source dataplane"
SRC_DP=$($HX -c "show log source dataplane" 2>&1)
echo "$SRC_DP" | sed 's/^/    /'
echo "  -- severity warning"
SEV=$($HX -c "show log severity warning" 2>&1)
echo "$SEV" | sed 's/^/    /'
echo "  -- last 2"
LAST2=$($HX -c "show log last 2" 2>&1)
echo "$LAST2" | sed 's/^/    /'
echo "  -- since 1h | match rollback"
MATCH=$($HX -c "show log since 1h | match rollback" 2>&1)
echo "$MATCH" | sed 's/^/    /'
echo "  -- since 2099-01-01"
FUTURE=$($HX -c "show log since 2099-01-01" 2>&1)
echo "$FUTURE" | sed 's/^/    /'
echo "  -- since tomorrow (refused)"
BAD=$($HX -c "show log since tomorrow" 2>&1)
echo "$BAD" | sed 's/^/    /'
echo "  -- show system uptime"
UP=$($HX -c "show system uptime" 2>&1)
echo "$UP" | sed 's/^/    /'
echo "  -- show logging (FRR's, unchanged)"
FRRLOG=$($HX -c "show logging" 2>&1)
echo "$FRRLOG" | head -4 | sed 's/^/    /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
# The controls: what the off run keeps green.
expect "control: the dataplane came back from both restarts" \
  sh -c "vppctl show version >/dev/null 2>&1"
expect_in "control: the restart's own apply put the pool back" \
  "alpha" "$POOL_BACK"
expect_in "control: FRR's show logging still answers, as FRR's" \
  "logging" "$FRRLOG"
# What show log says. Each event's sentence is read with its folds undone
# (expect_said): an event wider than the console folds, its continuation
# indented, as every row the VTY prints does.
expect_said "the first commit, with its comment" \
  "configuration committed: first pool" "$ALL"
expect_said "and who made it" "(by root" "$ALL"
expect_said "the rollback, naming its revision" \
  "rollback to revision 1 committed" "$ALL"
expect_said "the commit confirmed, with its window" \
  "to be confirmed within 1 minute(s) or rolled back" "$ALL"
expect_said "its expiry, the box's own revert" \
  "a commit confirmed 1 was not confirmed in time" "$ALL"
expect_said "the box's first reading records the boot" "the box booted" "$ALL"
expect_said "the restart, recorded as asked before it ran" \
  "system dataplane restart asked" "$ALL"
expect_said "the dataplane's start, with the operator's restart as its cause" \
  "the dataplane started again: system dataplane restart asked by root" "$ALL"
expect_said "the routing daemons' start, with the same cause" \
  "the routing daemons (FRR) started again: system dataplane restart asked" "$ALL"
expect_said "the dataplane that came back with nothing asking: unexpected" \
  "the dataplane started again and nothing on the box asked for it: unexpected" "$ALL"
# An event is a row that starts with its time.
events_in() { printf '%s\n' "$1" | grep -c '^[0-9][0-9][0-9][0-9]-'; }
last_event() { printf '%s\n' "$1" | awk '/^[0-9][0-9][0-9][0-9]-/ { e = $0; next }
  { e = e " " $0 } END { print e }' | tr -s ' '; }
# The order: newest last.
expect "newest last: the unexpected start is the last event" \
  sh -c "printf '%s' \"\$1\" | grep -q 'warning dataplane the dataplane started again and nothing'" \
  _ "$(last_event "$ALL")"
expect "source dataplane: only the dataplane's events, both starts" \
  sh -c "[ \"\$1\" = 2 ] && [ \"\$2\" = 2 ]" _ "$(events_in "$SRC_DP")" \
  "$(printf '%s\n' "$SRC_DP" | grep -c '  dataplane  ')"
expect "severity warning: the expiry and the unexpected start, nothing else" \
  sh -c "[ \"\$1\" = 2 ] && printf '%s' \"\$2\" | grep -q 'not confirmed in time' \
         && printf '%s' \"\$2\" | grep -q unexpected" _ "$(events_in "$SEV")" \
  "$(printf '%s' "$SEV" | tr '\n' ' ' | tr -s ' ')"
expect "last 2: two events, the unexpected start the second" \
  sh -c "[ \"\$1\" = 2 ] && printf '%s' \"\$2\" | grep -q unexpected" \
  _ "$(events_in "$LAST2")" "$(last_event "$LAST2")"
expect "since 1h | match rollback: the one rollback" \
  sh -c "[ \"\$1\" = 1 ] && printf '%s' \"\$2\" | grep -q 'rollback to revision 1'" \
  _ "$(events_in "$MATCH")" "$MATCH"
expect_in "since a date after everything: nothing, said" \
  "no event recorded matches" "$FUTURE"
expect_in "since a word that is no time: refused, with how to say one" \
  "is not a time" "$BAD"
expect "the idle timer runs wrote nothing: no noise in the record" \
  sh -c "[ \"$N_BEFORE\" -gt 0 ] && [ \"$N_BEFORE\" = \"$N_AFTER\" ]"
expect "and no door call ever reached it" \
  sh -c "! grep -q 'door:' \"$PRISMA_EVENTS\" && [ -s \"$PRISMA_EVENTS\" ]"
expect_in "show system uptime says why the dataplane last started" \
  "started by: nothing recorded: unexpected" "$UP"
expect_in "and why the routing daemons did" \
  "started by: system dataplane restart" "$UP"
verdict
