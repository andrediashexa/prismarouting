#!/usr/bin/env python3
"""
Every function in the product is named by a unit test, or says at its
definition why it cannot be, and the count of the ones that are neither is a
ceiling that may only go down.

WHY A READER. The suites in test/suites.list are organised by behaviour: a
refusal, a commit, a lab. A function can exist with nothing calling it and
only a skeptic with a profiler finds out; #385 had two checks that stopped
running without going red, found that way. The maintainer's rule of
2026-09-17 (#390) is that every function has a unit test, and a rule with no
reader is a wish. This file is the reader: the definitions are parsed out of
the product, the calls are parsed out of the tests, and the function no test
names is printed by name on every run.

WHAT A FUNCTION IS, per language, measured against this tree:

  python  every `def` the `ast` module finds in src/prismash/*.py and in the
          scripts under appliance/ whose shebang names python3, methods and
          nested functions included, harness files (test_*) excluded.
  c       the plugin's own definition shape in src/plugins/bpa/*.c: a return
          type alone on one line and `name (` at column 0 on the next, which
          is the VPP style every definition in the tree follows. A VPP macro
          at column 0 (VLIB_CLI_COMMAND, VLIB_NODE_FN, VLIB_REGISTER_NODE) is
          upper case and does not match, so nothing has to name them. The
          static_always_inline helpers ARE counted: they are functions.
  shell   `name() {` at column 0 in appliance/*.sh, appliance/selftest,
          appliance/install.sh, tools/*.sh and lab/lib.sh, harness files
          excluded.

WHAT NAMING IT MEANS. A unit test is a file under test/ or an appliance/
test_*.py, and it names a function only in a context that is not a comment:

  python  the AST of the test file holds a Call whose func is the bare name
          (and the test file does not define that name itself, so a test's
          own `check()` is not the product's) or an attribute `.name(`. A
          method's `__init__` is named by calling its class.
  c       a harness test_*.c calls `name (` outside a comment.
  shell   a test_*.sh that reaches the script (its basename appears outside
          a comment) and calls the name in command position: outside a
          comment, outside a quoted string unless the string is a `$(...)`
          or is handed to `sh -c`, and without defining the name itself.

WHAT IT CANNOT SEE, stated so nobody reads the rule as wider than it is: a
same-named function in two product files is named by one call to either; a
property read without a call, a dunder reached through an operator, and a
function handed to a caller by reference are not calls and do not count,
which is the safe direction. A shell function nested inside another is not
at column 0 and is not counted. tools/ipfix_collector.py and the appliance
scripts with no .sh suffix are outside the sets above and are not read.

THE EXCUSE. A function that cannot be tested in isolation - a `main`, the
plugin's init that VPP calls, a node function that needs the graph - says so
at its definition, on the comment line(s) directly above it:

    # not a unit: <reason>          python and shell
    /* not a unit: <reason> */      c

and is printed as excused WITH its reason and counted apart. A marker with no
reason is refused: the escape from the count costs a sentence, or it becomes
the way every new function opts out.

THE CEILINGS, and there is no allowlist. An allowlist is read once and grown
forever. So the functions no test names are NAMED on every run, the number
per language they may not exceed is written down with the date it was
measured, and lowering it is the only way to change it - the idiom
test_off_run_coverage.sh uses for labs with no off run and
test_prismash_refusals.py for refusals nothing has printed. A batch of tests
that lands lowers the number; a function added with no test goes red with
nothing to subtract from.
"""
import ast
import builtins
import os
import re
import sys
import textwrap

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF = os.path.abspath(__file__)
WIDTH = 79
MARKER = "not a unit:"

