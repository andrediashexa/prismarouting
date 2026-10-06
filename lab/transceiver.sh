#!/bin/sh
# A port's transceiver: its identity and optical levels (#590), read the way
# the box reads them, and said at the VTY and over SNMP.
#
#   sh tools/lab.sh transceiver       stand-in modules on four ports - an SFP
#                                     past its RX warning, a QSFP28 with one
#                                     low lane, an externally calibrated SFP,
#                                     one without DDM, a healthy one
#   sh tools/lab.sh transceiver off   no stand-in bytes: every check on what a
#                                     module says MUST fail
#
# THE LAB HAS NO SFP. The dataplane here is DPDK with null ports (net_null:
# no card, no cage), so two things are measured apart:
#
#   * THE READ REACHES DPDK. Ethernet0 is left alone, and its read goes
#     through the plugin's resolved offsets into rte_eth_dev_get_module_info,
#     which the null PMD answers -95 (no get_module_info op). A wrong offset
#     is a crash or garbage, not that sentence; a dpdk_plugin.so of another
#     build is "unavailable". This is the check the off run keeps green.
#   * WHAT A MODULE'S BYTES BECOME. The other ports are given a module's
#     EEPROM through `bpa transceiver stand-in`, from test/transceiver_
#     fixtures.py (built from SFF-8472 and SFF-8636 by hand), and everything
#     after the plugin - the VTY's screens, `show interface`'s notes, the
#     SNMP agent's tables through snmpd - is the box's own code reading them.
#     The plugin says `source stand-in` and the VTY "a stand-in", so the
#     bytes cannot pass for a reading.
#
# And a card Linux owns: an af_packet port on a veth, read through the host's
# door with the ioctl `ethtool -m` sends. A veth has no module (EOPNOTSUPP),
# which is what the box's management card on virtio says too.
#
# What the decode does with real hardware is NOT here, and cannot be: an
# X520 with a module in the cage is the only proof of the offsets' arithmetic
# against ixgbe (see the #590 report).
#
# OFF-RED-AT-LEAST: 20
#
# A FLOOR, measured on the off run (#590, 2026-10-06): 20 of its 30 checks
# red - every one about what a module says, at the VTY, in `show interface`'s
# notes and through snmpd. Green in that run and rightly: the eight controls
# (the read reaching DPDK, the Linux card through the door, the refusals, 79
# columns, snmpd's registration), Ethernet0 unreadable over SNMP, and "nothing
# for the healthy module", which no stand-in is also true of. See
# lab/bypass.sh for why it is a floor and not a target.
set -u
MODE=${1:-on}
. /src/lab/lib.sh   # startup_conf, wait_vpp, use_binary_api, vc
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /etc/prisma-routing /etc/snmp \
         /var/lib/snmp /usr/lib/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
HX="python3 /src/src/prismash/prismash.py"
export PRISMA_CONFIG=/etc/prisma-routing/prisma-routing.conf
# The agent and the reader where install.sh puts them: the agent decodes with
# the transceiver.py beside it, and the door runs only the installed one.
install -D -m 0755 /src/appliance/prisma-routing-snmp-agent /usr/lib/prisma-routing/snmp-agent
install -D -m 0644 /src/src/prismash/transceiver.py /usr/lib/prisma-routing/transceiver.py

# DPDK ON, with six null ports and no hugepages: the lab's own startup, the
# test one with the dpdk plugin left enabled.
sed 's/^ *plugin dpdk_plugin.so { disable }//' "$(startup_conf test)" > /tmp/startup-xcvr.conf
printf 'dpdk {\n  no-hugetlb\n  no-pci\n' >> /tmp/startup-xcvr.conf
for n in 0 1 2 3 4 5; do printf '  vdev net_null%s\n' "$n" >> /tmp/startup-xcvr.conf; done
printf '}\n' >> /tmp/startup-xcvr.conf
vpp -c /tmp/startup-xcvr.conf > /tmp/vpp.log 2>&1 &
wait_vpp
use_binary_api
for n in 0 1 2 3 4 5; do vppctl set interface state Ethernet$n up; done
# A card Linux owns, given to the dataplane through af_packet.
ip link add vx0 type veth peer name vx1 2>/dev/null
ip link set vx0 up; ip link set vx1 up
vppctl create host-interface name vx0 >/dev/null
vppctl set interface state host-vx0 up

# THE HOST'S DOORS, as on a box: the VTY and the agent (as Debian-snmp) read
# a Linux card's module through them.
python3 /src/src/prismash/doors.py --serve > /tmp/doors.log 2>&1 &
for _i in 1 2 3 4 5 6 7 8 9 10; do
  [ -S /run/prisma-routing/doors.sock ] && break; sleep 0.5
