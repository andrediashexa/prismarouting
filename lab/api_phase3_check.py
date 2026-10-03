#!/usr/bin/env python3
"""
lab/api-grpc.sh's client for #558's third phase: the routing configuration
as fields. The read is held to the VTY's own text (GetRunning, which is
`show running-config`) block by block - static routes, BGP, OSPF, OSPF6,
IS-IS and an interface's IGP settings - and the two typed edits (a static
route, a BGP neighbour) go the whole road: SetConfig, Commit, GetConfig of
the running configuration, the VTY's text, and removed again.

Arguments: <stubs dir> <certs dir> <host:port>. One RESULT line per check.
"""
import sys

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET = sys.argv[2], sys.argv[3]
D = pb.Diagnostics
ROUTING = ["static", "bgp", "ospf", "ospf6", "isis", "interface_routing"]


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
    """The statements of one block of a configuration text, nested ones
    included: the lines after its header while they are indented deeper."""
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


def run(name, fn):
    try:
        fn()
    except grpc.RpcError as e:
        result(name, False, f"{e.code().name} {e.details()[:160]}")
    except Exception as e:  # noqa: BLE001 - every check reports, none aborts the rest
        result(name, False, f"{type(e).__name__}: {e}")


root = channel("root")
cfg = rpc.ConfigServiceStub(root)


def running():
    return cfg.GetRunning(pb.GetRunningRequest(), timeout=30).text


def typed(areas=ROUTING, cid=""):
    return cfg.GetConfig(pb.GetConfigRequest(areas=areas, candidate_id=cid),
                         timeout=30)


def commit_with(setter, comment="api phase 3"):
    cid = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id
    replies = setter(cid)
    cfg.Commit(pb.CommitRequest(candidate_id=cid, comment=comment), timeout=120)
    return replies


def check_read():
    text = running()
    t = typed()
    static_blk = block(text, "router static")
    bgp = {(b.asn, b.vrf): b for b in t.bgp}
    b = bgp.get(("65000", ""))
    nb = {n.address: n for n in b.neighbors} if b else {}
    o = t.ospf[0] if t.ospf else None
    i = t.isis[0] if t.isis else None
    checks = {
        "static lines are the router static block":
            sorted(r.line for r in t.static_routes if not r.vrf) == sorted(static_blk),
        "static 10.66.0.0/16 distance/tag":
            any(r.prefix == "10.66.0.0/16" and r.distance == 150 and r.tag == 7
                for r in t.static_routes),
        "bgp 65000 router-id": bool(b) and b.router_id == "10.255.0.1"
            and "bgp router-id 10.255.0.1" in block(text, "router bgp 65000"),
        "bgp neighbour remote-as/description as the text says":
            "192.0.2.9" in nb and nb["192.0.2.9"].remote_as == "65009"
            and f"neighbor 192.0.2.9 description {nb['192.0.2.9'].description}"
            in block(text, "router bgp 65000"),
        "bgp family: the policy's compiled map on the neighbour, network":
            bool(b) and any(f.afi == "ipv4" and "198.18.0.0/24" in f.networks
                            and any(x.neighbor == "192.0.2.9"
                                    and x.route_map_in.startswith("PO-")
                                    for x in f.neighbors) for f in b.families),
        "ospf router-id, network, stub area":
            bool(o) and o.router_id == "10.255.0.1"
            and any(n.prefix == "100.64.0.0/22" and n.area == "0" for n in o.networks)
            and any(a.area == "1" and a.area_type == "stub" for a in o.areas)
            and "network 100.64.0.0/22 area 0" in block(text, "router ospf"),
        "ospf6 router-id": bool(t.ospf6) and t.ospf6[0].router_id == "10.255.0.1",
        "isis net, is-type": bool(i) and list(i.nets) == ["49.0001.0000.0000.0001.00"]
            and i.is_type == "level-2-only",
        "bgp default ipv4-unicast on": bool(b) and b.default_ipv4_unicast,
    }
    bad = [k for k, v in checks.items() if not v]
    result("routing-read", not bad, f"failed: {bad}" if bad else
           f"static {len(t.static_routes)} bgp {len(t.bgp)} ospf {len(t.ospf)} "
           f"isis {len(t.isis)}")
    # Everything a block holds is somewhere: a field or `other`, verbatim.
    # Since 0.149.24 (#558 phase 4) a known knob is read as a setting, the
    # statement's keywords its key; what no key takes stays in `other`.
    result("routing-read-keeps-rest", bool(b) and (
        "bgp log-neighbor-changes" in list(b.other)
        or any(x.key == "bgp log-neighbor-changes" for x in b.settings)),
        f"bgp other {list(b.other) if b else None} settings "
        f"{[x.key for x in b.settings] if b else None}")


