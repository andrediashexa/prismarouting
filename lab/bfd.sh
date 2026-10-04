#!/bin/sh
# OFF-RED-AT-LEAST: 10
# (the floor: what the off run of 2026-10-04 measured red)
# BFD (#559): a BGP session and a static route watched by BFD, configured
# through the VTY, and a link failure that keeps the link up detected in the
# time the configuration says.
#
#   sh tools/lab.sh bfd          -> LAB PASSED
#   sh tools/lab.sh bfd off      -> LAB FAILED (no `bfd` anywhere: the same
#                                   cut is seen only by BGP's hold timer, and
#                                   the static route is never withdrawn)
#
#   [netns peer] FRR: bgpd + bfdd, AS 65001
#     dum-bgp  198.51.100.0/24 -- BGP -->
#     dum-st   203.0.113.0/24  (the static route's destination)
#     veth-out 10.0.0.2 ------(veth)------ veth-vpp -> VPP -> vppwan0 10.0.0.1
#                                                     [default] Prisma Routing
#
# THE FAILURE IS SILENT, and that is the point of BFD. `ip link set down` on
# the peer would take the box's interface down too and every protocol would
# notice at once; what BFD is for is the neighbour that stops forwarding with
# its link up - a frozen line card, a switch in the middle that lost the
# VLAN. So the cut is `netem loss 100%` on the peer's side of the wire: the
# carrier stays up, packets stop.
#
# WHAT RUNS IS THE PRODUCT'S DECISION. bfdd is turned on by the routing
# container's entrypoint (appliance/frr-entrypoint), not by the installer -
# a patch cannot change the installer - so this lab seeds a fakeroot with the
# distribution's daemons file, runs the installer into it, runs the
# entrypoint against it, and starts exactly what is then enabled. Taking
# bfdd out of the entrypoint turns this lab red.
#
# Timings are measured and printed, then compared with the configured detect
# time (100 ms x 3 = 300 ms): bfdd's own state, and each route leaving the
# DATAPLANE'S table, which is what forwards.
set -u
LAB_T0=$(date +%s)
MODE=${1:-on}
. /src/lab/lib.sh
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/verdict.sh

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out
ip netns exec peer ip link add dum-bgp type dummy
ip netns exec peer ip addr add 198.51.100.1/24 dev dum-bgp
ip netns exec peer ip link set dum-bgp up
ip netns exec peer ip link add dum-st type dummy
ip netns exec peer ip addr add 203.0.113.1/24 dev dum-st
ip netns exec peer ip link set dum-st up

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
wait_vpp || exit 1
use_binary_api
HX="python3 /src/src/prismash/prismash.py"
vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
$HX -c "configure" -c "interface host-veth-vpp" -c "linux-name vppwan0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'
vppctl set interface ip address host-veth-vpp 10.0.0.1/24

# ---------------- which daemons: the installer, then the entrypoint ----------
mkdir -p /var/run/frr /etc/frr
install -D -m 0644 /etc/frr/daemons /tmp/fakeroot/etc/frr/daemons
expect "the distribution ships bfdd disabled" \
  sh -c "grep -q '^bfdd=no$' /tmp/fakeroot/etc/frr/daemons"
DESTDIR=/tmp/fakeroot sh /src/appliance/install.sh > /tmp/install-sh.log 2>&1 \
  || { echo "  install.sh stopped early:"; tail -5 /tmp/install-sh.log | sed 's/^/    /'; }
# The entrypoint as the container runs it, with FRR's own start stubbed: it
# turns bfdd on and starts nothing, and then exits because no watchfrr runs.
PRISMA_FRR_ETC=/tmp/fakeroot/etc/frr PRISMA_FRR_RUN=/tmp/fakeroot/run \
  FRRINIT=/bin/true sh /src/appliance/frr-entrypoint > /tmp/entry.log 2>&1
