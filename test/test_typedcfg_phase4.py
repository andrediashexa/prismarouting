#!/usr/bin/env python3
"""
src/prismash/typedcfg.py, #558 phase 4: the rest of the configuration read
as fields - the system settings, the accounts, SNMP's users, the policy,
the route-maps and lists written for FRR, RPKI, the protocol attachments,
the VRF devices, the virtual routers, the bundles and the CGNAT globals -
the routing blocks' knobs as settings, and the system/VRF edits.

THE SECRETS. Every account, SNMP user, community and password that this
configuration holds is read here, and the checks below are what holds the
promise the proto makes: a client is told that a secret is there, never
what it is. The sample's secrets are distinctive strings, and the whole
typed answer is searched for them.

The values are checked against the VTY's own screens on a real box by
lab/api-grpc.sh (lab/api_phase4_check.py).
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src", "prismash"))
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


SAMPLE = """hostname edge-1
dns server 1.1.1.1
dns server 8.8.8.8
ssh port 2222
ssh permit-root-login no
api enabled yes
api listen-address 10.0.0.1
snmp enabled yes
snmp community SECRETCOMMUNITY
snmp trap-host 10.0.0.9 community SECRETCOMMUNITY inform
vrf red table 10
nat
 cgnat
  timeout udp 120
  block-holddown 90
  identity radius secret SECRETRADIUS port 1813
  session-table buckets 4096 memory 512
  memory 4
  exit
 exit
system
 memory
  control-plane 4
  exit
 cpu
  workers 3
  exit
 exit
user ops
 role administrator
 ssh-key ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAASECRETKEY ops@x
 exit
user viewer
 exit
snmp-user mon
 auth sha SECRETAUTHPASS
 priv aes SECRETPRIVPASS
 exit
policy-options
 prefix-list BOGONS
  10.0.0.0/8
  192.168.0.0/16
  exit
 community CUST members 65000:100 65000:200
 large-community BIG members 65000:1:2
 extended-community EXT members rt:65000:5
 as-path SHORT "^65001_"
 policy-statement UP
  term cust
   from community CUST
   then accept
   exit
  term bogon
   from prefix-list BOGONS
   then reject
   exit
  exit
 exit
interface ens18
 ipv4 address 10.0.0.1/24
 vrrp 5 address 10.0.0.254
 vrrp 5 priority 200
 vrrp 5 track ens19 decrement 50
 exit
interface ae0
 member ens20
 lacp rate fast
 exit
! routing
!
fib-policy bgp UP
ip prefix-list PL1 seq 5 permit 10.0.0.0/8 le 24
ipv6 prefix-list PL6 seq 10 deny 2001:db8::/32
bgp community-list standard CL1 permit 65000:1
bgp as-path access-list AP1 seq 5 permit ^65001_
ip protocol bgp route-map RM1
route-map RM1 permit 10
 match ip address prefix-list PL1
 set local-preference 150
 on-match next
exit
route-map PO-UP-IN permit 10
exit
rpki
 rpki cache tcp 10.0.0.50 3323 preference 1
 rpki polling_period 300
exit
router bgp 65000
 bgp log-neighbor-changes
 neighbor 10.0.0.2 remote-as 65001
 neighbor 10.0.0.2 capability extended-nexthop
 neighbor 10.0.0.2 maximum-prefix 1000
 address-family ipv4 unicast
  neighbor 10.0.0.2 remove-private-AS
  table-map RM1
 exit-address-family
exit
router ospf
 auto-cost reference-bandwidth 100000
 timers throttle spf 50 100 5000
exit
router isis CORE
 net 49.0001.0000.0000.0001.00
 domain-password md5 SECRETISISPASS
 set-overload-bit
