"""
lab/api-grpc.sh's client for docs/bng.md 0.4 item 2 (#597): the BNG's
configuration as fields - the `pppoe` and `aaa` blocks through SetConfig,
Commit and GetConfig, held to GetRunning's text and to the files the commit
wrote, every answer searched for the secrets it set - and, with `old`, a
client built from the contract of a release before any of it, against this
server.

Arguments: <stubs dir> <certs dir> <host:port> new|old [<prismash command>].
One RESULT line per check.
"""
import sys

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET, MODE = sys.argv[2], sys.argv[3], sys.argv[4]
SECRETS = ("Sekr1t-A", "Sekr1t-B", "Dae-S3c", "Loc-Pw1", "Sekr1t-C")


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


def code_of(fn):
    try:
        fn()
        return "OK"
    except grpc.RpcError as e:
        return e.code().name


cfg = rpc.ConfigServiceStub(channel("root"))
op = rpc.OperationalServiceStub(channel("root"))


def opened():
    return cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60).candidate_id


def committed(cid, comment):
    r = cfg.Commit(pb.CommitRequest(candidate_id=cid, comment=comment),
                   timeout=300)
    return r


def clean(*messages):
    """True when none of the secrets is in any of the messages' bytes."""
    raw = b"".join(m.SerializeToString() for m in messages)
    return not any(s.encode() in raw for s in SECRETS)


def running(areas=("pppoe", "aaa")):
    return cfg.GetConfig(pb.GetConfigRequest(areas=list(areas)), timeout=60)


def blocks():
    """The two objects set_both() sets: built when called, so `old`'s
    stubs, which have none of these messages, never build them."""
    aaa = pb.AaaConfig(radius=pb.RadiusConfig(
        servers=[pb.RadiusServerConfig(address="192.0.2.1", secret="Sekr1t-A",
                                       priority=1),
                 pb.RadiusServerConfig(address="192.0.2.2", secret="Sekr1t-B",
                                       role="auth", auth_port=1645,
                                       priority=2)],
        retries=0, dead_time_seconds=10,
        dynamic_authorization_listen="127.0.0.1",
        dynamic_authorization_clients=[
            pb.RadiusClientConfig(address="192.0.2.1", secret="Dae-S3c")],
        rate_profiles=[pb.RateProfileConfig(name="UP15", down_kbit=0,
                                            up_kbit=15000)]))
    pppoe = pb.PppoeConfig(
        interfaces=["pg0"], gateway="100.64.200.1",
        address_pools=["100.64.200.10-100.64.200.200"],
        dns_servers=["8.8.8.8"], tcp_mss=1452,
        rate_limit_default_down_kbit=20000, rate_limit_default_up_kbit=10000,
        local_users=[pb.PppoeLocalUser(name="bob", password="Loc-Pw1")])
    return aaa, pppoe


