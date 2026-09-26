#!/bin/sh
# OFF-RED-AT-LEAST: 8
# The host's doors (#448, phase B): the VTY's accounts, and the files it
# writes on the host (resolv.conf, authorized_keys, snmpd.conf), are made by
# the doors daemon, and the daemon does them for root only. So is the Linux
# end of a dataplane interface: its description and MTU are set through a
# door, because it lives in the host's network namespace.
#
# The daemon runs as root, as systemd runs it on a box. The VTY commits three
# accounts, a role, a password, and then removes one; each is asserted on the
# host (id, getent) AND in the daemon's own log, which is the only place
# that tells a command the daemon ran from one the VTY ran itself. Then an
# account with no privilege asks the same socket for the same thing and is
# refused.
#
# MODE=off runs no daemon: the accounts are still made, by the VTY itself -
# which is the fallback, and correct - and the checks that the daemon made
# them go red.
MODE=${1:-on}
set -u
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja >/dev/null
}
cd /src
. /src/lab/lib.sh
. /src/lab/verdict.sh
use_binary_api

HX="python3 /src/src/prismash/prismash.py"
export PRISMA_CONFIG=/etc/prisma-routing/prisma-routing.conf
vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
wait_vpp /tmp/vpp.log || exit 1

# What install.sh puts down for accounts, as lab/vty.sh does.
groupadd -f prisma-admin 2>/dev/null
groupadd -f prisma-cgnat 2>/dev/null
install -D -m 0755 /src/appliance/prismash-login /usr/bin/prismash-login

# resolv.conf is docker's bind mount; a rename onto it fails. lab/dns.sh does
# the same, for the same reason.
umount /etc/resolv.conf 2>/dev/null
printf 'nameserver 192.0.2.53\n' > /etc/resolv.conf

# The daemon, where the unit puts its socket.
if [ "$MODE" = on ]; then
  python3 /src/src/prismash/doors.py --serve > /tmp/doors.log 2>&1 &
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -S /run/prisma-routing/doors.sock ] && break; sleep 0.5
  done
else
  : > /tmp/doors.log
fi

echo "  -- the VTY makes three accounts, one with a password"
# A crypt(3) hash, as `password` takes it: not a password this box accepts.
HASH='$6$lab$aVkxAXWc0YyZCbgZ6sGzgDfmvVHU/0Y3n/OI1BRi8Qq2ubfYhxtVbRFGGr3mJPXrXXxc2Zp1n/tFjNxjBQuRY.'
MAKE=$($HX -c "configure" \
    -c "user ro" -c "exit" \
    -c "user netadm" -c "role administrator" -c "password $HASH" -c "exit" \
    -c "user gone" -c "exit" \
    -c "commit" 2>&1)
printf '%s\n' "$MAKE" | sed 's/^/    /'
DEL=$($HX -c "configure" -c "no user gone" -c "commit" 2>&1)
printf '%s\n' "$DEL" | sed 's/^/    /'

echo "  -- the files: a resolver, a key, the SNMP agent"
FILES=$($HX -c "configure" -c "dns server 198.51.100.53" \
    -c "user ro" -c "ssh-key ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDoorlab ro@lab" -c "exit" \
    -c "snmp enabled yes" -c "snmp listen-address 127.0.0.1" \
    -c "snmp community doorlabro" -c "commit" 2>&1)
printf '%s\n' "$FILES" | sed 's/^/    /'
RESOLV=$(cat /etc/resolv.conf)
KEYS=$(cat /home/ro/.ssh/authorized_keys 2>&1)
KEYMODE=$(stat -c %a /home/ro/.ssh/authorized_keys 2>&1)
SNMPMODE=$(stat -c %a /etc/snmp/snmpd.conf 2>&1)
echo "    resolv: $(echo $RESOLV) | keys $KEYMODE: $KEYS | snmpd.conf $SNMPMODE"
printf '%s\n' "$DEL" | sed 's/^/    /'
IDS="ro=$(id -nG ro 2>&1) | netadm=$(id -nG netadm 2>&1) | gone=$(id -u gone 2>&1)"
echo "    $IDS"
SHADOW=$(getent shadow netadm | cut -d: -f2)
echo "  -- the daemon's log"
sed 's/^/    /' /tmp/doors.log

