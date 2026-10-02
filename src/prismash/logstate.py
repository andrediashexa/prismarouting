"""
The CGNAT log export's state, as the API serves it (#555).

ONE SOURCE OF TRUTH. Everything here is read from `show bpa logging` - the
screen the VTY prints as `show cgnat logging`, byte for byte - and not from
the shared segment or the plugin's counters a second time. The screen
already decides who carries each stream (the log container or the dataplane,
#550), copies the container's counters over the dataplane's while the
container carries them, and says why the dataplane kept a stream; reading
the segment here as well would be a second implementation of those rules,
and the first time the two disagreed an operator would see one number at the
prompt and another in the web interface. test/test_logstate.py holds the
parser to the format strings in src/plugins/bpa/bpa_cli.c, so a change of
wording there is a red suite here rather than a field that silently reads 0.

WHAT "DELIVERING" MEANS, which the screen does not say and the API does
(LogWatch below, the rule docs/api.md states):
  * a TCP stream (syslog, IPFIX over TCP) has something WAITING when bytes
    are pending in memory or the spill file is not empty; its PROGRESS is
    its "bytes sent" counter growing;
  * the UDP export has something FAILING when its send failures or its
    dropped records grew; its PROGRESS is the packets the log container sent,
    or the records the dataplane emitted, growing;
  * a configured stream that has had something waiting (or failing) and no
    progress for LOG_STALL seconds is STALLED - not delivering - and the
    health service "prisma.v1.LogExport" answers NOT_SERVING while any is.
A stream with nothing to send is delivering whatever its connection: an
idle syslog collector that is down is a problem the moment a line has to go
there, and the rule says so then, not before.
"""
import os
import re
import threading
import time

#: Seconds a configured stream may have something waiting and deliver
#: nothing before it counts as stalled.
LOG_STALL = float(os.environ.get("PRISMA_API_LOG_STALL", "60") or 60)
#: How often the server reads the screen when nobody asks (the health
#: service and the event stream need readings over time).
LOG_INTERVAL = float(os.environ.get("PRISMA_API_LOG_INTERVAL", "10") or 10)
#: How many events the watcher keeps for subscribers that fall behind.
LOG_EVENTS_KEPT = 256

STREAMS = ("syslog", "ipfix-tcp", "ipfix-udp")

_N = r"(\d+)"


def _num(pattern, line):
    """The integers `pattern` captures in `line`, or None when it does not match.
    Every group the callers write is `(\\d+)`, so each is digits."""
    m = re.match(pattern, line)
    return [int(g) for g in m.groups()] if m else None


def parse_logging(text):
    """
    `show bpa logging` as a dict: the IPFIX section's lines, the TCP carrier
    line, and the two TCP streams. A line it does not know is left out; a
    stream whose section says "disabled" has configured False.
    """
    out = {"udp": {"state": "", "exporter": None, "records": 0, "dropped": 0,
                   "sender": "dataplane", "sender_reason": "", "packets": 0,
                   "send_failures": 0, "container_dropped": 0,
                   "container_records": 0},
           "exhaustion_repeats": 0, "queue_dropped": 0, "export_dropped": 0,
           "rings_shared": False, "transmit_interface": "", "transmit_failed": 0,
           "carrier": "dataplane", "logger_pid": 0,
           "ipfix-tcp": {"configured": False}, "syslog": {"configured": False}}
    section = "udp"
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("IPFIX export of block events"):
            section = "udp"
            continue
        if line.startswith("TCP streams carried by the log container"):
            m = re.search(r"\(pid (\d+)\)", line)
            out["carrier"], out["logger_pid"] = "log-container", int(m.group(1)) if m else 0
            continue
        if line.startswith("TCP streams carried by the dataplane"):
            out["carrier"], out["logger_pid"] = "dataplane", 0
            continue
        if line.startswith("TCP transport"):
            section = "ipfix-tcp"
            out[section] = {"configured": not line.endswith("disabled")}
            continue
        if line.startswith("syslog transport"):
            section = "syslog"
            out[section] = {"configured": not line.endswith("disabled")}
            continue
        body = line.strip()
        if section == "udp":
            _udp_line(out, body)
        else:
            _tcp_line(out[section], body)
    return out


