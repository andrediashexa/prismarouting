#!/usr/bin/env python3
"""
src/prismash/typedcfg.py (#558, phase 2): typed reads off the VTY's own
Config, desired state turned into the exact VTY lines, and the schema read
off the VTY's grammar.

THE ROUND TRIP IS THE POINT. Every edit here is checked the way a box would
take it: the lines typedcfg writes are fed through the VTY's own parser
(Config.parse of the block with the lines applied is not available without a
box, so the lines are checked against the grammar's words and against the
values a re-read gives), and lab/api-grpc.sh does the rest on a real VTY -
SetConfig, Commit, GetConfig equal to `show running-config`.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src", "prismash"))
import typedcfg as T  # noqa: E402
import prismash as P  # noqa: E402
from config import Config  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


TEXT = """nat
 cgnat
  pool A
   prefix-inside 100.64.0.0/10
   prefix-outside 200.0.0.0/24
   block-size 256
   ports 1024 65535
   policy interleaved
   mode dynamic
   vrf 7
   max-blocks 8
   timeout udp 120
   alg ftp off
   forward tcp 8080 to 100.64.0.5 80
   exit
  logging udp 10.0.0.2 src 10.0.0.1 port 4739
  logging syslog 10.0.0.2 port 601 enterprise 67033 facility 16 severity 6
  logging enable
  exit
 exit
interface ens18
 description up link
 ipv4 address 10.0.0.1/24
 ipv6 address 2001:db8::1/64
 mtu 1500
 cgnat outside
 exit
interface lo1
 ipv4 address 10.255.0.1/32
 exit
