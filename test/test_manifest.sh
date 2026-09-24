#!/bin/sh
# The list of what is on the box, written by one file and read by another.
#
# THE MEASUREMENT THIS EXISTS FOR. `show version` and the first line of the
# boot self-test print /usr/share/prisma-routing/VERSION, which is one sentence
# about the thirty-odd files appliance/install.sh puts on a box. Exactly one
# of them could answer for itself - #291 gave the dataplane plugin a build
# stamp - and the other thirty carried no version of any kind. So this, taken
# on a root installed by this repository before anything changed:
#
#   the box is stamped 0.70.0 (f10a3ed), /usr/bin/prisma-routing-tune is
#   v0.68.0's, and the self-test's forty-two lines are identical, line for
#   line, to the same box with the right file on it.
#
# That is #302. The appliance supplied for the #290 work was found in exactly
# that state and it got there honestly, by somebody copying one file onto it
# to prove a fix on a CPU they had.
#
# WHY A SUITE OF ITS OWN, AND WHAT EACH OF THE THREE HOLDS.
#
#   test/test_install_manifest.py reads appliance/install.sh as TEXT: that the
#   manifest is written below the last install line, and that a file the
#   operator owns is guarded in one place. Both are properties of the file and
#   neither survives being measured, because a line added tomorrow is not in
#   today's run.
#
#   test/test_selftest.sh drives appliance/selftest over a hundred synthetic
#   boxes. It cannot arrange a box whose files really are an installation,
#   because it never installs one.
#
#   This file is the join. It RUNS the installer into a root, and then runs
#   the self-test against that same root - so the two halves of the mechanism
#   are measured against each other rather than each against a fixture of its
#   own. A manifest whose format the self-test cannot read goes red here and
#   nowhere else.
#
# WHY A STAND-IN TREE. install.sh derives $REPO from its own path, copies out
# of it, and wants a compiled bpa_plugin.so that exists only after a build. So
# the tree is a directory of symlinks into this repository plus two stand-in
# plugin files, and nothing is written into the source tree or onto this
# machine. This is test/test_install_says.sh's fixture and the argument for it
# is the same one.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)

# SOURCED FOR ONE FUNCTION AND NO READER (#371). This suite runs the installer
# seven times and reads the MANIFEST it wrote, not the screen; the screen goes
# into a file and is looked at only when a run fails. Those files are most of
# what appliance/install.sh has ever printed, and until #371 nothing could keep
# them: SAY_CORPUS names a directory and say_capture puts each run in it, off
# unless somebody asks. No say_init here, so nothing in this file is measured
# by the coverage reader - test/test_install_says.sh arranges the boxes for
# that, and a second reader over one program would be a second count to
# believe.
. "$(cd "$(dirname "$0")" && pwd)/say-coverage.sh"
trap 'rm -rf "$WORK"' EXIT

TREE=$WORK/tree
DEST=$WORK/root
BIN=$WORK/bin
EVERY=$WORK/every-line

# A sentence, not the row it happened to fold on.
#
# Everything the self-test prints is folded to an 80-column console, and a
# needle written against the raw text matches only for as long as the message
# goes on breaking where it breaks today. This repository has paid for that
# seven times, twice in the last release. Both sides are collapsed, because
# these needles are themselves written across source lines.
said() {  # <extended regexp>
  printf '%s\n' "$OUTPUT" | tr -s '[:space:]' ' ' | grep -qE "$1"
}

# A version stamp is "0.72.0 (beefcaf)", and every one of those brackets and
# dots is a regexp operator. Written into a needle raw it matched a box that
# said "0x72a0 beefcaf" just as happily, and this suite's job is to prove the
# box names the version it is claiming - so the one place that matters is
# escaped once, here, rather than spelled by hand at each of the four needles.
re_quote() { printf '%s' "$1" | sed 's/[][\\.*^$(){}?+|/]/\\&/g'; }

