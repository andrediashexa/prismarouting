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
import array
import codecs
import contextlib
import difflib
import hashlib
import io
import ipaddress
import json
import multiprocessing
import os
import re
import subprocess
import sys
import tempfile
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
#: The routes process is kept this long between uses (#533): as long as a
#: snapshot is, so a client paging slowly keeps its snapshot.
ROUTE_IDLE = 600
#: Routes per ListRoutes page when the call names none, and the most it may.
ROUTE_PAGE = 1000
ROUTE_PAGE_MAX = 5000
#: How long a page waits for the snapshot to reach it before answering what
#: it has, with a token at the point it reached.
ROUTE_PAGE_WAIT = 60
#: Snapshots a routes process holds at once; a new one drops the oldest.
ROUTE_SNAPSHOTS = 4
#: FRR's protocol words ListRoutes takes, and passes to vtysh only from here.
ROUTE_PROTOCOLS = ("kernel", "connected", "local", "static", "rip", "ripng",
                   "ospf", "ospf6", "isis", "bgp", "pim", "eigrp", "nhrp",
                   "table", "babel", "sharp", "openfabric")
#: The counter history (#533): one sample a minute, a day of them, and the
#: interfaces it records at most - 7 counters x 8 bytes x 1440 samples is
#: 80 KB an interface, so the cap holds the worst case near 20 MB.
HISTORY_INTERVAL = int(os.environ.get("PRISMA_API_HISTORY_INTERVAL", "60") or 60)
HISTORY_SAMPLES = int(os.environ.get("PRISMA_API_HISTORY_SAMPLES", "1440") or 1440)
HISTORY_INTERFACES = 256
#: Where the history is kept across a restart of the server: the host's
#: /run, which the container shares - an upgrade restarts the server and
#: recreates the container, and keeps this; a reboot empties it, and the
#: dataplane's counters start again from zero then anyway.
HISTORY_PATH = os.environ.get("PRISMA_API_HISTORY",
                              "/run/prisma-routing/api/history.json")
#: Written every this many samples, and when the server stops.
HISTORY_SAVE_EVERY = 5
#: A value a series did not have at a sample: an interface that did not
#: exist yet, a reading that failed.
MISSING = 2 ** 64 - 1
#: A worker running fewer main loops a second than this is not polling: it
#: waits for an interrupt between packets. Measured in lab/api-grpc.sh's
#: dataplane: a worker polling nothing ran 98 loops a second, one polling an
#: idle input 750 thousand, the same one saturated by a packet generator 70
#: thousand. Between the two the line is wide.
SLEEPING_LOOPS = 1000
#: The shortest window a busy ratio is measured over.
BUSY_WINDOW = 1.0

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
            # sw_interface_details' link_speed is kbps (#533).
            "speed_bps": int(d.get("link_speed", 0) or 0) * 1000,
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
    # Routing policy (#538): what `show policy`, `show policy <name>` and
    # `test policy` compute, as data, from the same Shell methods.
    "policies": lambda sh, a: sh.policies_data(),
    "policy_counters": lambda sh, a: sh.policy_counters_data(a or ""),
    "test_policy": lambda sh, a: sh.test_policy_data(a or {}),
    # #533: who the account is to the VTY, and the routing table typed.
    "whoami": lambda sh, a: whoami(sh),
    "routes": lambda sh, a: routes_page(sh, a or {}),
    "route_summary": lambda sh, a: route_summary(sh, a or ""),
}


# ------------------------------------------------------------- who am I (#533)

def whoami(sh):
    """
    The account this process is and the role the VTY gives it: the Shell's
    own capabilities, read off the process's groups when it started - the
    same answer the VTY login gives, not a second reading of the groups.
    """
    import pwd
    from prismash import CAP_ALL, CAP_CGNAT, role_name
    caps = set(getattr(sh, "caps", set()) or set())
    return {"account": pwd.getpwuid(os.geteuid()).pw_name,
            "role": role_name(caps),
            "can_configure": CAP_ALL in caps,
            "can_configure_cgnat": CAP_CGNAT in caps}


# --------------------------------------------------------- the routes (#533)
#
# FRR's RIB, read from zebra's own JSON a route at a time. A full table is a
# million routes, and `show ip route json` for it is far more than a process
# should hold: so the answer is read as it streams (json_object_items), each
# route written to a snapshot file as one line, and a page is read back from
# that file by offset. Memory holds one route and one page, whatever the
# table; the file is unlinked from the start and goes with the process.

def json_object_items(read, chunk=65536):
    """
    The (key, value) pairs of the JSON object `read` returns a chunk at a
    time (bytes or text), with one value in memory at a time. Raises
    ValueError when what is read is not a JSON object - vtysh answering a
    refusal, say - with the start of what it said.
    """
    dec = json.JSONDecoder()
    utf8 = codecs.getincrementaldecoder("utf-8")("replace")
    st = {"buf": "", "pos": 0, "eof": False}

    # not a unit: a closure of json_object_items(), which the tests drive whole.
    def more():
        data = read(chunk)
        if not data:
            st["eof"] = True
            st["buf"] = st["buf"][st["pos"]:] + utf8.decode(b"", True)
            st["pos"] = 0
            return False
        if isinstance(data, bytes):
            data = utf8.decode(data)
        st["buf"] = st["buf"][st["pos"]:] + data
        st["pos"] = 0
        return True

    # not a unit: a closure of json_object_items(), which the tests drive whole.
    def peek():
        while True:
            buf, pos = st["buf"], st["pos"]
            while pos < len(buf) and buf[pos] in " \t\r\n":
                pos += 1
            st["pos"] = pos
            if pos < len(buf):
                return buf[pos]
            if not more():
                return ""

    # not a unit: a closure of json_object_items(), which the tests drive whole.
    def value():
        while True:
            try:
                v, end = dec.raw_decode(st["buf"], st["pos"])
                # A value that ends where the buffer does may be a number cut
                # by the chunk; read on, and decode it again whole.
                if end < len(st["buf"]) or st["eof"]:
                    st["pos"] = end
                    return v
            except ValueError:
                if st["eof"]:
                    raise ValueError("the JSON ends inside a value")
            more()

    if peek() != "{":
        # What it said instead, a line of it: vtysh's refusal, say.
        while len(st["buf"]) - st["pos"] < 120 and "\n" not in st["buf"][st["pos"]:] \
                and more():
            pass
        said = st["buf"][st["pos"]:].strip().split("\n")[0][:120]
        raise ValueError(said or "nothing")
    st["pos"] += 1
    while True:
        c = peek()
        if c == ",":
            st["pos"] += 1
            c = peek()
        if c == "}" or c == "":
            return
        key = value()
        if peek() != ":":
            raise ValueError(f"no ':' after {key!r}")
        st["pos"] += 1
        peek()
        yield key, value()


