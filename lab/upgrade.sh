#!/bin/sh
# Install a RELEASED version, configure the box, install this tree over it.
#
#   sh tools/lab.sh upgrade           from v0.79.0 to the current tree
#   sh tools/lab.sh upgrade v0.80.0   from another released tag
#
# WHY THE OLD SIDE STARTS AT v0.79.0 AND NOT EARLIER, which is a fact about
# the product and not a fixture preference. v0.79.0 is the rename (#202): the
# VTY is `prismash` from there on and `hexash` before it, the configuration
# lives in /etc/prisma-routing and not /etc/hexarouting, and
# `appliance/upgrade` REFUSES a box carrying the old directory or the old
# service - "the product was renamed and NOTHING MIGRATES". So an upgrade from
# a pre-rename tag is not a path this product supports; measuring one would be
# measuring something the box is written to refuse.
#
# It was v0.68.0 until the full suite run of 0.90.0 reported this lab NOT
# MEASURED, every run: the lab drives the old box through /usr/bin/prismash,
# a v0.68.0 box has /usr/bin/hexash, and the fixture could not be configured
# at all. The lab said so rather than reporting the product broken, which is
# what `give_up` is for - but a lab that cannot measure anything is a lab
# nobody is testing with.
#
# Nothing in this repository has ever upgraded a box (#291). appliance/install.sh
# says at its own line 4 that it is the path "for updating a box in production
# without reinstalling it", lab/install-iso.sh installs one and boots it, and
# lab/in-place.sh changes a setting on one that is already running. None of
# them puts one version on top of another, and five places in the product
# assert that doing so is safe.
#
# What it costs to be wrong is at the next boot. prisma-routing.service runs
#
#   ExecStart=/usr/bin/prismash --load /etc/prisma-routing/prisma-routing.conf
#
# and `--load` refuses the file WHOLE - deliberately, and src/prismash/config.py
# argues for it at length: a file applied minus the one line this VTY cannot
# hold is a box whose running configuration silently differs from its own
# file. The consequence is the sentence at src/prismash/config.py:1107, "the box
# then comes up with no pools, no addresses and no pair at all". So one
# statement the new grammar stopped accepting does not degrade an upgraded
# box. It unconfigures it, at a boot nobody is watching, on a machine carrying
# subscribers. Until this file existed that was guarded by nobody having
# removed a statement yet.
#
# The central assertion is therefore section 6: the new VTY accepts the file
# the OLD version saved, whole. Everything around it is there so that a green
# section 6 means something - a file can be accepted and apply nothing.
#
# ---------------------------------------------------------------------------
# THE OLD SIDE COMES FROM THE TAG'S TREE, NOT FROM A RELEASED ISO
#
# Both were available and the tree wins on three counts.
#
#   It needs no download. tools/lab.sh already fights the network for apt
#   (see its own comment about apt sitting on IPv6 for 29 minutes), and a lab
#   that fetches a 1 GB image from a release page fails for reasons that have
#   nothing to do with upgrading.
#
#   It needs no boot. lab/install-iso.sh takes minutes of qemu to install and
#   boot an image, and it is excluded from tools/run-all-labs.sh for exactly
#   that reason. This lab is meant to run in the suite, every time, beside the
#   others.
#
#   It is the same artefact. appliance/install.sh IS the upgrade path under
#   test, and an ISO is that same script run by build-iso.sh into a DESTDIR.
#   Running the released tag's own install.sh is running what the release
#   shipped, and it is what an operator with a git checkout does.
#
# What the tree route costs, said out loud rather than left implicit: the old
# tree's dataplane plugin has to be COMPILED before the old install.sh has
# anything to install. That is a cmake plus ninja of a three-release-old tree
# against the container's vpp-dev, and it is the one step that can fail for a
# reason that is nobody's defect - a toolchain that moved, a machine with no
# room to compile. When it fails this lab has learned NOTHING about upgrading,
# so it is expect_setup and the verdict is NOT MEASURED, never FAILED. That is
# the #253 shape and lab/verdict.sh already holds the argument for it.
#
# Where the tag's tree comes from: `git archive` inside the container, and
# TWO different things stop that, which was measured rather than reasoned
# about. The first version of this file named only the second of them, and the
# consequence was worse than an unclear message: the lab came out NOT MEASURED
# in a clean CLONE, where everything it blamed was fine.
#
# The one that fires everywhere is git's ownership check. The container runs as
# root and /src is bind-mounted from a tree owned by the operator, so git
# refuses it outright:
#
#   fatal: detected dubious ownership in repository at '/src'
#
# That is not a property of a worktree and not a property of the tag. It is
# every run, on every machine, in a clone as much as anywhere - so without the
# override below this lab would have been NOT MEASURED in every release suite
# it ever ran in, asserting nothing while reporting nothing wrong.
#
# `git -c safe.directory=/src` and NOT `git config --global --add`. The
# container is thrown away, so the global file would cost nothing here, but
# #220 is what a habit of writing to it costs: 2325 copies of one line in a
# ~/.gitconfig this product does not own, and an earlier reading with 12710
# entries for clones that no longer existed. A one-shot override has no file to
# grow.
#
# The second only fires in a git WORKTREE: its .git is a file pointing at a
# directory outside the bind mount, so the repository genuinely is not there.
# tools/release-gate.sh carries the same scar ("Built from a git WORKTREE,
# whose .git is a pointer to a repository the build container does not have
# mounted"). Nothing can fix that from inside the container, so the lab looks
# first for a tree somebody extracted on the host:
#
#   git -C <repo> archive v0.79.0 \
#     | (mkdir -p <repo>/.build-old/v0.79.0 \
#        && tar -x -C <repo>/.build-old/v0.79.0)
#
# and falls back to that being absent as NOT MEASURED, with that command
# printed. In the clone the release suite runs from, git now answers and none
# of this is reached - which is the state this file claimed it was already in.
#
# ---------------------------------------------------------------------------
# WHAT THIS LAB DOES BY HAND THAT A REAL UPGRADE WOULD NOT
#
# Two of them, and both are findings about the product rather than fixture
# detail. They are #291's items 2 and 3, measured here rather than argued:
#
#   1. IT RESTARTS THE DATAPLANE. appliance/install.sh writes the new
#      bpa_plugin.so, runs `systemctl daemon-reload` and `systemctl enable`,
#      and stops. It does not restart vpp.service. So until somebody reboots,
#      the VTY, the tuner, the self-test and the collector are the new build
#      and the TRANSLATING DATAPLANE IS THE OLD ONE. Section 5 measures that
#      split through /proc/<vpp>/maps and prints it; section 6 then restarts
#      VPP by hand, because a lab that did not would be measuring the old
#      plugin and calling it the new one. That was never an argument for
#      restarting automatically - it drops every session on the box - it was
#      an argument for SAYING SO, which is acceptance item 3 of #291.
#
#      AND IT NOW SAYS SO. The installer used to close every run with a first
#      install's sentence, "Installed. Adjust /etc/prisma-routing/tune.env and
#      reboot so that isolcpus and hugepages take effect.", which an operator
#      who tuned the box a year ago reads as "nothing to do". It reads the
#      version stamp before it overwrites it, and on a root that already had
#      one it says which build replaced which and what is still running the
#      old one. The verdict asserts that here, on a box that really was
#      a released version an hour ago, which is the only place in this
#      repository where those two stamps are real.
#
#      The readings in this header were taken with v0.68.0 on the old side,
#      before the rename made that tag unupgradeable. What they measure - a
#      new VTY against an old dataplane - is a property of the split and not
#      of the tag, and the run re-measures it on every execution.
#
#      WHAT THE SPLIT COSTS IS NOW MEASURED, and it is worse than the issue
#      says. #291 calls the combination "not tested, not documented and not
#      stated". It is also PARTLY BROKEN, and section 5 prints which part: on
#      a box upgraded from the old side to this tree, the new VTY changing a
#      setting on an existing pool commits normally, and the new VTY CREATING
#      A POOL is refused by the old dataplane -
#
#        error: pool 'split291': Call failed: bpa_pool_add_del
#        error: commit failed; rolling back to the previous state
#
#      because the API message grew fields the running plugin does not have.
#      An operator who upgrades in the evening and turns up a new customer
#      range before the maintenance reboot gets that, with nothing on the box
#      to explain it. Printed and not asserted: an assertion either pins a
#      defect that somebody will fix - lab/crossaddr.sh is what that costs,
#      having gone red the day #226 fixed what it pinned and needed rewriting
#      against a running box before it could be a lab again - or turns this
#      lab permanently red for a defect it is not the one holding.
#
#   2. IT RECREATES THE pg INTERFACES after that restart. A real box's cards
#      come back by themselves; packet-generator interfaces are created by
#      vppctl and a fresh VPP has none. `--load` would then refuse the file
#      for naming an interface the box does not have, which would be the lab
#      measuring its own fixture. Every lab here that starts VPP creates them
#      the same way. The neighbour entries and the return route into
#      100.64.0.0/22 are plumbing of the same kind, and lab/forward.sh states
#      them for the same reason.
#
# ---------------------------------------------------------------------------
# THERE IS NO `off` MODE, AND THAT IS DELIBERATE
#
# NO-OFF-RUN: the feature here is that a released configuration survives an
# upgrade, and its opposite is a new tree that lost a statement - an edit to
# src/prismash/config.py, not an argument to a shell script.
#
# Every other lab's off run switches off the FEATURE the lab is about, and the
# feature here is "a released configuration survives an upgrade". Its opposite
# is not a mode of this file: it is a NEW TREE THAT LOST A STATEMENT, which is
# an edit to src/prismash/config.py and not an argument to a shell script. So
# there is no OFF-RED-AT-LEAST header, because there is no off run to have
# measured one from. The mutations each assertion was shown failing under are
# recorded in the commit for #291 instead; the named one is removing the
# `forward` arm of Config._pool_line, which makes the new grammar refuse the
# old version's whole file and takes this lab's central assertion red - 17 of
# the 35 checks below go with it, which is the blast radius the issue names.
#
# It is not in BOTH_WAYS either. That list is the fifteen labs the suite runs
# a second time with workers, and its own comment says every one of them was
# MEASURED both ways before it went in - a lab does not belong there because
# it looks as though it forwards something. This one drives two packets; the
# rest of it is an installer, a grammar, a store on disk and a log, which run
# the same code whatever the thread count. Putting it there is a measurement
# somebody has to take, not a line somebody adds.
#
# Measured on this machine: 40 s run directly in the build container, 93 s
# through tools/lab.sh, which installs its package list first. The slowest lab
# in the suite is about two minutes, so this one is ordinary.
#
# ---------------------------------------------------------------------------
# SOMETHING ELSE THIS LAB FOUND, WHICH IS NOT WHAT IT WAS BUILT FOR
#
# One of the mutations put a default /etc/frr/frr.conf on the box and made FRR
# converge onto it before the boot. The saved file was then refused WHOLE, and
# not for anything to do with the mutation:
#
#   error: this next hop is on no connected network: 200.150.0.2
#       router static / route 10.99.0.0/24 200.150.0.2
#       the route would be accepted and never reach the forwarding table
#   error: refusing to load this file, the check above says why
#
# The next-hop check reads the LIVE VPP FIB, and at `--load` the addresses
# that make that next hop reachable are in the very file being checked and
# have not been applied yet. A normal boot survives it because FRR has already
# read the route out of its own frr.conf, so `routing_changed` is false and
# the check never runs. Which means a box is one lost frr.conf - or one
# ordering change between frr.service and prisma-routing.service - away from
# refusing its entire configuration for a static route that is correct.
#
# That was filed as #294 and fixed, and section 6 now ASSERTS it rather than
# printing it: FRR's own copy is taken away before the boot, so the file below
# is read by a box with nothing on the dataplane and nothing behind it. The
# claim about an ordering this file does not control is still not made - what
# is staged is a reset frr.conf, which is one of the three doors #294 names and
# the one an FRR package upgrade opens on its own.
set -u
. /src/lab/lib.sh      # startup_conf, start_frr, wait_vpp, vc
. /src/lab/verdict.sh  # expect, expect_in, expect_setup, verdict

