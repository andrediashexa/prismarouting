#!/bin/sh
# Installs Prisma Routing onto an existing Debian 12.
#
# Does not build an ISO: for a fresh install see build-iso.sh. This path is
# for updating a box in production without reinstalling it.
#
# THE NAMES ON THE DISK ARE STILL THE OLD ONES, and this file is where they
# are laid down, so this is where the reason is written (#202). The product
# is Prisma Routing; the banner, the prompt, `show version`, the self-test
# and the medium's filename say so. Everything below keeps `prisma-routing`:
#
#   /usr/bin/prismash            the login shell recorded in /etc/passwd for
#                              every account the VTY created; an account
#                              whose shell does not exist cannot log in
#   /etc/prisma-routing/*         the saved configuration and its rollback
#                              store, which an upgrade must still find
#   /var/log/prisma-routing       a mountpoint in fstab holding the
#                              configuration trail and the exporter's spill;
#                              the connection records themselves are EXPORTED
#                              and kept by the collector (#401)
#   /usr/lib/prisma-routing,      the runtime and the installer, named by every
#   /usr/sbin/prisma-routing-install
#                              unit and by the VTY by absolute path
#   prisma-routing*.service       unit names, and the vpp.service drop-ins
#   prisma-admin, prisma-cgnat     the role groups; membership is by GID and a
#                              group recreated under a new name takes every
#                              operator's role away
#   PRISMA_*                     the environment the VTY, the tuner and the
#                              labs read and set
#
# Renaming any of those is a migration of a box in service, not a rename,
# and this release deliberately makes none: an upgraded box changes what it
# prints and nothing it has on disk.
#
#
# WHAT THIS SCRIPT NEEDS AND WHERE AN OPERATOR GETS IT. Every install line
# below copies out of $REPO, and the plugin comes from .build-vpp or from a
# build container's /vpp_plugins, so this script cannot run where those are
# not. A box installed from a published medium has none of them - measured on
# one: /usr/lib/prisma-routing holds the runtime files and no install.sh, there is
# no repository and there is no toolchain (#295). For most of this project's
# life that meant this path served the release gate and lab/upgrade.sh, which
# have a build tree, while five sentences in the product told an operator
# holding a medium that it was theirs.
#
# It is theirs now, and this file is still the only implementation of it.
# appliance/build-upgrade.sh packs the four directories the ISO installs from
# with the compiled plugins already inside, and appliance/upgrade runs THIS
# SCRIPT out of the unpacked result. So the upgrade an operator performs is
# byte for byte the one the release gate and lab/upgrade.sh have always
# driven, rather than a second implementation that nothing exercises - which
# is the only reason it can be believed.
#
# WHAT THE ARTIFACT CANNOT DO, because it bounds what this path is: it carries
# product files and no packages, so it cannot move the VPP or FRR that
# appliance/build-iso.sh pins. A release that changes either is refused by
# appliance/upgrade, naming both versions, and the operator reinstalls from the
# medium - which erases the disk and takes /var/log/prisma-routing with it: the
# configuration trail, and whatever the exporter had not yet delivered to the
# collector. The wiki page on taking a new version states both routes and what
# each costs.
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
DESTDIR=${DESTDIR:-}
PLUGIN_DIR=${PLUGIN_DIR:-/usr/lib/x86_64-linux-gnu/vpp_plugins}

say() { echo "==> $*"; }

# WHAT WAS HERE BEFORE, read before a single byte of it is overwritten.
#
# This is the only moment the question can be asked. Forty lines down this
# script writes /usr/share/prisma-routing/VERSION with the build it is putting on
# the box, and from then on there is nothing left that remembers what the box
# used to be - the stamp is the one file that answers it, and this script is
# what destroys the answer. Read late, it reads back what this run just wrote
# and every install looks like a first install, which is the state this file
# shipped in.
#
# Empty means a first install: an ISO build into a fresh debootstrap, or a box
# that never had this product. It is also what a box whose stamp somebody
# deleted looks like, and that is the right way round - saying nothing about
# running services is the quiet failure, and saying they are the old build
# when there is no evidence of an older build would be a claim this script
# cannot support.
PREV_STAMP=$(cat "$DESTDIR/usr/share/prisma-routing/VERSION" 2>/dev/null || true)

# EVERY COMPONENT THIS BUILD IS MADE OF, RESOLVED BEFORE ANY OF IT IS WRITTEN.
#
# THE TWO PLUGINS ARE ONE BUILD PRODUCT. tools/build.sh compiles them in one
# ninja run and copies both out of the container in the same block, and
# lab/upgrade.sh's build_plugin does the same with `|| return 1` on each. So a
# tree that has bpa_plugin.so and no vat2 plugin beside it is not a tree
# somebody chose to build that way, it is a build that did not finish. And
# there is no box that wants one and not the other: the vat2 plugin is what
# lets anything outside VPP speak this plugin's binary API, which is how prismash
# learns that a configuration was REFUSED rather than applied. #119 is what a
# medium without it cost - every release shipped with that API unregistered and
# no CGNAT pool configurable on the installed box, which is the one thing the
# appliance exists to do.
#
# WHY THIS REFUSES WHERE IT USED TO WARN. It printed "warning: no vat2 plugin"
# on stderr and carried on: it wrote the new bpa_plugin.so over the old one,
# left the PREVIOUS version's vat2 plugin exactly where it was, and stamped the
# box with the new version. The box then asserts a build it has half of, with a
# vat2 plugin older than the dataplane plugin beside it - the skew #293
# measured as unable to answer a message that grew a field. Nothing on the box
# can read the half that is wrong: #291's `show version` compares the stamp
# against the build the loaded plugin reports and both of those are the new
# version, and the self-test asks whether the API answers, not which build is
# answering. That is why the combination is worse than either half of it, and
# why the answer is a refusal and not a louder warning (#301).
#
# WHY IT IS UP HERE AND NOT WHERE THE PLUGINS ARE INSTALLED. A refusal is only
# worth taking while it still costs nothing, and it costs nothing only while
# nothing has been written. The bpa_plugin.so refusal used to sit forty lines
# down, past the whole of src/prismash: a tree with no dataplane plugin left a
# box carrying a NEW VTY over an OLD dataplane and told the reader to run
# tools/build.sh, which is this same half state with one component swapped.
# Resolved up here, a box whose upgrade is refused is still wholly the build it
# was this morning, and an upgrade that failed having changed nothing is the
# only outcome an operator woken at two in the morning can act on.
#
# THE SAME ON BOTH PATHS, deliberately, because what is refused is a property
# of the BUILD TREE and not of the root being written. A $DESTDIR fakeroot gets
# no old vat2 plugin left behind - it gets none at all - but the medium
# assembled from that tree carries the missing half to every box installed from
# it, permanently, and the box's own route out of it is an upgrade artifact
# that would have to be built from the same broken tree. appliance/build-iso.sh
# already refuses both plugins before it spends ten minutes on a debootstrap,
# and asserts afterwards that the vat2 plugin reached the image;
# appliance/build-upgrade.sh refuses the same pair before it packs anything.
# This is the guard inside the script all three of those are watching.
if [ -f "$REPO/.build-vpp/lib/vpp_plugins/bpa_plugin.so" ]; then
  SO="$REPO/.build-vpp/lib/vpp_plugins/bpa_plugin.so"
