#!/bin/sh
# OFF-RED-AT-LEAST: 32
# (the floor: what the off run of 2026-10-04 measured red, every check)
# NAT64: an IPv6-only subscriber reaches an IPv4 server (#485), through a
# deterministic pool (sections 1-12) and then a dynamic one whose blocks are
# logged (13-14).
#
#   sh tools/lab.sh nat64          -> LAB PASSED
#   sh tools/lab.sh nat64 off      -> LAB FAILED (no NAT64 pool: nothing crosses)
#
#   sub  (netns)  2001:db8:1200:ab00::10/56 ... veth ... host-sub  VPP inside
#   srv  (netns)  192.0.2.2/24 ...................... host-srv    VPP outside
#
# The subscriber sends to 64:ff9b:1::<server> - the local-use prefix of RFC
# 8215, a /48, so the RFC 6052 layout with the u octet is what is exercised
# rather than the /96 that skips it - and the server must see the
# subscriber's public address with a port inside the block the pool's
# arithmetic gives the subscriber PREFIX, which ref/bpa_model.py computes
# independently here. Then each RFC 7915 case the issue names, read off the
# wire on both sides rather than off a counter: TCP both ways, UDP, a UDP
# datagram with no checksum from the IPv4 side, ICMP echo, an ICMP error
# from the IPv4 side with its quoted packet, Packet Too Big, and a
# fragmented UDP datagram in both directions.
#
# Sections 13 and 14 turn the pool dynamic and read its log off collectors:
# IPFIX and syslog naming the subscriber by its delegated prefix, RADIUS's
# Delegated-IPv6-Prefix giving its name, one exhaustion record for a
# subscriber's many refused flows, releases after the hold-down and at the
# pool's drain - once with VPP carrying the log and once with the log
# container (prisma-logd) carrying it.
#
# OFF MODE adds no NAT64 pool. The same traffic must then cross nothing: the
# checks that say a packet arrived go red, which is what makes them mean
# something (docs/context.md 1.4).
set -u
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
. /src/lab/verdict.sh
use_binary_api
HX="python3 /src/src/prismash/prismash.py"

# --- the two hosts ---------------------------------------------------------
for ns in sub srv; do
  ip netns add $ns 2>/dev/null
  ip link add v-$ns type veth peer name host-$ns 2>/dev/null
  ip link set v-$ns netns $ns
  ip link set host-$ns up
  ip netns exec $ns ip link set lo up
  ip netns exec $ns ip link set v-$ns up
done
# Full checksums from both hosts: with transmit checksum offload a veth hands
# on a TCP or UDP datagram whose checksum field holds only the pseudo-header,
# to be completed by "hardware" that does not exist here - and a translation
# measured on those would be measured on a number nobody finishes. ethtool is
# not in the image; this is its ETHTOOL_STXCSUM ioctl.
txcsum_off() {  # <netns or -> <ifname>
  _ns=""; [ "$1" != - ] && _ns="ip netns exec $1"
  $_ns python3 -c '
import fcntl, socket, struct, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for cmd in (0x17, 0x1f):   # ETHTOOL_STXCSUM, ETHTOOL_SSG
    ev = struct.pack("II", cmd, 0)
    ifr = struct.pack("16sP", sys.argv[1].encode(), 0)
    buf = bytearray(ev)
    import ctypes
    b = ctypes.create_string_buffer(bytes(buf))
    ifr = struct.pack("16sQ", sys.argv[1].encode(), ctypes.addressof(b))
    try: fcntl.ioctl(s, 0x8946, ifr)
    except OSError as e: print("txcsum_off", sys.argv[1], e)
' "$2"
}
txcsum_off sub v-sub; txcsum_off srv v-srv; txcsum_off - host-sub; txcsum_off - host-srv
ip netns exec sub sysctl -qw net.ipv6.conf.all.accept_dad=0 net.ipv6.conf.v-sub.accept_dad=0
ip netns exec sub ip -6 addr add 2001:db8:1200:ab00::10/56 dev v-sub nodad
ip netns exec sub ip -6 addr add 2001:db8:1200:ab00::11/56 dev v-sub nodad
ip netns exec sub ip -6 route add 64:ff9b:1::/48 via 2001:db8:1200:ab00::1
ip netns exec srv ip addr add 192.0.2.2/24 dev v-srv
ip netns exec srv ip route add 203.0.113.0/24 via 192.0.2.1

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp

