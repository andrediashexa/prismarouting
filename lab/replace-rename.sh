#!/bin/sh
# replace pattern and rename in configuration mode, through the VTY to a running box (#585).
#
#   sh tools/lab.sh replace-rename        the two words, end to end
#   sh tools/lab.sh replace-rename off    the VTY before #585 - every check
#                                         that an edit happened MUST fail
#
# Through prismash against a running VPP and FRR, because what the issue asks
# is about the box and not about the text: a replace and a rename change only
# the candidate, and what `commit` then hands the dataplane and FRR is the
# edited configuration - the pool under its new name holding the same sets,
# every BGP and route-map line naming the renamed list, map and peer-group.
# The refusals are driven here too, each against the box's own checker: FRR's
# parser for a line the edit broke, the commit check for a reference it left
# dangling, a name already taken.
#
# The off run is the same session through lab/replace_rename_hook.py with
# EDIT=off, the build before #585: both words fall through to FRR, which
# refuses them, so nothing is renamed or replaced, and the refusals are FRR's
# "Unknown command" rather than the edit's own. The controls - the first
# commit, the dataplane holding the pool - stay green.
#
# OFF-RED-AT-LEAST: 30
#
# A FLOOR, measured on the first off run of this lab (#585): 30 of its 35
# checks red, the two controls and the three that hold either way green -
# the candidate is the running configuration after a dry run and after the
# refusals, and the pool translates what it did. See lab/bypass.sh for why it
# is a floor and not a target.
set -u
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api, wait_vpp
use_binary_api
export PRISMA_ROLLBACK=/tmp/rollback PRISMA_CONFIG=/tmp/prisma-routing.conf
export PRISMA_POLICY_PATH=/tmp/policy-options
rm -rf /tmp/rollback /tmp/policy-options

mkdir -p /var/run/frr /etc/frr
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
printf 'hostname prisma-routing\n' > /etc/frr/bgpd.conf
chown -R frr:frr /var/run/frr /etc/frr 2>/dev/null
start_frr bgpd

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp

EDIT=on; [ "$MODE" = off ] && EDIT=off
export EDIT
HX="python3 /src/lab/replace_rename_hook.py"
[ "$MODE" = off ] && echo "  MODE=off: the VTY before #585."

echo "=========== 1. what is edited: a pool on two lists, BGP using them ==="
C1=$($HX -c "configure" \
    -c 'ip prefix-list CGN-IN seq 5 deny 100.64.1.0/24 description "management range"' \
    -c "ip prefix-list CGN-IN seq 10 permit 100.64.0.0/22 le 32" \
    -c "ip prefix-list CGN-OUT seq 10 permit 200.150.0.16/30" \
    -c "route-map RM-OUT permit 10" -c "match ip address prefix-list CGN-OUT" \
    -c "exit" \
    -c "router bgp 65000" -c "bgp router-id 10.0.0.1" \
    -c "no bgp ebgp-requires-policy" -c "neighbor PG peer-group" \
    -c "neighbor PG remote-as 65001" -c "neighbor 10.0.0.2 peer-group PG" \
    -c "address-family ipv4 unicast" -c "neighbor PG activate" \
    -c "neighbor PG route-map RM-OUT out" -c "neighbor PG prefix-list CGN-IN in" \
    -c "exit-address-family" -c "exit" \
    -c "nat" -c "cgnat" -c "pool lab" -c "prefix-list-inside CGN-IN" \
    -c "prefix-list-outside CGN-OUT" -c "block-size 4096" \
    -c "mode deterministic" -c "exit" -c "exit" -c "exit" -c "commit" 2>&1)
echo "$C1" | tail -3 | sed 's/^/  /'
CONF0=$(vppctl show bpa config | tr -d '\r')
echo "$CONF0" | sed -n '/cgnat pool lab/,/exit/p' | sed 's/^/  /'

echo
echo "=========== 2. a dry run: the difference, the count, nothing staged ==="
DRY=$($HX -c "configure" \
    -c 'replace pattern "200\.150\.0\.16/30" with 200.150.0.20/30 | compare' \
    -c "show configuration compare" 2>&1)
echo "$DRY" | sed 's/^/  /'

echo
echo "=========== 3. replace inside a pool covers that pool, and commits ==="
REP=$($HX -c "configure" -c "nat" -c "cgnat" -c "pool lab" \
    -c "replace pattern 4096 with 2048" -c "show this" -c "top" \
    -c "show configuration compare" -c "commit remap" 2>&1)
echo "$REP" | sed 's/^/  /'
CONF1=$(vppctl show bpa config | tr -d '\r')

echo
echo "=========== 4. rename a prefix-list: the pool, the route-map, BGP ==="
RNL=$($HX -c "configure" -c "rename ip prefix-list CGN-IN to CGN-INSIDE" \
    -c "show configuration compare" -c "commit" 2>&1)
echo "$RNL" | sed 's/^/  /'
FRR1=$(vtysh -c "show running-config" 2>&1)
CONF2=$(vppctl show bpa config | tr -d '\r')
RUN1=$($HX -c "show running-config" 2>&1)

echo
echo "=========== 5. rename a peer-group and a route-map ==========="
RNP=$($HX -c "configure" -c "router bgp 65000" \
    -c "rename peer-group PG to UPSTREAM" -c "top" \
    -c "rename route-map RM-OUT to UPSTREAM-OUT" -c "commit confirmed 5" 2>&1)
