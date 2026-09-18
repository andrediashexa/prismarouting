#!/bin/sh
# A virtual router on the dataplane interface, and a takeover (#7).
#
#   sh tools/lab.sh vrrp          -> LAB PASSED
#   sh tools/lab.sh vrrp off      -> LAB FAILED (nothing is configured)
#
#   br-lan    the subscriber side: two boxes and a client, as a pair is cabled
#   br-up     the upstream side, where the reply has to come back through
#   a, b      one VPP each, own namespace, own API segment
#   client    a CPE, pointed at an address neither box owns
#   net       what the client is talking to, routed back to an address
#             neither box owns either
#
# WHY THE VIRTUAL ROUTER IS THE DATAPLANE'S, which is the decision #7 turns
# on. FRR ships VRRP and keepalived is the other candidate, and both run in
# Linux - which on this box means the linux_cp TAP. The TAP is not where the
# packets are: the dataplane interface is. A pair built that way moves an
# address in Linux while the VPP FIB, the CGNAT feature arcs and the ARP that
# a CPE actually asks go on living on the box that died. VPP's own vrrp
# plugin, RFC 5798, has shipped on every box this product ever installed with
# no virtual router on it because nothing asked for one. This is what asks.
#
# WHAT THIS LAB ASSERTS THAT NO UNIT SUITE CAN. The unit suites read the
# statement and the refusals; what they cannot say is whether a CPE that
# never flushed its ARP cache still reaches its gateway thirty seconds after
# the box holding it was killed. Between those two facts sit the election,
# the virtual MAC, the gratuitous ARP, and the interface tracking - and every
# one of them is a place a design can be correct on paper and wrong on wire.
#
# NOTHING HERE FLUSHES THE CLIENT'S ARP CACHE, at any point, and nothing here
# puts the virtual address on a box or moves it. A CPE holds a stale entry
# for its gateway across a failover exactly as this namespace does. A lab
# that flushed it would be performing the product's work and would stay green
# on a product that never sends a gratuitous ARP. lab/ha-failover.sh states
# the same rule at length and lab/lcp.sh carries the lesson it came from.
#
# THE SERVICE IS NOT A PING AT THE VIRTUAL ADDRESS. The client's traffic goes
# THROUGH it: a CPE sends to the virtual MAC and whichever box is master
# routes the packet on and the reply back. That is what a gateway is for, and
# it is also what the RFC leaves working - a router that does not own the
# virtual address does not answer ICMP for it unless accept mode is on, and
# accept mode is one flag this product's API client cannot encode alongside
# preempt (the measurement is in prismash's set_vrrp). A lab built on pinging
# the address would be measuring a flag; this one measures the service.
#
# The failover this lab does NOT measure is the CGNAT one: a subscriber's
# translated flow surviving the takeover with the same public address is #9,
# in lab/ha-failover.sh, which is red until the pair exists. This is the
# mechanism under it.
# OFF-RED-AT-LEAST: 14
set -u
. /src/lab/lib.sh
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

MODE=${1:-on}
VIP_IN=100.64.3.254         # the gateway the client is pointed at
VIP_OUT=198.18.0.254        # the next hop the upstream sends the client to
VMAC=00:00:5e:00:01:01      # RFC 5798 section 7.3, for VRID 1
CLIENT=100.64.3.10
UPSTREAM=198.18.0.2
# VRRP's master-down interval at the default one-second advertisement is about
# 3.6 s. Ten is generous by a factor of nearly three and still short enough
# that a design which "fails over" by being restarted cannot pass.
TAKEOVER_WINDOW=10

# -------------------------------------------------------------------- the LAN

ip netns add a 2>/dev/null
ip netns add b 2>/dev/null
ip netns add client 2>/dev/null
ip netns add net 2>/dev/null
ip link add br-lan type bridge 2>/dev/null
ip link add br-up  type bridge 2>/dev/null
ip link set br-lan up
ip link set br-up up

