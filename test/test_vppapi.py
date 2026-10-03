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
import tempfile
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "prismash"))
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
seg_dir = tempfile.mkdtemp(prefix="prismash-vppapi-")
seg = os.path.join(seg_dir, "global_vm")
open(seg, "w").close()
gid = os.stat(seg).st_gid
own_group = grp.getgrgid(gid).gr_name


class Segment(vppapi.Vat2):
    API_SEGMENT = seg
    API_GROUP = own_group


class NoGroup(Segment):
    API_GROUP = "prisma-no-such-group-390"


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
rc, out, err = Segment._subprocess(["/nonexistent/prisma-vat2-390", "--dump-apis"])
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

print("\n=== cached_dumps: one vat2 per dump asked, until anything is sent (#520) ===")
_n = []


def _counting(argv, timeout=10):
    _n.append(argv[1])
    if argv[1] == "sw_interface_dump":
        return (0, '[{"sw_if_index": 1, "interface_name": "pg0"}]', "")
    if argv[1] == "ip_table_dump":
        return (1, "", "connect: no such file")
    return (0, '{"retval": 0}', "")


api = vppapi.Vat2(runner=_counting)
with api.cached_dumps():
    a = api.interface_index("pg0")
    b = api.interface_index("pg0")
    c = api.dump("sw_interface_dump", {"sw_if_index": 0xFFFFFFFF,
                                       "name_filter": "",
                                       "name_filter_valid": False})
check(a == b == 1 and _n.count("sw_interface_dump") == 1 and c.ok,
      f"the same dump, asked three ways, is one vat2: {_n}")
_n.clear()
with api.cached_dumps():
    api.interface_index("pg0")
    api.call("sw_interface_set_flags", {"sw_if_index": 1, "flags": 1})
    api.interface_index("pg0")
check(_n.count("sw_interface_dump") == 2,
      f"anything sent in between and the next dump asks again: {_n}")
_n.clear()
with api.cached_dumps():
    api.dump("ip_table_dump", {})
    api.dump("ip_table_dump", {})
check(_n.count("ip_table_dump") == 2, "a failure is not kept: asked again")
_n.clear()
with api.cached_dumps():
    with api.cached_dumps():
        api.interface_index("pg0")
    api.interface_index("pg0")
check(_n.count("sw_interface_dump") == 1,
      "a nested block shares the outer one's answers, and does not end them")
_n.clear()
api.interface_index("pg0")
api.interface_index("pg0")
check(_n.count("sw_interface_dump") == 2 and api._dumps is None,
      "outside a block every dump is its own vat2, as it always was")
_n.clear()
r = api._dump("sw_interface_dump", {}, 15)
check(r.ok and r.fields["rows"][0]["interface_name"] == "pg0" and len(_n) == 1,
      "_dump is the one uncached vat2")
check(vppapi.is_question("sw_interface_get_table")
      and vppapi.is_question("lcp_osi_proto_get")
      and not vppapi.is_question("sw_interface_set_table")
      and not vppapi.is_question("bpa_pool_add_del"),
      "a *_get only asks; anything else may change what the dataplane says")
_n.clear()
with api.cached_dumps():
    api.interface_index("pg0")
    api.call("sw_interface_get_table", {"sw_if_index": 1, "is_ipv6": False})
    api.call("sw_interface_get_table", {"sw_if_index": 1, "is_ipv6": False})
    api.call("sw_interface_get_table", {"sw_if_index": 2, "is_ipv6": False})
    api.interface_index("pg0")
check(_n.count("sw_interface_get_table") == 2
      and _n.count("sw_interface_dump") == 1,
      f"a question is answered once per argument, and forgets nothing: {_n}")
_n.clear()
r = api._call("sw_interface_get_table", {"sw_if_index": 1, "is_ipv6": False})
check(r.ok and len(_n) == 1, "_call is the one uncached vat2 for a message")


print("\n=== one connection: vpp_papi behind Vat2's interface (#527) ===")
import enum
import types as _types


class _Mode(enum.IntEnum):
    URPF_API_MODE_OFF = 0
    URPF_API_MODE_STRICT = 2


