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
   mode bpa
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
# Area "bfd" (#559 phase 3) answers two lists, its sessions and its profiles.
check(sorted(d) == sorted([a for a in T.AREAS if a != "bfd"] +
                          ["bfd_peers", "bfd_profiles", "box_owned"]),
      "every area by default - the routing half too (#558 phase 3) - and the box's own lines")
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
      and (p["port_first"], p["port_last"]) == (1024, 65535) and p["mode"] == "bpa"
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

print("=== BFD as fields (#559 phase 3) ===")
# FRR 10.7.1's read-back shape: indented, `!` between stanzas, the profile
# of a neighbour on a second `bfd` line (#570), and the BFD words of a static
# route after its tag.
BFD = Config()
BFD.routing = [
    "bfd", " profile fast", "  detect-multiplier 3", "  receive-interval 100",
    "  echo receive-interval disabled", " exit", " !",
    " peer 10.0.0.2 interface eth0", "  profile fast",
    "  transmit-interval 200", " exit", " !",
    " peer 10.0.0.9 multihop local-address 10.0.0.1", "  minimum-ttl 250",
    "  passive-mode", "  log-session-changes", " exit", "exit",
    "router bgp 65000", " neighbor 10.0.0.2 remote-as 65001",
    " neighbor 10.0.0.2 bfd", " neighbor 10.0.0.2 bfd profile fast", "exit",
    "ip route 10.9.0.0/16 10.0.0.2 tag 5 bfd multi-hop profile fast"]
b = T.bfd_typed(BFD)
p2 = next(p for p in b["peers"] if p["address"] == "10.0.0.2")
p9 = next(p for p in b["peers"] if p["address"] == "10.0.0.9")
check(p2["interface"] == "eth0" and p2["profile"] == "fast"
      and p2["timers"]["transmit_interval_ms"] == 200
      and p2["timers"]["detect_multiplier"] == 0,
      f"a session: its key, its profile, the timer it sets, 0 for the rest: {p2}")
check(p9["multihop"] and p9["local_address"] == "10.0.0.1"
      and p9["timers"]["minimum_ttl"] == 250 and p9["timers"]["passive_mode"]
      and p9["other"] == ["log-session-changes"],
      f"a multihop session, a flag, and what no field takes in `other`: {p9}")
fast = b["profiles"][0]
check(fast["name"] == "fast" and fast["timers"]["receive_interval_ms"] == 100
      and fast["timers"]["echo_receive_disabled"],
      f"a profile, `echo receive-interval disabled` its own flag: {fast}")
# The key, not the text: FRR writes the words in its order, a client in any.
check(T.bfd_peer_key("peer 10.0.0.9 local-address 10.0.0.1 multihop")
      == {"address": "10.0.0.9", "multihop": True, "local_address": "10.0.0.1",
          "interface": "", "vrf": ""}
      and T.bfd_peer_header({"address": "10.0.0.9", "vrf": "red",
                             "local_address": "10.0.0.1", "multihop": True})
      == "peer 10.0.0.9 multihop local-address 10.0.0.1 vrf red",
      "a peer's key read in any order, and its header written in FRR's")
# Two `bfd` blocks in a candidate - each SetConfig enters the block again -
# are one: the reader merges by key. Mutation shown red: `peers.append`
# without the lookup gave the session twice.
TWICE = Config()
TWICE.routing = ["bfd", "peer 10.0.0.2", "detect-multiplier 5", "exit", "exit",
                 "bfd", "peer 10.0.0.2", "receive-interval 50", "exit", "exit"]
tw = T.bfd_typed(TWICE)["peers"]
check(len(tw) == 1 and tw[0]["timers"]["detect_multiplier"] == 5
      and tw[0]["timers"]["receive_interval_ms"] == 50,
      f"a session staged in two `bfd` blocks reads as one: {tw}")

print("=== BFD: the helpers, one by one ===")
blank = T.bfd_timers_blank()
check(set(blank) == set(T.BFD_TIMER_FIELDS) and not any(blank.values()),
      f"bfd_timers_blank: every timer, nothing stated: {blank}")
