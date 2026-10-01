#!/bin/sh
# The heap stays flat with a 200 MB spill and a collector that never reads
# (#547).
#
#   sh tools/lab.sh syslog-stall-heap    -> LAB PASSED
#
# On the #475 bench, 0.149.4 read a 573 MB syslog spill WHOLE on every
# connection - vec_validate of the file, then vec_insert at the front of the
# buffer - and VPP aborted, "Out-of-memory, calling os_panic()" in
# bpa_log_stream_service, with no traffic at all; with a collector that took
# nothing, the buffer grew until it did the same. The rule the maintainer set:
# log export must never affect forwarding or translation, so it must never
# take the main heap with it.
#
# So: 200 MB of whole syslog lines are the spill before logging is enabled,
# and the collector accepts the connection and never reads a byte. The main
# heap is read before, through the replay and through allocations written
# meanwhile; then a collector that reads takes the stream over, and the heap
# is read again while the backlog drains.
# NO-OFF-RUN: the defect is a heap that grows by the spill's size until VPP aborts, and bringing it back would be a switch in the product that only aborts the box; the control is 0.149.4 on the #475 bench, which aborted with "Out-of-memory, calling os_panic()" replaying a 573 MB spill with no traffic
. /src/lab/lib.sh
. /src/lab/verdict.sh
set -u
HX="python3 /src/src/prismash/prismash.py"

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create host-interface name veth-vpp >/dev/null
vppctl set interface state host-veth-vpp up
# THE STREAM IS A KERNEL SOCKET, not a packet the dataplane forwards, so the
# collector has to be reachable from the VPP PROCESS - which means a linux_cp
# pair mirroring this interface as a TAP. Made through the VTY, the way the
# box makes it, for the reason lab/ipfix-tcp.sh records: with `vppctl lcp
# create` here, the lab proved a transport that worked only on a box somebody
# had paired by hand (#182).
$HX -c "configure" -c "interface host-veth-vpp" -c "linux-name vppmgmt0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'
vppctl set interface mtu 1500 host-veth-vpp
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
PEERMAC=$(ip netns exec peer cat /sys/class/net/veth-out/address)
vppctl set ip neighbor host-veth-vpp 10.0.0.2 $PEERMAC


vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up
vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1

vppctl bpa pool din inside 100.64.0.0/22 outside 200.150.0.64/26 block 16384 mode dynamic
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl bpa block-holddown 4

vppctl bpa block-holddown 2 >/dev/null

burst() {  # burst <name> <first> <last>: one allocation per subscriber
  vppctl packet-generator new "name $1 limit $(( $3 - $2 + 1 )) node ip4-input \
      interface pg0 size 60-60 data { UDP: 100.64.0.$2 - 100.64.0.$3 -> 8.8.8.8 \
      UDP: 40000 -> 53 incrementing 8 }" >/dev/null
  vppctl packet-generator enable-stream $1 >/dev/null
}


heap_mb() {  # the main heap's "used" in MB, from `show memory main-heap`
  vppctl show memory main-heap 2>&1 | tr -d '\r' | grep -o 'used: [0-9.]*[KMG]' | head -1 \
    | awk '{v=$2; u=substr(v,length(v)); n=substr(v,1,length(v)-1);
            if (u=="G") n*=1024; else if (u=="K") n/=1024; printf "%d\n", n}'
}

SPILL=/var/log/prisma-routing/syslog-spill.bin
rm -f "$SPILL" "$SPILL.off"
python3 - <<'PY'
line_n = 0
with open("/var/log/prisma-routing/syslog-spill.bin", "wb") as f:
    chunk = []
    size = 0
    while size < 200 * 1024 * 1024:
        m = (b"<134>1 2026-09-28T18:20:00.000Z prisma-bench prisma-cgnat - "
             b"BLOCK-ALLOC [cgnat@32473 subscriber=\"100.64.0.1\" public=\"203.0.113.1\" "
             b"portStart=\"1024\" portEnd=\"3071\"] planted-%08d" % line_n)
        rec = b"%d " % len(m) + m
        chunk.append(rec); size += len(rec); line_n += 1
        if len(chunk) == 10000:
            f.write(b"".join(chunk)); chunk = []
    f.write(b"".join(chunk))
print(line_n)
PY
echo "  planted: $(stat -c %s $SPILL) bytes of whole lines"

H0=$(heap_mb)
echo "  main heap before logging: ${H0} MB used"

echo "=== a collector that accepts and never reads ==="
ip netns exec peer python3 -c '
import socket, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("10.0.0.2", 601)); s.listen(4)
c, _ = s.accept()
time.sleep(40)
' > /tmp/stall.out 2>&1 &
STALL=$!
sleep 1
$HX -c "configure" -c "nat" -c "cgnat" \
    -c "logging syslog 10.0.0.2 enterprise 44444 port 601" -c "commit" >/dev/null 2>&1
HMAX=$H0
for t in 1 2 3 4 5 6 7 8; do
  sleep 2
  [ $t = 3 ] && burst s1 10 109     # a hundred allocations, written meanwhile
  h=$(heap_mb); [ "${h:-0}" -gt "$HMAX" ] && HMAX=$h
  echo "  t=$((t*2))s heap ${h} MB"
done
STALL_LINE=$(vppctl show bpa logging 2>&1 | tr -d '\r' | sed -n '/syslog transport/,$p' | grep -E "  file |  lines ")
printf '%s\n' "$STALL_LINE" | sed 's/^/  | /'
kill $STALL 2>/dev/null; wait $STALL 2>/dev/null

