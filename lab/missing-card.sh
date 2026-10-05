# OFF-RED-AT-LEAST: 6
# A card the saved file names is not on the box at boot (#575).
#
#   [netns mgmt] 192.0.2.2 --- veth m19/ens19 --- [box]  ens19: management
#   [netns up]   198.51.100.2 --- veth u20/ens20 --- [box]  ens20: MISSING,
#                                                          plugged in later
#
# Measured on 10.40.200.166 (0.151.1): the X520 failed its driver's probe at
# boot, `prismash --load` refused the saved file whole over it, and the box
# came up with nothing - its management port, a card that WAS there,
# included. This lab boots the same shape: the dataplane holds ens19, the file
# names ens19 and ens20, and ens20 is not there. The box MUST answer on ens19,
# say what it left out, refuse `save` (the file is never trimmed), and - once
# ens20 is plugged in the way udev plugs a card in, through prisma-hot-card -
# put ens20's statements on by itself, which is what the watcher runs every
# tick (`prismash --apply-pending`).
#
# MODE=off: PRISMA_MISSING_CARD_STRICT=1, the behaviour before #575 - the file
# refused whole. Management is then not answered, nothing is pending, and the
# card plugged in later gets nothing. This lab MUST go red then.
. /src/lab/lib.sh
. /src/lab/verdict.sh
set -u
MODE=${1:-on}
if [ "$MODE" = off ]; then
  export PRISMA_MISSING_CARD_STRICT=1
fi
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing /run/prisma-routing
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
export PRISMA_CONFIG_PENDING=/run/prisma-routing/config-pending.json
rm -f "$PRISMA_CONFIG_PENDING"

# A card the way prisma-hot-card sees one: a netdev whose device is on PCI,
# and a tuner that says claim. lab/hot-card.sh has the reasons.
R=/tmp/mc-root
mkdir -p "$R/etc/vpp" "$R/etc/prisma-routing" "$R/run/prisma-routing"
printf 'dpdk {\n}\n' > "$R/etc/vpp/startup.conf"
cat > /tmp/tune-claims <<'EOF'
#!/bin/sh
for a; do n=$a; done
echo "claim: $n"
EOF
chmod +x /tmp/tune-claims
plug() { # <netdev> <pci> <netns> <peer-netdev> <peer-address>
  ip netns add "$3"
  ip link add "$1" type veth peer name "$4"
  ip link set "$4" netns "$3"
  ip netns exec "$3" sh -c "ip link set lo up; ip link set $4 up; ip addr add $5 dev $4"
  mkdir -p "$R/sys/class/net/$1" "$R/sys/devices/pci0000:00/$2"
  ln -sfn "../../../devices/pci0000:00/$2" "$R/sys/class/net/$1/device"
  cat "/sys/class/net/$1/address" > "$R/sys/class/net/$1/address"
  PRISMA_ROOT=$R PRISMA_TUNE=/tmp/tune-claims sh /src/appliance/prisma-hot-card add "$1" 2>&1 \
    | sed 's/^/  hot-card: /'
}

echo "=========== the box boots with ens19 and without ens20 ==========="
plug ens19 0000:00:13.0 mgmt m19 192.0.2.2/24
cat > /etc/prisma-routing/saved.conf <<'EOF'
interface ens19
 linux-name ens19
 ipv4 address 192.0.2.1/24
 exit
interface ens20
 linux-name ens20
 ipv4 address 198.51.100.1/24
 mtu 1500
 exit
EOF
LOAD=$($HX --load /etc/prisma-routing/saved.conf 2>&1)
printf '%s\n' "$LOAD" | sed 's/^/  /'
sleep 2
MGMT=$(ip netns exec mgmt ping -c 3 -W 2 192.0.2.1 2>&1 | tail -2)
echo "  management: $MGMT"
PENDING=$($HX -c "show configuration pending" 2>&1)
printf '%s\n' "$PENDING" | sed 's/^/  /'
SAVE=$(PRISMA_CONFIG=/tmp/would-save.conf $HX -c "save" 2>&1)
printf '%s\n' "$SAVE" | sed 's/^/  save: /'
WHOLE=$(cat /etc/prisma-routing/saved.conf)

echo; echo "=========== ens20 is plugged in; the watcher's tick runs ==========="
plug ens20 0000:00:14.0 up u20 198.51.100.2/24
APPLY=$($HX --apply-pending 2>&1)
printf '%s\n' "$APPLY" | sed 's/^/  /'
sleep 2
UP=$(ip netns exec up ping -c 3 -W 2 198.51.100.1 2>&1 | tail -2)
echo "  ens20: $UP"
AFTER=$($HX -c "show configuration pending" 2>&1)
printf '%s\n' "$AFTER" | sed 's/^/  after: /'

echo; echo "=========== VERDICT ==========="
expect_in "the load said which card the box does not have" \
  "this box does not have: ens20" "$LOAD"
expect_not_in "  and did not refuse the file whole" \
  "refusing to load this file" "$LOAD"
expect_in "management is answered on the card that IS there" \
  " 0% packet loss" "$MGMT"
expect_in "show configuration pending lists the card" "ens20" "$PENDING"
expect_in "  and what waits for it" "ipv4 address 198.51.100.1/24" "$PENDING"
expect_in "save is refused while it waits, so the file keeps it" \
  "save: refused" "$SAVE"
expect_in "  and the file on disk still names ens20" \
  "198.51.100.1/24" "$WHOLE"
expect_in "the card plugged in is taken up by the watcher's tick" \
  "the statements that waited for a card go on" "$APPLY"
expect_in "  and its peer is answered" " 0% packet loss" "$UP"
expect_in "  and nothing is pending after it" "nothing pending" "$AFTER"
verdict
