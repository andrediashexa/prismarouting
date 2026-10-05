#!/bin/sh
# OFF-RED-AT-LEAST: 4
# (the floor: what the no-door run measured red - the BFD-STD-MIB checks)
# The SNMP agent, polled the way an NMS polls it.
#
#   sh tools/lab.sh snmp           -> LAB PASSED
#   sh tools/lab.sh snmp no-door   -> LAB FAILED (#571: no doors daemon, so the
#                                     agent, as Debian-snmp, reaches bfdd only
#                                     itself - which the box's ownership of
#                                     FRR's sockets refuses)
#
# The oracle is a real snmpget against a real snmpd, not the agent's own
# output: an agent that answers its own protocol correctly and is never reached
# through the daemon is an agent nobody can poll.
#
# Two properties matter more than the values:
#   - it is READ-ONLY. An SNMP SET that reconfigures a CGNAT is an outage one
#     typo away.
#   - it returns NOTHING when the dataplane is silent. A monitoring system that
#     keeps reporting yesterday's numbers through an outage hides the outage;
#     one that reports a gap shows it.
set -u
MODE=${1:-on}
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /etc/prisma-routing /etc/snmp /var/lib/snmp
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
HX="python3 /src/src/prismash/prismash.py"
export PRISMA_CONFIG=/etc/prisma-routing/prisma-routing.conf
install -D -m 0755 /src/appliance/prisma-routing-snmp-agent /usr/lib/prisma-routing/snmp-agent

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl bpa pool nb inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl bpa timeouts udp 600 icmp 90 >/dev/null
vppctl set interface bpa in2out pg0
for pp in 40000 40001 40002; do
  vppctl packet-generator new "name t limit 1 node ip4-input interface pg0 \
      size 60-60 data { UDP: 100.64.0.5 -> 8.8.8.8 UDP: $pp -> 53 incrementing 8 }" >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 0.3
  vppctl packet-generator delete t >/dev/null 2>&1
done

# Before snmpd starts, since the agent caches what it reads (#504). The build the dataplane reports, as the installed release: this tree has no
# stamp of its own, and a box that was rebooted since its upgrade has them
# equal - which is the state prSystemDataplaneCurrent is true in.
mkdir -p /usr/share/prisma-routing
vc show plugins | awk '/bpa_plugin.so/ { print $3; exit }' \
  > /usr/share/prisma-routing/VERSION

# #505's features on this dataplane: a VRRP router started on pg0, and an
# LACP bundle with no member yet - a bundle at zero of zero is a state the
# table has to be able to say.
vppctl vrrp vr add pg0 vr_id 7 priority 150 interval 100 100.64.3.1 >/dev/null
vppctl vrrp proto start pg0 vr_id 7 >/dev/null
vppctl create bond mode lacp >/dev/null

# BFD-STD-MIB (#559): bfdd, with one session nobody answers - a `peer` on a
# dummy interface, so its state is down and stays down, which this lab knows.
ip link add d0 type dummy 2>/dev/null; ip link set d0 up
ip addr add 192.0.2.1/24 dev d0 2>/dev/null
mkdir -p /var/run/frr /etc/frr
for d in zebra mgmtd staticd bfdd; do printf 'hostname prisma-routing\n' > "/etc/frr/$d.conf"; done
printf 'service integrated-vtysh-config\n' > /etc/frr/vtysh.conf
chown -R frr:frr /var/run/frr /etc/frr
start_frr bfdd
vtysh -c "conf t" -c "bfd" -c "peer 192.0.2.77 interface d0" -c exit -c exit \
  2>&1 | sed 's/^/  frr: /'
