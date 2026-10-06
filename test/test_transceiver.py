#!/usr/bin/env python3
"""
A module's EEPROM, decoded (#590): src/prismash/transceiver.py driven with
maps built byte by byte from SFF-8472 and SFF-8636 (test/transceiver_
fixtures.py), not from the decoder's own view of them.

WHAT GOES WRONG QUIETLY, and is held here. A decode that ignores external
calibration prints a raw A/D count as a temperature, and the screen still
looks like a module. A threshold read from the wrong offset is a level that
is "ok" when it is not. A dBm computed from the wrong unit is off by a
power of ten and still plausible. 0 mW printed as a number is a level, which
no light is not. And a module with no thresholds, read as having thresholds
of 0, is every reading in alarm. Each of those is a check below, and each
was seen red against its mutation before being kept (the mutations are
logged in the #590 branch's report).
"""
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src", "prismash"))
sys.path.insert(0, HERE)
import transceiver as T  # noqa: E402
import transceiver_fixtures as F  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def near(a, b, tol=1e-3):
    return a is not None and b is not None and abs(a - b) <= tol


def read(name, data, typ, ranges=None, source="dpdk", extra=""):
    """A fixture through the plugin's format and back, decoded."""
    return T.decode(T.parse_dump(F.dump(name, source, typ, data, ranges,
                                        extra))[name])


print("=== the dump: the plugin's lines read back ===")
txt = F.dump("enp1s0", "dpdk", "sff-8472", F.sfp_internal(), F.SFF8472_READ,
             "port 0 device 0000:01:00.0 age 0.4")
r = T.parse_dump(txt)["enp1s0"]
check(r["source"] == "dpdk" and r["type"] == "sff-8472" and r["length"] == 512,
      f"head line: {r['source']} {r['type']} {r['length']}")
check(r["port"] == 0 and r["device"] == "0000:01:00.0" and r["age"] == 0.4,
      f"port, device, age: {r['port']} {r['device']} {r['age']}")
check(r["bytes"][0] == 0x03 and r["bytes"][92] == 0x68,
      "A0h's identifier and diagnostic type read")
check(r["bytes"][96] is None and r["bytes"][255] is None,
      "a byte the plugin printed as -- is unread, not 0")
check(r["bytes"][352] is not None and r["bytes"][376] is None,
      "the readings read; past them, unread")
both = T.parse_dump("interface pg0 unread not-dpdk not a DPDK port\r\n"
                    "interface e0 unread no-module the card answered -5 "
                    "(Input/output error) on DPDK port 0\n"
                    "garbage line\n  0000 zz\n")
check(list(both) == ["pg0", "e0"], f"two unread interfaces: {list(both)}")
check(both["pg0"]["unread"] == "not-dpdk" and both["pg0"]["bytes"] == [],
      "an unread port has a reason and no bytes")
check("-5" in both["e0"]["why"], "and its sentence")
check(T.parse_dump("") == {} and T.parse_dump(None) == {}, "nothing is nothing")
check(T._digits("12") and not T._digits("\u00b2") and not T._digits("")
      and not T._digits("-1"), "a port number is ASCII digits")
print("  [ ok ] head, bytes, -- as unread, the unread lines")

print("\n=== dump_lines writes what parse_dump reads ===")
data = list(F.sfp_internal())
valid = [False] * 512
for off, n in F.SFF8472_READ:
    valid[off:off + n] = [True] * n
lines = T.dump_lines("eth9", "linux", "sff-8472", data, valid)
back = T.parse_dump("\n".join(lines))["eth9"]
check(back["source"] == "linux" and back["bytes"][:96] == data[:96]
      and back["bytes"][100] is None and back["bytes"][352:376] == data[352:376],
      "the Linux reader's format round-trips through parse_dump")
check(all(l.startswith("  ") for l in lines[1:]) and len(lines) == 1 + 6 + 6 + 2,
      f"only the rows that hold a byte read: {len(lines)} lines")
print("  [ ok ] the same format both ways")

