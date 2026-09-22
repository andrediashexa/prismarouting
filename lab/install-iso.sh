#!/bin/sh
# Installs the ISO onto a blank disk, then boots that disk with the ISO gone.
#
# The second boot is the whole point. Everything before it only proves the
# installer ran; booting the disk with no ISO attached proves it produced a
# system that stands on its own - which is the thing the live image cannot do
# and the reason #33 exists.
#
# It also proves the part the live image can never reach: the installed system
# applies isolcpus and hugepages, so its self-test comes up OUT of first-boot
# mode. On the live ISO that path is dead code by construction.
#
# And the installed box's console is DRIVEN, not only read (#189). A role is
# only real if an account the product created on a box somebody RECEIVES can
# use it, and until this existed the only proof of one was lab/vty.sh: in a
# container, against a VPP the lab had started itself, with the memberships
# handed out by the lab. That is the shape of #99, #168, #182 and #184, and it
# cost three shipped defects in two days when the same assumption was made
# about VPP workers (#199, #200, #201).
#
#   sh tools/lab.sh install-iso                  the lab
#   sh tools/lab.sh install-iso wrong-password   the driver typing a password
#                                                the box will refuse
#   sh tools/lab.sh install-iso mirror           two disks, and one of them
#                                                pulled out afterwards
#
# The second is not a mode anybody wants; it is how this lab is shown to FAIL
# rather than hang when the box does not answer, which is the one failure a
# console driver has to get right.
#
# The third is the records mirror (#83). It installs onto TWO disks, boots the
# box, has the box write a record, then boots it again with the mirror disk
# GONE - which is the only way to find out what a machine does when a disk
# dies under it. The installer builds the array; this lab never does. A lab
# that creates the array itself and then checks an array exists has proved
# nothing about the installer, which is the shape of #99, #119 and #184.
set -u

# HOW LONG THE INSTALL TOOK (#408). This lab's own header records 687-692 s
# from three runs, in a comment - numbers somebody read off a terminal once
# and wrote down. Printing it with the verdict makes EVERY run say it, which
# is what docs/build-timing.md had to estimate and what #396's phase-0
# baseline needs: a container cannot be shown to have cost nothing against a
# before that was never recorded.
#
# On both exits, because this lab has two and a duration printed on one of
# them is a duration missing exactly when the run went wrong.
LAB_START=$(date +%s)
ISO=${ISO:-/src/$(sh /src/tools/version-stamp.sh --filename /src)}
DISK=${DISK:-/tmp/prisma-disk.qcow2}
# 40G, not 8G. The installer reserves 8 GB for the system before it will give
# any of the disk to the log volume (#83), so an 8 GB disk is one the
# installer correctly refuses - it found that out by refusing this lab. qcow2
# is sparse: a 40 GB disk that holds 3 GB costs 3 GB.
#
# It is also closer to honest. An appliance nobody would deploy on 8 GB is not
# a useful thing to prove installs on 8 GB.
SIZE=${DISK_SIZE:-40G}
LOG=${LOGCOPY:-/src/install-console.log}

# The answers the installer must ask for. They are given here rather than typed
# at the prompt because a test that sends keystrokes at the right moment tests
# the timing of the keystrokes - and because the point of the assertions below
# is that these exact values reach the disk.
NEW_HOST=${NEW_HOST:-cgnat-lab}
NEW_TZ=${NEW_TZ:-America/Sao_Paulo}
NEW_PW=${NEW_PW:-lab-secret-42}

# The accounts the VTY is made to create on the installed box, and what the
# driver types to log in as them.
#
# The password is a crypt HASH because that is what the `user` block takes: a
# plaintext one lands in /etc/shadow and simply never matches, so the VTY
# refuses it. The salt is fixed so two runs put the same line on the console
# and a diff between two consoles is a real difference.
OP_USER=${OP_USER:-netadm}
OP_PW=${OP_PW:-op-secret-42}
RO_USER=${RO_USER:-lookonly}
RO_PW=${RO_PW:-ro-secret-42}
crypt_hash() {  # <plaintext> -> md5-crypt hash, as `user ... password` takes
  # md5-crypt and not sha512, which is what the VTY's own refusal suggests. An
  # sha512 hash is about 106 characters: typed at an 80-column serial console
  # it wraps, and readline redraws the line around the wrap, so what the
  # console log holds is the hash with escape sequences through the middle of
  # it. Nothing breaks - the box gets the characters - but the artefact this
  # lab leaves behind stops being readable, and that artefact is the evidence.
  #
  # crypt goes away in Python 3.13; the container is Debian 12 (3.11), the same
  # note the password check below carries.
  python3 -W ignore -c 'import crypt,sys; print(crypt.crypt(sys.argv[1], "$1$hexalab"))' "$1"
}
OP_HASH=$(crypt_hash "$OP_PW")
RO_HASH=$(crypt_hash "$RO_PW")

# The account a "commit confirmed" creates and never confirms (#212). It is an
# account and not a pool because creating one is what `touches_access` means:
# it is the change this VTY turns into a confirmed commit whether or not the
# operator asked, which is how the gap was found in the first place.
TMP_USER=${TMP_USER:-nevercnf}
TMP_HASH=$(crypt_hash "tmp-secret-42")

# The password the driver TYPES at the console, which is the answer the
# installer was given and is deliberately a SECOND name for it. Making them
# differ is the only way to see what this lab does when the box answers and
# refuses - and a console driver whose failure mode is a hang is not finished.
LOGIN_PW=$NEW_PW
MODE=one-disk
case "${1:-}" in
  "") ;;
  wrong-password)
    LOGIN_PW="not-$NEW_PW"
    echo "running with a root password the box will REFUSE: this lab MUST"
    echo "fail, and it must say which wait timed out and why" ;;
  mirror)
    MODE=mirror
    echo "installing onto TWO disks and then pulling one out" ;;
  *)
    echo "usage: install-iso.sh [wrong-password|mirror]" >&2; exit 1 ;;
esac

# The second disk, in mirror mode. 4 GB against 2 GB of records: the installer
# refuses a mirror disk that cannot hold the records it was told to keep, and a
# leg the same size as the answer to the megabyte is an array one megabyte
# short of it.
DISK2=${DISK2:-/tmp/prisma-disk2.qcow2}
DISK2_SIZE=${DISK2_SIZE:-4G}
MIRROR_DEV=/dev/vdb

# ------------------------------------------------- reaching the box (#303)
#
# THE ONE THING THIS LAB COULD NOT DO, and the reason a box with no management
# network passed the release gate for the whole life of the product: every
# assertion in this file is driven down the SERIAL CONSOLE. A box with no
# address behaves identically down a serial line to a box with one, so the
# missing network was invisible here by construction - and an assertion that
# reads the box's own words about its address ("ip a" typed at a shell, a line
# the self-test printed) would pass on a box nobody can reach, which is the
# exact shape of check this repository keeps finding and throwing away.
#
# So the lab opens a TCP connection to the box's sshd FROM OUTSIDE, over the
# network, and reads the banner the box sends back. qemu's user-mode
# networking runs a DHCP server handing the guest 10.0.2.15, and hostfwd
# forwards a port on this host to that address inside the guest: the
# connection completes only if the guest really holds the address, with the
# interface up and sshd listening on it. Nothing is typed at the console for
# it and the box is never asked.
#
# WHAT IT PROVES AND WHAT IT DOES NOT, said here rather than left to be
# assumed. The host side of a forwarded port is libslirp, a userspace TCP
# stack, so this is not a packet over a wire and says nothing about any real
# NIC driver. The GUEST side is real: slirp sends a SYN to the address its own
# DHCP server handed out, inside the guest's virtual network, and the box must
# hold that address, answer for it and have sshd listening on it for a byte to
# come back. So it proves the box took the lease, configured the card and is
# answering on it, which is the whole of what #303 is about, and it proves
# nothing about 10-gigabit optics.
#
# NOR ABOUT ROUTING. The peer the box sees is 10.0.2.2, on the guest's own
# wire, so a box with no default route at all answers this probe (#378
# shipped that box and this stayed green). That is gap 3 of #383, written up
# under MGMT_NET below with what would make a banner here mean the box can
# route.
#
# ONE card, explicitly, at a FIXED PCI address, on every boot. qemu adds a
# default NIC when told nothing, which is how this lab has been running - and
# a card that changes model or slot between the install and the boot changes
# its predictable NAME, so the box would come up looking for a card that is
# not there and this lab would be measuring its own inconsistency. The box is
# then the same shape as the one in #303: a machine with a single NIC.
SSH_PORT=${SSH_PORT:-2222}
# host_mtu=1500 IS NOT COSMETIC, and leaving it off cost a release.
#
# Without it qemu does not negotiate VIRTIO_NET_F_MTU and the guest reports a
# maximum of 65535. Every hypervisor an operator actually runs this on sets it:
# the box that found #380 is a Proxmox guest and its ens18 reports maxmtu 1500.
#
# The tuner only writes anything about MTU when the card's maximum is below
# VPP's default of 9000, so with the qemu default that branch was unreachable
# HERE and reachable on every real VM. v0.75.0 shipped `mtu <n>` into the dpdk
# stanza, VPP has no such keyword and refuses the whole file, and this lab -
# which boots the installed disk with the card claimed, in PHASE 5, exactly
# where it would have seen it - passed, because its card advertised a maximum
# no real one has.
#
# So the card is given the maximum a real one has. A guest that is easier than
# the machines this product runs on is a guest that answers a different
# question.
#
# ONE HOLDER, because PHASE 5 now asserts against it. The maximum this lab
# gives its card and the maximum it expects the dataplane to come up at are
# the same number, and two copies of it would drift the first time somebody
# changed the qemu line - leaving an assertion that compares the box against
# a number nothing on the box ever had.
MGMT_MAXMTU=${MGMT_MAXMTU:-1500}
# WITH A VIRTUAL IOMMU the virtio device has to be told to use it: a guest
# driver behind an IOMMU programs addresses the device must translate, and a
# virtio-net that was not given iommu_platform does not - so DMA goes to
# addresses nothing mapped, silently, and the card carries no traffic at all.
# Measured: without these two the box with an IOMMU came up with its address
# and could not ping its own gateway (#383).
IOMMU_NET=""
[ "${BOOT_IOMMU:-1}" = 1 ] && IOMMU_NET=",iommu_platform=on,ats=on,disable-legacy=on"
MGMT_NET="-netdev user,id=mgmt,hostfwd=tcp:127.0.0.1:$SSH_PORT-:22 \
  -device virtio-net-pci,netdev=mgmt,addr=0x9,host_mtu=$MGMT_MAXMTU$IOMMU_NET"

# ------------------------------- the guest against a real box, again (#383)
#
# host_mtu above is the first property that rule cost a release to learn: a
# guest that is easier than the machines this product runs on is a guest
# that answers a different question. Three more remain, measured against the
# box that reported #380 (8 CPUs, isolcpus=1-7, intel_iommu=on iommu=pt, a
# gateway it reaches by a route), and each has already hidden a defect an
# operator then found:
#
#   property   this guest                       a real box
#   CPUs       2, so ONE worker                 8, so seven
#   IOMMU      none, so uio_pci_generic         present, so vfio-pci
#   gateway    10.0.2.2, on the guest's wire    off-link, reached by a route
#
# NOTHING IS CHANGED HERE YET. Each one costs something - gate wall-clock, a
# machine type, a probe that has to mean more - and the release-gate runner
# measures each cost alone, one lab at a time, before any option moves. Per
# gap, below: what closes it, what that touches, the measurement, and two
# paragraphs of which exactly ONE stays next to the option once the numbers
# are in. The other is deleted with them.
#
# 1. -smp 2, on all four qemu lines in this file: the PHASE 1 install,
#    boot_vm(), the mirror reinstall and the one-disk boot.
#
#    Driven offline with the real tuner (appliance/hexarouting-tune
#    --sysfs-root over a root of this guest's shape, built the way
#    appliance/test_tune.py builds one: a virtio card with ONE queue, the
#    DHCP stanza reserving it, five pages reserved). -smp "$BOOT_SMP" gives
#    `isolcpus=1`, `corelist-workers 1`, `buffers-per-numa 16384` and no cap
#    of any kind. -smp 8 gives `isolcpus=1-7`, `corelist-workers 1-7`,
#    `buffers-per-numa 114688`, `NIC queues, ens9: 1, and this box has 7
#    worker(s). Capped at 1` - the half of #330 that was a real -22 - and,
#    if MemTotal floors to 7 GB, `WARNING: 6 GB of hugepages were needed ...
#    Capped at 5 GB`, which is the reporting box's own hugepages=5; at 8 GB
#    the tuner asks for six pages instead, and the kernel command line on
#    the console says which this guest is. With the pool the driven session
#    creates (`cgnat pool lab-a`), the per-worker partition (#210) and the
#    reserve-per-worker warning (#372, src/plugins/bpa/bpa.c) then run on a
#    booted box for the first time. None of those lines has ever been on
#    this console: appliance/test_startup_conf_shapes.sh proves they are
#    GENERATED at eight cores, not that a box boots with them.
#
#    What closes it: -smp 8,sockets=1,cores=8,threads=1, spelled out because
#    qemu's default topology flipped from sockets to cores in 6.2 and the
#    tuner reads sibling lists. THE SAME NUMBER ON ALL FOUR LINES, in one
#    holder: the install runs apply-tuning in the chroot ("boot tuning" in
#    appliance/hexarouting-install), so the isolcpus the disk first boots
#    with is computed from the INSTALL boot's CPUs, and an eight-CPU boot
#    over a two-CPU install is a box reporting its own tuning as requested
#    and not in effect.
#
#    What it risks: seven poll-mode workers at 100% for the length of every
#    disk boot, on a runner this lab shares with nothing (nproc answers 32
#    on the machine this was written on); the wrong-password control boots
#    once more at that cost; without KVM, eight vCPUs under TCG on a path
#    this file already marks slow. lab/boot-iso.sh keeps -smp "$BOOT_SMP" either way:
#    the live medium never leaves first-boot mode, so a worker has nothing
#    to show there.
#
#    Measured by: `time sh tools/lab.sh install-iso` at 2 and again at 8,
#    alone on the machine, and install-console.log read for
#    "corelist-workers 1-7", "dataplane workers hold the cores the profile
#    names", "Capped at 1" and "hugepages=" on the kernel command line. The
#    cost is the difference in wall-clock; the benefit is those lines.
#
#    IF THE GUEST GETS IT, the line at the option reads: eight CPUs because
#      the reporting box has eight; a two-CPU guest gives the dataplane one
#      worker, and with one worker the queue cap, the pinning, the
#      per-worker partition and the reserve warning never run on a booted
#      box. Measured: <N> s at 2 against <M> s at 8, on <date>.
#    IF THE GAP STAYS: two CPUs, and a real box has eight. This guest gets
#      ONE worker, so the queue cap, corelist-workers, the per-worker
#      partition and the reserve warning are proved as generated files
#      (appliance/test_startup_conf_shapes.sh) and never as a booted box.
#      Eight cost <M> s over <N> s per gate, measured <date>, which is why
#      it stays 2. A green run here says nothing about a box with more than
#      one worker.
#
# 2. No IOMMU. bind-dataplane-nic decides on exactly one thing: whether
#    /sys/kernel/iommu_groups has an entry. Driven offline through
#    HEXA_IOMMU_GROUPS, a directory that is absent or empty answers
#    "uio_pci_generic without IOMMU protection" and one with a single group
#    answers "vfio-pci through the IOMMU". A qemu guest has no groups, so
#    this file has only ever measured the fallback, and the path the
#    product prefers has never once been taken here.
#
#    What closes it is four parts, each read in its source (qemu 7.2 and
#    Linux 6.1, which are what the runner and the medium carry):
#      -machine q35,kernel-irqchip=split
#        The DMAR table is built by build_dmar_q35() in hw/i386/acpi-build.c
#        and by nothing else, so the vIOMMU is a q35 device; and interrupt
#        remapping refuses kernel-irqchip=on outright (hw/i386/x86-iommu.c,
#        "please use 'split|off'"). Without remapping the guest's
#        vfio_iommu_type1 returns -EPERM for every group unless
#        allow_unsafe_interrupts=1 (drivers/vfio/vfio_iommu_type1.c), a
#        module option this product does not ship.
#      -device intel-iommu,intremap=on,aw-bits=48
#        intremap defaults on under a split irqchip and is said anyway.
#        aw-bits defaults to 39; a real Xeon reports 48, and DPDK reads the
#        width off the IOMMU's cap register to choose IOVA as VA against PA,
#        so the VPP journal says which mode it took. caching-mode stays off:
#        it is for vhost and host-assigned devices, and this card is neither.
#      ,disable-legacy=on,iommu_platform=on on the virtio-net-pci line above
#        iommu_platform=on is what sends the card's DMA through the vIOMMU;
#        without it the device bypasses translation and a driver that
#        programs IOVAs corrupts memory. qemu refuses it on a transitional
#        device ("VIRTIO_F_IOMMU_PLATFORM was supported by neither legacy
#        nor transitional device", hw/virtio/virtio-pci.c), and on q35's
#        root bus disable-legacy defaults OFF, because pcie_port there is
#        `pci_bus_is_express && !pci_bus_is_root`. DPDK's virtio PMD
#        negotiates VIRTIO_F_IOMMU_PLATFORM and VERSION_1
#        (drivers/net/virtio/virtio_ethdev.h), so a modern-only card is one
#        it drives.
#      intel_iommu=on iommu=pt on the guest's kernel command line
#        Already there: hexarouting-tune writes both into grub on every
#        installed box, which is why the tuning readback says "the kernel
#        exposes no IOMMU" on this guest today and would say "the kernel
#        exposes dmar0" with the device present.
#
#    What it touches: MGMT_NET (the two device options) and the four qemu
#    lines - the machine type on ALL of them, so the card keeps one
#    predictable name from the install to the last boot, and the IOMMU
#    device on whichever boots are to take vfio-pci. Which boots is the
#    design question: with the device on EVERY boot the fallback stops
#    being measured, and the fallback is the path most hypervisors put an
#    operator on. One shape measures both binds on one disk: PHASE 2's boot
#    without it, so the claim-at-boot block reads uio_pci_generic as it does
#    today, and boot_vm() with it, so PHASE 3, 4 and 5 read vfio-pci and
#    PHASE 5's port-state and MTU assertions judge that bind. Every driver
#    reading in this file already accepts either name.
#
#    What it risks: q35 puts the medium on AHCI instead of IDE, so live-boot
#    has to find it there; the card's predictable name may change under
#    q35, which this file reads off the box and never assumes (#161) but
#    docs/ and CHANGELOG quote as ens9 in measurements; a split irqchip
#    costs an exit per IOAPIC interrupt; DPDK under a 39-bit vIOMMU may
#    choose IOVA as PA, which works as root and is a journal line to read
#    rather than a failure. A vfio bind that fails leaves the only card on
#    no driver at all, so the red is the box unreachable in PHASE 2 with
#    "did NOT bind to vfio-pci" on its console.
#
#    Measured by: one run with those options on every boot, read for
#    "vfio-pci" in the `-- cards:` line, "dmar0" in the tuning readback, no
#    port in error state, and the box answering from outside with the card
#    on vfio-pci; then the wall-clock against the run at 2 CPUs above.
#
#    IF THE GUEST GETS IT, the line at the option reads: a q35 machine with
#      an IOMMU because every real box this product is installed on has
#      one, and bind-dataplane-nic takes vfio-pci wherever a group exists.
#      Boots without the device measure the fallback; boots with it measure
#      the path the product prefers. Measured: vfio-pci bound, port up at
#      1500, answered from outside, <N> s, on <date>.
#    IF THE GAP STAYS: no IOMMU, so this box binds uio_pci_generic on every
#      boot and the vfio-pci path, the one the product prefers because it
#      takes the device through the IOMMU, is never taken here. It was
#      tried on <date>: <what broke, or what it cost>. A green run here
#      says nothing about a box with VT-d on.
#
# 3. The gateway is on the guest's own wire. slirp's DHCP hands the guest
#    10.0.2.15/24 with 10.0.2.2 as the router, and 10.0.2.2 is also where a
#    forwarded connection comes FROM: libslirp 4.7.0, src/socket.c,
#    sotranslate_accept() rewrites the peer to vhost_addr when the host side
#    connected from INADDR_ANY or loopback, and leaves any other address
#    alone. reachable_over_the_network() below connects to 127.0.0.1, so
#    the SYN the box sees is from 10.0.2.2, on-link, and a box with NO
#    default route answers it. #378 shipped exactly that box and this probe
#    stayed green; a different reading caught it.
#
#    What closes it is not the gateway: slirp cannot put its router
#    off-link, the DHCP router option is always vhost_addr. What CAN move
#    is the peer. Bind the forward on this container's own address instead
#    of loopback (hostfwd=tcp:<addr>:$SSH_PORT-:22, the address read off
#    eth0 at run time) and have the probe connect to that address: slirp
#    then keeps 172.17.x.y as the source, which is off the guest's /24, so
#    the SYN-ACK needs the default route and a box without one is silent.
#    Same probe, same banner, same wall-clock; only what a green MEANS
#    changes, and it then means the box can route.
#
#    What it touches: MGMT_NET's hostfwd, reachable_over_the_network(), and
#    the two readings taken through it - BOOT_REACHED in PHASE 2, which the
#    claim-at-boot block reuses, and RB_REACHED in PHASE 3, which PHASE 4
#    and 5 stand on. Each of them then also proves the way back #378
#    computes carries a gateway the box can use.
#
#    What it risks: the container's address is docker's to choose, so it is
#    read and never written down; a guest whose rp_filter is strict drops an
#    off-link SYN it has no route back to, which is the same red one step
#    earlier; nothing outside this file uses the forwarded port.
#
#    Measured by: boot the installed disk, `ip route del default` at its
#    console, and run the probe both ways. Today's shape MUST still answer
#    with the banner, which is the gap; the off-link shape MUST time out,
#    and MUST answer again after `ip route add default via 10.0.2.2`.
#
#    IF THE GUEST GETS IT, the line at the probe reads: the peer is this
#      container's own address and not loopback, on purpose. slirp keeps a
#      non-loopback source, so the SYN arrives from off the guest's subnet
#      and a box with no default route cannot answer it; a banner here
#      proves the box routes, which is what an operator's gateway asks of
#      it. Measured <date>: silent with the route deleted, a banner with it
#      back.
#    IF THE GAP STAYS: a banner here proves the box holds its address and
#      sshd is listening. It does NOT prove the box can reach anything off
#      its subnet, because the peer is 10.0.2.2 on the guest's own wire and
#      needs no route; the default-route reading below is the only thing in
#      this file that does. Measured <date>: the banner, with no default
#      route at all.

# ============ WHAT THE MEASUREMENTS SAID, AND WHAT THIS GUEST NOW IS =========
#
# Taken 2026-09-18 on the machine that cuts the releases (32 cores, KVM),
# against the published prisma-routing-0.79.0.iso, one run at a time:
#
#   guest                       result       wall clock
#   -smp 2, no IOMMU            LAB PASSED        687 s
#   -smp 8, no IOMMU            LAB PASSED          -
#   -smp 8 with an IOMMU        LAB PASSED        692 s
#
# BOTH GAPS ARE CLOSED, and the wall-clock argument for keeping the easier
# guest turned out to be five seconds - under one per cent of a run.
#
#   * -smp 8 gives isolcpus=1-7, seven workers holding the cores the profile
#     names and a control plane on one CPU. Everything above that needed more
#     than one worker now runs on a booted box.
#   * the IOMMU gives 0000:00:09.0:vfio-pci on the installed box, so the bind
#     this product prefers is measured end to end for the first time.
#
# TWO THINGS THE RUNS TAUGHT THAT THE READING ABOVE COULD NOT:
#
#   * qemu REFUSES -device intel-iommu on the default machine ("Parameter
#     'driver' expects a dynamic sysbus device type for the machine") and
#     refuses intremap with the default irqchip ("Interrupt Remapping cannot
#     work with kernel-irqchip=on, please use 'split|off'"). A run that passes
#     the device without q35 and kernel-irqchip=split is a run with no IOMMU
#     in it, whatever its command line says - and it passes, which is worse.
#   * without iommu_platform on the card, the box with an IOMMU came up
#     holding its address and could not ping its own gateway: four checks red,
#     and every one of them a failure of this lab rather than of the product.
#     With the card told to use the IOMMU, the same run is green.
#
# The card's predictable name is enp0s9 under q35 where it was ens9; nothing
# in this file assumes either, which is #161's rule paying for itself, but
# docs/ and CHANGELOG quote ens9 in measurements taken before this.

# Does the box answer on the network? <seconds> to keep trying.
#
# The SSH BANNER and not a completed connection. libslirp accepts the host
# side of a forwarded port before it knows whether anything inside the guest
# will answer, so "the connect() returned" is not evidence: what proves a
# round trip to the box is bytes the box itself sent. An unreachable guest
# gives an empty read and this keeps trying until the deadline, which is also
# what an sshd that has not started yet looks like.
#
# python3, because this lab already needs it for the password hash and because
# /bin/sh here has no /dev/tcp. Nothing is authenticated: whether an account
# can log in is proved at the console above, and what is being asked here is
# only whether the box is on the network at all.
reachable_over_the_network() {   # <seconds>; prints the banner, or the reason
  python3 - "$SSH_PORT" "$1" <<'PYPROBE'
import socket, sys, time
port, secs = int(sys.argv[1]), float(sys.argv[2])
deadline = time.time() + secs
last = "nothing answered on the forwarded port"
while time.time() < deadline:
    try:
        s = socket.create_connection(("127.0.0.1", port), 5)
        s.settimeout(5)
        data = s.recv(128)
        s.close()
        if data.startswith(b"SSH-"):
            print(data.split(b"\r")[0].split(b"\n")[0].decode("ascii", "replace"))
            sys.exit(0)
        last = "the connection was accepted and the box sent nothing back"
    except OSError as e:
        last = str(e)
    time.sleep(2)
print(last)
sys.exit(1)
PYPROBE
}

[ -f "$ISO" ] || { echo "no ISO at $ISO"; exit 1; }
fail=0

# THE TWO GAPS ABOVE, AS VARIABLES (#383). The block beside MGMT_NET says
# what each hides and what would close it; these are what let the question be
# ASKED - run the lab with them and read the result - rather than argued. What
# the default is, and why, is written at the end of that block from the
# numbers those runs produced.
# EIGHT, AND THE NUMBER IS MEASURED. Two gave the box one worker, so the
# queue cap, corelist-workers and its pinning, and the per-worker block
# partition were generated in this lab and never run. Eight gives
# `isolcpus=1-7`, seven workers holding the cores the profile names, and a
# control plane on one CPU - the shape of the box that reported #380.
#
# What it costs, timed back to back on the same host, same ISO, KVM:
#   -smp 2, no IOMMU          687 s   LAB PASSED
#   -smp 8 with an IOMMU      692 s   LAB PASSED
# Five seconds, under one per cent. The argument for keeping the easier guest
# was wall-clock on every release, and the wall-clock is not there.
#
# A host with fewer than eight cores runs this over-committed and slower; the
# guest is the same shape either way, which is the property worth keeping.
BOOT_SMP=${BOOT_SMP:-8}
IOMMU_DEV=""
IOMMU_ARGS=""
# AND AN IOMMU BY DEFAULT, for the same reason and with the same measurement.
# Without one, bind-dataplane-nic always falls back to uio_pci_generic, so
# vfio-pci - the path this product PREFERS, because the device goes through
# the IOMMU and a bug in the dataplane cannot write to memory it was never
# given - was the path nothing ever measured. With one, the installed box
# binds `0000:00:09.0:vfio-pci` and every check in this file still passes.
if [ "${BOOT_IOMMU:-1}" = 1 ]; then
  # THE THREE PARTS, each measured rather than guessed at (#383):
  #   * -machine q35: the default machine refuses the device outright -
  #     "Parameter 'driver' expects a dynamic sysbus device type for the
  #     machine". A run that passes the device to the default machine is a run
  #     with no IOMMU in it at all, whatever the command line says.
  #   * kernel-irqchip=split: with the default, qemu refuses intremap -
  #     "Interrupt Remapping cannot work with kernel-irqchip=on".
  #   * caching-mode=on: without it the guest's driver never sees the mappings
  #     qemu makes.
  IOMMU_DEV="-machine q35,kernel-irqchip=split \
    -device intel-iommu,intremap=on,caching-mode=on"
  IOMMU_ARGS="intel_iommu=on iommu=pt"
fi

if [ -w /dev/kvm ]; then
  ACCEL="-enable-kvm -cpu host"
  echo "accelerating with KVM"
else
  ACCEL="-cpu ${BOOT_CPU:-Nehalem}"
  echo "no KVM: emulating with ${BOOT_CPU:-Nehalem} (slow)"
fi
# -smp "$BOOT_SMP" on the four qemu lines below, and no -machine, so qemu's i440fx:
# both are gaps 1 and 2 of #383, written up under MGMT_NET above, and
# neither moves until the measurements listed there are in.

mkdir -p /mnt/iso /tmp/bf
mount -o loop,ro "$ISO" /mnt/iso
cp /mnt/iso/live/vmlinuz /mnt/iso/live/initrd /tmp/bf/
# The live image must carry no usable credentials. Read its /etc/shadow out of
# the squashfs before anything else touches it - this is the file that used to
# travel onto the disk with a published password in it.
#
# The WHOLE squashfs is unpacked, not just that one file. /etc/shadow was the
# credential this lab knew about; it is not the only one an image can carry,
# and the rule in docs/appliance.md is about all of them - "a credential in a
# published image is the defect the locked live root account exists to
# prevent". Unpacking the lot costs about 2.5 seconds and a gigabyte of the
# runner, against an install that boots two virtual machines.
rm -rf /tmp/sq
SHADOW_READ=1
if ! unsquashfs -d /tmp/sq -no-progress /mnt/iso/live/filesystem.squashfs \
       > /tmp/unsq.log 2>&1; then
  echo "  could not unpack the live image:"
  tail -3 /tmp/unsq.log | sed 's/^/    /'
  SHADOW_READ=0
fi
LIVE_ROOT=$(grep "^root:" /tmp/sq/etc/shadow 2>/dev/null | cut -d: -f2)

# THE ASSERTION ON THE ARTEFACT (#245).
#
# test/test_no_credentials.sh proves this script refuses, in milliseconds,
# against roots it made up. Only this proves the image that was actually built
# went through it: the fast suite cannot tell a build that runs the refusal
# from one that quietly stopped calling it, because it never reads an ISO.
#
# Both trees, and the second is not the first. The squashfs is what lands on
# the disk; /mnt/iso is the root of the MEDIUM, which is where
# "prisma-routing.answers" with no argument reads
# /run/live/medium/prisma-routing.answers from.
#
# An unread image is not a clean image - the same argument the /etc/shadow
# check below makes, and for the same reason: the moment this matters is the
# moment nobody is looking.
echo "=========== the image carries no credential ==========="
if [ "$SHADOW_READ" != 1 ]; then
  echo "  [FAIL] the live image could not be unpacked, so nothing here proves"
  echo "         what it carries"
  fail=1
elif sh /src/appliance/no-credentials-in-image /tmp/sq /mnt/iso; then
  echo "  [ ok ] no answer file, no private key and no usable password hash"
  echo "         in the squashfs or on the medium"
else
  echo "  [FAIL] the ISO that was just built carries a credential, above."
  echo "         An ISO is published: it goes on a USB stick and onto a"
  echo "         download page. See docs/appliance.md and #245."
  fail=1
fi
# The host keys are the case this found: every released ISO carried the build
# container's own set. The image must have none, and phase 2 checks that the
# installed box grew its own.
LIVE_HOSTKEYS=$(ls /tmp/sq/etc/ssh/ssh_host_*_key 2>/dev/null | wc -l)
if [ "$SHADOW_READ" = 1 ] && [ "$LIVE_HOSTKEYS" != 0 ]; then
  echo "  [FAIL] the image carries $LIVE_HOSTKEYS ssh host private key(s)"
  fail=1
fi

# Given back before the install starts: a gigabyte of unpacked root on the same
# runner that is about to hold two qcow2 disks.
rm -rf /tmp/sq
umount /mnt/iso
echo "live image root entry: '${LIVE_ROOT:-<unreadable>}'"

rm -f "$DISK"
qemu-img create -f qcow2 "$DISK" "$SIZE" >/dev/null

