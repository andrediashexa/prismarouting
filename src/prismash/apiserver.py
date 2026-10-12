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

import cgnatshow  # noqa: E402 - the CGNAT's screens, typed (#558, phase 6)
import logstate  # noqa: E402 - the CGNAT log export's state (#555)
import opshow  # noqa: E402 - the rest of the screens, typed (#558, phase 7)
import typedcfg  # noqa: E402 - typed configuration (#558, phase 2)
import transceiver  # noqa: E402 - a module's EEPROM, decoded (#590)
import tuneadvice  # noqa: E402 - the box's own tuning, judged (#596)
import cpuview  # noqa: E402 - the CPUs one by one (#598)
from tuneadvice import busy_between, runtime_clocks  # noqa: E402,F401 - #598

#: Where the server listens inside the management container: the backplane
#: address the host forwards the management address to (#448's bp0).
DEFAULT_LISTEN = "128.0.0.2:50051"
#: The certificates, under the directory the container shares with the host.
API_DIR = os.environ.get("PRISMA_API_DIR", "/etc/prisma-routing/api")
#: A candidate nobody has touched for this long is closed. A client reads it
#: back as ListCandidatesResponse.idle_seconds and each candidate's
#: expires_at (#558) rather than assuming it; PRISMA_API_CANDIDATE_IDLE is
#: for lab/api-grpc.sh, which expires one in seconds.
CANDIDATE_IDLE = int(os.environ.get("PRISMA_API_CANDIDATE_IDLE", "") or 1800)
#: Candidates the server closed for idleness, remembered so a call naming
#: one answers "expired" and not "never existed" (#558) - at most this many.
EXPIRED_KEEP = 1000
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
#: /proc/stat, which the sampler reads each CPU's usage from (#598): the
#: kernel's, whatever namespace reads it - the container sees the host's CPUs.
PROC_STAT = os.environ.get("PRISMA_PROC_STAT", "/proc/stat")

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


# ---------------------------------------------- why a change was refused

#: A refusal's reason, as prisma.v1's Diagnostics.Reason names it (#558).
#: The order is the order refusal_reason() tests in: the first that matches
#: is the answer, so a commit refused at a gate is not also "validation".
REASONS = ("stale_base", "permission", "candidate_expired", "candidate_unknown",
           "limit", "gate_remap", "gate_diverge", "gate_cutoff",
           "confirm_pending", "grammar", "validation", "apply_failed",
           "usage", "other", "unsaved", "busy")
#: config.text_error's verdict: "<what>: <size> bytes, and the limit is <n>".
LIMIT_RE = re.compile(r"^(?:error: )?(.+?): (\d+) bytes, and the limit is (\d+)$",
                      re.M)
#: The VTY's sentences for each refusal, as prismash.py prints them. Each is
#: a sentence the VTY prints only for that refusal - test/test_apiserver.py
#: holds this table to prismash.py's own source, so a reworded message turns
#: the suite red instead of quietly answering "other".
REFUSAL_TEXTS = (
    ("gate_remap", "if that is what you mean, type: commit remap"),
    ("gate_diverge", "if that is what you mean, type: commit diverge"),
    ("gate_cutoff", "if that is what you mean, type: commit cutoff"),
    ("confirm_pending", "the box is still on the unconfirmed configuration"),
    ("confirm_pending", "cannot confirm: "),
    ("grammar", "statement(s) in this batch were refused"),
    ("grammar", "there are lines this VTY does not "),
    ("grammar", "not a statement FRR accepts here"),
    ("grammar", "% Unknown command"),
    ("validation", "candidate did not pass validation"),
    ("apply_failed", "commit failed; rolling back to the previous state"),
    ("apply_failed", "FRR refused "),
    ("usage", "usage: commit confirmed"),
    ("usage", "expected 1-60"),
    # #558 phase 5: the actions.
    ("usage", "usage: clear "),
    ("usage", "usage: system upgrade [<version>]"),
    ("other", "no candidate configuration"),
)


def refusal_reason(output, stage="commit", moved_to="", expired=None):
    """
    Why the VTY refused, as a code a client can act on (#558): {"reason",
    "running_revision", "limit_field", "limit", "limit_size"}. `moved_to` is
    the running revision when the refusal is a base that moved; `expired`
    True/False when the refusal is a candidate the server does not hold
    (closed for idleness, or never opened). `stage` is where it was refused,
    "edit" or "commit", and decides what a refusal no sentence names is: the
    prompt refuses a line by its grammar, a commit by its validation. The
    VTY's words stay the human message - this reads them, it does not
    replace them.
    """
    out = {"reason": "other", "running_revision": "", "limit_field": "",
           "limit": 0, "limit_size": 0}
    if moved_to:
        out.update(reason="stale_base", running_revision=moved_to)
        return out
    if expired is not None:
        out["reason"] = "candidate_expired" if expired else "candidate_unknown"
        return out
    text = output or ""
    if denied(text):
        out["reason"] = "permission"
        return out
    # A typed request whose own fields cannot become a line (#558, phase 2):
    # the request's words, not the VTY's grammar, were wrong.
    if stage == "usage":
        out["reason"] = "usage"
        return out
    m = LIMIT_RE.search(text)
    if m:
        out.update(reason="limit", limit_field=m.group(1).strip(),
                   limit_size=int(m.group(2)), limit=int(m.group(3)))
        return out
    for reason, phrase in REFUSAL_TEXTS:
        if phrase in text:
            out["reason"] = reason
            return out
    # Anything else: at entry the prompt refuses a line by its grammar, at a
    # commit the candidate by what it means. An answer with no error at all
    # stays "other" - it is not a refusal this table knows.
    if any(d["severity"] == "error" for d in parse_output(text)):
        out["reason"] = {"edit": "grammar", "action": "other"}.get(stage,
                                                                   "validation")
    return out


def check_reason(res, gates):
    """
    What a Commit of a checked candidate would be refused for (#558), or "".
    `commit check` itself never refuses at a gate - it reports - so a check
    that passed with gate items still answers the gate a Commit would meet.
    """
    if res["failed"]:
        return refusal_reason(res["output"], stage="commit")["reason"]
    for reason, key in (("gate_remap", "remap"), ("gate_diverge", "diverge"),
                        ("gate_cutoff", "cutoff")):
        if gates.get(key):
            return reason
    return ""


def pending_confirm(confirm, state, running, now=None):
    """
    The `commit confirmed` window the box holds (#558), or {"pending": False}.

    From the box, never from a client's memory: `state` is the config state
    file in /run (ConfigState.read(): confirm_pending, confirm_deadline,
    commit_by, commit_at, comment), which every account can read, and
    `confirm` the root-owned window file (ConfirmPending.read()), which a
    read-only account cannot - so the deadline comes from either, the window
    file's minutes and revert attempts when it was readable. `running` is the
    running configuration's text: the unconfirmed revision is its revision.
    """
    pending = bool(confirm) or state.get("confirm_pending") == "yes"
    if not pending:
        return {"pending": False}
    deadline = 0.0
    if confirm and confirm.get("deadline"):
        deadline = float(confirm["deadline"])
    else:
        try:
            deadline = float(state.get("confirm_deadline") or 0)
        except ValueError:
            deadline = 0.0
    try:
        at = int(state.get("commit_at") or 0)
    except ValueError:
        at = 0
    return {"pending": True, "revision": revision_of(running or ""),
            "deadline": deadline, "account": state.get("commit_by", ""),
            "comment": state.get("comment", ""), "committed_at": at,
            "minutes": int((confirm or {}).get("minutes") or 0),
            "revert_attempts": int((confirm or {}).get("attempts") or 0),
            "overdue": bool(deadline) and (now or time.time()) > deadline}


def staged_lines(base, text):
    """How many lines a candidate changes against its base: + and - lines."""
    return sum(1 for ln in difflib.unified_diff(base.splitlines(),
                                                text.splitlines(), lineterm="")
               if ln[:1] in "+-" and not ln.startswith(("+++", "---")))


def wall_of(mono, now_mono=None, now_wall=None):
    """A time.monotonic() reading as a wall-clock time."""
    return (now_wall if now_wall is not None else time.time()) - \
        ((now_mono if now_mono is not None else time.monotonic()) - mono)


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
            "speed_bps": link_speed_bps(d.get("link_speed")),
        })
    return rows


def link_speed_bps(kbps):
    """sw_interface_details' link_speed (kbps) in bits a second, 0 unknown.

    VPP reports a speed it does not know as ~0, 0xFFFFFFFF kbps - a virtio
    port on the X520 bench read 4294967295000 bps from 0.149.2 (#545). That
    is "unknown", which the contract says is 0, never a 4.3 Tbps link.
    """
    try:
        k = int(kbps or 0)
    except (TypeError, ValueError):
        return 0
    if k <= 0 or k >= 0xFFFFFFFF:
        return 0
    return k * 1000


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
        out.append({"name": name,
                    "mode": "bpa" if p.mode == "dynamic" else p.mode,
                    "policy": p.policy,
                    "block_size": int(p.block_size or 0),
                    # What the dataplane translates and translates to: a
                    # prefix-list's effective sets, when a side names one
                    # (#589), with the list and what it leaves out beside.
                    "prefixes_inside": list(getattr(p, "eff_inside",
                                                    p.inside)),
                    "prefixes_outside": list(getattr(p, "eff_outside",
                                                     p.outside)),
                    "prefix_list_inside": getattr(p, "inside_list", ""),
                    "prefix_list_outside": getattr(p, "outside_list", ""),
                    "untranslated": list(getattr(p, "untranslated", [])),
                    "outside_excluded": list(getattr(p, "outside_excluded",
                                                     [])),
                    "capacity": int(d.get("capacity", 0) or 0),
                    "guaranteed": int(d.get("guaranteed", 0) or 0),
                    # #572: the level the pool is written under.
                    "level": getattr(p, "level", "cgnat")})
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
                    "family": int(r.get("family")
                                  or (6 if any(":" in a for a in addrs)
                                      else 4)),
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


# ------------------------------------------- the CGNAT's screens (#558, phase 6)

#: What each typed CGNAT read asks the VTY, and the parser that reads the
#: answer. The command is the VTY's own spelling: the same Shell runs it, so
#: the rpc is that screen, never a second reading of the dataplane.
CGNAT_READS = {
    "translation": ("show cgnat translation", cgnatshow.parse_translation),
    "top": ("show cgnat top", cgnatshow.parse_top),
    "drops": ("show cgnat drops", cgnatshow.parse_drops),
    "blocks": ("show cgnat blocks", cgnatshow.parse_blocks),
    "identity": ("show cgnat identity", cgnatshow.parse_identity),
    "history": ("show cgnat history verify", cgnatshow.parse_history_verify),
    "alg": ("show cgnat alg", cgnatshow.parse_alg),
    "forward": ("show cgnat forward", cgnatshow.parse_forward),
}


def cgnat_command(kind, args=None):
    """
    The VTY line a typed CGNAT read sends, its arguments checked first. They
    travel into a VTY line, so an argument that is not the one thing it
    names - a word with a space, a `|` the VTY reads as a pipe - is refused
    here (ValueError) rather than handed on.
    """
    import ipaddress
    args = args or {}
    base = CGNAT_READS[kind][0]
    if kind == "translation":
        try:
            addr = ipaddress.IPv4Address(str(args.get("public_address", "")))
        except ValueError:
            raise ValueError(f"'{args.get('public_address', '')}' is not an IPv4 "
                             "address: a pool's public addresses are IPv4")
        port = args.get("port", 0)
        if not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError(f"port {port} is not 0-65535")
        return f"{base} {addr} {port}"
    if kind == "top":
        n = args.get("count", 0)
        if not isinstance(n, int) or n < 0:
            raise ValueError(f"count {n} is not a number of rows")
        return f"{base} {n}" if n else base
    if kind == "forward":
        pool = args.get("pool", "")
        if pool and not re.fullmatch(r"[^\s|]{1,63}", pool):
            raise ValueError(f"'{pool}' is not a pool's name")
        return f"{base} {pool}" if pool else base
    return base


def cgnat_read(sh, kind, args=None):
    """
    A typed CGNAT read in the worker: the VTY's screen for it, whether it
    failed - the dataplane not answering, or refusing the command in its own
    words (cgnatshow.screen_error) - and the screen's fields. A refused
    argument is {"error": ...}.
    """
    try:
        command = cgnat_command(kind, args)
    except ValueError as e:
        return {"error": str(e)}
    if not sh.dp.reachable():
        return {"text": "the dataplane is not answering", "failed": True,
                "fields": {}}
    text = screen(sh, command)
    return {"text": text, "failed": bool(cgnatshow.screen_error(text)),
            "fields": CGNAT_READS[kind][1](text)}


def cgnat_memory_read(sh):
    """
    `show cgnat memory` in the worker: Shell.cgnat_memory_facts() and the
    screen drawn from that one reading, so its numbers are the fields'.
    """
    f = sh.cgnat_memory_facts()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        sh.show_cgnat_memory(f)
    return {"facts": f, "text": buf.getvalue()}


def memory_fields(facts):
    """CgnatMemory's fields from Shell.cgnat_memory_facts(); None stays None."""
    if facts is None:
        return None
    return {"heap_bytes": facts["heap_bytes"],
            "heap_used_bytes": facts["heap_used_bytes"],
            "sessions": facts["sessions"] or 0,
            "sessions_known": facts["sessions"] is not None,
            "sessions_fit": facts["sessions_fit"],
            "refused_no_memory": facts["refused_no_memory"],
            "given_gb": facts["given_gb"] or 0,
            "given": facts["given_gb"] is not None,
            "given_in_effect": bool(facts["given_in_effect"])}


def ha_read(sh, path=None):
    """
    `show cgnat ha [peer <path>]` in the worker: Shell.ha_facts() at the
    peer copy's default path, or at `path` (#558), and the screen drawn
    from that one reading - the one `show cgnat ha peer <path>` prints.

    The worker has become the account (become()), so the file is read with
    that account's permissions, as its VTY session reads it; and
    read_peer_config() reads only a regular file of PEER_CONFIG_MAX at
    most, for the VTY and the API alike.
    """
    f = sh.ha_facts(path or None)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        sh.show_ha(["peer", path] if path else [], f)
    return {"facts": f, "text": buf.getvalue()}


def ha_peer_path_error(path):
    """
    Why GetHaStatusRequest.peer_path is refused before any worker reads it,
    or None: empty (the default copy) and an absolute path are taken. A
    relative one is not - the VTY resolves it against the session's own
    directory, and an API worker has none an operator chose - nor a NUL,
    which no path holds, nor one longer than PATH_MAX.
    """
    if not path:
        return None
    if "\0" in path or len(path) > 4096:
        return "peer_path: not a path"
    if not path.startswith("/"):
        return (f"peer_path: {path!r} is not an absolute path; the copy is "
                f"named by where it is on the box, e.g. "
                f"/etc/prisma-routing/peer.conf")
    return None


