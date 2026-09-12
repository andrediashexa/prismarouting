#!/bin/sh
# SPIKE (#170): can accel-ppp-ng terminate a PPPoE session on stock VPP 26.06,
# and does BPA then translate the subscriber that comes out of it?
#
#   sh tools/lab.sh bng            everything, with bpa-in2out enabled
#   sh tools/lab.sh bng off        the same run with the feature never enabled,
#                                  which is how the translation check is shown
#                                  to be able to fail
#
# This is evidence, not a feature. Nothing here is a commitment to ship a BNG,
# and nothing here is wired into the product: no hexash statement creates a
# PPPoE session and none is proposed.
#
# What makes this lab worth anything is that the control plane does the control
# plane's job. accel-pppd runs discovery, LCP, PAP and IPCP on the interface
# VPP's pppoe plugin punts to, and it is accel-pppd - over the binary API,
# visible in "show api clients" - that calls pppoe_add_del_session. No step of
# that is performed by this script with vppctl. A lab that typed
# "create pppoe session" by hand would prove the plugin's data path works and
# would prove nothing whatever about a BNG, which is the shape of #99, #168,
# #182 and #184.
#
# The one thing the lab does do by hand is "set interface bpa in2out" on the
# session interface, because that is precisely the missing product step #170
# asks about: today nothing in this repository enables a feature on an
# interface that appears at three in the morning when a subscriber dials in.
set -u

OFF=${1:-}

mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting /etc/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

# The fork #170 names, pinned. accel-ppp-ng is the VyOS-driven fork; upstream
# accel-ppp has no VPP support at all.
ACCEL_REPO=https://github.com/accel-ppp/accel-ppp-ng.git
ACCEL_SHA=cb69ccc686ee089e4a1e0de946b32991edb084e0

echo "=========== 1. WHAT IT COSTS TO BUILD accel-ppp-ng ==========="
# Recorded rather than hidden: "what it costs to build" is half of what #170
# wanted to know, and a lab that quietly installs a page of -dev packages has
# answered the question and thrown the answer away.
DEPS="libssl-dev libpcre2-dev libnl-3-dev libnl-route-3-dev libnl-genl-3-dev"
LABDEPS="ppp pppoe"
echo "  build dependencies on top of hexarouting-dev:26.06:"
echo "    $DEPS"
echo "  and for the lab's own PPPoE client, which is not a product dependency:"
echo "    $LABDEPS"
echo "  already present in the image: cmake, gcc, git, vpp-dev (headers, vapi)"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $DEPS $LABDEPS \
  > /tmp/apt.log 2>&1 || { echo "  apt failed"; tail -5 /tmp/apt.log; }

[ -d /opt/accel-ppp-ng ] || git clone -q "$ACCEL_REPO" /opt/accel-ppp-ng
(cd /opt/accel-ppp-ng && git checkout -q "$ACCEL_SHA" 2>/dev/null)
echo "  accel-ppp-ng at $ACCEL_SHA"

echo
echo "=========== 2. IT DOES NOT BUILD AGAINST STOCK VPP 26.06 ==========="
# The claim being measured: the VyOS fork's VPP hook is written against a VPP
# that carries the vyos-vpp-patches series, not against what vpp-plugin-core
# ships. The build error is the cheapest possible proof of that.
mkdir -p /opt/accel-ppp-ng/build
(cd /opt/accel-ppp-ng/build && cmake -DBUILD_DRIVER=FALSE -DRADIUS=TRUE \
   -DSHAPER=TRUE -DNETSNMP=FALSE -DLOG_PGSQL=FALSE -DHAVE_VPP=TRUE \
   -DHAVE_SESSION_HOOKS=TRUE -DCMAKE_INSTALL_PREFIX=/usr \
   -DCMAKE_BUILD_TYPE=Release .. > /tmp/cmake.log 2>&1) \
  || { echo "cmake failed"; tail -20 /tmp/cmake.log; exit 1; }

(cd /opt/accel-ppp-ng/build && make vyos_vpp) > /tmp/build-stock.log 2>&1
STOCK_RC=$?
echo "  make vyos_vpp exit status: $STOCK_RC"
grep -E "error:" /tmp/build-stock.log | head -4 | sed 's/^/  /'
echo "  and what the API actually offers, from the box's own pppoe.api.json:"
python3 - <<'PY' | sed 's/^/    /'
import json
d = json.load(open("/usr/share/vpp/api/plugins/pppoe.api.json"))
for m in d["messages"]:
    if m[0] == "pppoe_add_del_session":
        print(" ".join(f[1] for f in m[1:] if isinstance(f, list)))