def frr_age(text):
    """
    Seconds in FRR's uptime words: "00:01:02", "1d02h03m", "02w3d04h".
    0 for a form it does not know.
    """
    t = str(text or "").strip()
    m = re.match(r"^(\d+):(\d+):(\d+)$", t)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    unit = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
    parts = re.findall(r"(\d+)([wdhms])", t)
    if not parts or "".join(n + u for n, u in parts) != t:
        return 0
    return sum(int(n) * unit[u] for n, u in parts)


def route_row(vrf, family, entry):
    """One of FRR's route entries as a ListRoutes row."""
    hops = []
    for n in entry.get("nexthops") or []:
        hole = ("reject" if n.get("reject") else
                "blackhole" if n.get("blackhole") or n.get("unreachable") else "")
        nvrf = n.get("vrf", "")
        hops.append({"address": n.get("ip", ""),
                     "interface": n.get("interfaceName", ""),
                     "active": bool(n.get("active")), "fib": bool(n.get("fib")),
                     "weight": int(n.get("weight", 0) or 0),
                     "directly_connected": bool(n.get("directlyConnected")),
                     "vrf": nvrf if nvrf and nvrf != vrf else "",
                     "blackhole": hole})
    return {"vrf": entry.get("vrfName") or vrf, "prefix": entry.get("prefix", ""),
            "family": family, "protocol": entry.get("protocol", ""),
            "distance": int(entry.get("distance", 0) or 0),
            "metric": int(entry.get("metric", 0) or 0),
            "selected": bool(entry.get("selected")),
            "installed": bool(entry.get("installed")),
            "next_hops": hops, "table": int(entry.get("table", 0) or 0),
            "age_seconds": frr_age(entry.get("uptime"))}


def route_request(args):
    """
    A ListRoutes request checked: (filters, None), or (None, why) for one
    the box refuses. Nothing in a request reaches vtysh unchecked.
    """
    vrf = str(args.get("vrf") or "")
    if vrf and not re.match(r"^[A-Za-z0-9_.-]{1,32}$", vrf):
        return None, f"'{vrf}' is not a VRF name"
    family = int(args.get("family") or 0)
    if family not in (0, 4, 6):
        return None, f"family {family} is not 4, 6 or 0 for both"
    prefix = str(args.get("prefix") or "")
    net = None
    if prefix:
        try:
            net = ipaddress.ip_network(prefix, strict=False)
        except ValueError:
            return None, f"'{prefix}' is not a prefix"
        if family and family != net.version:
            return None, f"{prefix} is not an IPv{family} prefix"
        family = net.version
    proto = str(args.get("protocol") or "")
    if proto and proto not in ROUTE_PROTOCOLS:
        return None, (f"'{proto}' is not a protocol FRR names; one of "
                      f"{', '.join(ROUTE_PROTOCOLS)}")
    size = int(args.get("page_size") or ROUTE_PAGE)
    return {"vrf": vrf, "families": [family] if family else [4, 6],
            "prefix": str(net) if net else "", "net": net, "protocol": proto,
            "selected_only": bool(args.get("selected_only")),
            "page_size": max(1, min(size, ROUTE_PAGE_MAX))}, None


def route_command(vrf, family, prefix="", protocol=""):
    """The vtysh command that lists one VRF's routes of one family."""
    words = ["show", "ip" if family == 4 else "ipv6", "route", "vrf", vrf]
    if prefix:
        words += [prefix, "longer-prefixes"]
    if protocol:
        words.append(protocol)
    return " ".join(words + ["json"])


def route_keeps(f, row):
    """Does a ListRoutes request's filter `f` keep this row?"""
    if f["selected_only"] and not row["selected"]:
        return False
    if f["protocol"] and row["protocol"] != f["protocol"]:
        return False
    if f["net"] is not None:
        try:
            net = ipaddress.ip_network(row["prefix"], strict=False)
        except ValueError:
            return False
        if net.version != f["net"].version or not net.subnet_of(f["net"]):
            return False
    return True


def summary_counts(data, family):
    """RouteCount rows out of `show ip[v6] route vrf all summary json`."""
    out = []
    # One VRF answers its summary unwrapped; several answer one per name.
    vrfs = {"default": data} if "routes" in data else data
    for vrf, v in sorted((vrfs or {}).items()):
        for r in (v or {}).get("routes") or []:
            out.append({"vrf": vrf, "family": family,
                        "protocol": r.get("type", ""),
                        "rib": int(r.get("rib", 0) or 0),
                        "fib": int(r.get("fib", 0) or 0)})
    return out


def route_summary(sh, vrf=""):
    """GetRouteSummary: zebra's counts, both families, every VRF or one."""
    out = []
    for family, word in ((4, "ip"), (6, "ipv6")):
        out += summary_counts(frr_json(sh, f"show {word} route vrf all summary json"),
                              family)
    return [c for c in out if not vrf or c["vrf"] == vrf]


