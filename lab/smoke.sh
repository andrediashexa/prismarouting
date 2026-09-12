#!/bin/sh
# Starts VPP with the plugin and exercises the pool CLI. Needs no NIC.
set -e
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
mkdir -p /run/vpp /vpp_plugins
# the .so lives in /vpp_plugins, which belongs to the container: rebuilt each run
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

# Three pools with DISTINCT inside ranges. They used to share 100.64.0.0/16,
# which meant only the first ever translated anything: bpa_pool_by_inside()
# returns the first pool that claims an address, so the other two existed,
# accepted configuration and did nothing. Nobody noticed, because nothing said
# so - which is issue #73, and it was sitting in this file.
vppctl bpa pool provedor-a inside 100.64.0.0/12 \
  outside 200.150.0.0/24 outside 45.170.16.0/24 outside 189.90.100.0/26 block 512
vppctl bpa pool provedor-b inside 10.60.0.0/16 \
  outside 177.85.32.0/24 block 2048 policy blocked
vppctl bpa pool provedor-c inside 172.16.0.0/16 inside 10.50.0.0/16 \
  outside 191.240.8.0/23 outside 191.240.10.0/24 block 1024
vppctl show bpa pool

echo
echo "=========== and the overlap that used to be accepted ==========="
OVERLAP=$(vppctl bpa pool greedy inside 100.64.0.0/16 \
  outside 198.51.100.0/28 block 512 2>&1)
echo "  $OVERLAP"

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
P=$(vppctl show bpa pool)
expect_in "provedor-a was created" "pool provedor-a" "$P"
expect_in "adjacent prefixes were merged" "191.240.8.0+768" "$P"
expect_in "fragmented pool kept three ranges" "3 range(s)" "$P"
expect_in "a pool claiming a served range is refused" \
  "already served by pool" "$OVERLAP"
expect_in "and the refusal names the pool that already has it" \
  "provedor-a" "$OVERLAP"
verdict
