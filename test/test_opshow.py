#!/usr/bin/env python3
"""
The rest of the VTY's screens as the API's typed reads (#558, phase 7):
`show logging` and the probes' lines read into fields by opshow, the
hardware screen drawn from the facts the rpc carries, the SNMP files, a probe
decided by the VTY's own statement, and the worker's streams - parts as they
come, a cancel that stops the probe, and a pipe left clean for the next call.

The lines below are FRR 10.7.1's and the tools' own, captured in the dev image
(iputils ping, traceroute 2.1, mtr 0.95), not written to fit the patterns.
The OSPF and IS-IS screens' facts are tested in test_prismash_igp.py, beside
the fixtures their screens are already held to.
"""
import contextlib
import io
import os
import stat
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src", "prismash"))
import opshow as O  # noqa: E402
import apiserver as A  # noqa: E402
import prismash as P  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


print("=== show logging: FRR's blocks, one per daemon ===")
LOGGING = """Logging configuration for mgmtd:
Syslog logging: level warnings, facility daemon, ident mgmtd
Stdout logging: level errors
File logging: level informational, filename /tmp/frr.log
Protocol name: MGMTD
Record priority: enabled
Record severity: disabled
Timestamp precision: 3

Logging configuration for zebra:
Syslog logging: disabled
Stdout logging: disabled
File logging: disabled
Protocol name: ZEBRA
Record priority: disabled
Record severity: disabled
Timestamp precision: 0
"""
d = O.parse_routing_logging(LOGGING)
check([x["daemon"] for x in d] == ["mgmtd", "zebra"],
      f"one entry per daemon, in FRR's order: {[x['daemon'] for x in d]}")
check(d[0]["syslog"] == {"enabled": True, "level": "warnings", "facility": "daemon",
                         "ident": "mgmtd", "filename": ""},
      f"syslog: its level, facility and ident: {d[0]['syslog']}")
check(d[0]["file"]["level"] == "informational" and d[0]["file"]["filename"] == "/tmp/frr.log"
      and d[0]["stdout"] == {"enabled": True, "level": "errors", "facility": "",
                             "ident": "", "filename": ""},
      f"file with its filename, stdout with its level: {d[0]['file']}, {d[0]['stdout']}")
check(d[0]["protocol"] == "MGMTD" and d[0]["record_priority"]
      and not d[0]["record_severity"] and d[0]["timestamp_precision"] == 3,
      f"and the record settings: {d[0]}")
check(not any(d[1][t]["enabled"] for t in ("syslog", "stdout", "file"))
      and d[1]["timestamp_precision"] == 0 and not d[1]["record_priority"],
      f"'disabled' is disabled, with nothing else set: {d[1]}")
check(O.parse_routing_logging("") == [] and
      O.parse_routing_logging("% Unknown command.\n") == [],
      "FRR not answering is no daemon, not a parse error")

print("\n=== the probes' lines ===")
check(O.probe_line("ping", "64 bytes from 127.0.0.1: icmp_seq=2 ttl=64 time=0.052 ms")
      == ("reply", {"sequence": 2, "from_address": "127.0.0.1", "ttl": 64,
                    "rtt_ms": 0.052}),
      "ping: a reply, its sequence, address, TTL and round trip")
check((O.probe_line("ping", "64 bytes from 2001:db8::1: icmp_seq=1 ttl=63 time=1.20 ms")[1]
       or {}).get("from_address") == "2001:db8::1",
      "an IPv6 reply's address, colons and all")
check(O.probe_line("ping", "PING 127.0.0.1 (127.0.0.1) 56(84) bytes of data.")
      == (None, None), "the banner is a line, not a reply")
check(O.probe_line("traceroute", " 1  172.17.0.1  0.915 ms")
      == ("hop", {"hop": 1, "address": "172.17.0.1", "answered": True, "rtt_ms": 0.915}),
      "traceroute: a hop that answered")
