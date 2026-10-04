#!/usr/bin/env python3
"""
The RFC 8158 record this box exports, written down in three places.

The same list of information elements exists three times and nothing compared
them:

  src/plugins/bpa/bpa_log.c      the UDP template, by IANA NAME
  src/plugins/bpa/bpa_log_tcp.c  the TCP template, by IANA NUMBER
  docs/logging.md                the table an operator hands their collector

A field added to one and not the others is not a build error and not a test
failure: it is two transports exporting different records, and a document that
describes neither. A collector reads the template off the wire, so it would
decode both streams happily and give two different answers about the same box.

This is the shape that cost four separate defects in one day - the `<- not
checked` marker text quoted in three places after it changed, the BGP page's
table of what the VTY checks, the wiki titles, and `ip_csum_add_even` defined
one way in VPP and the other way in a test shim. A second copy of a truth is
fine; a second copy nothing compares is not.

The documentation is the place where the NAME and the NUMBER meet, so it is
what the two code lists are read against. That keeps the comparison at three
copies rather than introducing a fourth here.
"""
import os
import re
import sys

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    return open(os.path.join(REPO, path), encoding="utf-8").read()


def doc_table(after, before=None):
    """The (number, name, bytes) rows of one markdown table in logging.md.

    `after` is a string that appears immediately before the table, because the
    file has more than one and the wrong one would be compared silently.
    """
    text = read("docs/logging.md")
    start = text.index(after)
    end = text.index(before, start) if before else len(text)
    rows = []
    for line in text[start:end].split("\n"):
        m = re.match(r"^\|\s*(\d+)\s*\|\s*`([A-Za-z0-9]+)`\s*\|\s*(\d+)\s*\|", line)
        if m:
            # The block table states the width in BITS, which is how RFC 8158
            # Table 21 states it; both code lists are in bytes.
            rows.append((int(m.group(1)), m.group(2), int(m.group(3)) // 8))
        m = re.match(r"^\|\s*([A-Za-z0-9]+)\s*\|\s*\**(\d+)\**\s*\|\s*(\d+)\s*\|", line)
        if m:
            rows.append((int(m.group(2)), m.group(1), int(m.group(3))))
    return rows


def c_list(path, name):
    """The `{ a, b }` pairs of one C array, in order."""
    text = read(path)
    m = re.search(re.escape(name) + r"\b[^=]*=\s*\{(.*?)\n\};", text, re.S)
    if not m:
        return None
    return re.findall(r"\{\s*([A-Za-z0-9_]+)\s*,\s*(\d+)\s*\}", m.group(1))


print("=== the block record: three copies, one list ===")
DOC_BLOCK = doc_table("RFC 8158", "A NAT64 subscriber has a template")
check(len(DOC_BLOCK) >= 5,
      f"docs/logging.md no longer has a readable block table: {DOC_BLOCK}. "
      f"Nothing below this is comparing anything")

TCP = c_list("src/plugins/bpa/bpa_log_tcp.c", "bpa_tcp_ies")
UDP = c_list("src/plugins/bpa/bpa_log.c", "bpa_block_elements")
check(TCP is not None, "bpa_tcp_ies is gone from bpa_log_tcp.c")
check(UDP is not None, "bpa_block_elements is gone from bpa_log.c")

if DOC_BLOCK and TCP and UDP:
    want_num = [(n, w) for n, _, w in DOC_BLOCK]
    got_tcp = [(int(a), int(b)) for a, b in TCP]
    check(got_tcp == want_num,
          f"the TCP template and the documented record are different records:\n"
          f"    TCP  {got_tcp}\n    docs {want_num}")
    if got_tcp == want_num:
        ok(f"the TCP template is the {len(want_num)} elements the docs describe, "
           f"in that order and those widths")

    want_name = [(name, w) for _, name, w in DOC_BLOCK]
    got_udp = [(a, int(b)) for a, b in UDP]
    check(got_udp == want_name,
          f"the UDP template and the documented record are different records:\n"
          f"    UDP  {got_udp}\n    docs {want_name}")
    if got_udp == want_name:
        ok("and the UDP template is the same list by name, so the two "
           "transports export the same record")

print("\n=== the NAT64 block, a third template (#485) ===")
# Its own table in the docs, its own template on each transport, and its own
# size in bpa_log.h: the same three-way agreement as the IPv4 block's. A
# NAT64 record that drifted from its table would name the subscriber with a
# prefix length read from the wrong octet.
DOC_N64 = doc_table("A NAT64 subscriber has a template", "boot marker")
TCP6 = c_list("src/plugins/bpa/bpa_log_tcp.c", "bpa_tcp_nat64_ies")
UDP6 = c_list("src/plugins/bpa/bpa_log.c", "bpa_nat64_elements")
check(len(DOC_N64) == 8,
      f"the NAT64 table in docs/logging.md is not eight rows: {DOC_N64}")
check(TCP6 is not None and UDP6 is not None,
      "bpa_tcp_nat64_ies or bpa_nat64_elements is gone")
if len(DOC_N64) == 8 and TCP6 and UDP6:
    check([(int(a), int(b)) for a, b in TCP6] == [(n, w) for n, _, w in DOC_N64],
          f"the TCP NAT64 template is not the documented record: {TCP6}")
    check([(a, int(b)) for a, b in UDP6] == [(nm, w) for _, nm, w in DOC_N64],
          f"the UDP NAT64 template is not the documented record: {UDP6}")
    m6 = re.search(r"The NAT64 block \(#485\): ([0-9 +]+)\.",
                   read("src/plugins/bpa/bpa_log.h"))
    check(m6 is not None and [int(x) for x in m6.group(1).split("+")]
          == [w for _, _, w in DOC_N64],
          "bpa_log.h's NAT64 size is not the documented widths")
    ok("both NAT64 templates and the record size are the documented eight")

print("\n=== the boot marker, which is a record of its own ===")
# Its own table, and its own pair of templates. The marker is what lets a
# collector tell an allocation still in force from one the box died holding
# (#217), so a drift here is a wrong answer about a subscriber rather than an
# untidy one.
DOC_MARK = doc_table("boot marker")
TCPM = c_list("src/plugins/bpa/bpa_log_tcp.c", "bpa_tcp_marker_ies")
UDPM = c_list("src/plugins/bpa/bpa_log.c", "bpa_marker_elements")
check(len(DOC_MARK) == 2,
      f"the marker table in docs/logging.md is not two rows: {DOC_MARK}")
if len(DOC_MARK) == 2 and TCPM and UDPM:
    check([(int(a), int(b)) for a, b in TCPM] == [(n, w) for n, _, w in DOC_MARK],
          f"the TCP marker template is not what the docs describe: {TCPM} "
          f"against {DOC_MARK}")
    check([(a, int(b)) for a, b in UDPM] == [(name, w) for _, name, w in DOC_MARK],
          f"the UDP marker template is not what the docs describe: {UDPM} "
          f"against {DOC_MARK}")
    ok("both marker templates are the two elements the docs describe")

print("\n=== and the record size the plugin allocates for ===")
# bpa_log.h states the record's size as a sum, with the widths written out.
# It is a fourth statement of the same fact and it is arithmetic, so it is
# checkable rather than trusted.
HDR = read("src/plugins/bpa/bpa_log.h")
m = re.search(r"Tabela 21 da RFC 8158: ([0-9 +]+)", HDR)
check(m is not None,
      "bpa_log.h no longer states the record size as a sum of its widths")
if m and DOC_BLOCK:
    stated = [int(x) for x in m.group(1).replace(" ", "").split("+")]
    widths = [w for _, _, w in DOC_BLOCK]
    check(stated == widths,
          f"the size comment in bpa_log.h adds up a different record: "
          f"{stated} against {widths}")
    if stated == widths:
        ok(f"the size comment is the same {len(widths)} widths, summing to "
           f"{sum(widths)}")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