def ha_fields(facts):
    """
    HaStatus's fields from Shell.ha_facts(), the Timestamp aside: the
    routers as HaRouter, the divergences as HaDivergence, and `agree` - true
    only when the peer was read and nothing diverges, never on a question
    left unanswered.
    """
    routers = [{"interface": str(r.get("interface", "")),
                "vr_id": int(r.get("vr_id") or 0),
                "state": str(r.get("state", "")),
                "addresses": [str(a) for a in r.get("addresses", [])],
                "virtual_mac": str(r.get("mac") or ""),
                "priority_configured": int(r.get("configured") or 0),
                "priority_advertised": int(r.get("advertising") or 0),
                "family": int(r.get("family") or 4)}
               for r in facts["routers"]]
    return {"dataplane": facts["dataplane"], "fingerprint": facts["fingerprint"],
            "pools": list(facts["pools"]), "routers": routers,
            "peer_path": facts["peer_path"], "peer_read": facts["peer_read"],
            "peer_unread": facts["peer_unread"] or "",
            "peer_fingerprint": facts["peer_fingerprint"],
            "peer_pools": list(facts["peer_pools"]),
            "pair_findings": [str(x) for x in facts["pair_findings"]],
            "routers_either": facts["routers_either"],
            "divergences": [{"pool": p, "here": h or "", "there": t or ""}
                            for p, h, t in facts["divergences"]],
            "agree": bool(facts["dataplane"] and facts["peer_read"]
                          and not facts["divergences"])}


def facts_screen(sh, read, draw):
    """
    A screen drawn from facts (#558, phase 7): `read()` once, the screen
    `draw(facts)` prints from that reading, and both back - so an rpc's
    fields and the text it carries are one reading, as phase 6's memory and
    HA screens are.
    """
    f = read()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        draw(f)
    return {"facts": f, "text": buf.getvalue()}


#: `show snmp mib` and `show snmp zabbix-template` (#558, phase 7): the
#: VTY's command, the file it reads, and how the file is handed out.
SNMP_FILES = {
    "mib": ("show snmp mib", "SNMP_MIB_TEMPLATE", "PRISMA-ROUTING-MIB.txt",
            "text/plain"),
    "zabbix": ("show snmp zabbix-template", "ZABBIX_TEMPLATE",
               "prisma-routing-zabbix.json", "application/json"),
}


def snmp_file_read(sh, kind):
    """
    The MIB or the Zabbix template as the VTY prints it: the file with the
    running enterprise number in it, or the VTY's own refusal - no number
    yet, or the file missing. {"ok", "content" | "refusal", "pen"}.
    """
    import prismash as P
    command, attr, _, _ = SNMP_FILES[kind]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        content = sh.text_with_pen(getattr(P, attr))
    pen = sh.running_config().snmp.get("enterprise-number", "")
    if content:
        return {"ok": True, "content": content, "pen": str(pen)}
    return {"ok": False, "refusal": screen(sh, command).strip(), "pen": str(pen)}


#: The probes the API runs (#558, phase 7), the VTY's three.
PROBES = ("ping", "traceroute", "mtr")


def probe_stream(sh, args):
    """
    A probe for the API, in the account's process: the VTY's own statement
    decides it - Shell.do_probe() reads the host, the count and the source
    and refuses what it would refuse at the prompt - and builds the argv;
    the argv is then run here or through the host's door, as the VTY runs
    it, and its output yielded as it comes.

    Yields {"refused": text, "absent": bool} once when the VTY refuses it,
    else {"out": chunk} per chunk, {"tick": True} while the tool is quiet,
    and {"rc": status, "refusal": text} last.
    """
    import doors
    tool = args["tool"]
    if tool not in PROBES:
        yield {"refused": f"'{tool}' is not a probe", "absent": False}
        return
    words = [args["host"]] if args.get("host") else []
    if args.get("count"):
        words += ["count", str(args["count"])]
    if args.get("source"):
        words += ["source", args["source"]]
    got = {}
    # THE VTY'S DECISION, AND ONLY ITS ARGV KEPT: do_probe validates exactly
    # as at the prompt and hands run_probe() what it would run; this
    # instance's run_probe keeps it instead of running it to the console.
    sh.run_probe = lambda _tool, argv: got.setdefault("argv", list(argv))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            sh.do_probe(tool, words)
    finally:
        del sh.run_probe
    if "argv" not in got:
        text = buf.getvalue().strip()
        yield {"refused": text, "absent": f"this box has no {tool}" in text}
        return
    if os.environ.get("PRISMA_IN_MGMT"):
        chunks = doors.probe_chunks(got["argv"], ticks=True)
    else:
        chunks = doors.run_chunks(got["argv"], ticks=True)
    try:
        for m in chunks:
            if m[0] == "out":
                yield {"out": m[1]}
            elif m[0] == "tick":
                yield {"tick": True}
            else:
                yield {"rc": m[1], "refusal": m[2] if len(m) > 2 else ""}
    finally:
        chunks.close()


#: Worker operations that answer in parts (#558, phase 7): a generator
#: whose items go back one by one.
STREAMS = {
    "probe": probe_stream,
}


def stream_to(conn, items):
    """
    Send a stream's items down the worker's pipe as ("part", item), then
    ("ok", None) - or ("err", why). Between items the parent may say
    "cancel" (its caller went away: the generator is closed, which stops the
    probe) or None (the worker is closing). Returns False when the worker
    must stop.
    """
    try:
        for item in items:
            conn.send(("part", item))
            if conn.poll(0):
                msg = conn.recv()
                if msg == "cancel" or msg is None:
                    items.close()
                    conn.send(("ok", None))
                    return msg is not None
        conn.send(("ok", None))
    except Exception as e:  # noqa: BLE001 - the request fails, not the worker
        items.close()
        conn.send(("err", f"{type(e).__name__}: {e}"))
    return True


#: How long a probe's stream may go without a part (#558, phase 7): the
#: tool's deadline - its quiet stretches send ticks - with room to end.
PROBE_WAIT = 330


def probe_event_pb(pb, tool, line):
    """One line a probe printed as a ProbeEvent, with its reply or hop."""
    kind, fields = opshow.probe_line(tool, line)
    if kind == "reply":
        return pb.ProbeEvent(line=line, reply=pb.PingReply(**fields))
    if kind == "hop":
        return pb.ProbeEvent(line=line, hop=pb.TraceHop(**fields))
    if kind == "mtr_hop":
        return pb.ProbeEvent(line=line, mtr_hop=pb.MtrHop(**fields))
    return pb.ProbeEvent(line=line)


def enum_name(prefix, value):
    """A parser's word as its proto enum's name: ("SOURCE", "outside_range")
    is "SOURCE_OUTSIDE_RANGE"; an empty word is the enum's UNSPECIFIED."""
    return f"{prefix}_{(value or 'unspecified').upper()}"


def subscriber_blocks(text):
    """
    The port blocks in `show cgnat subscriber`'s screen, which the plugin
    writes `block 0  200.150.0.41  ports 1536-2047`, `holds <address> ports
    <lo>-<hi>` or `the formula gives it <address> ports <lo>-<hi>`.

    WITH the word `ports` between the address and the range. This read
    `address port-port` only, against a fixture written by hand rather than
    in the plugin's words, so GetSubscriber answered no block for every
    subscriber since 0.148.4 - measured in lab/api-grpc.sh's sixth phase,
    whose lookup of a subscriber's block found none to look up.
    """
    out = []
    for m in re.finditer(r"(\d+\.\d+\.\d+\.\d+)(?:\s*:\s*|\s+(?:ports\s+)?)(\d+)\s*-\s*(\d+)",
                         text):
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
    # Why each started, as the box recorded it (#588); "" when it has not.
    out["dataplane_start_cause"] = facts.get("dataplane_cause") or ""
    out["control_plane_start_cause"] = facts.get("control_plane_cause") or ""
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
    # The card drivers' settings `show system hardware` reads (#574).
    "drivers": lambda sh, a: sh.driver_facts(),
    # The remote syslog destinations and the watcher's word on each (#588
    # phase 3), as `show system logging` reads them.
    "logging_state": lambda sh, a: logging_state(),
    # Management in its own VRF (#580), as `show system management` reads it.
    "management": lambda sh, a: sh.management_state(),
    # Routing policy (#538): what `show policy`, `show policy <name>` and
    # `test policy` compute, as data, from the same Shell methods.
    "policies": lambda sh, a: sh.policies_data(),
    "policy_counters": lambda sh, a: sh.policy_counters_data(a or ""),
    "test_policy": lambda sh, a: sh.test_policy_data(a or {}),
    # #533: who the account is to the VTY, and the routing table typed.
    "whoami": lambda sh, a: whoami(sh),
    "routes": lambda sh, a: routes_page(sh, a or {}),
    "route_summary": lambda sh, a: route_summary(sh, a or ""),
    # #558, phase 6: the CGNAT's screens, read by the same Shell that prints
    # them; `a` is {"kind": ..., "args": {...}}.
    "cgnat": lambda sh, a: cgnat_read(sh, a["kind"], a.get("args")),
    "cgnat_memory": lambda sh, a: cgnat_memory_read(sh),
    "ha": lambda sh, a: ha_read(sh, (a or {}).get("path")),
    # #558, phase 7: the rest of the screens, each drawn from the facts the
    # rpc carries, or (show logging, FRR's own screen) read from its text.
    "ospf_interfaces": lambda sh, a: facts_screen(
        sh, sh.ospf_interface_facts, sh.show_ospf_interfaces),
    "isis_interfaces": lambda sh, a: facts_screen(
        sh, sh.isis_interface_facts, sh.show_isis_interfaces),
    "hardware": lambda sh, a: facts_screen(
        sh, sh.hardware_facts, lambda f: sh.show_hardware([], f)),
    "routing_logging": lambda sh, a: screen(sh, "show logging"),
    # #588: the box's record of events, as `show log` selects them.
    "events": lambda sh, a: events_read(sh, a or {}),
    # WatchEvents' reads (#588 phase 3): select_events()' own keywords,
    # already checked by events_watch_plan(), through the account's door.
    "events_raw": lambda sh, a: events_raw(a or {}),
    # #559 phase 2: bfdd's sessions, `show bfd peers` (or `peer <a>`) drawn
    # from the reading the fields carry; `a` is the peer, "" for every one.
    "bfd": lambda sh, a: facts_screen(
        sh, sh.bfd_facts,
        lambda f: sh.show_bfd(["peer", a] if a else ["peers"], f)),
    "snmp_file": lambda sh, a: snmp_file_read(sh, a),
    # #590: each port's module, `show interface [<name>] transceiver` drawn
    # from the reading the fields carry; `a` is the names, [] for all.
    "transceivers": lambda sh, a: facts_screen(
        sh, lambda: sh.transceiver_facts(a or []),
        lambda f: sh.show_transceivers(a or [], f)),
    # #596: `show system tuning advice`; `a` is {"seconds", "host_topology"}.
    "tuning_advice": lambda sh, a: tuning_advice_read(sh, a or {}),
    # #598: the CPUs' topology and `show system cpu cores`, drawn from it and
    # from the sampler's usage `a` ({"rows", "said"}) - nothing measured.
    "cpu_cores": lambda sh, a: facts_screen(
        sh, lambda: sh.cpu_cores_facts(a), sh.show_system_cpu_cores),
}


def cpu_usage_said(at, rows, now=None):
    """
    Where GetSystem's per-CPU usage is from, as `show system cpu cores`
    says it (#598): the sampler's last window, its length and its age; or
    that the sampler has not taken one yet.
    """
    if at is None or not rows:
        return ("usage: none yet - the API samples it once a minute, and the "
                "first window ends a minute after the server starts")
    ago = max(0, int((now if now is not None else time.time()) - at))
    return (f"usage over the API's last sample: {rows[0]['window_ms'] / 1000:.0f}"
            f" s, ended {ago} s ago")


def cpu_topology_pb(pb, t):
    """CpuTopology from cpuview.topology_fields() (#598)."""
    return pb.CpuTopology(
        model=t["model"], sockets=t["sockets"], physical_cores=t["physical_cores"],
        threads_per_core=t["threads_per_core"], numa_nodes=t["numa_nodes"],
        logical_cpus=t["logical_cpus"],
        socket_cores=[pb.CpuSocket(**s) for s in t["socket_cores"]],
        cpus=[pb.LogicalCpu(**c) for c in t["cpus"]])


def tuning_advice_read(sh, a):
    """
    GetTuningAdvice's reading (#596): the Shell's one window of facts and the
    advice decided from it, the screen drawn from that pair and the fields
    from the same pair - so the text and the fields cannot disagree, and the
    window is measured once.
    """
    seconds = int(a.get("seconds") or tuneadvice.WINDOW_DEFAULT)
    f, adv = sh.tuning_advice(seconds, a.get("host_topology") or "")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        sh.show_system_tuning_advice([], f, adv)
    return {"fields": tuneadvice.advice_fields(f, adv),
            "text": buf.getvalue()}


# ------------------------------------------------------------- who am I (#533)

def whoami(sh):
    """
    The account this process is and the role the VTY gives it: the Shell's
    own capabilities, read off the process's groups when it started - the
    same answer the VTY login gives, not a second reading of the groups.
    """
    import pwd
    from prismash import CAP_ALL, CAP_CGNAT, CAP_OPERATOR, role_name
    caps = set(getattr(sh, "caps", set()) or set())
    return {"account": pwd.getpwuid(os.geteuid()).pw_name,
            "role": role_name(caps),
            "can_configure": CAP_ALL in caps,
            "can_configure_cgnat": CAP_CGNAT in caps,
            "can_operate": CAP_OPERATOR in caps}


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


def typed_config(sh, args):
    """
    The typed configuration of the areas asked for (#558, phase 2): of the
    candidate this worker holds when `source` is "candidate", else of the
    running configuration - the same Config objects `show running-config`
    and `show this` print, never text parsed again.
    """
    running = args.get("source") != "candidate"
    cfg = sh.running_config() if running else sh.candidate
    if cfg is None:
        raise ValueError("no candidate is open in this session")
    out = typedcfg.config_typed(cfg, args.get("areas") or ())
    # The revision of the same read, so GetConfig does not read it twice:
    # the second read is a second trip to the dataplane per call.
    out["revision"] = revision_of(cfg.text()) if running else ""
    return out


def typed_lines(sh, args):
    """(path, lines) that set one object of this worker's candidate."""
    if sh.candidate is None:
        raise ValueError("no candidate is open in this session")
    path, lines = typedcfg.edit_for(sh.candidate, args["area"], args["object"],
                                    args.get("fields") or ())
    return {"path": path, "lines": lines}


def typed_delete(sh, args):
    """(path, lines) that remove one object from this worker's candidate:
    read off the candidate itself, because a BGP neighbour is removed by the
    statements it has there (#558 phase 3)."""
    if sh.candidate is None:
        raise ValueError("no candidate is open in this session")
    path, lines = typedcfg.delete_for(
        sh.candidate, args.get("area", ""), args.get("name", ""),
        args.get("transport", ""), args.get("prefix", ""),
        args.get("next_hop", ""), args.get("asn", ""), args.get("vrf", ""),
        args.get("bfd_peer"))
    return {"path": path, "lines": lines}


