#!/usr/bin/env python3
"""
An interface's side of the CGNAT, written on the interface (#465).

What is pinned: the text writes `cgnat inside|outside` in the interface's
block and nothing under `nat cgnat`; the three forms a file can carry - the
new one, the nested one under `cgnat` and the plugin's flat one - all read
into the same field; the VTY takes the new statement in an interface block,
refuses a side it does not know, removes it with `no cgnat`, and still takes
the old one at the `cgnat` level with a note.
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src", "prismash"))
import config    # noqa: E402
import prismash  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        print(f"  FAIL: {msg}")
        fail += 1


def ok(msg):
    print(f"  [ ok ] {msg}")


def said(sh, line):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sh.feed(line)
    return out.getvalue()


print("=== the text: on the interface, and not under cgnat ===")
OLD = ("nat\n cgnat\n  pool p\n   inside 100.64.0.0/24\n   outside "
       "203.0.113.0/28\n   exit\n  interface ens18 outside\n"
       "  interface ens19 inside\n  exit\n exit\n"
       "interface ens19\n description CLIENTE\n exit\n")
c = config.Config.parse(OLD)
check(c.unknown == [] and c.interfaces == {"ens18": "outside",
                                           "ens19": "inside"},
      f"the nested form under cgnat, as an older box saved it: "
      f"{c.interfaces} {c.unknown}")
t = c.text()
check("interface ens19\n description CLIENTE\n cgnat inside\n exit" in t
      and "interface ens18\n cgnat outside\n exit" in t,
      f"each side in its interface's block, a block made for it if it had "
      f"none:\n{t}")
check("  interface ens1" not in t,
      "and no interface line left under cgnat")
back = config.Config.parse(t)
check(back.interfaces == c.interfaces and back.unknown == []
      and back.text() == t, "read back, the same field and the same text")
flat = config.Config.parse("cgnat interface ens18 outside\n", dataplane=True)
check(flat.interfaces == {"ens18": "outside"},
      f"the plugin's own flat form, which show bpa config prints: "
      f"{flat.interfaces} {flat.unknown}")
ok("written on the interface; the three forms read into one field")

print("\n=== what a file cannot say ===")
bad = config.Config.parse("interface ens19\n cgnat sideways\n exit\n")
check(bad.unknown and bad.unknown[0][2] == "expected: cgnat <inside|outside>",
      f"a side that is not one is named: {bad.unknown}")
gone = config.Config.parse("cgnat interface ens19 inside\n")
check(gone.unknown and gone.unknown[0][2] == config.ONE_LINE_GONE,
      "the one-line form at column 0 is still refused, as #446 decided")
neg = config.Config.parse("interface ens19\n cgnat inside\n no cgnat\n exit\n")
check(neg.interfaces == {}, "no cgnat in a file takes it out")
ok("a wrong side refused, #446's refusal kept, no cgnat read")

print("\n=== at the prompt ===")


def shell():
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}
    sh.dp = type("D", (), {"TRACE": False, "run": lambda _s, c: (0, ""),
                           "reachable": lambda _s: True})()
    sh.frr = type("F", (prismash.Frr,), {
        "available": lambda s: False, "running": lambda s: [],
        "show": lambda s, c: (1, ""),
        "reload": lambda s, l, check_only=False: (0, "")})()
    return sh


sh = shell()
with contextlib.redirect_stdout(io.StringIO()):
    sh.feed("configure")
    sh.feed("interface ens19")
opts, _, _ = sh._options("cg", True)
check("cgnat" in opts, f"`?` offers it in an interface: {sorted(opts)}")
opts, _, _ = sh._options("cgnat ", False)
check(set(opts) == {"inside", "outside"}, f"and its two sides: {opts}")
out = said(sh, "cgnat inside")
check(out == "" and sh.candidate.interfaces == {"ens19": "inside"},
      f"cgnat inside is taken, silently: {out!r} "
      f"{sh.candidate.interfaces}")
out = said(sh, "show this")
check(" cgnat inside" in out, f"show this reads it back: {out!r}")
out = said(sh, "cgnat sideways")
check("an interface's cgnat side is inside or outside" in out
      and sh.candidate.interfaces == {"ens19": "inside"},
      f"a side that is not one is refused and changes nothing: {out!r}")
said(sh, "no cgnat")
check(sh.candidate.interfaces == {}, "no cgnat takes it out")
with contextlib.redirect_stdout(io.StringIO()):
    sh.feed("exit")
    sh.feed("nat")
    sh.feed("cgnat")
out = said(sh, "interface ens18 outside")
check(sh.candidate.interfaces == {"ens18": "outside"}
      and "note: kept as 'cgnat outside' in the block of interface ens18"
      in out, f"the old statement is taken, and says where it went: {out!r}")
ok("offered, taken, shown, refused, removed; the old door still open")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