ENABLED=$(awk -F= '/^[a-z0-9]+d=yes$/ {print $1}' /tmp/fakeroot/etc/frr/daemons | tr '\n' ' ')
echo "  the product enabled: $ENABLED"
expect "the routing container's entrypoint turns bfdd on" \
  sh -c "grep -q '^bfdd=yes$' /tmp/fakeroot/etc/frr/daemons"
for d in zebra $ENABLED; do printf 'hostname prisma-routing\n' > "/etc/frr/$d.conf"; done
chown -R frr:frr /var/run/frr /etc/frr
# shellcheck disable=SC2086
start_frr $ENABLED

# ---------------- the peer ----------------
mkdir -p /var/run/frr/peer /etc/frr/peer
: > /etc/frr/peer/vtysh.conf
printf 'hostname upstream\n' > /etc/frr/peer/zebra.conf
if [ "$MODE" = off ]; then
  PEER_BFD=""; PEER_NB_BFD=""
else
  PEER_BFD='bfd
 peer 10.0.0.1 interface veth-out
  receive-interval 100
  transmit-interval 100
 exit
exit'
  PEER_NB_BFD=" neighbor 10.0.0.1 bfd"
fi
printf 'hostname upstream\n%s\n' "$PEER_BFD" > /etc/frr/peer/bfdd.conf
cat > /etc/frr/peer/bgpd.conf <<EOF
hostname upstream
router bgp 65001
 bgp router-id 10.0.0.2
 no bgp ebgp-requires-policy
 neighbor 10.0.0.1 remote-as 65000
 neighbor 10.0.0.1 timers 10 30
$PEER_NB_BFD
 address-family ipv4 unicast
  network 198.51.100.0/24
 exit-address-family
exit
EOF
chown -R frr:frr /var/run/frr/peer /etc/frr/peer
for d in zebra bfdd bgpd; do
  ip netns exec peer /usr/lib/frr/$d -d -N peer -A 127.0.0.1 -f /etc/frr/peer/$d.conf 2>/dev/null
done

# ---------------- the box, through the VTY ----------------
echo
echo "=========== BGP and a static route over BFD, through the VTY ==========="
# The batch as positional arguments, so the off mode is the same batch with
# every `bfd` taken out and nothing else changed.
set -- -c configure
[ "$MODE" = off ] || set -- "$@" -c bfd -c "profile lab" \
  -c "receive-interval 100" -c "transmit-interval 100" \
  -c "detect-multiplier 3" -c exit \
  -c "peer 10.0.0.2 interface vppwan0" -c "profile lab" -c exit -c exit
set -- "$@" -c "router bgp 65000" -c "bgp router-id 10.0.0.1" \
  -c "no bgp ebgp-requires-policy" -c "neighbor 10.0.0.2 remote-as 65001" \
  -c "neighbor 10.0.0.2 timers 10 30"
[ "$MODE" = off ] || set -- "$@" -c "neighbor 10.0.0.2 bfd profile lab"
ST="203.0.113.0/24 next-hop 10.0.0.2"
[ "$MODE" = off ] || ST="$ST bfd profile lab"
# And a default route with no BFD, the shape #570 was found on: every removal
# below must leave it where it is.
set -- "$@" -c exit -c "router static" -c "$ST" \
  -c "0.0.0.0/0 next-hop 10.0.0.2" -c exit -c commit
CFG=$($HX "$@" 2>&1)
echo "$CFG" | tail -4 | sed 's/^/  /'
expect_in "the VTY commits it" "commit complete" "$CFG"
RUN=$(vtysh -c 'show running-config' 2>/dev/null)
expect_in "FRR holds the BFD session" "peer 10.0.0.2 interface vppwan0" "$RUN"
expect_in "and the BGP neighbour asks for BFD with the profile" \
  "neighbor 10.0.0.2 bfd profile lab" "$RUN"
expect_in "and the static route does" \
  "ip route 203.0.113.0/24 10.0.0.2 bfd profile lab" "$RUN"

