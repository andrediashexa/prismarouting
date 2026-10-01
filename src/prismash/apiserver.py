#!/usr/bin/python3
"""
The management API's server (#532): gRPC, prisma.v1, in the management
container.

WHAT IT IS NOT: a second configuration system. Every configuration call is
lines fed to the VTY's own `Shell` - `configure`, the lines an operator
types, `commit check`, `commit`, `rollback`, `save` - and what comes back is
what the VTY printed and refused, in its words. The typed operational data is
read by the same readers the VTY's `show` commands use.

EACH CALL RUNS AS THE ACCOUNT IT ACTS FOR. The account is the Common Name of
the client certificate the mutual-TLS handshake presented, and its work runs
in a process of its own that has become that account (initgroups, setgid,
setuid) before it creates the Shell: so its roles are the ones the VTY login
gives it (capabilities() reads the process's own groups), the host's doors see
its uid as they see a VTY session's, and the configuration trail records it as
the author. The server itself runs as root only to do that, and to read the
statistics segment for the telemetry stream.

A CANDIDATE IS A SESSION: OpenCandidate starts that account's process with
`configure`, and every call naming the candidate goes to the same process,
which holds it as the VTY holds one between two lines. A commit whose running
configuration has moved since the candidate was opened is refused with the
difference, rather than applied over a change somebody else made.

The core below (parsing the VTY's output, the sessions, the data readers)
imports no gRPC and is what test/test_apiserver.py drives; the servicers at
the end convert it to and from prisma.v1's messages and are driven by
lab/api-grpc.sh.
"""
import contextlib
import difflib
import hashlib
import io
import json
import multiprocessing
import os
import re
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

#: Where the server listens inside the management container: the backplane
#: address the host forwards the management address to (#448's bp0).
DEFAULT_LISTEN = "128.0.0.2:50051"
#: The certificates, under the directory the container shares with the host.
API_DIR = os.environ.get("PRISMA_API_DIR", "/etc/prisma-routing/api")
#: A candidate nobody has touched for this long is closed.
CANDIDATE_IDLE = 1800
#: An account's process for operational calls is kept this long between uses.
WORKER_IDLE = 300
#: The commands Show takes: reads, never a change and never a shell. Not
#: `monitor`: the VTY refuses `monitor interface` without a terminal, and a
#: call has none - the telemetry stream is the API's live view.
SHOW_WORDS = ("show", "ping", "traceroute", "mtr")
#: The VTY's sentence for an account without the role (Shell.require).
DENIED = "permission denied for a"

DIAG = re.compile(r"^(error|warning|note): ?(.*)$")


# ------------------------------------------------------------ the VTY's text

def parse_output(text):
    """
    The diagnostics in what the VTY printed: one per `error:`, `warning:` or
    `note:` line, with the indented lines under it - a hierarchy path written
    "nat / cgnat / pool X" becomes `path`, a configuration line `line`, and
    the rest continues the message.
    """
    out = []
    for raw in text.splitlines():
        m = DIAG.match(raw)
        if m:
            out.append({"severity": m.group(1), "message": m.group(2).strip(),
                        "path": [], "line": ""})
            continue
        if not out or not raw.startswith(" ") or not raw.strip():
            continue
        s = raw.strip()
        cur = out[-1]
        if " / " in s and not s.endswith(".") and len(s) < 120 \
                and all(p.strip() for p in s.split(" / ")):
            cur["path"] = [p.strip() for p in s.split(" / ")]
        elif s.startswith("line ") and ":" in s:
            cur["line"] = s.split(":", 1)[1].strip()
        else:
            cur["message"] = (cur["message"] + " " + s).strip()
    return out


def denied(text):
    """Did the VTY refuse this for the account's role?"""
    return DENIED in text


def revision_of(text):
    """A revision id for a configuration's text: equal texts, equal ids."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def config_diff(base, now):
    """What changed between two configuration texts, as a unified diff."""
    return "".join(difflib.unified_diff(
        base.splitlines(True), now.splitlines(True),
        "running when the candidate was opened", "running now"))


def edit_lines(path, lines):
    """The lines that apply `lines` at hierarchy `path`, from the top back to it."""
    return ["top"] + list(path) + list(lines) + ["top"]


def commit_line(comment="", confirmed_minutes=0, remap=False, cutoff=False,
                diverge=False):
    """The `commit` an API Commit is, in the order the VTY reads its words."""
    words = ["commit"]
    words += [w for w, on in (("remap", remap), ("cutoff", cutoff),
                              ("diverge", diverge)) if on]
    if confirmed_minutes:
        words += ["confirmed", str(int(confirmed_minutes))]
    if comment:
        c = comment.replace('"', "'")
        # A comment saying `confirmed` is the VTY's one quoted case.
        words += ["comment", f'"{c}"' if "confirmed" in c.split() else c]
    return " ".join(words)


def show_allowed(command):
    """A Show command the API runs: an operational read, never a change."""
    w = command.split()
    return bool(w) and w[0] in SHOW_WORDS and "|" not in command.replace(
        "| include", "").replace("| exclude", "").replace("| count", "")


def account_from_auth(auth):
    """The account a call acts as: the client certificate's Common Name."""
    names = (auth or {}).get("x509_common_name") or []
    if len(names) != 1:
        return None
    name = names[0].decode() if isinstance(names[0], bytes) else names[0]
    return name if re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$", name) else None


# ---------------------------------------------------- what a worker computes

def feed_lines(sh, lines):
    """
    `lines` fed to Shell `sh` one at a time, stopping at the first the VTY
    refuses: {"output", "failed", "at"} - `at` the index of the refused line,
    or -1.
    """
    buf = io.StringIO()
    at = -1
    for i, line in enumerate(lines):
        before = sh.failed
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            sh.feed(line)
        if sh.failed > before:
            at = i
            break
    return {"output": buf.getvalue(), "failed": at >= 0, "at": at}


