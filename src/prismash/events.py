"""
What happened on this box, in Prisma's words (#588): one record of events,
written by the components that act and read by `show log` and the API's
ListEvents.

WHY A RECORD OF ITS OWN. Before this the VTY's `show logging` was FRR's,
and it printed each routing daemon's logging CONFIGURATION - nothing that had
happened. What had happened was in the host's journal, under "door: uid 1000:
hostname -> 0" every 2 s and a confirm timer's "skipped because of an unmet
condition" every 16 s (measured on 10.40.200.166, 2026-10-05). And a restart
had no recorded cause at all: prisma-web read a dataplane younger than the
boot and said "restarted by itself" about an upgrade. So the record here is
written on purpose, one line per thing an operator would want to know, by
the code that did it - never scraped from a journal, so the noise cannot
reach it: a door call or a timer that finds nothing to do writes nothing.

WHERE. /var/log/prisma-routing/events.log, on the log volume the installer
gives that directory, so it survives a reboot and an upgrade. Bounded: when
the file would pass EVENTS_MAX it becomes events.log.1 and a new one starts,
so the record is never more than twice EVENTS_MAX and never less than
EVENTS_MAX of the newest events. One JSON object per line, appended with one
write, so a reader never sees half an event.

HOW IT GETS THERE FROM THE MANAGEMENT CONTAINER: THROUGH THE HOST'S DOOR,
NOT THE MOUNT. The container does mount /var/log/prisma-routing, but the VTY
in it runs as the account that logged in, and that directory is 0750 root
(appliance/install.sh) because the trail beside this file names subscribers.
Widening it so a CGNAT operator can append here would widen it for the trail
too; and an account writing the file directly could write any name into it.
The door (doors.answer_event) appends for any account and stamps the
account from SO_PEERCRED, so `who` is the kernel's word, not the caller's.
With no daemon listening - the host's own VTY, a lab - the write is local,
as every door falls back.

A RESTART'S CAUSE IS RECORDED BEFORE THE RESTART. Whoever asks for one -
`system dataplane restart`, `system upgrade`, `system reboot`, from the VTY
or the API - writes an "asked" event first, naming what it will restart.
The host's apply (`prismash --load` at boot and after a restart, `--reapply`
after Docker restarted a container) then reads the dataplane's and the
routing daemons' start times and, for a start it has not recorded yet,
writes a "start" event with the cause it finds: the boot, when the start is
within RESTARTED_AFTER of it; else the newest "asked" before it, within
CAUSE_WINDOW; else "unexpected" - nothing on the box asked for it. Never a
guess beyond that.
"""
import datetime
import json
import os
import re
import socket
import time

EVENTS_DIR = os.environ.get("PRISMA_EVENTS_DIR", "/var/log/prisma-routing")
EVENTS_PATH = os.environ.get("PRISMA_EVENTS",
                             os.path.join(EVENTS_DIR, "events.log"))
#: What the host's apply last recorded as started: the boot and each
#: component's start time. Root's, written only on the host (#588).
STATE_PATH = os.environ.get("PRISMA_EVENTS_STATE",
                            os.path.join(EVENTS_DIR, "events.state"))
#: A file's size before it is rotated: about 1500 events at ~170 bytes,
#: which is years of commits and restarts on a box (#588).
EVENTS_MAX = int(os.environ.get("PRISMA_EVENTS_MAX", "262144") or 262144)
#: The doors' socket, as doors.SOCKET; not imported, so the host's scripts
#: can run this file without the VTY's modules.
DOOR = os.environ.get("PRISMA_DOORS", "/run/prisma-routing/doors.sock")

