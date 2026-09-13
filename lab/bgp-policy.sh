#!/bin/sh
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# BGP policy asserted on the tables that forward, not on the text of a config.
#
# Route-maps, AS-path lists, communities, graceful shutdown, the pool prefix
# nobody announces, and RPKI origin validation - each one checked in the VPP
# FIB or in the neighbour's own table.
#
#  [netns origin] AS 65002   [netns peer] AS 65001    [default] AS 65000
#   dum-origin 198.51.100.0/24   dum-bgp 203.0.113.0/24
#     veth3-origin 10.2.0.2 -- 10.2.0.1 veth3-peer
#                     veth-out  10.0.0.2 --- veth-vpp  -> vppwan0 10.0.0.1
#                     veth2-out 10.1.0.2 --- veth2-vpp -> vppwan1 10.1.0.1
#
# TWO sessions to the same peer, carrying the same prefix. Without a second
# path `set local-preference` has nothing to choose between and an outbound
# policy has no control session to be compared against, so a policy test would
# be asserting that a value appears in a table rather than that it changed
# which way packets go (#144).
#
# A THIRD autonomous system one hop behind the peer. Without it every path this
# box receives has the same origin, "the other AS is not affected" is asserted
# against nothing (#146), and RPKI has no pair of prefixes where one ROA is
# right and the other is not.
#
# No OSPF ADJACENCY and no IS-IS. Nothing here reads 198.18.0.0/24 or
# 198.19.0.0/24; an adjacency nothing asserts on costs a minute of convergence
# and proves nothing. ospfd is nevertheless started, with no interface in any
# area: the pool-announce check has to tell a `redistribute` under `router bgp`
# from the same word under `router ospf`, and a `router ospf` statement is
# refused outright when the daemon is not running, which would make that case
# assert the wrong thing. The punt path for all three protocols is lab/routing.sh, which this
# file was split out of: the two halves in one file ran over 400 s and made it
# the floor of the whole suite (#167).
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
HX="python3 /src/src/hexash/hexash.py"

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

# ---------------- HexaRouting ----------------
mkdir -p /var/run/frr /etc/frr
cat > /etc/frr/zebra.conf <<'EOF'
hostname hexarouting
EOF
# Deliberately EMPTY of BGP. Both sessions are configured through the VTY
# further down, so every table a policy below moves is evidence about a peering
# the VTY built - not one a file written by the lab did (#142).
cat > /etc/frr/bgpd.conf <<'EOF'
hostname hexarouting
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
start_frr bgpd ospfd

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
echo "=== aguardando as rotas das duas AS chegarem na FIB do VPP ==="
in_fib0() { vppctl show ip fib "$1" 2>/dev/null | grep -c "^$1" || true; }
n=0
while [ "$n" -lt 90 ]; do
  [ "$(in_fib0 203.0.113.0/24)" -ge 1 ] && [ "$(in_fib0 198.51.100.0/24)" -ge 1 ] && break
  sleep 1; n=$((n + 1))
done
echo "  203.0.113.0/24=$(in_fib0 203.0.113.0/24)  198.51.100.0/24=$(in_fib0 198.51.100.0/24)  (${n}s)"
expect "both autonomous systems' prefixes are in the FIB before any policy" \
  sh -c "[ $(in_fib0 203.0.113.0/24) -ge 1 ] && [ $(in_fib0 198.51.100.0/24) -ge 1 ]"

echo
echo "=== adjacencias ==="
vtysh -c "show bgp ipv4 unicast summary" 2>/dev/null | grep -E "^10\.[01]\.0\.2" \
  | awk '{print "BGP   : peer "$1" AS "$3" up "$9" prefixos "$10}'
# --- a pool's public prefix that nothing announces (#145) ---
#
# The check this product can make and nobody else can. Configure a pool, get
# BGP right, and unless something advertises a prefix covering the pool's
# public side, every subscriber behind it is translated to an address the
# internet cannot route back to. Both halves individually correct, service
# dead, and nothing in the box connected them.
echo "=========== a pool nobody announces ==========="
NOANN=$($HX -c "configure" \
        -c "cgnat pool lab-ann" -c "inside 100.64.8.0/22" \
        -c "outside 200.150.9.16/28" -c "block-size 1024" -c "exit" \
        -c "commit check" 2>&1)
printf '%s\n' "$NOANN" | tail -12 | sed 's/^/  check: /'
expect_in "an unannounced pool prefix is called out" \
  "nothing here announces 200.150.9.16/28" "$NOANN"
expect_in "and the message says what it costs" \
  "cannot route back" "$NOANN"

# Announce a COVERING prefix - not the same one - because the check has to
# understand containment, not string equality. A /24 covers the pool's /28.
ANN=$($HX -c "configure" \
      -c "cgnat pool lab-ann" -c "inside 100.64.8.0/22" \
      -c "outside 200.150.9.16/28" -c "block-size 1024" -c "exit" \
      -c "router bgp 65000" -c "address-family ipv4 unicast" \
      -c "network 200.150.9.0/24" -c "exit-address-family" -c "exit" \
      -c "commit check" 2>&1)
expect_not_in "a covering prefix silences it" \
  "nothing here announces 200.150.9.16/28" "$ANN"

# And it must not fire on a box that announces by redistribution, which this
# check cannot see. A warning that cries on a working box gets ignored, and
# then the real one is ignored too.
RED=$($HX -c "configure" \
      -c "cgnat pool lab-ann" -c "inside 100.64.8.0/22" \
      -c "outside 200.150.9.16/28" -c "block-size 1024" -c "exit" \
      -c "router bgp 65000" -c "redistribute connected" -c "exit" \
      -c "commit check" 2>&1)
expect_not_in "and redistribution silences it, because it cannot be seen" \
  "nothing here announces" "$RED"

# A redistributed STATIC route is the one form of redistribution this box can
# follow, and it is the ordinary way an ISP originates its own aggregate: a
# blackhole route for the prefix, redistributed into BGP. Measured on FRR
# 10.7.1 - `redistribute static` under `router bgp` with `ip route
# 200.150.9.16/28 blackhole` present puts `*> 200.150.9.16/28 ... ?` in the BGP
# table, and removing the route empties it - so this configuration ANNOUNCES
# the pool and must be met with silence.
RSTAT=$($HX -c "configure" \
        -c "cgnat pool lab-ann" -c "inside 100.64.8.0/22" \
        -c "outside 200.150.9.16/28" -c "block-size 1024" -c "exit" \
        -c "router static" -c "route 200.150.9.0/24 blackhole" -c "exit" \
        -c "router bgp 65000" -c "address-family ipv4 unicast" \
        -c "redistribute static" -c "exit-address-family" -c "exit" \
        -c "commit check" 2>&1)
expect_not_in "a blackhole route redistributed into BGP announces the pool" \
  "nothing here announces" "$RSTAT"

# And the half that makes the assertion above worth having: the same
# `redistribute static`, with a static route that covers something else. If the
# statement alone silenced the check, this would be silent too - and every box
# that redistributes statics would be exempt whether it announced the pool or
# not.
RELSE=$($HX -c "configure" \
        -c "cgnat pool lab-ann" -c "inside 100.64.8.0/22" \
        -c "outside 200.150.9.16/28" -c "block-size 1024" -c "exit" \
        -c "router static" -c "route 10.0.0.0/8 blackhole" -c "exit" \
        -c "router bgp 65000" -c "address-family ipv4 unicast" \
        -c "redistribute static" -c "exit-address-family" -c "exit" \
        -c "commit check" 2>&1)
printf '%s\n' "$RELSE" | tail -8 | sed 's/^/  check: /'
expect_in "a static route covering something else announces nothing" \
  "nothing here announces 200.150.9.16/28" "$RELSE"

# The scope, which is where this check was wrong. `redistribute static` under
# `router ospf` puts NOTHING into BGP - measured on FRR 10.7.1, with the
# covering static route present and BGP configured the whole time, `show bgp
# ipv4 unicast` answered "No BGP prefixes displayed, 0 exist". Reading the word
# wherever it appeared treated this box as one it could not see through, so the
# ordinary edge box that redistributes into its IGP got no warning at all.
ROSPF=$($HX -c "configure" \
        -c "cgnat pool lab-ann" -c "inside 100.64.8.0/22" \
        -c "outside 200.150.9.16/28" -c "block-size 1024" -c "exit" \
        -c "router static" -c "route 200.150.9.0/24 blackhole" -c "exit" \
        -c "router ospf" -c "redistribute static" -c "exit" \
        -c "router bgp 65000" -c "address-family ipv4 unicast" \
        -c "exit-address-family" -c "exit" \
        -c "commit check" 2>&1)
