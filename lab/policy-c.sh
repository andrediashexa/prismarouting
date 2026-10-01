#!/bin/sh
# Routing policy, phase C (#537): route leaking between VRFs, through a policy
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# OFF-RED-AT-LEAST: 25
# Routing policy, phase C (#537): a VRF importing routes from another VRF and
# from the default table through a policy (`vrf-import <vrf>|default policy
# <p>`, FRR's `import vrf` and `import vrf route-map`) - asserted on what
# bgpd, zebra and VPP's FIB of the importing VRF hold, and on a packet that
# crosses from one VRF to the other, not on the text of a configuration. And
# bgpd is asked whether it holds each statement the box gave it (#539).
#
# Three tables, each a VPP FIB table and a Linux VRF of the same number:
#
#   default (0)  host-veth-up   10.0.0.1/24   the upstream, AS 65001, sends
#                                             0.0.0.0/0 and 10.99.0.0/24
#   red     (10) host-veth-red  10.10.0.1/24  a customer's router, AS 65010,
#                                             sends its LAN, 10.11.0.0/24
#   svc     (30) host-veth-svc  10.30.0.1/24  shared services, AS 65030,
#                                             sends 203.0.113.0/24 (the
#                                             service) and 192.0.2.0/24
#
# red imports svc and the default table through one policy, LEAK: the
# service with local preference 200 and a community, the default route,
# and nothing else - 192.0.2.0/24 and 10.99.0.0/24 stay out. svc and the
# default table import red without a policy, so the replies find their way
# to the customer's LAN.
#
# A CONNECTED ROUTE LEAKED DOES NOT REACH VPP. red also redistributes its
# connected 10.10.0.0/24, and svc imports it: bgpd and zebra have it, and
# the kernel holds it in svc's table as `dev red` - the VRF device, which is
# how FRR leaks a route whose next hop is the other VRF itself. linux-nl has
# no VPP interface for a VRF device, so VPP's table 30 never gets it
# (measured, the first run of this lab: the echo reply to 10.10.0.2 was
# dropped in table 30). A leaked route with a next hop - every BGP route
# here - reaches VPP. The lab prints the connected one and does not assert
# it: it is #537's finding, not the behaviour the box should have.
#
# THE LINUX VRFS ARE THE VTY'S (#540). Until then this lab made `red` and
# `svc` by hand, with `ip link add red type vrf table 10`, because the box
# made no Linux VRF device - only prisma-bp, the containers' backplane, had
# one (#447) - and FRR's BGP in a VRF needs that device. Now `vrf red table
# 10` makes it and puts the LCP host interface of table 10 in it, and the
# lab asserts that, and that the boot's `--load` makes them again on a host
# that has lost them.
#
# MODE=off commits the same box and the same policies without the vrf-import
# statements, and every check that a route leaked must go red.
MODE=${1:-on}
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

# One namespace per neighbour: up (the upstream), red (the customer), svc
# (the shared services).
for n in up red svc; do
  ip link add veth-$n-o type veth peer name veth-$n 2>/dev/null
  ip link set veth-$n up
  ip netns add $n 2>/dev/null
  ip link set veth-$n-o netns $n
  ip netns exec $n ip link set lo up
  ip netns exec $n ip link set veth-$n-o up
done
ip netns exec up ip addr add 10.0.0.2/24 dev veth-up-o
ip netns exec up ip addr add 10.99.0.1/24 dev lo
ip netns exec up ip route add 10.11.0.0/24 via 10.0.0.1
ip netns exec red ip addr add 10.10.0.2/24 dev veth-red-o
ip netns exec red ip addr add 10.11.0.1/24 dev lo
ip netns exec red ip route add default via 10.10.0.1
ip netns exec svc ip addr add 10.30.0.2/24 dev veth-svc-o
ip netns exec svc ip addr add 203.0.113.10/24 dev lo
ip netns exec svc ip addr add 192.0.2.10/24 dev lo
ip netns exec svc ip route add default via 10.30.0.1

