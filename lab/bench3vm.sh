#!/bin/sh
# BPA under real traffic: subscribers, the released ISO installed, the internet - three local VMs (#475).
#
#   sh tools/lab.sh bench3vm          the bench, against the newest released ISO
#   sh tools/lab.sh bench3vm off      the same bench with the pool deterministic:
#                                     BPA switched off, and the checks that are
#                                     about BPA go red
#   ISO=/cache/prisma-routing-X.Y.Z.iso sh tools/lab.sh bench3vm
#                                     a given medium instead of the newest one
#   BENCH_UPGRADE=prisma-routing-<stamp>-upgrade.tar.gz sh tools/lab.sh bench3vm
#                                     the medium, then upgraded to that
#                                     artifact (a file in the bench's cache,
#                                     built by appliance/build-upgrade.sh) the
#                                     way an operator does it, and the
#                                     dataplane restarted onto it: how a
#                                     change to the plugin is measured here
#                                     before it has a medium of its own (#603)
#
# WHAT IT IS. Three qemu/KVM guests inside this lab's container, wired by two
# Linux bridges that exist only in the container's network namespace:
#
#   subscribers (Debian 12) --brin-- box (the ISO, installed) --brout-- internet (Debian 12)
#   100.64.0.1-240/22          ens16 100.64.3.254/22             198.51.100.1/30
#                              ens17 198.51.100.2/30             routes 203.0.113.0/29
#                              pool bench: 203.0.113.0/29,       to the box
#                              block-size 4096, mode bpa,
#                              policy interleaved
#
# The bridges have no address and no uplink, so no test packet can leave the
# container, let alone the host. Each guest's management card is qemu's user
# network with restrict=on - the guest cannot reach anything through it - and
# a forwarded port on the container's loopback, which is how the lab reaches
# each one over ssh. The only time a guest touches the outside is the one-off
# preparation of the Debian image below, which needs apt.
#
# This was a Proxmox bench until 2026-10-11, and those rounds are in #475. It
# moved here so that it is a thing anybody can run, on this build machine and
# with no hypervisor host of anybody's touched.
#
# THE BOX IS INSTALLED THE WAY AN OPERATOR DOES IT: the published ISO, its
# sha256 checked against the one published beside it, booted, and installed
# unattended onto a blank disk with prisma-routing.install= (what
# lab/install-iso.sh drives too); then the disk is booted with the ISO gone,
# root logs in at the serial console - into the VTY - and the configuration is
# typed through prismash. The only thing the lab adds at the console is its
# ssh key, so the rest runs over ssh.
#
# CACHED, AND OUT OF THE REPOSITORY. tools/lab.sh mounts
# /var/cache/prisma/bench3vm (BENCH_CACHE) at /cache. There live: the ISO and
# its .sha256, the installed disk (box-<version>.qcow2, reused until
# BENCH_REINSTALL=1), the Debian cloud image and its SHA512SUMS, the prepared
# image (bench-debian.qcow2: iperf3 and tcpdump installed, cloud-init then
# disabled), the bench's ssh key, and runs/<time>-<mode>/, which holds every
# file the verdict was read from. Every guest boots from a throwaway overlay,
# so a run never changes a cached disk.
#
# WHAT IT MEASURES, in order:
#   1. fill: 60 subscribers join (half the 120 blocks), then all 240 - each
#      holds one TCP connection to the internet guest's who-server and sends
#      one UDP datagram; the server logs the public address:port it SAW;
#   2. churn: BENCH_ROUNDS rounds of half the holders leaving (with a reset,
#      so the ports free at once), the box returning their blocks, and 30
#      random subscribers arriving - and after each step `show bpa subscriber`
#      for all 240, which is the per-public-address count the maintainer asked
#      for on 2026-09-30;
#   3. throughput: 8 subscribers, iperf3 TCP, then UDP with 64-byte payloads,
#      with `show errors`, `show runtime` and the interface counters cleared
#      before and read after - Gbit/s, packets/s, and the handoff share per
#      worker (#487).
# Then lab/bench3vm_check.py holds the three witnesses against each other.
#
# THE NUMBERS ARE A SANITY CHECK, NOT A NIC BENCHMARK. Virtio through vhost
# on a build machine other labs are using at the same time: what limits them
# is the host's packet copying and whoever else is running, as #475's
# Proxmox rounds measured. #28 is the real one.
#
# THE OFF RUN. `off` makes the pool `mode deterministic` and changes nothing
# else. The subscribers still get through - up to the formula's capacity - so
# the checks about the path stay green, and the ones about BPA go red: the
# pool never fills on demand, no block is returned, and there is no record
# naming who held a port. Measured on 0.153.0: five red - the pool did not
# fill on demand, no block came back, no record arrived, no connection could
# be traced to a subscriber, and no allocation was there to be used.
#
# Not run by tools/run-all-labs.sh: it installs an ISO and boots three
# machines, which is a quarter of an hour the first time and about ten
# minutes after, and it is a measurement as much as a test (NOT_LABS says so).
set -u
. /src/lab/verdict.sh
MODE=${1:-on}
case "$MODE" in on|off) ;; *) echo "usage: bench3vm [on|off]"; exit 2 ;; esac
LAB_START=$(date +%s)

