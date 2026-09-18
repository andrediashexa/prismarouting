#!/bin/sh
# Two boxes, one subscriber, three ways of losing the active one (#9).
#
# THIS LAB IS EXPECTED TO FAIL ON master AND THAT IS THE POINT. High
# availability is analysed in docs/ha.md and not implemented, so nothing in
# this repository carries a service address from one box to another. The lab
# exists before the implementation so that #7 and #8 are judged against the
# requirement instead of the requirement being written to fit them, which is
# what docs/roadmap.md phase 6 asks for. tools/run-all-labs.sh does not run it,
# for the reason written beside its name there.
#
# WHERE THE LINE IS, and it is the whole design of this file.
#
# lab/lcp.sh carries the lesson: until #182 it ran `vppctl lcp create` itself,
# so it proved the glue on a box somebody had set up by hand, while the box an
# operator receives created no pair at all and every routing protocol was
# configurable and inert. A lab that performs the product's missing step does
# not test the product. So:
#
#   The lab MAY                            The lab MUST NOT
#   ------------------------------------   ------------------------------------
#   build the two LANs and the veths       put the service address on a box
#   start both dataplanes                  move the service address between them
#   give each box its OWN addresses        answer ARP for the service address
#   configure the same pool on both        flush the subscriber's ARP cache
#   send subscriber traffic                start or reconfigure the second box
#   cause the failure                        at the moment of the failure
#   read what arrived on the far side      decide which box is active
#
# The right-hand column is what #7 has to build. If a run of this lab ever goes
# green because the lab did one of those, it has tested itself.
#
# The subscriber's ARP cache is on that list deliberately, and nothing in this
# file flushes it, at any point. A CPE holds a stale entry for its gateway
# across a failover exactly as this namespace does, and dealing with that is
# part of the takeover: a virtual MAC that moves with the address, or a
# gratuitous ARP from the box that took it. A lab that ran `ip neigh flush`
# would be performing that step, and would stay green on a product that never
# sends one.
#
# WHAT THE SERVICE IS. Not a ping. The thing this box exists for is a
# subscriber's TRANSLATED flow, so the probe is a round trip through the
# translation: the subscriber sends from a fixed private socket, the upstream
# echoes back the source address it saw, and the reply comes home through
# out2in. A failover that keeps ICMP alive and loses every NAT session has
# failed at the only thing being asked of it. The address asserted is therefore
# the one observed ON THE FAR SIDE of the translation, never one read off a
# counter on the box doing the translating (docs/context.md 1.4).
#
#            br-in                                   br-out
#   [sub] ---------+--- box A (netns a) 100.64.3.252 ---+--- [net]
#    100.64.0.5    |             200.150.0.11           |   200.150.0.2
#    100.64.0.9    +--- box B (netns b) 100.64.3.251 ---+
#                                200.150.0.12
#
#   the service addresses, which nothing owns:  100.64.3.254 and 200.150.0.10
#
# Two subscribers on purpose. 100.64.0.9 is the fixture's, used only to prove
# each box can translate at all before anything is asked of the pair, and
# 100.64.0.5 is the service's, so that a session for .5 appearing on a box is
# unambiguous evidence that THAT box received the subscriber's traffic.
#
# TELLING A BROKEN LAB FROM A MISSING FEATURE. Stage 1 runs a subscriber
# through each box in turn on that box's OWN address. Those checks are named
# FIXTURE in the verdict: if they are red the topology is wrong and nothing
# under them means anything. Everything after them goes through the service
# address and is the product's.
#
# WHAT A PASSING RUN LOOKS LIKE, stated here because #7 will be judged against
# it and a target written afterwards is not a target:
#
#   1. Before any failure the subscriber's round trip through the SERVICE
#      address succeeds, and the upstream sees it as the address and port block
#      the formula gives, read off the box rather than typed here.
#   2. One box is receiving the subscriber's traffic, and the lab can say which
#      by watching where a new session appears.
#   3. After EACH of the three failures the round trip works again within
#      TAKEOVER_WINDOW seconds, with the SAME public address and a port in the
#      SAME block, and the box now receiving is not the one that was broken.
#   4. The three failures are three different failures. A pulled uplink leaves
#      the active box alive and still holding the inside address, which is the
#      one a design without interface tracking does not survive. Surviving a
#      SIGKILL and not that one is a real result, so they are reported apart.
#   5. An in-flight TCP connection is GONE after the takeover, while a new one
#      from the same subscriber gets the same public address and a port in the
#      same block. Connections drop; addresses do not. Nobody may read a green
#      run here as "connections survived".
#
# Nothing in the verdict names VRRP, keepalived or any configuration syntax.
# Only configure_ha() below mentions a spelling, and it asserts nothing, for a
# reason measured and written above it.
#
# OFF-RED-AT-LEAST: 8
#
#   sh tools/lab.sh ha-failover        -> LAB PASSED
#   sh tools/lab.sh ha-failover off    -> LAB FAILED (no pair is configured)
#
# The off run is what says this lab can fail at all: with `off` the boxes get
# their own addresses and no virtual router, which is the box this lab was
# written against and every check of it was red on.
set -u
. /src/lab/lib.sh      # startup_conf, use_binary_api
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

