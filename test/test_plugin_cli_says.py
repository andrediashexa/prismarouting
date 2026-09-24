#!/usr/bin/env python3
"""
Every message the plugin CLI can print, against what in this repository names
it - and, when a kept lab corpus is given, against what any lab made it print.

WHAT IS READ. `src/plugins/bpa/bpa_cli.c` is what `show bpa ...` prints, which
is what an operator types when a customer calls. Its `vlib_cli_output` call
sites are read out of the source by test/say-coverage.sh's C extraction - the
same rows tools/plugin-says.sh reads, so a message added to the plugin is a
row here on the next run and there is no list to keep in step. Each row's
NEEDLE is the longest literal run of its format string with no `%` conversion
and no escape in it, the rule test/test_selftest.sh states for its own
messages: values are cut out, not globbed, because hunting a value under its
own name matches nothing a box prints. A run shorter than FLOOR characters
once its indent is stripped is a value, not a message, and is counted as such
rather than passed.

THREE ANSWERS PER SITE, and they are not the same question:

  (a) PRINTED: a lab in a kept corpus made the box print it. The corpus is
      what `LAB_OUTPUT=<dir> sh tools/run-all-labs.sh` keeps, and
      tools/plugin-says.sh is the reader that answers this; it is asked here,
      not re-implemented, when PLUGIN_CLI_CORPUS names the directory. `make
      test` has no corpus - it runs with no root, no VPP and no network - so
      without one this answer is "not asked", never "no".
  (b) NAMED: a lab, a suite, a wiki page or a document carries the needle
      whole. That is the weaker measurement and it is the one this tree can
      always take. It is deliberately the WHOLE run and not any ten
      characters of it: measured on 2026-09-17, a rule that accepted any
      ten-character slice named 65 of 69 sites through slices like
      " subscribe" and " the pool ", which is the fourth lie TESTING.md
      describes, automated.
  (c) NEITHER: named by nothing, and - with a corpus - printed by nothing.

WHAT "NAMED" DOES NOT COUNT, each with its reason, because an exclusion
nobody re-reads is an allowlist:

  * test/say-coverage.sh, test/test_plugin_says.sh and this file. They are
    the readers. test_plugin_says.sh quotes needles out of the real plugin to
    pin the extraction; it drives none of the screens. Counting it would let
    a message be "named" by the instrument that measures it.
  * docs/roadmap.md and CHANGELOG.md. The roadmap quotes three of these
    sentences to say that nothing names them, and the changelog announces
    sentences as they land. Neither is a page an operator reads to learn
    what the box says, and a quotation made in order to report a gap must
    not close the gap.

THE CEILING, and it may only go down. The count of (c) is written down with
the date it was measured, and the run refuses a count above it - a message
added to the plugin with nothing naming it goes red on the next run, naming
the line - and refuses a count below it, because a ceiling that is no longer
tight holds nothing. That is the idiom test/test_prismash_refusals.py's
OVER_AT_MOST uses, and the only way to change the number is to make something
name a sentence. NOT by quoting it in a lab that does not drive it: a lab
names a sentence honestly only on a screen it captures and asserts on, which
is why the three labs that lowered this number on 2026-09-17 each assert the
sentence on a variable they already held, on screens the kept corpus of
2026-09-15 shows them printing.

With a corpus the count of (c) can only be lower than without one, so the
equality is asked of the tree alone and the corpus run is held to the ceiling
from above only: it is a measurement of what the labs did, printed beside the
tree's, not a second number to keep.

WHAT IT CANNOT SEE, said rather than discovered: a lab that asserts on a
fragment of a sentence ("no live state", "past or present") drives the screen
and does not name the sentence by this rule; the remedy is the assertion on
the sentence, not a looser rule. And a message reaching the screen as a value
handed to a "%s", or built with format() into a vector, is read by
tools/plugin-says.sh under PLUGIN_PRINTERS and not here: this suite is about
the body #366 measured, and moving the denominator without moving the
evidence is how a measurement stops meaning anything.
"""
import glob
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.environ.get("PLUGIN_CLI_SOURCE") or os.path.join(
    REPO, "src", "plugins", "bpa", "bpa_cli.c")
