#!/usr/bin/env python3
"""
lab/api-grpc.sh's client for #558's first phase: why a change was refused,
the commit-confirmed window, the candidates' lifecycle - each provoked for
real against the server, and one RESULT line per check.

Arguments: <stubs dir> <certs dir> <host:port> <before|after> <state file>.
`before` runs on the lab's first server and leaves a `commit confirmed`
window open, writing its deadline to <state file>; `after` runs on a server
started afterwards (a restart, as an upgrade restarts it) with a candidate
idle time of seconds, reads the same window back, confirms it, and lets a
candidate expire.
"""
import json
import sys
import time
import uuid

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET, MODE, STATE = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
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


def refused(call):
    """(status, Diagnostics) of a call expected to be refused; (None, None) if not."""
    try:
        call()
        return None, None
    except grpc.RpcError as e:
        return e.code(), diagnostics(e)


def name(reason):
    return D.Reason.Name(reason)


cfg = rpc.ConfigServiceStub(channel("root"))
POOL = ["nat", "cgnat"]

if MODE == "before":
    # grammar: a line the prompt does not take
    c = cfg.OpenCandidate(pb.OpenCandidateRequest())
    code, d = refused(lambda: cfg.Edit(pb.EditRequest(
        candidate_id=c.candidate_id, path=POOL + ["pool P558"], lines=["frobnicate 1"])))
    result("reason-grammar", code == grpc.StatusCode.INVALID_ARGUMENT
           and d.reason == D.REASON_GRAMMAR, f"{code} {name(d.reason) if d else ''}")

    # validation: a pool without its prefixes, refused by the commit and
    # named by the check
    cfg.Edit(pb.EditRequest(candidate_id=c.candidate_id, path=POOL + ["pool P558"],
                            lines=["mode dynamic"]))
    chk = cfg.Check(pb.CandidateRef(candidate_id=c.candidate_id))
    code, d = refused(lambda: cfg.Commit(pb.CommitRequest(candidate_id=c.candidate_id)))
    result("reason-validation", code == grpc.StatusCode.FAILED_PRECONDITION
           and d.reason == D.REASON_VALIDATION and not chk.passed
           and chk.reason == D.REASON_VALIDATION,
           f"commit {name(d.reason) if d else code}, check {name(chk.reason)}")
    cfg.CloseCandidate(pb.CandidateRef(candidate_id=c.candidate_id))

    # limit: a 256-character identity path, refused at the prompt (#553)
    # The VTY takes the line and the commit refuses it before anything is
    # sent (0.149.10): FAILED_PRECONDITION, at the commit.
    c = cfg.OpenCandidate(pb.OpenCandidateRequest())
    path = "/tmp/" + "a" * 251
    cfg.Edit(pb.EditRequest(candidate_id=c.candidate_id, path=POOL,
                            lines=[f"identity file {path}"]))
    code, d = refused(lambda: cfg.Commit(pb.CommitRequest(candidate_id=c.candidate_id)))
    result("reason-limit", code == grpc.StatusCode.FAILED_PRECONDITION
           and d.reason == D.REASON_LIMIT and d.limit_field == "cgnat identity file"
           and d.limit == 255 and d.limit_size == 256,
           f"{name(d.reason) if d else code} {d.limit_field if d else ''}"
           f" {d.limit if d else ''}/{d.limit_size if d else ''}")
    cfg.CloseCandidate(pb.CandidateRef(candidate_id=c.candidate_id))

    # stale base: another candidate committed under this one
    a = cfg.OpenCandidate(pb.OpenCandidateRequest())
    b = cfg.OpenCandidate(pb.OpenCandidateRequest())
    cfg.Edit(pb.EditRequest(candidate_id=a.candidate_id, path=POOL + ["pool nb"],
                            lines=["max-blocks 3"]))
    cfg.Commit(pb.CommitRequest(candidate_id=a.candidate_id, comment="phase1 stale",
                                acknowledge_remap=True))
    cfg.Edit(pb.EditRequest(candidate_id=b.candidate_id, path=POOL + ["pool nb"],
                            lines=["max-blocks 5"]))
    code, d = refused(lambda: cfg.Commit(pb.CommitRequest(candidate_id=b.candidate_id)))
    now = cfg.GetRunning(pb.GetRunningRequest()).revision
    result("reason-stale-base", code == grpc.StatusCode.FAILED_PRECONDITION
           and d.reason == D.REASON_STALE_BASE and d.running_revision == now
           and now != b.base_revision,
           f"{name(d.reason) if d else code} moved to {d.running_revision if d else ''}, "
           f"GetRunning {now}")
    for x in (a, b):
        cfg.CloseCandidate(pb.CandidateRef(candidate_id=x.candidate_id))

    # permission: an account without the role
    nv = rpc.ConfigServiceStub(channel("netview"))
    code, d = refused(lambda: nv.OpenCandidate(pb.OpenCandidateRequest()))
    result("reason-permission", code == grpc.StatusCode.PERMISSION_DENIED
           and d.reason == D.REASON_PERMISSION, f"{code} {name(d.reason) if d else ''}")

    # a candidate this account never had
    code, d = refused(lambda: cfg.Edit(pb.EditRequest(
        candidate_id=uuid.uuid4().hex, path=[], lines=["hostname x"])))
    result("reason-unknown", code == grpc.StatusCode.NOT_FOUND
           and d.reason == D.REASON_CANDIDATE_UNKNOWN, f"{code} {name(d.reason) if d else ''}")

    # the re-map gate: pool nb has mappings (the lab sent traffic through it)
    g = cfg.OpenCandidate(pb.OpenCandidateRequest())
    cfg.Edit(pb.EditRequest(candidate_id=g.candidate_id, path=POOL + ["pool nb"],
                            lines=["no prefix-outside 200.150.0.16/30",
                                   "prefix-outside 200.150.0.20/30"]))
    chk = cfg.Check(pb.CandidateRef(candidate_id=g.candidate_id))
    code, d = refused(lambda: cfg.Commit(pb.CommitRequest(candidate_id=g.candidate_id)))
    result("reason-gate-remap", code == grpc.StatusCode.FAILED_PRECONDITION
           and d.reason == D.REASON_GATE_REMAP and len(d.gate_items) > 0
           and chk.reason == D.REASON_GATE_REMAP,
           f"commit {name(d.reason) if d else code} items {len(d.gate_items) if d else 0}, "
           f"check {name(chk.reason)} ({len(chk.remap)} remap)")
    cfg.CloseCandidate(pb.CandidateRef(candidate_id=g.candidate_id))

    # the candidates' lifecycle
    c = cfg.OpenCandidate(pb.OpenCandidateRequest())
    t0 = time.time()
    opened_ok = c.idle_seconds > 0 and c.expires_at.seconds >= int(t0) + c.idle_seconds - 2
    time.sleep(1.2)
    e = cfg.Edit(pb.EditRequest(candidate_id=c.candidate_id, path=POOL + ["pool nb"],
                                lines=["max-blocks 7"]))
    lst = cfg.ListCandidates(pb.ListCandidatesRequest())
    mine = [x for x in lst.candidates if x.candidate_id == c.candidate_id]
    time.sleep(1.2)
    lst2 = cfg.ListCandidates(pb.ListCandidatesRequest())
    mine2 = [x for x in lst2.candidates if x.candidate_id == c.candidate_id]
    ok = (opened_ok and len(mine) == 1 and mine[0].staged_lines >= 1
          and lst.idle_seconds == c.idle_seconds
          and mine[0].expires_at.seconds - mine[0].last_activity.seconds in
          (lst.idle_seconds - 1, lst.idle_seconds, lst.idle_seconds + 1)
          and e.expires_at.seconds >= c.expires_at.seconds
          and mine2 and mine2[0].last_activity.seconds == mine[0].last_activity.seconds)
    result("candidates-listed", ok,
           f"idle {lst.idle_seconds}s, staged {mine[0].staged_lines if mine else '-'}, "
           f"listing kept last_activity: {bool(mine2) and mine2[0].last_activity == mine[0].last_activity}")
    cfg.CloseCandidate(pb.CandidateRef(candidate_id=c.candidate_id))

    # the confirm window, left open for the server that comes after
    nothing = cfg.GetPendingConfirm(pb.GetPendingConfirmRequest())
    c = cfg.OpenCandidate(pb.OpenCandidateRequest())
    cfg.Edit(pb.EditRequest(candidate_id=c.candidate_id, path=POOL + ["pool nb"],
                            lines=["max-blocks 6"]))
    r = cfg.Commit(pb.CommitRequest(candidate_id=c.candidate_id, comment="phase1 window",
                                    confirmed_minutes=5, acknowledge_remap=True))
    p = cfg.GetPendingConfirm(pb.GetPendingConfirmRequest())
    run = cfg.GetRunning(pb.GetRunningRequest()).revision
    result("pending-confirm", not nothing.pending and p.pending
           and p.deadline.seconds == r.confirm_deadline.seconds and p.account == "root"
           and p.comment == "phase1 window" and p.revision == run and p.minutes == 5,
           f"before: {nothing.pending}; now: {p.pending} {p.account} '{p.comment}' "
           f"deadline {p.deadline.seconds} (commit said {r.confirm_deadline.seconds})")
    json.dump({"deadline": p.deadline.seconds, "revision": p.revision}, open(STATE, "w"))
    nvp = rpc.ConfigServiceStub(channel("netview")).GetPendingConfirm(
        pb.GetPendingConfirmRequest())
    result("pending-confirm-any-account", nvp.pending and nvp.deadline.seconds == p.deadline.seconds,
           f"an account without the role reads it too: {nvp.pending} {nvp.deadline.seconds}")

