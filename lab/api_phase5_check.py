#!/usr/bin/env python3
"""
lab/api-grpc.sh's client for #558's fifth phase: nothing the VTY does is
unreachable through the API.

  clear              OperationalService.Clear for each target, held to what
                     the dataplane and FRR then say, and the refusals.
  watch <seconds>    TelemetryService.WatchLogging: one line per event, for
                     the lab to hold against the VTY's own monitor.
  system             SystemService against a server whose systemctl and
                     systemd-run are stand-ins (lab/api-grpc.sh writes them):
                     a reboot, a shutdown, a dataplane restart and an upgrade
                     whose phases stream - the real ones are measured on a
                     box, which a container cannot be.

Arguments: <stubs dir> <certs dir> <host:port> <mode> [arg]. One RESULT line
per check (and EVENT lines for `watch`).
"""
import subprocess
import sys
import time

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET, MODE = sys.argv[2], sys.argv[3], sys.argv[4]
ARG = sys.argv[5] if len(sys.argv) > 5 else ""


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


def reason_of(e):
    """The Diagnostics.reason a refused call carried, by name."""
    for k, v in e.trailing_metadata() or ():
        if k == "prisma-diagnostics-bin":
            d = pb.Diagnostics()
            d.ParseFromString(v)
            return pb.Diagnostics.Reason.Name(d.reason)
    return ""


def refused(name, fn, code, reason):
    try:
        fn()
    except grpc.RpcError as e:
        got = reason_of(e)
        result(name, e.code() == code and got == reason,
               f"{e.code().name} {got} {e.details()[:100]}")
        return
    result(name, False, "not refused")


def vpp(*words):
    return subprocess.run(["vppctl", *words], capture_output=True, text=True).stdout


T = pb.ClearRequest


def clear_checks():
    ops = rpc.OperationalServiceStub(channel("root"))
    before = vpp("show", "interface", "pg0")

    def one():
        r = ops.Clear(T(target=T.TARGET_INTERFACE_COUNTERS, name="pg0"), timeout=30)
        after = vpp("show", "interface", "pg0")
        result("clear-interface-one",
               r.command == "clear interface counters pg0"
               and "cleared: counters of pg0" in r.output
               and "rx packets" in before and "rx packets" not in after,
               f"before {'rx' if 'rx packets' in before else '-'}, after: "
               f"{' '.join(after.split())[:120]}")
    run("clear-interface-one", one)

    def every():
        r = ops.Clear(T(target=T.TARGET_INTERFACE_COUNTERS), timeout=30)
        after = vpp("show", "interface")
        result("clear-interface-all",
               r.command == "clear interface counters"
               and "every interface" in r.output and "packets" not in after,
               " ".join(after.split())[:120])
    run("clear-interface-all", every)

    def sub():
        r = ops.Clear(T(target=T.TARGET_CGNAT_SUBSCRIBER, name="100.64.11.5"), timeout=30)
        n = int(r.output.split("cleared ", 1)[1].split()[0])
        again = ops.Clear(T(target=T.TARGET_CGNAT_SUBSCRIBER, name="100.64.11.5"),
                          timeout=30)
        result("clear-subscriber",
               n >= 1 and "cleared 0 session(s) of 100.64.11.5" in again.output
               and "its block is released after the hold-down (" in r.output,
               f"{n} session(s); then: {again.output.strip()[:80]}")
    run("clear-subscriber", sub)

    def bgp():
        r = ops.Clear(T(target=T.TARGET_BGP, name="192.0.2.9", bgp=T.BGP_SOFT_OUT),
                      timeout=30)
        result("clear-bgp", r.command == "clear bgp 192.0.2.9 soft out", r.output.strip()[:100])
    run("clear-bgp", bgp)

    def ospf():
        r = ops.Clear(T(target=T.TARGET_OSPF_PROCESS), timeout=30)
        result("clear-ospf", r.command == "clear ospf process"
               and "cleared: process" in r.output, r.output.strip()[:100])
    run("clear-ospf", ospf)

    netview = rpc.OperationalServiceStub(channel("netview"))
    refused("clear-permission",
            lambda: netview.Clear(T(target=T.TARGET_INTERFACE_COUNTERS), timeout=30),
            grpc.StatusCode.PERMISSION_DENIED, "REASON_PERMISSION")
    refused("clear-usage",
            lambda: ops.Clear(T(target=T.TARGET_BGP, name="10.0.0.2 soft"), timeout=30),
            grpc.StatusCode.INVALID_ARGUMENT, "REASON_USAGE")
    refused("clear-usage-dataplane",
            lambda: ops.Clear(T(target=T.TARGET_CGNAT_SUBSCRIBER, name="10.9.9.9"),
                              timeout=30),
            grpc.StatusCode.FAILED_PRECONDITION, "REASON_OTHER")


