#!/bin/sh
# Refresh the local mirror of the wiki under wiki/.
#
#   WIKI_HOST=<host> sh tools/wiki-pull.sh
#
# The wiki (Outline) is the live copy and the one people read. This mirror
# exists so the pages can be read, searched and diffed from inside the
# repository - not as a second place to edit them. Editing a file under wiki/
# changes nothing: the next pull overwrites it.
#
# NO CREDENTIALS LIVE HERE. The host comes from WIKI_HOST and the connection
# uses whatever ssh is already configured to use. PADRAO-DOCUMENTACAO.md says
# passwords, keys, management addresses and internal machine names never go
# into the wiki; the same applies to the repository that mirrors it.
set -eu

HOST=${WIKI_HOST:-}
[ -n "$HOST" ] || {
  echo "error: set WIKI_HOST to the machine running Outline." >&2
  echo "       No address is kept in this repository on purpose." >&2
  exit 1
}

REPO=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "==> reading published documents from $HOST"
ssh -o ConnectTimeout=10 "root@$HOST" 'docker exec outline-postgres psql -U outline -d outline -At -c "
select json_agg(row_to_json(d)) from (
  select doc.id, doc.\"parentDocumentId\" as parent, doc.title, doc.text,
         c.name as collection, doc.\"updatedAt\"
  from documents doc left join collections c on c.id = doc.\"collectionId\"
  where doc.\"deletedAt\" is null and doc.\"publishedAt\" is not null
  order by c.name, doc.\"parentDocumentId\" nulls first, doc.title
) d;"' > "$TMP/docs.json"

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
print("%d pages" % n[0])
PYEOF

echo "==> done. 'git diff wiki/' shows what changed since the last pull."
