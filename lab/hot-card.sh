# OFF-RED-AT-LEAST: 8
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
VPPPID=$!
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

echo; echo "=========== system dataplane restart, the record lost (#576) ==========="
# 10.40.200.166, 0.151.1: the hot card's linux_cp end held its name past the
# stop, the record was gone, the tuner wrote `name xdp-enp1s0`, and the box
# went dark. Here the stop is VPP itself going away, and the restart runs on
# the real netdevs: R2's sys/class/net links each to the kernel's own (so a
# TAP shows tun_flags and a deleted one dangles), and xdp-ens20 is given the
# PCI parent a veth lacks. systemctl, the give-back and udev are stand-ins:
# the units are not in this container, and they are not what failed.
TAPWAS=$( [ -e /sys/class/net/ens20/tun_flags ] && echo tap || echo none )
kill "$VPPPID" 2>/dev/null; wait "$VPPPID" 2>/dev/null
STALE=$( [ -e /sys/class/net/ens20/tun_flags ] && echo "the dataplane's end outlived it" || echo "the dataplane's end went with it" )
echo "  ens20 before the stop: $TAPWAS; after: $STALE"
R2=/tmp/hot-root2
rm -rf "$R2"; mkdir -p "$R2/sys/class/net" "$R2/sys/devices/pci0000:00/0000:00:14.0" \
  "$R2/etc/vpp" "$R2/run/prisma-routing"
for d in /sys/class/net/*; do
  [ "${d##*/}" = xdp-ens20 ] && continue
  ln -s "$d" "$R2/sys/class/net/${d##*/}"
done
mkdir -p "$R2/sys/class/net/xdp-ens20"
ln -sfn "../../../devices/pci0000:00/0000:00:14.0" "$R2/sys/class/net/xdp-ens20/device"
printf 'dpdk {\n}\n' > "$R2/etc/vpp/startup.conf"
cat > /tmp/sysctl-stub <<EOF2
#!/bin/sh
case "\$*" in
  "is-active --quiet prisma-routing-containers.service") exit 0 ;;
  "is-active --quiet vpp.service") exit 1 ;;
  "restart prisma-routing-tune.service") cp $R2/run/prisma-routing/restart-cards /tmp/seen-keep 2>/dev/null ;;
esac
exit 0
EOF2
chmod +x /tmp/sysctl-stub
if [ "$MODE" = off ]; then
  RESTART="(not run: MODE=off)"
else
  RESTART=$(PRISMA_ROOT=$R2 PRISMA_SYSTEMCTL=/tmp/sysctl-stub PRISMA_GIVE_BACK=true \
            PRISMA_UDEVADM=true PRISMA_SLEEP=: PRISMA_LOAD_WAIT=0 \
            sh /src/appliance/prisma-hot-card restart-dataplane 2>&1)
fi
printf '%s\n' "$RESTART" | sed 's/^/  /'
AFTER=$(ip -d link show ens20 2>&1)
SEEN=$(cat /tmp/seen-keep 2>/dev/null)

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
expect_in "after a restart with no record, the card is ens20 again (#576)" \
  "veth" "$AFTER"
expect "  and no netdev is left called xdp-ens20" sh -c '! ip link show xdp-ens20 >/dev/null 2>&1'
expect_in "  and the tuner is told the dataplane held it" "0000:00:14.0" "$SEEN"
verdict
