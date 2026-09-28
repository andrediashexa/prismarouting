#!/usr/bin/env python3
"""
prisma_collector.py - a lab collector for the box's retention log (#491).

Receives what a Prisma Routing box exports about its port blocks, keeps it,
and answers the question the log exists for: which subscriber held public
address X, port Y, at time T.

* IPFIX on UDP 4739 - RFC 8158 NAT events (natEvent 16 allocation, 17
  release, 12 ports exhausted) and the box's boot-marker template (IE 160,
  systemInitTimeMilliseconds), as docs/logging.md defines them.
* syslog on TCP 601 - RFC 5424, framed by RFC 6587 octet counting, the same
  events with the fields in structured data (BLOCK-ALLOC, BLOCK-RELEASE,
  PORTS-EXHAUSTED, BOOT-MARKER).
* a page on HTTP 8080 to ask it.

WHY NOT nfdump AND rsyslog, which were tried first on the #475 bench: nfdump
1.7.5 decoded two of the record's fields and printed `nat event = 0: INVALID`,
because it has no RFC 8158 port-range elements; and rsyslog, given the box's
octet-counted stream, stored several events glued into one line. The box's
framing was measured correct (every frame's declared length equals its size),
so the fault was the receiver's, and a receiver that cannot be read back is no
retention log.

Lab infrastructure: it ships in no medium, and it keeps everything in one
SQLite file. It is not the provider's collector - docs/logging.md says the
collector is theirs - it is a way to see what the box sends.
"""

import datetime
import html
import ipaddress
import os
import re
import socket
import socketserver
import sqlite3
import struct
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - python < 3.9
    ZoneInfo = None

DB_PATH = os.environ.get("PRISMA_COLLECTOR_DB", "/var/lib/prisma-collector/events.db")
LOCAL_TZ = os.environ.get("PRISMA_COLLECTOR_TZ", "America/Sao_Paulo")

# The information elements this box exports (docs/logging.md) and a few a
# session exporter would add. Anything else is kept by number.
IE_NAMES = {
    4: "protocolIdentifier", 7: "sourceTransportPort",
    8: "sourceIPv4Address", 11: "destinationTransportPort",
    12: "destinationIPv4Address", 160: "systemInitTimeMilliseconds",
    225: "postNATSourceIPv4Address", 226: "postNATDestinationIPv4Address",
    227: "postNAPTSourceTransportPort", 230: "natEvent", 283: "natPoolId",
    323: "observationTimeMilliseconds", 361: "portRangeStart",
    362: "portRangeEnd", 363: "portRangeStepSize", 364: "portRangeNumPorts",
}
IPV4_IES = {8, 12, 225, 226}
NAT_EVENTS = {16: "BLOCK-ALLOC", 17: "BLOCK-RELEASE", 12: "PORTS-EXHAUSTED"}


# --- IPFIX ------------------------------------------------------------------

def ipfix_value(ie, raw, text_first=False):
    """One field's value: dotted quad for an IPv4 element, an integer for
    anything up to eight bytes, text for printable bytes, hex otherwise. A
    variable-length or enterprise field is text first: those carry names
    (a subscriber identifier), and "abc" read as a number is 6382179."""
    if ie in IPV4_IES and len(raw) == 4:
        return str(ipaddress.IPv4Address(raw))
    if len(raw) <= 8 and not text_first:
        return int.from_bytes(raw, "big")
    try:
        text = raw.decode("utf-8")
        if text.isprintable():
            return text
    except UnicodeDecodeError:
        pass
    if len(raw) <= 8:
        return int.from_bytes(raw, "big")
    return raw.hex()