MODE=${1:-on}
FLOW=/src/lab/haflow.py
SUB_SERVICE=100.64.0.5     # the subscriber whose service has to survive
SUB_FIXTURE=100.64.0.9     # the subscriber that proves the topology works
VIP_IN=100.64.3.254        # the gateway the subscriber is pointed at
VIP_OUT=200.150.0.10       # the next hop the upstream sends the pool to
UPSTREAM=200.150.0.2
UDP_PORT=9999
TCP_PORT=8888
# How long a takeover is allowed. VRRP's default master-down interval is about
# 3.6 s, so this is generous by a factor of five: a design that needs longer
# has not failed over, it has been restarted.
TAKEOVER_WINDOW=20

# ---------------------------------------------------------------- the two LANs

ip netns add sub 2>/dev/null
ip netns add net 2>/dev/null
ip netns add a   2>/dev/null
ip netns add b   2>/dev/null
ip link add br-in  type bridge 2>/dev/null
ip link add br-out type bridge 2>/dev/null
ip link set br-in up
ip link set br-out up

leg() {   # <bridge-side name> <namespace-side name> <namespace> <address|empty>
  ip link add "$1" type veth peer name "$2" 2>/dev/null
  ip link set "$2" netns "$3"
  ip netns exec "$3" ip link set lo up
  ip netns exec "$3" ip link set "$2" up
  [ -n "$4" ] && ip netns exec "$3" ip addr add "$4" dev "$2"
  ip link set "$1" up
}
leg sub-br sub0 sub "$SUB_SERVICE/22"
ip netns exec sub ip addr add "$SUB_FIXTURE/22" dev sub0
ip link set sub-br master br-in
leg net-br net0 net "$UPSTREAM/24"
ip link set net-br master br-out
# The boxes' ports carry no Linux address: VPP takes them and owns the
# addressing, which is how an appliance is built (docs/appliance.md).
leg a-in-br  a-in  a ""
ip link set a-in-br master br-in
leg a-out-br a-out a ""
ip link set a-out-br master br-out
leg b-in-br  b-in  b ""
ip link set b-in-br master br-in
leg b-out-br b-out b ""
ip link set b-out-br master br-out

# ------------------------------------------------------------------- two boxes
#
# One container, two dataplanes. Each VPP gets its own network namespace, its
# own runtime directory and its own shared-memory prefix, and that is the whole
# of what makes them two boxes rather than one process with four interfaces.
#
# Derived from lab/startup-test.conf rather than kept beside it as a copy, for
# the reason lab/lib.sh gives about the file that used to be
# lab/startup-workers.conf: two files that must differ by exactly one stanza
# are two files that will one day differ by two.
startup_for() {   # <box> <api prefix> -> the path of the generated file
  mkdir -p "/run/vpp/$1"
  sed -e "s#cli-listen /run/vpp/cli.sock#runtime-dir /run/vpp/$1\n  cli-listen /run/vpp/$1/cli.sock#" \
      -e "s#^api-segment {#api-segment {\n  prefix $2#" \
      "$(startup_conf test)" > "/tmp/startup-$1.conf"
  echo "/tmp/startup-$1.conf"
}

vb() {   # <box> <vppctl arguments...>, with the carriage returns off (lib.sh)
  _box=$1; shift
  vppctl -s "/run/vpp/$_box/cli.sock" "$@" | tr -d '\r'
}

start_box() {   # <box> <api prefix>
  ip netns exec "$1" vpp -c "$(startup_for "$1" "$2")" > "/tmp/vpp-$1.log" 2>&1 &
  eval "VPP_PID_$1=$!"
  _n=0
  until vppctl -s "/run/vpp/$1/cli.sock" show version >/dev/null 2>&1 \
        || [ "$_n" -ge 30 ]; do sleep 1; _n=$((_n + 1)); done
  vppctl -s "/run/vpp/$1/cli.sock" show version >/dev/null 2>&1 || {
    # The line tools/run-all-labs.sh classifies NO VPP on, per lab/lib.sh.
    echo "VPP did not start"; tail -20 "/tmp/vpp-$1.log"; exit 1; }
}

