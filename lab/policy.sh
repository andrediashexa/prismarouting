#!/bin/sh
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# OFF-RED-AT-LEAST: 5
# Routing policy, Junos-style (#458), asserted on the tables that forward and
# on the neighbour's own table - not on the text of a configuration.
#
# The topology is lab/bgp-policy.sh's: two sessions to one peer (AS 65001),
# carrying the same prefixes, and a third AS (65002) behind it.
#
#  [netns origin] AS 65002   [netns peer] AS 65001    [default] AS 65000
#   dum-origin 198.51.100.0/24   dum-bgp 203.0.113.0/24
#
# Each policy is written under `policy-options` and applied with `neighbor X
# import|export <policy>`, the way an operator types it; the box compiles it to
# FRR route-maps. MODE=off commits the same box without the import and export
# lines, and every check that a policy moved something must go red.
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

# The BPA plugin for the binary API. Core VPP messages work without it, but a
# pool goes through bpa_pool_add_del, and the check that a pool's public prefix
# is announced needs both halves present.
use_binary_api
HX="python3 /src/src/prismash/prismash.py"

vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
# Both pairs below are made by the PRODUCT (#182). `vppctl lcp create` stood on
# these two lines, and while it did, every policy this lab asserts was measured
# on a box somebody had paired by hand - on an installed appliance FRR had no
# interface for either port and no session could come up at all. Moved up here
# with use_binary_api, which has to come first for any commit to work.
$HX -c "configure" -c "interface host-veth-vpp" -c "linux-name vppwan0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'

# The TAP inherits MTU 9000 while the wire is at 1500. BGP is TCP over that
# TAP, so the kernel derives its MSS from 9000 and hands VPP segments the veth
# cannot carry: the session opens and then stalls on the first full-sized
# UPDATE, which reads as a peer that will not converge rather than as an MTU.
# It has to be explicit, never inherited.
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
cat > /etc/frr/zebra.conf <<'EOF'
hostname prisma-routing
EOF
# Deliberately EMPTY of BGP. Both sessions are configured through the VTY
# further down, so every table a policy below moves is evidence about a peering
# the VTY built - not one a file written by the lab did (#142).
cat > /etc/frr/bgpd.conf <<'EOF'
hostname prisma-routing
EOF
chown -R frr:frr /var/run/frr /etc/frr
# These are the daemons THIS LAB arranges, not what the product ships: an
# installed box inherits Debian's /etc/frr/daemons, where bgpd is "no" (#99).
# Starting them by hand here is what let that go unnoticed, so it is said out
# loud rather than left as a list somebody skims.
# RPKI is a loadable MODULE, and `bgpd --version` reports --enable-rpki whether
# or not it is there. Installed from the same repository and at the same
# version as frr itself, so this adds no new source. Without it every RPKI
# command answers "bgpd is not running", because bgpd refuses to start when
# told to load a module it cannot find - a misleading symptom worth knowing
# about, and the reason the boot self-test checks for it by name.
apt-get install -y -qq frr-rpki-rtrlib stayrtr >/dev/null 2>&1
# This lab worked out the mgmtd/staticd requirement; it now lives in
# lab/lib.sh so the other six labs cannot go on not knowing it. Sourced here
# rather than where it was, because FRR starts before that point.
# ospfd is here for ONE assertion and forms no adjacency: the pool-announce
# check has to tell `redistribute static` under `router bgp` from the same six
# characters under `router ospf`, and `commit check` refuses a `router ospf`
# statement outright when ospfd is not running (#99) - so without the daemon
# that case would be asserting a daemon refusal rather than the scope. No
# interface is put in an area, so this costs no convergence, which is what the
# header means by "no OSPF": no adjacency, not no daemon.
start_frr bgpd

# ---------------- upstream ----------------
mkdir -p /var/run/frr/peer /etc/frr/peer
cat > /etc/frr/peer/zebra.conf <<'EOF'
hostname upstream
EOF
cat > /etc/frr/peer/bgpd.conf <<'EOF'
hostname upstream
router bgp 65001
 bgp router-id 10.0.0.2
 no bgp ebgp-requires-policy
 neighbor 10.0.0.1 remote-as 65000
 neighbor 10.1.0.1 remote-as 65000
 neighbor 10.2.0.2 remote-as 65002
 address-family ipv4 unicast
  network 203.0.113.0/24
  neighbor 10.0.0.1 activate
  neighbor 10.1.0.1 activate
  neighbor 10.2.0.2 activate
 exit-address-family
