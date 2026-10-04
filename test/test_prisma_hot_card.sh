#!/bin/sh
# appliance/prisma-hot-card against a stand-in sysfs, `ip`, `vppctl`, tuner
# and systemctl (#453): which cards it takes, which it leaves and why, what it
# does to the one it takes, and how it hands them back. The attach itself -
# AF_XDP on a virtio card added to a running KVM guest - is measured in
# lab/hot-card.sh and on #453; this is the script's decisions, with no root.
#
# Mutations shown red, one at a time: the altname loop removed; the MAC line
# removed; the startup.conf check removed; the rollback on a refused attach
# removed; the rename back in restart-dataplane removed.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0
ok()  { echo "  [ ok ] $1"; }
bad() { echo "  FAIL: $1"; fail=$((fail + 1)); }

R=$T/root; S=$T/state
fresh() {  # a running box: VPP answering, eth9 added, no address on it
  rm -rf "$R" "$S"; mkdir -p "$R/sys/class/net" "$R/etc/vpp" "$R/etc/prisma-routing" "$S"
  mkdir -p "$R/sys/devices/pci0000:00/0000:00:14.0" "$R/sys/class/net/eth9"
  ln -s "../../../devices/pci0000:00/0000:00:14.0" "$R/sys/class/net/eth9/device"
  echo 52:54:00:12:34:58 > "$R/sys/class/net/eth9/address"
  printf 'dpdk {\n  dev 0000:00:13.0 {\n  }\n}\n' > "$R/etc/vpp/startup.conf"
  : > "$S/calls"; : > "$S/vpp"; : > "$S/addr"; : > "$S/route"
  echo "enp0s20 ens20x" > "$S/alts"
  echo claim > "$S/verdict"; echo up > "$S/vppstate"; : > "$S/refuse"
  echo eth9 > "$S/names"
}
cat > "$T/ip" <<EOF
#!/bin/sh
echo "ip \$*" >> "$S/calls"
case "\$*" in
  "-4 -o addr show dev "*) cat "$S/addr" ;;
  "route show default") cat "$S/route" ;;
  "-d link show dev "*) for a in \$(cat "$S/alts"); do echo "    altname \$a"; done ;;
  "link property del dev "*) sed -i "s/\$7//" "$S/alts" ;;
  "link show dev "*) grep -qx "\$4" "$S/names"; exit \$? ;;
  "link set dev "*" name "*) if ! grep -qx "\$4" "$S/names" && [ -e "$R/sys/class/net/\$4" ]; then
                             mv "$R/sys/class/net/\$4" "$R/sys/class/net/\$6"; exit 0; fi
                           grep -qx "\$4" "$S/names" || exit 1
                           sed -i "s/^\$4\$/\$6/" "$S/names" ;;
  "link del dev "*) rm -rf "$R/sys/class/net/\$4" ;;
esac
exit 0
EOF
cat > "$T/vppctl" <<EOF
#!/bin/sh
echo "vppctl \$*" >> "$S/calls"
[ "\$(cat "$S/vppstate")" = up ] || exit 1
case "\$*" in
  "show version") echo "vpp v26.06" ;;
  "show hardware "*) grep -q "^\$3\$" "$S/vpp" && echo "\$3   2   up   \$3" ;;
  "create interface af_xdp "*) [ -s "$S/refuse" ] && { cat "$S/refuse"; exit 1; }
                               echo "\$7" >> "$S/vpp"; echo "\$7" ;;
