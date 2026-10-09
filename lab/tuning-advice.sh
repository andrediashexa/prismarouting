#!/bin/sh
# `show system tuning advice` (#596) against a real dataplane: the facts the
# advice is built from, read back from VPP itself.
#
#   sh tools/lab.sh tuning-advice       two workers, two DPDK null ports of
#                                       two rx queues each
#   sh tools/lab.sh tuning-advice off   the same dataplane with no workers:
#                                       every check on what the workers do
#                                       MUST fail
#
# WHAT IS MEASURED HERE AND NOT IN test/test_tuneadvice.py. The unit suite
# feeds the parsers VPP's text as the .166 printed it; this lab hands them
# the text a running VPP prints, so a format the suite's fixtures got wrong
# - `show threads`' columns, `show runtime`'s polling rows, the RX Queues
# table of `show hardware-interfaces` on a DPDK port - goes red here. Each
# fact on the screen is compared with VPP asked directly: the worker CPUs
# with `show threads`, each card's queues and their workers with `show
# interface rx-placement`, each worker's loop rate with the header's own
# loops/sec, and the window is shown not to clear a counter (`show
# runtime`'s Time since the last clear keeps growing across it).
#
# What is NOT here: a hypervisor. The container's /sys is the build host's,
# whatever that is, so the screen's virtualization and CPU topology are that
# machine's and are printed, not asserted; the hookscript is asserted for
# its shape - the host's CPUs filled in from a pasted topology - and its
# mapping against the .166's own is the unit suite's.
#
# OFF-RED-AT-LEAST: 7
#
# A FLOOR, measured on the off run (#596, 2026-10-08): the checks on the
# workers - that there are two, their CPUs on the screen, their rows, their
# loop rates, the queues on them and the hookscript's worker lines - red
# with no worker to read, 7 of 14. Green in that run and rightly: the
# window's length, no counter cleared, the screen's width, the refusal and
# the sizing table, none of which needs a worker.
# See lab/bypass.sh for why it is a floor and not a target.
set -u
MODE=${1:-on}
. /src/lab/lib.sh   # startup_conf, wait_vpp, use_binary_api, vc
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /etc/prisma-routing \
         /var/log/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
HX="python3 /src/src/prismash/prismash.py"
export PRISMA_CONFIG=/etc/prisma-routing/prisma-routing.conf

# DPDK ON, two null ports and no hugepages (lab/transceiver.sh's dataplane),
# two rx queues a port, and two workers - or none, off.
sed 's/^ *plugin dpdk_plugin.so { disable }//' "$(startup_conf test)" \
  > /tmp/startup-tune.conf
printf 'dpdk {\n  no-hugetlb\n  no-pci\n  dev default {\n    num-rx-queues 2\n  }\n' \
  >> /tmp/startup-tune.conf
for n in 0 1; do printf '  vdev net_null%s\n' "$n" >> /tmp/startup-tune.conf; done
printf '}\n' >> /tmp/startup-tune.conf
if [ "$MODE" = off ]; then
  echo "  MODE=off: no workers. Every check on what the workers do MUST fail."
else
  printf 'cpu {\n  workers 2\n}\n' >> /tmp/startup-tune.conf
fi
export PRISMA_STARTUP_CONF=/tmp/startup-tune.conf
vpp -c /tmp/startup-tune.conf > /tmp/vpp.log 2>&1 &
wait_vpp
use_binary_api
for n in 0 1; do vppctl set interface state Ethernet$n up; done
sleep 2

echo "=========== 1. VPP, asked directly ==========="
THREADS=$(vc show threads)
printf '%s\n' "$THREADS" | sed 's/^/  /'
PLACE=$(vc show interface rx-placement)
printf '%s\n' "$PLACE" | sed 's/^/  /'
# Each worker's lcore, in worker order: the CPUs the screen must name.
WCPUS=$(printf '%s\n' "$THREADS" | awk '$2 ~ /^vpp_wk_/ {print $(NF-2)}' \
        | tr '\n' ' ' | sed 's/ $//')
echo "  worker CPUs, from show threads: ${WCPUS:-none}"
# `show runtime`'s Time - seconds since the counters were last cleared -
# before the screen's window, and after it.
time_now() { vc show runtime | awk '/^Time /{sub(",", "", $2); print $2; exit}'; }
T_BEFORE=$(time_now)

echo
echo "=========== 2. show system tuning advice seconds 3 ==========="
S0=$(date +%s)
SCREEN=$($HX -c "show system tuning advice seconds 3" 2>&1)
S1=$(date +%s)
printf '%s\n' "$SCREEN" | sed 's/^/  | /'
T_AFTER=$(time_now)
HDR=$(vc show runtime | awk '/^Thread [0-9]+ vpp_wk_/{w=$3} /^Time /{if (w) print w, $NF; w=""}')
echo "  VPP's own loops/sec per worker: $(printf '%s' "$HDR" | tr '\n' ';')"
echo "  show runtime's Time: $T_BEFORE before the window, $T_AFTER after"

