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
    # A wiki that takes the request and says no. A FILE and not a variable,
    # because this stub is a fresh process on every call and carries nothing
    # across one; the case that wants it creates the file and removes it again.
    if [ -f "$WIKI_STUB/update-refuses" ]; then
      printf '{"ok":false,"error":"editing this document is not permitted"}'
    else
      printf '{"ok":true,"data":{"title":"stub"}}'
    fi ;;
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

echo "=== a document edited after the pull stops the push (the whole point) ==="
# THE refusal of wiki-push.sh, and nothing had ever driven it in either
# direction. The wiki is written by people and this publishes over what they
# wrote, so the live document is read first and compared against the moment
# the mirror was pulled. The near miss is in the script's own header: four
# pages updated through the API, a mirror never refreshed, and a later edit
# built from the stale copy at the same time the maintainer was renaming a
# category - nothing was lost, and only because the stale text failed to match.
#
# The mirror above was pulled at 2026-09-01T00:00Z. Somebody else edits the
# page nine days later.
cp "$STUB/documents.info.json" "$WORK/info.good"
python3 - "$STUB" <<'PYEOF'
import json, sys
path = sys.argv[1] + "/documents.info.json"
info = json.load(open(path))
info["data"]["updatedAt"] = "2026-09-10T12:00Z"
json.dump(info, open(path, "w"))
PYEOF
rm -f "$STUB/sent.json"
if sh "$WORK/repo/tools/wiki-push.sh" "$DOCID" "$MIRROR" \
     > "$WORK/stale.log" 2>&1; then
  bad "a document edited after the pull was published over anyway"
else
  grep -q "changed on the wiki at 2026-09-10T12:00Z, after this mirror was pulled" \
    "$WORK/stale.log" \
    && ok "refused: $(grep -o "'.*' changed on the wiki.*" "$WORK/stale.log")" \
    || { bad "it failed, but not on the staleness check:"
         sed 's/^/    /' "$WORK/stale.log"; }
  # A refusal that says only "no" is one somebody works around by hand, and
  # the way round this one is to push the stale text again with the id.
  grep -q "Pull again and rebuild the edit on what is there now" \
    "$WORK/stale.log" \
    && ok "and it says what to do instead, which is not 'push harder'" \
    || bad "it refused without saying what to do next"
fi
[ -f "$STUB/sent.json" ] && bad "it refused and published the page anyway"
# Restored HERE and not at the end of the file: every case below reads this
# document, and a stub left nine days ahead would refuse all of them for a
# reason none of them is about.
cp "$WORK/info.good" "$STUB/documents.info.json"
# And the other direction, on the same site: the timestamps above were equal
# on every push before this case and each one went out, so the comparison is
# not simply refusing everything.

echo "=== a mirror that was never pulled stops the push ==="
# The worse half of the same question. With no wiki/.pulled there is no moment
# to compare the live document against, so "has this page moved since?" has no
# answer at all - and a push that guessed would overwrite somebody's work
# silently, which is the defect the comparison above exists to prevent.
mv "$WORK/repo/wiki/.pulled" "$WORK/pulled.saved"
rm -f "$STUB/sent.json"
if sh "$WORK/repo/tools/wiki-push.sh" "$DOCID" "$MIRROR" \
     > "$WORK/nostamp.log" 2>&1; then
  bad "a push from a mirror of unknown age was published"
else
  grep -q "wiki/ has never been pulled" "$WORK/nostamp.log" \
    && ok "refused: $(grep -o 'wiki/ has never been pulled.*' "$WORK/nostamp.log")" \
    || { bad "it failed, but not because the mirror carries no pull stamp:"
         sed 's/^/    /' "$WORK/nostamp.log"; }
fi
[ -f "$STUB/sent.json" ] && bad "it refused and published the page anyway"
mv "$WORK/pulled.saved" "$WORK/repo/wiki/.pulled"

echo "=== a live document that cannot be read stops the push ==="
# An expired, wrong or revoked token answers with an error object and no
# "data", and every comparison in the block below it depends on that document.
# Reading the absence as "nothing changed since the pull" would publish over
# whatever is really on the page, so this stops at the READ and not at the
# write.
cp "$STUB/documents.info.json" "$WORK/info.good"
printf '{"ok":false,"error":"authentication required"}' \
  > "$STUB/documents.info.json"
