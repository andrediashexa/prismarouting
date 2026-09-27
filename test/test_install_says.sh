#!/bin/sh
# What appliance/install.sh SAYS on a second run, driven by running it twice.
#
# For the whole life of this product the installer behaved identically on
# every run and closed with a first install's sentence: "Installed. Adjust
# /etc/prisma-routing/tune.env and reboot so that isolcpus and hugepages take
# effect." An operator upgrading a box they tuned a year ago reads "and reboot
# so that isolcpus and hugepages take effect" as "nothing to do here", and
# walks away from a box whose control plane is the new build and whose
# translating dataplane is still the old one (#291). The installer does not
# restart vpp.service, deliberately and permanently, because that drops every
# session and every port block the box is carrying. The defect was never the
# default. It was that the default was taken in silence.
#
# WHY THIS IS A SECOND READER OF install.sh AND test/test_install_manifest.py
# IS NOT ENOUGH. That file READS the script as text: which paths it names,
# which it does not, which the medium carries. It cannot answer what the
# script DOES on a root that already has a version of this product on it,
# because that is not a property of the text - it is a property of the order
# two runs happen in, and of which of them wrote the version stamp. Nothing
# here greps install.sh. It runs it, twice, and reads what came out.
#
# WHY A STAND-IN TREE. install.sh derives $REPO from its own path and copies
# out of it, and it wants a compiled bpa_plugin.so that only exists after a
# build. So the tree is a directory of symlinks into this repository plus a
# stand-in plugin file: the real script, the real units, the real documents,
# and nothing written into the source tree or onto this machine. Building the
# plugin to test a sentence would be a suite that does not run.
#
# AND WHERE IT STOPS RUNNING THE WHOLE SCRIPT, said here rather than left to
# be discovered half way down. install.sh only talks about running services
# when the root it wrote is this machine's own and systemd is up. A unit suite
# may not install into / to read a sentence, and no container this product's
# labs run in has systemd - lab/upgrade.sh drives a real upgrade over a real
# dataplane and still takes the offline arm of that branch. So three of the
# four closing screens are reachable end to end by nothing at all, which is
# why the closing words are the last section of install.sh, read three
# variables, and are RUN here on their own against box states this machine
# cannot be put into. The last section of this file does that; everything
# before it is the script itself, twice, over one root.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# A sentence, not the row it happened to fold on.
#
# Every needle below is prose the installer wraps by hand, and a needle that
# matches raw text only matches for as long as the message goes on folding in
# the same place. This repository has paid for that four times - #279 broke
# three lab needles in a day when a message was re-wrapped, and one of them
# was a negative that then passed whether the box said the thing or not. BOTH
# sides are collapsed, because these needles are themselves written across
# source lines. This is lab/verdict.sh's expect_said, in a unit suite that
# must not source a lab's runtime.
said() {  # <haystack> <sentence>
  _h=$(printf '%s' "$1" | tr '\n' ' ' | tr -s ' ')
  _n=$(printf '%s' "$2" | tr '\n' ' ' | tr -s ' ')
  case "$_h" in *"$_n"*) return 0 ;; *) return 1 ;; esac
}

echo "=== say, install and keep, on their own ==="
# Three functions the runs above go through on every file, cut out of the
# script and called alone the way test/test_run_all_labs.sh cuts _jobs_by_mem
# out of the lab suite: install.sh starts copying on the line after keep is
# defined, so sourcing it is running it. DESTDIR is a root of this suite's own.
unit_of() {  # <name> -> its definition out of appliance/install.sh
  if grep -q "^$1() .*}[[:space:]]*$" "$REPO/appliance/install.sh"; then
    grep -m1 "^$1() " "$REPO/appliance/install.sh"
  else
    sed -n "/^$1() *{/,/^}/p" "$REPO/appliance/install.sh"
  fi
}
UNITS=$WORK/units.sh
{ unit_of say; unit_of install; unit_of keep; } > "$UNITS"
for n in say install keep; do
  grep -q "^$n() " "$UNITS" \
    || bad "$n() was not found in appliance/install.sh, so its case below runs nothing"
done
UROOT=$WORK/uroot
mkdir -p "$UROOT/etc/prisma-routing"
echo payload > "$WORK/src.txt"

got=$(. "$UNITS"; say binaries)
[ "$got" = "==> binaries" ] \
  && ok "say prints the section behind the '==>' the install log is read by" \
  || bad "say gave '$got'"

got=$(. "$UNITS"; DESTDIR=$UROOT; BUILD_FILES=
      install -d -m 0755 "$DESTDIR/usr/lib/prisma-routing"
      install -D -m 0644 "$WORK/src.txt" "$DESTDIR/usr/lib/prisma-routing/a.py"
      install -D -m 0755 "$WORK/src.txt" "$DESTDIR/usr/bin/prismash"
      echo "files=[$BUILD_FILES]"
      cat "$DESTDIR/usr/bin/prismash"
      stat -c %a "$DESTDIR/usr/bin/prismash")
