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

start_frr zebra staticd bgpd ospfd ospf6d isisd
sleep 2

# Three, because section 4d needs one interface per value: a probe that types
# into the same interface it just typed into cannot tell FRR's answer from its
# own keystrokes, which is how a rewrite that does not exist got into the table
# once.
for _i in 9 8 7; do
  ip link add "dum$_i" type dummy 2>/dev/null
  ip link set "dum$_i" up 2>/dev/null
done

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
echo "=========== 4b. omitted because something ELSE is on ==========="
# The one entry in the table that is not about a value. `neighbor <x> activate`
# inside an ipv4-unicast address family is implied by `bgp default
# ipv4-unicast`, which is on unless somebody turns it off - so FRR accepts the
# line and writes nothing.
#
# This is the probe that would have saved a run: the check refused a BGP
# session in lab/routing.sh the first time it met one, because the ordinary way
# to configure a peer includes an activate line.
run "router bgp 65000" " neighbor 10.9.9.9 remote-as 65001"
run "router bgp 65000" " address-family ipv4 unicast" \
    "  neighbor 10.9.9.9 activate" "  exit-address-family"
expect "'neighbor activate' is not read back with the default on" \
       sh -c '! vtysh -c "show running-config" 2>/dev/null | grep -qF "neighbor 10.9.9.9 activate"'
# The control, in the same address family, so "not read back" is not simply
# what this box says about everything under address-family.
run "router bgp 65000" " address-family ipv4 unicast" \
    "  neighbor 10.9.9.9 soft-reconfiguration inbound" "  exit-address-family"
expect "while another statement in the same family is" \
       sh -c 'vtysh -c "show running-config" 2>/dev/null | grep -qF "neighbor 10.9.9.9 soft-reconfiguration inbound"'
run "router bgp 65000" " no neighbor 10.9.9.9"

# AND THE OTHER HALF, which is the assertion that matters. Turn the default
# off - which is what an ISP configuration does, so that a peer carries only
# the families it was given - and FRR stores the activate line like any other.
#
# Without this probe the table entry above was written UNCONDITIONAL, off the
# first half of the measurement alone, and it would have excused a genuinely
# dropped activate on every box that turns the default off: the box where an
# activate line is load-bearing rather than decorative, so the check would have
# been switched off exactly where it was needed.
run "no router bgp 65000"
run "router bgp 65000" " no bgp default ipv4-unicast" \
    " neighbor 10.9.9.9 remote-as 65001" \
    " address-family ipv4 unicast" "  neighbor 10.9.9.9 activate" \
    "  exit-address-family"
expect "and IS read back once that default is turned off" \
       sh -c 'vtysh -c "show running-config" 2>/dev/null | grep -qF "neighbor 10.9.9.9 activate"'
run "no router bgp 65000"

echo
echo "=========== 4c. IS-IS, and the control that tells omission from refusal ==="
# Probed because lab/igp.sh configures IS-IS and nothing had asked. Each
# statement is typed twice, at its default and at another value - a statement
# FRR REJECTED outright also fails to come back, and a table entry written off
# the absence alone would excuse the very thing this check exists for.
run "router isis lab" " net 49.0001.0000.0000.0001.00"
run "interface dum9" " ip router isis lab"
probe ABSENT     "metric-style wide" \
      "router isis lab" " metric-style narrow" -- \
      "router isis lab" " metric-style wide"
probe ROUNDTRIPS "metric-style narrow" \
      "router isis lab" " metric-style wide" -- \
      "router isis lab" " metric-style narrow"
probe ABSENT     "isis hello-interval 3" \
      "interface dum9" " no isis hello-interval" -- \
      "interface dum9" " isis hello-interval 3"
probe ROUNDTRIPS "isis hello-interval 5" \
      "interface dum9" " no isis hello-interval" -- \
      "interface dum9" " isis hello-interval 5"
