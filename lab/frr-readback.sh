#!/bin/sh
. /src/lab/lib.sh   # start_frr
#
# WHAT FRR GIVES BACK, measured statement by statement.
#
# #197 is the one defect in this product an operator cannot work around,
# because it is about `commit` reporting success: frr-reload exits 0 having
# dropped a statement, and the box believes it. Every diagnosis an operator
# makes afterwards starts from a commit they were told worked.
#
# The general answer is a read-back check - a statement the candidate holds
# that FRR's running configuration does not contain afterwards is a failed
# commit, whatever the exit status said. It could not be written because of a
# MEASURED false positive: FRR leaves some statements out of its own read-back,
# so refusing on "typed but not read back" would refuse correct configurations,
# which is worse than the bug.
#
# THIS LAB IS THAT MEASUREMENT, kept honest by being run. It types one
# statement into a live FRR, reads the configuration back, and asserts which of
# four things happened. An FRR upgrade that changes one of these answers fails
# here rather than silently changing what the product refuses.
#
# The four shapes, and all four were found by measuring rather than by reading
# FRR's source:
#
#   ROUNDTRIPS  the statement comes back as it was typed. Most of them.
#   OMITTED     accepted, and left out of the read-back entirely.
#   REWRITTEN   accepted, and read back as DIFFERENT words meaning the same
#               thing: `passive-interface dum9` becomes `ip ospf passive`, in
#               the interface stanza rather than the router one.
#   NEGATED     the positive form is a default, so what gets written is the
#               `no` line when you turn it OFF, and nothing when you turn it on.
#
# The assumption this lab exists to refute is "FRR omits a statement whose
# value equals a default". It does not: omission is per STATEMENT, not per
# value. `ip ospf dead-interval 40` and `ip ospf cost 10` come back at their
# default while `ip ospf hello-interval 10` does not. So there is no table of
# FRR defaults to chase across versions - there is a short list of statements
# FRR drops, and this file is what produces it.
#
# EACH PROBE IS UNDONE BEFORE THE NEXT ONE RUNS. The first version of this
# measurement did not, and the results contaminated each other: `ip ospf
# cost 10` was probed before `ip ospf cost 77`, so the second answer was read
# out of a configuration the first had already written. A measurement whose
# result depends on the order it was taken in is not a measurement, and the
# final assertion below checks the undo actually worked.
set -u
LAB_T0=$(date +%s)

start_frr zebra staticd bgpd ospfd isisd
sleep 2

ip link add dum9 type dummy 2>/dev/null
ip link set dum9 up 2>/dev/null

fail=0
pass=0
expect() {  # <name> <cmd...>
  _n=$1; shift
  if "$@"; then echo "  [ ok ] $_n"; pass=$((pass + 1))
  else echo "  [FAIL] $_n"; fail=1; fi
}

# All the lines in ONE vtysh invocation, because a nested line only means
# anything inside the node the line above it opened.
run() {
  _args=""
  for l in "$@"; do _args="$_args -c \"$l\""; done
  eval vtysh -c \"configure terminal\" $_args >/dev/null 2>&1
}

cfg() { vtysh -c "show running-config" 2>/dev/null | tr -d '\r'; }

# <expected shape> <what to look for> <undo...> -- <type...>
#
# The expected shape is asserted, not printed. A probe that only printed what
# it saw would be a report, and a report does not fail when FRR changes.
probe() {
  _shape=$1; _want=$2; shift 2
  _undo=""
  while [ "$1" != "--" ]; do _undo="$_undo|$1"; shift; done
  shift
  run "$@"
  if cfg | grep -qF "$_want"; then _got=ROUNDTRIPS; else _got=ABSENT; fi
  case "$_shape" in
    ROUNDTRIPS) expect "$_want roundtrips" test "$_got" = ROUNDTRIPS ;;
    ABSENT)     expect "$_want is left out of the read-back" \
                       test "$_got" = ABSENT ;;
  esac
  _old="$IFS"; IFS='|'
  # shellcheck disable=SC2086
  set -- $_undo
  IFS="$_old"
  run "$@"
}

echo "=========== 0. which FRR is answering ==========="
vtysh -c "show version" 2>/dev/null | head -1 | sed 's/^/  /'

echo
echo "=========== 1. typed with a value that IS the FRR default ==========="
# The group that refutes the blocking assumption. If omission followed the
# value, every one of these would be ABSENT. Three of them are not.
probe ABSENT     "timers throttle spf 0 50 5000" \
      "router ospf" " no timers throttle spf" -- \
      "router ospf" " timers throttle spf 0 50 5000"
probe ABSENT     "ip ospf hello-interval 10" \
      "interface dum9" " no ip ospf hello-interval" -- \
      "interface dum9" " ip ospf hello-interval 10"
probe ROUNDTRIPS "ip ospf dead-interval 40" \
      "interface dum9" " no ip ospf dead-interval" -- \
      "interface dum9" " ip ospf dead-interval 40"