CORPUS = os.environ.get("PLUGIN_CLI_CORPUS") or (
    sys.argv[1] if len(sys.argv) > 1 else "")
BASENAME = os.path.basename(SOURCE)

#: A run shorter than this, indent stripped, is a value and not a message.
#: 10 and not say-coverage.sh's 8: that floor bounds what a transcript can be
#: hunted for; this one bounds what a tree of 35 wiki pages and 84 labs can
#: be searched for without a match being an accident.
FLOOR = 10

#: Sites named by nothing in the tree, measured 2026-09-21 against
#: src/plugins/bpa/bpa_cli.c: 69 sites carry a needle, 26 of them named by
#: nothing. 37 before lab/troubleshoot.sh, lab/extblocks.sh and lab/forward.sh
#: asserted nine of the rows whole, on screens the kept corpus of 2026-09-15
#: shows them printing; 28 until lab/troubleshoot.sh grew four answers an
#: operator can get and nothing named - an address inside the prefix and
#: beyond the pool's capacity, a free dynamic block, a port under the pool's
#: range, and a box with no forwards. 21 since it grew the screens of a box
#: NOTHING HAS HAPPENED ON YET - the ones an operator reads first, on a box
#: just configured, and therefore at the worst moment to read a sentence
#: nobody is holding: top with no block held, blocks on a pool that allocates
#: none, logging with no exporter and the event numbers a collector will see,
#: a drain with logging off, the per-worker session count, and a
#: deterministic port answering with its block. May only go down; see the
#: header for how. 19 since lab/limits.sh carried the rate-cap row WHOLE -
#: it had the tail of that sentence and a slice names nothing - and
#: lab/bypass.sh named the line the bypass command answers with, which says
#: how many prefixes it took and on which side. 16 after two of the rows
#: lab/troubleshoot.sh had named were found to be carrying only their
#: OPENING - the count went on listing them, correctly - and lab/ipfix-stop.sh
#: read the line the drain prints, which is the last thing a box says before
#: it goes and the operator's only evidence that it ran.
#: 16 to 13 after lab/ipfix.sh read the logging screen an operator hands
#: their collector team: how big a record is, when this box thinks it booted
#: by IANA element, and the flush counters - which are what tell "nothing
#: happened" from "this box could not get a buffer". Those four assertions
#: were written ABOVE the source of lab/verdict.sh first, where expect_in
#: does not exist: the lab printed `expect_in: not found` to stderr, has no
#: `set -e`, and PASSED with four checks that had never run. They are below
#: it now, and one was watched red with a mutated needle.
#: 13 to 10 after lab/dynamic.sh read the three answers `show bpa
#: translation` gives when there is no subscriber behind the port: it is
#: outside anything the pool hands out, or it is in a block nobody is
#: holding at this instant. An operator arrives at that screen with a port
#: and a complaint and nothing else, and cannot otherwise tell "my records
#: are incomplete" from "there is nothing to find". The caveat under the
#: second - `this answers for NOW ONLY` - is held too, because without it
#: `nobody, right now` reads as `nobody, ever` and a complaint about last
#: Tuesday gets closed as unfounded.
#: 10 to 7, and all three are `show bpa blocks`. lab/detreserve.sh already
#: cut the exhaustion COUNT out of that screen with sed, which says nothing
#: about the words around it - and the words are the whole answer: `reserve
#: exhausted N time(s)` is a number with no cause, while the run beside it
#: names which of the three ways to run out this was. It now holds that, the
#: deterministic header's statement of the reserve's shape, and the
#: hold-down; lab/dynamic.sh holds the one-line header a dynamic pool's
#: blocks sit under, which carries the only number on that screen that is a
#: policy rather than a measurement.
#: 7 to 3, and all four are what `check` answers at the dataplane's own
#: console. The remap lines never reach the VTY whole - prismash takes the
#: `remap:` prefix off and re-wraps them - so lab/commit.sh, which read the
#: remap only through prismash, held none of them; it now asks the
#: dataplane about a pool shrinking from its low end, which is what puts a
#: subscriber served by NO block into the examples, and holds the closing
#: sentence whole. lab/forward.sh holds "forward is valid" on a forward it
#: only checked, and that the check opened no port.
#: 3 to 0, and the last three were one screen each. lab/forward.sh reads
#: the subscriber screen while the forward's block has traffic on it, where
#: the per-block line says "(pinned by a forward)" - the idle screen it
#: already held says it in another sentence - with an unpinned block as the
#: control. lab/mss.sh asks for `auto` before any interface has joined the
#: CGNAT, where there is no MTU to derive a clamp from and the box says so,
#: and asks again after, where it must not. lab/commit.sh reads the session
#: table's screen, whose caveat - the size is a virtual reservation and the
#: OOM killer counts it - is the sentence that screen exists for. Every
#: message bpa_cli.c prints is now named by something that drives it.
NAMED_BY_NOTHING_AT_MOST = 0

