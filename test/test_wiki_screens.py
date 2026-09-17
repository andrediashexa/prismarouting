#!/usr/bin/env python3
"""
The screens the PUBLIC wiki and `docs/` quote, read against the box that
prints them.

`wiki/` quotes what the box says, verbatim, inside fenced blocks - 71 of them
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

TWO DIRECTORIES, AND THE RUN SAYS SO (#384). `docs/` quotes screens too - 38
of its 175 fenced blocks carry a verdict marker, 44 screens on 12 pages, 28 of
them in `docs/routing.md` - and until #384 nothing read one of them:
test_doc_copies.py reads that directory for column-width claims and nothing
else. `CONTRIBUTING.md` calls `docs/` authoritative about the product, which
makes a stale transcript there MORE load-bearing than one on the wiki, not
less. So this file reads both trees, and it was not widened quietly: the first
line of every run names each directory with its page and screen count, every
count below is given per directory, and what could not be attributed is
printed per directory with the reason the reading stopped where it did.

Pointed at `docs/` unchanged, this file attributed 29 of 44 and skipped 15.
Read one at a time the way #365 read the wiki's 22: FOUR were pages splitting
rows the box writes whole - the announce warning and the cutoff refusal
hand-folded at 72 columns, the session-table refusal folded into three rows,
and a screen from #121 in the shape of two releases ago; those are corrected
on the page, and the fold is now a FINDING rather than something read by hand
(see `folded`). THREE more are reached by driving - the `cgnat` setting this
VTY has not got, the VRF whose table FRR cannot find, and the cutoff with
`docs/routing.md`'s own addresses. TWO were rows the installer writes after
`&&` and in a `case` arm, which the shell reader did not read past. ONE was
history - what `install.sh` printed before #119 - and is quoted as history
now, in prose, because a fenced block under `docs/` is read as what the box
prints. What remains is named on every run, each with where the reading
stopped.

`docs/` is written in English and `wiki/` in Portuguese, and that costs this
file nothing: what it compares is the box's own rows, which are English on
both.

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
writes. Seven things are done about that, and they are the reason this file is
worth its runtime:

  1. DRIVING BEATS MATCHING. A screen that can be produced by running the
     product is produced and compared character for character, no wildcard
     involved. The run is reported separately from the matched one so the
     proportion is visible on the screen. It is also the ONLY way to reach a
     refusal whose headline is mostly interpolated: `error: router bgp 65001:
     bgpd is not running` is sixteen literal characters out of thirty-seven
     and no template may ever account for it. Two of these need a box under
     the VTY and get one that answers "I am here" and nothing else.
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
  7. A HOLE HOLDS WHAT ITS SLOT HOLDS. The five above are all about HOW MUCH
     of a row is hole; none of them asks WHAT a hole may contain, and that is
     the gap #363 fell through. `{args[1]} does not keep what it received`
     is 33 literal characters and 60% literal on an address - it passes every
     one of them, correctly - and it OPENS with a hole, so when #363 took the
     noun out of that verdict the page's leftover `neighbour ` went into the
     hole whole and this file stayed green on both sides of the change. 199
     templates in this corpus open with a hole and 101 of those open on a
     slot the source says holds ONE VALUE: a name, a subscript, an attribute.
     One value is one token - an address, an interface, a path, a count - so
     a hole whose slot is one value MAY NOT CONTAIN WHITESPACE. `py_slot`
     reads that off the expression, and what it reads is printed.

     MEASURED BEFORE IT WAS WRITTEN, over the 59 screens this file attributed
     the day it went in: 48 holes filled, 6 holding whitespace, and not one
     of the 6 a slot that legitimately holds a phrase. Two are a `${...}` in
     a shell script, whose slots this file does not parse and therefore asks
     nothing of - they are the ceiling below. Three were the rule's own
     evidence: the hole was swallowing the `error: ` marker, or the first
     clause of a row built by concatenation, and with the rule on all three
     are read again TIGHTER - through the sentence after the marker, and
     through `concat_match`'s pair. The sixth is `(length {length}` in
     hexash.py's prefix-range refusal, a row this product builds in FOUR
     goes, where the final hole was swallowing `8, ge 24, le 16)` whole;
     no two strings account for that row, so it moved from the matched count
     to the printed skip list. That is the whole cost: one screen, and it was
     never really attributed - a hole was eating three clauses of it.

     WHERE THE RULE STOPS is measured too: it is not taken over the halves of
     `concat_match`, because held to it there five screens the box really
     prints are called wrong. `concat_match` says why.

## What it refuses on

Three shapes, and each is a page that half agrees with a message.

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

THE BOX'S OWN WORDS IN ROWS THE BOX DOES NOT PRINT. Two of the 22 skips #365
read by hand were this: a warning the dataplane writes in three rows of 177,
42 and 107 columns, hand-folded on the page into six of about 74; and a
failure quoted one space too far right. Four of the fifteen under `docs/`
were the same. It is read off the driven runs and nothing else: when the
words of a quoted screen, whitespace collapsed, are exactly the words of
rows this file has just watched the product print, and the rows are not
those rows, the page is quoting the box and folding it - or indenting it -
where the box does not. Driving is what makes this a finding and not a
guess: a template could only say the sentence exists, and a page that
folds a sentence at 79 columns where the box prints it at 140 still says
that sentence. See `folded`.

A VERDICT ROW A TEMPLATE ACCOUNTS FOR ONLY BECAUSE A HOLE SWALLOWED A WORD.
Point 7's rule, said out loud instead of silently refusing the match: the
template fits, most of the row is its literal text, and one hole the source
says holds a single value came out holding two words. That is a page carrying
a word the box does not print - or a value in a position that does not hold
it - and `swallows` names the page, the line, the slot and what went into it.

It is read on the VERDICT ROW ONLY, and that width was chosen rather than
assumed. Both widths are green on the wiki as it stands, so the wiki does not
choose between them; what does is that an `err` headline is one clause whose
slots are an address, an interface, a count, while the paragraphs under it are
exactly where a slot legitimately holds a sentence - `r.reason` is whatever
the dataplane said. Failing to MATCH on a wide hole is cheap: the screen lands
on the printed skip list, which is where a screen this file cannot account for
belongs. Calling a page WRONG is not cheap, and a check that goes red on
correct work is one people learn to run around. So the quiet half of the rule
runs everywhere and the loud half runs where the evidence is strongest.

## What it cannot see, and says so

Everything else is a SKIP, printed with its page and line AND WHERE THE
READING STOPPED: which rows were the box's and by which source line, and the
first row nothing accounts for - or that the verdict row fits a template and
is under the literal floor, with the share. A screen built through a
variable, a table the dataplane formats, a row that is the value alone:
those are named on the screen rather than counted as green, and named with
the reason so the next person does not have to work out again which of them
is an edge and which is a page to read. The skip list is the honest edge of
this check and it belongs where the next person sees it. A value in the
WRONG PLACE is the other blind spot - the wiki's forward refusal named public
addresses in the positions that carry private ones, and to this file those
are both just a wildcard. Only driving sees that.
"""
import contextlib
import errno
import io
import os
import re
import sys

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(REPO, "src", "hexash"))
import hexash                                                    # noqa: E402
import config as _config                                         # noqa: E402
import routes as _routes                                         # noqa: E402
import vppapi as _vppapi                                         # noqa: E402

# The trees to read. `HEXA_WIKI` exists for one reason and it is the reason
# this file is trusted: pointed at the pages as they were before the sweep of
# 2026-09-15, it names the stale screens out loud. A check that has only ever
# been seen green has not been seen. `HEXA_DOCS` is the same door for the
# second tree.
#
# The floors are the size each tree had the day it was read, rounded down:
# 32 pages and 71 screens on the wiki, 12 pages and 44 screens under docs/. A
# run that reads fewer than that is reading something else - an empty
# checkout, the wrong directory - and every count it then prints is about
# nothing.
WIKI = os.environ.get("HEXA_WIKI") or os.path.join(REPO, "wiki")
DOCS = os.environ.get("HEXA_DOCS") or os.path.join(REPO, "docs")
TREES = [("wiki", WIKI, 30, 50), ("docs", DOCS, 10, 40)]

# A hole in a template: an interpolated value, whatever the language wrote it
# with. One character so it can never be confused with the literal text around
# it, and so literal share is a subtraction.
HOLE = "\x00"


class Slot:
    """What the source says goes in one hole.

    `one_value` is the whole of the judgement: this slot holds ONE thing, so
    what comes out of it is one token. See `py_slot` for how that is read off
    the expression, and point 7 of the docstring for what is done with it.
    """

    def __init__(self, expr, one_value, why=""):
        self.expr = expr
        self.one_value = one_value
        self.why = why


# The hole nobody could read: the shell's `$(...)`, the plugin's `%v`, a row
# this file split out of a longer template. Not held to anything, because a
# rule applied where the evidence was never gathered is a guess.
UNREAD = Slot("?", False, "the language this came from was not parsed")

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

def pages(root):
    """Every page under one tree, the way test_wiki_rules.py reads them."""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(
                path, os.path.dirname(root)).replace(os.sep, "/")
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
    return [s for rel, first, block in fenced(page_list)
            for s in screens_in(rel, first, block)]


def runs(page_list):
    """
    Every run of consecutive rows in a fenced block that carries a verdict,
    as (page, line, rows) - the block as the box printed it, markers and
    all, up to a blank row.

    A screen is one verdict and what is under it, and a block that quotes
    the self-test's `[FAIL]` with the service's journal under it holds
    three: the verdict, and each `error:` the journal carries. What is
    between two of those - a row the box printed after one screen and
    before the next - is not in any screen, so a page that left it out was
    read as three honest screens. `gapped` reads these instead.
    """
    out = []
    for rel, first, block in fenced(page_list):
        n = 0
        while n < len(block):
            if not block[n].strip():
                n += 1
                continue
            k = n
            while k < len(block) and block[k].strip():
                k += 1
            if any(MARKED.match(r) for r in block[n:k]):
                out.append((rel, first + n + 1, block[n:k]))
            n = k
    return out


def fenced(page_list):
    """Every fenced block on these pages, as (page, line of the fence, rows)."""
    out = []
    for rel, text in page_list:
        lines = text.split("\n")
        inside, block, first, fence = False, [], 0, 0
        for n, line in enumerate(lines, 1):
            if line.strip().startswith("```"):
                if inside:
                    out.append((rel, first, block))
                    inside, block = False, []
                else:
                    inside, block, first = True, [], n
                    fence = indent_of(line)
            elif inside:
                # A fence inside a list item is indented with the item, and
                # Markdown takes that indent off every row when it renders
                # the block. So does this: docs/routing.md quotes the `nat`
                # refusal two columns in, under a bullet, and read with the
                # bullet's indent the screen is the box's rows two columns
                # to the right of where the box puts them.
                block.append(line[min(fence, indent_of(line)):])
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


