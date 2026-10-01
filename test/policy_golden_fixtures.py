"""
The policies whose compiled text test/policy-golden-0.148.6.txt pins: the
fixtures test_prismash_policy.py already used, compiled by the compiler as
0.148.6 shipped it, before #535 (phase A) touched it. Run as a script it
prints the text the compiler gives now, which the suite compares.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src", "prismash"))
import policy  # noqa: E402

P = "policy-options"
FIX = {
"example": (f"""{P} prefix-list CUSTOMERS 203.0.113.0/24
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
{P} policy-statement UP-IN term 2 set local-preference 200""".splitlines(),
  [("UP-OUT", "export"), ("UP-IN", "import"), ("UP-OUT", "import")]),
"step2": (f"""{P} prefix-list LOOPS 10.255.0.0/16
{P} policy-statement EXT term 1 from protocol static
{P} policy-statement EXT term 1 set metric 20
{P} policy-statement EXT term 1 set tag 7
{P} policy-statement EXT term 1 then accept
{P} policy-statement EXT term 2 from protocol direct
{P} policy-statement EXT term 2 from prefix-list LOOPS
{P} policy-statement EXT term 2 then accept
{P} policy-statement NH term 1 from tag 7
{P} policy-statement NH term 1 from metric 20
{P} policy-statement NH term 1 set next-hop 192.0.2.1
{P} policy-statement NH term 2 from protocol ospf
{P} policy-statement NH term 2 then reject""".splitlines(),
  [("EXT", f"{igp}:{p}") for igp in policy.IGPS for p in policy.IGPS[igp]]
  + [("NH", "import"), ("NH", "export")]),
"filters": (f"""{P} policy-statement F term a from route-filter 10.0.0.0/8 exact
{P} policy-statement F term a from route-filter 10.0.0.0/8 orlonger
{P} policy-statement F term a from route-filter 10.0.0.0/8 longer
{P} policy-statement F term a from route-filter 2001:db8::/32 upto /48
{P} policy-statement F term a from route-filter 192.0.2.1/32 longer
{P} policy-statement F term a set next-hop 2001:db8::1
{P} policy-statement F term b from prefix-list MISSING
{P} policy-statement F term b then accept
{P} policy-statement F term c then reject""".splitlines(), [("F", "import"), ("F", "export")]),
}

#: Phase A's (#535) fixture, pinned by test/policy-golden-0.148.9.txt as the
#: compiler shipped it in 0.148.9, before phase B (#536) touched it.
FIX_A = {
"phase-a": (f"""{P} prefix-list CUST 203.0.113.0/24
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
{P} policy-statement OSPF-EXT term 1 then accept""".splitlines(),
  [("IN-A+IN-B", "import"), ("OUT", "export"), ("OSPF-EXT", "ospf:static"),
   ("IN-A", "ospf:static"), ("BOGONS", "export")]),
}


def compiled(fixtures=None):
    """The text every fixture compiles to now, as the golden file holds it."""
    out = []
    for name, (lines, uses) in (fixtures or FIX).items():
        pol, bad = policy.parse(lines)
        assert not bad, bad
        out.append(f"### {name}")
        out += policy.compile_all(pol, set(uses))
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.stdout.write(compiled(FIX_A if sys.argv[1:] == ["a"] else None))
