#!/bin/sh
# OFF-RED-AT-LEAST: 8
# (the floor: what the off run of 2026-10-04 measured red, 8 and then 9:
# "every one carried PREF64" is red off only when VPP's own advertisement
# lands inside the 10 s window, and with none heard it holds vacuously)
# 464XLAT: an IPv4-only application behind a CLAT that learned the NAT64
# prefix from the box's router advertisement (#485, phase 6, RFC 8781).
#
#   sh tools/lab.sh pref64        -> LAB PASSED
#   sh tools/lab.sh pref64 off    -> LAB FAILED (no `ipv6 nd nat64`: the
#                                    advertisement carries no prefix, the CLAT
#                                    has nothing to translate to)
#
#   sub (netns)  IPv6 only on v-sub: 2001:db8:1200:ab00::10/56, and tayga as
#                the CLAT, IPv4 192.0.0.1 on its TUN (RFC 7335)
#     ... veth ... host-sub  VPP inside, paired into Linux as vppsub0
#   srv (netns)  192.0.2.2/24 ...... host-srv  VPP outside
#
# What makes it 464XLAT and not a NAT64 lab with extra steps: the
# application in `sub` opens an AF_INET socket to 192.0.2.2 and nothing on
# that side is told the NAT64 prefix. The CLAT's prefix is read off the
# router advertisements the subscriber hears - option 38, PREF64 - the way a
# CLAT reads it, and tayga is started with what was found. The prefix is
# 2001:db8:64::/48, not the well-known one, so a CLAT falling back to
# 64:ff9b::/96 cannot pass by accident - and not RFC 8215's local-use
# 64:ff9b:1::/48 either, which lab/nat64.sh uses: tayga 0.9.2 refuses every
# prefix in ::/8 but the well-known one ("Cannot use reserved address
# 64:ff9b:1::/48 in prefix directive"), measured.
#
# The advertisement is FRR's (VPP 26.06's carries no PREF64), out through
# the linux_cp pair, and VPP's own is kept off on that interface: every
# advertisement heard is asked to carry the prefix, so a second router
# speaking for the same link without it is a red check, not a quiet one.
set -u
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing \
         /var/run/frr /etc/frr
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
. /src/lab/verdict.sh
HX="python3 /src/src/prismash/prismash.py"

# Every RA heard for <seconds>: the PREF64 each carries, or "none". A CLAT
# reads exactly this - RFC 8781 3. A router solicitation goes first, as a
# host coming up sends one.
hear_ras() {  # <seconds> <file>
  ip netns exec sub python3 - "$1" > "$2" 2>&1 <<'EOF'
import socket, struct, sys, time
s = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_ICMPV6)
s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 255)
s.sendto(bytes([133, 0, 0, 0, 0, 0, 0, 0]), ("ff02::2", 0, 0,
         socket.if_nametoindex("v-sub")))
end = time.time() + float(sys.argv[1])
while time.time() < end:
    s.settimeout(max(0.1, end - time.time()))
    try:
        d, a = s.recvfrom(2000)
    except socket.timeout:
        break
    if d[0] != 134:
        continue
    o, found = 16, "none"
    while o + 2 <= len(d) and d[o + 1]:
        t, l = d[o], d[o + 1] * 8
        if t == 38 and l == 16:
            sl = struct.unpack("!H", d[o + 2:o + 4])[0]
            plen = {0: 96, 1: 64, 2: 56, 3: 48, 4: 40, 5: 32}.get(sl & 7)
            if plen:
                net = socket.inet_ntop(socket.AF_INET6,
                                       d[o + 4:o + 16] + bytes(4))
                found = f"{net}/{plen} lifetime {(sl >> 3) * 8}"
        o += l
    print(f"RA {a[0]} pref64 {found}")
EOF
}

# tayga is the translator clatd drives; Debian ships it.
apt-get install -y -qq tayga >/tmp/apt-tayga.log 2>&1
expect_setup "tayga is installed (the CLAT's translator)" command -v tayga

for ns in sub srv; do
  ip netns add $ns 2>/dev/null
  ip link add v-$ns type veth peer name host-$ns
  ip link set v-$ns netns $ns
  ip netns exec $ns ip link set lo up
  ip netns exec $ns ip link set v-$ns up
  ip link set host-$ns up
done
# Without the veths' checksum offload, for lab/nat64.sh's reason: with it a
# TCP segment leaves carrying only the pseudo-header sum, and the lab would
# measure the veth instead of the translations. Measured here too: ICMP
# crossed and the TCP connection timed out until this was added.
txcsum_off() {  # <netns or -> <ifname>
  _ns=""; [ "$1" != - ] && _ns="ip netns exec $1"
  $_ns python3 -c '
import ctypes, fcntl, socket, struct, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for cmd in (0x17, 0x1f):   # ETHTOOL_STXCSUM, ETHTOOL_SSG
    b = ctypes.create_string_buffer(struct.pack("II", cmd, 0))
    ifr = struct.pack("16sQ", sys.argv[1].encode(), ctypes.addressof(b))
    try: fcntl.ioctl(s, 0x8946, ifr)
    except OSError as e: print("txcsum_off", sys.argv[1], e)
' "$2"
}
txcsum_off sub v-sub; txcsum_off srv v-srv; txcsum_off - host-sub; txcsum_off - host-srv
# Forwarding, because the CLAT's IPv6 crosses the subscriber's own kernel
# between tayga's TUN and the wire; accept_ra 2 so a forwarding host still
# takes its default route from the advertisement, as a CPE does.
ip netns exec sub sysctl -qw net.ipv6.conf.all.forwarding=1 \
  net.ipv6.conf.v-sub.accept_ra=2 net.ipv6.conf.all.accept_dad=0 \
  net.ipv6.conf.v-sub.accept_dad=0
