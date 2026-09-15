#!/bin/sh
# The upgrade an operator can actually perform, driven end to end.
#
# THE MEASUREMENT THIS EXISTS FOR. On a real appliance installed from a
# published medium (#295):
#
#   # ls /usr/lib/hexarouting/
#   apply-tuning  bind-dataplane-nic  config.py  hexash.py  ...
#   # find / -name install.sh -path '*hexarouting*'
#   (nothing)
#   # command -v cmake ninja gcc
#   (nothing)
#
# appliance/install.sh's own line 4 calls itself the path "for updating a box
# in production without reinstalling it", and it copies every file out of
# $REPO. There was no $REPO on that box and no toolchain to make one, so the
# path served tools/release-gate.sh and lab/upgrade.sh - which have a build
# tree - and nobody who bought the product. Their route to a new version was a
# reinstall, which erases the disk and takes a year of connection records with
# it.
#
# appliance/build-upgrade.sh packs that $REPO with the build already in it and
# appliance/upgrade runs install.sh out of it. This drives both.
#
# WHY A SUITE AND NOT A LAB. There is no lab here at all, and that is
# deliberate: an upgrade is thirty file copies and two refusals, none of which
# needs a dataplane, and every one of them is reachable in a directory. What
# DOES need a booted box is the question of whether the upgraded box still
# forwards, and lab/upgrade.sh has asked that since it was written - by running
# install.sh out of a source tree, because until now that was the only place it
# could run.
#
# WHY A STAND-IN TREE. build-upgrade.sh wants a compiled bpa_plugin.so that
# exists only after a build, and it copies four directories, so it cannot be
# pointed at a symlink farm the way test/test_manifest.sh points install.sh.
# The tree here is a real copy of this repository's four payload directories
# plus two stand-in plugin files. Nothing is written into the source tree or
# onto this machine.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

TREE=$WORK/tree
STAMP="0.75.0 (beefcaf)"

# The stamp is PASSED IN rather than computed, and this is the supported
# override that build-iso.sh and tools/release-gate.sh both use. A copied tree
# has no .git, so version-stamp.sh would answer "0.74.2 (not a git tree)" here
# - which is a real answer for a real situation and not the one this suite is
# about. What it is about is whether the stamp reaches the box at all.

fresh_tree() {
  rm -rf "$TREE"
  mkdir -p "$TREE/.build-vpp/lib/vpp_plugins" "$TREE/.build-vpp/lib/vat2_plugins"
  cp -a "$REPO/src" "$REPO/appliance" "$REPO/tools" "$REPO/docs" "$TREE/"
  cat "$REPO/VERSION" > "$TREE/VERSION"
  # Sized like the real ones, so the artifact this suite weighs is the weight
  # of the thing an operator downloads. bpa_plugin.so measured 1085448 bytes
  # and the vat2 plugin 50160 on the appliance #295 was written from.
  head -c 1085448 /dev/zero > "$TREE/.build-vpp/lib/vpp_plugins/bpa_plugin.so"
  head -c 50160  /dev/zero > "$TREE/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so"
}

build() {  # -> $ART, the unpacked artifact directory; $BUILD_OUT, what it said
  _tar=$WORK/artifact.tar.gz
  rm -rf "$WORK/unpacked"; mkdir -p "$WORK/unpacked"
  BUILD_OUT=$(OUT="$_tar" HEXA_VERSION_STAMP="$STAMP" \
              sh "$TREE/appliance/build-upgrade.sh" 2>&1)
  BUILD_RC=$?
  [ "$BUILD_RC" = 0 ] || return 1
  tar xzf "$_tar" -C "$WORK/unpacked" || return 1
  ART=$WORK/unpacked/$(ls "$WORK/unpacked")
  return 0
}

