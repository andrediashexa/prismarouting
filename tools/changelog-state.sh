#!/bin/sh
# Compare CHANGELOG.md against the issue tracker.
#
#   sh tools/changelog-state.sh                 the CHANGELOG in this tree
#   sh tools/changelog-state.sh path/to/file.md another copy of it
#
# An issue closed as COMPLETED is work that shipped. CONTRIBUTING.md section 5
# rule 3 says every merge produces a CHANGELOG entry, and the CHANGELOG is what
# an operator who does not clone this repository reads to find out what changed
# for them. Work that shipped with no entry is work no operator can discover.
#
# A TOOL and not a `make test` suite, for the same reason tools/roadmap-state.sh
# is one and says so in its own header: whether an issue is closed is written
# down nowhere in the tree, and every suite here has to pass with no network
# (#317). Run it when a release is cut, which is when the CHANGELOG is written.
#
# WHAT IT ASKS, and the two things it deliberately does not.
#
# NAMED ANYWHERE IN THE FILE, not in the section being cut. An issue is often
# closed days after the release that carried it shipped. Measured on
# 2026-09-15: of the 17 issues closed since v0.74.2 and absent from the
# `## Unreleased` section, SIXTEEN are named in 0.74.1 or 0.74.2, which is the
# ordinary case and not a defect. Demanding the current section would have
# reported sixteen findings and taught whoever reads them to skip the list.
#
# NOT_PLANNED IS EXEMPT. An issue closed that way is a decision not to do
# something, so there is no change for a reader to be told about. Measured over
# the whole tracker: 293 closed, 291 COMPLETED and 2 NOT_PLANNED, and neither
# of the two is named in the file - correctly.
#
# AN OPEN ISSUE NAMED IN THE FILE IS NOT A FINDING, in either direction. An
# entry names the issues it argues from, and most of those are open on purpose:
# an entry saying a claim was narrowed until #226 lands is an entry doing its
# job. That is the opposite of tools/roadmap-state.sh, which DOES flag an open
# issue it never mentions - because the roadmap is a list of what remains and
# this is a record of what happened. The two files answer different questions
# and a check copied between them would be wrong in one of them.
#
# WHAT IT CANNOT SEE. It compares issue NUMBERS. An entry that describes work
# accurately and names no issue satisfies this tool and tells a reader nothing
# about where the reasoning lives, and an entry naming the wrong number passes
# too. It holds the half that goes wrong by forgetting, which is the half that
# has actually gone wrong here three times.
#
# WHY IT EXISTS. Measured on 2026-09-15 over the whole history: 293 closed
# issues, and THREE completed ones the CHANGELOG names nowhere - #175 (isis
# instance completion, shipped in v0.62.0), #177 (the IGP lab driven through
# the VTY, v0.61.0) and #341 (wiki-pull.sh's empty-answer guard, still
# unreleased at the time). Each was found by hand, one at a time, while writing
# a release note. Nothing would have found the fourth.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DOC=${1:-$ROOT/CHANGELOG.md}
[ -f "$DOC" ] || { echo "error: no such file: $DOC" >&2; exit 2; }

# Every refusal below exits 2 and says the comparison did not happen. 1 means
# findings. Nothing that could not reach the tracker may exit 0: a CHANGELOG
# nobody compared reads exactly like a CHANGELOG that agreed.
command -v gh >/dev/null 2>&1 || {
  echo "error: gh is not installed, so NOTHING WAS COMPARED." >&2
  echo "       This tool reads the tracker; there is no offline answer it" >&2
  echo "       could give instead." >&2
  exit 2
}
gh auth status >/dev/null 2>&1 || {
  echo "error: gh is not authenticated, so NOTHING WAS COMPARED." >&2
  echo "       Run 'gh auth login'. The file was read and the tracker was" >&2
  echo "       never asked; this is not a clean run." >&2
  exit 2
}

CLOSED=$(mktemp) || exit 2
ERRS=$(mktemp) || exit 2
trap 'rm -f "$CLOSED" "$ERRS"' EXIT INT TERM

