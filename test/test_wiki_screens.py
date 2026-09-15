#!/usr/bin/env python3
"""
The screens the PUBLIC wiki quotes, read against the box that prints them.

`wiki/` quotes what the box says, verbatim, inside fenced blocks - 61 of them
carry a verdict marker (`error:`, `warning:`, `[ ok ]`, `[warn]`, `[FAIL]`,
`[info]`). Until now nothing compared one of them to the product. The sweep of
2026-09-15 found ten stale by hand, one page at a time, and eight were stale
the same way: a refusal that the product had split into rows of its own was
still quoted as one long sentence, with a paragraph beside it telling the
operator that the terminal would fold it and to look for the beginning of the
sentence. That paragraph was true when it was written, and by then it sent the
reader looking for a fold that no longer happens.

A page that quotes a screen nobody produced is worse than a page with no
screen on it, because it is read as measurement.

WHY THIS IS NOT IN test_wiki_rules.py. That file's three readers each answer
"does this text appear anywhere on a page" over the page as a string. This one
has to build a corpus out of the product's own sources, drive the VTY, and
fold text the way the console folds it. Sharing a file would put 400 lines of
apparatus under three rules that need none of it.

## A matcher with wildcards is the classic check that cannot fail

Most of these screens interpolate a value - a pool name, a neighbour, a count.
`error: neighbour 200.150.0.2 does not keep what it received` is built from
`f"neighbour {w[2]} does not keep what it received"`, so the comparison is
against a TEMPLATE. Turn `{...}` into `.*` and require nothing else and every
wiki line matches something: the check goes green over any page anybody ever
writes. Six things are done about that, and they are the reason this file is
worth its runtime:

  1. DRIVING BEATS MATCHING. A screen that can be produced by running the
     product is produced and compared character for character, no wildcard
     involved. The run is reported separately from the matched one so the
     proportion is visible on the screen.
  2. ANCHORED. A template must account for the WHOLE row. Matching a piece of
     a row would let one short template attribute every long line on the page.
     A row this product builds in two goes may be accounted for by two strings
     end to end, and then the same rule is taken over the pair: see
     `concat_match`, which is the one loosening in this file and carries its
     own guards, both of them measured against a screen it must still refuse.
  3. THE LITERAL HALF HAS TO BE THERE. Every literal segment between the
     wildcards, in order, and together they must be at least LITERAL_SHARE of
     the row's characters. A row that is mostly wildcard is not attributed.
  4. A TEMPLATE THAT IS MOSTLY HOLE NEVER ENTERS THE CORPUS. The statement
     row `say_finding` ends on is four spaces and a value, and as a matcher
     that is the empty rule: it accepts every row anybody will ever write. So
     templates with fewer than LITERAL_FLOOR literal characters are refused
     entry and COUNTED - the count is printed, because a corpus that quietly
     shrank is the other way this goes vacuous.
  5. A WILDCARD NEVER CROSSES A ROW. Rows are matched one at a time. A
     paragraph is reconstructed from several rows only to hand it straight
     back to the product's own folder: `console_prose` must reproduce the
     page's rows from it, character for character, at the page's own indent.
     THIS is the constraint that catches the eight, and it is why the fold has
     to be done rather than assumed - a template written on one source line is
     several rows on a console of `config.CONSOLE_COLUMNS`.
  6. THE LOOSEST MATCH IS PRINTED. How many holes the loosest accepted match
     carried, what share of the row was literal, and which source line it came
     from, all go on the screen. A reader who sees "4 hole(s), 63% literal"
     knows what this file was worth that day without reading it.

## What it refuses on

Two shapes, and both are a page that half agrees with a message.

A row that STARTS with a row the box prints AND GOES ON - the page's sentence
carrying past the point where the product's line ends, with a comma or a colon
where the box had a line break. That is exactly what an unfolded refusal looks
like from here, and it is a fact about the page rather than a guess: the
literal prefix is the product's own string.

A row that AGREES WITH ONE FOR THIRTY CHARACTERS AND THEN SAYS SOMETHING ELSE.
That is a word edited inside a quoted refusal - a screen repaired by hand
instead of re-read off the box. Thirty characters because two or three words
agreeing is a coincidence between unrelated messages and a whole opening
clause is not; under a verdict row, this is only read once the verdict itself
has been recognised as the box's, so a screen nothing can attribute stays a
skip rather than becoming a finding about the wrong message.

## What it cannot see, and says so

Everything else is a SKIP, printed with its page and line. A screen built
through a variable, a table the dataplane formats, a shell helper this cannot
follow: those are named on the screen rather than counted as green. The skip
list is the honest edge of this check and it belongs where the next person
sees it. A value in the WRONG PLACE is the other blind spot - the wiki's
forward refusal named public addresses in the positions that carry private
ones, and to this file those are both just a wildcard. Only driving sees that.
"""
import contextlib
import io
import os
import re
import sys

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(REPO, "src", "hexash"))
import hexash                                                    # noqa: E402
import config as _config                                         # noqa: E402

# The tree to read. `HEXA_WIKI` exists for one reason and it is the reason
# this file is trusted: pointed at the pages as they were before the sweep of
# 2026-09-15, it names the stale screens out loud. A check that has only ever
# been seen green has not been seen.
WIKI = os.environ.get("HEXA_WIKI") or os.path.join(REPO, "wiki")

# A hole in a template: an interpolated value, whatever the language wrote it
# with. One character so it can never be confused with the literal text around
# it, and so literal share is a subtraction.
HOLE = "\x00"