[ "$got" = "files=[ usr/lib/prisma-routing/a.py usr/bin/prismash]
payload
755" ] \
  && ok "install copies with the mode asked for and records each FILE it \
wrote, relative to the root, and not the directory -d made" \
  || bad "install gave:
$got"

got=$(. "$UNITS"; DESTDIR=$UROOT; YOUR_FILES=
      echo new > "$WORK/tune.env"
      keep 0644 "$WORK/tune.env" "$DESTDIR/etc/prisma-routing/tune.env"
      first=$(cat "$DESTDIR/etc/prisma-routing/tune.env")
      echo edited > "$DESTDIR/etc/prisma-routing/tune.env"
      keep 0644 "$WORK/tune.env" "$DESTDIR/etc/prisma-routing/tune.env"
      echo "first=$first second=$(cat "$DESTDIR/etc/prisma-routing/tune.env") yours=[$YOUR_FILES]")
[ "$got" = "first=new second=edited yours=[ etc/prisma-routing/tune.env etc/prisma-routing/tune.env]" ] \
  && ok "keep writes a file once, leaves the operator's edit alone on the \
next run, and lists it as theirs both times" \
  || bad "keep gave:
$got"

# THE THREE READERS AT THE BOTTOM OF THIS FILE, and the reason every run below
# hands its output to say_capture (#371).
#
# #364 gave appliance/prisma-routing-install, appliance/bind-dataplane-nic and
# appliance/apply-tuning a message-coverage reader over test/say-coverage.sh.
# appliance/install.sh did not get one, and it is the program that puts the
# files on the box - on a first install and on every upgrade, which is the path
# #295 opened. Measured over its real output: of the messages it can print, 44
# had been produced by no run anywhere in this repository. Three of them are
# the manifest header, one is the last sentence an operator reads on a first
# install, and the rest are EVERYTHING THIS SCRIPT SAYS ABOUT UPGRADING A BOX
# THAT IS RUNNING - the whole of #291 and #293, the work that stopped a box
# reporting a new version while forwarding through the previous dataplane.
#
# WHY THE READER IS HERE AND NOT IN A SUITE OF ITS OWN. The boxes are already
# here: a first install, three upgrades, two refusals, the ISO's own arm and
# the closing screens driven against box states this machine cannot be put
# into. A second suite would have to build every one of those again, and the
# day after it was written the two rigs would differ. test_installer.sh and
# test_selftest.sh put their pair at the bottom of the file that arranges the
# boxes for the same reason.
#
# AND WHY EVERY RUN GOES THROUGH say_capture RATHER THAN A PIPE INTO say_box.
# This suite redirects the installer's output into a file, deliberately and
# permanently - the two streams are kept apart because #301 is a defect about
# WHICH stream a sentence went to - so there is no pipeline to box. say_capture
# is handed the file after the run, boxes it and keeps it where SAY_CORPUS
# says, and the suite is otherwise untouched.
. "$(cd "$(dirname "$0")" && pwd)/say-coverage.sh"
INSTALL=$REPO/appliance/install.sh
say_init "$INSTALL"

# ---------------------------------------------------------------- the fixture
TREE=$WORK/tree
DEST=$WORK/root
mkdir -p "$TREE" "$DEST/etc/frr"
for e in src appliance tools docs ref VERSION; do
  [ -e "$REPO/$e" ] && ln -s "$REPO/$e" "$TREE/$e"
done
mkdir -p "$TREE/.build-vpp/lib/vpp_plugins" "$TREE/.build-vpp/lib/vat2_plugins"
echo "stand-in for a compiled bpa_plugin.so" \
  > "$TREE/.build-vpp/lib/vpp_plugins/bpa_plugin.so"
echo "stand-in for a compiled vat2 plugin" \
  > "$TREE/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so"
# The Debian package's own defaults, because the installer edits this file and
# appends to it, and a missing directory would stop the run under `set -e`
# before it reached anything this suite is about.
{ printf 'bgpd=no\nospfd=no\nospf6d=no\nisisd=no\n'
  printf 'bgpd_options="  -A 127.0.0.1"\n'; } > "$DEST/etc/frr/daemons"

OLD_V="0.68.0"
NEW_V="0.72.0 (beefcaf)"

install_run() {  # <version stamp> <output file>
  DESTDIR="$DEST" PRISMA_VERSION_STAMP="$1" PLUGIN_DIR=/usr/lib/vpp_plugins \
    VAT2_DIR=/usr/lib/vat2_plugins \
    sh "$TREE/appliance/install.sh" > "$2" 2>&1
  # THE STATUS FIRST. say_capture is a command, and every caller of this
  # function reads what it returns - `if install_run ... ; then` is how the
  # first run decides whether anything below it was measured at all.
  _rc=$?
  say_capture "$INSTALL" "$2" "$(basename "$2")"
  return "$_rc"
}

# The same run with the two streams kept apart, and the exit status returned
# rather than swallowed. #301 is a defect about WHICH STREAM a sentence went
# to and what the exit status was beside it, so a runner that merges them
# cannot ask the question: a warning on stderr followed by exit 0 and a
# refusal on stderr followed by exit 1 are the same text in one file.
install_try() {  # <version stamp> <stdout file> <stderr file>
  DESTDIR="$DEST" PRISMA_VERSION_STAMP="$1" PLUGIN_DIR=/usr/lib/vpp_plugins \
    VAT2_DIR=/usr/lib/vat2_plugins \
    sh "$TREE/appliance/install.sh" > "$2" 2> "$3"
  _rc=$?
  # BOTH STREAMS, as two boxes. They are two files because the question this
  # suite asks needs them apart, and they are two boxes for the same reason:
  # the refusals live on stderr and the narration on stdout, and a box that
  # merged them could not tell the reader below which of the two a message
  # came out of. An empty one is not a box at all - say_capture drops it, and
  # the runs here that print nothing on stdout print nothing on purpose.
  say_capture "$INSTALL" "$2" "$(basename "$2")"
  say_capture "$INSTALL" "$3" "$(basename "$3")"
  return "$_rc"
}

echo "=== the first install: there was nothing here before ==="
if install_run "$OLD_V" "$WORK/run1"; then
  ok "appliance/install.sh ran into an empty root"
else
  bad "the installer failed on an empty root, so nothing below was measured:"
  sed 's/^/        /' "$WORK/run1"
  echo
  echo "1 FAILURES"
  exit 1
fi
RUN1=$(cat "$WORK/run1")

[ "$(cat "$DEST/usr/share/prisma-routing/VERSION" 2>/dev/null)" = "$OLD_V" ] \
  && ok "and stamped the box $OLD_V" \
  || bad "the version stamp is not $OLD_V, so the fixture below is not an \
upgrade at all: $(cat "$DEST/usr/share/prisma-routing/VERSION" 2>/dev/null)"

# The whole point of the change, in the direction that is easy to get wrong.
# A script that decided "upgrade" by looking for the stamp AFTER writing it
# would say this on every run, including the first, and an operator installing
# a box for the first time would be told a dataplane is running the old build
# on a machine that has never had one.
said "$RUN1" "Upgraded." \
  && bad "a FIRST install announced an upgrade. There was no previous \
version on this root, so the stamp is being read after it was written" \
  || ok "it says nothing about an upgrade"
said "$RUN1" "the version stamp was" \
  && bad "a first install named a previous version stamp, and there was none" \
  || ok "and names no previous version"

echo
echo "=== the second run: the same root, a different build ==="
# Anything the operator wrote is put back first, because an installer that
# silently rewrites a conffile on an upgrade is the other half of this
# question and this is the cheapest place to hold it.
echo "PRISMA_SUBSCRIBERS=40000" >> "$DEST/etc/prisma-routing/tune.env"
echo "! an operator's line" >> "$DEST/etc/prisma-routing/prisma-routing.conf"

if install_run "$NEW_V" "$WORK/run2"; then
  ok "it ran again over the root the first run left"
else
  bad "the installer failed on a root it had already installed into:"
  sed 's/^/        /' "$WORK/run2"
fi
RUN2=$(cat "$WORK/run2")

said "$RUN2" "Upgraded." \
  && ok "and this time it says so" \
  || bad "the second run over an installed root said nothing about being an \
upgrade, which is the defect #291 names: the operator is told the files are \
in place and not that nothing was restarted"

# Both versions, and the OLD one is the one that can only come from a read
# taken before the install overwrote it.
said "$RUN2" "the version stamp was $OLD_V" \
  && ok "it names the build that was on the box: $OLD_V" \
  || bad "the upgrade notice does not name $OLD_V, the version that was \
here. It was read after the install had already overwritten the stamp, or it \
was not read at all"
said "$RUN2" "and now says $NEW_V" \
  && ok "and the build that is on it now: $NEW_V" \
  || bad "the upgrade notice does not name $NEW_V, the version this run \
installed"

[ "$(cat "$DEST/usr/share/prisma-routing/VERSION" 2>/dev/null)" = "$NEW_V" ] \
  && ok "and the stamp on the box is the new one" \
  || bad "the box is not stamped $NEW_V after the upgrade"

echo
echo "=== what the second run must NOT have done to the operator's files ==="
grep -q "^PRISMA_SUBSCRIBERS=40000$" "$DEST/etc/prisma-routing/tune.env" \
  && ok "tune.env still has the line the operator put in it" \
  || bad "the upgrade overwrote /etc/prisma-routing/tune.env, which is the file \
the closing sentence has always told the operator to edit"
grep -q "^! an operator's line$" "$DEST/etc/prisma-routing/prisma-routing.conf" \
  && ok "and prisma-routing.conf is the configuration the box was carrying" \
  || bad "the upgrade overwrote /etc/prisma-routing/prisma-routing.conf, which is \
the whole configuration of the box"

echo
echo "=== and it is no noisier on the path that has no services ==="
# This root is a $DESTDIR, which is what lab/igp.sh and lab/oom.sh install
# into and the shape appliance/build-iso.sh assembles a medium in. There is no
# systemd under it and no process running out of it, so a paragraph about
# vpp.service still carrying traffic would be a paragraph about some other
# machine - the one running this suite.
for n in "vpp.service" "prisma-routing.service" "systemctl restart" \
         "show version"; do
  said "$RUN2" "$n" \
    && bad "installing into a \$DESTDIR talked about '$n'. Nothing is running \
out of that root; the only box with those services is the one this ran on, \
and it was not touched" \
    || ok "it does not mention $n"
done
said "$RUN2" "(services enabled offline)" \
  && ok "and it still closes the way this path always closed" \
  || bad "the offline closing line is gone, so this path changed after all"

echo
echo "=== a third run, the same build over itself ==="
# A reinstall of the build already on the box. It is still an upgrade in the
# only sense that matters here - files were replaced under services that are
# still running the ones they loaded - and a script that went quiet because
# the two stamps match would be silent in exactly the case an operator is most
# likely to be repairing something.
install_run "$NEW_V" "$WORK/run3"
RUN3=$(cat "$WORK/run3")
said "$RUN3" "Upgraded." \
  && ok "re-installing the same build still says the files were replaced" \
  || bad "re-installing $NEW_V over $NEW_V said nothing, so an operator \
repairing a box is told the same thing a first install is told"
said "$RUN3" "the version stamp was $NEW_V" \
  && ok "and names it on both sides" \
  || bad "the third run does not name $NEW_V as what was there"

echo
echo "=== an upgrade it cannot install every component of ==="
#
# The defect this answers, measured on the build before it: install.sh could
# not find a vat2 plugin, printed "warning: no vat2 plugin; the binary API
# will not be reachable" ON STDERR, wrote the new bpa_plugin.so over the old
# one, left the PREVIOUS version's vat2 plugin exactly where it was, stamped
# the box with the new version and exited 0. The box then asserted a build it
# had half of, with a vat2 plugin older than the dataplane plugin beside it,
# which is the skew #293 measured as unable to answer a message that grew a
# field - and #291's `show version` cannot see it, because the stamp and the
# loaded plugin's build are both the new version (#301).
#
# WHAT IS ASSERTED HERE IS NOT THE WORDING, IT IS THAT NOTHING MOVED. A
# refusal that still wrote the dataplane plugin, or still moved the stamp,
# would be the same half state with a longer message in front of it, and the
# message is the easiest half to get right. So the three files this run could
# have touched are checksummed before it and compared after: the version
# stamp, the dataplane plugin on the root, and the previous version's vat2
# plugin. That is also why the stand-in bpa_plugin.so is CHANGED first - over
# an identical file a copy that did happen is indistinguishable from one that
# did not, and this check would pass on an installer that wrote everything.
BUILT_VAT2=$TREE/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so
BUILT_DP=$TREE/.build-vpp/lib/vpp_plugins/bpa_plugin.so
ROOT_VAT2=$DEST/usr/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so
ROOT_DP=$DEST/usr/lib/vpp_plugins/bpa_plugin.so
STAMP_FILE=$DEST/usr/share/prisma-routing/VERSION

sum() { md5sum "$1" 2>/dev/null | cut -d' ' -f1; }

WAS_VAT2=$(sum "$ROOT_VAT2")
WAS_DP=$(sum "$ROOT_DP")
WAS_STAMP=$(cat "$STAMP_FILE" 2>/dev/null)

# The fixture is only a fixture if the box really is carrying a vat2 plugin to
# begin with. Asserted rather than assumed: if the three runs above never put
# one on this root, "the old one is still there" below is true of nothing.
[ -n "$WAS_VAT2" ] \
  && ok "the root is carrying a vat2 plugin from the runs above" \
  || bad "no vat2 plugin ever reached $ROOT_VAT2 across three installs, so \
appliance/install.sh is not installing it at all and every check below is \
about a file that is not there"

echo "a NEWER stand-in bpa_plugin.so" > "$BUILT_DP"
rm -f "$BUILT_VAT2"
NEXT_V="0.73.0 (dddddd)"

if install_try "$NEXT_V" "$WORK/out4" "$WORK/err4"; then
  bad "the installer exited 0 on a build tree with no vat2 plugin. That is \
#301: it warns on stderr, installs the rest, and stamps the box with a \
version it only has half of"
else
  ok "it refuses: a build tree with no vat2 plugin is not an upgrade"
fi
ERR4=$(cat "$WORK/err4")
OUT4=$(cat "$WORK/out4")

said "$ERR4" "bpa_test_plugin_bpa_plugin.so not found" \
  && ok "and names the file that is missing" \
  || bad "the refusal does not name bpa_test_plugin_bpa_plugin.so, so the \
reader is told the install failed and not which component it could not \
install: $ERR4"
said "$ERR4" "run tools/build.sh plugin" \
  && ok "and the command that produces it" \
  || bad "the refusal does not say how to produce the missing plugin"
said "$ERR4" "Nothing has been written." \
  && ok "and says the root was not touched, which is the whole reason to \
refuse this early" \
  || bad "the refusal does not tell the reader that nothing was written. A \
failed upgrade whose blast radius is not stated is one an operator has to go \
and measure at two in the morning"

# Every negative below is satisfied by an installer that refuses for some
# unrelated reason before it reaches the plugins at all, so the messages are
# read first and the state second.
[ "$(cat "$STAMP_FILE" 2>/dev/null)" = "$WAS_STAMP" ] \
  && ok "the version stamp still says $WAS_STAMP" \
  || bad "the refused install moved the version stamp from $WAS_STAMP to \
$(cat "$STAMP_FILE" 2>/dev/null). The box now asserts a build it does not \
wholly have, which is #301 with a refusal printed over it"
[ "$(sum "$ROOT_DP")" = "$WAS_DP" ] \
  && ok "and the dataplane plugin on the root is the one that was there" \
  || bad "the refused install wrote the new bpa_plugin.so anyway, so the box \
is carrying a new dataplane behind the previous version's vat2 plugin - the \
exact skew this refusal exists to prevent"
[ "$(sum "$ROOT_VAT2")" = "$WAS_VAT2" ] \
  && ok "and the vat2 plugin it could not replace was left alone, not \
half-replaced" \
  || bad "the refused install changed $ROOT_VAT2, which it had no new \
version of"
[ -z "$OUT4" ] \
  && ok "it printed nothing at all on stdout: it refused before it announced \
installing anything" \
  || bad "the refused install narrated work on stdout before it refused, so \
it got past the point where refusing is free: $OUT4"

# #252, #271, #285. This is prose an operator reads on an 80-column serial
# console, and three messages in this product have shipped wider than one.
WIDE=$(awk 'length > 79 {print length" columns: "$0}' "$WORK/err4")
if [ -z "$WIDE" ] && [ "$(wc -l < "$WORK/err4")" -ge 6 ]; then
  ok "the refusal is $(wc -l < "$WORK/err4") lines, widest $(awk '{print \
length}' "$WORK/err4" | sort -rn | head -1) columns"
else
  bad "the refusal is $(wc -l < "$WORK/err4") line(s) and these are wider \
than the 79-column console an operator reads it on: $WIDE"
fi

echo
echo "=== and a tree with NO dataplane plugin, which is the other spelling ==="
#
# THE FIRST REFUSAL IN install.sh AND THE ONE NOTHING HAD RUN. It sits eight
# lines above the one this section has just measured, refuses for the same
# reason, and makes the same promise about what it left on the root - and the
# only thing that had ever driven it was somebody running a build out of
# order. A refusal nobody has watched fire is a refusal that may not fire.
#
# It reads as an empty build tree rather than a half-finished one, and it is
# the likelier of the two: a clean checkout, a container that was rebuilt, a
# .build-vpp somebody removed to reclaim space. The box it protects is the
# same box - an upgrade that is refused having changed nothing is the only
# outcome an operator woken at two in the morning can act on.
# THE VAT2 PLUGIN IS PUT BACK FIRST, so that the dataplane plugin is the only
# thing missing from this tree. Left out, both refusals would be available and
# the first one to run would be the only one measured - a check that passes
# whichever of the two fired.
echo "a NEWER stand-in vat2 plugin" > "$BUILT_VAT2"
rm -f "$BUILT_DP"
WAS_DP=$(sum "$ROOT_DP")
WAS_VAT2=$(sum "$ROOT_VAT2")
WAS_STAMP=$(cat "$STAMP_FILE" 2>/dev/null)
if install_try "$NEXT_V" "$WORK/out6" "$WORK/err6"; then
  bad "the installer exited 0 on a build tree with no dataplane plugin at \
all, so the box was stamped a version whose dataplane it never received"
else
  ok "it refuses: a tree with no bpa_plugin.so is not an upgrade"
fi
ERR6=$(cat "$WORK/err6")
OUT6=$(cat "$WORK/out6")
said "$ERR6" "bpa_plugin.so not found" \
  && ok "and names the file that is missing" \
  || bad "the refusal does not name bpa_plugin.so, so the reader is told the \
install failed and not which component it could not install: $ERR6"
said "$ERR6" "run tools/build.sh plugin first" \
  && ok "and the command that produces it" \
  || bad "the refusal does not say how to produce the missing plugin: $ERR6"
# THE SAME PROMISE THE OTHER REFUSAL MAKES, and it is the reason both of them
# are at the top of the file rather than beside the lines that install the
# plugins. A refusal is only worth taking while it still costs nothing, and it
# costs nothing only while nothing has been written.
said "$ERR6" "this root is still whatever it was before" \
  && ok "and says the root was not touched" \
  || bad "the refusal does not tell the reader that nothing was written. A \
failed upgrade whose blast radius is not stated is one an operator has to go \
and measure at two in the morning: $ERR6"
# And the state, because every message above is satisfied by an installer that
# says all of it and installs anyway.
[ "$(cat "$STAMP_FILE" 2>/dev/null)" = "$WAS_STAMP" ] \
  && ok "the version stamp still says $WAS_STAMP" \
  || bad "the refused install moved the version stamp from $WAS_STAMP to \
$(cat "$STAMP_FILE" 2>/dev/null)"
[ "$(sum "$ROOT_DP")" = "$WAS_DP" ] && [ "$(sum "$ROOT_VAT2")" = "$WAS_VAT2" ] \
  && ok "and both plugins on the root are the ones that were there" \
  || bad "the refused install wrote a plugin onto the root"
[ -z "$OUT6" ] \
  && ok "and it printed nothing on stdout: it refused before it announced \
installing anything" \
  || bad "the refused install narrated work on stdout before it refused: $OUT6"
# Put back, because everything below this line is about a tree that has both.
echo "a NEWER stand-in bpa_plugin.so" > "$BUILT_DP"

echo
echo "=== and the same tree, once the build is finished ==="
# The other half of the refusal, and the one that says it is about the missing
# file rather than about anything else on this root. It also proves the
# refused run did not consume the previous stamp: the notice below still names
# the version the THIRD run left, not the one the refused fourth run carried.
echo "a NEWER stand-in vat2 plugin" > "$BUILT_VAT2"
if install_try "$NEXT_V" "$WORK/out5" "$WORK/err5"; then
  ok "the same tree installs once both plugins are in it"
else
  bad "the installer still refuses a tree that has both plugins, so the \
refusal above was not about the vat2 plugin at all:"
  sed 's/^/        /' "$WORK/err5"
fi
RUN5=$(cat "$WORK/out5")
[ "$(cat "$ROOT_VAT2" 2>/dev/null)" = "a NEWER stand-in vat2 plugin" ] \
  && ok "and the new vat2 plugin is the one on the root" \
  || bad "the run that found a vat2 plugin did not install it: $ROOT_VAT2 is \
still $(cat "$ROOT_VAT2" 2>/dev/null)"
[ "$(cat "$ROOT_DP" 2>/dev/null)" = "a NEWER stand-in bpa_plugin.so" ] \
  && ok "and so is the new dataplane plugin" \
  || bad "the dataplane plugin on the root is not the one this run installed"
said "$RUN5" "the version stamp was $WAS_STAMP" \
  && ok "and it upgrades from $WAS_STAMP, the build the refused run left \
standing" \
  || bad "the upgrade after the refusal does not name $WAS_STAMP as what was \
here, so the refused run moved the stamp after all: $RUN5"

echo
echo "=== the arm the ISO takes, where root lands in the VTY ==="
#
# PRISMA_VTY_LOGIN=1 is the one branch of this installer that an operator never
# takes and every shipped box is built by: appliance/build-iso.sh sets it in
# the chroot, and it is what makes the console of a racked appliance the VTY
# rather than a root shell. Opt-in on an existing Debian on purpose, because
# silently replacing root's login shell on a machine that has other jobs is
# hostile.
#
# It is here for the sentence it prints, which is the one line of this run an
# operator reads if they ever take this arm by hand, and which no run in this
# repository had ever produced. LAST of the runs over this root, because it is
# the only one that edits /etc/passwd and /etc/shells and every check above is
# about a root that was not treated that way.
printf 'root:x:0:0::/root:/bin/sh\n' > "$DEST/etc/passwd"
: > "$DEST/etc/shells"
install_run_vty() {  # <version stamp> <output file>
  DESTDIR="$DEST" PRISMA_VERSION_STAMP="$1" PLUGIN_DIR=/usr/lib/vpp_plugins \
    VAT2_DIR=/usr/lib/vat2_plugins PRISMA_VTY_LOGIN=1 \
    sh "$TREE/appliance/install.sh" > "$2" 2>&1
  _rc=$?
  say_capture "$INSTALL" "$2" "$(basename "$2")"
  return "$_rc"
}
if install_run_vty "$NEXT_V" "$WORK/run7"; then
  ok "the installer ran the way build-iso.sh runs it, with PRISMA_VTY_LOGIN=1"
else
  bad "the installer failed on the ISO's own arm:"
  sed 's/^/        /' "$WORK/run7"
fi
RUN7=$(cat "$WORK/run7")
said "$RUN7" "root logs into the VTY; 'start shell' escapes, 'prismash=off' \
bypasses" \
  && ok "and says so, with both ways out of it" \
  || bad "the arm that puts root in the VTY says nothing about it. An operator \
who takes it by hand is not told which console they have just changed, nor \
either way back out of it: $RUN7"
said "$RUN7" "not changing any login shell" \
  && bad "it said it was leaving the login shell alone on the run that \
changed it" \
  || ok "and not the sentence the other arm prints"
grep -qx /usr/bin/prismash-login "$DEST/etc/shells" \
  && ok "/etc/shells names the VTY launcher, so it is a shell root may have" \
  || bad "the VTY launcher is not in /etc/shells, so chsh would refuse it and \
the sentence above describes something that did not happen"
case "$(cat "$DEST/etc/passwd")" in
  *:/usr/bin/prismash-login) ok "and root's shell in /etc/passwd is the VTY" ;;
  *) bad "root's login shell was not changed: $(cat "$DEST/etc/passwd")" ;;
