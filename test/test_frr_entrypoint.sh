#!/bin/sh
# appliance/frr-entrypoint, driven with an frrinit and an id set that are
# stubs.
#
# WHAT IT IS FOR, and it was found the expensive way (#426). The control-plane
# image ran `/usr/lib/frr/frrinit.sh start` as its command: that script starts
# watchfrr and RETURNS, so PID 1 exited. And /etc/frr and /var/run/frr are
# bind mounts, which carry NUMERIC ids - the frr user inside the image is not
# the frr user on the box, so zebra could not write its pid file:
#
#   ZEBRA: Can't create pid lock file /var/run/frr/zebra.pid (Permission
#   denied), exiting
#
# watchfrr stayed up with nothing under it, the container read as `running`,
# and `prismash --load` refused the box's whole saved configuration with
# `router bgp 65000: bgpd is not running`. An installed box came up with no
# BGP and no CGNAT, reporting nothing wrong.
#
# So what is asserted here is the decision, with no docker and no FRR: which
# ids it moves the user to, that it does not move them when the box has none
# to give, that it starts FRR the way FRR starts itself, and that a SIGTERM
# stops the daemons rather than killing them where they stand.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }
ENTRY=$REPO/appliance/frr-entrypoint
[ -x "$ENTRY" ] || { echo "  FAIL: $ENTRY is not executable"; exit 1; }

# A box: /etc/frr owned by the ids this box's own FRR files carry, and stubs
# for the four programs that would change the container's passwd database.
mkbox() { # <uid of /etc/frr> <gid of /etc/frr>
  B=$(mktemp -d)
  mkdir -p "$B/bin" "$B/etc/frr" "$B/run/frr"
  cat > "$B/bin/stat" <<STUB
#!/bin/sh
# %u and %g of anything under /etc/frr is what this box was made with.
case "\$*" in
  *%u*) echo $1 ;;
  *%g*) echo $2 ;;
esac
STUB
  cat > "$B/bin/id" <<'STUB'
#!/bin/sh
case "$1" in
  -u) echo 100 ;;
  -g) echo 103 ;;
esac
STUB
  for p in usermod groupmod chown getent; do
    cat > "$B/bin/$p" <<STUB
#!/bin/sh
echo "$p \$*" >> "$B/calls"
exit 0
STUB
  done
  cat > "$B/bin/frrinit.sh" <<STUB
#!/bin/sh
echo "frrinit \$*" >> "$B/calls"
STUB
  cat > "$B/bin/pidof" <<STUB
#!/bin/sh
# watchfrr is there for as long as the file is.
[ -e "$B/watchfrr" ]
STUB
  chmod +x "$B/bin/"*
  echo "$B"
}

echo "=== the ids come off the box's own /etc/frr ==="
B=$(mkbox 1234 5678)
: > "$B/watchfrr"
( PATH="$B/bin:$PATH" FRRINIT="$B/bin/frrinit.sh" PRISMA_FRR_ETC="$B/etc/frr" PRISMA_FRR_RUN="$B/run/frr" \
  sh -c "cd $B && exec sh $ENTRY" >/dev/null 2>&1 & sleep 1; kill %1 2>/dev/null ) \
  >/dev/null 2>&1
# The calls file is written by the stubs in the order the entrypoint made them.
grep -q "groupmod -g 5678 -o frr" "$B/calls" \
  && ok "the group is moved to the gid /etc/frr carries" \
  || bad "the entrypoint did not move group frr to 5678: $(tr '\n' ' ' < "$B/calls")"
grep -q "usermod -u 1234 -o frr" "$B/calls" \
  && ok "and the user to its uid, which is what the pid file needs" \
  || bad "the entrypoint did not move user frr to 1234: $(tr '\n' ' ' < "$B/calls")"
grep -q "chown frr:frr" "$B/calls" \
  && ok "and the runtime directory is given to it" \
  || bad "nothing chowned /var/run/frr, so zebra cannot write its pid file"
grep -q "frrinit start" "$B/calls" \
  && ok "and FRR is started the way FRR starts itself" \
  || bad "frrinit.sh start was never run: $(tr '\n' ' ' < "$B/calls")"
rm -rf "$B"

