#!/bin/sh
# Two boxes, an IPv6-only subscriber, a NAT64 pool, three failures (#485).
# OFF-RED-AT-LEAST: 21
#
#   sh tools/lab.sh ha-nat64          -> LAB PASSED
#   sh tools/lab.sh ha-nat64 off      -> LAB FAILED (the pair has no IPv6
#                                        virtual router: the box before #485)
#
# lab/ha-failover.sh is this lab for an IPv4 subscriber, and its header is the
# rule this file follows: the lab MAY build the LANs, start both boxes, give
# each its own addresses, configure the same pool on both and cause the
# failure; it MUST NOT put the gateway address on a box, move it, answer
# Neighbor Discovery for it, flush the subscriber's neighbor cache, or decide
# which box is active. Nothing below touches the subscriber's neighbor cache,
# at any point: a CPE holds its gateway's entry across a failover exactly as
# this namespace does, and a lab that flushed it would pass a product that
# never sends an unsolicited Neighbor Advertisement.
#
#              br-in                                        br-out
#   [sub] --------+--- box a  2001:db8:1200:ab00::2  ---+--- [net]
#   2001:db8:     |           192.0.2.11                 |   192.0.2.2
#   1200:ab00::10 +--- box b  2001:db8:1200:ab00::3  ---+
#                             192.0.2.12
#
#   the gateway, which nothing owns:   fe80::1 and 2001:db8:1200:ab00::1
#                                      (vrrp 1, IPv6 - RFC 5798 5.2.9: the
#                                      link-local first)
#   the pool's next hop upstream:      192.0.2.10 (vrrp 2, IPv4)
#
# The subscriber routes the NAT64 prefix to fe80::1, the virtual router's
# link-local address, which is what RFC 5798 has a host pointed at. The
# upstream routes the pool's public addresses to 192.0.2.10. Each box tracks
# the other side's port, so a box whose uplink is gone drops below its peer
# on BOTH sides and gives up the IPv6 gateway with the IPv4 next hop: they are
# one `vrrp <vrid>`'s priority and tracking on each side, which is how the
# IPv6 gateway follows the master that holds the public addresses.
#
# WHAT IS MEASURED, and written into docs/nat64.md and docs/ha.md: how long
# each failure takes from the moment it is caused to the first translated
# round trip through the new master, and what happens to sessions that were
# open - a UDP flow from a fixed socket and a TCP connection held across the
# kill. The pool is deterministic, so the claim under test is the IPv4 one
# of docs/ha.md: connections drop, the subscriber's public address and port
# block do not.
#
# OFF gives both boxes only the IPv4 router on the upstream side - the pair
# this box could build before #485, where `vrrp 1 address fe80::1` was
# refused. The subscriber's gateway is then nobody's, and every check about
# the service goes red, as does the one that says commit check stopped
# warning about the NAT64 pool.
set -u
. /src/lab/lib.sh      # startup_conf, use_binary_api
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

MODE=${1:-on}
FLOW=/src/lab/haflow.py
SUB=2001:db8:1200:ab00::10      # the subscriber, in its delegated /56
GW_LL=fe80::1                   # the virtual router's link-local
GW_GUA=2001:db8:1200:ab00::1    # and its global address
VMAC6=00:00:5e:00:02:01         # RFC 5798 section 7.3, IPv6, VRID 1
VIP_OUT=192.0.2.10
UPSTREAM=192.0.2.2
NAT64_PFX=64:ff9b:1::/48
DST=64:ff9b:1:c000:2:200::      # 192.0.2.2 under the /48, RFC 6052 layout
UDP_PORT=9999
TCP_PORT=8888
TAKEOVER_WINDOW=20

