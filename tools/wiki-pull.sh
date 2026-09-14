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

# This script DELETES wiki/ and rewrites it from the live wiki. Anything
# corrected here and not yet published is gone from the tree the moment it
# runs, and #277 is what that costs: 28 pages were corrected here over five
# days, one of them saying what state a box is left in by a refused commit,
# and the published advice to "pull first, then push" would have thrown all 28
# away before a single one went out.
#
# There is no need to pull first. tools/wiki-push.sh reads the live document
# before it writes and refuses if somebody edited it after wiki/.pulled - the
# protection is per document and it does not depend on this having just run.
# So the order is push, then pull.

# rc is captured off the assignment itself. Inside `if ! VAR=$(cmd)` the shell
# has already replaced $? with the status of the negation, so the three cases
# below would all read 0.
rc=0
DRAFTS=$(sh "$REPO/tools/wiki-status.sh" 2>&1) || rc=$?
if [ "$rc" -ne 0 ] && [ "${WIKI_IDS_ONLY:-0}" != "1" ]; then
  case $rc in
    1)
      echo "$DRAFTS" >&2
      echo >&2
      echo "error: refusing to pull. The lines above are edits that exist" >&2
      echo "       ONLY here, and this rewrites wiki/ from the live wiki." >&2
      echo "       Publish them first. If the push cannot find their" >&2
      echo "       document ids, WIKI_IDS_ONLY=1 sh tools/wiki-pull.sh" >&2
      echo "       fetches only wiki/.ids and touches no page." >&2
      echo "       WIKI_PULL_DISCARD=1 throws the edits away on purpose." >&2
      [ "${WIKI_PULL_DISCARD:-0}" = "1" ] || exit 1
      echo "==> WIKI_PULL_DISCARD=1: discarding the edits listed above" >&2
      ;;
    *)
      # Not a git checkout, or a checkout that never recorded a pull. The
      # question cannot be answered here, and answering it wrongly either way
      # is worse than saying so.
      echo "$DRAFTS" >&2
      echo "warning: could not tell whether wiki/ holds unpublished edits." >&2
      ;;
  esac
fi

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

python3 - "$TMP/docs.json" "$REPO" "${WIKI_IDS_ONLY:-0}" <<'PYEOF'
import json, os, re, shutil, sys, unicodedata
from datetime import datetime, timezone

src, repo = sys.argv[1], sys.argv[2]
# INDEX ONLY: learn which document each page is, and touch nothing else.
#
# Without it the two halves of #277 deadlock. The push needs wiki/.ids; only
# this script writes it; and this script refuses to run while there are
# unpublished drafts, because it deletes wiki/ - which is exactly the state
# somebody arriving with the token is in. The only way through was to pass 28
# ids by hand, which is what the index was added to stop.
#
# Same walk, same slug(), same line deciding the path and the id together, so
# the index cannot describe a layout the pull would not produce. What is
# skipped is every write except the index: no rmtree, no page, no .pulled, and
# no README stamp, because none of those is true of a mirror this did not
# refresh.
ids_only = sys.argv[3] == "1"
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
saved = None
if not ids_only:
    saved = open(keep).read() if os.path.exists(keep) else None
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(root)

n = [0]
# Which document each file came from. Written here because this is the only
# place that knows: the id and the path are decided by the same line, so they
# cannot drift apart. It is read by tools/wiki-push.sh, which until #277 made
# the caller supply the id by hand - and a mirror 28 pages behind is 28 ids to
# find in a web interface before a single correction reaches an operator.
ids = []
def walk(parent, path):
    for d in sorted(by_parent.get(parent, []), key=lambda x: x["title"]):
        if d["title"] in DEFAULTS:
            continue
        kids = [k for k in by_parent.get(d["id"], [])
                if k["title"] not in DEFAULTS]
        name = slug(d["title"])
        if kids:
            sub = os.path.join(path, name)
            if not ids_only:
                os.makedirs(sub, exist_ok=True)
            f = os.path.join(sub, "index.md")
        else:
            f = os.path.join(path, name + ".md")
        if not ids_only:
            open(f, "w").write("# %s\n\n%s\n"
                               % (d["title"], (d["text"] or "").strip()))
        ids.append((os.path.relpath(f, root), d["id"]))
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
if not ids_only:
    open(os.path.join(root, ".pulled"), "w").write(newest + "\n")
# Sorted by path, one page per line, so a diff of this file reads as pages
# appearing and disappearing rather than as a reordering. Neither field can
# contain a space: an Outline id is a uuid and a path is built out of slug()
# above, which keeps nothing but letters, digits and hyphens.
with open(os.path.join(root, ".ids"), "w") as fh:
    for rel, doc_id in sorted(ids):
        fh.write("%s %s\n" % (rel, doc_id))
if ids_only:
    print("%d document id(s) written to wiki/.ids; no page was touched"
          % len(ids))
else:
    print("%d pages, newest change %s" % (n[0], newest))
PYEOF

echo "==> done. 'git diff wiki/' shows what changed since the last pull."