# The same pool on both boxes, which is what makes the mapping a function of
# the configuration rather than of the box (docs/ha.md). Proving that two boxes
# really do carry the same configuration is #8; here they are made the same and
# the lab reads back what each one computes.
configure_box() {   # <box> <inside address/len> <outside address/len>
  vb "$1" create host-interface name "$1-in"  >/dev/null
  vb "$1" create host-interface name "$1-out" >/dev/null
  vb "$1" set interface state "host-$1-in" up
  vb "$1" set interface state "host-$1-out" up
  vb "$1" set interface ip address "host-$1-in" "$2"
  vb "$1" set interface ip address "host-$1-out" "$3"
  vb "$1" ip route add 0.0.0.0/0 via "$UPSTREAM" "host-$1-out"
  vb "$1" bpa pool res inside 100.64.0.0/26 outside 200.150.16.0/28 \
      ports 1024-9215 block 2048 mode deterministic
  vb "$1" set interface bpa in2out "host-$1-in"
  vb "$1" set interface bpa out2in "host-$1-out"
  # Long enough that no UDP session in this lab expires while it runs, so a
  # session that is gone is gone because something ended it. TCP keeps its
  # default, which RFC 5382 REQ-5 already puts at two hours.
  vb "$1" bpa timeouts udp 600 >/dev/null 2>&1
}

echo "=== two dataplanes, each in its own namespace with its own API segment ==="
start_box a hxa
start_box b hxb
configure_box a 100.64.3.252/22 200.150.0.11/24
configure_box b 100.64.3.251/22 200.150.0.12/24
eval "echo \"  box a: vpp pid \$VPP_PID_a    box b: vpp pid \$VPP_PID_b\""
vb a show version | sed 's/^/  a: /'
vb b show version | sed 's/^/  b: /'

# What the formula says, read off each box rather than computed here. This is
# the address and the block every assertion below is about, and docs/ha.md's
# claim that two boxes agree without exchanging a byte is the first thing the
# lab measures.
formula_of() {   # <box> <subscriber> -> "<ip> <lo> <hi>"
  vb "$1" show bpa subscriber "$2" \
    | sed -n 's/.*gives it \([0-9.]*\) ports \([0-9]*\)-\([0-9]*\).*/\1 \2 \3/p'
}
FORMULA_A=$(formula_of a "$SUB_SERVICE")
FORMULA_B=$(formula_of b "$SUB_SERVICE")
PUB_IP=$(echo "$FORMULA_A" | cut -d' ' -f1)
PORT_LO=$(echo "$FORMULA_A" | cut -d' ' -f2)
PORT_HI=$(echo "$FORMULA_A" | cut -d' ' -f3)
echo
echo "=== what each box computes for $SUB_SERVICE, with no state exchanged ==="
echo "  box a: $FORMULA_A"
echo "  box b: $FORMULA_B"
echo "  the service has to keep $PUB_IP and a port inside $PORT_LO-$PORT_HI"

# --------------------------------------------------- the subscriber's upstream

rm -f /tmp/upstream.log /tmp/hold.flag /tmp/hold.out
ip netns exec net python3 "$FLOW" udp-echo "$UPSTREAM" "$UDP_PORT" \
    /tmp/upstream.log > /tmp/udp-echo.out 2>&1 &
ip netns exec net python3 "$FLOW" tcp-echo "$UPSTREAM" "$TCP_PORT" \
    /tmp/upstream.log > /tmp/tcp-echo.out 2>&1 &
sleep 1

probe() {   # <subscriber> <source port> <count> -> "<ok>/<count> <ip>:<port>"
  ip netns exec sub python3 "$FLOW" udp-probe "$1" "$2" "$UPSTREAM" \
      "$UDP_PORT" "$3" 1.0 2>/dev/null | sed -n 's/^result //p'
}

# Is the observed source the block the formula gives this subscriber? Both
# halves are checked, because "the same address, some other block" is a
# different product from the one docs/ha.md describes.
in_block() {   # <ip:port>
  _ip=${1%%:*}; _port=${1##*:}
  [ "$_ip" = "$PUB_IP" ] || return 1
  case "$_port" in ''|*[!0-9]*) return 1 ;; esac
  [ "$_port" -ge "$PORT_LO" ] && [ "$_port" -le "$PORT_HI" ]
}