#: Who says it. The issue's four, and the API beside the VTY, because a
#: commit from prisma-web is not one typed at the prompt.
SOURCES = ("vty", "api", "host", "dataplane", "routing")
#: Most severe first; `severity <level>` shows that level and every one above.
SEVERITIES = ("error", "warning", "notice", "info")
#: The components a start is recorded for, as `show system uptime` names
#: them; and the PPPoE control plane's container (#597), whose starts and
#: exits the host's watcher records (eventwatch.bng_changes) and which a
#: commit or a format upgrade asks to restart.
COMPONENTS = ("dataplane", "routing", "bng")
#: Why a component started. `operator` is `system dataplane restart`.
CAUSES = ("boot", "operator", "upgrade", "reboot", "shutdown", "unexpected")
#: The kinds an account that is not root may write: what it does at the VTY
#: or the API. A start, a boot and an upgrade done are the host's. A
#: "settings" event is the SNMP or the API settings a commit applied or was
#: refused (#588 phase 2), said by the session whose commit it was.
ACCOUNT_KINDS = ("commit", "rollback", "confirm", "confirm-expired", "asked",
                 "settings")
#: What the box SEES happen rather than what it is told (#588 phase 2), each
#: written by the host's watcher (eventwatch.py, `prismash --watch-events`)
#: as root, or by the host's own scripts: a link up or down, a module put in
#: or taken out or crossing a threshold, a BGP or BFD session, a CGNAT pool
#: out of blocks, the log exporter's collector lost or back, a login or a
#: failed one, and the dataplane given up on. Never an account's: no account
#: can write that a link went down. `show log kind <kind>` selects one.
WATCH_KINDS = ("link", "transceiver", "bgp", "bfd", "pool", "collector",
               "login", "gave-up", "radius")
#: A start's counterpart, for a component whose exits are seen (#597): the
#: PPPoE control plane's, with its code and why - "start" and "exit" are the
#: container's runs, "radius" its RADIUS servers failed and back.
KINDS = ACCOUNT_KINDS + ("start", "exit", "boot", "upgraded") + WATCH_KINDS
#: One sentence, not a screen.
TEXT_MAX = 300
WHO_MAX = 64
#: What an event is about - an interface, a peer, a pool, a module, an
#: account - and why, when the box knows: FRR's last reset, bfdd's
#: diagnostic, the threshold crossed (#588 phase 2). The reason is in the
#: sentence too; these are for a client that filters or groups.
SUBJECT_MAX = 64
REASON_MAX = 160
#: `show log` with no `last` and no `since`.
SHOW_LAST = 50

#: An "asked" more than this before a start is not that start's cause. A
#: dataplane restart is given 600 s to finish (dataplane_restart_detached).
CAUSE_WINDOW = 900
#: Two readings of one start differ by at most this. VPP's start is read as
#: now less its clock, and FRR's as a file's mtime. 10 s was the first cut,
#: and lab/show-log.sh's FRR, restarted 10 s after it first started, read
#: as the same start: two starts closer than this are one.
START_SLACK = 3
#: A start within this of the boot is the boot's (prismash.RESTARTED_AFTER).
RESTARTED_AFTER = 300

_RELATIVE = re.compile(r"^(\d+)\s*(s|m|min|h|d|w)$")
_UNIT = {"s": 1, "m": 60, "min": 60, "h": 3600, "d": 86400, "w": 604800}


def _clean(text, limit):
    """`text` on one line, its whitespace collapsed, at most `limit` long."""
    return " ".join(str(text or "").split())[:limit]


def make_event(source, severity, text, who="", origin="", now=None, **extra):
    """
    One event as it is written: {"t", "severity", "source", "text", "who",
    "from"} and whichever of kind, component, cause, affects and started are
    given. The text is put on one line - an event is a sentence, and a line
    break inside one would be read as a second event.
    """
    ev = {"t": round(time.time() if now is None else now, 3),
          "severity": severity, "source": source,
          "text": _clean(text, TEXT_MAX), "who": _clean(who, WHO_MAX),
          "from": _clean(origin, WHO_MAX)}
    for k, v in extra.items():
        if v not in (None, ""):
            ev[k] = v
    return ev