echo
echo "=== a box whose /etc/frr is root's ==="
# A box that never installed FRR's own files. Moving the container's frr user
# to 0 would run the routing daemons as root, which is a worse answer than
# leaving the image's ids alone.
B=$(mkbox 0 0)
: > "$B/watchfrr"
( PATH="$B/bin:$PATH" FRRINIT="$B/bin/frrinit.sh" PRISMA_FRR_ETC="$B/etc/frr" PRISMA_FRR_RUN="$B/run/frr" \
  sh -c "cd $B && exec sh $ENTRY" >/dev/null 2>&1 & sleep 1; kill %1 2>/dev/null ) \
  >/dev/null 2>&1
grep -q "usermod" "$B/calls" \
  && bad "the entrypoint moved frr to root's id: the daemons would run as root" \
  || ok "root-owned /etc/frr leaves the image's own ids alone"
grep -q "frrinit start" "$B/calls" \
  && ok "and FRR is still started" \
  || bad "a box with root-owned /etc/frr got no control plane at all"
rm -rf "$B"

echo
echo "=== the image runs it as PID 1, and not frrinit directly ==="
# The whole reason the file exists: frrinit.sh start returns, and a PID 1
# that returns is a container that is gone.
D=$REPO/build/Dockerfile.frr
grep -q 'ENTRYPOINT \["/usr/local/bin/frr-entrypoint"\]' "$D" \
  && ok "Dockerfile.frr makes the entrypoint PID 1" \
  || bad "$D does not run frr-entrypoint as its entrypoint"
grep -qE '^CMD \["/usr/lib/frr/frrinit.sh", "start"\]' "$D" \
  && bad "the image still runs frrinit.sh as its command: that script returns, so PID 1 exits and the control plane goes with it" \
  || ok "and does not run frrinit.sh as the command it returns from"
grep -q 'COPY appliance/frr-entrypoint' "$D" \
  && ok "and the file is copied in rather than written in the image" \
  || bad "$D does not COPY appliance/frr-entrypoint"
grep -q 'cp "$REPO/appliance/frr-entrypoint"' "$REPO/tools/build-images.sh" \
  && ok "and the build context carries it" \
  || bad "tools/build-images.sh does not put frr-entrypoint in the build context, so the image would not build at all"

echo
echo "=== a stop is a stop ==="
# `docker stop` sends SIGTERM to PID 1 and waits. Without a handler the
# daemons are killed where they stand: no GR-aware close for bgpd, and zebra
# leaves its routes in the kernel.
B=$(mkbox 1234 5678)
: > "$B/watchfrr"
PATH="$B/bin:$PATH" FRRINIT="$B/bin/frrinit.sh" PRISMA_FRR_ETC="$B/etc/frr" PRISMA_FRR_RUN="$B/run/frr" sh "$ENTRY" >/dev/null 2>&1 &
EPID=$!
sleep 1
kill -TERM "$EPID" 2>/dev/null
_n=0
while [ "$_n" -lt 20 ] && kill -0 "$EPID" 2>/dev/null; do sleep 0.25; _n=$((_n+1)); done
grep -q "frrinit stop" "$B/calls" \
  && ok "SIGTERM runs 'frrinit.sh stop' before PID 1 goes" \
  || bad "the daemons were killed where they stood: $(tr '\n' ' ' < "$B/calls")"
kill -0 "$EPID" 2>/dev/null \
  && bad "the entrypoint was still running after its stop" \
  || ok "and then it exits, so docker stop does not wait out its grace period"
rm -rf "$B"

echo
echo "=== watchfrr going is the container going ==="
# FRR supervises its daemons with watchfrr. A container that stayed up around
# a dead watchfrr would report health it does not have - which is exactly the
# state the box was found in.
B=$(mkbox 1234 5678)
PATH="$B/bin:$PATH" FRRINIT="$B/bin/frrinit.sh" PRISMA_FRR_ETC="$B/etc/frr" PRISMA_FRR_RUN="$B/run/frr" sh "$ENTRY" >"$B/out" 2>&1
RC=$?
[ "$RC" != 0 ] \
  && ok "it exits non-zero when watchfrr is not there, so the policy restarts it" \
  || bad "the entrypoint stayed up with no watchfrr under it"
grep -q "nothing is supervising" "$B/out" \
  && ok "and says why, in the container's log" \
  || bad "it exited silently: $(cat "$B/out")"
rm -rf "$B"

