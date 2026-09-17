#!/usr/bin/env python3
"""
Second copies of a truth in the documentation, with the first copy to compare
them against.

Three subjects, one file, because they are one defect shape. This project has
paid for it repeatedly: the `<- not checked` marker text quoted in three places
after it changed, the BGP page's table of what the VTY checks, the wiki titles,
`ip_csum_add_even` defined one way in VPP and the other way in a test shim, the
RFC 8158 record written out in three places. A second copy of a truth is fine.
A second copy nothing compares is not.

WHAT DOES NOT WORK, measured three times, so nobody spends the afternoon on it
again. Comparing a MESSAGE against the source that prints it is unsound in both
directions, and every variant tried here was rejected on its own numbers:

  * the wiki's quoted messages against `hexash.py`: 14 headings, 11 reported
    missing, ALL 11 false positives. Real messages are split across two string
    literals, so a whole-sentence match cannot find them, and Portuguese prose
    headings are not messages at all.
  * `lab/*.sh`'s `expect_not_in` needles against the whole product: 118
    needles, 42 reported unproducible, almost all false. A needle is usually
    composed at run time from a fixture the lab set up - a pool name, an
    address, an interface - so its absence from the source proves nothing. And
    it had false NEGATIVES from the same line-split literals: `no digest:
    written before the chain existed` IS printed by `bpa_audit.c`, across two
    lines.
  * documentation tokens beside a lab reference: 26 findings, about a quarter
    false, written up on #247.

So: compare a truth against the OTHER PLACE IT IS WRITTEN DOWN, never against
the code that would produce it. Every check in this file does the former, and
that is why none of them needs an allowlist.

WHICH ALGs SHIP ON, written down in the plugin and in the documentation.

`BPA_ALG_DEFAULT` in `src/plugins/bpa/bpa_pool.h` is what the box does.
`docs/roadmap.md` section 2.3 is what an operator is told. They were different
sets and nothing compared them: the section carried the decision from #14
("every ALG built here is on") and, eleven lines further down, the paragraph
that decision had reversed ("SHOULD default to off ... FTP, SIP and PPTP
default on"). RTSP and ESP were therefore documented as shipping off while
shipping on, and those two are exactly the ones an operator goes looking for
after a support call: a black frame from an IP camera, an IPsec tunnel that
will not come up. The document sent that diagnosis somewhere else (#230).

Two checks, because the defect had two halves and only one of them is about
ALGs.

THE SETS. The registry, the default mask and the table of what is built must
name the same five. This is what a sixth ALG added to the header and not to
the document would fail.

THE DUPLICATE SENTENCE. The stale paragraph restated `Each ALG MUST be
individually switchable per pool` word for word from eleven lines above, which
is how the duplication was visible without knowing anything about ALGs. A
statement made twice in one document is a statement that can disagree with
itself, and that shape has cost this project four separate defects. Measured
across the five documents an operator reads: zero repeated sentences once this
paragraph is gone, so the rule costs nothing to hold.

And, from #231, the two things that check found nothing about because they are
not sentences: a version pinned in two files, and a line repeated inside a
fenced file listing. The listing is why the sentence rule strips fences and
this one does not. An example command repeated in two places is an example; a
directory listed twice is a claim about the tree, and README.md listed
`docs/context.md` twice for long enough that nobody saw it.
"""
import collections
import contextlib
import io
import os
import re
import subprocess
import sys

fail = 0


def bad(msg):
    global fail
    fail += 1
    print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    return open(os.path.join(REPO, path), encoding="utf-8").read()


HDR = read("src/plugins/bpa/bpa_pool.h")
ROADMAP = read("docs/roadmap.md")

print("=== the registry, the default mask and what the document claims ===")

# The registry: every ALG this plugin has, by the name the CLI takes. Scoped to
# the foreach_bpa_alg block, because bpa_pool.h has other registries built from
# the same `_ (` form and a regex over the whole file read seven ALGs where
# there are five. The check was wrong, not the header.
block = re.search(r"#define foreach_bpa_alg(.*?)\n\n", HDR, re.S)
registry = dict(re.findall(r'_ \((\w+), "(\w+)",', block.group(1))) if block else {}

# The default mask, which is what the box actually does.
m = re.search(r"#define BPA_ALG_DEFAULT\s*\\\n\s*\((.*?)\)\n", HDR, re.S)
if not m:
    bad("BPA_ALG_DEFAULT is gone from bpa_pool.h, so nothing below is "
        "comparing the box against anything")
    on_syms = set()
else:
    on_syms = set(re.findall(r"BPA_ALG_(\w+)", m.group(1)))

have = set(registry)
if len(have) >= 5 and on_syms:
    ok(f"read {len(have)} registered ALG(s) and {len(on_syms)} in the default "
       f"mask")
else:
    bad(f"only {len(have)} registered and {len(on_syms)} in the mask were "
        f"found, so this file is asserting almost nothing")

# The document's sentence, checked rather than trusted: "Every ALG built here
# is on". The roadmap table is NOT what is read for this. It has two SIP rows -
# SIP over UDP, built, and SIP over TCP/TLS, deliberately not being built - and
# a check that resolved a row to an ALG by the name in it would call the second
# a contradiction. That ambiguity is in the subject, not in the document.
if on_syms and on_syms != have:
    off = sorted(registry[s] for s in have - on_syms)
    bad(f"the roadmap says every ALG built here is on, and {off} is registered "
        f"and not in BPA_ALG_DEFAULT. An ALG documented as shipping on and "
        f"shipping off is the support call this section exists to prevent")
elif on_syms:
    ok(f"every registered ALG is in BPA_ALG_DEFAULT: "
       f"{sorted(registry.values())}, which is what the section says in as "
       f"many words")

# And a sixth ALG cannot arrive in the header with the document left behind.
# Only that the name is there at all: which row it belongs to is the human's
# question, and the ambiguity above is why.
missing = sorted(n for n in registry.values()
                 if not re.search(rf"\b{n}\b", ROADMAP, re.I))
if missing:
    bad(f"the plugin registers {missing} and docs/roadmap.md section 2.3 never "
        f"names it, so the box has an ALG an operator is not told about")
else:
    ok("and the roadmap names every one of them")

print("\n=== no document times or counts make test for itself ===")
# #231 took `25 suites in ~21 s` out of the README, because both numbers go up
# whenever somebody adds a test and a check holding a document to them would go
# red on the day a test is written. The same line was in TESTING.md, which is
# where the README sends the reader next, and it survived the fix: 21 seconds
# against 36 measured on this machine with a lab suite beside it, and 25 suites
# against 38.
#
# So the rule is the one #231 argued for, applied to every document that quotes
# the command rather than to the one that was noticed. The claim each number
# was making - no root, no VPP - is kept.
_TIMED = re.compile(r"make test[^\n]*#[^\n]*?(\d+)\s*(s|sec|seconds|m|min|minutes)\b")
_COUNTED = re.compile(r"make test[^\n]*#[^\n]*?(\d+)\s*suites?\b")
_stated = []
for _name in ("README.md", "TESTING.md", "CONTRIBUTING.md"):
    _path = os.path.join(REPO, _name)
    if not os.path.exists(_path):
        continue
    _text = open(_path, encoding="utf-8").read()
    for _rx, _what in ((_TIMED, "a time"), (_COUNTED, "a suite count")):
        for _m in _rx.finditer(_text):
            _stated.append(f"{_name} states {_what} for `make test`: {_m.group(0)!r}")
if _stated:
    for _t in _stated:
        bad(f"{_t}. Both go up whenever a test is added, so the document is "
            f"wrong on the day somebody writes one. Say what it needs, not how "
            f"long it takes")
else:
    ok("README, TESTING and CONTRIBUTING leave the count and the clock to the "
       "run that produces them")

