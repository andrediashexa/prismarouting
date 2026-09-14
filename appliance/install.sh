#!/bin/sh
# Installs HexaRouting onto an existing Debian 12.
#
# Does not build an ISO: for a fresh install see build-iso.sh. This path is
# for updating a box in production without reinstalling it.
#
# WHO HAS THAT PATH: somebody with this source tree and a build of it. Every
# install line below copies out of $REPO, and the plugin comes from .build-vpp
# or from a build container's /vpp_plugins, so this script cannot run where
# those are not. A box installed from a published medium has none of them -
# measured on one: /usr/lib/hexarouting holds the runtime files and no
# install.sh, there is no repository and there is no toolchain (#295). The
# callers this really serves are the release gate and lab/upgrade.sh, which is
# where the path is measured rather than asserted.
#
# So an operator holding an ISO does not have it. Their only route to a new
# version today is a reinstall from the new medium, which erases the disk and
# takes /var/log/hexarouting with it - a year of connection records and the
# configuration trail that makes a past answer defensible. The wiki page on
# taking a new version states that procedure and what it costs. Nothing here
# closes the gap; #295 is where the choice of what would is being made.
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
DESTDIR=${DESTDIR:-}
PLUGIN_DIR=${PLUGIN_DIR:-/usr/lib/x86_64-linux-gnu/vpp_plugins}

say() { echo "==> $*"; }

# WHAT WAS HERE BEFORE, read before a single byte of it is overwritten.
#
# This is the only moment the question can be asked. Forty lines down this
# script writes /usr/share/hexarouting/VERSION with the build it is putting on
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
PREV_STAMP=$(cat "$DESTDIR/usr/share/hexarouting/VERSION" 2>/dev/null || true)

# EVERY COMPONENT THIS BUILD IS MADE OF, RESOLVED BEFORE ANY OF IT IS WRITTEN.
#
# THE TWO PLUGINS ARE ONE BUILD PRODUCT. tools/build.sh compiles them in one
# ninja run and copies both out of the container in the same block, and
# lab/upgrade.sh's build_plugin does the same with `|| return 1` on each. So a
# tree that has bpa_plugin.so and no vat2 plugin beside it is not a tree
# somebody chose to build that way, it is a build that did not finish. And
# there is no box that wants one and not the other: the vat2 plugin is what
# lets anything outside VPP speak this plugin's binary API, which is how hexash
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
# down, past the whole of src/hexash: a tree with no dataplane plugin left a
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
# it, permanently, and #295 says those boxes have no install.sh to re-run.
# appliance/build-iso.sh already refuses both plugins before it spends ten
# minutes on a debootstrap, and asserts afterwards that the vat2 plugin reached
# the image. This is the third of those guards and the first one inside the
# script the other two are watching.
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
  # absent. Whoever reads this has a source tree by definition (#295).
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

