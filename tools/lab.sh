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
IMAGE=${IMAGE:-hexarouting-dev:26.06}

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
                     '$0' '$LAB' $LAB_ARGV"
fi

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
esac

exec docker run --rm --privileged $KVM --memory="${LAB_MEM:-5g}" \
  -e BPA_RTSP_OFF="${BPA_RTSP_OFF:-}" \
  -e LAB_WORKERS="${LAB_WORKERS:-}" \
  -e LAB_WORKER_COUNT="${LAB_WORKER_COUNT:-}" \
  -v "$REPO:/src" -v "$HOME/.ssh:/root/.ssh:ro" -w /src "$IMAGE" \
  sh -c "apt-get update -qq >/dev/null 2>&1
         apt-get install -y -qq qemu-system-x86 qemu-utils fdisk squashfs-tools mdadm openssh-client openssh-server snmpd snmp iptables tcpdump >/dev/null 2>&1
         sh /src/lab/$LAB.sh $LAB_ARGV"