C=/cache
[ -d "$C" ] && [ -w "$C" ] || { echo "no writable /cache: run it through tools/lab.sh"; exit 2; }
[ -w /dev/kvm ] || { echo "no /dev/kvm: three guests without KVM would measure the emulator"; exit 2; }
T=/tmp/bench
rm -rf "$T"; mkdir -p "$T"
RUN=$C/runs/$(date -u +%Y%m%dT%H%M%SZ)-$MODE
mkdir -p "$RUN"
echo "$MODE" > "$RUN/mode"
KEY=$T/key
BOX_SMP=${BOX_SMP:-4}       # 3 workers; the issue's Proxmox box had 7
BOX_MEM=${BOX_MEM:-6144}    # the documented minimum to forward
GUEST_SMP=${GUEST_SMP:-2}
GUEST_MEM=${GUEST_MEM:-1024}
SUBS=${BENCH_SUBS:-240}
ROUNDS=${BENCH_ROUNDS:-6}
HOST=bench-box
PW=bench-secret-475
QUEUES=$((BOX_SMP - 1))
PIDS=""

# not a unit: the lab's own teardown
cleanup() {
  for p in $PIDS; do kill "$p" 2>/dev/null; done
  sleep 2
  for p in $PIDS; do kill -9 "$p" 2>/dev/null; done
  rm -f "$T"/*.qcow2
}
trap cleanup EXIT INT TERM

SSHO="-i $KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5"
# not a unit: ssh to a guest by its forwarded port
on() { _p=$1; shift; ssh $SSHO -p "$_p" root@127.0.0.1 "$@"; }
# not a unit: scp to a guest by its forwarded port
put() { _p=$1; shift; scp -q $SSHO -P "$_p" "$@"; }
BOX=2201; SUBP=2202; INET=2203

# not a unit: wait until a guest answers ssh
wait_ssh() {  # <port> <seconds>
  _n=0
  while [ "$_n" -lt "$2" ]; do
    on "$1" true 2>/dev/null && return 0
    sleep 3; _n=$((_n + 3))
  done
  return 1
}

echo "=========== the medium ==========="
if [ -z "${ISO:-}" ]; then
  # The newest version directory that HAS a medium. `latest/` is not it: a
  # patch release is published with its upgrade artifact and no ISO, and
  # `latest` moves to it (0.153.1 did, the day this was written).
  PUB=https://prismarouting.com.br/releases
  NAME=""
  for v in $(curl -4 -sSf --max-time 30 "$PUB/" | grep -o 'href="[0-9][0-9.]*/"' \
               | sed 's/href="//; s/\/"//' | sort -t. -k1,1nr -k2,2nr -k3,3nr | head -10); do
    NAME=$(curl -4 -sSf --max-time 30 "$PUB/$v/" | grep -o 'prisma-routing-[0-9.]*\.iso"' | tr -d '"' | head -1)
    [ -n "$NAME" ] && { REL=$PUB/$v/; break; }
  done
  [ -n "$NAME" ] || { echo "  no medium among the newest releases on $PUB, and no ISO= given"; exit 2; }
  ISO=$C/$NAME
  [ -f "$ISO" ] || curl -4 -sSf --max-time 1800 -o "$ISO" "$REL$NAME" || { echo "  download of $NAME failed"; rm -f "$ISO"; exit 2; }
  curl -4 -sSf --max-time 30 -o "$ISO.sha256" "$REL$NAME.sha256" || true
fi
VER=$(basename "$ISO" .iso | sed 's/^prisma-routing-//')
echo "  $ISO"
expect "the ISO's sha256 is the one published beside it" \
  sh -c "cd '$(dirname "$ISO")' && sha256sum -c '$(basename "$ISO").sha256'"
echo "medium=$(basename "$ISO") $(cut -c1-16 "$ISO.sha256" 2>/dev/null)..." > "$RUN/meta.txt"

echo
echo "=========== the guests' image (Debian 12, prepared once) ==========="
[ -f "$C/bench_key" ] || { rm -f "$C/bench-debian.qcow2"; ssh-keygen -q -t ed25519 -N '' -C bench3vm -f "$C/bench_key"; }
cp "$C/bench_key" "$KEY"; chmod 600 "$KEY"
if [ ! -f "$C/bench-debian.qcow2" ]; then
  D=https://cloud.debian.org/images/cloud/bookworm/latest
  [ -f "$C/debian-12-genericcloud-amd64.qcow2" ] || curl -4 -sSfL --max-time 1800 \
    -o "$C/debian-12-genericcloud-amd64.qcow2" "$D/debian-12-genericcloud-amd64.qcow2"
  curl -4 -sSfL --max-time 60 -o "$C/SHA512SUMS" "$D/SHA512SUMS"
  (cd "$C" && grep ' debian-12-genericcloud-amd64.qcow2$' SHA512SUMS | sha512sum -c -) \
    || { echo "  the Debian image does not match SHA512SUMS"; exit 2; }
  mkdir -p "$T/seed"
  cat > "$T/seed/user-data" <<EOF
#cloud-config
disable_root: false
ssh_pwauth: false
ssh_authorized_keys: ["$(cat "$C/bench_key.pub")"]
package_update: true
packages: [iperf3, tcpdump, python3]
runcmd:
  - [sh, -c, 'touch /etc/cloud/cloud-init.disabled; echo BENCH-PREPARED > /dev/ttyS0; sync; poweroff']
EOF
  printf 'instance-id: bench-debian-1\nlocal-hostname: bench-debian\n' > "$T/seed/meta-data"
  : > "$T/seed/vendor-data"
  (cd "$T/seed" && exec python3 -m http.server 8123 --bind 127.0.0.1 >/dev/null 2>&1) &
  _h=$!
  cp "$C/debian-12-genericcloud-amd64.qcow2" "$T/prep.qcow2"
  qemu-img resize "$T/prep.qcow2" 4G >/dev/null
  # The one boot with a way out (apt), and the only one with no restrict.
  timeout 900 qemu-system-x86_64 -enable-kvm -cpu host -m 1024 -smp 2 -nographic -no-reboot \
    -drive file="$T/prep.qcow2",format=qcow2,if=virtio \
    -smbios "type=1,serial=ds=nocloud-net;s=http://10.0.2.2:8123/" \
    -netdev user,id=mgmt -device virtio-net-pci,netdev=mgmt,addr=0x3,mac=52:54:00:47:50:01 \
    > "$RUN/prepare-debian.log" 2>&1
  kill "$_h" 2>/dev/null
  grep -aq BENCH-PREPARED "$RUN/prepare-debian.log" \
    || { echo "  the Debian image was not prepared; see $RUN/prepare-debian.log"; exit 2; }
  mv "$T/prep.qcow2" "$C/bench-debian.qcow2"
  echo "  prepared $C/bench-debian.qcow2"
else
  echo "  cached: $C/bench-debian.qcow2"
fi

echo
echo "=========== the box, installed from the medium ==========="
DISK=$C/box-$VER.qcow2
if [ ! -f "$DISK" ] || [ "${BENCH_REINSTALL:-0}" = 1 ]; then
  mkdir -p /mnt/iso "$T/bf"
  mount -o loop,ro "$ISO" /mnt/iso
  cp /mnt/iso/live/vmlinuz /mnt/iso/live/initrd "$T/bf/"
  umount /mnt/iso
  rm -f "$DISK.tmp"
  qemu-img create -f qcow2 "$DISK.tmp" 40G >/dev/null
  _t0=$(date +%s)
  # The kernel and initrd are handed over only to put the answers on the
  # command line, exactly as lab/install-iso.sh does; the medium is the ISO.
  timeout 1200 qemu-system-x86_64 -enable-kvm -cpu host -m "$BOX_MEM" -smp "$BOX_SMP" \
    -nographic -no-reboot -cdrom "$ISO" -kernel "$T/bf/vmlinuz" -initrd "$T/bf/initrd" \
    -drive file="$DISK.tmp",format=qcow2,if=virtio \
    -netdev user,id=mgmt,restrict=on -device virtio-net-pci,netdev=mgmt,addr=0x9,host_mtu=1500 \
    -append "boot=live components console=ttyS0,115200 prisma-routing.install=/dev/vda \
             prisma-routing.yes prisma-routing.mgmt-address=dhcp prisma-routing.log-gb=2 \
             prisma-routing.hostname=$HOST prisma-routing.timezone=UTC prisma-routing.password=$PW" \
    > "$RUN/install-console.log" 2>&1
  if grep -aq "Prisma Routing installed to /dev/vda" "$RUN/install-console.log"; then
    mv "$DISK.tmp" "$DISK"
    echo "  installed in $(( $(date +%s) - _t0 )) s"
  else
    echo "  the install did not finish; see $RUN/install-console.log"
    rm -f "$DISK.tmp"; exit 1
  fi
else
  echo "  cached: $DISK (BENCH_REINSTALL=1 installs again)"
fi

echo
echo "=========== three guests, two isolated bridges ==========="
for br in brin brout; do
  ip link add "$br" type bridge 2>/dev/null
  # No address, no IPv6, no uplink: nothing on this bridge has a way out.
  sysctl -qw "net.ipv6.conf.$br.disable_ipv6=1" 2>/dev/null
  ip link set "$br" up
done
tap() {  # <tap> <bridge>
  ip tuntap add dev "$1" mode tap multi_queue 2>/dev/null
  ip link set "$1" master "$2" up
}
tap tbin brin; tap tbout brout; tap tsub brin; tap tinet brout

qemu-img create -f qcow2 -F qcow2 -b "$DISK" "$T/box.qcow2" >/dev/null
qemu-system-x86_64 -enable-kvm -cpu host -m "$BOX_MEM" -smp "$BOX_SMP" -display none -no-reboot \
  -drive file="$T/box.qcow2",format=qcow2,if=virtio \
  -chardev socket,id=s0,path=$T/box.sock,server=on,wait=off,logfile=$RUN/box-console.log \
  -serial chardev:s0 \
  -netdev user,id=mgmt,restrict=on,hostfwd=tcp:127.0.0.1:$BOX-:22 \
  -device virtio-net-pci,netdev=mgmt,addr=0x9,host_mtu=1500 \
  -netdev tap,id=nin,ifname=tbin,script=no,downscript=no,vhost=on,queues=$QUEUES \
  -device virtio-net-pci,netdev=nin,addr=0x10,mq=on,vectors=$((2 * QUEUES + 2)),host_mtu=1500,mac=52:54:00:47:51:10 \
  -netdev tap,id=nout,ifname=tbout,script=no,downscript=no,vhost=on,queues=$QUEUES \
  -device virtio-net-pci,netdev=nout,addr=0x11,mq=on,vectors=$((2 * QUEUES + 2)),host_mtu=1500,mac=52:54:00:47:51:11 \
  > "$RUN/qemu-box.log" 2>&1 &
PIDS="$PIDS $!"
guest() {  # <name> <ssh port> <tap> <mac>
  qemu-img create -f qcow2 -F qcow2 -b "$C/bench-debian.qcow2" "$T/$1.qcow2" >/dev/null
  qemu-system-x86_64 -enable-kvm -cpu host -m "$GUEST_MEM" -smp "$GUEST_SMP" -display none -no-reboot \
    -drive file="$T/$1.qcow2",format=qcow2,if=virtio -serial file:"$RUN/$1-console.log" \
    -netdev user,id=mgmt,restrict=on,hostfwd=tcp:127.0.0.1:$2-:22 \
    -device virtio-net-pci,netdev=mgmt,addr=0x3,mac=52:54:00:47:50:01 \
    -netdev tap,id=d,ifname=$3,script=no,downscript=no,vhost=on,queues=2 \
    -device virtio-net-pci,netdev=d,addr=0x10,mq=on,vectors=6,mac=$4 \
    > "$RUN/qemu-$1.log" 2>&1 &
  PIDS="$PIDS $!"
}
guest subs $SUBP tsub 52:54:00:47:52:10
guest inet $INET tinet 52:54:00:47:53:10

echo "  box: $BOX_SMP vCPU, $BOX_MEM MB, data cards with $QUEUES queues; guests: $GUEST_SMP vCPU, $GUEST_MEM MB"
_t0=$(date +%s)
if ! timeout 1000 python3 /src/lab/bench3vm_console.py "$T/box.sock" "$T/drive.log" \
       "$HOST" "$PW" "$C/bench_key.pub"; then
  echo "  the box's console did not take the key; see $RUN/box-console.log"; exit 1
fi
echo "  box up and logged in at its console after $(( $(date +%s) - _t0 )) s"
for p in $BOX $SUBP $INET; do
  wait_ssh "$p" 300 || { echo "  no ssh on port $p"; exit 1; }
done
echo "  all three answer ssh"

# THIS TREE, ON THE RELEASED BOX (#603). The bench installs a published
# medium, so on its own it measures what has shipped. A change to the plugin
# is measured by upgrading that box to an artifact built from the change -
# appliance/upgrade, then `system dataplane restart`, which is the route the
# wiki gives an operator - and the plugin's version is read back so a run
# cannot report on the old dataplane by mistake. The overlay the box runs on
# is thrown away with the run, so the cached disk stays the medium's.
if [ -n "${BENCH_UPGRADE:-}" ]; then
  echo
  echo "=========== the box, upgraded to $BENCH_UPGRADE ==========="
  _a=$C/$(basename "$BENCH_UPGRADE")
  [ -f "$_a" ] || { echo "  no $_a: put the artifact in the bench's cache"; exit 2; }
  _d=$(basename "$_a" .tar.gz)
  put $BOX "$_a" root@127.0.0.1:/root/
  on $BOX "cd /root && tar xzf $(basename "$_a") && sh /root/$_d/appliance/upgrade" \
    > "$RUN/upgrade.txt" 2>&1
  sed 's/^/  | /' "$RUN/upgrade.txt" | tail -5
  _want=$(on $BOX "sed -n 's/^stamp=//p' /root/$_d/RELEASE" | awk '{print $2}' | tr -d '()')
  on $BOX "prismash -c 'system dataplane restart'" > "$RUN/dataplane-restart.txt" 2>&1
  _n=0
  until on $BOX "prismash -c 'show version'" 2>/dev/null | grep -i plugin | grep -q "$_want" \
        || [ "$_n" -ge 300 ]; do sleep 5; _n=$((_n + 5)); done
  on $BOX "prismash -c 'show version'" > "$RUN/version-after-upgrade.txt" 2>&1
  expect "the box's dataplane runs the artifact's plugin ($_want)" \
    sh -c "grep -i plugin '$RUN/version-after-upgrade.txt' | grep -q '$_want'"
  echo "upgraded_to=$(basename "$_a") plugin=$_want" >> "$RUN/meta.txt"
fi

{
  echo "box=$(on $BOX 'cat /etc/prisma-routing/version 2>/dev/null || prismash -c "show version" | head -1')"
  echo "box_vcpu=$BOX_SMP box_mem_mb=$BOX_MEM data_queues=$QUEUES"
  echo "guest_vcpu=$GUEST_SMP guest_mem_mb=$GUEST_MEM"
  echo "host_cpu=$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ //') host_threads=$(nproc)"
  echo "host_load_before=$(cut -d' ' -f1-3 /proc/loadavg)"
} >> "$RUN/meta.txt"

echo
echo "=========== the internet and the subscribers ==========="
put $INET /src/lab/bench3vm_vm.py /src/lab/syslog_sink.py root@127.0.0.1:/root/
put $SUBP /src/lab/bench3vm_vm.py root@127.0.0.1:/root/
on $INET 'ip link set ens16 up && ip addr add 198.51.100.1/30 dev ens16 &&
  ip route add 203.0.113.0/29 via 198.51.100.2 &&
  (setsid python3 /root/bench3vm_vm.py srv /root/who.log </dev/null >/root/srv.out 2>&1 &) &&
  (setsid python3 /root/syslog_sink.py tcp 198.51.100.1 601 /root/syslog.txt </dev/null >/root/sink.out 2>&1 &) &&
  for p in 5201 5202 5203 5204 5205 5206 5207 5208; do iperf3 -s -D -p $p; done'
on $SUBP "ip link set ens16 up && for i in \$(seq 1 $SUBS); do ip addr add 100.64.\$((i / 256)).\$((i % 256))/22 dev ens16; done &&
  ip route add default via 100.64.3.254 &&
  (setsid python3 /root/bench3vm_vm.py subd 100.64.0.1 $SUBS </dev/null >/root/subd.out 2>&1 &)"
echo "  $SUBS subscribers on 100.64.0.0/22, servers on 198.51.100.1"

echo
echo "=========== the box, configured at its VTY ==========="
POOLMODE=bpa
[ "$MODE" = off ] && POOLMODE=deterministic
# udp 20: below RFC 4787's floor on purpose, and the box says so at commit.
# A subscriber's one UDP datagram would otherwise hold its block for 300 s
# after it left, and a churn round would take five minutes instead of thirty
# seconds. TCP is closed with a reset, which frees its port at once.
on $BOX "prismash -c configure \
 -c 'interface ens16' -c 'description subscribers' -c 'ipv4 address 100.64.3.254/22' -c 'no shutdown' -c 'cgnat inside' -c exit \
 -c 'interface ens17' -c 'description internet' -c 'ipv4 address 198.51.100.2/30' -c 'no shutdown' -c 'cgnat outside' -c exit \
 -c 'router static' -c '0.0.0.0/0 next-hop 198.51.100.1' -c exit \
 -c nat -c cgnat -c 'pool bench' -c 'prefix-inside 100.64.0.0/22' -c 'prefix-outside 203.0.113.0/29' \
 -c 'block-size 4096' -c 'ports 1024 65535' -c 'policy interleaved' -c 'mode $POOLMODE' -c 'timeout udp 20' -c exit \
 -c 'block-holddown 5' -c 'logging syslog 198.51.100.1 enterprise 67033' -c top -c commit -c exit" \
  > "$RUN/commit.txt" 2>&1
sed 's/^/  | /' "$RUN/commit.txt"
on $BOX "prismash -c 'show running-config'" > "$RUN/running-config.txt" 2>&1
expect "the box took the configuration" grep -q "commit complete" "$RUN/commit.txt"
_n=0
until on $BOX 'ping -c1 -W1 198.51.100.1' >/dev/null 2>&1 || [ "$_n" -ge 60 ]; do sleep 2; _n=$((_n + 2)); done
expect "the box reaches the internet guest over its outside card" on $BOX 'ping -c2 -W2 198.51.100.1'
_n=0
until on $BOX "prismash -c 'show cgnat logging'" 2>/dev/null | grep -q "601  connected" || [ "$_n" -ge 60 ]; do sleep 2; _n=$((_n + 2)); done

ctl() { on $SUBP "python3 /root/bench3vm_vm.py ctl $*"; }
blocks() { on $BOX 'vppctl show bpa blocks' > "$RUN/blocks-$1.txt" 2>&1; }
# Every subscriber, asked of the box itself - one ssh, one vppctl each.
subscribers() {
  on $BOX "for i in \$(seq 1 $SUBS); do a=100.64.\$((i / 256)).\$((i % 256)); echo SUB \$a; vppctl show bpa subscriber \$a; done" \
    > "$RUN/subscribers-$1.txt" 2>&1
}
in_use() { on $BOX 'vppctl show bpa blocks' 2>/dev/null | sed -n 's/.*total: \([0-9]*\)\/.*/\1/p'; }
held() { ctl state | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["held"]))'; }

