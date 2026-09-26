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
         "local-preference <0-4294967295>"),
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
F = policy._filter_entry
check(F("10.0.0.0/8", "exact", None) == "10.0.0.0/8", "exact")
check(F("10.0.0.0/8", "orlonger", None) == "10.0.0.0/8 le 32", "orlonger")
check(F("10.0.0.0/8", "longer", None) == "10.0.0.0/8 ge 9 le 32", "longer")
check(F("10.0.0.1/32", "longer", None) is None,
      "longer than a host route is nothing")
check(F("10.0.0.0/8", "upto", 24) == "10.0.0.0/8 le 24", "upto /24")
check(F("10.0.0.0/8", "upto", 8) == "10.0.0.0/8", "upto its own length")
check(F("2001:db8::/32", "orlonger", None) == "2001:db8::/32 le 128",
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

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
