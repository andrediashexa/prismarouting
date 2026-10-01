#!/bin/sh
# OFF-RED-AT-LEAST: 5
# A syslog spill that begins mid-message is replayed from its first whole
# message, once (#547).
#
#   sh tools/lab.sh syslog-spill-resync       -> LAB PASSED
#   sh tools/lab.sh syslog-spill-resync off   -> LAB FAILED (built with
#                                                BPA_RESYNC_OFF: the front
#                                                left as it is, as to 0.149.4)
#
# On the #475 bench a spill written by 0.140.2 - the #492 cut - began with
# `otTime="2026-09-28T18:19:18.534Z" ...`, the tail of a boot marker, and it
# survived every upgrade. Each release since replayed it on every connection,
# could drop nothing behind it because its front never framed, wrote the whole
# buffer back to the file and sent it again on the next connection: the lab
# collector stored 55 million boot markers in three days, almost all of them
# copies.
#
# So: that file, as the bench had it - the fragment, then PLANTED whole lines
# each with a number of its own - is in place before logging is enabled.
# Collector A takes the stream for a while and goes away; collector B takes
# it after. Between them every planted line must arrive EXACTLY ONCE, nothing
# malformed, and the box must say how many bytes it skipped. With the front
# left as it was, A abandons the stream at its first byte and B gets the
# same garbage again: no planted line arrives, and both count it malformed.
. /src/lab/lib.sh
. /src/lab/verdict.sh
set -u
HX="python3 /src/src/prismash/prismash.py"

MODE=${1:-on}
mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing
if [ "$MODE" = off ]; then
  mkdir -p /tmp/build-off && cd /tmp/build-off \
    && cmake -G Ninja -DCMAKE_C_FLAGS='-DBPA_RESYNC_OFF' /src >/dev/null && ninja >/dev/null
else
  (cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
    mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
  }
fi
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


# The bench's file: the tail of a boot marker, then 40 whole lines.
FRAG='otTime="2026-09-28T18:19:18.534Z" observedAt="2026-09-28T18:19:18.534Z"] this box has been up since 2026-09-28T18:19:18.534Z'
python3 - "$FRAG" <<'PY'
import sys
frag = sys.argv[1].encode()
out = frag
for i in range(40):
    m = (b"<134>1 2026-09-28T18:20:%02d.000Z prisma-bench prisma-cgnat - "
         b"BOOT-MARKER - planted-%02d" % (i, i))
    out += b"%d " % len(m) + m
open("/var/log/prisma-routing/syslog-spill.bin", "wb").write(out)
PY
FRAG_LEN=$(printf '%s' "$FRAG" | wc -c)
echo "  planted: $(stat -c %s /var/log/prisma-routing/syslog-spill.bin) bytes," \
     "a $FRAG_LEN-byte fragment in front of 40 whole lines"

echo "=== collector A, then logging on ==="
ip netns exec peer python3 /src/tools/syslog_collector.py 601 12 /tmp/a.txt /tmp/a.lines \
  > /tmp/a.out 2>&1 &
A=$!
sleep 1
$HX -c "configure" -c "nat" -c "cgnat" \
    -c "logging syslog 10.0.0.2 enterprise 44444 port 601" -c "commit" >/dev/null 2>&1
wait $A
cat /tmp/a.txt | sed 's/^/  A: /'

echo "=== collector A is gone; collector B ==="
ip netns exec peer python3 /src/tools/syslog_collector.py 601 30 /tmp/b.txt /tmp/b.lines \
  > /tmp/b.out 2>&1 &
B=$!
sleep 2
# Allocations written after A went away: what B is given is what a second
# connection sends. (The boot marker is once per boot now, #547, so it is not
# what proves the reconnection.)
burst b1 110 129; sleep 6
wait $B
cat /tmp/b.txt | sed 's/^/  B: /'
echo "  -- the exporter:"
vppctl show bpa logging 2>&1 | tr -d '\r' | sed -n '/syslog transport/,$p' | sed 's/^/  | /'

PLANTED=$(cat /tmp/a.lines /tmp/b.lines 2>/dev/null | grep -o 'planted-[0-9][0-9]' | wc -l)
DISTINCT=$(cat /tmp/a.lines /tmp/b.lines 2>/dev/null | grep -o 'planted-[0-9][0-9]' | sort -u | wc -l)
BAD=$(( $(awk '$1=="malformed"{print $2}' /tmp/a.txt 2>/dev/null || echo 1) + \
        $(awk '$1=="malformed"{print $2}' /tmp/b.txt 2>/dev/null || echo 1) ))
FILE_LINE=$(vppctl show bpa logging 2>&1 | tr -d '\r' | sed -n '/syslog transport/,$p' | grep "  file ")
RESYNCED=$(vppctl show bpa logging 2>&1 | tr -d '\r' | sed -n '/syslog transport/,$p' \
           | sed -n 's/.* \([0-9][0-9]*\) resynced.*/\1/p' | head -1)

echo
echo "=========== VERDICT ==========="
expect "all 40 planted lines reached a collector ($DISTINCT distinct)" \
  test "$DISTINCT" -eq 40
expect "  each exactly once - nothing sent again on the next connection ($PLANTED)" \
  test "$PLANTED" -eq 40
expect "  and nothing the collectors were given was malformed ($BAD)" \
  test "$BAD" -eq 0
B_ALLOC=$(awk '$1=="BLOCK-ALLOC"{print $2}' /tmp/b.txt 2>/dev/null)
expect "the box reconnected to B and B framed the 20 allocations after A (${B_ALLOC:-0})" \
  test "${B_ALLOC:-0}" -eq 20
expect "the box says it skipped the $FRAG_LEN-byte fragment (${RESYNCED:-none})" \
  test "${RESYNCED:-0}" -eq "$FRAG_LEN"
# The file line, word for word where the figures are known: nothing left on
# disk, nothing over the ceiling, nothing stalled.
expect "and its file line says so: '${FILE_LINE}'" \
  sh -c "printf '%s' \"\$1\" | grep -q '0 bytes now of 256 MB at most, 0 dropped over it, $FRAG_LEN resynced, 0 stalls'" _ "$FILE_LINE"
verdict