probe ABSENT     "isis metric 10" \
      "interface dum9" " no isis metric" -- \
      "interface dum9" " isis metric 10"
probe ROUNDTRIPS "isis metric 77" \
      "interface dum9" " no isis metric" -- \
      "interface dum9" " isis metric 77"
run "interface dum9" " no ip router isis lab"
run "no router isis lab"

echo
echo "=========== 4d. an area number is NOT rewritten, and this refutes an entry ==="
# A table entry claiming `ip ospf area 0` comes back as `ip ospf area 0.0.0.0`
# was written and then removed, and the refutation is worth more than the entry
# would have been: the probe that produced it TYPED the dotted form before
# looking for it, so what it found was its own keystrokes.
#
# ONE INTERFACE PER VALUE, so nothing typed earlier can be what is read later.
# That is the whole correction: `probe` above types and then looks, which is
# fine when the two forms are the same string and is a trap when they are not.
run "interface dum9" " ip ospf area 0"
run "interface dum8" " ip ospf area 5"
run "interface dum7" " ip ospf area 0.0.0.7"
cfg | grep -E "ip ospf area" | sed 's/^/    /'
expect "an area number typed plain comes back plain" \
       sh -c 'vtysh -c "show running-config" 2>/dev/null | grep -qE "ip ospf area 0$"'
expect "a non-zero one too" \
       sh -c 'vtysh -c "show running-config" 2>/dev/null | grep -qE "ip ospf area 5$"'
expect "and a dotted one stays dotted" \
       sh -c 'vtysh -c "show running-config" 2>/dev/null | grep -qF "ip ospf area 0.0.0.7"'
for _i in 9 8 7; do run "interface dum$_i" " no ip ospf area"; done

echo
echo "=========== 4e. OSPFv3, whose statements had never been probed ==========="
# Probed because #187 put `router ospf6` and `ipv6 ospf6 ...` into the VTY's
# grammar, and a statement this VTY OFFERS that FRR does not read back is a
# commit the box refuses as dropped - on a configuration that worked. The
# grammar was written from this section rather than the other way round: the
# three statements that come back ABSENT at their default are the three the
# grammar does not offer.
#
# The controls are the point, exactly as in 4c. `ipv6 ospf6 hello-interval 10`
# failing to come back is equally consistent with ospf6d having refused it,
# and an entry written off that alone would switch the read-back check off for
# a statement FRR was throwing away for real.
run "router ospf6" " ospf6 router-id 10.0.0.1"
run "interface dum9" " ipv6 ospf6 area 0"
probe ABSENT     "ipv6 ospf6 hello-interval 10" \
      "interface dum9" " no ipv6 ospf6 hello-interval" -- \
      "interface dum9" " ipv6 ospf6 hello-interval 10"
probe ROUNDTRIPS "ipv6 ospf6 hello-interval 7" \
      "interface dum9" " no ipv6 ospf6 hello-interval" -- \
      "interface dum9" " ipv6 ospf6 hello-interval 7"
probe ABSENT     "ipv6 ospf6 dead-interval 40" \
      "interface dum9" " no ipv6 ospf6 dead-interval" -- \
      "interface dum9" " ipv6 ospf6 dead-interval 40"
probe ROUNDTRIPS "ipv6 ospf6 dead-interval 33" \
      "interface dum9" " no ipv6 ospf6 dead-interval" -- \
      "interface dum9" " ipv6 ospf6 dead-interval 33"
probe ABSENT     "ipv6 ospf6 priority 1" \
      "interface dum9" " no ipv6 ospf6 priority" -- \
      "interface dum9" " ipv6 ospf6 priority 1"
probe ROUNDTRIPS "ipv6 ospf6 priority 42" \
      "interface dum9" " no ipv6 ospf6 priority" -- \
      "interface dum9" " ipv6 ospf6 priority 42"