print("\n=== the numbers ===")
check(near(T.mw_to_dbm(1.0), 0.0) and near(T.mw_to_dbm(0.1), -10.0)
      and near(T.mw_to_dbm(0.2218), -6.54, 0.01),
      "dBm is 10 log10(mW): 1 mW 0, 0.1 mW -10, 0.2218 mW -6.54")
check(T.mw_to_dbm(0.0) == float("-inf"), "0 mW is -inf, not a level")
check(T.mw_to_dbm(None) is None, "no reading is no dBm")
check(T.fmt_dbm(float("-inf")) == "-inf" and T.fmt_dbm(None) == "-"
      and T.fmt_dbm(-6.5432) == "-6.54", "dBm as the screen prints it")
th = T.thresholds([1.0, 0.01, 0.7943, 0.0158])
check(th == {"high_alarm": 1.0, "low_alarm": 0.01, "high_warning": 0.7943,
             "low_warning": 0.0158}, f"the four, in the module's order: {th}")
check(T.level_of(0.5, th) == "normal", "inside every threshold")
check(T.level_of(0.8, th) == "high-warning", "past the high warning")
check(T.level_of(1.5, th) == "high-alarm", "past the high alarm, which is "
      "past the warning too: the alarm is said")
check(T.level_of(0.012, th) == "low-warning", "below the low warning")
check(T.level_of(0.0, th) == "low-alarm", "no light is below the low alarm")
check(T.level_of(0.7943, th) == "normal" and T.level_of(0.0158, th) == "normal",
      "AT a threshold is not past it (SFF-8472 sets its flags beyond)")
check(T.level_of(1.0, th) == "high-warning" and T.level_of(0.01, th) == "low-warning",
      "AT an alarm is past the warning and not the alarm")
check(T.level_of(None, th) == "" and T.level_of(0.5, None) == "",
      "no value, or no thresholds: nothing to say")
check(T.thresholds([0, 0, 0, 0]) is None,
      "all four zero is a module with no thresholds, not thresholds of 0")
check(T.thresholds([0.01, 1.0, 0.7, 0.02]) is None,
      "a high alarm below its low alarm is no threshold set")
check(T.thresholds([1.0, 0.01, 0.01, 0.7]) is None,
      "nor a high warning below its low warning")
check(T.thresholds([1.0, None, 0.7, 0.02]) is None, "nor one unread")
print("  [ ok ] dBm, -inf, the four levels, the edges, no thresholds")

print("\n=== the byte readers ===")
b = [0x80, 0x00, 0x7F, 0xFF, None, 0x41, 0x42, 0x20, 0x20]
check(T._u16(b, 0) == 0x8000 and T._s16(b, 0) == -32768
      and T._s16(b, 2) == 32767, "u16 and two's complement s16, big endian")
check(T._u16(b, 3) is None and T._s16(b, 4) is None and T._u16(b, 8) is None,
      "a pair with a byte unread, or past the end, is no value")
check(T._f32(list(struct.pack(">f", 1.5)), 0) == 1.5
      and T._f32([0, 0, None, 0], 0) is None, "an IEEE single, or None")
check(T._text(b, 5, 9) == "AB" and T._text(b, 4, 6) == "",
      "ASCII with its padding stripped; one unread byte is no text")
check(T._date(list(b"230815  "), 0) == "2023-08-15"
      and T._date(list(b"ABCDEFGH"), 0) == "ABCDEFGH",
      "a date code as a date, and what is not one as it is")
check(T._oui([0x00, 0x90, 0x65], 0) == "00:90:65" and T._oui([0, 0, 0], 0) == "",
      "an OUI, and none for zero")
check(T._byte([1, 2], 1) == 2 and T._byte([1, 2], 5) is None, "a byte")
check(T._hexname(0x99) == "0x99" and T._hexname(None) == "",
      "a code no table names, as its number")
print("  [ ok ] u16 s16 f32 text date oui")

print("\n=== SFF-8472, internally calibrated: the X520's module ===")
m = read("enp1s0", F.sfp_internal(), "sff-8472", F.SFF8472_READ)
check(m["status"] == "ddm" and m["calibration"] == "internal",
      f"a module with diagnostics, internal: {m['status']} {m['calibration']}")
