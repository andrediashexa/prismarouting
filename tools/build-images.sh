#!/bin/sh
# Builds the two runtime images and, given `save`, writes the file a medium
# would carry.
#
#   sh tools/build-images.sh          -> prisma-vpp:<version>, prisma-frr:<version>
#   sh tools/build-images.sh save     -> prisma-routing-images.tar.gz
#   sh tools/build-images.sh check    -> starts them and asks what they hold
#   sh tools/build-images.sh compose-check -> starts the stack and stops it
#
# WHAT THIS IS FOR, stated so it is not mistaken for the appliance changing
# shape: #396 phase 1 asks for the images to exist and to be shippable. The
# box still runs VPP and FRR as systemd units, and nothing here is started by
# anything. Phase 2 is what gives them a job.
#
# The sizes are printed because the size IS the phase-1 result. Measured on
# 0.111.2: 211 MB for the dataplane image, 216 MB for the control plane, and
# 118 MB for the two of them saved and gzipped - against a 325 MB medium that
# already installs both sets of packages into its own root. Shipping them
# inside it is a 36% bigger medium carrying a second copy of what it has.
set -e

REPO=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(cat "$REPO/VERSION")
TARGET=${1:-build}

# The same re-exec tools/build.sh does: on this VM the docker group only
# takes effect in a new login.
if ! docker ps >/dev/null 2>&1; then
  exec sg docker -c "'$0' '$TARGET'"
fi

# The pins come from appliance/build-iso.sh and from nowhere else, so the
# images and the medium cannot drift apart. build-upgrade.sh reads the same
# two lines for the same reason.
pin() {
  grep -m1 "^$1=" "$REPO/appliance/build-iso.sh" \
    | sed 's/.*:-//; s/}.*//'
}
VPP_VERSION=$(pin VPP_VERSION)
FRR_VERSION=$(pin FRR_VERSION)
if [ -z "$VPP_VERSION" ] || [ -z "$FRR_VERSION" ]; then
  echo "error: could not read the VPP and FRR pins out of" >&2
  echo "       appliance/build-iso.sh: got vpp '$VPP_VERSION'," >&2
  echo "       frr '$FRR_VERSION'. The images would then be built" >&2
  echo "       against whatever the repositories hold today, which is" >&2
  echo "       the drift the pins exist to stop." >&2
  exit 1
fi

# THE PLUGIN THIS REPOSITORY COMPILED, not one the image builds for itself.
# A plugin built inside the dataplane image would be a second compile against
# a second apt resolution, and the whole reason the version is pinned is that
# a plugin and a VPP that disagree do not load.
PLUGIN_SO=$REPO/.build-vpp/lib/vpp_plugins/bpa_plugin.so
if [ ! -f "$PLUGIN_SO" ]; then
  echo "error: $PLUGIN_SO is not there, so the dataplane image would" >&2
  echo "       carry VPP and no CGNAT at all - which starts and forwards" >&2
  echo "       nothing this product exists for." >&2
  echo "       Build it:  sh tools/build.sh plugin" >&2
  exit 1
fi

CTX=$(mktemp -d)
trap 'rm -rf "$CTX"' EXIT
mkdir -p "$CTX/vpp_plugins" "$CTX/appliance/systemd"
cp "$PLUGIN_SO" "$CTX/vpp_plugins/"
# The entrypoint and the drop-in it reads, at the paths the Dockerfile names.
# A build context of the whole repository would send 200 MB of history to the
# daemon and would let a Dockerfile copy in anything; this one holds the four
# files the images are made of.
cp "$REPO/appliance/vpp-entrypoint" "$CTX/appliance/"
cp "$REPO/appliance/systemd/vpp-stop-drain.conf" "$CTX/appliance/systemd/"

echo "==> dataplane image, VPP $VPP_VERSION with this tree's plugin"
docker build -q -f "$REPO/build/Dockerfile.vpp" \
  --build-arg "VPP_VERSION=$VPP_VERSION" \
  -t "prisma-vpp:$VERSION" "$CTX" > /dev/null

