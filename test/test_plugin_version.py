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
    stamp written into /usr/share/prisma-routing/VERSION both come from
    `tools/version-stamp.sh`, and both honour `PRISMA_VERSION_STAMP`. That is
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
with the built plugin reports the stamp in `show plugins`, and prismash reads it
off a live dataplane. That needs docker, and this file must not.
"""
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "prismash"))
import prismash                                                   # noqa: E402
import vppapi                                                   # noqa: E402

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

    The same function, for the same reason, as in test_prismash_bgp.py: these
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
    e.pop("PRISMA_VERSION_STAMP", None)
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
    e.pop("PRISMA_VERSION_STAMP", None)
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
    generate(REPO, hdr, {"PRISMA_VERSION_STAMP": "9.9.9 (feedbee)"})
    check(defined(hdr) == "9.9.9 (feedbee)",
          "PRISMA_VERSION_STAMP overrides it the way it overrides the VERSION "
          "file", repr(defined(hdr)))

    # Only when it changed. This header is a compile dependency of every
    # object in the plugin, and forty labs run ninja before they start.
    os.utime(hdr, ns=(0, 0))
    generate(REPO, hdr, {"PRISMA_VERSION_STAMP": "9.9.9 (feedbee)"})
    check(os.stat(hdr).st_mtime_ns == 0,
          "an unchanged stamp does not rewrite the header, so the plugin does "
          "not relink on every build", str(os.stat(hdr).st_mtime_ns))
    generate(REPO, hdr, {"PRISMA_VERSION_STAMP": "9.9.9 (0ddba11)"})
    check(defined(hdr) == "9.9.9 (0ddba11)",
          "and a changed one does", repr(defined(hdr)))

    # A stamp that cannot become a C string literal is refused HERE, where the
    # cause is, and not by the compiler inside a generated file nobody wrote.
    r = generate(REPO, hdr, {"PRISMA_VERSION_STAMP": '9.9.9 ("x)'})
    check(r.returncode != 0 and "quote" in r.stderr,
          "a stamp carrying a quote is refused, by name",
          f"rc={r.returncode} {r.stderr!r}")
    # The other pattern of the same case arm, and it is not the same
    # character: a lone backslash closes nothing, it ESCAPES the quote that
    # ends the literal, so the compiler swallows the next line as well and the
    # error it reports is about a token further down a file nobody wrote.
    r = generate(REPO, hdr, {"PRISMA_VERSION_STAMP": "9.9.9 (x\\)"})
    check(r.returncode != 0 and "backslash" in r.stderr,
          "and so is one carrying a backslash",
          f"rc={r.returncode} {r.stderr!r}")
    # And the header is NOT left half-written. The refusal comes before the
    # `cat > "$OUT.new"`, so a stamp the generator would not take leaves the
    # previous header standing rather than a .new beside it for the next build
    # to find.
    check(defined(hdr) == "9.9.9 (0ddba11)"
          and not os.path.exists(hdr + ".new"),
          "and neither leaves a header behind",
          f"{defined(hdr)!r}, .new exists: {os.path.exists(hdr + '.new')}")

    # The two arguments, which are `${1:?usage}` and `${2:?usage}` and had
    # never been driven either. tools/build.sh and the Makefile both call this
    # with two; a caller that passes one would otherwise write the header to
    # an empty path, and the build that followed would compile the LAST one -
    # a plugin stamped with a version nobody generated for it.
    # Three, because two of them cannot tell the two guards apart: with no
    # arguments BOTH fire, so a run that only passed none would stay green
    # with the first one deleted. An EMPTY first argument is the call only
    # `${1:?}` refuses, and it is the realistic one - a caller expanding a
    # variable that is not set.
    for argv, which in (([], "no arguments"),
                        ([REPO], "one argument"),
                        (["", os.path.join(tmp, "empty.h")],
                         "an empty repository argument")):
        r = subprocess.run(["sh", GEN] + argv, capture_output=True, text=True)
        check(r.returncode != 0 and "usage: plugin-version-h.sh" in r.stderr,
              f"{which} is refused with the usage line",
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


class Dp(prismash.Vpp):
    """A dataplane answering canned text, with the product's own reader on top.

    Derived from `prismash.Vpp` and not free-standing, for the reason `_Frr271`
    in test_prismash_complete.py writes out: a stub that reimplements the method
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
        return 0, "FRRouting 10.7.1 (prisma-routing)\n"

    def daemons(self):
        return {"zebra", "mgmtd", "staticd", "bgpd", "ospfd", "ospf6d",
                "isisd"}