leg() {   # <bridge side> <namespace side> <namespace> <address|empty>
  ip link add "$1" type veth peer name "$2" 2>/dev/null
  ip link set "$2" netns "$3"
  ip netns exec "$3" ip link set lo up
  ip netns exec "$3" ip link set "$2" up
  [ -n "$4" ] && ip netns exec "$3" ip addr add "$4" dev "$2"
  ip link set "$1" up
}
leg client-br client0 client "$CLIENT/24"
ip link set client-br master br-lan
# The boxes' ports carry no Linux address: VPP takes them and owns the
# addressing, which is how an appliance is built (docs/appliance.md).
leg a-lan-br a-lan a ""
ip link set a-lan-br master br-lan
leg b-lan-br b-lan b ""
ip link set b-lan-br master br-lan
leg net-br net0 net "$UPSTREAM/24"
ip link set net-br master br-up
# The upstream port of each box. It carries a virtual router of its own, and
# it is also the port this lab takes down: a pulled uplink leaves the box
# alive and still holding the inside address, which is the failure a design
# without interface tracking does not survive.
leg a-up-br a-up a ""
ip link set a-up-br master br-up
leg b-up-br b-up b ""
ip link set b-up-br master br-up

# ------------------------------------------------------------------ two boxes
#
# One container, two dataplanes, each with its own namespace, runtime
# directory and shared-memory prefix - the same arrangement lab/ha-failover.sh
# uses, and for the same reason: that is the whole of what makes them two
# boxes rather than one process with four interfaces.
startup_for() {   # <box> <api prefix> -> path of the generated file
  mkdir -p "/run/vpp/$1"
  sed -e "s#cli-listen /run/vpp/cli.sock#runtime-dir /run/vpp/$1\n  cli-listen /run/vpp/$1/cli.sock#" \
      -e "s#^api-segment {#api-segment {\n  prefix $2#" \
      "$(startup_conf test)" > "/tmp/startup-$1.conf"
  echo "/tmp/startup-$1.conf"
}

vb() {   # <box> <vppctl arguments...>
  _box=$1; shift
  vppctl -s "/run/vpp/$_box/cli.sock" "$@" | tr -d '\r'
}

start_box() {   # <box> <api prefix>
  ip netns exec "$1" vpp -c "$(startup_for "$1" "$2")" > "/tmp/vpp-$1.log" 2>&1 &
  _n=0
  until vppctl -s "/run/vpp/$1/cli.sock" show version >/dev/null 2>&1 \
        || [ "$_n" -ge 30 ]; do sleep 1; _n=$((_n + 1)); done
  vppctl -s "/run/vpp/$1/cli.sock" show version >/dev/null 2>&1 || {
    echo "VPP did not start"; tail -20 "/tmp/vpp-$1.log"; exit 1; }
  cat > "/usr/local/bin/vppctl-$1" <<EOF
#!/bin/sh
exec vppctl -s /run/vpp/$1/cli.sock "\$@"
EOF
  cat > "/usr/local/bin/vat2-$1" <<EOF
#!/bin/sh
exec vat2 -s $2 "\$@"
EOF
  chmod +x "/usr/local/bin/vppctl-$1" "/usr/local/bin/vat2-$1"
}

# Everything the box needs that is NOT the feature under test: its ports, its
# own address, the upstream port. Typed at the dataplane because none of it is
# what this lab is about.
address_box() {   # <box> <lan address/len> <upstream address/len>
  vb "$1" create host-interface name "$1-lan" >/dev/null
  vb "$1" create host-interface name "$1-up"  >/dev/null
  vb "$1" set interface state "host-$1-lan" up
  vb "$1" set interface state "host-$1-up" up
  vb "$1" set interface ip address "host-$1-lan" "$2"
  vb "$1" set interface ip address "host-$1-up" "$3"
}