esac
exit 0
EOF
cat > "$T/tune" <<EOF
#!/bin/sh
echo "tune \$*" >> "$S/calls"
for a; do n=\$a; done
v=\$(cat "$S/verdict")
[ "\$v" = claim ] && { echo "claim: \$n"; exit 0; }
echo "leave: \$n: \$v"; exit 1
EOF
cat > "$T/systemctl" <<EOF
#!/bin/sh
echo "systemctl \$*" >> "$S/calls"
[ "\$*" = "stop prisma-routing-containers.service" ] && [ -e "$S/nostop" ] && exit 1
# #564: is-active answers from the stand-in's state; the API's socket exists
# only when the test says so.
[ "\$*" = "is-active --quiet prisma-routing-containers.service" ] && { [ -e "$S/stillactive" ]; exit \$?; }
# A box put back on the units: vpp.service is the dataplane, the containers
# unit inactive.
[ "\$*" = "is-active --quiet vpp.service" ] && { [ -e "$S/units" ]; exit \$?; }
[ "\$*" = "cat prisma-routing-api-proxy.socket" ] && { [ -e "$S/apisocket" ]; exit \$?; }
# #564: the tuner, standing in - startup.conf from the cards as Linux names
# them now, which is what the real one writes; and a configuration that names
# an interface the dataplane does not have is refused, as --load refuses it.
if [ "\$*" = "restart prisma-routing-tune.service" ] && [ -e "$S/tunewrites" ]; then
  { echo "dpdk {"
    for d in "$R"/sys/class/net/*; do
      [ -e "\$d/device" ] || continue
      echo "  dev \$(basename "\$(readlink "\$d/device")") {"
      echo "    name \${d##*/}"
      echo "  }"
    done
    echo "}"; } > "$R/etc/vpp/startup.conf"
fi
if [ "\$*" = "start prisma-routing.service" ]; then
  if [ -e "$S/loadneeds" ]; then
    grep -q "name \$(cat "$S/loadneeds")\$" "$R/etc/vpp/startup.conf" || exit 1
  fi
  # #564: a load that fails its first tries (FRR still starting), then loads.
  if [ -s "$S/loadfails" ]; then
    n=\$(cat "$S/loadfails")
    [ "\$n" -gt 0 ] && { echo \$((n - 1)) > "$S/loadfails"; exit 1; }
  fi
  : > "$S/loaded"; exit 0
fi
[ "\$*" = "is-active --quiet prisma-routing.service" ] && { [ -e "$S/loaded" ]; exit \$?; }
exit 0
EOF
cat > "$T/giveback" <<EOF
#!/bin/sh
echo "giveback \$*" >> "$S/calls"
EOF
cat > "$T/udevadm" <<EOF
#!/bin/sh
echo "udevadm \$*" >> "$S/calls"
EOF
chmod +x "$T/ip" "$T/vppctl" "$T/tune" "$T/systemctl" "$T/udevadm" "$T/giveback"
HC() {
  PRISMA_SLEEP=: PRISMA_LOAD_WAIT=30 PRISMA_LOAD_RETRY=10 \
  PRISMA_ROOT=$R PRISMA_IP=$T/ip PRISMA_VPPCTL=$T/vppctl PRISMA_TUNE=$T/tune \
  PRISMA_SYSTEMCTL=$T/systemctl PRISMA_UDEVADM=$T/udevadm PRISMA_GIVE_BACK=$T/giveback \
  sh "$REPO/appliance/prisma-hot-card" "$@"
}
said() { printf '%s\n' "$OUT" | grep -q "$1"; }
called() { grep -q "$1" "$S/calls"; }

echo "=== a card the rule claims is attached through AF_XDP, under its own name ==="
fresh; OUT=$(HC add eth9); RC=$?
printf '%s\n' "$OUT" | sed 's/^/    /'
[ $RC = 0 ] && called "^ip link set dev eth9 name xdp-eth9$" \
  && ok "the kernel's card is renamed out of the way" \
  || bad "no rename (rc=$RC): $(cat "$S/calls")"
called "^vppctl create interface af_xdp host-if xdp-eth9 name eth9 num-rx-queues all$" \
  && ok "and the dataplane's interface carries the name the operator knows" \
  || bad "the attach was not asked for as measured: $(grep af_xdp "$S/calls")"