# And the ones the grammar DOES offer, every one of which has to roundtrip or
# it has no business being offered. `cost 10` and `network broadcast` are FRR's
# defaults and come back anyway, which is the same refutation section 1 makes
# for OSPFv2: omission is per statement, not per value.
probe ROUNDTRIPS "ipv6 ospf6 cost 10" \
      "interface dum9" " no ipv6 ospf6 cost" -- \
      "interface dum9" " ipv6 ospf6 cost 10"
probe ROUNDTRIPS "ipv6 ospf6 network point-to-point" \
      "interface dum9" " no ipv6 ospf6 network" -- \
      "interface dum9" " ipv6 ospf6 network point-to-point"
probe ROUNDTRIPS "ipv6 ospf6 passive" \
      "interface dum9" " no ipv6 ospf6 passive" -- \
      "interface dum9" " ipv6 ospf6 passive"
probe ROUNDTRIPS "ipv6 ospf6 mtu-ignore" \
      "interface dum9" " no ipv6 ospf6 mtu-ignore" -- \
      "interface dum9" " ipv6 ospf6 mtu-ignore"
probe ROUNDTRIPS "ipv6 ospf6 bfd" \
      "interface dum9" " no ipv6 ospf6 bfd" -- \
      "interface dum9" " ipv6 ospf6 bfd"
# The statement the grammar deliberately does NOT offer, and the measurement
# that says why. A comment claiming ospf6d REWRITES `interface <name> area
# <a>` into the interface stanza was written first, off a probe that had typed
# `ipv6 ospf6 area 0` into that same interface before looking for it - the
# section 4d trap, made again by the same hand within the hour. Measured
# properly, on an interface nothing else has touched:
#
#   vtysh: % Unknown command: interface dum8 area 0.0.0.0
#
# ospf6d has no such command in FRR 10.7.1. It is REFUSED, not rewritten, so
# offering it would offer a statement that fails mid-commit - and the read-back
# check is right to report its absence as a drop, which is why it must not be
# excused in FRR_OMITS_AT either.
run "interface dum8" " no ipv6 ospf6 area"
OLDSPELL=$(vtysh -c "configure terminal" -c "router ospf6" \
           -c "interface dum8 area 0.0.0.0" 2>&1 | tr -d '\r')
echo "  vtysh answered: $OLDSPELL" | head -2
expect "'interface <name> area' under router ospf6 is refused outright" \
       sh -c 'case "$0" in *"Unknown command"*) exit 0 ;; *) exit 1 ;; esac' "$OLDSPELL"
# Scoped to dum8's own stanza. The first version grepped the whole
# configuration, and dum9 has carried `ipv6 ospf6 area 0` since the top of
# this section - so the assertion was reading a line another probe had typed,
# which is the one mistake this file is most careful about.
expect "and it left nothing behind on that interface" \
       sh -c '! vtysh -c "show running-config" 2>/dev/null \
               | sed -n "/^interface dum8$/,/^exit$/p" | grep -q "ipv6 ospf6 area"'
# The control, in the same place: the spelling the grammar DOES offer is
# taken, so the refusal above is about that statement and not about ospf6d
# refusing everything.
probe ROUNDTRIPS "ipv6 ospf6 area 0.0.0.0" \
      "interface dum8" " no ipv6 ospf6 area" -- \
      "interface dum8" " ipv6 ospf6 area 0.0.0.0"
run "interface dum9" " no ipv6 ospf6 area"
run "no router ospf6"

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
LEFT=$(cfg | grep -cE "^ +(ip ospf|ipv6 ospf6|timers throttle|neighbor (10\.9\.9\.9|dum9))|^ip route 192\.0\.2\.0|^router ospf6" || true)
cfg | sed 's/^/  /'
expect "nothing a probe typed is left behind (found $LEFT)" test "${LEFT:-1}" -eq 0

echo
if [ "$fail" -eq 0 ]; then
  echo "LAB PASSED  ($pass checks, $(($(date +%s) - LAB_T0))s)"
else
  echo "LAB FAILED"
fi
exit "$fail"
