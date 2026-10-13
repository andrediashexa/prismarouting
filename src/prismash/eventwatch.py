"""
What the box sees happen, as events in its record (#588 phase 2): a link up
or down, a module put in or taken out or crossing its thresholds, a BGP or
BFD session, a CGNAT pool out of blocks, the log exporter's collector lost
and found, a login and a failed one.

A WATCHER THAT COMPARES READINGS, NEVER A LOG LINE. Each kind is a reading
of the box's own state - the dataplane's interfaces, `show bpa transceiver`,
FRR's neighbours as JSON, bfdd's sessions, `show bpa blocks`, `show bpa
logging` - taken again a few seconds later and compared with the one before,
as observe_starts() compares the clocks. What changed between two readings
is an event; nothing else is. So the journal's noise has no way in, the same
argument phase 1 made: a door call, a timer run, FRR's debug are not
readings. Logins are the one kind that is a record and not a state, and they
are read from wtmp and btmp - sshd's and login(1)'s own fixed-size utmp
records (utmp(5)), never sshd's log lines: see read_utmp().

THIS FILE IS THE PURE HALF. Shell.watch_tick() (prismash.py) takes the
readings and hands them here with the state the last tick left; tick()
returns the events and the new state. Everything that decides - what is a
change, what is said, what is held back - is here and is driven by
test/test_eventwatch.py with readings built by hand and times pinned.

WHAT A RESTART OF THE WATCHER DOES NOT DO: SAY EVERYTHING AGAIN. The last
readings are kept in events-watch.state beside events.state, written only
when they change. A first reading with no state is a baseline and says
nothing, as observe_starts()' first reading says nothing of a start long
before it: whatever changed before anything watched is not known.

A START IS NOT A CHANGE OF EVERYTHING. Within SETTLE of the dataplane's
start its readings (links, modules, pools, the exporter) are not compared,
and within SETTLE of the routing daemons' start FRR's (BGP, BFD) are not:
a restart is already an event of its own (#588 phase 1), and every link
coming up after it is the restart, not news. After SETTLE the reading is
compared with the one from before the restart, so a link that was up and is
still down a minute after the dataplane came back IS said. Counters that a
restart zeroes (FRR's connections, the pools' refusals) are read as a new
baseline across one.

WHAT STANDS WHEN IT STARTS IS SAID ONCE (#588 phase 3). Comparing
readings says only what changes, so a condition already true when the
watcher starts was never said: on 10.40.200.166 (2026-10-06) the X520's RX
power stood below its low warning before the watcher first ran, and nothing
recorded it. So each kind's first reading of a boot is also read for what is
abnormal in it - a port administratively up whose link is down, a module
past a warning or an alarm, a configured BGP or BFD session that is not up,
a pool with no block free that has refused, a log stream whose collector is
not reached - and each is said once, in the present tense, "standing at
start: ...". The state file carries the boot it was said for (`stood`), so a
restart of the watcher within the same boot says nothing again; healthy
things are never announced.

NOISE IS BOUNDED, BY SUBJECT. A link that flaps would write an event per
reading; flap_admit() lets FLAP_EVENTS changes of one subject through in
FLAP_WINDOW and holds the rest, counted, and flap_summaries() says them in
one event per window: "interface X flapped 37 times in 5 min, not each
recorded; it is now up". A burst of failed logins from one address is held
the same way. The record's own rotation (events.EVENTS_MAX) bounds the rest.
"""
import json
import os
import re
import struct
import time

import events

STATE_PATH = os.environ.get("PRISMA_WATCH_STATE",
                            os.path.join(events.EVENTS_DIR,
                                         "events-watch.state"))
#: The login records sshd and login(1) keep (utmp(5)): every login and
#: logout in wtmp, every failed password in btmp. The host's, read as root.
WTMP = os.environ.get("PRISMA_WTMP", "/var/log/wtmp")
BTMP = os.environ.get("PRISMA_BTMP", "/var/log/btmp")

#: The loop's period, seconds. Each kind is read at its own CADENCE, a
#: multiple of it.
TICK = 10
#: How often each kind is read, seconds. Links, sessions, the exporter and
#: the login records every tick: each is one vppctl, one vtysh or one stat.
#: The pools every 30 s: `show bpa blocks` walks every worker's slice.
#: Modules every 60 s, which is BPA_XCVR_STATIC_SECONDS: the plugin keeps a
#: module's identity and thresholds that long and reads its levels over I2C
#: (src/plugins/bpa/bpa_transceiver.c), so a reading every minute costs the
#: card one identity read and 24 bytes of levels, and the API's own advice
#: for ListTransceivers is minutes, not seconds.
CADENCE = {"link": 10, "bfd": 10, "bgp": 10, "collector": 10, "login": 10,
           "pool": 30, "transceiver": 60, "bng": 10, "radius": 10}
#: The kinds read from the dataplane, and from the routing daemons: within
#: SETTLE of that component's start they are not compared.
DATAPLANE_KINDS = ("link", "transceiver", "pool", "collector")
ROUTING_KINDS = ("bgp", "bfd")
#: The PPPoE control plane's (#597): its runs, from a file on the host, and
#: its RADIUS servers, from accel-ppp's `show stat` - read only while the
#: container runs and RADIUS is configured, so a box with no PPPoE pays one
#: stat a tick. No SETTLE: a run's record has its own times, and the
#: servers' reading carries accel-pppd's uptime (radius_changes).
BNG_KINDS = ("bng", "radius")
SETTLE = 60
#: A subject changing more than FLAP_EVENTS times in FLAP_WINDOW seconds is
#: flapping: the rest of the window is counted, not written.
FLAP_WINDOW = 300
FLAP_EVENTS = 4
#: What is abnormal in a component's first reading is said as standing only
#: once the component has run this long (#588 phase 3). Longer than SETTLE:
#: at a boot a BGP session is still connecting a minute after FRR started
#: (FRR's connect retry is 120 s), and "not established" said then would be
#: the boot, not news. A component whose start is not known is not held.
STANDING_SETTLE = 180
#: Standing conditions said one by one per kind, at most: a box whose 300
#: unused ports are up and dark says 20 and how many more, not 300.
STANDING_MAX = 20
#: The words every standing event starts with.
STANDING = "standing at start: "
#: Sessions a logout is matched against, at most: wtmp lines whose logout
#: never came (a power cut) must not grow the state for ever.
OPEN_LOGINS_MAX = 256
#: utmp records read per file per tick, at most: a brute force between two
#: ticks is read over the next ones, not in one gulp.
UTMP_PER_TICK = 2048


# ------------------------------------------------------------------ state

