#!/bin/sh
# PPPoE sessions up to the configured limit, on the statistics segment the tuner sizes for it
# NO-OFF-RUN: the control is in the same run - VPP's 32 MB default driven to its abort - and the product before docs/bng.md 0.4 item 5 fails this lab outright (a block with no session-limit sized nothing, and pppoe.segment_room did not exist)
#
#   sh tools/lab.sh pppoe-scale                  the default limit, 16,000
#   sh tools/lab.sh pppoe-scale 40000 100000     those session-limits too
#   LAB_WORKERS=3 sh tools/lab.sh pppoe-scale    the same with three workers
#
# WHAT IT DEFENDS. Every PPPoE session is an interface in the dataplane, and
# every interface puts counters in VPP's statistics segment. A segment that
# runs out is not a refused session: it is os_panic and SIGABRT, the whole
# dataplane and every subscriber on it (docs/bng.md 2.1). The box has three
# answers to that, and this lab drives each against a running VPP:
#
#   1. the tuner sizes the segment for `pppoe` > `session-limit`, or for
#      pppoe.SESSION_LIMIT_DEFAULT when the block has none - the census is
#      the tuner's own configured_interfaces() on a configuration file, and
#      the size its own statseg_mb_for(), imported and not copied;
#   2. accel-ppp is told never to take more than the RUNNING segment holds
#      (pppoe.segment_room, the VTY's arithmetic), which is what protects a
#      box that has not rebooted since its limit was raised or since it was
#      upgraded onto one - checked here by filling VPP's default segment to
#      exactly that number and requiring the dataplane to be there;
#   3. a commit refuses a limit the box's RAM cannot give a segment for -
#      test/test_pppoe.py, which needs no dataplane.
#
# THE CONTROL IS THE ABORT ITSELF: VPP's 32 MB default, sessions created
# until the dataplane is gone, in the same run. A lab that only showed a big
# segment holding many sessions would pass on any size big enough.
#
# HOW, which is docs/bng.md's appendix method and measures the DATAPLANE half
# only: one PPPoE discovery frame per synthetic subscriber into the access
# veth from a raw AF_PACKET socket (nothing else fills the plugin's link
# table, 1.3), then `create pppoe session` per subscriber through `vppctl
# exec`, then `set interface bpa in2out` on each - what
# appliance/prisma-bng-session does for every session that comes up. The
# control plane is not here: lab/pppoe.sh is the end-to-end evidence, and
# accel-ppp's `max-sessions` is its own code (pppoe.c, conf_max_sessions).
#
# THE ATTACH, AT EVERY SIZE (docs/bng.md 0.4 item 5's second half). Each step
# times its attaches, and the median of the first third of the steps and of
# the last third are printed - NOT ASSERTED. On the shared host it was
# measured on, with other labs' workers polling, one run read 887 us a session
# at 2,000 and 6,386 us at 100,000 while the run before read 15,422 us at
# 2,000 and 865 us at 40,000: the machine, not the session count. The ratio
# is lab/attach-scale.sh's assertion, which times batches back to back on one
# dataplane; run it with LAB_WORKERS for the workers.
set -u
mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
. /src/lab/verdict.sh

SCALES="default ${*:-}"
STEP=2000
CONTROL_STEP=500
HEAP=4G

