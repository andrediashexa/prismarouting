#!/usr/bin/env python3
"""
src/prismash/policy.py, the routing-policy compiler (#458): what it reads,
what it refuses, and the FRR text it writes.

The FRR text was checked by FRR itself: the output of the example below,
fed to `vtysh -C -f` in the product's own FRR image, returned 0, and the same
file with one bad line appended returned 2 naming the line - so a silent 0
there is a verdict and not a check that read nothing. This suite pins the
text that was checked; lab/policy.sh measures what it does to routes.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src", "prismash"))
import policy  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        print(f"  FAIL: {msg}")
        fail += 1


def ok(msg):
    print(f"  [ ok ] {msg}")


P = "policy-options"
EXAMPLE = f"""{P} prefix-list CUSTOMERS 203.0.113.0/24
{P} prefix-list CUSTOMERS 2001:db8::/32
{P} community NOEXP members 65000:666
{P} community TAG members 65000:100 65000:200
{P} as-path PRIVATE "_6451[2-9]_"
{P} policy-statement UP-OUT term 10 from prefix-list CUSTOMERS
{P} policy-statement UP-OUT term 10 set as-path-prepend 65000 65000
{P} policy-statement UP-OUT term 10 set community add TAG
{P} policy-statement UP-OUT term 10 then accept
{P} policy-statement UP-OUT term 20 from route-filter 198.51.100.0/22 upto /24
{P} policy-statement UP-OUT term 20 then reject
{P} policy-statement UP-OUT term 30 from community NOEXP
{P} policy-statement UP-OUT term 30 set community delete NOEXP
{P} policy-statement UP-OUT term 30 then next term
{P} policy-statement UP-IN term 1 from as-path PRIVATE
{P} policy-statement UP-IN term 1 then reject
{P} policy-statement UP-IN term 2 set local-preference 200
""".strip().splitlines()

print("=== parse: the flat statements into the model ===")
pol, bad = policy.parse(EXAMPLE)
check(bad == [], f"the example reads clean: {bad}")
check(pol.prefix_lists["CUSTOMERS"] == ["203.0.113.0/24", "2001:db8::/32"],
      "a prefix list keeps its networks in the order typed")
check(pol.communities["TAG"] == ["65000:100", "65000:200"], "a community")
check(pol.as_paths["PRIVATE"] == "_6451[2-9]_", "an as-path, quotes gone")
check(list(pol.statements["UP-OUT"]) == ["10", "20", "30"],
      "terms in the order they were typed")
t = pol.statements["UP-OUT"]["20"]
check(t.filters == [("198.51.100.0/22", "upto", 24)] and t.then == "reject",
      f"a route-filter and a then: {t.filters} {t.then}")
check(policy.findings(pol) == [], "and nothing refers to a name not defined")
ok("the example reads, every kind of statement")

print("\n=== parse: what is refused, each with why ===")
for line, want in (
        (f"{P} prefix-list X 10.0.0.1/8", "host bits zero"),
        (f"{P} prefix-list bad/name 10.0.0.0/8", "a name is letters"),
        (f"{P} community C members 65000", "ASN:value"),
        (f"{P} community C members 70000:1", "ASN:value"),
        (f"{P} as-path A ((", "does not compile"),
        (f"{P} policy-statement P term 1 from route-filter 10.0.0.0/8 upto /4",
         "between 8 and 32"),
        (f"{P} policy-statement P term 1 from route-filter 10.0.0.0/8 maybe",
         "exact, orlonger, longer or upto"),
        (f"{P} policy-statement P term 1 set local-preference high",
         "set: local-preference, community"),
        (f"{P} policy-statement P term 1 then local-preference 200",
         "a change to the route is 'set ...'"),
        (f"{P} policy-statement P term 1 whatever", "a term takes"),
        (f"{P} nonsense", "expected: prefix-list"),
        ("not-policy line", "not a policy-options statement")):
    _, bad = policy.parse([line])
    check(len(bad) == 1 and want in bad[0][1],
          f"{line!r} should be refused with {want!r}: {bad}")
_p, _ = policy.parse([f"{P} policy-statement P term 1 from prefix-list NOPE",
                      f"{P} policy-statement P term 1 set community add NOC",
                      f"{P} policy-statement Q term 1 from community NOC",
                      f"{P} policy-statement Q term 1 from as-path NOA"])
_why = [w for _, w in policy.findings(_p)]
check(any("no prefix-list NOPE" in w for w in _why)
      and any("no community NOC" in w for w in _why)
      and any("no as-path NOA" in w for w in _why),
      f"a name a term uses and nothing defines is said: {_why}")
check(policy.name_error("x", "A" * 33) and not policy.name_error("x", "A-1.b"),
      "the name rule, at its bound")
check(policy.network_error("10.0.0.0/8") is None
      and policy.community_error("65535:65535") is None,
      "and what it takes")
ok("each refused with the reason")

print("\n=== route-filter: each kind as the prefix-list entry it becomes ===")
check(policy._filter_entry("10.0.0.0/8", "exact", None) == "10.0.0.0/8", "exact")
check(policy._filter_entry("10.0.0.0/8", "orlonger", None) == "10.0.0.0/8 le 32", "orlonger")
check(policy._filter_entry("10.0.0.0/8", "longer", None) == "10.0.0.0/8 ge 9 le 32", "longer")
check(policy._filter_entry("10.0.0.1/32", "longer", None) is None,
      "longer than a host route is nothing")
check(policy._filter_entry("10.0.0.0/8", "upto", 24) == "10.0.0.0/8 le 24", "upto /24")
check(policy._filter_entry("10.0.0.0/8", "upto", 8) == "10.0.0.0/8", "upto its own length")
check(policy._filter_entry("2001:db8::/32", "orlonger", None) == "2001:db8::/32 le 128",
      "an IPv6 network orlonger goes to 128")
ok("exact, orlonger, longer, upto")

print("\n=== compile: the FRR text, as FRR accepted it ===")
out = policy.compile_all(pol, {("UP-OUT", "export"), ("UP-IN", "import")})
text = "\n".join(out)
check(policy.map_name("UP-OUT", "export") == "PO-UP-OUT-OUT"
      and policy.map_name("UP-IN", "import") == "PO-UP-IN-IN",
      "a map per policy and direction, in the PO- namespace")
check("route-map PO-UP-OUT-OUT permit 10\n match ip address prefix-list "
      "PO-UP-OUT-T10-4\n" in text
      and "route-map PO-UP-OUT-OUT permit 11\n match ipv6 address "
      "prefix-list PO-UP-OUT-T10-6\n" in text,
      "a term with both families is two clauses, one each: FRR ANDs "
      "match ip with match ipv6 in one")
check("route-map PO-UP-OUT-OUT deny 20\n match ip address prefix-list "
      "PO-UP-OUT-T20-4\nexit" in text
      and "ip prefix-list PO-UP-OUT-T20-4 seq 5 permit 198.51.100.0/22 le 24"
      in text, "then reject is a deny clause, and upto is le")
check(" set as-path prepend 65000 65000\n set community 65000:100 "
      "65000:200 additive\nexit" in text,
      "then accept stops: no on-match next after its sets")
check(" set comm-list PO-C-NOEXP delete\n on-match next\nexit" in text
      and "bgp community-list standard PO-C-NOEXP seq 5 permit 65000:666"
      in text,
      "community delete names a list of its own, and next term goes on")
check("route-map PO-UP-IN-IN permit 20\n set local-preference 200\n "
      "on-match next\nexit" in text,
      "a term with no then applies its sets and goes on, as Junos does")
check("route-map PO-UP-OUT-OUT permit 65535\nexit" in text
      and "route-map PO-UP-IN-IN permit 65535\nexit" in text,
      "the Junos default: a route no term decided is accepted, by a clause "
      "of its own at 65535")
check(all(("PO-" in l) for l in out if not l.startswith((" ", "exit"))),
      "every FRR object is in the PO- namespace")
check(policy.compile_all(pol, {("UP-IN", "import"), ("UP-OUT", "export")})
      == out, "the same configuration compiles to the same text")
check(policy.compile_policy(pol, "UP-IN", "import")[-2:]
      == ["route-map PO-UP-IN-IN permit 65535", "exit"],
      "one policy on its own ends in its default")
ok("the text FRR's dry run accepted")

print("\n=== nest and unnest: the block a file carries, and back ===")
block = policy.nest(EXAMPLE)
check(block[0] == "policy-options" and block[-1] == " exit"
      and " prefix-list CUSTOMERS" in block and "  2001:db8::/32" in block
      and "  term 20" in block and "   then reject" in block,
      f"the nested block, as an operator types it: {block}")
back, bad = policy.unnest(list(enumerate(block, 1)))
_p2, _e2 = policy.parse([l for _, l in back])
_uses = {("UP-OUT", "export"), ("UP-IN", "import")}
check(bad == [] and _e2 == []
      and policy.compile_all(_p2, _uses) == policy.compile_all(pol, _uses),
      "read back, it compiles to exactly the same FRR text")
check(policy.nest([l for _, l in back]) == block, "and writes the same block")
check(policy.nest([]) == [], "no policy, no block")
_, bad = policy.unnest(list(enumerate(
    ["policy-options", " policy-statement P", "  from prefix-list X",
     "  exit", " exit"], 1)))
check(bad and "terms, 'term <name>'" in bad[0][2],
      f"a statement outside a term is said: {bad}")
_out, _ = policy.unnest(list(enumerate(["ssh port 22"], 1)))
check(_out == [(1, "ssh port 22")], "and a line outside the block passes")
ok("one shape, written and read")

print("\n=== the small pieces, each called on its own ===")
_pp = policy.Policies()
check(policy._parse_one(_pp, ["prefix-list", "L", "10.0.0.0/8"]) is None
      and _pp.prefix_lists == {"L": ["10.0.0.0/8"]}, "one statement in")
_t = policy.Term("1")
check(policy._parse_term(_t, ["then", "accept"]) is None
      and _t.then == "accept", "one term statement in")
check(policy._family("2001:db8::/32") == "ipv6"
      and policy._family("10.0.0.0/8") == "ip", "the family of a network")
_pp.communities["X"] = ["65000:1"]
_t.sets = [("community", ["set", "X"]), ("local-preference", ["50"])]
check(policy._set_lines(_pp, _t) == [" set community 65000:1",
                                     " set local-preference 50"],
      f"a term's sets as FRR writes them: {policy._set_lines(_pp, _t)}")
ok("parse one, term one, family, sets")

print("\n=== step 2: from protocol, tag and metric; set metric, tag, next-hop "
      "(#458) ===")
_L = [f"{P} prefix-list LOOPS 10.255.0.0/16",
      f"{P} policy-statement EXT term 1 from protocol static",
      f"{P} policy-statement EXT term 1 set metric 20",
      f"{P} policy-statement EXT term 1 set tag 7",
      f"{P} policy-statement EXT term 1 then accept",
      f"{P} policy-statement EXT term 2 from protocol direct",
      f"{P} policy-statement EXT term 2 from prefix-list LOOPS",
      f"{P} policy-statement EXT term 2 then accept",
      f"{P} policy-statement NH term 1 from tag 7",
      f"{P} policy-statement NH term 1 from metric 20",
      f"{P} policy-statement NH term 1 set next-hop 192.0.2.1",
      f"{P} policy-statement NH term 2 from protocol ospf",
      f"{P} policy-statement NH term 2 then reject"]
_pol, _bad = policy.parse(_L)
check(_bad == [] and _pol.statements["EXT"]["2"].protocols == ["connected"],
      f"direct is read as FRR's connected: {_bad}")
for _line, _want in ((f"{P} policy-statement X term 1 from protocol rip",
                      "from protocol: bgp, ospf"),
                     (f"{P} policy-statement X term 1 from tag x",
                      "from tag <0-4294967295>"),
                     (f"{P} policy-statement X term 1 set next-hop nowhere",
                      "is not an address")):
    _, _b = policy.parse([_line])
    check(_b and _want in _b[0][1], f"{_line!r}: {_b}")
check(policy.map_name("EXT", "ospf:static") == "PO-EXT-OSPF-STATIC",
      "an export into an IGP is a map per source protocol")
_ex = policy.export_lines("EXT", "ospf")
check(_ex[:1] == [" redistribute bgp route-map PO-EXT-OSPF-BGP"]
      and len(_ex) == len(policy.IGPS["ospf"]),
      f"OSPF: one redistribute per protocol it can take: {_ex}")
_ix = policy.export_lines("EXT", "isis")
check(" redistribute ipv6 ospf6 level-2 route-map PO-EXT-ISIS-OSPF" in _ix
      and " redistribute ipv4 static level-1 route-map PO-EXT-ISIS-STATIC"
      in _ix and len(_ix) == len(policy.IGPS["isis"]) * 4,
      f"IS-IS: both families and both levels, ospf6 for IPv6: {_ix}")
_st = "\n".join(policy.compile_policy(_pol, "EXT", "ospf:static"))
check("route-map PO-EXT-OSPF-STATIC permit 10\n set metric 20\n set tag 7"
      "\nexit" in _st and "route-map PO-EXT-OSPF-STATIC deny 65535" in _st
      and "LOOPS" not in _st and "PO-EXT-T2" not in _st,
      f"the static map: term 1 only, and the IGP default rejects: {_st}")
_bg = "\n".join(policy.compile_policy(_pol, "EXT", "ospf:bgp"))
check(_bg == "route-map PO-EXT-OSPF-BGP deny 65535\nexit",
      f"a protocol no term names exports nothing: {_bg!r}")
_nh = "\n".join(policy.compile_policy(_pol, "NH", "import"))
check(" match tag 7\n match metric 20\n set ip next-hop 192.0.2.1" in _nh
      and "PO-NH-IN deny 20" not in _nh
      and "route-map PO-NH-IN permit 65535" in _nh,
      f"on a neighbor: tag, metric, next-hop; the ospf-only term left out; "
      f"BGP's default still accepts: {_nh}")
check(policy.igp_findings(_pol, "EXT") == []
      and policy.igp_findings(policy.parse([
          f"{P} policy-statement B term 1 set local-preference 5",
          f"{P} community C members 1:1",
          f"{P} policy-statement B term 2 from community C"])[0], "B")
      == ["term 1: set local-preference is for BGP routes",
          "term 2: from community is for BGP routes"],
      "what only a BGP route has is said, per term, before an IGP export")
_twice = policy.compile_all(_pol, {("EXT", "ospf:connected"),
                                   ("EXT", "isis:connected")})
check(sum(1 for l in _twice if l.startswith("ip prefix-list")) == 1,
      f"a list two maps share is handed to FRR once: {_twice}")
ok("protocol, tag, metric; per-protocol maps; the IGP default; findings")

print("\n=== step 3: evaluate, the policy asked about a prefix (#458) ===")
_E = [f"{P} prefix-list C 203.0.113.0/24",
      f"{P} policy-statement UP term 10 from prefix-list C",
      f"{P} policy-statement UP term 10 set local-preference 200",
      f"{P} policy-statement UP term 10 then accept",
      f"{P} policy-statement UP term 20 from route-filter 198.51.100.0/22 "
      f"upto /24",
      f"{P} policy-statement UP term 20 then reject",
      f"{P} community K members 1:1",
      f"{P} policy-statement UP term 30 from community K",
      f"{P} policy-statement UP term 30 then reject"]
_pe, _ = policy.parse(_E)
_r = policy.evaluate(_pe, "UP", "203.0.113.0/24")
check(_r == [("10", "accept (set local-preference 200)", "matches")],
      f"a prefix a list holds is decided by the first term: {_r}")
_r = policy.evaluate(_pe, "UP", "198.51.100.0/23")
check([t for t, _, _ in _r] == ["10", "20"] and _r[-1][1] == "reject",
      f"a route-filter upto /24 takes a /23 inside it: {_r}")
_r = policy.evaluate(_pe, "UP", "198.51.100.0/25")
check(_r[1][1] == "no match"
      and _r[2] == ("30", "if the route's community match: reject",
                    "the prefix alone does not say")
      and _r[-1] == (None, "accept",
                     "no term decided: the default for this use"),
      f"longer than upto is no match; a community is not guessed, and the "
      f"walk goes on to BGP's default: {_r}")
check(policy.evaluate(_pe, "UP", "10.0.0.0/8", "ospf:static")[-1][1]
      == "reject", "into an IGP the default rejects")
check(policy._filter_matches(policy.ipaddress.ip_network("10.1.0.0/16"),
                             "10.0.0.0/8", "longer", None)
      and not policy._filter_matches(policy.ipaddress.ip_network("10.0.0.0/8"),
                                     "10.0.0.0/8", "longer", None)
      and policy._filter_matches(policy.ipaddress.ip_network("10.0.0.0/8"),
                                 "10.0.0.0/8", "exact", None)
      and not policy._filter_matches(
          policy.ipaddress.ip_network("2001:db8::/32"), "10.0.0.0/8",
          "orlonger", None),
      "_filter_matches: longer, exact, and another family")
ok("decided, not matched, not guessed, and each default")

print("\n=== a term whose prefix lists match nothing matches nothing (#484) ===")
# One mistyped list name compiled to a clause with no `match`, which FRR
# reads as "every route": on a BGP export, the whole table announced. And
# evaluate() - `test policy` - said "no match" for the same term.
_leak = policy.parse([
    "policy-options policy-statement EXPORT term t1 from prefix-list NAO-EXISTE",
    "policy-options policy-statement EXPORT term t1 then accept"])[0]
_out = policy.compile_policy(_leak, "EXPORT", "export")
check(not any(l.startswith("route-map PO-EXPORT-OUT permit 10") for l in _out),
      f"a term naming a missing prefix-list compiled to a clause: {_out}")
_empty = policy.parse([
    "policy-options prefix-list VAZIA",
    "policy-options policy-statement EXPORT term t1 from prefix-list VAZIA",
    "policy-options policy-statement EXPORT term t1 then accept"])[0]
_out = policy.compile_policy(_empty, "EXPORT", "export")
check(not any(l.startswith("route-map PO-EXPORT-OUT permit 10") for l in _out),
      f"a term naming an EMPTY prefix-list compiled to a clause: {_out}")
_good = policy.parse([
    "policy-options prefix-list MINHAS 203.0.113.0/24",
    "policy-options policy-statement EXPORT term t1 from prefix-list MINHAS",
    "policy-options policy-statement EXPORT term t1 then accept"])[0]
_out = policy.compile_policy(_good, "EXPORT", "export")
check("route-map PO-EXPORT-OUT permit 10" in _out
      and " match ip address prefix-list PO-EXPORT-Tt1-4" in _out,
      f"a term whose list has prefixes lost its clause or its match: {_out}")
check([w for _, w in policy.findings(_leak)]
      == ["policy-statement EXPORT term t1: no prefix-list NAO-EXISTE"],
      f"findings() does not name the missing list: {policy.findings(_leak)}")
ok("a missing or empty list matches nothing; a list with prefixes still does")

print("\n=== phase A (#535): what compiled before compiles exactly as before ===")
import policy_golden_fixtures as _gold  # noqa: E402
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "policy-golden-0.148.6.txt")) as _f:
    _golden = _f.read()
check(_gold.compiled() == _golden,
      "every fixture compiles to the text 0.148.6's compiler gave, byte for "
      "byte: a saved policy-options file applies as it did")
ok("backward compatibility, against the golden text")

print("\n=== phase A (#535): the new definitions, conditions and actions ===")
A = f"""{P} prefix-list CUST 203.0.113.0/24
{P} prefix-list CUST 2001:db8:100::/48
{P} community BH members 65535:666
{P} large-community LC members 65000:1:2
{P} large-community LCX members 65000:9:9
{P} extended-community EC members rt:65000:100 soo:65000:200
{P} policy-statement BOGONS term 1 from route-filter 10.0.0.0/8 orlonger
{P} policy-statement BOGONS term 1 then reject
{P} policy-statement BOGONS default-action accept
{P} policy-statement IN-A term rtbh from community BH
{P} policy-statement IN-A term rtbh from route-filter 0.0.0.0/0 upto /32
{P} policy-statement IN-A term rtbh set next-hop discard
{P} policy-statement IN-A term rtbh then accept
{P} policy-statement IN-A term rpki from validation-state invalid
{P} policy-statement IN-A term rpki then reject
{P} policy-statement IN-A term peers from neighbor 10.0.0.2
{P} policy-statement IN-A term peers from neighbor 10.1.0.2
{P} policy-statement IN-A term peers from origin igp
{P} policy-statement IN-A term peers from family inet
{P} policy-statement IN-A term peers set local-preference +20
{P} policy-statement IN-A term peers set weight 100
{P} policy-statement IN-A term peers set med -5
{P} policy-statement IN-A term peers set origin incomplete
{P} policy-statement IN-A term peers set large-community add LC
{P} policy-statement IN-A term peers set large-community delete LCX
{P} policy-statement IN-A term peers set extended-community add EC
{P} policy-statement IN-A term peers set as-path remove-private
{P} policy-statement IN-A term peers set next-hop peer-address
{P} policy-statement IN-A term peers then next policy
{P} policy-statement IN-A term sub from policy BOGONS
{P} policy-statement IN-A term sub from large-community LC
{P} policy-statement IN-A term sub from extended-community EC
{P} policy-statement IN-A term sub from local-preference 120
{P} policy-statement IN-A term sub from next-hop 2001:db8::1
{P} policy-statement IN-A term sub set med igp
{P} policy-statement IN-B term 1 from prefix-list CUST
{P} policy-statement IN-B term 1 set local-preference 300
{P} policy-statement IN-B term 1 then accept
{P} policy-statement IN-B default-action reject
{P} policy-statement OUT term 1 from prefix-list CUST
{P} policy-statement OUT term 1 set as-path-prepend last-as 3
{P} policy-statement OUT term 1 set next-hop self
{P} policy-statement OUT term 1 then accept
{P} policy-statement OSPF-EXT term 1 from protocol static
{P} policy-statement OSPF-EXT term 1 set metric-type 1
{P} policy-statement OSPF-EXT term 1 then accept""".splitlines()
pa, bad = policy.parse(A)
check(bad == [] and policy.findings(pa) == [], f"phase A reads clean: {bad}")
check(pa.large_communities == {"LC": ["65000:1:2"], "LCX": ["65000:9:9"]}
      and pa.ext_communities["EC"] == [("rt", "65000:100"),
                                       ("soo", "65000:200")]
      and pa.defaults == {"BOGONS": "accept", "IN-B": "reject"},
      "large and extended community sets, and the default-actions")
t = pa.statements["IN-A"]["peers"]
check(t.neighbors == ["10.0.0.2", "10.1.0.2"] and t.origins == ["igp"]
      and t.families == ["inet"] and t.then == "next policy",
      "neighbor ORed, origin, family, then next policy")
t = pa.statements["IN-A"]["sub"]
check(t.policies == ["BOGONS"] and t.local_prefs == ["120"]
      and t.next_hops == ["2001:db8::1"] and t.large_communities == ["LC"]
      and t.ext_communities == ["EC"]
      and pa.statements["IN-A"]["rpki"].validation == ["invalid"],
      "from policy, local-preference, next-hop, the community sets, rpki")
for line, want in (
        (f"{P} large-community L members 1:2", "ASN:value:value"),
        (f"{P} large-community L members 1:2:4294967296", "ASN:value:value"),
        (f"{P} extended-community E members xx:1:2", "rt:<asn>:<value>"),
        (f"{P} extended-community E members rt:70000:70000",
         "rt:<asn>:<value>"),
        (f"{P} extended-community E members rt:10.0.0.1:70000",
         "rt:<asn>:<value>"),
        (f"{P} policy-statement P default-action maybe",
         "default-action: accept or reject"),
        (f"{P} policy-statement P term 1 from neighbor nobody",
         "is not an address"),
        (f"{P} policy-statement P term 1 from family ipx", "inet or inet6"),
        (f"{P} policy-statement P term 1 from origin bgp",
         "igp, egp or incomplete"),
        (f"{P} policy-statement P term 1 from validation-state good",
         "valid, invalid or unknown"),
        (f"{P} policy-statement P term 1 from next-hop x", "not an address"),
        (f"{P} policy-statement P term 1 from local-preference -1",
         "from local-preference"),
        (f"{P} policy-statement P term 1 from color red", "from: prefix-list"),
        (f"{P} policy-statement P term 1 set weight 70000", "set weight"),
        (f"{P} policy-statement P term 1 set med up", "set med"),
        (f"{P} policy-statement P term 1 set origin bgp", "set origin"),
        (f"{P} policy-statement P term 1 set large-community drop L",
         "set large-community add|set|delete"),
        (f"{P} policy-statement P term 1 set extended-community delete E",
         "set extended-community add|set"),
        (f"{P} policy-statement P term 1 set as-path rewrite",
         "set as-path remove-private"),
        (f"{P} policy-statement P term 1 set metric-type 3",
         "set metric-type 1|2"),
        (f"{P} policy-statement P term 1 set as-path-prepend last-as 11",
         "last-as <1-10>"),
        (f"{P} policy-statement P term 1 set color red", "set: local-pref"),
        (f"{P} policy-statement P term 1 then next hop", "next policy")):
    _, b = policy.parse([line])
    check(b and want in b[0][1], f"{line!r}: {b}")
_two = policy.Term("t")
check(policy._parse_term(_two, ["from", "policy", "A"]) is None
      and "calls one policy" in policy._parse_term(_two, ["from", "policy",
                                                          "B"]),
      "a term calls one policy: FRR holds one `call` a clause")
check(policy.large_community_error("1:2:3") is None
      and policy.ext_community_error("rt:65000:4294967295") is None
      and policy.ext_community_error("soo:192.0.2.1:7") is None
      and policy.ext_community_error("rt:4200000000:65536") is not None,
      "a 2-byte ASN leaves 32 bits for the value, a 4-byte one or an "
      "address 16")
_t = policy.Term("x")
check(policy._parse_from_a(_t, ["from", "family", "inet6"]) is None
      and policy._parse_from_a(_t, ["from", "family", "inet6"]) is None
      and _t.families == ["inet6"], "_parse_from_a: a family said twice is "
      "one")
check(policy._parse_set_a(_t, ["set", "weight", "7"]) is None
      and _t.sets == [("weight", ["7"])], "_parse_set_a: one action in")
ok("each new statement read, each refusal said")

print("\n=== phase A: the FRR text (vtysh -C in the product's image: 0) ===")
uses = {("IN-A+IN-B", "import"), ("OUT", "export"),
        ("OSPF-EXT", "ospf:static")}
out = "\n".join(policy.compile_all(pa, uses))
check(policy.chain("IN-A+IN-B") == ["IN-A", "IN-B"]
      and policy.chain_name(["IN-A", "IN-B"]) == "IN-A+IN-B"
      and policy.map_name("IN-A+IN-B", "import") == "PO-IN-A+IN-B-IN",
      "a chain is one map named for its policies")
check("route-map PO-IN-A+IN-B-IN permit 10\n match ip address prefix-list "
      "PO-IN-A-Trtbh-4\n match community PO-IN-A-Trtbh-C\n set ip next-hop "
      "192.0.2.1\nexit" in out
      and "ip route 192.0.2.1/32 blackhole tag 4294967294" in out
      and "ipv6 route 100::1/128 blackhole tag 4294967294" in out,
      "RTBH: discard is the TEST-NET-1 next hop, and the blackhole route the "
      "compiler emits beside it, tagged")
check("route-map PO-IN-A+IN-B-IN deny 20\n match rpki invalid\nexit" in out,
      "validation-state invalid is FRR's match rpki invalid")
check(" match peer 10.0.0.2\n match origin igp\n set local-preference +20"
      "\n set weight 100\n set metric -5\n set origin incomplete\n"
      " set large-community 65000:1:2 additive\n set large-comm-list "
      "PO-L-LCX delete\n set extcommunity rt 65000:100\n"
      " set extcommunity soo 65000:200\n set as-path exclude "
      "as-path-access-list PO-PRIVATE-AS\n set ip next-hop peer-address\n"
      " on-match goto 1000\nexit" in out
      and "route-map PO-IN-A+IN-B-IN permit 31\n match ip address "
      "prefix-list PO-IN-A-Tpeers-4\n match peer 10.1.0.2" in out
      and "ip prefix-list PO-IN-A-Tpeers-4 seq 5 permit 0.0.0.0/0 le 32"
      in out,
      "two neighbors are two clauses; family inet with no prefix is every "
      "IPv4 prefix; then next policy jumps to the next policy's block")
check(" match large-community PO-IN-A-Tsub-L\n match extcommunity "
      "PO-IN-A-Tsub-E\n match local-preference 120\n match ipv6 next-hop "
      "address 2001:db8::1\n call PO-BOGONS-SUB-IN\n set metric igp\n"
      " on-match next\nexit" in out
      and "bgp extcommunity-list standard PO-IN-A-Tsub-E seq 5 permit rt "
      "65000:100 soo 65000:200" in out
      and "route-map PO-BOGONS-SUB-IN deny 10" in out
      and "route-map PO-BOGONS-SUB-IN permit 65534" in out,
      "from policy calls the subroutine map, compiled once with its own "
      "default-action")
check("route-map PO-IN-A+IN-B-IN permit 1010\n match ip address prefix-list "
      "PO-IN-B-T1-4" in out
      and "route-map PO-IN-A+IN-B-IN deny 1999\nexit" in out
      and "route-map PO-IN-A+IN-B-IN permit 65535" in out,
      "the second policy from 1000, its default-action reject at the end "
      "of its block")
check(" set as-path prepend last-as 3\n set ip next-hop peer-address\nexit"
      in out and " set ipv6 next-hop peer-address" in out,
      "export: last-as, and next-hop self as FRR's outbound peer-address")
check("route-map PO-OSPF-EXT-OSPF-STATIC permit 10\n set metric-type "
      "type-1\nexit" in out, "metric-type on an export into OSPF")
_es, _ = policy.parse([f"{P} extended-community E members rt:1:2",
                      f"{P} policy-statement X term 1 set extended-community "
                      "set E"])
check(policy._set_lines(_es, _es.statements["X"]["1"])
      == [" set extcommunity none", " set extcommunity rt 1:2"],
      "extended-community set clears first: FRR's set merges, and takes no "
      "additive")
check(policy.compile_all(pa, uses) == policy.compile_all(pa, set(uses)),
      "deterministic")
check(policy._compile_term(pa, "OUT", pa.statements["OUT"]["1"], 1, 0,
                           65535, "M", "bgp", "export")[1][0]
      == "route-map M permit 10", "_compile_term: one term's clauses")
check(policy.sub_map_name("X", "ospf:static") == "PO-X-SUB-OSPF-STATIC"
      and policy._set_word("as-path-remove-private")
      == "as-path remove-private",
      "sub_map_name and _set_word")
ok("the text FRR accepted, line by line")

print("\n=== phase A: where a policy may be used, and what it reaches ===")
check(policy.reached(pa, ["IN-A", "IN-B"]) == ["IN-A", "IN-B", "BOGONS"],
      "reached: the chain, then what it calls")
check(policy.use_findings(pa, "IN-A+IN-B", "import") == []
      and policy.use_findings(pa, "OUT", "export") == [],
      "a correct use says nothing")
check(policy.use_findings(pa, "OUT", "import")
      == ["OUT term 1: set next-hop self is for an export to a BGP neighbor"]
      and policy.use_findings(pa, "IN-A", "export")
      == ["IN-A term peers: set next-hop peer-address is for an import"]
      and policy.use_findings(pa, "OSPF-EXT", "import")
      == ["OSPF-EXT term 1: set metric-type is for an export into OSPF"]
      and policy.use_findings(pa, "OSPF-EXT", "isis:static")
      == ["OSPF-EXT term 1: set metric-type is for an export into OSPF"],
      "self on an import, peer-address on an export, metric-type outside "
      "OSPF: each refused where the policy is applied")
_ig = policy.use_findings(pa, "IN-A", "ospf:static")
check(any("from neighbor" in w and "from origin" in w for w in _ig)
      and any("from validation-state" in w for w in _ig)
      and all("is for an import" not in w for w in _ig),
      f"exported into an IGP, BGP's conditions are said once: {_ig}")
_cy, _ = policy.parse([f"{P} policy-statement A term 1 from policy B",
                       f"{P} policy-statement B term 1 from policy A"])
check(policy._call_cycles(_cy) == [
    "policy-statement A: from policy reaches itself again",
    "policy-statement B: from policy reaches itself again"]
      and any("reaches itself" in w for _, w in policy.findings(_cy)),
      "a policy that calls itself round is refused at commit")
_many, _ = policy.parse(
    [f"{P} policy-statement M term 1 from neighbor 10.0.0.{i}"
     for i in range(1, 7)]
    + [f"{P} policy-statement M term 1 from origin igp",
       f"{P} policy-statement M term 1 from origin egp"])
check(len(policy._combinations(_many.statements["M"]["1"])) == 12
      and any("more than 10 combinations" in w
              for _, w in policy.findings(_many)),
      "twelve combinations would collide with the next term's numbers")
_un, _ = policy.parse([f"{P} policy-statement U term 1 from large-community "
                       "NOPE", f"{P} policy-statement U term 1 set "
                       "extended-community add NADA",
                       f"{P} policy-statement U term 2 from policy GONE"])
check([w for _, w in policy.findings(_un)] == [
    "policy-statement U term 1: no large-community NOPE",
    "policy-statement U term 1: no extended-community NADA",
    "policy-statement U term 2: no policy-statement GONE"],
      f"names nothing defines: {policy.findings(_un)}")
check(policy._term_findings_a(_un, _un.statements["U"]["2"])
      == ["no policy-statement GONE"],
      "_term_findings_a: one term's phase A names")
ok("use_findings, reached, cycles, combinations, names")

print("\n=== phase A: the block, default-action, test policy ===")
blk = policy.nest(A)
check("  default-action accept" in blk
      and blk.index("  default-action accept")
      < blk.index(" policy-statement BOGONS") + 3
      and " large-community LC members 65000:1:2" in blk,
      "default-action written first in its policy; the sets as one line")
back, bad = policy.unnest(list(enumerate(blk, 1)))
pb, eb = policy.parse([l for _, l in back])
check(bad == [] and eb == [] and policy.compile_all(pb, uses)
      == policy.compile_all(pa, uses), "and read back to the same text")
r = policy.evaluate(pa, "IN-B", "198.51.100.0/24")
check(r[-1] == (None, "reject", "no term decided: this policy's "
                "default-action"), f"default-action decides: {r}")
r = policy.evaluate(pa, "IN-A", "2001:db8::/32")
check(r[2] == ("peers", "no match", "the prefix is not inet")
      and "called policy" in r[3][1] and "next-hop" in r[3][1],
      f"the family answered by the prefix; the rest said, not guessed: {r}")
_np, _ = policy.parse([f"{P} policy-statement N term 1 then next policy"])
check(policy.evaluate(_np, "N", "10.0.0.0/8")[-1][1] == "accept"
      and policy.evaluate(_np, "N", "10.0.0.0/8", "ospf:static")[-1][1]
      == "reject",
      "next policy, alone, is the use's default")
ok("the block and the test")

print("\n=== phase B (#536): what phase A compiled compiles as before ===")
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "policy-golden-0.148.9.txt")) as _f:
    _golden_a = _f.read()
check(_gold.compiled(_gold.FIX_A) == _golden_a,
      "phase A's fixture compiles to the text 0.148.9's compiler gave, byte "
      "for byte")
ok("backward compatibility, against 0.148.9")

print("\n=== phase B (#536): the attach points' maps ===")
B = f"""{P} prefix-list UP 10.9.0.0/16
{P} policy-statement COND term 1 from prefix-list UP
{P} policy-statement COND term 1 then accept
{P} policy-statement FIB term 1 from protocol static
{P} policy-statement FIB term 1 set src 10.40.0.1
{P} policy-statement FIB term 1 then accept
{P} policy-statement FIB term 2 from route-filter 10.66.0.0/16 orlonger
{P} policy-statement FIB term 2 then reject
{P} policy-statement SIX term 1 set src 2001:db8::1
{P} policy-statement LP term 1 set local-preference 5
{P} policy-statement TBL term 1 set metric 7
{P} policy-statement OD term 1 set metric-type 1
{P} policy-statement PROTO term 1 from protocol static""".splitlines()
pb, bad = policy.parse(B)
check(bad == [] and pb.statements["FIB"]["1"].sets
      == [("src", ["10.40.0.1"])], f"set src read: {bad}")
check(policy._parse_set_a(policy.Term("s"), ["set", "src", "nowhere"])
      == "set src <address>", "set src takes an address")
check(policy.map_name("COND", "bgp:default") == "PO-COND-BGP-DEFAULT"
      and policy.map_name("FIB", "fib:static") == "PO-FIB-FIB-STATIC"
      and policy.map_name("OD", "isis:default") == "PO-OD-ISIS-DEFAULT",
      "each attach point named as an IGP export's part is")
check([policy.use_origin(d) for d in ("import", "ospf:static", "bgp:table",
                                      "fib:isis", "ospf:default")]
      == ["bgp", "static", "bgp", "isis", None],
      "use_origin: whose routes each map sees")
check([policy.use_default(d) for d in ("import", "ospf:static",
                                       "bgp:default", "bgp:network",
                                       "bgp:suppress", "bgp:table",
                                       "fib:bgp", "isis:default")]
      == ["permit", "deny", "deny", "permit", "deny", "permit", "permit",
          "deny"], "use_default: what a route no term decided gets")
check(policy._verdict("bgp:advertise") == "reject"
      and policy._verdict("fib:static") == "accept",
      "_verdict: the default as test policy says it")
check(policy.term_families(pb.statements["FIB"]["1"]) == ["inet"]
      and policy.term_families(pb.statements["SIX"]["1"]) == ["inet6"]
      and policy.term_families(pb.statements["LP"]["1"]) == [],
      "term_families: a source address makes the term its family's")
out = "\n".join(policy.compile_all(pb, {("FIB", "fib:static"),
                                        ("COND", "bgp:default"),
                                        ("COND", "ospf:default"),
                                        ("SIX", "fib:bgp")}))
check("route-map PO-FIB-FIB-STATIC permit 10\n match ip address "
      "prefix-list PO-FIB-T1-4\n set src 10.40.0.1\nexit" in out
      and "ip prefix-list PO-FIB-T1-4 seq 5 permit 0.0.0.0/0 le 32" in out
      and "route-map PO-FIB-FIB-STATIC deny 20" in out
      and "route-map PO-FIB-FIB-STATIC permit 65535" in out,
      "a FIB policy: set src on IPv4 routes, a reject, installed by default")
check(" match ipv6 address prefix-list PO-SIX-T1-6\n set src 2001:db8::1"
      in out and "route-map PO-COND-BGP-DEFAULT deny 65535" in out
      and "route-map PO-COND-OSPF-DEFAULT deny 65535" in out,
      "an IPv6 source on IPv6 routes; a condition's default is no route")
check(policy._set_lines(pb, pb.statements["FIB"]["1"], "ipv6") == []
      and policy._set_lines(pb, pb.statements["FIB"]["1"])
      == [" set src 10.40.0.1"],
      "_set_lines: a source address only on its family's clause")
check(policy._compile_term(pb, "PROTO", pb.statements["PROTO"]["1"], 1, 0,
                           65535, "M", None, "ospf:default")[1][0]
      == "route-map M permit 10",
      "_compile_term: no origin, no protocol filter")
for _pol, _use, _why in (
        ("FIB", "bgp:default", "set src is for a fib-policy"),
        ("LP", "fib:static", "set local-preference is for BGP routes"),
        ("TBL", "fib:static", "set metric does nothing on a fib-policy: it "
                              "takes set src"),
        ("LP", "bgp:table", "set local-preference does nothing on "
                            "table-policy: it takes set metric and set "
                            "next-hop"),
        ("TBL", "bgp:suppress", "set metric does nothing on "
                                "aggregate-address suppress-policy"),
        ("TBL", "bgp:condition", "does nothing on advertise condition"),
        ("OD", "isis:default", "set metric-type is for an export into OSPF"),
        ("PROTO", "ospf:default", "from protocol has nothing to match on "
                                  "ospf default-information")):
    _got = policy.use_findings(pb, _pol, _use)
    check(any(_why in w for w in _got), f"{_pol} as {_use}: {_got}")
check(policy.use_findings(pb, "LP", "fib:bgp")
      == ["LP term 1: set local-preference is for BGP routes"],
      "a BGP set on a FIB policy is said once")
for _pol, _use in (("FIB", "fib:static"), ("TBL", "bgp:table"),
                   ("OD", "ospf:default"), ("LP", "bgp:network"),
                   ("COND", "bgp:advertise"), ("LP", "bgp:aggregate")):
    check(policy.use_findings(pb, _pol, _use) == [],
          f"{_pol} as {_use} says nothing: "
          f"{policy.use_findings(pb, _pol, _use)}")
r = policy.evaluate(pb, "FIB", "2001:db8::/32", "fib:static")
check(r[0] == ("1", "no match", "the prefix is not inet")
      and r[-1] == (None, "accept", "no term decided: the default for this "
                    "use"), f"test policy for a FIB use: {r}")
check(policy.evaluate(pb, "COND", "10.1.0.0/16", "bgp:default")[-1][1]
      == "reject", "a condition's default is reject")
ok("set src, each use's origin, default and refusals")

print("\n=== phase C (#537): what phase B compiled compiles as before ===")
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "policy-golden-0.148.10.txt")) as _f:
    _golden_b = _f.read()
check(_gold.compiled(_gold.FIX_B) == _golden_b,
      "phase B's fixture compiles to the text 0.148.10's compiler gave, "
      "byte for byte")
check(_gold.compiled(_gold.FIX_A) == _golden_a
      and _gold.compiled() == _golden,
      "and phase A's, and 0.148.6's, still")
ok("backward compatibility, against 0.148.10")

print("\n=== phase C (#537): a leak's map ===")
C = f"""{P} prefix-list SVC 203.0.113.0/24
{P} community SHARED members 65000:537
{P} policy-statement LEAK term svc from prefix-list SVC
{P} policy-statement LEAK term svc from source-vrf svc
{P} policy-statement LEAK term svc from source-vrf blue
{P} policy-statement LEAK term svc set local-preference 200
{P} policy-statement LEAK term svc set community add SHARED
{P} policy-statement LEAK term svc then accept
{P} policy-statement DEF term 1 from route-filter 0.0.0.0/0 exact
{P} policy-statement DEF term 1 from source-vrf default
{P} policy-statement DEF term 1 then accept
{P} policy-statement NH term 1 set next-hop self
{P} policy-statement EC term 1 set extended-community add X
{P} policy-statement NB term 1 from neighbor 10.0.0.2""".splitlines()
pc, bad = policy.parse(C)
check(bad == [] and pc.statements["LEAK"]["svc"].source_vrfs
      == ["svc", "blue"], f"from source-vrf read, ORed: {bad}")
check(policy._parse_from_a(policy.Term("t"),
                           ["from", "source-vrf", "a/b"]).startswith(
          "from source-vrf: 'a/b' is not a VRF name")
      and policy.vrf_name_error("x" * 16) is not None
      and policy.vrf_name_error("default") is None,
      "a source VRF is a VRF's name, or default")
check(policy.map_name("LEAK", "bgp:vrfimport") == "PO-LEAK-BGP-VRFIMPORT"
      and policy.use_origin("bgp:vrfimport") == "bgp"
      and policy.use_default("bgp:vrfimport") == "deny"
      and policy._verdict("bgp:vrfimport") == "reject",
      "vrf-import: named as an attach point, BGP's routes, nothing by default")
out = "\n".join(policy.compile_all(pc, {("LEAK", "bgp:vrfimport"),
                                        ("DEF", "bgp:vrfimport")}))
check("route-map PO-LEAK-BGP-VRFIMPORT permit 10\n match ip address "
      "prefix-list PO-LEAK-Tsvc-4\n match source-vrf svc\n set "
      "local-preference 200\n set community 65000:537 additive\nexit" in out
      and "route-map PO-LEAK-BGP-VRFIMPORT permit 11\n match ip address "
      "prefix-list PO-LEAK-Tsvc-4\n match source-vrf blue\n" in out
      and "route-map PO-LEAK-BGP-VRFIMPORT deny 65535" in out
      and " match source-vrf default" in out,
      f"one clause per source VRF, a reject by default:\n{out}")
for _pol, _use, _why in (
        ("NH", "bgp:vrfimport", "set next-hop does nothing on vrf-import"),
        ("EC", "bgp:vrfimport", "set extended-community does nothing on "
                                "vrf-import"),
        ("NB", "bgp:vrfimport", "from neighbor matches nothing on "
                                "vrf-import"),
        ("LEAK", "import", "from source-vrf is for a vrf-import"),
        ("LEAK", "bgp:table", "from source-vrf is for a vrf-import"),
        ("DEF", "ospf:static", "from source-vrf is for BGP routes")):
    _got = policy.use_findings(pc, _pol, _use)
    check(any(_why in w for w in _got), f"{_pol} as {_use}: {_got}")
check(policy.use_findings(pc, "DEF", "ospf:static")
      == ["DEF term 1: from source-vrf is for BGP routes"],
      "said once on an IGP export")
check(policy.use_findings(pc, "LEAK", "bgp:vrfimport") == []
      and policy.use_findings(pc, "DEF", "bgp:vrfimport") == [],
      "a leak's conditions and actions say nothing at vrf-import")
r = policy.evaluate(pc, "LEAK", "203.0.113.0/24", "bgp:vrfimport")
check("source-vrf" in r[0][1] and r[-1][1] == "reject",
      f"test policy: the source VRF is not guessed, a reject by default: {r}")
ok("from source-vrf, the leak's map, its refusals")

print("\n=== phase D (#538): counters, and the simulation ===")
pd, _ = policy.parse(f"""{P} prefix-list C 203.0.113.0/24
{P} prefix-list C 2001:db8::/32
{P} community T members 65001:10
{P} community NEW members 65000:1 65000:2
{P} large-community L members 65001:1:2
{P} extended-community E members rt:65001:5
{P} as-path PRIV "_6451[2-9]_"
{P} policy-statement A term cust from prefix-list C
{P} policy-statement A term cust set local-preference +10
{P} policy-statement A term cust set community add NEW
{P} policy-statement A term cust then next policy
{P} policy-statement A term tagged from community T
{P} policy-statement A term tagged then reject
{P} policy-statement A default-action reject
{P} policy-statement B term priv from as-path PRIV
{P} policy-statement B term priv set as-path remove-private
{P} policy-statement B term priv set med igp
{P} policy-statement B term priv then accept
{P} policy-statement B term ospf from protocol ospf
{P} policy-statement B term ospf then accept
{P} policy-statement B term rest set as-path-prepend last-as 2
{P} policy-statement B term rest set community delete T
{P} policy-statement B term rest set next-hop self
{P} policy-statement CALL term 1 from policy GATE
{P} policy-statement CALL term 1 set large-community set L
{P} policy-statement CALL term 1 set extended-community add E
{P} policy-statement CALL term 1 then accept
{P} policy-statement GATE term no from route-filter 10.0.0.0/8 orlonger
{P} policy-statement GATE term no then reject
{P} policy-statement RPKI term v from validation-state valid
{P} policy-statement RPKI term v then accept
{P} policy-statement RPKI term n from neighbor 192.0.2.1
{P} policy-statement RPKI term n from neighbor 192.0.2.2
{P} policy-statement RPKI term n set next-hop peer-address
{P} policy-statement RPKI term n then accept
{P} policy-statement TBL term 1 set local-preference 50
{P} policy-statement TBL term 1 set metric 9
{P} policy-statement TBL term 1 then accept""".splitlines())
check(policy.clause_owner(pd, "A", 10) == ("A", "cust")
      and policy.clause_owner(pd, "A", 11) == ("A", "cust")
      and policy.clause_owner(pd, "A", 20) == ("A", "tagged")
      and policy.clause_owner(pd, "A", 65534) == ("A", None)
      and policy.clause_owner(pd, "A", 65535) == (None, None)
      and policy.clause_owner(pd, "A", 30) is None
      and policy.clause_owner(pd, "A+B", 999) == ("A", None)
      and policy.clause_owner(pd, "A+B", 1010) == ("B", "priv")
      and policy.clause_owner(pd, "A+B", 1999) == ("B", None)
      and policy.clause_owner(pd, "A+B", 2010) is None,
      "clause_owner: the inverse of the numbering, alone and in a chain")
# Read back from what the compiler wrote: every clause it emits has an
# owner, and the owner is the term whose text the clause carries.
_rm = [l.split() for l in policy.compile_policy(pd, "A+B", "import")
       if l.startswith("route-map ")]
check(all(policy.clause_owner(pd, "A+B", int(w[3])) is not None
          for w in _rm),
      f"every clause of a chain's map has an owner: {_rm}")
check([policy.use_daemon(u) for u in ("import", "export", "bgp:table",
                                      "bgp:vrfimport", "ospf:static",
                                      "ospf:default", "isis:bgp",
                                      "fib:static")]
      == ["bgpd", "bgpd", "bgpd", "bgpd", "ospfd", "ospfd", "isisd",
          "zebra"],
      "use_daemon: the daemon whose counters move for each use")
_maps = {"bgpd": {"PO-A+B-IN": {"rules": [
    {"sequenceNumber": 10, "invoked": 3}, {"sequenceNumber": 11,
                                           "invoked": 2},
    {"sequenceNumber": 20, "invoked": 1}, {"sequenceNumber": 999,
                                           "invoked": 4},
    {"sequenceNumber": 1010, "invoked": 5},
    {"sequenceNumber": 1030, "invoked": 6},
    {"sequenceNumber": 65535, "invoked": 7}]}},
    "zebra": {"PO-A+B-IN": {"rules": [{"sequenceNumber": 10,
                                        "invoked": 99}]}}}
_c = policy.counters(pd, "A+B", "import", _maps)
check(_c == [("A", "cust", 5), ("A", "tagged", 1), ("A", None, 4),
             ("B", "priv", 5), ("B", "ospf", None), ("B", "rest", 6),
             (None, None, 7)],
      f"counters: a term's clauses summed (IPv4 and IPv6), a term not "
      f"compiled here None, bgpd's and not zebra's: {_c}")
check(policy.counters(pd, "A", "export", _maps) is None
      and policy.counters(pd, "A", "import", {}) is None,
      "counters: a map the daemon does not report is no counter, not zero")
check(policy.counters(pd, "GATE", "import", {"bgpd": {
    "PO-GATE-SUB-IN": {"rules": [{"sequenceNumber": 10, "invoked": 2}]}}},
    sub=True) == [("GATE", "no", 2), (None, None, 0)],
      "counters: the map a from policy term calls")

_r = {"prefix": "203.0.113.0/24", "community": ["65001:10"],
      "large-community": [], "extended-community": [],
      "as-path": ["3356", "64512"], "neighbor": "192.0.2.1",
      "local-preference": "100"}
sim = policy.simulate(pd, "A+B", _r, "import")
check(sim["verdict"] == "accept" and sim["by"] == ("B", "priv")
      and sim["route"]["local-preference"] == "110"
      and sim["route"]["community"] == ["65001:10", "65000:1", "65000:2"]
      and sim["route"]["as-path"] == ["3356"]
      and sim["route"]["metric"] == "the IGP metric to the next hop"
      and ("A", "tagged", "no match", "the prefix is in none of its lists "
           "or filters") not in sim["steps"],
      f"simulate: next policy skips the rest of A and its default-action, "
      f"B decides, every set in order: {sim}")
check(not any(t == "tagged" for _, t, _, _ in sim["steps"]),
      "next policy leaves the policy: its later terms are not walked")
_r2 = dict(_r, prefix="198.51.100.0/24", **{"as-path": ["3356"]})
sim = policy.simulate(pd, "A+B", _r2, "import")
check(sim["verdict"] == "reject" and sim["by"] == ("A", "tagged"),
      f"simulate: the community term rejects: {sim}")
_r3 = dict(_r2, community=[])
sim = policy.simulate(pd, "A+B", _r3, "import")
check(sim["verdict"] == "reject" and sim["by"] == ("A", None),
      f"simulate: no term decided, A's default-action: {sim}")
sim = policy.simulate(pd, "B", _r3, "export")
check(sim["verdict"] == "accept" and sim["by"] == (None, None)
      and sim["route"]["as-path"] == ["3356", "3356", "3356"]
      and sim["route"]["next-hop"] == "this box's address on the session"
      and sim["route"]["community"] == [],
      f"simulate: sets with no then go on, the use's default accepts, "
      f"prepend last-as, next-hop self: {sim}")
_igp = {"prefix": "10.1.0.0/16", "metric": "0", "tag": "0"}
sim = policy.simulate(pd, "B", _igp, "ospf:static")
check(sim["verdict"] == "reject"
      and any(t == "ospf" and o == "no match" and "not compiled here" in w
              for _, t, o, w in sim["steps"])
      and any("as-path" in w for w in sim["assumed"])
      and any("changes nothing here" in w for w in sim["said"]),
      f"simulate: from protocol answered by the use, an IGP export rejects "
      f"by default, a BGP set changes nothing: {sim}")
sim = policy.simulate(pd, "CALL", dict(_r, prefix="10.1.0.0/16"), "import")
check(sim["verdict"] == "reject" and sim["by"] == ("CALL", "1")
      and any(p == "GATE" and "called by CALL term 1" in w
              for p, _, _, w in sim["steps"]),
      f"simulate: from policy is FRR's call, its reject rejects: {sim}")
sim = policy.simulate(pd, "CALL", _r, "import")
check(sim["verdict"] == "accept"
      and sim["route"]["large-community"] == ["65001:1:2"]
      and sim["route"]["extended-community"] == ["rt:65001:5"],
      f"simulate: the call permits, the caller's sets apply: {sim}")
sim = policy.simulate(pd, "RPKI", {"prefix": "203.0.113.0/24",
                                   "neighbor": "192.0.2.2"}, "import")
check(sim["verdict"] == "accept" and sim["by"] == ("RPKI", "n")
      and sim["route"]["next-hop"] == "192.0.2.2"
      and any("validation-state is not known" in a and "RPKI cache" in a
              for a in sim["assumed"]),
      f"simulate: an RPKI state not known is said, not guessed; "
      f"peer-address on an import is the neighbor: {sim}")
sim = policy.simulate(pd, "TBL", {"prefix": "203.0.113.0/24"}, "bgp:table")
check(sim["route"].get("metric") == "9"
      and "local-preference" not in sim["route"]
      and any("set local-preference changes nothing" in w
              for w in sim["said"]),
      f"simulate: table-policy applies its metric and nothing else: {sim}")
_out = {"steps": [], "assumed": [], "said": [],
        "route": {"prefix": "10.1.0.0/16", "community": []}}
check(policy._walk(pd, "GATE", _out, "import", "bgp", 0)
      == ("reject", ("GATE", "no"))
      and _out["steps"] == [("GATE", "no", "match", "then reject")]
      and policy._walk(pd, "A", dict(_out, steps=[]), "import", "bgp", 0)
      is None,
      "_walk: one policy's terms, a decision or None when none decided")
check(policy._relative("100", "+10") == "110"
      and policy._relative(None, "-5") == "its own -5"
      and policy._relative("3", "-5") == "0"
      and policy._relative("3", "7") == "7",
      "_relative: +n and -n on what is known, said on what is not")
check(re.search(policy.as_path_regex("_6451[2-9]_"), "65001 64512")
      and not re.search(policy.as_path_regex("_6451[2-9]_"), "645123"),
      "as_path_regex: FRR's _ is a boundary")
check(policy._members_match([["a", "b"], ["c"]], ["c", "x"])
      and not policy._members_match([["a", "b"]], ["a"])
      and not policy._members_match([[]], ["a"]),
      "_members_match: entries ORed, an entry's members ANDed, none empty")
check(policy._term_matches(pd, pd.statements["A"]["tagged"],
                           {"prefix": "10.0.0.0/8"}, "bgp")[0] is None
      and "community is not known" in policy._term_matches(
          pd, pd.statements["A"]["tagged"], {"prefix": "10.0.0.0/8"},
          "bgp")[1],
      "_term_matches: a value not known is None, with what was not known")
_t = policy.Term("x")
_t.sets = [("community", ["set", "NEW"]), ("origin", ["egp"]),
           ("metric-type", ["1"]), ("next-hop", ["discard"])]
_said = []
_got = policy._apply_sets(pd, _t, {"prefix": "2001:db8::/32",
                                   "community": ["1:1"]}, "ospf:bgp", _said)
check(_got.get("metric-type") == "type-1" and _got["community"] == ["1:1"]
      and len(_said) == 3,
      f"_apply_sets: metric-type into OSPF, BGP's sets said, not done: "
      f"{_got} {_said}")
_said = []
_got = policy._apply_sets(pd, _t, {"prefix": "2001:db8::/32",
                                   "community": ["1:1"]}, "import", _said)
check(_got["community"] == ["65000:1", "65000:2"] and _got["origin"] == "egp"
      and _got["next-hop"] == "100::1 (discard)" and len(_said) == 1,
      f"_apply_sets: set replaces, discard is the family's address: {_got}")
check(policy.use_from_words(["fib", "static", "x"]) == ("fib:static", ["x"])
      and policy.use_from_words(["isis", "ospf"]) == ("isis:ospf", [])
      and policy.use_from_words(["table"]) == ("bgp:table", [])
      and policy.use_from_words(["fib", "kernel"])[0] is None
      and policy.use_words("bgp:vrfimport") == "vrf-import"
      and policy.use_words("ospf:static") == "ospf static",
      "use_from_words and use_words: at's words, both ways")
_route, _use, _why = policy.route_from_words(
    "203.0.113.7/24", ["neighbor", "192.0.2.1", "community",
                       "65001:10,65001:20", "as-path", "65001,64512", "med",
                       "5", "validation-state", "invalid", "at", "export"])
check(_why is None and _use == "export"
      and _route == {"prefix": "203.0.113.0/24", "neighbor": "192.0.2.1",
                     "community": ["65001:10", "65001:20"],
                     "large-community": [], "extended-community": [],
                     "as-path": ["65001", "64512"], "metric": "5",
                     "validation-state": "invalid"},
      f"route_from_words: the described route: {_route} {_use} {_why}")
for _w, _bad in ((["community", "1:x"], "is not a community"),
                 (["as-path", "a"], "as-path: AS numbers"),
                 (["neighbor", "x"], "is not an address"),
                 (["origin", "x"], "origin: igp"),
                 (["validation-state", "x"], "validation-state: valid"),
                 (["source-vrf", "a/b"], "source-vrf:"),
                 (["tag", "x"], "tag <0-"),
                 (["colour", "red"], "colour: not an attribute of a route"),
                 (["tag"], "tag: a value after it"),
                 (["at", "nowhere"], "at: nowhere is not a use")):
    check(_bad in (policy.route_from_words("10.0.0.0/8", _w)[2] or ""),
          f"route_from_words {_w}: {policy.route_from_words('10/8', _w)}")
check(policy._route_value("large-community", "1:2:3")[0] == ["1:2:3"]
      and policy._route_value("extended-community", "soo:1:2")[0]
      == ["soo:1:2"],
      "_route_value: the lists, comma-separated")
check(policy.describe_route({"prefix": "x", "as-path": [], "tag": "5",
                             "src": "10.0.0.1"})
      == ["prefix x", "as-path (none)", "tag 5", "src 10.0.0.1"],
      "describe_route: a line per attribute known, an empty list said none")
check(policy.set_keys(pd, "A+B") == ["as-path", "community",
                                     "local-preference", "metric",
                                     "next-hop"]
      and policy.set_keys(pd, "CALL") == ["extended-community",
                                          "large-community"],
      f"set_keys: what a use may change: {policy.set_keys(pd, 'A+B')}")
ok("counters per term, the simulation, a route described")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