printf '%s\n' "$ROSPF" | tail -8 | sed 's/^/  check: /'
expect_in "redistribution into the IGP does not announce the pool, or hide it" \
  "nothing here announces 200.150.9.16/28" "$ROSPF"

# It stays a WARNING, and the configuration it warns about still passes.
# Announcing the prefix from a router upstream of this box is a legitimate
# design and the common one, and the measured reason is stronger than that:
# `network 200.150.9.16/28` with no matching route in the RIB is STORED by FRR
# 10.7.1 and advertised by nothing, so what this check can test is a proxy for
# what matters rather than the thing itself. A commit MUST NOT be refused on a
# proxy - that is the shape of #208, where refusing a whole commit over a BGP
# statement was itself the defect.
#
# Asserted on the SAME output that carries the warning, so the two cannot drift
# apart: the box says the prefix is announced by nothing, and in the same
# breath says the configuration is good.
expect_in "the box that warns about the pool still passes its check" \
  "configuration check succeeds" "$ROSPF"
expect_not_in "and the warning is not an error" "error:" "$ROSPF"

# --- what FRR says on a SUCCESSFUL apply (#145) ---
#
# frr-reload returning 0 does not mean FRR was silent. Told to use a route-map
# that does not exist, FRR 10.7.1 answers "The route-map 'X' does not exist.",
# stores the statement anyway and exits 0 - measured. The VTY only printed the
# output when the return code was non-zero, so that warning went nowhere and
# the operator committed a policy pointing at nothing.
# --- the prefix range rule, checked against FRR itself ---
#
# A `ge`/`le` FRR refuses is not a cheap refusal. It arrives through mgmtd,
# which keeps the rejected entry in its candidate datastore and then fails the
# NEXT configuration commit with the same message - naming a prefix-list that
# is in no running configuration, on no disk, and in nothing the VTY sent.
# Measured here: one file load carrying a good line and a bad one wedged the
# two commits that followed it, and this lab spent a whole run failing a
# router-advertisement section for that reason.
#
# So the VTY refuses these lines itself. Which means the VTY now holds an
# opinion about what FRR accepts, and an opinion that drifts is worse than no
# opinion: it would refuse configurations that are fine. Every case below is
# put to BOTH, and they must agree. mgmtd is restarted between cases so a
# poisoned candidate cannot make the next case look refused.
# --- what the VTY refuses, and what it will not print (#142) ---
#
# The refusals are about shapes that cannot work whatever the far end does.
# "Will the session come up" is not in the configuration and is not claimed.
echo "=========== BGP: what the VTY refuses ==========="
NOAS=$($HX -c "configure" -c "router bgp 65000" \
       -c "neighbor 10.0.0.9 description sem-as" \
       -c "exit" -c "commit" 2>&1)
echo "$NOAS" | tail -2 | sed 's/^/  /'
expect_in "a neighbour with no remote-as is refused" "no 'remote-as'" "$NOAS"
expect_in "and the refusal names the neighbour" "10.0.0.9" "$NOAS"

NOPG=$($HX -c "configure" -c "router bgp 65000" \
       -c "neighbor 10.0.0.9 peer-group TRANSITO" \
       -c "exit" -c "commit" 2>&1)
expect_in "a peer group nobody declared is refused" \
  "is not declared" "$NOPG"

# An interface the box does not have. FRR accepts this and the session sources
# from nothing, so the configuration reads correctly and never works.
NOIF=$($HX -c "configure" -c "router bgp 65000" \
       -c "neighbor 10.0.0.2 update-source Loopback9" \
       -c "exit" -c "commit" 2>&1)
expect_in "update-source naming an interface the box lacks is refused" \
  "no interface Loopback9" "$NOIF"

# The valid shapes must still commit. A check that refuses everything would
# pass every assertion above and make the feature unusable.
OKPG=$($HX -c "configure" -c "router bgp 65000" \
       -c "neighbor TRANSITO peer-group" \
       -c "neighbor TRANSITO remote-as 65001" \
       -c "neighbor 10.0.0.9 peer-group TRANSITO" \
       -c "exit" -c "commit" 2>&1)
echo "$OKPG" | tail -2 | sed 's/^/  /'
expect_in "a neighbour inheriting its AS from a peer group commits" \
  "commit complete" "$OKPG"

# The secret itself, not a hash of it, and it opens the session on the other
# router too. It must not reach the terminal.
SECRET=n0tinlogs
$HX -c "configure" -c "router bgp 65000" \
    -c "neighbor 10.0.0.9 password $SECRET" \
    -c "exit" -c "commit" > /tmp/bgppw.log 2>&1 || true
tail -2 /tmp/bgppw.log | sed 's/^/  /'
RUN=$($HX -c "show running-config" 2>&1)
expect_not_in "the neighbour password is not in the running configuration" \
  "$SECRET" "$RUN"
# The WHOLE line, not the words "not shown". An account's password is redacted
# with the same phrase, so grepping for it alone passes on any box that has a
# user with a password - which is every box - whether or not the BGP secret
# was touched. It also proves the statement reached FRR at all: if the commit
# had not applied it, there would be no line to redact.
expect_in "and the line is there, saying a password is set" \
  "neighbor 10.0.0.9 password <set, not shown>" "$RUN"
THIS=$($HX -c "configure" -c "router bgp 65000" -c "show this" 2>&1)
expect_not_in "nor in 'show this' inside the block" "$SECRET" "$THIS"
# ...and 'show this' printed the block, rather than nothing. Without this the
# assertion above passes on empty output, which is the same result as a
# redaction that works and proves neither.
expect_in "which did print the block" "neighbor 10.0.0.9" "$THIS"

# --- route-maps, asserted on the tables and not on the text (#144) ---
#
# Two sessions to the same peer carry the same prefix, so `set
# local-preference` has something to CHOOSE between. Asserting that a value
# appears in a table would pass on a policy that changes nothing.
echo "=========== route-maps: local-preference decides the path ==========="
fib_nexthop() { vppctl show ip fib 203.0.113.0/24 2>/dev/null \
                | grep -oE "10\.[01]\.0\.2" | head -1; }
BEFORE_NH=$(fib_nexthop)
echo "  the FIB forwards 203.0.113.0/24 via: ${BEFORE_NH:-none}"
# Prefer the OTHER one, whichever the tie-break happened to pick. Hard-coding
# the winner would make this pass or fail on FRR's tie-break rather than on the
# policy.
if [ "$BEFORE_NH" = "10.0.0.2" ]; then OTHER=10.1.0.2; else OTHER=10.0.0.2; fi
echo "  so the policy will prefer: $OTHER"

