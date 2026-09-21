#!/usr/bin/env python3
"""
The candidate configuration model: which changes recreate a pool.

`Config.delta_to` decides, for every field of a pool, whether changing it
rebuilds the pool on the dataplane or is applied in place. That decision is
not a detail of the diff - it is the difference between a setting that reaches
the box and a setting that reaches the box after disconnecting every
subscriber on the pool (#69), and between an operator being warned that a
change re-maps subscribers and being surprised by it (#23).

Both directions are wrong in their own way:

  - a mapping field classified as in-place is sent to a dataplane that cannot
    apply it that way, and the pool keeps its old mapping while the running
    configuration says otherwise;
  - an in-place field classified as mapping drops every session on the pool to
    turn one ALG off.

The table below states the classification for every field a pool has, and the
last case asserts that the TABLE IS COMPLETE - a field added later and not
classified here fails, rather than silently defaulting to whatever the diff
happens to do with it.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "prismash"))
import config      # noqa: E402
import system      # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def base_pool(name="p"):
    p = config.Pool(name)
    p.inside = ["100.64.0.0/22"]
    p.outside = ["200.150.0.16/30"]
    return p


def cfg_with(pool):
    c = config.Config()
    c.pools[pool.name] = pool
    return c


# name -> (how to change it, which delta bucket must report it)
#
# "pool_replace" means every session on the pool ends. Anything else means the
# pool keeps carrying traffic while the setting changes underneath it.
MUTATIONS = {
    # --- the mapping. Changing any of these changes which public address and
    # --- port block a subscriber gets, so the pool is rebuilt.
    "inside":       (lambda p: p.inside.append("100.64.4.0/22"), "pool_replace"),
    "outside":      (lambda p: p.outside.append("45.170.16.0/28"), "pool_replace"),
    "block_size":   (lambda p: setattr(p, "block_size", "1024"), "pool_replace"),
    "ports":        (lambda p: setattr(p, "ports", "2048-65535"), "pool_replace"),
    "policy":       (lambda p: setattr(p, "policy", "blocked"), "pool_replace"),
    "mode":         (lambda p: setattr(p, "mode", "dynamic"), "pool_replace"),
    # A pool that starts serving one VRF stops serving the others, so the set
    # of subscribers it maps changes even though the arithmetic does not.
    "vrf":          (lambda p: setattr(p, "vrf", "1"), "pool_replace"),
    "outside_vrf":  (lambda p: setattr(p, "outside_vrf", "2"), "pool_replace"),
    # A reserve is taken OUT of capacity, and capacity is the mapping: under
    # the blocked policy blocks_mapped is the divisor, so every subscriber
    # past the first public address moves, and under every policy the
    # subscribers above the new capacity lose their block entirely. The
    # opposite classification from block_stride below, and for the opposite
    # reason - this one decides how many blocks the FORMULA may use, not where
    # the next extension goes (issue #39).
    "block_reserve": (lambda p: setattr(p, "block_reserve", 2),
                      "pool_replace"),

    # --- settings the dataplane changes under a worker barrier, in place.
    "timeouts":     (lambda p: p.timeouts.update({"udp": 900}), "settings_set"),
    "max_blocks":   (lambda p: setattr(p, "max_blocks", 2), "settings_set"),
    # In place, and the reason is the whole argument for it being a setting:
    # a stride says where the NEXT extension goes. Nothing already handed out
    # moves, and bpa_forward() never reads it, so no subscriber's own block
    # changes. If it ever rebuilt the pool it would be a mapping change and
    # would belong to the capacity-change procedure instead.
    "block_stride": (lambda p: setattr(p, "block_stride", 1), "settings_set"),
    "tcp_mss":      (lambda p: setattr(p, "tcp_mss", "1400"), "settings_set"),
    "util_alarm":   (lambda p: setattr(p, "util_alarm", 80), "settings_set"),
    "filter":       (lambda p: setattr(p, "filter", "address-dependent"),
                     "settings_set"),
    "algs":         (lambda p: p.algs.update({"rtsp": False}), "settings_set"),

    "max_sessions": (lambda p: setattr(p, "max_sessions", 2000), "limits_set"),
    "max_rate":     (lambda p: setattr(p, "max_rate", 50), "limits_set"),

    "bypass_src":   (lambda p: p.bypass_src.append("100.64.1.0/24"),
                     "bypass_set"),
    "bypass_dst":   (lambda p: p.bypass_dst.append("10.0.0.0/8"), "bypass_set"),

    "forwards":     (lambda p: p.forwards.append(("tcp", 8080, "100.64.0.5",
                                                  80)), "fwd_add"),
}

# Fields that are not settings at all: the pool's identity, and the tables the
# mutations above reach through.
NOT_A_SETTING = {"name"}


print("=== which changes rebuild a pool, and which do not ===")
for field, (mutate, bucket) in sorted(MUTATIONS.items()):
    before = cfg_with(base_pool())
    after = cfg_with(base_pool())
    mutate(after.pools["p"])

    d = before.delta_to(after)
    replaced = [p.name for p in d["pool_replace"]]

    if bucket == "pool_replace":
        check(replaced == ["p"],
              f"changing {field} must rebuild the pool, got replace={replaced}")
    else:
        check(replaced == [],
              f"changing {field} must NOT rebuild the pool "
              f"(every session on it would end), got replace={replaced}")
        check(bool(d[bucket]),
              f"changing {field} must be reported in {bucket}, "
              f"which was empty")

    # The SAME classification, asked of the fingerprint two HA peers compare
    # (#8). It is asserted here, in the loop that owns the table, and nowhere
    # else, precisely so that there is no second list of "fields the peers
    # must agree on": a field added to Pool and forgotten fails the
    # completeness case below, and a field classified as mapping that the
    # fingerprint does not see fails right here.
    #
    # The consequence of getting it wrong is the one docs/ha.md names: in
    # deterministic mode a subscriber's public address and port block are a
    # function of the pool definition, so a fingerprint that ignores a mapping
    # field reports two boxes as identical while they hand the same subscriber
    # different addresses, and neither box logs anything.
    moved = (before.mapping_fingerprint() != after.mapping_fingerprint())
    if bucket == "pool_replace":
        check(moved,
              f"changing {field} re-maps subscribers, so the HA fingerprint "
              "must move with it; it did not")
    else:
        check(not moved,
              f"changing {field} moves no subscriber, so the HA fingerprint "
              "must NOT move; it did, which would report a correctly "
              "configured pair as divergent")
    # And the comparison itself, which is what an operator is shown. Same
    # direction, so a fingerprint that moved for the wrong reason cannot pass
    # by moving without the diff being able to say which pool.
    named = [n for n, _, _ in before.mapping_diff_to(after)]
    check(named == (["p"] if bucket == "pool_replace" else []),
          f"changing {field}: mapping_diff_to named {named}")

print("  every field classified above behaves as classified")
print("  and the HA fingerprint moves for exactly the mapping fields")

print("\n=== the table covers every field a pool has ===")
# The assertion that survives the next feature. Without it, a field added to
# Pool and forgotten here is classified by accident: whatever lines() happens
# to do with it decides whether an operator loses their subscribers.
have = {k for k in vars(base_pool()) if not k.startswith("_")}
classified = set(MUTATIONS) | NOT_A_SETTING
missing = sorted(have - classified)
extra = sorted(classified - have - NOT_A_SETTING)
check(not missing,
      f"these pool fields are not classified as mapping or in-place: {missing}")
check(not extra, f"these entries name fields a pool no longer has: {extra}")
print(f"  {len(have)} fields, {len(missing)} unclassified")

print("\n=== a change that changes nothing asks for nothing ===")
# A diff that reports work for an unchanged configuration is a commit that
# rebuilds pools for no reason, and an operator who learns to ignore the
# warning that says so.
same = cfg_with(base_pool())
d = same.delta_to(cfg_with(base_pool()))
busy = {k: v for k, v in d.items() if v}
check(not busy, f"an identical configuration must ask for nothing: {busy}")
print(f"  buckets reporting work: {len(busy)}")

print("\n=== a new pool is added, not replaced ===")
d = config.Config().delta_to(cfg_with(base_pool()))
check([p.name for p in d["pool_add"]] == ["p"], "a new pool must be added")
check(d["pool_replace"] == [], "and not also replaced")
# A pool being created carries its own settings, so a separate call would be
# a second write of what the first one already said.
check(not d["settings_set"],
      f"a new pool needs no separate settings call: {d['settings_set']}")
check(not d["limits_set"],
      f"nor a separate limits call: {d['limits_set']}")
print("  add=1, replace=0, no follow-up calls")

print("\n=== a pool being rebuilt re-sends what it loses ===")
# The other side of the rule above: a pool that is recreated arrives empty of
# forwards, so every forward the candidate asks for is an addition. Treating
# it as unchanged leaves the operator's inbound ports silently gone.
before = cfg_with(base_pool())
before.pools["p"].forwards.append(("tcp", 8080, "100.64.0.5", 80))
after = copy.deepcopy(before)
after.pools["p"].block_size = "1024"
d = before.delta_to(after)
check([p.name for p in d["pool_replace"]] == ["p"], "the pool is rebuilt")
check([f for _, f in d["fwd_add"]] == [("tcp", 8080, "100.64.0.5", 80)],
      f"and its forwards are re-sent: {d['fwd_add']}")
print(f"  forwards re-sent: {len(d['fwd_add'])}")

print("\n=== removing a pool is a removal, not a rebuild ===")
d = cfg_with(base_pool()).delta_to(config.Config())
check(d["pool_remove"] == ["p"], "the pool must be removed")
check(d["pool_replace"] == [], "and not replaced")

print("\n=== lines(in_place=False) is the mapping and only the mapping ===")
# What delta_to compares. Anything that leaks into it rebuilds the pool.
rich = base_pool()
for field, (mutate, bucket) in MUTATIONS.items():
    if bucket != "pool_replace":
        mutate(rich)
check(rich.lines(in_place=False) == base_pool().lines(in_place=False),
      "no in-place setting may appear in the mapping half of a pool:\n"
      f"    {rich.lines(in_place=False)}\n"
      f"    {base_pool().lines(in_place=False)}")
check(len(rich.lines()) > len(rich.lines(in_place=False)),
      "and the in-place settings must appear in the full text")
print(f"  mapping lines: {len(rich.lines(in_place=False))}, "
      f"full: {len(rich.lines())}")

print("\n=== the API arguments a pool produces ===")
p = base_pool()
p.timeouts["udp"] = 900
p.algs["rtsp"] = False
p.tcp_mss = "auto"
a = p.settings_api_args()
check(a["timeouts"]["udp"] == 900, f"the timeout must be sent: {a['timeouts']}")
check(a["timeouts"]["icmp"] == 0,
      "a timeout the operator did not set must be sent as zero, which is what "
      "the device means by inherit")
# Off and unset are different: the device cannot tell "the operator turned
# RTSP off" from "the operator said nothing" without the second field.
check(a["alg"] & config.Pool.ALG_BITS["rtsp"] == 0, "rtsp must be off")
check(a["alg_set"] & config.Pool.ALG_BITS["rtsp"] != 0,
      "and must be marked as chosen rather than defaulted")
check(a["alg"] & config.Pool.ALG_BITS["ftp"] != 0, "ftp stays on by default")
check(a["alg_set"] & config.Pool.ALG_BITS["ftp"] == 0,
      "and is not marked as chosen")
check(a["tcp_mss"] == 0 and a["tcp_mss_auto"] is True,
      f"an automatic clamp sends no number: {a['tcp_mss']}")

p.tcp_mss = "1400"
a = p.settings_api_args()
check(a["tcp_mss"] == 1400 and a["tcp_mss_auto"] is False,
      f"an explicit clamp sends the number: {a}")

b = p.bypass_api_args(is_dst=True)
check(b["is_destination"] is True and b["n_prefixes"] == len(b["prefixes"]),
      f"the count must match the list it describes: {b}")

f = p.forward_api_args(("udp", 5060, "100.64.0.9", 5060), is_add=False)
check(f["is_add"] is False and f["proto"] == "BPA_API_FWD_UDP",
      f"a forward removal must say so: {f}")

print("\n=== the MSS clamp is refused at the prompt, with the reason ===")
# Mirrors BPA_MSS_MIN/MAX in bpa_mss.h, which test_bpa_mss.c pins from the
# other side. Both have to move together or the VTY accepts what the dataplane
# refuses.
check(config.Pool.check_mss("1400") is None, "1400 is a legal clamp")
check(config.Pool.check_mss("auto") is None, "auto is legal")
check(config.Pool.check_mss("535") is not None, "535 is below the RFC floor")
check(config.Pool.check_mss("9001") is not None, "9001 is above a jumbo frame")
check(config.Pool.check_mss("wat") is not None, "a word is not a size")
check("RFC 1122" in (config.Pool.check_mss("14") or ""),
      "and the refusal must say which floor it is, not just 'invalid'")
check((config.Pool.MSS_MIN, config.Pool.MSS_MAX) == (536, 9000),
      "the bounds must match bpa_mss.h")

print("\n=== the configuration the installer writes must parse ===")
#
# install.sh writes a default prisma-routing.conf. prisma-routing.service loads it at
# boot with "prismash --load", and prismash REFUSES a file it does not fully
# understand - deliberately, because applying a file minus the lines it could
# not read, on a box nobody is watching, is worse.
#
# So the banner the installer writes and the parser that reads it have to
# agree, and they did not: "!" was not recognised as a comment, the load
# refused, prismash exited non-zero, and prisma-routing.service FAILED on the first
# boot of every install (#94).
#
# The default is read out of install.sh rather than copied here, so that
# changing one side without the other fails this test instead of a box.
#
# It used to be a `printf` in the installer and this read the format string.
# It is a file in the tree now, because the installer writes three files it
# will never overwrite again and the manifest has to know which three (#302):
# keep() is the one place a file the operator owns may be written, and what
# keep() writes is a file. So what is read out of install.sh is now WHICH file
# it installs, and the content comes from that - which is the same property
# this comment claimed, with one more hop and no second copy.
import re  # noqa: E402
installer = os.path.join(os.path.dirname(__file__), "..", "appliance",
                         "install.sh")
m = re.search(r'keep\s+\S+\s+"\$REPO/([^"]+)"\s*\\?\s*'
              r'"\$DESTDIR/etc/prisma-routing/prisma-routing\.conf"',
              open(installer).read())
check(m is not None,
      "could not find the default configuration in appliance/install.sh; "
      "if it moved, this test no longer checks anything")
if m:
    default = open(os.path.join(os.path.dirname(__file__), "..",
                                m.group(1)), encoding="utf-8").read()
    print("  what install.sh writes:")
    for line in default.splitlines():
        print(f"    {line!r}")
    parsed = config.Config.parse(default)
    check(parsed.unknown == [],
          f"the installer's own default file does not parse: {parsed.unknown}")
    check(parsed.pools == {},
          f"a file of comments must produce no configuration: {parsed.pools}")

print("\n=== '!' is a comment, except where it is a section header ===")
c = config.Config.parse("""!
! a comment
cgnat pool p
 inside 100.64.0.0/22
 outside 200.150.0.16/30
 exit
