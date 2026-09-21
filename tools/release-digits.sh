#!/bin/sh
# Every published release, against what its DIGIT says it should have been.
#
#   sh tools/release-digits.sh          say which ones disagree
#
# THE RULE, from CONTRIBUTING.md section 5: a Z release publishes the upgrade
# artifact and no medium, and is published as a PRE-RELEASE so that GitHub
# leaves `Latest` on the last X or Y - the last medium this project actually
# booted. An X or Y publishes the medium and is a full release.
#
# WHY A READER, AND WHY THE GATE IS NOT ENOUGH. tools/release-gate.sh reads
# the digit off the tag and takes the matching path; it refuses a Z whose note
# claims a boot, and a Z that changes a file the artifact cannot carry. What it
# cannot do is check the PUBLISHING, because at cut time nothing is published
# yet - the `--prerelease` flag is typed at the one moment nothing is looking,
# which is exactly how #402's title went out wrong for the neighbouring rule.
#
# Five Z releases have been published since the rule landed and all five are
# pre-releases. They were typed that way by whoever cut them, and a rule kept
# by whoever remembers is a rule this repository does not count as kept.
#
# WHAT IT DOES NOT REFUSE, and why the line is where it is. The rule was
# decided on 2026-09-20 and shipped in 0.109.0. NINE releases before it were
# patches published as full releases - v0.2.1, v0.38.1, v0.61.2, v0.74.1,
# v0.74.2, v0.75.1, v0.75.2, v0.76.1, v0.77.1 - and none of them broke a rule
# that did not exist. They are counted and named, never refused: a check that
# fails on history is a check somebody turns off.
#
# It needs the network and `gh`, so it is not in `make test`, for the reason
# tools/release-titles.sh gives: a suite that cannot run offline is a suite
# that stops being run.
#
# PRISMA_RELEASE_DIGITS names a file of "<tag>\t<true|false>" rows to read
# INSTEAD of asking GitHub, which is how test/test_release_digits.sh drives
# the refusals. Without it the only way to watch one fire is to publish a
# release with the defect, which is the trap the sibling reader was caught in.
set -u

# The first release that the rule binds. Before it the rule did not exist.
RULE_FROM_MAJOR=0
RULE_FROM_MINOR=109

rows() {
  if [ -n "${PRISMA_RELEASE_DIGITS:-}" ]; then
    cat "$PRISMA_RELEASE_DIGITS"
  else
    gh release list --limit 300 --json tagName,isPrerelease \
      -q '.[] | "\(.tagName)\t\(.isPrerelease)"' 2>/dev/null
  fi
}

ALL=$(rows)
if [ -z "$ALL" ]; then
  echo "==> the published releases were NOT checked: gh answered nothing"
  echo "    A release page this cannot read is not a release page this can"
  echo "    judge. Nothing is refused on that."
  exit 0
fi

bound=0 before=0 wrong=0 names=""
for row in $(printf '%s\n' "$ALL" | tr '\t' '|' | tr -d ' '); do
  tag=${row%%|*}; pre=${row##*|}
  # vX.Y.Z and nothing else: a tag this cannot parse is one it says nothing
  # about, rather than one it guesses at.
  case "$tag" in
    v[0-9]*.[0-9]*.[0-9]*) : ;;
    *) continue ;;
  esac
  rest=${tag#v}
  x=${rest%%.*}; rest=${rest#*.}
  y=${rest%%.*}; z=${rest#*.}
  case "$x$y$z" in *[!0-9]*) continue ;; esac

  if [ "$x" -lt "$RULE_FROM_MAJOR" ] \
     || { [ "$x" -eq "$RULE_FROM_MAJOR" ] && [ "$y" -lt "$RULE_FROM_MINOR" ]; }
  then
    if [ "$z" -ne 0 ] && [ "$pre" = false ]; then
      before=$((before + 1))
    fi
    continue
  fi

  bound=$((bound + 1))
  if [ "$z" -ne 0 ] && [ "$pre" = false ]; then
    wrong=$((wrong + 1))
    names="$names
    $tag: a patch, published as a FULL release. Latest moved to a version
      this project never booted."
  elif [ "$z" -eq 0 ] && [ "$pre" = true ]; then
    wrong=$((wrong + 1))
    names="$names
    $tag: a minor or major, published as a PRE-RELEASE. Latest stayed on an
      older medium, so the page an operator lands on offers the wrong ISO."
  fi
done

echo "==> $bound release(s) are bound by the X.Y.Z rule (0.$RULE_FROM_MINOR.0 and after)"
if [ "$before" -gt 0 ]; then
  echo "    $before patch release(s) before it were published as full releases."
  echo "    The rule was decided on 2026-09-20 and shipped in 0.$RULE_FROM_MINOR.0;"
  echo "    they did not break it. Counted, not refused."
fi

if [ "$wrong" -eq 0 ]; then
  echo "    every one of them was published the way its digit says"
  exit 0
fi
echo "error: $wrong published release(s) disagree with their own digit:$names" >&2
echo "       CONTRIBUTING.md section 5: a Z is published as a pre-release so" >&2
echo "       that Latest stays on the last medium this project booted, and an" >&2
echo "       X or Y is published whole. The flag is typed at the one moment" >&2
echo "       nothing else is looking, which is why this reader exists." >&2
exit 1
