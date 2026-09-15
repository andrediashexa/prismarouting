#!/bin/sh
# Does pull -> push leave the page alone?
#
# The failure this test exists to prevent has already shipped: wiki-pull.sh
# writes `# <title>` into each file so the mirror reads as a page, wiki-push.sh
# sent the file verbatim, and Outline keeps the title in a field of its own -
# so a page that made the documented round trip grew a SECOND title above its
# opening sentence, and every further round trip added another (#165).
#
# NO WIKI IS REACHED. There is no token here and there must not be one: `curl`
# is replaced on PATH by a stub that answers from a fixture and captures what
# would have been sent. So this asserts the two scripts agree with each other
# about the file format, which is where the defect was - not that Outline
# stores back what it is handed.
set -u
fail=0
ok()  { echo "  $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

DOCID=doc-0001
TITLE="Primeiros passos"
# Deliberately not a one-liner: a heading of its own further down, a blank
# line, and the accented characters the real pages are written in. A round trip
# that only survives ASCII is one that corrupts this wiki.
BODY='Do disco vazio até assinantes navegando, em uma caixa só.

## O que você precisa

Uma máquina com duas placas de rede.
'

# --- the wiki, as far as these two scripts can tell ---------------------------
#
# One stub for both endpoints, dispatching on the URL the way the real service
# does. documents.list answers one page so wiki-pull.sh's paging loop
# terminates; documents.info feeds the staleness check; documents.update is
# where the payload lands, and it is COPIED rather than reported, because the
# thing under test is its exact bytes.
mkdir -p "$WORK/bin"
cat > "$WORK/bin/curl" <<'STUB'
#!/bin/sh
url=""
data=""
while [ $# -gt 0 ]; do
  case "$1" in
    -d) data=$2; shift 2 ;;
    http*|https*) url=$1; shift ;;
    -H|-X|--max-time) shift 2 ;;
    *) shift ;;
  esac
done
case "$url" in
  *collections.list)
    printf '{"data":[{"id":"col-1","name":"comecar-aqui"}]}' ;;
  *documents.list)
    cat "$WIKI_STUB/documents.list.json" ;;
  *documents.info)
    cat "$WIKI_STUB/documents.info.json" ;;
  *documents.update)
    case "$data" in
      @*) cp "${data#@}" "$WIKI_STUB/sent.json" ;;
      *)  printf '%s' "$data" > "$WIKI_STUB/sent.json" ;;
    esac
    printf '{"ok":true,"data":{"title":"stub"}}' ;;
  *)
    printf '{"ok":false,"error":"the stub was asked for %s"}' "$url" ;;
esac
STUB
chmod +x "$WORK/bin/curl"

STUB="$WORK/stub"; mkdir -p "$STUB"
export WIKI_STUB="$STUB"
printf '%s' "$BODY" > "$STUB/body.txt"
python3 - "$STUB" "$DOCID" "$TITLE" <<'PYEOF'
import json, sys
stub, doc_id, title = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(stub + "/body.txt").read()
doc = {"id": doc_id, "parentDocumentId": None, "title": title, "text": text,
       "collectionId": "col-1", "publishedAt": "2026-09-01T00:00Z",
       "updatedAt": "2026-09-01T00:00Z"}
json.dump({"data": [doc],
           "pagination": {"offset": 0, "limit": 100, "total": 1}},
          open(stub + "/documents.list.json", "w"))
json.dump({"data": doc}, open(stub + "/documents.info.json", "w"))
PYEOF

# The scripts resolve the repository from their own path and wiki-pull.sh
# DELETES the wiki/ directory it finds there. So they are run out of a copy:
# a test that rewrites the mirror it is run from is a test nobody can run
# twice.
mkdir -p "$WORK/repo/tools" "$WORK/repo/wiki"
cp "$REPO/tools/wiki-pull.sh" "$REPO/tools/wiki-push.sh" \
   "$REPO/tools/wiki-status.sh" "$WORK/repo/tools/"
cp "$REPO/wiki/README.md" "$WORK/repo/wiki/"