def parse_ipfix(data, templates, exporter):
    """
    The records in one IPFIX message, decoded with `templates` - a dict the
    caller keeps across messages, keyed by (exporter, domain, template id),
    which template sets in this message add to. Each record is a dict of
    element name (or number) to value. A data set whose template has not
    been seen is skipped: the exporter resends templates, and guessing a
    layout would store a wrong answer.
    """
    records = []
    if len(data) < 16:
        return records
    version, length, _export, _seq, domain = struct.unpack("!HHIII", data[:16])
    if version != 10:
        return records
    pos, end = 16, min(length, len(data))
    while pos + 4 <= end:
        set_id, set_len = struct.unpack("!HH", data[pos:pos + 4])
        if set_len < 4:
            break
        body, pos = data[pos + 4:pos + set_len], pos + set_len
        if set_id in (2, 3):
            parse_template_set(body, set_id, templates, (exporter, domain))
        elif set_id >= 256:
            fields = templates.get((exporter, domain, set_id))
            if fields:
                records += parse_data_set(body, fields)
    return records


def parse_template_set(body, set_id, templates, key):
    """Template (2) and options template (3) records into `templates`."""
    p = 0
    while p + 4 <= len(body):
        tid, count = struct.unpack("!HH", body[p:p + 4])
        p += 4
        if set_id == 3:
            if p + 2 > len(body):
                return
            p += 2  # scope field count: the fields follow the same way
        fields = []
        for _ in range(count):
            if p + 4 > len(body):
                return
            ie, flen = struct.unpack("!HH", body[p:p + 4])
            p += 4
            pen = 0
            if ie & 0x8000:
                if p + 4 > len(body):
                    return
                pen = struct.unpack("!I", body[p:p + 4])[0]
                p += 4
                ie &= 0x7FFF
            fields.append((ie, flen, pen))
        templates[key + (tid,)] = fields


def parse_data_set(body, fields):
    """The records of one data set, by the template's field list."""
    out, p = [], 0
    fixed = sum(f[1] for f in fields if f[1] != 0xFFFF)
    while p + max(fixed, 1) <= len(body):
        rec = {}
        for ie, flen, pen in fields:
            variable = flen == 0xFFFF
            if variable:
                if p >= len(body):
                    return out
                flen = body[p]
                p += 1
                if flen == 255:
                    flen = struct.unpack("!H", body[p:p + 2])[0]
                    p += 2
            raw = body[p:p + flen]
            p += flen
            name = IE_NAMES.get(ie, ie) if pen == 0 else f"{pen}.{ie}"
            rec[name] = ipfix_value(ie if pen == 0 else -1, raw,
                                    text_first=variable or pen != 0)
        out.append(rec)
    return out


def event_from_ipfix(rec, exporter):
    """A stored event from one decoded record, or None for a record that is
    neither a NAT event nor a boot marker."""
    ts = rec.get("observationTimeMilliseconds")
    if ts is None:
        return None
    if "systemInitTimeMilliseconds" in rec and "natEvent" not in rec:
        return {"ts_ms": ts, "via": "ipfix", "exporter": exporter,
                "kind": "BOOT-MARKER", "init_ms": rec["systemInitTimeMilliseconds"]}
    kind = NAT_EVENTS.get(rec.get("natEvent"))
    if not kind:
        return None
    return {"ts_ms": ts, "via": "ipfix", "exporter": exporter, "kind": kind,
            "subscriber": rec.get("sourceIPv4Address"),
            "public": rec.get("postNATSourceIPv4Address"),
            "port_start": rec.get("portRangeStart"),
            "port_end": rec.get("portRangeEnd", rec.get("portRangeStart")),
            "pool": rec.get("natPoolId")}


# --- syslog -------------------------------------------------------------------

FRAME_START = re.compile(rb"(\d{1,6}) <\d{1,3}>1 ")


