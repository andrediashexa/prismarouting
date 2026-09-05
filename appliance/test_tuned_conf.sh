#!/bin/sh
# Does the TUNED startup.conf get through VPP's parser?
#
# test_startup_conf.sh only exercises first-boot mode, because the container
# has neither hugepages nor isolcpus - and first boot is precisely the profile
# with NO cpu{}, no dpdk{} and no page-size default-hugepage. Which means the
# stanzas that only appear in production had never met a parser. Here we
# reserve real hugepages, render the full profile and start VPP with it.
set -u
FAILED=0
ok()  { echo "  $1"; }
bad() { echo "  FAIL: $1"; FAILED=$((FAILED + 1)); }

PAGES=${PAGES:-1400}            # 2 MB each: ~2.8 GB, covers a 2G main-heap
NR=/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages

[ -w "$NR" ] || { echo "SKIP: $NR is not writable (needs --privileged)"; exit 0; }
before=$(cat "$NR")
echo "$PAGES" > "$NR" 2>/dev/null || true
got=$(cat "$NR")
if [ "$got" -lt 1200 ]; then
  echo "SKIP: only got $got 2M hugepages (wanted $PAGES); host memory"
  echo "$before" > "$NR" 2>/dev/null || true
  exit 0
fi
ok "reserved $got 2M hugepages"
mkdir -p /dev/hugepages
mountpoint -q /dev/hugepages || mount -t hugetlbfs none /dev/hugepages 2>/dev/null

# --- synthetic topology that convinces the tuner the kernel is tuned
ROOT=$(mktemp -d)
mk() { mkdir -p "$(dirname "$1")"; printf '%s\n' "$2" > "$1"; }
for n in 0 1 2; do
  mk "$ROOT/sys/devices/system/cpu/cpu$n/topology/physical_package_id" 0
  mk "$ROOT/sys/devices/system/cpu/cpu$n/topology/core_id" "$n"
  mk "$ROOT/sys/devices/system/cpu/cpu$n/topology/thread_siblings_list" "$n"
done
mk "$ROOT/sys/devices/system/node/node0/cpulist" "0-2"
mk "$ROOT/sys/devices/system/cpu/isolated" "1-2"
mk "$ROOT/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages" 4
mk "$ROOT/sys/devices/pci0000:00/0000:3b:00.0/numa_node" 0
mkdir -p "$ROOT/sys/class/net/eth0"
ln -s "$ROOT/sys/devices/pci0000:00/0000:3b:00.0" "$ROOT/sys/class/net/eth0/device"

# Installs the way the product installs: the plugin has to be on the path VPP
# scans, otherwise "bpa pool" comes back as "unknown input" and the test blames
# the startup.conf for a problem that is not its own.
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /etc/hexarouting
sh /src/appliance/install.sh >/dev/null 2>&1 || bad "install.sh failed"
cp /src/appliance/etc/vpp-base.conf /etc/hexarouting/vpp-base.conf
python3 /src/appliance/hexarouting-tune --sysfs-root "$ROOT" --interface eth0 \
  --subscribers 10000 --write >/dev/null 2>&1 || bad "the tuner exited with an error"

CONF=/etc/vpp/startup.conf
grep -q "FIRST BOOT MODE" "$CONF" && bad "fell into first boot: the test tests nothing that way"
grep -q "corelist-workers 1-2" "$CONF" || bad "no pinning: wrong profile"
grep -q "dev 0000:3b:00.0"     "$CONF" || bad "no PCI device: wrong profile"
grep -q "num-rx-queues 2"      "$CONF" || bad "no per-worker queue: wrong profile"
grep -q "page-size default-hugepage" "$CONF" || bad "no hugepages: wrong profile"
ok "the generated profile is the full one (pinning + dpdk + hugepages)"

echo "=== does VPP accept that file? ==="
mkdir -p /var/log/vpp /run/vpp
(vpp -c "$CONF" > /tmp/vpp-tuned.log 2>&1 &) 
i=0
while [ $i -lt 30 ]; do
  vppctl -s /run/vpp/cli.sock show version >/dev/null 2>&1 && break
  i=$((i + 1)); sleep 1
done
if vppctl -s /run/vpp/cli.sock show version >/dev/null 2>&1; then
  ok "up in ${i}s: $(vppctl -s /run/vpp/cli.sock show version | head -1 | cut -c1-40)"
else
  bad "did not come up; log:"
  tail -20 /tmp/vpp-tuned.log | sed 's/^/    /'
fi

# A syntax error in startup.conf comes out as "unknown input" - and VPP can
# still come up ignoring the rest of the file, so being alive is not enough.
if grep -qi "unknown input\|unknown parameter\|parse error" /tmp/vpp-tuned.log; then
  bad "the parser refused something:"
  grep -i "unknown input\|unknown parameter\|parse error" /tmp/vpp-tuned.log | sed 's/^/    /'
else
  ok "no stanza refused by the parser"
fi

threads=$(vppctl -s /run/vpp/cli.sock show threads 2>/dev/null | grep -c "vpp_wk_")
if [ "${threads:-0}" -eq 2 ]; then
  ok "2 workers actually created and pinned"
else
  bad "expected 2 workers, VPP reports ${threads:-0}"
  vppctl -s /run/vpp/cli.sock show threads 2>/dev/null | sed 's/^/    /'
fi

vppctl -s /run/vpp/cli.sock bpa pool tuned inside 100.64.0.0/22 \
    outside 200.150.0.16/30 block 512 2>&1 | sed "s/^/    pool: /"
if vppctl -s /run/vpp/cli.sock show bpa pool 2>/dev/null | grep -q "pool tuned"; then
  ok "CGNAT pool created on the tuned profile"
else
  bad "could not create a pool on the tuned profile"
fi

pkill -f "vpp -c $CONF" 2>/dev/null
echo "$before" > "$NR" 2>/dev/null || true
rm -rf "$ROOT"

if [ "$FAILED" -eq 0 ]; then
  echo; echo "ALL TESTS PASSED"; exit 0
fi
echo; echo "$FAILED FAILURES"; exit 1
