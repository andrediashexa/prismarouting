#!/bin/sh
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# OSPF and IS-IS configured through the VTY, asserted on the tables that forward.
#
# Both IGPs are configured with `configure` ... `commit` at this box's own VTY,
# and every assertion below reads the VPP FIB, an adjacency state, or the punt
# the dataplane is actually doing - never the text of a configuration file.
#
#   [netns peer] OSPF / IS-IS                  [default] HexaRouting
#     dum-ospf  198.18.0.0/24    -- OSPF -->   FRR -> kernel -> linux_nl
#     dum-isis  198.19.0.0/24    -- ISIS -->                 -> FIB do VPP
#               2001:db8:19::/64 -- ISIS -->
#     veth-out 10.0.0.2 ------(veth)------ veth-vpp -> VPP -> vppwan0 10.0.0.1
#              2001:db8:0::2                                          2001:db8:0::1
#
# THREE things this file does NOT do, and each one is the point of an assertion:
#
#  * `/etc/frr/ospfd.conf` and `/etc/frr/isisd.conf` are emptied to a `hostname`
#    line, the way `bgpd.conf` was for #142. A prefix in the FIB is then
#    evidence that the VTY configured a working protocol instance, not that FRR
#    read a file this lab wrote.
#  * `vppctl lcp osi-proto enable 0x83` is NOT run. The product enables the OSI
#    punt when the configuration it is applying contains an IS-IS instance
#    (#168); a lab that enables it by hand makes that assertion unfailable in
#    the one dimension that matters.
#  * `vppctl set interface mtu 1500` is NOT run at setup. The TAP comes up at
#    9000 while the wire is 1500, which is the inherited-MTU stall documented in
#    docs/routing.md - reproduced here on purpose, measured, and then fixed
#    through the VTY.
#
# Split out of lab/routing.sh, which is the suite's floor (#167, #177): these
# tests need the peer namespace and the TAP, and none of the third autonomous
# system, the second BGP session or the policy machinery that lab/bgp-policy.sh
# builds. No BGP session is configured here for the same reason - bgpd is
# started, with no peer, only so that ONE assertion about a BGP refusal is not
# muddied by a second error about a daemon that is not running.
set -u
LAB_T0=$(date +%s)
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
ip netns exec peer ip addr add 2001:db8:0::2/64 dev veth-out
# One prefix per protocol, so there is no doubt about which brought which, and
# an IPv6 prefix behind the IS-IS one so `show ip6 fib` has something to hold.
ip netns exec peer ip link add dum-ospf type dummy
ip netns exec peer ip addr add 198.18.0.1/24 dev dum-ospf
ip netns exec peer ip link set dum-ospf up
ip netns exec peer ip link add dum-isis type dummy
ip netns exec peer ip addr add 198.19.0.1/24 dev dum-isis
ip netns exec peer ip addr add 2001:db8:19::1/64 dev dum-isis
ip netns exec peer ip link set dum-isis up

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -25 /tmp/vpp.log; exit 1; }

vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
vppctl lcp create host-veth-vpp host-if vppwan0

# NO `set interface mtu` here. See the header: the mismatch is the subject of
# the first section, not an accident.
#
# NO `lcp osi-proto enable 0x83` here either. See the header.

vppctl set interface ip address host-veth-vpp 10.0.0.1/24
# IPv6 on the link, so IS-IS's IPv6 topology has a next hop to resolve and VPP
# has a link-local of its own to answer neighbour solicitations from.
vppctl set interface ip address host-veth-vpp 2001:db8:0::1/64

# ---------------- HexaRouting ----------------
mkdir -p /var/run/frr /etc/frr
cat > /etc/frr/zebra.conf <<'EOF'
hostname hexarouting
EOF
# Deliberately EMPTY of every routing protocol. OSPF and IS-IS are configured
# through the VTY further down, so the prefixes that arrive are evidence that
# the VTY configured working protocol instances - not that a file written by
# this lab did (#142, #177).
cat > /etc/frr/ospfd.conf <<'EOF'
hostname hexarouting
EOF
cat > /etc/frr/isisd.conf <<'EOF'
hostname hexarouting
EOF
cat > /etc/frr/bgpd.conf <<'EOF'
hostname hexarouting
EOF
chown -R frr:frr /var/run/frr /etc/frr
# These are the daemons THIS LAB arranges, not what the product ships: an
# installed box inherits Debian's /etc/frr/daemons, where bgpd, ospfd and isisd
# are all "no" (#99). Starting them by hand here is what let that go unnoticed,
# so it is said out loud rather than left as a list somebody skims.
#
# ospf6d is deliberately NOT started, and is not an oversight: the installer
# does not enable it either, while the VTY offers `router ospf6`. That gap is
# asserted on further down.
for d in zebra ospfd isisd bgpd; do
  /usr/lib/frr/$d -d -A 127.0.0.1 -f /etc/frr/$d.conf