OLD_TAG=${1:-v0.79.0}
OLD=/tmp/old-tree
CACHE=/src/.build-old/$OLD_TAG
HX=/usr/bin/prismash   # the INSTALLED VTY, deliberately: see section 2
PLUGIN_DIR=/usr/lib/x86_64-linux-gnu/vpp_plugins
VAT2_DIR=/usr/lib/x86_64-linux-gnu/vat2_plugins
CONF=/etc/prisma-routing/prisma-routing.conf
TRAIL=/var/log/prisma-routing/config-history.log

mkdir -p /run/vpp /vpp_plugins /vat2_plugins /var/log/prisma-routing \
         /etc/prisma-routing /var/run/frr /etc/frr

# A fixture that could not be built, said in the words tools/run-all-labs.sh
# buckets separately. Reached only before anything has been measured, which is
# what makes NOT MEASURED the honest answer rather than a softened failure.
give_up() {  # <what could not be built> [detail...]
  echo
  _why=$1; shift
  for _l in "$@"; do echo "  $_l"; done
  echo "=========== VERDICT ==========="
  expect_setup "$_why" false
  verdict
}

echo "=========== 1. the old release, built and installed ==========="
echo "old side: $OLD_TAG   new side: the tree at /src"

rm -rf "$OLD"; mkdir -p "$OLD"
if [ -d "$CACHE" ] && [ -f "$CACHE/appliance/install.sh" ]; then
  cp -a "$CACHE/." "$OLD/"
  echo "  the tag's tree came from $CACHE"