esac

echo
echo "=== the three screens only a running box can reach ==="
#
# install.sh says what it says about vpp.service and prisma-routing.service only
# when the root it wrote is this machine's own and systemd is up. Neither this
# suite nor any lab in this repository can arrange that: installing into / to
# read a sentence is not something a unit suite may do, and the containers the
# labs run in have no systemd - lab/upgrade.sh drives a real upgrade over a
# real dataplane and still takes the offline arm of that branch.
#
# So the closing words are the last section of install.sh, they read three
# variables and touch nothing, and they are RUN here - not read. What is
# stubbed is `systemctl`, exactly as test/test_apply_tuning.sh stubs the tuner
# and test/test_selftest.sh stubs getent: the decision under test is the
# script's, and the answers it decides on are the fixture.
#
# The cost, stated because it is real: this reaches into install.sh by a
# marker line rather than by calling a function the file exports, because a
# shell script has no way to be sourced for its functions alone without a
# guard nobody would remember to keep. If the marker moves, the extraction
# comes back empty and the checks below go red rather than quiet, which is the
# direction that failure has to fall.
CLOSING=$WORK/closing.sh
sed -n '/^# THE CLOSING WORDS\./,$p' "$REPO/appliance/install.sh" > "$CLOSING"
if [ "$(wc -l < "$CLOSING")" -ge 40 ]; then
  ok "the closing section is $(wc -l < "$CLOSING") lines at the end of \
install.sh"
else
  bad "the closing section of appliance/install.sh could not be found after \
the marker line, so every check below is running on nothing"
fi