elif [ -f /vpp_plugins/bpa_plugin.so ]; then
  SO=/vpp_plugins/bpa_plugin.so
else
  echo "bpa_plugin.so not found; run tools/build.sh plugin first. Nothing" >&2
  echo "has been written, so this root is still whatever it was before." >&2
  exit 1
fi

VAT2_DIR=${VAT2_DIR:-/usr/lib/x86_64-linux-gnu/vat2_plugins}
if [ -f "$REPO/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so" ]; then
  VSO="$REPO/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so"
elif [ -f /vat2_plugins/bpa_test_plugin_bpa_plugin.so ]; then
  VSO=/vat2_plugins/bpa_test_plugin_bpa_plugin.so
else
  # Named in full, and it names the OTHER plugin too: the reader is standing
  # in front of a tree that has one and not the other, and the single most
  # useful thing to tell them is that the build is half done rather than
  # absent. Whoever reads this has a build tree by definition: an operator's
  # upgrade artifact is packed from one, and appliance/build-upgrade.sh
  # refuses a half-finished build before it can become one.
  echo "bpa_test_plugin_bpa_plugin.so not found; run tools/build.sh plugin" >&2
  echo "first. This tree HAS bpa_plugin.so and the two are built" >&2
  echo "together, so this build did not finish." >&2
  echo >&2
  echo "Refused rather than installed without it. The vat2 plugin is how" >&2
  echo "anything outside VPP reaches this plugin's binary API, so a box" >&2
  echo "that took this upgrade would run a new dataplane behind the" >&2
  echo "PREVIOUS version's vat2 plugin and stamp itself the new version" >&2
  echo "anyway - a build it has half of, and the half that is wrong is the" >&2
  echo "one nothing on the box reads (#301)." >&2
  echo >&2
  echo "Nothing has been written. This root is still whatever it was" >&2
  echo "before this ran, which is the state it can be upgraded from once" >&2
  echo "the build is finished." >&2
  exit 1
fi

# END OF THE PREFLIGHT. Every line below this one writes into ${DESTDIR:-/}.
# test/test_install_manifest.py reads this file to keep it that way, because a
# refusal that comes after the first write is a refusal that has already cost
# something - which is the defect this block exists to answer, with a different
# component in it.

# ------------------------------------------- what this run leaves on the box
#
# WHAT THE VERSION STAMP IS A CLAIM ABOUT. A hundred lines down this script
# writes one line into /usr/share/prisma-routing/VERSION, and that line is what
# every reader of this box then believes: `show version` prints it, the boot
# self-test puts it at the top of the console report, and it is the first
# thing anybody asks for on the telephone. It is a sentence about the thirty
# odd files installed below, and exactly ONE of them can answer for itself -
# #291 gave the dataplane plugin a build stamp that `show version` compares
# against the box's own. The other thirty carry no version of any kind.
#
# So a box stamped 0.70.0 carrying 0.71.0's prisma-routing-tune reads as a 0.70.0
# box, to every command and to every operator, permanently. That is not
# hypothetical: it is the state the appliance supplied for the #290 work was
# found in, and it got there honestly - somebody copied one file onto it to
# prove a fix on a CPU they had, which is what debugging looks like. The
# defect is not that it happened. It is that nothing on the box could say that
# it had (#302).
#
# WHY THERE IS NO LIST IN THIS FILE. The obvious manifest is a list of the
# files this script installs, written down beside the lines that install them.
# That is the same defect one layer up: it would be right on the day it was
# written and wrong the first time somebody adds an install line without
# thinking about it, and it would be wrong SILENTLY, because a manifest that
# does not name a file cannot report anything at all about that file. The two
# lists would drift in the one direction nobody can see, which is precisely
# the relationship between the version stamp and the files that this exists to
# repair.
#
# So nothing is listed. `install` below is a FUNCTION: it calls the real one
# and remembers where it put things, and every line of this script that
# installs a file is in the manifest because it RAN. There is no second place
# to keep in step, and a line added tomorrow is covered by having been added.
#
# The accumulators are plain strings split on spaces, which is the one thing
# this makes fragile: a destination path with a space in it would become two
# wrong entries. No path here has ever had one, and test/test_manifest.sh
# resolves every path in a real manifest back to a real file, so one would go
# red there rather than produce a manifest that silently checks nothing.
BUILD_FILES=
YOUR_FILES=

install() {  # shadows /usr/bin/install for the rest of this script
  command install "$@"
  # -d creates a directory and copies nothing into it. A directory has no
  # contents to checksum, and its mode - which IS this script's business, and
  # argued about at length further down - is not a claim about which build the
  # box is running.
  case " $* " in *" -d "*) return 0 ;; esac
  # install's last argument is the destination, whatever else was passed.
  for _a in "$@"; do _dest=$_a; done
  _rel=${_dest#"$DESTDIR"}
  BUILD_FILES="$BUILD_FILES ${_rel#/}"
}

