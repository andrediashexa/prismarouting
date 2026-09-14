#!/usr/bin/env python3
"""
The kernel command line, written down twice, and nothing compared the two.

`appliance/hexarouting-tune` builds the line in `render_grub()` and
`LOW_LATENCY_CMDLINE`. `docs/appliance.md` states the same line parameter by
parameter, and states a second list of parameters this appliance deliberately
does NOT set. A reader diagnosing a box reads the document; a booting box reads
the tuner. Nothing made them agree.

That is the shape this repository has already paid for several times - the
IPFIX record written in three places (`test/test_ipfix_record.py`), the paths
in `docs/context.md` (`test/test_context_map.py`), the VTY transcripts in the
docs and the wiki (`test/test_doc_commands.py`). A second copy of a truth is
fine. A second copy nothing compares is not.

So four things are asserted here, and each of them can fail:

  1. Every parameter the tuner puts on the line is in the document's table,
     and every parameter in the table is on the line. A knob added to the
     tuner and not documented is a box doing something undocumented; a knob
     documented and not set is a document promising tuning that is not there.
  2. Where the document states a literal value, it is the value the tuner
     emits. `iommu=pt` and `iommu=on` are different machines.
  3. The parameters the document marks as conditional on HEXA_LOW_LATENCY are
     exactly the ones that disappear with --no-low-latency. This is the rule
     that "anything costing power or a diagnostic goes behind the switch",
     turned into something that fails when it is broken.
  4. Nothing in the document's "still on the table" list is actually set -
     not on either command line, not in apply-tuning, not in sysctl.conf.
     Those rows are hypotheses awaiting #28, and a hypothesis that quietly
     became a shipped knob is the exact failure the section exists to prevent.

What is NOT asserted here, on purpose: the hardware readings the document
records under "What could be measured on this hardware". Those are
measurements taken on a named machine on a given day, the way CHANGELOG.md
records measurements. Re-taking them on whatever runs `make test` would assert
that every machine is that machine, which is false, and would turn a green
suite into a claim nobody made.
"""
import os
import re
import subprocess
import sys
import tempfile

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    return cond


def ok(msg):
    print(f"  [ ok ] {msg}")


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUNE = os.path.join(REPO, "appliance", "hexarouting-tune")
DOC = os.path.join(REPO, "docs", "appliance.md")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_required(path):
    """A file this suite names by path, read so that its ABSENCE is a verdict.

    Reading it directly raised FileNotFoundError and the suite died with no
    verdict line at all - so renaming appliance/etc/sysctl.conf, or moving the
    tuner, turned "this knob is not set" into a traceback. A suite that dies
    has not been read, it has been interrupted, and tools/run-tests.sh says so
    rather than guessing. Same repair as section() above, for the other half.
    """
    try:
        return read(path)
    except OSError as e:
        check(False,
              f"{os.path.relpath(path, REPO)} is named by this suite and "
              f"could not be read ({e.strerror}). Everything this file "
              f"asserts about it is unmeasured, not satisfied")
        return ""


# --------------------------------------------------------------------------
# What the tuner actually emits, taken from the tuner and not from its source:
# a synthetic topology on disk, the real script, and the line it prints.
# --------------------------------------------------------------------------