# The second disk exists only in mirror mode, and it is BLANK. Everything on
# it after this point was put there by the installer.
MIRROR_DRIVE=""
MIRROR_ANSWER=""
if [ "$MODE" = mirror ]; then
  rm -f "$DISK2"
  qemu-img create -f qcow2 "$DISK2" "$DISK2_SIZE" >/dev/null
  MIRROR_DRIVE="-drive file=$DISK2,format=qcow2,if=virtio"
  MIRROR_ANSWER="prisma-routing.mirror=$MIRROR_DEV"
fi

echo "=========== PHASE 1: install onto a blank disk ==========="
# prisma-routing.yes runs it unattended. An interactive installer driven by
# keystrokes timed against a boot would be testing the timing.
#
# prisma-routing.mgmt-address=dhcp IS ASKED FOR, and since #394 it has to be: an
# unattended install that says nothing gets no management network, because a
# new box is configured at its console and every card it has is the
# dataplane's. This lab is the other case - it reaches the installed box over
# ssh, from outside, which is the one assertion the serial console cannot make
# - so it asks for an address and names no card, and the installer takes the
# card this medium is reaching the network through. The card is still not
# spelled out here, for the reason #161 gives: what predictable naming calls
# qemu's card is the machine's business and not this lab's.
# shellcheck disable=SC2086
timeout "${INSTALL_SECS:-600}" qemu-system-x86_64 \
  $ACCEL $IOMMU_DEV -m "${BOOT_MEM:-8192}" -smp "$BOOT_SMP" -nographic -no-reboot \
  -cdrom "$ISO" -kernel /tmp/bf/vmlinuz -initrd /tmp/bf/initrd \
  -drive file="$DISK",format=qcow2,if=virtio $MIRROR_DRIVE $MGMT_NET \
  -append "boot=live components console=ttyS0,115200 systemd.show_status=1 $IOMMU_ARGS \
           prisma-routing.install=/dev/vda prisma-routing.yes \
           prisma-routing.mgmt-address=dhcp \
           prisma-routing.log-gb=2 $MIRROR_ANSWER \
           prisma-routing.hostname=$NEW_HOST prisma-routing.timezone=$NEW_TZ \
           prisma-routing.password=$NEW_PW" \
  > /tmp/install.log 2>&1

sed 's/\x1b\[[0-9;]*m//g' /tmp/install.log > /tmp/install-clean.log
cp /tmp/install-clean.log "$LOG" 2>/dev/null || true
sed -n '/install to disk/,/installed on/p' /tmp/install-clean.log | head -30

if ! grep -q "Prisma Routing installed to /dev/vda" /tmp/install-clean.log; then
  echo
  echo "=========== THE INSTALL DID NOT FINISH ==========="
  # systemd skipping the unit looks nothing like the installer failing, and
  # the first version of this test could not tell them apart: both produced an
  # empty section and the same message. It cost a full build cycle.
  if grep -q "Ordering cycle found" /tmp/install-clean.log; then
    echo "  systemd dropped the installer unit to break an ordering cycle:"
    grep -i "ordering cycle" /tmp/install-clean.log | tail -3 | sed 's/^/    /'
  elif grep -qE "prisma-routing-inst|install to disk" /tmp/install-clean.log; then
    # "prisma-routing-inst", not "prisma-routing-installer": systemd truncates a unit
    # description on the status line - "Starting prisma-routing-insta...". Looking
    # for the full name never matched, so a unit that HAD started was reported
    # as never having started, and the next person to read that went and
    # checked ConditionKernelCommandLine, which was correct all along.
    if grep -q "install to disk" /tmp/install-clean.log; then
      echo "  the unit ran; what it printed:"
      grep -A6 "install to disk" /tmp/install-clean.log | tail -12 | sed 's/^/    /'
    else
      echo "  the unit STARTED and printed nothing."
      echo "  Something else owns the console: the installer writes to"
      echo "  /dev/console, and a getty that reached it first takes the"
      echo "  screen with a login prompt. Check the ordering between"
      echo "  getty@/serial-getty@ and prisma-routing-installer.service."
      grep -nE "Started (serial-)?getty|Starting prisma-routing-inst" \
        /tmp/install-clean.log | tail -4 | sed 's/^/    /'
    fi
  else
    echo "  the installer unit never started at all - check"
    echo "  ConditionKernelCommandLine against the -append line."
  fi
  if grep -q "Prisma Routing inst" /tmp/install-clean.log; then
    echo "  NOTE: the marker line is present but truncated, so the install"
    echo "  finished and the machine was killed before it could say so."
  fi
  grep -iE "error:|failed|not confirmed|not a block" /tmp/install-clean.log \
    | tail -8 | sed 's/^/  /'
  echo "--- full console in $LOG ---"
  exit 1
fi
echo "  install reported success"

# It verified the disk before it said that (#83).
#
# The marker above only proves the installer exited zero, and it would exit
# zero having looked at nothing if the verification were ever dropped or
# short-circuited. The lines it prints are the evidence that it looked - on the
# disk this lab actually installed, which is the only place blkid can answer
# for the UUIDs in the fstab that was just written.
#
# test/test_installer.sh drives the broken trees this cannot produce: an
# install that succeeds cannot demonstrate what a failed verification does.
if grep -q "verifying the disk that was just written" /tmp/install-clean.log; then
  echo "  [ ok ] the installer verified the disk before reporting success"
  grep -E "\[ ok \] (GRUB boots|fstab:|root has a password|management network)" \
    /tmp/install-clean.log | sed 's/^/         /'
else
  echo "  [FAIL] the installer reported success without checking what it wrote:"
  echo "         a box that boots to a grub> prompt reports as an install that"
  echo "         worked, in front of whoever is standing there"
  fail=1
fi

# And the mirror, verified by the INSTALLER on the machine that built it (#83).
#
# "mdadm --create" exits zero having built a degraded array, so an install that
# merely ran mdadm is not an install that produced a mirror. The line asserted
# here is the installer reading /proc/mdstat back - on the two disks it had
# just partitioned, which is the only moment and the only machine where that
# question can be answered.
if [ "$MODE" = mirror ]; then
  if grep -q "records mirrored on /dev/md0, 2 of 2 disks present" \
       /tmp/install-clean.log; then
    echo "  [ ok ] the installer built the records mirror and checked both legs"
    grep -o "records mirrored on.*" /tmp/install-clean.log | head -1 \
      | sed 's/^/         /'
  else
    echo "  [FAIL] the install finished without a two-legged records mirror."
    echo "         Either the array was never created, or it was created"
    echo "         DEGRADED - which is the failure the second disk was bought"
    echo "         to prevent, arriving at the moment the operator is told the"
    echo "         install worked"
    grep -iE "mirror|mdadm|md0" /tmp/install-clean.log | tail -6 | sed 's/^/         /'
    fail=1
  fi
fi

echo
echo "=========== WHAT LANDED ON THE DISK ==========="
# Read the installed filesystem directly. Booting it and logging in would be
# the other way to prove the password, and it would mean sending keystrokes at
# a login prompt at the right moment - the thing this lab exists not to do.
DISK_FAIL=0
# Mounted by byte offset, not through /dev/loop0p2.
#
# "losetup -P" attaches the image and the partition nodes never appear: the
# kernel creates them, udev publishes them, and there is no udev in this
# container. The first version of this check used them, could not read
# anything back, and reported it as though the install had gone wrong - so
# every step below says what happened rather than swallowing it.
# The tools this verification needs, checked BY NAME before it starts.
#
# Without this the missing tool surfaced as "could not read the installed
# filesystem back" - which blames the install for a tool that was never
# installed in the container, and sends whoever reads it to look at the
# installer. The same lab passed under "make test-install", whose target
# happens to apt-get fdisk, and failed under "tools/lab.sh install-iso",
# whose apt line did not: the same lab, two answers, depending on which
# command ran it.
for _t in qemu-img sfdisk; do
  command -v "$_t" >/dev/null 2>&1 || {
    echo "  [FAIL] $_t is not installed in this container."
    echo "         This step reads the installed disk back; it cannot run"
    echo "         without it, and its absence says nothing about the install."
    exit 1
  }
done

qemu-img convert -O raw "$DISK" /tmp/prisma-disk.raw 2>&1 | sed 's/^/  qemu-img: /'
ls -l /tmp/prisma-disk.raw 2>&1 | sed 's/^/  /'
sfdisk -J /tmp/prisma-disk.raw > /tmp/parts.json 2>/tmp/sfdisk-j.log \
  || sed 's/^/  sfdisk: /' /tmp/sfdisk-j.log
START=$(python3 -c '
import json, sys
t = json.load(open("/tmp/parts.json"))["partitiontable"]
print(max(t["partitions"], key=lambda p: p["size"])["start"])
' 2>/tmp/parse.log) || sed 's/^/  parse: /' /tmp/parse.log
echo "  root partition starts at sector ${START:-?}"
mkdir -p /tmp/target
if [ -n "$START" ] \
   && mount -o loop,ro,offset=$((START * 512)) /tmp/prisma-disk.raw /tmp/target \
        2>/tmp/mount.log; then
  INST_ROOT=$(grep "^root:" /tmp/target/etc/shadow 2>/dev/null | cut -d: -f2)
  INST_HOST=$(cat /tmp/target/etc/hostname 2>/dev/null || true)
  INST_TZ=$(cat /tmp/target/etc/timezone 2>/dev/null || true)
  # What the installed box will mount the records from, and what it believes
  # about its own disks. Read off the disk rather than off the console: the
  # installer's output says what it meant to write.
  INST_REC_UUID=$(awk '$2 == "/var/log/prisma-routing" { print $1 }' \
      /tmp/target/etc/fstab 2>/dev/null | sed 's/^UUID=//')
  INST_MDCONF=$(grep "^ARRAY" /tmp/target/etc/mdadm/mdadm.conf 2>/dev/null || true)
  # The other half of #245. The medium carries no ssh host keys, so the
  # installer has to make this box its own - and sshd, which the installer
  # enables with PermitRootLogin yes, does not start without them. A box
  # installed at a site with no management path is found by somebody who
  # drove there.
  INST_HOSTKEYS=$(ls /tmp/target/etc/ssh/ssh_host_*_key 2>/dev/null | wc -l)
  echo "  hostname  ${INST_HOST:-<none>}"
  echo "  timezone  ${INST_TZ:-<none>}"
  echo "  root      $(printf '%s' "${INST_ROOT:-<none>}" | cut -c1-12)..."
  PW_OK=$(python3 -W ignore - "$INST_ROOT" "$NEW_PW" <<'PY'
# crypt goes away in Python 3.13; the container is Debian 12 (3.11). When the
# base image moves, this becomes passlib or a call to "openssl passwd".
import crypt, sys
h, pw = sys.argv[1], sys.argv[2]
print("yes" if h.startswith("$") and crypt.crypt(pw, h) == h else "no")
PY
)
  umount /tmp/target
else
  echo "  [FAIL] could not read the installed filesystem back"
  echo "         root partition starts at sector '${START:-?}'"
  sed 's/^/         /' /tmp/mount.log 2>/dev/null | head -3
  DISK_FAIL=1
fi

if [ "$DISK_FAIL" = 0 ]; then
  # THE assertion. Everything else about this install can be right and the box
  # can still ship with the password whoever built the image chose.
  if [ "${PW_OK:-no}" = yes ]; then
    echo "  [ ok ] the root password on disk is the one the installer was given"
  else
    echo "  [FAIL] the installed root password is NOT the answer given"
    fail=1
  fi
  if [ -n "${INST_ROOT:-}" ] && [ "${INST_ROOT:-}" != "${LIVE_ROOT:-x}" ]; then
    echo "  [ ok ] and it is not the entry the live image carried"
  else
    echo "  [FAIL] the installed shadow entry is the live image's"
    fail=1
  fi
  # An unread /etc/shadow is NOT a clean /etc/shadow.
  #
  # This used to accept an empty LIVE_ROOT as proof, and the read above is
  # deliberately tolerant of unsquashfs failing - so on any runner without it,
  # the check that guards a published-password ISO passed without reading a
  # byte of the image. The whole point of this check is the case where nobody
  # is looking.
  if [ "$SHADOW_READ" != "1" ]; then
    echo "  [FAIL] could not read the live image's /etc/shadow, so nothing"
    echo "         here proves the ISO carries no usable root password"
    fail=1
  else
    case "${LIVE_ROOT:-}" in
      "!"*|"*"|"") echo "  [ ok ] the live image itself carries no usable password" ;;
      *) echo "  [FAIL] the live image has a usable root password: an ISO on a"
         echo "         network is rootable by anyone who has read the release"
         fail=1 ;;
    esac
  fi
  if [ "${INST_HOSTKEYS:-0}" -ge 1 ]; then
    echo "  [ ok ] the installed box generated its own ssh host keys"
  else
    echo "  [FAIL] the installed box has no ssh host key, so sshd will not"
    echo "         start and the box has no management path over the network"
    fail=1
  fi
  if [ "${INST_HOST:-}" = "$NEW_HOST" ]; then
    echo "  [ ok ] the hostname answer reached the disk"
  else
    echo "  [FAIL] hostname is '${INST_HOST:-}', expected '$NEW_HOST'"
    fail=1
  fi
  if [ "${INST_TZ:-}" = "$NEW_TZ" ]; then
    echo "  [ ok ] the timezone answer reached the disk"
  else
    echo "  [FAIL] timezone is '${INST_TZ:-}', expected '$NEW_TZ'"
    fail=1
  fi
else
  fail=1
fi

# --------------------------------------------------- the SECOND disk (#83)
#
# Everything below is read off the mirror disk alone, with the machine that
# wrote it switched off. It answers the one question the decision to mirror
# the records and not the system rests on: when the system disk dies, are the
# records still there and can anybody read them?
#
# Nothing here assembles an array to find out. The ext4 is mounted straight
# off the leg at its data offset, which is what a person with one surviving
# disk and a different machine actually has.
if [ "$MODE" = mirror ]; then
  echo
  echo "=========== WHAT LANDED ON THE MIRROR DISK ==========="
  for _t in mdadm losetup; do
    command -v "$_t" >/dev/null 2>&1 || {
      echo "  [FAIL] $_t is not installed in this container, and this step"
      echo "         reads the mirror disk back. Its absence says nothing"
      echo "         about the install."
      exit 1
    }
  done
  qemu-img convert -O raw "$DISK2" /tmp/prisma-mirror.raw 2>&1 | sed 's/^/  qemu-img: /'
  sfdisk -J /tmp/prisma-mirror.raw > /tmp/parts2.json 2>/tmp/sfdisk-j2.log \
    || sed 's/^/  sfdisk: /' /tmp/sfdisk-j2.log
  MSTART=$(python3 -c '
import json
t = json.load(open("/tmp/parts2.json"))["partitiontable"]
p = t["partitions"]
print("%d %d" % (len(p), p[0]["start"]))
' 2>/tmp/parse2.log) || sed 's/^/  parse: /' /tmp/parse2.log
  MPARTS=$(printf '%s' "${MSTART:-0 0}" | cut -d' ' -f1)
  MOFF=$(printf '%s' "${MSTART:-0 0}" | cut -d' ' -f2)
  echo "  the installer left $MPARTS partition(s) on the mirror disk, the first"
  echo "  at sector $MOFF"
  if [ "${MPARTS:-0}" = 1 ]; then
    echo "  [ ok ] the mirror disk was partitioned by the installer"
  else
    echo "  [FAIL] the mirror disk carries $MPARTS partitions, not the one the"
    echo "         installer says it writes. Nothing below is about a mirror"
    echo "         this product built"
    fail=1
  fi
  MLOOP=$(losetup --find --show --read-only --offset "$(( MOFF * 512 ))" \
          /tmp/prisma-mirror.raw 2>/tmp/losetup.log) || true
  if [ -n "${MLOOP:-}" ]; then
    mdadm --examine "$MLOOP" > /tmp/examine.log 2>&1 || true
    MLEVEL=$(awk -F': *' '/Raid Level/ { print $2; exit }' /tmp/examine.log)
    MDEVS=$(awk -F': *' '/Raid Devices/ { print $2; exit }' /tmp/examine.log)
    # The name carries a colon of its own ("<host>:records"), so it is cut at
    # the label rather than split on colons.
    MNAME=$(sed -n 's/^ *Name : *//p' /tmp/examine.log | head -1)
    MDATAOFF=$(awk '/Data Offset/ { print $4; exit }' /tmp/examine.log)
    losetup -d "$MLOOP" 2>/dev/null || true
    echo "  the superblock on that partition: level '${MLEVEL:-<none>}',"
    echo "  ${MDEVS:-?} devices, name '${MNAME:-<none>}'"
    if [ "${MLEVEL:-}" = "raid1" ] && [ "${MDEVS:-}" = "2" ]; then
      echo "  [ ok ] the mirror disk carries a two-device raid1 superblock"
    else
      echo "  [FAIL] the mirror disk is not a leg of a two-device mirror:"
      sed 's/^/         /' /tmp/examine.log | head -8
      fail=1
    fi
  else
    echo "  [FAIL] could not open the mirror disk's partition:"
    sed 's/^/         /' /tmp/losetup.log 2>/dev/null | head -3
    fail=1
  fi

  # The filesystem itself, on the leg, with no md anywhere in the picture. Its
  # UUID is the one the installed box mounts /var/log/prisma-routing by - which is
  # what makes this the same records volume and not merely an ext4.
  mkdir -p /mnt/rec
  MIRROR_UUID=""
  if [ -n "${MDATAOFF:-}" ] \
     && mount -o loop,ro,offset="$(( (MOFF + MDATAOFF) * 512 ))" \
          /tmp/prisma-mirror.raw /mnt/rec 2>/tmp/mount2.log; then
    # The loop device the mount is on, named before anything is asked of it:
    # blkid with an empty argument lists every device on the machine running
    # this container, and one of those answers would read like this one.
    RECLOOP=$(findmnt -no SOURCE /mnt/rec 2>/dev/null)
    if [ -n "$RECLOOP" ]; then
      MIRROR_UUID=$(blkid -s UUID -o value "$RECLOOP" 2>/dev/null || true)
      echo "  the records filesystem reads off the mirror leg alone, label" \
           "'$(blkid -s LABEL -o value "$RECLOOP" 2>/dev/null)'"
    fi
    umount /mnt/rec
    echo "  [ ok ] the records filesystem is on the second disk and mounts"
    echo "         without the first one and without assembling an array"
  else
    echo "  [FAIL] the records filesystem could not be read off the mirror"
    echo "         disk. A mirror nobody can read from the survivor is not a"
    echo "         copy of anything:"
    sed 's/^/         /' /tmp/mount2.log 2>/dev/null | head -3
    fail=1
  fi
  if [ -n "$MIRROR_UUID" ] && [ "$MIRROR_UUID" = "${INST_REC_UUID:-x}" ]; then
    echo "  [ ok ] and it is the volume the installed box mounts: fstab names"
    echo "         UUID=$INST_REC_UUID"
  else
    echo "  [FAIL] the filesystem on the mirror disk is '${MIRROR_UUID:-<none>}'"
    echo "         and the installed fstab mounts '${INST_REC_UUID:-<none>}'."
    echo "         Two different volumes: the records the box writes are not"
    echo "         the ones on the second disk"
    fail=1
  fi
  # The installed system's own account of its disks. Without it, a box whose
  # array did not assemble has nothing on it saying there was ever supposed to
  # be a mirror - which is the state the self-test reports as a failed check.
  case "${INST_MDCONF:-}" in
    *":records"*)
      echo "  [ ok ] and the installed system records the array it belongs to:"
      echo "         $INST_MDCONF" ;;
    *)
      echo "  [FAIL] /etc/mdadm/mdadm.conf on the installed system names no"
      echo "         records array ('${INST_MDCONF:-<nothing>}'), so a box"
      echo "         whose mirror never assembled would come up writing to one"
      echo "         disk with nothing on it saying otherwise"
      fail=1 ;;
  esac
fi

# ------------------------------------------- booting the disk, more than once
#
# These four were written inside "if MODE = mirror" - the only path that
# booted a machine more than once - and they are here now because the one-disk
# path boots twice as well: the box is booted, driven, and then booted AGAIN
# with nothing typed at all, which is the only way to answer "does this box
# come back on the network by itself" (#303). Two copies of a console boot
# would be two things to keep in step.
#
# One machine at a time, each with its own console. The driver's waits, its
# report and its rewinding all read $CONSOLE_LOG and $CONSOLE_POS, so a boot
# is "a fresh log and a fresh pipe" and nothing else.
# shellcheck disable=SC2086
boot_vm() {  # <seconds> <qemu arguments...>
  _secs=$1; shift
  CONSOLE_POS=1
  : > "$CONSOLE_LOG"
  rm -f "$CONSOLE_IN"
  mkfifo "$CONSOLE_IN" || { echo "cannot create the console pipe $CONSOLE_IN"; exit 1; }
  timeout "$_secs" qemu-system-x86_64 \
    $ACCEL $IOMMU_DEV -m "${DISK_BOOT_MEM:-8192}" -smp "$BOOT_SMP" -nographic -no-reboot "$@" \
    < "$CONSOLE_IN" > "$CONSOLE_LOG" 2>&1 &
  QEMU_PID=$!
  # Held open for the whole session: a writer that opens and closes the pipe
  # for each line hands qemu an end-of-file on the serial console in between.
  exec 3> "$CONSOLE_IN"
}

# Asked to power off, not killed, wherever the box has something to flush.
# The record written on the mirrored volume is the whole point of the boot
# before this one, and a machine killed mid-write proves the mirror lost
# something it never held.
stop_vm() {  # <seconds to wait for a machine that was asked to power off>
  exec 3>&-
  _n=0
  while kill -0 "$QEMU_PID" 2>/dev/null && [ "$_n" -lt "${1:-0}" ]; do
    sleep 1
    _n=$((_n + 1))
  done
  kill "$QEMU_PID" 2>/dev/null || true
  sleep 2
  pkill -9 -f qemu-system-x86_64 2>/dev/null || true
  wait "$QEMU_PID" 2>/dev/null || true
  sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' "$CONSOLE_LOG" | tr -d '\r' > "$CONSOLE_CLEAN"
  cat "$CONSOLE_CLEAN" >> "$LOG" 2>/dev/null || true
}

mfield() {  # <clean console> <name>
  sed -n "s/^HXLAB $2=//p" "$1" | head -1 | tr -d '\r'
}

# The same marker, off the console of a machine that is STILL RUNNING.
#
# mfield reads the cleaned log, and that is only written when the machine
# stops - so a value the driver needs in order to type its next line cannot
# come from there. The cleaning is done here instead: \r goes because a serial
# console ends every line with one, and the escape sequences go with it.
#
# Anchored at the start of a line, which is what makes the "HX""LAB" split in
# every send() worth the ugliness: the console echoes what was typed, so the
# typed line carries the two halves and only the box's own output carries the
# marker joined up.
live_field() {  # <name>
  tr -d '\r' < "$CONSOLE_LOG" | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' \
    | sed -n "s/^HXLAB $1=//p" | head -1
}

login_root() {  # the first prompt the box offers, and root in the VTY
  wait_for "$NEW_HOST login:" 420 "$1" || return 1
  send "root"
  wait_for "Password:" 60 "login to ask for root's password" || return 1
  send "$LOGIN_PW"
  wait_for "Prisma Routing VTY" 180 "root's login to land in the VTY" || return 1
  send "start shell"
  wait_for "entering /bin" 60 "root's shell escape to open" || return 1
}


echo
if [ "$MODE" = mirror ]; then
  echo "=========== PHASE 2: boot both disks, ISO detached ==========="
else
  echo "=========== PHASE 2: boot the disk, ISO detached ==========="
fi
# The SAME memory as phase 1, deliberately.
#
# Booting the installed disk with less was an optimisation that fabricated a
# failure: the tuning is computed during the install, from the RAM the machine
# has then, and a box does not shrink between being installed and being turned
# on. Giving phase 2 less produced hugepages reserved for 4 GB on a 3 GB boot,
# VPP segfaulting in a restart loop, and an afternoon spent reading it as a
# product bug.
# No -cdrom and no -kernel: if the disk cannot boot itself, nothing here will
# boot it for it.
#
# ---------------------------------------------------------- the console driver
#
# A named pipe is qemu's stdin, so this lab can TYPE at the same serial line an
# operator is handed. Everything it types is in drive_console() below.
#
# Nothing here sleeps and hopes. A serial console echoes what is typed, a
# prompt arrives when the box is ready and not when the driver is, and prismash
# is not a shell - so every wait is for a string the BOX prints, with a timeout
# and a report naming the string it gave up on. A driver that cannot tell "the
# box never asked" from "the box asked and refused" is worse than no driver:
# both look like a timeout, and they need opposite work.
CONSOLE_IN=/tmp/console.in
CONSOLE_LOG=/tmp/disk.log
CONSOLE_POS=1
: > "$CONSOLE_LOG"
rm -f "$CONSOLE_IN"
mkfifo "$CONSOLE_IN" || { echo "cannot create the console pipe $CONSOLE_IN"; exit 1; }

# What the console has printed since the last thing the driver recognised.
#
# \r goes, because a serial console ends every line with one and a pattern
# anchored on the start of a line would never match otherwise. The escape
# sequences go with it: readline redraws its prompt with them, so the string
# "prisma> " arrives cut in half by a cursor move often enough to matter.
console_since() {
  tail -c "+$CONSOLE_POS" "$CONSOLE_LOG" 2>/dev/null \
    | tr -d '\r' | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g'
}

send() { printf '%s\n' "$*" >&3; }

# The whole console from the top, for the one question `console_since` cannot
# answer: what order two strings arrived in. Everything else here reads from
# the last thing recognised forward, deliberately - but "the box said it was
# ready BEFORE it offered a login prompt" is a claim about the transcript, not
# about what has happened since.
console_all() {
  tr -d '\r' < "$CONSOLE_LOG" 2>/dev/null | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g'
}

# Did <first> appear before <second> in everything printed so far?
#
# Byte offsets and not line numbers: the login prompt has no line ending of its
# own, so whatever the box printed last shares a line with it and a line
# comparison puts the two in the wrong order exactly when it matters.
printed_before() {  # <first> <second>
  console_all | awk -v a="$1" -v b="$2" '
    BEGIN { pa = -1; pb = -1; off = 0 }
    {
      if (pa < 0 && index($0, a)) pa = off + index($0, a)
      if (pb < 0 && index($0, b)) pb = off + index($0, b)
      off += length($0) + 1
    }
    END { exit !(pa > 0 && pb > 0 && pa < pb) }'
}

# Did the self-test reach its verdict, whatever that verdict was?
#
# ONE reader, and the reason is that there were two and only one of them was
# fixed. appliance/selftest ends with one of four sentences (#297): ready,
# ready with warnings above, installed and healthy with no NIC to forward on,
# and n checks failed. The ordering assertion above was corrected to accept
# all four and this one, four hundred lines below it, went on grepping for
# "Prisma Routing ready" - so an installed box that had printed a complete report
# was reported as a box that never came up, and the gate refused the medium a
# second time for the same reason it had refused it the first.
#
# test/test_selftest.sh holds the other half: the four sentences may appear in
# this file only inside this function, so a third reader cannot be written by
# hand without going red.
# The four sentences live in tools/selftest-verdict.sh, which boot-iso.sh and
# the release gate take too. See that file for what two of them being copied
# by hand cost.
. /src/tools/selftest-verdict.sh


console_gave_up() {  # <string> <what the driver was doing> <how long>
  echo
  echo "=========== THE CONSOLE DRIVER GAVE UP ==========="
  echo "  waiting for: '$1'"
  echo "  which was:   $2"
  echo "  it waited:   $3"
  echo "  what the console printed since the last thing the driver"
  echo "  recognised:"
  # The newline is not decoration: the console's last line has no line ending
  # of its own - it is a half-drawn prompt - so without this the diagnosis
  # below is printed onto the end of it and reads as the box's own words.
  console_since | tail -25 | sed 's/^/    | /'
  echo
  # The whole reason this function exists rather than an "expect timed out".
  # These four are different failures needing different work, and reporting
  # them as one timeout sends whoever reads it to the timing when the answer
  # is already on the screen.
  if console_since | grep -q "Login incorrect"; then
    echo "  DIAGNOSIS: the box ANSWERED and REFUSED the credentials. Nothing"
    echo "  here is slow: the password the installer was given and the one the"
    echo "  driver typed are not the same password."
  elif console_since | grep -qi "permission denied"; then
    echo "  DIAGNOSIS: the box ANSWERED and REFUSED the command. The account"
    echo "  does not have the reach its role implies - which is the defect"
    echo "  this lab exists to catch, and not a fault in the driver."
  elif [ -z "$(console_since | tr -d ' \t\n')" ]; then
    echo "  DIAGNOSIS: the box printed NOTHING at all since then. It is not"
    echo "  refusing anything - the console went silent, so the question is"
    echo "  whether the machine is still running, not what was typed at it."
  else
    echo "  DIAGNOSIS: the box is talking and never said the string above."
    echo "  Two different things end here and the tail tells them apart: the"
    echo "  box ANSWERED SOMETHING ELSE, which is this lab's assertion failing"
    echo "  and the answer is printed above - or a prompt's wording changed,"
    echo "  which fails identically and means nothing is wrong with the box."
  fi
  echo "  --- full console in $LOG ---"
}

wait_said() {  # <sentence> <seconds> <what the driver was doing>
  # wait_for, over a console whose line breaks are collapsed first.
  #
  # The VTY wraps its prose to 79 columns, so where a sentence breaks depends
  # on the words in it - and on the numbers. `commit confirmed 1` and
  # `commit confirmed 10` are the same sentence and break in different places:
  # at one minute it reads "...so this box has put" / "the previous
  # configuration back", and the needle straddles the fold. The one-minute
  # spelling is what this lab sends, so this assertion stopped matching the
  # moment that sentence was reflowed, over a box doing exactly the right
  # thing (#279, found again here in #283).
  #
  # THE JOURNAL PREFIX HAS TO COME OFF FIRST, and collapsing whitespace alone
  # is not enough. Every line on this console arrives as
  #
  #   [  189.895690] prismash[1772]: commit confirmed 1: nobody confirmed it ...
  #   [  189.899320] prismash[1772]: the previous configuration back
  #
  # so the fold does not merely break the sentence, it inserts a timestamp and
  # a pid into the middle of it. Joining the lines without stripping that
  # leaves the needle further from matching than it was.
  #
  # The offset bookkeeping stays on the RAW file. Only the comparison is
  # rewritten, because CONSOLE_POS is a byte count into the capture.
  _s=$(printf '%s' "$1" | tr -s '[:space:]' ' '); _t=$2; _why=$3; _n=0
  while :; do
    if console_since \
         | sed 's/^\[[0-9. ]*\] [A-Za-z0-9_.-]*\[[0-9]*\]: //' \
         | tr -s '[:space:]' ' ' | grep -qF "$_s"; then
      CONSOLE_POS=$(( $(wc -c < "$CONSOLE_LOG") + 1 ))
      return 0
    fi
    if ! kill -0 "$QEMU_PID" 2>/dev/null; then
      console_gave_up "$1" "$_why" "${_n}s, and then the machine exited"
      return 1
    fi
    if [ "$_n" -ge "$_t" ]; then
      console_gave_up "$1" "$_why" "${_t}s"
      return 1
    fi
    sleep 1
    _n=$((_n + 1))
  done
}

# TWO OUTCOMES, because a driver that can only wait for success spends its
# whole timeout on a box that already answered. PHASE 4 waited the full 600
# seconds for "commit complete" on a commit that had printed "commit failed"
# seconds in, and what diagnosed it was the console dump the timeout prints,
# not the wait (#332). Ten minutes per run, and a verdict that says "the box
# never answered" about a box that answered immediately.
#
# 0 for the first string, 2 for the second, 1 for neither - so a caller that
# ignores the distinction still reads a refusal as a failure rather than as a
# pass. The console position advances the same way wait_for's does, and for
# the same reason: without it a line printed once satisfies every later wait.
wait_for_either() {  # <string-a> <string-b> <seconds> <what it was doing>
  _a=$1; _b=$2; _t=$3; _why=$4; _n=0
  while :; do
    _seen=$(console_since)
    if printf '%s\n' "$_seen" | grep -qF "$_a"; then
      CONSOLE_POS=$(( $(wc -c < "$CONSOLE_LOG") + 1 ))
      return 0
    fi
    if printf '%s\n' "$_seen" | grep -qF "$_b"; then
      CONSOLE_POS=$(( $(wc -c < "$CONSOLE_LOG") + 1 ))
      return 2
    fi
    if ! kill -0 "$QEMU_PID" 2>/dev/null; then
      console_gave_up "$_a or $_b" "$_why" "${_n}s, and then the machine exited"
      return 1
    fi
    if [ "$_n" -ge "$_t" ]; then
      console_gave_up "$_a or $_b" "$_why" "${_t}s"
      return 1
    fi
    sleep 1
    _n=$((_n + 1))
  done
}

