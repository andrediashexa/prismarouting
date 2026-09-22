#!/bin/sh
# appliance/load-the-images, driven against a stubbed docker.
#
# THE DEFECT IT CAME FROM, measured on a booted box by lab/install-iso.sh's
# PHASE 6 (#426): compose.yaml asks for `prisma-vpp:${PRISMA_VERSION:-latest}`
# and tools/build-images.sh saves the images tagged with the VERSION file. On
# an installed box nothing set PRISMA_VERSION, so compose asked for
# `prisma-vpp:latest`, did not find it, and went to a registry an appliance
# has no route to. What the operator saw was no containers at all.
#
# WHAT IS ASSERTED is the choice of tag and the refusal, not `docker load` -
# the stub answers for docker, because a test that needs a daemon is a test
# that does not run in `make test`.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)
BIN=$REPO/appliance/load-the-images

mkbox() { # <tags, newest first, space separated> <version stamp>
  B=$(mktemp -d)
  mkdir -p "$B/bin" "$B/run" "$B/share"
  printf '%s\n' "$2" > "$B/share/VERSION"
  cat > "$B/bin/docker" <<DC
#!/bin/sh
case "\$1" in
  load)   echo "Loaded image: prisma-vpp:whatever" ;;
  images) for t in $1; do echo "\$t"; done ;;
esac
exit 0
DC
  chmod +x "$B/bin/docker"
  echo "$B"
}
run() { # <box>
  PRISMA_ROOT=$1 PRISMA_DOCKER=$1/bin/docker \
  PRISMA_IMAGES=$1/images.tar.gz \
  PRISMA_VERSION_FILE=$1/share/VERSION \
  PRISMA_COMPOSE_ENV=$1/run/compose.env \
  sh "$BIN" 2>&1
}

echo "=== the tag this box says it is ==="
B=$(mkbox "0.115.3 0.115.2" "0.115.3")
OUT=$(run "$B"); RC=$?
[ "$RC" = 0 ] && ok "it exits 0 when there is an image to run" \
  || bad "it exited $RC: $OUT"
V=$(cat "$B/run/compose.env" 2>/dev/null)
[ "$V" = "PRISMA_VERSION=0.115.3" ] \
  && ok "and compose is told the version the box says it is: $V" \
  || bad "compose was told '$V' on a box whose stamp says 0.115.3"
rm -rf "$B"

echo
echo "=== an upgraded box, with the previous release's images still loaded ==="
# `docker load` leaves what was there, so "the only image" is not a safe
# reading: the dataplane from another release is one whose API this release's
# plugin may not match, which is what the pins in build-iso.sh are for.
B=$(mkbox "0.115.2 0.115.3" "0.115.3")
run "$B" >/dev/null 2>&1
V=$(cat "$B/run/compose.env" 2>/dev/null)
[ "$V" = "PRISMA_VERSION=0.115.3" ] \
  && ok "the box's own version wins over the one docker lists first" \
  || bad "compose was told '$V', which is not what this box is"
rm -rf "$B"

echo
echo "=== a box whose stamp matches no image ==="
# A source install, or a stamp carrying a git hash nothing was tagged with.
# It still has to start, and it has to SAY that the images and the code came
# from different builds.
B=$(mkbox "0.115.2" "0.116.0-abc1234")
OUT=$(run "$B"); RC=$?
V=$(cat "$B/run/compose.env" 2>/dev/null)
[ "$RC" = 0 ] && [ "$V" = "PRISMA_VERSION=0.115.2" ] \
  && ok "it starts the image it has rather than refusing" \
  || bad "a box with no matching tag got rc=$RC and '$V'"
printf '%s\n' "$OUT" | grep -q "different builds" \
  && ok "and says the images and the code did not come from one build" \
  || bad "it started a mismatched image silently: $OUT"
rm -rf "$B"

echo
echo "=== a box with no prisma-vpp image at all ==="
# THE REFUSAL THAT MATTERS. Left to compose, this is a pull from a registry
# the appliance cannot reach, and what it prints is about a name nobody
# recognises - on a box whose real problem is that its images are missing.
B=$(mkbox "" "0.115.3")
OUT=$(run "$B"); RC=$?
[ "$RC" != 0 ] && ok "it refuses, rather than leaving compose to ask a registry" \
  || bad "a box with no image exited 0, so compose would try to pull"
