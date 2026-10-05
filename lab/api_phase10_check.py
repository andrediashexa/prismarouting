"""
lab/api-grpc.sh's client for #575: what the boot left out for a card the box
did not have, through the API. The record is the one `prismash --load` writes
(lab/missing-card.sh makes it from a real boot; this phase writes its shape,
so the API's half is measured on its own): GetPendingConfiguration reads it,
the health service prisma.v1.Configuration is NOT_SERVING while it stands,
and Clear's TARGET_CONFIGURATION_PENDING gives it up through the VTY.

Arguments: <stubs dir> <certs dir> <host:port> <record path>. One RESULT line
per check.
"""
import json
import os
import sys

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402
from health.v1 import health_pb2 as hpb, health_pb2_grpc as hrpc  # noqa: E402

CERTS, TARGET, RECORD = sys.argv[2], sys.argv[3], sys.argv[4]


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


ch = channel("root")
op = rpc.OperationalServiceStub(ch)
health = hrpc.HealthStub(ch)


def health_of():
    r = health.Check(hpb.HealthCheckRequest(service="prisma.v1.Configuration"),
                     timeout=10)
    return hpb.HealthCheckResponse.ServingStatus.Name(r.status)


def nothing():
    p = op.GetPendingConfiguration(pb.GetPendingConfigurationRequest(), timeout=10)
    result("pending-none", not p.pending and not p.cards and health_of() == "SERVING",
           f"pending={p.pending} health={health_of()}")


def something():
    os.makedirs(os.path.dirname(RECORD), exist_ok=True)
    with open(RECORD, "w") as f:
        json.dump({"cards": ["ens20"], "left_out": ["interface ens20",
                   " ipv4 address 198.51.100.1/24", " exit"],
                   "since": 1791168000, "path": "/etc/prisma-routing/x.conf",
                   "applied": "00"}, f)
    p = op.GetPendingConfiguration(pb.GetPendingConfigurationRequest(), timeout=10)
    result("pending-read", p.pending and list(p.cards) == ["ens20"]
           and " ipv4 address 198.51.100.1/24" in p.left_out
           and p.since == 1791168000,
           f"{p.pending} {list(p.cards)} {list(p.left_out)}")
    result("pending-health", health_of() == "NOT_SERVING", health_of())


def cleared():
    r = op.Clear(pb.ClearRequest(
        target=pb.ClearRequest.TARGET_CONFIGURATION_PENDING), timeout=30)
    p = op.GetPendingConfiguration(pb.GetPendingConfigurationRequest(), timeout=10)
    result("pending-clear", r.command == "clear configuration pending"
           and not p.pending and not os.path.exists(RECORD)
           and health_of() == "SERVING",
           f"{r.command!r} {r.output.strip()[:120]!r} pending={p.pending}")


run("pending-none", nothing)
run("pending-read", something)
run("pending-clear", cleared)
