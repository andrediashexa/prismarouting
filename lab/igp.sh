#!/bin/sh
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# OSPF, OSPFv3 and IS-IS configured through the VTY, asserted on the tables that forward.
#
# All three IGPs are configured with `configure` ... `commit` at this box's own VTY,
# and every assertion below reads the VPP FIB, an adjacency state, or the punt
# the dataplane is actually doing - never the text of a configuration file.
#
#   [netns peer] OSPF / OSPFv3 / IS-IS         [default] Prisma Routing
#     dum-ospf  198.18.0.0/24    -- OSPF -->   FRR -> kernel -> linux_nl
#     dum-isis  198.19.0.0/24    -- ISIS -->                 -> FIB do VPP
#               2001:db8:19::/64 -- ISIS -->
#     dum-ospf6 2001:db8:66::/64 -- OSPFv3 ->
#     veth-out 10.0.0.2 ------(veth)------ veth-vpp -> VPP -> vppwan0 10.0.0.1
#              2001:db8:0::2                                          2001:db8:0::1
#
# FOUR things this file does NOT do, and each one is the point of an assertion:
#
#  * it does NOT decide which routing daemons this box runs. It runs the
#    installer into a fakeroot and starts what the installer enabled, so an
#    IGP assertion below cannot pass on a box the product would not produce.
#    That is #187: ospf6d was enabled in no file the product shipped while the
#    VTY accepted `router ospf6`, and this lab could not see it because this
#    lab held a daemon list of its own.
#
#  * `/etc/frr/ospfd.conf`, `/etc/frr/ospf6d.conf` and `/etc/frr/isisd.conf` are
#    emptied to a `hostname` line, the way `bgpd.conf` was for #142. A prefix
#    in the FIB is then evidence that the VTY configured a working protocol
#    instance, not that FRR read a file this lab wrote.
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
# The third one, for OSPFv3 (#187). IPv6 only, because OSPFv3 carries nothing
# else, and a prefix of its own so that "which protocol brought this" is never
# a question the FIB cannot answer.
ip netns exec peer ip link add dum-ospf6 type dummy
ip netns exec peer ip addr add 2001:db8:66::1/64 dev dum-ospf6
ip netns exec peer ip link set dum-ospf6 up

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -25 /tmp/vpp.log; exit 1; }

# The BPA plugin, installed where vat2 looks for it on a real box. Nothing below
# sends a bpa_ message, but prismash asks vat2 which messages are registered
# before it touches the dataplane and calls it unusable when bpa_pool_add_del is
# not among them - so without this every commit here fails for a reason that has
# nothing to do with routing.
use_binary_api
HX="python3 /src/src/prismash/prismash.py"

vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
# The pair is the PRODUCT's, not this lab's (#182). `vppctl lcp create` used to
# be here, and while it was, this lab proved an IGP on a box nobody receives:
# on an installed appliance nothing created a pair, so FRR had no interface and
# OSPF and IS-IS were configurable and inert. Moved up from further down with
# use_binary_api, which has to come first for any commit at all.
$HX -c "configure" -c "interface host-veth-vpp" -c "linux-name vppwan0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'

# NO `set interface mtu` here. See the header: the mismatch is the subject of
# the first section, not an accident.
#
# NO `lcp osi-proto enable 0x83` here either. See the header.

vppctl set interface ip address host-veth-vpp 10.0.0.1/24
# IPv6 on the link, so IS-IS's IPv6 topology has a next hop to resolve and VPP
# has a link-local of its own to answer neighbour solicitations from.
vppctl set interface ip address host-veth-vpp 2001:db8:0::1/64

# ---------------- Prisma Routing ----------------
mkdir -p /var/run/frr /etc/frr
cat > /etc/frr/zebra.conf <<'EOF'
hostname prisma-routing
EOF
# Deliberately EMPTY of every routing protocol. OSPF and IS-IS are configured
# through the VTY further down, so the prefixes that arrive are evidence that
# the VTY configured working protocol instances - not that a file written by
# this lab did (#142, #177).
cat > /etc/frr/ospfd.conf <<'EOF'
hostname prisma-routing
EOF
cat > /etc/frr/isisd.conf <<'EOF'
hostname prisma-routing
EOF
cat > /etc/frr/bgpd.conf <<'EOF'
hostname prisma-routing
EOF
cat > /etc/frr/ospf6d.conf <<'EOF'
hostname prisma-routing
EOF
chown -R frr:frr /var/run/frr /etc/frr

. /src/lab/verdict.sh

# WHICH daemons run on this box is the PRODUCT's answer, and this lab now asks
# it instead of holding a list of its own.
#
# The list used to be written out here - `for d in zebra ospfd isisd bgpd` -
# with a comment explaining that an installed box would not have them. That is
# the #99 shape one layer up: the lab could not fail when the installer's list
# was wrong, because the lab's list was a copy that somebody would have to
# remember to keep in step. Nobody did, and ospf6d was missing from the
# installer for six releases while `router ospf6` committed (#187).
#
# So: seed a fakeroot with the distribution's own /etc/frr/daemons, run the
# installer into it, and start exactly what the installer turned on. Every
# protocol assertion below now rests on appliance/install.sh, and taking a
# daemon back out of it fails this lab rather than passing it.
install -D -m 0644 /etc/frr/daemons /tmp/fakeroot/etc/frr/daemons
# The negative half, recorded BEFORE the installer runs. Without it "ospf6d is
# enabled" is equally consistent with Debian having enabled it, and the
# assertion would be about the distribution rather than about this product.
expect "the distribution ships ospf6d disabled, as it does every daemon" \
  sh -c "grep -q '^ospf6d=no$' /tmp/fakeroot/etc/frr/daemons"
