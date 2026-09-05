#!/usr/bin/env python3
"""
The API client's failure surface.

Four outcomes need four different actions from the operator: install something,
start the dataplane, fix the install, fix the configuration. Collapsing them
into "it failed" is how an unplugged dataplane and a rejected configuration
end up indistinguishable - which is the defect this client exists to remove,
so testing only the happy path would miss the entire point.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import vppapi

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def fake(responses):
    """A runner that answers from a table keyed by the first two argv words."""
    def run(argv, timeout=10):
        key = " ".join(argv[:2])
        for prefix, answer in responses.items():
            if key.startswith(prefix):
                return answer
        return (0, "", "")
    return run


print("=== vat2 missing: say what to install, do not blame the config ===")
api = vppapi.Vat2(vat2="/nonexistent/vat2",
                  runner=fake({"/nonexistent/vat2": (127, "", "not found")}))
state, msg = api.available()
check(state == vppapi.NO_CLIENT, f"available() should say no-client, got {state}")
check("vpp package" in msg, f"say where it comes from: {msg!r}")
r = api.call("bpa_pool_add_del", {})
check(r.state == vppapi.NO_CLIENT, f"expected no-client, got {r.state}")
check(not r.ok, "a missing client is not a success")
print(f"  {r.state}: {r.reason}")

print("\n=== VPP not running: not the same as a refusal ===")
api = vppapi.Vat2(runner=fake({
    "vat2 --dump-apis": (0, "bpa_pool_add_del\n", ""),
    "vppctl show": (1, "", "connect failed"),
}))
state, msg = api.available()
check(state == vppapi.NOT_RUNNING, f"expected not-running, got {state}")
check("systemctl" in msg, f"the message should say what to check: {msg!r}")
print(f"  {state}: {msg}")

print("\n=== VPP up, plugin absent: an install problem, not a config problem ===")
api = vppapi.Vat2(runner=fake({
    "vppctl show": (0, "vpp v26.06-release", ""),
    "vat2 --dump-apis": (0, "show_version\nip_route_add_del\n", ""),
}))
state, msg = api.available()
check(state == vppapi.NO_PLUGIN, f"expected no-plugin, got {state}")
print(f"  {state}: {msg}")

api = vppapi.Vat2(runner=fake({"vat2 bpa_pool_add_del": (0, "No such command bpa_pool_add_del", "")}))
r = api.call("bpa_pool_add_del", {})
check(r.state == vppapi.NO_PLUGIN, f"expected no-plugin on call, got {r.state}")
print(f"  on call: {r.reason}")

print("\n=== everything present: ready ===")
api = vppapi.Vat2(runner=fake({
    "vppctl show": (0, "vpp v26.06-release", ""),
    "vat2 --dump-apis": (0, "bpa_pool_add_del\nbpa_pool_dump\n", ""),
}))
state, msg = api.available()
check(state == vppapi.OK, f"expected ok, got {state}: {msg}")
print(f"  {state}")

print("\n=== accepted: retval 0 ===")
api = vppapi.Vat2(runner=fake({
    "vat2 bpa_pool_add_del": (0, json.dumps(
        {"_msgname": "bpa_pool_add_del_reply", "retval": 0}), ""),
}))
r = api.call("bpa_pool_add_del", {"is_add": True})
check(r.ok, f"retval 0 must be a success, got {r}")
print(f"  {r.state}")

print("\n=== refused: retval -7, with the reason from the log ===")
# -7 is what the dataplane actually returned for block_size 500, measured
# against a running VPP. The number alone is not actionable, so the client
# recovers the sentence the plugin sent to VPP's log.
api = vppapi.Vat2(runner=fake({
    "vat2 bpa_pool_add_del": (0, json.dumps(
        {"_msgname": "bpa_pool_add_del_reply", "retval": -7}), ""),
    "vppctl show": (0, "2026/09/05 18:00:00:000 err  bpa  block_size 500 is "
                       "not a power of two\n", ""),
}))
r = api.call("bpa_pool_add_del", {"is_add": True, "block_size": 500})
check(r.state == vppapi.REFUSED, f"expected refused, got {r.state}")
check(r.retval == -7, f"the return value must survive, got {r.retval}")
check("invalid value" in r.reason, f"the code should be named: {r.reason!r}")
check("power of two" in r.reason,
      f"the reason from the log should be carried: {r.reason!r}")
print(f"  {r.reason}")

print("\n=== a dead socket must not be reported as a refusal ===")
# Blaming the configuration for a dataplane that never answered sends the
# operator to edit a pool that was never the problem.
api = vppapi.Vat2(runner=fake({
    "vat2 bpa_pool_add_del": (1, "", "clib_socket_init: connection refused"),
}))
r = api.call("bpa_pool_add_del", {})
check(r.state == vppapi.NOT_RUNNING,
      f"no JSON reply means the dataplane never answered, got {r.state}")
print(f"  {r.state}: {r.reason}")

print("\n=== malformed reply is not silently a success ===")
api = vppapi.Vat2(runner=fake({"vat2 bpa_pool_add_del": (0, "{ this is not json", "")}))
r = api.call("bpa_pool_add_del", {})
check(not r.ok, "unparseable output must never read as success")
check(r.state == vppapi.BAD_REPLY,
      f"unreadable output is a version mismatch, not a dead socket: {r.state}")
print(f"  {r.state}: {r.reason}")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