# Measured 2026-09-17 on master 38fdd4a, the day the rule was set (#390):
# 469 python functions, 194 named by a test and 3 excused; 210 c functions,
# 11 named and 4 excused; 40 shell functions, 4 named and none excused. The
# issue's own grep said 447, 201 and 56: it did not see nested defs, C names
# with a digit in them, or that fourteen of the shell functions belong to
# harness files. These go down with every batch of #390 and never up.
# 2026-09-17, #390 batches A to H: every Python and shell function at 0
# but for the excusals, which print with their reasons; C holds what the
# plain-cc harness reaches, the rest waits for the VPP build.
# 2026-09-20, #390 step 1 of the decision taken on #21's successor: 171 to 130.
# 2026-09-21: 129 to 115. Fourteen functions that a plain-cc harness cannot
# reach now say so at their definitions, which is the other half of this
# rule rather than a way round it: six in bpa_pptp.c and two in bpa_ftp.c are
# not compiled under BPA_STANDALONE at all - that guard is what leaves the
# text and arithmetic halves of those ALGs testable - four are VPP format
# functions over a va_list, the class bpa_cli.c's header already describes,
# and two open and read a UDP socket inside VPP's main loop. Each names the
# lab that drives it.
# 2026-09-21: 130 to 129. bpa_ranges_overlap() moved from bpa.c to
# bpa_pool.h, beside the type it is about, so test_bpa_pool.c can drive it -
# it is the rule that refuses a pool overlapping another (#73), and a
# half-open interval has every way of being wrong at its boundaries. Moving
# a function to where a harness reaches it is the way this number goes down
# for anything bpa.c holds: that file needs a VPP to link.
# bpa_cli.c's forty-one rendering functions are excused at their definitions -
# thirty VLIB_CLI_COMMAND bodies, eight format functions over a va_list, and
# two that write through vlib_cli_output() - each with the class it belongs to
# and a pointer to that file's header, where the argument is made once instead
# of forty-one times. What holds them instead is named there too:
# test_plugin_cli_says.py requires every message the file can print to be named
# by a lab, a suite or a document. The arithmetic in the same file - the
# comparator, the counter total, the subscriber lookups - is NOT excused and
# waits for the VPP-linked harness, which is the other half of that decision.
# 2026-09-18, #21: 161 to 171. The identity table's loader, its statement and
# its screen, and the API handler that carries the path: the loader swaps the
# table under the worker barrier, two are a VLIB_CLI_COMMAND body each and one
# is an API handler, and the fifth is bpa_log_tcp_enable's wrapper, which is
# the IPFIX stream's enable under the name its callers use; the sixth and
# seventh are the identity loader and the kind it takes, which swap the table
# under the worker barrier, one is the API handler that carries the kind, and
# the last three are the RADIUS listener's socket half - enable, service and
# the handler that carries the secret. All of them need the VPP build: a
# socket, a barrier, or a CLI body. The
# parse, the search and the unknown marker came with their own harness and are
# not in this count.
# 2026-09-18, #20: 159 to 161. The syslog transport added a VLIB_CLI_COMMAND
# body and an API handler, both in the half of the plugin that needs the VPP
# build - which the reader now says on every run, so the number explains
# itself: 161 of 161 are behind that boundary. The line formatter and the
# stream functions that carry it came with their own harnesses and are not in
# this count.
# 2026-09-17, #390 batch I: 187 to 159. Five more product files were brought
# within reach of the plain-cc harness by the BPA_STANDALONE guards their
# headers already carried - the FTP ALG's text half (9), the PPTP ALG's three
# segment-local functions, the IPFIX-over-TCP exporter (15) and the
# configuration trail's hex reader. What is left in those files needs a
# buffer, a session pool, a bihash, VPP's format() or the CLI, and waits for
# the VPP build with bpa_log.c, which needs the ipfix-export infrastructure
# for all eight of its functions.
# 2026-09-22: 115 to 88. Twenty-seven more that say so at their definition,
# and both groups are whole files rather than a pick: the twelve in
# bpa_audit.c live under `#ifndef BPA_STANDALONE` - the guard that file's
# header explains, which compiles exactly one function standalone, the hex
# reader that decides whether a line of the trail carries a digest at all -
# and the fifteen in bpa_api.c are the binary API, dispatched from a message
# on the shared-memory segment and replying through REPLY_MACRO. Neither
# exists outside a running VPP. What they do is measured elsewhere and the
# excusals name where: lab/audit.sh drives the trail on a box, lab/vty.sh
# drives what the VTY sends through the API, and test/test_api_widths.py
# reads the widths of the message fields off the .api file.

# 2026-09-22, later: 88 to 85, and these three went down the OTHER way -
# they are tested now. The arithmetic over a pool's static forwards took a
# pool, and so a VPP vector, so a harness with no vectors could not call any
# of it: where a forward belongs in the ordered list, which forward stands
# for a subscriber's block, and whether the one just removed was the last.
# They take (v, n) now, the vector stays with the one caller in bpa.c that
# has one, and test/test_bpa_static.c drives them over plain arrays. The
# tie-goes-after rule is the one the VTY's commit diff rests on and it was
# held by nothing.

