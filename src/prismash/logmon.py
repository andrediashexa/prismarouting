"""
`monitor cgnat logging`: the CGNAT block events, live, in words (#556).

WHERE THE EVENTS COME FROM. The shared segment #550 put the log in
(/dev/shm/prisma-bpa-log, src/plugins/bpa/bpa_log_ring.h) has, since this
issue, a WATCH RING after the export ring: bpa_log_emit() - the one place
every exported event passes, whoever carries it afterwards - and the boot
marker write each event there with the wall clock their records carry. It
has no consumer: the main thread writes over its oldest slot, never waits
and never drops. The worker rings were the first answer and the lab refuted
it: a release by expiry is emitted by the main thread directly and never
crosses a worker ring, so a monitor reading those missed every one. They are
still read, as the fallback, on a dataplane that predates the watch ring -
and the screen says so and says what it misses.

A READER THAT TAKES NOTHING. It never writes the segment (mapped PROT_READ):
a cursor of its own follows `head`, copies the slots behind it, and reads
head again. Only `slots - 1` behind head are certainly whole - the slot of
index head - slots is the one the writer fills next - so a copy taken
further behind, or lapped while it was copied, is counted as lost and said,
never printed half-written. The dataplane neither waits for this reader nor
knows it exists.

WHAT IT CAN AND CANNOT TELL.
  * Every event the box exported - subscriber, public address, port range,
    pool, the identity the record carries - at the time the record carries.
  * Whether a given event reached a collector is not known per event: the
    export counts per stream. So delivery is a separate line, every 10 s, per
    configured stream, from `show bpa logging` - the same screen as `show
    cgnat logging` and the API's GetLogging (#555).
  * An exhaustion repeat the dataplane held back (#549) is not exported, so
    it is never a line; the delivery line counts them.

The segment's layout is restated here byte for byte; test/test_logmon.py
holds every offset to the C header by compiling it, so a change there turns
the suite red rather than a field silently reading the wrong bytes.
"""
import ipaddress
import mmap
import os
import struct
import time

from system import is_number

SEGMENT = os.environ.get("PRISMA_LOG_SEGMENT", "/dev/shm/prisma-bpa-log")
MAGIC = b"PRBALOG1"
VERSION = 3
HDR_BYTES = 1024
RING_HDR_BYTES = 192
EVENT_BYTES = 52
IDENTITY_MAX = 32
# Offsets in bpa_log_seg_hdr_t and bpa_log_ring_t (test/test_logmon.py
# compiles bpa_log_ring.h and compares).
OFF = {"magic": 0, "version": 8, "hdr_bytes": 12, "n_rings": 16,
       "ring_slots": 20, "event_bytes": 24, "ring_bytes": 28,
       "generation": 32, "boot_wall": 48, "cfg": 88, "mring_offset": 640,
       "mring_slots": 648, "ring_head": 0, "ring_written": 136}
XEV_BYTES = 64
XEV_RECORD, XEV_MARKER = 1, 2
CFG_BYTES = 96
STREAM_SYSLOG = 1

EVENT_ALLOC, EVENT_RELEASE, EVENT_EXHAUSTED = 16, 17, 12
EVENT_WORD = {EVENT_ALLOC: "ALLOC", EVENT_RELEASE: "RELEASE",
              EVENT_EXHAUSTED: "EXHAUSTED"}
FILTER_EVENT = {"alloc": EVENT_ALLOC, "release": EVENT_RELEASE,
                "exhausted": EVENT_EXHAUSTED, "boot": "boot"}
MSGID = {EVENT_ALLOC: "BLOCK-ALLOC", EVENT_RELEASE: "BLOCK-RELEASE",
         EVENT_EXHAUSTED: "PORTS-EXHAUSTED"}
POLL = 0.2
DELIVERY_EVERY = 10.0
MAX_SECONDS = 86400
WIDTH = 79


def ip4(n):
    """A host-order u32 as dotted quad (the ring stores addresses so)."""
    return str(ipaddress.IPv4Address(n & 0xFFFFFFFF))


