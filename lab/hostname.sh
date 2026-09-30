#!/bin/sh
# The box's name from the VTY (#516): one `hostname` statement, read back from
# every place that says a name - the kernel, /etc/hostname, the 127.0.1.1
# line of /etc/hosts, SNMP's sysName and the running configuration - kept
# across a routing commit, and given back to the installer's default by
# `no hostname`.
#
# FRR IS PRINTED AND NOT ASSERTED ON. Measured here on FRR 10.7.1:
# `hostname` through vtysh, to every daemon, to one with -d, or as
# frr-reload sends it, exits 0 and changes nothing any daemon's `show
# running-config` says; and vtysh adds a line of its own, the kernel's name
# as it read it when it started. What is asserted is the part the VTY owns:
# FRR's lines never reach the routing half, and a routing commit leaves them
# as they were instead of sending `no hostname` for each.
#
#   sh tools/lab.sh hostname        with the host program installed
#   sh tools/lab.sh hostname off    without it - the rename checks MUST fail
#
# OFF-RED-AT-LEAST: 6
#
# The off run leaves appliance/set-hostname out, which is what a box upgraded
# without it would be: the commit is refused, and every place still says the
# old name. What it measures is that the host program is what renames the
# box, and that a refused rename leaves FRR where the host is.
#
# In this container /etc/hostname and /etc/hosts are docker's bind mounts,
# which a rename cannot replace; the program writes them in place there, so
# this also runs the fallback its unit test can only stand in for.
set -u
. /src/lab/lib.sh   # startup_conf, wait_vpp, start_frr, use_binary_api
. /src/lab/verdict.sh
MODE=${1:-on}
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /etc/prisma-routing /etc/snmp \
         /var/lib/snmp /var/run/frr /etc/frr
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
HX="python3 /src/src/prismash/prismash.py"
export PRISMA_CONFIG=/etc/prisma-routing/prisma-routing.conf
install -D -m 0755 /src/appliance/prisma-routing-snmp-agent /usr/lib/prisma-routing/snmp-agent
if [ "$MODE" = off ]; then
  echo "  MODE=off: no set-hostname. The rename checks MUST fail."
  rm -f /usr/lib/prisma-routing/set-hostname
else
  install -D -m 0755 /src/appliance/set-hostname /usr/lib/prisma-routing/set-hostname
fi

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp
use_binary_api
# The name the installer gives a box that was not asked for one, everywhere.
hostname prisma-routing
echo prisma-routing > /etc/hostname
printf '127.0.0.1 localhost\n127.0.1.1 prisma-routing\n' > /etc/hosts
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr
start_frr bgpd
sleep 2

# snmpd is not under systemd here; started the way lab/snmp.sh starts it,
# again after each commit, since the file it reads is what changed.
snmpd_again() {
  pkill snmpd 2>/dev/null; sleep 0.5
  snmpd -Lo -f -C -c /etc/snmp/snmpd.conf > /tmp/snmpd.log 2>&1 &
  sleep 2
}
names() {  # names <label> - every place a name is said, one per line
  echo "kernel $(hostname)"
  echo "etc-hostname $(cat /etc/hostname)"
  echo "etc-hosts $(awk '$1 == "127.0.1.1" { print $2 }' /etc/hosts | tr '\n' ' ')"
  echo "frr $(vtysh -c 'show running-config' 2>/dev/null | awk '/^hostname / { print $2 }' | tr '\n' ' ')"
  echo "sysname $(snmpget -v2c -c hexro -Ovq 127.0.0.1 1.3.6.1.2.1.1.5.0 2>&1)"
}

echo "=========== 1. hostname edge-lab1, with SNMP on ==========="
SET=$($HX -c configure -c "hostname edge-lab1" -c "snmp enabled yes" \
          -c "snmp listen-address 127.0.0.1" -c "snmp community hexro" \
          -c commit 2>&1)
printf '%s\n' "$SET" | tail -4 | sed 's/^/  /'
snmpd_again
N1=$(names)
printf '%s\n' "$N1" | sed 's/^/  | /'
RUN1=$($HX -c "show running-config" 2>&1)
printf '%s\n' "$RUN1" | grep -n "hostname" | sed 's/^/  running: /'

echo "=========== 2. a routing commit afterwards ==========="
FRR1=$(vtysh -c 'show running-config' 2>/dev/null | grep "^hostname")
BGP=$($HX -c configure -c "router bgp 65001" -c exit -c commit 2>&1)
printf '%s\n' "$BGP" | tail -2 | sed 's/^/  /'
FRR2=$(vtysh -c 'show running-config' 2>/dev/null | grep "^hostname")
echo "  FRR before: $(echo $FRR1)"
echo "  FRR after:  $(echo $FRR2)"
BGPRUN=$(vtysh -c 'show running-config' 2>/dev/null | grep -c "^router bgp 65001")

echo "=========== 3. a name the VTY refuses ==========="
BAD=$($HX -c configure -c "hostname 9box" 2>&1)
printf '%s\n' "$BAD" | sed 's/^/  /'

echo "=========== 4. no hostname ==========="
BACK=$($HX -c configure -c "no hostname" -c commit 2>&1)
printf '%s\n' "$BACK" | tail -2 | sed 's/^/  /'
snmpd_again
N4=$(names)
printf '%s\n' "$N4" | sed 's/^/  | /'

echo "=========== VERDICT ==========="
# Five of the six the off run turns red, the sixth being sysName after
# `no hostname`: the host is never renamed, and the commit is refused whole.
expect_in "the kernel's name is the one committed" "kernel edge-lab1" "$N1"
expect_in "so is /etc/hostname, which the next boot reads" \
  "etc-hostname edge-lab1" "$N1"
expect "and the one 127.0.1.1 line of /etc/hosts" \
  test "$(printf '%s\n' "$N1" | grep '^etc-hosts')" = "etc-hosts edge-lab1 "
expect_in "SNMP answers it as sysName" 'sysname "edge-lab1"' "$N1"
expect_in "and the running configuration states it" "hostname edge-lab1" "$RUN1"
# Once, and not as routing: FRR's line is the box's statement now.
expect "said once in the running configuration, not again under routing" \
  test "$(printf '%s\n' "$RUN1" | grep -c '^hostname ')" = 1
# The reload carries FRR's lines: without it frr-reload sends `no hostname`
# for each, on every commit.
expect "a routing commit reaches FRR" test "$BGPRUN" = 1
expect "and leaves FRR's hostname lines as they were" \
  test -n "$FRR1" -a "$FRR1" = "$FRR2"
expect_in "a name FRR would refuse is refused at the prompt" \
  "starts with a letter" "$BAD"
expect_in "no hostname gives the kernel the installer's default back" \
  "kernel prisma-routing" "$N4"
expect_in "and sysName" 'sysname "prisma-routing"' "$N4"
expect "and the program is where appliance/install.sh puts it" \
  grep -q "usr/lib/prisma-routing/set-hostname" /src/appliance/install.sh

verdict
