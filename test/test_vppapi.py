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

print("\n=== a value the client cannot encode is never handed to vat2 ===")
# vat2 dies on a null pointer for anything that is not a single flag NAME in
# an `enum` field (#136). Measured spellings, all of them SIGSEGV against vpp
# 26.06-release: 20, 0, null, ["A"], ["A","B"]. A commit that dumps core in
# the middle has told the operator nothing about which field was wrong, so the
# refusal has to happen before the process starts - which is what `attempts`
# asserts here. Without the guard this test crashes a real vat2 instead.
attempts = []


def counting(argv, timeout=10):
    attempts.append(argv)
    return (0, '{"retval": 0}', "")


for bad in (20, 0, None, ["SUB_IF_API_FLAG_TWO_TAGS"],
            ["SUB_IF_API_FLAG_TWO_TAGS", "SUB_IF_API_FLAG_EXACT_MATCH"],
            "SUB_IF_API_FLAG_TWO_TAGS|SUB_IF_API_FLAG_EXACT_MATCH",
            "SUB_IF_API_FLAG_TWO_TAGS,SUB_IF_API_FLAG_EXACT_MATCH",
            "SUB_IF_API_FLAG_TWO_TAGS SUB_IF_API_FLAG_EXACT_MATCH", ""):
    api = vppapi.Vat2(runner=counting)
    r = api.call("create_subif", {"sw_if_index": 1, "sub_id": 100,
                                  "sub_if_flags": bad,
                                  "outer_vlan_id": 100, "inner_vlan_id": 900})
    check(r.state == vppapi.CANNOT_SEND,
          f"sub_if_flags={bad!r} must be refused, got {r.state}")
    check("sub_if_flags" in r.reason,
          f"the reason must name the field: {r.reason!r}")
check(attempts == [], f"nothing may be run for a value that crashes it: "
                      f"{attempts}")
print(f"  9 spellings refused, vat2 started {len(attempts)} times")

# The one spelling that works still goes through, or the guard has replaced
# the defect with a worse one.
api = vppapi.Vat2(runner=counting)
r = api.call("create_subif", {"sw_if_index": 1, "sub_id": 100,
                              "sub_if_flags": "SUB_IF_API_FLAG_TWO_TAGS",
                              "outer_vlan_id": 100, "inner_vlan_id": 900})
check(r.ok, f"a single flag name must still be sent: {r.state} {r.reason}")
check(len(attempts) == 1, f"and it must reach vat2: {attempts}")
print(f"  one flag name: {r.state}")

print("\n=== a client that crashed is not a dataplane that died ===")
# The distinction this whole module exists for, applied to its own failures.
# Both of these used to come back as not-running, sending the operator to
# `systemctl status vpp` for a box that was answering perfectly.
api = vppapi.Vat2(runner=fake({"vat2 sw_interface_set_rx_mode": (-11, "", "")}))
r = api.call("sw_interface_set_rx_mode", {"sw_if_index": 1, "mode": 0})
check(r.state == vppapi.CANNOT_SEND,
      f"a signal death is the client's failure, not VPP's: {r.state}")
check("11" in r.reason, f"and says which signal: {r.reason!r}")
print(f"  {r.state}: {r.reason}")

api = vppapi.Vat2(runner=fake({"vat2 create_subif": (139, "", "")}))
r = api.call("create_subif", {"sw_if_index": 1})
check(r.state == vppapi.CANNOT_SEND,
      f"128+n from a shell means the same thing: {r.state}")
print(f"  via a shell: {r.state}: {r.reason}")

# vat2 exits 0 when it merely refuses to convert, so the return code says
# nothing at all here and only the text does.
api = vppapi.Vat2(runner=fake({"vat2 create_subif": (
    0, "", "Failed converting JSON to API\nCall failed: create_subif\n")}))
r = api.call("create_subif", {"sw_if_index": 1})
check(r.state == vppapi.CANNOT_SEND,
      f"a refusal to convert is not a dead dataplane: {r.state}")
check("never saw it" in r.reason, f"say that nothing was applied: {r.reason!r}")
print(f"  {r.state}: {r.reason}")

# A dump reaches the same conclusion, or interface_index() turns a crashed
# client into "the dataplane does not have that interface".
api = vppapi.Vat2(runner=fake({"vat2 sw_interface_dump": (-11, "", "")}))
r = api.dump("sw_interface_dump", {})
check(r.state == vppapi.CANNOT_SEND,
      f"a dump must not report a crash as a dead dataplane: {r.state}")
print(f"  on a dump: {r.state}")

# And a dataplane that really is not answering still says so.
api = vppapi.Vat2(runner=fake({"vat2 bpa_pool_add_del": (1, "", "")}))
r = api.call("bpa_pool_add_del", {})
check(r.state == vppapi.NOT_RUNNING,
      f"a silent failure is still not-running: {r.state}")
print(f"  a real silence: {r.state}")

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
            "bpa_limits_set", "bpa_forward_add_del",
            "bpa_interface_add_del_feature"}
check(vppapi.WRITE_MESSAGES == mutating,
      f"the write list has drifted: {sorted(vppapi.WRITE_MESSAGES ^ mutating)}")
