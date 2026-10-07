"""
Where the box's record of events goes (#588 phase 3): `system logging`, the
remote syslog destinations an operator configures for what `show log` shows,
fed from events.log by the host's watcher.

NOT THE CGNAT'S CONNECTION RECORDS. `nat` > `cgnat` > `logging syslog` (and
IPFIX) exports who held which public port, for a court order: a compliance
stream, written by the dataplane, spilled to disk when its collector is away
(docs/logging.md). This is the box's own record - commits, restarts, links,
sessions, logins - for the NOC's syslog. Two streams with two purposes, two
configurations and two collectors that SHOULD be different machines: one
setting cannot touch the other, and neither carries the other's lines.

WHY THE WATCHER FORWARDS, AND NOT RSYSLOG. The box has no rsyslog: Debian's
package is not on the medium, and install.sh installs no packages (it is
also the upgrade, which runs with no network). Adding one would make this a
medium release and leave a second daemon with a generated configuration to
keep in step. The host's watcher (`prismash --watch-events`) is already
resident, root and on the host, and reads the record's neighbour files; so
it follows events.log itself:

  * THE RECORD IS THE SPOOL. Each destination keeps its position in
    events.log (inode and offset, events-forward.state on the log volume),
    advanced only for what was sent. A TCP collector that is away, a
    restart of the watcher or a reboot loses nothing and repeats nothing;
    what waits is what events.log and events.log.1 still hold.
  * THE EVENT'S OWN FIELDS. The severity, the kind, the subject and who
    acted go out as RFC 5424's PRI and structured data, read off the JSON
    the record keeps - no line is parsed back.
  * A new destination starts at the end: history is not news, as the
    watcher's first reading of wtmp is not.

THE WIRE. RFC 5424, facility daemon (3), the severity mapped from the
event's: error 3, warning 4, notice 5, info 6. Over UDP one datagram per
event (port 514); over TCP framed by RFC 6587 octet counting (port 601),
the CGNAT exporter's framing (bpa_log_tcp.c says why it is not newlines).
The structured data's id is `event@67033`, under the product's own IANA
enterprise number (#554).
"""
import datetime
import errno
import ipaddress
import json
import os
import re
import socket
import time

import events

#: The destinations as the commit wrote them: one `host ...` statement a
#: line, in the VTY's words. Beside api.env, in the directory the management
#: container shares with the host, where the VTY writes and the watcher reads.
CONF_PATH = os.environ.get("PRISMA_LOGGING_CONF",
                           "/etc/prisma-routing/logging.conf")
#: Each destination's position in the record. On the log volume, beside
#: events-watch.state, so a reboot keeps it.
FWD_STATE = os.environ.get("PRISMA_LOGGING_FWD_STATE",
                           os.path.join(events.EVENTS_DIR,
                                        "events-forward.state"))
#: What each destination is doing, for `show system logging`: nothing in it
#: is secret, and it is 0644 in the directory the SNMP agent's state files
#: are in (/run, so it starts empty at each boot).
STATUS_PATH = os.environ.get("PRISMA_LOGGING_STATUS",
                             "/run/prisma-routing/state/logging")

PROTOCOLS = ("udp", "tcp")
#: RFC 5426's port for syslog over UDP, RFC 6587's for it over TCP.
DEFAULT_PORT = {"udp": 514, "tcp": 601}
#: Every event, by default: the collector gets the whole record unless the
#: operator narrows it, so nothing the box said is lost by a default.
DEFAULT_SEVERITY = "info"
#: Destinations, at most. A bound on a typo and on the watcher's tick.
HOSTS_MAX = 4
#: RFC 5424's "system daemons", apart from the CGNAT records' local0.
FACILITY = 3
SEVERITY_CODE = {"error": 3, "warning": 4, "notice": 5, "info": 6}
APP_NAME = "prisma-routing"
#: The structured data's id, under the product's PEN (system.PRODUCT_PEN).
SD_ID = "event@67033"
#: Events sent to one destination in one pass, at most: a collector back
#: after a day is caught up over a few seconds, not in one burst.
BATCH = 500
#: A TCP collector that refused is asked again after this long, so a dead
#: one does not stall the watcher's every second.
RETRY = 30
#: One connect or send, at most, seconds.
TIMEOUT = 2.0
#: The watcher forwards this often between its ticks, seconds.
FORWARD_EVERY = 1.0
#: A VRF is a Linux device the box made (#540): a name the kernel takes.
_VRF = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
_OPTS = ("port", "protocol", "severity", "source-address", "vrf")


