#!/usr/bin/env python3
"""
docs/clients.md's contract hashes, held to the proto each release tagged.

prisma-web compares its vendored prisma.proto against the sha256 each entry
names. The 0.149.19 entry named 0.149.17's (`b08a5a08...`) when the proto had
changed in 0.149.18 - prisma-web found it by hashing the file at the tag. So
every entry names one, in full or as `<8+ hex>...<4+ hex>`, and it is the
hash of api/prisma/v1/prisma.proto at that entry's tag; an entry whose tag
does not exist yet (the release being prepared) is held to the file in the
tree, and only the newest entry may be in that state.
"""
import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DOC = os.path.join(ROOT, "docs", "clients.md")
PROTO = os.path.join(ROOT, "api", "prisma", "v1", "prisma.proto")
fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


def entries(text):
    """[(version, the entry's Contract bullet)] in the order the file has them."""
    out = []
    for e in re.split(r"\n(?=## )", text):
        m = re.match(r"## (\d+\.\d+\.\d+) ", e)
        if not m:
            continue
        c = re.search(r"\n\* \*\*Contract:\*\*(.*?)(?=\n\* |\n\n|\Z)", e, re.S)
        out.append((m.group(1), c.group(1) if c else ""))
    return out


def named(bullet):
    """The prisma.proto sha256 a Contract bullet names, full or abbreviated."""
    m = re.search(r"sha256\s+`([0-9a-f]{8,64}(?:\.\.\.[0-9a-f]{4,})?)`", bullet)
    return m.group(1) if m else None


def matches(claim, real):
    if "..." in claim:
        head, tail = claim.split("...")
        return real.startswith(head) and real.endswith(tail)
    return claim == real


def at_tag(version):
    """The proto's sha256 at tag v<version>, or None when there is no such tag."""
    try:
        data = subprocess.check_output(
            ["git", "-C", ROOT, "show", f"v{version}:api/prisma/v1/prisma.proto"],
            stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, OSError):
        return None
    return hashlib.sha256(data).hexdigest()


ents = entries(open(DOC).read())
check(len(ents) > 10, f"{len(ents)} release entries read")
check(matches("8fdfa497...50ed5d", "8fdfa4978201d4fa54fe5d0b8b368bc3ae95d6ca163d23c2bb0ac303c550ed5d")
      and not matches("b08a5a08...0c3e7a", "8fdfa4978201d4fa54fe5d0b8b368bc3ae95d6ca163d23c2bb0ac303c550ed5d"),
      "matches: an abbreviated hash by its head and tail")
check(named(" unchanged (`prisma.proto` sha256 `269fb5c9...106f`).") == "269fb5c9...106f"
      and named(" unchanged.") is None, "named: the hash in a Contract bullet")
tree = hashlib.sha256(open(PROTO, "rb").read()).hexdigest()
untagged = 0
for i, (v, bullet) in enumerate(ents):
    claim = named(bullet)
    if claim is None:
        check(False, f"{v}: the Contract bullet names no prisma.proto sha256")
        continue
    real = at_tag(v)
    if real is None:
        untagged += 1
        check(i == 0 and matches(claim, tree),
              f"{v}: not tagged yet, so the newest entry, and the tree's proto ({tree[:8]})")
    else:
        check(matches(claim, real), f"{v}: {claim} is the proto at v{v} ({real[:8]}...)")
check(untagged <= 1, "at most one entry is ahead of the tags")

if fail:
    print(f"\n{fail} FAILURES")
    sys.exit(1)
print("\nALL TESTS PASSED")