echo "  -- a dataplane interface's Linux end: description and MTU"
ip link add dv0 type veth peer name dv0p 2>/dev/null
ip link set dv0 up; ip link set dv0p up
vppctl create host-interface name dv0 >/dev/null
LINK=$($HX -c "configure" -c "interface host-dv0" -c "description door-uplink" \
    -c "mtu 1400" -c "exit" -c "commit" 2>&1)
printf '%s\n' "$LINK" | sed 's/^/    /'
LINUX_END=$(ip -o link show dev host-dv0 2>&1)
echo "    $LINUX_END"

echo "  -- an account with no privilege asks the daemon itself"
useradd -M -s /bin/sh doorlab 2>/dev/null
ASKED=$(su doorlab -s /bin/sh -c "cd /src/src/prismash && python3 -c '
import doors
print(doors.runner()([\"useradd\", \"-m\", \"-s\", \"/usr/bin/prismash-login\", \"sneak\"]))
'" 2>&1)
echo "    $ASKED"
SNEAK=$(id -u sneak 2>&1)

expect_said "the accounts exist on the host" \
  "ro=ro" "$IDS"
expect_said "the administrator is in its role group" \
  "prisma-admin" "$IDS"
case "$SHADOW" in
  '$6$lab$'*) echo "  [ ok ] the password reached the host's shadow" ;;
  *) echo "  [FAIL] the password did not reach the host's shadow: $SHADOW"
     VERDICT_FAILURES=$((VERDICT_FAILURES + 1)) ;;
esac
expect_said "the removed account is gone" "no such user" "$IDS"
expect_said "the daemon created them" "door: uid 0: useradd -m -s /usr/bin/prismash-login netadm -> 0" "$(cat /tmp/doors.log)"
expect_said "the daemon set the password, and its log does not carry it" \
  "door: uid 0: chpasswd -e -> 0" "$(cat /tmp/doors.log)"
expect_said "the daemon removed one" "door: uid 0: userdel -r gone -> 0" "$(cat /tmp/doors.log)"
case "$(cat /tmp/doors.log)" in
  *'$6$lab$'*) echo "  [FAIL] the daemon's log carries the password hash"
               VERDICT_FAILURES=$((VERDICT_FAILURES + 1)) ;;
  *) echo "  [ ok ] the daemon's log carries no password hash" ;;
esac
expect_said "the resolver reached resolv.conf" "nameserver 198.51.100.53" "$RESOLV"
expect_said "the daemon wrote resolv.conf" "door: uid 0: write /etc/resolv.conf -> 0" "$(cat /tmp/doors.log)"
expect_said "the key reached the account, 0600" "600: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDoorlab" "$KEYMODE: $KEYS"
expect_said "the daemon wrote the key" "door: uid 0: keys /home/ro -> 0" "$(cat /tmp/doors.log)"
expect_said "the daemon wrote snmpd.conf" "door: uid 0: write /etc/snmp/snmpd.conf -> 0" "$(cat /tmp/doors.log)"
case "$SNMPMODE" in
  600) echo "  [ ok ] snmpd.conf is 0600: the community is in it" ;;
  *) echo "  [FAIL] snmpd.conf is $SNMPMODE"
     VERDICT_FAILURES=$((VERDICT_FAILURES + 1)) ;;
esac
case "$(cat /tmp/doors.log)" in
  *doorlabro*) echo "  [FAIL] the daemon's log carries the community"
               VERDICT_FAILURES=$((VERDICT_FAILURES + 1)) ;;
  *) echo "  [ ok ] the daemon's log carries no community" ;;
esac
expect_said "the description reached the Linux end" "alias door-uplink" "$LINUX_END"
expect_said "the daemon set it" "door: uid 0: ip link set dev host-dv0 alias door-uplink -> 0" "$(cat /tmp/doors.log)"
expect_said "the MTU reached the Linux end" "mtu 1400" "$LINUX_END"
expect_said "the daemon set that too" "door: uid 0: ip link set dev host-dv0 mtu 1400 -> 0" "$(cat /tmp/doors.log)"
case "$SNEAK" in
  *"no such user"*) echo "  [ ok ] an account with no privilege made nothing" ;;
  *) echo "  [FAIL] an account with no privilege made 'sneak': $SNEAK"
     VERDICT_FAILURES=$((VERDICT_FAILURES + 1)) ;;
esac
verdict