# ---------------------------------------------------------------- the two LANs
ip netns add sub 2>/dev/null; ip netns add net 2>/dev/null
ip netns add a 2>/dev/null;   ip netns add b 2>/dev/null
ip link add br-in type bridge 2>/dev/null; ip link add br-out type bridge 2>/dev/null
ip link set br-in up; ip link set br-out up
leg() {   # <bridge-side name> <namespace-side name> <namespace> <bridge>
  ip link add "$1" type veth peer name "$2" 2>/dev/null
  ip link set "$2" netns "$3"
  ip netns exec "$3" ip link set lo up
  ip netns exec "$3" ip link set "$2" up
  ip link set "$1" up
  ip link set "$1" master "$4"
}
leg sub-br sub0 sub br-in
leg net-br net0 net br-out
leg a-in-br a-in a br-in;   leg a-out-br a-out a br-out
leg b-in-br b-in b br-in;   leg b-out-br b-out b br-out
# The subscriber takes no router advertisement: its route to the NAT64
# prefix is the one written below, so nothing but the virtual router can
# be its gateway.
ip netns exec sub sysctl -qw net.ipv6.conf.all.accept_dad=0 \
  net.ipv6.conf.sub0.accept_dad=0 net.ipv6.conf.sub0.accept_ra=0
ip netns exec sub ip -6 addr add "$SUB/56" dev sub0 nodad
ip netns exec net ip addr add "$UPSTREAM/24" dev net0
# Full checksums from every sender, for the reason lab/nat64.sh measured:
# with transmit offload a veth hands on a TCP or UDP checksum holding only
# the pseudo-header, and a translation measured on that is measured on a
# number nobody finishes. ethtool is not in the image; this is its
# ETHTOOL_STXCSUM and ETHTOOL_SSG ioctls.
txcsum_off() {  # <netns> <ifname>
  ip netns exec "$1" python3 -c '
import ctypes, fcntl, socket, struct, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for cmd in (0x17, 0x1f):
    b = ctypes.create_string_buffer(struct.pack("II", cmd, 0))
    ifr = struct.pack("16sQ", sys.argv[1].encode(), ctypes.addressof(b))
    try: fcntl.ioctl(s, 0x8946, ifr)
    except OSError as e: print("txcsum_off", sys.argv[1], e)
' "$2"
}
txcsum_off sub sub0; txcsum_off net net0
for _b in a b; do txcsum_off $_b $_b-in; txcsum_off $_b $_b-out; done

# ------------------------------------------------------------------- two boxes
startup_for() {   # <box> <api prefix> -> the generated startup file
  mkdir -p "/run/vpp/$1"
  sed -e "s#cli-listen /run/vpp/cli.sock#runtime-dir /run/vpp/$1\n  cli-listen /run/vpp/$1/cli.sock#" \
      -e "s#^api-segment {#api-segment {\n  prefix $2#" \
      "$(startup_conf test)" > "/tmp/startup-$1.conf"
  echo "/tmp/startup-$1.conf"
}
vb() { _box=$1; shift; vppctl -s "/run/vpp/$_box/cli.sock" "$@" | tr -d '\r'; }
start_box() {   # <box> <api prefix>
  rm -f "/dev/shm/$2-global_vm" "/dev/shm/$2-vpe-api"
  ip netns exec "$1" vpp -c "$(startup_for "$1" "$2")" > "/tmp/vpp-$1.log" 2>&1 &
  eval "VPP_PID_$1=$!"
  _n=0
  until vppctl -s "/run/vpp/$1/cli.sock" show version >/dev/null 2>&1 \
        || [ "$_n" -ge 30 ]; do sleep 1; _n=$((_n + 1)); done
  vppctl -s "/run/vpp/$1/cli.sock" show version >/dev/null 2>&1 || {
    echo "VPP did not start"; tail -20 "/tmp/vpp-$1.log"; exit 1; }
}
# The VTY of one box: its own CLI socket, its own API socket - an IPv6
# virtual router is built over vpp_papi, which vat2 cannot carry - its own
# saved file and rollback directory.
hx() {   # <box> <prismash arguments...>
  _box=$1; shift
  VPPCTL=/usr/local/bin/vppctl-$_box VAT2=/usr/local/bin/vat2-$_box \
  PRISMA_API_SOCKET=/run/vpp/$_box/api.sock \
  PRISMA_API_SEGMENT=/dev/shm/hx$_box-global_vm \
  PRISMA_CONFIG=/etc/prisma-routing/$_box.conf \
  PRISMA_ROLLBACK=/tmp/rollback-$_box \
  python3 /src/src/prismash/prismash.py "$@"
}
for _b in a b; do
  printf '#!/bin/sh\nexec vppctl -s /run/vpp/%s/cli.sock "$@"\n' "$_b" \
    > "/usr/local/bin/vppctl-$_b"
  printf '#!/bin/sh\nexec vat2 -s hx%s "$@"\n' "$_b" > "/usr/local/bin/vat2-$_b"
  chmod +x "/usr/local/bin/vppctl-$_b" "/usr/local/bin/vat2-$_b"