printf '%s\n' "$OUT" | grep -q "no route to one" \
  && ok "and says why an appliance cannot simply fetch it" \
  || bad "the refusal does not explain itself: $OUT"
[ ! -s "$B/run/compose.env" ] \
  && ok "and writes no version, because there is nothing to run" \
  || bad "it wrote a version for an image that is not there"
rm -rf "$B"

echo
echo "=== the box's vpp group id goes in the same file ==="
# VPP resolves `gid vpp` from startup.conf in the CONTAINER's /etc/group, and
# /run/vpp is a bind mount - so without the box's own number the CLI socket
# comes out group-owned by a gid that is nobody's on the box, and an operator
# account is told "the dataplane is not answering" by a dataplane that
# answers root. The host is the only place that number exists.
B=$(mkbox "0.115.3" "0.115.3")
cat > "$B/bin/getent" <<'GE'
#!/bin/sh
[ "$2" = vpp ] && echo "vpp:x:4242:"
exit 0
GE
chmod +x "$B/bin/getent"
PATH="$B/bin:$PATH" run "$B" >/dev/null 2>&1
grep -q "^PRISMA_VPP_GID=4242$" "$B/run/compose.env" \
  && ok "the box's own vpp gid is written where compose reads it" \
  || bad "compose.env has no PRISMA_VPP_GID: $(tr '\n' ' ' < "$B/run/compose.env")"
rm -rf "$B"

# A box with no vpp group at all says so rather than writing an empty one:
# an empty value in compose.env is a value the entrypoint would act on.
B=$(mkbox "0.115.3" "0.115.3")
cat > "$B/bin/getent" <<'GE'
#!/bin/sh
exit 2
GE
chmod +x "$B/bin/getent"
OUT=$(PATH="$B/bin:$PATH" run "$B" 2>&1)
grep -q "PRISMA_VPP_GID" "$B/run/compose.env" \
  && bad "a box with no vpp group got a PRISMA_VPP_GID line anyway" \
  || ok "a box with no vpp group writes none"
printf '%s\n' "$OUT" | grep -q "only root will reach the dataplane" \
  && ok "and says what that costs" \
  || bad "it was silent about a box with no vpp group: $OUT"
rm -rf "$B"

echo
echo "=== the unit runs it, and reads what it writes ==="
U=$REPO/appliance/systemd/prisma-routing-containers.service
grep -q "ExecStartPre=/usr/lib/prisma-routing/load-the-images" "$U" \
  && ok "the containers unit loads the images through it" \
  || bad "$U does not run load-the-images"
grep -q "EnvironmentFile=-/run/prisma-routing/compose.env" "$U" \
  && ok "and takes PRISMA_VERSION out of the file it writes" \
  || bad "$U does not read /run/prisma-routing/compose.env, so compose would fall back to :latest - which is the tag nothing is ever saved under"
grep -q "^TimeoutStartSec=" "$U" \
  && ok "and is given longer than the 90s default to load 118 MB of gzip" \
  || bad "$U has no TimeoutStartSec: a slow box would have the load killed half way and the unit marked failed"
grep -q "share/prisma-routing/VERSION" "$BIN" \
  && ok "and the version comes from the box's own stamp" \
  || bad "load-the-images does not read /usr/share/prisma-routing/VERSION"
grep -v '^[[:space:]]*#' "$REPO/appliance/compose.yaml" \
  | grep -q "PRISMA_VPP_GID" \
  && ok "and compose hands PRISMA_VPP_GID to the dataplane" \
  || bad "appliance/compose.yaml does not pass PRISMA_VPP_GID, so the number never reaches the container and the CLI socket keeps the image's own group"
grep -v '^[[:space:]]*#' "$REPO/appliance/vpp-entrypoint" \
  | grep -q "groupmod -g" \
  && ok "and the entrypoint moves the container's vpp group to it" \
  || bad "appliance/vpp-entrypoint does not move group vpp to PRISMA_VPP_GID"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