# FRR'S SOCKETS AS THE BOX HAS THEM (#571), measured on 10.40.200.166
# (0.150.10, the containers): bfdd.vty srwxrwx--- frr:frr, /etc/frr/vtysh.conf
# -rw-r----- frr:frr. Here Debian's FRR makes them frrvty's, and 0.150.10's
# lab passed with Debian-snmp in frrvty while the box's MIB answered nothing.
chgrp frr /var/run/frr/*.vty; chmod 0770 /var/run/frr/*.vty
chmod 0640 /etc/frr/vtysh.conf
# And snmpd's user in neither group: frrvty is what 0.150.10 granted, frr is
# what would have made it work and is vtysh's whole CLI.
gpasswd -d Debian-snmp frr >/dev/null 2>&1; gpasswd -d Debian-snmp frrvty >/dev/null 2>&1
# THE HOST'S DOORS (#571): the agent reads bfdd through them, as on the box,
# where prisma-routing-doors.service runs them as root. The no-door run is
# a box without them - what the agent read before.
if [ "$MODE" != no-door ]; then
  python3 /src/src/prismash/doors.py --serve > /tmp/doors.log 2>&1 &
  for _i in 1 2 3 4 5 6 7 8 9 10; do
    [ -S /run/prisma-routing/doors.sock ] && break; sleep 0.5
  done
fi

echo "=========== 1. turn SNMP on from the VTY ==========="
$HX -c "configure" \
    -c "snmp enabled yes" -c "snmp listen-address 127.0.0.1" \
    -c "snmp enterprise-number 99999" -c "snmp community hexro" \
    -c "commit" 2>&1 | sed 's/^/  /'
CONF=$(cat /etc/snmp/snmpd.conf 2>/dev/null || echo "")
printf '%s\n' "$CONF" | sed 's/^/  |/'
# THE RETENTION LOG'S FILESYSTEM, AS A REAL ONE (#497). In this container /
# and /tmp are overlay, which net-snmp leaves out of dskTable on purpose, so a
# disk line on either stays empty here while it answers on the box, where both
# are ext4. /src is a bind of the host's ext4, so a directory of it mounted on
# the path the box uses is the same kind of filesystem the box has.
mkdir -p /src/.lab-retention /var/log/prisma-routing
mount --bind /src/.lab-retention /var/log/prisma-routing
# snmpd is not under systemd in this container; start it the way the unit does.
pkill snmpd 2>/dev/null; sleep 0.5
# THE SOCKETS AS THE BOX HAS THEM: root:vpp, srwxrwxr-x, measured on the #475
# bench. Here VPP leaves them root:root, and Debian-snmp could not connect to
# them whatever group it was in - which would test nothing about the box.
chgrp vpp /run/vpp/cli.sock /run/vpp/api.sock /run/vpp/stats.sock 2>/dev/null
# Debian's package owns /var/lib/snmp as Debian-snmp; the agent keeps its
# ifIndex file there (#498).
chown Debian-snmp:Debian-snmp /var/lib/snmp
# AS THE BOX RUNS IT (#499): Debian's unit starts snmpd as Debian-snmp, and
# the agent it spawns is that user too. Run as root here, the lab read the
# dataplane through sockets the box's snmpd could not open, and passed.
snmpd -Lo -f -C -u Debian-snmp -g Debian-snmp -c /etc/snmp/snmpd.conf > /tmp/snmpd.log 2>&1 &
sleep 2

echo "  --- the snmpd user and the dataplane's sockets ---"
id Debian-snmp | sed 's/^/    /'
ls -la /run/vpp/ | sed 's/^/    /'
ls -la /etc/snmp/snmpd.conf | sed 's/^/    /'
printf 'get\n1.3.6.1.2.1.123.1.1.2.0\n' | runuser -u Debian-snmp -- /usr/lib/prisma-routing/snmp-agent 2>&1 | sed 's/^/    as Debian-snmp: /'
echo "  --- what the agent itself serves, run by hand ---"
printf 'getnext\n1.3.6.1.4.1\n' | /usr/lib/prisma-routing/snmp-agent | sed 's/^/    /'
printf 'get\n1.3.6.1.4.1.99999.1.1.0\n' | /usr/lib/prisma-routing/snmp-agent | sed 's/^/    /'
echo "  --- the interfaces, as Debian-snmp ---"
printf 'getnext\n1.3.6.1.2.1.2\ngetnext\n1.3.6.1.2.1.2.2.1.2\n' | su -s /bin/sh Debian-snmp -c '/usr/lib/prisma-routing/snmp-agent --interfaces' 2>&1 | sed 's/^/    /'

echo "  --- BFD-STD-MIB, the agent as Debian-snmp reading bfdd ---"
printf 'getnext\n1.3.6.1.2.1.222.1.2.1.11\n' | runuser -u Debian-snmp -- \
  /usr/lib/prisma-routing/snmp-agent --bfd 2>&1 | sed 's/^/    /'

echo
echo "=========== 2. poll it ==========="
# BFD-STD-MIB (#559), through snmpd: the session's state, its neighbour and
# its transmit interval, by the index the table gives it.
BW="snmpwalk -v2c -c hexro -On 127.0.0.1"
BFDSTATE=$($BW 1.3.6.1.2.1.222.1.2.1.11 2>&1)
BFDIX=$(printf '%s\n' "$BFDSTATE" | awk -F'[ .]' '/^\.1\.3\.6\.1\.2\.1\.222\.1\.2\.1\.11\./ {print $13; exit}')
BFDDST=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.222.1.2.1.22.${BFDIX:-0} 2>&1)
BFDTX=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.222.1.2.1.25.${BFDIX:-0} 2>&1)
BFDNOTIF=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.222.1.1.3.0 2>&1)
echo "  BFD-STD-MIB: $BFDSTATE / dst $BFDDST / tx $BFDTX / notifications $BFDNOTIF"
# WHAT AN NMS SEES FIRST (#497): the box's identity, CPU, memory and disks,
# which net-snmp serves and the view used to keep out.
DESCR=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.1.1.0 2>&1)
# The same community in v1 (#511): v2c and v3 only, by the maintainer's
# decision. A v1 request is answered as an unknown community - which snmpget
# sees as no answer at all.
V1=$(snmpget -v1 -c hexro -Ovq -t 1 -r 0 127.0.0.1 1.3.6.1.2.1.1.1.0 2>&1)
UPTIME=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.1.3.0 2>&1)
# The processors as HOST-RESOURCES lists them: hrDeviceType rows that are
# hrDeviceProcessor. Not hrProcessorLoad, which net-snmp only fills after a
# minute of samples - a poll two seconds after start would read an empty table
# on a box that has one.
CPUS=$(snmpwalk -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.25.3.2.1.2 2>&1 | grep -c "25.3.1.3$")
MEMTOTAL=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.4.1.2021.4.5.0 2>&1)
DISKS=$(snmpwalk -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.4.1.2021.9.1.2 2>&1)
# THE DATAPLANE'S CARDS IN IF-MIB (#498): the agent's registrations above
# net-snmp's own module, so the rows are VPP's interfaces and not Linux's.
IFDESCR=$(snmpwalk -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.2.2.1.2 2>&1)
PG0=$(snmpwalk -v2c -c hexro -On 127.0.0.1 1.3.6.1.2.1.2.2.1.2 2>&1 | awk '/"pg0"/{n=split($1, o, "."); print o[n]; exit}')
PG0IN=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.31.1.1.1.6.${PG0:-0} 2>&1)
PG0UC=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.31.1.1.1.7.${PG0:-0} 2>&1)
PG0OPER=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.2.2.1.8.${PG0:-0} 2>&1)
printf '%s\n' "$IFDESCR" | sed 's/^/  ifDescr: /'
echo "  pg0 is ifIndex ${PG0:-none}: ifHCInOctets $PG0IN, ifOperStatus $PG0OPER"
echo "  sysDescr:  $DESCR"
echo "  sysUpTime: $UPTIME"
echo "  processors in hrDeviceTable: $CPUS"
echo "  memTotalReal: $MEMTOTAL"
printf '%s\n' "$DISKS" | sed 's/^/  dskPath: /'
UDPT=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.123.1.1.2.0 2>&1)
ICMPT=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.123.1.1.3.0 2>&1)
SUBS=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.4.1.99999.1.1.0 2>&1)
PORTS=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.4.1.99999.1.2.0 2>&1)
# The value the DATAPLANE holds, so the poll below is compared against the box
# and not against the number typed into this script. The strip is the whole
# reason this line is worth reading twice: vppctl ends its lines with CR+LF and
# awk keeps the CR on the last field, so `awk '$1=="udp"{print $2}'` returns
# "600<CR>". This line used to be
#
#   vppctl show bpa timeouts | tr ' ' '\n' | grep -A0 -x -m1 600 || echo 600
#
# and `grep -x` compares WHOLE LINES, so it was matching "600" against
# "600<CR>" and never matching anything. The `|| echo 600` then printed the
# literal, so the report said "vppctl says 600" whatever the dataplane held -
# a cross-check that had been inert since it was written (#249).
DP_UDPT=$(vc show bpa timeouts | awk '$1 == "udp" { print $2; exit }')
echo "  natUdpDefIdleTimeout  = $UDPT   (the dataplane says ${DP_UDPT:-nothing})"
echo "  natIcmpDefIdleTimeout = $ICMPT"
echo "  subscribers           = $SUBS"
echo "  portsUsed             = $PORTS"

echo
echo "=========== 2b. PRISMA-ROUTING-MIB, loaded as an NMS loads it (#504) ==="
# The file the box prints, with its number filled in, checked by libsmi at
# its strictest and then LOADED: every row below is read by name, so an OID
# the agent serves and the MIB does not define would come back as a number.
# The SNMPv2 modules it imports are IETF's, which Debian ships only in
# non-free; test/mibs carries the three.
mkdir -p /tmp/mibs
cp /src/test/mibs/*.txt /tmp/mibs/
PRISMA_SNMP_MIB=/src/appliance/PRISMA-ROUTING-MIB.in $HX -c "show snmp mib" \
  > /tmp/mibs/PRISMA-ROUTING-MIB.txt 2>&1
SMILINT=$(SMIPATH=/tmp/mibs smilint -l 6 -s /tmp/mibs/PRISMA-ROUTING-MIB.txt 2>&1)
# The Zabbix template (#514), printed the same way: {$PRISMA.PEN} defaulting
# to the number this box serves under.
ZBXPEN=$(PRISMA_ZABBIX_TEMPLATE=/src/appliance/prisma-routing-zabbix.json.in \
         $HX -c "show snmp zabbix-template" 2>&1 | python3 -c "import json, sys
t = json.load(sys.stdin)['zabbix_export']['templates'][0]
print([m['value'] for m in t['macros'] if m['macro'] == '{\$PRISMA.PEN}'][0])" 2>&1)
PM="snmpget -v2c -c hexro -M /tmp/mibs -m PRISMA-ROUTING-MIB -Ov -Oq 127.0.0.1"
POOLNAME=$($PM PRISMA-ROUTING-MIB::prPoolName.1 2>&1)
POOLMODE=$($PM PRISMA-ROUTING-MIB::prPoolMode.1 2>&1)
POOLCAP=$($PM PRISMA-ROUTING-MIB::prPoolCapacity.1 2>&1)
POOLSUBS=$($PM PRISMA-ROUTING-MIB::prPoolSubscribers.1 2>&1)
POOLSESS=$($PM PRISMA-ROUTING-MIB::prPoolSessions.1 2>&1)
POOLPORTS=$($PM PRISMA-ROUTING-MIB::prPoolPortsInUse.1 2>&1)
SESSIONS=$($PM PRISMA-ROUTING-MIB::prSessions.0 2>&1)
BUILD=$($PM PRISMA-ROUTING-MIB::prSystemDataplaneBuild.0 2>&1)
CURRENT=$($PM PRISMA-ROUTING-MIB::prSystemDataplaneCurrent.0 2>&1)
TCPON=$($PM PRISMA-ROUTING-MIB::prLogTransportEnabled.1 2>&1)
FIT=$($PM PRISMA-ROUTING-MIB::prSessionsFit.0 2>&1)
WSESS=$(snmpwalk -v2c -c hexro -M /tmp/mibs -m PRISMA-ROUTING-MIB -Ovq 127.0.0.1 \
        PRISMA-ROUTING-MIB::prWorkerSessions 2>&1)
VRIF=$($PM PRISMA-ROUTING-MIB::prVrrpInterface.1 2>&1)
VRST=$($PM PRISMA-ROUTING-MIB::prVrrpState.1 2>&1)
VRPRI=$($PM PRISMA-ROUTING-MIB::prVrrpPriority.1 2>&1)
BNAME=$($PM PRISMA-ROUTING-MIB::prBundleName.1 2>&1)
BMEM=$($PM PRISMA-ROUTING-MIB::prBundleMembers.1 2>&1)
IDENT=$($PM PRISMA-ROUTING-MIB::prIdentityEntries.0 2>&1)
DP_FIT=$($HX -c "show cgnat memory" 2>&1 | awk '/sessions that fit/ { gsub(",", "", $NF); print $NF }')
# NATV2-MIB, RFC 7659 (#512): the IETF's module, loaded as an NMS loads it,
# one instance per pool. test/mibs carries it, cut from the RFC, with the two
# modules it imports that Debian ships only in non-free.
NV="snmpget -v2c -c hexro -M /tmp/mibs -m NATV2-MIB -Ov -Oq 127.0.0.1"
NVALIAS=$($NV NATV2-MIB::natv2InstanceAlias.1 2>&1)
NVPORTS=$($NV NATV2-MIB::natv2InstancePortMapEntries.1 2>&1)
NVSUBS=$($NV NATV2-MIB::natv2PoolAddressMapEntries.1.1 2>&1)
NVMIN=$($NV NATV2-MIB::natv2PoolMinimumPort.1.1 2>&1)
NVBEGIN=$($NV NATV2-MIB::natv2PoolRangeBegin.1.1.1 2>&1)
NVEND=$($NV NATV2-MIB::natv2PoolRangeEnd.1.1.1 2>&1)
NVWALK=$(snmpwalk -v2c -c hexro -M /tmp/mibs -m NATV2-MIB 127.0.0.1 \
         NATV2-MIB::natv2MIB 2>&1)
# VRRPV3-MIB, RFC 6527 (#513): the same router as the IETF's table indexes
# it - pg0's ifIndex from IF-MIB, the VRID, and 1 for IPv4.
VV="snmpget -v2c -c hexro -M /tmp/mibs -m VRRPV3-MIB -Ov -Oq 127.0.0.1"
VVIX="${PG0:-0}.7.1"
VVST=$($VV VRRPV3-MIB::vrrpv3OperationsStatus.$VVIX 2>&1)
VVPRI=$($VV VRRPV3-MIB::vrrpv3OperationsPriority.$VVIX 2>&1)
VVMAC=$($VV VRRPV3-MIB::vrrpv3OperationsVirtualMacAddr.$VVIX 2>&1)
VVCNT=$($VV VRRPV3-MIB::vrrpv3OperationsAddrCount.$VVIX 2>&1)
VVWALK=$(snmpwalk -v2c -c hexro -M /tmp/mibs -m VRRPV3-MIB 127.0.0.1 \
         VRRPV3-MIB::vrrpv3MIB 2>&1)
# IP-MIB (#522): pg0's address under the ifIndex IF-MIB gives pg0, which is
# the join an NMS makes to name an interface by its address. net-snmp's own
# would list the Linux interfaces instead, under Linux's numbering.
IM="snmpget -v2c -c hexro -M /tmp/mibs -m IP-MIB -Ov -Oq 127.0.0.1"
IPFWD=$($IM IP-MIB::ipForwarding.0 2>&1)
IPADIX=$($IM IP-MIB::ipAdEntIfIndex.100.64.3.254 2>&1)
IPADMASK=$($IM IP-MIB::ipAdEntNetMask.100.64.3.254 2>&1)
IPAIX=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.4.34.1.3.1.4.100.64.3.254 2>&1)
IPPFX=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.4.32.1.5.${PG0:-0}.1.4.100.64.0.0.22 2>&1)
IPWALK=$(snmpwalk -v2c -c hexro -M /tmp/mibs -m IP-MIB 127.0.0.1 IP-MIB::ipAddrTable 2>&1)
WALK=$(snmpwalk -v2c -c hexro -M /tmp/mibs -m PRISMA-ROUTING-MIB 127.0.0.1 \
       PRISMA-ROUTING-MIB::prismaRouting 2>&1)
DP_CAP=$(vc show bpa pool | awk '$1 == "capacity" { print $2; exit }')
echo "  smilint: ${SMILINT:-(nothing to say)}"
printf '%s\n' "$WALK" | sed -n '1,12p' | sed 's/^/  /'
echo "  ... $(printf '%s\n' "$WALK" | grep -c .) rows in all"
echo "  pool 1: $POOLNAME, $POOLMODE, capacity $POOLCAP (the dataplane says ${DP_CAP:-nothing})"
echo "  subscribers $POOLSUBS, sessions $POOLSESS, ports $POOLPORTS; prSessions $SESSIONS"
echo "  dataplane build $BUILD, current $CURRENT; TCP transport enabled $TCPON"
echo "  sessions that fit $FIT (show cgnat memory says ${DP_FIT:-nothing}); per worker $(printf '%s' "$WSESS" | tr '\n' ' ')"
echo "  VRRP 1: $VRIF, $VRST, priority $VRPRI; bundle 1: $BNAME with $BMEM member(s); identity entries $IDENT"
echo "  NATV2-MIB: instance 1 $NVALIAS, $NVPORTS port map entries, $NVSUBS address map entries, ports from $NVMIN, range $NVBEGIN - $NVEND"
echo "  ... $(printf '%s\n' "$NVWALK" | grep -c .) NATV2-MIB rows walked"
echo "  VRRPV3-MIB: router $VVIX $VVST, priority $VVPRI, MAC $VVMAC, $VVCNT address(es)"
echo "  ... $(printf '%s\n' "$VVWALK" | grep -c .) VRRPV3-MIB rows walked"
echo "  IP-MIB: ipForwarding $IPFWD; 100.64.3.254 on ifIndex $IPADIX (ipAddressTable $IPAIX, pg0 is ${PG0:-none}), mask $IPADMASK"
printf '%s\n' "$IPWALK" | sed 's/^/    /' | head -12

echo
echo "=========== the boot after an upgrade rewrites snmpd.conf (#523) ==========="
# What an upgrade leaves: the file the release before wrote, with none of this
# release's lines and its release in sysDescr. No SNMP setting changed, so
# only the boot's --load can notice - and before #523 it did not.
$HX -c save >/dev/null 2>&1
sed -i -e '/\.1\.3\.6\.1\.2\.1\.4\.34/d' -e 's/^sysdescr .*/sysdescr Prisma Routing 0.0.1/' \
  /etc/snmp/snmpd.conf
