"""
lab/api-grpc.sh's client for BFD's third phase (#559): the `bfd` block as
fields. Every edit goes the whole road - SetConfig, Commit, GetConfig of the
running configuration, the VTY's own text (GetRunning) - and every object is
removed again by DeleteConfig, the whole block last, because removing BFD is
where 0.150.8 failed (#570): a removal that left the block's body behind was
refused by vtysh and the commit with it.

Every message here carries repeated fields (a session's `other`, a static
route's `extra`) across the pipe to the account's worker, where 0.150.2's
interface SetConfig failed ("can't pickle repeated message fields").

Arguments: <stubs dir> <certs dir> <host:port>. One RESULT line per check.
"""
import sys

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET = sys.argv[2], sys.argv[3]
PEER = {"address": "192.0.2.88"}


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
        result(name, False, f"{e.code().name} {e.details()[:200]}")
    except Exception as e:  # noqa: BLE001 - every check reports, none aborts the rest
        result(name, False, f"{type(e).__name__}: {e}")


def block(text, header):
    """The statements of one block of a configuration text, nested ones
    included, `exit` and `!` left out."""
    lines = text.splitlines()
    for k, ln in enumerate(lines):
        if ln.strip() == header:
            depth = len(ln) - len(ln.lstrip())
            out = []
            for nxt in lines[k + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= depth:
                    break
                if nxt.strip() and nxt.strip() not in ("exit", "!"):
                    out.append(nxt.strip())
            return out
    return []


root = channel("root")
cfg = rpc.ConfigServiceStub(root)


def running():
    return cfg.GetRunning(pb.GetRunningRequest(), timeout=60).text


def typed(areas=("bfd",)):
    return cfg.GetConfig(pb.GetConfigRequest(areas=list(areas)), timeout=60)


def commit_with(setter, comment="api bfd phase 3"):
    cid = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id
    try:
        replies = setter(cid)
        cfg.Commit(pb.CommitRequest(candidate_id=cid, comment=comment),
                   timeout=120)
    except Exception:
        cfg.CloseCandidate(pb.CandidateRef(candidate_id=cid), timeout=30)
        raise
    return replies


def peer_of(t):
    return [p for p in t.bfd_peers if p.address == PEER["address"]
            and not p.interface and not p.multihop]


def statics():
    return sorted((r.prefix, r.next_hop) for r in typed(["static"]).static_routes)


BEFORE = {}


def check_profile():
    BEFORE["static"] = statics()
    rep = commit_with(lambda cid: cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=cid, bfd_profile=pb.BfdProfileConfig(
            name="api9", timers=pb.BfdTimers(detect_multiplier=4,
                                             receive_interval_ms=150))),
        timeout=60))
    pr = [p for p in typed().bfd_profiles if p.name == "api9"]
    blk = block(running(), "profile api9")
    result("set-profile", list(rep.path) == ["bfd"] and pr
           and pr[0].timers.detect_multiplier == 4
           and pr[0].timers.receive_interval_ms == 150
           and "detect-multiplier 4" in blk and "receive-interval 150" in blk,
           f"lines {list(rep.lines)} vty {blk}")


def check_peer():
    rep = commit_with(lambda cid: cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=cid, bfd_peer=pb.BfdPeerConfig(
            profile="api9", timers=pb.BfdTimers(transmit_interval_ms=200),
            **PEER)), timeout=60))
    p = peer_of(typed())
    blk = block(running(), "peer 192.0.2.88")
    result("set-peer", p and p[0].profile == "api9"
           and p[0].timers.transmit_interval_ms == 200
           and p[0].header == "peer 192.0.2.88"
           and "profile api9" in blk and "transmit-interval 200" in blk,
           f"lines {list(rep.lines)} vty {blk}")


def check_peer_one_field():
    rep = commit_with(lambda cid: cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=cid, bfd_peer=pb.BfdPeerConfig(
            timers=pb.BfdTimers(transmit_interval_ms=400), **PEER),
        fields=["timers.transmit_interval_ms"]), timeout=60))
    p = peer_of(typed())
    blk = block(running(), "peer 192.0.2.88")
    result("set-peer-one-field",
           list(rep.lines) == ["peer 192.0.2.88",
                               "no transmit-interval 200",
                               "transmit-interval 400", "exit"]
           and p and p[0].timers.transmit_interval_ms == 400
           and p[0].profile == "api9" and "transmit-interval 200" not in blk,
           f"lines {list(rep.lines)} vty {blk}")


def check_neighbor_profile():
    rep = commit_with(lambda cid: cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=cid, bgp_neighbor=pb.BgpNeighborEdit(
            asn="65000", neighbor=pb.BgpNeighborConfig(
                address="192.0.2.11", remote_as="65011", bfd=True,
                bfd_profile="api9"))), timeout=60))
    b = [x for x in typed(["bgp"]).bgp if x.asn == "65000" and not x.vrf]
    n = [x for x in (b[0].neighbors if b else []) if x.address == "192.0.2.11"]
    blk = block(running(), "router bgp 65000")
    result("neighbor-bfd-profile", n and n[0].bfd and n[0].bfd_profile == "api9"
           and "neighbor 192.0.2.11 bfd profile api9" in blk,
           f"lines {list(rep.lines)} vty {[x for x in blk if '192.0.2.11' in x]}")
    rep2 = commit_with(lambda cid: cfg.DeleteConfig(pb.DeleteConfigRequest(
        candidate_id=cid, area="bgp", name="192.0.2.11", asn="65000"),
        timeout=60))
    blk = block(running(), "router bgp 65000")
    result("neighbor-with-bfd-deleted",
           not any("192.0.2.11" in x for x in blk),
           f"lines {list(rep2.lines)} left {[x for x in blk if '192.0.2.11' in x]}")