done

# Each box's own addresses and the CGNAT roles, then the NAT64 pool through
# the VTY - the grammar, commit check and bpa_nat64_pool_add, as
# lab/nat64.sh builds it - the same on both, which is what makes the
# subscriber's block a function of the configuration (docs/ha.md).
#
# THE INSIDE HAS AN IPv4 ADDRESS TOO, and that is a requirement this lab
# found rather than a convenience: VPP counts a tracked interface as up for
# the tracking router's FAMILY, so the IPv4 router upstream, tracking an
# IPv6-only subscriber side, would see it down for good. commit check warns
# about exactly that (config.vrrp_track_findings); a box carrying NAT44 and
# NAT64 subscribers on one port, the common case, has the address anyway.
configure_box() {   # <box> <inside IPv6/len> <outside IPv4/len> <inside IPv4/len>
  vb "$1" create host-interface name "$1-in" cksum-gso-disable >/dev/null
  vb "$1" create host-interface name "$1-out" cksum-gso-disable >/dev/null
  vb "$1" set interface state "host-$1-in" up
  vb "$1" set interface state "host-$1-out" up
  vb "$1" enable ip6 interface "host-$1-in"
  vb "$1" set interface ip address "host-$1-in" "$2"
  vb "$1" set interface ip address "host-$1-in" "$4"
  vb "$1" set interface ip address "host-$1-out" "$3"
  vb "$1" ip route add 0.0.0.0/0 via "$UPSTREAM" "host-$1-out"
  vb "$1" set interface bpa in2out "host-$1-in"
  vb "$1" set interface bpa out2in "host-$1-out"
  hx "$1" -c configure -c nat -c nat64 -c "pool v6" \
      -c "prefix-inside 2001:db8:1200::/40" -c "subscriber-length 56" \
      -c "nat64-prefix $NAT64_PFX" -c "prefix-outside 203.0.113.0/29" \
      -c "block-size 2048" -c "timeout udp 600" -c exit -c commit 2>&1
}
# The pair. EACH SIDE TRACKS THE OTHER, as lab/ha-failover.sh's does, so a
# box whose uplink is gone gives up the subscribers' gateway as well.
configure_ha() {   # <box> <priority>
  if [ "$MODE" = off ]; then
    hx "$1" -c configure \
      -c "interface host-$1-out" -c "vrrp 2 address $VIP_OUT" \
      -c "vrrp 2 priority $2" -c "vrrp 2 track host-$1-in decrement 150" \
      -c exit -c "commit check" -c commit 2>&1
  else
    hx "$1" -c configure \
      -c "interface host-$1-in" -c "vrrp 1 address $GW_GUA" \
      -c "vrrp 1 address $GW_LL" -c "vrrp 1 priority $2" \
      -c "vrrp 1 track host-$1-out decrement 150" -c exit \
      -c "interface host-$1-out" -c "vrrp 2 address $VIP_OUT" \
      -c "vrrp 2 priority $2" -c "vrrp 2 track host-$1-in decrement 150" \
      -c exit -c "commit check" -c commit 2>&1
  fi
}