# THE FEATURE, typed at the VTY exactly as an operator types it. A lab that
# configured the box with vppctl would prove the plugin and say nothing about
# the product being able to ask for it, which is the shape of #99, #168 and
# #182 and is why every lab here goes through prismash.
hx() {   # <box> <prismash arguments...>
  _box=$1; shift
  VPPCTL="/usr/local/bin/vppctl-$_box" VAT2="/usr/local/bin/vat2-$_box" \
  PRISMA_API_SEGMENT="/dev/shm/hx$_box-global_vm" \
  PRISMA_CONFIG="/etc/prisma-routing/$_box.conf" \
  python3 /src/src/prismash/prismash.py "$@" 2>&1
}

# Two virtual routers per box, one on each side, which is how a pair is
# actually deployed: an address that moves on the subscriber side and nothing
# on the upstream side is a takeover the return traffic does not follow.
#
# The LAN router TRACKS the upstream port. That is the pulled-uplink failure:
# box a is alive, its subscriber port is up, and it can reach nothing. Without
# tracking it goes on holding the gateway address and the service is down with
# a healthy box standing beside it.
configure_vrrp() {   # <box> <priority> <decrement>
  hx "$1" -c "configure" \
     -c "interface host-$1-lan" \
     -c "vrrp 1 address $VIP_IN" \
     -c "vrrp 1 priority $2" \
     -c "vrrp 1 track host-$1-up decrement $3" \
     -c "exit" \
     -c "interface host-$1-up" \
     -c "vrrp 2 address $VIP_OUT" \
     -c "vrrp 2 priority $2" \
     -c "exit" -c "commit"
}

echo "=== two dataplanes, one LAN, and a client that is nobody's business ==="
start_box a hxa
start_box b hxb
address_box a 100.64.3.252/24 198.18.0.11/24
address_box b 100.64.3.251/24 198.18.0.12/24
vb a show version | sed 's/^/  a: /'
vb b show version | sed 's/^/  b: /'
echo "  the plugin the answer is built on, and RFC it implements:"
vb a show plugins | grep -i vrrp | sed 's/^/    /'

echo
echo "=== the pair is asked for, through the VTY ==="
if [ "$MODE" = "off" ]; then
  echo "  off: no virtual router is configured on either box, so every check"
  echo "  below about the service address has to go red. That is what this"
  echo "  mode is for."
else
  # 200 against 100, and a decrement of 150 on each: a box whose uplink is
  # down advertises 50 and loses to a healthy peer's 100. The numbers are the
  # operator's statement of which box should hold the service.
  echo "  box a (priority 200):"; configure_vrrp a 200 150 | sed 's/^/    /'
  echo "  box b (priority 100):"; configure_vrrp b 100 150 | sed 's/^/    /'
fi

echo
echo "=== what each box says it is ==="
HA_A=$(hx a -c "show cgnat ha")
HA_B=$(hx b -c "show cgnat ha")
echo "$HA_A" | sed 's/^/  a: /'
echo "$HA_B" | sed 's/^/  b: /'

echo
echo "=== the configuration comes back from the box, not from memory ==="
RUN_A=$(hx a -c "show running-config" 2>/dev/null)
echo "$RUN_A" | grep -E "vrrp" | sed 's/^/  a: /'

# ------------------------------------------------- the client, which is a CPE

# Pointed at addresses NOBODY OWNS, on both sides, and never touched again.
# Whoever answers for them is the product's business.
ip netns exec client ip route replace default via "$VIP_IN" 2>/dev/null
ip netns exec net ip route replace 100.64.3.0/24 via "$VIP_OUT" 2>/dev/null
echo
echo "=== the client talks THROUGH the gateway, which is what a gateway is for ==="
# A bounded wait for the FIRST crossing, not a fixed sleep. An election takes
# about three and a half seconds at the RFC's default interval and two ARP
# exchanges have to happen after it; a fixed sleep that is long enough today
# is a flake tomorrow, and one that is too short measures the election rather
# than the service. This waits for the box to be ready and says how long it
# took, which is a reading rather than a check.
BEFORE_RC=1
SETTLE_AT=$(date +%s)
while [ $(( $(date +%s) - SETTLE_AT )) -lt 20 ]; do
  if ip netns exec client ping -c 1 -W 1 "$UPSTREAM" > /tmp/ping-before.log 2>&1
  then BEFORE_RC=0; break; fi
