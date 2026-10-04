#!/usr/bin/env python3
"""
lab/api-grpc.sh's client for #558's sixth phase: the rest of `show cgnat`,
typed, against a running dataplane.

Each rpc is held two ways, neither of them the parser checking itself:

  * its `text` against the VTY's own screen, which lab/api-grpc.sh captured
    with `prismash -c "show cgnat ..."` into <screens dir> with the traffic
    stopped - the rpc and the prompt asked the same question of the same box;
  * its fields against what the lab KNOWS independently: the pools, forwards
    and ALGs it configured, the subscribers it sent traffic from, the block a
    subscriber holds as GetSubscriber says it, the heap the screen prints.

Arguments: <stubs dir> <certs dir> <host:port> <screens dir>. One RESULT line
per check.
"""
import ipaddress
import os
import sys

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET, SCREENS = sys.argv[2], sys.argv[3], sys.argv[4]
LONG = "residential-cgnat"   # 17 letters: show bpa top's pool column was 12


def channel(who):
    ca = open(f"{CERTS}/ca.crt", "rb").read()
    creds = grpc.ssl_channel_credentials(ca, open(f"{CERTS}/{who}.key", "rb").read(),
                                         open(f"{CERTS}/{who}.crt", "rb").read())
    return grpc.secure_channel(TARGET, creds,
                               options=(("grpc.ssl_target_name_override", "prisma-api"),))


def result(name, ok, detail=""):
    print(f"RESULT {name} {'ok' if ok else 'FAIL'} {detail}".rstrip(), flush=True)


def run(name, fn):
    try:
        fn()
    except grpc.RpcError as e:
        result(name, False, f"{e.code().name} {e.details()[:160]}")
    except Exception as e:  # noqa: BLE001 - every check reports, none aborts the rest
        result(name, False, f"{type(e).__name__}: {e}")


def screen(name):
    """The VTY's screen the lab captured, as `prismash -c` printed it."""
    with open(os.path.join(SCREENS, name)) as f:
        return f.read()


def same(rpc_text, name):
    """The rpc's text is the VTY's screen, trailing blank space aside."""
    return rpc_text.rstrip() == screen(name).rstrip()


op = rpc.OperationalServiceStub(channel("root"))
T = 20


def translation():
    # A deterministic IPv4 pool: the formula, for any instant.
    t = op.LookupTranslation(pb.LookupTranslationRequest(public_address="200.150.0.16",
                                                         port=5000), timeout=T)
    ok = (t.source == pb.Translation.SOURCE_FORMULA and not t.now_only
          and t.pool == "nb" and not t.nat64
          and ipaddress.ip_address(t.subscriber) in ipaddress.ip_network("100.64.0.0/22")
          and same(t.text, "translation-nb"))
    result("translation-formula", ok, f"{t.subscriber} block {t.block} {t.pool}")
    # A NAT64 pool's formula: the subscriber is its delegated /56.
    t = op.LookupTranslation(pb.LookupTranslationRequest(public_address="200.150.0.48",
                                                         port=5000), timeout=T)
    net = ipaddress.ip_network(t.subscriber, strict=True) if "/" in t.subscriber else None
    ok = (t.nat64 and t.pool == "v6" and net is not None and net.prefixlen == 56
          and net.subnet_of(ipaddress.ip_network("2001:db8:1200::/40"))
          and t.source == pb.Translation.SOURCE_FORMULA and same(t.text, "translation-v6"))
    result("translation-nat64", ok, f"{t.subscriber} {pb.Translation.Source.Name(t.source)}")
    # A dynamic pool: the block a subscriber holds, as GetSubscriber reads it,
    # looked up the other way round.
    sub = op.GetSubscriber(pb.GetSubscriberRequest(address="100.64.16.1"), timeout=T)
    b = sub.blocks[0]
    t = op.LookupTranslation(pb.LookupTranslationRequest(public_address=b.public_address,
                                                         port=b.first_port + 1), timeout=T)
    result("translation-dynamic",
           t.subscriber == "100.64.16.1" and t.pool == LONG and t.now_only
           and t.source == pb.Translation.SOURCE_DYNAMIC,
           f"{b.public_address}:{b.first_port + 1} -> {t.subscriber} "
           f"{pb.Translation.Source.Name(t.source)}")
    # No pool's address, and no address at all.
    try:
        op.LookupTranslation(pb.LookupTranslationRequest(public_address="9.9.9.9",
                                                         port=1), timeout=T)
        result("translation-not-found", False, "answered")
    except grpc.RpcError as e:
        result("translation-not-found", e.code() == grpc.StatusCode.NOT_FOUND
               and "not a public address" in e.details(), e.code().name)
    try:
        op.LookupTranslation(pb.LookupTranslationRequest(public_address="200.150.0.16 | x",
                                                         port=1), timeout=T)
        result("translation-refused", False, "answered")
    except grpc.RpcError as e:
        result("translation-refused", e.code() == grpc.StatusCode.INVALID_ARGUMENT,
               e.code().name)