check(O.probe_line("traceroute", " 3  *")
      == ("hop", {"hop": 3, "address": "", "answered": False, "rtt_ms": 0.0}),
      "and one that did not, '*'")
check(O.probe_line("traceroute", "traceroute to 192.0.2.1 (192.0.2.1), 3 hops max, "
                   "60 byte packets") == (None, None),
      "its banner is not a hop")
k, h = O.probe_line("mtr", "  1.|-- 127.0.0.1                  0.0%     2    0.1   "
                    "0.1   0.1   0.1   0.0")
check(k == "mtr_hop" and h["hop"] == 1 and h["address"] == "127.0.0.1"
      and h["answered"] and h["sent"] == 2 and h["loss_percent"] == 0.0
      and h["avg_ms"] == 0.1, f"mtr: a row of the report: {h}")
k, h = O.probe_line("mtr", "  2.|-- ???                       100.0     2    0.0   "
                    "0.0   0.0   0.0   0.0")
check(k is None or not h["answered"],
      "an mtr hop nobody answered is not read as an address called ???")
k, h = O.probe_line("mtr", "  2.|-- ???                       100.0%     2    0.0   "
                    "0.0   0.0   0.0   0.0")
check(k == "mtr_hop" and h["address"] == "" and not h["answered"]
      and h["loss_percent"] == 100.0, f"'???' is an unanswered hop: {h}")
check(O.probe_line("mtr", "HOST: f1bedf2694c6                Loss%   Snt   Last") == (None, None),
      "mtr's header is not a hop")

e = O.probe_end("ping", "--- 127.0.0.1 ping statistics ---\n"
                "2 packets transmitted, 2 received, 0% packet loss, time 1004ms\n"
                "rtt min/avg/max/mdev = 0.039/0.045/0.052/0.006 ms\n", 0)
check(e["transmitted"] == 2 and e["received"] == 2 and e["loss_percent"] == 0.0
      and e["rtt_known"] and e["rtt_avg_ms"] == 0.045 and e["exit_status"] == 0
      and not e["stopped_at_deadline"], f"ping's summary: {e}")
e = O.probe_end("ping", "2 packets transmitted, 0 received, 100% packet loss, "
                "time 1030ms\n", 1)
check(e["received"] == 0 and e["loss_percent"] == 100.0 and not e["rtt_known"]
      and e["exit_status"] == 1, f"no reply: 100% and no round trip to report: {e}")
e = O.probe_end("ping", "5 packets transmitted, 4 received, +1 errors, 20% packet "
                "loss, time 4005ms\n", 1)
check(e["transmitted"] == 5 and e["received"] == 4 and e["loss_percent"] == 20.0,
      f"with ping's '+N errors' between: {e}")
e = O.probe_end("traceroute", "", 124)
check(e["stopped_at_deadline"] and e["exit_status"] == 124 and e["transmitted"] == 0,
      f"124 is the box's deadline, and a traceroute has no ping summary: {e}")
check(O.probe_end("mtr", "", None)["exit_status"] == -1,
      "no status at all is -1, never 0 (a success nobody saw)")

s = O.LineSplitter()
check(s.feed("64 bytes fr") == [] and s.feed("om x\r\nPING y\nPA") == ["64 bytes from x", "PING y"]
      and s.rest() == ["PA"] and s.rest() == [],
      "a chunk ends anywhere: whole lines out, carriage returns off, the tail last")

print("\n=== hardware and the other fields ===")
hw = O.hardware_fields({"held": ["enp1s0"], "mgmt": "enp6s18",
                        "nics": [{"name": "enp6s18", "pci": "0000:06:12.0",
                                  "driver": "virtio-pci", "dpdk": False,
                                  "state": "up", "speed": None, "mac": "bc:24:11:00:00:01"},
                                 {"name": "enp6s19", "pci": "0000:06:13.0",
                                  "driver": "vfio-pci", "dpdk": True,
                                  "state": "down", "speed": 10000, "mac": "-"}]})
