#!/bin/sh
# Refresh the local mirror of the wiki under wiki/.
#
#   WIKI_TOKEN=... sh tools/wiki-pull.sh
#
# The wiki (Outline) is the live copy and the one people read. This mirror
# exists so the pages can be read, searched and diffed from inside the
# repository, and so an edit can be DRAFTED against what the page actually
# says. A file changed under wiki/ reaches nobody until tools/wiki-push.sh
# publishes it, and the next pull overwrites whatever was not published.
#
# Each file opens with `# <title>`, which is the document's title field and
# NOT part of its body. wiki-push.sh strips that line back off before sending,
# so the round trip is a no-op; the two halves have to stay in step (#165).
#
# NO CREDENTIALS LIVE HERE. WIKI_TOKEN and WIKI_URL come from the environment.
# PADRAO-DOCUMENTACAO.md says passwords, keys, management addresses and
# internal machine names never go into the wiki; the same applies to the
# repository that mirrors it.
#
# This reads over Outline's HTTP API. It used to ssh in as root and query
# Postgres directly, which meant refreshing a read-only mirror required the
# machine's root password - the widest possible credential for the narrowest
# possible job. An API token scoped to one account does the same work, is
# revocable from the web interface without touching the host, and cannot be
# reused to log in anywhere.
set -eu

URL=${WIKI_URL:-https://hexarouting.hexanetworks.com.br}
[ -n "${WIKI_TOKEN:-}" ] || {
  echo "error: set WIKI_TOKEN to an Outline API token." >&2
  echo "       No token or address is kept in this repository on purpose." >&2
  exit 1
}

REPO=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "==> reading published documents over the API"
api() {
  curl -sk --max-time 30 -X POST "$URL/api/$1" \
    -H "Authorization: Bearer $WIKI_TOKEN" \
    -H "Content-Type: application/json" -d "$2"
}

api collections.list '{"limit":100}' > "$TMP/collections.json"
: > "$TMP/pages.json"
off=0
while : ; do
  api documents.list "{\"limit\":100,\"offset\":$off}" > "$TMP/page.json"
  cat "$TMP/page.json" >> "$TMP/pages.json"
  printf '\n' >> "$TMP/pages.json"
  more=$(python3 -c "
import json,sys
r=json.load(open('$TMP/page.json'))
p=r.get('pagination') or {}
print(1 if p.get('offset',0)+p.get('limit',0) < p.get('total',0) else 0)")
  [ "$more" = "1" ] || break
  off=$((off + 100))
done

python3 - "$TMP" <<'PYEOF' > "$TMP/docs.json"
import json, sys
tmp = sys.argv[1]
names = {c["id"]: c["name"]
         for c in json.load(open(tmp + "/collections.json"))["data"]}
docs = []
for line in open(tmp + "/pages.json"):
    line = line.strip()
    if not line:
        continue
    for d in json.loads(line)["data"]:
        # A draft is not documentation and a deleted page is not either.
        # Mirroring them would put text in the repository that nobody reading
        # the wiki can see.
        if not d.get("publishedAt") or d.get("deletedAt"):
            continue
        docs.append({"id": d["id"], "parent": d.get("parentDocumentId"),
                     "title": d["title"], "text": d.get("text") or "",
                     "collection": names.get(d.get("collectionId")),
                     "updatedAt": d["updatedAt"]})
docs.sort(key=lambda d: (d["collection"] or "", d["parent"] or "",
                         d["title"]))
json.dump(docs, sys.stdout)
PYEOF

[ -s "$TMP/docs.json" ] || { echo "error: nothing came back" >&2; exit 1; }

python3 - "$TMP/docs.json" "$REPO" <<'PYEOF'
import json, os, re, shutil, sys, unicodedata
from datetime import datetime, timezone

src, repo = sys.argv[1], sys.argv[2]
docs = json.load(open(src))
# Outline ships these; they are not ours and mirroring them would suggest they
# are.
DEFAULTS = {"Getting Started", "Integrations & API", "Our Editor",
            "What is Outline"}

def slug(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower() or "page"

by_parent = {}
for d in docs:
    by_parent.setdefault(d["parent"], []).append(d)

root = os.path.join(repo, "wiki")
keep = os.path.join(root, "README.md")
saved = open(keep).read() if os.path.exists(keep) else None
if os.path.isdir(root):
    shutil.rmtree(root)
os.makedirs(root)

n = [0]
def walk(parent, path):
    for d in sorted(by_parent.get(parent, []), key=lambda x: x["title"]):
        if d["title"] in DEFAULTS:
            continue
        kids = [k for k in by_parent.get(d["id"], [])
                if k["title"] not in DEFAULTS]
        name = slug(d["title"])
        if kids:
            sub = os.path.join(path, name)
            os.makedirs(sub, exist_ok=True)
            f = os.path.join(sub, "index.md")
        else:
            f = os.path.join(path, name + ".md")
        open(f, "w").write("# %s\n\n%s\n" % (d["title"], (d["text"] or "").strip()))
        n[0] += 1
        if kids:
            walk(d["id"], sub)

walk(None, root)
if saved:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    saved = re.sub(r"Snapshot taken .*", "Snapshot taken %s, %d pages."
                   % (stamp, n[0]), saved, count=1)
    open(keep, "w").write(saved)
# The high-water mark of what this pull saw. tools/wiki-push.sh refuses to
# publish over a document that changed after it - the mirror is a snapshot,
# and editing from a snapshot of unknown age is how somebody else's work gets
# overwritten.
newest = max((d.get("updatedAt") or "") for d in docs) if docs else ""
open(os.path.join(root, ".pulled"), "w").write(newest + "\n")
print("%d pages, newest change %s" % (n[0], newest))
PYEOF

echo "==> done. 'git diff wiki/' shows what changed since the last pull."