check(m["vendor"] == "Rei do SFP" and m["part"] == "RSMPP8510GL-03D"
      and m["revision"] == "2.1" and m["serial"] == "FP23H150378"
      and m["date"] == "2023-08-15",
      f"the identity measured on 10.40.200.166: {m['vendor']} {m['part']} "
      f"{m['revision']} {m['serial']} {m['date']}")
check(m["identifier"] == "SFP/SFP+/SFP28" and m["connector"] == "LC"
      and m["wavelength_nm"] == 1310 and m["oui"] == "00:90:65",
      f"identifier, connector, wavelength: {m['identifier']} "
      f"{m['connector']} {m['wavelength_nm']}")
ln = m["lanes"][0]
check(near(m["temperature"]["value"], 36.5) and near(m["voltage"]["value"], 3.3)
      and near(ln["bias"]["value"], 7.5) and near(ln["tx_power"]["value"], 0.6166)
      and near(ln["rx_power"]["value"], 0.2218),
      f"the levels: {m['temperature']['value']} C {m['voltage']['value']} V "
      f"{ln['bias']['value']} mA {ln['tx_power']['value']} "
      f"{ln['rx_power']['value']} mW")
check(near(T.mw_to_dbm(ln["rx_power"]["value"]), -6.54, 0.01)
      and near(T.mw_to_dbm(ln["tx_power"]["value"]), -2.10, 0.01),
      "RX -6.54 dBm and TX -2.10 dBm")
check(near(m["temperature"]["thresholds"]["high_alarm"], 75.0)
      and near(m["temperature"]["thresholds"]["low_alarm"], -5.0),
      "a negative threshold (-5 C) is read signed")
check(near(ln["rx_power"]["thresholds"]["low_warning"], 0.0158)
      and near(ln["bias"]["thresholds"]["high_alarm"], 15.0)
      and near(m["voltage"]["thresholds"]["low_warning"], 3.1),
      "each quantity's thresholds from its own offset")
check(all(q["level"] == "normal" for q in (m["temperature"], m["voltage"],
                                          ln["bias"], ln["tx_power"],
                                          ln["rx_power"])),
      "every level normal on a healthy module")
check(m["rx_power_kind"] == "average", "byte 92 bit 3: RX power is an average")
print("  [ ok ] identity, five levels, their thresholds, all normal")

print("\n=== SFF-8472, externally calibrated: the constants applied ===")
e = read("ext0", F.sfp_external(), "sff-8472")
el = e["lanes"][0]
check(e["calibration"] == "external", "byte 92 bit 4: external")
check(near(e["temperature"]["value"], 36.5),
      f"T = 2.0 x 4544 + 256, /256: 36.5 C, got {e['temperature']['value']}")
check(near(e["voltage"]["value"], 3.3) and near(el["bias"]["value"], 7.5)
      and near(el["tx_power"]["value"], 0.6166),
      f"voltage, bias, TX through slope and offset: {e['voltage']['value']} "
      f"{el['bias']['value']} {el['tx_power']['value']}")
check(near(el["rx_power"]["value"], 0.2218),
      f"RX through the polynomial (10 + 1.5 x 1472): 0.2218 mW, got "
      f"{el['rx_power']['value']}")
check(near(e["temperature"]["thresholds"]["low_alarm"], -5.0)
      and near(el["rx_power"]["thresholds"]["high_alarm"], 1.0, 1e-3),
      "the thresholds calibrated with the same constants")
raw_t = F.sfp_external()[256 + 96] << 8 | F.sfp_external()[256 + 97]
check(raw_t == 4544, "and the stored count really is the raw A/D, 4544")
check(T.sff8472_calibrate(4544, 512, 256, signed=True) == 9344,
      "formula 1, with the slope as 8.8 fixed point")
check(T.sff8472_calibrate(0xFE00, 256, 0, signed=True) == -512,
      "a negative raw temperature is signed")
