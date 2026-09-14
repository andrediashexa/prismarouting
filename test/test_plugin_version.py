#!/usr/bin/env python3
"""
The build the LOADED dataplane is, and `show version` saying when that is not
the build the rest of the box is (#291, acceptance item 2).

WHAT WAS WRONG. `src/plugins/bpa/bpa.c` registered the plugin with
`.version = "0.1"`. That literal had not moved in seventy-one releases, so
`show plugins` reported the same three characters on every box ever shipped
and there was no reading anywhere that said which build of the dataplane was
loaded. `show version` printed the VERSION file - a stamp `install.sh` writes
at install time - and VPP's own version, which is upstream's. Neither is our
plugin, so between an upgrade and the next reboot the box stated a version it
was not running and nothing on it could contradict the claim.

WHAT IS ASSERTED HERE, and why each half is a unit suite rather than a lab:

  * THE SOURCE OF TRUTH IS ONE. The stamp compiled into the plugin and the
    stamp written into /usr/share/hexarouting/VERSION both come from
    `tools/version-stamp.sh`, and both honour `HEXA_VERSION_STAMP`. That is
    the whole reason the comparison can be an equality: two spellings of one
    truth compare unequal on a box that is perfectly fine, and a warning that
    fires when nothing is wrong is one that gets ignored on the day it means
    something.
  * THE FIELD IS READ THE WAY VPP PRINTS IT. `show plugins` lays the version
    out in a fixed column, and the stamp this product puts there contains a
    space. Splitting the row on whitespace silently returns "0.71.0" and drops
    the commit, which is the half that says which build it is.
  * FOUR ANSWERS, NOT TWO. Not answering, answering with no build, agreeing
    and disagreeing are four faults with four different fixes, and this
    repository has spent an issue on each occasion the first two were
    collapsed (`Frr.daemons()` and the `show_daemons` comment above it).
  * THE WIDTH. Every line of every branch fits the 80-column console an
    appliance has, less the column an auto-wrapping terminal needs free.

The needles below are matched against whitespace-COLLAPSED text, through
`unwrapped()`. A needle that matches raw output only matches while the message
happens to fold somewhere else, and this repository has been bitten by that
four times - `expect_said` in lab/verdict.sh is the shell half of the same
answer.

The other end of this is measured rather than asserted here: a VPP started
with the built plugin reports the stamp in `show plugins`, and hexash reads it
off a live dataplane. That needs docker, and this file must not.
"""
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import hexash                                                   # noqa: E402

from config import CONSOLE_COLUMNS                              # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fail = 0


def check(cond, msg, detail=""):
    """One assertion, with the evidence held back for the line that goes red.

    Three arguments where the rest of this directory manages with two, and the
    reason is what the evidence IS here: a whole screen of `show version`.
    Printed beside every green check it buries the report in fifteen copies of
    text nobody reads, and a report nobody reads is how #218 lost the line
    that named the failing check. The screens are printed once, under the
    heading they belong to; the repr comes back only where it says something.
    """
    global fail
    if cond:
        print(f"  [ ok ] {msg}")
        return
    fail += 1
    print(f"  FAIL: {msg}" + (f": {detail}" if detail else ""))


def unwrapped(text):
    """`text` with every run of whitespace collapsed to one space.

    The same function, for the same reason, as in test_hexash_bgp.py: these
    messages are wrapped to the console by measurement, so where a line breaks
    moves the moment anybody edits a word. A needle straddling the break stops
    matching - loudly for a positive check and SILENTLY for a `not in` one,
    which is the defect class this project is organised against.
    """
    return " ".join(text.split())


def show(title, text):
    """A screen, printed once, as the evidence behind the checks under it."""
    print(f"\n  --- {title} ---")
    for line in text.splitlines():
        print(f"  | {line}")
    print()


def stamp_of(repo, env=None):
    """What `tools/version-stamp.sh` says about a tree - the one calculation."""
    e = dict(os.environ)
    e.pop("HEXA_VERSION_STAMP", None)
    e.update(env or {})
    return subprocess.run(["sh", os.path.join(repo, "tools", "version-stamp.sh"),
                           repo], capture_output=True, text=True,
                          env=e).stdout.strip()


