"""
The PPPoE session trail, read (#597, docs/bng.md 0.7): who held which inside
address from when to when, as the control plane wrote it - and whether the
file is still what it wrote.

WHY IT IS READ HERE. A BNG hands the inside address out at session time, so
the CGNAT's record of a block names an inside address and no longer a
subscriber (docs/bng.md 3.1). The court-order question - which subscriber was
behind a public address and port at an instant - is answered in two steps:
the BPA log (or `show cgnat translation`, for a formula) gives the inside
address at that instant, and this trail gives the session that held it then.
`show pppoe history address <a> at <t>` is the second step;
`show cgnat translation <public> <port> [at <t>]` does both.

WHO WRITES IT. accel-ppp's prisma_trail module, in the prisma-bng container
(build/prisma_trail.c and build/accel-ppp-ng.patch): one line per start,
stop, carry and control-plane restart, appended and fsynced, each ending
` seq <n> sha256 <hex>` - the digest over the previous entry's digest and
this line up to its seq, the configuration trail's rule
(src/plugins/bpa/bpa_audit.c). Monthly the file is moved aside as
`<path>.<YYYY-MM>` and the sessions still held are restated (`carry`) at the
head of the next one, so the latest file whose month is not after an
instant answers for that instant alone.

WHO READS IT. The directory is root's 0750 (it holds the CGNAT trail), and
the VTY runs as the account that logged in, so the host's door reads it
(doors.answer_pppoe_history) and this module is both ends: the reading and
the selecting run on the host, the screens in the VTY. Any account may ask,
as any account may `show pppoe sessions` - the same users and addresses.
"""
import calendar
import hashlib
import json
import os
import re
import socket
import time

TRAIL_PATH = os.environ.get("PRISMA_PPPOE_TRAIL",
                            "/var/log/prisma-routing/pppoe-sessions.log")
#: The doors' socket, as doors.SOCKET; not imported, so the host's door can
#: import this module without the VTY's.
DOOR = os.environ.get("PRISMA_DOORS", "/run/prisma-routing/doors.sock")
#: `show pppoe history` with no `last` and no `at`.
SHOW_LAST = 50
#: The most sessions one answer carries: the door's reply is one line of at
#: most 1 MiB (doors._read_line), and a session is about 300 bytes of JSON.
ROWS_MAX = 2000
KINDS = ("start", "stop", "carry", "restart")

_STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{3})Z$")
_TAIL = re.compile(r" seq (\d+) sha256 ([0-9a-f]{64})$")
_SEGMENT = re.compile(r"\.(\d{4}-\d{2})$")
_SID = re.compile(r"^[0-9a-fA-F]{1,32}$")


def parse_stamp(text):
    """The trail's instant, `2026-10-13T14:02:11.123Z`, as epoch seconds."""
    m = _STAMP.match(text or "")
    if not m:
        return None
    y, mo, d, h, mi, s, ms = (int(x) for x in m.groups())
    try:
        return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) + ms / 1000.0
    except (ValueError, OverflowError):
        return None


def stamp(t):
    """Epoch seconds as the trail writes an instant: UTC, ms, Z."""
    ms = int(round((t - int(t)) * 1000))
    whole = int(t) + (1 if ms == 1000 else 0)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(whole)) + \
        f".{ms % 1000:03d}Z"


def unescape(word):
    """A field as written (%XX for a space, a control, '%'), as text."""
    if word == "-":
        return ""
    out, i = bytearray(), 0
    raw = word.encode("utf-8", "surrogateescape")
    while i < len(raw):
        if raw[i:i + 1] == b"%" and re.fullmatch(rb"[0-9A-F]{2}", raw[i + 1:i + 3]):
            out.append(int(raw[i + 1:i + 3], 16))
            i += 3
        else:
            out.append(raw[i])
            i += 1
    return out.decode("utf-8", "replace")


