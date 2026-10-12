#!/bin/sh
# Management in a VRF of its own, while the main table carries the
# subscribers' default (#580).
#
#   sh tools/lab.sh mgmt-vrf        with `system / management / vrf mgmt`
#   sh tools/lab.sh mgmt-vrf off    the same VRF and the same routes, without
#                                   the statement - and the box is unreachable
#   sh tools/lab.sh mgmt-vrf unrepaired
#                                   with the statement, on a dataplane started
#                                   with `bpa { ip6-mcast-follow off }`: an IPv6
#                                   table move as VPP 26.06 makes it (#594)
#   sh tools/lab.sh mgmt-vrf loaded with the statement, on a zebra whose RIB
#                                   queue runs a second behind the kernel
#                                   and with every core kept busy (#595)
#   sh tools/lab.sh mgmt-vrf unsettled
#                                   loaded, with the box not waiting for
#                                   zebra (PRISMA_VRF_SETTLE_S=0): red
#
# LOADED IS WHAT 5-10 LABS AT ONCE DID TO ZEBRA, ON PURPOSE (#595). 2 runs in
# about 51 lost table 100's IPv6 default after the move, and the cause was
# measured with zebra's debug on (remove_vrf_device() in prismash has it):
# zebra hears links and routes on two sockets and processes its RIB queue
# later still, and the VRF's removal at the rollback stranded a node of
# table 100 marked "queued" - never processed again. `zebra work-queue 1000`
# holds that queue a second, so every route event lands inside the window
# the busy runs only sometimes hit; measured on 2026-10-08 with the box as it
# was in 0.152.6, 300, 1000 and 3000 ms each went red on all 15 checks the
# field runs failed. The burners (one per core) are the contention itself.
# frr-reload takes the knob away at each commit - it is not the box's
# configuration - so every prismash call here puts it back first.
#
# The box as 10.40.200.166 asked for it: its management card is a linux_cp
# pair in VRF mgmt (table 100), with an IPv4 and an IPv6 address and a
# default of each family through the management gateway, and a static route
# to a second management network (.166's 10.95.200.0/24) in the VRF's table;
# the main table's defaults go to the upstream. An administrator at
# 198.51.100.7, 2001:db8:7::7 and 203.0.113.9 - behind the management
# gateway, reached by no route of the main table - must reach, over IPv4 and
# IPv6:
#
#   SSH       a real sshd on the drop-in `ssh listen-address` writes, the
#             management addresses, both families (#593)
#   the API   the socket appliance/api-configure writes from `api
#             listen-address`, listened on as systemd does (FreeBind=yes)
#   SNMP      a real snmpd on `snmp listen-address`, both families
#
# and the box must reach, through management, a trap receiver (snmpd's own
# coldStart, to `snmp trap-host`), syslog collectors (`system logging`, sent by
# the events watcher's own pass), a resolver (`dns server`), and a release
# server by the very function `system upgrade` downloads with - while a peer
# the main table has a route for is still reached by the main table.
#
# THE SERVICES ARE THE BOX'S, THE SERVICE MANAGER IS A STAND-IN. This
# container runs no systemd, so a `systemctl` on PATH does for snmpd and the
# API's socket what the box's units do: restart snmpd on the configuration the
# VTY wrote, and run appliance/api-configure, whose socket unit it then listens
# on with that unit's own ListenStream and FreeBind lines. /run/systemd/system
# exists so the VTY asks it (#587).
#
# EVERY PHASE ASKS THE SAME QUESTIONS (reach), after:
#   1. a `commit confirmed` that moves management into the VRF, and the box
#      taking it all back when nobody confirms - the session lost;
#   2. the same commit, confirmed - with an administrator's TCP session opened
#      on the main table before it and still answering after it;
#   3. another commit that does not touch management;
#   4. `system dataplane restart` as prisma-hot-card does it: the dataplane and
#      FRR stopped, the pairs gone, the tuner run (appliance/apply-tuning, its
#      computation stubbed as test/test_apply_tuning.sh stubs it), the cards
#      claimed again, the way back recorded, `prismash --load`;
#   5. a restart of the containers behind the VTY's back: the dataplane and
#      FRR again, and `prismash --reapply`, what watch-the-dataplane runs;
#   6. a boot: no VRF device, no rules, the sysctls at the kernel's values,
#      the tuner, then snmpd, sshd and the API's socket started BEFORE the load
#      as systemd starts them - before the address they name exists (#591) -
#      then `prismash --load`;
#   7. a boot whose tuner did not run: `--load` puts management back alone.
#
# and in each, the gateway forgets the box's IPv6 address and must learn it
# again within 5 s (#594). A move left the card's IPv6 enabled - linux_cp holds
# it by the TAP's link-local - and VPP 26.06 moves such an interface to the new
# table without its Accept on the solicited-node group, so every MULTICAST
# solicitation for the address was dropped ("Multicast RPF check failed")
# while unicast ones were answered. Whether a run saw it depended on the
# gateway's entry: kept, it was answered; flushed by the card's link cycling
# through the VRF's enslave (lcp-sync), it waited for the box to solicit the
# gateway itself - 0 s in one run, over 30 s in the next, 3 runs of 4 red on
# 2026-10-08. Forgetting the entry on purpose makes that the case every run
# asks, rather than the case a run is lucky enough to hit. Then
#   9. a tenant VRF (#540): the upstream card into `vrf cust`, and the
#      upstream must learn its IPv6 address afresh the same way;
#  10. that VRF taken away by a `commit confirmed` and given back by the box:
#      its routes of both families back in table 200 (#595).
# `unrepaired` turns those red and nothing else, measured on 2026-10-08: 21
# checks - afresh, the solicited-node Accept and IPv6's SSH, API and SNMP in
# phases 1, 2 and 3 (once moved, the card stays deaf to multicast until it
# moves again; phases 2 and 3 also lose IPv6's download and TCP syslog, and
# 3 its `ssh listen-address` on IPv6), and phase 9's afresh. Phases 4 to 7
# are green either way, and rightly: the Accept is in table 100 there, read
# by the check - a dataplane started anew has the card bound to its table
# before its IPv6 is enabled, and the link's own enable puts it there.
#
# OFF-RED-AT-LEAST: 122
#
# 122 since #595, measured on 2026-10-08 (122 of 220), and the 33 that went
# green are the box doing better, not the lab asserting less: a card now
# changes VRF with no address on it and gets them back after, so it no
# longer needs the statement's keep_addr_on_down to keep its IPv6 address
# in the VRF. Green now without the statement, in each of the seven phases:
# the card's and the dataplane's IPv6 in table 100, table 100's IPv6 default
# and afresh; and after the rollback the IPv6 address, SSH and SNMP on IPv6
# and SNMP on IPv4 (snmpd names that address). What the statement is for -
# SSH, the API, SNMP, the box's own traffic through the VRF - is red as
# before. Phase 10 does not run off.
#
# 155 since #594, measured on 2026-10-08 (155 of 220): afresh is red in each
# of the seven phases and after the rollback, and rightly - without the
# statement the card has no IPv6 address in the VRF to be learnt. The
# solicited-node Accept is green there: the link-local alone keeps the
# card's IPv6 enabled, and the plugin moves its Accept with it either way.
#
# 147 since #593, measured on 2026-10-08: phase 3's `ssh listen-address`
# changed inside the VRF adds four. Without the statement the commit check
# refuses the IPv6 address back ("in vrf mgmt"), and sshd, reloaded on one
# address it cannot bind from the main table, answers on none.
#
# A FLOOR, measured on 2026-10-08 (#580): the off run red on 143 of its 197
# checks. Green there and rightly: the controls before the move; the routes
# FRR holds either way (both main defaults, table 100's IPv4 default and
# second network, what main has a route for); the `vrf mgmt` logging
# destination, which binds to the VRF itself; and, after the expired commit
# confirmed, the card out of the VRF, no rule, l3mdev off, the main default
# back, SSH and the API on IPv4. Red after the rollback too, and rightly:
# without the statement the card lost its IPv6 address on the way into the
# VRF, so IPv6 and snmpd (which names that address) stay down.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api, wait_vpp
. /src/lab/verdict.sh
MODE=${1:-on}
case "$MODE" in on|off|unrepaired|loaded|unsettled) ;;
  *) echo "mode: on, off, unrepaired, loaded or unsettled"; exit 2 ;; esac
