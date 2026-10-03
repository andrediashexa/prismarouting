#!/usr/bin/env python3
"""
lab/api-grpc.sh's client for #558's fourth phase: the rest of the
configuration as fields - system, the accounts, SNMP's users, the policy,
the lists and route-maps written for FRR, RPKI, the protocol attachments,
the VRF devices, the CGNAT globals and the routing blocks' knobs.

What the box already runs is held to the VTY's own text (GetRunning, which
is `show running-config`). What it does not run is staged in a candidate -
with Edit as lines, or with SetConfig as fields - read back typed off that
candidate, and held to the candidate's own text (ShowCandidate, `show
this`), then thrown away: the candidate is never committed, so nothing here
depends on the doors a commit of a resolver or an account would use on a
box, which this lab's container does not run.

The secrets staged are distinctive strings, and the typed answer is
searched for them: a client is told a secret is there, never what it is.

Arguments: <stubs dir> <certs dir> <host:port>. One RESULT line per check.
"""
import sys

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET = sys.argv[2], sys.argv[3]
PHASE4 = ["system", "users", "snmp_users", "policy", "route_maps",
          "frr_lists", "rpki", "routing_attachments", "vrfs", "vrrp",
          "bundles", "cgnat_globals"]


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


root = channel("root")
cfg = rpc.ConfigServiceStub(root)


def lines_of(text):
    return [ln.strip() for ln in text.splitlines()]


def check_running_read():
    text = cfg.GetRunning(pb.GetRunningRequest(), timeout=30).text
    t = cfg.GetConfig(pb.GetConfigRequest(areas=PHASE4), timeout=30)
    tl = lines_of(text)
    up = [s for s in t.policy.statements if s.name == "UP"]
    checks = {
        "policy UP and its term, as policy-options says":
            bool(up) and [x.name for x in up[0].terms] == ["cust"]
            and any(m.kind == "prefix-list" and list(m.values) == ["C"]
                    for m in getattr(up[0].terms[0], "from"))
            and up[0].terms[0].then == "accept",
        "policy prefix-list C, its member":
            any(p.name == "C" and list(p.members) == ["203.0.113.0/24"]
                for p in t.policy.prefix_lists),
        "vrf red table 10, as the text says":
            any(v.name == "red" and v.table == 10 for v in t.vrfs)
            and "vrf red table 10" in tl,
        "the accounts the text has, by name":
            sorted(u.name for u in t.users)
            == sorted(ln.split()[1] for ln in tl if ln.startswith("user ")),
        "api enabled, as the text says":
            t.system.api.enabled == ("api enabled yes" in tl),
    }
    bad = [k for k, v in checks.items() if not v]
    result("phase4-running-read", not bad, f"failed: {bad}" if bad else
           f"users {len(t.users)} vrfs {len(t.vrfs)} statements "
           f"{len(t.policy.statements)}")


def staged(lines_by_path, setters=()):
    """A candidate with `lines_by_path` [(path, [lines])] staged and each
    SetConfigRequest of `setters` applied: (candidate id, typed, text)."""
    cid = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id
    for path, lines in lines_by_path:
        cfg.Edit(pb.EditRequest(candidate_id=cid, path=path, lines=lines),
                 timeout=60)
    replies = [cfg.SetConfig(req_for(cid), timeout=60) for req_for in setters]
    t = cfg.GetConfig(pb.GetConfigRequest(candidate_id=cid), timeout=30)
    text = cfg.ShowCandidate(pb.CandidateRef(candidate_id=cid), timeout=30).text
    return cid, t, text, replies


def close(cid):
    try:
        cfg.CloseCandidate(pb.CandidateRef(candidate_id=cid), timeout=30)
    except grpc.RpcError:
        pass


