#!/bin/sh
# Does a translated packet LEAVE the box when VPP has workers? (probe)
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || { mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja; }
cd /src; . /src/lab/lib.sh; use_binary_api
CONF=${1:-workers}
case "$CONF" in
  workers) C=/src/lab/startup-workers.conf ;;
  *)       C=/src/lab/startup-test.conf ;;
esac
echo "===== using $C"
vpp -c "$C" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show threads | head -5
vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0
vppctl clear errors >/dev/null; vppctl clear interfaces >/dev/null
echo "===== feature arc"
vppctl show interface features pg0 | grep -A3 ip4-unicast | head -6
echo "===== graph"
vppctl show vlib graph bpa-in2out | head -6
echo "===== send one packet"
vppctl packet-generator new "name t limit 1 node ip4-input interface pg0 size 60-60 \
  data { UDP: 100.64.0.5 -> 8.8.8.8 UDP: 12345 -> 53 incrementing 8 }"
vppctl trace add pg-input 10
vppctl packet-generator enable-stream t
sleep 2
echo "===== interface counters"
vppctl show interface | head -20
echo "===== errors"
vppctl show errors | head -12
echo "===== trace"
vppctl show trace | tail -40