export PATH="$WORK/bin:$PATH"
export WIKI_TOKEN=stub-token
# Unroutable on purpose. If the stub above ever stopped intercepting, this is
# what would be dialled, and nothing is listening at .invalid by RFC 6761.
export WIKI_URL=http://wiki.invalid

echo "=== pull writes the page with its title as a heading ==="
sh "$WORK/repo/tools/wiki-pull.sh" > "$WORK/pull.log" 2>&1 || {
  bad "wiki-pull.sh failed:"; sed 's/^/    /' "$WORK/pull.log"; }
MIRROR="$WORK/repo/wiki/primeiros-passos.md"
if [ -f "$MIRROR" ]; then
  ok "$(head -1 "$MIRROR")"
  [ "$(head -1 "$MIRROR")" = "# $TITLE" ] || \
    bad "the mirror does not open with the title: $(head -1 "$MIRROR")"
else
  bad "wiki-pull.sh wrote no file at $MIRROR"
fi

echo "=== pushing it back unedited sends the body and not the title ==="
# THE assertion. What is compared is the bytes of the payload against the bytes
# the wiki already held - not "the payload looks reasonable", and not the
# rendered page, which nobody here can see.
sh "$WORK/repo/tools/wiki-push.sh" "$DOCID" "$MIRROR" > "$WORK/push.log" 2>&1 || {
  bad "wiki-push.sh failed:"; sed 's/^/    /' "$WORK/push.log"; }
if [ -f "$STUB/sent.json" ]; then
  python3 - "$STUB" <<'PYEOF'
import json, sys
stub = sys.argv[1]
sent = json.load(open(stub + "/sent.json"))["text"]
was = open(stub + "/body.txt").read()
if sent == was:
    print("  the text sent is byte-identical to the text the wiki held")
else:
    print("  FAIL: the round trip changed the page")
    print("    held: %r" % was[:70])
    print("    sent: %r" % sent[:70])
    sys.exit(1)
PYEOF
  [ $? -eq 0 ] || fail=$((fail+1))
  # Said twice on purpose, and the second way is the one an operator would
  # recognise: a duplicated title is a `# ` on the first line.
  first=$(python3 -c "
import json,sys
print(json.load(open(sys.argv[1]))['text'].split(chr(10))[0])" "$STUB/sent.json")
  case "$first" in
    "# "*) bad "the payload still opens with a heading: $first" ;;
    *) ok "and it does not open with a heading: $(printf '%.40s' "$first")" ;;
  esac
else
  bad "wiki-push.sh sent nothing"
fi

echo "=== a second round trip changes nothing either ==="
# The defect grew the page by one title EVERY time, so a test that pushes once
# is satisfied by a fix that only works once.
cp "$STUB/sent.json" "$WORK/first.json"
# The wiki now holds what was just sent, in BOTH endpoints. Feeding it back
# through only one of them is what the first version of this did, and it made
# the second pull read the ORIGINAL text - so the growth this exists to catch
# could not happen and the case passed against the defect itself.
python3 - "$STUB" <<'PYEOF'
import json, sys
stub = sys.argv[1]
text = json.load(open(stub + "/sent.json"))["text"]
info = json.load(open(stub + "/documents.info.json"))
info["data"]["text"] = text
json.dump(info, open(stub + "/documents.info.json", "w"))
lst = json.load(open(stub + "/documents.list.json"))
lst["data"][0]["text"] = text
json.dump(lst, open(stub + "/documents.list.json", "w"))
PYEOF
sh "$WORK/repo/tools/wiki-pull.sh" > "$WORK/pull2.log" 2>&1 || \
  bad "the second wiki-pull.sh failed"
sh "$WORK/repo/tools/wiki-push.sh" "$DOCID" "$MIRROR" > "$WORK/push2.log" 2>&1 || \
  bad "the second wiki-push.sh failed"
if cmp -s "$WORK/first.json" "$STUB/sent.json"; then
  ok "the second round trip sent exactly what the first one did"
else
  bad "the second round trip differs from the first"
  diff "$WORK/first.json" "$STUB/sent.json" | sed 's/^/    /'
fi