wait_for() {  # <string> <seconds> <what the driver was doing>
  _s=$1; _t=$2; _why=$3; _n=0
  while :; do
    if console_since | grep -qF "$_s"; then
      # Everything up to here has been recognised, so the next wait starts
      # after it. Without this a prompt printed once satisfies every later
      # wait for a prompt, and the driver races ahead of the box saying yes
      # to itself.
      CONSOLE_POS=$(( $(wc -c < "$CONSOLE_LOG") + 1 ))
      return 0
    fi
    if ! kill -0 "$QEMU_PID" 2>/dev/null; then
      console_gave_up "$_s" "$_why" "${_n}s, and then the machine exited"
      return 1
    fi
    if [ "$_n" -ge "$_t" ]; then
      console_gave_up "$_s" "$_why" "${_t}s"
      return 1
    fi
    sleep 1
    _n=$((_n + 1))
  done
}

# Wait until the box prints ONE of two strings, and say which.
#
# `wait_for` is the right shape when there is one right answer and everything
# else is a timeout. It is the wrong shape when the box has two answers and
# one of them is the defect: waiting for the good one turns a refusal the box
# printed in a second into a five-minute wait, and the report then blames the
# timing for something the screen said at once. Answers 0 for the first
# string, 1 for the second, 2 for neither.
expect_one_of() {  # <good> <bad> <seconds> <what the driver was doing>
  _g=$1; _b=$2; _t=$3; _why=$4; _n=0
  while :; do
    if console_since | grep -qF "$_g"; then
      CONSOLE_POS=$(( $(wc -c < "$CONSOLE_LOG") + 1 )); return 0
    fi
    if console_since | grep -qF "$_b"; then
      CONSOLE_POS=$(( $(wc -c < "$CONSOLE_LOG") + 1 )); return 1
    fi
    if ! kill -0 "$QEMU_PID" 2>/dev/null; then
      console_gave_up "$_g' or '$_b" "$_why" "${_n}s, and then the machine exited"
      return 2
    fi
    if [ "$_n" -ge "$_t" ]; then
      console_gave_up "$_g' or '$_b" "$_why" "${_t}s"
      return 2
    fi
    sleep 1
    _n=$((_n + 1))
  done
}

# The markers the box is made to print are split in the command - echo
# "HX""LAB" - so that what comes back is the box's ANSWER and never the
# driver's own keystrokes echoed back at it. Every assertion below anchors on
# the start of a line for the same reason.
drive_console() {
  # ------------------------------------- the FIRST prompt the box ever offers
  #
  # This lab used to start at "Prisma Routing ready." and then ask for a prompt,
  # and the comment here said why: the getty drew "cgnat-lab login:" while
  # prisma-routing.service was still waiting for the dataplane, so `configure`
  # typed at that prompt built its candidate out of vppctl's "Could not find
  # VPP CLI socket" and every commit in the session was then refused. Starting
  # later was the right call for a lab about ROLES - and it left the defect
  # untested, described in a comment, on a box somebody receives (#211).
  #
  # So the lab now starts where the operator does. The first thing anybody
  # does with a new router is log in at the first prompt it shows them, and
  # what happens next is the product's answer, not the timing of this driver.
  #
  # The fix has two halves and this exercises both. The getty is ordered after
  # the self-test, which is ordered after prisma-routing.service, whose
  # ExecStartPre waits for VPP - so a prompt offered at all means the dataplane
  # is up. And the VTY refuses to build a candidate from a dataplane that is
  # not answering, rather than building one out of the error text.
  #
  # 420s because this is a cold boot of an installed disk, not a wait for any
  # particular ordering: the prompt arrives when the box has finished starting.
  wait_for "$NEW_HOST login:" 420 \
    "the installed box to boot and offer its first login prompt" || return 1

  # Half one, read off the transcript: the self-test FINISHED first.
  #
  # Not a proxy for it. The self-test is ordered after prisma-routing.service,
  # whose ExecStartPre waits for VPP, so the position of its output in the
  # transcript is the ordering itself. Before the drop-in was split in two,
  # the installer deleted the file carrying this ordering along with the
  # autologin it was sharing a file with, and an installed box had no ordering
  # at all.
  #
  # THE ANCHOR IS THE CLOSING ROW AND NOT THE VERDICT SENTENCE, and that is a
  # scar. It used to look for "Prisma Routing ready." - the last line the
  # self-test printed, which made it the right anchor for an ordering
  # question. #297 gave that summary three forms, because a box that is
  # installed and healthy and has no NIC to forward on is neither ready nor
  # failed, and this gate then refused 0.72.0 over a box that had reported
  # itself correctly and in the right order.
  #
  # The row of equals signs is the last thing the self-test writes on every
  # path, so it answers the ordering question and cannot be changed by a
  # verdict being reworded. It is a bare row, and the header at the top of the
  # report carries text between its runs of equals, so the two cannot be
  # confused.
  #
  # What the verdict SAID is asserted separately, below. One of these is about
  # order and the other about content, and merging them is how the first one
  # broke.
  if printed_before "=============================================================" "$NEW_HOST login:"; then
    echo "  [ ok ] the box reports itself ready before it offers a login"
  else
    echo "  [FAIL] the box offered a login prompt before it was ready"
    echo "         the getty reached the console ahead of the self-test, so"
    echo "         the prompt is drawn while prisma-routing.service is still"
    echo "         waiting for VPP. An operator who logs in there is in a VTY"
    echo "         that cannot read the box (#211). On the live image the"
    echo "         ordering is in 10-after-selftest.conf; on an installed box"
    echo "         it is the same file, and the installer must delete only"
    echo "         20-autologin.conf beside it."
    fail=1
  fi

  # And WHAT it said, which is a different question from when it said it.
  #
  # All three of the self-test's verdicts are a report, and any of them
  # answers the ordering check above. Only two of them say this box is fit to
  # be handed over. The third - installed and healthy with no interface to
  # forward on - was the state an appliance was in on the day it was racked,
  # and it is what a box built by this lab used to be in, because nothing
  # here names a dataplane interface.
  #
  # So the assertion HERE is that the verdict is one the self-test can print,
  # and NOT that it is any particular one. A lab that demanded "ready" would
  # be demanding the box lie about having a NIC it was never given.
  #
  # SINCE #378 THE THIRD VERDICT IS NOT ALWAYS GOOD NEWS, and this is not the
  # place that can tell. The box claims its own card now, so that sentence on
  # a machine that HAS a PCI NIC is the box reporting it did not - and whether
  # this box has one is read off the box further down, not known here. The
  # check that pairs the two is "the card this box claimed with nobody having
  # typed", below, and it is deliberately not duplicated into this branch:
  # two readers of one verdict is what refused 0.72.0 twice (#311).
  console_all > /tmp/verdict-probe.log 2>/dev/null || true
  if ! said_a_verdict /tmp/verdict-probe.log; then
    echo "  [FAIL] the self-test printed no verdict this lab recognises"
    echo "         appliance/selftest ends with one of four sentences and the"
    echo "         console carries none of them. Either it stopped before its"
    echo "         summary, or the summary was reworded and this lab was not."
    fail=1
  elif said_a_failed_verdict /tmp/verdict-probe.log; then
    echo "  [FAIL] the installed box failed its own self-test"
    echo "         the console above says which checks. This is the box an"
    echo "         operator is handed, so a failed check here is a defect"
    echo "         that ships."
    fail=1
  else
    # Which of the three healthy verdicts it was is printed and not asserted.
    # All three mean the box is fit to be handed over, and demanding a
    # particular one would demand the box lie about a NIC it was never given,
    # which is the defect #297 fixed, reintroduced from the test side.
    echo "  [ ok ] and its verdict was one the self-test can reach:"
    grep -m1 -E "$VERDICT_OK_RE" /tmp/verdict-probe.log \
      | sed 's/^ */         /'
  fi

  send "root"
  wait_for "Password:" 60 \
    "the first login to ask for root's password" || return 1
  send "$LOGIN_PW"
  wait_for "Prisma Routing VTY" 180 \
    "root's first login to land in the VTY" || return 1

  # Half two: what that session can actually do.
  #
  # `configure` and `commit`, with nothing typed in between. It is the smallest
  # thing an operator does and it is the whole defect: the candidate is built
  # at `configure` time out of what the box reads back, so a dataplane that is
  # not answering puts its error text in the candidate as configuration lines,
  # and the refusal lands on the FIRST REAL COMMIT of the session - by which
  # time the operator has typed a pool and has every reason to think the pool
  # is what was refused.
  echo "  -- the first session: configure and commit, with nothing typed"
  send "configure"
  expect_one_of "entering configuration mode" "the dataplane is not answering" \
    90 "the first session to enter configuration mode"
  case $? in
    0) echo "  [ ok ] the first session can enter configuration mode" ;;
    1) echo "  [FAIL] the VTY refused to build a candidate: the dataplane was"
       echo "         not answering at the prompt the box had just offered."
       echo "         The VTY is right to refuse - this is the getty half of"
       echo "         #211, and the ordering drop-in did not survive the"
       echo "         install."
       fail=1; return 1 ;;
    *) return 1 ;;
  esac

  send "commit"
  expect_one_of "no changes to commit" "does not understand" \
    300 "the first session's empty commit to be answered"
  case $? in
    0) echo "  [ ok ] an empty commit is answered with nothing to do" ;;
    1) echo "  [FAIL] the commit was refused for a line nobody typed"
       echo "         the candidate was built out of the dataplane's error"
       echo "         text and carries it as configuration. Every commit in"
       echo "         this session is refused from here on, including the"
       echo "         operator's first real one (#211)."
       fail=1; return 1 ;;
    *) return 1 ;;
  esac
  send "exit"
  wait_for "prisma> " 60 \
    "the first session to leave configuration mode" || return 1
  send "exit"
  wait_for "$NEW_HOST login:" 120 \
    "the first session to log out and the getty to come back" || return 1

  # ------------------------------------------ the roles, from here on as ever
  send "root"
  wait_for "Password:" 60 "login to ask for root's password" || return 1
  send "$LOGIN_PW"
  # The banner, not a prompt. It is the one string that proves the login
  # SUCCEEDED - and that root's login shell on a shipped box is this VTY. A
  # driver that waited for a prompt would type its next command at the login
  # prompt that follows a refusal and run the whole lab against it, which is
  # the single most likely way for this to go green proving nothing.
  wait_for "Prisma Routing VTY" 180 "root's login to land in the VTY" || return 1
  echo "  -- root is in the VTY; the VTY creates the accounts and grants a role"

  send "configure"
  wait_for "entering configuration mode" 60 \
    "the VTY to enter configuration mode" || return 1
  send "user $OP_USER"
  wait_for "[edit user $OP_USER]" 30 "the user block to open" || return 1
  send "password $OP_HASH"
  send "role administrator"
  send "exit"
  wait_for "[edit]" 30 "the user block to close" || return 1
  send "user $RO_USER"
  wait_for "[edit user $RO_USER]" 30 \
    "the second user block to open" || return 1
  # No role at all: the half a lab that only grants things never checks. An
  # account with no role must be able to do nothing, or the boundary the box
  # reports is decoration.
  send "password $RO_HASH"
  send "exit"
  wait_for "[edit]" 30 "the second user block to close" || return 1
  send "commit"
  wait_for "commit complete" 300 "root's commit to apply the accounts" || return 1
  send "exit"
  wait_for "prisma> " 30 "the VTY to leave configuration mode" || return 1

  echo "  -- what the BOX says those accounts got, read off the box"
  send "start shell"
  wait_for "entering /bin" 60 "root's shell escape to open" || return 1
  send 'echo "HX""LAB whoami=$(id -un)"'
  wait_for "HXLAB whoami=root" 60 "the shell to answer" || return 1
  send 'echo "HX""LAB groups-op=$(id -nG '"$OP_USER"')"'
  wait_for "HXLAB groups-op=" 30 \
    "the box to list the administrator's memberships" || return 1
  send 'echo "HX""LAB groups-ro=$(id -nG '"$RO_USER"')"'
  wait_for "HXLAB groups-ro=" 30 \
    "the box to list the read-only account's memberships" || return 1
  send 'echo "HX""LAB shell-op=$(getent passwd '"$OP_USER"' | cut -d: -f7)"'
  wait_for "HXLAB shell-op=" 30 \
    "the box to say what the account logs into" || return 1
  send 'echo "HX""LAB modes=$(stat -c %a:%G /etc/prisma-routing) $(stat -c %a:%G /etc/prisma-routing/rollback)"'
  wait_for "HXLAB modes=" 30 \
    "the box to state the configuration directory's mode" || return 1
  # What this ONE-DISK box has in the way of arrays, asked of the kernel (#83).
  #
  # The records can be mirrored across two disks, and most boxes have one. The
  # cost of that feature to a box that did not ask for it has to be zero, and
  # zero is a thing to read off the machine rather than to assume: an installer
  # that built an array nobody asked for, or a self-test that warns about a
  # mirror that was never bought, would both show up here.
  send 'echo "HX""LAB arrays=$(grep -cE "^md[0-9]" /proc/mdstat 2>/dev/null || echo no-mdstat)"'
  wait_for "HXLAB arrays=" 30 \
    "the box to say how many arrays it has" || return 1
  # The management network, asked of the KERNEL and of the box's own
  # configuration, not of what the installer printed while it was installing
  # (#303). Nothing in this lab typed an `ip` command, and that is the whole
  # assertion: on 0.72.0 this card came up DOWN with no address, and the
  # operator's `ip addr add` at the console did not survive the next reboot.
  #
  # The interface NAME is read off the box, never assumed. It is whatever
  # predictable naming calls qemu's card on this host, the installer chose it
  # from what the machine had, and a lab that hard-coded "ens3" would be
  # asserting against its own guess - which is #161's standing rule.
  send 'M=$(sed -n "s/^iface \([^ ]*\) .*/\1/p" /etc/network/interfaces.d/prisma-routing | head -1); echo "HX""LAB mgmt-if=$M"'
  wait_for "HXLAB mgmt-if=" 30 \
    "the box to say which card it was installed to be managed on" || return 1
  send 'echo "HX""LAB mgmt-addr=$(ip -o -4 addr show dev $M | awk "{print \$4}" | tr "\n" " ")"'
  wait_for "HXLAB mgmt-addr=" 30 \
    "the box to say what address that card is carrying" || return 1
  send 'echo "HX""LAB mgmt-route=$(ip -o -4 route list default | tr "\n" "/")"'
  wait_for "HXLAB mgmt-route=" 30 \
    "the box to say how it reaches anything off its own subnet" || return 1
  # And what systemd did with the file. A stanza in a directory nothing
  # sources, or a networking.service that failed, are both a box with a
  # perfect configuration file and no address - and they look identical from
  # the file alone.
  send 'echo "HX""LAB mgmt-unit=$(systemctl is-enabled networking 2>&1) $(systemctl is-active networking 2>&1)"'
  wait_for "HXLAB mgmt-unit=" 30 \
    "systemd to say what it did with the management network" || return 1
  # ------------- can the box reach anything, ASKED OF THE VTY (#395)
  #
  # THE STATEMENT AN OPERATOR TYPES, on the box the medium installed, against
  # a host this lab knows the answer for: the gateway qemu's user network
  # hands out, which is what the management card just got its default route
  # to. A unit test can assert the argument vector and nothing else; whether
  # this appliance can actually send an echo request and read the reply is a
  # property of the installed image - the tool being in it, the VTY finding
  # it, and the account being allowed to run it.
  #
  # Through `prismash -c` rather than at the prompt, because the statement and
  # the batch go through the same dispatch and the batch can be read back
  # from a serial line without timing anything.
  #
  # THE COUNT IS 2 and the deadline is the VTY's own: a lab that waited for
  # five packets against an unreachable host would be waiting on the default.
  send 'G=$(ip -o -4 route list default | awk "{print \$3}" | head -1); echo "HX""LAB probe-gw=$G"'
  wait_for "HXLAB probe-gw=" 30 \
    "the box to say which gateway it would be pinging" || return 1
  send 'echo "HX""LAB probe-ping=$(prismash -c "ping $G count 2" 2>&1 | tr -d "\r" | grep -c " bytes from ")"'
  wait_for "HXLAB probe-ping=" 120 \
    "the VTY's own ping to answer" || return 1
  # AND THE REFUSAL, from the same box, because a statement that only ever
  # says yes is one nobody has seen decide. A read-only account is not
  # arranged here - that is what the unit suite drives - but an answer this
  # VTY must not accept is, and it must not reach the tool at all.
  send 'echo "HX""LAB probe-refused=$(prismash -c "ping 1.1.1.1 count 0" 2>&1 | tr -d "\r" | grep -c "count out of range")"'
  wait_for "HXLAB probe-refused=" 60 \
    "the VTY to refuse a count it does not take" || return 1
  send 'echo "HX""LAB probe-tools=$(for t in ping traceroute mtr; do command -v $t >/dev/null 2>&1 || echo $t; done | tr "\n" " ")"'
  wait_for "HXLAB probe-tools=" 30 \
    "the box to say which reachability tools it has not got" || return 1

  # ------------- the card the box claimed, with nobody having typed (#378)
  #
  # THE ABSENCE OF AN OPERATOR IS THE SUBJECT. Every reading here is taken
  # before this lab types anything into the VTY, so a dataplane card found
  # at this point is one the box chose. Until #378 there was no such thing
  # to read: a fresh install came up with no-pci, the self-test told the
  # operator to name a card, and the release gate accepted that as healthy
  # because it was the only state a box built by this lab could be in.
  #
  # FIRST, THE PROOF THAT NOBODY NAMED IT, and it is the reading the rest
  # of this block is worthless without. PRISMA_DATAPLANE_IF is the tuner's
  # input and the VTY writes it; it ships empty and nothing in this lab
  # sets it. A non-empty value here means something typed, and then a
  # claimed card proves nothing about a box that claims its own.
  #
  # The COUNT of non-empty assignments, not the value. An empty assignment
  # is what the box ships with and is the answer wanted, so a reader that
  # printed the value would give "" for the good case and for a tune.env
  # that is missing altogether - and those are different boxes.
  send 'echo "HX""LAB claim-typed=$(grep -c "^PRISMA_DATAPLANE_IF=." /etc/prisma-routing/tune.env)"'
  wait_for "HXLAB claim-typed=" 30 \
    "the box to say whether anything named a card in the tuner's file" \
    || return 1
  # The box's NICs as PCI has them, with the driver holding each.
  #
  # FROM /sys/bus/pci AND NOT FROM /sys/class/net, and that is the whole
  # reason this reading exists in this shape: a card handed to DPDK stops
  # having a netdev, so a box that claimed everything would answer "no
  # cards" to the obvious question and read identically to a box with no
  # hardware. The PCI class is 0x02 for a network controller and does not
  # change when a driver does, so this counts the same cards before and
  # after the claim - which is what makes the two comparable at all.
  send 'echo "HX""LAB claim-pci=$(for d in /sys/bus/pci/devices/*; do grep -q "^0x02" $d/class 2>/dev/null && printf "%s:%s " ${d##*/} $([ -e $d/driver ] && basename $(readlink -f $d/driver) || echo none); done)"'
  wait_for "HXLAB claim-pci=" 30 \
    "the box to list its network cards and what holds each" || return 1
  # What the TUNER decided, which is a different question from what the
  # dataplane got. A device named here and absent below is a bind that did
  # not take; nothing named here at all is a box that decided to claim
  # none, and the two need opposite work.
  send 'echo "HX""LAB claim-conf=$(grep -o "dev 0000:[0-9a-f:.]*" /etc/vpp/startup.conf | sed "s/dev //" | tr "\n" " ")"'
  wait_for "HXLAB claim-conf=" 30 \
    "the box to say which devices its startup.conf names" || return 1
  send 'echo "HX""LAB claim-nopci=$(grep -c "no-pci" /etc/vpp/startup.conf)"'
  wait_for "HXLAB claim-nopci=" 30 \
    "the box to say whether its startup.conf refuses every device" \
    || return 1
  # AND WHETHER DPDK IS ON AT ALL, which is a third answer and not a shade of
  # the other two. Without hugepages the tuner turns the DPDK plugin off and
  # emits no dpdk stanza whatever - no device, and no `no-pci` either - so a
  # reader that knew only those two would report the deliberate first-boot
  # profile as the #123 box that lets DPDK probe for itself. appliance/selftest
  # tells the same three apart for the same reason, and got it wrong once.
  #
  # It is asked here rather than inferred, because "no dpdk stanza" and "DPDK
  # disabled" look identical from the stanza alone.
  send 'echo "HX""LAB claim-dpdk=$(grep -c "plugin dpdk_plugin.so { disable }" /etc/vpp/startup.conf)"'
  wait_for "HXLAB claim-dpdk=" 30 \
    "the box to say whether the DPDK plugin is on at all" \
    || return 1
  # AND WHAT THE DATAPLANE ITSELF HOLDS, WITH THE STATE OF IT. The readings
  # above are files and sysfs; this is the only one that answers "did
  # anything take the card". A card unbound from the kernel with VPP holding
  # nothing is worse than a card nobody touched - neither stack can reach the
  # wire - and from outside the box the two are indistinguishable.
  #
  # NAME, STATE AND MTU and not the name alone, which is what this asked for
  # when it was written. On a tree where the box could not claim a card
  # unbidden, "the dataplane holds an interface" was the whole question: the
  # only way to get one was to type, and this block runs before anything is
  # typed. Now the box claims by itself and a new answer exists that the old
  # reading could not tell from success - the port is held AND in `error`
  # state, which is #382: the driver refused it, it carries nothing, and
  # every other reading here is still true of that box.
  #
  # The same triple PHASE 5 reads after a handover, in the same shape, asked
  # here on a box nobody has typed at.
  send 'echo "HX""LAB claim-dpif=$(vppctl show interface 2>/dev/null | tr -d "\r" | awk "/^[^ ]/ && \$1 != \"Name\" && \$1 != \"local0\" {split(\$4, m, \"/\"); print \$1 \":\" \$3 \":\" m[1]}" | tr "\n" " ")"'
  wait_for "HXLAB claim-dpif=" 60 \
    "the dataplane to list the interfaces it holds unbidden" || return 1
  # AND HOW MANY RX QUEUES IT WAS CONFIGURED WITH ON EACH, from the one place
  # that knows (#345): `show hardware-interfaces` prints `rx: queues N (max
  # M)` under every DPDK device and nothing of the kind for a pg interface,
  # which is why the self-test's reader waited for this guest. Asked here so
  # that the self-test's own row can be checked against the dataplane's
  # answer rather than against a fixture: name:opened:max per card, one card
  # per token. "Opened" only for a card whose driver started it - the row is
  # printed for a refused card too, and claim-dpif above carries the state.
  send 'echo "HX""LAB claim-rxq=$(vppctl show hardware-interfaces 2>/dev/null | tr -d "\r" | awk "/^[^ ]/ {n=\$1} \$1 == \"rx:\" && \$2 == \"queues\" {m=\$5; gsub(/[^0-9]/, \"\", m); print n \":\" \$3 \":\" m}" | tr "\n" " ")"'
  wait_for "HXLAB claim-rxq=" 60 \
    "the dataplane to say how many rx queues it opened on each card" \
    || return 1
  # What systemd will do when this box is stopped, asked of SYSTEMD and not of
  # the drop-in file. In dynamic mode the block record is what answers a court
  # order under the Marco Civil, and a block held when the dataplane stops
  # leaves an allocation open in the collector's copy for ever (#217). The dataplane closes them when
  # asked, and what asks is an ExecStop in a vpp.service drop-in - so a drop-in
  # that was installed and that systemd parsed and refused reads exactly like
  # one that works. There is no systemd in a lab container; this is the only
  # place in the repository that has one.
  send 'echo "HX""LAB stopdrain=$(systemctl show vpp.service -p ExecStop 2>&1 | grep -c "bpa stop-drain")"'
  wait_for "HXLAB stopdrain=" 30 \
    "systemd to say what it runs before stopping the dataplane" || return 1

  # The bounds the control plane carries, asked of SYSTEMD rather than of the
  # unit file (#172). prisma-routing.service ships MemoryMax=512M against a
  # measured 13 MB peak, and frr.service deliberately ships none because one
  # full IPv4 table costs FRR 1.37 GB and 37 MB on a box taking default routes
  # only - one number cannot serve both. Neither of those has ever been read
  # back on a box: a unit systemd parsed and REFUSED reports exactly like one
  # that took, and this is the only place in the repository with a real
  # systemd. Both halves are asserted, because the absence is as deliberate as
  # the presence and a MemoryMax that appeared on frr.service by accident
  # would be a five-second kill loop on the box with the most to lose.
  send 'echo "HX""LAB memmax=hx:$(systemctl show prisma-routing.service -p MemoryMax --value) frr:$(systemctl show frr.service -p MemoryMax --value)"'
  wait_for "HXLAB memmax=" 30 \
    "systemd to say what memory bounds it applied" || return 1

  # And the CPU half, measured rather than assumed. isolcpus keeps the
  # scheduler from PLACING work on a worker's core; the open question is
  # whether it also narrows what a control-plane process is ALLOWED to ask
  # for, because if it does not then a slice with AllowedCPUs= buys something
  # isolcpus does not, and if it does then it buys nothing. bgpd's own mask is
  # one half; the other is whether taskset onto an isolated core is accepted.
  # Recorded, not asserted: what the kernel does with isolcpus is not this
  # repository's to change, and a check nothing here can make fail is not a
  # check. The reading is what the refusal in docs/appliance.md rests on.
  send 'echo "HX""LAB cpu-iso=$(cat /sys/devices/system/cpu/isolated) online=$(cat /sys/devices/system/cpu/online)"'
  wait_for "HXLAB cpu-iso=" 30 "the box to say which cores it isolates" || return 1
  send 'echo "HX""LAB cpu-bgpd=$(grep Cpus_allowed_list /proc/$(pidof bgpd)/status | tr -d " \t")"'
  wait_for "HXLAB cpu-bgpd=" 30 "the box to say where bgpd may run" || return 1
  send 'echo "HX""LAB cpu-escape=$(taskset -c $(cat /sys/devices/system/cpu/isolated) grep Cpus_allowed_list /proc/self/status | tr -d " \t")"'
  wait_for "HXLAB cpu-escape=" 30 \
    "the box to say whether an isolated core can be asked for" || return 1

  # Where the inherited mask comes from, and whether bgpd is still wearing it.
  #
  # Two runs of this lab on 2026-09-13 read cpu-bgpd as 0 and as 0-1 on boxes
  # that both reported isolcpus=1, so "the kernel already confines the control
  # plane" is a claim one reading supports and another refuses. These three
  # lines are what tells them apart and none of them was being read: pid 1's
  # own mask is what every service inherits, bgpd's parent and start time say
  # whether this bgpd is the one systemd started at boot or one frr-reload
  # started later from a different parent, and the isolcpus SPELLING decides
  # the behaviour - a bare list restricts pid 1's affinity and "domain," alone
  # does not.
  send 'echo "HX""LAB cpu-init=$(grep Cpus_allowed_list /proc/1/status | tr -d " \t")"'
  wait_for "HXLAB cpu-init=" 30 "the box to say what pid 1 is allowed" || return 1
  send 'echo "HX""LAB bgpd-from=pid:$(pidof bgpd) ppid:$(awk "{print \$4}" /proc/$(pidof bgpd)/stat) starttime:$(awk "{print \$22}" /proc/$(pidof bgpd)/stat)"'
  wait_for "HXLAB bgpd-from=" 30 "the box to say which bgpd this is" || return 1
  send 'echo "HX""LAB isolspell=$(tr " " "\n" < /proc/cmdline | grep isolcpus)"'
  wait_for "HXLAB isolspell=" 30 "the box to say how isolcpus is spelled" || return 1

  # The self-test again, now that the box has an account. At boot it said it
  # had none; this is the same program, on the same console, with the only
  # difference being the thing the VTY just did.
  echo "  -- the boot self-test, run again now that an account has a role"
  send 'echo "HX""LAB selftest-rerun"'
  wait_for "HXLAB selftest-rerun" 30 "the marker before the re-run" || return 1
  send "/usr/lib/prisma-routing/selftest"
  wait_for "====================" 240 "the self-test to finish" || return 1

  send 'echo "HX""LAB section=op"'
  wait_for "HXLAB section=op" 30 "the marker that ends root's session" || return 1
  send "exit"
  wait_for "back in the Prisma Routing VTY" 60 \
    "the shell escape to return to the VTY" || return 1
  send "exit"
  wait_for "$NEW_HOST login:" 120 \
    "the VTY to log root out and the getty to come back" || return 1

  # --------------------------------------------------- the account, as itself
  echo "  -- the account the VTY made logs in and uses the role it was given"
  send "$OP_USER"
  wait_for "Password:" 60 "login to ask for the account's password" || return 1
  send "$OP_PW"
  wait_for "Prisma Routing VTY" 180 \
    "the account the VTY created to log in and land in the VTY" || return 1
  send "configure"
  wait_for "entering configuration mode" 60 \
    "an administrator to be let into configuration mode" || return 1
  # Routing, which is the half a cgnat-operator is refused and the half that
  # was broken for a non-root administrator: frr-reload.py writes its log into
  # a directory the frr package ships read-only to group frr, so this account
  # could `commit check` a change and not commit it (#186).
  send "router bgp 65000"
  wait_for "[edit router bgp 65000]" 30 "the BGP block to open" || return 1
  send "neighbor 10.0.0.2 remote-as 65001"
  send "neighbor 10.0.0.2 description NETADM-ON-AN-INSTALLED-BOX"
  send "exit"
  wait_for "[edit]" 30 "the BGP block to close" || return 1
  # And CGNAT, which goes over the BINARY API. That is the call that fails for
  # everybody but root unless the drop-in on vpp.service group-owns
  # /dev/shm/global_vm - a drop-in that runs under systemd, which no other lab
  # in this repository has (#76).
  send "cgnat pool lab-a"
  wait_for "[edit cgnat pool lab-a]" 30 "the pool block to open" || return 1
  send "inside 100.64.0.0/22"
  send "outside 198.51.100.64/30"
  send "exit"
  wait_for "[edit]" 30 "the pool block to close" || return 1
  send "commit"
  wait_for "commit complete" 300 \
    "an administrator's commit to be applied on the installed box" || return 1
  send "exit"
  wait_for "prisma> " 30 "the VTY to leave configuration mode" || return 1

  # Read back from FRR and from the dataplane, by the account itself. "commit
  # complete" is this VTY's own sentence; whether the routing daemon and the
  # dataplane took a change from an account that is not root is a question
  # only they can answer.
  send "start shell"
  wait_for "entering /bin" 60 "the administrator's shell escape to open" || return 1
  send 'echo "HX""LAB frr=$(vtysh -c "show run" | grep -c NETADM-ON-AN-INSTALLED-BOX)"'
  wait_for "HXLAB frr=" 60 "FRR to answer the account" || return 1
  send 'echo "HX""LAB pool=$(vppctl show bpa pool | grep -c lab-a)"'
  wait_for "HXLAB pool=" 60 "the dataplane to answer the account" || return 1
  # A commit whose rollback revision could not be written still says "commit
  # complete" and warns one line above it, so an operator whose changes could
  # not be undone went unnoticed until #188 was filed.
  send 'echo "HX""LAB rollback=$(ls /etc/prisma-routing/rollback | wc -l) $(stat -c %a:%G /etc/prisma-routing/rollback)"'
  wait_for "HXLAB rollback=" 30 "the rollback store to be listed" || return 1
  # WHO owns the newest revision, and not how many there are. root's commit
  # above wrote one, so a count proves nothing about the operator's - a store
  # holding somebody else's revision is exactly the box #188 describes, where
  # the operator's own change is the one that cannot be undone.
  send 'echo "HX""LAB newest-rev=$(stat -c %U $(ls -dt /etc/prisma-routing/rollback/* | head -1))"'
  wait_for "HXLAB newest-rev=" 30 \
    "the box to say who owns the newest rollback revision" || return 1
  send 'echo "HX""LAB section=ro"'
  wait_for "HXLAB section=ro" 30 \
    "the marker that ends the administrator's session" || return 1
  send "exit"
  wait_for "back in the Prisma Routing VTY" 60 \
    "the shell escape to return to the VTY" || return 1
  send "exit"
  wait_for "$NEW_HOST login:" 120 \
    "the getty to come back after the administrator logs out" || return 1

  # ------------------------------------------- the account with no role at all
  echo "  -- and the account with no role is held to it"
  send "$RO_USER"
  wait_for "Password:" 60 \
    "login to ask the read-only account for its password" || return 1
  send "$RO_PW"
  wait_for "Prisma Routing VTY" 180 \
    "the read-only account to log in and land in the VTY" || return 1
  send "configure"
  wait_for "configure: permission denied for a read-only account" 60 \
    "the box to refuse configuration to an account with no role" || return 1
  send "start shell"
  wait_for "start shell: permission denied for a read-only account" 60 \
    "the box to refuse the shell escape to an account with no role" || return 1
  # The other half: a role that refuses everything is not a role, it is a
  # broken VTY. Reading needs the dataplane socket and FRR's, both group-owned
  # - so this is where the memberships every account gets, role or none, are
  # proved on an installed box.
  send "show running-config"
  # Rewound between the two, deliberately. They are two halves of ONE answer
  # and they arrive in an order this driver must not depend on: waiting for
  # whichever the VTY happens to print first would step the reader past the
  # other and time out on an answer that was already on the screen.
  _cfg_at=$CONSOLE_POS
  wait_for "router bgp 65000" 120 \
    "the read-only account to read the routing configuration back from FRR" \
    || return 1
  CONSOLE_POS=$_cfg_at
  wait_for "cgnat pool lab-a" 60 \
    "the read-only account to read the pool back from the dataplane" || return 1
  send "exit"
  wait_for "$NEW_HOST login:" 120 \
    "the getty to come back after the read-only account logs out" || return 1

  # ------------------- a 'commit confirmed' with nobody logged in to enforce it
  #
  # The one case the feature exists for, and the one it did not cover (#212).
  # The deadline used to live in the prismash process that made the commit, so
  # the operator logging out cancelled it - and the failure it guards against
  # is precisely the one that ends that session.
  #
  # Nothing below prompts the box. Between the logout and the box's own
  # sentence there is no prismash on this machine at all, which is the whole
  # point: a lab that had to go and trigger the revert would be performing the
  # step the product is missing.
  echo "  -- a commit nobody confirms, made by a session that then ends"
  send "root"
  wait_for "Password:" 60 "login to ask for root's password again" || return 1
  send "$LOGIN_PW"
  wait_for "Prisma Routing VTY" 180 "root to land in the VTY again" || return 1
  send "start shell"
  wait_for "entering /bin" 60 "root's shell escape to open" || return 1
  send 'echo "HX""LAB section=confirm"'
  wait_for "HXLAB section=confirm" 30 \
    "the marker that separates this from the read-only session" || return 1
  send "exit"
  wait_for "back in the Prisma Routing VTY" 60 \
    "the shell escape to return to the VTY" || return 1

  send "configure"
  wait_for "entering configuration mode" 60 \
    "the VTY to enter configuration mode" || return 1
  send "user $TMP_USER"
  wait_for "[edit user $TMP_USER]" 30 \
    "the block for the account nobody will confirm to open" || return 1
  send "password $TMP_HASH"
  send "role administrator"
  send "exit"
  wait_for "[edit]" 30 "that user block to close" || return 1
  # One minute, so the lab can wait it out. The mechanism is the same one a
  # ten-minute window uses; only the number differs.
  # Rewound after the wait below, deliberately, and for the reason the
  # read-only account's two halves are: the VTY prints "commit complete" and
  # what it promises about the revert in one go, so by the time a one-second
  # poll notices the first the second is already in the buffer - and wait_for
  # moves the reader past everything it has seen. Waiting for them in order
  # times out on a sentence that was on the screen before the wait started.
  _commit_at=$CONSOLE_POS
  send "commit confirmed 1"
  wait_for "commit complete" 300 "the confirmed commit to apply" || return 1
  CONSOLE_POS=$_commit_at
  # What the box PROMISES, in its own words, before anything here tests
  # whether it keeps it. The wording IS half the defect: the old one described
  # a revert that outlived the session over a product that implemented one
  # dying with it, so an operator reading it was less likely to keep a second
  # session open.
  #
  # Not a return, for the same reason the wait below is not: a box that makes
  # the wrong promise is a box this lab has to go on and catch breaking it.
  if wait_for "armed on the box" 30 \
       "the box to say where the revert it just promised lives"
  then
    CONFIRM_PROMISED=1
  else
    CONFIRM_PROMISED=0
    CONSOLE_POS=$(( $(wc -c < "$CONSOLE_LOG") + 1 ))
  fi
  send "exit"
  wait_for "prisma> " 30 "the VTY to leave configuration mode" || return 1
  send "start shell"
  wait_for "entering /bin" 60 "root's shell escape to open" || return 1
  # Out of /etc/passwd, not out of what the VTY said. The account has to be
  # there now, or "it is gone later" proves nothing.
  send 'echo "HX""LAB confirm-before=$(getent passwd '"$TMP_USER"' | cut -d: -f1)"'
  wait_for "HXLAB confirm-before=" 30 \
    "the box to say the account was really created" || return 1
  # And whether the deadline reached the DISK, asked while the window is open.
  # This is the whole of #212 in one stat: a deadline that exists only inside
  # the process that made the commit leaves nothing here, and the assertion
  # about the account being gone later would otherwise be the first place that
  # shows.
  send 'echo "HX""LAB confirm-armed=$(ls /etc/prisma-routing/confirm-pending 2>/dev/null | wc -l)"'
  wait_for "HXLAB confirm-armed=" 30 \
    "the box to say whether the deadline reached the disk" || return 1
  send "exit"
  wait_for "back in the Prisma Routing VTY" 60 \
    "the shell escape to return to the VTY" || return 1
  send "exit"
  wait_for "$NEW_HOST login:" 120 \
    "the VTY to log root out, ending the session that made the commit" || return 1

  # From here nothing is typed. The box is alone with a change nobody
  # confirmed, and it either says this on its own or it does not.
  echo "  -- nobody is logged in; waiting for the box to act on its own"
  # wait_said: this is prose the VTY wraps, and at `commit confirmed 1` the
  # fold lands inside this very needle.
  if wait_said "put the previous configuration back" 180 \
       "the box to roll back a commit nobody confirmed, with nobody logged in"
  then
    CONFIRM_ANNOUNCED=1
  else
    # NOT a return. The console driver has not failed - the product has, and
    # the assertion that says so needs the session that comes next. Returning
    # here would report this as "nothing was proved" and take every other
    # assertion in this lab down with it.
    CONFIRM_ANNOUNCED=0
    CONSOLE_POS=$(( $(wc -c < "$CONSOLE_LOG") + 1 ))
  fi

  send ""
  wait_for "$NEW_HOST login:" 120 \
    "the getty to draw a login prompt after the wait" || return 1
  send "root"
  wait_for "Password:" 60 "login to ask for root's password" || return 1
  send "$LOGIN_PW"
  wait_for "Prisma Routing VTY" 180 \
    "root to log back in after the window has passed" || return 1
  send "start shell"
  wait_for "entering /bin" 60 "root's shell escape to open" || return 1
  send 'echo "HX""LAB confirm-after=$(getent passwd '"$TMP_USER"' | cut -d: -f1)"'
  wait_for "HXLAB confirm-after=" 30 \
    "the box to say whether the account is still there" || return 1
  # What systemd thinks it is running, asked of systemd. A unit that was
  # installed and never enabled is indistinguishable from one that works, on
  # a feature nobody notices working.
  send 'echo "HX""LAB confirm-unit=$(systemctl is-enabled prisma-routing-confirm.timer 2>&1) $(systemctl is-active prisma-routing-confirm.timer 2>&1)"'
  wait_for "HXLAB confirm-unit=" 30 \
    "systemd to say whether the timer is enabled and running" || return 1
  send 'echo "HX""LAB confirm-left=$(ls /etc/prisma-routing/confirm-pending 2>/dev/null | wc -l)"'
  wait_for "HXLAB confirm-left=" 30 \
    "the box to say whether anything is still pending" || return 1
  return 0
}