OLDCONF=$(grep -c '4\.34' /etc/snmp/snmpd.conf)
LOADOUT=$($HX --load "$PRISMA_CONFIG" 2>&1)
printf '%s\n' "$LOADOUT" | tail -3 | sed 's/^/  /'
NEWCONF=$(grep -c '4\.34' /etc/snmp/snmpd.conf)
NEWDESCR=$(grep '^sysdescr' /etc/snmp/snmpd.conf)
echo "  lines naming ipAddressTable: $OLDCONF before the load, $NEWCONF after; $NEWDESCR"
expect "the boot's load puts this release's lines back ($OLDCONF -> $NEWCONF)" \
  test "$OLDCONF" = "0" -a "$NEWCONF" -gt 0
expect "and this release's sysDescr ($NEWDESCR)" \
  sh -c "! printf '%s' \"$NEWDESCR\" | grep -q 0.0.1"

echo
echo "=========== the box's own state (#524) ==========="
# THE TUNER'S FILE, as prisma-routing-tune writes it on a box between two
# plans: this container runs VPP from a string and has no tuner, and the
# header is the whole of what the agent reads from it.
mkdir -p /etc/vpp
printf '%s\n' "# BETWEEN TWO PLANS. This boot runs 2 worker(s) on 2-3, isolated now;" \
  "# the next boot isolates 2-4 and runs 3." \
  "# Generated by prisma-routing-tune. Do not edit by hand." \
  "cpu {" "  main-core 1" "  corelist-workers 2-3" "}" > /etc/vpp/startup.conf