# Which box is receiving the subscriber's traffic, measured rather than
# assumed. Live state alone will not answer it: a session already on a box
# outlives the round that created it, so a box that stopped receiving anything
# ten seconds ago still shows one. What is read here is a DELTA - a probe from
# a source port never used before, and whichever box grew a session for it is
# the box the packet reached.
#
# The lab does not decide this and must not. Whose address it is is the
# product's answer; all the lab does is break whoever turns out to have it.
rm -f /tmp/probe-port          # the counter next_port() keeps, below
sessions_on() {   # <box> -> the total session count, or nothing if it is dead
  # A box this lab has just killed answers "connect: Connection refused" on
  # stderr, which is the expected answer and not a fault to report.
  vb "$1" show bpa session 2>/dev/null \
    | sed -n 's/^total: \([0-9]*\) sessions.*/\1/p'
}
# A SOURCE PORT NOBODY HAS USED YET, kept in a FILE and not in a variable.
#
# The instrument this lab reads with was wrong, and it was wrong in the way
# that reports the product as broken: `receiving_box` is called as
# `$(receiving_box)`, which is a SUBSHELL, so `PROBE_PORT=$((PROBE_PORT + 1))`
# incremented a copy that died with the subshell. Every reading after the
# first one probed from the SAME source port, the session for it already
# existed on whichever box had answered the first time, and no session count
# moved anywhere - so the reading came back `none` and the verdict said "the
# service came back and nothing is receiving it", which is not a state that
# exists.
#
# It cost one red check in the run that measured it and it would have cost an
# investigation of the product. A counter that has to survive a subshell lives
# where subshells can see it.
next_port() {
  _p=$(cat /tmp/probe-port 2>/dev/null || echo 41000)
  _p=$((_p + 1))
  echo "$_p" > /tmp/probe-port
  echo "$_p"
}
receiving_box() {   # -> a | b | none
  PROBE_PORT=$(next_port)
  _a0=$(sessions_on a); _b0=$(sessions_on b)
  probe "$SUB_SERVICE" "$PROBE_PORT" 1 >/dev/null 2>&1
  _a1=$(sessions_on a); _b1=$(sessions_on b)
  if [ "${_a1:-0}" -gt "${_a0:-0}" ]; then echo a
  elif [ "${_b1:-0}" -gt "${_b0:-0}" ]; then echo b
  else echo none; fi
}

# ------------------------------------------- stage 1: is the fixture any good?
#
# Each box on its OWN address, one after the other. Nothing here is about HA.
echo
echo "=== stage 1: the fixture - each box translates on its own address ==="
ip netns exec net ip route replace 200.150.16.0/28 via 200.150.0.11
ip netns exec sub ip route replace default via 100.64.3.252
FIX_A=$(probe "$SUB_FIXTURE" 40009 3)
echo "  through box a (100.64.3.252): $FIX_A"
ip netns exec net ip route replace 200.150.16.0/28 via 200.150.0.12
ip netns exec sub ip route replace default via 100.64.3.251
FIX_B=$(probe "$SUB_FIXTURE" 40009 3)
echo "  through box b (100.64.3.251): $FIX_B"
FIX_A_IP=$(echo "$FIX_A" | cut -d' ' -f2 | cut -d: -f1)
FIX_B_IP=$(echo "$FIX_B" | cut -d' ' -f2 | cut -d: -f1)
echo "  seen upstream as $FIX_A_IP through a and $FIX_B_IP through b"
# The TCP machinery stage 6 depends on, proven here where nothing is broken.
# Without this, a red "the connection dropped" in stage 6 could be this file's
# bug rather than the product's absence - and the drop detector in particular
# has to be shown capable of saying YES before its NO means anything.
FIX_TCP=$(ip netns exec sub python3 "$FLOW" tcp-probe "$SUB_FIXTURE" 40010 \
          "$UPSTREAM" "$TCP_PORT" 5 2>/dev/null | sed -n 's/^result //p')
echo "  a TCP connection through box b: ${FIX_TCP:-(nothing)}"
rm -f /tmp/fixhold.flag /tmp/fixhold.out
ip netns exec sub python3 "$FLOW" tcp-hold "$SUB_FIXTURE" 40011 "$UPSTREAM" \
    "$TCP_PORT" /tmp/fixhold.flag 20 5 > /tmp/fixhold.out 2>&1 &
FIXHOLD_PID=$!
sleep 2
touch /tmp/fixhold.flag
wait "$FIXHOLD_PID" 2>/dev/null
FIX_HOLD=$(sed -n 's/^survived //p' /tmp/fixhold.out)
echo "  and a held one, with nothing broken under it: ${FIX_HOLD:-(no answer)}"