def vtysh_stream(command):
    """vtysh answering `command`, its output read as it comes."""
    from prismash import VTYSH
    return subprocess.Popen([VTYSH, "-c", command], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)


class RouteSnapshot:
    """
    One reading of the RIB for one ListRoutes request: filled by a thread of
    its own, as vtysh answers, into an unlinked file of one JSON line a
    route; paged from the same file by offset while it is still being filled.
    """

    #: Rows written together, and announced to a page waiting for them.
    BATCH = 200

    def __init__(self, commands, keep, run=vtysh_stream):
        self.id = uuid.uuid4().hex[:12]
        self.at = time.time()
        self.used = time.monotonic()
        self.commands, self.keep, self.run = commands, keep, run
        self.file = tempfile.TemporaryFile()
        self.rows = 0
        self.size = 0
        self.done = False
        self.error = ""
        self.stopped = False
        self.proc = None
        self.cond = threading.Condition()
        threading.Thread(target=self._fill, daemon=True).start()

    # not a unit: the snapshot's thread; RouteSnapshot is driven whole by
    # test_apiserver, which waits on what this fills.
    def _fill(self):
        batch, n = [], 0
        try:
            for vrf, family, command in self.commands:
                if self.stopped:
                    break
                self.proc = self.run(command)
                try:
                    for _, entries in json_object_items(self.proc.stdout.read):
                        for e in entries if isinstance(entries, list) else []:
                            row = route_row(vrf, family, e)
                            if self.keep(row):
                                batch.append(json.dumps(row, separators=(",", ":")))
                                n += 1
                        if len(batch) >= self.BATCH:
                            self._write(batch)
                            batch = []
                        if self.stopped:
                            break
                finally:
                    self.proc.stdout.close()
                    self.proc.wait()
        except ValueError as e:
            self.error = f"FRR did not answer the routes: {e}"
        except OSError as e:
            self.error = f"vtysh could not be asked: {e}"
        self._write(batch, done=True)

    def _write(self, lines, done=False):
        """Append rows to the file and wake a page waiting for them."""
        data = "".join(l + "\n" for l in lines).encode()
        if data:
            os.pwrite(self.file.fileno(), data, self.size)
        with self.cond:
            self.rows += len(lines)
            self.size += len(data)
            self.done = self.done or done
            self.cond.notify_all()

    def page(self, index, offset, count, wait=ROUTE_PAGE_WAIT):
        """
        Up to `count` rows from row `index` at byte `offset`, once the
        snapshot has them or is done, or after `wait` seconds with what it
        has: (rows, next index, next offset, more to come).
        """
        self.used = time.monotonic()
        with self.cond:
            self.cond.wait_for(lambda: self.done or self.rows >= index + count, wait)
            rows_now, size_now, done = self.rows, self.size, self.done
        out, pos, used, tail = [], offset, offset, b""
        while len(out) < count and pos < size_now:
            data = os.pread(self.file.fileno(), min(1 << 20, size_now - pos), pos)
            if not data:
                break
            pos += len(data)
            lines = (tail + data).split(b"\n")
            tail = lines.pop()
            for line in lines:
                if len(out) == count:
                    break
                out.append(json.loads(line))
                # The next page starts at the byte after the last row given.
                used += len(line) + 1
        nxt = index + len(out)
        return out, nxt, used, (nxt < rows_now) or not done

    def close(self):
        """Stop reading and drop the file."""
        self.stopped = True
        try:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.kill()
        except OSError:
            pass
        self.file.close()


def route_snapshots(sh):
    """The snapshots this process holds, expiring the stale ones."""
    snaps = sh.__dict__.setdefault("_route_snapshots", {})
    now = time.monotonic()
    for sid in [k for k, v in snaps.items() if now - v.used > ROUTE_IDLE]:
        snaps.pop(sid).close()
    return snaps


def routes_page(sh, args):
    """
    ListRoutes in the account's routes process: a page of the snapshot the
    token names, or of a new one. {"rows", "next", "total", "total_known",
    "at"}, or {"error", "code"} - code "invalid", "not_found" or "expired".
    """
    snaps = route_snapshots(sh)
    token = str(args.get("page_token") or "")
    if token:
        m = re.match(r"^([0-9a-f]{12}):(\d+):(\d+)$", token)
        snap = snaps.get(m.group(1)) if m else None
        if snap is None:
            return {"error": "the page token names no snapshot this box still "
                    "holds; start again without one", "code": "expired"}
        size = int(args.get("page_size") or ROUTE_PAGE)
        index, offset = int(m.group(2)), int(m.group(3))
    else:
        f, why = route_request(args)
        if f is None:
            return {"error": why, "code": "invalid"}
        known = {c["vrf"] for c in route_summary(sh)}
        if f["vrf"] and known and f["vrf"] not in known:
            return {"error": f"no VRF {f['vrf']}", "code": "not_found"}
        vrfs = [f["vrf"]] if f["vrf"] else sorted(known, key=lambda v: (v != "default", v)) \
            or ["default"]
        commands = [(v, fam, route_command(v, fam, f["prefix"], f["protocol"]))
                    for v in vrfs for fam in f["families"]]
        snap = RouteSnapshot(commands, lambda r, f=f: route_keeps(f, r),
                             run=getattr(sh, "vtysh_stream", vtysh_stream))
        while len(snaps) >= ROUTE_SNAPSHOTS:
            oldest = min(snaps.values(), key=lambda v: v.used)
            snaps.pop(oldest.id).close()
        snaps[snap.id] = snap
        size, index, offset = f["page_size"], 0, 0
    size = max(1, min(size, ROUTE_PAGE_MAX))
    rows, ni, no, more = snap.page(index, offset, size,
                                   wait=getattr(sh, "route_page_wait", ROUTE_PAGE_WAIT))
    if snap.error:
        if not rows and not more:
            return {"error": snap.error, "code": "unavailable"}
        # Rows read before FRR stopped answering: handed out, and the call
        # after them says why there are no more - never a short table
        # passed off as the whole one.
        more = True
    return {"rows": rows, "next": f"{snap.id}:{ni}:{no}" if more else "",
            "total": snap.rows if snap.done else 0, "total_known": snap.done,
            "at": snap.at}


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


