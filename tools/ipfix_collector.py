#!/usr/bin/env python3
"""
Minimal IPFIX collector, only for checking HexaRouting's export.

Decodes the template and the data records and prints the RFC 8158 block events
as text. It is not a product: it exists so the tests can prove that what leaves
VPP conforms to the standard, not merely that "some packet went out".
"""
import socket, struct, sys, time

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
                print(f"  data for template {set_id} with no template yet: holding")
                pending.append((set_id, body))
            else:
                emit(set_id, body)
        off += set_len

def report_open():
    """
    Every allocation with no release, read the way a court order is read.

    THE POINT OF THE WHOLE EXERCISE. An open allocation has two meanings and
    they are opposite answers about a subscriber:

      STILL IN FORCE   the exporter has not restarted since; the subscriber
                       holds those ports now
      ENDED AT RESTART the exporter announced a new systemInitTime afterwards,
                       so the box was replaced while holding the block. It did
                       not observe the end, so the honest answer is a window
                       and not an instant.

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
            # The init time in force when the allocation was made, against the
            # latest one announced. Different means the box was replaced while
            # it held the block.
            before = [m for m in markers if m <= t]
            after = [m for m in markers if m > t]
            init_then = inits[before[-1]] if before else None
            init_now = inits[after[-1]] if after else init_then
            when = time.strftime('%H:%M:%S', time.gmtime(t))
            if init_then is not None and init_now is not None \
               and init_now > init_then:
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


def serve_tcp(port, secs):
    """
    IPFIX over TCP is a continuous stream of messages, not one datagram per
    packet: it has to be reassembled using the length in the header.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(1)
    srv.settimeout(1.0)
    print(f"IPFIX/TCP collector listening on {port} for {secs:.0f}s")
    end, buf, conn = time.time() + secs, b"", None
    while time.time() < end:
        if conn is None:
            try:
                conn, _ = srv.accept()
                conn.settimeout(1.0)
                print("  connection accepted")
            except socket.timeout:
                continue
        try:
            chunk = conn.recv(65535)
        except socket.timeout:
            continue
        if not chunk:
            print("  connection closed by the exporter")
            conn.close(); conn = None; continue
        buf += chunk
        while len(buf) >= 16:
            mlen = struct.unpack("!H", buf[2:4])[0]
            if mlen < 16 or len(buf) < mlen:
                break
            handle(buf[:mlen])
            buf = buf[mlen:]
    if conn:
        conn.close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "tcp":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 4739
        secs = float(sys.argv[3]) if len(sys.argv) > 3 else 25
        serve_tcp(port, secs)
        allocs = sum(1 for r in records if r.get("natEvent") == 16)
        deallocs = sum(1 for r in records if r.get("natEvent") == 17)
        exh = sum(1 for r in records if r.get("natEvent") == 12)
        print(f"\nRESUMO: {len(records)} registros "
              f"({allocs} alocacoes, {deallocs} liberacoes, {exh} esgotamentos)")
        report_open()
        return
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4739
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 25
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    s.settimeout(1.0)
    print(f"IPFIX collector listening on udp/{port} for {secs:.0f}s")
    end = time.time() + secs
    n = 0
    while time.time() < end:
        try:
            data, _ = s.recvfrom(65535)
        except socket.timeout:
            continue
        n += 1
        handle(data)
    allocs = sum(1 for r in records if r.get("natEvent") == 16)
    deallocs = sum(1 for r in records if r.get("natEvent") == 17)
    exh = sum(1 for r in records if r.get("natEvent") == 12)
    print(f"\nSUMMARY: {n} packets, {len(records)} records "
          f"({allocs} allocations, {deallocs} deallocations, {exh} exhaustions)")
    if allocs and deallocs and allocs == deallocs:
        print("Every allocation had its matching deallocation.")
    elif allocs != deallocs:
        print(f"WARNING: {allocs} allocations against {deallocs} deallocations; "
              f"the difference is blocks still in use, or records lost.")
    report_open()

main()