STUB=$WORK/bin
mkdir -p "$STUB"
# is-active answers from a file, so the same stub serves a box with the
# dataplane up and one with it down. Everything else systemctl is asked here
# succeeds silently, which is what a real one does.
cat > "$STUB/systemctl" <<'STUBEOF'
#!/bin/sh
if [ "${1:-}" = "is-active" ]; then
  for a in "$@"; do u=$a; done
  grep -qx "$u" "$FAKE_ACTIVE" 2>/dev/null && exit 0
  exit 3
fi
exit 0
STUBEOF
chmod +x "$STUB/systemctl"

screen() {  # <prev stamp> <live box 0|1> <active units, newline separated> <tag>
  printf '%s\n' "$3" > "$WORK/active"
  { echo "PREV_STAMP='$1'; stamp='$NEW_V'; LIVE_BOX=$2; CONTAINER_DEFAULT=${CD:-0}"
    cat "$CLOSING"; } \
    > "$WORK/drive.sh"
  # INTO A FILE AND THEN OUT AGAIN, rather than straight to the caller's
  # command substitution. These screens are the three the reader at the bottom
  # of this file can get nowhere else - they are most of #371's forty-four -
  # and a corpus that held every install this suite runs and none of them
  # would be missing the half the issue is about.
  PATH="$STUB:$PATH" FAKE_ACTIVE="$WORK/active" sh "$WORK/drive.sh" \
    > "$WORK/screen.$4" 2>&1
  say_capture "$INSTALL" "$WORK/screen.$4" "screen-$4"
  cat "$WORK/screen.$4"
}

