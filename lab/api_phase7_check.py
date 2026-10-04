#!/usr/bin/env python3
"""
lab/api-grpc.sh's client for #558's seventh phase: the rest of the VTY's
screens, typed, and the three probes streamed, against a running dataplane
and FRR.

Each read is held two ways, as phase 6's are: its `text` against the VTY's
own screen, which lab/api-grpc.sh captured with `prismash -c` into <screens
dir> just before; and its fields against what the lab KNOWS - the interface
it put OSPF and IS-IS on, the logging it set, the enterprise number the box
serves under, the dataplane's own interfaces. Each probe against the address it
was sent to, and the stream against the tool's own lines.

Arguments: <stubs dir> <certs dir> <host:port> <screens dir>. One RESULT line
per check.
"""
import os
import sys
import threading
import time

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET, SCREENS = sys.argv[2], sys.argv[3], sys.argv[4]
PHASE = sys.argv[5] if len(sys.argv) > 5 else "reads"


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


def same(rpc_text, name):
    return rpc_text.rstrip() == screen(name).rstrip()


op = rpc.OperationalServiceStub(channel("root"))
T = 30


def ospf():
    r = op.ListOspfInterfaces(pb.ListOspfInterfacesRequest(), timeout=T)
    d0 = [i for i in r.interfaces if i.name == "d0"]
    ok = (r.answered and same(r.text, "ospf-interface") and d0
          and d0[0].area == "0.0.0.0" and d0[0].ospf_mtu > 0
          and not d0[0].dataplane_mtu_known and d0[0].cost > 0)
    result("ospf-interfaces", ok,
           f"{[(i.name, i.area, i.state, i.cost, i.ospf_mtu) for i in r.interfaces]}")


def isis():
    r = op.ListIsisInterfaces(pb.ListIsisInterfacesRequest(), timeout=T)
    d0 = [c for c in r.circuits if c.name == "d0"]
    # The punt as the screen says it: here the VTY reports it unknown - the
    # lab's dataplane does not answer the question it asks - and the field
    # has to say the same, not a reading the screen did not make.
    unknown = "is unknown" in screen("isis-interface")
    ok = (r.answered and same(r.text, "isis-interface") and d0
          and d0[0].area == "HEXA" and r.punt_known == (not unknown))
    result("isis-interfaces", ok,
           f"{[(c.name, c.area, c.state, c.level, c.silent) for c in r.circuits]} "
           f"punting={r.punting} drops={r.osi_drops}")


def hardware():
    r = op.GetHardware(pb.GetHardwareRequest(), timeout=T)
    ok = (same(r.text, "hardware") and r.dataplane_answered
          and "pg0" in r.dataplane_holds and "local0" not in r.dataplane_holds)
    result("hardware", ok, f"holds {list(r.dataplane_holds)} nics "
           f"{[(n.name, n.pci, n.driver) for n in r.nics]} route {r.default_route_interface!r}")


def logging():
    r = op.GetRoutingLogging(pb.GetRoutingLoggingRequest(), timeout=T)
    by = {d.daemon: d for d in r.daemons}
    z = by.get("zebra")
    ok = (same(r.text, "logging") and z is not None and z.syslog.enabled
          and z.syslog.level == "warnings" and z.file.enabled
          and z.file.filename == "/tmp/frr-api7.log" and z.protocol == "ZEBRA"
          and {"zebra", "ospfd", "isisd"} <= set(by))
    result("routing-logging", ok,
           f"{[(d.daemon, d.syslog.level, d.file.filename) for d in r.daemons]}")