def event_error(ev, privileged=True):
    """
    Why `ev` is not an event this record takes, or None.

    The door's whole check (doors.answer_event): an event an account sends
    is written only when this says None. `privileged` is root: an account
    that is not may write only what it does - the kinds in ACCOUNT_KINDS,
    from the VTY or the API - and never a start, so no account can explain
    away a crash after the fact.
    """
    if not isinstance(ev, dict):
        return "not an event"
    if ev.get("source") not in SOURCES:
        return "source is one of " + ", ".join(SOURCES)
    if ev.get("severity") not in SEVERITIES:
        return "severity is one of " + ", ".join(SEVERITIES)
    text = ev.get("text")
    if not isinstance(text, str) or not text.strip() \
            or len(text) > TEXT_MAX or any(ord(c) < 32 for c in text):
        return f"the text is one line of at most {TEXT_MAX} characters"
    for k in ("who", "from"):
        v = ev.get(k, "")
        if not isinstance(v, str) or len(v) > WHO_MAX \
                or any(ord(c) < 32 for c in v):
            return f"{k} is one short line"
    if not isinstance(ev.get("t", 0), (int, float)):
        return "t is a time"
    kind = ev.get("kind", "")
    if kind and kind not in KINDS:
        return "kind is one of " + ", ".join(KINDS)
    if ev.get("component", "") and ev["component"] not in COMPONENTS:
        return "component is one of " + ", ".join(COMPONENTS)
    if ev.get("cause", "") and ev["cause"] not in CAUSES:
        return "cause is one of " + ", ".join(CAUSES)
    affects = ev.get("affects", "")
    if not isinstance(affects, str) or \
            any(a not in COMPONENTS for a in affects.split()):
        return "affects names components"
    for k, most in (("subject", SUBJECT_MAX), ("reason", REASON_MAX)):
        v = ev.get(k, "")
        if not isinstance(v, str) or len(v) > most \
                or any(ord(c) < 32 for c in v):
            return f"{k} is one line of at most {most} characters"
    if not isinstance(ev.get("started", 0), (int, float)):
        return "started is a time"
    if not privileged:
        if ev["source"] not in ("vty", "api"):
            return "an account writes events of the VTY or the API"
        if kind not in ACCOUNT_KINDS:
            return "an account writes what it did, not what the box did"
    return None


def append_event(ev, path=None, cap=None):
    """
    Append `ev` to the record at `path`, rotating it to `path`.1 first when
    this line would take it past `cap`. True when it was written.

    One write of one line under O_APPEND: two writers at once - the door's
    threads, the host's apply - interleave whole lines, never parts of them.
    """
    path = path or EVENTS_PATH
    cap = EVENTS_MAX if cap is None else cap
    line = (json.dumps(ev, sort_keys=True, separators=(",", ":")) + "\n").encode()
    try:
        try:
            if os.path.getsize(path) + len(line) > cap:
                os.replace(path, path + ".1")
        except FileNotFoundError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        return False
    return True


def read_events(path=None):
    """
    Every event in the record at `path` - the rotated file first - oldest
    first by time. A line that is not an event (a torn tail after a power
    cut) is passed over, not fatal.
    """
    path = path or EVENTS_PATH
    out = []
    for p in (path + ".1", path):
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(ev, dict) and \
                            isinstance(ev.get("t"), (int, float)):
                        out.append(ev)
        except OSError:
            continue
    out.sort(key=lambda e: e["t"])
    return out


def severity_rank(name):
    """Where `name` is in SEVERITIES, most severe 0; unknown is least."""
    return SEVERITIES.index(name) if name in SEVERITIES else len(SEVERITIES)


def select_events(events, last=None, since=None, severity=None, source=None,
                  kind=None, component=None):
    """
    The events `show log` shows, oldest first and newest last: those at or
    after `since`, at `severity` or more severe, from `source`, of `kind`
    and `component` - then the `last` of what is left.
    """
    out = []
    for ev in sorted(events, key=lambda e: e.get("t", 0)):
        if since is not None and ev.get("t", 0) < since:
            continue
        if severity and severity_rank(ev.get("severity")) > \
                severity_rank(severity):
            continue
        if source and ev.get("source") != source:
            continue
        if kind and ev.get("kind") != kind:
            continue
        if component and ev.get("component") != component:
            continue
        out.append(ev)
    if last is not None:
        out = out[-last:] if last > 0 else []
    return out


