#!/bin/sh
# Etapa 1 do plano de controle: provar a cola entre VPP e Linux.
#
#   linux_cp: a VPP interface becomes a TAP in Linux, with a synced address
#   linux_nl: a route installed in Linux appears in the VPP FIB
#
# Se isto funciona, o FRR funciona: ele so enxerga netdev normal.
#
# The pair is created BY THE PRODUCT here, and that is the point of the file.
# Until #182 this lab ran `vppctl lcp create` itself, so it proved the glue on
# a box that had been set up by hand and said nothing about the box an operator
# receives - where nothing created a pair at all and every routing protocol was
# configurable and inert. A lab that performs the product's missing step does
# not test the product.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
mkdir -p /run/vpp /vpp_plugins /etc/hexarouting
HX="python3 /src/src/hexash/hexash.py"
export HEXA_CONFIG=/etc/hexarouting/hexarouting.conf
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
use_binary_api

# par veth: um lado fica no Linux "de fora", o outro entra no VPP
ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-out up
ip link set veth-vpp up
ip addr add 10.0.0.2/24 dev veth-out 2>/dev/null

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0
until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -25 /tmp/vpp.log; exit 1; }

echo "=== plugins de plano de controle carregados ==="
vppctl show plugins | grep -iE "linux_cp|linux_nl" | sed 's/^/  /'

echo
echo "=== VPP absorve o veth, e por enquanto o Linux nao ve nada ==="
vppctl create host-interface name veth-vpp
vppctl set interface state host-veth-vpp up
# The negative half (#182). Recorded BEFORE the product is asked for a pair,
# because an assertion made only after the fact cannot say whether the state it
# found was produced by the commit or was there all along - and here it is the
# absence that carries the argument: VPP owns the port outright, so with no
# pair there is no netdev for it, and FRR, which sees netdevs and nothing else,
# has no interface to run a protocol on.
NOPAIR_LINKS=$(ip -o link show 2>/dev/null)
NOPAIR_LCP=$(vppctl show lcp 2>&1)
# The boot self-test, run against a REAL dataplane rather than against the
# stubs in test/test_selftest.sh. Those cases pin the logic; this one pins the
# parse, which is the half that breaks when VPP changes the shape of a listing
# and the half no stub can hold honest. A box that cannot route has to say so
# on the console before somebody configures routing on it (#182).
ST_BEFORE=$(HEXA_SELFTEST_OUT=/dev/stdout sh /src/appliance/selftest 2>/dev/null)
echo "  what the self-test says about pairing:"
printf '%s\n' "$ST_BEFORE" | grep -i "pair" | sed 's/^/    /'
echo "  netdevs Linux has:"; printf '%s\n' "$NOPAIR_LINKS" \
  | awk -F': ' '{print $2}' | tr '\n' ' ' | sed 's/^/    /'; echo
echo "  show lcp:"; printf '%s\n' "$NOPAIR_LCP" | sed 's/^/    /'

echo
echo "=== o produto cria o par: linux-name, pela API binaria ==="
# `lcp_itf_pair_add_del`, not `vppctl lcp create`: the VTY is off the CLI
# socket (#76), and the name is the operator's own so that FRR, `ip` and every
# script they have can be written against a spelling that is predictable.
PAIRED=$($HX -c "configure" -c "interface host-veth-vpp" \
         -c "linux-name vppctl0" -c "exit" -c "commit" 2>&1)
printf '%s\n' "$PAIRED" | tail -3 | sed 's/^/  /'
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
vppctl show lcp | sed 's/^/  /'
ST_AFTER=$(HEXA_SELFTEST_OUT=/dev/stdout sh /src/appliance/selftest 2>/dev/null)
echo "  and now:"
printf '%s\n' "$ST_AFTER" | grep -i "pair" | sed 's/^/    /'

echo
echo "=== does the TAP exist in Linux, with the address synced? ==="
echo "expected: vppctl0 with 10.0.0.1/24, propagated by lcp-sync"
ip -4 addr show vppctl0 2>&1 | grep -E "vppctl0|inet" | sed 's/^/  /'