!
! routing
ip route 0.0.0.0/0 192.0.2.1
!
""")
check(c.unknown == [], f"comments must not be unknown statements: {c.unknown}")
check("p" in c.pools, "the pool between the comments is still read")
# FRR uses "!" as a separator and is handed its section verbatim, so those
# must survive - the comment rule applies to the CGNAT half only.
check("!" in c.routing,
      f"routing keeps its own '!' lines, which FRR needs: {c.routing}")
check("ip route 0.0.0.0/0 192.0.2.1" in c.routing,
      f"the routing statement is preserved: {c.routing}")
print(f"  routing section: {c.routing}")

# --- an empty file is not a configuration that removes everything ---
#
# This is the text appliance/install.sh writes, byte for byte. Read as a
# complete configuration it says "no SSH access", and the box it is read on has
# SSH access the installer just set up - so the lockout check refused it and
# prisma-routing.service failed on the first boot of every install (#121).
print("\n=== the installer's own default configuration ===")
INSTALLED_DEFAULT = "!\n! Prisma Routing\n!\n"
check(config.Config.parse(INSTALLED_DEFAULT).is_empty(),
      "the file install.sh writes holds no statements")
check(config.Config.parse("").is_empty(), "and neither does an empty file")
check(config.Config.parse("\n\n   \n").is_empty(), "nor blank lines")

# The distinction that matters, and the one an is_empty() written against the
# fields gets wrong: ssh and snmp start as copies of their defaults, so a file
# setting one of them to its default value looks identical to a file that said
# nothing. It is not nothing - it is a statement, and it MUST be checked like
# any other.
check(not config.Config.parse("ssh password-authentication yes\n").is_empty(),
      "a statement whose value happens to be the default is still a statement")
check(not config.Config.parse("cgnat logging enable\n").is_empty(),
      "an ordinary statement is not empty")
check(not config.Config.parse("! routing\nrouter bgp 65000\n").is_empty(),
      "a routing section alone is not empty")


# ---------------------------------------------------------------------------
# The hierarchies moved, and every box in service is written in the old ones
# (#193).
#
# CGNAT is under `nat` now and static routes are under `router`. Neither
# hierarchy exists in the tool underneath - VPP's plugin knows `cgnat`, FRR has
# no `router static` at all - so both are translated. The property that matters
# is not that the new spelling works; it is that the OLD one still does, in
# both directions, because prisma-routing.service replays a saved file at every
# boot on a machine nobody is watching, and every rollback revision on a box
# already in service is written the old way.

OLD_FILE = """! cgnat
cgnat pool residential
 inside 100.64.0.0/22
 outside 203.0.113.0/24
 block-size 512
 policy interleaved
 mode deterministic
 exit
cgnat interface pg0 inside
! routing
router bgp 65000
 bgp router-id 10.0.0.1
ip route 0.0.0.0/0 10.0.0.2
ipv6 route 2001:db8::/32 fe80::1
vrf red
 ip route 10.0.0.0/8 192.0.2.1
 exit-vrf
"""

old = config.Config.parse(OLD_FILE)
check(not old.unknown, f"a file in the old hierarchy still loads: {old.unknown}")
check(list(old.pools) == ["residential"] and old.interfaces == {"pg0": "inside"},
      "with its CGNAT intact")
check("ip route 0.0.0.0/0 10.0.0.2" in old.routing,
      f"and its static routes: {old.routing}")

# Written back out, it comes out in the NEW hierarchy...
new_text = old.text()
check("nat cgnat pool residential" in new_text,
      f"CGNAT is written under nat: {new_text}")
check("router static" in new_text and " route 0.0.0.0/0 10.0.0.2" in new_text,
      f"and static routes as a block under router: {new_text}")
check("\nip route " not in new_text,
      "the flat spelling is not written twice")

# ...and reading that back gives the same configuration. This is the assertion
# that would fail if the translation were one-way: the box would save a file it
# could not read at the next boot.
back = config.Config.parse(new_text)
check(not back.unknown, f"the new hierarchy loads: {back.unknown}")
check(back.is_same(old), "an old file and its rewritten form are one configuration")
check(back.routing == old.routing,
      f"including the routing lines FRR is handed: {back.routing}")

# A VRF's static routes live INSIDE its `vrf` block, indented. Hoisting one of
# those into `router static` would move the route to another table - a display
# transform that changes where packets go - so the column is read as well as
# the statement.
check(" ip route 10.0.0.0/8 192.0.2.1" in new_text.splitlines(),
      f"a vrf's static route stays in its vrf: {new_text}")
# The same route typed at the VTY, where it is NOT indented: `Shell.feed`
# strips leading whitespace before dispatch, so a route typed inside `vrf red`
# reaches `routing` at column 0 like every other typed line. Reading the column
# to decide what is top-level therefore hoisted it out of the VRF and into
# `router static`, which is another routing table - the file said the route was
# global and the operator had put it in a VRF (#180). Which block a statement
# is in is decided by `block_paths`, which reads the typed shape as well.
typed = config.Config()
typed.routing = ["vrf red", "ip route 10.0.0.0/8 192.0.2.1", "exit-vrf",
                 "ip route 0.0.0.0/0 10.0.0.2"]
lines = config.group_static(typed.routing)
# By position, and without assuming the route is there at all: hoisting it is
# exactly the defect, and a lookup that raises reports the wrong thing.
in_vrf = "ip route 10.0.0.0/8 192.0.2.1" in lines
check(in_vrf and lines.index("vrf red") < lines.index("ip route 10.0.0.0/8 192.0.2.1"),
      f"a route typed inside a vrf stays inside it: {lines}")
check(" route 0.0.0.0/0 10.0.0.2" in lines,
      f"while the one typed outside is still grouped: {lines}")
check(" route 10.0.0.0/8 192.0.2.1" not in lines,
      f"and the vrf's is not grouped with it: {lines}")
check(back.routing.index("vrf red") < back.routing.index(" ip route 10.0.0.0/8 192.0.2.1"),
      "and is still inside it after a round trip")

# The same route with the separator FRR 10.7.1 actually writes between the
# statements of a stanza: an INDENTED `!`. `block_paths` read any `!` as the
# top-level separator and cleared the block stack, so everything after this one
# looked top-level - and this route was hoisted out of `vrf red` into `router
# static`, which is the other routing table and is the whole defect #180 was
# about, reintroduced by a line the operator never typed.
#
# Written with the ` !` because that is what came back off a live daemon. The
# case above has no separator and was green against this the entire time.
bang = config.group_static(["vrf red", " !", " ip route 10.0.0.0/8 192.0.2.1",
                            "exit-vrf"])
check("router static" not in bang,
      f"an indented '!' does not hoist a vrf's route out of it: {bang}")
check(" ip route 10.0.0.0/8 192.0.2.1" in bang,
      f"and the route is left exactly where FRR put it: {bang}")

# The third thing that `!` broke, and the one no lab holds: `show this` inside
# `router bgp`, on a configuration READ BACK from FRR rather than typed.
#
# frr_block_lines() promises the address-families are part of the answer - an
# operator standing in `router bgp 65001` is asking what that router holds. FRR
# 10.7.1 writes ` !` between the router's own statements and its first
# address-family, and with the stack cleared there the family and everything
# inside it sat at no path at all, so this function filtered the whole lot out
# and `show this` answered with the router's bare statements. Not a wrong
# answer an operator could see was wrong: a SHORTER one, with no marker saying
# anything was missing.
#
# lab/vty.sh asserts the address-family is there, but mid-edit, where the
# candidate is flat and carries no `!` - so it was green throughout. Its
# after-commit case reads `router static`, which is top-level and cannot show
# this. Hence an assertion here, on the read-back shape.
READBACK = ["router bgp 65000",
            " bgp router-id 10.0.0.1",
            " !",
            " address-family ipv4 unicast",
            "  maximum-paths 4",
            " exit-address-family",
            "exit"]
held = config.frr_block_lines(READBACK, ["router bgp 65000"])
check(" address-family ipv4 unicast" in held,
      f"show this in router bgp keeps the address-family FRR read back: {held}")
check("  maximum-paths 4" in held,
      f"and what the family holds, indented under it: {held}")

# The family comes from the prefix, not from a keyword: a prefix with a colon
# in it is IPv6 and nothing else, and asking for it twice is asking the
# operator to contradict themselves.
v6 = config.Config.parse("! routing\nrouter static\n route 2001:db8::/32 fe80::1\n exit\n")
check(v6.routing == ["ipv6 route 2001:db8::/32 fe80::1"],
      f"an IPv6 prefix becomes an ipv6 route: {v6.routing}")

# Both spellings in ONE file - which is what a hand-edited file looks like
# during a migration, and what a rollback revision written either side of an
# upgrade looks like.
mixed = config.Config.parse("""! cgnat
nat cgnat interface pg0 inside
cgnat interface pg1 outside
! routing
router static
 route 0.0.0.0/0 10.0.0.2
 exit