grep -n "" "$S/calls" | grep -A1 "udevadm settle" | grep -q "ip -d link show dev xdp-eth9" \
  && ok "the altnames are read only after udev has handled the rename" \
  || bad "altnames dropped before udev's move put them back: $(cat "$S/calls")"
called "^ip link property del dev xdp-eth9 altname enp0s20$" \
  && called "^ip link property del dev xdp-eth9 altname ens20x$" \
  && ok "every altname udev gave it is dropped" \
  || bad "the altnames stayed, and the name stays taken: $(cat "$S/calls")"
called "^vppctl set interface mac address eth9 52:54:00:12:34:58$" \
  && ok "and the dataplane gets the card's own MAC" \
  || bad "the dataplane keeps an invented MAC the card filters: $(cat "$S/calls")"
[ "$(cat "$R/run/prisma-routing/hot-cards")" = "eth9 0000:00:14.0 xdp-eth9" ] \
  && ok "the card is recorded, with the name to give back" \
  || bad "recorded as '$(cat "$R/run/prisma-routing/hot-cards" 2>/dev/null)'"
said "system dataplane restart" \
  && ok "and it says how it moves to DPDK" || bad "no way forward said: $OUT"
[ "$(HC list)" = "eth9 0000:00:14.0 xdp-eth9" ] \
  && ok "list shows it" || bad "list said '$(HC list)'"

echo "=== and what it leaves, each said ==="
fresh; echo "reserved for Linux: the management network" > "$S/verdict"
OUT=$(HC add eth9); RC=$?
[ $RC = 0 ] && said "leave: eth9: reserved for Linux" && ! called "name xdp-" \
  && ok "the tuner's reserve holds at a click as it does at boot" \
  || bad "a card the rule leaves was touched (rc=$RC): $OUT"
fresh; echo "10.0.0.5/24" > "$S/addr"
OUT=$(HC add eth9)
said "Linux has an address" && ! called "name xdp-" \
  && ok "a card carrying an address is Linux's" || bad "an addressed card: $OUT"
fresh; echo "default via 10.0.0.1 dev eth9 proto dhcp" > "$S/route"
OUT=$(HC add eth9)
said "Linux has an address or the default route" && ! called "name xdp-" \
  && ok "and so is the one the default route leaves on" || bad "the default route's card: $OUT"
fresh; printf 'dpdk {\n  dev 0000:00:14.0 {\n  }\n}\n' > "$R/etc/vpp/startup.conf"
OUT=$(HC add eth9)
said "the boot's claim" && ! called "af_xdp" \
  && ok "a card startup.conf names is the boot's, not a hot one" || bad "the boot's card: $OUT"
fresh; echo down > "$S/vppstate"
OUT=$(HC add eth9)
said "its next start claims the card" && ! called "name xdp-" \
  && ok "with the dataplane not answering, the next start takes it" || bad "no dataplane: $OUT"
fresh; rm -rf "$R/sys/class/net/eth9"
OUT=$(HC add eth9)
said "no longer a Linux interface" \
  && ok "a card already claimed through DPDK is gone from Linux, and said so" || bad "gone: $OUT"
fresh; rm "$R/sys/class/net/eth9/device"
OUT=$(HC add eth9)
said "not a PCI card" && ! called "tune" \
  && ok "a netdev with no PCI parent is not offered" || bad "no pci: $OUT"

echo "=== the name still held after the rename: the card stays with Linux ==="
fresh
cat > "$T/ip2" <<EOF
#!/bin/sh
[ "\$*" = "link show dev eth9" ] && exit 0
exec "$T/ip" "\$@"
EOF
chmod +x "$T/ip2"
OUT=$(PRISMA_ROOT=$R PRISMA_IP=$T/ip2 PRISMA_VPPCTL=$T/vppctl PRISMA_TUNE=$T/tune \
      PRISMA_SYSTEMCTL=$T/systemctl PRISMA_UDEVADM=$T/udevadm \
      sh "$REPO/appliance/prisma-hot-card" add eth9); RC=$?
