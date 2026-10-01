#!/bin/sh
# Routing policy, phase B (#536): the attach points, on real daemons
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# OFF-RED-AT-LEAST: 34
# Routing policy, phase B (#536): a policy where BGP originates, aggregates,
# advertises on a condition and installs, where OSPF and IS-IS originate their
# default, and on what zebra installs from each protocol - asserted on what
# the neighbour receives, what zebra and the kernel hold and what VPP
# forwards, not on the text of a configuration. And each daemon is asked
# whether it holds the statement the box gave it (#539): FRR's syntax check
# passes statements a daemon then leaves out.
#
# One link, the box (AS 65000, 10.0.0.1) to an upstream (AS 65001, 10.0.0.2),
# with BGP, OSPF and IS-IS over it. The upstream sends:
#
#   10.9.0.0/16    BGP: the condition - the default route to the upstream and
#                  the backup advertisement wait on it, and the lab withdraws
#                  it half way
#   10.20.1.0/24   BGP: the table policy gives it metric 77
#   10.20.2.0/24   BGP: the table policy keeps it out of the FIB
#   10.70.0.0/24   OSPF: the FIB policy for OSPF keeps it out of the FIB
#   10.71.0.0/24   IS-IS: the FIB policy for IS-IS keeps it out of the FIB
#
# The box's static routes: 10.30.1.0/24 and 10.30.2.0/24 under an aggregate,
# 10.40.0.0/24 a network with a policy, 10.50.0.0/24 the backup
# advertisement, 10.60.0.0/24 tagged 60, 10.66.0.0/24 the static FIB policy
# rejects and 10.67.0.0/24 the one it gives a source address.
#
# MODE=off commits the same box and the same policies without the attach
# statements, and every check that a policy moved something must go red.
MODE=${1:-on}
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out
for d in ospf:10.70.0.1/24 isis:10.71.0.1/24; do
  n=${d%%:*}; a=${d##*:}
  ip netns exec peer ip link add dum-$n type dummy
  ip netns exec peer ip addr add $a dev dum-$n
  ip netns exec peer ip link set dum-$n up
done

sysctl -p /src/appliance/etc/sysctl.conf >/dev/null 2>&1 || true
vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -25 /tmp/vpp.log; exit 1; }
use_binary_api
HX="python3 /src/src/prismash/prismash.py"

# The pair is made by the product (#182); the MTU is explicit for OSPF, as
# lab/routing.sh says.
vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
$HX -c "configure" -c "interface host-veth-vpp" -c "linux-name vppwan0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'
vppctl set interface mtu 1500 host-veth-vpp
vppctl set interface ip address host-veth-vpp 10.0.0.1/24

# ---------------- Prisma Routing ----------------
# The adjacencies are setup, written as lab/routing.sh writes them; what is
# under test is typed through the VTY below.
mkdir -p /var/run/frr /etc/frr
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
printf 'hostname prisma-routing\n' > /etc/frr/bgpd.conf
cat > /etc/frr/ospfd.conf <<'EOF'
hostname prisma-routing
router ospf
 ospf router-id 10.0.0.1
 network 10.0.0.0/24 area 0
EOF
cat > /etc/frr/isisd.conf <<'EOF'
hostname prisma-routing
interface vppwan0
 ip router isis CORE
exit
router isis CORE
 net 49.0001.0000.0000.0001.00
 is-type level-2-only
EOF
chown -R frr:frr /var/run/frr /etc/frr
start_frr ospfd isisd bgpd

# ---------------- upstream ----------------
mkdir -p /var/run/frr/peer /etc/frr/peer
printf 'hostname upstream\n' > /etc/frr/peer/zebra.conf
cat > /etc/frr/peer/bgpd.conf <<'EOF'
hostname upstream
router bgp 65001
 bgp router-id 10.0.0.2
 no bgp ebgp-requires-policy
 no bgp network import-check
 neighbor 10.0.0.1 remote-as 65000
 address-family ipv4 unicast
  network 10.9.0.0/16
  network 10.20.1.0/24
  network 10.20.2.0/24
  neighbor 10.0.0.1 activate
 exit-address-family