# ----------------------------------------------------------------------------
print("=== the plugin no longer carries a literal nobody has to bump ===")
# The defect itself, asserted on the source. It cannot be asserted anywhere
# else without a compiler: what `show plugins` prints is decided at build time.
BPA_C = open(os.path.join(REPO, "src", "plugins", "bpa", "bpa.c")).read()

_reg = re.search(r"VLIB_PLUGIN_REGISTER \(\) = \{(.*?)\};", BPA_C, re.S)
check(_reg is not None, "bpa.c still registers the plugin")
_body = _reg.group(1) if _reg else ""
check(".version = BPA_BUILD_VERSION" in _body,
      "the registration takes its version from the build", repr(_body.strip()))
check(not re.search(r'\.version\s*=\s*"', _body),
      "and no string literal is left in it at all", repr(_body.strip()))
check("#include <bpa/bpa_version.h>" in BPA_C,
      "bpa.c includes the generated header that defines it")


# ----------------------------------------------------------------------------
print("\n=== and the generated header says what version-stamp.sh says ===")
# The claim the whole comparison rests on. install.sh writes the VERSION file
# from tools/version-stamp.sh; the plugin is stamped from the same script; so
# a box whose dataplane was built from the tree it was installed from reports
# ONE string in both places, and an equality is a fair test.
INSTALL_SH = open(os.path.join(REPO, "appliance", "install.sh")).read()
check("tools/version-stamp.sh" in INSTALL_SH,
      "install.sh still takes the VERSION file from tools/version-stamp.sh")
GEN = os.path.join(REPO, "tools", "plugin-version-h.sh")
check("version-stamp.sh" in open(GEN).read(),
      "and the plugin's header is generated from the same script")


def generate(repo, out, env=None):
    e = dict(os.environ)
    e.pop("HEXA_VERSION_STAMP", None)
    e.update(env or {})
    return subprocess.run(["sh", GEN, repo, out], capture_output=True,
                          text=True, env=e)


def defined(path):
    """The version BPA_BUILD_VERSION is defined as in a generated header."""
    m = re.search(r'#define BPA_BUILD_VERSION "(.*)"', open(path).read())
    return m.group(1) if m else None


with tempfile.TemporaryDirectory() as tmp:
    hdr = os.path.join(tmp, "gen", "bpa_version.h")
    r = generate(REPO, hdr)
    check(r.returncode == 0, "the generator ran", repr(r.stderr))
    check(os.path.exists(hdr) and defined(hdr) == stamp_of(REPO),
          f"the header defines what version-stamp.sh says: "
          f"{defined(hdr) if os.path.exists(hdr) else None!r}",
          f"version-stamp.sh says {stamp_of(REPO)!r}, so the plugin and the "
          f"VERSION file would disagree on a box that is perfectly fine")

    # The override install.sh honours, honoured here too. Not decoration: a
    # git WORKTREE has no .git directory inside the build container's bind
    # mount, so the stamp read in there is "not a git tree" while the one
    # written on the host is the commit. Measured on this tree while #291 was
    # being built, which is why tools/build.sh passes it in.
    generate(REPO, hdr, {"HEXA_VERSION_STAMP": "9.9.9 (feedbee)"})
    check(defined(hdr) == "9.9.9 (feedbee)",
          "HEXA_VERSION_STAMP overrides it the way it overrides the VERSION "
          "file", repr(defined(hdr)))

    # Only when it changed. This header is a compile dependency of every
    # object in the plugin, and forty labs run ninja before they start.
    os.utime(hdr, ns=(0, 0))
    generate(REPO, hdr, {"HEXA_VERSION_STAMP": "9.9.9 (feedbee)"})
    check(os.stat(hdr).st_mtime_ns == 0,
          "an unchanged stamp does not rewrite the header, so the plugin does "
          "not relink on every build", str(os.stat(hdr).st_mtime_ns))
    generate(REPO, hdr, {"HEXA_VERSION_STAMP": "9.9.9 (0ddba11)"})
    check(defined(hdr) == "9.9.9 (0ddba11)",
          "and a changed one does", repr(defined(hdr)))

    # A stamp that cannot become a C string literal is refused HERE, where the
    # cause is, and not by the compiler inside a generated file nobody wrote.
    r = generate(REPO, hdr, {"HEXA_VERSION_STAMP": '9.9.9 ("x)'})
    check(r.returncode != 0 and "quote" in r.stderr,
          "a stamp carrying a quote is refused, by name",
          f"rc={r.returncode} {r.stderr!r}")


