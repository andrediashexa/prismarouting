#!/bin/sh
# Every startup.conf this tuner can write, read by the VPP that has to read it.
#
# WHAT WENT WRONG WITHOUT THIS. `hexarouting-tune` writes /etc/vpp/startup.conf
# and VPP is the only program that ever reads it. #330 added a per-device MTU
# line to the dpdk stanza, for a card whose maximum is below VPP's default of
# 9000:
#
#   dpdk { dev 0000:00:12.0 { num-rx-queues 1 mtu 1500 } }
#
# There is no `mtu` keyword in this VPP's dpdk configuration. VPP answers
#
#   dpdk_device_config: unknown input `mtu 1500'
#
# and exits; `vpp.service` carries Restart=always, so the box spends its boot
# in a restart loop and forwards nothing. It shipped in 0.75.0 and reached an
# installed box (#377). Every virtio guest whose card reports a maximum below
# 9000 is in that class, which is most of the machines this product is first
# tried on.
#
# WHY NOTHING CAUGHT IT, which is the part this file is the answer to. The
# release gate boots the medium and installs it to a disk, and an installed box
# comes up in FIRST BOOT mode: no `dpdk` stanza at all, because the DPDK plugin
# is disabled until the kernel has reserved hugepages. The TUNED profile - the
# one with cpu pinning, hugepage-backed memory and the dpdk stanza - had never
# been handed to a real VPP by anything in this repository except
# appliance/test_tuned_conf.sh, which renders ONE shape and skips itself when
# the machine cannot spare the pages. A generated file whose consumer never
# reads it is a file we hope is right.
#
# WHY THE TEXT IS NOT CHECKED HERE, and this is the whole design. The only
# authority on what VPP accepts is VPP. A list of legal keywords kept in this
# repository would be a second copy of VPP's grammar, written by the same
# people who have just been shown inventing a keyword that reads exactly like a
# DPDK option and is not one - and it would go stale the first time VPP renamed
# anything. So every shape is given to the vpp binary and the answer is VPP's.
#
# WHAT IT ASKS, exactly, and what it does NOT. It asks whether VPP accepts the
# file: whether the parser complains, and whether VPP is still alive a few
# seconds later. It does not ask whether the box forwards, whether the workers
# landed on the cores the profile names, or whether a device was claimed -
# there is no NIC here to claim. Those are lab/install-iso.sh's, on a booted
# guest with a real card.
#
# WHY IT NEEDS HUGEPAGES. Measured: with `main-heap-page-size default-hugepage`
# and no pages reserved, VPP dies in EARLY config, before the dpdk config
# function runs, and the parse error is never printed - the whole run comes
# back `Segmentation fault` and says nothing about the keyword. So the pages
# have to be there for this check to reach the thing it is checking, and a run
# that cannot get them has measured nothing rather than passed.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }

SRC=${SRC:-/src}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# ---------------------------------------------------------------- hugepages
#
# 2 MB pages, because that is what a container can reserve and what
# `default-hugepage` resolves to here. The tuned profiles below are generated
# against synthetic roots whose only pool is the 2 MB one, so the file asks for
# the page size this machine actually has.
NR=/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
PAGES=${PAGES:-1800}
if [ ! -w "$NR" ]; then
  # THE PATH ON A ROW OF ITS OWN. It is 52 columns and the sentence around it
  # was 101 on the 80-column console this is read on (#367); the path is the
  # only thing here anybody goes and looks at, so it is what a fold must not
  # break.
  echo "  NOT MEASURED: this file is not writable, so VPP cannot be given"
  echo "                the pages the tuned profile asks for:"
  echo "                  $NR"
  echo "                Nothing was learned about the generated file. Run"
  echo "                this under --privileged."
  echo "1 NOT MEASURED"
  exit 0
fi
BEFORE=$(cat "$NR")
restore() { echo "$BEFORE" > "$NR" 2>/dev/null || true; rm -rf "$WORK"; }
trap restore EXIT
echo "$PAGES" > "$NR" 2>/dev/null || true
GOT=$(cat "$NR")
if [ "$GOT" -lt 1600 ]; then
  echo "  NOT MEASURED: asked for $PAGES 2M hugepages and got $GOT. The"
  echo "                tuned profile asks for a 2G heap and its buffers,"
  echo "                so VPP would die of the shortage rather than of"
  echo "                anything in the file."
  echo "1 NOT MEASURED"
  exit 0
