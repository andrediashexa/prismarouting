#!/usr/bin/env python3
"""
lab/api-grpc.sh's client (#532): drives prisma.v1 over mutual TLS the way the
web interface's backend will, and prints one RESULT line per check for the
lab to assert. Arguments: <stubs dir> <certs dir> <host:port>.
"""
import sys
import threading
import time

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET = sys.argv[2], sys.argv[3]


def channel(who):
    ca = open(f"{CERTS}/ca.crt", "rb").read()
    key = open(f"{CERTS}/{who}.key", "rb").read()
    crt = open(f"{CERTS}/{who}.crt", "rb").read()
    creds = grpc.ssl_channel_credentials(ca, key, crt)
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
    return None


root = channel("root")
cfg = rpc.ConfigServiceStub(root)
ops = rpc.OperationalServiceStub(root)
tel = rpc.TelemetryServiceStub(root)
POOL = ["nat", "cgnat", "pool API"]

# open, edit, and the VTY's own refusal
c = cfg.OpenCandidate(pb.OpenCandidateRequest())
result("open", bool(c.candidate_id and c.base_revision), c.base_revision)
cfg.Edit(pb.EditRequest(candidate_id=c.candidate_id, path=POOL,
                        lines=["mode dynamic", "block-size 256"]))
chk = cfg.Check(pb.CandidateRef(candidate_id=c.candidate_id))
said = " | ".join(d.message for d in chk.diagnostics)
result("check-refuses", not chk.passed and "outside" in said, said[:160])
try:
    cfg.Commit(pb.CommitRequest(candidate_id=c.candidate_id))
    result("commit-refused", False, "a refused candidate was committed")
except grpc.RpcError as e:
    d = diagnostics(e)
    result("commit-refused", e.code() == grpc.StatusCode.FAILED_PRECONDITION
           and d is not None and any("outside" in i.message for i in d.items),
           f"{e.code().name}: {e.details()[:100]}")
try:
    cfg.Edit(pb.EditRequest(candidate_id=c.candidate_id, path=POOL,
                            lines=["frobnicate 1"]))
    result("edit-refused", False, "an invalid line was staged")
except grpc.RpcError as e:
    result("edit-refused", e.code() == grpc.StatusCode.INVALID_ARGUMENT,
           f"{e.code().name}: {e.details()[:120]}")

# complete it, commit confirmed, confirm
cfg.Edit(pb.EditRequest(candidate_id=c.candidate_id, path=POOL,
                        lines=["inside 100.64.8.0/22", "outside 200.150.9.0/30"]))
cmp_ = cfg.Compare(pb.CandidateRef(candidate_id=c.candidate_id))
result("compare", cmp_.changed and "pool API" in cmp_.diff, cmp_.diff.replace("\n", " / ")[:160])
chk = cfg.Check(pb.CandidateRef(candidate_id=c.candidate_id))
result("check-passes", chk.passed, chk.output.replace("\n", " / ")[:160])
r = cfg.Commit(pb.CommitRequest(candidate_id=c.candidate_id, comment="via the api",
                                confirmed_minutes=5))
result("commit-confirmed", bool(r.revision) and r.confirm_deadline.seconds > time.time(),
       f"deadline in {r.confirm_deadline.seconds - int(time.time())}s")
r = cfg.Confirm(pb.ConfirmRequest())
result("confirm", "confirmed" in r.output, r.output.replace("\n", " / ")[:120])
running = cfg.GetRunning(pb.GetRunningRequest())
result("running-has-pool", "pool API" in running.text, running.revision)
import subprocess  # noqa: E402
prompt = subprocess.run(["python3", "/src/src/prismash/prismash.py", "-c",
                         "show running-config"], capture_output=True, text=True).stdout
result("prompt-has-pool", "pool API" in prompt and "max-blocks" not in prompt,
       "the prompt's show running-config, right after the API's commit")
revs = cfg.ListRevisions(pb.ListRevisionsRequest())
result("revisions", len(revs.revisions) >= 2,
       f"{len(revs.revisions)} revisions, newest '{revs.revisions[0].comment}'")
cfg.CloseCandidate(pb.CandidateRef(candidate_id=c.candidate_id))

# a base that moved under a candidate
a = cfg.OpenCandidate(pb.OpenCandidateRequest())
b = cfg.OpenCandidate(pb.OpenCandidateRequest())
cfg.Edit(pb.EditRequest(candidate_id=a.candidate_id, path=POOL, lines=["max-blocks 4"]))
cfg.Commit(pb.CommitRequest(candidate_id=a.candidate_id))
cfg.Edit(pb.EditRequest(candidate_id=b.candidate_id, path=POOL, lines=["max-blocks 2"]))
try:
    cfg.Commit(pb.CommitRequest(candidate_id=b.candidate_id))
    result("moved-base", False, "a candidate over a moved base was committed")
except grpc.RpcError as e:
    d = diagnostics(e)
    result("moved-base", e.code() == grpc.StatusCode.FAILED_PRECONDITION
           and d is not None and "max-blocks 4" in d.output,
           f"{e.code().name}: {e.details()[:100]}")
