"""
lab/pppoe.sh's client for docs/bng.md 0.4 item 2 (#597): the BNG through the
management API, on the lab's real PPPoE sessions - each typed read held to
the VTY's own screen of the same command, read by `prismash -c` around it,
and the sessions cleared through the API by address, by id and by user.

Arguments: <stubs dir> <certs dir> <host:port> <prismash command> [shaper].
One RESULT line per check. With `shaper` (lab/pppoe.sh section 24, #610),
only the shaper's checks: the session's queue, what enforces each direction,
and the policer put back through SetConfig.
"""
import re
import subprocess
import sys
import time

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET, HX = sys.argv[2], sys.argv[3], sys.argv[4]
SECRETS = ("testing123", "daesecret")


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


def vty(command):
    """What the prompt prints for `command`, as an operator reads it."""
    return subprocess.run(HX.split() + ["-c", command], capture_output=True,
                          text=True, timeout=120).stdout


def vty_rows():
    """`show pppoe sessions` as {id: (user, address)}: the rows with an id."""
    out = {}
    for line in vty("show pppoe sessions").splitlines():
        w = line.split()
        if w and re.fullmatch(r"[0-9a-f]{16}", w[0]):
            out[w[0]] = (w[1], w[2])
    return out


def code_of(fn):
    """The status code a call answers with: OK, or its error's."""
    try:
        fn()
        return "OK"
    except grpc.RpcError as e:
        return e.code().name


op = rpc.OperationalServiceStub(channel("root"))
cfg = rpc.ConfigServiceStub(channel("root"))
op_nv = rpc.OperationalServiceStub(channel("netview"))
# The clears go as an administrator that is not root, as a client's
# certificate does on a box: 0.155.7 drove them as root, and prisma-bench
# refused the same Clear to an administrator ("only root may", #597).
op_adm = rpc.OperationalServiceStub(channel("labadmin"))


def listed():
    return op.ListPppoeSessions(pb.ListPppoeSessionsRequest(), timeout=60)


def wait_gone(sid, seconds=20):
    """True once no session with id `sid` is listed."""
    for _ in range(seconds):
        if sid not in {s.session_id for s in listed().sessions}:
            return True
        time.sleep(1)
    return False


def wait_user(user, old, seconds=90):
    """The id of `user`'s active session that is not `old`, or ""."""
    for _ in range(seconds):
        for s in listed().sessions:
            if s.user == user and s.state == "active" and s.session_id != old:
                return s.session_id
        time.sleep(1)
    return ""


def sessions():
    before = vty_rows()
    got = listed()
    after = vty_rows()
    api = {s.session_id: (s.user, s.address) for s in got.sessions
           if s.state == "active"}
    result("sessions-are-vty", got.running and api and api in (before, after),
           f"api {sorted(api.items())} vty {sorted(before.items())}")
    # The screen, but for the uptime column, which moves between two reads.
    lines = lambda t: [ln.split()[:3] for ln in t.splitlines()]  # noqa: E731
    result("sessions-text-is-vty",
           got.text.strip().endswith("session(s)")
           and lines(got.text) == lines(vty("show pppoe sessions")),
           f"{got.text.splitlines()[:1]}")
    ok = True
    for s in got.sessions:
        one = vty(f"show pppoe session {s.session_id}")
        mac = re.search(r"^mac\s+(\S+)", one, re.M)
        ok = ok and mac is not None and mac.group(1) == s.mac \
            and re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", s.mac) \
            and s.uptime_seconds > 0 and s.interface
    result("sessions-fields", ok and len(got.sessions) >= 3,
           " ".join(f"{s.user}/{s.address}/{s.mac}/{s.state}/{s.uptime_seconds}s/"
                    f"{s.rate_limit_down_kbit}:{s.rate_limit_up_kbit}"
                    for s in got.sessions))


def one():
    got = listed()
    s = max(got.sessions, key=lambda x: x.rate_limit_down_kbit
            + x.rate_limit_up_kbit)
    d = op.GetPppoeSession(pb.GetPppoeSessionRequest(session_id=s.session_id),
                           timeout=60)
    screen = vty(f"show pppoe session {s.session_id}")
    pols = re.findall(r"^policer (down|up)\s+(\d+) packets passed, (\d+) dropped",
                      screen, re.M)
    result("session-is-vty",
           d.session.session_id == s.session_id and d.session.mac == s.mac
           and [p.direction for p in d.policers] == [p[0] for p in pols]
           and all(p.conform_packets >= int(v[1])
                   for p, v in zip(d.policers, pols))
           and d.text.splitlines()[:5] == screen.splitlines()[:5],
           f"{s.session_id} sw_if_index {d.sw_if_index} policers "
           f"{[(p.direction, p.conform_packets, p.dropped_packets) for p in d.policers]}"
           f" vty {pols}")
    result("session-bad-id", code_of(lambda: op.GetPppoeSession(
        pb.GetPppoeSessionRequest(session_id="12"), timeout=30))
        == "INVALID_ARGUMENT", "a short id")
    result("session-unknown", code_of(lambda: op.GetPppoeSession(
        pb.GetPppoeSessionRequest(session_id="0000000000000000"), timeout=30))
        == "NOT_FOUND", "an id no session has")