rm -f "$STUB/sent.json"
if sh "$WORK/repo/tools/wiki-push.sh" "$DOCID" "$MIRROR" \
     > "$WORK/badtoken.log" 2>&1; then
  bad "a push went out without the live document having been read at all"
else
  grep -q "could not read the live document; is the token valid?" \
    "$WORK/badtoken.log" \
    && ok "refused, and it names the likely cause" \
    || { bad "it failed without saying the live document could not be read:"
         sed 's/^/    /' "$WORK/badtoken.log"; }
fi
[ -f "$STUB/sent.json" ] && bad "it refused and published the page anyway"
cp "$WORK/info.good" "$STUB/documents.info.json"

echo "=== a wiki that refuses the update is not reported as published ==="
# The last refusal in the file and the one an operator would otherwise take
# for a success: the request went out, the wiki said no, and the exit status
# of a pipeline is its LAST command - so without this the script would end
# quietly and the correction would sit there with nobody looking for it again.
: > "$STUB/update-refuses"
rm -f "$STUB/sent.json"
if sh "$WORK/repo/tools/wiki-push.sh" "$DOCID" "$MIRROR" \
     > "$WORK/norefuse.log" 2>&1; then
  bad "the wiki refused the update and the push reported success"
else
  grep -q "the wiki refused the update" "$WORK/norefuse.log" \
    && ok "refused, and it repeats what the wiki said back" \
    || { bad "it failed without saying the wiki had refused:"
         sed 's/^/    /' "$WORK/norefuse.log"; }
fi
grep -q "published:" "$WORK/norefuse.log" \
  && bad "it printed 'published:' over an update the wiki refused"
rm -f "$STUB/update-refuses"

echo "=== no token, no file, no arguments: the three checks before any curl ==="
# Cheap, and each of them is the alternative to a request against the live
# wiki with a guess in it. The token one is not cosmetic either: with
# WIKI_TOKEN unset the Authorization header would go out reading "Bearer ",
# and what came back would be read as a document.
rm -f "$STUB/sent.json"
if (unset WIKI_TOKEN; sh "$WORK/repo/tools/wiki-push.sh" "$DOCID" "$MIRROR") \
     > "$WORK/notoken.log" 2>&1; then
  bad "wiki-push.sh ran with no token at all"
else
  grep -q "error: set WIKI_TOKEN" "$WORK/notoken.log" \
    && ok "no token: $(head -1 "$WORK/notoken.log")" \
    || { bad "it failed for some reason other than the missing token:"
         sed 's/^/    /' "$WORK/notoken.log"; }
fi
if sh "$WORK/repo/tools/wiki-push.sh" "$DOCID" "$WORK/no-such-page.md" \
     > "$WORK/nofile.log" 2>&1; then
  bad "wiki-push.sh accepted a file that is not there"
else
  grep -q "no-such-page.md does not exist" "$WORK/nofile.log" \
    && ok "no file: $(head -1 "$WORK/nofile.log")" \
    || { bad "it failed without naming the file it could not find:"
         sed 's/^/    /' "$WORK/nofile.log"; }
fi
if sh "$WORK/repo/tools/wiki-push.sh" > "$WORK/noargs.log" 2>&1; then
  bad "wiki-push.sh ran with no arguments"
else
  grep -q "^usage: " "$WORK/noargs.log" \
    && ok "no arguments: $(head -1 "$WORK/noargs.log")" \
    || { bad "it failed without printing a usage line:"
         sed 's/^/    /' "$WORK/noargs.log"; }
fi
# Three arguments and not two is the same site read the other way: an id, a
# file and something else is somebody passing a flag this script has not got.
if sh "$WORK/repo/tools/wiki-push.sh" "$DOCID" "$MIRROR" --force \
     > "$WORK/threeargs.log" 2>&1; then
  bad "wiki-push.sh ran with three arguments and published something"
else
  grep -q "^usage: " "$WORK/threeargs.log" \
    && ok "three arguments: the same usage, and no push" \
    || { bad "three arguments failed without printing a usage line:"
         sed 's/^/    /' "$WORK/threeargs.log"; }
fi
[ -f "$STUB/sent.json" ] && bad "one of those four sent a document"

