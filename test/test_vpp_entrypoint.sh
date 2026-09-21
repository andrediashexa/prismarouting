#!/bin/sh
# appliance/vpp-entrypoint, driven with a VPP and a vppctl that are stubs.
#
# THE ORDERING IS THE PRODUCT HERE, and nothing else in this repository can
# see it. lab/ipfix-stop.sh proves what the drain DOES - the stopped box's
# block is not left open - but it supplies the ordering itself: its own script
# runs the drain and then sends the signal. Under systemd the ordering is
# systemd's documented contract. Under a container supervisor it is this
# wrapper's code, and a wrapper that forwards SIGTERM without waiting for the
# drain produces #217 again while every assertion in that lab stays green
# (#396, decision 4).
#
# So the assertion is written where it can fail: a stub `vpp` that records
# when it was signalled, a stub `vppctl` that takes time and records when it
# finished, and the requirement that the drain's line comes FIRST. No docker,
# no VPP, no root - which is why it is in make test rather than in a lab.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }

ENTRY=$REPO/appliance/vpp-entrypoint
[ -x "$ENTRY" ] || { echo "  FAIL: $ENTRY is not executable"; exit 1; }

# The drop-in the box ships, read rather than retyped - the same one the
# wrapper reads and the same one lab/ipfix-stop.sh reads.
DROPIN=$REPO/appliance/systemd/vpp-stop-drain.conf
[ -f "$DROPIN" ] || { echo "  FAIL: no $DROPIN to read the drain out of"; exit 1; }

mkdir -p "$WORK/bin"
# A VPP that runs until it is signalled, and says when it was. `trap` rather
# than a default TERM so the line is written before it goes.
cat > "$WORK/bin/vpp" <<'STUB'
#!/bin/sh
trap 'echo "vpp signalled" >> "$TRACE"; exit 0' TERM INT
echo "vpp started" >> "$TRACE"
while :; do sleep 0.2; done
STUB
# A vppctl that TAKES TIME. Without the sleep a backgrounded drain would
# usually finish first anyway, and the check would pass over the defect it
# exists to catch.
cat > "$WORK/bin/vppctl" <<'STUB'
#!/bin/sh
sleep 1
echo "drain done: $*" >> "$TRACE"
STUB
chmod +x "$WORK/bin/vpp" "$WORK/bin/vppctl"

# run_entry <script> -> writes $WORK/trace
run_entry() {
  TRACE=$WORK/trace; rm -f "$TRACE"; touch "$TRACE"
  export TRACE
  # THROUGH A LAUNCHER THAT RESTORES SIGINT, and the reason is POSIX rather
  # than taste: a command started in the background by a non-interactive
  # shell has SIGINT and SIGQUIT set to IGNORE, that disposition survives
  # exec, and a shell cannot trap a signal it was entered with ignored. So
  # `sh "$ENTRY" & kill -INT` hangs for ever - measured here, not read: the
  # INT case below sat until it was killed. A container's PID 1 is started
  # with the default disposition, so the ignore is the harness's artefact and
  # would otherwise make the INT assertion unable to fail OR to pass.
  PATH="$WORK/bin:$PATH" \
  VPP_BIN="$WORK/bin/vpp" VPP_CONF=/dev/null \
  VPP_STOP_DRAIN_CONF="$1" \
  python3 -c 'import os, signal, sys
signal.signal(signal.SIGINT, signal.SIG_DFL)
signal.signal(signal.SIGTERM, signal.SIG_DFL)
os.execv("/bin/sh", ["sh"] + sys.argv[1:])' "$ENTRY" >"$WORK/out" 2>"$WORK/err" &
  ENTRY_PID=$!
  # The wrapper has to have started VPP before the signal means anything.
  _i=0
  while [ "$_i" -lt 50 ] && ! grep -q "vpp started" "$TRACE" 2>/dev/null; do
    sleep 0.1; _i=$((_i + 1))
  done
}