def check_interface_routing():
    """pg0's IGP settings, staged with Edit and read off the candidate: the
    reader on a real VTY's candidate, flat as typed."""
    cid = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id
    try:
        cfg.Edit(pb.EditRequest(candidate_id=cid, path=["interface pg0"],
                                lines=["ip ospf area 0", "ip ospf cost 15",
                                       "ip ospf network point-to-point"]),
                 timeout=60)
        t = typed(["interface_routing"], cid)
        ir = {x.name: x for x in t.interface_routing}
        p = ir.get("pg0")
        result("interface-routing-read", bool(p) and p.ospf_area == "0"
               and p.ospf_cost == 15 and p.ospf_network == "point-to-point",
               f"pg0 {(p.ospf_area, p.ospf_cost, p.ospf_network) if p else None}")
    finally:
        cfg.CloseCandidate(pb.CandidateRef(candidate_id=cid), timeout=30)


def static(cid, **kw):
    fields = kw.pop("fields", [])
    return cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=cid, static_route=pb.StaticRouteConfig(**kw), fields=fields),
        timeout=60)


def check_static():
    rep = commit_with(lambda cid: static(cid, prefix="10.77.0.0/16",
                                         next_hop="100.64.3.1", distance=200,
                                         description="api route"))
    t = typed(["static"])
    r = [x for x in t.static_routes if x.prefix == "10.77.0.0/16"]
    blk = block(running(), "router static")
    want = "10.77.0.0/16 next-hop 100.64.3.1 distance 200 description api route"
    result("set-static-roundtrip", list(rep.path) == ["router static"]
           and list(rep.lines) == [want] and r and r[0].distance == 200
           and r[0].description == "api route" and want in blk,
           f"lines {list(rep.lines)} vty {blk}")
    rep2 = commit_with(lambda cid: static(cid, prefix="10.77.0.0/16",
                                          next_hop="100.64.3.1", tag=9,
                                          fields=["tag"]))
    r = [x for x in typed(["static"]).static_routes if x.prefix == "10.77.0.0/16"]
    result("set-static-one-field", list(rep2.lines) == [
        "10.77.0.0/16 next-hop 100.64.3.1 distance 200 tag 9 description api route"]
        and r and (r[0].distance, r[0].tag, r[0].description) == (200, 9, "api route"),
        f"{list(rep2.lines)} -> {[(x.distance, x.tag, x.description) for x in r]}")

    def delete(cid):
        return cfg.DeleteConfig(pb.DeleteConfigRequest(
            candidate_id=cid, area="static", prefix="10.77.0.0/16",
            next_hop="100.64.3.1"), timeout=60)
    rep3 = commit_with(delete)
    gone = not [x for x in typed(["static"]).static_routes if x.prefix == "10.77.0.0/16"]
    result("delete-static", list(rep3.lines) == ["no 10.77.0.0/16 next-hop 100.64.3.1"]
           and gone and not any(x.startswith("10.77.0.0/16") for x in
                                block(running(), "router static")),
           f"{list(rep3.lines)} gone {gone}")


def check_static_vrf_refused():
    cid = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id
    try:
        static(cid, prefix="10.9.0.0/16", next_hop="100.64.3.1", vrf="red")
        result("static-vrf-refused", False, "a VRF's route was set here")
    except grpc.RpcError as e:
        d = diagnostics(e)
        result("static-vrf-refused", e.code() == grpc.StatusCode.INVALID_ARGUMENT
               and d.reason == D.REASON_USAGE, f"{e.code().name} {d.reason}")
    finally:
        cfg.CloseCandidate(pb.CandidateRef(candidate_id=cid), timeout=30)


