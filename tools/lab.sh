#!/bin/sh
# Runs a lab in the build container.
#
#   sh tools/lab.sh                lists the labs
#   sh tools/lab.sh datapath       runs lab/datapath.sh
#   sh tools/lab.sh boot-iso       boots the ISO (which must already be built)
#   sh tools/lab.sh ftp-alg off    passes "off" through to the lab
#   sh tools/lab.sh pptp off       the same, for the PPTP lab
#
# Anything after the lab name reaches the lab as its arguments. That is what
# lets a lab be run with the feature it tests switched off, which is the only
# way to know the lab can fail at all.
#
# Anything after the lab name reaches the lab as its arguments. That is what
# lets a lab be run with the feature it tests switched off, which is the only
# way to know the lab can fail at all.
#
# On this machine the docker group only takes effect in a new login, so we
# re-exec through "sg docker" when the socket denies access.
#
# The package list below is one list for every lab on purpose. It already had
# to learn this once: lab/install-iso.sh reads the installed disk back with
# sfdisk and the live image's /etc/shadow with unsquashfs, and "make test-install"
# happened to install both while this runner installed neither - so the same
# lab gave two answers depending on which command ran it, and the one that
# could not look reported it as something the install had done wrong.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${IMAGE:-prisma-routing-dev:26.06}