check(T.same_bfd_peer({"address": "10.0.0.2", "interface": "eth0",
                       "profile": "a"},
                      {"address": "10.0.0.2", "interface": "eth0",
                       "multihop": False})
      and not T.same_bfd_peer({"address": "10.0.0.2"},
                              {"address": "10.0.0.2", "multihop": True}),
      "same_bfd_peer: the key decides, a profile does not; multihop does")
t, o = T.bfd_timers_blank(), []
for s in ("detect-multiplier 4", "echo transmit-interval 70", "shutdown",
          "detect-multiplier nonsense"):
    T.bfd_body_statement(t, o, s)
check(t["detect_multiplier"] == 4 and t["echo_transmit_interval_ms"] == 70
      and t["shutdown"] and o == ["detect-multiplier nonsense"],
      f"bfd_body_statement: timers into fields, a malformed one into other: "
      f"{t} {o}")
check(T._echo_rx({"echo_receive_disabled": True}) == "disabled"
      and T._echo_rx({"echo_receive_interval_ms": 40}) == "40"
      and T._echo_rx({}) == "",
      "_echo_rx: disabled, a value, or nothing stated")
check(T.bfd_timer_lines({"echo_mode": True}, {}) == ["no echo-mode"]
      and T.bfd_timer_lines({}, {"echo_receive_disabled": True})
      == ["echo receive-interval disabled"]
      and T.bfd_timer_lines({"detect_multiplier": 3},
                            {"detect_multiplier": 9},
                            ["receive_interval_ms"]) == [],
      "bfd_timer_lines: a flag off, echo disabled, and a timer not named left")
check(T.bfd_timer_mask([]) == ([], ()) and
      T.bfd_timer_mask(["profile"]) == (["profile"], None) and
      T.bfd_timer_mask(["timers.shutdown"]) == ([], ["shutdown"]),
      "bfd_timer_mask: nothing named is everything; `profile` alone leaves "
      "the timers; one timer named is that one")
check(T.bfd_peer_lines(None, {"address": "10.0.0.5"}) ==
      ["peer 10.0.0.5", "exit"]
      and T.bfd_profile_lines(None, {"name": "p",
                                     "timers": {"passive_mode": True}})
      == ["profile p", "passive-mode", "exit"],
      "bfd_peer_lines / bfd_profile_lines: a new session, a new profile")
try:
    T.bfd_delete_lines(Config(), "", None)
    check(False, "bfd_delete_lines: no block, nothing to remove, refused")
except ValueError as e:
    check("no bfd block" in str(e), f"bfd_delete_lines refuses: {e}")
lst = []
first = T.bfd_peer_of(lst, "peer 10.0.0.2 vrf red")
again = T.bfd_peer_of(lst, "peer 10.0.0.2 vrf red")
check(first is again and len(lst) == 1 and first["vrf"] == "red",
      "bfd_peer_of: one entry per key")
pl = []
check(T.bfd_profile_of(pl, "x") is T.bfd_profile_of(pl, "x") and len(pl) == 1,
      "bfd_profile_of: one entry per name")

print("=== BFD edits: the lines, and only the ones that change ===")
path, bl = T.edit_for(BFD, "bfd_peers", {
    "address": "10.0.0.2", "interface": "eth0", "profile": "",
    "timers": {"transmit_interval_ms": 300, "detect_multiplier": 5}})
# Mutation shown red: a bare `no transmit-interval`, which this VTY stages as
# a statement of its own and leaves the old line in the candidate.
check(path == ["bfd"] and bl == ["peer 10.0.0.2 interface eth0",
                                 "no profile fast", "detect-multiplier 5",
                                 "no transmit-interval 200",
                                 "transmit-interval 300", "exit"],
      f"a session's changes, each removal the `no` of the line as it is: {bl}")
_, bl = T.edit_for(BFD, "bfd_profiles",
                   {"name": "fast", "timers": {"receive_interval_ms": 50}},
                   ["timers.receive_interval_ms"])
check(bl == ["profile fast", "no receive-interval 100", "receive-interval 50",
             "exit"], f"one timer named: only that one moves: {bl}")
