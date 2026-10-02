#!/bin/sh
# appliance/refresh-the-vty, driven against a stubbed docker, docker-compose,
# load-the-images and prisma-backplane (#528).
#
# THE DEFECT IT CAME FROM, measured on the X520 bench on 2026-09-30: after
# `system upgrade 0.147.4` the VTY in the management container was still
# prisma-mgmt:0.147.3, because images are loaded and containers created only
# when prisma-routing-containers.service starts, at boot.
#
# WHAT IS ASSERTED: that only the management service is recreated (never the
# dataplane's or the control plane's), with the environment file the
# containers unit uses; that nothing is recreated when the image to run is
# not this box's release or is the one already running; and that a box with
# no management container is left alone. Not `docker` itself: a test that
# needs a daemon is one `make test` cannot run.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)
BIN=$REPO/appliance/refresh-the-vty

mkbox() { # <image mgmt runs now, or - for none> <box version> <tag load-the-images picks> [load rc]
  B=$(mktemp -d)
  mkdir -p "$B/bin" "$B/run" "$B/etc"
  printf '%s\n' "$2" > "$B/VERSION"
  : > "$B/etc/compose.yaml"
  : > "$B/run/compose-devices.yaml"
  if [ "$1" = - ]; then
    printf '#!/bin/sh\nexit 1\n' > "$B/bin/docker"
  else
    # inspect answers the image mgmt runs: the old one until compose ran.
    cat > "$B/bin/docker" <<DC
#!/bin/sh
if [ -f "$B/recreated" ]; then cat "$B/recreated"; else echo "$1"; fi
DC
  fi
  cat > "$B/bin/load" <<LD
#!/bin/sh
printf 'PRISMA_VERSION=%s\nPRISMA_VPP_GID=997\n' "$3" > "$B/run/compose.env"
exit ${4:-0}
LD
  cat > "$B/bin/compose" <<CP
#!/bin/sh
echo "\$*" >> "$B/compose.argv"
echo "prisma-mgmt:$3" > "$B/recreated"
CP
  printf '#!/bin/sh\necho "$*" >> %s/backplane.argv\n' "$B" > "$B/bin/backplane"
  printf '#!/bin/sh\necho "$*" >> %s/systemctl.argv\n' "$B" > "$B/bin/systemctl"
  chmod +x "$B/bin/"*
  echo "$B"
}
run() { # <box>
  PRISMA_DOCKER=$1/bin/docker PRISMA_COMPOSE=$1/bin/compose \
  PRISMA_LOAD_IMAGES=$1/bin/load PRISMA_BACKPLANE=$1/bin/backplane \
  PRISMA_SYSTEMCTL=$1/bin/systemctl \
  PRISMA_COMPOSE_ENV=$1/run/compose.env \
  PRISMA_COMPOSE_YAML=$1/etc/compose.yaml \
  PRISMA_COMPOSE_DEVICES=$1/run/compose-devices.yaml \
  PRISMA_VERSION_FILE=$1/VERSION \
  sh "$BIN" 2>&1
}

echo "=== two asked at once: one recreates, the other finds it current (#529) ==="
# The upgrade script schedules it and a 0.148.0 VTY asks again. Under the lock
# the second waits for the first, then reads the new image and does nothing.
if command -v flock >/dev/null 2>&1; then
  B=$(mkbox "prisma-mgmt:0.148.0" "0.149.0" "0.149.0")
  # compose takes a second, so an unlocked pair would both run it.
  cat > "$B/bin/compose" <<CP
echo "\$*" >> "$B/compose.argv"
sleep 1
echo "prisma-mgmt:0.149.0" > "$B/recreated"
CP
  chmod +x "$B/bin/compose"
  export PRISMA_REFRESH_LOCK=$B/run/refresh.lock
  run "$B" > "$B/one.out" & P1=$!
  run "$B" > "$B/two.out" & P2=$!
  wait $P1; wait $P2
  unset PRISMA_REFRESH_LOCK
  N=$(grep -c "up -d --no-deps mgmt" "$B/compose.argv" 2>/dev/null)
  [ "$N" = 1 ] && ok "compose recreated the VTY's container once for the two of them" \
    || bad "compose recreated mgmt $N times: two refreshes raced on one container"
  cat "$B/one.out" "$B/two.out" | grep -q "already runs prisma-mgmt:0.149.0" \
    && ok "and the second said there was nothing left to do" \
    || bad "the second did not find the image current: $(cat "$B/one.out" "$B/two.out")"
  rm -rf "$B"
else
  echo "  SKIP: no flock on this machine"
fi

