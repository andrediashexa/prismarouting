#!/usr/bin/env python3
"""
A transceiver's EEPROM, decoded (#590): who made the module, and its optical
levels - temperature, supply voltage, laser bias, TX and RX power - with the
alarm and warning thresholds the module itself carries.

WHY. On 10.40.200.166 (2026-10-06) an X520 port counted 60,674 rx_crc_errors
in 10.4 M frames, and the operator asked for the box's RX power to compare
with the switch's (TX -2.00 dBm, RX -6.54 dBm on the Huawei S6730). The box
had no way to give it: `show hardware-interfaces detail` prints the module's
identity page and nothing of its diagnostics, and the only other way was to
hand the card back to Linux for `ethtool -m`, which takes the dataplane down.

ONE DECODER. The bytes come from two places - the bpa plugin's `show bpa
transceiver` for a card the dataplane owns (through DPDK, see
src/plugins/bpa/bpa_transceiver.c), and this file's own `read` for a card
Linux owns (the module EEPROM ioctl ethtool uses, run on the host through a
door) - in ONE text format, and this module turns either into the same
reading. The VTY, the API and the SNMP agent all decode here, so the three
cannot disagree about a module.

THE LAYOUTS, with the bytes this reads (offsets into the map DPDK and Linux
both hand out):

  SFF-8472 (SFP, 512 bytes; A0h at 0, A2h at 256)
    A0h   0 identifier, 2 connector, 3-10 codes, 20-35 vendor, 37-39 OUI,
          40-55 part, 56-59 revision, 60-61 wavelength, 68-83 serial,
          84-91 date code, 92 diagnostic type (bit 6 DDM, 5 internal,
          4 external calibration, 3 RX power is an average), 94 compliance
    A2h   0-39 thresholds, 2 bytes each, in the order high alarm, low alarm,
          high warning, low warning: temperature, voltage, bias, TX power,
          RX power; 56-75 RX power calibration (five IEEE floats, x^4 first);
          76-91 slope and offset for bias, TX power, temperature, voltage;
          96-105 the readings
  SFF-8636 / SFF-8436 (QSFP, 640 bytes: lower page, page 00h upper at 128,
  page 03h upper at 512)
    lower 0 identifier, 2 status (bit 2 flat memory), 22 temperature,
          26 voltage, 34/42/50 RX power, bias, TX power for lanes 1-4
    00h   130 connector, 147 device technology, 148-163 vendor, 165 OUI,
          168-183 part, 184 revision, 186 wavelength (/20 nm), 196-211
          serial, 212-219 date code, 220 diagnostic type
    03h   512 temperature, 528 voltage, 560 RX power, 568 bias, 576 TX power
          thresholds, high alarm first as above

UNITS, as the documents give them: temperature 1/256 degree C (signed),
voltage 100 uV, bias 2 uA, power 0.1 uW. dBm is 10 log10(mW); a receiver
reading 0 mW has no dBm, and is said as "no light".
"""

import math
import os
import re
import struct
import sys
import textwrap

#: DPDK's (and Linux's) names for the map a module follows, by type number.
TYPES = {1: "sff-8079", 2: "sff-8472", 3: "sff-8636", 4: "sff-8436"}

#: The map's length for each, when it is whole.
LENGTHS = {"sff-8079": 256, "sff-8472": 512, "sff-8636": 640, "sff-8436": 640}

#: SFF-8024's identifiers, the ones a box of this kind meets.
IDENTIFIERS = {0x01: "GBIC", 0x02: "soldered", 0x03: "SFP/SFP+/SFP28",
               0x0B: "DWDM-SFP", 0x0C: "QSFP", 0x0D: "QSFP+",
               0x11: "QSFP28", 0x18: "QSFP-DD", 0x19: "OSFP",
               0x1A: "SFP-DD"}

#: SFF-8024's connector codes.
CONNECTORS = {0x01: "SC", 0x07: "LC", 0x0B: "optical pigtail",
              0x0C: "MPO 1x12", 0x0D: "MPO 2x16", 0x21: "copper pigtail",
              0x22: "RJ45", 0x23: "no separable connector", 0x24: "MXC 2x16",
              0x25: "CS", 0x26: "SN"}

#: The four thresholds of a quantity, in the order the module stores them.
LEVELS = ("high_alarm", "low_alarm", "high_warning", "low_warning")

#: A port whose CRC errors are more than this fraction of the frames it
#: received has a note in `show interface` (#590): 1 in 10,000, 0.01%. Clean
#: optics and fibre count none - a 10G link's specified bit error ratio is
#: 1e-12, a few frames a day - so a ratio this size over the counters' life
#: is a fibre, a connector or a module, not noise. 10.40.200.166's was 0.58%,
#: 58 times over it. MIN_CRC_ERRORS keeps a link that took a handful while it
#: came up from saying so for the rest of its life.
CRC_NOTE_RATIO = 0.0001
MIN_CRC_ERRORS = 10