# THE SELF-TEST ITSELF, which leaves its verdict for the NMS: this container
# fails most of its checks (no systemd, no installed box), and the counts are
# what is compared, not the verdict.
# Through a pipe: the self-test opens its output for every line, and a file
# would be truncated at each.
PRISMA_SELFTEST_OUT=/dev/stdout timeout 300 sh /src/appliance/selftest 2>&1 \
  | cat > /tmp/selftest.out
STFAILS=$(grep -c '\[FAIL\]' /tmp/selftest.out)
# A COMMIT CONFIRMED, pending and then confirmed by the next commit.
CCOUT=$($HX -c configure -c "snmp community hexcc" -c "commit confirmed 5" 2>&1)
printf '%s\n' "$CCOUT" | grep -v "has no Linux end\|does not see it" \
  | sed 's/^/  commit confirmed: /'
sleep 11
BS="snmpget -v2c -c hexro -M /tmp/mibs -m PRISMA-ROUTING-MIB -Ov -Oq 127.0.0.1"
TSTATE=$($BS PRISMA-ROUTING-MIB::prTuningState.0 2>&1)
TWORK=$($BS PRISMA-ROUTING-MIB::prTuningWorkers.0 2>&1)
TNEXT=$($BS PRISMA-ROUTING-MIB::prTuningWorkersNextBoot.0 2>&1)
SVERD=$($BS PRISMA-ROUTING-MIB::prSelfTestVerdict.0 2>&1)
SFAIL=$($BS PRISMA-ROUTING-MIB::prSelfTestFailures.0 2>&1)
CPEND=$($BS PRISMA-ROUTING-MIB::prConfigConfirmPending.0 2>&1)
CDEAD=$($BS PRISMA-ROUTING-MIB::prConfigConfirmDeadline.0 2>&1)
CBY=$($BS PRISMA-ROUTING-MIB::prConfigLastCommitBy.0 2>&1)
CN1=$($BS PRISMA-ROUTING-MIB::prConfigCommits.0 2>&1)
CCOUT2=$($HX -c configure -c commit 2>&1)
printf '%s\n' "$CCOUT2" | tail -3 | sed 's/^/  commit: /'
sleep 11
CPEND2=$($BS PRISMA-ROUTING-MIB::prConfigConfirmPending.0 2>&1)
CN2=$($BS PRISMA-ROUTING-MIB::prConfigCommits.0 2>&1)
ASTATE=$($BS PRISMA-ROUTING-MIB::prAuditState.0 2>&1)
BSWALK=$(snmpwalk -v2c -c hexro -M /tmp/mibs -m PRISMA-ROUTING-MIB 127.0.0.1 \
         PRISMA-ROUTING-MIB::prTuning 2>&1; \
         snmpwalk -v2c -c hexro -M /tmp/mibs -m PRISMA-ROUTING-MIB 127.0.0.1 \
         PRISMA-ROUTING-MIB::prConfig 2>&1)
