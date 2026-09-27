#!/bin/sh
# Is the kernel the medium carries Debian's current one? (#449)
#
#   sh tools/kernel-current.sh      exit 0 current, 1 behind, 2 could not ask
#
# THE COST #449 ACCEPTED, MADE LOUD. Debian's kernel reached a box by apt with
# nobody here choosing it. Prisma Routing's kernel is Debian's source at the
# version in appliance/kernel.env, rebuilt, so a Debian security update
# reaches no box until somebody moves that line - and the failure mode of
# "somebody" is silence. The release gate asks this before building a medium,
# and refuses one whose kernel Debian has already replaced.
#
# Measured the day it was written: kernel.env said 6.1.176-1, the medium's
# kernel until then, and Debian's candidate was 6.1.187-1 - five security
# releases, found only because the developer host happened to run a newer one.
#
# KERNEL_CANDIDATE is the test's seam: the version Debian would answer.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
. "$REPO/appliance/kernel.env"
have=$KERNEL_DEB_VERSION
want=${KERNEL_CANDIDATE:-}
if [ -z "$want" ]; then
  want=$(sg docker -c "docker run --rm debian:bookworm sh -c 'apt-get update -qq >/dev/null 2>&1; apt-cache policy linux-image-amd64'" 2>/dev/null \
         | sed -n 's/^ *Candidate: *//p')
fi
case "$want" in
  ""|"(none)")
    echo "Debian's current kernel could not be asked (no docker or no network):"
    echo "kernel.env says $have, and nothing here says whether that is current"
    exit 2 ;;
esac
if [ "$have" = "$want" ]; then
  echo "the kernel is Debian's current one: $have"
  exit 0
fi
if dpkg --compare-versions "$have" gt "$want" 2>/dev/null; then
  echo "kernel.env says $have, newer than Debian's candidate $want: not a"
  echo "version this archive serves"
  exit 1
fi
echo "kernel.env says $have and Debian's current kernel is $want: the medium"
echo "would ship a kernel Debian has already replaced, and whatever that fixed."
echo "Move appliance/kernel.env to $want (and KERNEL_DEB_ABI to its ABI), then"
echo "'make kernel' builds it."
exit 1