def syslog_frames(buf):
    """
    The complete messages at the front of `buf`, the bytes left over, and
    how many bytes were skipped to find a frame. RFC 6587 octet counting
    ("<length> <message>") first; a line ending in a newline is read as one
    message (non-transparent framing, which some senders use).

    RESYNCHRONISED, AND SAID. Every release to 0.140.2 cut its syslog stream
    mid-message after 12576 bytes (#492), so a stream from a box in the field
    can begin in the middle of a line. A reader that stops there stores
    nothing again for the life of the connection; this one skips to the next
    "<length> <PRI>1 " and counts what it skipped, so the loss is on the page
    rather than silent. What was skipped is lost either way.
    """
    msgs, skipped = [], 0
    while buf:
        m = FRAME_START.match(buf)
        if m:
            n, at = int(m.group(1)), len(m.group(1)) + 1
            if len(buf) < at + n:
                break
            msgs.append(buf[at:at + n])
            buf = buf[at + n:]
            continue
        nxt = FRAME_START.search(buf, 1)
        nl = buf.find(b"\n")
        if nl >= 0 and (nxt is None or nl < nxt.start()):
            if buf[:1] == b"<":
                msgs.append(buf[:nl])
            else:
                skipped += nl + 1
            buf = buf[nl + 1:]
            continue
        if nxt is None:
            break
        skipped += nxt.start()
        buf = buf[nxt.start():]
    return msgs, buf, skipped


def parse_syslog(msg):
    """
    An RFC 5424 message into its parts: pri, timestamp, hostname, app,
    msgid, the structured-data parameters (one dict, all SD elements merged)
    and the free text. None when the message is not RFC 5424.
    """
    text = msg.decode("utf-8", "replace") if isinstance(msg, bytes) else msg
    if not text.startswith("<") or ">" not in text:
        return None
    pri, rest = text[1:].split(">", 1)
    parts = rest.split(" ", 6)
    if len(parts) < 7 or not parts[0].isdigit():
        return None
    _ver, ts, host, app, _procid, msgid, tail = parts
    params, free = {}, tail
    if tail.startswith("["):
        free = ""
        i = 0
        while i < len(tail) and tail[i] == "[":
            j, inq = i + 1, False
            while j < len(tail):
                c = tail[j]
                if c == "\\" and inq:
                    j += 2
                    continue
                if c == '"':
                    inq = not inq
                elif c == "]" and not inq:
                    break
                j += 1
            params.update(sd_params(tail[i + 1:j]))
            i = j + 1
        free = tail[i:].strip()
    elif tail.startswith("- "):
        free = tail[2:]
    return {"pri": int(pri) if pri.isdigit() else None, "ts": ts,
            "host": host, "app": app, "msgid": msgid,
            "params": params, "text": free}


def sd_params(element):
    """`id k="v" k2="v2"` into {k: v, k2: v2}, unescaping \\" \\\\ \\]."""
    out, i = {}, element.find(" ")
    if i < 0:
        return out
    s = element[i + 1:]
    while s:
        s = s.lstrip()
        eq = s.find('="')
        if eq < 0:
            break
        key, j, val = s[:eq], eq + 2, []
        while j < len(s) and s[j] != '"':
            if s[j] == "\\" and j + 1 < len(s):
                j += 1
            val.append(s[j])
            j += 1
        out[key] = "".join(val)
        s = s[j + 1:]
    return out


def rfc3339_ms(ts):
    """An RFC 3339 timestamp (as the box writes it, UTC with Z) in epoch ms."""
    t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return int(t.timestamp() * 1000)


def event_from_syslog(parsed, exporter):
    """A stored event from one parsed syslog line, or None."""
    if not parsed or parsed["msgid"] not in (
            "BLOCK-ALLOC", "BLOCK-RELEASE", "PORTS-EXHAUSTED", "BOOT-MARKER"):
        return None
    p = parsed["params"]
    try:
        ts = rfc3339_ms(parsed["ts"])
    except ValueError:
        return None
    ev = {"ts_ms": ts, "via": "syslog", "exporter": exporter,
          "kind": parsed["msgid"], "text": parsed["text"]}
    if parsed["msgid"] == "BOOT-MARKER":
        if "bootTime" in p:
            ev["init_ms"] = rfc3339_ms(p["bootTime"])
        return ev
    ev.update(subscriber=p.get("subscriber"), public=p.get("public"),
              port_start=int(p["portStart"]) if "portStart" in p else None,
              port_end=int(p.get("portEnd", p.get("portStart", 0))) if "portStart" in p else None,
              pool=int(p["pool"]) if p.get("pool", "").isdigit() else None,
              sub_id=p.get("subscriberId"))
    return ev


