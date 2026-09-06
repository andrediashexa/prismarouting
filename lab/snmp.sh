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
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /etc/hexarouting /etc/snmp /var/lib/snmp
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
HX="python3 /src/src/hexash/hexash.py"
export HEXA_CONFIG=/etc/hexarouting/hexarouting.conf
install -D -m 0755 /src/appliance/hexarouting-snmp-agent /usr/lib/hexarouting/snmp-agent

vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
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
# snmpd is not under systemd in this container; start it the way the unit does.
pkill snmpd 2>/dev/null; sleep 0.5
snmpd -Lo -f -C -c /etc/snmp/snmpd.conf > /tmp/snmpd.log 2>&1 &
sleep 2

echo "  --- what the agent itself serves, run by hand ---"
printf 'getnext\n1.3.6.1.4.1\n' | /usr/lib/hexarouting/snmp-agent | sed 's/^/    /'
printf 'get\n1.3.6.1.4.1.99999.1.1.0\n' | /usr/lib/hexarouting/snmp-agent | sed 's/^/    /'

echo
echo "=========== 2. poll it ==========="
UDPT=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.123.1.1.2.0 2>&1)
ICMPT=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.123.1.1.3.0 2>&1)
SUBS=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.4.1.99999.1.1.0 2>&1)
PORTS=$(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.4.1.99999.1.2.0 2>&1)
echo "  natUdpDefIdleTimeout  = $UDPT   (vppctl says $(vppctl show bpa timeouts | tr ' ' '\n' | grep -A0 -x -m1 600 || echo 600))"
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
expect "the UDP timeout came back over SNMP" test "$UDPT" = "600"
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
expect_not_in "the community string never reaches the screen" "hexro" "$REDACTED"
expect_not_in "and neither does a v3 passphrase" "averylongpassphrase" "$REDACTED"

expect_not_in "with the dataplane stopped, no value is served" "1" "$GONE"
expect_in "the poll says so instead" "No Such" "$GONE"
verdict
