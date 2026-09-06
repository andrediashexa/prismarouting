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
# On this machine the docker group only takes effect in a new login, so we
# re-exec through "sg docker" when the socket denies access.
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
  exec sg docker -c "IMAGE='$IMAGE' '$0' '$LAB' $LAB_ARGV"
fi

# --device=/dev/kvm when it exists: an ISO boots in seconds instead of minutes
KVM=""
[ -w /dev/kvm ] && KVM="--device=/dev/kvm"

exec docker run --rm --privileged $KVM --memory="${LAB_MEM:-5g}" \
  -e BPA_RTSP_OFF="${BPA_RTSP_OFF:-}" \
  -v "$REPO:/src" -v "$HOME/.ssh:/root/.ssh:ro" -w /src "$IMAGE" \
  sh -c "apt-get update -qq >/dev/null 2>&1
         apt-get install -y -qq qemu-system-x86 openssh-client iptables tcpdump >/dev/null 2>&1
         sh /src/lab/$LAB.sh $LAB_ARGV"