check(hw["dataplane_answered"] and hw["dataplane_holds"] == ["enp1s0"]
      and hw["default_route_interface"] == "enp6s18"
      and hw["nics"][0]["default_route"] and not hw["nics"][1]["default_route"]
      and hw["nics"][0]["speed_mbps"] == 0 and hw["nics"][1]["speed_mbps"] == 10000
      and hw["nics"][1]["dpdk_driver"] and hw["nics"][0]["link"] == "up",
      f"hardware_fields: the default route marked, no speed is 0: {hw}")
hw = O.hardware_fields({"held": None, "mgmt": None, "nics": []})
check(not hw["dataplane_answered"] and hw["dataplane_holds"] == []
      and hw["default_route_interface"] == "",
      f"a dataplane that was not asked is answered False, not 'holds nothing': {hw}")
check(O.ospf_fields({"answered": True, "interfaces": [
        {"name": "x", "area": "-", "state": "Down", "passive": True, "cost": "-",
         "adjacent": 0, "neighbors": 0, "mtu": "-", "dataplane": "",
         "dataplane_mtu": None, "stall": None}]})["interfaces"][0]
      == {"name": "x", "area": "-", "state": "Down", "passive": True, "cost": -1,
          "adjacent": 0, "neighbors": 0, "ospf_mtu": 0, "dataplane": "",
          "dataplane_mtu": 0, "dataplane_mtu_known": False, "mtu_stall": False},
      "ospf_fields: FRR's '-' is -1 for the cost and 0 for the MTU; no "
      "dataplane MTU is known False")
check(O.isis_fields({"answered": True, "circuits": [], "punting": None, "drops": 0})
      == {"answered": True, "circuits": [], "punt_known": False, "punting": False,
          "osi_drops": 0}, "isis_fields: a punt not asked is not known")

print("\n=== show hardware: the screen drawn from the facts the rpc carries ===")


class _Dp:
    def __init__(self, rc, out):
        self.rc, self.out = rc, out

    def run(self, cmd):
        return (self.rc, self.out) if cmd == "show interface" else (1, "")


sh = P.Shell()
sh.dp = _Dp(1, "")
_nics = [{"name": "enp6s18", "pci": "0000:06:12.0", "driver": "virtio-pci",
          "dpdk": False, "state": "up", "speed": 1000, "mac": "bc:24:11:00:00:01"}]
sh.host_nics = lambda: list(_nics)
sh.default_route_nic = lambda: "enp6s18"
for _m in ("claim_note", "waiting_note"):
    setattr(sh, _m, lambda *a: [])
sh.which_dpdk_driver = lambda: "vfio-pci"
sh.one_nic_note = lambda: []
sh.read_dataplane_if = lambda: ""
sh.reserved_for_linux = lambda: "enp6s18"
sh.claimed_at_boot = lambda: ["ens10"]
hf = sh.hardware_facts()
check(hf == {"held": None, "nics": _nics, "mgmt": "enp6s18",
             "claimed": ["ens10"]},
      f"hardware_facts: not asked (None), the NICs, the default route's card, "
      f"what the tuner claimed (#605): {hf}")
got = A.facts_screen(sh, sh.hardware_facts, lambda f: sh.show_hardware([], f))
check(got["facts"] == hf and "enp6s18" in got["text"]
      and "DEFAULT ROUTE" in got["text"] and "not answering" in got["text"],
      "facts_screen: the screen of that reading, the mark and the unanswered "
      "dataplane in it")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.show_hardware([], {"held": ["drawn0"], "mgmt": None,
                          "nics": [dict(_nics[0], name="fromfacts9")]})
check("fromfacts9" in buf.getvalue() and "drawn0" in buf.getvalue()
      and "enp6s18" not in buf.getvalue(),
      f"the screen prints the facts it is given, not a second reading: {buf.getvalue()!r}")