echo
echo "=== agentx is in frr.conf exactly while SNMP is enabled (#502) ==="
# A daemon cannot drop agentx once it has it, and with it and no snmpd every
# daemon logs a warning every 15 s: so the start decides, from the one file
# that exists only while SNMP is enabled.
agentx_run() { # <box>
  ( PATH="$1/bin:$PATH" FRRINIT="$1/bin/frrinit.sh" PRISMA_FRR_ETC="$1/etc/frr" \
    PRISMA_FRR_RUN="$1/run/frr" PRISMA_SNMPD_CONF="$1/snmpd.conf" \
    sh "$ENTRY" >/dev/null 2>&1 )
}
B=$(mkbox 1234 5678)
printf 'router bgp 65001\nexit\n' > "$B/etc/frr/frr.conf"
chmod 0660 "$B/etc/frr/frr.conf"
printf 'master agentx\nagentXSocket /var/agentx/master\n' > "$B/snmpd.conf"
agentx_run "$B"
[ "$(head -1 "$B/etc/frr/frr.conf")" = "agentx" ] \
  && ok "SNMP enabled: agentx is put first in frr.conf" \
  || bad "SNMP enabled and no agentx in frr.conf: $(cat "$B/etc/frr/frr.conf")"
grep -q "router bgp 65001" "$B/etc/frr/frr.conf" \
  && ok "and the routing is still there" \
  || bad "the routing was lost: $(cat "$B/etc/frr/frr.conf")"
[ "$(stat -c %a "$B/etc/frr/frr.conf")" = 660 ] \
  && ok "and the file keeps the mode #186 gave it" \
  || bad "frr.conf's mode changed to $(stat -c %a "$B/etc/frr/frr.conf")"
agentx_run "$B"
[ "$(grep -cx agentx "$B/etc/frr/frr.conf")" = 1 ] \
  && ok "a second start does not add it twice" \
  || bad "agentx appears $(grep -cx agentx "$B/etc/frr/frr.conf") times"
rm -f "$B/snmpd.conf"
agentx_run "$B"
grep -qx agentx "$B/etc/frr/frr.conf" \
  && bad "SNMP disabled and agentx still in frr.conf: every daemon would warn every 15 s" \
  || ok "SNMP disabled: agentx is taken out at the next start"
grep -q "router bgp 65001" "$B/etc/frr/frr.conf" \
  && ok "and the routing is still there" \
  || bad "the routing was lost: $(cat "$B/etc/frr/frr.conf")"
ls "$B/etc/frr" | grep -q "\.agentx" \
  && bad "a scratch file was left in /etc/frr" \
  || ok "and no scratch file is left behind"
rm -rf "$B"

echo
echo "=== bfdd is on from the first start of this image (#559) ==="
# The installer enables the other routing daemons and a patch cannot change
# it, so the image that rides every upgrade turns bfdd on before FRR starts.
B=$(mkbox 1234 5678)
printf 'bgpd=yes\nbfdd=no\nvtysh_enable=yes\n' > "$B/etc/frr/daemons"
chmod 0640 "$B/etc/frr/daemons"
agentx_run "$B"
grep -qx "bfdd=yes" "$B/etc/frr/daemons" && ! grep -qx "bfdd=no" "$B/etc/frr/daemons" \
  && ok "Debian's bfdd=no becomes bfdd=yes" \
  || bad "bfdd is not on: $(cat "$B/etc/frr/daemons")"
grep -qx "bgpd=yes" "$B/etc/frr/daemons" && grep -qx "vtysh_enable=yes" "$B/etc/frr/daemons" \
  && ok "and nothing else in the file moved" \
  || bad "the rest of the daemons file changed: $(cat "$B/etc/frr/daemons")"
[ "$(stat -c %a "$B/etc/frr/daemons")" = 640 ] \
  && ok "and the file keeps its mode" \
  || bad "the daemons file's mode changed to $(stat -c %a "$B/etc/frr/daemons")"
agentx_run "$B"
[ "$(grep -c "^bfdd=" "$B/etc/frr/daemons")" = 1 ] \
  && ok "a second start leaves one bfdd line" \
  || bad "bfdd appears $(grep -c "^bfdd=" "$B/etc/frr/daemons") times"
printf 'bgpd=yes\n' > "$B/etc/frr/daemons"
agentx_run "$B"
grep -qx "bfdd=yes" "$B/etc/frr/daemons" \
  && ok "a daemons file with no bfdd line gets one" \
  || bad "no bfdd line was added: $(cat "$B/etc/frr/daemons")"
ls "$B/etc/frr" | grep -q "\.bfdd" \
  && bad "a scratch file was left in /etc/frr" \
  || ok "and no scratch file is left behind"
rm -rf "$B"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
