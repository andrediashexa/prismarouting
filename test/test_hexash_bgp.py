#!/usr/bin/env python3
"""
The two BGP refusals of #145: an undefined route-map, and a policy that
withdraws the prefix carrying the operator's own session.

Both are decided from the text of a candidate configuration, so both are
decidable here in milliseconds - but neither was DESIGNED from the text. What
each shape does was measured first, on FRR 10.7.1, two speakers over a veth
pair, asserting on the receiving side's table rather than on anybody's memory
of the protocol. The captures are quoted beside the assertions they justify,
because a check built on what somebody believes FRR does is how #208 was
filed, and refusing a correct commit is the worst outcome this VTY can
produce.

lab/bgp-policy.sh carries the other half: the same configurations applied to a
real pair of speakers, asserted on the neighbour's table. This file is what
makes "which sentence does the box print, and does the commit stop" decidable
without docker.
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import config      # noqa: E402
import hexash      # noqa: E402
import vppapi      # noqa: E402

fail = 0


def unwrapped(text):
    """`text` with every run of whitespace collapsed to one space.

    The messages below are wrapped to the console by measurement (#252,
    #271), so where a line breaks moves when anybody edits the sentence - and
    a needle that straddles the break stops matching. For a POSITIVE check
    that is a noisy failure; for a `not in` check it is a silent pass, which
    is the defect class this project is organised against. Searching the
    unwrapped text asks the question the assertion means: did the box say
    this, not where did it wrap.
    """
    return " ".join(text.split())


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


def sev_of(findings, needle):
    """The severity of the one finding whose statement carries `needle`."""
    hit = [f for f in findings if needle in f[1]]
    return hit[0][0] if len(hit) == 1 else f"{len(hit)} findings"


# ------------------------------------------------------------------ #145.1
#
# Measured, FRR 10.7.1, `left` AS 65000 against `right` AS 65001 offering
# 203.0.113.0/24 and 198.51.100.0/24:
#
#   no route-map at all                  left holds both prefixes
#   neighbor 10.9.0.2 route-map          "The route-map 'RM-MISSING' does not
#     RM-MISSING in                       exist.", session Established, 0 pfx
#   route-map RM-MISSING permit 10       both prefixes back, nothing retyped
#   neighbor ... route-map OUT-MISSING   the other end holds 0
#     out
#   redistribute kernel route-map        originates nothing; with the map
#     RM-NOPE                             defined, "*u 203.0.113.0/24"
#   network 203.0.113.0/24 route-map     the same
#     RM-NONE
#
# So the issue's claim is true where it counts, and the statement is kept by
# FRR in every one of those - which is what makes it silent afterwards.
print("=== a route-map referenced and not defined ===")

BGP = ["router bgp 65000",
       " neighbor 10.0.0.2 remote-as 65001",
       " address-family ipv4 unicast",
       "  neighbor 10.0.0.2 route-map RM-IN in",
       " exit-address-family"]

found = hexash.route_map_reference_findings(BGP)
check(len(found) == 1, f"one finding, not several: {found}")
check(found and found[0][0] == "error",
      f"an undefined route-map on a neighbour is refused: {found}")
check(found and "RM-IN" in found[0][2],
      f"and the missing map is named: {found}")
check(found and "neighbor 10.0.0.2 route-map RM-IN in" == found[0][1],
      f"and so is the statement that names it: {found}")

# The legitimate neighbour of it, which MUST still commit. Every refusal in
# this file is paired with one of these: a check that refuses a correct
# design teaches the operator to work around the VTY.
check(hexash.route_map_reference_findings(
    ["route-map RM-IN permit 10", " match ip address prefix-list PL"] + BGP)
    == [],
    "the same configuration with the map defined says nothing at all")

# The other two shapes measured to deny everything.
check(sev_of(hexash.route_map_reference_findings(
    BGP[:3] + ["  redistribute static route-map RM-R"]), "redistribute")
    == "error",
    "a redistribute through an undefined map is refused")
check(sev_of(hexash.route_map_reference_findings(
    BGP[:3] + ["  network 203.0.113.0/24 route-map RM-N"]), "network")
    == "error",
    "and so is a network statement through one")

# `call` is in the same file and does NOT deny. Measured: a permit clause
# carrying `call RM-MISSING`, with nothing defining RM-MISSING, still put
# 203.0.113.0/24 in the peer's table, and `show route-map OUT` printed the
# call clause. Refusing a commit for it would be refusing a configuration that
# works, which is the shape #208 named.
called = hexash.route_map_reference_findings(
    ["route-map OUT permit 10", " call RM-MISSING"])
check(sev_of(called, "call RM-MISSING") == "warning",
      f"a call to an undefined map is a warning, not a refusal: {called}")
check(any("does nothing" in f[2] for f in called if "call" in f[1]),
      f"and it says what FRR does with it: {called}")

# The reverse, which the issue settled outright: dead configuration is untidy,
# not dangerous. It is also what an operator building a policy map-first has
# on the box between two commits.
dead = hexash.route_map_reference_findings(["route-map DEAD permit 10"])
check(len(dead) == 1 and dead[0][0] == "warning",
      f"a map nobody uses is reported and not refused: {dead}")
check(dead and "nothing uses it" in dead[0][2], f"and says so: {dead}")

# A reference through ANY of the places one can be named silences that
# warning, or the check reports a working policy as dead. `call` and the
# conditional-advertisement maps are the ones nothing else in this VTY reads.
for used in (" call DEAD", " neighbor 10.0.0.2 route-map DEAD out",
             " table-map DEAD", " neighbor 10.0.0.2 advertise-map DEAD",
             " redistribute connected route-map DEAD",
             "ip protocol bgp route-map DEAD"):
    got = hexash.route_map_reference_findings(
        ["route-map DEAD permit 10", "router bgp 65000", used])
    check(not [f for f in got if "nothing uses it" in f[2]],
          f"a map used by '{used.strip()}' is not reported as unused: {got}")

# Words that are not a route-map name. `bgp route-map delay-timer 10` is a
# knob, and a description is free text an operator writes sentences in - read
# as references, the first would refuse a commit over a timer and the second
# over a comment.
for line in ("bgp route-map delay-timer 10",
             "neighbor 10.0.0.2 description see route-map RM-IN",
             "description the route-map RM-IN applies here",
             "no neighbor 10.0.0.2 route-map RM-IN in"):
    check(hexash.route_map_reference_findings(["router bgp 65000", " " + line])
          == [],
          f"'{line}' names no route-map")


# ------------------------------------------------------------------ #145.2
print("\n=== a policy that withdraws the prefix carrying this session ===")

# Measured, same pair, this box originating 203.0.113.0/24 and 198.18.0.0/24,
# asserted on the PEER's table:
#
#   no policy                              peer holds both
#   route-map OUT permit 10, matching a    peer holds 198.18.0.0/24 only: the
#     list holding only 198.18.0.0/24       implicit deny at the end withdrew
#                                           the other one
#   route-map OUT deny 5 matching a list   the same
#     holding 203.0.113.0/24
#   neighbor prefix-list PL-OUT out,       the same
#     deny 203.0.113.0/24 then permit any
#   the policy removed                     both prefixes back
RUN = ["router bgp 65000",
       " neighbor 10.0.0.2 remote-as 65001",
       " address-family ipv4 unicast",
       "  network 203.0.113.0/24",
       "  neighbor 10.0.0.2 activate",
       " exit-address-family"]
HERE = "203.0.113.5"

check(hexash.prefix_list_verdict(
    ["ip prefix-list PL seq 5 permit 203.0.113.0/24"], "PL",
    "203.0.113.0/24") == "permit", "a prefix-list that names it permits it")
check(hexash.prefix_list_verdict(
    ["ip prefix-list PL seq 5 permit 198.18.0.0/24"], "PL",
    "203.0.113.0/24") == "deny",
    "and one that names something else denies it at the end of the list")
check(hexash.prefix_list_verdict(
    ["ip prefix-list PL seq 5 deny 203.0.113.0/24",
     "ip prefix-list PL seq 10 permit 0.0.0.0/0 le 32"], "PL",
    "203.0.113.0/24") == "deny", "first match wins, in sequence order")
check(hexash.prefix_list_verdict(
    ["ip prefix-list PL seq 5 permit 203.0.0.0/16 le 24"], "PL",
    "203.0.113.0/24") == "permit", "'le' widens the lengths an entry matches")
check(hexash.prefix_list_verdict(
    ["ip prefix-list PL seq 5 permit 203.0.0.0/16"], "PL",
    "203.0.113.0/24") == "deny",
    "and without it the length must match exactly, which is FRR's rule")
check(hexash.prefix_list_verdict(
    ["ip prefix-list PL seq 5 permit 203.0.113.0/24"], "OTHER",
    "203.0.113.0/24") is None,
    "a list that does not exist is not a deny this function invents")

# The route-map's own implicit deny, which is the measured case 1 above and
# the one an operator does not see in their own configuration.
IMPLICIT = ["ip prefix-list PL-OTHER seq 5 permit 198.18.0.0/24",
            "route-map OUT permit 10",
            " match ip address prefix-list PL-OTHER"]
check(hexash.route_map_verdict(IMPLICIT, "OUT", "203.0.113.0/24") == "deny",
      "a map whose only clause matches something else denies the rest")
check(hexash.route_map_verdict(IMPLICIT, "OUT", "198.18.0.0/24") == "permit",
      "and permits what it does match")
check(hexash.route_map_verdict(
    ["route-map OUT permit 10"], "OUT", "203.0.113.0/24") == "permit",
    "a clause with no match permits everything, which is the ordinary idiom")
# What it refuses to answer, and this is the half that keeps the gate off a
# working policy: a community match is decided by the route's attributes and a
# `call` by flow this does not follow.
for opaque in (" match community CL", " match as-path AS1", " call OTHER",
               " on-match next"):
    check(hexash.route_map_verdict(
        ["route-map OUT permit 10", opaque], "OUT", "203.0.113.0/24") is None,
        f"'{opaque.strip()}' is a clause this will not decide from the text")

# advertised_out: who would still be sent it.
POLICY = RUN[:5] + ["  neighbor 10.0.0.2 route-map OUT out"] + RUN[5:] \
    + IMPLICIT
carries, unsure = hexash.advertised_out(POLICY, "203.0.113.0/24")
check(carries == [] and unsure == [],
      f"the denied prefix reaches no neighbour: {carries} {unsure}")
carries, _ = hexash.advertised_out(POLICY, "198.18.0.0/24")
check(carries == ["10.0.0.2"],
      f"while the one the policy permits still reaches it: {carries}")
carries, _ = hexash.advertised_out(RUN, "203.0.113.0/24")
check(carries == ["10.0.0.2"], f"with no policy at all it is sent: {carries}")
# A session the configuration holds down carries nothing, and that IS in the
# text - unlike "has never come up", which is not and is why a neighbour in
# that state still counts as a carrier.
SHUT = RUN[:2] + [" neighbor 10.1.0.2 remote-as 65002",
                  " neighbor 10.1.0.2 shutdown"] + RUN[2:]
carries, _ = hexash.advertised_out(SHUT, "203.0.113.0/24")
check(carries == ["10.0.0.2"],
      f"a neighbour under 'shutdown' is not a carrier: {carries}")
SHUTPOL = SHUT[:6] + ["  neighbor 10.0.0.2 route-map OUT out"] + SHUT[6:] \
    + IMPLICIT
check(len(hexash.session_cut_findings(SHUT, SHUTPOL, HERE)) == 1,
      "so denying the prefix to the only live session is still a withdrawal")

# The findings themselves.
gone = hexash.session_cut_findings(
    RUN, [l for l in RUN if "network 203.0.113.0/24" not in l], HERE)
check(len(gone) == 1 and gone[0][0] == "203.0.113.0/24",
      f"removing the origination is reported, and the prefix named: {gone}")
check(gone and HERE in gone[0][1],
      f"and the address the session arrived on is named too: {gone}")
check(gone and any("commit cutoff" in a for a in gone[0][2]),
      f"and the operator is told the word to type: {gone}")

cut = hexash.session_cut_findings(RUN, POLICY, HERE)
check(len(cut) == 1 and cut[0][0] == "203.0.113.0/24",
      f"and so is an outbound policy that denies it: {cut}")

# Everything that MUST stay silent. Each of these is a configuration somebody
# has, and a gate that fires on one of them is a gate the operator learns to
# type past.
PERMITS = RUN[:5] + ["  neighbor 10.0.0.2 route-map OUT out"] + RUN[5:] \
    + ["ip prefix-list PL-MINE seq 5 permit 203.0.113.0/24",
       "route-map OUT permit 10", " match ip address prefix-list PL-MINE"]
check(hexash.session_cut_findings(RUN, PERMITS, HERE) == [],
      "a policy that still permits the prefix is not a withdrawal")
check(hexash.session_cut_findings(RUN, POLICY, "10.9.9.9") == [],
      "nor is one that withdraws a prefix this session does not run over")
check(hexash.session_cut_findings(RUN, POLICY, None) == [],
      "with no address there is nothing to decide, and nothing is claimed")
# A second session with no policy of its own still carries it out of the box.
TWO = [l for l in POLICY]
TWO.insert(2, " neighbor 10.1.0.2 remote-as 65002")
check(hexash.session_cut_findings(
    RUN[:2] + [" neighbor 10.1.0.2 remote-as 65002"] + RUN[2:], TWO, HERE)
    == [],
    "a second neighbour with no outbound policy still advertises it")
# One neighbour whose policy cannot be read is enough to withdraw the claim.
UNSURE = [l for l in POLICY]
UNSURE.insert(2, " neighbor 10.1.0.2 remote-as 65002")
UNSURE.insert(6, "  neighbor 10.1.0.2 filter-list AS-OUT out")
check(hexash.session_cut_findings(
    RUN[:2] + [" neighbor 10.1.0.2 remote-as 65002"] + RUN[2:], UNSURE, HERE)
    == [],
    "an AS-path filter this cannot follow silences the finding")
# A box that does not originate the prefix today is the ordinary CGNAT behind
# an edge router, and nothing in its configuration governs the announcement.
check(hexash.session_cut_findings(
    [l for l in RUN if "network" not in l], POLICY, HERE) == [],
    "a box that never originated it is told nothing")
# And an origination this cannot follow means the candidate may still carry
# the prefix by a route that is not in the text.
AGG = POLICY[:5] + ["  aggregate-address 203.0.0.0/16 summary-only"] \
    + POLICY[5:]
check(hexash.session_cut_findings(RUN, AGG, HERE) == [],
      "an aggregate-address makes the answer unknowable, so nothing is said")

print("\n=== the address of the session applying the change ===")
check(hexash.session_local_address(
    {"SSH_CONNECTION": "198.51.100.9 51234 203.0.113.5 22"}) == "203.0.113.5",
    "the SERVER half of SSH_CONNECTION is the address a withdrawal costs")
check(hexash.session_local_address({}) is None,
      "a serial console has no SSH_CONNECTION, and that is not an address")
check(hexash.session_local_address({"SSH_CONNECTION": "garbage"}) is None,
      "and a value that is not four fields is not read as one")

print("\n=== what the fingerprint decides is worth a note ===")
check(hexash.advertisement_fingerprint(RUN)
      != hexash.advertisement_fingerprint(POLICY),
      "an outbound policy changes what this box advertises")
check(hexash.advertisement_fingerprint(RUN)
      == hexash.advertisement_fingerprint(
          RUN + ["interface vppwan0", " description upstream"]),
      "an interface description does not, so it earns no note")


# ------------------------------------------------------------------ #145.4
print("\n=== an eBGP neighbour with no maximum-prefix ===")

# A warning and never a refusal, which is what the issue asking for it
# decided: a route server, a customer whose table is known small and a lab
# peer all run without a limit on purpose. What this section is really about
# is the OTHER direction - every shape below that MUST stay silent is a
# configuration somebody has, and a warning printed over one of them is the
# same harm as a refusal, only quieter.
NOLIMIT = ["router bgp 65000",
           " neighbor 10.0.0.2 remote-as 65001"]
mp = hexash.max_prefix_findings(NOLIMIT)
check(len(mp) == 1 and mp[0][0] == "warning",
      f"an eBGP neighbour with no limit is a warning, not a refusal: {mp}")
check(mp and mp[0][1] == "neighbor 10.0.0.2 remote-as 65001",
      f"and the statement that opens the session is named: {mp}")
check(mp and "maximum-prefix 10000" in mp[0][2],
      f"and the operator is told what to type, not only 'no': {mp}")

# The limit itself, in both places FRR takes it.
check(hexash.max_prefix_findings(
    NOLIMIT + [" neighbor 10.0.0.2 maximum-prefix 10000"]) == [],
    "a neighbour carrying the limit is told nothing")
check(hexash.max_prefix_findings(
    NOLIMIT + [" address-family ipv4 unicast",
               "  neighbor 10.0.0.2 maximum-prefix 10000 90 restart 5",
               " exit-address-family"]) == [],
    "and so is one carrying it inside an address family")

# `maximum-prefix-out` limits what this box SENDS. Counting it would silence
# the warning on a session with no inbound defence at all.
check(len(hexash.max_prefix_findings(
    NOLIMIT + [" neighbor 10.0.0.2 maximum-prefix-out 100"])) == 1,
    "'maximum-prefix-out' is a different statement and does not count")

# iBGP, both spellings. The peer is a box the same operator runs, its table is
# the table this AS already decided to hold, and a limit there fires on the
# network growing rather than on a leak.
check(hexash.max_prefix_findings(
    ["router bgp 65000", " neighbor 10.0.0.2 remote-as 65000"]) == [],
    "an iBGP neighbour is not this check's business")
check(hexash.max_prefix_findings(
    ["router bgp 65000", " neighbor 10.0.0.2 remote-as internal"]) == [],
    "and neither is one written 'remote-as internal'")
check(len(hexash.max_prefix_findings(
    ["router bgp 65000", " neighbor 10.0.0.2 remote-as external"])) == 1,
    "while 'remote-as external' is eBGP and is warned about")

# Peer groups, which is where an operator with six upstreams puts the limit
# exactly once. Missing this is how a check earns a workaround.
GROUP = ["router bgp 65000",
         " neighbor UPSTREAM peer-group",
         " neighbor UPSTREAM remote-as external",
         " neighbor 10.0.0.2 peer-group UPSTREAM",
         " neighbor 10.1.0.2 peer-group UPSTREAM"]
check(len(hexash.max_prefix_findings(GROUP)) == 2,
      f"members of a group with no limit are each warned: "
      f"{hexash.max_prefix_findings(GROUP)}")
check(hexash.max_prefix_findings(
    GROUP + [" neighbor UPSTREAM maximum-prefix 500000"]) == [],
    "the limit on the group covers every member of it")
check(not [f for f in hexash.max_prefix_findings(GROUP)
           if f[1].startswith("neighbor UPSTREAM ")],
      "and the group itself is not a session, so it is never the subject")

# What cannot be decided from the text is left alone. A second sentence about
# a limit on a session that will never open is noise on top of a refusal
# bgp_errors already makes.
check(hexash.max_prefix_findings(
    ["router bgp 65000", " neighbor 10.0.0.2 description upstream"]) == [],
    "a neighbour with no remote-as anywhere is bgp_errors' business, not this")
check(hexash.max_prefix_findings(
    ["router bgp 65000", " neighbor 10.0.0.2 remote-as auto"]) == [],
    "'remote-as auto' resolves to an AS that is not in the text")
# `neighbor` is an OSPF word too, and reading every routing line for it is
# how #208's sibling was filed. Recorded because the mutation says so: taking
# the `router bgp` guard OUT of the walk leaves this assertion GREEN, because
# `poll-interval` is neither a remote-as nor a peer-group and the undecidable
# case is skipped anyway. The property is true and worth stating; the guard
# that would make it true on its own is not what pins it.
check(hexash.max_prefix_findings(
    ["router ospf", " neighbor 10.0.0.2 poll-interval 60"]) == [],
    "and OSPF's 'neighbor' is not a BGP session at all")

# The unnumbered form, read by keyword. Read positionally it was a neighbour
# with no AS, and that reading is what #208 refused a working commit over.
check(len(hexash.max_prefix_findings(
    ["router bgp 65000", " neighbor vppwan0 interface remote-as external"]))
    == 1,
    "an unnumbered eBGP session with no limit is warned about too")
check(hexash.max_prefix_findings(
    ["router bgp 65000", " neighbor vppwan0 interface remote-as internal"])
    == [],
    "and the internal one is not")

# Printed, and measured against the console rather than eyeballed. The
# neighbour name is in the sentence AND on the row below it, so the widest
# real case is an IPv6 peer - which is one of the three rows #276 found over
# budget after somebody had read them and decided they fitted.
_sh = hexash.Shell.__new__(hexash.Shell)
for _peer in ("10.0.0.2", "2001:db8:aaaa:bbbb::2", "vppwan0"):
    _f = hexash.max_prefix_findings(
        ["router bgp 65000", f" neighbor {_peer} remote-as 65001"])
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        _sh.say_finding(*_f[0])
    _over = [l for l in _buf.getvalue().splitlines()
             if len(l) > config.CONSOLE_COLUMNS]
    check(not _over,
          f"the warning for {_peer} fits {config.CONSOLE_COLUMNS} "
          f"columns: {_over}")

# A stanza's own AS, so the same neighbour AS is eBGP in one VRF and iBGP in
# another. Reading one local AS for the file would get one of them wrong.
TWO_AS = ["router bgp 65000",
          " neighbor 10.0.0.2 remote-as 65001",
          " neighbor 10.0.0.2 maximum-prefix 10000",
          "router bgp 65001 vrf blue",
          " neighbor 10.2.0.2 remote-as 65001"]
check(hexash.max_prefix_findings(TWO_AS) == [],
      f"a neighbour in the AS of its own stanza is iBGP there: "
      f"{hexash.max_prefix_findings(TWO_AS)}")

# ------------------------------------------------------------------- commit
print("\n=== the commit path: the gate, and the way past it ===")


class FakeApi:
    def available(self):
        return (vppapi.OK, "stub")

    def call(self, message, args):
        return vppapi.Reply(vppapi.OK)


class FakeVpp:
    TRACE = False

    def run(self, cmd):
        return 0, ""

    def running(self):
        return 0, ""


class Box:
    """One appliance, and a hexash session over it that commits."""

    def __init__(self, running):
        self.running = running
        self.applied = []

    def shell(self):
        sh = hexash.Shell()
        sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT}
        sh.api = FakeApi()
        sh.dp = FakeVpp()
        sh.frr = type("F", (hexash.Frr,), {
            "available": lambda s: True,
            "show": lambda s, cmd: (0, "zebra bgpd staticd mgmtd"),
            # What FRR holds AFTER the apply, which on a box that took the
            # commit is the commit. A stub answering [] makes every applied
            # statement look like one FRR silently dropped, and `commit` then
            # prints a refusal listing them - noise that lands in the same
            # captured output these assertions search.
            "running": lambda s: config.Config.parse(self.running).routing,
            "outstanding": lambda s, out: [],
            "diagnostics": lambda s, out: [],
            "reload": lambda s, lines, check_only=False: (0, "")})()
        sh.rb = type("R", (), {"push": lambda s, text, comment="": None})()
        sh.running_config = lambda: config.Config.parse(self.running)
        real = sh.apply

        def apply(cfg, check_only=False):
            ok = real(cfg, check_only=check_only)
            if ok and not check_only:
                self.running = cfg.text()
                self.applied.append(cfg.text())
            return ok
        sh.apply = apply
        return sh

    def commit(self, text, args=()):
        sh = self.shell()
        sh.candidate = config.Config.parse(text)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with contextlib.redirect_stderr(buf):
                sh.do_commit(list(args))
        return buf.getvalue(), sh


def cfg_text(routing):
    """A whole configuration file carrying these routing statements.

    The ROUTING_HEADER is not decoration: without it Config.parse reads every
    line as a CGNAT statement it does not understand, and `commit` refuses the
    file before any of the checks below are reached.
    """
    return config.ROUTING_HEADER + "\n" + "\n".join(routing) + "\n"


os.environ["SSH_CONNECTION"] = "198.51.100.9 51234 203.0.113.5 22"

# The undefined route-map: refused, whole, and the map named.
box = Box(cfg_text(RUN))
out, sh = box.commit(cfg_text(RUN[:3] + ["  neighbor 10.0.0.2 route-map RM-IN in"]
                              + RUN[3:]))
check("RM-IN" in out, f"the commit names the missing map:\n{out}")
check("nothing applied" in out, f"and nothing is applied:\n{out}")
check(box.applied == [], f"and the box did not change: {box.applied}")

# Defined in the same commit: applied. An operator writing the map and the
# neighbour together is the ordinary way to build a policy.
box = Box(cfg_text(RUN))
out, sh = box.commit(cfg_text(
    ["route-map RM-IN permit 10"]
    + RUN[:3] + ["  neighbor 10.0.0.2 route-map RM-IN in"] + RUN[3:]))
check("no route-map called" not in out,
      f"a map defined in the same commit is not reported missing:\n{out}")
check(len(box.applied) == 1, f"and the box took it: {box.applied}")

# A box ALREADY in that state can still be worked on. `--load` runs this same
# check at boot and refuses the file whole, so refusing here would leave a box
# whose saved configuration carries the reference coming up with nothing on it.
BAD = RUN[:3] + ["  neighbor 10.0.0.2 route-map RM-IN in"] + RUN[3:]
box = Box(cfg_text(BAD))
out, sh = box.commit(cfg_text(BAD + ["ip route 10.0.0.0/8 blackhole"]))
check("warning" in out and "RM-IN" in out,
      f"a box already in that state is warned, not refused:\n{out}")
check(len(box.applied) == 1,
      f"and an unrelated change on it still commits: {box.applied}")

# The withdrawal: refused until the word is typed, then applied - and applied
# as a confirmed commit, because the word does not itself give a way back.
WITHDRAW = cfg_text(POLICY)
box = Box(cfg_text(RUN))
out, sh = box.commit(WITHDRAW)
check("203.0.113.0/24" in out, f"the prefix is named:\n{out}")
check("commit cutoff" in out, f"and the word to type with it:\n{out}")
check(box.applied == [], f"and nothing was applied: {box.applied}")

box = Box(cfg_text(RUN))
out, sh = box.commit(WITHDRAW, args=("cutoff",))
check(len(box.applied) == 1, f"'commit cutoff' applies it:\n{out}")
# And it combines with the other gate word and with `comment`, in either
# order: a change with two consequences has two to acknowledge, and an
# operator who has to acknowledge them should not lose the record of why.
for args in (("cutoff", "remap"), ("remap", "cutoff"),
             ("cutoff", "comment", "drenando o transito")):
    other = Box(cfg_text(RUN))
    got, _ = other.commit(WITHDRAW, args=args)
    check(len(other.applied) == 1,
          f"'commit {' '.join(args)}' applies it too:\n{got}")
check("confirmed" in out,
      f"and arms a rollback, because the session may not survive it:\n{out}")

# And an ordinary commit on the same box, with the same session address, is
# not gated at all.
box = Box(cfg_text(RUN))
out, sh = box.commit(cfg_text(RUN + ["ip route 10.0.0.0/8 blackhole"]))
check(len(box.applied) == 1, f"an unrelated change still commits:\n{out}")
check("cutoff" not in out, f"and is told nothing about cutoff:\n{out}")

# The maximum-prefix warning on the commit path, both directions: said over
# the neighbour this commit opens, and not said over the one that carries a
# limit. The second is the assertion that matters - it is the configuration an
# operator who did everything right has.
box = Box(cfg_text(["router bgp 65000"]))
out, sh = box.commit(cfg_text(["router bgp 65000",
                               " neighbor 10.0.0.2 remote-as 65001"]))
check("maximum-prefix" in unwrapped(out),
      f"opening an eBGP session with no limit is said out loud:\n{out}")
check(len(box.applied) == 1,
      f"and it is a warning, so the commit goes through: {box.applied}")
box = Box(cfg_text(["router bgp 65000"]))
out, sh = box.commit(cfg_text(["router bgp 65000",
                               " neighbor 10.0.0.2 remote-as 65001",
                               " neighbor 10.0.0.2 maximum-prefix 10000"]))
check("maximum-prefix" not in unwrapped(out),
      f"while the same session with a limit is told nothing:\n{out}")
check(len(box.applied) == 1, f"and commits: {box.applied}")

# And it speaks once, not on every commit afterwards. A box whose upstreams
# are deliberately unlimited would otherwise carry the same warning forever,
# which is the alarm nobody reads.
LIVE = ["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001"]
box = Box(cfg_text(LIVE))
out, sh = box.commit(cfg_text(LIVE + ["ip route 10.0.0.0/8 blackhole"]))
check("maximum-prefix" not in unwrapped(out),
      f"a box already in that state is not warned again:\n{out}")
check(len(box.applied) == 1,
      f"and its unrelated change commits: {box.applied}")

# The console case: no address, and the box says which check it could not
# make rather than letting the operator assume it passed.
del os.environ["SSH_CONNECTION"]
box = Box(cfg_text(RUN))
out, sh = box.commit(WITHDRAW)
check("did not arrive over SSH" in unwrapped(out),
      f"a session with no address is told the check did not run:\n{out}")
check(len(box.applied) == 1,
      f"and the commit is not gated on a check nobody could make: {box.applied}")
box = Box(cfg_text(RUN))
out, sh = box.commit(cfg_text(RUN + ["ip route 10.0.0.0/8 blackhole"]))
check("did not arrive over SSH" not in unwrapped(out),
      f"while a change that advertises nothing new earns no note:\n{out}")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