_REAL_SOURCE = prismash.prisma_routing_version_source

#: The box is not installed and prismash is being run out of a source tree.
#
# Nothing is replaced for this one: VERSION_PATH is pointed at a file that is
# not there, so the product's OWN fallback runs and reaches the same file it
# reaches on a developer's machine. That is the whole case, driven end to end.
FROM_TREE = object()

#: There is no VERSION file anywhere, not even the repository's.
NOWHERE = None


@contextlib.contextmanager
def box_at(box_version):
    """This box's version reading, set to one of the three cases and put back.

    A context manager rather than a step inside `screen()`, because the
    refusal section further down needs the SAME three cases: the disagreement
    is one reading with two readers now (#293), and a second copy of this swap
    is a second place for the NOWHERE case to be got subtly wrong.
    """
    real = prismash.VERSION_PATH
    with tempfile.TemporaryDirectory() as tmp:
        if box_version is NOWHERE:
            # There is no seam for "no VERSION file anywhere": the fallback
            # reaches the repository's own VERSION, which exists whenever this
            # suite runs. The reading is replaced for this one case and put
            # back, rather than this suite asserting a branch nothing can
            # reach. It is the READING that is replaced and not the comparison
            # - the product still decides for itself what to do with it.
            prismash.prisma_routing_version_source = (
                lambda: (prismash.VERSION_UNKNOWN, prismash.VERSION_FROM_NOWHERE))
        elif box_version is FROM_TREE:
            prismash.VERSION_PATH = os.path.join(tmp, "not-installed")
        else:
            path = os.path.join(tmp, "VERSION")
            open(path, "w").write(box_version + "\n")
            prismash.VERSION_PATH = path
        try:
            yield
        finally:
            prismash.VERSION_PATH = real
            prismash.prisma_routing_version_source = _REAL_SOURCE


def screen(plugins_out, box_version, rc=0):
    """`show version`, driven whole, with the VERSION file at `box_version`."""
    sh = prismash.Shell.__new__(prismash.Shell)
    sh.dp, sh.frr = Dp(plugins_out, rc), Frr()
    buf = io.StringIO()
    with box_at(box_version):
        with contextlib.redirect_stdout(buf):
            sh.show_version()
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
# The first line is the one an operator reads out on the phone, and it is the
# product's name beside the build: "Prisma Routing 0.71.0 (d07ebda)". Held
# here because nothing else offline reads that row - the wiki quotes it and
# test_wiki_screens.py cannot drive `show version` - and #202 moved the name.
check(_agree.splitlines()[0] == f"Prisma Routing {BOX}",
      "the first line names the product and the build the box is",
      repr(_agree.splitlines()[:1]))
check(f"  plugin:    {BOX}" in _agree.splitlines(),
      "the build is on a row of its own, whole", repr(_agree))
