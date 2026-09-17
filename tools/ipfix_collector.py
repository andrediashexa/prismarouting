#!/usr/bin/env python3
"""
Minimal IPFIX collector, only for checking Prisma Routing's export.

Decodes the template and the data records and prints the RFC 8158 block events
as text. It is not a product: it exists so the tests can prove that what leaves
VPP conforms to the standard, not merely that "some packet went out".
"""
import os, select, socket, struct, sys, time

IE = {
    8:   ("sourceIPv4Address", "ip"),
    160: ("systemInitTimeMilliseconds", "u64"),
    225: ("postNATSourceIPv4Address", "ip"),
    230: ("natEvent", "u8"),
    283: ("natPoolId", "u32"),
    323: ("observationTimeMilliseconds", "u64"),
    361: ("portRangeStart", "u16"),
    362: ("portRangeEnd", "u16"),
}
EVENT = {12: "EXHAUSTION", 16: "ALLOCATION", 17: "DEALLOCATION"}

templates = {}   # template_id -> [(ie, length)]
records = []
pending = []     # data sets seen before their template
# How many of those there were, over the life of this collector.
#
# The holding above is what a serious collector does (docs/logging.md), and it
# is why the defect it forgives is invisible: a record that went out before its
# template decodes here anyway, a few milliseconds later, and every count of
# records still adds up. So the forgiveness is counted as well as performed,
# and a lab can assert that the box never needed it.
held_before_template = 0

def decode(kind, raw):
    if kind == "ip":
        return ".".join(str(b) for b in raw)
    v = int.from_bytes(raw, "big")
    return v

def emit(tid, body, late=False):
    fields = templates[tid]
    size = sum(l for _, l in fields)
    b = 0
    while b + size <= len(body):
        rec = {}
        for ie, ln in fields:
            name, kind = IE.get(ie, (f"ie{ie}", "u32"))
            rec[name] = decode(kind, body[b:b+ln]); b += ln
        records.append(rec)
        ts = rec.get("observationTimeMilliseconds", 0) / 1000.0
        tag = " (reprocessed)" if late else ""
        # The boot marker (IE 160): a record with no natEvent at all. It says
        # when the dataplane that is exporting last started, so that an
        # allocation with no release can be told apart from one the box died
        # holding. See docs/logging.md.
        if "systemInitTimeMilliseconds" in rec:
            init = rec["systemInitTimeMilliseconds"] / 1000.0
            print(f"  skew={time.time() - ts:+.0f}s")
            print(f"  {time.strftime('%H:%M:%S', time.gmtime(ts))} "
                  f"{'RESTART-MARKER':11} exporter init "
                  f"{time.strftime('%H:%M:%S', time.gmtime(init))}{tag}")
            continue
        # str(), because a record may carry no natEvent at all: any template
        # this collector has not been taught about decodes to a row with the
        # field missing, and "{None:11}" raises. It did - the whole collector
        # died on the first boot marker when run against a build that had not
        # been told about IE 160, which is exactly the collector the marker
        # has to survive meeting. A collector that crashes on an unknown
        # record is not a stricter collector, it is one that loses the
        # records after it too.
        ev = str(EVENT.get(rec.get("natEvent"), rec.get("natEvent")))
        # The epoch value as well as the readable one. A record stamped from
        # VPP's boot clock reads "00:00:05", which looks like a time and is
        # not one; the lab needs a number it can subtract from the box's own
        # clock to tell the two apart.
        print(f"  skew={time.time() - ts:+.0f}s")
        print(f"  {time.strftime('%H:%M:%S', time.gmtime(ts))} "
              f"{ev:11} {rec.get('sourceIPv4Address')} -> "
              f"{rec.get('postNATSourceIPv4Address')} "
              f"portas {rec.get('portRangeStart')}-"
              f"{rec.get('portRangeEnd')} pool {rec.get('natPoolId')}{tag}")

