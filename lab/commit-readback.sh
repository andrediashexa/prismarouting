#!/bin/sh
# A line a daemon refuses after vtysh -C accepted it fails the commit (#539).
# OFF-RED-AT-LEAST: 8
#
# #535's lab found it: `set extcommunity rt 65000:100 additive` passed FRR's
# syntax check - `vtysh -C`, which the commit check relies on - bgpd refused
# the line when it was applied, the route-map clause ran with no set at all,
# and the commit said "commit complete". The compiler no longer writes that
# line, so lab/commit_readback_hook.py puts the old spelling back for this
# lab; the line is the real one, refused by the real bgpd.
#
# MODE=off runs the same commits through a build without #539's read-back
# (the hook's READBACK=off), and every check that the commit noticed must go
# red: that build says "commit complete".
MODE=${1:-on}
set -u
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing /var/run/frr /etc/frr
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api, wait_vpp
use_binary_api
export PRISMA_ROLLBACK=/tmp/rollback PRISMA_CONFIG=/tmp/prisma-routing.conf
export PRISMA_POLICY_PATH=/tmp/policy-options
rm -rf /tmp/rollback /tmp/policy-options

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
printf 'hostname prisma-routing\n' > /etc/frr/bgpd.conf
chown -R frr:frr /var/run/frr /etc/frr
pgrep zebra >/dev/null || start_frr bgpd
sleep 2

HX="python3 /src/src/prismash/prismash.py"
READBACK=on; [ "$MODE" = off ] && READBACK=off
export READBACK
OLD="python3 /src/lab/commit_readback_hook.py"

echo "=========== the session, through the VTY ==========="
SETUP=$($HX -c "configure" -c "router bgp 65000" -c "bgp router-id 10.0.0.1" \
  -c "no bgp ebgp-requires-policy" -c "neighbor 10.0.0.2 remote-as 65001" \
  -c "address-family ipv4 unicast" -c "neighbor 10.0.0.2 activate" \
  -c "exit-address-family" -c "exit" \
  -c "policy-options" -c "extended-community EC members rt:65000:100" \
  -c "exit" -c "commit" 2>&1)
printf '%s\n' "$SETUP" | tail -2 | sed 's/^/  /'
printf '%s' "$SETUP" | grep -q "commit complete" || {
  echo "the setup did not commit - nothing below would mean anything"; exit 1; }

echo; echo "=========== the premise: vtysh -C accepts the line ==========="
printf 'route-map RB-PROBE permit 10\n set extcommunity rt 65000:100 additive\nexit\n' \
  > /tmp/rb-probe.conf
vtysh -C -f /tmp/rb-probe.conf >/tmp/rb-probe.out 2>&1
_rc=$?
sed 's/^/  /' /tmp/rb-probe.out | tail -3
expect "vtysh -C accepts 'set extcommunity rt 65000:100 additive'" test "$_rc" = 0
# And applied, bgpd refuses it - yet its own configuration read-back keeps the
# line. This is why the commit asks what bgpd RUNS (`show route-map`) and not
# what it was told: a read-back of the configuration passes this line.
vtysh -c "configure terminal" -c "route-map RB-PROBE permit 10" \
  -c "set extcommunity rt 65000:100 additive" > /tmp/rb-apply.out 2>&1
sed 's/^/  /' /tmp/rb-apply.out | tail -2
BGPCFG=$(vtysh -c "show running-config bgpd" 2>&1)
BGPRUN=$(vtysh -c "show route-map RB-PROBE json" 2>&1 \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["bgpd"]["RB-PROBE"]["rules"][0]["setClauses"])' 2>&1)
echo "  bgpd runs the clause with set clauses: $BGPRUN"
expect_in "bgpd's configuration read-back keeps the line it refused" \
  "set extcommunity rt 65000:100 additive" "$BGPCFG"
expect "and bgpd runs the clause with no set at all" test "$BGPRUN" = "[]"
vtysh -c "configure terminal" -c "no route-map RB-PROBE" >/dev/null 2>&1

# The policy, committed through the prismash it is handed: one term that
# adds the extended community, imported on the session.
commit_policy() {
  "$@" -c "configure" -c "policy-options" -c "policy-statement P" \
    -c "term ec" -c "from route-filter 10.0.0.0/8 orlonger" \
    -c "set extended-community add EC" -c "then accept" -c "exit" -c "exit" \
    -c "exit" -c "router bgp 65000" -c "address-family ipv4 unicast" \
    -c "neighbor 10.0.0.2 import P" -c "exit-address-family" -c "exit" \
    -c "commit" 2>&1
}

echo; echo "=========== committed with the line bgpd refuses, READBACK=$READBACK ==========="
_t0=$(date +%s%N)
REFUSED=$(commit_policy $OLD)
_t1=$(date +%s%N)
printf '%s\n' "$REFUSED" | grep -vE "^(note|warning):" | sed 's/^/  /'
FRRRUN=$(vtysh -c "show running-config" 2>&1)
echo "  -- FRR holds, afterwards:"
printf '%s\n' "$FRRRUN" | sed -n '/^route-map PO-P/,/^exit/p' | sed 's/^/    /'
expect_not_said "the commit does not say 'commit complete'" "commit complete" "$REFUSED"
expect_said "it says a line the policy compiled to was refused" \
  "FRR refused 1 line(s) the routing policy compiled to" "$REFUSED"
expect_in "it names the line" "set extcommunity rt 65000:100 additive" "$REFUSED"
expect_said "and the term it came from, in the VTY's words" \
  "in policy-statement P term ec:" "$REFUSED"
expect_said "and says the commit was rolled back" \
  "commit failed; rolling back to the previous state" "$REFUSED"
expect_not_in "FRR holds no map of the refused policy afterwards" \
  "route-map PO-P-IN" "$FRRRUN"
expect_not_in "and the neighbor is not left pointing at it" \
  "neighbor 10.0.0.2 route-map PO-P-IN in" "$FRRRUN"
BOXCFG=$($HX -c "show running-config" 2>&1)
expect_not_in "and the box's own configuration does not hold the policy either" \
  "policy-statement P" "$BOXCFG"
echo "  that commit took $(( (_t1 - _t0) / 1000000 )) ms"

echo; echo "=========== the same policy, as the compiler writes it today ==========="
# The control: without the refused line the same commit completes. A read-back
# that refused this would be the false refusal #208 warned about.
_t0=$(date +%s%N)
GOOD=$(commit_policy $HX)
_t1=$(date +%s%N)
printf '%s\n' "$GOOD" | grep -vE "^(note|warning):" | tail -3 | sed 's/^/  /'
FRRRUN=$(vtysh -c "show running-config" 2>&1)
expect_said "the policy bgpd keeps commits" "commit complete" "$GOOD"
expect_in "and bgpd holds its set" " set extcommunity rt 65000:100" "$FRRRUN"
GOODMS=$(( (_t1 - _t0) / 1000000 ))

echo; echo "=========== what the read-back costs ==========="
# One `show route-map json` per commit whose compiled route-maps changed, and
# none for any other commit. Asked five times, the median, beside the commit.
_ms=""
for _i in 1 2 3 4 5; do
  _a=$(date +%s%N); vtysh -c "show route-map json" >/dev/null 2>&1
  _b=$(date +%s%N); _ms="$_ms $(( (_b - _a) / 1000000 ))"
done
_med=$(printf '%s\n' $_ms | sort -n | sed -n 3p)
echo "  show route-map json:$_ms ms (median $_med); the commit: $GOODMS ms"

verdict