# =========================================================================
# The records mirror, and the disk that dies under it (#83)
# =========================================================================
#
# From here to the exit below is "install-iso.sh mirror". It uses the console
# driver above and nothing else: the array was built by the INSTALLER in phase
# 1, and the only thing this section does to the disks is stop passing one of
# them to qemu - which is what a disk failing looks like from inside the box.
#
# Three boots, because there are three questions and each one needs the
# machine in a different state:
#
#   both disks     the box comes up on a healthy mirror and writes a record,
#   mirror pulled  the box still boots, still mounts the records, and SAYS the
#                  mirror is degraded - the state that is otherwise invisible,
#   system pulled  the measured cost of mirroring the records and not the
#                  system: what the survivor does on its own.
if [ "$MODE" = mirror ]; then

# What the box says about its own disks, asked of the KERNEL and of the
# mount table - not of the installer's console output, which says what the
# installer meant to do, and not of mdadm.conf, which says what it was told.
read_disks() {  # <prefix for the markers>
  send 'echo "HX""LAB '"$1"'rec=$(findmnt -no SOURCE --target /var/log/prisma-routing)"'
  wait_for "HXLAB $1rec=" 60 "the box to say where the records are" || return 1
  send 'echo "HX""LAB '"$1"'md=$(grep -E "^md[0-9]|blocks " /proc/mdstat | tr "\n" "/")"'
  wait_for "HXLAB $1md=" 30 "the kernel to describe its arrays" || return 1
}

# The first boot: a healthy mirror, and a record written onto it by the box.
#
# The record matters more than the array does. An ext4 that reached the second
# disk during mkfs proves the array existed at install time; a line written a
# reboot later, through the running box, is the only thing that proves the
# mirror is still copying what this appliance writes.
healthy_session() {
  login_root "the installed box to boot with both disks attached" || return 1
  read_disks "" || return 1
  send 'echo "HX""LAB wrote=$(echo prisma-record-42 > /var/log/prisma-routing/lab-record.txt; sync; cat /var/log/prisma-routing/lab-record.txt)"'
  wait_for "HXLAB wrote=prisma-record-42" 60 \
    "the box to write a line onto the mirrored volume" || return 1
  send "sync"
  send "systemctl poweroff"
  return 0
}

# The second boot: the same disk image, with the mirror disk simply not
# passed to qemu. Nothing was edited, nothing was zeroed - from inside the
# box this is a disk that is no longer there.
degraded_session() {
  login_root "the box to boot with the mirror disk GONE" || return 1
  read_disks "dg" || return 1
  # The record written by the previous boot, read back off the survivor. This
  # is the question the whole feature exists to answer.
  send 'echo "HX""LAB dgrecord=$(cat /var/log/prisma-routing/lab-record.txt 2>&1)"'
  wait_for "HXLAB dgrecord=" 60 \
    "the box to read back the record it wrote before the disk died" || return 1
  # The self-test again, on a settled box. At boot it may run while the kernel
  # is still waiting out its timeout on the missing leg, and what is being
  # asserted is what the product SAYS about a degraded mirror, not how fast
  # the kernel gives up on a disk.
  send 'echo "HX""LAB selftest-degraded"'
  wait_for "HXLAB selftest-degraded" 30 "the marker before the re-run" || return 1
  send "/usr/lib/prisma-routing/selftest"
  wait_for "====================" 240 "the self-test to finish" || return 1
  send "sync"
  send "systemctl poweroff"
  return 0
}

echo
echo "=========== PHASE 2: both disks, and a record written ==========="
CONSOLE_LOG=/tmp/mirror-healthy.log
CONSOLE_CLEAN=/tmp/mirror-healthy-clean.log
boot_vm "${MIRROR_BOOT_SECS:-900}" $MGMT_NET \
  -drive file="$DISK",format=qcow2,if=virtio \
  -drive file="$DISK2",format=qcow2,if=virtio
HEALTHY_DRIVEN=1
healthy_session || { HEALTHY_DRIVEN=0; fail=1; }
stop_vm 120
sed -n '/boot self-test/,/=====$/p' "$CONSOLE_CLEAN" | head -40

HEALTHY_ST=$CONSOLE_CLEAN
if grep -q "\[ ok \] records mirrored on 2 disks" "$HEALTHY_ST"; then
  echo "  [ ok ] the box reports its records on two disks, unprompted, at boot"
  grep -o "records mirrored on.*" "$HEALTHY_ST" | head -1 | sed 's/^/         /'
else
  echo "  [FAIL] the box came up on a mirror and said nothing about it. A"
  echo "         mirror nobody is told about is a mirror that silently becomes"
  echo "         one disk, and then zero"
  grep -iE "records|mirror|md0" "$HEALTHY_ST" | tail -6 | sed 's/^/         /'
  fail=1
fi
H_REC=$(mfield "$HEALTHY_ST" rec)
H_MD=$(mfield "$HEALTHY_ST" md)
H_WROTE=$(mfield "$HEALTHY_ST" wrote)
echo "  [meas] records mounted from: ${H_REC:-<no answer>}"
echo "  [meas] /proc/mdstat:         ${H_MD:-<no answer>}"
case "${H_REC:-}" in
  /dev/md*)
    echo "  [ ok ] and the volume it writes them to is the array, not a disk" ;;
  *)
    echo "  [FAIL] /var/log/prisma-routing is on '${H_REC:-nothing}', which is not"
    echo "         the array. The records are being written to one disk on a"
    echo "         box that was installed with two"
    fail=1 ;;
esac
if [ "$H_WROTE" = prisma-record-42 ]; then
  echo "  [ ok ] and a record written through the running box lands on it"
else
  echo "  [FAIL] the box could not write a record onto the records volume:"
  echo "         '${H_WROTE:-<no answer>}'"
  fail=1
fi

echo
echo "=========== PHASE 3: the mirror disk is GONE ==========="
# Not corrupted, not zeroed: not passed to qemu. That is what a disk failing
# looks like from inside the box, and it is the state no unit test can reach.
CONSOLE_LOG=/tmp/mirror-degraded.log
CONSOLE_CLEAN=/tmp/mirror-degraded-clean.log
boot_vm "${MIRROR_BOOT_SECS:-900}" $MGMT_NET \
  -drive file="$DISK",format=qcow2,if=virtio
DEGRADED_DRIVEN=1
degraded_session || { DEGRADED_DRIVEN=0; fail=1; }
stop_vm 120

DEG_ST=$CONSOLE_CLEAN
D_REC=$(mfield "$DEG_ST" dgrec)
D_MD=$(mfield "$DEG_ST" dgmd)
D_RECORD=$(mfield "$DEG_ST" dgrecord)
echo "  [meas] records mounted from: ${D_REC:-<no answer>}"
echo "  [meas] /proc/mdstat:         ${D_MD:-<no answer>}"
# What the box said at BOOT, before anybody logged in, and what it said once
# settled. The first is what an operator sees on the console of a box that
# came up with a dead disk; they are reported separately because a report that
# merges them cannot say which one was silent.
sed -n '1,/^HXLAB selftest-degraded/p' "$DEG_ST" > /tmp/deg-boot.log
sed -n '/^HXLAB selftest-degraded/,$p' "$DEG_ST" > /tmp/deg-settled.log
echo "  [meas] at boot, before anyone logged in, the self-test said:"
grep -iE "records|mirror" /tmp/deg-boot.log | tail -4 | sed 's/^/         /'

if [ "$DEGRADED_DRIVEN" = 1 ]; then
  if grep -q "\[FAIL\] the records mirror is DEGRADED" /tmp/deg-settled.log; then
    echo "  [ ok ] the box says its mirror is degraded, and says it as a"
    echo "         FAILED check rather than in passing:"
    grep -o "the records mirror is DEGRADED.*" /tmp/deg-settled.log | head -1 \
      | sed 's/^/         /'
  else
    echo "  [FAIL] a disk died under this box and its self-test does not say"
    echo "         so. The volume is still mounted, the records are still"
    echo "         being written and the box still forwards - and the next"
    echo "         failure loses the year it is required to keep"
    grep -iE "records|mirror|md0" /tmp/deg-settled.log | tail -6 | sed 's/^/         /'
    fail=1
  fi
  case "${D_REC:-}" in
    /dev/md*)
      echo "  [ ok ] and the records are still mounted, from the array running"
      echo "         on one leg: a dead mirror disk is not an outage" ;;
    *)
      echo "  [FAIL] with one disk gone the records are on '${D_REC:-nothing}'."
      echo "         The box boots - fstab says nofail - and writes its records"
      echo "         somewhere else, which is the arrangement the volume"
      echo "         replaced"
      fail=1 ;;
  esac
  if [ "$D_RECORD" = prisma-record-42 ]; then
    echo "  [ ok ] and the record written before the disk died is still there:"
    echo "         the copy survived the disk"
  else
    echo "  [FAIL] the record this box wrote before the disk died reads back as"
    echo "         '${D_RECORD:-<no answer>}'. The mirror held nothing"
    fail=1
  fi
fi

echo
echo "=========== THE SURVIVOR, READ ON ANOTHER MACHINE ==========="
# The box is off. This is the mirror disk alone, in a container that is not it,
# read the way an operator with a dead system disk is told to read it: assemble
# the array from the one leg and mount it.
#
# The record it is asked for was written by the RUNNING BOX two boots ago, not
# by mkfs - which is the difference between "an array existed at install time"
# and "the mirror is still copying what this appliance writes".
rm -f /tmp/prisma-mirror.raw
qemu-img convert -O raw "$DISK2" /tmp/prisma-mirror.raw 2>&1 | sed 's/^/  qemu-img: /'
SURV_LOOP=$(losetup --find --show --read-only --offset "$(( MOFF * 512 ))" \
            /tmp/prisma-mirror.raw 2>/tmp/losetup2.log) || true
SURV_RECORD=""
SURV_HOW=""
if [ -n "${SURV_LOOP:-}" ]; then
  mkdir -p /mnt/surv
  if mdadm --assemble --run /dev/md127 "$SURV_LOOP" >/tmp/assemble.log 2>&1 \
     && mount -o ro /dev/md127 /mnt/surv 2>>/tmp/assemble.log; then
    SURV_RECORD=$(cat /mnt/surv/lab-record.txt 2>/dev/null)
    SURV_HOW="mdadm --assemble, which is what the operator is told to run"
    umount /mnt/surv 2>/dev/null || true
    mdadm --stop /dev/md127 >/dev/null 2>&1 || true
  else
    echo "  [meas] mdadm --assemble did not start the array in this container:"
    sed 's/^/         /' /tmp/assemble.log | head -4
    # The fallback is not a second-best proof, it is the same filesystem
    # reached without any md at all - and it says whether the assemble failing
    # was about the data or about the container.
    if [ -n "${MDATAOFF:-}" ] \
       && mount -o loop,ro,offset="$(( (MOFF + MDATAOFF) * 512 ))" \
            /tmp/prisma-mirror.raw /mnt/surv 2>>/tmp/assemble.log; then
      SURV_RECORD=$(cat /mnt/surv/lab-record.txt 2>/dev/null)
      SURV_HOW="mounted straight off the leg at its data offset, with no array"
      umount /mnt/surv 2>/dev/null || true
    fi
  fi
  losetup -d "$SURV_LOOP" 2>/dev/null || true
fi
if [ "$SURV_RECORD" = prisma-record-42 ]; then
  echo "  [ ok ] the record the running box wrote is on the mirror disk, read"
  echo "         with the box switched off: $SURV_HOW"
else
  echo "  [FAIL] the record the box wrote is not readable on the mirror disk"
  echo "         ('${SURV_RECORD:-<nothing>}'). Losing the system disk would"
  echo "         then lose the records as well, which is the whole of what the"
  echo "         second disk was bought for"
  fail=1
fi

echo
echo "=========== PHASE 4: the survivor, on its own ==========="
# The measured COST of mirroring the records and not the system. It is not an
# assertion - the box is not expected to boot from a disk no bootloader was
# ever written to - it is the number this decision was made on, taken rather
# than assumed.
CONSOLE_LOG=/tmp/mirror-survivor.log
CONSOLE_CLEAN=/tmp/mirror-survivor-clean.log
# -net none, so the firmware says what it did with the DISK. Without it qemu
# adds a NIC, iPXE takes the console asking to be configured, and the console
# this measurement is read off is a network boot attempt instead of an answer
# about the disk.
boot_vm "${SURVIVOR_SECS:-90}" -net none \
  -drive file="$DISK2",format=qcow2,if=virtio
_n=0
while kill -0 "$QEMU_PID" 2>/dev/null && [ "$_n" -lt "${SURVIVOR_SECS:-90}" ]; do
  grep -qiE "no bootable|not a bootable|boot failed|disk read error" \
    "$CONSOLE_LOG" 2>/dev/null && break
  sleep 3
  _n=$((_n + 3))
done
stop_vm 0
echo "  [meas] with only the records disk attached, the machine said:"
grep -viE "^$|^SeaBIOS|^iPXE|^Booting from|^$" "$CONSOLE_CLEAN" | tail -6 \
  | sed 's/^/         /'
if grep -qiE "bootable|boot failed" "$CONSOLE_CLEAN"; then
  echo "  [meas] so the survivor does NOT boot: losing the system disk is a"
  echo "         reinstall, and the records on this disk are read back on"
  echo "         another machine, which the section above did without"
  echo "         assembling anything"
else
  # What was actually seen on 2026-09-13: SeaBIOS printed its banner and
  # nothing else for the whole window, and the lab killed it. No bootloader,
  # no kernel, no login prompt - the machine sat at the firmware. That is the
  # same answer as a refusal and it takes longer to say, which is worth
  # recording as it stands rather than tidying into a message the firmware
  # never printed.
  echo "  [meas] no bootloader, no kernel and no prompt: the machine sat at"
  echo "         the firmware until this lab stopped it. Losing the system"
  echo "         disk is a reinstall - and the records on this disk were read"
  echo "         back above, on another machine, with that box switched off"
fi

echo
echo "=========== PHASE 5: the SECOND install, over a mirror that exists ==========="
# The install every mirrored box eventually gets. Both disks already carry md
# superblocks, so the medium's udev finds them and assembles the array before
# the installer has been typed - and a partition held by a running array is
# one sfdisk cannot rewrite.
#
# This is not a fresh install being repeated. It is the state the first four
# phases left behind, which is the only way to have it.
# shellcheck disable=SC2086
timeout "${INSTALL_SECS:-600}" qemu-system-x86_64 \
  $ACCEL $IOMMU_DEV -m "${BOOT_MEM:-8192}" -smp "$BOOT_SMP" -nographic -no-reboot \
  -cdrom "$ISO" -kernel /tmp/bf/vmlinuz -initrd /tmp/bf/initrd \
  -drive file="$DISK",format=qcow2,if=virtio \
  -drive file="$DISK2",format=qcow2,if=virtio $MGMT_NET \
  -append "boot=live components console=ttyS0,115200 systemd.show_status=1 $IOMMU_ARGS \
           prisma-routing.install=/dev/vda prisma-routing.yes \
           prisma-routing.mgmt-address=dhcp \
           prisma-routing.log-gb=2 prisma-routing.mirror=$MIRROR_DEV \
           prisma-routing.hostname=$NEW_HOST prisma-routing.timezone=$NEW_TZ \
           prisma-routing.password=$NEW_PW" \
  > /tmp/reinstall.log 2>&1
sed 's/\x1b\[[0-9;]*m//g' /tmp/reinstall.log > /tmp/reinstall-clean.log
cat /tmp/reinstall-clean.log >> "$LOG" 2>/dev/null || true
if grep -q "Prisma Routing installed to /dev/vda" /tmp/reinstall-clean.log \
   && grep -q "records mirrored on /dev/md0, 2 of 2 disks present" \
        /tmp/reinstall-clean.log; then
  echo "  [ ok ] a box that already has a records mirror can be reinstalled,"
  echo "         and comes out with a two-legged mirror again"
else
  echo "  [FAIL] the second install onto the same two disks did not finish."
  echo "         Every mirrored box gets reinstalled eventually, and this is"
  echo "         what happens when it does - the medium assembles the array"
  echo "         it finds and the installer then cannot rewrite the partitions"
  echo "         it is holding:"
  grep -iE "error|busy|sfdisk|mdadm|FAILED|partitioning did not" \
    /tmp/reinstall-clean.log | tail -8 | sed 's/^/         /'
  fail=1
fi

echo
if [ "$fail" = 0 ]; then
  echo "LAB PASSED in $(( $(date +%s) - LAB_START ))s"
  exit 0
fi
echo "LAB FAILED after $(( $(date +%s) - LAB_START ))s"
echo "--- full console in $LOG ---"
exit 1

fi
# ------------------------------------------------- end of the mirror lab

# BOOT_SECS is the ceiling on the whole driven session now - the boot, five
# logins, a re-run of the self-test and one minute in which nobody is logged
# in at all - and not a schedule. Every wait above has its own timeout and the
# machine is killed the moment the last answer is in.
#
# 1200 and not 900: the window a "commit confirmed 1" opens has to be waited
# out with no session on the box, and shortening it would test something else.
# A minute of that is the product's own deadline and the rest is two more
# logins around it.
timeout "${BOOT_SECS:-1200}" qemu-system-x86_64 \
  $ACCEL $IOMMU_DEV -m "${DISK_BOOT_MEM:-8192}" -smp "$BOOT_SMP" -nographic -no-reboot \
  -drive file="$DISK",format=qcow2,if=virtio $MGMT_NET \
  < "$CONSOLE_IN" > "$CONSOLE_LOG" 2>&1 &
QEMU_PID=$!
# Held open for the whole session. A writer that opens the pipe for each line
# and closes it hands qemu an end-of-file on the serial console in between,
# and the box stops reading.
exec 3> "$CONSOLE_IN"

CONSOLE_DRIVEN=1
# 0 until the box says, unprompted, that it put the configuration back. It is
# set here so that a driver which never reaches that point reports a silence
# rather than an unset variable.
CONFIRM_ANNOUNCED=0
# 0 until the box states, at the moment of the commit, where the revert it is
# promising actually lives.
CONFIRM_PROMISED=0
if ! drive_console; then
  CONSOLE_DRIVEN=0
  fail=1
fi

# THE BOX, FROM OUTSIDE, WHILE IT IS STILL RUNNING (#303).
#
# Taken here because the machine is alive here and nowhere else in this
# section, and taken over the network rather than asked: everything above this
# line is the box's own account of itself down a serial cable, and a box with
# no address gives exactly the same account as a box with one.
#
# 90 seconds and not one attempt. sshd is up long before the driven session
# ends, so this normally answers on the first try; the window is for the run
# where it does not, so that "the box is not reachable" is a measurement and
# not a race.
BOOT_BANNER=$(reachable_over_the_network 90)
BOOT_REACHED=$?

# The box has answered everything it is going to be asked. Killing it is not a
# shutdown test - nothing here asserts anything about how it goes down - and
# waiting out BOOT_SECS for a machine that has finished costs ten minutes of
# every run. The pause is so that the last thing it said reaches the log.
exec 3>&-
sleep 2
kill "$QEMU_PID" 2>/dev/null || true
sleep 3
pkill -9 -f qemu-system-x86_64 2>/dev/null || true
wait "$QEMU_PID" 2>/dev/null || true

# \r as well as the colours, now that assertions anchor on the start of a line.
sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' /tmp/disk.log | tr -d '\r' > /tmp/disk-clean.log
cat /tmp/disk-clean.log >> "$LOG" 2>/dev/null || true
sed -n '/boot self-test/,/=====$/p' /tmp/disk-clean.log | head -40

up=1
# "Came up and failed its checks" and "never said anything" are different
# failures needing different work, and reporting the first as the second sent
# the diagnosis to the bootloader while the real problem was VPP segfaulting
# in a restart loop, plainly printed on the same console.
if said_a_failed_verdict /tmp/disk-clean.log; then
  echo
  echo "=========== THE INSTALLED SYSTEM CAME UP AND FAILED ITS CHECKS ==========="
  sed -n '/boot self-test/,/=====$/p' /tmp/disk-clean.log \
    | grep -E "\[FAIL\]|\[warn\]" | sed 's/^/  /'
  echo "  --- what the tuner decided, and from what ---"
  grep -E "prisma-routing-tune|1G hugepages|WARNING|cmdline hugepages|reserved 1G" \
    /tmp/disk-clean.log | tail -8 | sed 's/^/  /'
  echo "  --- what the dataplane said ---"
  grep -iE "vpp.service|SEGV|Killed|Out of memory" /tmp/disk-clean.log \
    | tail -6 | sed 's/^/  /'
  fail=1
elif ! said_a_verdict /tmp/disk-clean.log; then
  echo
  echo "=========== THE INSTALLED SYSTEM DID NOT COME UP ==========="
  if grep -q "grub>" /tmp/disk-clean.log; then
    echo "  GRUB reached its own prompt, so the bootloader installed correctly"
    echo "  and then found nothing to boot: no kernel, no initrd, or no"
    echo "  grub.cfg on the disk. Check that /boot survived the copy."
  elif grep -q "Welcome to GRUB" /tmp/disk-clean.log; then
    echo "  GRUB loaded and the console then went silent. Usually the installed"
    echo "  system has no serial console configured, not that it failed to boot."
  fi
  tail -20 /tmp/disk-clean.log | sed 's/^/  /'
  up=0
  fail=1
fi
if ! grep -q "\[ ok \] VTY starts" /tmp/disk-clean.log; then
  echo "  [FAIL] the VTY did not start on the installed system"
  fail=1
fi

# The log volume must be on its OWN filesystem (#83).
#
# The assertion is not that a partition exists - it is that this volume and the
# root are DIFFERENT filesystems, which is the property that matters: a spill
# that fills must not take down the box that is writing it.
if [ "$up" = 1 ]; then
  if grep -q "log volume on its own filesystem" /tmp/disk-clean.log; then
    echo "  [ ok ] the log volume is on its own filesystem"
    grep -o "log volume on its own filesystem.*" /tmp/disk-clean.log | head -1 \
      | sed 's/^/         /'
  else
    echo "  [FAIL] the log volume shares a filesystem with the root"
    grep -A2 "are on the ROOT filesystem" /tmp/disk-clean.log | head -3 \
      | sed 's/^/         /'
    fail=1
  fi
fi