echo "==> control-plane image, FRR $FRR_VERSION"
docker build -q -f "$REPO/build/Dockerfile.frr" \
  --build-arg "FRR_VERSION=$FRR_VERSION" \
  -t "prisma-frr:$VERSION" "$CTX" > /dev/null

# BYTES, and the megabyte is left to the one place that prints it. A size()
# that formatted its own answer would give docs/appliance.md's measured table
# a second opinion about what a megabyte is.
size() {
  docker image inspect "$1" --format '{{.Size}}'
}
printf '    prisma-vpp:%s  %s MB\n' "$VERSION" "$(( $(size "prisma-vpp:$VERSION") / 1048576 ))"
printf '    prisma-frr:%s  %s MB\n' "$VERSION" "$(( $(size "prisma-frr:$VERSION") / 1048576 ))"

if [ "$TARGET" = check ]; then
  # WHAT THE IMAGE HOLDS, asked of the image rather than of the Dockerfile.
  # test/test_image_pins.py reads the file and can say what was written; only
  # a started container can say what apt resolved, what the plugin loads
  # against and whether a compiler came along for the ride.
  fails=0
  bad() { echo "  [FAIL] $*"; fails=$((fails + 1)); }
  ok()  { echo "  [ ok ] $*"; }

  echo "==> the dataplane image, started"
  # --entrypoint sh, because the image HAS one now: without it the `sh -c`
  # below becomes an argument to the wrapper, which ignores it and starts VPP
  # for ever. Measured the slow way - a container sat Up for twenty minutes
  # while this check waited for output it was never going to get.
  out=$(docker run --rm --privileged --entrypoint sh "prisma-vpp:$VERSION" -c '
    mkdir -p /run/vpp
    cat > /tmp/check.conf <<CONF
unix { nodaemon cli-listen /run/vpp/cli.sock }
api-segment { gid 0 }
plugins { plugin default { disable } plugin bpa_plugin.so { enable } }
CONF
    vpp -c /tmp/check.conf >/dev/null 2>&1 &
    sleep 6
    vppctl show plugins 2>/dev/null | grep -i bpa_plugin
    vppctl show version 2>/dev/null | head -1' 2>/dev/null)

  # THE PLUGIN LOADS, which is the whole point of the image. An image with
  # VPP and no CGNAT starts, listens, forwards, and is not this product.
  if echo "$out" | grep -q "bpa_plugin.so"; then
    ok "the bpa plugin is loaded"
  else
    bad "the image started VPP and the bpa plugin is not among its plugins"
  fi

  # AND IT IS THIS TREE'S PLUGIN. The stamp is the version and the commit,
  # and an image built around a plugin from some other build is an image
  # whose CGNAT is not the one in this checkout.
  # ONE WORD PER HOLE. The stamp is "<version> (<commit>)" and printing it
  # whole puts three words inside one slot of the row - which is how a page
  # quoting this screen keeps a word the tool stopped printing, and what
  # test/test_wiki_screens.py counts and refuses.
  # WITHOUT THE -dirty SUFFIX, and this is not a loosening. The stamp is
  # `<version> (<commit>)`, and version-stamp.sh appends `-dirty` when the
  # working tree has uncommitted changes - which is every moment somebody is
  # working. Requiring it to match would make this check red on the developer
  # who just ran it and green only on a clean checkout, so the tree's state
  # is SAID on its own row instead of failing a row that is about the image.
  stamp=$(sh "$REPO/tools/version-stamp.sh" "$REPO")
  sver=${stamp%% *}
  scommit=$(echo "$stamp" | sed -n 's/.*(\(.*\))/\1/p')
  clean=${scommit%-dirty}
  if echo "$out" | grep -q "$sver ($clean"; then
    ok "and it is this tree's plugin: $sver, commit $clean"
  else
    got=$(echo "$out" | sed -n 's/.*bpa_plugin.so  *\([^ ]*\) *(\([^)]*\)).*/\1 \2/p' | head -1)
    bad "the plugin in the image is stamped '$got' and this tree is '$sver $clean'"
  fi
  if [ "$scommit" != "$clean" ]; then
    ok "the tree has uncommitted changes, so only the commit is compared"
  fi

  if echo "$out" | grep -q "vpp v$VPP_VERSION"; then
    ok "VPP is the pinned $VPP_VERSION"
  else
    bad "the image resolved a VPP that is not the pin: $(echo "$out" | grep -m1 '^vpp v')"
  fi

  # NO COMPILER, and this is not tidiness. build/Dockerfile.dev exists to
  # build things and carries gcc, cmake, ninja and vpp-dev; an appliance
  # image that carries them ships a build environment to every customer, and
  # the first person to notice will be the one asking why the dataplane can
  # compile.
  for tool in gcc cc cmake ninja; do
    if docker run --rm --entrypoint sh "prisma-vpp:$VERSION" -c "command -v $tool" >/dev/null 2>&1; then
      bad "the dataplane image carries $tool: it is a runtime image and this is build/Dockerfile.dev's job"
    fi
  done
  [ "$fails" -eq 0 ] && ok "and no compiler came with it"

  # PID 1 IS THE WRAPPER, asked of the image rather than of the Dockerfile.
  # test/test_vpp_entrypoint.sh proves the wrapper waits for the drain; this
  # proves the image is actually running it, which is the half a file cannot
  # answer.
  ep=$(docker image inspect "prisma-vpp:$VERSION" --format '{{json .Config.Entrypoint}}')
  case "$ep" in
    *vpp-entrypoint*) ok "PID 1 in the dataplane image is the wrapper" ;;
    *) bad "the image's entrypoint is $ep, so a stop would signal VPP with no drain in front of it" ;;
  esac
  # AND THE DROP-IN IS IN THERE, because the wrapper reads the drain out of
  # it and one that finds none says so and drains nothing.
  if docker run --rm --entrypoint sh "prisma-vpp:$VERSION" -c \
       'grep -q "^ExecStop" /etc/systemd/system/vpp.service.d/vpp-stop-drain.conf' \
       >/dev/null 2>&1; then
    ok "and the drain it reads is in the image"
  else
    bad "the image carries no vpp-stop-drain.conf, so the wrapper would start VPP and drain nothing on the way down"
  fi

  echo "==> the control-plane image, asked its version"
  # /usr/lib/frr, not the PATH: FRR installs its daemons there and puts none
  # of them on the PATH, so `bgpd --version` is "executable file not found"
  # and reads like an image without FRR in it.
  frr=$(docker run --rm --entrypoint /usr/lib/frr/bgpd "prisma-frr:$VERSION" --version 2>&1 | head -1)
  # The package pin carries a Debian revision (10.7.1-0~deb12u1) and the
  # daemon prints the upstream version (10.7.1), so the comparison is against
  # the part they share rather than against the whole string.
  if echo "$frr" | grep -q "${FRR_VERSION%%-*}"; then
    ok "FRR is the pinned ${FRR_VERSION%%-*}"
  else
    bad "the image resolved an FRR that is not the pin: $frr"
  fi

  if [ "$fails" -eq 0 ]; then
    echo "IMAGES PASSED"
  else
    echo "IMAGES FAILED: $fails check(s)"
    exit 1
  fi
  exit 0