DESTDIR=/tmp/fakeroot sh /src/appliance/install.sh > /tmp/install-sh.log 2>&1 \
  || { echo "  install.sh stopped early:"; tail -5 /tmp/install-sh.log | sed 's/^/    /'; }
# Only the daemons. `vtysh_enable=yes` lives in the same file and is not a
# process, and starting it would be a lab failing for a reason of its own
# making.
ENABLED=$(awk -F= '/^[a-z0-9]+d=yes$/ {print $1}' /tmp/fakeroot/etc/frr/daemons | tr '\n' ' ')
echo "  the installer enabled: $ENABLED"
expect "the installer enables the daemon that serves 'router ospf6'" \
  sh -c "grep -q '^ospf6d=yes$' /tmp/fakeroot/etc/frr/daemons"
# start_frr adds zebra, mgmtd and staticd, which frrinit.sh starts
# unconditionally on a real box and which are not lines in this file at all.
# shellcheck disable=SC2086
start_frr $ENABLED

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
cat > /etc/frr/peer/ospf6d.conf <<'EOF'
hostname upstream
interface veth-out
 ipv6 ospf6 area 0
exit
interface dum-ospf6
 ipv6 ospf6 area 0
exit
router ospf6
 ospf6 router-id 10.0.0.2
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
for d in zebra ospfd ospf6d isisd; do
  ip netns exec peer /usr/lib/frr/$d -d -N peer -A 127.0.0.1 -f /etc/frr/peer/$d.conf
done

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
# OSPFv3's state column. FRR prints it as "State/IfState" - `Full/DR`,
# `ExStart/BDR` - so the adjacency's own half is what is read, and it is read
# from the row whose interface is vppwan0 rather than from anywhere the word
# "Full" might also appear.
ospf6_state() {
  vtysh -c 'show ipv6 ospf6 neighbor' 2>/dev/null | tr -d '\r' \
    | awk '$NF ~ /^vppwan0\[/ {split($4, a, "/"); print a[1]}'
}
ospf6_full() { case "$(ospf6_state)" in Full) return 0 ;; *) return 1 ;; esac; }

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

# --- what the BOX says about the stall it is sitting in -----------------------
#
# FRR is not wrong above: the adjacency really is in Exchange. What FRR does
# not say - anywhere, in any command - is why, and the reason is the shape of
# this box. ospfd sees the TAP and puts the TAP's MTU in its Database
# Description packets while the wire runs at 1500, the far end drops them, and
# neither side logs a word. #176 is that screen.
echo "  --- show ospf neighbor, with the MTUs apart ---"
OSPFSTALL=$($HX -c "show ospf neighbor" 2>&1)
printf '%s\n' "$OSPFSTALL" | sed 's/^/    /'
expect_in "the VTY reports the state FRR reports" "Exchange" "$OSPFSTALL"
expect_in "and names what this end is advertising, which FRR never mentions" \
  "which is where the two ends compare MTU; this end advertises $(tap_mtu)" \
  "$OSPFSTALL"
expect_in "and that neither side will log it" \
  "neither side logs a word about it" "$OSPFSTALL"
# The contrast that makes the point, asserted rather than asserted about: FRR's
# own answer to the same question contains no mention of MTU at all.
expect_not_in "while FRR's own answer says nothing about MTU" \
  "MTU" "$(vtysh -c 'show ip ospf neighbor' 2>&1)"

echo "  --- show ospf interface ---"
OSPFIF=$($HX -c "show ospf interface" 2>&1)
printf '%s\n' "$OSPFIF" | sed 's/^/    /'
# Read as FIELDS of the vppwan0 row. "9000 appears in the output" is satisfied
# by the header, by a counter, and by the other MTU on the same line.
OSPFIF_ROW=$(printf '%s\n' "$OSPFIF" | awk '$1 == "vppwan0" {print $(NF-2), $(NF-1), $NF}')
echo "    vppwan0 row, last three fields: $OSPFIF_ROW"
expect "the two MTUs are on one screen with the dataplane's own name" \
  test "$OSPFIF_ROW" = "$(tap_mtu) $(mtu_of) (host-veth-vpp)"

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

# The same screen on a box where nothing is wrong. Without this half, every
# assertion above is also satisfied by a command that prints the stall
# unconditionally.
OSPFOK=$($HX -c "show ospf neighbor" 2>&1)
printf '%s\n' "$OSPFOK" | sed 's/^/    /'
expect_in "the adjacency is reported Full" "Full" "$OSPFOK"
expect_not_in "and nothing is said about an MTU stall that is not happening" \
  "compare MTU" "$OSPFOK"
# The number #148 put on `show bgp summary`, for an IGP: FRR holding the route
# and the box forwarding on it are two claims, and this is the second one.
expect_in "and it says the OSPF prefix reached the forwarding table" \
  "OSPF prefix(es) are in the forwarding table" "$OSPFOK"
expect_not_in "with nothing missing from it" "NOT in the forwarding table" \
  "$OSPFOK"
