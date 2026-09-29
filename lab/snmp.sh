#!/bin/sh
# The SNMP agent, polled the way an NMS polls it.
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

echo
echo "=========== 2. poll it ==========="
# WHAT AN NMS SEES FIRST (#497): the box's identity, CPU, memory and disks,
# which net-snmp serves and the view used to keep out.
DESCR=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.1.1.0 2>&1)
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
expect "and it reads up ($PG0OPER)" sh -c "case \"$PG0OPER\" in up*|1*) exit 0;; *) exit 1;; esac"
expect_in "sysDescr names the product" "Prisma Routing" "$DESCR"
expect "sysUpTime answers" sh -c "case \"$UPTIME\" in *No\ Such*|*Timeout*|'') exit 1;; *) exit 0;; esac"
expect "HOST-RESOURCES lists the processors ($CPUS)" test "${CPUS:-0}" -ge 1
expect "UCD reports the memory" sh -c "case \"$MEMTOTAL\" in [0-9]*) exit 0;; *) exit 1;; esac"
expect_in "and the retention log's filesystem has a disk threshold" "/var/log/prisma-routing" "$DISKS"
expect "the UDP timeout came back over SNMP" test "$UDPT" = "600"
expect "and it is the value the dataplane actually holds, not a constant" \
  test "$UDPT" = "${DP_UDPT:-unread}"
expect "and the ICMP one" test "$ICMPT" = "90"
expect "the enterprise subtree reports the subscriber" test "$SUBS" = "1"
expect "and the ports it holds" test "$PORTS" = "3"
# Refused at the daemon (noAccess, from the read-only community and view) or
# by the agent (notWritable). Either is a refusal; asserting one spelling
# would tie the test to which layer happened to say no first.
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
