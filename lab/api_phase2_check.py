#!/usr/bin/env python3
"""
lab/api-grpc.sh's client for #558's second phase: the configuration as
fields. Every typed edit is taken through the whole road a box takes it -
SetConfig, Commit, GetConfig of the running configuration - and the typed
read is held to the VTY's own text (GetRunning, which is `show
running-config`), so the two cannot disagree without a check going red.

Arguments: <stubs dir> <certs dir> <host:port>. One RESULT line per check.
"""
import sys

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET = sys.argv[2], sys.argv[3]
D = pb.Diagnostics


def channel(who):
    ca = open(f"{CERTS}/ca.crt", "rb").read()
    creds = grpc.ssl_channel_credentials(ca, open(f"{CERTS}/{who}.key", "rb").read(),
                                         open(f"{CERTS}/{who}.crt", "rb").read())
    return grpc.secure_channel(TARGET, creds,
                               options=(("grpc.ssl_target_name_override", "prisma-api"),))


def result(name, ok, detail=""):
    print(f"RESULT {name} {'ok' if ok else 'FAIL'} {detail}".rstrip(), flush=True)


def diagnostics(err):
    for k, v in err.trailing_metadata() or ():
        if k == "prisma-diagnostics-bin":
            d = pb.Diagnostics()
            d.ParseFromString(v)
            return d
    return pb.Diagnostics()


def block(text, header):
    """The statements of one block of a configuration text: the lines after
    its header, while they are indented deeper than it."""
    lines = text.splitlines()
    for k, ln in enumerate(lines):
        if ln.strip() == header:
            depth = len(ln) - len(ln.lstrip())
            out = []
            for nxt in lines[k + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= depth:
                    break
                if nxt.strip() and nxt.strip() != "exit":
                    out.append(nxt.strip())
            return out
    return []


def run(name, fn):
    try:
        fn()
    except grpc.RpcError as e:
        result(name, False, f"{e.code().name} {e.details()[:160]}")
    except Exception as e:  # noqa: BLE001 - every check reports, none aborts the rest
        result(name, False, f"{type(e).__name__}: {e}")


root = channel("root")
cfg = rpc.ConfigServiceStub(root)


def commit_with(setter):
    """Open a candidate, apply `setter(cid)`, commit; the SetConfig replies."""
    cid = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id
    replies = setter(cid)
    cfg.Commit(pb.CommitRequest(candidate_id=cid, comment="api phase 2"), timeout=120)
    return replies


def iface(cid, **kw):
    fields = kw.pop("fields")
    return cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=cid, interface=pb.InterfaceConfig(name="pg0", **kw),
        fields=fields), timeout=60)


def check_read():
    run_text = cfg.GetRunning(pb.GetRunningRequest(), timeout=30).text
    t = cfg.GetConfig(pb.GetConfigRequest(), timeout=30)
    pg0 = [i for i in t.interfaces if i.name == "pg0"]
    nb = [p for p in t.pools if p.name == "nb"]
    want_if = block(run_text, "interface pg0")
    ok = (t.source == "running" and t.revision and pg0 and nb
          and all(f"ipv4 address {a}" in want_if for a in pg0[0].ipv4)
          and list(pg0[0].ipv4) == ["100.64.3.254/22"]
          and list(nb[0].prefix_inside) == ["100.64.0.0/22"]
          and f"block-size {nb[0].block_size}" in block(run_text, "pool nb"))
    # The box's own lines (#558): read only, never an operator's (this lab
    # has a VRF `red` of its own, which must not be among them).
    owned = [b.line for b in t.box_owned]
    ok = ok and all(b.why for b in t.box_owned) and "vrf red" not in owned \
        and all(x.strip() in owned for x in run_text.splitlines()
                if x.strip() in ("service integrated-vtysh-config", "vrf prisma-bp"))
    result("config-read-running", ok,
           f"pg0 {list(pg0[0].ipv4) if pg0 else None} nb "
           f"{list(nb[0].prefix_inside) if nb else None} box-owned {owned}")