print("\n=== the sizing an ISP buys a prefix on ===")
# README.md's 5W2H table answers "How much" with a number somebody spends money
# on: 200k subscribers in deterministic mode with 512 port blocks require a /21
# of public IPv4. It is arithmetic, the reference model does that arithmetic,
# and nothing compared them.
#
# It is not a safe over-estimate either, which is what makes it worth holding
# in both directions: a /21 gives 258,048 and a /22 gives 129,024, so the claim
# is TIGHT. A default block size that grew, or a usable port range that
# narrowed, would move the answer to /20 and leave an ISP having bought half
# the addresses they need - and the box would be right and the README wrong.
sys.path.insert(0, os.path.join(REPO, "ref"))
try:
    from bpa_model import PoolConfig                              # noqa: E402
except ImportError as _e:                                        # pragma: no cover
    bad(f"the reference model could not be imported, so the sizing claim is "
        f"compared against nothing: {_e}")
    PoolConfig = None

_README = read("README.md")
_m = re.search(r"(\d+)k subscribers in deterministic mode with (\d+)-port "
               r"blocks require an? `(/\d+)`", _README)
if not _m:
    bad("README.md no longer states how much public IPv4 a subscriber count "
        "needs, in the words this reads, so nothing below is checking it")
elif PoolConfig is not None:
    _subs = int(_m.group(1)) * 1000
    _block = int(_m.group(2))
    _bits = int(_m.group(3)[1:])

    def _capacity(bits):
        return PoolConfig("sizing", ["100.64.0.0/10"],
                          [f"200.150.0.0/{bits}"], block_size=_block).capacity

    _got = _capacity(_bits)
    _smaller = _capacity(_bits + 1)     # one bit smaller is half the addresses
    if _got < _subs:
        bad(f"README.md says {_subs:,} subscribers fit in a /{_bits} with "
            f"{_block} port blocks, and the model says that prefix holds "
            f"{_got:,}. An ISP sizing a purchase on this line buys too few "
            f"addresses")
    elif _smaller >= _subs:
        bad(f"README.md says {_subs:,} subscribers need a /{_bits}, and a "
            f"/{_bits + 1} already holds {_smaller:,}. The line asks an ISP to "
            f"buy twice what it needs")
    else:
        ok(f"{_subs:,} subscribers at {_block} ports fit in a /{_bits} "
           f"({_got:,}) and not in a /{_bits + 1} ({_smaller:,})")

print("\n=== the VPP version is pinned in one place and quoted in another ===")
# A pin, unlike a suite count, does not drift on correct work: it changes when
# somebody moves the project to a new VPP, and that is exactly the day both
# copies must move. The README's number is the one an operator builds from.
README = read("README.md")
BUILD = read("tools/build.sh")
pinned = re.search(r"IMAGE=\$\{IMAGE:-hexarouting-dev:([0-9.]+)\}", BUILD)
quoted = re.findall(r"VPP pinned to ([0-9.]+)", README)
if not pinned:
    bad("tools/build.sh no longer pins an image version, so the line below is "
        "comparing the README against nothing")
elif not quoted:
    bad("README.md no longer says which VPP the build pins, which is the "
        "number somebody building this needs")
elif set(quoted) != {pinned.group(1)}:
    bad(f"README.md says VPP {quoted} and tools/build.sh pins "
        f"{pinned.group(1)}")
else:
    ok(f"both say VPP {pinned.group(1)}")

print("\n=== and no document states the same thing twice ===")
# The shape that produced #230, checked without knowing what the sentence is
# about. Code fences are dropped first: an example command repeated in two
# places is an example, not a claim.
for path in ("docs/roadmap.md", "docs/architecture.md", "docs/logging.md",
             "docs/appliance.md", "README.md"):
    text = re.sub(r"```.*?```", "", read(path), flags=re.S)
    flat = re.sub(r"\s+", " ", text)
    # Long sentences only. A short one repeating is a heading or a label, and
    # a rule that went red on those would be a rule somebody switches off.
    sents = [s.strip() for s in re.split(r"(?<=[.:])\s", flat)
             if len(s.strip()) > 60]
    dup = [s for s, n in collections.Counter(sents).items() if n > 1]
    if dup:
        for s in dup[:3]:
            bad(f"{path} says this twice, so the two copies can disagree and "
                f"one of them did: {s[:120]!r}")
    else:
        ok(f"{path}: {len(sents)} sentence(s), none repeated")

    # Whole repeated lines INSIDE the fences, which the pass above strips. A
    # file listing is a claim about the tree and a duplicate in one is a wrong
    # claim; an example command repeated in two places is an example, so only
    # lines that look like a listing row are compared: a path, whitespace, and
    # a description of it.
    rows = [ln.rstrip() for ln in read(path).split("\n")
            if re.match(r"^[\w./-]+/?\s{2,}\S", ln)]
    dup_rows = [r for r, n in collections.Counter(rows).items() if n > 1]
    if dup_rows:
        for r in dup_rows[:3]:
            bad(f"{path} lists this twice: {r.strip()!r}")
    elif rows:
        ok(f"    and {len(rows)} listing row(s), none repeated")

print("\n=== nothing is blocked and delivered in the same document ===")
# docs/roadmap.md has a table headed "What is blocked on somebody else, and on
# what", and the sentence above it states what the table is FOR: "Kept short
# and kept honest, so the rest of this file is read as work that can actually
# start." That promise is the thing being checked. A row naming an issue the
# same file elsewhere calls delivered does not merely age badly: it tells the
# next reader that the queue is shorter than it is, which is the one question
# this table exists to answer.
#
# It has already happened. #30, the soak test, was shipped in 0.71.0 and
# described as delivered in three separate places in this file, while the
# blocked table went on listing it beside #28 and #29 as "hardware this box
# does not have". The soak needs no NIC - that is the first sentence of its
# own section, 96 lines further down.
#
# Compared against the OTHER PLACE IT IS WRITTEN DOWN and never against the
# issue tracker, which is the rule at the top of this file and is also the
# only rule a test with no network can follow. What that cannot see is an
# issue simply closed with nothing in this file saying so, and #197 was
# exactly that on the same day: the table described a promotion to a refusal
# as pending when it had shipped and the issue was closed. Nothing offline
# could have caught it, and saying so here is better than implying this check
# covers more than it does.
ROADMAP = read("docs/roadmap.md")
BLOCKED_HEAD = "#### What is blocked on somebody else, and on what"
start = ROADMAP.find(BLOCKED_HEAD)
if start < 0:
    bad("docs/roadmap.md no longer has the blocked-on-somebody-else section, "
        "so this check is reading nothing")
