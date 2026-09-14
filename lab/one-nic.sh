#!/bin/sh
# A box with ONE NIC: the card moves to the dataplane and the box stays up (#303)
#
# The box this is about has a single card. That card carries its address and
# its default route, so the dataplane refuses it - and the refusal's advice,
# "name the card carrying subscriber traffic instead", names a card that does
# not exist. The box cannot forward and cannot be told to.
#
# What is measured here is the way out: the card goes to the dataplane and the
# management address goes onto its linux_cp pair, so the box is reached
# THROUGH the dataplane instead of beside it.
#
# WHERE THE LINE IS. lab/ha-failover.sh carries the table and the argument; it
# holds here word for word, because everything this lab could helpfully do by
# hand is the product's missing step.
#
#   The lab MAY                            The lab MUST NOT
#   ------------------------------------   ------------------------------------
#   build the two namespaces and the veth  create the linux_cp pair
#   give the box its address and route     put the address on the pair
#     BEFORE anything is committed         install the default route
#   take the card away from the kernel     name the dataplane interface the
#     (see below)                            card became
#   start the dataplane                    put the address back after a revert
#   run the product's own boot command     write anything into tune.env
#   read what arrives from upstream        answer ARP for the box
#
# The right-hand column is #303's own work. Everything the lab types into the
# VTY is one statement long - `dataplane interface box0` - because that is all
# an operator has to go on: on a box with one card there is no second card to
# name, no dataplane interface name to write down (the card has no name in the
# dataplane until the dataplane has claimed it) and no way to test a guess.
# A lab that typed the address onto a pair it had created itself would be
# measuring a box somebody had already rescued by hand.
#
# TAKING THE CARD AWAY FROM THE KERNEL IS MODELLED, and it is the one thing
# here that is not the real mechanism. On the box, `bind-dataplane-nic`
# unbinds the card from its kernel driver and the netdev is GONE. af_packet
# cannot do that - the netdev stays, and the kernel would go on answering ARP
# and ICMP for an address it holds on the pair, straight off the wire, so this
# lab would pass over a path no such box has. So the netdev is emptied and a
# matchall drop is put on its ingress: VPP taps the port through ptype_all,
# which runs BEFORE tc, so the dataplane receives every frame and the kernel
# receives none. That is what the bind leaves behind. The negative check in
# stage 2 is what keeps it honest: with the card in the dataplane and nothing
# configured, the box must be unreachable.
#
# WHAT THIS LAB DOES NOT MEASURE, said here rather than left to be assumed.
# The bind itself: a container has no PCI device to hand DPDK, so nothing here
# exercises `hexarouting-tune` refusing or allowing the card, or
# `bind-dataplane-nic` unbinding it. Those two make the same decision from the
# same two facts as the VTY - is there another card, and has the box written
# the management path down - and each is driven against a sysfs tree built for
# it in appliance/test_tune.py and test/test_bind_nic.sh. What is measured
# HERE is the half no fake tree can hold: whether a box whose only path is
# through the dataplane is actually reachable.
#
#   netns up                                 netns box  (ONE card, and no lo route)
#   up0     10.95.200.1/24   <--- veth --->  box0
#   dummy9  198.51.100.9/32                    |  the dataplane takes it
#            (off-subnet, so the box's                   |
#             default route is exercised)      the pair carries 10.95.200.198
#
set -u
LAB_SRC=${LAB_SRC:-/src}
MGMT=10.95.200.198
PREFIX=24
GW=10.95.200.1
FAR=198.51.100.9

if [ "${ONE_NIC_IN_BOX:-0}" != 1 ]; then
  # ------------------------------------------------------ the fixture, outside
  # Built from the root namespace, then this script re-runs itself inside the
  # box. The box is a namespace of its own and not the container, because the
  # container has an ethernet card and a default route of Docker's: a box with
  # a second way out is not the box #303 is about, and every reading here -
  # /sys/class/net, /proc/net/route, what the kernel does with a packet - would
  # be taken from the wrong machine.
  ip netns del box 2>/dev/null
  ip netns del up 2>/dev/null
  ip link del box0 2>/dev/null
  ip netns add box
  ip netns add up
  ip link add box0 type veth peer name up0
  ip link set box0 netns box
  ip link set up0 netns up

  ip -n up link set lo up
  ip -n up link set up0 up
  ip -n up addr add $GW/$PREFIX dev up0
  ip -n up link add dummy9 type dummy
  ip -n up link set dummy9 up
  ip -n up addr add $FAR/32 dev dummy9
  ip netns exec up sysctl -qw net.ipv4.ip_forward=1

  # The box as the operator has it: one card, an address on it, a default
  # route through it. This is the state the issue reports, and it is the state
  # the product is asked to move FROM - so the lab builds it and never rebuilds
  # it afterwards.
  ip -n box link set lo up
  ip -n box link set box0 up
  ip -n box addr add $MGMT/$PREFIX dev box0
  ip -n box route add default via $GW

  ONE_NIC_IN_BOX=1 export ONE_NIC_IN_BOX
  exec ip netns exec box sh "$0" "$@"