def set_both():
    AAA, PPPOE = blocks()
    cid = opened()
    ra = cfg.SetConfig(pb.SetConfigRequest(candidate_id=cid, aaa=AAA), timeout=60)
    rp = cfg.SetConfig(pb.SetConfigRequest(candidate_id=cid, pppoe=PPPOE),
                       timeout=60)
    result("set-lines", list(ra.path) == ["aaa", "radius"]
           and list(rp.path) == ["pppoe"]
           and "server 192.0.2.1 secret <set, not shown> priority 1" in ra.lines
           and "local-user bob password <set, not shown>" in rp.lines
           and clean(ra, rp),
           f"{list(ra.lines)} {list(rp.lines)}")
    c = cfg.GetConfig(pb.GetConfigRequest(candidate_id=cid,
                                          areas=["pppoe", "aaa"]), timeout=60)
    r = c.aaa.radius
    result("candidate-read",
           c.pppoe.configured and list(c.pppoe.interfaces) == ["pg0"]
           and c.pppoe.rate_limit_default_down_kbit == 20000
           and [(u.name, u.password_set, u.password) for u in c.pppoe.local_users]
           == [("bob", True, "")]
           and [(s.address, s.role, s.auth_port, s.priority, s.secret_set,
                 s.secret) for s in r.servers]
           == [("192.0.2.1", "both", 1812, 1, True, ""),
               ("192.0.2.2", "auth", 1645, 2, True, "")]
           and r.HasField("retries") and r.retries == 0
           and not r.HasField("timeout_seconds") and clean(c),
           f"{[(s.address, s.priority) for s in r.servers]} retries "
           f"{r.retries if r.HasField('retries') else 'unset'}")
    cm = committed(cid, "bng typed api")
    t = running()
    text = cfg.GetRunning(pb.GetRunningRequest(), timeout=60).text
    parts = {"pppoe": t.pppoe == c.pppoe, "aaa": t.aaa == c.aaa,
             "clean": clean(t, cm),
             # GetRunning is the configuration's text, as it was before
             # #597 for every secret (an SNMP community, a BGP password):
             # held here to the blocks, not to what it does with a secret.
             "server": "\n  server 192.0.2.1 secret " in text,
             "user": "\n local-user bob password " in text}
    result("commit-running", all(parts.values()),
           f"revision {cm.revision}: GetConfig of the running configuration "
           f"is the candidate's, GetRunning has both blocks: {parts}"
           + ("" if all(parts.values()) else
              f" running {t.pppoe} {t.aaa} candidate {c.pppoe} {c.aaa} "
              f"text {[ln for ln in text.splitlines() if 'secret' in ln or 'password' in ln]}"))
    aaa_conf = open("/etc/prisma-routing/aaa.conf").read()
    pppoe_conf = open("/etc/prisma-routing/pppoe.conf").read()
    result("secrets-reached-the-box",
           "secret Sekr1t-A" in aaa_conf and "secret Dae-S3c" in aaa_conf
           and "password Loc-Pw1" in pppoe_conf,
           "the files the commit wrote carry the secrets the API set")


def omitted():
    before = running()
    cid = opened()
    cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=cid, interface=pb.InterfaceConfig(name="pg0",
                                                       description="bng-api"),
        fields=["description"]), timeout=60)
    r = cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=cid, aaa=pb.AaaConfig(radius=pb.RadiusConfig(
            timeout_seconds=5)), fields=["radius.timeout_seconds"]), timeout=60)
    committed(cid, "a SetConfig that leaves the BNG alone")
    after = running()
    want = pb.TypedConfig()
    want.CopyFrom(before)
    want.aaa.radius.timeout_seconds = 5
    result("omitted-kept", list(r.lines) == ["timeout 5"]
           and after.pppoe == before.pppoe
           and list(after.aaa.radius.servers) == list(before.aaa.radius.servers)
           and after.aaa == want.aaa,
           "an interface's SetConfig and one naming radius.timeout_seconds: "
           "the servers, the clients, the pppoe block kept")


def secret_kept():
    cid = opened()
    r = cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=cid, aaa=pb.AaaConfig(radius=pb.RadiusConfig(servers=[
            pb.RadiusServerConfig(address="192.0.2.2", role="auth",
                                  auth_port=1645, priority=1),
            pb.RadiusServerConfig(address="192.0.2.1", priority=2),
            pb.RadiusServerConfig(address="192.0.2.3", secret="Sekr1t-C",
                                  priority=3)])),
        fields=["radius.servers"]), timeout=60)
    bad = code_of(lambda: cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=cid, aaa=pb.AaaConfig(radius=pb.RadiusConfig(servers=[
            pb.RadiusServerConfig(address="192.0.2.9")])),
        fields=["radius.servers"]), timeout=60))
    committed(cid, "failover order changed, secrets kept")
    t = running()
    conf = open("/etc/prisma-routing/aaa.conf").read()
    result("secret-kept",
           [(s.address, s.priority) for s in t.aaa.radius.servers]
           == [("192.0.2.2", 1), ("192.0.2.1", 2), ("192.0.2.3", 3)]
           and "server 192.0.2.2 secret Sekr1t-B" in conf
           and "server 192.0.2.1 secret Sekr1t-A" in conf
           and "server 192.0.2.3 secret Sekr1t-C" in conf and clean(r, t),
           f"{list(r.lines)}")
    result("new-server-needs-secret", bad == "INVALID_ARGUMENT", bad)