# 2026-09-22, third batch: 85 to 82, and the three are three different
# answers to the same question. bpa_static_clash() is the DECISION taken out
# of bpa_static_conflict(): which of the two forwards cannot both hold, and
# that a difference of protocol or of public address is not a clash at all -
# tested. What is left of that function is the sentence, which needs VPP's
# format(), and it says so. bpa_check_local_overlap() walks VPP's interface
# pool and bpa_check_pool_overlap() walks bpa_main's pools; both say so, and
# both name the lab that produces their refusal on a box.

# 2026-09-22, fourth: 82 to 81, tested. bpa_pool_remap_count() answers the
# one question an operator has before committing a change to a pool that is
# carrying traffic - how many subscribers it would move - and it needed
# nothing but two pools, which is why it moved to bpa_pool.h and
# test/test_bpa_pool.c drives it. An address the new definition does not
# serve AT ALL counts as moved, and that rule was the one nothing held: its
# mutation is the narrowing case, and only that case tells the two apart.

# 2026-09-22, fifth: 81 to 80, and the same split as the third - the
# DECISION out of the function that words it. Whether a forward can be made
# at all is five different things for an operator to do, and each was a
# branch inside a function that needs VPP's format() to say them. It is
# bpa_static_check() now, and bpa_proto_t moved to bpa_pool.h with it:
# comparing a protocol against a second copy of those values, written where
# the harness could reach it, would be a copy that can disagree.

# 2026-09-22, sixth: 80 to 72. Two more decisions split from the sentences
# that carry them - whether a stride reaches every worker, and how many
# workers a reserve misses - and six functions that write those sentences to
# a vlib_main_t's CLI or log saying so. The floor of one worker stays in
# bpa.c, which is the one place that reads how many this box has; what moved
# to bpa_pool.h is the arithmetic, and test/test_bpa_pool.c derives its cases
# from the pool's own stride unit rather than from numbers that happened to
# work.

# 2026-09-22, seventh: 72 to 71. Which forward an operator meant, when they
# named a protocol and a public port and not a subscriber: a pool has more
# than one public address, so the same port can be forwarded to several
# customers, and answering with the first would end one of their inbound
# service. The count and the two to name are bpa_static_on_port() now; the
# refusal that names them stays where format() is.

# 2026-09-22, eighth: 71 to 46, and this is the rest of bpa.c. What is left
# in that file after six batches of splitting decisions out of it is the part
# that IS VPP: the pool of pools and its hash, the worker barrier, the
# per-worker allocators, vnet's features and MTUs, and the graph. Each of the
# twenty-five says which of those it needs and where the behaviour is
# measured instead - twelve name a lab, nine name another suite, and the four
# validators name the decisions they orchestrate, all of which are now
# tested. Every file and function named in those reasons was checked to
# exist; the batch before this one cited two that did not.

# 2026-09-22, ninth: 46 to 45, and this one was already promised. bpa_cli.c's
# header says the arithmetic in that file is deliberately NOT excused because
# it waits for a harness, and it named bpa_top_cmp. That function needed
# nothing but two rows, so it moved to bpa_top.h and test/test_bpa_top.c
# drives the order `show bpa top` reads its rows in - the screen an operator
# reads the first lines of and nothing else.

# 2026-09-22, tenth: 45 to 33 - the export path, the second transport's
# connection, and the two node helpers. Every one of them is inside VPP's
# own machinery: the flow-report API and its per-thread buffers, a TCP
# connection the dataplane owns, a pool read by index, a bihash keyed by
# VPP. The twelve reasons name eight labs and three suites between them, and
# what those measure is the record an operator's collector receives - which
# is the only place this path can honestly be read.

UNTESTED_AT_MOST = {"python": 0, "c": 33, "shell": 0}
MEASURED_ON = "2026-09-20"

# A parse that came back nearly empty would make every count below small and
# the whole file green, which is the failure this check exists to find one
# level down. Well under the measured counts on purpose: a file that leaves
# the product lowers these, a broken parse falls through them.
PARSED_AT_LEAST = {"python": 300, "c": 150, "shell": 25}

BUILTINS = set(dir(builtins))
fail = 0


def ok(msg):
    say("  [ ok ] ", msg)


def bad(msg):
    global fail
    fail += 1
    say("  FAIL: ", msg)


