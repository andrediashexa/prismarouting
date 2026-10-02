#!/usr/bin/env python3
"""
The CGNAT log export's state as the API serves it (#555): `show bpa logging`
read into fields, the three streams' rows, and the rule that says a stream
is delivering or stalled - with the events and the health answer it makes.

The screens below are the ones the legacy bench printed on 0.149.14; the
last check holds the parser to the format strings in bpa_cli.c, so a change
of wording there turns this red instead of a field reading 0.
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src", "prismash"))
import logstate as L  # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


def screen(syslog=None, container=True, udp_sender=None, spill=0, over=0,
           pending=0, sent=1000, conn="connected", tcp=False, udp_on=True,
           repeats=690168, queue=0, export=0, tx=0):
    """A `show bpa logging` screen in the plugin's own words."""
    out = ["IPFIX export of block events (RFC 8158)",
           f"  state       {'enabled' if udp_on else 'disabled'}"]
    out.append("  exporter    198.51.100.3:4739 src 198.51.100.2 (path mtu 512, "
               "template every 20s)" if udp_on else
               "  exporter    none: nothing is sent over UDP")
    out += ["  template    256 (25 bytes per record)",
            "  records     7 emitted, 2 dropped",
            udp_sender or ("  sender      the log container: 1020 records, 68 packets, "
                           "8 templates, 0 send failures, 0 dropped" if container
                           else "  sender      the dataplane"),
            "  natEvent    16 allocation, 17 de-allocation, 12 exhaustion",
            f"  exhaustion  one record per subscriber per 10s: {repeats} repeats not reported",
            f"  queue       {queue} event(s) dropped with a worker's queue full (8192 each)",
            "  rings       /dev/shm/prisma-bpa-log, 7604928 bytes, outside the main heap",
            f"  export      {export} event(s) dropped with the log container's ring full (65536)",
            f"  transmit    ens20: {tx} packet(s) the interface failed to send, from every "
            "sender on it",
            "  boot marker template 257, 13 sent, init 2026-10-02T00:39:31Z "
            "(IE 160 systemInitTimeMilliseconds)",
            "  flushes     7 with data, 379 empty, 0 without buffer",
            ("TCP streams carried by the log container (pid 7)" if container else
             "TCP streams carried by the dataplane: no log container is running")]
    if tcp:
        out += ["TCP transport (survives collector outage)",
                "  collector   198.51.100.3:4739  connected",
                "  records     5 emitted, 400 bytes sent, 0 pending",
                "  spill       0 bytes on disk, 0 replayed, 0 dropped",
                "  file        0 bytes now of 256 MB at most, 0 dropped over it, 0 resynced, "
                "0 stalls",
                "  connections 1, service 9, send 3, errno 0"]
    else:
        out.append("TCP transport: disabled")
    if syslog is False:
        out.append("syslog transport: disabled")
    else:
        out += ["syslog transport (RFC 5424 over TCP)",
                f"  collector   198.51.100.3:601  {conn}",
                "  enterprise  32473, facility 16, severity 6",
                f"  lines       3089 written, {sent} bytes sent, {pending} pending",
                "  spill       10 bytes on disk, 4 replayed, 1 dropped",
                f"  file        {spill} bytes now of 256 MB at most, {over} dropped over it, "
                "124 resynced, 0 stalls"]
    return "\n".join(out) + "\n"


print("=== parse_logging: the screen into fields ===")
p = L.parse_logging(screen(tcp=True))
check(p["carrier"] == "log-container" and p["logger_pid"] == 7,
      f"who carries the TCP streams, with the pid: {p['carrier']} {p['logger_pid']}")
check(p["udp"]["exporter"] == {"collector": "198.51.100.3:4739", "source": "198.51.100.2"},
      f"the UDP exporter and its source: {p['udp']['exporter']}")
check((p["udp"]["sender"], p["udp"]["container_records"], p["udp"]["packets"]) ==
      ("log-container", 1020, 68),
      "the UDP sender is the log container, with its records and packets")
check((p["udp"]["records"], p["udp"]["dropped"]) == (7, 2),
      "the dataplane's own UDP records line, read in the IPFIX section only")
check((p["exhaustion_repeats"], p["queue_dropped"], p["export_dropped"]) == (690168, 0, 0),
      "the box-wide drop lines")
check(p["rings_shared"] and (p["transmit_interface"], p["transmit_failed"]) == ("ens20", 0),
      "the rings outside the heap, and the transmit line")
s = p["syslog"]
check(s["configured"] and s["collector"] == "198.51.100.3:601" and s["connection"] == "connected",
      f"syslog's collector and connection: {s}")