# ------------------------------------ stage 2: ask the product to pair the two
#
# NOTHING IS ASSERTED ON WHAT THE VTY SAYS HERE, and that is a measured
# decision rather than a shy one. Measured on master at 0.67.0 against a live
# dataplane: `nat cgnat ha peer 200.150.0.12 priority 200` is accepted by
# `commit check` - "configuration check succeeds" - and by `commit` - "commit
# complete" - and then appears in no `show running-config` and changes nothing
# on the box. Anything at all under `nat cgnat` behaves that way. So a check
# that asserted the configuration had been ACCEPTED would be green on a box
# that does nothing with it, which is the defect class this repository is
# organised against (docs/context.md 1.4). That silent acceptance is a defect
# of its own and is filed separately; it is not #9.
#
# What is printed here is evidence, not a check. It is also the only place in
# this file that names a syntax: #7 MAY change what configure_ha() types. #7
# MUST NOT change anything below the verdict line, because that is the
# requirement and this is only how it is asked for.
#
# EACH SIDE TRACKS THE OTHER, which is what makes stage 4 a failure rather
# than a cable nobody reacts to: a box whose uplink is gone drops from 200 to
# 50 and loses the subscriber-side address to a healthy peer advertising 100.
# A pair configured without tracking survives a dead box and not a deaf one,
# and that difference is the whole reason stage 4 is asked separately.
configure_ha() {   # <box> <api prefix> <priority>
  cat > "/usr/local/bin/vppctl-$1" <<EOF
#!/bin/sh
exec vppctl -s /run/vpp/$1/cli.sock "\$@"
EOF
  cat > "/usr/local/bin/vat2-$1" <<EOF
#!/bin/sh
exec vat2 -s $2 "\$@"
EOF
  chmod +x "/usr/local/bin/vppctl-$1" "/usr/local/bin/vat2-$1"
  VPPCTL=/usr/local/bin/vppctl-$1 VAT2=/usr/local/bin/vat2-$1 \
  PRISMA_API_SEGMENT="/dev/shm/$2-global_vm" PRISMA_CONFIG="/etc/prisma-routing/$1.conf" \
  python3 /src/src/prismash/prismash.py \
    -c "configure" \
    -c "interface host-$1-in"  -c "vrrp 1 address $VIP_IN"  -c "vrrp 1 priority $3" \
    -c "vrrp 1 track host-$1-out decrement 150" -c "exit" \
    -c "interface host-$1-out" -c "vrrp 2 address $VIP_OUT" -c "vrrp 2 priority $3" \
    -c "vrrp 2 track host-$1-in decrement 150" -c "exit" \
    -c "commit" 2>&1
}
# The other shape somebody would reach for, kept beside the first because the
# two fail differently and a reader needs both. See the paragraph above.
ask_cgnat_ha() {   # <box> <api prefix> <peer address>
  VPPCTL=/usr/local/bin/vppctl-$1 VAT2=/usr/local/bin/vat2-$1 \
  PRISMA_API_SEGMENT="/dev/shm/$2-global_vm" PRISMA_CONFIG="/etc/prisma-routing/$1.conf" \
  python3 /src/src/prismash/prismash.py \
    -c "configure" -c "nat cgnat ha peer $3 priority 200" -c "commit check" \
    -c "commit" 2>&1
}
echo
echo "=== stage 2: what happens when the product is asked for a pair ==="
echo "  an address on the interface, which is where VRRP would live:"
if [ "$MODE" = "off" ]; then
  echo "  off: no virtual router is configured on either box, which is the"
  echo "  box this lab was written against. Every check about the service"
  echo "  address has to go red."
else
  echo "  box a:"; configure_ha a hxa 200 | sed 's/^/    /'
  echo "  box b:"; configure_ha b hxb 100 | sed 's/^/    /'
fi
echo "  Since #7 the VTY has a 'vrrp' of its own and it goes to the DATAPLANE,"
echo "  over the binary API, onto the interface that carries the packets - not"
echo "  to FRR and not onto the linux_cp TAP. lab/vrrp.sh measures the election,"
echo "  the virtual MAC and the takeover; what is still missing here is what"
echo "  THIS lab is about, which is the CGNAT half surviving it (#9)."
echo
echo "  and under the CGNAT hierarchy, where an operator would look for it:"
echo "  box a:"; ask_cgnat_ha a hxa 200.150.0.12 | sed 's/^/    /'
# This read "Accepted, committed, and then absent from the running
# configuration", and the first two words had stopped being true: the VTY
# refuses `nat cgnat ha` at the prompt, and since #376 the `commit` behind a
# refused statement is refused with it rather than printing "commit complete".
# What the line below counts is unchanged and is still the point - there is no
# HA to configure here, which is what #7 names.
echo "  Refused at the prompt, and the commit behind it refused with it, so"
echo "  the running configuration never carried it either:"
ask_cgnat_ha a hxa 200.150.0.12 >/dev/null 2>&1
VPPCTL=/usr/local/bin/vppctl-a VAT2=/usr/local/bin/vat2-a \
  PRISMA_API_SEGMENT=/dev/shm/hxa-global_vm PRISMA_CONFIG=/etc/prisma-routing/a.conf \
  python3 /src/src/prismash/prismash.py -c "show running-config" 2>/dev/null \
  | grep -c "ha peer" | sed 's/^/    lines matching "ha peer": /'
echo
echo "  the dataplane already ships RFC 5798, and nothing asks it for a router:"
vb a show plugins | grep -i vrrp | sed 's/^/  /'
echo "    a: show vrrp vr -> $(vb a show vrrp vr | tr '\n' ' ')"
echo "    b: show vrrp vr -> $(vb b show vrrp vr | tr '\n' ' ')"