class _Flag(enum.IntFlag):
    IF_STATUS_API_FLAG_ADMIN_UP = 1
    IF_STATUS_API_FLAG_LINK_UP = 2


_TYPES = {
    "vl_api_urpf_mode_t": _types.SimpleNamespace(enum=_Mode),
    "vl_api_if_status_flags_t": _types.SimpleNamespace(enum=_Flag),
    "vl_api_table_t": _types.SimpleNamespace(
        msgdef=[["u32", "table_id"], ["vl_api_urpf_mode_t", "mode"]]),
}
# encode(): names become numbers by the field's declared type.
check(vppapi.encode("urpf_update",
                    {"mode": "URPF_API_MODE_STRICT", "sw_if_index": 3,
                     "prefix": "10.0.0.1/24"},
                    {"mode": "vl_api_urpf_mode_t", "sw_if_index": "u32",
                     "prefix": "vl_api_prefix_t"}, _TYPES)
      == {"mode": 2, "sw_if_index": 3, "prefix": "10.0.0.1/24"},
      "an enum name packs as its number; everything else as written")
check(vppapi.encode("x", {"flags": ["IF_STATUS_API_FLAG_ADMIN_UP",
                                    "IF_STATUS_API_FLAG_LINK_UP"]},
                    {"flags": "vl_api_if_status_flags_t"}, _TYPES)
      == {"flags": 3}, "a list of flag names is their OR")
check(vppapi.encode("x", {"t": {"table_id": 7, "mode": "URPF_API_MODE_OFF"},
                          "ms": ["URPF_API_MODE_STRICT"]},
                    {"t": "vl_api_table_t", "ms": "vl_api_urpf_mode_t"},
                    _TYPES)
      == {"t": {"table_id": 7, "mode": 0}, "ms": 2},
      "a struct is followed into its declaration; a list of one enum ORs")
try:
    vppapi.encode("x", {"mode": "NOT_A_MODE"}, {"mode": "vl_api_urpf_mode_t"},
                  _TYPES)
    check(False, "an unknown enum name must not pack")
except KeyError:
    pass

# plain(): what vpp_papi answers, as vat2's JSON.
import collections
import ipaddress
# rename=True, as vpp_papi builds them: that is where its `_0` comes from -
# the renamed `_vl_msg_id`.
_Details = collections.namedtuple("sw_interface_details",
                                  "_vl_msg_id context sw_if_index flags "
                                  "l2_address interface_name mtu link_speed "
                                  "ok addr", rename=True)


class _Mac:
    def __str__(self):
        return "de:ad:00:00:00:01"


_row = vppapi.plain(_Details(820, 1, 1, _Flag(3), _Mac(), "loop0",
                             [9000, 0, 0, 0], 1.5, True,
                             ipaddress.ip_address("10.9.0.1")))
check(_row == {"_msgname": "sw_interface_details", "sw_if_index": 1,
               "flags": "IF_STATUS_API_FLAG_ADMIN_UP|IF_STATUS_API_FLAG_LINK_UP",
               "l2_address": "de:ad:00:00:00:01", "interface_name": "loop0",
               "mtu": [9000, 0, 0, 0], "link_speed": 1.5, "ok": True,
               "addr": "10.9.0.1"},
      f"a reply as vat2 printed it, without _0 and context: {_row}")
_Table = collections.namedtuple("vl_api_ip_table_t", "table_id name")
_TD = collections.namedtuple("ip_table_details", "_vl_msg_id context table",
                             rename=True)
check(vppapi.plain(_TD(1, 2, _Table(7, "ipv4-VRF:7")))
      == {"_msgname": "ip_table_details",
          "table": {"table_id": 7, "name": "ipv4-VRF:7"}},
      "a struct inside a message carries no _msgname, as vat2 prints it")
check(vppapi.plain(_Mode(2)) == "URPF_API_MODE_STRICT"
      and vppapi.plain(b"\x01\x02") == [1, 2] and vppapi.plain(None) is None,
      "an enum by name, bytes as numbers, None as it is")


_Reply = collections.namedtuple("urpf_update_reply",
                                "_vl_msg_id context retval", rename=True)


