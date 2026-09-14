#!/bin/sh
# What appliance/install.sh SAYS on a second run, driven by running it twice.
#
# For the whole life of this product the installer behaved identically on
# every run and closed with a first install's sentence: "Installed. Adjust
# /etc/hexarouting/tune.env and reboot so that isolcpus and hugepages take
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
  DESTDIR="$DEST" HEXA_VERSION_STAMP="$1" PLUGIN_DIR=/usr/lib/vpp_plugins \
    VAT2_DIR=/usr/lib/vat2_plugins \
    sh "$TREE/appliance/install.sh" > "$2" 2>&1
}

# The same run with the two streams kept apart, and the exit status returned
# rather than swallowed. #301 is a defect about WHICH STREAM a sentence went
# to and what the exit status was beside it, so a runner that merges them
# cannot ask the question: a warning on stderr followed by exit 0 and a
# refusal on stderr followed by exit 1 are the same text in one file.
install_try() {  # <version stamp> <stdout file> <stderr file>
  DESTDIR="$DEST" HEXA_VERSION_STAMP="$1" PLUGIN_DIR=/usr/lib/vpp_plugins \
    VAT2_DIR=/usr/lib/vat2_plugins \
    sh "$TREE/appliance/install.sh" > "$2" 2> "$3"
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

[ "$(cat "$DEST/usr/share/hexarouting/VERSION" 2>/dev/null)" = "$OLD_V" ] \
  && ok "and stamped the box $OLD_V" \
  || bad "the version stamp is not $OLD_V, so the fixture below is not an \
upgrade at all: $(cat "$DEST/usr/share/hexarouting/VERSION" 2>/dev/null)"

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
echo "HEXA_SUBSCRIBERS=40000" >> "$DEST/etc/hexarouting/tune.env"
echo "! an operator's line" >> "$DEST/etc/hexarouting/hexarouting.conf"

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

[ "$(cat "$DEST/usr/share/hexarouting/VERSION" 2>/dev/null)" = "$NEW_V" ] \
  && ok "and the stamp on the box is the new one" \
  || bad "the box is not stamped $NEW_V after the upgrade"

echo
echo "=== what the second run must NOT have done to the operator's files ==="
grep -q "^HEXA_SUBSCRIBERS=40000$" "$DEST/etc/hexarouting/tune.env" \
  && ok "tune.env still has the line the operator put in it" \
  || bad "the upgrade overwrote /etc/hexarouting/tune.env, which is the file \
the closing sentence has always told the operator to edit"
grep -q "^! an operator's line$" "$DEST/etc/hexarouting/hexarouting.conf" \
  && ok "and hexarouting.conf is the configuration the box was carrying" \
  || bad "the upgrade overwrote /etc/hexarouting/hexarouting.conf, which is \
the whole configuration of the box"

echo
echo "=== and it is no noisier on the path that has no services ==="
# This root is a $DESTDIR, which is what lab/igp.sh and lab/oom.sh install
# into and the shape appliance/build-iso.sh assembles a medium in. There is no
# systemd under it and no process running out of it, so a paragraph about
# vpp.service still carrying traffic would be a paragraph about some other
# machine - the one running this suite.
for n in "vpp.service" "hexarouting.service" "systemctl restart" \
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
STAMP_FILE=$DEST/usr/share/hexarouting/VERSION

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
echo "=== the three screens only a running box can reach ==="
#
# install.sh says what it says about vpp.service and hexarouting.service only
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

screen() {  # <previous stamp> <live box 0|1> <active units, newline separated>
  printf '%s\n' "$3" > "$WORK/active"
  { echo "PREV_STAMP='$1'; stamp='$NEW_V'; LIVE_BOX=$2"; cat "$CLOSING"; } \
    > "$WORK/drive.sh"
  PATH="$STUB:$PATH" FAKE_ACTIVE="$WORK/active" sh "$WORK/drive.sh" 2>&1
}

BOTH_UP="vpp.service
hexarouting.service"

FIRST=$(screen "" 1 "")
said "$FIRST" "Installed. Adjust /etc/hexarouting/tune.env and reboot so \
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

UP=$(screen "$OLD_V" 1 "$BOTH_UP")
said "$UP" "Installed. Adjust /etc/hexarouting/tune.env and reboot" \
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
said "$UP" "hexarouting.service is active. It is a Type=oneshot" \
  && ok "hexarouting.service is named as the Type=oneshot it is" \
  || bad "the upgrade screen does not say that hexarouting.service is a \
Type=oneshot"
said "$UP" "re-applies /etc/hexarouting/hexarouting.conf through the new VTY \
and exits. It drops no session." \
  && ok "and restarting it is priced separately, at nothing" \
  || bad "the upgrade screen does not say what restarting hexarouting.service \
does or that it costs no session"
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
# toolchain. A closing sentence that told the operator to run this again would
# be sending them to a file that is not there.
said "$UP" "run this installer again" \
  && bad "the closing words tell the operator to re-run an installer that is \
not on their box (#295)" \
  || ok "and does not send the operator back to a file the box does not have"

DOWN=$(screen "$OLD_V" 1 "")
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

if [ "$fail" -eq 0 ]; then
  echo
  echo "ALL TESTS PASSED"
  exit 0
fi
echo
echo "$fail FAILURES"
exit 1
