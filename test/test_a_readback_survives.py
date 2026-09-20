#!/usr/bin/env python3
"""
Everything the box says about itself, put back through the box's own parser.

WHAT THIS IS FOR. `Shell.running_config()` builds a candidate out of what the
dataplane, sshd and snmpd report. `save` writes that candidate to
/etc/prisma-routing/prisma-routing.conf. `prisma-routing.service` hands that
file to `prismash --load` at **every boot**, and `--load` refuses a file
WHOLE. So a value the box produced, written out by `save` and refused on the
way back in, is a box that comes up with no configuration at all - on a
machine nobody is watching.

That has happened twice:

  * #357 - a range in `Config._pool_line`, which also reads `show bpa config`.
    A pool the dataplane had built with `block-size 4` came back unmodellable,
    `running_config()` reported that committing would REMOVE it, and every
    commit on that box was refused.
  * 0.110.0 - `address_error` refused `ssh listen-address [::]`, the form
    `sshd -T` gives back, so every commit on an installed box failed. The
    release gate caught it; `make test` was green at 95 suites.

Both were found by something that installs a box. This is the check that does
not need one, and it exists because the two above were each found by accident
of a different check noticing.

THE RULE IT ENFORCES, and it is the #357 rule generalised:

    A value the box can report MUST survive `text()` and `parse()`.
    A RANGE belongs at the prompt, where a request is made.
    The file parser reads FACTS, and refuses only what it cannot parse.

WHAT IT CANNOT SEE, stated rather than left to be discovered:

  * The values a real box actually produces. This drives the range each field
    is DECLARED to hold, which is a claim about the box rather than a
    measurement of one. `lab/install-iso.sh` is where a real box's answers are
    captured, and #419 carries that half.
  * Anything `Config.text()` does not render. A field the candidate holds and
    `save` does not write cannot be refused on the way back, so it is out of
    scope by construction - and the count below is what says how much of the
    model is in scope at all.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src",
                                "prismash"))
import config                                                   # noqa: E402
from config import Config, Pool                                 # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


#: What each readback field can hold, and WHERE THAT COMES FROM. The source is
#: in the table for the reason config.TEXT_LIMITS carries one: a range with no
#: source is a number somebody chose, and the next reader cannot tell it from
#: a measurement.
#:
#: These are what the BOX can report, not what an operator may ask for. Where
#: the two differ the difference is declared in config.DOORS_DIFFER, and
#: test_both_doors.py reads that declaration.
READBACK = [
    ("interface mtu", "mtus", "pg0",
     [68, 576, 1500, 9000, 9216, 65535],
     "device_mtus reads the L3 column of `show interface` and takes any "
     "positive number; VPP's own field is a u32"),
    ("interface vrf", "vrfs", "pg0",
     [1, 5, 4294967295],
     "device_vrfs reads sw_interface_dump; the table id is a u32"),
    ("interface description", "descriptions", "pg0",
     ["uplink", "d" * 64, "up\x07link", "trânsito"],
     "device_descriptions reads the interface tag, which is string tag[64] "
     "in VPP's own vnet/interface.api - 64 BYTES, and anything a byte can be"),
    ("interface mac", "macs", "pg0",
     ["02:00:00:00:00:01", "ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:01"],
     "device_macs reads l2_address from sw_interface_dump, which is six "
     "bytes and carries no opinion about what they are"),
    ("interface address", "addresses", "pg0",
     [["10.0.0.5/8"], ["192.0.2.1/24", "2001:db8::1/64"]],
     "device_addresses reads ip_address_dump, which reports the address and "
     "its prefix length as the box holds them"),
]

print("=== every value the box can report, through save and back ===")
survived, refused = 0, []
for label, field, key, values, source in READBACK:
    for value in values:
        cfg = Config()
        getattr(cfg, field)[key] = value
        text = cfg.text()
        back = Config.parse(text)
        if back.unknown:
            refused.append((label, value, back.unknown[0][2], source))
        else:
            survived += 1
for label, value, why, source in refused:
    shown = value if len(repr(value)) < 30 else f"{str(value)[:18]}..."
    print(f"    {label:24} {shown!r:24} {why[:44]}")
    print(f"      the box can report it: {source}")
check(not refused,
      f"{len(refused)} value(s) the box can report are refused by its own "
      f"parser. A saved file carrying one is a box that comes up with NO "
      f"configuration, because --load refuses a file whole. Move the RANGE to "
      f"the prompt where the request is made, and declare the difference in "
      f"config.DOORS_DIFFER - do not widen this table to make it pass")
ok(f"{survived} value(s) survived text() and parse()")

print("\n=== and the table is not a wish: every field it names is rendered ===")
# A row for a field `text()` does not write is a row that proves nothing,
# because nothing can refuse it on the way back in.
for label, field, key, values, source in READBACK:
    cfg = Config()
    getattr(cfg, field)[key] = values[0]
    check(cfg.text() != Config().text(),
          f"{label}: text() renders nothing for it, so this row tests nothing")
    check(source.strip(), f"{label}: a range with no source")
ok(f"{len(READBACK)} field(s), each rendered and each carrying its source")

print("\n=== the two that have already happened, as regressions ===")
# #357: a pool the dataplane built with a small block.
_p = Config.parse("cgnat pool p\n inside 100.64.0.0/10\n outside "
                  "203.0.113.0/24\n block-size 4\n")
check(not _p.unknown,
      f"#357: `block-size 4` is a pool the dataplane builds and this parser "
      f"must take: {_p.unknown!r}")
# 0.110.0: the form sshd -T gives back.
check(config.address_error("ssh listen-address", "[::]") is None,
      "0.110.0: `[::]` is what sshd -T prints and this parser must take it")
_s = Config.parse("ssh listen-address [::]\n")
check(not _s.unknown and _s.ssh.get("listen-address") == "[::]",
      f"and it survives the file too: {_s.unknown!r} {_s.ssh.get('listen-address')!r}")
ok("both are pinned here, so neither can come back quietly")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