fi
ok "reserved $GOT 2M hugepages, so VPP reaches the dpdk stanza"
mkdir -p /dev/hugepages /run/vpp /var/log/vpp /vpp_plugins
mountpoint -q /dev/hugepages \
  || mount -t hugetlbfs none /dev/hugepages 2>/dev/null

# The product, installed the way it installs. The generated file names
# bpa_plugin.so and linux_cp; a VPP that cannot find them says so, and an
# unrelated complaint on every shape is noise this check would have to learn
# to ignore.
(cd "$SRC/.build-vpp" && ninja >/dev/null 2>&1)
sh "$SRC/appliance/install.sh" >/dev/null 2>&1 || bad "install.sh failed"

# ----------------------------------------------------------- the fake boxes
#
# One directory per shape, in the layout hexarouting-tune reads: the CPU
# topology, the NUMA node, the pool that exists, what the kernel is isolating
# now and how much RAM the machine has. It is the same shape
# appliance/test_tune.py builds, in shell, because this file has to hand the
# result to a binary rather than to an assertion in the same process.
mk() { mkdir -p "$(dirname "$1")"; printf '%s\n' "$2" > "$1"; }

# mkroot <name> <ncpu> <isolated-now> <hugepages-gb-reserved> <ram-gb>
mkroot() {
  R=$WORK/$1; shift
  _n=$1; _iso=$2; _hp=$3; _ram=$4
  _i=0
  while [ "$_i" -lt "$_n" ]; do
    mk "$R/sys/devices/system/cpu/cpu$_i/topology/physical_package_id" 0
    mk "$R/sys/devices/system/cpu/cpu$_i/topology/core_id" "$_i"
    mk "$R/sys/devices/system/cpu/cpu$_i/topology/thread_siblings_list" "$_i"
    _i=$((_i + 1))
  done
  mk "$R/sys/devices/system/node/node0/cpulist" "0-$((_n - 1))"
  mk "$R/sys/devices/system/cpu/isolated" "$_iso"
  # ONLY the 2 MB pool exists, which is the box this check can actually run
  # against and is also the ordinary virtual machine: a guest whose CPU does
  # not expose pdpe1gb has no 1G pool at all (#290).
  mk "$R/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages" \
     "$((_hp * 512))"
  mk "$R/proc/meminfo" "MemTotal:       $((_ram * 1024 * 1024)) kB"
  echo "$R"
}

# addcard <root> <ifname> <pci> <tx-queues>
#
# Through a bus device, which is the shape of every virtio guest: `device`
# points at virtio3 and the PCI address is its parent (#286). The bare-metal
# shape is the easy one and is not the one that broke.
addcard() {
  _r=$1; _if=$2; _pci=$3; _q=$4
  mk "$_r/sys/devices/pci0000:00/$_pci/numa_node" 0
  mkdir -p "$_r/sys/devices/pci0000:00/$_pci/virtio3"
  mkdir -p "$_r/sys/class/net/$_if"
  ln -sf "../../devices/pci0000:00/$_pci/virtio3" \
         "$_r/sys/class/net/$_if/device"
  _i=0
  while [ "$_i" -lt "$_q" ]; do
    mkdir -p "$_r/sys/class/net/$_if/queues/tx-$_i"
    mkdir -p "$_r/sys/class/net/$_if/queues/rx-$_i"
    _i=$((_i + 1))
  done
}

# The card's maximum MTU is a netlink attribute and not a file under the root,
# so the tuner shells out to `ip` and HEXA_IP is the seam. Without a stub here
# the tuner would ask THIS machine's `ip` about a card that only exists in a
# tempdir - which is how the maximum below 9000 came to be a shape nothing had
# ever generated.
IPSTUB=$WORK/bin/ip
mkdir -p "$WORK/bin"
cat > "$IPSTUB" <<'STUB'
#!/bin/sh
[ -n "${STUB_MAXMTU:-}" ] \
  && echo "1: x: <BROADCAST> mtu 1500 maxmtu $STUB_MAXMTU"
exit 0
STUB
chmod +x "$IPSTUB"