# ONE query, and --limit far above the tracker's size on purpose: gh truncates
# at the limit in silence, and a truncated answer here is the dangerous
# direction. Every issue past the cut is never looked for at all, so the tool
# reports agreement over a list it did not read - the empty-haystack shape this
# repository refuses everywhere else. The count is compared to the limit below.
LIMIT=5000
if ! gh issue list --state closed --limit "$LIMIT" \
       --json number,stateReason --jq '.[] | "\(.number) \(.stateReason)"' \
       > "$CLOSED" 2>"$ERRS"; then
  # gh's own text is kept: "could not resolve host" and "HTTP 403 rate limit"
  # need different work and both look like an empty answer from here.
  echo "error: the tracker could not be reached, so NOTHING WAS COMPARED." >&2
  sed 's/^/       gh: /' "$ERRS" >&2
  exit 2
fi
[ -s "$CLOSED" ] || {
  echo "error: the tracker answered with no closed issues at all, which" >&2
  echo "       cannot be right for this repository. NOTHING WAS COMPARED." >&2
  exit 2
}
N=$(wc -l < "$CLOSED" | tr -d ' ')
[ "$N" -lt "$LIMIT" ] || {
  echo "error: the tracker returned exactly the $LIMIT-issue limit, so the" >&2
  echo "       answer is truncated and everything past the cut was never" >&2
  echo "       looked for. Raise LIMIT in this file. NOTHING WAS COMPARED." >&2
  exit 2
}

echo "==> $DOC"
echo "    tracker: $N closed issue(s)"

# The file's references, read once. Both spellings are NOT read here, unlike
# tools/roadmap-state.sh: this file writes every reference as a markdown link
# to the issue, and a bare `#123` in a CHANGELOG entry is far more often a
# quoted commit subject or a column count than a reference. Measured: reading
# bare numbers too finds no issue this misses and picks up prose numerals.
grep -oE "issues/[0-9]+" "$DOC" | cut -d/ -f2 | sort -un > "$CLOSED.named"

# THE FIRST FILE IS NAMED, not counted. This was `NR == FNR`, which is the
# idiom for "still reading the first file" and is WRONG the moment that file is
# empty: awk never runs the rule at all, NR and FNR then both count from 1
# through the second file, and every closed issue is filed as named. A
# CHANGELOG with no links in it reported that it records everything, having
# looked at nothing - the empty-haystack shape, in the tool written to refuse
# it. Found by test/test_tool_refusals.sh's bare-#10 case, which is there
# because the tool claims to read links and not prose.
MISS=$(awk -v named_file="$CLOSED.named" '
  FILENAME == named_file { named[$1 + 0] = 1; next }
  $2 != "COMPLETED" { next }
  !($1 + 0 in named) { print $1 }
' "$CLOSED.named" "$CLOSED" | sort -un)
EXEMPT=$(awk '$2 != "COMPLETED"' "$CLOSED" | wc -l | tr -d ' ')
echo "    named by this file: $(wc -l < "$CLOSED.named" | tr -d ' ')"
echo "    closed as a decision rather than a change, so exempt: $EXEMPT"
echo

if [ -z "$MISS" ]; then
  echo "every completed issue is named in the CHANGELOG"
  exit 0
fi

echo "==> completed issues this file names nowhere"
for n in $MISS; do
  t=$(gh issue view "$n" --json title -q .title 2>/dev/null || echo "?")
  printf '    #%-5s %s\n' "$n" "$t"
done
echo
c=$(printf '%s\n' "$MISS" | wc -l | tr -d ' ')
echo "$c finding(s). Each is work that shipped with no record a reader can"
echo "find. An entry belongs in the CURRENT section even when the work went"
echo "out in an older release: CHANGELOG.md's own TL;DR forbids editing a"
echo "shipped section, because a reconstruction that hides being one is a"
echo "second and quieter defect. Say which release carried it."
exit 1