def typed_schema(sh, args):
    """
    The configuration grammar of the modes asked for (every one when none
    is): typedcfg.schema() off the VTY's own tree, with the release, a hash,
    and - when asked - what each dynamic source holds now.
    """
    import prismash as P
    modes = typedcfg.schema(P, args.get("modes") or ())
    out = {"modes": modes, "digest": typedcfg.schema_digest(modes),
           "release": P.prisma_routing_version(), "values": {},
           "box_owned": [{"pattern": p, "why": w} for p, w in typedcfg.BOX_OWNED]}
    if args.get("with_values"):
        for hint, fn in P.Shell.DYNAMIC.items():
            source = fn[len("dyn_"):] if fn.startswith("dyn_") else fn
            if source in out["values"]:
                continue
            try:
                out["values"][source] = sorted(sh.dynamic_options(hint, ""))
            except Exception:  # noqa: BLE001 - a source that cannot answer has none
                out["values"][source] = []
    return out


#: An argument a Clear puts on a VTY line (#558, phase 5): one word - an
#: interface, an address, a system id - never a second statement.
CLEAR_WORD = re.compile(r"^[A-Za-z0-9_.:/-]{1,64}$")
#: ClearRequest's targets, as the VTY line each one is.
CLEAR_LINES = {
    "interface_counters": "clear interface counters {name}",
    "cgnat_subscriber": "clear cgnat subscriber {name}",
    "bgp": "clear bgp {name}{mode}",
    "ospf_process": "clear ospf process",
    "ospf_neighbor": "clear ospf neighbor {name}",
    "isis_adjacency": "clear isis adjacency {name}",
    "configuration_pending": "clear configuration pending",
}
#: BgpMode, as the words after the neighbour.
BGP_MODES = {"hard": "", "soft": " soft", "soft_in": " soft in",
             "soft_out": " soft out"}


def clear_line(target, name="", bgp="hard"):
    """
    The VTY line a ClearRequest is, or None when its fields cannot make one:
    an unknown target, a name that is not one word, a name missing where the
    target needs one (#558, phase 5).
    """
    tmpl = CLEAR_LINES.get(target)
    if tmpl is None or bgp not in BGP_MODES:
        return None
    if name and not CLEAR_WORD.match(name):
        return None
    if "{name}" in tmpl and not name and target not in (
            "interface_counters", "isis_adjacency"):
        return None
    return " ".join(tmpl.format(name=name, mode=BGP_MODES[bgp]).split())


