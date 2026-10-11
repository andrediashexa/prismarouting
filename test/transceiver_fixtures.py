"""
Module EEPROM maps for #590, built byte by byte from the published layouts
(SFF-8472 rev 12.4, SFF-8636 rev 2.10a, SFF-8024 for the codes) - the bytes
a real module carries, not the decoder's view of them.

Shared by test/test_transceiver.py (the decode), the VTY, API and SNMP
suites, and lab/transceiver.sh, which hands them to the plugin as a
stand-in module: one set of modules, so the lab and the suites read the same
ones.

The X520's module is modelled on the one measured on 10.40.200.166
(2026-10-06): Rei do SFP RSMPP8510GL-03D, revision 2.1, serial FP23H150378,
date code 230815. Its levels are not known - the box could not read them,
which is the issue - so the ones here are the switch's own end of that link
(TX -2.00 dBm, RX -6.54 dBm) moved a little, and its thresholds are a
typical 10G LR's.
"""
import struct


def _put_text(b, start, end, text):
    raw = text.encode("ascii")[:end - start]
    b[start:end] = raw + b" " * (end - start - len(raw))


def _put_u16(b, at, v):
    b[at:at + 2] = struct.pack(">H", int(round(v)) & 0xFFFF)


def _put_s16(b, at, v):
    b[at:at + 2] = struct.pack(">h", int(round(v)))


def _put_f32(b, at, v):
    b[at:at + 4] = struct.pack(">f", v)


def sfp_a0(vendor="Rei do SFP", part="RSMPP8510GL-03D", rev="2.1",
           serial="FP23H150378", date="230815", wavelength=1310,
           connector=0x07, diag=0x68, tech=0x00, ident=0x03):
    """Page A0h, 256 bytes. diag 0x68: DDM, internally calibrated, RX power
    an average."""
    b = bytearray(256)
    b[0] = ident                   # SFP/SFP+/SFP28
    b[1] = 0x04                    # serial ID defined by two-wire interface
    b[2] = connector
    b[3] = 0x20                    # 10GBASE-LR
    b[8] = tech                    # SFP+ cable technology (DAC bits 2-3)
    _put_text(b, 20, 36, vendor)
    b[37:40] = bytes((0x00, 0x90, 0x65))
    _put_text(b, 40, 56, part)
    _put_text(b, 56, 60, rev)
    _put_u16(b, 60, wavelength)
    _put_text(b, 68, 84, serial)
    _put_text(b, 84, 92, date + "  ")
    b[92] = diag
    b[93] = 0xF0                   # alarm/warning flags implemented
    b[94] = 0x08 if diag & 0x40 else 0x00
    return b


#: The X520 module's levels, in the units SFF-8472 stores (internal
#: calibration): degrees C, volts, mA, mW.
LEVELS = {"temp": 36.5, "volt": 3.3, "bias": 7.5, "tx": 0.6166, "rx": 0.2218}

#: Its thresholds, high alarm / low alarm / high warning / low warning.
THRESHOLDS = {"temp": (75.0, -5.0, 70.0, 0.0),
              "volt": (3.6, 3.0, 3.5, 3.1),
              "bias": (15.0, 1.0, 12.0, 2.0),
              "tx": (1.5849, 0.1202, 1.2589, 0.1514),     # +2/-9.2/+1/-8.2
              "rx": (1.0, 0.0100, 0.7943, 0.0158)}        # 0/-20/-1/-18.01

#: The units each is stored in.
_SCALE = {"temp": 256.0, "volt": 10000.0, "bias": 500.0, "tx": 10000.0,
          "rx": 10000.0}
#: Where each lives in A2h: (thresholds, reading).
_AT = {"temp": (0, 96), "volt": (8, 98), "bias": (16, 100), "tx": (24, 102),
       "rx": (32, 104)}


def sfp_a2_internal(levels=None, th=None):
    """Page A2h, 256 bytes, internally calibrated: what is stored is the
    value in its unit."""
    levels = dict(LEVELS, **(levels or {}))
    th = dict(THRESHOLDS, **(th or {}))
    b = bytearray(256)
    for k, (t_at, v_at) in _AT.items():
        put = _put_s16 if k == "temp" else _put_u16
        for i, t in enumerate(th[k]):
            put(b, t_at + 2 * i, t * _SCALE[k])
        put(b, v_at, levels[k] * _SCALE[k])
    b[110] = 0x00
    return b


#: The external calibration constants the externally calibrated module
#: carries: slope (a plain number; stored x256), offset (in the reading's
#: own units), and the RX polynomial x^0..x^4.
EXT = {"temp": (2.0, 256), "volt": (1.0, 100), "bias": (0.5, 0),
       "tx": (1.0, -100), "rx": (10.0, 1.5, 0.0, 0.0, 0.0)}


