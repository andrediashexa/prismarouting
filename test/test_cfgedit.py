#!/usr/bin/env python3
"""
`replace pattern` and `rename` in configuration mode (#585): what each
changes, what each refuses, and that a refused one leaves the candidate as it
was.

The rules under test are the issue's: both change only the candidate; the
result passes the checks a typed line passes, and is refused WHOLE, naming
the first failing line, when it would not; `rename` follows every reference
or refuses naming the one it cannot; a taken name is refused; `replace`
says how many substitutions it made and has a dry run, `| compare`.

Each case names the mutation it was shown red against, in the house style.
"""
import contextlib
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src",
                                "prismash"))
import cfgedit       # noqa: E402
import prismash      # noqa: E402
import vppapi        # noqa: E402
from cfgedit import (edit_tokens, replace_request, statement_paths,  # noqa
                     in_scope, substitute, rename_target, name_positions,
                     declared_names, neighbour_names, rename_lines,
                     interface_shape, interface_rename_error,
                     substituted_row)
from config import Config  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  {msg}")


RUNNING = """! cgnat
nat
 cgnat
  pool cgnat
   prefix-list-inside IN
   prefix-outside 200.0.0.0/30
   block-size 512
   exit
  pool other
   prefix-inside 100.70.0.0/24
   prefix-outside 200.0.1.0/30
   block-size 512
   exit
  exit
 exit
interface enp1s0.801
 vlan 801
 description uplink enp1s0.801
 exit
interface loop0
 exit
user ops
 role administrator
 exit
! routing
ip prefix-list IN seq 5 deny 100.64.1.0/24 description "management range"
ip prefix-list IN seq 10 permit 100.64.0.0/22 le 32
ipv6 prefix-list IN seq 5 permit 2001:db8::/32
route-map RM permit 10
 match ip address prefix-list IN
exit
router bgp 65000
 neighbor PG peer-group
 neighbor 1.1.1.1 remote-as 1
 neighbor 1.1.1.1 peer-group PG
 neighbor 1.1.1.1 description to IN and RM
 address-family ipv4 unicast
  neighbor PG route-map RM out
  neighbor PG prefix-list IN in
  network 200.0.0.0/23
 exit-address-family
exit
ip route 10.9.0.0/16 10.0.0.2 enp1s0.801
ip route 200.0.0.0/23 blackhole
router ospf
 passive-interface enp1s0.801
exit
interface enp1s0.801
 ip ospf area 0
exit
"""


class Api:
    """The binary API: every call OK, every dump empty."""

    def available(self):
        return (vppapi.OK, "stub")

    def interface_index(self, name):
        return None

    def call(self, message, args):
        return vppapi.Reply(vppapi.OK)

    def dump(self, message, args):
        return vppapi.Reply(vppapi.OK, fields={"rows": []})


class Dp(prismash.Vpp):
    """A dataplane whose CLI answers nothing and accepts everything."""

    TRACE = False

    def __init__(self):
        self.commands = []

    def run(self, cmd):
        self.commands.append(cmd)
        return 0, ""

    def running(self):
        return 0, ""


class Frr(prismash.Frr):
    """FRR up, reloading anything, its parser refusing a line with `bogus`
    in it in FRR's own words; `up=False` is a daemon that does not answer."""

    def __init__(self, up=True):
        self.up = up
        self.asked = []

    def available(self):
        return self.up

    def show(self, cmd):
        return (0, "") if self.up else (1, "")

    def reload(self, lines, check_only=False):
        return (0, "")

    def dryrun(self, ctx, line):
        self.asked.append((list(ctx), line))
        if "bogus" in line:
            return prismash.Frr.DRYRUN_REFUSED, f"% Unknown command: {line}"
        return prismash.Frr.DRYRUN_ACCEPTED, ""


def shell(running=RUNNING, up=True):
    """A Shell in configuration mode over the stubs above, its capability
    stated outright for the reason test_prismash_apply.py records."""
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT, prismash.CAP_OPERATOR}
    sh._prompt_name = prismash.HOSTNAME_DEFAULT
    sh.api = Api()
    sh.dp = Dp()
    sh.frr = Frr(up)
    sh.running_config = lambda: Config.parse(running)
    with contextlib.redirect_stdout(io.StringIO()):
        sh.feed("configure")
    return sh