apt-get install -y -qq snmptrapd >/dev/null 2>&1
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing /var/log/prisma-routing \
         /etc/snmp /var/lib/snmp /run/prisma-routing/state /run/sshd \
         /run/systemd/system /run/lab-units
HX="python3 /src/src/prismash/prismash.py"
export PRISMA_CONFIG=/etc/prisma-routing/prisma-routing.conf
export PRISMA_ROLLBACK=/tmp/rollback PRISMA_TUNE_ENV=/tmp/tune.env
export PRISMA_CONFIRM=/tmp/confirm-pending
export PRISMA_MGMT_PATH=/run/prisma-routing/dataplane-management
export PRISMA_EVENTS=/var/log/prisma-routing/events.log
export PRISMA_EVENTS_STATE=/var/log/prisma-routing/events.state
export PRISMA_WATCH_STATE=/var/log/prisma-routing/events-watch.state
export PRISMA_LOGGING_CONF=/etc/prisma-routing/logging.conf
export PRISMA_LOGGING_FWD_STATE=/var/log/prisma-routing/events-forward.state
export PRISMA_LOGGING_STATUS=/run/prisma-routing/state/logging
export PRISMA_WTMP=/tmp/no-wtmp PRISMA_BTMP=/tmp/no-btmp
export PRISMA_DOORS=/tmp/no-doors.sock
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api
T=4   # seconds a reach check waits: a red one costs this, and off has many
# The slow zebra and the busy cores (#595), see the top.
case "$MODE" in
  loaded|unsettled)
    cat > /usr/local/bin/hx-lagged <<'EOF'
#!/bin/sh
vtysh -c configure -c "zebra work-queue 1000" > /dev/null 2>&1
exec python3 /src/src/prismash/prismash.py "$@"
EOF
    chmod +x /usr/local/bin/hx-lagged
    HX=/usr/local/bin/hx-lagged
    [ "$MODE" = unsettled ] && export PRISMA_VRF_SETTLE_S=0
    BURNERS=
    for _i in $(seq "$(nproc)"); do
      python3 -c "while True: pass" & BURNERS="$BURNERS $!"
    done
    trap 'kill $BURNERS 2>/dev/null' EXIT
    echo "  MODE=$MODE: zebra's RIB queue held 1 s, $(nproc) cores kept" \
         "busy$([ "$MODE" = unsettled ] && echo ', the box not waiting')"
    ;;
esac

# ------------------------------------------------------------------ stand-ins
# systemctl, as far as the box's units go here.
cat > /usr/local/sbin/systemctl <<'EOF'
#!/bin/sh
echo "systemctl $*" >> /tmp/systemctl.log
snmpd_pid() { cat /run/snmpd-lab.pid 2>/dev/null; }
case "$*" in
  "restart snmpd")
    p=$(snmpd_pid); [ -n "$p" ] && kill "$p" 2>/dev/null && sleep 1
    rm -f /run/snmpd-lab.pid
    snmpd -Lf /tmp/snmpd.log -C -c /etc/snmp/snmpd.conf -p /run/snmpd-lab.pid
    sleep 1; exit 0 ;;
  "is-active --quiet snmpd")
    p=$(snmpd_pid); [ -n "$p" ] && kill -0 "$p" 2>/dev/null; exit $? ;;
  "disable --now snmpd")
    p=$(snmpd_pid); [ -n "$p" ] && kill "$p" 2>/dev/null; exit 0 ;;
  "restart prisma-routing-api-config.service")
    PRISMA_SYSTEMCTL=/usr/local/sbin/systemctl PRISMA_API_UNIT_DIR=/run/lab-units \
      sh /src/appliance/api-configure; exit $? ;;
  "restart prisma-routing-api-proxy.socket")
    p=$(cat /run/api-lab.pid 2>/dev/null); [ -n "$p" ] && kill "$p" 2>/dev/null
    python3 /tmp/api-socket.py /run/lab-units/prisma-routing-api-proxy.socket \
      >> /tmp/api-socket.log 2>&1 &
    echo $! > /run/api-lab.pid; sleep 1
    kill -0 "$(cat /run/api-lab.pid)" 2>/dev/null; exit $? ;;
  # systemd's reload of ssh: a SIGHUP, sshd re-executing on the drop-in.
  "reload ssh")
    p=$(cat /run/sshd-lab.pid 2>/dev/null)
    [ -n "$p" ] && kill -HUP "$p" 2>/dev/null && sleep 1; exit 0 ;;
  is-active*) exit 3 ;;
  *) exit 0 ;;