# A BOX, and it is a directory with a package database in it.
#
# appliance/upgrade asks dpkg which VPP and FRR are installed, because dpkg is
# what installed them and its database is what says so. Standing a box up
# therefore means writing that database, which is cheap and is the real file
# format rather than a stub of it - a stub would let this suite agree with
# itself about a question the product asks the system.
#
# /etc/frr is the frr package's directory. install.sh edits the file in it in
# place and cannot create the directory, so a box without it is an incomplete
# fixture rather than a defect.
fresh_box() {  # <vpp version> <frr version>
  rm -rf "$BOX"
  mkdir -p "$BOX/etc/frr" "$BOX/var/lib/dpkg"
  for p in "vpp $1" "frr $2"; do
    set -- $p
    cat >> "$BOX/var/lib/dpkg/status" <<EOF
Package: $1
Status: install ok installed
Priority: optional
Section: net
Architecture: amd64
Version: $2
Maintainer: HexaRouting test fixture <nobody@example.invalid>
Description: stand-in for the $1 the medium installs

EOF
  done
  printf 'bgpd=no\nospfd=no\nospf6d=no\nisisd=no\n' > "$BOX/etc/frr/daemons"
  printf 'bgpd_options="  -A 127.0.0.1"\n' >> "$BOX/etc/frr/daemons"
}
BOX=$WORK/box

# THROUGH THE NAME AN OPERATOR TYPES, which is the one at the root of the
# artifact. Driving appliance/upgrade here instead would leave the root entry
# point - the only one the wiki names - with no check on it at all.
upgrade() {  # -> $OUTPUT and $RC; runs the artifact against $BOX
  OUTPUT=$(DESTDIR="$BOX" sh "$ART/upgrade" 2>&1)
  RC=$?
}

# The versions the medium pins, read here with a different expression from the
# one build-upgrade.sh uses. Two readings of one line is the only way this can
# say anything: a check that extracted the value the same way the product does
# would agree with the product about a value neither of them had read.
iso_pin() {  # <variable>
  sed -n "s/^$1=\${$1:-\(.*\)}\$/\1/p" "$REPO/appliance/build-iso.sh" | head -1
}

fresh_tree

echo "=== the artifact is built from a tree, with no root and no container ==="
if build; then
  ok "appliance/build-upgrade.sh produced $(basename "$ART").tar.gz"
else
  bad "the build failed, so nothing below was measured:"
  printf '%s\n' "$BUILD_OUT" | sed 's/^/        /'
  echo
  echo "1 FAILURES"
  exit 1
fi

# The size, because the number is the argument. An operator who wants a new
# version is told to download a 325 MB medium and erase the disk under a year
# of connection records; what actually changes is this.
SZ=$(wc -c < "$WORK/artifact.tar.gz")
if [ "$SZ" -gt 100000 ] && [ "$SZ" -lt 20000000 ]; then
  ok "$((SZ / 1024)) KB against a 325 MB medium"
else
  bad "the artifact is $SZ bytes, which is not the size of this product"
fi

echo
echo "=== the entry point is at the root, where somebody who unpacked looks ==="
# AND IT IS ONE FILE, not two. The wiki tells an operator to unpack and run
# `sudo sh upgrade`; before this, it had to tell them `sudo sh
# appliance/upgrade`, which is a path into this product's source layout and
# the only thing on that page asking them to know one. A copy at the root
# would be a second implementation to keep true, so it is a symlink and
# appliance/upgrade resolves the artifact by finding RELEASE rather than by
# counting directories up from itself.
[ -L "$ART/upgrade" ] \
  && ok "the artifact carries 'upgrade' at its root, as a symlink" \
  || bad "the artifact has no 'upgrade' at its root, so the wiki's own command \
does not exist: $(ls -a "$ART" | tr "\n" " ")"
[ "$(readlink "$ART/upgrade" 2>/dev/null)" = appliance/upgrade ] \
  && ok "and it points at the one implementation" \
  || bad "the root entry point is not the same file as appliance/upgrade, so \
there are two: $(readlink -f "$ART/upgrade" 2>/dev/null)"
# And the page does not teach a source path anyway. Read here rather than in
# the English-only or documentation suites because it is this artifact's
# layout that decides whether the page can avoid one.
grep -q "sudo sh upgrade" "$REPO/wiki/operar/levar-a-caixa-para-uma-versao-nova.md" \
  && ok "and the page an operator reads names that command" \
  || bad "the wiki does not tell an operator to run the root entry point"
grep -q "appliance/" "$REPO/wiki/operar/levar-a-caixa-para-uma-versao-nova.md" \
  && bad "the wiki page names a path inside this product's source tree, which \
is the one thing on it that asks an operator to know the layout" \
  || ok "and it names no path inside the source tree"

echo "=== and a box takes it ==="
fresh_box "$(iso_pin VPP_VERSION)" "$(iso_pin FRR_VERSION)"
upgrade
if [ "$RC" = 0 ]; then
  ok "appliance/upgrade ran on a box with no repository and no toolchain"