def check_static():
    rep = commit_with(lambda cid: cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=cid, static_route=pb.StaticRouteConfig(
            prefix="10.88.0.0/16", next_hop="100.64.3.1", bfd=True,
            bfd_profile="api9")), timeout=60))
    r = [x for x in typed(["static"]).static_routes if x.prefix == "10.88.0.0/16"]
    text = running()
    result("static-bfd", r and r[0].bfd and r[0].bfd_profile == "api9"
           and not r[0].extra
           and any("10.88.0.0/16" in x and "bfd profile api9" in x
                   for x in text.splitlines()),
           f"lines {list(rep.lines)} read {r[0] if r else None}")
    commit_with(lambda cid: cfg.DeleteConfig(pb.DeleteConfigRequest(
        candidate_id=cid, area="static", prefix="10.88.0.0/16",
        next_hop="100.64.3.1"), timeout=60))
    result("static-bfd-deleted", not any("10.88.0.0/16" in x
                                         for x in running().splitlines()))


def check_delete_peer():
    rep = commit_with(lambda cid: cfg.DeleteConfig(pb.DeleteConfigRequest(
        candidate_id=cid, area="bfd",
        bfd_peer=pb.BfdPeerConfig(**PEER)), timeout=60))
    result("delete-peer", list(rep.lines) == ["no peer 192.0.2.88"]
           and not peer_of(typed())
           and not block(running(), "peer 192.0.2.88"),
           f"lines {list(rep.lines)}")


def check_delete_profile():
    rep = commit_with(lambda cid: cfg.DeleteConfig(pb.DeleteConfigRequest(
        candidate_id=cid, area="bfd", name="api9"), timeout=60))
    result("delete-profile", list(rep.lines) == ["no profile api9"]
           and not [p for p in typed().bfd_profiles if p.name == "api9"],
           f"lines {list(rep.lines)}")


def check_delete_block():
    # A session and a profile again, then the whole block in one call: the
    # removal 0.150.8 could not commit (#570).
    def both(cid):
        cfg.SetConfig(pb.SetConfigRequest(candidate_id=cid,
                      bfd_profile=pb.BfdProfileConfig(name="api9b")), timeout=60)
        return cfg.SetConfig(pb.SetConfigRequest(
            candidate_id=cid, bfd_peer=pb.BfdPeerConfig(profile="api9b", **PEER)),
            timeout=60)
    commit_with(both)
    rep = commit_with(lambda cid: cfg.DeleteConfig(pb.DeleteConfigRequest(
        candidate_id=cid, area="bfd"), timeout=60))
    t = typed()
    text = running()
    result("delete-block", list(rep.lines) == ["no bfd"]
           and not peer_of(t)
           and not [p for p in t.bfd_profiles if p.name == "api9b"]
           and "peer 192.0.2.88" not in text
           and statics() == BEFORE.get("static"),
           f"lines {list(rep.lines)} statics {statics()} before "
           f"{BEFORE.get('static')}")


def check_refused():
    cid = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id
    try:
        cfg.DeleteConfig(pb.DeleteConfigRequest(
            candidate_id=cid, area="bfd",
            bfd_peer=pb.BfdPeerConfig(address="192.0.2.99")), timeout=60)
        result("delete-unknown-refused", False, "a session nobody has was removed")
    except grpc.RpcError as e:
        result("delete-unknown-refused",
               e.code() == grpc.StatusCode.INVALID_ARGUMENT
               and "no session" in e.details(), e.details()[:120])
    finally:
        cfg.CloseCandidate(pb.CandidateRef(candidate_id=cid), timeout=30)


def check_schema():
    s = cfg.GetSchema(pb.GetSchemaRequest(modes=["bfd peer"]), timeout=60)
    m = [x for x in s.modes if x.mode == "bfd peer"]
    st = {" ".join(x.words): x for x in (m[0].statements if m else [])}
    result("schema-bfd", m and "detect-multiplier" in st
           and st["detect-multiplier"].typed and "profile" in st,
           f"{sorted(st)[:8]}")


for name, fn in (("set-profile", check_profile), ("set-peer", check_peer),
                 ("set-peer-one-field", check_peer_one_field),
                 ("neighbor-bfd-profile", check_neighbor_profile),
                 ("static-bfd", check_static),
                 ("delete-peer", check_delete_peer),
                 ("delete-profile", check_delete_profile),
                 ("delete-block", check_delete_block),
                 ("delete-unknown-refused", check_refused),
                 ("schema-bfd", check_schema)):
    run(name, fn)