# ------- the network this box comes back on, with nothing typed (#303) -------
#
# THE ACCEPTANCE. Measured on a 0.72.0 appliance and escalated three times: the
# installer wrote nothing whatever under /etc/network, so a box installed from
# the published medium booted with its card DOWN and no address, was reachable
# at its console and nowhere else, and what the operator typed at that console
# - `ip link set up`, `ip addr add`, `ip route add` - did not survive the next
# reboot.
#
# Nothing in this lab passes a management answer to the installer and nothing
# in it types an `ip` command. The install above is the one the release gate
# runs, with the same command line it has always had, so what is asserted here
# is the DEFAULT: an operator who answers nothing gets a box that comes back.
#
# The interface name is read off the box and never assumed. It is whatever
# predictable naming calls qemu's card, the installer chose it from what the
# machine had, and a lab that spelled it out would be asserting against its
# own guess (#161).
if [ "$up" = 1 ]; then
  MGMT_IF=$(sed -n 's/^HXLAB mgmt-if=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  MGMT_ADDR=$(sed -n 's/^HXLAB mgmt-addr=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  MGMT_ROUTE=$(sed -n 's/^HXLAB mgmt-route=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  MGMT_UNIT=$(sed -n 's/^HXLAB mgmt-unit=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  # WAS THE BOX ASKED AT ALL, which is a different thing from what it
  # answered. Measured on the wrong-password control, whose driver never gets
  # past the login: the four markers above were absent, and the three checks
  # below read that absence as "this box has no management network" and said
  # so in as many words about a box that had one. A lab that cannot tell "the
  # box never answered" from "the box answered badly" sends whoever reads it
  # to the wrong place, which is the whole argument behind that control
  # existing. The marker LINE, not its value: an empty value is an answer.
  MGMT_ASKED=$(grep -c "^HXLAB mgmt-if=" /tmp/disk-clean.log)
  if [ "$MGMT_ASKED" = 0 ]; then
    echo "  [FAIL] the box was never asked what network it is on, so nothing"
    echo "         here says whether it has one. The console driver did not"
    echo "         reach the shell; the two checks below it were taken over"
    echo "         the network and stand on their own"
    fail=1
  elif [ -n "$MGMT_IF" ]; then
    echo "  [ ok ] the installed box was given a management card: $MGMT_IF"
  else
    echo "  [FAIL] the installed box has no /etc/network/interfaces.d/prisma-routing"
    echo "         naming a card, so nothing configures an interface at boot."
    echo "         This is the box of #303: it comes back from every reboot"
    echo "         down and unreachable, and the console is the only way in"
    fail=1
  fi
  # An address, in the shape of one. The failure being caught is the ABSENCE
  # of any address at all, which is what a box with no stanza has; the exact
  # value is qemu's to choose and is printed rather than demanded.
  #
  # Skipped when the box was never asked, for the reason above: silence from a
  # driver that never logged in is not a reading about the box.
  if [ "$MGMT_ASKED" != 0 ]; then
    case "$MGMT_ADDR" in
      *[0-9].[0-9]*/[0-9]*)
        echo "  [ ok ] and it came up carrying an address, with nothing typed:"
        echo "         $MGMT_ADDR" ;;
      *)
        echo "  [FAIL] the management card carries no IPv4 address"
        echo "         ('${MGMT_ADDR:-<empty>}'). The box booted, and it"
        echo "         cannot be reached over the network"
        fail=1 ;;
    esac
    case "$MGMT_ROUTE" in
      default*)
        echo "  [ ok ] and a default route, so it is reachable off its subnet:"
        echo "         $MGMT_ROUTE" ;;
      *)
        echo "  [FAIL] the box has no default route ('${MGMT_ROUTE:-<none>}'),"
        echo "         so only its own subnet can reach it"
        fail=1 ;;
    esac
    # What systemd did with the file, asked of systemd. A stanza in a
    # directory nothing sources and a networking.service that failed both
    # leave a box with a perfect configuration file and no address, and they
    # are identical from the file alone.
    case "$MGMT_UNIT" in
      "enabled active")
        echo "  [ ok ] and networking.service is enabled and ran: $MGMT_UNIT" ;;
      *)
        echo "  [FAIL] networking.service is '${MGMT_UNIT:-<unread>}' on the"
        echo "         installed box, so whatever is in interfaces.d was not"
        echo "         applied by anything"
        fail=1 ;;
    esac
  fi
  # ---- and the VTY could reach something, typed as an operator types it ----
  PROBE_GW=$(sed -n 's/^HXLAB probe-gw=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  PROBE_PING=$(sed -n 's/^HXLAB probe-ping=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  PROBE_REFUSED=$(sed -n 's/^HXLAB probe-refused=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  PROBE_TOOLS=$(sed -n 's/^HXLAB probe-tools=//p' /tmp/disk-clean.log | head -1 | tr -d '\r' | sed 's/ *$//')
  if [ -z "$PROBE_TOOLS" ]; then
    echo "  [ ok ] the image carries ping, traceroute and mtr, which the VTY's"
    echo "         own statements run"
  else
    echo "  [FAIL] the installed image has no: $PROBE_TOOLS"
    echo "         prismash offers a statement for each, and refuses the ones"
    echo "         whose tool is not here: that is a promise the medium does"
    echo "         not keep"
    fail=1
  fi
  case "$PROBE_PING" in
    ""|0)
      echo "  [FAIL] 'ping $PROBE_GW count 2' typed at this box's VTY brought"
      echo "         back no reply at all ('${PROBE_PING:-<unread>}' replies)."
      echo "         That gateway is the one its own default route points at"
      fail=1 ;;
    *)
      echo "  [ ok ] and 'ping <gateway> count 2' at the VTY brought back"
      echo "         $PROBE_PING replies, from $PROBE_GW" ;;
  esac
  if [ "$PROBE_REFUSED" = 1 ]; then
    echo "  [ ok ] and a count this VTY does not take is refused at the"
    echo "         prompt, before any tool is run"
  else
    echo "  [FAIL] 'ping 1.1.1.1 count 0' was not refused by the VTY on the"
    echo "         installed box ('${PROBE_REFUSED:-<unread>}' matches). A"
    echo "         statement that only ever says yes is one nobody has seen"
    echo "         decide"
    fail=1
  fi

  # AND IT ANSWERED FROM OUTSIDE. This is the assertion the other five cannot
  # make: they are the box talking about itself on a serial line, and the
  # release gate has been reading those for the whole life of a product whose
  # boxes had no network at all.
  if [ "$BOOT_REACHED" = 0 ]; then
    echo "  [ ok ] and it answered a TCP connection from outside, over the"
    echo "         network, with nothing typed to make it: $BOOT_BANNER"
  else
    echo "  [FAIL] the box does not answer on the network. A connection to"
    echo "         its sshd through qemu's forwarded port got:"
    echo "         ${BOOT_BANNER:-<nothing at all>}"
    echo "         Every other line in this section is the box's own account"
    echo "         of itself down a serial cable. This one is the only thing"
    echo "         here that an operator could not fake by standing at the"
    echo "         console, and it is what #303 is about"
    fail=1
  fi

  # And the console SAID it, before anybody logged in. The address is what
  # somebody standing at a serial console needs in order to leave the rack and
  # carry on over ssh, and on a DHCP answer it is the only place it appears.
  sed -n "1,/$NEW_HOST login:/p" /tmp/disk-clean.log > /tmp/boot-before-login.log
  if grep -q "managed on" /tmp/boot-before-login.log; then
    echo "  [ ok ] and the boot self-test said so before any login, unprompted:"
    grep -A2 "managed on" /tmp/boot-before-login.log | head -3 | sed 's/^/        /'
  else
    echo "  [FAIL] nothing on the console before the login prompt says what"
    echo "         network this box is on. On a DHCP answer that console is"
    echo "         the only place the address appears, and whoever racked the"
    echo "         box is standing in front of it"
    fail=1
  fi
fi

# ---- the card this box claimed, with nobody having typed a name (#378) ----
#
# THE ASSERTION THE RELEASE GATE DID NOT MAKE. The gate has installed to a
# disk and booted it since #119, and everything it asserted about the result
# was about a box that could not forward - because nothing in this lab names a
# dataplane interface, and until #378 nothing in the product named one either.
# So "the medium produces a working appliance" was proved as far as "it boots,
# it answers, somebody can log in", and stopped one step short of the thing
# the appliance exists to do.
#
# NOTHING HERE PERFORMS THE STEP. This lab does not write tune.env, does not
# bind a driver, does not create a pair and does not type `dataplane
# interface`. PHASE 4 does type it, and that is a different measurement: it
# asks whether a box TOLD to take a card can. This asks whether a box nobody
# told took one anyway, which is the whole of #378 and cannot be measured on
# a box that was told.
#
# Read in the order a reader needs them: was anything typed, what cards does
# the box have, what did the tuner decide, what did the dataplane get. A
# failure in an early one makes the later ones unreadable rather than false,
# and saying which is which is the difference between sending somebody to the
# tuner and sending them to DPDK.
if [ "$up" = 1 ]; then
  CLAIM_TYPED=$(sed -n 's/^HXLAB claim-typed=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  CLAIM_PCI=$(sed -n 's/^HXLAB claim-pci=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  CLAIM_CONF=$(sed -n 's/^HXLAB claim-conf=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  CLAIM_NOPCI=$(sed -n 's/^HXLAB claim-nopci=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  CLAIM_DPDK=$(sed -n 's/^HXLAB claim-dpdk=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  CLAIM_DPIF=$(sed -n 's/^HXLAB claim-dpif=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  CLAIM_RXQ=$(sed -n 's/^HXLAB claim-rxq=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  # WAS THE BOX ASKED AT ALL. The same distinction the management block above
  # makes and for the same measured reason: on the wrong-password control the
  # driver never reaches a shell, and an absent marker read as an answer would
  # report "this box claimed nothing" about a box nobody questioned.
  CLAIM_ASKED=$(grep -c "^HXLAB claim-typed=" /tmp/disk-clean.log)
  # How many network cards this box has, counted from the marker rather than
  # from the qemu line that attached them. A lab that counted its own command
  # line would assert against its own guess (#161), and this one has a second
  # reason: the count has to be true after the claim, when the netdevs are
  # gone.
  CLAIM_NICS=$(printf '%s' "$CLAIM_PCI" | tr ' ' '\n' | grep -c .)
  # EACH VALUE ON A ROW OF ITS OWN, and the reason is arithmetic rather than
  # taste: every one of these is a LIST whose width grows with the number of
  # cards. A PCI address and a driver name is thirty columns, so a four-card
  # box put 120 of them on one row - and a fold in the middle of an address is
  # the one thing on this screen somebody copies (#367).
  # ONE CARD PER ROW and not one list. A PCI address with a driver name is
  # thirty columns, so two of them never fit together and a four-card box is
  # 110 - and an address folded mid-token is the one thing here somebody
  # copies into a command.
  echo "  -- cards:"
  printf '%s\n' "${CLAIM_PCI:-<none read>}" | tr ' ' '\n' | grep . \
    | sed 's/^/       /'

  echo "  -- startup.conf names:"
  echo "       ${CLAIM_CONF:-<no device>}"
  echo "  -- the dataplane holds:"
  echo "       ${CLAIM_DPIF:-<nothing>}"

  if [ "$CLAIM_ASKED" = 0 ]; then
    echo "  [FAIL] the box was never asked what it claimed, so nothing here"
    echo "         says whether it claimed anything. The console driver did"
    echo "         not reach the shell"
    fail=1
  elif [ "${CLAIM_TYPED:-0}" != 0 ]; then
    # The control on the whole block. Not a pass and not a product defect:
    # it is this lab having typed, which would make every line below it a
    # measurement of the wrong box.
    echo "  [FAIL] something named a dataplane card in this box's tune.env,"
    echo "         so what it claimed is not what it chose. Nothing in this"
    echo "         lab writes that file: find what did before reading the"
    echo "         checks below, which are now about a box that was told"
    fail=1
  elif [ "$CLAIM_NICS" = 0 ]; then
    # A real answer about a machine with nothing to claim, and not a defect.
    # It is also not this lab's box - MGMT_NET attaches one card at a fixed
    # slot - so it says so rather than passing quietly.
    echo "  [FAIL] this box reports no PCI network card at all, so there is"
    echo "         nothing here for the dataplane to have claimed. The lab"
    echo "         attaches one card at a fixed slot, so either it did not"
    echo "         arrive or the reading is broken"
    fail=1
  else
    echo "  [ ok ] the box has $CLAIM_NICS network card(s) and nothing named"
    echo "         one to it: what it claimed below, it claimed by itself"
    # 1. THE TUNER DECIDED. no-pci is the old default and is now the defect:
    # it is the box computing an allowlist that claims nothing on a machine
    # that has a card.
    if [ "${CLAIM_DPDK:-0}" != 0 ]; then
      # THE ONE STATE THIS BLOCK CANNOT JUDGE, and it says so rather than
      # passing. Without hugepages the tuner turns DPDK off on purpose, so no
      # card can be claimed by anything and the box is correct - but #378's
      # whole claim is unmeasured on such a boot, and a gate that goes green
      # without measuring the thing it exists for is the defect class this
      # repository is organised against. If the gate's box lands here, these
      # probes belong on the boot after it.
      echo "  [FAIL] DPDK is off on this boot, so nothing here measured"
      echo "         whether the box claims a card. That is the conservative"
      echo "         first-boot profile and it is correct; it is also the one"
      echo "         boot on which #378 cannot be seen. Move these probes to"
      echo "         the boot after this one."
      fail=1
    elif [ "${CLAIM_NOPCI:-0}" != 0 ]; then
      echo "  [FAIL] startup.conf says no-pci on a box with $CLAIM_NICS card(s):"
      echo "         the tuner claimed nothing, so the dataplane was given no"
      echo "         device and the box forwards nothing. This is the state a"
      echo "         fresh install came up in before #378, and the operator"
      echo "         had to name a card to leave it"
      fail=1
    elif [ -z "$CLAIM_CONF" ]; then
      echo "  [FAIL] startup.conf names no device and does not say no-pci, so"
      echo "         DPDK probes and takes whatever it can bind. That is the"
      echo "         box of #123, and an allowlist is the thing #378 changed"
      echo "         the CONTENT of, never whether there is one"
      fail=1
    else
      echo "  [ ok ] and the tuner computed an allowlist, naming:"
      echo "         $CLAIM_CONF"
    fi
    # 2. AND THE DATAPLANE GOT IT. A device named in a file is not a device
    # the dataplane has: that gap is #283, where an operator did exactly what
    # they were told and still forwarded nothing, and it is the gap this
    # whole change would automate onto every box if it were left unasserted.
    if [ -n "$CLAIM_DPIF" ]; then
      echo "  [ ok ] and the dataplane holds it, with nobody having asked:"
      printf '%s\n' "$CLAIM_DPIF" | tr ' ' '\n' | grep . \
        | sed 's/^/         /'
      # AND THE PORT STARTED. Held is not started: a port whose driver
      # refused it sits in `error` state and carries nothing, while every
      # other reading in this block stays true (#382). Automating the claim
      # onto every box would automate that state too if nothing here looked.
      _cerr=""
      for _e in $CLAIM_DPIF; do
        case "$_e" in *:error:*) _cerr="$_cerr ${_e%%:*}" ;; esac
      done
      if [ -n "$_cerr" ]; then
        echo "  [FAIL] and the card it claimed is in error state:$_cerr"
        echo "         The box took a card by itself and its driver refused"
        echo "         to start the port, so it forwards nothing and every"
        echo "         other line above is still true. 'journalctl -u vpp'"
        echo "         has the driver's reason."
        fail=1
      else
        echo "  [ ok ] and no port it claimed is in error state"
      fi
    else
      echo "  [FAIL] the dataplane holds no interface, and startup.conf"
      echo "         names:"
      echo "         ${CLAIM_CONF:-<no device>}"
      echo "         The box decided to claim a card and did not get it:"
      echo "         either the bind did not take or DPDK refused the device."
      echo "         The cards and their drivers are printed above, and the"
      echo "         VPP journal says which"
      fail=1
    fi
    # 3. AND THE CARD IS OFF ITS KERNEL DRIVER. Read from the same marker as
    # the count, because a driver is what the claim actually changes: DPDK
    # cannot take a device through ixgbe, i40e or virtio-pci, so a card still
    # on one is a card the box named and never took (#283).
    case "$CLAIM_PCI" in
      *:vfio-pci*|*:uio_pci_generic*|*:igb_uio*)
        echo "  [ ok ] and a card is on a driver DPDK can use, so the box"
        echo "         performed the bind itself" ;;
      *)
        echo "  [FAIL] no card on this box is on a driver DPDK can use:"
        printf '%s\n' "${CLAIM_PCI:-<none read>}" | tr ' ' '\n' | grep . \
          | sed 's/^/         /'

        echo "         Naming a device is not having it. Nothing took the"
        echo "         card off its kernel driver, so DPDK has nothing"
        fail=1 ;;
    esac
    # 4. AND THE BOX SAID SO ON ITS OWN CONSOLE. The verdict is read through
    # the holder, never spelled here: four readers of one sentence refused a
    # correct medium twice (#311). On a box with a card, the sentence that
    # means "healthy and not forwarding" is the box reporting that #378 did
    # not happen - which is a defect a reader would otherwise have to notice
    # in prose.
    if said_a_no_forward_verdict /tmp/disk-clean.log; then
      echo "  [FAIL] the box ended its self-test saying it has no interface"
      echo "         to forward on, and it has $CLAIM_NICS card(s). That"
      echo "         sentence is correct on a box with no NIC and is this box"
      echo "         reporting it claimed nothing on one that has some"
      fail=1
    else
      echo "  [ ok ] and its own verdict does not say it has nothing to"
      echo "         forward on"
    fi
  fi

  # 5. AND IT IS STILL REACHABLE, FROM OUTSIDE. The acceptance, and the only
  # line here an operator could not fake by standing at the console.
  #
  # ON THIS BOX THE CLAIMED CARD IS THE ONE IT IS REACHED ON. The lab attaches
  # a single NIC, so #378's rule - never the last card - hands the dataplane
  # the card carrying the forwarded port, and the address survives only on the
  # linux_cp pair the dataplane keeps. So a banner here is not a repeat of the
  # #303 reading above: that one was taken from a box whose kernel still held
  # the card, and this one is taken after the box gave it away without being
  # asked. A TCP handshake completing means the packets went through VPP.
  #
  # It reuses BOOT_REACHED rather than probing again because it is the same
  # boot and the same connection: what differs is what the reading MEANS now,
  # and a second probe would be a second reader of one fact.
  if [ "$CLAIM_ASKED" != 0 ] && [ "${CLAIM_NICS:-0}" != 0 ] \
     && [ -n "$CLAIM_DPIF" ]; then
    if [ "$BOOT_REACHED" = 0 ]; then
      echo "  [ ok ] and it answers from outside while the dataplane holds"
      echo "         its card, so the box forwards and is still reachable"
    else
      echo "  [FAIL] the box claimed its card and does not answer on the"
      echo "         network. It ate the port it is reached on, which on a"
      echo "         box with one card means somebody walks to it - and"
      echo "         nobody asked it to take the card at all"
      fail=1
    fi
  fi

  # 6. AND THE SELF-TEST READ THE QUEUES OFF THE DATAPLANE, NOT OFF THE FILE
  # (#345). `[ ok ] rx queues requested: num-rx-queues N` was the tuner's
  # request, printed green on the #330 box whose dataplane had refused the
  # card. The self-test now reads `rx: queues N (max M)` off `show
  # hardware-interfaces` for every card the dataplane holds, with the
  # driver's state beside it, and prints `rx queues: N of M on <card>`; this
  # is the one place that assertion can be made against a real card rather
  # than a fixture, and it is made against the dataplane's own answer, read
  # above through the same command. The requested count is checked by name
  # and not asserted here: on a virtio guest the tuner reads one TX queue off
  # the card and asks for one, so a warning on this box would be the tuner
  # and the card disagreeing, which is a defect in one of them and is
  # reported as one.
  #
  # THE CARD'S STATE FIRST, whatever the queue rows say. The listing this
  # guest printed on 2026-09-16 is of a card the driver REFUSED - `flags:
  # pmd-init-fail`, an Errors: block, the port in `error` state - and it
  # still carries `rx: queues 1 (max 1)`: the count VPP was configured with,
  # not one it opened. Compared by name and number alone, that box was green
  # here. So a card CLAIM_DPIF holds in `error` state fails this block
  # outright, and what is then read is whether the self-test said so too or
  # printed `as requested` over it, which is #330 again.
  #
  # WRITTEN BEFORE IT WAS RUN. The reader was written against the listing
  # this lab captured on 2026-09-16; these rows are what the next run of this
  # lab is expected to print, and the first run is the measurement.
  if [ "$CLAIM_ASKED" != 0 ] && [ -n "$CLAIM_DPIF" ]; then
    echo "  -- rx queues the dataplane was configured with (name:opened:max):"
    echo "       ${CLAIM_RXQ:-<none read>}"
    _rxerr=""
    for _e in $CLAIM_DPIF; do
      case "$_e" in *:error:*) _rxerr="$_rxerr ${_e%%:*}" ;; esac
    done
    if [ -n "$_rxerr" ]; then
      echo "  [FAIL] the card is in error state, so no queue on it is open,"
      echo "         whatever its rx row says:$_rxerr"
      _rxsaid=""
      for _nm in $_rxerr; do
        if grep -q "\[FAIL\] rx queues: none opened on $_nm" /tmp/disk-clean.log; then
          _rxsaid="$_rxsaid $_nm"
        fi
      done
      if grep -q "\[ ok \] rx queues: .* on $_nm, as requested" /tmp/disk-clean.log; then
        echo "         and the self-test printed a green rx-queues row over it,"
        echo "         which is the #330 row this reader exists to stop:"
        grep -o "\[ ok \] rx queues: .* on $_nm, as requested ([0-9]*)" /tmp/disk-clean.log \
          | head -3 | tr -d '\r' | sed 's/^/         /'
      elif [ -n "$_rxsaid" ]; then
        echo "         The self-test read the driver's state and said so, for:"
        echo "        $_rxsaid"
      else
        echo "         and the self-test printed no rx-queues row naming it:"
        grep "rx queues" /tmp/disk-clean.log | head -3 | tr -d '\r' \
          | sed 's/^/         /'
      fi
      fail=1
    elif [ -z "$CLAIM_RXQ" ]; then
      echo "  [FAIL] the dataplane holds a card and prints no rx queue row for"
      echo "         it, so the self-test has nothing to read the count off."
      echo "         Either the listing changed shape or the card is not a"
      echo "         DPDK device; 'show hardware-interfaces' on the box says"
      echo "         which, under the card's own name."
      fail=1
    else
      _rxbad=""; _rxrow=""
      for _e in $CLAIM_RXQ; do
        _nm=${_e%%:*}; _r=${_e#*:}; _n=${_r%%:*}; _m=${_r#*:}
        _row=$(grep -o "\[ ok \] rx queues: $_n of $_m on $_nm, as requested ([0-9]*)" \
                 /tmp/disk-clean.log | head -1 | tr -d '\r')
        if [ -n "$_row" ]; then
          _rxrow="$_rxrow
         $_row"
        else
          _rxbad="$_rxbad $_e"
        fi
      done
      if [ -z "$_rxbad" ]; then
        echo "  [ ok ] and the self-test read the queues the dataplane opened,"
        echo "         per card, and each agrees with the dataplane's own row:"
        printf '%s\n' "$_rxrow" | grep .
      elif grep -q "\[warn\] rx queues: .* [0-9]* requested" /tmp/disk-clean.log; then
        echo "  [FAIL] the self-test says a card opened a different count than"
        echo "         the tuner asked for:"
        grep -o "\[warn\] rx queues: .* [0-9]* requested" /tmp/disk-clean.log \
          | head -3 | tr -d '\r' | sed 's/^/         /'
        echo "         The tuner reads the card's queue count before asking,"
        echo "         so on this box that is the tuner and the card"
        echo "         disagreeing, not an operator's request"
        fail=1
      elif grep -q "rx queues requested:" /tmp/disk-clean.log; then
        echo "  [FAIL] the self-test fell back to the tuner's request on a box"
        echo "         whose dataplane holds a card and answers:"
        grep -A2 "rx queues requested:" /tmp/disk-clean.log | head -3 \
          | tr -d '\r' | sed 's/^/         /'
        echo "         The row under it says which reading it could not make"
        fail=1
      else
        echo "  [FAIL] the self-test printed no rx-queues row agreeing with the"
        echo "         dataplane for:$_rxbad"
        echo "         What it printed instead:"
        grep "rx queues" /tmp/disk-clean.log | head -3 | tr -d '\r' \
          | sed 's/^/         /'
        fail=1
      fi
    fi
  fi
fi

# And the box with ONE disk has no mirror, and is not told about one (#83).
#
# THE assertion about what the mirror costs a box that does not have one.
# Whatever "install-iso.sh mirror" proves about two disks, this is the run
# that says the other case is untouched: no array, and a self-test that does
# not mention a mirror at all - not even to say there is none, which on the
# console of a box with one disk is an alarm nobody can act on.
# The INSTALLER's half of the same claim, read off the console it printed
# while installing this box. It has exactly one disk, so it must not have said
# anything about a second one - and it did: the first run of this lab against
# the mirror work printed "records: not mirrored ... for the second disk" on a
# machine whose only disk was the target, because lsblk calls /dev/ram0 a disk
# and the table behind that line counted it.
if grep -q "records: not mirrored" /tmp/install-clean.log; then
  echo "  [FAIL] the installer told a ONE-DISK box about a second disk:"
  grep -A1 "records: not mirrored" /tmp/install-clean.log | sed 's/^/         /'
  echo "         whatever it counted is also offered in the install menu as"
  echo "         somewhere to put a router"
  fail=1
else
  echo "  [ ok ] the installer said nothing about a second disk on a box that"
  echo "         has one"
fi

if [ "$up" = 1 ]; then
  ARRAYS=$(sed -n 's/^HXLAB arrays=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
  case "${ARRAYS:-}" in
    0) echo "  [ ok ] the one-disk box has no array: the mirror costs it nothing" ;;
    "") echo "  [FAIL] the box was never asked how many arrays it has, so"
        echo "         nothing here says a one-disk install is unchanged"
        fail=1 ;;
    *) echo "  [FAIL] a one-disk install produced $ARRAYS array(s). Nobody asked"
       echo "         for a mirror on this box and the installer built one"
       fail=1 ;;
  esac
  if sed -n '/boot self-test/,/=====$/p' /tmp/disk-clean.log | grep -qi mirror; then
    echo "  [FAIL] the self-test on a one-disk box talks about a mirror:"
    sed -n '/boot self-test/,/=====$/p' /tmp/disk-clean.log | grep -i mirror \
      | sed 's/^/         /'
    echo "         an alarm about hardware this box does not have is an alarm"
    echo "         whoever reads this console learns to skip"
    fail=1
  else
    echo "  [ ok ] and its self-test says nothing about mirrors at all"
  fi
fi

# The routing daemons the VTY OFFERS, asserted on the INSTALLED box.
#
# This is the assertion that was missing, and its absence is what let #99 ship:
# lab/routing.sh proves BGP, OSPF and IS-IS by starting those daemons BY HAND,
# so the lab and the product disagreed about what runs and the lab was the
# optimistic one. Debian's /etc/frr/daemons has all three as "no", and
# frrinit.sh starts only zebra, mgmtd and staticd - so an installed box ran the
# dataplane, the VTY and static routes, and not one routing protocol.
#
# Checked here, on the disk that was actually installed, because that is the
# only place the question means anything.
if [ "$up" = 1 ]; then
  if grep -q "routing daemons up:" /tmp/disk-clean.log; then
    echo "  [ ok ] the installed box runs the daemons the VTY offers"
    grep -o "routing daemons up:.*" /tmp/disk-clean.log | head -1 | sed 's/^/         /'
  else
    echo "  [FAIL] the installed box is missing routing daemons the VTY offers"
    grep -A2 "routing daemons not running" /tmp/disk-clean.log | head -3 \
      | sed 's/^/         /'
    fail=1
  fi
fi
# The binary API, asserted on the INSTALLED box (#119).
#
# prismash creates a pool over that API. The ISO never carried the vat2 plugin
# that registers it, so an installed appliance could route and answer and never
# be configured to do CGNAT at all. Ten labs hid this by placing the plugin by
# hand, describing that as "what install.sh does on a real box".
#
# Asserted here, on the disk that was actually installed, for the same reason
# the routing daemons are.
if [ "$up" = 1 ]; then
  if grep -q "binary API registered" /tmp/disk-clean.log; then
    echo "  [ ok ] the installed box can configure a pool"
  else
    echo "  [FAIL] the installed box cannot configure a pool: the binary API"
    echo "         is not registered"
    grep -A2 "binary API NOT registered" /tmp/disk-clean.log | head -3 \
      | sed 's/^/         /'
    fail=1
  fi
fi

# Roles are only real if somebody other than root can reach the dataplane (#76).
#
# The appliance ships a drop-in on vpp.service that group-owns
# /dev/shm/global_vm, because "api-segment { gid vpp }" covers /dev/shm/vpe-api
# and not that one, and a client maps both. lab/vty.sh has been doing this by
# hand since roles were built - which is the same shape as #119, a lab
# arranging an environment the product never produced.
#
# Asserted here because the drop-in runs under systemd, and the labs start VPP
# directly: this is the only place the unit file is exercised at all.
if [ "$up" = 1 ]; then
  # What this can and cannot assert, stated rather than implied.
  #
  # It used to grep for "binary API works for a non-root account", a line the
  # self-test printed after SYNTHESISING the group with setpriv. #184 replaced
  # that with a check on the box's real accounts - and this assertion then
  # failed on a correct box, because a freshly installed one has no operator
  # account at all. The release gate refused two tags for it before the cause
  # was read out of the console rather than guessed at.
  #
  # Both halves are asserted now, and they are told apart by WHEN the
  # self-test said them: the one at boot is a box nobody has configured, the
  # one after the marker is the same program on the same box after the VTY
  # gave an account a role. A single grep over the whole console would find
  # either and could not say which - and "no accounts" being mistaken for "the
  # accounts are fine" is the entire reason that line exists.
  BOOT_ST=/tmp/selftest-boot.log
  RERUN_ST=/tmp/selftest-rerun.log
  sed -n '1,/^HXLAB selftest-rerun/p' /tmp/disk-clean.log > "$BOOT_ST"
  sed -n '/^HXLAB selftest-rerun/,$p' /tmp/disk-clean.log > "$RERUN_ST"
  if grep -q "no account on this box is in group vpp" /tmp/disk-clean.log \
     || grep -q "the binary API does not work for" /tmp/disk-clean.log; then
    echo "  [FAIL] the binary API is root-only on the installed box: every"
    echo "         operator would have to be root and roles would be decorative"
    grep -A2 "in group vpp\|does not work for" /tmp/disk-clean.log | head -3 \
      | sed 's/^/         /'
    fail=1
  else
    if grep -q "no operator account exists yet" "$BOOT_ST"; then
      echo "  [ ok ] a fresh box says it has no operator account yet, rather"
      echo "         than reporting reach nobody has"
    else
      echo "  [FAIL] the box that had just been installed said nothing about"
      echo "         having no operator account: a box with no accounts and a"
      echo "         box whose accounts are fine would read the same"
      fail=1
    fi
    # WHICH account it names, rather than that it named one. The self-test
    # proves the API with the first account it finds in group vpp, and the
    # only accounts on this box are the two the VTY made a minute ago - so
    # naming anything else would mean it found reach somewhere this lab did
    # not put it.
    API_FOR=$(sed -n 's/.*the binary API works for \([^,]*\),.*/\1/p' \
      "$RERUN_ST" | head -1)
    if [ "$API_FOR" = "$OP_USER" ] || [ "$API_FOR" = "$RO_USER" ]; then
      echo "  [ ok ] and after the VTY gave an account a role, the same"
      echo "         self-test names one of the accounts it made:"
      grep -o "the binary API works for.*" "$RERUN_ST" | head -1 \
        | sed 's/^/         /'
    elif [ "$CONSOLE_DRIVEN" = 0 ]; then
      echo "  [FAIL] and the self-test was never re-run: the driver did not"
      echo "         get as far as giving an account a role"
    else
      echo "  [FAIL] the self-test still does not name an account that reaches"
      echo "         the binary API, after the VTY created one and gave it a"
      echo "         role on this box. Either the account did not get the"
      echo "         membership, or the membership does not reach the API:"
      grep -E "no operator account|in group vpp|does not work for|\[warn\] $OP_USER" \
        "$RERUN_ST" | head -3 | sed 's/^/         /'
      fail=1
    fi
  fi
fi

# --- the role, on a box somebody receives (#189) ---
#
# Every answer below was read OFF the installed box - out of /etc/group, out of
# FRR, out of the dataplane - and not out of what the VTY printed, which is the
# lesson #161 cost: a VTY whose belief and the box's answer never met is
# exactly what #184 was.
#
# The accounts were created BY THE PRODUCT, at the console, through the VTY.
# There is no useradd, no usermod and no writing to the disk image anywhere in
# this lab. A lab that performs the product's missing step cannot notice the
# product missing it.
echo
echo "=========== THE ROLE, ON THE BOX THAT WAS JUST INSTALLED ==========="
if [ "$CONSOLE_DRIVEN" = 0 ]; then
  echo "  [FAIL] nothing about roles was proved on this box: the console"
  echo "         driver did not finish its session, and every assertion"
  echo "         below it went untested rather than passing"
  fail=1