else:
    # To the next heading of the same level, not to the next blank line: the
    # table has rows long enough to wrap in any editor and short enough to be
    # missed by a needle that stops early.
    end = ROADMAP.find("\n#### ", start + 1)
    table = ROADMAP[start:end if end > 0 else len(ROADMAP)]

    def issues(text):
        return set(int(n) for n in
                   re.findall(r"/hexarouting/issues/(\d+)\)", text))

    blocked = issues(table)
    # The marker is the EMPHASISED word - `*delivered*` or `**delivered**` -
    # attributed to the nearest issue link before it on the SAME LINE. Both
    # halves of that are there because a looser rule was measured and was
    # wrong in the way this file's own header warns about.
    #
    # A window of characters came first: one row reads
    #
    #   [#28](...) throughput on real NICs, [#29](...) symmetric RSS,
    #   [#30](...) soak test - *delivered*
    #
    # and any window wide enough to reach #30's marker from #30 reached it
    # from #29 too. Three findings, two false.
    #
    # Nearest-link-in-the-same-paragraph came next, and a markdown table is
    # one paragraph: it attributed a `*delivered*` in the table's fourth row
    # to the last link in its first row, three rows above. Hence the same
    # line.
    #
    # And the word has to be emphasised, which is the only part that is a
    # judgement rather than a measurement. Plain "shipped" and plain
    # "delivered" are ordinary English in this file - "two newer release
    # trains have shipped since" is about fd.io and not about #36 - so a
    # marker spelling is what separates a claim from a sentence. The cost is
    # that somebody can write the claim unemphasised and this will not see
    # it; the alternative was a check nobody could leave switched on.
    delivered = set()
    outside = ROADMAP[:start] + ROADMAP[end:] if end > 0 else ROADMAP[:start]
    LINK = re.compile(r"/hexarouting/issues/(\d+)\)")
    for m in re.finditer(r"\*\*?(delivered|shipped)\*?\*", outside):
        line_start = outside.rfind("\n", 0, m.start()) + 1
        before = list(LINK.finditer(outside, line_start, m.start()))
        if before:
            delivered.add(int(before[-1].group(1)))
    both = sorted(blocked & delivered)
    if both:
        for n in both:
            bad(f"docs/roadmap.md lists #{n} as blocked on somebody else and "
                f"elsewhere in the same file calls it delivered. The table's "
                f"own promise is that the rest of the file is work that can "
                f"actually start")
    elif len(blocked) >= 5:
        ok(f"the blocked table names {len(blocked)} issue(s) and the file "
           f"calls {len(delivered)} delivered, with no issue in both")
    else:
        bad(f"only {len(blocked)} issue(s) were read out of the blocked "
            f"table, so this check is asserting almost nothing")

print("\n=== a counted list says how many it has ===")
# CONTRIBUTING.md's release procedure opens "When it happens it MUST produce
# all <n> of:" and then numbers them. The number and the list are two copies
# of the same fact, and the number is the one a reader trusts: somebody who
# has done five things and read "all five of" stops, and the sixth is the one
# that does not get done. This list gained an item today and the count had to
# be changed by hand, which is the whole reason for checking it.
#
# Only this one list. A rule that demanded every "three reasons" in the
# repository be countable would be a rule somebody switches off, and the
# argument above is specific to a checklist somebody works through.
WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10}
CONTRIB = read("CONTRIBUTING.md")
m = re.search(r"MUST\*\* produce all ([a-z]+) of:\n", CONTRIB)
if not m:
    bad("CONTRIBUTING.md no longer opens the release list with a count, so "
        "this check is reading nothing")
else:
    said = WORDS.get(m.group(1))
    # From the sentence to the next heading, so a numbered list further down
    # the file is not counted into this one.
    end = CONTRIB.find("\n### ", m.end())
    items = re.findall(r"^(\d+)\. \*\*", CONTRIB[m.end():end], re.M)
    if said is None:
        bad(f"CONTRIBUTING.md says 'all {m.group(1)} of' and that is not a "
            f"number this check knows")
    elif len(items) != said:
        bad(f"CONTRIBUTING.md's release procedure says 'all {m.group(1)} of' "
            f"and lists {len(items)}. A reader who has done {said} things "
            f"stops, and the item that does not get done is the last one")
    elif [int(i) for i in items] != list(range(1, said + 1)):
        bad(f"CONTRIBUTING.md's release list is numbered {items}, which is "
            f"not 1 to {said}")
    else:
        ok(f"the release procedure says 'all {m.group(1)} of' and lists "
           f"{len(items)}, numbered 1 to {said}")

print("\n=== a document that quotes install.sh's own line quotes it right ===")
# `appliance/install.sh` opens by saying what it is for, and two other files
# quote that opening back AND cite the line number it is on. #295 is the change
# that rewrites that header - the claim was measured false for anybody without
# a build tree - so this is the day the copies could go stale, and nothing
# compared them.
#
# Same shape as everything above, and the same rule: compared against the OTHER
# PLACE IT IS WRITTEN DOWN, never against a behaviour. A quoted sentence with a
# line number attached is the strongest form of that shape in this tree,
# because both halves can rot independently - the words can be edited, or a
# line can be inserted above them and move the sentence without changing it.
#
# CHANGELOG.md quotes the same line and is deliberately NOT read. A changelog
# entry records what a release said; it is not a claim about the tree today,
# and a check that made it track the source would be a check that asks somebody
# to rewrite history to stay green.
_INSTALL = read("appliance/install.sh").split("\n")

# The opening comment block only, down to the first line that is not a comment.
# Stopping there keeps the `#` comments further down the script - which say
# other things about other subjects - out of the haystack a quotation is
# searched for in.
_head_text, _head_line = "", []
for _n, _ln in enumerate(_INSTALL, 1):
    if _n == 1 and _ln.startswith("#!"):
        continue
    if not _ln.startswith("#"):
        break
    _txt = _ln.lstrip("#").strip()
    if _head_text:
        _head_text += " "
        _head_line.append(_n)
    _head_text += _txt
    _head_line.extend([_n] * len(_txt))


def _prose(text):
    """Comment markers off and whitespace flattened.

    A sentence in this repository wraps at 79 columns, and in a shell file it
    wraps across `#` lines as well. A quotation that spans a wrap is one
    string to the reader and two lines to a naive search, and the first
    version of this check reported both real quotations as missing for
    exactly that reason.
    """
    return re.sub(r"\s+", " ", re.sub(r"(?m)^\s*#+ ?", "", text))


_ORDINAL = {"second": 2, "third": 3, "fourth": 4, "fifth": 5}
# The quoted text has to look like PROSE: letters, spaces and ordinary
# punctuation, nothing else. Without that clause lab/upgrade.sh produced a
# third finding, and it was false - the file says "itself claims at its line 4"
# in an echo and the next line is `sh /src/appliance/install.sh > ...`, so the
# window reached past the sentence into the command and called a shell line a
# quotation. A lab that drives the path is full of install.sh beside quoted
# strings, and that is the one place this would have gone red on work that is
# right.
# And no full stop anywhere between the three parts, because a quotation
# attributed to a line sits in the SAME SENTENCE as the attribution. That is
# what finally separated the two real citations from the lab: `echo "itself
# claims at its line 4."` ends the sentence there, and the quoted strings the
# window was reaching are two shell lines further down, in a give_up call.
# Prose-shaped and all of it, so the shape test alone could not tell them
# apart. The cost is that a citation written "see appliance/install.sh. Its
# line 4 says ..." is not read at all, which is a miss and not a false alarm.
_CITE = re.compile(r"install\.sh\b[^.]{0,160}?"
                   r"(?:line (\d+)|(second|third|fourth|fifth) line)"
                   r"[^.]{0,160}?\"([A-Za-z0-9 ,.'()`-]{20,})\"")
_QUOTERS = ("docs/roadmap.md", "docs/appliance.md", "docs/routing.md",
            "lab/upgrade.sh", "README.md", "TESTING.md", "CONTRIBUTING.md")
_quotations = 0
for _path in _QUOTERS:
    if not os.path.exists(os.path.join(REPO, _path)):
        continue
    for _m in _CITE.finditer(_prose(read(_path))):
        _quotations += 1
        _cited = int(_m.group(1)) if _m.group(1) else _ORDINAL[_m.group(2)]
        _quote = re.sub(r"\s+", " ", _m.group(3)).strip()
        _at = _head_text.find(_quote)
        if _at < 0:
            bad(f"{_path} quotes appliance/install.sh as saying {_quote!r} and "
                f"its opening comment does not say that. One of the two was "
                f"edited and the other was left behind")
        else:
            # From the start of the SENTENCE the quote sits in, not from the
            # quote. Both citations say "line 4", and line 4 is where the
            # sentence "This path is for updating a box in production without
            # reinstalling it." begins - the quoted fragment itself starts one
            # line further down, because the line wraps in the middle of it.
            # Holding the fragment alone reported both of them wrong, which
            # would have been this check going red on prose that is correct.
            _dot = _head_text.rfind(". ", 0, _at)
            _from = _dot + 2 if _dot >= 0 else 0
            _span = set(_head_line[_from:_at + len(_quote)])
            if _cited not in _span:
                bad(f"{_path} says appliance/install.sh carries {_quote!r} on "
                    f"line {_cited} and it is on line "
                    f"{'-'.join(str(x) for x in sorted(_span))}. A reader sent "
                    f"to a line number reads whatever is there now")
            else:
                ok(f"{_path}: install.sh line {_cited} still says "
                   f"{_quote[:46]!r}...")
