#!/bin/sh
# What is drafted under wiki/ and has not been published yet.
#
#   sh tools/wiki-status.sh
#
# Exit 0 when the mirror matches the last pull. Exit 1, and a list, when it
# does not.
#
# This exists because nothing could answer the question. The mirror was 28
# pages ahead of the live wiki for five days and the only way to see that was
# to read the git log by hand and know which commit was the last pull. One of
# those pages told an operator that a refused commit applies nothing, which
# stopped being true when the FRR read-back check became a refusal that leaves
# the box half-configured. A correction sitting in a mirror nobody can read is
# not a correction yet, and until now nothing said it was sitting there.
#
# THE HOLDER IS THE HISTORY, not a second file that could disagree with it.
# tools/wiki-pull.sh rewrites the whole mirror from the live wiki and writes
# wiki/.pulled in the same commit, so the last commit that touched .pulled is
# the last moment the mirror and the wiki were the same text. Every change to
# a page after it is an edit that has not been published. No message is
# matched and no page content is parsed: this asks git which files moved.
#
# Only COMMITTED work counts. An edit in the working tree is not yet part of
# the repository's record, and git's own status already shows it.
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO"

git rev-parse --git-dir >/dev/null 2>&1 || {
  echo "error: not a git repository, so there is no history to read." >&2
  exit 2
}

PULL=$(git log -1 --format=%H -- wiki/.pulled)
[ -n "$PULL" ] || {
  echo "error: no commit in this history ever wrote wiki/.pulled, so there" >&2
  echo "       is no last pull to compare against." >&2
  exit 2
}

# wiki/README.md describes the mirror and is never published; tools/wiki-pull.sh
# carries it across the rewrite rather than fetching it. .pulled and .ids are
# the mirror's own bookkeeping. None of the three is a page an operator reads,
# so none of them is a draft waiting to go out.
CHANGED=$(git diff --name-status "$PULL" HEAD -- wiki/ |
          grep -v -E '	wiki/(README\.md|\.pulled|\.ids)$' || true)

echo "last pull: $(git log -1 --format='%h %ad' --date=iso-strict -- wiki/.pulled)"
echo "           wiki said $(cat wiki/.pulled 2>/dev/null || echo '(no stamp)')"

if [ -z "$CHANGED" ]; then
  echo "the mirror matches the last pull: nothing is waiting to be published."
  exit 0
fi

# Split by git's status letter rather than by looking for the file on the
# wiki, which needs a token. An added page has no live document at all, and
# tools/wiki-push.sh updates documents and cannot create one - so the two
# groups need different work and are not reported as one number.
EDITED=$(printf '%s\n' "$CHANGED" | grep -c '^M' || true)
ADDED=$(printf '%s\n' "$CHANGED" | grep -c '^A' || true)
GONE=$(printf '%s\n' "$CHANGED" | grep -c '^D' || true)

echo
echo "$EDITED page(s) corrected here and still showing the old text to operators:"
printf '%s\n' "$CHANGED" | sed -n 's/^M\t/  /p'

if [ "$ADDED" -gt 0 ]; then
  echo
  echo "$ADDED page(s) written here that do not exist on the wiki at all:"
  printf '%s\n' "$CHANGED" | sed -n 's/^A\t/  /p'
  echo "  tools/wiki-push.sh updates a document; it cannot create one."
  echo "  These have to be created on the wiki first, then pulled, so the"
  echo "  mirror learns their document ids."
fi

if [ "$GONE" -gt 0 ]; then
  echo
  echo "$GONE page(s) deleted here and still published:"
  printf '%s\n' "$CHANGED" | sed -n 's/^D\t/  /p'
fi

echo
echo "To publish: WIKI_TOKEN=... sh tools/wiki-push.sh <file>, one page at a"
echo "time. Each push checks that document on its own and refuses if somebody"
echo "edited it after the last pull, so there is no need to pull first - and"
echo "pulling first would overwrite every page listed above."
exit 1