BOTH_UP="vpp.service
prisma-routing.service"

FIRST=$(screen "" 1 "" first)
said "$FIRST" "Installed. Adjust /etc/prisma-routing/tune.env and reboot so \
that isolcpus and hugepages take effect." \
  && ok "a first install on a live box still gets the sentence it always got" \
  || bad "the first install's closing sentence is gone from a first install, \
which is the one run it was always right for: $FIRST"
said "$FIRST" "Upgraded." \
  && bad "a first install on a live box announced an upgrade" \
  || ok "and nothing about an upgrade"
said "$FIRST" "vpp.service" \
  && bad "a first install told the operator a dataplane is running the old \
build on a box that has never had one" \
  || ok "and nothing about a dataplane still running an older build"

UP=$(screen "$OLD_V" 1 "$BOTH_UP" up)
said "$UP" "Installed. Adjust /etc/prisma-routing/tune.env and reboot" \
  && bad "an upgrade closed with the first install's sentence. That is #291: \
an operator who tuned this box a year ago reads 'and reboot so that isolcpus \
and hugepages take effect' as nothing to do" \
  || ok "an upgrade does not close with the first install's sentence"
said "$UP" "the version stamp was $OLD_V" \
  && ok "it names what was here and what is here now" \
  || bad "the upgrade screen on a live box does not name $OLD_V"