if _quotations >= 2:
    ok(f"{_quotations} quotation(s) of install.sh's own words, each held "
       f"against the line it cites")
else:
    bad(f"only {_quotations} quotation(s) of appliance/install.sh were found, "
        f"so this check is asserting almost nothing")

print("\n=== the RFC timeout floors, in three places ===")
# The numbers an operator sizes a pool's timeouts against, written down three
# times: the X-macro in src/plugins/bpa/bpa_pool.h that the dataplane actually
# enforces, the table in docs/architecture.md, and the page an operator reads
# on the wiki. Nothing compared them.
#
# The macro is the one that decides. `show bpa timeouts` marks a value below a
# floor by asking the dataplane, and hexash reads the answer back rather than
# recomputing it - which is right, and is why a wrong number in a DOCUMENT is
# the failure mode here rather than a wrong refusal. An operator who shortens
# a timeout because a pool is filling reads the page, not the header, and a
# page carrying last year's floor tells them a value is safe when the box will
# mark it.
#
# Compared by NAME, SECONDS and RFC REFERENCE, not by the prose after them.
# The three copies say what goes wrong in three different sentences, two of
# them in Portuguese, and demanding one wording would be demanding a
# translation nobody wants.
FLOOR_H = read("src/plugins/bpa/bpa_pool.h")
m = re.search(r"#define foreach_bpa_timeout_floor(.*?)\n\n", FLOOR_H, re.S)
if not m:
    bad("bpa_pool.h no longer has foreach_bpa_timeout_floor, so this check is "
        "reading nothing")
else:
    macro = [(name, int(secs), rfc) for name, secs, rfc in
             re.findall(r'_ \(\w+, "([a-z-]+)", (\d+), "([^"]+)"', m.group(1))]

    def doc_floors(path, pattern):
        rows = []
        for line in read(path).split("\n"):
            r = re.match(pattern, line)
            if r:
                rows.append((r.group(1), int(r.group(2)), r.group(3).strip()))
        return rows

    ROW = r"^\|\s*`([a-z-]+)`\s*\|\s*(\d+) s\s*\|\s*(RFC [^|]+)\|"
    copies = [("docs/architecture.md", doc_floors("docs/architecture.md", ROW)),
              ("wiki/referencia/timeouts-e-os-pisos-das-rfcs.md",
               doc_floors("wiki/referencia/timeouts-e-os-pisos-das-rfcs.md", ROW))]
    if len(macro) < 4:
        bad(f"only {len(macro)} floor(s) were read out of the macro, so this "
            f"check is asserting almost nothing")
    for path, rows in copies:
        if rows != macro:
            bad(f"{path}'s floor table is not what the dataplane enforces:\n"
                f"    document  {rows}\n    bpa_pool.h {macro}")
        else:
            ok(f"{path}: all {len(rows)}, by name, seconds and RFC")

print("\n=== a figure this project has published a correction for ===")
# #288 reported that `statseg { size 1G }` costs `+395 MB`. It does not: in the
# table that number came from it is the RSS difference between 0 and 40,000
# SESSIONS on a box that already had the 1 GB segment, so it is the cost of the
# traffic and not of the segment. Nothing measured isolates what a larger
# statseg costs an idle box, and docs/appliance.md says so in as many words.
#
# docs/roadmap.md restated the retracted number as fact anyway, in the same
# release that shipped the correction, because the row was written from the
# issue body and nothing compared the two. That is this file's subject exactly:
# a second copy of a truth is fine, a second copy nothing compares is not.
#
# A NUMBER and not a sentence, which is why the needle is sound here where the
# header above rejects needles against messages: a retracted figure either
# appears or it does not, and it cannot be reworded into passing.
_CORRECTION = re.compile(r"\+395 MB.{0,400}?correction", re.S)
_FIGURE = re.compile(r"\+?\s*395\s*MB")
_HOLDER = "docs/appliance.md"

if not _CORRECTION.search(read(_HOLDER)):
    bad(f"{_HOLDER} no longer carries the correction for the +395 MB figure, "
        f"so this check is holding the other documents to nothing and the "
        f"retracted number has no owner")
else:
    ok(f"{_HOLDER} holds the correction, so there is something to compare "
       f"against")
    _restated = []
    _read = 0
    # SKIPPED BY PATH COMPONENT, not by substring. Written as
    # `any(s in dirpath for s in (".git", "/build", "/ref"))` this walk
    # silently skipped `wiki/referencia/` and everything under it, because
    # "/ref" is a substring of "/referencia" - eleven of the pages an operator
    # actually reads, reported green having never been opened. Found by
    # counting what the walk reached (38 files) against what the tree has.
    _SKIP = {".git", "build", "ref", ".build-vpp"}
    for _dirpath, _dirnames, _filenames in os.walk(REPO):
        _dirnames[:] = [_d for _d in _dirnames if _d not in _SKIP]
        for _f in _filenames:
            if not _f.endswith(".md"):
                continue
            _rel = os.path.relpath(os.path.join(_dirpath, _f), REPO)
            _read += 1
            # No exception but the holder, and the holder earns it because
            # naming the figure is how the correction is made. Measured before
            # the rule was written: zero occurrences anywhere else, the
            # changelog included, so an exception list would be an unused one.
            if _rel == _HOLDER:
                continue
            for _line in read(_rel).split("\n"):
                if _FIGURE.search(_line):
                    _restated.append(f"{_rel}: {_line.strip()[:120]}")
    if _restated:
        for _r in _restated:
            bad(f"the retracted +395 MB figure is stated again, and the only "
                f"document allowed to name it is the one that corrects it "
                f"({_HOLDER}):\n    {_r}")
    elif _read < 45:
        # A walk that skips too much reports green having opened nothing, and
        # says the same words as a walk that read everything. The first version
        # of this one skipped wiki/referencia/ and reported on 38 files where
        # the tree has 52.
        bad(f"only {_read} document(s) were read, so this check is asserting "
            f"almost nothing: something in the walk is skipping the tree")
    else:
        ok(f"and none of the other {_read} document(s) restates it")

print("\n=== the off-run counts TESTING.md states, against the labs' floors ===")
# THE DEFECT (#317). TESTING.md said `sh tools/lab.sh ipfix no-exporter`
# "turns 19 of its assertions red". lab/ipfix.sh declares
# `# OFF-RED-AT-LEAST: 20`, and tools/run-all-labs.sh FAILS an off run that
# goes below its floor - so the document stated, as measured, a number the
# suite would refuse. Somebody reading it to decide whether an off run looked
# right would have called a passing run a regression.
#
# ONE HOLDER AND A READER, which is this file's subject. The floor marker in
# the lab is what the suite enforces; TESTING.md is the second copy; nothing
# compared them. The comparison is a number against a number, so it is sound
# where this file's header rejects comparing prose.
_FLOOR_RE = re.compile(r"^#\s*OFF-RED-AT-LEAST:\s*(\d+)", re.M)
_floors = {}
for _f in sorted(os.listdir(os.path.join(REPO, "lab"))):
    if not _f.endswith(".sh"):
        continue
    _m = _FLOOR_RE.search(read(os.path.join("lab", _f)))
    if _m:
        _floors[_f[:-3]] = int(_m.group(1))