PAGES = {}                              # tree -> pages
SCREENS = []                            # (tree, page, line, rows)
RUNS = []                               # the same, per block up to a blank
for _tree, _root, _min_pages, _min_screens in TREES:
    PAGES[_tree] = pages(_root)
    _found = screens(PAGES[_tree])
    SCREENS.extend((_tree,) + s for s in _found)
    RUNS.extend((_tree,) + s for s in runs(PAGES[_tree]))
    check(len(PAGES[_tree]) >= _min_pages,
          f"only {len(PAGES[_tree])} page(s) were read out of {_root}")
    check(len(_found) >= _min_screens,
          f"only {len(_found)} quoted screen(s) were found on "
          f"{len(PAGES[_tree])} page(s) under {_root}, so whatever this run "
          f"reports about {_tree}/ it is not reporting on {_tree}/")


# ---------------------------------------------------------------------------
# What the product builds: the templates, out of the sources that print them.

class Template:
    """One string the product builds, with a HOLE where a value goes."""

    def __init__(self, text, origin, slots=(), constant=None):
        self.text = text
        self.origin = origin
        # The NAME this string is assigned to, when it is a module constant
        # rather than an argument to a printer. `CONFIG_PATH` accounts for a
        # row on two screens and is not what printed it; the skip list says
        # which printer did, and this is how it knows to look.
        self.constant = constant
        self.holes = text.count(HOLE)
        self.literal = len(text) - self.holes
        # What the SOURCE says goes in each hole, in order. A reading this
        # file can only get from a language it parses: the Python side hands
        # over the slot expression per hole, everything else hands over
        # nothing and `UNREAD` is what a hole nobody could read holds - it is
        # never held to the whitespace rule below.
        self.slots = list(slots) + [UNREAD] * max(0, self.holes - len(slots))
        # A hole matches within ONE row and never nothing: an interpolated
        # value that came out empty is a different screen from this one, and
        # letting a hole match the empty string is how `f"{a}{b}"` starts
        # attributing every row on the page. Captured, because what a hole
        # SWALLOWED is the evidence `swallowed` reads.
        self.rx = re.compile("".join(
            re.escape(part) if i % 2 == 0 else "([^\n]+?)"
            for i, part in enumerate(
                re.split("(" + re.escape(HOLE) + ")", text))))

    def match(self, row):
        """The whole row, or nothing. Anchored - see the docstring, point 2."""
        m = self.rx.fullmatch(row)
        if not m or not row:
            return None
        if self.literal < LITERAL_SHARE * len(row):
            return None
        if self.swallowed(m):
            return None
        return self

    def swallowed(self, m):
        """
        The first hole this match filled with something no slot can hold.

        Point 7 of the docstring. Returns (slot, text) or None.
        """
        for slot, got in zip(self.slots, m.groups()):
            if slot.one_value and re.search(r"\s", got):
                return (slot, got)
        return None

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


class Fill:
    """One template and the exact text it was accepted against.

    The templates alone say how loose the loosest accepted match was; what
    its holes HELD is the other half of that answer, and it is the half
    point 7 rests on. Carried together because a template and the text it
    matched are one fact, and re-deriving the text afterwards reads a
    different row from the one attribution actually leaned on.
    """

    def __init__(self, t, text):
        self.t = t
        self.text = text

    def holes(self):
        m = self.t.rx.fullmatch(self.text)
        return list(zip(self.t.slots, m.groups())) if m else []


# Printed whole, folded to the console, and said after `error: `.
ROWS, PROSE, ERRS = [], [], []
TOO_LOOSE = []                          # refused entry: nearly all hole
LOOSE = []                              # the same, as templates


def keep(bucket, text, origin, slots=(), constant=None):
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
    t = Template(text.rstrip("\n"), origin, slots, constant)
    if t.literal < LITERAL_FLOOR:
        TOO_LOOSE.append((origin, text.replace(HOLE, "{}")))
        LOOSE.append(t)
        return
    bucket.append(t)


def keep_rows(buckets, text, origin, slots=(), constant=None):
    """The same, for a template written as several rows at once.

    The slots are handed out row by row, because a hole belongs to the row it
    is written on and a row that takes the wrong slot would be held to the
    wrong rule.
    """
    used = 0
    for row in text.split("\n"):
        n = row.count(HOLE)
        for bucket in buckets:
            keep(bucket, row, origin, slots[used:used + n], constant)
        used += n


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


def py_slot(node):
    """What the source says goes in this hole - see point 7 of the docstring.

    A NAME, an ATTRIBUTE or a SUBSCRIPT is one value: `args[1]` is the word
    the operator typed, `pool.name` is a pool's name, `r.prefix` is a prefix.
    One value is one token, and that is the whole of the rule.

    Everything else is a phrase and is left alone, and each of these is a
    reading of the expression rather than a decision about a site:

      * a CALL - `"/".join(sorted(names))` is a list, `enable_daemon_advice()`
        is a sentence a function composed. The name of the call says nothing
        about how many words come back, so nothing is asked of it.
      * another F-STRING, or a `+`, or a conditional: a phrase built here out
        of pieces, and the pieces are matched by the holes they became.
      * a slot NAMED FOR PROSE. `r.reason` is what the dataplane said and
        `{why}` is a finding's own sentence. These are attributes and names
        like any other, so the syntax cannot tell them apart - the identifier
        the author chose can, and it is written at the site, in the source,
        by the person who knew what goes in it. PROSE_WORDS is a list of
        WORDS and not of sites: a new `err(f"{reason} ...")` anywhere in this
        tree is exempt the moment it is written, nobody has to remember to
        come back here, and how much each word exempts is printed.
    """
    expr = ast.unparse(node)
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        return Slot(expr, False, "a slice is a sequence")
    if not isinstance(node, (ast.Name, ast.Attribute, ast.Subscript,
                             ast.Constant)):
        return Slot(expr, False, "composed here out of pieces")
    last = re.findall(r"\w+", expr)
    if last and last[-1].lower() in PROSE_WORDS:
        return Slot(expr, False, f"named {last[-1]!r}, which is a sentence")
    return Slot(expr, True)


# The words this product names a sentence with. FOUR, and each one was put
# here by finding the site that composes the sentence, not by imagining a
# slot that might hold one:
#
#   reason   `vppapi.Reply.reason`, which the dataplane's own refusal is
#            built into - `f"the dataplane refused it: {name}"` and a detail
#            after it. 74 holes in the corpus end on one.
#   why      `say_finding(mark, stmt, why)` and the four-field announce
#            findings: `why` IS the sentence that names the fault.
#   what     `require(cap, what)` is called with `start shell`, `clear bgp`,
#            `commit` - the VTY's own name for a command, and two of those
#            are two words. `print_prose(f"{what}; when this card...")` hands
#            it a whole clause about DPDK drivers.
#   msg      `cannot reach the dataplane: {msg}` - what the API layer said.
#
# How many holes each of these exempts is PRINTED on every run, which is the
# difference between a reading of the source and a list of excuses: a word
# that stops exempting anything, or one that starts exempting half the
# corpus, is on the screen the next time anybody runs this.
#
# `stmt`, `advice` and `line` are NOT here, and that is measured rather than
# forgotten: every template that interpolates one of them is under
# LITERAL_FLOOR - `f"    {stmt}"`, `f"{sev}: {subject}: {why}"` - so none of
# them is in the corpus at all, and the finding below reads only the verdict
# row, which is not where any of the three is printed.
PROSE_WORDS = {"reason", "why", "what", "msg"}


def py_template(node):
    """A literal or an f-string as (template, slots), or (None, ()).

    The slots come back with the template because the hole and what the
    source says goes in it are one fact, and splitting them across two passes
    is how they stop agreeing.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, []
    if isinstance(node, ast.JoinedStr):
        out, slots = "", []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out += part.value
            else:
                out += HOLE
                slots.append(py_slot(part.value if isinstance(
                    part, ast.FormattedValue) else part))
        return out, slots
    return None, []


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
    """Every (template, slots) inside a container the tables are written as."""
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
                    if len(parts) >= 2 and all(
                            p is not None for p, _ in parts):
                        at = f"{where}:{value.lineno}"
                        keep(ERRS, parts[0][0], at, parts[0][1])
                        for rest, slots in parts[1:]:
                            keep(PROSE, rest, at, slots)
            continue
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        args = [a for a in node.args if not isinstance(a, ast.Starred)]
        origin = f"{where}:{node.lineno}"
        if name in ("err", "err_dataplane") and args:
            for text, slots in py_strings(args[0]):
                if text is not None:
                    keep(ERRS, text, origin, slots)
        elif name == "err_prose" and args:
            for text, slots in py_strings(args[0]):
                if text is not None:
                    keep(ERRS, text, origin, slots)
            for arg in args[1:]:
                for text, slots in py_strings(arg):
                    if text is not None:
                        keep(PROSE, text, origin, slots)
        elif name in ("print_prose", "console_prose") and args:
            for text, slots in py_strings(args[0]):
                if text is not None:
                    keep(PROSE, text, origin, slots)
        elif name == "say_finding" and len(args) >= 3:
            for text, slots in py_strings(args[2]):
                if text is not None:
                    keep(PROSE, text, origin, slots)
        elif name == "print" and args:
            for text, slots in py_strings(args[0]):
                if text is not None:
                    keep_rows((ROWS,), text, origin, slots)
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
    # A string assigned to a module constant is a VALUE, not a printer -
    # `CONFIG_PATH` at the top of hexash.py is the path two screens quote on
    # a row of their own, and the skip list used to cite that line as "the
    # box's" as if it printed anything. The name is carried on the template
    # so the report can say what it is and find the printer that put it on
    # the screen.
    named = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            for part in ast.walk(node.value):
                named[id(part)] = node.targets[0].id
    for node in ast.walk(tree):
        if id(node) in docs or id(node) in inner:
            continue
        text, slots = py_template(node) if isinstance(
            node, (ast.Constant, ast.JoinedStr)) else (None, [])
        if text is None:
            continue
        keep_rows((PROSE, ROWS), text, f"{where}:{node.lineno}", slots,
                  named.get(id(node)))


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

# A call to one of the printers, wherever on the line it stands. Two shapes
# were being read past (#384): the installer writes `[ "$_n" -gt 0 ] && _vok
# "fstab: ..."` and `'$'*) _vok "root has a password, ..."` - a guard and a
# `case` arm in front of the verb - and both rows were on the skip list as
# screens this file could not see. Quotes in the prefix have to close, so a
# verb INSIDE a printed sentence (`say "... and ok to ignore"`) is never
# taken for a call: the sentence is one quoted unit and the `)` or `&&` that
# would have to follow it is inside it.
SH_CALL = re.compile(r"^\s*(?:(?:[^\"'#]|\"[^\"]*\"|'[^']*')*?(?:&&|\|\||\))"
                     r"\s*)?(ok|bad|warn|say|say_list|_vok|_vbad)\s+(\S.*)$",
                     re.S)


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


def sh_unquote(rest, sep=""):
    """The text a shell call prints, with HOLE where a value goes.

    Returns every reading of it: `${x:-default}` prints one of two things and
    both are screens this box produces, and the wiki quotes both of them for
    the rx-queue line.

    `sep` goes between quoted strings that are separate ARGUMENTS - a blank
    between them in the source. `say_list` prints `"$*"`, which is its
    arguments joined with a space, and the pair check writes one sentence
    as four of them across four continued lines; read as four templates it
    folds four times where the box folds once, and the wiki's correct quote
    of it sat on the skip list.
    """
    out, end = [], None
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'', rest):
        # Blank between two quoted strings, or a backslash and a newline:
        # either way the shell sees two arguments.
        if end is not None and m.start() > end \
                and re.fullmatch(r"[\s\\]+", rest[end:m.start()]):
            out.append(sep)
        out.append(m.group(1) if m.group(1) is not None else m.group(2))
        end = m.end()
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
        m = SH_CALL.match(line)
        # A message wider than the source line is written across several of
        # them, so the call is read until its quotes close - but only a CALL
        # is read that way. An odd number of quotes in a comment is not an
        # unclosed string: `# "in effect" must not collapse into "in effect"`
        # is a comment in appliance/selftest and reading on from it swallowed
        # the 247 lines under it, the account checks and the statistics
        # segment among them, so four screens this file could account for
        # were reported as screens it could not see. Quoting inside a
        # substitution - `tr -d '"'` - does the same thing from the other
        # side. Neither line is one this loop wants, so neither is read on
        # from.
        if m:
            # Read on while the quotes are open OR the line is continued
            # with a backslash: `say_list "  " "one" \` and three more
            # arguments under it is one call with one sentence.
            while ((line.count('"') % 2 or line.rstrip().endswith("\\"))
                   and n + 1 < len(lines)):
                n += 1
                line += "\n" + lines[n]
            m = SH_CALL.match(line)
        n += 1
        if not m:
            continue
        verb, rest = m.group(1), m.group(2)
        origin = f"{rel}:{first + 1}"
        if verb == "say_list":
            ind = re.match(r'\s*"(\s*)"', rest)
            if not ind:
                continue
            for text in sh_unquote(rest[ind.end():], sep=" "):
                t = Template(text, origin)
                if t.literal >= LITERAL_FLOOR:
                    SPROSE.append((len(ind.group(1)), t))
                else:
                    # `say_list "          " "$names"` is a value on a row
                    # of its own, kept as the printer it is for the skip
                    # list - with its indent, which is the whole of what it
                    # writes.
                    TOO_LOOSE.append((origin, text.replace(HOLE, "{}")))
                    LOOSE.append(Template(ind.group(1) + text, origin))
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


def plugin_format(word):
    """The plugin's `remap:` format string carrying this word, read above."""
    for t in ROWS:
        if (t.origin.startswith("src/plugins/") and t.text.startswith("remap:")
                and word in t.text):
            return t
    return None


