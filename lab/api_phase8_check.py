#!/usr/bin/env python3
"""
lab/api-grpc.sh's client for BFD's phase 2 (#559): bfdd's sessions typed
(ListBfdSessions) and their changes streamed (WatchBfd), against a running
bfdd that lab/api-grpc.sh gave one `peer` on d0 - a dummy interface nobody
answers BFD on, so the session is down and stays down, which is a state the
lab KNOWS rather than one it hopes for.

Each read is held two ways, as phases 6 and 7 are: `text` against the VTY's
own screen, captured with `prismash -c` into <screens dir> just before; the
fields against what the lab configured. WatchBfd is driven by changing bfdd
under it: a second peer added (APPEARED) and removed (GONE), the events read
as they come.

Arguments: <stubs dir> <certs dir> <host:port> <screens dir>. One RESULT line
per check.
"""
import os
import queue
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET, SCREENS = sys.argv[2], sys.argv[3], sys.argv[4]
PEER, SECOND = "192.0.2.77", "192.0.2.78"


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
    with open(os.path.join(SCREENS, name)) as f:
        return f.read()


def untimed(text):
    """
    A BFD screen with its clock taken out: `down for 4 s` is a different
    line a second later, and the VTY's screen was captured before the rpc
    ran. Everything else on the screen still has to be the same.
    """
    return re.sub(r"\b(up|down|init|shutdown) for [^,\n]*", r"\1 for T",
                  text.rstrip())


def same(rpc_text, name):
    return untimed(rpc_text) == untimed(screen(name))


def vtysh(*lines):
    args = ["vtysh", "-c", "configure terminal"]
    for l in lines:
        args += ["-c", l]
    subprocess.run(args, capture_output=True, timeout=30)


op = rpc.OperationalServiceStub(channel("root"))


def sessions():
    r = op.ListBfdSessions(pb.ListBfdSessionsRequest(), timeout=60)
    s = [x for x in r.sessions if x.peer == PEER]
    ok = (r.answered and len(s) == 1 and s[0].interface == "d0"
          and s[0].configured and s[0].state == pb.BfdSession.STATE_DOWN
          and s[0].detect_multiplier == 3 and s[0].receive_interval_ms == 300
          and s[0].detection_timeout_ms > 0 and same(r.text, "bfd-peers"))
    result("sessions", ok, f"answered={r.answered} n={len(r.sessions)} "
           f"{[(x.peer, x.interface, x.state, x.detect_multiplier) for x in r.sessions]}"
           f" same={same(r.text, 'bfd-peers')}")


def shape(text):
    """
    `show bfd peer`'s screen with every number taken out: it prints the
    session's packet counters, which bfdd moves every 300 ms between the
    VTY's capture and the rpc. The lines, the words and their order stay.
    """
    return re.sub(r"\d+", "N", untimed(text))


def one_peer():
    r = op.ListBfdSessions(pb.ListBfdSessionsRequest(peer=PEER), timeout=60)
    ok = ([x.peer for x in r.sessions] == [PEER]
          and shape(r.text) == shape(screen("bfd-peer"))
          and PEER in r.text)
    r2 = op.ListBfdSessions(pb.ListBfdSessionsRequest(peer="192.0.2.200"), timeout=60)
    result("one-peer", ok and r2.answered and len(r2.sessions) == 0,
           f"{[x.peer for x in r.sessions]} "
           f"shape={shape(r.text) == shape(screen('bfd-peer'))} "
           f"none-for-other={len(r2.sessions)}")


def refused():
    try:
        op.ListBfdSessions(pb.ListBfdSessionsRequest(peer="bananas"), timeout=30)
        result("peer-refused", False, "answered")
    except grpc.RpcError as e:
        result("peer-refused", e.code() == grpc.StatusCode.INVALID_ARGUMENT,
               f"{e.code().name} {e.details()}")
    try:
        next(iter(op.WatchBfd(pb.WatchBfdRequest(interval_seconds=61), timeout=30)))
        result("interval-refused", False, "answered")
    except grpc.RpcError as e:
        result("interval-refused", e.code() == grpc.StatusCode.INVALID_ARGUMENT,
               f"{e.code().name} {e.details()}")


def netview():
    r = rpc.OperationalServiceStub(channel("netview")).ListBfdSessions(
        pb.ListBfdSessionsRequest(), timeout=60)
    result("netview-reads", same(r.text, "bfd-netview"),
           f"answered={r.answered} n={len(r.sessions)}")


def watch():
    q = queue.Queue()
    call = op.WatchBfd(pb.WatchBfdRequest(interval_seconds=1), timeout=120)

    def pump():
        try:
            for ev in call:
                q.put(ev)
        except grpc.RpcError:
            pass
    threading.Thread(target=pump, daemon=True).start()

    def wait(kind, peer, limit=20):
        end = time.time() + limit
        while time.time() < end:
            try:
                ev = q.get(timeout=max(end - time.time(), 0.1))
            except queue.Empty:
                break
            if ev.kind == kind and ev.session.peer == peer:
                return ev
        return None

    first = wait(pb.BfdEvent.KIND_INITIAL, PEER)
    result("watch-initial", first is not None
           and first.session.state == pb.BfdSession.STATE_DOWN
           and first.at.seconds > 0, f"{first.session.peer if first else None}")
    t0 = time.time()
    vtysh("bfd", f"peer {SECOND} interface d0", "exit", "exit")
    came = wait(pb.BfdEvent.KIND_APPEARED, SECOND)
    result("watch-appeared", came is not None,
           f"after {time.time() - t0:.1f}s" if came else "no event")
    t0 = time.time()
    vtysh("bfd", f"no peer {SECOND} interface d0", "exit", "exit")
    gone = wait(pb.BfdEvent.KIND_GONE, SECOND)
    result("watch-gone", gone is not None
           and gone.previous_state == gone.session.state,
           f"after {time.time() - t0:.1f}s" if gone else "no event")
    call.cancel()


for name, fn in (("sessions", sessions), ("one-peer", one_peer),
                 ("peer-refused", refused), ("netview-reads", netview),
                 ("watch", watch)):
    run(name, fn)