def screen(sh, command):
    """What the VTY prints for an operational `command`, as text."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        sh.feed(command)
    return buf.getvalue()


def parse_version(text):
    """VersionInfo's fields out of `show version`'s screen."""
    out = {"release": "", "plugin_build": "", "vpp_version": "",
           "frr_version": "", "api_transport": ""}
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^Prisma Routing (\S+)", s)
        if m:
            out["release"] = m.group(1)
        elif s.startswith("plugin:"):
            out["plugin_build"] = s.split(":", 1)[1].strip()
        elif s.startswith("dataplane:"):
            m = re.search(r"vpp (v\S+)", s)
            out["vpp_version"] = m.group(1) if m else s.split(":", 1)[1].strip()
        elif s.startswith("routing:"):
            m = re.search(r"FRRouting (\S+)", s)
            out["frr_version"] = m.group(1) if m else ""
        elif s.startswith("api:"):
            out["api_transport"] = "vpp_papi" if "vpp_papi" in s else "vat2"
    out["dataplane_current"] = bool(out["release"]) and \
        out["plugin_build"].split(" ")[0] == out["release"]
    return out


def parse_cpu(text):
    """The `this boot` half of `show system cpu`."""
    out = {"control_plane_cpus": "", "main_thread_cpu": "", "worker_cpus": ""}
    boot = text.split("this boot:", 1)[1] if "this boot:" in text else ""
    for line in boot.splitlines():
        s = line.strip()
        if s.startswith("VPP main thread"):
            out["main_thread_cpu"] = s.split()[-1]
        elif s.startswith("VPP workers"):
            out["worker_cpus"] = s.split()[-1]
        elif s.startswith("control plane held to"):
            out["control_plane_cpus"] = s.split()[-1]
    out["workers"] = cpu_count(out["worker_cpus"])
    return out


def cpu_count(spec):
    """How many CPUs a list like '3-7,9' names."""
    from system import is_number
    n = 0
    for part in filter(None, (spec or "").split(",")):
        a, _, b = part.partition("-")
        if is_number(a) and (not b or is_number(b)):
            n += int(b) - int(a) + 1 if b else 1
    return n


def interfaces_of(sh, names=()):
    """
    Interface rows: the dataplane's own (sw_interface_dump, as the VTY's
    readers ask it), the running configuration's addresses, MTU and
    description, and the Linux end of each pair.
    """
    cfg = sh.running_config()
    rows = []
    r = sh.api.dump("sw_interface_dump", {"sw_if_index": 0xFFFFFFFF,
                                          "name_filter_valid": False,
                                          "name_filter": ""})
    linux = {}
    try:
        linux = sh.device_linux_names()
    except Exception:  # noqa: BLE001 - a box with no linux_cp has no pairs
        linux = {}
    for d in (r.fields or {}).get("rows", []) if getattr(r, "ok", False) else []:
        name = d.get("interface_name", "")
        # The VTY's rule for what is an interface (#532): not local0, and not
        # the Linux end of a linux_cp pair - `show interface` lists neither,
        # and the X520 bench's ListInterfaces returned tap4096 beside it.
        if not name or name == "local0" or is_lcp_tap(name) \
                or (names and name not in names):
            continue
        flags = str(d.get("flags", ""))
        rows.append({
            "name": name,
            "admin_up": "ADMIN_UP" in flags,
            "link_up": "LINK_UP" in flags,
            "mtu": int((d.get("mtu") or [0])[0]) if isinstance(d.get("mtu"), list)
                   else int(d.get("link_mtu", 0) or 0),
            "mac_address": d.get("l2_address", ""),
            "addresses": list(cfg_addresses(cfg, name)),
            "linux_name": linux.get(name, ""),
            "description": getattr(cfg, "descriptions", {}).get(name, ""),
            "sw_if_index": d.get("sw_if_index", 0),
        })
    return rows


def is_lcp_tap(name):
    """The Linux end of a linux_cp pair, by the VTY's own predicate."""
    from config import _is_lcp_tap
    return _is_lcp_tap(name)


def cfg_addresses(cfg, name):
    """The addresses the running configuration gives interface `name`."""
    return list((getattr(cfg, "addresses", {}) or {}).get(name, []) or [])


def pools_of(sh):
    """Pool rows: the running configuration's pools, with the dataplane's capacity."""
    cfg = sh.running_config()
    dp = {}
    r = sh.api.dump("bpa_pool_dump", {})
    for d in (r.fields or {}).get("rows", []) if getattr(r, "ok", False) else []:
        dp[d.get("name", "")] = d
    out = []
    for name in sorted(cfg.pools):
        p = cfg.pools[name]
        d = dp.get(name, {})
        out.append({"name": name, "mode": p.mode, "policy": p.policy,
                    "block_size": int(p.block_size or 0),
                    "prefixes_inside": list(p.inside),
                    "prefixes_outside": list(p.outside),
                    "capacity": int(d.get("capacity", 0) or 0),
                    "guaranteed": int(d.get("guaranteed", 0) or 0)})
    return out


def frr_json(sh, command):
    """An FRR `... json` screen, parsed; {} when FRR does not answer."""
    rc, out = sh.frr.show(command)
    if rc != 0:
        return {}
    try:
        return json.loads(out or "{}")
    except ValueError:
        return {}