check(T.sff8472_calibrate(None, 256, 0) is None, "no reading, none")
check(T.sff8472_rx_calibrate(1472, [10.0, 1.5, 0.0, 0.0, 0.0]) == 2218.0,
      "formula 2, x^0 first")
check(T.sff8472_rx_calibrate(10, [-100.0, 1.0, 0, 0, 0]) == 0.0,
      "a polynomial below zero is clamped to 0, not a negative power")
check(T.sff8472_rx_calibrate(10, [1.0, None, 0, 0, 0]) is None,
      "a coefficient unread, none")
print("  [ ok ] the same levels as the internal module, through the constants")

print("\n=== RX power past its warning, and no light ===")
lo = read("enp1s0", F.sfp_low_rx(), "sff-8472")
check(lo["lanes"][0]["rx_power"]["level"] == "low-warning",
      f"-19 dBm is past the low warning (-18.01): "
      f"{lo['lanes'][0]['rx_power']['level']}")
notes = T.rx_power_notes(lo)
check(len(notes) == 2 and "rx power -19.00 dBm, below its low warning "
      "(-18.01 dBm)" in notes[0] and "transceiver" in notes[1],
      f"the note says the level and the threshold: {notes}")
dark = read("enp1s0", F.sfp_dark(), "sff-8472")
check(dark["lanes"][0]["rx_power"]["level"] == "low-alarm", "0 mW: low alarm")
dn = " ".join(T.rx_power_notes(dark))
check("no light" in dn and "and its alarm" in dn and "-inf" not in dn,
      f"and the note says no light, not a number: {dn}")
check(T.rx_power_notes(m) == [], "a healthy module has no note")
check(T.rx_power_notes({"status": "no-ddm"}) == [], "nor one without DDM")
print("  [ ok ] low warning, no light, silence when healthy")

print("\n=== a module without thresholds ===")
nt = read("enp1s0", F.sfp_no_thresholds(), "sff-8472")
check(nt["status"] == "ddm" and near(nt["temperature"]["value"], 36.5),
      "its levels are still read")
check(nt["temperature"]["thresholds"] is None
      and nt["lanes"][0]["rx_power"]["level"] == "",
      "and nothing is in alarm: there is nothing to cross")
check(T.rx_power_notes(nt) == [], "so no note")
print("  [ ok ] levels, no thresholds, no alarm")

print("\n=== a module without DDM, a copper cable, a hidden A2h ===")
nd = read("e1", F.sfp_no_ddm(), "sff-8079")
check(nd["status"] == "no-ddm" and "does not implement" in nd["why"]
      and nd["vendor"] == "OEM", f"no DDM, identity kept: {nd['status']}")
dac = read("e2", F.sfp_dac(), "sff-8079")
check(dac["status"] == "copper" and dac["wavelength_nm"] is None
      and dac["connector"] == "copper pigtail",
      f"a DAC: copper, no wavelength: {dac['status']} {dac['wavelength_nm']}")
check(T._copper_sfp(list(F.sfp_dac())) and not T._copper_sfp(list(F.sfp_internal())),
      "copper by the cable bits or the connector")
hid = read("e3", F.sfp_internal()[:256], "sff-8079")
check(hid["status"] == "no-ddm" and "A2h page was not read" in hid["why"],
      f"diagnostics declared, page not mapped (ixgbe's address change): "
      f"{hid['why']}")
a0 = bytearray(F.sfp_internal()[:256])
a0[92] |= 0x04
hid2 = T.decode_sff8472(list(a0) + [None] * 256, True)
check("address change" in hid2["why"], "and the address-change bit is named")
idn = T._identity_sfp(list(F.sfp_internal()))
check(idn["vendor"] == "Rei do SFP" and idn["wavelength_nm"] == 1310,
      "the identity alone")
print("  [ ok ] no-ddm, copper, A2h unread")

print("\n=== absent, blank, unreadable ===")
check(T.decode(both["e0"])["status"] == "absent", "-EIO: an empty cage")
ns = T.decode({"name": "Ethernet0", "unread": "no-module",
               "why": "the card answered -95 (Operation not supported) on "
                      "DPDK port 0"})