def header(buf):
    """
    The segment header as a dict, or None when buf does not begin with one
    this build reads (magic, version, event size, sizes that add up): the
    same questions bpa_log_seg_check() asks, in the same order.
    """
    if len(buf) < HDR_BYTES or bytes(buf[0:8]) != MAGIC:
        return None
    version, hdr_bytes, n_rings, slots, ev_bytes, ring_bytes = \
        struct.unpack_from("<IIIIII", buf, OFF["version"])
    if version != VERSION or hdr_bytes != HDR_BYTES or ev_bytes != EVENT_BYTES:
        return None
    if not slots or slots & (slots - 1) or \
            ring_bytes != RING_HDR_BYTES + slots * ev_bytes:
        return None
    if HDR_BYTES + n_rings * ring_bytes > len(buf):
        return None
    generation, = struct.unpack_from("<Q", buf, OFF["generation"])
    boot_wall, = struct.unpack_from("<d", buf, OFF["boot_wall"])
    c = OFF["cfg"] + STREAM_SYSLOG * CFG_BYTES
    enabled = buf[c + 4]
    port, = struct.unpack_from("<H", buf, c + 6)
    pen, pri = struct.unpack_from("<II", buf, c + 12)
    host = bytes(buf[c + 20:c + 84]).split(b"\0", 1)[0].decode("utf-8", "replace")
    mring, mslots = struct.unpack_from("<QI", buf, OFF["mring_offset"])
    if not mslots or mslots & (mslots - 1) or \
            mring + RING_HDR_BYTES + mslots * XEV_BYTES > len(buf):
        mring = mslots = 0
    return {"n_rings": n_rings, "slots": slots, "ring_bytes": ring_bytes,
            "generation": generation, "boot_wall": boot_wall,
            "mring": mring, "mslots": mslots,
            "syslog": {"enabled": bool(enabled), "port": port, "pen": pen,
                       "pri": pri, "hostname": host}}


def ring_base(h, i):
    """Byte offset of ring i's header in the segment."""
    return HDR_BYTES + i * h["ring_bytes"]


def ring_head(buf, h, i):
    """Ring i's head: how many events its writer has published, mod 2^32."""
    return struct.unpack_from("<I", buf, ring_base(h, i) + OFF["ring_head"])[0]


def decode_event(raw):
    """One 52-byte bpa_log_event_t as a dict."""
    ip_priv, ip_pub, pool_id, lo, hi, ev = struct.unpack_from("<IIIHHB", raw, 0)
    ident = bytes(raw[17:17 + IDENTITY_MAX]).split(b"\0", 1)[0]
    return {"event": ev, "priv": ip4(ip_priv), "pub": ip4(ip_pub),
            "pool": pool_id, "lo": lo, "hi": hi,
            "identity": ident.decode("utf-8", "replace")}


class RingReader:
    """
    One cursor per ring, following head without ever writing the segment.
    `poll(buf)` returns (events, lost): what was published since the last
    poll, oldest first per ring, and how many it could not read whole.
    """

    def __init__(self, buf, h):
        self.h = h
        # From now: a monitor shows what happens while it watches.
        self.cur = [ring_head(buf, h, i) for i in range(h["n_rings"])]

    def poll(self, buf):
        h, events, lost = self.h, [], 0
        slots = h["slots"]
        for i in range(h["n_rings"]):
            head = ring_head(buf, h, i)
            behind = (head - self.cur[i]) & 0xFFFFFFFF
            if behind > slots - 1:
                # The slot of index head - slots is the one the writer fills
                # next: only slots - 1 behind head are certainly whole.
                lost += behind - (slots - 1)
                self.cur[i] = (head - (slots - 1)) & 0xFFFFFFFF
                behind = slots - 1
            got = []
            for k in range(behind):
                idx = (self.cur[i] + k) & 0xFFFFFFFF
                off = ring_base(h, i) + RING_HDR_BYTES + (idx & (slots - 1)) * EVENT_BYTES
                got.append((idx, bytes(buf[off:off + EVENT_BYTES])))
            # Read head again: a slot the writer lapped while it was copied
            # is torn, and is counted rather than shown.
            after = ring_head(buf, h, i)
            for idx, raw in got:
                if ((after - idx) & 0xFFFFFFFF) >= slots:
                    lost += 1
                else:
                    events.append(decode_event(raw))
            self.cur[i] = (self.cur[i] + behind) & 0xFFFFFFFF
        return events, lost