PY

echo
echo "=========== 3. THE DELTA THAT MAKES IT BUILD ==========="
# Two assignments, both of a field that exists only in the patched plugin.
# disable_fib=1 tells the VyOS pppoe plugin NOT to install the client route;
# accel-ppp installs it itself over ip_route_add_del a moment later. Dropping
# the assignment means stock VPP installs its own reverse route from the
# client_ip in the message - which accel-ppp leaves at 0.0.0.0 - so the box
# ends up with a 0.0.0.0/32 entry pointing at a session interface. Harmless
# here, and recorded below, because it is exactly the sort of thing that is
# not harmless on a box carrying traffic.
sed -i 's|^\treq->payload.disable_fib = 1;|\t/* dropped: stock VPP 26.06 pppoe.api has no disable_fib */|' \
  /opt/accel-ppp-ng/accel-pppd/session_hooks/vyos_vpp/vpppoe.c
grep -n "disable_fib" /opt/accel-ppp-ng/accel-pppd/session_hooks/vyos_vpp/vpppoe.c \
  | sed 's/^/  /'
(cd /opt/accel-ppp-ng/build && make -j"$(nproc)" && make install) \
  > /tmp/build-patched.log 2>&1
PATCHED_RC=$?
echo "  make after the delta: exit status $PATCHED_RC"
ls -1 /usr/lib64/accel-ppp/libvyos_vpp.so /usr/sbin/accel-pppd 2>&1 | sed 's/^/  /'

echo
echo "=========== 4. THE ETHERTYPE QUESTION ==========="
# #170 says linux_cp and the pppoe plugin are mutually exclusive box-wide and
# that (a') MUST NOT be done. Both plugins are loaded below, together, for the
# whole of this lab - so whether that is survivable is not argued here, it is
# demonstrated by everything that follows working with linux_cp resident.
echo "  pppoe_init() registers 0x8863 and 0x8864 unconditionally at plugin"
echo "  init; lcp_ethertype_enable() has no caller in linux-cp and is reached"
echo "  only from the API, so linux_cp takes an ethertype only when an"
echo "  operator asks it to. Source for both is quoted in the report."

echo
echo "=========== 5. TOPOLOGY ==========="
# One access interface with a bridge behind it, and one subscriber netns per
# client. The pppoe plugin's link table is keyed on client MAC ALONE with no
# access-interface dimension, so many subscribers on one access port is the
# shape it can actually carry; many access ports is the shape it cannot.
build_topology() {
  ip link add br0 type bridge 2>/dev/null
  ip link set br0 up
  ip link add acc0 type veth peer name accb 2>/dev/null
  ip link set acc0 up
  ip link set accb master br0
  ip link set accb up
  ip link add wan0 type veth peer name wanp 2>/dev/null
  ip link set wan0 up
  ip link set wanp up
  ip addr add 200.150.0.2/24 dev wanp 2>/dev/null
  i=1
  while [ "$i" -le "$1" ]; do
    ip netns add "s$i" 2>/dev/null
    ip link add "cli$i" type veth peer name "br$i" 2>/dev/null
    ip link set "br$i" master br0
    ip link set "br$i" up
    ip link set "cli$i" netns "s$i" 2>/dev/null
    ip netns exec "s$i" ip link set "cli$i" address "02:aa:00:00:00:$(printf %02x "$i")"
    ip netns exec "s$i" ip link set "cli$i" up
    ip netns exec "s$i" ip link set lo up
    add_subscriber_secret "$i"
    sed -e "s/cliN/cli$i/" -e "s/subN/sub$i/" /tmp/peer.tmpl > "/etc/ppp/peers/sub$i"
    i=$(( i + 1 ))
  done
}

# BOTH files, and not by accident. accel-pppd's chap-secrets module answers the
# PAP request from /etc/ppp/chap-secrets; the pppd on the other end of the wire
# looks the same credential up in /etc/ppp/pap-secrets. With only the first of
# them written, accel-ppp offers PAP, the client has nothing to answer with, and
# the session dies after PADS with a log that shows discovery succeeding.
add_subscriber_secret() {
  printf '"sub%s" * "s3cr3t" *\n' "$1" >> /etc/ppp/chap-secrets
  printf '"sub%s" * "s3cr3t" *\n' "$1" >> /etc/ppp/pap-secrets
}