done
# mgmtd and staticd: on FRR 10 they serve the statements that have no `router`
# line, and without them vtysh answers "mgmtd is not running" to every one of
# them. frrinit.sh starts both on a real box; a lab that starts daemons by hand
# has to know that.
/usr/lib/frr/mgmtd -d -A 127.0.0.1 2>/dev/null
/usr/lib/frr/staticd -d -A 127.0.0.1 2>/dev/null
sleep 1

# ---------------- upstream ----------------
mkdir -p /var/run/frr/peer /etc/frr/peer
cat > /etc/frr/peer/zebra.conf <<'EOF'
hostname upstream
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
 ipv6 router isis HEXA
exit
interface dum-isis
 ip router isis HEXA
 ipv6 router isis HEXA
exit
router isis HEXA
 net 49.0001.0000.0000.0002.00
 is-type level-2-only
EOF
chown -R frr:frr /var/run/frr/peer /etc/frr/peer
for d in zebra ospfd isisd; do
  ip netns exec peer /usr/lib/frr/$d -d -N peer -A 127.0.0.1 -f /etc/frr/peer/$d.conf
done

. /src/lab/verdict.sh
# The BPA plugin, installed where vat2 looks for it on a real box. Nothing below
# sends a bpa_ message, but hexash asks vat2 which messages are registered
# before it touches the dataplane and calls it unusable when bpa_pool_add_del is
# not among them - so without this every commit here fails for a reason that has
# nothing to do with routing.
use_binary_api
HX="python3 /src/src/hexash/hexash.py"

# --- what the assertions read -------------------------------------------------
#
# The STATE COLUMN in each case, never "the word appears somewhere in the
# output". The peer's IS-IS hostname is literally "upstream", so a case-
# insensitive grep for "up" matches the System Id and passes with the adjacency
# in Init or Down - which is every state except the one being asserted.
ospf_state() {
  vtysh -c 'show ip ospf neighbor' 2>/dev/null | tr -d '\r' \
    | awk '$1 == "10.0.0.2" {print $3}'
}
ospf_full() { case "$(ospf_state)" in Full*) return 0 ;; *) return 1 ;; esac; }
isis_state() {
  vtysh -c 'show isis neighbor' 2>/dev/null | tr -d '\r' \
    | awk '$2 == "vppwan0" {print $4}'
}
isis_up() { case "$(isis_state)" in Up) return 0 ;; *) return 1 ;; esac; }

in_fib()  { vppctl show ip fib "$1" 2>/dev/null | grep -q "^$1"; }
in_fib6() { vppctl show ip6 fib "$1" 2>/dev/null | grep -q "^$1"; }

# The OSI protocols linux_cp is punting, asked of the same API the product uses
# to set them, because `show lcp` does not report them at all. VPP answers with
# a HEX STRING - "0x83" for one protocol, bare "0x" for none - not a list of
# numbers (this is why an earlier version of the assertion in lab/routing.sh
# counted "131" and found nothing on a box that was punting).
osi_count() { vat2 lcp_osi_proto_get '{}' 2>/dev/null | grep -c '"0x83"' || true; }

# MTU as a VALUE, from the column that holds it - not `grep -q 1500`, which any
# counter with that number in it satisfies.
mtu_of()  { vppctl show interface host-veth-vpp | awk 'NR==2 {split($4,a,"/"); print a[1]}'; }
tap_mtu() { ip link show vppwan0 | sed -n 's/.* mtu \([0-9]*\) .*/\1/p'; }

# Recorded before anything commits, so every assertion about the punt below is
# about a CHANGE and not about a state that might have been there all along.
OSI_BEFORE=$(osi_count)