for x in (a, b):
    cfg.CloseCandidate(pb.CandidateRef(candidate_id=x.candidate_id))

# rollback: the pool goes, by loading the revision before it
rb = cfg.OpenCandidate(pb.OpenCandidateRequest())
revs = cfg.ListRevisions(pb.ListRevisionsRequest())
before = next((x.number for x in revs.revisions if "via the api" in x.comment), None)
cfg.Rollback(pb.RollbackRequest(candidate_id=rb.candidate_id, revision=(before or 0) + 1))
cfg.Commit(pb.CommitRequest(candidate_id=rb.candidate_id, comment="rolled back via the api",
                            acknowledge_remap=True))
running = cfg.GetRunning(pb.GetRunningRequest())
result("rollback", "pool API" not in running.text, f"revision {(before or 0) + 1} loaded")

# operational, typed
v = ops.GetVersion(pb.GetVersionRequest())
result("version", bool(v.release) and v.api_transport in ("vpp_papi", "vat2"),
       f"{v.release} {v.plugin_build} {v.api_transport}")
# The host's name, as the prompt reads it (#533's follow-up): empty on the
# X520 bench in 0.148.4, where it was asked through a running-config filter.
import socket as _socket
_host = _socket.gethostname()
sy = ops.GetSystem(pb.GetSystemRequest())
result("hostname", v.hostname == _host and sy.hostname == _host,
       f"GetVersion {v.hostname!r}, GetSystem {sy.hostname!r}, the host {_host!r}")
ifs = ops.ListInterfaces(pb.ListInterfacesRequest())
pg0 = next((i for i in ifs.interfaces if i.name == "pg0"), None)
result("interfaces", pg0 is not None and pg0.admin_up and "100.64.3.254/22" in pg0.addresses,
       f"{[i.name for i in ifs.interfaces]}")
pools = ops.ListPools(pb.ListPoolsRequest())
nb = next((p for p in pools.pools if p.name == "nb"), None)
result("pools", nb is not None and nb.capacity > 0 and list(nb.prefixes_inside),
       f"{[(p.name, p.capacity, p.subscribers) for p in pools.pools]}")
s = ops.Show(pb.ShowRequest(command="show cgnat pool"))
result("show", "nb" in s.text, s.text.splitlines()[0][:80] if s.text else "")
try:
    ops.Show(pb.ShowRequest(command="configure"))
    result("show-refuses-config", False, "Show ran configure")
except grpc.RpcError as e:
    result("show-refuses-config", e.code() == grpc.StatusCode.INVALID_ARGUMENT, e.code().name)

# telemetry: pg0's counters move while traffic runs
got = []
got_enable = []


def take():
    req = pb.SubscribeRequest(interval_ms=500, interface_counters=True,
                              interfaces=["pg0"], cgnat_totals=True)
    try:
        for u in tel.Subscribe(req, timeout=4):
            if u.WhichOneof("update") == "interfaces":
                for smp in u.interfaces.samples:
                    got.append(smp.counters.rx_packets)
                if len(got) == 1:
                    en = subprocess.run(["vppctl", "packet-generator", "enable"],
                                        capture_output=True, text=True)
                    got_enable.append((en.returncode, en.stdout + en.stderr))
    except grpc.RpcError as e:
        # The stream ends at its deadline: that is how this reader stops it.
        if e.code() != grpc.StatusCode.DEADLINE_EXCEEDED:
            raise


t = threading.Thread(target=take)
t.start()
t.join(6)
vpp_says = subprocess.run(["vppctl", "show", "interface", "pg0"], capture_output=True,
                          text=True).stdout.split()[-6:]
result("telemetry", len(got) >= 3 and got[-1] > got[0],
       f"pg0 rx_packets {got[:1]} -> {got[-1:]} in {len(got)} samples (the dataplane says {' '.join(vpp_says)})")

# an account without the role
nv = channel("netview")
try:
    rpc.ConfigServiceStub(nv).OpenCandidate(pb.OpenCandidateRequest())
    result("unprivileged-refused", False, "netview opened a candidate")
except grpc.RpcError as e:
    result("unprivileged-refused", e.code() == grpc.StatusCode.PERMISSION_DENIED,
           f"{e.code().name}: {e.details()[:120]}")
rr = rpc.ConfigServiceStub(nv).GetRunning(pb.GetRunningRequest())
# What it reads is what the VTY shows that account: without the vpp group
# it does not reach the dataplane, so the routing half and no pool.
result("unprivileged-reads", "! routing" in rr.text and bool(rr.revision),
       "netview reads the running configuration its role sees")

# no client certificate: refused at the handshake
try:
    ca = open(f"{CERTS}/ca.crt", "rb").read()
    bare = grpc.secure_channel(TARGET, grpc.ssl_channel_credentials(ca),
                               options=(("grpc.ssl_target_name_override", "prisma-api"),))
    rpc.ConfigServiceStub(bare).GetRunning(pb.GetRunningRequest(), timeout=5)
    result("no-client-cert", False, "a call without a client certificate was answered")
except grpc.RpcError as e:
    result("no-client-cert", e.code() == grpc.StatusCode.UNAVAILABLE, e.code().name)
