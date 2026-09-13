#!/bin/sh
# Does the lab suite tell a defect apart from a machine that could not run it?
#
# A release run reported `handoff FAIL (exit 1)` over a VPP startup backtrace
# and `nat-behaviour+workers FAIL` over a capture in which every vppctl call
# said "connect: Connection refused". Neither lab's source had changed and both
# passed alone afterwards: the machine was at load 12 to 15 with three agents
# running labs beside the suite. The suite spent its verdict on the machine and
# said it in the words it uses for a product defect - the same hole
# lab/install-iso.sh's console_gave_up() exists to close on the console.
#
# Driven with STUB labs, not real ones. The state this is about is a VPP that
# did not start, and a test that had to arrange one would need a container, a
# dataplane and a machine under load - so it would never be part of `make
# test`, which is where a classifier like this has to be pinned. The real
# tools/run-all-labs.sh is run here, unmodified, against a repository whose
# lab.sh prints captures taken from the failures above.
set -u
fail=0
ok()  { echo "  $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)

d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
mkdir -p "$d/tools" "$d/lab"
cp "$REPO/tools/run-all-labs.sh" "$d/tools/"

# The suite builds the plugin before anything runs. Nothing here compiles.
cat > "$d/tools/build.sh" <<'EOF'
#!/bin/sh
exit 0
EOF

# The stub labs, keyed on the name and the mode the suite asks for. Each
# capture is the shape of a real one, cut down to the lines the classifier
# reads.
cat > "$d/tools/lab.sh" <<'EOF'
#!/bin/sh
case "$1 ${2:-}" in
  "aa-good ")
    echo "  [ ok ] something was measured"; echo "LAB PASSED"; exit 0 ;;

  "bb-broken ")
    echo "  [ ok ] something was measured"
    echo "  [FAIL] and something else was not (missing: a thing)"
    echo "LAB FAILED: 1 check(s)"; exit 1 ;;

  # handoff's capture: the dataplane paniced on the way up and the lab never
  # reached an assertion, so it exits non-zero with no verdict of any kind.
  "cc-panic ")
    echo "VPP did not start"
    echo "vpp[4502]: #4  vlib_worker_thread_barrier_release + 0x6b2"
    echo "vpp[4502]: #5  vlib_node_add_next_with_slot + 0xc32"
    echo "vpp[4502]: #6  ethernet_register_input_type + 0xc1"
    echo "vpp[4502]: #7  from /usr/lib/x86_64-linux-gnu/vpp_plugins/lldp_plugin.so"
    exit 1 ;;

  # nat-behaviour+workers' capture: vppctl never reached a socket, so every
  # value an assertion read was empty and every assertion failed. Not one
  # check went green, which is what separates this from a defect.
  "dd-refused ")
    echo "clib_socket_init: connect: Connection refused"
    echo "clib_socket_init: connect: Connection refused"
    echo "clib_socket_init: connect: Connection refused"
    echo "  [FAIL] the mapping test measured a real public port"
    echo "  [FAIL] one internal socket keeps one external port (missing: 200.150.0.17)"
    echo "LAB FAILED: 2 check(s)"; exit 1 ;;

  # The boundary, and it must land on the other side: VPP answered for a
  # while and then went. Green checks above the refusals mean something WAS
  # measured, so this is a failure until somebody looks at it.
  "ff-halfdead ")
    echo "  [ ok ] the pool was created"
    echo "  [ ok ] and the subscriber was translated"
    echo "clib_socket_init: connect: Connection refused"
    echo "  [FAIL] the session expired (missing: total: 0 sessions)"
    echo "LAB FAILED: 1 check(s)"; exit 1 ;;

  "ee-silent ")
    exit 0 ;;

  # igp's capture: the box cleared an adjacency, the reading was taken after
  # the hello interval, and the check says so rather than reporting a product
  # defect. It measured everything else first, which is what separates it from
  # a lab that could not run at all (#218).
  "gg-late ")
    echo "  [ ok ] the adjacency is up before it is cleared"
    echo "  [NOT MEASURED] clearing it took the adjacency down there and then"
    echo "         the reading was taken 5s after the change and the"
    echo "         window is 3s, so this says nothing either way"
    echo "LAB NOT MEASURED: 1 check(s)"; exit 2 ;;

  # An off mode that does what its header says: red with the feature gone.
  "vrf ")    echo "LAB PASSED"; exit 0 ;;
  "vrf off") echo "  [FAIL] the two tenants got different public addresses"
             echo "LAB FAILED: 1 check(s)"; exit 1 ;;

  # An off mode that still fails, and on FEWER checks than it used to. Its
  # verdict is unchanged, which is exactly what makes this invisible without
  # the floor: the lab lost assertions and went on reporting a pass (#151).
  "mss ")    echo "LAB PASSED"; exit 0 ;;
  "mss off") echo "  [FAIL] one of the four that used to go red"
             echo "LAB FAILED: 1 check(s)"; exit 1 ;;

  # An off mode that passes, which is the suite's own defect to catch.
  "audit ")    echo "LAB PASSED"; exit 0 ;;
  "audit off") echo "  [ ok ] the identity travelled"; echo "LAB PASSED"; exit 0 ;;

  # An off mode on a box whose VPP never came up. It fails every check it has,
  # which is exactly what the inverted verdict calls a pass.
  "urpf ")    echo "LAB PASSED"; exit 0 ;;
  "urpf off") echo "VPP did not start"; echo "LAB FAILED: 6 check(s)"; exit 1 ;;

  *) echo "the stub was asked for a lab it does not have: $*" >&2; exit 2 ;;