# ----------------------------------------------------------------- the check
#
# TWO ANSWERS, because a refusal has two faces and only one of them is a
# sentence that can be reworded. VPP prints `unknown input` and then EXITS, so
# a shape it refuses comes back with the process gone; a shape it accepts is
# still running when the deadline arrives. Measured against the real binary:
# the refused file exits with status 0 in under a second and prints
# `dpdk_device_config: unknown input 'mtu 1500'`; the accepted one is killed by
# the timeout. Asserting only on the sentence would pass the day VPP rewords
# it; asserting only on the exit would not say WHICH keyword, which is the one
# thing whoever reads this needs.
SECS=${SECS:-12}
feed() {  # <shape name> <startup.conf>
  _name=$1; _conf=$2
  timeout "$SECS" vpp -c "$_conf" > "$WORK/$_name.log" 2>&1
  _rc=$?
  _why=$(grep -iE "unknown input|unknown parameter|parse error" \
           "$WORK/$_name.log" | head -1)
  if [ -n "$_why" ]; then
    bad "VPP refused the '$_name' profile:"
    echo "         $_why"
    echo "         the file is generated by hexarouting-tune; the keyword"
    echo "         above is not one this VPP has"
  elif [ "$_rc" != 124 ]; then
    # Not a parse complaint and VPP is gone anyway. That is still a file this
    # box cannot boot on, and saying "it parsed" about it would be the
    # narrower claim rather than the true one.
    bad "VPP exited on the '$_name' profile (status $_rc) with no parse"
    echo "         complaint. Last of its output:"
    tail -4 "$WORK/$_name.log" | sed 's/^/         | /'
  else
    ok "VPP accepted and kept running on the '$_name' profile"
  fi
  pkill -9 vpp 2>/dev/null || true
  sleep 1
}

# shape <name> <sysfs root> <maxmtu or empty> [extra tuner arguments...]
shape() {
  _name=$1; _root=$2; _mtu=$3; shift 3
  rm -f /etc/vpp/startup.conf
  PATH="$WORK/bin:$PATH" HEXA_IP="$IPSTUB" STUB_MAXMTU="$_mtu" \
    python3 "$SRC/appliance/hexarouting-tune" --sysfs-root "$_root" \
      --subscribers 200000 --write \
      --vpp-base "${BASE:-$SRC/appliance/etc/vpp-base.conf}" \
      --config "${CONF:-/nonexistent}" "$@" >/dev/null 2>&1 \
    || { bad "the tuner exited with an error on the '$_name' shape"; return; }
  [ -s /etc/vpp/startup.conf ] \
    || { bad "no startup.conf was written for the '$_name' shape"; return; }
  cp /etc/vpp/startup.conf "$WORK/$_name.conf"
  feed "$_name" "$WORK/$_name.conf"
}

echo
echo "=== every shape the tuner can write, given to the real VPP ==="

# 1. FIRST BOOT. Nothing reserved and nothing isolated, which is every box on
# the boot after an install. No dpdk stanza at all, 4k pages, no pinning.
R=$(mkroot firstboot 2 "" 0 8); addcard "$R" ens18 0000:00:12.0 1
shape firstboot "$R" 1500 --interface ens18 --keep-management
grep -q "FIRST BOOT MODE" "$WORK/firstboot.conf" \
  && ok "and it really is the conservative profile" \
  || bad "the 'firstboot' shape did not produce the conservative profile"

# 2. TUNED WITH NO CARD. The box has been rebooted into its tuning and the
# operator has named nothing, so the dataplane must claim NOTHING: this is the
# no-pci branch, and it is the one that keeps a box reachable (#123).
R=$(mkroot nopci 2 "1" 2 8)
shape nopci "$R" ""
grep -q "no-pci" "$WORK/nopci.conf" \
  && ok "and it claims no device at all" \
  || bad "the 'nopci' shape did not produce the no-pci branch"

# 3. TUNED WITH ONE CARD, the ordinary small virtual machine: one worker, one
# queue, one device named.
R=$(mkroot onecard 2 "1" 2 8); addcard "$R" ens18 0000:00:12.0 1
shape onecard "$R" 9000 --interface ens18 --keep-management
grep -q "dev 0000:00:12.0" "$WORK/onecard.conf" \
  && ok "and it names the device the operator chose" \
  || bad "the 'onecard' shape named no device"