fi

# ---------------------------------------------------------------- inside the box
. "$LAB_SRC/lab/lib.sh"
mkdir -p /run/vpp /vpp_plugins /etc/hexarouting /var/run/frr
HX="python3 $LAB_SRC/src/hexash/hexash.py"
export HEXA_CONFIG=/etc/hexarouting/hexarouting.conf
export HEXA_TUNE_ENV=/etc/hexarouting/tune.env
rm -f "$HEXA_CONFIG" /etc/hexarouting/confirm-pending
printf '# lab\nHEXA_DATAPLANE_IF=\n' > "$HEXA_TUNE_ENV"

(cd "$LAB_SRC/.build-vpp" && ninja >/dev/null 2>&1) || {
  mkdir -p "$LAB_SRC/.build-vpp" && cd "$LAB_SRC/.build-vpp" \
    && cmake -G Ninja "$LAB_SRC" >/dev/null && ninja
}
cd "$LAB_SRC"
use_binary_api

# A dataplane with no interface at all, which is what a fresh appliance has and
# what the box in #303 has: DPDK was given no device, so VPP holds local0 and
# nothing else.
vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
wait_vpp

# FRR the way an installed box runs it. The default route this box needs in
# order to answer anybody off its own subnet is a static route, and a static
# route in this product is FRR's - so a lab without FRR could not measure the
# management path at all.
mkdir -p /var/run/frr /etc/frr
cat > /etc/frr/zebra.conf <<'EOF'
hostname hexarouting
EOF
chown -R frr:frr /var/run/frr /etc/frr
start_frr

# The probe upstream uses, in its own namespace. TCP and not only ICMP:
# management is SSH, and a path that carries a ping and not a session is not a
# management path. Reading the banner means the connection was established and
# the box sent bytes back over it.
cat > /tmp/probe.py <<'PY'
import socket, sys
try:
    s = socket.create_connection((sys.argv[1], int(sys.argv[2])), 6)
    s.settimeout(6)
    print("banner:", s.recv(64).decode(errors="replace").strip())
except Exception as e:
    print("no:", e)
PY
# Something for it to reach: sshd is what an appliance answers on, and it is in
# the runner's package list.
mkdir -p /run/sshd
ssh-keygen -A >/dev/null 2>&1
# The container's sshd ships `permit-root-login without-password`, which this
# VTY renders back and then refuses to parse. That is a defect and it is not
# this one: left alone it stops the boot replay below on a line that has
# nothing to do with #303, so the fixture writes the spelling the product
# takes and the two are not confused for each other.
# `sshd -T` prints `without-password` for the `prohibit-password` this VTY
# offers, and the VTY then refuses its own read-back when the saved file is
# replayed. That is a defect and it is not this one: left alone it stops the
# boot replay below on a line that has nothing to do with #303, so the fixture
# uses a spelling both sides spell the same way.
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
/usr/sbin/sshd -o ListenAddress=0.0.0.0 -o Port=22 2>/dev/null

echo "=========== STAGE 0: the box as the operator has it ==========="
echo "  the cards Linux has in this box:"
ls /sys/class/net | tr '\n' ' ' | sed 's/^/    /'; echo
echo "  and what carries the default route:"
awk 'NR>1 && $2=="00000000" {print "    " $1}' /proc/net/route
FIXTURE=$(ip netns exec up ping -c 2 -W 2 -I $FAR $MGMT 2>&1)
printf '%s\n' "$FIXTURE" | tail -2 | sed 's/^/    /'

echo
echo "=========== STAGE 1: the operator names the only card ==========="
# One statement. Everything else the box needs in order to survive it is the
# product's to work out, because the operator has nothing else to give it.
NAMED=$($HX -c "configure" -c "dataplane interface box0" \
            -c "commit confirmed 1" 2>&1)