def decode_xev(raw):
    """
    One 64-byte bpa_log_xev_t of the watch ring: the event, its kind (a
    record or the boot marker) and the wall clock the dataplane stamped -
    the time its exported record carries.
    """
    ev = decode_event(raw[:EVENT_BYTES])
    ev["kind"] = raw[EVENT_BYTES]
    ev["wall"] = struct.unpack_from("<d", raw, 56)[0]
    return ev


class WatchReader:
    """
    The watch ring (#556): every event bpa_log_emit() exported and every boot
    marker, written by the main thread with nobody consuming - the writer
    overwrites, this follows head. `poll(buf)` returns (events, lost) like
    RingReader's, each event with its "kind" and "wall".
    """

    def __init__(self, buf, h):
        self.h = h
        self.cur = struct.unpack_from("<I", buf, h["mring"])[0]

    def poll(self, buf):
        h = self.h
        slots, base = h["mslots"], h["mring"]
        head = struct.unpack_from("<I", buf, base)[0]
        behind, lost = (head - self.cur) & 0xFFFFFFFF, 0
        if behind > slots - 1:
            lost = behind - (slots - 1)
            self.cur = (head - (slots - 1)) & 0xFFFFFFFF
            behind = slots - 1
        got = []
        for k in range(behind):
            idx = (self.cur + k) & 0xFFFFFFFF
            off = base + RING_HDR_BYTES + (idx & (slots - 1)) * XEV_BYTES
            got.append((idx, bytes(buf[off:off + XEV_BYTES])))
        after = struct.unpack_from("<I", buf, base)[0]
        events = []
        for idx, raw in got:
            if ((after - idx) & 0xFFFFFFFF) >= slots:
                lost += 1
            else:
                events.append(decode_xev(raw))
        self.cur = (self.cur + behind) & 0xFFFFFFFF
        return events, lost


def reader(buf, h):
    """The watch ring's reader when the segment has one, else the rings'."""
    return WatchReader(buf, h) if h["mring"] else RingReader(buf, h)


def parse_args(args):
    """
    The words after `monitor cgnat logging` as (options, None), or
    (None, refusal). Options: raw, subscriber <ip>, public <ip>[:port],
    event alloc|release|exhausted|boot, pool <name>, seconds <n>.
    """
    o = {"raw": False, "subscriber": None, "public": None, "port": None,
         "event": None, "pool": None, "seconds": None}
    i = 0
    while i < len(args):
        w = args[i]
        if w == "raw":
            o["raw"] = True
            i += 1
            continue
        if w not in ("subscriber", "public", "event", "pool", "seconds"):
            return None, f"monitor cgnat logging does not take {w!r}"
        if i + 1 >= len(args):
            return None, f"{w} needs a value after it"
        v = args[i + 1]
        if w in ("subscriber", "public"):
            addr, _, port = v.partition(":") if w == "public" else (v, "", "")
            try:
                o[w] = str(ipaddress.IPv4Address(addr))
            except ValueError:
                return None, f"{w} needs an IPv4 address: {v}"
            if port:
                if not is_number(port) or not 0 < int(port) < 65536:
                    return None, f"a port is 1 to 65535: {port}"
                o["port"] = int(port)
        elif w == "event":
            if v not in FILTER_EVENT:
                return None, "event is alloc, release, exhausted or boot"
            o["event"] = FILTER_EVENT[v]
        elif w == "pool":
            o["pool"] = v
        else:
            if not is_number(v) or not 1 <= int(v) <= MAX_SECONDS:
                return None, f"seconds is 1 to {MAX_SECONDS}"
            o["seconds"] = int(v)
        i += 2
    return o, None


def matches(ev, o, pools):
    """Does event ev pass the filters in o? pools maps id -> name."""
    if o["event"] is not None and o["event"] != ev["event"]:
        return False
    if o["subscriber"] and ev["priv"] != o["subscriber"]:
        return False
    if o["public"] and ev["pub"] != o["public"]:
        return False
    if o["port"] is not None and not ev["lo"] <= o["port"] <= ev["hi"]:
        return False
    if o["pool"] and pool_name(ev["pool"], pools) != o["pool"]:
        return False
    return True


def pool_name(pid, pools):
    """A pool's configured name, or #<id> when the dataplane did not say."""
    return pools.get(pid, f"#{pid}")


def parse_pool_index(text):
    """`show bpa pool index`: one 'N name' row per pool, as {N: name}."""
    out = {}
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and is_number(parts[0]):
            out[int(parts[0])] = parts[1].strip()
    return out