def say(prefix, msg):
    """Wrapped to the console, the continuation under the first word."""
    rows = textwrap.wrap(msg, width=WIDTH, initial_indent=prefix,
                         subsequent_indent=" " * len(prefix),
                         break_long_words=False, break_on_hyphens=False)
    for row in rows or [prefix.rstrip()]:
        print(row)


def rel(path):
    return os.path.relpath(path, REPO)


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


class Fn:
    """One definition: where it is, what it is called, and its excuse."""

    def __init__(self, path, line, name, excuse, disp=None):
        self.path = path
        self.line = line
        self.name = name
        self.disp = disp or name
        # None when there is no marker, "" when the marker has no reason.
        self.excuse = excuse

    def where(self):
        return f"{os.path.basename(self.path)}:{self.line}"


def marker_above(lines, idx, is_comment):
    """
    The marker in the contiguous comment block ending on line idx-1, or
    None. `is_comment` says whether a line belongs to that block.

    THE REASON IS THE REST OF THE BLOCK, not the rest of that one line. A
    reason worth giving does not fit in what is left of a 79-column line
    after the marker, and the first version of this read only that - so a
    wrapped comment, which is the house style for every other comment in
    these files, printed as a sentence cut off in the middle: "a VPP format
    function: its argument is a va_list and it". An excuse that reads as a
    fragment is an excuse nobody can weigh, which is the whole thing the
    reason is for (#390).
    """
    i = idx - 1
    while i >= 0 and is_comment(lines[i]):
        m = re.search(re.escape(MARKER) + r"(.*)", lines[i])
        if m:
            parts = [m.group(1)]
            # Forward from the marker to the end of the block, so the reason
            # reads in the order it was written.
            j = i + 1
            while j < idx and is_comment(lines[j]):
                parts.append(lines[j])
                j += 1
            reason = " ".join(parts)
            # The comment's own punctuation, in all three languages: C's
            # opening and closing, the continuation star, and the # or the
            # leading whitespace python and shell use.
            reason = reason.replace("*/", " ").replace("/*", " ")
            reason = re.sub(r"(^|\s)[*#]+(\s|$)", " ", reason)
            return re.sub(r"\s+", " ", reason).strip()
        i -= 1
    return None


# ------------------------------------------------------------------ python

def python_files():
    out = sorted(os.path.join(REPO, "src", "prismash", f)
                 for f in os.listdir(os.path.join(REPO, "src", "prismash"))
                 if f.endswith(".py"))
    adir = os.path.join(REPO, "appliance")
    for f in sorted(os.listdir(adir)):
        p = os.path.join(adir, f)
        if not os.path.isfile(p) or f.startswith("test_"):
            continue
        with open(p, "rb") as fh:
            first = fh.readline()
        if first.startswith(b"#!") and b"python" in first:
            out.append(p)
    return out


def python_is_comment(line):
    s = line.strip()
    return s.startswith("#") or s.startswith("@")


def python_functions(path):
    src = read(path)
    lines = src.splitlines()
    tree = ast.parse(src, path)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    child._owner = node.name
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # node.lineno is the `def` line; decorators sit above it and the
        # marker sits above those.
        first = node.lineno - 1
        for d in node.decorator_list:
            first = min(first, d.lineno - 1)
        excuse = marker_above(lines, first, python_is_comment)
        name = disp = node.name
        owner = getattr(node, "_owner", None)
        if owner:
            disp = f"{owner}.{name}"
        if name == "__init__" and owner:
            name = owner
        found.append(Fn(path, node.lineno, name, excuse, disp))
    return found


def python_tests():
    tdir = os.path.join(REPO, "test")
    out = sorted(os.path.join(tdir, f) for f in os.listdir(tdir)
                 if f.startswith("test_") and f.endswith(".py"))
    adir = os.path.join(REPO, "appliance")
    out += sorted(os.path.join(adir, f) for f in os.listdir(adir)
                  if f.startswith("test_") and f.endswith(".py"))
    # This file is not its own witness: it calls `where()` on what it
    # found, and prismash.py has a `where` too.
    return [t for t in out if os.path.abspath(t) != SELF]


def root_name(node):
    """The Name at the root of a call or attribute chain, or None."""
    while True:
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        elif isinstance(node, ast.Name):
            return node.id
        else:
            return None


def bound_names(target):
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for t in target.elts for n in bound_names(t)]
    return []