else
  # Three sessions in one console log, told apart by a marker each one printed
  # before it logged out. Without this a refusal printed by one account would
  # satisfy an assertion about another, which is the console equivalent of
  # asserting on what the VTY printed.
  sed -n '/^HXLAB section=op/,/^HXLAB section=ro/p' /tmp/disk-clean.log \
    > /tmp/sec-op.log
  sed -n '/^HXLAB section=ro/,/^HXLAB section=confirm/p' /tmp/disk-clean.log \
    > /tmp/sec-ro.log
  field() { sed -n "s/^HXLAB $1=//p" /tmp/disk-clean.log | head -1; }
  OP_GROUPS=" $(field groups-op) "
  RO_GROUPS=" $(field groups-ro) "
  OP_SHELL=$(field shell-op)
  CONF_MODES=$(field modes)
  FRR_HITS=$(field frr)
  POOL_HITS=$(field pool)
  RB_COUNT=$(field rollback | awk '{print $1}')
  RB_MODE=$(field rollback | awk '{print $2}')
  NEWEST_REV=$(field newest-rev)
  has() { case "$2" in *" $1 "*) return 0 ;; *) return 1 ;; esac; }
  echo "  $OP_USER (role administrator) is in:$OP_GROUPS"
  echo "  $RO_USER (no role) is in:$RO_GROUPS"

  # The role groups. prisma-admin is additive on top of prisma-cgnat, because the
  # configuration directory and the rollback store are group prisma-cgnat and an
  # administrator that cannot write them cannot record the revision for the
  # change it just made.
  for g in prisma-admin prisma-cgnat; do
    if has "$g" "$OP_GROUPS"; then
      echo "  [ ok ] the account the VTY made is in $g"
    else
      echo "  [FAIL] the account the VTY made is NOT in $g: it was granted a"
      echo "         role the box will not honour, and every command it issues"
      echo "         will be refused as though it were an authorization"
      fail=1
    fi
    if has "$g" "$RO_GROUPS"; then
      echo "  [FAIL] the account with NO role is in $g: the box grants rights"
      echo "         nobody asked it to grant, and the boundary the VTY reports"
      echo "         is decoration"
      fail=1
    fi
  done
  # The reach every account needs before authorization is even the question.
  # VPP creates its sockets srwxrwxr-x root:vpp and /etc/frr is drwxr-x---
  # frr:frr, so an account outside these is refused by the SOCKET on every
  # command, the read-only ones included.
  for g in vpp frr frrvty; do
    if has "$g" "$OP_GROUPS" && has "$g" "$RO_GROUPS"; then
      echo "  [ ok ] both accounts reach $g, role or none"
    else
      echo "  [FAIL] an account the VTY created is outside $g, so it cannot"
      echo "         open the socket its role is about - which reads as an"
      echo "         authorization refusal and is not one"
      fail=1
    fi
  done
  if [ "$OP_SHELL" = /usr/bin/prismash-login ]; then
    echo "  [ ok ] and it logs into the VTY, not into a shell"
  else
    echo "  [FAIL] the account's login shell is '$OP_SHELL', so the box hands"
    echo "         whoever logs in a shell instead of the VTY"
    fail=1
  fi

  # #188, on an installed box: the installer states these modes and the VTY
  # must not narrow them. The first commit here was ROOT's, which is the case
  # that broke every later one.
  if [ "$CONF_MODES" = "2770:prisma-cgnat 2770:prisma-cgnat" ]; then
    echo "  [ ok ] root's commit left the configuration directory and the"
    echo "         rollback store 2770 group prisma-cgnat"
  else
    echo "  [FAIL] after root's commit the configuration directory and the"
    echo "         rollback store are '$CONF_MODES', not '2770:prisma-cgnat"
    echo "         2770:prisma-cgnat': every non-root operation on them fails"
    fail=1
  fi

  # The account, as itself. "commit complete" is the VTY's own sentence; these
  # two are the daemons' answers.
  if [ "${FRR_HITS:-0}" -ge 1 ] 2>/dev/null; then
    echo "  [ ok ] FRR holds the routing statement that account committed"
  else
    echo "  [FAIL] FRR holds nothing from that account: an administrator that"
    echo "         can enter a routing change and not apply it is #186, found"
    echo "         in the maintenance window after the check has said yes"
    grep -E "permission denied|frr-reload|reached FRR" /tmp/sec-op.log 2>/dev/null \
      | head -3 | sed 's/^/         /'
    fail=1
  fi
  if [ "${POOL_HITS:-0}" -ge 1 ] 2>/dev/null; then
    echo "  [ ok ] and the dataplane holds the pool it created over the"
    echo "         binary API, which is the call that is root-only unless the"
    echo "         drop-in on vpp.service ran"
  else
    echo "  [FAIL] the dataplane holds no pool from that account: on an"
    echo "         installed box the binary API is reached through a drop-in"
    echo "         that only systemd runs, and this is the only lab that has one"
    fail=1
  fi
  if [ "$NEWEST_REV" = "$OP_USER" ] && [ "$RB_MODE" = 2770:prisma-cgnat ] \
     && ! grep -q "no rollback revision could be recorded" /tmp/sec-op.log; then
    echo "  [ ok ] and the commit it made can be undone: the newest of the"
    echo "         $RB_COUNT revision(s) belongs to $NEWEST_REV, in a store"
    echo "         that is still $RB_MODE"
  else
    echo "  [FAIL] that account's commit did not record a rollback revision of"
    echo "         its own: the newest belongs to '$NEWEST_REV', not to"
    echo "         $OP_USER, and the store is '$RB_MODE'. The commit still says"
    echo "         'commit complete' and warns one line above it, so an"
    echo "         operator whose changes cannot be undone does not find out"
    grep "no rollback revision could be recorded" /tmp/sec-op.log | head -1 \
      | sed 's/^/         /'
    fail=1
  fi

  # The negative half. A role that refuses nothing is not a role.
  if grep -q "configure: permission denied for a read-only account" \
       /tmp/sec-ro.log 2>/dev/null; then
    echo "  [ ok ] the account with no role is refused configuration mode"
  else
    echo "  [FAIL] an account the VTY gave no role was let into configuration"
    echo "         mode on the installed box"
    fail=1
  fi
  if grep -q "start shell: permission denied for a read-only account" \
       /tmp/sec-ro.log 2>/dev/null; then
    echo "  [ ok ] and refused the shell escape"
  else
    echo "  [FAIL] an account with no role reached a root shell through the"
    echo "         VTY, which is every boundary above it at once"
    fail=1
  fi
  # And can still read, through the two sockets it is in the groups for.
  if grep -q "router bgp 65000" /tmp/sec-ro.log 2>/dev/null \
     && grep -q "cgnat pool lab-a" /tmp/sec-ro.log 2>/dev/null; then
    echo "  [ ok ] and can still read the box: the routing half from FRR and"
    echo "         the CGNAT half from the dataplane"
  else
    echo "  [FAIL] the account with no role cannot read the box either, so it"
    echo "         is not read-only - it is locked out, and the operator will"
    echo "         conclude the box is broken"
    fail=1
  fi

  # --- the revert that has to outlive the session that armed it (#212) ---
  #
  # Every answer here was read out of /etc/passwd and out of systemd, on the
  # installed box, in a session that did not exist when the commit was made.
  echo
  echo "  --- 'commit confirmed' with nobody logged in ---"
  CONF_BEFORE=$(field confirm-before)
  CONF_AFTER=$(field confirm-after)
  CONF_UNIT=$(field confirm-unit)
  CONF_ARMED=$(field confirm-armed)
  CONF_LEFT=$(field confirm-left)
  if [ "$CONF_BEFORE" = "$TMP_USER" ]; then
    echo "  [ ok ] the confirmed commit really applied: $TMP_USER is in"
    echo "         /etc/passwd on this box"
  else
    echo "  [FAIL] the commit did not create $TMP_USER at all, so nothing"
    echo "         below is about a revert - it is about a commit that did"
    echo "         not happen. /etc/passwd said: '${CONF_BEFORE:-<nothing>}'"
    fail=1
  fi
  if [ "${CONF_ARMED:-0}" = 1 ]; then
    echo "  [ ok ] and the deadline reached the disk: while the window was"
    echo "         open, /etc/prisma-routing/confirm-pending was on this box"
  else
    echo "  [FAIL] nothing was written to /etc/prisma-routing/confirm-pending"
    echo "         while the window was open, so the deadline exists only"
    echo "         inside the prismash process that made the commit - and that"
    echo "         process is what the change this guards against destroys"
    fail=1
  fi
  if [ "$CONFIRM_PROMISED" = 1 ]; then
    echo "  [ ok ] and it said where the revert lives, at the moment it made"
    echo "         the promise:"
    grep -o "the revert is armed on the box.*" /tmp/disk-clean.log | head -1 \
      | sed 's/^/         /'
  else
    echo "  [FAIL] the box never said where the revert it promised lives. The"
    echo "         wording is half of #212: a message describing a revert that"
    echo "         outlives the session, over a product whose revert dies with"
    echo "         it, makes an operator LESS likely to keep a second session"
    echo "         open. What it said instead:"
    grep -o "rollback .*confirm.*" /tmp/disk-clean.log | head -2 \
      | sed 's/^/         /'
    fail=1
  fi
  if [ "$CONFIRM_ANNOUNCED" = 1 ]; then
    echo "  [ ok ] and the box said, on a console nobody was logged into,"
    echo "         that it had put the previous configuration back:"
    grep -o "nobody confirmed it within.*" /tmp/disk-clean.log | head -1 \
      | sed 's/^/         /'
  else
    echo "  [FAIL] with nobody logged in, this box said NOTHING and did"
    echo "         nothing. That is #212: the deadline lived in the prismash"
    echo "         process that made the commit, so logging out cancelled it"
    echo "         - and the change it guards against is the one that ends"
    echo "         that session. It reverts in every case where it is not"
    echo "         needed and in none of the cases where it is."
    fail=1
  fi
  if [ -z "$CONF_AFTER" ]; then
    echo "  [ ok ] and the account is gone from the box that was logged back"
    echo "         into: the revert outlived the session that armed it"
  else
    echo "  [FAIL] '$CONF_AFTER' is still in /etc/passwd after the window"
    echo "         passed with nobody logged in. An operator was told this"
    echo "         box would come back on its own and it did not - which is"
    echo "         worse than not having the feature, because they are less"
    echo "         likely to have kept a second session open"
    fail=1
  fi
  case "$CONF_UNIT" in
    "enabled active")
      echo "  [ ok ] and systemd is what enforces it: prisma-routing-confirm.timer"
      echo "         is enabled and active on this box" ;;
    *)
      echo "  [FAIL] prisma-routing-confirm.timer is '${CONF_UNIT:-<no answer>}',"
      echo "         not 'enabled active'. A unit that was installed and never"
      echo "         enabled reads exactly like one that works, on a feature"
      echo "         whose whole job is to do nothing most of the time"
      fail=1 ;;
  esac
  if [ "${CONF_LEFT:-1}" = 0 ]; then
    echo "  [ ok ] and nothing is left pending, so the next boot reverts"
    echo "         nothing a second time"
  else
    echo "  [FAIL] /etc/prisma-routing/confirm-pending is still on the box after"
    echo "         the revert ran, so the box would do it again at every boot"
    fail=1
  fi

  # --- the retention log is closed when the box stops (#217) ---
  echo
  echo "  --- what systemd runs before it stops the dataplane ---"
  STOP_DRAIN=$(field stopdrain)
  if [ "${STOP_DRAIN:-0}" -ge 1 ] 2>/dev/null; then
    echo "  [ ok ] vpp.service carries the drain in ExecStop, so a planned"
    echo "         reboot or an upgrade closes every block this box holds"
    echo "         instead of leaving the allocations open in the retention"
    echo "         log for as long as it is kept"
  else
    echo "  [FAIL] systemd has no 'bpa stop-drain' in vpp.service's ExecStop"
    echo "         (grep counted '${STOP_DRAIN:-<no answer>}'). Every planned"
    echo "         reboot and every upgrade then leaves one open allocation"
    echo "         per block held, and the box answers a court order with a"
    echo "         subscriber who had already been disconnected (#217)"
    fail=1
  fi
fi

echo
# Which NIC the dataplane was allowed to claim (#123).
#
# An installed box used to generate a DPDK stanza with no device allowlist,
# because the shipped PRISMA_DATAPLANE_IF named an interface that does not exist
# under predictable names. VPP then took whatever it found - the management
# interface - and the box left the network the moment the dataplane started.
#
# Either it names a device or it claims none. An open probe is neither.
if [ "$up" = 1 ]; then
  if grep -q "no dataplane interface" /tmp/disk-clean.log \
     || grep -q "dataplane NIC: 0000:" /tmp/disk-clean.log \
     || grep -q "DPDK is off until hugepages exist" /tmp/disk-clean.log; then
    echo "  [ ok ] the dataplane claims a named NIC, or none at all"
  else
    echo "  [FAIL] the dataplane may claim any NIC it finds, including the"
    echo "         management interface this box is reached over"
    grep -A2 "may claim any NIC" /tmp/disk-clean.log | head -3 \
      | sed 's/^/         /'
    fail=1
  fi
fi

# Is the dataplane out of the OOM killer's selection on the INSTALLED box?
#
# The protection is a drop-in on vpp.service, and a drop-in that was never
# installed - or that systemd parsed and refused - is indistinguishable from
# one that is working. lab/oom.sh shows the kernel making that choice both
# ways, but it has to apply the value itself: there is no systemd in a lab
# container. Whether the unit file arrived and took effect can only be seen
# where there is one, and this is the only place in the repository that boots
# a real installed box under real systemd (#172).
#
# What is read back is the self-test's line, which it prints from
# /proc/<vpp pid>/oom_score_adj and not from the drop-in file.
if [ "$up" = 1 ]; then
  if grep -q "out of the OOM killer's selection" /tmp/disk-clean.log; then
    echo "  [ ok ] the installed dataplane is out of the OOM killer's selection"
    grep -o "dataplane out of the OOM killer's selection.*" /tmp/disk-clean.log \
      | head -1 | sed 's/^/         /'
  else
    echo "  [FAIL] the drop-in did not take effect on the installed box: the"
    echo "         dataplane can still be chosen as the victim of somebody"
    echo "         else's allocation, which is how it was killed at 19 MB"
    grep -iE "oom_score_adj|vpp.service owns no process" /tmp/disk-clean.log \
      | head -3 | sed 's/^/         /'
    fail=1
  fi
fi

# Are the dataplane's workers on cores the kernel is actually isolating, and
# what is left over for the control plane? (#172)
#
# "full tuning active: corelist-workers ..." has been on this console since the
# tuner existed, and it reports the FILE. The lines read back here report what
# the dataplane GOT: the self-test compares /etc/vpp/startup.conf against
# /proc/<vpp pid>/task/*/status and /sys/devices/system/cpu/isolated, three
# places and none of them the one that made the decision. test_selftest.sh
# shows every one of those states going red against a synthetic root; this is
# the reading with nothing synthetic, on a box that really booted with
# isolcpus on its command line - which the live ISO never does.
if [ "$up" = 1 ]; then
  if grep -q "dataplane workers hold the cores the profile names" /tmp/disk-clean.log \
     && grep -q "the kernel is isolating every one of them" /tmp/disk-clean.log; then
    echo "  [ ok ] the installed dataplane holds the cores its profile names,"
    echo "         and the kernel is isolating them"
    grep -oE "(dataplane workers hold|and the kernel is isolating).*" /tmp/disk-clean.log \
      | head -2 | sed 's/^/         /'
  else
    echo "  [FAIL] the installed box did not confirm its worker cores: either a"
    echo "         worker is on a core the kernel is not isolating, or the"
    echo "         dataplane is older than the profile it is judged against."
    echo "         Both forward and both cost throughput silently"
    grep -iE "worker core|pins workers|owns no process" /tmp/disk-clean.log \
      | head -3 | sed 's/^/         /'
    fail=1
  fi
  if grep -qE "control plane has [0-9]+ cpu|control plane has ONE cpu" /tmp/disk-clean.log; then
    echo "  [ ok ] and it says what the control plane is left with"
    grep -oE "control plane has .*" /tmp/disk-clean.log | head -1 | sed 's/^/         /'
  else
    echo "  [FAIL] the installed box never said how many cpus the control plane"
    echo "         has. That number is the whole of what this product can"
    echo "         honestly say about CPU bounds, and it said nothing"
    fail=1
  fi
fi

# The memory bound the control plane carries, read back from systemd (#172).
#
# prisma-routing.service ships MemoryMax=512M against a measured 13 MB peak and
# frr.service ships none against a measured 1.37 GB full table. Both halves are
# asserted: a MemoryMax that arrived on frr.service would turn a slow box into
# a five-second restart loop, and one that systemd parsed and refused on
# prisma-routing.service reads exactly like one that took.
mm=$(sed -n 's/^HXLAB memmax=//p' /tmp/disk-clean.log | head -1 | tr -d '\r')
case "$mm" in
  "") if [ "$up" = 1 ]; then
        echo "  [FAIL] systemd was never asked what memory bounds it applied"
        fail=1
      fi ;;
  *"hx:536870912"*"frr:infinity"*)
    echo "  [ ok ] systemd applied MemoryMax to the control plane and not to FRR"
    echo "         $mm" ;;
  *)
    echo "  [FAIL] the memory bounds systemd applied are not the shipped ones:"
    echo "         $mm"
    echo "         expected hx:536870912 (512M, prisma-routing.service) and"
    echo "         frr:infinity (deliberately unbounded - one full table costs"
    echo "         FRR 1.37 GB and a bound guessed low kills rather than"
    echo "         degrades; docs/appliance.md carries the measurement)"
    fail=1 ;;
esac

# What isolcpus does and does not do to the control plane, recorded.
#
# Not a pass or a fail: this is the kernel's behaviour, not this repository's,
# and a check nothing here can make fail is not a check. It is printed because
# it is the evidence behind the decision in docs/appliance.md not to ship a
# cpuset for the control plane.
for k in cpu-iso cpu-bgpd cpu-escape cpu-init bgpd-from isolspell; do
  v=$(sed -n "s/^HXLAB $k=//p" /tmp/disk-clean.log | head -1 | tr -d '\r')
  [ -n "$v" ] && echo "  [meas] $k: $v"
done

# Did the tuning survive the install?
#
# The evidence is the KERNEL COMMAND LINE, not the profile. isolcpus and the
# C-state parameters are written by the installer and can only be there if the
# tuning ran and update-grub picked it up - and a live system never has them,
# so their presence is proof the installed system is not the live one.
#
# The profile is deliberately NOT the test. Whether the box leaves first-boot
# mode depends on whether it has the RAM for a hugepage pool, and a small box
# staying conservative is the tuner working, not failing. An earlier version of
# this check demanded the tuned profile and failed a box that was behaving
# exactly as designed.
#
# Guarded by "up": absence of a string proves nothing when the console produced
# nothing at all. An even earlier version reported the tuning as applied on a
# boot that had shown only "Welcome to GRUB!".
if [ "$up" = 0 ]; then
  echo "  [skip] tuning: cannot be judged, the system said nothing"
else
  if grep -qE "Kernel command line:.*isolcpus=" /tmp/disk-clean.log; then
    echo "  [ ok ] the tuning reached the installed kernel command line"
  else
    echo "  [FAIL] no isolcpus on the installed kernel command line:"
    echo "         the tuning did not survive the install"
    fail=1
  fi
  if grep -qE "Kernel command line:.*max_cstate=" /tmp/disk-clean.log; then
    echo "  [ ok ] and so did the latency parameters"
  else
    echo "  [FAIL] the low-latency parameters did not survive the install"
    fail=1
  fi
  if grep -q "FIRST BOOT mode" /tmp/disk-clean.log; then
    hp=$(grep -oE "hugepages=[0-9]+" /tmp/disk-clean.log | head -1)
    echo "  [info] conservative profile (${hp:-hugepages=?}): correct for this"
    echo "         box, which has too little RAM for a 1G pool"
  else
    echo "  [ ok ] fully tuned profile in effect"
  fi
fi

# =========================================================================
# PHASE 3: the same box, switched off and started again (#303)
# =========================================================================
#
# The acceptance says "comes back after a reboot on the network it was
# installed on, with no `ip` command typed by hand", and every phase above
# this one is the FIRST boot after the install. That is not the same question.
# A box can be reachable once because something in the install left an
# interface up; the question an operator is asking is whether it is reachable
# again tomorrow, which on the box in #303 it was not - the address the
# operator typed at the console went away with the power.
#
# So this is a cold start of the same disk image, with a fresh console, and
# nothing has been typed into it. It is deliberately the LAST thing this lab
# does: the session above left the box configured, committed to and rebooted
# once, which is the state a box that has been worked on is in.
#
# Skipped when the box never came up at all, and when the session above never
# got into it. "It came back" about a machine that never arrived is a sentence
# with no subject - and the wrong-password control, whose whole job is to fail
# at the login, must not spend another seven minutes booting a box it was
# never able to talk to.
if [ "$up" = 1 ] && [ "${CONSOLE_DRIVEN:-0}" = 1 ]; then
  echo
  echo "=========== PHASE 3: the box is switched off and started again ==========="
  CONSOLE_LOG=/tmp/reboot.log
  CONSOLE_CLEAN=/tmp/reboot-clean.log
  # shellcheck disable=SC2086
  boot_vm "${REBOOT_SECS:-900}" \
    -drive file="$DISK",format=qcow2,if=virtio $MGMT_NET
  # Reached from outside BEFORE anything is typed into it. In this phase the
  # box has had nothing at all done to it since the power came back, so a
  # connection completing here is the whole of the acceptance: it came back on
  # the network by itself.
  #
  # 420 seconds because this is a cold boot and the wait is for the box, not
  # for the network: sshd starts when the box has finished starting.
  RB_BANNER=$(reachable_over_the_network 420)
  RB_REACHED=$?
  if [ "$RB_REACHED" = 0 ]; then
    echo "  [ ok ] it answered from outside after the power cycle, with"
    echo "         nothing typed at its console: $RB_BANNER"
  else
    echo "  [FAIL] after the power cycle the box does not answer on the"
    echo "         network: ${RB_BANNER:-<nothing at all>}"
    echo "         This is #303 exactly: whatever made it reachable before"
    echo "         did not survive being switched off, and the console is"
    echo "         the only way back in"
    fail=1
  fi

  if login_root "the installed box to come back from a cold start"; then
    send 'M=$(sed -n "s/^iface \([^ ]*\) .*/\1/p" /etc/network/interfaces.d/prisma-routing | head -1); echo "HX""LAB rb-if=$M"'
    wait_for "HXLAB rb-if=" 30 "the box to name its management card again" \
      || fail=1
    send 'echo "HX""LAB rb-addr=$(ip -o -4 addr show dev $M | awk "{print \$4}" | tr "\n" " ")"'
    wait_for "HXLAB rb-addr=" 30 \
      "the box to say what address it came back with" || fail=1
    send 'echo "HX""LAB rb-route=$(ip -o -4 route list default | tr "\n" "/")"'
    wait_for "HXLAB rb-route=" 30 \
      "the box to say whether it can reach anything off its subnet" || fail=1
    send "sync"
    send "systemctl poweroff"
  else
    echo "  [FAIL] the box did not come back from a cold start at all"
    fail=1
  fi
  stop_vm 60
  RB_IF=$(sed -n 's/^HXLAB rb-if=//p' "$CONSOLE_CLEAN" | head -1)
  RB_ADDR=$(sed -n 's/^HXLAB rb-addr=//p' "$CONSOLE_CLEAN" | head -1)
  RB_ROUTE=$(sed -n 's/^HXLAB rb-route=//p' "$CONSOLE_CLEAN" | head -1)
  case "$RB_ADDR" in
    *[0-9].[0-9]*/[0-9]*)
      echo "  [ ok ] it came back on the network by itself: $RB_IF $RB_ADDR"
      echo "         nothing was typed on this boot before it was asked" ;;
    *)
      echo "  [FAIL] the box came back with no address on ${RB_IF:-its card}"
      echo "         ('${RB_ADDR:-<the box was never asked>}'). This is #303:"
      echo "         whatever made it reachable the first time did not"
      echo "         survive the power cycle, and the console is the only"
      echo "         way in again"
      fail=1 ;;
  esac
  case "$RB_ROUTE" in
    default*) echo "  [ ok ] and with its default route: $RB_ROUTE" ;;
    *) echo "  [FAIL] it came back with no default route ('${RB_ROUTE:-<none>}')"
       fail=1 ;;
  esac
  # The address it came back with is the address it had. A box that comes back
  # on a DIFFERENT address is reachable and is not where anybody left it: every
  # script, every monitoring check and every ssh known-host entry points at the
  # old one. On this lab's network that is qemu's DHCP server handing out the
  # same lease, which is what a real one does too.
  if [ -n "$RB_ADDR" ] && [ "$RB_ADDR" = "${MGMT_ADDR:-}" ]; then
    echo "  [ ok ] and it is the same address it had before the power cycle"
  else
    echo "  [warn] the address changed over the power cycle:"
    echo "         before: ${MGMT_ADDR:-<unread>}"
    echo "         after:  ${RB_ADDR:-<unread>}"
  fi
fi


# =========================================================================
# PHASE 3b: 'system reboot' typed at the VTY, and the box comes back (#388)
# =========================================================================
#
# PHASE 3 above power-cycles the box the way an operator WITHOUT this feature
# had to: it drops to a shell and types `systemctl poweroff`, then the disk is
# cold-booted and found reachable. #388 is that an operator never has to leave
# the VTY to do it. So this phase types `system reboot` at the `prisma> `
# prompt instead - no shell, no systemctl - and the assertion is the one PHASE
# 3 makes: the box comes back on the network by itself.
#
# WITHOUT -no-reboot, deliberately, and it is the whole difference from every
# other boot in this lab. boot_vm passes -no-reboot so that a guest which
# reboots signals a fault by making qemu exit; here the reboot is the thing
# under test, so qemu must carry the guest down and back up in one process. A
# run that ended at the first boot would prove the command was accepted and
# nothing about whether the box returned, which is the half that matters.
#
# WHAT IT PROVES BEYOND "the command exists". The offline suite drives
# `system reboot` through a stub and reads the argv (test/test_prismash_system.py);
# what a stub cannot show is that `systemctl reboot` on a real box takes it
# down cleanly - vpp.service's ExecStop draining the connection log on the way
# (#217) - and that the box boots again to a reachable state. That is an end to
# end fact about the appliance and belongs at this level.
#
# THE ROW THE VTY PRINTS BACK is half the assertion, the way it is in PHASE 4:
# `system reboot` handed to FRR would answer in vtysh's words about a command
# it has not got, so "restarting the box" coming from the box is what says the
# statement is the VTY's own and was dispatched before the FRR pass-through.
#
# Runs only when PHASE 3 got the box back, for the reason PHASE 4 states: "it
# rebooted and came back" about a box that never arrived is a sentence with no
# subject.
if [ "$up" = 1 ] && [ "${CONSOLE_DRIVEN:-0}" = 1 ] && [ "${RB_REACHED:-1}" = 0 ]
then
  echo
  echo "=========== PHASE 3b: 'system reboot' at the VTY ==========="
  CONSOLE_LOG=/tmp/vty-reboot.log
  CONSOLE_CLEAN=/tmp/vty-reboot-clean.log
  # boot_vm's body, WITHOUT -no-reboot, because this phase's whole point is a
  # guest that reboots in place. Everything else matches boot_vm so send(),
  # wait_for() and stop_vm() work exactly as they do everywhere else.
  CONSOLE_POS=1
  : > "$CONSOLE_LOG"
  rm -f "$CONSOLE_IN"
  mkfifo "$CONSOLE_IN" || { echo "cannot create the console pipe $CONSOLE_IN"; exit 1; }
  # shellcheck disable=SC2086
  timeout "${VTY_REBOOT_SECS:-1200}" qemu-system-x86_64 \
    $ACCEL $IOMMU_DEV -m "${DISK_BOOT_MEM:-8192}" -smp "$BOOT_SMP" -nographic \
    -drive file="$DISK",format=qcow2,if=virtio $MGMT_NET \
    < "$CONSOLE_IN" > "$CONSOLE_LOG" 2>&1 &
  QEMU_PID=$!
  # Held open for the whole session, like boot_vm: a writer that opens and
  # closes the pipe per line hands qemu an end-of-file on the console between.
  exec 3> "$CONSOLE_IN"

  VR_DRIVEN=0
  # Into the VTY and NOT on to a shell: login_root ends with `start shell`,
  # and this command has to be typed at `prisma> ` to prove it is the
  # VTY's. So the login is inline here, stopping at the operational prompt.
  if wait_for "$NEW_HOST login:" 420 "the box to come up for the VTY reboot" \
     && { send "root"; wait_for "Password:" 60 \
          "the login to ask for root's password"; } \
     && { send "$LOGIN_PW"; wait_for "prisma> " 180 \
          "root's login to land at the VTY prompt"; }; then
    VR_DRIVEN=1
    send "system reboot"
    if wait_for "restarting the box" 30 \
       "the VTY to take 'system reboot' as its own command"; then
      echo "  [ ok ] the VTY took 'system reboot' and said it is restarting"
    else
      echo "  [FAIL] the VTY did not answer 'system reboot' with its own"
      echo "         'restarting the box'. Either it was handed to FRR or the"
      echo "         command is not in operational mode; $LOG has what it"
      echo "         said instead"
      fail=1
    fi
    # The box goes down and, with no -no-reboot on this qemu, comes back up in
    # the same process. The login prompt returning is the box having rebooted;
    # the wait above advanced the console position past the first boot's copy
    # of the same string, so this matches only the second one.
    if wait_for "$NEW_HOST login:" 600 \
       "the box to reboot and reach a login prompt again"; then
      echo "  [ ok ] it rebooted from the VTY and came back to a login prompt"
      # Reachable from OUTSIDE - the same acceptance PHASE 3 makes for the cold
      # start, and the whole of what #388 promises an operator: the reboot they
      # typed at the VTY brought the box back on the network. Independent of
      # the console, so it does not consume the prompt the clean shutdown below
      # logs in at.
      VR_BANNER=$(reachable_over_the_network 300)
      if [ $? = 0 ]; then
        echo "  [ ok ] and it answers from outside after a reboot typed at"
        echo "         the VTY, nothing typed at its console: $VR_BANNER"
      else
        echo "  [FAIL] after the VTY reboot the box does not answer on the"
        echo "         network: ${VR_BANNER:-<nothing at all>}. A reboot that"
        echo "         does not return is worse than the shell 'systemctl' it"
        echo "         replaces"
        fail=1
      fi
      # Shut it down cleanly for the phase after this one, logging in at the
      # prompt it just returned to rather than waiting for a fresh one.
      if { send "root"; wait_for "Password:" 60 \
           "the reboot's login to ask for root's password"; } \
         && { send "$LOGIN_PW"; wait_for "prisma> " 180 \
              "the reboot's login to land at the VTY prompt"; }; then
        send "start shell"
        if wait_for "entering /bin" 60 \
             "the shell to open for a clean poweroff"; then
        # ---------------------------------------------------------------------------
        # THE BOX WRITES ITS OWN CONFIGURATION AND READS IT BACK (#419)
        #
        # prisma-routing.service hands /etc/prisma-routing/prisma-routing.conf to
        # `prismash --load` at EVERY boot, and --load refuses a file WHOLE. The file
        # is written by `save` out of a candidate built from what the box reports
        # about itself. So a value the box produces, written out and refused on the
        # way back in, is a box that comes up with NO configuration - on a machine
        # nobody is watching.
        #
        # That has happened twice, both times caught by something else noticing:
        # #357, a range in the pool parser that also reads `show bpa config`; and
        # 0.110.0, where `[::]` - the form sshd -T gives back for an IPv6
        # ListenAddress - was refused, and every commit on an installed box failed.
        #
        # This is the direct test of the property, and it is here because this is the
        # only place in this repository where a box that was INSTALLED writes a file
        # and boots again. test/test_a_readback_survives.py is the same rule against
        # the model, in seconds and with no box; this is the same rule against the
        # box's own answers, which is the half a model cannot have.
        echo
        echo "  -- the box saves its own configuration and loads it back (#419)"
        send 'echo "HX""LAB rb-load=$(prismash -c "save" >/tmp/rb.save 2>&1; prismash --load /etc/prisma-routing/prisma-routing.conf >/tmp/rb.out 2>&1; echo rc=$?)"'
        wait_for "HXLAB rb-load=" 180 \
          "the box to save its own configuration and read it back" || fail=1
        send 'echo "HX""LAB rb-why=$(head -c 200 /tmp/rb.out 2>/dev/null | tr -d "\r" | tr "\n" "|")"'
        wait_for "HXLAB rb-why=" 60 "the box to say what --load made of its file" \
          || fail=1

        # live_field and NOT mfield, which is the third time this check was
        # put in the wrong place and the last: mfield reads the CLEANED log,
        # and that is written only when the machine stops. This check runs on
        # a box that is still up - it has to, because it asks the box a
        # question - so the value has to come off the live console. The
        # comment on live_field says exactly this, and it was there before
        # this check was written.
        RB_LOAD=$(live_field rb-load)
        RB_WHY=$(live_field rb-why)
        case "$RB_LOAD" in
          *rc=0*)
            echo "  [ ok ] the file this box wrote is one it can read: --load exited 0"
            ;;
          "")
            echo "  [FAIL] the box never answered, so nothing here says whether its"
            echo "         own configuration survives a reboot"
            fail=1 ;;
          *)
            echo "  [FAIL] the box REFUSED THE FILE IT JUST WROTE ($RB_LOAD)."
            echo "         prisma-routing.service hands that file to --load at every"
            echo "         boot and --load refuses a file whole, so this box would"
            echo "         come up with no configuration at all. A validator has been"
            echo "         put on a value the box reads back - see #419. What it said:"
            echo "         ${RB_WHY:-<nothing>}"
            fail=1 ;;
        esac

        # -- what the medium carried, on the disk it installed to (#396)
        #
        # The container stack is WHAT THIS BOX RUNS (#426). What this asks
        # is that an installed disk has the runtime, the compose file, the
        # unit and the images - all four ride the squashfs and none of them
        # is fetched at first boot, because a box in an ISP has no route to
        # a registry - and that the unit and its watcher are ENABLED while
        # vpp.service and frr.service are not. Whether the containers then
        # carry packets is PHASE 2 onwards: this box is reached over ssh,
        # configured, and reads its own configuration back with the
        # dataplane in a container throughout.
        echo
        echo "  -- the installed disk carries the container stack (#396)"
        send 'echo "HX""LAB dk-bin=$(command -v docker-compose >/dev/null 2>&1 && command -v docker >/dev/null 2>&1 && echo both || echo missing)"'
        wait_for "HXLAB dk-bin=" 60 "the box to say whether it has docker" || fail=1
        send 'echo "HX""LAB dk-files=$(test -f /etc/prisma-routing/compose.yaml && echo -n compose; test -f /usr/lib/prisma-routing/images.tar.gz && echo -n +images; test -f /lib/systemd/system/prisma-routing-containers.service && echo -n +unit)"'
        wait_for "HXLAB dk-files=" 60 "the box to list what it carries" || fail=1
        send 'echo "HX""LAB dk-enabled=$(systemctl is-enabled prisma-routing-containers.service 2>&1 | tr -d "\r")"'
        wait_for "HXLAB dk-enabled=" 60 "the box to say whether the unit is enabled" \
          || fail=1
        send 'echo "HX""LAB dk-old=$(systemctl is-enabled vpp.service 2>&1 | tr -d "\r"):$(systemctl is-enabled frr.service 2>&1 | tr -d "\r")"'
        wait_for "HXLAB dk-old=" 60 \
          "the box to say whether the units it replaces are enabled" || fail=1
        send 'echo "HX""LAB dk-timer=$(systemctl is-enabled prisma-routing-card-watch.timer 2>&1 | tr -d "\r")"'
        wait_for "HXLAB dk-timer=" 60 \
          "the box to say whether the card watcher is enabled" || fail=1

        DK_BIN=$(live_field dk-bin)
        DK_FILES=$(live_field dk-files)
        DK_ENABLED=$(live_field dk-enabled)
        if [ "$DK_BIN" = "both" ]; then
          echo "  [ ok ] docker and docker-compose are on the installed disk"
        else
          echo "  [FAIL] the installed box has no container runtime (dk-bin=${DK_BIN:-<nothing>});"
          echo "         the medium is supposed to carry docker.io and docker-compose,"
          echo "         and a box in an ISP cannot fetch them"
          fail=1
        fi
        if [ "$DK_FILES" = "compose+images+unit" ]; then
          echo "  [ ok ] compose.yaml, images.tar.gz and the unit all installed"
        else
          echo "  [FAIL] the disk is missing part of the stack (dk-files=${DK_FILES:-<nothing>});"
          echo "         expected compose+images+unit. Without images.tar.gz the unit"
          echo "         would start by pulling from a registry this box cannot reach."
          fail=1
        fi
        DK_OLD=$(live_field dk-old)
        DK_TIMER=$(live_field dk-timer)
        case "$DK_ENABLED" in
          enabled)
            echo "  [ ok ] the container stack is what this box boots" ;;
          "")
            echo "  [FAIL] the box never answered about the container unit"
            fail=1 ;;
          *)
            # cut to 40: systemd's answer when the unit is absent is a
            # whole sentence with the path in it, and printed raw it made
            # this line 120 columns wide on the control run.
            echo "  [FAIL] prisma-routing-containers.service is not 'enabled':"
            echo "         $(printf '%.40s' "$DK_ENABLED")"
            echo "         This box was installed by a release whose default"
            echo "         is the container stack, so it would boot nothing"
            echo "         at all on the dataplane side."
            fail=1 ;;
        esac
        case "$DK_OLD" in
          disabled:disabled)
            echo "  [ ok ] and vpp.service and frr.service are disabled, so"
            echo "         nothing asks systemd to run two dataplanes" ;;
          "")
            echo "  [FAIL] the box never said whether vpp.service is enabled"
            fail=1 ;;
          *)
            echo "  [FAIL] vpp.service/frr.service read '$DK_OLD' beside an"
            echo "         enabled container stack. The unit Conflicts= both,"
            echo "         so every boot asks systemd to choose and nothing"
            echo "         in this product says which wins."
            fail=1 ;;
        esac
        case "$DK_TIMER" in
          enabled)
            echo "  [ ok ] and the card watcher runs with it, so a dataplane"
            echo "         Docker gives up on hands the card back (#379)" ;;
          *)
            echo "  [FAIL] prisma-routing-card-watch.timer is"
            echo "         '${DK_TIMER:-<nothing>}': nothing would notice a"
            echo "         dataplane container that stopped, and the box"
            echo "         would sit with its card bound to DPDK and no"
            echo "         interface at all"
            fail=1 ;;
        esac
          send "sync"; send "systemctl poweroff"
        fi
      fi
    else
      echo "  [FAIL] after 'system reboot' the box did not come back to a"
      echo "         login prompt; $LOG has the console"
      fail=1
    fi
  fi
  if [ "$VR_DRIVEN" = 0 ]; then
    echo "  [FAIL] the box did not come up to a VTY prompt for the reboot test"
    fail=1
  fi
  stop_vm 60