# The punt is still off. OSPF is carried over IP and needs nothing from
# linux_cp's OSI handling, so this says the section above proved the MTU and
# not something else - and it is the precondition the IS-IS section below needs.
expect "and nothing has enabled the OSI punt yet" test "$(osi_count)" -eq 0

# =============================================================================
# 1b. OSPFv3, which this box offered and could not run (#187)
# =============================================================================
#
# The VTY accepted `router ospf6`, the commit reported success, and ospf6d was
# enabled in no file the product shipped - so an operator could configure
# OSPFv3 on a shipped box and nothing ran. The decision #187 asked for was
# whether to take the statement out of the command tree or make it work, and
# it was made by measuring rather than by arguing:
#
#   OSPFv3 hellos (IPv6 protocol 89 to ff02::5) reach the TAP over a linux_cp
#   pair, captured with tcpdump on vppwan0 in both directions. VPP has no
#   local handler for protocol 89, so the packet falls through ip6-local to
#   ip6-punt and linux_cp's per-pair redirect DVRs it out of the TAP, ethernet
#   header and all - which is the same reason OSPFv2 has always worked. The
#   OSI punt IS-IS needs is not involved and stays off, asserted below.
#
# So the transport was never the problem, the daemon list was. It is asserted
# here the way every other protocol in this file is: configured at this box's
# own VTY, and read out of the table that forwards.
#
# AFTER the MTU is aligned, and that ordering is not cosmetic. OSPFv3 keeps
# the Interface MTU field in its database description packets (RFC 5340), so
# with the TAP at 9000 against a 1500 wire the adjacency stops short of Full
# exactly as OSPFv2 did above - measured, and then measured again reaching
# Full within seconds of the two being made to agree.
echo
echo "=========== OSPFv3 configured through the VTY ==========="
OSPF6_T0=$(date +%s)
OSPF6=$($HX -c "configure" -c "router ospf6" \
        -c "ospf6 router-id 10.0.0.1" -c "exit" \
        -c "interface vppwan0" -c "ipv6 ospf6 area 0" \
        -c "exit" -c "commit" 2>&1)
echo "$OSPF6" | tail -3 | sed 's/^/  /'
expect_in "the VTY commits an OSPFv3 instance" "commit complete" "$OSPF6"
# It must not be refused for a daemon that is not running. That refusal is the
# box being right about a box that is wrong, and on an installed appliance
# there is no longer such a box - so seeing it here would mean the installer
# stopped enabling ospf6d.
expect_not_in "and is not refused for a daemon nobody enabled" \
  "ospf6d is not running" "$OSPF6"
expect "and FRR is holding it, read back from the box" \
  sh -c "vtysh -c 'show running-config' | grep -q '^router ospf6'"

n=0
until [ -n "$(ospf6_state)" ] || [ "$n" -ge 60 ]; do sleep 1; n=$((n + 1)); done
echo "  the neighbour appeared after ${n}s, in state: $(ospf6_state)"
expect "OSPFv3 hellos cross the punt path and a neighbour appears" \
  sh -c "[ -n \"\$(vtysh -c 'show ipv6 ospf6 neighbor' 2>/dev/null | tr -d '\r' | awk '\$NF ~ /^vppwan0\[/ {split(\$4, a, \"/\"); print a[1]}')\" ]"

n=0
until ospf6_full || [ "$n" -ge 120 ]; do sleep 2; n=$((n + 2)); done
T_OSPF6=$(( $(date +%s) - OSPF6_T0 ))
echo "  --- show ipv6 ospf6 neighbor ---"
vtysh -c "show ipv6 ospf6 neighbor" 2>&1 | sed 's/^/    /'
expect "the OSPFv3 adjacency reached Full (${T_OSPF6}s)" ospf6_full

# --- OSPFv3 and the size of the FIB, over SNMP (#503) ---
# The adjacency just asserted Full with vtysh, read through the box's own
# snmpd: ospf6d registers OSPFV3-MIB with it over AgentX. And the FIB's two
# counts, which are in the view where the tables are not - a full table is
# about a million routes (docs/snmp-plan.md).
echo "=========== OSPFv3 and the FIB's size over SNMP (#503) ==========="
install -d -m 0750 /var/agentx; chgrp frr /var/agentx
$HX -c "configure" -c "snmp enabled yes" -c "snmp listen-address 127.0.0.1" \
    -c "snmp community hexro" -c "commit" 2>&1 | sed 's/^/  /'
snmpd -Lo -f -C -u Debian-snmp -g Debian-snmp -c /etc/snmp/snmpd.conf \
  > /tmp/snmpd.log 2>&1 &
# A subagent that lost its master reconnects at its next ping, 15 s apart.
_n=0; NBR6=""
until printf '%s\n' "$NBR6" | grep -qx 8 || [ "$_n" -ge 40 ]; do
  sleep 1; _n=$((_n + 1))
  NBR6=$(snmpwalk -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.191.1.9.1.8 2>&1)
done
ROUTES=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.4.24.6.0 2>&1)
TABLE=$(snmpgetnext -v2c -c hexro -On 127.0.0.1 1.3.6.1.2.1.4.24.7 2>&1)
echo "  ospfv3NbrState: $(printf '%s' "$NBR6" | tr '\n' ' ')"
echo "  inetCidrRouteNumber: $ROUTES"
echo "  the next object after inetCidrRouteTable: $TABLE"
expect "OSPFV3-MIB reads the neighbour full (8), as vtysh does" \
  sh -c "printf '%s\\n' \"$NBR6\" | grep -qx 8"
