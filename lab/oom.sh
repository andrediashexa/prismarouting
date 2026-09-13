#!/bin/sh
# The OOM killer's choice: the dataplane is out of it, the control plane is not.
#
#   sh tools/lab.sh oom        with the shipped drop-in's value on VPP
#   sh tools/lab.sh oom off    without it - every check in sections A and C
#                              that says VPP survived MUST fail, and section B
#                              must pass unchanged
#
# docs/appliance.md records the event this exists for: booting with 2 GB
# produced "Out of memory: Killed process (vpp)" with VPP holding 19 MB
# resident. The answer at the time was to raise the RAM floor, which leaves the
# victim exactly as exposed, and appliance/systemd/vpp-oom-protect.conf is the
# answer that does not (#172). Nothing had ever shown the kernel making that
# choice, either way.
#
# WHAT THIS LAB CAN HONESTLY ASSERT, and what it cannot. It runs real VPP,
# because a stand-in would prove that oom_score_adj works - which nobody
# doubts - instead of proving that a process holding this product's heap, its
# stats segment and its session tables is the one the kernel passes over. It
# cannot assert that systemd APPLIES the drop-in: there is no systemd in this
# container. That half is asserted on a real box, by the self-test's own check
# on /proc/<vpp pid>/oom_score_adj and by lab/install-iso.sh reading it back
# off a box it installed and booted.
#
# So the number is never typed here. It is read out of the shipped drop-in, and
# the lab also checks that install.sh puts that same file on vpp.service.d - a
# lab that repeated "-1000" would go on passing after the drop-in was deleted,
# which is precisely the failure it is meant to catch.
#
# HOW THE KERNEL IS MADE TO CHOOSE, without killing the lab's own shell. A
# nested cgroup v2 with its own memory.max holds VPP and one balloon, and
# nothing else: a memcg out-of-memory event only scans the tasks inside that
# cgroup, so the lab shell - which stays outside it - is never a candidate. The
# alternative, exhausting the container's whole memory, puts the shell reading
# the result in the same draw as the thing being measured.
#
# VPP is STARTED inside that cgroup rather than moved into it afterwards.
# cgroup v2 does not migrate a page's charge when its process moves, so a VPP
# moved in arrives owning nothing there: killing it would then free nothing,
# the balloon would hit the limit again immediately and be killed as the only
# candidate left, and both sides of the experiment would end with everything
# dead. Started inside, VPP's pages are charged where they are held, and the
# kernel's choice has the consequence it has on a real box.
#
# The limit is set from what VPP turned out to need, not from a constant:
# memory.max is VPP's own usage plus half its resident size, so the balloon can
# only ever grow to half of what VPP holds. That is what makes section B
# deterministic without anyone tilting it - the kernel scores by resident size,
# the balloon is the SMALLER of the two, and it is still VPP that gets chosen
# once VPP is selectable at all. Nothing here protects the balloon to force
# that outcome.
#
# OFF-RED-AT-LEAST: 6
#
# A FLOOR, not a target, and the number is measured rather than chosen: it is
# what this lab's off run went red on in the release suite of 2026-09-13.
# tools/run-all-labs.sh refuses an off run that goes red on fewer, because a
# lab that used to fail eleven checks and now fails one has lost ten assertions
# without losing its verdict - which is the defect this whole sweep exists to
# find. Gaining assertions is fine and needs no edit here; losing them is what
# has to be deliberate.
set -u
MODE=${1:-on}
CG=/sys/fs/cgroup
# A token in the cgroup names. dmesg is the host ring buffer, shared with
# every container on the machine, so a line about "oom-a" is not necessarily
# a line about this run.
TOKEN=$(od -An -tx1 -N4 /dev/urandom | tr -d " \n")
DROPIN=/src/appliance/systemd/vpp-oom-protect.conf

# A lab that cannot arrange its conditions must say so and fail, never pass
# quietly: "the kernel did not kill VPP" is the reading whether the protection
# worked or whether there was never a shortage to survive.
cannot_arrange() {
  echo
  echo "  the conditions for this measurement could not be arranged:"
  echo "  $*"
  echo
  echo "LAB FAILED: the mechanism was never exercised"
  exit 1
}

mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

echo "=========== 0. what the product ships ==========="
[ -f "$DROPIN" ] || cannot_arrange "$DROPIN does not exist, so there is no
  shipped value to apply. That is the product missing its protection, and this
  lab has nothing to measure."