echo "=== the pull needs a token, and refuses BEFORE it deletes anything ==="
# No credential is kept in this repository on purpose, so the refusal carries
# the sentence that says so - otherwise the next person goes looking for the
# file it is missing. What is asserted second is the half that costs work:
# this script rewrites wiki/ from the live wiki, so a refusal that arrived
# after the rmtree would take the mirror with it.
pullbefore=$(cat "$MIRROR")
if (unset WIKI_TOKEN; sh "$WORK/repo/tools/wiki-pull.sh") \
     > "$WORK/pullnotoken.log" 2>&1; then
  bad "wiki-pull.sh rewrote the mirror with no token at all"
else
  grep -q "set WIKI_TOKEN to an Outline API token" "$WORK/pullnotoken.log" \
    && ok "refused: $(head -1 "$WORK/pullnotoken.log")" \
    || { bad "it failed for some reason other than the missing token:"
         sed 's/^/    /' "$WORK/pullnotoken.log"; }
fi
[ -f "$MIRROR" ] && [ "$(cat "$MIRROR")" = "$pullbefore" ] \
  && ok "and the mirror is untouched" \
  || bad "the pull refused and rewrote wiki/ anyway"

echo "=== a wiki that answers with no documents does not empty the mirror ==="
# This guard was `[ -s "$TMP/docs.json" ]` and could never fire: an answer
# holding no published documents reads out as "[]", which is two bytes and
# passes a size test. Measured against this stub before the change - the
# rewrite ran, every page in the mirror was deleted, wiki/.ids came back
# empty and wiki/.pulled was written EMPTY, and the script exited 0 saying
# "0 pages". A mirror stamped as freshly pulled and holding nothing is the
# state the whole first half of that script exists to prevent.
cp "$STUB/documents.list.json" "$WORK/list.good"
printf '{"data":[],"pagination":{"offset":0,"limit":100,"total":0}}' \
  > "$STUB/documents.list.json"
emptybefore=$(cat "$MIRROR")
if sh "$WORK/repo/tools/wiki-pull.sh" > "$WORK/empty.log" 2>&1; then
  bad "an answer with no documents in it rewrote the mirror"
else
  grep -q "no published documents at all" "$WORK/empty.log" \
    && ok "refused: $(grep -o 'error: the wiki answered.*' "$WORK/empty.log")" \
    || { bad "it failed, but not on the empty answer:"
         sed 's/^/    /' "$WORK/empty.log"; }
fi
[ -f "$MIRROR" ] && [ "$(cat "$MIRROR")" = "$emptybefore" ] \
  && ok "and every page in the mirror is still there" \
  || bad "the pull refused and emptied wiki/ anyway"
cp "$WORK/list.good" "$STUB/documents.list.json"

echo "=== a mirror it cannot ask about is a warning, not a silent pull ==="
# tools/wiki-status.sh exits 2 when it cannot answer - not a git checkout, or
# a checkout whose history never recorded a pull - and this copy of the
# repository is exactly the first of those. The question "is there unpublished
# work here?" then has no answer, and answering it wrongly either way is worse
# than saying so: a silent pull deletes corrections nobody can see, and a
# refusal stops a pull in a tree that never had any. So it pulls, and it says.
#
# LAST of the pull cases on purpose. This one really does rewrite
# "$WORK/repo/wiki", so anything above it that compares the mirror against
# itself has already run.
sh "$WORK/repo/tools/wiki-pull.sh" > "$WORK/unknown.log" 2>&1 \
  && ok "the pull ran in a tree whose history cannot be read" \
  || { bad "the pull failed in a tree with no history:"
       sed 's/^/    /' "$WORK/unknown.log"; }
grep -q "could not tell whether wiki/ holds unpublished edits" \
  "$WORK/unknown.log" \
  && ok "and it said it could not tell, rather than pulling in silence" \
  || { bad "it pulled without warning that it had not checked for drafts:"
       sed 's/^/    /' "$WORK/unknown.log"; }
# The holder's own words are kept. "not a git repository" and "no commit ever
# wrote wiki/.pulled" are different things to go and do, and a warning that
# flattened both into one sentence would send the reader to the wrong one.
grep -q "not a git repository" "$WORK/unknown.log" \
  && ok "and it repeats the holder's reason rather than inventing one" \
  || bad "the warning does not carry why the holder could not answer"

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