LP=$($HX -c "configure" \
     -c "ip prefix-list PL-PREFER seq 10 permit 203.0.113.0/24" \
     -c "route-map RM-PREFER permit 10" \
     -c "match ip address prefix-list PL-PREFER" \
     -c "set local-preference 200" \
     -c "exit" \
     -c "route-map RM-PREFER permit 20" \
     -c "exit" \
     -c "router bgp 65000" -c "address-family ipv4 unicast" \
     -c "neighbor $OTHER route-map RM-PREFER in" \
     -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$LP" | tail -4 | sed 's/^/  /'
expect_in "a route-map commits" "commit complete" "$LP"
# The catch-all at the end is a real idiom and must not be refused - but it
# must be SAID, because afterwards it is indistinguishable from a policy
# somebody stopped typing in the middle of.
expect_in "and the catch-all clause is reported, not refused" \
  "permits every route" "$LP"

n=0
while [ "$n" -lt 40 ]; do
  [ "$(fib_nexthop)" = "$OTHER" ] && break
  sleep 1; n=$((n + 1))
done
AFTER_NH=$(fib_nexthop)
echo "  after the policy, the FIB forwards via: ${AFTER_NH:-none} (${n}s)"
# THE assertion: the table that moves packets changed its mind. Not FRR's
# table, not the text of the configuration - the VPP FIB.
expect "local-preference changed which path the dataplane forwards on" \
  test "$AFTER_NH" = "$OTHER"
expect "and it really did change, rather than starting there" \
  test "$BEFORE_NH" != "$AFTER_NH"

echo "=========== route-maps: a prepend the neighbour can see ==========="
# This box announces something of its own, so there is an outbound direction
# to have a policy in.
ANN=$($HX -c "configure" -c "ip route 192.0.2.0/24 blackhole" \
      -c "router bgp 65000" -c "address-family ipv4 unicast" \
      -c "network 192.0.2.0/24" \
      -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$ANN" | tail -2 | sed 's/^/  /'
expect_in "the box announces a prefix of its own" "commit complete" "$ANN"

# Prepend on ONE session only. The other carries the same prefix with no
# policy, which makes it the control: same prefix, same box, same moment, and
# the policy is the only difference between the two AS paths.
PREP=$($HX -c "configure" \
       -c "ip prefix-list PL-MINE seq 10 permit 192.0.2.0/24" \
       -c "route-map RM-OUT permit 10" \
       -c "match ip address prefix-list PL-MINE" \
       -c "set as-path prepend 65000 65000" \
       -c "exit" \
       -c "route-map RM-OUT permit 20" \
       -c "exit" \
       -c "router bgp 65000" -c "address-family ipv4 unicast" \
       -c "neighbor 10.0.0.2 route-map RM-OUT out" \
       -c "exit-address-family" -c "exit" -c "commit" 2>&1)
expect_in "the outbound policy commits" "commit complete" "$PREP"

# A match that matches NOTHING, on the other session. If the set were applying
# to everything, this would prepend too - and a test that only checked the
# session it meant to change would never notice.
NOMATCH=$($HX -c "configure" \
          -c "ip prefix-list PL-NOTHING seq 10 permit 198.51.100.0/24" \
          -c "route-map RM-NOMATCH permit 10" \
          -c "match ip address prefix-list PL-NOTHING" \
          -c "set as-path prepend 65000 65000 65000 65000" \
          -c "exit" \
          -c "route-map RM-NOMATCH permit 20" \
          -c "exit" \
          -c "router bgp 65000" -c "address-family ipv4 unicast" \
          -c "neighbor 10.1.0.2 route-map RM-NOMATCH out" \
          -c "exit-address-family" -c "exit" -c "commit" 2>&1)
expect_in "the no-match policy commits too" "commit complete" "$NOMATCH"

# Read from the NEIGHBOUR's table, in JSON, because parsing the human output
# means matching an AS-path line to the "from" line under it by position.
aspath_from() {   # aspath_from <our address as the peer sees it>
  ip netns exec peer vtysh -N peer \
    -c "show bgp ipv4 unicast 192.0.2.0/24 json" 2>/dev/null > /tmp/paths.json
  python3 - "$1" <<'PYEOF'
import json, sys
want = sys.argv[1]
try:
    d = json.load(open("/tmp/paths.json"))
except Exception as e:
    print("UNREADABLE", e)
    raise SystemExit
for p in d.get("paths", []):
    peer = (p.get("peer") or {}).get("peerId") or (p.get("peer") or {}).get("routerId")
    if peer != want:
        continue
    # FRR has spelled this both ways across versions; ask for what is there
    # rather than assuming, and say so if neither is.
    ap = p.get("aspath")
    if isinstance(ap, dict):
        print(ap.get("string", "").strip())
    elif isinstance(ap, str):
        print(ap.strip())
    else:
        print("NOFIELD", sorted(p))
    raise SystemExit
print("NOPATH")
PYEOF
}
n=0
while [ "$n" -lt 40 ]; do
  case "$(aspath_from 10.0.0.1)" in *"65000 65000"*) break ;; esac
  sleep 1; n=$((n + 1))
done
AS_PREPENDED=$(aspath_from 10.0.0.1)
AS_PLAIN=$(aspath_from 10.1.0.1)
echo "  AS path the neighbour sees, session with the prepend: '$AS_PREPENDED'"
echo "  AS path the neighbour sees, session without it:       '$AS_PLAIN'"
expect "the prepend reached the neighbour's table" \
  test "$AS_PREPENDED" = "65000 65000 65000"
expect "and the session with a match that matches nothing is untouched" \
  test "$AS_PLAIN" = "65000"

echo "=========== route-maps: what is refused, and what is only said ==========="
# A deny clause with `set` statements is a mistake every time: the set never
# runs, because the route is denied. That one can be refused.
DENYSET=$($HX -c "configure" \
          -c "route-map RM-BAD deny 10" \
          -c "match ip address prefix-list PL-MINE" \
          -c "set local-preference 50" \
          -c "exit" -c "commit" 2>&1)
echo "$DENYSET" | tail -3 | sed 's/^/  /'
expect_in "a deny clause carrying 'set' is refused" "the set never runs" "$DENYSET"
expect_in "and the refusal names the clause" "RM-BAD deny 10" "$DENYSET"

# A clause that can never be reached. Sequence numbers are the operator's and
# this must not renumber them - but a policy that looks right and does nothing
# is worth a sentence.
DEAD=$($HX -c "configure" \
       -c "route-map RM-DEAD permit 10" \
       -c "set local-preference 120" \
       -c "exit" \
       -c "route-map RM-DEAD permit 20" \
       -c "match ip address prefix-list PL-MINE" \
       -c "set local-preference 300" \
       -c "exit" -c "commit check" 2>&1)
echo "$DEAD" | grep -i "warning" | sed 's/^/  /'
expect_in "a clause an earlier catch-all makes unreachable is reported" \
  "unreachable" "$DEAD"
expect_in "and the report names what shadows it" "RM-DEAD permit 10" "$DEAD"

# What the VTY did NOT check has to be visible, or an operator reads a
# guarantee into a statement that was merely forwarded.
UNCH=$($HX -c "configure" \
       -c "route-map RM-PASS permit 10" \
       -c "match ip address prefix-list PL-MINE" \
       -c "set tag 100" \
       -c "exit" -c "commit" 2>&1)
expect_in "a statement the VTY does not model still commits" \
  "commit complete" "$UNCH"
SHOWN=$($HX -c "show running-config" 2>&1)
# The marker text is two spaces and four words, and it was five words and four
# spaces until #141 shortened it: the long form put a real statement at 89
# columns on an 80-column serial console, which is the console a racked box is
# seen through. This assertion carried the old text for three days after the
# change and went red the first time anything ran it (#141).
expect_in "and is marked as unchecked where it is displayed" \
  "set tag 100  <- not checked" "$SHOWN"
# The other half, and it has to name a statement that is REALLY in that output
# and REALLY modelled, or it passes because the needle was never there. The
# line it used to look for, "set local-preference 200    <-", had neither the
# value nor the marker anywhere in this lab: vacuous both ways. The first
# replacement was wrong too and the suite said so - RM-DEAD is typed at
# `commit check` and never committed, so nothing of it is in a running
# configuration.
#
# `match ip address prefix-list PL-MINE` is in the same route-map as the
# `set tag 100` above, it was committed by the same $HX run, and `match` is in
# the route-map grammar. So it is in that output and it is modelled.
expect_in "and the modelled statement really is in that output" \
  "match ip address prefix-list PL-MINE" "$SHOWN"
expect_not_in "while the ones it does model carry no marker" \
  "match ip address prefix-list PL-MINE  <-" "$SHOWN"

# --- a refusal that belongs to FRR, and what it used to cost ---
#
# The VTY refuses the shapes it knows. Everything else reaches FRR, and one
# class of FRR refusal is not free: it arrives through mgmtd, which keeps the
# rejected entry in its candidate datastore and then refuses the NEXT
# configuration commit with the same message - naming a statement that is in no
# configuration the operator can find. Exactly one commit, and then it clears.
#
# Measured, and both halves matter:
#
#   * it takes the FILE-LOAD path to happen. A single statement typed at
#     `vtysh -c` is refused and costs nothing. A file carrying a good line and
#     a bad one - which is what frr-reload hands FRR on every commit - poisons.
#   * of the statements tried, only an invalid `ge`/`le` reaches the commit at
#     all. The rest are refused by FRR's PARSER, which does not poison.
#
# So the VTY refuses that class itself, and the poison is no longer reachable
# through the product. The absorber below is the defence for the class rather
# than for that one statement, and it is tested where the poison IS
# reproducible: by loading the file directly, the way frr-reload does.
# --- AS-path lists, against the paths the box really received (#146) ---
# --- the commands an operator runs while something is wrong (#148) ---
echo "=========== show bgp summary, against the table that forwards ==========="
SUM=$($HX -c "show bgp summary" 2>&1)
echo "$SUM" | sed 's/^/  /'
expect_in "the summary names a session" "10.0.0.2" "$SUM"
expect_in "and says the state" "Established" "$SUM"
# THE line this command exists for. FRR's own summary says a session is up and
# carrying N prefixes; whether the dataplane got them is a different question,
# and it is the one that decides whether packets move.
expect_in "and says how many BGP prefixes reached the forwarding table" \
  "forwarding table" "$SUM"

echo "=========== announce, and withdraw ==========="
# Announced through the VTY, asserted in the neighbour's table AND in the VPP
# FIB. FRR holding a route is a different claim from the box forwarding on it.
peer_has() {
  ip netns exec peer vtysh -N peer -c "show bgp ipv4 unicast $1" 2>/dev/null \
    | grep -c "$1" || true
}
in_fib2() { vppctl show ip fib "$1" 2>/dev/null | grep -c "^$1" || true; }
ANNOUNCE=$($HX -c "configure" -c "ip route 203.0.114.0/24 blackhole" \
           -c "router bgp 65000" -c "address-family ipv4 unicast" \
           -c "network 203.0.114.0/24" \
           -c "exit-address-family" -c "exit" -c "commit" 2>&1)
expect_in "announcing a prefix commits" "commit complete" "$ANNOUNCE"
n=0
while [ "$n" -lt 30 ]; do
  [ "$(peer_has 203.0.114.0/24)" -ge 1 ] && break
  sleep 1; n=$((n + 1))
done
echo "  the neighbour sees it: $(peer_has 203.0.114.0/24)   the FIB has it: $(in_fib2 203.0.114.0/24)   (${n}s)"
expect "the neighbour received the announcement" \
  test "$(peer_has 203.0.114.0/24)" -ge 1
expect "and this box forwards on it" test "$(in_fib2 203.0.114.0/24)" -ge 1

WITHDRAW=$($HX -c "configure" -c "router bgp 65000" \
           -c "address-family ipv4 unicast" \
           -c "no network 203.0.114.0/24" \
           -c "exit-address-family" -c "exit" \
           -c "no ip route 203.0.114.0/24 blackhole" -c "commit" 2>&1)
echo "$WITHDRAW" | tail -5 | sed 's/^/  withdraw: /'
expect_in "withdrawing it commits" "commit complete" "$WITHDRAW"
n=0
while [ "$n" -lt 30 ]; do
  [ "$(peer_has 203.0.114.0/24)" -eq 0 ] && break
  sleep 1; n=$((n + 1))
done
echo "  after withdrawing: neighbour $(peer_has 203.0.114.0/24), FIB $(in_fib2 203.0.114.0/24)   (${n}s)"
expect "the neighbour stops seeing it" test "$(peer_has 203.0.114.0/24)" -eq 0
expect "and it leaves the forwarding table too" \
  test "$(in_fib2 203.0.114.0/24)" -eq 0

echo "=========== graceful shutdown, measured by its effect ==========="
# RFC 8326. Before maintenance an operator wants traffic to DRAIN rather than
# drop. Asserted on what the neighbour receives and on what this box does to
# the routes it holds - not on the configuration containing the words, which
# would pass on a statement that did nothing.
peer_communities() {
  ip netns exec peer vtysh -N peer \
    -c "show bgp ipv4 unicast $1 json" 2>/dev/null > /tmp/gs.json
  python3 - <<'PYEOF'
import json
try:
    d = json.load(open("/tmp/gs.json"))
except Exception:
    print("UNREADABLE"); raise SystemExit
for p in d.get("paths", []):
    c = p.get("community") or {}
    print(" ".join(c.get("list", [])) or "(none)")
    raise SystemExit
print("NOPATH")
PYEOF
}
local_pref_of() {
  vtysh -c "show bgp ipv4 unicast $1 json" 2>/dev/null > /tmp/gs2.json
  python3 - <<'PYEOF'
import json
try:
    d = json.load(open("/tmp/gs2.json"))
except Exception:
    print("UNREADABLE"); raise SystemExit
best = None
for p in d.get("paths", []):
    lp = p.get("locPrf", p.get("localPref"))
    if lp is not None:
        best = lp if best is None else min(best, lp)
print("NONE" if best is None else best)
PYEOF
}
COMM_BEFORE=$(peer_communities 192.0.2.0/24)
LP_BEFORE=$(local_pref_of 198.51.100.0/24)
echo "  before: the neighbour sees communities '$COMM_BEFORE'; local-pref here is $LP_BEFORE"

GS=$($HX -c "configure" -c "router bgp 65000" \
     -c "bgp graceful-shutdown" -c "exit" -c "commit" 2>&1)
expect_in "graceful-shutdown commits" "commit complete" "$GS"
n=0
while [ "$n" -lt 30 ]; do
  case "$(peer_communities 192.0.2.0/24)" in *65535:0*) break ;; esac
  sleep 1; n=$((n + 1))
