#!/bin/sh
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# O plano de controle sobre o TAP do linux_cp, e a interface por baixo dele.
#
# BGP, OSPF e IS-IS chegando na FIB do VPP, e o que a VTY dirige na interface
# que os carrega: MTU, enderecos, descricao, estado administrativo, rotas
# estaticas com rollback, e router advertisements.
#
#   [netns peer] AS 65001 / OSPF / IS-IS       [default] HexaRouting
#     dum-bgp   203.0.113.0/24  -- BGP  -->
#     dum-ospf  198.18.0.0/24   -- OSPF -->    FRR -> kernel -> linux_nl
#     dum-isis  198.19.0.0/24   -- ISIS -->                  -> FIB do VPP
#     veth-out 10.0.0.2 ------(veth)------ veth-vpp -> VPP -> vppwan0 10.0.0.1
#
# IS-IS is the test that matters for punt: it does not run over IP, it uses CLNS
# direto sobre o enlace. Se o caminho de punt so entendesse IPv4/IPv6, BGP e
# OSPF subiriam e o IS-IS ficaria mudo.
#
# BGP policy - route-maps, AS-path lists, communities, graceful shutdown and
# RPKI - is lab/bgp-policy.sh and is no longer here. The two halves in one file
# ran over 400 s, which made this the slowest lab in the suite and therefore
# its floor: the per-lab cap in tools/run-all-labs.sh had to be raised twice in
# one day to stop it being killed (#167).
#
# The topology went with it. The second link, the third autonomous system and
# the second BGP session exist ONLY so a policy has two paths to choose between
# and a second origin to leave alone; nothing left in this file reads them, and
# a namespace nothing asserts on is a minute of convergence that proves
# nothing. They are built in bgp-policy.sh instead.
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

# A SECOND link with no address on either end, for BGP unnumbered (#208). The
# whole point of the form is that neither side has an IPv4 address, so it
# cannot share the link above: a peer that can be reached at 10.0.0.2 is not
# a demonstration of anything.
ip link add veth9-out type veth peer name veth9-vpp 2>/dev/null
ip link set veth9-vpp up
ip link set veth9-out netns peer
ip netns exec peer ip link set veth9-out up
# one prefix per protocol, so there is no doubt about which brought which
for d in bgp:203.0.113.1/24 ospf:198.18.0.1/24 isis:198.19.0.1/24; do
  n=${d%%:*}; a=${d##*:}
  ip netns exec peer ip link add dum-$n type dummy
  ip netns exec peer ip addr add $a dev dum-$n
  ip netns exec peer ip link set dum-$n up
done

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -25 /tmp/vpp.log; exit 1; }

vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
vppctl lcp create host-veth-vpp host-if vppwan0

# The TAP inherits MTU 9000 while the wire is at 1500. OSPF compares MTU in DD
# packets, so the adjacency stalls in Exchange without saying why. MTU has to
# be explicit, never inherited.
vppctl set interface mtu 1500 host-veth-vpp

# NOT enabled here any more, and that is the point of the assertion below.
# Until #168 this line existed in this lab and in the documentation and NOWHERE
# in the product, so the lab proved a box nobody received: on a shipped
# appliance an operator configured IS-IS, the commit succeeded, isisd came up,
# and no adjacency ever formed. The product does it now, when the
# configuration contains an IS-IS instance, and the lab has to leave it alone
# or the assertion tests nothing.

vppctl set interface ip address host-veth-vpp 10.0.0.1/24

# The second pair, and no address on it. vppwan9 is the name FRR will be given
# in the unnumbered statement below, and it is a linux_cp TAP like any other -
# which is the point: the statement has to name the spelling FRR acts on.
vppctl create host-interface name veth9-vpp
vppctl set interface state host-veth9-vpp up
vppctl lcp create host-veth9-vpp host-if vppwan9
vppctl set interface mtu 1500 host-veth9-vpp
ip link set vppwan9 up

# Recorded before anything commits, so the assertion after the first commit is
# a CHANGE rather than a state that might have been there all along.
OSI_BEFORE=$(vat2 lcp_osi_proto_get '{}' 2>/dev/null | grep -c '"0x83"' || true)

# ---------------- HexaRouting ----------------
mkdir -p /var/run/frr /etc/frr
cat > /etc/frr/zebra.conf <<'EOF'
hostname hexarouting
EOF
# Deliberately EMPTY of BGP. This side's session is configured through the VTY
# further down, so the routes that arrive are evidence that the VTY configured
# a working peering - not that a file written by the lab did (#142).
cat > /etc/frr/bgpd.conf <<'EOF'
hostname hexarouting
EOF
cat > /etc/frr/ospfd.conf <<'EOF'
hostname hexarouting
router ospf
 ospf router-id 10.0.0.1
 network 10.0.0.0/24 area 0
EOF
cat > /etc/frr/isisd.conf <<'EOF'
hostname hexarouting
interface vppwan0
 ip router isis HEXA
exit
router isis HEXA
 net 49.0001.0000.0000.0001.00
 is-type level-2-only
EOF
chown -R frr:frr /var/run/frr /etc/frr
# These are the daemons THIS LAB arranges, not what the product ships: an
# installed box inherits Debian's /etc/frr/daemons, where bgpd, ospfd and isisd
# are all "no" (#99). Starting them by hand here is what let that go unnoticed,
# so it is said out loud rather than left as a list somebody skims.
# No -M rpki here, and no frr-rpki-rtrlib to install. Nothing in this lab
# validates anything, and bgpd refuses to start when told to load a module it
# cannot find - which is a whole lab reporting "bgpd is not running" for a
# feature it never exercises. That module, and the cache behind it, are
# lab/bgp-policy.sh. start_frr only asks for it when it is installed.
#
# lib.sh is sourced here rather than where it was, because FRR starts before
# that point.
#
# mgmtd and staticd come with start_frr. This lab and lab/bgp-policy.sh knew
# they were needed and six others did not, which is why that knowledge is now
# in one place (#196).
start_frr ospfd isisd bgpd

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
 neighbor veth9-out interface remote-as external
 address-family ipv4 unicast
  network 203.0.113.0/24
  neighbor 10.0.0.1 activate
  neighbor veth9-out activate
 exit-address-family
EOF
cat > /etc/frr/peer/ospfd.conf <<'EOF'
hostname upstream
router ospf
 ospf router-id 10.0.0.2
 network 10.0.0.0/24 area 0
 network 198.18.0.0/24 area 0
EOF
cat > /etc/frr/peer/isisd.conf <<'EOF'
hostname upstream
interface veth-out
 ip router isis HEXA
exit
interface dum-isis
 ip router isis HEXA
exit
router isis HEXA
 net 49.0001.0000.0000.0002.00
 is-type level-2-only
EOF
chown -R frr:frr /var/run/frr/peer /etc/frr/peer
for d in zebra bgpd ospfd isisd; do
  ip netns exec peer /usr/lib/frr/$d -d -N peer -A 127.0.0.1 -f /etc/frr/peer/$d.conf
done