exit
"""

cfg = Config.parse(SAMPLE)
check(not cfg.unknown, f"the sample parses whole: {cfg.unknown[:3]}")
everything = T.config_typed(cfg, ())
blob = json.dumps(everything)

print("=== no secret is ever read ===")
for secret in ("SECRETCOMMUNITY", "SECRETRADIUS", "SECRETKEY",
               "SECRETAUTHPASS", "SECRETPRIVPASS", "SECRETISISPASS"):
    check(secret not in blob, f"{secret} appears nowhere in the typed answer")

print("=== system (system_typed, _kv) ===")
s = T.system_typed(cfg)
check(s["hostname"] == "edge-1" and s["dns_servers"] == ["1.1.1.1", "8.8.8.8"],
      "hostname and resolvers, in order")
check(s["ssh"] == {"port": 2222, "listen_address": "0.0.0.0",
                   "permit_root_login": "no", "password_authentication": True},
      f"ssh, defaults included: {s['ssh']}")
check(s["api"] == {"enabled": True, "listen_address": "10.0.0.1", "port": 50051},
      "the API listener")
check(s["snmp"]["enabled"] and s["snmp"]["communities"] == 1
      and s["snmp"]["trap_hosts"] == [{"address": "10.0.0.9", "inform": True}]
      and s["snmp"]["enterprise_number"] == 67033,
      "SNMP: communities counted, the trap host without its community")
check(s["cpu"] == [{"key": "workers", "value": "3"}]
      and s["memory"] == [{"key": "control-plane", "value": "4"}],
      "cores and memory by role, as pairs")
check(T._kv({"b": 2, "a": 1}) == [{"key": "a", "value": "1"},
                                  {"key": "b", "value": "2"}],
      "_kv: a map as pairs in key order, values as text")

print("=== accounts (users_typed, snmp_users_typed) ===")
u = T.users_typed(cfg)
check(u == [{"name": "ops", "role": "administrator", "ssh_keys": 1,
             "password_set": False},
            {"name": "viewer", "role": "", "ssh_keys": 0, "password_set": False}],
      f"accounts: role, key count, password presence: {u}")
check(T.snmp_users_typed(cfg) == [{"name": "mon", "auth_protocol": "sha",
                                   "priv_protocol": "aes"}],
      "an SNMP user's protocols, not its passphrases")

print("=== policy (policy_typed, _filter_text) ===")
p = T.policy_typed(cfg)
check([x["name"] for x in p["prefix_lists"]] == ["BOGONS"]
      and p["prefix_lists"][0]["members"] == ["10.0.0.0/8", "192.168.0.0/16"],
      "a named prefix-list, members in order")
check(p["communities"] == [{"name": "CUST", "members": ["65000:100", "65000:200"]}]
      and p["extended_communities"] == [{"name": "EXT", "members": ["rt:65000:5"]}]
      and p["as_paths"] == [{"name": "SHORT", "regex": "^65001_"}],
      "communities, extended communities as rt:asn:v, AS paths")
st = p["statements"][0]
check(st["name"] == "UP" and [t["name"] for t in st["terms"]] == ["cust", "bogon"]
      and st["terms"][0]["from"] == [{"kind": "community", "values": ["CUST"]}]
      and st["terms"][0]["then"] == "accept" and st["terms"][1]["then"] == "reject",
      "a policy-statement's terms in order, conditions by kind, decisions")
check(not p["unread"], "nothing unread")
check(T._filter_text(("10.0.0.0/8", "upto", 24)) == "10.0.0.0/8 upto 24"
      and T._filter_text(("10.0.0.0/8", "exact", None)) == "10.0.0.0/8 exact",
      "_filter_text: a route-filter as the VTY writes it")

print("=== the routing half's lists, maps and attachments ===")
check(T._top_statements(cfg)[:1] == ["fib-policy bgp UP"],
      "_top_statements: the top-level routing statements, in order")
rm = T.route_maps_typed(cfg)
check([m["name"] for m in rm] == ["RM1"],
      "route_maps_typed: the operator's map, the compiled PO- one left out")
check(rm[0]["entries"] == [{"action": "permit", "sequence": 10,
                            "matches": ["ip address prefix-list PL1"],
                            "sets": ["local-preference 150"], "call": "",
                            "on_match": "next", "description": "", "other": []}],
      f"an entry's match, set and on-match: {rm[0]['entries']}")
check(T._list_entry("ip prefix-list X seq 5 permit 10.0.0.0/8".split(), 3)
      == (5, "permit", ["10.0.0.0/8"])
      and T._list_entry("bgp as-path access-list A deny ^1_".split(), 4)
      == (0, "deny", ["^1_"]),
      "_list_entry: with and without a sequence")
fl = T.frr_lists_typed(cfg)
check(fl["prefix_lists"][0] == {"family": "ipv4", "name": "PL1", "entries": [
        {"sequence": 5, "action": "permit", "prefix": "10.0.0.0/8", "ge": 0, "le": 24}]}
      and fl["prefix_lists"][1]["family"] == "ipv6",
      "FRR prefix-lists: family, entries, ge/le")
check({(b["kind"], b["style"], b["name"]) for b in fl["bgp_lists"]}
      == {("community", "standard", "CL1"), ("as-path", "", "AP1")},
      "community lists and AS-path access-lists")
r = T.rpki_typed(cfg)
check(r["caches"] == [{"transport": "tcp", "host": "10.0.0.50", "port": 3323,
                       "user": "", "source": "", "preference": 1}]
      and r["polling_period"] == 300, "RPKI caches and timers")
at = T.routing_attachments_typed(cfg)
check(at["attachments"] == [
        {"kind": "fib-policy", "family": "", "protocol": "bgp", "name": "UP"},
        {"kind": "protocol-route-map", "family": "ipv4", "protocol": "bgp",
         "name": "RM1"}] and not at["other"],
      f"attachments, nothing else left over: {at}")

print("=== VRF devices, virtual routers, bundles, CGNAT globals ===")
check(T.vrfs_typed(cfg) == [{"name": "red", "table": 10}], "vrfs_typed")
check(T.vrrp_typed(cfg) == [{"interface": "ens18", "vrid": 5,
                             "addresses": ["10.0.0.254"], "priority": 200,
                             "track": [{"interface": "ens19", "decrement": 50}]}],
      "vrrp_typed: per interface and VRID, its tracks")
check(T.bundles_typed(cfg) == [{"name": "ae0", "members": ["ens20"],
                                "lacp": "active", "rate": "fast", "hash": "l34"}],
      "bundles_typed: members and LACP settings")
g = T.cgnat_globals_typed(cfg)
check(g["timeouts"] == [{"key": "udp", "value": "120"}] and g["block_holddown"] == 90
      and g["identity_source"] == "radius" and g["identity_radius_port"] == 1813
      and g["identity_radius_secret_set"] and g["session_table_buckets"] == 4096
      and g["session_table_memory_mb"] == 512 and g["memory_gb"] == 4,
      f"cgnat_globals_typed: timeouts, hold-down, identity, session table: {g}")
check(set(T.system_areas_typed(cfg, {"vrfs", "bundles"})) == {"vrfs", "bundles"}
      and T.SYSTEM_AREAS == T.OTHER_AREAS,
      "system_areas_typed: only the areas asked; the two area lists agree")

print("=== routing knobs as settings (split_settings, _with_settings, "
      "routing_settings) ===")
rt = T.routing_typed(cfg, {"bgp", "ospf", "isis"})
b = rt["bgp"][0]
check(b["settings"] == [{"key": "bgp log-neighbor-changes", "value": ""}],
      "BGP: a flag knob, value empty")
check(b["neighbors"][0]["settings"] == [
        {"key": "capability", "value": "extended-nexthop"},
        {"key": "maximum-prefix", "value": "1000"}] and not b["neighbors"][0]["other"],
      "a neighbour's knobs, its `neighbor <address>` words stripped")
check(b["families"][0]["settings"] == [{"key": "table-map", "value": "RM1"}]
      and b["families"][0]["neighbors"][0]["settings"]
      == [{"key": "remove-private-AS", "value": ""}],
      "a family's knobs, and its neighbours'")
check(rt["ospf"][0]["settings"] == [
        {"key": "auto-cost reference-bandwidth", "value": "100000"},
        {"key": "timers throttle spf", "value": "50 100 5000"}],
      "OSPF: the longest key wins, the rest is the value")
check({"key": "domain-password", "value": "<set>"} in rt["isis"][0]["settings"],
      "an IS-IS password is <set>, never its text")
sset, rest = T.split_settings(["maximum-paths 4", "something else"], "ospf")
check(sset == [{"key": "maximum-paths", "value": "4"}] and rest == ["something else"],
      "split_settings: an unknown statement stays in `other`, verbatim")
blk = T._with_settings({"other": ["neighbor 1.2.3.4 passive"]}, "neighbor", 2)
check(blk["settings"] == [{"key": "passive", "value": ""}] and blk["other"] == [],
      "_with_settings: in place, the leading words dropped")
check(T.routing_settings({}) == {}, "routing_settings: nothing asked, nothing added")

print("=== edits (system_lines, vrf_lines, _get, _yes, edit_for, delete_for) ===")
check(T._get({"ssh": {"port": 22}}, "ssh.port") == 22
      and T._get({"hostname": "x"}, "hostname") == "x"
      and T._get({}, "ssh.port") is None, "_get: a flattened field")
check(T._yes(True) == "yes" and T._yes(0) == "no", "_yes")
cur = T.system_typed(cfg)
want = {"hostname": "edge-2", "dns_servers": ["1.1.1.1", "9.9.9.9"],
        "ssh": {"port": 2200, "password_authentication": False},
        "api": {"listen_address": ""}, "snmp": {"enabled": False}}
lines = T.system_lines(cur, want, ["hostname", "dns_servers", "ssh.port",
                                   "ssh.password_authentication",
                                   "api.listen_address", "snmp.enabled"])
check(lines == ["hostname edge-2", "no dns server 8.8.8.8", "dns server 9.9.9.9",
                "ssh port 2200", "ssh password-authentication no",
                "no api listen-address", "snmp enabled no"],
      f"system_lines: only what differs, an empty listen-address removed: {lines}")
check(T.system_lines(cur, cur, ["hostname", "ssh.port"]) == [],
      "nothing differs, nothing staged")
try:
    T.system_lines(cur, want, ["snmp.community"])
    check(False, "a community is not a typed field")
except ValueError as e:
    check("unknown field" in str(e), "a community is never a typed system field")
check(T.edit_for(cfg, "system", want, ["hostname"]) == ([], ["hostname edge-2"]),
      "edit_for system: at the configuration level")
check(T.vrf_lines(None, {"name": "blue", "table": 20}) == ["vrf blue table 20"]
      and T.vrf_lines({"name": "red", "table": 10}, {"name": "red", "table": 10}) == [],
      "vrf_lines: a new device, and nothing for one that is already so")
check(T.edit_for(cfg, "vrfs", {"name": "red", "table": 11}) == ([], ["vrf red table 11"]),
      "edit_for vrfs: a table changed")
check(T.delete_for(cfg, "vrfs", "red") == ([], ["no vrf red table"]),
      "delete_for vrfs")

print("=== the API's conversion (other_areas_pb) ===")


class _Msg:
    """A stand-in message: enough of protobuf for other_areas_pb's walk."""