def bgp_of(sh):
    """BGP neighbours, every VRF, from FRR's own JSON."""
    out = []
    data = frr_json(sh, "show bgp vrf all summary json")
    for vrf, families in (data or {}).items():
        seen = set()
        for fam in (families or {}).values():
            for peer, p in ((fam or {}).get("peers") or {}).items():
                if peer in seen:
                    continue
                seen.add(peer)
                out.append({"vrf": vrf, "peer": peer,
                            "remote_as": int(p.get("remoteAs", 0) or 0),
                            "state": p.get("state", ""),
                            "uptime_seconds": int(p.get("peerUptimeMsec", 0) or 0) // 1000,
                            "prefixes_received": int(p.get("pfxRcd", 0) or 0),
                            "description": p.get("desc", "")})
    return out


def ospf_of(sh):
    """OSPF and OSPFv3 neighbours from FRR's JSON."""
    out = []
    v2 = frr_json(sh, "show ip ospf neighbor json").get("neighbors", {})
    for rid, entries in (v2 or {}).items():
        for e in entries or []:
            out.append({"protocol": "ospf", "router_id": rid,
                        "address": e.get("ifaceAddress", e.get("address", "")),
                        "interface": e.get("ifaceName", ""),
                        "state": e.get("nbrState", e.get("state", ""))})
    v3 = frr_json(sh, "show ipv6 ospf6 neighbor json").get("neighbors", [])
    for e in v3 or []:
        out.append({"protocol": "ospf6", "router_id": e.get("neighborId", ""),
                    "address": "", "interface": e.get("interfaceName", ""),
                    "state": e.get("state", "")})
    return out


def isis_of(sh):
    """IS-IS adjacencies from FRR's JSON."""
    out = []
    data = frr_json(sh, "show isis neighbor json")
    for area in (data.get("areas") or []):
        for c in area.get("circuits") or []:
            adj = c.get("adj") or c.get("adjacency")
            if not adj:
                continue
            out.append({"system_id": adj if isinstance(adj, str) else str(adj),
                        "interface": (c.get("interface") or {}).get("name", "")
                        if isinstance(c.get("interface"), dict) else c.get("interface", ""),
                        "state": c.get("state", ""), "level": str(c.get("level", ""))})
    return out


def vrrp_of(sh):
    """VRRP routers, as the VTY's device_vrrp_state() reads them."""
    out = []
    for r in sh.device_vrrp_state() or []:
        addrs = [str(a) for a in r.get("addresses", [])]
        out.append({"interface": r.get("interface", ""),
                    "vr_id": int(r.get("vr_id", 0) or 0),
                    "family": 6 if any(":" in a for a in addrs) else 4,
                    "state": str(r.get("state", "")),
                    "priority": int(r.get("advertising", 0) or 0),
                    "addresses": addrs})
    return out


def bundles_of(sh):
    """LACP and other bundles, from the dataplane's bond dumps."""
    out = []
    r = sh.api.dump("sw_bond_interface_dump", {"sw_if_index": 0xFFFFFFFF})
    for d in (r.fields or {}).get("rows", []) if getattr(r, "ok", False) else []:
        idx = d.get("sw_if_index")
        m = sh.api.dump("sw_member_interface_dump", {"sw_if_index": idx})
        members = (m.fields or {}).get("rows", []) if getattr(m, "ok", False) else []
        out.append({"name": d.get("interface_name", ""),
                    "mode": str(d.get("mode", "")).replace("BOND_API_MODE_", "").lower(),
                    "members": [x.get("interface_name", "") for x in members],
                    "active_members": [x.get("interface_name", "") for x in members
                                       if x.get("is_active", x.get("active"))]})
    return out


def subscriber_blocks(text):
    """The `address port-port` blocks in `show cgnat subscriber`'s screen."""
    out = []
    for m in re.finditer(r"(\d+\.\d+\.\d+\.\d+)\s*[: ]\s*(\d+)\s*-\s*(\d+)", text):
        out.append({"public_address": m.group(1), "first_port": int(m.group(2)),
                    "last_port": int(m.group(3))})
    return out


def uptime_fields(facts, ts):
    """
    GetSystem's uptime fields (#534) from Shell.uptime_facts(): a Timestamp
    built by `ts` for each clock that was read, none for one that was not, the
    boot's recorded reason, and whether the dataplane restarted after the boot.
    """
    out = {}
    for key, field in (("boot", "boot_time"), ("dataplane", "dataplane_started"),
                       ("control_plane", "control_plane_started"),
                       ("vty", "vty_started"),
                       ("release_installed", "release_installed_at")):
        if facts.get(key) is not None:
            out[field] = ts(facts[key])
    out["last_boot_reason"] = facts.get("boot_reason") or "not recorded"
    out["dataplane_restarted"] = bool(facts.get("dataplane_restarted"))
    return out


#: The operational readers a worker runs, by name.
READERS = {
    "interfaces": lambda sh, a: interfaces_of(sh, a or ()),
    "pools": lambda sh, a: pools_of(sh),
    "bgp": lambda sh, a: bgp_of(sh),
    "ospf": lambda sh, a: ospf_of(sh),
    "isis": lambda sh, a: isis_of(sh),
    "vrrp": lambda sh, a: vrrp_of(sh),
    "bundles": lambda sh, a: bundles_of(sh),
    # The host's name as the prompt reads it (#516): one question to the
    # host's door. `show running-config | include ^hostname`, which this used
    # to ask, answered nothing on the X520 bench (0.148.4), and the whole
    # running configuration is too dear for a call clients poll every 5 s.
    "hostname": lambda sh, a: sh.sys.read_hostname(),
    # The clocks `show system uptime` reads (#534), from the same Shell.
    "uptime": lambda sh, a: sh.uptime_facts(),
}