# THE FILES THAT ARE THE OPERATOR'S, and the one place that difference is
# stated.
#
# This script writes two kinds of file. Most of them it overwrites on every
# run, because they ARE the build. Three it writes once and then never touches
# again - tune.env, vpp-base.conf and prisma-routing.conf - because the operator
# edits them, and an installer that overwrote an edited tune.env would undo a
# tuning decision in the middle of an upgrade run at two in the morning.
#
# That difference is exactly the difference between a checksum somebody has to
# act on and one nobody may. An edited tune.env is not a corrupted tune.env,
# it is the file doing its job, and a manifest that shouted about it is a
# manifest somebody switches off - after which the file that really IS from
# another release goes unreported along with it (#302).
#
# So the guard and the class are ONE statement and cannot drift apart. There
# is no way to write a file this installer will not overwrite except through
# this function, and no way through this function without the guard.
# test/test_install_manifest.py holds that shut: a second `[ -f ... ] ||`
# anywhere in this file goes red, because a guard written by hand is a file
# the operator owns that the manifest would report as corruption.
#
# No checksum is recorded for them, deliberately. A checksum nobody is allowed
# to act on is an invitation to start acting on it, and the manifest is read
# by a boot-time check that must stay quiet on a healthy box. They are named
# and not checked, so that a reader of the manifest sees the whole install
# rather than a complete-looking list with three files missing from it.
keep() {  # <mode> <source> <destination> - a file this installer writes once
  [ -f "$3" ] || command install -m "$1" "$2" "$3"
  _rel=${3#"$DESTDIR"}
  YOUR_FILES="$YOUR_FILES ${_rel#/}"
}

say "binaries"
# prismash imports config.py from its own directory, so both have to travel
# together. Installing only prismash.py put a VTY on the box that raised
# ModuleNotFoundError on every start - the product's front door, broken on
# every installed box and every ISO, and invisible because the labs run it
# from the source tree where config.py sits next to it.
# The WHOLE module directory, not a list of files.
#
# Naming them one by one has now shipped the same defect twice: config.py was
# missing in 0.2.0 and the VTY raised ModuleNotFoundError on every installed
# box, and vppapi.py was missing the moment the binary API landed. A list that
# has to be updated whenever an import is added is a list that will be wrong
# again.
install -d -m 0755 "$DESTDIR/usr/lib/prisma-routing"
for f in "$REPO"/src/prismash/*.py; do
  install -D -m 0644 "$f" "$DESTDIR/usr/lib/prisma-routing/$(basename "$f")"
done
chmod 0755 "$DESTDIR/usr/lib/prisma-routing/prismash.py"
# The login's end of a VTY session (#448): prismash-login runs the VTY
# through it, so the terminal stays with the login and `start shell` is
# decided and run on the host.
install -D -m 0755 "$REPO/appliance/prisma-vty-broker" \
    "$DESTDIR/usr/lib/prisma-routing/prisma-vty-broker"
# And what the broker runs (#448, phase C): the VTY in the management plane,
# or here when there is none.
install -D -m 0755 "$REPO/appliance/prisma-vty-session" \
    "$DESTDIR/usr/lib/prisma-routing/prisma-vty-session"
install -D -m 0755 "$REPO/appliance/prismash-launcher" "$DESTDIR/usr/bin/prismash"
install -D -m 0755 "$REPO/appliance/prisma-routing-tune" "$DESTDIR/usr/bin/prisma-routing-tune"
install -D -m 0755 "$REPO/appliance/wait-for-vpp"     "$DESTDIR/usr/lib/prisma-routing/wait-for-vpp"
install -D -m 0755 "$REPO/appliance/apply-tuning"     "$DESTDIR/usr/lib/prisma-routing/apply-tuning"
install -D -m 0755 "$REPO/appliance/bind-dataplane-nic" "$DESTDIR/usr/lib/prisma-routing/bind-dataplane-nic"
# Run by TWO units: vpp.service's own drop-in and, for the dataplane in a
# container, prisma-routing-containers.service. It is one file because the
# rule about which group owns the API segment is one rule (#426).
install -D -m 0755 "$REPO/appliance/share-the-api-segment" "$DESTDIR/usr/lib/prisma-routing/share-the-api-segment"
# The watcher that notices a dataplane container which gave up, and the card
# it is still holding (#426). The unit path gets this from OnFailure= on
# vpp.service; a container reaches no failed state to raise it.
install -D -m 0755 "$REPO/appliance/watch-the-dataplane" "$DESTDIR/usr/lib/prisma-routing/watch-the-dataplane"
# Run by prisma-routing-containers.service before compose: it loads the
# images the medium carried and tells compose which tag to run (#426).
install -D -m 0755 "$REPO/appliance/load-the-images" "$DESTDIR/usr/lib/prisma-routing/load-the-images"
# The containers' backplane, 128.0.0.0/8 in VRF prisma-bp (#447), brought up
# by prisma-routing-backplane.service before them.
install -D -m 0755 "$REPO/appliance/prisma-backplane" "$DESTDIR/usr/lib/prisma-routing/prisma-backplane"
# The initramfs hook that leaves out what no root on this box sits behind
# (#450). The medium builds its initrd with it.
#
# AND ON A RUNNING BOX A CHANGED HOOK REBUILDS THE INITRD NOW (#468). It used
# to wait for the next kernel update, which was harmless while the hook only
# ever dropped more - and is exactly wrong for a box whose initrd was built by
# 0.132.0's hook, which dropped the NIC drivers and booted with its cards
# missing. Left for a kernel update, the upgrade that fixes it would fix
# nothing. Rebuilt only when the hook differs, so an upgrade that does not
# touch it costs nothing.
_trim="$DESTDIR/etc/initramfs-tools/hooks/prisma-trim"
_trim_was=$(cat "$_trim" 2>/dev/null || true)
install -D -m 0755 "$REPO/appliance/initramfs/prisma-trim" "$_trim"
if [ -z "$DESTDIR" ] && [ "$_trim_was" != "$(cat "$_trim")" ] \
    && command -v update-initramfs >/dev/null 2>&1; then
  say "the initramfs hook changed: rebuilding the initrd"
  update-initramfs -u >/dev/null 2>&1 \
    || say "  update-initramfs failed; the hook applies at the next kernel update"
fi
# A card added to the running box, attached through AF_XDP (#453): the script,
# and the udev rule that starts prisma-routing-hot-card@<card>.service for it.
# The template needs no enabling; udev's SYSTEMD_WANTS starts it.
install -D -m 0755 "$REPO/appliance/prisma-hot-card" "$DESTDIR/usr/lib/prisma-routing/prisma-hot-card"
install -D -m 0644 "$REPO/appliance/udev/90-prisma-hot-card.rules" \
  "$DESTDIR/lib/udev/rules.d/90-prisma-hot-card.rules"
install -D -m 0755 "$REPO/appliance/selftest"         "$DESTDIR/usr/lib/prisma-routing/selftest"
# The DHCP hook that keeps a lease from rewriting the resolvers `dns server`
# set (#437). A hook directory and not a package's conffile, so an upgrade
# can carry it.
install -D -m 0644 "$REPO/appliance/dhclient-prisma-routing-dns" \
  "$DESTDIR/etc/dhcp/dhclient-enter-hooks.d/prisma-routing-dns"
# Beside the self-test, which finds it with `dirname "$0"`, and named by the
# VTY banner by absolute path. Two readers of one answer (#248).
install -D -m 0755 "$REPO/appliance/ssh-host-key-origin" \
    "$DESTDIR/usr/lib/prisma-routing/ssh-host-key-origin"
install -D -m 0755 "$REPO/appliance/prisma-routing-install" "$DESTDIR/usr/sbin/prisma-routing-install"
install -D -m 0755 "$REPO/appliance/install-from-cmdline" "$DESTDIR/usr/lib/prisma-routing/install-from-cmdline"
install -D -m 0755 "$REPO/tools/ipfix_collector.py"   "$DESTDIR/usr/lib/prisma-routing/ipfix-collector"
install -D -m 0755 "$REPO/appliance/prisma-routing-snmp-agent" \
    "$DESTDIR/usr/lib/prisma-routing/snmp-agent"

say "dataplane plugin"
# Both paths were resolved by the preflight, which refused if either was
# missing, so these two are a copy and nothing else. The vat2 plugin used to be
# guarded here with `[ -n "$VSO" ]` and a warning on stderr, and that guard is
# what left a box running the previous version's vat2 plugin under a new
# dataplane (#301). There is nothing left to decide at this point in the file.
install -D -m 0644 "$SO" "$DESTDIR$PLUGIN_DIR/bpa_plugin.so"
install -D -m 0644 "$VSO" "$DESTDIR$VAT2_DIR/bpa_test_plugin_bpa_plugin.so"

# THE COMPOSE FILE AND THE UNIT (#396), where the box looks for them rather
# than where this tree keeps them. Installed on every box, enabled on none:
# prisma-routing-containers.service is shipped and not started, and says at
# its own top why.
#
# THE IMAGES, from the tree this runs out of, when it has them (#427).
# tools/build-images.sh writes prisma-routing-images.tar.gz at the top of a
# source tree, build-upgrade.sh packs it at the same place in an artifact,
# and build-iso.sh puts it there in the chroot's /src - so a medium, an
# upgrade and a source install are one line here rather than three routes.
# Through install(), so the manifest names it and the boot self-test says so
# when a box's images are not this build's.
#
# UNTIL 0.125.0 THEY WERE NOT INSTALLED HERE. They rode the medium alone, and
# an upgrade carried none: an upgraded box on the systemd units could never
# move to the containers, and one already on them kept starting the previous
# release's dataplane image, with the previous release's plugin in it, under
# this release's code. The maintainer decided on 2026-09-24 that the artifact
# carries them, at about 120 MB instead of 5.
#
# A tree without them installs nothing here and leaves what the box has:
# the default below then follows what is on the box, as it always has.
if [ -f "$REPO/prisma-routing-images.tar.gz" ]; then
  say "the container images"
  install -D -m 0644 "$REPO/prisma-routing-images.tar.gz" \
    "$DESTDIR/usr/lib/prisma-routing/images.tar.gz"
fi
say "the container stack, shipped and not started"
install -D -m 0644 "$REPO/appliance/compose.yaml" \
  "$DESTDIR/etc/prisma-routing/compose.yaml"

say "systemd"
for u in prisma-routing.service prisma-routing-tune.service prisma-routing-selftest.service \
         prisma-routing-installer.service prisma-routing-confirm.service \
         prisma-routing-confirm.timer prisma-routing-containers.service \
         prisma-routing-card-watch.service prisma-routing-card-watch.timer \
         prisma-routing-backplane.service prisma-routing-hot-card@.service \
         prisma-routing-doors.service; do
  install -D -m 0644 "$REPO/appliance/systemd/$u" "$DESTDIR/lib/systemd/system/$u"
done
# A drop-in on VPP's own unit, not a unit of ours: it has to run every time the
# dataplane starts, including a restart nobody ordered, and ordering a separate
# unit after vpp.service would put it in the wrong place on exactly that path.
install -D -m 0644 "$REPO/appliance/systemd/vpp-api-access.conf" \
  "$DESTDIR/etc/systemd/system/vpp.service.d/prisma-routing-api-access.conf"
# Same reason it is a drop-in and not a unit of ours, plus one more: a
# protection that only applies to the VPP this boot started is no protection.
# vpp.service carries Restart=always, and the restart after an OOM kill is
# exactly the moment the box must not be choosable again.
install -D -m 0644 "$REPO/appliance/systemd/vpp-oom-protect.conf" \
  "$DESTDIR/etc/systemd/system/vpp.service.d/prisma-routing-oom-protect.conf"
# The dataplane out of system.slice, which the control-plane fence holds
# (#466). The container path does the same in compose.yaml.
install -D -m 0644 "$REPO/appliance/systemd/vpp-slice.conf" \
  "$DESTDIR/etc/systemd/system/vpp.service.d/prisma-routing-slice.conf"
# And the third, for the same reason again: the retention log has to be closed
# by the dataplane that is stopping, on every stop, including the restart after
# an upgrade that no unit of ours is ordered around.
# The card comes back when the dataplane will not start (#379). Two files:
# the drop-in that arms it on vpp.service, and the unit it names.
install -D -m 0644 "$REPO/appliance/systemd/vpp-give-card-back.conf" \
  "$DESTDIR/etc/systemd/system/vpp.service.d/prisma-routing-give-card-back.conf"
install -D -m 0644 "$REPO/appliance/systemd/prisma-routing-give-card-back.service" \
  "$DESTDIR/lib/systemd/system/prisma-routing-give-card-back.service"
install -D -m 0755 "$REPO/appliance/give-the-card-back" \
  "$DESTDIR/usr/lib/prisma-routing/give-the-card-back"
install -D -m 0644 "$REPO/appliance/systemd/vpp-stop-drain.conf" \
  "$DESTDIR/etc/systemd/system/vpp.service.d/prisma-routing-stop-drain.conf"

say "login shell"
install -D -m 0755 "$REPO/appliance/prismash-login" "$DESTDIR/usr/bin/prismash-login"
# Opt-in on purpose. On an existing Debian the machine may have other jobs, and
# silently replacing root's login shell is hostile - the operator would discover
# it at the worst moment, on the next login. The ISO sets PRISMA_VTY_LOGIN=1.
if [ "${PRISMA_VTY_LOGIN:-0}" = 1 ]; then
  grep -qx /usr/bin/prismash-login "$DESTDIR/etc/shells" 2>/dev/null || \
    echo /usr/bin/prismash-login >> "$DESTDIR/etc/shells"
  if [ -n "$DESTDIR" ]; then
    sed -i 's|^root:\([^:]*\):\([^:]*\):\([^:]*\):\([^:]*\):\([^:]*\):.*|root:\1:\2:\3:\4:\5:/usr/bin/prismash-login|' \
      "$DESTDIR/etc/passwd"
  else
    chsh -s /usr/bin/prismash-login root
  fi
  say "  root logs into the VTY; 'start shell' escapes, 'prismash=off' bypasses"
else
  say "  not changing any login shell (set PRISMA_VTY_LOGIN=1 to land in the VTY)"
fi

say "version stamp"
# The box has to be able to state its own version: the tag identifies the
# release only to whoever has the repository open, and that is not the person
# on the phone. And if the tree had uncommitted changes, the file says so - an
# ISO that declares itself v0.4.1 without being it sends the whole
# investigation the wrong way.
if [ -n "${PRISMA_VERSION_STAMP:-}" ]; then
  stamp=$PRISMA_VERSION_STAMP
else
  stamp=$(sh "$REPO/tools/version-stamp.sh" "$REPO")
fi
install -d -m 0755 "$DESTDIR/usr/share/prisma-routing"
printf '%s\n' "$stamp" > "$DESTDIR/usr/share/prisma-routing/VERSION"
chmod 0644 "$DESTDIR/usr/share/prisma-routing/VERSION"

# What the operating system says it is (#438): os-release, the login banners,
# the motd and the boot menu's name, from the stamp just written. Here and
# not only in build-iso.sh, so a box that is upgraded says the new version
# too. The script is installed as well, so the box can say it again after
# anything that rewrites those files.
say "the system's name"
install -D -m 0755 "$REPO/appliance/prisma-identity" \
    "$DESTDIR/usr/lib/prisma-routing/prisma-identity"
sh "$REPO/appliance/prisma-identity" "${DESTDIR:-/}" "$stamp"
install -D -m 0644 "$REPO/appliance/etc/sshd-identity.conf" \
    "$DESTDIR/etc/ssh/sshd_config.d/00-identity.conf"

say "kernel knobs"
install -D -m 0644 "$REPO/appliance/etc/sysctl.conf" \
    "$DESTDIR/etc/sysctl.d/99-prisma-routing.conf"

say "configuration"
# Group-readable, never world-readable. Since accounts became part of the
# configuration (#45) this directory holds password hashes - in
# prisma-routing.conf and in every rollback revision under it - and a
# world-readable copy hands every local account an offline cracking target.
# The group is a role group, so seeing them requires being trusted to change
# the configuration in the first place (#35).
# The two roles the VTY understands. Created here rather than documented as a
# step somebody must remember: an authorization model whose groups do not exist
# is one where every account falls back to read-only and the operator concludes
# the box is broken. Empty until somebody is added to them; root always has
# both.
for g in prisma-admin prisma-cgnat; do
  if [ -z "${DESTDIR:-}" ]; then
    getent group "$g" >/dev/null 2>&1 || groupadd --system "$g" 2>/dev/null || true
  fi
done

# 0770 and group-owned, not 0700: an account that may configure has to write
# the rollback store and the saved configuration, and both hold the whole
# configuration - password hashes included. Granting the capability grants
# sight of them, which is why the group is a role group and not "users".
#
# prisma-cgnat owns it and prisma-admin is ADDITIVE on top: an administrator is a
# member of both. Owning it with prisma-admin instead would leave a CGNAT
# operator unable to record a rollback revision for the change it just made.
#
# THIS FILE IS THE ONLY PLACE THESE MODES ARE STATED. The VTY does not restate
# them and does not narrow them: it chmod'd 0700/0600 on every save and every
# commit, so the first one by anybody - root applying the configuration at
# boot - took the group's access away and every non-root operation afterwards
# failed (#188). It cannot be the other owner either, because half of this is
# ownership: a mode without the group grants nobody anything, and a non-root
# operator cannot chgrp a directory it does not own.
#
# 2770 rather than 0770. The group is not enough on its own: a revision root
# writes at boot would be group root, and one operator's revision would be
# unreadable to the next. setgid makes the kernel put prisma-cgnat on everything
# created in here - including the rollback directory, since a directory made
# inside a setgid directory inherits the bit - so "rollback 1" works whoever
# made the revision.
#
# install -d re-states it on an upgrade as well as a first install, which is
# what repairs a box the old VTY already narrowed.
install -d -m 2770 "$DESTDIR/etc/prisma-routing"
install -d -m 2770 "$DESTDIR/etc/prisma-routing/rollback"
if [ -z "${DESTDIR:-}" ] && getent group prisma-cgnat >/dev/null 2>&1; then
  chgrp prisma-cgnat /etc/prisma-routing /etc/prisma-routing/rollback 2>/dev/null || true
fi
# The configuration trail. The plugin opens it with O_CREAT and cannot create
# the directory, so without this the trail exists in every lab - where the lab
# happens to make the directory - and on no installed box. In deterministic
# mode that trail is the record of which pool definition was in force at a
# given instant, which is what makes an answer to a court order defensible.
# 0750: it names subscribers and their public addresses.
install -d -m 0750 "$DESTDIR/var/log/prisma-routing"
# AND THE DATAPLANE'S OWN LOG DIRECTORY, which was on no installed box.
# /etc/prisma-routing/vpp-base.conf says `log /var/log/vpp/vpp.log`, VPP
# cannot create the directory, and so every dataplane this product has ever
# shipped began with
#
#   unix_config:445: couldn't open log '/var/log/vpp/vpp.log'
#
# and then threw away everything it would have written there. That line is
# in the console transcript of every failing box in this repository's
# history and was read as noise. It is not: it is the reason a dataplane
# that will not start has only ever printed two lines about why, and the
# reason #428 took six boots of an installed box to diagnose.
#
# 0755 and root: it is a log an operator reads, and the dataplane writes it
# as root on the unit path and inside the container on the other.
install -d -m 0755 "$DESTDIR/var/log/vpp"
keep 0644 "$REPO/appliance/etc/tune.env" "$DESTDIR/etc/prisma-routing/tune.env"
keep 0644 "$REPO/appliance/etc/vpp-base.conf" \
     "$DESTDIR/etc/prisma-routing/vpp-base.conf"
# The empty configuration is a FILE in the tree rather than a printf here, and
# it moved for the manifest's sake: keep() is the one place a file the
# operator owns may be written, so the thing it writes has to be a file. It is
# read back by test/test_prismash_config.py, which loads it through the VTY's
# own parser - "!" was not a comment there once, and prisma-routing.service
# FAILED on the first boot of every install for it (#94).
keep 0660 "$REPO/appliance/etc/prisma-routing.conf" \
     "$DESTDIR/etc/prisma-routing/prisma-routing.conf"
# 0660 for the same reason as the directory: an account that may configure
# saves it, and it holds hashes. Never world-readable.
chmod 0660 "$DESTDIR/etc/prisma-routing/prisma-routing.conf"
if [ -z "${DESTDIR:-}" ] && getent group prisma-cgnat >/dev/null 2>&1; then
  chgrp prisma-cgnat "$DESTDIR/etc/prisma-routing/prisma-routing.conf" 2>/dev/null || true
fi

# ------------------------------------------------------------ routing daemons
#
# The Debian package ships /etc/frr/daemons with bgpd=no, ospfd=no and
# isisd=no. frrinit.sh always starts zebra, mgmtd and staticd, so a box
# installed without this runs the dataplane, the VTY, and static routes - and
# none of the routing protocols.
#
# The VTY offers all four. "?" lists them, TAB completes them, "commit"
# accepts them, and the daemon is not there. This project's own documentation
# claims BGP, OSPF and IS-IS (#99).
#
# ospf6d is the fourth, and it was missing from this list for one release
# longer than the rest (#187). The VTY took `router ospf6` as a word it did not
# model and handed it to FRR, so it committed; nothing here enabled the daemon
# that serves it, and nothing on the box said so until the commit check learned
# to name the daemon. Measured before enabling it rather than after: an OSPFv3
# adjacency over a linux_cp pair reaches Full and its prefix reaches the VPP
# FIB, with no punt beyond the one OSPFv2 has always used - so this is a
# protocol the box can run, and leaving it offered and disabled was the only
# thing wrong with it.
#
# Enabled here rather than left to the distribution default, because inheriting
# a default for something the product claims to support is exactly how it went
# unnoticed: lab/routing.sh starts these daemons by hand, so the lab proved a
# control plane the shipped box did not have.
say "routing daemons"
for d in bgpd ospfd ospf6d isisd; do
  if grep -qE "^$d=" "$DESTDIR/etc/frr/daemons" 2>/dev/null; then
    sed -i "s/^$d=no$/$d=yes/" "$DESTDIR/etc/frr/daemons"
  else
    echo "$d=yes" >> "$DESTDIR/etc/frr/daemons"
  fi
done
grep -E "^(bgpd|ospfd|ospf6d|isisd)=" "$DESTDIR/etc/frr/daemons" | sed 's/^/  /'

# The one file in /etc/frr an administrator has to be able to write.
#
# Applying a routing change ends with frr-reload asking vtysh to persist the
# result, and with integrated-vtysh-config that is /etc/frr/frr.conf. The
# package ships it 0640 frr:frr, so a non-root administrator got "failed to
# open configuration file: Permission denied" and the whole commit was rolled
# back - the account could check a routing change and not make one (#186).
#
# The FILE and not the directory, measured both ways on FRR 10.7.1: with the
# file group-writable vtysh rewrites it in place and it stays frr:frr, which
# works for every account and for the next one. With the DIRECTORY group-
# writable instead, vtysh renames it aside and creates a new one owned by
# whoever committed - after which the next administrator cannot write it at
# all, and FRR reads its own configuration from a file an operator owns.
#
# Group frr and not a role group: user frr has to read this file at boot, and
# the accounts the VTY creates are in group frr already - they run vtysh, which
# is total control of the routing daemons whatever this mode says. What it adds
# is the ability to persist a configuration they can already make.
#
# The cost, stated because it is real: this is a package conffile, so an frr
# upgrade that reinstalls it puts 0640 back and non-root commits start failing
# again until this installer is re-run. It fails loudly - the commit is refused
# and rolled back - rather than quietly.
if [ -f "$DESTDIR/etc/frr/frr.conf" ]; then
  chmod 0660 "$DESTDIR/etc/frr/frr.conf"
fi

# RPKI is a MODULE, and a module that is installed and never loaded is not
# there. `bgpd --version` reports `--enable-rpki` either way, which is how this
# looks like support and is not: without the module every RPKI command answers
# "bgpd is not running", because bgpd refuses to start when told to load a
# module it cannot find.
#
# Measured: the module ships in frr-rpki-rtrlib, from the same repository and
# at the same version as frr itself, so this adds no new source and no skew.
say "RPKI module"
if grep -q "^bgpd_options=" "$DESTDIR/etc/frr/daemons" 2>/dev/null; then
  if grep -q "^bgpd_options=.*-M rpki" "$DESTDIR/etc/frr/daemons"; then
    echo "  bgpd already loads it"
  else
    sed -i 's/^\(bgpd_options="[^"]*\)"/\1 -M rpki"/' \
      "$DESTDIR/etc/frr/daemons"
  fi
else
  echo 'bgpd_options="  -A 127.0.0.1 -M rpki"' >> "$DESTDIR/etc/frr/daemons"
fi
grep -E "^bgpd_options=" "$DESTDIR/etc/frr/daemons" | sed 's/^/  /'

say "documentation"
for d in architecture routing logging; do
  install -D -m 0644 "$REPO/docs/$d.md" "$DESTDIR/usr/share/doc/prisma-routing/$d.md"
done

# WILL THIS BOX HAVE A DATAPLANE TO RUN? (#426)
#
# THE UPGRADE ARTIFACT CARRIES NO IMAGES. They ride the squashfs, so a box
# INSTALLED from a medium has them and a box UPGRADED from an older release
# has whatever its own medium carried - which for anything before 0.113.0 is
# nothing at all. Enabling the container stack there hands the box a
# dataplane it cannot start: the unit fails, the card is given back by the
# watcher two minutes later, and what an operator sees is a box that routes
# and does not forward.
#
# That is not hypothetical. It is what a maintainer's box did on 0.117.0,
# and the lab could not see it because a lab always installs from a medium
# that carries them.
#
# So the default follows the images: a box that has them runs the containers,
# a box that does not keeps vpp.service and frr.service and is TOLD why. It
# is the same rule the rest of this file follows - the box is left in a state
# it can actually boot into - and it costs an upgraded box nothing it had.
container_default() {  # <root> - does this root have images to run?
  [ -f "${1}/usr/lib/prisma-routing/images.tar.gz" ]
}

IMAGES_TARBALL=$DESTDIR/usr/lib/prisma-routing/images.tar.gz
if container_default "$DESTDIR"; then
  CONTAINER_DEFAULT=1
else
  CONTAINER_DEFAULT=0
fi
# THE LOADER IS PART OF THE DATAPLANE THIS BOX RUNS (#459). On the unit path
# it is PartOf=vpp.service as well, through a drop-in; on the containers it
# must not be, because the containers unit Conflicts=vpp.service and PartOf
# both made `systemctl restart prisma-routing-containers` a transaction
# systemd refuses. Removed as well as written, so a box that moves to the
# containers loses it.
UNITPATH_DROPIN=$DESTDIR/etc/systemd/system/prisma-routing.service.d/prisma-routing-unit-path.conf
if [ "$CONTAINER_DEFAULT" = 1 ]; then
  rm -f "$UNITPATH_DROPIN"
else
  install -D -m 0644 "$REPO/appliance/systemd/prisma-routing-unit-path.conf" \
    "$UNITPATH_DROPIN"
fi

# In a chroot or container there is no systemd running; installing is still
# valid, only enabling services is not. That is the normal case when the ISO
# is being assembled.
if [ -z "$DESTDIR" ] && systemctl --version >/dev/null 2>&1 \
   && [ -d /run/systemd/system ]; then
  LIVE_BOX=1
  say "enabling services"
  systemctl daemon-reload
  # The hot-card rule (#453) is read by a udev that is already running.
  udevadm control --reload >/dev/null 2>&1 || true
  systemctl enable prisma-routing-tune.service prisma-routing.service \
    prisma-routing-selftest.service prisma-routing-installer.service \
    prisma-routing-confirm.timer prisma-routing-backplane.service \
    prisma-routing-doors.service
  # The doors (#448) run the code this install just put down: restarted, not
  # only started, or an upgrade leaves the old daemon answering.
  systemctl restart prisma-routing-doors.service >/dev/null 2>&1 || true
  if [ "$CONTAINER_DEFAULT" = 1 ]; then
    systemctl enable prisma-routing-containers.service \
      prisma-routing-card-watch.timer
    # AND THE TWO UNITS THE CONTAINERS REPLACE (#426). They are enabled by
    # their own Debian packages, and prisma-routing-containers.service
    # Conflicts with both - so a box with all three enabled asks systemd to
    # decide at every boot, and nothing in this product says which wins.
    # Disabled and not masked: an operator who wants the units back types
    # `systemctl enable --now vpp.service frr.service` and gets them.
    systemctl disable vpp.service frr.service >/dev/null 2>&1 || true
  else
    say "no container images on this box, so it keeps vpp.service and frr.service"
    say "  the file a medium carries and an upgrade does not:"
    say "  $IMAGES_TARBALL"
    say "  This box goes on running the dataplane and the routing daemons as"
    say "  systemd units, which is what it was doing before this upgrade."
    say "  To move it to the containers, install from a medium of this"
    say "  release - or put that file there and enable the unit by hand."
  fi
else
  LIVE_BOX=0
  # offline enablement: creates the link systemctl enable would create
  install -d -m 0755 "$DESTDIR/etc/systemd/system/multi-user.target.wants"
  for u in prisma-routing-tune.service prisma-routing.service \
           prisma-routing-selftest.service prisma-routing-installer.service \
           prisma-routing-backplane.service prisma-routing-doors.service; do
    ln -sf "/lib/systemd/system/$u" \
      "$DESTDIR/etc/systemd/system/multi-user.target.wants/$u"
  done
  if [ "$CONTAINER_DEFAULT" = 1 ]; then
    ln -sf "/lib/systemd/system/prisma-routing-containers.service" \
      "$DESTDIR/etc/systemd/system/multi-user.target.wants/prisma-routing-containers.service"
  else
    say "no container images in this root, so it keeps the units"
    rm -f "$DESTDIR/etc/systemd/system/multi-user.target.wants/prisma-routing-containers.service"
  fi
  # AND THE TWO THE CONTAINERS REPLACE, unlinked from what enabled them -
  # but only on a root that HAS the images. Offline there is no systemctl to
  # ask, so the links Debian's own package made are removed by name;
  # `systemctl disable` would do exactly this.
  if [ "$CONTAINER_DEFAULT" = 1 ]; then
    rm -f "$DESTDIR/etc/systemd/system/multi-user.target.wants/vpp.service" \
          "$DESTDIR/etc/systemd/system/multi-user.target.wants/frr.service"
  fi
  # The timer is wanted by timers.target and not by multi-user.target, so it
  # needs its own directory. Linking it into the wrong one installs a timer
  # that exists, parses, and never elapses - and the feature it enforces is
  # one nobody notices working, so nobody would notice that either.
  install -d -m 0755 "$DESTDIR/etc/systemd/system/timers.target.wants"
  ln -sf "/lib/systemd/system/prisma-routing-confirm.timer" \
    "$DESTDIR/etc/systemd/system/timers.target.wants/prisma-routing-confirm.timer"
  # The card watcher's timer, with the unit it belongs to: on a box running
  # the containers, nothing else notices a dataplane that Docker gave up on,
  # and the card stays bound to DPDK with no interface at all (#379).
  if [ "$CONTAINER_DEFAULT" = 1 ]; then
    ln -sf "/lib/systemd/system/prisma-routing-card-watch.timer" \
      "$DESTDIR/etc/systemd/system/timers.target.wants/prisma-routing-card-watch.timer"
  else
    rm -f "$DESTDIR/etc/systemd/system/timers.target.wants/prisma-routing-card-watch.timer"
  fi
  say "installed into ${DESTDIR:-/} (services enabled offline)"
fi

say "manifest"
# WRITTEN HERE, BELOW EVERYTHING, because it is a statement about what this
# run did and there is no honest place for it until the run has done it. An
# install line added below this block would put a file on the box that the
# manifest does not name, and a file the manifest does not name is a file
# nothing will ever check - the silent half of the two-lists problem this
# whole block exists to avoid. test/test_install_manifest.py refuses a write
# below here for that reason, the same way it refuses one above the preflight.
#
# NOT A SECURITY MEASURE, and saying so is cheaper than letting somebody
# assume it. Whoever can replace a file on this box can replace the line in
# this manifest that describes it. What it answers is the accident and the
# half-finished job - a file copied on to prove a fix and left behind, an
# install that stopped in the middle, an upgrade that ran over some of the box
# and not the rest - which is what #302 measured and what actually happens.
#
# WHY sha256 AND NOT A TIMESTAMP OR A SIZE. Both of those are properties of
# the copy rather than of the contents, and both are what the reader of #302
# had to work with: mtimes told that prisma-routing-tune was written at 04:06 and
# everything else at 02:38, which is evidence and not an answer, because the
# same reading appears on a box whose files are all correct and were installed
# in two passes.
#
# WHY IT IS A sha256sum FILE AND NOTHING CLEVERER. `sha256sum -c` reads this
# format, it is one process over the whole list, and it skips the '#' lines -
# so the box needs no parser of its own to check itself, and the reader who
# opens the file gets its own explanation at the top. Paths are relative to /
# so that a check can be run against a root that is not the running one, which
# is how test/test_manifest.sh drives it and how an ISO build could.
#
# The version stamp itself is not in here. It is the claim, not one of the
# files the claim is about, and its checksum would be a checksum of the very
# line being questioned. Neither is this file, which cannot contain its own
# checksum.
{
  echo "# What appliance/install.sh put on this box when it stamped it"
  echo "# $stamp."
  echo "#"
  echo "# Paths are relative to /. A line with a checksum is a file OF that"
  echo "# build: nothing on a working box changes it, and the boot self-test"
  echo "# reports it when something has. A '# yours' line is a file this"
  echo "# installer creates once and then never overwrites, so its contents"
  echo "# are the operator's and nothing here has an opinion about them."
  echo "#"
  echo "# sha256sum -c reads this file as it stands."
  for _f in $YOUR_FILES; do echo "# yours: $_f"; done
  # Unquoted on purpose: the split into words IS the list. cd first so the
  # names sha256sum prints are the ones that go in the file.
  #
  # Guarded because sha256sum with no operands reads STDIN. An installer whose
  # wrapper had stopped recording would then sit there with the script's own
  # stdin open instead of producing an empty list, and a suite that hangs is
  # one somebody kills rather than one that goes red.
  if [ -n "$BUILD_FILES" ]; then
    # shellcheck disable=SC2086
    ( cd "${DESTDIR:-/}" && sha256sum $BUILD_FILES )
  fi
} > "$DESTDIR/usr/share/prisma-routing/MANIFEST"
chmod 0644 "$DESTDIR/usr/share/prisma-routing/MANIFEST"
echo "  $(grep -c '^[^#]' "$DESTDIR/usr/share/prisma-routing/MANIFEST") file(s) of this build, in /usr/share/prisma-routing/MANIFEST"

# THE CLOSING WORDS. Nothing below this line installs anything.
#
# WHY ANY OF THIS IS SAID. Everything above is a file copy. None of it
# restarts anything, and that default is deliberate and is not changing:
# restarting the dataplane drops every session and every port block the box is
# carrying, which is not a decision an installer gets to take on behalf of an
# operator who ran it at two in the morning. What was wrong was that the
# script took that decision silently and then closed with a first install's
# sentence, so the reader walked away from a box whose control plane is the
# new build and whose translating dataplane is the old one (#291).
#
# WHY IT IS GATED ON THE STAMP AND NOT ON ANYTHING ELSE. A stamp that was
# there before this run is the one piece of evidence on the box that an older
# build was installed, and an older build that was installed is an older build
# something may still be running. A debootstrap has no stamp, so the ISO build
# reaches this and prints nothing, which is why there is no second condition
# here keeping it quiet: the honest test and the ISO test are the same test.
#
# WHY THE VERSIONS ARE PRINTED WHEREVER THERE IS A STAMP AND THE SERVICES ARE
# NOT. The two halves answer different questions. "Which build replaced which"
# is true of any root this script wrote into, including a $DESTDIR fakeroot.
# "What is still running the old one" is only true of the machine this script
# is running ON, and a fakeroot install that talked about the host's
# vpp.service would be describing a box it did not touch.
#
# WHY IT IS ALL DOWN HERE, BELOW THE BRANCH THAT ENABLES THE SERVICES, RATHER
# THAN ONE MESSAGE IN EACH ARM OF IT. Because otherwise nothing could ever
# test it. Three of these four screens are only reachable on a root that IS
# the running machine, with systemd up - and a unit suite that installed into
# / to read a sentence is a unit suite nobody can run twice, while every
# container this product's labs run in has no systemd at all. Written as one
# section at the end of the file, reading three variables set above and
# touching nothing, it can be run on its own against a box state this machine
# cannot be put into. test/test_install_says.sh does exactly that, from the
# marker line above, and it is the only reason the paragraph about vpp.service
# has a red light behind it.
#
# So: PREV_STAMP, stamp and LIVE_BOX in, words out. Nothing else, and nothing
# after it.
if [ -z "$PREV_STAMP" ]; then
  # A FIRST INSTALL'S sentence, and now only on a first install. It tells the
  # operator to do two things that have already been done on a box that is
  # being upgraded: tune.env is theirs and this script does not touch it once
  # it exists, and the reboot it asks for is about isolcpus and hugepages,
  # which took effect the first time. Printed on an upgrade it reads as
  # "nothing to do here", which is the opposite of true.
  #
  # Still only on a live box, because on any other root there is nothing to
  # reboot and no tune.env of the operator's to adjust yet. That is where this
  # sentence has always been printed and this change does not move it.
  if [ "$LIVE_BOX" = 1 ]; then
    echo
    echo "Installed. Adjust /etc/prisma-routing/tune.env and reboot so that"
    echo "isolcpus and hugepages take effect."
  fi
else
  echo
  echo "Upgraded. Every file is in place, and nothing was restarted."
  echo
  echo "  the version stamp was  $PREV_STAMP"
  echo "  and now says           $stamp"
fi

if [ -n "$PREV_STAMP" ] && [ "$LIVE_BOX" = 1 ]; then
  echo
  echo "A process does not re-read the file it was loaded from, so whatever"
  echo "was already up when this started is still the OLD build:"
  echo
  # ASKED, not assumed, and each answer gets its own paragraph rather than one
  # paragraph with a word swapped in the middle of it.
  #
  # A box being upgraded normally has both of these up, but one being repaired
  # or one that has never been booted into service may have neither - and the
  # sentence that is right for a running dataplane is WRONG for a stopped one
  # in the direction that matters. "It is what translates every packet on this
  # box" printed over a stopped vpp.service sends an operator to schedule a
  # maintenance window for a restart that would cost them nothing, and the
  # real answer, that this box is already going to come up on the new plugin,
  # is the one they needed.
  if systemctl is-active --quiet vpp.service 2>/dev/null; then
    echo "  vpp.service is running, and it is still holding the"
    echo "  bpa_plugin.so that this install has just replaced underneath it."
    echo "  For as long as it stays up it is what translates every packet on"
    echo "  this box, and it is the old build."
  else
    echo "  vpp.service is NOT running, so nothing on this box is"
    echo "  translating anything. Whenever it is next started it loads the"
    echo "  bpa_plugin.so this install has just written, which is the new"
    echo "  build, and there is no split to clear."
  fi
  echo
  # RemainAfterExit=yes is why the oneshot can be asked this at all: it reports
  # active for as long as the configuration it applied is the one in force.
  if systemctl is-active --quiet prisma-routing.service 2>/dev/null; then
    echo "  prisma-routing.service is active. It is a Type=oneshot: it applied"
    echo "  /etc/prisma-routing/prisma-routing.conf at the last boot, with the VTY"
    echo "  that was installed then, and exited."
  else
    echo "  prisma-routing.service is NOT active, so nothing has applied"
    echo "  /etc/prisma-routing/prisma-routing.conf to the dataplane. It is a"
    echo "  Type=oneshot and it runs at boot; it has not run on this one."
  fi
  echo
  # Pointed at rather than repeated. `show version` prints the dataplane's
  # build beside the box's own and explains the split in its own words
  # (#291 item 2), and a refusal that comes back from a dataplane older than
  # the VTY says the upgrade is why (#293). A second copy of that paragraph
  # here is a second copy to keep in step with the first.
  echo "'show version' on this box reads both builds and compares them. Take"
  echo "that reading before deciding anything below."
  echo
  echo "Clearing the split is the operator's call, and this installer does"
  echo "not take it, because neither restart is free:"
  echo
  # ON A BOX WHOSE DATAPLANE IS A CONTAINER the unit below is not the one
  # running it, and this paragraph named only it: measured on a KVM guest on
  # the containers, the upgrade ended telling the operator to restart
  # vpp.service (#453). The VTY's own command restarts the tuner and the
  # containers, which is what that box needs, and says what it costs first.
  if [ "$CONTAINER_DEFAULT" = 1 ]; then
  echo "  system dataplane restart     (typed in the VTY)"
  echo "      re-runs the tuner, starts the dataplane again on the new images"
  echo "      and plugin, and DROPS EVERY SESSION AND EVERY PORT BLOCK on this"
  echo "      box. The SAVED configuration is replayed when it is back, so"
  echo "      'save' first anything committed since. It says all of this"
  echo "      before it acts."
  echo
  fi
  echo "  systemctl restart vpp.service"
  echo "      picks up the new plugin, and DROPS EVERY SESSION AND EVERY PORT"
  echo "      BLOCK on this box: every subscriber behind it is translated"
  echo "      from nothing again. prisma-routing.service is PartOf=vpp.service,"
  echo "      so it is stopped and started with it and re-applies the"
  echo "      configuration to the new dataplane."
  echo
  echo "  systemctl restart prisma-routing.service"
  echo "      re-applies /etc/prisma-routing/prisma-routing.conf through the new"
  echo "      VTY and exits. It drops no session. It also does not change"
  echo "      which plugin the dataplane is running, so on its own the split"
  echo "      above stays exactly where it is."
  echo
  echo "  a reboot does both, and takes any /etc/prisma-routing/tune.env change"
  echo "  with it."
  echo
  # AND THE ONE THING NEITHER RESTART DOES, which is the half this paragraph
  # was missing. /etc/vpp/startup.conf is written by prisma-routing-tune.service,
  # a Type=oneshot with RemainAfterExit=yes that runs at BOOT. Restarting
  # vpp.service does not re-run it, so a box that upgrades onto a version
  # whose tuner writes something new keeps the file the old tuner wrote and
  # the new dataplane starts on it.
  #
  # Not hypothetical, and it is why this is here: 0.75.2 writes the card's own
  # maximum MTU into that file, because a card that cannot carry VPP's default
  # of 9000 is refused by the driver and the port comes up in `error` state
  # forwarding nothing. An operator who upgraded for that fix and restarted
  # vpp.service alone would get the same dead port and no reason for it.
  echo "  NEITHER restart rewrites /etc/vpp/startup.conf. It is generated by"
  echo "  prisma-routing-tune.service, which runs at BOOT, so a dataplane"
  echo "  restarted without one starts on the file the PREVIOUS version"
  echo "  generated. If you upgraded for something the tuner decides - what"
  echo "  the card's MTU is, how many queues it gets, how the CPUs are"
  echo "  pinned - reboot, or run it yourself first:"
  echo
  echo "  systemctl restart prisma-routing-tune.service"
  echo "  systemctl restart vpp.service"
fi