# The session state, as the VTY reads it.
bfd_up() { $HX -c "show bfd peers" 2>/dev/null | grep -A1 "^  10.0.0.2 " | grep -q "^      up "; }
in_fib() { vppctl show ip fib "$1" 2>/dev/null | grep -q "^$1"; }
bgp_est() { vtysh -c "show bgp neighbor 10.0.0.2 json" 2>/dev/null | grep -q '"bgpState":"Established"'; }
wait_for() {  # <seconds> <cmd...>
  _s=$1; shift; _n=0
  until "$@" || [ "$_n" -ge "$((_s * 2))" ]; do sleep 0.5; _n=$((_n + 1)); done
  "$@"
}

wait_for 40 bfd_up
expect "the BFD session to the neighbour comes up (show bfd peers)" bfd_up
wait_for 60 bgp_est
expect "BGP is Established" bgp_est
wait_for 30 in_fib 198.51.100.0/24
expect "the BGP route is in the dataplane's table" in_fib 198.51.100.0/24
wait_for 30 in_fib 203.0.113.0/24
expect "and the static route" in_fib 203.0.113.0/24
SHOWPEER=$($HX -c "show bfd peer 10.0.0.2" 2>&1)
echo "$SHOWPEER" | sed 's/^/    /'
expect_in "show bfd peer reads the profile's 100 ms" "sends every 100 ms" "$SHOWPEER"
expect_in "and the detect time it gives" "down after 300 ms" "$SHOWPEER"

# ---------------- the silent failure ----------------
echo
echo "=========== the neighbour stops answering, its link up ==========="
python3 - <<'EOF' > /tmp/bfd-cut.txt 2>&1
import json, subprocess, time
def run(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout
def bfd_down():
    try:
        p = json.loads(run("vtysh", "-c", "show bfd peer 10.0.0.2 interface vppwan0 json"))
        return p.get("status") != "up"
    except ValueError:
        return False
def gone(prefix):
    # Any line, the way in_fib() reads it: the first line `show ip fib`
    # prints is the table's header, and the first version of this read only
    # that and measured both routes "gone" 15 ms after the cut - before bfdd
    # had seen anything.
    out = run("vppctl", "show", "ip", "fib", prefix)
    return not any(l.startswith(prefix) for l in out.splitlines())
t0 = time.monotonic()
subprocess.run(["ip", "netns", "exec", "peer", "tc", "qdisc", "add", "dev",
                "veth-out", "root", "netem", "loss", "100%"], check=True)
seen = {}
while time.monotonic() - t0 < 12 and len(seen) < 3:
    now = int((time.monotonic() - t0) * 1000)
    if "bfd" not in seen and bfd_down():
        seen["bfd"] = now
    if "bgp" not in seen and gone("198.51.100.0/24"):
        seen["bgp"] = now
    if "static" not in seen and gone("203.0.113.0/24"):
        seen["static"] = now
    time.sleep(0.02)
for k in ("bfd", "bgp", "static"):
    print(f"{k}={seen.get(k, 'never')}")
EOF
cat /tmp/bfd-cut.txt | sed 's/^/  measured, ms after the cut: /'
cut() { sed -n "s/^$1=//p" /tmp/bfd-cut.txt; }
num() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; [ "$1" -le "$2" ]; }
expect "bfdd saw the session down within 1 s of the cut ($(cut bfd) ms; detect 300 ms)" \
  num "$(cut bfd)" 1000
expect "the BGP route left the dataplane within 2 s ($(cut bgp) ms; hold time 30 s)" \
  num "$(cut bgp)" 2000
expect "the static route left the dataplane within 2 s ($(cut static) ms)" \
  num "$(cut static)" 2000

echo
echo "=========== and comes back ==========="
ip netns exec peer tc qdisc del dev veth-out root
wait_for 40 bfd_up
expect "the session comes back up" bfd_up
wait_for 60 in_fib 203.0.113.0/24
expect "the static route is back in the dataplane" in_fib 203.0.113.0/24
wait_for 90 in_fib 198.51.100.0/24
expect "and the BGP route" in_fib 198.51.100.0/24