echo "=========== the box before either IGP is configured ==========="
echo "  OSI protocols linux_cp is punting: $OSI_BEFORE"
echo "  MTU on the dataplane interface:    $(mtu_of)"
echo "  MTU on its TAP:                    $(tap_mtu)"
echo "  MTU on the peer's side of the wire: $(ip netns exec peer ip link show veth-out | sed -n 's/.* mtu \([0-9]*\) .*/\1/p')"
expect "nothing is punting IS-IS before anything is configured" \
  test "$OSI_BEFORE" -eq 0
expect_not_in "no router ospf in FRR before the VTY puts one there" \
  "router ospf" "$(vtysh -c 'show running-config' 2>/dev/null)"
expect_not_in "and no router isis" \
  "router isis" "$(vtysh -c 'show running-config' 2>/dev/null)"

# =============================================================================
# 1. OSPF through the VTY, and the inherited-MTU stall, measured
# =============================================================================
#
# docs/routing.md, Pitfall 1: the TAP comes up at 9000, the wire is 1500, OSPF
# compares MTU in Database Description packets and the adjacency stops in
# Exchange with no log line saying why. It is documented and, until now, never
# tested - so a change that made every adjacency stall would pass the suite.
#
# The stall is not staged with an artificial value: it is the state a linux_cp
# pair comes up in. The lab simply does not paper over it before looking.
echo
echo "=========== OSPF configured through the VTY ==========="
OSPFUP=$($HX -c "configure" -c "router ospf" \
         -c "ospf router-id 10.0.0.1" \
         -c "network 10.0.0.0/24 area 0" \
         -c "exit" -c "commit" 2>&1)
echo "$OSPFUP" | tail -3 | sed 's/^/  /'
expect_in "the VTY commits an OSPF instance" "commit complete" "$OSPFUP"
expect "and FRR is holding it, read back from the box" \
  sh -c "vtysh -c 'show running-config' | grep -q '^router ospf'"

# The neighbour has to APPEAR before "it did not reach Full" means anything. An
# adjacency that never started is also not Full, and asserting on that would be
# a test that passes when the punt path is broken, the peer is dead, or nobody
# configured anything.
n=0
until [ -n "$(ospf_state)" ] || [ "$n" -ge 60 ]; do sleep 1; n=$((n + 1)); done
echo "  the neighbour appeared after ${n}s, in state: $(ospf_state)"
expect "OSPF hellos cross the punt path and a neighbour appears" \
  sh -c "[ -n \"\$(vtysh -c 'show ip ospf neighbor' 2>/dev/null | tr -d '\r' | awk '\$1 == \"10.0.0.2\" {print \$3}')\" ]"

# The window. Held open for its full length on purpose - a negative cannot
# break out early - and calibrated below against the time the SAME adjacency
# takes to reach Full once the MTU is aligned, rather than against a wall-clock
# number that would mean something different on a loaded runner.
STALL_WINDOW=40
echo "  holding the mismatch open for ${STALL_WINDOW}s and watching the state"
STALL_SAW_FULL=0
n=0
while [ "$n" -lt "$STALL_WINDOW" ]; do
  ospf_full && STALL_SAW_FULL=1
  sleep 2; n=$((n + 2))
done
echo "  state after ${STALL_WINDOW}s with the MTUs apart: $(ospf_state)"
echo "  198.18.0.0/24 in the VPP FIB: $(vppctl show ip fib 198.18.0.0/24 2>/dev/null | head -1)"
expect "with the TAP at $(tap_mtu) and the wire at 1500 the adjacency never reached Full" \
  test "$STALL_SAW_FULL" -eq 0
expect "and the OSPF prefix is NOT in the VPP FIB" \
  sh -c "! vppctl show ip fib 198.18.0.0/24 | grep -q '^198.18.0.0/24'"

echo "=========== the MTU aligned, through the VTY ==========="
MTUFIX=$($HX -c "configure" -c "interface host-veth-vpp" -c "mtu 1500" \
         -c "exit" -c "commit" 2>&1)
echo "$MTUFIX" | tail -2 | sed 's/^/  commit: /'
expect_in "the MTU commits" "commit complete" "$MTUFIX"
expect "the dataplane interface took it" test "$(mtu_of)" = "1500"
expect "and so did its TAP, which is the half OSPF compares" \
  test "$(tap_mtu)" = "1500"