check((s["records"], s["bytes_sent"], s["pending"]) == (3089, 1000, 0),
      "syslog's lines, bytes sent and pending")
check((s["spilled"], s["replayed"], s["dropped"], s["spill_bytes"], s["spill_cap_mb"],
       s["dropped_over_cap"]) == (10, 4, 1, 0, 256, 0), "syslog's spill and file lines")
t = p["ipfix-tcp"]
check(t["configured"] and (t["records"], t["bytes_sent"]) == (5, 400),
      "IPFIX over TCP's records line, which says 'emitted' where syslog says 'written'")
p2 = L.parse_logging(screen(syslog=False, container=False, udp_on=False))
check(not p2["syslog"]["configured"] and not p2["ipfix-tcp"]["configured"]
      and p2["udp"]["exporter"] is None and p2["carrier"] == "dataplane",
      "a box with nothing configured and no container reads as such")
p3 = L.parse_logging(screen(container=False, udp_sender="  sender      the dataplane: the "
                            "exporter is in a VRF, which the log container does not take"))
check(p3["udp"]["sender"] == "dataplane" and "in a VRF" in p3["udp"]["sender_reason"],
      "why the dataplane kept the UDP export is read")

print("=== stream_rows: the three LogStream rows ===")
rows = {r["kind"]: r for r in L.stream_rows(p)}
check(list(rows) == list(L.STREAMS), f"syslog, ipfix-tcp, ipfix-udp, in order: {list(rows)}")
u = rows["ipfix-udp"]
check(u["configured"] and u["carrier"] == "log-container" and u["records"] == 1020
      and u["packets_sent"] == 68 and u["source"] == "198.51.100.2",
      "UDP's row takes the container's counters while it sends")
check(rows["syslog"]["spill_cap_bytes"] == 256 << 20, "the cap in bytes")
off = {r["kind"]: r for r in L.stream_rows(p2)}
check(not off["syslog"]["configured"] and off["syslog"]["carrier"] == ""
      and not off["ipfix-udp"]["configured"], "unconfigured streams have no carrier")

print("=== LogWatch: delivering, stalled, events, health ===")
w = L.LogWatch(stall=60)
check(w.health()[0] == "NOT_SERVING", "no reading yet is not serving, with why")
w.observe(screen(), now=1000)
check(w.health() == ("SERVING", ""), "a connected collector taking bytes serves")
# The collector goes: the connection drops, lines wait.
ev = w.observe(screen(conn="disconnected", pending=500, sent=1000), now=1010)
check([e["kind"] for e in ev] == ["log-disconnected"],
      f"losing the collector is an event: {[e['kind'] for e in ev]}")
check(w.health()[0] == "SERVING", "and is not yet a stall")
ev = w.observe(screen(conn="disconnected", pending=500, spill=900, sent=1000), now=1040)
check([e["kind"] for e in ev] == ["log-spill"], "spilling to disk is an event")
ev = w.observe(screen(conn="disconnected", pending=500, spill=900, sent=1000), now=1071)
st, why = w.health()
check([e["kind"] for e in ev] == ["log-stalled"] and st == "NOT_SERVING"
      and "syslog" in why and "disconnected" in why,
      f"a minute of nothing delivered with lines waiting is a stall: {why!r}")
_, rws, *_ = w.snapshot()
sy = [r for r in rws if r["kind"] == "syslog"][0]
check(not sy["delivering"] and sy["last_progress"] == 1000,
      f"the row says so, and when it last got anything out: {sy['last_progress']}")
ev = w.observe(screen(conn="disconnected", pending=500, spill=900, over=77, sent=1000),
               now=1080)
check([e["kind"] for e in ev] == ["log-spill-full"], "dropping over the cap is an event")
ev = w.observe(screen(conn="connected", pending=0, spill=0, sent=9000), now=1090)
check([e["kind"] for e in ev] == ["log-connected", "log-delivering"]
      and w.health()[0] == "SERVING", "the collector back and bytes going: delivering again")
check(w.last_seq() == 6 and [s for s, _ in w.events_after(4)] == [5, 6],
      "the events are numbered for a subscriber that resumes")
# Idle and disconnected: nothing to send is not a stall.
w.observe(screen(conn="disconnected", pending=0, spill=0, sent=9000), now=1100)
w.observe(screen(conn="disconnected", pending=0, spill=0, sent=9000), now=2000)
check(w.health()[0] == "SERVING", "a collector that is down with nothing to send is no stall")
# The carrier changes.
ev = w.observe(screen(container=False, conn="disconnected", sent=9000), now=2010)
check("log-carrier" in [e["kind"] for e in ev] and "dataplane" in ev[0]["text"],
      f"the dataplane taking a stream back is an event: {[e['text'] for e in ev]}")