# ---------------- taking it away again (#570) ----------------
# On 10.40.200.166 (0.150.8) `no bfd` and `no peer ...` failed `commit check`:
# the VTY deleted the block's header line and left its body behind, and FRR
# refused the file. Each removal here has to commit, leave FRR without what
# was removed, and leave the routes - the default one included - in the
# dataplane's table.
if [ "$MODE" != off ]; then
  echo
  echo "=========== and each piece of it taken away (#570) ==========="
  # In FRR's table and not the dataplane's: this lab's container has a
  # kernel default route of docker's at distance 0, so the static default is
  # never FRR's best and never reaches the FIB here. What #570 asked is
  # whether the commit withdraws it, which FRR's table answers.
  default_via() { vtysh -c "show ip route 0.0.0.0/0" 2>/dev/null \
                    | grep -A6 'Known via "static"' | grep -q "10.0.0.2"; }
  routes_intact() {  # <what was just removed>
    expect "after $1: the static default route is still in FRR" default_via
    expect "after $1: and so is the static route" in_fib 203.0.113.0/24
  }
  frr_run() { vtysh -c 'show running-config' 2>/dev/null; }

  OUT=$($HX -c configure -c "router bgp 65000" \
            -c "no neighbor 10.0.0.2 bfd" -c exit -c commit 2>&1)
  expect_in "BFD off a BGP neighbour commits" "commit complete" "$OUT"
  expect_not_in "and FRR's neighbour has no bfd left, profile line included" \
    "neighbor 10.0.0.2 bfd" "$(frr_run)"
  routes_intact "the neighbour's bfd"

  OUT=$($HX -c configure -c "router static" \
            -c "203.0.113.0/24 next-hop 10.0.0.2" -c exit -c commit 2>&1)
  expect_in "BFD off a static route (typed again without it) commits" \
    "commit complete" "$OUT"
  expect_in "and FRR holds the route without bfd" \
    "ip route 203.0.113.0/24 10.0.0.2" "$(frr_run | grep '^ip route 203')"
  expect_not_in "and only that once" "bfd profile lab" \
    "$(frr_run | grep '^ip route 203')"
  routes_intact "the route's bfd"

  OUT=$($HX -c configure -c bfd -c "no peer 10.0.0.2 interface vppwan0" \
            -c exit -c commit 2>&1)
  expect_in "a peer taken away commits" "commit complete" "$OUT"
  expect_not_in "and FRR has no such session" \
    "peer 10.0.0.2 interface vppwan0" "$(frr_run)"
  expect_in "and the profile it used is still there" " profile lab" "$(frr_run)"
  routes_intact "the peer"

  OUT=$($HX -c configure -c bfd -c "no profile lab" -c exit -c commit 2>&1)
  expect_in "a profile taken away commits" "commit complete" "$OUT"
  expect_not_in "and FRR has no such profile" "profile lab" "$(frr_run)"
  routes_intact "the profile"

  OUT=$($HX -c configure -c bfd -c "profile again" -c "detect-multiplier 5" \
            -c exit -c "peer 10.0.0.2 interface vppwan0" -c exit -c exit \
            -c commit 2>&1)
  expect_in "a new profile and peer commit" "commit complete" "$OUT"
  OUT=$($HX -c configure -c "no bfd" -c commit 2>&1)
  expect_in "the whole bfd block taken away commits" "commit complete" "$OUT"
  # Not even an empty one: frr-reload re-opens the block to remove its
  # sessions one by one, and the commit takes that leftover away.
  expect_not_in "and FRR has no bfd block, not even an empty one" "bfd" \
    "$(frr_run | grep -E '^bfd|^ peer|^ profile')"
  routes_intact "the whole block"
fi

echo "  -- lab took $(( $(date +%s) - LAB_T0 )) s"
verdict