fi


# =========================================================================
# PHASE 4: the box takes its own card, for real (#329)
# =========================================================================
#
# Handing a card to the dataplane means unbinding it from its kernel driver
# and binding it to vfio-pci or uio_pci_generic. NOTHING IN THIS REPOSITORY
# HAD EVER PERFORMED THAT BIND before this phase: lab/one-nic.sh modelled it
# in a container, which has no PCI device to hand DPDK, and that lab went
# with #385 because this phase is the only place the bind is real. Every phase
# above this one boots a real guest with a real virtio card and never asks the
# box to take it - PHASE 2 asserts the opposite, that a box claims a NAMED
# card or none at all, which is the check that stops a box swallowing its own
# management port by accident. That check is right and it is not this one.
#
# So what was proven up to here is the DECISION and the boot-time replay. The
# driver change itself was unmeasured, on the only path most deployments will
# ever run: a virtual machine with one virtio card and no IOMMU, where
# bind-dataplane-nic chooses uio_pci_generic.
#
# This phase asks the BOX to do it, through the VTY, with the one statement an
# operator has: `dataplane interface <name>`. Nothing here unbinds anything,
# writes driver_override, loads a module or creates a tap. A lab that
# performed the step the product is missing would prove nothing at all - and
# this is the step the product claims and had never been seen to make.
#
# The acceptance is two sentences, and the second is the one that matters:
#   1. the card is off its kernel driver and on a driver DPDK can use
#   2. THE BOX IS STILL REACHABLE FROM OUTSIDE, through the linux_cp pair the
#      dataplane holds for it
# A box that does (1) and not (2) has eaten its own management port, which on
# a single-card machine means somebody walks to it.
#
# Runs only when PHASE 3 got the box back on the network. "It took its card"
# about a machine that never arrived is a sentence with no subject, and a box
# that was already unreachable cannot tell an unreachable handover from the
# state it was in.
if [ "$up" = 1 ] && [ "${CONSOLE_DRIVEN:-0}" = 1 ] && [ "${RB_REACHED:-1}" = 0 ]
then
  echo
  echo "=========== PHASE 4: the box takes its own card ==========="
  CONSOLE_LOG=/tmp/handover.log
  CONSOLE_CLEAN=/tmp/handover-clean.log
  # shellcheck disable=SC2086
  boot_vm "${HANDOVER_SECS:-1200}" \
    -drive file="$DISK",format=qcow2,if=virtio $MGMT_NET

  HO_DRIVEN=0
  if login_root "the box to come up for the handover"; then
    HO_DRIVEN=1
    # ---------------------------------------------- what Linux holds, before
    #
    # READ OFF THE BOX rather than assumed from $MGMT_NET. The qemu line says
    # which card is attached; it does not say what Linux named it, which slot
    # it landed in or which driver claimed it, and every one of those is a
    # thing this phase later asserts CHANGED. A before-value the lab wrote
    # itself cannot show a change.
    send 'M=$(ip -o -4 route list default | awk "{print \$5}"); echo "HX""LAB ho-if=$M"'
    wait_for "HXLAB ho-if=" 60 "the box to name the card it is reached on" \
      || fail=1
    # The PCI address, by the same walk the product uses: /sys/class/net/<if>/
    # device points at the device that OWNS the netdev, and on a virtio guest
    # that is the virtio bus device whose PARENT is the PCI one. Stopping at
    # the basename answers "not PCI" for every NIC in every VM, which is #286.
    send 'D=$(readlink -f /sys/class/net/$M/device); case "$(basename $D)" in *:*:*.*) P=$(basename $D);; *) P=$(basename $(dirname $D));; esac; echo "HX""LAB ho-pci=$P"'
    wait_for "HXLAB ho-pci=" 30 "the box to give the card's PCI address" \
      || fail=1
    send 'echo "HX""LAB ho-drv0=$([ -e /sys/bus/pci/devices/$P/driver ] && basename $(readlink -f /sys/bus/pci/devices/$P/driver) || echo none)"'
    wait_for "HXLAB ho-drv0=" 30 "the box to name the card's driver" || fail=1
    send 'echo "HX""LAB ho-addr0=$(ip -o -4 addr show dev $M | awk "{print \$4}")"'
    wait_for "HXLAB ho-addr0=" 30 "the box to say what address it is on" \
      || fail=1
    send "exit"
    wait_for "prisma> " 60 "the shell to close before the handover" \
      || fail=1

    # ------------------------------------------------------- the handover
    #
    # ONE STATEMENT, because that is all an operator has. On a box with one
    # card there is no second card to name, the card has no dataplane name
    # until the dataplane has claimed it, and there is no way to test a guess.
    # If this refuses, the refusal is the finding: it is the product telling
    # an operator with one card that they cannot use the product.
    send "configure"
    wait_for "entering configuration mode" 60 \
      "the VTY to enter configuration mode for the handover" || fail=1
    # live_field and not mfield: the clean console is only written when the
    # machine stops, and this card has to be named while it is running.
    send "dataplane interface $(live_field ho-if)"
    HO_NAMED=0
    if wait_for "[edit]" 60 "the VTY to answer the dataplane interface line"
    then
      HO_NAMED=1
    fi
    if [ "$HO_NAMED" = 0 ]; then
      echo "  [FAIL] the VTY would not take 'dataplane interface' for the"
      echo "         only card this box has, so a single-card box cannot be"
      echo "         given a dataplane at all. What it said is in $LOG"
      fail=1
    fi
    send "commit"
    # The tuner is restarted by this commit and the tuning regenerates
    # startup.conf, so this is slower than an ordinary commit. It is also the
    # point where the box may stop answering on the network - the card is
    # being taken off the kernel - which is why every reading from here on is
    # taken at the CONSOLE and not over ssh.
    wait_for_either "commit complete" "commit failed" 600 \
      "the handover commit to apply"
    case $? in
      0) echo "  [ ok ] the VTY accepted the handover and the commit completed" ;;
      2) echo "  [FAIL] the box refused the handover and rolled the commit"
         echo "         back. It is still reachable, which is the right way"
         echo "         to fail, and it did not take its card - so a box with"
         echo "         one NIC cannot be given a dataplane at all. What it"
         echo "         said is on the console above"
         fail=1 ;;
      *) echo "  [FAIL] the handover commit neither completed nor was refused."
         echo "         On a box with one card this is the whole product:"
         echo "         there is no second card to give the dataplane instead"
         fail=1 ;;
    esac
    send "exit"
    wait_for "prisma> " 120 "the VTY to leave configuration mode" || fail=1

    # --------------------------------- what the commit itself did to the card
    #
    # RECORDED, NOT ASSERTED. Whether the bind happens at the commit or at the
    # next boot is the product's choice and it has changed: apply-tuning does
    # the bind and the commit restarts the unit that runs it, while the VTY's
    # own message tells the operator the card has not moved yet. One of those
    # is wrong on this box and this is the reading that says which, so it is
    # printed either way and the acceptance below is taken after the reboot,
    # where both designs are supposed to end up.
    send "start shell"
    wait_for "entering /bin" 60 "the shell to open after the commit" || fail=1
    # NAMED BY THE HOST, not by a variable in the guest. $P and $M were set in
    # the shell that `exit` closed, and `start shell` opens a new one, so with
    # them empty the box was asked about /sys/bus/pci/devices//driver and
    # /sys/class/net/ - and answered "driver" and "present", about neither. A
    # reading whose subject is empty does not fail; it answers about something
    # else, which is the worse of the two.
    _p=$(live_field ho-pci); _m=$(live_field ho-if)
    send 'echo "HX""LAB ho-drv1=$([ -e /sys/bus/pci/devices/'"$_p"'/driver ] && basename $(readlink -f /sys/bus/pci/devices/'"$_p"'/driver) || echo none)"'
    wait_for "HXLAB ho-drv1=" 60 \
      "the box to say which driver holds the card after the commit" || fail=1
    send 'echo "HX""LAB ho-netdev1=$([ -e /sys/class/net/'"$_m"' ] && echo present || echo gone)"'
    wait_for "HXLAB ho-netdev1=" 30 \
      "the box to say whether Linux still has the netdev" || fail=1
    send "sync"
    send "systemctl poweroff"
  else
    echo "  [FAIL] the box did not come up for the handover at all"
    fail=1
  fi
  stop_vm 120

  HO_IF=$(mfield "$CONSOLE_CLEAN" ho-if)
  HO_PCI=$(mfield "$CONSOLE_CLEAN" ho-pci)
  HO_DRV0=$(mfield "$CONSOLE_CLEAN" ho-drv0)
  HO_ADDR0=$(mfield "$CONSOLE_CLEAN" ho-addr0)
  HO_DRV1=$(mfield "$CONSOLE_CLEAN" ho-drv1)
  HO_NETDEV1=$(mfield "$CONSOLE_CLEAN" ho-netdev1)
  echo "  -- before: $HO_IF at $HO_PCI on $HO_DRV0, address $HO_ADDR0"
  echo "  -- after the commit: driver ${HO_DRV1:-<unread>},"
  echo "     kernel netdev ${HO_NETDEV1:-<unread>}"

  # The one reading this phase exists for, and it is worth nothing unless the
  # before-value was a kernel driver: a card that was already on uio_pci_generic
  # proves no bind happened here.
  # THE PREMISE, CHECKED BEFORE THE READINGS ARE BELIEVED (#378).
  #
  # This phase was written when a box claimed nothing until it was told: the
  # card was Linux's when the phase started, and asking the box to take it
  # showed a bind. A box that claims its own card is already in the end state
  # before this phase boots, so there is no "before" for a change of driver to
  # be measured against - `ho-pci` resolves the linux_cp TAP, which has no PCI
  # parent at all, and every driver reading below it is about nothing.
  #
  # `none` AND NOT ONLY "", which is how this got through. The probe prints
  # the literal word `none` when the device has no driver link; the guard
  # tested for an EMPTY value, which is what an unread probe gives. Two
  # spellings of one answer, and the case that mattered was the one not
  # listed - so a box already holding its card was reported as one that
  # started on a kernel driver called "none".
  case "$HO_PCI" in
    0000:*) _ho_premise=ok ;;
    *) _ho_premise=claimed ;;
  esac
  case "$HO_DRV0" in
    vfio-pci|uio_pci_generic|igb_uio|none|"") _ho_premise=claimed ;;
  esac
  if [ "$_ho_premise" = claimed ]; then
    # NOT A FAILURE, and that is the whole finding: the box did by itself,
    # at boot, what this phase exists to ask it to do. PHASE 2 asserted
    # exactly that on this same box - the allowlist, the bind, the port out
    # of error state and the box answering from outside - with nothing typed.
    # The coverage moved; it did not go away.
    echo "  [ ok ] the card was already the dataplane's before this phase"
    echo "         began, so there is no bind left for it to show: the box"
    echo "         claimed it at boot with nobody asked, which PHASE 2"
    echo "         asserted. Read '${HO_IF:-<unread>}' at"
    echo "         '${HO_PCI:-<unread>}' on '${HO_DRV0:-<unread>}'"
  else
    echo "  [ ok ] the card started on a kernel driver ($HO_DRV0), so a"
    echo "         change of driver below is a bind this box performed"
  fi
fi