# The [Service] section only. A number in the file's prose would otherwise be
# read as configuration.
PROTECTED=$(awk -F= '/^\[/{sec=$0} sec=="[Service]" && /^OOMScoreAdjust=/{v=$2} END{print v}' "$DROPIN")
case "${PROTECTED:-}" in
  -[0-9]*|[0-9]*) ;;
  *) cannot_arrange "$DROPIN names no OOMScoreAdjust in its [Service] section" ;;
esac
echo "  $DROPIN says OOMScoreAdjust=$PROTECTED"

# Where a box gets it from. Asserted by running the installer into an empty
# root rather than by grepping install.sh, because what matters is the file
# arriving under vpp.service.d - a drop-in installed anywhere else is a file
# systemd never reads.
rm -rf /tmp/fakeroot
# install.sh updates an existing Debian that already has FRR, and it appends to
# /etc/frr/daemons rather than creating the tree - so an empty root needs that
# directory to exist or the run stops there. Made here rather than worked
# around, because a run that stops before the drop-in is a run that would fail
# the assertion below for the wrong reason.
mkdir -p /tmp/fakeroot/etc/frr
DESTDIR=/tmp/fakeroot sh /src/appliance/install.sh > /tmp/install-sh.log 2>&1 \
  || { echo "  install.sh stopped early:"; tail -3 /tmp/install-sh.log | sed 's/^/    /'; }
