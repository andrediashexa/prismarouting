"""
BFD at the VTY (#559): what it offers, what needs bfdd, and the two screens.

The sessions themselves run in FRR's bfdd and are proved end to end in
lab/bfd.sh - a silent link failure detected in the configured 300 ms and both
routes leaving the dataplane's table. What is decidable in milliseconds is
here: which statements need bfdd, the grammar the `bfd` level offers, the
defaults FRR leaves out of its read-back, and that both screens fit a console
of 79 columns at their widest - an IPv6 neighbour, an IPv6 source, a VRF and
a profile on one session.

The JSON below is the shape FRR 10.7.1's `show bfd peers json` and `show bfd
peers counters json` print, captured from a bfdd with a configured session, a
multihop one and two asked for by protocols.
"""
import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "prismash"))
import prismash      # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if cond:
        print(f"  ok: {msg}")
    else:
        print(f"  FAIL: {msg}")
        fail += 1


PEERS = [
    {"multihop": False, "peer": "10.9.0.9", "local": "10.9.0.1",
     "vrf": "default", "interface": "dum0", "id": 1712291648,
     "status": "up", "uptime": 8100, "diagnostic": "ok",
     "remote-diagnostic": "ok", "type": "configured",
     "receive-interval": 300, "transmit-interval": 300,
     "transmit-interval-actual": 282, "detection-timeout": 900,
     "echo-receive-interval": 50, "echo-transmit-interval": 0,
     "detect-multiplier": 3, "remote-receive-interval": 300,
     "remote-transmit-interval": 300, "remote-detect-multiplier": 3},
    {"multihop": True, "peer": "10.9.0.2", "local": "10.9.0.1",
     "vrf": "default", "id": 1177033545, "minimum-ttl": 254,
     "status": "down", "downtime": 367, "diagnostic": "ok",
     "type": "dynamic", "receive-interval": 300, "transmit-interval": 300,
     "detection-timeout": 3000, "detect-multiplier": 3},
    {"multihop": False, "peer": "10.9.0.2", "local": "10.9.0.1",
     "vrf": "default", "interface": "dum0", "id": 4244773251,
     "status": "down", "downtime": 367,
     "diagnostic": "control detection time expired",
     "type": "configured", "profile": "fast", "receive-interval": 100,
     "transmit-interval": 100, "detection-timeout": 3000,
     "echo-receive-interval": 50, "echo-transmit-interval": 50,
     "detect-multiplier": 3},
]
COUNTERS = [
    {"id": 1712291648, "control-packet-input": 120,
     "controlPacketBadInput": 0, "control-packet-output": 121,
     "session-up": 1, "session-down": 0},
    {"id": 4244773251, "control-packet-input": 0,
     "controlPacketBadInput": 0, "control-packet-output": 0,
     "session-up": 2, "session-down": 2},
]

print("=== the statements that need bfdd ===")
# Mutation shown red: `"bfd" in w` instead of the positions - the route-map
# named bfd and a description were then counted, and a box without bfdd was
# refused a configuration that asked nothing of it.
ROUTING = ["bfd", " peer 10.0.0.2 interface vppwan0", " exit", "exit",
           "router bgp 65000", " neighbor 10.0.0.2 remote-as 65001",
           " neighbor 10.0.0.2 bfd profile lab",
           " neighbor 10.0.0.3 description to bfd lab", "exit",
           "ip route 203.0.113.0/24 10.0.0.2 tag 5 10 bfd multi-hop",
           "ip route 198.51.100.0/24 10.0.0.2",
           "interface vppwan0", " ip ospf bfd", " isis bfd", "exit",
           "route-map bfd permit 10", "exit"]
got = prismash.bfd_statements(ROUTING)
check(got == ["bfd", "neighbor 10.0.0.2 bfd profile lab",
              "ip route 203.0.113.0/24 10.0.0.2 tag 5 10 bfd multi-hop",
              "ip ospf bfd", "isis bfd"],
      f"the block, a neighbour's, a route's and the IGPs': {got!r}")
check(prismash.bfd_statements(["route-map bfd permit 10",
                               "ip route 198.51.100.0/24 10.0.0.2"]) == [],
      "and nothing from a route-map called bfd or a route without it")