class _Conn:
    """A stand-in VPPApiClient: counts connects, answers from a table."""
    connects = 0
    fail_connect = False

    def __init__(self, apifiles, use_socket, server_address, read_timeout):
        self.apifiles = apifiles
        self.server = server_address
        self.sent = []
        self.die_once = set()
        self.api = _types.SimpleNamespace()
        self.messages = {}
        for name, answer in _Conn.table.items():
            self.messages[name] = _types.SimpleNamespace(
                fields=["_vl_msg_id", "context", "mode", "sw_if_index"],
                fieldtypes=["u16", "u32", "vl_api_urpf_mode_t", "u32"])
            setattr(self.api, name, self._method(name, answer))

    def _method(self, name, answer):
        def call(**kw):
            if name in self.die_once:
                self.die_once.discard(name)
                raise OSError("Broken pipe")
            self.sent.append((name, kw))
            if isinstance(answer, Exception):
                raise answer
            return answer
        return call

    def connect(self, name):
        _Conn.connects += 1
        if _Conn.fail_connect:
            raise OSError("No such file or directory")
        self.name = name

    def disconnect(self):
        self.closed = True


_Conn.table = {
    "urpf_update": _Reply(1, 2, 0),
    "bpa_pool_add_del": _Reply(1, 2, -7),
    "sw_interface_dump": [_Details(820, 1, 1, _Flag(1), _Mac(), "loop0",
                                   [1500, 0, 0, 0], 0.0, True,
                                   ipaddress.ip_address("10.9.0.1"))],
    "create_subif": ValueError("bad value"),
}
_fake = _types.SimpleNamespace(VPPApiClient=_Conn,
                               vpp_serializer=_types.SimpleNamespace(types=_TYPES))