[ $RC = 1 ] && said "still held" && ! called "af_xdp" \
  && called "^ip link set dev xdp-eth9 name eth9$" \
  && ok "renamed back and not attached, rather than attached under a name it cannot carry" \
  || bad "a held name (rc=$RC): $OUT"

echo "=== a refused attach puts the card back ==="
fresh; echo "af_xdp: cannot load program" > "$S/refuse"
OUT=$(HC add eth9); RC=$?
[ $RC = 1 ] && called "^ip link set dev xdp-eth9 name eth9$" && said "stays with Linux" \
  && [ ! -s "$R/run/prisma-routing/hot-cards" ] \
  && ok "the card is renamed back, not left half-taken, and nothing is recorded" \
  || bad "a refused attach (rc=$RC): $OUT"

echo "=== restart-dataplane: stop, hand the cards back, tune, start ==="
fresh; HC add eth9 >/dev/null; : > "$S/calls"
OUT=$(HC restart-dataplane); RC=$?
_seq=$(grep -E "^systemctl|^giveback|name eth9$" "$S/calls" | tr '\n' '|')
[ $RC = 0 ] && [ "$_seq" = "systemctl is-active --quiet prisma-routing-containers.service|systemctl is-active --quiet vpp.service|systemctl stop prisma-routing-containers.service|giveback restart|ip link set dev xdp-eth9 name eth9|systemctl restart prisma-routing-tune.service|systemctl start prisma-routing-containers.service|systemctl is-active --quiet prisma-routing.service|systemctl start prisma-routing.service|systemctl cat prisma-routing-api-proxy.socket|" ] \
  && ok "stopped, every claimed card given back, renamed back, tuned, started, replayed" \
  || bad "the sequence (rc=$RC): $_seq"
! called "restart prisma-routing-containers" \
  && ok "and never a restart of the containers unit, which systemd refuses" \
  || bad "a restart of the containers unit was asked"
[ ! -e "$R/run/prisma-routing/hot-cards" ] && ok "and the record is cleared" \
  || bad "the record outlived the dataplane that knew it"
fresh; HC add eth9 >/dev/null; : > "$S/calls"; touch "$S/nostop" "$S/stillactive"
OUT=$(HC restart-dataplane); RC=$?
[ $RC = 1 ] && ! called "name eth9$" && ! called "prisma-routing-tune" && ! called "giveback" \
  && [ -s "$R/run/prisma-routing/hot-cards" ] && said "nothing was changed" \
  && ok "a dataplane that would not stop, and still runs, is left exactly as it was" \
  || bad "a failed stop (rc=$RC): $(cat "$S/calls")"
rm -f "$S/nostop" "$S/stillactive"

echo "=== #564: a stop that failed with the dataplane down still ends started ==="
# 10.95.200.198, 0.149.26: the containers went down, the stop did not report
# success, and this returned "nothing was changed" - leaving the box with no
# dataplane until a reboot.
fresh; HC add eth9 >/dev/null; : > "$S/calls"; touch "$S/nostop"
OUT=$(HC restart-dataplane); RC=$?
called "start prisma-routing-containers.service" && called "start prisma-routing.service" \
  && ! said "nothing was changed" \
  && ok "the containers and the configuration are started again" \
  || bad "a stop that failed on a dataplane going down (rc=$RC): $(cat "$S/calls")"
rm -f "$S/nostop"
fresh; : > "$S/calls"; touch "$S/apisocket"
OUT=$(HC restart-dataplane); RC=$?
called "reset-failed prisma-routing-api-proxy.socket" \
  && called "start prisma-routing-api-proxy.socket" \
  && ok "the API's socket is reset and started again after the dataplane" \
  || bad "the API's socket was left as the restart found it: $(cat "$S/calls")"
rm -f "$S/apisocket"

