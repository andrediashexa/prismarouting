#!/usr/bin/env python3
"""
src/prismash/typedcfg.py, the routing half (#558, phase 3): static routes,
BGP, OSPF/OSPF6, IS-IS and the interfaces' IGP settings read as fields off
the FRR statements the candidate holds, and the lines that set a static
route or a BGP neighbour.

THE TWO SHAPES. `cfg.routing` arrives indented when it is read back from FRR
and flat when it was typed at the VTY in this session (`Shell.feed` strips
the indentation). Both are read here through `block_paths`, and both must
give the same fields - a read that only worked for one of them would be
right on a fresh session and wrong after the first commit.

The values are checked against the VTY's own screens on a real box by
lab/api-grpc.sh (lab/api_phase3_check.py): SetConfig, Commit, GetConfig
equal to `show running-config`.
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src", "prismash"))
import typedcfg as T  # noqa: E402
import apiserver as A  # noqa: E402
from config import Config  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


INDENTED = """interface ens18
 ipv4 address 10.0.0.1/24
 exit
! routing
!
ip route 10.77.0.0/16 10.0.0.2 tag 5 200
ipv6 route 2001:db8:77::/48 2001:db8::2
!
vrf red
 ip route 10.9.0.0/16 10.0.0.9
exit-vrf
!
interface ens18
 ip ospf area 0
 ip ospf cost 10
 ip ospf network point-to-point
 ip ospf hello-interval 5
 ip ospf dead-interval 20
 ipv6 ospf6 area 0
 ip router isis HEXA
 isis circuit-type level-2-only
 isis metric 20
 isis passive
 ip pim
exit
!
router bgp 65000
 bgp router-id 10.0.0.1
 no bgp ebgp-requires-policy
 neighbor 10.0.0.2 remote-as 65001
 neighbor 10.0.0.2 description upstream
 neighbor 10.0.0.2 update-source 10.0.0.1
 neighbor 10.0.0.2 timers 10 30
 neighbor 10.0.0.2 password s3cret
 neighbor 10.0.0.2 ebgp-multihop 2
 neighbor 10.0.0.2 bfd
 neighbor PG peer-group
 neighbor PG remote-as external
 neighbor 10.0.0.3 peer-group PG
 neighbor 10.0.0.3 shutdown
 neighbor 10.0.0.3 capability extended-nexthop
 !
 address-family ipv4 unicast
  network 200.0.0.0/24
  aggregate-address 200.0.0.0/22 summary-only as-set
  redistribute connected route-map RM
  neighbor 10.0.0.2 activate
  neighbor 10.0.0.2 route-map PO-UP-IN in
  neighbor 10.0.0.2 route-map PO-UP-OUT out
  neighbor 10.0.0.2 prefix-list PL-IN in
  neighbor 10.0.0.2 maximum-prefix 1000
  neighbor 10.0.0.2 next-hop-self
  neighbor 10.0.0.2 soft-reconfiguration inbound
  neighbor 10.0.0.2 default-originate
  neighbor 10.0.0.2 allowas-in 1
  table-map TM
 exit-address-family
exit
!
router bgp 65010 vrf red
 neighbor 10.9.0.2 remote-as 65020
exit
!
router ospf
 ospf router-id 1.1.1.1
 passive-interface default
 no passive-interface ens18
 passive-interface ens19
 network 10.0.0.0/24 area 0
 area 1 stub no-summary
 area 2 nssa
 area 3 range 10.3.0.0/16
 redistribute connected metric 20 metric-type 1 route-map RM
 default-information originate always
 timers throttle spf 10 100 1000
exit
!
router ospf vrf red
 ospf router-id 9.9.9.9
exit
!
router ospf6
 ospf6 router-id 1.1.1.1
exit
!
router isis HEXA
 net 49.0001.0000.0000.0001.00
 is-type level-2-only
 metric-style wide
 redistribute ipv4 connected level-2
 set-overload-bit
exit
!
"""

# The same routing as typed at the prompt: no indentation, blocks closed by
# their own `exit`. Interfaces' IGP lines and the VRF are left out: what is
# compared below is the BGP block, which is where the shapes differ most.
FLAT = """interface ens18
 ipv4 address 10.0.0.1/24
 exit