SHIPPED=$(ls /tmp/fakeroot/etc/systemd/system/vpp.service.d/*.conf 2>/dev/null)
INSTALLED_ADJ=""
for f in $SHIPPED; do
  v=$(awk -F= '/^\[/{sec=$0} sec=="[Service]" && /^OOMScoreAdjust=/{v=$2} END{print v}' "$f")
  [ -n "$v" ] && INSTALLED_ADJ=$v
done
echo "  install.sh leaves under vpp.service.d:$(for f in $SHIPPED; do printf ' %s' "$(basename "$f")"; done)"
echo "  and the OOMScoreAdjust a box gets from it: ${INSTALLED_ADJ:-<none>}"

# 0 is not a number chosen for the negative case: it is what the kernel gives
# every process whose unit says nothing, which is what vpp.service was before
# the drop-in existed.
UNPROTECTED=0

# The balloon runs under a copy of python3 named for what it stands in for, so
# the kernel's own line names the victim in terms of this box: a control-plane
# process that grew. Its content is not a stand-in - it is genuinely the
# process allocating, and the kernel genuinely picks between it and VPP.
cp /usr/bin/python3 /tmp/control-plane
cat > /tmp/balloon.py <<'PY'
import os, sys, time
cg = sys.argv[1]
def kills():
    for line in open(cg + "/memory.events"):
        if line.startswith("oom_kill "):
            return int(line.split()[1])
    return 0
# It stops the moment the cgroup reports a kill. Without that it would go on
# allocating after the kernel had already made its choice, hit the limit again
# and be killed as the only candidate left - so both outcomes would end with
# both processes dead and the choice would be unreadable.
base = kills()
held = []
open("/tmp/balloon.pid.tmp", "w").write(str(os.getpid()))
os.rename("/tmp/balloon.pid.tmp", "/tmp/balloon.pid")
while kills() == base and len(held) < 4096:
    chunk = bytearray(2 * 1024 * 1024)
    for i in range(0, len(chunk), 4096):
        chunk[i] = 1          # touched, or it is a reservation and not memory
    held.append(chunk)
    time.sleep(0.02)
print("stopped after %d MB held; kills %d -> %d" % (len(held) * 2, base, kills()))
sys.stdout.flush()
time.sleep(600)
PY

echo
echo "=========== 0b. the cgroup this is measured in ==========="
[ "$(stat -fc %T $CG 2>/dev/null)" = cgroup2fs ] \
  || cannot_arrange "/sys/fs/cgroup is not cgroup v2, so there is no per-cgroup
  memory limit to put VPP and a balloon inside."
# The lab's own shell leaves the cgroup root first. A cgroup may not have both
# processes of its own and controllers enabled for its children, so without
# this the memory controller cannot be delegated at all - and the shell must be
# outside the limited cgroup anyway.
mkdir -p $CG/lab-shell
for p in $(cat $CG/cgroup.procs 2>/dev/null); do
  echo "$p" > $CG/lab-shell/cgroup.procs 2>/dev/null || true
done
echo "+memory" > $CG/cgroup.subtree_control 2>/dev/null
case "$(cat $CG/cgroup.subtree_control 2>/dev/null)" in
  *memory*) ;;
  *) cannot_arrange "the memory controller cannot be delegated to a child
  cgroup here. Without it nothing can be given a memory.max, and no shortage
  can be produced without taking the whole container down with it." ;;
esac
echo "  lab shell is in $(cat /proc/self/cgroup), outside anything limited"
echo "  delegated controllers: $(cat $CG/cgroup.subtree_control)"

# run_phase <name> <adjustment to put on VPP>
#
# Leaves: VPP_ALIVE, BAL_ALIVE, VICTIM (what the kernel said it killed),
# KILLS, VPP_RSS_KB, VPP_VM_KB, VPP_SCORE, LIMIT, BAL_RSS_KB.
run_phase() {
  PHASE=$1; ADJ=$2
  POT=$CG/oom-$TOKEN-$PHASE
  mkdir -p $POT
  echo max > $POT/memory.max
  # No swap, so a shortage is a shortage. With swap the balloon's pages would
  # be written out and the limit never reached.
  echo 0 > $POT/memory.swap.max 2>/dev/null || true
  # memory.oom.group stays 0: the whole point is WHICH task the kernel picks,
  # and 1 would kill every task in the cgroup and answer nothing.
  echo 0 > $POT/memory.oom.group 2>/dev/null || true

  rm -f /tmp/vpp.pid /tmp/balloon.pid /tmp/balloon.log
  # Started from inside the cgroup, for the reason in the header.
  setsid sh -c "echo \$\$ > $POT/cgroup.procs; echo \$\$ > /tmp/vpp.pid; \
                exec vpp -c /src/lab/startup-test.conf" > /tmp/vpp.log 2>&1 &
  n=0
  until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
  vppctl show version >/dev/null 2>&1 \
    || cannot_arrange "VPP did not start in phase $PHASE: $(tail -3 /tmp/vpp.log)"
  VPP=$(cat /tmp/vpp.pid)
  # A pool, so the process the kernel is choosing between is this product's
  # dataplane holding this product's state and not an empty VPP.
  vppctl bpa pool oomlab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512 >/dev/null 2>&1
  sleep 1

  VPP_RSS_KB=$(awk '/VmRSS/{print $2}' /proc/$VPP/status)
  VPP_VM_KB=$(awk '/VmSize/{print $2}' /proc/$VPP/status)
  USED=$(cat $POT/memory.current)
  # Half of what VPP holds resident. The balloon can therefore never be the
  # bigger of the two, which is what makes the unprotected outcome a statement
  # about selection rather than about arithmetic.
  HEADROOM=$(( VPP_RSS_KB * 1024 / 2 ))
  [ "$HEADROOM" -ge 33554432 ] \
    || cannot_arrange "VPP holds only ${VPP_RSS_KB} kB resident, which leaves
  no room for a balloon that is smaller than VPP and still big enough to reach
  the limit."
  LIMIT=$(( USED + HEADROOM ))
  echo "$LIMIT" > $POT/memory.max
  echo "$ADJ" > /proc/$VPP/oom_score_adj \
    || cannot_arrange "could not set oom_score_adj on VPP (pid $VPP)"
  VPP_SCORE=$(cat /proc/$VPP/oom_score)

  echo "  VPP pid $VPP ($(cat /proc/$VPP/comm)): VmRSS ${VPP_RSS_KB} kB, VmSize ${VPP_VM_KB} kB"
  echo "  cgroup $POT: memory.current $USED, memory.max $LIMIT"
  echo "  VPP oom_score_adj $(cat /proc/$VPP/oom_score_adj), oom_score $VPP_SCORE"

  setsid sh -c "echo \$\$ > $POT/cgroup.procs; \
                exec /tmp/control-plane /tmp/balloon.py $POT" > /tmp/balloon.log 2>&1 &
  n=0
  while [ ! -f /tmp/balloon.pid ] && [ "$n" -lt 50 ]; do sleep 0.2; n=$((n+1)); done
  [ -f /tmp/balloon.pid ] || cannot_arrange "the balloon never started"
  BAL=$(cat /tmp/balloon.pid)

  # Sampled while it grows, not read afterwards: the balloon is one of the two
  # candidates and its size is what makes the kernel's choice mean anything, so
  # reading it after it has been killed - when /proc has nothing left to read -
  # would let "the balloon stayed smaller than VPP" pass on a zero.
  KILLS=0
  BAL_RSS_KB=0
  n=0
  while [ "$n" -lt 90 ]; do
    _r=$(awk '/VmRSS/{print $2}' /proc/$BAL/status 2>/dev/null)
    [ -n "$_r" ] && BAL_RSS_KB=$_r
    KILLS=$(awk '/^oom_kill /{print $2}' $POT/memory.events)
    [ "${KILLS:-0}" -gt 0 ] && break
    sleep 0.5; n=$((n+1))
  done
  sleep 1
  [ "${KILLS:-0}" -gt 0 ] || cannot_arrange "the balloon grew for 45 s and the
  cgroup never reported an out-of-memory event. Nothing was chosen, so nothing
  here says anything about the choice."

  VPP_ALIVE=no; [ -d /proc/$VPP ] && VPP_ALIVE=yes
  BAL_ALIVE=no; [ -d /proc/$BAL ] && BAL_ALIVE=yes
  VPP_ANSWERS=no; vppctl show version >/dev/null 2>&1 && VPP_ANSWERS=yes
  VPP_POOL=$(vppctl show bpa pool 2>/dev/null | grep -c "oomlab" || true)

  # WHO the kernel chose, read from the two processes rather than from the
  # kernel log.
  #
  # The log line that names a memcg victim - "oom-kill:constraint=...,
  # oom_memcg=...,task=..." - is printed under a rate limiter of one per five
  # seconds for the whole machine. It was the first version of this check, and
  # on a busy runner it simply is not there: a run in which the kernel had
  # behaved exactly as asserted failed three checks because sixteen other
  # containers had used up the allowance. Meanwhile this cgroup holds two
  # processes and nothing else, and memory.events says one of them was killed,
  # so which one is a fact about /proc and not about a buffer shared with the
  # rest of the machine.
  if [ "$VPP_ALIVE" = yes ] && [ "$BAL_ALIVE" = no ]; then
    VICTIM=control-plane
  elif [ "$VPP_ALIVE" = no ] && [ "$BAL_ALIVE" = yes ]; then
    VICTIM=vpp_main
  else
    VICTIM="unclear (vpp alive=$VPP_ALIVE, balloon alive=$BAL_ALIVE)"
  fi
  # The kernel's own words, when the rate limiter let them through. Printed as
  # evidence and never asserted on, for the reason above.
  KLINE=$(dmesg 2>/dev/null | grep "oom_memcg=[^,]*/oom-$TOKEN-$PHASE," | tail -1)
  if [ "$BAL_ALIVE" = yes ]; then
    echo "  balloon pid $BAL is holding ${BAL_RSS_KB} kB"
  else
    echo "  balloon pid $BAL was killed (the kernel's line below says at what size)"
  fi
  echo "  oom events: $(tr '\n' ' ' < $POT/memory.events)"
  echo "  the kernel chose: $VICTIM"
  if [ -n "$KLINE" ]; then
    printf '%s\n' "$KLINE" | grep -o "task=[^,]*,pid=[0-9]*" | sed 's/^/    kernel: /'
  else
    echo "    kernel: its own line was dropped by the printk rate limiter"
  fi
  echo "  VPP alive $VPP_ALIVE, answers $VPP_ANSWERS, pool still there $VPP_POOL"
  [ "$BAL_ALIVE" = yes ] && echo "  balloon: $(head -1 /tmp/balloon.log 2>/dev/null)"

  kill -9 "$VPP" "$BAL" 2>/dev/null || true
  sleep 2
}