fi

if [ "$TARGET" = compose-check ]; then
  # THE STACK, STARTED AND STOPPED FOR REAL, and the one question it is here
  # to answer: does a `docker compose stop` drain before the dataplane dies?
  # Everything else about decision 4 is proven with stubs
  # (test/test_vpp_entrypoint.sh) or read off a file - this is the only place
  # the actual supervisor sends the actual signal to the actual image (#396).
  #
  # The shipped appliance/compose.yaml is used as it ships, with ONE override
  # layered on top: its volumes are the box's paths - /etc/vpp, /dev/vfio,
  # /var/log/prisma-routing - which this machine has not got. The override
  # replaces those and nothing else, so `stop_grace_period`, `network_mode`,
  # the entrypoint and the image tags are the shipped ones under test.
  fails=0
  bad() { echo "  [FAIL] $*"; fails=$((fails + 1)); }
  ok()  { echo "  [ ok ] $*"; }

  CDIR=$(mktemp -d)
  trap 'docker compose -p prismacheck -f "$REPO/appliance/compose.yaml" -f "$CDIR/override.yaml" -f "$CDIR/devices.yaml" down -t 5 >/dev/null 2>&1; rm -rf "$CDIR" "$CTX"' EXIT
  mkdir -p "$CDIR/vpp" "$CDIR/run" "$CDIR/log" "$CDIR/frr"
  # A dataplane with no card and no DPDK: this check is about the STOP, and a
  # VPP that spends its startup failing to bind a NIC this machine does not
  # have would fail it for a reason that is not the question.
  # DPDK ON, AND WORKERS PINNED, because that is what an appliance runs and
  # because the privileges below are only interesting against it: a VPP with
  # the EAL disabled starts under no privileges at all and would report this
  # check green on a container that could not run the product (#396).
  #
  # `no-pci`: this machine has no card DPDK can bind, which is the gap #28
  # records. The EAL still initialises, still wants its hugepages and still
  # calls set_mempolicy, which is the part being measured here.
  #
  # The plugins stanza is the one appliance/prisma-routing-tune writes on a
  # tuned box - default, bpa, linux_cp, linux_nl - and not a shorter list
  # chosen here. With `plugin default { disable }` even `create tap` is not a
  # command this VPP has, so a check written against that list would be
  # measuring a dataplane the product never runs.
  cat > "$CDIR/vpp/startup.conf" <<CONF