# The dataplane does not answer.
w.observe("", now=2020)
check(w.health()[0] == "NOT_SERVING" and "does not answer" in w.health()[1],
      "a dataplane that does not answer is not serving, said")
# UDP: sends that fail for a minute, no packet out.
w2 = L.LogWatch(stall=60)
sender = "  sender      the log container: 10 records, 5 packets, 2 templates, {f} send failures, 0 dropped"
w2.observe(screen(syslog=False, udp_sender=sender.format(f=0)), now=0)
w2.observe(screen(syslog=False, udp_sender=sender.format(f=3)), now=30)
w2.observe(screen(syslog=False, udp_sender=sender.format(f=9)), now=61)
check(w2.health()[0] == "NOT_SERVING" and "ipfix-udp" in w2.health()[1],
      f"UDP sends failing for a minute with no packet out is a stall: {w2.health()[1]!r}")
w2.observe(screen(syslog=False, udp_sender=sender.format(f=9).replace("5 packets", "6 packets")),
           now=70)
check(w2.health()[0] == "SERVING", "and a packet out ends it")
check(len(w.events) <= w.keep, "the kept events are bounded")

print("=== the helpers, one by one ===")
check(L._num(r"a (\d+) b (\d+)", "a 3 b 40") == [3, 40] and L._num(r"x (\d+)", "y 1") is None,
      "_num: the integers a pattern captures, or None")
_o = L.parse_logging("")
L._udp_line(_o, "queue       12 event(s) dropped with a worker's queue full (8192 each)")
check(_o["queue_dropped"] == 12, "_udp_line: one IPFIX-section line into the dict")
_t = {}
L._tcp_line(_t, "lines       1 written, 2 bytes sent, 3 pending")
check((_t["records"], _t["bytes_sent"], _t["pending"]) == (1, 2, 3),
      "_tcp_line: one stream line into the stream's dict")
check(L._event("log-x", "t") == {"kind": "log-x", "account": "", "text": "t",
                                 "revision": "", "comment": ""},
      "_event: an Event's fields, as state_events builds them")
check(L._who("log-container") == "log container" and L._who("dataplane") == "dataplane",
      "_who: the carrier for a sentence")
check("disconnected from" in L.LogWatch._why({"kind": "syslog", "connection": "disconnected",
                                              "collector": "c:1", "pending_bytes": 1,
                                              "spill_bytes": 2}, 61.5)
      and "takes nothing" in L.LogWatch._why({"kind": "syslog", "connection": "connected",
                                              "collector": "c:1", "pending_bytes": 1,
                                              "spill_bytes": 2}, 61)
      and "send" in L.LogWatch._why({"kind": "ipfix-udp", "connection": ""}, 61),
      "LogWatch._why: the sentence for each way of stalling")

print("=== the parser is held to bpa_cli.c's format strings ===")
cli = open(os.path.join(ROOT, "src", "plugins", "bpa", "bpa_cli.c")).read()
flat = re.sub(r'"\s*\n\s*"', "", cli)   # adjacent C string literals joined
for fmt in ("TCP streams carried by the log container (pid %u)",
            "TCP streams carried by the dataplane",
            "TCP transport (survives collector outage)",
            "TCP transport: disabled",
            "syslog transport (RFC 5424 over TCP)",
            "syslog transport: disabled",
            "  exporter    %U:%u src %U (path mtu",
            "  records     %Lu emitted, %Lu dropped",
            "  sender      the log container: %Lu records, %Lu packets, %Lu templates, "
            "%Lu send failures, %Lu dropped",
            "  sender      the dataplane",
            "repeats not reported",
            "  queue       %Lu event(s) dropped with a worker's queue",
            "  export      %Lu event(s) dropped with the log container's ring",
            "outside the main heap",
            "  transmit    %v: %Lu packet(s) the interface failed to send",
            "  collector   %U:%u  %s",
            "  records     %Lu emitted, %Lu bytes sent, %Lu pending",
            "  lines       %Lu written, %Lu bytes sent, %Lu pending",
            "  spill       %Lu bytes on disk, %Lu replayed, %Lu dropped",
            "  file        %Lu bytes now of %Lu MB at most, %Lu dropped over it"):
    check(fmt in flat, f"bpa_cli.c still prints {fmt!r}")

print()
print("ALL TESTS PASSED" if not fail else f"{fail} FAILURES")
sys.exit(1 if fail else 0)