echo "=== two dataplanes, a NAT64 pool on each ==="
start_box a hxa
start_box b hxb
configure_box a 2001:db8:1200:ab00::2/56 192.0.2.11/24 100.64.3.252/22 | sed 's/^/  a: /'
configure_box b 2001:db8:1200:ab00::3/56 192.0.2.12/24 100.64.3.251/22 | sed 's/^/  b: /'

# The block the arithmetic gives the subscriber's prefix, from the model -
# not from either box - and then what each box says it computes.
EXPECT=$(python3 - <<'EOF'
import sys; sys.path.insert(0, "/src/ref")
from bpa_model import AddrSet6, PoolConfig
s = AddrSet6(["2001:db8:1200::/40"], 56)
idx = s.index_of("2001:db8:1200:ab00::10")
p = PoolConfig("v6", ["0.0.0.0/%d" % (32 - (56 - 40))],
               ["203.0.113.0/29"], block_size=2048)
f = p.forward(idx)
print(f["ip_pub"], f["port_lo"], f["port_hi"])
EOF
)
PUB_IP=$(echo "$EXPECT" | cut -d' ' -f1)
PORT_LO=$(echo "$EXPECT" | cut -d' ' -f2)
PORT_HI=$(echo "$EXPECT" | cut -d' ' -f3)
formula_of() {   # <box>
  hx "$1" -c "show nat64 subscriber $SUB" 2>&1 \
    | sed -n 's/.*gives it \([0-9.]*\) ports \([0-9]*\)-\([0-9]*\).*/\1 \2 \3/p'
}
FORMULA_A=$(formula_of a); FORMULA_B=$(formula_of b)
echo "  the model:  $EXPECT"
echo "  box a says: $FORMULA_A"
echo "  box b says: $FORMULA_B"

# ------------------------------------------------------------- the upstream
rm -f /tmp/upstream.log /tmp/hold.flag /tmp/hold.out /tmp/probe-port
ip netns exec net python3 "$FLOW" udp-echo "$UPSTREAM" "$UDP_PORT" \
    /tmp/upstream.log > /tmp/udp-echo.out 2>&1 &
ip netns exec net python3 "$FLOW" tcp-echo "$UPSTREAM" "$TCP_PORT" \
    /tmp/upstream.log > /tmp/tcp-echo.out 2>&1 &
sleep 1

probe() {   # <source port> <count> <timeout> -> "<ok>/<count> <ip>:<port>"
  ip netns exec sub python3 "$FLOW" udp-probe "$SUB" "$1" "$DST" \
      "$UDP_PORT" "$2" "$3" 2>/dev/null | sed -n 's/^result //p'
}
in_block() {   # <ip:port>
  _ip=${1%%:*}; _port=${1##*:}
  [ "$_ip" = "$PUB_IP" ] || return 1
  case "$_port" in ''|*[!0-9]*) return 1 ;; esac
  [ "$_port" -ge "$PORT_LO" ] && [ "$_port" -le "$PORT_HI" ]
}
sessions_on() {
  vb "$1" show bpa session 2>/dev/null | sed -n 's/^total: \([0-9]*\) sessions.*/\1/p'
}
next_port() {   # a source port nobody used yet, in a file: see ha-failover.sh
  _p=$(cat /tmp/probe-port 2>/dev/null || echo 41000)
  _p=$((_p + 1)); echo "$_p" > /tmp/probe-port; echo "$_p"
}
receiving_box() {   # -> a | b | none, by which box grew a session
  _pp=$(next_port)
  _a0=$(sessions_on a); _b0=$(sessions_on b)
  probe "$_pp" 1 1.0 >/dev/null 2>&1
  _a1=$(sessions_on a); _b1=$(sessions_on b)
  if [ "${_a1:-0}" -gt "${_a0:-0}" ]; then echo a
  elif [ "${_b1:-0}" -gt "${_b0:-0}" ]; then echo b
  else echo none; fi
}