def parse_line(line):
    """
    One line as {"t", "kind", "fields", "seq", "sha", "content"}, or None
    when it is not an entry (a write cut short has no digest). `content` is
    what the digest covers: the line up to and including its seq.
    """
    line = line.rstrip("\n")
    m = _TAIL.search(line)
    if not m:
        return None
    content = line[:m.start() + len(f" seq {m.group(1)}")]
    words = line[:m.start()].split(" ")
    if len(words) < 2 or words[1] not in KINDS:
        return None
    t = parse_stamp(words[0])
    if t is None:
        return None
    fields = {}
    rest = words[2:]
    for i in range(0, len(rest) - 1, 2):
        fields[rest[i]] = rest[i + 1]
    return {"t": t, "kind": words[1], "fields": fields,
            "seq": int(m.group(1)), "sha": m.group(2), "content": content}


def digest(prev, content):
    """The chain's digest: sha256(previous digest's 32 bytes || content)."""
    return hashlib.sha256(prev + content.encode("utf-8", "surrogateescape")).hexdigest()


def segments(path=None):
    """The trail's files, oldest first: `<path>.<YYYY-MM>` by month, then
    `<path>` itself - the order the entries were written in."""
    path = path or TRAIL_PATH
    d, base = os.path.split(path)
    try:
        names = os.listdir(d or ".")
    except OSError:
        return []
    months = sorted(n for n in names if n.startswith(base + ".")
                    and _SEGMENT.search(n) and len(n) == len(base) + 8)
    out = [os.path.join(d, n) for n in months]
    if base in names:
        out.append(path)
    return out


def segment_month(path, live=None):
    """A moved-aside file's month, "YYYY-MM"; the live file's is its first
    entry's (`live` given), or None when it has none."""
    m = _SEGMENT.search(path)
    if m and path != live:
        return m.group(1)
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as fh:
            for line in fh:
                rec = parse_line(line)
                if rec:
                    return stamp(rec["t"])[:7]
    except OSError:
        pass
    return None


def files_for(t, path=None):
    """
    The files that answer for instant `t` and after: the latest one whose
    month is not after t's - which holds every session held at t, by its
    start or its carry - and every later one, for their stops. All of them
    when `t` is None.
    """
    path = path or TRAIL_PATH
    segs = segments(path)
    if t is None:
        return segs
    month = stamp(t)[:7]
    first = 0
    for i, p in enumerate(segs):
        m = segment_month(p, live=path)
        if m is not None and m <= month:
            first = i
    return segs[first:]


def records(files):
    """Every entry of `files`, in order; what is not an entry is passed over."""
    for p in files:
        try:
            with open(p, encoding="utf-8", errors="surrogateescape") as fh:
                for line in fh:
                    rec = parse_line(line)
                    if rec:
                        yield rec
        except OSError:
            continue


def _session(rec):
    """A session's dict from a start, carry or stop entry."""
    f = rec["fields"]
    since = parse_stamp(f.get("since", "")) if rec["kind"] != "start" \
        else rec["t"]
    return {"session": f.get("session", ""), "user": unescape(f.get("user", "-")),
            "mac": unescape(f.get("mac", "-")),
            "interface": unescape(f.get("interface", "-")),
            "address": f.get("address", ""), "start": since, "stop": None,
            "cause": ""}


def sessions(recs):
    """
    The entries paired into sessions, by session id, oldest start first: a
    start or a carry opens one (a carry of a session already open is the
    same session), a stop closes it - or, when the start is in a file not
    read, stands for the whole session alone, as it carries its `since`.
    A session with no stop is still held, or was when the file ends.
    """
    out, open_ = [], {}
    for rec in recs:
        kind, sid = rec["kind"], rec["fields"].get("session", "")
        if kind in ("start", "carry"):
            if sid not in open_:
                s = _session(rec)
                open_[sid] = s
                out.append(s)
        elif kind == "stop":
            s = open_.pop(sid, None)
            if s is None:
                s = _session(rec)
                out.append(s)
            s["stop"] = rec["t"]
            s["cause"] = rec["fields"].get("cause", "")
    out.sort(key=lambda s: (s["start"] or 0))
    return out


