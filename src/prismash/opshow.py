"""
The rest of the VTY's screens as fields (#558, phase 7): `show ospf
interface`, `show isis interface`, `show hardware`, `show logging`, and the
three probes - `ping`, `traceroute`, `mtr` - read into the dicts
OperationalService's typed rpcs answer with.

ONE READING, as in phase 6 (cgnatshow.py). The routing and hardware screens
are drawn by the Shell from a facts dict (Shell.ospf_interface_facts() and
its siblings) and the rpc carries that same dict, so the fields and the
screen are one reading. `show logging` is FRR's own screen, which the VTY
passes through, so it is read back here from the text. A probe's lines are
the tool's own, the same ones the VTY prints, read here one at a time.

None of these raises on a line or a value it does not recognise: a field
stays at its zero value and the text travels beside it.
"""
import re


def _int(v, unknown=-1):
    """`v` as an int, `unknown` for "-", None or anything that is not one."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return unknown


def ospf_fields(facts):
    """OspfInterfaces's fields from Shell.ospf_interface_facts()."""
    rows = []
    for r in facts.get("interfaces") or []:
        there = r.get("dataplane_mtu")
        rows.append({"name": str(r["name"]), "area": str(r.get("area", "-")),
                     "state": str(r.get("state", "")),
                     "passive": bool(r.get("passive")),
                     "cost": _int(r.get("cost")),
                     "adjacent": max(_int(r.get("adjacent"), 0), 0),
                     "neighbors": max(_int(r.get("neighbors"), 0), 0),
                     "ospf_mtu": max(_int(r.get("mtu"), 0), 0),
                     "dataplane": str(r.get("dataplane") or ""),
                     "dataplane_mtu": max(_int(there, 0), 0),
                     "dataplane_mtu_known": there is not None,
                     "mtu_stall": bool(r.get("stall"))})
    return {"answered": bool(facts.get("answered")), "interfaces": rows}


def isis_fields(facts):
    """IsisInterfaces's fields from Shell.isis_interface_facts()."""
    rows = [{"name": str(r["name"]), "area": str(r.get("area", "")),
             "circuit_id": str(r.get("circuit_id", "-")),
             "state": str(r.get("state", "")), "passive": bool(r.get("passive")),
             "level": str(r.get("level", "")), "metric": _int(r.get("metric")),
             "active_neighbors": max(_int(r.get("active_neighbors"), 0), 0),
             "silent": bool(r.get("silent"))}
            for r in facts.get("circuits") or []]
    punting = facts.get("punting")
    return {"answered": bool(facts.get("answered")), "circuits": rows,
            "punt_known": punting is not None, "punting": bool(punting),
            "osi_drops": max(_int(facts.get("drops"), 0), 0)}


def hardware_fields(facts):
    """Hardware's fields from Shell.hardware_facts()."""
    mgmt = facts.get("mgmt") or ""
    nics = [{"name": str(n["name"]), "pci": str(n.get("pci") or ""),
             "driver": str(n.get("driver") or ""),
             "dpdk_driver": bool(n.get("dpdk")),
             "link": str(n.get("state") or ""),
             "speed_mbps": max(_int(n.get("speed"), 0), 0),
             "mac": str(n.get("mac") or ""),
             "default_route": bool(mgmt) and n["name"] == mgmt}
            for n in facts.get("nics") or []]
    held = facts.get("held")
    return {"dataplane_answered": held is not None,
            "dataplane_holds": [str(h) for h in held or []],
            "nics": nics, "default_route_interface": str(mgmt)}


# ------------------------------------------------------------- show logging

_DAEMON = re.compile(r"^Logging configuration for (\S+):\s*$")
_TARGET = re.compile(r"^(Syslog|Stdout|File) logging:\s*(.*)$")


def _target(rest):
    """`disabled`, or `level <l>[, facility <f>][, ident <i>][, filename <f>]`."""
    out = {"enabled": False, "level": "", "facility": "", "ident": "",
           "filename": ""}
    rest = rest.strip()
    if rest == "disabled" or not rest:
        return out
    out["enabled"] = True
    for part in rest.split(","):
        k, _, v = part.strip().partition(" ")
        if k in ("level", "facility", "ident", "filename"):
            out[k] = v.strip()
    return out


def parse_routing_logging(text):
    """
    FRR's `show logging` - one block per daemon, "Logging configuration for
    zebra:" and its lines - as RoutingLogging's daemons, in FRR's order.
    """
    daemons = []
    cur = None
    for line in text.splitlines():
        s = line.strip()
        m = _DAEMON.match(s)
        if m:
            cur = {"daemon": m.group(1), "syslog": _target(""),
                   "stdout": _target(""), "file": _target(""), "protocol": "",
                   "record_priority": False, "record_severity": False,
                   "timestamp_precision": 0}
            daemons.append(cur)
            continue
        if cur is None:
            continue
        m = _TARGET.match(s)
        if m:
            cur[m.group(1).lower()] = _target(m.group(2))
        elif s.startswith("Protocol name:"):
            cur["protocol"] = s.split(":", 1)[1].strip()
        elif s.startswith("Record priority:"):
            cur["record_priority"] = s.split(":", 1)[1].strip() == "enabled"
        elif s.startswith("Record severity:"):
            cur["record_severity"] = s.split(":", 1)[1].strip() == "enabled"
        elif s.startswith("Timestamp precision:"):
            cur["timestamp_precision"] = max(_int(s.split(":", 1)[1].strip(), 0), 0)
    return daemons