done
echo "  the first crossing took $(( $(date +%s) - SETTLE_AT ))s after the commit"
NEIGH_BEFORE=$(ip netns exec client ip neigh show "$VIP_IN" | tr -d '\n')
echo "  ping: $(sed -n '/packets transmitted/p' /tmp/ping-before.log)"
echo "  arp:  ${NEIGH_BEFORE:-nothing}"

echo
echo "=== the uplink goes, and the box that is alive stops holding the address ==="
# THE FAILURE A DESIGN WITHOUT TRACKING DOES NOT SURVIVE: box a is healthy,
# its subscriber port is up, and it can reach nothing upstream. Tracking is
# what notices, by subtracting from the priority until the peer wins.
#
# WHAT THIS CONTAINER CANNOT DO, measured rather than assumed: make the
# dataplane see a CABLE go. These boxes forward over af_packet host
# interfaces, and this VPP never reports one of those as link down - with the
# bridge side of the veth down, with the box's own port down in Linux, and
# with the veth deleted outright, `show hardware` goes on saying "up". A VPP
# tap was tried on the theory that a virtio device carries the host's carrier:
# it does not either. The readings are printed below.
#
# So the cable is pulled AND the port is taken down in the dataplane, which is
# the state a card whose cable is out reports on real hardware. That is
# causing the failure, which this lab may do; it is not configuring the box
# out of it. What is left for #28 is the one question hardware answers: does
# the driver notice the cable itself.
ip link set a-up-br down
sleep 2
echo "  the cable is out at the bridge, and Linux says so inside the box:"
ip netns exec a ip link show a-up | sed -n 1p | sed 's/^/    /'
echo "  and the dataplane still says the link is up, which is why the port"
echo "  is taken down there too:"
vb a show hardware host-a-up | sed -n 2p | sed 's/^/    /'
vb a set interface state host-a-up down
TRACK_AT=$(date +%s)
TRACK_OK=1
while [ $(( $(date +%s) - TRACK_AT )) -lt 20 ]; do
  case "$(hx b -c "show cgnat ha")" in
    *"host-b-lan vrrp 1   master, holding the address"*) TRACK_OK=0; break ;;
  esac
done
echo "  box b took the address after $(( $(date +%s) - TRACK_AT ))s"
HA_A_TRACK=$(hx a -c "show cgnat ha")
echo "$HA_A_TRACK" | sed -n '/role/,/peer/p' | sed 's/^/  a: /'
# Bounded, not a single shot: the upstream has to learn the virtual MAC on
# ITS side too, and that is an ARP exchange after the election - the same
# reason the first crossing above is waited for rather than slept through.
CROSS_TRACK=1
CROSS_AT=$(date +%s)
while [ $(( $(date +%s) - CROSS_AT )) -lt 15 ]; do
  if ip netns exec client ping -c 1 -W 1 "$UPSTREAM" >/dev/null 2>&1; then
    CROSS_TRACK=0; break
  fi
done
echo "  the client crossed the pair again after $(( $(date +%s) - CROSS_AT ))s"

echo
echo "=== and the cable goes back in ==="
# Preempt: the configuration says box a holds the service when both are
# healthy, so a box that comes back takes it back rather than leaving the pair
# where the last failure put it.
vb a set interface state host-a-up up
ip link set a-up-br up
BACK_AT=$(date +%s)
BACK_OK=1
while [ $(( $(date +%s) - BACK_AT )) -lt 20 ]; do
  case "$(hx a -c "show cgnat ha")" in
    *"host-a-lan vrrp 1   master, holding the address"*) BACK_OK=0; break ;;
  esac
done
echo "  box a took it back after $(( $(date +%s) - BACK_AT ))s"

echo
echo "=== the priority is what decides, and changing it moves the service ==="
# What CAN be measured here, and it is the half that tracking rests on: an
# election that follows the numbers. Tracking works by subtracting from the
# priority, so a pair whose service does not move when the priority moves
# would not fail over on a tracked interface either.
hx a -c "configure" -c "interface host-a-lan" -c "vrrp 1 priority 50" \
   -c "exit" -c "commit" | sed 's/^/  a: /'
