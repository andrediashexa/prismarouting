#!/bin/sh
# Every published release title, against the CHANGELOG heading for its version.
#
#   sh tools/release-titles.sh          say which differ
#
# WHY THIS EXISTS, and why the gate is not enough. tools/release-gate.sh
# compares the ONE release it is cutting, which is the right check at the right
# moment and says nothing about the 80-odd already published. Eight of them
# were measured as different on 2026-09-15 (#340), four of those truncations
# that announced half of what the release fixed - and the page an operator
# lands on from a search is the published one, not this file.
#
# The title is the sentence. Both sides are normalised for the product name in
# either spelling (#202 renamed it at 0.79.0), an optional "v", the version and
# whatever dash somebody typed into a web form, so what is compared is what the
# release SAYS and not its punctuation.
#
# It needs the network and `gh`, so it is not in `make test`, for the reason
# the wiki and roadmap checks are not: a suite that cannot run offline is a
# suite that stops being run.
#
# PRISMA_RELEASE_TITLES names a file of "<tag>\t<title>" rows to read INSTEAD
# of asking GitHub, and that is what test/test_release_titles.sh drives this
# over. Without it, the only way to watch a refusal fire was to publish a
# release with the defect - which is how the one this file did not catch got
# published (#402): a title with no version normalises to the same string as
# one that has it, so the check compared the sentence and never the shape.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
if [ -n "${PRISMA_RELEASE_TITLES:-}" ]; then
  [ -r "$PRISMA_RELEASE_TITLES" ] || {
    echo "error: $PRISMA_RELEASE_TITLES cannot be read." >&2
    exit 2
  }
  cp "$PRISMA_RELEASE_TITLES" /tmp/release-titles.tsv
else
  command -v gh >/dev/null 2>&1 || {
    echo "error: gh is not installed, so no release can be read." >&2
    exit 2
  }
  gh release list -L 300 --json tagName,name -q '.[] | .tagName + "\t" + .name' \
    > /tmp/release-titles.tsv || {
    echo "error: gh could not list the releases." >&2
    exit 2
  }
fi
diff=0
total=0
while IFS="$(printf '\t')" read -r tag name; do
  ver=${tag#v}
  # THE NEXT CHARACTER HAS TO BE A SPACE, and that is not pedantry: "## 0.111.1"
  # is a prefix of "## 0.111.10 - ...", so on the day this project published a
  # two-digit patch, 0.111.1 was compared against 0.111.10's heading and
  # reported as carrying the wrong title. The published title was right and
  # the reader was wrong (#210's release week).
  head=$(awk -v v="## $ver" '
    index($0, v) == 1 &&
    (length($0) == length(v) || substr($0, length(v) + 1, 1) == " ") {
      sub(/^## [0-9.]+[[:space:]]*[-\xe2\x80\x94]*[[:space:]]*/, "")
      print; exit
    }' "$REPO/CHANGELOG.md")
  [ -n "$head" ] || continue
  total=$((total + 1))
  norm=$(printf '%s' "$name" \
    | sed -e 's/^HexaRouting[[:space:]]*//' -e 's/^Prisma Routing[[:space:]]*//' \
          -e "s/^v\{0,1\}$ver[[:space:]]*[-\xe2\x80\x94]*[[:space:]]*//")
  # AND IT CARRIES THE VERSION, which is the other half of the convention and
  # was checked by nothing. Every one of the 104 releases published before
  # 0.101.0 begins with the version - some with a `v`, some after the product
  # name - and 0.101.0 was published as the sentence alone. This loop passed
  # it, because the normaliser above STRIPS the version before comparing: a
  # title with no version at all normalises to exactly the same string as one
  # that has it, so the shape was invisible to the only reader that looks at
  # published titles. The maintainer saw it on the releases page.
  #
  # Checked on the raw name and not on `norm`, for that reason.
  case "$name" in
    "$ver"*|"v$ver"*|"HexaRouting $ver"*|"HexaRouting v$ver"*|\
    "Prisma Routing $ver"*|"Prisma Routing v$ver"*) ;;
    *)
      diff=$((diff + 1))
      echo "$ver"
      echo "  published: $name"
      echo "  and it does not begin with the version, as every other does"
      continue ;;
  esac
  # A PUBLISHED TITLE THAT EXTENDS THE HEADING IS RIGHT, and 0.2.0 is why:
  # it reads "foundations (superseded: VTY does not start)", which is a fact
  # about that release the CHANGELOG entry does not carry and a reader landing
  # on that page needs before anything else. A title that is SHORTER than the
  # heading is the defect this looks for - it announces less than the release
  # fixed.
  case "$norm" in
    "$head"*) continue ;;
  esac
  if [ "$norm" != "$head" ]; then
    diff=$((diff + 1))
    echo "$ver"
    echo "  published: $norm"
    echo "  heading:   $head"
  fi
done < /tmp/release-titles.tsv
echo
if [ "$diff" = 0 ]; then
  echo "$total published release(s) carry the CHANGELOG heading as their title."
else
  echo "$diff of $total published release(s) do not carry the CHANGELOG heading."
  echo "The published page is what somebody who does not clone this repository"
  echo "reads, and a shorter title announces less than the release fixed."
  echo "Fix one with:  gh release edit vX.Y.Z --title 'X.Y.Z - <heading>'"
  exit 1
fi
