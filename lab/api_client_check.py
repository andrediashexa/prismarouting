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

BASE = {
    "port_min": 1024,
    "port_max": 65535,
    "policy": "BPA_API_POLICY_INTERLEAVED",
    "mode": "BPA_API_MODE_DETERMINISTIC",
    "n_inside": 1,
    "n_prefixes": 2,
}


def add(api, name, block_size, prefixes):
    args = dict(BASE, is_add=True, name=name, block_size=block_size,
                prefixes=prefixes)
    return api.call("bpa_pool_add_del", args)


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
