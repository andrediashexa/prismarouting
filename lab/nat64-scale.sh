#!/bin/sh
# NO-OFF-RUN: a measurement, not a verdict on a feature; its control is the NAT44 pool measured in the same run, on the same heap, with the same streams
#
# What a NAT64 session costs (#485), against a NAT44 one, measured the way
# docs/architecture.md "Memory" measured NAT44 for #460: VPP's main heap and
# the process's resident size, idle and then loaded by the packet generator,
# and then the clocks a packet spends in the translation node on sessions that
# already exist.
#
#   sh tools/lab.sh nat64-scale
#
# A NAT64 session is the NAT44 session plus three things: its in2out key is
# the 24-byte bihash (the subscriber's 16-byte address, port and protocol)
# rather than the 16-byte one; the IPv6 host is kept beside the session, 16
# bytes in a per-thread vector indexed like the session pool; and the session
# carries one more byte, which the struct's padding absorbs. The table below
# is what that comes to, rather than what it ought to.
#
# Each subscriber here is a /128 (subscriber-length 128), so the generator's
# address range walks subscribers one by one the way 100.64.x.y does for
# NAT44. Single-threaded: the per-session figure does not depend on the worker
# count, and the clocks are per packet on one core either way.
#
# READ lab/throughput.sh's HEADER BEFORE QUOTING A CLOCK FIGURE: the generator
# is not a NIC, and the clocks are the node's own, measured by VPP.
set -u
mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
. /src/lab/verdict.sh

# 2 GB: the NAT44 figure at two million sessions was 848 MB of heap.
sed 's/main-heap-size [0-9]*[MG]/main-heap-size 2G/' lab/startup-test.conf \
  > /tmp/startup-scale.conf

heap_used() {  # main heap in use, in MB
  vppctl show memory main-heap 2>&1 | tr -d '\r' \
    | sed -n 's/.*used: \([0-9.]*\)\([KMG]\).*/\1 \2/p' | head -1 \
    | awk '{v=$1; if ($2=="K") v/=1024; if ($2=="G") v*=1024; printf "%.1f", v}'
}
rss() {  # VPP's resident size, in MB
  awk '/^VmRSS/ {printf "%d", $2/1024}' "/proc/$(pgrep -f 'vpp -[c] ' | head -1)/status"
}
sessions() {
  vppctl show bpa session | tr -d '\r' | awk '/^ *total:/ {print $2}'
}
clocks() {  # clocks per packet, as VPP measured them, for one node
  vppctl show runtime | awk -v n="$1" '$1==n && $2=="active" {print $(NF-1)}' \
    | head -1
}

start() {  # <kind>: a fresh dataplane with one pool of that kind
  # By its command line: the process is not named `vpp`, and a first version
  # of this lab that killed by name measured both kinds on one dataplane.
  pkill -f 'vpp -[c] ' 2>/dev/null
  _n=0; while pgrep -f 'vpp -[c] ' >/dev/null && [ "$_n" -lt 20 ]; do
    sleep 1; _n=$((_n + 1)); done
  vpp -c /tmp/startup-scale.conf > /tmp/vpp.log 2>&1 &
  wait_vpp
  vppctl create packet-generator interface pg0 >/dev/null
  vppctl create packet-generator interface pg1 >/dev/null
  vppctl set interface state pg0 up; vppctl set interface state pg1 up
  vppctl set interface ip address pg1 192.0.2.1/24
  vppctl set ip neighbor pg1 192.0.2.2 02:00:00:00:00:02
  # A GLOBAL destination: the well-known prefix refuses to carry TEST-NET
  # (RFC 6052 3.1), measured - every packet to 192.0.2.2 was refused, so the
  # first NAT64 row was the cost of a refusal. Both kinds go to the same one.
  vppctl ip route add 8.8.8.8/32 via 192.0.2.2 pg1
  if [ "$1" = nat64 ]; then
    vppctl set interface ip address pg0 2001:db8:1200::fffe/112
    vppctl bpa pool p inside 2001:db8:1200::/112 subscriber-length 128 \
      nat64-prefix 64:ff9b::/96 outside 203.0.113.0/24 block 512
  else
    vppctl set interface ip address pg0 100.64.255.254/16
    vppctl bpa pool p inside 100.64.0.0/16 outside 203.0.113.0/24 block 512
  fi
  vppctl set interface bpa in2out pg0
  vppctl set interface bpa out2in pg1
  vppctl show bpa pool | grep -q "pool p" \
    || { echo "the $1 pool was refused; nothing below would mean anything"; exit 1; }
}