sysctl -p /src/appliance/etc/sysctl.conf >/dev/null 2>&1 || true
vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -25 /tmp/vpp.log; exit 1; }
use_binary_api
HX="python3 /src/src/prismash/prismash.py"

# Each pair is made by the product (#182). The tables before the addresses:
# VPP refuses to move an interface that has one.
vppctl ip table add 10
vppctl ip table add 30
for n in up red svc; do
  vppctl create host-interface name veth-$n >/dev/null
  vppctl set interface state host-veth-$n up
done
vppctl set interface ip table host-veth-red 10
vppctl set interface ip table host-veth-svc 30
# The backplane's VRF, as appliance/prisma-backplane makes it on a box: the
# VTY must neither list it nor take its table (#540).
ip link add prisma-bp type vrf table 128
ip link set prisma-bp up
# The Linux VRFs, by the VTY (#540), in the commit that makes the pairs:
# the same table numbers as VPP's, which is what linux-nl syncs a kernel
# table into.
PAIRS=$($HX -c "configure" -c "vrf red table 10" -c "vrf svc table 30" \
    -c "interface host-veth-up" -c "linux-name vppup0" \
    -c "exit" -c "interface host-veth-red" -c "linux-name vppred0" \
    -c "exit" -c "interface host-veth-svc" -c "linux-name vppsvc0" \
    -c "exit" -c "commit" 2>&1)
printf '%s\n' "$PAIRS" | tail -1 | sed 's/^/  pairs and VRFs: /'
L_RED=$(ip -d -o link show red 2>&1)
L_SVC=$(ip -d -o link show svc 2>&1)
L_RED0=$(ip -o link show vppred0 2>&1)
L_SVC0=$(ip -o link show vppsvc0 2>&1)
L_UP0=$(ip -o link show vppup0 2>&1)
vppctl set interface ip address host-veth-up 10.0.0.1/24
vppctl set interface ip address host-veth-red 10.10.0.1/24
vppctl set interface ip address host-veth-svc 10.30.0.1/24
sleep 2
echo "  linux: $(ip -br link show master red | awk '{print $1}') in red," \
     "$(ip -br link show master svc | awk '{print $1}') in svc;" \
     "$(ip -4 -br addr show vppred0 | awk '{print $3}') on vppred0"

# ---------------- Prisma Routing ----------------
mkdir -p /var/run/frr /etc/frr
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
printf 'hostname prisma-routing\n' > /etc/frr/bgpd.conf
chown -R frr:frr /var/run/frr /etc/frr
start_frr bgpd

# ---------------- the upstream and the shared services ----------------
peer_frr() {   # <ns> <bgpd.conf>
  mkdir -p /var/run/frr/$1 /etc/frr/$1
  printf 'hostname %s\n' "$1" > /etc/frr/$1/zebra.conf
  printf '%s\n' "$2" > /etc/frr/$1/bgpd.conf
  touch /etc/frr/$1/vtysh.conf
  chown -R frr:frr /var/run/frr/$1 /etc/frr/$1
  for d in zebra bgpd; do
    ip netns exec $1 /usr/lib/frr/$d -d -N $1 -A 127.0.0.1 -f /etc/frr/$1/$d.conf
  done
}
peer_frr up "hostname up
router bgp 65001
 bgp router-id 10.0.0.2
 no bgp ebgp-requires-policy
 no bgp network import-check
 neighbor 10.0.0.1 remote-as 65000
 address-family ipv4 unicast
  network 0.0.0.0/0
  network 10.99.0.0/24
  neighbor 10.0.0.1 activate
 exit-address-family"
peer_frr red "hostname red
router bgp 65010
 bgp router-id 10.10.0.2
 no bgp ebgp-requires-policy
 neighbor 10.10.0.1 remote-as 65000
 address-family ipv4 unicast
  network 10.11.0.0/24
  neighbor 10.10.0.1 activate
 exit-address-family"