done
COMM_AFTER=$(peer_communities 192.0.2.0/24)
LP_AFTER=$(local_pref_of 198.51.100.0/24)
echo "  after:  the neighbour sees communities '$COMM_AFTER'; local-pref here is $LP_AFTER  (${n}s)"

# The well-known community, in the neighbour's table. Asserted by the NAME FRR
# renders, not by 65535:0 - the raw value never appears, because FRR prints
# well-known communities symbolically, and asserting the number would be
# asserting a rendering nothing produces.
expect_in "the neighbour receives the graceful-shutdown community" \
  "gracefulShutdown" "$COMM_AFTER"
expect_not_in "which it was not receiving before" \
  "gracefulShutdown" "$COMM_BEFORE"
# And the other half of RFC 8326: this box stops preferring what it holds, so
# traffic drains towards whoever else has a path.
expect "the local preference of received routes drops to zero" \
  test "$LP_AFTER" = "0"
expect "and it was not zero before" test "$LP_BEFORE" != "0"

# Reversible. A maintenance switch that cannot be switched back is not one.
$HX -c "configure" -c "router bgp 65000" \
    -c "no bgp graceful-shutdown" -c "exit" -c "commit" > /tmp/gs3.log 2>&1 || true
n=0
while [ "$n" -lt 30 ]; do
  case "$(peer_communities 192.0.2.0/24)" in *65535:0*) ;; *) break ;; esac
  sleep 1; n=$((n + 1))
done
echo "  after undoing it: '$(peer_communities 192.0.2.0/24)', local-pref $(local_pref_of 198.51.100.0/24)  (${n}s)"
expect_not_in "and undoing it takes the community back off" \
  "gracefulShutdown" "$(peer_communities 192.0.2.0/24)"

echo "=========== routes it is not keeping ==========="
# "no routes received" and "I am not keeping them" are different answers, and
# an operator at 2am acts on the first one.
RECV=$($HX -c "show bgp neighbor 10.0.0.2 received-routes" 2>&1)
echo "$RECV" | tail -3 | sed 's/^/  /'
expect_in "asking for routes the box does not keep says so" \
  "does not keep what it received" "$RECV"
expect_in "and names what to turn on" "soft-reconfiguration" "$RECV"

CLEARSOFT=$($HX -c "clear bgp 10.0.0.2 soft in" 2>&1)
echo "$CLEARSOFT" | tail -2 | sed 's/^/  /'
expect_in "and 'clear soft in' refuses for the same reason" \
  "nothing to re-apply" "$CLEARSOFT"

# With it on, both work. A refusal that cannot be lifted is a wall, not a check.
SOFTON=$($HX -c "configure" -c "router bgp 65000" \
         -c "address-family ipv4 unicast" \
         -c "neighbor 10.0.0.2 soft-reconfiguration inbound" \
         -c "exit-address-family" -c "exit" -c "commit" 2>&1)
expect_in "turning soft-reconfiguration on commits" "commit complete" "$SOFTON"
sleep 3
RECV2=$($HX -c "show bgp neighbor 10.0.0.2 received-routes" 2>&1)
expect_not_in "and then the routes are shown, not refused" \
  "does not keep what it received" "$RECV2"
CLEAR2=$($HX -c "clear bgp 10.0.0.2 soft in" 2>&1)
echo "$CLEAR2" | tail -2 | sed 's/^/  /'
expect_in "and the session can be softly cleared" "cleared" "$CLEAR2"

echo "=========== AS-path lists ==========="
# Wait for the third AS's prefix to arrive, so what follows is asserted against
# a real path and not against an empty table.
n=0
while [ "$n" -lt 60 ]; do
  vtysh -c "show bgp ipv4 unicast" 2>/dev/null | grep -q "198.51.100.0" && break
  sleep 1; n=$((n + 1))
done
echo "  paths this box has received:"
vtysh -c "show bgp ipv4 unicast" 2>/dev/null | tr -d '\r' \
  | awk 'NR>1 && $1 ~ /^[*>]/ {print "    " $0}' | head -8

# A regex FRR would refuse. Refused at the prompt instead of mid-commit.
BADRX=$($HX -c "configure" \
        -c "bgp as-path access-list AS-BAD permit ^65001(" \
        -c "commit" 2>&1)
echo "$BADRX" | tail -2 | sed 's/^/  /'
expect_in "a regex that does not compile is refused" \
  "does not compile" "$BADRX"

# The VTY now holds an opinion about which regexes FRR accepts, and an opinion
# that drifts is worse than none: it refuses configurations that are fine.
# Every case goes to BOTH, and they must agree. Same arrangement as the
# prefix-range rule, and it is here for the same reason.
echo "  --- the regex rule, against FRR itself ---"
RXN=0
for RX in '^65001$' '65001' '_65002_' '^65001(_65001)*$' '.*' '^$' \
          '^65001(' '65001)' '*65001' '[65001'; do
  RXN=$((RXN + 1))
  vtysh -c 'configure terminal' \
        -c "bgp as-path access-list RX$RXN permit $RX" >/dev/null 2>&1
  if vtysh -c 'show running-config' 2>/dev/null | grep -q "access-list RX$RXN "; then
    FRR_SAYS=accepted
  else
    FRR_SAYS=refused
  fi
  if RX="$RX" RXN="$RXN" python3 -c "
