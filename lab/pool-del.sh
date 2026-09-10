#!/bin/sh
# How long deleting a pool stops forwarding, measured from VPP's own barrier
# accounting, at two session counts.
#
#   sh tools/lab.sh pool-del
#
# The worker barrier is what stops forwarding: while it is held every worker
# spins and no packet moves. VPP records how long each acquisition lasted, so
# this lab does not have to infer the stall from packet counts - it reads the
# number VPP itself wrote.
#
# Two counts, because the defect this lab exists for is a walk with no bound:
# a delete whose cost grows with the size of the session table is an outage
# whose length is decided by how busy the box was. One count could not tell
# the difference between that and a delete that costs what it costs.
set -u

mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting /etc/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

# Workers, and not for realism: with fewer than two threads VPP's barrier
# returns without doing anything, so a single-threaded box has no stall to
# measure and this lab would report zero on a build that has the defect.
vpp -c /src/lab/startup-workers.conf > /tmp/vpp.log 2>&1 &
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
# Nothing may expire while the lab runs: the sessions have to still be there
# when the delete walks them, or the walk is being timed over an empty table.
vppctl bpa timeouts udp 3600
vppctl event-logger trace barrier > /dev/null

sessions() {
  vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}'
}

# fill <subscribers> <sessions each>: one packet-generator stream per
# subscriber, each stepping through a range of source ports, so one enable
# opens <subscribers> x <sessions each> mappings.
fill() {
  rm -f /tmp/pg.txt
  i=0
  while [ "$i" -lt "$1" ]; do
    a=$(( i / 256 )); b=$(( i % 256 ))
    echo "packet-generator new { name f$i limit $2 node ip4-input \
      interface pg0 size 60-60 data { UDP: 100.64.$a.$b -> 8.8.8.8 \
      UDP: 10000-$(( 10000 + $2 - 1 )) -> 53 incrementing 8 } }" >> /tmp/pg.txt
    i=$(( i + 1 ))
  done
  vppctl exec /tmp/pg.txt > /dev/null
  vppctl packet-generator enable > /dev/null
  n=0
  until [ "$(sessions)" -ge "$(( $1 * $2 ))" ] || [ "$n" -ge 60 ]; do
    sleep 1; n=$(( n + 1 ))
  done
  i=0
  while [ "$i" -lt "$1" ]; do
    vppctl packet-generator delete "f$i" > /dev/null 2>&1
    i=$(( i + 1 ))
  done
}

# Everything the barrier events say about a delete. Reads a saved copy of the
# log rather than asking VPP twice: "show event-logger" is itself a CLI command
# that takes the barrier, so the second read would be reporting the first one.
ELOG=/tmp/elog.txt
capture_elog() { vppctl show event-logger all > "$ELOG" 2>/dev/null; }