# The numbers the vacuity argument rests on. LITERAL_FLOOR keeps a template
# that is nearly all hole out of the corpus; LITERAL_SHARE is how much of the
# ROW the literal halves have to account for; PREFIX_FLOOR is how much of a
# message a page has to carry before this file will say the page went on past
# it.
#
# MEASURED, over the wiki as it stands and the wiki as it was before the sweep
# of 2026-09-15, which is the only way to choose one of these. At 0.70 the
# self-test's `[ ok ] rx queues requested: num-rx-queues 4` stops being
# attributable and this file REFUSES a screen the box really prints. At 0.40
# three more screens are attributed and the five stale ones are still all
# found, so the share buys nothing there except a looser bound on how loose an
# accepted match may be - and that bound is the whole argument. 0.55 is the
# tighter of the two that cost nothing.
LITERAL_FLOOR = 12
LITERAL_SHARE = 0.55
PREFIX_FLOOR = 16
TAIL_FLOOR = 8

# Every verdict this product prints, surveyed rather than remembered: the VTY
# says `error:`, `warning:` and `note:`; the boot self-test and the installer
# say `  [ ok ] `, `  [warn] `, `  [FAIL] ` and `  [info] `, the last of those
# with no helper behind it; the plugin says `warning:` and one `note:`. There
# is no `[skip]` and no `[ .. ]` anywhere in the tree - and the tuner's
# `in-effect` / `cannot-tell` / `no-reader` words are not verdicts at all,
# they are a machine-readable answer the self-test turns INTO one, which is
# why the rows quoted on the wiki carry the self-test's marker and not those.
MARKERS = ("error:", "warning:", "note:",
           "[ ok ]", "[warn]", "[FAIL]", "[info]")
MARKED = re.compile(r"^\s*(" + "|".join(re.escape(m) for m in MARKERS) + r")")

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


# ---------------------------------------------------------------------------
# What the wiki quotes.

def pages():
    """Every published page, the way test_wiki_rules.py reads them."""
    out = []
    for dirpath, _dirs, files in os.walk(WIKI):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(
                path, os.path.dirname(WIKI)).replace(os.sep, "/")
            if rel.endswith("/README.md"):
                continue
            out.append((rel, io.open(path, encoding="utf-8").read()))
    return sorted(out)


def indent_of(row):
    return len(row) - len(row.lstrip(" "))


def screens(page_list):
    """
    Every quoted screen that carries a verdict, as (page, line, rows).

    A screen is a marked row and the rows printed under it: everything
    indented FURTHER than the marker, up to a blank row or the next marker.
    That is the shape every one of these printers has - a verdict on its own
    row and the reason under it - so the grouping is the product's, not a
    guess about the page.
    """
    out = []
    for rel, text in page_list:
        lines = text.split("\n")
        inside, block, first = False, [], 0
        for n, line in enumerate(lines, 1):
            if line.strip().startswith("```"):
                if inside:
                    out.extend(screens_in(rel, first, block))
                    inside, block = False, []
                else:
                    inside, block, first = True, [], n
            elif inside:
                block.append(line)
    return out


def screens_in(rel, first, block):
    out = []
    n = 0
    while n < len(block):
        row = block[n]
        if not MARKED.match(row):
            n += 1
            continue
        rows, base, k = [row], indent_of(row), n + 1
        while k < len(block):
            nxt = block[k]
            if not nxt.strip() or indent_of(nxt) <= base or MARKED.match(nxt):
                break
            rows.append(nxt)
            k += 1
        out.append((rel, first + n + 1, rows))
        n = k
    return out


PAGES = pages()
SCREENS = screens(PAGES)
check(len(PAGES) >= 30, f"only {len(PAGES)} page(s) were read out of {WIKI}")
check(len(SCREENS) >= 50,
      f"only {len(SCREENS)} quoted screen(s) were found on {len(PAGES)} "
      f"pages, so whatever this run reports it is not reporting on the wiki")


# ---------------------------------------------------------------------------
# What the product builds: the templates, out of the sources that print them.

class Template:
    """One string the product builds, with a HOLE where a value goes."""

    def __init__(self, text, origin):
        self.text = text
        self.origin = origin
        self.holes = text.count(HOLE)
        self.literal = len(text) - self.holes
        # A hole matches within ONE row and never nothing: an interpolated
        # value that came out empty is a different screen from this one, and
        # letting a hole match the empty string is how `f"{a}{b}"` starts
        # attributing every row on the page.
        self.rx = re.compile("".join(
            re.escape(part) if i % 2 == 0 else "[^\n]+?"
            for i, part in enumerate(
                re.split("(" + re.escape(HOLE) + ")", text))))

    def match(self, row):
        """The whole row, or nothing. Anchored - see the docstring, point 2."""
        m = self.rx.fullmatch(row)
        if not m or not row:
            return None
        if self.literal < LITERAL_SHARE * len(row):
            return None
        return self

    def prefix_of(self, row):
        """
        Where this template STOPS inside a longer row, or None.

        The first of the two findings: the page's sentence carries on past the
        end of the product's own line. `re.match` rather than `fullmatch`, and
        what is left has to read as the same sentence continuing, so a
        template that merely happens to be a prefix of an unrelated row is not
        a finding.
        """
        m = self.rx.match(row)
        if not m or m.end() >= len(row):
            return None
        rest = row[m.end():]
        # The page's sentence CONTINUES where the product's row stopped: a
        # comma, a colon, a semicolon, a dash. That is what an unfolded
        # refusal looks like, because the row the product ends is a clause and
        # the page glued the next one onto it. A remainder that just starts
        # another word is not this finding - `block-stride ` is a grammar word
        # in the plugin's CLI and it is the head of a warning nobody wrote
        # wrong. PREFIX_FLOOR sits between the two: the shortest verdict of
        # the ten found by hand, `FRR is not answering`, is 20 characters and
        # that grammar word is 13.
        if self.literal < PREFIX_FLOOR:
            return None
        if not re.match(r"[,;:]|\.\s|\s-\s", rest):
            return None
        return rest