def every_area():
    t = cfg.GetConfig(pb.GetConfigRequest(), timeout=120)
    result("every-area", t.pppoe.configured and t.aaa.configured
           and t.interfaces and clean(t),
           "GetConfig with no area answers pppoe and aaa too")


def reads():
    s = op.ListPppoeSessions(pb.ListPppoeSessionsRequest(), timeout=60)
    m = op.GetPppoeSummary(pb.GetPppoeSummaryRequest(), timeout=60)
    result("pppoe-reads", not s.running and not s.sessions
           and "PPPoE is not running" in s.text
           and m.configured and m.control_plane == "stopped"
           and list(m.access_interfaces) == ["pg0"] and m.radius
           and not m.trail_known and "configured, NOT running" in m.text,
           f"running {s.running}; summary {m.control_plane}")
    code = code_of(lambda: op.Clear(pb.ClearRequest(
        target=pb.ClearRequest.TARGET_PPPOE_ALL), timeout=60))
    result("clear-not-running", code == "FAILED_PRECONDITION", code)


def deleted():
    cid = opened()
    cfg.DeleteConfig(pb.DeleteConfigRequest(candidate_id=cid, area="pppoe"),
                     timeout=60)
    cfg.DeleteConfig(pb.DeleteConfigRequest(candidate_id=cid, area="aaa"),
                     timeout=60)
    committed(cid, "no pppoe, no aaa")
    t = running()
    text = cfg.GetRunning(pb.GetRunningRequest(), timeout=60).text
    result("deleted", not t.pppoe.configured and not t.aaa.configured
           and "\npppoe\n" not in text and "\naaa\n" not in text,
           "DeleteConfig area pppoe and aaa: the blocks gone")


def old_client():
    # A contract from before #597's typed API: none of its new messages,
    # fields or targets. Everything it knows still answers as it did.
    v = op.GetVersion(pb.GetVersionRequest(), timeout=60)
    t = cfg.GetConfig(pb.GetConfigRequest(), timeout=120)
    names = [i.name for i in t.interfaces]
    result("old-getconfig", "pg0" in names and t.pools,
           f"{v.release}: GetConfig with no area parses, {len(names)} "
           f"interfaces, {len(t.pools)} pools")
    cid = opened()
    s = cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=cid, interface=pb.InterfaceConfig(name="pg0",
                                                       description="old-client"),
        fields=["description"]), timeout=60)
    cfg.Commit(pb.CommitRequest(candidate_id=cid, comment="an old client"),
               timeout=300)
    t = cfg.GetConfig(pb.GetConfigRequest(areas=["interfaces"]), timeout=60)
    pg0 = [i for i in t.interfaces if i.name == "pg0"]
    result("old-setconfig", list(s.lines) == ["description old-client"]
           and pg0 and pg0[0].description == "old-client",
           "SetConfig, Commit, GetConfig of an interface")
    c = op.Clear(pb.ClearRequest(target=pb.ClearRequest.TARGET_INTERFACE_COUNTERS,
                                 name="pg0"), timeout=60)
    sch = cfg.GetSchema(pb.GetSchemaRequest(), timeout=120)
    ev = op.ListEvents(pb.ListEventsRequest(last=5), timeout=60)
    sh = op.Show(pb.ShowRequest(command="show pppoe summary"), timeout=60)
    result("old-rest", c.command == "clear interface counters pg0"
           and len(sch.modes) >= 38 and ev.events
           and "PPPoE is not configured" in sh.text,
           f"Clear, GetSchema ({len(sch.modes)} modes), ListEvents, Show")


if MODE == "old":
    run("old", old_client)
else:
    for name, fn in (("set", set_both), ("omitted", omitted),
                     ("secret-kept", secret_kept), ("every-area", every_area),
                     ("reads", reads), ("deleted", deleted)):
        run(name, fn)