def system_cost(sh):
    """
    What a reboot or a shutdown would lose, as the VTY reads it before
    either (#558, phase 5): {"unsaved", "confirm_pending", "said"} - from
    say_unsaved() and say_unconfirmed() themselves, so the API refuses on
    exactly what the VTY warns of.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        sh.say_unsaved()
        sh.say_unconfirmed()
    said = buf.getvalue()
    return {"unsaved": "has NOT been saved" in said,
            "confirm_pending": "has NOT been confirmed" in said, "said": said}


#: How long the management API is away after an upgrade recreated its
#: container: the client's reconnect hint, measured on the X520 bench at
#: 0.149.4 (74 s from the upgrade command to the API answering, most of it
#: the upgrade itself) and rounded up for the restart alone.
API_AWAY_SECONDS = 60
#: The exit line the API's detached runs end with (doors.api_run_argv).
RUN_EXIT = re.compile(r"^prisma-api-exit (\d+)$")


def upgrade_phases(text):
    """
    What `system upgrade <version>` has printed so far, as UpgradeProgress
    steps (#558, phase 5): a list of {"phase", "message", "version",
    "newest"}, in order - read off the VTY's own lines, which
    test/test_apiserver.py holds to prismash.py's source.
    """
    steps, installing = [], False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = RUN_EXIT.match(line.strip())
        if m:
            if m.group(1) != "0" and not any(x["phase"] == "failed" for x in steps):
                steps.append({"phase": "failed", "message":
                              f"the upgrade exited {m.group(1)}"})
            continue
        if line.startswith("newest published: "):
            steps.append({"phase": "checked", "message": line,
                          "newest": line.split(": ", 1)[1].strip()})
        elif line.startswith("downloading "):
            steps.append({"phase": "download", "message": line})
        elif line.startswith("the package matches its published sha256"):
            steps.append({"phase": "verify", "message": line})
            installing = True
        elif line.startswith("upgraded to "):
            installing = False
            steps.append({"phase": "installed", "message": line,
                          "version": line.split()[-1]})
        elif line.strip().startswith("the VTY restarts on") and \
                any(x["phase"] == "installed" for x in steps) and \
                not any(x["phase"] == "api_restart" for x in steps):
            # The VTY's own sentence after `upgraded to`, once. The package's
            # script says "the VTY restarts on the new release" too, during
            # the install: ending the stream there would lose INSTALLED.
            steps.append({"phase": "api_restart", "message": line.strip()})
        elif line.startswith("error:"):
            installing = False
            steps.append({"phase": "failed", "message": line})
        elif installing and not line.startswith("    "):
            steps.append({"phase": "install", "message": line.strip()})
    return steps


def run_log(path):
    """
    A detached run's log (doors.api_run_argv) as {"text", "done", "status",
    "started"}: done once its exit line is there; None when there is none.
    """
    try:
        with open(path) as f:
            text = f.read()
        started = os.stat(path).st_mtime
    except OSError:
        return None
    status = None
    for line in text.splitlines():
        m = RUN_EXIT.match(line.strip())
        if m:
            status = int(m.group(1))
    return {"text": text, "done": status is not None, "status": status,
            "started": started}


#: How long a detached run may take to write its first line (#564): its
#: shell makes the log as its first act, so a log still missing after this
#: is a unit that never got as far as its command.
FIRST_WRITE_WAIT = 20


def run_since(path, started):
    """run_log(path), but only the run that started at `started` or later:
    a previous run's finished log is not this one's result (#564)."""
    log = run_log(path)
    if log is None or log["started"] < started - 1:
        return None
    return log


def run_silent(log, started, now, first=FIRST_WRITE_WAIT):
    """
    True when a run started at `started` has written nothing by `now`, past
    `first` seconds (#564): the unit did not get to its command, and waiting
    longer would only turn a clear failure into a deadline. Measured on
    10.40.200.166: the unit failed at STDOUT and the API waited 180 s.
    """
    return log is None and now - started > first


def silent_reason(unit):
    """What a client is told when a detached run never wrote (#564)."""
    return (f"the host started {unit} and it wrote nothing in "
            f"{FIRST_WRITE_WAIT} s; nothing was changed by it - "
            f"'journalctl -u {unit}' on the box says why")


def run_busy(path, now=None, stale=1800):
    """Is a detached run at `path` still going: a log with no exit line,
    written in the last `stale` seconds (a run that died says nothing)."""
    got = run_log(path)
    if got is None or got["done"]:
        return False
    try:
        age = (now or time.time()) - os.stat(path).st_mtime
    except OSError:
        return False
    return age < stale


def hostrun(sh, kind, version=""):
    """
    Start `kind` (doors.API_RUNS) on the host, detached, through the doors,
    as the account `sh` is: the VTY's permission first - `system upgrade`
    and `system dataplane restart` need the system actions, root's and
    prisma-operator's members' (#563) - then the host's door, which lets the
    same two in. {"ok", "output"}.
    """
    import doors
    import prismash as P
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        allowed = sh.require(P.CAP_OPERATOR, doors.API_RUNS[kind])
    if not allowed:
        return {"ok": False, "output": buf.getvalue()}
    argv = doors.api_run_argv(kind, version)
    if argv is None:
        return {"ok": False, "output": "usage: system upgrade [<version>], "
                                       "as in 0.149.25"}
    # Before the host is asked, as the account (#588): the host's VTY runs
    # it as root, and a start later is matched to this request.
    sh.record_restart_asked(kind, version)
    rc, out = sh.sys._run(list(argv))
    if rc != 0:
        return {"ok": False, "output": f"error: the host would not start it\n{out}"}
    return {"ok": True, "output": out}


def events_filters(req, now=None):
    """
    ListEvents' request as `show log`'s filters (#588): (filters, None), or
    (None, why) - the VTY's own words - for a value it refuses. Read through
    the same parser, so the API and the prompt cannot disagree about what
    `since 2h` means.
    """
    import events
    words = []
    if req.get("last"):
        words += ["last", str(req["last"])]
    for k in ("since", "severity", "source", "kind"):
        if req.get(k):
            words += [k, str(req[k])]
    return events.parse_show_args(words, now)


def events_read(sh, req):
    """
    The events ListEvents answers, and `show log`'s screen of them: {"ok",
    "events", "text", "why"}. Read by the account's own Shell, so what the
    host's door gives it is what that account's `show log` shows.
    """
    import events
    f, why = events_filters(req)
    if why:
        return {"ok": False, "events": [], "text": "", "why": why}
    got = events.fetch(f)
    when = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
    text = "".join(events.format_event(e, when) + "\n" for e in got)
    return {"ok": True, "events": got, "text": text, "why": ""}


def events_watch_plan(req, now=None):
    """
    WatchEvents' request (ListEventsRequest's fields) as `monitor log`
    reads its words (#588 phase 3): (backlog, follow, None) or (None,
    None, why). `backlog` is the filters of what is sent first - None when
    neither `last` nor `since` is given, and the stream starts now; `follow`
    selects what is sent as it is recorded.
    """
    import events
    words = []
    if req.get("last"):
        words += ["last", str(req["last"])]
    for k in ("since", "severity", "source", "kind"):
        if req.get(k):
            words += [k, str(req[k])]
    f, _seconds, why = events.parse_follow_args(words, now)
    if why:
        return None, None, why
    follow = {k: v for k, v in f.items() if k not in ("last", "since")}
    return (f if ("last" in f or "since" in f) else None), follow, None


def events_raw(filters):
    """The events select_events() picks with `filters`, through the host's
    door as `show log` reads them; [] when the filters are not its own."""
    import events
    keys = ("last", "since", "severity", "source", "kind")
    if not isinstance(filters, dict) or any(k not in keys for k in filters):
        return []
    return events.fetch(filters)


def logging_state(now=None):
    """GetSystem's `logging` (#588 phase 3): eventfwd.status_fields() of
    the destinations configured, against the watcher's last status."""
    import eventfwd
    status = eventfwd.read_status()
    return eventfwd.status_fields(eventfwd.destinations(status), status,
                                  time.time() if now is None else now)


def box_event_pb(pb, ev):
    """One event as a BoxEvent of `pb` (#588 phase 3, WatchEvents): its
    fields, and its time as a Timestamp to the nanosecond."""
    f = event_pb_fields(ev)
    t = f.pop("seconds")
    out = pb.BoxEvent(**f)
    out.time.seconds = int(t)
    out.time.nanos = int(round((t - int(t)) * 1e9))
    return out


def event_pb_fields(ev):
    """One event as ListEvents' Event's fields (#588)."""
    return {"seconds": ev.get("t", 0), "severity": ev.get("severity", ""),
            "source": ev.get("source", ""), "text": ev.get("text", ""),
            "account": ev.get("who", ""), "origin": ev.get("from", ""),
            "kind": ev.get("kind", ""), "component": ev.get("component", ""),
            "cause": ev.get("cause", ""), "subject": ev.get("subject", ""),
            "reason": ev.get("reason", "")}


def segment_readable(path=None):
    """Can this account read the log's event segment, as the VTY monitor
    would (#558, phase 5)? (ok, why)."""
    import logmon
    p = path or logmon.SEGMENT
    if os.access(p, os.R_OK):
        return True, ""
    if not os.path.exists(p):
        return False, (f"no event segment at {p}: this dataplane keeps its "
                       "events in its own heap, or it is not running")
    return False, f"this account may not read {p}"


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
    if op == "pending_confirm":
        return pending_confirm(sh.confirm.read(), sh.state.read(),
                               sh.running_config().text())
    if op == "screen":
        return screen(sh, args)
    if op == "typed":
        return typed_config(sh, args or {})
    if op == "typed_lines":
        return typed_lines(sh, args or {})
    if op == "typed_delete":
        return typed_delete(sh, args or {})
    if op == "schema":
        return typed_schema(sh, args or {})
    if op == "system_cost":
        return system_cost(sh)
    if op == "hostrun":
        return hostrun(sh, args["kind"], args.get("version", ""))
    if op == "segment_readable":
        return segment_readable(args)
    if op in READERS:
        return READERS[op](sh, args)
    raise ValueError(f"unknown worker operation {op!r}")


# -------------------------------------------------------- the account's process
def account_key(account, getpwnam=None, getgrouplist=None):
    """
    What a worker becomes for `account`, read now (#565): its uid, its gid
    and every group it is in - or None when the account does not exist.

    A worker takes its groups once, when it starts (become()), so a role or
    `operator` given or taken after that did not reach the account's calls
    until the server restarted: measured on 10.95.200.198, prisma-web was
    refused a dataplane restart with prisma-operator already in its groups on
    the host and in the container. A worker whose key no longer matches is a
    worker of an account that has changed.
    """
    import pwd
    getpwnam = getpwnam or pwd.getpwnam
    getgrouplist = getgrouplist or os.getgrouplist
    try:
        pw = getpwnam(account)
    except KeyError:
        return None
    return (pw.pw_uid, pw.pw_gid,
            tuple(sorted(set(getgrouplist(account, pw.pw_gid)))))



def become(account):
    """Turn this process into `account`: its groups, its gid, its uid, its env."""
    import pwd
    pw = pwd.getpwnam(account)
    if os.geteuid() == 0:
        os.initgroups(account, pw.pw_gid)
        os.setgid(pw.pw_gid)
        os.setuid(pw.pw_uid)
    # PRISMA_EVENT_SOURCE: what this account does through the API is the
    # API's in the box's record of events, not the VTY's (#588).
    os.environ.update({"HOME": pw.pw_dir, "USER": account, "LOGNAME": account,
                       "PRISMA_EVENT_SOURCE": "api"})


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
        # A cancel that crossed a stream's own end (#558, phase 7): the
        # stream it was for is over, and the parent's drain read its end.
        if msg == "cancel":
            continue
        op, args = msg
        if op in STREAMS:
            if not stream_to(conn, STREAMS[op](sh, args)):
                return
            continue
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
        # Read before the process becomes the account (#565): the identity
        # it will hold for as long as it lives.
        self.key = account_key(account)
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

    def ask_stream(self, op, args=None, timeout=None):
        """
        `op` answered in parts by the account's Shell (#558, phase 7): yields
        each part as it comes. The process is held for the whole stream, as
        the console is held for a probe. A caller that stops early - its
        client cancelled - closes this generator: the process is told
        "cancel", which stops the probe, and a thread reads what is still in
        flight so the next request starts on a clean pipe.
        """
        wait = self.timeout if timeout is None else timeout
        self.lock.acquire()
        done = False
        try:
            self.used = time.monotonic()
            self.conn.send((op, args))
            while True:
                if not self.conn.poll(wait):
                    raise WorkerError(f"{op}: nothing in {wait}s")
                state, value = self.conn.recv()
                if state == "part":
                    yield value
                    continue
                done = True
                if state != "ok":
                    raise WorkerError(value)
                return
        except (EOFError, OSError, BrokenPipeError) as e:
            done = True
            self.close()
            raise WorkerError(f"{op}: the session ended ({e})")
        finally:
            if done:
                self.lock.release()
            else:
                try:
                    self.conn.send("cancel")
                except (OSError, BrokenPipeError):
                    pass
                threading.Thread(target=self._drain, args=(wait,),
                                 daemon=True).start()

    def _drain(self, wait):
        """After a cancel: read until the stream's end, then free the process
        for the next request; a process that never ends it is stopped."""
        try:
            while self.conn.poll(wait):
                state, _ = self.conn.recv()
                if state != "part":
                    return
            self.close()
        except (EOFError, OSError, BrokenPipeError):
            pass
        finally:
            self.used = time.monotonic()
            self.lock.release()

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
        self.candidates = {}   # id -> {"account", "worker", "base", "created"}
        self.expired = {}      # id -> account, the candidates reap() closed
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
            self.candidates[cid] = {"account": account, "worker": w, "base": base,
                                    "created": time.time()}
        return cid, base, text, res["output"]

    def was_expired(self, account, cid):
        """Did reap() close `cid`, `account`'s, for idleness? (#558)"""
        with self.lock:
            return self.expired.get(cid) == account

    def timing(self, c):
        """A candidate's created, last activity and expiry, wall-clock (#558)."""
        last = wall_of(c["worker"].used)
        return {"created": c.get("created", last), "last_activity": last,
                "expires_at": last + self.idle}

    def listing(self, account):
        """
        `account`'s open candidates, oldest first, each {"id", "base",
        "created", "last_activity", "expires_at", "staged"} (#558).

        Asking a candidate's process for its text would count as using it and
        push its expiry back - so a client polling ListCandidates would keep
        every candidate alive for ever. Its last use is put back after.
        """
        self.reap()
        with self.lock:
            mine = [(k, c) for k, c in self.candidates.items()
                    if c["account"] == account]
        out = []
        for cid, c in sorted(mine, key=lambda kc: kc[1].get("created", 0)):
            used = c["worker"].used
            try:
                text = c["worker"].ask("candidate") or ""
            except Exception:  # noqa: BLE001 - a dead process lists as unknown
                text = None
            c["worker"].used = used
            row = {"id": cid, "base": c["base"], **self.timing(c),
                   "staged": staged_lines(c["base"], text) if text is not None else 0}
            out.append(row)
        return out

    def current(self, w, account):
        """
        Is `w` still a worker of the `account` there is now (#565)? False
        when the account's uid, gid or groups changed since it started - a
        role or `operator` given or taken - and the worker must be replaced.
        A worker that recorded nothing (a test's) is taken as current.
        """
        key = getattr(w, "key", None)
        return key is None or key == account_key(account)

    def get(self, account, cid):
        """
        The candidate `cid` of `account`; KeyError for anybody else's or none.

        A candidate opened before the account changed holds the rights it was
        opened with (#565); it is closed and reads as expired, so a commit
        never runs with rights the account no longer has - or without ones it
        has just been given.
        """
        self.reap()
        with self.lock:
            c = self.candidates.get(cid)
        if c is None or c["account"] != account:
            raise KeyError(cid)
        if not self.current(c["worker"], account):
            with self.lock:
                self.candidates.pop(cid, None)
                self.expired[cid] = account
            c["worker"].close()
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
        if w is not None and w.proc.is_alive() and not self.current(w, account):
            w.close()
            w = None
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
        if w is not None and w.proc.is_alive() and not self.current(w, account):
            w.close()
            w = None
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
        if w is not None and w.proc.is_alive() and not self.current(w, account):
            w.close()
            w = None
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
            for k in stale:
                self.expired[k] = self.candidates[k]["account"]
            while len(self.expired) > EXPIRED_KEEP:
                self.expired.pop(next(iter(self.expired)))
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


def startup_worker_cpus(path=None, key="corelist-workers"):
    """The `corelist-workers` of the dataplane's startup.conf, or ''; or
    another `key`'s value - `main-core` for startup_main_cpu() (#598)."""
    try:
        with open(path or os.environ.get("PRISMA_STARTUP_CONF",
                                         "/etc/vpp/startup.conf")) as f:
            for raw in f:
                w = raw.split()
                if w[:1] == [key] and len(w) > 1:
                    return w[1]
    except OSError:
        pass
    return ""


def startup_main_cpu(path=None):
    """The CPU VPP's main thread is pinned to, from startup.conf's
    `main-core`; None when it names none (#598)."""
    v = startup_worker_cpus(path, "main-core")
    return int(v) if is_number(v) else None


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


# runtime_clocks() and busy_between() are tuneadvice's since #598: the VTY's
# `show system cpu cores` measures a worker's busy ratio the way BusyMeter
# does, and two copies of one parser are two answers the first time VPP's
# `show runtime` changes.


def vpp_runtime():
    """`show runtime`, as vppctl prints it; "" when the dataplane does not answer."""
    try:
        p = subprocess.run([os.environ.get("PRISMA_VPPCTL", "vppctl"), "show", "runtime"],
                           capture_output=True, text=True, timeout=10)
        return p.stdout if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def vpp_logging():
    """`show bpa logging` - the screen `show cgnat logging` prints - as vppctl
    prints it; "" when the dataplane does not answer (#555)."""
    try:
        p = subprocess.run([os.environ.get("PRISMA_VPPCTL", "vppctl"), "show", "bpa",
                            "logging"], capture_output=True, text=True, timeout=10)
        return p.stdout if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


#: LogStream's fields, in its message's order: what logging_fields() keeps
#: of a watcher's row (the rest - delivered, waiting, failing - is the rule's
#: own bookkeeping, not the contract).
LOG_STREAM_FIELDS = ("kind", "configured", "collector", "source", "carrier",
                     "carrier_reason", "connection", "records", "bytes_sent",
                     "packets_sent", "pending_bytes", "spill_bytes", "spill_cap_bytes",
                     "dropped_over_cap", "send_failures", "dropped", "delivering",
                     "stalled_reason", "last_progress")


def logging_fields(parsed, rows, text, at):
    """
    LoggingState's fields from a LogWatch snapshot (#555), timestamps as
    epoch seconds for the servicer to wrap. The streams always all three, in
    logstate.STREAMS' order.
    """
    by = {r["kind"]: r for r in rows}
    streams = [{f: by[k][f] for f in LOG_STREAM_FIELDS} for k in logstate.STREAMS
               if k in by]
    return {"at": at, "streams": streams, "carrier": parsed["carrier"],
            "logger_pid": parsed["logger_pid"],
            "export_ring_dropped": parsed["export_dropped"],
            "worker_queue_dropped": parsed["queue_dropped"],
            "exhaustion_repeats_not_reported": parsed["exhaustion_repeats"],
            "transmit_failed": parsed["transmit_failed"],
            "transmit_interface": parsed["transmit_interface"],
            "rings_shared": parsed["rings_shared"], "text": text}


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
        # The main thread's loops a second in the last reading, VPP's own
        # header figure (#598): whether it polls. The statistics segment's
        # /sys/loops_per_worker has none for it - the lab read its 0.
        self.main_loops = None
        # Each thread's CPU, from the same reading's headers (#598).
        self.lcores = {}

    def busy(self):
        """{worker: (busy ratio, window in ms)}; {} when there is no reading."""
        with self.lock:
            if self.prev is None or self.clock() - self.prev[0] < BUSY_WINDOW:
                if self.prev is None:
                    self.prev = (self.clock(), runtime_clocks(self.read()))
                self.sleep(max(0.0, BUSY_WINDOW - (self.clock() - self.prev[0])))
            text = self.read()
            now = (self.clock(), runtime_clocks(text))
            before, self.prev = self.prev, now
            self.main_loops = (tuneadvice.parse_runtime(text).get("main")
                               or {}).get("loops_hdr")
            self.lcores = tuneadvice.runtime_lcores(text)
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


def dataplane_threads(workers, main_cpu=None, main_loops=None, lcores=None):
    """
    {cpu: {"polling", "busy_ratio"}} for the CPUs VPP's threads run on
    (#598): each worker_load() row's - polling unless it runs under
    SLEEPING_LOOPS, as worker_load() judges it - and the main thread's, by
    its loop rate (BusyMeter.main_loops) on a dataplane with no workers, with
    no busy ratio: VPP measures the main thread's graph as nobody's load.
    With workers the main thread does not poll the cards and sleeps between
    its loops: lab/api-grpc.sh's read 15% busy on its CPU at well over
    SLEEPING_LOOPS a second, where lab/vty.sh's, with no workers, read
    100%. Each thread's CPU is `lcores`
    (BusyMeter.lcores, `show runtime`'s own) where it says, and startup.conf's
    otherwise: a dataplane started with `workers 2` and no corelist, as the
    labs start it, has worker_load() say a worker's number for its CPU.
    """
    lcores = lcores or {}
    out = {}
    main_cpu = lcores.get("main", main_cpu)
    if main_cpu is not None and main_loops is not None:
        out[main_cpu] = {"polling": not workers and main_loops >= SLEEPING_LOOPS,
                         "busy_ratio": 0.0}
    for w in workers or ():
        out[int(lcores.get(int(w["worker"]), w["cpu"]))] = {
            "polling": w["loops_per_second"] >= SLEEPING_LOOPS,
            "busy_ratio": float(w["busy_ratio"])}
    return out


class CpuUsage:
    """
    Each CPU's usage between the sampler's last two readings of /proc/stat
    (#598): what GetSystem's cpu_usage says and GetHistory keeps. Read in
    the sampler, once a HISTORY_INTERVAL, and never for a request - a
    client polling GetSystem every few seconds reads the last window, it
    does not start one.
    """

    def __init__(self):
        self.prev = None        # (when, parse_proc_stat())
        self.last = (None, [])  # (when the window ended, usage rows)
        self.lock = threading.Lock()

    def observe(self, text, now, threads=None):
        """
        A /proc/stat reading taken at `now` (epoch seconds): the usage rows
        since the one before, kept as the latest; [] for the first.
        """
        cur = (now, tuneadvice.parse_proc_stat(text))
        with self.lock:
            before, self.prev = self.prev, cur
            if before is None or now <= before[0]:
                return []
            rows = cpuview.usage_rows(
                tuneadvice.cpu_window(before[1], cur[1]),
                int((now - before[0]) * 1000), threads)
            self.last = (now, rows)
        return rows

    def latest(self):
        """(when, rows) of the last window; (None, []) before there is one."""
        with self.lock:
            return self.last


# ------------------------------------------------------ counter history (#533)

#: The figures of a CpuUsage row the history keeps a series of (#598).
CPU_FIELDS = ("busy_percent", "steal_percent", "polling", "busy_ratio",
              "window_ms")


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

    def add(self, at, interfaces=(), cgnat=None, workers=(), memory_used=None,
            cpus=()):
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
            # Each CPU's usage (#598), a gauge per figure: 5 x 8 bytes x 1440
            # samples is 58 KB a CPU, 3.7 MB for 64.
            for c in cpus:
                for f in CPU_FIELDS:
                    self._put(self.gauges, "d", ("cpu", int(c["cpu"]), f),
                              float(c[f]))
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
                workers=True, memory=True, cpus=False):
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
            gs = {k: a[first:] for k, a in self.gauges.items()
                  if (k[0] == "wk" and workers) or (k[0] == "cpu" and cpus)}
        per_if, per_wk, per_cpu = {}, {}, {}
        for k, a in cs.items():
            if k[0] == "if":
                per_if.setdefault(k[1], []).append((k[2], a))
        for k, a in gs.items():
            (per_cpu if k[0] == "cpu" else per_wk).setdefault(
                k[1], []).append((k[2], a))
        tots = [(k[1], a) for k, a in cs.items() if k[0] == "cgnat"]
        mem = cs.get(("mem",))
        for i, at in enumerate(times):
            out = {"at": at, "interfaces": [], "cgnat": None, "workers": [],
                   "memory_used_bytes": None, "cpus": []}
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
            for c in sorted(per_cpu):
                row = {f: a[i] for f, a in per_cpu[c]}
                if len(row) == len(CPU_FIELDS) and all(v == v for v in row.values()):
                    row["cpu"], row["polling"] = c, bool(row["polling"])
                    row["window_ms"] = int(row["window_ms"])
                    out["cpus"].append(row)
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
    return tuple(int(p) if parts[0] in ("wk", "cpu") and j == 1 and is_number(p)
                 else p for j, p in enumerate(parts))