def check_interface_roundtrip():
    rep = commit_with(lambda cid: iface(cid, description="api-typed", mtu=1400,
                                        fields=["description", "mtu"]))
    run_text = cfg.GetRunning(pb.GetRunningRequest(), timeout=30).text
    t = cfg.GetConfig(pb.GetConfigRequest(areas=["interfaces"]), timeout=30)
    pg0 = [i for i in t.interfaces if i.name == "pg0"][0]
    blk = block(run_text, "interface pg0")
    ok = (list(rep.lines) == ["description api-typed", "mtu 1400"]
          and list(rep.path) == ["interface pg0"]
          and pg0.description == "api-typed" and pg0.mtu == 1400
          and "description api-typed" in blk and "mtu 1400" in blk
          and not t.pools and not t.HasField("logging"))
    result("set-interface-roundtrip", ok,
           f"lines {list(rep.lines)} typed {pg0.description!r}/{pg0.mtu} vty {blk}")
    rep2 = commit_with(lambda cid: iface(cid, description="", mtu=0,
                                         fields=["description", "mtu"]))
    pg0 = [i for i in cfg.GetConfig(pb.GetConfigRequest(areas=["interfaces"]),
                                    timeout=30).interfaces if i.name == "pg0"][0]
    blk = block(cfg.GetRunning(pb.GetRunningRequest(), timeout=30).text, "interface pg0")
    # What `no mtu` leaves is the dataplane's, read back: the typed field is
    # held to the VTY's text, not to a number this check assumes. Measured on
    # this lab (0.149.18): after `no mtu` committed, `show running-config`
    # still says `mtu 1400` - the VTY's own `no mtu` keeps the dataplane's
    # MTU, typed or typed at the prompt alike; a defect of the line form,
    # reported apart from #558, which this API reproduces faithfully.
    vty_mtu = [int(x.split()[1]) for x in blk if x.startswith("mtu ")]
    result("set-interface-removes", list(rep2.lines) == ["no description", "no mtu"]
           and pg0.description == "" and not any(x.startswith("description") for x in blk)
           and pg0.mtu == (vty_mtu[0] if vty_mtu else 0),
           f"{list(rep2.lines)} typed mtu {pg0.mtu} vty {vty_mtu} desc {pg0.description!r}")


def check_pool_roundtrip():
    def setter(cid):
        return cfg.SetConfig(pb.SetConfigRequest(
            candidate_id=cid,
            pool=pb.PoolConfig(name="nb", utilisation_alarm=80, max_sessions=1000,
                               timeouts={"udp": 90}),
            fields=["utilisation_alarm", "max_sessions", "timeouts"]), timeout=60)
    rep = commit_with(setter)
    run_text = cfg.GetRunning(pb.GetRunningRequest(), timeout=30).text
    nb = [p for p in cfg.GetConfig(pb.GetConfigRequest(areas=["pools"]),
                                   timeout=30).pools if p.name == "nb"][0]
    blk = block(run_text, "pool nb")
    ok = (sorted(rep.lines) == sorted(["timeout udp 90", "utilisation-alarm 80",
                                       "max-sessions 1000"])
          and nb.utilisation_alarm == 80 and nb.max_sessions == 1000
          and dict(nb.timeouts) == {"udp": 90}
          and "utilisation-alarm 80" in blk and "max-sessions 1000" in blk
          and "timeout udp 90" in blk)
    result("set-pool-roundtrip", ok, f"lines {list(rep.lines)} vty {blk}")

    def undo(cid):
        return cfg.SetConfig(pb.SetConfigRequest(
            candidate_id=cid, pool=pb.PoolConfig(name="nb"),
            fields=["utilisation_alarm", "max_sessions", "timeouts"]), timeout=60)
    rep2 = commit_with(undo)
    result("set-pool-removes", sorted(rep2.lines) == sorted(
        ["no timeout udp", "no utilisation-alarm", "no max-sessions"]), f"{list(rep2.lines)}")


