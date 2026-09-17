#!/bin/sh
# Every lab is accounted for on the question of its off run, or it is named.
#
# WHAT AN OFF RUN IS FOR. tools/run-all-labs.sh runs some labs a third time
# with the feature they test switched off, and requires each to go red against
# a floor the lab writes in its own header. That is the only mechanism in this
# repository that renders a lab assertion's RED branch: measured while sweeping
# them, 425 of the 1381 assertions in lab/ have their failure shown on every
# release run, and 956 do not. A lab with no off run is a lab whose checks
# nobody has watched fail.
#
# WHY THIS IS A CHECK AND NOT A CHORE. There are three ways a lab can be
# accounted for - an off mode plus a floor, a NOT_LABS entry saying why it is
# not run at all, or a NO-OFF-RUN line in its own header saying why it has
# none - and until now there was a fourth: nothing at all. 32 labs are in it,
# and no file anywhere records whether that was a decision. That is the shape
# this project keeps paying for: not a wrong answer, an absent question.
#
# THE FLOOR GOES DOWN, NEVER UP. Writing 32 reasons tonight would mean
# inventing most of them, and a rubber stamp is worse than the silence it
# replaces. So the count is a floor in the same idiom as OFF-RED-AT-LEAST,
# which this project already trusts: a new lab may not join the unaccounted,
# and every one that leaves lowers it.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
RUNNER=$REPO/tools/run-all-labs.sh
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }

# Read from the runner, which is the holder of both lists. A second copy here
# would agree with itself: the whole question is what THAT file knows.
OFF_NAMES=$(sed -n '/^OFF_MODES="/,/"$/p' "$RUNNER" \
            | sed 's/^OFF_MODES="//' \
            | tr ' \n' '\n\n' | sed -n 's/^\([a-z0-9-]*\):.*/\1/p' | sort -u)
NOT_NAMES=$(sed -n '/^NOT_LABS="/,/^"$/p' "$RUNNER" \
            | sed 's/^NOT_LABS="//' \
            | sed -n 's/^\([a-z0-9-]*\):.\+$/\1/p' | sort -u)

echo "=== the runner's two lists were read at all ==="
# A parse that came back empty would make every count below zero and the whole
# file green, which is the failure this check exists to find one level down.
[ "$(printf '%s\n' "$OFF_NAMES" | grep -c .)" -ge 10 ] \
  && [ "$(printf '%s\n' "$NOT_NAMES" | grep -c .)" -ge 5 ] \
  && ok "$(printf '%s\n' "$OFF_NAMES" | grep -c .) labs have an off mode and \
$(printf '%s\n' "$NOT_NAMES" | grep -c .) entries are not run as labs" \
  || { bad "the runner's lists could not be read, so everything below is \
about nothing: off=$(printf '%s\n' "$OFF_NAMES" | grep -c .) \
not=$(printf '%s\n' "$NOT_NAMES" | grep -c .)"
     echo; echo "$fail FAILURES"; exit 1; }

echo
echo "=== a lab with an off mode declares the floor that run is measured against ==="
# An off run with no floor cannot refuse anything: the suite would run the lab
# with the feature gone and accept whatever came back.
missing_floor=""
for n in $OFF_NAMES; do
  [ -f "$REPO/lab/$n.sh" ] || continue
  grep -q '^# OFF-RED-AT-LEAST:' "$REPO/lab/$n.sh" || missing_floor="$missing_floor $n"
done
[ -z "$missing_floor" ] \
  && ok "every lab with an off mode says how many checks that run must turn red" \
  || bad "these have an off mode and no floor, so the off run accepts \
whatever it gets:$missing_floor"

echo
echo "=== and a floor without an off mode is a number nobody reads ==="
orphan_floor=""
for f in "$REPO"/lab/*.sh; do
  n=$(basename "$f" .sh)
  grep -q '^# OFF-RED-AT-LEAST:' "$f" || continue
  printf '%s\n' "$OFF_NAMES" | grep -qx "$n" || orphan_floor="$orphan_floor $n"
done
[ -z "$orphan_floor" ] \
  && ok "every floor belongs to a run that happens" \
  || bad "these declare a floor and are never run with the feature off, so \
the number is decoration:$orphan_floor"

echo
echo "=== and every other lab is named, with the count as a floor ==="
# THE FLOOR IS THE HIGHEST THE UNACCOUNTED MAY BE. Not the exact number: a lab
# that gains a reason lowers it, and requiring equality would make doing the
# right thing turn this red.
# 32 until #385 deleted lab/one-nic.sh, which was #303's proof and had no off
# run; a lab that leaves lowers the floor, which is the ratchet.
UNACCOUNTED_AT_MOST=31
unaccounted=""
for f in "$REPO"/lab/*.sh; do
  n=$(basename "$f" .sh)
  printf '%s\n' "$NOT_NAMES" | grep -qx "$n" && continue
  printf '%s\n' "$OFF_NAMES" | grep -qx "$n" && continue
  grep -q '^# NO-OFF-RUN:' "$f" && continue
  unaccounted="$unaccounted $n"
done
n_un=$(printf '%s' "$unaccounted" | wc -w | tr -d ' ')
if [ "$n_un" -le "$UNACCOUNTED_AT_MOST" ]; then
  ok "$n_un lab(s) have no off run and no recorded reason, at or under the \
floor of $UNACCOUNTED_AT_MOST"
else
  bad "$n_un lab(s) have no off run and no recorded reason, above the floor \
of $UNACCOUNTED_AT_MOST. A lab whose checks nobody has watched fail is one \
this repository cannot tell from a lab that passes for the wrong reason - \
give it an off mode, a NOT_LABS entry, or a NO-OFF-RUN line saying \
why:$unaccounted"
fi
# And the floor is not slack that has quietly opened up. A floor well above
# the count is one nobody lowered when the work was done, which is how a
# ratchet stops ratcheting.
if [ "$n_un" -lt "$UNACCOUNTED_AT_MOST" ]; then
  bad "the floor says $UNACCOUNTED_AT_MOST and only $n_un lab(s) are \
unaccounted: lower it, or it stops holding anything"
else
  ok "and the floor is where the count is, so it still holds"
fi

echo
echo "=== a NO-OFF-RUN line says something ==="
# The escape from the count has to cost a sentence, or it becomes the way
# every new lab opts out.
thin=""
for f in "$REPO"/lab/*.sh; do
  r=$(sed -n 's/^# NO-OFF-RUN:[[:space:]]*//p' "$f" | head -1)
  grep -q '^# NO-OFF-RUN:' "$f" || continue
  [ "${#r}" -ge 30 ] || thin="$thin $(basename "$f" .sh)"
done
[ -z "$thin" ] \
  && ok "every NO-OFF-RUN carries a reason, or there are none yet" \
  || bad "these opt out of the off run without saying why:$thin"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