# Printed whole, folded to the console, and said after `error: `.
ROWS, PROSE, ERRS = [], [], []
TOO_LOOSE = []                          # refused entry: nearly all hole
LOOSE = []                              # the same, as templates


def keep(bucket, text, origin):
    """
    A template enters the corpus only if it says something.

    `print(f"    {stmt}")` is a real line of this product and as a matcher it
    is the empty rule - it accepts every row anybody ever writes. Counting
    what was refused is part of the report, because a corpus that quietly
    emptied itself reads exactly like a corpus that found everything.

    Kept in `LOOSE` rather than thrown away: a string too short to attribute a
    row on its own can still be the TAIL of one, under the pair rule in
    `concat_match` and under that rule's own floor.
    """
    t = Template(text.rstrip("\n"), origin)
    if t.literal < LITERAL_FLOOR:
        TOO_LOOSE.append((origin, text.replace(HOLE, "{}")))
        LOOSE.append(t)
        return
    bucket.append(t)


# --- Python: the VTY -------------------------------------------------------
#
# Read with `ast` rather than grepped, because these messages are written as
# adjacent string literals across four or five source lines and the parser is
# what joins them - a regular expression over the file would compare the wiki
# against a fifth of each sentence. Docstrings are excluded deliberately: this
# file's own docstring quotes a refusal, and so does `err_dataplane`'s. A
# quoted example in a comment is not a string the product builds, and a corpus
# that cannot tell the two apart would attribute a wiki screen to the prose
# that describes it.
import ast                                                       # noqa: E402

PY_SOURCES = [os.path.join(REPO, "src", "hexash", n)
              for n in sorted(os.listdir(os.path.join(REPO, "src", "hexash")))
              if n.endswith(".py")]
PY_SOURCES.append(os.path.join(REPO, "appliance", "hexarouting-tune"))


def py_template(node):
    """A literal or an f-string as a template, or None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = ""
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out += part.value
            else:
                out += HOLE
        return out
    return None


def py_docstrings(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr):
                first = body[0].value
                if isinstance(first, ast.Constant) and isinstance(first.value,
                                                                 str):
                    out.add(id(first))
    return out


def py_strings(node):
    """Every template inside a container the message tables are written as."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [py_template(e) for e in node.elts]
    return [py_template(node)]


for path in PY_SOURCES:
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    docs = py_docstrings(tree)
    where = os.path.relpath(path, REPO)
    for node in ast.walk(tree):
        # The message tables: a tuple of strings assigned to a CONSTANT is a
        # refusal written down away from the call that prints it, and it is
        # the headline first and the paragraphs after - INTERFACE_NO_REFUSED
        # is the shape and `no vlan` is the screen it prints.
        if isinstance(node, ast.Assign):
            for value in ast.walk(node.value):
                if isinstance(value, ast.Tuple):
                    parts = [py_template(e) for e in value.elts]
                    if len(parts) >= 2 and all(p is not None for p in parts):
                        at = f"{where}:{value.lineno}"
                        keep(ERRS, parts[0], at)
                        for rest in parts[1:]:
                            keep(PROSE, rest, at)
            continue
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        args = [a for a in node.args if not isinstance(a, ast.Starred)]
        origin = f"{where}:{node.lineno}"
        if name in ("err", "err_dataplane") and args:
            for text in py_strings(args[0]):
                if text is not None:
                    keep(ERRS, text, origin)
        elif name == "err_prose" and args:
            for text in py_strings(args[0]):
                if text is not None:
                    keep(ERRS, text, origin)
            for arg in args[1:]:
                for text in py_strings(arg):
                    if text is not None:
                        keep(PROSE, text, origin)
        elif name in ("print_prose", "console_prose") and args:
            for text in py_strings(args[0]):
                if text is not None:
                    keep(PROSE, text, origin)
        elif name == "say_finding" and len(args) >= 3:
            for text in py_strings(args[2]):
                if text is not None:
                    keep(PROSE, text, origin)
        elif name == "print" and args:
            for text in py_strings(args[0]):
                if text is not None:
                    for row in text.split("\n"):
                        keep(ROWS, row, origin)
    # And every other string these sources build. The pass above follows the
    # call that PRINTS a message, and most of the VTY's findings are not
    # printed where they are written: `route_map_findings` and the eight
    # functions beside it RETURN (severity, statement, reason) and a caller
    # hands the reason to `say_finding` a thousand lines away. Following that
    # by hand is a list to keep up to date, and a list nobody updates is the
    # rule with no reader this whole file exists because of.
    #
    # So the corpus is every string literal in the product's own sources,
    # docstrings excluded - a docstring quotes screens rather than printing
    # them, this file's own does, and a corpus that cannot tell those apart
    # would attribute a wiki screen to the paragraph describing it. The
    # widening is safe exactly because of the constraints on the MATCH: a row
    # is still accounted for whole, by literal text that is most of it, folded
    # the way this box folds.
    inner = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in ast.walk(node):
                if part is not node:
                    inner.add(id(part))
    for node in ast.walk(tree):
        if id(node) in docs or id(node) in inner:
            continue
        text = py_template(node) if isinstance(
            node, (ast.Constant, ast.JoinedStr)) else None
        if text is None:
            continue
        for row in text.split("\n"):
            keep(PROSE, row, f"{where}:{node.lineno}")
            keep(ROWS, row, f"{where}:{node.lineno}")


# --- the shell: the boot self-test and the installer -----------------------
#
# `ok`/`warn`/`bad` are one-liners that prepend the marker and nothing else,
# and `say_list` is the only thing on that side that folds - through `fold -s
# -w $((79 - indent))`, which is NOT textwrap: it breaks after the last blank
# that fits and the caller strips the blank off the end. Emulated here rather
# than approximated, because the whole point of this file is that a fold which
# is nearly right is what put ten screens on the wiki that nobody produced.