esac
EOF
chmod +x /usr/local/sbin/systemctl
# The API's socket, as systemd listens on a socket unit: each ListenStream,
# with IP_FREEBIND when the unit says FreeBind=yes. A connection is answered
# with a banner and closed; the proxy behind it is the API's own business.
cat > /tmp/api-socket.py <<'EOF'
import socket, sys, threading
unit = open(sys.argv[1]).read().splitlines()
free = "FreeBind=yes" in unit
socks = []
for l in unit:
    if not l.startswith("ListenStream="):
        continue
    a, _, p = l.split("=", 1)[1].rpartition(":")
    fam = socket.AF_INET6 if a.startswith("[") else socket.AF_INET
    s = socket.socket(fam, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if free:
        s.setsockopt(socket.SOL_IP, 15, 1)   # IP_FREEBIND
    s.bind((a.strip("[]"), int(p)))
    s.listen(8)
    socks.append(s)
    print("listening on", a, p, "freebind" if free else "", flush=True)
def serve(s):
    while True:
        c, _ = s.accept(); c.sendall(b"PRISMA-API\n"); c.close()
for s in socks[1:]:
    threading.Thread(target=serve, args=(s,), daemon=True).start()
serve(socks[0])
EOF

# ------------------------------------------------------------- the networks
# The container's own default would answer for the main table; the box's is
# the one the VTY puts there.
ip route del default 2>/dev/null
ip -6 route del default 2>/dev/null
# mg: the management gateway, with the administrator behind it on its lo, and
# a second management network (.166's 10.95.200.0/24) behind it too.
# up: the upstream, which has no route to any of them.
for n in mg up; do
  ip link add veth-$n-o type veth peer name veth-$n 2>/dev/null
  ip link set veth-$n up
  ip netns add $n 2>/dev/null
  ip link set veth-$n-o netns $n
  ip netns exec $n ip link set lo up
  ip netns exec $n ip link set veth-$n-o up
done
ip netns exec mg ip addr add 10.200.0.2/24 dev veth-mg-o
ip netns exec mg ip -6 addr add 2001:db8:200::2/64 dev veth-mg-o nodad
ip netns exec mg ip addr add 198.51.100.7/32 dev lo
ip netns exec mg ip addr add 203.0.113.9/32 dev lo
ip netns exec mg ip -6 addr add 2001:db8:7::7/128 dev lo nodad
ip netns exec up ip addr add 10.0.0.2/24 dev veth-up-o
ip netns exec up ip -6 addr add 2001:db8:10::2/64 dev veth-up-o nodad
ip netns exec mg sysctl -q -w net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1
# The gateway asks ARP from its own address on the link, as a router does.
# Left at Linux's default it asked from the administrator's 198.51.100.7, the
# source of the packet it was forwarding, and VPP's arp-reply refuses a sender
# that is not on the interface's subnet ("IP4 source address not local to
# subnet"): measured, the gateway lost the box's MAC some seconds after the
# move and never got it back. Nothing of the box's.
ip netns exec mg sysctl -q -w net.ipv4.conf.all.arp_announce=2 \
    net.ipv4.conf.veth-mg-o.arp_announce=2

# What the administrator's side serves: a release server on each family, a
# resolver, a trap receiver, and three syslog collectors.
mkdir -p /tmp/rel && echo "a release, 580" > /tmp/rel/pkg
ip netns exec mg sh -c 'cd /tmp/rel && exec python3 -m http.server 8080 --bind 198.51.100.7' \
    > /tmp/http4.log 2>&1 &
ip netns exec mg sh -c 'cd /tmp/rel && exec python3 -m http.server 8080 --bind 2001:db8:7::7' \
    > /tmp/http6.log 2>&1 &
ip netns exec mg python3 - > /tmp/dns.log 2>&1 <<'PY' &
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("198.51.100.7", 53))
while True:
    q, a = s.recvfrom(512)
    qd = q[12:]
    end = qd.index(b"\0") + 5
    name = qd[:end - 4]
    qtype = qd[end - 4:end - 2]
    ans = b""
    if qtype == b"\x00\x01":
        ans = b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04" + bytes([192, 0, 2, 80])
    hdr = q[:2] + b"\x81\x80" + q[4:6] + (b"\x00\x01" if ans else b"\x00\x00") + b"\x00\x00\x00\x00"
    s.sendto(hdr + qd[:end] + ans, a)
    print("asked", name, flush=True)
PY
echo "disableAuthorization yes" > /etc/snmp/snmptrapd.conf
ip netns exec mg snmptrapd -f -Lf /tmp/traps.log -On udp:198.51.100.7:162 &
ip netns exec mg python3 /src/lab/syslog_sink.py udp 198.51.100.7 5514 /tmp/sys-udp4.log &
ip netns exec mg python3 /src/lab/syslog_sink.py tcp 2001:db8:7::7 5601 /tmp/sys-tcp6.log &
ip netns exec mg python3 /src/lab/syslog_sink.py udp 203.0.113.9 5515 /tmp/sys-vrf.log &