# ------------------------------------------------------------ a destination

def parse_host(words):
    """
    `host <address> [port <n>] [protocol udp|tcp] [severity <level>]
    [source-address <address>] [vrf <name>]`'s words after `host`, as a
    destination dict, or (None, why) - the VTY's and the file's one rule.

    AN ADDRESS, NOT A NAME. The watcher would resolve a name on every send,
    and a box whose DNS is the thing that failed is the box whose events
    most need to leave it.
    """
    from system import address_error
    if not words:
        return None, "host: expected an address"
    why = address_error("logging host", words[0])
    if why:
        return None, why
    addr = str(ipaddress.ip_address(words[0]))
    d = {"address": addr, "port": 0, "protocol": "udp",
         "severity": DEFAULT_SEVERITY, "source-address": "", "vrf": ""}
    i, seen = 1, set()
    while i < len(words):
        k = words[i]
        if k not in _OPTS:
            return None, (f"'{k}': host takes port, protocol, severity, "
                          f"source-address and vrf")
        if k in seen:
            return None, f"'{k}' is given twice"
        if i + 1 >= len(words):
            return None, f"'{k}' needs a value after it"
        v = words[i + 1]
        seen.add(k)
        if k == "port":
            if not re.fullmatch(r"[0-9]{1,5}", v) or not 1 <= int(v) <= 65535:
                return None, f"port '{v}': expected 1-65535"
            d["port"] = int(v)
        elif k == "protocol":
            if v not in PROTOCOLS:
                return None, f"protocol '{v}': expected udp or tcp"
            d["protocol"] = v
        elif k == "severity":
            if v not in events.SEVERITIES:
                return None, ("severity is one of "
                              + ", ".join(events.SEVERITIES))
            d["severity"] = v
        elif k == "source-address":
            why = address_error("source-address", v)
            if why:
                return None, why
            d["source-address"] = str(ipaddress.ip_address(v))
        else:
            if not _VRF.match(v):
                return None, f"vrf '{v}': not a device name"
            d["vrf"] = v
        i += 2
    if d["source-address"] and ipaddress.ip_address(d["source-address"]) \
            .version != ipaddress.ip_address(addr).version:
        return None, "source-address and host are not of one family"
    if not d["port"]:
        d["port"] = DEFAULT_PORT[d["protocol"]]
    return d, None


def host_words(d):
    """
    A destination as the statement that makes it, `host <address>` and only
    the options that are not the defaults: the shape the file, `show
    running-config` and `show this` print.
    """
    w = [d["address"]]
    if d["port"] != DEFAULT_PORT[d["protocol"]]:
        w += ["port", str(d["port"])]
    if d["protocol"] != "udp":
        w += ["protocol", d["protocol"]]
    if d["severity"] != DEFAULT_SEVERITY:
        w += ["severity", d["severity"]]
    if d["source-address"]:
        w += ["source-address", d["source-address"]]
    if d["vrf"]:
        w += ["vrf", d["vrf"]]
    return "host " + " ".join(w)


def where(d):
    """'192.0.2.10:514 udp' - a destination as a row names it."""
    a = d["address"]
    a = f"[{a}]" if ":" in a else a
    return f"{a}:{d['port']} {d['protocol']}"


def read_conf(path=None):
    """
    The destinations logging.conf holds, in its order; [] with none. A line
    that is not one is passed over: the commit wrote this file through
    parse_host(), so a line it refuses is an edit by hand, and the rest
    still go out.
    """
    out = []
    try:
        with open(path or CONF_PATH) as fh:
            for line in fh:
                w = line.split()
                if len(w) < 2 or w[0] != "host":
                    continue
                d, why = parse_host(w[1:])
                if d and len(out) < HOSTS_MAX and \
                        d["address"] not in [x["address"] for x in out]:
                    out.append(d)
    except OSError:
        pass
    return out


def conf_text(dests):
    """logging.conf's text for `dests`."""
    return ("# Written by prismash: system > logging (#588). Edit through "
            "the VTY.\n" + "".join(host_words(d) + "\n" for d in dests))