SHELL_SOURCES = ["appliance/selftest", "appliance/hexarouting-install",
                 "appliance/bind-dataplane-nic", "appliance/wait-for-vpp",
                 "appliance/apply-tuning"]

SHELL_MARK = {"ok": "  [ ok ] ", "_vok": "  [ ok ] ",
              "bad": "  [FAIL] ", "_vbad": "  [FAIL] ",
              "warn": "  [warn] ", "say": ""}

SPROSE = []                             # (indent, Template), folded by fold -s


def fold_s(text, width):
    """`fold -s -w <width>`, which is not `textwrap.wrap`.

    It breaks after the LAST blank that fits, keeps that blank at the end of
    the row - `say_list` strips it - and cuts mid-token when no blank fits.
    """
    rows = []
    while len(text) > width:
        cut = text.rfind(" ", 0, width + 1)
        if cut <= 0:
            rows.append(text[:width])
            text = text[width:]
        else:
            rows.append(text[:cut + 1])
            text = text[cut + 1:]
    rows.append(text)
    return [r[:-1] if r.endswith(" ") else r for r in rows]


def sh_unquote(rest):
    """The text a shell call prints, with HOLE where a value goes.

    Returns every reading of it: `${x:-default}` prints one of two things and
    both are screens this box produces, and the wiki quotes both of them for
    the rx-queue line.
    """
    out = []
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'', rest):
        out.append(m.group(1) if m.group(1) is not None else m.group(2))
    text = "".join(out).replace('\\"', '"').replace("\\$", "$")
    readings = [text]
    for _ in range(4):
        grown = []
        for one in readings:
            m = re.search(r"\$\{(\w+):-([^{}]*)\}", one)
            if m:
                grown.append(one[:m.start()] + HOLE + one[m.end():])
                grown.append(one[:m.start()] + m.group(2) + one[m.end():])
            else:
                grown.append(one)
        readings = grown
    return [re.sub(r"\$\{[^{}]*\}|\$\([^()]*\)|\$\w+|`[^`]*`", HOLE, r)
            for r in readings]


for rel in SHELL_SOURCES:
    path = os.path.join(REPO, rel)
    if not os.path.exists(path):
        continue
    lines = io.open(path, encoding="utf-8").read().split("\n")
    n = 0
    while n < len(lines):
        line, first = lines[n], n
        # A message wider than the source line is written across several of
        # them, so the call is read until its quotes close.
        while line.count('"') % 2 and n + 1 < len(lines):
            n += 1
            line += "\n" + lines[n]
        n += 1
        m = re.match(r"^\s*(ok|bad|warn|say|say_list|_vok|_vbad)\s+(\S.*)$",
                     line, re.S)
        if not m:
            continue
        verb, rest = m.group(1), m.group(2)
        origin = f"{rel}:{first + 1}"
        if verb == "say_list":
            ind = re.match(r'\s*"(\s*)"', rest)
            if not ind:
                continue
            for text in sh_unquote(rest[ind.end():]):
                t = Template(text, origin)
                if t.literal >= LITERAL_FLOOR:
                    SPROSE.append((len(ind.group(1)), t))
                else:
                    TOO_LOOSE.append((origin, text.replace(HOLE, "{}")))
            continue
        for text in sh_unquote(rest):
            for row in (SHELL_MARK[verb] + text).split("\n"):
                keep(ROWS, row, origin)


# --- the dataplane ---------------------------------------------------------
#
# A handful of refusals and all three CGNAT warnings are printed by the plugin
# in C, where the interpolation is `%u`/`%v`/`%U` instead of a brace. The runs
# of adjacent literals are joined the way the C compiler joins them, for the
# same reason the Python side is read with `ast`.

for name in sorted(os.listdir(os.path.join(REPO, "src", "plugins", "bpa"))):
    if not name.endswith(".c"):
        continue
    rel = f"src/plugins/bpa/{name}"
    src = io.open(os.path.join(REPO, rel), encoding="utf-8").read()
    for m in re.finditer(r'(?:"(?:[^"\\]|\\.)*"\s*)+', src):
        run = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(0)))
        text = re.sub(r"%[-0-9.*]*[a-zA-Z]", HOLE, run)
        text = text.replace("\\t", "\t").replace('\\"', '"')
        origin = f"{rel}:{src[:m.start()].count(chr(10)) + 1}"
        for row in text.split("\\n"):
            keep(ROWS, row, origin)
            keep(PROSE, row, origin)


# ---------------------------------------------------------------------------
# What the product PRINTS, which is better than what it builds.
#
# Every screen reachable from a Shell with no box under it is produced here
# and compared character for character - no template, no hole, nothing to
# argue about. The list is the price: a screen nobody added a scenario for is
# matched by template or skipped, and both of those are weaker. Adding a line
# here is the cheapest way to strengthen this file, which is why the count of
# driven matches is the first number the report prints.