said "$UP" "vpp.service is running, and it is still holding the \
bpa_plugin.so that this install has just replaced underneath it." \
  && ok "it says vpp.service is still the old build" \
  || bad "the upgrade screen does not say that vpp.service is still running \
the plugin this install replaced, which is the whole finding: $UP"
said "$UP" "it is what translates every packet on this box, and it is the \
old build" \
  && ok "and that it is what is translating packets right now" \
  || bad "the upgrade screen does not say that the OLD dataplane is what is \
translating packets right now"
said "$UP" "DROPS EVERY SESSION AND EVERY PORT BLOCK on this box" \
  && ok "restarting vpp.service is priced: every session, every port block" \
  || bad "the upgrade screen does not say what restarting vpp.service costs, \
so the operator is told about a split and not about the price of closing it"
said "$UP" "prisma-routing.service is active. It is a Type=oneshot" \
  && ok "prisma-routing.service is named as the Type=oneshot it is" \
  || bad "the upgrade screen does not say that prisma-routing.service is a \
Type=oneshot"
said "$UP" "re-applies /etc/prisma-routing/prisma-routing.conf through the new VTY \
and exits. It drops no session." \
  && ok "and restarting it is priced separately, at nothing" \
  || bad "the upgrade screen does not say what restarting prisma-routing.service \
does or that it costs no session"

# AND THE ONE THING NEITHER RESTART DOES. /etc/vpp/startup.conf is written by
# prisma-routing-tune.service, which runs at BOOT, so a dataplane restarted
# without one starts on the file the PREVIOUS version's tuner generated. An
# operator who upgraded to 0.75.2 for the MTU their card can carry, and then
# restarted vpp.service alone, would get the same dead port and no reason for
# it. The paragraph priced both restarts and said nothing about what neither
# of them does.
said "$UP" "NEITHER restart rewrites /etc/vpp/startup.conf" \
  && ok "and the screen says what neither restart does" \
  || bad "the upgrade screen prices both restarts and never says that \
neither rewrites the generated startup.conf, so an operator who upgraded for \
something the tuner decides restarts into the old file"
said "$UP" "systemctl restart prisma-routing-tune.service" \
  && ok "and names the unit to run first when a reboot is not wanted" \
  || bad "it says a reboot is needed and does not give the operator the one \
command that avoids it"
said "$UP" "system dataplane restart" \
  && bad "a box on the unit path was sent to the containers' command: $UP" \
  || ok "a box on vpp.service is not sent to the containers' command"