else
  bad "the upgrade failed on a box whose platform matches:"
  printf '%s\n' "$OUTPUT" | sed 's/^/        /'
fi
# The dataplane plugin is the file the whole exercise is about: #295's own
# words are that the box says an upgrade "replaces bpa_plugin.so on disk", and
# it described a state no operator could reach.
if [ -f "$BOX/usr/lib/x86_64-linux-gnu/vpp_plugins/bpa_plugin.so" ]; then
  ok "the new bpa_plugin.so is on the box"
else
  bad "no bpa_plugin.so reached the box, so nothing was upgraded"
fi
if [ -f "$BOX/usr/lib/x86_64-linux-gnu/vat2_plugins/bpa_test_plugin_bpa_plugin.so" ]; then
  ok "and the vat2 plugin beside it, so the binary API is registered (#119)"
else
  bad "the vat2 plugin did not reach the box: hexash could not configure a pool"
fi
if [ -x "$BOX/usr/bin/hexash" ] && [ -f "$BOX/usr/lib/hexarouting/config.py" ]; then
  ok "the VTY and the module it imports both travelled"
else
  bad "the VTY did not reach the box whole, which is #119's shape on the other \
component: hexash would raise ModuleNotFoundError on every start"
fi

echo
echo "=== the box is stamped what the artifact says, not what git said ==="
# WHY THIS IS A CHECK OF ITS OWN. install.sh asks tools/version-stamp.sh for
# the line it writes into /usr/share/hexarouting/VERSION, and that calculation
# reads git. There is no .git inside a tarball and there never can be. Left to
# itself install.sh stamps the box "0.75.0 (not a git tree)", and that line is
# what `show version` prints, what the boot self-test puts at the top of its
# report and the first thing anybody is asked on the telephone - so the box
# would be unable to name the build it is running, which is the whole of #302,
# put there by the thing that upgraded it.
GOT=$(cat "$BOX/usr/share/hexarouting/VERSION" 2>/dev/null || echo "")
if [ "$GOT" = "$STAMP" ]; then
  ok "/usr/share/hexarouting/VERSION says $GOT"
else
  bad "the box is stamped '$GOT' and the artifact is '$STAMP'"
fi
case $GOT in
  *git*|*unverified*|*unknown*)
    bad "the stamp on the box is git's failure to read a tree that is not \
there: '$GOT'" ;;
  *) ok "and it is a version, not a note about a missing repository" ;;
esac

echo
echo "=== a box on another platform is refused, and nothing is written ==="
# WHY THIS REFUSAL EXISTS. The artifact carries product files and nothing else.
# `grep -nE "^[[:space:]]*(apt|apt-get|dpkg)\b" appliance/install.sh` returns
# nothing, so it installs no packages, needs no network and no toolchain - and
# cannot move the VPP or FRR under the box. The plugin in it was compiled
# against the versions appliance/build-iso.sh pins, so a box running others has
# to be sent back to the medium.
#
# BEFORE ANYTHING IS WRITTEN is half the check and the more important half. An
# upgrade that stops in the middle leaves a box that is part one version and
# part another, and the half that would be wrong is the one nothing on the box
# reads - the shape #301 and #293 are both about. A refusal is only worth
# taking while it still costs nothing.
fresh_box 25.02-release "$(iso_pin FRR_VERSION)"
upgrade
if [ "$RC" != 0 ]; then
  ok "a box on vpp 25.02-release is refused"
else
  bad "a box on vpp 25.02-release took an artifact built against \
$(iso_pin VPP_VERSION)"
fi
case "$OUTPUT" in
  *25.02-release*"$(iso_pin VPP_VERSION)"*)
    ok "and the refusal names both versions, so the operator knows which" ;;
  *) bad "the refusal does not name the version the box has against the one \
this release wants: $OUTPUT" ;;
esac
WROTE=$(find "$BOX/usr" "$BOX/lib" -type f 2>/dev/null | head -3)
if [ -z "$WROTE" ]; then
  ok "nothing was written: the box is still whatever it was this morning"
else
  bad "the refusal came after the box had already been changed:"
  printf '  %s\n' $WROTE
fi

fresh_box "$(iso_pin VPP_VERSION)" 9.1-0
upgrade
if [ "$RC" != 0 ]; then
  ok "and so is a box on another FRR"