echo "  tuning: $TSTATE, $TWORK worker(s) now and $TNEXT next boot"
echo "  self-test: $SVERD, $SFAIL failure(s) (its console printed $STFAILS)"
echo "  commit confirmed: pending $CPEND until $CDEAD, by $CBY; after the confirming commit $CPEND2; changes applied $CN1 -> $CN2"
echo "  configuration trail: $ASTATE"
printf '%s\n' "$BSWALK" | sed 's/^/    /'
expect "the tuner's state as the MIB names it ($TSTATE)" test "$TSTATE" = "betweenPlans"
expect "with the workers now and at the next boot ($TWORK, $TNEXT)" \
  test "$TWORK/$TNEXT" = "2/3"
expect "the self-test's failures, as many as its console printed ($SFAIL)" \
  test "$SFAIL" = "$STFAILS"
expect "a commit confirmed shows pending ($CPEND), with its deadline" \
  sh -c "[ \"$CPEND\" = true ] && printf '%s' \"$CDEAD\" | grep -qE '^\"?[0-9]{4}-[0-9]{2}-[0-9]{2}T'"
expect "by the account that made it ($CBY)" sh -c "printf '%s' \"$CBY\" | grep -q root"
# An empty commit confirms and applies nothing, so it is not a change.
expect "and the commit after it confirmed it ($CPEND2), applying nothing ($CN1 -> $CN2)" \
  sh -c "[ \"$CPEND2\" = false ] && [ \"$CN2\" = \"$CN1\" ]"