# ------------------------------------------------------------------- probes

# iputils ping: "64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time=0.039 ms"
# An IPv6 address carries colons, so the address is the shortest run of
# non-space before the optional colon and space ping writes after it.
_PING_REPLY = re.compile(r"^\d+ bytes from (\S+?):?\s.*?icmp_seq=(\d+)"
                         r"(?: ttl=(\d+))?(?: time=([\d.]+) ms)?")
# "2 packets transmitted, 2 received, 0% packet loss, time 1004ms" - and with
# "+N errors" or "+N duplicates" between, which ping writes when it has them.
_PING_SUM = re.compile(r"^(\d+) packets transmitted, (\d+) received,"
                       r"(?: \+\d+ \w+,)* ([\d.]+)% packet loss")
# "rtt min/avg/max/mdev = 0.039/0.045/0.052/0.006 ms"
_PING_RTT = re.compile(r"^(?:rtt|round-trip) min/avg/max/mdev = "
                       r"([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms")
# traceroute -q 1 -n: " 1  172.17.0.1  0.915 ms", " 3  *"
_TRACE_HOP = re.compile(r"^\s*(\d+)\s+(?:(\*)|(\S+)\s+([\d.]+) ms)")
# mtr --report: "  1.|-- 127.0.0.1    0.0%     2    0.1   0.1   0.1   0.1   0.0"
_MTR_HOP = re.compile(r"^\s*(\d+)\.\|--\s+(\S+)\s+([\d.]+)%\s+(\d+)\s+"
                      r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)")


def probe_line(tool, line):
    """
    One line a probe printed, read into ProbeEvent's kind: ("reply", {...})
    for a ping reply, ("hop", {...}) for a traceroute hop, ("mtr_hop", {...})
    for a row of mtr's report, or (None, None) for any other line - the
    banner, a summary, an error the tool wrote - which travels as text only.
    """
    s = line.rstrip("\r\n")
    if tool == "ping":
        m = _PING_REPLY.match(s)
        if m:
            return "reply", {"sequence": int(m.group(2)),
                             "from_address": m.group(1),
                             "ttl": int(m.group(3) or 0),
                             "rtt_ms": float(m.group(4) or 0)}
    elif tool == "traceroute":
        m = _TRACE_HOP.match(s)
        if m:
            star = m.group(2) is not None
            return "hop", {"hop": int(m.group(1)),
                           "address": "" if star else m.group(3),
                           "answered": not star,
                           "rtt_ms": 0.0 if star else float(m.group(4))}
    elif tool == "mtr":
        m = _MTR_HOP.match(s)
        if m:
            addr = m.group(2)
            nums = [float(m.group(i)) for i in range(5, 10)]
            return "mtr_hop", {"hop": int(m.group(1)),
                               "address": "" if addr == "???" else addr,
                               "answered": addr != "???",
                               "loss_percent": float(m.group(3)),
                               "sent": int(m.group(4)),
                               "last_ms": nums[0], "avg_ms": nums[1],
                               "best_ms": nums[2], "worst_ms": nums[3],
                               "stdev_ms": nums[4]}
    return None, None


def probe_end(tool, text, rc):
    """
    ProbeEnd's fields: the exit status, whether the box's deadline stopped
    the tool (124), and ping's summary when the whole output carries one.
    """
    out = {"exit_status": -1 if rc is None else int(rc),
           "stopped_at_deadline": rc == 124,
           "transmitted": 0, "received": 0, "loss_percent": 0.0,
           "rtt_known": False, "rtt_min_ms": 0.0, "rtt_avg_ms": 0.0,
           "rtt_max_ms": 0.0, "rtt_mdev_ms": 0.0}
    if tool != "ping":
        return out
    for line in text.splitlines():
        s = line.strip()
        m = _PING_SUM.match(s)
        if m:
            out.update(transmitted=int(m.group(1)), received=int(m.group(2)),
                       loss_percent=float(m.group(3)))
        m = _PING_RTT.match(s)
        if m:
            out.update(rtt_known=True, rtt_min_ms=float(m.group(1)),
                       rtt_avg_ms=float(m.group(2)), rtt_max_ms=float(m.group(3)),
                       rtt_mdev_ms=float(m.group(4)))
    return out


class LineSplitter:
    """
    Chunks in, whole lines out: a chunk ends anywhere, and a ProbeEvent is
    one line. feed() returns the lines a chunk completes; rest() the last,
    unterminated one at the end of the run.
    """

    def __init__(self):
        self.buf = ""

    def feed(self, chunk):
        self.buf += chunk
        *lines, self.buf = self.buf.split("\n")
        return [l.rstrip("\r") for l in lines]

    def rest(self):
        s, self.buf = self.buf.rstrip("\r"), ""
        return [s] if s else []