else
  # safe.directory as a one-shot -c and not as a config write: see the header.
  git -c safe.directory=/src -C /src archive "$OLD_TAG" \
    2>/tmp/git-archive.err | tar -x -C "$OLD" 2>/dev/null || true
  if [ -f "$OLD/appliance/install.sh" ]; then
    echo "  the tag's tree came from 'git archive $OLD_TAG' inside /src"
  else
    give_up "the released tree $OLD_TAG could be extracted" \
      "git inside the container could not produce that tree:" \
      "  $(head -1 /tmp/git-archive.err 2>/dev/null)" \
      "git's own line above says which. /src is a WORKTREE, whose .git" \
      "points at a repository outside the bind mount, or the tag is not in" \
      "this repository at all. Neither can be fixed from in here, so extract" \
      "the tree on the host and run again:" \
      "  git -C <repo> archive $OLD_TAG \\" \
      "    | (mkdir -p <repo>/.build-old/$OLD_TAG \\" \
      "       && tar -x -C <repo>/.build-old/$OLD_TAG)"
  fi
fi
echo "  $OLD/VERSION says: $(cat "$OLD/VERSION")"

# Why a build failed, in the compiler's words rather than ninja's.
#
# The first version of this printed tail -5 of the log, and a real failing
# build ends in four "Building C object" lines and "ninja: build stopped" -
# ninja keeps going through the other translation units after the one that
# broke, so the end of the log is the part with nothing in it. What a reader
# needs is the first `error:`, which is what the compiler said.
build_why() {  # <log>
  if grep -q 'error:' "$1" 2>/dev/null; then
    grep -m1 -A4 'error:' "$1" | sed 's/^/  | /'
  else
    tail -5 "$1" 2>/dev/null | sed 's/^/  | /'
  fi
}

# TWO TREES, TWO PLUGINS, AND ONE DIRECTORY THEY BOTH LINK INTO.
#
# VPP's add_vpp_plugin makes /vpp_plugins/bpa_plugin.so the LINK TARGET
# itself - `ninja -t targets` names it, and tools/build.sh copies out of there
# for the same reason. It belongs to the container and to neither tree, so a
# build of one tree overwrites the other's output under the same name, and
# appliance/install.sh reads <tree>/.build-vpp/lib/vpp_plugins/bpa_plugin.so
# in preference to it. Both halves of that have to be got right or the lab
# installs one release's dataplane wearing the other's version.
#
# It went wrong on the second run of this file, which is why it is a function
# with a check in it rather than two pairs of commands. /src/.build-vpp
# persists on the HOST between runs: the second run found it up to date, ninja
# did nothing, /vpp_plugins still held the plugin THIS RUN had just built from
# the OLD tag, and the lab copied that into /src/.build-vpp as "this tree's".
# The upgrade then installed the old dataplane over the old one and reported it as
# the current tree. What caught it was the sha256 comparison two lines down,
# which is the only reason it is not still happening.
#
# So: remove the link target first and REQUIRE it to come back. Removing it is
# enough to make even a warm build directory relink, measured - "[1/1] Linking
# C shared library /vpp_plugins/bpa_plugin.so" - and a full build of this
# plugin from nothing takes about two seconds, so nothing here is being
# optimised at the cost of knowing which build is which.
build_plugin() {  # <source tree> <build dir> <log>
  rm -f /vpp_plugins/bpa_plugin.so /vat2_plugins/bpa_test_plugin_bpa_plugin.so
  mkdir -p "$2" || return 1
  (cd "$2" && cmake -G Ninja "$1" > "$3" 2>&1 && ninja >> "$3" 2>&1) || return 1
  [ -s /vpp_plugins/bpa_plugin.so ] || return 1
  mkdir -p "$1/.build-vpp/lib/vpp_plugins" "$1/.build-vpp/lib/vat2_plugins"
  cp /vpp_plugins/bpa_plugin.so "$1/.build-vpp/lib/vpp_plugins/" || return 1
  cp /vat2_plugins/bpa_test_plugin_bpa_plugin.so \
     "$1/.build-vpp/lib/vat2_plugins/" || return 1
}

echo "  compiling ${OLD_TAG}'s dataplane plugin (the price of the tag route)"
if ! build_plugin "$OLD" /tmp/build-old /tmp/old-build.log; then
  give_up "${OLD_TAG}'s plugin could be compiled" \
    "the old tree does not build against this container's vpp-dev, so" \
    "nothing here was measured about upgrading. What the compiler said:" \
    "$(build_why /tmp/old-build.log)"
fi
OLD_SO_SUM=$(sha256sum "$OLD/.build-vpp/lib/vpp_plugins/bpa_plugin.so" | cut -c1-16)

# And this tree's, into the build directory every other lab uses, because that
# is the one appliance/install.sh reaches for.
if ! build_plugin /src /src/.build-vpp /tmp/new-build.log; then
  give_up "this tree's plugin could be compiled" \
    "the current tree does not build, which is not a question about" \
    "upgrading. What the compiler said:" \
    "$(build_why /tmp/new-build.log)"
fi
NEW_SO_SUM=$(sha256sum /src/.build-vpp/lib/vpp_plugins/bpa_plugin.so | cut -c1-16)
cd /src
echo "  plugin builds: $OLD_TAG is $OLD_SO_SUM, this tree is $NEW_SO_SUM"
# Said before anything is installed. Two trees three releases apart that
# compile to the same bytes is not an upgrade, and every reading after this
# line would be green for the wrong reason.
[ "$OLD_SO_SUM" = "$NEW_SO_SUM" ] && echo "  WARNING: the two builds are byte" \
  "for byte the same plugin"

# The released install, run as the release shipped it. PRISMA_VERSION_STAMP
# because the extracted tree has no .git and tools/version-stamp.sh would
# stamp it "0.68.0 (not a git tree)" - true of the extraction and not of the
# release, and this box's own version is a reading section 4 compares.
if ! PRISMA_VERSION_STAMP="${OLD_TAG#v}" sh "$OLD/appliance/install.sh" \
     > /tmp/old-install.log 2>&1; then
  give_up "${OLD_TAG}'s installer ran" \
    "the released install.sh failed on this machine:" \
    "$(tail -5 /tmp/old-install.log 2>/dev/null | sed 's/^/  | /')"
fi
OLD_STAMP=$(cat /usr/share/prisma-routing/VERSION 2>/dev/null || echo "")
OLD_INSTALLED_SUM=$(sha256sum "$PLUGIN_DIR/bpa_plugin.so" 2>/dev/null | cut -c1-16)
# Read NOW, and not at the verdict. A precondition about what the OLD
# installer did has to be taken before the new one runs: the first version of
# this asked "is the vat2 plugin on the box" in the verdict block, where the
# answer is yes because section 4 installed one. Found by a mutation that
# removed it from the old installer and watched the check stay green.
OLD_VAT2_SUM=$(sha256sum "$VAT2_DIR/bpa_test_plugin_bpa_plugin.so" 2>/dev/null \
               | cut -c1-16)
OLD_VAT2_BUILT=$(sha256sum "$OLD/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so" \
                 2>/dev/null | cut -c1-16)