echo "=== the holder's two ways of having no answer, and a page removed ==="
# tools/wiki-status.sh is the only thing that says whether work is waiting
# here, and tools/wiki-pull.sh reads it before deleting wiki/. Both of the
# states below exit 2 rather than 0 or 1, and the difference is not academic:
# a "clean" it invented would have the pull destroy corrections, and a
# "drafts" it invented would stop a pull in a tree that never had any.
#
# A FIXTURE OF ITS OWN, and that is not tidiness. The repository above has had
# wiki/.pulled rewritten by the discard pull, so the last commit that wrote
# that file is now the last commit outright and every comparison against it is
# empty - which reports clean, for a reason that has nothing to do with what
# is being asserted.
SREPO="$WORK/statusrepo"
mkdir -p "$SREPO/tools" "$SREPO/wiki"
cp "$REPO/tools/wiki-status.sh" "$SREPO/tools/"
cp "$REPO/wiki/README.md" "$SREPO/wiki/"
printf '# %s\n\n%s' "$TITLE" "$BODY" > "$SREPO/wiki/primeiros-passos.md"
printf '# Outra pagina\n\ncorpo\n' > "$SREPO/wiki/outra-pagina.md"

sh "$SREPO/tools/wiki-status.sh" > "$WORK/nogit.log" 2>&1; rc=$?
[ "$rc" = 2 ] \
  && ok "a directory that is not a checkout exits 2, not 0 and not 1" \
  || bad "wiki-status.sh exited $rc over a directory with no git history"
grep -q "not a git repository, so there is no history to read" \
  "$WORK/nogit.log" \
  && ok "and it says the history is what it reads" \
  || { bad "it refused without saying what it could not read:"
       sed 's/^/    /' "$WORK/nogit.log"; }

# A checkout whose history has no pull in it. The distinction matters because
# the thing this compares against is not a file, it is the last COMMIT that
# wrote wiki/.pulled, and a tree can have the file without any commit having
# written it - a fresh clone of the mirror with the stamp still unstaged.
git -C "$SREPO" init -q
git -C "$SREPO" -c user.email=t@t -c user.name=t add -A
git -C "$SREPO" -c user.email=t@t -c user.name=t -c commit.gpgsign=false \
  commit -q -m "a mirror nobody recorded a pull for"
sh "$SREPO/tools/wiki-status.sh" > "$WORK/nopull.log" 2>&1; rc=$?
[ "$rc" = 2 ] \
  && ok "a history with no pull in it exits 2 as well" \
  || bad "wiki-status.sh exited $rc over a history that never pulled"
grep -q "no last pull to compare against" "$WORK/nopull.log" \
  && ok "and it says there is no moment to compare against" \
  || { bad "it refused without saying why it could not compare:"
       sed 's/^/    /' "$WORK/nopull.log"; }

# A page DELETED here and still published. Three groups and three different
# jobs: a correction is pushed, a page that exists only here has to be created
# on the wiki before it can be, and a page removed here is still on the wiki
# and wiki-push.sh cannot take it down. Reported as one number, whoever holds
# the token does the wrong thing twice.
printf '2026-09-01T00:00Z\n' > "$SREPO/wiki/.pulled"
git -C "$SREPO" -c user.email=t@t -c user.name=t add -A
git -C "$SREPO" -c user.email=t@t -c user.name=t -c commit.gpgsign=false \
  commit -q -m "the mirror as pulled"
rm -f "$SREPO/wiki/outra-pagina.md"
git -C "$SREPO" -c user.email=t@t -c user.name=t add -A
git -C "$SREPO" -c user.email=t@t -c user.name=t -c commit.gpgsign=false \
  commit -q -m "a page removed here and nobody told the wiki"
sh "$SREPO/tools/wiki-status.sh" > "$WORK/gone.log" 2>&1; rc=$?
[ "$rc" = 1 ] \
  && ok "a removal is an outstanding difference, so exit 1" \
  || { bad "wiki-status.sh exited $rc over a page deleted since the pull:"
       sed 's/^/    /' "$WORK/gone.log"; }
if grep -q "deleted here and still published" "$WORK/gone.log" &&
   grep -q "outra-pagina.md" "$WORK/gone.log"; then
  ok "$(grep 'deleted here and still published' "$WORK/gone.log")"
