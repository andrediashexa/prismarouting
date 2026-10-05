#!/usr/bin/env python3
"""
The pool's on-demand mode is called BPA in every message the box prints (#573,
#577).

0.151.0 renamed `mode dynamic` to `mode bpa` in the configuration, the
refusals and the API, and the VTY went on saying "dynamic" in its own words:
the commit warning for a BPA pool with no log export read "dynamic, and
nothing exports its block events" on 10.40.200.166, and the selftest warned
that "a pool runs in dynamic mode". This reads every string the VTY's modules
hold and every line the selftest prints, and refuses the old word used as the
mode's name. Two messages name the old word on purpose - the refusal of a
typed `mode dynamic` and the note a migrated file prints - and are allowed by
their text. Log records are not here: they follow the RFCs and do not change.
"""
import ast
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = ["src/prismash/prismash.py", "src/prismash/config.py",
           "src/prismash/apiserver.py", "src/prismash/cgnatshow.py",
           "src/prismash/typedcfg.py"]
# The mode's name, as a message would use it: "dynamic pool(s)", "dynamic
# mode", "in dynamic", or a sentence that starts with the word.
# A bare "dynamic" is the dataplane's own value, compared in code, not a
# message, and is left alone.
OLD = re.compile(r"\bdynamic (pools?|mode)\b|\bin dynamic\b|^dynamic[,:]")
# The two that name the old word on purpose (#573).
ALLOWED = ("mode dynamic is now 'mode bpa'", "'mode dynamic' is now 'mode bpa'")

fail = 0


def check(cond, msg):
    global fail
    if cond:
        print(f"  [ ok ] {msg}")
    else:
        print(f"  FAIL: {msg}")
        fail += 1


def strings(path):
    """Every string constant in a module, with its line, docstrings left out."""
    tree = ast.parse(open(os.path.join(REPO, path)).read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef,
                             ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(
                    getattr(first, "value", None), ast.Constant):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docs):
            yield node.lineno, node.value


def offenders(text_lines):
    return [(n, t) for n, t in text_lines
            if OLD.search(t.strip()) and not any(a in t for a in ALLOWED)]


print("=== the VTY's own strings say bpa (#577) ===")
for mod in MODULES:
    bad = offenders(strings(mod))
    check(not bad, f"{mod}: no message calls the pool mode 'dynamic'"
          + "".join(f"\n         line {n}: {t!r}" for n, t in bad[:5]))

print("=== the selftest's words say bpa ===")
_said = []
for n, line in enumerate(open(os.path.join(REPO, "appliance/selftest")), 1):
    m = re.match(r'\s*(say|warn|fail|ok)\s+"(.*)"\s*$', line)
    if m:
        _said.append((n, m.group(2)))
bad = offenders(_said)
check(_said and not bad, "appliance/selftest prints no 'dynamic mode'"
      + "".join(f"\n         line {n}: {t!r}" for n, t in bad[:5]))

print("=== and the rule can fail ===")
check(offenders([(1, "block-stride applies to dynamic pools only")]),
      "a message naming 'dynamic pools' is caught")
check(offenders([(1, "dynamic, and nothing exports its block events. ")]),
      "the 0.151.1 commit warning is caught")
check(not offenders([(1, "mode dynamic is now 'mode bpa' (#573)")]),
      "the refusal that names the old word on purpose is allowed")
check(not offenders([(1, "routing, static and dynamic")]),
      "dynamic routing is not the pool's mode")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
