# OFF-RED-AT-LEAST: 4
# A card added to the running box forwards at once, through AF_XDP (#453).
#
#   [netns peer] 192.0.2.2 --- veth p20/ens20 --- [box: VPP running]
#
# The card appears AFTER the dataplane is up, the way a card added in the
# hypervisor does. appliance/prisma-hot-card is run on it the way udev runs it,
# and the operator configures it in the VTY like any other card: the lab
# measures that the dataplane holds it under the name the operator knows, that
# the VTY configures it, that the peer is answered THROUGH the dataplane, and
# that `show interface` says it is on AF_XDP and what moves it.
#
# A veth has no PCI parent, and the tuner rightly leaves such a card alone.
# So the script is given a root whose sys/class/net/ens20 points at a PCI
# device, and a tuner that says claim: the RULE is appliance/test_tune.py's
# --would-claim cases, and the KVM guest with a real virtio card added by
# device_add is on #453 (70 of 70 echo replies). What is left to prove here is
# the attach and the VTY around it.
#
# MODE=off: the script is not run, as on a box without it - the card stays
# with Linux, the VTY has no such interface, and the peer is not answered.
. /src/lab/lib.sh
. /src/lab/verdict.sh
set -u
MODE=${1:-on}
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja >/dev/null
}
cd /src
vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
wait_vpp /tmp/vpp.log || exit 1
use_binary_api
mkdir -p /var/run/frr /etc/frr
echo "hostname prisma-routing" > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr
start_frr
HX="python3 /src/src/prismash/prismash.py"

echo "=========== the dataplane is running; now a card appears ==========="
ip netns add peer
ip link add ens20 type veth peer name p20
ip link set p20 netns peer
ip netns exec peer sh -c 'ip link set lo up; ip link set p20 up; ip addr add 192.0.2.2/24 dev p20'
ip -br link show ens20 | sed 's/^/  /'

R=/tmp/hot-root
mkdir -p "$R/sys/class/net/ens20" "$R/sys/devices/pci0000:00/0000:00:14.0" \
         "$R/etc/vpp" "$R/etc/prisma-routing" "$R/run/prisma-routing"
ln -sfn "../../../devices/pci0000:00/0000:00:14.0" "$R/sys/class/net/ens20/device"
cat /sys/class/net/ens20/address > "$R/sys/class/net/ens20/address"
printf 'dpdk {\n}\n' > "$R/etc/vpp/startup.conf"
cat > /tmp/tune-claims <<'EOF'
#!/bin/sh
for a; do n=$a; done
echo "claim: $n"
EOF
chmod +x /tmp/tune-claims

if [ "$MODE" = off ]; then
  HOT="(not run: MODE=off)"
else
  HOT=$(PRISMA_ROOT=$R PRISMA_TUNE=/tmp/tune-claims sh /src/appliance/prisma-hot-card add ens20 2>&1)
fi
printf '%s\n' "$HOT" | sed 's/^/  /'
HW=$(vppctl show hardware ens20 2>&1)
printf '%s\n' "$HW" | sed -n '1,4p' | sed 's/^/  vpp: /'
KERNEL=$(ip -br link 2>&1)

echo; echo "=========== the operator configures it in the VTY, like any card ==========="
CONF=$($HX -c "configure" \
  -c "interface ens20" -c "linux-name ens20" -c "ipv4 address 192.0.2.1/24" \
  -c "no shutdown" -c "exit" -c "commit" 2>&1)
printf '%s\n' "$CONF" | tail -4 | sed 's/^/  /'
sleep 2
SHOWIF=$(PRISMA_HOT_CARDS=$R/run/prisma-routing/hot-cards $HX -c "show interface" 2>&1)
printf '%s\n' "$SHOWIF" | sed 's/^/  /'

echo; echo "=========== the peer is answered through the dataplane ==========="
PING=$(ip netns exec peer ping -c 5 -W 2 192.0.2.1 2>&1 | tail -2)
echo "  $PING"
COUNTERS=$(vppctl show interface ens20 2>&1)
printf '%s\n' "$COUNTERS" | sed 's/^/  vpp: /'
RXIP4=$(printf '%s\n' "$COUNTERS" | awk '$1 == "ip4" {print $2}')

echo; echo "=========== VERDICT ==========="
expect_in "the script attached the card, and said how it moves to DPDK" \
  "system dataplane restart" "$HOT"
expect_in "the dataplane holds it under the name the operator knows" \
  "netdev xdp-ens20" "$HW"
expect_in "  and the kernel's card went out of the way" "xdp-ens20" "$KERNEL"
expect_in "the VTY configures it like any other card" \
  "192.0.2.1/24" "$SHOWIF"
expect_in "  and show interface says it is on AF_XDP, and what moves it" \
  "forwarding through AF_XDP" "$SHOWIF"
expect_in "the peer is answered" " 0% packet loss" "$PING"
expect "  through the dataplane, not around it" test "${RXIP4:-0}" -ge 5
verdict