import os, sys
sys.path.insert(0, '/src/src/hexash')
from hexash import as_path_regex_error as e
stmt = 'bgp as-path access-list RX%s permit %s' % (os.environ['RXN'], os.environ['RX'])
sys.exit(1 if e(stmt) else 0)"; then
    VTY_SAYS=accepted
  else
    VTY_SAYS=refused
  fi
  expect "FRR and the VTY agree on '$RX'" test "$FRR_SAYS" = "$VTY_SAYS"
  [ "$FRR_SAYS" = "$VTY_SAYS" ] || echo "      frr: $FRR_SAYS   vty: $VTY_SAYS"
done

# A name a route-map matches on that nothing defines. Said, not refused: FRR
# accepts the clause, stores it and binds it when the list turns up - measured
# further down, against this same FRR - so refusing it made building a policy
# map-first impossible and took every unrelated change in the commit with it.
# The wording says which of the two this is, and the commit completing is half
# the assertion (#208).
UNDEF=$($HX -c "configure" \
        -c "route-map RM-UNDEF permit 10" \
        -c "match as-path AS-NAO-EXISTE" \
        -c "exit" -c "commit" 2>&1)
expect_in "matching a list nothing defines is reported" \
  "until one is, the clause matches nothing" "$UNDEF"
expect_in "and the commit is not refused for it" "commit complete" "$UNDEF"

# THE report this exists for. `permit 65001` compiles and is legal, and it also
# matches 165001 and 650012 - the mistake nobody sees by reading their own
# regex back. Counted against paths the box actually holds.
LOOSE=$($HX -c "configure" \
        -c "bgp as-path access-list AS-LOOSE permit 65002" \
        -c "commit check" 2>&1)
echo "$LOOSE" | grep -i "note:" | sed 's/^/  /'
expect_in "the VTY says what a regex matches, against real paths" \
  "received AS path" "$LOOSE"

# --- and the assertion the issue asks for: the OTHER AS is not affected ---
#
# Accept only what 65002 originated, on both sessions. 198.51.100.0/24 must
# stay in the forwarding table and 203.0.113.0/24 must leave it.
#
# The second half is the one that matters: a regex matching everything would
# keep BOTH, and a test that only looked at the route the policy was meant to
# select would pass on it. That is the failure this whole section exists for -
# a missing `^` or `$` turns an exact match into a substring one, and nobody
# sees it by reading their own regex back.
in_fib() { vppctl show ip fib "$1" 2>/dev/null | grep -c "^$1" || true; }
echo "  before the policy: 198.51.100.0/24=$(in_fib 198.51.100.0/24)  203.0.113.0/24=$(in_fib 203.0.113.0/24)"
expect "the third AS's prefix is in the FIB to begin with" \
  test "$(in_fib 198.51.100.0/24)" -ge 1

