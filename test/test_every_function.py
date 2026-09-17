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

  python  every `def` the `ast` module finds in src/hexash/*.py and in the
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
test_hexash_refusals.py for refusals nothing has printed. A batch of tests
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
# 2026-09-17, #390 batches A and B: routes.py, config.py, vppapi.py and
# system.py at 0, with Reply.ok excused (a property is read, not called).
# 2026-09-17, #390 batch E: the eight static functions of bpa_pool.c and
# bpa_dyn.c, the two product files the plain-cc harness compiles, at 0; the
# harness reaches a static function by including the .c. The other thirteen
# .c files need VPP to compile and wait for the VPP-build batch.
UNTESTED_AT_MOST = {"python": 215, "c": 187, "shell": 36}
MEASURED_ON = "2026-09-17"

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
    """
    i = idx - 1
    while i >= 0 and is_comment(lines[i]):
        m = re.search(re.escape(MARKER) + r"(.*)", lines[i])
        if m:
            return m.group(1).replace("*/", "").strip()
        i -= 1
    return None


# ------------------------------------------------------------------ python

def python_files():
    out = sorted(os.path.join(REPO, "src", "hexash", f)
                 for f in os.listdir(os.path.join(REPO, "src", "hexash"))
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
    # found, and hexash.py has a `where` too.
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
