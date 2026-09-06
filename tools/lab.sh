#!/bin/sh
# Runs a lab in the build container.
#
#   sh tools/lab.sh                lists the labs
#   sh tools/lab.sh datapath       runs lab/datapath.sh
#   sh tools/lab.sh boot-iso       boots the ISO (which must already be built)
#   sh tools/lab.sh pptp off       anything after the name is passed to the lab
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
# Passed through to the lab. A lab that can be told to run with a feature
# switched off is how the other half of an assertion gets proved: that it
# fails when the feature is not there.
ARGS=$*

if ! docker ps >/dev/null 2>&1; then
  exec sg docker -c "IMAGE='$IMAGE' '$0' '$LAB' $ARGS"
fi

# --device=/dev/kvm when it exists: an ISO boots in seconds instead of minutes
KVM=""
[ -w /dev/kvm ] && KVM="--device=/dev/kvm"

exec docker run --rm --privileged $KVM --memory="${LAB_MEM:-5g}" \
  -v "$REPO:/src" -v "$HOME/.ssh:/root/.ssh:ro" -w /src "$IMAGE" \
  sh -c "apt-get update -qq >/dev/null 2>&1
         apt-get install -y -qq qemu-system-x86 openssh-client iptables tcpdump >/dev/null 2>&1
         sh /src/lab/$LAB.sh $ARGS"