# Without the af_packet checksum/GSO offload, for two measured reasons. With
# it, the kernel hands VPP a TCP or UDP datagram whose checksum is only the
# pseudo-header (CHECKSUM_PARTIAL) and completes it again on the way out, so
# a translation that got the checksum wrong would still deliver: the lab
# would measure the veth's offload, not RFC 7915. And with it, every frame
# that carries no checksum request - ICMPv6, a fragment, VPP's own ping6 -
# was refused by sendto on the inside veth ("tx sendto temporary failure")
# and never reached the subscriber.
vppctl create host-interface name host-sub cksum-gso-disable >/dev/null
vppctl create host-interface name host-srv cksum-gso-disable >/dev/null
vppctl set interface state host-host-sub up
vppctl set interface state host-host-srv up
vppctl enable ip6 interface host-host-sub
vppctl set interface ip address host-host-sub 2001:db8:1200:ab00::1/56
vppctl set interface ip address host-host-srv 192.0.2.1/24
vppctl set ip neighbor host-host-srv 192.0.2.2 \
  "$(ip netns exec srv cat /sys/class/net/v-srv/address)"
vppctl set ip neighbor host-host-sub 2001:db8:1200:ab00::10 \
  "$(ip netns exec sub cat /sys/class/net/v-sub/address)"
vppctl set ip neighbor host-host-sub 2001:db8:1200:ab00::11 \
  "$(ip netns exec sub cat /sys/class/net/v-sub/address)"
vppctl set interface bpa in2out host-host-sub
vppctl set interface bpa out2in host-host-srv

echo "=========== 1. the pool ==========="
if [ "$MODE" != off ]; then
  # Through the VTY, so the grammar, the commit check, bpa_nat64_pool_add
  # and the readback are what built it.
  $HX -c "configure" -c "nat" -c "cgnat" -c "pool v6" \
      -c "prefix-inside 2001:db8:1200::/40" -c "subscriber-length 56" \
      -c "nat64-prefix 64:ff9b:1::/48" -c "prefix-outside 203.0.113.0/29" \
      -c "block-size 2048" -c "exit" -c "commit" 2>&1 | sed 's/^/  /'
else
  echo "  off: no NAT64 pool"
fi
vppctl show bpa pool | sed 's/^/  /'
vppctl show bpa config | sed -n '/cgnat pool v6/,/exit/p' | sed 's/^/  /' > /tmp/n64-config.txt
cat /tmp/n64-config.txt
vppctl show interface features host-host-sub 2>/dev/null | grep -iE "bpa|reass" | sed 's/^/  /'

# The block the arithmetic gives subscriber prefix 2001:db8:1200:ab00::/56,
# from the model: the prefix's index in the IPv6 inside set, then the IPv4
# pool's formula over a dense inside of that many subscribers - which is
# what the dataplane reuses (docs/nat64.md).
EXPECT=$(python3 - <<'EOF'
import sys; sys.path.insert(0, "/src/ref")
from bpa_model import AddrSet6, PoolConfig
s = AddrSet6(["2001:db8:1200::/40"], 56)
idx = s.index_of("2001:db8:1200:ab00::10")
p = PoolConfig("v6", ["0.0.0.0/%d" % (32 - (56 - 40))],
               ["203.0.113.0/29"], block_size=2048)
f = p.forward(idx)
print(idx, f["ip_pub"], f["port_lo"], f["port_hi"])
EOF
)
echo "  the model: subscriber index, public address, block: $EXPECT"

server() {  # <proto> <port> : echo back whatever arrives, and record its source
  ip netns exec srv python3 - "$1" "$2" > /tmp/srv-$1-$2.log 2>&1 <<'EOF' &
import socket, sys
proto, port = sys.argv[1], int(sys.argv[2])
if proto == "tcp":
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port)); s.listen(4); s.settimeout(20)
    c, a = s.accept(); data = c.recv(65536); print(a[0], a[1], len(data), flush=True)
    c.sendall(b"pong:" + data); c.close()
else:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("0.0.0.0", port)); s.settimeout(20)
    data, a = s.recvfrom(65536); print(a[0], a[1], len(data), flush=True)
    s.sendto(b"pong:" + data, a)