def check_staged_read():
    cid, t, text, _ = staged([
        ([], ["ip prefix-list LAB4 seq 5 permit 10.44.0.0/16 le 24",
              "bgp community-list standard LAB4C permit 65000:44",
              "ip protocol bgp route-map LAB4M",
              "snmp community SECRETLAB4COMM"]),
        (["route-map LAB4M permit 10"], ["match ip address prefix-list LAB4",
                                         "set local-preference 144"]),
        (["snmp-user lab4"], ["auth sha SECRETLAB4AUTH",
                              "priv aes SECRETLAB4PRIV"]),
        (["router ospf"], ["auto-cost reference-bandwidth 100000"]),
        (["nat", "cgnat"], ["timeout udp 144", "block-holddown 44"]),
    ])
    try:
        tl = lines_of(text)
        blob = str(t)
        rm = [m for m in t.route_maps if m.name == "LAB4M"]
        pl = [p for p in t.frr_lists.prefix_lists if p.name == "LAB4"]
        su = [u for u in t.snmp_users if u.name == "lab4"]
        o = t.ospf[0] if t.ospf else None
        checks = {
            "route-map LAB4M: its match and set, as the candidate's text":
                bool(rm) and list(rm[0].entries[0].matches)
                == ["ip address prefix-list LAB4"]
                and list(rm[0].entries[0].sets) == ["local-preference 144"]
                and "set local-preference 144" in tl,
            "FRR prefix-list LAB4 entry, le 24":
                bool(pl) and pl[0].entries[0].prefix == "10.44.0.0/16"
                and pl[0].entries[0].le == 24 and pl[0].entries[0].sequence == 5,
            "community list LAB4C":
                any(b.name == "LAB4C" and b.kind == "community"
                    for b in t.frr_lists.bgp_lists),
            "attachment ip protocol bgp route-map LAB4M":
                any(a.kind == "protocol-route-map" and a.name == "LAB4M"
                    for a in t.routing_attachments.attachments),
            "snmp-user lab4: its protocols":
                bool(su) and su[0].auth_protocol == "sha"
                and su[0].priv_protocol == "aes",
            "the community counted": t.system.snmp.communities >= 1,
            "OSPF knob as a setting":
                bool(o) and any(x.key == "auto-cost reference-bandwidth"
                                and x.value == "100000" for x in o.settings),
            "CGNAT globals: udp timeout and hold-down":
                any(x.key == "udp" and x.value == "144"
                    for x in t.cgnat_globals.timeouts)
                and t.cgnat_globals.block_holddown == 44,
        }
        bad = [k for k, v in checks.items() if not v]
        result("phase4-candidate-read", not bad, f"failed: {bad}" if bad else
               "route-map, lists, attachment, snmp user, setting, globals")
        leaked = [s for s in ("SECRETLAB4COMM", "SECRETLAB4AUTH", "SECRETLAB4PRIV")
                  if s in blob]
        result("phase4-no-secret", not leaked, f"leaked: {leaked}" if leaked else
               "no community or passphrase in the typed answer")
    finally:
        close(cid)


def check_typed_edits():
    def sysreq(cid):
        return pb.SetConfigRequest(
            candidate_id=cid, fields=["hostname", "ssh.port"],
            system=pb.SystemConfig(hostname="api-lab4",
                                   ssh=pb.SshConfig(port=2244)))

    def vrfreq(cid):
        return pb.SetConfigRequest(candidate_id=cid,
                                   vrf_device=pb.VrfDeviceConfig(name="lab4v",
                                                                 table=44))
    cid, t, text, replies = staged([], [sysreq, vrfreq])
    try:
        tl = lines_of(text)
        checks = {
            "SetConfig system staged hostname and ssh port":
                list(replies[0].lines) == ["hostname api-lab4", "ssh port 2244"],
            "and the candidate reads them back typed":
                t.system.hostname == "api-lab4" and t.system.ssh.port == 2244,
            "and the candidate's text has them":
                "hostname api-lab4" in tl and "ssh port 2244" in tl,
            "SetConfig vrf_device staged it, read back and in the text":
                list(replies[1].lines) == ["vrf lab4v table 44"]
                and any(v.name == "lab4v" and v.table == 44 for v in t.vrfs)
                and "vrf lab4v table 44" in tl,
        }
        d = cfg.DeleteConfig(pb.DeleteConfigRequest(candidate_id=cid, area="vrfs",
                                                    name="lab4v"), timeout=60)
        t2 = cfg.GetConfig(pb.GetConfigRequest(candidate_id=cid, areas=["vrfs"]),
                           timeout=30)
        checks["DeleteConfig vrfs removes it from the candidate"] = (
            list(d.lines) == ["no vrf lab4v table"]
            and not any(v.name == "lab4v" for v in t2.vrfs))
        bad = [k for k, v in checks.items() if not v]
        result("phase4-typed-edits", not bad, f"failed: {bad}" if bad else
               "system and vrf_device staged, read back, removed")
    finally:
        close(cid)