def select(found, user=None, address=None, session=None, at=None,
           since=None, last=None):
    """
    The sessions `show pppoe history` shows, oldest first: of `user`, of
    `address`, the `session`; held at instant `at` (begun at or before it,
    not ended before it); or held at any time from `since` on - then the
    `last` of what is left.
    """
    out = []
    for s in found:
        if user is not None and s["user"] != user:
            continue
        if address is not None and s["address"] != address:
            continue
        if session is not None and s["session"].lower() != session.lower():
            continue
        if at is not None and not (s["start"] is not None and s["start"] <= at
                                   and (s["stop"] is None or at < s["stop"])):
            continue
        if since is not None and s["stop"] is not None and s["stop"] < since:
            continue
        out.append(s)
    if last:
        out = out[-last:]
    return out


def history(filters, path=None):
    """
    {"sessions": [...], "more": n}: what select() gives for `filters` out of
    the files that can hold it - at most ROWS_MAX, the newest, and how many
    more there were.
    """
    f = dict(filters or {})
    when = f.get("at") if f.get("at") is not None else f.get("since")
    got = select(sessions(records(files_for(when, path))), **f)
    more = max(0, len(got) - ROWS_MAX)
    return {"sessions": got[more:], "more": more}


def verify(path=None):
    """
    The chain checked across every file, oldest first - the sequence before
    the digest, as the configuration trail's verifier does, so a removed
    entry is named as a removal and not as an edit of the innocent entry
    after it. {"files", "checked", "uncovered", "first", "head_seq",
    "head_sha", "problem", "kind", "entry"}: `problem` None when intact,
    else the lines that say where and what; `kind` then "unreadable",
    "removed" or "altered", and `entry` the entry it names (the API's
    verdict, docs/bng.md 0.4 item 2).
    """
    files = segments(path)
    rep = {"files": len(files), "checked": 0, "uncovered": [], "first": None,
           "head_seq": 0, "head_sha": "", "problem": None, "kind": "",
           "entry": 0}
    prev, expect = None, None
    for p in files:
        try:
            fh = open(p, encoding="utf-8", errors="surrogateescape")
        except OSError as e:
            rep["problem"] = [f"{os.path.basename(p)} cannot be read: "
                              f"{e.strerror}"]
            rep["kind"] = "unreadable"
            return rep
        with fh:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                rec = parse_line(line)
                where = f"{os.path.basename(p)} line {n}"
                if rec is None:
                    # A write cut short (a power cut on a filesystem that
                    # kept the fragment): no digest, so not an entry. The
                    # writer gave the next entry a line of its own and
                    # chained it to the last whole one, so the chain goes on.
                    rep["uncovered"].append(where)
                    continue
                if prev is None:
                    rep["first"] = rec["seq"]
                    if rec["seq"] != 1:
                        # The earlier files are not on this box: archived, or
                        # removed. What is here is checked from here on.
                        prev = bytes.fromhex(rec["sha"])
                        expect = rec["seq"] + 1
                        rep["checked"] += 1
                        rep["head_seq"], rep["head_sha"] = rec["seq"], rec["sha"]
                        continue
                    prev, expect = b"\0" * 32, 1
                if rec["seq"] != expect:
                    gone = rec["seq"] - expect
                    rep["problem"] = [
                        f"BROKEN before entry {rec['seq']} ({where})",
                        f"the sequence jumps from {expect - 1} to "
                        f"{rec['seq']}: " + (
                            f"{gone} entr{'y was' if gone == 1 else 'ies were'}"
                            f" removed" if gone > 0 else
                            "an entry is out of its place")]
                    rep["kind"], rep["entry"] = "removed", rec["seq"]
                    return rep
                if digest(prev, rec["content"]) != rec["sha"]:
                    rep["problem"] = [
                        f"BROKEN at entry {rec['seq']} ({where})",
                        "it does not match its own digest: it was changed "
                        "after it was written"]
                    rep["kind"], rep["entry"] = "altered", rec["seq"]
                    return rep
                prev, expect = bytes.fromhex(rec["sha"]), rec["seq"] + 1
                rep["checked"] += 1
                rep["head_seq"], rep["head_sha"] = rec["seq"], rec["sha"]
    return rep