_doc = read("TESTING.md")
_stated = re.findall(
    r"`sh tools/lab\.sh (\S+) (\S+)`[^|]*?(\d+) of its assertions red",
    _doc)
if not _floors:
    bad("no lab declares an OFF-RED-AT-LEAST floor any more, so this check "
        "has nothing to compare against")
elif not _stated:
    bad("TESTING.md states no off-run count for any lab, so this check is "
        "reading nothing: the sentence it compares has been reworded")
else:
    for _lab, _mode, _n in _stated:
        _floor = _floors.get(_lab)
        if _floor is None:
            bad(f"TESTING.md says `{_lab} {_mode}` turns {_n} assertions red "
                f"and lab/{_lab}.sh declares no OFF-RED-AT-LEAST floor, so "
                f"nothing enforces that number")
        elif int(_n) != _floor:
            bad(f"TESTING.md says `{_lab} {_mode}` turns {_n} assertions red "
                f"and lab/{_lab}.sh's floor is {_floor}: the suite FAILS an "
                f"off run below its floor, so the document states a number "
                f"the suite would refuse")
        else:
            ok(f"{_lab} {_mode}: {_n}, which is the floor lab/{_lab}.sh "
               f"declares")
    ok(f"and {len(_floors)} lab(s) declare a floor for this to read")

print("\n=== TESTING.md does not claim a CI this repository has not got ===")
# It said "CI runs level 1 on every commit" (#315). There are no workflows and
# no run has ever been recorded: `.github/` holds ISSUE_TEMPLATE and nothing
# else. A reader takes that sentence as a safety net that catches what they
# forget, and there is nothing behind it.
#
# The holder is the filesystem again: either there is a workflow directory
# with something in it, or there is not.
_WF = os.path.join(REPO, ".github", "workflows")
_has_ci = os.path.isdir(_WF) and any(
    _f.endswith((".yml", ".yaml")) for _f in os.listdir(_WF))
# "no CI" is the document SAYING there is none, so it is removed before the
# search rather than excluded by a lookahead - the first version of this
# matched the very sentence that fixed the defect.
_ci_text = re.sub(r"\bno CI\b", "", read("TESTING.md"))
_claims_ci = re.search(r"\bCI\b", _ci_text)
if _claims_ci and not _has_ci:
    bad(f"TESTING.md says {_claims_ci.group(0)!r} at offset "
        f"{_claims_ci.start()} and this repository has no workflow: a reader "
        f"takes that for a safety net that catches what they forget")
elif _has_ci and not _claims_ci:
    bad("this repository has a workflow and TESTING.md does not say so, so "
        "nobody reading it knows what already runs without them")
else:
    ok("no CI, and the document says so" if not _has_ci
       else "a workflow exists and the document says so")

print("\n=== a width a document states for a line it quotes ===")
# THE DEFECT (#319). docs/routing.md said
# `neighbor 2001:db8:aaaa:bbbb:cccc:dddd:eeee:ffff route-server-client` sits
# "at 66" columns. It is 67. The number is the whole of the argument in that
# paragraph - it is the statement that does NOT fit inside 79 less the indent
# and the mark - so it is a number a reader checks the arithmetic of, and the
# arithmetic was one column out.
#
# SOUND BECAUSE THE HOLDER IS THE STRING ITSELF. Nothing here reads a document
# against another document or against a message: the quoted text is right
# there, and its length either is the stated number or is not. Measured before
# it was written: 2 such pairs in docs/routing.md, both checkable, 0 false.
#
# The pattern spans a line break because prose wraps: the backticks and the
# "at <n>" are frequently on different lines, and a check that only matched
# them together on one line would have found neither of these two.
_flat = re.sub(r"\s*\n\s*", " ", read("docs/routing.md"))
_pairs = re.findall(r"`([^`\n]{8,120})`\s+at\s+(\d+)", _flat)
if not _pairs:
    bad("docs/routing.md states no width for any line it quotes, so this "
        "check is reading nothing: the sentence it compares has been reworded")
else:
    for _stmt, _n in _pairs:
        if len(_stmt) != int(_n):
            bad(f"docs/routing.md says `{_stmt}` is at {_n} columns and it is "
                f"{len(_stmt)}: the paragraph's argument is about whether that "
                f"number fits inside the console budget, so it is a number the "
                f"reader does the arithmetic with")
    if all(len(_s) == int(_n) for _s, _n in _pairs):
        ok(f"both of them, measured off the quoted text"
           if len(_pairs) == 2 else f"all {len(_pairs)} of them")

print("\n=== the volume tables do their own arithmetic ===")
# THE DEFECT (#320). docs/logging.md's TL;DR said per-flow NAT is "~2.4 GB/day"
# and its own Volume table, 320 lines down, said "~1.6 GB" for the same 40
# million records at the ~40 bytes the page states as its premise. The wiki's
# table, which is what an operator sizes hardware with, said 2,4 GB in a table
# whose own premise line says 40 bytes. 40e6 x 40 = 1.6e9, so the table was
# right and the other two were not.
#
# The number is not decoration: the ratio between the two rows IS the
# commercial argument for BPA, and it reads as 30x in one place and 20x in the
# other.
#
# ARITHMETIC AND NOT A NEEDLE. The premise is on the page, in the sentence
# above the table; the rows are on the page; the check multiplies. Nothing is
# compared against code or against another document's prose.
_VOL = [("docs/logging.md", r"~(\d+) bytes with IPFIX framing",
         r"\|[^|]*\|\s*~?([\d.]+) (million|thousand)\s*\|\s*~?([\d.]+) (GB|MB)\s*\|"),
        ("wiki/comecar-aqui/dimensionamento-e-requisitos.md",
         r"~(\d+) bytes por registro",
         r"\|[^|]*\|\s*\\?~?([\d.]+) milh[oõ]es?\s*\|\s*\\?~?([\d,]+) (GB|MB)")]
_checked = 0
for _path, _premise_re, _row_re in _VOL:
    _txt = read(_path)
    _pm = re.search(_premise_re, _txt)
    if not _pm:
        bad(f"{_path} no longer states the bytes-per-record its volume table "
            f"is computed from, so the table is a set of numbers nothing can "
            f"check")
        continue
    _bytes = int(_pm.group(1))
    for _m in re.finditer(_row_re, _txt):
        _g = _m.groups()
        _recs = float(_g[0]) * 1e6
        _vol = float(_g[-2].replace(",", ".")) if _g[-1] in ("GB", "MB") \
            else None
        _unit = _g[-1]
        if _vol is None:
            continue
        _want = _recs * _bytes / (1e9 if _unit == "GB" else 1e6)
        _checked += 1
        # A tenth either way: the tables say "order of magnitude, not
        # precision" and round to one decimal.
        if abs(_want - _vol) > 0.15:
            bad(f"{_path}: {_g[0]} million records at {_bytes} bytes is "
                f"{_want:.1f} {_unit} and the table says {_g[-2]}: the ratio "
                f"between these rows is the commercial argument, and it is "
                f"being stated wrong")
if _checked >= 2:
    ok(f"{_checked} row(s) across {len(_VOL)} table(s), each against the "
       f"bytes-per-record its own page states")
else:
    bad(f"only {_checked} volume row(s) were read, so this check is asserting "
        f"almost nothing")


