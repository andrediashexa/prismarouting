#!/bin/sh
# How many interfaces a statistics segment holds before the dataplane aborts
# NO-OFF-RUN: a measurement whose control is in the same run - VPP's own 32 MB default, which this lab drives to its abort before asserting that the tuner's size does not reach one
#
#   sh tools/lab.sh statseg-scale                 10,000 and 50,000
#   sh tools/lab.sh statseg-scale 10000           10,000 only
#   LAB_WORKERS=2 sh tools/lab.sh statseg-scale   the same, with two workers
#   sh tools/lab.sh statseg-scale old 10000       and the size #288 gave too
#
# WHAT IT DEFENDS. VPP's statistics segment has a fixed size, every interface
# costs it counters, and running out is not a refused interface: it is
# os_panic and SIGABRT, the whole dataplane (docs/bng.md 2.1, docs/appliance.md
# "The statistics segment"). prisma-routing-tune sizes the segment for the
# interfaces a box will hold. This lab is the only place that sizing meets a
# running VPP: it boots the dataplane on the size the tuner's own function
# computes - imported from appliance/prisma-routing-tune, not copied - creates
# that many interfaces, and requires the box still to be there.
#
# THE CONTROL IS THE ABORT ITSELF, on VPP's default. A lab that only showed a
# big segment holding many interfaces would pass on any size big enough, and
# could not tell a formula from a lucky constant. So the default is driven to
# its abort first, in the same run and on the same machine, and the number it
# dies at is printed: that is the instrument showing it can see the failure it
# is guarding against.
#
# THE WORKER COUNT IS PART OF THE COST. Each interface's counters are kept per
# thread, so a box with workers spends more of the segment on each interface
# than the single-threaded box #288's figure was measured on. With
# LAB_WORKERS set this lab measures that box; the tuner is told the same
# thread count, which is what it reads off the hardware on an installed box.
#
# Dot1q sub-interfaces stand in for subscriber interfaces, made by
# make_interfaces in lab/lib.sh: each is a software interface with its
# counters in the segment, which is what a PPPoE session is too. What a
# loopback, a sub-interface and a session each cost the segment is in the
# tuner's STATSEG_GROWTH table, measured rather than assumed equal.
set -u
mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
. /src/lab/verdict.sh

OLD_TOO=""
[ "${1:-}" = old ] && { OLD_TOO=1; shift; }
SCALES=${*:-10000 50000}
STEP=100
# Read off the box after the first boot (box_workers), never from the
# request: the tuner is told the threads this dataplane actually runs.
WORKERS=0

# The tuner's own arithmetic, by import. A lab that recomputed the size would
# be asserting its own copy of the formula.
tuner_mb() {  # <interfaces> <threads> -> MB, or empty for VPP's default
  python3 - "$1" "$2" <<'PY'
import importlib.machinery, importlib.util, os, sys
os.environ.setdefault("PRISMA_MGMT_PATH", "/tmp/mgmt-path")
p = "/src/appliance/prisma-routing-tune"
l = importlib.machinery.SourceFileLoader("tune", p)
s = importlib.util.spec_from_file_location("tune", p, loader=l)
m = importlib.util.module_from_spec(s); l.exec_module(m)
mb = m.statseg_mb_for(int(sys.argv[1]), threads=int(sys.argv[2]))
print(mb or "")
PY
}

stop_vpp() {
  _p=$(pgrep -f 'vpp -[c] ' | head -1)
  [ -n "$_p" ] && kill "$_p" 2>/dev/null
  _n=0; while pgrep -f 'vpp -[c] ' >/dev/null && [ "$_n" -lt 20 ]; do
    sleep 1; _n=$((_n + 1)); done
  _p=$(pgrep -f 'vpp -[c] ' | head -1)
  [ -n "$_p" ] && kill -9 "$_p" 2>/dev/null
  rm -f /run/vpp/cli.sock
}