echo "=== a box on the units restarts vpp.service, never gives a held card back ==="
# The 0.150.0 install lab: on vpp.service the containers stop was a no-op, the
# give-back ran with VPP still holding the card through vfio-pci, and the
# kernel hung waiting for it to let go.
fresh; HC add eth9 >/dev/null; : > "$S/calls"; touch "$S/units"
OUT=$(HC restart-dataplane); RC=$?
_seq=$(grep -E "^systemctl (stop|start|restart) |^giveback" "$S/calls" | tr '\n' '|')
[ $RC = 0 ] && [ "$_seq" = "systemctl stop vpp.service|giveback restart|systemctl restart prisma-routing-tune.service|systemctl start vpp.service|systemctl start prisma-routing.service|" ] \
  && ok "vpp.service stopped before the give-back and started after the tuner" \
  || bad "the units sequence (rc=$RC): $_seq"
! called "start prisma-routing-containers.service" \
  && ok "and the containers unit, which Conflicts with it, is never started" \
  || bad "the containers unit was started on a units box: $(cat "$S/calls")"
rm -f "$S/units"

echo "=== #564: the restart ends with the names a boot gives the cards ==="
# 10.40.200.166, 0.149.30: the dataplane's Linux ends (TAPs named enp1s0,
# enp1s0.666) outlived the stop, udev could not rename the given-back card
# from eth0 ("File exists"), the tuner wrote eth0 into startup.conf, and the
# saved configuration naming enp1s0 was refused - the box went dark.
fresh
printf 'dpdk {\n  dev 0000:00:14.0 {\n    num-rx-queues 1\n    name ens20\n  }\n}\n' > "$R/etc/vpp/startup.conf"
cp "$R/etc/vpp/startup.conf" "$S/startup.before"
rm -rf "$R/sys/class/net/eth9"
mkdir -p "$R/sys/class/net/eth0" "$R/sys/class/net/ens20" "$R/sys/class/net/ens20.666"
ln -s "../../../devices/pci0000:00/0000:00:14.0" "$R/sys/class/net/eth0/device"
: > "$R/sys/class/net/ens20/tun_flags"; : > "$R/sys/class/net/ens20.666/tun_flags"
mkdir -p "$R/sys/class/net/ens21"; : > "$R/sys/class/net/ens21/tun_flags"
: > "$S/calls"; touch "$S/tunewrites"; echo ens20 > "$S/loadneeds"
OUT=$(HC restart-dataplane); RC=$?
_order=$(grep -nE "^ip link del dev ens20$|^ip link del dev ens20.666$|^giveback|^udevadm settle|^ip link set dev eth0 name ens20$|^systemctl restart prisma-routing-tune|^systemctl start prisma-routing-containers" "$S/calls" | cut -d: -f2- | tr '\n' '|')
[ "$_order" = "ip link del dev ens20|ip link del dev ens20.666|giveback restart|udevadm settle --timeout=10|ip link set dev eth0 name ens20|systemctl restart prisma-routing-tune.service|systemctl start prisma-routing-containers.service|" ] \
  && ok "stale Linux ends removed, cards given back, udev settled, names restored, then the tuner, then the dataplane" \
  || bad "the order (rc=$RC): $_order"
_names_after=$(grep -o "name [a-z0-9.]*" "$R/etc/vpp/startup.conf" | tr '\n' ' ')
[ "$_names_after" = "name ens20 " ] \
  && ok "the startup.conf the restart ends with names the card ens20, as before it" \
  || bad "startup.conf names after the restart: $_names_after"
[ $RC = 0 ] && ! called "link del dev ens21$" \
  && ok "the saved configuration loads, and a TAP not named for a card is left alone" \
  || bad "rc=$RC, or ens21 was removed: $(grep ens21 "$S/calls")"
rm -f "$S/tunewrites" "$S/loadneeds"

echo "=== #564: a configuration that does not load is said, and fails the restart ==="
fresh; : > "$S/calls"; echo enp1s0 > "$S/loadneeds"
OUT=$(HC restart-dataplane); RC=$?
[ $RC = 1 ] && said "did not load onto the restarted dataplane" \
  && ok "the restart unit checks its own result: rc 1 and the reason in its log" \
  || bad "a configuration that did not load (rc=$RC): $OUT"