SCENARIOS = [
    ("nat, which is a hierarchy", {}, ["nat"]),
    ("an IPv4 prefix under ipv6 address", {},
     ["interface GigabitEthernet0/12/0", "ipv6 address 10.0.0.1/24"]),
    ("a linux-name no kernel would take", {},
     ["interface GigabitEthernet0/12/0",
      "linux-name GigabitEthernet0/12/0"]),
    ("a VLAN 802.1Q reserves", {},
     ["interface GigabitEthernet0/12/0.700", "vlan 4095"]),
    ("no vlan, with the tag", {},
     ["interface GigabitEthernet0/12/0.700", "no vlan 700"]),
    ("no vlan, without it", {},
     ["interface GigabitEthernet0/12/0.700", "no vlan"]),
    ("no block-size", {}, ["nat cgnat pool residencial", "no block-size"]),
    ("an inside the pool does not have", {},
     ["nat cgnat pool residencial", "no inside 10.0.0.0/8"]),
    ("a role that is not one", {}, ["user joao", "role supervisor"]),
    ("root's groups", {}, ["user root", "role administrator"]),
    ("start shell with no rights", {"caps": set(), "mode": "operational"},
     ["start shell"]),
    ("start shell with only the CGNAT rights",
     {"caps": {"cgnat"}, "mode": "operational"}, ["start shell"]),
    # The two the box can only answer with cards under it, so the cards are
    # stubbed and nothing else is: the refusal, the list and the advice are
    # all the product's.
    ("a card this box has not got",
     {"nics": ["enp1s0", "enp2s0"], "route": "enp1s0"},
     ["dataplane interface eth9"]),
    ("the card this box is reached on",
     {"nics": ["enp1s0", "enp2s0"], "route": "enp1s0"},
     ["dataplane interface enp1s0"]),
]

# The findings are the other half of the VTY's voice and they are not printed
# where they are written: nine functions decide "this commits and does
# nothing" from the text of a candidate, RETURN the sentence, and a caller
# renders it through `say_finding` much later. Both halves are the product's,
# so calling the one and rendering with the other is still driving - the only
# thing written here is the configuration an operator would have typed.
def say_findings(sh, findings):
    for sev, stmt, why in findings:
        sh.say_finding(sev, stmt, why)


def say_announce(sh, findings):
    """The one finding printer that is not `say_finding`, and it wraps nothing.

    Quoted as it stands rather than folded, which is what the product does
    here - `print(f"{sev}: {subject}: {why}")` and the advice under it. The
    wiki quotes rows past the console's width on this screen and they are the
    box's rows.
    """
    for sev, subject, why, advice in findings:
        print(f"{sev}: {subject}: {why}")
        for line in advice:
            print("    " + line)


BGP_RM = ["router bgp 65001", " address-family ipv4 unicast",
          "  neighbor 200.150.0.2 route-map RM-TRANSITO-IN in"]
POOL = _config.Pool("clientes")
POOL.outside = ["200.150.0.16/28"]

FINDINGS = [
    ("a route-map that is not defined", lambda sh: say_findings(
        sh, hexash.route_map_reference_findings(BGP_RM))),
    ("a call to a route-map that is not defined", lambda sh: say_findings(
        sh, hexash.route_map_reference_findings(
            ["route-map RM-X permit 10", " call RM-COMUNIDADES"]))),
    ("an eBGP session with no maximum-prefix", lambda sh: say_findings(
        sh, hexash.max_prefix_findings(
            ["router bgp 65001", " neighbor 200.150.0.2 remote-as 65002"]))),
    ("a permit with no match", lambda sh: say_findings(
        sh, hexash.route_map_findings(
            ["route-map RM-TRANSITO-OUT permit 20"]))),
    ("an AS-path list that is not defined", lambda sh: say_findings(
        sh, [("warning", stmt, why) for stmt, why
             in hexash.undefined_policy_lists(
                 ["route-map RM-X permit 10",
                  " match as-path AS-CLIENTES"])])),
    ("a pool nothing announces", lambda sh: say_announce(
        sh, hexash.pool_announce_findings(
            ["router bgp 65001", " address-family ipv4 unicast"],
            {"clientes": POOL}))),
]


def a_shell(setup):
    sh = hexash.Shell()
    caps = setup.get("caps", {hexash.CAP_ALL, hexash.CAP_CGNAT})
    sh.caps = {hexash.CAP_CGNAT} if caps == {"cgnat"} else caps
    sh.mode = setup.get("mode", "config")
    sh.candidate = _config.Config()
    if "nics" in setup:
        sh.host_nics = lambda: [{"name": n} for n in setup["nics"]]
        sh.default_route_nic = lambda: setup.get("route")
    return sh


def drive(setup, lines):
    """One scripted session's output, with no box under the VTY."""
    sh = a_shell(setup)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for line in lines:
            try:
                sh.feed(line)
            except Exception:                       # pragma: no cover
                pass
    return buf.getvalue().split("\n")


def drive_findings(render):
    """What the VTY prints for the findings one of its checks returns."""
    sh = a_shell({})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        render(sh)
    return buf.getvalue().split("\n")


DRIVEN = [(name, drive(setup, lines)) for name, setup, lines in SCENARIOS]
DRIVEN += [(name, drive_findings(render)) for name, render in FINDINGS]


def driven_match(rows):
    for name, out in DRIVEN:
        for i in range(len(out) - len(rows) + 1):
            if out[i:i + len(rows)] == rows:
                return name
    return None


# ---------------------------------------------------------------------------
# The matcher.

# A row can also stop being the box's row in the MIDDLE. The prefix rule
# below sees a page that went on past the end of a message; this sees a page
# that agreed with one for a while and then said something else - a word
# edited inside a quoted refusal, which is what a screen looks like after
# somebody "fixed" it by hand. AGREE_FLOOR is 30 characters of the message's
# own opening text: two or three words agreeing is a coincidence between
# unrelated messages, thirty characters is the same sentence.
AGREE_FLOOR = 30
LEADING = [(t.text.split(HOLE)[0], t)
           for t in ERRS + ROWS + PROSE
           if len(t.text.split(HOLE)[0]) >= AGREE_FLOOR]


