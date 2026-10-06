#!/usr/bin/env python3
"""
A CGNAT pool whose sides are prefix-lists (#589): how a list is read as a
set of addresses, what a pool resolves to, what the commit refuses, and how
an entry is moved.

The rule under test is the one decided with the operator on 2026-10-05: a
source matching a `permit` of `prefix-list-inside` is translated, one
matching a `deny` leaves untranslated, one matching nothing is not the
pool's; the public addresses are the outside list's permits less its denies;
a side is given one way or the other, never both. An entry with neither ge
nor le covers every address of its prefix; with either, an address is a /32
and the entry covers its prefix when 32 is in the window.

Each case names the mutation it was shown red against, in the house style.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src",
                                "prismash"))
import config      # noqa: E402
from config import (Config, Pool, prefix_list_entries,  # noqa: E402
                    prefix_entry_covers_addresses, prefix_list_spans,
                    spans_as_prefixes, list_entry_key,
                    split_list_description, with_list_descriptions,
                    number_list_entries, list_moves, renumber_list,
                    prefix_entry_text)

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  {msg}")


ISSUE = """! cgnat
nat
 cgnat
  pool A
   prefix-list-inside IN
   prefix-list-outside OUT
   block-size 512
   exit
  exit
 exit
! routing
ip prefix-list IN seq 5 deny 100.64.1.0/24 description "management range"
ip prefix-list IN seq 10 permit 100.64.0.0/22 le 32
ip prefix-list OUT seq 5 deny 45.237.115.129/32
ip prefix-list OUT seq 10 permit 45.237.115.128/28
"""

print("=== an entry as an address set: the issue's own example (#589) ===")
# Mutation shown red: prefix_entry_covers_addresses() reading FRR's route
# rule (an entry with no ge/le matches only its own length) - the /28 then
# covers no address and OUT resolves to an empty pool.
check(prefix_entry_covers_addresses("45.237.115.128/28", None, None),
      "a bare /28 covers no address: the issue's example would be empty")
check(prefix_entry_covers_addresses("10.0.0.0/8", None, 32),
      "le 32 is every address of the prefix")
check(prefix_entry_covers_addresses("10.0.0.0/8", 24, None),
      "ge alone runs to 32, FRR's rule")
check(not prefix_entry_covers_addresses("10.0.0.0/8", None, 24),
      "le 24 leaves /32 out, and so every address")
check(not prefix_entry_covers_addresses("10.0.0.0/8", 16, 24),
      "ge 16 le 24 leaves /32 out")
ok("a bare entry is its whole prefix; with ge or le, an address is a /32")

print("\n=== entries in FRR's order: seq, a missing seq, any, a repeat ===")
# Mutation shown red: the entries returned in the order typed rather than
# by seq (a deny typed after its permit then wins, which FRR never does).
r = ["ip prefix-list L seq 20 permit 10.0.0.0/8 le 32",
     "ip prefix-list L seq 10 deny 10.1.0.0/16",
     "ip prefix-list L permit any",
     "ip prefix-list L seq 10 deny 10.2.0.0/16",
     "ip prefix-list L description a list",
     "ipv6 prefix-list L seq 5 permit ::/0",
     "ip prefix-list M seq 5 permit 1.0.0.0/8"]
e = prefix_list_entries(r, "L")
check([x[0] for x in e] == [10, 20, 25],
      f"seq order, a seq-less entry numbered as FRR numbers it: {e!r}")
check(e[0][2] == "10.2.0.0/16", f"a seq typed twice is the later: {e[0]!r}")
check(e[2][2:] == ("0.0.0.0/0", None, 32), f"'any' is /0 le 32: {e[2]!r}")
check(prefix_list_entries(r, "NONE") is None, "no such list is None")
check(prefix_list_entries(["ip prefix-list D description x"], "D") is None,
      "a list with only its description has no entry")
check(prefix_list_entries(["ip prefix-list D seq 5 permit 10.256.0.0/16"],
                          "D") is None, "a prefix that is not one is FRR's")
check(len(prefix_list_entries(r, "L", "ipv6")) == 1, "the families apart")
ok("seq order, FRR's numbering, any, the later of two, the family apart")

print("\n=== first match wins; a deny is untranslated; no match is nobody's ===")
# Mutation shown red: `taken` not updated in prefix_list_spans (a later
# permit then re-claims what an earlier deny took, and the management range
# is translated).
permit, deny, cover, inert = prefix_list_spans(
    prefix_list_entries(Config.parse(ISSUE).routing, "IN"))
check(spans_as_prefixes(permit) == ["100.64.0.0/24", "100.64.2.0/23"],
      f"translated: {spans_as_prefixes(permit)!r}")
check(spans_as_prefixes(deny) == ["100.64.1.0/24"],
      f"untranslated: {spans_as_prefixes(deny)!r}")
check(not inert, "no inert entry")
_, d2, _, _ = prefix_list_spans([(5, "permit", "10.0.0.0/8", None, 32),
                                 (10, "deny", "10.1.0.0/16", None, None)])
check(d2 == [], "a deny AFTER a permit that covers it matches nothing")
_, _, _, i2 = prefix_list_spans([(5, "permit", "10.0.0.0/8", None, 24)])
check([x[0] for x in i2] == [5], f"an entry with no address is named: {i2!r}")
check(spans_as_prefixes([(0x0A000000, 0x0A0000FF), (0x0A000100, 0x0A0001FF)])
      == ["10.0.0.0/23"], "adjacent spans are one prefix")
check(spans_as_prefixes([(int.from_bytes(bytes([45, 237, 115, 128]), "big"),
                          int.from_bytes(bytes([45, 237, 115, 128]), "big")),
                         (int.from_bytes(bytes([45, 237, 115, 130]), "big"),
                          int.from_bytes(bytes([45, 237, 115, 143]), "big"))])
      == ["45.237.115.128/32", "45.237.115.130/31", "45.237.115.132/30",
          "45.237.115.136/29"],
      "the fewest prefixes, in address order - bpa_cli.c's decomposition")
# Mutation shown red: _span_minus() keeping the tail past the last taken
# span when it should stop at hi.
check(config._span_minus(0, 9, [(2, 3), (6, 20)]) == [(0, 1), (4, 5)],
      "_span_minus: what the taken spans leave of [0, 9]")
check(config._span_minus(0, 9, []) == [(0, 9)]
      and config._span_minus(5, 6, [(0, 9)]) == [],
      "_span_minus: nothing taken, everything taken")
check(config._span_union([(5, 6), (0, 2), (3, 4), (9, 9)])
      == [(0, 6), (9, 9)], "_span_union: sorted, adjacent ones merged")
ok("first match wins, and the sets come out as the dataplane prints them")

print("\n=== a pool resolved: effective sets, untranslated, excluded ===")
# Mutation shown red: resolve_pool_lists() leaving outside_excluded as the
# deny spans whole (a trailing `deny any` would then 'exclude' the world).
c = Config.parse(ISSUE)
check(c.unknown == [], f"the file reads: {c.unknown!r}")
check(c.resolve_pool_lists() == [], "nothing refused")
p = c.pools["A"]
check(p.eff_inside == ["100.64.0.0/24", "100.64.2.0/23"]
      and p.untranslated == ["100.64.1.0/24"], f"inside: {p.eff_inside!r}")
check(p.eff_outside == ["45.237.115.128/32", "45.237.115.130/31",
                        "45.237.115.132/30", "45.237.115.136/29"]
      and p.outside_excluded == ["45.237.115.129/32"],
      f"outside: {p.eff_outside!r} {p.outside_excluded!r}")
c2 = copy.deepcopy(c)
c2.routing.append("ip prefix-list OUT seq 99 deny 0.0.0.0/0 le 32")
check(c2.resolve_pool_lists() == []
      and c2.pools["A"].outside_excluded == ["45.237.115.129/32"],
      "a trailing deny-all excludes only what a permit gave: "
      f"{c2.pools['A'].outside_excluded!r}")
a = p.api_args()
check(a["prefixes"] == p.eff_inside + p.eff_outside and a["n_inside"] == 2,
      f"the dataplane is handed the effective sets: {a['prefixes']!r}")
check("inside 100.64.0.0/24 inside 100.64.2.0/23" in p.device_command(),
      "and so is the commit check")
la = p.lists_api_args()
check(la == {"name": "A", "inside_list": "IN", "outside_list": "OUT",
             "n_untranslated": 1, "n_prefixes": 2,
             "prefixes": ["100.64.1.0/24", "45.237.115.129/32"]},
      f"lists_api_args: {la!r}")
ok("permits less denies, the denies named, the dataplane's arguments")

print("\n=== the text: the list's name, never what it resolved to ===")
# Mutation shown red: lines() printing the effective prefixes in the whole
# text (the saved file then has both forms on one side and the next load
# refuses it).
t = c.text()
check(" prefix-list-inside IN" in t and " prefix-inside" not in t,
      "the saved file names the list and not its prefixes")
check('seq 5 deny 100.64.1.0/24 description "management range"' in t,
      "and puts the entry's description back on its line")
check(Config.parse(t).text() == t, "and reads back as itself")
check(" prefix-inside 100.64.0.0/24" in "\n".join(p.lines(in_place=False)),
      "the mapping text carries the effective set")
# Mutation shown red: mapping_lines() reading an unresolved file as is (a
# peer's copy then maps to nothing and every pair reads as divergent).
typed = Config.parse(ISSUE.replace("   prefix-list-inside IN\n",
                                   "   prefix-inside 100.64.0.0/24\n"
                                   "   prefix-inside 100.64.2.0/23\n"))
typed.resolve_pool_lists()
check(Config.parse(ISSUE).mapping_lines() == typed.mapping_lines(),
      "a file's lists are resolved for the mapping a peer compares")
ok("the list in the file, the effective set in the mapping")

print("\n=== show | compare shows the pools a list change moves ===")
# Mutation shown red: compare() without effective=True (the diff is the
# routing line alone, and the pool it moves is not on the screen).
c3 = copy.deepcopy(c)
c3.routing = [l.replace("100.64.0.0/22", "100.64.0.0/21") for l in c3.routing]
d = c.compare(c3)
check("+   ! A translates 100.64.4.0/22" in d,
      f"the pool's new addresses are in the diff:\n{d}")
c3.resolve_pool_lists()
check([x.name for x in c.delta_to(c3)["pool_replace"]] == ["A"],
      "and the commit rebuilds it")
c4 = copy.deepcopy(c)
c4.routing = [l.replace("seq 5 deny 100.64.1.0/24",
                        "seq 5 deny 100.64.1.0/25") for l in c4.routing]
c4.resolve_pool_lists()
dd = c.delta_to(c4)
check([x.name for x in dd["pool_replace"]] == ["A"],
      "a narrower deny is a wider inside: rebuilt")
ok("the list line and the pool's resolution both in the diff")

print("\n=== what the commit refuses ===")
# Mutations shown red, one each: the both-sides check removed; the
# missing-list branch returning nothing; the empty-outside check removed;
# the inert-entry refusal removed; the overlap with another pool removed.


def refused(text, extra=()):
    cfg = Config.parse(text)
    cfg.routing += list(extra)
    return [w[0] for w in cfg.resolve_pool_lists()]


both = ISSUE.replace("   prefix-list-inside IN\n",
                     "   prefix-list-inside IN\n   prefix-inside 10.0.0.0/8\n")
check(refused(both) == ["pool 'A': prefix-inside and prefix-list-inside both "
                        "given"], f"both: {refused(both)!r}")
gone = ISSUE.replace("prefix-list-inside IN", "prefix-list-inside NOPE")
check(refused(gone) == ["pool 'A': prefix-list-inside NOPE does not exist"],
      f"missing: {refused(gone)!r}")
check(refused(gone, ["ip prefix-list NOPE description empty"])
      == ["pool 'A': prefix-list-inside NOPE has no entry"],
      "an empty list is refused as one")
check(refused(ISSUE, ["ip prefix-list OUT seq 1 deny 45.237.115.128/28"])
      == ["pool 'A': prefix-list-outside OUT leaves no public address"],
      "an outside deny that empties the pool")
check(refused(ISSUE, ["ip prefix-list IN seq 1 deny 100.64.0.0/22 le 32"])
      == ["pool 'A': prefix-list-inside IN translates nothing"],
      "an inside that translates nothing")
check(refused(ISSUE, ["ip prefix-list IN seq 20 permit 10.0.0.0/8 le 24"])
      == ["pool 'A': prefix-list IN seq 20 permit 10.0.0.0/8 le 24 matches "
          "no address"], "an entry with no address in it, named")
two = ISSUE.replace("  exit\n exit\n! routing", "  pool B\n   prefix-inside "
                    "100.64.1.0/25\n   prefix-outside 192.0.2.0/30\n   "
                    "exit\n  exit\n exit\n! routing")
check(refused(two) == ["pool 'A': prefix-list-inside IN denies "
                       "100.64.1.0/24"],
      f"a deny another pool translates: {refused(two)!r}")
check(refused(two.replace("prefix-outside 192.0.2.0/30",
                          "prefix-outside 192.0.2.0/30\n   vrf 7")
                 .replace("block-size 512", "block-size 512\n   vrf 2"))
      == [], "two pools in two VRFs serve two tenants")
n64 = Pool("X", config.NAT64_LEVEL)
n64.inside_list = "IN"
check(n64.nat64_fits()[0] == "pool 'X': a NAT64 pool takes no prefix-list",
      "a NAT64 pool takes no list")
big = ISSUE + "".join(f"ip prefix-list IN seq {100 + i} permit "
                      f"10.{i // 100}.{i % 100 * 2}.1/32\n" for i in range(260))
check(any("take" in w and "prefixes" in w for w in refused(big)),
      "more prefixes than a message carries")
ok("both, missing, empty, emptied, nothing translated, inert, overlap, "
   "NAT64, too many")

print("\n=== a side taken back to prefixes forgets what its list said ===")
# Mutation shown red: the reset at the top of resolve_pool_lists() removed;
# the untranslated sources of a list no longer named go on being sent.
c5 = copy.deepcopy(c)
c5.pools["A"].inside_list = ""
c5.pools["A"].inside = ["100.64.0.0/22"]
c5.resolve_pool_lists()
check(c5.pools["A"].untranslated == [] and c5.pools["A"].list_inside == [],
      "the untranslated set went with the list")
check("A" in c.delta_to(c5)["lists_set"], "and the dataplane is told")
ok("the derived sets go with the list")

print("\n=== the dataplane's readback: names and facts ===")
# Mutation shown red: the dataplane-mode move of `inside` into list_inside
# taken out (the read-back pool then has both forms on its inside and the
# next commit refuses a box nobody touched).
dp = """cgnat pool A
 inside 100.64.0.0/24
 inside 100.64.2.0/23
 outside 45.237.115.128/32
 block-size 512
 policy interleaved
 mode deterministic
 prefix-list-inside IN
 prefix-list-outside OUT
 untranslated 100.64.1.0/24
 outside-excluded 45.237.115.129/32
 exit