def start_method(shell_factory):
    """
    How an account's process is started: "spawn" on the box, "fork" for a
    test's stand-in shell.

    NEVER FORK THE SERVER. It runs gRPC's thread pool, and a fork taken while
    another thread holds a lock - gRPC's, the logging module's, the
    allocator's - leaves the child holding it for ever. Measured on the X520
    bench (0.148.7): three prisma-web processes parked in futex_wait and every
    call DEADLINE_EXCEEDED, after it had worked on the releases before by the
    luck of when the fork fell. spawn starts a fresh interpreter, which holds
    nothing. A test's stand-in shell is a function of the test script, which
    spawn would have to import again as a program, so a test forks - from a
    process with no gRPC threads in it.
    """
    return "fork" if shell_factory else "spawn"


class WorkerError(Exception):
    """A worker that could not start or could not answer."""


class Worker:
    """One account's process: a Shell held between calls, one request at a time."""

    def __init__(self, account, shell_factory=None, timeout=300):
        self.account = account
        self.timeout = timeout
        self.lock = threading.Lock()
        self.used = time.monotonic()
        ctx = multiprocessing.get_context(start_method(shell_factory))
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

    def __init__(self, worker=Worker, idle=CANDIDATE_IDLE, ops_idle=WORKER_IDLE,
                 route_idle=ROUTE_IDLE):
        self.worker = worker
        self.idle = idle
        self.ops_idle = ops_idle
        self.route_idle = route_idle
        self.candidates = {}   # id -> {"account", "worker", "base"}
        self.ops = {}          # account -> Worker
        # The console's own process per account (Show): a ping or a traceroute
        # holds its process for as long as it runs, and in the operational
        # one it held every GetVersion behind it - measured from the code for
        # prisma-web, whose status monitor polls every 5 s with a 4 s limit.
        self.consoles = {}     # account -> Worker
        # The routes' own process per account (#533): a snapshot of a full
        # table is read by a thread in it for a minute, and the operational
        # process answering GetVersion must not share its interpreter.
        self.routes = {}       # account -> Worker
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

    def route_worker(self, account):
        """The account's process for ListRoutes, whose snapshots it holds."""
        self.reap()
        with self.lock:
            w = self.routes.get(account)
        if w is None or not w.proc.is_alive():
            w = self.worker(account)
            with self.lock:
                self.routes[account] = w
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
            # As long as its snapshots live (ROUTE_IDLE).
            stale = [a for a, w in self.routes.items()
                     if now - w.used > self.route_idle]
            gone += [self.routes.pop(a) for a in stale]
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
    """
    The configuration events between two readings of the state file (#524),
    each with the revision and the comment the box recorded (#533).

    The keys are the ones Shell.record_applied() writes: `commit_by` and
    `confirm_reverts`. This read `last_commit_by` and `reverts`, which
    nothing writes, so every Event's account was empty and no rolled-back
    event was ever sent - found by lab/api-grpc.sh's event check (#533).
    """
    out = []
    by = after.get("commit_by", "")
    # The revision is the server's to fill (event_revision): it is the
    # running configuration's id as the subscriber's account reads it.
    tag = {"revision": "", "comment": after.get("comment", "")}
    if after.get("commits") != before.get("commits") and after.get("commits"):
        out.append({"kind": "commit", "account": by,
                    "text": f"commit {after.get('commits')}", **tag})
    if after.get("confirm_pending") != before.get("confirm_pending"):
        pending = after.get("confirm_pending") == "yes"
        out.append({"kind": "confirm-pending" if pending else "confirmed",
                    "account": by, "text": after.get("confirm_deadline", ""), **tag})
    if after.get("confirm_reverts") != before.get("confirm_reverts") \
            and after.get("confirm_reverts"):
        out.append({"kind": "rolled-back", "account": by,
                    "text": f"reverts {after.get('confirm_reverts')}", **tag})
    return out


def event_revision(read_running, state_then, read=None):
    """
    The revision an event left: the running configuration's id, read now by
    `read_running` - the subscriber's own reading, so it is the id that
    account's CommitResponse and GetRunning carry. "" when another change
    took effect while it was read (the state's commit count moved past
    `state_then`): that id would be the later change's, and the later
    change's own event carries it.
    """
    text = read_running()
    now = (read or read_state)()
    return revision_of(text) if now.get("commits") == state_then.get("commits") else ""


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


# ------------------------------------------------- memory and the workers (#533)

def is_number(text):
    """The VTY's own digits predicate (system.is_number, #412)."""
    from system import is_number as digits
    return digits(text)


def memory_of(path=None):
    """
    The host's RAM in bytes from meminfo: total, used (MemTotal less
    MemAvailable, so reserved hugepages count as used) and the hugepages.
    Zeros for what it cannot read.
    """
    info = {}
    try:
        with open(path or os.environ.get("PRISMA_MEMINFO", "/proc/meminfo")) as f:
            for raw in f:
                k, _, v = raw.partition(":")
                w = v.split()
                if w and is_number(w[0]):
                    info[k.strip()] = int(w[0]) * (1024 if w[1:2] == ["kB"] else 1)
    except OSError:
        pass
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", total)
    pages = info.get("HugePages_Total", 0)
    size = info.get("Hugepagesize", 0)
    return {"memory_total_bytes": total, "memory_used_bytes": max(0, total - avail),
            "memory_hugepages_bytes": pages * size}


