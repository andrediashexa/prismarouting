"""
lab/api-grpc.sh's client for #588 phase 3: the box's record as it is written,
and where it goes, through the API.

WatchEvents streams what is recorded while the call is open - with
ListEvents' filters, nothing first unless `last` or `since` asks for it -
and refuses a filter `show log` refuses. system > logging is set with Edit
at its path, read back typed in GetConfig's SystemConfig.log_destinations,
and GetSystem says each destination and whether the watcher has said
anything of it, with `show system logging` as its text.

Arguments: <stubs dir> <certs dir> <host:port> <prismash command>. One
RESULT line per check. Events are written as the host's scripts write them
(events.py record), as root, while the stream is open.
"""
import subprocess
import sys
import threading
import time

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET, HX = sys.argv[2], sys.argv[3], sys.argv[4]


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


def record(kind, severity, text):
    subprocess.run([sys.executable, "src/prismash/events.py", "record",
                    f"host={kind}", severity, text], check=True,
                   capture_output=True)


op = rpc.OperationalServiceStub(channel("root"))
cfg = rpc.ConfigServiceStub(channel("root"))


def watch(req, seconds, during=None):
    """The BoxEvents a WatchEvents call streamed in `seconds`, `during`
    run a second after it opened."""
    call = op.WatchEvents(req)
    got = []

    def read():
        try:
            for ev in call:
                got.append(ev)
        except grpc.RpcError as e:
            if e.code() != grpc.StatusCode.CANCELLED:
                got.append(e)

    t = threading.Thread(target=read)
    t.start()
    time.sleep(1.5)
    if during:
        during()
    time.sleep(seconds)
    call.cancel()
    t.join(10)
    return got


def follows():
    record("gave-up", "warning", "before the watch opened")

    def write():
        record("gave-up", "warning", "watched, one")
        record("upgraded", "notice", "not the kind watched")
        record("gave-up", "warning", "watched, two")

    got = watch(pb.ListEventsRequest(kind="gave-up"), 4, write)
    texts = [e.text for e in got if hasattr(e, "text")]
    result("watch-follows", texts == ["watched, one", "watched, two"],
           f"{texts}")
    result("watch-fields", bool(got) and all(
        hasattr(e, "kind") and e.kind == "gave-up" and e.source == "host"
        and e.severity == "warning" and e.time.seconds > 0 for e in got),
           f"{[(e.kind, e.source, e.severity) for e in got if hasattr(e, 'kind')]}")


def backlog():
    got = watch(pb.ListEventsRequest(kind="gave-up", last=2), 1)
    texts = [e.text for e in got if hasattr(e, "text")]
    result("watch-last-first", texts == ["watched, one", "watched, two"],
           f"{texts}")


def refused():
    try:
        for _ in op.WatchEvents(pb.ListEventsRequest(kind="weather"),
                                timeout=30):
            break
        result("watch-refused", False, "answered")
    except grpc.RpcError as e:
        result("watch-refused", e.code() == grpc.StatusCode.INVALID_ARGUMENT
               and "kind is one of" in e.details(),
               f"{e.code().name} {e.details()}")


def logging():
    cid = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id
    cfg.Edit(pb.EditRequest(candidate_id=cid, path=["system", "logging"],
                            lines=["host 192.0.2.77 protocol tcp severity "
                                   "warning"]), timeout=60)
    cfg.Commit(pb.CommitRequest(candidate_id=cid, comment="#588 phase 3"),
               timeout=120)
    got = cfg.GetConfig(pb.GetConfigRequest(areas=["system"]), timeout=60)
    d = [(x.address, x.port, x.protocol, x.severity)
         for x in got.system.log_destinations]
    result("logging-typed", ("192.0.2.77", 601, "tcp", "warning") in d, f"{d}")
    vty = subprocess.run(HX.split() + ["-c", "show running-config"],
                         capture_output=True, text=True).stdout
    result("logging-vty", "  host 192.0.2.77 protocol tcp severity warning"
           in vty, "the VTY reads what the API wrote")
    s = op.GetSystem(pb.GetSystemRequest(), timeout=60)
    st = [(x.destination.address, x.known) for x in s.logging]
    result("logging-system", ("192.0.2.77", False) in st
           and "192.0.2.77:601 tcp  warning and above" in s.logging_text,
           f"{st} {s.logging_text[:160]!r}")
    try:
        cid = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id
        cfg.Edit(pb.EditRequest(candidate_id=cid, path=["system", "logging"],
                                lines=["host syslog.example"]), timeout=60)
        result("logging-refused", False, "taken")
    except grpc.RpcError as e:
        result("logging-refused", e.code() == grpc.StatusCode.INVALID_ARGUMENT
               and ("not an IP address" in e.details()
                    or "logging: not taken" in e.details()),
               f"{e.code().name} {e.details()[:120]}")


for name, fn in (("watch-follows", follows), ("watch-last-first", backlog),
                 ("watch-refused", refused), ("logging-typed", logging)):
    run(name, fn)