print("\n=== a '?' listing a document prints, against the tree that answers it ===")
# THE DEFECT. docs/routing.md quotes `show cgnat ?` with eleven sub-commands.
# The tree offers twelve: `show cgnat ha` has been there since HA comparison
# was built, and the transcript was never touched. An operator reading that
# page to find out what the box has is being handed a list the box would not
# print, on the screen whose whole job is to say what is there. The `vrf`
# description inside an interface block had drifted the same way, in both of
# the two places routing.md prints that listing.
#
# NOT COVERED BY test_doc_commands.py, and the boundary is worth being exact
# about: that file walks the COMMAND on the prompt line - `show cgnat ?` - and
# stops at the placeholder. What follows the prompt is OUTPUT, and no check
# read it. A transcript of `?` is the one piece of output in these pages that
# is not prose: it is the tree's own option set, rendered.
#
# ONE HOLDER AND A READER. The holder is `Shell._options()`, which is what `?`
# and TAB both answer from; the document is the second copy. This compares a
# set of names against a set of names, so it is sound where this file's header
# rejects comparing prose against the source that prints it.
_sys_path_added = os.path.join(REPO, "src", "hexash")
if _sys_path_added not in sys.path:
    sys.path.insert(0, _sys_path_added)
import hexash                                                    # noqa: E402

#: The box behind the walk. Interfaces come from the dataplane, so a listing
#: taken inside `interface <name>` needs one to enter - and the names here are
#: the ones the documentation's own transcripts use.
_IFACES = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)
GigabitEthernet0/12/0             1      up          9000/0/0/0
TenGigabitEthernet0/8/0           4      up          9000/0/0/0
host-veth-vpp                     5      up          9000/0/0/0
local0                            0     down            0/0/0/0
"""


def _shell(mode, enter=()):
    """A Shell parked where a transcript's prompt says it is."""
    sh = hexash.Shell()
    # Wide on purpose: a page documents the product, not one account (#35).
    sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT}
    sh.dp = type("D", (), {
        "TRACE": False,
        "run": lambda _s, c: (0, _IFACES) if c.startswith("show interface")
        else (0, ""),
        "reachable": lambda _s: True})()
    # The developer's own cards are a property of the MACHINE running this
    # suite, not of the product: left reachable, `dataplane interface` would
    # enumerate them and this check would pass or fail on whose laptop it ran.
    sh.SYS_CLASS_NET = "/nonexistent/sys/class/net"
    sh.frr = type("F", (hexash.Frr,), {"available": lambda s: False,
                                       "running": lambda s: [],
                                       "show": lambda s, c: (1, "")})()
    _quiet = io.StringIO()
    with contextlib.redirect_stdout(_quiet):
        if mode == "configuration":
            sh.feed("configure")
        for _line in enter:
            sh.feed(_line)
    return sh


#: Each entry is a transcript this file knows how to stand in front of: the
#: prompt line as the page prints it, and what to type to get there. A prompt
#: shape nobody lists is reported rather than skipped, because a transcript
#: added later and silently unread is this check going quiet.
_LISTINGS = {
    "prismarouting> show cgnat ?": ("operational", (), "show cgnat "),
    "prismarouting# [edit interface GigabitEthernet0/12/0] ?":
        ("configuration", ("interface GigabitEthernet0/12/0",), ""),
}

_seen = set()
for _path in ("docs/routing.md",):
    _lines = read(_path).split("\n")
    for _i, _line in enumerate(_lines):
        _where = _LISTINGS.get(_line.strip())
        if _where is None:
            continue
        _mode, _enter, _prefix = _where
        # The listing is the indented rows under the prompt, up to the first
        # line that is not one. `?` prints "  <name>  <description>".
        _rows = {}
        for _row in _lines[_i + 1:]:
            _m = re.match(r"^  (\S+)  +(\S.*?)\s*$", _row)
            if not _m:
                break
            _rows[_m.group(1)] = _m.group(2)
        if not _rows:
            bad(f"{_path}: '{_line.strip()}' is followed by no option rows, "
                f"so this check is reading nothing")
            continue
        _seen.add(_line.strip())
        _opts, _hint, _ = _shell(_mode, _enter)._options(_prefix, False)
        _missing = sorted(set(_opts) - set(_rows))
        _extra = sorted(set(_rows) - set(_opts))
        if _missing:
            bad(f"{_path}: '{_line.strip()}' does not list {_missing}, and "
                f"the box offers them: the page is a screen this VTY would "
                f"not print, on the one screen whose job is saying what is "
                f"there")
        if _extra:
            bad(f"{_path}: '{_line.strip()}' lists {_extra}, and the box "
                f"offers no such option: a reader is told to type something "
                f"that is refused")
        _drifted = [n for n in sorted(set(_rows) & set(_opts))
                    if _rows[n] != _opts[n]]
        for _n in _drifted:
            bad(f"{_path}: '{_line.strip()}' describes {_n!r} as "
                f"{_rows[_n]!r} and the box says {_opts[_n]!r}")
        if not (_missing or _extra or _drifted):
            ok(f"{_path}: '{_line.strip()}' - all {len(_rows)} option(s), "
               f"name and description")
_lost = sorted(set(_LISTINGS) - _seen)
if _lost:
    bad(f"the '?' transcript(s) {_lost} are in no document this check reads: "
        f"a prompt has been reworded or moved and this went quiet")

print("\n=== the reserve's cost, against the model that is its definition ===")
# architecture.md states what a reserve of one block costs under each policy,
# and names ref/validate.py as where it was measured. The blocked row said
# "738 of 882 subscribers moved ... plus the 21 the truncation dropped". The
# model says 750 and 7. The 7 is also what the interleaved row beside it
# derives from the pool's own definition - `outside.count x reserve`, 7 x 1 -
# so the document contradicted itself and the wrong half was the one an
# operator sizes a maintenance window against.
#
# ONE HOLDER AND A READER. ref/bpa_model.py is the specification; the table is
# a copy of four numbers out of it. This RUNS the model rather than grepping
# validate.py's output, because the number has to be what the arithmetic says
# and not what a print statement last said it was.
_ref = os.path.join(REPO, "ref")
if _ref not in sys.path:
    sys.path.insert(0, _ref)
from bpa_model import MapPolicy, PoolConfig                       # noqa: E402

#: The model's own fragmented pool, which is the one the document names. Its
#: outside set is three separate ranges - 4 + 2 + 1 - so `outside.count` is 7
#: and not a power of two, which is the whole reason it is the pool quoted.
_INSIDE = ["100.64.0.0/22"]
_OUTSIDE = ["200.150.0.8/30", "45.170.16.64/31", "203.0.113.200/32"]


def _reserve_cost(policy):
    """(capacity, moved, dropped) when that pool is given a reserve of one."""
    p = PoolConfig("fragmented", _INSIDE, _OUTSIDE, block_size=512,
                   policy=policy)
    q = PoolConfig("fragmented", _INSIDE, _OUTSIDE, block_size=512,
                   policy=policy, block_reserve=1)
    moved = dropped = 0
    for sub in range(min(p.capacity, p.inside.count)):
        was = p.forward(p.inside.addr_of(sub))
        if sub >= q.capacity:
            dropped += 1
        elif q.forward(q.inside.addr_of(sub)) != was:
            moved += 1
    return p.capacity, moved, dropped


_cap, _moved, _dropped = _reserve_cost(MapPolicy.BLOCKED)
_row = re.search(
    r"\|\s*`blocked`\s*\|(?P<text>[^|]*)\|", read("docs/architecture.md"))
if not _row:
    bad("docs/architecture.md no longer has a `blocked` row in the reserve "
        "cost table, so this check is comparing against nothing")
