#!/bin/sh
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# OFF-RED-AT-LEAST: 27
# Routing policy, phase A (#535): the new conditions, actions and flow,
# asserted on real sessions - the box's BGP table, the neighbour's table, the
# VPP FIB and a packet - not on the text of a configuration.
#
# lab/policy.sh's topology: two sessions to one peer (AS 65001) and a third AS
# (65002) behind it. The peer marks what it sends with route-maps of its own -
# a lab fixture, not the product - so each new `from` has a route to select:
#
#   10.10.1.0/24   large community 65001:1:1
#   10.10.2.0/24   extended community rt:65001:100
#   10.10.3.0/24   origin incomplete
#   10.10.4.0/24   MED 50
#   10.10.5.0/24   AS path 65001 64496 64512: 64496 is a documentation AS
#                  (RFC 5398) and public; 65001 and 64512 are private
#   10.10.10.0/24  plain, for as-path-prepend last-as
#   10.10.6.0/24   the chain: the first policy marks it, the second decides
#   10.10.7.0/24   and 10.10.7.128/25: `from policy`, a subroutine that rejects
#                  the /25
#   10.10.8.0/24   a third-party next hop, 10.0.0.3, on the first session
#   10.10.9.0/24   nothing matches it: the second policy's default-action
#   10.10.66.66/32 community 65535:666, the RTBH request
#   198.51.100.0/24 from AS 65002, which the ROA gives to AS 64999: invalid
#
# MODE=off commits the same box, the same policies and the same RPKI cache,
# without the import and export lines, and every check that a policy moved
# something must go red.
MODE=${1:-on}
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip link add veth2-out type veth peer name veth2-vpp 2>/dev/null
ip link set veth2-vpp up

ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out
ip link set veth2-out netns peer
ip netns exec peer ip link set veth2-out up
ip netns exec peer ip addr add 10.1.0.2/24 dev veth2-out
ip netns exec peer ip link add dum-bgp type dummy
ip netns exec peer ip addr add 203.0.113.1/24 dev dum-bgp
ip netns exec peer ip link set dum-bgp up

ip netns add origin 2>/dev/null
ip link add veth3-peer type veth peer name veth3-origin 2>/dev/null
ip link set veth3-peer netns peer
ip link set veth3-origin netns origin
ip netns exec peer ip link set veth3-peer up
ip netns exec peer ip addr add 10.2.0.1/24 dev veth3-peer
ip netns exec origin ip link set lo up
ip netns exec origin ip link set veth3-origin up
ip netns exec origin ip addr add 10.2.0.2/24 dev veth3-origin
ip netns exec origin ip link add dum-origin type dummy
ip netns exec origin ip addr add 198.51.100.1/24 dev dum-origin
ip netns exec origin ip link set dum-origin up

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -25 /tmp/vpp.log; exit 1; }
use_binary_api
HX="python3 /src/src/prismash/prismash.py"

# The pairs are made by the product (#182), as in lab/policy.sh; the MTU is
# explicit for the reason that lab gives.
vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
$HX -c "configure" -c "interface host-veth-vpp" -c "linux-name vppwan0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'
vppctl set interface mtu 1500 host-veth-vpp
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
vppctl create host-interface name veth2-vpp
vppctl set interface state host-veth2-vpp up
$HX -c "configure" -c "interface host-veth2-vpp" -c "linux-name vppwan1" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'
vppctl set interface mtu 1500 host-veth2-vpp
vppctl set interface ip address host-veth2-vpp 10.1.0.1/24

# ---------------- Prisma Routing ----------------
mkdir -p /var/run/frr /etc/frr
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
printf 'hostname prisma-routing\n' > /etc/frr/bgpd.conf
chown -R frr:frr /var/run/frr /etc/frr
# The RPKI module and a cache, as lab/bgp-policy.sh has them: a cache is a
# service, so the lab runs one. ospfd for metric-type, read off the box's own
# external LSAs; no adjacency is formed.
apt-get install -y -qq frr-rpki-rtrlib stayrtr >/dev/null 2>&1
start_frr bgpd ospfd
cat > /tmp/vrp.json <<'VRP'
{"roas":[
 {"prefix":"203.0.113.0/24","maxLength":24,"asn":"AS65001"},
 {"prefix":"198.51.100.0/24","maxLength":24,"asn":"AS64999"}
]}
VRP
stayrtr -cache /tmp/vrp.json -bind 127.0.0.1:8282 -checktime=false \
  > /tmp/stayrtr.log 2>&1 &

