#!/bin/sh
# `no neighbor X` at the prompt removes a BGP neighbour, cleanly (#562).
#
#   sh tools/lab.sh bgp-no-neighbor     -> LAB PASSED
#
# NO-OFF-RUN: the fix is how the VTY spells a removal, with no switch in the product; an off mode would be a switch added only for this lab to flip. The control is this lab run against the tree before it (0.149.22): the eight removals below went red there - see "Measured before" at the end of this header.
#
# Found by #558 phase 3's lab: the VTY staged `no neighbor X` as a statement
# of its own. FRR deleted the neighbour, and the commit's check then found the
# neighbour's own lines - still in the candidate - missing from FRR and said
# FRR "did not keep" them; with an address-family statement for the
# neighbour, frr-reload refused the file ("% Specify remote-as or peer-group
# commands first") and the commit rolled back. The API's DeleteConfig
# (0.149.22) removed a neighbour statement by statement; the prompt now goes
# through that same function, so the two cannot disagree.
#
# Every neighbour shape the issue names, each removed by `no neighbor X` and
# committed on its own, then read back from FRR itself and from the VTY:
#
#   192.0.2.1    IPv4, router level only (remote-as, description)
#   192.0.2.2    IPv4, with an ipv4 unicast family statement
#   2001:db8::2  IPv6, activated in the ipv6 unicast family
#   192.0.2.4    a member of peer-group PG
#   PG           the peer-group itself (refused while a member names it)
#   192.0.2.5    in `router bgp 65000 vrf red`, with a family statement
#
# Measured before (0.149.22, this lab unchanged): 192.0.2.1 committed but the
# check said FRR "did not keep" its statements; 192.0.2.2, 2001:db8::2 and
# 192.0.2.5 rolled back on frr-reload; the member and the group left lines in
# FRR or the configuration.
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
. /src/lab/verdict.sh
set -u
use_binary_api
export PRISMA_ROLLBACK=/tmp/rollback PRISMA_CONFIG=/tmp/prisma-routing.conf
rm -rf /tmp/rollback

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
printf 'hostname prisma-routing\n' > /etc/frr/bgpd.conf
chown -R frr:frr /var/run/frr /etc/frr
pgrep zebra >/dev/null || start_frr bgpd
sleep 2

HX="python3 /src/src/prismash/prismash.py"
OUT=/tmp/bgp-no-neighbor; mkdir -p $OUT

echo "=========== 1. the neighbours, committed ==========="
$HX -c "configure" -c "router bgp 65000" \
    -c "neighbor 192.0.2.1 remote-as 65001" \
    -c "neighbor 192.0.2.1 description router-level-only" \
    -c "neighbor 192.0.2.2 remote-as 65002" \
    -c "neighbor 2001:db8::2 remote-as 65003" \
    -c "neighbor PG peer-group" \
    -c "neighbor PG remote-as 65004" \
    -c "neighbor 192.0.2.4 peer-group PG" \
    -c "address-family ipv4 unicast" \
    -c "neighbor 192.0.2.2 soft-reconfiguration inbound" \
    -c "exit-address-family" \
    -c "address-family ipv6 unicast" \
    -c "neighbor 2001:db8::2 activate" \
    -c "exit-address-family" \
    -c "exit" \
    -c "router bgp 65000 vrf red" \
    -c "neighbor 192.0.2.5 remote-as 65005" \
    -c "address-family ipv4 unicast" \
    -c "neighbor 192.0.2.5 soft-reconfiguration inbound" \
    -c "exit-address-family" \
    -c "exit" \
    -c "commit" > $OUT/setup.txt 2>&1
sed 's/^/  /' $OUT/setup.txt
vtysh -c "show running-config" > $OUT/frr-before.txt 2>&1
grep -E "neighbor" $OUT/frr-before.txt | sed 's/^/  frr: /'

# remove <label> <block> <neighbour>: `no neighbor X` and commit, then what
# FRR and the VTY hold for it afterwards.
remove() {
    $HX -c "configure" -c "$2" -c "no neighbor $3" -c "exit" -c "commit" \
        > $OUT/$1.txt 2>&1
    vtysh -c "show running-config" > $OUT/$1.frr 2>&1
    $HX -c "show running-config" > $OUT/$1.vty 2>&1
    echo "=========== no neighbor $3 ($2) ==========="
    sed 's/^/  /' $OUT/$1.txt
}
remove v4 "router bgp 65000" 192.0.2.1
remove v4fam "router bgp 65000" 192.0.2.2
remove v6 "router bgp 65000" 2001:db8::2
remove pg-early "router bgp 65000" PG
remove member "router bgp 65000" 192.0.2.4
remove pg "router bgp 65000" PG
remove vrf "router bgp 65000 vrf red" 192.0.2.5

# clean <label> <neighbour>: the commit said nothing went wrong, and neither
# FRR nor the VTY's running configuration names the neighbour any more.
committed() { grep -q "commit complete" $OUT/$1.txt \
                && ! grep -qiE "did not keep|rolling back|error" $OUT/$1.txt; }
gone() { ! grep -qE "neighbor $2( |$)" $OUT/$1.frr \
           && ! grep -qE "neighbor $2( |$)" $OUT/$1.vty; }

expect "the six neighbours and the group were committed" \
  grep -q "commit complete" $OUT/setup.txt
expect "an IPv4 neighbour with no family: commit complete, nothing 'not kept'" \
  committed v4
expect "  and FRR and the running configuration no longer name it" gone v4 192.0.2.1
expect "an IPv4 neighbour with a family statement: commit complete" committed v4fam
expect "  and it is gone from FRR and the configuration, its family line too" \
  gone v4fam 192.0.2.2
expect "an IPv6 neighbour activated in ipv6 unicast: commit complete" committed v6
expect "  and it is gone, the activate with it" gone v6 2001:db8::2
expect "the peer-group while a member names it: refused, naming the member" \
  grep -q "192.0.2.4" $OUT/pg-early.txt
expect "  and nothing was removed: the group and its member are still in FRR" \
  sh -c "grep -q 'neighbor PG peer-group' $OUT/pg-early.frr && grep -q 'neighbor 192.0.2.4 peer-group PG' $OUT/pg-early.frr"
expect "a peer-group member: commit complete" committed member
expect "  and the member is gone, the group left alone" \
  sh -c "! grep -qE 'neighbor 192.0.2.4( |\$)' $OUT/member.frr && grep -q 'neighbor PG peer-group' $OUT/member.frr"
expect "the peer-group with no member left: commit complete" committed pg
expect "  and the group is gone, its remote-as with it" gone pg PG
expect "a neighbour in a VRF's router bgp, with a family: commit complete" \
  committed vrf
expect "  and it is gone from that router" gone vrf 192.0.2.5

verdict
