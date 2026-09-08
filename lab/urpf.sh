#!/bin/sh
# uRPF on an inside interface: a spoofed source must not be translated, and
# therefore must not be attributed to a subscriber who did not send it.
#
#   sh tools/lab.sh urpf        with uRPF configured
#   sh tools/lab.sh urpf off    without it - the protection checks MUST fail
#
# The harm is measured before anything is turned on, because a lab that only
# shows the protected case proves nothing about what it protects against. In
# "off" mode uRPF is configured by no path at all, and six checks MUST fail.
set -u
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/vpp /var/log/hexarouting /etc/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
use_binary_api
HX="python3 /src/src/hexash/hexash.py"

# Only zebra. The VTY validates the candidate's routing half with FRR before
# applying anything, and a `urpf` line lives inside FRR's own `interface` block
# - so without a live FRR, section 6 would be measuring the refusal of an
# unvalidatable candidate rather than anything about uRPF.
mkdir -p /var/run/frr /etc/frr
printf 'hostname hexarouting\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr 2>/dev/null
/usr/lib/frr/zebra -d -A 127.0.0.1 -f /etc/frr/zebra.conf 2>/dev/null

vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

for i in 0 1 2; do
  vppctl create packet-generator interface pg$i >/dev/null
  vppctl set interface state pg$i up
done
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set interface ip address pg2 10.10.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1

vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
# BOTH are inside interfaces of the same CGNAT. That is the topology the check
# is about: pg0 faces the subscribers, and pg2 is another access link - another
# region, another aggregation switch - on the same box. The route to
# 100.64.0.0/22 points out pg0 and nowhere else.
vppctl set interface bpa in2out pg0
vppctl set interface bpa in2out pg2
vppctl set interface bpa out2in pg1

# 100.64.0.5 is subscriber 5 -> 200.150.0.17, block 1536-2047
# 100.64.0.9 is subscriber 9 -> 200.150.0.17, block 2048-2559

send() { # send <interface> <source> <sport>
  vppctl packet-generator new "name s limit 1 node ip4-input interface $1 \
      size 60-60 data { UDP: $2 -> 8.8.8.8 \
                        UDP: $3 -> 53 \
                        incrementing 8 }" >/dev/null
  vppctl clear trace >/dev/null
  vppctl trace add pg-input 5 >/dev/null
  vppctl packet-generator enable >/dev/null
  sleep 1
  vppctl packet-generator delete s >/dev/null 2>&1
}
trace() { vppctl show trace | grep -E "BPA in2out|urpf"; }
sess() { vppctl show bpa session | grep -o "total: [0-9]*" | awk '{print $2}'; }
# Empty when the counter does not exist yet, which is a value too: nothing has
# been dropped by the source check.
urpf_drops() { vppctl show errors | grep "uRPF Drop" | awk '{print $1}' | head -1; }

echo "=========== 1. the harm, with nothing turned on ==========="
echo "subscriber 5's address, injected on pg2, where its route does not point."
echo "There is no packet from that subscriber; there is a record saying so."
send pg2 100.64.0.5 12345
HARM=$(trace)
echo "$HARM" | sed 's/^/  /'
vppctl show bpa translation 200.150.0.17 1536 2>/dev/null | sed 's/^/  /'

echo
echo "=========== 2. turning it on ==========="
if [ "$MODE" = off ]; then
  echo "  MODE=off: uRPF is not configured. Every protection check below MUST"
  echo "  fail; one that passes was never testing anything."
else
  vppctl set urpf ip4 rx strict pg2
  vppctl set urpf ip4 rx strict pg0
  vppctl show bpa urpf | sed 's/^/  /'
  # A guard, not a check: if the feature did not go on, nothing below means
  # anything and reporting eight failures would hide that.
  vppctl show bpa urpf | grep -q "^pg2 strict" || {
    echo "uRPF did not go on; nothing below would mean anything"
    exit 1
  }
fi

echo
echo "=========== 3. the same packet, refused ==========="
BEFORE=$(sess)
send pg2 100.64.0.5 12346
PROTECTED=$(trace)
AFTER=$(sess)
echo "$PROTECTED" | sed 's/^/  /'
echo "  sessions before: $BEFORE, after: $AFTER"
DROPS=$(vppctl show errors | grep -i "urpf")
echo "$DROPS" | sed 's/^/  /'

echo
echo "=========== 4. and the subscriber's own traffic still passes ==========="
echo "the check that says uRPF did not simply break the inside interface:"
send pg0 100.64.0.5 12347
LEGIT=$(trace)
echo "$LEGIT" | sed 's/^/  /'