expect "the FIB's size answers ($ROUTES)" \
  sh -c "case \"$ROUTES\" in ''|*[!0-9]*|0) exit 1;; *) exit 0;; esac"
expect_not_in "and its table does not: a walk of the FIB is not in the view" \
  ".1.3.6.1.2.1.4.24.7." "$TABLE"
pkill -x snmpd 2>/dev/null
$HX -c "configure" -c "snmp enabled no" -c "commit" >/dev/null 2>&1

# Full is not the route, for OSPFv3 exactly as for OSPFv2: after Full there is
# an LSA exchange, an install into the kernel and a linux_nl walk into the VPP
# FIB, and it is the FIB that forwards. Nothing in this suite had ever put an
# IPv6 prefix in the VPP FIB by OSPFv3 - #187 says so in as many words.
n=0
until in_fib6 2001:db8:66::/64 || [ "$n" -ge 120 ]; do sleep 2; n=$((n + 2)); done
echo "  --- what FRR installed in the kernel ---"
ip -6 route show | grep -E "2001:db8:66" | sed 's/^/    /'
echo "  --- 2001:db8:66::/64 in the VPP IPv6 FIB, raw ---"
vppctl show ip6 fib 2001:db8:66::/64 | head -4 | sed 's/^/    /'
expect "a prefix learned over OSPFv3 reached the VPP IPv6 FIB (${n}s)" \
  in_fib6 2001:db8:66::/64
# The negative that keeps the positive honest: the prefix came from OSPFv3 and
# not from the IS-IS instance this file also builds, which is not configured
# yet and whose own IPv6 prefix is a different one.
expect "and the IS-IS prefix is not there yet, so it was OSPFv3 that brought it" \
  sh -c "! vppctl show ip6 fib 2001:db8:19::/64 | grep -q '^2001:db8:19::/64'"
# OSPFv3 is carried over IPv6 and needs nothing from linux_cp's OSI handling.
# Without this line the section above would be equally consistent with OSPFv3
# having needed the punt that IS-IS needs, which is the thing #168 is about.
expect "and OSPFv3 needed no OSI punt: it is still off" test "$(osi_count)" -eq 0

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

# --- and the box saying so, which is what #176 is about ----------------------
#
# Everything above is this lab reading a VPP error counter. An operator has
# `show isis neighbor`, which prints a header and no rows, and nothing
# anywhere that connects the two. This is the box making the connection.
echo "  --- show isis adjacency, with no punt ---"
ISISNOPUNT=$($HX -c "show isis adjacency" 2>&1)
printf '%s\n' "$ISISNOPUNT" | sed 's/^/    /'
expect_in "the VTY reports the circuit is Up and Active with nobody on it" \
  "is Up and Active with no adjacency" "$ISISNOPUNT"
expect_in "and names the punt as the reason" \
  "NOT punting IS-IS to the kernel" "$ISISNOPUNT"
expect_in "with the counter that proves the frames were arriving" \
  "counted in osi-input as an unknown OSI protocol" "$ISISNOPUNT"
# The contrast: FRR's own answer to the same question is a header and no rows.
expect_not_in "while FRR's own answer says nothing about a punt" \
  "punt" "$(vtysh -c 'show isis neighbor' 2>&1)"

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

# And therefore the commit that configured it must not have said it would not.
#
# This instance has no `topology ipv6-unicast` - Multi-Topology IS-IS, a second
# SPF for IPv6 (RFC 5120) - and the line above is the proof it does not need
# one: IPv6 floods in the single legacy topology and forwards when both ends
# agree, which is the ordinary single-vendor core and is what this lab is. The
# box used to warn here anyway, on every run of its own acceptance test, and
# then on every unrelated commit further down this file. An alarm that is
# always on is one nobody reads, and these IGP findings are the diagnoses FRR
# cannot make (#198, docs/routing.md).
#
# Read from the commit that configured THIS instance, so it cannot be
# satisfied by a box that simply has no IS-IS.
expect_not_in "and the commit that configured it never claimed it would not" \
  "topology ipv6-unicast" "$ISISUP"

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
ip netns exec peer ip link set dum-ospf6 down
n=0
until { ! in_fib 198.18.0.0/24 && ! in_fib 198.19.0.0/24 \
        && ! in_fib6 2001:db8:19::/64 && ! in_fib6 2001:db8:66::/64; } \
      || [ "$n" -ge 90 ]; do
  sleep 1; n=$((n + 1))
done
echo "  after dropping the peer's stub interfaces (${n}s):"
printf '    198.18.0.0/24    '; vppctl show ip fib 198.18.0.0/24 | head -1
printf '    198.19.0.0/24    '; vppctl show ip fib 198.19.0.0/24 | head -1
printf '    2001:db8:19::/64 '; vppctl show ip6 fib 2001:db8:19::/64 | head -1
printf '    2001:db8:66::/64 '; vppctl show ip6 fib 2001:db8:66::/64 | head -1
expect "the OSPF prefix left the VPP FIB"  sh -c "! vppctl show ip fib 198.18.0.0/24 | grep -q '^198.18.0.0/24'"
expect "the IS-IS prefix left the VPP FIB" sh -c "! vppctl show ip fib 198.19.0.0/24 | grep -q '^198.19.0.0/24'"
expect "and so did the IPv6 one"           sh -c "! vppctl show ip6 fib 2001:db8:19::/64 | grep -q '^2001:db8:19::/64'"
# A prefix that arrives and never leaves is a black hole with a route to it,
# and that is as true of the protocol this box has just learned to run as of
# the two it already did.
expect "and the OSPFv3 prefix left it too" sh -c "! vppctl show ip6 fib 2001:db8:66::/64 | grep -q '^2001:db8:66::/64'"