n=0
until ospf_full || [ "$n" -ge 90 ]; do sleep 1; n=$((n + 1)); done
T_FULL=$n
echo "  state after aligning the MTU: $(ospf_state)  (${T_FULL}s)"
expect "the adjacency reached Full once the MTUs matched (${T_FULL}s)" ospf_full
# The measurement that makes the negative half honest. If the box takes longer
# to converge than the window the stall was watched for, "it never reached Full"
# would be a statement about the window rather than about the MTU.
expect "the stall window (${STALL_WINDOW}s) outlasted convergence (${T_FULL}s)" \
  test "$STALL_WINDOW" -gt "$T_FULL"

# Full is not the route. After Full there is an LSA exchange, an install into
# the kernel and a linux_nl walk into the VPP FIB, and it is the FIB that
# forwards.
n=0
until in_fib 198.18.0.0/24 || [ "$n" -ge 90 ]; do sleep 1; n=$((n + 1)); done
echo "  --- 198.18.0.0/24 in the VPP FIB, raw ---"
vppctl show ip fib 198.18.0.0/24 | sed 's/^/    /'
expect "the OSPF prefix reached the VPP FIB (${n}s)" in_fib 198.18.0.0/24
# The punt is still off. OSPF is carried over IP and needs nothing from
# linux_cp's OSI handling, so this says the section above proved the MTU and
# not something else - and it is the precondition the IS-IS section below needs.
expect "and nothing has enabled the OSI punt yet" test "$(osi_count)" -eq 0

# =============================================================================
# 2. IS-IS: the punt the PRODUCT does, with the negative half
# =============================================================================
#
# There is no way to un-punt: VPP's linux_cp offers `lcp osi-proto enable` and
# no disable verb, and lcp_osi_proto_get is read-only. So the negative half is
# not "turn it off and watch it fail" - it is taken BEFORE the product has ever
# been asked to turn it on.
#
# The IS-IS instance is put on this box with vtysh DIRECTLY, which is exactly
# the shipped box of #168: an operator configures IS-IS, the commit succeeds,
# isisd comes up, the running configuration reads correctly, and no adjacency
# ever forms. Nothing here goes through the product, so nothing enables the
# punt.
#
# It is done AFTER the MTU is aligned, and that ordering is not cosmetic. FRR
# pads IS-IS hellos to the interface MTU, so with the TAP at 9000 and the wire
# at 1500 the hellos would be dropped by the veth and the adjacency would fail
# for a second reason - which would make this negative unfalsifiable in the one
# dimension it exists for.
echo
echo "=========== IS-IS with no punt: the box #168 shipped ==========="
vtysh -c "configure terminal" \
      -c "interface vppwan0" \
      -c "ip router isis HEXA" \
      -c "ipv6 router isis HEXA" \
      -c "exit" \
      -c "router isis HEXA" \
      -c "net 49.0001.0000.0000.0001.00" \
      -c "is-type level-2-only" > /tmp/isis-direct.log 2>&1
cat /tmp/isis-direct.log | sed 's/^/  vtysh: /'
ISIS_CFG_A=$(vtysh -c 'show running-config' 2>/dev/null \
             | grep -E "isis|^interface vppwan0" | sed 's/^ *//' | sort)
echo "  --- the IS-IS configuration now on the box ---"
printf '%s\n' "$ISIS_CFG_A" | sed 's/^/    /'
echo "  --- isisd's own view of the circuit ---"
vtysh -c "show isis interface vppwan0" 2>&1 | head -8 | sed 's/^/    /'
expect "isisd holds an IS-IS instance" \
  sh -c "vtysh -c 'show running-config' | grep -q '^router isis HEXA'"
expect "and it is on the interface the peer is behind" \
  sh -c "vtysh -c 'show isis interface vppwan0' | grep -q vppwan0"
expect "and the product has still not been asked to punt anything" \
  test "$(osi_count)" -eq 0

PUNT_WINDOW=40
echo "  holding it open for ${PUNT_WINDOW}s with no punt"
PUNT_SAW_UP=0
n=0
while [ "$n" -lt "$PUNT_WINDOW" ]; do
  isis_up && PUNT_SAW_UP=1
  sleep 2; n=$((n + 2))