def clock(t):
    """
    A wall time as the box's local HH:MM:SS. No milliseconds: the time is
    when this monitor read the event, polled every 0.2 s, and a millisecond
    printed would claim a precision it does not have - and the four columns
    are what lets the pool fit on the event's line.
    """
    return time.strftime("%H:%M:%S", time.localtime(t))


def fit(head, parts, indent=14):
    """
    head, then each of parts after it while the line stays within 79
    columns; the parts that do not fit go on continuation lines indented by
    `indent`, never cut. A single part longer than a whole line is the only
    thing left long - an operator's pool name or identity is printed whole.
    """
    lines, cur = [], head
    for part in parts:
        if len(cur) + 2 + len(part) <= WIDTH or cur == " " * indent:
            cur = f"{cur}  {part}" if cur.strip() else f"{cur}{part}"
        else:
            lines.append(cur)
            cur = " " * indent + part
    lines.append(cur)
    return lines


def event_lines(ev, seen, pools):
    """
    One event as the lines a person reads, each within 79 columns: the event,
    the subscriber, the public address and the ports first, then the pool
    and the subscriber's identity on the same line when they fit, else under
    it.
    """
    word = EVENT_WORD.get(ev["event"], f"EVENT {ev['event']}")
    pool = f"pool {pool_name(ev['pool'], pools)}"
    if ev["event"] == EVENT_EXHAUSTED:
        head = f"{clock(seen)} {word:<9} {ev['priv']:<15} no free block in"
        head, parts = f"{head} {pool}", []
    else:
        head = (f"{clock(seen)} {word:<9} {ev['priv']:<15} > {ev['pub']:<15} "
                f"{ev['lo']}-{ev['hi']}")
        parts = [pool]
    who = ev["identity"]
    if who and who != "unknown":
        parts.append(f"subscriber {who}")
    return fit(head, parts)


def lines_for(ev, seen, h, pools, opts):
    """
    What one event prints under opts: nothing when a filter drops it, the
    BOOT line for a boot marker, else the event in words (or its wire forms
    with `raw`) - at the time its record carries when the watch ring gave it,
    else at `seen`, when this monitor read it.
    """
    at = ev.get("wall") or seen
    if ev.get("kind") == XEV_MARKER:
        if opts["event"] not in (None, "boot") or opts["subscriber"] \
                or opts["public"] or opts["pool"]:
            return []
        return [boot_line(h["boot_wall"], at, exported=True)]
    if not matches(ev, opts, pools):
        return []
    return raw_lines(ev, at, h) if opts["raw"] else event_lines(ev, at, pools)


def boot_line(boot_wall, at=None, exported=False):
    """
    The dataplane's start, as the BOOT line - aligned with the events'. Said
    once when the monitor starts, and again each time the box exports its
    boot marker (when a transport comes up), which a collector also gets.
    """
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot_wall))
    what = "boot marker exported: up since" if exported else \
        "this dataplane has been up since"
    return f"{clock(at or time.time())} {'BOOT':<9} {what} {since}"


def lost_line(t, n):
    """The line for n events this monitor fell too far behind to read."""
    return f"{clock(t)} ... {n} event(s) not shown: this monitor fell behind"


def stopped_line(t):
    """The last line, when the monitor stops."""
    return f"{clock(t)} stopped"