echo
echo "=========== 1. the pool fills ==========="
ctl join 1-60 > "$RUN/join-fill.json"
subscribers fresh
ctl join 1-$SUBS > "$RUN/join-full.json"
# On the internet guest's clock, which is the one its who-log is stamped by.
echo "fill_end=$(on $INET 'date +%s.%N')" >> "$RUN/meta.txt"
blocks full
subscribers full
sed 's/^/  /' "$RUN/blocks-full.txt"

echo
echo "=========== 2. churn: $ROUNDS rounds of half leaving, 30 arriving ==========="
r=1
while [ "$r" -le "$ROUNDS" ]; do
  ctl churn 0.5 "$r" > "$RUN/churn-$r-leave.json"
  want=$(held)
  _n=0
  # Back when the box's count of blocks in use is the subscribers still
  # holding one: their sessions are gone (reset; UDP after 20 s) and the
  # 5 s hold-down has passed.
  # A deterministic pool (the off run) has no count at all, and nothing to
  # wait for.
  while _u=$(in_use) && [ -n "$_u" ] && [ "$_u" != "$want" ] && [ "$_n" -lt 120 ]; do
    sleep 3; _n=$((_n + 3))
  done
  ctl arrive 30 "$((100 + r))" > "$RUN/churn-$r-arrive.json"
  subscribers "churn$r"
  echo "  round $r: $(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["left"]))' "$RUN/churn-$r-leave.json") left, blocks back after ${_n}s, $(python3 -c 'import json,sys; print(sum(r["ok"] for r in json.load(open(sys.argv[1]))["joined"]))' "$RUN/churn-$r-arrive.json") of 30 arrivals got through; $(held) holding"
  r=$((r + 1))