sysctl -p /src/appliance/etc/sysctl.conf >/dev/null 2>&1 || true
# The VTY owns /etc/resolv.conf once `dns server` is set; docker's is a mount
# a rename cannot replace (lab/dns.sh does the same).
umount /etc/resolv.conf 2>/dev/null
printf '# the container\nnameserver 192.0.2.53\n' > /etc/resolv.conf
VPP_PID=
start_box() {   # the dataplane and FRR, and the two cards claimed
  _conf=$(startup_conf lcp)
  if [ "$MODE" = unrepaired ]; then
    { cat "$_conf"; printf '\nbpa {\n  ip6-mcast-follow off\n}\n'; } \
        > /tmp/startup-unrepaired.conf
    _conf=/tmp/startup-unrepaired.conf
  fi
  vpp -c "$_conf" > /tmp/vpp.log 2>&1 &
  VPP_PID=$!
  wait_vpp
  # Each card with the MAC it had before, as a real one has: an af_packet
  # interface is given a new one at every start, and the saved file's
  # `mac-address` then set the dataplane's side to the old one while the
  # Linux pair kept the new - the gateway learnt the pair's MAC from its ARP
  # and the dataplane dropped it ("l3 mac mismatch"). Nothing of the box's.
  vppctl create host-interface name veth-mg hw-addr 02:58:00:00:00:01 >/dev/null
  vppctl create host-interface name veth-up hw-addr 02:58:00:00:00:02 >/dev/null
  # Up by hand the first time only, as the cards a box's startup.conf claims
  # come up down: after that it is the load's own business.
  if [ -z "${BOX_STARTED:-}" ]; then
    for n in mg up; do vppctl set interface state host-veth-$n up; done
    BOX_STARTED=1
  fi
  mkdir -p /var/run/frr /etc/frr
  printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
  chown -R frr:frr /var/run/frr /etc/frr
  start_frr
}
stop_box() {    # the dataplane and FRR stopped, their Linux ends gone
  for f in /var/run/frr/*.pid; do
    [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null
  done
  [ -n "$VPP_PID" ] && kill "$VPP_PID" 2>/dev/null && wait "$VPP_PID" 2>/dev/null
  sleep 2
  # prisma-hot-card's stale_ends: a pair the stopped dataplane left behind.
  for t in vppmg0 vppup0; do ip link del "$t" 2>/dev/null; done
  rm -f /var/run/frr/*.pid /var/run/frr/*.sock
}
way_back() {    # what prisma-routing-tune records for the card it claims
  echo "vppmg0 10.200.0.1/24 02:58:00:00:00:01 10.200.0.2" > "$PRISMA_MGMT_PATH"
}
tuner() {       # appliance/apply-tuning, its computation stubbed
  mkdir -p /tmp/tuneroot/stub
  printf '#!/bin/sh\nexit 0\n' > /tmp/tuneroot/stub/prisma-routing-tune
  chmod +x /tmp/tuneroot/stub/prisma-routing-tune
  ( set -a; . "$PRISMA_TUNE_ENV"; set +a
    PATH=/tmp/tuneroot/stub:$PATH PRISMA_ROOT=/tmp/tuneroot \
      sh /src/appliance/apply-tuning ) > /tmp/tuner.log 2>&1
  grep -i "management" /tmp/tuner.log | sed 's/^/  tuner: /'
}
SSHD_PID=
start_sshd() {  # sshd on the drop-in the VTY wrote: `ssh listen-address`
  [ -n "$SSHD_PID" ] && kill "$SSHD_PID" 2>/dev/null && sleep 1
  /usr/sbin/sshd -D -e > /tmp/sshd.log 2>&1 &
  SSHD_PID=$!
  echo "$SSHD_PID" > /run/sshd-lab.pid
  sleep 1
}
ssh-keygen -A >/dev/null 2>&1

start_box
# The administrator's TCP session's other end, and the box's main-table echo.
python3 - > /tmp/echo.log 2>&1 <<'PY' &
import socket, threading
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 7007)); s.listen(8)
def echo(c):
    while True:
        d = c.recv(64)
        if not d:
            return
        c.sendall(d)
while True:
    c, _ = s.accept(); threading.Thread(target=echo, args=(c,), daemon=True).start()
PY

# ------------------------------------------------------------------ the checks
# The administrator's side in python rather than nc or ssh: which nc a
# container carries decides what `-s` and `-w` mean, and a check must not
# depend on it. Each one connects FROM an administrator address, so the reply
# has to find its way back through the management gateway.
tcp_in() {   # <box address> <port> <from>
  ip netns exec mg timeout $T python3 -c "
import socket, sys
fam = socket.AF_INET6 if ':' in '$1' else socket.AF_INET
s = socket.socket(fam); s.settimeout($T - 1)
s.bind(('$3', 0)); s.connect(('$1', $2))
print(s.recv(64).decode().strip())" 2>&1; }
snmp_in() {  # <box address> <from>
  case "$1" in *:*) a="udp6:[$1]:161" ;; *) a="udp:$1:161" ;; esac
  ip netns exec mg timeout $T snmpget -v2c -c hexro -t 2 -r 0 \
      --clientaddr="$2" "$a" 1.3.6.1.2.1.1.5.0 2>&1; }
fetch() {    # <url>
  timeout 20 python3 -c "
import sys; sys.path.insert(0, '/src/src/prismash'); import prismash
sha, n, why = prismash.download_to('$1', '/tmp/got', $T)
print('downloaded', n, 'bytes' if sha else why)" 2>&1; }
pass_() { $HX --watch-events once > /dev/null 2>&1; }
coldstarts() { grep -c "1.3.6.1.6.3.1.1.5.1" /tmp/traps.log 2>/dev/null || echo 0; }

# THE GATEWAY'S NEIGHBOUR ENTRY, settled before anything is asked. A move
# takes the card's addresses off and puts them back in the dataplane (VPP
# refuses a table move with one on), and a restart makes the card anew: for
# those seconds the gateway's ARP or ND entry for the box fails, and a check
# that lands in them measures the gateway's retry timer, not the box. Waited
# for, up to 20 s, and the time said: it is what an operator's session sees
# too. The 20 s that IPv6 used to reach here right after the commit were the
# box's (#594), and afresh() below is what asserts they are gone.
settle() {
  t0=$(date +%s); t=0
  while [ "$t" -lt 20 ]; do
    ip netns exec mg ping -c1 -W1 -I 198.51.100.7 10.200.0.1 >/dev/null 2>&1 \
      && ip netns exec mg ping -6 -c1 -W1 -I 2001:db8:7::7 2001:db8:200::1 \
         >/dev/null 2>&1 && break
    sleep 1; t=$(( $(date +%s) - t0 ))
  done
  echo "  settled: the management addresses answer the gateway after ${t} s"
}

# THE BOX'S IPv6 ADDRESS LEARNT AFRESH (#594): the neighbour's entry deleted
# and asked for again, as a router asks whose entry went FAILED or was
# flushed by the link going down - a multicast solicitation, which is what
# the dataplane dropped after a move. Under a 5 s deadline, which Linux's
# three solicitations a second apart (mcast_solicit) end before: answered,
# it is the first of them; dropped, ping gives up at about 3 s.
afresh() {  # <description> <namespace> <its device> <box address> <from>
  ip netns exec "$2" ip -6 neigh del "$4" dev "$3" 2>/dev/null
  _t0=$(date +%s.%N)
  ip netns exec "$2" ping -6 -c1 -w5 -I "$5" "$4" > /dev/null 2>&1; _rc=$?
  _dt=$(awk -v a="$_t0" -v b="$(date +%s.%N)" 'BEGIN { printf "%.1f", b - a }')
  echo "  afresh: $4 from $2 answered=$([ $_rc = 0 ] && echo yes || echo no) after ${_dt} s ($(ip netns exec "$2" ip -6 neigh show "$4" dev "$3" | tr -d '\n'))"
  expect "$1" [ "$_rc" = 0 ]
}

reach() {   # <when>
  W=$1
  afresh "$W: the gateway learns the box's IPv6 afresh within 5 s (#594)" \
      mg veth-mg-o 2001:db8:200::1 2001:db8:200::2
  settle
  echo "  -- $W: what the host holds"
  ip route show table 100 2>&1 | sed 's/^/    table 100: /'
  ip -6 route show table 100 2>&1 | grep -v "^local\|^multicast\|fe80" | sed 's/^/    table 100: /'
  ip rule show 2>&1 | sed 's/^/    rule: /'
  echo "    vppmg0: $(ip -o link show vppmg0 2>&1 | grep -o 'master [^ ]*') $(ip -o addr show dev vppmg0 2>/dev/null | awk '{print $4}' | tr '\n' ' ')"
  # The box's routing: the main defaults are the upstream's, management's
  # are in table 100, with the second management network beside them.
  expect_in "$W: the main table's IPv4 default is the upstream's" "via 10.0.0.2" \
      "$(ip route show default 2>&1)"
  expect_in "$W: and its IPv6 default" "via 2001:db8:10::2" \
      "$(ip -6 route show default 2>&1)"
  expect_in "$W: management's IPv4 default is in table 100" "via 10.200.0.2" \
      "$(ip route show table 100 default 2>&1)"
  expect_in "$W: and its IPv6 default" "via 2001:db8:200::2" \
      "$(ip -6 route show table 100 default 2>&1)"
  expect_in "$W: the second management network is in table 100" "via 10.200.0.2" \
      "$(ip route show table 100 203.0.113.0/24 2>&1)"
  expect_in "$W: the card keeps its IPv6 address in the VRF" "2001:db8:200::1/64" \
      "$(ip -o -6 addr show dev vppmg0 2>&1)"
  expect_in "$W: the dataplane has the card's IPv6 in table 100 too" \
      "ip6 table-id 100" "$(vppctl show int addr host-veth-mg 2>&1)"
  # What afresh() needs of the dataplane, read where it lives (#594).
  expect_in "$W: and accepts its solicited-node group from the card there" \
      "host-veth-mg: Accept" \
      "$(vppctl show ip6 mfib table 100 ff02::1:ff00:0/104 2>&1)"
  # Into the box, from the administrator.
  expect_in "$W: SSH answers on the IPv4 management address" "SSH-2.0-OpenSSH" \
      "$(tcp_in 10.200.0.1 22 198.51.100.7)"
  expect_in "$W: SSH answers on the IPv6 one" "SSH-2.0-OpenSSH" \
      "$(tcp_in 2001:db8:200::1 22 2001:db8:7::7)"
  expect_in "$W: SSH answers from the second management network" "SSH-2.0-OpenSSH" \
      "$(tcp_in 10.200.0.1 22 203.0.113.9)"
  expect_in "$W: the API's socket answers on IPv4" "PRISMA-API" \
      "$(tcp_in 10.200.0.1 50051 198.51.100.7)"
  expect_in "$W: and on IPv6" "PRISMA-API" \
      "$(tcp_in 2001:db8:200::1 50051 2001:db8:7::7)"
  expect_in "$W: SNMP answers on IPv4" "STRING:" \
      "$(snmp_in 10.200.0.1 198.51.100.7)"
  expect_in "$W: and on IPv6" "STRING:" \
      "$(snmp_in 2001:db8:200::1 2001:db8:7::7)"
  # Out of the box, to the administrator's side.
  expect_in "$W: what the box sends to 198.51.100.7 leaves by vppmg0" \
      "dev vppmg0" "$(ip route get 198.51.100.7 2>&1)"
  expect_in "$W: and to 2001:db8:7::7" "dev vppmg0" \
      "$(ip -6 route get 2001:db8:7::7 2>&1)"
  expect_in "$W: and to the second management network" "dev vppmg0" \
      "$(ip route get 203.0.113.9 2>&1)"
  expect_in "$W: what the main table has a route for still goes by it" \
      "dev vppup0" "$(ip route get 10.0.0.2 2>&1)"
  expect_in "$W: system upgrade's download, over IPv4" "downloaded 15 bytes" \
      "$(fetch http://198.51.100.7:8080/pkg)"
  expect_in "$W: and over IPv6" "downloaded 15 bytes" \
      "$(fetch 'http://[2001:db8:7::7]:8080/pkg')"
  expect_in "$W: DNS: 'dns server 198.51.100.7' answers the box" "192.0.2.80" \
      "$(timeout $T getent ahostsv4 releases.prisma.test 2>&1)"
  # A trap: snmpd's coldStart, sent each time snmpd starts.
  c0=$(coldstarts); systemctl restart snmpd; sleep 2
  expect "$W: a trap reaches the trap-host through management ($c0 -> $(coldstarts))" \
      [ "$(coldstarts)" -gt "$c0" ]
  # The events watcher: an event, and one pass of its forwarding.
  python3 /src/src/prismash/events.py record host=gave-up warning "580 $W"
  pass_
  sleep 1
  $HX -c "show system logging" 2>&1 | sed 's/^/    logging: /'
  expect_in "$W: system logging reaches a UDP collector through management" \
      "580 $W" "$(cat /tmp/sys-udp4.log 2>/dev/null)"
  expect_in "$W: and a TCP one over IPv6" "580 $W" \
      "$(cat /tmp/sys-tcp6.log 2>/dev/null)"
  expect_in "$W: control: a destination named 'vrf mgmt' is sent through it" \
      "580 $W" "$(cat /tmp/sys-vrf.log 2>/dev/null)"
  expect_in "$W: the rules are there, both families" "lookup 100" \
      "$(ip rule show 2>&1; ip -6 rule show 2>&1 | grep 'lookup 100')"
}

# --------------------------------------------------------------- 0. baseline
echo "=== 0. the box before: management in the main table ==="
$HX -c "configure" \
    -c "interface host-veth-mg" -c "linux-name vppmg0" \
    -c "ipv4 address 10.200.0.1/24" -c "ipv6 address 2001:db8:200::1/64" -c "exit" \
    -c "interface host-veth-up" -c "linux-name vppup0" \
    -c "ipv4 address 10.0.0.1/24" -c "ipv6 address 2001:db8:10::1/64" -c "exit" \
    -c "router static" -c "0.0.0.0/0 next-hop 10.200.0.2" \
    -c "::/0 next-hop 2001:db8:200::2" -c "exit" \
    -c "commit" > /tmp/c0.log 2>&1
sed 's/^/  c0: /' /tmp/c0.log | tail -4
# SSH on the management addresses, both families (#593), once they are the
# box's: a commit of its own, which an ssh change applies as `commit
# confirmed`, confirmed here. Before the wait below, so what follows runs on
# the timeline it ran on before - the gateway's IPv6 neighbour entry is what
# a few seconds more or less moves (see settle).
$HX -c "configure" -c "ssh listen-address 10.200.0.1 2001:db8:200::1" \
    -c "commit" > /tmp/c0ssh.log 2>&1
$HX -c "configure" -c "commit" >> /tmp/c0ssh.log 2>&1
grep -E "error|commit complete|confirmed:" /tmp/c0ssh.log | sed 's/^/  c0ssh: /'
# IPv6's duplicate address detection, over before snmpd binds the address.
sleep 4
# The services, each from the VTY: SNMP and the API listen on the management
# addresses, both families (#582); the box's events go to three collectors,
# one of them named through the VRF; the box resolves through 198.51.100.7.
$HX -c "configure" -c "snmp enabled yes" \
    -c "snmp listen-address 10.200.0.1 2001:db8:200::1" \
    -c "snmp community hexro" -c "snmp trap-host 198.51.100.7 community tr4pc" \
    -c "api enabled yes" -c "api listen-address 10.200.0.1 2001:db8:200::1" \
    -c "dns server 198.51.100.7" \
    -c "commit" > /tmp/c0s.log 2>&1
sed 's/^/  c0s: /' /tmp/c0s.log | tail -6
$HX -c "configure" -c "system" -c "logging" -c "host 198.51.100.7 port 5514" \
    -c "host 2001:db8:7::7 port 5601 protocol tcp" -c "exit" -c "exit" \
    -c "commit" > /tmp/c0l.log 2>&1
sed 's/^/  c0l: /' /tmp/c0l.log | tail -3
start_sshd
pass_
cat /run/lab-units/prisma-routing-api-proxy.socket 2>&1 | grep -E "Listen|FreeBind" | sed 's/^/  api unit: /'
expect_in "control: the API's socket unit listens with FreeBind" "FreeBind=yes" \
    "$(cat /run/lab-units/prisma-routing-api-proxy.socket 2>&1)"
grep '^ListenAddress' /etc/ssh/sshd_config.d/prisma-routing.conf 2>&1 \
    | sed 's/^/  sshd drop-in: /'
expect_said "control: the VTY wrote sshd one ListenAddress per address (#593)" \
    "ListenAddress 10.200.0.1 ListenAddress [2001:db8:200::1]" \
    "$(cat /etc/ssh/sshd_config.d/prisma-routing.conf 2>&1)"
expect_in "control: and reads the list back" \
    "ssh listen-address 10.200.0.1 [2001:db8:200::1]" \
    "$($HX -c 'show running-config' 2>&1)"
expect_in "control: before the move, SSH answers on IPv4" "SSH-2.0-OpenSSH" \
    "$(tcp_in 10.200.0.1 22 198.51.100.7)"
expect_in "control: and on IPv6" "SSH-2.0-OpenSSH" \
    "$(tcp_in 2001:db8:200::1 22 2001:db8:7::7)"
expect_in "control: and SNMP on IPv6" "STRING:" "$(snmp_in 2001:db8:200::1 2001:db8:7::7)"

# The move, as one commit: the VRF, the card in it with both addresses, its
# defaults and the second management network in FRR's `vrf mgmt` block, the
# main defaults to the upstream, and - on - the statement. The same lines the
# report gives for 10.40.200.166.
move() {   # <commit words>
  c=$1
  set -- -c configure -c "vrf mgmt table 100" \
      -c "interface host-veth-mg" -c "vrf 100" -c "exit" \
      -c "vrf mgmt" -c "ip route 0.0.0.0/0 10.200.0.2" \
      -c "ip route 203.0.113.0/24 10.200.0.2" \
      -c "ipv6 route ::/0 2001:db8:200::2" -c "exit" \
      -c "router static" -c "no 0.0.0.0/0 next-hop 10.200.0.2" \
      -c "no ::/0 next-hop 2001:db8:200::2" \
      -c "0.0.0.0/0 next-hop 10.0.0.2" -c "::/0 next-hop 2001:db8:10::2" -c "exit" \
      -c system -c logging \
      -c "host 203.0.113.9 port 5515 source-address 10.200.0.1 vrf mgmt" \
      -c exit -c exit
  [ "$MODE" = off ] || set -- "$@" -c system -c management -c "vrf mgmt" \
      -c exit -c exit
  $HX "$@" -c "$c" > /tmp/move.log 2>&1
  grep -v "^entering\|^    move" /tmp/move.log | sed 's/^/    move: /' | tail -12
  # A destination the commit adds starts at the record's end, at its first
  # pass: this is that pass, so the phase's own event is news to it.
  pass_
}
[ "$MODE" = off ] && echo "  MODE=off: no 'system / management / vrf mgmt'"

# ---------------------------------------------- 1. commit confirmed, lost
echo; echo "=== 1. commit confirmed, and nobody confirms it ==="
move "commit confirmed 5"
sleep 3
reach confirmed
# The minutes, gone by: the deadline moved back as waiting would move it.
python3 - <<'EOF'
import sys
sys.path.insert(0, "/src/src/prismash")
import prismash
c = prismash.ConfirmPending(prismash.CONFIRM_PATH)
p = c.read()
c.arm(p["deadline"] - 301, p["config"], p["minutes"], p["attempts"])
EOF
$HX --confirm-expired > /tmp/expired.log 2>&1
sed 's/^/  expired: /' /tmp/expired.log | head -4
sleep 3
echo "  -- taken back: what the host holds"
ip rule show | sed 's/^/    rule: /'
echo "    vppmg0: $(ip -o link show vppmg0 2>&1 | grep -o 'master [^ ]*')"
expect_not_in "taken back: the card is out of the VRF" "master mgmt" \
    "$(ip -o link show vppmg0 2>&1)"
expect_not_in "taken back: no rule sends the box's traffic to table 100" "lookup 100" \
    "$(ip rule show 2>&1; ip -6 rule show 2>&1)"
expect_in "taken back: l3mdev accept is off again" "= 0" \
    "$(sysctl net.ipv4.tcp_l3mdev_accept 2>&1)"
expect_in "taken back: the main default is management's again" "via 10.200.0.2" \
    "$(ip route show default 2>&1)"
expect_in "taken back: the card kept its IPv6 address on the way out" \
    "2001:db8:200::1/64" "$(ip -o -6 addr show dev vppmg0 2>&1)"
expect_in "taken back: SSH answers on IPv4" "SSH-2.0-OpenSSH" \
    "$(tcp_in 10.200.0.1 22 198.51.100.7)"
expect_in "taken back: SSH answers on IPv6" "SSH-2.0-OpenSSH" \
    "$(tcp_in 2001:db8:200::1 22 2001:db8:7::7)"
expect_in "taken back: the API answers on IPv4" "PRISMA-API" \
    "$(tcp_in 10.200.0.1 50051 198.51.100.7)"
afresh "taken back: the gateway learns the box's IPv6 afresh within 5 s" \
    mg veth-mg-o 2001:db8:200::1 2001:db8:200::2
expect_in "taken back: SNMP answers on IPv4" "STRING:" \
    "$(snmp_in 10.200.0.1 198.51.100.7)"
expect_in "taken back: SNMP answers on IPv6" "STRING:" \
    "$(snmp_in 2001:db8:200::1 2001:db8:7::7)"

# ----------------------------------------- 2. the same commit, confirmed
echo; echo "=== 2. the same commit, confirmed, with a session open across it ==="
# The administrator's session, opened on the main table before the commit and
# asked again after it: the session the commit is typed in.
rm -f /tmp/sess-go /tmp/sess.out
ip netns exec mg python3 - > /tmp/sess.out 2>&1 <<'PY' &
import os, socket, time
s = socket.create_connection(("10.200.0.1", 7007), 5, ("198.51.100.7", 0))
s.sendall(b"before\n"); print("1", s.recv(64).decode().strip(), flush=True)
while not os.path.exists("/tmp/sess-go"):
    time.sleep(0.2)
s.settimeout(5)
try:
    s.sendall(b"after\n"); print("2", s.recv(64).decode().strip(), flush=True)
except OSError as e:
    print("2 lost:", e, flush=True)
PY
SESS=$!
sleep 1
move "commit confirmed 5"
$HX -c "configure" -c "commit" > /tmp/confirm.log 2>&1
sed 's/^/  confirm: /' /tmp/confirm.log | tail -2
sleep 3
touch /tmp/sess-go; wait "$SESS"
sed 's/^/  session: /' /tmp/sess.out
expect_in "commit: the session opened before it still answers" "2 after" \
    "$(cat /tmp/sess.out)"
expect "commit: no commit confirmed is pending" test ! -s "$PRISMA_CONFIRM"
reach commit
SHOWMG=$($HX -c "show system management" 2>&1)
echo "$SHOWMG" | sed 's/^/  /'
if [ "$MODE" != off ]; then
  expect_in "commit: show system management says where it is configured" \
      "mgmt, table 100" "$SHOWMG"
  expect_in "and the box's own traffic in effect" "by table 100" "$SHOWMG"
  expect_in "and the interface in it" "vppmg0" "$SHOWMG"
fi

# ------------------------------------------- 3. a commit that does not touch it
echo; echo "=== 3. another commit that does not touch management ==="
$HX -c "configure" -c "router static" -c "192.0.2.0/24 next-hop 10.0.0.2" \
    -c "exit" -c "commit" > /tmp/c3.log 2>&1
sed 's/^/  c3: /' /tmp/c3.log | tail -3
sleep 2
reach recommit
# THE LIST, CHANGED INSIDE THE VRF (#593): sshd reloaded on management's
# addresses once they are the VRF's - one address, then both again. Each is
# a bind of an address the main table does not hold, which nonlocal bind
# makes possible and the commit check accepts because management is that
# VRF. Off has no statement, so the check refuses the IPv6 address back.
echo "  -- ssh listen-address, changed in the VRF"
$HX -c "configure" -c "ssh listen-address 10.200.0.1" -c "commit" \
    > /tmp/c3s1.log 2>&1
$HX -c "configure" -c "commit" > /dev/null 2>&1
sed 's/^/    one: /' /tmp/c3s1.log | tail -2
expect_in "ssh in the VRF: one address, SSH answers on it" "SSH-2.0-OpenSSH" \
    "$(tcp_in 10.200.0.1 22 198.51.100.7)"
expect_not_in "ssh in the VRF: and no longer on the IPv6 one" "SSH-2.0" \
    "$(tcp_in 2001:db8:200::1 22 2001:db8:7::7)"
$HX -c "configure" -c "ssh listen-address 10.200.0.1 2001:db8:200::1" \
    -c "commit" > /tmp/c3s2.log 2>&1
$HX -c "configure" -c "commit" > /dev/null 2>&1
sed 's/^/    both: /' /tmp/c3s2.log | tail -3
expect_not_in "ssh in the VRF: the list back is accepted" "error" \
    "$(cat /tmp/c3s2.log)"
expect_in "ssh in the VRF: SSH answers on IPv4" "SSH-2.0-OpenSSH" \
    "$(tcp_in 10.200.0.1 22 198.51.100.7)"
expect_in "ssh in the VRF: and on IPv6 again" "SSH-2.0-OpenSSH" \
    "$(tcp_in 2001:db8:200::1 22 2001:db8:7::7)"
$HX -c "save" 2>&1 | tail -1 | sed 's/^/  /'

# -------------------------------------------- 4. system dataplane restart
echo; echo "=== 4. system dataplane restart, as prisma-hot-card does it ==="
stop_box
tuner
start_box
way_back
$HX --load "$PRISMA_CONFIG" > /tmp/load4.log 2>&1
sed 's/^/  load: /' /tmp/load4.log | tail -4
sleep 4
reach "dataplane restart"

# ------------------------------------------------ 5. the containers restart
echo; echo "=== 5. the containers restarted behind the VTY: --reapply ==="
stop_box
start_box
$HX --reapply > /tmp/reapply5.log 2>&1
sed 's/^/  reapply: /' /tmp/reapply5.log | tail -4
sleep 4
reach "containers restart"

# ------------------------------------------------------------------ 6. a boot
echo; echo "=== 6. a boot: the tuner, the host's services, then --load ==="
stop_box
kill "$SSHD_PID" 2>/dev/null; SSHD_PID=
systemctl disable --now snmpd
kill "$(cat /run/api-lab.pid 2>/dev/null)" 2>/dev/null
ip link del mgmt 2>/dev/null
for f in "" -6; do
  ip $f rule del pref 31000 2>/dev/null; ip $f rule del pref 31001 2>/dev/null
done
sysctl -q -w net.ipv4.tcp_l3mdev_accept=0 net.ipv4.udp_l3mdev_accept=0 \
    net.ipv4.ip_nonlocal_bind=0 net.ipv6.ip_nonlocal_bind=0 \
    net.ipv6.conf.all.keep_addr_on_down=0
tuner
# systemd starts these before prisma-routing.service: the addresses they name
# are not on any interface yet (#591).
systemctl restart snmpd
start_sshd
systemctl restart prisma-routing-api-config.service > /dev/null 2>&1
echo "  snmpd before the load: $(tail -2 /tmp/snmpd.log | tr '\n' ' ')"
echo "  sshd before the load: $(tail -1 /tmp/sshd.log)"
start_box
way_back
$HX --load "$PRISMA_CONFIG" > /tmp/load6.log 2>&1
sed 's/^/  load: /' /tmp/load6.log | tail -4
sleep 4
reach boot

# ---------------------------------------------- 7. a boot without the tuner
echo; echo "=== 7. a boot whose tuner did not run: --load alone ==="
for f in "" -6; do
  ip $f rule del pref 31000 2>/dev/null; ip $f rule del pref 31001 2>/dev/null
done
sysctl -q -w net.ipv4.tcp_l3mdev_accept=0 net.ipv4.udp_l3mdev_accept=0
$HX --load "$PRISMA_CONFIG" > /tmp/load7.log 2>&1
sed 's/^/  load: /' /tmp/load7.log | tail -4
sleep 3
reach "load alone"

if [ "$MODE" != off ]; then
  echo; echo "=== 8. 'no vrf' gives management back to the main table ==="
  $HX -c "configure" -c "system" -c "management" -c "no vrf" -c "exit" \
      -c "exit" -c "commit" > /tmp/c8.log 2>&1
  sed 's/^/  c8: /' /tmp/c8.log | tail -3
  expect_not_in "no vrf: the rules are gone" "lookup 100" "$(ip rule show 2>&1)"
  expect_in "no vrf: and l3mdev accept is off" "= 0" \
      "$(sysctl net.ipv4.tcp_l3mdev_accept 2>&1)"

  # The same move for a tenant's card (#540): one dataplane bind for every
  # VRF, and the repair is in it, not in anything management does (#594).
  echo; echo "=== 9. a tenant VRF: the upstream card into vrf cust ==="
  $HX -c "configure" -c "vrf cust table 200" -c "interface host-veth-up" \
      -c "vrf 200" -c "exit" -c "commit" > /tmp/c9.log 2>&1
  sed 's/^/  c9: /' /tmp/c9.log | tail -3
  expect_in "tenant: the card's IPv6 is in table 200" "ip6 table-id 200" \
      "$(vppctl show int addr host-veth-up 2>&1)"
  afresh "tenant: the upstream learns the card's IPv6 afresh within 5 s" \
      up veth-up-o 2001:db8:10::1 2001:db8:10::2

  # THE TENANT'S VRF TAKEN AWAY AND GIVEN BACK (#595): what phases 1 and 2
  # do to mgmt, done to cust - one device removal for every VRF, so the
  # waiting is in it and not in anything management does. A route of each
  # family in `vrf cust`, then a `commit confirmed` removing the VRF, its
  # card and its routes, taken back by the box, which makes cust again. The
  # main table's routes through the upstream go first: their next hop left
  # with the card in phase 9, and the commit check refuses a route on no
  # connected network.
  echo; echo "=== 10. the tenant VRF taken away, and the box giving it back ==="
  $HX -c "configure" -c "router static" -c "no 0.0.0.0/0 next-hop 10.0.0.2" \
      -c "no ::/0 next-hop 2001:db8:10::2" \
      -c "no 192.0.2.0/24 next-hop 10.0.0.2" -c "exit" -c "vrf cust" -c "ip route 198.18.0.0/15 10.0.0.2" \
      -c "ipv6 route 2001:db8:99::/48 2001:db8:10::2" -c "exit-vrf" \
      -c "commit" > /tmp/c10.log 2>&1
  grep -v "^entering" /tmp/c10.log | sed 's/^/  c10: /' | tail -4
  sleep 3
  expect_in "tenant: its IPv4 route is in table 200" "via 10.0.0.2" \
      "$(ip route show table 200 198.18.0.0/15 2>&1)"
  expect_in "tenant: and its IPv6 one" "via 2001:db8:10::2" \
      "$(ip -6 route show table 200 2001:db8:99::/48 2>&1)"
  $HX -c "configure" -c "no vrf cust table 200" -c "interface host-veth-up" \
      -c "vrf 0" -c "exit" -c "vrf cust" -c "no ip route 198.18.0.0/15 10.0.0.2" \
      -c "no ipv6 route 2001:db8:99::/48 2001:db8:10::2" -c "exit-vrf" \
      -c "commit confirmed 5" > /tmp/c10b.log 2>&1
  sed 's/^/  c10b: /' /tmp/c10b.log | grep -v "^  c10b: entering" | tail -3
  sleep 2
  expect_not_in "tenant: taken away, cust is gone" "cust" \
      "$(ip -o link show type vrf 2>&1)"
  python3 - <<'EOF'
import sys
sys.path.insert(0, "/src/src/prismash")
import prismash
c = prismash.ConfirmPending(prismash.CONFIRM_PATH)
p = c.read()
c.arm(p["deadline"] - 301, p["config"], p["minutes"], p["attempts"])
EOF
  $HX --confirm-expired > /tmp/expired10.log 2>&1
  sed 's/^/  expired: /' /tmp/expired10.log | head -2
  sleep 3
  ip route show table 200 2>&1 | sed 's/^/    table 200: /'
  ip -6 route show table 200 2>&1 | grep -v "^local\|^multicast\|fe80" \
      | sed 's/^/    table 200: /'
  expect_in "tenant: given back, the card is in cust again" "master cust" \
      "$(ip -o link show vppup0 2>&1)"
  expect_in "tenant: given back, its IPv4 route is in table 200 again" \
      "via 10.0.0.2" "$(ip route show table 200 198.18.0.0/15 2>&1)"
  expect_in "tenant: and its IPv6 one (#595)" "via 2001:db8:10::2" \
      "$(ip -6 route show table 200 2001:db8:99::/48 2>&1)"
  afresh "tenant: given back, the upstream learns the card's IPv6 afresh" \
      up veth-up-o 2001:db8:10::1 2001:db8:10::2

  # THE BOOT'S SNMP, RECORDED ONCE AND ONLY WHEN IT IS TRUE (#601). The load
  # applies SNMP before the interfaces whenever snmpd.conf is not the file
  # this release writes (#523) - forced here by a line appended to it - and
  # with nonlocal bind off snmpd cannot bind an address no interface has
  # yet. 10.40.200.166 recorded "the SNMP settings were refused" at every
  # boot that way, then "applied once the interfaces were up", the same
  # file. Phases 4 to 7 never see it: management in its VRF turns nonlocal
  # bind on. A boot after phase 8 left the card in vrf mgmt with no
  # management statement:
  #   11. the card still in the VRF: snmpd cannot bind its addresses from
  #       the main table, ever - a real refusal, recorded ONCE, after the
  #       retry (0.152.11 recorded it twice, the first before the retry);
  # The other half - an address that is only not up YET, the shape
  # 10.40.200.166 shows at every boot - is not reproduced here: stopping
  # and starting this lab's dataplane does not recreate the boot's order
  # (the load's SNMP apply succeeded at once, or did not run, in two
  # attempts on 2026-10-12). It is held by test_prismash_apply and proven
  # on .166 itself after the release.
  boot11() {  # <phase>: a boot, snmpd started first, then --load
    $HX -c "save" 2>&1 | tail -1 | sed 's/^/  /'
    stop_box
    sysctl -q -w net.ipv4.ip_nonlocal_bind=0 net.ipv6.ip_nonlocal_bind=0
    echo "# written by an earlier release" >> /etc/snmp/snmpd.conf
    systemctl restart snmpd
    EV11=$(wc -l < "$PRISMA_EVENTS" 2>/dev/null || echo 0)
    start_box
    $HX --load "$PRISMA_CONFIG" > "/tmp/load$1.log" 2>&1
    grep -i "snmp" "/tmp/load$1.log" | sed 's/^/  load: /'
    EVENTS11=$(tail -n +"$((EV11 + 1))" "$PRISMA_EVENTS" 2>/dev/null \
               | grep '"subject":"snmp"')
    echo "$EVENTS11" | grep . | sed 's/.*"text":"\([^"]*\)".*/  event: \1/'
  }
  echo; echo "=== 11. a boot whose snmpd cannot bind, the card in a VRF ==="
  boot11 11
  expect_in "real refusal: the load says it, as a warning" \
      "warning: snmp" "$(cat /tmp/load11.log)"
  expect "real refusal: recorded once, after the retry (#601)" \
      [ "$(echo "$EVENTS11" | grep -c 'SNMP settings were refused')" = 1 ]
  expect_not_in "real refusal: and nothing says it was applied" \
      "SNMP settings were applied" "$EVENTS11"

fi

verdict
