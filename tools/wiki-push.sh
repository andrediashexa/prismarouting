#!/bin/sh
# Publish one page to the wiki, refusing to overwrite somebody else's edit.
#
#   WIKI_TOKEN=... sh tools/wiki-push.sh <document-id> <file.md>
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
# NO TOKEN OR HOST LIVES HERE. WIKI_TOKEN and WIKI_URL come from the
# environment; PADRAO-DOCUMENTACAO.md says credentials never go into the wiki,
# and the same applies to the repository that mirrors it.
set -eu

ID=${1:-}
FILE=${2:-}
URL=${WIKI_URL:-https://hexarouting.hexanetworks.com.br}
[ -n "$ID" ] && [ -n "$FILE" ] || {
  echo "usage: WIKI_TOKEN=... sh tools/wiki-push.sh <document-id> <file.md>" >&2
  exit 1
}
[ -n "${WIKI_TOKEN:-}" ] || { echo "error: set WIKI_TOKEN" >&2; exit 1; }
[ -f "$FILE" ] || { echo "error: $FILE does not exist" >&2; exit 1; }

REPO=$(cd "$(dirname "$0")/.." && pwd)
STAMP="$REPO/wiki/.pulled"

live=$(curl -sk -X POST "$URL/api/documents.info" \
  -H "Authorization: Bearer $WIKI_TOKEN" -H "Content-Type: application/json" \
  -d "{\"id\":\"$ID\"}")

python3 - "$live" "$STAMP" "$ID" <<'PYEOF'
import json, os, sys
live, stamp_path, doc_id = sys.argv[1], sys.argv[2], sys.argv[3]
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
PYEOF

python3 -c "
import json, sys
print(json.dumps({'id': sys.argv[1], 'text': open(sys.argv[2]).read(),
                  'publish': True}))
" "$ID" "$FILE" > /tmp/wiki-push-payload.json

curl -sk -X POST "$URL/api/documents.update" \
  -H "Authorization: Bearer $WIKI_TOKEN" -H "Content-Type: application/json" \
  -d @/tmp/wiki-push-payload.json |
  python3 -c "
import json, sys
d = json.load(sys.stdin)
if not d.get('ok'):
    sys.exit('error: the wiki refused the update: %s' % d)
print('  published:', d['data']['title'])
"
rm -f /tmp/wiki-push-payload.json