def plugin_row(word, *values):
    """
    One row of the dataplane's answer, built from the plugin's own format
    string with these values in its holes.

    The fake dataplane under the re-mapping scenarios answers with rows made
    here, so that the words on them are bpa_cli.c's and not this file's: a
    fake answering a sentence the plugin does not build would drive a screen
    nobody prints and call the page that quotes it the box's. Asserted at
    the point of use, because a format string that stopped being found would
    otherwise answer an empty row and the scenario would drive nothing.
    """
    t = plugin_format(word)
    check(t is not None and t.holes == len(values),
          f"no `remap:` format string of the plugin's carries {word!r} with "
          f"{len(values)} hole(s): the fake dataplane cannot answer with "
          f"the plugin's words")
    if t is None or t.holes != len(values):
        return ""
    parts = t.text.split(HOLE)
    out = parts[0]
    for value, part in zip(values, parts[1:]):
        out += str(value) + part
    return out


def _remap(moved, total, live, moves):
    """The dataplane's whole answer for a pool that re-maps `moved` of `total`
    subscribers, `live` of them with state, and two of the moves by name."""
    return ([plugin_row("subscribers change", moved, total, live)]
            + [plugin_row("moves from", *m) for m in moves]
            + [plugin_row("every session on this pool")])


C_CONVERSION = re.compile(r"%[-0-9.*]*[a-zA-Z]")


def c_function(rel, name):
    """The source text of one C function of the plugin's, by name."""
    src = io.open(os.path.join(REPO, rel), encoding="utf-8").read()
    start = src.find(f"\n{name} (")
    check(start >= 0, f"{rel} defines no function {name}: the fake dataplane "
          f"cannot answer with its words")
    return src[start:src.find("\n}", start)] if start >= 0 else ""


def c_strings(text):
    """Every run of adjacent string literals in `text`, joined the way the
    compiler joins them, in source order."""
    return ["".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(0)))
            for m in re.finditer(r'(?:"(?:[^"\\]|\\.)*"\s*)+', text)]


def timeout_row(name, value):
    """
    One row of the dataplane's answer to `show bpa timeouts`, for this
    timeout at this value, as `bpa_timeouts_show` prints it.

    The VTY relays every row of that answer carrying the floor mark under
    `warning:` after a commit that set a timeout, and the wiki quotes one.
    The row is `%-16s %6u%s` - three literal characters, so it is under the
    floor and `plugin_row` cannot fill it, and the widths are what the
    operator sees, so a template with its holes stripped of them would not
    print the row either. The format is read off the function's own source
    with its widths kept and filled with Python's `%`, which formats these
    three conversions as C does; the mark is the function's two halves round
    the RFC, and the floor and the RFC are bpa_pool.h's table entry for this
    timeout. The `!= 0 && < floor` test is the ternary's, retyped, and the
    assertion beside it holds it to the source. What is typed here is the
    timeout's name and the page's value.
    """
    body = c_function("src/plugins/bpa/bpa_cli.c", "bpa_timeouts_show")
    strs = c_strings(body)
    fmts = [t for t in strs if len(C_CONVERSION.findall(t)) == 3]
    check(len(fmts) == 1 and "< (floor)" in body,
          f"bpa_timeouts_show prints {len(fmts)} row shape(s) with three "
          f"values, not the one marked row this file fills")
    if len(fmts) != 1:
        return ""
    fmt = fmts[0]
    below, asks = strs[strs.index(fmt) + 1:strs.index(fmt) + 3]
    hdr = io.open(os.path.join(REPO, "src", "plugins", "bpa", "bpa_pool.h"),
                  encoding="utf-8").read()
    m = re.search(r'_ \(\w+, "%s", (\d+), "([^"]+)"' % re.escape(name), hdr)
    check(m is not None and "BELOW" in below,
          f"bpa_pool.h sets no floor for the {name!r} timeout, or the mark "
          f"after the row's format is not the floor mark: {below!r}")
    if m is None:
        return ""
    floor, rfc = int(m.group(1)), m.group(2)
    mark = below + rfc + asks if value != 0 and value < floor else ""
    return fmt % (name, value, mark)


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
    # The one the box can only answer with cards under it, so the cards are
    # stubbed and nothing else is: the refusal, the list and the advice are
    # all the product's. It was two: the card carrying the default route was
    # refused on a box with another card until #385, and a run typing that
    # card printed nothing at all for a session with this file green - the
    # scenario stayed on the list, the wiki kept the refusal, and no check
    # asked whether a run had printed a row. One does now, under DRIVEN.
    ("a card this box has not got",
     {"nics": ["enp1s0", "enp2s0"], "route": "enp1s0"},
     ["dataplane interface eth9"]),
    # The two `commit check` refuses on. `daemons` is what FRR answers `show
    # daemons` with, and it is the whole of what either box is stubbed to say.
    ("a port range with no room for one block", {"daemons": ""},
     ["nat cgnat pool residencial", "inside 100.64.0.0/22",
      "outside 200.150.0.16/28", "block-size 512", "ports 1024 1200",
      "exit", "commit check"]),
    ("a router stanza for a daemon that is not running",
     {"daemons": "zebra staticd mgmtd"},
     ["router bgp 65001", "exit", "commit check"]),
    # A word under `cgnat` this VTY has no setting for: docs/ha.md quotes the
    # refusal #273 put in front of a statement that used to be accepted and
    # applied to nothing. The list under it is joined from the VTY's own
    # table, which no template accounts for and driving does.
    ("a cgnat setting this VTY has not got", {},
     ["nat cgnat ha peer 200.150.0.12 priority 200"]),
    # `commit` over a pool that re-maps subscribers with live mappings. The
    # dataplane decides it and answers `... check` with `remap:` rows; the
    # VTY strips the tag and prints them under its own verdict and its own
    # advice. Every row the fake answers with is the plugin's format string
    # with the page's numbers in its holes, so the words are bpa_cli.c's and
    # the numbers are the only thing typed here - once per page that quotes
    # this screen, because the numbers are what the operator compares.
    ("a change that re-maps subscribers, as docs/architecture.md quotes it",
     {"daemons": "zebra staticd mgmtd bgpd",
      "remap": _remap(248, 256, 0, [
          ("100.64.64.8", "203.0.113.0", 1536, 2047,
           "203.0.113.0", 2048, 3071),
          ("100.64.64.9", "203.0.113.1", 1536, 2047,
           "203.0.113.1", 2048, 3071)])},
     ["nat cgnat pool clientes", " inside 100.64.64.0/24",
      " outside 203.0.113.0/31", "exit", "commit"]),
    ("a change that re-maps subscribers, as docs/pool-resize.md quotes it",
     {"daemons": "zebra staticd mgmtd bgpd",
      "remap": _remap(1024, 1024, 61, [
          ("100.64.0.0", "200.150.0.16", 1024, 1535,
           "45.170.16.0", 1024, 1535),
          ("100.64.0.1", "200.150.0.17", 1024, 1535,
           "45.170.16.1", 1024, 1535)])},
     ["nat cgnat pool clientes", " inside 100.64.0.0/22",
      " outside 45.170.16.0/28", "exit", "commit"]),
    ("a change that re-maps subscribers, as the wiki quotes it",
     {"daemons": "zebra staticd mgmtd bgpd",
      "remap": _remap(1024, 1024, 512, [
          ("100.64.0.5", "200.150.0.17", 1536, 2047,
           "200.150.0.33", 2048, 2559),
          ("100.64.0.9", "200.150.0.17", 2048, 2559,
           "200.150.0.34", 1536, 2047)])},
     ["nat cgnat pool clientes", " inside 100.64.0.0/22",
      " outside 200.150.0.32/28", "exit", "commit"]),
    # `commit` over a timeout under the floor its RFC sets. The dataplane
    # applies it and marks the row in `show bpa timeouts`; the VTY reads that
    # answer back and prints every marked row under `warning:`. The fake's
    # answer is the plugin's own row (`timeout_row`, above), so the words,
    # the widths and the RFC are bpa_cli.c's and bpa_pool.h's, and what is
    # typed here is the page's value.
    ("a timeout under the floor its RFC sets",
     {"daemons": "zebra staticd mgmtd bgpd",
      "timeouts": [timeout_row("udp", 119)]},
     ["nat cgnat timeout udp 119", "commit"]),
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


def say_cutoff(sh, findings):
    """The refusal `commit` prints for the prefix carrying this session.

    `session_cut_findings` decides it and do_commit renders it, in the shape
    `say_announce` already has: `err` for the headline, which wraps nothing,
    and the advice whole underneath. Both rows run past the console on real
    input and they are the box's rows, which is why the wiki quotes them long.
    """
    for pfx, why, advice in findings:
        sh.err(f"{pfx}: {why}")
        for line in advice:
            print("    " + line)