EOF
  sleep 1
}
client() {  # <proto> <port> <bytes> : from the subscriber, through the box
  ip netns exec sub python3 - "$1" "$2" "$3" <<'EOF'
import socket, sys
proto, port, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
dst = "64:ff9b:1:c000:2:200::"   # 192.0.2.2 under a /48, RFC 6052 layout
data = bytes((i * 7) & 0xff for i in range(n))
if proto == "tcp":
    s = socket.socket(socket.AF_INET6); s.settimeout(10)
    s.bind(("2001:db8:1200:ab00::10", 0)); s.connect((dst, port)); s.sendall(data)
    r = b""
    while True:
        b = s.recv(65536)
        if not b: break
        r += b
else:
    s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM); s.settimeout(10)
    s.bind(("2001:db8:1200:ab00::10", 0)); s.sendto(data, (dst, port)); r, _ = s.recvfrom(65536)
print("ok" if r == b"pong:" + data else "mismatch %d" % len(r))
EOF
}

echo
echo "=========== 2. TCP, through the box both ways ==========="
server tcp 8080
TCP=$(client tcp 8080 300 2>&1); echo "  client: $TCP"; sleep 1
TCPSRV=$(cat /tmp/srv-tcp-8080.log); echo "  server saw: $TCPSRV"

echo
echo "=========== 3. UDP ==========="
server udp 5353
UDP=$(client udp 5353 200 2>&1); echo "  client: $UDP"; sleep 1
UDPSRV=$(cat /tmp/srv-udp-5353.log); echo "  server saw: $UDPSRV"

echo
echo "=========== 4. a fragmented UDP datagram, both ways ==========="
server udp 5454
FRAG=$(client udp 5454 3000 2>&1); echo "  client: $FRAG"; sleep 1
FRAGSRV=$(cat /tmp/srv-udp-5454.log); echo "  server saw: $FRAGSRV"

echo
echo "=========== 5. ICMP echo ==========="
PING=$(ip netns exec sub ping -6 -c 3 -W 2 -I 2001:db8:1200:ab00::10 64:ff9b:1:c000:2:200:: 2>&1 | tail -2)
echo "$PING" | sed 's/^/  /'

echo
echo "=========== 6. an ICMP error from the IPv4 side: port unreachable ==========="
ERR=$(ip netns exec sub python3 - <<'EOF'
import socket
s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM); s.settimeout(4)
s.bind(("2001:db8:1200:ab00::10", 0))
s.connect(("64:ff9b:1:c000:2:200::", 9999))
s.send(b"x")
try:
    s.recv(100); print("no error")
except ConnectionRefusedError:
    print("refused")
except socket.timeout:
    print("timeout")
EOF
)
echo "  the subscriber's socket: $ERR"

echo
echo "=========== 7. UDP with no checksum from the IPv4 side ==========="
# IPv4 allows a zero UDP checksum and IPv6 does not (RFC 7915 4.5): the
# server's socket sends with SO_NO_CHECK, so the reply reaches the box with
# checksum 0, and the subscriber's kernel drops it unless the box computed one.
ip netns exec srv python3 - > /tmp/srv-udp0.log 2>&1 <<'EOF' &
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, 11, 1)          # SO_NO_CHECK
s.bind(("0.0.0.0", 5555)); s.settimeout(20)
data, a = s.recvfrom(65536); print(a[0], a[1], len(data), flush=True)
s.sendto(b"pong:" + data, a)
EOF
sleep 1
UDP0=$(client udp 5555 100 2>&1); echo "  client: $UDP0"