# A timer named alone touches no plain field. Mutation shown red - and found
# by lab/api-grpc.sh's phase 9 before this check existed: "no plain field
# named" read as "every plain field", and the session lost its profile.
_, bl = T.edit_for(BFD, "bfd_peers", {
    "address": "10.0.0.2", "interface": "eth0",
    "timers": {"transmit_interval_ms": 400}}, ["timers.transmit_interval_ms"])
check(bl == ["peer 10.0.0.2 interface eth0", "no transmit-interval 200",
             "transmit-interval 400", "exit"],
      f"a session's one timer named: the profile left as it is: {bl}")
# Removing a neighbour with a BFD profile: its `bfd` line takes the profile
# line with it (#570), so no `no` is written for the second. Mutation shown
# red - and found by the lab: the commit failed "routing reload failed".
_, dl = T.delete_for(BFD, "bgp", name="10.0.0.2", asn="65000")
check("no neighbor 10.0.0.2 bfd" in dl
      and not any("bfd profile" in x for x in dl),
      f"a neighbour with a BFD profile removed by its `bfd` line alone: {dl}")
_, bl = T.edit_for(BFD, "bfd_peers", {"address": "10.0.0.2",
                                      "interface": "eth0",
                                      "profile": "slow"}, ["profile"])
check(bl == ["peer 10.0.0.2 interface eth0", "no profile fast",
             "profile slow", "exit"],
      f"`profile` named, the timers left as they are: {bl}")
for bad, why in (({"name": "two words"}, "a profile name is one word"),
                 ({"name": "x", "timers": {}}, None)):
    try:
        T.edit_for(BFD, "bfd_profiles", bad, ["timers.nonsense"])
        check(False, "an unknown timer field is refused")
    except ValueError as e:
        check("timers.nonsense" in str(e) or "one word" in str(e),
              f"refused: {e}")
check(T.delete_for(BFD, "bfd", bfd_peer={
          "address": "10.0.0.9", "local_address": "10.0.0.1",
          "multihop": True}) ==
      (["bfd"], ["no peer 10.0.0.9 multihop local-address 10.0.0.1"])
      and T.delete_for(BFD, "bfd", name="fast") == (["bfd"],
                                                     ["no profile fast"])
      and T.delete_for(BFD, "bfd") == ([], ["no bfd"]),
      "delete: a session by its key, a profile by its name, the whole block "
      "- each the `no` of its header, which takes the body with it (#570)")
try:
    T.delete_for(BFD, "bfd", bfd_peer={"address": "10.0.0.7"})
    check(False, "a session that is not there is refused")
except ValueError as e:
    check("no session" in str(e), f"refused: {e}")

print("=== BFD on a BGP neighbour and a static route ===")
nb = T.bgp_typed(BFD)[0]["neighbors"][0]
# Mutation shown red: the `bfd profile P` line read as `bfd` alone (the
# phase 2 reader), which a client then wrote back without its profile.
check(nb["bfd"] and nb["bfd_profile"] == "fast" and nb["other"] == [],
      f"the neighbour's two BFD lines are one field each: {nb}")
_, bl = T.edit_for(BFD, "bgp", {"asn": "65000", "neighbor": {
    "address": "10.0.0.2", "bfd": True, "bfd_profile": "slow"}},
    ["bfd_profile"])
check(bl == ["no neighbor 10.0.0.2 bfd profile fast",
             "neighbor 10.0.0.2 bfd profile slow"],
      f"the profile changed, BFD left on: {bl}")
_, bl = T.edit_for(BFD, "bgp", {"asn": "65000", "neighbor": {
    "address": "10.0.0.2", "bfd": False}}, ["bfd", "bfd_profile"])
check(bl == ["no neighbor 10.0.0.2 bfd"],
      f"BFD off: one `no`, which takes the profile line too (#570): {bl}")
st = T.static_routes_typed(BFD)[0]
check(st["bfd"] and st["bfd_multihop"] and st["bfd_profile"] == "fast"
      and st["extra"] == [] and st["tag"] == 5,
      f"a static route's BFD words are its fields, not `extra`: {st}")
check(T.static_line(st) ==
      "10.9.0.0/16 next-hop 10.0.0.2 tag 5 bfd multi-hop profile fast",
      f"and written back after the tag, where the route takes them: "
      f"{T.static_line(st)}")