# ----------------------------------------- stage 1: is the fixture any good?
echo
echo "=== stage 1: the fixture - each box translates on its own address ==="
ip netns exec net ip route replace 203.0.113.0/29 via 192.0.2.11
ip netns exec sub ip -6 route replace "$NAT64_PFX" via 2001:db8:1200:ab00::2 dev sub0
FIX_A=$(probe 40009 3 1.0)
ip netns exec net ip route replace 203.0.113.0/29 via 192.0.2.12
ip netns exec sub ip -6 route replace "$NAT64_PFX" via 2001:db8:1200:ab00::3 dev sub0
FIX_B=$(probe 40010 3 1.0)
echo "  through box a: $FIX_A"
echo "  through box b: $FIX_B"
FIX_TCP=$(ip netns exec sub python3 "$FLOW" tcp-probe "$SUB" 40011 "$DST" \
          "$TCP_PORT" 5 2>/dev/null | sed -n 's/^result //p')
echo "  a TCP connection through box b: ${FIX_TCP:-(nothing)}"
rm -f /tmp/fixhold.flag
ip netns exec sub python3 "$FLOW" tcp-hold "$SUB" 40012 "$DST" "$TCP_PORT" \
    /tmp/fixhold.flag 20 5 > /tmp/fixhold.out 2>&1 &
FIXHOLD_PID=$!
sleep 2; touch /tmp/fixhold.flag; wait "$FIXHOLD_PID" 2>/dev/null
FIX_HOLD=$(sed -n 's/^survived //p' /tmp/fixhold.out)
echo "  a held one, nothing broken under it: ${FIX_HOLD:-(no answer)}"

# ------------------------------------------------- stage 2: the pair, typed
echo
echo "=== stage 2: the pair, through the VTY ==="
[ "$MODE" = off ] && echo "  off: the IPv4 router only, as before #485"
configure_ha a 200 > /tmp/ha-a.txt 2>&1
configure_ha b 100 > /tmp/ha-b.txt 2>&1
sed 's/^/  a: /' /tmp/ha-a.txt
sed 's/^/  b: /' /tmp/ha-b.txt
hx a -c "show running-config" > /tmp/run-a.txt 2>&1
sed -n '/^interface host-a-in/,/^exit/p' /tmp/run-a.txt | sed 's/^/  a: /'
sleep 5
hx a -c "show cgnat ha" > /tmp/cgha-a.txt 2>&1
hx b -c "show cgnat ha" > /tmp/cgha-b.txt 2>&1
sed -n '/^  role/,/^$/p' /tmp/cgha-a.txt | sed 's/^/  a: /'
sed -n '/^  role/,/^$/p' /tmp/cgha-b.txt | sed 's/^/  b: /'
vb a show vrrp vr | sed 's/^/  a: /'

# ----------------------------------- stage 3: the service, through the gateway
#
# From here the subscriber's route goes to the virtual link-local and the
# upstream's to the virtual IPv4 address, and NEITHER IS TOUCHED AGAIN.
echo
echo "=== stage 3: the service, through a gateway the product has to own ==="
ip netns exec sub ip -6 route replace "$NAT64_PFX" via "$GW_LL" dev sub0
ip netns exec net ip route replace 203.0.113.0/29 via "$VIP_OUT"
_settle=0
while [ "$_settle" -lt 20 ]; do
  case "$(probe 40000 1 1.0)" in 1/1\ *) break ;; esac
  _settle=$((_settle + 1))
done
echo "  the pair answered after ${_settle} tries"
BEFORE=$(probe 40001 4 1.0)
BEFORE_SRC=$(echo "$BEFORE" | cut -d' ' -f2)
ACTIVE_BEFORE=$(receiving_box)
echo "  round trips through $GW_LL: ${BEFORE:-(nothing came back)}"
echo "  the box receiving the subscriber's traffic: $ACTIVE_BEFORE"
NEIGH=$(ip netns exec sub ip -6 neigh show dev sub0 | tr '\n' ';')
echo "  the subscriber's neighbor cache: $NEIGH"