def top():
    r = op.ListTop(pb.ListTopRequest(), timeout=T)
    result("top", same(r.text, "top") and r.holding_blocks >= 5
           and len(r.subscribers) == min(10, r.holding_blocks)
           and all(s.ports_total > 0 and s.blocks >= 1 for s in r.subscribers),
           f"{r.holding_blocks} holding, {len(r.subscribers)} rows")
    # Every row, so the five subscribers of the long pool are among them
    # whatever the order: pool lg holds sixty of its own by now.
    every = op.ListTop(pb.ListTopRequest(count=1000), timeout=T)
    mine = {s.subscriber: s.pool for s in every.subscribers
            if s.subscriber.startswith("100.64.16.")}
    result("top-long-pool", len(mine) >= 5 and set(mine.values()) == {LONG}
           and len(every.subscribers) == every.holding_blocks,
           f"the 17-letter pool read whole: {sorted(mine.items())}")
    r2 = op.ListTop(pb.ListTopRequest(count=2), timeout=T)
    result("top-count", len(r2.subscribers) == 2
           and r2.holding_blocks == r.holding_blocks, f"{len(r2.subscribers)} rows")


def drops():
    r = op.GetDrops(pb.GetDropsRequest(), timeout=T)
    nodes = {n.node: n for n in r.nodes}
    translated = [c for c in nodes.get("in2out", pb.DropNode()).counters
                  if c.reason == "packets translated"]
    result("drops", same(r.text, "drops") and set(nodes) == {"in2out", "out2in"}
           and translated and translated[0].count > 0 and not translated[0].dropped,
           f"in2out {[(c.reason, c.count, c.dropped) for c in nodes['in2out'].counters]}")


def memory():
    m = op.GetCgnatMemory(pb.GetCgnatMemoryRequest(), timeout=T)
    s = screen("memory")
    # The sessions row is on the screen exactly when the count is known.
    sessions_ok = ((f"sessions now          {m.sessions:,}" in s) if m.sessions_known
                   else "sessions now" not in s)
    result("memory", m.heap_bytes > 0 and m.sessions_fit > 0 and sessions_ok
           and f"main heap GB          {m.heap_bytes / (1 << 30):.2f}" in s
           and f"in use GB             {m.heap_used_bytes / (1 << 30):.2f}" in s
           and f"sessions that fit     {m.sessions_fit:,}" in s,
           f"heap {m.heap_bytes} used {m.heap_used_bytes} sessions {m.sessions} "
           f"(known {m.sessions_known}) fit {m.sessions_fit}")


def blocks():
    r = op.ListBlocks(pb.ListBlocksRequest(), timeout=T)
    by = {p.pool: p for p in r.pools}
    p = by.get(LONG)
    result("blocks", same(r.text, "blocks") and p is not None and not p.reserve
           and p.total.in_use >= 5 and p.total.allocations >= 5
           and sum(w.counts.in_use for w in p.workers) == p.total.in_use,
           f"{sorted(by)}; {LONG}: {p.total.in_use if p else '-'} in use over "
           f"{len(p.workers) if p else 0} worker(s)")