boot() {  # <statseg MB or empty> - a fresh dataplane on that segment
  stop_vpp
  # startup_conf, so LAB_WORKERS reaches this lab the way it reaches every
  # other one.
  sed -e 's/main-heap-size [0-9]*[MG]/main-heap-size 4G/' "$(startup_conf test)" \
    > /tmp/startup-seg.conf
  [ -n "$1" ] && printf '\nstatseg {\n  size %sM\n}\n' "$1" >> /tmp/startup-seg.conf
  vpp -c /tmp/startup-seg.conf > /tmp/vpp.log 2>&1 &
  wait_vpp
}

seg_used() {  # "used of total", as VPP prints them
  vppctl show statistics segment /sys/vector_rate verbose 2>/dev/null \
    | tr -d '\r' | grep -m1 'total:.*used:' \
    | sed 's/.*total: *\([^,]*\), *used: *\([^,]*\).*/\2 of \1/'
}

# fill <up to>: interfaces in STEPs until <up to> exist or the dataplane dies.
# Prints the count that was live after the last step the dataplane survived.
# A fresh dataplane each time, so the names start again from loop0.
fill() {
  _have=0; MI_P=-1; MI_V=4095
  while [ "$_have" -lt "$1" ]; do
    : > /tmp/step.txt
    _i=$STEP
    [ $((_have + _i)) -gt "$1" ] && _i=$(($1 - _have))
    make_interfaces "$_i" /tmp/step.txt /dev/null
    vppctl exec /tmp/step.txt > /dev/null 2>&1
    vppctl show version > /dev/null 2>&1 || break
    _have=$((_have + _i))
    seg_used > /tmp/seg.txt
  done
  echo "$_have"
}

echo "=========== 1. the control: VPP's 32 MB default, driven to its abort ==========="
boot ""
WORKERS=$(box_workers)
DEF=$(fill 100000)
DEF_DEAD=no
vppctl show version > /dev/null 2>&1 || DEF_DEAD=yes
echo "  $WORKERS worker(s): $DEF interfaces live after the last step it survived; aborted: $DEF_DEAD"
echo "  segment at that step: $(cat /tmp/seg.txt 2>/dev/null)"
grep -m1 -E "os_panic|SIGABRT|received signal" /tmp/vpp.log | sed 's/^/  /'
expect "the default segment aborts the dataplane before 100,000 interfaces" \
  test "$DEF_DEAD" = yes

for N in $SCALES; do
  echo
  MB=$(tuner_mb "$N" "$((WORKERS + 1))")
  echo "=========== the tuner's size for $N interfaces, $((WORKERS + 1)) thread(s): ${MB:-VPP default} MB ==========="
  boot "$MB"
  GOT=$(fill "$N")
  vppctl show version > /dev/null 2>&1 && ALIVE=yes || ALIVE=no
  echo "  $GOT of $N created, dataplane alive: $ALIVE, segment $(cat /tmp/seg.txt 2>/dev/null)"
  expect "a segment the tuner sized for $N interfaces holds $N of them" \
    test "$ALIVE" = yes -a "$GOT" -eq "$N"
  # And how far past it, which is the margin the formula leaves - printed for
  # the documents, not asserted: it is what the formula costs, not whether it
  # works. A fresh dataplane on the same size, filled to its abort.
  if [ "$ALIVE" = yes ]; then
    boot "$MB"
    MAXN=$(fill $((N * 3)))
    vppctl show version > /dev/null 2>&1 && _end="stopped at 3x" || _end="aborted"
    echo "  headroom: the same segment held $MAXN, then $_end"
  fi
  # The size #288 gave this box, 4,793 bytes an interface whatever its
  # threads, for the record of what #597 changed. Not asserted either way.
  if [ -n "$OLD_TOO" ]; then
    OLD=$(( (N * 4793 + 1048575) / 1048576 ))
    [ "$OLD" -le 32 ] && OLD=""
    boot "$OLD"
    OGOT=$(fill "$N")
    echo "  #288's size, ${OLD:-VPP default} MB: $OGOT of $N, then $(vppctl show version >/dev/null 2>&1 && echo alive || echo aborted)"
  fi
done
stop_vpp

echo
verdict