def watch(seconds):
    tel = rpc.TelemetryServiceStub(channel("root"))
    n = 0
    try:
        for e in tel.WatchLogging(pb.WatchLoggingRequest(seconds=seconds),
                                  timeout=seconds + 30):
            kind = pb.LogEvent.Kind.Name(e.kind)
            print(f"EVENT {kind} {e.subscriber or '-'} {e.public_address or '-'} "
                  f"{e.port_first}-{e.port_last} {e.pool or '-'} "
                  f"ring={int(e.from_watch_ring)} {e.stream or '-'} sent={e.sent}",
                  flush=True)
            n += 1
        result("watch-ended", True, f"{n} events, then the stream ended at seconds")
    except grpc.RpcError as e:
        result("watch-ended", False, f"{e.code().name} {e.details()[:160]}")
    netview = rpc.TelemetryServiceStub(channel("netview"))

    def bad_filter():
        list(tel.WatchLogging(pb.WatchLoggingRequest(event="sideways"), timeout=10))
    refused_stream("watch-bad-filter", bad_filter, grpc.StatusCode.INVALID_ARGUMENT)
    # The lab's segment is the plugin's 0640 root:root, which netview is not
    # in: the VTY's monitor refuses that account, and so must the stream.
    try:
        got = list(netview.WatchLogging(pb.WatchLoggingRequest(seconds=1), timeout=20))
        result("watch-unreadable", False, f"not refused: {len(got)} event(s)")
    except grpc.RpcError as e:
        result("watch-unreadable", e.code() == grpc.StatusCode.PERMISSION_DENIED
               and "may not read /dev/shm/prisma-bpa-log" in e.details(),
               f"{e.code().name} {e.details()[:120]}")


def refused_stream(name, fn, code):
    try:
        fn()
    except grpc.RpcError as e:
        result(name, e.code() == code, f"{e.code().name} {e.details()[:100]}")
        return
    result(name, False, "not refused")


def system_checks():
    root = rpc.SystemServiceStub(channel("root"))
    netview = rpc.SystemServiceStub(channel("netview"))
    A = pb.SystemActionRequest
    refused("system-reboot-unsaved", lambda: root.Reboot(A(), timeout=60),
            grpc.StatusCode.FAILED_PRECONDITION, "REASON_UNSAVED")
    refused("system-reboot-permission", lambda: netview.Reboot(A(force=True), timeout=60),
            grpc.StatusCode.PERMISSION_DENIED, "REASON_PERMISSION")

    def reboot():
        r = root.Reboot(A(force=True), timeout=60)
        asked = open("/tmp/fake-systemctl.argv").read().split("\n")
        result("system-reboot-force",
               "restarting the box" in r.output and "reboot" in asked,
               f"systemctl asked: {asked[:3]}")
    run("system-reboot-force", reboot)

    def shutdown():
        r = root.Shutdown(A(force=True), timeout=60)
        asked = open("/tmp/fake-systemctl.argv").read().split("\n")
        result("system-shutdown-force",
               "shutting the box down" in r.output and "poweroff" in asked,
               f"systemctl asked: {asked[:3]}")
    run("system-shutdown-force", shutdown)

    def dataplane():
        r = root.RestartDataplane(A(), timeout=120)
        argv = open("/tmp/fake-systemd-run.argv").read()
        result("system-dataplane-restart",
               "prisma-api-exit 0" in r.output and "system dataplane restart" in argv
               and "--unit=prisma-routing-api-dataplane" in argv,
               r.output.strip().replace("\n", " | ")[:120])
    run("system-dataplane-restart", dataplane)

    def upgrade():
        t0 = time.time()
        phases = [pb.UpgradeProgress.Phase.Name(p.phase)
                  for p in root.Upgrade(pb.UpgradeRequest(version="0.149.99"), timeout=120)]
        status = root.GetUpgradeStatus(pb.GetUpgradeStatusRequest(), timeout=30)
        result("system-upgrade-stream",
               phases[:3] == ["STARTED", "DOWNLOAD", "VERIFY"]
               and "INSTALL" in phases and phases[-2:] == ["INSTALLED", "API_RESTART"]
               and pb.UpgradeProgress.Phase.Name(status.phase) == "API_RESTART"
               and status.api_away_seconds == 0 and "upgraded to 0.149.99" in status.output,
               f"{phases} in {time.time() - t0:.1f}s; status "
               f"{pb.UpgradeProgress.Phase.Name(status.phase)}")
    run("system-upgrade-stream", upgrade)

    def away():
        got = list(root.Upgrade(pb.UpgradeRequest(version="0.149.98"), timeout=120))
        result("system-upgrade-away", got[-1].api_away_seconds > 0
               and got[-1].version == "" and "0.149.98" in got[-2].version,
               f"last: {pb.UpgradeProgress.Phase.Name(got[-1].phase)} "
               f"away {got[-1].api_away_seconds}s")
    run("system-upgrade-away", away)
    refused_stream("system-upgrade-bad-version",
                   lambda: list(root.Upgrade(pb.UpgradeRequest(version="0.149"), timeout=30)),
                   grpc.StatusCode.INVALID_ARGUMENT)
    refused("system-upgrade-permission",
            lambda: list(netview.Upgrade(pb.UpgradeRequest(version="0.149.96"), timeout=30)),
            grpc.StatusCode.PERMISSION_DENIED, "REASON_PERMISSION")
    # A run still going: its log, with no exit line.
    with open("/run/prisma-routing/api/upgrade.log", "w") as f:
        f.write("downloading x\n")
    refused("system-upgrade-busy",
            lambda: list(root.Upgrade(pb.UpgradeRequest(version="0.149.97"), timeout=30)),
            grpc.StatusCode.FAILED_PRECONDITION, "REASON_BUSY")


if MODE == "clear":
    clear_checks()
elif MODE == "watch":
    watch(int(ARG or 15))
elif MODE == "system":
    system_checks()
