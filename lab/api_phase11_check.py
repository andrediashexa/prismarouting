"""
lab/api-grpc.sh's client for #588: the box's record of events through the
API. What the phases before this one did through the API is in it - the
commits, and phase 5's reboot, dataplane restart and upgrade, asked through
the real doors daemon, each stamped with the account the kernel named -
and ListEvents reads it with `show log`'s filters and its screen. GetSystem
carries why the dataplane last started, as the record says.

Arguments: <stubs dir> <certs dir> <host:port>. One RESULT line per check.
"""
import sys

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET = sys.argv[2], sys.argv[3]


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


op = rpc.OperationalServiceStub(channel("root"))
op_ro = rpc.OperationalServiceStub(channel("netview"))


def every(**kw):
    return list(op.ListEvents(pb.ListEventsRequest(last=1000, **kw), timeout=30).events)


def commits():
    got = [e for e in every(source="api") if e.kind == "commit"]
    result("events-api-commit",
           bool(got) and all(e.origin == "the API" for e in got)
           and any(e.account == "root" for e in got),
           f"{len(got)} commit(s): {[(e.account, e.text[:50]) for e in got[:3]]}")


def asked():
    got = [e for e in every(source="api") if e.kind == "asked"]
    by = {(e.cause, e.account) for e in got}
    result("events-asked-reboot-by-opsman", ("reboot", "opsman") in by,
           f"{sorted(by)}")
    result("events-asked-restart-by-root", ("operator", "root") in by
           and any(e.text == "system dataplane restart asked" for e in got),
           f"{sorted(by)}")
    result("events-asked-upgrade", any(
        e.cause == "upgrade" and e.text == "system upgrade to 0.149.99 asked"
        for e in got), f"{[e.text for e in got]}")
    # netview asks twice in phase 5: once while it is in prisma-operator
    # (#565), allowed, and once after it was taken out, refused.
    nv = [e for e in got if e.cause == "reboot" and e.account == "netview"]
    result("events-refused-not-recorded", len(nv) == 1,
           f"netview's reboots recorded: {len(nv)} - the allowed one, not the refused")


def filters():
    two = list(op.ListEvents(pb.ListEventsRequest(source="api", last=2),
                             timeout=30).events)
    result("events-last-source", len(two) == 2 and all(e.source == "api" for e in two)
           and two[0].time.ToSeconds() <= two[1].time.ToSeconds(),
           f"{[(e.source, e.time.ToSeconds()) for e in two]}")
    warn = every(severity="warning")
    result("events-severity", all(e.severity in ("error", "warning") for e in warn),
           f"{len(warn)} at warning or above")
    since = every(since="2099-01-01")
    result("events-since", since == [], f"{len(since)} after 2099")
    # #588 phase 2: `kind`, as `show log kind`.
    com = every(kind="commit")
    result("events-kind", bool(com) and all(e.kind == "commit" for e in com),
           f"{len(com)} commit(s), kinds {sorted({e.kind for e in com})}")


def settings():
    """#588 phase 2: the VTY's `api enabled yes` commit, before phase 1,
    applied the API's settings - an event of kind "settings" whose subject
    says which, through BoxEvent.subject."""
    got = every(kind="settings")
    result("events-settings-subject", any(
        e.subject == "api" and e.text.startswith("the API settings were")
        for e in got), f"{[(e.subject, e.text[:60], e.reason[:40]) for e in got]}")


def screen():
    r = op.ListEvents(pb.ListEventsRequest(source="api", last=3), timeout=30)
    s = op.Show(pb.ShowRequest(command="show log source api last 3"), timeout=30)
    result("events-text-is-vty", bool(r.text) and r.text.strip() == s.text.strip(),
           f"{r.text[:120]!r} / {s.text[:120]!r}")


def refused():
    for name, req, said in (("events-since-refused", {"since": "tomorrow"}, "is not a time"),
                            ("events-source-refused", {"source": "frr"}, "source is one of"),
                            ("events-kind-refused", {"kind": "weather"}, "kind is one of")):
        try:
            op.ListEvents(pb.ListEventsRequest(**req), timeout=30)
            result(name, False, "answered")
        except grpc.RpcError as e:
            result(name, e.code() == grpc.StatusCode.INVALID_ARGUMENT
                   and said in e.details(), f"{e.code().name} {e.details()}")


def any_account():
    a = len(op_ro.ListEvents(pb.ListEventsRequest(last=1000), timeout=30).events)
    b = len(every())
    result("events-any-account", a == b and a > 0, f"netview {a}, root {b}")


def cause():
    s = op.GetSystem(pb.GetSystemRequest(), timeout=60)
    result("system-start-cause", s.dataplane_start_cause == "unexpected"
           and s.dataplane_restarted,
           f"cause={s.dataplane_start_cause!r} restarted={s.dataplane_restarted}")


for name, fn in (("events-api-commit", commits), ("events-asked", asked),
                 ("events-filters", filters), ("events-text-is-vty", screen),
                 ("events-settings-subject", settings),
                 ("events-refused", refused), ("events-any-account", any_account),
                 ("system-start-cause", cause)):
    run(name, fn)