def read_state(path=None):
    """events-watch.state as a dict; {} when it is not there or not one."""
    try:
        with open(path or STATE_PATH) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(state, path=None):
    """events-watch.state, whole and by rename. False when it could not be."""
    path = path or STATE_PATH
    tmp = path + ".new"
    try:
        with open(tmp, "w") as fh:
            json.dump(state, fh, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def state_moved(before, after):
    """Whether `after` differs from `before` in more than the reading
    times: the watcher writes its state file only then."""
    keep = [k for k in set(before or {}) | set(after or {}) if k != "last"]
    return any((before or {}).get(k) != (after or {}).get(k) for k in keep)


def due(state, kind, now, force=False):
    """Whether `kind` is to be read at `now`: its CADENCE has passed since
    the last reading (a clock that went back counts as passed)."""
    if force:
        return True
    last = (state.get("last") or {}).get(kind)
    return last is None or not 0 <= now - last < CADENCE[kind] - 1


# ---------------------------------------------------------------- flapping

def flap_admit(flaps, c, now):
    """
    Whether the change `c` (a change dict, see tick()) at `now` is written
    as an event of its own. `flaps` is the state's record of recent changes
    per subject - per c["key"] - updated here; c["what"] names the subject
    in a summary ("interface X") and c["state"] is what it is now.

    FLAP_EVENTS changes in FLAP_WINDOW are written one by one. The next one
    starts a hold: from then on each change is counted, and flap_summaries()
    says the count once per window. A hold ends after a whole window with
    no change.
    """
    f = flaps.setdefault(c["key"], {"times": [], "since": 0, "count": 0,
                                    "held": 0})
    f.update(what=c["what"], now=c["state"], source=c["source"],
             kind=c["kind"], subject=c["subject"])
    if f["since"]:
        f["count"] += 1
        f["held"] += 1
        return False
    f["times"] = [t for t in f["times"] if 0 <= now - t < FLAP_WINDOW]
    if len(f["times"]) >= FLAP_EVENTS:
        f["since"] = f["times"][0]
        f["count"] = len(f["times"]) + 1
        f["held"] = 1
        return False
    f["times"].append(now)
    return True


def flap_sentence(f, minutes):
    """The one sentence a held window says (#588 phase 2)."""
    if f["kind"] == "login":
        return (f"{f['what']}: {f['count']} in {minutes} min, not each "
                f"recorded")
    return (f"{f['what']} flapped {f['count']} times in {minutes} min, not "
            f"each recorded; it is now {f['now']}")


def flap_summaries(flaps, now):
    """
    The summary events of every hold whose window has ended at `now`, and
    the holds ended: a window with changes held says them in one event and
    a new window starts; a window with none ends the hold, and the subject's
    next change is written as any other.
    """
    out = []
    for key in sorted(flaps):
        f = flaps[key]
        if not f.get("since"):
            if not [t for t in f.get("times", []) if 0 <= now - t
                    < FLAP_WINDOW]:
                del flaps[key]
            continue
        if now - f["since"] < FLAP_WINDOW:
            continue
        if f["held"]:
            minutes = max(1, int(round((now - f["since"]) / 60)))
            out.append(events.make_event(
                f["source"], "warning", flap_sentence(f, minutes), now=now,
                kind=f["kind"],
                subject=events._clean(f["subject"], events.SUBJECT_MAX),
                reason=f"{f['count']} changes in {minutes} min"))
            f.update(since=now, count=0, held=0, times=[])
        else:
            del flaps[key]
    return out


# ------------------------------------------------------------------- links

def link_reading(ifaces, hardware, counts=None):
    """
    The links of the interfaces an operator configured, from
    routes.parse_interfaces() (the admin state) and routes.parse_hardware()
    (the link): {name: {"admin", "link", "parent", "ups", "downs"}}. A
    sub-interface's link is its parent's (Shell.link_state says why), and
    `parent` names it. `ups` and `downs` are the plugin's count of the
    port's link changes (parse_link_changes(), #588), None for a port it
    does not count or a plugin older than the count.
    """
    import routes
    out = {}
    counts = counts or {}
    names = set(routes.operator_interfaces(ifaces))
    for name in sorted(names):
        parent = name.split(".")[0] if "." in name else ""
        row = hardware.get(name) or (hardware.get(parent) if parent else None)
        n = counts.get(name) if not parent else None
        out[name] = {"admin": ifaces[name].get("state", "?"),
                     "link": row["link"] if row else "?",
                     "parent": parent if parent in ifaces else "",
                     "ups": n["ups"] if n else None,
                     "downs": n["downs"] if n else None}
    return out


#: `show bpa link-changes`' line (bpa_linkcount.c's format string, which
#: test/test_eventwatch.py holds this to).
_LINK_CHANGES = re.compile(r"^interface (\S+) link (up|down) ups (\d+) "
                           r"downs (\d+)$")


def parse_link_changes(text):
    """
    `show bpa link-changes` as {port: {"link", "ups", "downs"}}: how many
    times each port's link came up and went down since the port was created
    (#588). Empty for anything else - a plugin older than the command
    answers "unknown input", and a reading with no count is compared by its
    state alone, as before.
    """
    out = {}
    for line in (text or "").splitlines():
        m = _LINK_CHANGES.match(line.strip())
        if m:
            out[m.group(1)] = {"link": m.group(2), "ups": int(m.group(3)),
                               "downs": int(m.group(4))}
    return out


def _sysfs_word(path):
    """The first word of a sysfs attribute, or None when it cannot be read
    (an attribute the kernel refuses - `carrier` of a card that is
    administratively down answers EINVAL - or one this kernel has not)."""
    try:
        with open(path) as fh:
            w = fh.read().split()
        return w[0] if w else None
    except OSError:
        return None


def _count(word):
    """A sysfs count as an int - ASCII digits and nothing else, what the
    kernel's %u prints - or None."""
    return int(word) if word and re.fullmatch(r"[0-9]+", word) else None


def linux_link_reading(names, sysdir="/sys/class/net"):
    """
    The links of the cards Linux holds (the management card, a spare port
    the dataplane was not given), as link_reading() rows with `host` true
    (#588): the administrative state from IFF_UP in `flags`, the link from
    `carrier`, and the kernel's own count of the carrier's changes,
    `carrier_up_count` and `carrier_down_count` (Linux 4.16+), so a flap
    between two readings is seen as on a dataplane port. A card that is not
    there is left out: it was not seen, which is not "down".
    """
    out = {}
    for name in sorted(set(names)):
        base = os.path.join(sysdir, name)
        flags = _sysfs_word(os.path.join(base, "flags"))
        if flags is None:
            continue
        try:
            admin = "up" if int(flags, 16) & 0x1 else "down"
        except ValueError:
            continue
        carrier = _sysfs_word(os.path.join(base, "carrier"))
        ups = _sysfs_word(os.path.join(base, "carrier_up_count"))
        downs = _sysfs_word(os.path.join(base, "carrier_down_count"))
        out[name] = {"admin": admin,
                     "link": "up" if carrier == "1" else "down",
                     "parent": "", "host": True,
                     "ups": _count(ups), "downs": _count(downs)}
    return out


def _link_what(name, c):
    """How a sentence names a link_reading() row: a sub-interface, a
    dataplane port, or a card Linux holds."""
    if c.get("parent"):
        return "sub-interface " + name
    return f"interface {name}" + (" (held by Linux)" if c.get("host") else "")


def link_transitions(p, c, restarted=False):
    """
    (ups, downs) between two readings of one port, from its counts, or None
    when they do not say: a count missing on either side, one that went
    backwards (the port deleted and made again, the plugin or the card's
    driver reloaded), or a dataplane port read across a dataplane start,
    whose counts began again (#588). A card Linux holds is not the
    dataplane's: its counts survive a dataplane start.
    """
    if restarted and not c.get("host"):
        return None
    if None in (p.get("ups"), p.get("downs"), c.get("ups"), c.get("downs")):
        return None
    du, dd = c["ups"] - p["ups"], c["downs"] - p["downs"]
    if du < 0 or dd < 0:
        return None
    return du, dd


def link_changes(prev, cur, restarted=False):
    """
    What changed between two link_reading()s, as change dicts (see tick()).

    ADMINISTRATIVE OR NOT is the first thing said, because it is the first
    question (Shell.show_interfaces_terse: up/down is a wire, down/down a
    person). A sub-interface's link is its parent's, so a parent's link
    going down is ONE event that counts the sub-interfaces it took with it -
    an ISP's port carries hundreds - and a sub-interface says only its own
    administrative changes. An interface that appeared or went is a commit,
    already in the record.

    A FLAP BETWEEN TWO READINGS (#588): a port whose link reads the same
    twice but whose count of changes moved went down and came back (or up
    and down again) in between - a reading every 10 s saw nothing, and that
    is the flap an operator chasing a bad fibre needs. It is said with how
    many times, as bgp_changes() says a session's; a change of state that
    took more than one transition says how many. `restarted` is a reading
    across a dataplane start, whose counts began again: states only, for
    the dataplane's ports.
    """
    out = []
    for name in sorted(set(prev) & set(cur)):
        p, c = prev[name], cur[name]
        what = _link_what(name, c)
        src = "host" if c.get("host") else "dataplane"
        if p["admin"] != c["admin"] and "?" not in (p["admin"], c["admin"]):
            if c["admin"] == "down":
                text = f"{what} is administratively down: shut down by " \
                       f"configuration"
            else:
                text = f"{what} is administratively up" + (
                    "; its link is down: the wire or the far end"
                    if c["link"] == "down" else "")
            out.append({"key": "link:" + name, "source": src,
                        "kind": "link", "subject": name,
                        "severity": "notice", "text": text,
                        "reason": "administrative", "what": what,
                        "state": "administratively " + c["admin"]})
            continue
        if c["parent"] or c["admin"] != "up" \
                or "?" in (p["link"], c["link"]):
            continue
        moved = link_transitions(p, c, restarted)
        subs = sum(1 for n, s in cur.items()
                   if s["parent"] == name and s["admin"] == "up")
        if p["link"] == c["link"]:
            # The same state twice; the count says whether it stayed so.
            n = (moved[1] if c["link"] == "up" else moved[0]) if moved else 0
            if not n:
                continue
            if c["link"] == "up":
                text = f"{what}: link went down and came back between two " \
                       f"readings ({n} time(s)): the wire or the far end"
            else:
                text = f"{what}: link came up and went down again between " \
                       f"two readings ({n} time(s)); it is down"
            if subs:
                text += f"; its {subs} sub-interface(s) with it"
            out.append({"key": "link:" + name, "source": src,
                        "kind": "link", "subject": name,
                        "severity": "warning", "text": text,
                        "reason": "flap", "what": what,
                        "state": "link " + c["link"]})
            continue
        if c["link"] == "down":
            text = f"{what}: link down, administratively up: the wire or " \
                   f"the far end"
            if subs:
                text += f"; its {subs} sub-interface(s) with it"
            sev, reason = "warning", "link"
        else:
            text = f"{what}: link up"
            if subs:
                text += f", and its {subs} sub-interface(s) with it"
            sev, reason = "notice", "link"
        if moved and sum(moved) > 1:
            text += f" ({sum(moved)} changes since the last reading)"
        out.append({"key": "link:" + name, "source": src,
                    "kind": "link", "subject": name, "severity": sev,
                    "text": text, "reason": reason, "what": what,
                    "state": "link " + c["link"]})
    return out


# ------------------------------------------------------------ transceivers

#: How bad a level is, for the worst of a module's lanes.
_LEVEL_RANK = {"": 0, "normal": 1, "low-warning": 2, "high-warning": 2,
               "low-alarm": 3, "high-alarm": 3}


def _worst(quantities):
    """The worst-ranked of `quantities` (decode()'s {"value", "thresholds",
    "level"} dicts, None for one not measured), or None."""
    best = None
    for q in quantities:
        if not q or not q.get("level"):
            continue
        if best is None or _LEVEL_RANK.get(q["level"], 0) > \
                _LEVEL_RANK.get(best["level"], 0):
            best = q
    return best


def module_reading(modules):
    """
    The modules of the box's ports, from transceiver.decode() of each
    reading - the plugin's `show bpa transceiver` for a card the dataplane
    drives through DPDK, the kernel's module EEPROM interface for a card
    Linux holds (#588): {port: {"present", "module", "rx", "rx_dbm",
    "rx_bound", "temp", "temp_c", "temp_bound", "host"}}, `host` true for
    Linux's. A port whose module could not be read either way
    ("unreadable": a virtio or veth card, whose driver has no module to
    read, answers EOPNOTSUPP) is left out: neither in nor out is known, and
    a card with no cage says nothing, ever.
    """
    import transceiver as xc
    out = {}
    for m in modules:
        st = m.get("status")
        if st not in ("ddm", "no-ddm", "copper", "absent"):
            continue
        r = {"present": st != "absent",
             "module": " ".join(x for x in (m.get("vendor", ""),
                                            m.get("part", "")) if x)
             + (f" (serial {m['serial']})" if m.get("serial") else ""),
             "rx": "", "rx_dbm": "", "rx_bound": "", "temp": "",
             "temp_c": "", "temp_bound": "",
             "host": m.get("source") == "linux"}
        rx = _worst(ln.get("rx_power") for ln in m.get("lanes") or [])
        if rx:
            r["rx"] = rx["level"]
            v = xc.mw_to_dbm(rx["value"])
            r["rx_dbm"] = "no light" if v == float("-inf") else \
                f"{xc.fmt_dbm(v)} dBm"
            r["rx_bound"] = _bound(
                rx, lambda x: f"{xc.fmt_dbm(xc.mw_to_dbm(x))} dBm")
        t = _worst([m.get("temperature")])
        if t:
            r["temp"] = t["level"]
            r["temp_c"] = f"{t['value']:.1f} C"
            r["temp_bound"] = _bound(t, lambda x: f"{x:.1f} C")
        out[m["name"]] = r
    return out


def _bound(q, fmt):
    """The threshold a level is past, formatted, or "" for "normal"."""
    lvl = q.get("level", "")
    th = q.get("thresholds") or {}
    if lvl in ("", "normal"):
        return ""
    side, sort = lvl.split("-")
    v = th.get(f"{side}_{sort}")
    return f"its {side} {sort} ({fmt(v)})" if v is not None else ""


def module_changes(prev, cur):
    """
    What changed between two module_reading()s: a module put in, taken out
    or replaced, and its RX power or temperature crossing a warning or an
    alarm threshold, or coming back inside them (#590's levels).
    """
    out = []
    for port in sorted(set(prev) & set(cur)):
        p, c = prev[port], cur[port]
        # Linux's card when either reading says so: an empty cage's reading
        # carries no bytes, and so no source, to tell it by.
        base = {"key": "transceiver:" + port,
                "source": "host" if c.get("host") or p.get("host")
                else "dataplane",
                "kind": "transceiver", "subject": port,
                "what": f"the module in {port}"}
        if p["present"] != c["present"]:
            if c["present"]:
                out.append(dict(base, severity="notice", reason="inserted",
                                state="in", text=f"module inserted in {port}"
                                + (f": {c['module']}" if c["module"] else "")))
            else:
                out.append(dict(base, severity="warning", reason="removed",
                                state="out",
                                text=f"module removed from {port}"
                                + (f" (it was {p['module']})"
                                   if p["module"] else "")))
            continue
        if not c["present"]:
            continue
        if p["module"] != c["module"] and p["module"] and c["module"]:
            out.append(dict(base, severity="notice", reason="replaced",
                            state="in", text=f"module in {port} replaced: "
                            f"{c['module']}, was {p['module']}"))
            continue
        for q, name, unit in (("rx", "rx power", "rx_dbm"),
                              ("temp", "temperature", "temp_c")):
            a, b = p[q], c[q]
            if a == b or not a or not b:
                continue
            if b == "normal":
                text, sev = f"{port}: {name} back within its thresholds " \
                            f"({c[unit]})", "notice"
            else:
                text, sev = level_text(port, c, q)
            out.append(dict(base, key=f"transceiver:{port}:{q}",
                            severity=sev, reason=f"{name} {b}", text=text,
                            what=f"the {name} of {port}", state=b))
    return out


#: A module's two levels, as a sentence names each and where its value is.
_LEVELS = {"rx": ("rx power", "rx_dbm"), "temp": ("temperature", "temp_c")}


def level_text(port, c, q):
    """
    (text, severity) for a module whose `q` ("rx" or "temp") stands past a
    threshold in the module_reading() row `c`: "<port>: rx power -19.00
    dBm, below its low warning (-18.01 dBm)"; an alarm is an error. Said by
    a change and by a standing condition (#588 phase 3) in the same words.
    """
    name, unit = _LEVELS[q]
    side = "above" if c[q].startswith("high") else "below"
    return (f"{port}: {name} {c[unit]}, {side} "
            f"{c[q + '_bound'] or 'a threshold'}",
            "error" if c[q].endswith("alarm") else "warning")


# --------------------------------------------------------------------- BGP

def bgp_reading(data):
    """
    FRR's `show bgp vrf all neighbors json` as {"<vrf> <peer>": {"state",
    "up", "down", "as", "reason"}}: the session's state, how many times it
    was established and dropped (FRR's own counters, so a session that went
    down and back between two readings is seen), and why it last reset.
    """
    out = {}
    for vrf, peers in (data or {}).items():
        if not isinstance(peers, dict):
            continue
        for peer, p in peers.items():
            if not isinstance(p, dict) or "bgpState" not in p:
                continue
            out[f"{vrf} {peer}"] = {
                "state": str(p.get("bgpState", "")),
                "up": _n(p.get("connectionsEstablished")),
                "down": _n(p.get("connectionsDropped")),
                "as": _n(p.get("remoteAs")),
                "reason": bgp_reason(p),
                # Shut by the box's own configuration (`neighbor X
                # shutdown`): not Established on purpose, so not a standing
                # condition (#588 phase 3).
                "shut": bool(p.get("adminShutDown"))}
    return out


def _n(v):
    """`v` as a count, 0 for anything that is not one."""
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 \
        else 0


def bgp_reason(p):
    """
    Why FRR last reset a session, in its own words: lastResetDueTo, the
    NOTIFICATION's reason when there was one, and the shutdown message a
    peer sent with it ("BGP Notification received: Cease/Administrative
    Shutdown ('maintenance')", read from FRR 10.7.1 in the lab image).
    """
    why = str(p.get("lastResetDueTo") or "")
    note = str(p.get("lastNotificationReason") or "")
    if note:
        why = f"{why}: {note}" if why else note
    msg = str(p.get("lastShutdownDescription") or "")
    if msg:
        why += f" ('{msg}')"
    return events._clean(why, events.REASON_MAX)


def _bgp_what(key, s):
    """"BGP session with <peer> (AS n[, vrf v])" for a sentence."""
    vrf, peer = key.split(" ", 1)
    return f"BGP session with {peer} (AS {s['as']}" + \
        ("" if vrf == "default" else f", vrf {vrf}") + ")"


def bgp_changes(prev, cur, restarted=False):
    """
    What changed between two bgp_reading()s: a session that became
    Established, one that left it with FRR's reason, and one that went down
    and came back between two readings - its dropped count moved while its
    state did not. `restarted` is a reading across a start of the routing
    daemons, whose counters began again at zero: states only.
    """
    out = []
    for key in sorted(set(prev) & set(cur)):
        p, c = prev[key], cur[key]
        what = _bgp_what(key, c)
        base = {"key": "bgp:" + key, "source": "routing", "kind": "bgp",
                "subject": key.split(" ", 1)[1], "what": what,
                "state": c["state"]}
        was, now = p["state"] == "Established", c["state"] == "Established"
        if was and not now:
            out.append(dict(base, severity="warning", reason=c["reason"],
                            text=f"{what} left Established"
                            + (f": {c['reason']}" if c["reason"] else "")))
        elif now and not was:
            out.append(dict(base, severity="notice", reason="",
                            text=f"{what} is Established"))
        elif now and not restarted and c["down"] > p["down"]:
            n = c["down"] - p["down"]
            out.append(dict(base, severity="warning", reason=c["reason"],
                            text=f"{what} went down and came back between "
                                 f"two readings ({n} time(s))"
                            + (f": {c['reason']}" if c["reason"] else "")))
    return out


# --------------------------------------------------------------------- BFD

def bfd_reading(fields, ids=None):
    """
    opshow.bfd_fields() as {key: {"up", "down", "diag", "peer", "where",
    "index"}}: whether the session is up, how many times bfdd counted it
    going down, and its diagnostic. The key is opshow.bfd_key()'s, not
    bfdd's id, which bfdd draws afresh when it restarts. `ids` is bfdd's
    id of each session, in the order of fields["sessions"]: the index the
    agent serves bfdSessTable by, which a BFD notification names (#559).
    """
    import opshow
    out = {}
    if not fields.get("answered"):
        return None
    ids = list(ids or [])
    for n, s in enumerate(fields.get("sessions") or []):
        where = s["interface"] or ""
        if s["vrf"] not in ("", "default"):
            where = (where + " " if where else "") + f"vrf {s['vrf']}"
        out["|".join(str(x) for x in opshow.bfd_key(s))] = {
            "up": s["state"] == "STATE_UP",
            "down": int(s.get("session_down_count") or 0),
            "diag": s.get("diagnostic") or "",
            "peer": s["peer"], "where": where,
            # Shut by configuration: down on purpose (#588 phase 3).
            "admin": s["state"] == "STATE_ADMIN_DOWN",
            "index": ids[n] if n < len(ids) else None}
    return out


def bfd_changes(prev, cur, restarted=False):
    """
    What changed between two bfd_reading()s: a session up, down with bfdd's
    diagnostic (#559), or down and back between two readings - its down
    count moved, which at a 300 ms detect time and a 10 s reading is the
    flap a state comparison alone would never see (opshow.bfd_changes).
    """
    out = []
    for key in sorted(set(prev) & set(cur)):
        p, c = prev[key], cur[key]
        what = f"BFD session with {c['peer']}" + \
            (f" (on {c['where']})" if c["where"] else "")
        diag = "" if c["diag"] in ("", "ok") else c["diag"]
        base = {"key": "bfd:" + key, "source": "routing", "kind": "bfd",
                "subject": c["peer"], "what": what,
                "state": "up" if c["up"] else "down"}
        if p["up"] and not c["up"]:
            out.append(dict(base, severity="warning", reason=diag,
                            text=f"{what} down" + (f": {diag}" if diag
                                                   else "")))
        elif c["up"] and not p["up"]:
            out.append(dict(base, severity="notice", reason="",
                            text=f"{what} up"))
        elif c["up"] and not restarted and c["down"] > p["down"]:
            n = c["down"] - p["down"]
            out.append(dict(base, severity="warning", reason=diag,
                            text=f"{what} went down and came back between "
                                 f"two readings ({n} time(s))"
                            + (f": {diag}" if diag else "")))
    return out


# ------------------------------------------------------------------- pools

def pool_reading(pools):
    """
    cgnatshow.parse_blocks()'s pools as {pool: {"refusals", "in_use", "blocks",
    "reserve", "exhausted"}}. `exhausted` is a verdict over readings, not a
    number: tick() carries it over (pool_reading_carry) and pool_changes()
    moves it.
    """
    out = {}
    for p in (pools or {}).get("pools") or []:
        t = p.get("total")
        if not t:
            continue
        out[p["pool"]] = {"refusals": t["refusals"], "in_use": t["in_use"],
                          "blocks": t["blocks"], "reserve": p["reserve"],
                          "exhausted": False}
    return out


def pool_changes(prev, cur, restarted=False):
    """
    What changed between two pool_reading()s, and the verdicts it moves:
    a pool whose refusals grew has run out of blocks - a subscriber asked
    for one and none was free; one that ran out and has refused nobody since,
    with a block free again, is said to have them again. A refusal count
    that went DOWN is a dataplane that started again (counters from zero):
    a baseline. Sets cur's `exhausted`.
    """
    out = []
    for name in sorted(set(prev) & set(cur)):
        p, c = prev[name], cur[name]
        if restarted or c["refusals"] < p["refusals"]:
            c["exhausted"] = False
            continue
        grew = c["refusals"] - p["refusals"]
        what = f"CGNAT pool {name}"
        base = {"key": "pool:" + name, "source": "dataplane", "kind": "pool",
                "subject": name, "what": what}
        use = f"{c['in_use']} of {c['blocks']} blocks in use"
        if grew and not p["exhausted"]:
            c["exhausted"] = True
            ran = "its reserve ran out" if c["reserve"] else \
                "ran out of blocks"
            out.append(dict(base, severity="warning", state="exhausted",
                            reason=f"{grew} refused",
                            text=f"{what} {ran}: {grew} request(s) for a "
                                 f"block refused since the last reading, "
                                 f"{use}"))
        elif not grew and p["exhausted"] and c["in_use"] < c["blocks"]:
            c["exhausted"] = False
            out.append(dict(base, severity="notice", state="has free blocks",
                            reason="",
                            text=f"{what} has free blocks again: {use}"))
    return out


# --------------------------------------------------------------- collector

def collector_reading(rows):
    """
    The log export's streams from logstate.LogWatch's rows (the verdicts
    the API serves, #555): {stream: {"configured", "collector", "ok",
    "why"}}. A TCP stream's collector is LOST when its connection is not
    "connected" - the box sees that directly. A UDP stream has no
    connection: what the box can see is its sends failing, so it is lost
    when LogWatch says it is stalled, and found when it delivers again.
    """
    out = {}
    for r in rows or []:
        if r["kind"] == "ipfix-udp":
            ok = r.get("delivering", True)
            why = r.get("stalled_reason", "")
        else:
            ok = r.get("connection") == "connected"
            why = r.get("connection", "")
        out[r["kind"]] = {"configured": bool(r["configured"]),
                          "collector": r.get("collector", ""), "ok": ok,
                          "why": why}
    return out


#: The streams as a sentence names them.
_STREAM = {"syslog": "syslog", "ipfix-tcp": "IPFIX over TCP",
           "ipfix-udp": "IPFIX over UDP"}


def collector_changes(prev, cur):
    """What changed between two collector_reading()s: a configured stream
    that lost its collector, or reached it again. A stream configured,
    unconfigured or pointed elsewhere is a commit's: a baseline."""
    out = []
    for k in sorted(set(prev) & set(cur)):
        p, c = prev[k], cur[k]
        if not (p["configured"] and c["configured"]) or \
                p["collector"] != c["collector"] or p["ok"] == c["ok"]:
            continue
        what = f"the CGNAT log export ({_STREAM.get(k, k)})"
        base = {"key": "collector:" + k, "source": "dataplane",
                "kind": "collector", "subject": k, "what": what}
        if c["ok"]:
            out.append(dict(base, severity="notice", reason="", state="up",
                            text=f"{what} reached its collector "
                                 f"{c['collector']} again"))
        else:
            out.append(dict(base, severity="warning", reason=c["why"],
                            state="lost",
                            text=f"{what} lost its collector {c['collector']}"
                            + (f": {c['why']}" if c["why"] else "")))
    return out


# ------------------------------------------------- the PPPoE control plane

#: The prisma-bng container's record of its runs (#597), written by its
#: entrypoint (appliance/prisma-bng-entrypoint, run_note) on the host's /run.
BNG_RUNS = os.environ.get("PRISMA_BNG_RUNS", "/run/prisma-routing/bng/runs")
#: How the container is named in a sentence.
BNG_WHAT = events._NAMES["bng"]
#: accel-ppp's own uptime, read twice, differs by this much at most when
#: it has not restarted: it prints whole seconds, and the reading takes some.
BNG_UPTIME_SLACK = 5


#: A count as accel-ppp and the entrypoint write one: ASCII digits only,
#: the rule of system.is_number (#412).
_COUNT = re.compile(r"[0-9]+")


def parse_bng_runs(text):
    """
    The container's record of its runs, `<n> <epoch> start` and `<n> <epoch>
    exit <code> <reason...>` a line, as [{"n", "t", "what", "code",
    "reason"}] in the file's order. A line that is not one - a torn tail -
    is passed over.
    """
    out = []
    for line in (text or "").splitlines():
        w = line.split()
        if len(w) < 3 or not _COUNT.fullmatch(w[0]) or \
                not _COUNT.fullmatch(w[1]):
            continue
        if w[2] == "start" and len(w) == 3:
            out.append({"n": int(w[0]), "t": int(w[1]), "what": "start",
                        "code": 0, "reason": ""})
        elif w[2] == "exit" and len(w) >= 4 and _COUNT.fullmatch(w[3]):
            out.append({"n": int(w[0]), "t": int(w[1]), "what": "exit",
                        "code": int(w[3]),
                        "reason": events._clean(" ".join(w[4:]),
                                                events.REASON_MAX)})
    return out


def bng_reading(text):
    """
    The container's runs as the watcher compares them: {"runs": [...]},
    parse_bng_runs()' list. A box where PPPoE never started since its boot
    has no file, and that is a reading - nothing ran - not a missing one.

    A RECORD, NOT A STATE, like the login records: docker brings the
    container back within a second, so "running" read every 10 s is true
    both times across a crash, and docker resets State.ExitCode to 0 at the
    start that follows. Measured with docker 27.1.1 on 2026-10-13, a
    container under `restart: unless-stopped` exiting 3: "restarting
    exit=3" for under 0.1 s, running again 35-86 ms after it exited, and
    "exit=0" from then on. What a reading compares is how far the record has
    gone.
    """
    return {"runs": parse_bng_runs(text)}


def _bng_new(prev, cur):
    """
    The runs of `cur` not in `prev`: past the last one `prev` saw. A record
    whose numbering went back is a new boot's (/run is a tmpfs): all of it
    is new.
    """
    runs = cur.get("runs") or []
    seen = (prev or {}).get("runs") or []
    if not seen:
        return list(runs)
    last = seen[-1]["n"]
    if runs and runs[-1]["n"] < last:
        return list(runs)
    return [r for r in runs if r["n"] > last]


def bng_start_text(cause, detail, before):
    """
    The sentence a start of the container says, and its severity: `cause`
    and `detail` as events.cause_of() found them, `before` the run record
    that came before this start (None for the first). A start nothing on
    the box asked for is said by what preceded it: an exit the entrypoint
    recorded, which the restart policy answered; or a run that recorded no
    exit at all, which only a kill from outside leaves.
    """
    again = " again" if before is not None else ""
    if cause == "boot":
        return f"{BNG_WHAT} started with the boot", "notice"
    if cause != "unexpected":
        return (f"{BNG_WHAT} started{again}: {detail or cause}", "notice")
    if before is not None and before["what"] == "exit" and before["code"]:
        return (f"{BNG_WHAT} started again, by its restart policy, after it "
                f"exited: {before['reason']}", "warning")
    if before is not None and before["what"] == "start":
        return (f"{BNG_WHAT} started again; its last run recorded no exit: "
                f"the container was killed from outside (docker kill, the "
                f"OOM killer, a stop that ran out of time)", "warning")
    return f"{BNG_WHAT} started{again}", "notice"


def bng_changes(prev, cur, record=None, boot=None):
    """
    What the container's record says happened since `prev`: each exit,
    with its code and the entrypoint's reason, and each start, with its
    cause - the boot, an "asked" event in `record` (the box's record of
    events: a commit's, a format upgrade's), else what came before it.
    Change dicts as tick() takes them, each with its own time.
    """
    out = []
    runs = cur.get("runs") or []
    seen = (prev or {}).get("runs") or []
    # What came before the first new run: the last one seen - unless the
    # record started again with a boot, and nothing seen came before it.
    if seen and runs and runs[-1]["n"] < seen[-1]["n"]:
        seen = []
    before = seen[-1] if seen else None
    starts = [x["t"] for x in seen if x["what"] == "start"]
    last_start = starts[-1] if starts else None
    base = {"source": "host", "subject": "prisma-bng", "what": BNG_WHAT,
            "component": "bng"}
    for r in _bng_new(prev, cur):
        if r["what"] == "exit":
            if r["code"] == 0:
                text = f"{BNG_WHAT} stopped: {r['reason']}"
                sev = "notice"
            else:
                text = f"{BNG_WHAT} exited with code {r['code']}: " \
                       f"{r['reason']}"
                sev = "warning"
            out.append(dict(base, key="bng:exit", kind="exit",
                            severity=sev, text=text, reason=r["reason"],
                            state="stopped", t=r["t"]))
        else:
            cause, detail = events.cause_of(record or [], "bng", r["t"],
                                            boot, after=last_start)
            text, sev = bng_start_text(cause, detail, before)
            out.append(dict(base, key="bng:start", kind="start",
                            severity=sev, text=text, reason=detail,
                            state="running", t=r["t"], cause=cause))
            last_start = r["t"]
        before = r
    return out


def bng_running(reading):
    """Whether the container's record says it is running: its last line
    is a start."""
    runs = (reading or {}).get("runs") or []
    return bool(runs) and runs[-1]["what"] == "start"


_UPTIME = re.compile(r"^uptime:\s*(\d+)\.(\d+):(\d+):(\d+)$")
_RADIUS = re.compile(r"^radius\(\d+,\s*([0-9a-fA-F.:]+)\):$")


def radius_reading(stat, now):
    """
    accel-ppp's `show stat` (`prisma-bng summary`) as {"started", "servers":
    {address: {"state", "fails"}}}: when accel-pppd started (now less its
    uptime), and each RADIUS server's state - active, or failed for its
    dead-time - and accel-ppp's count of the times it marked it failed, so
    a server that failed and came back between two readings is seen. A
    server listed twice (one a reload is retiring) is the later one.
    """
    out = {"started": None, "servers": {}}
    cur = None
    for raw in (stat or "").splitlines():
        line = raw.strip()
        m = _UPTIME.match(line)
        if m:
            d, h, mi, se = (int(x) for x in m.groups())
            out["started"] = round(now - (((d * 24 + h) * 60 + mi) * 60 + se))
            continue
        m = _RADIUS.match(line)
        if m:
            cur = {"state": "", "fails": 0}
            out["servers"][m.group(1)] = cur
            continue
        if not raw.startswith(" "):
            cur = None
            continue
        if cur is not None and line.startswith("state:"):
            cur["state"] = line.split(":", 1)[1].strip()
        elif cur is not None and line.startswith("fail count:"):
            v = line.split(":", 1)[1].strip()
            cur["fails"] = int(v) if _COUNT.fullmatch(v) else 0
    return out


def radius_changes(prev, cur):
    """
    What changed between two radius_reading()s: a server accel-ppp marked
    failed, one it uses again, and one that failed and came back between
    two readings (its fail count moved, its state did not). Across a start
    of accel-pppd - its uptime went back - every server is active again
    and counted from zero: a baseline, not news.
    """
    out = []
    ps, cs = prev.get("started"), cur.get("started")
    if ps is None or cs is None or abs(cs - ps) > BNG_UPTIME_SLACK:
        return out
    for addr in sorted(set(prev["servers"]) & set(cur["servers"])):
        p, c = prev["servers"][addr], cur["servers"][addr]
        what = f"RADIUS server {addr}"
        base = {"key": "radius:" + addr, "source": "host", "kind": "radius",
                "subject": addr, "what": what, "state": c["state"]}
        if p["state"] != "failed" and c["state"] == "failed":
            out.append(dict(base, severity="warning", reason="no answer",
                            text=f"{what} failed: it did not answer, and "
                                 f"accel-ppp uses the others for its "
                                 f"dead-time"))
        elif p["state"] == "failed" and c["state"] == "active":
            out.append(dict(base, severity="notice", reason="",
                            text=f"{what} is active again: its dead-time "
                                 f"is over and accel-ppp sends to it"))
        elif c["fails"] > p["fails"]:
            n = c["fails"] - p["fails"]
            out.append(dict(base, severity="warning", reason="no answer",
                            text=f"{what} failed and was active again "
                                 f"between two readings ({n} time(s))"))
    return out


def bng_standing(cur):
    """Nothing: a run's exit is a change, said when it happens."""
    return []


def radius_standing(cur):
    """The servers of a radius_reading() failed when the watcher starts."""
    return [{"key": "radius:" + a, "source": "host", "kind": "radius",
             "subject": a, "severity": "warning", "reason": "no answer",
             "text": f"RADIUS server {a} is failed: it did not answer, and "
                     f"accel-ppp uses the others for its dead-time"}
            for a in sorted(cur.get("servers") or {})
            if cur["servers"][a]["state"] == "failed"]


# ---------------------------------------------------------------- standing

def link_standing(cur):
    """
    The ports of a link_reading() administratively up whose link is down,
    as standing change dicts (#588 phase 3). A sub-interface's link is its
    parent's, so the port says it with the sub-interfaces it carries; one
    shut down by configuration is down on purpose and is not said.
    """
    out = []
    for name in sorted(cur):
        c = cur[name]
        if c["parent"] or c["admin"] != "up" or c["link"] != "down":
            continue
        subs = sum(1 for s in cur.values()
                   if s["parent"] == name and s["admin"] == "up")
        text = f"{_link_what(name, c)}: link down, administratively up: " \
               f"the wire or the far end"
        if subs:
            text += f"; its {subs} sub-interface(s) with it"
        out.append({"key": "link:" + name,
                    "source": "host" if c.get("host") else "dataplane",
                    "kind": "link", "subject": name, "severity": "warning",
                    "text": text, "reason": "link"})
    return out


def module_standing(cur):
    """The modules of a module_reading() whose RX power or temperature
    stands past a warning or an alarm threshold (#588 phase 3)."""
    out = []
    for port in sorted(cur):
        c = cur[port]
        if not c["present"]:
            continue
        for q in ("rx", "temp"):
            if c[q] in ("", "normal"):
                continue
            text, sev = level_text(port, c, q)
            out.append({"key": f"transceiver:{port}:{q}",
                        "source": "host" if c.get("host") else "dataplane",
                        "kind": "transceiver",
                        "subject": port, "severity": sev, "text": text,
                        "reason": f"{_LEVELS[q][0]} {c[q]}"})
    return out


def bgp_standing(cur):
    """
    The sessions of a bgp_reading() that are not Established, with the
    state FRR has them in and its last reset (#588 phase 3). One shut by
    the box's configuration is not: it is down because it was told to be.
    """
    out = []
    for key in sorted(cur):
        c = cur[key]
        if c["state"] == "Established" or c.get("shut"):
            continue
        text = f"{_bgp_what(key, c)} is not Established: " \
               f"{c['state'] or 'no state'}"
        if c["reason"]:
            text += f"; FRR's last reset: {c['reason']}"
        out.append({"key": "bgp:" + key, "source": "routing", "kind": "bgp",
                    "subject": key.split(" ", 1)[1], "severity": "warning",
                    "text": text, "reason": c["reason"]})
    return out


def bfd_standing(cur):
    """The sessions of a bfd_reading() that are down, with bfdd's
    diagnostic; not one shut by configuration (#588 phase 3)."""
    out = []
    for key in sorted(cur or {}):
        c = cur[key]
        if c["up"] or c.get("admin"):
            continue
        what = f"BFD session with {c['peer']}" + \
            (f" (on {c['where']})" if c["where"] else "")
        diag = "" if c["diag"] in ("", "ok") else c["diag"]
        out.append({"key": "bfd:" + key, "source": "routing", "kind": "bfd",
                    "subject": c["peer"], "severity": "warning",
                    "text": f"{what} is down" + (f": {diag}" if diag else ""),
                    "reason": diag})
    return out


def pool_standing(cur):
    """
    The pools of a pool_reading() out of blocks: none free, and requests
    refused since the dataplane started (#588 phase 3). A pool whose every
    block is held and that has refused nobody is full, not refusing - a
    deterministic pool is that by design. Sets the pool's `exhausted`, so
    pool_changes() says when it has blocks again.
    """
    out = []
    for name in sorted(cur):
        c = cur[name]
        if c["in_use"] < c["blocks"] or not c["refusals"]:
            continue
        c["exhausted"] = True
        ran = "its reserve is out" if c["reserve"] else "is out of blocks"
        out.append({"key": "pool:" + name, "source": "dataplane",
                    "kind": "pool", "subject": name, "severity": "warning",
                    "reason": f"{c['refusals']} refused",
                    "text": f"CGNAT pool {name} {ran}: {c['refusals']} "
                            f"request(s) for a block refused since the "
                            f"dataplane started, {c['in_use']} of "
                            f"{c['blocks']} blocks in use"})
    return out


def collector_standing(cur):
    """The configured streams of a collector_reading() not reaching their
    collector (#588 phase 3)."""
    out = []
    for k in sorted(cur):
        c = cur[k]
        if not c["configured"] or c["ok"]:
            continue
        what = f"the CGNAT log export ({_STREAM.get(k, k)})"
        out.append({"key": "collector:" + k, "source": "dataplane",
                    "kind": "collector", "subject": k, "severity": "warning",
                    "reason": c["why"],
                    "text": f"{what} is not reaching its collector "
                            f"{c['collector']}"
                            + (f": {c['why']}" if c["why"] else "")})
    return out


#: Each kind's standing conditions. Logins are records, not a state.
STANDING_OF = {"link": link_standing, "transceiver": module_standing,
               "bgp": bgp_standing, "bfd": bfd_standing,
               "pool": pool_standing, "collector": collector_standing,
               "bng": bng_standing, "radius": radius_standing}


def standing_due(stood, kind, boot, start, now):
    """
    Whether `kind`'s reading at `now` is read for standing conditions: the
    state does not carry them for this boot (`stood`, {kind: boot}), and
    the component it is read from has run STANDING_SETTLE, when its start
    `start` is known. A state with no `stood` - none at all, or one written
    by 0.152, whose watcher never said them - says them: what stands was
    never said (#588 phase 3, the X520 on 10.40.200.166).
    """
    if start is not None and 0 <= now - start < STANDING_SETTLE:
        return False
    said = stood.get(kind)
    return said is None or abs(said - round(boot or 0)) > events.START_SLACK


def standing_changes(kind, cur, said=()):
    """
    `kind`'s standing conditions in the reading `cur`, as change dicts whose
    text starts STANDING; those whose key is in `said` - a change this very
    tick already said - left out, and past STANDING_MAX one more saying how
    many were not each recorded.
    """
    found = [c for c in STANDING_OF[kind](cur) if c["key"] not in said]
    out = [dict(c, text=STANDING + c["text"], standing=True)
           for c in found[:STANDING_MAX]]
    if len(found) > STANDING_MAX:
        rest = len(found) - STANDING_MAX
        out.append({"key": f"{kind}:standing", "source": found[0]["source"],
                    "kind": kind, "subject": "", "severity": "warning",
                    "standing": True,
                    "reason": f"{rest} more",
                    "text": f"{STANDING}and {rest} more {kind} condition(s) "
                            f"like these, not each recorded"})
    return out


# ------------------------------------------------------------------ logins

#: struct utmp on Linux x86_64 and aarch64 (bits/utmp.h): ut_type, ut_pid,
#: ut_line[32], ut_id[4], ut_user[32], ut_host[256], ut_exit (two shorts),
#: ut_session, ut_tv (two int32), ut_addr_v6[4], 20 bytes unused. 384 bytes,
#: unchanged in glibc since 2.x.
UTMP = struct.Struct("<hxxi32s4s32s256shhiii16s20s")
USER_PROCESS, DEAD_PROCESS = 7, 8


def _text(b):
    """A utmp char field as text: up to its first NUL, control characters
    out (a user name in btmp is whatever was typed at the login)."""
    s = b.split(b"\0", 1)[0].decode("utf-8", "replace")
    return "".join(c for c in s if ord(c) >= 32)


def parse_utmp(data):
    """
    utmp records in `data` (a whole number of them; a trailing partial one
    is left for the next read): [{"type", "pid", "line", "user", "host",
    "t"}].
    """
    out = []
    for i in range(0, len(data) - UTMP.size + 1, UTMP.size):
        (typ, pid, line, _id, user, host, _e1, _e2, _sess, sec, usec,
         _addr, _pad) = UTMP.unpack_from(data, i)
        out.append({"type": typ, "pid": pid, "line": _text(line),
                    "user": _text(user), "host": _text(host),
                    "t": sec + usec / 1e6})
    return out


def read_utmp(path, pos):
    """
    The records appended to the utmp file at `path` since `pos` ({"ino",
    "off"}, or None for a first reading): (records, new_pos).

    WHY wtmp AND btmp. sshd and login(1) write a fixed-size record per
    login to wtmp and per failed password to btmp (OpenSSH's
    record_failed_login, which Debian builds with); the layout is glibc's
    ABI. The other ways were each more fragile: sshd's log lines are prose
    that changes between releases and needs the journal; a pam_exec line
    in /etc/pam.d/sshd sees a failure only when it is placed after the
    module that fails, and an upgrade of the package rewrites the file.

    A first reading starts at the end - the logins before anything watched
    are history, not news. A file that is not the one read before (a new
    inode: logrotate's monthly rotation) or that is shorter is read from
    its start. At most UTMP_PER_TICK records a call.
    """
    try:
        st = os.stat(path)
    except OSError:
        return [], pos
    if pos is None:
        return [], {"ino": st.st_ino, "off": st.st_size - st.st_size
                    % UTMP.size}
    off = pos.get("off", 0)
    if pos.get("ino") != st.st_ino or st.st_size < off:
        off = 0
    if st.st_size - off < UTMP.size:
        return [], {"ino": st.st_ino, "off": off}
    want = min(st.st_size - off, UTMP_PER_TICK * UTMP.size)
    want -= want % UTMP.size
    try:
        with open(path, "rb") as fh:
            fh.seek(off)
            data = fh.read(want)
    except OSError:
        return [], pos
    got = len(data) - len(data) % UTMP.size
    return parse_utmp(data[:got]), {"ino": st.st_ino, "off": off + got}


def _origin(r):
    """Where a login came from, as an event says it."""
    if r["host"]:
        return f"ssh {r['host']}" if not r["line"].startswith("tty") \
            else r["host"]
    if r["line"].startswith(("tty", "ttyS", "console")):
        return f"the console ({r['line']})"
    return r["line"]


def login_changes(wtmp, btmp, open_logins, shell_of=None):
    """
    Logins, logouts and failed logins from new wtmp and btmp records, as
    change dicts with their own times, and the sessions still open
    (`open_logins`, {line: {...}}, updated here).

    A login says what it landed in when the account's shell says it - the
    VTY (prismash-login) or a shell - because a VTY login IS an SSH or a
    console login: the VTY has no password of its own. A logout is matched
    to its login by the terminal line, as last(1) does; one with no login
    known is said nothing about. A failed login names the account typed,
    which may not exist, and is keyed by where it came from, so a brute
    force from one address is held as one subject (flap_admit()).
    """
    out = []
    shell_of = shell_of or (lambda user: "")
    for r in wtmp:
        if r["type"] == USER_PROCESS and r["user"]:
            sh = shell_of(r["user"])
            into = " to the VTY" if sh.endswith("prismash-login") else (
                " to a shell" if sh else "")
            origin = _origin(r)
            out.append({"key": "login:" + r["line"], "source": "host",
                        "kind": "login", "subject": r["user"],
                        "severity": "notice", "reason": "",
                        "text": f"{r['user']} logged in{into}",
                        "who": r["user"], "from": origin, "t": r["t"],
                        "what": f"logins on {r['line']}", "state": "in"})
            open_logins[r["line"]] = {"user": r["user"], "from": origin,
                                      "t": r["t"]}
        elif r["type"] == DEAD_PROCESS and r["line"] in open_logins:
            s = open_logins.pop(r["line"])
            mins = max(0, int((r["t"] - s["t"]) // 60))
            out.append({"key": "logout:" + r["line"], "source": "host",
                        "kind": "login", "subject": s["user"],
                        "severity": "info", "reason": "",
                        "text": f"{s['user']} logged out after {mins} min",
                        "who": s["user"], "from": s["from"], "t": r["t"],
                        "what": f"logouts on {r['line']}", "state": "out"})
    while len(open_logins) > OPEN_LOGINS_MAX:
        del open_logins[min(open_logins, key=lambda k:
                            open_logins[k].get("t", 0))]
    for r in btmp:
        origin = _origin(r)
        user = events._clean(r["user"], events.SUBJECT_MAX) or "(no name)"
        out.append({"key": "login-failed:" + (origin or r["line"]),
                    "source": "host", "kind": "login", "subject": user,
                    "severity": "warning", "reason": "authentication failed",
                    "text": f"failed login as {user}", "who": "",
                    "from": origin, "t": r["t"],
                    "what": f"failed logins from {origin or r['line']}",
                    "state": "failing"})
    return out


# -------------------------------------------------------------------- tick

#: Each kind's comparison, and whether it takes `restarted`.
COMPARE = {"link": (link_changes, True), "transceiver": (module_changes,
                                                          False),
           "bgp": (bgp_changes, True), "bfd": (bfd_changes, True),
           "pool": (pool_changes, True), "collector": (collector_changes,
                                                       False),
           "radius": (radius_changes, False)}


def tick(state, readings, starts, now, logins=None, record=None):
    """
    One tick of the watcher, pure: (events, new_state).

    `readings` is {kind: reading} for the kinds read this tick - a reading
    is None when its source did not answer, and is then skipped, keeping
    the one before. `starts` is {"dataplane", "routing"}: when each last
    started (Shell.uptime_facts()), None when not known. `logins` is
    (wtmp_records, btmp_records, positions) when the login records were
    read, positions being read_utmp()'s. `starts["boot"]`, when given, is
    the boot what stands is said once for (#588 phase 3); standing events
    are not passed through flap_admit(): once a boot is their bound.
    `record` is the box's record of events, for a start of the PPPoE
    control plane to find its cause in (bng_changes); None when no new run
    was read.
    """
    state = json.loads(json.dumps(state or {}))
    seen = state.setdefault("seen", {})
    started = state.setdefault("started", {})
    last = state.setdefault("last", {})
    flaps = state.setdefault("flaps", {})
    stood = state.setdefault("stood", {})
    changes, standing = [], []
    for kind, cur in readings.items():
        last[kind] = now
        if cur is None:
            continue
        comp = "dataplane" if kind in DATAPLANE_KINDS else \
            "bng" if kind in BNG_KINDS else "routing"
        start = starts.get(comp)
        if start is not None and 0 <= now - start < SETTLE:
            continue
        prev = seen.get(kind)
        if kind == "pool":
            cur = pool_reading_carry(cur, prev)
        moved = []
        if prev is not None:
            restarted = start is not None and started.get(kind) is not None \
                and abs(start - started[kind]) > events.START_SLACK
            if kind == "bng":
                moved = bng_changes(prev, cur, record, starts.get("boot"))
            else:
                fn, takes = COMPARE[kind]
                moved = fn(prev, cur, restarted) if takes else fn(prev, cur)
            changes += moved
        # After the comparison, so a condition this tick's change already
        # says - a link that went down while the watcher was stopped - is
        # said once, as the change.
        if standing_due(stood, kind, starts.get("boot"), start, now):
            standing += standing_changes(kind, cur,
                                         {c["key"] for c in moved})
            stood[kind] = round(starts.get("boot") or 0)
        seen[kind] = cur
        if start is not None:
            started[kind] = start
    if logins is not None:
        wtmp, btmp, pos = logins
        opened = state.setdefault("open_logins", {})
        changes += login_changes(wtmp, btmp, opened, SHELL_OF)
        state["logins"] = pos
        last["login"] = now
    out = []
    for c in standing + changes:
        if not c.get("standing") and not flap_admit(flaps, c,
                                                    c.get("t", now)):
            continue
        out.append(events.make_event(
            c["source"], c["severity"], c["text"], who=c.get("who", ""),
            origin=c.get("from", ""), now=c.get("t", now), kind=c["kind"],
            subject=events._clean(c["subject"], events.SUBJECT_MAX),
            reason=events._clean(c["reason"], events.REASON_MAX),
            component=c.get("component"), cause=c.get("cause")))
    out += flap_summaries(flaps, now)
    return out, state


def pool_reading_carry(cur, prev):
    """A pool reading with each pool's `exhausted` verdict carried over
    from `prev` (pool_changes() moves it)."""
    out = {}
    for name, c in cur.items():
        c = dict(c)
        c["exhausted"] = bool(((prev or {}).get(name) or {}).get("exhausted"))
        out[name] = c
    return out


def _shell_of(user):
    """The account's login shell, from the host's passwd."""
    import pwd
    try:
        return pwd.getpwnam(user).pw_shell or ""
    except (KeyError, OSError):
        return ""


#: How login_changes() learns an account's shell; test/test_eventwatch.py
#: replaces it, so a test does not depend on the passwd of the machine it
#: runs on.
SHELL_OF = _shell_of