printf '%s\n' "$NAMED" | sed 's/^/  /'
echo "  --- what the box will replay at its next boot ---"
$HX -c "save" >/dev/null 2>&1
SAVED=$(cat "$HEXA_CONFIG" 2>/dev/null)
printf '%s\n' "$SAVED" | sed 's/^/    /'
echo "  --- and what the tuner was told ---"
TUNED=$(grep HEXA_DATAPLANE "$HEXA_TUNE_ENV")
printf '%s\n' "$TUNED" | sed 's/^/    /'

echo
echo "=========== STAGE 2: the card moves, and the box boots on it ==========="
# The bind, modelled. See the header: the netdev cannot be removed here, so it
# is emptied and its ingress is dropped, which is what the kernel is left with
# once DPDK has the device.
MAC=$(cat /sys/class/net/box0/address)
ip addr flush dev box0
# The NAME goes too, and that is not cosmetic. On the box the netdev does not
# exist after the bind, so `ens18` is free and the pair takes it - which is
# the point of naming the pair after the card: FRR, every script and the
# operator's own fingers go on spelling the interface the way they always did.
# A veth cannot be removed while the dataplane is reading it, so it is renamed
# out of the way before the dataplane is given it.
ip link set box0 down
ip link set box0 name box0-bound
ip link set box0-bound up
tc qdisc add dev box0-bound ingress 2>/dev/null
tc filter add dev box0-bound ingress matchall action drop 2>/dev/null
vppctl create host-interface name box0-bound hw-addr "$MAC" >/dev/null
vppctl set interface state host-box0-bound up
echo "  the dataplane now holds the card:"
vppctl show interface | awk 'NR<5' | sed 's/^/    /'

# The negative, taken BEFORE the box is asked to configure anything. It is what
# says the modelled bind is honest: if the kernel could still answer for this
# address off the wire, this ping would come back and everything below it would
# be measuring the fixture.
GONE=$(ip netns exec up ping -c 2 -W 2 -I $FAR $MGMT 2>&1)
printf '%s\n' "$GONE" | tail -2 | sed 's/^/    /'
# And with no pair, which is what the negative above is the consequence of.
# Read before the replay rather than after the whole run: the revert in stage 3
# takes the pair down again, and a reading taken at the verdict would report
# the teardown as a boot that never paired anything.
LCP_BEFORE=$(vppctl show lcp 2>&1)

echo "  --- the boot: hexarouting.service replays the saved file ---"
# `hexash --load <file>` verbatim: that is the ExecStart of hexarouting.service
# and the only thing that runs between a cold box and a reachable one.
BOOT=$($HX --load "$HEXA_CONFIG" 2>&1)
printf '%s\n' "$BOOT" | tail -12 | sed 's/^/    /'
sleep 3

LCP_AFTER=$(vppctl show lcp 2>&1)
echo "  --- what the box has now ---"
ip -o -4 addr show 2>&1 | sed 's/^/    /'
ip route show 2>&1 | sed 's/^/    /'
vppctl show lcp 2>&1 | grep itf-pair | sed 's/^/    /'
vppctl show interface addr 2>&1 | sed 's/^/    /'

echo
echo "  --- is the box reachable from off its own subnet? ---"
PAIR=$(vppctl show lcp | sed -n 's/.*itf-pair: \[[0-9]*\] [^ ]* [^ ]* \([^ ]*\) .*/\1/p' | head -1)
RX0=$(cat "/sys/class/net/${PAIR:-none}/statistics/rx_packets" 2>/dev/null || echo 0)
PING=$(ip netns exec up ping -c 5 -W 2 -I $FAR $MGMT 2>&1)
printf '%s\n' "$PING" | tail -2 | sed 's/^/    /'
# WAITED FOR, not slept on. The counter is the kernel's own on the TAP and it
# is updated as the dataplane punts, not as the reply leaves, so a reading
# taken the instant `ping` returns can be short by the last packet or two, and
# has been: one run said 1 -> 2 for three echo requests.
#
# A fixed sleep was the first answer and it was not enough. Measured: this lab
# passed alone at load 3.0 and failed in the release suite at load 4.5 with
# sixteen labs in parallel, on this one check and no other - the ping in the
# same run reported 0% packet loss, so the packets had arrived and only the
# reading was early. A sleep long enough for the worst machine is a sleep
# everybody else pays on every run, and it is still a guess about a machine
# nobody has measured.
#
# So the deadline is generous and the wait ends the moment the counter moves.
# It can still fail honestly: a box the packets never reached leaves RX1 equal
# to RX0 and the check goes red after the deadline rather than never.
_deadline=$(( $(date +%s) + 30 ))
while :; do
  RX1=$(cat "/sys/class/net/${PAIR:-none}/statistics/rx_packets" 2>/dev/null || echo 0)
  [ "$RX1" -gt "$RX0" ] && break
  [ "$(date +%s)" -ge "$_deadline" ] && break
  sleep 1