# The self-test, run against the root the installer wrote, with the box's own
# surroundings stubbed out.
#
# The stubs are here so that this machine's answers do not reach the report: it
# may itself have a vpp.service, and a suite whose output depends on that reads
# differently on a developer box and on the build runner. They are deliberately
# thin - every one of them fails or says nothing - because the checks they feed
# are not what this file is about. Those are test/test_selftest.sh's.
mkdir -p "$BIN"
for c in vppctl vtysh systemctl journalctl findmnt df getent id runuser \
         setpriv vat2 snmpwalk; do
  printf '#!/bin/sh\nexit 1\n' > "$BIN/$c"
done
chmod +x "$BIN"/*

selftest() {  # -> $OUTPUT, and every line kept for the width check at the end
  OUTPUT=$(PATH="$BIN:$PATH" PRISMA_SELFTEST_OUT=/dev/stdout \
           PRISMA_SELFTEST_ROOT="$DEST" sh "$REPO/appliance/selftest" 2>/dev/null)
  printf '%s\n' "$OUTPUT" >> "$EVERY"
}

# The manifest as the two machine-readable halves the self-test uses: the
# files with a checksum, and the files named without one. Read with the same
# rule the self-test reads them by - a '#' line is not a checksum line - so
# that a manifest this suite can read and the self-test cannot does not exist.
checked()  { grep '^[^#]' "$DEST/usr/share/prisma-routing/MANIFEST" | awk '{print $2}'; }
yours()    { sed -n 's/^# yours: //p' "$DEST/usr/share/prisma-routing/MANIFEST"; }

fresh_tree() {
  rm -rf "$TREE"
  mkdir -p "$TREE/.build-vpp/lib/vpp_plugins" "$TREE/.build-vpp/lib/vat2_plugins"
  for e in src appliance tools docs ref VERSION; do
    [ -e "$REPO/$e" ] && ln -s "$REPO/$e" "$TREE/$e"
  done
  # A MILLION BYTES, not the word "stand-in". The cost of the check on a boot
  # is the last section of this file and it is the argument for putting the
  # check in the self-test at all, so the thing being timed has to be the size
  # of the thing a real box carries: bpa_plugin.so measured 1085408 bytes on
  # the appliance #302 was written from.
  head -c 1085408 /dev/zero > "$TREE/.build-vpp/lib/vpp_plugins/bpa_plugin.so"
  head -c 120000  /dev/zero > "$TREE/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so"
}

fresh_root() {
  rm -rf "$DEST"
  mkdir -p "$DEST/etc/frr"
  # The Debian package's own defaults. The installer edits this file and
  # appends to it, and a missing one stops the run under `set -e` before it
  # reaches anything this suite is about.
  { printf 'bgpd=no\nospfd=no\nospf6d=no\nisisd=no\n'
    printf 'bgpd_options="  -A 127.0.0.1"\n'; } > "$DEST/etc/frr/daemons"
}

install_run() {  # <version stamp>
  env DESTDIR="$DEST" PRISMA_VERSION_STAMP="$1" \
      PLUGIN_DIR=/usr/lib/vpp_plugins VAT2_DIR=/usr/lib/vat2_plugins \
      sh "$TREE/appliance/install.sh" > "$WORK/install.out" 2>&1
  # The status FIRST, because say_capture below is a command and would
  # otherwise be what `if install_run ...` reads. Every caller here tests it.
  _rc=$?
  _runs=$((${_runs:-0} + 1))
  say_capture "$REPO/appliance/install.sh" "$WORK/install.out" "run$_runs"
  return "$_rc"
}

fresh_tree
fresh_root
STAMP="0.72.0 (beefcaf)"

echo "=== the installer writes a list of what it put on the box ==="
if install_run "$STAMP"; then
  ok "appliance/install.sh ran into an empty root"
else
  bad "the installer failed, so nothing below was measured:"
  sed 's/^/        /' "$WORK/install.out"
  echo
  echo "1 FAILURES"
  exit 1
fi
if [ -f "$DEST/usr/share/prisma-routing/MANIFEST" ]; then
  ok "and left /usr/share/prisma-routing/MANIFEST beside the version stamp"
else
  bad "there is no manifest on the installed root, so every check below this \
one is about a box that cannot answer for itself"
  echo
  echo "1 FAILURES"
  exit 1
fi
NCHECKED=$(checked | grep -c .)
NYOURS=$(yours | grep -c .)
# Not a round number anybody typed: the point of the mechanism is that nobody
# maintains this count. The floor is there so that an installer whose wrapper
# has stopped recording - or a manifest of nothing but comments - cannot make
# every comparison below trivially true.
if [ "$NCHECKED" -ge 20 ] && [ "$NYOURS" -ge 2 ]; then
  ok "$NCHECKED file(s) with a checksum and $NYOURS named as the operator's"
else
  bad "the manifest has $NCHECKED checksummed file(s) and $NYOURS of the \
operator's, which is too few for anything below to be asserting much"
fi

echo
echo "=== and the list is of the root, not of a list somebody kept ==="
# THE CONSTRAINT THIS WHOLE DESIGN RESTS ON, measured rather than argued.
#
# The cheap manifest is a list of paths written beside the install lines. It
# is right on the day it is written and wrong the first time somebody adds an
# install line without thinking about it, and wrong SILENTLY: a manifest that
# does not name a file cannot report anything about that file. install.sh has
# no list - `install` is a shell function that records where it put things -
# and this is what holds that true. Every regular file under an installed root
# is in the manifest, or it is one of seven named below with a reason.
#
# A `cp` added to install.sh tomorrow goes red here, which is the direction
# that matters: the file would otherwise be on every box, unchecked, and
# nothing would say so.
( cd "$DEST" && find . -type f | sed 's|^\./||' | sort ) > "$WORK/on-root"
{ checked; yours; } | sort > "$WORK/in-manifest"
# usr/share/prisma-routing/VERSION is the claim itself rather than one of the
# files the claim is about, and a checksum of it would be a checksum of the
# line being questioned. MANIFEST cannot contain its own checksum.
# etc/frr/daemons is the Debian package's file, which this installer edits in
# place and did not put there - it belongs to frr, and reporting an frr
# upgrade as this product's corruption would be a check nobody believes.
# etc/issue, etc/issue.net, etc/motd and usr/lib/os-release are base-files'
# paths, rewritten by appliance/prisma-identity (#438) the same way: the first
# three are files an operator is entitled to edit - a login banner is the
# commonest local change there is - and the fourth is diverted, so it is ours
# only until somebody removes the diversion. None is a claim about the build.
cat > "$WORK/expected-absent" <<'ABSENT'
etc/frr/daemons
etc/issue
etc/issue.net
etc/motd
usr/lib/os-release
usr/share/prisma-routing/MANIFEST
usr/share/prisma-routing/VERSION
ABSENT
UNNAMED=$(comm -23 "$WORK/on-root" "$WORK/in-manifest" | sort)
if [ "$UNNAMED" = "$(cat "$WORK/expected-absent")" ]; then
  ok "every file on the root is in the manifest but the seven that cannot be"
else
  bad "the manifest and the root disagree about what was installed.
        on the root and not in the manifest, beyond the seven expected:
$(comm -23 "$WORK/on-root" "$WORK/in-manifest" | comm -23 - "$WORK/expected-absent" | sed 's/^/          /')
        expected and not found:
$(printf '%s\n' "$UNNAMED" | comm -13 - "$WORK/expected-absent" | sed 's/^/          /')"
fi
# The other direction, and it catches the thing that would make the whole
# manifest useless without making it look broken: a path that does not resolve
# checks nothing, and `sha256sum -c` reports it as a failure rather than as a
# manifest that is wrong. A destination with a space in it would arrive here
# as two paths, neither of which exists.
GHOSTS=$({ checked; yours; } | while read -r p; do
           [ -e "$DEST/$p" ] || echo "$p"; done)
if [ -z "$GHOSTS" ]; then
  ok "and every path in it resolves to a file that is really there"
else
  bad "the manifest names paths that are not on the root, so it is checking \
things that do not exist: $(printf '%s' "$GHOSTS" | tr '\n' ' ')"
fi

echo
echo "=== the box reads it back, and says so in one line ==="
selftest
if said '\[ ok \] all '"$NCHECKED"' files of that build are the ones it shipped'; then
  ok "the self-test checked all $NCHECKED of them and said so"
else
  bad "the self-test did not report the files of the build: $(printf '%s\n' "$OUTPUT" | grep -iE 'manifest|that build' | tr '\n' ' ')"
fi
# One line, and not a line per file. A box where everything agrees must not be
# made noisier by this: the self-test is read on a console that scrolls, by
# somebody racking a box, and twenty-six warnings were written into it one at
# a time because each of them was worth somebody noticing (#297).
NOISE=$(printf '%s\n' "$OUTPUT" | grep -c 'that build' || true)
if [ "$NOISE" = 1 ]; then
  ok "in one line, on a box where everything agrees"
else
  bad "the check printed $NOISE lines on a box where nothing is wrong"
fi

echo
echo "=== a file from another release, which is the measurement above ==="
# v0.68.0's prisma-routing-tune on a box stamped 0.72.0, which is #302's own
# reproduction with the versions moved: "put a file from another release on a
# box and see nothing notice."
git -C "$REPO" show v0.68.0:appliance/prisma-routing-tune \
  > "$DEST/usr/bin/prisma-routing-tune" 2>/dev/null \
  || cp "$REPO/appliance/selftest" "$DEST/usr/bin/prisma-routing-tune"
selftest
if said '\[FAIL\] 1 file\(s\) on this box are not from '"$(re_quote "$STAMP")"':'; then
  ok "the box says one of its files is not from the version it claims"
else
  bad "the box said nothing about the file from another release: $(printf '%s\n' "$OUTPUT" | grep -iE 'not from|that build' | tr '\n' ' ')"
fi
# BY NAME. "Something is wrong" sends the reader nowhere, and the reader is at
# a serial console because the box is behaving unlike its release notes.
if said 'usr/bin/prisma-routing-tune'; then
  ok "and names it: usr/bin/prisma-routing-tune"
else
  bad "it did not name which file: $(printf '%s\n' "$OUTPUT" | grep -A2 'not from' | tr '\n' ' ')"
fi
# AND THE SENTENCE UNDER THE LIST, which is the half that tells whoever is at
# the console what they are looking at. It is folded across five rows of an
# 80-column console, so the needle is read the way the reader reads it rather
# than the way the message happens to break today - which is the trap this
# repository has met seven times, twice in the last release, and it is the
# only reason said() collapses at all.
if said "that line is one sentence about $NCHECKED files, and these are not the ones that build shipped"; then
  ok "and says what that means for the version line above it"
else
  bad "it named the file and did not say what it means: $(printf '%s\n' "$OUTPUT" | sed -n '/not from/,/installer/p' | tr '\n' ' ')"
fi
# And nothing else. A check that names the whole install whenever one file
# changes is a check that has found nothing.
OTHERS=$(printf '%s\n' "$OUTPUT" | sed -n '/not from/,/that line is one/p' \
         | grep -c '^          ' || true)
if [ "$OTHERS" = 1 ]; then
  ok "and names that one and no other"
else
  bad "it named $OTHERS files when one had changed"
fi

echo
echo "=== a file that is not there at all ==="
# Different from a file with the wrong contents and worse: an install that
# stopped half way leaves this, and the box still boots.
rm -f "$DEST/usr/lib/prisma-routing/vppapi.py"
selftest
if said '\[FAIL\] 2 file\(s\) on this box are not from ' \
   && said 'usr/lib/prisma-routing/vppapi.py'; then
  ok "a missing file is counted and named beside the changed one"
else
  bad "a file removed from the box was not reported: $(printf '%s\n' "$OUTPUT" | grep -iE 'not from|vppapi' | tr '\n' ' ')"
fi

echo
echo "=== and the operator's own files, which are none of its business ==="
# THE HALF THAT DECIDES WHETHER ANY OF THIS SURVIVES CONTACT. install.sh
# writes three files once and then never touches them again, because the
# operator edits them: tune.env is where the tuning is set, and the installer's
# own closing sentence has always told them to edit it. A check that reported
# a deliberate edit as corruption is a check somebody switches off - and the
# file that really is from another release goes quiet along with it (#302).
fresh_root
install_run "$STAMP" >/dev/null 2>&1
echo 'PRISMA_SUBSCRIBERS=40000' >> "$DEST/etc/prisma-routing/tune.env"
echo '# an operator changed this' >> "$DEST/etc/prisma-routing/vpp-base.conf"
echo '! an operator line' >> "$DEST/etc/prisma-routing/prisma-routing.conf"
selftest
# The positive FIRST. A negative needle passes whether or not the box said the
# thing, and one of #279's three broken needles was exactly that - so the same
# run has to show the check ran and was green before "it said nothing about
# tune.env" means anything at all.
if said '\[ ok \] all '"$NCHECKED"' files of that build are the ones it shipped'; then
  ok "the check ran on this box and found the build intact"
else
  bad "the check did not run or did not pass, so the silence below proves \
nothing: $(printf '%s\n' "$OUTPUT" | grep -iE 'that build|not from' | tr '\n' ' ')"
fi
if said 'tune\.env|vpp-base\.conf|prisma-routing\.conf'; then
  bad "the box reported an edit to a file the installer deliberately leaves \
alone: $(printf '%s\n' "$OUTPUT" | grep -E 'tune.env|vpp-base|prisma-routing.conf' | tr '\n' ' ')"
else
  ok "and said nothing about the three files it does not own"
fi

echo
echo "=== an upgrade, which is where the stamp and the files part company ==="
# The path #302 is about. install.sh over a box that already has a version of
# this product on it moves the stamp, and everything it does not replace stays
# where it was - which is how a box comes to assert a build it only partly
# has. The manifest has to be the NEW one, covering what is there now.
install_run "0.73.0 (cafe123)" >/dev/null 2>&1
if grep -q '^# 0\.73\.0 (cafe123)\.$' "$DEST/usr/share/prisma-routing/MANIFEST"; then
  ok "the second run rewrote the manifest for the version it stamped"
else
  bad "the manifest still names an older build than the stamp beside it: \
$(head -2 "$DEST/usr/share/prisma-routing/MANIFEST" | tr '\n' ' ')"
fi
selftest
if said '\[ ok \] all '"$NCHECKED"' files of that build are the ones it shipped'; then
  ok "and the upgraded box agrees with it"
else
  bad "the upgraded box does not match its own manifest: $(printf '%s\n' "$OUTPUT" | grep -iE 'not from' -A2 | tr '\n' ' ')"
fi
# The operator's lines survived the upgrade AND are still not checked, which
# is the two halves of keep() meeting: the guard that does not overwrite, and
# the class that is not reported.
if grep -q '^PRISMA_SUBSCRIBERS=40000$' "$DEST/etc/prisma-routing/tune.env"; then
  ok "with the operator's tune.env line still in place under it"
else
  bad "the upgrade overwrote the operator's tune.env, which keep() exists to \
prevent"
fi

echo
echo "=== the shape appliance/build-iso.sh assembles a medium in ==="
# The release gate boots an installed box and reads the self-test's verdict off
# the console (lab/install-iso.sh). This check is new to that verdict, so a
# false positive on the ISO path would refuse a correct medium - which is what
# 0.72.0 was refused for, twice, one release ago. The chroot build-iso.sh runs
# the installer in differs from the roots above in exactly one way, and it is
# the arm that edits /etc/passwd and /etc/shells, so it is driven here.
fresh_root
mkdir -p "$DEST/etc"
printf 'root:x:0:0::/root:/bin/sh\n' > "$DEST/etc/passwd"
: > "$DEST/etc/shells"
if env PRISMA_VTY_LOGIN=1 DESTDIR="$DEST" PRISMA_VERSION_STAMP="$STAMP" \
     PLUGIN_DIR=/usr/lib/vpp_plugins VAT2_DIR=/usr/lib/vat2_plugins \
     sh "$TREE/appliance/install.sh" > "$WORK/iso.out" 2>&1; then
  say_capture "$REPO/appliance/install.sh" "$WORK/iso.out" "iso"
  ok "the installer ran the way build-iso.sh runs it, into a chroot"
else
  bad "the installer failed on the ISO's own path:"
  sed 's/^/        /' "$WORK/iso.out"
fi
selftest
if said '\[ ok \] all '"$NCHECKED"' files of that build are the ones it shipped'; then
  ok "and the root it assembled verifies against its own manifest"
else
  bad "a medium assembled this way would fail its own check at every boot, \
and the release gate reads that verdict: $(printf '%s\n' "$OUTPUT" | grep -iE 'not from|manifest' -A2 | tr '\n' ' ')"
fi

echo
echo "=== when the checker fails in words this does not recognise ==="
# THE TRAP THIS REPOSITORY HAS MET SEVEN TIMES, TWICE IN THE LAST RELEASE.
# `sha256sum -c` writes "<path>: FAILED", and that is coreutils' sentence and
# not this product's. A check that read its VERDICT out of that text would go
# quiet the day the wording moved - silently, and in the one direction that
# matters, because "no line matched" and "nothing is wrong" are the same thing
# to a needle. That is #279 and the two refusals of 0.72.0 in one sentence.
#
# So the self-test takes the verdict from the EXIT STATUS, which is defined
# behaviour, and reads the text only to decide how much detail it can give.
# Driven with a checker that fails in words nothing here recognises: the box
# must still be reported as not matching, with the detail lost and not the
# finding.
fresh_root
install_run "$STAMP" >/dev/null 2>&1
printf '#!/bin/sh\necho "everything here is wrong"\nexit 1\n' > "$BIN/sha256sum"
chmod +x "$BIN/sha256sum"
selftest
rm -f "$BIN/sha256sum"
if said '\[FAIL\].*does not match its own manifest'; then
  ok "the box is still reported as not matching its own manifest"
else
  bad "a checker that failed in an unfamiliar wording left the box reading as \
healthy: $(printf '%s\n' "$OUTPUT" | grep -iE 'manifest|that build|not from' | tr '\n' ' ')"
fi

echo
echo "=== and on a box with no sha256sum to check with ==="
# Defensive, and driven rather than argued: coreutils is on every box this
# product runs on, and an assertion nothing can make red is an assertion
# nobody should believe. PATH is a farm of symlinks to the real commands with
# that one removed, so the self-test runs in every other respect.
FARM=$WORK/no-sha256sum
rm -rf "$FARM"
cp -as /usr/bin "$FARM" 2>/dev/null || true
rm -f "$FARM/sha256sum"
OUTPUT=$(PATH="$BIN:$FARM" PRISMA_SELFTEST_OUT=/dev/stdout \
         PRISMA_SELFTEST_ROOT="$DEST" sh "$REPO/appliance/selftest" 2>/dev/null)
printf '%s\n' "$OUTPUT" >> "$EVERY"
if said '\[warn\] no sha256sum here'; then
  ok "it says so, rather than passing a box it did not look at"
elif said 'files of that build are the ones it shipped'; then
  bad "it reported the files as checked on a box with nothing to check them \
with"
else
  bad "a box with no sha256sum said: $(printf '%s\n' "$OUTPUT" | grep -iE 'sha256|that build|manifest' | tr '\n' ' ')"
fi

echo
echo "=== a box with no manifest is not a box that failed ==="
# A check that cannot look is not a check that agrees, and it is not a box
# that is broken either. Every box this self-test reaches was installed by the
# install.sh that wrote it one, so the absence means somebody removed it - and
# saying nothing about that would be the quiet failure this file is against.
# It is a warning and not a failure because no file of the build has been shown
# to be wrong: nothing has been measured at all, which is a different thing.
rm -f "$DEST/usr/share/prisma-routing/MANIFEST"
selftest
if said '\[warn\] no /usr/share/prisma-routing/MANIFEST, so nothing checked'; then
  ok "it warns that nothing checked the version line"
else
  bad "a box with no manifest said: $(printf '%s\n' "$OUTPUT" | grep -iE 'manifest' | tr '\n' ' ')"
fi
if said '\[FAIL\].*([Mm][Aa][Nn][Ii][Ff][Ee][Ss][Tt]|not from)'; then
  bad "the missing manifest was reported as a failed check, so a box that \
merely cannot be checked is called broken"
else
  ok "and does not report it as a failure"
fi
# And the report still finishes. The one thing this script may never do is
# stop, because the box that fails a check is the box whose console somebody
# needs.
if said '=====$|====='; then
  ok "and the report still reaches its closing line"
else
  bad "the self-test did not finish on a box with no manifest"
fi

echo
echo "=== and a manifest that names nothing is not a box that failed either ==="
# THIS CASE WAS FOUND BY A MUTATION AND THE CHECK WAS WRONG. `grep -c` prints
# its count AND exits 1 when the count is zero, so the `|| echo 0` written
# beside it put two numbers in the substitution and the emptiness test compared
# "0 0" against 0. A manifest of nothing but comments therefore fell through to
# the comparison and the box was reported as FAILING a check that had not run -
# which is worse than either honest answer, and it was invisible until the
# installer's recording was mutated away.
fresh_root
install_run "$STAMP" >/dev/null 2>&1
grep '^#' "$DEST/usr/share/prisma-routing/MANIFEST" > "$WORK/comments-only"
cp "$WORK/comments-only" "$DEST/usr/share/prisma-routing/MANIFEST"
selftest
if said 'the manifest names no files'; then
  ok "it says the manifest names nothing, rather than judging the box on it"
else
  bad "a manifest with no checksum lines produced: $(printf '%s\n' "$OUTPUT" | grep -iE 'manifest|that build|not from' | tr '\n' ' ')"
fi
if said '\[FAIL\].*([Mm][Aa][Nn][Ii][Ff][Ee][Ss][Tt]|not from)'; then
  bad "an empty manifest was reported as files that do not match, so a box \
nothing was measured on is called broken"
else
  ok "and does not report it as files that do not match"
fi

echo
echo "=== what it costs at every boot ==="
# THE CONSTRAINT: a box checksumming its own files on every boot is only fine
# if it is fine on the slowest hardware this product accepts. docs/appliance.md
# puts that floor at 4 GB of RAM and a CPU with SSE4.2, so the slowest CPU that
# may run this has no SHA extensions at all - and neither does the Broadwell
# this suite was written on, which is why the number below is the plain-code
# path rather than a hardware-accelerated one.
#
# Measured rather than asserted, and printed rather than judged: a suite that
# failed on a time is a suite that goes red on a loaded build runner and
# teaches nobody anything. The ceiling below is loose enough that only a check
# that has become something else entirely can reach it.
fresh_root
install_run "$STAMP" >/dev/null 2>&1
BYTES=$(cd "$DEST" && checked | xargs stat -c %s 2>/dev/null | awk '{s+=$1} END {print s+0}')
S=$(date +%s%N)
i=0
while [ "$i" -lt 10 ]; do
  ( cd "$DEST" && sha256sum --quiet -c usr/share/prisma-routing/MANIFEST ) \
    >/dev/null 2>&1
  i=$((i + 1))
done
E=$(date +%s%N)
MS=$(( (E - S) / 10 / 1000000 ))
if [ "$MS" -lt 2000 ]; then
  ok "$NCHECKED files, $BYTES bytes, ${MS} ms per run (mean of 10) on $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ *//')"
else
  bad "the check took ${MS} ms over $BYTES bytes, which is no longer the \
cheap thing this design assumed"
fi

echo
echo "=== every line of it fits the console it is printed on ==="
# The self-test writes to /dev/console on a racked appliance, which is 80
# columns. test/test_selftest.sh enforces this over the boxes it arranges, and
# it cannot arrange these: the failure rows below carry a file list and a
# version stamp interpolated into them, so reading the source finds nothing
# and driving the boxes above finds them.
if [ ! -s "$EVERY" ]; then
  bad "no self-test output was collected, so this measured nothing"
else
  WIDE=$(awk 'length > 79 {print length": "$0}' "$EVERY" | sort -u)
  if [ -z "$WIDE" ]; then
    ok "$(sort -u "$EVERY" | wc -l | tr -d ' ') distinct lines, widest $(awk '{print length}' "$EVERY" | sort -rn | head -1) columns"
  else
    bad "lines wider than the 79-column console:
$WIDE"
  fi
fi

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
