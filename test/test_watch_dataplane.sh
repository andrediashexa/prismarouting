#!/bin/sh
# appliance/watch-the-dataplane, driven against a stubbed Docker and systemd.
#
# WHAT IT IS FOR. The card is bound to a DPDK driver before the dataplane
# starts, so between the bind and a working dataplane it belongs to neither
# Linux nor VPP. A box left in that window has no interface at all (#379).
# vpp.service reaches `failed` after its restart limit and systemd raises
# OnFailure=; prisma-routing-containers.service is a oneshot that exited
# successfully long before, so nothing raises anything and Docker tells
# nobody that its own policy gave up. This is what looks, and what is driven
# here is the decision it makes - not the bind it undoes, which is
# appliance/give-the-card-back's and test/test_bind_nic.sh's.
#
# EVERY BRANCH HAS A BOX. The three that must do nothing are the dangerous
# ones: handing the card back under a dataplane that is forwarding is the
# outage this script would cause rather than prevent.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }
REPO=$(cd "$(dirname "$0")/.." && pwd)
BIN=$REPO/appliance/watch-the-dataplane

# A box: a directory with stub programs whose answers this test chooses, and
# a file each one touches so the assertions can read what was called.
mkbox() { # <systemctl-answer 0|1> <docker-state>
  B=$(mktemp -d)
  mkdir -p "$B/bin" "$B/run/prisma-routing"
  cat > "$B/bin/systemctl" <<SC
#!/bin/sh
echo "\$@" >> "$B/systemctl.calls"
exit $1
SC
  cat > "$B/bin/docker" <<DC
#!/bin/sh
echo "\$@" >> "$B/docker.calls"
printf '%s' "$2"
DC
  cat > "$B/bin/giveback" <<GB
#!/bin/sh
echo called >> "$B/giveback.calls"
GB
  cat > "$B/bin/ifup" <<IU
#!/bin/sh
echo "\$@" >> "$B/ifup.calls"
IU
  chmod +x "$B/bin/"*
  echo "$B"
}
run() { # <box>
  PRISMA_ROOT=$1 PRISMA_SYSTEMCTL=$1/bin/systemctl PRISMA_DOCKER=$1/bin/docker \
  PRISMA_GIVEBACK=$1/bin/giveback PRISMA_IFUP=$1/bin/ifup \
  PRISMA_CARD_STAMP=$1/run/prisma-routing/card-given-back \
  sh "$BIN" 2>&1
}
gave_back() { [ -e "$1/giveback.calls" ]; }

echo "=== a box still booting vpp.service is not this script's business ==="
# THE MOST IMPORTANT BOX HERE, and it is every box until the default moves:
# the containers unit is inactive, its dataplane is not this box's dataplane,
# and handing the card back would take it off a VPP that is forwarding.
B=$(mkbox 1 exited)
OUT=$(run "$B")
gave_back "$B" \
  && bad "the card was handed back on a box whose container stack is not running: that is an outage this script caused" \
  || ok "it does nothing when prisma-routing-containers.service is inactive"
[ -z "$OUT" ] && ok "and says nothing, because there is nothing to report" \
  || bad "it printed something on a box it has no business on: $OUT"
rm -rf "$B"

echo
echo "=== a dataplane that is running ==="
B=$(mkbox 0 running)
OUT=$(run "$B")
gave_back "$B" \
  && bad "the card was handed back under a RUNNING dataplane" \
  || ok "a running container is left alone"
rm -rf "$B"

echo
echo "=== a dataplane Docker is still retrying ==="
# `restarting` is the policy still trying, and that is not giving up. Firing
# here would undo the bind underneath a dataplane that is about to come up.
B=$(mkbox 0 restarting)
OUT=$(run "$B")
gave_back "$B" \
  && bad "the card was handed back while Docker was still restarting the dataplane" \
  || ok "a restarting container is still being retried, so it is left alone"
rm -rf "$B"

echo
echo "=== a dataplane that gave up ==="
B=$(mkbox 0 exited)
OUT=$(run "$B")
gave_back "$B" \
  && ok "the card is handed back when Docker has stopped restarting it" \
  || bad "a box with an exited dataplane kept its card bound to DPDK, which leaves it with no interface at all: $OUT"
printf '%s\n' "$OUT" | grep -q "exited" \
  && ok "and the state it read is in what it says" \
  || bad "it did not name the container's state: $OUT"
[ -e "$B/ifup.calls" ] \
  && ok "and the interface is brought back up after the bind is undone" \
  || bad "nothing ran ifup, so the card is back on its kernel driver with no address on it"
[ -e "$B/run/prisma-routing/card-given-back" ] \
  && ok "and a stamp is left, so it fires once and not every 30 seconds" \
  || bad "no stamp was written: the timer would run ifup at an operator's box twice a minute"
# AND THE SECOND RUN DOES NOTHING. The stamp is the whole of that.
rm -f "$B/giveback.calls"
run "$B" >/dev/null 2>&1
gave_back "$B" \
  && bad "it handed the card back a second time, with the stamp already there" \
  || ok "and the run after it does nothing at all"
rm -rf "$B"

echo
echo "=== the stack is up and there is no container at all ==="
# `up -d` having failed rather than a dataplane that died. The card was bound
# by the tuner before either happened, so the box is stranded the same way.
B=$(mkbox 0 "")
OUT=$(run "$B")
gave_back "$B" \
  && ok "a missing container is treated as a dataplane that is not coming" \
  || bad "the stack was up with no dataplane container and the card stayed bound: $OUT"
printf '%s\n' "$OUT" | grep -q "no prisma-routing_vpp_1 container" \
  && ok "and it says the container is not there, rather than naming a state" \
  || bad "it did not distinguish a missing container: $OUT"
