#!/usr/bin/env python3
"""
Every command the documentation tells a reader to type, typed at the command
tree.

The wiki is where this product makes promises to somebody who does not have
the source, and `docs/` is where it makes them to somebody who does: a page
that shows a command the box does not have, or shows it at a prompt the box
would never show it at, is the product claiming something it does not do.

Both trees are read, and `docs/` is not the softer half: its transcripts are
what somebody reaches for when the wiki does not answer, and two of them
carried a prompt this VTY never prints. Nothing checked that, and `test_context_map.py` already makes the
same argument one level down for the paths `docs/context.md` names - a map
naming files that are no longer there is worse than no map, because it is
confidently wrong.

What is read is the PROMPT LINES inside fenced blocks, because those are the
lines a reader retypes. Output lines in the same block are not commands and
are left alone.

  hexarouting> show route                 operational mode
  hexarouting# [edit] interface Gi0/1     configuration mode, top level
  hexarouting# [edit interface Gi0/1]     the prompt alone: where you now are,
                                          not something anybody typed

The prompt is half the assertion. `hexarouting>` and `hexarouting# [edit]` are
what Shell.prompt() returns for the two modes, so a page that shows
`hexarouting# configure` is showing a transcript the box cannot produce:
`configure` is an operational command and `#` means you are already in
configuration mode, where it is not in the tree at all.

HOW DEEP THIS GOES, said plainly rather than implied: each token is looked up
in the options the tree offers at that point, and the walk STOPS at the first
place the tree offers nothing, because that is an argument - an address, an
interface name, a number - and this file has no way to know what is valid
there. So it catches a command that does not exist and a sub-command that does
not exist; it does not check arguments. That is the same boundary `?` and TAB
have at the real VTY.
"""
import contextlib
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import hexash                                                   # noqa: E402

fail = 0


def bad(msg):
    global fail
    fail += 1
    print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
#: Everything that shows a transcript of this VTY. README.md is in here
#: because it is the first page anybody reads and it quotes the VTY too.
DOC_ROOTS = (os.path.join(REPO, "wiki"),
             os.path.join(REPO, "docs"),
             os.path.join(REPO, "README.md"))

