#!/bin/sh
# Compare docs/roadmap.md against the issue tracker.
#
#   sh tools/roadmap-state.sh                 the roadmap in this tree
#   sh tools/roadmap-state.sh path/to/file.md another copy of it
#
# A TOOL, and deliberately not a `make test` suite. Every suite in this
# repository has to pass with no network - that is why test/test_doc_copies.py
# compares a truth against the other place it is written down and never against
# the tracker, and says so in its own header. This comparison cannot be made
# that way: whether an issue is open is written down nowhere in the tree. Run
# it when a release is cut, which is when this file is read.
#
# It is NOT wired into tools/release-gate.sh, and that is a decision rather
# than an omission. Whether a stale roadmap should refuse a release is the
# maintainer's call: the gate is the only thing in this project that has ever
# caught a release mistake, and a check that needs GitHub to be reachable
# changes what a failed gate means.
#
# What it was written for, measured on 2026-09-14 (#292): the file named 116
# issues, 99 of them closed, and never mentioned seven that were open -
# including a published wiki page that is wrong and boxes sharing one SSH host
# key. The document that says what remains was wrong in both directions.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DOC=${1:-$ROOT/docs/roadmap.md}
[ -f "$DOC" ] || { echo "error: no such file: $DOC" >&2; exit 2; }

# Every refusal below exits 2 and says the comparison did not happen. 1 means
# findings. Nothing that could not reach the tracker may exit 0: a roadmap
# nobody compared reads exactly like a roadmap that agreed.
command -v gh >/dev/null 2>&1 || {
  echo "error: gh is not installed, so NOTHING WAS COMPARED." >&2
  echo "       This tool reads the tracker; there is no offline answer it" >&2
  echo "       could give instead." >&2
  exit 2
}
gh auth status >/dev/null 2>&1 || {
  echo "error: gh is not authenticated, so NOTHING WAS COMPARED." >&2
  echo "       Run 'gh auth login'. The roadmap was read and the tracker was" >&2
  echo "       never asked; this is not a clean run." >&2
  exit 2
}

STATES=$(mktemp) || exit 2
ERRS=$(mktemp) || exit 2
trap 'rm -f "$STATES" "$ERRS"' EXIT INT TERM

# ONE query for the whole tracker, not one per reference.
#
# `gh issue view` per issue is 116 round trips and several minutes, which is
# long enough that whoever runs this at release time runs it once and stops
# running it. It is also the only way to see the other direction: an issue that
# is open and that this file never mentions cannot be found by asking about the
# numbers the file already has.
#
# --limit is far above the tracker's size on purpose, because gh truncates at
# the limit in silence and a truncated answer reports every issue past the cut
# as absent - a wrong answer that looks like a finding.
if ! gh issue list --state all --limit 5000 \
       --json number,state --jq '.[] | "\(.number) \(.state)"' \
       > "$STATES" 2>"$ERRS"; then
  # gh's own text is kept: "could not resolve host" and "HTTP 403 rate limit"
  # need different work and both look like an empty answer from here.
  echo "error: the tracker could not be reached, so NOTHING WAS COMPARED." >&2
  sed 's/^/       gh: /' "$ERRS" >&2
  exit 2
fi
# An empty answer is not "no issues". It is a query that went wrong in a way gh
# reported as success, and reading it as an empty tracker would call every
# reference in the file unknown and leave every check below silent.
[ -s "$STATES" ] || {
  echo "error: the tracker answered with no issues at all, which cannot be" >&2
  echo "       right for this repository. NOTHING WAS COMPARED." >&2
  exit 2
}

echo "==> $DOC"
echo "    tracker: $(wc -l < "$STATES" | tr -d ' ') issue(s)"
echo