def write_conf(dests, path=None):
    """(ok, message): logging.conf written whole, by rename. The watcher
    reads it again at its next pass, so nothing is restarted."""
    path = path or CONF_PATH
    tmp = path + ".new"
    try:
        with open(tmp, "w") as fh:
            fh.write(conf_text(dests))
        os.replace(tmp, path)
    except OSError as e:
        return False, f"{path}: {e.strerror}"
    n = len(dests)
    return True, (f"{n} remote syslog destination(s)" if n
                  else "no remote syslog destination")


# --------------------------------------------------------------- the wire

def _sd_value(v):
    """An SD-PARAM value escaped as RFC 5424 6.3.3 asks: \\, " and ]."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"') \
        .replace("]", "\\]")


def rfc5424(ev, hostname):
    """
    One event as an RFC 5424 message, bytes, unframed:

      <29>1 2026-10-07T00:01:01.250Z edge-1 prisma-routing - LINK
      [event@67033 source="dataplane" kind="link" subject="memif1/0"
      reason="link"] interface memif1/0: link down, ...

    The MSGID is the event's kind, upper case, a name a person filters on,
    as the CGNAT's BLOCK-ALLOC is; "-" for an event with none.
    """
    pri = FACILITY * 8 + SEVERITY_CODE.get(ev.get("severity"), 6)
    ts = datetime.datetime.fromtimestamp(
        ev.get("t", 0), datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    host = "".join(c for c in (hostname or "-") if 33 <= ord(c) <= 126)[:255]
    msgid = (ev.get("kind") or "-").upper()[:32]
    params = [("source", ev.get("source", ""))]
    for k, name in (("kind", "kind"), ("subject", "subject"),
                    ("reason", "reason"), ("who", "account"),
                    ("from", "origin"), ("cause", "cause"),
                    ("component", "component")):
        if ev.get(k):
            params.append((name, ev[k]))
    sd = "[" + SD_ID + "".join(f' {k}="{_sd_value(v)}"'
                               for k, v in params) + "]"
    text = ev.get("text", "")
    who, frm = ev.get("who", ""), ev.get("from", "")
    if who and frm:
        text += f" (by {who}, from {frm})"
    elif who:
        text += f" (by {who})"
    elif frm:
        text += f" (from {frm})"
    return f"<{pri}>1 {ts} {host or '-'} {APP_NAME} - {msgid} {sd} " \
           f"{text}".encode("utf-8", "replace")


def frame(msg, protocol):
    """A message as it goes on the wire: over TCP, RFC 6587's octet
    counting, its length and a space in front; over UDP, as it is."""
    return (b"%d " % len(msg)) + msg if protocol == "tcp" else msg


# ---------------------------------------------------------- the record

def read_new(path, pos, most=BATCH):
    """
    The events appended to the record at `path` since `pos` ({"ino",
    "off"}), at most `most`: (events, new_pos). With `pos` None, none and
    the end: a destination starts with what happens from now.

    ROTATION. append_event() renames the file to `path`.1 when it is full:
    a position in a file that is now `path`.1 reads the rest of that one
    first, then the new file from its start. A position in neither file -
    rotated twice while a collector was away - starts the new file, and what
    fell off is not sent: the record itself no longer has it.

    A line not ended yet is left for the next read: a writer's one write is
    a whole line, but a read can meet it half-flushed.
    """
    try:
        st = os.stat(path)
    except OSError:
        return [], pos
    if pos is None:
        return [], {"ino": st.st_ino, "off": st.st_size}
    out = []
    off = pos.get("off", 0)
    if pos.get("ino") != st.st_ino:
        try:
            old = os.stat(path + ".1")
        except OSError:
            old = None
        if old is not None and old.st_ino == pos.get("ino") \
                and off < old.st_size:
            got, rest = _read_lines(path + ".1", off, most)
            out += got
            if rest < old.st_size:
                return out, {"ino": old.st_ino, "off": rest}
        off = 0
    elif st.st_size < off:
        off = 0
    got, off = _read_lines(path, off, most - len(out))
    return out + got, {"ino": st.st_ino, "off": off}


def _read_lines(path, off, most):
    """(events, offset after the last whole line read) from `path` at
    `off`; a line that is not an event is passed over, as read_events()
    does."""
    out = []
    try:
        with open(path, "rb") as fh:
            fh.seek(off)
            while len(out) < most:
                line = fh.readline()
                if not line.endswith(b"\n"):
                    break
                off += len(line)
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and isinstance(ev.get("t"),
                                                       (int, float)):
                    out.append(ev)
    except OSError:
        pass
    return out, off


def wanted(ev, d):
    """Whether destination `d` takes `ev`: its severity or above."""
    return events.severity_rank(ev.get("severity")) <= \
        events.severity_rank(d["severity"])


# ----------------------------------------------------------- the sender

def open_socket(d):
    """
    A socket to destination `d`, bound to its source address and its VRF
    when it names them, connected (a TCP stream; a UDP socket is connected
    too, so the kernel answers an unreachable port as an error on the next
    send). Raises OSError with the kernel's reason.

    THE VRF IS SO_BINDTODEVICE on the VRF's Linux device (#540): the socket
    then routes by that VRF's table, as `ip vrf exec` does.
    """
    fam = socket.AF_INET6 if ":" in d["address"] else socket.AF_INET
    kind = socket.SOCK_STREAM if d["protocol"] == "tcp" else socket.SOCK_DGRAM
    s = socket.socket(fam, kind)
    try:
        s.settimeout(TIMEOUT)
        if d["vrf"]:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                         d["vrf"].encode())
        if d["source-address"]:
            s.bind((d["source-address"], 0))
        s.connect((d["address"], d["port"]))
    except OSError:
        s.close()
        raise
    return s


def _why(e):
    """An OSError as a short reason: the kernel's words."""
    return (e.strerror or errno.errorcode.get(e.errno, "") or str(e)).lower() \
        if isinstance(e, OSError) else str(e)