check(ns["status"] == "unreadable" and "driver" in ns["why"],
      f"-ENOTSUP: a driver with no module access: {ns['why']}")
check(T.decode(both["pg0"])["status"] == "unreadable", "not a DPDK port")
check(T.decode({"name": "x", "unread": "no-device", "why": ""})["status"]
      == "absent", "a card the host does not have")
blank = read("e4", bytes([0xFF] * 256), "sff-8079")
check(blank["status"] == "absent" and "blank" in blank["why"],
      "an EEPROM of 0xFF is no module")
check(read("e5", bytes(256), "sff-8079")["status"] == "absent",
      "nor one of zeros")
un = T.decode({"name": "e6", "type": "sff-9999", "bytes": [3] * 256})
check(un["status"] == "unreadable" and "sff-9999" in un["why"],
      "a map this box does not decode says which")
print("  [ ok ] absent, unreadable, blank")

print("\n=== SFF-8636: a QSFP28's four lanes ===")
q = read("hu0", F.qsfp28(), "sff-8636")
check(q["status"] == "ddm" and len(q["lanes"]) == 4,
      f"four lanes: {len(q['lanes'])}")
check(q["identifier"] == "QSFP28" and q["vendor"] == "Rei do SFP"
      and q["part"] == "QSFP28-LR4-10" and q["serial"] == "Q28LR4X0001"
      and q["wavelength_nm"] == 1310 and q["date"] == "2024-03-01",
      f"page 00h's identity: {q['identifier']} {q['part']} "
      f"{q['wavelength_nm']}")
check(near(q["temperature"]["value"], 41.25) and near(q["voltage"]["value"], 3.29),
      "the lower page's temperature and voltage")
rx = [T.mw_to_dbm(l["rx_power"]["value"]) for l in q["lanes"]]
check(all(near(a, b, 0.01) for a, b in zip(rx, (-3.0, -3.5, -4.0, -12.5))),
      f"each lane's RX power: {rx}")
check(near(q["lanes"][3]["bias"]["value"], 43.0), "lane 4's bias")
check(q["lanes"][3]["rx_power"]["level"] == "low-warning"
      and q["lanes"][0]["rx_power"]["level"] == "normal",
      "page 03h's thresholds: lane 4 past its low warning, lane 1 not")
qn = T.rx_power_notes(q)
check(len(qn) == 2 and "lane 4" in qn[0], f"the note names the lane: {qn}")
qf = read("hu1", F.qsfp28(flat=True), "sff-8636")
check(qf["status"] == "ddm" and qf["temperature"]["thresholds"] is None
      and qf["lanes"][3]["rx_power"]["level"] == "",
      "a flat-memory module has no page 03h: levels, no thresholds")
qd = T.decode_sff8636(list(F.qsfp28()))
check(qd["status"] == "ddm", "decode_sff8636 on its own")
c = bytearray(F.qsfp28())
c[147] = 0xA0
check(T.decode_sff8636(list(c))["status"] == "copper",
      "device technology 0xA0 and above is copper")
c = bytearray(F.qsfp28())
c[220] = 0x08
check(T.decode_sff8636(list(c))["lanes"][0]["tx_power"]["value"] is None,
      "byte 220 bit 2 clear: TX power not measured, so none")
print("  [ ok ] four lanes, page 03h, flat memory, copper")

print("\n=== the screen ===")
sc = T.screen(m)
check(sc[0] == "  enp1s0: SFP/SFP+/SFP28, LC connector, 1310 nm",
      f"the first line: {sc[0]!r}")
check(any(l.startswith("    rx power (dBm)   -6.54") for l in sc)
      and any(l.startswith("    rx power (mW)   0.2218") for l in sc),
      "RX power in dBm and in mW")
row = next(l for l in sc if l.startswith("    rx power (dBm)"))
check(row.split()[4:] == ["0.00", "-1.00", "-18.01", "-20.00", "ok"],
      f"its thresholds high to low, then its state: {row.split()[4:]}")
check(all(len(l) <= 79 for s_ in (sc, T.screen(q), T.screen(dark),
                                  T.screen(dac), T.screen(hid))
          for l in s_), "every line fits 79 columns")