echo "  installed: version stamp '$OLD_STAMP', plugin $OLD_INSTALLED_SUM"

printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr 2>/dev/null
start_frr

# THE PID COMES FROM $!, NOT FROM pgrep.
#
# Measured, because the first version of this used `pgrep -x vpp` and got
# nothing on a box with VPP plainly running: VPP renames its own main thread,
# so the comm pgrep matches on is "vpp_main" and `-x vpp` matches no process at
# all. The `pkill -x vpp` beside it therefore killed nothing, the second VPP
# could not have the sockets and died with "Active listener on
# /run/vpp/stats.sock", and the lab went on measuring THE OLD DATAPLANE while
# announcing the new one - which is precisely the confusion this lab exists to
# remove. lab/ipfix-stop.sh keeps the pid from $! for the same reason.
vpp -c "$(startup_conf upgrade)" > /tmp/vpp-old.log 2>&1 &
OLD_VPP_PID=$!
wait_vpp /tmp/vpp-old.log

# The fixture's cards. See the header: a real box's NICs come back on their
# own, and these do not.
make_ports() {
  vppctl create packet-generator interface pg0 >/dev/null
  vppctl create packet-generator interface pg1 >/dev/null
  vppctl set interface state pg0 up
  vppctl set interface state pg1 up
  vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
  vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
  vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0
}
make_ports
echo "  the old dataplane answers: $(vc show version | head -1 | cut -c1-40)"

echo
# Everything the old VTY says while it is being configured, kept so that a tag
# whose grammar this lab cannot speak can QUOTE the refusal instead of leaving
# the reader to scroll for it.
SAID=/tmp/old-vty-said.txt
: > "$SAID"

echo "=========== 2. an operator configures the box ==========="
# Through /usr/bin/prismash, which is the launcher the INSTALL put on the box,
# reading /usr/lib/prisma-routing/prismash.py. Every other lab runs
# "python3 /src/src/prismash/prismash.py" from the source tree, and that is
# exactly the shape lab/lib.sh's use_binary_api() was written against: prismash
# had never been started from its installed location, so a missing config.py
# reached every ISO. Here it would be worse - the whole subject of this lab is
# which build is on the box, and driving the source tree would drive the NEW
# VTY for the old box's configuration.
#
# The configuration is a box somebody runs, not a token: two interfaces with
# addresses, descriptions and an MTU, a CGNAT pool with a block size and a
# timeout, the pool attached on both sides, a static port forward, a static
# route, and three commits so the rollback store has revisions in it.
$HX -c "configure" \
    -c "interface pg0" \
    -c "description subscribers, south side" \
    -c "mtu 1500" \
    -c "ipv4 address 100.64.3.254/22" \
    -c "exit" \
    -c "interface pg1" \
    -c "description transit, north side" \
    -c "ipv4 address 200.150.0.1/24" \
    -c "exit" \
    -c "cgnat pool minas" \
    -c "inside 100.64.0.0/22" \
    -c "outside 200.150.0.16/30" \
    -c "block-size 512" \
    -c "timeout udp 300" \
    -c "exit" \
    -c "cgnat interface pg0 inside" \
    -c "cgnat interface pg1 outside" \
    -c "commit comment \"pool minas and the two ports\"" 2>&1 | tee -a "$SAID" | sed 's/^/  /'

# The route in its own commit, so the rollback store holds a revision that is
# the box WITHOUT it - which is what section 9 loads.
$HX -c "configure" \
    -c "ip route 10.99.0.0/24 200.150.0.2" \
    -c "commit comment \"reach the management range\"" 2>&1 | tee -a "$SAID" | sed 's/^/  /'

# And the forward in a third, for the same reason. 1600 is inside subscriber
# .5's block on this pool - 100.64.0.5 is subscriber 5, block 1536-2047 - and
# the arithmetic is lab/forward.sh's, restated because the packet in section 8
# depends on it.
$HX -c "configure" \
    -c "cgnat pool minas" \
    -c "forward tcp 1600 to 100.64.0.5 80" \
    -c "exit" \
    -c "commit comment \"port 80 for the camera at .5\"" 2>&1 | tee -a "$SAID" | sed 's/^/  /'

$HX -c "save" 2>&1 | tee -a "$SAID" | sed 's/^/  /'

echo
echo "=========== 3. what the old box is ==========="
echo "the readings the upgrade must not change. Taken from the DATAPLANE and"
echo "from the file, because the whole failure this lab is about is a file"
echo "that is still on disk and a box that is no longer configured by it."
SAVED=$(cat "$CONF" 2>/dev/null || echo "")
SAVED_SUM=$(sha256sum "$CONF" 2>/dev/null | cut -d' ' -f1)
printf '%s\n' "$SAVED" | sed 's/^/  | /'
OLD_POOL=$(vc show bpa pool)
OLD_FWD=$(vc show bpa forward)
OLD_ADDR=$(vc show interface address)
OLD_RB=$($HX -c "show rollback" 2>&1)
OLD_FRR=$(vtysh -c "show running-config" 2>/dev/null || echo "")
echo "  rollback store:"
printf '%s\n' "$OLD_RB" | sed 's/^/  /'
# The line of the trail that the upgrade must not lose. The plugin opens this
# file O_APPEND (bpa_audit.c:388), so a trail that RESTARTS at the upgrade is
# a trail with a hole in it - and in deterministic mode this file is the
# record of which pool definition was in force at a given instant, which is
# what makes an answer to a court order defensible (docs/logging.md).
TRAIL_OLD=$(grep "pool minas" "$TRAIL" 2>/dev/null | head -1)
TRAIL_LINES=$(wc -l < "$TRAIL" 2>/dev/null || echo 0)
echo "  the trail's first word about this pool: ${TRAIL_OLD:-<nothing>}"