print("\n=== bfdd is a daemon this box runs ===")
check("bfdd" in prismash.configurable_daemons(),
      "the `bfd` level puts bfdd among the daemons show version expects")
advice = prismash.enable_daemon_advice("bfdd")
check("next reboot" in advice and "/etc/frr/daemons" not in advice,
      f"and the advice for it is the reboot, not the daemons file: {advice!r}")

print("\n=== the grammar ===")
# Measured on FRR 10.7.1: `profile <name>` opens a profile at the bfd level and
# applies one inside a session, so the two grammars differ by that word.
sh = prismash.Shell()
sh.frr_ctx = ["bfd"]
check(sh.block_grammar([]) is prismash.BFD_BLOCK,
      "inside `bfd`: sessions and profiles")
sh.frr_ctx = ["bfd", "peer 10.0.0.2 interface vppwan0"]
check(sh.block_grammar([]) is prismash.BFD_PEER
      and "profile" in prismash.BFD_PEER,
      "inside a session: its timers, and `profile` to take one")
sh.frr_ctx = ["bfd", "profile fast"]
check(sh.block_grammar([]) is prismash.BFD_PROFILE
      and "profile" not in prismash.BFD_PROFILE,
      "inside a profile: the timers, and no profile of a profile")
check("bfd" in prismash.TREE["config"],
      "and `bfd` is offered at the configuration level")
check(prismash.BGP_NEIGHBOUR["bfd"][1] == "[profile <name>]",
      "a BGP neighbour's bfd takes an optional profile")
check("[bfd]" in prismash.ARG_STATIC_NEXT_HOP,
      "and a static route offers bfd after its next hop")
for word in ("peers", "peer"):
    check(word in prismash.SHOW["bfd"][1], f"`show bfd {word}` is offered")

print("\n=== the defaults FRR leaves out of its read-back ===")
# lab/frr-readback.sh 4f: each of these is absent at its default and comes
# back at a control value. Mutation shown red: dropping the six entries - a
# profile typed with `detect-multiplier 3` was then a dropped statement.
for stmt in ("detect-multiplier 3", "receive-interval 300",
             "transmit-interval 300", "echo receive-interval 50",
             "echo transmit-interval 50", "minimum-ttl 254"):
    check(not prismash.frr_keeps(stmt), f"{stmt!r} is expected to vanish")
for stmt in ("detect-multiplier 7", "receive-interval 100",
             "echo transmit-interval 70", "minimum-ttl 200"):
    check(prismash.frr_keeps(stmt), f"{stmt!r} is expected back")

print("\n=== the sessions, joined to their counters ===")
s = prismash.bfd_sessions(PEERS, COUNTERS)
check([(x["peer"], x["multihop"]) for x in s]
      == [("10.9.0.2", False), ("10.9.0.2", True), ("10.9.0.9", False)],
      f"by neighbour, the single-hop one first: "
      f"{[(x['peer'], x['multihop']) for x in s]!r}")
check(s[0]["counters"].get("session-down") == 2 and s[1]["counters"] == {},
      "each with its own counters, by id, and none where FRR gave none")
check(prismash._addr_key("10.0.0.9") < prismash._addr_key("10.0.0.10")
      < prismash._addr_key("2001:db8::1") < prismash._addr_key("not-one"),
      "addresses sort as numbers, IPv4 before IPv6, anything else last")
check(prismash._bfd_where(s[0]) == "single hop, on dum0, from 10.9.0.1, "
                                   "profile fast"
      and prismash._bfd_where({"multihop": True, "vrf": "red",
                               "type": "dynamic"})
      == "multihop, vrf red, asked for by a protocol",
      f"where a session runs, in words: {prismash._bfd_where(s[0])!r}")
check(prismash._bfd_state({"status": "up", "uptime": 8100})
      == "up for 2 h 15 min"
      and prismash._bfd_state({"status": "down", "downtime": 45})
      == "down for 45 s"
      and prismash._bfd_state({"status": "init"}) == "init",
      "its state, for how long when FRR says")
check(prismash.bfd_sessions(None, None) == []
      and prismash.bfd_sessions([{"no": "peer"}], []) == [],
      "nothing, and a row with no neighbour, are no sessions")