def identity():
    r = op.GetIdentity(pb.GetIdentityRequest(), timeout=T)
    result("identity", same(r.text, "identity") and not r.loaded and r.entries == 0,
           r.text.strip()[:60])


def history():
    r = op.VerifyHistory(pb.VerifyHistoryRequest(), timeout=T)
    v = pb.HistoryVerification.Verdict.Name(r.verdict)
    ok = same(r.text, "history") and r.path and (
        (r.verdict == pb.HistoryVerification.VERDICT_INTACT and r.checked > 0
         and len(r.head_sha256) == 64 and "chain intact" in r.text)
        or (r.verdict == pb.HistoryVerification.VERDICT_NO_TRAIL
            and "no trail" in r.text))
    result("history", ok, f"{v} checked {r.checked} at {r.path}")


def algs():
    r = op.ListAlgs(pb.ListAlgsRequest(), timeout=T)
    by = {p.pool: {a.alg: a.on for a in p.algs} for p in r.pools}
    result("algs", same(r.text, "alg") and by.get(LONG, {}).get("rtsp") is False
           and by.get(LONG, {}).get("ftp") is True
           and by.get("v6", {}).get("ftp") is True
           and by.get("v6", {}).get("sip") is False,
           f"{LONG} {by.get(LONG)}, v6 {by.get('v6')}")


def forwards():
    r = op.ListForwards(pb.ListForwardsRequest(), timeout=T)
    got = {(f.pool, f.protocol, f.public_port, f.private_address, f.private_port)
           for f in r.forwards}
    want = {(LONG, "tcp", 1600, "100.64.16.5", 80),
            ("v6", "tcp", 22600, "2001:db8:1200:ab00::10", 8080)}
    result("forwards", same(r.text, "forward") and want <= got
           and all(f.public_address for f in r.forwards), f"{sorted(got)}")
    r = op.ListForwards(pb.ListForwardsRequest(pool="v6"), timeout=T)
    result("forwards-pool", [f.pool for f in r.forwards] == ["v6"]
           and same(r.text, "forward-v6"), f"{[f.private_address for f in r.forwards]}")


def ha():
    r = op.GetHaStatus(pb.GetHaStatusRequest(), timeout=T)
    result("ha", same(r.text, "ha") and r.dataplane and not r.peer_read
           and r.peer_unread and not r.agree and {"nb", LONG, "v6"} <= set(r.pools)
           and r.fingerprint, f"pools {list(r.pools)}; peer: {r.peer_unread[:60]}")


def unprivileged():
    # An account with no role gets from the API what the VTY gives it: the
    # screen, when the VTY can read the dataplane for it, and a refusal when
    # it cannot - measured here, where netview is in no group and the VTY's
    # own `show cgnat alg` as netview cannot open the dataplane's socket.
    vty = screen("alg-netview")
    o = rpc.OperationalServiceStub(channel("netview"))
    try:
        r = o.ListAlgs(pb.ListAlgsRequest(), timeout=T)
        result("netview-reads", same(r.text, "alg-netview")
               and any(p.pool == LONG for p in r.pools), "ListAlgs as netview: the screen")
    except grpc.RpcError as e:
        result("netview-reads", e.code() == grpc.StatusCode.UNAVAILABLE
               and LONG not in vty and ("ermission" in vty or "not answering" in vty
                                        or "denied" in vty),
               f"refused {e.code().name}, as the VTY: {vty.strip()[:80]!r}")


for name, fn in (("translation", translation), ("top", top), ("drops", drops),
                 ("memory", memory), ("blocks", blocks), ("identity", identity),
                 ("history", history), ("algs", algs), ("forwards", forwards),
                 ("ha", ha), ("netview-reads", unprivileged)):
    run(name, fn)