fail = 0


def bad(msg):
    global fail
    fail += 1
    print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


def ceiling(found, allowed, what, detail):
    if len(found) > allowed:
        bad(f"{len(found)} {what}, above the ceiling of {allowed}. A message "
            f"nothing names is a branch nothing checks the box still says - "
            f"assert it in a lab that prints it, do not raise the number:")
        for row in detail:
            print(f"    {row}")
    elif len(found) < allowed:
        bad(f"the ceiling says {allowed} and only {len(found)} {what}: lower "
            f"it, or it stops holding anything")
    else:
        ok(f"{len(found)} {what}, at the ceiling of {allowed}")


# --- the plugin, read by the one extraction this repository has --------------
print(f"=== {BASENAME} was read ===")
if not os.path.isfile(SOURCE):
    bad(f"no such source: {SOURCE}")
    print("\n1 FAILURES")
    sys.exit(1)
rows = subprocess.run(
    ["sh", "-c",
     '. "$1/test/say-coverage.sh"; say_init "$2" c vlib_cli_output; '
     'say_messages_c vlib_cli_output; rm -rf "$SAY_DIR"',
     "sh", REPO, SOURCE],
    capture_output=True, text=True, env={**os.environ, "SAY_CORPUS": ""},
).stdout.splitlines()

sites = []      # (line, needle, fragments joined by "~" as plugin-says prints)
under = []      # rows whose longest run is a value
for row in rows:
    line, _, frags = row.partition("\t")
    parts = [p for p in frags.split("\x01") if p]
    needle = max(parts, key=len).strip() if parts else ""
    entry = (int(line), needle, frags.replace("\x01", "~"))
    (sites if len(needle) >= FLOOR else under).append(entry)
if not rows:
    bad(f"the extraction found no vlib_cli_output row in {SOURCE}, so "
        f"nothing below is about the plugin")
else:
    ok(f"{len(rows)} vlib_cli_output row(s), {len(sites)} with a needle of "
       f"{FLOOR} or more literal characters, {len(under)} that are a value "
       f"and cannot be looked for")

# --- (b): what in the tree names each needle ----------------------------------
READERS = ("test/say-coverage.sh", "test/test_plugin_says.sh",
           "test/test_plugin_cli_says.py")
NOT_DOCS = ("docs/roadmap.md",)


def files(patterns, exclude=()):
    out = []
    for pat in patterns:
        for f in sorted(glob.glob(os.path.join(REPO, pat), recursive=True)):
            rel = os.path.relpath(f, REPO)
            if os.path.isfile(f) and rel not in exclude:
                out.append(rel)
    return out