mkdir -p /etc/ppp/peers
: > /etc/ppp/chap-secrets
: > /etc/ppp/pap-secrets
cat > /tmp/peer.tmpl <<'EOF'
plugin rp-pppoe.so
nic-cliN
user subN
noauth
noipdefault
defaultroute
persist
maxfail 0
holdoff 2
lcp-echo-interval 30
lcp-echo-failure 4
nodetach
EOF
build_topology 3
echo "  br0: acc0 (VPP) + cli1..3 (one subscriber netns each)"
echo "  wan0/wanp: the upstream, where the translated packet is captured"

cat > /etc/accel-ppp.conf <<'EOF'
[modules]
log_file
vyos_vpp
pppoe
auth_pap
auth_chap_md5
chap-secrets
ippool

[core]
log-error=/tmp/accel-error.log
thread-count=1

[common]
single-session=replace

[ppp]
verbose=1
mtu=1492
mru=1492
ipv4=require
ipv6=deny
lcp-echo-interval=30
lcp-echo-failure=3

[pppoe]
verbose=1
ac-name=hexarouting
interface=vppcp,vpp-cp=enable

[chap-secrets]
chap-secrets=/etc/ppp/chap-secrets

[ip-pool]
gw-ip-address=100.64.0.1
100.64.0.10-100.64.0.200

[log]
log-file=/tmp/accel.log
copy=1
level=5

[cli]
tcp=127.0.0.1:2001
EOF