#: What reads a card Linux owns: this file, as the host has it installed.
#: The door (src/prismash/doors.py) accepts exactly this path; a lab, with no
#: door and the file at /src, points PRISMA_TRANSCEIVER_PROGRAM at its copy.
PROGRAM = "/usr/lib/prisma-routing/transceiver.py"
PYTHON = "/usr/bin/python3"

#: SIOCETHTOOL and the two commands `ethtool -m` sends (linux/ethtool.h).
SIOCETHTOOL = 0x8946
ETHTOOL_GMODULEINFO = 0x42
ETHTOOL_GMODULEEEPROM = 0x43

#: A netdev name as the reader takes it: never an option, never a path.
NETDEV = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:@-]{0,14}$")


def read_argv(dev, program=None):
    """
    The argv that reads `dev`'s module on the host - the shape the door
    accepts (doors.query_of) and the SNMP agent sends, built in one place.
    """
    return (PYTHON, program or os.environ.get("PRISMA_TRANSCEIVER_PROGRAM",
                                              PROGRAM), "read", dev)


# --------------------------------------------------------------- the dump

def _digits(word):
    """ASCII digits and nothing else - what int() takes as this file means
    it. Not str's isdigit, which is true for a superscript two that int()
    refuses (test/test_prismash_config.py holds the VTY to that)."""
    return bool(re.fullmatch(r"[0-9]+", word or ""))


def parse_dump(text):
    """
    `show bpa transceiver`'s text (or this file's `read`'s) as
    {name: reading}, in the order it came. A reading is

      {"name", "source", "type", "length", "port", "device", "age",
       "bytes", "unread", "why"}

    `bytes` a list of `length` entries, an int for each byte read and None
    for one that was not (the plugin prints `--`); `unread` the reason word
    when there are no bytes at all (not-dpdk, unavailable, no-module,
    no-device), with `why` its sentence. Lines it does not recognise are
    skipped: a dump is data, not a protocol to fail on.
    """
    out = {}
    cur = None
    for line in (text or "").splitlines():
        line = line.rstrip("\r")
        w = line.split()
        if len(w) >= 3 and w[0] == "interface" and not line[:1].isspace():
            name = w[1]
            cur = {"name": name, "source": "", "type": "", "length": 0,
                   "port": None, "device": "", "age": None, "bytes": [],
                   "unread": None, "why": ""}
            out[name] = cur
            if w[2] == "unread":
                cur["unread"] = w[3] if len(w) > 3 else "unavailable"
                cur["why"] = " ".join(w[4:])
                cur = None
                continue
            kv = dict(zip(w[2::2], w[3::2]))
            cur["source"] = kv.get("source", "")
            cur["type"] = kv.get("type", "")
            try:
                cur["length"] = min(int(kv.get("length", "0")), 640)
            except ValueError:
                cur["length"] = 0
            if _digits(kv.get("port", "")):
                cur["port"] = int(kv["port"])
            cur["device"] = kv.get("device", "")
            try:
                cur["age"] = float(kv["age"]) if "age" in kv else None
            except ValueError:
                cur["age"] = None
            cur["bytes"] = [None] * cur["length"]
            continue
        if cur is None or len(w) < 2 or not line[:1].isspace():
            continue
        try:
            at = int(w[0], 16)
        except ValueError:
            continue
        for i, b in enumerate(w[1:17]):
            if at + i >= cur["length"]:
                break
            if b != "--":
                try:
                    cur["bytes"][at + i] = int(b, 16) & 0xFF
                except ValueError:
                    pass
    return out


def dump_lines(name, source, type_name, data, valid=None, extra=""):
    """
    One module's bytes in the dump's own format - what `read` prints for a
    card Linux owns, so parse_dump() reads both sources the same way.
    `valid` is a list of booleans, None for every byte read.
    """
    head = f"interface {name} source {source} type {type_name} " \
           f"length {len(data)}" + (f" {extra}" if extra else "")
    out = [head]
    for row in range(0, len(data), 16):
        cells = range(row, min(row + 16, len(data)))
        if valid is not None and not any(valid[i] for i in cells):
            continue
        out.append(f"  {row:04x} " + " ".join(
            f"{data[i]:02x}" if valid is None or valid[i] else "--"
            for i in cells))
    return out


# ------------------------------------------------------------ the numbers

def mw_to_dbm(mw):
    """
    `mw` milliwatts in dBm; None for no reading, -inf for 0 mW - a receiver
    with no light has no dBm, and printing a large negative number for it
    would be a level, which it is not.
    """
    if mw is None:
        return None
    if mw <= 0:
        return float("-inf")
    return 10.0 * math.log10(mw)