class _Field:
    def __init__(self, name, msg=None, repeated=False):
        self.name, self.message_type = name, msg
        self.label = 3 if repeated else 1
        self.LABEL_REPEATED = 3


calls = []


class _Out:
    def __getattr__(self, field):
        class _Slot:
            def extend(_s, xs):
                calls.append(("extend", field, len(list(xs))))

            def CopyFrom(_s, x):
                calls.append(("copy", field))
        return _Slot()


saved = A.msg_from_dict
A.msg_from_dict = lambda cls, d: d
try:
    pb = type("pb", (), {m: object for _a, (_f, m, _r) in A.OTHER_FIELDS.items()})
    A.other_areas_pb(pb, _Out(), {"vrfs": [{"name": "red"}], "system": {}})
finally:
    A.msg_from_dict = saved
check(("extend", "vrfs", 1) in calls and ("copy", "system") in calls
      and len(calls) == 2,
      f"other_areas_pb: a list extended, a singular copied, absent areas skipped: {calls}")

print("=== the coverage rules (classify_phase4) ===")
spec = importlib.util.spec_from_file_location(
    "cov", os.path.join(HERE, "..", "tools", "api-coverage.py"))
cov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cov)
check(cov.classify_phase4("config", ["hostname", "<name>"])[0] == "typed"
      and "read and written" in cov.classify_phase4("config", ["hostname"])[2],
      "classify_phase4: hostname is read and written as a field")