def handle(pkt):
    global held_before_template
    if len(pkt) < 16:
        return
    ver, length, exp_time, seq, domain = struct.unpack("!HHIII", pkt[:16])
    if ver != 10:
        print(f"  version {ver}, expected 10 (IPFIX)"); return
    off = 16
    while off + 4 <= length:
        set_id, set_len = struct.unpack("!HH", pkt[off:off+4])
        if set_len < 4:
            break
        body = pkt[off+4:off+set_len]
        if set_id == 2:                      # template set
            b = 0
            while b + 4 <= len(body):
                tid, cnt = struct.unpack("!HH", body[b:b+4]); b += 4
                fields = []
                for _ in range(cnt):
                    ie, ln = struct.unpack("!HH", body[b:b+4]); b += 4
                    if ie & 0x8000:
                        b += 4               # enterprise number
                    fields.append((ie & 0x7fff, ln))
                templates[tid] = fields
                names = ", ".join(f"{IE.get(i,(str(i),))[0]}({l})" for i, l in fields)
                print(f"  TEMPLATE {tid}: {cnt} fields, {sum(l for _,l in fields)} bytes")
                print(f"    {names}")
            # a new template can unblock data that arrived before it
            for tid, body_pending in pending[:]:
                if tid in templates:
                    pending.remove((tid, body_pending))
                    emit(tid, body_pending, late=True)
        elif set_id >= 256:                  # data set
            fields = templates.get(set_id)
            if not fields:
                # a real collector holds and reprocesses when the template arrives
                held_before_template += 1
                print(f"  data for template {set_id} with no template yet: holding")
                pending.append((set_id, body))
            else:
                emit(set_id, body)
        off += set_len

def report_early():
    """What arrived before the layout needed to read it.

    Printed even when it is zero: a line that only appears on a defect is a
    line a lab cannot tell from a collector that was never started.
    """
    print(f"EARLY DATA SETS: {held_before_template} arrived before their "
          f"template")


def report_open():
    """
    Every allocation with no release, read the way a court order is read.

    THE POINT OF THE WHOLE EXERCISE. An open allocation has two meanings and
    they are opposite answers about a subscriber:

      STILL IN FORCE   the exporter has not restarted since; the subscriber
                       holds those ports now
      ENDED AT RESTART the exporter's current incarnation began AFTER the
                       allocation, so the box was replaced while holding the
                       block. It did not observe the end, so the honest answer
                       is a window and not an instant.

    Without the marker every open allocation is the first kind by default,
    which is the reading that names a subscriber who had already gone. This
    function exists so the difference is printed rather than argued about.
    """
    key = lambda r: (r.get("sourceIPv4Address"), r.get("postNATSourceIPv4Address"),
                     r.get("portRangeStart"), r.get("portRangeEnd"))
    markers = sorted(r["observationTimeMilliseconds"] / 1000.0
                     for r in records if "systemInitTimeMilliseconds" in r)
    inits = {}
    for r in records:
        if "systemInitTimeMilliseconds" in r:
            inits[r["observationTimeMilliseconds"] / 1000.0] = \
                r["systemInitTimeMilliseconds"] / 1000.0

    open_allocs = {}
    for r in records:
        ev = r.get("natEvent")
        if ev == 16:
            open_allocs.setdefault(key(r), []).append(
                r["observationTimeMilliseconds"] / 1000.0)
        elif ev == 17:
            got = open_allocs.get(key(r))
            if got:
                got.pop(0)

    print("\nOPEN ALLOCATIONS (allocation with no matching release):")
    any_open = False
    for k, times in sorted(open_allocs.items(), key=lambda kv: str(kv[0])):
        for t in times:
            any_open = True
            # Did an incarnation start AFTER this allocation was made? That is
            # the whole question, and it is one comparison against the
            # allocation's own timestamp.
            #
            # It used to compare the init announced by the last marker at or
            # before the allocation with the one announced by the last marker
            # after it, and markers are periodic. An allocation made in the gap
            # between a restart and the first marker of the NEW incarnation
            # therefore read its "init then" off the OLD incarnation's marker,
            # and every such allocation came out ENDED AT RESTART - at a
            # restart that had happened before it. Captured (#223):
            #
            #   05:17:59 RESTART-MARKER exporter init 05:17:24
            #   05:18:52 RESTART-MARKER exporter init 05:18:17
            #   05:18:28 100.64.0.9 ... ENDED AT RESTART (exporter
            #            reinitialised at 05:18:17)
            #
            # eleven seconds AFTER the restart it was said to have ended at. A
            # subscriber given a block in the first seconds after a reboot was
            # read as having stopped holding it, in the report a court order
            # asks for.
            #
            # An incarnation whose init time is earlier than the allocation
            # cannot have started after it, so "init then" is not needed and
            # the marker cadence stops mattering.
            init_now = inits[markers[-1]] if markers else None
            when = time.strftime('%H:%M:%S', time.gmtime(t))
            if init_now is not None and init_now > t:
                print(f"  {when} {k[0]} -> {k[1]} portas {k[2]}-{k[3]}: "
                      f"ENDED AT RESTART (exporter reinitialised at "
                      f"{time.strftime('%H:%M:%S', time.gmtime(init_now))}; "
                      f"the box did not observe the release)")
            elif not markers:
                print(f"  {when} {k[0]} -> {k[1]} portas {k[2]}-{k[3]}: "
                      f"UNDECIDABLE (no boot marker: cannot tell an allocation "
                      f"in force from one the box died holding)")
            else:
                print(f"  {when} {k[0]} -> {k[1]} portas {k[2]}-{k[3]}: "
                      f"STILL IN FORCE (same exporter incarnation)")
    if not any_open:
        print("  none")