# ----------------------------------------------------------------------------
print("\n=== the version column of `show plugins`, as VPP prints it ===")
# MEASURED, VPP 26.06, the plugin loaded from lab/startup-test.conf: the name
# starts at column 5, the version at 46, the description at 79. This fixture
# is built FROM those offsets, so a hand-counted row cannot drift away from
# what was measured.
NAME_COL, VERSION_COL, DESC_COL = 5, 46, 79
DESCRIPTION = "CGNAT with allocated port blocks (BPA)"


def show_plugins(version, name="bpa_plugin.so"):
    """`show plugins` with one row for our plugin, laid out as VPP lays it."""
    head = ("".ljust(NAME_COL) + "Plugin".ljust(VERSION_COL - NAME_COL)
            + "Version".ljust(DESC_COL - VERSION_COL) + "Description")
    row = ("  8." + " " + name.ljust(VERSION_COL - NAME_COL)
           + version.ljust(DESC_COL - VERSION_COL) + DESCRIPTION)
    other = ("  1." + " " + "acl_plugin.so".ljust(VERSION_COL - NAME_COL)
             + "26.06-release".ljust(DESC_COL - VERSION_COL)
             + "Access Control Lists (ACL)")
    return (" Plugin path is: /vpp_plugins, plugins shown in load order\n\n"
            + head + "\n" + other + "\n" + row + "\n")


class Dp(hexash.Vpp):
    """A dataplane answering canned text, with the product's own reader on top.

    Derived from `hexash.Vpp` and not free-standing, for the reason `_Frr271`
    in test_hexash_complete.py writes out: a stub that reimplements the method
    under test asserts the stub. `plugin_build()` and its regex are the real
    ones here; only `run()` is canned.
    """

    def __init__(self, plugins_out, rc=0):
        self.plugins_out, self.rc = plugins_out, rc

    def run(self, cmd):
        if cmd.startswith("show plugins"):
            return self.rc, self.plugins_out
        return 0, "vpp v26.06-release built by root on host at 2026-06-24"


check(Dp(show_plugins("0.1")).plugin_build() == "0.1",
      "the version every box ever shipped reported is read off the row")

# The one that makes the parser worth writing down. The stamp is
# "0.71.0 (d07ebda)" - a version, a space and a commit - so a reader that
# split the row on whitespace would return "0.71.0" and throw away the half
# that says WHICH BUILD it is.
check(Dp(show_plugins("0.71.0 (d07ebda)")).plugin_build() == "0.71.0 (d07ebda)",
      "a stamp with a space in it comes back whole, commit and all")
check(Dp(show_plugins("0.71.0 (d07ebda-dirty)")).plugin_build()
      == "0.71.0 (d07ebda-dirty)",
      "and so does the one a tree with uncommitted changes produces")

# Three answers. None is "the dataplane did not answer"; "" is "it answered
# and reports no build"; a string is a build. Collapsing the first two is the
# mistake show_daemons spends a paragraph on.
check(Dp("", rc=1).plugin_build() is None,
      "a dataplane that did not answer reads as None")
check(Dp("\n").plugin_build() is None,
      "and so does one that answered nothing at all")
_no_bpa = show_plugins("26.06-release", name="acl_plugin.so")
check(Dp(_no_bpa).plugin_build() == "",
      "a `show plugins` with no bpa row reads as the empty string, NOT None",
      repr(Dp(_no_bpa).plugin_build()))