print("\n=== the SNMP files ===")
_d = tempfile.mkdtemp()
_mib = os.path.join(_d, "PRISMA-ROUTING-MIB.in")
with open(_mib, "w") as _f:
    _f.write("PRISMA-ROUTING-MIB DEFINITIONS ::= BEGIN\n  enterprises @PEN@\nEND\n")
P.SNMP_MIB_TEMPLATE = _mib
P.ZABBIX_TEMPLATE = os.path.join(_d, "absent.json.in")


class _Snmp:
    def __init__(self, pen):
        self.snmp = {"enterprise-number": pen} if pen else {}


sh = P.Shell()
sh.running_config = lambda: _Snmp("67033")
r = A.snmp_file_read(sh, "mib")
check(r["ok"] and "enterprises 67033" in r["content"] and "@PEN@" not in r["content"]
      and r["pen"] == "67033",
      f"the MIB with the box's enterprise number in it: {r}")
check(r["content"] == A.screen(sh, "show snmp mib"),
      "exactly what 'show snmp mib' prints")
r = A.snmp_file_read(sh, "zabbix")
check(not r["ok"] and "not on this box" in r["refusal"],
      f"a template not on the box: the VTY's refusal: {r}")
sh.running_config = lambda: _Snmp("")
r = A.snmp_file_read(sh, "mib")
check(not r["ok"] and "no enterprise subtree" in r["refusal"],
      f"no number yet: the VTY's refusal, not a file naming @PEN@: {r}")
check(set(A.SNMP_FILES) == {"mib", "zabbix"}
      and A.SNMP_FILES["zabbix"][3] == "application/json"
      and A.SNMP_FILES["zabbix"][2].endswith(".json"),
      "the template is handed out as the JSON it is")

print("\n=== a probe, decided by the VTY's own statement ===")
_fake = os.path.join(_d, "fakeping")
with open(_fake, "w") as _f:
    _f.write("#!/bin/sh\necho \"PING $*\"\nsleep 0.6\n"
             "echo '64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time=0.039 ms'\n"
             "echo '1 packets transmitted, 1 received, 0% packet loss, time 0ms'\n")
os.chmod(_fake, os.stat(_fake).st_mode | stat.S_IEXEC)
P.PROBE_TOOLS["ping"] = (_fake, "iputils-ping")
P.PROBE_TOOLS["mtr"] = (os.path.join(_d, "no-mtr-here"), "mtr-tiny")
os.environ.pop("PRISMA_IN_MGMT", None)
sh = P.Shell()
parts = list(A.probe_stream(sh, {"tool": "ping", "host": "127.0.0.1", "count": 1,
                                 "source": ""}))
out = "".join(p.get("out", "") for p in parts)
check(f"PING -c 1 -n 127.0.0.1" in out and "icmp_seq=1" in out,
      f"the argv the VTY builds, run, its output in parts: {out!r}")
check(parts[-1] == {"rc": 0, "refusal": ""}, f"and its status last: {parts[-1]}")
check(any(p.get("tick") for p in parts),
      "a quiet stretch sends ticks, so a cancel is noticed while the tool waits")
check("run_probe" not in vars(sh), "the Shell's own run_probe is back afterwards")
for _args, _say in (({"host": "-c9 x"}, "not a host"),
                    ({"host": "127.0.0.1", "count": 101}, "count out of range"),
                    ({"host": "127.0.0.1", "source": "a b"}, "not a source address"),
                    ({"host": ""}, "no host")):
    r = list(A.probe_stream(sh, dict({"tool": "ping", "count": 0, "source": ""}, **_args)))
    check(len(r) == 1 and _say in r[0].get("refused", "") and not r[0]["absent"],
          f"refused as at the prompt ({_say}), and nothing run: {r}")
r = list(A.probe_stream(sh, {"tool": "mtr", "host": "127.0.0.1", "count": 0,
                             "source": ""}))