# A BOX ON THE CONTAINERS (#453, #459). Measured on a KVM guest: the screen
# above was all it got, naming vpp.service, which that box does not run - and
# the containers' own `systemctl restart` is refused by systemd there. It is
# sent to the VTY's command, priced, and told the saved file is what returns.
CUP=$(CD=1 screen "$OLD_V" 1 "prisma-routing-containers.service
prisma-routing.service" cup)
said "$CUP" "system dataplane restart     (typed in the VTY)" \
  && ok "a container box is given the command that restarts its dataplane" \
  || bad "a container box was not told how to restart its dataplane: $CUP"
said "$CUP" "DROPS EVERY SESSION AND EVERY PORT BLOCK on this" \
  && said "$CUP" "'save' first anything committed since" \
  && ok "priced, and told that the saved configuration is what comes back" \
  || bad "the containers' advice is not priced or does not say to save: $CUP"
said "$UP" "'show version' on this box reads both builds and compares them." \
  && ok "and it points at 'show version' for the comparison" \
  || bad "the upgrade screen does not send the operator to 'show version', \
which is the one reading on the box that compares the two builds (#291 item 2)"
# The paragraph show version owns, which this must NOT carry a second copy of.
said "$UP" "this is the build the dataplane was loaded with at the last boot" \
  && bad "the installer has its own copy of the sentence 'show version' \
prints. Two copies of one paragraph are two things to keep in step" \
  || ok "and does not repeat what that reading says"
# #295: there is no install.sh on an installed box, no source tree and no
# toolchain. What an operator has is the unpacked upgrade artifact they ran
# this from, which they may well have deleted by the time they read the screen,
# and a box they may have reached this way after a reinstall - so a closing
# sentence that told them to run this again would be sending them to a file
# that is very likely not there.
said "$UP" "run this installer again" \
  && bad "the closing words tell the operator to re-run an installer that is \
not on their box (#295)" \
  || ok "and does not send the operator back to a file the box does not have"

DOWN=$(screen "$OLD_V" 1 "" down)
said "$DOWN" "vpp.service is NOT running" \
  && ok "a stopped dataplane is reported stopped" \
  || bad "the upgrade screen claims vpp.service is running on a box where it \
is not: $DOWN"
said "$DOWN" "it is what translates every packet on this box" \
  && bad "a stopped vpp.service was described as translating every packet on \
the box, which sends the operator to schedule a window for a restart that \
would cost them nothing" \
  || ok "and is not described as carrying traffic"

echo
echo "=== and the screen a \$DESTDIR install can never reach ==="
#
# `say "enabling services"` sits behind `[ -z "$DESTDIR" ]`, which is to say
# behind installing into the machine this suite is running on. Everything else
# in this file installs into a fake root, because a unit suite may not put
# files in /usr/bin to read a sentence, and every container this product's labs
# run in has no systemd for the second half of that condition either. So this
# is the fourth screen the comment at the top of this file counts: three are
# below the CLOSING WORDS marker and driven above, and this one is not.
#
# DRIVEN THE SAME WAY, at the seam the product already has: the branch is its
# own `if` under a comment of its own, it is the only thing between the
# documentation and the manifest, and the LIVE arm of it writes nothing at all
# - it reloads systemd and enables five units, and that is the whole of it. The
# OFFLINE arm writes, and is what every run above took, so it is covered
# already and not driven here.
#
# The cost, stated because it is the same cost the closing section carries:
# this reaches into install.sh by a line of its own text rather than by calling
# a function the file exports. If that comment moves, the extraction comes back
# short and the floor below goes red rather than quiet.
ENABLE=$WORK/enable.sh
sed -n '/^# In a chroot or container there is no systemd running/,/^fi$/p' \
  "$REPO/appliance/install.sh" > "$ENABLE"
if [ "$(wc -l < "$ENABLE")" -ge 20 ] && grep -q 'enabling services' "$ENABLE"
then
  ok "the service-enabling branch is $(wc -l < "$ENABLE") lines of install.sh"
else
  bad "the branch that enables the services could not be found in \
appliance/install.sh, so the check below is running on nothing"
fi
# DESTDIR EMPTY, which is what makes it the live arm, and say() because the
# fragment calls it and the definition is three hundred lines above the marker.
{ echo 'DESTDIR=""'; echo 'say() { echo "==> $*"; }'; cat "$ENABLE"; } \
  > "$WORK/drive-enable.sh"
# AND A /run/systemd/system OF ITS OWN, in a mount namespace, rather than this
# machine's. The third clause of the condition is `[ -d /run/systemd/system ]`,
# and read off the machine it makes the answer a property of where this suite
# was run: green on a developer's laptop and red in a build container, over a
# product nobody changed. With a namespace the answer is the fixture's.
if unshare -r true 2>/dev/null; then
  PATH="$STUB:$PATH" unshare -rm sh -c '
      mount -t tmpfs tmpfs /run || exit 97
      mkdir -p /run/systemd/system || exit 96
      exec sh "$1"' sh "$WORK/drive-enable.sh" > "$WORK/enable.out" 2>&1
elif [ -d /run/systemd/system ]; then
  # No namespaces here, so the machine answers. Still a box, and still the
  # live arm, because the two clauses this suite does control are the ones
  # that decide it.
  PATH="$STUB:$PATH" sh "$WORK/drive-enable.sh" > "$WORK/enable.out" 2>&1
else
  : > "$WORK/enable.out"
fi
say_capture "$INSTALL" "$WORK/enable.out" "enable"
ENABLED=$(cat "$WORK/enable.out")
said "$ENABLED" "enabling services" \
  && ok "a live box is told its services are being enabled" \
  || bad "the live arm of the service branch printed nothing about enabling \
services, so this machine could arrange neither a user namespace nor a \
/run/systemd/system and the branch was not run: $ENABLED"
said "$ENABLED" "(services enabled offline)" \
  && bad "the run with DESTDIR empty took the OFFLINE arm, so what was \
measured is the branch every other run in this file already takes" \
  || ok "and not the offline line, which is the other arm"

echo
echo "=== a live box whose initramfs hook changed rebuilds its initrd (#468) ==="
#
# The same shape as the box above, for the same reason: the branch runs only
# with DESTDIR empty, which is the machine itself, so it is taken out by its
# own lines and run in a mount namespace whose /etc is this suite's. Missed
# by 0.134.0, whose gate does not run `make test`: the two sentences below
# were printed by no box, and this suite said so on master after the release.
TRIM=$WORK/trim.sh
sed -n '/^_trim="\$DESTDIR\/etc\/initramfs-tools\/hooks\/prisma-trim"$/,/^fi$/p' \
  "$REPO/appliance/install.sh" > "$TRIM"
if grep -q "rebuilding the initrd" "$TRIM"; then
  ok "the initrd-rebuilding branch is $(wc -l < "$TRIM") lines of install.sh"
else
  bad "the branch that rebuilds the initrd could not be found in \
appliance/install.sh, so the checks below are running on nothing"
fi
{ echo 'DESTDIR=""'; echo "REPO='$REPO'"; echo 'say() { echo "==> $*"; }'
  cat "$TRIM"; } > "$WORK/drive-trim.sh"
TSTUB=$WORK/tbin
mkdir -p "$TSTUB"
printf '#!/bin/sh\necho ran >> "$UIR_LOG"\nexit "${UIR_RC:-0}"\n' \
  > "$TSTUB/update-initramfs"
chmod +x "$TSTUB/update-initramfs"
trim_box() {  # <hook already there: old|same|none> <update-initramfs rc> <out>
  mkdir -p "$WORK/fake-etc-$1-$2/initramfs-tools/hooks"
  case "$1" in
    old) echo "# 0.132.0's hook" > "$WORK/fake-etc-$1-$2/initramfs-tools/hooks/prisma-trim" ;;
    same) cp "$REPO/appliance/initramfs/prisma-trim" \
            "$WORK/fake-etc-$1-$2/initramfs-tools/hooks/prisma-trim" ;;
  esac
  : > "$WORK/uir-$1-$2.log"
  PATH="$TSTUB:$PATH" UIR_RC=$2 UIR_LOG="$WORK/uir-$1-$2.log" \
    unshare -rm sh -c 'mount --bind "$1" /etc || exit 97; exec sh "$2"' \
    sh "$WORK/fake-etc-$1-$2" "$WORK/drive-trim.sh" > "$3" 2>&1
}
if unshare -rm true 2>/dev/null; then
  trim_box old 0 "$WORK/trim-old.out"
  say_capture "$INSTALL" "$WORK/trim-old.out" "trim-changed"
  said "$(cat "$WORK/trim-old.out")" "the initramfs hook changed: rebuilding the initrd" \
    && [ -s "$WORK/uir-old-0.log" ] \
    && ok "a box holding 0.132.0's hook is told, and update-initramfs runs" \
    || bad "a changed hook did not rebuild the initrd: $(cat "$WORK/trim-old.out")"
  trim_box old 1 "$WORK/trim-fail.out"
  say_capture "$INSTALL" "$WORK/trim-fail.out" "trim-failed"
  said "$(cat "$WORK/trim-fail.out")" "update-initramfs failed; the hook applies at the next kernel update" \
    && ok "and a failed rebuild is said, not hidden" \
    || bad "a failed update-initramfs went unsaid: $(cat "$WORK/trim-fail.out")"
  trim_box same 0 "$WORK/trim-same.out"
  [ ! -s "$WORK/uir-same-0.log" ] && ! said "$(cat "$WORK/trim-same.out")" "rebuilding" \
    && ok "an unchanged hook rebuilds nothing, and says nothing" \
    || bad "an unchanged hook rebuilt the initrd: $(cat "$WORK/trim-same.out")"