def cpu_list(spec):
    """The CPUs a list like '3-7,9' names, in order."""
    out = []
    for part in filter(None, (spec or "").replace(" ", "").split(",")):
        a, _, b = part.partition("-")
        if is_number(a) and (not b or is_number(b)):
            out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def startup_worker_cpus(path=None):
    """The `corelist-workers` of the dataplane's startup.conf, or ''."""
    try:
        with open(path or os.environ.get("PRISMA_STARTUP_CONF",
                                         "/etc/vpp/startup.conf")) as f:
            for raw in f:
                w = raw.split()
                if w[:1] == ["corelist-workers"] and len(w) > 1:
                    return w[1]
    except OSError:
        pass
    return ""


def per_worker(stats, name, workers):
    """
    A /sys/... per-thread vector from the statistics segment, the workers'
    values only: index 0 is the main thread, which is not a worker - on the
    lab's two workers it read [[0, 6354288, 6901372]].
    """
    try:
        rows = stats[name]
    except (KeyError, IndexError, TypeError, ValueError):
        return [0] * workers
    row = list(rows[0]) if rows and isinstance(rows[0], (list, tuple)) else list(rows or [])
    vals = row[1:] if len(row) == workers + 1 else row[-workers:] if workers else []
    return [float(v or 0) for v in vals] + [0.0] * (workers - len(vals))


RUNTIME_ROW = re.compile(r"^(.*?)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+(?:e[-+]?\d+)?)\s+([\d.]+)\s*$")


def runtime_clocks(text):
    """
    Each worker's clocks out of `show runtime`: {worker: {"active", "all"}}
    - the clocks its graph nodes took (state `active`: they run only with
    packets) and every node's, the input nodes' polling included. A row's
    Clocks column is per vector when the node had vectors, per call when it
    had none, so a node's clocks are the column times the one it is per.
    """
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^Thread \d+ vpp_wk_(\d+)", line)
        if m:
            cur = out.setdefault(int(m.group(1)), {"active": 0.0, "all": 0.0})
            continue
        if line.startswith("Thread "):
            cur = None
            continue
        r = RUNTIME_ROW.match(line) if cur is not None else None
        if not r or r.group(1).strip() in ("Name", ""):
            continue
        head = r.group(1).rstrip()
        state = head.rsplit('"', 2)[1] if head.endswith('"') else head.split()[-1]
        calls, vectors = int(r.group(2)), int(r.group(3))
        clocks = float(r.group(5)) * (vectors if vectors else calls)
        cur["all"] += clocks
        if state == "active":
            cur["active"] += clocks
    return out


def busy_between(before, after):
    """
    The share of each worker's node time that went to its graph between two
    runtime_clocks() readings: its packets past the input node, 0 to 1.
    Not the receive in the input node itself, which is counted with the
    polling it shares a node with, so a worker that cannot keep up reads
    below 1 - the lab's worker saturated by a packet generator read 0.49.
    """
    out = {}
    for w, a in after.items():
        b = before.get(w, {"active": 0.0, "all": 0.0})
        total = a["all"] - b["all"]
        work = a["active"] - b["active"]
        out[w] = round(min(1.0, max(0.0, work / total)), 4) if total > 0 else 0.0
    return out


def vpp_runtime():
    """`show runtime`, as vppctl prints it; "" when the dataplane does not answer."""
    try:
        p = subprocess.run([os.environ.get("PRISMA_VPPCTL", "vppctl"), "show", "runtime"],
                           capture_output=True, text=True, timeout=10)
        return p.stdout if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


class BusyMeter:
    """
    The workers' busy ratios over a window: `show runtime`'s clocks read now
    against the reading before, which this keeps - at least BUSY_WINDOW
    old, so a reading taken right after another waits for the rest of it.
    """

    def __init__(self, read=vpp_runtime, clock=time.monotonic, sleep=time.sleep):
        self.read, self.clock, self.sleep = read, clock, sleep
        self.prev = None     # (when, runtime_clocks)
        self.lock = threading.Lock()

    def busy(self):
        """{worker: (busy ratio, window in ms)}; {} when there is no reading."""
        with self.lock:
            if self.prev is None or self.clock() - self.prev[0] < BUSY_WINDOW:
                if self.prev is None:
                    self.prev = (self.clock(), runtime_clocks(self.read()))
                self.sleep(max(0.0, BUSY_WINDOW - (self.clock() - self.prev[0])))
            now = (self.clock(), runtime_clocks(self.read()))
            before, self.prev = self.prev, now
        ms = int((now[0] - before[0]) * 1000)
        return {w: (r, ms) for w, r in busy_between(before[1], now[1]).items()}


def worker_load(stats, worker_cpus="", busy=None):
    """
    WorkerLoad rows: the statistics segment's vector and loop rates, the
    busy ratios a BusyMeter measured ({worker: (ratio, window ms)}).
    """
    busy = busy or {}
    try:
        n = int(stats["/sys/num_worker_threads"])
    except (KeyError, TypeError, ValueError):
        return []
    rate = per_worker(stats, "/sys/vector_rate_per_worker", n)
    loops = per_worker(stats, "/sys/loops_per_worker", n)
    cpus = cpu_list(worker_cpus)
    out = []
    for i in range(n):
        # A worker waiting for interrupts is not busy, and the dataplane does
        # not update its vector rate while it waits - the lab read a worker's
        # 5 for a minute after its traffic stopped - so both are 0.
        sleeping = loops[i] < SLEEPING_LOOPS
        ratio, ms = busy.get(i, (0.0, 0))
        out.append({"worker": i, "cpu": cpus[i] if i < len(cpus) else i,
                    "vectors_per_call": 0.0 if sleeping else rate[i],
                    "busy_ratio": 0.0 if sleeping else ratio,
                    "loops_per_second": loops[i], "busy_window_ms": ms})
    return out