def parse_since(text, now=None):
    """
    The instant `since <text>` means, as epoch seconds; ValueError with the
    reason when it is none.

    Relative - 45s, 30m, 1h, 2d, 1w back from now - or an ISO date or time:
    2026-10-05, 2026-10-05T14:00, 2026-10-05 14:00:00, with Z or an offset.
    A date or time with no zone is the box's own zone, which is what every
    time `show log` prints is in.
    """
    now = time.time() if now is None else now
    t = (text or "").strip()
    m = _RELATIVE.match(t)
    if m:
        return now - int(m.group(1)) * _UNIT[m.group(2)]
    iso = t[:-1] + "+00:00" if t.endswith(("Z", "z")) else t
    try:
        d = datetime.datetime.fromisoformat(iso)
    except ValueError:
        raise ValueError(f"'{text}' is not a time: give 30m, 1h, 2d, or a "
                         f"date as 2026-10-05 or 2026-10-05T14:00") from None
    return d.timestamp()


def parse_show_args(words, now=None):
    """
    `show log`'s words after `log`, as (filters, None) - the keyword
    arguments select_events() takes - or (None, why) for the first word it
    refuses. With neither `last` nor `since`, the last SHOW_LAST.
    """
    f = {}
    i = 0
    while i < len(words):
        key = words[i]
        if key not in ("last", "since", "severity", "source", "kind"):
            return None, (f"'{key}': show log takes last <n>, since <time>, "
                          f"severity <level>, source <name>, kind <kind>")
        if key in f:
            return None, f"'{key}' is given twice"
        if i + 1 >= len(words):
            return None, f"'{key}' needs a value after it"
        val = words[i + 1]
        if key == "last":
            # ASCII digits only, as system.is_number (#412): '²' is a digit
            # to Python's digit test and a traceback to int().
            if not re.fullmatch(r"[0-9]{1,6}", val) or \
                    not 1 <= int(val) <= 100000:
                return None, f"last takes a number of events, not '{val}'"
            f["last"] = int(val)
        elif key == "since":
            try:
                f["since"] = parse_since(val, now)
            except ValueError as e:
                return None, str(e)
        elif key == "severity":
            if val not in SEVERITIES:
                return None, ("severity is one of " + ", ".join(SEVERITIES))
            f["severity"] = val
        elif key == "kind":
            # #588 phase 2: what an event is, beside who said it - `source
            # routing` is every BGP, BFD and FRR start; `kind bgp` is BGP's.
            if val not in KINDS:
                return None, "kind is one of " + ", ".join(KINDS)
            f["kind"] = val
        else:
            if val not in SOURCES:
                return None, "source is one of " + ", ".join(SOURCES)
            f["source"] = val
        i += 2
    if "last" not in f and "since" not in f:
        f["last"] = SHOW_LAST
    return f, None


#: How often a follower - `monitor log`, the API's WatchEvents (#588 phase
#: 3) - reads the record again, seconds.
FOLLOW_EVERY = 1.0
#: How far back each read of a follower looks, seconds. An event's time is
#: when it HAPPENED, not when it was appended: a start is written by the
#: host's apply up to minutes after it (observe_starts), a login carries
#: wtmp's time, a tick's events the reading's. A follower that read only
#: past the newest time it had seen would miss those; it reads this window
#: again and says what it has not said (follow_new), the CAUSE_WINDOW a
#: start's request is looked for in.
FOLLOW_LOOKBACK = 900
#: `monitor log seconds <n>`, at most: a day.
FOLLOW_SECONDS_MAX = 86400


def event_key(ev):
    """An event's identity for a follower: the whole of it, as written."""
    return json.dumps(ev, sort_keys=True, separators=(",", ":"))