def check_logging_roundtrip():
    col = pb.LogCollectorConfig(transport="udp", configured=True,
                                collector="192.168.209.2", source="100.64.3.254",
                                port=4739)

    def setter(cid):
        return cfg.SetConfig(pb.SetConfigRequest(
            candidate_id=cid, logging=pb.LoggingConfig(enabled=True, collectors=[col])),
            timeout=60)
    rep = commit_with(setter)
    run_text = cfg.GetRunning(pb.GetRunningRequest(), timeout=30).text
    lg = cfg.GetConfig(pb.GetConfigRequest(areas=["logging"]), timeout=30).logging
    udp = [c for c in lg.collectors if c.transport == "udp"][0]
    want = "logging udp 192.168.209.2 src 100.64.3.254 port 4739"
    ok = (want in rep.lines and "logging enable" in rep.lines and lg.enabled
          and udp.configured and udp.collector == "192.168.209.2"
          and udp.source == "100.64.3.254" and udp.port == 4739
          and want in [x.strip() for x in run_text.splitlines()]
          and [c.transport for c in lg.collectors] == ["udp", "tcp", "syslog"])
    result("set-logging-roundtrip", ok, f"lines {list(rep.lines)}")
    off = pb.LogCollectorConfig(transport="udp", configured=False)

    def undo(cid):
        return cfg.SetConfig(pb.SetConfigRequest(
            candidate_id=cid, logging=pb.LoggingConfig(enabled=False, collectors=[off])),
            timeout=60)
    rep2 = commit_with(undo)
    lg = cfg.GetConfig(pb.GetConfigRequest(areas=["logging"]), timeout=30).logging
    result("set-logging-disables", "logging udp disable" in rep2.lines and not lg.enabled
           and not [c for c in lg.collectors if c.transport == "udp"][0].configured,
           f"{list(rep2.lines)}")


def check_candidate_and_delete():
    cid = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id
    cfg.SetConfig(pb.SetConfigRequest(candidate_id=cid, pool=pb.PoolConfig(
        name="p2", prefix_inside=["100.64.8.0/22"], prefix_outside=["200.150.0.32/30"],
        block_size=512, policy="interleaved", mode="deterministic")), timeout=60)
    before = [p.name for p in cfg.GetConfig(pb.GetConfigRequest(
        candidate_id=cid, areas=["pools"]), timeout=30).pools]
    running = [p.name for p in cfg.GetConfig(pb.GetConfigRequest(areas=["pools"]),
                                             timeout=30).pools]
    rep = cfg.DeleteConfig(pb.DeleteConfigRequest(candidate_id=cid, area="pools",
                                                  name="p2"), timeout=60)
    after = [p.name for p in cfg.GetConfig(pb.GetConfigRequest(
        candidate_id=cid, areas=["pools"]), timeout=30).pools]
    cfg.CloseCandidate(pb.CandidateRef(candidate_id=cid), timeout=30)
    result("candidate-read-and-delete", "p2" in before and "p2" not in running
           and "p2" not in after and list(rep.lines) == ["no pool p2"],
           f"candidate {before} running {running} after delete {after}")


def check_refusals():
    cid = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id
    try:
        try:
            iface(cid, mtu=50, fields=["mtu"])
            result("typed-edit-refused", False, "mtu 50 was taken")
        except grpc.RpcError as e:
            d = diagnostics(e)
            cand = cfg.GetConfig(pb.GetConfigRequest(candidate_id=cid,
                                                     areas=["interfaces"]), timeout=30)
            pg0 = [i for i in cand.interfaces if i.name == "pg0"][0]
            result("typed-edit-refused", e.code() == grpc.StatusCode.INVALID_ARGUMENT
                   and d.reason in (D.REASON_GRAMMAR, D.REASON_VALIDATION, D.REASON_LIMIT)
                   and pg0.mtu != 50,
                   f"{e.code().name} {D.Reason.Name(d.reason)} {e.details()[:80]}")
        try:
            iface(cid, mtu=1400, fields=["bogus"])
            result("typed-edit-usage", False, "an unknown field was taken")
        except grpc.RpcError as e:
            d = diagnostics(e)
            result("typed-edit-usage", e.code() == grpc.StatusCode.INVALID_ARGUMENT
                   and d.reason == D.REASON_USAGE and "bogus" in e.details(),
                   f"{e.code().name} {D.Reason.Name(d.reason)} {e.details()[:80]}")
    finally:
        cfg.CloseCandidate(pb.CandidateRef(candidate_id=cid), timeout=30)