def handle(sh, op, args):
    """One request to a worker's Shell `sh`; what goes back over the pipe."""
    if op == "feed":
        return feed_lines(sh, args)
    if op == "candidate":
        return sh.candidate.text() if sh.candidate is not None else None
    if op == "running":
        return sh.running_config().text()
    if op == "gates":
        return {"remap": list(sh.remap), "cutoff": list(sh.cutoff),
                "diverge": list(sh.diverge)}
    if op == "revisions":
        return sh.rb.list()
    if op == "confirm":
        return sh.confirm.read()
    if op == "screen":
        return screen(sh, args)
    if op in READERS:
        return READERS[op](sh, args)
    raise ValueError(f"unknown worker operation {op!r}")


# -------------------------------------------------------- the account's process

def become(account):
    """Turn this process into `account`: its groups, its gid, its uid, its env."""
    import pwd
    pw = pwd.getpwnam(account)
    if os.geteuid() == 0:
        os.initgroups(account, pw.pw_gid)
        os.setgid(pw.pw_gid)
        os.setuid(pw.pw_uid)
    os.environ.update({"HOME": pw.pw_dir, "USER": account, "LOGNAME": account})


# not a unit: the body of a forked worker process - it becomes the account,
# builds the Shell and answers its pipe until told to stop; handle() above is
# what a test drives, and lab/api-grpc.sh drives this.
def _worker_main(conn, account, shell_factory):
    # NOT THE SERVER'S STDIN. Under the host unit it is the pipe whose end
    # stops the server (--stop-on-eof), and a Shell built on it waited on it:
    # lab/api-grpc.sh measured every session refused "did not start" the day
    # the server's stdin stopped being /dev/null.
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    sys.stdin = open(os.devnull)
    try:
        become(account)
        if shell_factory is None:
            import prismash
            sh = prismash.Shell()
        else:
            sh = shell_factory()
    except Exception as e:  # noqa: BLE001 - said to the parent, not raised
        conn.send(("dead", f"{type(e).__name__}: {e}"))
        return
    conn.send(("ready", None))
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            return
        if msg is None:
            return
        op, args = msg
        try:
            conn.send(("ok", handle(sh, op, args)))
        except Exception as e:  # noqa: BLE001 - the request fails, not the worker
            conn.send(("err", f"{type(e).__name__}: {e}"))


class WorkerError(Exception):
    """A worker that could not start or could not answer."""


class Worker:
    """One account's process: a Shell held between calls, one request at a time."""

    def __init__(self, account, shell_factory=None, timeout=300):
        self.account = account
        self.timeout = timeout
        self.lock = threading.Lock()
        self.used = time.monotonic()
        ctx = multiprocessing.get_context("fork")
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=_worker_main,
                                args=(child, account, shell_factory), daemon=True)
        self.proc.start()
        child.close()
        if not self.conn.poll(60):
            self.close()
            raise WorkerError(f"{account}: the session did not start")
        state, why = self.conn.recv()
        if state != "ready":
            self.close()
            raise WorkerError(f"{account}: {why}")

    def ask(self, op, args=None):
        """`op` answered by the account's Shell; raises WorkerError when it cannot be."""
        with self.lock:
            self.used = time.monotonic()
            try:
                self.conn.send((op, args))
                if not self.conn.poll(self.timeout):
                    raise WorkerError(f"{op}: no answer in {self.timeout}s")
                state, value = self.conn.recv()
            except (EOFError, OSError, BrokenPipeError) as e:
                raise WorkerError(f"{op}: the session ended ({e})")
        if state != "ok":
            raise WorkerError(value)
        return value

    def close(self):
        """Stop the process."""
        try:
            self.conn.send(None)
        except (OSError, BrokenPipeError):
            pass
        self.proc.join(5)
        if self.proc.is_alive():
            self.proc.kill()


class Sessions:
    """
    The candidates open over the API, each in its account's own process, and
    one process per account for operational calls.
    """

    def __init__(self, worker=Worker, idle=CANDIDATE_IDLE, ops_idle=WORKER_IDLE):
        self.worker = worker
        self.idle = idle
        self.ops_idle = ops_idle
        self.candidates = {}   # id -> {"account", "worker", "base"}
        self.ops = {}          # account -> Worker
        # The console's own process per account (Show): a ping or a traceroute
        # holds its process for as long as it runs, and in the operational
        # one it held every GetVersion behind it - measured from the code for
        # prisma-web, whose status monitor polls every 5 s with a 4 s limit.
        self.consoles = {}     # account -> Worker
        self.lock = threading.Lock()

    def open(self, account, from_revision=None):
        """
        A new candidate for `account`: (id, base running text, candidate text,
        the VTY's output). Raises PermissionError when the VTY refuses the
        account `configure`, ValueError when it refuses the revision.
        """
        w = self.worker(account)
        try:
            base = w.ask("running")
            lines = ["configure"]
            if from_revision is not None:
                lines.append(f"rollback {int(from_revision)}")
            res = w.ask("feed", lines)
            if res["failed"]:
                if denied(res["output"]):
                    raise PermissionError(res["output"])
                raise ValueError(res["output"])
            text = w.ask("candidate") or ""
        except Exception:
            w.close()
            raise
        cid = uuid.uuid4().hex
        with self.lock:
            self.candidates[cid] = {"account": account, "worker": w, "base": base}
        return cid, base, text, res["output"]

    def get(self, account, cid):
        """The candidate `cid` of `account`; KeyError for anybody else's or none."""
        self.reap()
        with self.lock:
            c = self.candidates.get(cid)
        if c is None or c["account"] != account:
            raise KeyError(cid)
        return c

    def close(self, account, cid):
        """Discard a candidate."""
        c = self.get(account, cid)
        with self.lock:
            self.candidates.pop(cid, None)
        c["worker"].close()

    def ops_worker(self, account):
        """The account's process for operational calls, started on first use."""
        self.reap()
        with self.lock:
            w = self.ops.get(account)
        if w is None or not w.proc.is_alive():
            w = self.worker(account)
            with self.lock:
                self.ops[account] = w
        return w

    def console_worker(self, account):
        """The account's process for Show, apart from its operational one."""
        self.reap()
        with self.lock:
            w = self.consoles.get(account)
        if w is None or not w.proc.is_alive():
            w = self.worker(account)
            with self.lock:
                self.consoles[account] = w
        return w

    def reap(self):
        """Close candidates and operational processes nobody has used lately."""
        now = time.monotonic()
        with self.lock:
            stale = [k for k, c in self.candidates.items()
                     if now - c["worker"].used > self.idle]
            gone = [self.candidates.pop(k)["worker"] for k in stale]
            idle = [a for a, w in self.ops.items() if now - w.used > self.ops_idle]
            gone += [self.ops.pop(a) for a in idle]
            quiet = [a for a, w in self.consoles.items()
                     if now - w.used > self.ops_idle]
            gone += [self.consoles.pop(a) for a in quiet]
        for w in gone:
            w.close()