echo
echo "=========== 5. where it stops ==========="
echo "subscriber 9's address, injected on pg0 - the interface its route DOES"
echo "point out. Strict uRPF asks the FIB which way the source lies, and the"
echo "FIB says pg0, because every subscriber of this pool is out pg0. So this"
echo "one is translated, and the record will name subscriber 9."
echo "That is the boundary of what a source-route check can see. Catching it"
echo "needs the address bound to the subscriber who was given it, which is a"
echo "different mechanism (RADIUS or DHCP correlation, issue #21)."
send pg0 100.64.0.9 12348
NEIGHBOUR=$(trace)
echo "$NEIGHBOUR" | sed 's/^/  /'

echo
echo "=========== 6. through the VTY, and back out of it ==========="
VTY_ON=""; RUNNING=""; VTY_OFF=$(vppctl show bpa urpf)
if [ "$MODE" = off ]; then
  echo "  MODE=off: not configured by this path either."
else
  # pg1 is the outside interface and has had nothing done to it, so it is the
  # one interface here whose uRPF state can only come from this commit.
  $HX -c "configure" -c "interface pg1" -c "urpf loose" -c "exit" \
      -c "commit" 2>&1 | sed 's/^/  /'
  VTY_ON=$(vppctl show bpa urpf)
  # The whole block, not a fixed number of lines after the header. `grep -A2`
  # was cutting the answer off the moment the interface block grew a statement
  # - the address line pushed `urpf loose` out of the window and the assertion
  # failed on a box where uRPF was correctly configured.
  RUNNING=$($HX -c "show running-config" 2>/dev/null \
            | sed -n '/^interface pg1$/,/^ exit$/p')
  echo "$VTY_ON" | sed 's/^/  /'
  echo "  what the VTY reads back:"
  echo "$RUNNING" | sed 's/^/    /'
  $HX -c "configure" -c "interface pg1" -c "no urpf" -c "exit" \
      -c "commit" 2>&1 | sed 's/^/  /'
  VTY_OFF=$(vppctl show bpa urpf)
  echo "  after 'no urpf':"
  echo "$VTY_OFF" | sed 's/^/    /'
fi

echo
echo "=========== 7. a source in no pool is a different failure ==========="
echo "192.0.2.7 gets a route out pg0, so the source check is satisfied and the"
echo "packet reaches the translator - which has no pool for it. That is not an"
echo "attack, it is an address this box does not translate, and it MUST be"
echo "counted apart from a spoof or an operator cannot tell them apart."
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01 >/dev/null 2>&1
vppctl ip route add 192.0.2.0/24 via 100.64.3.253 pg0 >/dev/null 2>&1
URPF_BEFORE=$(urpf_drops)
send pg0 192.0.2.7 12350
NOPOOL=$(trace)
URPF_AFTER=$(urpf_drops)
echo "$NOPOOL" | sed 's/^/  /'
COUNTERS=$(vppctl show errors | grep -iE "uRPF Drop|no pool for source")
echo "$COUNTERS" | sed 's/^/  /'
echo "  uRPF drops before: ${URPF_BEFORE:-none}, after: ${URPF_AFTER:-none}"

echo
echo "=========== 8. off is off ==========="
vppctl set urpf ip4 rx off pg2 2>/dev/null
send pg2 100.64.0.5 12349
AGAIN=$(trace)
echo "$AGAIN" | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

# The precondition: without it, every "was not translated" below would pass on
# a box where that packet never had a chance of being translated.
expect_in "unprotected, a spoofed subscriber address IS translated" \
  "100.64.0.5:12345 -> 200.150.0.17:" "$HARM"

expect_not_in "with uRPF, the spoofed packet was not translated" \
  "100.64.0.5:12346" "$PROTECTED"
expect_in "it was dropped by the source check instead" \
  "ip4-rx-urpf-strict" "$PROTECTED"
expect_in "and the drop was counted" "uRPF Drop" "$DROPS"
expect "no session was created for it" test "$BEFORE" = "$AFTER"

expect_in "the subscriber's own traffic still passes" \
  "100.64.0.5:12347 -> 200.150.0.17:" "$LEGIT"

# Not a defect and not an oversight: asserted so that the limit is a test
# rather than a sentence in a document that nobody re-reads.
expect_in "a neighbour spoofed on the right interface IS still translated" \
  "100.64.0.9:12348 -> 200.150.0.17:" "$NEIGHBOUR"

expect_in "the VTY turned uRPF on" "pg1 loose" "$VTY_ON"
expect_in "and reports it in the running configuration" "urpf loose" "$RUNNING"
expect_not_in "'no urpf' turned it off on the device" "pg1" "$VTY_OFF"

# Acceptance criterion 3: the two reasons must not share a counter. One is an
# attack or a misconfiguration; the other is ordinary on a link this box shares
# with addresses it does not translate.
expect_in "a source in no pool reaches the translator and is refused there" \
  "no pool for source address" "$COUNTERS"
expect "and the source check did not claim it" \
  test "${URPF_BEFORE:-0}" = "${URPF_AFTER:-0}"

expect_in "turning it off restores the unprotected behaviour" \
  "100.64.0.5:12349 -> 200.150.0.17:" "$AGAIN"

verdict