echo "=== the drain finishes before VPP is signalled ==="
# The drop-in's own ExecStop, with the binary pointed at the stub: the wrapper
# reads the LINE from the file, so the file is copied with its path rewritten
# rather than a second line being written here.
sed "s#/usr/bin/vppctl#$WORK/bin/vppctl#" "$DROPIN" > "$WORK/dropin.conf"
run_entry "$WORK/dropin.conf"
kill -TERM "$ENTRY_PID" 2>/dev/null
wait "$ENTRY_PID" 2>/dev/null
rc=$?

order=$(grep -n "drain done\|vpp signalled" "$WORK/trace" | cut -d: -f2- | tr '\n' '|')
case "$order" in
  "drain done"*"vpp signalled"*)
    ok "the drain finished first: $order" ;;
  *"vpp signalled"*"drain done"*)
    bad "VPP was signalled BEFORE the drain finished, which is #217 exactly: $order" ;;
  *)
    bad "the wrapper produced neither order: '$order' (out: $(cat "$WORK/out"), err: $(cat "$WORK/err"))" ;;
esac

grep -q "drain done: bpa stop-drain" "$WORK/trace" \
  && ok "and it ran the drop-in's own command, not one typed here" \
  || bad "the wrapper ran '$(grep 'drain done' "$WORK/trace")' where the drop-in says bpa stop-drain"

[ "$rc" = 0 ] \
  && ok "and the wrapper exits with VPP's status: $rc" \
  || bad "the wrapper exited $rc where its VPP exited 0"

echo
echo "=== a second signal does not start a second drain ==="
# `docker stop` follows SIGTERM with SIGKILL, and an operator can send a
# second TERM in between. Two drains at once is two readers of the same block
# state; the latch is what stops it.
run_entry "$WORK/dropin.conf"
kill -TERM "$ENTRY_PID" 2>/dev/null
# THE SECOND ONE WHILE THE FIRST DRAIN IS STILL RUNNING, which is the only
# moment a second drain could start: the stub vppctl takes a second, so this
# lands in the middle of it. Two signals sent back to back do not reach the
# handler twice - the shell runs the trap after the current command, and the
# second TERM is coalesced into the same handling - so a test that sends them
# together passes whether the latch is there or not.
sleep 0.4
kill -TERM "$ENTRY_PID" 2>/dev/null
wait "$ENTRY_PID" 2>/dev/null
n=$(grep -c "drain done" "$WORK/trace")
[ "$n" = 1 ] \
  && ok "one drain for two signals" \
  || bad "$n drain(s) ran for two signals"

echo
echo "=== SIGINT is the same door ==="
# `docker stop` sends TERM, but a container run in the foreground and
# interrupted gets INT, and the retention log does not care which one arrived.
run_entry "$WORK/dropin.conf"
kill -INT "$ENTRY_PID" 2>/dev/null
wait "$ENTRY_PID" 2>/dev/null
case "$(grep -n "drain done\|vpp signalled" "$WORK/trace" | cut -d: -f2- | tr '\n' '|')" in
  "drain done"*"vpp signalled"*) ok "INT drains too, and in the same order" ;;
  *) bad "INT did not drain first: $(cat "$WORK/trace" | tr '\n' '|')" ;;
esac

echo
echo "=== a drop-in with no ExecStop is said out loud, and still starts ==="
# The opposite failure to guard: refusing to start the dataplane because a
# drop-in is missing turns a retention defect into an outage. It is loud and
# it keeps going, the same choice the ExecStop's own "-" makes.
printf '[Service]\n' > "$WORK/empty.conf"
run_entry "$WORK/empty.conf"
grep -q "vpp started" "$WORK/trace" \
  && ok "the dataplane started anyway" \
  || bad "a drop-in with no ExecStop stopped the dataplane from starting"
kill -TERM "$ENTRY_PID" 2>/dev/null
wait "$ENTRY_PID" 2>/dev/null
grep -q "no ExecStop" "$WORK/err" \
  && ok "and the wrapper said what will not be drained" \
  || bad "nothing was said about a drain that cannot happen: $(cat "$WORK/err")"
grep -q "drain done" "$WORK/trace" \
  && bad "a drain ran from a drop-in that carries none" \
  || ok "and no drain was invented"

echo
if [ "$fail" = 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "$fail FAILURES"; exit 1