done

hexof() {   # hexof <fixture expression> - its bytes as one hex word
  python3 -c "import sys; sys.path.insert(0, '/src/test'); \
import transceiver_fixtures as F; print(($1).hex())"
}
if [ "$MODE" = off ]; then
  echo "  MODE=off: no stand-in module. Every check on a module's levels MUST fail."
else
  vppctl bpa transceiver stand-in Ethernet1 type sff-8472 hex "$(hexof 'F.sfp_low_rx()')"
  vppctl bpa transceiver stand-in Ethernet2 type sff-8636 hex "$(hexof 'F.qsfp28()')"
  vppctl bpa transceiver stand-in Ethernet3 type sff-8472 hex "$(hexof 'F.sfp_external()')"
  vppctl bpa transceiver stand-in Ethernet4 type sff-8079 hex "$(hexof 'F.sfp_no_ddm()')"
  vppctl bpa transceiver stand-in Ethernet5 type sff-8472 hex "$(hexof 'F.sfp_internal()')"
fi
BAD=$(vppctl bpa transceiver stand-in Ethernet1 type sff-9999 hex 00 2>&1)

echo "=========== 1. the plugin: through DPDK, and the stand-ins ==========="
RAW0=$(vc show bpa transceiver Ethernet0)
printf '%s\n' "$RAW0" | sed 's/^/  /'
RAW1=$(vc show bpa transceiver Ethernet1)
printf '%s\n' "$RAW1" | head -3 | sed 's/^/  /'
RAWX=$(vc show bpa transceiver host-vx0)
printf '%s\n' "$RAWX" | sed 's/^/  /'
echo "  a stand-in of a type no document has: $BAD"

echo
echo "=========== 2. the VTY ==========="
ALL=$($HX -c "show interface transceiver" 2>&1)
printf '%s\n' "$ALL" | sed 's/^/  | /'
ONE=$($HX -c "show interface Ethernet1 transceiver" 2>&1)
E0=$($HX -c "show interface Ethernet0 transceiver" 2>&1)
VX=$($HX -c "show interface host-vx0 transceiver" 2>&1)
NONE=$($HX -c "show interface nosuch9 transceiver" 2>&1)
TAB=$($HX -c "show interface" 2>&1)
echo "  -- show interface"
printf '%s\n' "$TAB" | sed 's/^/  | /'
DET=$($HX -c "show interface Ethernet2" 2>&1)
WIDE=$(printf '%s\n%s\n%s\n' "$ALL" "$TAB" "$DET" | awk 'length > 79' | head -3)

echo
echo "=========== 3. SNMP: prTransceiver through snmpd ==========="
$HX -c "configure" -c "snmp enabled yes" -c "snmp listen-address 127.0.0.1" \
    -c "snmp community hexro" -c "commit" 2>&1 | tail -2 | sed 's/^/  /'
grep "transceivers" /etc/snmp/snmpd.conf | sed 's/^/  |/'
chgrp vpp /run/vpp/cli.sock /run/vpp/api.sock /run/vpp/stats.sock 2>/dev/null
chown Debian-snmp:Debian-snmp /var/lib/snmp
pkill snmpd 2>/dev/null; sleep 0.5
snmpd -Lo -f -C -u Debian-snmp -g Debian-snmp -c /etc/snmp/snmpd.conf > /tmp/snmpd.log 2>&1 &
sleep 2
W="snmpwalk -v2c -c hexro -On -t 20 127.0.0.1"
# The ports' ifIndex, from IF-MIB, which the transceiver rows are under.
IFN=$($W 1.3.6.1.2.1.31.1.1.1.1 2>&1)
ix() { printf '%s\n' "$IFN" | awk -v n="\"$1\"" '$NF == n { k = split($1, o, "."); print o[k]; exit }'; }
I1=$(ix Ethernet1); I2=$(ix Ethernet2); I3=$(ix Ethernet3); I4=$(ix Ethernet4)
I0=$(ix Ethernet0); IX=$(ix host-vx0)
echo "  ifIndex: Ethernet0 $I0, Ethernet1 $I1, Ethernet2 $I2, Ethernet3 $I3, Ethernet4 $I4, host-vx0 $IX"
XC=$($W 1.3.6.1.4.1.67033.1.20 2>&1)
printf '%s\n' "$XC" | head -60 | sed 's/^/  | /'
v() { printf '%s\n' "$XC" | awk -v o=".1.3.6.1.4.1.67033.1.20.$1" '$1 == o { print $NF; exit }' | tr -d '"'; }