def snmp():
    r = op.GetSnmpFile(pb.GetSnmpFileRequest(kind=pb.GetSnmpFileRequest.KIND_MIB),
                       timeout=T)
    # The number the box serves under: the product's own (IANA 67033) on a
    # box nobody gave another.
    pen = r.enterprise_number
    ok = (r.content == screen("snmp-mib") and pen.isdigit() and pen in r.content
          and "@PEN@" not in r.content and r.filename == "PRISMA-ROUTING-MIB.txt")
    result("snmp-mib", ok, f"{r.filename} {r.media_type} pen {r.enterprise_number} "
           f"{len(r.content)} bytes")
    z = op.GetSnmpFile(pb.GetSnmpFileRequest(
        kind=pb.GetSnmpFileRequest.KIND_ZABBIX_TEMPLATE), timeout=T)
    import json
    ok = (z.content == screen("snmp-zabbix") and z.media_type == "application/json"
          and json.loads(z.content) and z.enterprise_number == pen
          and pen in z.content and "@PEN@" not in z.content)
    result("snmp-zabbix", ok, f"{z.filename} {len(z.content)} bytes")
    try:
        op.GetSnmpFile(pb.GetSnmpFileRequest(), timeout=T)
        result("snmp-kind-refused", False, "no kind was answered")
    except grpc.RpcError as e:
        result("snmp-kind-refused", e.code() == grpc.StatusCode.INVALID_ARGUMENT,
               e.code().name)


def events(call):
    return [e for e in call]


def ping():
    got = events(op.Ping(pb.ProbeRequest(host="127.0.0.1", count=3), timeout=T))
    replies = [e.reply for e in got if e.WhichOneof("kind") == "reply"]
    end = got[-1].end if got and got[-1].WhichOneof("kind") == "end" else None
    lines = [e.line for e in got if e.WhichOneof("kind") != "end"]
    vty = [l for l in screen("ping").splitlines()]
    ok = (len(replies) == 3 and [r.sequence for r in replies] == [1, 2, 3]
          and all(r.from_address == "127.0.0.1" and r.rtt_ms >= 0 for r in replies)
          and end is not None and end.exit_status == 0 and end.transmitted == 3
          and end.received == 3 and end.rtt_known
          and len(lines) == len(vty) and lines[0].startswith("PING 127.0.0.1"))
    result("ping", ok, f"{len(got)} events, replies {[r.sequence for r in replies]}, "
           f"end {end.exit_status if end else None} {end.received if end else ''}/"
           f"{end.transmitted if end else ''}; vty {len(vty)} lines, api {len(lines)}")


def ping_stream():
    # The rows arrive as ping prints them, a second apart, not at the end.
    call = op.Ping(pb.ProbeRequest(host="127.0.0.1", count=4), timeout=T)
    t0 = time.time()
    seen = []
    for e in call:
        if e.WhichOneof("kind") == "reply":
            seen.append(round(time.time() - t0, 1))
    result("ping-streams", len(seen) == 4 and seen[-1] - seen[0] >= 2.5,
           f"replies at {seen} s")


def ping_unanswered():
    got = events(op.Ping(pb.ProbeRequest(host="192.0.2.77", count=2), timeout=T))
    end = got[-1].end if got and got[-1].WhichOneof("kind") == "end" else None
    result("ping-no-reply", end is not None and end.exit_status != 0
           and end.received == 0 and end.loss_percent == 100.0 and not end.rtt_known,
           f"end {end}".replace("\n", " ")[:160])


def refused():
    out = []
    for req, say in ((pb.ProbeRequest(host="-c9 x"), "not a host"),
                     (pb.ProbeRequest(host="127.0.0.1", count=101), "count out of range"),
                     (pb.ProbeRequest(host="127.0.0.1", source="a b"), "not a source")):
        try:
            events(op.Ping(req, timeout=T))
            out.append((say, "answered"))
        except grpc.RpcError as e:
            out.append((say, e.code().name, say in e.details()))
    result("probe-refused", all(len(o) == 3 and o[1] == "INVALID_ARGUMENT" and o[2]
                                for o in out), f"{out}")


def absent():
    try:
        events(op.Mtr(pb.ProbeRequest(host="127.0.0.1", count=1), timeout=T))
        result("probe-absent", False, "mtr answered on a box without it")
    except grpc.RpcError as e:
        result("probe-absent", e.code() == grpc.StatusCode.FAILED_PRECONDITION
               and "this box has no mtr" in e.details() and "mtr-tiny" in e.details(),
               f"{e.code().name} {e.details()[:90]!r}")


