#!/usr/bin/env python3
"""
lab/api-grpc.sh's client for #598: GetSystem's CPU topology and per-CPU
usage, and GetHistory's per-CPU samples, against the lab's dataplane of two
workers and a server sampling once a second (PRISMA_API_HISTORY_INTERVAL=1).

What is held: the topology is this machine's - its online CPUs, the model
/proc/cpuinfo names - with the workers' CPUs as `worker`, numbered as
worker_load numbers them, and the main thread's as `main`; the usage is the
sampler's, a window of about a second, recent, with the two worker CPUs
polling; cpu_cores_text is `show system cpu cores` drawn from those fields;
GetHistory carries the per-CPU rows only when `cpus` asks for them.

Arguments: <stubs dir> <certs dir> <host:port>. One RESULT line per check.
"""
import re
import subprocess
import sys
import time

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET = sys.argv[2], sys.argv[3]
NAMES = ("cpu-topology", "cpu-planes", "cpu-usage", "cpu-usage-recent",
         "cpu-text", "history-cpus", "history-cpus-asked")


def channel(who):
    ca = open(f"{CERTS}/ca.crt", "rb").read()
    creds = grpc.ssl_channel_credentials(ca, open(f"{CERTS}/{who}.key", "rb").read(),
                                         open(f"{CERTS}/{who}.crt", "rb").read())
    return grpc.secure_channel(TARGET, creds,
                               options=(("grpc.ssl_target_name_override", "prisma-api"),))


def result(name, ok, detail=""):
    print(f"RESULT {name} {'ok' if ok else 'FAIL'} {detail}".rstrip(), flush=True)


def cpu_list(spec):
    out = []
    for part in filter(None, spec.strip().split(",")):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


online = cpu_list(open("/sys/devices/system/cpu/online").read())
model = next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo")
              if l.startswith("model name")), "")
op = rpc.OperationalServiceStub(channel("root"))
tel = rpc.TelemetryServiceStub(channel("root"))
try:
    # Two seconds of the server's one-second sampler: a window has ended.
    time.sleep(2)
    sy = op.GetSystem(pb.GetSystemRequest(), timeout=60)
except grpc.RpcError as e:
    print(f"  GetSystem: {e.code().name} {e.details()[:200]}")
    for n in NAMES:
        result(n, False, "no answer")
    sys.exit(0)

t = sy.cpu_topology
# Each thread's CPU as VPP itself says it: the lab starts the dataplane with
# `workers 2` and no corelist, so worker_load's cpu is the worker's number.
thr = subprocess.run(["vppctl", "show", "threads"], capture_output=True,
                     text=True).stdout
wcpus = [int(m.group(2)) for m in sorted(
    re.finditer(r"^\d+\s+vpp_wk_(\d+)\s.*?\(\d+\)\s+(\d+)", thr, re.M),
    key=lambda m: int(m.group(1)))]
main = next((int(m.group(1)) for m in re.finditer(
    r"^\d+\s+vpp_main\s.*?\(\d+\)\s+(\d+)", thr, re.M)), None)
by = {c.cpu: c for c in t.cpus}
print(f"  topology: {t.model!r}, {t.sockets} socket(s), {t.physical_cores} "
      f"core(s), {t.threads_per_core} thread(s) a core, {t.numa_nodes} node(s), "
      f"{t.logical_cpus} CPUs; workers on {wcpus}, main on {main}")
result("cpu-topology", t.logical_cpus == len(online) and sorted(by) == online
       and t.sockets >= 1 and t.physical_cores >= t.sockets
       and t.threads_per_core >= 1 and t.model == model
       and sum(s.logical_cpus for s in t.socket_cores) == len(online),
       f"{t.logical_cpus} CPUs (online {len(online)}), model {t.model!r}")
planes_ok = len(wcpus) == 2 and all(
    by[c].plane == "worker" and by[c].worker == i for i, c in enumerate(wcpus))
if main is not None and main not in wcpus:
    planes_ok = planes_ok and by[main].plane == "main"
result("cpu-planes", planes_ok and all(c.plane in ("control-plane", "main", "worker",
                                                     "none") for c in t.cpus),
       f"{[(c.cpu, c.plane, c.worker) for c in t.cpus if c.plane != 'control-plane']}")
use = {u.cpu: u for u in sy.cpu_usage}
print(f"  usage: {[(u.cpu, round(u.busy_percent), round(u.steal_percent, 1), u.polling, round(u.busy_ratio, 2), u.window_ms) for u in sy.cpu_usage if u.cpu in wcpus or u.cpu == main]}")
result("cpu-usage", sorted(use) == online and all(c in use for c in wcpus)
       and all(use[c].polling and use[c].busy_percent > 50 for c in wcpus)
       and all(500 <= u.window_ms <= 5000 for u in use.values())
       and all(0 <= u.busy_percent <= 100 and u.steal_percent >= 0 for u in use.values()),
       "every online CPU, the two workers polling, a window of about a second")
age = time.time() - (sy.cpu_usage_at.seconds + sy.cpu_usage_at.nanos / 1e9)
result("cpu-usage-recent", sy.HasField("cpu_usage_at") and -1 <= age <= 10,
       f"the window ended {age:.1f} s ago")
rows = {int(l.split()[0]): l for l in sy.cpu_cores_text.splitlines()
        if l[:5].strip().isdigit()}
result("cpu-text", sorted(rows) == online
       and all(f"worker {i}" in rows[c] for i, c in enumerate(wcpus))
       and sy.cpu_cores_text.splitlines()[0] == (t.model or
                                                  "CPU model not reported by the kernel")
       and all(len(l) <= 79 for l in sy.cpu_cores_text.splitlines()),
       f"{len(rows)} rows; worker 0's: {rows.get(wcpus[0] if wcpus else -1, '')!r}")
since = sy.cpu_usage_at
time.sleep(3)
hist = list(tel.GetHistory(pb.GetHistoryRequest(since=since, cpus=True), timeout=20))
print(f"  history since the window: {len(hist)} sample(s), "
      f"{[len(s.cpus) for s in hist]} CPU rows each")
result("history-cpus", len(hist) >= 2 and all(len(s.cpus) == len(online) for s in hist)
       and all({c.cpu for c in s.cpus if c.polling} >= set(wcpus) for s in hist)
       and not any(s.interfaces or s.workers for s in hist),
       "each sample every CPU, the workers polling, nothing else asked")
bare = list(tel.GetHistory(pb.GetHistoryRequest(since=since), timeout=20))
mem = list(tel.GetHistory(pb.GetHistoryRequest(since=since, memory=True), timeout=20))
result("history-cpus-asked", bare and mem and not any(s.cpus for s in bare + mem)
       and any(s.workers for s in bare),
       f"a bare request: {len(bare)} sample(s), none with CPUs; memory only: none either")
