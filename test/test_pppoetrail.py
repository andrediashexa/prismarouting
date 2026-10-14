#!/usr/bin/env python3
"""
The PPPoE session trail, read (#597, docs/bng.md 0.7): src/prismash/
pppoetrail.py, the host's door that answers with it, and the VTY's
`show pppoe history` and `show cgnat translation ... at`.

Each behaviour, shown red by a mutation (named beside the box):

  * the chain is the writer's: sha256(previous digest || the line up to its
    seq), zero before the first (mutation: digest() over the text alone);
  * a changed entry is BROKEN AT it, a removed one BROKEN BEFORE the next,
    sequence checked first (mutation: the digest checked first - a removal
    reads as an edit of the innocent entry after it);
  * a write cut short is not an entry and does not break the chain after it;
  * files moved aside earlier are read first, and a trail whose earliest
    files are gone verifies from where it begins;
  * sessions paired by id across files: a carry is the session it carries,
    a stop alone stands for its session (mutation: a stop without its start
    in the files read dropped);
  * `at` is an instant - begun at or before it, not yet ended (mutation:
    `<=` on the stop, so the instant a session ended still names it,
    beside the session that began then);
  * the files read for an instant are the latest whose month is not after
    it, and those after (mutation: only the instant's own month's file);
  * the door checks every filter's type, on the host;
  * `show cgnat translation ... at` joins only a formula's answer
    (mutation: a dynamic block joined to a past session).
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile

_root = tempfile.mkdtemp(prefix="pppoetrail-")
import atexit  # noqa: E402
atexit.register(shutil.rmtree, _root, True)
for k, v in (("PRISMA_PPPOE_CONF", "pppoe.conf"), ("PRISMA_BNG_DIR", "bng"),
             ("PRISMA_AAA_CONF", "aaa.conf"),
             ("PRISMA_CONFIG", "prisma-routing.conf"),
             ("PRISMA_CONFIRM", "confirm-pending"),
             ("PRISMA_ROLLBACK", "rollback"),
             ("PRISMA_EVENTS", "events.log"),
             ("PRISMA_EVENTS_STATE", "events.state"),
             ("PRISMA_DOORS", "no-doors.sock"),
             ("PRISMA_PPPOE_TRAIL", "trail/pppoe-sessions.log")):
    os.environ[k] = os.path.join(_root, v)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src", "prismash"))
import pppoetrail as T  # noqa: E402
import doors  # noqa: E402
import events  # noqa: E402
import prismash  # noqa: E402
import system  # noqa: E402
from config import Config  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if cond:
        print(f"  [ ok ] {msg}")
    else:
        print(f"  FAIL: {msg}")
        fail += 1


class Writer:
    """The control plane's writer, done again here from the rule alone."""

    def __init__(self, path):
        self.path, self.seq, self.head = path, 0, b"\0" * 32

    def put(self, body, path=None):
        self.seq += 1
        content = f"{body} seq {self.seq}"
        d = hashlib.sha256(self.head + content.encode()).digest()
        self.head = d
        with open(path or self.path, "a") as f:
            f.write(f"{content} sha256 {d.hex()}\n")


TRAIL = os.environ["PRISMA_PPPOE_TRAIL"]
DIR = os.path.dirname(TRAIL)
os.makedirs(DIR)
S1 = "session 00000000000000a1 user sub1 mac 02:aa:00:00:00:01 " \
     "interface host-acc0 address 100.64.0.10"
S2 = "session 00000000000000a2 user joao%20silva mac 02:aa:00:00:00:02 " \
     "interface host-acc0.100 address 100.64.0.11"
S3 = "session 00000000000000a3 user sub3 mac 02:aa:00:00:00:03 " \
     "interface host-acc0 address 100.64.0.10"
SEP = TRAIL + ".2026-09"
w = Writer(TRAIL)
# September: sub1 on .10, all month; sub2 from the 29th, still held when the
# month turns.
w.put("2026-09-01T00:00:00.000Z restart closed 0", SEP)
w.put(f"2026-09-01T10:00:00.000Z start {S1}", SEP)
w.put(f"2026-09-29T08:00:00.500Z start {S2}", SEP)
w.put(f"2026-09-30T12:00:00.000Z stop {S1} since 2026-09-01T10:00:00.000Z "
      f"cause User-Request", SEP)