def fmt_dbm(dbm):
    """dBm as the screens print it: two decimals, "-inf" for no light,
    "-" for none."""
    if dbm is None:
        return "-"
    if dbm == float("-inf"):
        return "-inf"
    return f"{dbm:.2f}"


def level_of(value, th):
    """
    Where `value` stands against the thresholds `th` (a dict of LEVELS, or
    None): "high-alarm", "low-alarm", "high-warning", "low-warning" or
    "normal"; "" when there is no value or no thresholds to compare with.
    Strictly beyond, as SFF-8472 sets its flags: a value AT a threshold is
    not over it. The alarm before the warning, since an alarm is past both.
    """
    if value is None or not th:
        return ""
    if value > th["high_alarm"]:
        return "high-alarm"
    if value < th["low_alarm"]:
        return "low-alarm"
    if value > th["high_warning"]:
        return "high-warning"
    if value < th["low_warning"]:
        return "low-warning"
    return "normal"


def thresholds(values):
    """
    The four thresholds `values` (in LEVELS order) as a dict, or None when
    the module carries none: all four zero (what a module that implements no
    alarms writes there), or an order no threshold set can have - a high
    below its low. Read as real thresholds, either would put every reading
    in alarm.
    """
    if values is None or any(v is None for v in values):
        return None
    th = dict(zip(LEVELS, values))
    if all(v == 0 for v in values):
        return None
    if th["high_alarm"] < th["low_alarm"] or \
            th["high_warning"] < th["low_warning"]:
        return None
    return th


def _u16(b, at):
    """The big-endian unsigned 16 bits at `at`, None if either is unread."""
    if at + 1 >= len(b) or b[at] is None or b[at + 1] is None:
        return None
    return (b[at] << 8) | b[at + 1]


def _s16(b, at):
    """The big-endian two's complement 16 bits at `at`, or None."""
    v = _u16(b, at)
    return None if v is None else (v - 0x10000 if v & 0x8000 else v)


def _f32(b, at):
    """The big-endian IEEE single at `at`, or None."""
    if at + 3 >= len(b) or any(x is None for x in b[at:at + 4]):
        return None
    return struct.unpack(">f", bytes(b[at:at + 4]))[0]


def _text(b, start, end):
    """ASCII from `start` to `end`, the padding stripped; "" when unread."""
    raw = b[start:end]
    if len(raw) < end - start or any(x is None for x in raw):
        return ""
    s = "".join(chr(x) if 32 <= x < 127 else " " for x in raw)
    return s.strip()


def _date(b, start):
    """SFF's date code YYMMDDLL as "20YY-MM-DD", the lot dropped; the raw
    characters when they are not a date."""
    raw = _text(b, start, start + 8)
    if len(raw) >= 6 and _digits(raw[:6]):
        return f"20{raw[0:2]}-{raw[2:4]}-{raw[4:6]}"
    return raw


def _oui(b, at):
    """The vendor's IEEE OUI, xx:xx:xx; "" when unread or all zero."""
    raw = b[at:at + 3]
    if len(raw) < 3 or any(x is None for x in raw) or not any(raw):
        return ""
    return ":".join(f"{x:02x}" for x in raw)


def _byte(b, at):
    """The byte at `at`, None when unread or past the map."""
    return b[at] if at < len(b) else None


# -------------------------------------------------------------- SFF-8472

def sff8472_calibrate(raw, slope, offset, signed=False):
    """
    SFF-8472's external calibration, formula 1: slope x reading + offset,
    the slope an unsigned 8.8 fixed-point number and the offset signed, in
    the reading's own units. None in, None out.
    """
    if raw is None or slope is None or offset is None:
        return None
    if signed and raw & 0x8000:
        raw -= 0x10000
    return (slope / 256.0) * raw + offset


def sff8472_rx_calibrate(raw, coeffs):
    """
    SFF-8472's external calibration of RX power, formula 2: the polynomial
    Rx_PWR(4) x^4 + ... + Rx_PWR(0), the coefficients IEEE floats in the
    module (`coeffs` x^0 first). A negative result is clamped to 0: the
    curve is fitted over the receiver's range and below it says nothing.
    """
    if raw is None or coeffs is None or any(c is None for c in coeffs):
        return None
    return max(sum(c * raw ** i for i, c in enumerate(coeffs)), 0.0)


def _copper_sfp(b):
    """
    Is this SFP a copper one - a direct-attach cable (SFP+ cable technology
    bits in byte 8), a copper pigtail, or an RJ45 module - and so has no
    optical levels to read?
    """
    conn, tech = _byte(b, 2), _byte(b, 8)
    return bool((tech is not None and tech & 0x0C) or conn in (0x21, 0x22))