def follow_new(got, seen, now):
    """
    The events of `got` - a read of the record over the last
    FOLLOW_LOOKBACK - that the follower has not said, oldest first; `seen`
    ({event_key: t}) is updated, and forgets what has left the window. An
    event appended late with an earlier time is said when it is appended,
    under its own time.
    """
    out = []
    for ev in sorted(got, key=lambda e: e.get("t", 0)):
        k = event_key(ev)
        if k in seen:
            continue
        seen[k] = ev.get("t", 0)
        out.append(ev)
    for k in [k for k, t in seen.items() if t < now - FOLLOW_LOOKBACK - 60]:
        del seen[k]
    return out


def parse_follow_args(words, now=None):
    """
    `monitor log`'s words after `log` (#588 phase 3): `show log`'s filters
    and `seconds <n>`, as (filters, seconds, None) or (None, None, why).
    `last` and `since` say what is shown before following; with neither,
    nothing is - a follow starts now - so `last` is absent from the
    filters then, where `show log` would put its 50.
    """
    seconds, rest, i = None, [], 0
    while i < len(words):
        if words[i] not in ("last", "since", "severity", "source", "kind",
                            "seconds"):
            return None, None, (f"'{words[i]}': monitor log takes last <n>, "
                                f"since <time>, severity <level>, source "
                                f"<name>, kind <kind>, seconds <n>")
        if words[i] == "seconds":
            if seconds is not None:
                return None, None, "'seconds' is given twice"
            v = words[i + 1] if i + 1 < len(words) else ""
            if not re.fullmatch(r"[0-9]{1,5}", v) or \
                    not 1 <= int(v) <= FOLLOW_SECONDS_MAX:
                return None, None, (f"seconds takes 1-{FOLLOW_SECONDS_MAX}, "
                                    f"not '{v}'")
            seconds = int(v)
        else:
            rest += words[i:i + 2]
        i += 2
    f, why = parse_show_args(rest, now)
    if why:
        return None, None, why
    if "last" not in rest[0::2] and "since" not in rest[0::2]:
        f.pop("last", None)
    return f, seconds, None


#: The console every row the VTY prints is held to (config.CONSOLE_COLUMNS),
#: and the indent an event's continuation rows take.
COLUMNS = 79
FOLD_INDENT = "    "


def format_event(ev, when, width=COLUMNS):
    """
    One event as `show log` prints it: its time in the box's zone (`when`
    formats an epoch), severity, source, and the sentence, with who and from
    where after it when they are known.

    FOLDED AT THE CONSOLE'S WIDTH, the continuation indented, like every other
    row this VTY prints (test_prismash_refusals.py holds them to it). One
    line whatever its length was the first cut, for `| match` to keep or drop
    an event whole; a 113-column row is read folded wherever the operator's
    window folds it, inside the words. So the first row carries what an event
    is found by - its time, severity, source and the start of its sentence -
    and `source` and `severity` select whole events.
    """
    import textwrap
    tail = ""
    who, frm = ev.get("who", ""), ev.get("from", "")
    if who and frm:
        tail = f" (by {who}, from {frm})"
    elif who:
        tail = f" (by {who})"
    elif frm:
        tail = f" (from {frm})"
    row = (f"{when(ev.get('t', 0))}  {ev.get('severity', ''):<7}  "
           f"{ev.get('source', ''):<9}  {ev.get('text', '')}{tail}")
    return "\n".join(textwrap.wrap(row, width, subsequent_indent=FOLD_INDENT,
                                   break_long_words=False,
                                   break_on_hyphens=False)) or row


def session_origin(environ=None):
    """
    Where the session acting now came from, as an event says it: "ssh
    <client address>", "the API", or "" when nothing says.

    In the management container sshd's SSH_CONNECTION is not there: the
    login's broker hands the client's address over the session door, which
    puts it in PRISMA_SESSION_FROM (doors.session_argv) after checking it is
    an address. The API's workers set PRISMA_EVENT_SOURCE (#588).
    """
    env = os.environ if environ is None else environ
    if env.get("PRISMA_EVENT_SOURCE") == "api":
        return "the API"
    addr = env.get("PRISMA_SESSION_FROM", "")
    if not addr:
        w = (env.get("SSH_CONNECTION") or "").split()
        addr = w[0] if len(w) == 4 else ""
    return f"ssh {addr}" if addr else ""