check(len(r) == 1 and r[0]["absent"] and "this box has no mtr" in r[0]["refused"],
      f"a tool the box lacks is said as the VTY says it: {r}")
r = list(A.probe_stream(sh, {"tool": "rm", "host": "x"}))
check(len(r) == 1 and "not a probe" in r[0]["refused"],
      "only the VTY's three tools are probes")

print("\n=== a stream from the account's process ===")
_saved = dict(A.STREAMS)


def _count(sh, args):
    """Yields n parts, `gap` seconds apart, and records that it was closed."""
    try:
        for i in range(args["n"]):
            time.sleep(args.get("gap", 0))
            yield {"i": i}
    finally:
        with open(args["mark"], "w") as f:
            f.write("closed")


A.STREAMS["count"] = _count


class _FSh:
    candidate = None

    def running_config(self):
        return None


me = __import__("pwd").getpwuid(os.getuid()).pw_name
w = A.Worker(me, shell_factory=_FSh, timeout=20)
_mk = os.path.join(_d, "mark1")
got = list(w.ask_stream("count", {"n": 3, "mark": _mk}))
check(got == [{"i": 0}, {"i": 1}, {"i": 2}], f"ask_stream: every part, in order: {got}")
check(w.lock.acquire(blocking=False), "and the process is free once it ended")
w.lock.release()
_mk = os.path.join(_d, "mark2")
g = w.ask_stream("count", {"n": 400, "gap": 0.05, "mark": _mk})
first = next(g)
g.close()
t0 = time.time()
while not os.path.exists(_mk) and time.time() - t0 < 5:
    time.sleep(0.05)
check(first == {"i": 0} and os.path.exists(_mk),
      "a caller that goes away: the process is told, and its generator closed")
check(w.lock.acquire(timeout=5), "the drain frees the process for the next call")
w.lock.release()
check(w.ask("candidate") is None,
      "and the next request reads its own answer, not the cancelled stream's")
try:
    list(w.ask_stream("count", {"n": 1, "mark": None}))
    check(False, "a stream that fails is a WorkerError")
except A.WorkerError as e:
    check("TypeError" in str(e) or "expected" in str(e),
          f"a stream that fails is a WorkerError: {e}")
check(w.ask("candidate") is None, "and the process still answers after it")
# _drain, on its own: the lock held as ask_stream leaves it on a cancel, a
# stream in flight; it reads to the stream's end and frees the process.
w.lock.acquire()
w.conn.send(("count", {"n": 3, "mark": os.path.join(_d, "mark3")}))
w._drain(5)
check(w.lock.acquire(blocking=False), "_drain reads the stream to its end and frees the lock")
w.lock.release()
check(w.ask("candidate") is None, "and the pipe is clean for the next request")
w.close()
A.STREAMS.clear()
A.STREAMS.update(_saved)


class _Conn:
    """A worker pipe's far end: what was sent, and what poll()/recv() give."""

    def __init__(self, inbox=()):
        self.sent, self.inbox = [], list(inbox)

    def send(self, m):
        self.sent.append(m)

    def poll(self, t=0):
        return bool(self.inbox)

    def recv(self):
        return self.inbox.pop(0)


def _gen(n, mark):
    try:
        for i in range(n):
            yield {"i": i}
    finally:
        mark.append("closed")


_m = []
c = _Conn()
check(A.stream_to(c, _gen(2, _m)) and c.sent == [("part", {"i": 0}), ("part", {"i": 1}),
                                                 ("ok", None)] and _m == ["closed"],
      f"stream_to: each item a part, then ok: {c.sent}")
_m = []
c = _Conn(["cancel"])
check(A.stream_to(c, _gen(5, _m)) and c.sent == [("part", {"i": 0}), ("ok", None)]
      and _m == ["closed"], f"a cancel after a part: the generator closed, ok sent: {c.sent}")
