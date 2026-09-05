#!/bin/sh
# Dated configuration trail: the "proof" behind deterministic mode.
set -u
mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done

echo "=== initial state ==="
vppctl show bpa pool history | sed 's/^/  /'

echo
echo "=== operacoes de configuracao ==="
vppctl bpa pool provider-a inside 100.64.0.0/22 outside 200.150.0.0/24 outside 45.170.16.0/26 block 512 >/dev/null
echo "  created provider-a"
sleep 1
vppctl bpa pool provider-b inside 10.50.0.0/16 outside 177.85.32.0/24 block 2048 policy blocked >/dev/null
echo "  created provider-b"
sleep 1
vppctl bpa pool provider-a del
echo "  removido provider-a"
sleep 1
vppctl bpa pool provider-a inside 100.64.0.0/22 outside 200.150.0.0/24 block 1024 >/dev/null
echo "  recreated provider-a with a different block"

echo
echo "=========== A TRILHA ==========="
vppctl show bpa pool history | sed 's/^/  /'

echo
echo "=== the file on disk (append-only, with fsync) ==="
ls -l /var/log/hexarouting/config-history.log | sed 's/^/  /'
echo
echo "=== the question the trail answers ==="
echo "  'what block did provider-a use before the last change?'"
grep "add pool provider-a" /var/log/hexarouting/config-history.log \
  | head -1 | grep -oE "block [0-9]+" | sed 's/^/    /'
echo "  'e agora?'"
grep "add pool provider-a" /var/log/hexarouting/config-history.log \
  | tail -1 | grep -oE "block [0-9]+" | sed 's/^/    /'
echo
echo "  Without the trail, the current formula would answer with block 1024 for"
echo "  a date when the pool still used 512 - naming the wrong subscriber."

echo
echo "=== it also went to VPP's log (which can ship to remote syslog) ==="
vppctl show logging | grep -c "pool provedor" | sed 's/^/  linhas: /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
H=$(vppctl show bpa pool history)
expect_in "an add was recorded" "add pool provider-a" "$H"
expect_in "a del was recorded" "del pool provider-a" "$H"
expect_in "entries are dated in UTC" "Z add" "$H"
expect "the trail survives on disk" test -s /var/log/hexarouting/config-history.log
verdict
