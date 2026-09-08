#!/bin/sh
# Plano de controle completo: BGP, OSPF e IS-IS sobre o TAP do linux_cp,
# with all three learned routes reaching the VPP FIB.
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
# one prefix per protocol, so there is no doubt about which brought which
for d in bgp:203.0.113.1/24 ospf:198.18.0.1/24 isis:198.19.0.1/24; do
  n=${d%%:*}; a=${d##*:}
  ip netns exec peer ip link add dum-$n type dummy
  ip netns exec peer ip addr add $a dev dum-$n
  ip netns exec peer ip link set dum-$n up
done

vpp -c /src/lab/startup-lcp.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -25 /tmp/vpp.log; exit 1; }

vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
vppctl lcp create host-veth-vpp host-if vppwan0

# The TAP inherits MTU 9000 while the wire is at 1500. OSPF compares MTU in DD
# packets, so the adjacency stalls in Exchange without saying why. MTU has to
# be explicit, never inherited.
vppctl set interface mtu 1500 host-veth-vpp

# IS-IS does not run over IP: it is OSI NLPID 0x83 straight over the link.
# Without this the frame reaches VPP, lands in osi-input as "unknown osi
# protocol" and is dropped - BGP and OSPF come up fine and only IS-IS is mute.
vppctl lcp osi-proto enable 0x83

vppctl set interface ip address host-veth-vpp 10.0.0.1/24

# ---------------- HexaRouting ----------------
mkdir -p /var/run/frr /etc/frr
cat > /etc/frr/zebra.conf <<'EOF'
hostname hexarouting
EOF
cat > /etc/frr/bgpd.conf <<'EOF'
hostname hexarouting
router bgp 65000
 bgp router-id 10.0.0.1
 no bgp ebgp-requires-policy
 neighbor 10.0.0.2 remote-as 65001
 address-family ipv4 unicast
  neighbor 10.0.0.2 activate
 exit-address-family
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
for d in zebra bgpd ospfd isisd; do
  /usr/lib/frr/$d -d -A 127.0.0.1 -f /etc/frr/$d.conf
done
# mgmtd and staticd: on FRR 10 a static route is configured through mgmtd and
# served by staticd, and without them vtysh answers "mgmtd is not running" to
# every "ip route" - which is how a static route can be typed, accepted and
# never exist. frrinit.sh starts both on a real box; a lab that starts daemons
# by hand has to know that.
/usr/lib/frr/mgmtd -d -A 127.0.0.1 2>/dev/null
/usr/lib/frr/staticd -d -A 127.0.0.1 2>/dev/null
sleep 1

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
 address-family ipv4 unicast
  network 203.0.113.0/24
  neighbor 10.0.0.1 activate
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
printf 'BGP   : '; vtysh -c "show bgp ipv4 unicast summary" 2>/dev/null | grep -E "^10\.0\.0\.2" | awk '{print "peer "$1" AS "$3" up "$9" prefixos "$10}'
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
. /src/lab/verdict.sh
for pfx in 203.0.113.0/24 198.18.0.0/24 198.19.0.0/24; do
  expect "$pfx reached the VPP FIB" sh -c "vppctl show ip fib $pfx | grep -q '^$pfx'"
done
expect "IS-IS adjacency is up" sh -c "vtysh -c 'show isis neighbor' 2>/dev/null | grep -qi up"

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

HX="python3 /src/src/hexash/hexash.py"
$HX -c "configure" -c "interface host-veth-vpp" -c "mtu 1400" -c "exit" \
    -c "commit" > /tmp/mtu.log 2>&1 || true
tail -3 /tmp/mtu.log | sed 's/^/  commit: /'
expect "the dataplane interface took the committed MTU"   sh -c "vppctl show interface host-veth-vpp | grep -q 1400"
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
  sh -c "vppctl show interface host-veth-vpp | grep -q 1450"
expect "with lcp-sync off, the TAP took it too - so hexash moved it, not VPP" \
  sh -c "ip link show vppwan0 | grep -q 'mtu 1450'"
vppctl lcp lcp-sync on
expect "the MTU is in the running configuration"   sh -c "$HX -c 'show running-config' | grep -q 'mtu 1450'"

# Put it back, or the OSPF assertions above would be checking a link this test
# just broke.
$HX -c "configure" -c "interface host-veth-vpp" -c "mtu 1500" -c "exit" \
    -c "commit" >/dev/null 2>&1 || true

# --- the forwarding table, and telling it apart from FRR's (#95) ---
#
# Everything above asserts on "vppctl show ip fib" directly, because that is
# the table that forwards. This section asserts that the VTY says the same
# thing - and that when the two tables disagree, it says WHICH.
echo
echo "=========== show route ==========="
SHOW_ROUTE=$($HX -c "show route" 2>&1)
printf '%s\n' "$SHOW_ROUTE" | head -12 | sed 's/^/  /'

echo
echo "=========== a static route through the VTY ==========="
# 10.0.0.2 is the peer, on the connected /24, so this one is legitimate.
$HX -c "configure" -c "ip route 192.168.77.0/24 10.0.0.2" -c "commit" \
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
expect "and all three of them" \
  sh -c "printf '%s' \"$SHOW_ROUTE\" | grep -qc '198.1[89]'"
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

verdict