def say_unplaced(sh, unplaced):
    """The note `commit check` prints for a VRF whose table it cannot find.

    `routes.unreachable_next_hops` returns what could not be checked and
    do_commit renders it in three goes: the note, the reason through
    `print_prose`, and the first statement whole. The last of those is the
    operator's own row after fifteen literal characters, so no template will
    reach it and docs/routing.md's screen sat on the skip list until it was
    driven.
    """
    for vrf, why, stmts in unplaced:
        print(f"note: {len(stmts)} static route(s) in 'vrf {vrf}' were not "
              "checked for a reachable next hop")
        hexash.print_prose(why)
        print(f"    first one: {stmts[0]}")


BGP_RM = ["router bgp 65001", " address-family ipv4 unicast",
          "  neighbor 200.150.0.2 route-map RM-TRANSITO-IN in"]
POOL = _config.Pool("clientes")
POOL.outside = ["200.150.0.16/28"]

_AS_BAD = "bgp as-path access-list AS-BAD permit ^65001("
_PL_BAD = "ip prefix-list PL-CLIENTES seq 10 permit 10.12.0.0/8 ge 24 le 16"

# What this box announces now, and the two changes that take the prefix
# carrying the session away: one stops originating it, the other keeps the
# origination and denies it to the only neighbour that would have carried it.
_CUT_RUNNING = ["router bgp 65001",
                " neighbor 200.150.0.2 remote-as 65002",
                " address-family ipv4 unicast",
                "  network 200.150.0.0/24",
                "  neighbor 200.150.0.2 activate",
                " exit-address-family"]
_CUT_GONE = [l for l in _CUT_RUNNING if l.strip() != "network 200.150.0.0/24"]
_CUT_DENIED = _CUT_RUNNING[:5] + [
    "  neighbor 200.150.0.2 route-map RM-NADA out",
    " exit-address-family",
    "route-map RM-NADA deny 10"]

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
    # A neighbour written with the name the DATAPLANE reports rather than the
    # one FRR can act on. The sentence is built in three goes - a literal, the
    # joined Linux-side names, and an f-string - so no pair of strings accounts
    # for the row and the template side cannot reach it. Driving can.
    ("a neighbour named by the dataplane's own name", lambda sh: say_findings(
        sh, hexash.bgp_errors(
            ["router bgp 65001",
             " neighbor host-veth-vpp interface remote-as external"],
            have_interfaces={"host-veth-vpp", "vppwan0"},
            lcp_pairs={"host-veth-vpp": ["vppwan0"]},
            device_interfaces={"host-veth-vpp"}))),
    # An AS-path regex FRR would refuse. The reason is whatever `re` says, and
    # on this one it is longer than the product's own sentence - 48 characters
    # against 40 - so the row is under LITERAL_SHARE and no template may have
    # it. The statement is the operator's, as everywhere here.
    ("an AS-path regex that does not compile", lambda sh: say_findings(
        sh, [("error", _AS_BAD, hexash.as_path_regex_error(_AS_BAD))])),
    # A prefix-list entry whose range does not hold. The sentence is built
    # in pieces under an `if` - `(length {}` and then `, ge {}` and `, le
    # {}` as they apply - so its template ends in a hole and no tiling
    # reaches the row; and `say_finding` wraps the reason, so the wiki's
    # two-row quote of it is either the box's fold or a hand one, and only
    # driving says which.
    ("a prefix range that does not hold", lambda sh: say_findings(
        sh, [("error", _PL_BAD, hexash.prefix_range_error(_PL_BAD))])),
    # The two boxes of an HA pair mapping subscribers differently. Two
    # statements under one reason, which is why it has a printer of its own.
    ("a peer that maps subscribers differently", lambda sh: sh.say_divergence(
        "warning", [("residencial", "block-size 2048", "block-size 1024")])),
    # The prefix that carries this session, in both shapes: the box stops
    # originating it, and the box still originates it and no neighbour is sent
    # it. The headline is `f"{pfx}: {why}"`, two literal characters, so the
    # corpus refuses it entry and only driving reaches these.
    ("this change stops originating the session's prefix",
     lambda sh: say_cutoff(sh, hexash.session_cut_findings(
         _CUT_RUNNING, _CUT_GONE, "200.150.0.7"))),
    ("no neighbour would be sent the session's prefix",
     lambda sh: say_cutoff(sh, hexash.session_cut_findings(
         _CUT_RUNNING, _CUT_DENIED, "200.150.0.7"))),
    # The same refusal with the addresses docs/routing.md quotes it with. The
    # words are the product's either way; what a second run buys is the row
    # character for character on the page that carries these values.
    ("no neighbour would be sent the session's prefix, as docs/ quotes it",
     lambda sh: say_cutoff(sh, hexash.session_cut_findings(
         _DOC_RUNNING, _DOC_DENIED, "203.0.115.9"))),
    # A static route in a VRF FRR reports no table for. `{}` for the tables
    # is the answer FRR gives before the VRF exists, and the note is the
    # product's way of saying the check did not run rather than passing it.
    ("a VRF static route whose table FRR cannot find",
     lambda sh: say_unplaced(sh, _routes.unreachable_next_hops(
         _VRF_ROUTE, {}, hexash.block_paths(_VRF_ROUTE), {})[1])),
    # Three refusals whose sentence is the DATAPLANE's, driven through the
    # product's own client with a canned return code and log line. `cgnat
    # session-table: {}` is thirteen percent literal and `pool '{}': {}` is
    # two characters, so no template reaches either - and until these were
    # driven the one-row session-table refusal docs/routing.md was corrected
    # to could be folded back into three with nothing going red.
    ("the session-table refused once pools exist", lambda sh: _refused(
        sh, -147, "session-table: tables already created; set this before "
        "configuring pools").apply_global(
            "cgnat session-table buckets 1024 memory 64")),
    ("a pool the dataplane refused, named", lambda sh: _pool_refused(sh)),
    # The revert that could not be armed: `arm_confirm` with the store
    # raising EACCES on /etc/hexarouting, which is the whole of what the
    # page's six rows say. Row 2 is the path and the reason on a row of its
    # own (#252), and it fits no template because it is all value.
    ("a revert that could not be armed on the box",
     lambda sh: _no_store(sh).arm_confirm(10, None)),
    # `--load` of a saved file with a line this VTY cannot hold. The path is
    # printed whole on a row of its own (#276), so the row is the box's own
    # path or it is not the page's: the file is answered from memory at
    # CONFIG_PATH (`_load`, below). Row 3 is the operator's line printed
    # whole, which is the one thing no template reaches, and the two pages
    # that quote this screen cut the hash at different places - so the run is
    # taken once per page, with that page's line, as the `remap:` runs are.
    ("a saved file with a line this VTY cannot hold, as docs/ quotes it",
     lambda sh: _load(sh, "user joao\n password 6$Xq7kPz2r$4Pv3cJ9sQm2WdT1hRf"
                      "Lb8XkY...\n")),
    ("a saved file with a line this VTY cannot hold, as the wiki quotes it",
     lambda sh: _load(sh, "user joao\n password 6$Xq7kPz2r$4Pv3cJ9sQm2WdT1hRf"
                      "Lb8XkY6nAeUoZgC5iMt...\n")),
    # `--load` of a file that locks everybody out, on a box somebody can log
    # into today: the #121 shape as the box prints it now, ten rows, of
    # which the self-test quotes the last six under its verdict. Both the
    # lockout check and the one behind it fire, because the account the
    # running configuration has is the administrator too.
    ("a saved file that locks everybody out",
     lambda sh: _load(sh, "ssh password-authentication no\n",
                      running="ssh password-authentication yes\n"
                      "user noc\n password " + _A_HASH + "\n"
                      " role administrator\n")),
]

#: A crypt hash `Config` accepts on a `password` row: `openssl passwd -6` of
#: nothing in particular. The running configuration needs one account able
#: to log in, and a password the parser refuses is an account with none.
_A_HASH = ("$6$rounds=5000$saltsalt$qFmFH.bQmmtXCsBDTYd.rF7dxJmR/n8pvv8Mh3M5T2"
           "bl1WV5hsY3W.yg7FyWqA9NnlnYfEzGjFeMwqk9cA6fY0")

_DOC_RUNNING = [l.replace("200.150.0.0/24", "203.0.115.0/24")
                for l in _CUT_RUNNING]
_DOC_DENIED = [l.replace("200.150.0.0/24", "203.0.115.0/24")
               for l in _CUT_DENIED]
_VRF_ROUTE = ["vrf green", " ip route 10.55.0.0/16 172.31.99.1", "exit-vrf"]


# A box under the VTY, for the two screens `commit check` refuses on and the
# one `commit` refuses on. The first two are decided by the product before
# anything is sent - the port range is measured against the block size, and
# the daemon list is read from FRR - so what is stubbed here answers "yes, I
# am here" and nothing more: no message is answered with a sentence, and none
# of the text on either screen comes from this. The third is decided by the
# DATAPLANE, which answers `... check` with `remap:` rows, and the rows the
# fake answers with are built from the plugin's own format strings - see
# `plugin_row`.
#
# WHY IT IS HERE AT ALL, when the rest of this file drives a Shell with no box
# under it. `error: router bgp 65001: bgpd is not running` is sixteen literal
# characters out of thirty-seven and `pool '{name}': {why}` is two. Neither
# will ever be reached by a template - `diverges` says why - so either they
# are driven or they stay on the wiki with nothing reading them.
# test_hexash_apply.py owns what a refusal at APPLY time does; this is the
# check that runs before it, and it sends nothing.
class _Api:
    def available(self):
        return (_vppapi.OK, "stub")

    def call(self, message, args):
        return _vppapi.Reply(_vppapi.OK)

    def dump(self, message, args):
        return _vppapi.Reply(_vppapi.OK, fields={"rows": []})


def _a_box(sh, daemons):
    sh.api = _Api()
    sh.dp = type("Dp", (hexash.Vpp,), {
        "TRACE": False,
        "__init__": lambda self: None,
        "run": lambda self, cmd: (0, ""),
        "running": lambda self: (0, "")})()
    # Derived from the real Frr so that `daemons()` is the product's own
    # parser over the product's own `show daemons` answer, which is the half
    # of that screen this file is reading.
    sh.frr = type("Frr", (hexash.Frr,), {
        "available": lambda self: daemons is not None,
        "show": lambda self, cmd: ((0, daemons) if "daemons" in cmd
                                   else (1, "")),
        "reload": lambda self, lines, check_only=False: (0, "")})()
    sh.running_config = lambda: _config.Config()


def a_shell(setup):
    sh = hexash.Shell()
    caps = setup.get("caps", {hexash.CAP_ALL, hexash.CAP_CGNAT})
    sh.caps = {hexash.CAP_CGNAT} if caps == {"cgnat"} else caps
    sh.mode = setup.get("mode", "config")
    sh.candidate = _config.Config()
    if "nics" in setup:
        sh.host_nics = lambda: [{"name": n} for n in setup["nics"]]
        sh.default_route_nic = lambda: setup.get("route")
    if "daemons" in setup:
        _a_box(sh, setup["daemons"])
    # What the fake dataplane answers, by the question: the `... check` of
    # a pool change, and `show bpa timeouts` after a commit. Anything else
    # is answered with nothing, which is what a box with no pools says.
    answers = []
    if "remap" in setup:
        answers.append((lambda cmd: cmd.endswith(" check"),
                        "\n".join(setup["remap"]) + "\n"))
    if "timeouts" in setup:
        answers.append((lambda cmd: cmd == "show bpa timeouts",
                        "\n".join(setup["timeouts"]) + "\n"))
    if answers:
        sh.dp.run = lambda cmd: next(((0, a) for asks, a in answers
                                      if asks(cmd)), (0, ""))
    return sh


