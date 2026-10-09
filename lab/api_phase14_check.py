"""
lab/api-grpc.sh's client for #596: `show system tuning advice` through the
API, GetTuningAdvice, against the lab's dataplane of two workers.

The fields and the screen are one reading (apiserver.tuning_advice_read), so
what is held here is that the reading is of this dataplane - its two
workers, each on a CPU and polling - that the text carried is the screen of
that same reading, that the window is the seconds asked, and that a window
past 60 s is refused before anything is measured.

Arguments: <stubs dir> <certs dir> <host:port>. One RESULT line per check.
"""
import re
import sys
import time

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET = sys.argv[2], sys.argv[3]

# Two nodes of four cores with HT, the NIC on node 1: a host to pin onto.
HOST = "CPU NODE SOCKET CORE\n" + "\n".join(
    f"{c} {(c // 4) % 2} {(c // 4) % 2} {c % 8}" for c in range(16)) \
    + "\nnic-node 1\n"


def channel(who):
    ca = open(f"{CERTS}/ca.crt", "rb").read()
    creds = grpc.ssl_channel_credentials(ca, open(f"{CERTS}/{who}.key", "rb").read(),
                                         open(f"{CERTS}/{who}.crt", "rb").read())
    return grpc.secure_channel(TARGET, creds,
                               options=(("grpc.ssl_target_name_override", "prisma-api"),))


def result(name, ok, detail=""):
    print(f"RESULT {name} {'ok' if ok else 'FAIL'} {detail}".rstrip(), flush=True)


op = rpc.OperationalServiceStub(channel("root"))
try:
    t0 = time.monotonic()
    got = op.GetTuningAdvice(pb.GetTuningAdviceRequest(
        seconds=2, host_topology=HOST), timeout=120)
    took = time.monotonic() - t0
except grpc.RpcError as e:
    got, took = None, 0.0
    print(f"  GetTuningAdvice: {e.code().name} {e.details()[:200]}")
if got is None:
    for name in ("tuning-workers", "tuning-window", "tuning-text-same",
                 "tuning-sizing", "tuning-snippets"):
        result(name, False, "no answer")
else:
    f = got.facts
    wk = [t for t in f.threads if t.worker >= 0]
    print(f"  workers {f.worker_cpus}, threads "
          f"{[(t.worker, t.cpu, round(t.loops_per_second)) for t in f.threads]}"
          f", window {f.window_ms} ms in {took:.1f} s, virtualization "
          f"{f.virtualization!r} ({f.virtualization_source})")
    result("tuning-workers", len(wk) == 2 and all(t.cpu >= 0 for t in wk)
           and all(t.loops_per_second > 0 for t in wk) and f.worker_cpus,
           f"{[(t.worker, t.cpu, t.loops_per_second) for t in wk]}")
    result("tuning-window", 1900 <= f.window_ms <= 4000 and took >= 2.0
           and "measured over 2 s" in got.text,
           f"{f.window_ms} ms, {took:.1f} s")
    m = re.search(r"workers (\S+)\s", " ".join(got.text.split()) + " ")
    result("tuning-text-same", bool(m) and m.group(1) == f.worker_cpus,
           f"text says {m and m.group(1)!r}, fields {f.worker_cpus!r}")
    sizes = [r.vcpus for r in got.sizing]
    result("tuning-sizing", sizes[:1] == [4] and sizes[-1:] == [32]
           and all(r.workers > 0 for r in got.sizing), f"{sizes}")
    hosted = f.virtualization not in ("", "none")
    if hosted:
        ok = got.HasField("snippets") and got.snippets.host_topology_used \
            and "HOST_W" not in got.snippets.proxmox_hookscript \
            and "map()" in got.snippets.proxmox_hookscript
    else:
        ok = not got.HasField("snippets")
    result("tuning-snippets", ok,
           f"hosted={hosted} snippets={got.HasField('snippets')}")
try:
    op.GetTuningAdvice(pb.GetTuningAdviceRequest(seconds=61), timeout=30)
    result("tuning-refused", False, "answered")
except grpc.RpcError as e:
    result("tuning-refused", e.code() == grpc.StatusCode.INVALID_ARGUMENT
           and "1 to 60" in e.details(), f"{e.code().name} {e.details()}")