! routing
router bgp 65000
bgp router-id 10.0.0.1
no bgp ebgp-requires-policy
neighbor 10.0.0.2 remote-as 65001
neighbor 10.0.0.2 description upstream
neighbor 10.0.0.2 update-source 10.0.0.1
neighbor 10.0.0.2 timers 10 30
neighbor 10.0.0.2 password s3cret
neighbor 10.0.0.2 ebgp-multihop 2
neighbor 10.0.0.2 bfd
neighbor PG peer-group
neighbor PG remote-as external
neighbor 10.0.0.3 peer-group PG
neighbor 10.0.0.3 shutdown
neighbor 10.0.0.3 capability extended-nexthop
address-family ipv4 unicast
network 200.0.0.0/24
aggregate-address 200.0.0.0/22 summary-only as-set
redistribute connected route-map RM
neighbor 10.0.0.2 activate
neighbor 10.0.0.2 route-map PO-UP-IN in
neighbor 10.0.0.2 route-map PO-UP-OUT out
neighbor 10.0.0.2 prefix-list PL-IN in
neighbor 10.0.0.2 maximum-prefix 1000
neighbor 10.0.0.2 next-hop-self
neighbor 10.0.0.2 soft-reconfiguration inbound
neighbor 10.0.0.2 default-originate
neighbor 10.0.0.2 allowas-in 1
table-map TM
exit-address-family
exit
"""

cfg = Config.parse(INDENTED)
cfg.static_descriptions = {"ip route 10.77.0.0/16 10.0.0.2": "lab route"}
check(not cfg.unknown, f"the sample parses whole: {cfg.unknown}")

print("=== the walk ===")
st = T.routing_statements(cfg)
check(("", "") not in st and all(p is not None for p, _ in st)
      and (["router bgp 65000", "address-family ipv4 unicast"],
           "network 200.0.0.0/24") in st,
      "routing_statements: (path, statement), separators and exits left out")
check(T._vrf_of_block("router bgp 65010 vrf red") == "red"
      and T._vrf_of_block("router ospf vrf red") == "red"
      and T._vrf_of_block("router bgp 65000") == "",
      "_vrf_of_block: the VRF a router block names, or none")
check(T.is_router("router ospf", "ospf") and not T.is_router("router ospf6", "ospf")
      and T.is_router("router ospf vrf red", "ospf"),
      "is_router: `router ospf` is not `router ospf6`")

print("=== static ===")
s = T.static_typed("ip route 10.77.0.0/16 10.0.0.2 tag 5 200", "lab route")
check(s == {"prefix": "10.77.0.0/16", "next_hop": "10.0.0.2", "distance": 200,
            "tag": 5, "description": "lab route", "vrf": "", "family": "ipv4",
            "extra": [], "line": "10.77.0.0/16 next-hop 10.0.0.2 distance 200 "
            "tag 5 description lab route"},
      f"static_typed: FRR's spelling read through the VTY's own: {s}")
check(T.static_typed("ip route 10.1.0.0/16 10.0.0.2 ens18 onlink")["extra"]
      == ["ens18", "onlink"], "static_typed: FRR's words after the hop kept")
sr = T.static_routes_typed(cfg)
check([(r["prefix"], r["vrf"], r["family"], r["description"]) for r in sr] ==
      [("10.77.0.0/16", "", "ipv4", "lab route"),
       ("2001:db8:77::/48", "", "ipv6", ""), ("10.9.0.0/16", "red", "ipv4", "")],
      f"static_routes_typed: the top level with its description, and a VRF's: {sr}")
check(T.find_static(cfg, "10.77.0.0/16", "10.0.0.2")["tag"] == 5
      and T.find_static(cfg, "10.9.0.0/16", "10.0.0.9") is None,
      "find_static: a top-level route by prefix and hop; a VRF's is not one")

print("=== bgp ===")
b = T.bgp_typed(cfg)
check([(r["asn"], r["vrf"]) for r in b] == [("65000", ""), ("65010", "red")],
      "bgp_typed: every router bgp block, with its VRF")
r = b[0]
n = {x["address"]: x for x in r["neighbors"]}
check(r["default_ipv4_unicast"] and T.bgp_typed(Config.parse(
          "! routing\nrouter bgp 1\n no bgp default ipv4-unicast\nexit\n"))[0][
          "default_ipv4_unicast"] is False,
      "bgp_typed: default_ipv4_unicast, on unless `no bgp default ipv4-unicast`")
check(r["router_id"] == "10.0.0.1" and r["other"] == ["no bgp ebgp-requires-policy"],
      f"bgp_typed: router-id a field, the rest kept verbatim: {r['other']}")
check(n["10.0.0.2"]["remote_as"] == "65001" and n["10.0.0.2"]["description"]
      == "upstream" and n["10.0.0.2"]["update_source"] == "10.0.0.1"
      and (n["10.0.0.2"]["keepalive"], n["10.0.0.2"]["holdtime"]) == (10, 30)
      and n["10.0.0.2"]["password_set"] and n["10.0.0.2"]["ebgp_multihop"] == 2
      and n["10.0.0.2"]["bfd"], f"bgp_typed: a neighbour's fields: {n['10.0.0.2']}")
check("s3cret" not in repr(b), "bgp_typed: a password is set or not; its value is never read")
check(n["PG"]["is_peer_group"] and n["PG"]["remote_as"] == "external"
      and n["10.0.0.3"]["peer_group"] == "PG" and n["10.0.0.3"]["shutdown"]
      and n["10.0.0.3"]["other"] == ["neighbor 10.0.0.3 capability extended-nexthop"],
      "bgp_typed: a peer-group, a member, and what no field takes")
f = r["families"][0]
fn = f["neighbors"][0]
check((f["afi"], f["safi"]) == ("ipv4", "unicast") and f["networks"] == ["200.0.0.0/24"]
      and f["aggregates"] == [{"prefix": "200.0.0.0/22", "summary_only": True,
                               "as_set": True}]
      and f["redistribute"][0]["route_map"] == "RM" and f["other"] == ["table-map TM"],
      f"bgp_typed: the family's networks, aggregates, redistribution: {f['other']}")
check(fn["activate"] and fn["route_map_in"] == "PO-UP-IN" and fn["route_map_out"]
      == "PO-UP-OUT" and fn["prefix_list_in"] == "PL-IN" and fn["maximum_prefix"]
      == 1000 and fn["next_hop_self"] and fn["soft_reconfiguration_inbound"]
      and fn["default_originate"]
      and fn["other"] == ["neighbor 10.0.0.2 allowas-in 1"],
      f"bgp_typed: what the family says about a neighbour: {fn}")
flat = Config.parse(FLAT)
check(not flat.unknown and T.bgp_typed(flat) == [b[0]],
      "bgp_typed: the same block typed flat at the prompt reads the same")
check(T.find_bgp(cfg, "65010", "red")["neighbors"][0]["remote_as"] == "65020"
      and T.find_bgp(cfg, "65010") is None,
      "find_bgp: a router by asn and VRF")
_nb, _nf = {}, {}
check(T._neighbor(_nb, "1.1.1.1") is _nb["1.1.1.1"] and _nb["1.1.1.1"]["remote_as"] == ""
      and T._neighbor_af(_nf, "1.1.1.1")["activate"] is False,
      "_neighbor/_neighbor_af: an entry made once, with every field at its default")
T._bgp_neighbor_statement(_nb["1.1.1.1"], ["local-as", "65099"])
T._bgp_af_neighbor_statement(_nf["1.1.1.1"], ["weight", "10"])
check(_nb["1.1.1.1"]["other"] == ["neighbor 1.1.1.1 local-as 65099"]
      and _nf["1.1.1.1"]["other"] == ["neighbor 1.1.1.1 weight 10"],
      "_bgp_neighbor_statement/_bgp_af_neighbor_statement: no field, kept verbatim")
check(T._redistribute("redistribute ipv4 connected level-2".split())["protocol"]
      == "ipv4 connected"
      and T._redistribute("redistribute static metric 5 metric-type 2".split())
      ["metric_type"] == "2",
      "_redistribute: IS-IS's family-first spelling, metrics")

print("=== ospf / ospf6 / isis ===")
o = T.ospf_typed(cfg, 2)
check([(x["vrf"], x["router_id"]) for x in o] == [("", "1.1.1.1"), ("red", "9.9.9.9")],
      "ospf_typed: every router ospf, with its VRF, and not ospf6")
o = o[0]
check(o["passive_default"] and o["not_passive"] == ["ens18"] and o["passive"] ==
      ["ens19"] and o["networks"] == [{"prefix": "10.0.0.0/24", "area": "0"}]
      and o["default_information"] == "default-information originate always"
      and o["redistribute"][0]["metric"] == 20
      and o["other"] == ["timers throttle spf 10 100 1000"],
      f"ospf_typed: passive posture, networks, redistribution, the rest: {o['other']}")
a = {x["area"]: x for x in o["areas"]}
check(a["1"]["area_type"] == "stub" and a["1"]["no_summary"] and a["2"]["area_type"]
      == "nssa" and a["3"]["area_type"] == "normal"
      and a["3"]["other"] == ["area 3 range 10.3.0.0/16"],
      "ospf_typed: area types, and an area's other statements")
check(T._area_entry({}, "7")["area_type"] == "normal", "_area_entry: a new area is normal")
o6 = T.ospf_typed(cfg, 3)
check(len(o6) == 1 and o6[0]["version"] == 3 and o6[0]["router_id"] == "1.1.1.1",
      "ospf_typed(3): router ospf6, by its own router-id word")
i = T.isis_typed(cfg)
check(i == [{"tag": "HEXA", "vrf": "", "nets": ["49.0001.0000.0000.0001.00"],
             "is_type": "level-2-only", "metric_style": "wide",
             "redistribute": [T._redistribute(
                 "redistribute ipv4 connected level-2".split())],
             "other": ["set-overload-bit"]}],
      f"isis_typed: net, is-type, metric-style, redistribution, the rest: {i}")
ir = T.interface_routing_typed(cfg)
check(len(ir) == 1 and ir[0]["name"] == "ens18" and ir[0]["ospf_area"] == "0"
      and ir[0]["ospf_cost"] == 10 and ir[0]["ospf_network"] == "point-to-point"
      and (ir[0]["ospf_hello"], ir[0]["ospf_dead"]) == (5, 20)
      and ir[0]["ospf6_area"] == "0" and ir[0]["isis_instances"] == ["HEXA"]
      and ir[0]["isis_circuit_type"] == "level-2-only" and ir[0]["isis_metric"] == 20
      and ir[0]["isis_passive"] and ir[0]["other"] == ["ip pim"],
      f"interface_routing_typed: each FRR interface block's IGP settings: {ir}")
rt = T.routing_typed(cfg, {"static", "isis"})
check(sorted(rt) == ["isis", "static"], "routing_typed: only the areas asked for")
allt = T.config_typed(cfg)
check(set(allt) >= set(T.AREAS) - {"logging"} | {"box_owned"},
      "config_typed: no area named is every area, routing included")

print("=== edits ===")
check(T.static_line({"prefix": "10.1.0.0/16", "next_hop": "10.0.0.2", "distance": 9,
                     "tag": 3, "description": "x y", "extra": ["onlink"]})
      == "10.1.0.0/16 next-hop 10.0.0.2 onlink distance 9 tag 3 description x y",
      "static_line: the one line `router static` takes, in its order")
try:
    T.static_line({"prefix": "10.1.0.0/16"})
    check(False, "static_line refuses a route with no next hop")
except ValueError:
    check(True, "static_line refuses a route with no next hop")
cur = T.find_static(cfg, "10.77.0.0/16", "10.0.0.2")
check(T.static_lines(cur, {"prefix": "10.77.0.0/16", "next_hop": "10.0.0.2",
                           "description": ""}, ["description"])
      == ["10.77.0.0/16 next-hop 10.0.0.2 distance 200 tag 5"],
      "static_lines: a field named changes it, the rest kept - the whole route retyped")
check(T.edit_for(cfg, "static", {"prefix": "10.88.0.0/16", "next_hop": "10.0.0.2"})
      == (["router static"], ["10.88.0.0/16 next-hop 10.0.0.2"]),
      "edit_for(static): a new route, at router static")
try:
    T.edit_for(cfg, "static", {"prefix": "10.9.0.0/16", "next_hop": "10.0.0.9",
                               "vrf": "red"})
    check(False, "a VRF's route is not edited here")
except ValueError:
    check(True, "edit_for(static) refuses a VRF's route: it is a line in its vrf block")
check(T.bgp_path("65000") == ["router bgp 65000"]
      and T.bgp_path("65010", "red") == ["router bgp 65010 vrf red"],
      "bgp_path: the router block a neighbour is set in")
check(T._flag("1.1.1.1", "bfd", False, True) == ["neighbor 1.1.1.1 bfd"]
      and T._flag("1.1.1.1", "bfd", True, False) == ["no neighbor 1.1.1.1 bfd"]
      and T._flag("1.1.1.1", "bfd", True, True) == [],
      "_flag: on, off, or nothing when it already is")
check(T._value("1.1.1.1", "description", "a", "b") == ["neighbor 1.1.1.1 description b"]
      and T._value("1.1.1.1", "description", "a", "") == ["no neighbor 1.1.1.1 description a"]
      and T._value("1.1.1.1", "description", "a", "a") == [],
      "_value: set, removed, or nothing")
p, lines = T.edit_for(cfg, "bgp", {
    "asn": "65000", "neighbor": {"address": "10.0.0.2", "description": "new"},
    "families": [{"afi": "ipv4", "safi": "unicast", "settings": {
        "activate": True, "route_map_in": "PO-UP-IN", "maximum_prefix": 500,
        "next_hop_self": True, "soft_reconfiguration_inbound": True,
        "default_originate": True, "prefix_list_in": "PL-IN"}}]},
    ["description", "families"])
check(p == ["router bgp 65000"] and lines == [
    "neighbor 10.0.0.2 description new", "address-family ipv4 unicast",
    "no neighbor 10.0.0.2 route-map PO-UP-OUT out",
    "neighbor 10.0.0.2 maximum-prefix 500", "exit-address-family"],
    f"bgp_neighbor_lines: only what differs; a family given is set whole: {lines}")
new = T.bgp_neighbor_lines(None, {"neighbor": {
    "address": "10.0.0.50", "remote_as": "65050", "keepalive": 5, "holdtime": 15},
    "password": "abc"})
check(new == ["neighbor 10.0.0.50 remote-as 65050", "neighbor 10.0.0.50 timers 5 15",
              "neighbor 10.0.0.50 password abc"],
      f"bgp_neighbor_lines: a new neighbour from nothing: {new}")
check(T.bgp_neighbor_lines(T.find_bgp(cfg, "65000"), {"neighbor": {
    "address": "10.0.0.2", "remote_as": "65001"}, "password": None}, ["password"]) == [],
      "bgp_neighbor_lines: a password not given never clears one")
try:
    T.bgp_neighbor_lines(None, {"neighbor": {"address": "1.1.1.1"}},
                         ["holdtime"])
    check(False, "an unknown field is refused")
except ValueError as e:
    check("timers" in str(e), f"bgp_neighbor_lines: the fields named, by their names: {e}")
check(T.delete_lines("static", prefix="10.77.0.0/16", next_hop="10.0.0.2")
      == (["router static"], ["no 10.77.0.0/16 next-hop 10.0.0.2"]),
      "delete_lines: a route by prefix and hop")
dl = T.bgp_neighbor_delete_lines(cfg, "65000", "", "10.0.0.2")
check(dl[0] == "address-family ipv4 unicast" and dl.index("exit-address-family")
      < dl.index("no neighbor 10.0.0.2 description upstream")
      and dl[-1] == "no neighbor 10.0.0.2 remote-as 65001"
      and "no neighbor 10.0.0.2 password s3cret" in dl
      and "no neighbor 10.0.0.2 maximum-prefix 1000" in dl
      and "no neighbor 10.0.0.2" not in dl,
      f"bgp_neighbor_delete_lines: each statement removed, families first, remote-as last: {dl}")
check(T.bgp_neighbor_delete_lines(cfg, "65000", "", "10.0.0.3")[-1]
      == "no neighbor 10.0.0.3 peer-group PG",
      "bgp_neighbor_delete_lines: a peer-group member's membership goes last")
try:
    T.bgp_neighbor_delete_lines(cfg, "65000", "", "10.9.9.9")
    check(False, "a neighbour the block has not is refused")
except ValueError:
    check(True, "bgp_neighbor_delete_lines: a neighbour the block has not is refused")
check(T.delete_for(cfg, "bgp", "10.0.0.2", asn="65000")[0] == ["router bgp 65000"]
      and T.delete_for(cfg, "static", prefix="10.77.0.0/16", next_hop="10.0.0.2")
      == T.delete_lines("static", prefix="10.77.0.0/16", next_hop="10.0.0.2"),
      "delete_for: a neighbour read off the candidate, the rest as delete_lines")
try:
    T.delete_lines("bgp", "10.0.0.2", asn="65000")
    check(False, "delete_lines refuses a neighbour: it needs the candidate")
except ValueError:
    check(True, "delete_lines refuses a neighbour: it needs the candidate")
check(T.shown_lines(["neighbor 1.1.1.1 password abc", "no neighbor 1.1.1.1 password x",
                     "neighbor 1.1.1.1 description password-change"])
      == ["neighbor 1.1.1.1 password <set, not shown>",
          "no neighbor 1.1.1.1 password <set, not shown>",
          "neighbor 1.1.1.1 description password-change"],
      "shown_lines: a neighbour's password masked, a description saying it left alone")

print("=== the server's message walk ===")
# A stand-in for generated classes: a descriptor with fields that are
# scalars, repeated scalars, messages and repeated messages - the four
# shapes msg_from_dict/dict_from_msg walk.
pbmod = types.ModuleType("fake_pb_558c")
sys.modules["fake_pb_558c"] = pbmod


class _F:
    LABEL_REPEATED = 3

    def __init__(self, name, msg=None, rep=False):
        self.name, self.label = name, 3 if rep else 1
        self.message_type = types.SimpleNamespace(name=msg) if msg else None


def _msg(name, fields):
    def init(self, **kw):
        for f in fields:
            setattr(self, f.name, kw.get(
                f.name, [] if f.label == 3 else (_cls[f.message_type.name]()
                                                 if f.message_type else "")))
    c = type(name, (), {"__init__": init, "__module__": "fake_pb_558c",
                        "DESCRIPTOR": types.SimpleNamespace(fields=fields)})
    setattr(pbmod, name, c)
    return c


_cls = {}
_cls["Agg"] = _msg("Agg", [_F("prefix"), _F("summary_only")])
_cls["Fam"] = _msg("Fam", [_F("afi"), _F("networks", rep=True),
                           _F("aggregates", "Agg", rep=True)])
_cls["Nb"] = _msg("Nb", [_F("address")])
_cls["Edit"] = _msg("Edit", [_F("asn"), _F("password"), _F("neighbor", "Nb"),
                             _F("families", "Fam", rep=True)])
m = A.msg_from_dict(_cls["Fam"], {"afi": "ipv4", "networks": ["1.0.0.0/8"],
                                  "aggregates": [{"prefix": "2.0.0.0/8",
                                                  "summary_only": True}],
                                  "not_a_field": 1})
check(m.afi == "ipv4" and m.networks == ["1.0.0.0/8"]
      and m.aggregates[0].prefix == "2.0.0.0/8" and m.aggregates[0].summary_only,
      "msg_from_dict: scalars, repeated, nested messages; keys it has no field for ignored")
d = A.dict_from_msg(m)
check(d == {"afi": "ipv4", "networks": ["1.0.0.0/8"],
            "aggregates": [{"prefix": "2.0.0.0/8", "summary_only": True}]},
      f"dict_from_msg: the message back as the dict typedcfg reads: {d}")
e = A.bgp_edit_dict(_cls["Edit"](asn="65000", password="", neighbor=_cls["Nb"](
    address="1.1.1.1")))
check(e["password"] is None and e["neighbor"]["address"] == "1.1.1.1",
      "bgp_edit_dict: no password given is None, never an empty one that clears")

if fail:
    print(f"\n{fail} FAILURES")
    sys.exit(1)
print("\nALL TESTS PASSED")