def python_names_called(path, product_modules):
    """
    (bare names called, attribute names called), each a set, for one test
    file. A bare name the file defines itself or a builtin is not a call
    into the product. An attribute call counts unless every binding of the
    name at the root of its receiver is FOREIGN: an import of a module that
    is not the product's, or a value taken from such a module or from a
    builtin, so `f.read()` on an open file and `json.load()` name nothing,
    while `sh.err()` on a Shell() and a helper's parameter do.
    """
    tree = ast.parse(read(path), path)
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef))}
    foreign, ours = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                bound = (a.asname or a.name).split(".")[0]
                mod = a.name.split(".")[0]
                (ours if mod in product_modules else foreign).add(bound)
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            for a in node.names:
                (ours if mod in product_modules
                 else foreign).add(a.asname or a.name)
    # Two passes so a name bound from a foreign name bound later in the
    # walk is still seen as foreign.
    for _ in range(2):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                pairs = [(t, node.value) for t in node.targets]
            elif isinstance(node, ast.With):
                pairs = [(i.optional_vars, i.context_expr)
                         for i in node.items if i.optional_vars]
            else:
                continue
            for target, value in pairs:
                root = root_name(value)
                is_foreign = (root is None or root in BUILTINS
                              or (root in foreign and root not in ours))
                for n in bound_names(target):
                    (foreign if is_foreign else ours).add(n)
    bare, attr = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            if (f.id not in defined and f.id not in BUILTINS
                    and (f.id not in foreign or f.id in ours)):
                bare.add(f.id)
        elif isinstance(f, ast.Attribute):
            root = root_name(f.value)
            if root is None or root in BUILTINS:
                continue
            if root in foreign and root not in ours:
                continue
            attr.add(f.attr)
    return bare, attr


# ----------------------------------------------------------------------- c

C_TYPE = re.compile(r"^(static |static_always_inline |always_inline |"
                    r"static inline )?[A-Za-z_][A-Za-z_0-9]*( \*+)?$")
C_NAME = re.compile(r"^([a-z_][a-z_0-9]*) \(")


def c_files():
    d = os.path.join(REPO, "src", "plugins", "bpa")
    return sorted(os.path.join(d, f) for f in os.listdir(d)
                  if f.endswith(".c"))


def c_is_comment(line):
    s = line.strip()
    return (s.startswith("/*") or s.startswith("*") or s.startswith("//")
            or s.endswith("*/"))


def c_functions(path):
    lines = read(path).splitlines()
    found = []
    for i in range(1, len(lines)):
        m = C_NAME.match(lines[i])
        if not m or not C_TYPE.match(lines[i - 1]):
            continue
        excuse = marker_above(lines, i - 1, c_is_comment)
        found.append(Fn(path, i + 1, m.group(1), excuse))
    return found


def c_standalone_visible(path):
    """
    Which lines of `path` a plain-cc harness can compile.

    The plugin's files guard the half that needs VPP with `BPA_STANDALONE`, so
    a function's reachability from `make test` is a property of the source and
    can be READ rather than guessed at. Every `#if` is tracked, not only the
    ones naming the guard: an `#endif` belonging to some other conditional -
    `#ifndef BPA_AUDIT_PATH_DEFAULT`, say - would otherwise close the guard's
    frame and report the whole of the VPP half as reachable, which is the
    mistake this function exists to have made once.
    """
    lines = read(path).splitlines()
    vis, stack = [], []          # stack entries: True while this branch shows
    for line in lines:
        s = line.strip()
        if s.startswith("#ifdef BPA_STANDALONE"):
            stack.append(True); vis.append(False); continue
        if s.startswith("#ifndef BPA_STANDALONE"):
            stack.append(False); vis.append(False); continue
        if s.startswith(("#if", "#ifdef", "#ifndef")):
            stack.append(None); vis.append(False); continue   # not ours
        if s.startswith("#else") and stack:
            if stack[-1] is not None:
                stack[-1] = not stack[-1]
            vis.append(False); continue
        if s.startswith("#endif") and stack:
            stack.pop(); vis.append(False); continue
        vis.append(all(x for x in stack if x is not None))
    return lines, vis


def c_needs_vpp(fn):
    """Is this function in the half of its file that needs the VPP build?"""
    src = read(fn.path)
    if "BPA_STANDALONE" not in src:
        return True
    lines, vis = c_standalone_visible(fn.path)
    i = fn.line - 1
    return not (0 <= i < len(vis) and vis[i])