echo
echo "=========== VERDICT ==========="
# The controls: what the off run keeps green.
expect_in "control: Ethernet0's read reached DPDK, whose null PMD has no module op" \
  "unread no-module the card answered -95 (Operation not supported) on DPDK port 0" "$RAW0"
expect_said "control: and the VTY says the driver cannot read a module" \
  "Ethernet0 the card's driver cannot read a module" "$E0"
expect_said "control: an af_packet port is read from its Linux card, through the door" \
  "host-vx0 the card's driver cannot read a module" "$VX"
expect_in "control: the plugin itself does not read a Linux card" "unread not-dpdk" "$RAWX"
expect_in "control: a name that is neither a port nor a card" "no interface matches nosuch9" "$NONE"
expect_in "control: a stand-in of a type no document has is refused" "a type sff-8079" "$BAD"
expect "control: every screen fits 79 columns" test -z "$WIDE"
expect_in "control: snmpd carries the transceivers' registration" "--transceivers" \
  "$(cat /etc/snmp/snmpd.conf 2>/dev/null)"
# What the modules say.
expect_in "the plugin hands the stand-in's bytes over, marked" \
  "interface Ethernet1 source stand-in type sff-8472 length 512" "$RAW1"
expect_in "show interface Ethernet1 transceiver: the module's identity" \
  "part        RSMPP8510GL-03D  revision 2.1" "$ONE"
expect_in "its RX power in dBm, past its low warning" \
  "rx power (dBm)  -19.00      0.00     -1.00    -18.01    -20.00  low warn" "$ONE"
expect_in "and in mW" "rx power (mW)   0.0126" "$ONE"
expect_in "its temperature against its thresholds" \
  "temp (C)         36.50     75.00     70.00      0.00     -5.00  ok" "$ONE"
expect_in "and it says the bytes are a stand-in's" "a stand-in (a lab's bytes" "$ONE"
expect_in "show interface transceiver: the QSFP28's lane 4, low" \
  "rx4 (dBm)       -12.50      5.50      4.50    -10.00    -13.60  low warn" "$ALL"
expect_in "the externally calibrated module, through its constants" \
  "ddm         externally calibrated" "$ALL"
expect_said "and its temperature is the calibrated one, 36.50" \
  "Ethernet3: SFP/SFP+/SFP28, LC connector, 1310 nm vendor FINISAR CORP." "$ALL"
expect_said "a module without DDM says so" \
  "ddm none: the module does not implement diagnostics (DDM)" "$ALL"
expect_said "show interface notes the RX power past its warning" \
  "Ethernet1: rx power -19.00 dBm, below its low warning (-18.01 dBm)" "$TAB"
expect_said "and the QSFP's low lane" \
  "Ethernet2: rx power lane 4 -12.50 dBm, below its low warning (-10.00 dBm)" "$TAB"
expect_not_said "and nothing for the healthy module" "Ethernet5: rx power" "$TAB"
expect_said "the one-interface screen carries the note too" \
  "rx power lane 4 -12.50 dBm" "$DET"
expect "SNMP: Ethernet1's module is ddm(1), its state warning(2)" \
  test "$(v 1.1.3.$I1)" = 1 -a "$(v 1.1.18.$I1)" = 2
expect "SNMP: its lane's RX power -19.00 dBm (-1900), lowWarning(3)" \
  test "$(v 2.1.3.$I1.1)" = -1900 -a "$(v 2.1.8.$I1.1)" = 3
expect "SNMP: its RX low warning threshold -18.01 dBm (-1801)" \
  test "$(v 2.1.6.$I1.1)" = -1801
expect "SNMP: its serial number" test "$(v 1.1.6.$I1)" = FP23H150378
expect "SNMP: the QSFP's lane 4 is lowWarning, lane 1 normal" \
  test "$(v 2.1.8.$I2.4)" = 3 -a "$(v 2.1.8.$I2.1)" = 1
expect "SNMP: the external module's temperature, 36.50 C (3650)" \
  test "$(v 1.1.8.$I3)" = 3650
expect "SNMP: the module without DDM is noDdm(2), with no levels" \
  test "$(v 1.1.3.$I4)" = 2 -a -z "$(v 1.1.8.$I4)"
expect "SNMP: Ethernet0, whose driver cannot read a module, is unreadable(5)" \
  test "$(v 1.1.3.$I0)" = 5
verdict