echo "$RNP" | sed 's/^/  /'
FRR2=$(vtysh -c "show running-config" 2>&1)
CONFIRM=$($HX -c "configure" -c "commit" 2>&1)
echo "$CONFIRM" | tail -2 | sed 's/^/  confirmed: /'

echo
echo "=========== 6. rename a pool, from its level ==========="
RNO=$($HX -c "configure" -c "nat" -c "cgnat" -c "rename pool lab to lab-a" \
    -c "top" -c "commit" 2>&1)
echo "$RNO" | sed 's/^/  /'
CONF3=$(vppctl show bpa config | tr -d '\r')

echo
echo "=========== 7. refused whole: the first failing line, nothing left ==="
BAD=$($HX -c "configure" \
    -c 'replace pattern "route-map UPSTREAM-OUT permit" with "route-map UPSTREAM-OUT allow"' \
    -c 'replace pattern "prefix-list CGN-OUT seq" with "prefix-list CGN-GONE seq"' \
    -c "rename ip prefix-list CGN-INSIDE to CGN-OUT" \
    -c "show configuration compare" 2>&1)
echo "$BAD" | sed 's/^/  /'
RIP=$($HX -c "configure" \
    -c "router rip" -c "distribute-list prefix CGN-INSIDE out" -c "exit" \
    -c "rename ip prefix-list CGN-INSIDE to CGN-A" 2>&1)
echo "$RIP" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

# Controls: the box this lab edits, there in both runs.
expect_in "the first configuration was committed (control)" \
  "commit complete" "$C1"
expect_in "and the dataplane holds the pool on its lists (control)" \
  "prefix-list-inside CGN-IN" "$CONF0"

expect_in "the dry run shows the line it would change" \
  "+ip prefix-list CGN-OUT seq 10 permit 200.150.0.20/30" "$DRY"
expect_in "and says how many substitutions" "1 substitution(s) in 1 statement(s)" "$DRY"
expect_in "and that it passes and stages nothing" \
  "it passes the checks; nothing staged ('| compare' is a dry run)" "$DRY"
expect_in "after which the candidate is the running configuration" \
  "no differences" "$DRY"

expect_in "replace inside the pool changed its block-size" \
  " block-size 2048" "$REP"
expect_in "and was staged, said so" "staged in the candidate" "$REP"
expect_in "show configuration compare shows it" "+   block-size 2048" "$REP"
expect_in "the commit took it, asked by name: a block size re-maps" \
  "commit complete" "$REP"
expect_in "and the dataplane has the new block size" " block-size 2048" "$CONF1"

expect_in "rename found the list in four places" "renamed in 4 place(s)" "$RNL"
expect_in "the rename committed" "commit complete" "$RNL"
expect_in "FRR has the list under its new name" \
  "ip prefix-list CGN-INSIDE seq 10 permit 100.64.0.0/22 le 32" "$FRR1"
expect "and not under the old" \
  sh -c "! printf '%s' \"\$1\" | grep -q 'prefix-list CGN-IN '" _ "$FRR1"
expect_in "BGP's filter names the new list" \
  "neighbor PG prefix-list CGN-INSIDE in" "$FRR1"
expect_in "the dataplane's pool names it" "prefix-list-inside CGN-INSIDE" "$CONF2"
expect_in "and still translates what it did" " inside 100.64.2.0/23" "$CONF2"
expect_in "the entry kept its description" \
  'ip prefix-list CGN-INSIDE seq 5 deny 100.64.1.0/24 description "management range"' \
  "$RUN1"

expect_in "the renames went out as a commit confirmed" "in 5 minute(s)" "$RNP"
expect_in "the peer-group was renamed from inside router bgp" \
  "neighbor UPSTREAM peer-group" "$FRR2"
expect_in "and its member follows it" "neighbor 10.0.0.2 peer-group UPSTREAM" "$FRR2"
expect_in "the route-map was renamed, and BGP's use of it" \
  "neighbor UPSTREAM route-map UPSTREAM-OUT out" "$FRR2"
expect_in "the declaration too" "route-map UPSTREAM-OUT permit 10" "$FRR2"

expect_in "the pool was renamed and committed" "commit complete" "$RNO"
expect_in "the dataplane holds the pool under its new name" \
  "cgnat pool lab-a" "$CONF3"
expect "and not under the old" \
  sh -c "! printf '%s' \"\$1\" | grep -q 'cgnat pool lab\$'" _ "$CONF3"

expect_said "a line FRR refuses is named, and nothing staged" \
  "of the result is refused; nothing staged" "$BAD"
expect_in "naming the line" "route-map UPSTREAM-OUT allow 10" "$BAD"
expect_said "a reference the edit leaves dangling fails the commit check" \
  "the result fails the commit check; nothing staged" "$BAD"
expect_said "naming what dangles" \
  "pool 'lab-a': prefix-list-outside CGN-OUT does not exist" "$BAD"
expect_said "a taken name is refused" \
  "rename: that name is taken; nothing renamed" "$BAD"
expect_in "and every refusal left the candidate as it was" \
  "no differences" "$BAD"
expect_said "a reference rename cannot follow is refused" \
  "rename: a reference this VTY cannot follow; nothing renamed" "$RIP"
expect_in "naming it" "distribute-list prefix CGN-INSIDE out" "$RIP"

verdict