done
echo "  --- show isis neighbor, with no punt ---"
vtysh -c "show isis neighbor" 2>&1 | sed 's/^/    /'
echo "  --- what the dataplane did with the frames ---"
vppctl show errors | grep -i osi | sed 's/^/    /'
expect "with no punt the IS-IS adjacency never came up" test "$PUNT_SAW_UP" -eq 0
expect "and the IS-IS prefix is NOT in the VPP FIB" \
  sh -c "! vppctl show ip fib 198.19.0.0/24 | grep -q '^198.19.0.0/24'"
expect "nor is its IPv6 prefix in the IPv6 FIB" \
  sh -c "! vppctl show ip6 fib 2001:db8:19::/64 | grep -q '^2001:db8:19::/64'"
# The decisive evidence, and the reason this is not merely "nothing happened":
# the frames ARRIVED at VPP and were dropped for want of a registered receiver.
# Without this the negative would also be satisfied by a peer that was never
# sending.
expect "the frames reached VPP and were dropped as an unknown OSI protocol" \
  sh -c "vppctl show errors | grep -qi 'unknown osi protocol'"

# Taken away again, so the commit below is a real change rather than a no-op
# the VTY would decline to apply. The configuration that goes back is compared
# against this one, further down, so "it was re-typed" cannot be the
# explanation for the adjacency coming up.
vtysh -c "configure terminal" \
      -c "interface vppwan0" \
      -c "no ip router isis HEXA" \
      -c "no ipv6 router isis HEXA" \
      -c "exit" \
      -c "no router isis HEXA" > /tmp/isis-undo.log 2>&1
expect_not_in "the instance is off the box again before the VTY puts it back" \
  "router isis HEXA" "$(vtysh -c 'show running-config' 2>/dev/null)"

echo
echo "=========== IS-IS configured through the VTY ==========="
ISIS_T0=$(date +%s)
ISISUP=$($HX -c "configure" \
         -c "interface vppwan0" \
         -c "ip router isis HEXA" \
         -c "ipv6 router isis HEXA" \
         -c "exit" \
         -c "router isis HEXA" \
         -c "net 49.0001.0000.0000.0001.00" \
         -c "is-type level-2-only" \
         -c "exit" -c "commit" 2>&1)
echo "$ISISUP" | tail -3 | sed 's/^/  /'
expect_in "the VTY commits an IS-IS instance" "commit complete" "$ISISUP"
echo "  OSI protocols linux_cp is punting, after the commit: $(osi_count)"
expect "the product enabled the OSI punt IS-IS needs" test "$(osi_count)" -ge 1
expect "and it was not already on before the commit" test "$OSI_BEFORE" -eq 0

ISIS_CFG_B=$(vtysh -c 'show running-config' 2>/dev/null \
             | grep -E "isis|^interface vppwan0" | sed 's/^ *//' | sort)
# The claim this makes falsifiable: FRR is holding the SAME configuration it
# held during the negative window, so the only thing that changed between an
# adjacency that never formed and one that does is the punt.
expect "FRR holds exactly the configuration it held with no punt" \
  test "$ISIS_CFG_A" = "$ISIS_CFG_B"

# Measured from BEFORE the commit, not from after it. The adjacency came up
# while the commit was still running, so a loop that starts afterwards reads
# zero seconds - and a calibration against zero calibrates nothing.
n=0
until isis_up || [ "$n" -ge 90 ]; do sleep 1; n=$((n + 1)); done
T_ISIS=$(( $(date +%s) - ISIS_T0 ))
echo "  --- show isis neighbor, with the punt on ---"
vtysh -c "show isis neighbor" 2>&1 | sed 's/^/    /'
expect "the IS-IS adjacency came up (${T_ISIS}s)" isis_up
expect "the punt window (${PUNT_WINDOW}s) outlasted the time it takes (${T_ISIS}s)" \
  test "$PUNT_WINDOW" -gt "$T_ISIS"

n=0
until in_fib 198.19.0.0/24 || [ "$n" -ge 90 ]; do sleep 1; n=$((n + 1)); done
echo "  --- 198.19.0.0/24 in the VPP FIB, raw ---"
vppctl show ip fib 198.19.0.0/24 | sed 's/^/    /'
expect "the IS-IS prefix reached the VPP FIB (${n}s)" in_fib 198.19.0.0/24