expect "the trail's state is one the dataplane can have ($ASTATE)" \
  sh -c "case \"$ASTATE\" in intact|empty) exit 0;; *) exit 1;; esac"
expect_not_in "every row of the box's state resolves to a name in the MIB" \
  "enterprises." "$BSWALK"

echo
echo "=========== 3. a SET must be refused ==========="
SETOUT=$(snmpset -v2c -c hexro 127.0.0.1 1.3.6.1.2.1.123.1.1.2.0 u 5 2>&1)
printf '%s\n' "$SETOUT" | sed 's/^/  /'
AFTERSET=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.123.1.1.2.0 2>&1)
echo "  the value afterwards: $AFTERSET"

echo
echo "=========== 3b. an SNMPv3 user, as far as this lab goes ==========="
# Configuration only. The v3 handshake itself is NOT exercised here: proving it
# needs an snmpget -v3 against a daemon that has consumed its createUser line,
# and net-snmp rewrites that file on first use. Said plainly rather than
# implied by a passing test that only looks at configuration.
$HX -c "configure" -c "snmp-user nms" -c "auth sha averylongpassphrase" \
    -c "priv aes anotherlongpassphrase" -c "exit" -c "commit" 2>&1 | sed 's/^/  /'
V3CONF=$(cat /etc/snmp/snmpd.conf 2>/dev/null || echo "")
V3STORE=$(cat /var/lib/snmp/snmpd.conf 2>/dev/null || echo "")
REDACTED=$($HX -c "show running-config" 2>&1 | grep -E "snmp community|auth |priv " || true)
printf '%s\n' "$REDACTED" | sed 's/^/  /'

echo
echo "=========== 3b. the product's own number, 67033 (#554) ==========="
# Every step above names the operator's 99999. With none named, the box serves
# PRISMA-ROUTING-MIB under the product's IANA number, 67033 since 0.149.13 -
# and no longer under 32473, IANA's documentation number it used until then.
$HX -c "configure" -c "no snmp enterprise-number" -c "commit" 2>&1 | sed 's/^/  /'
# No systemd here: restart snmpd on the file the commit wrote, as step 1 did.
pkill snmpd 2>/dev/null; sleep 0.5
snmpd -Lo -f -C -u Debian-snmp -g Debian-snmp -c /etc/snmp/snmpd.conf >> /tmp/snmpd.log 2>&1 &
sleep 2
PENSUBS=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.4.1.67033.1.1.0 2>&1)
OLDPEN=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.4.1.32473.1.1.0 2>&1)
PENMIB=$(PRISMA_SNMP_MIB=/src/appliance/PRISMA-ROUTING-MIB.in $HX -c "show snmp mib" 2>&1 | grep -m1 "::= { enterprises")
echo "  under 67033: $PENSUBS | under 32473: $OLDPEN | $PENMIB"
$HX -c "configure" -c "snmp enterprise-number 99999" -c "commit" >/dev/null 2>&1
pkill snmpd 2>/dev/null; sleep 0.5
snmpd -Lo -f -C -u Debian-snmp -g Debian-snmp -c /etc/snmp/snmpd.conf >> /tmp/snmpd.log 2>&1 &
sleep 2