# October: the file moved aside, sub2 carried, sub3 given .10.
w.put(f"2026-10-02T09:00:00.000Z carry {S2} since 2026-09-29T08:00:00.500Z")
w.put(f"2026-10-02T09:00:00.000Z start {S3}")
w.put(f"2026-10-02T09:30:00.000Z stop {S3} since 2026-10-02T09:00:00.000Z "
      f"cause Lost-Carrier")
w.put("2026-10-02T09:30:00.001Z restart closed 0")
w.put(f"2026-10-02T09:30:00.001Z start {S1.replace('a1', 'b1')}")

print("=== the line ===")
check(T.parse_stamp("2026-10-02T09:30:00.001Z") == 1790933400.001
      and T.parse_stamp("2026-10-02 09:30") is None,
      "parse_stamp: the trail's instant, nothing else")
check(T.stamp(1790933400.001) == "2026-10-02T09:30:00.001Z"
      and T.stamp(1790933400.9996) == "2026-10-02T09:30:01.000Z",
      "stamp: UTC, ms, Z - and a rounding that carries into the second")
check(T.unescape("joao%20silva%25x") == "joao silva%x"
      and T.unescape("-") == "" and T.unescape("cut%+") == "cut%+",
      "unescape: %XX back to bytes; '-' is empty; %+ (cut) left as it is")
rec = T.parse_line(open(TRAIL).readline())
check(rec and rec["kind"] == "carry" and rec["seq"] == 5
      and rec["fields"]["since"] == "2026-09-29T08:00:00.500Z"
      and rec["content"].endswith(" seq 5"),
      "parse_line: kind, fields, seq, and what the digest covers")
check(T.parse_line("2026-10-02T09:30:00.001Z start session 00") is None
      and T.parse_line("2026-10-02T09:30:00.001Z what x seq 1 sha256 "
                       + "0" * 64) is None,
      "parse_line: a cut write, and a kind nobody writes, are not entries")
check(T.digest(b"\0" * 32, "x seq 1") ==
      hashlib.sha256(b"\0" * 32 + b"x seq 1").hexdigest(),
      "digest: sha256 of the previous digest's bytes and the content")

print("\n=== the files ===")
check(T.segments() == [SEP, TRAIL], "segments: the moved-aside month first")
check(T.segment_month(SEP, live=TRAIL) == "2026-09"
      and T.segment_month(TRAIL, live=TRAIL) == "2026-10",
      "segment_month: a moved-aside file's by its name, the live one's by "
      "its first entry")
check(T.files_for(T.parse_stamp("2026-10-15T00:00:00.000Z")) == [TRAIL]
      and T.files_for(T.parse_stamp("2026-09-15T00:00:00.000Z")) == [SEP, TRAIL]
      and T.files_for(T.parse_stamp("2026-11-15T00:00:00.000Z")) == [TRAIL]
      and T.files_for(None) == [SEP, TRAIL],
      "files_for: the latest file not after the instant's month, and after")
check(len(list(T.records([SEP, TRAIL]))) == 9, "records: every entry, in order")

print("\n=== the sessions ===")
got = T.sessions(T.records([SEP, TRAIL]))
by = {s["session"]: s for s in got}
check(len(got) == 4 and by["00000000000000a2"]["user"] == "joao silva"
      and by["00000000000000a2"]["stop"] is None
      and by["00000000000000a2"]["start"] == T.parse_stamp(
          "2026-09-29T08:00:00.500Z"),
      "sessions: a carry is the session it carries, its start kept")
check(by["00000000000000a1"]["cause"] == "User-Request"
      and by["00000000000000a3"]["stop"] == T.parse_stamp(
          "2026-10-02T09:30:00.000Z"),
      "sessions: a stop closes its session, with its cause")
only_oct = {s["session"]: s for s in T.sessions(T.records([TRAIL]))}
check(only_oct["00000000000000a2"]["start"] == T.parse_stamp(
          "2026-09-29T08:00:00.500Z"),
      "sessions: October's file alone knows sub2 from its carry")
lone = T.sessions([T.parse_line(
    f"2026-10-02T09:30:00.000Z stop {S3} since 2026-10-02T09:00:00.000Z "
    f"cause Lost-Carrier seq 7 sha256 " + "0" * 64)])
check(len(lone) == 1 and lone[0]["start"] == T.parse_stamp(
          "2026-10-02T09:00:00.000Z") and lone[0]["stop"] is not None,
      "sessions: a stop whose start is not read stands for the session")