def summary():
    before = vty("show pppoe summary")
    s = op.GetPppoeSummary(pb.GetPppoeSummaryRequest(), timeout=60)
    after = vty("show pppoe summary")
    act = [int(m) for m in re.findall(r"^sessions\s+(\d+) active", before + after,
                                      re.M)]
    trail = [int(m) for m in re.findall(r"^session trail\s+(\d+) entries",
                                        before + after, re.M)]
    nacc = [int(m) for m in re.findall(r"^refused\s+(\d+) from outside",
                                       before + after, re.M)]
    held = [int(m) for m in re.findall(r"^\s+(\d+) for a MAC another",
                                       before + after, re.M)]
    result("summary-is-vty",
           s.configured and s.control_plane == "running"
           and act and min(act) <= s.sessions_active <= max(act)
           and s.trail_known and trail and min(trail) <= s.trail_written <= max(trail)
           and s.refusals_known and nacc and min(nacc) <= s.refused_not_access <= max(nacc)
           and held and min(held) <= s.refused_mac_held <= max(held),
           f"active {s.sessions_active} (vty {act}) trail {s.trail_written}/"
           f"{s.trail_failed}/seq {s.trail_seq} (vty {trail}) refused "
           f"{s.refused_not_access}/{s.refused_mac_held} (vty {nacc}/{held})")
    result("summary-radius",
           s.radius and [(r.address, r.priority) for r in s.radius_servers]
           == [("127.0.0.1", 1), ("127.0.0.2", 2)]
           and all(r.state in ("active", "failed") for r in s.radius_servers)
           and s.accounting and s.dynamic_authorization_listen == "127.0.0.1"
           and s.dynamic_authorization_clients == 1
           and list(s.access_interfaces) == ["host-acc0", "host-acc0.100"],
           f"{s.radius_mode} {[(r.address, r.priority, r.state) for r in s.radius_servers]}")
    result("summary-text-is-vty",
           s.text.splitlines()[0] == before.splitlines()[0]
           and len(s.text.splitlines()) == len(before.splitlines()),
           s.text.splitlines()[0])


def history():
    user = listed().sessions[0].user
    vt = vty(f"show pppoe history user {user} last 3")
    h = op.ListPppoeHistory(pb.ListPppoeHistoryRequest(user=user, last=3),
                            timeout=60)
    ok = h.text == vt and 1 <= len(h.sessions) <= 3
    for x in h.sessions:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(x.start.seconds)) \
            + f".{x.start.nanos // 1000000:03d}Z"
        ok = ok and x.user == user and f"session {x.session_id}" in vt \
            and f"from {stamp}" in vt
        ok = ok and (x.held or x.stop.seconds >= x.start.seconds)
    result("history-is-vty", ok,
           f"{user}: {[(x.session_id, x.address, x.held, x.cause) for x in h.sessions]}")
    result("history-refused", code_of(lambda: op.ListPppoeHistory(
        pb.ListPppoeHistoryRequest(address="not-an-address"), timeout=30))
        == "INVALID_ARGUMENT", "an address that is not one, in the VTY's words")


def verify():
    for _ in range(3):
        vt = vty("show pppoe history verify")
        v = op.VerifyPppoeHistory(pb.VerifyPppoeHistoryRequest(), timeout=60)
        if v.text == vt:
            break
    head = re.search(r"head\s+seq (\d+)", vt)
    result("verify-is-vty",
           v.text == vt and v.verdict == pb.PppoeHistoryVerification.VERDICT_INTACT
           and head and v.head_seq == int(head.group(1)) and len(v.head_sha256) == 64
           and v.checked > 0,
           f"{pb.PppoeHistoryVerification.Verdict.Name(v.verdict)} {v.checked} "
           f"checked, head {v.head_seq}")