for _alarm in ("not answering", "reports no build", "restart vpp",
               "not the Prisma Routing build", "cannot state its own version"):
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
check("it is not the Prisma Routing build above" in unwrapped(_split),
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
_unknown = screen(show_plugins(BOX), NOWHERE)
show("the box has no VERSION file", _unknown)
check(f"  plugin:    {BOX}" in _unknown.splitlines(),
      "the dataplane's build is still printed", repr(_unknown))
check("nothing here to compare" in unwrapped(_unknown),
      "and the box says the comparison is not available - the same words the "
      "source-tree screen below uses, because it is the same answer",
      repr(_unknown))
check("cannot state its own version" in unwrapped(_unknown),
      "with the reason that is particular to this one", repr(_unknown))
check("restart vpp" not in unwrapped(_unknown),
      "rather than sending anybody to restart the dataplane", repr(_unknown))

# --- prismash run out of a source tree, on a box that was never installed ---
#
# MEASURED before it was fixed, on a real VPP in a lab container with the real
# plugin loaded and no /usr/share/prisma-routing/VERSION on the box at all:
#
#   Prisma Routing 0.71.0
#     plugin:    0.71.0 (4a7044e)
#                this is the build the dataplane was loaded with at the last
#                boot, and it is not the Prisma Routing build above.
#
# Nothing had been upgraded. The reading had fallen back to the repository's
# own VERSION, which is "0.71.0" - the number the NEXT release will carry, and
# not a build stamp of anything. Comparing it against one is comparing two
# different kinds of thing, which is the defect class this repository is
# organised against, and the people it landed on were every developer running
# prismash out of the tree.
#
# It must not go SILENT either. "the two agree" and "there was nothing to
# compare" are different answers, and conflating them is the same mistake one
# step quieter.
_tree = screen(show_plugins(stamp_of(REPO)), FROM_TREE)
show("prismash run out of a source tree", _tree)
check("it is not the Prisma Routing build above" not in unwrapped(_tree),
      "a source tree is not reported as an upgrade waiting for a reboot",
      repr(_tree))
check("restart vpp" not in unwrapped(_tree),
      "and nobody is sent to restart a dataplane that is the right one",
      repr(_tree))
check("nothing here to compare" in unwrapped(_tree),
      "the box says there was nothing to compare, rather than going quiet and "
      "letting it read as agreement", repr(_tree))
check("cannot state its own version" not in unwrapped(_tree),
      "and NOT that it cannot state its own version - it stated one, it is "
      "just not a build", repr(_tree))
check(f"  plugin:    {stamp_of(REPO)}" in _tree.splitlines(),
      "the dataplane's build is still printed", repr(_tree))


# ----------------------------------------------------------------------------
print("\n=== a refusal that came back from that dataplane says so (#293) ===")
# WHAT WAS WRONG. Between appliance/install.sh finishing and the box being
# rebooted the new control plane talks to the old dataplane, and it cannot
# create a pool. The refusal named a symbol and nothing else: not that the
# thing answering is the build from before the upgrade, not that the upgrade
# is why, not what clearing it would cost. It read as a broken product rather
# than as a consequence of the upgrade that had just been run.
#
# The reading above is what makes this fixable: the box already knows both
# builds and already knows whether they can be compared at all. This section
# asserts that the SAME decision reaches the refusal, in both directions, and
# that it reaches nothing else.

#: What vat2 printed on the upgraded box.
#
# MEASURED on a box installed from v0.68.0 and upgraded with the current
# tree's installer, with no reboot and no 'systemctl restart vpp':
#
#     error: pool 'split291': Call failed: bpa_pool_add_del
#     error: commit failed; rolling back to the previous state
#
# bpa_pool_add_del grew fields the loaded plugin does not have. This text is
# fed to the REAL vppapi.Vat2 rather than to a Reply written here by hand, so
# the headline below is the product's own reading of what the box produced
# and not this file's idea of it.
VAT2_SAID = "Call failed: bpa_pool_add_del\n"

#: The refusal as it reads today. This change may add to it and may not
#: reword it, so it is asserted verbatim in every case below - the ones where
#: the new sentence fires and the ones where it must not.
REFUSAL = "error: pool 'split291': Call failed: bpa_pool_add_del"


def vat2_answers(argv, timeout=10):
    """A vat2 that refuses the pool the way the upgraded box refused it."""
    if argv[0].endswith("vppctl"):
        return 0, "vpp v26.06-release built by root on host at 2026-06-24\n", ""
    if "--dump-apis" in argv:
        # The plugin's API IS registered. That is the point of this window:
        # the message exists, and it is not the same message any more.
        return 0, "bpa_pool_add_del\nbpa_pool_settings_set\n", ""
    return 1, "", VAT2_SAID


def candidate():
    """A pool being CREATED, which is the change the split window refuses.

    Changing a setting on a pool that already exists goes through in the same
    window - measured, and recorded in #293 - so a candidate that edited a
    running pool would assert nothing.
    """
    cfg = prismash.Config()
    p = prismash.Pool("split291")
    p.inside = ["100.64.0.0/22"]
    p.outside = ["200.150.0.16/30"]
    p.block_size = "512"
    cfg.pools["split291"] = p
    return cfg


def refused(plugins_out, box_version, caps=None, rc=0):
    """`commit`'s apply path, refused by the dataplane, on that box.

    The product's own `Shell.apply` and the product's own `vppapi.Vat2`: the
    only things replaced here are the two subprocesses, which is what decides
    whether this asserts the product or a rewrite of it.
    """
    sh = prismash.Shell.__new__(prismash.Shell)
    sh.dp, sh.frr = Dp(plugins_out, rc), Frr()
    sh.api = vppapi.Vat2(runner=vat2_answers)
    sh.failed = 0
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT} if caps is None else caps
    sh.running_config = lambda: prismash.Config()
    buf = io.StringIO()
    with box_at(box_version):
        with contextlib.redirect_stdout(buf):
            sh.apply(candidate())
    return buf.getvalue()