ds = T.screen(dark)
check(any("-inf" in l and l.rstrip().endswith("no light") for l in ds),
      "no light on the dBm row")
check(any(l.startswith("    ddm         none: a copper module") for l in T.screen(dac)),
      "a copper module says so")
check(any("does not implement diagnostics" in l for l in T.screen(nd)),
      "a module without DDM says so")
ab = T.screen(T.decode(both["e0"]))
check(len(ab) == 2 and ab[1].startswith("    no module answered"),
      f"an empty cage is two lines: {ab}")
check(any(l.startswith("    rx4 (dBm)") and "low warn" in l for l in T.screen(q)),
      "a QSFP's rows are per lane")
check(any("a stand-in" in l for l in T.screen(read("x", F.sfp_internal(),
                                                   "sff-8472", source="stand-in"))),
      "a stand-in's bytes say they are not a module")
check(T._row("bias (mA)", {"value": 7.5, "thresholds": None, "level": ""}, 3)
      .split() == ["bias", "(mA)", "7.500", "-", "-", "-", "-", "-"],
      "a level with no thresholds")
check(T._cell(None, 2) == "-" and T._cell(1.234, 2) == "1.23", "a cell")
check(T.level_word("normal") == "ok" and T.level_word("high-warning")
      == "high warn" and T.level_word("") == "-", "the state words")
check(T.connector_words("LC") == "LC connector"
      and T.connector_words("copper pigtail") == "copper pigtail"
      and T.connector_words("") == "", "the connector's words")
w = T.wrap("word " * 40, "  ", "    ")
check(len(w) > 1 and all(len(l) <= 79 for l in w) and w[1].startswith("    "),
      "a long sentence folds at 79, its continuation indented")
check(T.wrap("", "  x", "") == ["  x"], "an empty sentence is its indent")
print("  [ ok ] the table, its thresholds, the states, 79 columns")

print("\n=== CRC: a note above 0.01% of what arrived ===")
cn = T.crc_note("enp1s0", 10_400_000, 60_674)
check(len(cn) >= 2 and "0.58% of received frames failed their CRC" in cn[0],
      f"10.40.200.166's port: {cn}")
check(all(len(l) <= 79 for l in cn), "and fits")
check(T.crc_note("e", 10_000_000, 999) == [],
      "999 in 10 M is 0.00999%: under the threshold, no note")
check(T.crc_note("e", 10_000_000, 1_002) != [], "1,002 in 10 M is over it")
check(T.crc_note("e", 1_000, 9) == [],
      "nine errors are a link coming up, whatever the ratio")
check(T.crc_note("e", 0, 0) == [] and T.crc_note("e", None, 50) == [],
      "nothing received, or no counter, no note")
hw = ("              Name                Idx   Link  Hardware\n"
      "enp1s0                             1     up   enp1s0\n"
      "  Link speed: 10 Gbps\n"
      "  extended stats:\n"
      "    rx_good_packets                          10400000\n"
      "    rx_crc_errors                               60674\n"
      "    rx_good_bytes                         12345678901\n"
      "mlx0                               2     up   mlx0\n"
      "  extended stats:\n"
      "    rx_good_packets                               100\n"
      "    rx_crc_errors_phy                               7\n"
      "virt0                              3     up   virt0\n"
      "  extended stats:\n"
      "    rx_good_packets                               100\n")
cc = T.crc_counts(hw)
check(cc == {"enp1s0": (10_400_000, 60_674), "mlx0": (100, 7)},
      f"ixgbe's and mlx5's counters, and none for a card without one: {cc}")
check(T.crc_counts("") == {} and T.crc_counts(None) == {}, "nothing")
print("  [ ok ] 0.58% noted, 0.00999% not, a handful not")

print("\n=== the API's fields ===")
f = T.transceiver_fields(m)
check(f["status"] == "STATUS_DDM" and f["part_number"] == "RSMPP8510GL-03D"
      and f["serial_number"] == "FP23H150378" and f["date_code"] == "2023-08-15"
      and f["wavelength_nm"] == 1310 and f["rx_power_average"],
      "status and identity")