# --- the store ----------------------------------------------------------------

COLUMNS = ("ts_ms", "via", "exporter", "kind", "subscriber", "public",
           "port_start", "port_end", "pool", "init_ms", "sub_id", "text")


def open_store(path):
    """The SQLite file, with its one table and the indexes the queries use."""
    db = sqlite3.connect(path, check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, "
               + ", ".join(COLUMNS) + ")")
    db.execute("CREATE INDEX IF NOT EXISTS ev_pub ON events (public, ts_ms)")
    db.execute("CREATE INDEX IF NOT EXISTS ev_sub ON events (subscriber, ts_ms)")
    db.execute("CREATE INDEX IF NOT EXISTS ev_ts ON events (ts_ms)")
    return db


def store_event(db, ev):
    """One event into the table."""
    db.execute("INSERT INTO events (" + ", ".join(COLUMNS) + ") VALUES ("
               + ", ".join("?" * len(COLUMNS)) + ")",
               [ev.get(c) for c in COLUMNS])


def who_held(db, public, port, ts_ms):
    """
    Which subscriber held `public`:`port` at `ts_ms`, read the way
    docs/logging.md says a collector must: the latest allocation covering the
    port at or before that instant, then whether a release for the same
    subscriber and range came before it, or a boot marker with a new init
    time did - an allocation with neither is still in force.

    Returns a dict: status ("held", "released", "ended-at-restart" or
    "none"), the allocation event, and the event that ended it if any. Both
    transports carry the same events, so a hit on either is the same answer.
    """
    row = db.execute(
        "SELECT " + ", ".join(COLUMNS) + " FROM events WHERE kind = "
        "'BLOCK-ALLOC' AND public = ? AND port_start <= ? AND port_end >= ? "
        "AND ts_ms <= ? ORDER BY ts_ms DESC LIMIT 1",
        (public, port, port, ts_ms)).fetchone()
    if not row:
        return {"status": "none"}
    alloc = dict(zip(COLUMNS, row))
    rel = db.execute(
        "SELECT " + ", ".join(COLUMNS) + " FROM events WHERE kind = "
        "'BLOCK-RELEASE' AND public = ? AND port_start = ? AND subscriber = ? "
        "AND ts_ms >= ? AND ts_ms <= ? ORDER BY ts_ms LIMIT 1",
        (public, alloc["port_start"], alloc["subscriber"], alloc["ts_ms"],
         ts_ms)).fetchone()
    if rel:
        return {"status": "released", "alloc": alloc,
                "end": dict(zip(COLUMNS, rel))}
    boot = db.execute(
        "SELECT " + ", ".join(COLUMNS) + " FROM events WHERE kind = "
        "'BOOT-MARKER' AND exporter = ? AND init_ms > ? AND init_ms <= ? "
        "ORDER BY init_ms LIMIT 1",
        (alloc["exporter"], alloc["ts_ms"], ts_ms)).fetchone()
    if boot:
        return {"status": "ended-at-restart", "alloc": alloc,
                "end": dict(zip(COLUMNS, boot))}
    return {"status": "held", "alloc": alloc}


def recent(db, limit=50, subscriber=None, public=None, kinds=None):
    """The latest events, newest first, optionally for one address, and
    optionally only the given kinds."""
    q = "SELECT " + ", ".join(COLUMNS) + " FROM events"
    where, args = [], []
    if subscriber:
        where.append("subscriber = ?")
        args.append(subscriber)
    if public:
        where.append("public = ?")
        args.append(public)
    if kinds:
        where.append("kind IN (" + ", ".join("?" * len(kinds)) + ")")
        args += list(kinds)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY ts_ms DESC, id DESC LIMIT ?"
    args.append(limit)
    return [dict(zip(COLUMNS, r)) for r in db.execute(q, args).fetchall()]


# --- the page -----------------------------------------------------------------

def fmt_ms(ms, tz=LOCAL_TZ):
    """Epoch ms as local wall-clock time with its UTC offset."""
    if ms is None:
        return ""
    zone = ZoneInfo(tz) if ZoneInfo else datetime.timezone.utc
    t = datetime.datetime.fromtimestamp(ms / 1000, zone)
    return t.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(ms % 1000):03d} " + t.strftime("%z")