check(T._session(rec)["address"] == "100.64.0.11", "_session: the fields")

at = T.parse_stamp("2026-10-02T09:15:00.000Z")
check([s["user"] for s in T.select(got, address="100.64.0.10", at=at)]
      == ["sub3"], "select: .10 at 09:15 on the 2nd was sub3")
check([s["user"] for s in T.select(got, address="100.64.0.10",
                                   at=T.parse_stamp("2026-09-15T00:00:00.000Z"))]
      == ["sub1"], "  and sub1 on the 15th of September")
edge = T.parse_stamp("2026-10-02T09:30:00.000Z")
check([s["user"] for s in T.select(got, address="100.64.0.10", at=edge)] == [],
      "  at the instant sub3's ended, not sub3 - an end is exclusive")
check([s["session"] for s in T.select(got, user="joao silva")]
      == ["00000000000000a2"] and len(T.select(got, last=2)) == 2
      and T.select(got, session="00000000000000A3")[0]["user"] == "sub3",
      "select: by user, by session id in either case, the last n")
check(len(T.select(got, since=T.parse_stamp("2026-10-01T00:00:00.000Z")))
      == 3, "select: since - every session held from then on, sub1's "
      "September one not")

h = T.history({"address": "100.64.0.10", "at": at})
check([s["user"] for s in h["sessions"]] == ["sub3"] and h["more"] == 0,
      "history: through the files for that instant")
T.ROWS_MAX, _rm = 1, T.ROWS_MAX
h = T.history({})
T.ROWS_MAX = _rm
check(len(h["sessions"]) == 1 and h["more"] == 3,
      "history: at most ROWS_MAX, the newest, and how many more")

print("\n=== verify ===")
rep = T.verify()
check(rep["problem"] is None and rep["checked"] == 9 and rep["head_seq"] == 9
      and rep["head_sha"] == w.head.hex() and rep["files"] == 2,
      "verify: nine entries across two files, the head the writer's")
scr = T.verify_screen(rep)
check("1 earlier file(s)" in scr[1] and
      "9 entries checked, chain intact" in scr[2]
      and scr[4] == f"  sha256 {w.head.hex()}"
      and all(len(x) <= 79 for x in scr[1:]),
      f"verify_screen: intact, the head whole, a console's width: {scr}")


# docs/bng.md 0.7 quotes the screen: its closing lines - where the head is
# to be compared - are the ones the screen prints. No doc test reads this
# screen, and the lines changed when the head began to be sent off the box.
_doc = open(os.path.join(HERE, "..", "docs", "bng.md")).read()
_quote = _doc.split("prisma> show pppoe history verify\n", 1)[-1] \
    .split("```", 1)[0].splitlines()
check(len(_quote) > 3 and _quote[-3:] == scr[-3:],
      f"docs/bng.md quotes verify's closing lines as the screen prints them: "
      f"{_quote[-3:]}")


def tampered(edit):
    """verify() of a copy of the trail with `edit` applied to its lines."""
    d = tempfile.mkdtemp(dir=_root)
    for p in (SEP, TRAIL):
        shutil.copy(p, d)
    p = os.path.join(d, os.path.basename(TRAIL))
    lines = open(p).readlines()
    open(p, "w").writelines(edit(lines))
    return T.verify(p)


rep = tampered(lambda ls: [ls[0], ls[1].replace("100.64.0.10", "100.64.0.12")]
               + ls[2:])
check(rep["problem"] and rep["problem"][0].startswith("BROKEN at entry 6")
      and "changed" in rep["problem"][1]
      and (rep["kind"], rep["entry"]) == ("altered", 6),
      f"verify: an address changed is BROKEN at its entry: {rep['problem']}")
rep = tampered(lambda ls: ls[:1] + ls[2:])
check(rep["problem"] and rep["problem"][0].startswith("BROKEN before entry 7")
      and "1 entry was removed" in rep["problem"][1]
      and (rep["kind"], rep["entry"]) == ("removed", 7),
      "verify: an entry removed is BROKEN before the next - not an edit of it")
check(T.verify()["kind"] == "" and T.verify()["entry"] == 0,
      "verify: an intact chain names no kind and no entry (the API's verdict)")
rep = tampered(lambda ls: ls[:2] + ["2026-10-02T09:20:00.000Z start sess\n"]
               + ls[2:])