# The other half. Without it, "the prefixes left" is also what a lab that broke
# the box looks like - and a withdrawal test that cannot tell those apart is
# not testing withdrawal.
ip netns exec peer ip link set dum-ospf up
ip netns exec peer ip link set dum-isis up
ip netns exec peer ip link set dum-ospf6 up
# The IPv6 addresses have to be put back by hand. Linux FLUSHES a link's IPv6
# addresses when it goes down and does not restore them when it comes up (the
# IPv4 one survives), so without this the prefix cannot return and the
# assertion below would be failing for something that is not withdrawal.
ip netns exec peer ip addr add 2001:db8:19::1/64 dev dum-isis 2>/dev/null
ip netns exec peer ip addr add 2001:db8:66::1/64 dev dum-ospf6 2>/dev/null
n=0
until { in_fib 198.18.0.0/24 && in_fib 198.19.0.0/24 \
        && in_fib6 2001:db8:19::/64 && in_fib6 2001:db8:66::/64; } \
      || [ "$n" -ge 90 ]; do
  sleep 1; n=$((n + 1))
done
echo "  after bringing them back up (${n}s):"
printf '    198.18.0.0/24    '; vppctl show ip fib 198.18.0.0/24 | sed -n 2p
printf '    198.19.0.0/24    '; vppctl show ip fib 198.19.0.0/24 | sed -n 2p
printf '    2001:db8:19::/64 '; vppctl show ip6 fib 2001:db8:19::/64 | sed -n 2p
printf '    2001:db8:66::/64 '; vppctl show ip6 fib 2001:db8:66::/64 | sed -n 2p
expect "and all four came back when the peer brought them up (${n}s)" \
  sh -c "vppctl show ip fib 198.18.0.0/24 | grep -q '^198.18.0.0/24' \
         && vppctl show ip fib 198.19.0.0/24 | grep -q '^198.19.0.0/24' \
         && vppctl show ip6 fib 2001:db8:19::/64 | grep -q '^2001:db8:19::/64' \
         && vppctl show ip6 fib 2001:db8:66::/64 | grep -q '^2001:db8:66::/64'"

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
# This used to be `router ospf6`, and it was the assertion #187 was about: the
# VTY accepted the statement, the installer enabled every daemon but that one,
# and the lab asserted the refusal as though a protocol the box refuses were a
# finished feature. It is not an assertion worth keeping against a protocol the
# product claims - the box now runs OSPFv3, asserted above, in the FIB.
#
# The check itself still has to work, or removing it would pass the section
# above just as well. RIP is the honest subject: FRR takes `router rip`, this
# product claims nothing about it, the installer enables no ripd, and an
# operator who types it has to be told at commit rather than after an
# afternoon of wondering why nothing is learned.
RIP=$($HX -c "configure" -c "router rip" -c "exit" -c "commit" 2>&1)
echo "$RIP" | tail -3 | sed 's/^/  /'
expect_in "router rip is refused while ripd is not running" \
  "ripd is not running" "$RIP"
expect_in "and the refusal says where to enable it" "/etc/frr/daemons" "$RIP"
expect_not_in "and nothing was applied" "commit complete" "$RIP"
# The half that keeps the check from being "refuse everything": the three
# protocols whose daemons ARE running committed above, and what their
# adjacencies brought is in the FIB - including OSPFv3, which is the one this
# section used to assert was impossible.
expect "while ospfd, ospf6d and isisd, which are running, committed and forward" \
  sh -c "vppctl show ip fib 198.18.0.0/24 | grep -q '^198.18.0.0/24' \
         && vppctl show ip fib 198.19.0.0/24 | grep -q '^198.19.0.0/24' \
         && vppctl show ip6 fib 2001:db8:66::/64 | grep -q '^2001:db8:66::/64'"