check("written as lines" in cov.classify_phase4("config", ["snmp", "community"])[2],
      "an SNMP community is read as a field and written as a line")
check(cov.classify_phase4("router bgp", ["neighbor"]) is None,
      "a level phase 4 does not own: no answer")

print("=== GetSchema's three defects (prisma-web, on 0.149.22) ===")
import prismash as P  # noqa: E402
sch = T.schema(P)
words = [(m["mode"], st["words"]) for m in sch for st in m["statements"]]
check(not [x for x in words if any("<" in w or ">" in w or "|" in w for w in x[1])],
      "every schema word is a literal keyword: no placeholder, no alternation")
choices = [(m["mode"], st["words"], c) for m in sch for st in m["statements"]
           for f in st["forms"] for a in f for c in a["choices"]]
check(not [c for c in choices if "<" in c[2] or ">" in c[2]],
      "no choice value holds '<' or '>' (0.149.22 answered \"1-4294967295>\")")
check(not [a for m in sch for st in m["statements"] for f in st["forms"] for a in f
           if a["kind"] == "choice" and not a["choices"]],
      "no choice is empty")
by = {m["mode"]: m for m in sch}
ra = [st for st in by["bgp neighbor"]["statements"]
      if st["words"] == ["neighbor", "remote-as"]][0]