"""
r = Config.parse(dp, dataplane=True)
q = r.pools["A"]
check(r.unknown == [] and q.inside == [] and q.inside_list == "IN"
      and q.eff_inside == ["100.64.0.0/24", "100.64.2.0/23"]
      and q.untranslated == ["100.64.1.0/24"]
      and q.outside_excluded == ["45.237.115.129/32"],
      f"read back: {r.unknown!r} {vars(q)!r}")
f = Config.parse("! cgnat\nnat\n cgnat\n  pool A\n"
                 "   untranslated 100.64.1.0/24\n   exit\n  exit\n exit\n")
check(any("untranslated" in u[1] for u in f.unknown),
      "a file is not allowed to say what a list resolved to")
check(Config._pool_line(Pool("Z"), "prefix-list-inside " + "x" * 64)
      is not None, "a name longer than the dataplane's field is refused")
ok("the readback is the list and its facts; a file may say only the list")

print("\n=== descriptions: off the line FRR gets, kept by the entry ===")
# Mutations shown red: split_list_description() taking the list's own
# `description` (FRR's) for an entry's; with_list_descriptions() keyed by
# the line instead of the entry (a renumbered entry loses its text).
check(list_entry_key("ip prefix-list L seq 10 permit 1.0.0.0/8") == "ip L 10",
      "the key is family, list, seq")
check(list_entry_key("ip prefix-list L permit 1.0.0.0/8") is None
      and list_entry_key("ip prefix-list L description x") is None,
      "an unnumbered entry and a list's description have none")
check(split_list_description("ip prefix-list L description the list")
      == ("ip prefix-list L description the list", None, None),
      "a list's own description is FRR's, left alone")
e, t, w = split_list_description(
    'ip prefix-list L seq 5 deny 10.0.0.0/8 le 32 description "a b"')
check((e, t, w) == ("ip prefix-list L seq 5 deny 10.0.0.0/8 le 32", "a b",
                    None), f"an entry's: {(e, t, w)!r}")
check(split_list_description("ip prefix-list L permit 1.0.0.0/8 "
                             "description x")[2][0]
      == "prefix-list description: the entry has no seq",
      "an unnumbered entry cannot keep one")
check(split_list_description("ip prefix-list L seq 5 permit 1.0.0.0/8 "
                             "description " + "x" * 70)[2] is not None,
      "and a description is bounded")
check(with_list_descriptions(["ip prefix-list L seq 5 permit 1.0.0.0/8",
                              "ip route 0.0.0.0/0 1.1.1.1"],
                             {"ip L 5": "x y"})
      == ['ip prefix-list L seq 5 permit 1.0.0.0/8 description "x y"',
          "ip route 0.0.0.0/0 1.1.1.1"], "put back on its entry only")
ok("an entry's text off FRR's line and back on the box's")

print("\n=== insert and resequence (Junos) ===")
# Mutations shown red: list_moves() placing `after` as `before`; the
# renumber fallback taken out (a move with no room between collides).
check(number_list_entries(["ip prefix-list L seq 7 permit 1.0.0.0/8",
                           "ip prefix-list L permit 2.0.0.0/8"], "L")
      == ["ip prefix-list L seq 7 permit 1.0.0.0/8",
          "ip prefix-list L seq 10 permit 2.0.0.0/8"],
      "every entry numbered as FRR would")
check(list_moves([5, 10, 20], 20, "before", 10) == {20: 7},
      "a free seq between the new neighbours: only the mover moves")
check(list_moves([5, 10, 20], 5, "after", 20) == {5: 25},
      "after the last: the next multiple of 5")
check(list_moves([5, 6, 7], 7, "before", 6) == {5: 10, 7: 20, 6: 30},
      "no room: the list renumbered by 10 in its new order")
check(list_moves([5, 10, 20], 20, "after", 5) == {20: 7}, "after, between")
check(list_moves([5, 10], 1, "after", 5) == "there is no entry seq 1"
      and list_moves([5, 10], 5, "before", 5)
      == "an entry cannot move relative to itself", "what cannot move")
rt, ds = renumber_list(["ip prefix-list L seq 5 permit 1.0.0.0/8",
                        "ip prefix-list L seq 6 deny 2.0.0.0/8",
                        "ip prefix-list M seq 5 permit 3.0.0.0/8"],
                       {"ip L 6": "two", "ip M 5": "other"}, "L",
                       {5: 20, 6: 10})
check(rt == ["ip prefix-list L seq 20 permit 1.0.0.0/8",
             "ip prefix-list L seq 10 deny 2.0.0.0/8",
             "ip prefix-list M seq 5 permit 3.0.0.0/8"]
      and ds == {"ip L 10": "two", "ip M 5": "other"},
      f"renumbered at once, the text with its entry: {rt!r} {ds!r}")
check(prefix_entry_text((5, "deny", "1.0.0.0/8", 9, 24))
      == "seq 5 deny 1.0.0.0/8 ge 9 le 24", "an entry as typed")
ok("insert moves one entry, resequence numbers by 10, texts follow")

print("\n=== through the API: the two names as typed fields ===")
# Mutation shown red: pool_lines() without the prefix-list branch (the field
# falls to the numeric `else` and writes `prefix_list_inside 0`).
import typedcfg  # noqa: E402
cur = typedcfg.pool_typed(Config.parse(ISSUE).pools["A"])
check(cur["prefix_list_inside"] == "IN" and cur["prefix_list_outside"] == "OUT",
      f"pool_typed carries the names: {cur!r}")
want = dict(cur, prefix_list_inside="", prefix_inside=["100.64.0.0/22"],
            prefix_list_outside="OTHER")
check(typedcfg.pool_lines(cur, want) == ["prefix-inside 100.64.0.0/22",
                                         "no prefix-list-inside",
                                         "prefix-list-outside OTHER"],
      f"pool_lines: {typedcfg.pool_lines(cur, want)!r}")
check("prefix_list_inside" not in typedcfg.nat64_pool_typed(Pool("N")),
      "a NAT64 pool has no field for a list")
ok("read and written as fields, a NAT64 pool without them")

print(f"\n{fail} FAILURES" if fail else "\nALL TESTS PASSED")
sys.exit(1 if fail else 0)
