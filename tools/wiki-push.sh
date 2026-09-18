#!/bin/sh
# Publish one page to the wiki, refusing to overwrite somebody else's edit.
#
#   WIKI_TOKEN=... sh tools/wiki-push.sh <file.md>
#   WIKI_TOKEN=... sh tools/wiki-push.sh <document-id> <file.md>
#
# One argument is the ordinary case: the document id comes from wiki/.ids,
# which tools/wiki-pull.sh writes as it lays the mirror out. Two arguments
# still work, for a page that is not in the mirror.
#
# The id used to be the caller's problem, and #277 is what that cost. The
# mirror stood 28 pages ahead of the live wiki, and publishing them meant
# finding 28 ids in a web interface first. Nobody did.
#
# The wiki is being written by people, and this pushes over what they wrote.
# So it checks first: if the live document has changed since the mirror under
# wiki/ was pulled, it refuses and says to pull again.
#
# That check exists because of a near miss. Four pages were updated through the
# API, the mirror was never refreshed, and a later edit was built from the
# stale copy - at the same time the maintainer was renaming a category and
# removing pages. Nothing was lost, and only because the stale text failed to
# match rather than because anything noticed.
#
# The mirror's files open with `# <title>`, which Outline does NOT keep in the
# body - the title is a field of its own. That heading is stripped here before
# the text is sent, so pull, edit, push is a no-op on a file nobody edited. It
# was not, and each round trip grew the page a second title (#165).
#
# NO TOKEN OR HOST LIVES HERE. WIKI_TOKEN and WIKI_URL come from the
# environment; PADRAO-DOCUMENTACAO.md says credentials never go into the wiki,
# and the same applies to the repository that mirrors it.
set -eu

URL=${WIKI_URL:-https://prisma-routing.hexanetworks.com.br}
REPO=$(cd "$(dirname "$0")/.." && pwd)
STAMP="$REPO/wiki/.pulled"
INDEX="$REPO/wiki/.ids"

case $# in
  1) ID=""   ; FILE=$1 ;;
  2) ID=$1   ; FILE=$2 ;;
  *)
    echo "usage: WIKI_TOKEN=... sh tools/wiki-push.sh <file.md>" >&2
    echo "       WIKI_TOKEN=... sh tools/wiki-push.sh <document-id> <file.md>" >&2
    exit 1 ;;
esac
[ -n "${WIKI_TOKEN:-}" ] || { echo "error: set WIKI_TOKEN" >&2; exit 1; }
[ -f "$FILE" ] || { echo "error: $FILE does not exist" >&2; exit 1; }

# Looked up by the file's path under wiki/, which is the key wiki-pull.sh
# wrote. A page drafted here that the wiki does not have yet is absent from
# the index, and that is not a lookup failure to shrug at: documents.update
# cannot create a document, so the push would go out against whatever id was
# guessed. It stops instead, and says which half of the work is missing.
if [ -z "$ID" ]; then
  [ -f "$INDEX" ] || {
    echo "error: $INDEX does not exist, so there is nothing to look the" >&2
    echo "       document id up in. Run tools/wiki-pull.sh, which writes it," >&2
    echo "       or pass the id as the first argument." >&2
    exit 1
  }
  REL=$(python3 -c '
import os, sys
print(os.path.relpath(os.path.abspath(sys.argv[1]),
                      os.path.join(sys.argv[2], "wiki")))' "$FILE" "$REPO")
  ID=$(awk -v want="$REL" '$1 == want { print $2; found = 1 }
                           END { exit !found }' "$INDEX") || {
    echo "error: $REL is not in $INDEX, so the wiki has no document for it." >&2
    echo "       This publishes an existing page and cannot create one." >&2
    echo "       Create the page on the wiki, pull, then push." >&2
    exit 1
  }
  echo "  $REL is document $ID"
fi

PAYLOAD=/tmp/wiki-push-payload.json

live=$(curl -sk -X POST "$URL/api/documents.info" \
  -H "Authorization: Bearer $WIKI_TOKEN" -H "Content-Type: application/json" \
  -d "{\"id\":\"$ID\"}")

# One block, and the payload is built here rather than a step later, because
# the TITLE the staleness check reads is the same title the body has to be
# stripped of. Two reads of the live document could disagree.
python3 - "$live" "$STAMP" "$ID" "$FILE" "$PAYLOAD" <<'PYEOF'
import json, os, sys
live, stamp_path, doc_id, path, payload = sys.argv[1:6]
try:
    d = json.loads(live)["data"]
except Exception:
    sys.exit("error: could not read the live document; is the token valid?")

if not os.path.exists(stamp_path):
    sys.exit("error: wiki/ has never been pulled. Run tools/wiki-pull.sh "
             "first - editing from a copy of unknown age is how somebody "
             "else's work gets overwritten.")

pulled = open(stamp_path).read().strip()
updated = d.get("updatedAt", "")
if updated > pulled:
    sys.exit(
        "error: '%s' changed on the wiki at %s, after this mirror was pulled "
        "at %s.\n       Pull again and rebuild the edit on what is there now. "
        "Pushing would\n       discard whoever made that change."
        % (d.get("title", doc_id), updated, pulled))
print("  %s last changed %s; mirror pulled %s" % (d.get("title"), updated, pulled))

# Outline keeps the title in a field of its own and NOT in the body.
# tools/wiki-pull.sh writes it into the file as a heading so the mirror reads
# as a page and diffs legibly; sending that heading back stores a second title
# above the opening sentence, which PADRAO-DOCUMENTACAO.md section 1 forbids,
# and every further round trip adds another one (#165).
#
# The heading has to MATCH. A file whose first line is some other heading is
# not a pulled page with a title on it - it is an edit that means something
# else, and quietly publishing it as the body is how the defect above happened
# in the first place. So that case stops, loudly, rather than guessing.
title = d.get("title") or ""
text = open(path).read()
head = "# %s\n" % title
if text.startswith(head):
    body = text[len(head):].lstrip("\n")
elif text.lstrip().startswith("# "):
    first = text.lstrip().split("\n", 1)[0]
    sys.exit("error: %s opens with %r, which is not this document's title "
             "%r.\n       A heading sent as the body becomes a second title "
             "on the page.\n       Pull the mirror again and edit what it "
             "writes." % (path, first, title))
else:
    body = text
json.dump({"id": doc_id, "text": body, "publish": True}, open(payload, "w"))
PYEOF

curl -sk -X POST "$URL/api/documents.update" \
  -H "Authorization: Bearer $WIKI_TOKEN" -H "Content-Type: application/json" \
  -d @"$PAYLOAD" |
  python3 -c "
import json, sys
d = json.load(sys.stdin)
if not d.get('ok'):
    sys.exit('error: the wiki refused the update: %s' % d)
print('  published:', d['data']['title'])
"
rm -f "$PAYLOAD"
