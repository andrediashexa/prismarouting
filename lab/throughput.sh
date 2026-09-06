#!/bin/sh
# What the CGNAT costs, per packet, in CPU clocks.
#
# READ THIS BEFORE QUOTING A NUMBER FROM IT.
#
# This does NOT answer "how many gigabits". That needs a NIC DPDK drives
# natively and this harness will produce it on one, but the machine it usually
# runs on has virtio and no IOMMU, so the bits-per-second here would measure
# the hypervisor.
#
# What it does answer is the part that does not depend on the NIC: how many CPU
# clocks a packet spends inside the translation nodes, measured by VPP itself
# rather than inferred. That number times the packet rate is the core count,
# and it is the number that decides whether the design can reach a target at
# all.
#
# The baseline matters as much as the figure. Every row is taken twice - with
# the CGNAT feature on the interface and without it - because "the box forwards
# N" and "the CGNAT costs N" are different questions and only the second is
# this project's to answer.
set -u
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

SECONDS_PER_RUN=${SECONDS_PER_RUN:-4}
CONF=${CONF:-/src/lab/startup-test.conf}
WORKERS=${WORKERS:-0}

start_vpp() {
  pkill -f "vpp -c" 2>/dev/null; sleep 1
  vpp -c "$CONF" > /tmp/vpp.log 2>&1 &
  n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
  vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }
  vppctl create packet-generator interface pg0 >/dev/null
  vppctl create packet-generator interface pg1 >/dev/null
  vppctl set interface state pg0 up; vppctl set interface state pg1 up
  vppctl set interface ip address pg0 100.64.3.254/22
  vppctl set interface ip address pg1 200.150.0.1/24
  # NOT sent to /dev/null, and the range does not overlap pg1's own address.
  # The first version did both: the pool was refused for colliding with
  # 200.150.0.1, nothing said so, every packet took a drop path, and the table
  # reported the cost of failing to translate as though it were the cost of
  # translating.
  vppctl bpa pool nb inside 100.64.0.0/22 outside 200.150.1.0/24 block 512
  vppctl show bpa pool | grep -q "pool nb" \
    || { echo "the pool was refused; nothing below would mean anything"; exit 1; }
}

# clocks per packet for one node, as VPP measured it
clocks() {
  vppctl show runtime | awk -v n="$1" '$1==n && $2=="active" {print $(NF-1)}' | head -1
}
# Vectors per call: how many packets VPP handled in one node invocation. A
# figure taken at a vector size of one is measuring call overhead, not the node.
vectors() {
  vppctl show runtime | awk -v n="$1" '$1==n && $2=="active" {print $NF}' | head -1
}

# run <name> <size> <src-spec> <sport-spec>
run() {
  vppctl packet-generator delete load >/dev/null 2>&1
  vppctl packet-generator new "name load node ip4-input interface pg0 \
      size $2-$2 data { UDP: $3 -> 8.8.8.8 UDP: $4 -> 53 incrementing 8 }" >/dev/null
  vppctl clear runtime >/dev/null
  vppctl packet-generator enable >/dev/null
  sleep "$SECONDS_PER_RUN"
  vppctl packet-generator disable >/dev/null
  vppctl packet-generator delete load >/dev/null 2>&1
}

CPUMHZ=$(awk -F: '/cpu MHz/{printf "%.0f", $2; exit}' /proc/cpuinfo)
echo "=============================================================="
echo " conditions"
echo "   $(awk -F: '/model name/{print $2; exit}' /proc/cpuinfo | sed 's/^ //')"
echo "   $CPUMHZ MHz, $(nproc) CPUs visible, workers configured: $WORKERS"
echo "   NIC: VPP packet generator, no hardware in the path"
echo "   $(date -u +%Y-%m-%dT%H:%M:%SZ), $SECONDS_PER_RUN s per row"

start_vpp
echo "   VPP $(vppctl show version | awk '{print $2}')"
echo "=============================================================="
echo
echo "=========== forwarding WITHOUT the CGNAT (the baseline) ==========="
printf "  %-8s %14s %14s\n" "bytes" "ip4-input" "ip4-lookup"
for sz in 64 128 512 1500; do
  run base "$sz" 100.64.0.5 40000
  printf "  %-8s %14s %14s\n" "$sz" "$(clocks ip4-input)" "$(clocks ip4-lookup)"