def _refused(sh, retval, logline):
    """
    A dataplane that refuses every message with one return code and has
    one line from the plugin in its log.

    Everything on the row is then the product's: `Vat2.call` parses the
    reply, names the code from `RETVAL_NAMES`, reads the sentence off `show
    log` exactly as it does on a box, and `err_dataplane` prints what
    `vppapi` composed. Nothing here but the number and the plugin's log line.
    """
    _a_box(sh, "zebra staticd mgmtd bgpd")

    def run(cmd, timeout=15):
        if "show" in cmd and "log" in cmd:
            return (0, "2026/09/14 18:36:13:123 error       bpa " + logline
                    + "\n", "")
        return (0, '{"_msgname": "bpa_reply", "retval": %d}' % retval, "")
    sh.api = _vppapi.Vat2(runner=run)
    return sh


# The pool loop's refusal in `apply`, which names the pool because a commit
# carrying several has to say which one the dataplane refused. The f-string
# below is that one line of the product retyped, and the check beside it says
# so the day the product's stops being this one.
_POOL_REFUSAL = """self.err_dataplane(f"pool '{pool.name}': {r.reason}")"""


def _pool_refused(sh):
    _refused(sh, -7, "bpa_pool_init: pool 'client-bad': block size 500 must "
             "be a power of two")
    r = sh.api_call("bpa_pool_add_del", {"name": "client-bad"})
    sh.err_dataplane(f"pool 'client-bad': {r.reason}")


_HEXASH = os.path.join(REPO, "src", "hexash", "hexash.py")
check(_POOL_REFUSAL in io.open(_HEXASH, encoding="utf-8").read(),
      f"the pool refusal driven here is not the line hexash.py prints: "
      f"{_POOL_REFUSAL!r} is not in src/hexash/hexash.py")


class _NoStore:
    """The confirm store on a directory this account cannot write."""
    path = "/etc/hexarouting/confirm-pending"

    def arm(self, deadline, backup, minutes):
        raise OSError(errno.EACCES, "Permission denied", "/etc/hexarouting")


def _no_store(sh):
    sh.confirm = _NoStore()
    return sh


def _load(sh, text, running=""):
    """
    `--load` of a saved file with these lines, on a box running `running`.

    The file is the one thing `load` reads off the box, and it is answered
    from memory at the box's own CONFIG_PATH, the way `_NoStore` answers the
    confirm store: the path is printed whole on a row of its own, so a file
    under /tmp would drive a row no page quotes. `os.path.exists` is the
    module's, patched for that one path and put back.
    """
    _a_box(sh, "zebra staticd mgmtd bgpd")
    if running:
        sh.running_config = lambda: _config.Config.parse(running)
    path = hexash.CONFIG_PATH
    exists, real_open = os.path.exists, open
    hexash.os.path.exists = lambda p: p == path or exists(p)
    hexash.open = (lambda p, *a, **k: io.StringIO(text) if p == path
                   else real_open(p, *a, **k))
    try:
        sh.load(path)
    finally:
        hexash.os.path.exists = exists
        del hexash.open


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

# A run that printed nothing drove nothing. `drive` swallows what the VTY
# raises, so a scenario whose statement the product now accepts in silence -
# or refuses by raising - is a name on the list, a count of one and no row
# anywhere: "the card this box is reached on" typed a card #385 stopped
# refusing and printed nothing for a session, while the wiki kept the
# refusal. Every run has to have printed a row.
_SILENT = [name for name, out in DRIVEN if not any(r.strip() for r in out)]
check(not _SILENT,
      "a driven run printed no row at all, so its scenario holds no screen "
      "and nothing says so: " + "; ".join(repr(n) for n in _SILENT))


def driven_match(rows):
    for name, out in DRIVEN:
        for i in range(len(out) - len(rows) + 1):
            if out[i:i + len(rows)] == rows:
                return name
    return None


DRIVEN_ROWS = {row for _name, out in DRIVEN for row in out if row.strip()}