"""
cfg = Config.parse(TEXT)
check(not cfg.unknown, f"the sample parses whole: {cfg.unknown}")

print("=== reads ===")
d = T.config_typed(cfg)
check(sorted(d) == sorted(list(T.AREAS) + ["box_owned"]), "every area by default - the routing half too (#558 phase 3) - and the box's own lines")
i = {x["name"]: x for x in d["interfaces"]}
e = i["ens18"]
check(e["description"] == "up link" and e["mtu"] == 1500 and not e["shutdown"]
      and e["ipv4"] == ["10.0.0.1/24"] and e["ipv6"] == ["2001:db8::1/64"]
      and e["cgnat_role"] == "outside" and e["vrf"] == 0 and e["linux_name"] == "",
      f"interface_typed: the block's fields, absent ones as defaults: {e}")
check(T.interface_typed(cfg, "ens18") == e, "interface_typed is what interfaces_typed lists")
check([x["name"] for x in T.interfaces_typed(cfg)] == sorted(cfg.interface_names()),
      "interfaces_typed: every interface the configuration has a block for")
cfg.loopbacks.add("lo1")
check(T.interface_kind(cfg, "lo1") == "loopback" and T.interface_kind(cfg, "ens18") == "port",
      "interface_kind: a loopback, a port")
p = T.pool_typed(cfg.pools["A"])
check(p["prefix_inside"] == ["100.64.0.0/10"] and p["block_size"] == 256
      and (p["port_first"], p["port_last"]) == (1024, 65535) and p["mode"] == "dynamic"
      and p["vrf"] == 7 and p["outside_vrf"] is None and p["max_blocks"] == 8
      and p["timeouts"] == {"udp": 120} and p["algs"] == {"ftp": False}
      and p["forwards"] == [{"protocol": "tcp", "public_port": 8080,
                             "private_address": "100.64.0.5", "private_port": 80}],
      f"pool_typed: every statement, an unset VRF as None (every VRF, not VRF 0): {p}")
check([x["name"] for x in T.pools_typed(cfg)] == ["A"], "pools_typed: every pool")
lg = T.logging_typed(cfg)
cols = {c["transport"]: c for c in lg["collectors"]}
check(lg["enabled"] and cols["udp"] == {"transport": "udp", "configured": True,
                                        "collector": "10.0.0.2", "port": 4739,
                                        "source": "10.0.0.1"}
      and not cols["tcp"]["configured"]
      and cols["syslog"]["enterprise"] == 67033 and cols["syslog"]["facility"] == 16
      and [c["transport"] for c in lg["collectors"]] == ["udp", "tcp", "syslog"],
      f"logging_typed: enabled, each transport's collector, always the three: {lg}")
check(T.logging_line_typed(["syslog", "disable"])["configured"] is False
      and T.logging_line_typed(["enable"]) is None,
      "logging_line_typed: a disable is unconfigured; `enable` is not a collector")
try:
    T.config_typed(cfg, ["routes"])
    check(False, "an unknown area is refused")
except ValueError as ex:
    check("unknown area" in str(ex), "config_typed refuses an unknown area")

print("=== edits: desired state as the lines an operator types ===")
want = dict(e, description="", mtu=9000, ipv4=["10.0.0.2/24"], shutdown=True,
            cgnat_role="inside")
lines = T.interface_lines(e, want)
check(lines == ["no description", "no ipv4 address 10.0.0.1/24",
                "ipv4 address 10.0.0.2/24", "shutdown", "mtu 9000", "cgnat inside"],
      f"interface_lines: removals before additions, only what changed: {lines}")
check(T.interface_lines(e, e) == [], "an unchanged interface writes nothing")
check(T.interface_lines(e, want, ["mtu"]) == ["mtu 9000"],
      "a field mask limits the change to the fields named")
new = T.interface_lines(None, dict(T.interface_typed_blank("ens20"),
                                   description="new", ipv4=["192.0.2.1/24"]))
check(new == ["description new", "ipv4 address 192.0.2.1/24"],
      f"a new interface: only what is set: {new}")
# PREF64 (#485, phase 6): advertising first, then the prefixes, as a list.
check(e["pref64"] == [], "no PREF64 is an empty list")
ra = T.interface_lines(e, dict(e, router_advertisements=True,
                               pref64=["64:ff9b:1::/48"]))
check(ra == ["no ipv6 nd suppress-ra", "ipv6 nd nat64 64:ff9b:1::/48"],
      f"the advertisement, then what it carries: {ra}")
cfg.ra_enabled.add("ens18")
cfg.pref64["ens18"] = ["64:ff9b:1::/48"]
e2 = T.interface_typed(cfg, "ens18")
check(e2["pref64"] == ["64:ff9b:1::/48"] and e2["router_advertisements"],
      f"read back as fields: {e2}")
check(T.interface_lines(e2, dict(e2, pref64=[])) == [
      "no ipv6 nd nat64 64:ff9b:1::/48"], "and removed one at a time")
cfg.ra_enabled.discard("ens18")
del cfg.pref64["ens18"]
try:
    T.interface_lines(e, want, ["bogus"])
    check(False, "an unknown field is refused")
except ValueError as ex:
    check("unknown field" in str(ex), "_mask refuses an unknown field, naming the fields")
check(T._mask([], ("a", "b")) == ["a", "b"] and T._list_lines("x", ["1"], ["2"]) == ["no x 1", "x 2"],
      "_mask, _list_lines")
pw = dict(p, max_blocks=1, timeouts={"icmp": 30}, algs={}, prefix_outside=["200.0.0.0/23"],
          vrf=None, forwards=[], port_first=0, port_last=0)
pl = T.pool_lines(p, pw)
check(pl == ["no prefix-outside 200.0.0.0/24", "prefix-outside 200.0.0.0/23",
             "no ports", "no vrf", "no timeout udp", "timeout icmp 30", "no max-blocks",
             "no alg ftp", "no forward tcp 8080 to 100.64.0.5 80"],
      f"pool_lines: each statement and its `no`: {pl}")
check(T.pool_lines(p, p) == [], "an unchanged pool writes nothing")
check(T.pool_lines(None, dict(T.pool_typed_blank("C"), block_size=0, policy="", mode="",
                              filter="", prefix_inside=["100.64.0.0/22"]))
      == ["prefix-inside 100.64.0.0/22"],
      "a field no pool can be without, sent empty, is left as it is: never `filter` alone")
blank = T.pool_typed_blank("B")
check(blank["mode"] == "deterministic" and blank["block_size"] == 512,
      "pool_typed_blank: the VTY's own defaults for a new pool")
check(T._forward_line({"protocol": "udp", "public_port": 53, "private_address": "100.64.0.9",
                       "private_port": 53}) == "forward udp 53 to 100.64.0.9 53",
      "_forward_line: the line that added a forward")
lw = {"enabled": False, "collectors": [
    {"transport": "syslog", "configured": False},
    {"transport": "tcp", "configured": True, "collector": "10.0.0.9", "port": 4740}]}
ll = T.logging_lines(lg, lw)
check(ll == ["logging syslog disable", "logging tcp 10.0.0.9 port 4740", "logging disable"],
      f"logging_lines: a collector set or disabled, the unlisted one left alone: {ll}")
check(T.logging_lines(lg, lg) == [], "an unchanged log export writes nothing")
check(T.collector_line({"transport": "syslog", "configured": True, "collector": "c",
                        "enterprise": 67033, "facility": 0, "severity": None})
      == "logging syslog c enterprise 67033 facility 0",
      "collector_line: facility 0 is a value, an unset severity is the box's default")
try:
    T.collector_line({"transport": "udp", "configured": True, "collector": "c"})
    check(False, "a udp collector needs its source")
except ValueError as ex:
    check("src" in str(ex), "collector_line refuses a udp collector with no source")
check(T._same_collector(cols["udp"], dict(cols["udp"])), "_same_collector")
check(T.interface_path("ens18") == ["interface ens18"]
      and T.pool_path("A") == ["nat", "cgnat", "pool A"], "the paths Edit takes")
check(T.delete_lines("pools", "A") == (["nat", "cgnat"], ["no pool A"])
      and T.delete_lines("logging", transport="tcp") == (["nat", "cgnat"],
                                                         ["logging tcp disable"]),
      "delete_lines: as the VTY removes each")
path, ls = T.edit_for(cfg, "interfaces", dict(e, mtu=1400), ["mtu"])
check(path == ["interface ens18"] and ls == ["mtu 1400"],
      "edit_for: relative to what the candidate holds now")
check(T.names_for(cfg, "pools") == ["A"] and "ens99" in T.names_for(cfg, "interfaces", ["ens99"]),
      "names_for: configured pools; interfaces with the dataplane's")

print("=== every line an edit writes is one the VTY's grammar has ===")
GRAMMAR = {"interfaces": P.PORT_BLOCK, "pools": P.TREE["pool"],
           "logging": P.TREE["cgnat"]}


def in_grammar(node, words):
    """Does the grammar have the statement these words start? (`no` included)"""
    if words[0] == "no":
        neg = node.get("no")
        return isinstance(neg, tuple) and isinstance(neg[1], dict) and words[1] in neg[1]
    cur = node
    for w in words:
        if not isinstance(cur, dict) or w not in cur:
            return isinstance(cur, str) or cur is None or not isinstance(cur, dict)
        val = cur[w]
        cur = val[1] if isinstance(val, tuple) and len(val) > 1 else None
    return True


for area, ls in (("interfaces", lines + new), ("pools", pl), ("logging", ll)):
    bad = [x for x in ls if not in_grammar(GRAMMAR[area], x.split())]
    check(not bad, f"{area}: every line is in the grammar: {bad}")

print("=== box-owned statements: read only, on purpose ===")
own = Config.parse("""! routing
log syslog informational
service integrated-vtysh-config
vrf prisma-bp
exit-vrf
vrf red
 ip route 1.0.0.0/8 10.0.0.1