def w(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(str(content) + "\n")


def synthetic_root(root):
    """One socket, four cores, no SMT, enough RAM, three cores isolated.

    Small on purpose: this file is about WHICH parameters appear, not about
    which cores they name. appliance/test_tune.py already owns the topology
    decisions.
    """
    for n in range(4):
        base = f"{root}/sys/devices/system/cpu/cpu{n}/topology"
        w(f"{base}/physical_package_id", 0)
        w(f"{base}/core_id", n)
        w(f"{base}/thread_siblings_list", n)
    w(f"{root}/sys/devices/system/node/node0/cpulist", "0-3")
    w(f"{root}/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages", 8)
    w(f"{root}/sys/devices/system/cpu/isolated", "1-3")
    w(f"{root}/proc/meminfo", "MemTotal:       67108864 kB")


def grub_line(*extra):
    with tempfile.TemporaryDirectory() as root:
        synthetic_root(root)
        p = subprocess.run(
            [sys.executable, TUNE, "--sysfs-root", root] + list(extra),
            capture_output=True, text=True)
        if p.returncode != 0:
            return None, p.stderr
        for line in p.stdout.splitlines():
            if line.startswith("GRUB_CMDLINE_LINUX_DEFAULT="):
                return line, p.stdout
    return None, "the tuner printed no GRUB_CMDLINE_LINUX_DEFAULT line"


def params(line):
    """{name: value} for the kernel parameters on a GRUB_CMDLINE line.

    The `$GRUB_CMDLINE_LINUX_DEFAULT` the tuner prepends is shell, not a
    parameter, and a bare flag with no `=` maps to None.
    """
    out = {}
    body = line.split("=", 1)[1].strip().strip('"')
    for tok in body.split():
        if tok.startswith("$"):
            continue
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
        else:
            out[tok] = None
    return out


# --------------------------------------------------------------------------
# What the document says.
# --------------------------------------------------------------------------

def section(heading):
    """The text of one section, up to the next heading of the same depth.

    A missing heading returns nothing rather than raising: a renamed section
    must come out as a stated failure with a verdict line, not as a traceback.
    A suite that dies has not been read, it has been interrupted, and
    tools/run-tests.sh says so instead of guessing - so the empty section
    walks into the "this table is unreadable" check below, which names the
    heading that moved.
    """
    text = read_required(DOC)
    marker = "\n" + heading + "\n"
    if marker not in text:
        return ""
    i = text.index(marker)
    depth = len(heading) - len(heading.lstrip("#"))
    nxt = re.compile(r"^#{1,%d} " % depth, re.M)
    m = nxt.search(text, i + len(heading) + 2)
    return text[i:m.start() if m else len(text)]


def rows(text):
    """The cells of every body row of the markdown tables in `text`."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or set("".join(cells)) <= set("-: "):
            continue                      # the ---|--- separator
        out.append(cells)
    return out


CODE = re.compile(r"`([^`]+)`")

print("=== the documented command line, against the one the tuner writes ===")

APPLIED_H = "### The kernel command line, parameter by parameter"
DOC_TABLE = rows(section(APPLIED_H))
DOC_TABLE = [r for r in DOC_TABLE if r and r[0].startswith("`")]
check(len(DOC_TABLE) >= 8,
      f"docs/appliance.md has no readable parameter table under "
      f"\"{APPLIED_H}\" ({len(DOC_TABLE)} rows found). Nothing below this is "
      f"comparing anything")

documented = {}          # name -> stated value, or None when "computed"
low_latency_only = set()
for cells in DOC_TABLE:
    name = CODE.search(cells[0]).group(1)
    value = CODE.search(cells[1]).group(1) if "`" in cells[1] else None
    documented[name] = value
    if "yes" in cells[2].lower():
        low_latency_only.add(name)

line_on, out_on = grub_line()
line_off, out_off = grub_line("--no-low-latency")
check(line_on is not None, f"the tuner emitted no command line: {out_on}")
check(line_off is not None, f"the tuner emitted no command line: {out_off}")

if line_on and line_off:
    emitted = params(line_on)
    emitted_off = params(line_off)

    missing = sorted(set(emitted) - set(documented))
    check(not missing,
          f"the tuner puts parameters on the kernel command line that "
          f"docs/appliance.md does not document: {missing}\n"
          f"    line: {line_on}")
    extra = sorted(set(documented) - set(emitted))
    check(not extra,
          f"docs/appliance.md documents parameters the tuner does not set: "
          f"{extra}\n    line: {line_on}")
    if not missing and not extra:
        ok(f"the same {len(emitted)} parameters in both: "
           f"{', '.join(sorted(emitted))}")

    wrong = {k: (documented[k], emitted[k])
             for k in sorted(set(documented) & set(emitted))
             if documented[k] is not None and documented[k] != emitted[k]}
    check(not wrong,
          "the document and the tuner disagree about a value "
          "(documented, emitted): " + repr(wrong))
    fixed = [k for k in sorted(documented) if documented[k] is not None]
    # An "ok" over an empty list is an artifact reporting success without
    # verifying anything, which is the defect class this repository is
    # organised against. The table being unreadable is already a failure above.
    if not wrong and fixed:
        ok(f"and the {len(fixed)} literal values match: "
           + ", ".join(f"{k}={documented[k]}" for k in fixed))

    dropped = set(emitted) - set(emitted_off)
    check(dropped == low_latency_only,
          f"HEXA_LOW_LATENCY=0 does not drop what the document says it does.\n"
          f"    documented as conditional: {sorted(low_latency_only)}\n"
          f"    actually dropped:          {sorted(dropped)}")
    if dropped == low_latency_only:
        ok(f"HEXA_LOW_LATENCY=0 drops exactly the {len(dropped)} parameters "
           f"marked conditional: {', '.join(sorted(dropped))}")

print("\n=== and the tuning the document says is NOT applied ===")
# These rows are hypotheses waiting on #28. Any of them turning into a shipped
# knob without leaving this table is a gain asserted and not measured, which is
# the failure CONTRIBUTING.md names. So the absence is asserted, not trusted.

CAND_H = "### The candidates, and what each would cost"
CAND = rows(section(CAND_H))
CAND = [r for r in CAND if r and r[0].startswith("`")]
check(len(CAND) >= 5,
      f"docs/appliance.md has no readable candidate table under "
      f"\"{CAND_H}\" ({len(CAND)} rows found). Nothing below this is "
      f"comparing anything")

cand_params, cand_paths = set(), set()
for cells in CAND:
    for tok in CODE.findall(cells[0]):
        (cand_paths if tok.startswith("/") else cand_params).add(
            tok.split("=", 1)[0])

# EVERY FILE THE APPLIANCE SHIPS, not three of them.
#
# This used to read the generated command line, appliance/apply-tuning and
# appliance/etc/sysctl.conf, and nothing else - while the sysfs-path half of
# the same table was already swept across the whole of appliance/. The
# asymmetry was the hole: a candidate parameter can reach a box from a systemd
# unit's ExecStartPre, from the installer, from build-iso.sh writing a boot
# line into the medium, or from a branch of hexarouting-tune that this file's
# one synthetic topology never takes - and none of those was being read. The
# tuner's own source is included for exactly that last reason: rendering one
# topology proves what that topology emits, not what the tuner can emit.
APPLIANCE = os.path.join(REPO, "appliance")


def appliance_files():
    """Every readable file under appliance/, with its repo-relative name."""
    for dirpath, _, names in os.walk(APPLIANCE):
        for n in sorted(names):
            f = os.path.join(dirpath, n)
            try:
                yield os.path.relpath(f, REPO), read(f)
            except (OSError, UnicodeDecodeError):
                continue


SHIPPED = list(appliance_files())
# An empty sweep finds nothing wrong with everything. appliance/ having no
# readable file at all means this check is comparing against nothing, which
# must read as a failure and not as a clean bill.
check(len(SHIPPED) >= 10,
      f"only {len(SHIPPED)} readable files under appliance/: the sweep below "
      f"is looking for candidate knobs in almost nothing and will find none")

# A sweep of a whole directory says nothing about a file that left it. Reading
# appliance/etc/sysctl.conf by name used to raise FileNotFoundError and kill
# the suite with no verdict; walking the tree instead turned that into
# SILENCE, which is worse - a knob moved out of a swept file into an unswept
# one would read as "none of them is set anywhere". So the files this suite is
# actually about are named, and one that is not in the sweep is a failure.
MUST_SWEEP = [
    os.path.join("appliance", "apply-tuning"),
    os.path.join("appliance", "hexarouting-tune"),
    os.path.join("appliance", "selftest"),
    os.path.join("appliance", "build-iso.sh"),
    os.path.join("appliance", "install.sh"),
    os.path.join("appliance", "etc", "sysctl.conf"),
    os.path.join("appliance", "etc", "tune.env"),
    os.path.join("appliance", "systemd", "hexarouting-tune.service"),
]
swept = {name for name, _ in SHIPPED}
absent = [m for m in MUST_SWEEP if m not in swept]
if check(not absent,
         f"these are named by this suite and were not swept: {absent}. Either "
         f"they were renamed and this list must follow, or they are gone - "
         f"and until then everything below is unmeasured about them, not "
         f"satisfied"):
    ok(f"{len(SHIPPED)} files under appliance/ swept, including all "
       f"{len(MUST_SWEEP)} this suite is about")

WHERE_SET = [("the generated kernel command line",
              (line_on or "") + " " + (line_off or ""))] + SHIPPED

clean = True
for name in sorted(cand_params):
    for where, text in WHERE_SET:
        clean &= check(
            not re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-])", text),
            f"`{name}` is listed in docs/appliance.md as tuning NOT applied, "
            f"and {where} sets it. Either it was measured under #28 and the "
            f"document must move it into the applied table, or it was "
            f"switched on without a measurement")
if clean and cand_params:
    ok(f"none of the {len(cand_params)} candidate parameters appears in the "
       f"kernel command line or in any of the {len(SHIPPED)} files under "
       f"appliance/: " + ", ".join(sorted(cand_params)))

# The runtime-state candidates are sysfs paths rather than parameters. The
# governor is the precedent for how one would ship - written by apply-tuning
# on every boot - so apply-tuning is where one would first appear.
written = [(where, path) for where, body in SHIPPED
           for path in sorted(cand_paths) if path in body]
if check(not written,
         "docs/appliance.md lists these as runtime state NOT yet applied, and "
         "the appliance references them: " + repr(written)) and cand_paths:
    ok(f"and neither of the {len(cand_paths)} runtime-state candidates is "
       f"touched by any of them")

overlap = cand_params & set(documented)
check(not overlap,
      f"the same parameter is in both tables - applied and not applied: "
      f"{sorted(overlap)}. One of the two rows is a lie")
if not overlap:
    ok("and no parameter is in both tables at once")

# --------------------------------------------------------------------------
# The kernel the medium installs.
#
# The refusal to build a custom kernel rests on exactly one mechanism: the
# appliance inherits Debian's kernel security updates because it installs an
# unpinned metapackage and chooses no version. Nothing asserted that, so the
# argument in docs/appliance.md was resting on a line of build-iso.sh that
# anybody could change for an unrelated reason - freezing a version to
# reproduce a bug is the obvious one, and it arrives looking like a debugging
# convenience rather than like the decision that section refuses.
# --------------------------------------------------------------------------
print("\n=== the kernel the medium installs ===")

REFUSAL_H = ("### And building our own? No, and for a reason that does "
             "not expire")
KERNEL_PKG = "linux-image-amd64"
ISO_TEXT = read_required(os.path.join(APPLIANCE, "build-iso.sh"))

if check(re.search(r"--include=\S*\b" + re.escape(KERNEL_PKG) + r"\b",
                   ISO_TEXT),
         f"appliance/build-iso.sh does not install `{KERNEL_PKG}` in its "
         f"debootstrap --include list. docs/appliance.md refuses a custom "
         f"kernel on the grounds that the appliance inherits Debian's "
         f"security updates by doing nothing, and that is only true while "
         f"the medium installs Debian's own kernel metapackage"):
    ok(f"the medium installs `{KERNEL_PKG}`, from debootstrap --include")

# A pin is the same loss as a private build, arriving quietly: the box stops
# following Debian's kernel and somebody has to notice the next CVE by hand.
pinned = sorted({m.group(0) for m in re.finditer(
    r"linux-image[\w.+-]*=[\w.:+~-]+|linux-image-\d[\w.+-]*", ISO_TEXT)})
if check(not pinned,
         f"appliance/build-iso.sh pins a kernel version: {pinned}. An "
         f"unpinned metapackage is what makes the security update arrive "
         f"without a decision, so a pin takes on the maintenance cost "
         f"docs/appliance.md refuses a custom kernel over, without the "
         f"argument that section demands"):
    ok("and pins no version, so the security update needs no decision here")

if check(KERNEL_PKG in section(REFUSAL_H),
         f"docs/appliance.md's custom-kernel refusal does not name "
         f"`{KERNEL_PKG}`. Its central cost is that the appliance would stop "
         f"inheriting Debian's kernel updates, and the mechanism it would "
         f"stop inheriting them through is then written down nowhere near "
         f"the argument that depends on it"):
    ok(f"and the refusal in docs/appliance.md names `{KERNEL_PKG}` as why")


if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