def sfp_a2_external():
    """
    Page A2h, externally calibrated: what is stored is the A/D's raw count,
    which the constants turn into the same levels and thresholds as the
    internal module - so the two decode to one screen, and a decoder that
    ignored the calibration reads different numbers. The raw counts are the
    inverse of SFF-8472's formulas, worked here by hand:

      temperature  raw = (value x 256 - 256) / 2      36.5 C -> 4544
      voltage      raw = value x 10000 - 100          3.3 V  -> 32900
      bias         raw = value x 500 / 0.5            7.5 mA -> 7500
      tx power     raw = value x 10000 + 100          0.6166 -> 6266
      rx power     raw = (value x 10000 - 10) / 1.5   0.2218 -> 1472
    """
    b = bytearray(256)

    def raw(k, v):
        if k == "temp":
            return (v * 256 - 256) / 2
        if k == "volt":
            return v * 10000 - 100
        if k == "bias":
            return v * 500 / 0.5
        if k == "tx":
            return v * 10000 + 100
        return (v * 10000 - 10) / 1.5

    for k, (t_at, v_at) in _AT.items():
        put = _put_s16 if k == "temp" else _put_u16
        for i, t in enumerate(THRESHOLDS[k]):
            put(b, t_at + 2 * i, raw(k, t))
        put(b, v_at, raw(k, LEVELS[k]))
    for i, c in enumerate(EXT["rx"]):         # Rx_PWR(4) at 56 ... (0) at 72
        _put_f32(b, 56 + 4 * (4 - i), c)
    _put_u16(b, 76, EXT["bias"][0] * 256)
    _put_s16(b, 78, EXT["bias"][1])
    _put_u16(b, 80, EXT["tx"][0] * 256)
    _put_s16(b, 82, EXT["tx"][1])
    _put_u16(b, 84, EXT["temp"][0] * 256)
    _put_s16(b, 86, EXT["temp"][1])
    _put_u16(b, 88, EXT["volt"][0] * 256)
    _put_s16(b, 90, EXT["volt"][1])
    return b


def sfp_internal(**kw):
    """The X520's module, 512 bytes: A0h then A2h."""
    return bytes(sfp_a0() + sfp_a2_internal(**kw))


def sfp_low_rx():
    """The same module with its receiver at -19.0 dBm (0.0126 mW): past the
    low warning (-18.01), short of the low alarm (-20)."""
    return sfp_internal(levels={"rx": 0.0126})


def sfp_dark():
    """The same module with nothing arriving: 0 mW."""
    return sfp_internal(levels={"rx": 0.0})


def sfp_swapped():
    """The same part with another serial number, SWAP0001, its receiver past
    the low warning as sfp_low_rx()'s: the module in a cage swapped for
    another, which ENTITY-MIB's entLastChangeTime says (#496)."""
    return bytes(sfp_a0(serial="SWAP0001")
                 + sfp_a2_internal(levels={"rx": 0.0126}))


def sfp_external():
    """An externally calibrated module with the same levels, 512 bytes."""
    return bytes(sfp_a0(vendor="FINISAR CORP.", part="FTLX1471D3BCL",
                        rev="A", serial="UQS0ABC", date="190402",
                        diag=0x58) + sfp_a2_external())


def sfp_no_ddm():
    """An SFP that implements no diagnostics: byte 92 is 0. DPDK maps it as
    sff-8079, 256 bytes."""
    return bytes(sfp_a0(vendor="OEM", part="SFP-10G-LR-ND", serial="ND0001",
                        diag=0x00))


def sfp_dac():
    """A passive copper direct-attach cable: no diagnostics, no optics."""
    return bytes(sfp_a0(vendor="Amphenol", part="NDCCGF-0001", serial="AP1",
                        connector=0x21, tech=0x04, wavelength=0x0100,
                        diag=0x00))


def sfp_no_thresholds():
    """A module with DDM whose threshold bytes are all zero - one that
    implements no alarms. Its levels are real; there is nothing to cross."""
    return sfp_internal(th={k: (0, 0, 0, 0) for k in THRESHOLDS})