# The assertion helpers and the VTY, needed from here on: the BGP session below
# is configured through the VTY, and everything that waits for its routes is an
# assertion about that.
. /src/lab/verdict.sh
# The BPA plugin, installed where vat2 looks for it on a real box. Nothing
# below sends a bpa_ message, but hexash asks vat2 which messages are
# registered before it touches the dataplane and calls it unusable when
# bpa_pool_add_del is not among them - so without this every interface commit
# here fails for a reason that has nothing to do with the interface.
use_binary_api
HX="python3 /src/src/hexash/hexash.py"

# ---------------- the session, configured through the VTY ----------------
#
# The point of doing it here rather than in bgpd.conf: everything below that
# depends on 203.0.113.0/24 - the FIB wait, the route comparison, `show route
# verbose` - is now evidence that the VTY configured a BGP session that works,
# on a box where nothing else did (#142).
echo "=========== the BGP session, configured through the VTY ==========="
BGPUP=$($HX -c "configure" -c "router bgp 65000" \
        -c "bgp router-id 10.0.0.1" \
        -c "no bgp ebgp-requires-policy" \
        -c "neighbor 10.0.0.2 remote-as 65001" \
        -c "neighbor 10.0.0.2 description upstream-lab" \
        -c "address-family ipv4 unicast" \
        -c "neighbor 10.0.0.2 activate" \
        -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$BGPUP" | tail -3 | sed 's/^/  /'
expect_in "the VTY commits a BGP session" "commit complete" "$BGPUP"

# --- BGP unnumbered over the linux_cp pair (#208) ---
#
# The form an operator writes for eBGP to a directly connected peer without
# spending a /30 on the link:
#
#   neighbor vppwan9 interface remote-as external
#
# The VTY refused it - "vppwan9 has no 'remote-as'", with the AS on that same
# line - and refused the WHOLE candidate with it, so a CGNAT change typed
# beside it went down too. There is no way round that inside the product: the
# statement cannot be typed, and vtysh is the tool this VTY exists to keep an
# operator out of.
#
# Asserted HERE rather than in lab/bgp-policy.sh because this is where the
# linux_cp pair is: the name in the statement is a TAP's, the candidate is
# read back from FRR afterwards, and both of those are the parts a unit test
# cannot hold.
echo "=========== BGP unnumbered, on a link with no address ==========="
UNN=$($HX -c "configure" -c "router bgp 65000" \
      -c "neighbor vppwan9 interface remote-as external" \
      -c "address-family ipv4 unicast" \
      -c "neighbor vppwan9 activate" \
      -c "exit-address-family" -c "exit" -c "commit" 2>&1)
printf '%s\n' "$UNN" | grep -vE "^note" | tail -4 | sed 's/^/  /'
expect_in "the VTY commits an unnumbered neighbour" "commit complete" "$UNN"
expect_not_in "and does not claim the AS is missing" "has no 'remote-as'" "$UNN"

# It reached FRR, which is the half a check on the VTY's own output cannot
# make: a refusal and an acceptance that never left the box look the same
# from here.
RUNFRR=$(vtysh -c "show running-config" 2>&1)
expect_in "FRR holds the statement" \
  "neighbor vppwan9 interface remote-as external" "$RUNFRR"
expect_in "and the peer is in FRR's own summary" "vppwan9" \
  "$(vtysh -c 'show bgp summary' 2>&1)"

# The round trip, which is where a parser that only understands what an
# operator TYPES comes apart. cfg.routing is read back FROM FRR, so from the
# next commit on this is the spelling every check sees - including this one.
# An unnumbered box would otherwise have had its first commit accepted and
# every one after it refused, for a statement nobody typed twice.
NEXT=$($HX -c "configure" -c "interface host-veth-vpp" \
       -c "description enlace de transito" -c "exit" -c "commit check" 2>&1)
printf '%s\n' "$NEXT" | grep -vE "^note" | tail -3 | sed 's/^/  /'
expect_in "an unrelated change commits cleanly afterwards" \
  "configuration check succeeds" "$NEXT"
expect_not_in "the statement read back from FRR is not refused on the way in" \
  "has no 'remote-as'" "$NEXT"

# --- and whether the session comes up, which is a different question ---
#
# It does not, and the reason is in the DATAPLANE and not in anything above.
# Measured on this topology with tcpdump on both ends of the pair:
#
#   * our Router Advertisements leave the TAP, cross VPP and arrive on the
#     wire - the peer learns our link-local and goes to Active, trying to
#     open the session;
#   * the peer's Router Advertisements arrive on the wire and NEVER reach the
#     TAP. `show ip6 mfib` says why: ff02::1/128 is (*, Accept) with a chain
#     of one `dpo-receive` and no path to the pair's TAP, so VPP receives the
#     advertisement itself - `show errors` counts it - and linux_cp has
#     nothing on the ip6-multicast arc to punt it with.
#
# FRR learns an unnumbered peer's address from that advertisement and from
# nothing else, so zebra never has one and bgpd sits at Idle with "remote AS
# 0". It is a punt gap for IPv6 multicast over linux_cp, it is the same shape
# as #168 (IS-IS frames that no lab and no product punted), and it belongs in
# an issue of its own rather than in a vppctl line here that would make this
# lab prove a box nobody receives.
#
# So this prints the state and asserts the half that IS this box's: the
# session is configured, known to bgpd, and attempted. Nothing here asserts
# Established, because on today's dataplane that would be asserting a wish.
echo "=== o estado da sessao unnumbered ==="
n=0
while [ "$n" -lt 60 ]; do
  vtysh -c "show bgp summary" 2>/dev/null | grep -qE "^vppwan9" && break
  sleep 2; n=$((n + 2))
done
vtysh -c "show bgp summary" 2>&1 | grep -E "^Neighbor|^vppwan9" | sed 's/^/  /'
ip netns exec peer vtysh -N peer -c "show bgp summary" 2>&1 \
  | grep -E "^veth9-out" | sed 's/^/  peer: /'
echo "  ff02::1 no MFIB do VPP:"
vppctl show ip6 mfib | grep -A4 "ff02::1/128" | head -5 | sed 's/^/    /'
# The peer reached Active, which is the half of the exchange that DOES cross
# the pair: it only knows where to send a SYN because our advertisement got
# there. That is the assertion the punt gap leaves standing.
expect "the far end learned this box over the pair and is opening the session" \
  sh -c "ip netns exec peer vtysh -N peer -c 'show bgp summary' 2>/dev/null \
         | grep -E '^veth9-out' | grep -qvE ' Idle '"

# --- IS-IS frames are punted because the PRODUCT punts them (#168) ---
#
# Nothing in this lab ran `lcp osi-proto enable`. The commit above carried an
# IS-IS instance - it is in the configuration the VTY read back from FRR - so
# the product enabled the punt as part of applying it.
#
# The negative half is what gives this meaning, and it is taken BEFORE the
# commit, above: with no punt, IS-IS forms no adjacency at all, so every
# IS-IS assertion further down is evidence about the product rather than
# about a line this file used to run.
# Asked of the same API the product uses to set it, because `show lcp` does not
# report the OSI protocols at all - the first version of this assertion grepped
# that output and failed while the adjacency was up, which is the assertion
# being wrong rather than the product.
# VPP answers with a HEX STRING - "0x83" for one protocol, bare "0x" for none
# - not with a list of numbers. The first version of this counted "131" and
# found nothing on a box that was punting, which is the assertion being wrong
# rather than the product.
osi_count() {
  vat2 lcp_osi_proto_get '{}' 2>/dev/null | grep -c '"0x83"' || true
}
echo "  OSI protocols linux_cp is punting, after the commit: $(osi_count)"
expect "the product enabled the OSI punt IS-IS needs" test "$(osi_count)" -ge 1
expect "and it was not already on before the commit" test "${OSI_BEFORE:-1}" -eq 0
# A `no` form that is CONFIGURATION rather than a removal. There is no
# `bgp ebgp-requires-policy` line for it to take away, so it has to be staged
# as written - and every eBGP session here needs it, which is why the check
# that `no X` removes X must not swallow this one.
expect_in "a 'no' with nothing to remove is kept as configuration" \
  "no bgp ebgp-requires-policy" "$(vtysh -c 'show running-config')"

echo "=== aguardando as tres rotas chegarem na FIB do VPP ==="
n=0
while [ "$n" -lt 90 ]; do
  ok=0
  for pfx in 203.0.113.0/24 198.18.0.0/24 198.19.0.0/24; do
    vppctl show ip fib "$pfx" 2>/dev/null | grep -q "^$pfx" && ok=$((ok+1))
  done
  [ "$ok" -eq 3 ] && break
  sleep 1; n=$((n+1))
done
echo "(${n}s, $ok de 3)"

echo
echo "=== adjacencias ==="
vtysh -c "show bgp ipv4 unicast summary" 2>/dev/null | grep -E "^10\.0\.0\.2" \
  | awk '{print "BGP   : peer "$1" AS "$3" up "$9" prefixos "$10}'
printf 'OSPF  : '; vtysh -c "show ip ospf neighbor" 2>/dev/null | grep -E "^10\.0\.0\.2" | awk '{print "neighbour "$1" state "$3}'
printf 'IS-IS : '; vtysh -c "show isis neighbor" 2>/dev/null | grep -iE "upstream" | awk '{print "neighbour "$1" state "$4" interface "$2}'

echo
echo "=== routes in the Linux FIB (installed by FRR) ==="
ip route show | grep -E "203.0.113|198.18|198.19" | sed 's/^/  /'

echo
echo "=== routes in the VPP FIB (what actually matters) ==="
for pfx in 203.0.113.0/24 198.18.0.0/24 198.19.0.0/24; do
  printf '  %-16s ' "$pfx"
  vppctl show ip fib "$pfx" 2>/dev/null | grep -oE "ipv4 via [0-9.]+ [a-z0-9-]+" | head -1 || echo "AUSENTE"
done

echo
echo "=========== VERDICT ==========="
for pfx in 203.0.113.0/24 198.18.0.0/24 198.19.0.0/24; do
  expect "$pfx reached the VPP FIB" sh -c "vppctl show ip fib $pfx | grep -q '^$pfx'"
done
# The STATE column, not "up" anywhere in the output. The peer's IS-IS hostname
# is literally "upstream", so `grep -qi up` matched the System Id and passed
# with the adjacency in Init or Down - which is every state except the one
# being asserted.
expect "IS-IS adjacency is up" \
  sh -c "vtysh -c 'show isis neighbor' 2>/dev/null | tr -d '\r' \
         | awk 'NR>1 && \$0 ~ /Up|UP/ {found=1} END {exit !found}'"

# --- MTU committed through the VTY reaches BOTH sides ---
# The lab sets 1500 by hand at the top, which is what makes OSPF come up here.
# This proves the operator can now do the same through the configuration, and
# that it lands on the VPP interface AND its TAP. Setting only the TAP leaves
# the packet path untouched, which is the half that matters and the half a
# quick test would miss.
# Printed before anything parses it. Two things in the product read this
# output - set_host_mtu and the interface-existence check - and a parse
# written against a remembered format is a parse that finds nothing, in
# silence, on the day VPP reorders a field.
echo "  the linux_cp pair, as VPP prints it:"
vppctl show lcp | sed 's/^/    /'

# The MTU as a VALUE, from the column that holds it.
#
# The assertions here used to be `show interface | grep -q 1500`, which matches
# any column with that number in it - a counter will do - and passed while the
# interface sat at 1450. An assertion that can be satisfied by a packet count
# is not an assertion about the MTU.
mtu_of() { vppctl show interface host-veth-vpp | awk 'NR==2 {split($4,a,"/"); print a[1]}'; }

$HX -c "configure" -c "interface host-veth-vpp" -c "mtu 1400" -c "exit" \
    -c "commit" > /tmp/mtu.log 2>&1 || true
tail -3 /tmp/mtu.log | sed 's/^/  commit: /'
expect "the dataplane interface took the committed MTU" test "$(mtu_of)" = "1400"
expect "and so did its TAP" sh -c "ip link show vppwan0 | grep -q 'mtu 1400'"

# The assertion above does not prove the product did it. lcp-sync is on by
# default, so VPP mirrors an MTU change onto the TAP by itself, and the
# assertion passes whether set_host_mtu works or is dead code. Turning the
# feature off is the only way to tell the two apart - and this repository has
# already shipped one parser that found nothing quietly.
vppctl lcp lcp-sync off
ip link set dev vppwan0 mtu 1500
$HX -c "configure" -c "interface host-veth-vpp" -c "mtu 1450" -c "exit" \
    -c "commit" > /tmp/mtu2.log 2>&1 || true
tail -2 /tmp/mtu2.log | sed 's/^/  commit: /'
expect "with lcp-sync off, the dataplane still took the MTU" \
  test "$(mtu_of)" = "1450"
expect "with lcp-sync off, the TAP took it too - so hexash moved it, not VPP" \
  sh -c "ip link show vppwan0 | grep -q 'mtu 1450'"

vppctl lcp lcp-sync on

# Read back BEFORE the restore below, or this asserts on a value the next two
# lines are about to change.
expect "the MTU is in the running configuration"   sh -c "$HX -c 'show running-config' | grep -q 'mtu 1450'"

# Put the MTU back, through vppctl and not a commit.
#
# Not a commit: a commit pushes a rollback revision, and the rollback test
# further down depends on `rollback 1` meaning the state before the static
# route. Restoring through the VTY shifted that by one and the rollback test
# silently began restoring the wrong revision.
#
# Here, and not later: the peer is at 1500, OSPF compares MTU in Database
# Description packets, and at 1450 the adjacency drops and takes
# 198.18.0.0/24 with it. Everything below would then assert on a routing table
# this test broke - which is what a `grep -qc` typo hid for months.
vppctl set interface mtu packet 1500 host-veth-vpp
echo "  mtu back to: $(mtu_of)"
expect "the MTU is back to the peer's" test "$(mtu_of)" = "1500"

# The adjacency returning is NOT the route returning. After Full there is still
# an LSA exchange, an install into the kernel and a linux_nl walk into the VPP
# FIB, and the section below reads the FIB. Waiting for the neighbour and then
# asserting on routes is how this test came to report a table that was still
# converging.
n=0
until vppctl show ip fib 198.18.0.0/24 | grep -q '^198.18.0.0/24' \
      || [ "$n" -ge 90 ]; do
  sleep 1; n=$((n + 1))
done
expect "and the OSPF route came back into the FIB after the MTU change (${n}s)" \
  sh -c "vppctl show ip fib 198.18.0.0/24 | grep -q '^198.18.0.0/24'"


# The restore that used to be here sent its output to /dev/null and ended in
# "|| true", so whether it worked was never checked - and it was not working:
# the interface reached the shutdown test below at 1450 and OSPF stuck in
# ExStart. It is done above now, with an assertion, which is the difference
# between putting something back and believing you did.

# --- the forwarding table, and telling it apart from FRR's (#95) ---
#
# Everything above asserts on "vppctl show ip fib" directly, because that is
# the table that forwards. This section asserts that the VTY says the same
# thing - and that when the two tables disagree, it says WHICH.
echo
# The two learned prefixes, raw, side by side. `show route` was listing one of
# them and not the other while both were asserted into the FIB above, and the
# difference is not visible from the summary.
echo "  --- 198.18.0.0/24 in the FIB, raw ---"
vppctl show ip fib 198.18.0.0/24 | sed 's/^/    /'
echo "  --- 198.19.0.0/24 in the FIB, raw ---"
vppctl show ip fib 198.19.0.0/24 | sed 's/^/    /'

echo "=========== show route ==========="
SHOW_ROUTE=$($HX -c "show route" 2>&1)
printf '%s\n' "$SHOW_ROUTE" | head -12 | sed 's/^/  /'

echo
echo "=========== a static route through the VTY ==========="
# 10.0.0.2 is the peer, on the connected /24, so this one is legitimate.
#
# Typed in the `router static` block, which is where static routes live now
# (#193). FRR has no such block - its static routes are top-level `ip route`
# statements - so this is a hierarchy the VTY provides and translates, and the
# assertion below is on the VPP FIB: if the translation were wrong the route
# would not be anywhere. The old spelling is still accepted and is exercised
# further down, in the MTU section, which types `ip route` directly.
$HX -c "configure" -c "router static" \
    -c "route 192.168.77.0/24 10.0.0.2" -c "exit" -c "commit" \
  > /tmp/staticroute.log 2>&1 || true
cat /tmp/staticroute.log | sed 's/^/  commit: /'
echo "  --- what FRR holds now ---"
vtysh -c "show running-config" 2>/dev/null | grep -E "^ip route" | sed 's/^/    /'
echo "  --- frr-reload present? ---"
ls -l /usr/lib/frr/frr-reload.py 2>&1 | sed 's/^/    /'
sleep 3
echo "  FRR:  $(vtysh -c 'show ip route 192.168.77.0/24' 2>/dev/null | grep -c 192.168.77) line(s)"
echo "  VPP:  $(vppctl show ip fib 192.168.77.0/24 2>/dev/null | grep -c '^192.168.77.0/24') entry"
STATIC_FIB=$(vppctl show ip fib 192.168.77.0/24 2>/dev/null | grep -c "^192.168.77.0/24")
STATIC_VTY=$($HX -c "show route" 2>&1 | grep -c "192.168.77.0/24")

echo
echo "  mtu before rollback: $(mtu_of)"
echo "=========== rollback removes it from the FIB ==========="
# A rollback that leaves the route forwarding is not a rollback, and FRR's
# answer cannot tell you: it is the FIB that has to lose it.
$HX -c "configure" -c "rollback 1" -c "commit" > /tmp/rbroute.log 2>&1 || true
sleep 3
ROLLED_FIB=$(vppctl show ip fib 192.168.77.0/24 2>/dev/null | grep -c "^192.168.77.0/24")
echo "  after rollback, in the VPP FIB: $ROLLED_FIB"

echo
echo "=========== a next hop on no connected network is refused ==========="
# Accepted, it would sit in FRR and never reach the FIB - the exact failure
# 'show route verbose' exists to find afterwards. Cheaper to say so here.
BADNH=$($HX -c "configure" -c "ip route 10.55.0.0/16 172.31.99.1" \
            -c "commit check" 2>&1)
printf '%s\n' "$BADNH" | tail -3 | sed 's/^/  /'

echo
echo "=========== show route verbose on a HEALTHY box ==========="
#
# The assertion here is that it says NOTHING is wrong, and that is the harder
# half. A route in FRR and not in the FIB is asserted in test/test_routes.py
# against a synthetic table, where the fault can be stated exactly.
#
# It is NOT staged here, and the attempt is worth recording: a static route
# added through FRR reaches the FIB by way of linux_nl, and taking it out of
# the FIB with "vppctl ip route del" does not hold - linux_nl puts it back.
# Producing a genuine divergence live means breaking that sync, and a lab that
# fakes the fault it is testing for proves nothing about the real one.
#
# What this box WILL have is routes FRR holds through interfaces VPP does not
# own - here the container's eth0, on a real appliance the management port that
# MUST NOT be handed to VPP. Those are permanently in FRR and permanently
# absent from the FIB, correctly. Reporting them would put standing alarms on
# every box, and an alarm that is always on is one nobody reads.
VERBOSE=$($HX -c "show route verbose" 2>&1)
printf '%s\n' "$VERBOSE" | sed 's/^/  /'
echo "  --- what FRR holds that VPP does not own ---"
vtysh -c "show ip route json" 2>/dev/null \
  | tr ',' '\n' | grep -o '"interfaceName":"[^"]*"' | sort -u | sed 's/^/    /'

expect "the VTY lists the learned routes from the FIB" \
  sh -c "printf '%s' \"$SHOW_ROUTE\" | grep -q '203.0.113.0/24'"
# COUNT them. `grep -qc` is `grep -q`: the -q wins and the -c is discarded, so
# the exit status only said "at least one line matched" and a show route that
# had lost the OSPF prefix but kept the IS-IS one passed as "all three".
SEEN=$(printf '%s' "$SHOW_ROUTE" | grep -c '198\.1[89]')
expect "and all three of them (198.18 and 198.19 seen: $SEEN)" \
  test "$SEEN" -ge 2
# The housekeeping VPP creates per interface address must not be in there, or
# the three routes that matter are lost among a dozen entries nobody asked for.
expect_not_in "without the per-neighbour adjacencies" "10.0.0.2/32" "$SHOW_ROUTE"

expect "a static route committed through the VTY reaches the FIB" \
  test "${STATIC_FIB:-0}" -ge 1
expect "and the VTY reports it" test "${STATIC_VTY:-0}" -ge 1
expect "rolling it back removes it from the FIB" test "${ROLLED_FIB:-1}" -eq 0

expect_in "an unreachable next hop is refused at commit check" \
  "on no connected network" "$BADNH"
expect_in "and says what would have happened" \
  "never reach the forwarding table" "$BADNH"

# Crying wolf is the failure mode of a command like this. A route through an
# interface VPP does not own - the management port on a real box - is in FRR
# and absent from the FIB for ever, correctly, and flagging it would leave a
# standing alarm on every machine.
expect_in "a healthy box reports no disagreement" \
  "every route FRR holds is in the forwarding table" "$VERBOSE"
expect_not_in "and does not flag routes off the dataplane" \
  "NOT forwarding" "$VERBOSE"
# The learned routes must be in there with their real next hop, or "no
# disagreement" would also be true of a comparison that found nothing at all.
expect_in "while still listing what the FIB holds" "203.0.113.0/24" "$VERBOSE"
expect_in "with the next hop it forwards to" "10.0.0.2" "$VERBOSE"
# A local address is the box itself. Traffic to it is received, not dropped,
# and calling that "not forwarding" is simply wrong.
expect_in "a local address is not reported as a failure" "local" "$VERBOSE"

# --- description and administrative state (#129) ---
#
# Every assertion here reads the DATAPLANE, not `show running-config`.
#
# The first version of this section read the running configuration, which
# includes FRR's half - and `description` and `shutdown` were falling through
# the VTY into FRR, which accepts both words inside an interface stanza. So the
# assertions matched the copy that had gone to the wrong place and passed while
# nothing reached the interface. Three of them were vacuous.
#
# The tag comes back from sw_interface_dump and the state from `show
# interface`, so a value only FRR knows cannot satisfy either.


# The rollback test above restored an older revision, and older revisions carry
# an older MTU - which is the rollback working, not failing. Every section after
# it has to re-establish its own preconditions rather than assume the state it
# left behind.
#
# Restored through vppctl so no rollback revision is pushed; a commit here
# would shift what `rollback 1` means for anything added later.
vppctl set interface mtu packet 1500 host-veth-vpp
n=0
until vppctl show ip fib 198.18.0.0/24 | grep -q '^198.18.0.0/24' \
      || [ "$n" -ge 90 ]; do
  sleep 1; n=$((n + 1))
done
expect "the MTU and the OSPF route are back before the shutdown test (${n}s)" \
  sh -c "[ \"$(mtu_of)\" = 1500 ] && vppctl show ip fib 198.18.0.0/24 | grep -q '^198.18.0.0/24'"

echo "  mtu before the shutdown test: $(mtu_of)"
echo "=========== description and shutdown ==========="
tag_of() { vat2 sw_interface_dump '{"sw_if_index":4294967295,"name_filter_valid":false,"name_filter":""}' 2>/dev/null | python3 -c '
import json,sys
for r in json.load(sys.stdin):
    if r["interface_name"] == "host-veth-vpp":
        print((r.get("tag") or "").strip())
'; }
state_of() { vppctl show interface host-veth-vpp | awk 'NR==2 {print $3}'; }

$HX -c "configure" -c "interface host-veth-vpp" \
    -c "description uplink para o transito" -c "exit" -c "commit" \
    > /tmp/desc.log 2>&1 || true
tail -2 /tmp/desc.log | sed 's/^/  commit: /'
echo "  dataplane tag: '$(tag_of)'"
expect_in "the description is on the dataplane interface itself" \
  "uplink para o transito" "$(tag_of)"
expect "and reached the TAP, so ip and FRR agree with the VTY" \
  sh -c "ip -d link show vppwan0 | grep -q 'alias uplink para o transito'"
# It must NOT have been staged as an FRR statement. That is where it was going,
# and FRR accepting it silently is what made the failure invisible.
expect_not_in "and it was not handed to FRR instead" \
  "description uplink" "$(vtysh -c 'show running-config' 2>/dev/null)"

# Shut it, and assert the STATE COLUMN rather than grepping the whole output
# for the word "down" - which matches half a dozen unrelated lines and passes
# whatever happens.
$HX -c "configure" -c "interface host-veth-vpp" -c "shutdown" -c "exit" \
    -c "commit" > /tmp/shut.log 2>&1 || true
tail -2 /tmp/shut.log | sed 's/^/  commit: /'
echo "  dataplane state: $(state_of)"
expect "the dataplane interface is administratively down" \
  test "$(state_of)" = "down"
expect "and so is its TAP, so FRR stops holding the adjacency up" \
  sh -c "ip link show vppwan0 | grep -q 'state DOWN'"
# Read as a BLOCK, not by counting lines back from the statement. The first
# version grepped four lines above ` shutdown` for the interface name, which
# held only until a statement was added to the block between the two - and one
# was, so it started failing for a reason that had nothing to do with
# shutdown.
block_of() {   # block_of <interface name>
  $HX -c 'show running-config' 2>/dev/null \
    | awk -v want="interface $1" '
        $0 == want { inside = 1; next }
        inside && /^[^ ]/ { inside = 0 }
        inside { print }'
}
expect_in "shutdown is in the running configuration, for THIS interface" \
  "shutdown" "$(block_of host-veth-vpp)"
expect_not_in "and not on the other one" \
  "shutdown" "$(block_of host-veth2-vpp)"

$HX -c "configure" -c "interface host-veth-vpp" -c "no shutdown" -c "exit" \
    -c "commit" > /tmp/noshut.log 2>&1 || true
echo "  dataplane state: $(state_of)"
expect "no shutdown brought the dataplane interface back" \
  test "$(state_of)" = "up"
expect_not_in "and the running configuration stopped saying shutdown" \
  " shutdown" "$($HX -c 'show running-config')"

# The real proof that `no shutdown` worked is the adjacency coming back, not
# the State column saying "up". Waited for, because OSPF takes its hello and
# dead timers to notice - and asserted, because if it never comes back the
# shutdown test has broken the box for everything after it.
n=0
until vtysh -c 'show ip ospf neighbor' 2>/dev/null | grep -q Full || [ "$n" -ge 60 ]; do
  sleep 1; n=$((n + 1))
done
if ! vtysh -c 'show ip ospf neighbor' 2>/dev/null | grep -q Full; then
  echo "  --- it did not come back; what the box looks like ---"
  echo "  vpp:   $(vppctl show interface host-veth-vpp | sed -n 2p)"
  echo "  linux: $(ip -br link show vppwan0)"
  echo "  addr:  $(ip -br addr show vppwan0)"
  vtysh -c 'show ip ospf interface vppwan0' 2>&1 | head -6 | sed 's/^/  ospf:  /'
  vtysh -c 'show ip ospf neighbor' 2>&1 | head -4 | sed 's/^/  nbr:   /'
fi
expect "the OSPF adjacency came back after no shutdown (${n}s)" \
  sh -c "vtysh -c 'show ip ospf neighbor' | grep -q Full"

# --- addresses (#130) ---
#
# The interface already carries 10.0.0.1/24, set by hand at the top of this
# lab and carrying the OSPF adjacency. A SECOND address is added through the
# VTY and then removed, which tests the two things that actually break:
# whether the address reaches the TAP so FRR can see it, and whether removing
# one takes the others with it.
echo "=========== addresses ==========="
addrs_of() { vppctl show interface addr host-veth-vpp | grep -o 'L3 .*' | sed 's/L3 //'; }
echo "  before: $(addrs_of | tr '\n' ' ')"

$HX -c "configure" -c "interface host-veth-vpp" \
    -c "ipv4 address 10.9.9.1/24" -c "ipv6 address 2001:db8:9::1/64" \
    -c "exit" -c "commit" > /tmp/addr.log 2>&1 || true
tail -2 /tmp/addr.log | sed 's/^/  commit: /'
echo "  after:  $(addrs_of | tr '\n' ' ')"
expect_in "the new IPv4 address is on the dataplane" "10.9.9.1/24" "$(addrs_of)"
expect_in "the IPv6 address too" "2001:db8:9::1/64" "$(addrs_of)"
expect_in "and the address it already had is still there" "10.0.0.1/24" "$(addrs_of)"
# FRR sees the TAP, not the dataplane interface. An address that does not
# reach the TAP is an address no routing protocol can use.
expect "the address reached the TAP, so FRR can use it" \
  sh -c "ip addr show vppwan0 | grep -q '10.9.9.1/24'"
expect "and the IPv6 one did too" \
  sh -c "ip -6 addr show vppwan0 | grep -q '2001:db8:9::1/64'"

# Removing one MUST NOT take the others. sw_interface_add_del_address has a
# del_all flag one field away from the one being set here, and "remove this
# address" turning into "remove every address" would drop the adjacency and
# the management path on a real box.
$HX -c "configure" -c "interface host-veth-vpp" \
    -c "no ipv4 address 10.9.9.1/24" -c "exit" -c "commit" \
    > /tmp/addrdel.log 2>&1 || true
echo "  after removal: $(addrs_of | tr '\n' ' ')"
expect_not_in "the removed address is gone" "10.9.9.1/24" "$(addrs_of)"
expect_in "and the one that was there before survived" "10.0.0.1/24" "$(addrs_of)"
expect_in "and so did the IPv6 address" "2001:db8:9::1/64" "$(addrs_of)"
# Adding and removing a SECONDARY address must not disturb the adjacency,
# which runs over the primary. This is only meaningful because the adjacency
# was asserted Full immediately before the address work started.
expect "the adjacency on the primary address was not disturbed" \
  sh -c "vtysh -c 'show ip ospf neighbor' | grep -q Full"

# --- a policy statement whose daemon is not running (#155) ---
#
# `ip prefix-list` and `route-map` have no `router` line, so the daemon check
# never looked at them. Measured on FRR 10.7.1: with mgmtd stopped, vtysh
# ACCEPTS a prefix-list and stores nothing - no error, and the statement is
# absent from the running configuration afterwards.
#
# A `router bgp` stanza that goes nowhere is noticed, because no session comes
# up. A prefix-list that goes nowhere is invisible until traffic that should
# have been filtered is not.
echo "=========== a policy with its daemon stopped ==========="

# With mgmtd running, an ordinary policy commit must still work. This half is
# not decoration: a check that refuses in both cases is not a check, and one
# that refuses too much gets worked around and is then wrong on the day it was
# right.
OKPOL=$($HX -c "configure" -c "ip prefix-list PL-LAB seq 10 permit 10.9.0.0/16" \
        -c "commit" 2>&1)
echo "$OKPOL" | tail -2 | sed 's/^/  /'
expect_in "with mgmtd running, a prefix-list commits" "commit complete" "$OKPOL"
expect "and the dataplane's control plane really has it" \
  sh -c "vtysh -c 'show running-config' | grep -q 'PL-LAB'"

pkill -f 'frr/mgmtd' 2>/dev/null
sleep 2
echo "  daemons now: $(vtysh -c 'show daemons' 2>&1 | head -1)"
BADPOL=$($HX -c "configure" -c "ip prefix-list PL-GONE seq 10 permit 10.8.0.0/16" \
         -c "commit" 2>&1)
echo "$BADPOL" | tail -3 | sed 's/^/  /'
expect_in "with mgmtd stopped, the commit is refused" "mgmtd" "$BADPOL"
# It used to be enough that the refusal contained "/etc/frr/daemons". That
# passed whatever the sentence around it said - and the sentence was wrong:
# frrinit.sh starts mgmtd itself, so it is NOT a line in that file and an
# operator sent there finds nothing to enable. The advice now depends on which
# daemon is missing, and the assertion is on the advice rather than on a path
# that appears in both versions of it.
expect_in "and the refusal says frr did not finish starting, not 'edit the file'" \
  "started by frr itself" "$BADPOL"
expect_in "and how to get it back" "systemctl restart frr" "$BADPOL"
expect_not_in "and nothing was applied" "commit complete" "$BADPOL"

/usr/lib/frr/mgmtd -d -A 127.0.0.1 2>/dev/null
sleep 2

# --- a static route whose daemon is not running (#197) ---
#
# A static route is neither a `router` line nor a policy statement, so BOTH
# checks above walked past it. Measured on FRR 10.7.1 with staticd and mgmtd
# stopped: frr-reload EXITS 0 having applied nothing. Its own log says
# "[zebra] sending configuration" and "done" for the line, then notices on a
# second pass that the statement is still missing, sends it AGAIN, and exits 0.
# FRR discards a statement no running daemon claims and says so to nobody - so
# the box reported "commit complete" for a default route that forwards nothing,
# which is the most common statement there is on an edge box.
echo "=========== a static route with staticd stopped ==========="
OKSTATIC=$($HX -c "configure" -c "router static" \
           -c "route 10.66.0.0/16 blackhole" -c "exit" -c "commit" 2>&1)
expect_in "with staticd running, a static route commits" "commit complete" \
  "$OKSTATIC"
expect "and FRR really holds it" \
  sh -c "vtysh -c 'show running-config' | grep -q '10.66.0.0/16'"

pkill -f 'frr/staticd' 2>/dev/null
sleep 2
echo "  daemons now: $(vtysh -c 'show daemons' 2>&1 | head -1)"
BADSTATIC=$($HX -c "configure" -c "router static" \
            -c "route 10.67.0.0/16 blackhole" -c "exit" -c "commit" 2>&1)
echo "$BADSTATIC" | tail -4 | sed 's/^/  /'
expect_in "with staticd stopped, the commit is refused" "staticd" "$BADSTATIC"
expect_in "and says what the route would do" "forward nothing" "$BADSTATIC"
# It names the route in the words the operator typed, not FRR's. Typed inside
# `router static`, a route is translated before any check runs, so the refusal
# used to name a statement that appears nowhere in what was typed and nowhere
# in `show configuration` either - an operator who then searched for "ip route"
# found nothing.
expect_in "and names it as the VTY spells it" "router static / route" \
  "$BADSTATIC"
expect_not_in "and nothing was applied" "commit complete" "$BADSTATIC"
expect "and FRR does not hold the route either" \
  sh -c "! vtysh -c 'show running-config' | grep -q '10.67.0.0/16'"

/usr/lib/frr/staticd -d -A 127.0.0.1 2>/dev/null
sleep 2

# --- what a rejected policy statement does to the rest of the commit ---
#
# Open question from #143, and it decides the design of #144 and #145: if one
# bad line can leave the box half-configured, a refusal has to be pre-flight;
# if the whole thing is discarded, the refusal can be about diagnosis.
#
# Measured through the VTY, not through frr-reload directly - an ad-hoc probe
# of frr-reload outside a working FRR was inconclusive, because it failed on a
# VALID configuration too, and reporting that as "one bad line discards all"
# would have been a conclusion the evidence did not support.
echo "=========== a rejected policy line, and what survives it ==========="
BEFORE_PL=$(vtysh -c 'show running-config' | grep -c 'prefix-list' || true)
MIXED=$($HX -c "configure" \
        -c "ip prefix-list PL-GOOD seq 10 permit 10.11.0.0/16" \
        -c "ip prefix-list PL-BAD seq 10 permit 10.12.0.0/8 ge 24 le 16" \
        -c "commit" 2>&1)
echo "$MIXED" | tail -3 | sed 's/^/  /'
AFTER_PL=$(vtysh -c 'show running-config' | grep -c 'prefix-list' || true)
echo "  prefix-lists on the box: $BEFORE_PL -> $AFTER_PL"
vtysh -c 'show running-config' | grep 'prefix-list' | sed 's/^/    /'

# Whatever the answer, it must not be "some of it, silently". Either the commit
# refuses and nothing changes, or it applies and says what it applied.
# And the half that decides whether the operator finds out BEFORE applying.
# `commit check` exists so a change can be validated without touching the box;
# if it passes what the commit then refuses, it is telling the operator the
# configuration is good and the box disagrees a moment later.
CHECKOUT=$($HX -c "configure" \
           -c "ip prefix-list PL-CHK seq 10 permit 10.13.0.0/8 ge 24 le 16" \
           -c "commit check" 2>&1)
echo "$CHECKOUT" | tail -3 | sed 's/^/  check: /'
# The string the product actually prints, which is "configuration check
# succeeds". The first version of this assertion grepped for phrases I imagined
# - "would commit", "no errors", "ok" - none of which the VTY emits, so it
# passed while the check was approving a configuration the commit then refused.
# The check cannot catch this one - frr-reload --test exits 0 on a statement
# FRR itself refuses - and it must not apply-and-roll-back to find out. What it
# CAN do is stop claiming more than it verified, which is what an operator in a
# maintenance window acts on.
expect_in "commit check now refuses the bad range itself" \
  "prefix range refused" "$CHECKOUT"
expect_not_in "and does not claim the whole configuration checks out" \
  "configuration check succeeds
" "$CHECKOUT"

# The caveat is still true, and still has to be said - just not about the line
# above, which the VTY now catches on its own. It is about every routing
# statement the VTY passes through and does not model: `frr-reload.py --test`
# exits 0 on statements FRR itself refuses at commit, so `commit check` must
# not claim to have validated them. Asserted with a statement that is VALID,
# so what is being tested is the wording and not a refusal.
CAVEAT=$($HX -c "configure" -c "router ospf" \
         -c "network 10.44.0.0/24 area 0" -c "exit" -c "commit check" 2>&1)
echo "$CAVEAT" | tail -3 | sed 's/^/  caveat: /'
expect_in "and commit check still says routing is not fully validated" \
  "FRR validates them at commit" "$CAVEAT"

expect "a commit carrying an invalid policy line does not report success" \
  sh -c "! printf '%s' \"$MIXED\" | grep -q 'commit complete'"
expect_not_in "and the invalid line is not on the box" "PL-BAD" \
  "$(vtysh -c 'show running-config')"


# --- router advertisements are off unless asked for (#133) ---
#
# VPP advertises this box as a router every 200 seconds on any interface
# holding an IPv6 address. On a subscriber-facing port that is a second default
# gateway for everybody who gets their prefix by DHCPv6-PD, so this product's
# default is the opposite of VPP's.
#
# Counted on the wire, not read from a status line: `show ip6 interface` prints
# the same text whether advertisements are suppressed or not - measured - so
# the only honest evidence is whether packets appear.
# --- a loopback, created and paired by the product (#179, #182) ---
#
# An operator reported `show interface` listing only local0 and no way to make
# a loopback appear. There was none: no command created one. And the VTY
# already REFUSED `neighbor X update-source Loopback0` for not existing, so the
# box named what was missing and gave no way to make it.
#
# This section is also where the pairing in #182 is proved, because a loopback
# is created and paired entirely by the product - no `vppctl lcp create` in
# sight - so if FRR can see it, the product built the whole path.
echo "=========== a loopback the product creates ==========="
LOOP=$($HX -c "configure" -c "interface loop0" \
       -c "linux-name loop0" \
       -c "ipv4 address 10.99.0.1/32" \
       -c "description router-id, which a port cannot take with it" \
       -c "exit" -c "commit" 2>&1)
echo "$LOOP" | tail -3 | sed 's/^/  /'
expect_in "creating a loopback commits" "commit complete" "$LOOP"

echo "  the dataplane:"; vppctl show interface loop0 2>&1 | head -2 | sed 's/^/    /'
echo "  the kernel:"; ip -br addr show loop0 2>&1 | sed 's/^/    /'
expect "the loopback is on the dataplane" \
  sh -c "vppctl show interface loop0 | grep -q loop0"
# The half that matters, and the reason a loopback is worth having: FRR has to
# see it, or a router-id and an update-source cannot live on it. Nothing in
# this section ran `lcp create` - the product built the pair.
expect "and the kernel has it, so FRR can" \
  sh -c "ip link show loop0 >/dev/null 2>&1"
expect "with the address on it" \
  sh -c "ip -br addr show loop0 | grep -q 10.99.0.1"
expect_in "the pair is in the running configuration, read back from the box" \
  "linux-name loop0" "$($HX -c 'show running-config')"
# And it is usable for what it exists for. FRR refusing this would mean the
# pair is present in VPP and absent from the kernel, which is the failure.
FRRSEES=$(vtysh -c "show interface loop0" 2>&1)
expect_not_in "FRR knows the interface" "Unknown" "$FRRSEES"

# Removing it takes the whole path back down: address, pair, interface.
BACK=$($HX -c "configure" -c "no interface loop0" -c "commit" 2>&1)
echo "$BACK" | tail -2 | sed 's/^/  /'
expect_in "removing it commits" "commit complete" "$BACK"
expect "the loopback is gone from the dataplane" \
  sh -c "! vppctl show interface loop0 2>/dev/null | grep -q '^loop0'"
expect "and its pair is gone from the kernel" \
  sh -c "! ip link show loop0 >/dev/null 2>&1"

echo "=========== router advertisements ==========="
# The capture is started BEFORE the change and stopped after, not started
# after it, and it is held for 30 seconds rather than 14.
#
# Both numbers were measured, not chosen. VPP does not advertise the instant it
# is enabled: it schedules the first of three initial advertisements within
# MAX_INITIAL_RTR_ADVERT_INTERVAL, which is 16 seconds. A 14-second window
# closed just before it, and the dataplane confirmed the reading - send_radv
# true, initial_adverts_count 3, n_advertisements_sent 0. Waiting for the
# unsolicited interval instead would mean a 200-second window per state.
# veth-out lives in the `peer` namespace. Capturing it from the root namespace
# is not an error tcpdump reports usefully once stderr is discarded: it exits,
# the file stays empty, and every count reads zero. That made "the default
# sends none" pass because nothing could ever be counted, on either side of the
# setting. So stderr is kept, and an empty capture is checked for having been a
# capture at all.
count_ra() {   # count_ra <seconds> <command...>
  secs=$1; shift
  ip netns exec peer timeout "$secs" tcpdump -i veth-out -nn -l icmp6 \
    > /tmp/ra.pcap.txt 2> /tmp/ra.tcpdump.err &
  cap=$!
  sleep 1
  "$@" > /tmp/ra.cmd.log 2>&1 || true
  wait $cap 2>/dev/null
  if ! grep -q "listening on veth-out" /tmp/ra.tcpdump.err; then
    echo "CAPTURE-FAILED"
    return
  fi
  # grep -c prints its count AND exits 1 when the count is zero, so a
  # "|| echo 0" fallback appends a SECOND line. The caller then compared a
  # two-line string against a number and every count looked wrong.
  n=$(grep -c "router advertisement" /tmp/ra.pcap.txt 2>/dev/null || true)
  echo "${n:-0}"
}

RA_DEFAULT=$(count_ra 30 $HX -c "configure" -c "interface host-veth-vpp" \
             -c "ipv6 address 2001:db8:5a::1/64" -c "exit" -c "commit")
tail -3 /tmp/ra.cmd.log | sed 's/^/  commit: /'
echo "  advertisements in 30s, by default: ${RA_DEFAULT:-0}"

# The DECISIVE half. Enabling makes VPP send an advertisement at once, so a
# count here is evidence inside a lab window rather than a wait for the
# unsolicited interval.
RA_ON=$(count_ra 30 $HX -c "configure" -c "interface host-veth-vpp" \
        -c "no ipv6 nd suppress-ra" -c "exit" -c "commit")
tail -3 /tmp/ra.cmd.log | sed 's/^/  commit: /'
echo "  advertisements in 30s, once asked for: ${RA_ON:-0}"

expect "asking for advertisements produces them" test "${RA_ON:-0}" -ge 1
# Weaker evidence, and labelled as such: at a 200-second interval, "none in 14
# seconds" is also what an UNSUPPRESSED interface looks like. It is asserted
# because it must hold, not because it distinguishes on its own - the line
# above is what carries the proof.
expect "and the default sends none in the same window" \
  test "${RA_DEFAULT:-0}" -eq 0
expect_in "the exception is in the running configuration" \
  "no ipv6 nd suppress-ra" "$($HX -c 'show running-config')"

# And the way back. This is the half that was BROKEN and had no test: the
# running configuration was read from the device for everything except this
# one setting, which was assumed to be "suppressed" always. So the difference
# between a configuration that said "advertise" and a device that already was
# advertising came out empty, no call was made, and an operator who had
# enabled advertisements on a subscriber port could not turn them off again -
# with `show running-config` not admitting they were on.
#
# Measured differently from the other two, and deliberately. The capture above
# opens BEFORE the commit, because VPP sends its initial advertisements as
# advertising starts and a capture opened afterwards misses them. That same
# choice makes it the wrong instrument here: suppression takes effect at the
# commit, so a window that starts before it counts whatever was already in
# flight - which is exactly what it did, one advertisement, and the assertion
# failed for a reason that had nothing to do with suppression working.
#
# So this half asks the dataplane instead. `n_advertisements_sent` is VPP's own
# count, and a count that does not move across a window is not a matter of
# timing.
adv_sent() {
  vat2 sw_interface_ip6nd_ra_dump '{"sw_if_index":4294967295}' 2>/dev/null \
    | tr ',' '\n' | awk -F: '/n_advertisements_sent/ {gsub(/[^0-9]/,"",$2); s+=$2} END {print s+0}'
}
$HX -c "configure" -c "interface host-veth-vpp" \
    -c "ipv6 nd suppress-ra" -c "exit" -c "commit" > /tmp/ra3.log 2>&1 || true
tail -2 /tmp/ra3.log | sed 's/^/  commit: /'
SENT_BEFORE=$(adv_sent)
RA_AGAIN=$(count_ra 30 true)
SENT_AFTER=$(adv_sent)
echo "  advertisements in 30s after suppressing: on the wire ${RA_AGAIN:-0}, by the dataplane's own count $SENT_BEFORE -> $SENT_AFTER"
expect "suppressing again stops the dataplane sending any" \
  test "$SENT_BEFORE" = "$SENT_AFTER"
expect "and none reach the wire either" test "${RA_AGAIN:-0}" -eq 0
expect_not_in "and the running configuration drops the exception" \
  "no ipv6 nd suppress-ra" "$($HX -c 'show running-config')"

# --- the OSPF MTU stall, which FRR cannot see (#174) ---
#
# docs/routing.md has documented this as Pitfall 1 since before there was a
# check for it: FRR compares the TAP's MTU in its database description packets
# while the wire runs at the dataplane's, so when they differ the adjacency
# sits in Exchange forever with no log line on either side.
#
# Last in the file, and deliberately: it pulls the two MTUs apart, which takes
# the OSPF adjacency down, and every section above needs that adjacency.
#
# The mismatch is made on the DATAPLANE side, with vppctl. Measured: setting
# the Linux side instead propagates back into VPP even with lcp-sync off - the
# two ended up equal and the check correctly said nothing, which is how this
# was found. And it cannot be made with `mtu` at the VTY either, because
# hexash sets both sides itself.
echo "=========== the OSPF MTU stall ==========="
vppctl lcp lcp-sync off
THERE=$(ip -o link show dev vppwan0 | awk '{for(i=1;i<=NF;i++) if($i=="mtu") print $(i+1)}')
vppctl set interface mtu 1400 host-veth-vpp
HERE=$(mtu_of)
echo "  the dataplane is at $HERE, its Linux side at $THERE"
expect "the two really do disagree, or the rest of this proves nothing" \
  sh -c "[ \"$HERE\" != \"$THERE\" ]"
MTUWARN=$($HX -c "configure" -c "ip route 10.77.0.0/16 blackhole" \
          -c "commit check" 2>&1)
echo "$MTUWARN" | grep -i "MTU is" | sed 's/^/  /'
expect_in "the MTU stall is reported" "carries OSPF and its MTU is" "$MTUWARN"
# Both numbers, because "the MTUs disagree" is not actionable and the two
# values are. Read from the box, so the assertion does not depend on what an
# earlier section happened to leave behind.
expect_in "with the dataplane's number" "MTU is $HERE" "$MTUWARN"
expect_in "and the Linux side's" "at $THERE" "$MTUWARN"
# It names the interface an operator can type at this VTY, not the TAP's own
# VPP name. The first version walked every interface with an MTU and reported
# `tap4096`, which is in no configuration anybody writes.
expect_in "and names the interface, not the TAP" \
  "interface host-veth-vpp" "$MTUWARN"

# The negative half. Put them back the same and it must go quiet, or the
# warning is unconditional and says nothing.
vppctl set interface mtu "$THERE" host-veth-vpp
AGREE=$($HX -c "configure" -c "ip route 10.78.0.0/16 blackhole" \
        -c "commit check" 2>&1)
expect_not_in "and it is silent when the two agree" \
  "carries OSPF and its MTU is" "$AGREE"
vppctl lcp lcp-sync on

verdict