else
  bad "no user namespace here, so the initrd branch of install.sh was not run"
fi

echo
echo "=== a tree that carries the container images (#427) ==="
# What an upgrade artifact is since 0.125.0, and what the ISO's chroot is: the
# images at the top of the tree. On a root of its own, so every run above and
# below keeps the root that has none - a box upgraded from before the images
# existed, which is what most of them are about.
head -c 4096 /dev/urandom > "$TREE/prisma-routing-images.tar.gz"
_SAVED_DEST=$DEST
DEST=$WORK/root-images
mkdir -p "$DEST/etc/frr"
cp "$_SAVED_DEST/etc/frr/daemons" "$DEST/etc/frr/daemons"
if install_run "$NEW_V" "$WORK/run-images"; then
  said "$(cat "$WORK/run-images")" "the container images" \
    && ok "it says it is installing them" \
    || bad "it installed from a tree with images and did not say so"
  cmp -s "$TREE/prisma-routing-images.tar.gz" \
         "$DEST/usr/lib/prisma-routing/images.tar.gz" \
    && ok "and they are where load-the-images reads them" \
    || bad "the images did not reach usr/lib/prisma-routing/images.tar.gz"
  said "$(cat "$WORK/run-images")" "no container images in this root" \
    && bad "and then said the root has none" \
    || ok "and it does not then say the root has none"
else
  bad "the installer failed on a tree carrying images:"
  sed 's/^/        /' "$WORK/run-images"
fi
DEST=$_SAVED_DEST
rm -f "$TREE/prisma-routing-images.tar.gz"

echo
echo "=== and this suite is reading something ==="
# Without this the whole file passes on an install.sh that prints nothing at
# all, which is the shape #151 spent an issue on: every needle above that
# matters is a positive, but four of them are negatives and a silent script
# satisfies all four.
N1=$(printf '%s\n' "$RUN1" | wc -l)
N2=$(printf '%s\n' "$RUN2" | wc -l)
if [ "$N1" -ge 12 ] && [ "$N2" -gt "$N1" ]; then
  ok "$N1 lines on the first install, $N2 on the upgrade: it got longer, and \
neither run was empty"
else
  bad "the first install printed $N1 line(s) and the upgrade $N2. Either the \
installer is saying nothing, or the upgrade added nothing to what a live box \
gets on a first install, and the assertions above are being satisfied by \
silence"
fi
# And the same question of the driven screens, whose negatives are satisfied
# by a driver that returns nothing at all.
NF=$(printf '%s\n' "$FIRST" | wc -l)
NU=$(printf '%s\n' "$UP" | wc -l)
if [ "$NF" -ge 3 ] && [ "$NU" -gt "$NF" ]; then
  ok "the first install screen is $NF lines and the upgrade screen $NU: the \
driver above produced both of them"
else
  bad "the first install screen came out $NF line(s) and the upgrade screen \
$NU. The driver is producing nothing, and every negative check above passes \
on nothing"
fi

# ------------------------------------------------- what it can say, and said
#
# THE CEILINGS, and they may only go down. Not an allowlist: what is left over
# is named on every run with the line it is on, and every row that gains a fix
# lowers one of these. SAY_OVER_AT_MOST is rows that do not fit the console at
# the widest value that can reach them; SAY_UNSAFE_AT_MOST is rows carrying a
# value with no widest and the program's own words behind it - which here is
# the $DESTDIR an operator names, printed with "(services enabled offline)"
# after it.
SAY_OVER_AT_MOST=0
SAY_UNSAFE_AT_MOST=1
# ONE printer this pass's boundary rule cannot see, and it hides nothing:
# `for _f in $YOUR_FILES; do echo "# yours: $_f"; done` at install.sh:579,
# inside the brace group that writes the MANIFEST to a file. It is not a
# console row, so nothing is missing from the count above - but it is exactly
# the shape that WOULD hide one somewhere else, so it is held at one rather
# than excused.
SAY_UNSEEN_AT_MOST=1
say_report appliance/install.sh
rm -rf "$SAY_DIR"

if [ "$fail" -eq 0 ]; then
  echo
  echo "ALL TESTS PASSED"
  exit 0
fi
echo
echo "$fail FAILURES"
exit 1
