#!/bin/sh
# OFF-RED-AT-LEAST: 3
# The host's doors (#448, phase B): the VTY's accounts are made by the doors
# daemon, and the daemon runs them for root only.
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
IDS="ro=$(id -nG ro 2>&1) | netadm=$(id -nG netadm 2>&1) | gone=$(id -u gone 2>&1)"
echo "    $IDS"
SHADOW=$(getent shadow netadm | cut -d: -f2)
echo "  -- the daemon's log"
sed 's/^/    /' /tmp/doors.log

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
case "$SNEAK" in
  *"no such user"*) echo "  [ ok ] an account with no privilege made nothing" ;;
  *) echo "  [FAIL] an account with no privilege made 'sneak': $SNEAK"
     VERDICT_FAILURES=$((VERDICT_FAILURES + 1)) ;;
esac
verdict