class Forwarder:
    """
    The watcher's follower of the record (#588 phase 3): run() reads what
    each destination has not been sent, sends it, and keeps where each one
    is. `connect` is open_socket(), and a test's stand-in.
    """

    def __init__(self, conf=None, record=None, state=None, status=None,
                 connect=None, hostname=None):
        self.conf = conf or CONF_PATH
        self.record = record or events.EVENTS_PATH
        self.state_path = state or FWD_STATE
        self.status_path = status or STATUS_PATH
        self.connect = connect or open_socket
        self.hostname = hostname
        self.dests, self.conf_mtime = [], None
        self.socks, self.retry_at, self.status = {}, {}, {}
        self.pos = self._load()

    def _load(self):
        """The positions events-forward.state keeps, {} with none."""
        try:
            with open(self.state_path) as fh:
                d = json.load(fh)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self):
        """events-forward.state, whole and by rename; False when it could
        not be written."""
        tmp = self.state_path + ".new"
        try:
            with open(tmp, "w") as fh:
                json.dump(self.pos, fh, sort_keys=True)
            os.replace(tmp, self.state_path)
        except OSError:
            return False
        return True

    def reload(self):
        """logging.conf read again when it changed: a destination gone is
        closed and forgotten, a new one starts at the record's end."""
        try:
            mtime = os.stat(self.conf).st_mtime_ns
        except OSError:
            mtime = None
        if mtime == self.conf_mtime:
            return False
        self.conf_mtime = mtime
        self.dests = read_conf(self.conf) if mtime is not None else []
        keys = {host_words(d) for d in self.dests}
        for k in list(self.socks):
            if k not in keys:
                self.close(k)
        for k in list(self.pos):
            if k not in keys:
                del self.pos[k]
        for k in list(self.status):
            if k not in keys:
                del self.status[k]
        return True

    def close(self, key):
        """Close destination `key`'s socket, if it has one."""
        s = self.socks.pop(key, None)
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    def run(self, now=None):
        """
        One pass: the configuration read again if it changed, and each
        destination sent what it has not been. Returns the number of events
        sent. Never raises for a destination: one that cannot be reached is
        said in the status and tried again, the others go on.
        """
        now = time.time() if now is None else now
        self.reload()
        moved, sent = False, 0
        for d in self.dests:
            k = host_words(d)
            st = self.status.setdefault(k, {"sent": 0, "failed": 0,
                                            "state": "waiting", "error": "",
                                            "last_sent": 0})
            if k not in self.pos:
                _, self.pos[k] = read_new(self.record, None)
                moved = True
                st["state"] = "ready"
            if self.retry_at.get(k, 0) > now:
                continue
            evs, nxt = read_new(self.record, self.pos[k])
            if not evs:
                if nxt != self.pos[k]:
                    self.pos[k], moved = nxt, True
                continue
            evs = [e for e in evs if wanted(e, d)]
            if not evs:
                self.pos[k], moved = nxt, True
                continue
            n, err = self.send(k, d, evs)
            if err:
                st.update(state="not reached", error=err)
                st["failed"] += 1
                self.retry_at[k] = now + RETRY
                continue
            st.update(state="sending", error="", last_sent=now)
            st["sent"] += n
            sent += n
            self.pos[k], moved = nxt, True
        if moved:
            self.save()
        self.write_status(now)
        return sent

    def send(self, key, d, evs):
        """(events sent, "" or why not) for `evs` to destination `d`. All
        or nothing as far as the position goes: a TCP stream that broke
        half way sends the batch again, which a collector sees as repeats
        rather than a gap."""
        host = self.hostname or socket.gethostname()
        try:
            if key not in self.socks:
                self.socks[key] = self.connect(d)
            s = self.socks[key]
            for ev in evs:
                s.sendall(frame(rfc5424(ev, host), d["protocol"]))
        except OSError as e:
            self.close(key)
            return 0, _why(e)
        return len(evs), ""

    def write_status(self, now):
        """The status file `show system logging` reads; False when it
        could not be written."""
        rows = []
        for d in self.dests:
            k = host_words(d)
            st = self.status.get(k, {})
            rows.append(dict(d, state=st.get("state", "waiting"),
                             error=st.get("error", ""),
                             sent=st.get("sent", 0),
                             failed=st.get("failed", 0),
                             last_sent=st.get("last_sent", 0)))
        tmp = self.status_path + ".new"
        try:
            with open(tmp, "w") as fh:
                json.dump({"t": now, "destinations": rows}, fh,
                          sort_keys=True)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.status_path)
        except OSError:
            return False
        return True


