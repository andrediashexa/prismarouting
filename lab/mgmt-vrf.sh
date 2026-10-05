#!/bin/sh
# Management in a VRF of its own, while the main table carries the
# subscribers' default (#580).
#
#   sh tools/lab.sh mgmt-vrf        with `system / management / vrf mgmt`
#   sh tools/lab.sh mgmt-vrf off    the same VRF and the same routes, without
#                                   the statement - and the box is unreachable
#
# The box as .166 asked for it: its management card is a linux_cp pair in VRF
# mgmt (table 100), whose default is the management gateway; the main table's
# default goes to the upstream. An administrator at 198.51.100.7 - behind the
# management gateway, and reachable by no route of the main table - must reach
# SSH and the API (a TCP listener in the main table, as sshd and the API's
# socket are), SNMP (a UDP one), and the box must reach a release server there
# by the very function `system upgrade` downloads with.
#
# Off is the configuration without the statement, not a broken one: the VRF,
# the pair in it and both defaults are there, so what goes red is exactly what
# the statement adds. A main-table listener does not answer a connection that
# arrives on a VRF (l3mdev_accept 0), and what the box itself sends follows
# the main table's default, to the upstream, where the administrator is not.
#
# NOT here, and said in docs/routing.md: a dataplane restart (the pairs are
# made again by the product's unit, which this container does not run), a
# restart of the containers, and a reboot. The boot is stood in for by
# flushing the rules and the sysctls and running `prismash --load` on the
# saved file, which is what prisma-routing.service runs; the tuner's own half
# is test/test_apply_tuning.sh.
#
# OFF-RED-AT-LEAST: 15
#
# Measured on 2026-10-05: off is red on five checks in each of the three
# phases - TCP, UDP, the download, the route the box's own traffic takes, and
# the rules - and nothing else, since both defaults are there either way.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
. /src/lab/verdict.sh
MODE=${1:-on}
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing /var/log/prisma-routing
HX="python3 /src/src/prismash/prismash.py"
export PRISMA_CONFIG=/etc/prisma-routing/prisma-routing.conf
export PRISMA_ROLLBACK=/tmp/rollback PRISMA_TUNE_ENV=/tmp/tune.env
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

# The container's own default would answer for the main table; the box's is
# the one the VTY puts there.
ip route del default 2>/dev/null

# mg: the management gateway, with the administrator behind it on its lo.
# up: the upstream, which has no route to the administrator.
for n in mg up; do
  ip link add veth-$n-o type veth peer name veth-$n 2>/dev/null
  ip link set veth-$n up
  ip netns add $n 2>/dev/null
  ip link set veth-$n-o netns $n
  ip netns exec $n ip link set lo up
  ip netns exec $n ip link set veth-$n-o up
done
ip netns exec mg ip addr add 10.200.0.2/24 dev veth-mg-o
ip netns exec mg ip addr add 198.51.100.7/32 dev lo
ip netns exec up ip addr add 10.0.0.2/24 dev veth-up-o
ip netns exec mg sysctl -q -w net.ipv4.ip_forward=1
mkdir -p /tmp/rel && echo "a release, 580" > /tmp/rel/pkg
ip netns exec mg sh -c 'cd /tmp/rel && exec python3 -m http.server 8080 --bind 198.51.100.7' \
    > /tmp/http.log 2>&1 &

sysctl -p /src/appliance/etc/sysctl.conf >/dev/null 2>&1 || true
vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -25 /tmp/vpp.log; exit 1; }
use_binary_api
for n in mg up; do
  vppctl create host-interface name veth-$n >/dev/null
  vppctl set interface state host-veth-$n up
done

mkdir -p /var/run/frr /etc/frr
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr
start_frr

# The box's listeners, in the main table as sshd and the API's socket are:
# TCP 2222 stands for both, UDP 1161 for snmpd.
python3 - > /tmp/listen.log 2>&1 <<'PY' &
import socket, threading
def tcp():
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 2222)); s.listen(8)
    while True:
        c, _ = s.accept(); c.sendall(b"SSH-2.0-box580\n"); c.close()
def udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 1161))
    while True:
        d, a = s.recvfrom(512); s.sendto(b"snmp:" + d, a)
threading.Thread(target=tcp, daemon=True).start()
udp()
PY

echo "=== the box: management card in VRF mgmt, two defaults ==="
$HX -c "configure" -c "vrf mgmt table 100" \
    -c "interface host-veth-mg" -c "linux-name vppmg0" -c "vrf 100" \
    -c "ipv4 address 10.200.0.1/24" -c "exit" \
    -c "interface host-veth-up" -c "linux-name vppup0" \
    -c "ipv4 address 10.0.0.1/24" -c "exit" -c "commit" > /tmp/c1.log 2>&1
sed 's/^/  c1: /' /tmp/c1.log | tail -6
sleep 2
# The two defaults, then management into its VRF: three commits, so a
# refusal names the one it is about.
$HX -c "configure" -c "router static" -c "0.0.0.0/0 next-hop 10.0.0.2" \
    -c "exit" -c "commit" > /tmp/c2.log 2>&1
