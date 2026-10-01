#!/usr/bin/env python3
"""
prismash, with the policy compiler of before #535's fix - a lab fixture for
lab/commit-readback.sh (#539), not the product.

#535's lab found `set extcommunity rt ... additive`: vtysh -C passes it, bgpd
refuses it when the line is applied. The compiler no longer writes it, so the
only way to put that line in front of a commit again is to put the old
spelling back, here: every `set extcommunity rt` the compiler emits gets its
`additive`.

READBACK=off also takes #539's check away (compiled_refused answers nothing),
which is the build before this one: the lab's MODE=off.
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
sys.exit(prismash.main())