# ---------------------------------------- stage 3: the service address, before
#
# From here on the subscriber is pointed at the service address and the
# upstream sends the pool to the service address, and NEITHER IS TOUCHED AGAIN.
# Whoever answers for them is the product's business.
echo
echo "=== stage 3: the service, through an address the product has to own ==="
ip netns exec sub ip route replace default via "$VIP_IN"
ip netns exec net ip route replace 200.150.16.0/28 via "$VIP_OUT"
# A BOUNDED WAIT AND NOT A SLEEP, since #7 made the service addresses answer.
# An election at the RFC's default interval takes about three and a half
# seconds and two ARP exchanges follow it, so the two-second sleep that stood
# here - written when nothing ever answered and any wait was as good as any
# other - measured the election rather than the service. Nothing is typed at
# either box in here and nothing is moved: it waits for the product to be
# ready and then asks it the question.
_settle=0
while [ "$_settle" -lt 20 ]; do
  case "$(probe "$SUB_SERVICE" 40000 1)" in 1/1\ *) break ;; esac
  _settle=$((_settle + 1))
done
echo "  the pair answered for the service address after ${_settle}s"
BEFORE=$(probe "$SUB_SERVICE" 40001 4)
BEFORE_SRC=$(echo "$BEFORE" | cut -d' ' -f2)
ACTIVE_BEFORE=$(receiving_box)
echo "  round trips through $VIP_IN: ${BEFORE:-(nothing came back)}"
echo "  the box receiving the subscriber's traffic: $ACTIVE_BEFORE"
echo "  what the subscriber resolved for its gateway:"
ip netns exec sub ip neigh show | sed 's/^/    /'
ip netns exec sub ip neigh show | grep -q . || echo "    (nothing answered)"

# ------------------------------------------------------ causing three failures

# Wait for the service to come back, and say how long it took. A window rather
# than a sleep, because "it took 3 s" and "it took 19 s" are different products
# and a fixed sleep reports them as the same.
await_service() {   # -> AWAIT_OK, AWAIT_SECS, AWAIT_SRC
  AWAIT_OK=no; AWAIT_SECS=$TAKEOVER_WINDOW; AWAIT_SRC="-"
  _t=0
  while [ "$_t" -lt "$TAKEOVER_WINDOW" ]; do
    _r=$(probe "$SUB_SERVICE" 40001 1)
    case "$_r" in
      1/1\ *) AWAIT_OK=yes; AWAIT_SECS=$_t
              AWAIT_SRC=$(echo "$_r" | cut -d' ' -f2); return 0 ;;
    esac
    _t=$((_t + 1))
  done
  return 1
}

pid_of() { eval "echo \"\$VPP_PID_$1\""; }

# The stop an appliance performs: what the shipped drop-in puts in ExecStop,
# then the signal systemd sends afterwards. Read out of the file rather than
# retyped, the way lab/ipfix-stop.sh does it, so a drop-in that loses its drain
# takes this with it.
EXECSTOP=$(sed -n 's/^ExecStop=-\{0,1\}//p' \
           /src/appliance/systemd/vpp-stop-drain.conf)

fail_link() {   # the uplink goes away and the box stays up
  #
  # TWO COMMANDS FOR ONE FAILURE, and the second one is here because of a
  # measurement rather than for convenience. The cable is pulled at the
  # bridge, which is what an operator's cable is - and this VPP does not see
  # it: an af_packet host interface goes on reporting "up" with the veth's
  # peer down, with its own port down in Linux, and with the veth deleted
  # (lab/vrrp.sh prints all three readings on every run, and a VPP tap
  # behaves the same). A pulled cable the dataplane cannot see is not the
  # failure this stage is about - it is a lab with no failure in it, which
  # reports the product as broken for a reason that has nothing to do with
  # the product.
  #
  # So the port is taken down in the dataplane as well, which is the state a
  # card whose cable is out reports on real hardware. That is CAUSING the
  # failure, which this lab may do; it is not configuring the box out of it,
  # and nothing is typed at the other box. The one question left for hardware
  # (#28) is whether a driver notices the cable itself.
  echo "  pulling box $1's uplink: ip link set $1-out-br down"
  ip link set "$1-out-br" down
  echo "  and down in the dataplane, which is what a real card would report:"
  echo "    set interface state host-$1-out down"
  vb "$1" set interface state "host-$1-out" down
}
fail_service() {   # planned maintenance: the dataplane is stopped properly
  _p=$(pid_of "$1")
  echo "  ExecStop on box $1: ${EXECSTOP:-(none in the drop-in)}"
  [ -n "$EXECSTOP" ] && vb "$1" bpa stop-drain 2>&1 | sed 's/^/    /'
  echo "  SIGTERM -> box $1 vpp pid $_p"
  kill -TERM "$_p" 2>/dev/null
  _n=0; while kill -0 "$_p" 2>/dev/null && [ "$_n" -lt 20 ]; do
    sleep 1; _n=$((_n + 1)); done
  kill -0 "$_p" 2>/dev/null && kill -9 "$_p" 2>/dev/null
  echo "  gone after ${_n}s"
}
fail_crash() {   # the power loss: no warning, and no chance to hand anything on
  _p=$(pid_of "$1")
  echo "  SIGKILL -> box $1 vpp pid $_p"
  kill -9 "$_p" 2>/dev/null
  _n=0; while kill -0 "$_p" 2>/dev/null && [ "$_n" -lt 20 ]; do
    sleep 1; _n=$((_n + 1)); done
  echo "  gone after ${_n}s"
}

