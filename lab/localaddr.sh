#!/bin/sh
# A public pool that contains one of the box's own addresses.
#
#   sh tools/lab.sh localaddr
#
# `docs/routing.md` calls two defences REQUIRED, and the paragraph above them
# states the symptom: `bpa-out2in` runs on the `ip4-unicast` arc BEFORE
# `ip4-lookup`, so it sees the box's own BGP and OSPF packets first. A peering
# address inside a public pool means the box translates its own control plane,
# and the session drops for no apparent reason - a dead BGP session with
# healthy interfaces and a healthy dataplane, which is the worst kind of
# outage to diagnose because nothing is broken.
#
# Both defences were implemented and NEITHER was asserted anywhere. A MUST
# sweep neutralised both with four characters each and all 33 suites stayed
# green, because `bpa_check_local_overlap()` needs `vnet` and the unit suite
# links `bpa_pool.c` alone, and no lab had ever created a pool over one of its
# own interface addresses (#242). This is that lab.
#
# It has to be a lab and not a unit test for the same reason the gap existed:
# the question is what the BOX does when a real interface really carries the
# address, and `vnet`'s interface table is the thing being read.
set -u

mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting /etc/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do
  sleep 1; n=$((n+1))
done
vppctl show version >/dev/null 2>&1 || {
  echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

# A real interface carrying a real address, because that is what the defence
# reads. A pg interface is enough: the check walks vnet's interface table and
# does not care how the packets would arrive.
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
vppctl set interface ip address pg0 200.150.0.20/28
echo "  the box's own address:"
vppctl show interface addr pg0 | sed 's/^/    /'

echo
echo "=========== 1. a pool whose public set contains it ==========="
# 200.150.0.16/28 is 200.150.0.16 through .31, so it contains .20.
REFUSAL=$(vppctl bpa pool clash inside 100.64.0.0/22 \
          outside 200.150.0.16/28 block 512 2>&1)
printf '%s\n' "$REFUSAL" | sed 's/^/    /'
CLASH_POOLS=$(vppctl show bpa pool | grep -c "^pool clash" || true)
echo "  pools named clash in the dataplane: $CLASH_POOLS"

echo
echo "=========== 2. a pool that clashes with nothing ==========="
# The control. Without it, a box that refused EVERY pool would satisfy the
# section above and this lab would be reporting a defence that does not exist.
CLEAN=$(vppctl bpa pool clean inside 100.64.4.0/22 \
        outside 200.150.9.0/28 block 512 2>&1)
printf '%s\n' "${CLEAN:-  (no output, which is what success looks like)}" | sed 's/^/    /'
CLEAN_POOLS=$(vppctl show bpa pool | grep -c "^pool clean" || true)
echo "  pools named clean in the dataplane: $CLEAN_POOLS"

echo
echo "=========== 3. the address configured AFTER the pool ==========="
# The half that cannot be refused: the pool exists first, and an operator
# numbers an interface into it later. The box cannot say no, so it has to say
# so loudly, in the place operators read.
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg1 up
vppctl clear logging
vppctl set interface ip address pg1 200.150.9.5/28
sleep 1
LOGGED=$(vppctl show logging)
printf '%s\n' "$LOGGED" | grep -i "200.150.9.5" | sed 's/^/    /'
echo "  (nothing above means the box said nothing)"

echo
echo "=========== 4. and an address that belongs to no pool is not reported ==========="
# The control for the second defence, and it is the one that keeps the message
# worth reading: a box that logged an error for every address configured
# anywhere would pass section 3 and teach operators to skip the line.
vppctl create packet-generator interface pg2 >/dev/null
vppctl set interface state pg2 up
vppctl clear logging
vppctl set interface ip address pg2 10.77.0.1/24
sleep 1
QUIET=$(vppctl show logging)
printf '%s\n' "$QUIET" | grep -i "10.77.0.1" | sed 's/^/    /'
echo "  (nothing above is what this section wants)"

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

expect_in "a pool whose public set holds a local address is refused" \
  "is configured on" "$REFUSAL"
expect_in "and the refusal names the address" "200.150.0.20" "$REFUSAL"
expect_in "and the interface it is configured on" "pg0" "$REFUSAL"
expect "and the pool does not exist" test "${CLASH_POOLS:-1}" -eq 0

expect "a pool that clashes with nothing is created" \
  test "${CLEAN_POOLS:-0}" -eq 1

expect_in "an address configured into an existing pool is reported" \
  "belongs to public pool" "$LOGGED"
expect_in "at error level, which is what show logging shows an operator" \
  "err" "$LOGGED"
expect_in "and it names the pool" "clean" "$LOGGED"

expect_not_in "an address belonging to no pool is not reported" \
  "belongs to public pool" "$QUIET"

verdict