# --- the box between an upgrade and its reboot: the refusal explains itself -
_r_split = refused(show_plugins("0.1"), BOX)
show("refused while the two builds disagree", _r_split)
check(REFUSAL in _r_split.splitlines(),
      "the refusal itself is unchanged, word for word", repr(_r_split))
check("the dataplane is not the build the rest of this box is"
      in unwrapped(_r_split),
      "and the box says WHICH of the two answered it", repr(_r_split))
check("    the dataplane is running 0.1" in _r_split.splitlines(),
      "the build that answered is named, on a row of its own and whole - it "
      "carries a space, so wrapped into a sentence a fold could break it in "
      "half", repr(_r_split))
check("An upgrade replaces the dataplane's files on disk and does not "
      "restart it" in unwrapped(_r_split),
      "the upgrade is named as the reason, so this does not read as a broken "
      "product", repr(_r_split))
check("ends every session and every port block this box is carrying"
      in unwrapped(_r_split),
      "and what clearing it costs is stated, which is what makes it a "
      "decision for the operator rather than an instruction", repr(_r_split))
check("systemctl restart vpp" in unwrapped(_r_split),
      "with the thing that clears it named", repr(_r_split))

# --- the same refusal on a box whose builds agree: nothing is added ---------
#
# The other direction, and the one that decides whether this is a diagnosis or
# a paragraph printed under every failed commit. A dataplane that refuses a
# pool on an up-to-date box is refusing it for some other reason entirely, and
# an upgrade note there sends the operator to restart vpp - ending every
# session on the box - over something a restart cannot fix.
_r_agree = refused(show_plugins(BOX), BOX)
show("refused while the two builds agree", _r_agree)
check(REFUSAL in _r_agree.splitlines(),
      "the refusal is there, unchanged", repr(_r_agree))
for _alarm in ("not the build the rest of this box is", "An upgrade replaces",
               "systemctl restart vpp", "the dataplane is running"):
    check(_alarm not in unwrapped(_r_agree),
          f"and nothing on the screen says {_alarm!r}", repr(_r_agree))

# --- the three boxes where the comparison is not available -----------------
#
# Not a disagreement, and each must stay silent here rather than guess. A
# source tree compares the repository's VERSION - the number the NEXT release
# will carry - against a build stamp, which is two different kinds of thing;
# #291 shipped that as a false "reboot me" once already and this must not be
# the door it comes back through.
for _what, _plugins, _box, _rc in (
        ("prismash run out of a source tree", show_plugins("0.1"), FROM_TREE, 0),
        ("no VERSION file on the box", show_plugins("0.1"), NOWHERE, 0),
        ("the dataplane did not answer 'show plugins'", "", BOX, 1),
        ("VPP up without bpa_plugin.so", _no_bpa, BOX, 0)):
    _quiet = refused(_plugins, _box, rc=_rc)
    check(REFUSAL in _quiet.splitlines(),
          f"{_what}: the refusal is still printed", repr(_quiet))
    check("An upgrade replaces" not in unwrapped(_quiet),
          f"{_what}: and no upgrade is blamed for it", repr(_quiet))

# --- a refusal the VTY made itself is not blamed on an upgrade --------------
#
# The narrowing this needs. `err` prints grammar errors, permission refusals
# and the VTY's own checks, and the great majority of them never touch the
# dataplane at all. Driven on the same box, disagreeing builds and all, so a
# green here cannot be an accident of the fixture.
_r_perms = refused(show_plugins("0.1"), BOX, caps=set())
show("refused by this VTY, on the same split box", _r_perms)
check("permission denied" in unwrapped(_r_perms),
      "the VTY's own refusal is the one printed", repr(_r_perms))
check(REFUSAL not in _r_perms,
      "and the dataplane was never asked", repr(_r_perms))
check("An upgrade replaces" not in unwrapped(_r_perms),
      "so no upgrade is blamed for a refusal that has nothing to do with the "
      "dataplane", repr(_r_perms))

# --- the widest stamp the build will let through still fits the console -----
#
# The build goes on an unwrapped row, so the one thing that can push it over
# is the stamp itself. bpa.c's STATIC_ASSERT bounds it at 31 columns, which is
# the budget measured above, and this is that budget spent in full.
_r_widest = refused(show_plugins(_fits), BOX)