EOF
cat > /etc/frr/peer/ospfd.conf <<'EOF'
hostname upstream
router ospf
 ospf router-id 10.0.0.2
 network 10.0.0.0/24 area 0
 network 10.70.0.0/24 area 0
EOF
cat > /etc/frr/peer/isisd.conf <<'EOF'
hostname upstream
interface veth-out
 ip router isis CORE
exit
interface dum-isis
 ip router isis CORE
exit
router isis CORE
 net 49.0001.0000.0000.0002.00
 is-type level-2-only
EOF
# vtysh -N peer reads these; without them a `configure terminal` there left
# every later answer prefixed with the complaint.
touch /etc/frr/peer/vtysh.conf
chown -R frr:frr /var/run/frr/peer /etc/frr/peer
for d in zebra bgpd ospfd isisd; do
  ip netns exec peer /usr/lib/frr/$d -d -N peer -A 127.0.0.1 -f /etc/frr/peer/$d.conf
done
pv() { ip netns exec peer vtysh -N peer -c "$1" 2>&1; }

. /src/lab/verdict.sh

echo "=========== the session and the static routes, through the VTY ==========="
SETUP=$($HX -c "configure" \
        -c "router static" \
        -c "10.30.1.0/24 next-hop blackhole" \
        -c "10.30.2.0/24 next-hop blackhole" \
        -c "10.40.0.0/24 next-hop blackhole" \
        -c "10.50.0.0/24 next-hop blackhole" \
        -c "10.60.0.0/24 next-hop blackhole tag 60" \
        -c "10.66.0.0/24 next-hop blackhole" \
        -c "10.67.0.0/24 next-hop 10.0.0.2" -c "exit" \
        -c "router bgp 65000" \
        -c "bgp router-id 10.0.0.1" \
        -c "no bgp ebgp-requires-policy" \
        -c "bgp conditional-advertisement timer 5" \
        -c "neighbor 10.0.0.2 remote-as 65001" \
        -c "address-family ipv4 unicast" \
        -c "network 10.30.1.0/24" -c "network 10.30.2.0/24" \
        -c "network 10.50.0.0/24" \
        -c "neighbor 10.0.0.2 activate" \
        -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$SETUP" | grep -vE "^(note|warning):" | tail -3 | sed 's/^/  /'
printf '%s' "$SETUP" | grep -q "commit complete" || {
  echo "the setup did not commit - nothing below would mean anything"
  printf '%s\n' "$SETUP" | tail -20 | sed 's/^/  /'
  exit 1
}
in_fib() { vppctl show ip fib "$1" 2>/dev/null | grep -c "^$1" || true; }
n=0
while [ "$n" -lt 90 ]; do
  [ "$(in_fib 10.20.2.0/24)" -ge 1 ] && [ "$(in_fib 10.70.0.0/24)" -ge 1 ] \
    && [ "$(in_fib 10.71.0.0/24)" -ge 1 ] && break
  sleep 1; n=$((n + 1))
done
echo "  in VPP before any policy: 10.20.2.0/24=$(in_fib 10.20.2.0/24)" \
     "10.70.0.0/24=$(in_fib 10.70.0.0/24) 10.71.0.0/24=$(in_fib 10.71.0.0/24)" \
     "10.66.0.0/24=$(in_fib 10.66.0.0/24) (${n}s)"
[ "$(in_fib 10.20.2.0/24)" -ge 1 ] || { echo "the upstream's BGP routes never arrived"; exit 1; }
mkdir -p /etc/prisma-routing

echo; echo "=========== the policies, written under policy-options ==========="
POL=$($HX -c "configure" -c "policy-options" \
  -c "community NETC members 65000:44" \
  -c "community AGGC members 65000:77" \
  -c "policy-statement COND" -c "term up" \
  -c "from route-filter 10.9.0.0/16 exact" -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement NET" -c "term 1" -c "set med 44" \
  -c "set community add NETC" -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement AGG" -c "term 1" -c "set community add AGGC" \
  -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement SUP" -c "term 1" \
  -c "from route-filter 10.30.1.0/24 exact" -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement ADV" -c "term 1" \
  -c "from route-filter 10.50.0.0/24 exact" -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement TBL" \
  -c "term drop" -c "from route-filter 10.20.2.0/24 exact" -c "then reject" \
  -c "exit" \
  -c "term metric" -c "from route-filter 10.20.1.0/24 exact" -c "set metric 77" \
  -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement FIBS" \
  -c "term drop" -c "from route-filter 10.66.0.0/24 exact" -c "then reject" \
  -c "exit" \
  -c "term src" -c "from route-filter 10.67.0.0/24 exact" \
  -c "set src 10.0.0.1" -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement FIBO" -c "term 1" \
  -c "from route-filter 10.70.0.0/24 exact" -c "then reject" -c "exit" -c "exit" \
  -c "policy-statement FIBI" -c "term 1" \
  -c "from route-filter 10.71.0.0/24 exact" -c "then reject" -c "exit" -c "exit" \
  -c "policy-statement TAGGED" -c "term 1" -c "from protocol static" \
  -c "from tag 60" -c "set metric 66" -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement ODEF" -c "term 1" -c "from tag 60" -c "then accept" \
  -c "exit" -c "exit" \
  -c "policy-statement IDEF" -c "term 1" -c "set metric 50" -c "then accept" \
  -c "exit" -c "exit" \
  -c "policy-statement LP" -c "term 1" -c "set local-preference 5" -c "exit" \
  -c "exit" \
  -c "exit" -c "commit" 2>&1)
printf '%s\n' "$POL" | grep -vE "^(note|warning):" | tail -3 | sed 's/^/  /'

# Refused where they are applied, in the VTY's words, and nothing staged.
WRONG=$($HX -c "configure" -c "router bgp 65000" \
  -c "address-family ipv4 unicast" -c "table-policy LP" \
  -c "network 10.40.0.0/24 policy FIBS" \
  -c "aggregate-address 10.30.0.0/16 summary-only suppress-policy SUP" \
  -c "exit-address-family" -c "exit" -c "fib-policy bgp LP" \
  -c "commit check" 2>&1)
printf '%s\n' "$WRONG" | grep -vE "^(note|warning):" | sed 's/^/  /'

echo; echo "=========== applied, MODE=$MODE ==========="
if [ "$MODE" = off ]; then
  APPLY="(nothing applied: MODE=off)"
else
  APPLY=$($HX -c "configure" -c "router bgp 65000" \
    -c "address-family ipv4 unicast" \
    -c "neighbor 10.0.0.2 default-originate policy COND" \
    -c "network 10.40.0.0/24 policy NET" \
    -c "aggregate-address 10.30.0.0/16 policy AGG suppress-policy SUP" \
    -c "neighbor 10.0.0.2 advertise ADV non-exist COND" \
    -c "table-policy TBL" \
    -c "exit-address-family" -c "exit" \
    -c "fib-policy static FIBS" -c "fib-policy ospf FIBO" \
    -c "fib-policy isis FIBI" \
    -c "router ospf" -c "export TAGGED" \
    -c "default-information originate policy ODEF" -c "exit" \
    -c "router isis CORE" \
    -c "default-information originate ipv4 level-2 always policy IDEF" \
    -c "exit" -c "commit" 2>&1)
fi
printf "%s\n" "$APPLY" | grep -vE "^(note|warning):" | sed "s/^/  /"
vtysh -c "clear bgp ipv4 unicast * soft" >/dev/null 2>&1
sleep 20

RUN=$($HX -c "show running-config" 2>&1)
FRRRUN=$(vtysh -c "show running-config" 2>&1)
echo "  what FRR's daemons hold:"
printf '%s\n' "$FRRRUN" | grep -E "PO-|set src" | grep -vE "^ *(match|exit)|^route-map|prefix-list" | sed 's/^/    /'

echo; echo "=========== BGP: what the upstream receives ==========="
DEF1=$(pv "show bgp ipv4 unicast 0.0.0.0/0")
NETR=$(pv "show bgp ipv4 unicast 10.40.0.0/24")
AGGR=$(pv "show bgp ipv4 unicast 10.30.0.0/16")
SUP1=$(pv "show bgp ipv4 unicast 10.30.1.0/24")
SUP2=$(pv "show bgp ipv4 unicast 10.30.2.0/24")
ADV1=$(pv "show bgp ipv4 unicast 10.50.0.0/24")
for v in "$DEF1" "$NETR" "$AGGR" "$SUP1" "$SUP2" "$ADV1"; do
  printf '%s\n' "$v" | grep -E "^BGP routing|not in table|Community|metric|^  [0-9]" | head -4 | sed 's/^/  /'
done

echo; echo "=========== BGP: the table policy, in zebra and in VPP ==========="
T1=$(vtysh -c "show ip route 10.20.1.0/24" 2>&1)
T2=$(vtysh -c "show ip route 10.20.2.0/24" 2>&1)
B2=$(vtysh -c "show bgp ipv4 unicast 10.20.2.0/24" 2>&1)
printf '%s\n' "$T1" | grep -E "Routing entry|Known via" | sed 's/^/  10.20.1.0\/24: /'
printf '%s\n' "$T2" | head -3 | sed 's/^/  10.20.2.0\/24: /'
F202=$(in_fib 10.20.2.0/24); F201=$(in_fib 10.20.1.0/24)
echo "  VPP: 10.20.1.0/24=$F201 10.20.2.0/24=$F202"

echo; echo "=========== the FIB policies: static, OSPF, IS-IS ==========="
Z66=$(vtysh -c "show ip route 10.66.0.0/24" 2>&1)
printf '%s\n' "$Z66" | head -4 | sed 's/^/  zebra 10.66.0.0\/24: /'
K67=$(ip route show 10.67.0.0/24)
echo "  kernel 10.67.0.0/24: $K67"
O70=$(vtysh -c "show ip ospf route" 2>&1 | grep -A1 "10.70.0.0/24")
I71=$(vtysh -c "show isis route" 2>&1 | grep "10.71.0.0/24")
echo "  ospfd 10.70.0.0/24: $(printf '%s' "$O70" | tr '\n' ' ')"
echo "  isisd 10.71.0.0/24: $I71"
F66=$(in_fib 10.66.0.0/24); F67=$(in_fib 10.67.0.0/24)
F70=$(in_fib 10.70.0.0/24); F71=$(in_fib 10.71.0.0/24)
echo "  VPP: 10.66.0.0/24=$F66 10.67.0.0/24=$F67 10.70.0.0/24=$F70 10.71.0.0/24=$F71"

echo; echo "=========== the IGPs: a tagged static exported, and their defaults ==========="
OLSA=$(pv "show ip ospf database external")
printf '%s\n' "$OLSA" | grep -E "Link State ID|Metric:|External Route Tag" | sed 's/^/  ospf: /'
OTAG=$(pv "show ip ospf route" | grep -A1 "10.60.0.0/24")
ODEF1=$(pv "show ip ospf route")
printf '%s\n' "$ODEF1" | grep -A1 "0.0.0.0/0" | sed 's/^/  upstream ospf: /' 
IDEF1=$(pv "show isis route" | grep "0.0.0.0/0")
echo "  upstream's IS-IS default: $IDEF1"

echo; echo "=========== show policy, test policy ==========="
SHOWP=$($HX -c "show policy" 2>&1)
printf '%s\n' "$SHOWP" | grep -E "policy-statement|applied" | sed 's/^/  /'
TESTP=$($HX -c "test policy FIBS 2001:db8::/32" 2>&1)
printf '%s\n' "$TESTP" | sed 's/^/  /'

echo; echo "=========== the condition goes: the upstream withdraws 10.9.0.0/16 ==========="
ip netns exec peer vtysh -N peer -c "configure terminal" -c "router bgp 65001" \
  -c "address-family ipv4 unicast" -c "no network 10.9.0.0/16" >/dev/null 2>&1
# And the tagged static, which the OSPF default waited on.
GONE=$($HX -c "configure" -c "router static" \
  -c "no 10.60.0.0/24 next-hop blackhole tag 60" -c "exit" -c "commit" 2>&1)
printf '%s\n' "$GONE" | grep -vE "^(note|warning):" | tail -1 | sed 's/^/  /'
n=0
while [ "$n" -lt 90 ]; do
  pv "show bgp ipv4 unicast 10.50.0.0/24" | grep -q "^BGP routing table entry" \
    && ! pv "show bgp ipv4 unicast 0.0.0.0/0" | grep -q "^BGP routing table entry" \
    && break
  sleep 1; n=$((n + 1))
done
# The OSPF default: its LSA flushed, so gone from the upstream's OSPF routes.
m=0
while [ "$m" -lt 60 ]; do
  pv "show ip ospf route" | grep -q "10.70.0.0/24" \
    && ! pv "show ip ospf route" | grep -q "0.0.0.0/0" && break
  sleep 1; m=$((m + 1))
done
DEF2=$(pv "show bgp ipv4 unicast 0.0.0.0/0")
ADV2=$(pv "show bgp ipv4 unicast 10.50.0.0/24")
ODEF2=$(pv "show ip ospf route")
printf '%s\n' "$DEF2" | head -2 | sed 's/^/  0.0.0.0\/0: /'
printf '%s\n' "$ADV2" | head -2 | sed 's/^/  10.50.0.0\/24: /'
echo "  (BGP ${n}s, OSPF ${m}s)"
printf '%s\n' "$ODEF2" | grep -E "0.0.0.0/0|10.60.0.0/24" | sed 's/^/  ospf: /' 

echo; echo "=========== IS-IS: the default follows the policy ==========="
RJ=$($HX -c "configure" -c "policy-options" -c "policy-statement IDEF" \
  -c "term 1" -c "then reject" -c "exit" -c "exit" -c "exit" -c "commit" 2>&1)
printf '%s\n' "$RJ" | grep -vE "^(note|warning):" | tail -1 | sed 's/^/  /'
n=0
while [ "$n" -lt 60 ]; do
  pv "show isis route" | grep -q "10.71.0.0/24" \
    && ! pv "show isis route" | grep -q "0.0.0.0/0" && break
  sleep 1; n=$((n + 1))
done
ISR2=$(pv "show isis route")
IDEF2=$(printf '%s\n' "$ISR2" | grep "0.0.0.0/0")
echo "  upstream's IS-IS default after reject: '${IDEF2}' (${n}s)"

echo; echo "=========== VERDICT ==========="
expect_in "the policies commit" "commit complete" "$POL"
expect_said "a table policy sets metric and next hop, not local-preference" \
  "set local-preference does nothing on table-policy" "$WRONG"
expect_said "set src is refused outside a fib-policy" \
  "set src is for a fib-policy" "$WRONG"
expect_said "summary-only beside a suppress-policy is refused" \
  "one or the other" "$WRONG"
expect_said "a FIB policy takes no BGP action" \
  "set local-preference is for BGP routes" "$WRONG"
# #539: each daemon holds what it was given.
expect_in "bgpd holds the conditional default-originate" \
  "neighbor 10.0.0.2 default-originate route-map PO-COND-BGP-DEFAULT" "$FRRRUN"
expect_in "bgpd holds the network's policy" \
  "network 10.40.0.0/24 route-map PO-NET-BGP-NETWORK" "$FRRRUN"
expect_in "bgpd holds the aggregate's two policies" \
  "aggregate-address 10.30.0.0/16 route-map PO-AGG-BGP-AGGREGATE suppress-map PO-SUP-BGP-SUPPRESS" "$FRRRUN"
expect_in "bgpd holds the conditional advertisement" \
  "neighbor 10.0.0.2 advertise-map PO-ADV-BGP-ADVERTISE non-exist-map PO-COND-BGP-CONDITION" "$FRRRUN"
expect_in "bgpd holds the table policy" "table-map PO-TBL-BGP-TABLE" "$FRRRUN"
expect_in "  and the maps a neighbor names, which bgpd warned about" \
  "route-map PO-COND-BGP-DEFAULT permit 10" "$FRRRUN"
expect_in "  the advertise-map's" "route-map PO-ADV-BGP-ADVERTISE permit 10" "$FRRRUN"
expect_not_in "  and the commit does not warn about them" "does not exist" "$APPLY"
expect_in "zebra holds the static FIB policy" \
  "ip protocol static route-map PO-FIBS-FIB-STATIC" "$FRRRUN"
expect_in "  and IPv6's" "ipv6 protocol static route-map PO-FIBS-FIB-STATIC" "$FRRRUN"
expect_in "zebra holds the OSPF one, IPv6's as ospf6" \
  "ipv6 protocol ospf6 route-map PO-FIBO-FIB-OSPF" "$FRRRUN"
expect_in "zebra holds the IS-IS one" "ip protocol isis route-map PO-FIBI-FIB-ISIS" "$FRRRUN"
expect_in "  and the map's set src" " set src 10.0.0.1" "$FRRRUN"
expect_in "ospfd holds default-information with the policy" \
  "default-information originate route-map PO-ODEF-OSPF-DEFAULT" "$FRRRUN"
expect_in "isisd holds default-information with the policy" \
  "default-information originate ipv4 level-2 always route-map PO-IDEF-ISIS-DEFAULT" "$FRRRUN"
expect_in "the box reads each back: show running-config" \
  "table-map PO-TBL-BGP-TABLE" "$RUN"
expect_in "show policy says where: default-originate" \
  "applied: neighbor 10.0.0.2 default-originate, neighbor 10.0.0.2 advertise" "$SHOWP"
expect_in "  fib-policy" "applied: fib-policy static" "$SHOWP"
expect_in "  table-policy" "applied: table-policy" "$SHOWP"
expect_in "test policy: set src 10.0.0.1 makes the term IPv4's" \
  "the prefix is not inet" "$TESTP"
expect_in "default-originate: sent while the condition's route is there" \
  "BGP routing table entry for 0.0.0.0/0" "$DEF1"
expect_in "network with a policy: its MED" "metric 44" "$NETR"
expect_in "  and its community" "65000:44" "$NETR"
expect_in "aggregate-address with a policy: the aggregate carries its community" \
  "65000:77" "$AGGR"
expect_in "suppress-policy: the more specific route it accepts is held back" \
  "not in table" "$SUP1"
expect_in "  and the one it does not is sent" "BGP routing table entry for 10.30.2.0/24" "$SUP2"
expect_in "advertise non-exist: held back while the condition's route is there" \
  "not in table" "$ADV1"
expect_in "advertise non-exist: sent once it is gone" \
  "BGP routing table entry for 10.50.0.0/24" "$ADV2"
expect_in "default-originate: withdrawn once it is gone" "not in table" "$DEF2"
expect_in "table-policy: the rejected route stays in BGP" \
  "BGP routing table entry for 10.20.2.0/24" "$B2"
expect "  and never reaches VPP" test "$F202" -eq 0
expect_in "table-policy: set metric reaches zebra" "metric 77" "$T1"
expect "  and the route VPP" test "$F201" -ge 1
expect "fib-policy static: the rejected static never reaches VPP" test "$F66" -eq 0
expect_in "fib-policy static: set src, on the kernel's route" "src 10.0.0.1" "$K67"
expect "  and VPP has the route" test "$F67" -ge 1
expect "fib-policy ospf: OSPF's route never reaches VPP" test "$F70" -eq 0
expect_in "  while ospfd has it" "10.70.0.0/24" "$O70"
expect "fib-policy isis: IS-IS's route never reaches VPP" test "$F71" -eq 0
expect_in "  while isisd has it" "10.71.0.0/24" "$I71"
expect_in "a static matched by its tag, exported into OSPF with metric 66" \
  "10.60.0.0/24" "$OTAG"
expect_in "OSPF default-information: originated while the tagged route is there" \
  "0.0.0.0/0" "$ODEF1"
expect_in "  (the upstream's OSPF answered)" "10.70.0.0/24" "$ODEF2"
expect_not_in "  and not once it is gone" "0.0.0.0/0" "$ODEF2"
expect_in "IS-IS default-information always: originated, as the policy accepts" \
  "0.0.0.0/0" "$IDEF1"
expect "  carrying the policy's metric: 50, and the link's 10" \
  test "$(printf '%s' "$IDEF1" | awk '{print $3}')" = 60
expect_in "  (the upstream's IS-IS answered)" "10.71.0.0/24" "$ISR2"
expect "  and withdrawn when the policy rejects" test -z "$IDEF2"
verdict