# How small the receive buffer is made when a stall file is in use.
#
# A collector that stops reading only backs the exporter up once the receive
# window AND the exporter's own send buffer are full; until then the kernel
# absorbs everything and the exporter's outbuf stays empty. On a loopback-ish
# veth with the defaults that is well over a hundred kilobytes, which would
# make the stall need thousands of block events to be visible at all. Capping
# the window here shrinks what has to be produced, it does not invent the
# condition: a WAN collector that stops reading closes the window the same way.
STALL_RCVBUF = 4096


def serve_tcp(port, secs, stall_file=None):
    """
    IPFIX over TCP is a continuous stream of messages, not one datagram per
    packet: it has to be reassembled using the length in the header.

    With stall_file set, the collector stays CONNECTED but does not read for as
    long as that file exists. That is the state a slow or wedged collector puts
    the exporter in, and it is the only way to leave bytes sitting in the
    exporter's own out buffer rather than in a kernel socket buffer, which is
    what the exporter loses when the box stops (#219).
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if stall_file:
        # Before listen(): the accepted socket inherits the listener's buffer
        # sizes, and setting it afterwards no longer affects the window that
        # was advertised in the handshake.
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, STALL_RCVBUF)
    srv.bind(("0.0.0.0", port))
    srv.listen(1)
    srv.settimeout(1.0)
    print(f"IPFIX/TCP collector listening on {port} for {secs:.0f}s")
    if stall_file:
        print(f"  stalling while {stall_file} exists "
              f"(SO_RCVBUF {srv.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)})")
    end, buf, conn = time.time() + secs, b"", None
    stalled = False
    while time.time() < end:
        stalling = bool(stall_file and os.path.exists(stall_file))
        if stalling and not stalled:
            print("  STALLED: connected and not reading")
            stalled = True
        elif stalled and not stalling:
            print("  reading again")
            stalled = False
        # The listening socket is watched even while a connection is open, and
        # that is not tidiness. When the box stops holding bytes it could not
        # send, the FIN is queued BEHIND them and the path they would take dies
        # with the dataplane - so the old connection never ends, and a
        # collector that waited for it would never see the box come back.
        watch = [srv] if (conn is None or stalling) else [srv, conn]
        try:
            ready, _, _ = select.select(watch, [], [], 1.0)
        except (ValueError, OSError):
            break
        if conn is not None and conn in ready:
            try:
                chunk = conn.recv(65535)
            except (socket.timeout, ConnectionResetError):
                chunk = None
            if chunk is not None:
                if not chunk:
                    print("  connection closed by the exporter")
                    conn.close(); conn = None; buf = b""
                else:
                    buf += chunk
                    while len(buf) >= 16:
                        mlen = struct.unpack("!H", buf[2:4])[0]
                        if mlen < 16 or len(buf) < mlen:
                            break
                        handle(buf[:mlen])
                        buf = buf[mlen:]
        if srv in ready:
            fresh, _ = srv.accept()
            fresh.settimeout(1.0)
            if conn is not None:
                print("  a second connection: the first is abandoned")
                conn.close()
            conn, buf = fresh, b""
            print("  connection accepted")
    if conn:
        conn.close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "tcp":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 4739
        secs = float(sys.argv[3]) if len(sys.argv) > 3 else 25
        # A fourth argument is a stall file: see serve_tcp().
        serve_tcp(port, secs, sys.argv[4] if len(sys.argv) > 4 else None)
        allocs = sum(1 for r in records if r.get("natEvent") == 16)
        deallocs = sum(1 for r in records if r.get("natEvent") == 17)
        exh = sum(1 for r in records if r.get("natEvent") == 12)
        print(f"\nRESUMO: {len(records)} registros "
              f"({allocs} allocations, {deallocs} releases, {exh} exhaustions)")
        report_early()
        report_open()
        return
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4739
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 25
    # A file whose appearance ends the run, so the CALLER decides when there
    # is nothing more to hear rather than a number guessed before the run
    # started. A collector outlived by its exporter reports the records it did
    # not wait for as allocations nobody ever released, and that reads exactly
    # like a leak (#30).
    stop_file = sys.argv[3] if len(sys.argv) > 3 else None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    s.settimeout(1.0)
    print(f"IPFIX collector listening on udp/{port} for {secs:.0f}s")
    if stop_file:
        print(f"  and until {stop_file} appears, whichever comes first")
    end = time.time() + secs
    n = 0
    # WHICH of the two ended it, because the answer changes what the numbers
    # below mean. Told out of order on purpose: the budget is the ceiling and
    # the stop file is the intended end, so a run that hit the ceiling has to
    # say so even if the file arrived in the same second.
    ended = "its own budget of %.0fs" % secs
    while time.time() < end:
        if stop_file and os.path.exists(stop_file):
            ended = "the caller, through %s" % stop_file
            # DRAIN BEFORE LEAVING. Datagrams already in the receive buffer
            # when the file appears are records the exporter did send, and
            # walking away from them would lose exactly what the stop file
            # exists to stop losing.
            s.settimeout(0.5)
            while True:
                try:
                    data, _ = s.recvfrom(65535)
                except socket.timeout:
                    break
                n += 1
                handle(data)
            break
        try:
            data, _ = s.recvfrom(65535)
        except socket.timeout:
            continue
        n += 1
        handle(data)
    allocs = sum(1 for r in records if r.get("natEvent") == 16)
    deallocs = sum(1 for r in records if r.get("natEvent") == 17)
    exh = sum(1 for r in records if r.get("natEvent") == 12)
    print(f"\nSTOPPED BY: {ended}")
    print(f"SUMMARY: {n} packets, {len(records)} records "
          f"({allocs} allocations, {deallocs} deallocations, {exh} exhaustions)")
    if allocs and deallocs and allocs == deallocs:
        print("Every allocation had its matching deallocation.")
    elif allocs != deallocs:
        print(f"WARNING: {allocs} allocations against {deallocs} deallocations; "
              f"the difference is blocks still in use, or records lost.")
    report_early()
    report_open()


# Only when run as a program. Without the guard, `import` STARTS A COLLECTOR:
# test/test_collector_marker.py loads this file to drive report_open() against
# a fixed timeline, and that import bound udp/4739 and sat there for 25
# seconds inside `make test` - on a box where a lab may already hold that port.
# A module that cannot be read without being run is a module nothing can
# assert about, which is how the rule in report_open() went three days
# unguarded (#223).
#
# Every caller runs this as a program - four labs invoke it with `python3
# tools/ipfix_collector.py ...`, and appliance/install.sh installs it as
# /usr/lib/hexarouting/ipfix-collector - so for all of them __name__ is
# "__main__" and nothing changes.
if __name__ == "__main__":
    main()