echo
echo "=========== 4. the dataplane goes away ==========="
# Not "the numbers freeze" - the numbers must DISAPPEAR.
pkill -f "vpp -c" 2>/dev/null; sleep 3
GONE=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.4.1.99999.1.1.0 2>&1)
echo "  subscribers with VPP stopped: $GONE"

echo
echo "=========== VERDICT ==========="
expect_in "the VTY generated an snmpd configuration" "pass_persist" "$CONF"
expect_in "with the standard NAT-MIB subtree" "1.3.6.1.2.1.123" "$CONF"
expect_in "and the operator's own enterprise subtree" "1.3.6.1.4.1.99999" "$CONF"
expect "with no number named, the product's 67033 answers ($PENSUBS)" \
  test "$PENSUBS" = "$SUBS"
expect "and 32473, the documentation number it used before, does not ($OLDPEN)" \
  sh -c 'case "$1" in *"No Such"*|*"no such"*|*imeout*) exit 0 ;; *) exit 1 ;; esac' _ "$OLDPEN"
expect_in "show snmp mib roots the module at 67033" "enterprises 67033" "$PENMIB"
# The daemon half of read-only. There must never be an rwcommunity line.
expect_not_in "and NO writable community" "rwcommunity" "$CONF"
# Two halves, because either alone passes for the wrong reason. The first
# pins the value this lab asked for; the second pins it to what the DATAPLANE
# answers, and an agent serving a constant is indistinguishable from an agent
# reading the box until somebody asks the box.
expect_in "IF-MIB lists the dataplane's card" "pg0" "$IFDESCR"
expect_not_in "and not Linux's loopback, which net-snmp's own table would" '"lo"' "$IFDESCR"
expect "its 64-bit input counter moved with the traffic sent ($PG0IN)" \
  sh -c "case \"$PG0IN\" in ''|*[!0-9]*) exit 1;; 0) exit 1;; *) exit 0;; esac"
# pg, like virtio and ixgbe (#501), fills no per-type counter.
expect "and so did its unicast packets, which pg does not count by type ($PG0UC)" \
  sh -c "case \"$PG0UC\" in ''|*[!0-9]*) exit 1;; 0) exit 1;; *) exit 0;; esac"
expect "and it reads up ($PG0OPER)" sh -c "case \"$PG0OPER\" in up*|1*) exit 0;; *) exit 1;; esac"
expect_in "sysDescr names the product" "Prisma Routing" "$DESCR"
expect_not_in "and the same community in SNMPv1 gets nothing ($V1)" "Prisma" "$V1"
expect "sysUpTime answers" sh -c "case \"$UPTIME\" in *No\ Such*|*Timeout*|'') exit 1;; *) exit 0;; esac"
expect "HOST-RESOURCES lists the processors ($CPUS)" test "${CPUS:-0}" -ge 1
expect "UCD reports the memory" sh -c "case \"$MEMTOTAL\" in [0-9]*) exit 0;; *) exit 1;; esac"
expect_in "and the retention log's filesystem has a disk threshold" "/var/log/prisma-routing" "$DISKS"
expect "the UDP timeout came back over SNMP" test "$UDPT" = "600"
expect "and it is the value the dataplane actually holds, not a constant" \
  test "$UDPT" = "${DP_UDPT:-unread}"
expect "and the ICMP one" test "$ICMPT" = "90"
expect "the enterprise subtree reports the subscriber" test "$SUBS" = "1"
expect "PRISMA-ROUTING-MIB passes smilint at its strictest, saying nothing" \
  test -z "$SMILINT"
expect_not_in "and every row it walks resolves to a name in it" \
  "iso.3.6.1.4.1" "$WALK"
expect "the pool table names the pool ($POOLNAME)" test "$POOLNAME" = "nb"
expect "and its mode ($POOLMODE)" test "$POOLMODE" = "deterministic"
expect "and its capacity, as the dataplane states it ($POOLCAP)" \
  test "$POOLCAP" = "${DP_CAP:-unread}"
expect "one subscriber, three sessions, three ports in the pool" \
  test "$POOLSUBS/$POOLSESS/$POOLPORTS" = "1/3/3"
expect "and three sessions on the box" test "$SESSIONS" = "3"
expect "the running dataplane's build is named ($BUILD)" \
  sh -c "case \"$BUILD\" in ''|'\"\"'|*No\ Such*) exit 1;; *) exit 0;; esac"
expect "and it is the release installed" test "$CURRENT" = "true"
expect "the TCP transport reads off, as configured" test "$TCPON" = "false"
# Within a percent of the VTY's figure: the heap moves between two reads.
expect "the sessions that fit agree with show cgnat memory ($FIT, ${DP_FIT:-unread})" \
  sh -c "[ -n \"$DP_FIT\" ] && [ \$(( ($FIT - $DP_FIT) * 100 / $DP_FIT )) -ge -1 ] && [ \$(( ($FIT - $DP_FIT) * 100 / $DP_FIT )) -le 1 ]"
