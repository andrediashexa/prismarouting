#!/usr/bin/env python3
"""
Drives the hexash API client against a real dataplane.

The unit test covers the failure surface with the runner stubbed. This is the
other half, and a stub can never prove it: that the parsing matches what vat2
actually prints, and that the return codes are the ones a real VPP returns.

Prints one line per outcome for lab/api.sh to assert on.
"""
import sys

sys.path.insert(0, "/src/src/hexash")
import vppapi  # noqa: E402
from config import Pool  # noqa: E402


def add(api, name, block_size, prefixes):
    """
    Built through Pool.api_args(), not from a dict written out here.

    A second copy of the argument list drifts from the first: when the API
    gained the per-pool settings, the hand-written copy in this file kept
    sending the old shape and vat2 refused to convert it. lab/api.sh spells
    the wire format out on purpose; this half must instead exercise exactly
    what hexash sends.
    """
    p = Pool(name)
    p.block_size = str(block_size)
    p.inside = list(prefixes[:1])
    p.outside = list(prefixes[1:])
    return api.call("bpa_pool_add_del", p.api_args())


api = vppapi.Vat2()

if sys.argv[1:] == ["down"]:
    print("down: " + "|".join(api.available()))
    sys.exit(0)

print("available: " + "|".join(api.available()))

r = add(api, "client-ok", 512, ["100.64.8.0/22", "200.150.9.16/30"])
print(f"valid: {r.state} {r.retval}")

# block_size 500 is not a power of two. The dataplane answers -7, and the
# reason it logged is what makes that number actionable.
r = add(api, "client-bad", 500, ["100.64.12.0/22", "200.150.9.20/30"])
print(f"invalid: {r.state} {r.retval} | {r.reason}")

# #136, both shapes of "the client could not say it", against a real vat2.
#
# The first is refused before the process starts: `flags: 0` is the natural
# way to say administratively down, and vat2 dies on it. The second is the
# reason the first exists - it is a message hexash never sends, chosen because
# it really does make vat2 segfault, so the classification is proved against a
# crashing client rather than against a stub of one.
r = api.call("sw_interface_set_flags", {"sw_if_index": 0, "flags": 0})
print(f"unencodable: {r.state} | {r.reason}")

r = api.call("sw_interface_set_rx_mode",
             {"sw_if_index": 0, "queue_id_valid": False, "queue_id": 0,
              "mode": 0})
print(f"crashing: {r.state} | {r.reason}")