# start_dataplane <startup.conf>
#
# The CP interface is a plain tapv2, NOT a linux_cp pair: the pppoe plugin's
# cp dispatch wants an interface it can transmit raw ethernet on, and
# lcp_ethertype_enable is not involved in any of it.
#
# Its Linux-side MAC is set to the access interface's. That is not cosmetic and
# it is not optional - see the note where it is set.
start_dataplane() {
  vpp -c "$1" > /tmp/vpp.log 2>&1 &
  n=0
  until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do
    sleep 1; n=$(( n + 1 ))
  done
  vppctl show version >/dev/null 2>&1 || {
    echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

  vppctl create host-interface name acc0 > /dev/null
  vppctl set interface state host-acc0 up
  ACCMAC=$(vppctl show hardware host-acc0 | sed -n 's/.*Ethernet address //p' | head -1)
  vppctl create tap id 0 host-if-name vppcp > /dev/null
  vppctl set interface state tap0 up
  # pppoe-cp-dispatch REWRITES the source MAC of every frame it sends from the
  # CP interface to the access interface's hardware address, so the PADO the
  # client sees comes from the access MAC and the PADR comes back addressed to
  # it. accel-ppp's disc.c forwards a discovery frame only when its destination
  # is broadcast or the MAC OF THE INTERFACE IT IS BOUND TO, so with the tap on
  # its own MAC every PADR is silently discarded and discovery dies after PADO
  # with nothing in any log. Measured, and it is a hard constraint on the shape
  # of the thing: one CP interface can carry one access MAC.
  ip link set vppcp down
  ip link set vppcp address "$ACCMAC"
  ip link set vppcp up
  vppctl create pppoe cp cp-if-index 2 > /dev/null

  vppctl create host-interface name wan0 > /dev/null
  vppctl set interface state host-wan0 up
  vppctl set interface ip address host-wan0 200.150.0.1/24
  WANMAC=$(ip link show wanp | sed -n 's/.*link\/ether \([0-9a-f:]*\).*/\1/p')
  vppctl set ip neighbor host-wan0 200.150.0.2 "$WANMAC" > /dev/null
  vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512 > /dev/null
  vppctl set interface bpa out2in host-wan0
}

# dial <n> - n subscribers, each its own pppd in its own netns
dial() {
  i=1
  while [ "$i" -le "$1" ]; do
    timeout 600 ip netns exec "s$i" pppd call "sub$i" > "/tmp/pppd$i.log" 2>&1 &
    i=$(( i + 1 ))
  done
  n=0
  until [ "$(vppctl show pppoe session | grep -c sw-if-index)" -ge "$1" ] \
        || [ "$n" -ge 60 ]; do
    sleep 1; n=$(( n + 1 ))
  done
  if [ "$(vppctl show pppoe session | grep -c sw-if-index)" -lt "$1" ]; then
    echo "  only $(vppctl show pppoe session | grep -c sw-if-index) of $1 came up; what the first client saw:"
    tail -15 /tmp/pppd1.log | sed 's/^/    /'
  fi
}

hangup() {
  pkill -x pppd 2>/dev/null
  pkill -x accel-pppd 2>/dev/null
  sleep 2
}

echo
echo "=========== 6. ONE REAL SESSION, TERMINATED BY accel-pppd ==========="
start_dataplane /src/lab/startup-bng.conf
rm -f /tmp/accel.log
accel-pppd -c /etc/accel-ppp.conf -d -p /var/run/accel-pppd.pid
sleep 2
echo "  who is on VPP's binary API:"
vppctl show api clients | sed 's/^/    /'
dial 3
echo
echo "  accel-ppp's own session table:"
accel-cmd -p 2001 show sessions 2>&1 | sed 's/^/    /'
echo
echo "  VPP's pppoe session table - created over the API, not by this script:"
vppctl show pppoe session | sed 's/^/    /'
echo
echo "  the discovery and negotiation that got there:"
grep -E "PAD[IOR S]|LCP ConfAck|PAP Auth|IPCP ConfAck|authentication succeeded" \
  /tmp/accel.log | head -12 | sed 's/^/    /'

SUB_IP=$(ip netns exec s1 ip -4 -br addr show ppp0 2>/dev/null | awk '{print $3}')
# Every needle below is built from this, and an empty one would turn "does the
# subscriber /32 appear" into "does a /32 appear" - which is true on any box
# and would report a pass on a run where no subscriber ever connected.
SUB_IP=${SUB_IP:-no-subscriber}
SUB_IF=$(accel-cmd -p 2001 show sessions 2>/dev/null \
         | awk -v ip="$SUB_IP" 'ip != "" && $0 ~ ip {print $1}')
echo
echo "  subscriber s1 has $SUB_IP on VPP interface $SUB_IF"

echo
echo "=========== 7. THE FIB ENTRY, AND WHERE IT IS NOT ==========="
# #170 predicted this and it is exactly right: the subscriber /32 is installed
# straight into the VPP FIB over the binary API and never touches the kernel.
echo "  VPP FIB:"
vppctl show ip fib "$SUB_IP/32" | grep -E "^${SUB_IP}/32|pppoe_session" | head -3 | sed 's/^/    /'
echo "  Linux kernel route table:"
ip route show "$SUB_IP" | sed 's/^/    /'
echo "    (nothing above this line means the kernel has never heard of it)"
echo
echo "  and the entry stock VPP adds that the patched plugin does not:"
vppctl show ip fib 0.0.0.0/32 | grep -E "pppoe_session|0.0.0.0/32 fib" | head -3 | sed 's/^/    /'

echo
echo "=========== 8. DOES BPA TRANSLATE THE SUBSCRIBER? ==========="
cat > /tmp/send.py <<'PY'
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("", 12345))
for _ in range(3):
    s.sendto(b"hexa", ("200.150.0.2", 53))
    time.sleep(0.3)
PY

capture() {
  timeout 6 tcpdump -i wanp -n -c 6 udp port 53 > "$1" 2>&1 &
  sleep 1
  ip netns exec s1 python3 /tmp/send.py
  sleep 5
}

echo "  RED: bpa-in2out not enabled on $SUB_IF"
capture /tmp/cap-off.txt
BEFORE=$(grep -oE "IP [0-9.]+\.[0-9]+ >" /tmp/cap-off.txt | head -1)
echo "    on the wire: ${BEFORE:-nothing captured}"

if [ "$OFF" = "off" ]; then
  echo
  echo "  GREEN: SKIPPED - lab run with 'off', the feature is never enabled"
else
  echo
  echo "  GREEN: enabling bpa-in2out on $SUB_IF"
  vppctl set interface bpa in2out "$SUB_IF"
  vppctl show interface features "$SUB_IF" | grep -A3 "ip4-unicast" | sed 's/^/    /'