unix { nodaemon cli-listen /run/vpp/cli.sock }
api-segment { gid 0 }
cpu { main-core 1 corelist-workers 2-3 }
dpdk { no-pci }
plugins {
  plugin default { enable }
  plugin bpa_plugin.so { enable }
  plugin linux_cp_plugin.so { enable }
  plugin linux_nl_plugin.so { enable }
}
CONF
  cat > "$CDIR/override.yaml" <<OVR
services:
  vpp:
    volumes:
      - $CDIR/vpp:/etc/vpp:ro
      - $CDIR/run:/run/vpp
      - $CDIR/log:/var/log/prisma-routing
  frr:
    volumes:
      - $CDIR/frr:/etc/frr
      - $CDIR/frr:/var/run/frr
OVR

  # THE FRAGMENT THE UNIT GENERATES, generated the way the unit generates it
  # (#396). prisma-routing-containers.service runs
  # `bind-dataplane-nic --compose-devices` into a file and hands that file to
  # `docker-compose -f`, so the file has to be one compose accepts - and the
  # case that is easy to get wrong is the box that names no card, where the
  # mode used to print nothing at all and compose refuses an empty file with
  # "Top level object needs to be a mapping".
  #
  # THIS MACHINE IS THAT BOX. It has no card DPDK can bind (#28), and the
  # startup.conf above says `no-pci` - so the fragment generated from it is
  # the empty-list one, and starting the stack with it layered on is the
  # direct measurement that a no-pci box can run the containers at all.
  # PRISMA_STARTUP_CONF and not PRISMA_ROOT: the file the container reads is
  # $CDIR/vpp/startup.conf, mounted at /etc/vpp, and pointing the mode at the
  # same copy is what makes the fragment describe THIS check's dataplane
  # rather than a second one written to suit it.
  PRISMA_STARTUP_CONF=$CDIR/vpp/startup.conf \
    sh "$REPO/appliance/bind-dataplane-nic" --compose-devices \
    > "$CDIR/devices.yaml" 2>/dev/null || true
  if grep -q "^services:" "$CDIR/devices.yaml"; then
    ok "the generated device fragment is a mapping compose can read"
  else
    bad "bind-dataplane-nic --compose-devices wrote something compose refuses: $(head -c 120 "$CDIR/devices.yaml")"
  fi

  echo "==> the stack, started by compose"
  export PRISMA_VERSION=$VERSION
  if ! docker compose -p prismacheck -f "$REPO/appliance/compose.yaml" \
       -f "$CDIR/override.yaml" -f "$CDIR/devices.yaml" up -d >/dev/null 2>&1; then
    bad "compose could not start the stack"
  else
    _n=0
    while [ "$_n" -lt 30 ] && ! docker compose -p prismacheck \
        -f "$REPO/appliance/compose.yaml" -f "$CDIR/override.yaml" -f "$CDIR/devices.yaml" \
        exec -T vpp vppctl show version >/dev/null 2>&1; do
      sleep 1; _n=$((_n + 1))
    done
    if docker compose -p prismacheck -f "$REPO/appliance/compose.yaml" \
       -f "$CDIR/override.yaml" -f "$CDIR/devices.yaml" exec -T vpp vppctl show plugins 2>/dev/null \
       | grep -q bpa_plugin; then
      ok "the dataplane came up under compose, with the plugin, in ${_n}s"
    else
      bad "the dataplane did not answer under compose after ${_n}s"
    fi

    # WITHOUT `privileged: true`, and the EAL is what says so: it is the
    # first thing that fails when SYS_NICE is missing, with
    # "set_mempolicy: Operation not permitted" and then
    # "rte_eal_init failed: Cannot allocate memory". A dpdk plugin that
    # loaded is an EAL that came up under the capability set in the shipped
    # compose file.
    # grep -v on the comments first: the file EXPLAINS that it is not
    # privileged, and the sentence saying so contains the words. Measured -
    # this check went red on its own documentation.
    if grep -v '^[[:space:]]*#' "$REPO/appliance/compose.yaml" \
       | grep -q "privileged: *true"; then
      bad "appliance/compose.yaml is back to privileged: true, so this check no longer measures a narrowed dataplane"
    elif docker compose -p prismacheck -f "$REPO/appliance/compose.yaml" \
         -f "$CDIR/override.yaml" -f "$CDIR/devices.yaml" exec -T vpp vppctl show plugins 2>/dev/null \
         | grep -q dpdk_plugin; then
      ok "and DPDK initialised with no privileged flag, on cap_add alone"
    else
      bad "the DPDK plugin is not loaded, so the EAL did not come up: the capability set in appliance/compose.yaml is short of what the dataplane needs"
    fi

    # A TAP, WHICH IS WHAT NET_ADMIN IS FOR. linux_cp pairs every dataplane
    # card into Linux through one, and on a one-card box the address the
    # operator reaches moves onto that pair. Measured with the capability
    # removed: `create tap: ioctl(TUNSETIFF): Operation not permitted`, and
    # with it, `tap0`.
    tapout=$(docker compose -p prismacheck -f "$REPO/appliance/compose.yaml" \
             -f "$CDIR/override.yaml" -f "$CDIR/devices.yaml" exec -T vpp vppctl create tap 2>&1 | head -1)
    case "$tapout" in
      tap[0-9]*) ok "and linux_cp's door is open: created $tapout" ;;
      *) bad "the dataplane could not create a TAP ('$tapout'), so linux_cp would pair no card into Linux and the control plane would route over nothing" ;;
    esac

    # THE BINARY API, ON THIS HOST'S /dev/shm, WHICH IS WHERE THE VTY LOOKS.
    # prismash is a host tool - one of the costs written on #396 - and it
    # maps /dev/shm/global_vm and /dev/shm/vpe-api. Docker gives a container
    # its own 64 MB /dev/shm by default, and the segments then exist only
    # inside it: the box would run, forward, and have a VTY that cannot
    # commit anything. `ipc: host` in the shipped compose file is what puts
    # them here, and this reads THIS MACHINE's /dev/shm rather than the
    # container's, because the container's is not the question.
    if [ -e /dev/shm/global_vm ] && [ -e /dev/shm/vpe-api ]; then
      ok "the API segments are on the host's /dev/shm, where the VTY maps them"
    else
      bad "the dataplane's API segments are not on this host's /dev/shm, so prismash - which runs on the box and not in a container - would have no binary API and every commit would fail: appliance/compose.yaml needs ipc: host"
    fi

    # AND THE KERNEL HAS THE OOM SCORE, read off /proc and not off the
    # compose file. `oom_score_adj` is a key compose 1.29.2 parses under the
    # 3.9 schema; whether it reaches the kernel is a different question, and
    # a box whose dataplane is choosable while a file says it is not is
    # exactly the shape #328 was about. -1000 takes VPP out of selection:
    # the 2 GB box that killed it while it held 19 MB resident is in
    # vpp-oom-protect.conf with the rest of the measurement.
    vpppid=$(docker compose -p prismacheck -f "$REPO/appliance/compose.yaml" \
             -f "$CDIR/override.yaml" -f "$CDIR/devices.yaml" \
             ps -q vpp 2>/dev/null \
             | head -1 | xargs -r docker inspect -f '{{.State.Pid}}' 2>/dev/null)
    oomadj=$(cat "/proc/${vpppid:-0}/oom_score_adj" 2>/dev/null)
    case "$oomadj" in
      -1000) ok "and the kernel has the dataplane out of the OOM killer's choice" ;;
      "") bad "could not read /proc/${vpppid:-?}/oom_score_adj, so nothing here says whether the containerised dataplane is choosable by the OOM killer" ;;
      *) bad "the containerised dataplane's oom_score_adj is $oomadj and not -1000, so the kernel may kill the dataplane for somebody else's allocation - which is how it came to be killed on a 2 GB box while holding 19 MB" ;;
    esac

    # AND THE WORKERS ARE WHERE startup.conf PUT THEM. The tuner computes
    # `corelist-workers` from the NIC's NUMA node and the physical cores, and
    # a container that confines the process to some other set would leave
    # those threads wherever the cgroup allowed - quietly, at a fraction of
    # the forwarding, which is the failure this whole product's tuning
    # exists to prevent.
    thr=$(docker compose -p prismacheck -f "$REPO/appliance/compose.yaml" \
          -f "$CDIR/override.yaml" -f "$CDIR/devices.yaml" exec -T vpp vppctl show threads 2>/dev/null)
    cores=$(printf '%s' "$thr" | awk '$2 ~ /^vpp_wk_/ {print $(NF-3)}' | sort -n | tr '\n' ' ')
    case "$cores" in
      "2 3 ") ok "and both workers are on the cores startup.conf named: 2 3" ;;
      "") bad "the dataplane reported no worker threads at all: $(printf '%s' "$thr" | head -2)" ;;
      *) bad "the workers are on core(s) '$cores' and startup.conf asked for 2 and 3: the container placed them somewhere the tuning did not" ;;
    esac
  fi

  # LOGGING ON, so the drain takes its real branch. With it off the command
  # answers "logging is not enabled; nothing to drain" and returns before the
  # suspend - which proves the wrapper RAN it, and not that the path a box in
  # service takes works. There are no blocks to release on a dataplane with
  # no cards, so the honest claim this buys is the branch, not the record;
  # lab/ipfix-stop.sh is where a real allocation is drained and read off a
  # collector.
  # A COLLECTOR FIRST, and the refusal if there is none is the product's own
  # (#209): VPP's exporter has no destination unless something sets one, and
  # `bpa logging enable` says so rather than enabling an export to nowhere.
  # Loopback is the destination here because nothing needs to arrive - this
  # check reads the drain's answer, not a collector's.
  cexec() {
    docker compose -p prismacheck -f "$REPO/appliance/compose.yaml" \
      -f "$CDIR/override.yaml" -f "$CDIR/devices.yaml" exec -T vpp vppctl "$@" 2>&1
  }
  cexec set ipfix exporter collector 127.0.0.1 port 4739 src 127.0.0.1 \
    >/dev/null 2>&1
  logmsg=$(cexec bpa logging enable)
  case "$logmsg" in
    "") ok "logging enabled, so the drain below takes a box's own branch" ;;
    *)  bad "the dataplane would not enable logging ('$logmsg'), so the \
drain below cannot take the branch a box in service takes" ;;
  esac

  echo "==> and stopped, which is the question"
  docker compose -p prismacheck -f "$REPO/appliance/compose.yaml" \
    -f "$CDIR/override.yaml" -f "$CDIR/devices.yaml" stop -t 30 >/dev/null 2>&1
  logs=$(docker compose -p prismacheck -f "$REPO/appliance/compose.yaml" \
         -f "$CDIR/override.yaml" -f "$CDIR/devices.yaml" logs vpp 2>/dev/null)

  # THE WRAPPER'S OWN LINE. Whatever the drain answered - blocks released, or
  # "logging is not enabled" on a box exporting nothing - it can only have
  # been answered by a dataplane that was still running when the stop began.
  # A container whose PID 1 is VPP prints nothing here at all.
  said=$(printf '%s' "$logs" | sed -n 's/.*vpp-entrypoint: //p' | head -1)
  # ONE WORD IN THE HOLE. The drain's own sentence is five words long, and a
  # verdict row whose slot swallows five words is a row a page can keep after
  # the product has stopped printing it - which test/test_wiki_screens.py
  # counts and refuses. The count is what this check is about anyway.
  nblocks=$(printf '%s' "$said" | sed -n 's/^released \([0-9][0-9]*\) .*/\1/p')
  case "$said" in
    "released "*) ok "the drain ran before the signal, releasing $nblocks block(s)" ;;
    "") bad "nothing in the container's log says the drain ran, so compose stopped the dataplane with no drain in front of it" ;;
    *)  bad "the drain answered '$said' rather than releasing: it did not take the branch a box in service takes" ;;
  esac

  # AND IT STOPPED WITHIN THE GRACE PERIOD rather than being killed at the
  # end of it: a box that is SIGKILLed after 30 s has not drained, it has
  # been cut off, and the exit code is how that is told apart.
  code=$(docker inspect -f '{{.State.ExitCode}}' \
         "$(docker compose -p prismacheck -f "$REPO/appliance/compose.yaml" \
            -f "$CDIR/override.yaml" -f "$CDIR/devices.yaml" ps -aq vpp 2>/dev/null)" 2>/dev/null)
  case "$code" in
    137) bad "the dataplane was SIGKILLed at the end of the grace period: the drain did not finish inside stop_grace_period" ;;
    "")  bad "the dataplane container's exit code could not be read" ;;
    *)   ok "and it exited on its own, code $code, inside the grace period" ;;
  esac

  if [ "$fails" -eq 0 ]; then
    echo "COMPOSE PASSED"
  else
    echo "COMPOSE FAILED: $fails check(s)"
    exit 1
  fi
  exit 0
fi

if [ "$TARGET" = save ]; then
  OUT=${OUT:-$REPO/prisma-routing-images.tar.gz}
  echo "==> saving both, which is what a medium would carry"
  # ONE save for the two images: they share the debian:12 base, and saving
  # them separately writes it twice. Measured: 84 MB for the dataplane alone
  # and 118 MB for both, not 84 + 90.
  docker save "prisma-vpp:$VERSION" "prisma-frr:$VERSION" | gzip -1 > "$OUT"
  printf '    %s  %s MB\n' "$OUT" "$(( $(wc -c < "$OUT") / 1048576 ))"
fi