echo "=== the path itself, measured with a plain socket ==="
# The lab's path to the collector crosses a linux_cp TAP, the dataplane and an
# af_packet veth: what it carries is measured first, from the namespace the
# dataplane's own socket lives in, and the box is judged against it.
ip netns exec peer python3 -c '
import socket, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("10.0.0.2", 602)); s.listen(1); s.settimeout(20)
c, _ = s.accept(); c.settimeout(2); n = 0
while True:
    try:
        d = c.recv(1 << 20)
    except socket.timeout:
        break
    if not d: break
    n += len(d)
open("/tmp/path.txt", "w").write(str(n))
' &
PS=$!
sleep 1
python3 -c '
import socket, time
c = socket.create_connection(("10.0.0.2", 602)); b = b"x" * (1 << 16); end = time.time() + 8
while time.time() < end: c.sendall(b)
c.close()
' 2>/dev/null
wait $PS
PATH_MBS=$(( $(cat /tmp/path.txt 2>/dev/null || echo 0) / 8 / 1048576 ))
echo "  the path carries ${PATH_MBS} MB/s"

echo "=== a collector that reads takes the stream ==="
# A sink that frames by offset (RFC 6587 octet counting) and keeps no more
# than a partial frame: what is measured is how fast the BOX sends, not how
# fast a collector that re-joins its buffer on every read can parse.
ip netns exec peer python3 -c '
import socket, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("10.0.0.2", 601)); s.listen(4); s.settimeout(30)
c, _ = s.accept(); c.settimeout(1)
end = time.time() + 26; rest = b""; lines = bad = total = 0
while time.time() < end:
    try:
        d = c.recv(1 << 20)
    except socket.timeout:
        continue
    if not d:
        break
    total += len(d); b = rest + d; i = 0; n = len(b)
    while True:
        sp = b.find(b" ", i, i + 8)
        if sp < 0:
            break
        try:
            ln = int(b[i:sp])
        except ValueError:
            bad += 1; i = n; break
        if sp + 1 + ln > n:
            break
        if b[sp + 1:sp + 2] != b"<":
            bad += 1
        lines += 1; i = sp + 1 + ln
    rest = b[i:]
open("/tmp/r.txt", "w").write("lines %d\nmalformed %d\nbytes %d\n" % (lines, bad, total))
' > /tmp/r.out 2>&1 &
R=$!
HMAX2=$H0
for t in 1 2 3 4 5 6 7 8 9 10 11 12 13; do
  sleep 2
  h=$(heap_mb); [ "${h:-0}" -gt "$HMAX2" ] && HMAX2=$h
done
# What drove it: how often the log process ran, and the socket's own state.
echo "  -- bpa-log-process and the socket, mid-drain:"
vppctl show runtime bpa-log-process 2>&1 | tr -d '\r' | grep -E "bpa-log-process|Name" | sed 's/^/  | /'
wait $R
cat /tmp/r.txt | sed 's/^/  R: /'
R_LINES=$(awk '$1=="lines"{print $2}' /tmp/r.txt 2>/dev/null)
R_BAD=$(awk '$1=="malformed"{print $2}' /tmp/r.txt 2>/dev/null)
OFF=$(python3 -c "import struct;print(struct.unpack('<Q',open('$SPILL.off','rb').read())[0])" 2>/dev/null || echo 0)
vppctl show bpa logging 2>&1 | tr -d '\r' | sed -n '/syslog transport/,$p' | sed 's/^/  | /'
echo "  heap: ${H0} MB before, at most ${HMAX} MB stalled, at most ${HMAX2} MB draining; ${OFF} bytes of the spill taken"
H_ALIVE=$(vppctl show version >/dev/null 2>&1 && echo yes || echo no)

echo
echo "=========== VERDICT ==========="
expect "VPP is alive after replaying a 200 MB spill (${H_ALIVE})" test "$H_ALIVE" = yes
expect "the heap grew by less than 32 MB with the collector reading nothing (${H0} -> ${HMAX} MB)" \
  test $((HMAX - H0)) -lt 32
expect "  and by less than 32 MB while the 200 MB backlog drained (${H0} -> ${HMAX2} MB)" \
  test $((HMAX2 - H0)) -lt 32
R_BYTES=$(awk '$1=="bytes"{print $2}' /tmp/r.txt 2>/dev/null)
# At least 512 KB/s - some 2,600 lines a second (measured: ~790 KB/s);
# 0.149.4 delivered ~500 KB per connection, one every 16-90 s on the #475
# bench, so ~130 lines/s grew its spill for ever. Not the path's rate: in this
# lab the dataplane's own socket leaves through a TAP the same single thread
# polls between passes of bpa-log-process, which holds TCP near its first
# window (test/test_bpa_log_tcp.c drains the exporter itself many times faster
# on a socket pair). A bigger socket buffer doubled it here and was taken back:
# what the kernel holds is lost with a connection a stopped box abandons.
R_KBS=$(( ${R_BYTES:-0} / 26 / 1024 ))
expect "a reading collector drains the backlog at 512 KB/s or more, in whole lines (${R_KBS} KB/s on a path that carries ${PATH_MBS} MB/s, ${R_LINES:-0} lines, malformed ${R_BAD:-?})" \
  sh -c "test ${R_KBS} -ge 512 && test ${R_LINES:-0} -gt 0 && test ${R_BAD:-1} -eq 0"
expect "and the box saved how far into the spill it got (${OFF} bytes)" test "${OFF:-0}" -gt 0
verdict