# The forward forwarding, BEFORE the upgrade. Without this reading, section 8
# would be an assertion about a box that may never have forwarded anything -
# and a forward that was broken all along would read as an upgrade breaking
# it, which sends the investigation to the wrong place.
tcp() { # tcp <src> <dst> <sport> <dport> <flags> <interface>
  H=$(python3 /src/lab/mktcp.py "$1" "$2" "$3" "$4" "$5")
  L=$(( ${#H} / 2 - 1 ))
  vppctl packet-generator new "name p limit 1 node ip4-input interface $6 \
      size ${L}-${L} data { hex $H }" >/dev/null
  vppctl clear trace >/dev/null
  vppctl trace add pg-input 5 >/dev/null
  vppctl packet-generator enable >/dev/null
  sleep 1
  vppctl packet-generator delete p >/dev/null 2>&1
}
trace() { vppctl show trace | grep -E "BPA (in2out|out2in)"; }
# A needle in a haystack, as a COMMAND, because expect_setup takes one. The
# same test expect_in makes, in the one place a precondition needs it.
has() { case "$2" in *"$1"*) return 0 ;; esac; return 1; }

echo "  an unsolicited packet to 200.150.0.17:1600, on the OLD dataplane:"
tcp 8.8.8.8 200.150.0.17 33000 1600 syn pg1
OLD_TRACE=$(trace)
printf '%s\n' "$OLD_TRACE" | sed 's/^/    /'


# The fixture, judged HERE and not at the verdict, because walking past it is
# what makes a run lie.
#
# Measured: `sh tools/lab.sh upgrade v0.40.0` closed LAB FAILED: 20 check(s).
# That tag's VTY refused the configuration this lab types - "pool 'minas':
# inside and outside required", a grammar that moved since - so the old box
# was never configured and there was no upgrade to measure. The two
# preconditions below did report NOT MEASURED, and it made no difference: a
# FAILED outranks a NOT MEASURED in the verdict, so the run said the PRODUCT
# was broken in twenty ways, about a box that had never been configured in
# the first place. A false red costs more than a wait, because somebody goes
# and investigates it.
#
# So this joins the extraction and the compile in give_up(), for the reason
# the header gives about those two: past this point every assertion is about
# a box that does not exist, and an assertion about a box that does not exist
# has no verdict to give. It is not restated at the verdict, where it could
# only ever be green.
has "pool minas" "$SAVED" || give_up \
  "the old box was configured and saved its configuration" \
  "$OLD_TAG's VTY did not accept the configuration this lab types, so there" \
  "is no configured box to upgrade. What it said:" \
  "$(grep -m1 '^ *error:' "$SAID" 2>/dev/null | sed 's/^ *//')" \
  "$(grep -m3 '^ *error:' "$SAID" 2>/dev/null | tail -1 | sed 's/^ *//')" \
  "This is the lab's fixture and not the product: the statements here are" \
  "spelled the way the CURRENT VTY spells them, and a tag old enough to" \
  "spell them differently cannot be configured by it."
has 100.64.0.5 "$OLD_TRACE" || give_up \
  "the old box really forwarded the pinned port" \
  "the forward was configured and the old dataplane did not translate a" \
  "packet through it, so section 8 would be reading a box that never" \
  "forwarded anything, and an upgrade would take the blame for it."

echo
echo "=========== 4. the upgrade ==========="
echo "this tree's appliance/install.sh, over the running box. Nothing else:"
echo "no reinstall, no reboot, no hand editing. This is the path the file"
echo "itself claims at its line 4."
sh /src/appliance/install.sh > /tmp/new-install.log 2>&1 || {
  give_up "this tree's installer ran over the old box" \
    "appliance/install.sh failed, which is the upgrade itself failing:" \
    "$(tail -10 /tmp/new-install.log 2>/dev/null | sed 's/^/  | /')"
}
# The CLOSING WORDS, whole, and not a fixed tail of them. What the installer
# says when it finds a version already on the box is #291 item 3 and it is
# asserted at the verdict, so cutting it to six rows would show the reader the
# half that was already there and hide the half this lab is holding.
NEW_INSTALL_SAID=$(cat /tmp/new-install.log)
sed -n '/^==> documentation/,$p' /tmp/new-install.log | sed 's/^/  | /'
NEW_STAMP=$(cat /usr/share/prisma-routing/VERSION 2>/dev/null || echo "")
NEW_INSTALLED_SUM=$(sha256sum "$PLUGIN_DIR/bpa_plugin.so" 2>/dev/null | cut -c1-16)
AFTER_SUM=$(sha256sum "$CONF" 2>/dev/null | cut -d' ' -f1)
echo "  version stamp: '$OLD_STAMP' -> '$NEW_STAMP'"
echo "  plugin on disk: $OLD_INSTALLED_SUM -> $NEW_INSTALLED_SUM"

echo
echo "=========== 5. what the upgrade did NOT do ==========="
echo "#291 item 2, measured rather than argued. The installer has replaced the"
echo "plugin file and restarted nothing, so the dataplane carrying traffic is"
echo "still the one that was there before it ran."
# /proc/<pid>/maps is the only place on the box that can answer this, and that
# is the finding: `show plugins` reports the plugin's .version, which is the
# literal "0.1" at src/plugins/bpa/bpa.c and has not moved in seventy-one
# releases (#291 item 3). So nothing an operator can type says which build is
# translating their subscribers' packets; this lab has to read the kernel's
# view of the process to find out.
MAPPED=$(tr -d '\0' < /proc/"$OLD_VPP_PID"/maps 2>/dev/null \
         | grep -o '/[^ ]*bpa_plugin.so.*' | head -1)
RUNNING_PLUGINS=$(vc show plugins 2>/dev/null | grep -i bpa)
echo "  the running VPP has mapped: ${MAPPED:-<could not read>}"
echo "  and everything the box can be ASKED says:"
printf '%s\n' "$RUNNING_PLUGINS" | sed 's/^/    /'
echo "  a '(deleted)' above is the installer having unlinked the .so under a"
echo "  process that is still running out of it."
echo
echo "  WHAT THE OPERATOR IS TOLD ABOUT IT is now the installer's closing"
echo "  words, printed in section 4 above and asserted at the verdict. This"
echo "  container has no systemd, so install.sh takes the offline arm of its"
echo "  own branch here and says the half that is true of any root: which"
echo "  build replaced which. The paragraph naming vpp.service and what"
echo "  restarting it costs is the other half, and it is reachable only on a"
echo "  box whose init is running - which is not this one, and is why"
echo "  test/test_install_says.sh drives those screens directly."

# AND WHAT THE SPLIT COSTS, which is worse than "untested" and was found here.
#
# In this window the VTY, its Python and the vat2 plugin are the NEW build and
# the dataplane is the old one - and the binary API between them is versioned.
# So an operator who upgrades and then changes anything before rebooting is
# talking to a plugin that does not speak the message their VTY is sending.
# Measured below, on a box in exactly the state appliance/install.sh leaves.
#
# WHETHER IT IS REFUSED AT ALL IS STILL PRINTED AND NOT ASSERTED, deliberately
# and for the reason this comment has always given. An assertion that this
# FAILS pins today's behaviour and would go red on the day somebody fixes it -
# which is exactly what lab/crossaddr.sh did when #226 landed, and it then had
# to be left out of the suite, asserting the opposite of the box, until it
# could be rewritten against a running one. An assertion that it SUCCEEDS would
# make this lab permanently red for a defect it is not the one holding.
#
# WHAT IS ASSERTED NOW is the other half, which is the one #293 fixed: IF the
# old dataplane refuses, the refusal says which dataplane answered, that the
# upgrade is why, and what clearing it would cost. The verdict makes that
# assertion conditional on the refusal having happened, and says so - so a
# released tag whose API this tree has not moved runs green, and a tag whose
# API it HAS moved is READ rather than assumed.
# A COMMIT and not a `commit check`. Measured both: the check passes in this
# window, because checking a per-pool setting does not send the message the
# dataplane would have to understand. It is the write that finds out, and the
# write is what an operator does at two in the morning.
#
# Safe to leave behind: the saved file still says 300, section 6 restarts VPP
# and section 7 asserts the value out of the FILE - so a split-window change
# that did take effect is undone by the boot, which is itself the point.
SPLIT=$($HX -c "configure" -c "cgnat pool minas" -c "timeout udp 301" \
            -c "exit" -c "commit comment \"changed in the split window\"" 2>&1)
echo "  the new VTY asking the old dataplane to make a change:"
printf '%s\n' "$SPLIT" | tail -4 | sed 's/^/    /'
# And a NEW pool, which is the other thing an operator does. Separate, because
# the two answers differ and the difference is the whole news: a setting on an
# existing pool goes through, and creating one sends a message whose shape
# moved between the two releases.
SPLIT_ADD=$($HX -c "configure" -c "cgnat pool split291" \
            -c "inside 10.77.0.0/22" -c "outside 198.51.100.0/30" \
            -c "block-size 512" -c "exit" \
            -c "commit comment \"a pool created in the split window\"" 2>&1)
echo "  and to create a pool it has never seen:"
# Printed WHOLE, not tailed. Three lines was right while the answer was two -
# a refusal naming a symbol, and a rollback. The refusal now carries its
# reason with it and that reason is this reading's whole subject (#293), so
# cutting it off would leave the lab showing the half that was already known.
printf '%s\n' "$SPLIT_ADD" | sed 's/^/    /'

echo
echo "=========== 6. the boot ==========="
echo "the dataplane restarted - BY THIS LAB, which is the step install.sh"
echo "does not take - and then the one command prisma-routing.service runs:"
echo "  prismash --load $CONF"
echo "with the file the OLD version wrote, untouched. This is the assertion"
echo "the whole lab exists for: it is accepted WHOLE, or the box comes up"
echo "with nothing on it."
kill -TERM "$OLD_VPP_PID" 2>/dev/null
n=0
while kill -0 "$OLD_VPP_PID" 2>/dev/null && [ "$n" -lt 30 ]; do
  sleep 1; n=$((n + 1))
done
kill -0 "$OLD_VPP_PID" 2>/dev/null && kill -9 "$OLD_VPP_PID" 2>/dev/null
# And the SOCKET, not only the process. A VPP that has gone still leaves
# /run/vpp/cli.sock behind for a moment, and a second VPP that cannot have the
# sockets exits with "Active listener on /run/vpp/stats.sock" - at which point
# wait_vpp is answered by nobody and the lab is measuring a dataplane that is
# not there.
n=0
while vppctl show version >/dev/null 2>&1 && [ "$n" -lt 15 ]; do
  sleep 1; n=$((n + 1))
done
echo "  the old dataplane (pid $OLD_VPP_PID) is gone"
vpp -c "$(startup_conf upgrade)" > /tmp/vpp-new.log 2>&1 &
NEW_VPP_PID=$!
wait_vpp /tmp/vpp-new.log
NEW_MAPPED=$(tr -d '\0' < /proc/"$NEW_VPP_PID"/maps 2>/dev/null \
             | grep -o '/[^ ]*bpa_plugin.so.*' | head -1)
echo "  the new dataplane has mapped: ${NEW_MAPPED:-<could not read>}"
make_ports

# THE CRUTCH THIS BOOT HAS BEEN LEANING ON, TAKEN AWAY (#294).
#
# Until here FRR has been running since section 1 with the static route in its
# own /etc/frr/frr.conf, and that is what made the boot below survive: the
# route is already in FRR, so `routing_changed` is false and the next-hop check
# never reaches the branch that refused the file. The box's configuration was
# protected by a second copy of it in another daemon's file - and this
# repository treats a second copy as a defect in its own right, so it may not
# be what holds the boot up.
#
# What this stages is the FIRST of the three doors #294 names, and it is a door
# an FRR package upgrade opens by itself: frr.conf is a conffile, and a
# reinstall replaces it. Nothing here is a step the product is missing - the
# box is left with LESS than a real boot has, never more, which is the only
# direction a fixture is allowed to move a boot in.
pkill -f '/usr/lib/frr/' 2>/dev/null
n=0
while pgrep -f '/usr/lib/frr/' >/dev/null 2>&1 && [ "$n" -lt 15 ]; do
  sleep 1; n=$((n + 1))
done
: > /etc/frr/frr.conf
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
chown -R frr:frr /var/run/frr /etc/frr 2>/dev/null
start_frr
# READ, not assumed. A reset that left the route in place would make the
# assertion below green for the boot nobody is worried about, which is the
# whole reason this is an expect_setup at the verdict and not a comment.
FRR_ROUTES_BEFORE=$(vtysh -c "show running-config" 2>/dev/null \
                    | grep -c "^ip route" || true)
echo "  FRR was reset: it holds ${FRR_ROUTES_BEFORE:-?} static route(s)"
echo "  the dataplane holds no address at all:"
vc show interface address | grep -c "L3" | sed 's/^/    L3 addresses: /'

LOAD=$("$HX" --load "$CONF" 2>&1); LOAD_RC=$?
printf '%s\n' "$LOAD" | sed 's/^/  | /'
echo "  exit status: $LOAD_RC"

echo
echo "=========== 7. the pool is in the dataplane, not only in the file ==========="
NEW_POOL=$(vc show bpa pool)
# `show bpa config` is the dataplane restating the configuration in the words
# the configuration is written in, which is what lets these needles be the
# statements the operator typed rather than a rendering of them. `show bpa
# pool` prints "inside 100.64.0.0+1024" for the same pool - true, and not a
# sentence anybody wrote. lab/in-place.sh reads this same output for the same
# reason.
NEW_CONF=$(vc show bpa config)
NEW_FWD=$(vc show bpa forward)
NEW_ADDR=$(vc show interface address)
NEW_FRR=$(vtysh -c "show running-config" 2>/dev/null || echo "")
FRR_FILE=$(cat /etc/frr/frr.conf 2>/dev/null || echo "")
printf '%s\n' "$NEW_POOL" | sed 's/^/  /'
echo "  and the same pool in the words it was configured in:"
printf '%s\n' "$NEW_CONF" | sed -n '/pool minas/,/exit/p' | sed 's/^/    /'
printf '%s\n' "$NEW_FWD" | sed 's/^/  /'
echo "  addresses:"
printf '%s\n' "$NEW_ADDR" | grep -E "^(pg0|pg1)|L3" | sed 's/^/    /'
echo "  the static route, in FRR and in the file a reboot would read:"
printf '%s\n' "$NEW_FRR" | grep "ip route" | sed 's/^/    /'
printf '%s\n' "$FRR_FILE" | grep "ip route" | sed 's/^/    /'

echo
echo "=========== 8. the forward still forwards ==========="
echo "the same packet as section 3, against the new dataplane holding the old"
echo "configuration. A pool that is in 'show' and does not translate is the"
echo "failure this section separates from the one above."
tcp 8.8.8.8 200.150.0.17 33000 1600 syn pg1
NEW_TRACE=$(trace)
printf '%s\n' "$NEW_TRACE" | sed 's/^/  /'
echo "  and the subscriber answering on the forwarded port:"
tcp 100.64.0.5 8.8.8.8 80 33000 "syn,ack" pg0
ANSWER_TRACE=$(trace)
printf '%s\n' "$ANSWER_TRACE" | sed 's/^/  /'

echo
echo "=========== 9. the old version's rollback revisions ==========="
echo "written by ${OLD_TAG}'s VTY, into /etc/prisma-routing/rollback, and read"
echo "here by this tree's. A store the new version cannot read is a box with"
echo "no way back, discovered in the maintenance window where somebody needs"
echo "one."
NEW_RB=$($HX -c "show rollback" 2>&1)
printf '%s\n' "$NEW_RB" | sed 's/^/  /'
# The revision picked by the COMMENT its operator typed, not by a number.
# Which slot it is in depends on whether `--load` has just pushed one of its
# own, and whether it did is exactly what this lab is measuring - so a
# hardcoded number reads a different revision on a good box and on a bad one.
# 99 when the comment is not there at all, so the lab is told the revision it
# MEANS does not exist, rather than falling back to `rollback` with no
# argument, which means something else entirely.
RB_N=$(printf '%s\n' "$NEW_RB" | awk '/port 80 for the camera/ {print $1; exit}')
RB_N=${RB_N:-99}
echo "  the old version's last commit is revision ${RB_N}, loading it:"
# Loaded into the candidate and then CHECKED, never committed. The check is
# what says the new VTY can still make sense of every statement in it, which
# is the same question section 6 asks about the saved file - and this is the
# store somebody reaches for in the maintenance window after an upgrade went
# wrong, which is the worst moment to find out.
RB_LOAD=$($HX -c "configure" -c "rollback $RB_N" -c "show configuration" \
              -c "commit check" 2>&1)
# The pool out of the candidate, and then the CHECK'S ANSWER. Printing the
# head of this alone was a mistake caught by a mutation: the revision came
# back looking perfect and the refusal underneath it was off the bottom of
# the section, so the capture showed a green-looking block above a red check.
printf '%s\n' "$RB_LOAD" | sed -n '/pool minas/,/ exit/p' | sed 's/^/  /'
printf '%s\n' "$RB_LOAD" | tail -5 | sed 's/^/  /'

echo
echo "=========== 10. the trail ==========="
TRAIL_NOW=$(cat "$TRAIL" 2>/dev/null || echo "")
TRAIL_LINES_NOW=$(wc -l < "$TRAIL" 2>/dev/null || echo 0)
echo "  $TRAIL_LINES lines before the upgrade, $TRAIL_LINES_NOW after it"
printf '%s\n' "$TRAIL_NOW" | grep "pool minas" | head -2 | sed 's/^/  /'
printf '%s\n' "$TRAIL_NOW" | tail -2 | sed 's/^/  /'

echo
echo "=========== VERDICT ==========="

# The fixture first, all of it, so a reader can tell a lab that measured an
# upgrade from one that never got as far as making one.
# The preconditions that are NOT here - the tree was extracted, its plugin
# compiled, the old box was configured, and it forwarded - are reported by
# give_up() at the point they fail, because the lab cannot walk past any of
# them and produce a reading about anything. Restating them at the verdict
# would be checks that can only ever be green, which is the defect this whole
# repository keeps finding inside the machinery built to catch it.
expect_setup "its installer put that plugin on the box" \
  test "$OLD_INSTALLED_SUM" = "$OLD_SO_SUM"
expect_setup "and the vat2 plugin the VTY needs to reach the API" \
  test -n "$OLD_VAT2_SUM" -a "$OLD_VAT2_SUM" = "$OLD_VAT2_BUILT"
# #294: the file was read by a box with nothing on it and nothing behind it.
# Without this the central assertion below is about a boot where FRR had
# already converged onto its own copy of the static route, which is the one
# case that was never in doubt.
expect_setup "FRR held no copy of the configuration when the file was read" \
  test "${FRR_ROUTES_BEFORE:-1}" -eq 0

# THE CENTRAL ASSERTION. Two halves, because they fail differently: a file
# that was refused says so and exits non-zero, and `--load` also returns
# non-zero when a statement was accepted and could not be APPLIED.
expect "the new VTY accepted the old version's file whole" \
  test "$LOAD_RC" -eq 0
expect_not_said "nothing in it was refused" "refusing to load this file" "$LOAD"
expect_not_said "and no line of it was unreadable to the new grammar" \
  "are not understood" "$LOAD"
# #294 by name. The file's static route is made reachable by an interface
# address a few lines above it in the same file, and the check that reads
# whether a next hop is connected used to read the LIVE FIB - which at this
# moment holds nothing, because the addresses have not been applied yet. The
# box refused its entire configuration over a route that is correct.
expect_not_said "and not over a next hop its own address makes reachable" \
  "this next hop is on no connected network" "$LOAD"

# The upgrade must not have touched the operator's file. install.sh creates
# prisma-routing.conf only when it is absent, and this is what says so on a box
# that HAS one - the case a first install never reaches.
expect "the upgrade left the saved configuration byte for byte" \
  test "$SAVED_SUM" = "$AFTER_SUM"
# Two halves, and the first one had to be added after a mutation walked
# through it. An installer that skipped the stamp entirely left
# /usr/share/prisma-routing/VERSION empty, and "the stamp changed" was GREEN for
# a box that had stopped stating a version at all - a box in that state is one
# nobody on the phone can identify.
expect "the box still states a version after the upgrade" test -n "$NEW_STAMP"
expect "and it is not the one it was running before" \
  test "$OLD_STAMP" != "$NEW_STAMP"
expect_in "the new version stamp is this tree's" \
  "$(tr -d '[:space:]' < /src/VERSION)" "$NEW_STAMP"

# AND THAT THE INSTALLER SAID SO (#291 item 3).
#
# The two stamps here are real: this box was the old release twenty minutes ago and
# the installer had to read that off the disk before it overwrote it. Nothing
# else in this repository can assert that, because everywhere else one of the
# two versions is a fixture.
#
# expect_said and not expect_in, on both directions. These are sentences the
# installer wraps by hand, so a needle held against raw text matches only for
# as long as the message goes on folding where it folds today - and the
# negative below is the one that would then pass whether the box said it or
# not, which is #279 exactly.
expect_said "the installer said this was an upgrade and not a first install" \
  "Upgraded. Every file is in place, and nothing was restarted." \
  "$NEW_INSTALL_SAID"
expect_said "and named the build that was on this box: $OLD_STAMP" \
  "the version stamp was $OLD_STAMP" "$NEW_INSTALL_SAID"
expect_said "and the build that is on it now: $NEW_STAMP" \
  "and now says $NEW_STAMP" "$NEW_INSTALL_SAID"
# The defect itself. Until #291 item 3 this ran over a configured, tuned,
# production-shaped box and closed by telling the operator to go and tune it.
expect_not_said "and did not close with a first install's sentence" \
  "Adjust /etc/prisma-routing/tune.env and reboot so that isolcpus and \
hugepages take effect." "$NEW_INSTALL_SAID"
# This container has no systemd, so install.sh takes the offline arm of its
# own branch and must not describe services out of a root it did not write.
# The paragraph itself is driven by test/test_install_says.sh.
expect_not_said "and said nothing about services this root does not have" \
  "it is what translates every packet on this box" "$NEW_INSTALL_SAID"
expect "and the plugin on disk changed with it" \
  test "$OLD_INSTALLED_SUM" != "$NEW_INSTALLED_SUM"
expect "the dataplane now runs the plugin the upgrade installed" \
  test "$NEW_INSTALLED_SUM" = "$NEW_SO_SUM"

# AND WHAT THE BOX SAYS WHILE THE TWO DISAGREE (#293).
#
# Section 5 tried to create a pool through the new VTY while the old plugin
# was still loaded, and printed what came back. Until #293 that was
#
#   error: pool 'split291': Call failed: bpa_pool_add_del
#   error: commit failed; rolling back to the previous state
#
# which names a symbol and nothing an operator can act on: not that the thing
# answering is the build from before the upgrade, not that the upgrade is why,
# not what clearing it would cost. #293 moved this reading from printed to
# asserted, which is what its own last line asked for.
#
# CONDITIONAL, and the condition is read rather than assumed. This lab takes a
# tag, and only a tag whose API message this tree has since changed produces
# the refusal at all - a tag that does not would make an
# unconditional assertion red for no defect. So the refusal is the fixture and
# the explanation is the subject, which is the split expect_setup exists for;
# it is not spelled expect_setup because a dataplane that ACCEPTS the pool has
# not failed to build anything, it has simply left this lab nothing to read.
#
# expect_said and not expect_in, for the reason verdict.sh gives beside it:
# the VTY wraps this paragraph to the console, so a needle matched against raw
# text only matches while the message happens to fold somewhere else. This
# repository has been bitten by that four times.
case "$SPLIT_ADD" in
  *"commit failed"*)
    expect_said "a pool refused by the old dataplane says WHICH dataplane \
answered" \
      "the dataplane is not the build the rest of this box is" "$SPLIT_ADD"
    expect_said "and names the build that answered" \
      "the dataplane is running" "$SPLIT_ADD"
    # The claim is "which of the two", so what is asserted is that the build
    # named is NOT the one the upgrade just stamped on this box. The literal
    # the old plugin reports is "0.1" on every tag before #291 and a real
    # stamp on every tag after it, and pinning either would be this lab
    # asserting its own fixture's history instead of the sentence.
    expect_not_said "and it is not the build the upgrade just installed" \
      "the dataplane is running $NEW_STAMP" "$SPLIT_ADD"
    expect_said "and that the upgrade is the reason, not the configuration" \
      "An upgrade replaces the dataplane's files on disk and does not \
restart it" "$SPLIT_ADD"
    expect_said "and what clearing it would cost, which makes it the \
operator's decision" \
      "ends every session and every port block this box is carrying" \
      "$SPLIT_ADD"
    ;;
  *)
    echo "  ---- ${OLD_TAG}'s dataplane accepted a pool created in the split"
    echo "       window, so there was no refusal here to explain. #293's"
    echo "       assertions are read on a tag whose API message moved, and"
    echo "       this one's has not moved since."
    ;;