with tempfile.TemporaryDirectory() as _d:
    os.makedirs(os.path.join(_d, "core"))
    os.makedirs(os.path.join(_d, "plugins"))
    for _f in ("core/interface.api.json", "plugins/bpa.api.json",
               "plugins/acl.api.json"):
        open(os.path.join(_d, _f), "w").close()
    _vat = []

    def _vrun(argv, timeout=10):
        _vat.append(argv[1])
        return (0, '{"retval": 0}', "")

    p = vppapi.Papi(papi=_fake, apidir=_d, runner=_vrun,
                    identity=lambda: ("alice", 1000))
    check(p.transport == "vpp_papi", "vpp_papi until something says otherwise")
    r = p.call("urpf_update", {"sw_if_index": 3, "mode": "URPF_API_MODE_STRICT"})
    c = p._conn
    check(sorted(os.path.basename(f) for f in c.apifiles)
          == ["bpa.api.json", "interface.api.json"],
          f"VPP's core definitions and the plugins prismash sends to, not every "
          f"plugin's: {c.apifiles}")
    check(r.ok and c.sent == [("urpf_update", {"sw_if_index": 3, "mode": 2})]
          and not _vat, f"one call over the connection, the name packed: {c.sent}")
    p.call("urpf_update", {"sw_if_index": 4, "mode": "URPF_API_MODE_OFF"})
    check(_Conn.connects == 1 and p._conn is c,
          "a second message reuses the connection")
    r = p.call("bpa_pool_add_del", {"name": "p"})
    check(r.state == vppapi.REFUSED and r.retval == -7
          and "invalid value" in r.reason
          and c.sent[-1][1].get("audit_user") == "alice",
          f"a refusal named, a write carrying who made it: {r.reason}")
    r = p.dump("sw_interface_dump", {"sw_if_index": 0xFFFFFFFF})
    check(r.ok and r.fields["rows"][0]["interface_name"] == "loop0"
          and p.interface_index("loop0") == 1,
          "a dump's rows as vat2's, and interface_index() through them")
    _vat.clear()
    r = p.call("no_such_message", {})
    check(r.ok and _vat == ["no_such_message"] and p._defined("urpf_update")
          and not p._defined("no_such_message"),
          "a message the definitions lack goes over vat2, not NO_PLUGIN")
    p._conn.messages["lcp_osi_proto_get"] = p._conn.messages["urpf_update"]
    r = p.call("lcp_osi_proto_get", {})
    check(r.state == vppapi.NO_PLUGIN,
          f"defined, and VPP does not list it: the plugin is missing: {r.reason}")
    r = p.call("urpf_update", {"mode": "NOT_A_MODE"})
    check(r.state == vppapi.CANNOT_SEND, f"a name that is no member: {r.reason}")
    r = p.call("create_subif", {"sub_if_flags": "A"})
    check(r.state == vppapi.CANNOT_SEND, f"a value the packer refuses: {r.reason}")
    r = p.call("create_subif", {"sub_if_flags": "A|B"})
    check(r.state == vppapi.CANNOT_SEND and "ONE flag" in r.reason,
          "unsendable() still speaks first")
    # A dead socket: a question is asked again on a new connection, a write
    # is not re-sent.
    before = _Conn.connects
    p._conn.die_once.add("sw_interface_dump")
    r = p._dump("sw_interface_dump", {}, 15)
    check(r.ok and _Conn.connects == before + 1,
          "a question on a dead socket is asked again on a new connection")
    p._conn.die_once.add("urpf_update")
    r = p.call("urpf_update", {"mode": "URPF_API_MODE_OFF", "sw_if_index": 1})
    check(r.state == vppapi.NOT_RUNNING and "lost" in r.reason,
          f"a write on a dead socket is not re-sent: {r.reason}")
    failed, answer = p._send("urpf_update", {"mode": 0}, True)
    check(failed is None and answer.retval == 0, "_send's answer, unconverted")
    st, msg = p.available()
    check(st == vppapi.OK, f"available() over the connection: {msg}")
    delattr(p._conn.api, "bpa_pool_add_del")
    st, msg = p.available()
    check(st == vppapi.NO_PLUGIN, f"no bpa API over the connection: {msg}")
    del p._conn.messages["bpa_pool_add_del"]
    _vat.clear()
    p.available()
    check(_vat[:1] == ["--dump-apis"],
          "no bpa definitions loaded: vat2 answers available()")
    last = p._conn
    p.close()
    check(p._conn is None and last.closed,
          "close() disconnects, next message reconnects")

    # The fallbacks, each saying why.
    _vat.clear()
    q = vppapi.Papi(papi=_fake, apidir=os.path.join(_d, "none"), runner=_vrun)
    r = q.call("urpf_update", {"mode": "URPF_API_MODE_OFF"})
    check(r.ok and _vat == ["urpf_update"] and q.transport == "vat2"
          and "no API definitions" in q.why_vat2,
          f"no definitions: vat2, and why: {q.why_vat2}")
    _Conn.fail_connect = True
    q = vppapi.Papi(papi=_fake, apidir=_d, runner=_vrun)
    q.dump("sw_interface_dump", {})
    check(q.transport == "vat2" and "No such file" in q.why_vat2
          and q._connect() is None,
          f"no socket: vat2, and why: {q.why_vat2}")
    _Conn.fail_connect = False
    q = vppapi.Papi(apidir=_d, runner=_vrun)
    q._papi = None
    import builtins
    _real_import = builtins.__import__

    def _no_papi(name, *a, **kw):
        if name == "vpp_papi":
            raise ImportError(name)
        return _real_import(name, *a, **kw)
    builtins.__import__ = _no_papi
    try:
        check(q._module() is None and "python3-vpp-api" in q.why_vat2,
              "no library: vat2, and why")
    finally:
        builtins.__import__ = _real_import
    _old = vppapi.FORCE_VAT2
    vppapi.FORCE_VAT2 = True
    check(vppapi.Papi(papi=_fake, apidir=_d).transport == "vat2",
          "PRISMA_API=vat2 keeps every message on vat2")
    vppapi.FORCE_VAT2 = _old
    check(isinstance(vppapi.client(), vppapi.Papi), "client() is the Papi")

# forget_dumps(): the block's answers end now.
_n = []
api = vppapi.Vat2(runner=_counting)
with api.cached_dumps():
    api.interface_index("pg0")
    api.forget_dumps()
    api.interface_index("pg0")
    api.interface_index("pg0")