else:
    _nums = [int(n) for n in re.findall(r"\b(\d+)\b", _row.group("text"))]
    _want = [_moved, _cap, _dropped]
    if _nums != _want:
        bad(f"docs/architecture.md's blocked reserve row states {_nums} where "
            f"the model gives moved={_moved}, of={_cap}, dropped={_dropped}: "
            f"this is the number an operator sizes a remap window against")
    else:
        ok(f"blocked: {_moved} of {_cap} moved and {_dropped} dropped, "
           f"recomputed from ref/bpa_model.py")
    # The row beside it derives its own count from the pool definition, and
    # the two have to agree about the truncation: a reserve of one takes one
    # block off every public address, so `outside.count` subscribers lose
    # theirs whichever policy is in force.
    _icap, _imoved, _idropped = _reserve_cost(MapPolicy.INTERLEAVED)
    if _idropped != _dropped:
        bad(f"the model drops {_idropped} under interleaved and {_dropped} "
            f"under blocked for one pool and one reserve: the table presents "
            f"them as the same truncation and they are not")
    elif _imoved:
        bad(f"interleaved moved {_imoved} subscriber(s), and the table beside "
            f"this says it moves nobody")
    else:
        ok(f"interleaved: nobody moves and the same {_idropped} are dropped, "
           f"which is the outside set's {_idropped} addresses times a "
           f"reserve of 1")

print("\n=== the release the gate starts at, against the git history ===")
# README.md's state table is what a reader consults to decide whether to trust
# this product, and that row said "every release is built from its tag and
# booted before publishing". It was false about a third of what is published:
# tools/release-gate.sh was added by #100, and 26 of the 74 published releases
# are older than it - v0.2.0 through v0.27.0, every one still downloadable.
#
# The row names the version the gate starts at now, and this reads that
# version back out of the HISTORY. The holder is
# `git tag --contains <the commit that added the gate>`: a boundary somebody
# writes down is a boundary that drifts, and this one already had.
#
# NOT a count of published releases, deliberately: that needs the network and
# `make test` runs offline (#317). What is held is the boundary the sentence
# states against the tags in this repository, which is the half that goes
# wrong by editing a document.
_added = subprocess.run(
    ["git", "log", "--format=%H", "--diff-filter=A", "-1", "--",
     "tools/release-gate.sh"],
    cwd=REPO, capture_output=True, text=True).stdout.strip()
_tags = subprocess.run(["git", "tag", "--contains", _added], cwd=REPO,
                       capture_output=True, text=True).stdout.split() \
    if _added else []
_vers = sorted((t for t in _tags if re.fullmatch(r"v\d+\.\d+\.\d+", t)),
               key=lambda t: [int(p) for p in t[1:].split(".")])
_said = re.search(r"since (v\d+\.\d+\.\d+)\*\* every release is built",
                  open(os.path.join(REPO, "README.md"), encoding="utf-8").read())
if not _added:
    bad("tools/release-gate.sh has no commit that added it, so the version "
        "README.md names cannot be read back from anything")
elif not _vers:
    bad("no tag contains the commit that added tools/release-gate.sh, so "
        "either the history was rewritten or the gate is on no release")
elif not _said:
    bad("README.md no longer says which version the release gate starts at. "
        "It said 'every release' until that was measured and found false "
        "about 26 of the 74 published")
elif _said.group(1) != _vers[0]:
    bad(f"README.md says the gate starts at {_said.group(1)} and the first "
        f"tag containing it is {_vers[0]}: a reader deciding whether to "
        f"trust a downloaded medium is told the wrong boundary")
else:
    ok(f"the gate starts at {_vers[0]}, which is the first tag containing "
       f"the commit that added it")

print("\n=== what the README claims the trail can answer, against the trail ===")
# The state table said "every entry names the operator". The product has a
# shape where there is no operator to name and it says so instead:
#
#   by unattributed via cli   typed at the VPP CLI, where there is no session
#                             to ask
#
# bpa_audit.c's own comment calls that third shape "the one that matters":
# leaving the field off would make such an entry indistinguishable from one
# written before attribution existed, and from one where nobody was
# accountable. So the product's property is STRONGER than the old sentence and
# a different shape - every entry says who asked, and an entry that cannot be
# attributed says so - and the row claiming the weaker-sounding absolute was
# the one thing a reader would rely on when deciding whether the trail can
# answer "who".
#
# HELD FROM THE C, not from a second sentence: if the unattributed path exists
# in the product, the README may not say every entry names the operator. When
# that path goes, this goes red and somebody reads both again.
_audit = open(os.path.join(REPO, "src", "plugins", "bpa", "bpa_audit.c"),
              encoding="utf-8").read()
_readme = open(os.path.join(REPO, "README.md"), encoding="utf-8").read()
_has_unattributed = "by unattributed" in _audit
_claims_every = re.search(r"every entry names the operator", _readme)
if _has_unattributed and _claims_every:
    bad("README.md says the trail's every entry names the operator, and "
        "bpa_audit.c writes 'by unattributed' for a change with no session "
        "to ask: a reader deciding whether the trail can answer 'who' is "
        "told it always can")
elif not _has_unattributed:
    bad("bpa_audit.c no longer writes 'by unattributed', so the shape this "
        "check is about is gone and the README's wording needs reading again")
else:
    ok("the trail has an unattributed shape and the README does not claim "
       "otherwise")

# ---------------------------------------------------------------------------
# The rest of README.md's state table. Two of its ten rows were read and found
# false in one cycle - the appliance row above and the trail row above it - and
# the question that produced the four checks below is what held the other
# eight. Six of them turned out to be held by a lab and by nothing else, which
# is the honest answer and is written up in TESTING.md rather than papered over
# here: `make test` runs offline and cannot say whether a route reached a FIB.
# These four are the ones that can be answered from the tree, and each has one
# owner of the truth and one reader of it.
# ---------------------------------------------------------------------------

print("\n=== every mapping in the pool, or a sample of them ===")
# The BPA row says the core is verified "every mapping in the pool one by one",
# and `every` is the whole of what that row promises: an enumeration and a
# sample read identically in a green run. test/test_bpa_pool.c walks whatever
# the vectors hold and never asks how many there should be - with
# BPA_VEC_N_FWD at zero it prints "0 forward vectors", "0 comparisons" and
# passes, which is this project's defect class exactly.
#
# ONE OWNER AND A READER, and they are not one number computed twice.
# exp_capacity and exp_inside_count are what ref/bpa_model.py says a pool
# HOLDS; BPA_VEC_N_FWD is how many mappings ref/gen_vectors.py actually wrote
# out. A generator that sampled would move the second and leave the first
# where it was.
#
# The header is generated rather than committed, and `make test` builds it
# before any suite runs - the `vectors` target is a prerequisite of `test` in
# the Makefile, and `make vectors` alone does it too. Its absence is a
# FAILURE and not a skip: a check that passes for free on a tree where the
# vectors were never built has said something about nothing.
_VEC = os.path.join(REPO, "test", "bpa_vectors.h")
if not os.path.exists(_VEC):
    bad("test/bpa_vectors.h is not generated, so nothing here compares the "
        "vectors against the pools they were cut from. `make test` generates "
        "it; so does `make vectors` on its own")