ip route 198.51.100.0/24 blackhole
""")
check(not mixed.unknown, f"a file in both hierarchies loads: {mixed.unknown}")
check(mixed.interfaces == {"pg0": "inside", "pg1": "outside"},
      f"both CGNAT spellings: {mixed.interfaces}")
check(mixed.routing == ["ip route 0.0.0.0/0 10.0.0.2",
                        "ip route 198.51.100.0/24 blackhole"],
      f"both static-route spellings: {mixed.routing}")


# ---------------------------------------------------------------------------
# The order a saved configuration is written in (#209).
#
# `cgnat logging enable` is refused by the dataplane while the IPFIX exporter
# has no collector, and alphabetical order puts it BEFORE the statement that
# gives it one. A file written that way refuses its own second line when it is
# reloaded - on the boot path, which is the one nobody types by hand and the
# one an appliance takes every time it starts.
print("\n=== the export state is written after the exporter it needs ===")

cfg = config.Config()
cfg.globals = ["cgnat logging enable",
               "cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739",
               "cgnat block-holddown 11"]
# The prefix is the hierarchy's, and it is on every CGNAT statement this
# renderer writes since #356 - pools, attachments and these. The needle
# carries it rather than being loosened to match either spelling: a file with
# one group prefixed and another not is exactly what that issue was.
written = [l for l in cfg.text().splitlines()
           if l.startswith("nat cgnat logging")]
check(written == ["nat cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739",
                  "nat cgnat logging enable"],
      f"the exporter comes first however the statements were typed: {written}")

# The rest stays alphabetical, so two boxes holding the same configuration
# write the same file and a diff of two saved files is a diff of the settings.
allg = [l for l in cfg.text().splitlines() if l.startswith("nat cgnat ")]
# Indexed only after it is known to have something in it. `allg[0]` on an
# empty list is an IndexError, which kills the suite where a red check would
# have named the defect and let the rest run - and the rest is where the
# spelling this file is about is measured.
check(allg, f"the renderer wrote no CGNAT statement at all, so the order "
            f"below is measured on nothing: {cfg.text()!r}")
check(allg and allg[0] == "nat cgnat block-holddown 11",
      f"and everything else keeps its order: {allg}")
# ONE SPELLING IN THE FILE (#356). The pools and the interface attachments
# have carried `nat ` since the hierarchy moved and the globals did not, so a
# saved configuration came back in two spellings and an operator reading their
# own file found `nat cgnat pool` above `cgnat timeout udp`.
cfg2 = config.Config()
cfg2.globals = ["cgnat timeout udp 300"]
cfg2.pools["P"] = config.Pool("P")
_lines = [l for l in cfg2.text().splitlines() if "cgnat" in l]
check(all(l.startswith("nat cgnat") or l.startswith(" ") or
          l.startswith("!") for l in _lines),
      f"the file writes CGNAT statements in two spellings: {_lines}")
# And a file written this way still reads back, because the parser has taken
# both spellings since the hierarchy moved and stores the bare one.
check(config.Config.parse(cfg2.text()).globals == ["cgnat timeout udp 300"],
      f"a file this box wrote does not load on this box: "
      f"{config.Config.parse(cfg2.text()).globals} "
      f"{config.Config.parse(cfg2.text()).unknown}")

# Read back and written again: a file the box wrote must survive the trip,
# because that is what `rollback` replays and what the boot applies.
again = [l for l in config.Config.parse(cfg.text()).text().splitlines()
         if l.startswith("nat cgnat logging")]
check(again == written, f"and the order survives a round trip: {again}")

# `enable` and `disable` are ONE setting and two values. Keyed on the value,
# both stayed in the candidate and the commit applied both - in whatever order
# they happened to be in, which is not a state anybody asked for.
check(config.global_key("cgnat logging enable")
      == config.global_key("cgnat logging disable"),
      "turning the export off replaces turning it on, not joins it")
check(config.global_key("cgnat logging udp 10.0.0.2 src 10.0.0.1")
      != config.global_key("cgnat logging enable"),
      "and the exporter is a different setting from the state")
check(config.global_key("cgnat logging tcp 10.0.0.2")
      != config.global_key("cgnat logging udp 10.0.0.2"),
      "as are the two transports")


print("\n=== a public port is one socket per public address, not per pool ===")
# The VTY does not know the pool's arithmetic - which public address a
# subscriber sends from is the device's answer - so the only duplicate it can
# judge is the same port for the same subscriber. Refusing the same port for a
# DIFFERENT subscriber made a port sellable once per pool when a pool has as
# many public addresses as the operator gave it (issue #214), and the operator
# saw it as "this pool already forwards that port" naming nobody.
fp = config.Pool("p")
check(config.Config._pool_line(fp, "forward tcp 1650 to 100.64.0.5 8080")
      is None, "the first forward is accepted")
why = config.Config._pool_line(fp, "forward tcp 1650 to 100.64.0.6 8081")
check(why is None,
      f"the same port for another subscriber is the device's call: {why}")
check(len(fp.forwards) == 2,
      f"and both lines stay in the candidate: {fp.forwards}")
why = config.Config._pool_line(fp, "forward tcp 1650 to 100.64.0.5 9090")
check(why is not None,
      "the same port for the SAME subscriber is one socket twice")
check("100.64.0.5" in (why or ""),
      f"and the refusal names the subscriber it collided with: {why}")
check(len(fp.forwards) == 2,
      f"a refused line is not added: {fp.forwards}")
# Round trip: two forwards that share a public port must both survive being
# rendered and read back, or the running configuration read off a device that
# has both comes back with one and the next commit deletes the other. Built by
# hand rather than through the parser, so that this asserts the round trip and
# not what the check above already asserted.
rt = config.Pool("p")
rt.forwards = [("tcp", 1650, "100.64.0.5", 8080),
               ("tcp", 1650, "100.64.0.6", 8081)]
back = config.Config.parse("\n".join(["! cgnat", "cgnat pool p"]
                                     + rt.lines()))
check(sorted(back.pools["p"].forwards) == sorted(rt.forwards),
      f"both survive a round trip through the text: {back.pools['p'].forwards}")

# --- the linux_cp TAP must never be part of a configuration (#182) ---
#
# What this prevents is a box that does not come up. The dataplane reads
# report EVERY interface VPP has, and once the product makes pairs one of them
# is the pair's own TAP - so `save` wrote `interface tap4097` into the file
# prisma-routing.service replays at every boot, against a VPP that has just
# started and has no pairs yet. The interface-existence check then refused the
# file whole and the box came up with no pools, no addresses and no pair at
# all, which is the symptom #182 was opened about reached through another
# door. Measured in lab/lcp.sh by taking a pair down and reloading the file
# the box had saved.
dev = config.Config.parse("""interface tap4097
 mac-address de:ad:00:00:00:07
 mtu 9000
 exit
interface host-veth-vpp
 linux-name vppwan0
 mac-address 02:fe:00:00:00:01
 mtu 1500
 exit
""")
check(not dev.unknown, f"a file naming a pair's TAP parses: {dev.unknown}")
# Parsing does NOT drop it: a file is read with no dataplane in reach, and
# this is the one place that cannot tell a pair's TAP from an operator's own.
check("tap4097" in dev.macs,
      f"parsing keeps what the file says; the caller decides: {dev.macs}")
dev.drop_lcp_taps({"tap4097"})
check("tap4097" not in dev.macs, f"the TAP's MAC is gone: {dev.macs}")
check("tap4097" not in dev.mtus, f"the TAP's MTU is gone: {dev.mtus}")
# Not a blanket drop. The interface the operator actually configured has to
# survive, or this would empty the file it was meant to make loadable.
check(dev.macs.get("host-veth-vpp") == "02:fe:00:00:00:01",
      f"the real interface keeps its MAC: {dev.macs}")
check(dev.mtus.get("host-veth-vpp") == 1500, f"and its MTU: {dev.mtus}")
check(dev.linux_names.get("host-veth-vpp") == "vppwan0",
      f"and its pair: {dev.linux_names}")

# Named, never guessed from the shape. `tap0` is equally what an operator's
# own `vppctl create tap id 0` produces, and lab/hw-vpp.sh runs its whole WAN
# over exactly that - an address, a default route and a CGNAT attachment.
# Dropping it by shape would take a real interface out of the running
# configuration and the NEXT commit would remove it from the device, which is
# #47.
own = config.Config.parse("""interface tap0
 mtu 1400
 exit
""")
own.drop_lcp_taps({"tap4096"})
check(own.mtus == {"tap0": 1400},
      f"a TAP nobody paired keeps its configuration: {own.mtus}")

# The shape predicate itself, which is one of the two signals the callers
# combine: on an itf-pair line AND spelled the way VPP spells an lcp TAP.
check(config._is_lcp_tap("tap4096"), "tap4096 is VPP's spelling")
check(config._is_lcp_tap("tap0"), "and so is tap0")
check(not config._is_lcp_tap("tapfoo"), "tapfoo is not")
check(not config._is_lcp_tap("tap"), "and neither is a bare tap")

# The sets and the CGNAT attachments.
s2 = config.Config()
s2.admin_down = {"tap4096", "loop0"}
s2.ra_enabled = {"tap4096", "loop0"}
s2.descriptions = {"tap4096": "x", "loop0": "y"}
s2.interfaces = {"tap4096": "outside", "pg0": "inside"}
s2.drop_lcp_taps({"tap4096"})
check(s2.admin_down == {"loop0"}, f"admin_down: {s2.admin_down}")
check(s2.ra_enabled == {"loop0"}, f"ra_enabled: {s2.ra_enabled}")
check(s2.descriptions == {"loop0": "y"}, f"descriptions: {s2.descriptions}")
# The CGNAT attachment stays even for a TAP that IS part of a pair: it is read
# back from `show bpa config`, so it is only there when the dataplane really
# has it attached, and removing it from the model is the next commit detaching
# it from the box (#47).
check(s2.interfaces == {"tap4096": "outside", "pg0": "inside"},
      f"a CGNAT attachment is never dropped: {s2.interfaces}")

# interface_names is what the load path asks which interfaces a file names,
# and it is the same union the renderer walks - so a field added to one and
# not the other cannot go unnoticed.
named = config.Config.parse("""interface loop3
 ipv4 address 10.0.0.1/32
 exit
interface tap4096
 mtu 9000
 exit
""").interface_names()
check(named == {"loop3", "tap4096"}, f"the interfaces a file names: {named}")


# --- a statement that is typed and never reaches the dataplane -------------
#
# The shape of #47: `filter`, `max-blocks`, the per-pool timeouts and the ALG
# switches were accepted at the prompt, accepted at commit, and arrived
# nowhere. A pool setting has FOUR renderings that must all carry it - the
# configuration text it is read back as, the device command, the pool-creation
# message and the in-place settings message - and a field added to one of them
# is a feature that exists through whichever path the author happened to test.
print("\n=== block-stride survives every rendering of a pool ===")
_p = config.Config.parse("""cgnat pool striding
 inside 100.64.0.0/22
 outside 200.150.0.16/28
 block-stride 3
 exit
""").pools["striding"]
check(_p.block_stride == 3, f"parsed: {_p.block_stride}")
check(" block-stride 3" in _p.lines(),
      f"read back as configuration: {_p.lines()}")
check("block-stride 3" in _p.device_command(),
      f"as a device command: {_p.device_command()}")
check(_p.api_args()["block_stride"] == 3,
      f"in the pool-creation message: {_p.api_args().get('block_stride')}")
# In the settings message above all, because that is the one that says it can
# be changed without dropping every session on the pool. Missing here, the
# statement would be accepted, committed, and silently need a pool rebuild it
# never gets.
check(_p.settings_api_args()["block_stride"] == 3,
      f"in the in-place settings message: "
      f"{_p.settings_api_args().get('block_stride')}")
# And absent from the text when it is not set, so a pool that says nothing
# about the stride does not acquire a line for it on the next commit.
check(" block-stride 0" not in config.Pool("plain").lines(),
      "a pool with no stride emits no stride line")
print("  parsed, read back, and in both API messages")

# --- and the one that must NOT reach the in-place message ------------------
#
# The mirror image of the check above, and the more dangerous direction. A
# reserve is taken out of capacity, so it re-maps subscribers and needs the
# pool rebuilt; sent on the in-place settings message it would be accepted by
# a dataplane that cannot act on it, and the operator would read their own
# `reserve 4` back off a box still mapping every block (issue #39).
print("\n=== reserve is a mapping change, in every rendering ===")
_r = config.Config.parse("""cgnat pool reserved
 inside 100.64.0.0/22
 outside 200.150.0.16/28
 reserve 4
 exit