peer_frr svc "hostname svc
router bgp 65030
 bgp router-id 10.30.0.2
 no bgp ebgp-requires-policy
 neighbor 10.30.0.1 remote-as 65000
 address-family ipv4 unicast
  network 203.0.113.0/24
  network 192.0.2.0/24
  neighbor 10.30.0.1 activate
 exit-address-family"

. /src/lab/verdict.sh

echo "=========== the three BGP instances, through the VTY ==========="
SETUP=$($HX -c "configure" \
        -c "router bgp 65000" -c "bgp router-id 10.0.0.1" \
        -c "no bgp ebgp-requires-policy" \
        -c "neighbor 10.0.0.2 remote-as 65001" \
        -c "address-family ipv4 unicast" -c "neighbor 10.0.0.2 activate" \
        -c "exit-address-family" -c "exit" \
        -c "router bgp 65000 vrf svc" -c "bgp router-id 10.30.0.1" \
        -c "no bgp ebgp-requires-policy" \
        -c "neighbor 10.30.0.2 remote-as 65030" \
        -c "address-family ipv4 unicast" -c "neighbor 10.30.0.2 activate" \
        -c "exit-address-family" -c "exit" \
        -c "router bgp 65000 vrf red" -c "bgp router-id 10.10.0.1" \
        -c "no bgp ebgp-requires-policy" \
        -c "neighbor 10.10.0.2 remote-as 65010" \
        -c "address-family ipv4 unicast" -c "neighbor 10.10.0.2 activate" \
        -c "redistribute connected" \
        -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$SETUP" | grep -vE "^(note|warning):" | tail -3 | sed 's/^/  /'
printf '%s' "$SETUP" | grep -q "commit complete" || {
  echo "the setup did not commit - nothing below would mean anything"
  printf '%s\n' "$SETUP" | tail -20 | sed 's/^/  /'
  exit 1
}
# A FIB table's entry for a prefix, by its path: VPP's table 10 always holds
# 0.0.0.0/0, as a drop, so a route is counted by where it goes.
fib() {   # <table> <prefix> -> the entry's lines
  vppctl show ip fib table "$1" "$2" 2>/dev/null
}
via() {   # <table> <prefix> <interface> -> 1 when the entry forwards there
  fib "$1" "$2" | grep -q "^$2" && fib "$1" "$2" | grep -q "$3" \
    && echo 1 || echo 0
}
n=0
while [ "$n" -lt 90 ]; do
  vtysh -c "show bgp vrf svc ipv4 unicast 203.0.113.0/24" 2>/dev/null \
      | grep -q "^BGP routing table entry" \
    && vtysh -c "show bgp ipv4 unicast 0.0.0.0/0" 2>/dev/null \
      | grep -q "^BGP routing table entry" \
    && vtysh -c "show bgp vrf red ipv4 unicast 10.11.0.0/24" 2>/dev/null \
      | grep -q "^BGP routing table entry" && break
  sleep 1; n=$((n + 1))
done
echo "  svc's BGP has the service, the default table the default, red the" \
     "customer's LAN (${n}s)"
echo "  in VPP before any leak: table 10 203.0.113.0/24 via svc=$(via 10 203.0.113.0/24 host-veth-svc)" \
     "0.0.0.0/0 via up=$(via 10 0.0.0.0/0 host-veth-up);" \
     "table 30 203.0.113.0/24=$(via 30 203.0.113.0/24 host-veth-svc)"
[ "$(via 30 203.0.113.0/24 host-veth-svc)" = 1 ] \
  || { echo "svc's routes never reached VPP's table 30"; fib 30 203.0.113.0/24; exit 1; }
mkdir -p /etc/prisma-routing

echo; echo "=========== the policies, written under policy-options ==========="
POL=$($HX -c "configure" -c "policy-options" \
  -c "prefix-list SERVICE" -c "203.0.113.0/24" -c "exit" \
  -c "community SHARED members 65000:537" \
  -c "policy-statement LEAK" \
  -c "term service" -c "from prefix-list SERVICE" -c "from source-vrf svc" \
  -c "set local-preference 200" -c "set community add SHARED" \
  -c "then accept" -c "exit" \
  -c "term default" -c "from route-filter 0.0.0.0/0 exact" \
  -c "from source-vrf default" -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement NH" -c "term 1" -c "set next-hop 10.30.0.2" \
  -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement OTHER" -c "term 1" -c "then accept" -c "exit" -c "exit" \
  -c "exit" -c "commit" 2>&1)
printf '%s\n' "$POL" | grep -vE "^(note|warning):" | tail -3 | sed 's/^/  /'

# Refused where they are applied, in the VTY's words, and nothing staged.
WRONG=$($HX -c "configure" -c "router bgp 65000 vrf red" \
  -c "address-family ipv4 unicast" \
  -c "vrf-import prisma-bp policy LEAK" \
  -c "vrf-import red policy LEAK" \
  -c "vrf-import svc policy NH" \
  -c "import vrf prisma-bp" \
  -c "exit-address-family" -c "exit" \
  -c "router bgp 65000 vrf prisma-bp" -c "address-family ipv4 unicast" \
  -c "vrf-import red" -c "exit-address-family" -c "exit" \
  -c "router bgp 65000" -c "address-family ipv4 unicast" \
  -c "neighbor 10.0.0.2 import LEAK" -c "exit-address-family" -c "exit" \
  -c "commit check" 2>&1)
printf '%s\n' "$WRONG" | grep -vE "^(note|warning):" | sed 's/^/  /'

echo; echo "=========== applied, MODE=$MODE ==========="
if [ "$MODE" = off ]; then
  APPLY="(nothing applied: MODE=off)"
else
  APPLY=$($HX -c "configure" -c "router bgp 65000 vrf red" \
    -c "address-family ipv4 unicast" \
    -c "vrf-import svc policy LEAK" -c "vrf-import default policy LEAK" \
    -c "exit-address-family" \
    -c "address-family ipv6 unicast" -c "vrf-import svc policy LEAK" \
    -c "exit-address-family" -c "exit" \
    -c "router bgp 65000 vrf svc" -c "address-family ipv4 unicast" \
    -c "vrf-import red" -c "exit-address-family" -c "exit" \
    -c "router bgp 65000" -c "address-family ipv4 unicast" \
    -c "vrf-import red" -c "exit-address-family" -c "exit" \
    -c "commit" 2>&1)
fi
printf "%s\n" "$APPLY" | grep -vE "^(note|warning):" | sed "s/^/  /"
# A second VRF imported through the family's policy under another one is
# refused, now that the family has LEAK.
SECOND=$($HX -c "configure" -c "router bgp 65000 vrf red" \
  -c "address-family ipv4 unicast" -c "vrf-import svc policy OTHER" \
  -c "exit-address-family" -c "exit" -c "commit check" 2>&1)
printf '%s\n' "$SECOND" | grep -vE "^(note|warning):" | sed 's/^/  /'

n=0
while [ "$n" -lt 60 ]; do
  [ "$(via 10 203.0.113.0/24 host-veth-svc)" = 1 ] \
    && [ "$(via 10 0.0.0.0/0 host-veth-up)" = 1 ] && break
  sleep 1; n=$((n + 1))
done
echo "  (${n}s)"

RUN=$($HX -c "show running-config" 2>&1)
FRRRUN=$(vtysh -c "show running-config" 2>&1)
echo "  what bgpd holds:"
printf '%s\n' "$FRRRUN" | grep -E "^router bgp|import vrf" | sed 's/^/    /'

echo; echo "=========== red: what BGP, zebra and VPP's table 10 hold ==========="
B_SVC=$(vtysh -c "show bgp vrf red ipv4 unicast 203.0.113.0/24" 2>&1)
B_REJ=$(vtysh -c "show bgp vrf red ipv4 unicast 192.0.2.0/24" 2>&1)
B_DEF=$(vtysh -c "show bgp vrf red ipv4 unicast 0.0.0.0/0" 2>&1)
B_99=$(vtysh -c "show bgp vrf red ipv4 unicast 10.99.0.0/24" 2>&1)
printf '%s\n' "$B_SVC" | grep -E "^BGP routing|Imported|localpref|Community" | sed 's/^/  203.0.113.0\/24: /'
printf '%s\n' "$B_REJ" | head -1 | sed 's/^/  192.0.2.0\/24: /'
printf '%s\n' "$B_DEF" | grep -E "^BGP routing|Imported|not in table" | sed 's/^/  0.0.0.0\/0: /'
printf '%s\n' "$B_99" | head -1 | sed 's/^/  10.99.0.0\/24: /'
Z_SVC=$(vtysh -c "show ip route vrf red 203.0.113.0/24" 2>&1)
Z_REJ=$(vtysh -c "show ip route vrf red 192.0.2.0/24" 2>&1)
Z_DEF=$(vtysh -c "show ip route vrf red 0.0.0.0/0" 2>&1)
printf '%s\n' "$Z_SVC" | grep -E "Known via|via|Routing entry" | head -3 | sed 's/^/  zebra 203.0.113.0\/24: /'
printf '%s\n' "$Z_DEF" | grep -E "Known via|via|Routing entry" | head -3 | sed 's/^/  zebra 0.0.0.0\/0: /'
K_SVC=$(ip route show vrf red 203.0.113.0/24)
echo "  kernel table red: 203.0.113.0/24 $K_SVC"
F_SVC=$(via 10 203.0.113.0/24 host-veth-svc)
F_REJ=$(via 10 192.0.2.0/24 host-veth-svc)
F_DEF=$(via 10 0.0.0.0/0 host-veth-up)
F_99=$(via 10 10.99.0.0/24 host-veth-up)
fib 10 203.0.113.0/24 | head -6 | sed 's/^/  vpp: /'
echo "  VPP table 10: 203.0.113.0/24 via svc=$F_SVC 192.0.2.0/24=$F_REJ" \
     "0.0.0.0/0 via up=$F_DEF 10.99.0.0/24=$F_99"

echo; echo "=========== a packet: red's customer to the service, across VRFs ==========="
# The way back: svc's table and the default table import red, without a
# policy.
R_SVC=$(via 30 10.11.0.0/24 host-veth-red)
R_UP=$(via 0 10.11.0.0/24 host-veth-red)
echo "  the way back: 10.11.0.0/24 via red in table 30=$R_SVC, in table 0=$R_UP"
# The connected route leaked (the header): printed, not asserted.
echo "  red's connected 10.10.0.0/24 leaked into svc:" \
     "bgp=$(vtysh -c 'show bgp vrf svc ipv4 unicast 10.10.0.0/24' 2>&1 | grep -c '^BGP routing table entry')" \
     "kernel='$(ip route show vrf svc 10.10.0.0/24)'" \
     "VPP table 30=$(fib 30 10.10.0.0/24 | grep -c '^10.10.0.0/24')"
[ "${2:-}" = trace ] && { vppctl clear trace; vppctl trace add af-packet-input 40; }
PING=$(ip netns exec red ping -c 3 -W 1 -I 10.11.0.1 203.0.113.10 2>&1)
printf '%s\n' "$PING" | tail -2 | sed 's/^/  /'
PING_REJ=$(ip netns exec red ping -c 2 -W 1 -I 10.11.0.1 192.0.2.10 2>&1)
printf '%s\n' "$PING_REJ" | grep "packet loss" | sed 's/^/  192.0.2.10: /'
PING_UP=$(ip netns exec red ping -c 3 -W 1 -I 10.11.0.1 10.99.0.1 2>&1)
printf '%s\n' "$PING_UP" | grep "packet loss" | sed 's/^/  10.99.0.1, by the leaked default: /'

[ "${2:-}" = trace ] && { vppctl show trace | head -300; fib 30 10.10.0.0/24; vtysh -c "show bgp vrf svc ipv4 unicast"; vtysh -c "show bgp vrf red ipv4 unicast"; ip route show vrf svc; ip netns exec svc ip route; }
echo; echo "=========== show policy ==========="
SHOWP=$($HX -c "show policy" 2>&1)
printf '%s\n' "$SHOWP" | grep -E "policy-statement|applied" | sed 's/^/  /'
TESTP=$($HX -c "test policy LEAK 192.0.2.0/24" 2>&1)
printf '%s\n' "$TESTP" | sed 's/^/  /'

echo; echo "=========== the default route's leak removed ==========="
GONE=$($HX -c "configure" -c "router bgp 65000 vrf red" \
  -c "address-family ipv4 unicast" -c "no vrf-import default" \
  -c "exit-address-family" -c "exit" -c "commit" 2>&1)
printf '%s\n' "$GONE" | grep -vE "^(note|warning):" | tail -1 | sed 's/^/  /'
n=0
while [ "$n" -lt 60 ]; do
  [ "$(via 10 0.0.0.0/0 host-veth-up)" = 0 ] && break
  sleep 1; n=$((n + 1))
done
B_DEF2=$(vtysh -c "show bgp vrf red ipv4 unicast 0.0.0.0/0" 2>&1)
Z_DEF2=$(vtysh -c "show ip route vrf red 0.0.0.0/0" 2>&1)
F_DEF2=$(via 10 0.0.0.0/0 host-veth-up)
F_SVC2=$(via 10 203.0.113.0/24 host-veth-svc)
FRRRUN2=$(vtysh -c "show running-config" 2>&1)
printf '%s\n' "$B_DEF2" | head -1 | sed 's/^/  bgp 0.0.0.0\/0: /'
printf '%s\n' "$Z_DEF2" | head -2 | sed 's/^/  zebra 0.0.0.0\/0: /'
echo "  VPP table 10: 0.0.0.0/0 via up=$F_DEF2, 203.0.113.0/24 via svc=$F_SVC2 (${n}s)"

echo; echo "=========== a boot: the saved file makes the VRFs again (#540) ==========="
# What a reboot does to the Linux side: the devices are gone and FRR is
# already running with `router bgp 65000 vrf red` in it - the boot unit
# replays the file after the containers and FRR. Removed by hand here, the
# one thing this lab still does to the VRFs without the VTY, because a
# reboot is not something the VTY does to them either.
$HX -c "save" 2>&1 | tail -1 | sed 's/^/  /'
cp "${PRISMA_CONFIG:-/etc/prisma-routing/prisma-routing.conf}" /tmp/boot.conf
grep -E "^vrf " /tmp/boot.conf | sed 's/^/  saved: /'
ip link del red
ip link del svc
B_GONE=$(ip -d -o link show type vrf | grep -cE ": (red|svc):")
LOAD=$($HX --load /tmp/boot.conf 2>&1); LOAD_RC=$?
printf '%s\n' "$LOAD" | grep -vE "^(note|warning):" | tail -2 | sed 's/^/  load: /'
B_RED=$(ip -d -o link show red 2>&1)
B_SVC0=$(ip -o link show vppsvc0 2>&1)
B_RED0=$(ip -o link show vppred0 2>&1)
n=0
while [ "$n" -lt 90 ]; do
  vtysh -c "show bgp vrf red ipv4 unicast 10.11.0.0/24" 2>/dev/null \
      | grep -q "^BGP routing table entry" \
    && [ "$(via 10 203.0.113.0/24 host-veth-svc)" = 1 ] && break
  sleep 1; n=$((n + 1))
done
echo "  red's BGP and the leak back after the load (${n}s)"
B_BGP=$(vtysh -c "show bgp vrf red ipv4 unicast 10.11.0.0/24" 2>&1)
PING_BOOT=$(ip netns exec red ping -c 3 -W 1 -I 10.11.0.1 203.0.113.10 2>&1)
printf '%s\n' "$PING_BOOT" | grep "packet loss" | sed 's/^/  /'

echo; echo "=========== taken back: an interface out, a VRF removed ==========="
OUT=$($HX -c "configure" -c "interface host-veth-red" -c "no vrf" -c "exit" \
  -c "no vrf svc table" -c "commit" 2>&1)
printf '%s\n' "$OUT" | grep -vE "^(note|warning):" | tail -1 | sed 's/^/  /'
U_RED0=$(ip -o link show vppred0 2>&1)
U_SVC=$(ip -o link show svc 2>&1)
U_SVC0=$(ip -o link show vppsvc0 2>&1)
U_RED=$(ip -d -o link show red 2>&1)
U_RUN=$($HX -c "show running-config" 2>&1)
BP_TAKE=$($HX -c "configure" -c "vrf mgmt table 128" -c "commit check" 2>&1)
printf '%s\n' "$BP_TAKE" | grep -E "^error" | sed 's/^/  /'
U_BP=$(ip -d -o link show prisma-bp 2>&1)
printf '%s\n' "$U_RUN" | grep -E "^vrf " | sed 's/^/  running: /'

echo; echo "=========== VERDICT ==========="
# #540: the VRF devices are the VTY's.
expect_in "the pairs and the VRFs commit" "commit complete" "$PAIRS"
expect_in "vrf red table 10 is a Linux VRF on table 10" "vrf table 10 " "$L_RED"
expect_in "vrf svc table 30, on table 30" "vrf table 30 " "$L_SVC"
expect_in "  red's TAP is in red" "master red " "$L_RED0"
expect_in "  svc's TAP in svc" "master svc " "$L_SVC0"
expect_not_in "  the upstream's TAP in neither" "master " "$L_UP0"
expect_in "show running-config reads them back" "vrf red table 10" "$RUN"
expect_in "  both" "vrf svc table 30" "$RUN"
expect "the boot: both devices gone before the load" test "$B_GONE" -eq 0
expect "  the load succeeds (rc $LOAD_RC)" test "$LOAD_RC" -eq 0
expect_in "  red made again on table 10" "vrf table 10 " "$B_RED"
expect_in "  its TAP in it" "master red " "$B_RED0"
expect_in "  and svc's in svc" "master svc " "$B_SVC0"
expect_in "  red's BGP comes back with no manual step" \
  "BGP routing table entry" "$B_BGP"
expect_in "  and a packet crosses from red to svc again" " 0% packet loss" \
  "$PING_BOOT"
expect_in "taken back: commit" "commit complete" "$OUT"
expect_not_in "  an interface out of table 10 has its TAP out of red" \
  "master " "$U_RED0"
expect_in "  red stays" "vrf table 10 " "$U_RED"
expect_in "  svc is removed" "does not exist" "$U_SVC"
expect_not_in "  and its TAP is in nothing" "master " "$U_SVC0"
expect_in "  prisma-bp is never the VTY's: running-config has no prisma-*" \
  "vrf red table 10" "$U_RUN"
# FRR's own `vrf prisma-bp` stanza is in the routing half, as it always was:
# zebra writes one for every VRF device it knows. The statement is not.
expect_not_in "  (none listed)" "vrf prisma-bp table" "$U_RUN"
expect_said "  and its table is not a tenant's" \
  "vrf mgmt: table 128 is prisma-bp's" "$BP_TAKE"
expect_in "  prisma-bp is untouched through all of it" "vrf table 128 " "$U_BP"
expect_in "the policies commit" "commit complete" "$POL"
expect_said "nothing leaks out of prisma-bp" \
  "prisma-bp is a VRF of the box's own: no route leaks out of it" "$WRONG"
expect_said "nothing leaks into prisma-bp" \
  "prisma-bp is a VRF of the box's own: no route leaks into it" "$WRONG"
expect_said "a VRF does not import from itself" \
  "a VRF does not import from itself" "$WRONG"
expect_said "a leak sets no next hop" \
  "set next-hop does nothing on vrf-import" "$WRONG"
expect_said "from source-vrf is for a vrf-import" \
  "from source-vrf is for a vrf-import" "$WRONG"
expect_said "one policy per address family" \
  "imports through policy LEAK" "$SECOND"
# #539: bgpd holds what it was given.
expect_in "bgpd holds red's import of svc" "import vrf svc" "$FRRRUN"
expect_in "  of the default table" "import vrf default" "$FRRRUN"
expect_in "  and the policy, its route-map" \
  "import vrf route-map PO-LEAK-BGP-VRFIMPORT" "$FRRRUN"
expect "  in IPv4 and in IPv6" test "$(printf '%s\n' "$FRRRUN" \
  | grep -c 'import vrf route-map PO-LEAK-BGP-VRFIMPORT')" -eq 2
expect_in "  and the map" "route-map PO-LEAK-BGP-VRFIMPORT permit 10" "$FRRRUN"
expect_in "  matching the source VRF" " match source-vrf svc" "$FRRRUN"
expect_in "the box reads it back: show running-config" \
  "import vrf route-map PO-LEAK-BGP-VRFIMPORT" "$RUN"
expect_in "show policy says where" "applied: vrf-import" "$SHOWP"
expect_said "test policy: a prefix no term takes is not leaked" \
  "imported into a VRF, by vrf-import, it is not" "$TESTP"
expect_in "the service leaks into red's BGP" \
  "BGP routing table entry for 203.0.113.0/24" "$B_SVC"
expect_in "  imported from svc" "Imported from" "$B_SVC"
expect_in "  with the policy's local preference" "localpref 200" "$B_SVC"
expect_in "  and its community" "65000:537" "$B_SVC"
expect_in "the second prefix of svc does not: the policy rejects it" \
  "not in table" "$B_REJ"
expect_in "the service is in zebra's red table" "Known via \"bgp\"" "$Z_SVC"
expect_in "  and in the kernel's" "10.30.0.2" "$K_SVC"
expect "  and in VPP's table 10, out through svc's interface" test "$F_SVC" = 1
expect "the rejected prefix is not in VPP's table 10" test "$F_REJ" = 0
expect "the customer's LAN reaches svc's table in VPP, the way back" \
  test "$R_SVC" = 1
expect "  and the default table's" test "$R_UP" = 1
expect_in "a packet crosses from red to svc and back" " 0% packet loss" "$PING"
expect_not_in "  and not to the rejected prefix's" " 0% packet loss" "$PING_REJ"
expect_in "the default table's default leaks into red, by policy" \
  "BGP routing table entry for 0.0.0.0/0" "$B_DEF"
expect_in "  and nothing else of it: 10.99.0.0/24 stays out" "not in table" "$B_99"
expect_in "  in zebra's red table" "Known via \"bgp\"" "$Z_DEF"
expect "  and VPP's table 10 sends the default out the upstream" test "$F_DEF" = 1
expect "  10.99.0.0/24 is not there by name" test "$F_99" = 0
expect_in "  a packet follows it to the upstream" " 0% packet loss" "$PING_UP"
expect_in "removing the leak: commit" "commit complete" "$GONE"
expect_in "  the default leaves red's BGP" "not in table" "$B_DEF2"
expect_not_in "  and zebra's red table" "Known via \"bgp\"" "$Z_DEF2"
expect "  and VPP's table 10" test "$F_DEF2" = 0
expect "  while the service stays" test "$F_SVC2" = 1
expect_not_in "  bgpd no longer imports the default table" \
  "import vrf default" "$FRRRUN2"
verdict