# ---------------- upstream ----------------
mkdir -p /var/run/frr/peer /etc/frr/peer
printf 'hostname upstream\n' > /etc/frr/peer/zebra.conf
cat > /etc/frr/peer/bgpd.conf <<'EOF'
hostname upstream
bgp large-community-list standard X permit 65001:0:0
ip prefix-list P1 permit 10.10.1.0/24
ip prefix-list P2 permit 10.10.2.0/24
ip prefix-list P3 permit 10.10.3.0/24
ip prefix-list P4 permit 10.10.4.0/24
ip prefix-list P5 permit 10.10.5.0/24
ip prefix-list P8 permit 10.10.8.0/24
ip prefix-list P66 permit 10.10.66.66/32
route-map MARK permit 10
 match ip address prefix-list P1
 set large-community 65001:1:1
exit
route-map MARK permit 20
 match ip address prefix-list P2
 set extcommunity rt 65001:100
exit
route-map MARK permit 30
 match ip address prefix-list P3
 set origin incomplete
exit
route-map MARK permit 40
 match ip address prefix-list P4
 set metric 50
exit
route-map MARK permit 50
 match ip address prefix-list P5
 set as-path prepend 64496 64512
exit
route-map MARK permit 60
 match ip address prefix-list P8
 set ip next-hop 10.0.0.3
exit
route-map MARK permit 70
 match ip address prefix-list P66
 set community 65535:666
exit
route-map MARK permit 100
exit
router bgp 65001
 bgp router-id 10.0.0.2
 no bgp ebgp-requires-policy
 no bgp network import-check
 neighbor 10.0.0.1 remote-as 65000
 neighbor 10.1.0.1 remote-as 65000
 neighbor 10.2.0.2 remote-as 65002
 address-family ipv4 unicast
  network 203.0.113.0/24
  network 10.10.1.0/24
  network 10.10.2.0/24
  network 10.10.3.0/24
  network 10.10.4.0/24
  network 10.10.5.0/24
  network 10.10.6.0/24
  network 10.10.7.0/24
  network 10.10.7.128/25
  network 10.10.8.0/24
  network 10.10.9.0/24
  network 10.10.10.0/24
  network 10.10.66.66/32
  neighbor 10.0.0.1 activate
  neighbor 10.1.0.1 activate
  neighbor 10.2.0.2 activate
  neighbor 10.0.0.1 route-map MARK out
  neighbor 10.1.0.1 route-map MARK out
 exit-address-family
EOF
chown -R frr:frr /var/run/frr/peer /etc/frr/peer
for d in zebra bgpd; do
  ip netns exec peer /usr/lib/frr/$d -d -N peer -A 127.0.0.1 -f /etc/frr/peer/$d.conf
done

mkdir -p /var/run/frr/origin /etc/frr/origin
printf 'hostname origin\n' > /etc/frr/origin/zebra.conf
cat > /etc/frr/origin/bgpd.conf <<'EOF'
hostname origin
router bgp 65002
 bgp router-id 10.2.0.2
 no bgp ebgp-requires-policy
 neighbor 10.2.0.1 remote-as 65001
 address-family ipv4 unicast
  network 198.51.100.0/24
  neighbor 10.2.0.1 activate
 exit-address-family
EOF
chown -R frr:frr /var/run/frr/origin /etc/frr/origin
for d in zebra bgpd; do
  ip netns exec origin /usr/lib/frr/$d -d -N origin -A 127.0.0.1 -f /etc/frr/origin/$d.conf
done

. /src/lab/verdict.sh