echo "=== an upgraded box: the VTY moves, nothing else does ==="
B=$(mkbox "prisma-mgmt:0.147.3" "0.147.4" "0.147.4")
OUT=$(run "$B"); RC=$?
ARGV=$(head -1 "$B/compose.argv" 2>/dev/null)
LOGGER=$(sed -n 2p "$B/compose.argv" 2>/dev/null)
[ "$RC" = 0 ] && ok "it exits 0" || bad "it exited $RC: $OUT"
case "$LOGGER" in
  *"--env-file $B/run/compose.env "*"up -d --no-deps logger")
    ok "and then brings the log container up on the same images (#550)" ;;
  *) bad "the log container was not brought up after the VTY's: '$LOGGER'" ;;
esac
case "$ARGV" in
  *"--env-file $B/run/compose.env "*"up -d --no-deps mgmt")
    ok "compose recreates the mgmt service alone, with the unit's environment"
    ;;
  *) bad "compose was run as: '$ARGV'" ;;
esac
# Mutation shown red: `--no-deps mgmt` dropped from the compose line, which
# would recreate the dataplane on the new image, traffic dropped - the case
# above goes red. This one holds the names on their own.
printf '%s' "$ARGV" | grep -qE '(^| )(vpp|frr)( |$)' \
  && bad "the dataplane or the control plane was named: '$ARGV'" \
  || ok "and neither vpp nor frr is named to it"
# The API's server lives in the container just replaced (#532): its unit is
# asked to come back at once, and only if it was running (try-restart).
# Mutation shown red: the try-restart line removed from refresh-the-vty.
grep -qx "try-restart prisma-routing-api.service" "$B/systemctl.argv" 2>/dev/null \
  && ok "the management API is asked to restart into the new container, if it ran" \
  || bad "the API's server is left behind in the old container: $(cat "$B/systemctl.argv" 2>/dev/null)"
grep -q "attach prisma-routing_mgmt_1" "$B/backplane.argv" 2>/dev/null \
  && ok "the recreated container is put back on the backplane" \
  || bad "the backplane was not asked for it"
printf '%s\n' "$OUT" | grep -q "prisma-mgmt:0.147.3 -> prisma-mgmt:0.147.4" \
  && ok "and it says what it did: $(printf '%s' "$OUT" | grep -- '->' | head -1)" \
  || bad "it did not say before and after: $OUT"
rm -rf "$B"

echo
echo "=== the images did not load: nothing is recreated ==="
# load-the-images falls back to the image already here, which is right at
# boot and pointless here: the same image, and every session ended for it.
B=$(mkbox "prisma-mgmt:0.147.3" "0.147.4" "0.147.3")
OUT=$(run "$B"); RC=$?
[ "$RC" != 0 ] && [ ! -f "$B/compose.argv" ] \
  && ok "refused, and compose never ran: $(printf '%s' "$OUT" | head -1)" \
  || bad "rc=$RC, compose: '$(cat "$B/compose.argv" 2>/dev/null)'"
rm -rf "$B"
B=$(mkbox "prisma-mgmt:0.147.3" "0.147.4" "0.147.4" 1)
OUT=$(run "$B"); RC=$?
[ "$RC" != 0 ] && [ ! -f "$B/compose.argv" ] \
  && ok "and so when load-the-images itself fails" \
  || bad "a failed load went on: rc=$RC"
rm -rf "$B"

echo
echo "=== already on this release: nothing to do ==="
B=$(mkbox "prisma-mgmt:0.147.4" "0.147.4" "0.147.4")
OUT=$(run "$B"); RC=$?
[ "$RC" = 0 ] && ! grep -q "no-deps mgmt" "$B/compose.argv" 2>/dev/null \
  && ok "the container is left, and every session in it: $OUT" \
  || bad "a VTY already on 0.147.4 was recreated: rc=$RC"
grep -q "up -d --no-deps logger" "$B/compose.argv" 2>/dev/null \
  && ok "but the log container is brought up: a patch is how it first arrives (#550)" \
  || bad "a box already on this release never got its log container"
rm -rf "$B"

echo
echo "=== a box with no management container ==="
B=$(mkbox - "0.147.4" "0.147.4")
OUT=$(run "$B"); RC=$?
[ "$RC" = 0 ] && [ ! -f "$B/compose.argv" ] \
  && ok "the units path is left alone, and says so: $OUT" \
  || bad "rc=$RC on a box with no container: $OUT"
rm -rf "$B"

echo
[ "$fail" = 0 ] && echo "ALL TESTS PASSED" || echo "$fail FAILURES"
exit "$fail"