# The product's arithmetic, by import: the tuner's census and size, and the
# VTY's room. A lab that recomputed either would assert its own copy.
py() {
  PRISMA_MGMT_PATH=/tmp/mgmt-path python3 - "$@" <<'PY'
import importlib.machinery, importlib.util, sys
sys.path.insert(0, "/src/src/prismash")
p = "/src/appliance/prisma-routing-tune"
l = importlib.machinery.SourceFileLoader("tune", p)
s = importlib.util.spec_from_file_location("tune", p, loader=l)
T = importlib.util.module_from_spec(s); l.exec_module(T)
import pppoe
what = sys.argv[1]
if what == "census":            # <config file> -> named+mirrors runtime
    c, why = T.configured_interfaces(sys.argv[2])
    print(f"{c['named'] + c['mirrors']} {c['runtime']}" if c else f"0 0 {why}")
elif what == "mb":              # <interfaces> <threads> -> MB, or empty
    print(T.statseg_mb_for(int(sys.argv[2]), int(sys.argv[3])) or "")
elif what == "room":            # <segment text file> <threads text file> <named>
    mb = pppoe.segment_mb(open(sys.argv[2]).read())
    th = pppoe.thread_count(open(sys.argv[3]).read())
    print(pppoe.segment_room(T, mb, th, int(sys.argv[4])))
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

# boot <statseg MB or empty>: a fresh dataplane on that segment, an access
# interface on a veth, the control plane's TAP, and a BPA pool to attach to.
boot() {
  stop_vpp
  ip link del acc0 2>/dev/null
  ip link add acc0 type veth peer name accp
  ip link set acc0 up; ip link set accp up
  sed -e "s/main-heap-size [0-9]*[MG]/main-heap-size $HEAP/" \
    "$(startup_conf bng)" > /tmp/startup-scale.conf
  [ -n "$1" ] && printf '\nstatseg {\n  size %sM\n}\n' "$1" >> /tmp/startup-scale.conf
  vpp -c /tmp/startup-scale.conf > /tmp/vpp.log 2>&1 &
  wait_vpp
  vppctl create host-interface name acc0 > /dev/null
  vppctl set interface state host-acc0 up
  vppctl create tap id 0 host-if-name pppoe-cp > /dev/null
  vppctl set interface state tap0 up
  vppctl create pppoe cp cp-if-index "$(vppctl show interface tap0 | tr -d '\r' \
    | awk '$1 == "tap0" { print $2 }')"
  vppctl bpa pool lab inside 100.64.0.0/10 outside 203.0.113.0/24 block 512 \
    > /dev/null
}

us() { echo $(($(date +%s%N) / 1000)); }
alive() { vppctl show version > /dev/null 2>&1; }
vpp_rss() { awk '/VmRSS/ { print $2 }' "/proc/$(pgrep -f 'vpp -[c] ' | head -1)/status" 2>/dev/null; }
seg_line() {
  vppctl show statistics segment /sys/vector_rate verbose 2>/dev/null | tr -d '\r'
}
seg_used() { seg_line | sed -n 's/.*total: *\([^,]*\), *used: *\([^,]*\).*/\2 of \1/p'; }
heap_used() {
  vppctl show memory main-heap 2>/dev/null | tr -d '\r' \
    | sed -n 's/.*total: *\([^,]*\), *used: *\([^,]*\).*/\2 of \1/p' | head -1
}

# fill <up to> <step>: sessions, attached, in steps until <up to> exist or the
# dataplane dies. Prints one line a step to stderr; the count live after the
# last step survived to stdout; each step's attach cost to /tmp/attach.
fill() {
  _have=0; : > /tmp/attach
  while [ "$_have" -lt "$1" ]; do
    _n=$2
    [ $((_have + _n)) -gt "$1" ] && _n=$(($1 - _have))
    python3 - accp $((_have + 1)) "$_n" <<'PY'
import socket, struct, sys
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
s.bind((sys.argv[1], 0))
for i in range(int(sys.argv[2]), int(sys.argv[2]) + int(sys.argv[3])):
    mac = bytes([2, 0xbb, i >> 24 & 255, i >> 16 & 255, i >> 8 & 255, i & 255])
    tags = struct.pack("!HH", 0x0101, 0)        # Service-Name, any
    s.send(b"\xff" * 6 + mac + b"\x88\x63"
           + struct.pack("!BBHH", 0x11, 0x09, 0, len(tags)) + tags)
PY
    sleep 0.5
    python3 - $((_have + 1)) $((_have + _n)) > /tmp/create.cli <<'PY'
import sys
for i in range(int(sys.argv[1]), int(sys.argv[2]) + 1):
    print(f"create pppoe session client-ip 100.{64 + (i >> 16)}.{i >> 8 & 255}."
          f"{i & 255} session-id {i % 65000 + 1} client-mac 02:bb:"
          f"{i >> 24 & 255:02x}:{i >> 16 & 255:02x}:{i >> 8 & 255:02x}:"
          f"{i & 255:02x}")
PY
    _t0=$(us); vppctl exec /tmp/create.cli > /tmp/create.out 2>&1; _t1=$(us)
    alive || break
    # pppoe_session<k> is the plugin's k-th, from 0, on a fresh dataplane.
    seq "$_have" $((_have + _n - 1)) \
      | sed 's/^/set interface bpa in2out pppoe_session/' > /tmp/attach.cli
    _a0=$(us); vppctl exec /tmp/attach.cli > /tmp/attach.out 2>&1; _a1=$(us)
    alive || break
    [ -s /tmp/create.out ] && { echo "  refused: $(head -1 /tmp/create.out)" >&2; break; }
    [ -s /tmp/attach.out ] && { echo "  attach refused: $(head -1 /tmp/attach.out)" >&2; break; }
    _have=$((_have + _n))
    _c=$(((_t1 - _t0) / _n)); _a=$(((_a1 - _a0) / _n))
    echo "$_a" >> /tmp/attach
    echo "    $_have: RSS $(($(vpp_rss) / 1024)) MB, segment $(seg_used), heap $(heap_used), create ${_c} us, attach ${_a} us" >&2
  done
  echo "$_have"
}

conf() {  # <limit or "default"> -> /tmp/scale.conf, the box's saved file
  {
    echo "pppoe"
    echo " interface host-acc0"
    echo " gateway 100.64.0.1"
    echo " address-pool 100.64.0.0/10"
    echo " local-user lab password lab"
    [ "$1" = default ] || echo " session-limit $1"
    echo " exit"
  } > /tmp/scale.conf
}

echo "=========== 1. the control: VPP's 32 MB default, driven to its abort ==========="
boot ""
WORKERS=$(box_workers)
THREADS=$((WORKERS + 1))
echo "  $WORKERS worker(s), $THREADS thread(s)"
DEF=$(fill 100000 "$CONTROL_STEP")
alive && DEAD=no || DEAD=yes
echo "  $DEF sessions live after the last step it survived; aborted: $DEAD"
grep -m1 -E "os_panic|SIGABRT|received signal" /tmp/vpp.log | sed 's/^/  /'
conf default
set -- $(py census /tmp/scale.conf)
DEFAULT_LIMIT=$2
expect "the default segment aborts the dataplane before the default limit, $DEFAULT_LIMIT sessions" \
  test "$DEAD" = yes -a "$DEF" -lt "${DEFAULT_LIMIT:-0}"

echo
echo "=========== 2. the ceiling the VTY gives accel-ppp on that segment ==========="
# A box upgraded onto the default limit, or whose limit was raised, and not
# rebooted: the segment is still VPP's default. What the VTY tells accel-ppp
# is pppoe.segment_room() of the RUNNING segment; that many must not abort.
boot ""
conf default
set -- $(py census /tmp/scale.conf)
NAMED=$1
seg_line > /tmp/seg.txt
vppctl show threads | tr -d '\r' > /tmp/threads.txt
ROOM=$(py room /tmp/seg.txt /tmp/threads.txt "$NAMED")
echo "  the running segment holds $ROOM sessions by the tuner's arithmetic"
GOT=$(fill "${ROOM:-0}" "$STEP")
alive && ALIVE=yes || ALIVE=no
echo "  $GOT of $ROOM created, dataplane alive: $ALIVE"
expect "held below the default limit on VPP's default segment ($ROOM < $DEFAULT_LIMIT)" \
  test "${ROOM:-0}" -gt 0 -a "${ROOM:-0}" -lt "$DEFAULT_LIMIT"
expect "the segment holds the ceiling accel-ppp is given: $ROOM sessions, dataplane up" \
  test "$ALIVE" = yes -a "$GOT" -eq "${ROOM:-0}"

N_SEC=3
for L in $SCALES; do
  echo
  conf "$L"
  set -- $(py census /tmp/scale.conf)
  NAMED=$1; LIMIT=$2
  MB=$(py mb $((NAMED + LIMIT)) "$THREADS")
  echo "=========== $N_SEC. session-limit $L: $LIMIT sessions, the tuner's segment ${MB:-VPP default} MB on $THREADS thread(s) ==========="
  N_SEC=$((N_SEC + 1))
  boot "$MB"
  _t0=$(date +%s)
  GOT=$(fill "$LIMIT" "$STEP")
  _t1=$(date +%s)
  alive && ALIVE=yes || ALIVE=no
  LIVE=$(vppctl show interface 2>/dev/null | tr -d '\r' | grep -c '^pppoe_session')
  echo "  $GOT of $LIMIT created and attached in $((_t1 - _t0)) s, $LIVE in the dataplane, alive: $ALIVE"
  python3 - /tmp/attach <<'PY'
import statistics, sys
a = [int(x) for x in open(sys.argv[1]) if x.strip()]
k = max(1, len(a) // 3)
if a:
    print(f"  attach, median a session: {statistics.median(a[:k]):.0f} us in "
          f"the first third of the steps, {statistics.median(a[-k:]):.0f} us "
          f"in the last (printed, not asserted: see the header)")
PY
  expect "a segment the tuner sized for session-limit $L holds $LIMIT sessions, attached to the CGNAT" \
    test "$LIMIT" -gt 0 -a "$ALIVE" = yes -a "$GOT" -eq "$LIMIT" -a "$LIVE" -eq "$LIMIT"
  # The room the VTY reads off this segment is the limit itself: a booted box
  # sized for it is not held below it, and not told so at every boot.
  seg_line > /tmp/seg.txt
  vppctl show threads | tr -d '\r' > /tmp/threads.txt
  ROOM=$(py room /tmp/seg.txt /tmp/threads.txt "$NAMED")
  expect "and the VTY reads it as holding the limit ($ROOM >= $LIMIT): accel-ppp is not held lower" \
    test "${ROOM:-0}" -ge "$LIMIT"
done
stop_vpp

echo
verdict