_m = []
c = _Conn([None])
check(A.stream_to(c, _gen(5, _m)) is False and _m == ["closed"],
      "the worker closing mid-stream: generator closed, and the worker told to stop")


def _bad():
    yield {"i": 0}
    raise ValueError("broken")


c = _Conn()
A.stream_to(c, _bad())
check(c.sent[-1][0] == "err" and "broken" in c.sent[-1][1],
      f"a stream that raises ends with err, the worker kept: {c.sent[-1]}")


class _FPB:
    """prisma_pb2's shapes, as plain records, for probe_event_pb."""

    class _R(dict):
        def __init__(self, **kw):
            super().__init__(**kw)

    ProbeEvent = PingReply = TraceHop = MtrHop = _R


e = A.probe_event_pb(_FPB, "ping", "64 bytes from 127.0.0.1: icmp_seq=4 ttl=64 time=0.1 ms")
check(e["line"].startswith("64 bytes") and e["reply"]["sequence"] == 4,
      f"probe_event_pb: a reply line carries its reply: {e}")
e = A.probe_event_pb(_FPB, "traceroute", " 2  *")
check(e["hop"]["hop"] == 2 and not e["hop"]["answered"], f"a hop line its hop: {e}")
e = A.probe_event_pb(_FPB, "mtr", "  1.|-- 10.0.0.1  0.0%  3  1.0  1.0  1.0  1.0  0.0")
check(e["mtr_hop"]["sent"] == 3, f"an mtr row its hop: {e}")
e = A.probe_event_pb(_FPB, "ping", "PING x (x) 56(84) bytes of data.")
check(e == {"line": "PING x (x) 56(84) bytes of data."}, f"any other line, the line alone: {e}")

print("\n=== the doors' chunk generators ===")
import doors  # noqa: E402
got = list(doors.run_chunks(["/bin/sh", "-c", "echo one; echo two"]))
check("".join(v for k, v in got if k == "out") == "one\ntwo\n" and got[-1] == ("rc", 0),
      f"run_chunks: the output, then the status: {got}")
got = list(doors.run_chunks(["/bin/sh", "-c", "sleep 5"], deadline=0.6))
check(got[-1] == ("rc", 124), f"past the deadline the tool is stopped, 124: {got}")
got = list(doors.run_chunks(["/bin/sh", "-c", "sleep 1.2; echo x"], ticks=True))
check(("tick", None) in got and got[-1] == ("rc", 0)
      and not any(k == "tick" for k, _ in doors.run_chunks(["/bin/sh", "-c", "sleep 0.7"]))
      , "ticks while quiet, only when asked")
g = doors.run_chunks(["/bin/sh", "-c", "echo up; exec sleep 30"])
first = next(g)
g.close()
check(first[0] == "out", "closing run_chunks early returns at once (the tool killed)")

import json as _json  # noqa: E402
import socket as _socket  # noqa: E402
import threading as _th  # noqa: E402


def _door(path, lines, delay=0.0):
    """A door that answers one probe with `lines`, `delay` apart."""
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.recv(65536)
        for m in lines:
            time.sleep(delay)
            conn.sendall(_json.dumps(m).encode() + b"\n")
        conn.close()
        srv.close()
    _th.Thread(target=serve, daemon=True).start()


_p = os.path.join(_d, "door1.sock")
_door(_p, [{"out": "a\n"}, {"out": "b\n"}, {"rc": 0}])
got = list(doors.probe_chunks(["/usr/bin/ping"], path=_p))
check(got == [("out", "a\n"), ("out", "b\n"), ("rc", 0, "")],
      f"probe_chunks: the door's chunks, then its status: {got}")