# THE HARD PART IS THE THIRD AND FOURTH CHECKS. A closed issue named in this
# file is usually correct: a section that RECORDS how something was decided
# keeps naming the issue it was decided on, and deleting that would be deleting
# the history. What is wrong is a sentence saying work REMAINS under an issue
# that is closed. The rules are narrow on purpose and the ones measured and
# rejected are named beside them, because a check that cries wolf is a check
# somebody switches off - which is the argument tools/release-gate.sh makes
# twice about its own widenings.
awk '
# The number of every issue a string refers to, once each.
#
# BOTH SPELLINGS, because this file uses both: [#55](.../issues/55) and a bare
# #55 in prose. Counting only the links misses one reference outright - #225,
# named bare in the blocked table - and the measurement on #292, taken over the
# links alone, said 115 where this says 116.
#
# The URL half of a markdown link is dropped FIRST so [#55](.../issues/55) is
# one reference and not two. Dropping it also makes every position below a
# position in what a reader sees, which is what the distance rules compare.
function nums(s,   out, t, u) {
  gsub(/\]\(https?:\/\/[^)]*\)/, "]", s)
  out = " "; t = s
  while (match(t, /#[0-9]+/)) {
    u = substr(t, RSTART + 1, RLENGTH - 1) + 0
    t = substr(t, RSTART + RLENGTH)
    if (index(out, " " u " ") == 0) out = out u " "
  }
  return out
}
function flat(s) { gsub(/\]\(https?:\/\/[^)]*\)/, "]", s); return s }
# One reading per issue per place. The checks overlap on purpose - a row that
# says **open** about a closed issue usually says it twice - and a reader who
# has to skip the second copy is a reader learning to skip.
function say(kind, n, ln, txt) {
  if ((n " @" ln) in said) return
  said[n " @" ln] = 1
  printf "    #%-4d %-6s line %-5d %s\n", n, kind, ln, txt
  findings++
}

NR == FNR { state[$1 + 0] = $2; if ($2 == "OPEN") isopen[$1 + 0] = 1; next }
{ L[FNR] = $0; last = FNR }