def cancel():
    # A long ping cancelled after its first reply: the box's ping stops - its
    # process gone within seconds - and the account's console answers the
    # next call, the drain having read the cancelled stream's end.
    call = op.Ping(pb.ProbeRequest(host="127.0.0.1", count=60), timeout=T * 4)
    for e in call:
        if e.WhichOneof("kind") == "reply":
            break
    call.cancel()
    t0 = time.time()
    gone = False
    while time.time() - t0 < 8:
        if os.system("pgrep -f '^/usr/bin/ping -c 60 ' >/dev/null") != 0:
            gone = True
            break
        time.sleep(0.25)
    after = events(op.Ping(pb.ProbeRequest(host="127.0.0.1", count=1), timeout=T))
    ok_after = after and after[-1].end.received == 1
    result("probe-cancel", gone and ok_after,
           f"ping gone {gone} in {round(time.time() - t0, 1)} s; next ping "
           f"{'answered' if ok_after else 'NOT answered'}")


def apart():
    # A probe holds the account's console, not its operational process:
    # GetVersion answers while a 6-second ping runs.
    th = threading.Thread(target=lambda: events(op.Ping(
        pb.ProbeRequest(host="127.0.0.1", count=6), timeout=T)))
    th.start()
    time.sleep(1.5)
    t0 = time.time()
    v = op.GetVersion(pb.GetVersionRequest(), timeout=4)
    took = time.time() - t0
    th.join()
    result("probe-apart", bool(v.release) and took < 3, f"GetVersion in {took:.2f}s")


def traceroute():
    got = events(op.Traceroute(pb.ProbeRequest(host="127.0.0.1", count=3), timeout=T))
    hops = [e.hop for e in got if e.WhichOneof("kind") == "hop"]
    end = got[-1].end if got and got[-1].WhichOneof("kind") == "end" else None
    ok = (hops and hops[0].hop == 1 and hops[0].answered
          and hops[0].address == "127.0.0.1" and end is not None
          and end.exit_status == 0 and got[0].line.startswith("traceroute to 127.0.0.1"))
    result("traceroute", ok, f"hops {[(h.hop, h.address, h.rtt_ms) for h in hops]}")


def mtr():
    got = events(op.Mtr(pb.ProbeRequest(host="127.0.0.1", count=2), timeout=T))
    hops = [e.mtr_hop for e in got if e.WhichOneof("kind") == "mtr_hop"]
    end = got[-1].end if got and got[-1].WhichOneof("kind") == "end" else None
    ok = (hops and hops[0].hop == 1 and hops[0].address == "127.0.0.1"
          and hops[0].sent == 2 and hops[0].loss_percent == 0.0
          and end is not None and end.exit_status == 0)
    result("mtr", ok, f"hops {[(h.hop, h.address, h.sent, h.loss_percent) for h in hops]}")


def netview():
    # An account with no role gets what the VTY gives it: show logging, and
    # a ping run as that account - answered or not, as at its own prompt.
    o = rpc.OperationalServiceStub(channel("netview"))
    r = o.GetRoutingLogging(pb.GetRoutingLoggingRequest(), timeout=T)
    vty = screen("ping-netview")
    try:
        got = events(o.Ping(pb.ProbeRequest(host="127.0.0.1", count=1), timeout=T))
        replies = sum(1 for e in got if e.WhichOneof("kind") == "reply")
        said = f"{replies} reply(ies)"
    except grpc.RpcError as e:
        replies, said = -1, f"{e.code().name} {e.details()[:60]!r}"
    result("netview-probes", r.text.strip() == screen("logging-netview").strip()
           and replies == vty.count("bytes from"),
           f"as netview: {said}; the VTY's own ping: {vty.count('bytes from')} reply(ies)")


CHECKS = {
    "reads": (("ospf", ospf), ("isis", isis), ("hardware", hardware),
              ("logging", logging), ("snmp", snmp), ("ping", ping),
              ("ping-stream", ping_stream), ("ping-no-reply", ping_unanswered),
              ("refused", refused), ("absent", absent), ("cancel", cancel),
              ("apart", apart), ("netview", netview)),
    "tools": (("traceroute", traceroute), ("mtr", mtr)),
}
for name, fn in CHECKS[PHASE]:
    run(name, fn)