# ------------------------------------------------------ counter history (#533)

class History:
    """
    The counters sampled once a HISTORY_INTERVAL, the last HISTORY_SAMPLES of
    them: one array a series - an interface's counter, a CGNAT total, a
    worker's figure - aligned on the sample times. Arrays of machine words,
    not Python objects: a day of a 64-interface box is 64 x 7 x 1440 words,
    5 MB, where the same as tuples measured some ten times that.
    """

    def __init__(self, keep=HISTORY_SAMPLES, max_interfaces=HISTORY_INTERFACES):
        self.keep = keep
        self.max_interfaces = max_interfaces
        self.times = array.array("d")
        self.counters = {}   # key -> array('Q')
        self.gauges = {}     # key -> array('d')
        self.lock = threading.Lock()

    def _put(self, table, kind, key, value):
        a = table.get(key)
        if a is None:
            a = table[key] = array.array(kind, [MISSING if kind == "Q" else float("nan")]
                                         * (len(self.times) - 1))
        a.append(value)

    def add(self, at, interfaces=(), cgnat=None, workers=(), memory_used=None):
        """One sample: what was read at `at`; what was not, missing."""
        with self.lock:
            self.times.append(at)
            names = {k[1] for k in self.counters if k[0] == "if"}
            for smp in interfaces:
                if smp["name"] not in names and len(names) >= self.max_interfaces:
                    continue
                names.add(smp["name"])
                for f, v in smp["counters"].items():
                    self._put(self.counters, "Q", ("if", smp["name"], f), int(v))
            for f, v in (cgnat or {}).items():
                self._put(self.counters, "Q", ("cgnat", f), int(v))
            for w in workers:
                for f in ("cpu", "vectors_per_call", "busy_ratio", "loops_per_second",
                          "busy_window_ms"):
                    self._put(self.gauges, "d", ("wk", int(w["worker"]), f), float(w[f]))
            if memory_used is not None:
                self._put(self.counters, "Q", ("mem",), int(memory_used))
            n = len(self.times)
            for table, gap in ((self.counters, MISSING), (self.gauges, float("nan"))):
                for a in table.values():
                    if len(a) < n:
                        a.append(gap)
            if n > self.keep:
                drop = n - self.keep
                del self.times[:drop]
                for table in (self.counters, self.gauges):
                    for a in table.values():
                        del a[:drop]

    def samples(self, since=0.0, interfaces=(), counters=True, cgnat=True,
                workers=True, memory=True):
        """
        The samples after `since`, oldest first, as GetHistory sends them:
        copied out under the lock, then built one at a time.
        """
        with self.lock:
            first = next((i for i, t in enumerate(self.times) if t > since), len(self.times))
            times = list(self.times[first:])
            cs = {k: a[first:] for k, a in self.counters.items()
                  if (k[0] == "if" and counters and (not interfaces or k[1] in interfaces))
                  or (k[0] == "cgnat" and cgnat) or (k[0] == "mem" and memory)}
            gs = {k: a[first:] for k, a in self.gauges.items() if workers}
        per_if, per_wk = {}, {}
        for k, a in cs.items():
            if k[0] == "if":
                per_if.setdefault(k[1], []).append((k[2], a))
        for k, a in gs.items():
            per_wk.setdefault(k[1], []).append((k[2], a))
        tots = [(k[1], a) for k, a in cs.items() if k[0] == "cgnat"]
        mem = cs.get(("mem",))
        for i, at in enumerate(times):
            out = {"at": at, "interfaces": [], "cgnat": None, "workers": [],
                   "memory_used_bytes": None}
            for name in sorted(per_if):
                row = {f: a[i] for f, a in per_if[name]}
                if MISSING not in row.values():
                    out["interfaces"].append({"name": name, "counters": row})
            tot = {f: a[i] for f, a in tots}
            if tot and MISSING not in tot.values():
                out["cgnat"] = tot
            for w in sorted(per_wk):
                row = {f: a[i] for f, a in per_wk[w]}
                if all(v == v for v in row.values()):
                    row["worker"], row["cpu"] = w, int(row.get("cpu", w))
                    row["busy_window_ms"] = int(row.get("busy_window_ms", 0))
                    out["workers"].append(row)
            if mem is not None and mem[i] != MISSING:
                out["memory_used_bytes"] = mem[i]
            yield out

    def dump(self):
        """The history as JSON-able data, for the file a restart reads back."""
        with self.lock:
            return {"version": 1, "times": list(self.times),
                    "counters": {"\t".join(map(str, k)): list(a)
                                 for k, a in self.counters.items()},
                    "gauges": {"\t".join(map(str, k)): [None if v != v else v for v in a]
                               for k, a in self.gauges.items()}}

    def load(self, data, now=None, interval=HISTORY_INTERVAL):
        """
        A dump() read back: what is no older than the samples kept span, and
        nothing at all from a file this does not understand.
        """
        if not isinstance(data, dict) or data.get("version") != 1:
            return False
        times = [float(t) for t in data.get("times", [])]
        horizon = (now or time.time()) - self.keep * interval
        first = next((i for i, t in enumerate(times) if t > horizon), len(times))
        n = len(times) - first
        with self.lock:
            self.times = array.array("d", times[first:])
            self.counters = {history_key(k): array.array("Q", [int(x) for x in v[first:]])
                             for k, v in data.get("counters", {}).items() if len(v) == len(times)}
            self.gauges = {history_key(k): array.array("d", [float("nan") if x is None else float(x)
                                                     for x in v[first:]])
                           for k, v in data.get("gauges", {}).items() if len(v) == len(times)}
        return n > 0

    def save(self, path=HISTORY_PATH):
        """Written whole, by rename, so a reader never sees half of it."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".new"
            with open(tmp, "w") as f:
                json.dump(self.dump(), f, separators=(",", ":"))
            os.replace(tmp, path)
            return True
        except OSError:
            return False

    def restore(self, path=HISTORY_PATH):
        """The file save() wrote, if there is one this understands."""
        try:
            with open(path) as f:
                return self.load(json.load(f))
        except (OSError, ValueError):
            return False


def history_key(text):
    """A series' key as dump() wrote it ("if\tpg0\trx_bytes"), as a tuple."""
    parts = text.split("\t")
    return tuple(int(p) if parts[0] == "wk" and j == 1 and is_number(p) else p
                 for j, p in enumerate(parts))