check(Dp(show_plugins("")).plugin_build() == "",
      "and so does a row whose version column is blank - not the description "
      "beside it", repr(Dp(show_plugins("")).plugin_build()))


# ----------------------------------------------------------------------------
print("\n=== the bound the build asserts is the bound the parser needs ===")
# bpa.c carries STATIC_ASSERT (sizeof (BPA_BUILD_VERSION) <= 32), which is a
# stamp of at most 31 characters. This is where that number comes from, shown
# rather than claimed in a comment: the field is closed by the run of two or
# more spaces before the description, and the column is 33 wide.
_BUDGET = DESC_COL - VERSION_COL - 2
check(_BUDGET == 31,
      f"the version column is {DESC_COL - VERSION_COL} wide, so the widest "
      f"stamp that still leaves two spaces is {_BUDGET}")
check("sizeof (BPA_BUILD_VERSION) <= 32" in BPA_C,
      "and bpa.c fails the BUILD above it, rather than this parser failing "
      "quietly on a box")
_fits = "0.71.0 " + "9" * (_BUDGET - len("0.71.0 "))
check(Dp(show_plugins(_fits)).plugin_build() == _fits,
      f"a {_BUDGET}-column stamp is read whole")
_over = _fits + "9"
check(Dp(show_plugins(_over)).plugin_build() != _over,
      "and one column more is not, which is what the build assertion is "
      "protecting", repr(Dp(show_plugins(_over)).plugin_build()))
check(len(stamp_of(REPO)) <= _BUDGET,
      f"this tree's own stamp is {len(stamp_of(REPO))} columns, inside the "
      f"budget of {_BUDGET}", repr(stamp_of(REPO)))


# ----------------------------------------------------------------------------
print("\n=== what `show version` says, one screen per fault ===")


class Frr:
    """Answers `show version` and `show daemons`, and nothing else is asked."""

    def show(self, what):
        return 0, "FRRouting 10.7.1 (hexarouting)\n"

    def daemons(self):
        return {"zebra", "mgmtd", "staticd", "bgpd", "ospfd", "ospf6d",
                "isisd"}


_REAL_VERSION = hexash.hexarouting_version


def screen(plugins_out, box_version, rc=0):
    """`show version`, driven whole, with the VERSION file at `box_version`."""
    sh = hexash.Shell.__new__(hexash.Shell)
    sh.dp, sh.frr = Dp(plugins_out, rc), Frr()
    buf = io.StringIO()
    real = hexash.VERSION_PATH
    with tempfile.TemporaryDirectory() as tmp:
        if box_version is None:
            # There is no seam for "no VERSION file anywhere": the fallback in
            # hexarouting_version() reaches the repository's own VERSION,
            # which exists whenever this suite runs. The function is replaced
            # for this one case and put back, rather than this suite asserting
            # a branch that nothing can reach.
            hexash.hexarouting_version = lambda: hexash.VERSION_UNKNOWN
        else:
            path = os.path.join(tmp, "VERSION")
            open(path, "w").write(box_version + "\n")
            hexash.VERSION_PATH = path
        try:
            with contextlib.redirect_stdout(buf):
                sh.show_version()
        finally:
            hexash.VERSION_PATH = real
            hexash.hexarouting_version = _REAL_VERSION
    return buf.getvalue()


BOX = "0.71.0 (d07ebda)"

# --- they agree, and the box says nothing alarming ---
#
# The normal state, and the state of every box that has been rebooted since
# its upgrade. A paragraph under every `show version` is a paragraph read once
# and skipped for ever, including on the box where it means something - which
# is the argument unchecked_note() makes for the same decision.
_agree = screen(show_plugins(BOX), BOX)
show("the dataplane is the build the box is", _agree)
check(f"  plugin:    {BOX}" in _agree.splitlines(),
      "the build is on a row of its own, whole", repr(_agree))
for _alarm in ("not answering", "reports no build", "restart vpp",
               "not the HexaRouting build", "cannot state its own version"):
    check(_alarm not in unwrapped(_agree),
          f"and nothing on the screen says {_alarm!r}", repr(_agree))