echo
echo "=========== A. with the protection the product ships ==========="
if [ "$MODE" = off ]; then
  echo "  MODE=off: the drop-in's value is NOT applied. VPP runs with the"
  echo "  $UNPROTECTED that every unit saying nothing gets, which is what"
  echo "  vpp.service was. Every check below that says VPP survived MUST fail."
  run_phase a "$UNPROTECTED"
else
  run_phase a "$PROTECTED"
fi
A_VICTIM=$VICTIM; A_KILLS=$KILLS
A_ANSWERS=$VPP_ANSWERS; A_POOL=$VPP_POOL
A_RSS=$VPP_RSS_KB; A_VM=$VPP_VM_KB; A_SCORE=$VPP_SCORE; A_LIMIT=$LIMIT

echo
echo "=========== B. without it, the same shortage ==========="
echo "  Nothing is done to the balloon here. It is still the SMALLER of the"
echo "  two, and the only thing that changed is that VPP is selectable."
run_phase b "$UNPROTECTED"
B_VICTIM=$VICTIM; B_KILLS=$KILLS
B_SCORE=$VPP_SCORE; B_RSS=$VPP_RSS_KB; B_BAL_RSS=$BAL_RSS_KB

echo
echo "=========== C. restored ==========="
if [ "$MODE" = off ]; then
  echo "  MODE=off: still not applied. The same checks as section A MUST fail."
  run_phase c "$UNPROTECTED"