probe ABSENT     "ip ospf retransmit-interval 5" \
      "interface dum9" " no ip ospf retransmit-interval" -- \
      "interface dum9" " ip ospf retransmit-interval 5"
probe ROUNDTRIPS "ip ospf cost 10" \
      "interface dum9" " no ip ospf cost" -- \
      "interface dum9" " ip ospf cost 10"

echo
echo "=========== 2. the same statements with a NON-default value ==========="
# The control for section 1. Without it "ABSENT" would be consistent with FRR
# having refused the statement outright, and the lab would be asserting that
# the box cannot configure OSPF at all.
probe ROUNDTRIPS "ip ospf hello-interval 7" \
      "interface dum9" " no ip ospf hello-interval" -- \
      "interface dum9" " ip ospf hello-interval 7"
probe ROUNDTRIPS "ip ospf retransmit-interval 9" \
      "interface dum9" " no ip ospf retransmit-interval" -- \
      "interface dum9" " ip ospf retransmit-interval 9"
probe ROUNDTRIPS "ip ospf cost 77" \
      "interface dum9" " no ip ospf cost" -- \
      "interface dum9" " ip ospf cost 77"
probe ROUNDTRIPS "timers throttle spf 100 200 3000" \
      "router ospf" " no timers throttle spf" -- \
      "router ospf" " timers throttle spf 100 200 3000"

echo
echo "=========== 3. REWRITTEN: back as different words ==========="
# The category nobody had named, and the one a naive read-back check would
# refuse. Both of these are already known to this codebase from somewhere
# else - #205 found the first while fixing an OSPF warning, #208 the second
# while fixing route-map ordering - which is evidence the list is discoverable
# rather than open-ended.
run "router ospf" " passive-interface dum9"
expect "passive-interface is not read back as itself" \
       sh -c '! vtysh -c "show running-config" 2>/dev/null | grep -qF "passive-interface dum9"'
expect "it is read back as 'ip ospf passive'" \
       sh -c 'vtysh -c "show running-config" 2>/dev/null | grep -qF "ip ospf passive"'
run "router ospf" " no passive-interface dum9"

run "route-map RM permit 10" " continue 20"
expect "'continue 20' is not read back as itself" \
       sh -c '! vtysh -c "show running-config" 2>/dev/null | grep -qE "^ continue 20"'
expect "it is read back as 'on-match goto 20'" \
       sh -c 'vtysh -c "show running-config" 2>/dev/null | grep -qF "on-match goto 20"'
run "no route-map RM permit 10"

echo
echo "=========== 4. NEGATED: the 'no' is what gets written ==========="
# Found by accident, in the undo of section 1. `bgp default ipv4-unicast` is
# the default, so turning it ON writes nothing - and turning it OFF writes the
# `no` line. A read-back looking only for positives would call the first a
# dropped statement.
run "router bgp 65000" " bgp default ipv4-unicast"
expect "turning on a default writes nothing" \
       sh -c '! vtysh -c "show running-config" 2>/dev/null | grep -qE "^ bgp default ipv4-unicast"'
run "router bgp 65000" " no bgp default ipv4-unicast"
expect "turning it off writes the 'no' line" \
       sh -c 'vtysh -c "show running-config" 2>/dev/null | grep -qF "no bgp default ipv4-unicast"'
run "router bgp 65000" " bgp default ipv4-unicast"

echo
echo "=========== 5. the ordinary case, for contrast ==========="
# Most statements do come back. If these went ABSENT the whole read-back idea
# would be dead, and the lab would be saying so instead of asserting a list.
probe ROUNDTRIPS "ip route 192.0.2.0/24 10.9.9.1" \
      "no ip route 192.0.2.0/24 10.9.9.1" -- \
      "ip route 192.0.2.0/24 10.9.9.1"
probe ROUNDTRIPS "neighbor 10.9.9.9 remote-as 65001" \
      "router bgp 65000" " no neighbor 10.9.9.9" -- \
      "router bgp 65000" " neighbor 10.9.9.9 remote-as 65001"
probe ROUNDTRIPS "neighbor dum9 interface remote-as external" \
      "router bgp 65000" " no neighbor dum9" -- \
      "router bgp 65000" " neighbor dum9 interface" " neighbor dum9 remote-as external"

echo
echo "=========== 6. the undo worked ==========="
# The assertion that makes every one above mean something. If a probe's undo
# silently failed, the next probe read a configuration the previous one had
# written, and this file would be measuring its own order.
LEFT=$(cfg | grep -cE "^ (ip ospf|timers throttle|neighbor (10\.9\.9\.9|dum9))|^ip route 192\.0\.2\.0" || true)
cfg | sed 's/^/  /'
expect "nothing a probe typed is left behind (found $LEFT)" test "${LEFT:-1}" -eq 0

echo
if [ "$fail" -eq 0 ]; then
  echo "LAB PASSED  ($pass checks, $(($(date +%s) - LAB_T0))s)"
else
  echo "LAB FAILED"
fi
exit "$fail"