def agreement(text):
    """The message this text starts like and then stops being, or None."""
    best = None
    for seg, t in LEADING:
        i = 0
        while i < len(seg) and i < len(text) and seg[i] == text[i]:
            i += 1
        # BOTH sides have to carry on differently. A row that simply STOPS
        # where the message stops is the fold, read at the first row of a
        # paragraph, and a row that carries on past the end of the message is
        # the other finding below - neither is this one.
        if (i >= AGREE_FLOOR and i < len(seg) and i < len(text)
                and not t.match(text) and (best is None or i > best[1])):
            best = (t, i)
    return best


def row_template(row):
    for t in ROWS:
        if t.match(row):
            return t
    return None


def verdict_template(row):
    """
    The template behind a marked row, marker included or not.

    `err` writes the `error: ` itself and is handed the sentence, so the
    template is what comes AFTER the marker; the self-test's `bad` puts the
    marker in the string. Both spellings are tried, and the sentence-only one
    is tried against every bucket because a refusal written down in one place
    and printed in another - `system.py` returns this one and `hexash.py`
    prints it - is not an emitter call anything can follow.
    """
    t = row_template(row)
    if t:
        return t
    m = MARKED.match(row)
    after = row[row.index(m.group(1)) + len(m.group(1)):].lstrip()
    for bucket in (ERRS, ROWS, PROSE):
        for t in bucket:
            if t.match(after):
                return t
    # Last: a row this product builds in two goes - `line = f"note: ..."` and
    # then `line += f"; e.g. '{example}'"` under an `if`. Following that would
    # be following the dataflow; accounting for the row with both strings is
    # the same evidence and it is readable. Both halves are held to the rule
    # that one would have been.
    pair = concat_match(row) or concat_match(after)
    return pair[0] if pair else None


# A row the product builds by CONCATENATION - `print_prose("the statement
# would commit and reach nothing. " + enable_daemon_advice(need))`, or a
# `line +=` under an `if` - is two strings written in two places, and
# following the `+` through a function call or an assignment is not something
# reading the source can do. So a row is allowed to be two of the product's
# strings end to end, under the guards in `concat_match`.
#
# PLAIN is the first half at its safest: a whole string with no hole in it at
# all, so where it ends is not a decision. A template WITH holes can be a
# first half too, but only one whose own text ends in literal characters -
# `heads_of` says why.
PLAIN = [t for t in ERRS + ROWS + PROSE
         if HOLE not in t.text and len(t.text) >= 20]


def heads_of(text):
    """(template, rest) for every product string this text STARTS with."""
    for t1 in PLAIN:
        if text.startswith(t1.text) and len(t1.text) < len(text):
            yield t1, text[len(t1.text):]
    for t1 in ERRS + ROWS + PROSE:
        # A template ending in a hole has no end to find: its last wildcard
        # would stop one character in and hand the rest of the row to the
        # second half, which is the split that would let any two templates
        # account for any row at all.
        if t1.text.endswith(HOLE) or t1.literal < PREFIX_FLOOR:
            continue
        m = t1.rx.match(text)
        if m and m.end() < len(text):
            yield t1, text[m.end():]


def concat_match(text):
    """
    Two of the product's strings, end to end, accounting for the whole row.

    The SHARE IS TAKEN OVER THE ROW and not over each half, which is what lets
    the tail be a string too short to have entered the corpus on its own -
    `f"; e.g. '{example}'"` is nine literal characters and it is half of a row
    whose other half is forty-two. What it does not let through is a tail that
    is mostly hole: TAIL_FLOOR asks the second string for words of its own,
    because `f"{a} {b} {c}"` is two literal characters and it would otherwise
    account for the whole back half of any row at all - measured, it is what
    swallowed a refusal this file is supposed to refuse.
    """
    for t1, rest in heads_of(text):
        for bucket in (PROSE, ROWS, ERRS, LOOSE):
            for t2 in bucket:
                if (t2.rx.fullmatch(rest) and t2.literal >= TAIL_FLOOR
                        and t1.literal + t2.literal
                        >= LITERAL_SHARE * len(text)):
                    return [t1, t2]
    return None


def prose_group(rows, i):
    """
    How many rows from `i` one paragraph accounts for, and by which template.

    The rows are joined back into the sentence they were folded out of and
    handed to the product's own folder; the answer has to come back equal to
    the page, row for row. That is the constraint that makes a hole safe here:
    a template can carry four of them and still only match rows that break
    exactly where this box breaks them, at this indent, on a console of
    CONSOLE_COLUMNS.
    """
    ind = indent_of(rows[i])
    out = []
    for k in range(i, len(rows)):
        if indent_of(rows[k]) != ind:
            break
        para = " ".join(r.strip() for r in rows[i:k + 1])
        want = rows[i:k + 1]
        shell = [" " * ind + r for r in fold_s(para, 79 - ind)]
        for t in PROSE:
            # Two folders, because this product has two: textwrap for what the
            # VTY prints and `fold -s` for what the boot self-test does with
            # the tuner's answer. Either one has to reproduce the page exactly
            # - it is still the fold being checked and not waved past.
            if t.match(para) and want in (
                    hexash.console_prose(para, " " * ind), shell):
                out.append((k - i + 1, t))
        for sind, t in SPROSE:
            if sind == ind and t.match(para) and shell == want:
                out.append((k - i + 1, t))
        if want in (hexash.console_prose(para, " " * ind), shell):
            pair = concat_match(para)
            if pair:
                out.append((k - i + 1, pair[0], pair[1]))
    return out


