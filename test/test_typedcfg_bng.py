#!/usr/bin/env python3
"""
src/prismash/typedcfg.py's BNG areas (#597, docs/bng.md 0.4 item 2): the
`pppoe` and `aaa` blocks read off the VTY's own model as fields, and desired
state turned into the lines an operator types under them.

THE ROUND TRIP IS THE POINT, as in test_typedcfg.py: the lines written here
are staged into a copy of the model by pppoe.stage() and aaa.stage() - the
prompt's own rule, the one a saved file is read with - and the copy read
back must be what was asked for. And the secret: never in a read, in the
lines only as the box is given them, and blanked in what a client is shown.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src", "prismash"))
import typedcfg as T  # noqa: E402
import prismash as P  # noqa: E402
import pppoe as PP  # noqa: E402
import aaa as A  # noqa: E402
from config import Config  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


TEXT = """aaa
 radius
  server 127.0.0.1 secret testing123 priority 1
  server 127.0.0.2 secret testing123 auth-port 1645 role auth priority 2
  retries 0
  dead-time 10
  dynamic-authorization listen 127.0.0.1
  dynamic-authorization client 127.0.0.1 secret daesecret
  rate-profile UP15 down 0 up 15M
  exit
 exit
pppoe
 interface host-acc0
 interface host-acc0.100
 gateway 100.64.0.1
 address-pool 100.64.0.10-100.64.0.200
 dns 8.8.8.8
 tcp-mss 1452
 rate-limit default 20M/10M
 local-user bob password s3cr3t
 exit