esac
EOF
chmod +x "$d/tools/build.sh" "$d/tools/lab.sh"

for n in aa-good bb-broken cc-panic dd-refused ee-silent ff-halfdead \
         gg-late vrf audit urpf; do
  echo '#!/bin/sh' > "$d/lab/$n.sh"
done
# The lab with a floor in its header, and a lab file is where the floor lives
# because that is where the number is true: the header of the lab whose off run
# it describes.
printf '#!/bin/sh\n# OFF-RED-AT-LEAST: 4\n' > "$d/lab/mss.sh"

out=$(sh "$d/tools/run-all-labs.sh" 2>&1); rc=$?

echo "=== a lab whose dataplane never came up is NO VPP, not FAIL ==="
case "$out" in
  *"cc-panic           NO VPP"*) ok "a startup backtrace is reported as NO VPP" ;;
  *) bad "the panic was not reported as NO VPP; got:
$out" ;;
esac
case "$out" in
  *"dd-refused         NO VPP"*)
    ok "and so is a capture in which nothing ever answered" ;;
  *) bad "the refused capture was not reported as NO VPP" ;;
esac
case "$out" in
  *"ff-halfdead        FAIL"*)
    ok "a lab that measured something first is still a FAIL" ;;
  *) bad "a half-dead run was excused as a machine problem" ;;
esac

echo "=== an off run that could not start VPP is not a pass ==="
# This is the one the inverted verdict gets wrong on its own: every check red
# is what an off run is supposed to look like.
case "$out" in
  *"urpf+off           NO VPP"*) ok "urpf+off is NO VPP, not a pass" ;;
  *) bad "an off run with no dataplane was counted as proving something" ;;
esac
case "$out" in
  *"vrf+off            pass"*) ok "an off run that really went red still passes" ;;
  *) bad "a genuine off failure stopped counting as a pass" ;;
esac
case "$out" in
  *"audit+off          FAIL - PASSED with the feature off"*)
    ok "and an off run that passed is the suite's own failure" ;;
  *) bad "an off mode that passed was not caught" ;;
esac

echo "=== an off run that lost assertions is caught by its own floor ==="
# The verdict is unchanged - still LAB FAILED, still red - so nothing about the
# pass or fail of the lab can see this. What sees it is the COUNT against the
# number its header declares.
case "$out" in
  *"mss+off            FAIL - 1 check(s) red, and its header floor is 4"*)
    ok "an off run red on fewer checks than its floor is a failure" ;;
  *) bad "a lab that lost three assertions still reported a pass; got:
