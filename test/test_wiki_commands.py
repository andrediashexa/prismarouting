#!/usr/bin/env python3
"""
Every command the wiki tells an operator to type, typed at the command tree.

The wiki is where this product makes promises to somebody who does not have
the source: a page that shows a command the box does not have, or shows it at
a prompt the box would never show it at, is the product claiming something it
does not do. Nothing checked that, and `test_context_map.py` already makes the
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
WIKI = os.path.join(REPO, "wiki")

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


def prompt_lines():
    """(path, mode, context, command) for every prompt line in the wiki."""
    out = []
    for root, _, files in os.walk(WIKI):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
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
if len(lines) >= 20:
    print(f"  [ ok ] {len(lines)} prompt line(s) across the wiki")
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

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