check(len(ra["forms"]) == 2
      and [(a["kind"], a["position"]) for a in ra["forms"][0]]
      == [("address", 1), ("integer", 2)]
      and ra["forms"][0][1]["min"] == 1 and ra["forms"][0][1]["max"] == 4294967295
      and ra["forms"][1][1]["choices"] == ["internal", "external"],
      "remote-as: words [neighbor, remote-as], <address> at 1, then an integer form "
      "and an internal|external form")
st_ = [st for st in by["router static"]["statements"] if st["words"] == ["next-hop"]][0]
check(st_["forms"][0][0]["kind"] == "prefix" and st_["forms"][0][0]["position"] == 0,
      "router static: the prefix is an argument at position 0, not a word")
sl = [st for st in by["cgnat"]["statements"] if st["words"] == ["logging", "syslog"]][0]
f0 = sl["forms"][0]
check(not f0[0]["unordered"] and all(a["unordered"] for a in f0[1:])
      and {a["group"] for a in f0[1:]} == {1, 2, 3, 4},
      "logging syslog: the collector positional, enterprise/port/facility/severity "
      "four order-free groups")
check(T.statement_matches(
    "logging syslog 10.95.200.196 port 1601 enterprise 67033 facility 16 severity 6", sl),
      "and the line FRR prints, port before enterprise, matches it")
check(not T.statement_matches("logging syslog 10.95.200.196 port 1601", sl),
      "a required group missing (enterprise) does not")
check(not T.statement_matches("logging syslog 10.0.0.1 enterprise 1 port 2 port 3", sl),
      "nor does a group twice")

print("=== every line the VTY renders matches the schema (unmatched_lines) ===")
rendered = cfg.text()
misses = T.unmatched_lines(sch, rendered)
check(not misses, f"the sample, rendered by Config.text(), matches line by line: {misses[:4]}")
x520 = """interface ens18
 description TESTE
 ipv4 address 10.95.200.198/24
 mtu 1500
 cgnat outside
 exit
nat
 cgnat
  pool rfc6598
   prefix-inside 100.64.0.0/10
   prefix-outside 200.0.0.0/24
   block-size 256
   exit
  logging syslog 10.95.200.196 port 1601 enterprise 67033 facility 16 severity 6
  logging udp 10.95.200.196 src 10.95.200.198 port 14739
  logging enable
  exit
 exit
ssh listen-address [::]
! routing
!
log syslog informational  <- not checked
service integrated-vtysh-config  <- not checked
router static
 0.0.0.0/0 next-hop 10.95.200.1
 exit
vrf prisma-bp  <- not checked
exit-vrf
router bgp 65000
 neighbor 10.95.200.1 remote-as 65000
exit
"""
check(not T.unmatched_lines(sch, x520),
      "a lab box's running config (.198's shape), box-owned lines left aside, matches")
check(T.unmatched_lines(sch, "interface ens18\n frobnicate 3\n exit\n")
      == [("interface ens18", "frobnicate 3")],
      "and a line no statement describes is reported, with its block")
# The three lines lab/api-grpc.sh found on the lab box's GetRunning before
# they were described: an operator VRF's FRR block, a neighbour's route-map
# with its direction, and an OSPF area's stub flag.
lab = ("vrf red\n ip route 198.51.100.0/24 192.0.2.1\n"
       " ipv6 route 2001:db8:9::/48 2001:db8::1 tag 7 20\nexit-vrf\n"
       "router bgp 65000\n address-family ipv4 unicast\n"
       "  neighbor 192.0.2.9 route-map PO-UP-IN in\n exit-address-family\nexit\n"
       "router ospf\n area 1 stub\n area 0.0.0.2 nssa no-summary\nexit\n")
check(not T.unmatched_lines(sch, lab),
      f"a VRF block, a route-map's direction, an area's stub: {T.unmatched_lines(sch, lab)}")

print("=== the generator's pieces ===")
check(T.split_alternatives("<1-4294967295>|internal|external")
      == ["<1-4294967295>", "internal", "external"]
      and T.split_alternatives("<A.B.C.D|X:X::X:X>") == ["<A.B.C.D|X:X::X:X>"],
      "split_alternatives: the | inside <...> kept")