# Interfaces for the fake dataplane, so a command naming one is walked against
# a box that has it rather than against a box with no interfaces at all.
IFACES = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)
GigabitEthernet0/12/0             1      up          9000/0/0/0
TenGigabitEthernet0/0/0           2      up          9000/0/0/0
TenGigabitEthernet0/0/1           3      up          9000/0/0/0
TenGigabitEthernet0/8/0           4      up          9000/0/0/0
host-veth-vpp                     5      up          9000/0/0/0
local0                            0     down            0/0/0/0
"""


def shell(mode):
    """A Shell with a box behind it, in operational or configuration mode.

    The capabilities are set wide on purpose: the wiki documents the whole
    product, and a command missing because this test logged in as a read-only
    account would be a failure about the test rather than about the page.
    """
    sh = hexash.Shell()
    sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT}

    def run(_self, cmd):
        if cmd.startswith("show interface"):
            return 0, IFACES
        return 0, ""

    sh.dp = type("D", (), {"TRACE": False, "run": run,
                           "reachable": lambda _s: True})()
    sh.frr = type("F", (), {"available": lambda s: False,
                            "running": lambda s: [],
                            "show": lambda s, c: (1, "")})()
    if mode == "configuration":
        # Quietly: feed() prints "entering configuration mode", and a page of
        # that between the checks is how a real failure gets scrolled past.
        with contextlib.redirect_stdout(io.StringIO()):
            sh.feed("configure")
    return sh


# A token the reader is not meant to type literally. The walk stops here
# rather than reporting "<name> is not a command", which would be this file
# failing to read rather than the page being wrong.
PLACEHOLDER = re.compile(r"^(\?|<[^>]+>|\.\.\.|\[[^\]]+\])$")


def walk(sh, cmd):
    """Follow cmd through the command tree. Returns (ok, why)."""
    sofar = ""
    for tok in cmd.split():
        if PLACEHOLDER.match(tok):
            return True, f"stops at the placeholder {tok!r}"
        options, _, _ = sh._options(sofar, False)
        if not options:
            return True, f"free-form argument after {sofar.strip()!r}"
        if tok in options:
            sofar += tok + " "
            continue
        # Not offered. If it looks like a word it is a command that is not
        # there; anything else is an argument the tree does not enumerate,
        # which is not this file's business.
        if re.match(r"^[a-z][a-z0-9-]*$", tok):
            return False, (f"{tok!r} is not offered after {sofar.strip()!r}; "
                           f"the tree has {sorted(options)}")
        return True, f"argument {tok!r} after {sofar.strip()!r}"
    return True, "every word of it is in the tree"


# Commands the wiki shows that are deliberately absent from an installed box.
#
# The entry is a pair, and the second half is the point: the page has to SAY
# why, in the reader's language, or the exception is just a hole. `install`
# exists only while the box is running from the medium, because a command that
# overwrites the disk should not sit one TAB away from `interface` on a router
# in service, and the installation page says exactly that.
LIVE_MEDIUM_ONLY = {
    "install": "somente quando a máquina está rodando a partir do meio",
}


def markdown_files():
    """Every page that might quote this VTY, wiki and docs alike."""
    for entry in DOC_ROOTS:
        if os.path.isfile(entry):
            yield entry
            continue
        for root, _, files in os.walk(entry):
            for name in sorted(files):
                if name.endswith(".md"):
                    yield os.path.join(root, name)


def prompt_lines():
    """(path, mode, context, command) for every prompt line in the docs."""
    out = []
    if True:
        for path in markdown_files():
            text = open(path, encoding="utf-8").read()
            for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
                for line in block.split("\n"):
                    line = line.rstrip()
                    m = re.match(r"^hexarouting> (.+)$", line)
                    if m:
                        out.append((path, "operational", "", m.group(1).strip()))
                        continue
                    m = re.match(r"^hexarouting# \[edit(| [^\]]*)\](.*)$", line)
                    if m:
                        cmd = m.group(2).strip()
                        if cmd:
                            out.append((path, "configuration",
                                        m.group(1).strip(), cmd))
                        continue
                    # A prompt with something after it that is neither of the
                    # two shapes above. Worth naming rather than skipping:
                    # `hexarouting# configure` is exactly that, and it is the
                    # defect this half of the file exists for.
                    m = re.match(r"^hexarouting#\s+([a-z].*)$", line)
                    if m:
                        out.append((path, "BAD PROMPT", "", m.group(1).strip()))
    return out


lines = prompt_lines()

print("=== the wiki's prompt lines were found at all ===")
# Without this the whole file passes by reading nothing, which is the shape
# #151 spent an issue on: a check whose needle never matches is green for the
# same reason a correct box is.
if len(lines) >= 40:
    print(f"  [ ok ] {len(lines)} prompt line(s) across the wiki and docs")
else:
    bad(f"only {len(lines)} prompt line(s) found: the pattern has stopped "
        f"matching the pages, so this file is asserting nothing")

print("\n=== the prompt matches the mode the command lives in ===")
# `hexarouting>` is operational and `hexarouting# [edit]` is configuration.
# A command shown at a bare `hexarouting#` is a transcript the box cannot
# produce, and the one that matters is `configure`: at `#` you are already in
# configuration mode, where `configure` is not in the tree at all.
wrong_prompt = [(p, c) for p, mode, _, c in lines if mode == "BAD PROMPT"]
if not wrong_prompt:
    ok("no page shows a command at a prompt this VTY never prints")
else:
    for path, cmd in wrong_prompt:
        rel = os.path.relpath(path, REPO)
        bad(f"{rel} shows 'hexarouting# {cmd}'. This VTY prints "
            f"'hexarouting> ' in operational mode and 'hexarouting# [edit] ' "
            f"in configuration mode, so that line is a transcript no box "
            f"produces")

print("\n=== every command shown is in the command tree ===")
walked = 0
for path, mode, context, cmd in lines:
    if mode == "BAD PROMPT":
        continue
    rel = os.path.relpath(path, REPO)
    first = cmd.split()[0] if cmd.split() else ""
    if first in LIVE_MEDIUM_ONLY:
        continue
    sh = shell(mode)
    if context:
        # `[edit interface Gi0/1]` means the reader is inside that block, so
        # the shell has to be put there before the command is looked up.
        with contextlib.redirect_stdout(io.StringIO()):
            sh.feed(context)
    good, why = walk(sh, cmd)
    walked += 1
    if not good:
        bad(f"{rel}: '{cmd}' ({mode}): {why}")
if walked:
    ok(f"{walked} command(s) walked, each as far as the tree enumerates")
else:
    bad("no command was walked at all")

print("\n=== a command the box only has on the medium says so on its page ===")
# The exception cannot be allowed to rot into a hole. If `install` is ever
# removed from the wiki, or the page stops explaining why it is not on an
# installed box, this says so instead of silently excusing the command.
for cmd, must_say in LIVE_MEDIUM_ONLY.items():
    shown = [p for p, _, _, c in lines if c.split()[:1] == [cmd]]
    if not shown:
        bad(f"'{cmd}' is excused here and no wiki page shows it any more: "
            f"take the exception out rather than leaving it")
        continue
    said = [p for p in shown
            if must_say in open(p, encoding="utf-8").read()]
    if said:
        ok(f"'{cmd}' is shown, and the page says why an installed box "
           f"does not have it")
    else:
        bad(f"'{cmd}' is shown at {os.path.relpath(shown[0], REPO)} and no "
            f"page explains that it exists only on the installation medium: "
            f"a reader on a running box will look for it and not find it")

print("\n=== every quotation of the unchecked marker is the one the box emits ===")
#
# The marker text changed from "    <- not checked by this VTY" to
# "  <- not checked" when the long form was measured putting a real statement
# at 89 columns on an 80-column console. Three places kept quoting the old one:
# a wiki page, a docs line, and an assertion in lab/bgp-policy.sh that had been
# green for three days and went red the first time anything ran it.
#
# A literal the product emits and other files quote is exactly the shape
# test_context_map.py guards for paths: confidently wrong, and silent until
# somebody meets it. Labs are the ones that matter, because a lab quoting a
# marker the box no longer prints is a check that cannot pass.
sys.path.insert(0, os.path.join(REPO, "src", "hexash"))
from config import UNCHECKED_MARK                                # noqa: E402

_mark = UNCHECKED_MARK.strip()
_quoters = []
for _root, _dirs, _files in os.walk(os.path.join(REPO, "lab")):
    for _f in sorted(_files):
        if _f.endswith(".sh"):
            _quoters.append(os.path.join(_root, _f))
_bad = []
for _path in _quoters:
    for _n, _line in enumerate(open(_path, encoding="utf-8"), 1):
        if "<- not checked" not in _line:
            continue
        # The quoted needle has to be a prefix of what the box prints. A
        # longer one cannot match, which is the failure that happened.
        _quoted = _line[_line.index("<- not checked"):].split('"')[0].rstrip()
        if not _mark.startswith(_quoted):
            _bad.append(f"{os.path.relpath(_path, REPO)}:{_n} quotes "
                        f"{_quoted!r}, and the box prints {_mark!r}")
if _bad:
    for _b in _bad:
        bad(_b)
else:
    ok(f"{len(_quoters)} lab(s) checked; every quotation matches {_mark!r}")

print("\n=== the BGP page's table is what the VTY actually checks ===")
#
# The page says of that table, in as many words, "essa tabela e o que o hexash
# confere" - and then explains that what is NOT in it is passed through unread
# and comes back marked `<- not checked`. So the table is a claim about the
# grammar, and a reader uses it to predict what their own `show this` will
# look like.
#
# It had drifted: `capability` was in the grammar and not on the page, and so
# were `maximum-prefix` and `next-hop-self` inside an address family. Nothing
# on the page claimed a statement the box lacks, which is the safer direction,
# but the sentence "this table is what it checks" was false either way and an
# operator would have expected a marker that does not appear.
from hexash import BGP_NEIGHBOUR, BGP_AF_NEIGHBOUR                # noqa: E402

_bgp_page = os.path.join(REPO, "wiki", "referencia", "vty-hexash-guia-de-uso",
                         "configuracao-de-bgp.md")
_text = open(_bgp_page, encoding="utf-8").read()
# The first word of the first cell of each row: the page writes
# `capability extended-nexthop` as one command, and the grammar keys on the
# first word.
_rows = set()
for _line in _text.split("\n"):
    if not _line.startswith("| `"):
        continue
    _cell = _line.split("|")[1].strip()
    if _cell.startswith("`"):
        _rows.add(_cell.strip("`").split()[0])
_missing = sorted(set(BGP_NEIGHBOUR) - _rows)
_extra = sorted(_rows - set(BGP_NEIGHBOUR) - {"remote-as"})
if _missing:
    bad(f"the neighbour table does not list {_missing}, which the VTY checks: "
        f"a reader expects those to come back marked and they do not")
else:
    ok(f"every one of the {len(BGP_NEIGHBOUR)} neighbour statements is on the page")

# The address-family list is prose rather than a table, so it is read as prose.
_af_line = [l for l in _text.split("\n")
            if l.startswith("Dentro de `address-family`")]
if not _af_line:
    bad("the address-family sentence is gone from the page, so nothing here "
        "is being compared")
else:
    _af_missing = sorted(k for k in BGP_AF_NEIGHBOUR if f"`{k}" not in _af_line[0])
    if _af_missing:
        bad(f"the address-family sentence does not name {_af_missing}, which "
            f"the VTY checks inside an address family")
    else:
        ok(f"and all {len(BGP_AF_NEIGHBOUR)} of the address-family ones are named")

print("\n=== every statement a block takes is named on that block's page ===")
#
# One step earlier than #221 and #224, which were both a thing the box does
# that an operator cannot find. A statement that reaches the grammar and no
# page is the same defect at the moment it is introduced: the box grows a
# feature nobody can discover, and the only way to learn it exists is to read
# the source.
#
# Deliberately weak: it asks whether the page NAMES the statement, not whether
# it explains it well. The pages are laid out differently - the pool page puts
# `alg` and `timeout` in sub-tables keyed by protocol - and a guard that
# demanded one shape would fail on prose that is perfectly clear. What it
# catches is the statement nobody wrote about at all.
from hexash import CGNAT_POOL, INTERFACE_BLOCK                    # noqa: E402

_ref = os.path.join(REPO, "wiki", "referencia", "vty-hexash-guia-de-uso")
for _page, _grammar, _what in (
        ("configuracao-de-pool.md", CGNAT_POOL, "a CGNAT pool"),
        ("configuracao-de-interface.md", INTERFACE_BLOCK, "an interface")):
    _path = os.path.join(_ref, _page)
    if not os.path.exists(_path):
        bad(f"{_page} is gone, so nothing here is comparing {_what}'s grammar "
            f"against anything")
        continue
    _text = open(_path, encoding="utf-8").read()
    # `exit`, `show` and `no` are in every block and are documented once, on
    # the pages about moving around and about removing things.
    _keys = sorted(set(_grammar) - {"exit", "show", "no"})
    _unnamed = [k for k in _keys if f"`{k}" not in _text]
    if _unnamed:
        bad(f"{_page} never names {_unnamed}, which {_what} takes: a statement "
            f"in the grammar and on no page can only be found by reading the "
            f"source")
    else:
        ok(f"all {len(_keys)} statements {_what} takes are named on {_page}")

print("\n=== every show command is named somewhere in the wiki ===")
#
# The same argument as the block statements above, for the commands an
# operator runs while something is wrong. Four were in the grammar and on no
# page at all: `show ospf neighbor`, `show ospf interface`, `show isis
# adjacency` and `show isis interface` - and those four carry the box's
# strongest claim, which is the comparison between what the routing daemon
# holds and what the table that forwards actually has.
#
# Read at two levels and no deeper. A command's ARGUMENTS are written the way
# a reader meets them - the utilisation page writes
# `show interface utilization [<nome>] [interval <segundos>] [count <n>]`,
# which names `interval` and `count` perfectly well without ever writing them
# as a command. Demanding the leaf string would go red on a page that is
# right, which is how a check gets switched off.
from hexash import SHOW                                           # noqa: E402

_wiki_text = ""
for _root, _dirs, _files in os.walk(os.path.join(REPO, "wiki")):
    for _f in sorted(_files):
        if _f.endswith(".md"):
            _wiki_text += open(os.path.join(_root, _f), encoding="utf-8").read()
_named = []
for _k, _v in SHOW.items():
    _d, _sub = _v if isinstance(_v, tuple) else (None, None)
    if isinstance(_sub, dict):
        for _k2 in _sub:
            if _k2.startswith("<"):
                continue            # an argument, not a sub-command
            _named.append(f"show {_k} {_k2}")
    else:
        _named.append(f"show {_k}")
_unnamed = sorted(n for n in _named if n not in _wiki_text)
if _unnamed:
    bad(f"the wiki never names {_unnamed}: a command an operator runs while "
        f"something is wrong, that they can only find by pressing '?' at the "
        f"right moment")
else:
    ok(f"all {len(_named)} show commands are named in the wiki")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