print(f"  {len(vppapi.WRITE_MESSAGES)} write messages")

print("\n=== sudo: the person, not the account they became ===")
# An operator who runs this under sudo IS root by the time the call is made.
# A trail that records "root" for every one of them has recorded nothing:
# root is the account nobody is.
saved = os.environ.get("SUDO_USER")
try:
    # The uid checked against a KNOWN value rather than against os.geteuid(),
    # which is the one expression identity() itself evaluates: comparing the
    # two asserted os.geteuid() == os.geteuid() and would have held for any
    # uid identity() cared to return. Stood in for, so the number under test
    # is not the number the test host happens to run as - and so the case the
    # docstring is about, an operator who has BECOME root, is actually
    # produced: under sudo the effective uid is 0 and the real one is not.
    _real_geteuid = vppapi.os.geteuid
    vppapi.os.geteuid = lambda: 0
    try:
        os.environ["SUDO_USER"] = "marina"
        name, uid = vppapi.identity()
        check(name == "marina", f"sudo must name the person who ran it: {name}")
        check(uid == 0,
              f"and the uid it is ACTING as, which under sudo is root: {uid}")
        print(f"  under sudo: {name} uid {uid}")

        os.environ.pop("SUDO_USER", None)
        vppapi.os.geteuid = lambda: 1000
        name, uid = vppapi.identity()
        check(name != "", "without sudo the account name is still resolved")
        check(uid == 1000, f"and the uid is the one it is running as: {uid}")
        print(f"  without:    {name} uid {uid}")
    finally:
        vppapi.os.geteuid = _real_geteuid
finally:
    if saved is None:
        os.environ.pop("SUDO_USER", None)
    else:
        os.environ["SUDO_USER"] = saved

print("\n=== the units of this module, each called on its own (#390) ===")
# Every function below is called directly with the input it exists for, and
# each box was watched going red against a mutation of the rule it reads.
import grp        # noqa: E402
import tempfile   # noqa: E402

# Reply.ok is the one question every caller asks of a reply. A property is
# read and not called, so the reader excuses it at its definition; it is
# still read both ways here, or a state renamed would make every failure
# read as a success.
check(vppapi.Reply(vppapi.OK).ok is True, "a reply in state ok answers ok")
for state in (vppapi.NO_CLIENT, vppapi.NOT_RUNNING, vppapi.NO_PLUGIN,
              vppapi.REFUSED, vppapi.BAD_REPLY, vppapi.CANNOT_SEND):
    check(vppapi.Reply(state).ok is False, f"and {state} answers not ok")

# Reply.__repr__ is what a failed assertion prints, so it has to carry the
# three things that tell one outcome from another.
text = vppapi.Reply(vppapi.REFUSED, retval=-7,
                    reason="not a power of two").__repr__()
check(vppapi.REFUSED in text and "-7" in text and "not a power of two" in text,
      f"the repr carries the state, the retval and the reason: {text}")
print(f"  repr: {text}")

# Vat2._client_failure: the two ways vat2 fails on its own, told apart from
# a dataplane that failed, on the code and the text alone.
r = vppapi.Vat2._client_failure("create_subif", -11, "")
check(r is not None and r.state == vppapi.CANNOT_SEND and "signal 11" in r.reason,
      f"a negative code is a signal death, named by its number: {r}")
r = vppapi.Vat2._client_failure("create_subif", 139, "")
check(r is not None and "signal 11" in r.reason,
      f"128+n from a shell is the same signal 11: {r}")
r = vppapi.Vat2._client_failure("create_subif", 0,
                                "Failed converting JSON to API\n")
check(r is not None and r.state == vppapi.CANNOT_SEND
      and "never saw it" in r.reason,
      f"a refusal to convert, exit 0, is the client's failure: {r}")
check(vppapi.Vat2._client_failure("bpa_pool_add_del", 1,
                                  "connect failed") is None,
      "exit 1 with neither is not the client's failure: None")
check(vppapi.Vat2._client_failure("bpa_pool_add_del", 0,
                                  '{"retval": 0}') is None,
      "and neither is a clean reply")

# Vat2._parse: vat2 prints a JSON object, with noise before it.
check(vppapi.Vat2._parse('vat2: connecting\n{"_msgname": "x", "retval": 0}')
      == {"_msgname": "x", "retval": 0},
      "the object is read from its first brace, the noise before it dropped")
check(vppapi.Vat2._parse("") is None, "nothing at all is None")
check(vppapi.Vat2._parse("connect failed") is None, "no brace is None")
check(vppapi.Vat2._parse("{ this is not json") is None,
      "a brace with no object behind it is None, not an exception")

# Vat2._last_log_error: the sentence the plugin sent to VPP's log, the LAST
# bpa error, with the timestamp and severity stripped off.
LOG = ("2026/09/05 18:00:00:000 notice bpa  pool lab created\n"
       "2026/09/05 18:00:01:000 err  bpa  block_size 500 is not a power of two\n"
       "2026/09/05 18:00:02:000 err  bpa  outside 200.150.0.16/30 overlaps pool lab\n"
       "2026/09/05 18:00:03:000 notice dpdk  link up\n")