done
echo "    the pair is '${PAIR:-none}', rx_packets $RX0 -> $RX1"
TCP=$(ip netns exec up python3 /tmp/probe.py $MGMT 22 2>&1)
echo "    TCP to port 22: $TCP"

echo
echo "=========== STAGE 3: nobody confirms ==========="
# The window was one minute and no second session ever arrived, which is the
# case this whole shape exists for: the box took its own management card and
# cannot be reached to be told it worked.
echo "  waiting out the window..."
sleep 65
EXPIRED=$($HX --confirm-expired 2>&1)
printf '%s\n' "$EXPIRED" | sed 's/^/    /'
echo "  --- what the tuner was told after the revert ---"
grep HEXA_DATAPLANE_IF "$HEXA_TUNE_ENV" | sed 's/^/    /'
AFTER=$(cat "$HEXA_CONFIG" 2>/dev/null; $HX -c "show running-config" 2>/dev/null)

echo
echo "=========== VERDICT ==========="
. "$LAB_SRC/lab/verdict.sh"

# FIXTURE. Nothing below means anything if the box was not reachable to start
# with, and a broken veth reads exactly like a product that lost the box.
expect_setup "FIXTURE: before anything is named, upstream can reach the box" \
  sh -c "case \"$FIXTURE\" in *', 0% packet loss'*) exit 0;; *) exit 1;; esac"

# Stage 1: the refusal is the defect. It is right about every box that has a
# second card and wrong about this one, where its advice cannot be followed.
expect_not_said "the only card is no longer refused" \
  "name the card carrying subscriber traffic instead" "$NAMED"
expect_in "the commit is accepted" "commit complete" "$NAMED"
expect_said "and the box says it is keeping the management path" \
  "$MGMT/$PREFIX" "$NAMED"
# A change that can take the box off the network undoes itself when nobody
# says otherwise. Not optional, and not something the operator has to remember
# to ask for.
expect_in "it is armed to roll back" "rollback at" "$NAMED"
expect_in "the tuner is told which card to hand over" \
  "HEXA_DATAPLANE_IF=box0" "$TUNED"
# The saved file is the whole of what the box has after a reboot. If the
# management path is not in there, the box comes up with the card in the
# dataplane and no way to be reached.
expect_in "the saved configuration names the card" \
  "dataplane interface box0" "$SAVED"
expect_in "and records the management address to put back" "$MGMT/$PREFIX" "$SAVED"
expect_in "and the default route that goes with it" "0.0.0.0/0 $GW" "$SAVED"

# Stage 2: the card is in the dataplane. This is the measurement the issue asks
# for, and the negative above it is what makes it worth reading.
expect "with the card in the dataplane and nothing configured, the box is gone" \
  sh -c "case \"$GONE\" in *'100% packet loss'*) exit 0;; *) exit 1;; esac"
expect_not_in "and with no pair for it either" "itf-pair" "$LCP_BEFORE"
expect_in "the boot replay creates the pair, named after the card" \
  "box0" "$(printf '%s' "$LCP_AFTER" | grep itf-pair)"
expect "upstream can reach the box again, from off its subnet" \
  sh -c "case \"$PING\" in *', 0% packet loss'*) exit 0;; *) exit 1;; esac"
expect "and the packets came through the pair, not past it" \
  test "$RX1" -gt "$RX0"
expect_in "a TCP session to the box is established through the dataplane" \
  "banner: SSH-2.0" "$TCP"

# Stage 3: nobody confirmed.
expect_said "an unconfirmed handover is put back" \
  "put the previous configuration back" "$EXPIRED"
expect_not_in "the card is not named anywhere the next boot would read it" \
  "HEXA_DATAPLANE_IF=box0" "$(cat "$HEXA_TUNE_ENV")"
expect_said "and the box says a restart is what gives the card back" \
  "reboot" "$EXPIRED"
verdict