def take_sample(history, stats, worker_cpus="", meminfo=None, now=None, meter=None,
                cpus=None, stat=None, main_cpu=None):
    """
    One sample of everything the history keeps, read from the statistics
    segment and meminfo; a part that cannot be read is missing from it, not
    zero. With `cpus` (a CpuUsage), each CPU's usage too (#598): /proc/stat
    (`stat`, its text; read here when not given) against the last sample's,
    with what the dataplane's threads on them do.
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
    now = now or time.time()
    use = []
    if cpus is not None:
        if stat is None:
            try:
                with open(PROC_STAT) as f:
                    stat = f.read()
            except OSError:
                stat = ""
        use = cpus.observe(stat, now, dataplane_threads(
            wk, main_cpu, getattr(meter, "main_loops", None),
            getattr(meter, "lcores", None)))
    history.add(now, ifs, cg, wk, mem, use)


#: The health service's name for the CGNAT log export (#555). A name of its
#: own and not OperationalService's: a stalled syslog collector is not a box
#: whose reads fail, and a client that reads NOT_SERVING on
#: OperationalService as "do not call it" would stop reading the very state
#: that says what is wrong. "" - the server as a whole - does not include it
#: either, for the same reason.
LOG_HEALTH = "prisma.v1.LogExport"

#: The health service's name for "is the saved configuration all applied"
#: (#575): NOT_SERVING while the boot left statements out for a card the box
#: did not have. A name of its own for LOG_HEALTH's reason: a box short of
#: a card still answers every call, and the reads are how a client finds
#: out what is missing.
CONFIG_HEALTH = "prisma.v1.Configuration"

#: Where `prismash --load` records what it left out (prismash.PENDING_PATH;
#: /run/prisma-routing is mounted into the management container).
PENDING_PATH = os.environ.get(
    "PRISMA_CONFIG_PENDING", "/run/prisma-routing/config-pending.json")


def config_pending(path=None):
    """True when the boot left statements out for a missing card (#575)."""
    return os.path.exists(path or PENDING_PATH)


def pending_fields(path=None):
    """
    PendingConfiguration's fields from the record `--load` writes (#575):
    pending false and the rest empty when there is none, or when it cannot
    be read - the record is the one reader, and a box with none applied all
    of its file.
    """
    try:
        with open(path or PENDING_PATH) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        rec = None
    if not isinstance(rec, dict):
        return {"pending": False, "cards": [], "left_out": [], "since": 0,
                "path": "", "held_for_a_commit": False}
    return {"pending": True, "cards": list(rec.get("cards", [])),
            "left_out": list(rec.get("left_out", [])),
            "since": int(rec.get("since", 0)), "path": rec.get("path", ""),
            "held_for_a_commit": bool(rec.get("told"))}


def health_status(service, stats_ok, log_status=None, pending=False):
    """
    grpc.health.v1's answer for `service`: "SERVING", "NOT_SERVING", or None
    for a service this server does not serve. `log_status` is the log
    export's own answer (LogWatch.health()'s first half) for LOG_HEALTH;
    without one that name is not served. `pending` is config_pending()'s
    answer, for CONFIG_HEALTH.
    """
    if service == LOG_HEALTH:
        return log_status
    if service == CONFIG_HEALTH:
        return "NOT_SERVING" if pending else "SERVING"
    served = ("", "prisma.v1.ConfigService", "prisma.v1.OperationalService",
              "prisma.v1.TelemetryService", "prisma.v1.RouteService",
              "prisma.v1.SessionService", "prisma.v1.SystemService")
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

def status_message(output, extra=""):
    """
    The status message of a refused call: `extra` when the server says it,
    else the VTY's first ERROR - not its first diagnostic. A commit the VTY
    refused after warning about something else answered with the warning,
    and a client read "nothing here announces 200.150.0.32/30" as why its
    hostname change failed (#558's lab).
    """
    if extra:
        return extra
    diags = parse_output(output)
    errors = [d for d in diags if d["severity"] == "error"]
    return (errors or diags or [{"message": output.strip()}])[0]["message"]


#: The typed areas by the SetConfigRequest field that carries each (#558).
AREA_OF_FIELD = {"interface": "interfaces", "pool": "pools",
                 "nat64_pool": "nat64",
                 "logging": "logging", "static_route": "static",
                 "bgp_neighbor": "bgp", "system": "system",
                 "vrf_device": "vrfs", "bfd_peer": "bfd_peers",
                 "bfd_profile": "bfd_profiles"}

#: TypedConfig's repeated routing fields (#558 phase 3), by typedcfg's area.
#: The areas of #558 phase 4, as {area: (TypedConfig field, message,
#: repeated)}: the singular ones are one message, the others a list.
OTHER_FIELDS = {"system": ("system", "SystemConfig", False),
                "users": ("users", "UserConfig", True),
                "snmp_users": ("snmp_users", "SnmpUserConfig", True),
                "policy": ("policy", "PolicyConfig", False),
                "route_maps": ("route_maps", "RouteMapConfig", True),
                "frr_lists": ("frr_lists", "FrrListsConfig", False),
                "rpki": ("rpki", "RpkiConfig", False),
                "routing_attachments": ("routing_attachments",
                                        "RoutingAttachmentsConfig", False),
                "vrfs": ("vrfs", "VrfDeviceConfig", True),
                "vrrp": ("vrrp", "VrrpConfig", True),
                "bundles": ("bundles", "BundleConfig", True),
                "cgnat_globals": ("cgnat_globals", "CgnatGlobalsConfig",
                                  False)}


def other_areas_pb(pb, out, d):
    """The phase-4 areas of typed_config's answer `d`, into TypedConfig
    `out`: one message or a list each, through msg_from_dict."""
    for area, (field, msg, repeated) in OTHER_FIELDS.items():
        if area not in d:
            continue
        cls = getattr(pb, msg)
        if repeated:
            getattr(out, field).extend(msg_from_dict(cls, x)
                                       for x in d[area])
        else:
            getattr(out, field).CopyFrom(msg_from_dict(cls, d[area]))


ROUTING_FIELDS = {"static": ("static_routes", "StaticRouteConfig"),
                  "bgp": ("bgp", "BgpConfig"), "ospf": ("ospf", "OspfConfig"),
                  "ospf6": ("ospf6", "OspfConfig"),
                  "isis": ("isis", "IsisConfig"),
                  "interface_routing": ("interface_routing",
                                        "InterfaceRoutingConfig"),
                  # #559 phase 3: area "bfd" answers both lists.
                  "bfd_peers": ("bfd_peers", "BfdPeerConfig"),
                  "bfd_profiles": ("bfd_profiles", "BfdProfileConfig")}


def msg_from_dict(cls, d):
    """
    A typedcfg dict as message `cls`, field by field off the descriptor:
    nested messages and repeated messages recursively, keys the message does
    not have ignored. The routing messages are shaped after the dicts, so
    one walker serves them all instead of a function per message.
    """
    kw = {}
    for f in cls.DESCRIPTOR.fields:
        if f.name not in d or d[f.name] is None:
            continue
        v = d[f.name]
        if f.message_type is not None:
            sub = getattr(sys.modules[cls.__module__], f.message_type.name)
            if f.label == f.LABEL_REPEATED:
                kw[f.name] = [msg_from_dict(sub, x) for x in v]
            else:
                kw[f.name] = msg_from_dict(sub, v)
        else:
            kw[f.name] = v
    return cls(**kw)


def dict_from_msg(m):
    """A message as a dict typedcfg reads: every field, defaults included."""
    out = {}
    for f in m.DESCRIPTOR.fields:
        v = getattr(m, f.name)
        if f.message_type is not None:
            if f.label == f.LABEL_REPEATED:
                out[f.name] = [dict_from_msg(x) for x in v]
            else:
                out[f.name] = dict_from_msg(v)
        elif f.label == f.LABEL_REPEATED:
            out[f.name] = list(v)
        else:
            out[f.name] = v
    return out

INTERFACE_KEYS = ("name", "kind", "description", "mtu", "shutdown", "ipv4",
                  "ipv6", "cgnat_role", "vrf", "linux_name", "vlan_outer",
                  "vlan_inner", "mac_address", "urpf", "router_advertisements",
                  "pref64")
POOL_SCALARS = ("name", "block_size", "port_first", "port_last", "policy",
                "mode", "max_blocks", "initial_allocation", "block_stride",
                "reserve", "utilisation_alarm", "max_sessions",
                "max_session_rate", "tcp_mss", "filter")
#: A CGNAT pool's prefix-lists (#589), which a NAT64 pool has no field for.
POOL_LIST_NAMES = ("prefix_list_inside", "prefix_list_outside")
POOL_LISTS = ("prefix_inside", "prefix_outside", "bypass_source",
              "bypass_destination")
#: Nat64PoolConfig's (#572): a pool's, less the bypass, plus its own two.
NAT64_SCALARS = POOL_SCALARS + ("subscriber_length", "nat64_prefix")
NAT64_LISTS = ("prefix_inside", "prefix_outside")
COLLECTOR_KEYS = ("transport", "configured", "collector", "port", "source",
                  "enterprise")


def interface_pb(pb, d):
    """A typedcfg interface dict as an InterfaceConfig."""
    return pb.InterfaceConfig(**{k: d[k] for k in INTERFACE_KEYS})


def pool_pb(pb, d, nat64=False):
    """A typedcfg pool dict as a PoolConfig - or, `nat64`, a Nat64PoolConfig
    (#572); an unset VRF stays unset."""
    kw = {k: d[k] for k in (NAT64_SCALARS + NAT64_LISTS if nat64
                            else POOL_SCALARS + POOL_LIST_NAMES
                            + POOL_LISTS)}
    kw["timeouts"] = dict(d["timeouts"])
    kw["algs"] = dict(d["algs"])
    kw["forwards"] = [pb.PortForwardConfig(**f) for f in d["forwards"]]
    for k in ("vrf", "outside_vrf"):
        if d[k] is not None:
            kw[k] = d[k]
    return (pb.Nat64PoolConfig if nat64 else pb.PoolConfig)(**kw)


def logging_pb(pb, d):
    """A typedcfg logging dict as a LoggingConfig."""
    cols = []
    for c in d["collectors"]:
        kw = {k: c[k] for k in COLLECTOR_KEYS if k in c}
        for k in ("facility", "severity"):
            if c.get(k) is not None:
                kw[k] = c[k]
        cols.append(pb.LogCollectorConfig(**kw))
    return pb.LoggingConfig(enabled=d["enabled"], collectors=cols)


def _has(msg, field):
    try:
        return msg.HasField(field)
    except ValueError:
        return False


def bgp_edit_dict(m):
    """A BgpNeighborEdit as typedcfg reads it. `password` stays None when it
    was not given, so an edit that does not mean it never clears one."""
    d = dict_from_msg(m)
    if not m.password:
        d["password"] = None
    return d


def interface_dict(m):
    """
    An InterfaceConfig as typedcfg's dict, every repeated field a list.

    By shape and not by name: the dict crosses a pipe to the account's
    worker, and a protobuf repeated container does not pickle. 0.150.2 added
    `pref64` as a repeated field beside ipv4 and ipv6, this listed only those
    two by name, and every SetConfig on an interface failed with "can't
    pickle repeated message fields" - found by lab/api-grpc.sh's phase 2.
    """
    out = {}
    for k in INTERFACE_KEYS:
        v = getattr(m, k)
        out[k] = v if isinstance(v, (str, bytes, int, float, bool)) else list(v)
    return out


def pool_dict(m, nat64=False):
    """A PoolConfig - or, `nat64`, a Nat64PoolConfig (#572) - as typedcfg's
    dict; an unset VRF is None."""
    d = {k: getattr(m, k) for k in (NAT64_SCALARS if nat64
                                    else POOL_SCALARS + POOL_LIST_NAMES)}
    d.update({k: list(getattr(m, k))
              for k in (NAT64_LISTS if nat64 else POOL_LISTS)})
    d["timeouts"] = dict(m.timeouts)
    d["algs"] = dict(m.algs)
    d["forwards"] = [{"protocol": f.protocol, "public_port": f.public_port,
                      "private_address": f.private_address,
                      "private_port": f.private_port} for f in m.forwards]
    for k in ("vrf", "outside_vrf"):
        d[k] = getattr(m, k) if _has(m, k) else None
    return d


def logging_dict(m):
    """A LoggingConfig as typedcfg's dict."""
    cols = []
    for c in m.collectors:
        d = {k: getattr(c, k) for k in COLLECTOR_KEYS}
        for k in ("facility", "severity"):
            d[k] = getattr(c, k) if _has(c, k) else None
        cols.append(d)
    return {"enabled": m.enabled, "collectors": cols}


def schema_pb(pb, sch):
    """typed_schema()'s answer as a ConfigSchema."""
    out = pb.ConfigSchema(release=sch.get("release", ""),
                          digest=sch.get("digest", ""))
    for m in sch["modes"]:
        sm = out.modes.add(mode=m["mode"], path=m["path"], enter=m["enter"],
                           roles=m["roles"], typed_area=m["typed"])
        for st in m["statements"]:
            ss = sm.statements.add(words=st["words"], help=st["help"],
                                   removable=st["removable"],
                                   no_words=st["no_words"],
                                   repeatable=st["repeatable"],
                                   typed=st["typed"],
                                   read_typed=st.get("read_typed", st["typed"]))
            for form in st["forms"]:
                sf = ss.forms.add()
                for arg in form:
                    sf.arguments.add(**arg)
    for source, vals in sch.get("values", {}).items():
        out.values[source].values.extend(vals)
    for b in sch.get("box_owned", []):
        out.box_owned.add(pattern=b["pattern"], why=b["why"])
    return out


def validate_verdicts(lines, outcomes):
    """
    ValidateLines' answer: per line {"line", "ok", "reason", "message"}, from
    `outcomes`, each feed_lines()'s result for that one line.
    """
    out = []
    for line, res in zip(lines, outcomes):
        text = res["output"].strip()
        if res["failed"]:
            out.append({"line": line, "ok": False,
                        "reason": refusal_reason(res["output"], stage="edit")["reason"],
                        "message": status_message(res["output"]) or text})
        else:
            out.append({"line": line, "ok": True, "reason": "",
                        "message": text})
    return out


def _reason_pb(pb, reason):
    """refusal_reason()'s code as prisma.v1's Diagnostics.Reason value."""
    return getattr(pb.Diagnostics, "REASON_" + reason.upper(),
                   pb.Diagnostics.REASON_OTHER)


def _diag_pb(pb, d):
    sev = {"error": pb.Diagnostic.SEVERITY_ERROR,
           "warning": pb.Diagnostic.SEVERITY_WARNING,
           "note": pb.Diagnostic.SEVERITY_NOTE}.get(d["severity"],
                                                     pb.Diagnostic.SEVERITY_UNSPECIFIED)
    return pb.Diagnostic(severity=sev, message=d["message"], path=d["path"],
                         line=d["line"])


#: How long RestartDataplane waits for the host's run to finish, and an
#: Upgrade stream for its last line (#558, phase 5).
RUN_WAIT = 300
UPGRADE_WAIT = 1800


def action_reason(output):
    """
    Why an action was refused (#558, phase 5): refusal_reason() at the
    "action" stage, and the host's door refusing an account that is not
    root - which the VTY prints under its own verdict - as a permission.
    """
    if re.search(r"only root (may|and prisma-operator's members may)",
                 output or "") or "permission denied for this account" in \
            (output or ""):
        return "permission"
    return refusal_reason(output, stage="action")["reason"]


def action_code(grpc, reason):
    """The status a refused action answers with, by its reason (#558)."""
    return {"permission": grpc.StatusCode.PERMISSION_DENIED,
            "usage": grpc.StatusCode.INVALID_ARGUMENT}.get(
                reason, grpc.StatusCode.FAILED_PRECONDITION)


def watch_words(req):
    """A WatchLoggingRequest as the words after `monitor cgnat logging`, so
    logmon.parse_args() validates the API's filters exactly as the VTY's."""
    words = []
    if req.raw:
        words.append("raw")
    if req.subscriber:
        words += ["subscriber", req.subscriber]
    if req.public_address:
        words += ["public", req.public_address
                  + (f":{req.port}" if req.port else "")]
    if req.event:
        words += ["event", req.event]
    if req.pool:
        words += ["pool", req.pool]
    if req.seconds:
        words += ["seconds", str(req.seconds)]
    return words


def vpp_show(*words):
    """`show <words>` as vppctl prints it; "" when the dataplane does not
    answer."""
    try:
        p = subprocess.run([os.environ.get("PRISMA_VPPCTL", "vppctl"), "show", *words],
                           capture_output=True, text=True, timeout=10)
        return p.stdout if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _stamp(pb_ts, epoch):
    """A Timestamp set from seconds since the epoch, to the nanosecond."""
    pb_ts.FromNanoseconds(int(epoch * 1e9))


def log_event_pb(pb, e):
    """One logmon.watch() dict as prisma.v1's LogEvent (#558, phase 5)."""
    kind = {"started": "STARTED", "alloc": "ALLOC", "release": "RELEASE",
            "exhausted": "EXHAUSTED", "boot": "BOOT", "delivery": "DELIVERY",
            "lost": "LOST"}.get(e["kind"], "KIND_UNSPECIFIED")
    out = pb.LogEvent(kind=pb.LogEvent.Kind.Value(kind), text=e.get("text", ""))
    _stamp(out.at, e["at"])
    for k, f in (("subscriber", "subscriber"), ("public", "public_address"),
                 ("port_first", "port_first"), ("port_last", "port_last"),
                 ("pool", "pool"), ("pool_id", "pool_id"), ("identity", "identity"),
                 ("stream", "stream"), ("collector", "collector"),
                 ("carrier", "carrier"), ("connection", "connection"),
                 ("sent", "sent"), ("lost", "lost"),
                 ("pending_bytes", "pending_bytes"), ("exported", "exported"),
                 ("from_watch_ring", "from_watch_ring"),
                 ("pools_named", "pools_named")):
        v = e.get(k)
        if v not in (None, ""):
            setattr(out, f, v)
    if e.get("boot_wall"):
        _stamp(out.boot_time, e["boot_wall"])
    return out


def progress_pb(pb, st, started=None, output=""):
    """One upgrade_phases() step as prisma.v1's UpgradeProgress."""
    out = pb.UpgradeProgress(
        phase=pb.UpgradeProgress.Phase.Value(st["phase"].upper()),
        message=st.get("message", ""), version=st.get("version", ""),
        newest=st.get("newest", ""),
        api_away_seconds=st.get("api_away_seconds", 0), output=output)
    if started:
        _stamp(out.started_at, started)
    if st["phase"] == "failed":
        out.diagnostics.CopyFrom(pb.Diagnostics(
            output=output, reason=_reason_pb(pb, refusal_reason(
                output, stage="action")["reason"])))
    return out


# not a unit: builds the gRPC servicers around the core above; it needs grpc
# and the generated stubs, and lab/api-grpc.sh drives every rpc through it.
def build(sessions, stats_factory, pb, rpc, grpc, history=None, health=None,
          meter=None, logwatch=None, cpus=None):
    """
    The servicers, bound to `sessions`, a statistics reader factory and the
    counter history: prisma.v1's five, and grpc.health.v1's when its
    modules `health` (messages, services) are given.
    """
    from google.protobuf.timestamp_pb2 import Timestamp
    history = history if history is not None else History()
    meter = meter if meter is not None else BusyMeter()
    cpus = cpus if cpus is not None else CpuUsage()

    # not a unit: a closure of build(), driven by lab/api-grpc.sh.
    def account(context):
        a = account_from_auth(context.auth_context())
        if a is None:
            context.abort(grpc.StatusCode.UNAUTHENTICATED,
                          "the client certificate names no box account")
        return a

    # not a unit: a closure of build(), driven by lab/api-grpc.sh.
    def fail(context, code, output, extra="", stage="commit", moved_to="",
             expired=None, gates=(), reason=None):
        why = refusal_reason(output, stage=stage, moved_to=moved_to, expired=expired)
        if reason:
            why["reason"] = reason
        diags = pb.Diagnostics(items=[_diag_pb(pb, d) for d in parse_output(output)],
                               output=output, reason=_reason_pb(pb, why["reason"]),
                               running_revision=why["running_revision"],
                               limit_field=why["limit_field"], limit=why["limit"],
                               limit_size=why["limit_size"],
                               gate_items=list(gates) if why["reason"].startswith("gate_")
                               else [])
        context.set_trailing_metadata((("prisma-diagnostics-bin",
                                        diags.SerializeToString()),))
        context.abort(code, status_message(output, extra)[:1000] or "refused")

    # not a unit: a closure of build(), driven by lab/api-grpc.sh.
    def gone(context, acct, cid):
        """A candidate this account does not hold: expired, or never (#558)."""
        expired = sessions.was_expired(acct, cid)
        fail(context, grpc.StatusCode.NOT_FOUND, "", expired=expired,
             extra=(f"candidate {cid} expired: unused for {sessions.idle} s"
                    if expired else f"no candidate {cid}"))

    # not a unit: a closure of build(), driven by lab/api-grpc.sh.
    def candidate(context, cid):
        acct = account(context)
        try:
            return sessions.get(acct, cid)
        except KeyError:
            gone(context, acct, cid)

    # not a unit: a closure of build(), driven by lab/api-grpc.sh.
    def ts(epoch):
        t = Timestamp()
        t.FromSeconds(int(epoch))
        return t

    # not a unit: a closure of build(), driven by lab/api-grpc.sh;
    # opshow.bfd_session_fields() builds what it converts, and is tested.
    def bfd_session_pb(s):
        f = dict(s)
        return pb.BfdSession(state=pb.BfdSession.State.Value(f.pop("state")), **f)

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
            out = pb.OpenCandidateResponse(candidate_id=cid,
                                           base_revision=revision_of(base), text=text,
                                           idle_seconds=int(sessions.idle))
            try:
                out.expires_at.CopyFrom(ts(sessions.timing(
                    sessions.get(acct, cid))["expires_at"]))
            except KeyError:
                pass
            return out

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Edit(self, req, context):
            c = candidate(context, req.candidate_id)
            res = c["worker"].ask("feed", edit_lines(req.path, req.lines))
            if res["failed"]:
                code = (grpc.StatusCode.PERMISSION_DENIED if denied(res["output"])
                        else grpc.StatusCode.INVALID_ARGUMENT)
                c["worker"].ask("feed", ["top"])
                fail(context, code, res["output"], stage="edit")
            out = pb.EditResponse(diagnostics=[_diag_pb(pb, d) for d in
                                               parse_output(res["output"])],
                                  output=res["output"])
            t = sessions.timing(c)
            out.last_activity.CopyFrom(ts(t["last_activity"]))
            out.expires_at.CopyFrom(ts(t["expires_at"]))
            return out

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
            why = check_reason(res, g)
            return pb.CheckResponse(
                passed=not res["failed"],
                diagnostics=[_diag_pb(pb, d) for d in parse_output(res["output"])],
                remap=g["remap"], cutoff=g["cutoff"], diverge=g["diverge"],
                output=res["output"],
                reason=_reason_pb(pb, why) if why else 0)

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Commit(self, req, context):
            acct = account(context)
            try:
                c = sessions.get(acct, req.candidate_id)
            except KeyError:
                gone(context, acct, req.candidate_id)
            c["worker"].ask("feed", ["top"])
            r = commit(sessions, acct, req.candidate_id, comment=req.comment,
                       confirmed_minutes=req.confirmed_minutes,
                       remap=req.acknowledge_remap, cutoff=req.acknowledge_cutoff,
                       diverge=req.acknowledge_diverge)
            if r["moved"]:
                fail(context, grpc.StatusCode.FAILED_PRECONDITION, r["moved"],
                     "the running configuration changed since this candidate "
                     "was opened; open a new candidate and apply again",
                     moved_to=r["revision"])
            if not r["ok"]:
                code = (grpc.StatusCode.PERMISSION_DENIED if denied(r["output"])
                        else grpc.StatusCode.FAILED_PRECONDITION)
                g = c["worker"].ask("gates")
                fail(context, code, r["output"],
                     gates=g["remap"] + g["diverge"] + g["cutoff"])
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
                fail(context, grpc.StatusCode.INVALID_ARGUMENT, res["output"], stage="edit")
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
            acct = account(context)
            try:
                sessions.close(acct, req.candidate_id)
            except KeyError:
                gone(context, acct, req.candidate_id)
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

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def GetPendingConfirm(self, req, context):
            p = sessions.ops_worker(account(context)).ask("pending_confirm")
            out = pb.PendingConfirm(pending=p["pending"])
            if p["pending"]:
                out.revision = p["revision"]
                out.account = p["account"]
                out.comment = p["comment"]
                out.minutes = p["minutes"]
                out.revert_attempts = p["revert_attempts"]
                out.overdue = p["overdue"]
                if p["deadline"]:
                    out.deadline.CopyFrom(ts(p["deadline"]))
                if p["committed_at"]:
                    out.committed_at.CopyFrom(ts(p["committed_at"]))
            return out

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListCandidates(self, req, context):
            out = pb.ListCandidatesResponse(idle_seconds=int(sessions.idle))
            for row in sessions.listing(account(context)):
                ci = out.candidates.add(candidate_id=row["id"],
                                        base_revision=revision_of(row["base"]),
                                        staged_lines=row["staged"])
                ci.created_at.CopyFrom(ts(row["created"]))
                ci.last_activity.CopyFrom(ts(row["last_activity"]))
                ci.expires_at.CopyFrom(ts(row["expires_at"]))
            return out

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def GetConfig(self, req, context):
            args = {"areas": list(req.areas)}
            try:
                if req.candidate_id:
                    c = candidate(context, req.candidate_id)
                    d = c["worker"].ask("typed", dict(args, source="candidate"))
                    out = pb.TypedConfig(source="candidate",
                                         candidate_id=req.candidate_id)
                else:
                    w = sessions.ops_worker(account(context))
                    d = w.ask("typed", dict(args, source="running"))
                    out = pb.TypedConfig(source="running", revision=d["revision"])
            except WorkerError as e:
                fail(context, grpc.StatusCode.INVALID_ARGUMENT,
                     f"error: {str(e).split(': ', 1)[-1]}", stage="usage")
            out.interfaces.extend(interface_pb(pb, x) for x in d.get("interfaces", []))
            out.pools.extend(pool_pb(pb, x) for x in d.get("pools", []))
            out.nat64_pools.extend(pool_pb(pb, x, nat64=True)
                                   for x in d.get("nat64", []))
            if "logging" in d:
                out.logging.CopyFrom(logging_pb(pb, d["logging"]))
            for b in d.get("box_owned", []):
                out.box_owned.add(line=b["line"], why=b["why"])
            for area, (field, msg) in ROUTING_FIELDS.items():
                getattr(out, field).extend(
                    msg_from_dict(getattr(pb, msg), x) for x in d.get(area, []))
            other_areas_pb(pb, out, d)
            return out

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def _stage(self, context, c, path, lines):
            """Lines at `path`, staged as Edit stages them: the reply."""
            # The lines a client is shown have a BGP password masked: the
            # box is given the real ones, the reply never echoes a secret -
            # a DeleteConfig's would be one the client never sent.
            out = pb.SetConfigResponse(path=path,
                                       lines=typedcfg.shown_lines(lines))
            if lines:
                res = c["worker"].ask("feed", edit_lines(path, lines))
                said = "\n".join(typedcfg.shown_lines(
                    res["output"].splitlines()))
                if res["failed"]:
                    code = (grpc.StatusCode.PERMISSION_DENIED
                            if denied(res["output"])
                            else grpc.StatusCode.INVALID_ARGUMENT)
                    c["worker"].ask("feed", ["top"])
                    fail(context, code, said, stage="edit")
                out.output = said
                out.diagnostics.extend(_diag_pb(pb, d)
                                       for d in parse_output(said))
            t = sessions.timing(c)
            out.last_activity.CopyFrom(ts(t["last_activity"]))
            out.expires_at.CopyFrom(ts(t["expires_at"]))
            return out

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def SetConfig(self, req, context):
            c = candidate(context, req.candidate_id)
            which = req.WhichOneof("object")
            if which is None:
                fail(context, grpc.StatusCode.INVALID_ARGUMENT,
                     "error: name the object to set: interface, pool, "
                     "nat64_pool, logging, static_route, bgp_neighbor, "
                     "system, vrf_device, bfd_peer or bfd_profile",
                     stage="usage")
            obj = {"interface": interface_dict, "pool": pool_dict,
                   "nat64_pool": lambda m: pool_dict(m, nat64=True),
                   "logging": logging_dict, "static_route": dict_from_msg,
                   "bgp_neighbor": bgp_edit_dict, "system": dict_from_msg,
                   "vrf_device": dict_from_msg, "bfd_peer": dict_from_msg,
                   "bfd_profile": dict_from_msg}[which](getattr(req, which))
            try:
                r = c["worker"].ask("typed_lines", {
                    "area": AREA_OF_FIELD[which], "object": obj,
                    "fields": list(req.fields)})
            except WorkerError as e:
                fail(context, grpc.StatusCode.INVALID_ARGUMENT,
                     f"error: {str(e).split(': ', 1)[-1]}", stage="usage")
            return self._stage(context, c, r["path"], r["lines"])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def DeleteConfig(self, req, context):
            c = candidate(context, req.candidate_id)
            try:
                r = c["worker"].ask("typed_delete", {
                    "area": req.area, "name": req.name,
                    "transport": req.transport, "prefix": req.prefix,
                    "next_hop": req.next_hop, "asn": req.asn, "vrf": req.vrf,
                    "bfd_peer": (dict_from_msg(req.bfd_peer)
                                 if req.HasField("bfd_peer") else None)})
            except WorkerError as e:
                fail(context, grpc.StatusCode.INVALID_ARGUMENT,
                     f"error: {str(e).split(': ', 1)[-1]}", stage="usage")
            return self._stage(context, c, r["path"], r["lines"])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def GetSchema(self, req, context):
            try:
                sch = sessions.ops_worker(account(context)).ask(
                    "schema", {"modes": list(req.modes),
                               "with_values": req.with_values})
            except WorkerError as e:
                fail(context, grpc.StatusCode.INVALID_ARGUMENT,
                     f"error: {str(e).split(': ', 1)[-1]}", stage="usage")
            return schema_pb(pb, sch)

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ValidateLines(self, req, context):
            acct = account(context)
            try:
                cid, _base, _text, _ = sessions.open(acct)
            except PermissionError as e:
                fail(context, grpc.StatusCode.PERMISSION_DENIED, str(e))
            except WorkerError as e:
                context.abort(grpc.StatusCode.UNAVAILABLE, str(e))
            try:
                w = sessions.get(acct, cid)["worker"]
                outcomes = []
                for line in req.lines:
                    outcomes.append(w.ask("feed", edit_lines(req.path, [line])))
                    w.ask("feed", ["top"])
            finally:
                sessions.close(acct, cid)
            out = pb.ValidateLinesResponse()
            for v in validate_verdicts(list(req.lines), outcomes):
                out.verdicts.add(line=v["line"], ok=v["ok"],
                                 reason=_reason_pb(pb, v["reason"]) if v["reason"] else 0,
                                 message=v["message"])
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
            hw = self._ask(context, "screen", "show system hardware")
            drv = self._ask(context, "drivers")
            lg_text = self._ask(context, "screen", "show system logging")
            mg_text = self._ask(context, "screen", "show system management")
            mg = pb.ManagementState(**self._ask(context, "management"))
            lg = []
            for f in self._ask(context, "logging_state"):
                last = f.pop("last_sent")
                m = pb.LoggingDestinationState(
                    destination=pb.LoggingDestination(**f.pop("destination")),
                    **f)
                if last:
                    m.last_sent.CopyFrom(ts(last))
                lg.append(m)
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
            # #598: the topology read once a boot, the usage the sampler's last
            # window - nothing measured for this call - and the screen drawn
            # from both.
            at, rows = cpus.latest()
            cores = self._ask(context, "cpu_cores",
                              {"rows": rows, "said": cpu_usage_said(at, rows)})
            extra = {}
            if at is not None:
                extra["cpu_usage_at"] = ts(at)
            return pb.SystemInfo(hostname=self._ask(context, "hostname"),
                                 cpu_topology=cpu_topology_pb(
                                     pb, cores["facts"]["topology"]),
                                 cpu_usage=[pb.CpuUsage(**r) for r in rows],
                                 cpu_cores_text=cores["text"], **extra,
                                 uptime_seconds=up, cpu_text=cpu, memory_text=mem,
                                 hardware_text=hw,
                                 logging=lg, logging_text=lg_text,
                                 management=mg, management_text=mg_text,
                                 unsupported_sfp_configured=bool(
                                     drv["unsupported_sfp_configured"]),
                                 unsupported_sfp_booted=bool(
                                     drv["unsupported_sfp_booted"]),
                                 unsupported_sfp_booted_known=(
                                     drv["unsupported_sfp_booted"] is not None),
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

        # not a unit: an rpc, driven by lab/api-grpc.sh; cgnat_command() and
        # cgnatshow are what it does, and are tested.
        def _cgnat(self, context, kind, args=None, not_found=None):
            """A typed CGNAT read's fields and screen, or the call aborted:
            INVALID_ARGUMENT for a refused argument, NOT_FOUND when the
            screen says `not_found`, UNAVAILABLE for any other failure."""
            got = self._ask(context, "cgnat", {"kind": kind, "args": args or {}})
            if "error" in got:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, got["error"])
            if got["failed"]:
                text = got["text"].strip()
                if not_found and not_found in text:
                    context.abort(grpc.StatusCode.NOT_FOUND, text)
                context.abort(grpc.StatusCode.UNAVAILABLE,
                              text or "the dataplane did not answer")
            return got["fields"], got["text"]

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def LookupTranslation(self, req, context):
            f, text = self._cgnat(context, "translation",
                                  {"public_address": req.public_address,
                                   "port": req.port},
                                  not_found="is not a public address")
            source = pb.Translation.Source.Value(enum_name("SOURCE", f.pop("source")))
            return pb.Translation(source=source, text=text, **f)

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListTop(self, req, context):
            f, text = self._cgnat(context, "top", {"count": req.count})
            return pb.ListTopResponse(
                subscribers=[pb.TopSubscriber(**r) for r in f["rows"]],
                holding_blocks=f["holding"], text=text)

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def GetDrops(self, req, context):
            f, text = self._cgnat(context, "drops")
            return pb.DropsReport(text=text, nodes=[
                pb.DropNode(node=n["node"],
                            counters=[pb.DropCounter(**c) for c in n["counters"]])
                for n in f["nodes"]])

        # not a unit: an rpc, driven by lab/api-grpc.sh; memory_fields() is
        # what it does, and is tested.
        def GetCgnatMemory(self, req, context):
            got = self._ask(context, "cgnat_memory")
            f = memory_fields(got["facts"])
            if f is None:
                context.abort(grpc.StatusCode.UNAVAILABLE, got["text"].strip())
            return pb.CgnatMemory(text=got["text"], **f)

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListBlocks(self, req, context):
            f, text = self._cgnat(context, "blocks")
            out = pb.ListBlocksResponse(text=text)
            for p in f["pools"]:
                threads, total = p.pop("threads"), p.pop("total")
                pp = out.pools.add(**p)
                if total:
                    pp.total.CopyFrom(pb.BlockCounts(**total))
                for t in threads:
                    pp.workers.add(thread=t.pop("thread"), lent=t.pop("lent"),
                                   counts=pb.BlockCounts(**t))
            return out

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def GetIdentity(self, req, context):
            f, text = self._cgnat(context, "identity")
            return pb.IdentityState(text=text, **f)

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def VerifyHistory(self, req, context):
            f, text = self._cgnat(context, "history")
            verdict = pb.HistoryVerification.Verdict.Value(
                enum_name("VERDICT", f.pop("verdict")))
            return pb.HistoryVerification(verdict=verdict, text=text, **f)

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListAlgs(self, req, context):
            f, text = self._cgnat(context, "alg")
            return pb.ListAlgsResponse(text=text, pools=[
                pb.PoolAlgs(pool=p["pool"], algs=[pb.AlgState(**a) for a in p["algs"]])
                for p in f["pools"]])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def ListForwards(self, req, context):
            f, text = self._cgnat(context, "forward", {"pool": req.pool})
            return pb.ListForwardsResponse(
                text=text, forwards=[pb.Forward(**x) for x in f["forwards"]])

        # not a unit: an rpc, driven by lab/api-grpc.sh; ha_fields() is what
        # it does, and is tested.
        def GetHaStatus(self, req, context):
            bad = ha_peer_path_error(req.peer_path)
            if bad:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, bad)
            got = self._ask(context, "ha", {"path": req.peer_path})
            f = ha_fields(got["facts"])
            routers, div = f.pop("routers"), f.pop("divergences")
            out = pb.HaStatus(text=got["text"],
                              routers=[pb.HaRouter(**r) for r in routers],
                              divergences=[pb.HaDivergence(**d) for d in div], **f)
            if got["facts"]["peer_copied"] is not None:
                out.peer_copied.CopyFrom(ts(got["facts"]["peer_copied"]))
            return out

        # not a unit: rpcs, driven by lab/api-grpc.sh's seventh phase; the
        # Shell's *_facts(), opshow's readers and probe_stream() are what
        # they do, and are tested.
        def ListOspfInterfaces(self, req, context):
            got = self._ask(context, "ospf_interfaces")
            f = opshow.ospf_fields(got["facts"])
            return pb.OspfInterfaces(
                answered=f["answered"], text=got["text"],
                interfaces=[pb.OspfInterface(**r) for r in f["interfaces"]])

        # not a unit: an rpc, driven by lab/api-grpc.sh's seventh phase.
        def ListIsisInterfaces(self, req, context):
            got = self._ask(context, "isis_interfaces")
            f = opshow.isis_fields(got["facts"])
            rows = f.pop("circuits")
            return pb.IsisInterfaces(
                text=got["text"], circuits=[pb.IsisInterface(**r) for r in rows],
                **f)

        # not a unit: an rpc, driven by lab/api-grpc.sh's seventh phase.
        def GetHardware(self, req, context):
            got = self._ask(context, "hardware")
            f = opshow.hardware_fields(got["facts"])
            nics = f.pop("nics")
            return pb.Hardware(text=got["text"],
                               nics=[pb.HardwareNic(**n) for n in nics], **f)

        # not a unit: an rpc, driven by lab/api-grpc.sh and
        # lab/transceiver.sh; transceiver.transceiver_fields() is what it
        # converts, and is tested.
        def ListTransceivers(self, req, context):
            got = self._ask(context, "transceivers", list(req.names))
            f = got["facts"]
            return pb.Transceivers(
                dataplane_answered=f["dataplane"], missing=f["missing"],
                text=got["text"],
                transceivers=[pb.Transceiver(**transceiver.transceiver_fields(m))
                              for m in f["modules"]])

        # not a unit: an rpc, driven by lab/api-grpc.sh's phase for #596;
        # tuning_advice_read() and tuneadvice.advice_fields() are what it
        # converts, and are tested.
        def GetTuningAdvice(self, req, context):
            if req.seconds > tuneadvice.WINDOW_MAX:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                              f"seconds: {tuneadvice.WINDOW_MIN} to "
                              f"{tuneadvice.WINDOW_MAX}, not {req.seconds}")
            got = self._ask(context, "tuning_advice", {
                "seconds": req.seconds, "host_topology": req.host_topology})
            f = got["fields"]
            facts = dict(f["facts"])
            cards = [pb.TuningCard(**c) for c in facts.pop("cards")]
            threads = [pb.TuningThread(**t) for t in facts.pop("threads")]
            out = pb.TuningAdvice(
                facts=pb.TuningFacts(cards=cards, threads=threads, **facts),
                findings=[pb.TuningFinding(**x) for x in f["findings"]],
                checked=f["checked"],
                recommendations=[pb.TuningRecommendation(**r)
                                 for r in f["recommendations"]],
                sizing=[pb.TuningSizingRow(**r) for r in f["sizing"]],
                text=got["text"])
            if f["snippets"]:
                out.snippets.CopyFrom(pb.TuningSnippets(**f["snippets"]))
            return out

        # not a unit: an rpc, driven by lab/api-grpc.sh's eleventh phase;
        # events_filters(), events_read() and event_pb_fields() are what it
        # does, and are tested.
        def ListEvents(self, req, context):
            got = self._ask(context, "events", {
                "last": req.last, "since": req.since,
                "severity": req.severity, "source": req.source,
                "kind": req.kind})
            if not got["ok"]:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, got["why"])
            out = pb.ListEventsResponse(text=got["text"])
            for ev in got["events"]:
                f = event_pb_fields(ev)
                t = f.pop("seconds")
                e = out.events.add(**f)
                e.time.seconds = int(t)
                e.time.nanos = int((t - int(t)) * 1e9)
            return out

        # not a unit: a streaming rpc, driven by lab/api-grpc.sh's phase for
        # #588 phase 3; events_watch_plan(), events_raw() and
        # events.follow_new() are what it does, and are tested.
        def WatchEvents(self, req, context):
            backlog, follow, why = events_watch_plan({
                "last": req.last, "since": req.since,
                "severity": req.severity, "source": req.source,
                "kind": req.kind})
            if why:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, why)
            import events
            seen, start = {}, time.time()
            events.follow_new(self._ask(context, "events_raw", dict(
                follow, since=start - events.FOLLOW_LOOKBACK)), seen, start)
            for ev in self._ask(context, "events_raw", backlog) \
                    if backlog else []:
                seen[events.event_key(ev)] = ev.get("t", 0)
                yield box_event_pb(pb, ev)
            while context.is_active():
                end = time.time() + events.FOLLOW_EVERY
                while context.is_active() and time.time() < end:
                    time.sleep(0.2)
                t = time.time()
                for ev in events.follow_new(self._ask(context, "events_raw", dict(
                        follow, since=t - events.FOLLOW_LOOKBACK)), seen, t):
                    yield box_event_pb(pb, ev)

        # not a unit: an rpc, driven by lab/api-grpc.sh's seventh phase.
        def GetRoutingLogging(self, req, context):
            text = self._ask(context, "routing_logging")
            daemons = []
            for d in opshow.parse_routing_logging(text):
                daemons.append(pb.DaemonLogging(
                    syslog=pb.LoggingTarget(**d.pop("syslog")),
                    stdout=pb.LoggingTarget(**d.pop("stdout")),
                    file=pb.LoggingTarget(**d.pop("file")), **d))
            return pb.RoutingLogging(daemons=daemons, text=text)

        # not a unit: an rpc, driven by lab/api-grpc.sh's seventh phase.
        def GetSnmpFile(self, req, context):
            kind = {pb.GetSnmpFileRequest.KIND_MIB: "mib",
                    pb.GetSnmpFileRequest.KIND_ZABBIX_TEMPLATE: "zabbix"}.get(req.kind)
            if kind is None:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                              "kind: KIND_MIB or KIND_ZABBIX_TEMPLATE")
            got = self._ask(context, "snmp_file", kind)
            if not got["ok"]:
                context.abort(grpc.StatusCode.FAILED_PRECONDITION, got["refusal"])
            _, _, filename, media = SNMP_FILES[kind]
            return pb.SnmpFile(filename=filename, media_type=media,
                               enterprise_number=got["pen"], content=got["content"])

        # not a unit: the rpcs' body, driven by lab/api-grpc.sh's seventh
        # phase; probe_stream(), probe_event_pb() and opshow are what it
        # does, and are tested.
        def _probe(self, tool, req, context):
            """A probe streamed as ProbeEvents: one per line the tool
            prints, read where the line is a reply or a hop, then `end`."""
            acct = account(context)
            try:
                w = sessions.console_worker(acct)
            except WorkerError as e:
                context.abort(grpc.StatusCode.UNAVAILABLE, str(e))
            args = {"tool": tool, "host": req.host, "count": int(req.count),
                    "source": req.source}
            lines = opshow.LineSplitter()
            seen = []
            parts = w.ask_stream("probe", args, timeout=PROBE_WAIT)
            try:
                for part in parts:
                    if not context.is_active():
                        return
                    if "refused" in part:
                        context.abort(grpc.StatusCode.FAILED_PRECONDITION
                                      if part["absent"] else
                                      grpc.StatusCode.INVALID_ARGUMENT,
                                      part["refused"])
                    if "out" in part:
                        for line in lines.feed(part["out"]):
                            seen.append(line)
                            yield probe_event_pb(pb, tool, line)
                    elif "rc" in part:
                        for line in lines.rest():
                            seen.append(line)
                            yield probe_event_pb(pb, tool, line)
                        if part.get("refusal"):
                            context.abort(grpc.StatusCode.FAILED_PRECONDITION,
                                          part["refusal"])
                        yield pb.ProbeEvent(end=pb.ProbeEnd(**opshow.probe_end(
                            tool, "\n".join(seen), part["rc"])))
            except WorkerError as e:
                context.abort(grpc.StatusCode.UNAVAILABLE, str(e))
            finally:
                parts.close()

        # not a unit: an rpc, driven by lab/api-grpc.sh's seventh phase.
        def Ping(self, req, context):
            yield from self._probe("ping", req, context)

        # not a unit: an rpc, driven by lab/api-grpc.sh's seventh phase.
        def Traceroute(self, req, context):
            yield from self._probe("traceroute", req, context)

        # not a unit: an rpc, driven by lab/api-grpc.sh's seventh phase.
        def Mtr(self, req, context):
            yield from self._probe("mtr", req, context)

        # not a unit: an rpc, driven by lab/api-grpc.sh and lab/bfd.sh;
        # opshow.bfd_fields() / bfd_only() / bfd_peer() are what it does.
        def ListBfdSessions(self, req, context):
            peer, why = opshow.bfd_peer(req.peer)
            if why:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, why)
            got = self._ask(context, "bfd", peer)
            f = opshow.bfd_only(opshow.bfd_fields(got["facts"]), peer)
            return pb.BfdSessions(
                answered=f["answered"], text=got["text"],
                sessions=[bfd_session_pb(s) for s in f["sessions"]])

        # not a unit: a streaming rpc, driven by lab/bfd.sh;
        # opshow.bfd_changes() and bfd_interval() are what it does.
        def WatchBfd(self, req, context):
            every = opshow.bfd_interval(req.interval_seconds)
            if every is None:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                              "interval_seconds is 1-60, or 0 for 2")
            prev = None
            while context.is_active():
                got = self._ask(context, "bfd", "")
                cur = opshow.bfd_fields(got["facts"])
                now = time.time()
                for kind, s, before in opshow.bfd_changes(prev, cur):
                    ev = pb.BfdEvent(
                        kind=pb.BfdEvent.Kind.Value(kind), at=ts(now),
                        previous_state=pb.BfdSession.State.Value(before))
                    if s is not None:
                        ev.session.CopyFrom(bfd_session_pb(s))
                    yield ev
                prev = cur
                end = time.time() + every
                while context.is_active() and time.time() < end:
                    time.sleep(0.2)

        # not a unit: an rpc, driven by lab/api-grpc.sh; logging_fields() and
        # logstate are what it does, and are tested.
        def GetLogging(self, req, context):
            account(context)
            if logwatch is None:
                context.abort(grpc.StatusCode.UNAVAILABLE, "the log export is not watched")
            logwatch.observe(vpp_logging())
            parsed, rows, text, at, error = logwatch.snapshot()
            if error or parsed is None:
                context.abort(grpc.StatusCode.UNAVAILABLE, error or "no reading")
            f = logging_fields(parsed, rows, text, at)
            out = pb.LoggingState(**{k: v for k, v in f.items()
                                     if k not in ("at", "streams")})
            out.at.CopyFrom(ts(f["at"]))
            for r in f["streams"]:
                lp = r.pop("last_progress")
                out.streams.add(**r).last_progress.CopyFrom(ts(lp))
            return out

        # not a unit: an rpc, driven by lab/missing-card.sh; pending_fields()
        # is what it reads, and is tested.
        def GetPendingConfiguration(self, req, context):
            return pb.PendingConfiguration(**pending_fields())

        # not a unit: an rpc, driven by lab/api-grpc.sh; clear_line() is
        # what it decides, and is tested.
        def Clear(self, req, context):
            acct = account(context)
            target = pb.ClearRequest.Target.Name(req.target)[len("TARGET_"):].lower()
            mode = pb.ClearRequest.BgpMode.Name(req.bgp)[len("BGP_"):].lower()
            line = clear_line(target, req.name, mode)
            if line is None:
                fail(context, grpc.StatusCode.INVALID_ARGUMENT, "", stage="usage",
                     extra=f"no clear for target {target} with name {req.name!r}")
            try:
                res = sessions.ops_worker(acct).ask("feed", [line])
            except WorkerError as e:
                context.abort(grpc.StatusCode.UNAVAILABLE, str(e))
            if res["failed"]:
                why = action_reason(res["output"])
                fail(context, action_code(grpc, why), res["output"], stage="action",
                     reason=why)
            return pb.ClearResponse(output=res["output"], command=line)

    class Telemetry(rpc.TelemetryServiceServicer):
        # not a unit: a streaming rpc, driven by lab/api-grpc.sh.
        def Subscribe(self, req, context):
            acct = account(context)
            interval = max(500, req.interval_ms or 1000) / 1000.0
            stats = stats_factory()
            state = read_state()
            # #555: from the newest log event the watcher has, onwards.
            log_seq = logwatch.last_seq() if logwatch is not None else 0
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
                if req.logging_events and logwatch is not None:
                    for log_seq, e in logwatch.events_after(log_seq):
                        yield pb.TelemetryUpdate(at=ts(now), event=pb.Event(**e))
                time.sleep(interval)

        # not a unit: a streaming rpc, driven by lab/api-grpc.sh.
        def GetHistory(self, req, context):
            account(context)
            since = req.since.seconds + req.since.nanos / 1e9 if req.HasField("since") else 0.0
            # None of the four asked is all four: a bare request is the day.
            # Each CPU's usage (#598) only when asked: a day of 64 CPUs is
            # 92,160 rows, and a bare request is what 0.150's clients send.
            want = (req.interface_counters, req.cgnat_totals, req.workers, req.memory)
            if not any(want) and not req.cpus:
                want = (True, True, True, True)
            for smp in history.samples(since, list(req.interfaces), *want,
                                       cpus=req.cpus):
                at = Timestamp()
                at.FromNanoseconds(int(smp["at"] * 1e9))
                out = pb.HistorySample(
                    at=at,
                    interfaces=[pb.InterfaceSample(name=i["name"],
                                                   counters=pb.InterfaceCounters(**i["counters"]))
                                for i in smp["interfaces"]],
                    workers=[pb.WorkerLoad(**w) for w in smp["workers"]],
                    cpus=[pb.CpuUsage(**c) for c in smp["cpus"]])
                if smp["cgnat"] is not None:
                    out.cgnat.CopyFrom(pb.CgnatTotals(**smp["cgnat"]))
                if smp["memory_used_bytes"] is not None:
                    out.memory_used_bytes = smp["memory_used_bytes"]
                yield out

        # not a unit: a streaming rpc, driven by lab/api-grpc.sh;
        # logmon.watch() and watch_words() are what it does, and are tested.
        def WatchLogging(self, req, context):
            import logmon
            acct = account(context)
            try:
                ok, why = sessions.ops_worker(acct).ask("segment_readable", None)
            except WorkerError as e:
                context.abort(grpc.StatusCode.UNAVAILABLE, str(e))
            if not ok:
                context.abort(grpc.StatusCode.PERMISSION_DENIED
                              if "may not read" in why else
                              grpc.StatusCode.UNAVAILABLE, why)
            opts, why = logmon.parse_args(watch_words(req))
            if why:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, why)
            opts["no_deliveries"] = req.no_deliveries
            buf, why = logmon.open_segment()
            if why:
                context.abort(grpc.StatusCode.UNAVAILABLE, " ".join(why))
            if logmon.header(buf) is None:
                context.abort(grpc.StatusCode.UNAVAILABLE,
                              "the event segment is not one this server reads")
            pools = logmon.parse_pool_index(vpp_show("bpa", "pool", "index"))

            # not a unit: a closure over this call's dataplane reads;
            # logstate.parse_logging() is what it does, and is tested.
            def read_logging():
                text = vpp_logging()
                return logstate.parse_logging(text) if text else None

            for e in logmon.watch(opts, buf, pools, read_logging,
                                  active=context.is_active):
                yield log_event_pb(pb, e)

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

    class System(rpc.SystemServiceServicer):
        # not a unit: a helper of the rpcs below, driven by lab/api-grpc.sh.
        def _worker(self, context):
            try:
                return sessions.ops_worker(account(context))
            except WorkerError as e:
                context.abort(grpc.StatusCode.UNAVAILABLE, str(e))

        # not a unit: the body of Reboot and Shutdown, driven by
        # lab/api-grpc.sh; system_cost() is what it decides on, and is tested.
        def _box(self, req, context, verb):
            w = self._worker(context)
            if not req.force:
                cost = w.ask("system_cost")
                if cost["unsaved"]:
                    fail(context, grpc.StatusCode.FAILED_PRECONDITION, cost["said"],
                         stage="action", reason="unsaved",
                         extra="the running configuration has not been saved")
                if cost["confirm_pending"]:
                    fail(context, grpc.StatusCode.FAILED_PRECONDITION, cost["said"],
                         stage="action", reason="confirm_pending",
                         extra="the last commit has not been confirmed")
            res = w.ask("feed", [f"system {verb}"])
            if res["failed"]:
                why = action_reason(res["output"])
                fail(context, action_code(grpc, why), res["output"], stage="action",
                     reason=why)
            return pb.SystemActionResponse(output=res["output"])

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Reboot(self, req, context):
            return self._box(req, context, "reboot")

        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Shutdown(self, req, context):
            return self._box(req, context, "shutdown")

        # not a unit: an rpc, driven by lab/api-grpc.sh; hostrun() and
        # run_log() are what it does, and are tested.
        def RestartDataplane(self, req, context):
            import doors
            path = doors.api_run_log("dataplane")
            if run_busy(path):
                fail(context, grpc.StatusCode.FAILED_PRECONDITION, "", stage="action",
                     reason="busy", extra="a dataplane restart is already running")
            started = time.time()
            got = self._worker(context).ask("hostrun", {"kind": "dataplane"})
            if not got["ok"]:
                why = action_reason(got["output"])
                fail(context, action_code(grpc, why), got["output"], stage="action",
                     reason=why)
            deadline = started + RUN_WAIT
            log = None
            while time.time() < deadline and context.is_active():
                log = run_since(path, started)
                if log and log["done"]:
                    break
                if run_silent(log, started, time.time()):
                    fail(context, grpc.StatusCode.FAILED_PRECONDITION, "",
                         stage="action", reason="apply_failed",
                         extra=silent_reason("prisma-routing-api-dataplane"))
                time.sleep(0.5)
            if not log or not log["done"]:
                context.abort(grpc.StatusCode.DEADLINE_EXCEEDED,
                              f"the dataplane restart did not finish in {RUN_WAIT} s;"
                              f" GetUpgradeStatus-like reads of {path} follow it")
            if log["status"] != 0:
                fail(context, grpc.StatusCode.FAILED_PRECONDITION, log["text"],
                     stage="action", reason="apply_failed")
            return pb.SystemActionResponse(output=log["text"])

        # not a unit: a streaming rpc, driven by lab/api-grpc.sh;
        # upgrade_phases(), run_log() and hostrun() are what it does.
        def Upgrade(self, req, context):
            import doors
            w = self._worker(context)
            if not req.version:
                res = w.ask("feed", ["system upgrade"])
                for st in upgrade_phases(res["output"]) or [
                        {"phase": "failed", "message": res["output"].strip()}]:
                    yield progress_pb(pb, st, output=res["output"])
                return
            if not doors.RELEASE.match(req.version):
                fail(context, grpc.StatusCode.INVALID_ARGUMENT, "", stage="usage",
                     extra=f"not a release: {req.version!r}, as in 0.149.25")
            path = doors.api_run_log("upgrade")
            if run_busy(path):
                fail(context, grpc.StatusCode.FAILED_PRECONDITION, "", stage="action",
                     reason="busy", extra="an upgrade is already running")
            started = time.time()
            got = w.ask("hostrun", {"kind": "upgrade", "version": req.version})
            if not got["ok"]:
                why = action_reason(got["output"])
                fail(context, action_code(grpc, why), got["output"], stage="action",
                     reason=why)
            yield progress_pb(pb, {"phase": "started", "version": req.version,
                                   "message": f"upgrading to {req.version}"},
                              started=started)
            sent, deadline = 0, time.time() + UPGRADE_WAIT
            while time.time() < deadline and context.is_active():
                log = run_since(path, started)
                if run_silent(log, started, time.time()):
                    yield progress_pb(pb, {"phase": "failed",
                                           "message": silent_reason(
                                               "prisma-routing-api-upgrade")},
                                      started=started)
                    return
                if log is None:
                    time.sleep(0.5)
                    continue
                steps = upgrade_phases(log["text"])
                for st in steps[sent:]:
                    if st["phase"] == "api_restart":
                        st["api_away_seconds"] = API_AWAY_SECONDS
                    yield progress_pb(pb, st, started=started,
                                      output=log["text"] if st["phase"] == "failed"
                                      else "")
                    if st["phase"] in ("api_restart", "failed"):
                        return
                sent = len(steps)
                if log["done"]:
                    # Finished without the API restart line: installed and
                    # the VTY could not be refreshed, or nothing to say.
                    return
                time.sleep(0.5)

        # not a unit: an rpc, driven by lab/api-grpc.sh; run_log() and
        # upgrade_phases() are what it reads, and are tested.
        def GetUpgradeStatus(self, req, context):
            import doors
            account(context)
            log = run_log(doors.api_run_log("upgrade"))
            if log is None:
                context.abort(grpc.StatusCode.NOT_FOUND,
                              "no upgrade was started through the API on this box")
            steps = upgrade_phases(log["text"])
            last = steps[-1] if steps else {"phase": "started", "message": ""}
            return progress_pb(pb, last, started=log["started"], output=log["text"])

    servicers = [Config(), Operational(), Telemetry(), Routes(), Session(), System()]
    if health is not None:
        servicers.append(health_servicer(stats_factory, health[0], health[1], grpc,
                                         logwatch))
    return servicers


