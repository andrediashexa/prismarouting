#!/usr/bin/env python3
"""
The CGNAT's screens as the API's typed reads (#558, phase 6): each `show
cgnat` screen the dataplane prints, read into fields by cgnatshow - IPv4 and
NAT64 pools both - and the server's half: the VTY line each rpc sends, its
refusals, and the fields of the two reads drawn from the Shell's own data
(memory, HA).

The screens below are written in the plugin's own words, line for line from
the format strings that print them; the last section holds every pattern to
those strings in src/plugins/bpa, so a change of wording there turns this
red instead of a field reading 0.
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src", "prismash"))
import cgnatshow as C  # noqa: E402
import apiserver as A  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


XLT_PROSE_NOW = ("  this answers for NOW ONLY. The block belonged to somebody else "
                 "before and will again; for a past instant read the RFC 8158 log, "
                 "which records when each block was assigned and released.")

print("=== show cgnat translation ===")
t = C.parse_translation(
    "200.150.0.17:1500  ->  100.64.0.5\n"
    "  pool nb, block 3, DETERMINISTIC\n"
    "  from the FORMULA. It holds for any instant, past or present, as long as "
    "this pool's addresses and block size have not changed since. No allocation "
    "record is involved.\n")
check(t == {"public_address": "200.150.0.17", "port": 1500, "subscriber": "100.64.0.5",
            "pool": "nb", "block": 3, "source": "formula", "nat64": False,
            "now_only": False},
      f"a deterministic pool: the formula, for any instant: {t}")
t = C.parse_translation(
    "200.150.0.17:1500  ->  2001:db8:1200:ab00::/56\n"
    "  pool v6, block 171, DETERMINISTIC NAT64\n  from the FORMULA. ...\n")
check(t["subscriber"] == "2001:db8:1200:ab00::/56" and t["nat64"]
      and t["source"] == "formula" and t["block"] == 171 and t["pool"] == "v6"
      and not t["now_only"],
      f"a NAT64 pool's formula names the subscriber's prefix: {t}")
t = C.parse_translation("200.150.0.18:4000  ->  2001:db8:1200:ab00::/56\n"
                        "  pool v6-dyn, block 9, DYNAMIC\n" + XLT_PROSE_NOW + "\n")
check(t["source"] == "bpa" and t["now_only"] and t["nat64"]
      and t["pool"] == "v6-dyn", f"a dynamic NAT64 holder, for now only: {t}")
t = C.parse_translation("200.150.0.18:4000  ->  nobody, right now\n"
                        "  pool dyn, block 9, DYNAMIC: no subscriber holds this "
                        "block at the moment\n" + XLT_PROSE_NOW + "\n")
check(t["subscriber"] == "" and t["source"] == "bpa" and t["now_only"]
      and t["block"] == 9, f"nobody holds it now: {t}")
t = C.parse_translation("200.150.0.19:2100  ->  100.64.1.7\n"
                        "  pool nb, reserve block 2, DETERMINISTIC RESERVE\n"
                        + XLT_PROSE_NOW + "\n")
check(t["source"] == "reserve" and t["block"] == 2 and t["now_only"]
      and t["subscriber"] == "100.64.1.7", f"a reserve block's holder: {t}")
t = C.parse_translation("200.150.0.17:80 belongs to pool nb but to no block: the "
                        "port is outside the pool's usable range, so nothing was "
                        "ever translated here\n")
check(t["source"] == "outside_range" and t["pool"] == "nb" and t["port"] == 80
      and t["subscriber"] == "", f"a port below the usable range: {t}")
t = C.parse_translation("show bpa translation: 9.9.9.9 is not a public address of "
                        "any pool\n")
check(t["source"] == "" and t["public_address"] == "",
      "an error screen fills nothing")

print("=== show cgnat top ===")
top = ("subscriber       pool               ports  full  blocks  refusals\n"
       "100.64.0.5       nb            1990/2048    97%       1         0\n"
       "2001:db8:1200:ab00::/56 residential-nat64 131000/131072   99%      64        12\n"
       "100.64.0.9       nb               3/2048     0%       1 4294967295\n"
       "3 subscriber(s) holding blocks\n")
r = C.parse_top(top)
check(r["holding"] == 3 and len(r["rows"]) == 3, f"three rows and the count: {r}")
check(r["rows"][1] == {"subscriber": "2001:db8:1200:ab00::/56",
                       "pool": "residential-nat64", "ports_used": 131000,
                       "ports_total": 131072, "percent_full": 99, "blocks": 64,
                       "refusals": 12},
      f"a NAT64 prefix overrunning its column, a 17-letter pool and six-digit "
      f"ports read whole: {r['rows'][1]}")
check(r["rows"][2]["refusals"] == 4294967295, "ten digits of refusals")
check(C.parse_top("no subscriber holds a block right now\n") == {"rows": [], "holding": 0},
      "nobody holding a block")

print("=== show cgnat drops ===")
drops = ("bpa-in2out\n"
         "  packets that did not get through\n"
         "           12  no pool for source address\n"
         "  everything else it counted\n"
         "1234567890123  packets translated\n"
         "bpa-out2in\n"
         "  nothing dropped\n")
d = C.parse_drops(drops.replace("1234567890123  packets",
                                "  1234567890123  packets"))
check([n["node"] for n in d["nodes"]] == ["in2out", "out2in"], f"both nodes: {d}")
check(d["nodes"][0]["counters"] == [
    {"reason": "no pool for source address", "count": 12, "dropped": True},
    {"reason": "packets translated", "count": 1234567890123, "dropped": False}],
    f"a drop and a thirteen-digit counter, each under its heading: {d['nodes'][0]}")
check(d["nodes"][1]["counters"] == [], "a node that dropped nothing has no counter")

print("=== show cgnat blocks ===")
blocks = (
    "pool dyn (hold-down 120s)\n"
    "  thread 1: 3/5 blocks in use, 10 allocations (4 extensions), 7 releases, "
    "0 refusals, 1 pinned\n"
    "             2 lent to another thread, so 2 of this slice is neither in use "
    "nor free here\n"
    "  thread 2: 1/5 blocks in use, 1 allocations (0 extensions), 0 releases, "
    "2 refusals, 0 pinned\n"
    "  total: 4/10 in use, 11 allocations (4 extensions), 7 releases, 2 refusals, "
    "1 pinned\n"
    "  stride 4: 3 extensions placed where it says, 1 fell back to the free list\n"
    "  RFC 4787 REQ-2 paired: 4 of 4 extension(s) on the holder's own address\n"
    "pool nb (deterministic reserve, 2 block(s) per public address, 8 in all, "
    "hold-down 120s)\n"
    "  thread 1: 0/8 blocks in use, 0 allocations (0 extensions), 0 releases, "
    "5 refusals, 0 pinned\n"
    "  total: 0/8 in use, 0 allocations (0 extensions), 0 releases, 5 refusals, "
    "0 pinned\n"
    "  reserve exhausted 5 time(s): a subscriber filled its block, was under "
    "max-blocks 4, and the reserve had nothing left\n")
b = C.parse_blocks(blocks)
dyn, res = b["pools"]
check(dyn["pool"] == "dyn" and not dyn["reserve"] and dyn["holddown_seconds"] == 120
      and len(dyn["threads"]) == 2, f"a dynamic pool, two workers: {dyn}")
check(dyn["threads"][0] == {"thread": 1, "in_use": 3, "blocks": 5, "allocations": 10,
                            "extensions": 4, "releases": 7, "refusals": 0,
                            "pinned": 1, "lent": 2},
      f"a worker's row, its lent blocks on the line below: {dyn['threads'][0]}")
check(dyn["threads"][1]["lent"] == 0 and dyn["threads"][1]["refusals"] == 2,
      "a worker with nothing lent")
check(dyn["total"] == {"in_use": 4, "blocks": 10, "allocations": 11,
                       "extensions": 4, "releases": 7, "refusals": 2, "pinned": 1},
      f"the total: {dyn['total']}")
check((dyn["stride"], dyn["stride_placed"], dyn["stride_fell_back"]) == (4, 3, 1)
      and (dyn["paired"], dyn["paired_of"], dyn["paired_shown"]) == (4, 4, True),
      "the stride and paired pooling")
check(res["reserve"] and res["reserve_per_address"] == 2 and res["reserve_blocks"] == 8
      and res["reserve_exhausted"] == 5 and not res["paired_shown"],
      f"a deterministic pool's reserve: {res}")
_m = re.match(r"(x) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+)", "x 1 2 3 4 5 6 7")
check(C._counts(_m, 2) == {"in_use": 1, "blocks": 2, "allocations": 3, "extensions": 4,
                           "releases": 5, "refusals": 6, "pinned": 7},
      "_counts: the seven counters a thread row and a total share, in order")
check(C.parse_blocks("no pool on this box allocates blocks: no dynamic pool, and no "
                     "deterministic pool with a reserve\n") == {"pools": []},
      "no pool allocates blocks")

print("=== show cgnat identity ===")
i = C.parse_identity("identity table: 2 entry(s), read 2026/10/04 12:34:56:123\n"
                     "radius accounting: listening on port 1813\n"
                     "  packets    10 received, 8 applied, 9 answered\n"
                     "  refused    1 bad authenticator, 0 malformed\n"
                     "  3 name(s) were longer than 63 characters and are carried "
                     "in part\n")
check(i == {"loaded": True, "entries": 2, "read_at": "2026/10/04 12:34:56:123",
            "radius": True, "radius_port": 1813, "received": 10, "applied": 8,
            "answered": 9, "bad_authenticator": 1, "malformed": 0,
            "truncated_names": 3, "name_limit": 63},
      f"the table, RADIUS and the names carried in part: {i}")
i = C.parse_identity("no identity table: every record says 'unknown'\n")
check(not i["loaded"] and i["entries"] == 0 and not i["radius"], "no table")

print("=== show cgnat history verify ===")
H = "configuration trail: /var/log/prisma-routing/audit.log\n"
v = C.parse_history_verify(
    H + "  2 entries carry no digest: written before the chain existed, readable "
    "but not covered\n  41 entries checked, chain intact\n"
    "  head  seq 41 sha256 " + "ab" * 32 + "\n"
    "  a chain that verifies proves the file was not edited, ...\n")
check(v["verdict"] == "intact" and v["checked"] == 41 and v["unchained"] == 2
      and v["head_seq"] == 41 and v["head_sha256"] == "ab" * 32
      and v["path"] == "/var/log/prisma-routing/audit.log",
      f"an intact chain and its head: {v}")
v = C.parse_history_verify(H + "  BROKEN at entry 7\n    2026-10-04 commit by x\n"
                           "    this entry does not match its own digest: its text "
                           "was altered after it was written\n")
check(v["verdict"] == "altered" and v["entry"] == 7
      and v["entry_text"] == "2026-10-04 commit by x", f"an altered entry: {v}")
v = C.parse_history_verify(H + "  BROKEN before entry 9\n    the sequence jumps from 6 "
                           "to 9: 2 entries were removed from the middle\n")
check(v["verdict"] == "removed" and v["entry"] == 9 and v["removed"] == 2,
      f"entries removed from the middle: {v}")
v = C.parse_history_verify(H + "  TRUNCATED: the file ends mid-entry\n"
                           "    2026-10-04 com\n    a write stopped part way ...\n")
check(v["verdict"] == "truncated_mid_entry" and v["entry_text"] == "2026-10-04 com",
      f"a write that stopped: {v}")
v = C.parse_history_verify(
    H + "  INCOMPLETE: 3 entries could not be written since this dataplane started, "
    "so they are missing ...\n  12 entries checked, chain intact\n"
    "  TRUNCATED: the file ends at entry 12 and this dataplane wrote up to 15 - "
    "3 entries were removed from the end\n")
check(v["verdict"] == "truncated_end" and v["file_ends_at"] == 12
      and v["dataplane_wrote_to"] == 15 and v["removed"] == 3 and v["unwritten"] == 3
      and v["checked"] == 12, f"removed from the end, and writes that failed: {v}")
v = C.parse_history_verify(H + "  no chained entries: nothing here is covered\n")
check(v["verdict"] == "no_chained_entries", "nothing chained")
v = C.parse_history_verify(H + "no trail at /var/log/prisma-routing/audit.log\n")
check(v["verdict"] == "no_trail" and v["path"].endswith("audit.log"), f"no trail: {v}")

print("=== show cgnat alg ===")
a = C.parse_alg("nb\n"
                "  ftp    on   File Transfer Protocol (RFC 959)\n"
                "  rtsp   off  Real Time Streaming Protocol\n"
                "v6\n"
                "  ftp    off  File Transfer Protocol (RFC 959)\n")
check([p["pool"] for p in a["pools"]] == ["nb", "v6"]
      and a["pools"][0]["algs"][0] == {"alg": "ftp", "on": True,
                                       "description": "File Transfer Protocol (RFC 959)"}
      and a["pools"][0]["algs"][1]["on"] is False and len(a["pools"][1]["algs"]) == 1,
      f"each pool, each ALG: {a}")
check(C.parse_alg("no pools configured\n") == {"pools": []}, "no pools")

print("=== show cgnat forward ===")
f = C.parse_forward("pool nb\n  tcp 200.150.0.17:8080 -> 100.64.0.5:80\n"
                    "pool v6\n  udp 200.150.0.18:5000 -> 2001:db8:1200:ab00::5:5060\n")
check(f["forwards"] == [
    {"pool": "nb", "protocol": "tcp", "public_address": "200.150.0.17",
     "public_port": 8080, "private_address": "100.64.0.5", "private_port": 80},
    {"pool": "v6", "protocol": "udp", "public_address": "200.150.0.18",
     "public_port": 5000, "private_address": "2001:db8:1200:ab00::5",
     "private_port": 5060}],
    f"an IPv4 forward, and a NAT64 one split at its last colon: {f}")
check(C.parse_forward("no forwards configured\n") == {"forwards": []}, "none")

print("=== the VTY line each read sends, and what it refuses ===")
check(A.cgnat_command("translation", {"public_address": "200.150.0.17", "port": 1500})
      == "show cgnat translation 200.150.0.17 1500", "translation")
for bad in ({"public_address": "200.150.0.17 | count", "port": 1},
            {"public_address": "2001:db8::1", "port": 1},
            {"public_address": "200.150.0.17", "port": 70000}):
    try:
        A.cgnat_command("translation", bad)
        check(False, f"{bad} must be refused")
    except ValueError as e:
        check(True, f"refused: {e}")
check(A.cgnat_command("top", {"count": 0}) == "show cgnat top"
      and A.cgnat_command("top", {"count": 25}) == "show cgnat top 25", "top")
check(A.cgnat_command("forward", {}) == "show cgnat forward"
      and A.cgnat_command("forward", {"pool": "nb"}) == "show cgnat forward nb",
      "forward, every pool or one")
for bad in ("nb extra", "nb|x", "x" * 64):
    try:
        A.cgnat_command("forward", {"pool": bad})
        check(False, f"pool {bad!r} must be refused")
    except ValueError:
        check(True, f"pool {bad[:20]!r} refused")
check(A.cgnat_command("history") == "show cgnat history verify", "history verify")
check(A.enum_name("SOURCE", "outside_range") == "SOURCE_OUTSIDE_RANGE"
      and A.enum_name("VERDICT", "") == "VERDICT_UNSPECIFIED", "enum names")


print("=== a refusal is the dataplane's, not a line shaped like one ===")
check(C.screen_error("show bpa translation: 9.9.9.9 is not a public address of any "
                     "pool\n").startswith("show bpa translation: 9.9.9.9")
      and C.screen_error("unknown input `bogus'\n") == "unknown input `bogus'",
      "the command's path before the colon, or VPP's parser")
for good in ("no identity table: every record says 'unknown'\n",
             "configuration trail: /var/log/prisma-routing/config-history.log\n  "
             "3 entries checked, chain intact\n",
             "identity table: 2 entry(s), read 2026/10/04 12:34:56:123\n",
             drops, top, ""):
    check(C.screen_error(good) == "",
          f"a screen whose first line is `words: text` is not a refusal: {good[:40]!r}")


class FakeDp:
    def __init__(self, up):
        self.up = up

    def reachable(self):
        return self.up


class FakeShell:
    """The VTY as cgnat_read sees it: feed() prints a screen, the dataplane
    answers or does not."""

    def __init__(self, screens, up=True):
        self.screens, self.fed, self.dp = screens, [], FakeDp(up)

    def feed(self, line):
        self.fed.append(line)
        print(self.screens.get(line, ""), end="")

    def cgnat_memory_facts(self):
        return self.mem

    def show_cgnat_memory(self, facts=False):
        print(f"memory screen of {facts!r}")

    def ha_facts(self, path):
        self.ha_path = path
        return self.ha

    def show_ha(self, args, facts=None):
        print(f"ha screen {args} of {sorted(facts)}")


_nf = "show bpa translation: 9.9.9.9 is not a public address of any pool\n"
sh = FakeShell({"show cgnat drops": drops,
                "show cgnat identity": "no identity table: every record says 'unknown'\n",
                "show cgnat translation 9.9.9.9 1": _nf})
got = A.cgnat_read(sh, "drops")
check(got["text"] == drops and not got["failed"]
      and got["fields"] == C.parse_drops(drops),
      "cgnat_read: the VTY's screen, not failed, and its fields")
got = A.cgnat_read(sh, "identity")
check(not got["failed"] and got["fields"]["loaded"] is False,
      "a good screen that reads `words: text` is not failed (the lab's GetIdentity)")
got = A.cgnat_read(sh, "translation", {"public_address": "9.9.9.9", "port": 1})
check(got["failed"] and got["text"] == _nf and sh.fed[-1] == "show cgnat translation 9.9.9.9 1",
      "the dataplane's refusal is a failed read, its words kept")
got = A.cgnat_read(sh, "forward", {"pool": "a b"})
check("error" in got and sh.fed[-1] == "show cgnat translation 9.9.9.9 1",
      "a refused argument reaches no VTY")
got = A.cgnat_read(FakeShell({"show cgnat drops": drops}, up=False), "drops")
check(got["failed"] and "not answering" in got["text"],
      "a dataplane that does not answer is a failed read, before any screen")

sh.mem = {"heap_bytes": 4 << 30, "heap_used_bytes": 1 << 30, "sessions": None,
          "sessions_fit": 5, "refused_no_memory": 0, "given_gb": None,
          "given_in_effect": False}
got = A.cgnat_memory_read(sh)
check(got["facts"] is sh.mem and got["text"].startswith("memory screen of {"),
      "cgnat_memory_read: one reading, and the screen drawn from it")
m = A.memory_fields(sh.mem)
check(m["sessions"] == 0 and m["sessions_known"] is False and m["given"] is False
      and m["given_gb"] == 0 and m["heap_bytes"] == 4 << 30,
      f"memory_fields: an unknown count and no figure are said, not zeros: {m}")
m = A.memory_fields(dict(sh.mem, sessions=7, given_gb=8, given_in_effect=True))
check(m["sessions"] == 7 and m["sessions_known"] and m["given"] and m["given_gb"] == 8
      and m["given_in_effect"], "and a known count, and a figure in effect")
check(A.memory_fields(None) is None, "a heap that was not read stays None")

sh.ha = {"dataplane": True, "fingerprint": "f1", "pools": ["nb"],
         "routers": [{"interface": "ens18", "vr_id": 10, "state": "master",
                      "addresses": ["200.150.0.1"], "mac": "00:00:5e:00:01:0a",
                      "configured": 200, "advertising": 190}],
         "peer_path": "/etc/prisma-routing/peer.conf", "peer_read": True,
         "peer_unread": "", "peer_fingerprint": "f2", "peer_pools": ["nb"],
         "peer_copied": 1759580000.0, "routers_either": True,
         "pair_findings": ["the same priority on both boxes"],
         "divergences": [("nb", "block-size 2048", "block-size 1024")]}
got = A.ha_read(sh)
check(sh.ha_path is None and got["facts"] is sh.ha and got["text"].startswith("ha screen []"),
      "ha_read: the default peer copy, one reading, the screen drawn from it")
# `show cgnat ha peer <file>` through GetHaStatusRequest.peer_path (#558):
# the facts read at that path, and the screen the VTY prints for those
# words. Mutation shown red: ha_read() ignoring `path` (the default copy
# read, and the screen of `show cgnat ha`).
got = A.ha_read(sh, "/var/tmp/peer-copy.conf")
check(sh.ha_path == "/var/tmp/peer-copy.conf"
      and got["text"].startswith("ha screen ['peer', '/var/tmp/peer-copy.conf']"),
      f"ha_read: a path is read there, and the screen is `peer <path>`'s: "
      f"{sh.ha_path!r} {got['text'][:60]!r}")
got = A.handle(sh, "ha", {"path": "/var/tmp/x.conf"})
check(sh.ha_path == "/var/tmp/x.conf", "and the worker's `ha` passes it on")
got = A.handle(sh, "ha", {"path": ""})
check(sh.ha_path is None and got["text"].startswith("ha screen []"),
      "an empty path is the default copy")
check(A.ha_peer_path_error("") is None
      and A.ha_peer_path_error("/etc/prisma-routing/peer.conf") is None,
      "ha_peer_path_error: the default and an absolute path are taken")
check("not an absolute path" in (A.ha_peer_path_error("peer.conf") or "")
      and "not an absolute path" in (A.ha_peer_path_error("../etc/shadow") or ""),
      "a relative path is refused: an API worker has no directory an "
      "operator chose")
check(A.ha_peer_path_error("/tmp/a\0b") == "peer_path: not a path"
      and A.ha_peer_path_error("/" + "a" * 4096) == "peer_path: not a path",
      "and a NUL, or longer than PATH_MAX")
h = A.ha_fields(sh.ha)
check(h["routers"] == [{"interface": "ens18", "vr_id": 10, "state": "master",
                        "addresses": ["200.150.0.1"],
                        "virtual_mac": "00:00:5e:00:01:0a",
                        "priority_configured": 200, "priority_advertised": 190}],
      f"ha_fields: a router, both priorities: {h['routers']}")
check(h["divergences"] == [{"pool": "nb", "here": "block-size 2048",
                            "there": "block-size 1024"}] and h["agree"] is False,
      "a divergence, and no agreement")
check(A.ha_fields(dict(sh.ha, divergences=[]))["agree"] is True,
      "agreement only when the peer was read and nothing diverges")
check(A.ha_fields(dict(sh.ha, divergences=[], peer_read=False))["agree"] is False
      and A.ha_fields(dict(sh.ha, divergences=[], dataplane=False))["agree"] is False,
      "never on a question left unanswered")

print("=== the patterns are bpa_cli.c's and bpa_audit.c's format strings ===")
src = ""
for name in ("bpa_cli.c", "bpa_audit.c"):
    src += open(os.path.join(ROOT, "src", "plugins", "bpa", name)).read()
flat = re.sub(r'"\s*\n\s*"', "", src)   # adjacent C string literals joined
for fmt in (
        # translation
        "%U:%u belongs to pool %v but to no block",
        "%U:%u  ->  %U", "  pool %v, block %u, DETERMINISTIC NAT64",
        "  pool %v, block %u, DETERMINISTIC\"", "%U:%u  ->  nobody, right now",
        "  pool %v, %s %u, %s", "\"reserve block\" : \"block\"",
        "\"DETERMINISTIC RESERVE\"", ": \"DYNAMIC\"",
        # top
        "%v %v %v/%v %4u%% %7u %v", "%u subscriber(s) holding blocks",
        "no subscriber holds a block right now",
        # drops
        "\"bpa-in2out\"", "\"bpa-out2in\"", "  packets that did not get through",
        "  everything else it counted", "  nothing dropped", "  %v  %s",
        # blocks
        "pool %v (hold-down %us)",
        "pool %v (deterministic reserve, %u block(s) per public address, %u in all, "
        "hold-down %us)",
        "  thread %u: %u/%u blocks in use, %Lu allocations (%Lu extensions), %Lu "
        "releases, %Lu refusals, %u pinned",
        "%u lent to another thread, so %u of this slice",
        "  total: %Lu/%Lu in use, %Lu allocations (%Lu extensions), %Lu releases, "
        "%Lu refusals, %Lu pinned",
        "  stride %u: %Lu extensions placed where it says, %Lu fell back to the free "
        "list",
        "  RFC 4787 REQ-2 paired: %Lu of %Lu extension(s) on the holder's own address",
        "  reserve exhausted %Lu time(s)",
        # identity
        "no identity table: every record says 'unknown'",
        "identity table: %u entry(s), read %U", "radius accounting: listening on port %u",
        "  packets    %Lu received, %Lu applied, %Lu answered",
        "  refused    %Lu bad authenticator, %Lu malformed",
        "  %u name(s) were longer than %u characters and are carried in part",
        # history verify
        "configuration trail: %v", "no trail at %v",
        "  TRUNCATED: the file ends mid-entry", "  BROKEN before entry %llu",
        "    the sequence jumps from %llu to %llu: %llu entr%s removed from the middle",
        "  BROKEN at entry %llu", "  %llu entr%s carr%s no digest",
        "  INCOMPLETE: %llu entr%s could not be written",
        "  no chained entries: nothing here is covered",
        "  %llu entr%s checked, chain intact",
        "  TRUNCATED: the file ends at entry %llu and this dataplane wrote up to "
        "%llu - %llu entr%s removed from the end",
        "  head  seq %llu sha256 %U",
        # alg
        "  %-6s %-3s  %s", "no pools configured",
        # forward
        "pool %v\"", "  %U %U:%u -> %U:%u", "no forwards configured"):
    check(fmt in flat, f"the plugin still prints {fmt!r}")
# The lines whose words the parsers match beyond the format string itself.
check('"y" : "ies"' in flat and '"ies" : "y"' in flat,
      "entr%s/carr%s are y/ies and ies/y, as the patterns read them")
check("(p->alg & BPA_ALG_##sym) ? \"on\" : \"off\"" in flat, "an ALG is on or off")
_code = re.sub(r"/\*.*?\*/", "", flat, flags=re.S)   # the comments name them
_span = _code[_code.find("bpa_show_top_command_fn"):_code.find("bpa_show_drops_command")]
check(_span and re.search(r"%12Lu|%-12v|%5u/%-5u|%9u", _span) is None,
      "no column of show bpa top or drops has a width vppinfra would cut it at")

print()
print("ALL TESTS PASSED" if not fail else f"{fail} FAILURES")
sys.exit(1 if fail else 0)