def said(sh, *lines):
    """What the VTY printed for `lines`, fed one after another."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for line in lines:
            sh.feed(line)
    return out.getvalue()


def flat(text):
    return " ".join(text.split())


print("=== the words: quotes, the pipe, upto (#585) ===")
# Mutation shown red: edit_tokens() splitting inside a quoted run - the
# pattern "a b" arrives as two words and the request is a usage refusal.
check(edit_tokens('replace pattern "a b|c" with x') ==
      [("replace", False), ("pattern", False), ("a b|c", True),
       ("with", False), ("x", False)], "a quoted run is one word")
check(edit_tokens('x "a\\"b"') == [("x", False), ('a"b', True)],
      "a quote inside one")
check(edit_tokens('x ""') == [("x", False), ("", True)], "an empty one")
check(edit_tokens('x "open') is None, "a quote left open is None")
t = edit_tokens("pattern a with b upto 2 | compare")
check(replace_request(t) == ("a", "b", 2, True), f"{replace_request(t)!r}")
t = edit_tokens("pattern a with b | compare upto 2")
check(replace_request(t) == ("a", "b", 2, True), "either order")
t = edit_tokens('pattern a with "|" compare')
check(isinstance(replace_request(t), str), "a quoted | is no pipe")
check(replace_request(edit_tokens("pattern a with b upto 0")).startswith(
    "upto:"), "upto 0 refused")
check(replace_request(edit_tokens("pattern a b")).startswith("usage:"),
      "no `with`")
check(replace_request(edit_tokens('pattern "" with b')).startswith("usage:"),
      "an empty pattern")
ok("a quoted run, the pipe only bare, upto and | compare in either order")

print("\n=== where each statement is: the two halves read their own way ===")
lines = Config.parse(RUNNING).text().splitlines()
paths = statement_paths(lines)
at = {l.strip(): p for l, p in zip(lines, paths) if p is not None}
check(at["block-size 512"] == ["nat", "cgnat", "pool cgnat"] or
      at["block-size 512"] == ["nat", "cgnat", "pool other"],
      f"a pool's statement: {at['block-size 512']!r}")
check(at["neighbor PG route-map RM out"] ==
      ["router bgp 65000", "address-family ipv4 unicast"],
      f"FRR's nesting: {at['neighbor PG route-map RM out']!r}")
check(at["vlan 801"] == ["interface enp1s0.801"], "an interface's")
check(at["10.9.0.0/16 next-hop 10.0.0.2 enp1s0.801"] == ["router static"],
      "a static route in its block")
check(all(p is None for l, p in zip(lines, paths)
          if l.strip() in ("exit", "! routing", "! cgnat")),
      "closers and comments are no statement")
check(in_scope(["nat", "cgnat", "pool A"], ["nat", "cgnat"]), "inside")
check(not in_scope(["nat"], ["nat", "cgnat"]), "the header is not")
check(not in_scope(None, []), "a closer is not")
ok("indentation for the box's half, block_paths() for FRR's")

print("\n=== substitute: per statement, upto, the two refusals ===")
# Mutation shown red: substitute() ignoring `scope` (in_scope always True) -
# the pool's replace then changes the other pool's block-size too.
new, n, c = substitute(lines, paths, ["nat", "cgnat", "pool other"],
                       re.compile("512"), "1024")
check((n, c) == (1, 1), f"one pool's only: {(n, c)!r}")
check(new[lines.index("  pool cgnat") + 3] == "   block-size 512",
      "the other pool's untouched")
# Mutation shown red: `upto` not passed to finditer's slice - every match is
# replaced.
new, n, c = substitute(lines, paths, [], re.compile(r"100\.64"), "100.65",
                       upto=1)
check(n == 1 and sum("100.65" in l for l in new) == 1, f"upto 1: {n}")
# Mutation shown red: `upto` not passed to finditer's slice - a statement
# with three matches has all three replaced under `upto 2`.
new, n, c = substitute(["x a a a"], [[]], [], re.compile("a"), "b", upto=2)
check(new == ["x b b a"] and n == 2, f"upto inside one statement: {new!r}")
for pat, repl, why in (("x*", "y", "empty"), ("512", "5\n12", "break")):
    try:
        substitute(lines, paths, [], re.compile(pat), repl)
        check(False, f"{pat!r} -> {repl!r} was taken")
    except ValueError as e:
        check(e.args[0][0] == why, f"{why}: {e.args[0]!r}")
check(substituted_row(re.compile("a"), "b", "aa") == "bb", "a row")
check(substituted_row(re.compile("a"), "\\3", "a") == "a",
      "a replacement that cannot be used leaves the row")
ok("a level covers its statements, upto counts matches, two refusals")

print("\n=== rename: what it takes, and every reference it follows ===")
check(rename_target(["nat", "cgnat", "pool"]) == ("pool", "cgnat"), "pool")
check(rename_target(["ipv6", "prefix-list"]) == ("prefix-list ipv6", None),
      "an IPv6 list")
check(rename_target(["router", "bgp", "65000", "address-family", "ipv4",
                     "unicast", "peer-group"]) ==
      ("peer-group", "router bgp 65000"), "from an address-family")
check(rename_target(["router", "bgp", "1", "vrf", "red", "peer-group"]) ==
      ("peer-group", "router bgp 1 vrf red"), "a VRF's instance")
check(rename_target(["policy-options", "prefix-list"]) is None,
      "policy-options is not taken yet")
check(name_positions("frr", [], "route-map RM permit 10".split()) ==
      {1: "route-map"}, "a route-map's declaration")
pos = name_positions("frr", ["router bgp 65000", "address-family ipv6"],
                     "neighbor PG prefix-list IN in".split())
check(pos.get(3) == "prefix-list ipv6", f"a list by its family: {pos!r}")
check(name_positions("frr", [], "bgp as-path access-list IN permit _1_"
                     .split()).get(3) == "other", "an AS-path list's name")
check(1 not in name_positions("frr", ["router rip"],
                              "redistribute IN".split()),
      "a word nothing recognises is unknown")
check(name_positions("vty", ["interface ae0"], "member ens19".split()) ==
      {1: "interface"}, "a bundle's member")
check(set(declared_names(lines, paths, "prefix-list ip")) == {"IN"}, "IPv4")
check(declared_names(lines, paths, "peer-group", "router bgp 65000") ==
      {"PG"}, "a peer-group")
check(neighbour_names(lines, paths, "router bgp 65000") == {"PG", "1.1.1.1"},
      "every neighbour")
# Mutation shown red: _kind_matches() ignoring the family - the IPv6 list
# named IN is renamed with the IPv4 one.
new, n, why = rename_lines(lines, paths, "prefix-list ip", None, "IN", "A")
check(why is None and n == 5, f"five places: {n}, {why!r}")
check("ipv6 prefix-list IN seq 5 permit 2001:db8::/32" in new,
      "the IPv6 list of the same name is another list")
check(any("description to IN and RM" in l for l in new),
      "a description is text, not a reference")
new, n, why = rename_lines(lines, paths, "interface", None, "enp1s0.801",
                           "enp1s0.802")
check(why is None and n == 4, f"declaration, static, passive, FRR: {n}")
check(" description uplink enp1s0.801" in new, "text again")
# Mutation shown red: rename_lines() renaming what it can and saying nothing
# of the rest - `redistribute IN`'s IN is left dangling.
odd = lines + ["router rip", " distribute-list prefix IN out", "exit"]
_, _, why = rename_lines(odd, statement_paths(odd), "prefix-list ip", None,
                         "IN", "A")
check(why == "distribute-list prefix IN out", f"cannot follow: {why!r}")
# Mutation shown red: rename_lines() passing over a position nothing reads -
# `distribute-list IN` (an access-list's place) is renamed around, silently.
odd = lines[:-1] + [" neighbor 1.1.1.1 distribute-list IN in", "exit"]
_, _, why = rename_lines(odd, statement_paths(odd), "prefix-list ip", None,
                         "IN", "A")
check(why == "neighbor 1.1.1.1 distribute-list IN in",
      f"a position nothing reads: {why!r}")
check(interface_shape("ae3") == "bundle" and interface_shape("loop1") ==
      "loopback" and interface_shape("ens19.7") == "sub ens19" and
      interface_shape("ens19") == "port", "the four shapes")
check(interface_rename_error("ens19", "ens20", set())[0].startswith(
    "a physical port"), "a port")
check(interface_rename_error("ens19.7", "ens20.7", set())[0].startswith(
    "a sub-interface keeps"), "another parent")
check(interface_rename_error("loop0", "ae1", set())[0].startswith(
    "a loopback is renamed"), "another kind")
check(interface_rename_error("ae0", "ae1", {"ae0.5"})[0].startswith(
    "its sub-interfaces"), "a parent with children")
check(interface_rename_error("ens19.7", "ens19.8", set()) is None, "fine")
check(cfgedit._is_address("2001:db8::1") and not cfgedit._is_address("x"),
      "an address")
check(cfgedit._route_interfaces("ip route P 10.0.0.2 ens19 5".split(), 3)
      == {4: "interface"}, "a route's interface, not its distance")
check(cfgedit._vty_positions([], "interface ens19".split()) ==
      {1: "interface"}, "an interface block")
check(cfgedit._frr_positions([], "interface ens19 vrf red".split()) ==
      {1: "interface", 3: "other"}, "FRR's interface stanza")
check(cfgedit._kind_matches("neighbor router bgp 1", "interface", None),
      "BGP unnumbered names an interface")
check(cfgedit._sections(["a", "! routing", "b"]) == ["vty", "frr", "frr"],
      "the halves")
ok("five kinds, their references by position, the rest refused")

print("\n=== at the prompt: replace, its count and its dry run ===")
sh = shell()
before = sh.candidate.text()
# Mutation shown red: stage_edit() staging on `| compare` - the candidate
# then changes on a dry run.
out = said(sh, r"replace pattern 100\.64 with 100.65 | compare")
check("-ip prefix-list IN seq 10 permit 100.64.0.0/22 le 32" in out
      and "+ip prefix-list IN seq 10 permit 100.65.0.0/22 le 32" in out,
      f"the dry run shows the difference: {out!r}")
check("2 substitution(s) in 2 statement(s)" in out, "and the count")
check(sh.candidate.text() == before, "and stages nothing")
out = said(sh, r"replace pattern 100\.64 with 100.65 upto 1")
check("1 substitution(s) in 1 statement(s)" in out and "staged" in out,
      f"staged: {out!r}")
check(sh.candidate.text().count("100.65.") == 1, "one of two")
out = said(sh, "show configuration compare")
check("+ip prefix-list IN seq 5 deny 100.65.1.0/24" in out,
      "`show configuration compare` reads the result")
check(sh.running_config().text() == Config.parse(RUNNING).text(),
      "and nothing reached the box")
out = said(sh, "rollback 0")
check(sh.candidate.text() == before, "`rollback 0` takes it back")
check(said(sh, "replace pattern zzz with y").startswith(
    "replace: 0 match(es)"), "no match, nothing staged")
ok("counted, dry-run, staged in the candidate only, rolled back")

print("\n=== a level covers itself, and the session stays in it ===")
sh = shell()
said(sh, "nat", "cgnat", "pool other")
p = sh.candidate.pools["other"]
out = said(sh, "replace pattern 512 with 1024")
check(str(sh.candidate.pools["other"].block_size) == "1024" and
      str(sh.candidate.pools["cgnat"].block_size) == "512",
      f"one pool: {out!r}")
# Mutation shown red: rebind_block() not called - `block-size` typed next
# goes to the Pool the candidate no longer holds.
check(sh.pool is sh.candidate.pools["other"] and sh.pool is not p,
      "the session edits the new pool")
said(sh, "block-size 2048")
check(str(sh.candidate.pools["other"].block_size) == "2048",
      "the next statement")
check(sh.edit_scope() == ["nat", "cgnat", "pool other"], "the scope")
said(sh, "top", "router bgp 65000")
check(sh.edit_scope() == ["router bgp 65000"], "an FRR block's")
out = said(sh, "replace pattern RM with RM2")
check("fails the commit check" in out and "no route-map called 'RM2'" in out,
      f"the map's declaration is outside `router bgp`, so it dangles: {out!r}")
out = said(sh, 'replace pattern "remote-as 1$" with "remote-as 2"')
check("neighbor 1.1.1.1 remote-as 2" in sh.candidate.text(), f"{out!r}")
said(sh, "top", "router static")
check(sh.edit_scope() == ["router static"], "inside router static")
check(said(sh, "replace pattern zzz with y").startswith("replace: 0"),
      "dispatched there too, not refused as a route")
said(sh, "top", "user ops")
check(sh.edit_scope() == ["user ops"] and sh.rename_level() == ["user",
                                                                "ops"],
      "an account")
ok("the level's statements only, and the block follows the new candidate")

print("\n=== refused whole, naming the first failing line ===")
sh = shell()
before = sh.candidate.text()
# Mutation shown red: typed_refusal() answering None - `block-size 7`, which
# the file door takes (config.DOORS_DIFFER), is staged.
out = said(sh, 'replace pattern "block-size 512" with "block-size 7"')
check("of the result is refused; nothing staged" in out
      and "block-size 7: expected 64-32768" in out, f"the prompt's: {out!r}")
check(sh.candidate.text() == before, "nothing left behind")
out = said(sh, "replace pattern RM with bogus")
check("route-map bogus permit 10" in out and "FRR answered" in out,
      f"FRR's, in its block: {out!r}")
check(sh.candidate.text() == before, "nothing left behind")
# Mutation shown red: edit_refusal() without the read-back - two pools
# merged into one under the name `other` is staged as one pool.
out = said(sh, "replace pattern \"pool cgnat\" with \"pool other\"")
check("of the result reads back otherwise; nothing staged" in out,
      f"merged: {out!r}")
out = said(sh, 'replace pattern "role administrator" with "role king"')
check("line" in out and "role 'king'" in out, f"the file door: {out!r}")
# Mutation shown red: edit_check() answering [] - the list's declaration
# renamed by hand leaves the pool's prefix-list-inside dangling, staged.
out = said(sh, 'replace pattern "prefix-list IN seq" with '
               '"prefix-list IN2 seq"')
check("the result fails the commit check; nothing staged" in out
      and "prefix-list-inside IN does not exist" in out, f"{out!r}")
check(sh.candidate.text() == before, "nothing left behind")
for line, needle in (
        ('replace pattern "x', "a quote is left open"),
        ("replace pattern ( with x", "not a regular expression"),
        ('replace pattern "x*" with y', "matches an empty string"),
        ('replace pattern 512 with "a\\nb"', "breaks a statement in two"),
        (r"replace pattern 512 with \3", "the replacement cannot be used")):
    out = said(sh, line)
    check(needle in out, f"{line}: {out!r}")
check(sh.candidate.text() == before, "nothing left behind by any of them")
ok("the prompt's door, FRR's, the read-back, the commit check")

print("\n=== the commit check: only what THIS edit causes ===")
sh = shell(up=False)
rows = sh.quiet_check(sh.candidate)
check(not rows[0] and any("FRR is not answering" in r for r in rows[1]),
      f"the candidate already fails: {rows!r}")
failed = sh.failed
out = said(sh, "replace pattern 512 with 1024")
check("staged" in out and sh.failed == failed,
      f"a refusal the edit did not cause is not the edit's: {out!r}")
check(sh.edit_check(sh.candidate, sh.candidate) == [], "the same, twice")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.do_replace("replace pattern zzz with y")
    sh.do_rename("rename route-map RM to RM2")
check("0 match(es)" in out.getvalue() and "renamed in 2 place(s)" in
      out.getvalue(), f"the two handlers, called whole: {out.getvalue()!r}")
ok("a daemon down refuses `commit`, not an edit elsewhere")

print("\n=== rename at the prompt: references, taken names, refusals ===")
sh = shell()
out = said(sh, "rename ip prefix-list IN to CGN-IN")
text = sh.candidate.text()
check("renamed in 5 place(s)" in out, f"{out!r}")
check("prefix-list-inside CGN-IN" in text and "match ip address "
      "prefix-list CGN-IN" in text and "neighbor PG prefix-list CGN-IN in"
      in text, "the pool's side, the route-map and BGP")
check('ip prefix-list CGN-IN seq 5 deny 100.64.1.0/24 description '
      '"management range"' in text, "the entry keeps its description")
check(sh.candidate.list_descriptions.get(prismash.list_entry_key(
    "ip prefix-list CGN-IN seq 5 deny 100.64.1.0/24")) == "management range",
      f"under its new key: {sh.candidate.list_descriptions!r}")
said(sh, "nat", "cgnat")
check(said(sh, "rename pool cgnat to cgnat-a").count("renamed in 1") == 1,
      "a pool, from its level")
check(set(sh.candidate.pools) == {"cgnat-a", "other"}, "the pool's name")
said(sh, "top", "router bgp 65000", "address-family ipv4 unicast")
out = said(sh, "rename peer-group PG to UPSTREAM")
check("neighbor 1.1.1.1 peer-group UPSTREAM" in sh.candidate.text()
      and "neighbor UPSTREAM route-map RM out" in sh.candidate.text(),
      f"from inside the address-family: {out!r}")
said(sh, "top")
said(sh, "rename interface enp1s0.801 to enp1s0.802")
text = sh.candidate.text()
check("interface enp1s0.802" in text and "enp1s0.801" not in text.replace(
    "uplink enp1s0.801", ""), "the interface, its stanza and its routes")
check("enp1s0.802" in sh.candidate.vlans, "the model's own dictionaries")
said(sh, "rename route-map RM to RM-OUT")
check("neighbor UPSTREAM route-map RM-OUT out" in sh.candidate.text(),
      "a route-map")
before = sh.candidate.text()
# Mutation shown red: the `taken` check removed - two pools named `other`
# reach the read-back, which refuses them as a merge instead of saying so.
for line, needle in (
        ("rename nat cgnat pool other to cgnat-a", "that name is taken"),
        ("rename router bgp 65000 peer-group UPSTREAM to 1.1.1.1",
         "that name is taken"),
        ("rename ip prefix-list NOPE to X", "no such name here"),
        ("rename nat nat64 pool other to x", "no such name here"),
        ("rename route-map RM-OUT to PO-X", "the PO- names belong"),
        ("rename interface enp1s0.802 to ens20", "keeps its parent"),
        ("rename interface loop0 to ae1", "a loopback is renamed"),
        ("rename policy-options prefix-list A to B", "not a name this VTY"),
        ("rename ip prefix-list CGN-IN", "not a whole statement"),
        ('rename ip prefix-list "CGN-IN" to X', "not a whole statement")):
    out = said(sh, line)
    check(needle in out, f"{line}: {out!r}")
check(sh.candidate.text() == before, "nothing left behind by any of them")
sh = shell(RUNNING + "router rip\n distribute-list prefix IN out\nexit\n")
out = said(sh, "rename ip prefix-list IN to A")
check("a reference this VTY cannot follow" in out
      and "distribute-list prefix IN out" in out, f"named: {out!r}")
ok("followed, or refused naming what it cannot follow; taken names refused")

print("\n=== a candidate that does not read back is not edited ===")
# The candidate from FRR with a static route the prompt's reader refuses.
sh = shell(RUNNING + "ip route 10.8.0.0/16 enp1s0.801\n")
before = sh.candidate.text()
out = said(sh, "replace pattern 512 with 1024")
check("does not read back as itself" in out and "10.8.0.0/16" in out,
      f"refused, naming it: {out!r}")
check(sh.candidate.text() == before, "and left as it was")
ok("the edit would lose the line, so it is refused")

print("\n=== a renamed pool is the pool the commit removes ===")
# Mutation shown red: the renamed_pool() skip taken out of the commit check -
# the dataplane, which still runs `cgnat`, refuses `cgnat-a` as a second pool
# on the same public addresses, and no pool could ever be renamed (measured
# in lab/replace-rename.sh before the skip existed).
run = Config.parse(RUNNING)
new = Config.parse(RUNNING.replace("pool cgnat", "pool cgnat-a"))
new.resolve_pool_lists()            # as _apply() resolves the target
check(cfgedit.renamed_pool(new.pools["cgnat-a"], run, ["cgnat"]) == "cgnat",
      "the same pool under another name")
new.pools["cgnat-a"].block_size = 1024
check(cfgedit.renamed_pool(new.pools["cgnat-a"], run, ["cgnat"]) is None,
      "a pool that also changed is a new pool")
check(cfgedit.renamed_pool(run.pools["other"], run, ["cgnat"]) is None,
      "another pool is not it")


class Collides(Dp):
    """A dataplane that refuses every pool `check` as overlapping a pool it
    runs, the answer it gives a renamed pool."""

    def run(self, cmd):
        self.commands.append(cmd)
        if cmd.endswith(" check"):
            return 1, ("bpa pool: public address 200.0.0.0 is already in "
                       "pool 'cgnat'.")
        return 0, ""


sh = shell()
sh.dp = Collides()
out = said(sh, "nat", "cgnat", "rename pool cgnat to cgnat-a", "top")
check("renamed in 1 place(s)" in out and "cgnat-a" in sh.candidate.pools,
      f"the rename passes the commit check: {out!r}")
check(not [c for c in sh.dp.commands if "cgnat-a" in c and "check" in c],
      "and the dataplane was not asked to check it again")
out = said(sh, "nat", "cgnat", "pool fresh", "prefix-inside 100.80.0.0/24",
           "prefix-outside 200.0.0.0/30", "top", "commit check")
check("pool 'fresh' would be refused" in out,
      f"a new pool is still checked: {out!r}")
ok("checked as the pool it was; a new pool is checked as before")

print("\n=== the parts, one at a time ===")
sh = shell()
check(sh.typed_refusal(sh.candidate, False, ["nat", "cgnat", "pool cgnat"],
                       "block-size 7")[0].startswith("invalid argument"),
      "a pool's statement, at its pool")
check(sh.typed_refusal(sh.candidate, True, [], "route-map RM permit 20")
      is None, "an FRR statement FRR takes")
cand = Config.parse(sh.candidate.text())
check(sh.edit_refusal(sh.candidate.text(), cand,
                      sh.candidate.text()) is None, "an unchanged result")
with contextlib.redirect_stdout(io.StringIO()):
    check(sh.stage_edit(sh.candidate.text(), "nothing", "replace", True),
          "a dry run of the same text passes")
sh.mode, sh.pool = "pool", prismash.Pool("gone")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.rebind_block()
check("no longer holds gone" in out.getvalue() and sh.mode == "config",
      f"a block the candidate lost: {out.getvalue()!r}")
ok("each part answers on its own")

print("\n=== completion: the forms, the names, the pipe ===")
sh = shell()
opts, _, _ = sh._options("", False)
check("replace" in opts and "rename" in opts, "both at the top")
opts, hint, _ = sh._options("replace pattern ", False)
check(hint == cfgedit.REPLACE_HINT, f"the form: {hint!r}")
opts, _, _ = sh._options("replace pattern a with b ", False)
# Mutation shown red: _token_takes() without the bare `|` - the pipe is not
# offered and `compare` never follows it.
check({"upto", "|", prismash.ENTER} <= set(opts), f"{opts!r}")
opts, _, _ = sh._options("replace pattern a with b | ", False)
check(set(opts) == {"compare"}, f"after the pipe: {opts!r}")
opts, hint, _ = sh._options("rename ip prefix-list ", False)
check("IN" in opts and hint.startswith("<prefix-list>"),
      f"a list's name completes: {opts!r} {hint!r}")
opts, _, _ = sh._options("rename router bgp 65000 peer-group ", False)
check("PG" in opts, f"a peer-group's: {opts!r}")
opts, _, _ = sh._options("rename ip prefix-list IN ", False)
check(set(opts) == {"to"}, f"then `to`: {opts!r}")
said(sh, "policy-options")
opts, _, _ = sh._options("", False)
check("replace" in opts and "rename" not in opts,
      "rename is not offered where it renames nothing")
said(sh, "top", "nat", "cgnat")
check(set(sh.edit_tree()["rename"][1]) == {"pool"}, "from cgnat, a pool")
ok("every form shown, names completed, `| compare` offered after a value")

print(f"\n{fail} FAILURES" if fail else "\nALL TESTS PASSED")
sys.exit(1 if fail else 0)
