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

print("\n=== a change carries who asked; a read does not ===")
# The trail records accountable ACTIONS. Attaching an operator to a dump would
# put a name against reading, which is not what the record is for and not what
# it says.
sent = []


def recording(argv, timeout=10):
    sent.append(json.loads(argv[2]) if len(argv) > 2 else {})
    return (0, '{"retval": 0}', "")


api = vppapi.Vat2(runner=recording, identity=lambda: ("marina", 1001))
api.call("bpa_pool_add_del", {"name": "lab", "is_add": True})
check(sent[-1].get("audit_user") == "marina",
      f"a write must name the operator: {sent[-1]}")
check(sent[-1].get("audit_uid") == 1001,
      f"and carry the uid: {sent[-1]}")
api.call("bpa_pool_dump", {})
check("audit_user" not in sent[-1],
      f"a read must not: {sent[-1]}")
print(f"  write: {sorted(k for k in sent[0] if k.startswith('audit'))}, "
      f"read: {sorted(k for k in sent[-1] if k.startswith('audit'))}")

print("\n=== every message that changes something is on the list ===")
# The list is what decides whether a change is attributable at all. A message
# added to the plugin and forgotten here writes an unattributed entry, and
# nothing anywhere says it should have had a name.
mutating = {"bpa_pool_add_del", "bpa_pool_settings_set", "bpa_bypass_set",
            "bpa_limits_set", "bpa_forward_add_del"}
check(vppapi.WRITE_MESSAGES == mutating,
      f"the write list has drifted: {sorted(vppapi.WRITE_MESSAGES ^ mutating)}")
print(f"  {len(vppapi.WRITE_MESSAGES)} write messages")

print("\n=== sudo: the person, not the account they became ===")
# An operator who runs this under sudo IS root by the time the call is made.
# A trail that records "root" for every one of them has recorded nothing:
# root is the account nobody is.
saved = os.environ.get("SUDO_USER")
try:
    os.environ["SUDO_USER"] = "marina"
    name, uid = vppapi.identity()
    check(name == "marina", f"sudo must name the person who ran it: {name}")
    check(uid == os.geteuid(),
          "and the uid it is acting as, so the line says both")
    print(f"  under sudo: {name} uid {uid}")

    os.environ.pop("SUDO_USER", None)
    name, uid = vppapi.identity()
    check(name != "", "without sudo the account name is still resolved")
    check(uid == os.geteuid(), "and the uid is the real one")
    print(f"  without:    {name} uid {uid}")
finally:
    if saved is None:
        os.environ.pop("SUDO_USER", None)
    else:
        os.environ["SUDO_USER"] = saved

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