else
  run_phase c "$PROTECTED"
fi
C_VICTIM=$VICTIM; C_KILLS=$KILLS
C_ANSWERS=$VPP_ANSWERS

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

# What a box gets is what this lab measured. Without these two, every check
# below would go on passing with the drop-in deleted from the tree.
expect "install.sh leaves drop-ins under vpp.service.d at all" test -n "$SHIPPED"
expect "and it carries the OOMScoreAdjust this lab applied" \
  test "${INSTALLED_ADJ:-none}" = "$PROTECTED"

# The precondition for section B meaning anything: the balloon must be the
# smaller of the two, or "VPP was chosen" would just be "VPP was bigger".
expect "the balloon stayed smaller than VPP" \
  test "${B_BAL_RSS:-0}" -lt "${B_RSS:-0}"

# Immunity rather than a lower weight, measured off the kernel instead of
# compared against a constant: a merely negative adjustment still leaves a
# score, and a score is a candidacy.
expect "protected, VPP scores 0 however much it holds" test "${A_SCORE:-1}" -eq 0
expect "unprotected, the same VPP scores as the memory it holds" \
  test "${B_SCORE:-0}" -gt 0

# A: the balloon dies and VPP lives.
expect "the kernel made exactly one choice" test "${A_KILLS:-0}" -eq 1
expect "and it chose the control-plane process, not VPP" \
  test "$A_VICTIM" = control-plane
expect "VPP is still answering afterwards" test "$A_ANSWERS" = yes
expect "with the pool it was carrying" test "${A_POOL:-0}" -ge 1

# B: with nothing else changed, the same kernel picks the dataplane. This is
# the section that must keep passing in "off" mode.
expect "unprotected, the kernel still made exactly one choice" \
  test "${B_KILLS:-0}" -eq 1
expect "and this time it chose VPP, leaving the process that caused the shortage" \
  test "$B_VICTIM" = vpp_main

# C: restored.
expect "restored, the kernel made exactly one choice" \
  test "${C_KILLS:-0}" -eq 1
expect "and chose the control-plane process again" test "$C_VICTIM" = control-plane
expect "and VPP is answering" test "$C_ANSWERS" = yes

# The premise underneath "session tables raise VPP's badness". VPP maps far
# more than it holds - the heap and the session tables are reservations - and
# if the kernel scored by what is mapped, a process with VmSize many times the
# limit would be at the top of every list. It is not: unprotected, the score is
# the resident share of the limit. Measured here rather than argued, because
# docs/appliance.md sizes the session-table default on the opposite belief.
MAPPED_OVER_LIMIT=$(( A_VM * 1024 / A_LIMIT ))
RESIDENT_SHARE=$(( A_RSS * 1000 / (A_LIMIT / 1024) ))
echo "  (VPP maps ${MAPPED_OVER_LIMIT}x the limit and holds $((RESIDENT_SHARE / 10))% of it;"
echo "   unprotected its score is $B_SCORE out of 1000)"
expect "VPP maps several times the limit" test "$MAPPED_OVER_LIMIT" -ge 4
expect "and the kernel scores it by what it holds, not by what it maps" \
  test "$B_SCORE" -lt 1000
expect "the score is the resident share" \
  sh -c "test $(( B_SCORE > RESIDENT_SHARE ? B_SCORE - RESIDENT_SHARE : RESIDENT_SHARE - B_SCORE )) -le 150"

verdict