def decode_sff8472(b, has_a2):
    """
    An SFP's identity and, when it has them, its diagnostics, from the map
    `b` (parse_dump's list). `has_a2` is whether the reader mapped page A2h:
    DPDK says sff-8079 for a module that declares no SFF-8472 compliance OR
    needs the address change ixgbe does not do, so the A2h bytes may be
    absent while byte 92 says the module has them.
    """
    m = _identity_sfp(b)
    diag = _byte(b, 92)
    if diag is None:
        m.update(status="unreadable",
                 why="the module's identity page was not read")
        return m
    if not diag & 0x40:
        if m["copper"]:
            m.update(status="copper",
                     why="a copper module (direct-attach or RJ45): no "
                         "optical levels")
        else:
            m.update(status="no-ddm",
                     why="the module does not implement diagnostics (DDM)")
        return m
    m["calibration"] = "external" if diag & 0x10 and not diag & 0x20 \
        else "internal"
    m["rx_power_kind"] = "average" if diag & 0x08 else "OMA"
    if not has_a2 or all(x is None for x in b[256 + 96:256 + 106]):
        m.update(status="no-ddm",
                 why="the module has diagnostics, but its A2h page was not "
                     "read" + (" (it needs an address change the driver "
                               "does not do)" if diag & 0x04 else ""))
        return m
    a2 = 256
    ext = m["calibration"] == "external"
    cal = None
    if ext:
        cal = {"bias": (_u16(b, a2 + 76), _s16(b, a2 + 78)),
               "tx": (_u16(b, a2 + 80), _s16(b, a2 + 82)),
               "temp": (_u16(b, a2 + 84), _s16(b, a2 + 86)),
               "volt": (_u16(b, a2 + 88), _s16(b, a2 + 90)),
               "rx": [_f32(b, a2 + 56 + 4 * (4 - i)) for i in range(5)]}

    # not a unit: a closure of decode_sff8472 over `b` and `cal`, measured
    # through it by test/test_transceiver.py's internal and external maps
    def value(at, kind):
        if kind == "temp":
            raw = _u16(b, at)
            if ext:
                v = sff8472_calibrate(raw, *cal["temp"], signed=True)
            else:
                v = _s16(b, at)
            return None if v is None else v / 256.0
        raw = _u16(b, at)
        if ext and kind == "rx":
            v = sff8472_rx_calibrate(raw, cal["rx"])
        elif ext:
            v = sff8472_calibrate(raw, *cal[kind])
            v = None if v is None else max(v, 0.0)
        else:
            v = raw
        if v is None:
            return None
        return {"volt": v / 10000.0, "bias": v * 0.002,
                "tx": v / 10000.0, "rx": v / 10000.0}[kind]

    # not a unit: a closure of decode_sff8472, measured through it as above
    def quantity(th_at, val_at, kind):
        v = value(a2 + val_at, kind)
        th = thresholds([value(a2 + th_at + 2 * i, kind) for i in range(4)])
        return {"value": v, "thresholds": th, "level": level_of(v, th)}

    m["status"] = "ddm"
    m["why"] = ""
    m["temperature"] = quantity(0, 96, "temp")
    m["voltage"] = quantity(8, 98, "volt")
    m["lanes"] = [{"lane": 1, "bias": quantity(16, 100, "bias"),
                   "tx_power": quantity(24, 102, "tx"),
                   "rx_power": quantity(32, 104, "rx")}]
    return m


def _identity_sfp(b):
    """The identity fields of an SFP's page A0h."""
    copper = _copper_sfp(b)
    wl = _u16(b, 60)
    return {"identifier": IDENTIFIERS.get(_byte(b, 0), _hexname(_byte(b, 0))),
            "connector": CONNECTORS.get(_byte(b, 2), _hexname(_byte(b, 2))),
            "vendor": _text(b, 20, 36), "oui": _oui(b, 37),
            "part": _text(b, 40, 56), "revision": _text(b, 56, 60),
            "serial": _text(b, 68, 84), "date": _date(b, 84),
            "wavelength_nm": wl if wl and not copper else None,
            "copper": copper, "calibration": "", "rx_power_kind": ""}


def _hexname(v):
    """A code no table names, as its number; "" when unread."""
    return "" if v is None else f"0x{v:02x}"


# ---------------------------------------------------------- SFF-8636/8436