done
cp "$RUN/subscribers-churn$ROUNDS.txt" "$RUN/subscribers-end.txt"
ctl state > "$RUN/state-end.json"
blocks end

echo
echo "=========== 3. throughput, as a sanity number ==========="
# Eight subscribers that hold a block right now, each to its own server port.
TP=$(python3 -c 'import json,sys; print(" ".join(r["private"] for r in sorted(json.load(open(sys.argv[1]))["held"], key=lambda r: r["private"])[:8]))' "$RUN/state-end.json")
SECS=${BENCH_SECONDS:-20}
for tag in tcp udp; do
  on $BOX 'vppctl clear errors; vppctl clear runtime; vppctl clear interfaces'
  args="-t $SECS -J"
  [ "$tag" = udp ] && args="$args -u -l 64 -b 0"
  on $SUBP "n=0; for a in $TP; do n=\$((n + 1)); iperf3 -c 198.51.100.1 -p \$((5200 + n)) -B \$a $args > /tmp/iperf-$tag-\$n.json 2>&1 & done; wait"
  on $BOX 'vppctl show errors' > "$RUN/errors-$tag.txt" 2>&1
  on $BOX 'vppctl show runtime' > "$RUN/runtime-$tag.txt" 2>&1
  on $BOX 'vppctl show interface' > "$RUN/iface-$tag.txt" 2>&1
  for n in 1 2 3 4 5 6 7 8; do on $SUBP "cat /tmp/iperf-$tag-$n.json" > "$RUN/iperf-$tag-$n.json" 2>/dev/null; done
  echo "${tag}_seconds=$SECS" >> "$RUN/meta.txt"
done
echo "host_load_after=$(cut -d' ' -f1-3 /proc/loadavg)" >> "$RUN/meta.txt"

echo
echo "=========== collected ==========="
sleep 3
on $BOX "vppctl show bpa logging; prismash -c 'show cgnat logging'" > "$RUN/logging.txt" 2>&1
on $INET 'cat /root/who.log' > "$RUN/who.log"
on $INET 'cat /root/syslog.txt' > "$RUN/syslog.txt"
cp "$T/drive.log" "$RUN/box-console-driven.log" 2>/dev/null
echo "  $RUN"
echo
python3 /src/lab/bench3vm_check.py "$RUN" > "$RUN/check.txt" 2>&1
cat "$RUN/check.txt"
VERDICT_FAILURES=$((VERDICT_FAILURES + $(grep -c '^  \[FAIL\]' "$RUN/check.txt")))
echo
echo "  took $(( $(date +%s) - LAB_START )) s; everything above is in $RUN"
verdict