# ------------------------------------------------------- causing the failures
#
# The takeover is timed from the moment the failure is caused to the first
# translated round trip through the new master, with a probe every half
# second from the SAME source socket the service used before (40001): what
# that socket is given afterwards is the answer to "what happens to a flow
# that was open".
now_ms() { date +%s%3N; }
await_service() {   # <t0 ms> -> AWAIT_OK, AWAIT_MS, AWAIT_SRC
  AWAIT_OK=no; AWAIT_MS=-; AWAIT_SRC="-"
  _deadline=$(( $1 + TAKEOVER_WINDOW * 1000 ))
  while [ "$(now_ms)" -lt "$_deadline" ]; do
    _r=$(probe 40001 1 0.5)
    case "$_r" in
      1/1\ *) AWAIT_OK=yes; AWAIT_MS=$(( $(now_ms) - $1 ))
              AWAIT_SRC=$(echo "$_r" | cut -d' ' -f2); return 0 ;;
    esac
  done
  return 1
}
pid_of() { eval "echo \"\$VPP_PID_$1\""; }
EXECSTOP=$(sed -n 's/^ExecStop=-\{0,1\}//p' /src/appliance/systemd/vpp-stop-drain.conf)
fail_link() {   # the uplink pulled, at the bridge and in the dataplane
  # The dataplane is told as well, for the measured reason lab/ha-failover.sh
  # gives: an af_packet port never reports a pulled veth as down.
  ip link set "$1-out-br" down
  vb "$1" set interface state "host-$1-out" down
}
fail_service() {   # stopped the way the appliance stops it
  _p=$(pid_of "$1")
  [ -n "$EXECSTOP" ] && vb "$1" bpa stop-drain >/dev/null 2>&1
  kill -TERM "$_p" 2>/dev/null
}
fail_crash() { kill -9 "$(pid_of "$1")" 2>/dev/null; }
round() {   # <label> <link|service|crash> -> VICTIM, AWAIT_*, MOVED
  VICTIM=$(receiving_box)
  if [ "$VICTIM" = none ]; then
    echo "  no box is receiving the subscriber's traffic; breaking box a anyway"
    VICTIM=a
  else
    echo "  the box receiving the subscriber's traffic: $VICTIM"
  fi
  _t0=$(now_ms)
  case "$2" in
    link) fail_link "$VICTIM" ;; service) fail_service "$VICTIM" ;;
    crash) fail_crash "$VICTIM" ;;
  esac
  await_service "$_t0"
  _now=$(receiving_box)
  if [ "$_now" = none ]; then MOVED="nothing is receiving the service"
  elif [ "$_now" = "$VICTIM" ]; then MOVED="the broken box $VICTIM still has it"
  else MOVED="the service moved to box $_now"; fi
  echo "  after the $1: ok=$AWAIT_OK after ${AWAIT_MS} ms, seen upstream as $AWAIT_SRC"
  echo "  $MOVED"
  echo "  the subscriber's neighbor cache: $(ip netns exec sub ip -6 neigh show dev sub0 | tr '\n' ';')"
}

echo
echo "=== stage 4: the uplink is pulled, and the box stays alive ==="
round "pulled uplink" link
LINK_OK=$AWAIT_OK; LINK_MS=$AWAIT_MS; LINK_SRC=$AWAIT_SRC; LINK_MOVED=$MOVED
LINK_VICTIM=$VICTIM
# The IPv6 gateway's role on the box whose uplink went, read off it: tracking
# took the IPv6 router down with the IPv4 one.
LINK_ROLE=$(vb "$LINK_VICTIM" show vrrp vr | sed -n '/IPv6/,/state/p' | sed -n 's/.*state \([A-Za-z]*\).*/\1/p')
echo "  box $LINK_VICTIM's IPv6 router now: ${LINK_ROLE:-(none)}"
ip link set "$LINK_VICTIM-out-br" up
vb "$LINK_VICTIM" set interface state "host-$LINK_VICTIM-out" up
sleep 6