check(T._is_placeholder("+<n>") and not T._is_placeholder("internal"),
      "_is_placeholder: a pattern around one counts")
check(T._clean("<n>") and T._clean("internal") and not T._clean("soo:<asn>:<value>")
      and not T._clean("+<n>"), "_clean: a single placeholder or a literal word")
check(T._alternation("inside|outside") == ["inside", "outside"]
      and T._alternation("rt|soo:<asn>:<value>") is None
      and T._alternation("word") is None,
      "_alternation: only clean alternatives split; a pattern stays whole")
check(T._kind_of("<1-255>") == ("integer", 1, 255)
      and T._kind_of("<A.B.C.D/M>")[0] == "ipv4_prefix"
      and T._kind_of("distance")[0] == "keyword", "_kind_of")
check(T._hint_tokens("<interface>, or a name that creates one") == [("<interface>", False)]
      and T._hint_tokens("<a> [port <n>]") == [("<a>", False), ("port", True), ("<n>", True)],
      "_hint_tokens: prose after a comma is not grammar; brackets optional")
forms = T._expand([("<1-9>|on|off", False)], {})
check([(a[0]["kind"], a[0]["choices"]) for a in forms]
      == [("integer", []), ("choice", ["on", "off"])],
      "_expand: a mixed alternation is one form per kind")
g = T.group_keywords([{"kind": "word", "optional": False},
                      {"kind": "keyword", "optional": False},
                      {"kind": "integer", "optional": False},
                      {"kind": "keyword", "optional": True}])
check([(a["group"], a["unordered"]) for a in g]
      == [(0, False), (1, True), (1, True), (2, True)],
      "group_keywords: a keyword and its values one group; positional before")
g = T.group_keywords([{"kind": "keyword", "optional": True},
                      {"kind": "integer", "optional": True},
                      {"kind": "choice", "optional": False},
                      {"kind": "prefix", "optional": False}])
check([(a["group"], a["unordered"]) for a in g]
      == [(1, True), (1, True), (0, False), (0, False)],
      "group_keywords: `[seq <n>] permit|deny <prefix>` - the required values "
      "after an optional group are positional, not the group's")
pl = [st for st in by["config"]["statements"] if st["words"] == ["ip", "prefix-list"]][0]
check(T.statement_matches("ip prefix-list PL seq 5 permit 10.0.0.0/8 le 24", pl)
      and T.statement_matches("ip prefix-list PL deny 10.0.0.0/8", pl)
      and T.statement_matches("ip prefix-list PL description the bogons", pl)
      and not T.statement_matches("ip prefix-list PL seq 5 10.0.0.0/8", pl),
      "a prefix-list line with and without seq, a description; no action is no match")
check(len(T.hint_forms("<a> | disable")) == 2 and T.hint_forms("") == [[]],
      "hint_forms: top-level alternatives are forms")
wk, args = T.key_words(["neighbor <address>", "remote-as"])
check(wk == ["neighbor", "remote-as"] and args[0]["position"] == 1
      and args[0]["kind"] == "address", "key_words: literal words, a placeholder positioned")
check(T._value_ok({"kind": "integer", "min": 1, "max": 5}, "3")
      and not T._value_ok({"kind": "integer", "min": 1, "max": 5}, "9")
      and T._value_ok({"kind": "choice", "choices": ["a"]}, "a")
      and not T._value_ok({"kind": "ipv6_address"}, "10.0.0.1")
      # '\u00b2' is isdigit() and not int(): the guard is config.is_number
      and not T._value_ok({"kind": "integer", "min": 0, "max": 0}, "\u00b2"),
      "_value_ok")
check(T.takes_rest({"kind": "text"}) and T.takes_rest({"kind": "word", "many": True})
      and not T.takes_rest({"kind": "word"}), "takes_rest: text, or a word that repeats")
check(T.form_matches(["neighbor", "1.2.3.4", "remote-as", "7"], ["neighbor", "remote-as"],
                     ra["forms"][0]), "form_matches: a placeholder among the words")
check(T._path_matches(["router bgp <asn>"], ["router bgp 65000"])
      and not T._path_matches(["router ospf"], ["router bgp 65000"])
      and T._path_matches([], []), "_path_matches")

print()
if fail:
    print(f"{fail} FAILURES")
    sys.exit(1)
print("ALL TESTS PASSED")