else:
    was = json.load(open(STATE))
    p = cfg.GetPendingConfirm(pb.GetPendingConfirmRequest())
    result("pending-confirm-restart", p.pending and p.deadline.seconds == was["deadline"]
           and p.comment == "phase1 window",
           f"after the server restarted: {p.pending} deadline {p.deadline.seconds} "
           f"(was {was['deadline']}) '{p.comment}'")
    cfg.Confirm(pb.ConfirmRequest())
    q = cfg.GetPendingConfirm(pb.GetPendingConfirmRequest())
    result("pending-confirm-closed", not q.pending, f"after Confirm: {q.pending}")

    # expiry, on this server's short idle time
    c = cfg.OpenCandidate(pb.OpenCandidateRequest())
    lst = cfg.ListCandidates(pb.ListCandidatesRequest())
    time.sleep(lst.idle_seconds + 3)
    code, d = refused(lambda: cfg.Edit(pb.EditRequest(
        candidate_id=c.candidate_id, path=[], lines=["hostname x"])))
    after = cfg.ListCandidates(pb.ListCandidatesRequest())
    result("candidate-expired", code == grpc.StatusCode.NOT_FOUND
           and d.reason == D.REASON_CANDIDATE_EXPIRED
           and all(x.candidate_id != c.candidate_id for x in after.candidates),
           f"idle {lst.idle_seconds}s: {code} {name(d.reason) if d else ''}")