else
  bad "a box on frr 9.1-0 took an artifact built against $(iso_pin FRR_VERSION)"
fi

echo
echo "=== a box that is not this product at all is refused ==="
# Distinguished from the mismatch above on purpose: "no vpp package" is a
# machine that is not an appliance, and telling its owner that their version is
# wrong would send them looking for an upgrade of something they do not have.
rm -rf "$BOX"; mkdir -p "$BOX/etc/frr" "$BOX/var/lib/dpkg"
: > "$BOX/var/lib/dpkg/status"
upgrade
case "$OUTPUT" in
  *"no vpp package"*) ok "a box with no VPP installed is told that, and not \
that its version is wrong" ;;
  *) bad "a box with no VPP installed was answered with: $OUTPUT" ;;
esac
[ "$RC" != 0 ] || bad "and it was not refused at all"

echo
echo "=== an artifact with no RELEASE is refused rather than guessed at ==="
# RELEASE is what makes an unpacked directory an artifact. Without it there is
# no stamp to write and no platform to compare, and carrying on would install a
# box stamped from a git tree that is not there.
mv "$ART/RELEASE" "$WORK/RELEASE.aside"
fresh_box "$(iso_pin VPP_VERSION)" "$(iso_pin FRR_VERSION)"
upgrade
if [ "$RC" != 0 ]; then
  ok "refused"
else
  bad "an artifact with no RELEASE installed anyway, stamping the box \
'$(cat "$BOX/usr/share/hexarouting/VERSION" 2>/dev/null)'"
fi
case "$OUTPUT" in
  *install.sh*) ok "and it says what to run in a source tree instead" ;;
  *) bad "the refusal does not tell a reader in a source tree what to run: \
$OUTPUT" ;;
esac
# Moved aside rather than deleted, because the section below reads it. An
# artifact this suite has damaged on purpose has to be whole again before
# anything else is asked of it.
mv "$WORK/RELEASE.aside" "$ART/RELEASE"

echo
echo "=== the payload is what the medium installs from ==="
# ONE RULE ABOUT WHAT THE INSTALL PATH IS MADE OF. build-iso.sh copies four
# directories into its chroot and runs install.sh there; build-upgrade.sh packs
# the same four and runs install.sh there. Two lists that have to agree and
# never meet are two lists that drift, and the drift here is an artifact that
# is missing whatever the new install line reads - discovered on an operator's
# box, halfway through.
ISO_DIRS=$(grep -m1 '^cp -a "\$REPO/src"' "$REPO/appliance/build-iso.sh" \
           | tr ' ' '\n' | sed -n 's|^"\$REPO/\([a-z]*\)"$|\1|p' | sort)
ART_DIRS=$(cd "$ART" && find . -maxdepth 1 -mindepth 1 -type d \
           ! -name .build-vpp | sed 's|^\./||' | sort)
if [ -n "$ISO_DIRS" ] && [ "$ISO_DIRS" = "$ART_DIRS" ]; then
  ok "both carry $(echo "$ISO_DIRS" | tr '\n' ' ')"
else
  bad "the medium installs from [$(echo "$ISO_DIRS" | tr '\n' ' ')] and the \
artifact carries [$(echo "$ART_DIRS" | tr '\n' ' ')]"
fi

echo
echo "=== the platform pins have one owner ==="
# build-upgrade.sh reads them out of build-iso.sh. Two copies of a number whose
# only job is to be compared would eventually disagree, and the disagreement
# would read as an operator's box being wrong rather than as this repository
# being inconsistent with itself.
REL_VPP=$(sed -n 's/^vpp=//p' "$ART/RELEASE" 2>/dev/null)
REL_FRR=$(sed -n 's/^frr=//p' "$ART/RELEASE" 2>/dev/null)
if [ "$REL_VPP" = "$(iso_pin VPP_VERSION)" ] && \
   [ "$REL_FRR" = "$(iso_pin FRR_VERSION)" ]; then
  ok "vpp $REL_VPP and frr $REL_FRR, the versions the medium installs"
else
  bad "the artifact says vpp '$REL_VPP' frr '$REL_FRR'; the medium installs \
vpp '$(iso_pin VPP_VERSION)' frr '$(iso_pin FRR_VERSION)'"
fi