ip netns exec sub ip -6 addr add 2001:db8:1200:ab00::10/56 dev v-sub nodad
ip netns exec srv ip addr add 192.0.2.2/24 dev v-srv
ip netns exec srv ip route add 203.0.113.0/24 via 192.0.2.1

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
wait_vpp
use_binary_api
vppctl create host-interface name host-sub cksum-gso-disable >/dev/null
vppctl create host-interface name host-srv cksum-gso-disable >/dev/null
vppctl set interface state host-host-sub up
vppctl set interface state host-host-srv up
vppctl set interface ip address host-host-srv 192.0.2.1/24
vppctl set ip neighbor host-host-srv 192.0.2.2 \
  "$(ip netns exec srv cat /sys/class/net/v-srv/address)"
vppctl set interface bpa in2out host-host-sub
vppctl set interface bpa out2in host-host-srv

printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr
start_frr

echo "=========== 1. the pool, the pair and the advertisement ==========="
# As an operator types it. Off: the same, without `ipv6 nd nat64`.
P64=yes
[ "$MODE" = off ] && P64=""
$HX -c "configure" -c "nat" -c "cgnat" -c "pool v6" \
    -c "prefix-inside 2001:db8:1200::/40" -c "subscriber-length 56" \
    -c "nat64-prefix 2001:db8:64::/48" -c "prefix-outside 203.0.113.0/29" \
    -c "block-size 2048" -c "exit" -c "exit" -c "exit" \
    -c "interface host-host-sub" -c "linux-name vppsub0" \
    -c "ipv6 address 2001:db8:1200:ab00::1/56" \
    -c "no ipv6 nd suppress-ra" \
    ${P64:+"-c" "ipv6 nd nat64 2001:db8:64::/48"} -c "exit" \
    -c "interface vppsub0" -c "ipv6 nd ra-interval 3" -c "exit" \
    -c "commit" 2>&1 | sed 's/^/  /'
$HX -c "show running-config" > /tmp/p64-running.txt 2>&1
sed -n '/^interface host-host-sub/,/exit/p' /tmp/p64-running.txt | sed 's/^/  /'
vtysh -c "show running-config" | sed -n '/^interface vppsub0/,/^exit/p' \
  > /tmp/p64-frr.txt
sed 's/^/  frr: /' /tmp/p64-frr.txt
# The subscriber's reply path, as lab/nat64.sh sets it: VPP resolves the
# CLAT's address by neighbour discovery the CLAT does not answer.
vppctl set ip neighbor host-host-sub 2001:db8:1200:ab00::c1a7 \
  "$(ip netns exec sub cat /sys/class/net/v-sub/address)"

echo "=========== 2. the advertisements the subscriber hears ==========="
hear_ras 10 /tmp/p64-ras.txt
sed 's/^/  /' /tmp/p64-ras.txt
N_RA=$(grep -c "^RA " /tmp/p64-ras.txt)
N_RA_NONE=$(grep -c "pref64 none" /tmp/p64-ras.txt)
LEARNED=$(sed -n 's/^RA .* pref64 \([0-9a-f:]*\/[0-9]*\) .*/\1/p' /tmp/p64-ras.txt | head -1)
echo "  heard $N_RA, $N_RA_NONE without a prefix; the CLAT learns: ${LEARNED:-nothing}"

echo "=========== 3. tayga, started with what the advertisement said ==========="
mkdir -p /var/lib/tayga
if [ -n "$LEARNED" ]; then
  cat > /tmp/tayga.conf <<EOF
tun-device clat
ipv4-addr 192.0.0.2
prefix $LEARNED
map 192.0.0.1 2001:db8:1200:ab00::c1a7
data-dir /var/lib/tayga
EOF
  [ -c /dev/net/tun ] || { mkdir -p /dev/net; mknod /dev/net/tun c 10 200; }
  ip netns exec sub tayga -c /tmp/tayga.conf --mktun > /tmp/tayga.log 2>&1
  sed 's/^/  tayga: /' /tmp/tayga.log
  ip netns exec sub ip link set clat up
  ip netns exec sub ip addr add 192.0.0.1/29 dev clat
  # The IPv4 route 20 under the uplink, the TUN at the uplink's own, as
  # clatd sets them: IPv4 becomes IPv6 20 bytes longer. Measured both wrong
  # ways: with no route MTU the server's first full-size segment became a
  # 1520-byte IPv6 frame on a 1500 link and never arrived; with the TUN
  # itself at 1480 the 1500-byte IPv6 segments arrived on v-sub and the
  # kernel would not forward them into the TUN.
  ip netns exec sub ip route add default dev clat mtu 1480
  ip netns exec sub ip -6 route add 2001:db8:1200:ab00::c1a7/128 dev clat
  ip netns exec sub tayga -c /tmp/tayga.conf -d >> /tmp/tayga.log 2>&1 &
  sleep 1
