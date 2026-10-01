#!/usr/bin/env python3
"""
prismash, with the policy compiler of before #535's fix - a lab fixture for
lab/commit-readback.sh (#539), not the product.

#535's lab found `set extcommunity rt ... additive`: vtysh -C passes it, bgpd
refuses it when the line is applied. The compiler no longer writes it, so the
only way to put that line in front of a commit again is to put the old
spelling back, here: every `set extcommunity rt` the compiler emits gets its
`additive`.

READBACK=off also takes #539's and #544's checks away (compiled_refused,
clauses_refused and lists_refused answer nothing, and #197's word check judges
list entries again), which is the build before them: the lab's MODE=off. Nothing else about a route-map the operator types
changes here.
"""
import os
import sys

sys.path.insert(0, "/src/src/prismash")
import policy  # noqa: E402

_set_lines = policy._set_lines


def _with_additive(*a, **k):
    return [line + " additive" if line.startswith(" set extcommunity rt ")
            else line for line in _set_lines(*a, **k)]


policy._set_lines = _with_additive
import prismash  # noqa: E402

if os.environ.get("READBACK") == "off":
    prismash.compiled_refused = lambda *a: []
    prismash.clauses_refused = lambda *a: []
    prismash.lists_refused = lambda *a: []
    prismash.not_list_entries = lambda added: added
sys.exit(prismash.main())