rm -f "$S/loadneeds"

echo "=== #564: a load that fails while FRR starts, then loads, is a success ==="
# 10.40.200.166, 0.149.32: the restart said "did not load" and the API
# answered apply_failed; the load had only failed its first tries, FRR still
# starting, and loaded on a later one - the box was whole.
fresh; : > "$S/calls"; echo 2 > "$S/loadfails"
OUT=$(HC restart-dataplane); RC=$?
_tries=$(grep -c "^systemctl start prisma-routing.service$" "$S/calls")
[ $RC = 0 ] && ! said "did not load onto" && [ "$_tries" = 3 ] \
  && said "did not load yet; trying again" \
  && ok "two refusals, then the load: success, after 3 tries" \
  || bad "a load that came on the third try (rc=$RC, tries=$_tries): $OUT"
fresh; : > "$S/calls"; echo 99 > "$S/loadfails"
OUT=$(HC restart-dataplane); RC=$?
_tries=$(grep -c "^systemctl start prisma-routing.service$" "$S/calls")
[ $RC = 1 ] && said "did not load onto the restarted dataplane" && [ "$_tries" = 4 ] \
  && ok "a load that never comes: failure, said, after the bound (4 tries in 30 s)" \
  || bad "a load that never came (rc=$RC, tries=$_tries): $OUT"
fresh; : > "$S/calls"; : > "$S/loaded"; echo 99 > "$S/loadfails"
OUT=$(HC restart-dataplane); RC=$?
[ $RC = 0 ] && ! called "start prisma-routing.service" \
  && ok "loaded already by another start (the watcher): success, not started again" \
  || bad "an already active configuration (rc=$RC): $(cat "$S/calls")"
rm -f "$S/loadfails" "$S/loaded"

echo "=== the paths it runs are the paths install.sh writes ==="
# Measured on a KVM guest: the default pointed at /usr/lib/prisma-routing/
# prisma-routing-tune, install.sh puts the tuner in /usr/bin, and every hot
# card was "left to Linux" because the tuner gave no answer. Every stub above
# overrides the path, so nothing else here could see it.
_tune=$(sed -n 's/^TUNE=\${PRISMA_TUNE:-\(.*\)}$/\1/p' "$REPO/appliance/prisma-hot-card")
grep -q "appliance/prisma-routing-tune\" \"\$DESTDIR$_tune\"" "$REPO/appliance/install.sh" \
  && ok "the tuner's default path ($_tune) is where install.sh puts it" \
  || bad "prisma-hot-card runs '$_tune', which install.sh does not write"
grep -q "\$DESTDIR/usr/lib/prisma-routing/prisma-hot-card\"" "$REPO/appliance/install.sh" \
  && grep -q '"/usr/lib/prisma-routing/prisma-hot-card")' "$REPO/src/prismash/prismash.py" \
  && ok "and the VTY runs the script from where install.sh puts it" \
  || bad "the VTY's HOT_CARD_TOOL and install.sh disagree"
grep -q '^GIVE_BACK=${PRISMA_GIVE_BACK:-/usr/lib/prisma-routing/give-the-card-back}$' "$REPO/appliance/prisma-hot-card" \
  && grep -q '"$DESTDIR/usr/lib/prisma-routing/give-the-card-back"' "$REPO/appliance/install.sh" \
  && ok "and the give-back it runs is where install.sh puts it" \
  || bad "prisma-hot-card's GIVE_BACK and install.sh disagree"
grep -q 'ACTION=="add|move"' "$REPO/appliance/udev/90-prisma-hot-card.rules" \
  && ok "the udev rule answers the move a renamed card ends with" \
  || bad "the rule answers add only, and systemd acts on the move"

echo
[ "$fail" = 0 ] && echo "ALL TESTS PASSED" || echo "$fail FAILURES"
[ "$fail" = 0 ]