echo
echo "=== the TAP talks: ping from Linux to the far side of the veth ==="
ping -c 2 -W 2 10.0.0.2 2>&1 | tail -3 | sed 's/^/  /'

echo
echo "=== linux_nl: a route installed in Linux must reach the VPP FIB ==="
ip route add 10.99.0.0/24 via 10.0.0.2 dev vppctl0 2>&1
echo "route in Linux:"; ip route show 10.99.0.0/24 | sed 's/^/  /'
sleep 2
echo "FIB do VPP:"; vppctl show ip fib 10.99.0.0/24 | grep -E "10.99|via|unicast" | head -6 | sed 's/^/  /'

echo
echo "=== o caminho do boot: o arquivo salvo recria o par ==="
# `ExecStart=/usr/bin/hexash --load /etc/hexarouting/hexarouting.conf` is how an
# installed box comes up, and it is the same apply() a commit goes through - so
# the pair a box has at boot is the pair its saved file names. Shown rather than
# reasoned about, which is the whole argument of #182: the half nobody measured
# was the half that did not exist.
#
# On a loopback of its own, not on the veth above. Taking the veth's pair down
# would delete the netdev the kernel route below it lives on, and every
# assertion up there would then be measuring the teardown instead.
$HX -c "configure" -c "interface loop7" -c "linux-name loop7" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  /'
# `save` is what writes the file the boot unit is given; commit only changes
# the box. Measured the hard way - without this the copy below found no file
# and the reload had nothing to replay.
$HX -c "save" 2>&1 | tail -1 | sed 's/^/  /'
cp "$HEXA_CONFIG" /tmp/boot.conf
BOOT_MADE=$(ip -o link show loop7 2>/dev/null)
$HX -c "configure" -c "no interface loop7" -c "commit" 2>&1 | tail -1 | sed 's/^/  /'
BOOT_GONE=$(ip -o link show loop7 2>/dev/null)
echo "  the saved file, which is what the boot unit is given:"
grep -A6 "^interface loop7" /tmp/boot.conf | sed 's/^/    /'
$HX --load /tmp/boot.conf 2>&1 | sed 's/^/  reload: /'
BOOT_BACK=$(ip -o link show loop7 2>/dev/null)

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
# The negative half first: with no pair there is no Linux interface at all.
expect_not_in "with no pair, Linux has no netdev for the port" \
  "vppctl0" "$NOPAIR_LINKS"
expect_not_in "and the dataplane has no pair to report" \
  "itf-pair" "$NOPAIR_LCP"
expect_in "the product creates the pair, and the commit says so" \
  "commit complete" "$PAIRED"
# Requirement 3 of #182, measured against a real dataplane and not a stub.
expect_in "the self-test names the unpaired port before anything is committed" \
  "no linux_cp pair on: host-veth-vpp" "$ST_BEFORE"
expect_in "and says what creates one" "linux-name <name>" "$ST_BEFORE"
expect_in "and once the product has paired it, it says so instead" \
  "1 dataplane interface(s) paired into the kernel" "$ST_AFTER"
expect_not_in "with nothing still reported unpaired" \
  "no linux_cp pair on" "$ST_AFTER"
expect "the TAP exists in Linux" ip link show vppctl0
expect "lcp-sync propagated the address" sh -c "ip -4 addr show vppctl0 | grep -q 10.0.0.1"
expect "linux_nl carried the route into the VPP FIB" sh -c "vppctl show ip fib 10.99.0.0/24 | grep -q '^10.99.0.0/24'"
# Read back from the box rather than remembered, which is the rule #161 set.
expect_in "the pair is in the running configuration, read off the dataplane" \
  "linux-name vppctl0" "$($HX -c 'show running-config')"
# The boot half of #182's first requirement: not only on commit, but on every
# boot, because the unit replays the file the commit wrote.
expect_in "a loopback the product paired is in the kernel" \
  "loop7" "$BOOT_MADE"
expect_not_in "removing it takes the pair with it" "loop7" "$BOOT_GONE"
expect_in "and replaying the saved file - what hexarouting.service does at boot - brings the pair back" \
  "loop7" "$BOOT_BACK"
verdict
