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
}
cat > "$T/ip" <<EOF
#!/bin/sh
echo "ip \$*" >> "$S/calls"
case "\$*" in
  "-4 -o addr show dev "*) cat "$S/addr" ;;
  "route show default") cat "$S/route" ;;
  "-d link show dev "*) for a in \$(cat "$S/alts"); do echo "    altname \$a"; done ;;
  "link property del dev "*) sed -i "s/\$7//" "$S/alts" ;;
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
EOF
chmod +x "$T/ip" "$T/vppctl" "$T/tune" "$T/systemctl"
HC() {
  PRISMA_ROOT=$R PRISMA_IP=$T/ip PRISMA_VPPCTL=$T/vppctl PRISMA_TUNE=$T/tune \
  PRISMA_SYSTEMCTL=$T/systemctl sh "$REPO/appliance/prisma-hot-card" "$@"
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

echo "=== a refused attach puts the card back ==="
fresh; echo "af_xdp: cannot load program" > "$S/refuse"
OUT=$(HC add eth9); RC=$?
[ $RC = 1 ] && called "^ip link set dev xdp-eth9 name eth9$" && said "stays with Linux" \
  && [ ! -s "$R/run/prisma-routing/hot-cards" ] \
  && ok "the card is renamed back, not left half-taken, and nothing is recorded" \
  || bad "a refused attach (rc=$RC): $OUT"

echo "=== restart-dataplane hands the cards back, then restarts ==="
fresh; HC add eth9 >/dev/null; : > "$S/calls"
OUT=$(HC restart-dataplane); RC=$?
[ $RC = 0 ] && called "^ip link set dev xdp-eth9 name eth9$" \
  && ok "the card is renamed back, so the tuner claims it under its own name" \
  || bad "not renamed back (rc=$RC): $(cat "$S/calls")"
grep -n "" "$S/calls" | grep -A1 "name eth9" | grep -q "systemctl restart prisma-routing-tune.service" \
  && called "^systemctl restart prisma-routing-containers.service$" \
  && ok "and only then the tuner and the dataplane are restarted" \
  || bad "the restart order: $(cat "$S/calls")"
[ ! -e "$R/run/prisma-routing/hot-cards" ] && ok "and the record is cleared" \
  || bad "the record outlived the dataplane that knew it"

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
grep -q 'ACTION=="add|move"' "$REPO/appliance/udev/90-prisma-hot-card.rules" \
  && ok "the udev rule answers the move a renamed card ends with" \
  || bad "the rule answers add only, and systemd acts on the move"

echo
[ "$fail" = 0 ] && echo "ALL TESTS PASSED" || echo "$fail FAILURES"
[ "$fail" = 0 ]