# One microsecond figure per barrier acquisition the delete is responsible for,
# one per line.
#
# "bar-trace-<who>-#<n>" opens an acquisition and
# "bar-rel-#<n>-e<entry>-u<update>-t<closed>" closes it; the last field is how
# long it was held closed, which is how long forwarding was stopped.
#
# Which acquisitions count depends on whether the delete took any of its own. If
# it did they are the ones to look at. If it did NOT, the walk ran inside the
# CLI or API dispatcher's barrier and that acquisition is the delete - which is
# the shape this lab has to be able to fail on, so it must not be filtered out.
# Either way the expiry process is excluded: it takes the barrier once a second
# on its own account and is not what is being measured.
#
# The sub(/\r$/) is not decoration: vppctl ends its lines with CR+LF, so the
# key parsed out of one line did not match the key parsed out of the next and
# every measurement came back zero - a lab reporting no stall at all on a build
# that had one.
del_times() {
  _own=none
  grep -q "bar-trace-bpa_pool_del-" "$ELOG" && _own=bpa_pool_del
  awk -v own="$_own" '
    { sub(/\r$/, "") }
    /bar-trace-/ {
      n = $2; sub(/.*#/, "", n)
      who = $2; sub(/^bar-trace-/, "", who); sub(/-#.*/, "", who)
      if (own == "none" ? who != "bpa_expire_process" : who == own) mine[n] = 1
      next
    }
    /bar-rel-#/ {
      n = $2; sub(/^bar-rel-#/, "", n); sub(/-.*/, "", n)
      t = $2; sub(/.*-t/, "", t)
      if (n in mine) print t + 0
    }' "$ELOG"
}

# How many acquisitions the delete took on its own account, and over how many
# milliseconds. Zero acquisitions means the walk ran inside somebody else's
# barrier, which is the defect.
del_span() {
  awk '
    { sub(/\r$/, "") }
    /bar-trace-bpa_pool_del-/ { acq++; if (acq == 1) first = $1 + 0; next }
    /bar-trace-/ { n = $2; sub(/.*#/, "", n); other[n] = 1; next }
    /bar-rel-#/ {
      n = $2; sub(/^bar-rel-#/, "", n); sub(/-.*/, "", n)
      if (!(n in other)) last = $1 + 0
    }
    END {
      if (acq == 0) print "0 0"
      else printf "%d %d\n", acq, (last - first) * 1000
    }' "$ELOG"
}

# measure <subscribers> <sessions each>
#
# The subscriber traffic keeps running THROUGH the delete. A budgeted walk
# drops the barrier while the pool is half drained, so the datapath sees a
# pool that exists, has live sessions, and must not be given any more - and a
# fix that traded the outage for a crash would be found here rather than by a
# customer.
measure() {
  vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.32/27 block 512 \
    > /dev/null
  fill "$1" "$2"
  HELD=$(sessions)

  # Ports 10000 upwards: the first few hundred are mappings the fill opened,
  # so they hit sessions of the pool being drained, and the rest are misses
  # that would open new ones. Both have to be safe while the pool is half
  # gone - the second is what makes the walk terminate, and the first is what
  # would be reading a freed definition if the drain freed one too early.
  vppctl packet-generator new "name live node ip4-input interface pg0 \
    rate 2000 size 60-60 data { UDP: 100.64.0.1 -> 8.8.8.8 \
    UDP: 10000-19999 -> 53 incrementing 8 }" > /dev/null
  vppctl packet-generator enable live > /dev/null

  vppctl event-logger clear > /dev/null
  vppctl bpa pool lab del > /dev/null
  capture_elog
  TIMES=$(del_times | sort -n)
  WORST=$(printf '%s\n' "$TIMES" | tail -1)
  set -- $(del_span)
  NBAR=$1
  MS=$2

  vppctl packet-generator disable live > /dev/null 2>&1
  vppctl packet-generator delete live > /dev/null 2>&1
  LEFT=$(sessions)
  ALIVE=$(vppctl show version 2>&1)

  echo "    $HELD sessions, every acquisition in us:"
  echo "      $(printf '%s\n' "$TIMES" | tr '\n' ' ')"
  echo "    worst ${WORST}us, ${NBAR} acquisition(s) of the delete's own over"
  echo "    ${MS}ms, $LEFT sessions left"
}

# best <subscribers> <sessions each> <runs>
#
# The delete, several times over, keeping the LEAST disturbed run.
#
# The worst single acquisition is the figure that matters - it is how long
# forwarding stopped - and on a shared build machine it is also the figure a
# descheduled worker lands in: an acquisition waits for every worker to arrive,
# and one acquisition in thirty has been seen waiting a full second here for
# reasons that have nothing to do with what is inside it. That noise only ever
# ADDS, so the smallest of several runs is the honest estimate of what the
# delete itself costs, and no run is thrown away silently - they are all
# printed.
best() {
  BEST=999999999
  _r=1
  while [ "$_r" -le "$3" ]; do
    measure "$1" "$2"
    if [ "${WORST:-999999999}" -lt "$BEST" ]; then
      BEST=$WORST
      BEST_NBAR=$NBAR
    fi
    _r=$(( _r + 1 ))
  done
}

echo "=========== 1. a small table, with traffic running ==========="
best 20 500 3
SMALL_N=$HELD
SMALL=$BEST
SMALL_ALIVE=$ALIVE

echo
echo "=========== 2. a table five times the size ==========="
best 100 500 3
BIG_N=$HELD
BIG=$BEST
BIG_NBAR=$BEST_NBAR
BIG_LEFT=$LEFT
BIG_ALIVE=$ALIVE

echo
echo "=========== 3. what the numbers say ==========="
echo "  $SMALL_N sessions -> ${SMALL}us"
echo "  $BIG_N sessions -> ${BIG}us"
echo "  A walk with no bound multiplies by five here. A budgeted one does not."

echo
echo "=========== 4. the pool is gone, and so are its sessions ==========="
POOLS=$(vppctl show bpa pool)
echo "$POOLS" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

# The precondition. Without the sessions there is nothing being timed.
expect "the small table was filled" test "${SMALL_N:-0}" -ge 9000
expect "the large table was several times it" \
  test "${BIG_N:-0}" -ge "$(( ${SMALL_N:-1} * 4 ))"

# Read the number before believing it. A harness that reports no stall reads
# the same as a box that has none, and this one did exactly that for a while.
expect "a barrier was actually measured" test "${SMALL:-0}" -gt 0
expect "on both tables" test "${BIG:-0}" -gt 0

# THE property, and it is a RATIO rather than a number of microseconds: the
# cost of one acquisition must not follow the size of the table. Twice the
# small figure is the slack a budgeted walk needs for the scan it still pays
# over entries belonging to nobody.
#
# A ratio because this lab runs beside fifteen others in the suite, and a
# barrier acquisition waits for every worker to arrive. Under that contention
# an acquisition was measured at 1 008 443 us - a hair under VPP's own 1.0 s
# barrier timeout - for reasons that have nothing to do with what is inside
# it. That noise only ever adds, and it adds to BOTH measurements, so the
# ratio survives it and an absolute threshold does not: the first version of
# this section asserted "under 20 ms" and passed alone, then failed in the
# suite while measuring nothing but the host.
expect "the stall does not grow with the session table" \
  test "${BIG:-999999}" -lt "$(( ${SMALL:-1} * 2 + 2000 ))"

# A ceiling as well, loose on purpose. It is not the measurement - the ratio
# above is - and it is here only to catch a regression so large that the ratio
# could still hold while forwarding stopped for a second. The unbudgeted walk
# it replaced measured 37 ms at 49k sessions UNCONTENDED and grew linearly, so
# anything approaching VPP's barrier timeout is a different failure entirely.
expect "and no single acquisition approaches VPP's own barrier timeout" \
  test "${BIG:-999999}" -lt 500000

# The mechanism, not just the number: a delete that takes no barrier of its own
# is running inside the dispatcher's, for as long as the walk takes.
expect "the delete takes its own barriers rather than the dispatcher's" \
  test "${BIG_NBAR:-0}" -gt 1

# The correctness constraint the delete exists for: sessions MUST NOT outlive
# the definition that produced them, however the walk is budgeted.
expect "no session outlives the pool that produced it" test "${BIG_LEFT:-1}" -eq 0
expect_in "and the pool is gone" "no pools configured" "$POOLS"

# A fix that traded an outage for a crash would not be a fix. Traffic was
# arriving for this pool throughout both deletes.
expect_in "the box survived a delete with traffic on the pool" \
  "vpp v" "$SMALL_ALIVE"
expect_in "and survived it on the larger table too" "vpp v" "$BIG_ALIVE"

verdict