echo "=== a heading that is NOT the title stops the push ==="
# The third acceptance criterion of #165, and the reason the strip is a match
# and not a blind first-line delete: a file that opens with somebody's real
# heading is not a pulled page, and publishing its body silently is how the
# original defect got in.
printf '# Outra coisa qualquer\n\ntexto\n' > "$WORK/wrong.md"
rm -f "$STUB/sent.json"
if sh "$WORK/repo/tools/wiki-push.sh" "$DOCID" "$WORK/wrong.md" \
     > "$WORK/wrong.log" 2>&1; then
  bad "a heading that is not the title was published anyway"
else
  if grep -q "not this document's title" "$WORK/wrong.log"; then
    ok "refused: $(grep -o "opens with.*" "$WORK/wrong.log" | head -1)"
  else
    bad "it failed, but not with the sentence that says why:"
    sed 's/^/    /' "$WORK/wrong.log"
  fi
fi
[ -f "$STUB/sent.json" ] && bad "it refused and sent the document anyway"

echo "=== a file with no heading at all is sent as it stands ==="
# Not every file comes from a pull. One written by hand has no title line and
# must not have its first line eaten.
printf 'texto sem titulo nenhum\n' > "$WORK/plain.md"
rm -f "$STUB/sent.json"
sh "$WORK/repo/tools/wiki-push.sh" "$DOCID" "$WORK/plain.md" \
  > "$WORK/plain.log" 2>&1 || bad "a plain file was refused"