rxf = f["lanes"][0]["rx_power_mw"]
check(rxf["known"] and near(rxf["value"], 0.2218) and near(rxf["dbm"], -6.54, 0.01)
      and rxf["level"] == "LEVEL_NORMAL" and rxf["thresholds_known"]
      and near(rxf["low_warning_dbm"], -18.01, 0.01) and not rxf["no_light"],
      f"RX power in mW and dBm with its thresholds: {rxf}")
df = T.transceiver_fields(dark)["lanes"][0]["rx_power_mw"]
check(df["no_light"] and df["dbm"] == 0.0 and df["level"] == "LEVEL_LOW_ALARM",
      f"no light is said, not -inf: {df}")
check(T.transceiver_fields(nd)["status"] == "STATUS_NO_DDM"
      and T.transceiver_fields(nd)["lanes"] == [], "no DDM, no lanes")
check(T.transceiver_fields(nt)["temperature_c"]["level"] == "LEVEL_UNKNOWN"
      and not T.transceiver_fields(nt)["temperature_c"]["thresholds_known"],
      "no thresholds: unknown, and said")
qf_ = T._quantity_fields({"value": None, "thresholds": None, "level": ""}, True)
check(not qf_["known"] and qf_["value"] == 0.0 and qf_["dbm"] == 0.0,
      "a quantity the module did not give")
print("  [ ok ] identity, levels, dBm, no light")

print("\n=== reading a card Linux owns ===")
check(T.read_argv("eth0", "/p/t.py") == ("/usr/bin/python3", "/p/t.py",
                                         "read", "eth0"),
      "the argv the door accepts")
check(T.module_ranges("sff-8472", 512) == F.SFF8472_READ,
      "the same ranges the plugin reads first")
check(T.module_ranges("sff-8636", 640) == [(128, 128), (512, 72), (0, 58)]
      and T.module_ranges("sff-8636", 256) == [(128, 128), (0, 58)]
      and T.module_ranges("sff-8079", 256) == [(0, 96)]
      and T.module_ranges("sff-8472", 256) == [], "and a QSFP's, an 8079's")
calls = []


def fake_ioctl(dev, req):
    calls.append(struct.unpack_from("=IIII", req))
    cmd = struct.unpack_from("=I", req)[0]
    if cmd == T.ETHTOOL_GMODULEINFO:
        return struct.pack("=III32x", cmd, 2, 512)
    _, _, off, n = struct.unpack_from("=IIII", req)
    return req[:16] + F.sfp_internal()[off:off + n]


got = T.parse_dump("\n".join(T.linux_read("eno1", fake_ioctl)))["eno1"]
check(got["source"] == "linux" and got["type"] == "sff-8472"
      and got["bytes"][:96] == list(F.sfp_internal()[:96]),
      "the module info, then each range, into the dump")
check([c_[2:] for c_ in calls[1:]] == [(0, 96), (256, 96), (352, 24)],
      f"the ranges asked of the kernel: {calls[1:]}")
check(T.decode(got)["lanes"][0]["rx_power"]["level"] == "normal",
      "and it decodes like the dataplane's")


def enodev(dev, req):
    raise OSError(19, "No such device")


def eopnotsupp(dev, req):
    raise OSError(95, "Operation not supported")


check(T.parse_dump("\n".join(T.linux_read("nope0", enodev)))["nope0"]["unread"]
      == "no-device", "a card the host does not have")
v = T.parse_dump("\n".join(T.linux_read("veth0", eopnotsupp)))["veth0"]
check(v["unread"] == "no-module" and T.decode(v)["status"] == "unreadable",
      "a veth: the driver has no module access")
check(T.linux_read("-rf", fake_ioctl)[0].startswith("interface -rf unread"),
      "a name that is an option is not asked")
print("  [ ok ] ioctl, ranges, errors")

print()
print("ALL TESTS PASSED" if fail == 0 else f"{fail} FAILURES")
sys.exit(1 if fail else 0)