def qsfp28(rx_dbm=(-3.0, -3.5, -4.0, -12.5), flat=False):
    """
    A QSFP28 100GBASE-LR4, SFF-8636, 640 bytes: lower page, page 00h upper
    at 128, page 03h upper at 512. Lane 4's receiver is low (-12.5 dBm, past
    the -10.0 warning). `flat` is a flat-memory module, which has no page
    03h and so no thresholds.
    """
    b = bytearray(640)
    b[0] = 0x11
    b[2] = 0x04 if flat else 0x00
    _put_s16(b, 22, 41.25 * 256)                  # temperature
    _put_u16(b, 26, 3.29 * 10000)                 # voltage
    for i, d in enumerate(rx_dbm):
        _put_u16(b, 34 + 2 * i, 10 ** (d / 10) * 10000)       # rx power
        _put_u16(b, 42 + 2 * i, (40.0 + i) * 500)             # bias, mA
        _put_u16(b, 50 + 2 * i, 10 ** ((0.5 - i * 0.1) / 10) * 10000)
    b[128] = 0x11
    b[130] = 0x07
    b[147] = 0x40                                 # 1310 nm DFB
    _put_text(b, 148, 164, "Rei do SFP")
    b[165:168] = bytes((0x00, 0x90, 0x65))
    _put_text(b, 168, 184, "QSFP28-LR4-10")
    _put_text(b, 184, 186, "A0")
    _put_u16(b, 186, 1310 * 20)
    _put_text(b, 196, 212, "Q28LR4X0001")
    _put_text(b, 212, 220, "240301  ")
    b[220] = 0x0C                                 # rx average, tx measured
    if not flat:
        p3 = 512
        for i, t in enumerate((75.0, -5.0, 70.0, 0.0)):
            _put_s16(b, p3 + 2 * i, t * 256)
        for i, t in enumerate((3.6, 3.0, 3.5, 3.1)):
            _put_u16(b, p3 + 16 + 2 * i, t * 10000)
        for i, d in enumerate((5.5, -13.6, 4.5, -10.0)):      # rx, dBm
            _put_u16(b, p3 + 48 + 2 * i, 10 ** (d / 10) * 10000)
        for i, t in enumerate((75.0, 10.0, 70.0, 15.0)):      # bias, mA
            _put_u16(b, p3 + 56 + 2 * i, t * 500)
        for i, d in enumerate((6.0, -10.0, 5.0, -8.0)):       # tx, dBm
            _put_u16(b, p3 + 64 + 2 * i, 10 ** (d / 10) * 10000)
    return bytes(b if not flat else b[:256])


def dump(name, source, type_name, data, ranges=None, extra=""):
    """
    `data` as the plugin's `show bpa transceiver` prints it: the head line,
    then 16 bytes a row, `--` for a byte outside `ranges` (a list of
    (offset, length); None is every byte). Written here by hand rather than
    with the module under test, so parse_dump() is measured against the
    plugin's format and not against itself.
    """
    valid = [ranges is None] * len(data)
    for off, n in ranges or []:
        for i in range(off, off + n):
            valid[i] = True
    lines = [f"interface {name} source {source} type {type_name} "
             f"length {len(data)}" + (f" {extra}" if extra else "")]
    for row in range(0, len(data), 16):
        cells = list(range(row, min(row + 16, len(data))))
        if not any(valid[i] for i in cells):
            continue
        lines.append("  %04x " % row + " ".join(
            "%02x" % data[i] if valid[i] else "--" for i in cells))
    return "\n".join(lines) + "\n"


#: The ranges the plugin asks DPDK for on its first (static) read of an
#: SFF-8472 module - bpa_xcvr_ranges(), as test/test_bpa_xcvr.c holds it.
SFF8472_READ = [(0, 96), (256, 96), (352, 24)]


#: `show bpa transceiver` on 10.40.200.166, read (and nothing else) on
#: 2026-10-11 for #496's phase 5: the X520's port with its module - the Rei
#: do SFP above, which is an 850 nm part, and its own thresholds, not the
#: typical ones THRESHOLDS models - while its fibre was down, so its receiver
#: read 0.0001 mW (-40.00 dBm, a low alarm); and the VM's two virtio cards,
#: whose driver has no module operation and answers -95.
X520_166 = """\
interface enp1s0 source dpdk type sff-8472 length 512 port 0 device 0000:01:00.0 age 1.2
  0000 03 04 07 08 00 00 00 00 00 06 00 03 67 00 00 00
  0010 1e 1e 00 1e 52 65 69 20 64 6f 20 53 46 50 20 20
  0020 20 20 20 20 00 00 00 00 52 53 4d 50 50 38 35 31
  0030 30 47 4c 2d 30 33 44 20 32 2e 31 20 03 52 00 a9
  0040 00 1a 00 00 46 50 32 33 48 31 35 30 33 37 38 20
  0050 20 20 20 20 32 33 30 38 31 35 20 20 68 f0 03 03
  0100 5a 00 d3 00 50 00 d8 00 8c a0 75 30 88 b8 79 18
  0110 1d 4c 00 00 17 70 01 f4 31 2d 04 eb 27 10 06 31
  0120 31 2d 01 3c 27 10 01 f5 ff ff ff ff ff ff ff ff
  0130 ff ff ff ff ff ff ff ff 00 00 00 00 00 00 00 00
  0140 00 00 00 00 3f 80 00 00 00 00 00 00 01 00 00 00
  0150 01 00 00 00 01 00 00 00 01 00 00 00 00 00 00 12
  0160 21 c3 7e 63 0c cc 18 a5 00 01 00 00 00 00 02 00
  0170 00 40 00 00 00 40 00 00 -- -- -- -- -- -- -- --
interface enp6s18 unread no-module the card answered -95 (Operation not supported) on DPDK port 1
interface enp6s19 unread no-module the card answered -95 (Operation not supported) on DPDK port 2
interface tap4096 unread not-dpdk not a DPDK port
interface tap4097 unread not-dpdk not a DPDK port
interface tap4098 unread not-dpdk not a DPDK port
"""