# ---------------- setup through the VTY: sessions, cache, OSPF ----------------
# Setup, not assertions (lab/policy.sh says why), and loud when it fails.
echo "=========== the sessions, the RPKI cache and OSPF, through the VTY ==========="
SETUP=$($HX -c "configure" \
        -c "rpki" -c "rpki cache tcp 127.0.0.1 8282 preference 1" -c "exit" \
        -c "router static" -c "100.64.50.0/24 next-hop blackhole" \
        -c "0.0.0.0/0 next-hop 10.0.0.2" -c "exit" \
        -c "router ospf" -c "ospf router-id 10.0.0.1" \
        -c "network 10.0.0.0/24 area 0" -c "exit" \
        -c "router bgp 65000" \
        -c "bgp router-id 10.0.0.1" \
        -c "no bgp ebgp-requires-policy" \
        -c "neighbor 10.0.0.2 remote-as 65001" \
        -c "neighbor 10.1.0.2 remote-as 65001" \
        -c "address-family ipv4 unicast" \
        -c "network 100.64.50.0/24" \
        -c "neighbor 10.0.0.2 activate" \
        -c "neighbor 10.1.0.2 activate" \
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
  [ "$(in_fib 10.10.9.0/24)" -ge 1 ] && [ "$(in_fib 198.51.100.0/24)" -ge 1 ] && break
  sleep 1; n=$((n + 1))
done
echo "  10.10.9.0/24=$(in_fib 10.10.9.0/24)  198.51.100.0/24=$(in_fib 198.51.100.0/24)  (${n}s)"
[ "$(in_fib 10.10.9.0/24)" -ge 1 ] || { echo "the peer's routes never arrived"; exit 1; }
mkdir -p /etc/prisma-routing

echo; echo "=========== the policies, written under policy-options ==========="
POL=$($HX -c "configure" -c "policy-options" \
  -c "community BH members 65535:666" \
  -c "large-community PEERLC members 65001:1:1" \
  -c "large-community LC members 65000:1:2" \
  -c "extended-community PEEREC members rt:65001:100" \
  -c "extended-community EC members rt:65000:100 soo:65000:200" \
  -c "policy-statement BOGONS" -c "default-action accept" \
  -c "term 1" -c "from route-filter 10.10.7.128/25 exact" -c "then reject" \
  -c "exit" -c "exit" \
  -c "policy-statement IN-A" \
  -c "term rtbh" -c "from community BH" \
  -c "from route-filter 0.0.0.0/0 upto /32" \
  -c "set next-hop discard" -c "then accept" -c "exit" \
  -c "term rpki" -c "from validation-state invalid" -c "then reject" -c "exit" \
  -c "term lc" -c "from large-community PEERLC" \
  -c "set large-community delete PEERLC" -c "set large-community add LC" \
  -c "then accept" -c "exit" \
  -c "term ec" -c "from extended-community PEEREC" \
  -c "set extended-community add EC" -c "then accept" -c "exit" \
  -c "term orig" -c "from origin incomplete" -c "set origin egp" \
  -c "then accept" -c "exit" \
  -c "term med" -c "from route-filter 10.10.4.0/24 exact" -c "set med +10" \
  -c "set weight 100" -c "then accept" -c "exit" \
  -c "term priv" -c "from route-filter 10.10.5.0/24 exact" \
  -c "set as-path remove-private" -c "then accept" -c "exit" \
  -c "term last" -c "from route-filter 10.10.10.0/24 exact" \
  -c "set as-path-prepend last-as 2" -c "then accept" -c "exit" \
  -c "term nh" -c "from next-hop 10.0.0.3" -c "set next-hop peer-address" \
  -c "then accept" -c "exit" \
  -c "term peer" -c "from neighbor 10.1.0.2" -c "from family inet" \
  -c "from route-filter 203.0.113.0/24 exact" \
  -c "set local-preference +50" -c "then next policy" -c "exit" \
  -c "term chain" -c "from route-filter 10.10.6.0/24 exact" \
  -c "set local-preference 170" -c "then next policy" -c "exit" \
  -c "term sub" -c "from policy BOGONS" \
  -c "from route-filter 10.10.7.0/24 orlonger" -c "set weight 7" \
  -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement IN-B" -c "default-action reject" \
  -c "term decide" -c "from local-preference 170" -c "set weight 200" \
  -c "then accept" -c "exit" \
  -c "term upstream" -c "from route-filter 203.0.113.0/24 exact" \
  -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement OUT" \
  -c "term own" -c "from route-filter 100.64.50.0/24 exact" \
  -c "set large-community set LC" -c "set extended-community set EC" \
  -c "set med 33" -c "set next-hop self" -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement OSPF-EXT" \
  -c "term 1" -c "from protocol static" \
  -c "from route-filter 100.64.50.0/24 exact" -c "set metric-type 1" \
  -c "then accept" -c "exit" -c "exit" \
  -c "exit" -c "commit" 2>&1)
printf '%s\n' "$POL" | grep -vE "^(note|warning):" | tail -3 | sed 's/^/  /'

# Refused at the neighbour, in the VTY's words, and nothing staged.
WRONG=$($HX -c "configure" -c "router bgp 65000" \
  -c "address-family ipv4 unicast" -c "neighbor 10.0.0.2 import OUT" \
  -c "neighbor 10.0.0.2 export OSPF-EXT" -c "exit-address-family" \
  -c "exit" -c "commit check" 2>&1)
printf '%s\n' "$WRONG" | grep -E "error|set " | sed 's/^/  /'

# RTBH on a single-hop eBGP session needs disable-connected-check, or FRR
# never installs the discard (policy.discard_findings measured it): refused
# at commit, with the statement that fixes it.
NOKNOB=$($HX -c "configure" -c "router bgp 65000" \
  -c "address-family ipv4 unicast" -c "neighbor 10.0.0.2 import [ IN-A IN-B ]" \
  -c "exit-address-family" -c "exit" -c "commit check" 2>&1)
printf '%s\n' "$NOKNOB" | grep -B1 -A3 "discard" | sed 's/^/  /'

echo; echo "=========== applied, MODE=$MODE ==========="
if [ "$MODE" = off ]; then
  APPLY="(nothing applied: MODE=off)"
else
  APPLY=$($HX -c "configure" -c "router bgp 65000" \
    -c "address-family ipv4 unicast" \
    -c "neighbor 10.0.0.2 import [ IN-A IN-B ]" \
    -c "neighbor 10.1.0.2 import [ IN-A IN-B ]" \
    -c "exit-address-family" \
    -c "neighbor 10.0.0.2 disable-connected-check" \
    -c "neighbor 10.1.0.2 disable-connected-check" \
    -c "address-family ipv4 unicast" \
    -c "neighbor 10.0.0.2 export OUT" \
    -c "exit-address-family" -c "exit" \
    -c "router ospf" -c "export OSPF-EXT" -c "exit" -c "commit" 2>&1)
fi
printf "%s\n" "$APPLY" | grep -vE "^(note|warning):" | sed "s/^/  /"
vtysh -c "clear bgp ipv4 unicast * soft" >/dev/null 2>&1
sleep 15

RUN=$($HX -c "show running-config" 2>&1)
printf '%s\n' "$RUN" | grep -E "route-map PO-|^ip(v6)? route .*tag" | sed 's/^/  /'
FRRRUN=$(vtysh -c "show running-config" 2>&1)

path() { vtysh -c "show bgp ipv4 unicast $1" 2>&1; }

echo; echo "=========== RTBH: 10.10.66.66/32, community 65535:666 ==========="
RTBH=$(path 10.10.66.66/32)
printf '%s\n' "$RTBH" | grep -E "192.0.2.1|10.0.0.2|Community" | head -4 | sed 's/^/  bgp: /'
vtysh -c "show bgp ipv4 unicast 10.10.66.66/32" 2>&1 | grep -E "valid|inaccessible|best" | sed 's/^/  bgp: /'
vtysh -c "show ip route 10.10.66.66/32" -c "show ip route 192.0.2.1/32" 2>&1 | sed 's/^/  zebra: /'
ip route show 10.10.66.66/32 | sed 's/^/  kernel: /'
ip route show table all type blackhole | sed 's/^/  kernel: /'
vppctl show ip fib 192.0.2.1/32 2>&1 | tr -d '\r' | head -6 | sed 's/^/  vpp 192.0.2.1: /'
RTBHFIB=$(vppctl show ip fib 10.10.66.66/32 2>&1 | tr -d '\r')
printf '%s\n' "$RTBHFIB" | head -8 | sed 's/^/  vpp: /'
# And a packet: the peer sends one to the address through the box, the way a
# DDoS toward a customer arrives. The peer's own route for it points at VPP,
# and the box has a default route back to the peer: a /32 that did not
# become a drop would be forwarded, not merely unrouted.
ip netns exec peer ip route replace 10.10.66.66/32 via 10.0.0.1
vppctl clear interfaces >/dev/null 2>&1
vppctl clear errors >/dev/null 2>&1
ip netns exec peer ping -c 5 -i 0.2 -W 1 10.10.66.66 >/dev/null 2>&1
DROPS=$(vppctl show errors 2>&1 | tr -d '\r')
printf '%s\n' "$DROPS" | grep -iE "drop|blackhol|null" | head -5 | sed 's/^/  vpp: /'
TXOUT=$(vppctl show interface host-veth-vpp 2>&1 | awk '/tx packets/{print $3}')
echo "  packets the box sent back out the first link: ${TXOUT:-0}"

echo; echo "=========== validation-state invalid ==========="
INV=$(path 198.51.100.0/24)
printf '%s\n' "$INV" | grep -E "Paths|from 10\.|Not advertised|not found" | head -4 | sed 's/^/  /'
VALID=$(path 203.0.113.0/24)

echo; echo "=========== large and extended communities, origin, med, weight ==========="
LC=$(path 10.10.1.0/24); EC=$(path 10.10.2.0/24); ORG=$(path 10.10.3.0/24)
MED=$(path 10.10.4.0/24); PRIV=$(path 10.10.5.0/24); NH=$(path 10.10.8.0/24)
LAST=$(path 10.10.10.0/24)
for v in "$LC" "$EC" "$ORG" "$MED" "$PRIV" "$NH" "$LAST"; do
  printf '%s\n' "$v" | grep -E "^BGP routing|Large Community|Extended Community|Origin|^  [0-9]|from 10\." | head -6 | sed 's/^/  /'
done
NHVIA=$(printf '%s\n' "$NH" | grep -E "from 10.0.0.2" | head -1)

echo; echo "=========== from neighbor, local-preference +n ==========="
printf '%s\n' "$VALID" | grep -E "from 10\.|Local|localpref" | sed 's/^/  /'
LP_SECOND=$(printf '%s\n' "$VALID" | awk '/from 10.1.0.2/{f=1} f && /localpref/{print; exit}')
LP_FIRST=$(printf '%s\n' "$VALID" | awk '/from 10.0.0.2/{f=1} f && /localpref/{print; exit}')
echo "  via 10.1.0.2: $LP_SECOND"
echo "  via 10.0.0.2: $LP_FIRST"

echo; echo "=========== the chain: the second policy decides ==========="
CH=$(path 10.10.6.0/24)
printf '%s\n' "$CH" | grep -E "from 10\.|localpref" | head -4 | sed 's/^/  /'
NONE=$(path 10.10.9.0/24)
printf '%s\n' "$NONE" | head -3 | sed 's/^/  10.10.9.0\/24: /'

echo; echo "=========== from policy: a subroutine ==========="
S7=$(path 10.10.7.0/24); S25=$(path 10.10.7.128/25)
printf '%s\n' "$S7" | grep -E "from 10\.|weight" | head -4 | sed 's/^/  /'
printf '%s\n' "$S25" | head -3 | sed 's/^/  10.10.7.128\/25: /'

echo; echo "=========== export: what the peer receives ==========="
PEER=$(ip netns exec peer vtysh -N peer -c "show bgp ipv4 unicast 100.64.50.0/24" 2>&1)
printf '%s\n' "$PEER" | sed -n '1,14p' | sed 's/^/  peer: /'

echo; echo "=========== metric-type, in the box's own external LSA ==========="
LSA=$(vtysh -c "show ip ospf database external self-originate" 2>&1)
printf '%s\n' "$LSA" | grep -E "Link State ID|Metric Type" | sed 's/^/  /'

echo; echo "=========== show policy, test policy ==========="
SHOWP=$($HX -c "show policy IN-B" 2>&1)
printf '%s\n' "$SHOWP" | sed 's/^/  /'
TESTP=$($HX -c "test policy IN-B 10.10.9.0/24" 2>&1)
printf '%s\n' "$TESTP" | sed 's/^/  /'

echo; echo "=========== VERDICT ==========="
expect_in "the policies commit" "commit complete" "$POL"
expect_said "next-hop self on an import is refused at the neighbour" \
  "set next-hop self is for an export to a BGP neighbor" "$WRONG"
expect_said "  and metric-type on a BGP export" \
  "set metric-type is for an export into OSPF" "$WRONG"
expect_said "show policy reads the default-action back" "default-action reject" "$SHOWP"
expect_said "test policy: the default-action decides what no term did" \
  "result: reject" "$TESTP"
expect_in "a chain applies as one map the box compiled" \
  "neighbor 10.0.0.2 route-map PO-IN-A+IN-B-IN in" "$RUN"
expect_in "  and FRR holds it" "route-map PO-IN-A+IN-B-IN permit 10" "$FRRRUN"
expect_in "  and show policy says where" "neighbor 10.0.0.2 import [ IN-A IN-B ]" "$SHOWP"
expect_in "RTBH: the /32 with 65535:666 takes the discard next hop" "192.0.2.1" "$RTBH"
expect_said "a single-hop eBGP import that discards needs disable-connected-check" \
  "add 'neighbor 10.0.0.2 disable-connected-check'" "$NOKNOB"
expect_in "  and VPP holds the /32 itself" "10.10.66.66/32 fib" "$RTBHFIB"
expect_in "  as a drop" "dpo-drop" "$RTBHFIB"
expect "  and the packets sent to it never leave the box" test "${TXOUT:-0}" -lt 5
expect_not_in "validation-state invalid rejects the route ROA calls invalid" \
  "from 10." "$INV"
expect_in "  and the valid one stays" "from 10.0.0.2" "$VALID"
expect_in "from large-community, set large-community add" "65000:1:2" "$LC"
expect_not_in "  and set large-community delete takes the peer's" "65001:1:1" "$LC"
expect_in "from extended-community, set extended-community add" \
  "RT:65000:100" "$EC"
expect_in "  both members" "SoO:65000:200" "$EC"
expect_in "from origin incomplete, set origin egp" "Origin EGP" "$ORG"
expect_in "set med +10 on a MED of 50" "metric 60" "$MED"
expect_in "set weight" "weight 100" "$MED"
# Every private AS, the peer's own 65001 too: `as-path exclude` takes each
# ASN the list matches, wherever it stands. The lab's ASNs are private, so the
# public one has to be there for the path to be anything at all.
expect_not_in "set as-path remove-private takes 64512 out" "64512" "$PRIV"
expect_in "  and leaves the public AS" "64496" "$PRIV"
expect_not_in "  and the private 65001 too" "65001" "$PRIV"
expect_in "set as-path-prepend last-as 2 repeats 65001" "65001 65001 65001" "$LAST"
expect_in "from next-hop 10.0.0.3, set next-hop peer-address" "10.0.0.2 from 10.0.0.2" "$NHVIA"
expect_in "from neighbor 10.1.0.2, set local-preference +50" "localpref 150" "$LP_SECOND"
expect_not_in "  and the other session's path is untouched" "localpref 150" "$LP_FIRST"
expect_in "the chain: the second policy decides on what the first set" \
  "weight 200" "$CH"
expect_in "  and the second policy's default-action rejects what neither took" \
  "Network not in table" "$NONE"
expect_in "from policy: the subroutine's accept continues into the term" \
  "weight 7" "$S7"
expect_in "  and its reject rejects" "Network not in table" "$S25"
expect_in "export: set large-community set, on the peer" "65000:1:2" "$PEER"
expect_in "  set extended-community set" "RT:65000:100" "$PEER"
expect_in "  set med" "metric 33" "$PEER"
expect_in "metric-type 1 on the export into OSPF" "Metric Type: 1" "$LSA"
verdict