LOWER_AT=$(date +%s)
LOWER_OK=1
while [ $(( $(date +%s) - LOWER_AT )) -lt 20 ]; do
  case "$(hx b -c "show cgnat ha")" in
    *"host-b-lan vrrp 1   master, holding the address"*) LOWER_OK=0; break ;;
  esac
done
echo "  box b took the address after $(( $(date +%s) - LOWER_AT ))s"
CROSS_LOWER=1
ip netns exec client ping -c 2 -W 2 "$UPSTREAM" >/dev/null 2>&1 && CROSS_LOWER=0

echo
echo "=== and back up again, which is what preempt is for ==="
hx a -c "configure" -c "interface host-a-lan" -c "vrrp 1 priority 200" \
   -c "exit" -c "commit" | sed 's/^/  a: /'
BACK_AT=$(date +%s)
PREEMPT_OK=1
while [ $(( $(date +%s) - BACK_AT )) -lt 20 ]; do
  case "$(hx a -c "show cgnat ha")" in
    *"host-a-lan vrrp 1   master, holding the address"*) PREEMPT_OK=0; break ;;
  esac
done
echo "  box a took it back after $(( $(date +%s) - BACK_AT ))s"

echo
echo "=== the pair is asked to diverge, and the commit refuses (#8) ==="
# THE SILENT FAILURE THIS GATE IS ABOUT. In deterministic mode the mapping is
# a function of the configuration, so two boxes with the same pool agree
# without exchanging a byte - and two boxes with DIFFERENT pools fail without
# saying anything: the takeover maps subscribers to blocks the other box never
# used, and both boxes log a perfectly ordinary day.
#
# The peer's configuration reaches this box the way an operator gets it there:
# `save` on the other box and a copy. Nothing here is the product's work.
pair_pool() {   # <box>: the same pool on both, which is what makes the
                # mapping a function of the configuration and not of the box
  hx "$1" -c "configure" -c "nat cgnat pool residential" \
     -c "inside 100.64.0.0/26" -c "outside 200.150.16.0/28" \
     -c "block-size 2048" -c "mode deterministic" -c "exit" -c "commit"
}
pair_pool a | sed 's/^/  a: /'
pair_pool b | sed 's/^/  b: /'
hx a -c "save" >/dev/null 2>&1
cp /etc/prisma-routing/a.conf /etc/prisma-routing/peer-for-b.conf
echo "  box b now holds box a's configuration as its peer file"
# A SECOND POOL ON ONE BOX, and not a block size on the shared one. Both
# diverge the pair, but a block size also re-maps the subscribers already on
# that pool - and `commit remap` refuses that first, which is correct and is
# another lab's subject. A pool the peer has never heard of moves nobody on
# this box and is exactly the divergence this gate is about: after a takeover
# its subscribers are translated by nothing at all.
new_pool() {   # <box> <commit words...>
  _box=$1; shift
  PRISMA_PEER_CONFIG=/etc/prisma-routing/peer-for-b.conf \
  hx "$_box" -c "configure" -c "nat cgnat pool business" \
     -c "inside 100.64.1.0/26" -c "outside 200.150.17.0/28" \
     -c "block-size 2048" -c "mode deterministic" -c "exit" -c "$*"
}
DIVERGE=$(new_pool b commit)
echo "$DIVERGE" | sed 's/^/  b: /'
CHECK=$(new_pool b commit check)
BYNAME=$(new_pool b commit diverge)
echo "  and when it is asked for by name:"
echo "$BYNAME" | sed 's/^/  b: /'
POOL_AFTER=$(hx b -c "show running-config" 2>/dev/null | grep -c "pool business")
# The pair agrees again before the takeover phase below, which is measured on
# a pair that agrees - the state the rest of this lab is about.
PRISMA_PEER_CONFIG=/etc/prisma-routing/peer-for-b.conf \
  hx b -c "configure" -c "no nat cgnat pool business" \
  -c "commit diverge" >/dev/null 2>&1