def driven_row(row):
    """Whether this exact row came out of one of the runs above.

    The other way a verdict row is known to be this box's, and it is the one
    that matters for the screens only DRIVING can attribute. `error: router
    bgp 65001: bgpd is not running` is sixteen literal characters out of
    thirty-seven, so no template will ever account for it - and without this,
    a word edited in the PARAGRAPH under it could not be a finding either,
    because `diverges` would not read past the verdict. Measured over one
    word edited in each of seven driven screens: reported once with the
    verdict recognised by template alone, three times with this. The rest
    still land on the printed skip list, which is where a screen nothing can
    account for belongs.
    """
    return row in DRIVEN_ROWS


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
            return Fill(t, row)
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
    got = row_template(row)
    if got:
        return got
    m = MARKED.match(row)
    after = row[row.index(m.group(1)) + len(m.group(1)):].lstrip()
    for bucket in (ERRS, ROWS, PROSE):
        for t in bucket:
            if t.match(after):
                return Fill(t, after)
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

    AND POINT 7 IS NOT TAKEN OVER EITHER HALF, which is the one place that
    rule stops. Measured: held to it, five screens this box really prints are
    called wrong - `error: invalid argument: ports min 70000: expected
    1-65535` is `vppapi`'s two words and then a whole clause the caller
    composed into the hole after them. A row assembled out of two strings is
    a row whose halves were written apart, so what the join puts in a hole is
    not one value any more, and a rule that goes red on five correct pages is
    a rule people run around.
    """
    for t1, rest in heads_of(text):
        for bucket in (PROSE, ROWS, ERRS, LOOSE):
            for t2 in bucket:
                if (t2.rx.fullmatch(rest) and t2.literal >= TAIL_FLOOR
                        and t1.literal + t2.literal
                        >= LITERAL_SHARE * len(text)):
                    return [Fill(t1, text[:len(text) - len(rest)]),
                            Fill(t2, rest)]
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
                out.append((k - i + 1, Fill(t, para)))
        for sind, t in SPROSE:
            if sind == ind and t.match(para) and shell == want:
                out.append((k - i + 1, Fill(t, para)))
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
                out.append((k + 1, Fill(t, para)))
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
                    out.append((k + 2, Fill(t, para)))
            break
    return out


def tiling(rows):
    """
    The templates that account for these rows, tiled left to right.

    Left to right and first fit: a screen is a verdict row and then whatever
    the printers put under it, so a tiling that reaches the last row is a
    reading of the whole screen. The templates come back because the report
    says how loose the loosest accepted match of the day was, and that number
    is only worth printing if it is the one actually leaned on.

    Returns (ok, used, reached): `reached` is the furthest row the tiling got
    to, and it is what the skip list prints - a screen this file cannot
    account for is a screen it read some way into, and WHERE it stopped is
    the difference between an edge of the rule and a page to go and read.
    `so_far` is the templates that got it there, kept for the same reason.
    """
    best = {}
    far = {"row": 0, "used": []}

    def solve(i, so_far):
        if i > far["row"]:
            far["row"], far["used"] = i, so_far
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
            ok, used = solve(i + option[0], so_far + list(option[1:]))
            if ok:
                best[i] = (True, list(option[1:]) + used)
                return best[i]
        best[i] = (False, [])
        return best[i]

    ok, used = solve(0, [])
    return ok, used, far


def attribute(rows):
    """The templates that account for the whole screen, or None."""
    ok, used, _far = tiling(rows)
    return used if ok else None


def loose_fit(text):
    """The template that fits this text once the literal floor is waived.

    Not a match - the floor is the whole argument - but the reason a row was
    not attributed, said out loud: `pool '{}': {}` fits every pool refusal
    and accounts for two characters of it.
    """
    best = None
    for bucket in (ERRS, ROWS, PROSE):
        for t in bucket:
            if t.rx.fullmatch(text) and (best is None
                                         or t.literal > best.literal):
                best = t
    return best


def near_by(origin, t):
    """Whether template `t` is written within six lines after `origin`."""
    path, _, line = origin.rpartition(":")
    tpath, _, tline = t.origin.rpartition(":")
    return (tpath == path and line.isdigit() and tline.isdigit()
            and 0 < int(tline) - int(line) <= 6)


def printed_whole(row, near=()):
    """
    The printer that put a value on a row of its own, or None.

    `print(f"    {path}")` is four literal characters and was refused entry
    as a matcher - it accepts every row anybody writes - but as the account
    of a row it is the right one: the path IS the row, and the printer is
    where to go and read. Two holes at most, because `%s %s %s %s` is a
    table row and not a value. There are twenty such printers in hexash.py,
    so the one written within a few lines of a string already accounting
    for this screen is the one cited, and the most literal of those: the
    `    line {}: {}` under the verdict printed the row under the verdict,
    not the `    {}` beside it.
    """
    fits = [t for t in LOOSE if t.literal and t.holes <= 2
            and t.rx.fullmatch(row)]
    close = [t for t in fits if any(near_by(o, t) for o in near)]
    if close:
        return max(close, key=lambda t: t.literal)
    return max(fits, key=lambda t: t.literal) if fits else None


def printer_under(near):
    """The printer of a value on a row of its own, near these origins.

    For a row the tiling accounted for with a CONSTANT: the text the tiling
    kept is the value without its indent, so the printer is found by where
    it is written and by its shape - one hole, nothing but whitespace round
    it - rather than by matching.
    """
    for o in near:
        close = [t for t in LOOSE if near_by(o, t) and t.holes == 1
                 and not t.text.replace(HOLE, "").strip()]
        if close:
            return close[0]
    return None


def plugin_fragment(row):
    """The longest run of the plugin's own words inside this row, or None.

    The dataplane's rows reach the screen through a VTY printer that strips
    the tag and re-indents (`remap:`), or prefixes a word (`warning:` on the
    timeout floor), so the plugin's template never fits the row whole. What
    can be said is which of its strings the row carries, and where.
    """
    best = None
    for t in ROWS + PROSE:
        if not t.origin.startswith("src/plugins/"):
            continue
        for frag in t.text.split(HOLE):
            frag = frag.strip()
            if (len(frag) >= LITERAL_FLOOR and frag in row
                    and (best is None or len(frag) > len(best[1]))):
                best = (t, frag)
    return best


def opens_as(text):
    """The product string this row starts with and then goes on past.

    An account and not an acceptance, so a template ending in a hole - which
    `heads_of` refuses as a first half, because its last wildcard would stop
    one character in - is allowed here: `prefix range refused for {}: mask
    length <= ge <= le <= {} does not hold (length {}` is the head of a row
    the product finishes with two more pieces under an `if`, and the row's
    account is that head and its line.
    """
    best = None
    for t in ERRS + ROWS + PROSE:
        if (t.literal >= PREFIX_FLOOR and t.rx.match(text)
                and (best is None or t.literal > best.literal)):
            best = t
    return best


def driven_by(row):
    for name, out in DRIVEN:
        if row in out:
            return name
    return None


def accounted(used):
    """The strings that accounted for the rows read so far, by origin.

    A string that is a module CONSTANT is named as one, with the printer
    that put it on the screen, rather than cited as if the line it is
    assigned on printed anything.
    """
    out = []
    for f in used:
        if f.t.constant:
            printer = printer_under([g.t.origin for g in used if g is not f])
            what = f"{f.t.constant}'s value ({f.t.origin})"
            if printer:
                what += f", printed whole through {printer.origin}"
            if what not in out:
                out.append(what)
        elif f.t.origin not in out:
            out.append(f.t.origin)
    return "; ".join(out)


def why_skipped(rows):
    """
    Where the reading of a screen nothing attributes stopped, in a sentence.

    Printed beside every skip. A list of pages and lines was the honest edge
    of the rule and it was also 22 lines nobody could act on without
    re-deriving each one, which #365 did by hand for the wiki. What is
    re-derivable is said here instead: how far the tiling got and by whose
    strings, and what the first row it could not account for looks like to
    the corpus. Every account names a line to go and read - a template under
    the literal floor with its share, the printer that put a value on the
    row whole, the plugin string the row carries, the run that printed the
    row, or the string the row opens as - and the check at the end holds it
    to that: "fits no string this product builds" was the label under four
    docs/ skips the branch review attributed by hand in an afternoon, which
    is the reading this file exists to do.
    """
    far, seen = None, rows
    # The self-test's markers carry two leading spaces some pages drop, and
    # the tiling is asked both ways above; the reason has to be read off the
    # same rows, or a template that fits the shifted row is reported as no
    # string at all.
    for shifted in (rows, ["  " + r for r in rows]):
        _ok, _used, got = tiling(shifted)
        if far is None or got["row"] > far["row"]:
            far, seen = got, shifted
    n, used = far["row"], far["used"]
    if n >= len(rows):
        return "accounted for row by row and refused whole"
    row = seen[n]
    mark = MARKED.match(row)
    text = row[row.index(mark.group(1)) + len(mark.group(1)):].lstrip() \
        if mark else row.strip()
    fit = over = None
    for over in (text, row, "  " + row):
        fit = loose_fit(over)
        if fit:
            break
    # The verdict row is printed by `err` and `bad`, and saying so accounts
    # for nothing; the rows under it are the ones a value printer puts there.
    printer = printed_whole(row, [f.t.origin for f in used]) if n else None
    plugin = plugin_fragment(row)
    # A shell row ending in a hole is folded by `say_list` at the console's
    # width, value included, and the value is the one thing this file has
    # not got: the row under it is the rest of that value, not a row of its
    # own. The Python side does not reach here - its folds are read whole.
    tail = used[-1].t if used else None
    if (tail is not None and tail.origin.startswith("appliance/")
            and tail.text.endswith(HOLE) and not fit and not plugin):
        what = (f"is the rest of the value in the last hole of "
                f"{tail.text.replace(HOLE, '{}')!r} ({tail.origin}), which "
                f"say_list folds at the console's width; this file cannot "
                f"fold a value it does not have")
    elif fit:
        share = fit.literal / max(1, len(over))
        what = (f"fits {fit.text.replace(HOLE, '{}')!r} from {fit.origin} "
                f"and is {share:.0%} literal, under the {LITERAL_SHARE:.0%} "
                f"floor")
    elif printer:
        what = (f"is a value printed whole through {printer.origin} "
                f"({printer.text.replace(HOLE, '{}')!r})")
        if plugin:
            what += (f", and the plugin builds it under the floor "
                     f"({plugin[0].origin}: {plugin[1]!r})")
    elif plugin:
        what = (f"is built by the plugin under the floor ({plugin[0].origin}: "
                f"{plugin[1]!r})")
    elif driven_row(row):
        what = (f"is a row the run {driven_by(row)!r} printed, under rows it "
                f"did not")
    else:
        head = opens_as(text)
        if head:
            what = (f"opens as {head.text.replace(HOLE, '{}')!r} from "
                    f"{head.origin} and goes on in pieces this file does not "
                    f"join")
        else:
            what = "fits no string this product builds"
    if n == 0:
        return f"the verdict row {what}"
    return (f"row(s) 1-{n} are the box's ({accounted(used)}); row {n + 1} "
            f"{what}: {row.strip()[:48]!r}")


def fold_shape(rows, box):
    """How the page's rows differ from the box's, for the `folded` finding.

    A page whose screen is ONE row against the box's one row is not a fold
    into one row, which reads as no fold at all: it is the headline cut
    short at the page's width, with its tail on an unindented row that
    `screens` does not read as part of the screen. Said as that.
    """
    if [r.strip() for r in rows] == [r.strip() for r in box]:
        return "at an indent the printer does not use"
    if len(rows) == 1 and len(box) == 1:
        return (f"the verdict row cut short at {len(rows[0])} columns where "
                f"the box's runs {len(box[0])}; what the page carries on "
                f"under an unindented row is not read as this screen")
    return (f"folded into {len(rows)} row(s) where the box prints "
            f"{len(box)}, the longest {max(map(len, box))} columns")


def folded(rows):
    """
    The fourth finding: the box's words, in rows the box does not print.

    Read against the DRIVEN runs only. The words of the quoted screen with
    its whitespace collapsed are compared with the words of every run of
    consecutive rows the product printed; when they agree and the rows do
    not, the page has the sentence and not the screen - folded at the page's
    width where the box writes the row whole, or set at an indent the
    printer does not use. Both were on the wiki once and both were found by
    reading the skip list by hand (#365); four of the fifteen skips under
    docs/ were the first kind. This is that reading, done by the file.

    A page that quotes FEWER rows than the box printed is not this: the
    verdict alone, or the verdict and its reason without the advice, agree
    with the box row for row as far as they go, and the comparison is over
    exactly as many of the box's rows as it takes to say the page's words.
    A single row cut short at a blank inside a longer row of the box's is
    the fold arriving from the other side - the headline itself broken at
    the page's width - and is read the same way, above AGREE_FLOOR so that a
    short verdict two screens happen to share is not a finding.

    Returns (scenario, the box's rows) or None.
    """
    words = " ".join(" ".join(r.split()) for r in rows)
    for name, out in DRIVEN:
        for i, first in enumerate(out):
            if not first.strip():
                continue
            said = []
            for k in range(i, len(out)):
                if not out[k].strip():
                    break
                said.append(" ".join(out[k].split()))
                joined = " ".join(said)
                if joined == words:
                    if out[i:k + 1] == rows:
                        return None
                    return name, out[i:k + 1]
                if (len(rows) == 1 and k == i and len(words) >= AGREE_FLOOR
                        and joined.startswith(words + " ")):
                    return name, out[i:k + 1]
                if not joined.startswith(words[:len(joined)]):
                    break
    return None


def gapped(rows):
    """
    The fifth finding: the box's rows with a row missing from the middle.

    `folded` reads a page that quotes FEWER rows than the box printed as
    honest, and it is - when what is missing is the tail. What it is not is
    a page whose two rows the box printed with a row between them: the
    verdict and the loader's refusal under it, with the advice the check
    prints between the two left out, is a screen the box does not print,
    and docs/appliance.md carried the #121 boot failure in that shape while
    saying it was the shape the box prints today. The template tiling
    accepted it, because each row is the box's.

    Read against the DRIVEN runs only, on every pair of consecutive page
    rows: both rows are rows of one run at the same indent, the second is
    not the row after the first, and it is a row further down before a
    blank. A pair the run prints adjacent anywhere is not a gap, so a row a
    run repeats does not make one. The indent is the page's - the self-test
    quotes the service's journal ten columns in - so the shift is read off
    the pair rather than tried at two widths.

    Returns (scenario, the box's rows from the first to the second) or None.
    """
    def shift(page, box):
        k = len(page) - len(box)
        if k >= 0 and page.endswith(box) and not page[:k].strip():
            return k
        return None

    for a, b in zip(rows, rows[1:]):
        for name, out in DRIVEN:
            gap = None
            for i in range(len(out) - 1):
                k = shift(a, out[i])
                if k is None or not out[i].strip():
                    continue
                if shift(b, out[i + 1]) == k:
                    gap = None
                    break
                for j in range(i + 2, len(out)):
                    if not out[j].strip():
                        break
                    if shift(b, out[j]) == k:
                        gap = gap or (name, out[i:j + 1])
                        break
            if gap:
                return gap
    return None


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
    # the paragraph was ever meant to be the message it half agrees with, and a
    # screen nothing can account for must stay a skip rather than become a
    # finding about the wrong message.
    #
    # TWO WAYS OF RECOGNISING IT, because a template is only one of them. A
    # refusal whose headline is mostly interpolated - `router bgp 65001: bgpd
    # is not running` is sixteen literal characters out of thirty-seven - will
    # never have a template, so with the template alone a word edited in the
    # PARAGRAPH under it could not be a finding either: the screen dropped off
    # the driven list and onto the skip list, and the run stayed green.
    # Measured over seven such screens, one word edited in each: reported
    # once before and three times after. A row this file has just watched the
    # product print is the product's row on better evidence than any template
    # gives.
    if verdict_template(rows[0]) is None and not driven_row(rows[0]):
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


def swallows(rows):
    """
    The third finding: a hole took a word the box has stopped printing.

    `{} does not keep what it received` opens with a hole, and a hole is
    `[^\n]+?`, so the page's `neighbour 200.150.0.2` goes into it WHOLE and
    the row matches. #363 took that noun out of the product and the page kept
    it for a session, with this file green on both sides of the change. The
    other four defences are all about how much of a row is hole and this row
    is 33 literal characters - they were right to pass it. What was never
    asked is what the hole is ALLOWED to hold.

    So: the template accounts for the row, most of the row is its literal
    text, and one hole the SOURCE says holds a single value came out holding
    two words. That is a page carrying a word the box does not print, and it
    is named rather than skipped.

    Read on the VERDICT ROW ONLY, which is where the evidence is strongest
    and the narrower of the two widths that were measured. An `err` headline
    is one clause and its slots are an address, an interface, a count - the
    paragraphs under it are where a slot legitimately holds a sentence, and a
    finding there would be this file going red on a page nobody got wrong.
    Failing to MATCH on a wide hole is cheap - the screen lands on the
    printed skip list, which is where a screen this file cannot account for
    belongs; calling a page WRONG is not, so the loud half is taken only on
    the row this product writes shortest.

    The longest template wins, and between two of the same length the one
    whose hole swallowed least: both send the reader to the message the page
    was actually quoting rather than to some other one that also fits.
    """
    mark = MARKED.match(rows[0]).group(1)
    after = rows[0][rows[0].index(mark) + len(mark):].lstrip()
    best = None
    for text in (after, rows[0]):
        for bucket in (ERRS, ROWS, PROSE):
            for t in bucket:
                m = t.rx.fullmatch(text)
                if not m or t.literal < LITERAL_SHARE * len(text):
                    continue
                took = t.swallowed(m)
                if not took:
                    continue
                key = (t.literal, -len(took[1]))
                if best is None or key > best[0]:
                    best = (key, t, took[0], took[1])
    return best[1:] if best else None


# ---------------------------------------------------------------------------
# The report.

def per_tree(counts):
    return ", ".join(f"{tree}/ {counts.get(tree, 0)}" for tree, *_ in TREES)


print(f"=== reads {len(TREES)} directories: " + ", ".join(
    f"{tree}/ ({sum(1 for s in SCREENS if s[0] == tree)} quoted screen(s) "
    f"carrying a verdict, on {len(PAGES[tree])} page(s))"
    for tree, *_ in TREES) + " ===")
print(f"  corpus: {len(ERRS)} refusal headline(s), {len(PROSE) + len(SPROSE)} "
      f"paragraph(s), {len(ROWS)} whole row(s) - from the VTY, the appliance "
      f"scripts and the plugin")
print(f"  {len(TOO_LOOSE)} template(s) refused entry: under {LITERAL_FLOOR} "
      f"literal characters, so they would match rows nobody wrote")
print(f"  {len(SCENARIOS) + len(FINDINGS)} scenario(s) driven through the VTY")

# What the whitespace rule actually reaches, printed for the same reason the
# refused-entry count is: a rule whose reach is not on the screen is one
# nobody can watch stop working. If a refactor turned every slot into
# something this file reads as a phrase, every count below would still be
# green and the first number here would be the only place it showed.
SLOTS = [slot for bucket in (ERRS, ROWS, PROSE) for t in bucket
         for slot in t.slots]
ONE_VALUE = [s for s in SLOTS if s.one_value]
BY_NAME = {}
for slot in SLOTS:
    if not slot.one_value and slot.expr != "?":
        word = [w for w in re.findall(r"\w+", slot.expr)
                if w.lower() in PROSE_WORDS]
        if word:
            BY_NAME[word[-1]] = BY_NAME.get(word[-1], 0) + 1
OPENS = [t for bucket in (ERRS, ROWS, PROSE) for t in bucket
         if t.text.startswith(HOLE)]
print(f"  the corpus's holes: {len(ONE_VALUE)} the source says hold ONE "
      f"VALUE and may not contain whitespace, "
      f"{len(SLOTS) - len(ONE_VALUE)} a phrase - of those, "
      f"{sum(BY_NAME.values())} by the word the author wrote "
      f"({', '.join(f'{k} x{v}' for k, v in sorted(BY_NAME.items()))})")
print(f"  {len(OPENS)} template(s) OPEN with a hole, "
      f"{len([t for t in OPENS if t.slots[0].one_value])} of them on a hole "
      f"that holds one value - the shape that swallowed `neighbour `")

driven, matched = {}, {}                # per tree
loosest = None
skipped = []
findings = []
spaced = []                             # accepted holes holding whitespace

# The ceiling, in the idiom test_hexash_refusals.py argues for: what is left
# is NAMED on every run and the number it may not exceed is written down,
# because an allowlist is read once and grown forever. These four are the
# accepted matches that still lean on a hole holding more than one word.
# Three are a `${...}` or a `$var` in a shell script - the one language here
# whose slots this file cannot read, so it asks them for nothing - and the
# fourth is `UNCHECKED_MARK.strip()`, a call on a constant whose value is the
# two words `<- not checked`; a call is a phrase to `py_slot`, and the hole is
# honest about holding one. It was 2 while this file read the wiki alone and
# went to 4 when docs/ joined (#384): docs/appliance.md:373 quotes the
# installer row the wiki quotes twice, and docs/routing.md:4070 is the
# `<- not checked` mark - the same two shapes on a second tree, not a third
# one. And 5 when #345 landed beside it: the self-test's `rx queues
# requested: num-rx-queues $x` row (appliance/selftest) is a fourth shell
# slot, the one shape this ceiling was written to tolerate, quoted by the
# wiki's operations page. Reading the shell's slots would take this to one;
# it may not go up again without a reading like that, and a page joining the
# corpus is not one.
SPACED_AT_MOST = 5

#: Where the self-test puts the service's journal under its verdict:
#: `sed 's/^/          /'` at appliance/selftest:1519, ten columns. A screen
#: the VTY printed at boot is quoted at that indent on a page quoting the
#: self-test, and it is the box's rows there, not a fold of them.
JOURNAL_INDENT = 10

for tree, rel, lineno, rows in SCREENS:
    # The self-test's markers carry two leading spaces of their own and some
    # pages quote them without. The offset is uniform when it happens, so it
    # is put back rather than compared away - a row that differs by a space
    # somewhere in the middle still has to fail. The third shift is the
    # journal's, the other way: taken off, because the VTY printed the row
    # without it.
    shifts = [rows, ["  " + r for r in rows]]
    if all(r.startswith(" " * JOURNAL_INDENT) for r in rows):
        shifts.append([r[JOURNAL_INDENT:] for r in rows])
    name = None
    for shifted in shifts:
        name = driven_match(shifted)
        if name:
            break
    if name:
        driven[tree] = driven.get(tree, 0) + 1
        continue
    # Before a template is asked: a screen whose words a driven run printed,
    # in rows it did not. Driving beats matching here too - a template would
    # accept the page's fold at the console's width and never see that the
    # box writes the row whole.
    found = None
    for shifted in shifts:
        bent = folded(shifted)
        if bent:
            found = ("folded",) + bent
            break
    if found:
        findings.append((tree, rel, lineno, rows, found))
        continue
    used = None
    for shifted in shifts:
        used = attribute(shifted)
        if used:
            break
    if used:
        matched[tree] = matched.get(tree, 0) + 1
        for f in used:
            t = f.t
            for slot, got in f.holes():
                if re.search(r"\s", got):
                    spaced.append((rel, lineno, t.origin, slot, got))
            share = t.literal / max(1, t.literal + 12 * t.holes)
            if loosest is None or (t.holes, -share) > loosest[0]:
                loosest = ((t.holes, -share), t)
        continue
    for shifted in shifts:
        goes_on, stops = stale(shifted), diverges(shifted)
        if goes_on:
            found = ("goes on past it",) + goes_on
        elif stops:
            found = ("stops agreeing with it",) + stops
        else:
            took = swallows(shifted)
            if took:
                found = ("swallowed",) + took
        if found:
            break
    if found:
        findings.append((tree, rel, lineno, rows, found))
    else:
        skipped.append((tree, rel, lineno, rows[0], why_skipped(rows)))

# And the blocks, as the box printed them: a row of the box's missing from
# between two the page kept is not in any screen above.
for tree, rel, lineno, rows in RUNS:
    gap = gapped(rows)
    if gap:
        findings.append((tree, rel, lineno, rows, ("gapped",) + gap))

print(f"\n  screen(s) matched by DRIVING the product - compared character "
      f"for character: {per_tree(driven)}")
print(f"  screen(s) matched a TEMPLATE the product builds: "
      f"{per_tree(matched)}")
print(f"  screen(s) SKIPPED, nothing here can attribute them: "
      + per_tree({t: sum(1 for s in skipped if s[0] == t)
                  for t, *_ in TREES}))
if loosest:
    (holes, share), t = loosest
    print(f"  the loosest match any of those leaned on: {holes} hole(s), "
          f"{t.literal} literal character(s), {-share:.0%} literal with a "
          f"hole counted as twelve, from {t.origin}")
    print(f"    {t.text.replace(HOLE, '{}')!r}")

for tree, *_ in TREES:
    mine = [s for s in skipped if s[0] == tree]
    if not mine:
        print(f"\n  {tree}/: every quoted screen is accounted for")
        continue
    print(f"\n  {tree}/: what this file cannot see, which is the edge of the "
          f"rule, and where the reading stopped:")
    for _tree, rel, lineno, row, why in mine:
        print(f"    {rel}:{lineno}  {row.strip()[:64]}")
        print(f"        {why}")

if findings:
    print()
for tree, rel, lineno, rows, (how, t, *rest) in findings:
    row = rows[0]
    if how == "folded":
        box = rest[0]
        shape = fold_shape(rows, box)
        check(False,
              f"{rel}:{lineno} quotes the box's words in rows the box does "
              f"not print: {shape}. Driven as '{t}', the box prints:\n"
              + "\n".join(f"          |{r}" for r in box))
        continue
    if how == "gapped":
        box = rest[0]
        check(False,
              f"{rel}:{lineno} quotes the box's rows with a row missing from "
              f"the middle: {box[0].strip()[:48]!r} and then "
              f"{box[-1].strip()[:48]!r}, which the box prints "
              f"{len(box) - 2} row(s) later. Driven as '{t}', between and "
              f"including the page's two rows the box prints:\n"
              + "\n".join(f"          |{r}" for r in box))
        continue
    if how == "swallowed":
        slot, got = rest
        check(False,
              f"{rel}:{lineno} quotes a screen the box does not print. It "
              f"matches {t.text.replace(HOLE, '{}')!r}, which {t.origin} "
              f"builds, only because the hole for {slot.expr} - one value in "
              f"the source - swallowed {got.strip()!r}. The page carries a "
              f"word this box does not print, or the value is in the wrong "
              f"place: read the row off the box")
        continue
    check(False,
          f"{rel}:{lineno} quotes a screen the box does not print. It starts "
          f"as {t.text.replace(HOLE, '{}')!r}, which {t.origin} builds, and "
          f"then {how}: {rest[0].strip()[:60]!r}")


# What is left of the loosening, named rather than counted. A hole that holds
# two words is either a slot this file could not read or a page carrying a
# word the box does not print, and the first is the only one allowed here.
spaced.sort()
print(f"\n  {len(spaced)} accepted match(es) lean on a hole holding more than "
      f"one word, against a ceiling of {SPACED_AT_MOST}:")
for rel, lineno, origin, slot, got in spaced:
    print(f"    {rel}:{lineno}  {origin}  the hole for {slot.expr} "
          f"({slot.why}) holds {got!r}")
check(len(spaced) <= SPACED_AT_MOST,
      f"{len(spaced)} accepted match(es) lean on a hole holding more than one "
      f"word, above the ceiling of {SPACED_AT_MOST}. A hole that swallows a "
      f"word is how a page keeps a word the box stopped printing - read the "
      f"slot, or read the row off the box; do not raise the number")
check(len(spaced) >= SPACED_AT_MOST,
      f"the ceiling says {SPACED_AT_MOST} and only {len(spaced)} accepted "
      f"match(es) lean on a wide hole: lower it, or it stops holding "
      f"anything")


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

# A word the box stopped printing, put back into the row it was taken out of.
# This is #363's own change to `{args[1]} does not keep what it received`, and
# the wiki carried the old form for a session with this file green on both
# sides of it: the template opens with a hole, and `neighbour 200.150.0.2`
# went into the hole whole. Both halves are asserted, because the rule is
# worth nothing if the corrected row stops being attributable.
_KEPT = ["error: 200.150.0.2 does not keep what it received"]
_WORD_BACK = ["error: neighbour 200.150.0.2 does not keep what it received"]
check(attribute(_KEPT), "the verdict #363 left is no longer attributed to the "
      "box that prints it, so the rule below is being read over nothing")
check(not attribute(_WORD_BACK),
      "a verdict carrying a word the product does not print is still "
      "attributed: the hole swallowed it")
_took = swallows(_WORD_BACK)
check(_took and _took[2].strip() == "neighbour 200.150.0.2",
      f"and nothing names what the hole swallowed: {_took and _took[2]!r}")

# The other side of that rule, and the one that decides whether it can be
# lived with: a slot the SOURCE says is a sentence is not asked for a single
# token. Asserted on `py_slot`, which is where it is decided, over the two
# expressions the measurement turned on - `args[1]` is the operator's word
# and `r.reason` is whatever the dataplane said.
check(py_slot(ast.parse("args[1]", mode="eval").body).one_value,
      "a subscript is not read as one value, so the rule reads nothing")
check(not py_slot(ast.parse("r.reason", mode="eval").body).one_value,
      "the dataplane's own sentence is read as one value, and every screen "
      "quoting a refusal it forwarded would be called wrong")
check(not py_slot(ast.parse('"/".join(sorted(n))', mode="eval").body)
      .one_value, "a joined list is read as one value")
check(not py_slot(ast.parse('f"{a} and {b}"', mode="eval").body).one_value,
      "a phrase composed out of pieces is read as one value")

# The box's words in rows the box does not print. The announce warning as
# docs/routing.md carried it until #384: the same three rows the product
# writes whole, hand-folded at 72 columns into five. Nothing may attribute
# it, and `folded` has to name it - and the box's own rows, and the verdict
# quoted alone, have to stay clear of it, because a finding that fires on a
# page quoting less of a screen is a finding people run around.
_ANNOUNCE = next(out for name, out in DRIVEN if name == "a pool nothing "
                 "announces")
_ANNOUNCE = [r for r in _ANNOUNCE if r.strip()]
_HAND_FOLDED = [
    "warning: pool 'clientes': nothing here announces 200.150.0.16/28",
    "    subscribers behind it would be translated to an address the internet",
    "    cannot route back to",
    "    announce it from another box, or originate it here with",
    "    'ip route 200.150.0.16/28 blackhole' and 'network 200.150.0.16/28'",
    "    under the BGP address family"]
check(not driven_match(_HAND_FOLDED) and not attribute(_HAND_FOLDED),
      "a screen hand-folded where the box writes the row whole is accepted")
_bent = folded(_HAND_FOLDED)
check(_bent and _bent[1] == _ANNOUNCE,
      f"and nothing names it as the box's words in rows the box does not "
      f"print: {_bent!r}")
check(folded(_ANNOUNCE) is None,
      "the rows the box prints are called a fold of themselves")
check(folded(_ANNOUNCE[:1]) is None and folded(_ANNOUNCE[:2]) is None,
      "a page quoting the verdict without the advice under it is called a "
      "fold")
_SHIFTED = [_ANNOUNCE[0]] + [" " + r for r in _ANNOUNCE[1:]]
check(folded(_SHIFTED) and folded(_SHIFTED)[1] == _ANNOUNCE,
      "the box's rows one space too far right are not named")
# And the headline broken at the page's width, which is the same fold from
# the other side: the first row alone, cut at a blank.
_CUT = [_ANNOUNCE[0][:_ANNOUNCE[0].rindex(" ")]]
check(folded(_CUT) and folded(_CUT)[1] == _ANNOUNCE[:1],
      f"a verdict row cut short inside the box's own row is not named: "
      f"{_CUT!r}")

# A row missing from the MIDDLE of the box's screen, which is the shape
# docs/appliance.md carried the #121 boot failure in: the verdict and the
# advice with the reason between them left out. `folded` reads a screen cut
# short at its end as honest, so the gap has its own finding, and the box's
# own rows and the box's rows cut short at the end have to stay clear of it.
_GAPPED = [_ANNOUNCE[0], _ANNOUNCE[2]]
check(not driven_match(_GAPPED),
      "the announce warning with its reason row left out is driven as the "
      "box's screen")
_gap = gapped(_GAPPED)
check(_gap and _gap[1] == _ANNOUNCE[:3],
      f"and nothing names the row missing from its middle: {_gap!r}")
check(gapped(_ANNOUNCE) is None and gapped(_ANNOUNCE[:2]) is None,
      "the box's rows, whole or cut short at the end, are called a gap")
check(gapped(["  " + _ANNOUNCE[0], "  " + _ANNOUNCE[2]])
      and gapped(["  " + _ANNOUNCE[0], _ANNOUNCE[2]]) is None,
      "the gap is not read at the page's indent, or is read across two "
      "indents")

# And the headline cut short is said as that. The `folded` finding above
# fires on `_CUT` with one row against one row, and "folded into 1 row(s)
# where the box prints 1" reads as no fold at all - which is what the report
# said for a five-row screen whose tail sat under an unindented row.
check("cut short" in fold_shape(_CUT, _ANNOUNCE[:1]),
      f"a verdict row cut short at the page's width is reported as a fold "
      f"into one row: {fold_shape(_CUT, _ANNOUNCE[:1])!r}")

# The pool refusal docs/routing.md:1959 carried until this branch was
# reviewed: the box's one row, split at the parenthesis onto a second. The
# refusal is driven now - `Vat2.call` composes the reason, `err_dataplane`
# prints it - so the split is a fold and `folded` has to name it, and the
# box's own row has to be the driven one.
_POOL = next(out for name, out in DRIVEN
             if name == "a pool the dataplane refused, named")
_POOL = [r for r in _POOL if r.strip()]
check(len(_POOL) == 1 and _POOL[0].startswith(
    "error: pool 'client-bad': the dataplane refused it: invalid value ("),
    f"the pool refusal is not driven as the box's one row: {_POOL!r}")
_SPLIT = [_POOL[0][:_POOL[0].index(" (")],
          "    " + _POOL[0][_POOL[0].index("("):]]
check(not driven_match(_SPLIT) and not attribute(_SPLIT),
      "the pool refusal split at its parenthesis is accepted as a screen")
_bent = folded(_SPLIT)
check(_bent and _bent[1] == _POOL,
      f"and nothing names the split as the box's row in rows the box does "
      f"not print: {_bent!r}")

# The session-table refusal folded back into the three rows docs/routing.md
# carried before #384 - the correction the review found held by nothing,
# because no run drove the row. One row of 169 columns, driven now.
_TABLE = next(out for name, out in DRIVEN
              if name == "the session-table refused once pools exist")
_TABLE = [r for r in _TABLE if r.strip()]
_TABLE_FOLDED = [
    "error: cgnat session-table: the dataplane refused it: the change is "
    "refused",
    "while it is in use (session-table: tables already created; set this "
    "before",
    "configuring pools)"]
check(len(_TABLE) == 1 and len(_TABLE[0]) == 169,
      f"the session-table refusal is not driven as one row of 169 columns: "
      f"{_TABLE!r}")
_bent = folded(_TABLE_FOLDED)
check(_bent and _bent[1] == _TABLE,
      f"the session-table refusal folded at the page's width is not named "
      f"as a fold: {_bent!r}")

# The fake dataplane's `remap:` rows are the plugin's format strings filled
# in, and the reading that makes them so is asserted: a row built here has
# to be one the plugin's own template accepts, and the plugin's templates
# have to be in the corpus at all.
_REMAP_ROWS = [row for _name, setup, _lines in SCENARIOS
               for row in setup.get("remap", [])]
check(len(_REMAP_ROWS) == 12,
      f"the re-mapping scenarios answer {len(_REMAP_ROWS)} rows, not the "
      f"twelve three pages quote")
for _row in _REMAP_ROWS:
    check(any(t.origin.startswith("src/plugins/") and t.rx.fullmatch(_row)
              for t in ROWS),
          f"a row the fake dataplane answers is not one the plugin builds: "
          f"{_row!r}")

# The fake dataplane's timeout row is the plugin's, marked exactly when the
# plugin would mark it: a value under the floor bpa_pool.h sets carries the
# mark, one at the floor does not, and the run relayed the marked row under
# `warning:` - which is the row the wiki quotes.
check("BELOW the floor" in timeout_row("udp", 119)
      and "BELOW" not in timeout_row("udp", 120),
      "the fake dataplane marks a timeout the plugin would not, or leaves "
      "one the plugin marks: the row it answers is not the plugin's")
_TIMEOUT = next(out for name, out in DRIVEN
                if name == "a timeout under the floor its RFC sets")
check(any(r.startswith("warning:  udp") and "BELOW the floor" in r
          for r in _TIMEOUT),
      f"the marked timeout row was not relayed under `warning:` by the "
      f"commit: {_TIMEOUT!r}")

# The shell reader follows a printer call behind a guard and in a `case`
# arm. Asserted on the corpus: the installer's two rows, which were on the
# skip list for docs/appliance.md until the reader read past `&&` and `)`.
_GUARDED = "  [ ok ] fstab: " + HOLE + " UUID(s), every one of them resolves"
_ARM = "  [ ok ] root has a password, so somebody can log into this box"
check(any(t.text == _GUARDED for t in ROWS),
      "a printer call written after `&&` is not in the corpus")
check(any(t.text == _ARM for t in ROWS),
      "a printer call written in a `case` arm is not in the corpus")
check(SH_CALL.match('  say "        and ok to ignore"').group(1) == "say",
      "a verb inside a printed sentence is taken for the call")

# And every skip says where the reading stopped, in words that name a row.
check(all(re.search(r"the verdict row|row\(s\) 1-\d+ are the box's", why)
          for *_, why in skipped),
      "a skipped screen is printed with no account of where its reading "
      "stopped")
# And names a line to go and read: a skip whose reason is a label is a skip
# somebody re-derives by hand, which is what the review of this branch did
# for four of them.
_unread = [f"{rel}:{lineno}  {why}" for _t, rel, lineno, _row, why in skipped
           if not re.search(r"\S+:\d+", why)]
check(not _unread,
      "a skipped screen is printed with no source line to go and read:\n"
      + "\n".join("          " + u for u in _unread))

# And the corpus is not empty, which is the last way all of this reads green
# over nothing at all.
check(len(ERRS) > 100 and len(PROSE) > 100 and len(ROWS) > 100,
      f"the corpus is {len(ERRS)}/{len(PROSE)}/{len(ROWS)}, which is not a "
      f"reading of this product's sources")
check(len(ONE_VALUE) > 1000,
      f"only {len(ONE_VALUE)} hole(s) in the corpus are held to one token, "
      f"so whatever the rule above is reading it is not this product")
print("  a glued refusal, a glued pair, an invented one, an empty "
      "template, a wrong fold, a swallowed word, a sentence asked for one "
      "token, a screen hand-folded where the box writes it whole, a verdict "
      "cut short inside the box's row, a dataplane refusal split at its "
      "parenthesis or folded at the page's width, a screen one space too far "
      "right, a screen with a row missing from its middle, a fake dataplane "
      "answering words the plugin does not build or marking a timeout it "
      "would not, a run that printed nothing, a skip with no line to read "
      "and an empty corpus are each refused; a printer call behind `&&` or "
      "in a `case` arm is read")

print()
if fail:
    print(f"{fail} FAILURES")
    sys.exit(1)
print("ALL TESTS PASSED")