def syslog_escape(s):
    """RFC 5424 PARAM-VALUE escaping, as bpa_syslog_escape() does."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")


def raw_lines(ev, seen, h):
    """
    The wire forms of an event, rebuilt from it: the syslog line (when syslog
    is configured, with its PEN, PRI and hostname from the segment) and the
    IPFIX record's fields by IE number. Rebuilt with this monitor's clock -
    the box's own records carry the dataplane's, within a fraction of a
    second of it.
    """
    ms = int(seen * 1000)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seen)) + f".{ms % 1000:03d}Z"
    who = ev["identity"] or "unknown"
    out = [f"{clock(seen)}  IPFIX 323={ms} 230={ev['event']} 8={ev['priv']} "
           f"225={ev['pub']} 361={ev['lo']} 362={ev['hi']} 283={ev['pool']} "
           f"371={who}"]
    s = h["syslog"]
    if s["enabled"] and ev["event"] in MSGID:
        verb = "released" if ev["event"] == EVENT_RELEASE else "->"
        out.append(
            f"{'':14}<{s['pri']}>1 {ts} {s['hostname']} prisma-cgnat - "
            f"{MSGID[ev['event']]} [cgnat@{s['pen']} natEvent=\"{ev['event']}\" "
            f"subscriber=\"{ev['priv']}\" public=\"{ev['pub']}\" "
            f"portStart=\"{ev['lo']}\" portEnd=\"{ev['hi']}\" pool=\"{ev['pool']}\" "
            f"subscriberId=\"{syslog_escape(who)}\"] {ev['priv']} {verb} {ev['pub']} "
            f"ports {ev['lo']}-{ev['hi']} pool {ev['pool']}")
    return out


def delivery_lines(before, now, at):
    """
    What the export did between two parsed `show bpa logging` screens
    (logstate.parse_logging), per configured stream, plus the box-wide losses
    when any grew - each within 79 columns, wrapped and never cut. Empty when
    nothing moved: a quiet export prints nothing every 10 s.
    """
    from logstate import stream_rows
    old = {r["kind"]: r for r in stream_rows(before)} if before else {}
    lines = []
    for r in stream_rows(now):
        if not r["configured"]:
            continue
        o = old.get(r["kind"], {})
        sent = r["records"] - o.get("records", 0)
        fail = (r["send_failures"] + r["dropped"] + r["dropped_over_cap"]
                - o.get("send_failures", 0) - o.get("dropped", 0)
                - o.get("dropped_over_cap", 0))
        if before and not sent and not fail and \
                r["connection"] == o.get("connection") and \
                r["pending_bytes"] == o.get("pending_bytes"):
            continue
        who = "container" if r["carrier"] == "log-container" else "dataplane"
        parts = [f"by the {who}" + (f", {r['connection']}" if r["connection"]
                                    else "") + ":", f"+{sent} sent"]
        if r["pending_bytes"]:
            parts.append(f"{r['pending_bytes']} bytes waiting")
        if fail > 0:
            parts.append(f"{fail} LOST")
        lines += fit(f"{clock(at)} -- {r['kind']} {r['collector']}", parts)
    if before:
        words = {"exhaustion_repeats": "exhaustion repeats held back",
                 "queue_dropped": "dropped by a full worker queue",
                 "export_dropped": "dropped by a full export ring",
                 "transmit_failed": "packets the card failed to send"}
        for k, text in words.items():
            if now[k] > before[k]:
                lines.append(f"{clock(at)} -- +{now[k] - before[k]} {text}")
    return lines


def open_segment(path=SEGMENT):
    """
    The segment mapped read-only, as (mmap, None), or (None, why) where why
    is the refusal as (verdict, explanation...) - the verdict a row of its
    own, the explanation folded under it. PROT_READ: this reader cannot
    write the segment even by mistake.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return None, (f"no event segment at {path}",
                      "this dataplane keeps its events in its own heap (show "
                      "cgnat logging says why), or it is not running")
    except PermissionError:
        return None, (f"this account may not read {path}",)
    try:
        size = os.fstat(fd).st_size
        if size < HDR_BYTES:
            return None, (f"{path} is shorter than its header",)
        return mmap.mmap(fd, size, prot=mmap.PROT_READ), None
    finally:
        os.close(fd)


def event_record(ev, at, pools, h, raw=False):
    """
    One block event as the API's LogEvent fields (#558, phase 5): what
    event_lines() prints, as values - plus `text`, the VTY's own line(s).
    """
    kind = {EVENT_ALLOC: "alloc", EVENT_RELEASE: "release",
            EVENT_EXHAUSTED: "exhausted"}.get(ev["event"], f"event-{ev['event']}")
    who = ev["identity"] if ev["identity"] != "unknown" else ""
    lines = raw_lines(ev, at, h) if raw else event_lines(ev, at, pools)
    return {"kind": kind, "at": at, "subscriber": ev["priv"],
            "public": "" if ev["event"] == EVENT_EXHAUSTED else ev["pub"],
            "port_first": 0 if ev["event"] == EVENT_EXHAUSTED else ev["lo"],
            "port_last": 0 if ev["event"] == EVENT_EXHAUSTED else ev["hi"],
            "pool": pool_name(ev["pool"], pools), "pool_id": ev["pool"],
            "identity": who, "text": "\n".join(lines)}


