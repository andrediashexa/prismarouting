#!/usr/bin/env python3
"""
src/prismash/events.py and its door (#588): the box's record of what happened
on it, which `show log` and ListEvents read.

Each behaviour the issue asks for, shown red by a mutation (docs/
logging-events.md lists them and what each did):

  * the record is BOUNDED: past EVENTS_MAX the file becomes events.log.1 and
    a new one starts, and the reader reads both, oldest first (mutation:
    append_event() without the rotation - the file grows past the cap);
  * `show log`'s filters: last, since (relative and ISO), severity (that
    level and above), source (mutation: severity_rank() inverted);
  * a restart's CAUSE is the newest "asked" before it, the boot when it is
    the boot's, "unexpected" when nothing asked (mutation: cause_of() never
    reading the "asked" events);
  * the same request is not recorded twice - the host's VTY running what
    the API asked (test_prismash_units.py, test_doors.py: the door's
    PRISMA_ASKED_RECORDED);
  * through the door, an account's event carries the account the KERNEL
    names, not the one it wrote, and the door's time; an account cannot
    write a start or a boot (mutation: answer_event() keeping the caller's
    `who`);
  * the noise stays out: a door call is never an event, and the events' own
    door calls are not written to the journal either (mutation: journaled()
    always True).
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import contextlib
import io

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src", "prismash")
sys.path.insert(0, SRC)
import events  # noqa: E402
import doors  # noqa: E402

fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


work = tempfile.mkdtemp(prefix="events-")
LOG = os.path.join(work, "events.log")
NOW = 1790836414.0

print("=== an event is one line, in the box's words ===")
ev = events.make_event("vty", "notice", "configuration\ncommitted:  first",
                       who="alice", origin="ssh 192.0.2.7", now=NOW,
                       kind="commit", cause="")
check(ev == {"t": NOW, "severity": "notice", "source": "vty",
             "text": "configuration committed: first", "who": "alice",
             "from": "ssh 192.0.2.7", "kind": "commit"},
      f"make_event: one line, the empty fields left out: {ev}")
check(events.event_error(ev) is None, "event_error: a commit is an event")
for bad, why in ((dict(ev, source="frr"), "source"),
                 (dict(ev, severity="debug"), "severity"),
                 (dict(ev, text="two\nlines"), "the text"),
                 (dict(ev, text=""), "the text"),
                 (dict(ev, text="x" * 301), "the text"),
                 (dict(ev, who="a\nb"), "who"),
                 (dict(ev, kind="reformat"), "kind"),
                 (dict(ev, component="mgmt"), "component"),
                 (dict(ev, cause="sunspots"), "cause"),
                 (dict(ev, affects="dataplane disk"), "affects"),
                 ("not a dict", "not an event")):
    check((events.event_error(bad) or "").startswith(why),
          f"event_error refuses: {events.event_error(bad)}")
start = events.make_event("dataplane", "notice", "the dataplane started",
                          now=NOW, kind="start", component="dataplane",
                          cause="boot")
check(events.event_error(start) is None
      and "not what the box did" in (events.event_error(
          dict(start, source="vty"), privileged=False) or "")
      and "an account writes events" in (events.event_error(
          start, privileged=False) or ""),
      "an account may not write a start: it would explain a crash away")

print("\n=== the record is bounded, and both files are read ===")
for i in range(40):
    check_ok = events.append_event(
        events.make_event("vty", "info", f"event {i:02d} " + "x" * 60,
                          now=NOW + i), LOG, cap=1000)
    if not check_ok:
        break
check(check_ok, "append_event: written")
sizes = [os.path.getsize(p) for p in (LOG, LOG + ".1") if os.path.exists(p)]
check(all(s <= 1000 for s in sizes) and os.path.exists(LOG + ".1"),
      f"append_event: rotated at the cap, no file past it: {sizes}")
got = events.read_events(LOG)
check(len(got) >= 1000 // 120 and got[-1]["text"].startswith("event 39")
      and [e["t"] for e in got] == sorted(e["t"] for e in got),
      f"read_events: the rotated file and the current one, oldest first: "
      f"{len(got)} events, newest '{got[-1]['text'][:8]}'")
with open(LOG, "a") as fh:
    fh.write('{"t": torn')
check(len(events.read_events(LOG)) == len(got),
      "read_events: a torn last line is passed over")
check(events.read_events(os.path.join(work, "none")) == [],
      "read_events: no record is no events")
check(not events.append_event(ev, os.path.join(work, "no", "dir", "x")),
      "append_event: a path it cannot write is False, not an exception")

print("\n=== show log's filters ===")
evs = [events.make_event("vty", "notice", "commit", now=NOW - 7200),
       events.make_event("host", "info", "boot", now=NOW - 3600),
       events.make_event("dataplane", "warning", "unexpected", now=NOW - 600,
                         kind="start", component="dataplane"),
       events.make_event("api", "error", "failed", now=NOW - 60)]
check([e["text"] for e in events.select_events(evs, last=2)]
      == ["unexpected", "failed"], "select_events: the last n, newest last")
check([e["text"] for e in events.select_events(evs, severity="warning")]
      == ["unexpected", "failed"],
      "select_events: a severity is that level and every one above it")
check(events.severity_rank("error") < events.severity_rank("info")
      < events.severity_rank("nonsense"), "severity_rank: most severe first")
check([e["text"] for e in events.select_events(evs, source="host")] == ["boot"]
      and [e["text"] for e in events.select_events(
          evs, kind="start", component="dataplane")] == ["unexpected"]
      and events.select_events(evs, last=0) == [],
      "select_events: by source, kind and component")
check([e["text"] for e in events.select_events(evs, since=NOW - 3600)]
      == ["boot", "unexpected", "failed"], "select_events: since, inclusive")
check(events.parse_since("30m", NOW) == NOW - 1800
      and events.parse_since("1h", NOW) == NOW - 3600
      and events.parse_since("2d", NOW) == NOW - 172800
      and events.parse_since("1w", NOW) == NOW - 604800
      and events.parse_since("45s", NOW) == NOW - 45,
      "parse_since: relative forms")
check(events.parse_since("2026-10-05T14:00:00Z") == 1791208800
      and events.parse_since("2026-10-05T11:00-03:00") == 1791208800,
      "parse_since: an ISO time in UTC, and with an offset")
_tz = os.environ.get("TZ")
os.environ["TZ"] = "America/Sao_Paulo"
time.tzset()
check(events.parse_since("2026-10-05") == 1791169200
      and events.parse_since("2026-10-05 14:00") == 1791219600,
      "parse_since: a date or time with no zone is the box's own zone")
os.environ.pop("TZ") if _tz is None else os.environ.update(TZ=_tz)
time.tzset()
try:
    events.parse_since("yesterday")
    check(False, "parse_since: a word that is no time is refused")
except ValueError as e:
    check("is not a time" in str(e), f"parse_since: {e}")
f, why = events.parse_show_args([], NOW)
check(f == {"last": events.SHOW_LAST} and why is None,
      "parse_show_args: nothing given is the last 50")
f, why = events.parse_show_args(["since", "1h", "severity", "warning",
                                 "source", "dataplane"], NOW)
check(f == {"since": NOW - 3600, "severity": "warning",
            "source": "dataplane"} and why is None,
      "parse_show_args: since with no last is every one since")
for words, said in ((["last"], "needs a value"), (["last", "x"], "a number"),
                    (["last", "\u00b2"], "a number"),
                    (["last", "0"], "a number"),
                    (["last", "1", "last", "2"], "twice"),
                    (["severity", "debug"], "severity is one of"),
                    (["source", "frr"], "source is one of"),
                    (["since", "soon"], "is not a time"),
                    (["match", "x"], "show log takes")):
    _f, _why = events.parse_show_args(words, NOW)
    check(_f is None and said in (_why or ""), f"parse_show_args refuses "
          f"{' '.join(words)!r}: {_why}")
utc = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t))
check(events.format_event(ev, utc) == "2026-10-01 06:33:34  notice   vty   "
      "     configuration committed: first (by\n    alice, from ssh 192.0.2.7)",
      f"format_event: time, severity, source, the sentence; folded at 79: "
      f"{events.format_event(ev, utc)!r}")
_long = dict(ev, text="x " * 140)
check(all(len(r) <= 79 for r in events.format_event(_long, utc).splitlines())
      and all(r.startswith("    ") for r in
              events.format_event(_long, utc).splitlines()[1:]),
      "format_event: every row inside the console, the continuation indented")
_short = dict(ev, text="booted")
check(events.format_event(dict(_short, who=""), utc).endswith("(from ssh 192.0.2.7)")
      and events.format_event(dict(_short, **{"from": ""}), utc).endswith("(by alice)")
      and events.format_event(dict(_short, who="", **{"from": ""}), utc)
      .endswith("booted"), "format_event: who and from only when known")

print("\n=== who and from where ===")
check(events.session_origin({"SSH_CONNECTION": "192.0.2.7 5000 10.0.0.1 22"})
      == "ssh 192.0.2.7"
      and events.session_origin({"PRISMA_SESSION_FROM": "2001:db8::7"})
      == "ssh 2001:db8::7"
      and events.session_origin({"PRISMA_EVENT_SOURCE": "api",
                                 "SSH_CONNECTION": "1 2 3 4"}) == "the API"
      and events.session_origin({}) == "",
      "session_origin: sshd's, the session door's, the API's, or nothing")
check(events.event_source({"PRISMA_EVENT_SOURCE": "api"}) == "api"
      and events.event_source({}) == "vty", "event_source: the API or the VTY")

print("\n=== a restart's cause ===")
BOOT = NOW - 86400
asked = events.make_event("vty", "notice", "system dataplane restart asked",
                          who="alice", origin="ssh 192.0.2.7", now=NOW - 100,
                          kind="asked", cause="operator",
                          affects="dataplane routing")
upg = events.make_event("api", "notice", "system upgrade to 0.151.14 asked",
                        who="web", origin="the API", now=NOW - 5000,
                        kind="asked", cause="upgrade",
                        affects="dataplane routing")
check(events.cause_of([upg, asked], "dataplane", NOW - 60, BOOT)
      == ("operator", "system dataplane restart asked by alice from "
                      "ssh 192.0.2.7"),
      "cause_of: the newest asked before the start")
check(events.cause_of([upg, asked], "dataplane", NOW - 4900, BOOT)[0]
      == "upgrade", "cause_of: an upgrade, before a later restart was asked")
check(events.cause_of([asked], "dataplane", NOW + 1200, BOOT)
      == ("unexpected", ""), "cause_of: an asked older than the window is not it")
check(events.cause_of([asked], "dataplane", NOW - 200, BOOT)[0] == "unexpected",
      "cause_of: an asked AFTER the start is not its cause")
check(events.cause_of([asked], "dataplane", BOOT + 40, BOOT, "system reboot by ana")
      == ("boot", "system reboot by ana"), "cause_of: a start with the boot is the boot's")
check(events.cause_of([asked], "dataplane", NOW - 60, BOOT, after=NOW - 105)[0]
      == "operator"
      and events.cause_of([asked], "dataplane", NOW - 60, BOOT, after=NOW - 95)[0]
      == "unexpected"
      and events.cause_of([asked], "dataplane", NOW + 10, BOOT, after=NOW - 60)[0]
      == "unexpected",
      "cause_of: one request is one start's cause - not a second start's after it")
check(events.cause_of([dict(asked, affects="routing")], "dataplane", NOW - 60,
                      BOOT)[0] == "unexpected",
      "cause_of: an asked that names another component is not it")
check(events.start_sentence("dataplane", "boot", "") ==
      "the dataplane started with the boot"
      and events.start_sentence("routing", "boot", "system reboot by ana")
      == "the routing daemons (FRR) started with the boot (system reboot by ana)"
      and "unexpected" in events.start_sentence("dataplane", "unexpected", "")
      and events.start_sentence("dataplane", "upgrade", "")
      == "the dataplane started again: upgrade",
      "start_sentence: one sentence per cause")

print("\n=== observe_starts: a start recorded once, with its cause ===")
facts = {"boot": BOOT, "dataplane": NOW - 60, "control_plane": NOW - 50,
         "boot_reason": "not recorded"}
seen0 = {"boot": BOOT, "dataplane": BOOT + 20, "routing": BOOT + 25}
new, seen = events.observe_starts(facts, [asked], seen0, NOW)
check([(e["source"], e["cause"], e["severity"]) for e in new]
      == [("dataplane", "operator", "notice"), ("routing", "operator", "notice")]
      and new[0]["t"] == NOW - 60 and seen["dataplane"] == NOW - 60,
      f"observe_starts: both restarted by the operator, at their start: "
      f"{[e['text'] for e in new]}")
again, seen2 = events.observe_starts(dict(facts, dataplane=NOW - 58), [asked],
                                     seen, NOW + 10)
check(again == [] and seen2 == seen,
      "observe_starts: the same start read again is said nothing about")
new, _ = events.observe_starts(dict(facts, dataplane=NOW + 3000), [asked],
                               seen, NOW + 3100)
_soon, _ = events.observe_starts(dict(facts, dataplane=NOW - 40), [asked],
                                 seen, NOW)
check([(e["cause"]) for e in _soon] == ["unexpected"],
      "observe_starts: a second start soon after the asked one is not its")
check([(e["cause"], e["severity"]) for e in new] == [("unexpected", "warning")],
      "observe_starts: a start nothing asked for is unexpected, a warning")
nb = {"boot": NOW - 100, "dataplane": NOW - 90, "control_plane": NOW - 80,
      "boot_reason": "system reboot by ana"}
new, seen = events.observe_starts(nb, [], seen0, NOW)
check([(e.get("kind"), e.get("cause")) for e in new]
      == [("boot", None), ("start", "boot"), ("start", "boot")]
      and "system reboot by ana" in new[0]["text"] and seen["boot"] == NOW - 100,
      f"observe_starts: a boot, and both started with it: "
      f"{[e['text'] for e in new]}")
new, seen = events.observe_starts(facts, [], {}, NOW)
check([e.get("kind") for e in new] == ["boot"]
      and seen == {"boot": BOOT, "dataplane": NOW - 60, "routing": NOW - 50},
      "observe_starts: a first reading records the boot, and a start long "
      "after it only as seen - its cause predates the record")
check(events.start_cause([new[0]] + events.observe_starts(
    facts, [asked], seen0, NOW)[0], "dataplane", NOW - 58) == "operator"
      and events.start_cause([], "dataplane", NOW) == ""
      and events.start_cause([start], "dataplane", None) == "",
      "start_cause: the recorded cause of the start running now, or nothing")
ST = os.path.join(work, "events.state")
check(events.read_state(ST) == {} and events.write_state(seen, ST)
      and events.read_state(ST) == seen
      and not events.write_state(seen, os.path.join(work, "no", "state")),
      "read_state / write_state: the last starts recorded, by rename")

print("\n=== the door: any account reads and writes; the kernel names it ===")
DLOG = os.path.join(work, "door.log")
r = doors.answer_event({"event": dict(ev, who="root", t=1)}, 1000,
                       is_operator=lambda u: False, path=DLOG,
                       name_of=lambda u: "carol", now=NOW)
got = events.read_events(DLOG)
check(r["rc"] == 0 and got[-1]["who"] == "carol" and got[-1]["t"] == NOW,
      f"answer_event: an account's event is stamped with its name and the "
      f"door's time, whatever it wrote: {got[-1]['who']} {got[-1]['t']}")
r = doors.answer_event({"event": dict(ev, who="ana")}, 0, path=DLOG, now=NOW)
check(r["rc"] == 0 and events.read_events(DLOG)[-1]["who"] == "ana",
      "answer_event: root speaks for others - the API's accounts, the boot")
r = doors.answer_event({"event": start}, 1000, path=DLOG,
                       is_operator=lambda u: True, name_of=lambda u: "carol")
check(r["rc"] == 126 and "an account writes" in r["out"],
      f"answer_event: an account cannot write a start: {r['out']}")
r = doors.answer_event({"event": asked}, 1000, path=DLOG,
                       is_operator=lambda u: False, name_of=lambda u: "carol")
check(r["rc"] == 126 and "prisma-operator" in r["out"],
      "answer_event: an account that may not restart cannot ask for one")
r = doors.answer_event({"event": asked}, 1000, path=DLOG,
                       is_operator=lambda u: True, name_of=lambda u: "carol")
check(r["rc"] == 0, "answer_event: an operator's asked is written")
r = doors.answer_event({"events": {"kind": "asked"}}, 1000, path=DLOG)
check(r["rc"] == 0 and [e["who"] for e in json.loads(r["out"])] == ["carol"],
      "answer_event: any account reads, filtered on the host")
for bad in ({"events": {"rm": 1}}, {"events": {"last": "x"}},
            {"events": {"last": True}}, {"events": []}):
    check(doors.answer_event(bad, 1000, path=DLOG)["rc"] == 126,
          f"answer_event: not a reading: {bad}")
check(doors.answer_event({"event": ev}, 0,
                         path=os.path.join(work, "no", "x"))["rc"] == 1,
      "answer_event: a record it cannot write is said")
check(doors.account_name(os.getuid()) and doors.account_name(4294967) == "uid 4294967",
      "account_name: the host's name, or the uid")
check(not doors.journaled(json.dumps({"event": ev}))
      and not doors.journaled(json.dumps({"events": {}}))
      and doors.journaled(json.dumps({"argv": ["hostname"]}))
      and doors.journaled("garbage"),
      "journaled: the events' own door calls are not journal noise")

print("\n=== through a real socket: record and fetch, as the VTY calls them ===")
SOCK = os.path.join(work, "doors.sock")
ready = threading.Event()
os.environ["PRISMA_EVENTS"] = os.path.join(work, "served.log")
events.EVENTS_PATH = os.environ["PRISMA_EVENTS"]
threading.Thread(target=doors.serve, daemon=True,
                 kwargs={"path": SOCK, "ready": ready}).start()
check(ready.wait(5), "the daemon listens")
_out = io.StringIO()
with contextlib.redirect_stdout(_out):
    check(events.record(dict(ev, who="mallory"), door=SOCK),
          "record: written through the door")
    _f = events.fetch({"source": "vty"}, door=SOCK)
check(len(_f) == 1 and _f[0]["who"] == doors.account_name(os.getuid()),
      f"fetch: read back, with the caller's real account: {_f}")
check("door:" not in _out.getvalue(),
      f"the daemon journaled nothing for them: {_out.getvalue()!r}")
check(events.fetch({"source": "vty"}, door=os.path.join(work, "none.sock"),
                   path=os.environ["PRISMA_EVENTS"]) == _f,
      "fetch: with no daemon, the file here")
check(events.record(ev, door=os.path.join(work, "none.sock"),
                    path=os.path.join(work, "local.log"))
      and events.read_events(os.path.join(work, "local.log"))[0]["who"] == "alice",
      "record: with no daemon, written here")
check(events._door({"x": 1}, os.path.join(work, "none.sock")) is None,
      "_door: None when nobody listens")

print("\n=== the host's scripts write through it as a program ===")
p = subprocess.run([sys.executable, os.path.join(SRC, "events.py"), "--root",
                    work, "record", "host=upgraded", "notice", "upgraded",
                    "from", "0.151.13", "to", "0.151.14"],
                   capture_output=True, text=True)
_rooted = work + events.EVENTS_PATH
check(p.returncode == 1, f"a root without the directory is a failure: {p.returncode}")
os.makedirs(os.path.dirname(_rooted), exist_ok=True)
p = subprocess.run([sys.executable, os.path.join(SRC, "events.py"), "--root",
                    work, "record", "host=upgraded", "notice", "upgraded",
                    "from", "0.151.13", "to", "0.151.14"],
                   capture_output=True, text=True)
_e = events.read_events(_rooted)
check(p.returncode == 0 and _e and _e[-1]["text"] ==
      "upgraded from 0.151.13 to 0.151.14" and _e[-1]["kind"] == "upgraded"
      and _e[-1]["from"] == "the box", f"events.py record: {p.stdout}{_e}")
p = subprocess.run([sys.executable, os.path.join(SRC, "events.py"),
                    "record", "frr", "notice", "x"], capture_output=True, text=True)
check(p.returncode == 2 and "source is one of" in p.stdout,
      "events.py record: a source it does not have is refused")
with contextlib.redirect_stdout(io.StringIO()):
    check(events.main(["nothing"]) == 2, "main: usage")

print("\n" + ("ALL TESTS PASSED" if not fail else f"{fail} FAILURES"))
sys.exit(1 if fail else 0)