else
  echo "  no prefix heard: no CLAT"
fi

echo "=========== 4. IPv4 from the subscriber, over IPv6, to an IPv4 server ==="
ip netns exec srv python3 - > /tmp/p64-srv.log 2>&1 <<'EOF' &
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("192.0.2.2", 8080)); s.listen(4); s.settimeout(25)
try:
    c, a = s.accept()
    print("peer", a[0], a[1], flush=True)
    c.sendall(b"hello over 464xlat\n" * 1000)
    c.close()
except socket.timeout:
    print("no connection", flush=True)
EOF
sleep 1
PING=$(ip netns exec sub ping -4 -c 3 -W 2 192.0.2.2 2>&1 | tail -2)
echo "  $PING"
FETCH=$(ip netns exec sub timeout 15 python3 - 2>&1 <<'EOF'
import socket
s = socket.create_connection(("192.0.2.2", 8080), timeout=10)
print("family", s.family.name, "bytes", len(s.makefile("rb").read()))
EOF
)
echo "  $FETCH"
sleep 1
PEER=$(sed -n 's/^peer \([0-9.]*\) .*/\1/p' /tmp/p64-srv.log)
echo "  the server saw: ${PEER:-nobody}"

echo "=========== 5. refused, read back, and taken away ==========="
REFUSED=$($HX -c "configure" -c "interface host-host-sub" \
               -c "ipv6 nd nat64 64:ff9b::/96" -c "exit" -c "commit" 2>&1)
echo "$REFUSED" | sed 's/^/  /'
ROUTING=$(sed -n '/^! routing/,$p' /tmp/p64-running.txt)
if [ "$MODE" != off ]; then
  $HX -c "configure" -c "interface host-host-sub" -c "no ipv6 nd nat64" \
      -c "exit" -c "commit" 2>&1 | sed 's/^/  /'
fi
vtysh -c "show running-config" | sed -n '/^interface vppsub0/,/^exit/p' \
  > /tmp/p64-frr2.txt
sed 's/^/  frr after: /' /tmp/p64-frr2.txt
# VPP takes the advertisement back. Its interval is 200 s, so it is asked
# with the solicitation hear_ras sends.
$HX -c "show running-config" > /tmp/p64-running2.txt 2>&1
sed -n '/^interface host-host-sub/,/exit/p' /tmp/p64-running2.txt | sed 's/^/  after: /'
vc show ip6 interface host-host-sub | sed 's/^/  vpp: /' | head -30
hear_ras 20 /tmp/p64-ras2.txt
sed 's/^/  after: /' /tmp/p64-ras2.txt
N_RA2=$(grep -c "^RA " /tmp/p64-ras2.txt)
N_RA2_P=$(grep -c "pref64 [0-9a-f]" /tmp/p64-ras2.txt)

expect_in "show running-config: the statement under the interface" \
  "ipv6 nd nat64 2001:db8:64::/48" \
  "$(sed -n '/^interface host-host-sub/,/exit/p' /tmp/p64-running.txt)"
expect_not_in "and not in the routing an operator typed" "nat64" "$ROUTING"
expect_in "FRR advertises on the pair's Linux end" "no ipv6 nd suppress-ra" \
  "$(cat /tmp/p64-frr.txt)"
expect_in "and carries the pool's prefix" "ipv6 nd nat64 2001:db8:64::/48" \
  "$(cat /tmp/p64-frr.txt)"
expect "the subscriber heard advertisements ($N_RA)" test "${N_RA:-0}" -ge 2
expect "every one carried PREF64: one router speaks for the link ($N_RA_NONE without)" \
  test "${N_RA_NONE:-1}" = 0
expect "the CLAT learned 2001:db8:64::/48 from them (${LEARNED:-nothing})" \
  test "$LEARNED" = "2001:db8:64::/48"
expect_in "an IPv4 ping crosses: the CLAT, then the NAT64 pool" \
  " 0% packet loss" "$PING"
expect_in "an AF_INET TCP connection fetches the whole reply" \
  "family AF_INET bytes 19000" "$FETCH"
expect "the IPv4 server saw the pool's public address (${PEER:-nobody})" \
  sh -c "echo '$PEER' | grep -q '^203\.0\.113\.'"
expect_in "a prefix no NAT64 pool translates is refused at commit" \
  "no NAT64 pool translates it" "$REFUSED"
expect_not_in "no ipv6 nd nat64 takes it out of FRR" "nat64" "$(cat /tmp/p64-frr2.txt)"
expect "and the interface still advertises, without it ($N_RA2 heard, $N_RA2_P with)" \
  test "${N_RA2:-0}" -ge 1 -a "${N_RA2_P:-1}" = 0
verdict