# One round: find who is receiving the subscriber's traffic, break that box the
# given way, and see whether the service comes back somewhere. The lab never
# says who should take over, only that somebody has to.
round() {   # <label> <link|service|crash> -> VICTIM, AWAIT_*, MOVED
  _label=$1; _how=$2
  VICTIM=$(receiving_box)
  if [ "$VICTIM" = none ]; then
    # Nothing is carrying the service, so there is nothing to fail over FROM.
    # Box a is broken anyway and the round still asks its question, because
    # "did the service come back" is the question either way.
    echo "  no box is receiving the subscriber's traffic; breaking box a anyway"
    VICTIM=a
  else
    echo "  the box receiving the subscriber's traffic: $VICTIM"
  fi
  case "$_how" in
    link)    fail_link    "$VICTIM" ;;
    service) fail_service "$VICTIM" ;;
    crash)   fail_crash   "$VICTIM" ;;
  esac
  await_service
  _now=$(receiving_box)
  if [ "$_now" = none ]; then
    MOVED="nothing is receiving the service"
  elif [ "$_now" = "$VICTIM" ]; then
    MOVED="the broken box $VICTIM still has it"
  else
    MOVED="the service moved to box $_now"
  fi
  echo "  after the $_label failure: ok=$AWAIT_OK after ${AWAIT_SECS}s," \
       "seen upstream as $AWAIT_SRC"
  echo "  $MOVED"
}

echo
echo "=== stage 4: the uplink is pulled, and the box stays alive ==="
echo "  The failure a design without interface tracking does not survive: the"
echo "  box is healthy, still holds the inside address, and has no path"
echo "  upstream. It is asked separately for exactly that reason."
round "pulled uplink" link
LINK_OK=$AWAIT_OK; LINK_SRC=$AWAIT_SRC; LINK_MOVED=$MOVED; LINK_VICTIM=$VICTIM
echo "  restoring box $LINK_VICTIM's uplink, at the bridge and in the dataplane"
ip link set "$LINK_VICTIM-out-br" up
vb "$LINK_VICTIM" set interface state "host-$LINK_VICTIM-out" up
sleep 5

echo
echo "=== stage 5: the dataplane is stopped the way an appliance stops it ==="
round "stop" service
STOP_OK=$AWAIT_OK; STOP_SRC=$AWAIT_SRC; STOP_MOVED=$MOVED; STOP_VICTIM=$VICTIM
echo "  bringing box $STOP_VICTIM back, so the crash round has two boxes again"
rm -f "/dev/shm/hx$STOP_VICTIM-global_vm" "/dev/shm/hx$STOP_VICTIM-vpe-api"
start_box "$STOP_VICTIM" "hx$STOP_VICTIM"
case "$STOP_VICTIM" in
  a) configure_box a 100.64.3.252/22 200.150.0.11/24 ;;
  b) configure_box b 100.64.3.251/22 200.150.0.12/24 ;;
esac
[ "$MODE" = "off" ] || configure_ha "$STOP_VICTIM" "hx$STOP_VICTIM" 200 \
  >/dev/null 2>&1
sleep 3

echo
echo "=== stage 6: the active box is killed, and an open connection with it ==="
# The TCP half of the criterion. A connection is opened through whoever is
# active and held open; the failure happens; the lab then asks that SAME
# connection to carry a byte, and separately opens a new one. Connections are
# expected to drop and the lab says so, so that nobody reads a green run as
# "connections survived" - but only where a takeover actually happened. On a
# box where nothing took over the connection is dead for the boring reason,
# and calling that "dropped as expected" would award a pass for a total outage.
ip netns exec sub python3 "$FLOW" tcp-hold "$SUB_SERVICE" 40002 "$UPSTREAM" \
    "$TCP_PORT" /tmp/hold.flag 90 5 > /tmp/hold.out 2>&1 &