esac

# A file that is accepted and applies nothing would pass everything above.
# These are the box.
expect_in "the pool is in the dataplane" "pool minas" "$NEW_POOL"
expect_in "with the inside prefix it was given" " inside 100.64.0.0/22" \
  "$NEW_CONF"
expect_in "the outside range" " outside 200.150.0.16/30" "$NEW_CONF"
expect_in "and the block size, which is what every port means" \
  " block-size 512" "$NEW_CONF"
expect_in "and the timeout the operator set, which is not a default" \
  " timeout udp 300" "$NEW_CONF"
expect_in "the pool is attached on the subscriber side" "bpa-in2out" \
  "$(vc show interface features pg0)"
expect_in "and on the transit side" "bpa-out2in" \
  "$(vc show interface features pg1)"
expect_in "the static forward is in the dataplane" "100.64.0.5:80" "$NEW_FWD"
expect_in "the subscriber-side address came back" "100.64.3.254" "$NEW_ADDR"
expect_in "and the transit-side address" "200.150.0.1" "$NEW_ADDR"
expect_in "the static route is still in FRR" \
  "ip route 10.99.0.0/24 200.150.0.2" "$NEW_FRR"
expect_in "and in the file the next boot would read it from" \
  "ip route 10.99.0.0/24 200.150.0.2" "$FRR_FILE"