KINDS = {
    "lab": files(["lab/*"]),
    "suite": files(["test/*", "appliance/test_*"], exclude=READERS),
    "wiki": files(["wiki/**/*.md"]),
    "document": files(["docs/*.md", "README.md", "TESTING.md",
                       "CONTRIBUTING.md"], exclude=NOT_DOCS),
}
text = {}
for kind, names in KINDS.items():
    for rel in names:
        with open(os.path.join(REPO, rel), errors="replace") as fh:
            text[rel] = (kind, fh.read())

print(f"\n=== which of them a lab, a suite, a wiki page or a document names ===")
named = {}
for line, needle, _ in sites:
    who = sorted(rel for rel, (kind, body) in text.items() if needle in body)
    named[line, needle] = who
nothing = [(line, needle, full) for line, needle, full in sites
           if not named[line, needle]]
by_kind = {k: 0 for k in KINDS}
for (line, needle), who in named.items():
    for k in {text[rel][0] for rel in who}:
        by_kind[k] += 1
ok(f"{len(sites) - len(nothing)} of {len(sites)} named by something: "
   + ", ".join(f"{n} by a {k}" for k, n in by_kind.items()))
print(f"  named by nothing, {len(nothing)}:")
for line, needle, _ in nothing:
    print(f"    {BASENAME}:{line}  {needle[:100]}")

# --- (a): what a kept corpus shows any lab printing --------------------------
neither = nothing
if CORPUS:
    print(f"\n=== and which of those any lab in {CORPUS} made the box print ===")
    says = subprocess.run(
        ["sh", os.path.join(REPO, "tools", "plugin-says.sh"), SOURCE, CORPUS],
        capture_output=True, text=True,
        env={**os.environ, "PLUGIN_PRINTERS": "vlib_cli_output"})
    boxes = re.search(r"\[ ok \] (\d+) box\(es\) read", says.stdout)
    unseen = set()
    for m in re.finditer(rf"^\s*{re.escape(BASENAME)}:(\d+)  (.*)$",
                         says.stdout, re.M):
        unseen.add((int(m.group(1)), m.group(2)))
    if not boxes:
        bad(f"tools/plugin-says.sh read no box out of {CORPUS}, so nothing "
            f"here is about what the labs printed:\n"
            + "\n".join("    " + l for l in
                        (says.stdout + says.stderr).splitlines()[-6:]))
    else:
        ok(f"{boxes.group(1)} box(es) read by tools/plugin-says.sh")
        neither = [(line, needle, full) for line, needle, full in nothing
                   if (line, full) in unseen]
        printed = [s for s in nothing if s not in neither]
        ok(f"{len(printed)} of the {len(nothing)} named by nothing were "
           f"printed by some lab all the same; a lab that asserts the "
           f"sentence on that screen names it honestly")
        for line, needle, _ in printed:
            print(f"    {BASENAME}:{line}  {needle[:100]}")
        print(f"  neither printed nor named, {len(neither)}: these branches "
              f"have never run anywhere")
        for line, needle, _ in neither:
            print(f"    {BASENAME}:{line}  {needle[:100]}")

# --- the ceiling ---------------------------------------------------------------
print(f"\n=== the count nothing names, against the ceiling ===")
detail = [f"{BASENAME}:{line}  {needle[:100]}" for line, needle, _ in nothing]
if CORPUS:
    if len(neither) > NAMED_BY_NOTHING_AT_MOST:
        bad(f"{len(neither)} neither printed nor named, above the ceiling of "
            f"{NAMED_BY_NOTHING_AT_MOST}: the corpus can only lower this, so "
            f"the tree itself is above it")
    else:
        ok(f"{len(neither)} neither printed nor named, under the ceiling of "
           f"{NAMED_BY_NOTHING_AT_MOST} the tree alone is held to")
ceiling(nothing, NAMED_BY_NOTHING_AT_MOST,
        f"message(s) of {BASENAME} named by no lab, suite, wiki page or "
        f"document (measured 2026-09-17)", detail)

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