# =============================================================================
# 5. The commands an operator runs while an IGP is wrong (#176)
# =============================================================================
#
# Sections 1 and 2 assert the two screens in the states they exist for - an
# adjacency that will not finish, and a circuit with nobody on it. This section
# is the other half of every one of those: the same screens on a box where
# nothing is wrong. Without it each of those assertions is also satisfied by a
# command that prints its diagnosis unconditionally.
#
# One thing is deliberately NOT staged here. `show ospf neighbor` counts the
# prefixes FRR holds that the VPP FIB does not, and producing that divergence
# live means breaking linux_nl: lab/routing.sh records that taking a route out
# of the FIB by hand does not hold, because linux_nl puts it straight back. The
# missing-prefix arm is asserted in test/test_prismash_igp.py against a FIB where
# the fault can be stated exactly; what is asserted here is that a healthy box
# says so and does not cry wolf.
echo
echo "=========== the IS-IS screens on a box that is working ==========="
ISISADJ=$($HX -c "show isis adjacency" 2>&1)
printf '%s\n' "$ISISADJ" | sed 's/^/  /'
# Read as FIELDS of the adjacency's row. The peer's IS-IS hostname is literally
# "upstream", so any grep for "up" matches the System Id column and passes with
# the adjacency in Init or Down - which is every state except the one asserted.
#
# Keyed on the INTERFACE column and not on the system id, which is what the
# first version did: isisd prints the raw system id until the hostname TLV has
# been learned, so `$1 == "upstream"` matched nothing for the first few seconds
# of every adjacency and the assertion was a race against LSP flooding.
ADJROW=$(printf '%s\n' "$ISISADJ" | awk '$2 == "vppwan0" {print $3, $4}')
ADJID=$(printf '%s\n' "$ISISADJ" | awk '$2 == "vppwan0" {print $1}')
echo "  the adjacency row: id=$ADJID level and state=$ADJROW"
expect "the adjacency is a row of fields, not a line that mentions the words" \
  test "$ADJROW" = "L2 Up"
expect "and it names the system on the other end" test -n "$ADJID"
expect_in "with the IS-IS prefixes counted against the forwarding table" \
  "IS-IS prefix(es) are in the forwarding table" "$ISISADJ"
expect_not_in "and nothing missing from it" "NOT in the forwarding table" \
  "$ISISADJ"
expect_not_in "and the punt is not blamed for a silence there is not" \
  "NOT punting" "$ISISADJ"

ISISIF=$($HX -c "show isis interface" 2>&1)
printf '%s\n' "$ISISIF" | sed 's/^/  /'
IFROW=$(printf '%s\n' "$ISISIF" | awk '$1 == "vppwan0" {print $4, $NF}')
echo "  the circuit row, state and neighbours: $IFROW"
expect "show isis interface has the circuit Up with one neighbour on it" \
  test "$IFROW" = "Up 1"

echo
echo "=========== clear, asserted by what it does ==========="
# Never by what it prints. A `clear` that printed "cleared" and did nothing
# would pass any assertion about its output, and it is the one command an
# operator runs precisely because they want something to happen.
#
# The adjacency has to be GONE the moment the command returns, and back on its
# own afterwards. Both halves: an adjacency that never went down is a `clear`
# that did nothing, and one that never came back is a lab that broke the box.
#
# Not the flap counter, which was the first attempt and does not work: isisd
# deletes the adjacency and builds a new one, and the new one starts at 1 - the
# same number the old one was showing. A counter that reads the same either way
# is not evidence.
expect "the adjacency is up before it is cleared" isis_up
CLEARISIS=$($HX -c "clear isis adjacency" 2>&1)
# When the reading was taken, not only what it said. The hello interval is 3
# seconds, so an adjacency that is back by then is one that re-formed rather
# than one the clear failed to remove - and on a box running eight labs at once
# a vtysh call can take longer than that. This check reported a product defect
# for exactly that reason in one release suite and passed in isolation (#218).
_CLEARED_AT=$(date +%s)
DOWN_NOW=$(isis_state)
_READ_AFTER=$(( $(date +%s) - _CLEARED_AT ))
printf '%s\n' "$CLEARISIS" | sed 's/^/  /'
echo "  the adjacency immediately after the clear: ${DOWN_NOW:-gone}"
expect_timed "clearing it took the adjacency down there and then" \
  3 "$_READ_AFTER" test -z "$DOWN_NOW"
n=0
until isis_up || [ "$n" -ge 90 ]; do sleep 1; n=$((n + 1)); done
echo "  and back up after ${n}s"
expect "and it came back on its own" isis_up

ospf_uptime_ms() {
  vtysh -c 'show ip ospf neighbor json' 2>/dev/null \
    | tr ',' '\n' | awk -F: '/upTimeInMsec/ {gsub(/[^0-9]/,"",$2); print $2; exit}'
}
expect "the OSPF adjacency is Full before the process is cleared" ospf_full
UP_BEFORE=$(ospf_uptime_ms)
CLEAROSPF=$($HX -c "clear ospf process" 2>&1)
OSPF_NOW=$(ospf_state)
printf '%s\n' "$CLEAROSPF" | sed 's/^/  /'
echo "  the adjacency immediately after the clear: ${OSPF_NOW:-gone}"
expect "clearing the process took the adjacency out of Full there and then" \
  sh -c "[ \"$OSPF_NOW\" != \"Full/DR\" ] && [ \"$OSPF_NOW\" != \"Full/Backup\" ]"
n=0
until ospf_full || [ "$n" -ge 120 ]; do sleep 1; n=$((n + 1)); done
UP_AFTER=$(ospf_uptime_ms)
echo "  adjacency uptime: ${UP_BEFORE}ms -> ${UP_AFTER}ms, Full again after ${n}s"
expect "and it reached Full again" ospf_full
# The uptime is the time since it reached Full, so it cannot be larger after a
# process that was torn down and rebuilt than it was before.
expect "on a process that really was rebuilt" \
  test "${UP_AFTER:-0}" -lt "${UP_BEFORE:-0}"