print("\n=== show bfd peers ===")
rows = prismash.bfd_peers_screen(s)
print("\n".join("    | " + r for r in rows))
check(rows[0] == "  10.9.0.2  single hop, on dum0, from 10.9.0.1, "
                 "profile fast",
      f"where the session runs, on its first row: {rows[0]!r}")
check(rows[1] == "      down for 6 min 7 s, every 100 ms, down after 3000 "
                 "ms, diagnostic:"
      and rows[2] == "      control detection time expired, downs 2",
      f"its state and timers on the second, wrapped under it: {rows[1:3]!r}")
check(any("asked for by a protocol" in r for r in rows),
      "a session a protocol made says so")
check(any("downs 0" in r for r in rows) and any("downs 2" in r for r in rows),
      "and how often each went down")
check(prismash.bfd_peers_screen([])[0].startswith("  no BFD sessions"),
      "an empty box says so, rather than printing nothing")

print("\n=== show bfd peer <address> ===")
detail = prismash.bfd_peer_screen([s[0]])
print("\n".join("    | " + r for r in detail))
check(detail[0] == "  session    10.9.0.2, single hop, on dum0, from "
                   "10.9.0.1, profile fast",
      f"the session: {detail[0]!r}")
check(any(r.startswith("  echo ") for r in detail),
      "echo, when this box sends it")
check(not any(r.startswith("  echo ") for r in prismash.bfd_peer_screen([s[2]])),
      "and no echo row when it does not")
check(any("down 2 time(s)" in r for r in detail), "and its history")

print("\n=== the widest session fits a console of 79 ===")
# An IPv6 neighbour, an IPv6 source, a sub-interface, a VRF and a profile, on
# one session, with a diagnostic and a five-digit count of downs. FRR's own
# brief row for the same session is 171 columns.
WIDE = [{"peer": "2001:db8:ffff:ffff:ffff:ffff:ffff:ffff",
         "local": "2001:db8:aaaa:aaaa:aaaa:aaaa:aaaa:aaaa", "multihop": True,
         "interface": "enp1s0.4094", "vrf": "customer-red-long",
         "profile": "a-profile-with-a-long-name", "type": "dynamic",
         "status": "admin_down", "downtime": 99999999,
         "transmit-interval": 4294967, "receive-interval": 4294967,
         "transmit-interval-actual": 4294967, "detection-timeout": 1095216585,
         "detect-multiplier": 255, "remote-transmit-interval": 4294967,
         "remote-receive-interval": 4294967, "remote-detect-multiplier": 255,
         "echo-transmit-interval": 4294967, "echo-receive-interval": 4294967,
         "diagnostic": "neighbor signaled session down",
         "remote-diagnostic": "administratively down",
         "counters": {"control-packet-input": 18446744073709551615,
                      "controlPacketBadInput": 18446744073709551615,
                      "control-packet-output": 18446744073709551615,
                      "session-up": 4294967295, "session-down": 4294967295}}]
for name, screen in (("peers", prismash.bfd_peers_screen(WIDE)),
                     ("peer", prismash.bfd_peer_screen(WIDE))):
    widest = max(len(r) for r in screen)
    check(widest <= 79, f"show bfd {name}: {widest} columns at its widest")

print("\n=== show bfd, end to end against a bfdd that answers ===")


class Frr:
    def __init__(self):
        self.asked = []

    def show(self, cmd):
        self.asked.append(cmd)
        if cmd == "show bfd peers json":
            return 0, json.dumps(PEERS)
        if cmd == "show bfd peers counters json":
            return 0, json.dumps(COUNTERS)
        return 1, ""


sh = prismash.Shell()
sh.frr = Frr()
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.show_bfd(["peer", "10.9.0.2"])
text = out.getvalue()
check(sh.frr.asked == ["show bfd peers json", "show bfd peers counters json"],
      f"bfdd is asked for the sessions and their counters: {sh.frr.asked!r}")
check(text.count("  session ") == 2,
      "both sessions to the address, single hop and multihop")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.show_bfd(["peer", "10.9.0.09"])
check("no BFD session" in out.getvalue() or "not an address"
      in out.getvalue(),
      "an address written another way is read as an address, or refused")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
