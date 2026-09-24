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

  prisma> show route               operational mode
  prisma# [edit] interface Gi0/1   configuration mode, top level
  prisma# [edit interface Gi0/1]   the prompt alone: where you now are,
                                          not something anybody typed

The prompt is half the assertion. `prisma>` and `prisma# [edit]` are
what Shell.prompt() returns for the two modes, so a page that shows
`prisma# configure` is showing a transcript the box cannot produce:
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "prismash"))
import prismash                                                   # noqa: E402

fail = 0


def bad(msg):
    global fail
    fail += 1
    print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doc_paths                                     # noqa: E402

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
#: Everything that shows a transcript of this VTY. README.md is in here
#: because it is the first page anybody reads and it quotes the VTY too.
DOC_ROOTS = (os.path.join(REPO, "wiki"),
             os.path.join(REPO, "docs"),
             os.path.join(REPO, "README.md"))

# Interfaces for the fake dataplane, so a command naming one is walked against
# a box that has it rather than against a box with no interfaces at all. Both
# shapes: the cards an installed box names after their Linux netdevs, ens18
# and ens19, which is what the wiki's transcripts show since #389, and VPP's
# own spelling for a device nobody named, which docs/routing.md still shows.
IFACES = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)
ens18                             6      up          9000/0/0/0
ens19                             7      up          1500/0/0/0
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
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT}

    def run(_self, cmd):
        if cmd.startswith("show interface"):
            return 0, IFACES
        return 0, ""

    sh.dp = type("D", (), {"TRACE": False, "run": run,
                           "reachable": lambda _s: True})()
    # The cards LINUX has are a property of the MACHINE RUNNING THIS SUITE,
    # not of the product, so they are put out of reach here. Left alone, the
    # walk would resolve `dataplane interface <nic>` against the developer's
    # own /sys/class/net and refuse every name a page could possibly print -
    # a suite that passes or fails on whose laptop it ran. Out of reach, the
    # marker enumerates nothing and the walk stops at it, which is exactly
    # how `<pool>`, `<user>` and every other per-box argument are treated.
    sh.SYS_CLASS_NET = "/nonexistent/sys/class/net"
    sh.frr = type("F", (prismash.Frr,), {"available": lambda s: False,
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


# A page may show an ambiguous abbreviation ON PURPOSE, to show the refusal
# (#393). That line is excused only when the very next line of the block is
# the refusal itself, so a page that shows one by mistake still fails.
AMBIGUOUS_VERDICT = "error: an ambiguous abbreviation"
SHOWN_REFUSED = set()
# And a statement FRR refuses at the prompt, shown ON PURPOSE to show that
# refusal (#431), under the same rule: excused only when the very next line
# is the refusal, so a page that quotes a typo by mistake still fails. Only
# the walk is excused - the tree is a partial model of FRR's grammar, so a
# line it cannot walk is exactly what such a page is showing.
FRR_REFUSED_VERDICT = "error: not a statement FRR accepts here"
SHOWN_FRR_REFUSED = set()


def prompt_lines():
    """(path, mode, context, command) for every prompt line in the docs."""
    out = []
    if True:
        for path in markdown_files():
            text = open(path, encoding="utf-8").read()
            for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
                rows = block.split("\n")
                for i, line in enumerate(rows):
                    line = line.rstrip()
                    if (i + 1 < len(rows)
                            and rows[i + 1].startswith(AMBIGUOUS_VERDICT)):
                        SHOWN_REFUSED.add((path, line))
                    if (i + 1 < len(rows)
                            and rows[i + 1].startswith(FRR_REFUSED_VERDICT)):
                        SHOWN_FRR_REFUSED.add((path, line))
                    m = re.match(r"^prisma> (.+)$", line)
                    if m:
                        out.append((path, "operational", "", m.group(1).strip()))
                        continue
                    m = re.match(r"^prisma# \[edit(| [^\]]*)\](.*)$", line)
                    if m:
                        cmd = m.group(2).strip()
                        if cmd:
                            out.append((path, "configuration",
                                        m.group(1).strip(), cmd))
                        continue
                    # A prompt with something after it that is neither of the
                    # two shapes above. Worth naming rather than skipping:
                    # `prisma# configure` is exactly that, and it is the
                    # defect this half of the file exists for.
                    m = re.match(r"^prisma#\s+([a-z].*)$", line)
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
# `prisma>` is operational and `prisma# [edit]` is configuration.
# A command shown at a bare `prisma#` is a transcript the box cannot
# produce, and the one that matters is `configure`: at `#` you are already in
# configuration mode, where `configure` is not in the tree at all.
wrong_prompt = [(p, c) for p, mode, _, c in lines if mode == "BAD PROMPT"]
if not wrong_prompt:
    ok("no page shows a command at a prompt this VTY never prints")
else:
    for path, cmd in wrong_prompt:
        rel = os.path.relpath(path, REPO)
        bad(f"{rel} shows 'prisma# {cmd}'. This VTY prints "
            f"'prisma> ' in operational mode and 'prisma# [edit] ' "
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
        #
        # NESTED BLOCKS ARE ONE PROMPT WITH ` / ` BETWEEN THEM, and a prompt
        # longer than `Shell.PROMPT_MAX` keeps its END - so the page can show
        # `[edit ...r bgp 65000 / address-family ipv4 unicast]`, whose first
        # block this file cannot recover. It is read the way the prompt itself
        # is written: the innermost block is the one that decides where the
        # next statement lands, an elided head is dropped, and what is left is
        # entered block by block. Before this, the whole string was fed as ONE
        # line: a nested prompt put the shell nowhere, and every command shown
        # at one was walked against the top of the tree.
        blocks = [b.strip() for b in context.split(" / ")]
        if blocks and blocks[0].startswith("..."):
            blocks.pop(0)
        with contextlib.redirect_stdout(io.StringIO()):
            for block in blocks:
                sh.feed(block)
    # Read the line the way feed() reads it: an abbreviation of this VTY's
    # own words is spelled out first (#393), so `no shut` walks as `no
    # shutdown` and an ambiguous one is refused before anything is walked.
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        spelled = sh.spell_out(cmd)
    if spelled is None:
        shown_line = (f"prisma# [edit{' ' + context if context else ''}]"
                      f" {cmd}")
        if (path, shown_line) not in SHOWN_REFUSED:
            bad(f"{rel}: '{cmd}' ({mode}): an ambiguous abbreviation, and the "
                f"page does not show it refused")
        walked += 1
        continue
    good, why = walk(sh, spelled)
    walked += 1
    shown_line = f"prisma# [edit{' ' + context if context else ''}] {cmd}"
    if not good and (path, shown_line) not in SHOWN_FRR_REFUSED:
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
sys.path.insert(0, os.path.join(REPO, "src", "prismash"))
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
        _at = _line.index("<- not checked")
        # Only a QUOTED needle. A comment may mention the marker while
        # explaining why an assertion is written the way it is, and reading
        # prose as a needle made this check fail on a lab that was right -
        # which is the worst kind of check there is.
        if '"' not in _line[:_at] or '"' not in _line[_at:]:
            continue
        # The quoted needle has to be a prefix of what the box prints. A
        # longer one cannot match, which is the failure that happened.
        _quoted = _line[_at:].split('"')[0].rstrip()
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
# The page says of that table, in as many words, "essa tabela e o que o prismash
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
from prismash import BGP_NEIGHBOUR, BGP_AF_NEIGHBOUR                # noqa: E402

_bgp_page = os.path.join(REPO, "wiki", "referencia", "vty-prismash-guia-de-uso",
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
from prismash import CGNAT_POOL, INTERFACE_BLOCK                    # noqa: E402

_ref = os.path.join(REPO, "wiki", "referencia", "vty-prismash-guia-de-uso")
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
from prismash import SHOW                                           # noqa: E402

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

print("\n=== every commit refusal an operator can meet is on the commit page ===")
#
# Refusing is the strongest thing this box does: it knows the state of the box
# and the change at the same time, and there are cases where applying means
# losing the box. An operator who meets one of those messages needs somewhere
# to look it up, and the page they would look at said nothing about any of
# them.
#
# Read out of the source rather than listed here, so a refusal added to the
# code and to no page fails. Only the runs of at least 25 characters that carry
# no interpolation are demanded: shorter ones like "is not on this box" are too
# generic to be a useful needle, and demanding them would make this check go
# red on a page that is right.
_prismash = open(os.path.join(REPO, "src", "prismash", "prismash.py"),
               encoding="utf-8").read().split("\n")
_page = open(os.path.join(REPO, "wiki", "referencia", "vty-prismash-guia-de-uso",
                          "commit-rollback-e-save.md"), encoding="utf-8").read()
_wanted = []
for _i, _l in enumerate(_prismash):
    if "ok = False" not in _l:
        continue
    # Walk back to the message this refusal prints. A commit refusal is an
    # err() followed by ok = False; the two are never far apart.
    for _j in range(_i - 1, max(0, _i - 16), -1):
        if not (re.search(r"self\.err\(\s*f?\"", _prismash[_j])
                or re.search(r"said = \(\s*\"", _prismash[_j])):
            continue
        _text, _k = "", _j
        while _k < _i:
            for _q in re.findall(r'"([^"]*)"', _prismash[_k]):
                _text += _q
            if _prismash[_k].rstrip().endswith(")") and _text:
                break
            _k += 1
        _runs = [r.strip() for r in re.split(r"\{[^}]*\}", _text)
                 if len(r.strip()) >= 25]
        if _runs:
            _wanted.append(max(_runs, key=len))
        break
_wanted = list(dict.fromkeys(_wanted))
if len(_wanted) < 4:
    bad(f"only {len(_wanted)} commit refusal(s) were found in the source, so "
        f"this check is not reading what it thinks it is")
_undocumented = [w for w in _wanted if w not in _page]
if _undocumented:
    for _u in _undocumented:
        bad(f"commit can refuse with {_u!r} and commit-rollback-e-save.md does "
            f"not carry that sentence: an operator meeting it has nowhere to "
            f"look")
else:
    ok(f"all {len(_wanted)} of them are quoted on the page, in the words the "
       f"box prints")

print("\n=== a lab the documentation names is the lab that holds the claim ===")
# `docs/routing.md` attributed three sets of assertions to `lab/routing.sh` and
# all three were in `lab/bgp-policy.sh`, which #167 split out of it. A reader
# following the document ran the wrong lab, found nothing, and was left with
# two readings that are both wrong: that the claim is unheld, or that the lab
# lost it (#247). Naming which lab holds a claim is the one thing a document of
# this kind has to get exactly right, because it is what somebody reaches for
# when they want to know whether a behaviour is asserted or merely described.
#
# THE RULE IS NARROW ON PURPOSE, and the wider ones were measured and rejected.
# Requiring every backticked token near a reference to appear in the named lab
# gives 26 findings of which almost all are correct prose - a document
# legitimately names a command it explains, or another test file - and a check
# that goes red on work that is right is the worst kind there is. Requiring a
# fixture token to be in ONLY the named lab says nothing here, because the
# prefixes appear in several labs.
#
# What holds with no false positives at all: a paragraph that names exactly one
# lab and quotes a FIXTURE - an IPv4 prefix, an AS number, an UPPERCASE-HYPHEN
# policy name - is claiming that lab uses it, and that is checkable. Measured
# over every document: one finding, and it was one of the three defects.
LAB_SRC = {}
for _f in sorted(os.listdir(os.path.join(REPO, "lab"))):
    if _f.endswith(".sh"):
        LAB_SRC[_f[:-3]] = open(os.path.join(REPO, "lab", _f),
                                encoding="utf-8").read()

_FIXTURE = re.compile(
    r"`(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}|6[0-9]{4}|[A-Z][A-Z0-9]*-[A-Z0-9-]{2,})`")

_pairs = 0
_wrong = []
for _name in sorted(os.listdir(os.path.join(REPO, "docs"))) + ["../README.md",
                                                               "../TESTING.md"]:
    if not _name.endswith(".md"):
        continue
    _text = open(os.path.join(REPO, "docs", _name), encoding="utf-8").read()
    for _para in re.split(r"\n\s*\n", _text):
        _refs = set(re.findall(r"lab/([a-z0-9-]+)\.sh", _para))
        if len(_refs) != 1:
            continue
        _lab = _refs.pop()
        if _lab not in LAB_SRC:
            _wrong.append(f"{_name} names lab/{_lab}.sh, which does not exist")
            continue
        for _tok in sorted(set(_FIXTURE.findall(_para))):
            _pairs += 1
            if _tok not in LAB_SRC[_lab]:
                _owners = [k for k in sorted(LAB_SRC) if _tok in LAB_SRC[k]]
                _where = (", ".join("lab/" + o + ".sh" for o in _owners)
                          if _owners else "no lab at all")
                _wrong.append(
                    f"{_name} sends the reader to lab/{_lab}.sh for {_tok}, "
                    f"and that fixture is in {_where}")
if _wrong:
    for _w in _wrong:
        bad(_w)
elif _pairs >= 5:
    ok(f"{_pairs} fixture(s) quoted beside a lab, each present in that lab")
else:
    bad(f"only {_pairs} fixture-and-lab pair(s) were found, so this is "
        f"asserting almost nothing")

print("\n=== the wiki names no command from the dataplane's own CLI ===")
# THE DEFECT (#313). Two operator pages told the reader to run
# `show bpa pool history verify` - the dataplane's spelling, which no VTY
# command reached. One of them puts it at the last step before the disk is
# erased, with a "pare aqui" attached to its result, so an operator following
# the page to the letter gets a parser error at the moment they are told to
# stop if the answer is wrong.
#
# NARROW ON PURPOSE, and the width was measured rather than chosen. Walking
# every command in every wiki code block against the tree flags 12 of 193
# lines and all 12 are false: help-table rows with their prose on the same
# line (`commit    aplica`), output lines (`commit complete`), a command from
# the installer's own tree (`install`), and commands with an argument. That is
# the rate test/test_doc_copies.py's header records getting a check switched
# off.
#
# So this asks one question with no heuristics in it: on a line that is a
# command ALONE - no run of two spaces, which is what separates a command from
# the prose beside it in a help table - is the word after `show` one the VTY
# offers? Measured: 62 such lines, 0 unknown. The dataplane's `show bpa ...`
# fails it on the first word, which is the whole class this is for.
_SHOW = prismash.TREE["operational"]["show"][1]
_lines, _unknown = 0, {}
for _root, _dirs, _files in os.walk(os.path.join(REPO, "wiki")):
    for _f in _files:
        if not _f.endswith(".md"):
            continue
        _rel = os.path.relpath(os.path.join(_root, _f), REPO)
        _inblock = False
        with open(os.path.join(REPO, _rel), encoding="utf-8") as _fh:
            for _ln, _line in enumerate(_fh, 1):
                if _line.startswith("```"):
                    _inblock = not _inblock
                    continue
                if not _inblock:
                    continue
                _s = _line.strip()
                if _s.startswith("prisma>"):
                    _s = _s[len("prisma>"):].strip()
                if "  " in _s:
                    continue
                _w = _s.split()
                if len(_w) < 2 or _w[0] != "show":
                    continue
                _lines += 1
                if _w[1] not in _SHOW:
                    _unknown.setdefault(_s, []).append(f"{_rel}:{_ln}")
for _s, _where in sorted(_unknown.items()):
    bad(f"{_where[0]} tells an operator to run {_s!r} and this VTY has no "
        f"'show {_s.split()[1]}': the wiki is written for people who have the "
        f"VTY and not the dataplane's CLI")
if not _unknown:
    if _lines >= 40:
        ok(f"all {_lines} of them")
    else:
        bad(f"only {_lines} command line(s) were found in the wiki's code "
            f"blocks, so this check is reading almost nothing")

print("\n=== every path these documents send a reader to is in the tree ===")
# A POINTER, not a second copy of a truth, which is why this is sound where
# test/test_doc_copies.py's header rejects its own opposite. The holder is the
# filesystem: a path either resolves or it does not, and it cannot be reworded
# into passing. Nothing here reads what is at the other end.
#
# WHICH STRINGS COUNT AS A PATH is decided by test/doc_paths.py and not here,
# because test/test_changelog_landing.py asks the same question. Written out
# twice, the two immediately disagreed: the changelog copy did not know that
# `vnet/error.h` is VPP's header rather than ours, and went red on an entry
# whose whole subject was that distinction. The measurement behind the rule is
# in that file.
#
# CHANGELOG.md is excluded here and checked separately in
# test/test_changelog_landing.py, which looks only at the unreleased section.
# A tagged section describes a medium somebody is running and MUST NOT be
# edited, so a file renamed after it shipped leaves it naming something that
# is gone and nobody may correct it.
#
# AND A FILE THE BUILD MAKES IS NOT A FILE THAT IS GONE. The rule's whole
# subject is a document left behind by a rename or a removal, and a build
# product is neither: `test/bpa_vectors.h` is 21,072 lines generated by
# `ref/gen_vectors.py`, listed in .gitignore, removed by `make clean` and
# remade by `make test`'s own `vectors` prerequisite. A document naming it is
# right, and it names it correctly.
#
# Without this the verdict depended on WHETHER SOMEBODY HAD BUILT. Measured on
# 2026-09-15: green in the working tree, where a build had left the file
# behind, and red in a clone made the same minute:
#
#     $ git clone --no-local . /tmp/fresh && cd /tmp/fresh
#     FAIL: docs/roadmap.md sends a reader to 'test/bpa_vectors.h' and the
#           tree does not have it
#
# A clone is what tools/release-gate.sh builds a release from, and a check
# that goes red on correct work is one people learn to run around - which is
# the failure this repository has met from the other side more than once.
#
# READ FROM .gitignore, and from doc_paths rather than from a copy here. Both
# readers ask the same question and the first version of the path rule was
# written out twice and immediately disagreed; putting this half in the same
# holder is that lesson applied before it costs anything.
_seen, _dead, _built = 0, {}, {}
for _root, _dirs, _files in os.walk(REPO):
    _dirs[:] = [_d for _d in _dirs
                if _d not in {".git", ".build", ".build-vpp", ".build-old",
                              "build"}]
    for _f in _files:
        if not _f.endswith(".md"):
            continue
        _rel = os.path.relpath(os.path.join(_root, _f), REPO)
        if _rel == "CHANGELOG.md":
            continue
        with open(os.path.join(REPO, _rel), encoding="utf-8") as _fh:
            _text = _fh.read()
        for _path in doc_paths.repo_paths(_text):
            _seen += 1
            if not os.path.exists(os.path.join(REPO, _path)):
                if doc_paths.built(_path):
                    _built.setdefault(_path, set()).add(_rel)
                else:
                    _dead.setdefault(_path, set()).add(_rel)
if _dead:
    for _path, _where in sorted(_dead.items()):
        bad(f"{', '.join(sorted(_where))} sends a reader to {_path!r} and the "
            f"tree does not have it: the file was renamed or removed and the "
            f"document was left behind")
elif _seen >= 250:
    ok(f"all {_seen} of them")
    # SAID, not counted silently. These are the paths a document names that
    # the tree does not carry because the build makes them, and the only
    # reason they are not failures. If that list grows, or grows a name
    # nobody recognises as a build product, it is on the screen rather than
    # inside this file's logic.
    for _path, _where in sorted(_built.items()):
        print(f"  [info] {_path} is named by {', '.join(sorted(_where))} and "
              f"is a build product .gitignore lists, so it is absent in a "
              f"clone and that is not a stale document")
else:
    bad(f"only {_seen} path mention(s) were found, so this check is asserting "
        f"almost nothing: the pattern no longer matches how these documents "
        f"name a file")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