def verify_screen(rep, path=None):
    """`show pppoe history verify`, from verify()'s report."""
    path = path or TRAIL_PATH
    files = rep.get("files", 0)
    out = [f"PPPoE session trail: {path}"]
    if files > 1:
        out.append(f"  and {files - 1} earlier file(s) beside it, one a month")
    if not files:
        return out + ["  no trail: no PPPoE session has been recorded here"]
    if rep.get("first") and rep["first"] != 1:
        out.append(f"  begins at entry {rep['first']}: the entries before it "
                   f"are in files no longer here")
    if rep.get("problem"):
        out.append(f"  {rep['problem'][0]}")
        out += [f"    {x}" for x in rep["problem"][1:]]
        out.append(f"  {rep.get('checked', 0)} entries before it verified")
        return out
    if not rep.get("checked"):
        return out + ["  no chained entries: nothing here is covered"]
    out.append(f"  {rep['checked']} entr{'y' if rep['checked'] == 1 else 'ies'}"
               f" checked, chain intact")
    for where in rep.get("uncovered", []):
        out.append(f"  not an entry: {where} (a write cut short)")
    out += [f"  head  seq {rep['head_seq']}",
            f"  sha256 {rep['head_sha']}",
            "  a chain that verifies proves the files were not edited, not "
            "that they",
            "  are the same files: compare the head with a copy kept off the "
            "box"]
    return out


def parse_show_args(words, parse_time, now=None):
    """
    `show pppoe history`'s words after `history`, as (filters, None) - the
    keywords select() takes - or (None, why). `parse_time` turns `at` and
    `since` into epoch seconds (events.parse_since: 30m, 2d, an ISO time
    with Z or an offset; with neither, the box's own zone).
    """
    f, i = {}, 0
    keys = ("user", "address", "session", "at", "since", "last")
    while i < len(words):
        key = words[i]
        if key not in keys:
            return None, (f"'{key}': show pppoe history takes user <name>, "
                          f"address <A.B.C.D>, session <id>, at <time>, "
                          f"since <time>, last <n>, or verify")
        if key in f:
            return None, f"'{key}' is given twice"
        if i + 1 >= len(words):
            return None, f"'{key}' needs a value after it"
        val = words[i + 1]
        if key == "last":
            if not re.fullmatch(r"[0-9]{1,6}", val) or not 1 <= int(val) <= ROWS_MAX:
                return None, f"last takes a number of sessions up to {ROWS_MAX}"
            f["last"] = int(val)
        elif key in ("at", "since"):
            try:
                f[key] = parse_time(val, now)
            except ValueError as e:
                return None, str(e)
        elif key == "address":
            if not re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", val) or \
                    any(int(x) > 255 for x in val.split(".")):
                return None, f"an inside address is IPv4: {val}"
            f["address"] = ".".join(str(int(x)) for x in val.split("."))
        elif key == "session":
            if not _SID.match(val):
                return None, f"a session id is hexadecimal: {val}"
            f["session"] = val
        else:
            f["user"] = val
        i += 2
    if "at" in f and "since" in f:
        return None, "at is one instant and since a period: give one of them"
    if "last" not in f and "at" not in f:
        f["last"] = SHOW_LAST
    return f, None