_p = os.path.join(_d, "door2.sock")
_door(_p, [{"rc": 126, "out": "not a probe shape"}])
got = list(doors.probe_chunks(["/bin/sh"], path=_p))
check(got == [("rc", 126, "not a probe shape")], f"a refusal carries the door's words: {got}")
_p = os.path.join(_d, "door3.sock")
_door(_p, [{"out": "late\n"}, {"rc": 0}], delay=1.2)
got = list(doors.probe_chunks(["/usr/bin/ping"], path=_p, ticks=True))
check(("tick", None) in got and got[-1] == ("rc", 0, ""), "ticks while the door is quiet")
_p = os.path.join(_d, "door4.sock")
_door(_p, [])
got = list(doors.probe_chunks(["/usr/bin/ping"], path=_p))
check(got == [("rc", 125, "the host's door went away")], f"a door that hangs up: {got}")
check(O._target("disabled")["enabled"] is False
      and O._target("level debugging, filename /var/log/frr/frr.log")
      == {"enabled": True, "level": "debugging", "facility": "", "ident": "",
          "filename": "/var/log/frr/frr.log"},
      "_target: 'disabled', and a level with its filename")

print("\n=== BFD: a session's fields (#559 phase 2) ===")
# The shape of FRR 10.7.1's `show bfd peers json`, one configured session up
# with its counters folded in by prismash.bfd_sessions(), one a protocol
# asked for, down, with none - test_prismash_bfd.py has where they were read.
UP = {"multihop": False, "peer": "10.9.0.9", "local": "10.9.0.1",
      "vrf": "default", "interface": "dum0", "id": 1, "status": "up",
      "uptime": 8100, "diagnostic": "ok", "remote-diagnostic": "ok",
      "type": "configured", "profile": "fast", "receive-interval": 300,
      "transmit-interval": 300, "detection-timeout": 900,
      "echo-receive-interval": 50, "echo-transmit-interval": 0,
      "detect-multiplier": 3, "remote-receive-interval": 300,
      "remote-transmit-interval": 300, "remote-detect-multiplier": 3,
      "counters": {"id": 1, "control-packet-input": 120,
                   "controlPacketBadInput": 2, "control-packet-output": 121,
                   "session-up": 1, "session-down": 0}}
DOWN = {"multihop": True, "peer": "2001:db8::2", "local": "2001:db8::1",
        "vrf": "red", "id": 2, "minimum-ttl": 254, "status": "down",
        "downtime": 367, "diagnostic": "control detection time expired",
        "type": "dynamic", "receive-interval": 300, "transmit-interval": 300,
        "detection-timeout": 3000, "detect-multiplier": 3, "counters": {}}
f = O.bfd_session_fields(UP)
check(f["state"] == "STATE_UP" and f["seconds_in_state"] == 8100
      and f["configured"] and f["profile"] == "fast"
      and f["detection_timeout_ms"] == 900 and f["remote_detect_multiplier"] == 3,
      f"an up session: state, its time in it, its timers: {f}")
# Mutation shown red: the counter keys read from the session rather than its
# folded-in counters - every count read 0 and counters_known stayed True.
check(f["counters_known"] and f["control_packets_in"] == 120
      and f["control_packets_out"] == 121 and f["control_packets_bad"] == 2
      and f["session_up_count"] == 1 and f["session_down_count"] == 0,
      "and its counters, from bfdd's counters reading")
g = O.bfd_session_fields(DOWN)
check(g["state"] == "STATE_DOWN" and g["seconds_in_state"] == 367
      and not g["configured"] and g["multihop"] and g["minimum_ttl"] == 254
      and g["vrf"] == "red" and g["interface"] == ""
      and g["remote_receive_interval_ms"] == 0 and not g["counters_known"],
      f"a protocol's multihop session, down, no counters: {g}")
check(O.bfd_session_fields({"peer": "10.0.0.1", "status": "AdminDown"})["state"]
      == "STATE_ADMIN_DOWN"
      and O.bfd_session_fields({"peer": "10.0.0.1", "status": "weird"})["state"]
      == "STATE_OTHER"
      and O.bfd_session_fields({"peer": "10.0.0.1"})["state"] == "STATE_UNSPECIFIED",
      "admin down by bfdd's word, an unknown word OTHER, none UNSPECIFIED")