check(_n.count("sw_interface_dump") == 3 and api._dumps is None,
      f"after forget_dumps() every dump asks: {_n}")

print("\n=== the definitions the VTY uses, pruned and kept (#529) ===")
check(vppapi._bare("vl_api_address_t") == "address"
      and vppapi._bare("u32") == "u32",
      "a type as the definitions key it")
_MEM = {"module": "memclnt", "messages": [
    ["sockclnt_create", ["u16", "_vl_msg_id"]],
    ["control_ping", ["u16", "_vl_msg_id"]],
    ["control_ping_reply", ["u16", "_vl_msg_id"]]], "services": {}}
_IFACE = {"module": "interface",
          "enums": [["address_family", ["ADDRESS_IP4", 0], {"enumtype": "u8"}],
                    ["if_status_flags", ["UP", 1], {"enumtype": "u32"}],
                    ["unused_enum", ["X", 0], {"enumtype": "u8"}]],
          "types": [["address", ["vl_api_address_family_t", "af"],
                     ["vl_api_address_union_t", "un"]],
                    ["unused_type", ["u8", "x"]]],
          "unions": [["address_union", ["vl_api_ip4_address_t", "ip4"]]],
          "aliases": {"ip4_address": {"type": "u8", "length": 4},
                      "interface_index": {"type": "u32"},
                      "unused_alias": {"type": "u8"}},
          "messages": [
              ["sw_interface_dump", ["u16", "_vl_msg_id"],
               ["vl_api_interface_index_t", "sw_if_index"]],
              ["sw_interface_details", ["u16", "_vl_msg_id"],
               ["vl_api_address_t", "addr"],
               ["vl_api_if_status_flags_t", "flags"]],
              ["not_asked", ["u16", "_vl_msg_id"],
               ["vl_api_unused_type_t", "x"]]],
          "services": {"sw_interface_dump": {"reply": "sw_interface_details",
                                             "stream": True},
                       "not_asked": {"reply": "not_asked_reply"}}}
_pr = vppapi.prune_definitions([_MEM, _IFACE],
                               {"sw_interface_dump", "no_such_request"})
_names = sorted(m[0] for m in _pr["messages"])
check(_names == ["control_ping", "control_ping_reply", "sockclnt_create",
                 "sw_interface_details", "sw_interface_dump"],
      f"the request, its details, memclnt whole, and nothing else: {_names}")
check([t[0] for t in _pr["types"]] == ["address"]
      and [t[0] for t in _pr["unions"]] == ["address_union"]
      and sorted(t[0] for t in _pr["enums"]) == ["address_family",
                                                 "if_status_flags"]
      and sorted(_pr["aliases"]) == ["interface_index", "ip4_address"],
      f"every type they reach, through types, unions and aliases, and no "
      f"other: {_pr}")
check(list(_pr["services"]) == ["sw_interface_dump"],
      "the services of the messages kept")
with tempfile.TemporaryDirectory() as _d:
    _files = []
    for _n2, _api in (("memclnt.api.json", _MEM), ("interface.api.json", _IFACE)):
        _files.append(os.path.join(_d, _n2))
        with open(_files[-1], "w") as _fh:
            json.dump(_api, _fh)
    _cd = os.path.join(_d, "cache")
    os.makedirs(_cd, mode=0o700)
    _p1 = vppapi.definitions_file(_files, {"sw_interface_dump"}, cachedir=_cd)
    with open(_p1) as _fh:
        _k1 = json.load(_fh)
    check(sorted(m[0] for m in _k1["messages"]) == _names,
          "made once, into the account's own directory")
    check(vppapi.definitions_file(_files, {"sw_interface_dump"},
                                  cachedir=_cd) == _p1,
          "and read back under the same name while nothing changed")
    with open(_files[1], "w") as _fh:
        json.dump(dict(_IFACE, services={}), _fh)
    _p2 = vppapi.definitions_file(_files, {"sw_interface_dump"}, cachedir=_cd)
    check(_p2 != _p1,
          "a definition rewritten by an upgrade is a new name, never the old file")
    open(_files[1], "w").write("{not json")
    check(vppapi.definitions_file(_files, {"sw_interface_dump"},
                                  cachedir=_cd) is None,
          "an unreadable definition is no file: vpp_papi gets the files themselves")
    check(vppapi.definitions_file([os.path.join(_d, "gone.api.json")],
                                  cachedir=_cd) is None,
          "and so is a file that is not there")
    _old_x = os.environ.get("XDG_RUNTIME_DIR")
    _open = os.path.join(_d, "open")
    os.makedirs(_open, mode=0o777)
    os.chmod(_open, 0o777)
    os.environ["XDG_RUNTIME_DIR"] = _open
    _got = vppapi._cache_dir()
    check(_got != _open,
          f"a directory other accounts can write is never used: {_got}")
    os.environ["XDG_RUNTIME_DIR"] = _cd
    check(vppapi._cache_dir() == _cd, "the account's own 0700 one is")
    if _old_x is None:
        del os.environ["XDG_RUNTIME_DIR"]
    else:
        os.environ["XDG_RUNTIME_DIR"] = _old_x