"""
cfg = Config.parse(TEXT)
check(not cfg.unknown, f"the configuration parses: {cfg.unknown}")


def staged_pppoe(held, lines):
    """`held` with `lines` (as typed under `pppoe`) staged, and why not."""
    h = copy.deepcopy(held)
    for ln in lines:
        w = ln.split()
        neg = w[:1] == ["no"]
        why = PP.stage(h, (["no"] if neg else []) + ["pppoe"]
                       + (w[1:] if neg else w))
        if why:
            return None, f"{ln}: {why}"
    return h, None


def staged_aaa(held, lines):
    """`held` with `lines` (as typed under `aaa` > `radius`) staged."""
    h = copy.deepcopy(held)
    for ln in lines:
        w = ln.split()
        neg = w[:1] == ["no"]
        why = A.stage(h, (["no"] if neg else []) + ["aaa", "radius"]
                      + (w[1:] if neg else w))
        if why:
            return None, f"{ln}: {why}"
    return h, None


print("=== reads: the model's fields, never a secret ===")
p = T.pppoe_typed(cfg)
check(p == T.pppoe_held_typed(cfg.pppoe) and p["configured"]
      and p["interfaces"] == ["host-acc0", "host-acc0.100"]
      and p["address_pools"] == ["100.64.0.10-100.64.0.200"]
      and p["tcp_mss"] == 1452 and p["mtu"] == 0 and p["authentication"] == []
      and (p["rate_limit_default_down_kbit"],
           p["rate_limit_default_up_kbit"]) == (20000, 10000)
      and p["local_users"] == [{"name": "bob", "password_set": True}],
      f"pppoe_typed: the block as fields, a default as 0: {p}")
a = T.aaa_typed(cfg)
r = a["radius"]
check(a == T.aaa_held_typed(cfg.aaa) and a["configured"]
      and [s["address"] for s in r["servers"]] == ["127.0.0.1", "127.0.0.2"]
      and r["servers"][1] == {"address": "127.0.0.2", "secret_set": True,
                              "auth_port": 1645, "acct_port": 1813,
                              "role": "auth", "priority": 2}
      and r["retries"] == 0 and r["timeout_seconds"] is None
      and r["dead_time_seconds"] == 10
      and r["dynamic_authorization_port"] == 3799
      and r["rate_profiles"] == [{"name": "UP15", "down_kbit": 0,
                                  "up_kbit": 15000}],
      f"aaa_typed: retries 0 is a value, an unset timeout is None: {r}")
check(not any(x in repr(T.config_typed(cfg, ["pppoe", "aaa"]))
              for x in ("testing123", "daesecret", "s3cr3t")),
      "config_typed: no secret in the areas pppoe and aaa")
e = T.config_typed(Config.parse(""), ["pppoe", "aaa"])
check(e["pppoe"]["configured"] is False and e["aaa"]["configured"] is False
      and e["aaa"]["radius"]["dynamic_authorization_port"] == 0,
      "config_typed: no block reads as not configured")
check("pppoe" in T.config_typed(cfg) and "aaa" in T.config_typed(cfg)
      and "pppoe" not in T.config_typed(cfg, ["interfaces"]),
      "config_typed: every area by default, only the ones asked otherwise")
check(T._rate_pair("20M/10M") == (20000, 10000) and T._rate_pair(None) == (0, 0),
      "_rate_pair: kbit/s both ways")
check(sorted(["10.0.0.9", "10.0.0.10"], key=T._addr_key)
      == ["10.0.0.9", "10.0.0.10"], "_addr_key: numerically")
check(T._profile_word(0) == "0" and T._profile_word(15000) == "15M",
      "_profile_word: a profile's rate as written")

print("=== edits: what differs, as the prompt takes it ===")
check(T.pppoe_lines(cfg.pppoe, p) == [] and T.aaa_lines(cfg.aaa, a) == [],
      "a read written back is no line at all")
want = dict(p, interfaces=["host-acc0"], mtu=1400, ac_name="bng1",
            authentication=["chap"], rate_limit_default_down_kbit=0,
            rate_limit_default_up_kbit=0,
            local_users=[{"name": "bob"}, {"name": "amy", "password": "pw"}])
lines = T.pppoe_lines(cfg.pppoe, want)
check(lines == ["no interface host-acc0.100", "authentication chap",
                "mtu 1400", "ac-name bng1", "no rate-limit default",
                "local-user amy password pw"],
      f"pppoe_lines: removals first, a kept user's password unsent: {lines}")
h, why = staged_pppoe(cfg.pppoe, lines)
back = T.pppoe_held_typed(h) if h else {}
check(why is None and {k: back[k] for k in ("interfaces", "mtu", "ac_name")}
      == {"interfaces": ["host-acc0"], "mtu": 1400, "ac_name": "bng1"}
      and h["users"] == {"bob": "s3cr3t", "amy": "pw"},
      f"pppoe_lines: staged by the prompt's rule, read back as asked: {why}")
check(T.pppoe_lines(cfg.pppoe, want, ["mtu"]) == ["mtu 1400"],
      "pppoe_lines: only the fields named")
check(T.pppoe_lines(cfg.pppoe, dict(p, local_users=[]), ["local_users"])
      == ["no local-user bob"], "pppoe_lines: a user not listed is removed")
for bad, says in ((dict(p, local_users=[{"name": "eve"}]), "needs a password"),
                  (dict(p, rate_limit_default_down_kbit=0), "both rates"),
                  (dict(p, local_users=[{"password": "x"}]), "its name")):
    try:
        T.pppoe_lines(cfg.pppoe, bad)
        check(False, f"pppoe_lines refuses ({says})")
    except ValueError as ex:
        check(says in str(ex), f"pppoe_lines refuses: {ex}")
try:
    T.pppoe_lines(cfg.pppoe, p, ["password"])
    check(False, "pppoe_lines: an unknown field is refused")
except ValueError as ex:
    check("unknown field" in str(ex), f"pppoe_lines: {ex}")

r2 = dict(r, servers=[dict(r["servers"][1], priority=1),
                      {"address": "10.0.0.9", "secret": "n3w", "role": "acct"}],
          timeout_seconds=5, retries=None, mode="round-robin",
          dynamic_authorization_clients=[{"address": "127.0.0.1"},
                                         {"address": "127.0.0.3",
                                          "secret": "d2"}],
          rate_profiles=[{"name": "P20", "down_kbit": 20000, "up_kbit": 0}])
lines = T.aaa_lines(cfg.aaa, {"radius": r2})
check(lines == ["no server 127.0.0.1",
                "server 127.0.0.2 secret testing123 auth-port 1645 role auth "
                "priority 1",
                "server 10.0.0.9 secret n3w role acct", "mode round-robin",
                "timeout 5", "no retries",
                "dynamic-authorization client 127.0.0.3 secret d2",
                "no rate-profile UP15", "rate-profile P20 down 20M up 0"],
      f"aaa_lines: the kept secret written for the box, a new one given: "
      f"{lines}")
h, why = staged_aaa(cfg.aaa, lines)
back = T.aaa_held_typed(h)["radius"] if h else {}
check(why is None and [(s["address"], s["priority"]) for s in back["servers"]]
      == [("127.0.0.2", 1), ("10.0.0.9", 2)]
      and back["retries"] is None and back["timeout_seconds"] == 5
      and h["dae"]["clients"] == {"127.0.0.1": "daesecret", "127.0.0.3": "d2"},
      f"aaa_lines: staged by the prompt's rule, read back as asked: {why}")
shown = "\n".join(T.shown_lines(lines))
check("testing123" not in shown and "n3w" not in shown and "d2" not in shown
      and shown.count("<set, not shown>") == 3,
      "shown_lines: every secret in the lines blanked for the client")
check(T.aaa_lines(cfg.aaa, {"radius": dict(r, timeout_seconds=7)},
                  ["radius.timeout_seconds"]) == ["timeout 7"],
      "aaa_lines: only the fields named; the servers untouched")
check(T.aaa_lines(cfg.aaa, {"radius": dict(
          r, dynamic_authorization_listen="")},
          ["radius.dynamic_authorization"])
      == ["no dynamic-authorization listen"]
      and T.aaa_lines(cfg.aaa, {"radius": dict(
          r, dynamic_authorization_port=1700)},
          ["radius.dynamic_authorization"])
      == ["dynamic-authorization listen 127.0.0.1 port 1700"],
      "aaa_lines: the listener and its port")
check(T._server_line({"address": "127.0.0.1"},
                     cfg.aaa["servers"][0]) is None
      and T._server_line({"address": "127.0.0.1", "secret": "x"},
                         cfg.aaa["servers"][0])
      == "server 127.0.0.1 secret x priority 1",
      "_server_line: nothing when nothing changes; a new secret is a line")
for bad, says in (([{"address": "10.9.9.9"}], "needs a secret"),
                  ([{"secret": "x"}], "needs its address")):
    try:
        T.aaa_lines(cfg.aaa, {"radius": dict(r, servers=bad)})
        check(False, f"aaa_lines refuses ({says})")
    except ValueError as ex:
        check(says in str(ex), f"aaa_lines refuses: {ex}")
check(T._secret_lines("local-user", {"a": "1"}, [{"name": "a",
                                                  "password": "1"}],
                      "password", "{name}") == [],
      "_secret_lines: the same secret again writes nothing")

print("=== the area as the server reaches it ===")
path, lines = T.edit_for(cfg, "pppoe", dict(p, mtu=1400), ["mtu"])
check((path, lines) == (["pppoe"], ["mtu 1400"]), "edit_for: pppoe, at its path")
path, lines = T.edit_for(cfg, "aaa", {"radius": dict(r, retries=2)},
                         ["radius.retries"])
check((path, lines) == (["aaa", "radius"], ["retries 2"]),
      "edit_for: aaa, under radius")
check(T.delete_lines("pppoe") == ([], ["no pppoe"])
      and T.delete_for(cfg, "aaa") == ([], ["no aaa"]),
      "delete_lines: each block whole")
sch = {m["mode"]: m for m in T.schema(P, ["pppoe", "aaa", "aaa radius"])}
check(sch["pppoe"]["path"] == ["pppoe"] and sch["pppoe"]["typed"] == "pppoe"
      and sch["aaa radius"]["path"] == ["aaa", "radius"]
      and all(s["typed"] for s in sch["aaa radius"]["statements"])
      and any(s["words"] == ["local-user"] for s in sch["pppoe"]["statements"]),
      "schema: the BNG's modes, every statement typed")

print("=== the server: AaaConfig's presence, the PPPoE reads as fields ===")
import types  # noqa: E402
import apiserver as API  # noqa: E402

pbmod = types.ModuleType("fake_pb_597")
sys.modules["fake_pb_597"] = pbmod


class _F:
    LABEL_REPEATED = 3

    def __init__(self, name, msg=None, rep=False):
        self.name, self.label = name, 3 if rep else 1
        self.message_type = types.SimpleNamespace(name=msg) if msg else None


def _msg(name, fields, has=()):
    def init(self, **kw):
        for f in fields:
            setattr(self, f.name, kw.get(
                f.name, [] if f.label == 3 else (_cls[f.message_type.name]()
                                                 if f.message_type else 0)))
        self._has = set(kw.get("_has", has))

    c = type(name, (), {"__init__": init, "__module__": "fake_pb_597",
                        "DESCRIPTOR": types.SimpleNamespace(fields=fields),
                        "HasField": lambda self, f: f in self._has})
    setattr(pbmod, name, c)
    return c


_cls = {}
_cls["Radius"] = _msg("Radius", [_F(f) for f, _w in T.RADIUS_NUMBERS]
                      + [_F("mode")])
_cls["Aaa"] = _msg("Aaa", [_F("configured"), _F("radius", "Radius")])
m = _cls["Aaa"](radius=_cls["Radius"](retries=0, timeout_seconds=0, mode="",
                                      _has={"retries"}))
d = API.aaa_dict(m)
check(d["radius"]["retries"] == 0 and d["radius"]["timeout_seconds"] is None
      and d["radius"]["dead_time_seconds"] is None,
      f"aaa_dict: retries 0 set is 0, a number unset is None: {d}")
check(API.AREA_OF_FIELD["pppoe"] == "pppoe" and API.AREA_OF_FIELD["aaa"] == "aaa",
      "AREA_OF_FIELD: SetConfig's pppoe and aaa edit their areas")

TABLE = PP.session_rows("""\
 8aa8eb1ab0657544 | pppoe_session2 | sub2     | 100.64.0.11 | 02:aa:00:00:00:02 | active | 3725       | 10           | 7            |
 8AA8EB1AB0657546 | pppoe_session1 | sub1     | 100.64.0.77 | 02:aa:00:00:00:01 | active | 90061      | 0            | 0            | 20000/10000
 8aa8eb1ab0657547 | pppoe_session3 |          |             | 02:aa:00:00:00:03 | starting |            |              |              |