def session_lines(s, indent=""):
    """One session, three lines: who held what, when, and which session."""
    end = stamp(s["stop"]) if s["stop"] is not None else "still held"
    begin = stamp(s["start"]) if s["start"] is not None else "?"
    cause = f"  {s['cause']}" if s.get("cause") else ""
    return [f"{indent}{s['user'] or '-'} held {s['address']}",
            f"{indent}  from {begin}  to {end}{cause}",
            f"{indent}  session {s['session']}  mac {s['mac'] or '-'}  "
            f"on {s['interface'] or '-'}"]


def translation_rows(inside, when, held, why=None, configured=True):
    """
    What `show cgnat translation` adds below the dataplane's answer: the
    sessions that held `inside` at `when` - or, on a box with PPPoE
    (`configured`), that none did and, when the trail could not be read,
    why. On a box with no PPPoE and no session, nothing.
    """
    if held:
        out = [f"  PPPoE, at {stamp(when)}:"]
        for s in held:
            out += session_lines(s, "    ")
        return out
    if not configured:
        return []
    return [f"  PPPoE: no session held {inside} at {stamp(when)}"
            + (f" ({why})" if why else "")]


def history_screen(answer):
    """`show pppoe history`, from history()'s answer."""
    got = answer.get("sessions") or []
    if not got:
        return ["no PPPoE session matches"]
    out = []
    if answer.get("more"):
        out.append(f"{answer['more']} older session(s) match and are not "
                   f"shown: narrow it")
    for s in got:
        out += session_lines(s)
    return out


def _door(request, door=None):
    """`request` asked of the host's door; its reply, or None when no door
    listens (the VTY on the host, a lab): then this reads the files itself."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(door or DOOR)
    except OSError:
        s.close()
        return None
    try:
        s.settimeout(130)
        s.sendall(json.dumps(request).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode() or "{}")
    except (OSError, ValueError):
        return {"rc": 125, "out": "the host's door did not answer"}
    finally:
        s.close()


def fetch(filters, door=None, path=None):
    """
    (history()'s answer, None) for `filters`, through the host's door when
    one listens and from the files here when none does; (None, why) when it
    could not be read.
    """
    r = _door({"pppoe-history": dict(filters or {})}, door)
    if r is None:
        return history(filters, path), None
    if r.get("rc") != 0:
        return None, r.get("out") or "the host did not say why"
    try:
        got = json.loads(r.get("out") or "{}")
    except ValueError:
        return None, "the host's answer was not the trail's"
    return (got, None) if isinstance(got, dict) else \
        (None, "the host's answer was not the trail's")


def fetch_verify(door=None, path=None):
    """verify()'s report, through the door as fetch() reads; (rep, why)."""
    r = _door({"pppoe-history": {"verify": True}}, door)
    if r is None:
        return verify(path), None
    if r.get("rc") != 0:
        return None, r.get("out") or "the host did not say why"
    try:
        got = json.loads(r.get("out") or "{}")
    except ValueError:
        return None, "the host's answer was not the trail's"
    return (got, None) if isinstance(got, dict) else \
        (None, "the host's answer was not the trail's")


#: What a reading of the trail through the door may say, and the type of
#: each (doors.answer_pppoe_history).
FILTERS = {"user": str, "address": str, "session": str, "at": (int, float),
           "since": (int, float), "last": int, "verify": bool}


def answer(filters, path=None):
    """
    The door's answer to {"pppoe-history": filters}: {"rc", "out"}, the
    report of verify() when `verify` is set, history()'s otherwise. Types
    are checked here, on the host, whatever the VTY sent.
    """
    if not isinstance(filters, dict) or any(
            k not in FILTERS or not isinstance(v, FILTERS[k])
            or (isinstance(v, bool) and k != "verify")
            for k, v in filters.items()):
        return {"rc": 126, "out": "the host's door: not a reading of the "
                                  "PPPoE session trail"}
    if filters.get("verify"):
        return {"rc": 0, "out": json.dumps(verify(path))}
    f = {k: v for k, v in filters.items() if k != "verify"}
    return {"rc": 0, "out": json.dumps(history(f, path))}