rm -rf "$B"

echo
echo "=== the name it asks Docker about is the name compose gives ==="
# Measured on docker-compose 1.29.2: a compose file at
# /etc/prisma-routing/compose.yaml, run with that directory as the working
# directory, makes the project `prisma-routing` and the container
# `prisma-routing_vpp_1` - dashes and all, which v1 does NOT strip.
grep -q "prisma-routing_vpp_1" "$BIN" \
  && ok "the default container name is the one compose produces" \
  || bad "watch-the-dataplane does not name prisma-routing_vpp_1"
grep -q "WorkingDirectory=/etc/prisma-routing" \
  "$REPO/appliance/systemd/prisma-routing-containers.service" \
  && ok "and the unit runs compose from the directory that makes it" \
  || bad "the containers unit no longer runs from /etc/prisma-routing, so the project name - and the container name above - is something else"

echo
echo "=== and the compose file lets the dataplane give up ==="
# `unless-stopped` never gives up, so the box would be stranded for ever.
grep -v '^[[:space:]]*#' "$REPO/appliance/compose.yaml" \
  | grep -q 'restart: "on-failure:5"' \
  && ok "the dataplane's restart policy stops after five attempts" \
  || bad "appliance/compose.yaml does not say on-failure:5 for the dataplane, so Docker would retry for ever and this watcher would never fire"

echo "=== and it keeps what the dataplane said before it died ==="
# THE DEFECT THIS ANSWERS, AND IT IS A FIELD ONE. A box was installed, its
# dataplane did not come up, and the report that reached this repository was
# `show interface` printing nothing and a kernel line at 120 seconds - which
# was this script giving the card back. The ONE thing that would have named
# the cause, `docker logs prisma-routing_vpp_1`, was gone: the operator had
# rebooted, which is what anybody does to a box with no interfaces (#428).
#
# So the box has to take the copy itself, at the only moment it still can.
mkwhybox() { # <docker-state> - a box whose docker stub answers `logs` properly
  B=$(mktemp -d)
  mkdir -p "$B/bin" "$B/run/prisma-routing" "$B/var/log/prisma-routing"
  printf '#!/bin/sh\nexit 0\n' > "$B/bin/systemctl"
  cat > "$B/bin/docker" <<DC
#!/bin/sh
case "\$1" in
  inspect)
    case "\$3" in
      *ExitCode*) printf 'exit=139 oom=false error=EAL: No free hugepages' ;;
      *)          printf '%s' "$1" ;;
    esac ;;
  logs) printf 'vpp[1]: EAL: No free 2048 kB hugepages reported\n' ;;
esac
DC
  printf '#!/bin/sh\n' > "$B/bin/giveback"
  printf '#!/bin/sh\n' > "$B/bin/ifup"
  chmod +x "$B/bin/"*
  echo "$B"
}
runwhy() { # <box>
  PRISMA_ROOT=$1 PRISMA_SYSTEMCTL=$1/bin/systemctl PRISMA_DOCKER=$1/bin/docker \
  PRISMA_GIVEBACK=$1/bin/giveback PRISMA_IFUP=$1/bin/ifup \
  PRISMA_CARD_STAMP=$1/run/prisma-routing/card-given-back \
  PRISMA_DATAPLANE_REPORT=$1/var/log/prisma-routing/why-the-dataplane-stopped \
  sh "$BIN" 2>&1
}

B=$(mkwhybox exited)
OUT=$(runwhy "$B")
R=$B/var/log/prisma-routing/why-the-dataplane-stopped
if [ -f "$R" ]; then
  ok "a box that stranded itself wrote down why"
  grep -q "state:     exited" "$R" \
    && ok "and the state Docker left the container in" \
    || bad "the report does not carry the container's state: $(cat "$R")"
  grep -q "exit=139" "$R" \
    && ok "and the exit code, which is the difference between a crash and a refusal" \
    || bad "the report does not carry the container's exit code: $(cat "$R")"
  grep -q "EAL: No free 2048 kB hugepages reported" "$R" \
    && ok "and the last thing the dataplane printed, which is the cause" \
    || bad "the report does not carry the dataplane's log: $(cat "$R")"
  grep -q "container: prisma-routing_vpp_1" "$R" \
    && ok "and names the container, so the next person can ask Docker themselves" \
    || bad "the report does not name the container: $(cat "$R")"
else
  bad "the card was given back and nothing was written down: the next field report has no cause attached, which is exactly #428"
fi
case "$OUT" in
  *"why it stopped is kept in $R"*)
    ok "and the console says where it is, at the moment it is written" ;;
  *) bad "it did not say where the reason is: $OUT" ;;
esac
rm -rf "$B"

# NOT IN /run, and this is the whole point: /run is cleared on reboot and a
# reboot is the first thing an operator does to a box with no interfaces.
grep -q 'PRISMA_DATAPLANE_REPORT:-$ROOT/var/log/prisma-routing/' "$BIN" \
  && ok "and it is kept somewhere a reboot does not delete" \
  || bad "the report defaults somewhere other than /var/log/prisma-routing; if it is under /run the reboot an operator does next deletes the only copy"

# AND A BOX THAT IS FINE WRITES NOTHING. A report left by a run that decided
# to do nothing is a report that outlives the failure it describes.
B=$(mkwhybox running)
runwhy "$B" >/dev/null
[ -f "$B/var/log/prisma-routing/why-the-dataplane-stopped" ] \
  && bad "it wrote a failure report about a dataplane that is running" \
  || ok "a running dataplane leaves no report behind"
rm -rf "$B"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