fi
capture /tmp/cap-on.txt
AFTER=$(grep -oE "IP [0-9.]+\.[0-9]+ >" /tmp/cap-on.txt | head -1)
echo "    on the wire: ${AFTER:-nothing captured}"
echo
echo "  what ref/bpa_model.py says the answer has to be:"
# Asked of the reference model rather than worked out here. A lab that
# recomputes the mapping in its own arithmetic is checking the plugin against
# the lab author, not against the specification.
PREDICTED=$(python3 - "$SUB_IP" 2>/dev/null <<'PY'
import sys
sys.path.insert(0, "/src/ref")
from bpa_model import PoolConfig
p = PoolConfig("lab", ["100.64.0.0/22"], ["200.150.0.16/30"], block_size=512)
try:
    m = p.forward(sys.argv[1])
    print(f"{m['ip_pub']}:{m['port_lo']}")
except Exception:
    print("no-subscriber")
PY
)
echo "    $PREDICTED"
vppctl show bpa session | sed 's/^/    /'

echo
echo "=========== 9. show route verbose, THE PRODUCT'S OWN COMMAND ==========="
# #170 says every subscriber /32 becomes a standing false alarm here. This is
# the five-minute check that settles it, run against a box that has three live
# subscribers in the FIB and none of them in the RIB.
mkdir -p /etc/frr /var/run/frr
cat > /etc/frr/zebra.conf <<'EOF'
hostname hexarouting
EOF
chown -R frr:frr /etc/frr /var/run/frr 2>/dev/null
. /src/lab/lib.sh
start_frr
ROUTE_VERBOSE=$(python3 /src/src/hexash/hexash.py -c "show route verbose" 2>&1)
printf '%s\n' "$ROUTE_VERBOSE" | sed 's/^/  /'

echo
echo "=========== 10. THE BARRIER, AT A MASS RECONNECT ==========="
# #170 marks this "not verified" and says it MUST be measured. It is measured
# the way lab/pool-del.sh measures a delete: from VPP's own barrier accounting,
# because while the barrier is held every worker spins and no packet moves.
#
# Workers, and not for realism: below two threads VPP's barrier returns without
# doing anything and this section would report zero on a box that stalls.
hangup
pkill vpp 2>/dev/null
sleep 2
i=4
while [ "$i" -le 10 ]; do
  ip netns add "s$i" 2>/dev/null
  ip link add "cli$i" type veth peer name "br$i" 2>/dev/null
  ip link set "br$i" master br0; ip link set "br$i" up
  ip link set "cli$i" netns "s$i" 2>/dev/null
  ip netns exec "s$i" ip link set "cli$i" address "02:aa:00:00:00:$(printf %02x "$i")"
  ip netns exec "s$i" ip link set "cli$i" up
  ip netns exec "s$i" ip link set lo up
  add_subscriber_secret "$i"
  sed -e "s/cliN/cli$i/" -e "s/subN/sub$i/" /tmp/peer.tmpl > "/etc/ppp/peers/sub$i"
  i=$(( i + 1 ))
done

start_dataplane /src/lab/startup-bng-workers.conf
vppctl show threads | head -4 | sed 's/^/  /'
rm -f /tmp/accel.log
accel-pppd -c /etc/accel-ppp.conf -d -p /var/run/accel-pppd.pid
sleep 2
vppctl event-logger trace barrier > /dev/null
vppctl event-logger clear > /dev/null
dial 10
vppctl show event-logger all > /tmp/elog-connect.txt 2>/dev/null
NSESS=$(vppctl show pppoe session | grep -c sw-if-index)
echo "  $NSESS sessions established"