exit-vrf
""")
bo = [x["line"] for x in T.box_owned_lines(own)]
check(bo == ["log syslog informational", "service integrated-vtysh-config",
             "vrf prisma-bp", "exit-vrf"],
      f"box_owned_lines: FRR's own, the box's VRF and its exit-vrf; not an operator's VRF: {bo}")
check(T.box_owned("vrf prisma-x") and not T.box_owned("vrf red")
      and T.box_owned("exit-vrf", in_box_vrf=True) and not T.box_owned("exit-vrf"),
      "box_owned: by the statement, and by the block it is in")
check("box_owned" in T.config_typed(own, ["pools"]), "every typed read answers them, whatever the areas")
check(len(T.BOX_OWNED) == 4 and all(why for _, why in T.BOX_OWNED), "BOX_OWNED: each with its why")

print("=== no IPv6 statement carries an IPv4 placeholder ===")
v6 = [(m["mode"], " ".join(st["words"]), a["name"]) for m in T.schema(P)
      for st in m["statements"] if any("ipv6" in w or "ospf6" in w for w in st["words"])
      # A router-id is a 32-bit number written as an IPv4 address in OSPFv3
      # too (RFC 5340 A.3.1): its dotted quad is right.
      and "router-id" not in st["words"]
      for f in st["forms"] for a in f if a["kind"].startswith("ipv4")]
check(not v6, f"every ipv6 statement's address/prefix is an IPv6 or either-family one: {v6}")

print("=== schema: every mode the VTY has ===")
sch = T.schema(P)
by = {m["mode"]: m for m in sch}
check([m["mode"] for m in sch] == [m[0] for m in T.config_modes(P)] and len(sch) == 27,
      f"every mode of config_modes, in its order: {len(sch)}")
check(sum(len(m["statements"]) for m in sch) > 350,
      f"{sum(len(m['statements']) for m in sch)} statements")
bs = [s_ for s_ in by["pool"]["statements"] if s_["words"] == ["block-size"]][0]
check(bs["forms"][0][0]["kind"] == "integer" and bs["forms"][0][0]["max"] == 32768
      and bs["typed"] and not bs["removable"],
      f"block-size: an integer with its range, typed, not removable: {bs}")
mx = [s_ for s_ in by["pool"]["statements"] if s_["words"] == ["max-blocks"]][0]
check(mx["removable"] and mx["no_words"] == ["no", "max-blocks"], "a `no` form, its words")
udp = [s_ for s_ in by["cgnat"]["statements"] if s_["words"] == ["logging", "udp"]][0]
check(len(udp["forms"]) == 2 and udp["forms"][1][0]["name"] == "disable"
      and udp["forms"][0][0]["kind"] == "address" and udp["typed"],
      f"logging udp: two forms, the collector an address, typed: {udp['forms']}")
tmo = [s_ for s_ in by["cgnat"]["statements"] if s_["words"][:1] == ["timeout"]]
check(tmo and not any(s_["typed"] for s_ in tmo), "cgnat: only logging is typed")
nb = [s_ for s_ in by["router bgp"]["statements"] if s_["words"][:1] == ["neighbor"]]
check(nb and not any(s_["typed"] for s_ in nb), "router bgp: a mode with no typed statement")
check(by["pool"]["path"] == ["nat", "cgnat", "pool <name>"]
      and by["term"]["path"] == ["policy-options", "policy-statement <name>", "term <name>"]
      and by["pool"]["typed"] == "pools" and by["router bgp"]["typed"] == "bgp",
      "each mode's full EditRequest.path, the parent's included, and its typed area")
check(nb and all(s_["read_typed"] for s_ in nb),
      "router bgp: read as fields (read_typed) though written as lines (0.149.24)")
check(tmo and all(s_["read_typed"] for s_ in tmo) and not any(s_["typed"] for s_ in tmo),
      "cgnat timeouts: read as fields, not written as fields")
host = [s_ for s_ in by["config"]["statements"] if s_["words"][:1] == ["hostname"]]
comm = [s_ for s_ in by["config"]["statements"] if s_["words"][:2] == ["snmp", "community"]]
check(host and host[0]["typed"] and comm and not comm[0]["typed"] and comm[0]["read_typed"],
      "config: hostname written as a field; an SNMP community read only, never written typed")
srcs = {a["source"] for m in sch for st in m["statements"] for f in st["forms"]
        for a in f if a["source"]}
check({"interfaces", "pools", "neighbours", "next_hops"} <= srcs,
      f"dynamic value sources, from the VTY's own completion table: {sorted(srcs)}")
check(T.dynamic_sources(P)["<interface>"] == "interfaces", "dynamic_sources")
check(any(st["repeatable"] for st in by["interface (port)"]["statements"]),
      "a statement the grammar says may repeat")
check([m["mode"] for m in T.schema(P, ["pool"])] == ["pool"], "schema of one mode")
try:
    T.schema(P, ["nosuch"])
    check(False, "an unknown mode is refused")
except ValueError as ex:
    check("unknown mode" in str(ex), "schema refuses an unknown mode, naming the modes")
check(T.schema_digest(sch) == T.schema_digest(T.schema(P)) and len(T.schema_digest(sch)) == 64,
      "schema_digest: stable for the same grammar")
check(T.argument_of("<on|off>")["choices"] == ["on", "off"]
      and T.argument_of("[x]")["optional"]
      and T.argument_of("<interface>", T.dynamic_sources(P))["source"] == "interfaces",
      "argument_of: a choice, an optional word, a dynamic source")
check([a["optional"] for a in T.hint_arguments("<a> [port <n>]")] == [False, True, True],
      "hint_arguments: a bracketed group is optional")
st = []
T._walk({"a": ("help", "<1-2>"), "exit": ("x", None)}, [], st, {})
check([s_["words"] for s_ in st] == [["a"]], "_walk skips the session's words")
check(T._negations({"no": ("n", {"x": ("", None)})}) == {"x"}, "_negations")
check(T._int("7") == 7 and T._int("x") == 0, "_int")
check(T._blank_collector("syslog")["facility"] is None, "_blank_collector")

if fail:
    print(f"\n{fail} FAILURES")
    sys.exit(1)
print("\nALL TESTS PASSED")