if [ -f "$STUB/sent.json" ]; then
  got=$(python3 -c "
import json,sys; print(json.load(open(sys.argv[1]))['text'])" "$STUB/sent.json")
  [ "$got" = "texto sem titulo nenhum" ] && ok "sent unchanged: $got" || \
    bad "a file with no heading came out as '$got'"
else
  bad "a plain file sent nothing"
fi

echo "=== the pull records which document each file came from (#277) ==="
# The mirror stood 28 pages ahead of the live wiki and none of them could be
# published without first finding 28 document ids in a web interface by hand.
# The ids are known at pull time and were thrown away; now they are written
# down, keyed on the path the same line decides.
INDEX="$WORK/repo/wiki/.ids"
if [ -f "$INDEX" ]; then
  line=$(grep '^primeiros-passos\.md ' "$INDEX" || true)
  [ "$line" = "primeiros-passos.md $DOCID" ] && ok "index says: $line" || \
    bad "the index does not map the page to $DOCID: ${line:-<no entry>}"
else
  bad "wiki-pull.sh wrote no index at $INDEX"
fi

echo "=== push takes the file alone and looks the id up ==="
rm -f "$STUB/sent.json"
sh "$WORK/repo/tools/wiki-push.sh" "$MIRROR" > "$WORK/byfile.log" 2>&1 || {
  bad "wiki-push.sh refused a file that is in the index:"
  sed 's/^/    /' "$WORK/byfile.log"; }
if [ -f "$STUB/sent.json" ]; then
  # The id it RESOLVED, not the id it printed. A push that says doc-0001 and
  # sends something else overwrites a page nobody was looking at.
  sent_id=$(python3 -c "
import json,sys; print(json.load(open(sys.argv[1]))['id'])" "$STUB/sent.json")
  [ "$sent_id" = "$DOCID" ] && ok "it sent to $sent_id, from the index alone" \
    || bad "one argument resolved to '$sent_id', not '$DOCID'"
else
  bad "one argument sent nothing"
fi

echo "=== a page the wiki does not have yet stops the push ==="
# The one page in #277's list that no token would have published either:
# documents.update cannot create a document, so a file with no entry has no
# id to guess and the push must not invent one.
mkdir -p "$WORK/repo/wiki/operar"
printf '# Pagina nova\n\ncorpo\n' > "$WORK/repo/wiki/operar/pagina-nova.md"
rm -f "$STUB/sent.json"
if sh "$WORK/repo/tools/wiki-push.sh" "$WORK/repo/wiki/operar/pagina-nova.md" \
     > "$WORK/new.log" 2>&1; then
  bad "a page with no document id was published against some other id"
else
  if grep -q "cannot create one" "$WORK/new.log"; then
    ok "refused: $(grep -o 'is not in .*' "$WORK/new.log" | head -1)"
  else
    bad "it failed, but not with the sentence that says why:"
    sed 's/^/    /' "$WORK/new.log"
  fi
fi
[ -f "$STUB/sent.json" ] && bad "it refused and sent the document anyway"

echo "=== and with no index at all it says to pull, not 'no such id' ==="
mv "$INDEX" "$WORK/ids.saved"
rm -f "$STUB/sent.json"
if sh "$WORK/repo/tools/wiki-push.sh" "$MIRROR" > "$WORK/noindex.log" 2>&1; then
  bad "a push with no index published anyway"
else
  grep -q "wiki-pull.sh" "$WORK/noindex.log" && \
    ok "refused, and names the script that writes the index" || {
      bad "it failed without saying where the index comes from:"
      sed 's/^/    /' "$WORK/noindex.log"; }
fi
mv "$WORK/ids.saved" "$INDEX"
rm -f "$WORK/repo/wiki/operar/pagina-nova.md"

echo "=== the pull refuses to throw away work nobody published (#277) ==="
# The whole of #277 in one case. The advice an operator was given was "pull
# first, then push"; the pull deletes wiki/ and rewrites it from the live
# wiki, so following that advice with 28 corrections in the tree loses all 28
# before one of them goes out.
#
# A separate repository, because this one needs a git history: the last commit
# that wrote wiki/.pulled is what the mirror is compared against, and that is
# the only holder of "when were these the same text". Nothing greps a page.
GITREPO="$WORK/gitrepo"
mkdir -p "$GITREPO/tools" "$GITREPO/wiki"
cp "$REPO/tools/wiki-pull.sh" "$REPO/tools/wiki-push.sh" \
   "$REPO/tools/wiki-status.sh" "$GITREPO/tools/"
cp "$REPO/wiki/README.md" "$GITREPO/wiki/"
printf '# %s\n\n%s' "$TITLE" "$BODY" > "$GITREPO/wiki/primeiros-passos.md"
printf 'primeiros-passos.md %s\n' "$DOCID" > "$GITREPO/wiki/.ids"
printf '2026-09-01T00:00Z\n' > "$GITREPO/wiki/.pulled"
git -C "$GITREPO" init -q
git -C "$GITREPO" -c user.email=t@t -c user.name=t add -A
git -C "$GITREPO" -c user.email=t@t -c user.name=t -c commit.gpgsign=false \
  commit -q -m "the mirror as pulled"

if sh "$GITREPO/tools/wiki-status.sh" > "$WORK/clean.log" 2>&1; then
  ok "a mirror that matches the last pull is reported clean"
else
  bad "a freshly pulled mirror was reported as holding drafts:"
  sed 's/^/    /' "$WORK/clean.log"
fi

# One page corrected and one page written, both AFTER the pull commit. The two
# need different work and the report must not merge them into one number.
printf '# %s\n\n%s\n\nUma frase corrigida.\n' "$TITLE" "$BODY" \
  > "$GITREPO/wiki/primeiros-passos.md"
printf '# Outra pagina\n\ncorpo\n' > "$GITREPO/wiki/outra-pagina.md"
git -C "$GITREPO" -c user.email=t@t -c user.name=t add -A
git -C "$GITREPO" -c user.email=t@t -c user.name=t -c commit.gpgsign=false \
  commit -q -m "a correction nobody published"

if sh "$GITREPO/tools/wiki-status.sh" > "$WORK/dirty.log" 2>&1; then
  bad "an unpublished correction was reported as nothing to do:"
  sed 's/^/    /' "$WORK/dirty.log"
else
  grep -q "primeiros-passos.md" "$WORK/dirty.log" && \
  grep -q "do not exist on the wiki at all" "$WORK/dirty.log" && \
    ok "$(grep -c '^  wiki/' "$WORK/dirty.log") unpublished page(s) named, split by what they need" || {
      bad "it reported drafts without naming them, or without splitting them:"
      sed 's/^/    /' "$WORK/dirty.log"; }
fi

before=$(cat "$GITREPO/wiki/primeiros-passos.md")
if sh "$GITREPO/tools/wiki-pull.sh" > "$WORK/refused.log" 2>&1; then
  bad "wiki-pull.sh wiped the mirror with unpublished corrections in it"
else
  grep -q "primeiros-passos.md" "$WORK/refused.log" && \
    ok "the pull refused and named the page it would have destroyed" || {
      bad "the pull failed without saying which work was at risk:"
      sed 's/^/    /' "$WORK/refused.log"; }
fi
[ "$(cat "$GITREPO/wiki/primeiros-passos.md")" = "$before" ] && \
  ok "and the correction is still in the tree" || \
  bad "the pull refused and deleted the page anyway"

# THE DEADLOCK THE TWO HALVES MAKE TOGETHER, which is the state somebody
# arriving with the token is actually in. The push needs wiki/.ids; only the
# pull writes it; and the pull refuses while drafts are outstanding. Without a
# way to fetch the index alone, the only route left is the 28 ids by hand that
# the index was added to remove - or WIKI_PULL_DISCARD=1, which destroys the
# work. So: the index is fetched, and NOT ONE PAGE MOVES.
rm -f "$GITREPO/wiki/.ids"
pulled_before=$(cat "$GITREPO/wiki/.pulled")
before=$(cat "$GITREPO/wiki/primeiros-passos.md")
if WIKI_IDS_ONLY=1 sh "$GITREPO/tools/wiki-pull.sh" \
     > "$WORK/idsonly.log" 2>&1; then
  [ -s "$GITREPO/wiki/.ids" ] && \
    ok "the index alone can be fetched with drafts outstanding" || \
    bad "WIKI_IDS_ONLY=1 succeeded and wrote no index"
else
  bad "WIKI_IDS_ONLY=1 could not fetch the index:"
  sed 's/^/    /' "$WORK/idsonly.log"
fi
[ "$(cat "$GITREPO/wiki/primeiros-passos.md")" = "$before" ] && \
  ok "and the unpublished correction is untouched" || \
  bad "WIKI_IDS_ONLY=1 overwrote the page it was supposed to leave alone"
[ -f "$GITREPO/wiki/outra-pagina.md" ] && \
  ok "and the page the wiki has never seen is still there" || \
  bad "WIKI_IDS_ONLY=1 deleted a page that exists only here"
[ "$(cat "$GITREPO/wiki/.pulled")" = "$pulled_before" ] && \
  ok "and .pulled is unmoved, so the mirror does not claim to be fresh" || \
  bad "WIKI_IDS_ONLY=1 moved .pulled, which would report 28 drafts as published"

# The override, because a refusal with no way past it is a script somebody
# edits out. It has to be asked for by name, and only then.
if WIKI_PULL_DISCARD=1 sh "$GITREPO/tools/wiki-pull.sh" \
     > "$WORK/discard.log" 2>&1; then
  [ "$(cat "$GITREPO/wiki/primeiros-passos.md")" = "$before" ] && \
    bad "WIKI_PULL_DISCARD=1 was asked to overwrite and did not" || \
    ok "WIKI_PULL_DISCARD=1 pulls over it, having said so"
else
  bad "WIKI_PULL_DISCARD=1 could not pull:"
  sed 's/^/    /' "$WORK/discard.log"
fi

echo "=== and the mirror itself keeps to that format (#225) ==="
# Everything above drives the two scripts against a stub and asserts they agree
# with each other about the format. That is where the #165 defect was, and it
# is fixed. What nothing looked at is the MIRROR: five pages had accumulated
# their own title two, three and four times before anybody noticed, because
# wiki-push.sh strips exactly one and the rest go out as body.
#
# A level-1 heading further down a page is ordinary and stays allowed:
# wiki/operar/receitas-do-dia-a-dia.md has several and is right. What is
# checked is the RUN at the top - the title, then the same title again before
# any body text.
python3 - "$REPO" <<'PY'
import os, sys
repo = sys.argv[1]
bad = []
seen = 0
for root, _, files in os.walk(os.path.join(repo, "wiki")):
    for f in sorted(files):
        if not f.endswith(".md"):
            continue
        path = os.path.join(root, f)
        lines = open(path, encoding="utf-8").read().split("\n")
        rel = os.path.relpath(path, repo)
        seen += 1
        if not lines or not lines[0].startswith("# "):
            bad.append(f"{rel} does not open with '# <title>', which is what "
                       f"wiki-push.sh strips and Outline keeps as the title")
            continue
        title, i, n = lines[0], 1, 1
        while i < len(lines):
            if lines[i].strip() == "":
                i += 1
                continue
            if lines[i] == title:
                n += 1
                i += 1
                continue
            break
        if n > 1:
            bad.append(f"{rel} opens with its title {n} times; push strips one "
                       f"and the other {n - 1} are published as body")
for b in bad:
    print(f"  FAIL: {b}")
print(f"  checked {seen} page(s)")
sys.exit(1 if bad else 0)
PY
if [ $? -eq 0 ]; then
  ok "every page in the mirror opens with exactly one title"
else
  bad "the mirror has drifted from the format the two scripts agree on"
fi

echo
echo "=== the release gate reads this, so nothing ships past a stale wiki ==="
# WHY THE GATE AND NOT THIS SUITE. A page on the published wiki that disagrees
# with the product is the product lying to somebody holding a screen and
# deciding what state their box is in - one of the pages sitting uncorrected
# told an operator that a refused commit applies nothing, which stopped being
# true when the FRR read-back check became a refusal that leaves the box
# half-configured. But a check that goes red on every `make test` is a check
# people learn to run around, and the person who can publish a page is the one
# with the token, standing in front of a release. So the gate asks, once, at
# the act it belongs to (#277).
#
# WHAT IS ASSERTED HERE IS AGREEMENT, not a verdict. "The gate refuses" would
# be a check that breaks the day the wiki is published, which is the day the
# product gets better - so this runs the holder and the gate and requires them
# to say the same thing, which is true before and after. The verdict itself is
# measured above, on the holder, in both directions.
#
# RELEASE_GATE_CHECK_ONLY, so this costs a clone and not a 321 MB image.
GATE_TAG=$(git -C "$REPO" tag --sort=-v:refname 2>/dev/null | head -1)
if [ -z "$GATE_TAG" ]; then
  bad "this repository has no tag, so the gate cannot be driven at all"
else
  if sh "$REPO/tools/wiki-status.sh" >/dev/null 2>&1; then
    HOLDER=clean
  else
    HOLDER=drafts
  fi
  RELEASE_WORK="$WORK/gate" RELEASE_GATE_CHECK_ONLY=1 \
    sh "$REPO/tools/release-gate.sh" "$GATE_TAG" > "$WORK/gate.log" 2>&1 || true
  # The gate has checks before this one and any of them can refuse first. What
  # proves it reached the wiki step is one of the wiki step's own two lines,
  # and a run that shows neither proves nothing either way - so it is a
  # failure here rather than a silent pass.
  if grep -q "the published wiki matches this tree" "$WORK/gate.log"; then
    GATE=clean
  elif grep -q "would ship with the published wiki behind it" "$WORK/gate.log"; then
    GATE=drafts
  else
    GATE=unreached
  fi
  case "$GATE" in
    unreached)
      bad "the gate never reached the wiki step for $GATE_TAG, so nothing \
here says whether it reads this at all. It stopped at:"
      grep -m1 '^error:' "$WORK/gate.log" | sed 's/^/    /' ;;
    "$HOLDER")
      ok "the gate and tools/wiki-status.sh agree: $GATE" ;;
    *)
      bad "the holder says '$HOLDER' and the gate says '$GATE', so the gate \
is deciding this for itself instead of reading the one place it is written" ;;
  esac
  # And it repeats what the holder said rather than listing pages of its own.
  # A second list is a second thing to keep true, and this one would be read
  # by somebody about to publish.
  if [ "$HOLDER" = drafts ] && [ "$GATE" = drafts ]; then
    FIRSTPAGE=$(sh "$REPO/tools/wiki-status.sh" 2>&1 |
                sed -n 's/^  \(wiki\/.*\.md\)$/\1/p' | head -1)
    if [ -n "$FIRSTPAGE" ] && grep -q "$FIRSTPAGE" "$WORK/gate.log"; then
      ok "and the refusal names the pages the holder named"
    else
      bad "the gate refused without naming what the holder named \
($FIRSTPAGE), so an operator with the token is told to publish and not what"
    fi
  fi
fi

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