def decode_sff8636(b):
    """
    A QSFP's identity and diagnostics from the map `b`: the module's
    temperature and voltage, and per lane (1-4) its bias, TX and RX power.
    The thresholds are page 03h's, which a flat-memory module, or a map of
    256 bytes, does not have: its levels are then shown with none.
    """
    tech = _byte(b, 147)
    copper = tech is not None and (tech >> 4) >= 0x0A
    wl = _u16(b, 186)
    m = {"identifier": IDENTIFIERS.get(_byte(b, 128), _hexname(_byte(b, 128)))
         if _byte(b, 128) is not None
         else IDENTIFIERS.get(_byte(b, 0), _hexname(_byte(b, 0))),
         "connector": CONNECTORS.get(_byte(b, 130), _hexname(_byte(b, 130))),
         "vendor": _text(b, 148, 164), "oui": _oui(b, 165),
         "part": _text(b, 168, 184), "revision": _text(b, 184, 186),
         "serial": _text(b, 196, 212), "date": _date(b, 212),
         "wavelength_nm": (wl // 20) if wl and not copper else None,
         "copper": copper, "calibration": "internal", "rx_power_kind": ""}
    diag = _byte(b, 220)
    if _u16(b, 22) is None:
        m.update(status="unreadable", why="the module's lower page was not read")
        return m
    if copper:
        m.update(status="copper", calibration="",
                 why="a copper cable: no optical levels")
        return m
    m["rx_power_kind"] = "average" if diag is not None and diag & 0x08 \
        else "OMA"
    tx_known = diag is None or bool(diag & 0x04)
    flat = _byte(b, 2) is not None and _byte(b, 2) & 0x04

    # not a unit: a closure of decode_sff8636 over `b` and `flat`, measured
    # through it by test/test_transceiver.py's QSFP maps
    def th(at, scale, signed=False):
        if flat:
            return None
        get = _s16 if signed else _u16
        vals = [get(b, at + 2 * i) for i in range(4)]
        return thresholds(None if any(v is None for v in vals)
                          else [v * scale for v in vals])

    # not a unit: a closure of decode_sff8636, measured through it as above
    def q(raw, scale, t):
        v = None if raw is None else raw * scale
        return {"value": v, "thresholds": t, "level": level_of(v, t)}

    t_rx, t_bias = th(560, 0.0001), th(568, 0.002)
    t_tx = th(576, 0.0001)
    m["status"] = "ddm"
    m["why"] = ""
    m["temperature"] = q(_s16(b, 22), 1 / 256.0, th(512, 1 / 256.0, True))
    m["voltage"] = q(_u16(b, 26), 0.0001, th(528, 0.0001))
    m["lanes"] = [{"lane": i + 1,
                   "bias": q(_u16(b, 42 + 2 * i), 0.002, t_bias),
                   "tx_power": q(_u16(b, 50 + 2 * i) if tx_known else None,
                                 0.0001, t_tx),
                   "rx_power": q(_u16(b, 34 + 2 * i), 0.0001, t_rx)}
                  for i in range(4)]
    return m


# ---------------------------------------------------------- the reading

#: What a reading without bytes says, by the reader's reason word.
UNREAD = {
    "not-dpdk": ("unreadable", "the dataplane does not drive this card "
                               "through DPDK"),
    "unavailable": ("unreadable", ""),
    "no-device": ("absent", "the host has no such card"),
}


def decode(reading):
    """
    One parse_dump() reading as a module:

      {"name", "source", "device", "status", "why", identity fields...,
       "temperature", "voltage", "lanes"}

    `status` is "ddm" (levels below), "no-ddm", "copper", "absent" or
    "unreadable", with `why` the sentence for the four without levels.
    `temperature` and `voltage` are {"value", "thresholds", "level"} and each
    lane carries "bias", "tx_power" and "rx_power" the same way: degrees C,
    volts, mA and mW.
    """
    base = {"name": reading["name"], "source": reading.get("source", ""),
            "device": reading.get("device", ""), "type": reading.get("type", ""),
            "identifier": "", "connector": "", "vendor": "", "oui": "",
            "part": "", "revision": "", "serial": "", "date": "",
            "wavelength_nm": None, "copper": False, "calibration": "",
            "rx_power_kind": "", "temperature": None, "voltage": None,
            "lanes": []}
    if reading.get("unread"):
        reason = reading["unread"]
        why = reading.get("why", "")
        if reason == "no-module":
            # ENOTSUP is a driver with no module access at all (virtio, the
            # null PMD, a veth); anything else is a cage that did not answer.
            if "-95" in why or "not supported" in why.lower():
                return {**base, "status": "unreadable",
                        "why": "the card's driver cannot read a module"}
            return {**base, "status": "absent",
                    "why": "no module answered: the cage is empty, or the "
                           "module cannot be read"}
        status, said = UNREAD.get(reason, ("unreadable", ""))
        return {**base, "status": status, "why": said or why or reason}
    b = reading.get("bytes") or []
    t = reading.get("type", "")
    ident = _byte(b, 128) if t in ("sff-8636", "sff-8436") else _byte(b, 0)
    if ident is None and t not in ("sff-8636", "sff-8436"):
        return {**base, "status": "unreadable",
                "why": "the module's identity page was not read"}
    if ident in (0x00, 0xFF):
        return {**base, "status": "absent",
                "why": "the module's EEPROM is blank: no module, or one "
                       "not programmed"}
    if t in ("sff-8636", "sff-8436"):
        return {**base, **decode_sff8636(b)}
    if t in ("sff-8472", "sff-8079"):
        return {**base, **decode_sff8472(b, t == "sff-8472")}
    return {**base, "status": "unreadable",
            "why": f"a module map this box does not decode ({t or 'none'})"}


# ------------------------------------------------------------- the screen

#: The rows of the table, per lane: label, the lane's key, how many
#: decimals, and whether the row is the dBm view of a power.
_UNITS = (("temp (C)", "temperature", 2), ("voltage (V)", "voltage", 3))


def _cell(v, decimals):
    """One number of the table, "-" for none."""
    return "-" if v is None else f"{v:.{decimals}f}"


def level_word(level):
    """The table's state column: "ok", the crossing, or "-" for unknown."""
    return {"normal": "ok", "": "-"}.get(level, level.replace("-", " ")
                                         .replace("warning", "warn"))


def _row(label, q, decimals, dbm=False):
    """One row of the table for the quantity `q`, its thresholds beside it."""
    th = (q or {}).get("thresholds") or {}
    v = (q or {}).get("value")
    if dbm:
        cells = [fmt_dbm(mw_to_dbm(v))] + [
            fmt_dbm(mw_to_dbm(th[k])) if th else "-" for k in
            ("high_alarm", "high_warning", "low_warning", "low_alarm")]
    else:
        cells = [_cell(v, decimals)] + [
            _cell(th.get(k), decimals) if th else "-" for k in
            ("high_alarm", "high_warning", "low_warning", "low_alarm")]
    word = level_word((q or {}).get("level", ""))
    if dbm and v is not None and v <= 0:
        word = "no light" if label.startswith("rx") else word
    return (f"    {label:<14}{cells[0]:>8}{cells[1]:>10}{cells[2]:>10}"
            f"{cells[3]:>10}{cells[4]:>10}  {word}")


def screen(m):
    """
    `show interface <name> transceiver` for one decoded module, as lines -
    the identity, then, for a module with diagnostics, the levels beside
    their thresholds, power in mW and in dBm. Laid out like the switch's own
    `display transceiver verbose` and Junos' `show interfaces diagnostics
    optics` read: a value, its four thresholds, and whether it is past one.
    Every line fits 79 columns.
    """
    out = []
    what = ", ".join(x for x in (
        m.get("identifier"), connector_words(m.get("connector")),
        f"{m['wavelength_nm']} nm" if m.get("wavelength_nm") else "") if x)
    out.append(f"  {m['name']}" + (f": {what}" if what else ""))
    if m["status"] in ("absent", "unreadable") and not m.get("vendor"):
        return out + wrap(m["why"], "    ", "    ")
    out.append(f"    vendor      {m['vendor'] or '-'}"
               + (f" (OUI {m['oui']})" if m.get("oui") else ""))
    out.append(f"    part        {m['part'] or '-'}"
               + (f"  revision {m['revision']}" if m.get("revision") else ""))
    out.append(f"    serial      {m['serial'] or '-'}"
               + (f"  date {m['date']}" if m.get("date") else ""))
    src = {"dpdk": "the dataplane, through DPDK", "linux": "Linux",
           "stand-in": "a stand-in (a lab's bytes, not a module)"}.get(
               m.get("source"), m.get("source") or "-")
    if m.get("device"):
        src += f" ({m['device']})"
    out.append(f"    read from   {src}")
    if m["status"] != "ddm":
        return out + wrap(f"none: {m['why']}", "    ddm         ",
                          " " * 16)
    kind = m.get("rx_power_kind")
    out.append(f"    ddm         {m['calibration']}ly calibrated"
               + (f"; rx power is {'an ' if kind == 'average' else ''}"
                  f"{kind}" if kind else ""))
    out.append("")
    out.append(f"    {'':<14}{'value':>8}{'hi alarm':>10}{'hi warn':>10}"
               f"{'lo warn':>10}{'lo alarm':>10}  state")
    for label, key, dec in _UNITS:
        out.append(_row(label, m.get(key), dec))
    many = len(m.get("lanes") or []) > 1
    for ln in m.get("lanes") or []:
        n = str(ln["lane"]) if many else ""
        out.append(_row(f"bias{n} (mA)" if many else "bias (mA)",
                        ln["bias"], 3))
        for key, word in (("tx_power", "tx"), ("rx_power", "rx")):
            name = f"{word}{n}" if many else f"{word} power"
            out.append(_row(f"{name} (mW)", ln[key], 4))
            out.append(_row(f"{name} (dBm)", ln[key], 2, dbm=True))
    return out


def connector_words(code):
    """The connector as the first line says it: "LC connector" for a
    connector's name, the words alone for what is not one ("copper
    pigtail", "no separable connector"); "" for none."""
    if not code:
        return ""
    if code in ("SC", "LC", "CS", "SN") or code.startswith(("MPO", "MXC")):
        return f"{code} connector"
    return code


def wrap(text, first, rest, width=79):
    """`text` folded at `width` columns, the first line after `first` and
    the rest after `rest` - every line the screens print fits a console."""
    return textwrap.wrap(text, width=width, initial_indent=first,
                         subsequent_indent=rest, break_on_hyphens=False) \
        or [first.rstrip()]


# ------------------------------------------------- notes for show interface

def rx_power_notes(m):
    """
    The `show interface` notes for a module whose RX power is outside its
    warning thresholds (an alarm is outside them too), one per lane, or
    none. Says the level, the threshold it crossed, and where the rest is.
    """
    out = []
    if m.get("status") != "ddm":
        return out
    many = len(m.get("lanes") or []) > 1
    for ln in m["lanes"]:
        q = ln["rx_power"]
        lvl = q.get("level") or ""
        if lvl in ("", "normal"):
            continue
        th = q["thresholds"]
        side = "high" if lvl.startswith("high") else "low"
        bound = th[f"{side}_warning"]
        lane = f" lane {ln['lane']}" if many else ""
        v = mw_to_dbm(q["value"])
        level = "no light" if v == float("-inf") else f"{fmt_dbm(v)} dBm"
        out += wrap(f"{m['name']}: rx power{lane} {level}, "
                    f"{'above' if side == 'high' else 'below'} its {side} "
                    f"warning ({fmt_dbm(mw_to_dbm(bound))} dBm)"
                    + (" and its alarm" if lvl.endswith("alarm") else ""),
                    "  ", "    ")
    if out:
        out += wrap(f"'show interface {m['name']} transceiver' has the "
                    "module's levels", "    ", "    ")
    return out


def crc_note(name, frames, errors):
    """
    The `show interface` note for a port whose CRC errors are more than
    CRC_NOTE_RATIO of what it received over the counters' life (#590), or
    none. A note and not an error: the port forwards, and what it says is
    where to look - the fibre, the connector, the module at either end.
    `frames` counts the good ones; the ratio is of everything received.
    """
    if errors is None or frames is None or errors < MIN_CRC_ERRORS:
        return []
    total = frames + errors
    if total <= 0 or errors / total <= CRC_NOTE_RATIO:
        return []
    pct = 100.0 * errors / total
    return wrap(f"{name}: {pct:.2f}% of received frames failed their CRC "
                f"({errors:,} of {total:,}) since the counters started: "
                "look at the fibre, the connectors and the modules at both "
                f"ends; 'show interface {name} transceiver' has this end's",
                "  ", "    ")


def crc_counts(hw_text):
    """
    {name: (good frames, crc errors)} from `show hardware-interfaces`, which
    prints a DPDK port's non-zero extended statistics under its own row:
    rx_good_packets beside rx_crc_errors (ixgbe, i40e, ice) or
    rx_crc_errors_phy (mlx5). A port with neither is left out - no counter is
    not a clean counter.
    """
    out = {}
    cur = None
    for line in (hw_text or "").splitlines():
        w = line.split()
        if not w:
            continue
        if not line[:1].isspace() and len(w) >= 2 and _digits(w[1]):
            cur = w[0]
            continue
        if cur is None or len(w) != 2 or not _digits(w[1]):
            continue
        good, bad = out.get(cur, (None, None))
        if w[0] == "rx_good_packets":
            good = int(w[1])
        elif w[0] in ("rx_crc_errors", "rx_crc_errors_phy"):
            bad = (bad or 0) + int(w[1])
        else:
            continue
        out[cur] = (good, bad)
    return {n: (g or 0, b) for n, (g, b) in out.items() if b is not None}


# ----------------------------------------------------- the API's fields

def _quantity_fields(q, power=False):
    """One quantity as the API's TransceiverLevel fields."""
    q = q or {}
    v = q.get("value")
    th = q.get("thresholds") or {}
    f = {"known": v is not None, "value": v if v is not None else 0.0,
         "thresholds_known": bool(th),
         "level": "LEVEL_" + (q.get("level") or "unknown").upper()
                  .replace("-", "_")}
    for k in LEVELS:
        f[k] = th.get(k, 0.0)
    if power:
        d = mw_to_dbm(v)
        f["no_light"] = v is not None and v <= 0
        f["dbm"] = d if d is not None and d != float("-inf") else 0.0
        for k in LEVELS:
            dk = mw_to_dbm(th.get(k)) if th else None
            f[f"{k}_dbm"] = dk if dk is not None and dk != float("-inf") \
                else 0.0
    return f


def transceiver_fields(m):
    """
    One decoded module as ListTransceivers' Transceiver fields (#590): the
    identity, the status and its sentence, and every level with its
    thresholds and where it stands - powers in mW and dBm both.
    """
    f = {"name": m["name"], "source": m.get("source") or "",
         "device": m.get("device") or "", "map": m.get("type") or "",
         "status": "STATUS_" + m["status"].upper().replace("-", "_"),
         "why": m.get("why") or "",
         "identifier": m.get("identifier") or "",
         "connector": m.get("connector") or "",
         "vendor": m.get("vendor") or "", "oui": m.get("oui") or "",
         "part_number": m.get("part") or "",
         "revision": m.get("revision") or "",
         "serial_number": m.get("serial") or "",
         "date_code": m.get("date") or "",
         "wavelength_nm": m.get("wavelength_nm") or 0,
         "copper": bool(m.get("copper")),
         "calibration": m.get("calibration") or "",
         "rx_power_average": m.get("rx_power_kind") == "average"}
    if m["status"] == "ddm":
        f["temperature_c"] = _quantity_fields(m["temperature"])
        f["voltage_v"] = _quantity_fields(m["voltage"])
        f["lanes"] = [{"lane": ln["lane"],
                       "bias_ma": _quantity_fields(ln["bias"]),
                       "tx_power_mw": _quantity_fields(ln["tx_power"], True),
                       "rx_power_mw": _quantity_fields(ln["rx_power"], True)}
                      for ln in m["lanes"]]
    else:
        f["lanes"] = []
    return f


# ---------------------------------------------- reading a card Linux owns

def module_ranges(type_name, length):
    """
    The (offset, length) ranges of a map worth reading: the identity, the
    thresholds and calibration, and the readings - what the plugin's
    bpa_xcvr_ranges() asks DPDK for with its static part, so a card Linux
    owns is read the same way as one the dataplane owns.
    """
    if type_name == "sff-8079" and length >= 96:
        return [(0, 96)]
    if type_name == "sff-8472" and length >= 376:
        return [(0, 96), (256, 96), (352, 24)]
    if type_name in ("sff-8636", "sff-8436") and length >= 256:
        return [(128, 128)] + ([(512, 72)] if length >= 640 else []) \
            + [(0, 58)]
    return []


def linux_read(dev, ioctl=None):
    """
    `dev`'s module, read the way `ethtool -m` reads it - SIOCETHTOOL with
    ETHTOOL_GMODULEINFO, then ETHTOOL_GMODULEEEPROM per range - as the
    dump's lines. Needs CAP_NET_ADMIN, which is why it runs on the host
    through a door. `ioctl(name, request_bytes)` -> reply bytes is what a
    test hands in; the default is the kernel's.
    """
    if not NETDEV.match(dev or ""):
        return [f"interface {dev} unread no-device not a card name"]
    ioctl = ioctl or _kernel_ioctl
    try:
        info = ioctl(dev, struct.pack("=III32x", ETHTOOL_GMODULEINFO, 0, 0))
    except OSError as e:
        word = "no-device" if e.errno == 19 else "no-module"
        return [f"interface {dev} unread {word} the card answered "
                f"-{e.errno} ({os.strerror(e.errno)})"]
    _, mtype, mlen = struct.unpack_from("=III", info)
    name = TYPES.get(mtype, "unknown")
    mlen = min(mlen, 640)
    data, valid = [0] * mlen, [False] * mlen
    for off, n in module_ranges(name, mlen):
        try:
            got = ioctl(dev, struct.pack("=IIII", ETHTOOL_GMODULEEEPROM, 0,
                                         off, n) + bytes(n))
        except OSError as e:
            return [f"interface {dev} unread no-module the card answered "
                    f"-{e.errno} ({os.strerror(e.errno)})"]
        data[off:off + n] = list(got[16:16 + n])
        valid[off:off + n] = [True] * n
    return dump_lines(dev, "linux", name, data, valid)


# not a unit: the kernel's ioctl on a live socket; linux_read() is tested
# with a stand-in ioctl, and lab/transceiver.sh drives this on a veth
def _kernel_ioctl(dev, request):
    import array
    import fcntl
    import socket
    buf = array.array("B", request)
    addr, _ = buf.buffer_info()
    ifr = struct.pack("16sP16x", dev.encode()[:15], addr)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        fcntl.ioctl(s.fileno(), SIOCETHTOOL, ifr)
    return buf.tobytes()


# not a unit: the program's entry, which the door runs; linux_read() is
# what it prints
def main(argv):
    if len(argv) != 3 or argv[1] != "read":
        sys.stderr.write("usage: transceiver.py read <netdev>\n")
        return 2
    print("\n".join(linux_read(argv[2])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