else
  bad "it reported the removal without a group and a name of its own:"
  sed 's/^/    /' "$WORK/gone.log"
fi
# And not into the corrected group, which is the mistake the split exists to
# stop: that group's line is what an operator reads as "push these".
grep -q "^0 page(s) corrected here" "$WORK/gone.log" \
  && ok "and it is not counted as a page to push" \
  || { bad "the removal was counted among the pages to push:"
       sed 's/^/    /' "$WORK/gone.log"; }

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
  GHBIN=$WORK/ghbin
  mkdir -p "$GHBIN"
  # `auth status` succeeds so the gate takes its asking path, and the issue
  # list answers from a file so both readers see one tracker. The states the
  # tool cares about are open and closed; the numbers are the ones the fixture
  # roadmap names.
  cat > "$GHBIN/gh" <<'GHSTUB'
#!/bin/sh
case "$*" in
  *"auth status"*) exit 0 ;;
  *"issue list"*)  cat "$GH_ISSUES" 2>/dev/null ;;
  *)               exit 0 ;;
esac
GHSTUB
  chmod +x "$GHBIN/gh"
  : > "$WORK/issues.json"
  RELEASE_WORK="$WORK/gate" RELEASE_GATE_CHECK_ONLY=1 \
    PATH="$GHBIN:$PATH" GH_ISSUES="$WORK/issues.json" \
    sh "$REPO/tools/release-gate.sh" "$GATE_TAG" > "$WORK/gate.log" 2>&1 || true
  # The gate has checks before this one and any of them can refuse first. What
  # proves it reached the wiki step is one of the wiki step's own two lines,
  # and a run that shows neither proves nothing either way - so it is a
  # failure here rather than a silent pass.
  # THE HOLDER, ASKED ABOUT THE TREE THE GATE IS LOOKING AT, which is the
  # gate's own clone at $GATE_TAG and not this working tree. Those are two
  # different trees and they agreed only for as long as nothing had been
  # published since the newest tag. The day the wiki was published (#277) the
  # working tree went clean, the tag stayed behind, and this check reported
  # "the gate is deciding this for itself" about a gate that was reading the
  # holder correctly - which is the failure the paragraph above set out to
  # avoid and got wrong by one tree.
  #
  # One question, one tree. If the clone is not there the gate never got far
  # enough to have an opinion, and that is the `unreached` case below.
  if [ -x "$WORK/gate/tools/wiki-status.sh" ] || [ -f "$WORK/gate/tools/wiki-status.sh" ]; then
    if sh "$WORK/gate/tools/wiki-status.sh" >/dev/null 2>&1; then
      HOLDER=clean
    else
      HOLDER=drafts
    fi
  else
    HOLDER=unreached
  fi
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
  # AND IT GOT PAST THE WIKI STEP TO THE NEXT DOCUMENT CHECK. The roadmap is
  # compared against the tracker right after this one, and while the wiki
  # refusal exited on the spot that comparison could never run: the wiki has
  # been behind since 2026-09-09, so a check placed behind it would have been
  # unexecuted for its whole life. Both verdicts are taken before either
  # refusal now, and this is what keeps it that way.
  #
  # Three lines are acceptable because three outcomes are: the roadmap agrees,
  # it does not, or gh cannot answer - which is a legitimate state for a gate
  # run offline and must not read as a pass.
  if grep -qE "the roadmap and the tracker agree|roadmap.md and the tracker disagree|roadmap was NOT checked" "$WORK/gate.log"; then
    ok "and it reaches the roadmap check rather than stopping at the wiki"
  else
    bad "the gate never reached the roadmap check for $GATE_TAG, so a \
document comparison sits behind one that has been red since September and \
has never run"
  fi
  # AND IT ASKED. "reaches the check" and "prints one of its three lines" are
  # not the same thing: a gate that stopped calling the holder would still
  # print "the roadmap and the tracker agree", and that reads as a pass. So
  # the holder is run too and the two are required to match.
  #
  # Run IN THE CLONE the gate made, not in this working tree. roadmap-state
  # reads the repository it sits in, and the gate checks the TAG - whose
  # roadmap is a different file from the one being edited here. Comparing the
  # gate's answer about the tag with this tree's answer about itself would be
  # comparing two different questions and calling the difference a defect.
  if grep -q "roadmap was NOT checked" "$WORK/gate.log"; then
    ok "and where gh cannot answer it says so, rather than reading as a pass"
  elif [ ! -d "$WORK/gate/tools" ]; then
    bad "the gate left no clone to ask, so nothing here can tell 'asked and \
agreed' from 'never asked'"
  else
    if PATH="$GHBIN:$PATH" GH_ISSUES="$WORK/issues.json" \
       sh "$WORK/gate/tools/roadmap-state.sh" >/dev/null 2>&1; then
      RM_HOLDER=agree
    else
      RM_HOLDER=disagree
    fi
    if grep -q "the roadmap and the tracker agree" "$WORK/gate.log"; then
      RM_GATE=agree
    else
      RM_GATE=disagree
    fi
    [ "$RM_HOLDER" = "$RM_GATE" ] \
      && ok "and the gate and tools/roadmap-state.sh agree: $RM_GATE" \
      || bad "the holder says '$RM_HOLDER' about the tagged tree and the gate \
says '$RM_GATE', so the gate is not asking it"
  fi

  # And it repeats what the holder said rather than listing pages of its own.
  # A second list is a second thing to keep true, and this one would be read
  # by somebody about to publish.
  if [ "$HOLDER" = drafts ] && [ "$GATE" = drafts ]; then
    # FROM THE CLONE, for the reason the holder above is read from it: the
    # gate's refusal lists the pages of the tree it cloned, and this working
    # tree is a different one the moment anything is published.
    FIRSTPAGE=$(sh "$WORK/gate/tools/wiki-status.sh" 2>&1 |
                sed -n 's/^  \(wiki\/.*\.md\)$/\1/p' | head -1)
    if [ -n "$FIRSTPAGE" ] && grep -q "$FIRSTPAGE" "$WORK/gate.log"; then
      ok "and the refusal names the pages the holder named"
    else
      bad "the gate refused without naming what the holder named \
($FIRSTPAGE), so an operator with the token is told to publish and not what"
    fi
  fi