print("\n=== the CLI over the same connection (#529) ===")
check(vppapi.cli_is_question("show bpa pool")
      and not vppapi.cli_is_question("bpa pool nb del")
      and not vppapi.cli_is_question("clear errors"),
      "a `show` only reads; any other line may write")
_CliReply = collections.namedtuple("cli_inband_reply", "retval reply")


class _CliConn:
    """A stand-in connection answering cli_inband, and counting what it sent."""

    def __init__(self, dead=False):
        self.sent = []
        self.dead = dead
        self.messages = {"cli_inband": object()}
        self.api = _types.SimpleNamespace(cli_inband=self._cli)

    def _cli(self, cmd):
        if self.dead:
            raise OSError("Broken pipe")
        self.sent.append(cmd)
        return _CliReply(0, f"out of {cmd}\n")


_cp = vppapi.Papi(papi=_fake, apidir="/nonexistent")
_cp._conn = _CliConn()
_cp._papi = _types.SimpleNamespace(vpp_serializer=_types.SimpleNamespace(types={}))
_cp._conn.messages["cli_inband"] = _types.SimpleNamespace(
    fields=["_vl_msg_id", "context", "cmd"], fieldtypes=["u16", "u32", "string"])
check(_cp.cli("show interface") == (True, "out of show interface\n"),
      "a screen comes back as the text vppctl prints")
with _cp.cached_dumps():
    _cp.cli("show bpa pool")
    _cp.cli("show bpa pool")
    _cp.remember("running_config", "cfg")
    check(_cp.recall("running_config") == "cfg",
          "what a reader built is recalled inside the block")
    _cp.cli("bpa pool nb del")
    check(_cp.recall("running_config") is None,
          "and forgotten at a line that writes")
    _cp.cli("show bpa pool")
check(_cp._conn.sent.count("show bpa pool") == 2
      and "bpa pool nb del" in _cp._conn.sent,
      f"a screen asked twice in a block is one message, and asked again after "
      f"a write: {_cp._conn.sent}")
check(_cp.recall("running_config") is None and _cp.remember("k", 1) is None
      and _cp.recall("k") is None,
      "outside a block nothing is kept or recalled")
_cp._conn = _CliConn(dead=True)
_cp._conn.messages["cli_inband"] = _types.SimpleNamespace(
    fields=["_vl_msg_id", "context", "cmd"], fieldtypes=["u16", "u32", "string"])
_cp._connect = lambda: _cp._conn
check(_cp.cli("show version") is None,
      "a question lost with the socket goes to vppctl, which can only read")
_dead = _CliConn(dead=True)
_dead.messages["cli_inband"] = _cp._conn.messages["cli_inband"] if _cp._conn else \
    _types.SimpleNamespace(fields=["_vl_msg_id", "context", "cmd"],
                           fieldtypes=["u16", "u32", "string"])
_cp._conn = _dead
_cp._connect = lambda: _cp._conn if _cp._conn is not None else _dead
got = _cp.cli("bpa pool nb del")
check(got is not None and got[0] is False and "lost" in got[1],
      f"a write lost with the socket is not handed to vppctl to send again: {got}")