# And a rename of those variables has to fail HERE, where whoever renamed them
# is standing. An extraction that quietly came back empty would write a RELEASE
# nothing can compare against, and appliance/upgrade would then refuse every
# box or accept every box depending on which half broke.
sed -i 's/^VPP_VERSION=/VPP_PKG_VERSION=/' "$TREE/appliance/build-iso.sh"
if build; then
  bad "build-upgrade.sh packed an artifact after the pin it reads was renamed \
out from under it"
else
  case "$BUILD_OUT" in
    *"could not read the VPP and FRR versions"*)
      ok "renaming VPP_VERSION in build-iso.sh is refused, by name" ;;
    *) bad "the build failed for some other reason: $BUILD_OUT" ;;
  esac
fi
fresh_tree

echo
echo "=== half a build is refused, the way install.sh and build-iso.sh do ==="
# The two plugins are ONE build product: tools/build.sh compiles them in one
# ninja run, so a tree with one of them is a build that did not finish. #119 is
# what a medium without the vat2 plugin cost - the binary API unregistered on
# every box, and no CGNAT pool configurable on the one thing the appliance
# exists to do - and an artifact without it would do that to a box that was
# working this morning.
rm -f "$TREE/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so"
if build; then
  bad "an artifact was packed with no vat2 plugin in it"
else
  case "$BUILD_OUT" in
    *bpa_test_plugin_bpa_plugin.so*)
      ok "refused, naming the file that is missing" ;;
    *) bad "the build failed without saying which half is missing: $BUILD_OUT" ;;
  esac
fi
fresh_tree

# AND THE OTHER HALF OF THE PAIR. Both plugins come out of one ninja run, so
# "one and not the other" has two spellings and only one of them had ever been
# driven. This is the one that reads as an empty build tree rather than as a
# half-finished one, and it is the likelier of the two: a clean checkout, a
# container that was rebuilt, a .build-vpp somebody removed to reclaim space.
rm -f "$TREE/.build-vpp/lib/vpp_plugins/bpa_plugin.so"
if build; then
  bad "an artifact was packed with no dataplane plugin in it at all: every \
box taking it would be stamped the new version over the plugin it already had"
else
  case "$BUILD_OUT" in
    *"bpa_plugin.so not found"*)
      ok "and a tree with no dataplane plugin is refused, naming it" ;;
    *) bad "the build failed without naming the missing plugin: $BUILD_OUT" ;;
  esac
fi
fresh_tree

echo
echo "=== the artifact is installed once before it is published ==="
#
# THE PROOF build-upgrade.sh CALLS ITS OWN, and nothing had ever made it fail.
# An artifact that is missing one path is an artifact whose failure happens on
# an operator's box, at the hour they chose to upgrade, with part of it already
# overwritten - install.sh's preflight can refuse a missing plugin but nothing
# can refuse a tree with no src/hexash/config.py until the line that copies it
# runs. So every artifact is unpacked and installed into a throwaway root
# before the tarball is written, and that step is only worth having if it can
# say no.
#
# A file the installer copies BY NAME and no preflight looks at, removed from
# the tree. It is not a plugin and not a version stamp: it is one of the
# thirty-odd ordinary files, which is the whole point - the check is that the
# tree is COMPLETE, and completeness cannot be asserted by a list.
#
# By name, and that word is load-bearing. src/hexash/*.py is installed through
# a GLOB, deliberately (install.sh:251, which says why: naming those files one
# by one shipped a box with no config.py in 0.2.0). A glob over a file that is
# not there installs one file fewer and exits 0, so it is not the case this
# proof can catch - measured, removing src/hexash/config.py from the tree
# still packed an artifact, "31 file(s) installed", and would have published
# it.
rm -f "$TREE/appliance/wait-for-vpp"
# Removed first, because build() writes to a fixed path and the successful
# builds above have already left one there. Without this the assertion below
# would be reading yesterday's tarball.
rm -f "$WORK/artifact.tar.gz"
if build; then
  bad "an artifact was packed and published with appliance/wait-for-vpp \
missing from it. The operator's box would take the upgrade, overwrite part of \
itself, and stop on a path nothing had ever looked for"
else
  case "$BUILD_OUT" in
    *"the artifact is incomplete"*)
      ok "a tree that cannot be installed does not become a tarball" ;;
    *) bad "the build failed, but not on its own proof: $BUILD_OUT" ;;
  esac