def check_refusal():
    cid = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id
    try:
        cfg.SetConfig(pb.SetConfigRequest(candidate_id=cid, fields=["snmp.community"],
                                          system=pb.SystemConfig()), timeout=60)
        result("phase4-secret-field-refused", False, "a community was accepted")
    except grpc.RpcError as e:
        result("phase4-secret-field-refused",
               e.code() == grpc.StatusCode.INVALID_ARGUMENT,
               f"{e.code().name}: {e.details()[:80]}")
    finally:
        close(cid)


def check_schema():
    s = cfg.GetSchema(pb.GetSchemaRequest(), timeout=30)
    by = {m.mode: m for m in s.modes}
    bgp = by.get("router bgp")
    cfgm = by.get("config")
    host = [x for x in cfgm.statements if list(x.words)[:1] == ["hostname"]] if cfgm else []
    comm = [x for x in cfgm.statements if list(x.words)[:2] == ["snmp", "community"]] \
        if cfgm else []
    ok = (bgp is not None and bgp.typed_area == "bgp"
          and all(x.read_typed for x in bgp.statements)
          and host and host[0].typed and comm and comm[0].read_typed
          and not comm[0].typed)
    result("phase4-schema", ok, f"router bgp area {bgp.typed_area if bgp else None}; "
           f"hostname typed {host[0].typed if host else None}; community "
           f"typed {comm[0].typed if comm else None}")


def check_schema_covers_running():
    """Every line of the lab box's running configuration matches a GetSchema
    statement, order-free parts included: the schema as a client gets it,
    matched by the product's own matcher (typedcfg.unmatched_lines)."""
    sys.path.insert(0, "/src/src/prismash")
    import typedcfg
    s = cfg.GetSchema(pb.GetSchemaRequest(), timeout=30)
    sch = [{"mode": m.mode, "path": list(m.path), "statements": [
        {"words": list(st.words), "removable": st.removable, "forms": [
            [{"name": a.name, "kind": a.kind, "min": a.min, "max": a.max,
              "choices": list(a.choices), "optional": a.optional,
              "position": a.position, "group": a.group,
              "unordered": a.unordered, "many": a.many}
             for a in f.arguments] for f in st.forms]}
        for st in m.statements]} for m in s.modes]
    text = cfg.GetRunning(pb.GetRunningRequest(), timeout=30).text
    misses = typedcfg.unmatched_lines(sch, text)
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("!")]
    result("phase4-schema-covers-running", not misses,
           f"unmatched {misses[:4]}" if misses else
           f"all {len(lines)} lines of GetRunning match a GetSchema statement")
    words = [w for m in s.modes for st in m.statements for w in st.words
             if "<" in w or ">" in w or "|" in w]
    choices = [c for m in s.modes for st in m.statements for f in st.forms
               for a in f.arguments for c in a.choices if "<" in c or ">" in c]
    result("phase4-schema-literal-words", not words and not choices,
           f"words {words[:3]} choices {choices[:3]}")


run("phase4-schema-covers-running", check_schema_covers_running)
run("phase4-running-read", check_running_read)
run("phase4-candidate-read", check_staged_read)
run("phase4-typed-edits", check_typed_edits)
run("phase4-secret-field-refused", check_refusal)
run("phase4-schema", check_schema)