# =========================================================================
# PHASE 5: and it comes back with the card in the dataplane, still reachable
# =========================================================================
#
# THE ACCEPTANCE. Everything above is preparation: what an operator gets is a
# box that forwards on the card AND that they can still log into. The two have
# to be true at the same time, on the same boot, with nothing typed at the
# console before they are asked - which is why the network reading is taken
# first, before this phase logs in at all.
if [ "${HO_DRIVEN:-0}" = 1 ]; then
  echo
  echo "=========== PHASE 5: it comes back holding its card ==========="
  CONSOLE_LOG=/tmp/handover2.log
  CONSOLE_CLEAN=/tmp/handover2-clean.log
  # shellcheck disable=SC2086
  boot_vm "${HANDOVER_SECS:-1200}" \
    -drive file="$DISK",format=qcow2,if=virtio $MGMT_NET

  # FIRST, and before anything is typed. On this boot the card belongs to the
  # dataplane and the address is on a linux_cp pair, so a connection completing
  # here is the whole of what #303's handover promised: the box kept the path
  # it is reached on while giving the card away.
  HO_BANNER=$(reachable_over_the_network 480)
  if [ $? = 0 ]; then
    echo "  [ ok ] it answers from outside with the card in the dataplane:"
    echo "         $HO_BANNER"
  else
    echo "  [FAIL] after the handover the box does not answer on the network:"
    echo "         ${HO_BANNER:-<nothing at all>}"
    echo "         The card moved and the management path did not come with"
    echo "         it. On a box with one NIC that means somebody walks to it,"
    echo "         which is the outcome the handover exists to prevent"
    fail=1
  fi

  # Set before the login so that a box which never came back leaves them
  # empty rather than unset under `set -u` (#389).
  HO_CARD=""; HO_COMMIT=""
  : > /tmp/ho-commit.txt
  if login_root "the box to come back holding its card"; then
    send 'echo "HX""LAB ho-drv2=$([ -e /sys/bus/pci/devices/'"$HO_PCI"'/driver ] && basename $(readlink -f /sys/bus/pci/devices/'"$HO_PCI"'/driver) || echo none)"'
    wait_for "HXLAB ho-drv2=" 60 \
      "the box to say which driver holds the card now" || fail=1
    # NOT "is the name still there", which is what this asked and got wrong.
    # The product's promise is that the name SURVIVES: the dataplane takes the
    # PCI device and linux_cp makes a TAP carrying the same name, so nothing
    # upstream has to re-learn anything and the address goes back where it was.
    # Measured on a booted guest: ens9 present, address on it, and the card
    # bound to uio_pci_generic at the same time. Asserting the name was gone
    # was asserting a design this product does not have.
    #
    # What distinguishes the two is what the name now resolves to. A TAP has no
    # PCI parent; the card did. So "pair" is the right answer, "card" means the
    # handover never happened, and "gone" means the name was not put back.
    send 'echo "HX""LAB ho-netdev2=$(if [ ! -e /sys/class/net/'"$HO_IF"' ]; then echo gone; elif [ -e /sys/class/net/'"$HO_IF"'/device ]; then echo card; else echo pair; fi)"'
    wait_for "HXLAB ho-netdev2=" 30 \
      "the box to say whether Linux still has the card" || fail=1
    # The address, wherever it now lives. NOT looked for on a named pair: the
    # pair's name is the dataplane's business and a lab that knew it would be
    # asserting against its own guess rather than against the box.
    send 'echo "HX""LAB ho-addr2=$(ip -o -4 addr show | awk "\$4 ~ /^[0-9]/ {print \$2\"=\"\$4}" | grep -v "^lo=" | tr "\n" " ")"'
    wait_for "HXLAB ho-addr2=" 30 \
      "the box to say where its address is now" || fail=1
    # WHAT THE DATAPLANE ITSELF SAYS IT HAS. The sysfs readings above prove
    # Linux gave the card up; this proves somebody took it. A box where the
    # card is unbound and VPP has nothing is WORSE than one that never
    # started: it forwards nothing and the kernel cannot reach the wire
    # either, and the two states are indistinguishable from outside.
    #
    # Asked of the dataplane rather than of a screen that reports it, because
    # this is the one question where a message that has drifted would pass:
    # every other reading here is a fact in sysfs.
    send 'echo "HX""LAB ho-dpif=$(vppctl show interface 2>/dev/null | tr -d "\r" | awk "/^[^ ]/ {print \$1}" | grep -v "^Name$" | grep -v "^local0$" | tr "\n" " ")"'
    wait_for "HXLAB ho-dpif=" 60 \
      "the dataplane to list the interfaces it holds" || fail=1
    # AND WHAT STATE THE PORT IS IN, AND AT WHAT MTU (#382).
    #
    # Everything above is true of a box that forwards nothing. On a card
    # whose maximum is below VPP's default of 9000 the driver refuses the
    # device, the port never starts, and `show interface` says:
    #
    #   GigabitEthernet0/9/0   1   error   9000/0/0/0
    #
    # while the card is bound to uio_pci_generic, the pair exists, the
    # address is on it and the dataplane lists the interface. The
    # reachability check above is the acceptance and it does go red on such a
    # box - but it goes red as a timeout, which is what a slow boot, a lost
    # lease and a dead sshd also look like. This reading names the fault.
    #
    # NAME:STATE:MTU for every interface the dataplane holds, the pair
    # included: the TAP has to agree with the card or the pair drops what the
    # card accepted, and reading only the card would not see that.
    send 'echo "HX""LAB ho-dpmtu=$(vppctl show interface 2>/dev/null | tr -d "\r" | awk "/^[^ ]/ && \$1 != \"Name\" && \$1 != \"local0\" {split(\$4, m, \"/\"); print \$1 \":\" \$3 \":\" m[1]}" | tr "\n" " ")"'
    wait_for "HXLAB ho-dpmtu=" 60 \
      "the dataplane to give the state and MTU of its interfaces" || fail=1
    send "exit"
    wait_for "prisma> " 60 "the shell to close" || fail=1
    # ------------------- the card, by its own name, at the console (#389)
    #
    # THE ISSUE'S TRANSCRIPT. On the maintainer's box `show interface` said
    # GigabitEthernet0/13/0, and `interface GigabitEthernet0/13/0` / `no
    # shutdown` / `commit` got "Failure to communicate[13] to ospf6d",
    # "routing reload failed" and a rollback: FRR 10.7.1 refuses an
    # interface name over 16 characters, and every name VPP gives a card
    # nobody named is over it. No lab met it because their interfaces are
    # pg0 and host-veth-vpp. This is the one box in the repository with a
    # real card, so this is where the four lines are typed.
    #
    # WHAT IS TYPED IS THE NAME THE BOX SHOWS. An operator reads the card's
    # name off `show interface` and types it, which is the transcript
    # above. What #389 promises is that the name they read there is the one
    # Linux gave the card - so on a box that keeps the promise these are
    # the same keystrokes as `interface <linux name>`, and on a box that
    # does not they are the maintainer's, and the commit answers what the
    # maintainer got. Read live off the ho-dpmtu marker block 5 already
    # asked for: the first interface that is not the pair's TAP. Nothing
    # here is spelled by the lab.
    #
    # NOTHING HERE PERFORMS A STEP. `configure`, `interface`, `no shutdown`
    # and `commit` are the operator's four lines; every reading after them
    # is what the box shows.
    for _e in $(live_field ho-dpmtu); do
      case "${_e%%:*}" in
        tap[0-9]*) ;;
        *) HO_CARD=${_e%%:*}; break ;;
      esac
    done
    # A dataplane holding nothing has no name to read; the Linux one is
    # typed and the refusal is the reading.
    [ -n "$HO_CARD" ] || HO_CARD=$HO_IF
    send "configure"
    wait_for "entering configuration mode" 60 \
      "the VTY to enter configuration mode for the interface block" \
      || fail=1
    send "interface $HO_CARD"
    wait_for "[edit interface" 60 "the VTY to open the interface block" \
      || fail=1
    send "no shutdown"
    wait_for "[edit interface" 60 "the VTY to take 'no shutdown'" || fail=1
    send "exit"
    wait_for "[edit]" 60 "the VTY to close the interface block" || fail=1
    # THREE ANSWERS, not two, and classified from the box's words rather
    # than waited for one at a time: `commit complete`; `commit failed;
    # rolling back` - the maintainer's, FRR refusing the stanza at apply -
    # and `candidate did not pass validation; nothing applied`, the commit
    # check refusing the name before anything is applied. The prompt comes
    # back after all three, so that is what is waited for, and what the box
    # printed in between is kept for the verdict.
    _ho_pos=$(( $(wc -c < "$CONSOLE_LOG") + 1 ))
    send "commit"
    wait_for "[edit]" 300 "the commit of the interface block to answer" \
      || fail=1
    tail -c "+$_ho_pos" "$CONSOLE_LOG" | tr -d '\r' \
      | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' > /tmp/ho-commit.txt
    if grep -qF "commit complete" /tmp/ho-commit.txt; then
      HO_COMMIT=complete
    elif grep -qF "commit failed" /tmp/ho-commit.txt; then
      HO_COMMIT=failed
    elif grep -qF "did not pass validation" /tmp/ho-commit.txt; then
      HO_COMMIT=refused
    else
      HO_COMMIT=unread
    fi
    send "exit"
    wait_for "prisma> " 120 "the VTY to leave configuration mode" \
      || fail=1
    # And in the operator's own words, on the console, so the log carries what
    # they would see. Nothing is armed by this commit since #378, and nothing
    # is written down beside the card since #385: what the box does with the
    # card is decided at the next start, and PHASE 5 reads it there.
    send "show interface"
    wait_for "prisma> " 120 "the dataplane to list its interfaces" \
      || fail=1
    # ------------- and the same card in the words an operator types (#393)
    #
    # THE MAINTAINER'S OWN TRANSCRIPT, on 0.77.0, on this box's real card:
    # `interface ens18` / `no shut` / `commit` left the interface down. `no
    # shut` is an abbreviation every router CLI takes and this VTY had no
    # grammar for, so it was staged as an FRR statement IN SILENCE, FRR
    # accepted it inside its own interface stanza and kept nothing, and the
    # operator was shown `commit complete` over an interface that never came
    # up. The block above types `no shutdown` whole and cannot see it.
    #
    # THE CARD IS TAKEN DOWN FIRST, and that is the assertion, not a
    # detour: the block above already left it up, so `no shut` typed onto an
    # interface that is not shut proves nothing at all - it is green on a
    # box that discards the line. What makes the reading real is a card that
    # IS administratively down when the abbreviation is typed.
    #
    # NOTHING HERE PERFORMS A STEP. Seven lines are the operator's, and
    # every reading after them is what the box shows.
    send "configure"
    wait_for "entering configuration mode" 60 \
      "the VTY to enter configuration mode for the abbreviation" || fail=1
    send "interface $HO_CARD"
    wait_for "[edit interface" 60 "the VTY to open the interface block" \
      || fail=1
    send "shutdown"
    wait_for "[edit interface" 60 "the VTY to take 'shutdown'" || fail=1
    send "exit"
    wait_for "[edit]" 60 "the VTY to close the interface block" || fail=1
    send "commit"
    wait_for "[edit]" 300 "the commit that takes the card down to answer" \
      || fail=1
    send "interface $HO_CARD"
    wait_for "[edit interface" 60 "the VTY to reopen the block" || fail=1
    _ab_pos=$(( $(wc -c < "$CONSOLE_LOG") + 1 ))
    send "no shut"
    wait_for "[edit interface" 60 "the VTY to take 'no shut'" || fail=1
    # WHAT THE BLOCK HOLDS, in the box's own spelling. On 0.77.0 `show this`
    # answered `no shut  <- not checked` - the marker #141 puts on a
    # statement that went to FRR word for word - and that row is what says
    # the box never read the line. The resolution happens once, before
    # dispatch, so after #393 `no shut` clears the block's `shutdown` row.
    # An interface that is up prints NO row for it - the block writes
    # ` shutdown` only when it is down, and never `no shutdown` - so the
    # reading is the ABSENCE of that row, which the `shutdown` committed
    # above had put there.
    send "show this"
    wait_for "[edit interface" 60 "the block to print itself back" || fail=1
    tail -c "+$_ab_pos" "$CONSOLE_LOG" | tr -d '\r' \
      | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' > /tmp/ab-block.txt
    send "exit"
    wait_for "[edit]" 60 "the VTY to close the interface block" || fail=1
    send "commit"
    wait_for "[edit]" 300 "the commit of 'no shut' to answer" || fail=1
    send "exit"
    wait_for "prisma> " 120 "the VTY to leave configuration mode" \
      || fail=1
    _ab_show=$(( $(wc -c < "$CONSOLE_LOG") + 1 ))
    send "show interface"
    wait_for "prisma> " 120 \
      "the VTY to list its interfaces after 'no shut'" || fail=1
    tail -c "+$_ab_show" "$CONSOLE_LOG" | tr -d '\r' \
      | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' > /tmp/ab-show.txt
    send "start shell"
    wait_for "entering /bin" 60 "the shell to reopen" || fail=1
    # What the box shows after the commit, each asked under the name Linux
    # gave the card and never under one the dataplane may have invented:
    # the dataplane's state for it, the netdev Linux has by that name,
    # whether FRR lists it, and the VTY's own row for it (#389).
    send 'echo "HX""LAB ho-up=$(vppctl show interface 2>/dev/null | tr -d "\r" | awk -v n="'"$HO_IF"'" "\$1 == n {print \$3}")"'
    wait_for "HXLAB ho-up=" 60 \
      "the dataplane to give the state of the card under its Linux name" \
      || fail=1
    send 'echo "HX""LAB ho-link=$(ip -o link show '"$HO_IF"' 2>/dev/null | cut -d: -f2 | tr -d " ")"'
    wait_for "HXLAB ho-link=" 30 \
      "Linux to say whether it has a netdev of the card's name" || fail=1
    send 'echo "HX""LAB ho-frr=$(vtysh -c "show interface brief" 2>/dev/null | awk -v n="'"$HO_IF"'" "\$1 == n {print \$1, \$2}")"'
    wait_for "HXLAB ho-frr=" 60 "FRR to say whether it lists the card" \
      || fail=1
    send 'echo "HX""LAB ho-shif=$(prismash -c "show interface" 2>/dev/null | tr -d "\r" | awk -v n="'"$HO_IF"'" "\$1 == n" | tr -s " " | sed "s/^ //")"'
    wait_for "HXLAB ho-shif=" 120 "the VTY to print its own row for the card" \
      || fail=1
    # AND THE ROW THE CARD RULE READS. prismash decides which interfaces are
    # cards - the ones paired under their own name by default - off the
    # `pci:` row `show hardware-interfaces` prints under each, written from
    # VPP's format string and never before read off a box here. The row
    # for the card, under its Linux name, so the fixture the apply suite
    # holds can be checked against the real one.
    send 'echo "HX""LAB ho-hwpci=$(vppctl show hardware-interfaces 2>/dev/null | tr -d "\r" | awk "/^[^ ]/ {n=\$1} /^ *pci:/ && n == \"'"$HO_IF"'\" {print}" | tr -s " " | sed "s/^ //")"'
    wait_for "HXLAB ho-hwpci=" 60 \
      "the dataplane to print the card's pci row" || fail=1
    send "sync"
    send "systemctl poweroff"
  else
    echo "  [FAIL] the box did not come back after the handover at all"
    fail=1
  fi
  stop_vm 120

  HO_DPIF=$(mfield "$CONSOLE_CLEAN" ho-dpif)
  HO_DPMTU=$(mfield "$CONSOLE_CLEAN" ho-dpmtu)
  HO_DRV2=$(mfield "$CONSOLE_CLEAN" ho-drv2)
  HO_NETDEV2=$(mfield "$CONSOLE_CLEAN" ho-netdev2)
  HO_ADDR2=$(mfield "$CONSOLE_CLEAN" ho-addr2)

  # 1. THE BIND. Compared against the driver the card started on, which was
  # read off this same box before anything was typed, and against the list of
  # drivers DPDK can use - not against a single name, because which of the two
  # a box gets depends on whether it has an IOMMU and both are correct.
  # SKIPPED WHEN THERE WAS NO BEFORE. Comparing "after" to a reading taken of
  # a TAP says nothing, and saying it anyway is how a lab reports a defect
  # that is its own premise (#378).
  if [ "${_ho_premise:-ok}" = claimed ]; then
    echo "  [ ok ] the card is the dataplane's, as it already was before this"
    echo "         phase: there is no change of driver to compare"
  else
  case "$HO_DRV2" in
    vfio-pci|uio_pci_generic)
      echo "  [ ok ] the card is bound to $HO_DRV2: the box took it off"
      echo "         $HO_DRV0 and gave it to DPDK. This is the first time"
      echo "         anything in this repository has performed that bind" ;;
    "")
      echo "  [FAIL] the card's driver could not be read at $HO_PCI, so"
      echo "         nothing here knows whether the bind happened"
      fail=1 ;;
    *)
      echo "  [FAIL] after the handover the card is still on '$HO_DRV2'."
      echo "         It started on '$HO_DRV0'. The box was asked for the"
      echo "         card, said the commit completed, and never bound it -"
      echo "         so DPDK has no device and the box forwards nothing"
      fail=1 ;;
  esac
  fi

  # 2. AND THE NAME IS THE PAIR NOW, NOT THE CARD. Something called $HO_IF
  # still being there is what the handover is FOR: the card keeps its MAC and
  # the pair keeps its name, so nothing across the cable re-learns anything.
  # What would be wrong is that name still resolving to the PCI device, which
  # means the card never left the kernel and two stacks believe they own the
  # port.
  case "$HO_NETDEV2" in
    pair) echo "  [ ok ] and $HO_IF is the pair now, not the card: the name"
          echo "         survived the handover and no longer resolves to a"
          echo "         PCI device" ;;
    card)
      echo "  [FAIL] $HO_IF still resolves to a PCI device after the"
      echo "         handover, so the card never left the kernel and two"
      echo "         stacks believe they own the port"
      fail=1 ;;
    gone)
      echo "  [FAIL] $HO_IF is not there at all. The card left the kernel and"
      echo "         nothing put the name back, so the address the box is"
      echo "         reached on has nowhere to live"
      fail=1 ;;
    *) echo "  [FAIL] what $HO_IF resolves to was not read"; fail=1 ;;
  esac

  # 3. AND THE ADDRESS IS BACK ON THAT NAME. This asked for the opposite and
  # was wrong for the same reason as the check above: the address being on
  # $HO_IF is the whole promise, once $HO_IF is the pair rather than the card.
  # Check 2 is what makes this one mean anything, which is why the two are
  # read from the same boot and reported together.
  case "$HO_ADDR2" in
    "")
      echo "  [FAIL] the box came back with no address on any interface."
      echo "         The card went to the dataplane and the management path"
      echo "         did not follow"
      fail=1 ;;
    *"$HO_IF="*)
      if [ "$HO_NETDEV2" = pair ]; then
        echo "  [ ok ] and the address is back on it: $HO_ADDR2"
      else
        echo "  [FAIL] the address is on $HO_IF and $HO_IF is still the card,"
        echo "         so nothing moved: $HO_ADDR2"
        fail=1
      fi ;;
    *)
      echo "  [FAIL] the box has an address and it is not on $HO_IF."
      echo "         The handover keeps the name so that nothing upstream has"
      echo "         to re-learn: $HO_ADDR2"
      fail=1 ;;
  esac

  # 4. AND SOMEBODY TOOK IT. local0 is VPP's loopback and is there on a box
  # that holds nothing, so it is excluded above: what is left is a real
  # interface or an empty string, and an empty string here is the exact screen
  # the handover was reported broken on - 'show interface' listing local0 and
  # nothing else, on a box whose card Linux had already given up.
  if [ -n "$HO_DPIF" ]; then
    echo "  [ ok ] and the dataplane holds it: $HO_DPIF"
  else
    echo "  [FAIL] the dataplane has no interface but local0, on a box whose"
    echo "         card the kernel has already released. Nothing owns the"
    echo "         port: this is the state an operator sees as 'show"
    echo "         interface' listing local0 and nothing else"
    fail=1
  fi

  # 5. AND THE PORT STARTED, AT AN MTU THE CARD CAN CARRY (#382).
  #
  # Two readings of one screen, and neither is the reachability check above:
  # that one says whether a packet got through, these say why not. THIS LAB
  # SETS NO MTU ANYWHERE - the number below is the one it gave the card in
  # $MGMT_MAXMTU, and the box has to arrive at it on its own.
  echo "  -- the dataplane's own interfaces, name:state:mtu:"
  echo "     ${HO_DPMTU:-<unread>}"
  if [ -z "$HO_DPMTU" ]; then
    echo "  [FAIL] the state and MTU of the dataplane's interfaces were not"
    echo "         read, so nothing below knows whether the port started"
    fail=1
  else
    _errored=""
    _toobig=""
    for _e in $HO_DPMTU; do
      _n=${_e%%:*}; _rest=${_e#*:}; _st=${_rest%%:*}; _mtu=${_rest##*:}
      [ "$_st" = error ] && _errored="$_errored $_n"
      case "$_mtu" in
        ''|*[!0-9]*) ;;
        *) [ "$_mtu" -gt "$MGMT_MAXMTU" ] && _toobig="$_toobig $_n($_mtu)" ;;
      esac
    done
    if [ -n "$_errored" ]; then
      echo "  [FAIL] the dataplane holds interface(s) in error state:$_errored"
      echo "         The driver refused the device, so the port never started"
      echo "         and the box forwards nothing - with the card bound, the"
      echo "         pair made and the address on it. 'journalctl -u vpp' on"
      echo "         the box has the driver's own reason"
      fail=1
    else
      echo "  [ ok ] and no interface it holds is in error state: the driver"
      echo "         accepted the device and the port started"
    fi
    if [ -n "$_toobig" ]; then
      echo "  [FAIL] interface(s) above the $MGMT_MAXMTU this card can"
      echo "         carry:$_toobig"
      echo "         This lab gave the card host_mtu=$MGMT_MAXMTU and typed"
      echo "         no MTU at the box. Coming up higher means nothing read"
      echo "         the card, which is the state the port is refused in"
      fail=1
    else
      echo "  [ ok ] and every one of them is at or below the $MGMT_MAXMTU"
      echo "         this card can carry, with nothing typed to ask for it:"
      echo "         the card and its pair agree"
    fi
  fi

  # 6. AND THE CARD ANSWERS TO ITS OWN NAME - at the console, to FRR and to
  # Linux (#389). Every reading above is true of the box the maintainer
  # measured: card bound, pair made, address on it, port up - and no
  # interface could leave `shutdown`, because the dataplane called the card
  # GigabitEthernet0/13/0 and FRR refuses the name. So the transcript was
  # typed above, and what is asserted here is the name on every side of it.
  HO_UP=$(mfield "$CONSOLE_CLEAN" ho-up)
  HO_UP_ASKED=$(grep -c "^HXLAB ho-up=" "$CONSOLE_CLEAN")
  HO_LINK=$(mfield "$CONSOLE_CLEAN" ho-link)
  HO_FRR=$(mfield "$CONSOLE_CLEAN" ho-frr)
  HO_SHIF=$(mfield "$CONSOLE_CLEAN" ho-shif)
  # The state the card was in BEFORE the commit, off the reading block 5
  # made. On this box the management path brings the card up at boot, so
  # `up` afterwards is not what the commit changed, and the row says so
  # when that is what it read. What the commit changes is whether FRR takes
  # the stanza, and that is the commit's own answer, classified above.
  _ho_before=""
  for _e in $HO_DPMTU; do
    case "$_e" in "$HO_IF":*) _r=${_e#*:}; _ho_before=${_r%%:*} ;; esac
  done
  echo "  -- the dataplane's name for the card: ${HO_CARD:-<unread>};"
  echo "     Linux's: ${HO_IF:-<unread>}"
  if [ -n "$HO_CARD" ] && [ "$HO_CARD" = "$HO_IF" ]; then
    echo "  [ ok ] the dataplane calls the card by the name Linux gave it,"
    echo "         so the operator has one name to learn"
  else
    echo "  [FAIL] the dataplane calls the card '${HO_CARD:-<nothing>}' and"
    echo "         Linux called it '$HO_IF'. The tuner claimed the card and"
    echo "         did not name it, so VPP named it after its address: a"
    echo "         name the operator never saw, and one FRR cannot take"
    fail=1
  fi
  case "${HO_COMMIT:-unread}" in
    complete)
      echo "  [ ok ] and 'interface $HO_CARD' / 'no shutdown' / 'commit'"
      echo "         completed at the console" ;;
    failed)
      echo "  [FAIL] 'interface $HO_CARD' / 'no shutdown' / 'commit' failed"
      echo "         and rolled back. The box said:"
      grep -E "Failure to communicate|routing reload failed|^error:" \
        /tmp/ho-commit.txt 2>/dev/null | head -5 | sed 's/^/         | /'
      echo "         This is the maintainer's transcript: FRR refused the"
      echo "         stanza, and no interface can leave shutdown on this box"
      fail=1 ;;
    refused)
      echo "  [FAIL] the commit check refused 'interface $HO_CARD' before"
      echo "         anything was applied. The box said:"
      grep -E "^error:" /tmp/ho-commit.txt 2>/dev/null | head -5 \
        | sed 's/^/         | /'
      fail=1 ;;
    *)
      echo "  [FAIL] the commit of 'interface $HO_CARD' neither completed"
      echo "         nor was refused; what the box said is in $LOG"
      fail=1 ;;
  esac
  if [ "$HO_UP_ASKED" = 0 ]; then
    echo "  [FAIL] the dataplane was never asked for $HO_IF after the commit"
    fail=1
  elif [ "$HO_UP" = up ]; then
    echo "  [ ok ] and 'show interface' lists $HO_IF up after it"
    if [ "$_ho_before" = up ]; then
      echo "         (it was up before the commit too: the management path"
      echo "         of a one-card box brings the card up at boot, so the"
      echo "         state is not what this commit changed - the name is,"
      echo "         and the rows above carry it)"
    fi
  elif [ -z "$HO_UP" ]; then
    echo "  [FAIL] the dataplane has no interface called $HO_IF after the"
    echo "         commit; it holds: ${HO_DPIF:-<nothing>}"
    fail=1
  else
    echo "  [FAIL] $HO_IF is '$HO_UP' after 'no shutdown' / 'commit'"
    fail=1
  fi
  # AND THE ABBREVIATION (#393). Three readings off the transcript above,
  # and each is one half of the defect. `show this` says whether the VTY
  # read the word or carried it; `show interface` says whether the card came
  # back up; and the marker row says whether the operator was told anything
  # at all. On 0.77.0 all three are red at once and only the first two are
  # visible: the box printed `commit complete` and left the card down.
  if [ ! -s /tmp/ab-block.txt ]; then
    echo "  [FAIL] 'no shut' was typed and nothing came back from the VTY;"
    echo "         what the console carried is in $LOG"
    fail=1
  elif grep -qF "not checked" /tmp/ab-block.txt; then
    echo "  [FAIL] 'no shut' went to FRR word for word. 'show this' said:"
    grep -F "not checked" /tmp/ab-block.txt | head -3 \
      | sed 's/^/         | /'
    echo "         This is the maintainer's screen: the VTY has no grammar"
    echo "         for the word, so nothing on this box ever read it"
    fail=1
  elif ! grep -qE "^ *interface $HO_CARD *$" /tmp/ab-block.txt; then
    echo "  [FAIL] 'show this' did not print the interface block after"
    echo "         'no shut'. It said:"
    sed 's/^/         | /' /tmp/ab-block.txt | head -8
    fail=1
  elif grep -qE "^ *shutdown *$" /tmp/ab-block.txt; then
    echo "  [FAIL] 'show this' still holds 'shutdown' after 'no shut'. It"
    echo "         said:"
    sed 's/^/         | /' /tmp/ab-block.txt | head -8
    fail=1
  else
    echo "  [ ok ] 'no shut' is read as 'no shutdown': the block's"
    echo "         'shutdown' row is gone, so 'save' writes the card up"
  fi
  AB_STATE=$(tr -s ' ' < /tmp/ab-show.txt 2>/dev/null \
    | awk -v n="$HO_IF" '$1 == n {print $2}' | head -1)
  case "$AB_STATE" in
    up)
      echo "  [ ok ] and 'show interface' reads $HO_IF up after it: the"
      echo "         abbreviation reached the dataplane, not FRR" ;;
    "")
      echo "  [FAIL] 'show interface' has no row for $HO_IF after 'no shut'"
      fail=1 ;;
    *)
      echo "  [FAIL] $HO_IF is '$AB_STATE' after 'shutdown' / 'commit' /"
      echo "         'no shut' / 'commit'. The card was taken down and the"
      echo "         word that brings it back was never read: this is the"
      echo "         interface the maintainer was left with on 0.77.0"
      fail=1 ;;
  esac
  if [ "$HO_LINK" = "$HO_IF" ]; then
    echo "  [ ok ] and Linux has a netdev of that name, the pair: $HO_LINK"
  else
    echo "  [FAIL] Linux has no netdev called $HO_IF after the commit"
    echo "         (ip link said '${HO_LINK:-<nothing>}'), so the routing"
    echo "         daemons have nothing by the card's name to bind to"
    fail=1
  fi
  case "$HO_FRR" in
    "$HO_IF up")
      echo "  [ ok ] and FRR lists it, up: '$HO_FRR'" ;;
    "$HO_IF "*)
      echo "  [FAIL] FRR lists $HO_IF and not up: '$HO_FRR'"; fail=1 ;;
    *)
      echo "  [FAIL] FRR's 'show interface brief' does not list $HO_IF"
      echo "         (it said '${HO_FRR:-<nothing>}'): there is nothing by"
      echo "         the card's name for a routing protocol to run on"
      fail=1 ;;
  esac
  case "$HO_SHIF" in
    "$HO_IF "*" $HO_IF")
      echo "  [ ok ] and the VTY's own 'show interface' has one row for it,"
      echo "         headed by that name and ending in its Linux end:"
      echo "         $HO_SHIF" ;;
    "$HO_IF "*)
      echo "  [FAIL] the VTY's 'show interface' row for $HO_IF does not end"
      echo "         in its Linux end: '$HO_SHIF'"
      fail=1 ;;
    *)
      echo "  [FAIL] the VTY's 'show interface' has no row headed $HO_IF"
      echo "         (it said '${HO_SHIF:-<nothing>}'): the card is listed"
      echo "         under a name the operator never gave it"
      fail=1 ;;
  esac
  HO_HWPCI=$(mfield "$CONSOLE_CLEAN" ho-hwpci)
  echo "  -- the dataplane's pci row under $HO_IF:"
  echo "     ${HO_HWPCI:-<none>}"
  case "$HO_HWPCI" in
    "pci: "*" address 0000:"*)
      echo "  [ ok ] and the dataplane lists the card's PCI address under its"
      echo "         Linux name, which is the row the card rule reads" ;;
    *)
      echo "  [FAIL] 'show hardware-interfaces' has no pci row under $HO_IF,"
      echo "         so prismash cannot tell that name is a card's"
      fail=1 ;;
  esac
fi

# =========================================================================
# PHASE 6: the same box, booted on the containers, still forwarding (#426)
# =========================================================================
#
# THE ACCEPTANCE FOR THE FLIP, and it is here rather than in a lab of its own
# for one reason: this is the only box in the repository with a real card that
# a dataplane has actually taken. Everything #426 has shipped so far - the
# images on the medium, the device fragment, ipc: host, the OOM score, the
# card handed back - is proven against a file, a stub or a container on the
# build machine. None of that answers the only question an operator has:
# switch this box to the containers and does it still carry packets.
#
# WHAT IS ASSERTED, and the order matters. The reachability probe comes FIRST,
# before anything is typed, and on this box that probe IS the forwarding
# measurement: PHASE 4 gave the card to the dataplane, so the address the
# probe connects to lives on a linux_cp pair and every packet of that
# connection crosses VPP. A box that answers here is a box forwarding on a
# dataplane in a container.
#
# THE SWITCH IS INVERTED NOW, and that is the release that moved the default
# (#426): an installed box boots the containers, so what this phase drives is
# THE WAY BACK - disable the containers unit and its watcher, enable
# vpp.service and frr.service, reboot. An operator who wants the units has to
# be able to have them, and a way back nobody has driven is not one.
#
# EVERY PHASE ABOVE IS NOW THE CONTAINER PATH, which is what makes this the
# cheap half: the box that installed, booted, took its card, was reached over
# ssh, had its configuration read back and gave its card away in PHASE 4 was
# running the dataplane in a container while it did all of it.
if [ "${HO_DRIVEN:-0}" = 1 ]; then
  echo
  echo "=========== PHASE 6: and it can be put back on the units ==========="
  CONSOLE_LOG=/tmp/containers.log
  CONSOLE_CLEAN=/tmp/containers-clean.log
  # shellcheck disable=SC2086
  boot_vm "${CONTAINER_SECS:-1200}" \
    -drive file="$DISK",format=qcow2,if=virtio $MGMT_NET
  CT_SWITCH=""
  if login_root "the box to come back for the switch to containers"; then
    # `disable --now` on both, then enable the pair. The units conflict, so
    # the order is not a preference: enabling the containers unit while
    # vpp.service is running is asking systemd to decide, and what this
    # measures is a boot, not a race.
    send 'echo "HX""LAB ct-switch=$(systemctl disable --now prisma-routing-containers.service prisma-routing-card-watch.timer > /tmp/ct.log 2>&1; systemctl enable vpp.service frr.service >> /tmp/ct.log 2>&1; echo rc=$?)"'
    wait_for "HXLAB ct-switch=" 300 \
      "the box to switch itself back to the systemd units" || fail=1
    CT_SWITCH=$(live_field ct-switch)
    send "sync"
    send "systemctl poweroff"
  else
    echo "  [FAIL] the box did not come back to be switched to containers"
    fail=1
  fi
  stop_vm 120

  case "$CT_SWITCH" in
    *rc=0*) echo "  [ ok ] the two commands that put a box back were taken" ;;
    "")     echo "  [FAIL] the box never answered about the switch"; fail=1 ;;
    *)      echo "  [FAIL] putting this box back on vpp.service and"
            echo "         frr.service failed ($CT_SWITCH). An operator who"
            echo "         wants the units has no way back from the default"
            fail=1 ;;
  esac

  # AND NOW THE BOOT THAT MATTERS.
  CONSOLE_LOG=/tmp/containers2.log
  CONSOLE_CLEAN=/tmp/containers2-clean.log
  # shellcheck disable=SC2086
  boot_vm "${CONTAINER_SECS:-1200}" \
    -drive file="$DISK",format=qcow2,if=virtio $MGMT_NET

  # FIRST, AND BEFORE ANYTHING IS TYPED: see the note at the top of this
  # phase. This connection crosses the dataplane.
  CT_BANNER=$(reachable_over_the_network 480)
  if [ $? = 0 ]; then
    echo "  [ ok ] put back on vpp.service, the box still answers over the"
    echo "         card its dataplane holds:"
    echo "         $CT_BANNER"
  else
    echo "  [FAIL] put back on vpp.service and frr.service, the box does not"
    echo "         answer on the network: ${CT_BANNER:-<nothing at all>}"
    echo "         The way back out of the default is a way back nobody can"
    echo "         take: this box carries packets on neither path now"
    fail=1
  fi

  CT_PS=""; CT_DPIF=""; CT_UNIT=""; CT_LOADER=""; CT_SELF=""
  CT_UNITSTATE=""; CT_WHY=""; CT_IMGS=""
  CT_LOADWHY=""; CT_LINKS=""; CT_THREADS=""; CT_DPLOG=""; CT_FRR=""
  if login_root "the box to come back on its container stack"; then
    # WHAT IS ACTUALLY RUNNING. Names and states, from docker itself.
    send 'echo "HX""LAB ct-ps=$(docker ps --format "{{.Names}}:{{.State}}" 2>/dev/null | sort | tr "\n" " ")"'
    wait_for "HXLAB ct-ps=" 120 "the box to list its containers" || fail=1
    # THE HOST VTY AGAINST A CONTAINERISED DATAPLANE. vppctl runs on the box
    # and reaches /run/vpp/cli.sock, which the compose file bind-mounts; the
    # binary API it also needs is what ipc: host and share-the-api-segment
    # are for (0.114.0). An empty answer here is a box whose operator has no
    # way to configure it.
    send 'echo "HX""LAB ct-dpif=$(vppctl show interface 2>/dev/null | tr -d "\r" | awk "/^[^ ]/ {print \$1}" | grep -v "^Name$" | grep -v "^local0$" | tr "\n" " ")"'
    wait_for "HXLAB ct-dpif=" 120 \
      "the dataplane in the container to list its interfaces" || fail=1
    # AND vpp.service IS NOT RUNNING. Two dataplanes on one card is the state
    # the Conflicts= exists to prevent, and a lab that did not look would not
    # notice it.
    send 'echo "HX""LAB ct-unit=$(systemctl is-active vpp.service 2>&1 | tr -d "\r")"'
    wait_for "HXLAB ct-unit=" 60 \
      "the box to say whether vpp.service is running too" || fail=1
    # THE CONFIGURATION, APPLIED TO THAT DATAPLANE. prisma-routing.service is
    # what hands the saved configuration to the box at every boot; on this
    # path it must have waited for the container and succeeded. This is the
    # reading 0.115.2's change to Requires= was made for.
    send 'echo "HX""LAB ct-loader=$(systemctl is-active prisma-routing.service 2>&1 | tr -d "\r")"'
    wait_for "HXLAB ct-loader=" 60 \
      "the box to say whether its configuration was applied" || fail=1
    # AND WHAT THE SELF-TEST MAKES OF IT. It is the screen an operator reads
    # at the console, and on this box it has to name the container rather
    # than a unit nobody started (0.115.1).
    # PRISMA_SELFTEST_OUT, because the self-test writes to /dev/console and
    # not to stdout - so `$( )` around it captured nothing, and this check
    # read 0 on a box whose console showed the line in full. The variable is
    # the self-test's own, which is what it is for.
    send 'echo "HX""LAB ct-self=$(PRISMA_SELFTEST_OUT=/dev/stdout /usr/lib/prisma-routing/selftest 2>&1 | tr -d "\r" | grep -c "the dataplane runs in a container")"'
    wait_for "HXLAB ct-self=" 300 "the self-test to run on the box" || fail=1
    # AND THE REASON, WHATEVER HAPPENED. The first run of this phase found no
    # containers and said exactly that and nothing else, and the box was gone
    # by the time anybody could ask it why - the whole diagnosis had to be
    # made from a boot log that did not contain it. Asked on every run, not
    # only on a failure: a reading taken only when something is wrong is one
    # nobody has ever seen working.
    send 'echo "HX""LAB ct-unitstate=$(systemctl is-active prisma-routing-containers.service 2>&1 | tr -d "\r")"'
    wait_for "HXLAB ct-unitstate=" 60 \
      "the box to say what state the container unit is in" || fail=1
    send 'echo "HX""LAB ct-why=$(journalctl -u prisma-routing-containers.service --no-pager -n 6 2>/dev/null | tr -d "\r" | tr "\n" "|" | cut -c1-400)"'
    wait_for "HXLAB ct-why=" 60 \
      "the box to say what the container unit did" || fail=1
    send 'echo "HX""LAB ct-imgs=$(docker images --format "{{.Repository}}:{{.Tag}}" 2>/dev/null | tr "\n" " ")"'
    wait_for "HXLAB ct-imgs=" 120 \
      "the box to list the images it has" || fail=1
    # WHY THE CONFIGURATION WAS REFUSED, if it was. prismash --load refuses a
    # file whole and says which line and why, and that sentence is in the
    # journal of this unit and nowhere else - the console only carries the
    # last four lines of it under the self-test's verdict.
    # THE WHOLE REFUSAL AND NOT ITS TAIL. `--load` refuses a file whole and
    # prints the check that refused it ABOVE the error line, so a capture
    # that keeps the last 500 characters keeps the error and loses the
    # reason - which is what the run before this one did, and the diagnosis
    # had to wait another twenty minutes for it.
    send 'echo "HX""LAB ct-loadwhy=$(journalctl -u prisma-routing.service --no-pager -n 40 2>/dev/null | tr -d "\r" | sed "s/^.*prismash\[[0-9]*\]: //" | grep -viE "systemd\[|Starting |Stopped |Failed to start" | tr "\n" "|" | cut -c1-1400)"'
    wait_for "HXLAB ct-loadwhy=" 60 \
      "the box to say what it made of its own configuration" || fail=1
    # AND WHETHER FRR ANSWERS AT ALL from the host, because on this path the
    # daemons are in a container and vtysh is not: they meet at /var/run/frr,
    # which compose bind-mounts. A validator that cannot ask FRR anything is
    # a validator whose refusals are about the wrong thing.
    send 'echo "HX""LAB ct-frr=$(vtysh -c "show version" 2>&1 | head -1 | tr -d "\r" | cut -c1-80)"'
    wait_for "HXLAB ct-frr=" 60 "the box to say whether FRR answers" || fail=1
    # THE NETDEVS LINUX HAS. On this box the address lives on a linux_cp pair
    # named after the card, so "which netdevs are there" is the difference
    # between a box that can be reached and one that cannot.
    send 'echo "HX""LAB ct-links=$(ip -o link show | awk -F": " "{print \$2}" | tr "\n" " ")"'
    wait_for "HXLAB ct-links=" 60 "the box to list its netdevs" || fail=1
    # AND THE DATAPLANE'S OWN THREADS. The self-test reported no worker
    # thread on a box whose startup.conf pins seven, which is either a
    # dataplane that could not place them or a reading that does not apply
    # to a container.
    send 'echo "HX""LAB ct-threads=$(vppctl show threads 2>/dev/null | tr -d "\r" | awk "NR>1 {print \$1\":\"\$4}" | tr "\n" " ")"'
    wait_for "HXLAB ct-threads=" 60 \
      "the dataplane to list its threads" || fail=1
    send 'echo "HX""LAB ct-dplog=$(docker logs --tail 12 prisma-routing_vpp_1 2>&1 | tr -d "\r" | grep -iE "error|warn|fail|thread|affinity" | tr "\n" "|" | cut -c1-400)"'
    wait_for "HXLAB ct-dplog=" 120 \
      "the dataplane container to say what it logged" || fail=1
    CT_PS=$(live_field ct-ps)
    CT_DPIF=$(live_field ct-dpif)
    CT_UNIT=$(live_field ct-unit)
    CT_LOADER=$(live_field ct-loader)
    CT_SELF=$(live_field ct-self)
    CT_UNITSTATE=$(live_field ct-unitstate)
    CT_WHY=$(live_field ct-why)
    CT_IMGS=$(live_field ct-imgs)
    CT_LOADWHY=$(live_field ct-loadwhy)
    CT_FRR=$(live_field ct-frr)
    CT_LINKS=$(live_field ct-links)
    CT_THREADS=$(live_field ct-threads)
    CT_DPLOG=$(live_field ct-dplog)
    send "sync"
    send "systemctl poweroff"
  else
    echo "  [FAIL] the box booted on the containers did not reach a login"
    fail=1
  fi
  stop_vm 120

  echo "  -- the container unit is: ${CT_UNITSTATE:-<no answer>}"
  echo "  -- images on the box: ${CT_IMGS:-<none>}"
  echo "  -- what the unit did: ${CT_WHY:-<nothing>}"
  echo "  -- netdevs Linux has: ${CT_LINKS:-<none>}"
  echo "  -- dataplane threads: ${CT_THREADS:-<none>}"
  echo "  -- the dataplane logged: ${CT_DPLOG:-<nothing of note>}"
  echo "  -- what --load made of the saved file: ${CT_LOADWHY:-<nothing>}"
  echo "  -- FRR, asked from the host: ${CT_FRR:-<no answer>}"
  echo "  -- containers: ${CT_PS:-<none>}"
  # ORDER-INDEPENDENT, and that is not tidiness: the reading is `sort`ed, so
  # frr comes first, and a pattern written vpp-then-frr called a box with
  # BOTH containers up "the control plane is not running". A lab that reports
  # a defect of its own making is worse than one that reports nothing.
  _ct_vpp=no; _ct_frr=no
  case "$CT_PS" in *prisma-routing_vpp_1:running*) _ct_vpp=yes ;; esac
  case "$CT_PS" in *prisma-routing_frr_1:running*) _ct_frr=yes ;; esac
  if [ -z "$CT_PS" ]; then
    echo "  [ ok ] and no container is running: the stack was stopped and"
    echo "         stayed stopped across the reboot"
  else
    echo "  [FAIL] a container is still running on a box put back on the"
    echo "         units: '$CT_PS'. Two dataplanes are holding one card,"
    echo "         which is what the Conflicts= exists to prevent - and the"
    echo "         operator asked for one of them, not both"
    fail=1
  fi

  echo "  -- the dataplane holds: ${CT_DPIF:-<nothing>}"
  case "$CT_DPIF" in
    "")
      echo "  [FAIL] the VTY on the host got nothing out of the dataplane."
      echo "         Either the container is not the dataplane, or vppctl"
      echo "         cannot reach it - and an operator on this box has no"
      echo "         way to configure anything"
      fail=1 ;;
    *tap*)
      echo "  [ ok ] and the host's own vppctl reads it, pair and all" ;;
    *)
      echo "  [ ok ] and the host's own vppctl reads it" ;;
  esac

  case "$CT_UNIT" in
    active)
      echo "  [ ok ] vpp.service is running, which is what was asked for" ;;
    *)
      echo "  [FAIL] vpp.service is '${CT_UNIT:-<nothing>}' after an operator"
      echo "         enabled it and rebooted: the way back off the default"
      echo "         does not arrive anywhere"
      fail=1 ;;
  esac

  case "$CT_LOADER" in
    active)
      echo "  [ ok ] and the saved configuration was applied to it at boot" ;;
    *)
      echo "  [FAIL] prisma-routing.service is '${CT_LOADER:-<nothing>}' on a"
      echo "         box that booted on the containers: the configuration an"
      echo "         operator saved was not applied, so the box came up with"
      echo "         no CGNAT at all and nothing on the console says so"
      fail=1 ;;
  esac

  case "$CT_SELF" in
    0)
      echo "  [ ok ] and the self-test no longer names a container: its"
      echo "         readings follow the box rather than the release" ;;
    "")
      echo "  [FAIL] the self-test did not answer at all"
      fail=1 ;;
    *)
      echo "  [FAIL] the self-test still calls this box's dataplane a"
      echo "         container ($CT_SELF line(s)) after it was put back on"
      echo "         vpp.service: every reading it takes about restarts and"
      echo "         the OOM score is then about something that is not"
      echo "         running"
      fail=1 ;;
  esac
fi


echo
if [ "$fail" = 0 ]; then
  echo "LAB PASSED in $(( $(date +%s) - LAB_START ))s"
  exit 0
fi
echo "LAB FAILED after $(( $(date +%s) - LAB_START ))s"
echo "--- full console in $LOG ---"
exit 1