try:
    T.static_line(dict(st, bfd=False))
    check(False, "a BFD option without bfd is refused")
except ValueError as e:
    check("needs bfd" in str(e), f"refused: {e}")
# Every line a BFD edit writes is the BFD grammar's own (a `no` checked on
# the statement it removes).
for node, ls in ((P.BFD_PEER, ["detect-multiplier 5", "no transmit-interval 200",
                               "profile slow", "echo receive-interval disabled",
                               "echo-mode", "no passive-mode"]),
                 (P.BFD_BLOCK, ["peer 10.0.0.2 interface eth0",
                                "profile fast"])):
    bad = [x for x in ls if not in_grammar(
        node, x.split()[1:] if x.startswith("no ") else x.split())]
    check(not bad, f"BFD: every line is in the grammar: {bad}")

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
check([m["mode"] for m in sch] == [m[0] for m in T.config_modes(P)] and len(sch) == 35,
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

# #572: the `nat64` area - NAT64 pools read and set apart from `pools`, at
# their own path, with their own fields; one name across both levels.
print("=== #572: the nat64 area ===")
import config as _C572  # noqa: E402
_c572 = _C572.Config.parse("""nat
 cgnat
  pool res
   prefix-inside 100.64.0.0/22
   prefix-outside 203.0.113.0/29
   exit
  exit
 nat64
  pool v6
   prefix-inside 2001:db8:1200::/40
   subscriber-length 56
   prefix-outside 203.0.113.8/29
   exit
  exit
 exit
""")
_t572 = T.config_typed(_c572, ["pools", "nat64"])
check([p["name"] for p in _t572["pools"]] == ["res"]
      and [p["name"] for p in _t572["nat64"]] == ["v6"],
      f"#572: pools holds the CGNAT pools, nat64 the NAT64 ones: {_t572}")
check(T.nat64_pools_typed(_c572)[0] == T.nat64_pool_typed(_c572.pools["v6"])
      and "bypass_source" not in _t572["nat64"][0]
      and _t572["nat64"][0]["subscriber_length"] == 56,
      f"#572: a NAT64 pool's fields, less the bypass: {_t572['nat64'][0]}")
check("nat64" in T.AREAS and "nat64" in T.config_typed(_c572),
      "#572: and nat64 is one of the areas every read answers")
_path, _lines = T.edit_for(_c572, "nat64", {"name": "v7",
                                            "prefix_inside": ["2001:db8:7::/48"],
                                            "subscriber_length": 56,
                                            "nat64_prefix": "64:ff9b:1::/48"})
check(_path == ["nat", "nat64", "pool v7"]
      and "subscriber-length 56" in _lines
      and "nat64-prefix 64:ff9b:1::/48" in _lines,
      f"#572: SetConfig's nat64 lines, at nat64's path: {_path} {_lines}")
_path, _lines = T.edit_for(_c572, "pools", {"name": "res2",
                                            "prefix_inside": ["100.64.4.0/22"]})
check(_path == ["nat", "cgnat", "pool res2"]
      and not any("subscriber-length" in x or "nat64-prefix" in x
                  for x in _lines),
      f"#572: a CGNAT pool's lines carry nothing NAT64: {_lines}")
try:
    T.edit_for(_c572, "pools", {"name": "v6", "block_size": 1024})
    _why = None
except ValueError as e:
    _why = str(e)
check(_why and "is a pool under 'nat64'" in _why,
      f"#572: one name across both levels, at SetConfig too: {_why}")
check(T.delete_lines("nat64", "v6") == (["nat", "nat64"], ["no pool v6"]),
      "#572: DeleteConfig's nat64 removes the pool at its level")
check(T.names_for(_c572, "nat64") == ["v6"]
      and T.names_for(_c572, "pools") == ["res"],
      "#572: and each area names its own pools")
check("nat64 pool" in T.TYPED_MODES and by.get("nat64 pool"),
      "#572: GetSchema has the nat64 pool mode, typed")

if fail:
    print(f"\n{fail} FAILURES")
    sys.exit(1)
print("\nALL TESTS PASSED")