HOLD_PID=$!
sleep 3
HOLD_PUBLIC=$(sed -n 's/^public //p' /tmp/hold.out)
echo "  the held connection's public address: ${HOLD_PUBLIC:-(it never connected)}"
round "kill" crash
CRASH_OK=$AWAIT_OK; CRASH_SRC=$AWAIT_SRC; CRASH_MOVED=$MOVED
CRASH_VICTIM=$VICTIM
touch /tmp/hold.flag
wait "$HOLD_PID" 2>/dev/null
HOLD_RESULT=$(sed -n 's/^survived //p' /tmp/hold.out)
echo "  the held connection after the kill: ${HOLD_RESULT:-(no answer)}"
NEWCONN=$(ip netns exec sub python3 "$FLOW" tcp-probe "$SUB_SERVICE" 40003 \
          "$UPSTREAM" "$TCP_PORT" 5 2>/dev/null | sed -n 's/^result //p')
echo "  a new connection from the same subscriber: ${NEWCONN:-(nothing)}"
NEW_SRC=$(echo "$NEWCONN" | sed -n 's/^ok //p')

# The one line that says whether the drop check measured anything at all.
if [ "$CRASH_OK" = yes ]; then
  case "$HOLD_RESULT" in
    no*)  DROP_VERDICT="dropped across a takeover, which is what this design claims" ;;
    yes*) DROP_VERDICT="SURVIVED the takeover, which this design does not claim" ;;
    *)    DROP_VERDICT="no answer from the held connection" ;;
  esac
else
  DROP_VERDICT="not measurable: nothing took the service over, so the"
  DROP_VERDICT="$DROP_VERDICT connection died of the outage and not of the failover"
fi
echo "  in-flight connections: $DROP_VERDICT"

echo
echo "=== what the upstream saw, which is the only address anybody asserts on ==="
tail -12 /tmp/upstream.log 2>/dev/null | sed 's/^/  /'
[ -s /tmp/upstream.log ] || echo "  (nothing ever arrived)"

echo
# Stage 6 kills a dataplane on purpose and leaves it dead, and this lab never
# opens the default CLI socket at all, so lab/verdict.sh's "NO DATAPLANE at the
# verdict" always fires under a failing run. The log it prints is the killed
# box's. It is not evidence that the machine could not run the lab.
echo "Stage 6 killed a dataplane on purpose, so the 'NO DATAPLANE at the"
echo "verdict' report below a failing run is that box's log, not a machine"
echo "that could not run this lab."
echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

# The fixture first. If these are red the topology is broken and nothing under
# them is about the product.
expect_in "FIXTURE: box a translates the fixture subscriber on its own address" \
  "3/3" "$FIX_A"
expect_in "FIXTURE: box b translates the fixture subscriber on its own address" \
  "3/3" "$FIX_B"
expect "FIXTURE: and both gave it the same public address" \
  sh -c "[ -n '$FIX_A_IP' ] && [ '$FIX_A_IP' = '$FIX_B_IP' ]"
expect "FIXTURE: both boxes compute the same block for the service subscriber" \
  sh -c "[ -n '$FORMULA_A' ] && [ '$FORMULA_A' = '$FORMULA_B' ]"
expect_in "FIXTURE: a TCP connection crosses the translation" "ok " "$FIX_TCP"
expect_in "FIXTURE: and the drop detector can say a live connection is live" \
  "yes" "$FIX_HOLD"

# Everything below this line is the product's, and is what #7 has to turn green.
expect_in "the subscriber's round trip works through the service address" \
  "4/4" "$BEFORE"
expect "the upstream sees it as the address and block the formula gives" \
  in_block "$BEFORE_SRC"
expect_not_in "a box is receiving the subscriber's traffic" \
  "none" "$ACTIVE_BEFORE"

expect_in "a pulled uplink is survived: the service comes back" "yes" "$LINK_OK"
expect "and on the same public address and block" in_block "$LINK_SRC"
expect_in "and it is no longer the broken box that receives it" \
  "the service moved to box" "$LINK_MOVED"

expect_in "a stopped dataplane is survived: the service comes back" \
  "yes" "$STOP_OK"
expect "and on the same public address and block" in_block "$STOP_SRC"
expect_in "and it is no longer the stopped box that receives it" \
  "the service moved to box" "$STOP_MOVED"

expect_in "a killed dataplane is survived: the service comes back" \
  "yes" "$CRASH_OK"
expect "and on the same public address and block" in_block "$CRASH_SRC"
expect_in "and it is no longer the killed box that receives it" \
  "the service moved to box" "$CRASH_MOVED"

expect "a new connection after the takeover works" \
  sh -c "[ -n '$NEW_SRC' ]"
expect "and gets the same public address and block" in_block "${NEW_SRC:--:-}"
expect_in "in-flight connections drop across the takeover, and this says so" \
  "dropped across a takeover" "$DROP_VERDICT"

verdict