def check_schema():
    import time
    t0 = time.monotonic()
    s = cfg.GetSchema(pb.GetSchemaRequest(), timeout=60)
    took = time.monotonic() - t0
    modes = {m.mode: m for m in s.modes}
    n = sum(len(m.statements) for m in s.modes)
    bs = [st for st in modes["pool"].statements if list(st.words) == ["block-size"]]
    arg = bs[0].forms[0].arguments[0] if bs else None
    udp = [st for st in modes["cgnat"].statements if list(st.words) == ["logging", "udp"]]
    srcs = {a.source for m in s.modes for st in m.statements for f in st.forms
            for a in f.arguments if a.source}
    ok = (len(modes) == 26 and "router bgp" in modes and "router isis" in modes
          and arg is not None and arg.kind == "integer" and (arg.min, arg.max) == (64, 32768)
          and list(modes["pool"].path) == ["nat", "cgnat", "pool <name>"]
          and modes["pool"].typed_area == "pools" and bs[0].typed
          and udp and len(udp[0].forms) == 2
          and {"interfaces", "neighbours", "pools"} <= srcs
          and s.release and len(s.digest) == 64 and not s.values
          and len(s.box_owned) == 4)
    result("schema", ok, f"{len(modes)} modes {n} statements in {took * 1000:.0f} ms, "
           f"release {s.release} digest {s.digest[:8]}")
    t0 = time.monotonic()
    v = cfg.GetSchema(pb.GetSchemaRequest(modes=["pool", "interface (port)"],
                                          with_values=True), timeout=120)
    took = time.monotonic() - t0
    vals = {k: list(x.values) for k, x in v.values.items()}
    result("schema-values", [m.mode for m in v.modes] == ["pool", "interface (port)"]
           and "nb" in vals.get("pools", []) and "pg0" in vals.get("interfaces", [])
           and v.digest != s.digest,
           f"in {took * 1000:.0f} ms: pools {vals.get('pools')} interfaces "
           f"{vals.get('interfaces')}")


def check_validate():
    before = cfg.GetRunning(pb.GetRunningRequest(), timeout=30).revision
    n0 = len(cfg.ListCandidates(pb.ListCandidatesRequest(), timeout=30).candidates)
    v = cfg.ValidateLines(pb.ValidateLinesRequest(
        path=["nat", "cgnat", "pool nb"],
        lines=["block-size 256", "block-size 7", "frobnicate 1"]), timeout=120)
    after = cfg.GetRunning(pb.GetRunningRequest(), timeout=30).revision
    n1 = len(cfg.ListCandidates(pb.ListCandidatesRequest(), timeout=30).candidates)
    ok = ([x.ok for x in v.verdicts] == [True, False, False]
          and v.verdicts[2].reason == D.REASON_GRAMMAR
          and v.verdicts[1].reason != 0 and v.verdicts[1].message
          and before == after and n0 == n1)
    result("validate-lines", ok, " | ".join(
        f"{x.line}: {'ok' if x.ok else D.Reason.Name(x.reason)} {x.message[:50]}"
        for x in v.verdicts))


def check_readonly():
    # An account with no role does not reach the dataplane (the lab's
    # unprivileged-reads): it reads the routing half and no pool, slowly. Its
    # typed read is held to its own GetRunning, not to root's.
    import time
    nv = rpc.ConfigServiceStub(channel("netview"))
    t0 = time.monotonic()
    t = nv.GetConfig(pb.GetConfigRequest(areas=["pools", "interfaces"]), timeout=300)
    took = time.monotonic() - t0
    text = nv.GetRunning(pb.GetRunningRequest(), timeout=300).text
    pools = sorted(p.name for p in t.pools)
    in_text = sorted(ln.strip().split()[1] for ln in text.splitlines()
                     if ln.strip().startswith("pool ") and len(ln.split()) == 2)
    result("readonly-reads-typed", t.source == "running" and pools == in_text
           and t.revision != "", f"pools {pools} text {in_text} in {took:.1f}s")


for name, fn in (("config-read-running", check_read),
                 ("set-interface-roundtrip", check_interface_roundtrip),
                 ("set-pool-roundtrip", check_pool_roundtrip),
                 ("set-logging-roundtrip", check_logging_roundtrip),
                 ("candidate-read-and-delete", check_candidate_and_delete),
                 ("typed-edit-refused", check_refusals),
                 ("schema", check_schema),
                 ("validate-lines", check_validate),
                 ("readonly-reads-typed", check_readonly)):
    run(name, fn)