# The packet. "show" agreeing with the file proves the VTY can read its own
# writing; only this proves the box does the work.
expect_in "an unsolicited packet still reaches the pinned subscriber" \
  "100.64.0.5" "$NEW_TRACE"
expect_in "on the port the forward names" ":80" "$NEW_TRACE"
expect_in "and the subscriber's answer leaves on the forwarded port" \
  "200.150.0.17" "$ANSWER_TRACE"

# The way back, which is the thing an operator reaches for when an upgrade
# went wrong - so a store the new version cannot read is the worst possible
# casualty of one.
# By the operator's own COMMENT, not by the label. "2 commit(s) ago" is a
# label `show rollback` prints for whatever is in slot 2, so it would be there
# on a store the upgrade had emptied and refilled; the comment is a string
# v0.68.0's VTY wrote into that file and nothing since has had a reason to
# write again.
expect_in "the old version's revisions are still listed" \
  "port 80 for the camera at .5" "$NEW_RB"
expect_in "and its last one loads into the new VTY's candidate" \
  "load complete (revision $RB_N)" "$RB_LOAD"
# What came back, and not only that something did. `rollback` says "load
# complete" for any revision it read; this is the revision's CONTENT arriving
# in the candidate, in the statement the old version's operator typed last.
expect_in "and what came back is the box the old version committed" \
  " forward tcp 1600 to 100.64.0.5 80" "$RB_LOAD"
# The POSITIVE sentence and not the absence of a negative one. A check for
# "no error appeared" passes on a revision the VTY never read at all, which is
# the same trap lab/verdict.sh's expect_not_said comment describes one layer
# down.
expect_in "and the new VTY validates what it read, statement by statement" \
  "configuration check succeeds" "$RB_LOAD"

# The trail. Appended to, not restarted: the upgrade is a line in the history
# of the box and not a new history.
expect "the trail kept everything the old version wrote" \
  test "${TRAIL_LINES_NOW:-0}" -ge "${TRAIL_LINES:-1}"
expect_in "including the pool's first appearance, in the old version's words" \
  "${TRAIL_OLD:-nothing was written}" "$TRAIL_NOW"
expect "and the new dataplane went on writing to the same file" \
  test "${TRAIL_LINES_NOW:-0}" -gt "${TRAIL_LINES:-0}"

verdict