check(rep["problem"] is None and rep["checked"] == 9
      and rep["uncovered"] == ["pppoe-sessions.log line 3"]
      and "not an entry" in "".join(T.verify_screen(rep)),
      "verify: a write cut short is named, and the chain goes on past it")
d = tempfile.mkdtemp(dir=_root)
shutil.copy(TRAIL, d)
rep = T.verify(os.path.join(d, os.path.basename(TRAIL)))
check(rep["problem"] is None and rep["first"] == 5 and rep["checked"] == 5
      and "begins at entry 5" in "".join(T.verify_screen(rep)),
      "verify: September's file gone, October's verifies from entry 5")
open(os.path.join(d, "x.log"), "w").write("2026-10-02T09:30:00.001Z restart\n")
rep = T.verify(os.path.join(d, "x.log"))
check("nothing here is covered" in "".join(T.verify_screen(rep)),
      "verify_screen: a file with no chained entry is never 'intact'")
check("no PPPoE session has been recorded" in
      "".join(T.verify_screen(T.verify(os.path.join(d, "none.log")))),
      "verify_screen: no trail at all")

print("\n=== the head, read from the end (sent off the box) ===")
# The host's watcher reads it every minute (eventwatch.trail_changes): from
# each file's end, never a verify. Mutations shown red: last_entry() not
# reading further back when its first 4 kB hold no whole entry (the 5 kB
# line), and head() taking the newest moved-aside file for the live one.
check(T.last_entry(TRAIL)["seq"] == 9 and T.last_entry(SEP)["seq"] == 4
      and T.last_entry(TRAIL)["sha"] == w.head.hex(),
      "last_entry: each file's last entry")
check(T.last_entry(os.path.join(DIR, "none.log")) is None,
      "last_entry: no file is none")
hd = T.head()
check(hd == {"file": "pppoe-sessions.log", "seq": 9, "sha": w.head.hex(),
             "closed": [{"file": "pppoe-sessions.log.2026-09", "seq": 4,
                         "sha": T.last_entry(SEP)["sha"]}]}
      and (hd["seq"], hd["sha"]) == (T.verify()["head_seq"],
                                     T.verify()["head_sha"]),
      f"head: the live file's last entry - verify()'s head - and the "
      f"moved-aside file's closing one: {hd}")
check(T.head(os.path.join(DIR, "none.log")) ==
      {"file": "", "seq": 0, "sha": "", "closed": []},
      "head: no trail at all - PPPoE never wrote one - is seq 0")
check(T.head(closed_max=0)["closed"] == [],
      "head: closed_max 0 reads no moved-aside file")
d = tempfile.mkdtemp(dir=_root)
shutil.copy(SEP, d)
lone_sep = T.head(os.path.join(d, os.path.basename(TRAIL)))
check(lone_sep["seq"] == 4 and lone_sep["file"].endswith(".2026-09"),
      "head: the month just moved aside and nothing written since - its "
      "last entry is the head")
# A long file, a torn last write, and a line far longer than one reading.
d = tempfile.mkdtemp(dir=_root)
lp = os.path.join(d, "pppoe-sessions.log")
lw = Writer(lp)
for i in range(60):
    lw.put(f"2026-10-02T09:{i:02d}:00.000Z start {S1}")
lw.put(f"2026-10-02T10:00:00.000Z start {S1.replace('sub1', 'x' * 5000)}")
with open(lp, "a") as fh:
    fh.write("2026-10-02T10:00:01.000Z start session 00")
check(T.last_entry(lp)["seq"] == 61 and T.last_entry(lp)["sha"] ==
      lw.head.hex(),
      "last_entry: past a write cut short, a 5 kB line read whole")
with open(lp, "a") as fh:
    fh.write("\n" + "y" * (T.TAIL_MAX + 10) + "\n")
check(T.last_entry(lp) is None,
      "last_entry: TAIL_MAX of what is not an entry is not read past")

print("\n=== the words ===")
now = T.parse_stamp("2026-10-13T12:00:00.000Z")
f, why = T.parse_show_args(["address", "100.064.0.10", "at",
                            "2026-10-02T09:15:00Z"], events.parse_since, now)
check(why is None and f == {"address": "100.64.0.10", "at": at},
      f"parse_show_args: an address and an instant: {f} {why}")