# --- the IPv6 half -----------------------------------------------------------
#
# Nothing in this suite had ever asserted that an IPv6 route reached the
# dataplane by ANY protocol (#177). It is carried here by IS-IS's IPv6 topology
# over the same adjacency, which costs no second daemon and no second
# convergence - and which exercises the punt for a second address family at the
# same time.
n=0
until in_fib6 2001:db8:19::/64 || [ "$n" -ge 90 ]; do sleep 1; n=$((n + 1)); done
echo "  --- what FRR installed in the kernel ---"
ip -6 route show | grep -E "2001:db8:19" | sed 's/^/    /'
echo "  --- 2001:db8:19::/64 in the VPP IPv6 FIB, raw ---"
vppctl show ip6 fib 2001:db8:19::/64 | sed 's/^/    /'
expect "an IPv6 prefix learned by IS-IS reached the VPP IPv6 FIB (${n}s)" \
  in_fib6 2001:db8:19::/64

# =============================================================================
# 3. Withdrawal: what leaves the FIB when the far end goes
# =============================================================================
#
# A prefix that arrives and never leaves is a black hole with a route to it.
# The peer's stub interfaces are dropped - not the link, so the adjacency
# survives and this is a withdrawal rather than a session teardown - and the
# VPP FIB has to lose all three prefixes.
echo
echo "=========== withdrawal ==========="
ip netns exec peer ip link set dum-ospf down
ip netns exec peer ip link set dum-isis down
n=0
until { ! in_fib 198.18.0.0/24 && ! in_fib 198.19.0.0/24 \
        && ! in_fib6 2001:db8:19::/64; } || [ "$n" -ge 90 ]; do
  sleep 1; n=$((n + 1))
done
echo "  after dropping the peer's stub interfaces (${n}s):"
printf '    198.18.0.0/24    '; vppctl show ip fib 198.18.0.0/24 | head -1
printf '    198.19.0.0/24    '; vppctl show ip fib 198.19.0.0/24 | head -1
printf '    2001:db8:19::/64 '; vppctl show ip6 fib 2001:db8:19::/64 | head -1
expect "the OSPF prefix left the VPP FIB"  sh -c "! vppctl show ip fib 198.18.0.0/24 | grep -q '^198.18.0.0/24'"
expect "the IS-IS prefix left the VPP FIB" sh -c "! vppctl show ip fib 198.19.0.0/24 | grep -q '^198.19.0.0/24'"
expect "and so did the IPv6 one"           sh -c "! vppctl show ip6 fib 2001:db8:19::/64 | grep -q '^2001:db8:19::/64'"

# The other half. Without it, "the prefixes left" is also what a lab that broke
# the box looks like - and a withdrawal test that cannot tell those apart is
# not testing withdrawal.
ip netns exec peer ip link set dum-ospf up
ip netns exec peer ip link set dum-isis up
# The IPv6 address has to be put back by hand. Linux FLUSHES a link's IPv6
# addresses when it goes down and does not restore them when it comes up (the
# IPv4 one survives), so without this the prefix cannot return and the
# assertion below would be failing for something that is not withdrawal.
ip netns exec peer ip addr add 2001:db8:19::1/64 dev dum-isis 2>/dev/null
n=0
until { in_fib 198.18.0.0/24 && in_fib 198.19.0.0/24 \
        && in_fib6 2001:db8:19::/64; } || [ "$n" -ge 90 ]; do
  sleep 1; n=$((n + 1))
done
echo "  after bringing them back up (${n}s):"
printf '    198.18.0.0/24    '; vppctl show ip fib 198.18.0.0/24 | sed -n 2p
printf '    198.19.0.0/24    '; vppctl show ip fib 198.19.0.0/24 | sed -n 2p
printf '    2001:db8:19::/64 '; vppctl show ip6 fib 2001:db8:19::/64 | sed -n 2p
expect "and all three came back when the peer brought them up (${n}s)" \
  sh -c "vppctl show ip fib 198.18.0.0/24 | grep -q '^198.18.0.0/24' \
         && vppctl show ip fib 198.19.0.0/24 | grep -q '^198.19.0.0/24' \
         && vppctl show ip6 fib 2001:db8:19::/64 | grep -q '^2001:db8:19::/64'"