def marked_prose(rows):
    """
    The say_finding shape: the reason's FIRST row shares the verdict's row.

    `say_finding` wraps the reason against an indent as wide as the marker,
    writes the marker over the first row and puts the rest under it at four
    spaces; the dataplane's own warnings arrive already carrying that marker
    and keep the wider indent. Both are reconstructed and re-folded here, and
    neither is allowed to differ by a space.
    """
    mark = MARKED.match(rows[0]).group(1)
    head = rows[0][rows[0].index(mark) + len(mark):].lstrip()
    wide = " " * len(f"{mark} ")
    out = []
    for k in range(len(rows)):
        para = " ".join([head] + [r.strip() for r in rows[1:k + 1]])
        folded = hexash.console_prose(para, wide)
        if len(folded) != k + 1 or folded[0].strip() != head:
            continue
        under = [r.strip() for r in folded[1:]]
        for width in (4, len(wide)):
            if [" " * width + u for u in under] != rows[1:k + 1]:
                continue
            for t in PROSE:
                if not t.match(para):
                    continue
                out.append((k + 1, t))
                # THE STATEMENT ROW, and the one place this file matches a
                # row without attributing it. `say_finding` ends by printing
                # the operator's own statement back whole and unwrapped -
                # `print(f"    {stmt}")` - and that text is the OPERATOR's,
                # not this box's. There is nothing in any source to compare it
                # to and there should not be.
                #
                # So it is allowed only under a verdict and a reason that have
                # both already been accounted for, at the four spaces that
                # printer uses, AND only when it looks nothing like one of
                # this box's own messages. Without that last condition the
                # exemption swallows the row under a verdict whenever somebody
                # edits a word inside a quoted refusal, which is the failure
                # this file exists to see - measured: it did, and the proof
                # that this file catches an edited word went green until the
                # condition was put here.
                if (k + 1 < len(rows) and indent_of(rows[k + 1]) == 4
                        and agreement(rows[k + 1].strip()) is None):
                    out.append((k + 2, t))
            break
    return out


def attribute(rows):
    """
    The templates that account for these rows, tiled left to right, or None.

    Left to right and first fit: a screen is a verdict row and then whatever
    the printers put under it, so a tiling that reaches the last row is a
    reading of the whole screen. The templates come back because the report
    says how loose the loosest accepted match of the day was, and that number
    is only worth printing if it is the one actually leaned on.
    """
    best = {}

    def solve(i):
        if i in best:
            return best[i]
        if i == len(rows):
            best[i] = (True, [])
            return best[i]
        options = []
        if i == 0:
            t = verdict_template(rows[0])
            if t:
                options.append((1, t))
            options.extend(marked_prose(rows))
        else:
            t = row_template(rows[i])
            if t:
                options.append((1, t))
            options.extend(prose_group(rows, i))
        for option in options:
            ok, used = solve(i + option[0])
            if ok:
                best[i] = (True, list(option[1:]) + used)
                return best[i]
        best[i] = (False, [])
        return best[i]

    ok, used = solve(0)
    return used if ok else None


def diverges(rows):
    """
    Where this screen stops being a screen the box prints, or None.

    Read on the verdict row and on each paragraph, never on the middle row of
    one: a folded paragraph's second row starts in the middle of a sentence
    and agreeing with the opening of some other message there would be an
    accident rather than a finding.
    """
    mark = MARKED.match(rows[0]).group(1)
    heads = [rows[0], rows[0][rows[0].index(mark) + len(mark):].lstrip()]
    # The rows UNDER a verdict are compared only when the verdict itself has
    # been recognised as this box's. Without that there is no reason to think
    # the paragraph was ever meant to be the message it half agrees with, and
    # a refusal whose headline is mostly interpolated - `router bgp 65001:
    # bgpd is not running` is sixteen literal characters out of thirty-seven -
    # is one this file cannot attribute either way. That is a skip, and a skip
    # must not turn into a finding about the wrong message.
    if verdict_template(rows[0]) is None:
        rows = rows[:1]
    i = 1
    while i < len(rows):
        k, ind = i, indent_of(rows[i])
        while k + 1 < len(rows) and indent_of(rows[k + 1]) == ind:
            k += 1
        heads.append(" ".join(r.strip() for r in rows[i:k + 1]))
        i = k + 1
    for text in heads:
        if row_template(text):
            continue
        found = agreement(text)
        if found:
            return found[0], text[found[1]:]
    return None


def stale(rows):
    """The first finding: the page's row goes on past the end of the box's."""
    row = rows[0]
    mark = MARKED.match(row).group(1)
    after = row[row.index(mark) + len(mark):].lstrip()
    best = None
    for text, buckets in ((row, (ROWS,)), (after, (ERRS, ROWS, PROSE))):
        for bucket in buckets:
            for t in bucket:
                rest = t.prefix_of(text)
                # The LONGEST row the box prints that this one starts with.
                # A short template that also fits says less about the page
                # than the long one does, and quoting the short one in the
                # failure would send the reader to the wrong message.
                if rest is not None and (best is None
                                         or t.literal > best[0].literal):
                    best = (t, rest)
    return best


# ---------------------------------------------------------------------------
# The report.

print(f"=== {len(SCREENS)} quoted screen(s) carrying a verdict, on "
      f"{len(PAGES)} page(s) ===")
print(f"  corpus: {len(ERRS)} refusal headline(s), {len(PROSE) + len(SPROSE)} "
      f"paragraph(s), {len(ROWS)} whole row(s) - from the VTY, the appliance "
      f"scripts and the plugin")
print(f"  {len(TOO_LOOSE)} template(s) refused entry: under {LITERAL_FLOOR} "
      f"literal characters, so they would match rows nobody wrote")
print(f"  {len(SCENARIOS) + len(FINDINGS)} scenario(s) driven through the VTY")

driven = matched = 0
loosest = None
skipped = []
findings = []