""").pools["reserved"]
check(_r.block_reserve == 4, f"parsed: {_r.block_reserve}")
check(" reserve 4" in _r.lines(), f"read back as configuration: {_r.lines()}")
# The half that decides everything downstream: lines(in_place=False) is what
# delta_to() compares to decide a pool must be REPLACED, and it is what
# `commit` renders to produce the re-mapping warning of #23. A reserve left
# out of it changes the mapping with no warning at all.
check(" reserve 4" in _r.lines(in_place=False),
      f"in the mapping half of the pool: {_r.lines(in_place=False)}")
check("reserve 4" in _r.device_command(),
      f"as a device command: {_r.device_command()}")
check(_r.api_args()["block_reserve"] == 4,
      f"in the pool-creation message: {_r.api_args().get('block_reserve')}")
check("block_reserve" not in _r.settings_api_args(),
      "the in-place settings message carries a reserve, which it cannot "
      "apply: that message exists for changes that are safe on a pool "
      "carrying traffic")
check(" reserve 0" not in config.Pool("plain").lines(),
      "a pool with no reserve emits no reserve line")
print("  parsed, read back, in the mapping half, and only in the creation "
      "message")
print("\n=== every function named, one at a time (#390) ===")
# The reader in test_every_function.py lists the functions no unit test
# names. Each block below calls one of them directly, on inputs the function
# exists for, and asserts what it answers: a test that only mentions the
# name would keep the reader quiet and prove nothing.
import hashlib  # noqa: E402
from system import User, ROLES  # noqa: E402

# mapping_digest: sha256 over the lines joined by newline, cut to sixteen
# characters. The separator is part of the rule: without it two different
# mappings could hash the same.
_D = config.mapping_digest(["cgnat pool a", " inside 100.64.0.0/22"])
check(_D == hashlib.sha256(
          b"cgnat pool a\n inside 100.64.0.0/22").hexdigest()[:16],
      f"sha256 of the newline-joined text, cut to 16: {_D}")
check(len(_D) == config.MAPPING_DIGEST_CHARS, f"sixteen characters: {_D}")
check(config.mapping_digest(["ab"]) != config.mapping_digest(["a", "b"]),
      "the line break is part of the text, so two lines are not one")

# strip_nat: the `nat` hierarchy taken off in both spellings, and a line
# outside it returned as it was, indentation and all.
check(config.strip_nat("nat cgnat pool p") == "cgnat pool p",
      f"nat cgnat -> cgnat: {config.strip_nat('nat cgnat pool p')!r}")
check(config.strip_nat("no nat cgnat pool p") == "no cgnat pool p",
      f"no nat cgnat -> no cgnat: {config.strip_nat('no nat cgnat pool p')!r}")
check(config.strip_nat(" nat ") == "", "the bare hierarchy word is nothing")
check(config.strip_nat(" interface x") == " interface x",
      "a line outside the hierarchy is returned verbatim")
check(config.strip_nat("nature is not nat") == "nature is not nat",
      "a word that starts with nat is not the hierarchy")

# opens_frr_block: the openers, and rpki as the one with no name.
check(config.opens_frr_block("router bgp 65000"), "router opens a block")
check(config.opens_frr_block(" interface vppwan0"),
      "so does interface, indented or not")
check(config.opens_frr_block("rpki"), "rpki opens a block with no name")
check(not config.opens_frr_block("rpki cache 10.0.0.1 3323 preference 1"),
      "a statement that starts with rpki and carries more is not the block")
check(not config.opens_frr_block("ip route 10.0.0.0/8 10.0.0.1"),
      "a static route opens nothing")

# block_of: the path of the FIRST line equal to the stripped statement.
_ROUTING = ["router bgp 65000",
            " neighbor 10.0.0.2 remote-as 65001",
            "exit",
            "vrf red",
            " ip route 10.0.0.0/8 10.0.0.1",
            "exit-vrf",
            "ip route 10.0.0.0/8 10.0.0.1"]
check(config.block_of(_ROUTING, "neighbor 10.0.0.2 remote-as 65001")
      == ["router bgp 65000"],
      f"a stripped statement finds its indented line: "
      f"{config.block_of(_ROUTING, 'neighbor 10.0.0.2 remote-as 65001')}")
check(config.block_of(_ROUTING, "ip route 10.0.0.0/8 10.0.0.1")
      == ["vrf red"],
      f"the same route in a VRF and at the top: the first occurrence: "
      f"{config.block_of(_ROUTING, 'ip route 10.0.0.0/8 10.0.0.1')}")
check(config.block_of(_ROUTING, "ip route 10.9.0.0/16 10.0.0.1") == [],
      "a statement the configuration does not hold has no path")

# is_static_route: the statement, not the position.
check(config.is_static_route("ip route 10.0.0.0/8 10.0.0.1"),
      "ip route is a static route")
check(config.is_static_route("ipv6 route 2001:db8::/32 2001:db8::1"),
      "and so is ipv6 route")
check(config.is_static_route("  ip route 10.0.0.0/8 10.0.0.1"),
      "indented is still the statement")
check(not config.is_static_route("ip ospf area 0"),
      "another ip statement is not")
check(not config.is_static_route(""), "an empty line is not")

# ungroup_static: the router static block back into FRR's words, family by
# the prefix, with everything outside the block untouched.
_GROUPED = ["router static",
            " route 10.0.0.0/8 10.0.0.1",
            " route 2001:db8::/32 2001:db8::1",
            " no route 10.1.0.0/16 10.0.0.1",
            "exit",
            "interface vppwan0",
            " ip ospf area 0",
            "exit"]
check(config.ungroup_static(_GROUPED) == [
          "ip route 10.0.0.0/8 10.0.0.1",
          "ipv6 route 2001:db8::/32 2001:db8::1",
          "no ip route 10.1.0.0/16 10.0.0.1",
          "interface vppwan0",
          " ip ospf area 0",
          "exit"],
      f"the block flattened, the family from the prefix: "
      f"{config.ungroup_static(_GROUPED)}")

# _address_family_error: the keyword against the prefix's own family.
check(config._address_family_error("ipv6", "10.0.0.1/24")
      == "'10.0.0.1/24' is not an IPv6 prefix",
      f"ipv6 with a v4 prefix: "
      f"{config._address_family_error('ipv6', '10.0.0.1/24')!r}")
check(config._address_family_error("ipv4", "2001:db8::1/64")
      == "'2001:db8::1/64' is not an IPv4 prefix",
      "ipv4 with a v6 prefix")
check(config._address_family_error("ipv4", "10.0.0.1")
      == "'10.0.0.1' has no prefix length",
      "a bare address has no length")
check(config._address_family_error("ipv4", "10.0.0.1/24") is None
      and config._address_family_error("ipv6", "2001:db8::1/64") is None,
      "the matching pairs are fine")

# vlan_tag_error: 1 to 4094, both ends in, 0 and 4095 out, and the half
# named in the refusal.
check(config.vlan_tag_error("1") is None
      and config.vlan_tag_error("4094") is None,
      "1 and 4094 are tags")
check(config.vlan_tag_error("0") ==
      "vlan 0 out of range (1-4094): 802.1Q reserves 0 and 4095",
      f"0 is refused with both ends: {config.vlan_tag_error('0')!r}")
check(config.vlan_tag_error("4095", "inner") ==
      "inner 4095 out of range (1-4094): 802.1Q reserves 0 and 4095",
      f"4095 refused, and the inner half named: "
      f"{config.vlan_tag_error('4095', 'inner')!r}")
check(config.vlan_tag_error("x") == "vlan 'x': expected a VLAN tag <1-4094>",
      f"a word is refused with the range: {config.vlan_tag_error('x')!r}")

# ordered_globals: alphabetical, except that the export state goes last so
# `cgnat logging enable` follows the collector line it depends on.
_G = config.ordered_globals(["cgnat logging udp 10.0.0.9 src 10.0.0.1",
                             "cgnat logging enable",
                             "cgnat alg ftp off"])
check(_G == ["cgnat alg ftp off",
             "cgnat logging udp 10.0.0.9 src 10.0.0.1",
             "cgnat logging enable"],
      f"alphabetical with the enable last: {_G}")

# linux_ifname_error: fifteen characters, no slash, not empty.
check(config.linux_ifname_error("vppwan0") is None, "an ordinary name")
check(config.linux_ifname_error("a" * 15) is None,
      "fifteen characters is the longest Linux allows, and is allowed")
check(config.linux_ifname_error("a" * 16) ==
      "'aaaaaaaaaaaaaaaa' cannot be a Linux interface name "
      "(15 characters, no '/')",
      f"sixteen is refused: {config.linux_ifname_error('a' * 16)!r}")
check(config.linux_ifname_error("GigabitEthernet0/13/0") is not None,
      "a VPP name with slashes is refused")
check(config.linux_ifname_error("") is not None, "and so is nothing")

# Pool.check_range: the statement's own range, both ends in.
check(config.Pool.check_range("block-size", "64") is None
      and config.Pool.check_range("block-size", "32768") is None,
      "both ends of block-size are in range")
check(config.Pool.check_range("block-size", "63") ==
      "block-size 63: expected 64-32768",
      f"one below: {config.Pool.check_range('block-size', '63')!r}")
check(config.Pool.check_range("block-size", "x") ==
      "block-size 'x': expected a number 64-32768",
      f"not a number: {config.Pool.check_range('block-size', 'x')!r}")
check(config.Pool.check_range("ports", "0", "min") ==
      "ports min 0: expected 1-65535",
      f"the half named: {config.Pool.check_range('ports', '0', 'min')!r}")

# Pool.check_pow2: a power of two, or the two nearest.
check(config.Pool.check_pow2("block-size", "512") is None,
      "512 is a power of two")
check(config.Pool.check_pow2("block-size", "100") ==
      "block-size 100 is not a power of two: the nearest are 64 and 128",
      f"100 is refused with 64 and 128: "
      f"{config.Pool.check_pow2('block-size', '100')!r}")
check(config.Pool.check_pow2("block-size", "x") is None,
      "not a number is check_range's refusal, not this one's")
try:
    config.Pool.check_pow2("ports", "8")
    check(False, "a statement with no power-of-two rule must raise")
except KeyError:
    pass

# Pool.check_order: first <= second, or the ordering named.
check(config.Pool.check_order("ports", "1024", "65535") is None,
      "min below max is fine")
check(config.Pool.check_order("ports", "1024", "1024") is None,
      "equal is fine: one port is a range of one")
check(config.Pool.check_order("ports", "9000", "100") ==
      "ports 9000 100: the max is below the min",
      f"the wrong way round: "
      f"{config.Pool.check_order('ports', '9000', '100')!r}")
check(config.Pool.check_order("ports", "x", "100") is None,
      "not a number is check_range's refusal, not this one's")
try:
    config.Pool.check_order("block-size", "1", "2")
    check(False, "a statement with no ordering rule must raise")
except KeyError:
    pass

# Pool._fwd_key: by public port, then TCP before UDP.
check(config.Pool._fwd_key(("udp", 80, "10.0.0.1", 8080)) == (80, 1),
      f"udp 80 keys as (80, 1): "
      f"{config.Pool._fwd_key(('udp', 80, '10.0.0.1', 8080))}")
_FWDS = [("udp", 80, "10.0.0.1", 80), ("tcp", 443, "10.0.0.1", 443),
         ("tcp", 80, "10.0.0.1", 80)]
check([f[:2] for f in sorted(_FWDS, key=config.Pool._fwd_key)]
      == [("tcp", 80), ("udp", 80), ("tcp", 443)],
      f"port first, then TCP before UDP: "
      f"{[f[:2] for f in sorted(_FWDS, key=config.Pool._fwd_key)]}")

# Pool.limits_api_args: both bounds, always, as numbers.
_L = base_pool("lim")
_L.max_sessions, _L.max_rate = 1000, 50
check(_L.limits_api_args() == {"name": "lim", "max_sessions": 1000,
                               "max_rate": 50},
      f"name and both bounds: {_L.limits_api_args()}")
check(config.Pool("z").limits_api_args() == {"name": "z", "max_sessions": 0,
                                             "max_rate": 0},
      "a pool with neither bound still sends both, as zero")

# Pool.forward_command: the add form, the checked add, and the delete.
_F = ("tcp", 80, "10.0.0.5", 8080)
check(_L.forward_command(_F) == "bpa forward lim tcp 80 to 10.0.0.5 8080",
      f"the add: {_L.forward_command(_F)!r}")
check(_L.forward_command(_F, check=True)
      == "bpa forward lim tcp 80 to 10.0.0.5 8080 check",
      f"the checked add: {_L.forward_command(_F, check=True)!r}")
check(_L.forward_command(_F, is_add=False) == "bpa forward lim tcp 80 del",
      f"the delete names the port and no target: "
      f"{_L.forward_command(_F, is_add=False)!r}")

# Pool.stride_inert: a stride on a pool that is not dynamic.
_S = base_pool("s")
_S.block_stride = 2
check(_S.stride_inert(), "a deterministic pool with a stride: inert")
_S.mode = "dynamic"
check(not _S.stride_inert(), "a dynamic pool with a stride: read")
_S.block_stride = 0
_S.mode = "deterministic"
check(not _S.stride_inert(), "a deterministic pool with no stride: nothing")

# Pool.__eq__: by rendered text, and never equal to something that is not a
# pool. Called by name because the reader does not see an operator.
# Two assignments, not one tuple: the reader files a name bound from a
# tuple as foreign and would not see the call.
_P1 = base_pool("q")
_P2 = base_pool("q")
check(_P1.__eq__(_P2), "two pools with the same fields are equal")
_P2.block_size = "1024"
check(not _P1.__eq__(_P2), "and not once a field differs")
check(not _P1.__eq__("cgnat pool q"), "a string is not a pool")

# Config.interface_lines: every field an interface has, in the order the
# configuration writes them, and nothing for an interface that has none.
_C = config.Config()
_C.linux_names["e"] = "wan0"
_C.macs["e"] = "52:54:00:00:00:01"
_C.ra_enabled.add("e")
_C.vrfs["e"] = 10
_C.vlans["e"] = {"outer": 100, "inner": 200}
_C.descriptions["e"] = "uplink"
_C.addresses["e"] = ["10.0.0.1/24", "2001:db8::1/64"]
_C.admin_down.add("e")
_C.mtus["e"] = 9000
_C.urpf["e"] = "loose"
check(_C.interface_lines("e") == [
          " linux-name wan0",
          " mac-address 52:54:00:00:00:01",
          " no ipv6 nd suppress-ra",
          " vrf 10",
          " vlan 100 inner 200",
          " description uplink",
          " ipv4 address 10.0.0.1/24",
          " ipv6 address 2001:db8::1/64",
          " shutdown",
          " mtu 9000",
          " urpf loose"],
      f"all eleven fields, in order: {_C.interface_lines('e')}")
_C.vlans["e"] = {"outer": 100, "inner": None}
check(" vlan 100" in _C.interface_lines("e")
      and not any(l.startswith(" vlan 100 inner")
                  for l in _C.interface_lines("e")),
      "a single tag has no inner half")
check(_C.interface_lines("nothing") == [],
      "an interface the configuration says nothing about has no lines")

# Config.drop_frr_interface_blocks: the stanza, its statements and its own
# exit go; the next block and everything else stay. In both shapes.
_C.routing = ["interface GigabitEthernet0/13/0",
              "ip ospf area 0",
              "exit",
              "interface vppwan0",
              "ip ospf area 0",
              "exit",
              "router ospf",
              "network 10.0.0.0/24 area 0",
              "exit"]
check(_C.drop_frr_interface_blocks(["GigabitEthernet0/13/0"])
      == ["GigabitEthernet0/13/0"],
      "the name dropped is returned")
check(_C.routing == ["interface vppwan0", "ip ospf area 0", "exit",
                     "router ospf", "network 10.0.0.0/24 area 0", "exit"],
      f"the flat shape: the stanza, its statement and its exit are gone, "
      f"the rest is not: {_C.routing}")
_C.routing = ["interface GigabitEthernet0/13/0",
              " ip ospf area 0",
              "exit",
              "!",
              "interface vppwan0",
              " ip ospf area 0",
              "exit",
              "!"]
check(_C.drop_frr_interface_blocks(["GigabitEthernet0/13/0", "none"])
      == ["GigabitEthernet0/13/0"],
      "a name the routing does not hold is not reported as dropped")
check(_C.routing == ["!", "interface vppwan0", " ip ospf area 0", "exit",
                     "!"],
      f"the indented shape, as FRR writes it: {_C.routing}")

# Config.interface_addresses_v4: (interface, address) for every v4 address,
# the host address as written and the v6 ones left out.
_C.addresses = {"a": ["10.0.0.1/24", "2001:db8::1/64"],
                "b": ["192.0.2.7/32"], "c": ["2001:db8::2/64"]}
check(_C.interface_addresses_v4() == [("a", "10.0.0.1/24"),
                                      ("b", "192.0.2.7/32")],
      f"the v4 addresses by interface, unmasked: "
      f"{_C.interface_addresses_v4()}")

# Config._user_line: one line of a user block into the User, or why not.
_U = User("ops")
check(config.Config._user_line(_U, "ssh-key ssh-ed25519 AAAA ops@box")
      is None and _U.keys == ["ssh-ed25519 AAAA ops@box"],
      f"an ssh-key line is kept whole: {_U.keys}")
check(config.Config._user_line(_U, "password hunter2") ==
      "password: expected a crypt hash such as $y$... or $6$... "
      "(openssl passwd -6), not a plaintext password"
      and _U.password == "",
      f"a plaintext password is refused and not stored: {_U.password!r}")
check(config.Config._user_line(_U, "password $6$salt$hash") is None
      and _U.password == "$6$salt$hash",
      "a crypt hash is stored")
check(config.Config._user_line(_U, f"role {ROLES[0]}") is None
      and _U.role == ROLES[0], f"a known role is stored: {_U.role!r}")
check(config.Config._user_line(_U, "role bogus")
      == f"role 'bogus': expected one of {', '.join(ROLES)}"
      and _U.role == ROLES[0],
      "an unknown role is refused in one row and the old role stays")
check(config.Config._user_line(_U, "exit") is None
      and config.Config._user_line(_U, "") is None,
      "exit and a blank line are nothing to refuse")
check(config.Config._user_line(_U, "shell /bin/sh")
      == "'shell /bin/sh' is not something a user block understands",
      "an unknown statement is refused by name")

# Config.mapping_lines: every pool's mapping half, in NAME order whatever
# the order they were typed in, and none of the in-place half.
_M = config.Config()
_M.pools["b"] = base_pool("b")
_M.pools["a"] = base_pool("a")
_M.pools["a"].timeouts["udp"] = 30
check(_M.mapping_lines() == base_pool("a").lines(in_place=False)
      + base_pool("b").lines(in_place=False),
      f"a before b, typed b then a: {_M.mapping_lines()}")
check(not any("timeout" in l for l in _M.mapping_lines()),
      "an in-place setting is not part of the mapping")
print("  twenty-three functions, each asked what it is for")


# --------------------------------------------------------------- VRRP (#7)

print("\n=== config.vrrp_statement: one statement, taken or refused ===")
# Mutation shown red: the range check dropped, which stages a vrid the
# dataplane's u8 cannot hold and a peer would never match.
v = {}
check(config.vrrp_statement(v, "pg0", ["vrrp", "1", "address",
                                       "100.64.3.254"]) is None,
      "an address is taken")
check(config.vrrp_statement(v, "pg0", ["vrrp", "1", "priority", "200"])
      is None, "a priority is taken")
check(config.vrrp_statement(v, "pg0", ["vrrp", "1", "track", "pg1",
                                       "decrement", "150"]) is None,
      "and what it watches")
check(v == {"pg0": {1: {"addresses": ["100.64.3.254"], "priority": 200,
                        "track": {"pg1": 150}}}},
      f"one virtual router, built up statement by statement: {v!r}")
# A second address on the same router, because a VR MAY stand for more than
# one address and RFC 5798 says so; a second statement, not a replacement.
config.vrrp_statement(v, "pg0", ["vrrp", "1", "address", "100.64.3.253"])
check(v["pg0"][1]["addresses"] == ["100.64.3.254", "100.64.3.253"],
      f"a second address is added, not swapped in: {v['pg0'][1]!r}")
config.vrrp_statement(v, "pg0", ["vrrp", "1", "address", "100.64.3.253"])
check(v["pg0"][1]["addresses"] == ["100.64.3.254", "100.64.3.253"],
      "and the same one twice is once")
check(config.vrrp_statement({}, "pg0", ["vrrp", "1"])
      == config.VRRP_USAGE_REFUSAL, "a vrid and nothing else is the usage")
for words, want in (
        (["vrrp", "x"], "the virtual router id is a number"),
        (["vrrp", "300", "address", "1.2.3.4"], "the virtual router id is 1"),
        (["vrrp", "1", "address", "2001:db8::1"], "IPv4 virtual routers only"),
        (["vrrp", "1", "address", "1.2.3.4/24"], "without a prefix length"),
        (["vrrp", "1", "address", "1.2.3"], "not an IPv4 address"),
        (["vrrp", "1", "priority", "255"], "RFC 5798's address owner"),
        (["vrrp", "1", "priority", "0"], "the range is 1 to 254"),
        (["vrrp", "1", "track", "pg0", "decrement", "5"],
         "cannot track itself"),
        (["vrrp", "1", "track", "pg1", "decrement", "255"],
         "the range is 1 to 254")):
    refusal = config.vrrp_statement({}, "pg0", words)
    check(refusal is not None and want in refusal[0] + refusal[1],
          f"{' '.join(words)}: expected {want!r}, got {refusal!r}")
check(config.vrrp_statement({}, "pg0", ["vrrp", "1", "address"])[0]
      == config.VRRP_USAGE_REFUSAL[0], "a half-typed statement is the usage")
print("  the ONE reader of this statement, so the prompt and a saved file agree")

print("\n=== config.vrrp_remove: the whole router, or a reason ===")
# Mutation shown red: leaving the empty interface entry behind, which makes
# interface_names() name a port with nothing on it.
v = {"pg0": {1: {"addresses": ["1.2.3.4"], "priority": 100, "track": {}},
             2: {"addresses": ["1.2.3.5"], "priority": 100, "track": {}}}}
check(config.vrrp_remove(v, "pg0", ["vrrp", "1"]) is None, "it goes")
check(list(v["pg0"]) == [2], f"and the other stays: {v!r}")
check(config.vrrp_remove(v, "pg0", ["vrrp", "2"]) is None, "the last one too")
check(v == {}, f"and the interface entry goes with it: {v!r}")
check(config.vrrp_remove(v, "pg0", ["vrrp", "1"])[0]
      == "pg0 has no virtual router 1", "removing one that is not there")
check(config.vrrp_remove(v, "pg0", ["vrrp"])[0] == "usage: no vrrp <vrid>",
      "with no id at all")
check("is a number" in config.vrrp_remove(v, "pg0", ["vrrp", "all"])[0],
      "with an id that is not one")
print("  all of a virtual router, because half of one stands for nothing")

print("\n=== config.vrrp_pair_findings: what two boxes say about each other ===")
# Mutation shown red: keying by interface name. The two boxes stand for one
# address under one id, and what the port is called locally is each box's own
# business - a comparison by name reports a correct pair as broken and a
# broken one as fine, depending on how the cabling was labelled.
mine = {"ens18": {1: {"addresses": ["100.64.3.254"], "priority": 200,
                      "track": {}}}}
same = {"ens19": {1: {"addresses": ["100.64.3.254"], "priority": 100,
                      "track": {}}}}
check(config.vrrp_pair_findings(mine, same) == [],
      f"a correct pair says nothing, whatever the ports are called: "
      f"{config.vrrp_pair_findings(mine, same)!r}")
tie = {"ens18": {1: {"addresses": ["100.64.3.254"], "priority": 200,
                     "track": {}}}}
found = config.vrrp_pair_findings(mine, tie)
check(len(found) == 1 and "priority 200 on both boxes" in found[0]
      and "RFC 5798" in found[0],
      f"the same priority on both is a finding, with the rule that breaks "
      f"the tie: {found!r}")
found = config.vrrp_pair_findings(mine, {})
check(len(found) == 1 and "on no interface of the peer's" in found[0],
      f"an id only this box carries is a finding: {found!r}")
found = config.vrrp_pair_findings({}, mine)
check(len(found) == 1 and "on no interface of this box" in found[0],
      f"and one only the peer carries, from the other side: {found!r}")
other = {"ens18": {1: {"addresses": ["100.64.9.254"], "priority": 100,
                       "track": {}}}}
found = config.vrrp_pair_findings(mine, other)
check(len(found) == 2,
      f"a different address under the same id is two findings, because each "
      f"box is alone for its own: {found!r}")
check(config.vrrp_pair_findings({}, {}) == [],
      "two boxes with no virtual router at all have nothing to compare")
print("  keyed by the id and the address, which is what a pair agrees on")

print("\n=== config.vrrp_why: the refusal as one sentence, for a log ===")
# Mutation shown red: the prose dropped, which leaves `--load` printing a
# verdict with no reason on a box nobody is watching.
check(config.vrrp_why(("a verdict", "a reason")) == "a verdict - a reason",
      "both halves joined")
check(config.vrrp_why(("a verdict", "")) == "a verdict",
      "and nothing added when there is no prose")
print("  what a file's reader gets, who has no console to fold in")

print("\n=== config._is_ipv4_address: a dotted quad and nothing else ===")
# Mutation shown red: the leading-zero check dropped. 010.1.1.1 is octal to
# some resolvers and decimal to others, so it is two addresses depending on
# who reads it.
for good in ("0.0.0.0", "1.2.3.4", "255.255.255.255", "100.64.3.254"):
    check(config._is_ipv4_address(good), f"{good} is one")
for bad in ("1.2.3", "1.2.3.4.5", "1.2.3.256", "1.2.3.-1", "010.1.1.1",
            "1.2.3.4/24", "gateway", "", "1.2.3.a"):
    check(not config._is_ipv4_address(bad), f"{bad!r} is not")
print("  each octet in range, written the way one reader would read it")

print("\n=== Pool.check_number: the shape, and never the range (#412) ===")
# Mutation shown red: `return None` for a non-digit, which puts the seven
# ValueErrors back and ends `prismash --load` in a traceback.
#
# THE SHAPE AND NOT THE RANGE is the whole of why this is not check_range with
# the numbers left off, and the case below that proves it is the `block-size 4`
# one: #357 enforced a range in this parser and a pool the dataplane had built
# with a small block came back unmodellable, so every commit on that box was
# refused. A shape check cannot do that, because `show bpa config` renders
# every one of these fields `%u`.
for good in ("0", "1", "4", "512", "65535", "4294967295"):
    check(config.Pool.check_number("block-size", good) is None,
          f"{good} is a number")
for bad in ("abc", "-5", "", "1.5", "0x10", "512 ", "+1"):
    why = config.Pool.check_number("block-size", bad)
    check(why is not None, f"{bad!r} is not a number")
check(config.Pool.check_number("ports", "abc", "min")
      == "ports min 'abc': expected a number",
      "the half is named when one statement carries two numbers")
import re                                                    # noqa: E402
check(not re.search(r"\d+-\d+",
                    config.Pool.check_number("max-blocks", "abc")),
      "and no range is quoted, because this parser does not enforce one - a "
      "bound in the sentence is a bound an operator would be told their own "
      "box's number breaks")
print("  a number or not, said without a bound this reader does not hold")

print("\n=== a pool block a hand cannot crash (#412) ===")
# Mutation shown red: any one of the ten guards removed - the case for that
# statement then raises out of Config.parse instead of landing in `unknown`,
# which is the traceback `prismash --load` printed at boot.
#
# Ten statements, not seven. The seven reach `int()` in _pool_line itself;
# block-size, ports and vrf reach it one step later in Pool.api_args, so they
# crashed the COMMIT rather than the parse - the same defect, further from the
# operator.
_POOL_HEAD = "cgnat pool p\n inside 10.0.0.0/8\n outside 200.0.0.0/24\n"
_CANNOT_CRASH = [
    ("timeout", " timeout udp abc\n", "timeout udp 'abc'"),
    ("timeout, negative", " timeout udp -5\n", "timeout udp '-5'"),
    ("max-blocks", " max-blocks abc\n", "max-blocks 'abc'"),
    ("block-stride", " block-stride abc\n", "block-stride 'abc'"),
    ("reserve", " reserve abc\n", "reserve 'abc'"),
    ("utilisation-alarm", " utilisation-alarm abc\n", "utilisation-alarm 'abc'"),
    ("max-sessions", " max-sessions abc\n", "max-sessions 'abc'"),
    ("max-session-rate", " max-session-rate abc\n", "max-session-rate 'abc'"),
    ("block-size", " block-size abc\n", "block-size 'abc'"),
    ("ports, the min", " ports abc 2000\n", "ports min 'abc'"),
    ("ports, the max", " ports 1024 abc\n", "ports max 'abc'"),
    ("vrf", " vrf banana\n", "vrf 'banana'"),
    ("outside-vrf", " outside-vrf banana\n", "outside-vrf 'banana'"),
]
for label, stmt, needle in _CANNOT_CRASH:
    try:
        cfg = config.Config.parse(_POOL_HEAD + stmt)
    except Exception as e:                                  # noqa: BLE001
        check(False, f"{label}: parse raised {type(e).__name__}: {e}")
        continue
    check(len(cfg.unknown) == 1,
          f"{label}: refused as one unknown line, got {cfg.unknown!r}")
    if cfg.unknown:
        lineno, text, why = cfg.unknown[0]
        check(lineno == 4,
              f"{label}: naming the line a hand would edit, got {lineno}")
        check(text == stmt.strip(),
              f"{label}: and the statement whole, got {text!r}")
        check(needle in why, f"{label}: {needle!r} not in {why!r}")
print(f"  {len(_CANNOT_CRASH)} statements, each a refusal with a line number "
      f"rather than a traceback")

print("\n=== and the direction #357 is about: the dataplane's own numbers ===")
# Mutation shown red: check_number replaced by check_range, which is what #357
# reverted. `block-size 4` is then refused, running_config() reports that
# committing would REMOVE the pool, and the box cannot be configured at all.
#
# Every value here is one `show bpa config` can print and the PROMPT refuses:
# a block below 64, a port range outside 1-65535, a utilisation alarm over 100.
# The parser has to take all of them, because they are facts about a running
# box rather than requests.
_DATAPLANE_SAYS = [
    ("a block the prompt would refuse", " block-size 4\n"),
    ("a port range the prompt would refuse", " ports 0 70000\n"),
    ("an alarm the prompt would refuse", " utilisation-alarm 200\n"),
    ("max-blocks above the u8 the prompt bounds", " max-blocks 300\n"),
    ("vrf 0, which is a real table", " vrf 0\n"),
]
for label, stmt in _DATAPLANE_SAYS:
    cfg = config.Config.parse(_POOL_HEAD + stmt)
    check(not cfg.unknown, f"{label}: refused, and must not be: {cfg.unknown!r}")
    if not cfg.unknown:
        pool = cfg.pools["p"]
        try:
            pool.api_args()
            pool.settings_api_args()
            pool.limits_api_args()
        except Exception as e:                              # noqa: BLE001
            check(False, f"{label}: parsed and then unsendable: "
                         f"{type(e).__name__}: {e}")
        check(stmt.strip() in cfg.text(),
              f"{label}: and round-trips, so `save` does not rewrite the box's "
              f"own number")
print("  a fact read off the box is taken whole; only a shape is refused")

print("\n=== the refusal's own words fit the console ===")
# Mutation shown red: the range put back into the sentence, or the statement
# interpolated twice - either takes the fixed half past CONSOLE_COLUMNS.
#
# THE OWN WORDS AND NOT THE VALUE, which is #252's split. The operator's value
# is quoted, and a quoted token is printed as it stands rather than folded -
# `console_prose` says why, and a value has no widest because an operator can
# type a word of any length. What has a widest is everything this box wrote,
# and that is what is measured: the longest statement name, the longest half
# name a statement carries, and the sentence around them.
_LONGEST_NAME = max(config.Pool.RANGES, key=len)
_LONGEST_HALF = max(config.Pool.TIMEOUTS, key=len)
_own = config.Pool.check_number(_LONGEST_NAME, "", _LONGEST_HALF)
check(len(_own) <= config.CONSOLE_COLUMNS,
      f"{len(_own)} columns of this box's own words: {_own!r}")
check(_LONGEST_NAME in _own and _LONGEST_HALF in _own,
      f"and both of the widest halves are really in it: {_own!r}")
print(f"  {len(_own)} columns at '{_LONGEST_NAME}' and '{_LONGEST_HALF}', the "
      f"widest this box can write")

print("\n=== system.is_number: the predicate isdigit() was not (#412) ===")
# Mutation shown red: `isascii()` dropped, which is the product as it shipped -
# '\u00b2' then passes the guard and the int() behind it raises, ending the VTY
# session and ending `prismash --load` at boot.
for good in ("0", "1", "42", "4294967295"):
    check(config.is_number(good), f"{good} is a number")
# The four measured on CPython 3.11. The first two RAISE in int(); the last two
# CONVERT, which is the quieter half - a file carrying one comes back a
# different file, because `save` writes the ASCII digit.
for bad, what in (("\u00b2", "superscript two: int() raises"),
                  ("\u2467", "circled eight: int() raises"),
                  ("\u0663", "arabic-indic three: int() returns 3"),
                  ("\U0001d7da", "mathematical two: int() returns 2")):
    check(bad.isdigit(), f"{what} - isdigit() really is true for it, which is "
                         f"the whole defect")
    check(not config.is_number(bad), f"{what} - and is_number says no")
for bad in ("", "abc", "-1", "1.5", " 1", "1 ", "0x10", "1_000"):
    check(not config.is_number(bad), f"{bad!r} is not a number")
check(config.is_number(7) and not config.is_number(None),
      "a non-string is stringified, the way validate_ssh hands it one")
print("  ASCII digits, which is what int() takes and %u prints")

print("\n=== and nothing in the VTY guards an int() with isdigit() any more ===")
# Mutation shown red: one call site put back - the count then disagrees.
#
# NO ALLOWLIST. The rule is that `.isdigit()` appears nowhere in the product
# but inside is_number's own definition and the docstring arguing against it.
# A site that genuinely wants Python's broader question can have it back by
# saying so here, which is a change somebody has to make deliberately.
_SRC = os.path.join(os.path.dirname(__file__), "..", "src", "prismash")
_left = []
for _name in sorted(os.listdir(_SRC)):
    if not _name.endswith(".py"):
        continue
    with open(os.path.join(_SRC, _name)) as _f:
        for _n, _line in enumerate(_f, 1):
            if ".isdigit()" not in _line:
                continue
            if _name == "system.py" and ("return v.isascii()" in _line
                                         or "isdigit=True" in _line
                                         or "WHY NOT" in _line):
                continue
            _left.append(f"{_name}:{_n}: {_line.strip()}")
check(not _left, "`.isdigit()` still guards something:\n    "
                 + "\n    ".join(_left))
print("  one predicate, and the forty-three call sites read it")

print("\n=== config.prefix_error: an IPv4 network the dataplane can hold ===")
# Mutation shown red: strict=True softened to strict=False, which is the
# dataplane's masking done silently here - the operator types 100.64.0.5/10,
# `save` writes 100.64.0.0/10, and the file is not the file.
for good in ("10.0.0.0/8", "100.64.0.0/10", "203.0.113.0/24", "0.0.0.0/0",
             "192.0.2.1/32"):
    check(config.prefix_error("inside", good) is None, f"{good} is a prefix")
_BAD_PREFIX = [
    ("potato", "expected a prefix", "not a prefix at all"),
    ("200.0.0.1", "expected a prefix", "an address with no length"),
    ("300.0.0.0/10", "not an IPv4 prefix", "an octet out of range"),
    ("10.0.0.0/33", "not a prefix length", "a length above 32"),
    ("2001:db8::/32", "expected an IPv4 prefix", "IPv6, which bpa_cli.c "
     "cannot unformat"),
    ("100.64.0.5/10", "host bits are set", "host bits, which the dataplane "
     "would mask in silence"),
    ("", "expected a prefix", "nothing at all"),
]
for value, needle, why in _BAD_PREFIX:
    r = config.prefix_error("inside", value)
    check(r is not None, f"{why}: {value!r} was accepted")
    if r:
        check(needle in r[0], f"{why}: {needle!r} not in {r[0]!r}")
# The network the operator meant is IN the refusal, which is the whole reason
# host bits are refused here rather than masked.
check("100.64.0.0/10" in config.prefix_error("inside", "100.64.0.5/10")[1],
      "and the prefix the operator meant is named, so the fix is a paste")
print(f"  {len(_BAD_PREFIX)} refusals, each naming what the dataplane cannot "
      f"hold")

print("\n=== config.prefix_why: the two rows as one, for the file ===")
# Mutation shown red: the prose dropped, which leaves `prismash --load`
# printing a verdict with no reason on a box nobody is watching - the same
# defect vrrp_why was written against.
check(config.prefix_why(("a verdict", "a reason")) == "a verdict - a reason",
      "both halves joined")
check(config.prefix_why(("a verdict", "")) == "a verdict",
      "and nothing added when there is no prose")
check(config.prefix_why(config.prefix_error("inside", "100.64.0.5/10"))
      == "inside '100.64.0.5/10': host bits are set - the prefix this names "
         "is 100.64.0.0/10",
      "and a real refusal reads as one sentence in a log")
print("  what a file's reader gets, who has no console to fold in")

print("\n=== and an INTERFACE address is the opposite thing (#413) ===")
# Mutation shown red: ip_interface swapped for ip_network, which refuses
# 10.0.0.5/8 - the ordinary way every router in this market is configured.
#
# On an interface the host bits ARE the address. In a pool prefix they are a
# mistake. Two statements, two rules, and this is the pair that says so.
for good in ("10.0.0.5/8", "192.0.2.1/24", "100.64.0.1/32"):
    check(config._address_family_error("ipv4", good) is None,
          f"{good} is an interface address, host bits and all")
check(config._address_family_error("ipv6", "2001:db8::1/64") is None,
      "and so is an IPv6 one")
for bad in ("999.1.1.1/24", "10.0.0.256/24", "10.0.0.1/33", "potato/24"):
    check(config._address_family_error("ipv4", bad) is not None,
          f"{bad!r} is not an address at all, and was staged until #413")
check(config._address_family_error("ipv6", "2001:zz::1/64") is not None,
      "nor is an IPv6 one with a bad group")
print("  host bits accepted where they are the address, refused where they "
      "are a mistake")

print("\n=== system.address_error: a host, and not a prefix (#413) ===")
# Mutation shown red: the "/" branch dropped, which lets a prefix through to
# sshd's ListenAddress and to the IPFIX exporter's collector field.
for good in ("10.0.0.1", "0.0.0.0", "2001:db8::1", "::1"):
    check(config.address_error("ssh listen-address", good) is None,
          f"{good} is an address")
# THE FORM AN INSTALLED BOX ACTUALLY HOLDS, and the case that made this
# refusal a defect rather than a guard. `sshd -T` prints an IPv6
# ListenAddress bracketed, `read_ssh` stores what it printed, so `[::]` is the
# value on every installed box - and refusing it took every commit there with
# it. Measured by tools/release-gate.sh on v0.110.0:
#
#     error: ssh listen-address '[::]': not an IP address
#     error: candidate did not pass validation; nothing applied
#
# Mutation shown red: unbracket() returning its argument unchanged.
for _real in ("[::]", "::", "0.0.0.0", "[2001:db8::1]", "10.0.0.1"):
    check(config.address_error("ssh listen-address", _real) is None,
          f"{_real} is a ListenAddress a box can hold, and was refused")
# The bracket stripper on its own, because address_error and
# ssh_listen_normal both stand on it and a test through either is a test of
# two things at once.
check(system.unbracket("[::]") == "::" and system.unbracket("[2001:db8::1]")
      == "2001:db8::1", "brackets come off an IPv6 literal")
for _plain in ("::", "0.0.0.0", "10.0.0.1", "potato", "", "[", "]"):
    check(system.unbracket(_plain) == _plain,
          f"{_plain!r} is not bracketed and comes back unchanged")
check(system.unbracket("[]") == "", "an empty pair strips to nothing, which "
                                    "address_error then refuses")
check(system.ssh_listen_normal("::") == "[::]"
      and system.ssh_listen_normal("[::]") == "[::]",
      "IPv6 is stored bracketed, which is the spelling sshd -T gives back")
check(system.ssh_listen_normal("0.0.0.0") == "0.0.0.0",
      "and IPv4 bare, which is the spelling it gives back for that")
_settings = {"listen-address": "::"}
check(system.validate_ssh(_settings) is None
      and _settings["listen-address"] == "[::]",
      f"so typing the bare form stores the box's form, and commit has nothing "
      f"to re-apply forever: {_settings!r}")
# And the brackets are not a wildcard: what is inside still has to be an
# address, or the refusal has been traded for no check at all.
for _bad in ("[potato]", "[::", "[]", "[10.0.0.0/8]"):
    check(config.address_error("ssh listen-address", _bad) is not None,
          f"{_bad!r} is not an address with brackets round it")
for bad, why in (("potato", "a word"), ("999.1.1.1", "an octet out of range"),
                 ("010.1.1.1", "a leading zero, which is octal to some "
                               "readers and decimal to others"),
                 ("10.0.0.0/8", "a prefix where a host is named"),
                 ("", "nothing at all")):
    check(config.address_error("ssh listen-address", bad) is not None,
          f"{why}: {bad!r} was accepted")
print("  the same rule for both families, from the library and not a copy")

print("\n=== both doors, on every statement that names an address (#413) ===")
# Mutation shown red: any one call site reverted - that row then parses.
#
# THE FILE HALF. The prompt half is in test_prismash_units.py, and #417 is the
# reader that will stop the two from drifting apart again.
_ADDRESS_DOORS = [
    ("pool inside", "cgnat pool p\n inside potato\n"),
    ("pool outside", "cgnat pool p\n outside 300.0.0.0/24\n"),
    ("pool bypass source", "cgnat pool p\n bypass source 999.9.9.9/99\n"),
    ("pool bypass destination",
     "cgnat pool p\n bypass destination 2001:db8::/32\n"),
    ("interface ipv4", "interface pg0\n ipv4 address 999.1.1.1/24\n"),
    ("interface ipv6", "interface pg0\n ipv6 address 2001:zz::1/64\n"),
    ("ssh listen-address", "ssh listen-address potato\n"),
    ("snmp listen-address", "snmp listen-address potato\n"),
]
for label, text in _ADDRESS_DOORS:
    cfg = config.Config.parse(text)
    check(len(cfg.unknown) == 1,
          f"{label}: a saved file took it: {cfg.unknown!r}")
# And the control, because a check that refuses everything is not a check.
_GOOD = ("cgnat pool p\n inside 100.64.0.0/10\n outside 203.0.113.0/24\n"
         " bypass source 10.0.0.0/8\n bypass destination 192.0.2.0/24\n exit\n"
         "interface pg0\n ipv4 address 10.0.0.5/8\n"
         " ipv6 address 2001:db8::1/64\n"
         "ssh listen-address 0.0.0.0\nsnmp listen-address 127.0.0.1\n")
_cfg = config.Config.parse(_GOOD)
check(not _cfg.unknown, f"a configuration of real values is taken whole: "
                        f"{_cfg.unknown!r}")
check(_cfg.pools["p"].inside == ["100.64.0.0/10"]
      and _cfg.addresses["pg0"] == ["10.0.0.5/8", "2001:db8::1/64"],
      f"and reaches the model unchanged: {_cfg.pools['p'].inside!r} "
      f"{_cfg.addresses.get('pg0')!r}")
print(f"  {len(_ADDRESS_DOORS)} statements refused in a saved file, and a real "
      f"configuration untouched")

print("\n=== and these refusals fit the console too ===")
# Mutation shown red: the two rows folded back into one sentence, which is
# 132 columns at an IPv6 value and is why prefix_error answers in rows (#355).
_LONG = "bypass destination"
for _value in ("2001:db8:aaaa:bbbb:cccc:dddd:eeee:ffff/64", "10.0.0.5/8",
               "potato", "10.0.0.0/33", ""):
    _r = config.prefix_error(_LONG, _value)
    if not _r:
        continue
    _own = _r[0].replace(f"'{_value}'", "")
    check(len(f"error: {_own}") <= config.CONSOLE_COLUMNS,
          f"verdict's own words at {len(_own)} columns: {_r[0]!r}")
    # THE PROSE IS FOLDED, so its length is not the rule - `console_prose`
    # wraps it and never splits a word, so what has to fit is the longest
    # WORD. Asserting the whole string is a stricter rule than the product's
    # and would refuse a correct two-row paragraph.
    for _word in _r[1].split():
        check(len(_word) <= config.CONSOLE_COLUMNS,
              f"a word the fold cannot break: {_word!r}")
print("  the verdict's own words unfolded, and no word in the prose the fold "
      "cannot break; the operator's value is quoted and runs over")

print("\n=== config.mac_error: six hex octets, unicast, not broadcast ===")
# Mutation shown red: the hex test dropped, which is the product as it
# shipped - the whole check was that the word had six colon-separated parts.
for good in ("02:00:00:00:00:01", "aa:bb:cc:dd:ee:ff", "AA:BB:CC:DD:EE:FF",
             "2:0:0:0:0:1", "00:00:00:00:00:00"):
    check(config.mac_error(good) is None, f"{good} is a MAC")
_BAD_MAC = [
    ("zz:zz:zz:zz:zz:zz", "not a hex octet", "letters that are not hex"),
    ("1:2:3:4:5:6789", "not a hex octet", "an octet of four digits"),
    ("02:00:00:00:00", "expected six octets", "five octets"),
    ("02:00:00:00:00:00:01", "expected six octets", "seven"),
    ("ff:ff:ff:ff:ff:ff", "broadcast", "the broadcast address"),
    ("01:00:5e:00:00:01", "group bit is set", "a multicast address"),
    ("", "expected an address", "nothing at all"),
]
for value, needle, why in _BAD_MAC:
    r = config.mac_error(value)
    check(r is not None, f"{why}: {value!r} was accepted")
    if r:
        check(needle in r[0], f"{why}: {needle!r} not in {r[0]!r}")
# The locally-administered bit is NOT a rule, and this is what says so: an
# operator matching a peer's filter may need a globally-unique address, and
# refusing one would refuse a real configuration.
check(config.mac_error("00:1b:21:00:00:01") is None,
      "a globally-unique address is not refused; the local bit is not a rule")
print(f"  {len(_BAD_MAC)} refusals, and the rule that is deliberately absent")

print("\n=== config.mac_normal: the spelling the dataplane writes (#414) ===")
# Mutation shown red: mac_normal returning value.lower() unchanged, which is
# the defect - `2:0:0:0:0:1` then never equals the `02:00:00:00:00:01` that
# sw_interface_dump gives back.
check(config.mac_normal("2:0:0:0:0:1") == "02:00:00:00:00:01",
      "a short octet is padded")
check(config.mac_normal("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff",
      "and the case is the dataplane's")
_typed = config.Config.parse("interface pg0\n mac-address 2:0:0:0:0:1\n")
_device = config.Config.parse(
    "interface pg0\n mac-address 02:00:00:00:00:01\n")
check(_typed.is_same(_device),
      f"so a short spelling equals what the box reports: {_typed.macs!r} "
      f"against {_device.macs!r}")
check(not {k: v for k, v in _device.delta_to(_typed).items() if v},
      "and commit has nothing to apply, where it used to apply the MAC "
      "forever")
print("  accepted in the spelling vppctl takes, stored in the one VPP prints")

print("\n=== config.mac_why: the two rows as one, for the file ===")
check(config.mac_why(("a verdict", "a reason")) == "a verdict - a reason",
      "both halves joined")
check(config.mac_why(("a verdict", "")) == "a verdict",
      "and nothing added when there is no prose")
print("  what a file's reader gets")

print("\n=== a MAC in a saved file: the shape, and not the policy (#419) ===")
# CHANGED BY #419, and the change is the point. A saved file is written FROM
# the readback, and `device_macs` reads six bytes out of sw_interface_dump
# with no opinion about what they are. So a port that really holds the
# broadcast or a multicast MAC has to round-trip through `save`, or `--load`
# refuses the whole file and the box comes up with no configuration.
#
# The shape is still refused at both doors: a word that is not six hex octets
# is not something any readback can produce.
for value in ("zz:zz:zz:zz:zz:zz", "1:2:3:4:5:6789"):
    _cfg = config.Config.parse(f"interface pg0\n mac-address {value}\n")
    check(len(_cfg.unknown) == 1,
          f"a saved file took the malformed {value!r}: {_cfg.unknown!r}")
for value in ("ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:01"):
    _cfg = config.Config.parse(f"interface pg0\n mac-address {value}\n")
    check(not _cfg.unknown and _cfg.macs == {"pg0": value},
          f"a saved file must TAKE {value!r}, which a box can report: "
          f"{_cfg.unknown!r}")
    check(config.mac_error(value) is not None,
          f"and the prompt must still refuse it: {value!r}")
    check(config.mac_error(value, request=False) is None,
          f"the same value read back as a fact is not refused: {value!r}")
_cfg = config.Config.parse("interface pg0\n mac-address 02:00:00:00:00:01\n")
check(not _cfg.unknown and _cfg.macs == {"pg0": "02:00:00:00:00:01"},
      f"and a real one is taken whole: {_cfg.macs!r} {_cfg.unknown!r}")
print("  the file door, four refusals and a control")

print("\n=== and these refusals fit the console ===")
for _v in ("zz:zz:zz:zz:zz:zz", "ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:01", ""):
    _r = config.mac_error(_v)
    _own = _r[0].replace(f"'{_v}'", "")
    check(len(f"error: {_own}") <= config.CONSOLE_COLUMNS,
          f"verdict at {len(_own)}: {_r[0]!r}")
    for _word in _r[1].split():
        check(len(_word) <= config.CONSOLE_COLUMNS,
              f"a word the fold cannot break: {_word!r}")
print("  the verdict unfolded, and no word in the prose the fold cannot break")

print("\n=== config.asn_error: 1 to 4294967295, and nothing else ===")
# Mutation shown red: the range test dropped, which is the product as it
# shipped - `router bgp 0` was staged and FRR refused it a commit later, on a
# candidate the operator had already built.
for good in ("1", "65000", "64512", "4200000000", "4294967295", "23456"):
    check(config.asn_error("router bgp", good) is None, f"{good} is an AS")
for bad, needle, why in (("0", "out of range", "zero, which RFC 7607 reserves"),
                         ("4294967296", "out of range", "one past 2^32-1"),
                         ("banana", "expected an AS number", "a word"),
                         ("-1", "expected an AS number", "a negative"),
                         ("", "expected an AS number", "nothing at all")):
    r = config.asn_error("router bgp", bad)
    check(r is not None, f"{why}: {bad!r} was accepted")
    if r:
        check(needle in r[0], f"{why}: {needle!r} not in {r[0]!r}")
check(config.asn_why(("a verdict", "a reason")) == "a verdict - a reason",
      "and the two rows join for a file, like every other refusal here")
print("  refused where no reading makes it an AS")

print("\n=== config.asn_note: said, and deliberately not refused ===")
# Mutation shown red: the private ranges added to AS_NOTABLE, which is the
# checker that warns about what is merely usual - the thing an operator learns
# to work around, and then works around the real findings too.
check("AS_TRANS" in config.asn_note("23456"),
      "AS_TRANS is named, with the RFC that reserves it")
check(config.asn_error("router bgp", "23456") is None,
      "and it is NOT refused: a lab may be built on it deliberately")
for private in ("64512", "65534", "4200000000", "4294967294"):
    check(config.asn_note(private) is None,
          f"{private} is a private AS (RFC 6996) and gets nothing at all")
for ordinary in ("1", "65000", "13335"):
    check(config.asn_note(ordinary) is None, f"{ordinary} gets nothing")
check(config.asn_note("banana") is None,
      "and a word gets nothing here; asn_error owns that refusal")
print(f"  {len(config.AS_NOTABLE)} number worth a sentence, and the private "
      f"ranges silent")

print("\n=== config.TEXT_LIMITS against bpa.api itself (#416) ===")
# Mutation shown red: any of the three numbers changed here - the table then
# disagrees with the message it describes, which is the second copy this
# whole file exists to prevent.
#
# THE API IS READ, not quoted. Three of the six rows come from bpa.api and are
# compared field by field; the other two are cited because their source is not
# in this repository, and they are named so a reader can check them in one
# command.
_API = os.path.join(os.path.dirname(__file__), "..", "src", "plugins", "bpa",
                    "bpa.api")
with open(_API) as _f:
    _api_text = _f.read()
#
# THE WIDTH AND THE USABLE MAXIMUM ARE TWO NUMBERS, and the difference is a
# measurement rather than arithmetic about C (#423). `string name[64]` holds
# 64 bytes and the dataplane refuses a 64-character name with retval -7 -
# asked, on a running box, one length at a time - so 63 is what an operator
# may type. The SECRET was asked too, and with a packet (#425): a secret
# cannot be read back, so lab/syslog.sh configures one and has a NAS sign
# with it - the box answers a signature made with the first 127 of a
# 128-character secret and not one made with all 128. `path[256]` is the row
# that still keeps its width, because it has not been asked:
# lowering them on the grounds that C strings have terminators would be
# lowering a limit an operator reads on a guess.
_FROM_API = {
    "cgnat pool": ("name", 64, 63),
    "cgnat identity file": ("path", 256, 256),
    "cgnat identity radius secret": ("secret", 128, 127),
}
for _what, (_field, _width, _usable) in _FROM_API.items():
    check(f"string {_field}[{_width}];" in _api_text,
          f"bpa.api no longer declares 'string {_field}[{_width}]' - the "
          f"limit for {_what!r} was read from it and has to move with it")
    check(config.TEXT_LIMITS[_what][0] == _usable,
          f"{_what}: the table says {config.TEXT_LIMITS[_what][0]}, and the "
          f"field is {_width} with {_usable} usable")
check(config.TEXT_LIMITS["cgnat identity dhcp-leases"][0] == 256,
      "both identity paths travel in the same field and take the same limit")
check(config.TEXT_LIMITS["description"] == (
          63, "VPP's vnet/interface.api, sw_interface_tag_add_del tag[64], "
              "which keeps 63 characters and truncates the rest"),
      "the description's limit names VPP's own field, which is not in this "
      "repository, AND what VPP does with more than it (#424)")
check(config.TEXT_LIMITS["user"][0] == 32
      and "useradd(8)" in config.TEXT_LIMITS["user"][1],
      "and a user name names useradd(8), which is the thing this box runs")
for _what, (_n, _source) in config.TEXT_LIMITS.items():
    check(_source.strip(), f"{_what} has a limit and no source for it")
print(f"  {len(config.TEXT_LIMITS)} bounds, {len(_FROM_API)} of them compared "
      f"against the API and the rest carrying their source")

print("\n=== config.text_error: bytes, and what a terminal executes ===")
# Mutation shown red: len(value) instead of len(value.encode()), which lets a
# 64-character accented description past a 64-BYTE field - the exact case the
# byte count exists for.
# 63, NOT 64, and VPP decided it (#424): asked through
# sw_interface_tag_add_del and read back with sw_interface_dump, 63, 64, 65
# and 100 characters all answer retval 0 and all read back as 63. It
# TRUNCATES where the pool name refuses, so a 64-character description was
# accepted by every screen and kept one character shorter - and a saved file
# is written from the readback (#419).
check(config.text_error("description", "a" * 63) is None,
      "63 bytes is what VPP keeps")
check(config.text_error("description", "a" * 64) is not None,
      "and 64 is what it silently turns into 63")
_d64 = config.text_error("description", "a" * 64)
check(_d64 and "truncates" in _d64[1],
      f"the refusal says what VPP does with the rest: {_d64}")
check(config.text_error("description", "a" * 65) is not None,
      "and 65 does not fit either")

# THE BOUNDARY THE DATAPLANE ACTUALLY DRAWS (#423), and it is one character
# below the field. A 64-character pool name used to pass this check, be
# staged, and be refused at the COMMIT with vat2's retval -7 - a refusal
# arriving where the operator can no longer act on it, which is the whole
# argument for the candidate model.
check(config.text_error("cgnat pool", "p" * 63) is None,
      "63 characters is what the dataplane accepted, and the prompt must too")
check(config.text_error("cgnat pool", "p" * 64) is not None,
      "64 characters is what the dataplane refused with retval -7, so the "
      "prompt has to refuse it first")
_r64 = config.text_error("cgnat pool", "p" * 64)
check(_r64 and "63" in _r64[0],
      f"the refusal has to name the number an operator can act on: {_r64}")
check(_r64 and "terminator" in _r64[1],
      f"and say why the field's own width is not it: {_r64}")
# THE COUNT IS IN BYTES, which is what this pair is about and which the
# limit moving from 64 to 63 does not change: 31 accented characters are 62
# bytes and fit, 32 are 64 and do not - on a field that would take 63 of them
# if they were ASCII.
check(config.text_error("description", "\u00e1" * 31) is None,
      "31 accented characters are 62 bytes and fit")
check(config.text_error("description", "\u00e1" * 32) is not None,
      "and 32 of them are 64 bytes, which does not fit 63")
check(config.text_error("description", "\u00e1" * 33) is not None,
      "33 of them are 66 bytes and do not - the count is bytes, because the "
      "field is")
check("66 bytes" in config.text_error("description", "\u00e1" * 33)[0],
      "and the refusal says how many, so the operator can shorten it")
# Accented text is not a control character, and this is what says so: the
# wiki is in Portuguese and an operator names a port in their own language.
check(config.text_error("description", "uplink para o tr\u00e2nsito") is None,
      "accented text is text")
for _bad, _why in (("up\x07link", "a BEL, which a terminal sounds"),
                   ("up\x1b[31mlink", "an ANSI escape, which a terminal obeys"),
                   ("up\nlink", "a newline, which is a second line in a file"),
                   ("up\x00link", "a NUL")):
    _r = config.text_error("description", _bad)
    check(_r is not None, f"{_why}: {_bad!r} was accepted")
    if _r:
        check("control character" in _r[0],
              f"{_why}: {_r[0]!r} does not name what is wrong")
# REFUSED AT THE PROMPT and not stripped, which is the half that made the two
# doors disagree: the prompt removed a BEL in silence and a file kept it.
#
# The FILE takes it, since #419: an interface tag is 64 bytes of whatever a
# byte can be, `device_descriptions` reports what the box holds, and refusing
# one here would refuse a file the box itself wrote.
_typed = config.Config.parse("interface pg0\n description up\x07link\n")
check(not _typed.unknown and _typed.descriptions == {"pg0": "up\x07link"},
      f"a saved file takes what the box can hold: {_typed.unknown!r}")
check(config.text_error("description", "up\x07link", request=False) is None,
      "read back as a fact it is not refused")
check(config.text_error("description", "up\x07link") is not None,
      "and asked for at the prompt it still is")
try:
    config.text_error("mtu", "1500")
    check(False, "text this table does not bound answered quietly")
except KeyError:
    pass
print("  counted in bytes, and refused where a terminal would act on it")

print("\n=== config.text_why: the two rows as one, for the file ===")
check(config.text_why(("a verdict", "a reason")) == "a verdict - a reason",
      "both halves joined")
check(config.text_why(("a verdict", "")) == "a verdict",
      "and nothing added when there is no prose")
check(config.text_why(config.text_error("cgnat pool", "p" * 200))
      == "cgnat pool: 200 bytes, and the limit is 63 - the field it travels "
         "in is bpa.api, bpa_pool_add_del name[64], which holds 63 "
         "characters and a terminator",
      "and a real refusal reads as one sentence in a log, naming the field "
      "AND why its width is not the limit (#423)")
print("  what a file's reader gets, who has no console to fold in")

print("\n=== and every bounded statement, at the file door (#416) ===")
_TEXT_DOORS = [
    ("pool name", "cgnat pool " + "p" * 200 + "\n"),
    ("user name", "user " + "u" * 200 + "\n"),
    ("description", "interface pg0\n description " + "A" * 4000 + "\n"),
]
for _label, _text in _TEXT_DOORS:
    _cfg = config.Config.parse(_text)
    check(len(_cfg.unknown) == 1,
          f"{_label}: a saved file took it: {_cfg.unknown!r}")
_cfg = config.Config.parse(
    "cgnat pool residential\n exit\nuser netadm\n exit\n"
    "interface pg0\n description uplink para o tr\u00e2nsito\n")
check(not _cfg.unknown and "residential" in _cfg.pools
      and "netadm" in _cfg.users
      and _cfg.descriptions["pg0"] == "uplink para o tr\u00e2nsito",
      f"and real names and a real description are taken whole: "
      f"{_cfg.unknown!r}")
print(f"  {len(_TEXT_DOORS)} refused in a file, and a real configuration "
      f"untouched")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
