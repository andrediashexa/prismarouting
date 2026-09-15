#!/bin/sh
# Publish every page the mirror is ahead on, one push at a time.
#
#   sh tools/wiki-publish.sh -n          say what it would publish, push nothing
#   WIKI_TOKEN=... sh tools/wiki-publish.sh
#
# WHY THIS EXISTS. tools/wiki-push.sh takes one file, which is the right
# granularity: each push checks that one document on its own and refuses if
# somebody edited it after the last pull. What it is not is a thing anybody
# does twenty-seven times by hand, and #277 is the measurement of that - the
# mirror stood 28 pages ahead of the live wiki from 2026-09-09, one of those
# pages telling an operator something about their box that stopped being true,
# and the reason recorded on the issue is "nobody did". A chore that nobody
# does is a defect with a slower fuse.
#
# IT STOPS AT THE FIRST REFUSAL, and that is the whole safety argument. A
# refusal from wiki-push.sh means the live document changed after the mirror
# was pulled, which means somebody is editing the wiki right now. Carrying on
# through the rest of the list would publish twenty-six pages over the top of
# whatever else that person is doing. It says how many went out before it
# stopped, so the operator knows where they are.
#
# THE LIST COMES FROM tools/wiki-status.sh AND IS NOT DERIVED HERE. That script
# already decides which pages can be updated and which have to be created by
# hand first, from git's own status letters; a second reader of that would be a
# second answer to keep true. `--updatable` is the same computation with a
# different printing.
#
# NO TOKEN LIVES HERE. WIKI_TOKEN and WIKI_URL come from the environment and
# are passed through untouched, as tools/wiki-push.sh requires.
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
DRY=0
case "${1:-}" in
  -n|--dry-run) DRY=1 ;;
  "") ;;
  *)
    echo "usage: WIKI_TOKEN=... sh tools/wiki-publish.sh" >&2
    echo "       sh tools/wiki-publish.sh -n" >&2
    exit 2 ;;
esac

# Refused before the list is even read, because a run that pushes nothing and
# exits 0 looks exactly like a mirror that was already published.
if [ "$DRY" = 0 ] && [ -z "${WIKI_TOKEN:-}" ]; then
  echo "error: WIKI_TOKEN is not set, so nothing can be published." >&2
  echo "       sh tools/wiki-publish.sh -n says what would go out, and" >&2
  echo "       needs no token." >&2
  exit 2
fi

LIST=$(sh "$REPO/tools/wiki-status.sh" --updatable) || {
  echo "error: tools/wiki-status.sh could not say what is waiting, so" >&2
  echo "       NOTHING was published. Its own refusal is above." >&2
  exit 2
}

if [ -z "$LIST" ]; then
  echo "nothing to publish: the mirror matches the last pull."
  exit 0
fi

TOTAL=$(printf '%s\n' "$LIST" | grep -c .)
if [ "$DRY" = 1 ]; then
  echo "$TOTAL page(s) would be published, in this order:"
  printf '%s\n' "$LIST" | sed 's/^/  /'
  echo
  echo "Nothing was sent. Set WIKI_TOKEN and run it again without -n."
  exit 0
fi

echo "publishing $TOTAL page(s)"
done_n=0
for f in $LIST; do
  printf '  %s ... ' "$f"
  if sh "$REPO/tools/wiki-push.sh" "$f" > "$REPO/.wiki-publish.log" 2>&1; then
    done_n=$((done_n + 1))
    echo "published"
  else
    echo "REFUSED"
    sed 's/^/      /' "$REPO/.wiki-publish.log" >&2
    rm -f "$REPO/.wiki-publish.log"
    echo >&2
    echo "error: stopped at $f, with $done_n of $TOTAL published." >&2
    echo "       A refusal here means the live document changed after the" >&2
    echo "       mirror was pulled, so somebody is editing the wiki. The" >&2
    echo "       rest of the list is NOT published, because pushing over" >&2
    echo "       whatever they are doing is the thing wiki-push.sh refuses" >&2
    echo "       one page at a time and this must not undo." >&2
    exit 1
  fi
done
rm -f "$REPO/.wiki-publish.log"

echo
echo "$done_n page(s) published. What remains, from tools/wiki-status.sh:"
sh "$REPO/tools/wiki-status.sh" 2>&1 | sed 's/^/  /' || true