fi

echo
echo "=== publishing the whole backlog is one command, and it stops on a refusal ==="
# tools/wiki-push.sh takes one file, and that is the right granularity: each
# push checks that one document and refuses if somebody edited it since the
# pull. What it is not is a thing anybody does twenty-seven times by hand.
# #277 is the measurement of exactly that - the mirror stood 28 pages ahead
# from 2026-09-09, one page telling an operator something that had stopped
# being true, and the reason on the issue is "nobody did".
#
# Its own fixture, because the cases above leave $SREPO with a deleted page in
# it and this is about the pages that can be pushed.
PREPO="$WORK/publish"
mkdir -p "$PREPO/tools" "$PREPO/wiki"
cp "$REPO/tools/wiki-status.sh" "$REPO/tools/wiki-push.sh" \
   "$REPO/tools/wiki-publish.sh" "$PREPO/tools/"
cp "$REPO/wiki/README.md" "$PREPO/wiki/"
# Both open with the stub document's own title, because wiki-push.sh refuses
# a file whose heading is not the title of the document it is being sent to -
# a heading in the body becomes a second title on the page (#165). The stub
# answers documents.info with one document, so both pages carry its title.
printf '# %s\n\ncorpo um\n' "$TITLE" > "$PREPO/wiki/uma.md"
printf '# %s\n\ncorpo dois\n' "$TITLE" > "$PREPO/wiki/outra.md"
printf '%s\n' "uma.md $DOCID" "outra.md $DOCID" > "$PREPO/wiki/.ids"
printf '2026-09-01T00:00Z\n' > "$PREPO/wiki/.pulled"
git -C "$PREPO" init -q
git -C "$PREPO" -c user.email=t@t -c user.name=t add -A
git -C "$PREPO" -c user.email=t@t -c user.name=t -c commit.gpgsign=false \
  commit -q -m "the mirror as pulled"
