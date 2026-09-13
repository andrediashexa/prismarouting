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

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