def _udp_line(out, body):
    """One line of the IPFIX section into `out`."""
    u = out["udp"]
    if body.startswith("state "):
        u["state"] = body.split()[-1]
    elif body.startswith("exporter "):
        m = re.match(r"exporter\s+(\S+):(\d+) src (\S+)", body)
        u["exporter"] = ({"collector": f"{m.group(1)}:{m.group(2)}", "source": m.group(3)}
                         if m else None)
    elif v := _num(r"records\s+" + _N + r" emitted, " + _N + r" dropped", body):
        u["records"], u["dropped"] = v
    elif body.startswith("sender "):
        v = _num(r"sender\s+the log container: " + _N + r" records, " + _N + r" packets, "
                 + _N + r" templates, " + _N + r" send failures, " + _N + r" dropped", body)
        if v:
            u["sender"], u["sender_reason"] = "log-container", ""
            (u["container_records"], u["packets"], _t, u["send_failures"],
             u["container_dropped"]) = v
        else:
            u["sender"] = "dataplane"
            m = re.match(r"sender\s+the dataplane: (.*)", body)
            u["sender_reason"] = m.group(1) if m else ""
    elif v := _num(r"exhaustion\s+.*: " + _N + r" repeats not reported", body):
        out["exhaustion_repeats"] = v[0]
    elif v := _num(r"queue\s+" + _N + r" event\(s\) dropped", body):
        out["queue_dropped"] = v[0]
    elif v := _num(r"export\s+" + _N + r" event\(s\) dropped", body):
        out["export_dropped"] = v[0]
    elif body.startswith("rings "):
        out["rings_shared"] = "outside the main heap" in body
    elif m := re.match(r"transmit\s+(\S+): (\d+) packet\(s\)", body):
        out["transmit_interface"], out["transmit_failed"] = m.group(1), int(m.group(2))


def _tcp_line(s, body):
    """One line of a TCP stream's section into the stream's dict `s`."""
    if m := re.match(r"collector\s+(\S+):(\d+)\s+(\w+)", body):
        s["collector"], s["connection"] = f"{m.group(1)}:{m.group(2)}", m.group(3)
    elif v := _num(r"(?:records|lines)\s+" + _N + r" (?:emitted|written), " + _N
                   + r" bytes sent, " + _N + r" pending", body):
        s["records"], s["bytes_sent"], s["pending"] = v
    elif v := _num(r"spill\s+" + _N + r" bytes on disk, " + _N + r" replayed, " + _N
                   + r" dropped", body):
        s["spilled"], s["replayed"], s["dropped"] = v
    elif v := _num(r"file\s+" + _N + r" bytes now of " + _N + r" MB at most, " + _N
                   + r" dropped over it", body):
        s["spill_bytes"], s["spill_cap_mb"], s["dropped_over_cap"] = v


def stream_rows(p):
    """
    The three streams of a parsed screen as rows of LogStream's fields, before
    the watcher's verdict. `delivered` and `failing` are the two numbers the
    rule reads: what grows when the stream gets records out, and what grows
    when it fails to.
    """
    rows = []
    for kind in ("syslog", "ipfix-tcp"):
        s = p[kind]
        rows.append({
            "kind": kind, "configured": s["configured"],
            "collector": s.get("collector", ""), "source": "",
            "carrier": p["carrier"] if s["configured"] else "",
            "carrier_reason": "", "connection": s.get("connection", ""),
            "records": s.get("records", 0), "bytes_sent": s.get("bytes_sent", 0),
            "packets_sent": 0, "pending_bytes": s.get("pending", 0),
            "spill_bytes": s.get("spill_bytes", 0),
            "spill_cap_bytes": s.get("spill_cap_mb", 0) << 20,
            "dropped_over_cap": s.get("dropped_over_cap", 0), "send_failures": 0,
            "dropped": s.get("dropped", 0),
            "delivered": s.get("bytes_sent", 0),
            "waiting": s.get("pending", 0) > 0 or s.get("spill_bytes", 0) > 0,
            "failing": 0})
    u = p["udp"]
    exp = u["exporter"]
    on = u["state"] == "enabled" and exp is not None
    by_logger = u["sender"] == "log-container"
    rows.append({
        "kind": "ipfix-udp", "configured": on,
        "collector": exp["collector"] if exp else "", "source": exp["source"] if exp else "",
        "carrier": u["sender"] if on else "", "carrier_reason": u["sender_reason"] if on else "",
        "connection": "", "records": u["container_records"] if by_logger else u["records"],
        "bytes_sent": 0, "packets_sent": u["packets"] if by_logger else 0,
        "pending_bytes": 0, "spill_bytes": 0, "spill_cap_bytes": 0, "dropped_over_cap": 0,
        "send_failures": u["send_failures"] if by_logger else 0,
        "dropped": u["container_dropped"] if by_logger else u["dropped"],
        "delivered": u["packets"] if by_logger else u["records"],
        "waiting": False,
        "failing": (u["send_failures"] + u["container_dropped"]) if by_logger else u["dropped"]})
    return rows