def read_status(path=None):
    """The status the watcher last wrote: {"t", "destinations"}, or None."""
    try:
        with open(path or STATUS_PATH) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


#: The watcher is said to be forwarding when its status is this recent.
STATUS_FRESH = 60


def status_fields(dests, status, now):
    """
    GetSystem's LoggingDestinationState fields for each destination
    configured (#588 phase 3): the destination as the proto names its
    fields, and the watcher's word on it - `known` False when it has not
    said within STATUS_FRESH, or said nothing of this one.
    """
    fresh = status is not None and 0 <= now - status.get("t", 0) \
        < STATUS_FRESH
    by = {host_words(r): r for r in (status or {}).get("destinations", [])
          if isinstance(r, dict) and "address" in r} if fresh else {}
    out = []
    for d in dests:
        r = by.get(host_words(d))
        out.append({
            "destination": {"address": d["address"], "port": d["port"],
                            "protocol": d["protocol"],
                            "severity": d["severity"],
                            "source_address": d["source-address"],
                            "vrf": d["vrf"]},
            "known": r is not None,
            "state": (r or {}).get("state", ""),
            "error": (r or {}).get("error", ""),
            "sent": int((r or {}).get("sent", 0)),
            "last_sent": float((r or {}).get("last_sent", 0) or 0)})
    return out


def status_rows(dests, status, now, when):
    """
    `show system logging`'s rows: each destination configured, and what the
    watcher says it is doing - as of its last pass, or that it has not said
    (stopped, or not yet at its pass since the commit). `when` formats an
    epoch.
    """
    if not dests:
        return ["no remote syslog destination: the record stays on the box "
                "('show log')"]
    out = []
    for d, f in zip(dests, status_fields(dests, status, now)):
        out.append(f"{where(d)}  {d['severity']} and above"
                   + (f"  from {d['source-address']}" if d["source-address"]
                      else "")
                   + (f"  vrf {d['vrf']}" if d["vrf"] else ""))
        if not f["known"]:
            out.append("  not forwarding yet: the watcher "
                       "(prisma-routing-events-watch) has not said")
            continue
        if f["state"] == "not reached":
            out.append(f"  not reached: {f['error'] or 'no answer'}; "
                       f"tried again every {RETRY} s")
        else:
            out.append(f"  {f['state']}")
        out.append(f"  sent {f['sent']} event(s) since the watcher "
                   f"started" + (f", the last at {when(f['last_sent'])}"
                                 if f["last_sent"] else ""))
    return out