echo
echo "=== stage 5: the dataplane is stopped the way an appliance stops it ==="
round "stop" service
STOP_OK=$AWAIT_OK; STOP_MS=$AWAIT_MS; STOP_SRC=$AWAIT_SRC; STOP_MOVED=$MOVED
STOP_VICTIM=$VICTIM
_n=0; while kill -0 "$(pid_of "$STOP_VICTIM")" 2>/dev/null && [ "$_n" -lt 20 ]; do
  sleep 1; _n=$((_n + 1)); done
kill -9 "$(pid_of "$STOP_VICTIM")" 2>/dev/null
echo "  bringing box $STOP_VICTIM back, configured as it was"
start_box "$STOP_VICTIM" "hx$STOP_VICTIM"
case "$STOP_VICTIM" in
  a) configure_box a 2001:db8:1200:ab00::2/56 192.0.2.11/24 100.64.3.252/22 >/dev/null 2>&1
     configure_ha a 200 >/dev/null 2>&1 ;;
  b) configure_box b 2001:db8:1200:ab00::3/56 192.0.2.12/24 100.64.3.251/22 >/dev/null 2>&1
     configure_ha b 100 >/dev/null 2>&1 ;;
esac
sleep 6

echo
echo "=== stage 6: the active box is killed, with a connection open through it ==="
ip netns exec sub python3 "$FLOW" tcp-hold "$SUB" 40002 "$DST" "$TCP_PORT" \
    /tmp/hold.flag 90 5 > /tmp/hold.out 2>&1 &
HOLD_PID=$!
sleep 3
HOLD_PUBLIC=$(sed -n 's/^public //p' /tmp/hold.out)
echo "  the held connection's public address: ${HOLD_PUBLIC:-(it never connected)}"
round "kill" crash
CRASH_OK=$AWAIT_OK; CRASH_MS=$AWAIT_MS; CRASH_SRC=$AWAIT_SRC; CRASH_MOVED=$MOVED
touch /tmp/hold.flag
wait "$HOLD_PID" 2>/dev/null
HOLD_RESULT=$(sed -n 's/^survived //p' /tmp/hold.out)
echo "  the held connection after the kill: ${HOLD_RESULT:-(no answer)}"
NEWCONN=$(ip netns exec sub python3 "$FLOW" tcp-probe "$SUB" 40003 "$DST" \
          "$TCP_PORT" 5 2>/dev/null | sed -n 's/^result //p')
NEW_SRC=$(echo "$NEWCONN" | sed -n 's/^ok //p')
echo "  a new connection from the same subscriber: ${NEWCONN:-(nothing)}"
if [ "$CRASH_OK" = yes ]; then
  case "$HOLD_RESULT" in
    no*)  DROP_VERDICT="dropped across a takeover, which is what this design claims" ;;
    yes*) DROP_VERDICT="SURVIVED the takeover, which this design does not claim" ;;
    *)    DROP_VERDICT="no answer from the held connection" ;;
  esac
else
  DROP_VERDICT="not measurable: nothing took the service over"
fi
echo "  in-flight connections: $DROP_VERDICT"

echo
echo "=== the measurement, for docs/nat64.md and docs/ha.md ==="
echo "  before:        $BEFORE_SRC (the model: $PUB_IP ports $PORT_LO-$PORT_HI)"
echo "  pulled uplink: ${LINK_MS} ms, the open UDP socket came back as $LINK_SRC"
echo "  stopped:       ${STOP_MS} ms, the open UDP socket came back as $STOP_SRC"
echo "  killed:        ${CRASH_MS} ms, the open UDP socket came back as $CRASH_SRC"
echo "  TCP held across the kill: ${HOLD_RESULT:-(no answer)}; a new one: ${NEW_SRC:-(none)}"
tail -8 /tmp/upstream.log 2>/dev/null | sed 's/^/  upstream: /'