f, why = T.parse_show_args([], events.parse_since, now)
check(f == {"last": T.SHOW_LAST}, "parse_show_args: the last 50 by default")
for words, needle in ((["at", "x"], "not a time"),
                      (["at", "1h", "since", "2h"], "give one of them"),
                      (["address", "100.64.0.300"], "IPv4"),
                      (["session", "zz"], "hexadecimal"),
                      (["last", "0"], "number of sessions"),
                      (["user"], "needs a value"),
                      (["user", "a", "user", "b"], "twice"),
                      (["mac", "x"], "takes user")):
    f, why = T.parse_show_args(words, events.parse_since, now)
    check(f is None and needle in why, f"parse_show_args refuses {words}")
lines = T.session_lines(by["00000000000000a2"])
check(lines[0] == "joao silva held 100.64.0.11"
      and lines[1] == "  from 2026-09-29T08:00:00.500Z  to still held"
      and lines[2].endswith("on host-acc0.100"),
      f"session_lines: who, when, which session: {lines}")
check(T.session_lines(by["00000000000000a1"], "    ")[1].endswith(
      "2026-09-30T12:00:00.000Z  User-Request"), "  an ended one, its cause")
check(T.history_screen({"sessions": []}) == ["no PPPoE session matches"]
      and "older session(s)" in T.history_screen(
          {"sessions": [by["00000000000000a1"]], "more": 2})[0],
      "history_screen: nothing, and what was left out")

check(T.translation_rows("100.64.0.10", at, [by["00000000000000a3"]])[0]
      == "  PPPoE, at 2026-10-02T09:15:00.000Z:"
      and T.translation_rows("100.64.0.99", at, [], None, True)
      == ["  PPPoE: no session held 100.64.0.99 at 2026-10-02T09:15:00.000Z"]
      and T.translation_rows("100.64.0.99", at, [], "no door", True)[0]
      .endswith("(no door)")
      and T.translation_rows("100.64.0.99", at, [], None, False) == [],
      "translation_rows: the holder, nobody, why not read, or nothing at all")

print("\n=== the door ===")
r = T.answer({"address": "100.64.0.10", "at": at})
check(r["rc"] == 0 and [s["user"] for s in json.loads(r["out"])["sessions"]]
      == ["sub3"], "answer: a reading, as JSON")
check(T.answer({"verify": True})["rc"] == 0 and
      json.loads(T.answer({"verify": True})["out"])["checked"] == 9,
      "answer: verify")
for bad in ({"at": "yesterday"}, {"last": True}, {"rm": 1}, ["x"]):
    check(T.answer(bad)["rc"] == 126, f"answer: refuses {bad}")
r = doors.answer(json.dumps({"pppoe-history": {"user": "sub3"}}), 1000)
check(r["rc"] == 0 and "sub3" in r["out"],
      "doors.answer: any account may read it, as `show pppoe sessions`")
check(doors.answer_pppoe_history({"pppoe-history": {"last": "x"}})["rc"]
      == 126, "answer_pppoe_history: a filter of the wrong type refused")
check(T._door({"x": 1}, os.path.join(_root, "nobody.sock")) is None,
      "_door: no door listening, None - the files are read here")
got, why = T.fetch({"user": "sub3"})
check(why is None and got["sessions"][0]["user"] == "sub3",
      "fetch: with no door, the files read here")
rep, why = T.fetch_verify()
check(why is None and rep["checked"] == 9, "fetch_verify: the same way")

print("\n=== the VTY ===")
OUT_FORMULA = ("200.150.0.16:1024  ->  100.64.0.10\n"
               "  pool lab, block 0, DETERMINISTIC\n"
               "  from the FORMULA. It holds for any instant, past or present.")
OUT_DYN = ("200.150.0.16:1024  ->  100.64.0.10\n  pool lab, block 0, DYNAMIC\n"
           "  this answers for NOW ONLY.")
check(prismash.translation_inside(OUT_FORMULA) == ("100.64.0.10", "formula")
      and prismash.translation_inside(OUT_DYN) == ("100.64.0.10", "now")
      and prismash.translation_inside("200.150.0.16:1  ->  nobody, right now")
      == (None, None) and prismash.translation_inside("") == (None, None),
      "translation_inside: the inside address and whether it holds for any "
      "instant")


class Dp:
    def __init__(self, out):
        self.out, self.commands = out, []

    def run(self, cmd):
        self.commands.append(cmd)
        return 0, self.out