def take_sample(history, stats, worker_cpus="", meminfo=None, now=None, meter=None):
    """
    One sample of everything the history keeps, read from the statistics
    segment and meminfo; a part that cannot be read is missing from it, not
    zero.
    """
    try:
        ifs = interface_samples(stats)
    except Exception:  # noqa: BLE001 - a dataplane that is restarting
        ifs = []
    try:
        cg = cgnat_totals(stats)[0]
    except Exception:  # noqa: BLE001 - a dataplane with no CGNAT counters
        cg = None
    try:
        wk = worker_load(stats, worker_cpus, meter.busy() if meter else None)
    except Exception:  # noqa: BLE001
        wk = []
    mem = memory_of(meminfo)["memory_used_bytes"] or None
    history.add(now or time.time(), ifs, cg, wk, mem)


def health_status(service, stats_ok):
    """
    grpc.health.v1's answer for `service`: "SERVING", "NOT_SERVING", or None
    for a service this server does not serve.
    """
    served = ("", "prisma.v1.ConfigService", "prisma.v1.OperationalService",
              "prisma.v1.TelemetryService", "prisma.v1.RouteService",
              "prisma.v1.SessionService")
    if service not in served:
        return None
    if service in ("prisma.v1.OperationalService", "prisma.v1.TelemetryService") \
            and not stats_ok:
        return "NOT_SERVING"
    return "SERVING"


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
def build(sessions, stats_factory, pb, rpc, grpc, history=None, health=None,
          meter=None):
    """
    The servicers, bound to `sessions`, a statistics reader factory and the
    counter history: prisma.v1's five, and grpc.health.v1's when its
    modules `health` (messages, services) are given.
    """
    from google.protobuf.timestamp_pb2 import Timestamp
    history = history if history is not None else History()
    meter = meter if meter is not None else BusyMeter()

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
            load = []
            try:
                load = worker_load(stats_factory(), c["worker_cpus"], meter.busy())
            except Exception:  # noqa: BLE001 - no dataplane, no workers to say
                pass
            return pb.SystemInfo(hostname=self._ask(context, "hostname"),
                                 uptime_seconds=up, cpu_text=cpu, memory_text=mem,
                                 hugepages=pages,
                                 worker_load=[pb.WorkerLoad(**w) for w in load],
                                 **memory_of(),
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
                                       description=r["description"],
                                       speed_bps=r.get("speed_bps", 0))
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
        def ListPolicies(self, req, context):
            return pb.ListPoliciesResponse(policies=[
                pb.Policy(name=p["name"], default_action=p["default_action"],
                          text=p["text"], applied=p["applied"],
                          terms=[pb.PolicyTerm(**t) for t in p["terms"]])
                for p in self._ask(context, "policies")])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def GetPolicyCounters(self, req, context):
            got = self._ask(context, "policy_counters", req.policy)
            if "error" in got:
                context.abort(grpc.StatusCode.NOT_FOUND, got["error"])
            return pb.GetPolicyCountersResponse(uses=[
                pb.PolicyUseCounters(
                    policy=u["policy"], attach_point=u["use"],
                    applied_at=u["where"], route_map=u["route_map"],
                    daemon=u["daemon"], called=u["called"],
                    counted=u["counted"],
                    terms=[pb.PolicyTermHits(**t) for t in u["terms"]])
                for u in got["uses"]])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def TestPolicy(self, req, context):
            got = self._ask(context, "test_policy", {
                "policy": req.policy, "prefix": req.prefix,
                "use": req.attach_point, "attributes": dict(req.attributes),
                "rib": req.rib, "vrf": req.vrf, "neighbor": req.neighbor})
            if "error" in got:
                code = (grpc.StatusCode.NOT_FOUND
                        if got["error"].startswith("no policy-statement")
                        else grpc.StatusCode.INVALID_ARGUMENT)
                context.abort(code, " ".join([got["error"]] + got["more"]))
            steps = got.pop("steps")
            got["attach_point"] = got.pop("use")
            return pb.TestPolicyResponse(
                steps=[pb.TestPolicyStep(**x) for x in steps], **got)

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
            acct = account(context)
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
                        try:
                            e["revision"] = event_revision(
                                lambda: sessions.ops_worker(acct).ask("running"), after)
                        except WorkerError:
                            pass
                        yield pb.TelemetryUpdate(at=ts(now), event=pb.Event(**e))
                    state = after
                time.sleep(interval)

        # not a unit: a streaming rpc, driven by lab/api-grpc.sh.
        def GetHistory(self, req, context):
            account(context)
            since = req.since.seconds + req.since.nanos / 1e9 if req.HasField("since") else 0.0
            # None of the four asked is all four: a bare request is the day.
            want = (req.interface_counters, req.cgnat_totals, req.workers, req.memory)
            if not any(want):
                want = (True, True, True, True)
            for smp in history.samples(since, list(req.interfaces), *want):
                at = Timestamp()
                at.FromNanoseconds(int(smp["at"] * 1e9))
                out = pb.HistorySample(
                    at=at,
                    interfaces=[pb.InterfaceSample(name=i["name"],
                                                   counters=pb.InterfaceCounters(**i["counters"]))
                                for i in smp["interfaces"]],
                    workers=[pb.WorkerLoad(**w) for w in smp["workers"]])
                if smp["cgnat"] is not None:
                    out.cgnat.CopyFrom(pb.CgnatTotals(**smp["cgnat"]))
                if smp["memory_used_bytes"] is not None:
                    out.memory_used_bytes = smp["memory_used_bytes"]
                yield out

    class Routes(rpc.RouteServiceServicer):
        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListRoutes(self, req, context):
            acct = account(context)
            try:
                got = sessions.route_worker(acct).ask("routes", {
                    "vrf": req.vrf, "family": req.family, "prefix": req.prefix,
                    "protocol": req.protocol, "selected_only": req.selected_only,
                    "page_size": req.page_size, "page_token": req.page_token})
            except WorkerError as e:
                context.abort(grpc.StatusCode.UNAVAILABLE, str(e))
            if "error" in got:
                code = {"invalid": grpc.StatusCode.INVALID_ARGUMENT,
                        "not_found": grpc.StatusCode.NOT_FOUND,
                        "expired": grpc.StatusCode.FAILED_PRECONDITION}.get(
                            got["code"], grpc.StatusCode.UNAVAILABLE)
                context.abort(code, got["error"])
            out = pb.ListRoutesResponse(next_page_token=got["next"], total=got["total"],
                                        total_known=got["total_known"],
                                        snapshot_at=ts(got["at"]))
            for r in got["rows"]:
                hops = r.pop("next_hops")
                out.routes.add(next_hops=[pb.RouteNextHop(**h) for h in hops], **r)
            return out

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def GetRouteSummary(self, req, context):
            acct = account(context)
            try:
                rows = sessions.ops_worker(acct).ask("route_summary", req.vrf)
            except WorkerError as e:
                context.abort(grpc.StatusCode.UNAVAILABLE, str(e))
            return pb.GetRouteSummaryResponse(counts=[pb.RouteCount(**c) for c in rows])

    class Session(rpc.SessionServiceServicer):
        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def WhoAmI(self, req, context):
            try:
                return pb.WhoAmIResponse(**sessions.ops_worker(account(context)).ask("whoami"))
            except WorkerError as e:
                context.abort(grpc.StatusCode.UNAVAILABLE, str(e))

    servicers = [Config(), Operational(), Telemetry(), Routes(), Session()]
    if health is not None:
        servicers.append(health_servicer(stats_factory, health[0], health[1], grpc))
    return servicers