""")
x = API.pppoe_session_fields(TABLE[1])
check(x == {"session_id": "8aa8eb1ab0657546", "user": "sub1",
            "address": "100.64.0.77", "mac": "02:aa:00:00:00:01",
            "interface": "pppoe_session1", "state": "active",
            "uptime_seconds": 90061, "rx_bytes": 0, "tx_bytes": 0,
            "rate_limit_down_kbit": 20000, "rate_limit_up_kbit": 10000},
      f"pppoe_session_fields: a row as PppoeSession, the id lower case: {x}")
x = API.pppoe_session_fields(TABLE[2])
check(x["state"] == "starting" and x["uptime_seconds"] == 0
      and x["rate_limit_down_kbit"] == 0 and x["user"] == "",
      "pppoe_session_fields: a session still authenticating: zeros, no user")
f = API.pppoe_sessions_fields({"running": True, "rows": TABLE})
check(f["running"] and [s["user"] for s in f["sessions"]] == ["", "sub1",
                                                              "sub2"],
      "pppoe_sessions_fields: in the screen's order, by user then id")
check(API.pppoe_sessions_fields({"running": False, "rows": []})
      == {"running": False, "sessions": []},
      "pppoe_sessions_fields: not running, no session")
pols = {"vyos_7_20000_1250000_down": {"conform": 10, "dropped": 2},
        "vyos_7_10000_625000_up": {"conform": 4, "dropped": 0},
        "vyos_17_1_1_up": {"conform": 99, "dropped": 99}}
check(API.pppoe_policer_fields(pols, "7") == [
          {"direction": "down", "conform_packets": 10, "dropped_packets": 2},
          {"direction": "up", "conform_packets": 4, "dropped_packets": 0}]
      and API.pppoe_policer_fields(pols, "") == [],
      "pppoe_policer_fields: the session's own, down first; none unfound")
facts = {"held": cfg.pppoe, "aaa": cfg.aaa, "status": "running",
         "counts": {"active": 3, "starting": 1},
         "states": {"127.0.0.1": "failed"},
         "trail": {"written": 12, "failed": 1, "seq": 40},
         "guard": {"not-access": 5, "held": 2}}
x = API.pppoe_summary_fields(facts)
check(x["configured"] and x["control_plane"] == "running"
      and (x["sessions_active"], x["sessions_starting"],
           x["sessions_finishing"]) == (3, 1, 0)
      and (x["trail_known"], x["trail_written"], x["trail_failed"],
           x["trail_seq"]) == (True, 12, 1, 40)
      and (x["refusals_known"], x["refused_not_access"],
           x["refused_mac_held"]) == (True, 5, 2)
      and x["radius"] and x["radius_mode"] == "failover" and x["accounting"]
      and x["radius_servers"][0] == {"address": "127.0.0.1", "role": "both",
                                     "priority": 1, "state": "failed"}
      and x["radius_servers"][1]["state"] == ""
      and x["dynamic_authorization_clients"] == 1
      and (x["rate_limit_default_down_kbit"],
           x["rate_limit_default_up_kbit"]) == (20000, 10000)
      and x["local_users"] == 1,
      f"pppoe_summary_fields: the summary's reading as fields: {x}")
x = API.pppoe_summary_fields(dict(facts, status="stopped", counts={},
                                  trail=None, guard=None,
                                  aaa=A.empty()))
check(not x["trail_known"] and not x["refusals_known"] and not x["radius"]
      and x["radius_servers"] == [] and x["radius_mode"] == ""
      and not x["accounting"],
      "pppoe_summary_fields: nothing read is never a zero that was read")
check(API.pppoe_summary_fields({"held": PP.empty()}) == {"configured": False}
      and API.pppoe_summary_fields({}) == {"configured": False},
      "pppoe_summary_fields: no PPPoE")
check(API.pppoe_history_words({"user": "sub1", "address": "100.64.0.10",
                               "session_id": "8aa8eb1ab0657546",
                               "since": "2h", "at": "", "last": 3})
      == ["user", "sub1", "address", "100.64.0.10", "session",
          "8aa8eb1ab0657546", "since", "2h", "last", "3"]
      and API.pppoe_history_words({}) == [],
      "pppoe_history_words: the request as the VTY's words")
x = API.pppoe_history_session_fields({
    "session": "7a", "user": "joao", "mac": "02:aa", "interface": "ens19",
    "address": "100.64.0.11", "start": 1.5, "stop": None, "cause": ""})
check(x["held"] and x["stop"] is None and x["start"] == 1.5
      and x["session_id"] == "7a" and x["interface"] == "ens19",
      "pppoe_history_session_fields: a session still held")
check(API.ts_ms(1760000000.123) == (1760000000, 123000000)
      and API.ts_ms(5) == (5, 0), "ts_ms: the trail's milliseconds exactly")
for rep, verdict in (({"files": 0}, "no_trail"),
                     ({"files": 1, "checked": 0}, "no_chained_entries"),
                     ({"files": 2, "checked": 9, "head_seq": 9,
                       "head_sha": "ab", "first": 1}, "intact"),
                     ({"files": 1, "checked": 5, "problem": ["BROKEN at"],
                       "kind": "altered", "entry": 6}, "altered"),
                     ({"files": 1, "checked": 5, "problem": ["BROKEN before"],
                       "kind": "removed", "entry": 7}, "removed"),
                     ({"files": 1, "problem": ["x cannot be read"],
                       "kind": "unreadable"}, "unreadable")):
    x = API.pppoe_verify_fields(rep, "/t")
    check(x["verdict"] == verdict and x["path"] == "/t",
          f"pppoe_verify_fields: {verdict}")
x = API.pppoe_verify_fields({"files": 1, "checked": 5, "problem": ["a", "b"],
                             "kind": "removed", "entry": 7}, "/t")
check(x["entry"] == 7 and x["problem"] == ["a", "b"] and x["checked"] == 5,
      "pppoe_verify_fields: the entry and the problem's words")
check(API.clear_line("pppoe_session", "8aa8eb1ab0657546")
      == "clear pppoe session 8aa8eb1ab0657546"
      and API.clear_line("pppoe_user", "joao@isp") == "clear pppoe session "
      "user joao@isp"
      and API.clear_line("pppoe_address", "100.64.0.9")
      == "clear pppoe session address 100.64.0.9"
      and API.clear_line("pppoe_all") == "clear pppoe session all"
      and API.clear_line("pppoe_user") is None
      and API.clear_line("pppoe_user", "a b") is None,
      "clear_line: the PPPoE targets as the VTY's lines")
check(API._num("12") == 12 and API._num("") == 0 and API._num(None) == 0,
      "_num: a number printed, or 0")

if fail:
    print(f"\n{fail} FAILURES")
    sys.exit(1)
print("\nALL TESTS PASSED")