_nv = vppapi.Papi(papi=None, apidir="/nonexistent")
_nv.why_vat2 = "PRISMA_API=vat2"
check(_nv.cli("show version") is None,
      "with no connection, None: the caller runs vppctl as it always did")

print("\n=== a papi string ends at its first NUL, as vat2 printed it ===")
import collections as _c
_Pair = _c.namedtuple("lcp_itf_pair_details", "phy_sw_if_index host_if_name netns")
_r = vppapi.plain(_Pair(1, "enp6s18\x00", "\x00\x00"))
check(_r["host_if_name"] == "enp6s18" and _r["netns"] == "",
      f"a fixed-size field's padding is not part of the name: {_r}")
check(vppapi.plain("a\x00b") == "a" and vppapi.plain("plain") == "plain",
      "everything after the first NUL goes, and a string without one is untouched")

print("\n=== a screen over cli_inband carries no NUL, as vppctl's carries none ===")
class _Ans:
    reply = "itf-pair: [0] enp6s18\x00 tap4096 enp6s18\x00 10 type tap\n"
_pc = vppapi.Papi.__new__(vppapi.Papi)
_pc._dumps = None
_pc._defined = lambda m: True
_pc._send = lambda m, a, q: (None, _Ans())
check(_pc.cli("show lcp") == (True, "itf-pair: [0] enp6s18 tap4096 enp6s18 10 type tap\n"),
      f"the terminator VPP prints inside a row is not part of the screen: {_pc.cli('show lcp')!r}")

print("\n=== vpp_papi's teardown message, and only it, is dropped ===")
import logging as _lg
vppapi.quiet_papi_teardown(); vppapi.quiet_papi_teardown()
_root = _lg.getLogger()
check(sum(isinstance(f, vppapi._PapiTeardown) for f in _root.filters) == 1,
      "the filter is put on the root logger once, however often it is asked")
_rec = lambda m: _lg.LogRecord("root", _lg.ERROR, __file__, 1, m, None, None)
check(_root.filter(_rec("select failed")) is False
      and _root.filter(_rec("socket gone")) is True,
      "\"select failed\" is dropped and any other record still reaches stderr")
check(vppapi._PapiTeardown().filter(_rec("select failed")) is False,
      "_PapiTeardown.filter answers the teardown record")



print("=== an account that cannot reach the API segment is answered at once (#561) ===")
# vat2 waited out its connect per dump: 90.6 s for one running-config read as an
# account with no role, against 0.5 s for root (lab/mtu-and-readonly.sh).
import tempfile as _tf
import time as _time
_seg = _tf.NamedTemporaryFile(delete=False)
_seg.close()
os.chmod(_seg.name, 0o000)
_saved = vppapi.Vat2.API_SEGMENT
vppapi.Vat2.API_SEGMENT = _seg.name
try:
    if os.geteuid() != 0:
        why = vppapi.Vat2.segment_denied()
        check(why and "cannot reach the dataplane's API" in why,
              f"segment_denied names the segment and its group: {why!r}")
        t0 = _time.monotonic()
        rc, out, err = vppapi.Vat2._subprocess(["vat2", "sw_interface_dump", "{}"],
                                               timeout=30)
        took = _time.monotonic() - t0
        check(rc == 126 and took < 1,
              f"_subprocess answers at once instead of running vat2: {rc} {took:.2f}s")
        r = vppapi.Vat2._client_failure("sw_interface_dump", rc, err)
        check(r is not None and r.state == vppapi.CANNOT_SEND,
              f"and the reply says the client could not send it: {r}")
        rc, _, _ = vppapi.Vat2._subprocess(["vat2", "--dump-apis"], timeout=5)
        check(rc != 126, "--dump-apis reads files, not the segment, and still runs")
    os.chmod(_seg.name, 0o600)
    check(vppapi.Vat2.segment_denied() is None,
          "an account that can open the segment is not stopped")
    vppapi.Vat2.API_SEGMENT = _seg.name + ".absent"
    check(vppapi.Vat2.segment_denied() is None,
          "no segment (the dataplane down) is left to vat2 to say")
finally:
    vppapi.Vat2.API_SEGMENT = _saved
    os.unlink(_seg.name)

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
