#!/bin/sh
# DNS resolvers from the VTY, against the real dhclient-script (#437).
#
#   sh tools/lab.sh dns        with the DHCP hook installed
#   sh tools/lab.sh dns off    without it - the renewal checks MUST fail
#
# The half a unit box cannot see is DHCP. On a box whose management address
# came from DHCP, dhclient-script rewrites /etc/resolv.conf on every lease and
# every renewal, so a `dns server` that only wrote the file would be undone at
# the next renewal, in silence. This drives isc-dhcp-client's own script
# through a renewal and reads the file after it.
#
# OFF-RED-AT-LEAST: 3
#
# Measured on 2026-09-24. The off run leaves the hook out, and the renewal
# then rewrites the file the VTY owns - which also takes the ownership line
# with it, so removing the last server finds nothing to give back. What this
# measures is that the HOOK is what stands between the two, and that the
# renewal the on run survives is one that really runs.
set -u
. /src/lab/lib.sh   # startup_conf, wait_vpp
. /src/lab/verdict.sh
MODE=${1:-on}
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
HX="python3 /src/src/prismash/prismash.py"
export PRISMA_CONFIG=/etc/prisma-routing/prisma-routing.conf

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp

# Docker bind-mounts its own /etc/resolv.conf over the container's, and a
# rename onto a mount point is EBUSY. A box has a plain file there, so the
# lab makes one: the file a DHCP lease wrote, which is what the VTY finds.
umount /etc/resolv.conf 2>/dev/null
printf '# written by dhclient\nnameserver 192.0.2.53\n' > /etc/resolv.conf
DHCP_BEFORE=$(cat /etc/resolv.conf)

# The hook, where appliance/install.sh puts it - asserted below to be the
# line install.sh has, so this is not a second idea of where it goes.
HOOK=/etc/dhcp/dhclient-enter-hooks.d/prisma-routing-dns
if [ "$MODE" = off ]; then
  echo "  MODE=off: no DHCP hook. The renewal checks MUST fail."
  rm -f "$HOOK"
else
  install -D -m 0644 /src/appliance/dhclient-prisma-routing-dns "$HOOK"
fi

# A renewal, through dhclient-script itself, on an interface of its own.
# dhclient-script configures the address it is handed and then calls
# make_resolv_conf() - the function the hook stands down.
ip link add dnslab0 type dummy 2>/dev/null
ip link set dnslab0 up
renew() {
  reason=RENEW interface=dnslab0 new_ip_address=198.51.100.10 \
    new_subnet_mask=255.255.255.0 new_network_number=198.51.100.0 \
    new_broadcast_address=198.51.100.255 \
    new_domain_name_servers="$1" \
    old_ip_address=198.51.100.10 /sbin/dhclient-script >/dev/null 2>&1
}

echo "=========== 1. the VTY takes the resolvers over ==========="
SET=$($HX -c "configure" -c "dns server 1.1.1.1" -c "dns server 9.9.9.9" \
          -c "commit" 2>&1)
printf '%s\n' "$SET" | tail -2 | sed 's/^/  /'
OWNED=$(cat /etc/resolv.conf)
printf '%s\n' "$OWNED" | sed 's/^/  | /'
RUN1=$($HX -c "show running-config" 2>&1 | grep "^dns ")

echo "=========== 2. a DHCP renewal arrives ==========="
renew 203.0.113.53
AFTER_RENEW=$(cat /etc/resolv.conf)
printf '%s\n' "$AFTER_RENEW" | sed 's/^/  | /'

echo "=========== 3. the last server removed ==========="
BACK=$($HX -c "configure" -c "no dns server 1.1.1.1" \
           -c "no dns server 9.9.9.9" -c "commit" 2>&1)
printf '%s\n' "$BACK" | tail -2 | sed 's/^/  /'
GIVEN_BACK=$(cat /etc/resolv.conf)
printf '%s\n' "$GIVEN_BACK" | sed 's/^/  | /'
RUN3=$($HX -c "show running-config" 2>&1 | grep -c "^dns " || true)

echo "=========== 4. and DHCP writes the file again ==========="
renew 203.0.113.53
DHCP_AGAIN=$(cat /etc/resolv.conf)
printf '%s\n' "$DHCP_AGAIN" | sed 's/^/  | /'

echo "=========== VERDICT ==========="
expect_in "the commit takes resolv.conf over, and says so on its first line" \
  "# Written by Prisma Routing" "$OWNED"
# The nameserver lines, exactly and in order: glibc asks them in that order,
# so the order an operator typed is the order the box asks in.
OWNED_NS=$(printf '%s\n' "$OWNED" | grep "^nameserver" | tr '\n' ' ')
expect "with the servers in the order typed" \
  test "$OWNED_NS" = "nameserver 1.1.1.1 nameserver 9.9.9.9 "
expect_in "and the box reports them, read back out of the file" \
  "dns server 1.1.1.1" "$RUN1"
# The two the off run turns red.
expect_in "a DHCP renewal does not put its resolver back" \
  "nameserver 1.1.1.1" "$AFTER_RENEW"
expect_not_in "and the lease's resolver is nowhere in the file" \
  "203.0.113.53" "$AFTER_RENEW"
expect "removing the last server gives the file DHCP wrote back, whole" \
  test "$GIVEN_BACK" = "$DHCP_BEFORE"
expect "and the box reports no dns server" test "${RUN3:-1}" = 0
# The control on the hook: standing down only while the VTY owns the file.
# Without it, a box that stops using `dns server` never gets a resolver from
# DHCP again - which is the failure a hook that always stood down would cause.
expect_in "once the VTY has given the file back, DHCP writes it again" \
  "nameserver 203.0.113.53" "$DHCP_AGAIN"
expect "and the hook is where appliance/install.sh puts it" \
  grep -q "etc/dhcp/dhclient-enter-hooks.d/prisma-routing-dns" \
  /src/appliance/install.sh

verdict
