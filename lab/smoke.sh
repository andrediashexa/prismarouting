#!/bin/sh
# Sobe o VPP com o plugin e exercita a CLI de pools. Nao precisa de NIC.
set -e
mkdir -p /run/vpp /vpp_plugins
# o .so vive em /vpp_plugins, que e do container: reconstroi a cada execucao
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP nao subiu"; tail -20 /tmp/vpp.log; exit 1; }

vppctl bpa pool provedor-a inside 100.64.0.0/12 \
  outside 200.150.0.0/24 outside 45.170.16.0/24 outside 189.90.100.0/26 block 512
vppctl bpa pool provedor-b inside 100.64.0.0/16 \
  outside 177.85.32.0/24 block 2048 policy blocked
vppctl bpa pool provedor-c inside 100.64.0.0/16 inside 10.50.0.0/16 \
  outside 191.240.8.0/23 outside 191.240.10.0/24 block 1024
vppctl show bpa pool

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
P=$(vppctl show bpa pool)
expect_in "provedor-a was created" "pool provedor-a" "$P"
expect_in "adjacent prefixes were merged" "191.240.8.0+768" "$P"
expect_in "fragmented pool kept three ranges" "3 range(s)" "$P"
verdict