class LogWatch:
    """
    Readings of the screen over time: each stream's verdict (delivering or
    stalled, and since when it last got anything out), the events between
    two readings, and the health answer. Thread-safe: the server's watcher
    thread, GetLogging and the health service all feed and read it.
    """

    def __init__(self, stall=None, keep=LOG_EVENTS_KEPT):
        self.stall = LOG_STALL if stall is None else stall
        self.keep = keep
        self.lock = threading.Lock()
        self.parsed = None
        self.rows = []
        self.text = ""
        self.at = 0.0
        self.error = ""
        self.track = {}     # kind -> {"delivered", "failing", "progress_at", "stalled"}
        self.events = []    # [(seq, event dict)]
        self.seq = 0

    def observe(self, text, now=None):
        """Take one reading of the screen (or "" when the dataplane did not
        answer); returns the events it caused."""
        now = time.time() if now is None else now
        with self.lock:
            if not text.strip():
                self.error = "the dataplane does not answer `show bpa logging`"
                self.at = now
                return []
            p = parse_logging(text)
            rows = stream_rows(p)
            evs = []
            old = {r["kind"]: r for r in self.rows}
            for r in rows:
                evs += self._verdict(r, old.get(r["kind"]), now)
            self.parsed, self.rows, self.text, self.at, self.error = p, rows, text, now, ""
            for e in evs:
                self.seq += 1
                self.events.append((self.seq, e))
            del self.events[:-self.keep]
            return evs

    def _verdict(self, r, before, now):
        """Fill r's verdict from its history; the events its change makes."""
        t = self.track.get(r["kind"])
        if t is None or not r["configured"]:
            t = {"delivered": r["delivered"], "failing": r["failing"],
                 "progress_at": now, "stalled": False}
        progressed = r["delivered"] != t["delivered"]
        failed = r["failing"] > t["failing"]
        trouble = r["waiting"] or failed or (t["stalled"] and not progressed)
        if progressed or not trouble:
            t["progress_at"] = now
        stalled = r["configured"] and trouble and now - t["progress_at"] >= self.stall
        r["delivering"] = not stalled
        r["last_progress"] = t["progress_at"]
        r["stalled_reason"] = self._why(r, now - t["progress_at"]) if stalled else ""
        evs = []
        k = r["kind"]
        if before is not None and before["configured"] and r["configured"]:
            if before["connection"] != r["connection"]:
                if r["connection"] == "connected":
                    evs.append(_event("log-connected", f"{k}: connected to {r['collector']}"))
                elif before["connection"] == "connected":
                    evs.append(_event("log-disconnected",
                                      f"{k}: {r['connection']} from {r['collector']}"))
            if before["carrier"] != r["carrier"]:
                evs.append(_event("log-carrier",
                                  f"{k}: carried by the {_who(r['carrier'])}, "
                                  f"was the {_who(before['carrier'])}"))
            if before["spill_bytes"] == 0 and r["spill_bytes"] > 0:
                evs.append(_event("log-spill", f"{k}: spilling to disk, {r['spill_bytes']} bytes"))
            if r["dropped_over_cap"] > before["dropped_over_cap"]:
                evs.append(_event("log-spill-full",
                                  f"{k}: the spill is at its cap, "
                                  f"{r['dropped_over_cap']} bytes dropped over it"))
        if stalled and not t["stalled"]:
            evs.append(_event("log-stalled", f"{k}: {r['stalled_reason']}"))
        elif t["stalled"] and not stalled and r["configured"]:
            evs.append(_event("log-delivering", f"{k}: delivering again"))
        t.update(delivered=r["delivered"], failing=r["failing"], stalled=stalled)
        self.track[k] = t
        return evs

    @staticmethod
    def _why(r, idle):
        """The sentence a stalled stream's stalled_reason carries."""
        secs = int(idle)
        if r["kind"] == "ipfix-udp":
            return f"no packet sent for {secs}s while sends failed"
        if r["connection"] != "connected":
            return (f"nothing delivered for {secs}s: {r['connection']} from "
                    f"{r['collector']}, {r['pending_bytes']} bytes pending, "
                    f"{r['spill_bytes']} spilled")
        return (f"nothing delivered for {secs}s: the collector takes nothing, "
                f"{r['pending_bytes']} bytes pending, {r['spill_bytes']} spilled")

    def health(self):
        """("SERVING"|"NOT_SERVING", reason) for prisma.v1.LogExport."""
        with self.lock:
            if self.error or self.parsed is None:
                return "NOT_SERVING", self.error or "no reading yet"
            bad = [f"{r['kind']}: {r['stalled_reason']}" for r in self.rows
                   if r["configured"] and not r["delivering"]]
            return ("NOT_SERVING", "; ".join(bad)) if bad else ("SERVING", "")

    def snapshot(self):
        """The latest reading: (parsed, rows, text, at, error)."""
        with self.lock:
            return self.parsed, [dict(r) for r in self.rows], self.text, self.at, self.error

    def events_after(self, seq):
        """The kept events after `seq`, oldest first: [(seq, event)]."""
        with self.lock:
            return [(s, dict(e)) for s, e in self.events if s > seq]

    def last_seq(self):
        """The sequence number of the newest event (0 for none)."""
        with self.lock:
            return self.seq


def _event(kind, text):
    """An Event's fields, as state_events() builds them."""
    return {"kind": kind, "account": "", "text": text, "revision": "", "comment": ""}


def _who(carrier):
    """'log container' / 'dataplane' for a sentence."""
    return "log container" if carrier == "log-container" else "dataplane"