def c_strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def c_tests():
    tdir = os.path.join(REPO, "test")
    return sorted(os.path.join(tdir, f) for f in os.listdir(tdir)
                  if f.startswith("test_") and f.endswith(".c"))


# ------------------------------------------------------------------- shell

SH_DEF = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*) ?\(\) *\{")


def shell_files():
    out = []
    adir = os.path.join(REPO, "appliance")
    out += [os.path.join(adir, f) for f in os.listdir(adir)
            if f.endswith(".sh") and not f.startswith("test_")]
    out += [os.path.join(adir, "selftest"), os.path.join(adir, "install.sh")]
    tdir = os.path.join(REPO, "tools")
    out += [os.path.join(tdir, f) for f in os.listdir(tdir)
            if f.endswith(".sh")]
    out.append(os.path.join(REPO, "lab", "lib.sh"))
    return sorted(set(out))


def shell_is_comment(line):
    return line.strip().startswith("#")


def shell_functions(path):
    lines = read(path).splitlines()
    found = []
    for i, line in enumerate(lines):
        m = SH_DEF.match(line)
        if not m:
            continue
        excuse = marker_above(lines, i, shell_is_comment)
        found.append(Fn(path, i + 1, m.group(1), excuse))
    return found


def shell_strip_comments(text):
    """
    The text a shell test EXECUTES: comments out, single-quoted strings out
    (that is where grep and awk programs quote a name), and of a
    double-quoted string only its `$(...)` substitutions kept, so
    `"$(startup_conf test)"` still calls and `"install was reported"` on a
    continuation line does not; a string handed to `sh -c` is executed and
    is kept whole. Heredoc bodies stay: a stub defined there is caught by
    the test defining the name itself.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "#" and (i == 0 or text[i - 1] in " \t\n"):
            while i < n and text[i] != "\n":
                i += 1
        elif c == "'":
            j = text.find("'", i + 1)
            i = n if j < 0 else j + 1
            out.append(" ")
        elif c == '"':
            # A string handed to `sh -c` IS executed, so it is kept whole.
            before = "".join(out).rstrip(" \t\n\\")
            if before.endswith("-c"):
                j = i + 1
                while j < n and text[j] != '"':
                    j += 2 if text[j] == "\\" else 1
                out.append(text[i + 1:j])
                i = j + 1
                continue
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\":
                    i += 2
                elif text.startswith("$(", i):
                    depth, j = 0, i
                    while j < n:
                        if text[j] == "(":
                            depth += 1
                        elif text[j] == ")":
                            depth -= 1
                            if depth == 0:
                                break
                        j += 1
                    out.append(text[i + 1:j + 1])
                    i = j + 1
                else:
                    i += 1
            i += 1
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def shell_tests():
    """
    Shell tests only. A python test reaches a script through subprocess
    with the command in a string, which is not command position here, so
    it would name nothing and is not read.
    """
    tdir = os.path.join(REPO, "test")
    out = [os.path.join(tdir, f) for f in os.listdir(tdir)
           if f.startswith("test_") and f.endswith(".sh")]
    adir = os.path.join(REPO, "appliance")
    out += [os.path.join(adir, f) for f in os.listdir(adir)
            if f.startswith("test_") and f.endswith(".sh")]
    return sorted(out)


def shell_uncommented(text):
    return re.sub(r"(^|[ \t])#[^\n]*", r"\1", text, flags=re.M)


def shell_named(fn, tests):
    """
    tests: {path: (text without comments, text the test executes)}. The
    script is reached when its basename is in the first (a path is quoted);
    the name is called when it stands in command position in the second.
    """
    script = os.path.basename(fn.path)
    call = re.compile(r"(^|[;&|(`{]|\$\()[ \t]*" + re.escape(fn.name)
                      + r"(?=[ \t;)\n]|$)", re.M)
    own = re.compile(r"^[ \t]*" + re.escape(fn.name) + r" ?\(\)", re.M)
    for plain, executed in tests.values():
        if script not in plain or own.search(plain):
            continue
        if call.search(executed):
            return True
    return False


# ------------------------------------------------------------- the reader

def report(lang, fns, named, n_tests):
    """
    Prints the language's four counts and applies its ceiling. `named` is
    the set of functions a test names.
    """
    global fail
    files = sorted({rel(f.path) for f in fns})
    print(f"\n=== {lang}: {len(fns)} functions in {len(files)} file(s), "
          f"read against {n_tests} test file(s) ===")
    if len(fns) < PARSED_AT_LEAST[lang]:
        bad(f"only {len(fns)} {lang} function(s) were parsed, under the "
            f"{PARSED_AT_LEAST[lang]} this tree is known to hold: the parse "
            f"is what is broken, not the coverage, and nothing below is "
            f"measured")
        return
    if n_tests == 0:
        bad(f"no {lang} test file was read, so nothing below is measured")
        return
    ok(f"{len(fns)} parsed, which is at least the {PARSED_AT_LEAST[lang]} "
       f"the parse has to find to be the whole product")

    thin = [f for f in fns if f.excuse == ""]
    excused = [f for f in fns if f.excuse]
    tested = [f for f in fns if f.excuse is None and f in named]
    untested = [f for f in fns if f.excuse is None and f not in named]

    ok(f"{len(tested)} named by a unit test")
    if thin:
        bad(f"{len(thin)} carry a '{MARKER}' marker with no reason after "
            f"it, and an excuse that says nothing is an allowlist entry: "
            + ", ".join(f"{f.where()} {f.disp}" for f in thin))
    if excused:
        ok(f"{len(excused)} excused at their definition, with the reason:")
        for f in excused:
            say(f"    {f.where()} {f.disp}: ", f.excuse)
    else:
        ok("0 excused at their definition")

    allowed = UNTESTED_AT_MOST[lang]
    if lang == "c" and untested:
        # WHAT IS LEFT, AND WHY, on every run. The number alone reads as a
        # backlog of batches; it is not one. The functions still uncounted are
        # in the half of the plugin that needs a buffer, a session pool, a
        # bihash, VPP's format() or the CLI, and no batch of plain-cc
        # harnesses can reach them - what they wait for is a harness built
        # against the real VPP, in the container that already builds the
        # plugin (#390).
        _vpp = [f for f in untested if c_needs_vpp(f)]
        print(f"  {len(_vpp)} of them are behind the VPP boundary and "
              f"{len(untested) - len(_vpp)} are within reach of a plain-cc "
              f"harness")
    print(f"  {len(untested)} named by no test, by file:")
    for path in files:
        names = [f.disp for f in untested if rel(f.path) == path]
        if names:
            say(f"    {os.path.basename(path)} ({len(names)}): ",
                " ".join(names))
    if len(untested) > allowed:
        bad(f"{lang}: {len(untested)} function(s) are named by no unit "
            f"test, above the ceiling of {allowed} measured {MEASURED_ON}; "
            f"they are listed by file above. A function nothing calls in "
            f"isolation is one this repository cannot tell from a function "
            f"that is wrong - write the test for the one that is new, do "
            f"not raise the number")
    elif len(untested) < allowed:
        bad(f"{lang}: the ceiling says {allowed} and only {len(untested)} "
            f"function(s) are named by no test: lower it, or it stops "
            f"holding anything")
    else:
        ok(f"{lang}: {len(untested)} named by no test, at the ceiling of "
           f"{allowed} measured {MEASURED_ON}")


def main():
    # python
    fns = [f for p in python_files() for f in python_functions(p)]
    tests = python_tests()
    modules = {os.path.basename(p).rsplit(".", 1)[0] for p in python_files()}
    bare, attr = set(), set()
    for t in tests:
        b, a = python_names_called(t, modules)
        bare |= b
        attr |= a
    named = {f for f in fns if f.name in bare or f.name in attr}
    report("python", fns, named, len(tests))

    # c
    fns = [f for p in c_files() for f in c_functions(p)]
    tests = c_tests()
    text = "\n".join(c_strip_comments(read(t)) for t in tests)
    called = set(re.findall(r"\b([a-z_][a-z_0-9]*)\s*\(", text))
    named = {f for f in fns if f.name in called}
    report("c", fns, named, len(tests))

    # shell
    fns = [f for p in shell_files() for f in shell_functions(p)]
    tests = {}
    for t in shell_tests():
        text = read(t)
        tests[t] = (shell_uncommented(text), shell_strip_comments(text))
    named = {f for f in fns if shell_named(f, tests)}
    report("shell", fns, named, len(tests))

    print()
    print("ALL TESTS PASSED" if not fail else f"{fail} FAILURES")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