echo
echo "Stage 6 killed a dataplane on purpose, so a 'NO DATAPLANE at the"
echo "verdict' report below a failing run is that box's log."
echo
echo "=========== VERDICT ==========="
expect_in "FIXTURE: box a translates the IPv6 subscriber on its own address" "3/3" "$FIX_A"
expect_in "FIXTURE: box b translates the IPv6 subscriber on its own address" "3/3" "$FIX_B"
expect "FIXTURE: both boxes compute the model's block for the subscriber" \
  sh -c "[ -n '$EXPECT' ] && [ '$FORMULA_A' = '$EXPECT' ] && [ '$FORMULA_B' = '$EXPECT' ]"
expect_in "FIXTURE: a TCP connection crosses the NAT64 pool" "ok " "$FIX_TCP"
expect_in "FIXTURE: and the drop detector can say a live connection is live" "yes" "$FIX_HOLD"

# Everything below is the product's.
expect "the VTY took the IPv6 gateway: show running-config carries it" \
  sh -c "grep -q '^ vrrp 1 address $GW_LL\$' /tmp/run-a.txt && grep -q '^ vrrp 1 address $GW_GUA\$' /tmp/run-a.txt"
expect "the IPv6 gateway tracks the IPv4 uplink: box a advertises its 200" \
  sh -c "sed -n '/vrrp 1 ipv6/,/priority/p' /tmp/cgha-a.txt | grep -q 'priority           200\$'"
expect "commit check no longer warns about the NAT64 pool in the pair" \
  sh -c "! grep -q 'a NAT64 pool on a box in a VRRP pair' /tmp/ha-a.txt && grep -q 'commit complete' /tmp/ha-a.txt"
expect "show cgnat ha: the IPv6 router is master on one box and backup on the other" \
  sh -c "cat /tmp/cgha-a.txt /tmp/cgha-b.txt | grep -c 'vrrp 1 ipv6' | grep -qx 2 && grep -q 'vrrp 1 ipv6   master' /tmp/cgha-a.txt && grep -q 'vrrp 1 ipv6   backup' /tmp/cgha-b.txt"
expect_in "the subscriber's gateway resolved to RFC 5798's IPv6 virtual MAC" \
  "$GW_LL lladdr $VMAC6" "$NEIGH"
expect_in "the subscriber's round trip works through the IPv6 virtual gateway" "4/4" "$BEFORE"
expect "the upstream sees it as the model's address and block" in_block "$BEFORE_SRC"
expect_not_in "a box is receiving the subscriber's traffic" "none" "$ACTIVE_BEFORE"

expect_in "a pulled uplink is survived: the service comes back" "yes" "$LINK_OK"
expect "and on the same public address and block" in_block "$LINK_SRC"
expect_in "and it is no longer the broken box that receives it" "the service moved to box" "$LINK_MOVED"
expect_in "the IPv6 gateway left the box whose uplink went, by tracking" "Backup" "$LINK_ROLE"

expect_in "a stopped dataplane is survived: the service comes back" "yes" "$STOP_OK"
expect "and on the same public address and block" in_block "$STOP_SRC"
expect_in "and it is no longer the stopped box that receives it" "the service moved to box" "$STOP_MOVED"

expect_in "a killed dataplane is survived: the service comes back" "yes" "$CRASH_OK"
expect "and on the same public address and block" in_block "$CRASH_SRC"
expect_in "and it is no longer the killed box that receives it" "the service moved to box" "$CRASH_MOVED"

expect "a new connection after the takeover works" sh -c "[ -n '$NEW_SRC' ]"
expect "and gets the same public address and block" in_block "${NEW_SRC:--:-}"
expect_in "in-flight connections drop across the takeover, and this says so" \
  "dropped across a takeover" "$DROP_VERDICT"
verdict