echo
echo "=========== set-overload-bit, by its effect on the neighbour ==========="
# Asserted where it has to be true - in the NEIGHBOUR's copy of this box's LSP
# - and never by the configuration containing the words (#176). ATT/P/OL is the
# last column of the database listing and OL is the third of the three.
# Matched on either name this box's LSP can appear under: isisd prints the raw
# system id until it has learned the hostname TLV, and the system id is the one
# this lab put there - `net 49.0001.0000.0000.0001.00`.
peer_ol() {
  ip netns exec peer vtysh -N peer -c 'show isis database' 2>/dev/null \
    | awk '$1 == "prisma-routing.00-00" || $1 == "0000.0000.0001.00-00" \
           {print $NF; exit}'
}
echo "  the neighbour's copy of this box's LSP, before: $(peer_ol)"
expect "the neighbour does not see this box as overloaded yet" \
  test "$(peer_ol)" = "0/0/0"
OL=$($HX -c "configure" -c "router isis HEXA" -c "set-overload-bit" \
     -c "exit" -c "commit" 2>&1)
printf '%s\n' "$OL" | tail -2 | sed 's/^/  /'
expect_in "the VTY commits it" "commit complete" "$OL"
n=0
until [ "$(peer_ol)" = "0/0/1" ] || [ "$n" -ge 90 ]; do sleep 1; n=$((n + 1)); done
echo "  after (${n}s): $(peer_ol)"
expect "the NEIGHBOUR sees this box as overloaded" test "$(peer_ol)" = "0/0/1"
OLSHOW=$($HX -c "show isis adjacency" 2>&1)
printf '%s\n' "$OLSHOW" | sed 's/^/  /'
expect_in "and the box says so on the screen an operator is already looking at" \
  "overload bit set" "$OLSHOW"

# Off again. A maintenance drain that cannot be switched back is not one, and
# the box must stop saying it the moment the LSP stops carrying it.
$HX -c "configure" -c "router isis HEXA" -c "no set-overload-bit" \
    -c "exit" -c "commit" > /tmp/ol-off.log 2>&1
tail -2 /tmp/ol-off.log | sed 's/^/  commit: /'
n=0
until [ "$(peer_ol)" = "0/0/0" ] || [ "$n" -ge 90 ]; do sleep 1; n=$((n + 1)); done
echo "  after taking it off (${n}s): $(peer_ol)"
expect "taking it off reaches the neighbour too" test "$(peer_ol)" = "0/0/0"
expect_not_in "and the box stops saying it" "overload bit set" \
  "$($HX -c 'show isis adjacency' 2>&1)"

echo
echo "=========== a subscriber-facing port speaking OSPF, in both spellings ==========="
# The cross-half check - FRR does not know what a CGNAT pool is and the
# dataplane does not know what OSPF is - on the only box that can disprove it.
# Its two halves arrive in different namespaces: the addresses are the
# dataplane's, keyed `host-veth-vpp`, while `passive-interface` is read by FRR
# and FRR knows this pair as `vppwan0` and by no other name. Neither spelling
# is invented here; both come off this box's one linux_cp pair.
#
# Before #205 the warning fired on the spelling FRR acts on and went quiet on
# the one it ignores, so the operator who did the right thing was told off and
# the one who typed a name FRR will never match was reassured - and this VTY's
# own completion offers the second, because it lists what `show interface`
# reports.
#
# `commit check` throughout, never `commit`: the pool that makes this
# interface subscriber-facing is staged and thrown away, so nothing this lab
# forwards is touched by it.
facing_check() {   # extra VTY lines, then commit check
  $HX -c "configure" \
      -c "nat" -c "cgnat" -c "pool facing" -c "prefix-inside 10.0.0.0/24" \
      -c "prefix-outside 203.0.113.8/32" -c "block-size 512" -c "exit" \
      -c "top" "$@" -c "commit check" 2>&1
}
FACING_BARE=$(facing_check)
FACING_TAP=$(facing_check -c "router ospf" \
             -c "passive-interface vppwan0" -c "exit")
FACING_DP=$(facing_check -c "router ospf" \
            -c "passive-interface host-veth-vpp" -c "exit")
printf '%s\n' "$FACING_BARE" | grep -i "facing subscribers" | sed 's/^/  bare:    /'
printf '%s\n' "$FACING_TAP" | grep -i "facing subscribers\|made passive" | sed 's/^/  vppwan0: /'
printf '%s\n' "$FACING_DP" | grep -i "facing subscribers\|made passive" | sed 's/^/  host-:   /'
SAYS="it is facing subscribers, and it is not passive"
# expect_said and not expect_in: the reason is prose and say_finding wraps it
# to the console, so this sentence folds after "facing" and a needle that
# straddles the fold stops matching (#279). The sentence is the assertion;
# where it breaks is not.
expect_said "a port inside a CGNAT pool speaking OSPF is reported at all" \
  "$SAYS" "$FACING_BARE"
expect_in "and it is the dataplane's name the warning names" \
  "interface host-veth-vpp" "$FACING_BARE"
expect_not_said "the spelling FRR acts on silences it" "$SAYS" "$FACING_TAP"
expect_said "while the one FRR ignores silences nothing" "$SAYS" "$FACING_DP"
expect_in "and the statement that does nothing is named, with the name FRR wants" \
  "FRR knows this interface as vppwan0, not host-veth-vpp" "$FACING_DP"