def commit(sessions, account, cid, **opts):
    """
    A Commit: the candidate's process runs `commit ...`, unless the running
    configuration has moved past the candidate's base - then nothing is run
    and the difference is the answer. {"ok", "output", "moved", "revision",
    "confirm"}.
    """
    c = sessions.get(account, cid)
    w = c["worker"]
    now = w.ask("running")
    if now != c["base"]:
        return {"ok": False, "moved": config_diff(c["base"], now),
                "output": "", "revision": revision_of(now), "confirm": None}
    res = w.ask("feed", [commit_line(**opts)])
    after = w.ask("running")
    if not res["failed"]:
        c["base"] = after
    return {"ok": not res["failed"], "moved": "", "output": res["output"],
            "revision": revision_of(after), "confirm": w.ask("confirm")}


# ------------------------------------------------------------- telemetry

def sum_simple(per_thread, idx):
    """A simple counter at `idx`, summed over the threads it was charged to."""
    return sum(t[idx] for t in per_thread if idx < len(t))


def sum_combined(per_thread, idx, field):
    """A combined counter's `field` at `idx`, summed over threads."""
    # By subscript, not by isinstance(dict): vpp_papi's combined counter
    # prints like a dict and is not one, and lab/api-grpc.sh read pg0's rx as
    # 0 through 2000 packets while the check was `isinstance(v, dict)`.
    total = 0
    for t in per_thread:
        if idx < len(t):
            v = t[idx]
            try:
                total += v[field]
            except (TypeError, KeyError, IndexError):
                total += getattr(v, field, 0)
    return total


def interface_samples(stats, only=()):
    """One sample of every interface's counters out of a statistics reader."""
    names = list(stats["/if/names"])
    rx, tx = stats["/if/rx"], stats["/if/tx"]
    rxe, txe, drops = stats["/if/rx-error"], stats["/if/tx-error"], stats["/if/drops"]
    out = []
    for i, name in enumerate(names):
        if not name or name == "local0" or is_lcp_tap(name) \
                or (only and name not in only):
            continue
        out.append({"name": name, "counters": {
            "rx_bytes": sum_combined(rx, i, "bytes"),
            "tx_bytes": sum_combined(tx, i, "bytes"),
            "rx_packets": sum_combined(rx, i, "packets"),
            "tx_packets": sum_combined(tx, i, "packets"),
            "rx_errors": sum_simple(rxe, i), "tx_errors": sum_simple(txe, i),
            "drops": sum_simple(drops, i)}})
    return out


def cgnat_totals(stats):
    """The CGNAT's totals, every pool summed, from the statistics segment."""
    names = list(stats["/bpa/pool/names"])
    tot = {"subscribers": 0, "ports_in_use": 0, "sessions": 0, "refusals": 0}
    pools = {}
    for i, name in enumerate(names):
        if not name:
            continue
        row = {"subscribers": sum_simple(stats["/bpa/pool/subscribers"], i),
               "ports_in_use": sum_simple(stats["/bpa/pool/ports"], i),
               "sessions": sum_simple(stats["/bpa/pool/sessions"], i),
               "refusals": sum_simple(stats["/bpa/pool/refused"], i)}
        pools[name] = row
        for k in tot:
            tot[k] += row[k]
    return tot, pools


def state_events(before, after):
    """The configuration events between two readings of the state file (#524)."""
    out = []
    if after.get("commits") != before.get("commits") and after.get("commits"):
        out.append({"kind": "commit", "account": after.get("last_commit_by", ""),
                    "text": f"commit {after.get('commits')}"})
    if after.get("confirm_pending") != before.get("confirm_pending"):
        pending = after.get("confirm_pending") == "yes"
        out.append({"kind": "confirm-pending" if pending else "confirmed",
                    "account": after.get("last_commit_by", ""),
                    "text": after.get("confirm_deadline", "")})
    if after.get("reverts") != before.get("reverts") and after.get("reverts"):
        out.append({"kind": "rolled-back", "account": "",
                    "text": f"reverts {after.get('reverts')}"})
    return out