if [ $# -eq 0 ]; then
  echo "available labs:"
  for f in "$REPO"/lab/*.sh; do
    n=$(basename "$f" .sh)
    d=$(sed -n '2s/^# //p' "$f")
    printf "  %-16s %s\n" "$n" "$d"
  done
  exit 0
fi

LAB=$1
shift
[ -f "$REPO/lab/$LAB.sh" ] || { echo "lab '$LAB' does not exist" >&2; exit 1; }
LAB_ARGV=$*

if ! docker ps >/dev/null 2>&1; then
  # LAB_WORKERS has to survive the re-exec through sg, or asking for a worker
  # run gets a single-threaded one and reports a pass for a configuration that
  # was never tested. That is the shape of #199 and it is not going to be
  # reintroduced by the runner.
  exec sg docker -c "IMAGE='$IMAGE' LAB_WORKERS='${LAB_WORKERS:-}' \
                     LAB_WORKER_COUNT='${LAB_WORKER_COUNT:-}' \
                     PRISMA_VERSION_STAMP='${PRISMA_VERSION_STAMP:-}' \
                     ISO='${ISO:-}' BENCH_CACHE='${BENCH_CACHE:-}' \
                     BENCH_REINSTALL='${BENCH_REINSTALL:-}' \
                     BENCH_ROUNDS='${BENCH_ROUNDS:-}' \
                     BENCH_SECONDS='${BENCH_SECONDS:-}' \
                     BENCH_UPGRADE='${BENCH_UPGRADE:-}' \
                     '$0' '$LAB' $LAB_ARGV"
fi

# The version stamp the plugin carries, read on the HOST and handed over (#291).
#
# Every lab starts with `ninja` in the shared /src/.build-vpp, and since #291
# that build regenerates the header holding the stamp. Inside the container a
# git WORKTREE has no .git directory - the file there points outside the bind
# mount - so the stamp read in here is "not a git tree" while the one read out
# there is the commit. Without this the header would flip back and forth, every
# one of the forty labs would relink the plugin before it started, and sixteen
# of them running at once would be writing that header in the same build
# directory at the same time. The whole reason run-all-labs.sh builds first is
# that ninja is then a no-op; this is what keeps it one.
PRISMA_VERSION_STAMP=${PRISMA_VERSION_STAMP:-$(sh "$REPO/tools/version-stamp.sh" "$REPO")}

# --device=/dev/kvm when it exists: an ISO boots in seconds instead of minutes
KVM=""
[ -w /dev/kvm ] && KVM="--device=/dev/kvm"

# The ISO labs boot a virtual machine INSIDE this container, and that VM asks
# for 8 GB. Under the 5 GB default the cgroup kills it part-way through, which
# looks like the install failing rather than like the runner refusing to hold
# it - the lab then reports whatever it could read off a disk that was never
# flushed. A lab that does not fit in its own runner fails for a reason that
# has nothing to do with what it tests.
case "$LAB" in
  boot-iso|install-iso) LAB_MEM=${LAB_MEM:-10g} ;;
  # Three guests: the box at 6 GB, the subscribers and the internet at 1 GB.
  bench3vm) LAB_MEM=${LAB_MEM:-10g} ;;
esac

# THE BENCH'S CACHE, OUTSIDE THE TREE (#475). lab/bench3vm.sh keeps the ISO,
# the installed disk, the prepared Debian image and every run's evidence in
# /var/cache/prisma, where build caches live; mounted only for that lab, so
# no other lab can come to depend on what happens to be cached there.
LAB_CACHE=""
if [ "$LAB" = bench3vm ]; then
  BENCH_CACHE=${BENCH_CACHE:-/var/cache/prisma/bench3vm}
  mkdir -p "$BENCH_CACHE"
  LAB_CACHE="-v $BENCH_CACHE:/cache"
fi

# APT OVER IPv4, AND WITH A DEADLINE. Measured on 2026-09-13, mid release
# suite: `getent hosts deb.debian.org` inside the container returns only
# 2a04:4e42:... addresses, `curl -4` gets 200 and `curl -6` gets nothing, so
# apt picked IPv6 and sat there. lab/oom.sh off held one runner for 29 minutes
# without printing a line and without failing.
#
# The stall is worse than the failure, and that is the reason for the timeout
# as much as for the address family. A lab that fails says so in its verdict
# and the suite classifies it; a lab that hangs takes a slot out of the suite
# for as long as nobody is looking, and the release it was run for waits on it.
# This box's own IPv6 reachability is not something a lab is testing.
exec docker run --rm --privileged $KVM --memory="${LAB_MEM:-5g}" \
  -e BPA_RTSP_OFF="${BPA_RTSP_OFF:-}" \
  -e LAB_WORKERS="${LAB_WORKERS:-}" \
  -e LAB_WORKER_COUNT="${LAB_WORKER_COUNT:-}" \
  -e ISO="${ISO:-}" \
  -e BOOT_NO_1G="${BOOT_NO_1G:-}" \
  -e BOOT_IOMMU="${BOOT_IOMMU:-}" \
  -e LOGCOPY="${LOGCOPY:-}" \
  -e BENCH_REINSTALL="${BENCH_REINSTALL:-}" \
  -e BENCH_ROUNDS="${BENCH_ROUNDS:-}" \
  -e BENCH_SECONDS="${BENCH_SECONDS:-}" \
  -e BENCH_UPGRADE="${BENCH_UPGRADE:-}" \
  -e BENCH_GROUPS="${BENCH_GROUPS:-}" \
  -e BENCH_SEED="${BENCH_SEED:-}" \
  -e BENCH_SCHED_SECONDS="${BENCH_SCHED_SECONDS:-}" \
  -e BENCH_REBALANCE_AFTER="${BENCH_REBALANCE_AFTER:-}" \
  $LAB_CACHE \
  -e PRISMA_VERSION_STAMP="$PRISMA_VERSION_STAMP" \
  -v "$REPO:/src" -v "$HOME/.ssh:/root/.ssh:ro" -w /src "$IMAGE" \
  sh -c "cat > /etc/apt/apt.conf.d/99lab <<'APTCONF'
Acquire::ForceIPv4 \"true\";
Acquire::http::Timeout \"20\";
Acquire::https::Timeout \"20\";
Acquire::Retries \"2\";
APTCONF
         apt-get update -qq >/dev/null 2>&1
         apt-get install -y -qq qemu-system-x86 qemu-utils fdisk squashfs-tools mdadm openssh-client openssh-server snmpd snmp smitools iptables tcpdump isc-dhcp-client >/dev/null 2>&1
         # At the version of the frr already installed, never the newest: frr-snmp
         # depends on frr at its own version, and apt would upgrade FRR under the lab (#502).
         dpkg -s frr-snmp >/dev/null 2>&1 || apt-get install -y -qq --no-install-recommends frr-snmp=\$(dpkg-query -W -f='\${Version}' frr) >/dev/null 2>&1
         sh /src/lab/$LAB.sh $LAB_ARGV"