EOF
chown -R frr:frr /var/run/frr/peer /etc/frr/peer
for d in zebra bgpd; do
  ip netns exec peer /usr/lib/frr/$d -d -N peer -A 127.0.0.1 -f /etc/frr/peer/$d.conf
done

# ---------------- the third AS, one hop behind the peer ----------------
mkdir -p /var/run/frr/origin /etc/frr/origin
cat > /etc/frr/origin/zebra.conf <<'EOF'
hostname origin
EOF
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

# ---------------- the two sessions, configured through the VTY ----------------
#
# Setup here, and NOT an assertion: that the VTY can commit a working BGP
# session is asserted once, in lab/routing.sh. Asserting it a second time here
# would put a check in the suite's count for something nothing new tested.
#
# It still has to be loud when it fails. Every section below reads a table
# these two sessions fill, and a policy applied to a session that never came up
# moves nothing - which is indistinguishable from a policy that does nothing.
echo "=========== the two BGP sessions, configured through the VTY ==========="
BGPUP=$($HX -c "configure" -c "router bgp 65000" \
        -c "bgp router-id 10.0.0.1" \
        -c "no bgp ebgp-requires-policy" \
        -c "neighbor 10.0.0.2 remote-as 65001" \
        -c "neighbor 10.0.0.2 description upstream-lab" \
        -c "neighbor 10.1.0.2 remote-as 65001" \
        -c "neighbor 10.1.0.2 description upstream-lab-segundo-enlace" \
        -c "address-family ipv4 unicast" \
        -c "neighbor 10.0.0.2 activate" \
        -c "neighbor 10.1.0.2 activate" \
        -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$BGPUP" | tail -3 | sed 's/^/  /'
printf '%s' "$BGPUP" | grep -q "commit complete" || {
  echo "the BGP sessions did not commit - nothing below would mean anything"
  printf '%s\n' "$BGPUP" | tail -20 | sed 's/^/  /'
  exit 1
}

# BOTH prefixes waited for HERE, and the second one is the reason this wait
# exists at all.
#
# 203.0.113.0/24 is the peer's own and arrives in seconds. 198.51.100.0/24
# comes from the third AS, one BGP hop further back. While this was one file
# with lab/routing.sh, the first section to read it ran ten minutes in and it
# had long since arrived; here graceful shutdown reads it two minutes in, and
# what that section asserts is that the local preference it holds is NOT zero -
# which a prefix that has not arrived yet satisfies by being absent. An
# assertion that passes because the table is empty is not an assertion.
echo "=== waiting for both AS routes to reach the VPP FIB ==="
in_fib0() { vppctl show ip fib "$1" 2>/dev/null | grep -c "^$1" || true; }
n=0
while [ "$n" -lt 90 ]; do
  [ "$(in_fib0 203.0.113.0/24)" -ge 1 ] && [ "$(in_fib0 198.51.100.0/24)" -ge 1 ] && break
  sleep 1; n=$((n + 1))
done
echo "  203.0.113.0/24=$(in_fib0 203.0.113.0/24)  198.51.100.0/24=$(in_fib0 198.51.100.0/24)  (${n}s)"

# Where the box keeps the policy's source, as an installed box has it.
mkdir -p /etc/prisma-routing

echo; echo "=========== the policies, written under policy-options ==========="
POL=$($HX -c "configure" -c "policy-options" \
  -c "prefix-list OTHER-AS" -c "198.51.100.0/24" -c "exit" \
  -c "community OURS members 65000:100" \
  -c "policy-statement FROM-UPSTREAM" \
  -c "term no-origin" -c "from prefix-list OTHER-AS" -c "then reject" -c "exit" \
  -c "exit" \
  -c "policy-statement PREFER-SECOND" \
  -c "term ours" -c "from route-filter 203.0.113.0/24 exact" \
  -c "set local-preference 200" -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement TO-UPSTREAM" \
  -c "term all" -c "set as-path-prepend 65000 65000" \
  -c "set community add OURS" -c "then accept" -c "exit" -c "exit" \
  -c "exit" -c "commit" 2>&1)
printf '%s\n' "$POL" | tail -3 | sed 's/^/  /'

# Something of this box's own for the export to carry: a blackholed /24,
# announced by a network statement.
$HX -c "configure" -c "router static" -c "192.0.2.0/24 next-hop blackhole" -c "exit" \
    -c "router bgp 65000" -c "address-family ipv4 unicast" \
    -c "network 192.0.2.0/24" -c "exit-address-family" -c "exit" \
    -c "commit" 2>&1 | tail -1 | sed 's/^/  /'

echo; echo "=========== applied, MODE=$MODE ==========="
if [ "$MODE" = off ]; then
  APPLY="(nothing applied: MODE=off)"
else
  APPLY=$($HX -c "configure" -c "router bgp 65000" \
    -c "address-family ipv4 unicast" \
    -c "neighbor 10.0.0.2 import FROM-UPSTREAM" \
    -c "neighbor 10.1.0.2 import PREFER-SECOND" \
    -c "neighbor 10.0.0.2 export TO-UPSTREAM" \
    -c "exit-address-family" -c "exit" -c "commit" 2>&1)
fi
printf "%s\n" "$APPLY" | sed "s/^/  /"
vtysh -c "clear bgp ipv4 unicast * soft" >/dev/null 2>&1
sleep 12

RUN=$($HX -c "show running-config" 2>&1)
printf '%s\n' "$RUN" | sed -n '/^policy-options/,/^ exit$/p' | head -30 | sed 's/^/  /'
printf '%s\n' "$RUN" | grep "route-map PO-" | sed 's/^/  /'

echo; echo "=========== import: the reject, and the default after it ==========="
# 198.51.100.0/24 still arrives over the second session, where the import is
# PREFER-SECOND, whose default accepts it; over the first it is rejected.
V1=$(vtysh -c "show bgp ipv4 unicast 198.51.100.0/24" 2>&1)
printf '%s\n' "$V1" | grep -E "Paths|from 10\." | sed 's/^/  /'
REJ_FIRST=$(printf '%s\n' "$V1" | grep -c "from 10.0.0.2")
ACC_203=$(vtysh -c "show bgp ipv4 unicast 203.0.113.0/24" 2>&1 | grep -c "from 10.0.0.2")

echo; echo "=========== import: local-preference moves the path ==========="
BEST=$(vtysh -c "show bgp ipv4 unicast 203.0.113.0/24" 2>&1)
printf '%s\n' "$BEST" | grep -E "Local|best|from" | sed 's/^/  /'
BEST_VIA=$(printf '%s\n' "$BEST" | awk '/from 10\./{f=$0} /best \(/{print f; exit}')
FIB=$(vppctl show ip fib 203.0.113.0/24 2>&1 | tr -d '\r')
printf '%s\n' "$FIB" | grep -E "via|10\.[01]\.0\.2" | head -3 | sed 's/^/  vpp: /'

echo; echo "=========== export: what the peer receives ==========="
vtysh -c "show ip route 192.0.2.0/24" 2>&1 | head -4 | sed 's/^/  box rib: /'
vtysh -c "show bgp ipv4 unicast 192.0.2.0/24" 2>&1 | head -6 | sed 's/^/  box bgp: /'
vtysh -c "show bgp ipv4 unicast neighbors 10.0.0.2 advertised-routes" 2>&1 | head -12 | sed 's/^/  adv: /' 
PEER=$(ip netns exec peer vtysh -N peer -c "show bgp ipv4 unicast 192.0.2.0/24" 2>&1)
printf '%s\n' "$PEER" | sed -n '1,14p' | sed 's/^/  peer: /'

echo; echo "=========== show policy ==========="
SHOWPOL=$($HX -c "show running-config" 2>&1 | grep -c "^ policy-statement")
echo "  policy-statements in the running configuration: $SHOWPOL"

echo; echo "=========== VERDICT ==========="
expect_in "the policies commit" "commit complete" "$POL"
expect "they are in the running configuration, as written" test "$SHOWPOL" -ge 3
expect_in "import applies as a route-map the box compiled" \
  "neighbor 10.0.0.2 route-map PO-FROM-UPSTREAM-IN in" "$RUN"
expect "then reject drops the other AS's prefix on that session" \
  test "$REJ_FIRST" -eq 0
expect "  and the default accepts what no term decided" test "$ACC_203" -ge 1
expect_in "set local-preference makes the second link the best path" \
  "10.1.0.2" "$BEST_VIA"
# Not only present: ALONE. With equal preference both sessions are
# multipath and the FIB lists both next hops, so "10.1.0.2 is there" passed
# with no policy at all; the policy is what takes 10.0.0.2 out.
expect_in "  and the dataplane forwards that way" "10.1.0.2" "$FIB"
expect_not_in "  and only that way" "via 10.0.0.2" "$FIB"
expect_in "export prepends, as the peer sees it" "65000 65000 65000" "$PEER"
expect_in "  and adds the community" "65000:100" "$PEER"
verdict