def read_state(path="/run/prisma-routing/state/config"):
    """The VTY's non-secret state file (#524) as a dict; {} when absent."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                k, _, v = line.strip().partition(" ")
                if k:
                    out[k] = v
    except OSError:
        pass
    return out


# ------------------------------------------------------------- the servicers
#
# Everything below needs grpc and the generated prisma.v1 stubs, which the
# management image builds (tools/gen-api.sh) and `make test`'s machine does
# not have. The functions above are the logic; these translate.

def _diag_pb(pb, d):
    sev = {"error": pb.Diagnostic.SEVERITY_ERROR,
           "warning": pb.Diagnostic.SEVERITY_WARNING,
           "note": pb.Diagnostic.SEVERITY_NOTE}.get(d["severity"],
                                                     pb.Diagnostic.SEVERITY_UNSPECIFIED)
    return pb.Diagnostic(severity=sev, message=d["message"], path=d["path"],
                         line=d["line"])


# not a unit: builds the gRPC servicers around the core above; it needs grpc
# and the generated stubs, and lab/api-grpc.sh drives every rpc through it.
def build(sessions, stats_factory, pb, rpc, grpc):
    """The three servicers, bound to `sessions` and a statistics reader factory."""
    from google.protobuf.timestamp_pb2 import Timestamp

    # not a unit: a closure of build(), driven by lab/api-grpc.sh.
    def account(context):
        a = account_from_auth(context.auth_context())
        if a is None:
            context.abort(grpc.StatusCode.UNAUTHENTICATED,
                          "the client certificate names no box account")
        return a

    # not a unit: a closure of build(), driven by lab/api-grpc.sh.
    def fail(context, code, output, extra=""):
        diags = pb.Diagnostics(items=[_diag_pb(pb, d) for d in parse_output(output)],
                               output=output)
        context.set_trailing_metadata((("prisma-diagnostics-bin",
                                        diags.SerializeToString()),))
        msg = (extra or (parse_output(output) or [{"message": output.strip()}])[0]["message"])
        context.abort(code, msg[:1000] or "refused")

    # not a unit: a closure of build(), driven by lab/api-grpc.sh.
    def candidate(context, cid):
        try:
            return sessions.get(account(context), cid)
        except KeyError:
            context.abort(grpc.StatusCode.NOT_FOUND, f"no candidate {cid}")

    # not a unit: a closure of build(), driven by lab/api-grpc.sh.
    def ts(epoch):
        t = Timestamp()
        t.FromSeconds(int(epoch))
        return t

    class Config(rpc.ConfigServiceServicer):
        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def OpenCandidate(self, req, context):
            acct = account(context)
            try:
                cid, base, text, _ = sessions.open(
                    acct, req.from_revision if req.HasField("from_revision") else None)
            except PermissionError as e:
                fail(context, grpc.StatusCode.PERMISSION_DENIED, str(e))
            except ValueError as e:
                fail(context, grpc.StatusCode.INVALID_ARGUMENT, str(e))
            except WorkerError as e:
                context.abort(grpc.StatusCode.UNAVAILABLE, str(e))
            return pb.OpenCandidateResponse(candidate_id=cid,
                                            base_revision=revision_of(base), text=text)

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Edit(self, req, context):
            c = candidate(context, req.candidate_id)
            res = c["worker"].ask("feed", edit_lines(req.path, req.lines))
            if res["failed"]:
                code = (grpc.StatusCode.PERMISSION_DENIED if denied(res["output"])
                        else grpc.StatusCode.INVALID_ARGUMENT)
                c["worker"].ask("feed", ["top"])
                fail(context, code, res["output"])
            return pb.EditResponse(diagnostics=[_diag_pb(pb, d) for d in
                                                parse_output(res["output"])],
                                   output=res["output"])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ShowCandidate(self, req, context):
            c = candidate(context, req.candidate_id)
            return pb.ConfigText(text=c["worker"].ask("candidate") or "")

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Compare(self, req, context):
            c = candidate(context, req.candidate_id)
            res = c["worker"].ask("feed", ["top", "show configuration compare"])
            diff = res["output"]
            changed = bool(diff.strip()) and "no differences" not in diff
            return pb.CompareResponse(diff=diff if changed else "", changed=changed)

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Check(self, req, context):
            c = candidate(context, req.candidate_id)
            res = c["worker"].ask("feed", ["top", "commit check"])
            g = c["worker"].ask("gates")
            return pb.CheckResponse(
                passed=not res["failed"],
                diagnostics=[_diag_pb(pb, d) for d in parse_output(res["output"])],
                remap=g["remap"], cutoff=g["cutoff"], diverge=g["diverge"],
                output=res["output"])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Commit(self, req, context):
            acct = account(context)
            try:
                c = sessions.get(acct, req.candidate_id)
            except KeyError:
                context.abort(grpc.StatusCode.NOT_FOUND, f"no candidate {req.candidate_id}")
            c["worker"].ask("feed", ["top"])
            r = commit(sessions, acct, req.candidate_id, comment=req.comment,
                       confirmed_minutes=req.confirmed_minutes,
                       remap=req.acknowledge_remap, cutoff=req.acknowledge_cutoff,
                       diverge=req.acknowledge_diverge)
            if r["moved"]:
                fail(context, grpc.StatusCode.FAILED_PRECONDITION, r["moved"],
                     "the running configuration changed since this candidate "
                     "was opened; open a new candidate and apply again")
            if not r["ok"]:
                code = (grpc.StatusCode.PERMISSION_DENIED if denied(r["output"])
                        else grpc.StatusCode.FAILED_PRECONDITION)
                fail(context, code, r["output"])
            out = pb.CommitResponse(revision=r["revision"], output=r["output"],
                                    diagnostics=[_diag_pb(pb, d) for d in
                                                 parse_output(r["output"])])
            if r["confirm"] and req.confirmed_minutes:
                out.confirm_deadline.CopyFrom(ts(r["confirm"]["deadline"]))
            return out

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Confirm(self, req, context):
            acct = account(context)
            w = sessions.worker(acct)
            try:
                res = w.ask("feed", ["configure", "commit", "exit"])
            finally:
                w.close()
            if res["failed"]:
                code = (grpc.StatusCode.PERMISSION_DENIED if denied(res["output"])
                        else grpc.StatusCode.FAILED_PRECONDITION)
                fail(context, code, res["output"])
            return pb.ConfirmResponse(output=res["output"],
                                      diagnostics=[_diag_pb(pb, d) for d in
                                                   parse_output(res["output"])])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Rollback(self, req, context):
            c = candidate(context, req.candidate_id)
            res = c["worker"].ask("feed", ["top", f"rollback {int(req.revision)}"])
            if res["failed"]:
                fail(context, grpc.StatusCode.INVALID_ARGUMENT, res["output"])
            return pb.EditResponse(output=res["output"],
                                   diagnostics=[_diag_pb(pb, d) for d in
                                                parse_output(res["output"])])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Save(self, req, context):
            w = sessions.ops_worker(account(context))
            res = w.ask("feed", ["save"])
            if res["failed"]:
                code = (grpc.StatusCode.PERMISSION_DENIED if denied(res["output"])
                        else grpc.StatusCode.FAILED_PRECONDITION)
                fail(context, code, res["output"])
            return pb.SaveResponse(output=res["output"])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def CloseCandidate(self, req, context):
            try:
                sessions.close(account(context), req.candidate_id)
            except KeyError:
                context.abort(grpc.StatusCode.NOT_FOUND, f"no candidate {req.candidate_id}")
            return pb.CloseResponse()

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def GetRunning(self, req, context):
            text = sessions.ops_worker(account(context)).ask("running")
            return pb.ConfigText(text=text, revision=revision_of(text))

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListRevisions(self, req, context):
            rows = sessions.ops_worker(account(context)).ask("revisions")
            out = pb.ListRevisionsResponse()
            for n, when, comment in rows:
                rev = out.revisions.add(number=n, comment=comment)
                rev.committed_at.FromJsonString(when)
            return out

    class Operational(rpc.OperationalServiceServicer):
        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def _ask(self, context, op, args=None):
            try:
                return sessions.ops_worker(account(context)).ask(op, args)
            except WorkerError as e:
                context.abort(grpc.StatusCode.UNAVAILABLE, str(e))

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def GetVersion(self, req, context):
            v = parse_version(self._ask(context, "screen", "show version"))
            return pb.VersionInfo(hostname=self._ask(context, "hostname"), **v)

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def GetSystem(self, req, context):
            cpu = self._ask(context, "screen", "show system cpu")
            mem = self._ask(context, "screen", "show system memory")
            c = parse_cpu(cpu)
            up = 0
            try:
                with open(os.environ.get("PRISMA_UPTIME", "/host/proc/uptime")) as f:
                    up = int(float(f.read().split()[0]))
            except (OSError, ValueError, IndexError):
                pass
            pages = 0
            m = re.search(r"(\d+)\s+(?:1G\s+)?(?:huge)?pages?", mem)
            if m:
                pages = int(m.group(1))
            return pb.SystemInfo(hostname=self._ask(context, "hostname"),
                                 uptime_seconds=up, cpu_text=cpu, memory_text=mem,
                                 hugepages=pages,
                                 **uptime_fields(self._ask(context, "uptime"), ts),
                                 **c)

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListInterfaces(self, req, context):
            rows = self._ask(context, "interfaces", list(req.names))
            counters = {}
            try:
                counters = {s["name"]: s["counters"] for s in
                            interface_samples(stats_factory(), list(req.names))}
            except Exception:  # noqa: BLE001 - counters are a bonus, not the answer
                pass
            out = pb.ListInterfacesResponse()
            for r in rows:
                i = out.interfaces.add(name=r["name"], admin_up=r["admin_up"],
                                       link_up=r["link_up"], mtu=r["mtu"],
                                       mac_address=r["mac_address"],
                                       addresses=r["addresses"],
                                       linux_name=r["linux_name"],
                                       description=r["description"])
                if r["name"] in counters:
                    i.counters.CopyFrom(pb.InterfaceCounters(**counters[r["name"]]))
            return out

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListPools(self, req, context):
            rows = self._ask(context, "pools")
            live = {}
            try:
                live = cgnat_totals(stats_factory())[1]
            except Exception:  # noqa: BLE001
                pass
            out = pb.ListPoolsResponse()
            for r in rows:
                l = live.get(r["name"], {})
                out.pools.add(subscribers=l.get("subscribers", 0),
                              ports_in_use=l.get("ports_in_use", 0),
                              refusals=l.get("refusals", 0),
                              sessions=l.get("sessions", 0), **r)
            return out

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def GetSubscriber(self, req, context):
            text = self._ask(context, "screen", f"show cgnat subscriber {req.address}")
            m = re.search(r"pool\s+'?([\w.-]+)'?", text)
            s = re.search(r"(\d+)\s+session", text)
            return pb.Subscriber(address=req.address, pool=m.group(1) if m else "",
                                 sessions=int(s.group(1)) if s else 0, text=text,
                                 blocks=[pb.PortBlock(**b) for b in subscriber_blocks(text)])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def GetSessionsSummary(self, req, context):
            tot = {}
            try:
                tot = cgnat_totals(stats_factory())[0]
            except Exception:  # noqa: BLE001
                pass
            mem = self._ask(context, "screen", "show cgnat memory")
            m = re.search(r"sessions that fit\s+([\d,]+)", mem)
            return pb.SessionsSummary(
                sessions=tot.get("sessions", 0), subscribers=tot.get("subscribers", 0),
                ports_in_use=tot.get("ports_in_use", 0),
                sessions_fit=int(m.group(1).replace(",", "")) if m else 0)

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListBgpNeighbors(self, req, context):
            return pb.ListBgpNeighborsResponse(
                neighbors=[pb.BgpNeighbor(**r) for r in self._ask(context, "bgp")])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListOspfNeighbors(self, req, context):
            return pb.ListOspfNeighborsResponse(
                neighbors=[pb.OspfNeighbor(**r) for r in self._ask(context, "ospf")])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListIsisAdjacencies(self, req, context):
            return pb.ListIsisAdjacenciesResponse(
                adjacencies=[pb.IsisAdjacency(**r) for r in self._ask(context, "isis")])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListVrrp(self, req, context):
            return pb.ListVrrpResponse(
                routers=[pb.VrrpRouter(**r) for r in self._ask(context, "vrrp")])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListBundles(self, req, context):
            return pb.ListBundlesResponse(
                bundles=[pb.Bundle(**r) for r in self._ask(context, "bundles")])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Show(self, req, context):
            if not show_allowed(req.command):
                context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                              f"'{req.command}' is not an operational read; Show "
                              f"takes {', '.join(SHOW_WORDS)}")
            try:
                w = sessions.console_worker(account(context))
                return pb.ShowResponse(text=w.ask("screen", req.command))
            except WorkerError as e:
                context.abort(grpc.StatusCode.UNAVAILABLE, str(e))

    class Telemetry(rpc.TelemetryServiceServicer):
        # not a unit: a streaming rpc, driven by lab/api-grpc.sh.
        def Subscribe(self, req, context):
            account(context)
            interval = max(500, req.interval_ms or 1000) / 1000.0
            stats = stats_factory()
            state = read_state()
            while context.is_active():
                now = time.time()
                if req.interface_counters:
                    batch = pb.InterfaceBatch(samples=[
                        pb.InterfaceSample(name=s["name"],
                                           counters=pb.InterfaceCounters(**s["counters"]))
                        for s in interface_samples(stats, list(req.interfaces))])
                    yield pb.TelemetryUpdate(at=ts(now), interfaces=batch)
                if req.cgnat_totals:
                    yield pb.TelemetryUpdate(at=ts(now),
                                             cgnat=pb.CgnatTotals(**cgnat_totals(stats)[0]))
                if req.events:
                    after = read_state()
                    for e in state_events(state, after):
                        yield pb.TelemetryUpdate(at=ts(now), event=pb.Event(**e))
                    state = after
                time.sleep(interval)

    return Config(), Operational(), Telemetry()


# not a unit: the program's entry - reads the certificates, binds the
# listener and serves until stopped; lab/api-grpc.sh starts it.
def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Prisma Routing management API (prisma.v1)")
    ap.add_argument("--listen", default=os.environ.get("PRISMA_API_LISTEN", DEFAULT_LISTEN))
    ap.add_argument("--dir", default=API_DIR, help="server.crt, server.key, clients-ca.crt")
    ap.add_argument("--stubs", default=os.environ.get("PRISMA_API_STUBS",
                                                      os.path.join(HERE, "apistubs")))
    ap.add_argument("--stats", default=os.environ.get("PRISMA_STATS_SOCKET",
                                                      "/run/vpp/stats.sock"))
    ap.add_argument("--stop-on-eof", action="store_true",
                    help="stop when stdin closes (the host unit's docker exec)")
    a = ap.parse_args(argv)
    sys.path.insert(0, a.stubs)
    import grpc
    from concurrent import futures
    from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc
    files = {n: os.path.join(a.dir, n) for n in ("server.key", "server.crt", "clients-ca.crt")}
    missing = [p for p in files.values() if not os.path.exists(p)]
    if missing:
        print(f"apiserver: missing {', '.join(missing)}: the API serves mutual TLS "
              "only; see docs/api.md", file=sys.stderr)
        return 2
    creds = grpc.ssl_server_credentials(
        [(open(files["server.key"], "rb").read(), open(files["server.crt"], "rb").read())],
        root_certificates=open(files["clients-ca.crt"], "rb").read(),
        require_client_auth=True)

    # not a unit: the statistics reader the telemetry and the counters share.
    def stats_factory():
        from vpp_papi.vpp_stats import VPPStats
        s = VPPStats(a.stats)
        s.connect()
        return s

    sessions = Sessions()
    cfg, ops, tel = build(sessions, stats_factory, pb, rpc, grpc)
    # NO SO_REUSEPORT, which gRPC turns on by default: with it a second server
    # binds the same port beside the first, and the X520 bench (0.148.4) was
    # found with two listeners on 128.0.0.2:50051 after restarts. Without it
    # a second server is refused at the bind and exits, and the one answering
    # is the one that started first.
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16),
                         options=(("grpc.so_reuseport", 0),))
    rpc.add_ConfigServiceServicer_to_server(cfg, server)
    rpc.add_OperationalServiceServicer_to_server(ops, server)
    rpc.add_TelemetryServiceServicer_to_server(tel, server)
    # grpcio 1.51 raises where older ones returned 0: both are the same
    # refusal, said in one sentence rather than as a traceback.
    try:
        bound = server.add_secure_port(a.listen, creds)
    except RuntimeError:
        bound = 0
    if not bound:
        print(f"apiserver: cannot listen on {a.listen}", file=sys.stderr)
        return 1
    server.start()
    print(f"apiserver: prisma.v1 on {a.listen}, mutual TLS", flush=True)
    if a.stop_on_eof:
        # not a unit: a closure of main(); stdin closing is how the host
        # unit's stop reaches a server docker exec started (see the unit).
        def watch():
            # os.read on the descriptor, not sys.stdin: a thread blocked in
            # sys.stdin's reader holds its lock across a fork, and every
            # worker's multiprocessing bootstrap closes sys.stdin first -
            # measured, each one hung there and no session started.
            while os.read(0, 4096):
                pass
            print("apiserver: stdin closed, stopping", flush=True)
            server.stop(5)
        threading.Thread(target=watch, daemon=True).start()
    server.wait_for_termination()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