def event_source(environ=None):
    """'api' in an API worker, 'vty' everywhere else a person acts."""
    env = os.environ if environ is None else environ
    return "api" if env.get("PRISMA_EVENT_SOURCE") == "api" else "vty"


def _door(req, path=None, timeout=10):
    """
    The host door's reply to `req`, or None when nothing listens at `path` -
    which is a box with no daemon, or a lab, and the caller then acts
    locally, as every door's client does (doors.runner).
    """
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path or DOOR)
    except OSError:
        s.close()
        return None
    try:
        s.settimeout(timeout)
        s.sendall(json.dumps(req).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 8 << 20:
                break
        return json.loads(buf.decode())
    except (OSError, ValueError):
        return {"rc": 125, "out": "the host's door did not answer"}
    finally:
        s.close()


def record(ev, door=None, path=None):
    """
    Write `ev`, through the host's door when one listens and here when none
    does. True when it was written. Best effort and silent, like the SNMP
    state (prismash.ConfigState): an event is written after - or just before
    - what it describes, and an action is never refused or reported failed
    because its record could not be kept.
    """
    r = _door({"event": ev}, door)
    if r is not None:
        return r.get("rc") == 0
    return append_event(ev, path)


def fetch(filters=None, door=None, path=None):
    """
    The events `filters` (select_events' keywords) select, through the
    host's door when one listens - any account may read them - and from the
    file here when none does. [] when they cannot be read.
    """
    filters = dict(filters or {})
    r = _door({"events": filters}, door)
    if r is not None:
        if r.get("rc") != 0:
            return []
        try:
            got = json.loads(r.get("out") or "[]")
        except ValueError:
            return []
        return got if isinstance(got, list) else []
    return select_events(read_events(path), **filters)


def cause_of(events, component, started, boot, boot_reason="", after=None):
    """
    Why `component` started at `started`: (cause, detail).

    The boot, when the start is within RESTARTED_AFTER of it; else the
    newest "asked" naming the component, at most CAUSE_WINDOW before the
    start (and no later than START_SLACK after it, the slack of reading a
    start time) and after `after`, the component's start before this one;
    else "unexpected". The detail is the asking event's text and who, for
    the sentence the start event says.

    `after` because one request is one start's cause: lab/show-log.sh killed
    a dataplane 8 s after an operator's restart had brought it up, and with
    the window alone the second start was the operator's too.
    """
    # Only the first start since the boot is the boot's: on prisma-bench
    # (0.155.5) accel-pppd killed 12 s after the box came up was restarted by
    # its policy, and the window alone called that start "with the boot".
    if boot is not None and 0 <= started - boot <= RESTARTED_AFTER and \
            (after is None or after < boot):
        return "boot", boot_reason
    best = None
    for ev in events:
        if ev.get("kind") != "asked" or \
                component not in ev.get("affects", "").split():
            continue
        if not started - CAUSE_WINDOW <= ev.get("t", 0) <= \
                started + START_SLACK:
            continue
        if after is not None and ev.get("t", 0) <= after:
            continue
        if best is None or ev["t"] > best["t"]:
            best = ev
    if best is None:
        return "unexpected", ""
    detail = best.get("text", "")
    if best.get("who"):
        detail += f" by {best['who']}"
    if best.get("from"):
        detail += f" from {best['from']}"
    return best.get("cause") or "unexpected", detail


#: The sentence a start says, per component and cause.
_NAMES = {"dataplane": "the dataplane", "routing": "the routing daemons (FRR)",
          "bng": "the PPPoE control plane (prisma-bng)"}


def start_sentence(component, cause, detail):
    """The one sentence a start event says (#588)."""
    name = _NAMES[component]
    if cause == "boot":
        return f"{name} started with the boot" + \
            (f" ({detail})" if detail else "")
    if cause == "unexpected":
        return (f"{name} started again and nothing on the box asked for it: "
                f"unexpected")
    return f"{name} started again: {detail}" if detail else \
        f"{name} started again: {cause}"


def observe_starts(facts, events, seen, now=None):
    """
    The boot and start events the clocks in `facts` (Shell.uptime_facts())
    call for, against `seen` - what was last recorded - and `events`, the
    record to find each start's cause in: (new_events, new_seen). Pure.

    A start already in `seen`, within START_SLACK, is said nothing about. A
    first reading - a box upgraded onto this release, whose state has never
    been written - records the boot (which is a fact) but a start long after
    it only as seen: whatever caused it happened before the record existed,
    and "unexpected" would be a guess about a time nothing watched.
    """
    now = time.time() if now is None else now
    first = not seen
    new, seen = [], dict(seen or {})
    boot = facts.get("boot")
    reason = facts.get("boot_reason") or "not recorded"
    if boot is not None and (seen.get("boot") is None
                             or abs(boot - seen["boot"]) > START_SLACK):
        new.append(make_event("host", "notice",
                              f"the box booted; the reason recorded: {reason}",
                              now=boot, kind="boot"))
        seen["boot"] = boot
    for component, key in (("dataplane", "dataplane"),
                           ("routing", "control_plane")):
        started = facts.get(key)
        if started is None:
            continue
        prev = seen.get(component)
        if prev is not None and abs(started - prev) <= START_SLACK:
            continue
        seen[component] = started
        near_boot = boot is not None and 0 <= started - boot <= RESTARTED_AFTER
        if first and not near_boot:
            continue
        cause, detail = cause_of(events, component, started, boot,
                                 "" if reason == "not recorded" else reason,
                                 after=prev)
        new.append(make_event(
            component, "warning" if cause == "unexpected" else "notice",
            start_sentence(component, cause, detail), now=started,
            kind="start", component=component, cause=cause,
            started=round(started, 3)))
    return new, seen


def start_cause(events, component, started):
    """
    The cause the record gives the start of `component` at `started`, or ""
    when no start event matches it - one the host's apply has not seen yet.
    For `show system uptime` and GetSystem (#588).
    """
    if started is None:
        return ""
    for ev in reversed(events):
        if ev.get("kind") == "start" and ev.get("component") == component \
                and abs(ev.get("started", ev.get("t", 0)) - started) \
                <= START_SLACK:
            return ev.get("cause", "")
    return ""


def read_state(path=None):
    """events.state as a dict; {} when it is not there or not readable."""
    try:
        with open(path or STATE_PATH) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(state, path=None):
    """events.state, whole and by rename. False when it could not be."""
    path = path or STATE_PATH
    tmp = path + ".new"
    try:
        with open(tmp, "w") as fh:
            json.dump(state, fh, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        return False
    return True


# not a unit: the command line the host's shell scripts write through
# (appliance/upgrade); record() and make_event() are what it calls, and
# test/test_events.py runs it as a program.
def main(argv):
    root = ""
    if argv[:1] == ["--root"] and len(argv) > 1:
        root, argv = argv[1].rstrip("/"), argv[2:]
    if len(argv) >= 4 and argv[0] == "record":
        source, severity, text = argv[1], argv[2], " ".join(argv[3:])
        kind = ""
        if "=" in source:
            source, kind = source.split("=", 1)
        ev = make_event(source, severity, text, who="", origin="the box",
                        kind=kind)
        why = event_error(ev)
        if why:
            print(f"events: {why}")
            return 2
        if root:
            return 0 if append_event(ev, root + EVENTS_PATH) else 1
        return 0 if record(ev) else 1
    print("usage: events.py [--root <dir>] record <source>[=<kind>] "
          "<severity> <text>")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