# ----------------------------------------------------------------------------
print("\n=== and every one of those screens fits the console ===")
# 79 columns, for the reason test_prismash_complete.py pins the constant on: 80
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
            "no plugin": _noplugin, "version unknown": _unknown,
            "source tree": _tree,
            # And the refusal screens, including the one carrying the widest
            # stamp the build will let through. The build is printed on a row
            # that is NOT wrapped, so the stamp's own width is the only thing
            # that can push it over and that is the row measured here.
            "refused, split": _r_split, "refused, agreeing": _r_agree,
            "refused, widest stamp": _r_widest}
_wide = [(n, len(l), l) for n, s in _screens.items()
         for l in s.splitlines() if len(l) > CONSOLE_COLUMNS]
check(not _wide,
      f"none of the {len(_screens)} screens wraps on a serial console: "
      f"the widest line is "
      f"{max(len(l) for s in _screens.values() for l in s.splitlines())} "
      f"columns", str(_wide))


print()
print("=== which unit an operator is sent to, on this box (#426) ===")
# THE SCREEN THIS COMES FROM was `show version` on a maintainer's box whose
# dataplane had stopped: it said `'systemctl status vpp' and its journal say
# why`, and that box runs the containers - vpp.service is a unit nobody
# started, which answers "inactive (dead)" and reads like the answer.
#
# The stub replaces subprocess.run for the one call the resolver makes,
# because what is being measured is the decision and not systemd.
_real_run = prismash.subprocess.run


class _Rc:
    def __init__(self, rc):
        self.returncode = rc


def _run_as(rc, raises=False):
    def _stub(argv, *a, **kw):
        if argv[:2] == ["systemctl", "is-active"]:
            if raises:
                raise OSError("no systemctl here")
            return _Rc(rc)
        return _real_run(argv, *a, **kw)
    return _stub


try:
    prismash.subprocess.run = _run_as(0)
    check(prismash.dataplane_unit() == "prisma-routing-containers",
          "a box whose container stack is active is sent to that unit")
    _adv = prismash.dataplane_journal_advice()
    check("prisma-routing-containers" in _adv and "vpp'" not in _adv,
          "and the advice names it rather than vpp.service", _adv)
    check("docker logs prisma-routing_vpp_1" in _adv,
          "and names the container's log, where the dataplane's own words "
          "are - the unit's journal carries compose's", _adv)

    prismash.subprocess.run = _run_as(3)
    check(prismash.dataplane_unit() == "vpp",
          "a box on the units is still sent to vpp.service")
    check("systemctl status vpp" in prismash.dataplane_journal_advice(),
          "and its advice is the one every box had before 0.117.0")

    # AND THE BOX THAT ALREADY WROTE THE ANSWER DOWN. This is the shape of
    # the field report in #428: the dataplane never started, the container
    # is long gone, the operator rebooted - so `docker logs` answers
    # nothing and the two commands above send them nowhere. The copy
    # appliance/watch-the-dataplane took is the only evidence left, and the
    # advice has to name it INSTEAD OF, not after, a command that is empty.
    _why = tempfile.NamedTemporaryFile(delete=False)
    _why.close()
    _real_why = prismash.DATAPLANE_WHY
    try:
        prismash.DATAPLANE_WHY = _why.name
        prismash.subprocess.run = _run_as(0)
        _adv = prismash.dataplane_journal_advice()
        check(_why.name in _adv,
              "a box that recorded why its dataplane stopped is sent to "
              "that file", _adv)
        check("docker logs" not in _adv,
              "and not to a container log that a reboot already emptied",
              _adv)
    finally:
        prismash.DATAPLANE_WHY = _real_why
        os.unlink(_why.name)

    check(prismash.DATAPLANE_WHY == "/var/log/prisma-routing/why-the-dataplane-stopped",
          "and the path the VTY names is the one watch-the-dataplane writes")

    # A BOX THAT CANNOT BE ASKED answers vpp.service: no systemd, no
    # permission, a container of our own. That is what every box ran before
    # the default moved and what a source install still runs, so it is the
    # answer that is right more often when nothing is known.
    prismash.subprocess.run = _run_as(0, raises=True)
    check(prismash.dataplane_unit() == "vpp",
          "a box that cannot be asked falls back to vpp.service")
finally:
    prismash.subprocess.run = _real_run


if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