# The screen's row for worker <n>: "  worker <n>  <cpu>  <loops>  <vpc>  <steal>"
row() { printf '%s\n' "$SCREEN" | awk -v w="$1" '$1 == "worker" && $2 == w'; }
# A loop rate as the screen says it (2.41M, 750k, 98) as a number.
num() { awk -v v="$1" 'BEGIN { m = 1; if (v ~ /M$/) m = 1e6; else if (v ~ /k$/) m = 1e3;
                               sub(/[Mk]$/, "", v); printf "%d", v * m }'; }
W0=$(row 0); W1=$(row 1)
L0=$(num "$(printf '%s' "$W0" | awk '{print $4}')")
L1=$(num "$(printf '%s' "$W1" | awk '{print $4}')")
H0=$(printf '%s\n' "$HDR" | awk '$1 == "vpp_wk_0" {printf "%d", $2}')
echo "  worker 0: the screen ${L0:-?} loops/s, VPP's header ${H0:-?}"
# Within a factor of two: the screen's rate is the calls' growth over its
# window, VPP's a smoothed 10 s figure; a parser reading the wrong column is
# off by orders of magnitude (a vectors count, a clock), not by a factor.
near() { [ "${1:-0}" -gt 0 ] && [ "${2:-0}" -gt 0 ] \
         && [ "$1" -le $(( $2 * 2 )) ] && [ "$2" -le $(( $1 * 2 )) ]; }

echo
echo "=========== 3. the host's topology, pasted ==========="
# A host of two nodes, four cores a node with HT, the NIC on node 1.
PASTE=$(python3 -c "
print('CPU NODE SOCKET CORE')
for c in range(16):
    print(c, (c // 4) % 2, (c // 4) % 2, c % 8)
print('nic-node 1')")
HOOK=$(printf 'show system tuning advice seconds 1 host-topology\n%s\n\nexit\n' \
       "$PASTE" | $HX 2>&1)
printf '%s\n' "$HOOK" | sed -n '/^#!\/bin\/sh/,/^exit 0/p' | sed 's/^/  | /'
REFUSED=$($HX -c "show system tuning advice seconds 61" 2>&1)
echo "  seconds 61: $REFUSED"
WIDE=$(printf '%s\n%s\n' "$SCREEN" "$HOOK" | awk 'length > 79' | head -3)
MAP=$(printf '%s\n' "$HOOK" | sed -n '/^map()/,/^}/p')
MAPLINES=$(printf '%s\n' "$MAP" | grep -c ') echo ')
HOSTLEFT=$(printf '%s\n' "$MAP" | grep -c 'HOST_')

echo
echo "=========== VERDICT ==========="
expect "the dataplane has two workers to read" test -n "$WCPUS"
expect_said "the screen names the workers' CPUs, as show threads does" \
  "workers $(printf '%s' "$WCPUS" | awk '{ if (NF == 2 && $2 == $1 + 1) print $1 "-" $2; else { gsub(" ", ","); print } }' | grep . || echo '<none read>')" \
  "$SCREEN"
expect "  and a row for each worker, on its CPU" \
  sh -c "[ -n \"$W0\" ] && [ -n \"$W1\" ] \
         && [ \"$(printf '%s' "$W0" | awk '{print $3}')\" = \"$(printf '%s' "$WCPUS" | awk '{print $1}')\" ]"
expect "each worker polls, measured from two readings (over 1,000 loops/s)" \
  sh -c "[ ${L0:-0} -gt 1000 ] && [ ${L1:-0} -gt 1000 ]"
expect "  and worker 0's rate is VPP's own, within a factor of two" \
  near "${L0:-0}" "${H0:-0}"
expect_said "each card's two queues on the two workers, as rx-placement says" \
  "2 rx queue(s) on worker(s) 0-1" "$(printf '%s\n' "$SCREEN" | sed -n '/card .*Ethernet0/,/NUMA/p')"
expect_in "the pasted host's CPUs fill in the hookscript's worker lines" \
  "VPP worker 1       -> " "$HOOK"
expect "  with no HOST_* left in its map" \
  sh -c "[ ${MAPLINES:-0} -gt 0 ] && [ ${HOSTLEFT:-1} = 0 ]"
expect_in "the window is the seconds asked" "measured over 3 s" "$SCREEN"
expect "  and took them (3 s or more)" test $(( S1 - S0 )) -ge 3
expect "no counter was cleared: show runtime's Time grew across the window" \
  sh -c "awk -v a='${T_BEFORE:-0}' -v b='${T_AFTER:-0}' 'BEGIN { exit !(b > a + 3) }'"
expect_in "the sizing table is the tuner's plan" "sizing: what the tuner makes" "$SCREEN"
expect_said "the host's kernel line isolates the VM's CPUs, on the pasted host" \
  'isolcpus=$CPUS' "$HOOK"
expect_in "a window past 60 s is refused" "a whole number from 1 to 60" "$REFUSED"
expect "every row fits 79 columns" test -z "$WIDE"
verdict