fi
# WHAT THE INSTALL SAID, quoted into the build log. "Installing it failed" on
# its own sends whoever is building to run the install by hand to find out
# which path; the tail of the log names it, and the path is the fix.
case "$BUILD_OUT" in
  *wait-for-vpp*)
    ok "and the build log names the path that could not be installed" ;;
  *) bad "the refusal quoted none of the install log, so the missing file is \
not named anywhere: $BUILD_OUT" ;;
esac
# And no tarball, which is the thing that would otherwise be published. A
# refusal that leaves the artifact on disk is a refusal somebody uploads past.
[ ! -f "$WORK/artifact.tar.gz" ] \
  && ok "and no tarball was left behind for anybody to publish" \
  || bad "the build refused and wrote $WORK/artifact.tar.gz anyway"
fresh_tree
build || bad "the tree could not be rebuilt after the cases above, so \
anything below this line is measuring a broken fixture"

echo
echo "=== an artifact that cannot say what it is (#295) ==="
#
# RELEASE carries three lines and appliance/upgrade reads all three: the stamp
# it writes into /usr/share/hexarouting/VERSION, and the two platform versions
# it compares against the box. A file with one of them missing is a DAMAGED
# artifact - a truncated download, an edit, a tar that was interrupted - and
# every one of the three has a different box at the end of it.
#
# Left to itself a missing "stamp" line stamps the box with an empty version,
# which is #302: a box that cannot name the build it is running. A missing
# "vpp" or "frr" line compares the box's version against nothing and either
# accepts every box or refuses every box, depending which way the comparison
# falls. All three go through one refusal and none of them had ever run it.
fresh_box "$(iso_pin VPP_VERSION)" "$(iso_pin FRR_VERSION)"
cp "$ART/RELEASE" "$WORK/RELEASE.whole"
for _f in stamp vpp frr; do
  grep -v "^$_f=" "$WORK/RELEASE.whole" > "$ART/RELEASE"
  upgrade
  if [ "$RC" = 0 ]; then
    bad "an artifact with no '$_f=' line installed anyway, stamping the box \
'$(cat "$BOX/usr/share/hexarouting/VERSION" 2>/dev/null)'"
  else
    case "$OUTPUT" in
      *"no '$_f=' line"*)
        ok "a RELEASE with no '$_f=' line is refused, and the line is named" ;;
      *) bad "refused without saying which line is missing: $OUTPUT" ;;
    esac
  fi
  # It is DAMAGED and not wrong, which is a different thing to tell somebody:
  # there is nothing here for them to correct, and the answer is to fetch the
  # artifact again.
  case "$OUTPUT" in
    *"fetch it again"*) ;;
    *) bad "the refusal over a damaged artifact does not say to fetch it \
again, so the reader is left looking for what they did wrong: $OUTPUT" ;;
  esac
done
ok "and each of the three says the artifact is damaged rather than the box"
cp "$WORK/RELEASE.whole" "$ART/RELEASE"

echo
echo "=== the box has to have a package database to be asked about (#295) ==="
# The platform check is ASKED OF THE BOX, and the thing it asks is dpkg. A
# root with no /var/lib/dpkg in it is not an appliance: it is a chroot, a
# half-unpacked image, or a DESTDIR somebody pointed at an empty directory.
# Answering "no vpp package installed" there would be true and useless - the
# reason is that nothing was asked at all, and the two have different fixes.
rm -rf "$BOX"; mkdir -p "$BOX/etc/frr"
upgrade
[ "$RC" != 0 ] \
  && ok "a root with no package database is refused" \
  || bad "an upgrade ran against a root nothing could be asked about"
case "$OUTPUT" in
  *"no package database at"*)
    ok "and says that is what is missing, not that the versions are wrong" ;;
  *) bad "a root with no dpkg database was answered with: $OUTPUT" ;;
esac