def parse_local(text, tz=LOCAL_TZ):
    """'2026-09-28T20:30' or '2026-09-28 20:30:15' in local time to epoch ms."""
    zone = ZoneInfo(tz) if ZoneInfo else datetime.timezone.utc
    t = datetime.datetime.fromisoformat(text.strip().replace(" ", "T"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=zone)
    return int(t.timestamp() * 1000)


def event_rows(events):
    """A table of events, escaped."""
    head = ("<tr><th>time</th><th>event</th><th>subscriber</th><th>public</th>"
            "<th>ports</th><th>pool</th><th>via</th></tr>")
    rows = []
    for e in events:
        ports = "" if e["port_start"] is None else f"{e['port_start']}-{e['port_end']}"
        if e["kind"] == "BOOT-MARKER":
            ports = "up since " + fmt_ms(e["init_ms"]) if e["init_ms"] else ""
        rows.append("<tr>" + "".join(
            f"<td>{html.escape(str(v))}</td>" for v in (
                fmt_ms(e["ts_ms"]), e["kind"], e["subscriber"] or "",
                e["public"] or "", ports, "" if e["pool"] is None else e["pool"],
                e["via"])) + "</tr>")
    return "<table>" + head + "".join(rows) + "</table>"


def answer_html(res, public, port, ts_ms):
    """The answer to who-held, as a sentence a person can read out."""
    q = f"{html.escape(public)} port {port} at {html.escape(fmt_ms(ts_ms))}"
    if res["status"] == "none":
        return f"<p class=no>No allocation covers {q}.</p>"
    a = res["alloc"]
    who = html.escape(str(a["subscriber"]))
    since = html.escape(fmt_ms(a["ts_ms"]))
    rng = f"{a['port_start']}-{a['port_end']}"
    if res["status"] == "held":
        return (f"<p class=yes><b>{who}</b> held {q}: block {rng}, allocated "
                f"{since}, not released since.</p>")
    end = html.escape(fmt_ms(res["end"]["ts_ms"]))
    if res["status"] == "released":
        return (f"<p class=no>Nobody: the last holder, {who} (block {rng}, "
                f"from {since}), released it at {end}, before the time asked."
                f"</p>")
    return (f"<p class=no>Nobody known: {who} held block {rng} from {since}, "
            f"and the box restarted at {end} without a release - the holding "
            f"ended somewhere in that window.</p>")


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Prisma Routing - retention log</title>{refresh}
<style>body{{font-family:sans-serif;margin:1.5em;color:#111}}
table{{border-collapse:collapse;font-size:13px}}td,th{{border:1px solid #ccc;padding:3px 7px;text-align:left}}
th{{background:#eee}}.yes{{background:#e3f6e3;padding:.6em}}.no{{background:#fbeaea;padding:.6em}}
form{{margin:.6em 0}}input{{margin-right:.5em}}</style></head><body>
<h2>Prisma Routing - retention log (lab collector)</h2>
<form action=/who>Who held public <input name=public size=15 placeholder=203.0.113.1 value="{public}">
port <input name=port size=6 value="{port}"> at <input name=at size=22 placeholder="2026-09-28 20:30:00" value="{at}">
<input type=submit value=Ask></form>
<form action=/subscriber>Everything about subscriber <input name=ip size=15 placeholder=100.64.0.11 value="{sub}">
<input type=submit value=Show></form>
{body}
<p><a href=/>latest events</a> &middot; {count} events stored &middot; times in {tz}</p>
</body></html>"""


def render(db, path, query):
    """The page for one request: the answer, and a table of events."""
    q = urllib.parse.parse_qs(query)
    get = lambda k: (q.get(k) or [""])[0].strip()
    count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    fill = {"public": "", "port": "", "at": "", "sub": "", "refresh": "",
            "tz": LOCAL_TZ, "count": count}
    if path == "/who":
        fill.update(public=html.escape(get("public")), port=html.escape(get("port")),
                    at=html.escape(get("at")))
        try:
            public = str(ipaddress.IPv4Address(get("public")))
            port = int(get("port"))
            ts = parse_local(get("at")) if get("at") else int(
                datetime.datetime.now().timestamp() * 1000)
        except ValueError as e:
            body = f"<p class=no>Cannot read the question: {html.escape(str(e))}</p>"
        else:
            res = who_held(db, public, port, ts)
            body = answer_html(res, public, port, ts) + "<h3>This public address</h3>" \
                + event_rows(recent(db, 30, public=public))
    elif path == "/subscriber":
        fill["sub"] = html.escape(get("ip"))
        body = "<h3>Events for " + fill["sub"] + "</h3>" + event_rows(
            recent(db, 200, subscriber=get("ip")))
    else:
        fill["refresh"] = "<meta http-equiv=refresh content=10>"
        body = "<h3>Latest events</h3>" + event_rows(recent(db, 60))
    return PAGE.format(body=body, **fill)


# --- the daemon ---------------------------------------------------------------

LOCK = threading.Lock()


# not a unit: a socketserver handler for one datagram; the parsing it calls
# is parse_ipfix/event_from_ipfix, which test/test_collector.py holds.
class IpfixHandler(socketserver.BaseRequestHandler):
    templates = {}

    def handle(self):
        data, exporter = self.request[0], self.client_address[0]
        evs = [e for e in (event_from_ipfix(r, exporter) for r in
                           parse_ipfix(data, self.templates, exporter)) if e]
        if evs:
            with LOCK:
                for e in evs:
                    store_event(self.server.db, e)
                self.server.db.commit()


# not a unit: a socketserver handler reading a TCP stream; the framing and
# parsing it calls are syslog_frames/parse_syslog/event_from_syslog, which
# test/test_collector.py holds.
class SyslogHandler(socketserver.BaseRequestHandler):
    def handle(self):
        buf, exporter = b"", self.client_address[0]
        while True:
            chunk = self.request.recv(65536)
            if not chunk:
                return
            msgs, buf, skipped = syslog_frames(buf + chunk)
            evs = [e for e in (event_from_syslog(parse_syslog(m), exporter)
                               for m in msgs) if e]
            if skipped:
                evs.append({"ts_ms": int(datetime.datetime.now().timestamp() * 1000),
                            "via": "syslog", "exporter": exporter,
                            "kind": "STREAM-SKIPPED",
                            "text": f"{skipped} bytes skipped to find a frame"})
            if evs:
                with LOCK:
                    for e in evs:
                        store_event(self.server.db, e)
                    self.server.db.commit()


# not a unit: an http.server handler; what it serves is render(), which
# test/test_collector.py holds.
class WebHandler(BaseHTTPRequestHandler):
    # not a unit: http.server calls it with a socket; the page is render(),
    # which test/test_collector.py holds.
    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        with LOCK:
            page = render(self.server.db, u.path, u.query).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    # not a unit: silences http.server's access log, and does nothing else.
    def log_message(self, *args):
        pass


class UDPServer(socketserver.ThreadingUDPServer):
    address_family = socket.AF_INET6
    allow_reuse_address = True


class TCPServer(socketserver.ThreadingTCPServer):
    address_family = socket.AF_INET6
    allow_reuse_address = True
    daemon_threads = True


class HTTPServer6(ThreadingHTTPServer):
    address_family = socket.AF_INET6


# not a unit: binds three sockets and serves them for ever; the collector's
# behaviour is its parsing and its queries, each held by a unit test.
def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = open_store(DB_PATH)
    servers = [UDPServer(("::", 4739), IpfixHandler),
               TCPServer(("::", 601), SyslogHandler),
               HTTPServer6(("::", 8080), WebHandler)]
    for s in servers:
        s.db = db
        threading.Thread(target=s.serve_forever, daemon=True).start()
    print("prisma-collector: IPFIX udp/4739, syslog tcp/601, web http/8080; "
          f"store {DB_PATH}", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    sys.exit(main())