echo
echo "=========== 8. Packet Too Big from the IPv4 side ==========="
# A router on the IPv4 side says 1400 does not fit: the subscriber must learn
# a path MTU of 1420 for the server, the IPv4 figure plus the 20 bytes the
# headers grow by (RFC 7915 4.2).
ip netns exec srv python3 - > /tmp/srv-ptb.log 2>&1 <<'EOF' &
import socket, struct
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.bind(("0.0.0.0", 5757))
u.settimeout(20); data, (pub, port) = u.recvfrom(65536)
def csum(b):
    b += b"\0" * (len(b) & 1); t = sum(struct.unpack("!%dH" % (len(b) // 2), b))
    while t >> 16: t = (t & 0xffff) + (t >> 16)
    return ~t & 0xffff
ulen = 8 + len(data)
iph = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + ulen, 1, 0x4000, 64, 17, 0,
                  socket.inet_aton(pub), socket.inet_aton("192.0.2.2"))
iph = iph[:10] + struct.pack("!H", csum(iph)) + iph[12:]
quoted = iph + struct.pack("!HHHH", port, 5757, ulen, 0)
icmp = struct.pack("!BBHHH", 3, 4, 0, 0, 1400) + quoted
icmp = icmp[:2] + struct.pack("!H", csum(icmp)) + icmp[4:]
r = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
r.sendto(icmp, (pub, 0)); print(pub, port, flush=True)
EOF
sleep 1
PTB=$(ip netns exec sub python3 - <<'EOF'
import socket, time
s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
s.bind(("2001:db8:1200:ab00::10", 0)); s.connect(("64:ff9b:1:c000:2:200::", 5757))
s.send(b"x" * 100); time.sleep(3)
print(s.getsockopt(socket.IPPROTO_IPV6, 24))     # IPV6_MTU: the path MTU
EOF
)
echo "  the subscriber's path MTU to the server: $PTB"

echo
echo "=========== 9. unsolicited, to a NAT64 pool's public address ==========="
# What an IPv4 pool does: no session, so dropped and counted, nothing sent
# back and nothing delivered (the coordinator's answer on #485).
NS0=$(vppctl show errors | awk '/bpa-out2in/ && /no session/ {print $1}')
ip netns exec sub python3 - > /tmp/unsol.log 2>&1 <<'EOF' &
import socket
s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM); s.settimeout(5)
s.bind(("::", 46000))
try:
    s.recvfrom(100); print("delivered")
except socket.timeout:
    print("nothing")
EOF
sleep 1
ip netns exec srv python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.sendto(b'x', ('203.0.113.3', 46000))"
sleep 6
NS1=$(vppctl show errors | awk '/bpa-out2in/ && /no session/ {print $1}')
UNSOL=$(cat /tmp/unsol.log)
echo "  the subscriber: $UNSOL; 'no session' ${NS0:-0} -> ${NS1:-0}"

echo
echo "=========== 10. the VTY: a change in place, and a refusal ==========="
SESS0=$(vppctl show bpa session | awk '/^ *total:/ {print $2}')
if [ "$MODE" != off ]; then
  $HX -c "configure" -c "nat" -c "cgnat" -c "pool v6" -c "timeout udp 600" \
      -c "exit" -c "commit" 2>&1 | sed 's/^/  /'
  REFUSED=$($HX -c "configure" -c "nat" -c "cgnat" -c "pool v6" \
      -c "reserve 2" -c "exit" -c "commit" 2>&1)
  echo "$REFUSED" | sed 's/^/  /'
else
  REFUSED=""
fi
printf '%s\n' "$REFUSED" > /tmp/n64-refused.txt
SESS1=$(vppctl show bpa session | awk '/^ *total:/ {print $2}')
echo "  sessions before $SESS0, after $SESS1"
vppctl show bpa config | sed -n '/cgnat pool v6/,/exit/p' > /tmp/n64-config2.txt

echo
echo "=========== 11. show cgnat: who is behind a public port, and back ==========="
# The court-order question. A NAT64 pool's inside index would print as
# 0.0.0.<n> through the IPv4 path; the answer is the subscriber's prefix.
$HX -c "show cgnat translation 203.0.113.3 44100" > /tmp/n64-xlt.txt 2>&1
$HX -c "show cgnat subscriber 2001:db8:1200:ab00::10" > /tmp/n64-sub.txt 2>&1
$HX -c "show cgnat top" > /tmp/n64-top.txt 2>&1
cat /tmp/n64-xlt.txt /tmp/n64-sub.txt /tmp/n64-top.txt | sed 's/^/  /'

# ======================================================================
# PHASE 3 (#485): the pool turned dynamic, and what it logs.
#
# The pool is cut down to two blocks in all - ports 1024-1151 in blocks of
# 64 on one public address - so that one subscriber can take both (its
# block, then an extension under max-blocks 2) and a second finds the pool
# exhausted. RFC 8158 natEvent 12 is a POOL with no free block, which is
# when this box records it, and it is recorded once per subscriber per
# interval however many packets are refused (#549). Then A's sessions are
# cleared and its blocks come back after the hold-down, through the reap;
# B is given one; and removing the pool drains B's - every allocation with
# its release, each naming the subscriber by its delegated prefix and, for
# A, by the name RADIUS's Delegated-IPv6-Prefix gave it.
#
# Twice: once with VPP carrying the log, once with the log container
# (prisma-logd, #550) carrying it - the dataplane is the fallback and the
# container the normal path, and a record shape that works on one of them
# only is a log that depends on which process happened to be up.
# ======================================================================
# B on A's worker, whatever the worker count: a subscriber's worker is its
# index modulo the workers (bpa_owner_worker), A's index is 171 (0xab), so
# B is 171 + workers. And each worker's slice of a dynamic pool holds two
# blocks: 2 x workers in all. Measured with two workers and a fixed two-block
# pool: one block per slice, A and B on different workers, A's extension
# refused by its empty slice and nothing exhausted - the box right, the lab's
# arithmetic single-threaded.
NW=$(box_workers); [ "$NW" -ge 1 ] || NW=1
B_HEX=$(printf %x $((171 + NW)))
B_PFX="2001:db8:1200:${B_HEX}00::/56"
B_ADDR="2001:db8:1200:${B_HEX}00::10"
PORT_HI=$((1024 + 128 * NW - 1))
echo "  workers $NW: B is $B_PFX, the pool ports 1024-$PORT_HI"
ip netns exec sub ip -6 addr add $B_ADDR/56 dev v-sub nodad
# The log container's collectors, reached the way the appliance's logger
# reaches them: through the host's own stack. 192.0.2.2 is behind VPP, which
# is where the dataplane sends from and the logger cannot - measured, every
# record the logger sent there was lost.
ip netns add logc 2>/dev/null
ip link add v-logc type veth peer name h-logc 2>/dev/null
ip link set v-logc netns logc
ip addr add 10.99.0.1/24 dev h-logc 2>/dev/null
ip link set h-logc up
ip netns exec logc ip addr add 10.99.0.2/24 dev v-logc
ip netns exec logc ip link set v-logc up
ip netns exec logc ip link set lo up
vppctl ip route add $B_PFX via 2001:db8:1200:ab00::10 host-host-sub >/dev/null

flows() {  # <source> <first port> <count>: one UDP datagram per source port
  ip netns exec sub python3 - "$@" <<'PY'
import socket, sys
src, first, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
for p in range(first, first + n):
    s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    s.bind((src, p))
    s.sendto(b"x", ("64:ff9b:1:c000:2:200::", 7000))
    s.close()
PY
}

cfg_dyn() {  # <collector> <source> <the pool's own lines>
  _col=$1 _src=$2; shift 2
  $HX -c "configure" -c "nat" -c "cgnat" \
      -c "logging udp $_col src $_src port 4739" \
      -c "logging enable" \
      -c "logging syslog 10.99.0.2 enterprise 44444 port 601" \
      -c "identity radius secret lab-s3cr3t port 1813" \
      -c "pool v6" "$@" -c "prefix-outside 203.0.113.3/32" \
      -c "block-size 64" -c "ports 1024 $PORT_HI" -c "mode dynamic" \
      -c "max-blocks 2" -c "exit" -c "commit remap"
}

dyn_phase() {  # <A|B> <netns> <IPFIX collector> <source>: one cycle
  _p=$1 _ns=$2 _col=$3 _src=$4
  rm -f /tmp/n64-ipfix-$_p.stop
  ip netns exec $_ns python3 /src/tools/ipfix_collector.py 4739 120 \
    /tmp/n64-ipfix-$_p.stop > /tmp/n64-ipfix-$_p.out 2>&1 &
  _ipfix=$!
  # Syslog in logc in both phases: the TCP streams leave through a Linux
  # socket whichever process carries them, VPP's included - measured, the
  # dataplane's syslog to 192.0.2.2 (behind VPP) went to its spill and was
  # replayed to the next collector configured. Only UDP IPFIX goes out
  # through VPP's own stack.
  ip netns exec logc python3 /src/tools/syslog_collector.py 601 45 \
    /tmp/n64-sys-$_p.rep /tmp/n64-sys-$_p.lines > /dev/null 2>&1 &
  _sys=$!
  sleep 2
  if [ "$MODE" != off ]; then
    # A turns the deterministic pool of sections 1-12 dynamic; B defines
    # it again, A's drain having removed it.
    if [ $_p = A ]; then
      cfg_dyn $_col $_src -c "no prefix-outside 203.0.113.0/29"
    else
      cfg_dyn $_col $_src -c "prefix-inside 2001:db8:1200::/40" \
              -c "subscriber-length 56" \
              -c "nat64-prefix 64:ff9b:1::/48"
    fi 2>&1 | grep -E "error|refus|complete" | sed 's/^/  /'
    vppctl bpa block-holddown 4 >/dev/null
    sleep 1
    python3 /src/tools/radius_nas.py 127.0.0.1 1813 lab-s3cr3t start \
      2001:db8:1200:ab00::/56 ana@isp 2>&1 | sed 's/^/  radius: /'
  fi
  vppctl show bpa logging | tr -d '\r' | grep -m1 'TCP streams carried by' \
    > /tmp/n64-carrier-$_p.txt
  sed 's/^/  /' /tmp/n64-carrier-$_p.txt
  flows 2001:db8:1200:ab00::10 20000 130          # A: a block, an extension
  flows $B_ADDR 20000 40                           # B: its slice is empty
  sleep 2
  # A's first block, from the box: which port of the pool it got depends on
  # which worker's slice it came from.
  A_LO=$($HX -c "show cgnat subscriber 2001:db8:1200:ab00::10" 2>&1 \
         | sed -n 's/.*block 0 .* ports \([0-9]*\)-.*/\1/p' | head -1)
  echo "$A_LO" > /tmp/n64-alo-$_p.txt
  $HX -c "show cgnat translation 203.0.113.3 ${A_LO:-1024}" \
    > /tmp/n64-dxlt-$_p.txt 2>&1
  sed 's/^/  /' /tmp/n64-dxlt-$_p.txt
  vppctl show bpa drops 2>/dev/null | tr -d '\r' | grep -iE "block" | sed 's/^/  /'
  $HX -c "clear cgnat subscriber 2001:db8:1200:ab00::10" 2>&1 | sed 's/^/  /'
  sleep 8                                         # the hold-down, then the reap
  flows $B_ADDR 21000 5                            # B, now there is a block
  sleep 2
  if [ "$MODE" != off ]; then
    $HX -c "configure" -c "nat" -c "cgnat" -c "no pool v6" -c "commit" \
      2>&1 | grep -E "error" | sed 's/^/  /'
  fi
  sleep 3
  touch /tmp/n64-ipfix-$_p.stop
  wait $_ipfix 2>/dev/null
  wait $_sys 2>/dev/null
  grep -E "ALLOCATION|EXHAUSTION|RESUMO" /tmp/n64-ipfix-$_p.out | sed 's/^/  ipfix: /'
  grep -E "2001:db8" /tmp/n64-sys-$_p.lines 2>/dev/null | cut -c1-220 \
    | sed 's/^/  syslog: /'
}

n64_count() {  # <phase> <event word> <subscriber>
  grep -E "^ +[0-9:]+ $2 +$3 " /tmp/n64-ipfix-$1.out | grep -vc reprocessed
}

echo
echo "=========== 13. dynamic, logged - VPP carries the log ==========="
dyn_phase A srv 192.0.2.2 192.0.2.1

echo
echo "=========== 14. dynamic, logged - the log container carries it ==========="
LOGD=
if [ "$MODE" != off ]; then
  cc -O2 -Wall -Wextra -Werror -DBPA_STANDALONE -I /src/test -I /src/src/plugins/bpa \
     -o /tmp/prisma-logd /src/src/logger/prisma_logd.c \
     /src/src/plugins/bpa/bpa_log_syslog.c /src/src/plugins/bpa/bpa_identity.c -lrt
  /tmp/prisma-logd > /tmp/logd.log 2>&1 &
  LOGD=$!
  sleep 3
fi
dyn_phase B logc 10.99.0.2 10.99.0.1
[ -n "$LOGD" ] && kill $LOGD 2>/dev/null

echo
echo "=========== 12. the box ==========="
vppctl show bpa session | sed 's/^/  /' | head -20
vppctl show errors | grep -iE "nat64|bpa-out2in" | sed 's/^/  /'

PUB=$(echo "$TCPSRV" | awk '{print $1}'); PORT=$(echo "$TCPSRV" | awk '{print $2}')
expect "TCP crossed both ways, payload intact" test "$TCP" = ok
expect "UDP crossed both ways, payload intact" test "$UDP" = ok
expect "a 3000-byte UDP datagram crossed fragmented, both ways" test "$FRAG" = ok
expect "ping through the translator: 3 replies" sh -c "echo '$PING' | grep -q '3 received'"
expect "a port unreachable from the IPv4 server reached the IPv6 socket" test "$ERR" = refused
expect "a zero-checksum UDP reply was given a checksum and delivered" test "$UDP0" = ok
expect "Packet Too Big 1400 reached the subscriber as a path MTU of 1420" test "$PTB" = 1420
set -- $EXPECT
expect "the server saw the model's public address ($2 = $PUB)" test "$PUB" = "$2"
for f in "$TCPSRV" "$UDPSRV" "$FRAGSRV"; do
  p=$(echo "$f" | cut -d' ' -f2)
  expect "a flow's port is in the model's block ($3-$4: $p)" \
    sh -c "[ '$p' -ge $3 ] && [ '$p' -le $4 ]"
done
expect "an unsolicited datagram was dropped, counted, not delivered" \
  sh -c "[ '$UNSOL' = nothing ] && [ '${NS1:-0}' -gt '${NS0:-0}' ]"
expect "a timeout changed in place: the pool was not rebuilt" \
  sh -c "[ -n '$SESS1' ] && [ '$SESS1' -gt 0 ] && [ '$SESS1' = '$SESS0' ] && grep -q 'timeout udp 600' /tmp/n64-config2.txt"
expect "the VTY refuses what a NAT64 pool does not take yet: reserve" \
  grep -q "a NAT64 pool takes no reserve yet" /tmp/n64-refused.txt
expect "show cgnat translation names the subscriber's IPv6 prefix" \
  grep -q "203.0.113.3:44100  ->  2001:db8:1200:ab00::/56" /tmp/n64-xlt.txt
expect "  and says it is the formula's answer, for any instant" \
  sh -c "grep -q ', DETERMINISTIC NAT64' /tmp/n64-xlt.txt && grep -q \"from the FORMULA. It holds for any instant, past or present, as long as this pool's prefixes and block size have not changed since. No allocation record is involved.\" /tmp/n64-xlt.txt"
expect "show cgnat subscriber takes an IPv6 address and gives the block" \
  grep -q "the formula gives it 203.0.113.3 ports 44032-46079" /tmp/n64-sub.txt
expect "show cgnat top names the subscriber by its prefix" \
  grep -q "^2001:db8:1200:ab00::/56 " /tmp/n64-top.txt
for _p in A B; do
  [ $_p = A ] && _who="VPP" || _who="the log container"
  expect "$_who: IPFIX names A by its prefix, block and extension (2 allocations)" \
    test "$(n64_count $_p ALLOCATION 2001:db8:1200:ab00::/56)" = 2
  expect "$_who: the pool's exhaustion recorded once for B's 40 refused flows" \
    test "$(n64_count $_p EXHAUSTION $B_PFX)" = 1
  expect "$_who: A's two blocks released after the hold-down, in the log" \
    test "$(n64_count $_p DEALLOCATION 2001:db8:1200:ab00::/56)" = 2
  expect "$_who: B's block allocated, and released by the pool's drain" \
    sh -c "[ \$(grep -cE ' ALLOCATION +$B_PFX ' /tmp/n64-ipfix-$_p.out) = 1 ] && [ \$(grep -cE ' DEALLOCATION +$B_PFX ' /tmp/n64-ipfix-$_p.out) = 1 ]"
  expect "$_who: syslog names A by prefix and by RADIUS's ana@isp" \
    grep -q 'subscriber="2001:db8:1200:ab00::/56" .*subscriberId="ana@isp"' \
      /tmp/n64-sys-$_p.lines
  expect "$_who: translation answers a dynamic port with A's prefix, now" \
    sh -c "grep -q \"203.0.113.3:\$(cat /tmp/n64-alo-$_p.txt)  ->  2001:db8:1200:ab00::/56\" /tmp/n64-dxlt-$_p.txt && grep -q 'DYNAMIC' /tmp/n64-dxlt-$_p.txt"
done
expect "the log was carried by the dataplane, then by the log container" \
  sh -c "grep -q 'carried by the dataplane' /tmp/n64-carrier-A.txt && grep -q 'carried by the log container' /tmp/n64-carrier-B.txt"
expect "show bpa config gives the pool back as configured" \
  sh -c "grep -q 'inside 2001:db8:1200::/40' /tmp/n64-config.txt && grep -q 'subscriber-length 56' /tmp/n64-config.txt && grep -q 'nat64-prefix 64:ff9b:1::/48' /tmp/n64-config.txt"
verdict