expect "the worker table carries the three sessions" \
  sh -c "printf '%s\\n' \"$WSESS\" | awk '{s += \$1} END {exit !(s == 3)}'"
expect "the VRRP router is read on its interface, by name ($VRIF)" test "$VRIF" = "pg0"
expect "master, alone on the link ($VRST)" test "$VRST" = "master"
expect "advertising the priority configured ($VRPRI)" test "$VRPRI" = "150"
expect "the bundle is in the table ($BNAME), at zero members" \
  test "$BNAME/$BMEM" = "BondEthernet0/0"
expect "and a box with no identity table says zero entries" test "$IDENT" = "0"
expect "NATV2-MIB names the pool as instance 1 ($NVALIAS)" test "$NVALIAS" = "nb"
expect "with the dataplane's three ports and one subscriber" \
  test "$NVPORTS/$NVSUBS" = "3/1"
expect "the pool's ports from 1024 ($NVMIN)" test "$NVMIN" = "1024"
# An InetAddress is printed as its bytes: the type that would make net-snmp
# print it dotted is natv2PoolAddressType, a column of another table.
expect_in "and its outside range begins at 200.150.0.16 (C8 96 00 10)" \
  "C8 96 00 10" "$NVBEGIN"
expect_in "and ends at 200.150.0.19 (C8 96 00 13)" "C8 96 00 13" "$NVEND"
expect_not_in "every NATV2-MIB row resolves to a name in the RFC's module" \
  "iso.3.6.1.2.1.234" "$NVWALK"
expect "VRRPV3-MIB finds pg0's VRID 7 master ($VVST)" test "$VVST" = "master"
expect "with the priority configured ($VVPRI)" test "$VVPRI" = "150"
expect_in "and VRID 7's virtual MAC (0:0:5e:0:1:7)" "0:0:5e:0:1:7" "$VVMAC"
expect "and its one virtual address ($VVCNT)" test "$VVCNT" = "1"
expect_not_in "every VRRPV3-MIB row resolves to a name in the RFC's module" \
  "iso.3.6.1.2.1.207" "$VVWALK"
expect "IP-MIB says the box forwards ($IPFWD)" test "$IPFWD" = "forwarding"
expect "ipAddrTable puts pg0's address under pg0's ifIndex ($IPADIX)" \
  test -n "${PG0:-}" -a "$IPADIX" = "${PG0:-x}"
expect "with its /22 mask ($IPADMASK)" test "$IPADMASK" = "255.255.252.0"
expect "and so does ipAddressTable ($IPAIX)" test "$IPAIX" = "${PG0:-x}"
expect "and the prefix row it points at is the dataplane's, set by hand ($IPPFX)" \
  sh -c "case \"$IPPFX\" in manual|2) exit 0;; *) exit 1;; esac"
expect_not_in "no Linux address in ipAddrTable: the loopback is Linux's" \
  "127.0.0.1" "$IPWALK"
expect "show snmp zabbix-template fills in this box's number ($ZBXPEN)" \
  test "$ZBXPEN" = "99999"
expect "and the ports it holds" test "$PORTS" = "3"
# Refused at the daemon (noAccess, from the read-only community and view) or
# by the agent (notWritable). Either is a refusal; asserting one spelling
# would tie the test to which layer happened to say no first.
expect "BFD-STD-MIB: bfdd's one session, down(2), through snmpd as Debian-snmp" \
  sh -c "printf '%s\n' \"$BFDSTATE\" | grep -c '^\.1\.3\.6\.1\.2\.1\.222\.1\.2\.1\.11\.' | grep -qx 1 && printf '%s\n' \"$BFDSTATE\" | grep -q 'INTEGER: 2$'"
expect_in "  its neighbour, 192.0.2.77, as InetAddress octets" "C0 00 02 4D" "$BFDDST"
expect "  its transmit interval in microseconds, bfdd's 300 ms ($BFDTX)" \
  test "$BFDTX" = "300000"
expect "  and bfdNotificationsEnable false: the agent sends no BFD trap" \
  test "$BFDNOTIF" = "2"
expect "a SET is refused" sh -c "case \"$SETOUT\" in *noAccess*|*otWritable*) exit 0;; *) exit 1;; esac"
expect "and changed nothing" test "$AFTERSET" = "600"
expect_in "the v3 user is granted read-only access" "rouser nms priv" "$V3CONF"
expect_in "and created in net-snmp's own store" "createUser nms SHA" "$V3STORE"
# Both checks below run against a variable built with "|| true", and nothing
# asserted it was non-empty - so on a box printing no SNMP configuration at
# all, "the secret never reached the screen" was true because nothing reached
# the screen. An absent answer is not a passing answer, least of all here.
expect "there is SNMP configuration to redact in the first place" \
  sh -c "[ -n \"\$(printf '%s' \"$REDACTED\" | tr -d '[:space:]')\" ]"
expect_not_in "the community string never reaches the screen" "hexro" "$REDACTED"
expect_not_in "and neither does a v3 passphrase" "averylongpassphrase" "$REDACTED"

expect_not_in "with the dataplane stopped, no value is served" "1" "$GONE"
expect_in "the poll says so instead" "No Such" "$GONE"
verdict