def config():
    t = cfg.GetConfig(pb.GetConfigRequest(areas=["pppoe", "aaa"]), timeout=60)
    raw = t.SerializeToString()
    r = t.aaa.radius
    result("config-read",
           t.pppoe.configured and list(t.pppoe.interfaces)
           == ["host-acc0", "host-acc0.100"] and t.pppoe.gateway == "100.64.0.1"
           # The resolvers as the VTY prints them: section 10 took one out.
           and list(t.pppoe.dns_servers) == re.findall(
               r"^ dns (\S+)$", vty("show running-config"), re.M)
           and t.pppoe.tcp_mss == 1452 and t.aaa.configured
           # #610: the block says `rate-limit download policer` here
           and t.pppoe.rate_limit_download == "policer"
           and t.pppoe.rate_limit_upload == ""
           and [(s.address, s.priority, s.secret_set, s.secret) for s in r.servers]
           == [("127.0.0.1", 1, True, ""), ("127.0.0.2", 2, True, "")]
           and r.HasField("dead_time_seconds") and r.dead_time_seconds == 10
           and not r.HasField("timeout_seconds")
           and [(c.address, c.secret_set) for c in r.dynamic_authorization_clients]
           == [("127.0.0.1", True)],
           f"pppoe {list(t.pppoe.interfaces)} radius "
           f"{[(s.address, s.priority) for s in r.servers]} - read: "
           + str(t.pppoe).replace("\n", " ") + " / "
           + str(t.aaa).replace("\n", " "))
    result("config-no-secret", not any(x.encode() in raw for x in SECRETS),
           f"{len(raw)} bytes, none of {SECRETS}")


def ipv6():
    """
    IPv6 on the sessions (#609), typed: sub7's WAN and delegated prefixes in
    ListPppoeSessions as `show pppoe sessions` prints them under it; the
    pools in GetConfig and GetPppoeSummary as configured; and who held an
    address of the delegated prefix, from ListPppoeHistory.
    """
    import ipaddress
    got = listed()
    s7 = [s for s in got.sessions if s.user == "sub7" and s.state == "active"]
    screen = vty("show pppoe sessions").splitlines()
    under = []
    if s7:
        at = [i for i, ln in enumerate(screen) if ln.startswith(s7[0].session_id)]
        under = [ln.strip() for ln in screen[at[0] + 1:at[0] + 3]] if at else []
    ok = bool(s7) and s7[0].ipv6_prefix.startswith("2001:db8:100:") \
        and s7[0].ipv6_prefix.endswith("/64") \
        and ipaddress.IPv6Network(s7[0].ipv6_delegated_prefix or "::/128") \
        .subnet_of(ipaddress.IPv6Network("2001:db8:200::/40")) \
        and under == [f"ipv6 {s7[0].ipv6_prefix}",
                      f"delegated {s7[0].ipv6_delegated_prefix}"]
    result("sessions-ipv6", ok,
           f"{[(s.user, s.ipv6_prefix, s.ipv6_delegated_prefix) for s in got.sessions]}"
           f" vty {under}")
    c = cfg.GetConfig(pb.GetConfigRequest(areas=["pppoe"]), timeout=60).pppoe
    sm = op.GetPppoeSummary(pb.GetPppoeSummaryRequest(), timeout=60)
    pools = [(d.prefix, d.length) for d in c.ipv6_delegated_pools]
    result("config-ipv6",
           list(c.ipv6_prefix_pools) == ["2001:db8:100::/48"]
           and pools == [("2001:db8:200::/40", 56)]
           and list(c.ipv6_dns_servers) == ["2001:4860:4860::8888"]
           and not c.ipv6_enable and sm.ipv6
           and list(sm.ipv6_prefix_pools) == ["2001:db8:100::/48"]
           and [(d.prefix, d.length) for d in sm.ipv6_delegated_pools] == pools,
           f"config {list(c.ipv6_prefix_pools)} {pools} summary {sm.ipv6}")
    if not s7 or not s7[0].ipv6_delegated_prefix:
        result("history-ipv6", False, "sub7 has no delegated prefix")
        return
    addr = str(ipaddress.IPv6Network(s7[0].ipv6_delegated_prefix)[1])
    h = op.ListPppoeHistory(pb.ListPppoeHistoryRequest(address=addr, at="0m"),
                            timeout=60)
    vt = vty(f"show pppoe history address {addr} at 0m")
    result("history-ipv6",
           [x.user for x in h.sessions] == ["sub7"]
           and h.sessions[0].ipv6_delegated_prefix == s7[0].ipv6_delegated_prefix
           and h.sessions[0].HasField("delegated")
           and h.sessions[0].delegated.seconds >= h.sessions[0].start.seconds
           and "sub7 held" in vt,
           f"{addr}: {[(x.user, x.ipv6_prefix, x.ipv6_delegated_prefix) for x in h.sessions]}")