def shell(dp_out, pppoe=True):
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT, prismash.CAP_OPERATOR}
    sh.dp = Dp(dp_out)
    sh.sys = system.System(runner=lambda argv, stdin=None, timeout=30: (0, ""))
    text = ("pppoe\n interface host-acc0\n gateway 100.64.0.1\n"
            " address-pool 100.64.0.10-100.64.0.20\n local-user a password b\n"
            " exit\n") if pppoe else ""
    sh.running_config = lambda: Config.parse(text)
    return sh


def said(fn):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn()
    return out.getvalue()


out = said(lambda: shell(OUT_FORMULA).show_cgnat_translation(
    ["200.150.0.16", "1024", "at", "2026-10-02T09:15:00Z"]))
check("->  100.64.0.10" in out and "PPPoE, at 2026-10-02T09:15:00.000Z:" in out
      and "    sub3 held 100.64.0.10" in out and "sub1" not in out,
      f"show_cgnat_translation at: the formula's inside, and who held it then: {out!r}")
out = said(lambda: shell(OUT_DYN).show_cgnat_translation(
    ["200.150.0.16", "1024", "at", "2026-10-02T09:15:00Z"]))
check("'at' needs a formula" in out and "sub3" not in out
      and "show pppoe history address" in out,
      "show_cgnat_translation: a dynamic block is never joined to the past")
sh = shell(OUT_DYN)
out = said(lambda: sh.show_cgnat_translation(["200.150.0.16", "1024"]))
check("DYNAMIC" in out and "sub1 held 100.64.0.10" in out
      and sh.dp.commands == ["show bpa translation 200.150.0.16 1024"],
      f"show_cgnat_translation: now, the session holding it now: {out!r}")
out = said(lambda: shell(OUT_FORMULA.replace("100.64.0.10", "100.64.0.99"))
           .show_cgnat_translation(["200.150.0.16", "1024"]))
check("PPPoE: no session held 100.64.0.99" in out,
      "  an address no session holds, said where PPPoE is configured")
out = said(lambda: shell(OUT_FORMULA.replace("100.64.0.10", "100.64.0.99"),
                         pppoe=False).show_cgnat_translation(
    ["200.150.0.16", "1024"]))
check("PPPoE" not in out, "  and nothing said on a box with no PPPoE")
out = said(lambda: shell("").show_cgnat_translation(
    ["200.150.0.16", "1024", "at", "whenever"]))
check("not a time" in out, "show_cgnat_translation: a time that is not one")
out = said(lambda: shell("").show_pppoe_history(
    ["address", "100.64.0.10", "at", "2026-10-02T09:15:00Z"]))
check(out.startswith("sub3 held 100.64.0.10\n"),
      f"show_pppoe_history: the session, three lines: {out!r}")
out = said(lambda: shell("").show_pppoe_history(["verify"]))
check("chain intact" in out, "show_pppoe_history verify")
out = said(lambda: shell("").show_pppoe(["history", "frobnicate"]))
check("not a filter it takes" in out, "show_pppoe history: a refusal")

# docs/bng.md 0.4 item 2: the reading the screen and the API share.
f = shell("").pppoe_history_facts(["address", "100.64.0.10", "at",
                                   "2026-10-02T09:15:00Z"])
check(f["why_filter"] is None and f["why_read"] is None
      and [s["user"] for s in f["got"]["sessions"]] == ["sub3"],
      "pppoe_history_facts: the filters read, the trail asked once")
check(said(lambda: shell("").show_pppoe_history_of(f)).startswith(
          "sub3 held 100.64.0.10\n"),
      "show_pppoe_history_of: the screen drawn from that reading")
f = shell("").pppoe_history_facts(["frobnicate"])
check(f["why_filter"] and f["got"] is None
      and "not a filter it takes" in said(
          lambda: shell("").show_pppoe_history_of(f)),
      "pppoe_history_facts: a filter refused, in the VTY's words")
check("trail was not read" in said(lambda: shell("").show_pppoe_history_of(
          {"why_filter": None, "why_read": "no door", "got": None})),
      "show_pppoe_history_of: a trail not read says so")
f = shell("").pppoe_verify_facts()
check(f["why"] is None and f["rep"]["checked"] == 9
      and "chain intact" in said(lambda: shell("").show_pppoe_verify(f)),
      "pppoe_verify_facts / show_pppoe_verify: one check, its screen")
check("not read" in said(lambda: shell("").show_pppoe_verify(
          {"rep": None, "why": "no door"})),
      "show_pppoe_verify: a check that could not be read says so")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
