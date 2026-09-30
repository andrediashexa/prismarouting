#!/usr/bin/env python3
"""
Drives the prismash API client against a real dataplane.

The unit test covers the failure surface with the runner stubbed. This is the
other half, and a stub can never prove it: that the parsing matches what vat2
actually prints, and that the return codes are the ones a real VPP returns.

Prints one line per outcome for lab/api.sh to assert on.
"""
import sys

sys.path.insert(0, "/src/src/prismash")
import vppapi  # noqa: E402
from config import Pool  # noqa: E402


def add(api, name, block_size, prefixes):
    """
    Built through Pool.api_args(), not from a dict written out here.

    A second copy of the argument list drifts from the first: when the API
    gained the per-pool settings, the hand-written copy in this file kept
    sending the old shape and vat2 refused to convert it. lab/api.sh spells
    the wire format out on purpose; this half must instead exercise exactly
    what prismash sends.
    """
    p = Pool(name)
    p.block_size = str(block_size)
    p.inside = list(prefixes[:1])
    p.outside = list(prefixes[1:])
    return api.call("bpa_pool_add_del", p.api_args())


# The transport under test (#527): `vat2`, a process per message, or `papi`,
# prismash's own client - one vpp_papi connection. Every outcome below is
# asserted through both, so the two cannot answer differently.
MODE = sys.argv[1] if sys.argv[1:2] in (["vat2"], ["papi"]) else "vat2"
ARGS = [a for a in sys.argv[1:] if a not in ("vat2", "papi")]
api = vppapi.client() if MODE == "papi" else vppapi.Vat2()
if MODE == "papi":
    print(f"transport: {api.transport} {api.why_vat2}".rstrip())

if ARGS == ["parity"]:
    # EVERY READ THE VTY MAKES, asked of both clients on one dataplane, and
    # the answers compared as the callers see them. The converted vpp_papi
    # reply has to BE vat2's JSON, or a caller reading a field one way reads
    # it wrong the other. Left out: `_crc`, which only vat2 prints and nothing
    # reads, and the flag fields, which vat2 prints as "Invalid ENUM" for
    # most values and which nothing reads either.
    import json
    vat, pap = vppapi.Vat2(), vppapi.client()
    ALL = 0xFFFFFFFF
    reads = [("sw_interface_dump", {"sw_if_index": ALL, "name_filter_valid":
                                    False, "name_filter": ""}),
             ("ip_table_dump", {}),
             ("sw_interface_ip6nd_ra_dump", {"sw_if_index": ALL}),
             ("vrrp_vr_dump", {"sw_if_index": ALL}),
             ("sw_bond_interface_dump", {"sw_if_index": ALL}),
             ("ipfix_exporter_dump", {}),
             ("bpa_pool_dump", {})]
    idx = vat.interface_index("loop0")
    calls = [("sw_interface_get_table", {"sw_if_index": idx or 0,
                                         "is_ipv6": False})]
    SKIP = {"_crc", "flags", "sub_if_flags"}

    def norm(v):
        if isinstance(v, dict):
            return {k: norm(x) for k, x in v.items() if k not in SKIP}
        if isinstance(v, list):
            return [norm(x) for x in v]
        if isinstance(v, float) and v == int(v):
            return int(v)
        return v
    for m, a in reads + calls:
        if (m, a) in reads:
            ra, rb = vat.dump(m, a), pap._dump(m, a, 15)
        else:
            ra, rb = vat.call(m, a), pap._call(m, a, 15)
        fa, fb = norm(ra.fields), norm(rb.fields)
        same = ra.state == rb.state and fa == fb
        n = len(fa.get("rows", [])) if "rows" in fa else 1
        print(f"parity {m}: {'same' if same else 'DIFFERENT'} "
              f"({ra.state}/{rb.state}, {n} answer(s))")
        if not same:
            print("  vat2:", json.dumps(fa, sort_keys=True)[:700])
            print("  papi:", json.dumps(fb, sort_keys=True)[:700])
    sys.exit(0)

if ARGS == ["defined"]:
    # EVERY MESSAGE prismash sends, read off its source, against the
    # definitions the connection loaded (vppapi.API_PLUGINS). One missing is
    # still sent, over vat2 at ~130 ms a process on the bench, and nothing
    # else would ever say so.
    import re
    src = open("/src/src/prismash/prismash.py").read()
    sent = sorted(set(re.findall(
        r'(?:api\.dump|api_call|api\.call)\(\s*"([a-z0-9_]+)"', src)))
    missing = [m for m in sent if not api._defined(m)]
    print(f"defined: {len(sent) - len(missing)} of {len(sent)} messages"
          + (f"; not: {' '.join(missing)}" if missing else ""))
    sys.exit(0)

if ARGS == ["down"]:
    print("down: " + "|".join(api.available()))
    sys.exit(0)

print("available: " + "|".join(api.available()))

# Each transport creates its own pools, on its own addresses: the second run
# must not be refused for the first run's pool.
OK_NAME, OK_PFX = (("client-ok", ["100.64.8.0/22", "200.150.9.16/30"])
                   if MODE == "vat2" else
                   ("client-ok-papi", ["100.64.16.0/22", "200.150.9.24/30"]))
BAD_NAME, BAD_PFX = (("client-bad", ["100.64.12.0/22", "200.150.9.20/30"])
                     if MODE == "vat2" else
                     ("client-bad-papi", ["100.64.20.0/22", "200.150.9.28/30"]))
r = add(api, OK_NAME, 512, OK_PFX)
print(f"valid: {r.state} {r.retval}")

# block_size 500 is not a power of two. The dataplane answers -7, and the
# reason it logged is what makes that number actionable.
r = add(api, BAD_NAME, 500, BAD_PFX)
print(f"invalid: {r.state} {r.retval} | {r.reason}")

# #136, both shapes of "the client could not say it", against a real vat2.
#
# The first is refused before the process starts: `flags: 0` is the natural
# way to say administratively down, and vat2 dies on it. The second is the
# reason the first exists - it is a message prismash never sends, chosen because
# it really does make vat2 segfault, so the classification is proved against a
# crashing client rather than against a stub of one.
r = api.call("sw_interface_set_flags", {"sw_if_index": 0, "flags": 0})
print(f"unencodable: {r.state} | {r.reason}")

if MODE == "papi":
    # vat2's own crash has no counterpart over one connection: the message
    # packs and VPP answers it.
    sys.exit(0)
r = api.call("sw_interface_set_rx_mode",
             {"sw_if_index": 0, "queue_id_valid": False, "queue_id": 0,
              "mode": 0})
print(f"crashing: {r.state} | {r.reason}")