for rel, lineno, rows in SCREENS:
    # The self-test's markers carry two leading spaces of their own and some
    # pages quote them without. The offset is uniform when it happens, so it
    # is put back rather than compared away - a row that differs by a space
    # somewhere in the middle still has to fail.
    shifts = [rows, ["  " + r for r in rows]]
    name = None
    for shifted in shifts:
        name = driven_match(shifted)
        if name:
            break
    if name:
        driven += 1
        continue
    used = None
    for shifted in shifts:
        used = attribute(shifted)
        if used:
            break
    if used:
        matched += 1
        for t in used:
            share = t.literal / max(1, t.literal + 12 * t.holes)
            if loosest is None or (t.holes, -share) > loosest[0]:
                loosest = ((t.holes, -share), t)
        continue
    found = None
    for shifted in shifts:
        goes_on, stops = stale(shifted), diverges(shifted)
        if goes_on:
            found = ("goes on past it",) + goes_on
        elif stops:
            found = ("stops agreeing with it",) + stops
        if found:
            break
    if found:
        findings.append((rel, lineno, rows[0], found))
    else:
        skipped.append((rel, lineno, rows[0]))

print(f"\n  {driven} screen(s) matched by DRIVING the product - compared "
      f"character for character")
print(f"  {matched} screen(s) matched a TEMPLATE the product builds")
print(f"  {len(skipped)} screen(s) SKIPPED: nothing here can attribute them")
if loosest:
    (holes, share), t = loosest
    print(f"  the loosest match any of those leaned on: {holes} hole(s), "
          f"{t.literal} literal character(s), {-share:.0%} literal with a "
          f"hole counted as twelve, from {t.origin}")
    print(f"    {t.text.replace(HOLE, '{}')!r}")

if skipped:
    print("\n  what this file cannot see, which is the edge of the rule:")
    for rel, lineno, row in skipped:
        print(f"    {rel}:{lineno}  {row.strip()[:64]}")

if findings:
    print()
for rel, lineno, row, (how, t, rest) in findings:
    check(False,
          f"{rel}:{lineno} quotes a screen the box does not print. It starts "
          f"as {t.text.replace(HOLE, '{}')!r}, which {t.origin} builds, and "
          f"then {how}: {rest.strip()[:60]!r}")


# ---------------------------------------------------------------------------
# And the reader is driven over screens it MUST refuse, because every count
# above is "this list came out empty" and a list that is empty because nothing
# was read is the same green. Six ways this file could go quietly vacuous, all
# six put to it here.

print("\n=== the reader is driven over what it must refuse ===")

_REAL = ["error: vlan cannot be removed",
         "    a sub-interface is named after its tag, so one without a tag is "
         "not",
         "    something the dataplane can hold",
         "    remove the sub-interface itself: no interface <name>"]
check(driven_match(_REAL) or attribute(_REAL),
      "the screen this box prints for 'no vlan' is not attributed to the box")

# The failure of 2026-09-15, rebuilt: the verdict and its reason on one row.
_GLUED = ["error: vlan cannot be removed: a sub-interface is named after its "
          "tag, so one without a tag is not something the dataplane can hold"]
check(not attribute(_GLUED),
      "a refusal glued back into one sentence is accepted as a screen")
check(stale(_GLUED), "and nothing names it as the page going on past the box")

# The two-piece rule must not become a way to say yes to anything. This row is
# the box's refusal with the box's own next paragraph glued onto it - the shape
# the wiki carried this morning - and a pair made of the first string and
# something that matches "the rest" is exactly what it must not accept.
_TWO_CLAUSE = ["error: root's rights come from being root, not from a group: "
               "this VTY does not manage root's memberships"]
check(not attribute(_TWO_CLAUSE),
      "a refusal and the paragraph under it, glued into one row, is accepted "
      "as a row the box prints")
check(stale(_TWO_CLAUSE), "and nothing names it")

# A sentence nobody wrote, in the shape of one that was. Nothing may attribute
# it: this is the check on the check on the wildcards.
_INVENTED = ["error: vlan cannot be removed from a sub-interface on Tuesdays"]
check(not attribute(_INVENTED) and not driven_match(_INVENTED),
      "a refusal nobody wrote is attributed to this box")

# A template that is nearly all hole is refused entry rather than matching
# every row on every page. Asserted on `keep`, which is where it is decided.
_before = len(ROWS)
keep(ROWS, f"    {HOLE}", "a test")
check(len(ROWS) == _before,
      "a template that is a hole and four spaces entered the corpus, and it "
      "matches every quoted row anybody will ever write")

# The fold is the product's fold, measured against the product's own folder
# rather than against a belief about it. `say_list` is `fold -s`, which is not
# textwrap: it breaks after the last blank that fits.
import subprocess                                                # noqa: E402
_para = ("the generated file asks this boot for 11 parameter(s) and it "
         "carries none of them: update-grub has not run since that file was "
         "written, or the box has not rebooted since. Run update-grub and "
         "reboot")
_real = subprocess.run(["fold", "-s", "-w", "70"], input=_para,
                       capture_output=True, text=True).stdout.split("\n")
_real = [r[:-1] if r.endswith(" ") else r for r in _real if r != ""]
check(fold_s(_para, 70) == _real,
      f"this file folds a self-test paragraph differently from `fold -s -w "
      f"70`, so every screen it reads that way is measured against the wrong "
      f"shape: {fold_s(_para, 70)} vs {_real}")

# And the corpus is not empty, which is the last way all of this reads green
# over nothing at all.
check(len(ERRS) > 100 and len(PROSE) > 100 and len(ROWS) > 100,
      f"the corpus is {len(ERRS)}/{len(PROSE)}/{len(ROWS)}, which is not a "
      f"reading of this product's sources")
print("  a glued refusal, a glued pair, an invented one, an empty "
      "template, a wrong fold and an empty corpus are each refused")

print()
if fail:
    print(f"{fail} FAILURES")
    sys.exit(1)
print("ALL TESTS PASSED")