sed 's/^/  c2: /' /tmp/c2.log
$HX -c "configure" -c "vrf mgmt" -c "ip route 0.0.0.0/0 10.200.0.2" \
    -c "exit-vrf" -c "commit" > /tmp/c2b.log 2>&1
sed 's/^/  c2b: /' /tmp/c2b.log
if [ "$MODE" = off ]; then
  echo "  MODE=off: no 'system / management / vrf mgmt'"
else
  $HX -c "configure" -c "system" -c "management" -c "vrf mgmt" -c "exit" \
      -c "exit" -c "commit" > /tmp/c2c.log 2>&1
  sed 's/^/  c2c: /' /tmp/c2c.log
fi
sleep 3
echo "  vppmg0 in: $(ip -o link show vppmg0 | grep -o 'master [^ ]*')"
ip route show table 100 2>&1 | sed 's/^/  table 100: /'
ip route show default 2>&1 | sed 's/^/  main: /'
ip rule show 2>&1 | sed 's/^/  rule: /'
vppctl show ip fib table 100 0.0.0.0/0 2>&1 | head -4 | sed 's/^/  vpp 100: /'
sysctl net.ipv4.tcp_l3mdev_accept net.ipv4.udp_l3mdev_accept | sed 's/^/  /'
ss -ltnu 2>&1 | grep -E "2222|1161" | sed 's/^/  listen: /'

# The administrator's side in python rather than nc: which nc a container
# carries decides what `-s` and `-w` mean, and a check must not depend on it.
tcp_in()  { ip netns exec mg timeout 8 python3 -c "
import socket
s = socket.create_connection(('10.200.0.1', 2222), 5, ('198.51.100.7', 0))
print(s.recv(64).decode().strip())" 2>&1; }
udp_in()  { ip netns exec mg timeout 8 python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(5)
s.bind(('198.51.100.7', 0)); s.sendto(b'hello', ('10.200.0.1', 1161))
print(s.recv(64).decode())" 2>&1; }
fetch()   { timeout 20 python3 -c "
import sys; sys.path.insert(0, '/src/src/prismash'); import prismash
sha, n, why = prismash.download_to('http://198.51.100.7:8080/pkg', '/tmp/got', 5)
print('downloaded', n, 'bytes' if sha else why)" 2>&1; }

reach() {   # <when>
  W=$1
  expect_in "$W: the main table's default is the upstream's" "via 10.0.0.2" \
      "$(ip route show default 2>&1)"
  expect_in "$W: and VPP forwards the subscribers by it" "10.0.0.2" \
      "$(vppctl show ip fib table 0 0.0.0.0/0 2>&1)"
  expect_in "$W: management's default is in table 100" "via 10.200.0.2" \
      "$(ip route show table 100 default 2>&1)"
  expect_in "$W: SSH/API (TCP, main-table listener) answers over the VRF" \
      "SSH-2.0-box580" "$(tcp_in)"
  expect_in "$W: SNMP (UDP, main-table listener) answers over the VRF" \
      "snmp:hello" "$(udp_in)"
  expect_in "$W: system upgrade's download reaches a server behind mgmt" \
      "downloaded 15 bytes" "$(fetch)"
  expect_in "$W: what the box sends to 198.51.100.7 leaves by vppmg0" \
      "dev vppmg0" "$(ip route get 198.51.100.7 2>&1)"
  expect_in "$W: what the main table has a route for still goes by it" \
      "dev vppup0" "$(ip route get 10.0.0.2 2>&1)"
  expect_in "$W: the rules are there, below main's" "lookup 100" \
      "$(ip rule show 2>&1)"
}

echo; echo "=== after the commit ==="
reach commit

echo; echo "=== after another commit that does not touch management ==="
$HX -c "configure" -c "router static" -c "192.0.2.0/24 next-hop 10.0.0.2" \
    -c "exit" -c "commit" > /tmp/c3.log 2>&1
sed 's/^/  c3: /' /tmp/c3.log | tail -3
sleep 2
reach recommit

echo; echo "=== a boot whose tuner did not run: rules and sysctls gone, --load ==="
$HX -c "save" 2>&1 | tail -1 | sed 's/^/  /'
for f in "" -6; do
  ip $f rule del pref 31000 2>/dev/null; ip $f rule del pref 31001 2>/dev/null
done
sysctl -q -w net.ipv4.tcp_l3mdev_accept=0 net.ipv4.udp_l3mdev_accept=0
$HX --load "$PRISMA_CONFIG" > /tmp/load.log 2>&1
sed 's/^/  load: /' /tmp/load.log | tail -4
sleep 2
reach load

if [ "$MODE" != off ]; then
  echo; echo "=== 'no vrf' gives management back to the main table ==="
  $HX -c "configure" -c "system" -c "management" -c "no vrf" -c "exit" \
      -c "exit" -c "commit" > /tmp/c4.log 2>&1
  sed 's/^/  c4: /' /tmp/c4.log | tail -3
  expect_not_in "no vrf: the rules are gone" "lookup 100" "$(ip rule show 2>&1)"
  expect_in "no vrf: and l3mdev accept is off" "= 0" \
      "$(sysctl net.ipv4.tcp_l3mdev_accept 2>&1)"
fi

verdict