else:
    _vec = open(_VEC, encoding="utf-8").read()
    # Read by POSITION, because bpa_vec_pool_t is positional: the numbers
    # after the policy are blocks_per_ip, blocks_mapped, reserve_count,
    # capacity, inside_count, and the ones after that are not needed here.
    _pools = []
    for _ln in _vec.split("\n"):
        _m = re.match(r'\s*\{\s*"([\w-]+)",.*?BPA_POLICY_\w+,\s*([0-9u,\s]+)\}',
                      _ln)
        if _m:
            _n = [int(_x.strip().rstrip("u")) for _x in _m.group(2).split(",")
                  if _x.strip()]
            if len(_n) >= 5:
                _pools.append((_m.group(1), _n[3], _n[4]))
    _m = re.search(r"#define BPA_VEC_N_POOLS (\d+)", _vec)
    _f = re.search(r"#define BPA_VEC_N_FWD (\d+)", _vec)
    if not _m or not _f:
        bad("test/bpa_vectors.h names no pool count or no forward count, so "
            "the generated header is not the shape this check reads")
    elif len(_pools) != int(_m.group(1)):
        # A regex that reads four of nine pools reports a shortfall that is
        # its own, and the message would send the next reader to the
        # generator. Say which it is.
        bad(f"{len(_pools)} pool row(s) were read out of test/bpa_vectors.h "
            f"and it declares {_m.group(1)}: this check's parse is wrong, not "
            f"the vectors")
    else:
        # min(), because a pool whose public side is larger than its private
        # one runs out of subscribers before it runs out of capacity, and the
        # generator enumerates subscribers.
        _want = sum(min(_cap, _ins) for _, _cap, _ins in _pools)
        _got = int(_f.group(1))
        _one_by_one = "every mapping in the pool one by one" in README
        if _got != _want:
            bad(f"the vectors carry {_got} forward mapping(s) where the {len(_pools)} "
                f"pool(s) hold {_want}, so test/test_bpa_pool.c compares a "
                f"SAMPLE against ref/bpa_model.py. README.md's state table "
                f"says every mapping in the pool, one by one, and that is the "
                f"strength of the only row a reader has for the mapping core")
        elif not _one_by_one:
            bad(f"the vectors enumerate all {_got} mapping(s) of the "
                f"{len(_pools)} pool(s) and README.md's state table no longer "
                f"says so: the row describes a weaker product than the one "
                f"this repository has")
        else:
            ok(f"{_got} forward mapping(s), which is every mapping the "
               f"{len(_pools)} pool(s) hold, one by one")

print("\n=== the filesystem the connection records are promised ===")
# "On their own filesystem, so a full log cannot stop the box working" is a
# claim about a box an operator installs, and the installer is what makes it
# true or false. appliance/selftest reports the arrangement on a running box
# and test/test_selftest.sh drives both of its arms, so what an INSTALLED box
# does is held; what the installer WRITES is not, and that is the half the
# README row rests on.
#
# ONE OWNER: the fstab appliance/hexarouting-install writes. Two mount points
# with two different UUID variables is the whole of the claim - one variable
# for both, or no line for the records at all, and the records are on the root
# filesystem with the README still promising otherwise.
_install = read("appliance/hexarouting-install")
# The variable and not its value: the value is a UUID blkid answers with at
# install time, and there is nothing in the tree to compare it against.
_mounts = {}
for _var, _at in re.findall(r'echo "UUID=\$(\w+) (\S+) ', _install):
    _mounts.setdefault(_at, _var)
_root_var = _mounts.get("/")
_rec_var = _mounts.get("/var/log/hexarouting")
_separate = bool(_root_var and _rec_var and _root_var != _rec_var)
_says_own = "own filesystem, so a full log cannot stop the box working" in README
if not _root_var:
    bad("no fstab line mounting / was found in appliance/hexarouting-install, "
        "so this check cannot tell a records volume from the root filesystem "
        "and is comparing nothing")
elif _separate and not _says_own:
    bad("the installer gives the connection records a filesystem of their own "
        "and README.md's state table no longer says so: that row is where a "
        "reader learns a full log cannot stop this box")
elif not _separate and _says_own:
    bad(f"README.md says the connection records are on their own filesystem, "
        f"and appliance/hexarouting-install mounts /var/log/hexarouting from "
        f"{_rec_var or 'nothing'} against / from {_root_var}: a log that fills "
        f"would take the box's own configuration with it")
else:
    ok(f"the installer mounts / from ${_root_var} and "
       f"/var/log/hexarouting from ${_rec_var}, which is the row's claim")

# tools/run-all-labs.sh holds, as data, which labs the release suite does NOT
# run and why. test/test_run_all_labs.sh already reads it, so it is a list
# something has to keep true; the two checks below read the reasons, because
# two rows of the state table are claims about a feature that does not exist
# and that list is where its absence is recorded as machinery.
_RAL = read("tools/run-all-labs.sh")
_nl = re.search(r'NOT_LABS="\n(.*?)\n"', _RAL, re.S)
_excluded = dict(_l.split(":", 1) for _l in _nl.group(1).split("\n") if ":" in _l) \
    if _nl else {}

print("\n=== the feature the state table says this product has not got ===")
# The HA row says NOT implemented, and the acceptance test for HA is written
# and sits outside the release suite for exactly that reason: lab/ha-failover.sh
# is red because the feature is MISSING. The day somebody implements #9 that
# lab joins the suite, and the row a reader consults to decide whether to trust
# this product would still say the feature is absent - which is the way a
# README goes stale in this project, by describing a smaller product than the
# one that now exists (CONTRIBUTING.md section 1).
#
# ONE OWNER: the exclusion, which is machinery rather than a second sentence.
# docs/ha.md says the same thing in prose and is the row's link, but a document
# and a document are two copies with nothing between them.
_ha = _excluded.get("ha-failover", "")
_ha_missing = "MISSING" in _ha
_says_not_impl = "**HA / failover** | **NOT implemented**" in README
if not _excluded:
    bad("no exclusions were read out of tools/run-all-labs.sh's NOT_LABS, so "
        "the two checks below are comparing the README against an empty list")
elif _ha_missing and not _says_not_impl:
    bad("the release suite still excludes lab/ha-failover.sh because the "
        "feature is missing, and README.md's state table no longer says HA is "
        "NOT implemented")
elif _says_not_impl and not _ha_missing:
    bad(f"README.md says HA is NOT implemented and tools/run-all-labs.sh no "
        f"longer excludes lab/ha-failover.sh for a missing feature "
        f"({_ha or 'it is not excluded at all'}): either the acceptance test "
        f"passes now and the row is stale, or the suite is running a lab that "
        f"cannot go green")
else:
    ok("the acceptance test for HA is outside the suite because the feature is "
       "missing, and the state table says so")

print("\n=== the performance figure the state table says nobody has taken ===")
# The Throughput row said "NOT measured - every performance figure is
# untested". The first half is true of bits per second and #28 stays open for
# it. The second half was false for 66 releases: lab/throughput.sh has measured
# what a packet costs the translation nodes in CPU clocks since v0.8.0, it is
# in the release suite - it is not in NOT_LABS - and docs/roadmap.md section
# 3.5 states the number it produced. A reader deciding whether this design can
# reach a target at all was being told the one measurement that answers them
# does not exist.
#
# ONE OWNER: the lab. Not the roadmap's figure, which is a number somebody
# typed, and not the README's own wording. While a lab the release suite runs
# asserts that a performance figure was taken, the state table may not say
# every performance figure is untested.
_thr = os.path.exists(os.path.join(REPO, "lab", "throughput.sh"))
_thr_run = _thr and "throughput" not in _excluded
_thr_asserts = _thr and bool(
    re.search(r'expect "[^"]*was measured', read("lab/throughput.sh")))
_says_none = "every performance figure is untested" in README
if _thr_run and _thr_asserts and _says_none:
    bad("README.md's state table says every performance figure is untested, "
        "and lab/throughput.sh is in the release suite asserting that it "
        "measured the CGNAT's cost per packet. docs/roadmap.md section 3.5 "
        "carries the number. Bits per second is what #28 is open for, and the "
        "row claims far more than that")
elif not _thr_run or not _thr_asserts:
    bad("lab/throughput.sh no longer measures a performance figure in the "
        "release suite, so the one measurement this product has is gone and "
        "README.md's Throughput row needs reading again")
else:
    ok("the per-packet cost is measured by a lab the release suite runs, and "
       "the state table does not claim every performance figure is untested")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