# 4. A CARD WHOSE MAXIMUM MTU IS BELOW VPP'S DEFAULT. This is #377's box and
# the shape nothing had ever generated: the tuner reads the maximum off the
# card, and what it does with a maximum below 9000 is a decision that has to
# meet VPP's parser like every other.
R=$(mkroot lowmtu 2 "1" 2 8); addcard "$R" ens18 0000:00:12.0 1
shape lowmtu "$R" 1500 --interface ens18 --keep-management
# The decision, named. Without this the [ ok ] above is "VPP accepted a file
# that happens to say nothing about the MTU", which is exactly the state
# before #382 and is what this shape exists to stop being invisible.
if grep -q "default-mtu 1500" "$WORK/lowmtu.conf"; then
  ok "and it carries the card's maximum, so the line above is VPP"
  echo "         accepting the decision #382 made and not a file that is"
  echo "         silent about the MTU"
else
  bad "the 'lowmtu' shape wrote no default-mtu, so VPP accepting it says"
  echo "         nothing about a card that cannot do 9000"
fi

# 5. SEVERAL WORKERS AND SEVERAL QUEUES. corelist-workers with a range, and a
# queue count taken off the card rather than off the processor (#330).
R=$(mkroot manyworkers 8 "1-7" 4 16); addcard "$R" ens18 0000:00:12.0 4
shape manyworkers "$R" 9000 --interface ens18 --keep-management
grep -q "corelist-workers" "$WORK/manyworkers.conf" \
  && ok "and it pins workers to a list of cores" \
  || bad "the 'manyworkers' shape pinned nothing"

# 6. A BOX WITH ONE CORE. There is no worker to give, so the cpu stanza is a
# comment and nothing else - which is a stanza VPP still has to accept.
R=$(mkroot noworkers 1 "" 2 8); addcard "$R" ens18 0000:00:12.0 1
shape noworkers "$R" 9000 --interface ens18 --keep-management

# 7. A STATISTICS SEGMENT SIZED BY THE TUNER. It appears only when the box's
# own configuration names more interfaces than VPP's default segment holds
# (#288), so it is a stanza most boxes never see.
# Counted by hexash's own parser and not by a grep here, which is what the
# tuner does deliberately - so the fixture has to be a configuration that
# parser accepts. An `interface` block with no statement under it is not
# recorded as an interface at all, measured: `Config.parse` returns an empty
# interface list for it, and a fixture written that way would have produced no
# statseg stanza and blamed the tuner.
CONFFILE=$WORK/many.conf
: > "$CONFFILE"
i=0
while [ "$i" -lt 9000 ]; do
  printf 'interface Ethernet%s\n  mtu 1500\n' "$i" >> "$CONFFILE"
  i=$((i + 1))
done
R=$(mkroot statseg 2 "1" 2 8); addcard "$R" ens18 0000:00:12.0 1
CONF=$CONFFILE shape statseg "$R" 9000 --interface ens18 --keep-management
grep -q "^statseg {" "$WORK/statseg.conf" \
  && ok "and it sized the statistics segment for them" \
  || bad "the 'statseg' shape wrote no statseg stanza"

# 8. THE OPERATOR'S OWN BASE, carrying a statseg of their own. The tuner steps
# aside for it, and the result is still a file VPP has to accept.
OPBASE=$WORK/operator-base.conf
{ cat "$SRC/appliance/etc/vpp-base.conf"; echo; echo "statseg {"
  echo "  size 96M"; echo "}"; } > "$OPBASE"
R=$(mkroot opbase 2 "1" 2 8); addcard "$R" ens18 0000:00:12.0 1
BASE=$OPBASE CONF=$CONFFILE shape opbase "$R" 9000 \
  --interface ens18 --keep-management

# 9. TWO CARDS, one of them carrying nothing. The tuner picks the NUMA node
# off the named card and leaves the other alone, and a box with a spare card
# is the one the refusal in #123 is written for.
R=$(mkroot twocards 2 "1" 2 8)
addcard "$R" ens18 0000:00:12.0 1
addcard "$R" ens19 0000:00:13.0 2
shape twocards "$R" 9000 --interface ens19
grep -q "dev 0000:00:13.0" "$WORK/twocards.conf" \
  && ok "and it names the second card, not the first" \
  || bad "the 'twocards' shape named the wrong device"

echo
# One of the three verdict lines tools/run-tests.sh reads, so this file can
# join test/suites.list the day `make test` is allowed a container. The two
# NOT MEASURED exits above are the third: a run that could not reserve pages
# has learned nothing about the product, which is not a pass.
[ "$fail" = 0 ] && { echo "ALL TESTS PASSED"; exit 0; }
echo "$fail FAILURES"
exit 1
