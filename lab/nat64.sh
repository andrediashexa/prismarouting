#!/bin/sh
# OFF-RED-AT-LEAST: 12
# (the floor: what the off run of 2026-10-04 measured red, every check)
# NAT64, deterministic: an IPv6-only subscriber reaches an IPv4 server (#485).
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
  vppctl bpa pool v6 inside 2001:db8:1200::/40 subscriber-length 56 \
    nat64-prefix 64:ff9b:1::/48 outside 203.0.113.0/29 block 2048 2>&1 \
    | sed 's/^/  /'
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
echo "=========== 9. the box ==========="
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
expect "show bpa config gives the pool back as configured" \
  sh -c "grep -q 'inside 2001:db8:1200::/40' /tmp/n64-config.txt && grep -q 'subscriber-length 56' /tmp/n64-config.txt && grep -q 'nat64-prefix 64:ff9b:1::/48' /tmp/n64-config.txt"
verdict