printf '# %s\n\ncorpo um corrigido\n' "$TITLE" > "$PREPO/wiki/uma.md"
printf '# %s\n\ncorpo dois corrigido\n' "$TITLE" > "$PREPO/wiki/outra.md"
# AND ONE PAGE THAT EXISTS ONLY HERE. wiki-push.sh updates a document and
# cannot create one, so this must not reach it: a run that fed it the new page
# would stop on a refusal that is nobody editing anything. Without this file
# in the fixture the list is the same whether --updatable filters by git's
# status letter or not, and the case below could not fail.
printf '# %s\n\nnunca publicada\n' "$TITLE" > "$PREPO/wiki/nova.md"
git -C "$PREPO" -c user.email=t@t -c user.name=t add -A
git -C "$PREPO" -c user.email=t@t -c user.name=t -c commit.gpgsign=false \
  commit -q -m "two corrections nobody published, and a page the wiki has not got"

# The list is the holder's, not a second reading of the prose.
HELD=$(sh "$PREPO/tools/wiki-status.sh" --updatable | sort | tr '\n' ' ')
[ "$HELD" = "wiki/outra.md wiki/uma.md " ] \
  && ok "wiki-status.sh --updatable names the two pages that can be pushed" \
  || bad "--updatable named '$HELD', which is not the two edited pages: a page \
the wiki has not got cannot be updated, and feeding it to wiki-push.sh stops \
the run on a refusal that is nobody editing anything"

rm -f "$STUB/sent.json"
( cd "$PREPO" && unset WIKI_TOKEN; sh tools/wiki-publish.sh -n ) \
  > "$WORK/dry.log" 2>&1; rc=$?
[ "$rc" = 0 ] && grep -q "2 page(s) would be published" "$WORK/dry.log" \
   && ! grep -q "nova.md" "$WORK/dry.log" \
  && ok "-n says how many and needs no token" \
  || { bad "the dry run exited $rc without saying what it would publish:"
       sed 's/^/    /' "$WORK/dry.log"; }
# WITH the token, which is the run that can actually do damage: the operator
# who has it exported and types -n first to see what would go out. Without the
# token a -n that forgot to stop would be caught by wiki-push.sh refusing, and
# this case would be passing on somebody else's guard.
rm -f "$STUB/sent.json"
( cd "$PREPO" && sh tools/wiki-publish.sh -n ) > "$WORK/dry2.log" 2>&1
[ ! -f "$STUB/sent.json" ] \
  && ok "and with a token in the environment it still sends nothing" \
  || bad "the dry run reached documents.update: a page went out on -n"

( cd "$PREPO" && unset WIKI_TOKEN; sh tools/wiki-publish.sh ) \
  > "$WORK/notok.log" 2>&1; rc=$?
[ "$rc" = 2 ] && grep -q "WIKI_TOKEN is not set" "$WORK/notok.log" \
  && ok "with no token it refuses, rather than publishing nothing and exiting 0" \
  || { bad "a run with no token exited $rc:"; sed 's/^/    /' "$WORK/notok.log"; }

( cd "$PREPO" && sh tools/wiki-publish.sh ) > "$WORK/pub.log" 2>&1; rc=$?
[ "$rc" = 0 ] && grep -q "2 page(s) published" "$WORK/pub.log" \
  && ok "$(grep -m1 'page(s) published' "$WORK/pub.log")" \
  || { bad "publishing two pages exited $rc:"; sed 's/^/    /' "$WORK/pub.log"; }
[ -f "$STUB/sent.json" ] \
  && ok "and the wiki was actually asked to take one" \
  || bad "nothing reached documents.update, so the run published nothing"

# THE ONE THAT MATTERS. A refusal means somebody is editing the wiki right
# now, and carrying on would publish the rest of the list over the top of
# whatever they are doing.
: > "$STUB/update-refuses"
( cd "$PREPO" && sh tools/wiki-publish.sh ) > "$WORK/stop.log" 2>&1; rc=$?
rm -f "$STUB/update-refuses"
[ "$rc" != 0 ] \
  && ok "a refused page takes the whole run down with it" \
  || bad "the run exited 0 with a page refused (rc=$rc)"
grep -q "with 0 of 2 published" "$WORK/stop.log" \
  && ok "and it says where it stopped and how many had gone out" \
  || { bad "it stopped without saying how far it got:"
       sed 's/^/    /' "$WORK/stop.log"; }
grep -q "rest of the list is NOT published" "$WORK/stop.log" \
  && ok "and says why the rest was left alone" \
  || bad "nothing explained why it did not carry on"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
