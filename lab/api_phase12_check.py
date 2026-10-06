"""
lab/api-grpc.sh's client for #590: ListTransceivers. The lab's pg0 is given
a module's EEPROM through the plugin's stand-in (the SFP of test/
transceiver_fixtures.py with its receiver at -19 dBm, past its -18.01 low
warning), and the API reads it as the VTY's `show interface pg0 transceiver`
does - the same decode, the same reading, its text the screen's.

Arguments: <stubs dir> <certs dir> <host:port> <the VTY's screen as
netview>. One RESULT line per check.
"""
import sys

sys.path.insert(0, sys.argv[1])
import grpc  # noqa: E402
from prisma.v1 import prisma_pb2 as pb, prisma_pb2_grpc as rpc  # noqa: E402

CERTS, TARGET = sys.argv[2], sys.argv[3]


def channel(who):
    ca = open(f"{CERTS}/ca.crt", "rb").read()
    creds = grpc.ssl_channel_credentials(ca, open(f"{CERTS}/{who}.key", "rb").read(),
                                         open(f"{CERTS}/{who}.crt", "rb").read())
    return grpc.secure_channel(TARGET, creds,
                               options=(("grpc.ssl_target_name_override", "prisma-api"),))


def result(name, ok, detail=""):
    print(f"RESULT {name} {'ok' if ok else 'FAIL'} {detail}".rstrip(), flush=True)


def run(name, fn):
    try:
        fn()
    except grpc.RpcError as e:
        result(name, False, f"{e.code().name} {e.details()[:200]}")
    except Exception as e:  # noqa: BLE001 - every check reports, none aborts the rest
        result(name, False, f"{type(e).__name__}: {e}")


op = rpc.OperationalServiceStub(channel("root"))
op_ro = rpc.OperationalServiceStub(channel("netview"))


def one():
    r = op.ListTransceivers(pb.ListTransceiversRequest(names=["pg0"]), timeout=60)
    t = r.transceivers[0] if r.transceivers else pb.Transceiver()
    result("xcvr-one", len(r.transceivers) == 1 and t.name == "pg0"
           and t.source == "stand-in" and t.map == "sff-8472"
           and t.status == pb.Transceiver.STATUS_DDM and r.dataplane_answered,
           f"{len(r.transceivers)} {t.name} {t.source} {t.map} {t.status}")
    result("xcvr-identity", t.vendor == "Rei do SFP"
           and t.part_number == "RSMPP8510GL-03D" and t.serial_number == "FP23H150378"
           and t.date_code == "2023-08-15" and t.wavelength_nm == 1310
           and t.calibration == "internal" and t.rx_power_average,
           f"{t.vendor} {t.part_number} {t.serial_number} {t.date_code} {t.wavelength_nm}")
    ln = t.lanes[0] if t.lanes else pb.TransceiverLane()
    rx = ln.rx_power_mw
    result("xcvr-rx-low", len(t.lanes) == 1 and rx.known
           and abs(rx.value - 0.0126) < 1e-4 and abs(rx.dbm + 19.0) < 0.01
           and rx.level == pb.TransceiverLevel.LEVEL_LOW_WARNING
           and rx.thresholds_known and abs(rx.low_warning_dbm + 18.01) < 0.01
           and not rx.no_light,
           f"{rx.value} mW {rx.dbm} dBm level {rx.level} lw {rx.low_warning_dbm}")
    result("xcvr-levels", abs(t.temperature_c.value - 36.5) < 1e-3
           and t.temperature_c.level == pb.TransceiverLevel.LEVEL_NORMAL
           and abs(t.temperature_c.low_alarm + 5.0) < 1e-3
           and abs(t.voltage_v.value - 3.3) < 1e-3
           and abs(ln.bias_ma.value - 7.5) < 1e-3
           and abs(ln.tx_power_mw.dbm + 2.10) < 0.01,
           f"{t.temperature_c.value} C {t.voltage_v.value} V {ln.bias_ma.value} mA "
           f"{ln.tx_power_mw.dbm} dBm")
    s = op.Show(pb.ShowRequest(command="show interface pg0 transceiver"), timeout=60)
    result("xcvr-text-is-vty", bool(r.text) and r.text.strip() == s.text.strip()
           and "low warn" in r.text, f"{r.text[:100]!r} / {s.text[:100]!r}")


def every():
    r = op.ListTransceivers(pb.ListTransceiversRequest(), timeout=60)
    names = [t.name for t in r.transceivers]
    result("xcvr-all", "pg0" in names and not any(n.startswith("tap") for n in names),
           f"{names}")


def missing():
    r = op.ListTransceivers(pb.ListTransceiversRequest(names=["nosuch9"]), timeout=60)
    result("xcvr-missing", list(r.missing) == ["nosuch9"] and not r.transceivers,
           f"missing {list(r.missing)}, {len(r.transceivers)} read")


def any_account():
    # An account with no role gets what the VTY gives it (phase 6's rule):
    # netview is in no group here, so its VTY cannot open the dataplane's
    # socket and says so - and the API, through netview's own worker, says
    # the same. On a box every account is in vpp (REACH_GROUPS) and reads it.
    vty = open(sys.argv[4]).read()
    r = op_ro.ListTransceivers(pb.ListTransceiversRequest(names=["pg0"]), timeout=60)
    result("xcvr-any-account", r.text.strip() == vty.strip() and (
        (r.dataplane_answered and len(r.transceivers) == 1)
        or (not r.dataplane_answered and "not answering" in vty)),
        f"answered {r.dataplane_answered}, {len(r.transceivers)} read; "
        f"VTY: {vty.strip()[:80]!r}")


for name, fn in (("xcvr-one", one), ("xcvr-all", every),
                 ("xcvr-missing", missing), ("xcvr-any-account", any_account)):
    run(name, fn)