# not a unit: grpc.health.v1's servicer, a class over health_status() and
# stats_ok(), which the unit tests drive; lab/api-grpc.sh asks it.
def health_servicer(stats_factory, hpb, hrpc, grpc, logwatch=None):
    """grpc.health.v1.Health: health_status() for each service asked."""
    seen = {"at": 0.0, "ok": False}

    # not a unit: a closure over LogWatch.health(), which is tested.
    def log_status():
        return logwatch.health()[0] if logwatch is not None else None

    # not a unit: a closure; stats_ok() below is the reading it caches.
    def ok():
        if time.monotonic() - seen["at"] > 2:
            seen["ok"], seen["at"] = stats_ok(stats_factory), time.monotonic()
        return seen["ok"]

    # not a unit: the servicer class, driven by lab/api-grpc.sh.
    class Health(hrpc.HealthServicer):
        # not a unit: an rpc, driven by lab/api-grpc.sh.
        def Check(self, req, context):
            st = health_status(req.service, ok(), log_status(),
                               config_pending())
            if st is None:
                context.abort(grpc.StatusCode.NOT_FOUND, f"no service {req.service!r}")
            return hpb.HealthCheckResponse(status=getattr(hpb.HealthCheckResponse, st))

        # not a unit: a streaming rpc, driven by lab/api-grpc.sh.
        def Watch(self, req, context):
            last = None
            while context.is_active():
                st = health_status(req.service, ok(), log_status(),
                                   config_pending()) or "SERVICE_UNKNOWN"
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
            interval=HISTORY_INTERVAL, cpus=None):
    """Sample every `interval` seconds, keep the file current, until `stop`."""
    stats, n = None, 0
    if cpus is not None:
        # The first reading now, so the first sample has a window (#598).
        try:
            with open(PROC_STAT) as f:
                cpus.observe(f.read(), time.time())
        except OSError:
            pass
    while not stop.wait(interval):
        try:
            if stats is None:
                stats = stats_factory()
            take_sample(history, stats, startup_worker_cpus(), meter=meter,
                        cpus=cpus, main_cpu=startup_main_cpu())
        except Exception:  # noqa: BLE001 - reconnect at the next tick
            stats = None
            history.add(time.time())
        n += 1
        if n % HISTORY_SAVE_EVERY == 0:
            history.save(path)


# not a unit: the watcher's thread body - LogWatch.observe() is what it does
# each time and is tested; lab/api-grpc.sh runs it with a one-second interval.
def log_watcher(watch, stop, interval=None, read=vpp_logging):
    """Read the log export's screen every `interval` seconds until `stop`, so
    the health service and the event stream have readings over time (#555)."""
    interval = logstate.LOG_INTERVAL if interval is None else interval
    while True:
        watch.observe(read())
        if stop.wait(interval):
            return


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
    cpus = CpuUsage()
    threading.Thread(target=sampler, args=(history, stats_factory, stop, meter),
                     kwargs={"cpus": cpus}, daemon=True).start()
    logwatch = logstate.LogWatch()
    threading.Thread(target=log_watcher, args=(logwatch, stop), daemon=True).start()
    cfg, ops, tel, routes, session, system, health = build(
        sessions, stats_factory, pb, rpc, grpc, history, (hpb, hrpc), meter, logwatch,
        cpus)
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
    rpc.add_SystemServiceServicer_to_server(system, server)
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
