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
import os
import re
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

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