echo
echo "=== and something has to be there to ask it with (#295) ==="
# The database is one half of "ask the box"; dpkg-query is the other, and it
# is the half that is not a file this artifact can look for. A root with a
# /var/lib/dpkg and no dpkg-query on PATH is a container, a rescue shell, or a
# box somebody has taken apart - and answering "no vpp package installed"
# there would be a sentence about the box when the truth is that nothing was
# asked at all.
#
# ARRANGED WITH A PATH THAT DOES NOT CONTAIN IT. `command -v` searches PATH,
# so a tool cannot be hidden by putting something in front of it: the run has
# to be handed a PATH it is not on. This one holds the four commands
# appliance/upgrade uses before that line - sh to start it, dirname to find
# the artifact, sed and head to read RELEASE - and nothing else.
mini=$WORK/mini; rm -rf "$mini"; mkdir -p "$mini"
for t in sh dirname sed head; do ln -s "$(command -v "$t")" "$mini/$t"; done
fresh_box "$(iso_pin VPP_VERSION)" "$(iso_pin FRR_VERSION)"
OUTPUT=$(DESTDIR="$BOX" env PATH="$mini" sh "$ART/upgrade" 2>&1); RC=$?
# WHICH SENTENCE, because refusing is not the half that is in doubt. Without
# this check `have` comes back empty for both packages and the box is told it
# has no VPP installed - a refusal, and a true-sounding one, about a database
# this fixture is carrying with the right versions in it. Its owner would go
# looking for a VPP that is on the disk.
case "$OUTPUT" in
  *"no dpkg-query here"*)
    [ "$RC" != 0 ] \
      && ok "a root with nothing to ask dpkg with is refused, and says so" \
      || bad "it said there was no dpkg-query and ran install.sh anyway" ;;
  *"no vpp package installed"*)
    bad "a box nothing could ask was told it is not an appliance at all, \
about a package database that has vpp $(iso_pin VPP_VERSION) in it: $OUTPUT" ;;
  *) bad "a root with no dpkg-query was answered with: $OUTPUT" ;;
esac
# THE PROOF THAT THE PATH IS NOT WHAT REFUSED. The same four commands with a
# dpkg-query added has to get PAST that line, and the only way to see that it
# did is a refusal from further down. The stub answers a version no release
# was ever built against, so it is the platform check that stops it - which is
# the sentence this case is telling its own apart from.
cat > "$mini/dpkg-query" <<'DQ'
#!/bin/sh
echo "0.0.0-not-a-release"
DQ
chmod +x "$mini/dpkg-query"
OUTPUT=$(DESTDIR="$BOX" env PATH="$mini" sh "$ART/upgrade" 2>&1); RC=$?
case "$OUTPUT" in
  *"no dpkg-query here"*)
    bad "the control run had a dpkg-query on its PATH and was told there was \
none, so this case's PATH is not what it says it is: $OUTPUT" ;;
  *"0.0.0-not-a-release"*)
    ok "and with one on the same PATH the run reaches the platform check" ;;
  *) bad "the control run stopped somewhere else entirely, so the four \
commands above are not enough to reach the platform check: $OUTPUT" ;;
esac

echo
echo "=== and it will not write into / as an ordinary user ==="
# install.sh writes into /usr, /lib and /etc and checks nothing: a non-root run
# gets some way in and then stops on the first permission error, leaving a box
# that is half one version and half another. The check is here because this is
# the file that knows the run is about a box at all - with DESTDIR set,
# something is driving this into a directory and root is neither needed nor
# wanted, which is how every other case in this suite runs.
#
# THE INTERLOCK, because this case deliberately unsets DESTDIR and so names
# the machine running the suite as the target. A dpkg-query of its own, first
# on PATH, answers with a version no release was ever built against - so a run
# that got past the root check would be refused by the platform check a few
# lines later and could not reach install.sh on this machine whatever it is
# installed on. The assertion is on the message, which is what tells the two
# refusals apart.
if [ "$(id -u)" = 0 ]; then
  echo "  SKIP: this suite is running as root"
else
  guard=$WORK/guard; mkdir -p "$guard"
  cat > "$guard/dpkg-query" <<'DQ'
#!/bin/sh
echo "0.0.0-not-a-release"
DQ
  chmod +x "$guard/dpkg-query"
  OUTPUT=$(PATH="$guard:$PATH" sh "$ART/upgrade" 2>&1); RC=$?
  [ "$RC" != 0 ] \
    && ok "an upgrade of this machine by an ordinary user is refused" \
    || bad "appliance/upgrade ran install.sh over / as an ordinary user"
  case "$OUTPUT" in
    *"must run as root"*)
      ok "and says so before it has asked the box anything" ;;
    *) bad "refused for some other reason, so the root check is not what \
stopped it: $OUTPUT" ;;
  esac
fi

echo
if [ "$fail" -gt 0 ]; then
  echo "$fail FAILURES"
  exit 1
fi
echo "ALL TESTS PASSED"