say "binaries"
# hexash imports config.py from its own directory, so both have to travel
# together. Installing only hexash.py put a VTY on the box that raised
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
install -d -m 0755 "$DESTDIR/usr/lib/hexarouting"
for f in "$REPO"/src/hexash/*.py; do
  install -D -m 0644 "$f" "$DESTDIR/usr/lib/hexarouting/$(basename "$f")"
done
chmod 0755 "$DESTDIR/usr/lib/hexarouting/hexash.py"
install -D -m 0755 "$REPO/appliance/hexash-launcher" "$DESTDIR/usr/bin/hexash"
install -D -m 0755 "$REPO/appliance/hexarouting-tune" "$DESTDIR/usr/bin/hexarouting-tune"
install -D -m 0755 "$REPO/appliance/wait-for-vpp"     "$DESTDIR/usr/lib/hexarouting/wait-for-vpp"
install -D -m 0755 "$REPO/appliance/apply-tuning"     "$DESTDIR/usr/lib/hexarouting/apply-tuning"
install -D -m 0755 "$REPO/appliance/bind-dataplane-nic" "$DESTDIR/usr/lib/hexarouting/bind-dataplane-nic"
install -D -m 0755 "$REPO/appliance/selftest"         "$DESTDIR/usr/lib/hexarouting/selftest"
install -D -m 0755 "$REPO/appliance/hexarouting-install" "$DESTDIR/usr/sbin/hexarouting-install"
install -D -m 0755 "$REPO/appliance/install-from-cmdline" "$DESTDIR/usr/lib/hexarouting/install-from-cmdline"
install -D -m 0755 "$REPO/tools/ipfix_collector.py"   "$DESTDIR/usr/lib/hexarouting/ipfix-collector"
install -D -m 0755 "$REPO/appliance/hexarouting-snmp-agent" \
    "$DESTDIR/usr/lib/hexarouting/snmp-agent"

say "dataplane plugin"
# Both paths were resolved by the preflight, which refused if either was
# missing, so these two are a copy and nothing else. The vat2 plugin used to be
# guarded here with `[ -n "$VSO" ]` and a warning on stderr, and that guard is
# what left a box running the previous version's vat2 plugin under a new
# dataplane (#301). There is nothing left to decide at this point in the file.
install -D -m 0644 "$SO" "$DESTDIR$PLUGIN_DIR/bpa_plugin.so"
install -D -m 0644 "$VSO" "$DESTDIR$VAT2_DIR/bpa_test_plugin_bpa_plugin.so"

say "systemd"
for u in hexarouting.service hexarouting-tune.service hexarouting-selftest.service \
         hexarouting-installer.service hexarouting-confirm.service \
         hexarouting-confirm.timer; do
  install -D -m 0644 "$REPO/appliance/systemd/$u" "$DESTDIR/lib/systemd/system/$u"
done
# A drop-in on VPP's own unit, not a unit of ours: it has to run every time the
# dataplane starts, including a restart nobody ordered, and ordering a separate
# unit after vpp.service would put it in the wrong place on exactly that path.
install -D -m 0644 "$REPO/appliance/systemd/vpp-api-access.conf" \
  "$DESTDIR/etc/systemd/system/vpp.service.d/hexarouting-api-access.conf"
# Same reason it is a drop-in and not a unit of ours, plus one more: a
# protection that only applies to the VPP this boot started is no protection.
# vpp.service carries Restart=always, and the restart after an OOM kill is
# exactly the moment the box must not be choosable again.
install -D -m 0644 "$REPO/appliance/systemd/vpp-oom-protect.conf" \
  "$DESTDIR/etc/systemd/system/vpp.service.d/hexarouting-oom-protect.conf"
# And the third, for the same reason again: the retention log has to be closed
# by the dataplane that is stopping, on every stop, including the restart after
# an upgrade that no unit of ours is ordered around.
install -D -m 0644 "$REPO/appliance/systemd/vpp-stop-drain.conf" \
  "$DESTDIR/etc/systemd/system/vpp.service.d/hexarouting-stop-drain.conf"

say "login shell"
install -D -m 0755 "$REPO/appliance/hexash-login" "$DESTDIR/usr/bin/hexash-login"
# Opt-in on purpose. On an existing Debian the machine may have other jobs, and
# silently replacing root's login shell is hostile - the operator would discover
# it at the worst moment, on the next login. The ISO sets HEXA_VTY_LOGIN=1.
if [ "${HEXA_VTY_LOGIN:-0}" = 1 ]; then
  grep -qx /usr/bin/hexash-login "$DESTDIR/etc/shells" 2>/dev/null || \
    echo /usr/bin/hexash-login >> "$DESTDIR/etc/shells"
  if [ -n "$DESTDIR" ]; then
    sed -i 's|^root:\([^:]*\):\([^:]*\):\([^:]*\):\([^:]*\):\([^:]*\):.*|root:\1:\2:\3:\4:\5:/usr/bin/hexash-login|' \
      "$DESTDIR/etc/passwd"
  else
    chsh -s /usr/bin/hexash-login root
  fi
  say "  root logs into the VTY; 'start shell' escapes, 'hexash=off' bypasses"
else
  say "  not changing any login shell (set HEXA_VTY_LOGIN=1 to land in the VTY)"
fi

say "version stamp"
# The box has to be able to state its own version: the tag identifies the
# release only to whoever has the repository open, and that is not the person
# on the phone. And if the tree had uncommitted changes, the file says so - an
# ISO that declares itself v0.4.1 without being it sends the whole
# investigation the wrong way.
if [ -n "${HEXA_VERSION_STAMP:-}" ]; then
  stamp=$HEXA_VERSION_STAMP
else
  stamp=$(sh "$REPO/tools/version-stamp.sh" "$REPO")
fi
install -d -m 0755 "$DESTDIR/usr/share/hexarouting"
printf '%s\n' "$stamp" > "$DESTDIR/usr/share/hexarouting/VERSION"
chmod 0644 "$DESTDIR/usr/share/hexarouting/VERSION"

say "kernel knobs"
install -D -m 0644 "$REPO/appliance/etc/sysctl.conf" \
    "$DESTDIR/etc/sysctl.d/99-hexarouting.conf"

say "configuration"
# Group-readable, never world-readable. Since accounts became part of the
# configuration (#45) this directory holds password hashes - in
# hexarouting.conf and in every rollback revision under it - and a
# world-readable copy hands every local account an offline cracking target.
# The group is a role group, so seeing them requires being trusted to change
# the configuration in the first place (#35).
# The two roles the VTY understands. Created here rather than documented as a
# step somebody must remember: an authorization model whose groups do not exist
# is one where every account falls back to read-only and the operator concludes
# the box is broken. Empty until somebody is added to them; root always has
# both.
for g in hexa-admin hexa-cgnat; do
  if [ -z "${DESTDIR:-}" ]; then
    getent group "$g" >/dev/null 2>&1 || groupadd --system "$g" 2>/dev/null || true
  fi
done

# 0770 and group-owned, not 0700: an account that may configure has to write
# the rollback store and the saved configuration, and both hold the whole
# configuration - password hashes included. Granting the capability grants
# sight of them, which is why the group is a role group and not "users".
#
# hexa-cgnat owns it and hexa-admin is ADDITIVE on top: an administrator is a
# member of both. Owning it with hexa-admin instead would leave a CGNAT
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
# unreadable to the next. setgid makes the kernel put hexa-cgnat on everything
# created in here - including the rollback directory, since a directory made
# inside a setgid directory inherits the bit - so "rollback 1" works whoever
# made the revision.
#
# install -d re-states it on an upgrade as well as a first install, which is
# what repairs a box the old VTY already narrowed.
install -d -m 2770 "$DESTDIR/etc/hexarouting"
install -d -m 2770 "$DESTDIR/etc/hexarouting/rollback"
if [ -z "${DESTDIR:-}" ] && getent group hexa-cgnat >/dev/null 2>&1; then
  chgrp hexa-cgnat /etc/hexarouting /etc/hexarouting/rollback 2>/dev/null || true
fi
# The configuration trail. The plugin opens it with O_CREAT and cannot create
# the directory, so without this the trail exists in every lab - where the lab
# happens to make the directory - and on no installed box. In deterministic
# mode that trail is the record of which pool definition was in force at a
# given instant, which is what makes an answer to a court order defensible.
# 0750: it names subscribers and their public addresses.
install -d -m 0750 "$DESTDIR/var/log/hexarouting"
[ -f "$DESTDIR/etc/hexarouting/tune.env" ] || \
  install -m 0644 "$REPO/appliance/etc/tune.env" "$DESTDIR/etc/hexarouting/tune.env"
[ -f "$DESTDIR/etc/hexarouting/vpp-base.conf" ] || \
  install -m 0644 "$REPO/appliance/etc/vpp-base.conf" \
    "$DESTDIR/etc/hexarouting/vpp-base.conf"
[ -f "$DESTDIR/etc/hexarouting/hexarouting.conf" ] || \
  printf '!\n! HexaRouting\n!\n' > "$DESTDIR/etc/hexarouting/hexarouting.conf"
# 0660 for the same reason as the directory: an account that may configure
# saves it, and it holds hashes. Never world-readable.
chmod 0660 "$DESTDIR/etc/hexarouting/hexarouting.conf"
if [ -z "${DESTDIR:-}" ] && getent group hexa-cgnat >/dev/null 2>&1; then
  chgrp hexa-cgnat "$DESTDIR/etc/hexarouting/hexarouting.conf" 2>/dev/null || true
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
  install -D -m 0644 "$REPO/docs/$d.md" "$DESTDIR/usr/share/doc/hexarouting/$d.md"
done

# In a chroot or container there is no systemd running; installing is still
# valid, only enabling services is not. That is the normal case when the ISO
# is being assembled.
if [ -z "$DESTDIR" ] && systemctl --version >/dev/null 2>&1 \
   && [ -d /run/systemd/system ]; then
  LIVE_BOX=1
  say "enabling services"
  systemctl daemon-reload
  systemctl enable hexarouting-tune.service hexarouting.service \
    hexarouting-selftest.service hexarouting-installer.service \
    hexarouting-confirm.timer
else
  LIVE_BOX=0
  # offline enablement: creates the link systemctl enable would create
  install -d -m 0755 "$DESTDIR/etc/systemd/system/multi-user.target.wants"
  for u in hexarouting-tune.service hexarouting.service \
           hexarouting-selftest.service hexarouting-installer.service; do
    ln -sf "/lib/systemd/system/$u" \
      "$DESTDIR/etc/systemd/system/multi-user.target.wants/$u"
  done
  # The timer is wanted by timers.target and not by multi-user.target, so it
  # needs its own directory. Linking it into the wrong one installs a timer
  # that exists, parses, and never elapses - and the feature it enforces is
  # one nobody notices working, so nobody would notice that either.
  install -d -m 0755 "$DESTDIR/etc/systemd/system/timers.target.wants"
  ln -sf "/lib/systemd/system/hexarouting-confirm.timer" \
    "$DESTDIR/etc/systemd/system/timers.target.wants/hexarouting-confirm.timer"
  say "installed into ${DESTDIR:-/} (services enabled offline)"
fi

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
    echo "Installed. Adjust /etc/hexarouting/tune.env and reboot so that"
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
  if systemctl is-active --quiet hexarouting.service 2>/dev/null; then
    echo "  hexarouting.service is active. It is a Type=oneshot: it applied"
    echo "  /etc/hexarouting/hexarouting.conf at the last boot, with the VTY"
    echo "  that was installed then, and exited."
  else
    echo "  hexarouting.service is NOT active, so nothing has applied"
    echo "  /etc/hexarouting/hexarouting.conf to the dataplane. It is a"
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
  echo "  systemctl restart vpp.service"
  echo "      picks up the new plugin, and DROPS EVERY SESSION AND EVERY PORT"
  echo "      BLOCK on this box: every subscriber behind it is translated"
  echo "      from nothing again. hexarouting.service is PartOf=vpp.service,"
  echo "      so it is stopped and started with it and re-applies the"
  echo "      configuration to the new dataplane."
  echo
  echo "  systemctl restart hexarouting.service"
  echo "      re-applies /etc/hexarouting/hexarouting.conf through the new"
  echo "      VTY and exits. It drops no session. It also does not change"
  echo "      which plugin the dataplane is running, so on its own the split"
  echo "      above stays exactly where it is."
  echo
  echo "  a reboot does both, and takes any /etc/hexarouting/tune.env change"
  echo "  with it."
fi