DROP=$($HX -c "configure" \
       -c "bgp as-path access-list AS-ORIGIN2 permit _65002\$" \
       -c "route-map RM-DROP permit 10" \
       -c "match as-path AS-ORIGIN2" \
       -c "exit" \
       -c "route-map RM-DROP deny 20" \
       -c "exit" \
       -c "router bgp 65000" -c "address-family ipv4 unicast" \
       -c "neighbor 10.0.0.2 route-map RM-DROP in" \
       -c "neighbor 10.1.0.2 route-map RM-DROP in" \
       -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$DROP" | tail -2 | sed 's/^/  /'
expect_in "the AS-path policy commits" "commit complete" "$DROP"
n=0
while [ "$n" -lt 40 ]; do
  [ "$(in_fib 203.0.113.0/24)" -eq 0 ] && break
  sleep 1; n=$((n + 1))
done
echo "  after the policy: 198.51.100.0/24=$(in_fib 198.51.100.0/24)  203.0.113.0/24=$(in_fib 203.0.113.0/24)  (${n}s)"
expect "the AS the policy names keeps its route" \
  test "$(in_fib 198.51.100.0/24)" -ge 1
expect "and the AS it does not name loses its own" \
  test "$(in_fib 203.0.113.0/24)" -eq 0

echo "=========== a stale refusal, and the commit that used to pay for it ==========="
cat > /tmp/poison.conf <<'PC'
ip prefix-list PL-POISON-OK seq 10 permit 10.21.0.0/16
ip prefix-list PL-POISON-BAD seq 10 permit 10.22.0.0/8 ge 24 le 16
PC
# THE CONTROL, and the reason the assertion after it means anything: with the
# refusal left standing, the next commit fails. Without this half, "the commit
# succeeded" would pass on a box that was never poisoned.
vtysh -f /tmp/poison.conf > /dev/null 2>&1
POISONED=$($HX -c "configure" \
           -c "ip prefix-list PL-PAYS seq 10 permit 10.23.0.0/16" \
           -c "commit" 2>&1)
echo "$POISONED" | tail -2 | sed 's/^/  /'
expect "with the stale refusal standing, the next commit is refused" \
  sh -c "! printf '%s' \"$POISONED\" | grep -q 'commit complete'"
expect_in "and the error names a prefix that is in no configuration" \
  "10.0.0.0/8" "$POISONED"

# And now the same thing with the absorber run first - the real function, from
# the product, not a copy of it here.
vtysh -f /tmp/poison.conf > /dev/null 2>&1
python3 -c "
import sys; sys.path.insert(0, '/src/src/hexash')
from hexash import Frr
Frr().absorb_stale_refusal()" 2>/dev/null
ABSORBED=$($HX -c "configure" \
           -c "ip prefix-list PL-SPARED seq 10 permit 10.24.0.0/16" \
           -c "commit" 2>&1)
echo "$ABSORBED" | tail -2 | sed 's/^/  /'
expect_in "after absorbing it, the operator's commit goes through" \
  "commit complete" "$ABSORBED"
# The absorber removes a prefix-list nobody has. If it ever leaves one behind,
# every box grows a statement the operator did not write.
expect_not_in "and the absorber leaves nothing behind" \
  "HEXA-ABSORB" "$(vtysh -c 'show running-config')"

echo "=========== the prefix range rule, against FRR itself ==========="
restart_mgmtd() {
  pkill -f 'frr/mgmtd' 2>/dev/null
  sleep 1
  /usr/lib/frr/mgmtd -d -A 127.0.0.1 2>/dev/null
  sleep 1
}
N=0
for CASE in "10.9.0.0/16" "10.9.0.0/16 ge 24 le 28" "10.9.0.0/16 ge 16" \
            "10.9.0.0/16 le 32" "10.12.0.0/8 ge 24 le 16" "10.9.0.0/16 le 12" \
            "10.9.0.0/16 ge 8" "10.9.0.0/16 ge 24 le 33"; do
  N=$((N + 1))
  STMT="ip prefix-list RC$N seq 10 permit $CASE"
  restart_mgmtd
  # "accepted" means FRR STORED it. Asking only whether the output said
  # "Configuration failed" called a vtysh parser refusal an acceptance, and
  # that is how this cross-check first reported a disagreement that was its
  # own reading error rather than a difference of opinion.
  vtysh -c 'configure terminal' -c "$STMT" >/dev/null 2>&1
  if vtysh -c 'show running-config' 2>/dev/null | grep -q "prefix-list RC$N "; then
    FRR_SAYS=accepted
  else
    FRR_SAYS=refused
  fi
  if STMT="$STMT" python3 -c "
import os, sys
sys.path.insert(0, '/src/src/hexash')
from hexash import prefix_range_error as e
sys.exit(1 if e(os.environ['STMT']) else 0)"; then
    VTY_SAYS=accepted
  else
    VTY_SAYS=refused
  fi
  expect "FRR and the VTY agree on '$CASE'" test "$FRR_SAYS" = "$VTY_SAYS"
  [ "$FRR_SAYS" = "$VTY_SAYS" ] || echo "    frr: $FRR_SAYS   vty: $VTY_SAYS"
done
restart_mgmtd
# The refusal has to name the line, or an operator with forty policy statements
# cannot find which one.
BADRANGE=$($HX -c "configure" \
           -c "ip prefix-list PL-RANGE seq 10 permit 10.12.0.0/8 ge 24 le 16" \
           -c "commit" 2>&1)
echo "$BADRANGE" | tail -3 | sed 's/^/  /'
expect_in "the VTY refuses the bad range before FRR sees it" \
  "prefix range refused" "$BADRANGE"
expect_in "and names the statement" "PL-RANGE" "$BADRANGE"
# The point of refusing early: the commit after it must still work. Before this
# check existed, this second commit failed citing 10.0.0.0/8.
AFTERBAD=$($HX -c "configure" \
           -c "ip prefix-list PL-AFTER seq 10 permit 10.14.0.0/16" \
           -c "commit" 2>&1)
expect_in "and the NEXT commit is unharmed" "commit complete" "$AFTERBAD"

# What THIS box holds, which is the table an inbound policy decides.
peer_has2() {
  vtysh -c "show bgp ipv4 unicast $1" 2>/dev/null | grep -c "$1" || true
}

echo "=========== a route-map referenced and not defined (#145) ==========="
# REFUSED now, where this section used to assert the commit succeeding. The
# behaviour it asserted was FRR's and it is unchanged - FRR warns, keeps the
# statement and exits 0 - and what changed is that the statement never reaches
# FRR, because what FRR then does to the routes was measured against this very
# pair of speakers and it is not survivable:
#
#   neighbor 10.0.0.2 route-map RM-MISSING in, nothing defining it
#     the peer offers two prefixes, the session reaches Established, and this
#     box holds ZERO. Defining the map brings both back with nothing retyped.
#
# So "the session comes up, carries no routes, and nothing says why" is true,
# and the last clause of it is true TWICE over on this box: FRR's own warning
# goes to the vtysh session, and Frr._summarise() keeps only lines carrying
# Exiting:, ERROR, %, Failed or Exception: - which that warning is not.
RM=$($HX -c "configure" -c "router bgp 65000" \
     -c "address-family ipv4 unicast" \
     -c "neighbor 10.0.0.2 route-map RM-MISSING in" \
     -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$RM" | tail -6 | sed 's/^/  /'
expect_in "the commit is refused" "nothing applied" "$RM"
expect_in "and the missing map is named" "RM-MISSING" "$RM"
expect_in "and what it would cost is said" "denies everything" "$RM"
expect_not_in "and nothing claims it was applied" "commit complete" "$RM"
# The statement must not have reached FRR at all. A refusal that arrives after
# the box is already denying everything is not a refusal.
expect_not_in "and FRR never saw the statement" "RM-MISSING" \
  "$(vtysh -c 'show running-config')"

# THE legitimate neighbour of it, which MUST still commit: the same statement
# with the map defined beside it, which is how a policy is written. Asserted
# on the prefixes the session carries afterwards, not on the commit's output -
# a map that commits and denies everything would pass that.
RMOK=$($HX -c "configure" \
       -c "route-map RM-MISSING permit 10" -c "exit" \
       -c "router bgp 65000" -c "address-family ipv4 unicast" \
       -c "neighbor 10.0.0.2 route-map RM-MISSING in" \
       -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$RMOK" | tail -4 | sed 's/^/  /'
expect_in "defining it in the same commit applies it" "commit complete" "$RMOK"
# The noise filter has to keep a commit readable, or a warning that does reach
# the operator is lost in progress output and nothing has been gained.
expect_not_in "without frr-reload's progress chatter" "Waiting for children" "$RMOK"
# frr-reload echoes the commands it sends as a Python list repr, split across
# lines. Those fragments are not diagnostics, and letting them through buried
# the one line that matters in punctuation.
expect_not_in "and without fragments of its command echo" "frr: '" "$RMOK"
# frr-reload narrates itself at INFO. Four lines of that around one line that
# matters is the warning being buried in a different way.
expect_not_in "and without the tool narrating itself" "INFO:" "$RMOK"
n=0
while [ "$n" -lt 40 ]; do
  [ "$(peer_has2 203.0.113.0/24)" -ge 1 ] && break
  sleep 1; n=$((n + 1))
done
echo "  through the defined map the session carries: $(peer_has2 203.0.113.0/24) (${n}s)"
expect "and the session carries routes through it" \
  test "$(peer_has2 203.0.113.0/24)" -ge 1

# FRR's own diagnostic still has to reach the operator where the VTY does NOT
# refuse. `neighbor X default-originate route-map RM-NOPE` is such a place:
# measured on FRR 10.7.1, it answers "The route-map 'RM-NOPE' does not exist."
# and stores the statement, and the VTY reports it rather than refusing,
# because what it costs there was not measured.
DEFO=$($HX -c "configure" -c "router bgp 65000" \
       -c "address-family ipv4 unicast" \
       -c "neighbor 10.0.0.2 default-originate route-map RM-NOPE" \
       -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$DEFO" | grep -iE "warning|frr:" | head -3 | sed 's/^/  /'
expect_in "a reference the VTY does not refuse still commits" "commit complete" "$DEFO"
expect_in "and FRR's own warning reaches the operator" "does not exist" "$DEFO"
$HX -c "configure" -c "router bgp 65000" -c "address-family ipv4 unicast" \
    -c "no neighbor 10.0.0.2 default-originate route-map RM-NOPE" \
    -c "no neighbor 10.0.0.2 route-map RM-MISSING in" \
    -c "exit-address-family" -c "exit" \
    -c "no route-map RM-MISSING permit 10" -c "commit" >/dev/null 2>&1 || true

# --- origin validation, with a cache that is really running (#147) ---
#
# A cache is a service, so the lab runs one. stayrtr with a small VRP set is
# enough, and it is in Debian.
echo "=========== RPKI: a route the validator calls invalid ==========="
# The AS-path policy from the section above is still on both sessions and it
# dropped 203.0.113.0/24. Taken off first, so what follows measures the RPKI
# policy and not the one before it.
$HX -c "configure" -c "router bgp 65000" -c "address-family ipv4 unicast" \
    -c "no neighbor 10.0.0.2 route-map RM-DROP in" \
    -c "no neighbor 10.1.0.2 route-map RM-DROP in" \
    -c "exit-address-family" -c "exit" -c "commit" > /tmp/rpki0.log 2>&1 || true
in_fib3() { vppctl show ip fib "$1" 2>/dev/null | grep -c "^$1" || true; }
n=0
while [ "$n" -lt 40 ]; do
  [ "$(in_fib3 203.0.113.0/24)" -ge 1 ] && [ "$(in_fib3 198.51.100.0/24)" -ge 1 ] && break
  sleep 1; n=$((n + 1))
done
echo "  starting point: 203.0.113.0/24=$(in_fib3 203.0.113.0/24)  198.51.100.0/24=$(in_fib3 198.51.100.0/24)  (${n}s)"
expect "both prefixes are in the FIB before validation is applied" \
  sh -c "[ $(in_fib3 203.0.113.0/24) -ge 1 ] && [ $(in_fib3 198.51.100.0/24) -ge 1 ]"

# 203.0.113.0/24 is really originated by 65001, and the ROA says 65001: VALID.
# 198.51.100.0/24 is really originated by 65002, and the ROA says 64999, so a
# ROA covers it and the origin does not match: INVALID. That is the pair the
# whole section turns on.
cat > /tmp/vrp.json <<'VRP'
{"roas":[
 {"prefix":"203.0.113.0/24","maxLength":24,"asn":"AS65001"},
 {"prefix":"198.51.100.0/24","maxLength":24,"asn":"AS64999"}
]}
VRP
stayrtr -cache /tmp/vrp.json -bind 127.0.0.1:8282 -checktime=false \
  > /tmp/stayrtr.log 2>&1 &
sleep 3

# The module has to actually be loaded, or everything below is asserted against
# a box that cannot validate anything.
expect "the RPKI module is loaded" \
  sh -c "vtysh -c 'show rpki cache-connection' >/dev/null 2>&1"

# The warning that exists because of the failure this feature CREATES: a policy
# keyed on notfound with no cache configured matches every route on the box.
NOCACHE=$($HX -c "configure" -c "route-map RM-EARLY deny 5" \
          -c "match rpki notfound" -c "exit" -c "commit check" 2>&1)
echo "$NOCACHE" | grep -i "warning" | sed 's/^/  /'
expect_in "a notfound policy with no cache configured is called out" \
  "every route is notfound" "$NOCACHE"

RPKI=$($HX -c "configure" -c "rpki" \
       -c "rpki cache tcp 127.0.0.1 8282 preference 1" -c "exit" \
       -c "route-map RM-RPKI deny 5" -c "match rpki invalid" -c "exit" \
       -c "route-map RM-RPKI permit 10" -c "exit" \
       -c "router bgp 65000" -c "address-family ipv4 unicast" \
       -c "neighbor 10.0.0.2 route-map RM-RPKI in" \
       -c "neighbor 10.1.0.2 route-map RM-RPKI in" \
       -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$RPKI" | grep -vE "^(note|warning):" | tail -3 | sed 's/^/  /'
expect_in "the RPKI policy commits" "commit complete" "$RPKI"

n=0
while [ "$n" -lt 40 ]; do
  [ "$(in_fib3 198.51.100.0/24)" -eq 0 ] && break
  sleep 1; n=$((n + 1))
done
echo "  after validation: 203.0.113.0/24=$(in_fib3 203.0.113.0/24)  198.51.100.0/24=$(in_fib3 198.51.100.0/24)  (${n}s)"
vtysh -c "show rpki prefix-table" 2>/dev/null | head -8 | sed 's/^/    /'
# The assertion on the table that forwards. Both halves: the invalid one is
# gone AND the valid one is still there - dropping everything would satisfy the
# first on its own.
expect "the invalid route does not reach the forwarding table" \
  test "$(in_fib3 198.51.100.0/24)" -eq 0
expect "and the valid one still does" \
  test "$(in_fib3 203.0.113.0/24)" -ge 1

# And the summary says validation is available, where somebody will see it.
SUMOK=$($HX -c "show bgp summary" 2>&1)
echo "$SUMOK" | grep -i "rpki" | sed 's/^/  /'
expect_in "the summary says the cache is connected" \
  "of 1 cache(s) connected" "$SUMOK"

echo "=========== RPKI: and when the cache goes away ==========="
# THE assertion that matters. A cache that is gone marks everything notfound,
# and from a route table that is indistinguishable from "nothing is invalid".
pkill -f stayrtr 2>/dev/null
# Waited on the per-cache marker. FRR's own header line says "Connected to
# group 1" for as long as the cache stays configured - measured at seventy
# seconds after the server was killed - so waiting on the word "connected"
# would wait for something that never happens.
n=0
while [ "$n" -lt 30 ]; do
  vtysh -c "show rpki cache-connection" 2>/dev/null \
    | grep -q "(connected)" || break
  sleep 1; n=$((n + 1))
done
vtysh -c "show rpki cache-connection" 2>&1 | head -5 | sed 's/^/    /'
SUMGONE=$($HX -c "show bgp summary" 2>&1)
echo "$SUMGONE" | grep -i "rpki" | sed 's/^/  /'
expect_in "the box says the cache is not connected, rather than staying quiet" \
  "NONE connected" "$SUMGONE"
expect_in "and says what that costs, without overstating it" \
  "which expires" "$SUMGONE"
# The other half, measured: killing the cache does NOT empty the prefix table.
# An operator told "nothing is validated" would go looking for an outage that
# has not started yet.
expect "the prefixes the cache already sent are still there" \
  sh -c "vtysh -c 'show rpki prefix-table' | grep -q 'Number of IPv4 Prefixes: 2'"

echo "=========== what FRR does with the shapes these checks judge (#208) ==========="
#
# Every opinion the VTY holds about BGP is an opinion about FRR, and an opinion
# that drifts from FRR refuses configurations that are fine. So each one is put
# to the FRR in this container and the answer is the assertion - the same
# arrangement as the prefix-range rule and the AS-path regex.
#
# Accepting a statement proves the parser and nothing else. What happens to it
# on a real pair - and why an unnumbered session does not reach Established on
# today's dataplane - is lab/routing.sh, where the linux_cp pair is.
ip link add dum-unnum type dummy 2>/dev/null; ip link set dum-unnum up

# --- BGP unnumbered: FRR takes it, so the VTY must ---
UNN=$(vtysh -c "conf t" -c "router bgp 65000" \
      -c "neighbor dum-unnum interface remote-as external" 2>&1)
echo "  FRR on 'neighbor dum-unnum interface remote-as external': ${UNN:-accepted, no output}"
expect_in "FRR accepts the unnumbered form" \
  " neighbor dum-unnum interface remote-as external" \
  "$(vtysh -c 'show running-config')"
# And on an interface that does not exist. FRR accepts THAT too and says
# nothing ever again, which is why the VTY checks the name at all.
GONE=$(vtysh -c "conf t" -c "router bgp 65000" \
       -c "neighbor nosuchif0 interface remote-as external" 2>&1)
echo "  FRR on an interface it has not got: ${GONE:-accepted, no output}"
expect_in "FRR accepts an unnumbered peer on an interface that does not exist" \
  " neighbor nosuchif0 interface remote-as external" \
  "$(vtysh -c 'show running-config')"
expect_in "and leaves it Idle, saying nothing" "nosuchif0" \
  "$(vtysh -c 'show bgp summary' | grep nosuchif0)"
vtysh -c "conf t" -c "router bgp 65000" -c "no neighbor nosuchif0" \
      -c "no neighbor dum-unnum" >/dev/null 2>&1

# --- the legacy `ip ...` spellings of a policy list ---
#
# Recorded because the answer was not what was expected. FRR 10.7.1 does not
# have them at all, so a check that refused them was refusing nothing an
# operator can type on this box - and being tolerant of them would be
# tolerance for a statement FRR rejects.
for legacy in "ip community-list standard CMX permit 65000:1" \
              "ip as-path access-list ASX permit ^65001$"; do
  OUTL=$(vtysh -c "conf t" -c "$legacy" 2>&1)
  echo "  FRR on '$legacy': $OUTL"
  expect_in "FRR 10.7.1 has no '$(echo "$legacy" | cut -d' ' -f1-2)' spelling" \
    "Unknown command" "$OUTL"
done

# --- a route-map matching a list that does not exist YET ---
#
# THE measurement behind narrowing that refusal to a warning: FRR takes the
# clause, stores it, and binds it when the list turns up, with nothing retyped.
vtysh -c "conf t" -c "route-map RM-LATE permit 10" \
      -c "match community CM-LATE" >/dev/null 2>&1
expect_in "FRR accepts a match on a community list that does not exist" \
  "match community CM-LATE" "$(vtysh -c 'show running-config')"
vtysh -c "conf t" -c "bgp community-list standard CM-LATE permit 65000:9" \
      >/dev/null 2>&1
expect_in "and binds it when the list arrives, with nothing retyped" \
  "community CM-LATE" "$(vtysh -c 'show route-map RM-LATE')"

# --- the order clauses run in ---
vtysh -c "conf t" -c "route-map RM-ORD permit 20" -c "exit" >/dev/null 2>&1
vtysh -c "conf t" -c "route-map RM-ORD permit 10" \
      -c "match ip address prefix-list PL-ORD" -c "exit" >/dev/null 2>&1
FIRST=$(vtysh -c "show route-map RM-ORD" | grep "sequence" | head -1)
echo "  first clause FRR lists, having been typed second: $FIRST"
expect_in "FRR orders clauses by seq, not by the order they were written" \
  "sequence 10" "$FIRST"

# --- `continue` and `on-match`, which are one statement with two spellings ---
vtysh -c "conf t" -c "route-map RM-CNT permit 10" -c "continue 30" \
      -c "exit" >/dev/null 2>&1
expect_in "FRR stores an operator's 'continue 30' as 'on-match goto 30'" \
  "on-match goto 30" "$(vtysh -c 'show running-config')"

echo "=========== and what the VTY makes of the same statements ==========="
# The defect as it was reported: an unnumbered neighbour refused the WHOLE
# candidate, so a CGNAT pool typed beside it was refused with it.
UNNCK=$($HX -c "configure" \
        -c "cgnat pool lab-unnum" -c "inside 100.64.16.0/22" \
        -c "outside 200.150.9.32/28" -c "block-size 1024" -c "exit" \
        -c "router bgp 65000" \
        -c "neighbor vppwan1 interface remote-as external" \
        -c "exit" -c "commit check" 2>&1)
printf '%s\n' "$UNNCK" | grep -vE "^note" | tail -8 | sed 's/^/  /'
expect_in "an unnumbered neighbour does not fail the check" \
  "configuration check succeeds" "$UNNCK"
expect_not_in "and nothing claims the AS is missing" \
  "has no 'remote-as'" "$UNNCK"
expect_in "the unrelated pool in the same candidate is still judged on its own merits" \
  "200.150.9.32/28" "$UNNCK"

# The half of the check that was right all along, and had to survive.
NORAS=$($HX -c "configure" -c "router bgp 65000" \
        -c "neighbor 10.0.0.77 description sem-as" \
        -c "exit" -c "commit check" 2>&1)
printf '%s\n' "$NORAS" | grep -iE "error" | head -3 | sed 's/^/  /'
expect_in "a neighbour declared with no remote-as at all is still refused" \
  "has no 'remote-as'" "$NORAS"

# The name in an unnumbered statement, held to #205's rule.
BADIF=$($HX -c "configure" -c "router bgp 65000" \
        -c "neighbor vppwan99 interface remote-as external" \
        -c "exit" -c "commit check" 2>&1)
printf '%s\n' "$BADIF" | grep -iE "error" | head -3 | sed 's/^/  /'
expect_in "an unnumbered session on an interface the box has not got is refused" \
  "no interface vppwan99" "$BADIF"

# The dataplane's end of the linux_cp pair: FRR accepts it and peers with
# nothing. Said, not refused - the operator cannot get the other name from
# anything `show interface` prints.
DPIF=$($HX -c "configure" -c "router bgp 65000" \
       -c "neighbor host-veth2-vpp interface remote-as external" \
       -c "exit" -c "commit check" 2>&1)
printf '%s\n' "$DPIF" | grep -i "warning" | head -3 | sed 's/^/  /'
expect_in "the dataplane's name for the pair is warned about" \
  "FRR knows this interface as vppwan1" "$DPIF"
expect_in "and the commit is not refused for it" "configuration check succeeds" "$DPIF"

# A policy list that is not defined yet: said, and NOT a refusal any more.
LATE=$($HX -c "configure" -c "route-map RM-FUT permit 10" \
       -c "match community CM-FUTURA" -c "exit" -c "commit check" 2>&1)
printf '%s\n' "$LATE" | grep -i "warning" | head -3 | sed 's/^/  /'
expect_in "a list that is not defined yet is reported" \
  "no community list called 'CM-FUTURA'" "$LATE"
expect_in "and reported as a state rather than a fault" "until one is" "$LATE"
expect_in "and does not fail the commit, because FRR binds it later" \
  "configuration check succeeds" "$LATE"

# The clause inserted in front of a catch-all, which is what seq is for.
SEQ=$($HX -c "configure" -c "route-map RM-SEQ permit 20" -c "exit" \
      -c "route-map RM-SEQ permit 10" -c "match as-path RX1" -c "exit" \
      -c "commit check" 2>&1)
printf '%s\n' "$SEQ" | grep -i "warning" | head -4 | sed 's/^/  /'
expect_not_in "a low-seq clause inserted after a catch-all is not called unreachable" \
  "unreachable" "$SEQ"

echo "=========== a policy that withdraws the prefix carrying this session ==========="
# The SSH lockout check refuses a configuration nobody could log in with; this
# is the same failure one layer down. It cannot be refused - withdrawing a
# prefix on purpose is an ordinary thing to do - so it is gated on the word,
# the shape #23 established for a change that re-maps subscribers.
#
# SSH_CONNECTION is what sshd sets for the session's own process, and on the
# appliance hexash IS the login shell, so it is there. It is supplied here
# because this lab runs hexash directly rather than through sshd; nothing in
# the product writes that variable, and nothing here stands in for a step the
# product is missing.
#
# The assertion is on the PEER's table both times. A policy that commits and
# changes nothing would pass a check made on the configuration text.
#
# The policy goes on the peer group TRANSITO as well as on the two addressed
# sessions, because the check fires only when NO neighbour would be sent the
# prefix - and this box also carries 10.0.0.9, a member of that group. Leaving
# it out is a box that still advertises the prefix somewhere, which is not the
# case being tested; it also means the group's inheritance is exercised rather
# than assumed.
SESSION_ADDR="203.0.115.9"
SESSION_ENV="10.0.0.2 51234 $SESSION_ADDR 22"
CARRIER=$($HX -c "configure" -c "ip route 203.0.115.0/24 blackhole" \
          -c "router bgp 65000" -c "address-family ipv4 unicast" \
          -c "network 203.0.115.0/24" \
          -c "exit-address-family" -c "exit" -c "commit" 2>&1)
expect_in "the prefix this session runs over is announced" "commit complete" "$CARRIER"
n=0
while [ "$n" -lt 40 ]; do
  [ "$(peer_has 203.0.115.0/24)" -ge 1 ] && break
  sleep 1; n=$((n + 1))
done
echo "  the neighbour has it: $(peer_has 203.0.115.0/24)   (${n}s)"
expect "and the neighbour has it before anything is changed" \
  test "$(peer_has 203.0.115.0/24)" -ge 1

# The legitimate neighbour FIRST, because it must leave the prefix where it
# is: an outbound policy that still permits the prefix is not a withdrawal,
# and a gate that fires on it is one the operator learns to type past.
KEEPS=$(SSH_CONNECTION="$SESSION_ENV" $HX -c "configure" \
        -c "ip prefix-list PL-KEEP seq 5 permit 203.0.115.0/24" \
        -c "route-map RM-KEEP permit 10" \
        -c "match ip address prefix-list PL-KEEP" -c "exit" \
        -c "router bgp 65000" -c "address-family ipv4 unicast" \
        -c "neighbor 10.0.0.2 route-map RM-KEEP out" \
        -c "neighbor 10.1.0.2 route-map RM-KEEP out" \
        -c "neighbor TRANSITO route-map RM-KEEP out" \
        -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$KEEPS" | tail -3 | sed 's/^/  /'
expect_in "a policy that still permits it commits with no gate" \
  "commit complete" "$KEEPS"
expect_not_in "and the operator is not asked for a word" "commit cutoff" "$KEEPS"
expect "and the neighbour still has it" test "$(peer_has 203.0.115.0/24)" -ge 1

# And the dangerous one: the same shape, permitting something else. The
# implicit deny at the end of a route-map is what takes the prefix away, which
# is the case an operator does not see by reading their own policy back.
CUT=$(SSH_CONNECTION="$SESSION_ENV" $HX -c "configure" \
      -c "ip prefix-list PL-OTHER seq 5 permit 203.0.113.0/24" \
      -c "route-map RM-CUT permit 10" \
      -c "match ip address prefix-list PL-OTHER" -c "exit" \
      -c "router bgp 65000" -c "address-family ipv4 unicast" \
      -c "neighbor 10.0.0.2 route-map RM-CUT out" \
      -c "neighbor 10.1.0.2 route-map RM-CUT out" \
      -c "neighbor TRANSITO route-map RM-CUT out" \
      -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$CUT" | grep -iE "error|cutoff|session" | head -4 | sed 's/^/  /'
expect_in "the box says which prefix would stop being advertised" \
  "203.0.115.0/24" "$CUT"
expect_in "and names the address the session arrived on" "$SESSION_ADDR" "$CUT"
expect_in "and asks for the commit by name" "commit cutoff" "$CUT"
expect_not_in "and nothing is applied" "commit complete" "$CUT"
expect "and the neighbour still has the prefix" \
  test "$(peer_has 203.0.115.0/24)" -ge 1

# Asked for by name: applied, and applied as a confirmed commit, because the
# word does not itself give the operator a way back.
CUTOK=$(SSH_CONNECTION="$SESSION_ENV" $HX -c "configure" \
        -c "ip prefix-list PL-OTHER seq 5 permit 203.0.113.0/24" \
        -c "route-map RM-CUT permit 10" \
        -c "match ip address prefix-list PL-OTHER" -c "exit" \
        -c "router bgp 65000" -c "address-family ipv4 unicast" \
        -c "neighbor 10.0.0.2 route-map RM-CUT out" \
        -c "neighbor 10.1.0.2 route-map RM-CUT out" \
        -c "neighbor TRANSITO route-map RM-CUT out" \
        -c "exit-address-family" -c "exit" -c "commit cutoff" 2>&1)
echo "$CUTOK" | tail -4 | sed 's/^/  /'
expect_in "'commit cutoff' applies it" "commit complete" "$CUTOK"
expect_in "and arms a rollback, because the session may not survive it" \
  "commit confirmed 10" "$CUTOK"
n=0
while [ "$n" -lt 40 ]; do
  [ "$(peer_has 203.0.115.0/24)" -eq 0 ] && break
  sleep 1; n=$((n + 1))
done
echo "  after the policy: the neighbour has $(peer_has 203.0.115.0/24)   (${n}s)"
expect "and the neighbour really stops seeing the prefix" \
  test "$(peer_has 203.0.115.0/24)" -eq 0
expect "while the one the policy permits is still there" \
  test "$(peer_has 203.0.113.0/24)" -ge 1

# The confirmation, which also proves the revert was armed on the box rather
# than promised. Left unconfirmed, the lab would be reverted under itself ten
# minutes later.
# `configure` first: a fresh hexash process has no candidate until it is
# entered, and `commit` on its own answers "no candidate configuration" -
# which is not a confirmation, and the box would have reverted the change
# under the lab ten minutes later.
CONF=$($HX -c "configure" -c "commit" 2>&1)
expect_in "and a later commit confirms it" "confirmed" "$CONF"

# A session that did not arrive over SSH has no address to check, and the box
# says which check it could not make rather than letting it be assumed.
CONSOLE=$($HX -c "configure" \
          -c "ip prefix-list PL-KEEP seq 6 permit 198.18.0.0/24" \
          -c "commit" 2>&1)
echo "$CONSOLE" | grep -i "note:" | head -2 | sed 's/^/  /'
expect_in "a console session is told the check did not run" \
  "did not arrive over SSH" "$CONSOLE"
expect_in "and is not gated on a check nobody could make" \
  "commit complete" "$CONSOLE"

verdict
