#!/usr/bin/env python3
"""An IPFIX/UDP collector for lab/log-container.sh (#550 step 3).

  log_udp_sink.py <bind address> <seconds> <out.json> [stop file]

Reads every datagram to <bind address>:4739 and writes one JSON object: the
senders (address:port) seen, how many template messages and data records,
every block record as [event, private, public, portStart], the markers, and
how many data sets arrived before any template. What a lab asserts on: which
address sent the export, and whether a record arrived once, twice, or never.
"""
import json
import os
import socket
import struct
import sys
import time

bind, secs, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
stop = sys.argv[4] if len(sys.argv) > 4 else None
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
s.bind((bind, 4739))
s.settimeout(0.5)
senders, templates, records, markers = {}, set(), [], 0
n_template_msgs = early = 0
end = time.time() + secs
while time.time() < end and not (stop and os.path.exists(stop)):
    try:
        m, frm = s.recvfrom(65535)
    except socket.timeout:
        continue
    key = "%s:%d" % frm
    senders[key] = senders.get(key, 0) + 1
    if len(m) < 20 or struct.unpack("!H", m[:2])[0] != 10:
        continue
    off = 16
    while off + 4 <= len(m):
        sid, slen = struct.unpack("!HH", m[off:off + 4])
        body = m[off + 4:off + slen]
        if sid == 2:
            n_template_msgs += 1
            templates.add(struct.unpack("!H", body[:2])[0])
        elif sid == 256:
            if 256 not in templates:
                early += 1
            for i in range(0, len(body) - 24, 25):
                r = body[i:i + 25]
                records.append([r[8], socket.inet_ntoa(r[9:13]),
                                socket.inet_ntoa(r[13:17]),
                                struct.unpack("!H", r[17:19])[0]])
        elif sid == 257:
            markers += 1
        off += max(slen, 4)
json.dump({"senders": senders, "template_msgs": n_template_msgs,
           "templates": sorted(templates), "records": records,
           "markers": markers, "early": early}, open(out, "w"))