$(printf '%s' "$out" | grep mss)" ;;
esac
case "$out" in
  *"lost assertions without losing its verdict"*)
    ok "and the report says what that means" ;;
  *) bad "nothing explained what a thinned off run is" ;;
esac
# And the line for a lab that meets its floor says what was compared, rather
# than claiming a header was read when it was not.
case "$out" in
  *"urpf+off           pass (6 check(s) red, floor 6)"*)
    ok "a lab that meets its floor says both numbers" ;;
  *"urpf+off           NO VPP"*)
    ok "urpf+off is NO VPP here, which its own case above already asserts" ;;
  *) bad "the floor was not reported beside the count; got:
$(printf '%s' "$out" | grep 'urpf+off')" ;;
esac

echo "=== a check whose timing the machine could not keep is its own state ==="
# Not a failure: the lab ran and measured everything else. Not a pass either:
# the thing it came to measure was not measured. This is the state that cost an
# investigation when lab/igp.sh reported it as a product defect in a release
# suite and passed in isolation (#218).
case "$out" in
  *"gg-late            NOT MEASURED"*)
    ok "a lab that could not take a timed reading is NOT MEASURED" ;;
  *) bad "a timed reading the machine missed was counted as something else" ;;
esac
case "$out" in
  *"the reading was taken 5s after the change"*)
    ok "and the report says which reading and by how much" ;;
  *) bad "nothing said which check could not be measured" ;;
esac
case "$out" in
  *"gg-late            FAIL"*)
    bad "a reading the machine missed was reported as a product defect" ;;
  *) ok "and it is not reported as a failure" ;;
esac

echo "=== the summary counts them apart, and the suite still exits non-zero ==="
case "$out" in
  *"pass=7  fail=4  notmeasured=1  novpp=3  ran=1"*) ok "the five states are counted apart" ;;
  *) bad "the summary line is not what was counted; got:
$(printf '%s' "$out" | tail -12)" ;;
esac
case "$out" in
  *"could not be run"*) ok "and the summary says what to check" ;;
  *) bad "nothing told the reader where to look" ;;
esac
[ "$rc" -ne 0 ] && ok "a suite with labs it could not run does not exit 0" \
                || bad "the suite exited 0 over labs it never ran"

# And NO VPP on its own, with nothing else wrong, must still be non-zero: a
# suite that ran nothing has cleared nothing.
rm -f "$d"/lab/*.sh
echo '#!/bin/sh' > "$d/lab/cc-panic.sh"
out2=$(sh "$d/tools/run-all-labs.sh" 2>&1); rc2=$?
case "$out2" in
  *"pass=0  fail=0  notmeasured=0  novpp=1  ran=0"*) ok "NO VPP alone is counted alone" ;;
  *) bad "NO VPP alone was miscounted; got:
$(printf '%s' "$out2" | tail -6)" ;;
esac
[ "$rc2" -ne 0 ] && ok "and is enough on its own to fail the suite" \
                 || bad "a suite that ran nothing at all exited 0"

# And NOT MEASURED on its own, with everything else green, must also be
# non-zero. A suite that reads "one check could not be taken" as success is the
# shape this file exists to find.
rm -f "$d"/lab/*.sh
echo '#!/bin/sh' > "$d/lab/gg-late.sh"
echo '#!/bin/sh' > "$d/lab/aa-good.sh"
out3=$(sh "$d/tools/run-all-labs.sh" 2>&1); rc3=$?
case "$out3" in
  *"pass=1  fail=0  notmeasured=1  novpp=0  ran=0"*)
    ok "NOT MEASURED beside a pass is counted apart from it" ;;
  *) bad "NOT MEASURED beside a pass was miscounted; got:
$(printf '%s' "$out3" | tail -8)" ;;
esac
case "$out3" in
  *"could not measure a check they came to measure"*)
    ok "and the summary says what it means and what to do" ;;
  *) bad "the summary said nothing about the check that was not taken" ;;
esac
[ "$rc3" -ne 0 ] && ok "and a suite with one unmeasured check does not exit 0" \
                 || bad "a suite that could not measure a check exited 0"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