check(O.bfd_fields({"answered": False, "garbled": False, "sessions": []})
      == {"answered": False, "sessions": []}
      and O.bfd_fields({"answered": True, "garbled": True,
                        "sessions": [UP]})["answered"] is False,
      "bfdd not answering, or answering garble, is not 'no sessions'")

print("\n=== BFD: the request's peer and interval ===")
check(O.bfd_peer("") == ("", None)
      and O.bfd_peer("2001:DB8::0002") == ("2001:db8::2", None)
      and O.bfd_peer("bananas")[0] is None,
      "a peer: empty, an address made canonical, or refused")
both = O.bfd_fields({"answered": True, "sessions": [UP, DOWN]})
check([s["peer"] for s in O.bfd_only(both, "2001:db8::2")["sessions"]]
      == ["2001:db8::2"] and len(O.bfd_only(both, "")["sessions"]) == 2,
      "narrowed by address equality, IPv6 spelled any way; empty keeps all")
check(O.bfd_interval(0) == 2 and O.bfd_interval(1) == 1
      and O.bfd_interval(60) == 60 and O.bfd_interval(61) is None,
      "the interval: 0 is 2, 1-60, the rest refused")

print("\n=== BFD: what WatchBfd sends between two readings ===")
def reading(*sessions, answered=True):
    return O.bfd_fields({"answered": answered, "sessions": list(sessions)})
first = O.bfd_changes(None, reading(UP, DOWN))
check([k for k, _, _ in first] == ["KIND_INITIAL", "KIND_INITIAL"],
      "the first reading: every session, as it is")
check(O.bfd_changes(reading(UP, DOWN), reading(UP, DOWN)) == [],
      "nothing changed, nothing sent")
went_down = dict(UP, status="down", downtime=1, counters=dict(UP["counters"],
                 **{"session-down": 1}))
ch = O.bfd_changes(reading(UP), reading(went_down))
check([(k, s["state"], p) for k, s, p in ch]
      == [("KIND_CHANGED", "STATE_DOWN", "STATE_UP")],
      f"up to down: CHANGED, with the state it left: {ch}")
# Mutation shown red: the state compared alone - a flap between two readings
# (down and back up inside 2 s) then sent nothing at all.
flapped = dict(UP, uptime=1, counters=dict(UP["counters"],
                                           **{"session-down": 1}))
ch = O.bfd_changes(reading(UP), reading(flapped))
check([k for k, _, _ in ch] == ["KIND_CHANGED"],
      "down and back up between two readings: the down counter moved, sent")
ch = O.bfd_changes(reading(UP), reading(DOWN))
check(sorted(k for k, _, _ in ch) == ["KIND_APPEARED", "KIND_GONE"],
      f"one session removed and another added: GONE and APPEARED: {ch}")
# bfdd's `id` is drawn afresh at its restart; the key is where the session
# runs, so a renumbered session is the same one (bfd_fields() drops the id).
renumbered = dict(UP, id=999)
check(O.bfd_key(O.bfd_session_fields(UP)) == ("10.9.0.9", "10.9.0.1", "dum0",
                                                 "default", False)
      and O._count({"a": "-3"}, "a") == 0 and O._count({}, "a") == 0
      and O._count({"a": "7"}, "a") == 7,
      "bfd_key is where the session runs; _count clamps to a count")
check(O.bfd_changes(reading(UP), reading(renumbered)) == [],
      "bfdd renumbering its sessions is not a change")
check([k for k, _, _ in O.bfd_changes(reading(UP), reading(answered=False))]
      == ["KIND_UNANSWERED"]
      and O.bfd_changes(reading(answered=False), reading(answered=False)) == []
      and [k for k, _, _ in O.bfd_changes(reading(answered=False), reading(UP))]
      == ["KIND_INITIAL"],
      "bfdd gone: one UNANSWERED, then quiet, then every session again")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