# =============================================================================
# 4. What the VTY refuses, and what it must not
# =============================================================================
echo
echo "=========== OSPF NBMA is not BGP (regression) ==========="
# `neighbor` is not a BGP word. OSPF uses it for NBMA and point-to-multipoint,
# and the BGP structural check used to scan every routing line for it - so
# `neighbor 10.0.0.2 poll-interval 60` under `router ospf` was refused with a
# sentence about `remote-as`, on a box that need not run BGP at all. A refusal
# that is wrong is worse than a check that is missing, and it is invisible
# until an operator with an NBMA segment cannot commit.
#
# The value is 90 and not 60 on purpose. Measured on FRR 10.7.1:
# OSPF_POLL_INTERVAL_DEFAULT is 60, and ospfd writes the interval back into the
# running configuration only when it differs from the default - so with 60 the
# statement commits, works, and reads back as a bare `neighbor 10.0.0.2`. The
# read-back assertion below would then fail for something that is FRR's config
# writer and not this product. 90 is the same statement with evidence attached.
NBMA=$($HX -c "configure" -c "router ospf" \
       -c "neighbor 10.0.0.2 poll-interval 90" \
       -c "exit" -c "commit" 2>&1)
echo "$NBMA" | tail -3 | sed 's/^/  /'
expect_in "an OSPF NBMA neighbour commits" "commit complete" "$NBMA"
expect_not_in "and is not refused with a BGP error" "remote-as" "$NBMA"
echo "  --- the router ospf stanza FRR is holding ---"
vtysh -c 'show running-config' 2>/dev/null \
  | awk '/^router ospf/ {inside=1} inside && /^[^ ]/ && !/^router ospf/ {inside=0} inside' \
  | sed 's/^/    /' 
# It has to have LANDED, not merely been accepted. A check that stops refusing
# by dropping the statement would pass the two assertions above.
expect "and FRR is holding it, read back from the box" \
  sh -c "vtysh -c 'show running-config' | grep -q 'neighbor 10.0.0.2 poll-interval 90'"

# The other direction. The BGP check must still catch the shape it exists for,
# or the regression above would also be satisfied by deleting the check.
NOAS=$($HX -c "configure" -c "router bgp 65000" \
       -c "neighbor 10.0.0.9 description sem-as" \
       -c "exit" -c "commit" 2>&1)
echo "$NOAS" | tail -3 | sed 's/^/  /'
expect_in "a BGP neighbour with no remote-as is still refused" "no 'remote-as'" "$NOAS"
expect_in "and the refusal names the neighbour" "10.0.0.9" "$NOAS"
expect_not_in "and nothing was applied" "commit complete" "$NOAS"

echo
echo "=========== a protocol whose daemon is not running ==========="
# The VTY offers `router ospf6`; the installer enables bgpd, ospfd and isisd and
# NOT ospf6d (appliance/install.sh), and the boot self-test checks the same
# three. So an operator can configure OSPFv3 on a shipped box and it reaches
# nothing. The box has to say so at commit rather than report success.
OSPF6=$($HX -c "configure" -c "router ospf6" -c "exit" -c "commit" 2>&1)
echo "$OSPF6" | tail -3 | sed 's/^/  /'
expect_in "router ospf6 is refused while ospf6d is not running" \
  "ospf6d is not running" "$OSPF6"
expect_in "and the refusal says where to enable it" "/etc/frr/daemons" "$OSPF6"
expect_not_in "and nothing was applied" "commit complete" "$OSPF6"
# The half that keeps the check from being "refuse everything": the two
# protocols whose daemons ARE running committed above, and the adjacencies they
# built are in the FIB.
expect "while ospfd and isisd, which are running, committed and forward" \
  sh -c "vppctl show ip fib 198.18.0.0/24 | grep -q '^198.18.0.0/24' \
         && vppctl show ip fib 198.19.0.0/24 | grep -q '^198.19.0.0/24'"

echo
echo "=========== adjacencias ==========="
printf 'OSPF  : '; vtysh -c "show ip ospf neighbor" 2>/dev/null | grep -E "^10\.0\.0\.2" | awk '{print "neighbour "$1" state "$3}'
printf 'IS-IS : '; vtysh -c "show isis neighbor" 2>/dev/null | awk '$2 == "vppwan0" {print "neighbour "$1" state "$4" interface "$2}'

echo
echo "  wall clock: $(( $(date +%s) - LAB_T0 ))s"
verdict