api = vppapi.Vat2(runner=fake({"vppctl show": (0, LOG, "")}))
detail = api._last_log_error()
check(detail == "outside 200.150.0.16/30 overlaps pool lab",
      f"the last bpa error line, prefix stripped: {detail!r}")
api = vppapi.Vat2(runner=fake({"vppctl show": (0, LOG.splitlines()[0] + "\n", "")}))
check(api._last_log_error() == "",
      "a log with no bpa error in it adds nothing, not the last notice")
api = vppapi.Vat2(runner=fake({"vppctl show": (1, "", "connect failed")}))
check(api._last_log_error() == "", "a vppctl that failed adds nothing")

# Vat2.interface_index: the name the operator typed to the index the
# dataplane takes, off a dump, and None for anything that is not an answer.
ROWS = json.dumps([
    {"_msgname": "sw_interface_details", "sw_if_index": 0,
     "interface_name": "local0"},
    {"_msgname": "sw_interface_details", "sw_if_index": 3,
     "interface_name": "GigabitEthernet0/8/0"}])
dumped = []


def dumping(argv, timeout=10):
    dumped.append(argv)
    return (0, ROWS, "")


api = vppapi.Vat2(runner=dumping)
check(api.interface_index("GigabitEthernet0/8/0") == 3,
      "the index of the row whose name matches")
check(api.interface_index("local0") == 0,
      "index 0 is an answer and not a miss")
check(api.interface_index("TenGigabitEthernet1/0/0") is None,
      "a name in no row is None")
check(dumped and dumped[0][1] == "sw_interface_dump"
      and json.loads(dumped[0][2])["sw_if_index"] == 0xFFFFFFFF,
      f"asked as a dump of every interface: {dumped[:1]}")
api = vppapi.Vat2(runner=fake({"vat2 sw_interface_dump": (-11, "", "")}))
check(api.interface_index("local0") is None,
      "a dump that failed is None, never a guess at an index")

# Vat2._keep_segment_reachable: the API segment's group put back after a
# root client attached, over a file of this suite's own, with chown and
# chmod recorded rather than done - the one thing this may not touch is
# the segment on the machine running it.
seg_dir = tempfile.mkdtemp(prefix="hexash-vppapi-")
seg = os.path.join(seg_dir, "global_vm")
open(seg, "w").close()
gid = os.stat(seg).st_gid
own_group = grp.getgrgid(gid).gr_name


class Segment(vppapi.Vat2):
    API_SEGMENT = seg
    API_GROUP = own_group


class NoGroup(Segment):
    API_GROUP = "hexa-no-such-group-390"


touched = []
_real = (vppapi.os.geteuid, vppapi.os.chown, vppapi.os.chmod)
try:
    vppapi.os.chown = lambda p, u, g: touched.append(("chown", p, u, g))
    vppapi.os.chmod = lambda p, m: touched.append(("chmod", p, m))
    _real[2](seg, 0o644)
    vppapi.os.geteuid = lambda: 0
    Segment._keep_segment_reachable()
    check(touched == [("chown", seg, 0, gid), ("chmod", seg, 0o660)],
          f"root, segment 0644: chown to root:{own_group} and chmod 0660: "
          f"{touched}")
    del touched[:]
    vppapi.os.geteuid = lambda: 1000
    Segment._keep_segment_reachable()
    check(touched == [], f"not root: nothing this can fix, nothing touched: "
                         f"{touched}")
    del touched[:]
    vppapi.os.geteuid = lambda: 0
    _real[2](seg, 0o660)
    Segment._keep_segment_reachable()
    check(touched == [], f"already {own_group} 0660: left alone: {touched}")
    _real[2](seg, 0o644)
    NoGroup._keep_segment_reachable()
    check(touched == [], f"a box without the group: silent, nothing touched: "
                         f"{touched}")
finally:
    vppapi.os.geteuid, vppapi.os.chown, vppapi.os.chmod = _real
print(f"  segment: {seg} as {own_group}")

# Vat2._subprocess: the three shapes a process comes back in - its status
# with stdout and stderr apart, a binary that is not there as 127, and a
# hang as 124 - driven with the interpreter itself so no real client runs.
# Through Segment, so the finally clause reaches the temporary segment above
# and not /dev/shm.
rc, out, err = Segment._subprocess(["/nonexistent/hexa-vat2-390", "--dump-apis"])
check(rc == 127 and out == "" and "not found" in err,
      f"a binary that is not there is 127 and says so: {(rc, out, err)}")
rc, out, err = Segment._subprocess([
    sys.executable, "-c",
    "import sys; sys.stdout.write('{\"retval\": 0}'); "
    "sys.stderr.write('warn'); sys.exit(3)"])
check((rc, out, err) == (3, '{"retval": 0}', "warn"),
      f"status, stdout and stderr come back apart: {(rc, out, err)}")
rc, out, err = Segment._subprocess(
    [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
check(rc == 124 and "timed out" in err,
      f"a hang is 124 after the timeout, not a hung commit: {(rc, err)}")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