def neighbor(cid, fields=(), password="", **kw):
    fams = kw.pop("families", [])
    return cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=cid, bgp_neighbor=pb.BgpNeighborEdit(
            asn="65000", neighbor=pb.BgpNeighborConfig(**kw), password=password,
            families=[pb.BgpNeighborFamilyEdit(
                afi="ipv4", safi="unicast",
                settings=pb.BgpNeighborFamilyConfig(**f)) for f in fams]),
        fields=list(fields)), timeout=60)


def check_neighbor():
    rep = commit_with(lambda cid: neighbor(
        cid, address="192.0.2.10", remote_as="65010", description="api-peer",
        keepalive=10, holdtime=30, password="Lab558secret",
        families=[{"activate": True, "maximum_prefix": 100,
                   "soft_reconfiguration_inbound": True}]))
    t = typed(["bgp"])
    b = [x for x in t.bgp if x.asn == "65000" and not x.vrf][0]
    n = [x for x in b.neighbors if x.address == "192.0.2.10"]
    fam = [f for f in b.families if f.afi == "ipv4" and f.safi == "unicast"]
    fn = [x for x in fam[0].neighbors if x.neighbor == "192.0.2.10"] if fam else []
    blk = block(running(), "router bgp 65000")
    result("set-neighbor-roundtrip", list(rep.path) == ["router bgp 65000"]
           and n and n[0].remote_as == "65010" and n[0].description == "api-peer"
           and (n[0].keepalive, n[0].holdtime) == (10, 30) and n[0].password_set
           and b.default_ipv4_unicast and fn and fn[0].maximum_prefix == 100
           and fn[0].soft_reconfiguration_inbound
           and "neighbor 192.0.2.10 remote-as 65010" in blk
           and "neighbor 192.0.2.10 maximum-prefix 100" in blk,
           f"lines {list(rep.lines)} vty {[x for x in blk if '192.0.2.10' in x]}")
    result("neighbor-password-never-read", "Lab558secret" not in str(t),
           "the password is set, its value nowhere in GetConfig")
    rep2 = commit_with(lambda cid: neighbor(cid, address="192.0.2.10",
                                            description="api-peer-2",
                                            fields=["description"]))
    n = [x for x in typed(["bgp"]).bgp[0].neighbors if x.address == "192.0.2.10"]
    result("set-neighbor-one-field", list(rep2.lines) ==
           ["neighbor 192.0.2.10 description api-peer-2"]
           and n and n[0].description == "api-peer-2" and n[0].password_set,
           f"{list(rep2.lines)}: a password not given is kept")

    def delete(cid):
        return cfg.DeleteConfig(pb.DeleteConfigRequest(
            candidate_id=cid, area="bgp", name="192.0.2.10", asn="65000"),
            timeout=60)
    rep3 = commit_with(delete)
    gone = not [x for x in typed(["bgp"]).bgp[0].neighbors
                if x.address == "192.0.2.10"]
    lines = list(rep3.lines)
    # Removed statement by statement, the family's first and remote-as last
    # (a bare `no neighbor X` is not what the VTY removes a neighbour by),
    # and the reply masks the password it had to name to remove it.
    result("delete-neighbor", lines and lines[0] == "address-family ipv4 unicast"
           and lines[-1] == "no neighbor 192.0.2.10 remote-as 65010"
           and "no neighbor 192.0.2.10 password <set, not shown>" in lines
           and gone and not any("192.0.2.10" in x for x in
                                block(running(), "router bgp 65000")),
           f"{lines} gone {gone}")
    result("delete-neighbor-masks-password",
           not any("Lab558secret" in x for x in lines + [rep3.output]),
           "the DeleteConfig reply names the password masked")


def check_candidate():
    cid = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id
    try:
        static(cid, prefix="10.88.0.0/16", next_hop="100.64.3.1")
        in_cand = [x.prefix for x in typed(["static"], cid).static_routes]
        in_run = [x.prefix for x in typed(["static"]).static_routes]
        result("routing-candidate-read", "10.88.0.0/16" in in_cand
               and "10.88.0.0/16" not in in_run,
               f"candidate {in_cand} running {in_run}")
    finally:
        cfg.CloseCandidate(pb.CandidateRef(candidate_id=cid), timeout=30)


for name, fn in (("routing-read", check_read),
                 ("interface-routing", check_interface_routing),
                 ("static", check_static),
                 ("static-vrf", check_static_vrf_refused),
                 ("neighbor", check_neighbor), ("candidate", check_candidate)):
    run(name, fn)
