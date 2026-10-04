#!/bin/sh
# OFF-RED-AT-LEAST: 5
# An upgraded box keeps the images it may still need and loses the rest
# (#569), against a real docker.
#
#   sh tools/lab.sh prune-images        -> LAB PASSED
#   sh tools/lab.sh prune-images off    -> LAB FAILED (PRISMA_PRUNE_IMAGES=0:
#                                          every old image stays, as before)
#
# THE DEFECT, measured on 10.40.200.166 on 2026-10-04: `/` at 96 % - 358 MB
# free of 8.3 GB - with 118 container images, because every patch upgrade
# loaded a new prisma-mgmt, prisma-vpp, prisma-frr and prisma-log and nothing
# ever removed the old ones.
#
# test/test_load_the_images.sh holds the rule against a stand-in docker. This
# is the same appliance/load-the-images against a REAL dockerd, because what a
# stand-in cannot say is what docker itself prints for `images --format`,
# `ps -a --format '{{.Image}}'` and `rmi` on this platform - and the rule is
# only as good as those three readings.
#
# The dockerd is this lab's own: started in the lab container on its own
# socket and data root, so nothing here can touch the images of the machine
# running the lab. The images are empty (`docker import` of an empty tar), so
# nothing is pulled. The box it builds is a 0.150.6 box upgraded from 0.150.5
# whose dataplane and FRR still run 0.150.1's images until their next boot,
# with a STOPPED container still on a 0.148.0 image, three older releases'
# images besides, and another repository's image with an x.y.z tag.
set -u
MODE=${1:-on}
cd /src
. /src/lab/verdict.sh

W=/tmp/prune-lab
rm -rf "$W"; mkdir -p "$W/root" "$W/run"
export DEBIAN_FRONTEND=noninteractive
command -v dockerd >/dev/null 2>&1 || {
  apt-get update -qq >/tmp/apt-docker.log 2>&1
  apt-get install -y -qq docker.io >>/tmp/apt-docker.log 2>&1
}
expect_setup "docker is installed in the lab container" command -v dockerd

SOCK=unix://$W/docker.sock
dockerd --data-root "$W/root" --host "$SOCK" --storage-driver vfs \
        --bridge none --iptables=false --ip6tables=false \
        --pidfile "$W/dockerd.pid" >"$W/dockerd.log" 2>&1 &
export DOCKER_HOST=$SOCK
_t=0
until docker info >/dev/null 2>&1; do
  sleep 1; _t=$((_t + 1)); [ "$_t" -ge 60 ] && break
done
expect_setup "the lab's own dockerd answers ($(tail -1 "$W/dockerd.log" | cut -c1-80))" \
  docker info

mkimage() {  # <repo:tag>
  tar -cf - -T /dev/null | docker import - "$1" >/dev/null
}
for r in 0.148.0 0.149.33 0.150.1 0.150.5 0.150.6; do
  for c in mgmt vpp frr log; do mkimage "prisma-$c:$r"; done
done
mkimage "other/thing:1.2.3"
# The containers: created, never started - a container docker knows, which is
# what `ps -a` reads, without a runtime this lab would have to provide.
docker create --name vpp  prisma-vpp:0.150.1  /x >/dev/null
docker create --name frr  prisma-frr:0.150.1  /x >/dev/null
docker create --name mgmt prisma-mgmt:0.150.6 /x >/dev/null
docker create --name log  prisma-log:0.150.6  /x >/dev/null
docker create --name old  prisma-mgmt:0.148.0 /x >/dev/null
expect_setup "21 images and 5 containers built" \
  test "$(docker images --format x | wc -l)" = 21

# The release the upgrade carried, as its tarball.
docker save prisma-mgmt:0.150.6 prisma-vpp:0.150.6 prisma-frr:0.150.6 \
            prisma-log:0.150.6 | gzip > "$W/images.tar.gz"
echo "0.150.6 (lab)" > "$W/VERSION"

load() {
  _off=1; [ "$MODE" = off ] && _off=0
  PRISMA_PRUNE_IMAGES=$_off PRISMA_DOCKER=docker \
  PRISMA_IMAGES="$W/images.tar.gz" PRISMA_VERSION_FILE="$W/VERSION" \
  PRISMA_COMPOSE_ENV="$W/run/compose.env" PRISMA_DOCKER_ROOT="$W/root" \
    sh /src/appliance/load-the-images 2>&1
}
OUT=$(load); RC=$?
echo "$OUT" | sed 's/^/    /'
HAVE=$(docker images --format '{{.Repository}}:{{.Tag}}' | sort | tr '\n' ' ')
echo "  images left: $HAVE"

KEEP="other/thing:1.2.3 prisma-frr:0.150.1 prisma-frr:0.150.5 prisma-frr:0.150.6 prisma-log:0.150.5 prisma-log:0.150.6 prisma-mgmt:0.148.0 prisma-mgmt:0.150.5 prisma-mgmt:0.150.6 prisma-vpp:0.150.1 prisma-vpp:0.150.5 prisma-vpp:0.150.6 "
expect "load-the-images exits 0 ($RC)" test "$RC" = 0
expect_in "and compose is told this box's release" "PRISMA_VERSION=0.150.6" \
  "$(cat "$W/run/compose.env" 2>/dev/null)"
expect "exactly what may still be needed is left: this release, 0.150.5, what the containers use (a stopped one's included), another repository's" \
  test "$HAVE" = "$KEEP"
expect "no 0.149.33 image is left" \
  sh -c '! docker images --format "{{.Tag}}" | grep -qx 0.149.33'
expect "0.150.1's are gone except the two the dataplane and FRR still run" \
  test "$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -c ':0.150.1$')" = 2
expect_in "and it says what it removed" "removed 9 image(s)" "$OUT"

# Twice is once: the next boot finds nothing more to remove.
OUT2=$(load)
expect_not_in "a second run removes nothing more" "removed" "$OUT2"
expect "and leaves the same set" \
  test "$(docker images --format '{{.Repository}}:{{.Tag}}' | sort | tr '\n' ' ')" = "$KEEP"

kill "$(cat "$W/dockerd.pid" 2>/dev/null)" 2>/dev/null
verdict
