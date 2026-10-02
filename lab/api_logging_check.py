#!/usr/bin/env python3
"""
lab/api-grpc.sh's client for the CGNAT log export over the API (#555).

  api_logging_check.py <stubs> <certdir> <addr> state
      GetLogging, each field held to the screen the server read (its `text`,
      parsed by the same rules) and to the VTY's own `show cgnat logging`
      through Show; prints RESULT lines and the syslog row.
  api_logging_check.py <stubs> <certdir> <addr> health
      grpc.health.v1's answer for prisma.v1.LogExport.
  api_logging_check.py <stubs> <certdir> <addr> events <seconds>
      Subscribe with logging_events for that long; one line per event.
"""
import os
import sys
import time

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                "src", "prismash"))
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402
import logstate  # noqa: E402

C, ADDR, WHAT = sys.argv[2], sys.argv[3], sys.argv[4]
creds = grpc.ssl_channel_credentials(open(f"{C}/ca.crt", "rb").read(),
                                     open(f"{C}/root.key", "rb").read(),
                                     open(f"{C}/root.crt", "rb").read())
ch = grpc.secure_channel(ADDR, creds, options=(("grpc.ssl_target_name_override",
                                                "prisma-api"),))


def result(name, ok, detail=""):
    print(f"RESULT {name} {'ok' if ok else 'FAIL'} {detail}".rstrip())


if WHAT == "state":
    try:
        st = rpc.OperationalServiceStub(ch).GetLogging(pb.GetLoggingRequest(), timeout=20)
    except grpc.RpcError as e:
        result("logging-state", False, f"{e.code().name}: {e.details()}")
        sys.exit(0)
    rows = {r["kind"]: r for r in logstate.stream_rows(logstate.parse_logging(st.text))}
    got = {s.kind: s for s in st.streams}
    same = list(got) == list(logstate.STREAMS) and all(
        getattr(got[k], f) == rows[k][f]
        for k in got for f in ("configured", "collector", "source", "carrier", "connection",
                               "records", "bytes_sent", "packets_sent", "pending_bytes",
                               "spill_bytes", "spill_cap_bytes", "dropped_over_cap",
                               "send_failures", "dropped"))
    result("logging-fields", same, "every stream field is the screen's")
    vty = rpc.OperationalServiceStub(ch).Show(pb.ShowRequest(command="show cgnat logging"),
                                              timeout=20).text
    line = [x for x in st.text.splitlines() if x.startswith("syslog transport")]
    result("logging-is-vty", bool(line) and line[0] in vty,
           "the screen GetLogging read is the VTY's show cgnat logging")
    s = got.get("syslog")
    print(f"SYSLOG configured={s.configured} connection={s.connection} carrier={s.carrier} "
          f"records={s.records} bytes_sent={s.bytes_sent} pending={s.pending_bytes} "
          f"spill={s.spill_bytes} delivering={s.delivering} "
          f"reason={s.stalled_reason!r} last_progress={s.last_progress.seconds}")
elif WHAT == "health":
    from health.v1 import health_pb2 as hpb, health_pb2_grpc as hrpc
    try:
        r = hrpc.HealthStub(ch).Check(hpb.HealthCheckRequest(service="prisma.v1.LogExport"),
                                      timeout=10)
        print("HEALTH", hpb.HealthCheckResponse.ServingStatus.Name(r.status))
    except grpc.RpcError as e:
        print("HEALTH", e.code().name)
elif WHAT == "events":
    end = time.time() + float(sys.argv[5])
    try:
        for u in rpc.TelemetryServiceStub(ch).Subscribe(
                pb.SubscribeRequest(interval_ms=500, logging_events=True),
                timeout=float(sys.argv[5])):
            if u.WhichOneof("update") == "event":
                print("EVENT", u.event.kind, u.event.text, flush=True)
            if time.time() > end:
                break
    except grpc.RpcError as e:
        if e.code() != grpc.StatusCode.DEADLINE_EXCEEDED:
            print("EVENTS", e.code().name, flush=True)