# The flows as a pcap the generator replays: pg's own edits cannot step an
# IPv6 address - measured, every packet left with source :: and the
# dataplane refused it - so both kinds are built here, the same way, for the
# comparison to be one method. Packet i is subscriber i mod S, port
# 20000 + i mod P: with S and P sharing no factor, S*P packets make every
# pair once. Checksums computed, so the translation's own arithmetic runs.
mkpcap() {  # <kind> <subscribers> <ports> <file>
  python3 - "$@" <<'PY'
import struct, sys, ipaddress
kind, S, P, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
def csum(b):
    if len(b) & 1: b += b"\0"
    t = sum(struct.unpack("!%dH" % (len(b) // 2), b))
    while t >> 16: t = (t & 0xffff) + (t >> 16)
    return t
payload = b"\0" * 8
with open(out, "wb") as f:
    f.write(struct.pack("<IHHiIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 101))
    if kind == "nat64":
        dst = ipaddress.IPv6Address("64:ff9b::808:808").packed
        base = int(ipaddress.IPv6Address("2001:db8:1200::1"))
    else:
        dst = ipaddress.IPv4Address("8.8.8.8").packed
        base = int(ipaddress.IPv4Address("100.64.0.1"))
    for i in range(S * P):
        sport = 20000 + i % P
        ulen = 8 + len(payload)
        if kind == "nat64":
            src = (base + i % S).to_bytes(16, "big")
            ph = src + dst + struct.pack("!IxxxB", ulen, 17)
        else:
            src = (base + i % S).to_bytes(4, "big")
            ph = src + dst + struct.pack("!xBH", 17, ulen)
        u = struct.pack("!HHHH", sport, 53, ulen, 0) + payload
        c = (~csum(ph + u)) & 0xffff or 0xffff
        u = u[:6] + struct.pack("!H", c) + u[8:]
        if kind == "nat64":
            pkt = struct.pack("!IHBB", 6 << 28, ulen, 17, 64) + src + dst + u
        else:
            h = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + ulen, 0, 0, 64, 17,
                            0, src, dst)
            h = h[:10] + struct.pack("!H", (~csum(h)) & 0xffff) + h[12:]
            pkt = h + u
        f.write(struct.pack("<IIII", 0, 0, len(pkt), len(pkt)) + pkt)
PY
}

stream() {  # <kind> <name> <subscribers> <ports>: one session per packet
  _f=/tmp/flows-$1-$3x$4.pcap
  [ -s "$_f" ] || mkpcap "$1" "$3" "$4" "$_f"
  [ "$1" = nat64 ] && _node=ip6-input || _node=ip4-input
  vppctl packet-generator delete "$2" >/dev/null 2>&1
  vppctl packet-generator new "{ name $2 limit $(($3 * $4)) node $_node \
    interface pg0 pcap $_f }" >/dev/null
  vppctl packet-generator enable-stream "$2"
  _n=0
  while [ "$_n" -lt 180 ]; do
    sleep 2; _n=$((_n + 2))
    vppctl show packet-generator | grep -q "$2.*Yes" || break
  done
  # The replayed packets are held in the heap; read nothing with them there.
  vppctl packet-generator delete "$2" >/dev/null 2>&1
}

# The address ranges walk the subscribers and the port range the ports, each
# one step a packet: with subscriber and port counts that share no factor, a
# limit of their product makes every pair once.
row() {  # <kind> <label> <subscribers> <ports>
  stream "$1" "s$3" "$3" "$4"
  printf "  %-6s %-34s %10s %10s MB %8s MB\n" "$1" "$2" "$(sessions)" \
    "$(heap_used)" "$(rss)"
}

RESULT=/tmp/nat64-scale.txt
: > $RESULT
for kind in nat44 nat64; do
  echo "=========== $kind ==========="
  printf "  %-6s %-34s %10s %13s %11s\n" kind load sessions "main heap" resident
  start $kind
  printf "  %-6s %-34s %10s %10s MB %8s MB\n" $kind idle 0 "$(heap_used)" "$(rss)" \
    | tee -a $RESULT
  row $kind "401 subscribers x 499 ports" 401 499 | tee -a $RESULT
  start $kind
  row $kind "4001 subscribers x 500 ports" 4001 500 | tee -a $RESULT

  # Clocks per packet on sessions that already exist: the same 2,000,500
  # flows again, which is the steady state a box spends its life in.
  vppctl clear runtime
  stream $kind again 4001 500
  if [ $kind = nat64 ]; then
    C=$(clocks bpa-nat64-in2out); I=$(clocks ip6-input)
  else
    C=$(clocks bpa-in2out); I=$(clocks ip4-input)
  fi
  echo "  $kind clocks/packet, existing sessions: translation $C, input $I" \
    | tee -a $RESULT
  eval "CLK_$kind=\$C"
  vppctl show errors | tr -d '\r' | grep -E "bpa" | grep -vE "^ *0 " \
    | sed 's/^/    /'
done

echo
S44=$(awk '$1=="nat44" && $2=="4001" {print $7}' $RESULT)
S64=$(awk '$1=="nat64" && $2=="4001" {print $7}' $RESULT)
expect "the NAT44 control reached two million sessions ($S44)" \
  sh -c "[ '${S44:-0}' -ge 2000000 ]"
expect "the NAT64 pool reached two million sessions ($S64)" \
  sh -c "[ '${S64:-0}' -ge 2000000 ]"
expect "a translation clock figure was read for each (${CLK_nat44:-} / ${CLK_nat64:-})" \
  sh -c "[ -n '${CLK_nat44:-}' ] && [ -n '${CLK_nat64:-}' ]"
verdict