echo
echo "=== the box holding the service is killed, and nothing else is touched ==="
pkill -KILL -f "/tmp/startup-a.conf"
KILLED_AT=$(date +%s)
# No ARP flush, no route change, no second box configured. The client goes on
# sending to the MAC it already has.
OK_AFTER=1
while [ $(( $(date +%s) - KILLED_AT )) -lt "$TAKEOVER_WINDOW" ]; do
  if ip netns exec client ping -c 1 -W 1 "$UPSTREAM" >/dev/null 2>&1; then
    OK_AFTER=0; break
  fi
done
TOOK=$(( $(date +%s) - KILLED_AT ))
NEIGH_AFTER=$(ip netns exec client ip neigh show "$VIP_IN" | tr -d '\n')
HA_B_AFTER=$(hx b -c "show cgnat ha")
echo "  the gateway answered again after ${TOOK}s (window ${TAKEOVER_WINDOW}s)"
echo "  arp:  ${NEIGH_AFTER:-nothing}"
echo "$HA_B_AFTER" | sed 's/^/  b: /'

# ------------------------------------------------------------------- the verdict

echo
echo "=== the verdict ==="
expect_in "the VTY takes the statement and the box keeps it" \
          "vrrp 1 address $VIP_IN" "$RUN_A"
expect_in "including the interface it tracks, read back from the dataplane" \
          "vrrp 1 track host-a-up decrement 150" "$RUN_A"
expect_in "box a, with the higher priority, holds the address" \
          "master, holding the address" "$HA_A"
expect_in "box b, with the lower one, stands by" "backup" "$HA_B"
expect_in "the address answers with RFC 5798's virtual MAC, not the card's" \
          "$VMAC" "$NEIGH_BEFORE"
expect "the client's traffic crosses the pair before anything fails" \
       test "$BEFORE_RC" -eq 0
expect "and crosses it again after the box holding the address was killed,
        with the ARP cache never flushed" test "$OK_AFTER" -eq 0
expect_in "the MAC the client holds is the same one, which is why it never
           had to ask again" "$VMAC" "$NEIGH_AFTER"
expect "an uplink that is gone moves the address: box a is alive and can
        reach nothing, and tracking is what notices" test "$TRACK_OK" -eq 0
expect_in "and box a says what it is ADVERTISING and why, not only what it
           was configured with" \
          "and something it tracks is down" "$HA_A_TRACK"
expect "the client's traffic crosses the pair while box a is the broken one" \
       test "$CROSS_TRACK" -eq 0
expect "and when the uplink comes back the box the configuration names takes
        the service back" test "$BACK_OK" -eq 0
expect "lowering box a's priority moves the service to box b" \
       test "$LOWER_OK" -eq 0
expect "and the client's traffic crosses the pair while it is box b holding
        the address" test "$CROSS_LOWER" -eq 0
expect "raising it again takes the service back, which is what preempt is
        for: the pair ends where the configuration says" \
       test "$PREEMPT_OK" -eq 0
expect_said "a commit that would map subscribers differently from the peer
             is refused, on a box that stands for a service address with one" \
            "this change maps subscribers differently from the peer" "$DIVERGE"
expect_in "and the refusal names the pool, and what a takeover would do
           with it" \
          "pool 'business' is on this box and not on the peer" "$DIVERGE"
expect_in "'commit check' says it too, in the window where somebody is
           deciding" "commit diverge" "$CHECK"
expect_in "and asked for by name it goes through" "commit complete" "$BYNAME"
expect "so the operator is stopped, not blocked: the change is on the box" \
       test "$POOL_AFTER" -ge 1
expect_in "and box b now says it is holding the address" \
          "master, holding the address" "$HA_B_AFTER"

echo
if [ "$VERDICT_FAILURES" -eq 0 ]; then
  echo "LAB PASSED"
else
  echo "LAB FAILED: $VERDICT_FAILURES check(s)"
fi
exit "$VERDICT_FAILURES"