def clears():
    got = listed().sessions
    by_user = {s.user: s for s in got if s.state == "active"}
    # By address: the session `show pppoe sessions` lists with it.
    a = by_user.get("sub3")
    r = op_adm.Clear(pb.ClearRequest(target=pb.ClearRequest.TARGET_PPPOE_ADDRESS,
                                 name=a.address), timeout=60)
    gone = wait_gone(a.session_id)
    back = wait_user("sub3", a.session_id)
    result("clear-address", gone and back and r.command
           == f"clear pppoe session address {a.address}"
           and f"cleared: sid {a.session_id}" in r.output,
           f"{a.address} held by {a.session_id}: gone {gone}, sub3 again {back}")
    h = op.ListPppoeHistory(pb.ListPppoeHistoryRequest(session_id=a.session_id),
                            timeout=60)
    result("clear-address-history",
           len(h.sessions) == 1 and not h.sessions[0].held
           and h.sessions[0].address == a.address and h.sessions[0].cause,
           f"{[(x.session_id, x.held, x.cause) for x in h.sessions]}")
    # By id.
    b = by_user.get("sub2")
    op_adm.Clear(pb.ClearRequest(target=pb.ClearRequest.TARGET_PPPOE_SESSION,
                             name=b.session_id), timeout=60)
    gone = wait_gone(b.session_id)
    back = wait_user("sub2", b.session_id)
    result("clear-session", gone and back,
           f"{b.session_id}: gone {gone}, sub2 again {back}")
    # By user.
    c = by_user.get("sub5")
    op_adm.Clear(pb.ClearRequest(target=pb.ClearRequest.TARGET_PPPOE_USER,
                             name="sub5"), timeout=60)
    gone = wait_gone(c.session_id)
    result("clear-user", gone, f"sub5's {c.session_id}: gone {gone}")
    # An address no session holds: refused, nothing cleared.
    n = len(listed().sessions)
    code = code_of(lambda: op_adm.Clear(pb.ClearRequest(
        target=pb.ClearRequest.TARGET_PPPOE_ADDRESS, name="100.64.3.3"),
        timeout=60))
    result("clear-address-nobody", code == "FAILED_PRECONDITION"
           and len(listed().sessions) >= n - 1, code)
    # An account without the role, as at the prompt.
    s = listed().sessions[0]
    code = code_of(lambda: op_nv.Clear(pb.ClearRequest(
        target=pb.ClearRequest.TARGET_PPPOE_SESSION, name=s.session_id),
        timeout=60))
    result("clear-no-role", code == "PERMISSION_DENIED"
           and s.session_id in {x.session_id for x in listed().sessions}, code)
    reads = op_nv.ListPppoeSessions(pb.ListPppoeSessionsRequest(), timeout=60)
    result("reads-any-account", reads.running and reads.sessions,
           f"{len(reads.sessions)} session(s) for netview")


def commit():
    """
    A commit through the API as an administrator that is not root (#597):
    on 0.155.9 prisma-bench refused one - the PPPoE files' directory was
    root's 2750 and the container's doors root's alone - while every commit
    this lab made went as root. An `aaa` change: it writes accel-ppp.conf
    again and opens the reload door, and restarts nothing - a `pppoe` one
    would add two restarts to the ones the lab counts, and past four in five
    minutes the watcher holds them in a summary (eventwatch.flap_admit).
    """
    cfg_adm = rpc.ConfigServiceStub(channel("labadmin"))
    radius = cfg.GetConfig(pb.GetConfigRequest(areas=["aaa"]),
                           timeout=60).aaa.radius
    before = radius.timeout_seconds if radius.HasField("timeout_seconds") \
        else None
    want = 7 if before != 7 else 8
    c = cfg_adm.OpenCandidate(pb.OpenCandidateRequest(), timeout=60)
    cfg_adm.SetConfig(pb.SetConfigRequest(
        candidate_id=c.candidate_id,
        aaa=pb.AaaConfig(radius=pb.RadiusConfig(timeout_seconds=want)),
        fields=["radius.timeout_seconds"]), timeout=60)
    try:
        cfg_adm.Commit(pb.CommitRequest(
            candidate_id=c.candidate_id,
            comment="lab: an administrator's commit"), timeout=300)
        code = "OK"
    except grpc.RpcError as e:
        code = f"{e.code().name}: {' '.join((e.details() or '').split())[:400]}"
    shown = vty("show pppoe summary")
    conf = open("/etc/prisma-routing/bng/accel-ppp.conf").read()
    result("commit-as-administrator", code == "OK"
           and f"timeout {want} s" in shown and f"timeout={want}" in conf,
           f"commit {code}, timeout {want} (was {before}): "
           f"{[l for l in shown.splitlines() if 'timeout' in l]}")
    c = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60)
    back = pb.RadiusConfig()
    if before is not None:
        back.timeout_seconds = before
    cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=c.candidate_id, aaa=pb.AaaConfig(radius=back),
        fields=["radius.timeout_seconds"]), timeout=60)
    cfg.Commit(pb.CommitRequest(candidate_id=c.candidate_id,
                                comment="lab: the timeout back"), timeout=300)