END {
  # Needles that claim work has NOT happened.
  #
  # MEASURED AND REJECTED, so nobody widens this again on a hunch. Over the
  # file as it stood on 2026-09-14, with the attribution below:
  #
  #   "needs"           11 lines, ordinary English in almost all of them
  #                     ("a leak is visible at the packet rate", "it needed
  #                     two pieces rather than one")
  #   "blocked"          3 lines, every one of them false: the word appears in
  #                     "unblocked", "blocked upstream" and "a provider
  #                     blocked on SIP"
  #   bare "missing"     3 lines, two of them describing what a defect WAS
  #   "stays open"       2 lines, zero true: one is about a milestone that
  #                     stays open for months, the other is #28, which is open
  #
  # The kept set below produced thirteen readings on that file and no false
  # positive that survived review.
  NEEDLE = "still open|remains open|what remains|remains |what is left|" \
           "does not do|depends on|is missing|still needs|not yet|why it waits"

  # ---- every reference, and the first line it appears on
  for (i = 1; i <= last; i++) {
    n = split(nums(L[i]), a, " ")
    for (j = 1; j <= n; j++)
      if (a[j] != "" && !(a[j] + 0 in first)) { first[a[j] + 0] = i; refs[++nrefs] = a[j] + 0 }
  }

  # ---- BLOCKS, and this is the part that matters most.
  #
  # docs/roadmap.md is hard-wrapped, so a sentence is not a line: "#197 is
  # ahead of anything else in this section that is still open, and half of" /
  # "it is done." is one claim across two of them. A needle matched against raw
  # lines only works while the prose happens to fold somewhere else, and
  # reflowing a paragraph would silently change what this tool sees. That trap
  # has cost this repository four separate defects, and test_doc_copies.py
  # names three of them in its own header.
  #
  # So a paragraph is joined back into one string before anything is matched,
  # and every heading and every table row is a block of its own because each is
  # a claim in itself. Fenced blocks are skipped: the mermaid diagrams name
  # issues in boxes, which is a picture of the order and not a sentence about
  # what remains.
  nb = 0; i = 1; queue = 0; prevrow = ""
  while (i <= last) {
    l = L[i]
    if (l ~ /^```/) { i++; while (i <= last && L[i] !~ /^```/) i++; i++; continue }
    # A blank line ends the table but NOT the section, so `queue` survives it
    # and is cleared at the next heading. It was cleared here first, and the
    # blocked-table rule was silent for it: the heading sets the flag, a blank
    # line follows before the table, and every row read as an ordinary one. A
    # closed issue pasted into that table produced nothing at all.
    if (l ~ /^[ \t]*$/) { i++; prevrow = ""; continue }
    if (l ~ /^#+ /) {
      prevrow = ""
      # The blocked table has no header row of its own - it opens "| | |" - so
      # it is found by the heading test_doc_copies.py already keys on by name.
      blocked = (l ~ /[Ww]hat is blocked on/) ? 1 : 0; queue = blocked
      nb++; bt[nb] = "head"; bl[nb] = i; bq[nb] = queue; b[nb] = l; i++; continue
    }
    if (l ~ /^\|/) {
      if (l ~ /^\|[ :|-]*\|[ :|-]*$/) {
        # A |---|---| means the row above it was a header. A table that names
        # a reason the work has NOT happened is a queue: "Why it waits",
        # "Why it goes first". A table headed "What" is a list of work, and
        # the same rule over one of those reports every shipped item in it.
        queue = (prevrow ~ /[Ww]hy it/) || blocked
        if (nb > 0 && bl[nb] == i - 1) nb--     # the header row is not a claim
        i++; continue
      }
      prevrow = l
      nb++; bt[nb] = "row"; bl[nb] = i; bq[nb] = queue; b[nb] = l; i++; continue
    }
    s = i; t = l; i++
    while (i <= last && L[i] !~ /^[ \t]*$/ && L[i] !~ /^\|/ && \
           L[i] !~ /^```/ && L[i] !~ /^#+ /) { t = t " " L[i]; i++ }
    nb++; bt[nb] = "prose"; bl[nb] = s; bq[nb] = 0; b[nb] = t
  }

  if (!queue_seen_blocked()) {
    print "error: this file no longer has a \"What is blocked on somebody"
    print "       else\" section, so one of the checks below is reading"
    print "       nothing at all."
    broken = 1
  }

  print "==> every issue the roadmap names, with its state"
  for (k = 1; k <= nrefs; k++) {
    n = refs[k]
    st = (n in state) ? tolower(state[n]) : "NOT IN THE TRACKER"
    if (st == "open") nopen++; else if (st == "closed") nclosed++; else nmissing++
    printf "    #%-4d %-6s first named on line %d\n", n, st, first[n]
  }
  printf "\n    %d named, %d open, %d closed", nrefs, nopen, nclosed
  if (nmissing) {
    # A number that is not an issue here is a finding of its own, and a quiet
    # one: it is usually a pull request number or a typo, and a reader who
    # follows it lands on something that is not the work the sentence claims.
    printf ", %d NOT IN THE TRACKER", nmissing
    findings += nmissing
  }
  printf "\n\n"

  print "==> closed issues the file still presents as work that remains"
  # Counted from here rather than from zero: a dangling reference above has
  # already moved `findings`, and reading that as a finding of THIS check
  # would swallow the "none" line and leave the section looking empty.
  before_this = findings
  for (k = 1; k <= nb; k++) {
    # Matched against a lower-cased copy, which tolower() keeps the same
    # length as, so every position below is still a position in txt. The file
    # opens a sentence with "Still open here: #67", and a case-sensitive
    # needle missed it - the check would have been green on the one line that
    # says "still open" in so many words.
    txt = flat(b[k]); low = tolower(txt); subj = 0; mark = 0
    if (bt[k] == "row") {
      split(b[k], c, "|")
      n = split(nums(c[2]), a, " "); cnt = 0
      for (j = 1; j <= n; j++) if (a[j] != "") { cnt++; subj = a[j] + 0 }
      # Exactly one, and in the FIRST cell, which is what the row is about. A row
      # naming three issues in one cell says nothing about which of them the
      # rest of the row is about, and attributing to all of them reported
      # seven issues off one v0.5.0 row where one was meant.
      if (cnt != 1) subj = 0
      # An emphasised delivery marker, the spelling test_doc_copies.py settled
      # on after a character window and a nearest-link-in-the-paragraph rule
      # were both measured wrong. Plain "delivered" and plain "shipped" are
      # ordinary English in this file.
      mark = (b[k] ~ /\*\*?([Dd]elivered|shipped|fixed|withdrawn|done)[^*]*\*/)

      # CHECK: a row that states the issue is open, about an issue that is not.
      # The mirror image of the marker rule, and the cheapest true reading in
      # here: the row has a state column and the state column is wrong. Not on
      # a row that carries a delivery marker, because "every block it held
      # stays **open** in the retention log" is a sentence about blocks, under
      # *fixed* - the one false positive this rule produced.
      if (subj && !mark && state[subj] == "CLOSED" && b[k] ~ /\*\*?(open|partly)\*?\*/)
        say("STATE", subj, bl[k], "the row says **open**: " substr(txt, 1, 90))

      # CHECK: a row of a queue table, with no delivery marker on it.
      # The header of that table says the row is work that has not happened.
      else if (bq[k] && subj && !mark && state[subj] == "CLOSED")
        say("QUEUE", subj, bl[k], "queued as pending: " substr(txt, 1, 90))
    }

    # CHECK: a sentence that says work remains, about a closed issue.
    t = low; off = 0
    while (match(t, NEEDLE)) {
      p = off + RSTART; w = substr(t, RSTART, RLENGTH)
      off = off + RSTART + RLENGTH - 1; t = substr(t, RSTART + RLENGTH)

      # The CLAUSE around the needle, not the block and not the line. A block
      # is a whole paragraph, and "Still open here: #67 ... ; and #32 PCP last
      # - now unblocked" is two claims in one of them: block scope attributed
      # the "still open" belonging to #67 to a sentence about #32, and then
      # suppressed it on that sentence. Only
      # `. `, `; ` and `: ` split, so a version number does not.
      cs = 1; ce = length(txt)
      for (q = p - 1; q > 1; q--)
        if (substr(low, q - 1, 2) ~ /[.;:] /) { cs = q + 1; break }
      for (q = p; q < length(low); q++)
        if (substr(low, q, 2) ~ /[.;:] /) { ce = q; break }
      clause = substr(txt, cs, ce - cs + 1)
      clow = substr(low, cs, ce - cs + 1)
      rel = p - cs + 1

      # A row that records a fix describes what was WRONG, in the present
      # tense, and that is not a claim that it still is: "either it is
      # unnecessary or a call is missing" under *fixed*.
      if (mark) continue
      # Past tense is the whole question, so the past auxiliary governing the
      # needle is what separates the two: "#182 WAS the one still open in that
      # family, and it is closed" is the record this file is supposed to keep.
      before = substr(clow, (rel - 45 < 1 ? 1 : rel - 45), (rel - 45 < 1 ? rel - 1 : 45))
      if (before ~ /(^| )(was|were|used to|had been|has been)( |$)/) continue
      # And a clause saying the dependency is no longer one is not a claim
      # that it holds: "#32 PCP last - now unblocked, since it depends on #17".
      if (clow ~ /unblocked/) continue

      # Nearest reference IN THE CLAUSE, falling back to the block. Nearest in
      # the block alone was measured wrong on "What it does not do is
      # everything that happens after the disk is written", whose clause names
      # nobody and whose paragraph is about #83.
      tgt = near(clause, rel); if (!tgt) tgt = near(txt, p)
      if (bt[k] == "row" && subj) tgt = subj
      if (!tgt || state[tgt] != "CLOSED") continue
      if ((tgt " @" k) in seen) continue
      seen[tgt " @" k] = 1
      say("PROSE", tgt, bl[k], "\"" w "\": " substr(clause, 1, 100))
    }
  }
  if (findings == before_this) print "    none"
  print ""

  print "==> open issues the roadmap never mentions"
  nn = 0
  for (n in isopen) if (!(n + 0 in first)) { miss[++nn] = n + 0 }
  # Sorted, because an unordered answer reads as a different answer on every
  # run and nobody can diff two of them.
  for (x = 1; x <= nn; x++) for (y = x + 1; y <= nn; y++)
    if (miss[y] < miss[x]) { tmpv = miss[x]; miss[x] = miss[y]; miss[y] = tmpv }
  for (x = 1; x <= nn; x++) { printf "    #%d\n", miss[x]; findings++ }
  if (!nn) print "    none"
  print ""

  if (broken) exit 2
  if (findings) {
    printf "%d finding(s). The roadmap is what somebody reads to decide what to\n", findings
    print "do next, so each one is either a sentence to move into the past tense"
    print "or an issue that needs a home in the file."
    exit 1
  }
  print "the roadmap and the tracker agree"
  exit 0
}

# The nearest reference to position p in s, or 0.
function near(s, p,   best, tgt, t, o, q, v, d) {
  best = 1000000; tgt = 0; t = s; o = 0
  while (match(t, /#[0-9]+/)) {
    q = o + RSTART; v = substr(t, RSTART + 1, RLENGTH - 1) + 0
    o = o + RSTART + RLENGTH - 1; t = substr(t, RSTART + RLENGTH)
    d = (q > p) ? q - p : p - q
    if (d < best) { best = d; tgt = v }
  }
  return tgt
}
# A check that has quietly stopped checking is worse than the defect it looks
# for, so the section the queue rule depends on has to be there.
function queue_seen_blocked(   k) {
  for (k = 1; k <= nb; k++) if (bt[k] == "head" && b[k] ~ /[Ww]hat is blocked on/) return 1
  return 0
}
' "$STATES" "$DOC"