# --- they disagree: the box between an upgrade and its reboot ---
#
# Driven with "0.1" on purpose. That is not an invented value: it is what
# every build shipped before #291 registered, so this is the screen an
# operator gets on the first upgrade onto a tree that carries this change.
_split = screen(show_plugins("0.1"), BOX)
show("upgraded, not yet rebooted", _split)
check("  plugin:    0.1" in _split.splitlines(),
      "the build the dataplane is actually running is printed", repr(_split))
check("it is not the HexaRouting build above" in unwrapped(_split),
      "and the box SAYS they are not the same", repr(_split))
check("does not restart the dataplane" in unwrapped(_split),
      "it says why that happens rather than leaving it a mystery", repr(_split))
check("'systemctl restart vpp' is run" in unwrapped(_split),
      "it names the thing an operator can do", repr(_split))
check("end every session it is carrying" in unwrapped(_split),
      "and what doing it costs, which is what makes it a decision rather "
      "than an instruction", repr(_split))

# --- the dataplane is not answering at all ---
_silent = screen("", BOX, rc=1)
show("the dataplane is not answering", _silent)
check("the dataplane is not answering" in unwrapped(_silent),
      "a silent dataplane is reported as silent", repr(_silent))
check("reports no build" not in unwrapped(_silent),
      "and NOT as a dataplane that is up without the plugin, which is a "
      "different fault with a different fix", repr(_silent))
check("systemctl status vpp" in unwrapped(_silent),
      "and the operator is sent to the dataplane's own unit", repr(_silent))

# --- the dataplane is up and is not running our plugin ---
_noplugin = screen(_no_bpa, BOX)
show("VPP is up without bpa_plugin.so", _noplugin)
check("reports no build" in unwrapped(_noplugin),
      "a VPP without bpa_plugin.so is reported as that", repr(_noplugin))
check("not answering" not in unwrapped(_noplugin),
      "and NOT as a dataplane that is down, which would send the operator to "
      "the wrong place", repr(_noplugin))
check("translates nothing" in unwrapped(_noplugin),
      "and what it means for the box is said", repr(_noplugin))

# --- the box cannot state its own version ---
#
# A third answer, not a disagreement. Reporting it as one would announce a
# reboot on a box whose only fault is a missing file.
_unknown = screen(show_plugins(BOX), None)
show("the box has no VERSION file", _unknown)
check(f"  plugin:    {BOX}" in _unknown.splitlines(),
      "the dataplane's build is still printed", repr(_unknown))
check("cannot be decided here" in unwrapped(_unknown),
      "and the box says the comparison is not available", repr(_unknown))
check("restart vpp" not in unwrapped(_unknown),
      "rather than sending anybody to restart the dataplane", repr(_unknown))


# ----------------------------------------------------------------------------
print("\n=== and every one of those screens fits the console ===")
# 79 columns, for the reason test_hexash_complete.py pins the constant on: 80
# is what a serial console gives an appliance and the last column is left free
# so an auto-wrapping terminal does not break the line itself. Pinned here too
# because every width assertion below is written against it, and a check
# somebody can satisfy by widening the rule is a check that will be satisfied
# that way the first time a message does not fit.
check(CONSOLE_COLUMNS == 79,
      f"the console is {CONSOLE_COLUMNS} columns, which is what these "
      f"assertions are written against",
      "raising it turns every width check here green without a line getting "
      "any narrower")
_screens = {"agree": _agree, "disagree": _split, "silent": _silent,
            "no plugin": _noplugin, "version unknown": _unknown}
_wide = [(n, len(l), l) for n, s in _screens.items()
         for l in s.splitlines() if len(l) > CONSOLE_COLUMNS]
check(not _wide,
      f"none of the {len(_screens)} screens wraps on a serial console: "
      f"the widest line is "
      f"{max(len(l) for s in _screens.values() for l in s.splitlines())} "
      f"columns", str(_wide))


if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