def shaper():
    """
    The download shaped by default (#610): GetPppoeSession's shapers are
    `show pppoe session`'s rows and its policers the upload's alone;
    GetPppoeSummary and GetConfig say what enforces each direction; and a
    SetConfig of rate_limit_download "policer", committed, puts the
    download's policer back on the session that redials.
    """
    s = next(x for x in listed().sessions if x.user == "sub7")
    d = op.GetPppoeSession(pb.GetPppoeSessionRequest(session_id=s.session_id),
                           timeout=60)
    screen = vty(f"show pppoe session {s.session_id}")
    head = re.search(r"^shaper down\s+(\S+), a queue of (\d+) ms", screen,
                     re.M)
    seen = re.search(r"^\s+passed at once (\d+), delayed (\d+)", screen, re.M)
    down = [x for x in d.shapers if x.direction == "down"]
    result("session-shapers",
           head and seen and len(down) == 1 and down[0].rate_kbit == 20000
           and down[0].queue_ms == int(head.group(2))
           and down[0].delayed_packets >= int(seen.group(2))
           and [p.direction for p in d.policers] == ["up"]
           and not [x for x in d.shapers if x.direction == "up"],
           f"{[(x.direction, x.rate_kbit, x.queue_ms, x.passed_packets, x.delayed_packets, x.dropped_codel_packets) for x in d.shapers]}"
           f" policers {[p.direction for p in d.policers]} vty "
           f"{head.groups() if head else None} {seen.groups() if seen else None}")
    summ = op.GetPppoeSummary(pb.GetPppoeSummaryRequest(), timeout=60)
    result("summary-limiters",
           summ.rate_limit_download == "shaper"
           and summ.rate_limit_upload == "policer"
           and "download by a shaper, upload by a policer"
           in vty("show pppoe summary"),
           f"{summ.rate_limit_download}/{summ.rate_limit_upload}")
    t = cfg.GetConfig(pb.GetConfigRequest(areas=["pppoe"]), timeout=60)
    result("config-limiters-default",
           t.pppoe.rate_limit_download == "" and t.pppoe.rate_limit_upload == "",
           f"{t.pppoe.rate_limit_download!r}/{t.pppoe.rate_limit_upload!r}")
    c = cfg.OpenCandidate(pb.OpenCandidateRequest(), timeout=60)
    cfg.SetConfig(pb.SetConfigRequest(
        candidate_id=c.candidate_id,
        pppoe=pb.PppoeConfig(rate_limit_download="policer"),
        fields=["rate_limit_download"]), timeout=60)
    cfg.Commit(pb.CommitRequest(candidate_id=c.candidate_id,
                                comment="lab: the download policed again"),
               timeout=300)
    sid = wait_user("sub7", s.session_id)
    after = op.GetPppoeSession(pb.GetPppoeSessionRequest(session_id=sid),
                               timeout=60) if sid else None
    run_cfg = vty("show running-config")
    result("switch-to-policer",
           after is not None and not after.shapers
           and sorted(p.direction for p in after.policers) == ["down", "up"]
           and " rate-limit download policer" in run_cfg
           and cfg.GetConfig(pb.GetConfigRequest(areas=["pppoe"]),
                             timeout=60).pppoe.rate_limit_download == "policer",
           f"sub7 again {sid or 'NOT'}: shapers "
           f"{[x.direction for x in after.shapers] if after else None} policers "
           f"{[p.direction for p in after.policers] if after else None}")


if sys.argv[5:] == ["shaper"]:
    run("shaper", shaper)
    sys.exit(0)

for name, fn in (("sessions", sessions), ("session", one), ("summary", summary),
                 ("history", history), ("verify", verify), ("config", config),
                 ("ipv6", ipv6), ("clears", clears), ("commit", commit)):
    run(name, fn)