done

echo
echo "=========== with the CGNAT, one subscriber, one session ==========="
vppctl set interface bpa in2out pg0 >/dev/null
vppctl set interface bpa out2in pg1 >/dev/null
printf "  %-8s %14s %14s %10s\n" "bytes" "bpa-in2out" "ip4-input" "vec/call"
for sz in 64 128 512 1500; do
  run one "$sz" 100.64.0.5 40000
  printf "  %-8s %14s %14s %10s\n" "$sz" "$(clocks bpa-in2out)" \
    "$(clocks ip4-input)" "$(vectors bpa-in2out)"
  [ "$sz" = 64 ] && ONE64=$(clocks bpa-in2out)
done
SESS1=$(vppctl show bpa session | awk '/^total:/{print $2}')

echo
echo "=========== with the CGNAT, many sessions ==========="
# The session table is the thing that stops being free as it grows: an empty
# table measures a hash that always hits the same bucket.
printf "  %-22s %14s %10s\n" "sessions in the table" "bpa-in2out" "vec/call"
for spec in "100.64.0.5 40000-40511" "100.64.0.5-100.64.0.36 40000-40511"; do
  set -- $spec
  run many 64 "$1" "$2"
  S=$(vppctl show bpa session | awk '/total:/{print $2}')
  printf "  %-22s %14s %10s\n" "$S" "$(clocks bpa-in2out)" "$(vectors bpa-in2out)"
  MANY64=$(clocks bpa-in2out)
  SESSN=$S
done

echo
echo "=========== with two workers ==========="
# Reported, and its limits stated. The packet generator feeds one thread, so
# what this shows is the cost of the HANDOFF plus the node - not how the design
# scales. Scaling is a question about RSS spreading a real NIC's queues across
# workers, and that needs the real NIC.
CONF=/src/lab/startup-workers.conf
WORKERS=2
start_vpp
vppctl set interface bpa in2out pg0 >/dev/null
vppctl set interface bpa out2in pg1 >/dev/null
run w2 64 100.64.0.5 40000-40511
printf "  %-22s %14s %10s\n" "node" "clocks/packet" "vec/call"
for node in bpa-in2out-handoff bpa-in2out; do
  C=$(clocks "$node")
  [ -n "$C" ] && printf "  %-22s %14s %10s\n" "$node" "$C" "$(vectors $node)"
done
W2=$(clocks bpa-in2out)
vppctl show errors | grep -E "bpa-in2out" | sed 's/^/  /'

echo
echo "=========== where the packets went ==========="
# Printed, not assumed. A row measured on a drop path looks exactly like a row
# measured on the translation path until you read this.
vppctl show errors | grep -E "bpa-in2out|bpa-out2in" | sed 's/^/  /'

echo
echo "=========== what that means ==========="
echo "  A packet costs the CGNAT node ${ONE64:-?} clocks at 64 bytes."
if [ -n "${ONE64:-}" ] && [ -n "$CPUMHZ" ]; then
  PPS=$(awk -v c="$ONE64" -v mhz="$CPUMHZ" 'BEGIN{printf "%.2f", (mhz*1e6)/c/1e6}')
  CORES=$(awk -v p="$PPS" 'BEGIN{printf "%.0f", 148.81/p + 0.5}')
  echo "  At $CPUMHZ MHz that is ${PPS} Mpps per core, in this node alone."
  echo "  100 Gbps of 64-byte frames is 148.81 Mpps, so about $CORES cores"
  echo "  spent in bpa-in2out - before the driver, ip4-input or anything else."
fi

echo
echo "=========== VERDICT ==========="
# Not a pass/fail on the numbers - there is no target to compare against yet.
# What is asserted is that the harness MEASURED something, because a table of
# empty cells is the failure this would otherwise report as success.
expect "the baseline was measured" test -n "$(clocks ip4-input)"
expect "the CGNAT node was measured at 64 bytes" test -n "${ONE64:-}"
expect "the many-session row was measured" test -n "${MANY64:-}"
expect "and it really had more sessions than the single-session row" \
  test "${SESSN:-0}" -gt "${SESS1:-0}"
expect "the CPU clock is known, or the derived rate would be invented" \
  test -n "$CPUMHZ"
expect "the two-worker run measured the node as well as the handoff" \
  test -n "${W2:-}"
verdict