echo
echo "=========== the MTU this box's two halves disagree about ==========="
# The other MTU fault, and a different one from section 1. There the two ends
# of the WIRE disagreed; here the two halves of THIS BOX do - the dataplane and
# its TAP - which is what the check at commit time has always been about.
#
# It has to be staged, and the way it is staged is recorded because it took a
# while to find (lab/routing.sh, same section): lcp-sync mirrors VPP's MTU onto
# the TAP, so the two are equal on a box nobody has interfered with, and `mtu`
# at this VTY sets both halves on purpose. Setting the Linux side propagates
# back into VPP even with lcp-sync off. Only vppctl, with lcp-sync off, pulls
# them apart - which also takes the OSPF adjacency down, so this is last.
vppctl lcp lcp-sync off
THERE=$(tap_mtu)
vppctl set interface mtu packet 1400 host-veth-vpp
HERE=$(mtu_of)
echo "  the dataplane is at $HERE, its Linux side at $THERE"
expect "the two halves really do disagree, or the rest of this proves nothing" \
  sh -c "[ \"$HERE\" != \"$THERE\" ]"
SPLITIF=$($HX -c "show ospf interface" 2>&1)
printf '%s\n' "$SPLITIF" | sed 's/^/  /'
SPLITROW=$(printf '%s\n' "$SPLITIF" | awk '$1 == "vppwan0" {print $(NF-2), $(NF-1), $NF}')
echo "  the vppwan0 row, last three fields: $SPLITROW"
expect "show ospf interface has what OSPF believes and what the dataplane has" \
  test "$SPLITROW" = "$THERE $HERE (host-veth-vpp)"
# The same sentence the commit check prints, from the same place, because two
# diagnoses of one fact that disagree are worse than one.
expect_in "and the stall is named in the words commit already used" \
  "carries OSPF and its MTU is $HERE while its Linux side vppwan0 is at $THERE" \
  "$SPLITIF"
MTUWARN=$($HX -c "configure" -c "router static" \
          -c "10.77.0.0/16 next-hop blackhole" -c "commit check" 2>&1)
expect_in "which is the sentence commit check prints, word for word" \
  "carries OSPF and its MTU is $HERE while its Linux side vppwan0 is at $THERE" \
  "$MTUWARN"
# Put them back the same and it must go quiet, or the sentence is
# unconditional and says nothing.
vppctl set interface mtu packet "$THERE" host-veth-vpp
vppctl lcp lcp-sync on
expect_not_in "and it is silent once the two halves agree again" \
  "carries OSPF and its MTU is" "$($HX -c 'show ospf interface' 2>&1)"

echo
echo "=========== the read-back check must not fire on a working box (#197) ==="
# `commit` now reads FRR's OWN running configuration back and refuses when a
# statement this commit introduced is not in it. The danger of that check is
# not that it misses a drop: it is that it refuses a commit that WORKED. These
# are the two shapes that would, and both are measured rather than assumed -
# lab/frr-readback.sh is where the measurement lives.
#
#   OMITTED    FRR accepts `ip ospf hello-interval 10`, its own default, and
#              does not store it. A check with no list calls that a dropped
#              statement on a box where OSPF is running perfectly.
#   REWRITTEN  FRR accepts `passive-interface vppwan0` and reads it back as
#              `ip ospf passive`, inside the interface stanza rather than the
#              router one. Same consequence, different cause.
#
# Last in the file on purpose: it changes OSPF timers on the live interface,
# and every adjacency assertion above has already been made.
#
# vppwan0 and not host-veth-vpp, because these are FRR statements and #205
# established that an FRR statement has to carry the name FRR can act on.
DEFAULTS=$($HX -c "configure" -c "interface vppwan0" \
           -c "ip ospf hello-interval 10" \
           -c "ip ospf retransmit-interval 5" \
           -c "exit" -c "router ospf" -c "passive-interface vppwan0" \
           -c "exit" -c "commit" 2>&1)
echo "$DEFAULTS" | tail -3 | sed 's/^/  /'
expect_in "a commit of statements FRR does not store still says complete" \
  "commit complete" "$DEFAULTS"
# The two assertions that make the one above mean something. Without them
# "commit complete" is equally consistent with FRR having stored everything,
# which is a box this check was never in danger on.
expect "and FRR really did leave the hello-interval out, so this was the case" \
  sh -c "! vtysh -c 'show running-config' | grep -q 'ip ospf hello-interval 10'"
expect "and really did rewrite passive-interface, so was that" \
  sh -c "vtysh -c 'show running-config' | grep -q 'ip ospf passive'"

# The control: a value FRR DOES store, committed the same way, so "commit
# complete" above is not simply what this box says to everything.
NONDEF=$($HX -c "configure" -c "interface vppwan0" \
         -c "ip ospf hello-interval 7" -c "exit" -c "commit" 2>&1)
expect_in "a value FRR does store commits too" "commit complete" "$NONDEF"
expect "and FRR is holding that one, read back from the box" \
  sh -c "vtysh -c 'show running-config' | grep -q 'ip ospf hello-interval 7'"

echo
echo "=========== adjacencias ==========="
printf 'OSPF  : '; vtysh -c "show ip ospf neighbor" 2>/dev/null | grep -E "^10\.0\.0\.2" | awk '{print "neighbour "$1" state "$3}'
printf 'IS-IS : '; vtysh -c "show isis neighbor" 2>/dev/null | awk '$2 == "vppwan0" {print "neighbour "$1" state "$4" interface "$2}'

echo
echo "  wall clock: $(( $(date +%s) - LAB_T0 ))s"
verdict