# not a unit: grpc.health.v1's servicer, a class over health_status() and
# stats_ok(), which the unit tests drive; lab/api-grpc.sh asks it.
def health_servicer(stats_factory, hpb, hrpc, grpc):
    """grpc.health.v1.Health: health_status() for each service asked."""
    seen = {"at": 0.0, "ok": False}

    # not a unit: a closure; stats_ok() below is the reading it caches.
    def ok():
        if time.monotonic() - seen["at"] > 2:
            seen["ok"], seen["at"] = stats_ok(stats_factory), time.monotonic()
        return seen["ok"]

    # not a unit: the servicer class, driven by lab/api-grpc.sh.
    class Health(hrpc.HealthServicer):
        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Check(self, req, context):
            st = health_status(req.service, ok())
            if st is None:
                context.abort(grpc.StatusCode.NOT_FOUND, f"no service {req.service!r}")
            return hpb.HealthCheckResponse(status=getattr(hpb.HealthCheckResponse, st))

        # not a unit: a streaming rpc, driven by lab/api-grpc.sh.
        def Watch(self, req, context):
            last = None
            while context.is_active():
                st = health_status(req.service, ok()) or "SERVICE_UNKNOWN"
                if st != last:
                    last = st
                    yield hpb.HealthCheckResponse(status=getattr(hpb.HealthCheckResponse, st))
                time.sleep(2)
    return Health()


def stats_ok(stats_factory):
    """Does the dataplane's statistics segment answer?"""
    try:
        s = stats_factory()
        s["/sys/heartbeat"]
        return True
    except Exception:  # noqa: BLE001 - any failure is a segment that does not answer
        return False


# not a unit: the sampler's thread body - take_sample() is what it does each
# time and is tested; lab/api-grpc.sh runs this with a one-second interval.
def sampler(history, stats_factory, stop, meter, path=HISTORY_PATH,
            interval=HISTORY_INTERVAL):
    """Sample every `interval` seconds, keep the file current, until `stop`."""
    stats, n = None, 0
    while not stop.wait(interval):
        try:
            if stats is None:
                stats = stats_factory()
            take_sample(history, stats, startup_worker_cpus(), meter=meter)
        except Exception:  # noqa: BLE001 - reconnect at the next tick
            stats = None
            history.add(time.time())
        n += 1
        if n % HISTORY_SAVE_EVERY == 0:
            history.save(path)


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
    from health.v1 import health_pb2 as hpb, health_pb2_grpc as hrpc
    history = History()
    if history.restore():
        print(f"apiserver: history read back, {len(history.times)} samples", flush=True)
    stop = threading.Event()
    meter = BusyMeter()
    threading.Thread(target=sampler, args=(history, stats_factory, stop, meter),
                     daemon=True).start()
    cfg, ops, tel, routes, session, health = build(sessions, stats_factory, pb, rpc,
                                                   grpc, history, (hpb, hrpc), meter)
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
    rpc.add_RouteServiceServicer_to_server(routes, server)
    rpc.add_SessionServiceServicer_to_server(session, server)
    hrpc.add_HealthServicer_to_server(health, server)
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
    # The history outlives the server in its file: an upgrade restarts this,
    # and a client's graphs go on from where they were.
    stop.set()
    history.save()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
