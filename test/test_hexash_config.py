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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import config      # noqa: E402

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

print("  every field classified above behaves as classified")

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
# install.sh writes a default hexarouting.conf. hexarouting.service loads it at
# boot with "hexash --load", and hexash REFUSES a file it does not fully
# understand - deliberately, because applying a file minus the lines it could
# not read, on a box nobody is watching, is worse.
#
# So the banner the installer writes and the parser that reads it have to
# agree, and they did not: "!" was not recognised as a comment, the load
# refused, hexash exited non-zero, and hexarouting.service FAILED on the first
# boot of every install (#94).
#
# The default is read out of install.sh rather than copied here, so that
# changing one side without the other fails this test instead of a box.
import re  # noqa: E402
installer = os.path.join(os.path.dirname(__file__), "..", "appliance",
                         "install.sh")
m = re.search(r"printf '([^']+)' > \"\$DESTDIR/etc/hexarouting/hexarouting\.conf\"",
              open(installer).read())
check(m is not None,
      "could not find the default configuration in appliance/install.sh; "
      "if it moved, this test no longer checks anything")
if m:
    default = m.group(1).replace("\\n", "\n")
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
# hexarouting.service failed on the first boot of every install (#121).
print("\n=== the installer's own default configuration ===")
INSTALLED_DEFAULT = "!\n! HexaRouting\n!\n"
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
# both directions, because hexarouting.service replays a saved file at every
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
written = [l for l in cfg.text().splitlines() if l.startswith("cgnat logging")]
check(written == ["cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739",
                  "cgnat logging enable"],
      f"the exporter comes first however the statements were typed: {written}")

# The rest stays alphabetical, so two boxes holding the same configuration
# write the same file and a diff of two saved files is a diff of the settings.
allg = [l for l in cfg.text().splitlines() if l.startswith("cgnat ")]
check(allg[0] == "cgnat block-holddown 11",
      f"and everything else keeps its order: {allg}")

# Read back and written again: a file the box wrote must survive the trip,
# because that is what `rollback` replays and what the boot applies.
again = [l for l in config.Config.parse(cfg.text()).text().splitlines()
         if l.startswith("cgnat logging")]
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
# hexarouting.service replays at every boot, against a VPP that has just
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

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