# One microsecond figure per barrier acquisition, attributed to whoever took
# it. "bar-trace-<who>-#<n>" opens an acquisition and
# "bar-rel-#<n>-e<e>-u<u>-t<closed>" closes it; the last field is how long
# forwarding was stopped. The CR strip is not decoration: vppctl ends its lines
# with CR+LF and without it no key matches the next line's, which reports no
# stall at all on a box that has one. lab/pool-del.sh learned that first.
bar_times() {
  tr -d '\r' < "$1" | awk -v who="$2" '
    /bar-trace-/ {
      n = $2; sub(/.*#/, "", n)
      w = $2; sub(/^bar-trace-/, "", w); sub(/-#.*/, "", w)
      if (w == who) mine[n] = 1
      next
    }
    /bar-rel-#/ {
      n = $2; sub(/^bar-rel-#/, "", n); sub(/-.*/, "", n)
      t = $2; sub(/.*-t/, "", t)
      if (n in mine) print t + 0
    }'
}

API_T=$(bar_times /tmp/elog-connect.txt vl_msg_api_barrier_sync | sort -n)
API_N=$(printf '%s\n' "$API_T" | grep -c .)
API_SUM=$(printf '%s\n' "$API_T" | awk '{s+=$1} END{print s+0}')
API_MAX=$(printf '%s\n' "$API_T" | tail -1)
# A barrier acquisition waits for every worker to arrive, so on a runner that
# is oversubscribed the figure is the MACHINE's scheduling latency and not the
# box's. lab/pool-del.sh says the same about the same number. Measured on a
# quiet runner these come out at tens of microseconds each; measured with a
# dozen other lab containers on the same cores they come out in the tens of
# milliseconds. Read the number as a ceiling, and read a number taken under
# load as a ceiling on the runner.
echo
echo "  what accel-ppp's own API calls cost, bringing $NSESS sessions up:"
echo "    $API_N acquisition(s), every one in us:"
echo "      $(printf '%s\n' "$API_T" | tr '\n' ' ')"
echo "    total ${API_SUM}us, worst ${API_MAX}us"

vppctl event-logger clear > /dev/null
n=0
while [ "$n" -lt "$NSESS" ]; do
  vppctl set interface bpa in2out "pppoe_session$n" > /dev/null 2>&1
  n=$(( n + 1 ))
done
vppctl show event-logger all > /tmp/elog-bpa.txt 2>/dev/null
BPA_T=$(bar_times /tmp/elog-bpa.txt vlib_cli_dispatch_sub_commands | sort -n)
BPA_SUM=$(printf '%s\n' "$BPA_T" | awk '{s+=$1} END{print s+0}')
BPA_MAX=$(printf '%s\n' "$BPA_T" | tail -1)
echo
echo "  what 'set interface bpa in2out' costs for the same $NSESS sessions:"
echo "    every acquisition in us:"
echo "      $(printf '%s\n' "$BPA_T" | tr '\n' ' ')"
echo "    total ${BPA_SUM}us, worst ${BPA_MAX}us"
echo
echo "  per session, end to end, that is roughly"
PER=$(( NSESS > 0 ? (API_SUM + BPA_SUM) / NSESS : 0 ))
echo "    ${PER}us of stopped forwarding"

echo
echo "  NOTE: in2out translation is NOT asserted on this multi-worker instance."
echo "  It is broken there for a reason that has nothing to do with PPPoE - see"
echo "  the separate defect report. Section 8 ran single-threaded."

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

expect "accel-ppp-ng does NOT build against stock VPP 26.06" \
  test "$STOCK_RC" -ne 0
expect_in "and the reason is a field the shipped pppoe.api does not have" \
  "has no member named 'disable_fib'" "$(cat /tmp/build-stock.log)"
expect "with that assignment dropped it builds" test "$PATCHED_RC" -eq 0

expect_in "a PPPoE session was negotiated end to end" \
  "authentication succeeded" "$(cat /tmp/accel.log 2>/dev/null || true)"
expect "accel-pppd - not this script - created the VPP interface" \
  test -n "$SUB_IF"
expect "the subscriber got an address out of the pool" \
  sh -c "echo '$SUB_IP' | grep -q '^100\\.64\\.'"

expect_in "with the feature off the subscriber leaves untranslated" \
  "IP $SUB_IP.12345 >" "$(cat /tmp/cap-off.txt)"
expect_in "with the feature on it leaves on the block the model predicted" \
  "IP $(echo "$PREDICTED" | tr ':' '.') >" "$(cat /tmp/cap-on.txt)"
expect_not_in "and its private address is gone from the wire" \
  "IP $SUB_IP.12345 >" "$(cat /tmp/cap-on.txt)"

# The claim #170 makes about this command, checked against the command.
expect_in "show route verbose lists the subscriber /32" \
  "$SUB_IP/32" "$ROUTE_VERBOSE"
expect_not_in "but does NOT report it as a fault - the alarm is one-way" \
  "in FRR, NOT forwarding" "$ROUTE_VERBOSE"

expect "the barrier cost of a mass connect was measured" \
  test "${API_N:-0}" -gt 0
# VPP's own barrier timeout is 1s. Anything approaching it is a different
# failure entirely, and the number is worth nothing without that bound.
expect "and no single acquisition approaches VPP's barrier timeout" \
  sh -c "test \"${API_MAX:-0}\" -lt 100000 && test \"${BPA_MAX:-0}\" -lt 100000"

verdict
