#!/usr/bin/env python3
"""
Which ALGs ship on, written down in the plugin and in the documentation.

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

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