def delivery_records(before, now, at):
    """
    delivery_lines() as LogEvent fields: one per configured stream that
    moved, and one per box-wide loss that grew, each with its line as `text`.
    """
    from logstate import stream_rows
    old = {r["kind"]: r for r in stream_rows(before)} if before else {}
    out = []
    for r in stream_rows(now):
        if not r["configured"]:
            continue
        o = old.get(r["kind"], {})
        sent = r["records"] - o.get("records", 0)
        lost = (r["send_failures"] + r["dropped"] + r["dropped_over_cap"]
                - o.get("send_failures", 0) - o.get("dropped", 0)
                - o.get("dropped_over_cap", 0))
        if before and not sent and not lost and \
                r["connection"] == o.get("connection") and \
                r["pending_bytes"] == o.get("pending_bytes"):
            continue
        one = {"kind": "delivery", "at": at, "stream": r["kind"],
               "collector": r["collector"], "carrier": r["carrier"],
               "connection": r["connection"], "sent": max(sent, 0),
               "lost": max(lost, 0), "pending_bytes": r["pending_bytes"]}
        out.append(one)
    lines = delivery_lines(before, now, at)
    for one in out:
        one["text"] = "\n".join(x for x in lines if f" -- {one['stream']} " in x)
    if before:
        for k in ("exhaustion_repeats", "queue_dropped", "export_dropped",
                  "transmit_failed"):
            if now[k] > before[k]:
                out.append({"kind": "delivery", "at": at, "stream": k,
                            "lost": now[k] - before[k],
                            "text": "\n".join(x for x in lines
                                              if x.startswith(f"{clock(at)} -- +{now[k] - before[k]} "))})
    return out


def watch(opts, buf, pools, read_logging, now=time.time, sleep=time.sleep,
          active=lambda: True):
    """
    `monitor cgnat logging` as a stream of LogEvent fields (#558, phase 5),
    for the API's WatchLogging: the same reader the VTY's monitor uses, the
    same filters, the same delivery lines every DELIVERY_EVERY - yielded as
    dicts until `active()` says the client went or `opts["seconds"]` ran out.
    `read_logging()` is `show bpa logging` parsed (logstate.parse_logging),
    or None. Like the VTY, it starts from now and takes nothing from the
    dataplane or the log container.
    """
    h = header(buf)
    if h is None:
        return
    yield {"kind": "started", "at": now(), "boot_wall": h["boot_wall"],
           "from_watch_ring": bool(h["mring"]), "pools_named": bool(pools),
           "text": "monitor cgnat logging"}
    if opts["event"] in (None, "boot"):
        yield {"kind": "boot", "at": now(), "boot_wall": h["boot_wall"],
               "text": boot_line(h["boot_wall"])}
    rd = reader(buf, h)
    before = read_logging()
    started, next_delivery = now(), now() + DELIVERY_EVERY
    while active() and (opts["seconds"] is None
                        or now() - started < opts["seconds"]):
        fresh = header(buf)
        if fresh and fresh["generation"] != h["generation"]:
            h = fresh
            rd = reader(buf, h)
            if opts["event"] in (None, "boot"):
                yield {"kind": "boot", "at": now(), "boot_wall": h["boot_wall"],
                       "text": boot_line(h["boot_wall"])}
        events, lost = rd.poll(buf)
        seen = now()
        if lost:
            yield {"kind": "lost", "at": seen, "lost": lost,
                   "text": lost_line(seen, lost)}
        for ev in events:
            at = ev.get("wall") or seen
            if ev.get("kind") == XEV_MARKER:
                if lines_for(ev, seen, h, pools, opts):
                    yield {"kind": "boot", "at": at, "boot_wall": h["boot_wall"],
                           "exported": True,
                           "text": boot_line(h["boot_wall"], at, exported=True)}
                continue
            if matches(ev, opts, pools):
                yield event_record(ev, at, pools, h, raw=opts["raw"])
        if not opts.get("no_deliveries") and now() >= next_delivery:
            next_delivery = now() + DELIVERY_EVERY
            cur = read_logging()
            if cur is not None:
                for d in delivery_records(before, cur, now()):
                    yield d
                before = cur
        sleep(POLL)
