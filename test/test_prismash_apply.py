#!/usr/bin/env python3
"""
What commit does when the dataplane refuses at apply time.

The lab proves validation catches a bad pool. It cannot prove this: validation
runs first, so a pool that fails it never reaches the apply path. The case that
matters here is the one validation does not catch - the dataplane accepting a
check and refusing the write - because that is precisely where the old code
reported success while holding none of the configuration.

The API is stubbed, so each outcome is produced on demand rather than raced
for.
"""
import contextlib
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "prismash"))
import config      # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nat_fixture import parse_nested      # noqa: E402
import prismash      # noqa: E402
import routes      # noqa: E402
import system      # noqa: E402
import vppapi      # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


class FakeApi:
    """Answers OK for everything except the pools named in `refuse`."""

    def __init__(self, refuse=(), state=vppapi.OK):
        self.refuse = set(refuse)
        self.state = state
        self.calls = []

    def available(self):
        return (self.state, "stub")

    def call(self, message, args):
        self.calls.append((message, args))
        if args.get("name") in self.refuse and args.get("is_add"):
            return vppapi.Reply(vppapi.REFUSED, retval=-7,
                                reason="the dataplane refused it: invalid value")
        return vppapi.Reply(vppapi.OK)

    def dump(self, message, args):
        # The named-FIB-table map (#268). An empty answer is a real one: the
        # boxes here have no named table, and it is what a VTY that cannot
        # reach the API gets too.
        return vppapi.Reply(vppapi.OK, fields={"rows": []})


class FakeVpp(prismash.Vpp):
    """A dataplane that accepts every CLI command, including validation.

    Derived from prismash.Vpp, not free-standing, and #293 is the third time in
    this repository that mattered. A refusal that came back from the dataplane
    now reads the loaded plugin's build before it says anything about an
    upgrade, this stub listed the methods it had, and every suite here died of
    AttributeError before reaching a verdict. `_version272` in
    test_prismash_complete.py makes the same argument over `plugin_build` and
    `_Frr271` above it over `daemons()`: a stub that enumerates the product's
    methods stops having whatever the product adds next, and both sides of
    that merge pass on their own.

    `run()` answering nothing means `plugin_build()` reads no build, so these
    suites drive the path where there is nothing to compare - which is right
    for them: what they are about is the refusal, not the upgrade window.
    """

    TRACE = False

    def __init__(self):
        self.commands = []

    def run(self, cmd):
        self.commands.append(cmd)
        return 0, ""

    def running(self):
        return 0, ""


def shell_with(api, running_text="", caps=None):
    """
    A Shell with the capability it is being tested under stated OUTRIGHT.

    Shell() reads the invoking account's real group membership (#35). Left
    alone, every apply below is refused before it reaches the code this file is
    about, and the result is a suite that fails on any developer machine whose
    account is not root - which is what happened between v0.15.0 and here.

    Stating it also makes the grant visible: `caps=set()` below asserts the
    gate still refuses, so handing out capabilities here cannot quietly turn a
    real check off.
    """
    sh = prismash.Shell()
    sh.caps = {prismash.CAP_ALL, prismash.CAP_CGNAT} if caps is None else caps
    sh.api = api
    sh.dp = FakeVpp()
    # Based on the real Frr so that daemons() is the product's own, asking
    # through show(): a stub that answered daemons() directly would let the
    # commit check pass over a method that never ran.
    sh.frr = type("F", (prismash.Frr,), {"available": lambda self: False,
                            "show": lambda self, c: (1, ""),
                            "reload": lambda self, l, check_only=False: (0, "")})()
    sh._running_text = running_text
    sh.running_config = lambda: parse_nested(running_text)
    return sh


def target_with(name, inside, outside, block="512"):
    cfg = config.Config()
    p = config.Pool(name)
    p.inside = [inside]
    p.outside = [outside]
    p.block_size = block
    cfg.pools[name] = p
    return cfg


print("=== the dataplane refuses at apply: commit must fail and name the pool ===")
api = FakeApi(refuse={"doomed"})
sh = shell_with(api)
ok = sh.apply(target_with("doomed", "100.64.0.0/22", "200.150.0.16/30"))
check(ok is False, "apply must report failure when the dataplane refuses")
check(sh.failed > 0, "the failure must be counted, not just returned")
print(f"  apply -> {ok}, errors recorded: {sh.failed}")

print("\n=== a refusal must not be reported as success ===")
# The defect being removed: vppctl exits 0 on failure, so the old path returned
# True here and the operator was told the pool existed.
api = FakeApi(refuse={"doomed"})
sh = shell_with(api)
sh.apply(target_with("doomed", "100.64.0.0/22", "200.150.0.16/30"))
sent = [a["name"] for m, a in api.calls if a.get("is_add")]
check("doomed" in sent, f"the pool must have been attempted: {sent}")
print(f"  attempted: {sent}")

print("\n=== an accepted pool goes through ===")
api = FakeApi()
sh = shell_with(api)
ok = sh.apply(target_with("fine", "100.64.0.0/22", "200.150.0.16/30"))
check(ok is True, "an accepted pool must succeed")
check(any(a.get("name") == "fine" and a.get("is_add") for m, a in api.calls),
      "the pool must reach the API")
print(f"  apply -> {ok}")

print("\n=== an unreachable dataplane fails before anything is applied ===")
# Finding out halfway through leaves the box in neither the old configuration
# nor the new one, with no way to tell which pools made it.
api = FakeApi(state=vppapi.NOT_RUNNING)
sh = shell_with(api)
ok = sh.apply(target_with("fine", "100.64.0.0/22", "200.150.0.16/30"))
check(ok is False, "an unreachable dataplane must fail the commit")
check(api.calls == [], f"nothing may be sent before the check: {api.calls}")
print(f"  apply -> {ok}, calls made: {len(api.calls)}")

print("\n=== commit check sends no write, whatever the outcome ===")
# A check that mutates is not a check. An earlier version deleted and recreated
# the running pool to work around a name check, turning a read-only operation
# into an outage risk.
api = FakeApi()
sh = shell_with(api)
sh.apply(target_with("fine", "100.64.0.0/22", "200.150.0.16/30"), check_only=True)
# Every write message, not one of them. This filtered for bpa_pool_add_del
# alone and then said "write nothing" - so a check that set a bypass list,
# changed a pool's limits or added a port forward sent three mutations and
# reported none. vppapi.WRITE_MESSAGES is the list, and it is the list the
# assertion two sections up already uses.
writes = [m for m, a in api.calls if m in vppapi.WRITE_MESSAGES]
check(writes == [], f"commit check must write nothing, sent: {writes}")
print(f"  calls during check: {len(api.calls)}, of them writes: {len(writes)}")

print("\n=== a read-only account is refused, and nothing is sent ===")
# The other half of stating capabilities above. Without this, granting them in
# shell_with() would be indistinguishable from removing the check entirely -
# every test in this file would pass on a build where anyone can commit.
api = FakeApi()
sh = shell_with(api, caps=set())
ok = sh.apply(target_with("fine", "100.64.0.0/22", "200.150.0.16/30"))
check(ok is False, "a read-only account must not be able to commit")
check(api.calls == [], f"and nothing may reach the dataplane: {api.calls}")
print(f"  apply -> {ok}, calls made: {len(api.calls)}")

print("\n=== a cgnat operator may change a pool ===")
# And the role in between: the capability the change actually needs, not the
# one that happens to allow everything.
api = FakeApi()
sh = shell_with(api, caps={prismash.CAP_CGNAT})
ok = sh.apply(target_with("fine", "100.64.0.0/22", "200.150.0.16/30"))
check(ok is True, "a cgnat operator must be able to change a pool")
print(f"  apply -> {ok}")

print("\n=== a QinQ sub-interface says what it actually matches ===")
# #136: vat2 can only send ONE name of a bitfield, so TWO_TAGS goes without
# EXACT_MATCH - and measurement on vpp 26.06-release showed that sending it
# would change nothing anyway, because eth_input_tag_lookup() reads three or
# more tags as two. Either way the sub-interface accepts frames the operator
# did not ask for, and the only honest thing left is to say so at the moment
# it is created. Silence here is what #131 shipped.
api = FakeApi()
api.interface_index = lambda name: 1
sh = shell_with(api)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ok = sh.create_subif("pg0.100", {"outer": 100, "inner": 900})
said = buf.getvalue()
check(ok is True, "the sub-interface is still created")
check("warning:" in said, f"the operator must be told: {said!r}")
check("pg0.100" in said, f"and which interface it is about: {said!r}")
check("more tags" in said, f"and what it also accepts: {said!r}")
sent = [a for m, a in api.calls if m == "create_subif"]
check(sent and sent[0]["sub_if_flags"] == "SUB_IF_API_FLAG_TWO_TAGS",
      f"one flag name, the only spelling vat2 can send: {sent}")
print("  " + said.strip())

print("\n=== a single-tag sub-interface has nothing to warn about ===")
# It goes through create_vlan_subif, which takes no flags at all, and VPP
# makes it exact-matching: measured, a two-tag frame does not land on one.
# A warning that fires on a working configuration teaches operators to ignore
# warnings.
api = FakeApi()
api.interface_index = lambda name: 1
sh = shell_with(api)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ok = sh.create_subif("pg0.100", {"outer": 100, "inner": None})
check(ok is True, "the sub-interface is created")
check("warning" not in buf.getvalue(),
      f"and nothing is warned about: {buf.getvalue()!r}")
check([m for m, a in api.calls] == ["create_vlan_subif"],
      f"through the message that needs no flags: {api.calls}")
print(f"  {[m for m, a in api.calls]}, no warning")

print("\n=== the flags vat2 cannot send never reach it ===")
# The guard lives in vppapi, but the value that would trip it is chosen here.
# If a later change starts computing TWO_TAGS|EXACT_MATCH, this catches it as
# a refusal instead of as a core dump during somebody's commit.
check(vppapi.unsendable("create_subif", {"sub_if_flags": 20}) != "",
      "an integer bitfield must be refused")
check(vppapi.unsendable(
    "create_subif",
    {"sub_if_flags": "SUB_IF_API_FLAG_TWO_TAGS"}) == "",
    "the spelling prismash uses must not be")
print("  integer refused, single name allowed")

# ------------------------------------------------------- accounts and roles
#
# The other end of the same authorization: the checks above ask what an account
# may do, and these ask whether the account the VTY created is in the groups
# that decide it. It created every account in NO group, so `capabilities()`
# came back empty for all of them and the role the operator granted did not
# exist anywhere but in the configuration file (#184).
#
# Run against a fake root and a runner that records argv rather than a real
# box: the assertion is which memberships are written and which are taken
# away, and useradd/gpasswd on the machine running the suite would be a real
# account nobody asked for.


class FakeBox:
    """A /etc/passwd, /etc/group and /etc/shadow, plus the commands run on them."""

    def __init__(self, tmp, passwd="", group="", refuse=()):
        self.dir = tmp
        self.calls = []
        self.refuse = set(refuse)
        os.makedirs(os.path.join(tmp, "etc"), exist_ok=True)
        for name, text in (("passwd", passwd), ("group", group),
                           ("shadow", "")):
            with open(os.path.join(tmp, "etc", name), "w") as f:
                f.write(text)

    def run(self, argv, stdin=None, timeout=30):
        self.calls.append(argv)
        if argv[0] == "gpasswd" and argv[-1] in self.refuse:
            return 3, f"gpasswd: group '{argv[-1]}' does not exist"
        return 0, ""

    def system(self):
        return system.System(runner=self.run, root=self.dir,
                             shell="/usr/bin/prismash-login")

    def groups(self, name):
        """The memberships the recorded gpasswd calls would have produced."""
        have = set()
        for g in system.MANAGED_GROUPS:
            if ["gpasswd", "-a", name, g] in self.calls:
                have.add(g)
            if ["gpasswd", "-d", name, g] in self.calls:
                have.discard(g)
        return have


PASSWD = ("root:x:0:0::/root:/bin/bash\n"
          "noc:x:1000:1000::/home/noc:/usr/bin/prismash-login\n")
GROUPS_NONE = "vpp:x:900:\nfrr:x:901:\nfrrvty:x:902:\n" \
              "prisma-admin:x:903:\nprisma-cgnat:x:904:\nnoc:x:1000:\n"

print("\n=== the role is read back from the box, not remembered ===")
# #161's lesson. A role kept in the saved configuration goes on being reported
# after somebody removes the membership by hand, and the VTY ends up describing
# an account it no longer describes.
tmp = tempfile.mkdtemp()
box = FakeBox(tmp, PASSWD, GROUPS_NONE.replace("prisma-admin:x:903:",
                                               "prisma-admin:x:903:noc"))
u = box.system().read_users()["noc"]
check(u.role == system.ROLE_ADMIN,
      f"membership of prisma-admin must read back as a role: {u.role!r}")
check(" role administrator" in u.lines(),
      f"and appear in the running configuration: {u.lines()}")
print(f"  {u.lines()}")

# The primary group is not in any member list, and an account whose primary
# group is a role group holds that role as far as the system is concerned - so
# a reader that only looked at the member lists would report read-only for an
# account the box lets configure.
box = FakeBox(tmp, PASSWD.replace("noc:x:1000:1000", "noc:x:1000:904"),
              GROUPS_NONE)
check(box.system().read_users()["noc"].role == system.ROLE_CGNAT,
      "a role held as the primary group must be read back too")

box = FakeBox(tmp, PASSWD, GROUPS_NONE)
check(box.system().read_users()["noc"].role == "",
      "and an account in neither group is read-only")
check(box.system().read_users()["root"].role == "",
      "root is never given a role: its rights do not come from a group")

print("\n=== the memberships the role is MADE of are written ===")
for role, expect in ((system.ROLE_ADMIN,
                      {"prisma-admin", "prisma-cgnat", "vpp", "frr", "frrvty"}),
                     (system.ROLE_CGNAT,
                      {"prisma-cgnat", "vpp", "frr", "frrvty"}),
                     ("", {"vpp", "frr", "frrvty"})):
    box = FakeBox(tempfile.mkdtemp(), PASSWD, GROUPS_NONE)
    want = system.User("noc")
    want.role = role
    ok, msgs = box.system().apply_users({"noc": want}, {"noc": system.User("noc")})
    check(ok, f"applying role {role!r} must succeed: {msgs}")
    check(box.groups("noc") == expect,
          f"role {role or 'read-only'!r}: {sorted(box.groups('noc'))} "
          f"!= {sorted(expect)}")
    print(f"  {role or 'read-only'}: {' '.join(sorted(box.groups('noc')))}")

# vpp, frr and frrvty are not a role and are not optional. VPP's sockets are
# group vpp and /etc/frr is group frr, so an account outside them is refused by
# the SOCKET on every command, read-only ones included - which is why the
# read-only case above still gets all three.
check("vpp" in system.groups_for(""),
      "a read-only account must still be able to reach the dataplane")

print("\n=== a demotion removes the membership ===")
# Or it is not a demotion: the VTY would report read-only and the box would go
# on granting the role.
box = FakeBox(tempfile.mkdtemp(), PASSWD,
              GROUPS_NONE.replace("prisma-admin:x:903:", "prisma-admin:x:903:noc")
                         .replace("prisma-cgnat:x:904:", "prisma-cgnat:x:904:noc"))
had = system.User("noc")
had.role = system.ROLE_ADMIN
ok, msgs = box.system().apply_users({"noc": system.User("noc")}, {"noc": had})
check(ok, f"the demotion must apply: {msgs}")
check(["gpasswd", "-d", "noc", "prisma-admin"] in box.calls,
      f"prisma-admin must be taken away: {box.calls}")
check(["gpasswd", "-d", "noc", "prisma-cgnat"] in box.calls,
      f"and prisma-cgnat with it: {box.calls}")
check(["gpasswd", "-d", "noc", "vpp"] not in box.calls,
      "but not the reach, which a read-only account needs to read at all")
print(f"  {[c for c in box.calls if c[0] == 'gpasswd']}")

# A group this VTY was never asked to manage stays. An operator may have put
# the account in "sudo" for reasons of their own, and a demotion that took it
# away would be this VTY deciding something nobody asked it to decide.
box = FakeBox(tempfile.mkdtemp(), PASSWD, GROUPS_NONE + "sudo:x:27:noc\n")
box.system().apply_users({"noc": system.User("noc")}, {"noc": system.User("noc")})
check(not [c for c in box.calls if c[:2] == ["gpasswd", "-d"] and c[-1] == "sudo"],
      f"an unmanaged group must be left alone: {box.calls}")

print("\n=== root's memberships are never touched ===")
box = FakeBox(tempfile.mkdtemp(), PASSWD, GROUPS_NONE)
box.system().apply_users({"root": system.User("root")},
                         {"root": system.User("root")})
check(box.calls == [], f"nothing may be run against root: {box.calls}")
check(system.validate_role("root", system.ROLE_ADMIN) is not None,
      "and a role typed on root is refused rather than written")
print(f"  {system.validate_role('root', system.ROLE_ADMIN)}")

print("\n=== a role group that cannot be joined fails the commit ===")
# The rank was granted and did not happen. Reporting success here is how the
# defect this file is about survived: the operator is told the account is an
# administrator and every command it runs is refused.
box = FakeBox(tempfile.mkdtemp(), PASSWD, GROUPS_NONE,
              refuse={"prisma-admin"})
want = system.User("noc")
want.role = system.ROLE_ADMIN
ok, msgs = box.system().apply_users({"noc": want}, {"noc": system.User("noc")})
check(ok is False, "a role that could not be granted must fail the commit")
check(any("prisma-admin" in m for m in msgs),
      f"and the message must name the group: {msgs}")
print(f"  {msgs[-1]}")

# A reach group that is missing does not stop the account existing: on a box
# without FRR there is no frrvty group to join and no vtysh to run either.
box = FakeBox(tempfile.mkdtemp(), PASSWD, GROUPS_NONE, refuse={"frrvty"})
ok, msgs = box.system().apply_users({"noc": system.User("noc")},
                                    {"noc": system.User("noc")})
check(ok is True, f"a missing reach group must not fail the commit: {msgs}")
check(any("frrvty" in m for m in msgs),
      f"but it must be said out loud: {msgs}")

print("\n=== removing the last administrator is refused ===")
# The lockout refusal through the other door. A box nobody can configure again
# is the mistake an operator makes once, from the other side of the country:
# a cgnat operator cannot grant a role, so the way back is a root shell they
# may not have.
KEY = "ssh-ed25519 AAAAfake noc@lab"
RUNNING = (f"ssh permit-root-login no\nuser noc\n role administrator\n"
           f" ssh-key {KEY}\n exit\n")
DEMOTED = f"ssh permit-root-login no\nuser noc\n ssh-key {KEY}\n exit\n"
sh = shell_with(FakeApi(), running_text=RUNNING)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ok = sh.apply(parse_nested(DEMOTED), check_only=True)
check(ok is False, "demoting the last administrator must be refused")
check("nobody able to configure" in buf.getvalue(),
      f"and say why: {buf.getvalue()!r}")
print("  " + [l for l in buf.getvalue().splitlines() if "error" in l][0])

# Refused only when THIS CHANGE is what causes it, exactly as the SSH lockout
# is. A box that already has no reachable administrator - freshly installed,
# reached by console - must still be able to commit.
sh = shell_with(FakeApi(), running_text=DEMOTED)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ok = sh.apply(parse_nested(DEMOTED + "user two\n exit\n"),
                  check_only=True)
check(ok is True,
      f"a box with no administrator already must still commit: {buf.getvalue()!r}")

# And a demotion that leaves another administrator behind is not refused, or
# the refusal would make demotion impossible rather than safe.
TWO = RUNNING + f"user two\n role administrator\n ssh-key {KEY}\n exit\n"
sh = shell_with(FakeApi(), running_text=TWO)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ok = sh.apply(parse_nested(
        DEMOTED + f"user two\n role administrator\n ssh-key {KEY}\n exit\n"),
        check_only=True)
check(ok is True, f"one of two administrators may be demoted: {buf.getvalue()!r}")
print("  one of two may be demoted; the last one may not")

print("\n=== 'no forward' names the subscriber when the port names two ===")
# The public address of a forward is the subscriber's own and is never typed,
# so one public port names one forward per public address the pool has. The
# removal used to drop EVERY forward on the port: the operator removes a
# customer's inbound port and hears about it from a second customer.
sh = shell_with(FakeApi())
sh.dp.reachable = lambda: True
# Straight into configuration mode: what is being tested is one statement in a
# pool block, not the way in.
sh.mode = "config"
sh.candidate = config.Config()
sh.feed("nat")
sh.feed("cgnat")
sh.feed("pool residential")
sh.feed("inside 100.64.0.0/22")
sh.feed("forward tcp 1650 to 100.64.0.5 8080")
sh.feed("forward tcp 1650 to 100.64.0.6 8081")
pool = sh.candidate.pools["residential"]
check(len(pool.forwards) == 2,
      f"two subscribers may hold the same port: {pool.forwards}")

out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("no forward tcp 1650")
said = out.getvalue()
check(len(pool.forwards) == 2,
      f"an ambiguous removal must remove nothing: {pool.forwards}")
check("100.64.0.5" in said and "100.64.0.6" in said,
      f"and must name both subscribers it could have meant: {said.strip()}")
print(f"  no forward tcp 1650 -> {said.strip()}")

sh.feed("no forward tcp 1650 to 100.64.0.5 8080")
check([f[2] for f in pool.forwards] == ["100.64.0.6"],
      f"naming the subscriber removes that one: {pool.forwards}")
sh.feed("no forward tcp 1650")
check(pool.forwards == [],
      f"and the port alone is enough once it names one: {pool.forwards}")

print("\n=== a loopback is created before it is shut down (#179, #182) ===")
# What this prevents is a box that does not come up. A loopback is created
# administratively DOWN, so the configuration a box saves carries `shutdown`
# under it - and prisma-routing.service replays that file at every boot, on a
# machine nobody is watching. With the shutdown pass ahead of the creation
# pass, the very first statement applied named an interface the dataplane did
# not have yet:
#
#   error: interface loop7: the dataplane does not have it
#
# The commit failed, the unit failed, and the box came up with NONE of its
# configuration - so every loopback this product can create survived exactly
# until the next reboot. Measured in lab/lcp.sh by saving a paired loopback,
# taking it down, and handing the box back the file it had written.


class FakeDataplane(FakeApi):
    """
    A dataplane that only has the interfaces it has been told to create.

    The stub above answers OK for everything and has no notion of an interface
    existing, which is exactly the assumption that hid this: the calls all
    succeeded in the test and the real box refused the first one.
    """

    def __init__(self, existing=()):
        super().__init__()
        self.have = {n: i for i, n in enumerate(existing, start=1)}

    def interface_index(self, name):
        return self.have.get(name)

    def call(self, message, args):
        self.calls.append((message, args))
        if message == "create_loopback_instance":
            n = args["user_instance"]
            self.have[f"loop{n}"] = 100 + n
        if message == "create_vlan_subif":
            # VPP names a sub-interface <parent>.<id>, and has it from then
            # on - which the pair a sub-interface gets by default (#461)
            # needs, in the same commit that created it.
            parent = {i: n for n, i in self.have.items()}.get(
                args["sw_if_index"])
            if parent:
                self.have[f"{parent}.{args['vlan_id']}"] = \
                    1000 + args["vlan_id"]
        return vppapi.Reply(vppapi.OK)


SAVED_LOOPBACK = """interface loop7
 linux-name loop7
 shutdown
 exit
"""
api = FakeDataplane()
sh = shell_with(api)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ok = sh.apply(parse_nested(SAVED_LOOPBACK))
check(ok is True,
      f"a box must be able to reload its own saved loopback: {buf.getvalue()!r}")
order = [m for m, _ in api.calls]
# The whole sequence, not "these three appear somewhere". The order IS the
# fix: with the shutdown ahead of the creation the first call names an
# interface that does not exist, set_admin refuses, and nothing after it runs
# at all. The pair is in the list because it is the statement #182 put in the
# file in the first place, and it comes after the creation for the same
# reason.
check(order == ["create_loopback_instance", "sw_interface_set_flags",
                "lcp_itf_pair_add_del"],
      f"created, then shut down, then paired: {order}")
print(f"  order: {order}")

# The ordering the exception is carved out of must be intact for an interface
# that already exists: taken down BEFORE anything else is changed on it, so it
# stops carrying traffic before its configuration moves underneath it.
api = FakeDataplane(existing=("host-veth-vpp",))
sh = shell_with(api, running_text="")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.apply(parse_nested("""interface host-veth-vpp
 shutdown
 mtu 1500
 exit
"""))
order = [m for m, _ in api.calls]
if "sw_interface_set_flags" in order and "sw_interface_set_mtu" in order:
    check(order.index("sw_interface_set_flags") < order.index("sw_interface_set_mtu"),
          f"an existing interface is still shut down first: {order}")
else:
    check(False, f"the existing-interface case sent neither call: {order}")
print(f"  order: {order}")

print("\n=== a saved file naming a pair's TAP still loads (#182) ===")
# The other half of the same boot failure. `save` wrote the pair's own TAP
# into the file, and at the next boot - a fresh VPP with no pairs yet - that
# interface does not exist, the interface-existence check refuses the file
# whole, and the box comes up with no configuration at all. Shell.load drops a
# tap<n> stanza the DATAPLANE DOES NOT HAVE, which is the boxes that already
# saved one, and leaves a tap the box really has alone.
api = FakeDataplane(existing=("host-veth-vpp",))
sh = shell_with(api)
sh.running_config = lambda: config.Config()
with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as fh:
    fh.write("""interface tap4096
 mac-address de:ad:00:00:00:01
 mtu 9000
 exit
interface host-veth-vpp
 mtu 1500
 exit
""")
    saved = fh.name
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.load(saved)
out = buf.getvalue()
check("refusing to load" not in out,
      f"a box must load its own saved file: {out!r}")
check("tap4096" not in out,
      f"and must not refuse it over the pair's TAP: {out!r}")
# And the real interface in the same file is still applied, so the drop took
# the stanza out rather than the file.
check(any(m == "sw_interface_set_mtu" for m, _ in api.calls),
      f"the real interface still gets its MTU: {[m for m, _ in api.calls]}")
print(f"  loaded; calls: {[m for m, _ in api.calls]}")

# And a `tap<n>` the box REALLY has is an interface somebody configured on
# purpose - `vppctl create tap id 0 host-if-name vppwan` produces tap0, and
# lab/hw-vpp.sh runs its whole WAN over it. Dropping that by name would take a
# real interface's configuration out of the file, so this asserts the MTU in
# the file reaches the dataplane.
api = FakeDataplane(existing=("tap0",))
sh = shell_with(api)
sh.running_config = lambda: config.Config()
with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as fh:
    fh.write("interface tap0\n mtu 1400\n exit\n")
    own = fh.name
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.load(own)
mtus = [(a.get("sw_if_index"), a.get("mtu")) for m, a in api.calls
        if m == "sw_interface_set_mtu"]
check(mtus, f"a TAP nobody paired keeps its configuration: {buf.getvalue()!r}")
print(f"  tap0 mtu calls: {mtus}")
os.unlink(saved)
os.unlink(own)

print("\n=== a saved file carrying block-stride on a deterministic pool "
      "(#375) ===")
# The pair has been accepted since 0.67.0 and the allocator never read the
# stride, so a file saved by any version since can carry it - on a box that
# ran on that file yesterday. The commit check refuses it at the prompt; at
# boot a refusal is a box coming up with no pools on it, read by nobody, to
# enforce a property about a setting that was already inert. So Shell.load
# drops the stride from such a pool, says so, and applies the pool as the box
# was running it.
#
# THE CONTROL IS THE SECOND POOL. The same stride on a dynamic pool in the
# same file is the configuration the setting exists for, and it MUST reach
# the dataplane whole: without it every check below passes on a load() that
# strips every stride there is.
api = FakeDataplane()
sh = shell_with(api)
sh.running_config = lambda: config.Config()
with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as fh:
    fh.write("""nat
 cgnat
  pool det
   inside 100.64.0.0/22
   outside 200.150.0.16/30
   mode deterministic
   max-blocks 2
   block-stride 3
   exit
  pool dyn
   inside 100.64.4.0/22
   outside 200.150.0.20/30
   mode dynamic
   max-blocks 2
   block-stride 3
   exit
  exit
 exit
""")
    saved = fh.name
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sh.load(saved)
out = buf.getvalue()
check("refusing to load" not in out,
      f"a box must load its own saved file: {out!r}")
check("block-stride applies to dynamic pools only" not in out,
      f"and must not refuse the pair at boot: {out!r}")
# The note whole and on a row of its own, the pool named last: a pool name
# has no widest, and last on the row the fold lands after the words (#363).
check("note: block-stride 3 dropped from pool det" in out.splitlines(),
      f"the boot says which statement it left out and from where: {out!r}")
check("dropped from pool dyn" not in out,
      f"and names only the pool it dropped from: {out!r}")
# And the dynamic pool in this file exports nothing, so the boot that loads
# it says so too (#444) - the one place a box nobody is typing at is heard.
check("nothing exports its block events" in " ".join(out.split())
      and "    nat / cgnat / pool dyn" in out.splitlines(),
      f"a loaded dynamic pool with no export is warned at boot too: {out!r}")
adds = {a["name"]: a for m, a in api.calls
        if m == "bpa_pool_add_del" and a.get("is_add")}
check(set(adds) == {"det", "dyn"},
      f"both pools reached the dataplane: {sorted(adds)}")
# What was APPLIED, not what was printed: the deterministic pool arrives as
# the box was running it, and the dynamic one arrives with its stride.
check(adds.get("det", {}).get("block_stride") == 0,
      f"the deterministic pool arrived without the stride: {adds.get('det')}")
check(adds.get("dyn", {}).get("block_stride") == 3,
      f"the dynamic pool kept its stride: {adds.get('dyn')}")
print(f"  loaded; strides applied: "
      f"{ {n: a.get('block_stride') for n, a in sorted(adds.items())} }")
os.unlink(saved)

print("\n=== a file's own address makes its own static route reachable (#294) ===")
# The most serious shape this repository has had: `prismash --load` refusing a
# box's ENTIRE configuration over a static route that is correct.
#
# The next-hop check reads the live VPP FIB. At boot the FIB is
# MID-CONSTRUCTION - VPP has just started and the interface addresses that
# make the next hop connected are in the very file being read - so the check
# answers a question about a box that does not exist yet, and `--load` refuses
# the file whole. The box then comes up with no pools, no addresses and no
# attachments, which is the same door drop_lcp_taps() was opened for.
#
# What has hidden it is worse than the defect: prisma-routing.service runs after
# frr.service, FRR has already read the same static route out of its OWN
# /etc/frr/frr.conf, so `routing_changed` is false and the failing branch is
# never reached. The configuration a box boots with is protected by a second
# copy of it in another daemon's file - one lost conffile, one unit-ordering
# change, one hand-run `--load` away from refusing everything (#294).
#
# `flat` here for the same reason it exists in test_prismash_complete.py: the
# console folds prose, so where it breaks is a measurement and not a promise.


def flat(text):
    return " ".join(text.split())


# Captured from a real VPP started with lab/startup-test.conf and nothing
# configured on it, not written by hand: table 0 holds the five drop entries
# VPP creates for itself and no connected prefix at all. That is the dataplane
# `prismash --load` is handed at every boot.
FRESH_FIB = """ipv4-VRF:0, fib_index:0, flow hash:[src dst sport dport proto flowlabel ] epoch:0 flags:none locks:[default-route:1, ]
0.0.0.0/0
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:1 buckets:1 uRPF:0 to:[0:0]]
    [0] [@0]: dpo-drop ip4
0.0.0.0/32
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:2 buckets:1 uRPF:1 to:[0:0]]
    [0] [@0]: dpo-drop ip4
224.0.0.0/4
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:4 buckets:1 uRPF:3 to:[0:0]]
    [0] [@0]: dpo-drop ip4
240.0.0.0/4
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:3 buckets:1 uRPF:2 to:[0:0]]
    [0] [@0]: dpo-drop ip4
255.255.255.255/32
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:5 buckets:1 uRPF:4 to:[0:0]]
    [0] [@0]: dpo-drop ip4
"""

# The same box once its configuration HAS been applied: the outside interface's
# address is connected. This is the box the interactive refusal is typed at.
CONFIGURED_FIB = FRESH_FIB + """200.150.0.0/24
  unicast-ip4-chain
  [@0]: dpo-load-balance: [proto:ip4 index:10 buckets:1 uRPF:9 to:[0:0]]
    [0] [@4]: ipv4-glean: [src:200.150.0.0/24] GigabitEthernet0/12/0: mtu:9000 next:1 flags:[] ffffffffffff02fefecd294a0806
"""

DP_IFACES = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
GigabitEthernet0/12/0             1      up          9000/0/0/0
local0                            0     down          0/0/0/0
"""

# What `save` writes on an ordinary edge box, trimmed to the two statements
# that are the whole question: the address, and the route it makes reachable.
SAVED_ROUTE = """interface GigabitEthernet0/12/0
 ipv4 address 200.150.0.1/24
 exit
! routing
router static
 200.150.0.0/24 next-hop 200.150.0.2
"""


class BootVpp:
    """The dataplane at boot: it has its card, and nothing is configured."""

    TRACE = False

    def __init__(self, fib=FRESH_FIB):
        self.fib = fib
        self.commands = []

    def run(self, cmd):
        self.commands.append(cmd)
        if cmd.startswith("show ip fib"):
            return 0, self.fib
        if cmd.startswith("show interface"):
            return 0, DP_IFACES
        return 0, ""

    def running(self):
        return 0, ""


def boot_shell(fib=FRESH_FIB, running_text=""):
    """A Shell whose FRR holds NOTHING, which is the case #294 is about.

    `prismash --load` survives today only because FRR has already converged onto
    its own copy of the route. A stub that answered `running` with the route in
    it would reproduce the crutch instead of the defect.
    """
    api = FakeDataplane(existing=("GigabitEthernet0/12/0",))
    sh = shell_with(api, running_text=running_text)
    sh.dp = BootVpp(fib)
    sh.frr = type("F", (prismash.Frr,), {
        "available": lambda s: True,
        "running": lambda s: [],
        "daemons": lambda s: {"zebra", "mgmtd", "staticd"},
        "reload": lambda s, l, check_only=False: (0, ""),
        "show": lambda s, c: ((0, "vrf default id 0 table 254\n")
                              if c == "show vrf" else (1, "")),
    })()
    sh.rb = type("RB", (), {"push": lambda self, *a: None})()
    return sh


def load_text(sh, text):
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as fh:
        fh.write(text)
        p = fh.name
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sh.load(p)
    os.unlink(p)
    return flat(buf.getvalue())


# A. THE CASE THE BOX BOOTS INTO. Nothing on the dataplane, nothing in FRR,
#    and the address that makes 200.150.0.2 connected is three lines above the
#    route in the same file.
_out = load_text(boot_shell(), SAVED_ROUTE)
check("refusing to load this file" not in _out,
      f"a box must load the configuration it saved: {_out!r}")
check("no connected network" not in _out,
      f"and must not refuse its own route over an address in the same "
      f"file: {_out!r}")
print(f"  loaded against an empty dataplane: {_out!r}")

# B. AND THE CHECK IS STILL RUN AT `--load`. Switching it off for the boot
#    path would trade a box that refuses its configuration loudly for one that
#    accepts a route which never forwards, silently - and #197's argument is
#    that the second is worse. This is the assertion that says so: the file
#    below carries no address for 172.31.99.0/24 and the dataplane has none
#    either, so nothing anywhere makes this next hop connected.
_out = load_text(boot_shell(),
                 SAVED_ROUTE + " 10.55.0.0/16 next-hop 172.31.99.1\n")
check("this next hop is on no connected network: 172.31.99.1" in _out,
      f"--load still refuses a next hop nothing makes connected: {_out!r}")
check("refusing to load this file" in _out,
      f"and refuses the file rather than applying half of it: {_out!r}")
print(f"  a next hop nothing makes reachable: {_out!r}")

# C. THE INTERACTIVE DIRECTION, at a box that IS configured. This is where the
#    FIB is the right thing to read and the refusal is the whole point of the
#    check (#95): a route accepted here sits in FRR, never reaches the FIB, and
#    `show ip route` looks correct.
_sh = boot_shell(fib=CONFIGURED_FIB, running_text=SAVED_ROUTE)
_sh.candidate = parse_nested(
    SAVED_ROUTE + " 10.55.0.0/16 next-hop 172.31.99.1\n")
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _sh.do_commit(["check"])
_out = flat(_buf.getvalue())
check("this next hop is on no connected network: 172.31.99.1" in _out,
      f"a configured box still refuses a next hop on no connected "
      f"network: {_out!r}")
check("this next hop is on no connected network: 200.150.0.2" not in _out,
      f"and does not refuse the one its own address covers: {_out!r}")
print(f"  at a configured box: {_out!r}")

# THE SAME MID-CONSTRUCTION PROBLEM, IN THE OTHER `--load`-time check that
# reads live dataplane state. Found while fixing the one above, and measured
# before it was believed.
#
# The interface-existence check builds "what this box has" from `show
# interface` and `show lcp`. Three things a file CREATES are already carved out
# of it - a sub-interface, a loopback, and (in Shell.load) a stale `tap<n>`
# stanza - and the fourth was not: the LINUX side of a linux_cp pair, which
# since #182 this product creates itself from `linux-name`. An FRR stanza names
# that side and no other, because FRR configures by the Linux name: `interface
# vppwan0 / ip ospf area 0` is what lab/igp.sh and lab/routing.sh both write.
#
# At boot `show lcp` is empty, so the name is in the file and on no box, and
# `--load` refused the whole configuration. Measured on the build this section
# was written against:
#
#   error: interface vppwan0: this box has no such interface
#       it has: GigabitEthernet0/12/0, local0
#
# Worse than #294's own half in one respect: this check is not gated on
# `routing_changed`, so FRR's second copy of the configuration does not hide it.
SAVED_PAIR = """interface GigabitEthernet0/12/0
 linux-name vppwan0
 exit
! routing
interface vppwan0
 ip ospf area 0
exit
"""
_out = load_text(boot_shell(), SAVED_PAIR)
check("refusing to load this file" not in _out,
      f"a pair this file CREATES is not an interface the box is "
      f"missing: {_out!r}")
check("no such interface" not in _out,
      f"and the FRR stanza naming its Linux side loads: {_out!r}")
print(f"  a pair created by the file being loaded: {_out!r}")

# And the carve-out must not become "every name is fine". A name nothing on the
# box has and nothing in the file creates is still refused, or the check has
# been turned off rather than corrected.
_out = load_text(boot_shell(), SAVED_PAIR.replace("interface vppwan0\n",
                                                  "interface vppwan9\n"))
check("interface vppwan9: this box has no such interface" in _out,
      f"a name nothing has and nothing creates is still refused: {_out!r}")
print(f"  a name nothing creates: {_out!r}")

# THE TWO HALVES OF "WHAT IS CONNECTED", ASKED DIRECTLY. The union above goes
# through apply(), which needs a whole box to drive; these two are the pieces
# it is built from, and one of them has an edge that the load path cannot
# reach.
#
# A VRF address in the file makes a VRF route in the same file reachable, the
# same way the global one does - `vrfs` is already the table number, which is
# what makes the two halves key the same way (#255).
_cfg = parse_nested("""interface GigabitEthernet0/12/0
 vrf 10
 ipv4 address 172.31.99.1/24
 exit
""")
check(_cfg.connected_prefixes() == {10: ["172.31.99.1/24"]},
      f"a VRF address lands in its own table: {_cfg.connected_prefixes()}")

# And a VRF this file only puts an IPv6 address in is NOT a table here. In
# routes.unreachable_next_hops a table present with an empty list is a
# REFUSAL and a table that is absent is a note saying the check could not be
# made, so a key added here would turn the note into a refusal of a
# configuration this VTY cannot see - the harm #208 was filed for. This half
# may only ever ADD prefixes.
_cfg = parse_nested("""interface GigabitEthernet0/12/0
 vrf 10
 ipv6 address 2001:db8::1/64
 exit
""")
check(_cfg.connected_prefixes() == {},
      f"an IPv6-only VRF is not a table this half claims to know: "
      f"{_cfg.connected_prefixes()}")
_bad, _unplaced = routes.unreachable_next_hops(
    ["vrf red", " ip route 10.55.0.0/16 172.31.99.1", "exit"],
    _cfg.connected_prefixes(),
    config.block_paths(["vrf red", " ip route 10.55.0.0/16 172.31.99.1",
                        "exit"]),
    {"red": 10})
check(not _bad and _unplaced,
      f"so the VRF is still a note and not a refusal: {_bad}, {_unplaced}")
print(f"  the unchecked VRF is still said out loud: {_unplaced}")

print("\n=== a commit that fails halfway puts back what it took away ===")
# docs/routing.md:1528 is one sentence with two halves - `commit` MUST validate
# before applying, AND MUST restore the previous state if application fails
# halfway - and only the first half had an assertion. Deleting the restore left
# all 33 suites green while a failed commit emptied a production pool and
# printed that it had rolled back: a report of success with no effect behind
# it, which is the defect class this repository is organised against.
#
# The dataplane is modelled with STATE here, and that is the whole point. An
# API that only records calls cannot answer the question this section asks,
# which is what the box is holding once the commit has finished failing.


class DeviceApi:
    """A dataplane that keeps the pools it is given, and refuses to make one."""

    def __init__(self, known, holds=(), refuse=()):
        self.known = known          # name -> the definition, as the box got it
        self.holds = list(holds)    # what the box is holding right now
        self.refuse = set(refuse)
        self.calls = []

    def available(self):
        return (vppapi.OK, "stub")

    def call(self, message, args):
        self.calls.append((message, args))
        if message != "bpa_pool_add_del":
            return vppapi.Reply(vppapi.OK)
        name = args.get("name")
        if not args.get("is_add"):
            if name in self.holds:
                self.holds.remove(name)
            return vppapi.Reply(vppapi.OK)
        if name in self.refuse:
            return vppapi.Reply(vppapi.REFUSED, retval=-7,
                                reason="the dataplane refused it: invalid value")
        if name not in self.holds:
            self.holds.append(name)
        return vppapi.Reply(vppapi.OK)

    def held_config(self):
        cfg = config.Config()
        for name in self.holds:
            cfg.pools[name] = self.known[name]
        return cfg


def pool_named(name, inside, outside):
    p = config.Pool(name)
    p.inside = [inside]
    p.outside = [outside]
    p.block_size = "512"
    return p


keep = pool_named("keep", "100.64.0.0/22", "200.150.0.16/28")
doomed = pool_named("doomed", "100.64.4.0/22", "200.150.0.32/28")

api = DeviceApi({"keep": keep, "doomed": doomed}, holds=["keep"],
                refuse=["doomed"])
sh = shell_with(api)
sh.running_config = api.held_config
# The two stores commit writes to. Neither is reached on the failure path, and
# stubbing them keeps this suite off the invoking account's filesystem.
sh.confirm = type("C", (), {"read": lambda s: None})()
sh.rb = type("R", (), {"push": lambda s, text, comment="": None})()
candidate = config.Config()
candidate.pools["doomed"] = doomed
sh.candidate = candidate

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    with contextlib.redirect_stderr(buf):
        sh.do_commit([])
said = buf.getvalue()
pool_calls = [(a.get("name"), a.get("is_add")) for m, a in api.calls
              if m == "bpa_pool_add_del"]

check("commit failed" in said,
      f"a write the dataplane refuses must fail the commit: {said!r}")
# THE CONTROL. Without it every assertion below passes on a box that never
# removed anything, which is the one way this section could prove nothing:
# "restored" and "never touched" look identical from the end state alone.
check(("keep", False) in pool_calls,
      f"the apply really did take the running pool off the box: {pool_calls}")
check(api.holds == ["keep"],
      f"the box must hold what it held before the commit: {api.holds}")
check(("keep", True) in pool_calls
      and pool_calls.index(("keep", True))
      > pool_calls.index(("doomed", True)),
      "and it must be put back AFTER the refusal, which is what makes it a "
      f"restore rather than an accident of ordering: {pool_calls}")
print(f"  pool calls: {pool_calls}")
print(f"  the box holds: {api.holds}")

print("\n=== CGNAT is applied before routing, and something watches (#241) ===")
# `docs/routing.md` calls this a MUST and `apply()`'s own docstring repeats it:
# a routing change can cost reachability, so doing it last keeps the window in
# which the box is half-configured as short as possible. Reloading FRR at the
# top of apply() left all 33 suites green, because nothing here was in a
# position to SEE the order: the FRR stub records nothing, and the API stub and
# the dataplane stub each keep a list of their own, so no two calls could be
# compared.
#
# One clock for all of them. An ordering claim needs a single sequence, and two
# lists that each start at zero cannot answer which came first - which is why
# this went unheld rather than anybody deciding it did not matter.


class OrderedApi(FakeApi):
    """A dataplane API that writes into a shared trace."""

    def __init__(self, trace):
        FakeApi.__init__(self)
        self.trace = trace

    def call(self, message, args):
        self.trace.append(("cgnat", message))
        return FakeApi.call(self, message, args)


class OrderedFrr:
    """An FRR that writes into the same trace, and says it is there."""

    def __init__(self, trace):
        self.trace = trace

    def available(self):
        return True

    def running(self):
        return []

    def outstanding(self, _out):
        return []

    def diagnostics(self, _out):
        return []

    def reload(self, lines, check_only=False):
        self.trace.append(("routing", "check" if check_only else "reload"))
        return 0, ""


_trace = []
_sh = shell_with(OrderedApi(_trace))
_sh.frr = OrderedFrr(_trace)
_sh.rb = type("_RB", (), {"push": lambda self, *a: None})()
_sh.dp = FakeVpp()

# A target that changes BOTH halves in one commit. Only a commit touching both
# can be wrong about the order.
_target = target_with("ordered", "100.64.0.0/22", "200.150.0.16/28")
_target.routing = ["router bgp 65000", " neighbor 10.0.0.2 remote-as 65001"]
_sh.apply(_target)

_kinds = [k for k, _ in _trace]
print(f"  the order the box was touched in: {_kinds}")

check("cgnat" in _kinds,
      f"no CGNAT call was made at all, so the order below is about nothing: "
      f"{_trace}")
check("routing" in _kinds,
      f"FRR was never reloaded, so there is no routing half to be second: "
      f"{_trace}")
if "cgnat" in _kinds and "routing" in _kinds:
    _last_cgnat = len(_kinds) - 1 - _kinds[::-1].index("cgnat")
    _first_routing = _kinds.index("routing")
    check(_last_cgnat < _first_routing,
          f"routing was applied before CGNAT had finished: the last CGNAT "
          f"call is at {_last_cgnat} and the first routing call at "
          f"{_first_routing}. A routing change can cost reachability, and "
          f"doing it first widens the window in which the box is "
          f"half-configured: {_kinds}")


print("\n=== a cgnat setting this VTY does not have is refused (#273) ===")
# `cgnat <anything>` used to match a catch-all: the line went into the
# candidate, was rendered back by `show configuration`, was saved, and applied
# NOTHING, because apply_global() had four branches and no else. The operator
# was told `commit complete` for a setting the box does not have, and then
# shown it in the configuration - the one thing that would otherwise have told
# them it was not real.
#
# docs/context.md 1.4: a counter is not evidence. An acceptance is not evidence
# either, and this is #136's defect one layer up: in the VTY the operator types
# at, rather than in vppctl.
#
# It is the failover lab (#9) that makes the cost concrete. A lab asserting the
# box ACCEPTS an HA configuration would have been green here on a box with no
# HA at all.


def cfg_shell():
    sh = shell_with(FakeApi())
    sh.candidate = parse_nested("")
    sh.mode = "config"
    sh.nat_ctx = ["nat", "cgnat"]      # typed where CGNAT is typed (#446)
    sh.failed = 0
    return sh


_sh = cfg_shell()
_sh.do_config("ha peer 10.0.0.2 priority 200")
check(_sh.failed == 1,
      "an HA setting this VTY has no grammar for was accepted: it would be "
      "saved, shown back by 'show configuration', and applied to nothing")
check(not any("ha peer" in g for g in _sh.candidate.globals),
      f"it was refused and reached the candidate anyway, so it would still be "
      f"written to the configuration file: {_sh.candidate.globals}")

_sh = cfg_shell()
_sh.do_config("completely made up nonsense")
check(_sh.failed == 1, "a wholly invented cgnat setting was accepted")

# The other direction, and it is what keeps this from becoming a blanket
# refusal. Reading the same table the refusal reads is deliberate: a second
# list here would drift from what `?` says the box takes, which is the defect
# this repository keeps finding.
for _line in ("cgnat block-holddown 4",
              "cgnat logging enable",
              "cgnat timeout udp 300",
              "cgnat session-table buckets 64 memory 8"):
    _sh = cfg_shell()
    _sh.do_config(_line[len("cgnat "):])
    check(_sh.failed == 0,
          f"{_line!r} was refused, and its setting is in CGNAT_CONFIG: the "
          f"refusal has become a blanket one")
    check(_line in _sh.candidate.globals,
          f"{_line!r} was accepted and did not reach the candidate: "
          f"{_sh.candidate.globals}")

# And the refusal has to say what the box does take, or the operator is left
# guessing at a grammar nothing prints.
_out = io.StringIO()
_sh = cfg_shell()
with contextlib.redirect_stdout(_out):
    _sh.do_config("ha peer 10.0.0.2")
_said = _out.getvalue()
for _w in sorted(prismash.CGNAT_CONFIG):
    check(_w in _said,
          f"the refusal does not name {_w!r} among what this box takes, so it "
          f"says no without saying what yes would be: {_said!r}")
print(f"  refused, and named all {len(prismash.CGNAT_CONFIG)} settings the box "
      f"takes")

print("\n=== and apply_global refuses one too, for the paths with no VTY ===")
# --load and the binary API reach apply_global() without passing do_config, so
# the refusal above does not cover them. Unreachable from the VTY is not the
# same as unreachable.
_sh = shell_with(FakeApi())
_sh.failed = 0
_sh.apply_global("cgnat ha peer 10.0.0.2 priority 200")
check(_sh.failed == 1,
      "apply_global applied an unknown cgnat global to nothing and said "
      "nothing: a configuration file with this line loads clean and the "
      "setting does not exist")

print("\n=== a pool over an address the same candidate configures is refused "
      "at check time (#296) ===")
# THE BOOT CASE, and this file is the only place it can be asked cheaply.
# FakeVpp accepts every CLI command, so `pool ... check` comes back clean -
# which is exactly the box `prismash --load` runs against at a boot: a dataplane
# that has just started, with no address on it for
# bpa_check_local_overlap()'s walk to find. The plugin is not weakened here
# and is not being tested here; it is simply blind at this moment, by
# construction, and lab/localaddr.sh is where its own refusal is asserted
# against a real vnet.
#
# MEASURED before the check existed, on a real dataplane: the same file typed
# at a configured box was refused with exit 1 and no pool created, and loaded
# into a box with nothing on it came back exit 0 with the pool in `show bpa
# pool`, the address on the interface, and one vlib_log_err in `show logging`
# that nothing else on the box reads back. That reading is lab/localaddr.sh
# sections 5 to 7, which drive both doors against a real vnet; this suite is
# the cheap half of it and runs in `make test`.


def candidate_with_clash():
    """One file, one address, one pool whose public range contains it.

    200.150.0.16/28 is .16 through .31, so it holds .20 - the same arithmetic
    bpa_addr_set_build() does, which stores a prefix as base+count with every
    address in it.
    """
    cfg = target_with("clash", "100.64.0.0/22", "200.150.0.16/28")
    cfg.addresses["pg1"] = ["200.150.0.20/28"]
    return cfg


_out = io.StringIO()
_sh = shell_with(FakeApi())
with contextlib.redirect_stdout(_out):
    _ok = _sh.apply(candidate_with_clash(), check_only=True)
_said = _out.getvalue()
check(_ok is False,
      "a candidate that puts a pool's public range over its own interface "
      "address passed the check: at --load the pool is created before the "
      "address is applied, so the plugin's walk finds nothing and the box "
      "translates its own BGP session")
check("200.150.0.20" in _said,
      f"the refusal does not name the address: {_said!r}")
check("pg1" in _said,
      f"the refusal does not name the interface it is on: {_said!r}")
check("clash" in _said,
      f"the refusal does not name the pool: {_said!r}")
# The two statements, whole and greppable, because deleting one of them is
# the operator's next move (#252).
check("    interface pg1 / ipv4 address 200.150.0.20/28" in _said,
      f"the address statement is not printed whole: {_said!r}")
check("    nat / cgnat / pool clash / outside 200.150.0.16/28" in _said,
      f"the pool statement is not printed whole: {_said!r}")
# WHAT IT COSTS, not only that it is refused. The needle is collapsed on both
# sides: console_prose wraps by measurement, so a raw-text needle only matches
# while the sentence happens to fold somewhere else, and this repository has
# been caught by that four times.
_flat = " ".join(_said.split())
check("translate the box's own control-plane traffic" in _flat,
      f"the refusal does not say what breaks: {_said!r}")
check("take the address off the interface" in _flat,
      f"the refusal does not say what to do about it: {_said!r}")
# And it is a CHECK: nothing was applied, which is the whole difference
# between this and the vlib_log_err it replaces on this path.
_writes = [m for m, a in _sh.api.calls if m in vppapi.WRITE_MESSAGES]
check(_writes == [], f"the refusal wrote to the dataplane: {_writes}")
print("  " + _said.strip().replace("\n", "\n  "))

# The console this is read on, asserted rather than eyeballed. Five messages
# in this product shipped over 80 columns having been wrapped by hand (#252).
# The two configuration statements are exempt by that same rule: they are
# printed whole, past the margin if they have to be.
_wide = [l for l in _said.splitlines()
         if len(l) > config.CONSOLE_COLUMNS and " / " not in l]
check(not _wide,
      f"the refusal's own prose is over {config.CONSOLE_COLUMNS} columns: "
      f"{_wide}")

print("\n=== and when the box is already carrying the address ===")
# The configured box, which is the other door. `commit`, `--load` and
# `rollback` all reach this through one apply(check_only=True) and there is no
# branch for which of them called - deliberately, because a branch for the boot
# would have left the interactive door open. What differs between the two is
# not the code path but the CANDIDATE: at a boot the address exists only in the
# file being read, and at a configured box it is in the running configuration
# too and therefore in the candidate built from it. Both are asserted, because
# a check that read only the delta would pass this one.
_run = "interface pg1\n ipv4 address 200.150.0.20/28\n"
_out = io.StringIO()
_sh = shell_with(FakeApi(), running_text=_run)
with contextlib.redirect_stdout(_out):
    _ok = _sh.apply(parse_nested(
        _run + "cgnat pool clash\n inside 100.64.0.0/22\n"
        " outside 200.150.0.16/28\n"), check_only=True)
check(_ok is False,
      "a pool typed at a box that already carries the address passed the "
      "VTY's own check: it would then be refused only by the plugin, which "
      "is the half that is blind at a boot")

print("\n=== a pool over an address in a STANDING pool's range too ===")
# The pool is not being added or replaced; the ADDRESS is what moved into it.
# It is the same collision, and refusing it is what stops `commit` writing a
# prisma-routing.conf that `--load` will refuse whole at the next boot - a box
# unconfigured by a change that went through months earlier.
_run = ("cgnat pool clash\n inside 100.64.0.0/22\n"
        " outside 200.150.0.16/28\n exit\n")
_out = io.StringIO()
_sh = shell_with(FakeApi(), running_text=_run)
_cand = parse_nested(
    _run + "interface pg1\n ipv4 address 200.150.0.20/28\n")
# The fixture READ back rather than assumed. Without the ` exit` above, the
# interface statement is swallowed into the pool block, the candidate holds no
# address at all, and the section below would be green over a candidate that
# has nothing to collide with.
check(_cand.addresses == {"pg1": ["200.150.0.20/28"]},
      f"the candidate does not carry the address: {_cand.addresses}")
check(list(_cand.pools) == ["clash"],
      f"and the standing pool did not survive the parse: {list(_cand.pools)}")
with contextlib.redirect_stdout(_out):
    _ok = _sh.apply(_cand, check_only=True)
check(_ok is False,
      "an address moved into a pool that already exists was accepted: "
      "`save` would write both statements and the next boot would refuse "
      "the file whole")

print("\n=== and a pool that collides with nothing still goes through ===")
# The control, and it is the assertion that keeps the one above from being
# satisfied by a box that refuses every pool. Without it the check could be
# `ok = False` and this suite would be green.
_cfg = target_with("clean", "100.64.0.0/22", "200.150.9.0/28")
_cfg.addresses["pg1"] = ["200.150.0.20/28", "2001:db8::1/64"]
_out = io.StringIO()
_sh = shell_with(FakeApi())
with contextlib.redirect_stdout(_out):
    _ok = _sh.apply(_cfg, check_only=True)
check(_ok is True,
      f"a pool whose public range holds none of this box's addresses was "
      f"refused: {_out.getvalue()!r}")
check("control-plane traffic" not in _out.getvalue(),
      f"and it said so anyway: {_out.getvalue()!r}")
print("  a non-colliding pool and an IPv6 address alongside it: accepted")

print("\n=== a pool whose port range holds no block is refused at check (#361) ===")
# THE THIRD OF #361'S THREE CHECKS, and the only one that is not at the
# `ports` line. It needs `block-size` as well, and the two can be typed in
# either order - `ports 1000 1100` then `block-size 64` is a correct pool
# built in a correct order - so a guard at either line would refuse a pool
# that is about to be right. The commit check is where the pool is finished.
#
# Driven through apply(check_only=True), which is what `commit`, `commit
# check`, `rollback` and `--load` all reach, and NOT by calling
# Pool.block_fits(): the predicate is proved in test_prismash_complete.py and
# what is proved here is that the commit asks it.
_cfg = target_with("narrow", "100.64.0.0/22", "200.150.9.0/28")
_cfg.pools["narrow"].ports = "1000 1100"
_out = io.StringIO()
_sh = shell_with(FakeApi())
with contextlib.redirect_stdout(_out):
    _ok = _sh.apply(_cfg, check_only=True)
_said = _out.getvalue()
check(_ok is False,
      f"a 512-port block in a 101-port range passed the commit check: "
      f"bpa_pool_init refuses it at the write, with the candidate already "
      f"half applied: {_said!r}")
check("101 usable ports" in _said and "512" in _said,
      f"the refusal does not name both numbers the operator has to reconcile: "
      f"{_said!r}")
# Both statements whole and on their own rows: the next move is to go and
# edit one of them out of `show configuration` (#252).
check("    ports 1000 1100" in _said and "    block-size 512" in _said,
      f"the two statements are not quoted whole, so neither is greppable: "
      f"{_said!r}")
_wide = [(len(l), l) for l in _said.splitlines() if len(l) > config.CONSOLE_COLUMNS]
check(not _wide,
      f"the refusal wraps on a serial console: {_wide}")

print("\n=== and the same range with a block that fits goes through ===")
# The control, without which the check above is satisfied by a box that
# refuses every narrow pool. 101 ports and a 64-port block is a legal pool.
_cfg = target_with("fits", "100.64.0.0/22", "200.150.9.0/28", block="64")
_cfg.pools["fits"].ports = "1000 1100"
_out = io.StringIO()
_sh = shell_with(FakeApi())
with contextlib.redirect_stdout(_out):
    _ok = _sh.apply(_cfg, check_only=True)
check(_ok is True,
      f"a 64-port block in a 101-port range was refused: {_out.getvalue()!r}")
check("usable ports" not in _out.getvalue(),
      f"and it said so anyway: {_out.getvalue()!r}")

print("\n=== a first allocation that cannot be what it says is refused at check "
      "(#462) ===")
# Driven through apply(check_only=True) for block_fits()'s reason above: the
# predicate is proved in test_prismash_units.py, and what is proved here is
# that the commit asks it, on the pool being written, and lets the right one
# through.
for _mode, _ports, _want in (("dynamic", 300, "not 300"),
                             ("dynamic", 4096, "allows: 4096"),
                             ("deterministic", 1024, "dynamic pools only")):
    _cfg = target_with("first", "100.64.0.0/22", "200.150.9.0/28")
    _pool = _cfg.pools["first"]
    _pool.mode, _pool.max_blocks, _pool.initial_ports = _mode, 4, _ports
    _out = io.StringIO()
    with contextlib.redirect_stdout(_out):
        _ok = shell_with(FakeApi()).apply(_cfg, check_only=True)
    check(_ok is False and "pool 'first': initial-allocation" in _out.getvalue()
          and _want in _out.getvalue(),
          f"initial-allocation {_ports} on a {_mode} pool passed the check: "
          f"{_out.getvalue()!r}")
_cfg = target_with("first", "100.64.0.0/22", "200.150.9.0/28")
_pool = _cfg.pools["first"]
_pool.mode, _pool.max_blocks, _pool.initial_ports = "dynamic", 4, 1024
_out = io.StringIO()
with contextlib.redirect_stdout(_out):
    _ok = shell_with(FakeApi()).apply(_cfg, check_only=True)
check(_ok is True and "initial-allocation" not in _out.getvalue(),
      f"two 512-port blocks under max-blocks 4 on a dynamic pool were "
      f"refused: {_out.getvalue()!r}")
print("  not whole blocks, over the cap, deterministic: refused; the right "
      "one through")

print("\n=== a pool the box ALREADY has is not re-judged by it (#357) ===")
# The #357 rule at commit level. A pool that is not in this candidate's
# pool_add or pool_replace is a fact rather than a request, and a check that
# swept every pool in the target would make one standing pool cost the
# operator every commit on the box. bpa_pool_init would not have built such a
# pool, so this is a guard against the check growing rather than against a
# shape seen in the field - which is why it is asserted rather than assumed.
_run = ("cgnat pool old\n inside 100.64.0.0/22\n"
        " outside 200.150.9.0/28\n block-size 512\n ports 1000 1100\n exit\n")
_out = io.StringIO()
_sh = shell_with(FakeApi(), running_text=_run)
_cand = parse_nested(_run + "interface pg1\n mtu 9000\n")
check(list(_cand.pools) == ["old"] and _cand.pools["old"].ports == "1000 1100",
      f"the fixture does not carry the standing pool: {list(_cand.pools)}")
with contextlib.redirect_stdout(_out):
    _ok = _sh.apply(_cand, check_only=True)
check(_ok is True,
      f"an unrelated change was refused over a pool the box already has, "
      f"which is how a box stops being configurable (#357): "
      f"{_out.getvalue()!r}")
print("  refused where it is asked for, and silent about what the box holds")

print("\n=== a claimed card is paired under its own name by default (#389) ===")
# MEASURED FIRST, on master: `Config.parse("interface ens19\n ipv4 address
# 203.0.113.1/24\n exit\n")` against an empty running configuration gives
# d["lcp_set"] == {} and d["lcp_del"] == [] - no `linux-name`, no pair, so
# FRR and `ip` had no ens19 at all. With `linux-name wan0` it gives
# {"ens19": "wan0"}. The default lands in the same dictionary: a card the
# dataplane holds - one with a `pci:` row under it in `show
# hardware-interfaces` - that has an `interface` block and no `linux-name`
# is paired under its own name, on the apply path, so `commit`, `rollback`
# and `--load` all make the pair and the candidate that is committed carries
# the statement.
HW_CARD = """              Name                Idx   Link  Hardware
ens19                              1     up   ens19
  Link speed: unknown
  Ethernet address 52:54:00:12:34:56
  Red Hat Virtio
    carrier up full duplex mtu 9000
    pci: device 1af4:1041 subsystem 1af4:1100 address 0000:00:13.00 numa 0
loop0                              2    down  loop0
  Link speed: unknown
local0                             0    down  local0
  Link speed: unknown
"""
IF_CARD = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
ens19                             1      up          9000/0/0/0
loop0                             2      up          9000/0/0/0
local0                            0     down          0/0/0/0
"""


class CardVpp(BootVpp):
    """A dataplane holding one claimed card, named after its Linux card."""

    def __init__(self, lcp="", ifaces=IF_CARD):
        super().__init__()
        self.lcp = lcp
        self.ifaces = ifaces

    def run(self, cmd):
        self.commands.append(cmd)
        if cmd.startswith("show hardware-interfaces"):
            return 0, HW_CARD
        if cmd.startswith("show interface"):
            return 0, self.ifaces
        if cmd.startswith("show lcp"):
            return 0, self.lcp
        if cmd.startswith("show ip fib"):
            return 0, self.fib
        return 0, ""


def card_shell(running_text="", lcp="", ifaces=IF_CARD):
    sh = boot_shell(running_text=running_text)
    sh.api = FakeDataplane(existing=("ens19", "loop0"))
    sh.dp = CardVpp(lcp, ifaces)
    return sh


def pairs_made(api):
    """(interface, host name, added?) for every pair call, in order."""
    return [(a["sw_if_index"], a["host_if_name"], a["is_add"])
            for m, a in api.calls if m == "lcp_itf_pair_add_del"]


_sh = card_shell()
_target = parse_nested("interface ens19\n ipv4 address "
                              "203.0.113.1/24\n exit\n")
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _ok = _sh.apply(_target)
check(_ok is True, f"the block on a card did not apply: {_buf.getvalue()!r}")
check(pairs_made(_sh.api) == [(1, "ens19", True)],
      f"the card was not paired under its own name: {pairs_made(_sh.api)}")
check(_target.linux_names == {"ens19": "ens19"},
      f"the default is not written into the configuration that was "
      f"committed, so `save` and `show lcp` would disagree: "
      f"{_target.linux_names}")
# NOT WRITTEN since #461: the pair is in the model, and the text says
# nothing a block does not.
check(" linux-name ens19" not in _target.text()
      and "interface ens19" in _target.text(),
      f"and the committed text does not carry the same-name line: "
      f"{_target.text()!r}")
print(f"  interface ens19, no linux-name: pairs {pairs_made(_sh.api)}")

# THE MAINTAINER'S TRANSCRIPT: `interface ens19 / no shutdown / commit`. Every
# device dictionary is empty after it - `no shutdown` discards from a set -
# and the block exists only as the FRR stanza the `interface` line staged.
# That block counts: the operator who typed it is the one expecting FRR to
# see the port.
_sh = card_shell()
_target = parse_nested("! routing\ninterface ens19\nexit\n")
check(not _target.interface_names() and _target.frr_interface_names()
      == ["ens19"],
      f"the fixture is not a block held only by the routing section: "
      f"{_target.interface_names()} {_target.frr_interface_names()}")
with contextlib.redirect_stdout(io.StringIO()):
    _ok = _sh.apply(_target)
check(_ok is True and pairs_made(_sh.api) == [(1, "ens19", True)],
      f"a block with nothing but its header did not pair the card: "
      f"{pairs_made(_sh.api)}")
print("  interface ens19 with nothing in it: paired all the same")

# AND A LOOPBACK, which is #404 and reverses what this box used to assert.
# It used to require NO pair here, on the reasoning that the default is a
# card's name put back where it came from and a loopback has none. Measured on
# the maintainer's box: a loopback with an address committed cleanly and
# `vtysh` called it a pseudo interface, so the one interface whose whole
# purpose is to carry the address FRR announces was the one FRR could not see.
# `loop0` is a name Linux never had - its own loopback is `lo` - so it is free
# for the same practical reason a claimed card's is.
_sh = card_shell()
_target = parse_nested("interface loop0\n ipv4 address "
                              "10.99.0.1/32\n exit\n")
with contextlib.redirect_stdout(io.StringIO()):
    _ok = _sh.apply(_target)
check(_ok is True and [p[1:] for p in pairs_made(_sh.api)]
      == [("loop0", True)],
      f"a loopback with a block is paired under its own name: "
      f"{pairs_made(_sh.api)}")
check(_target.linux_names.get("loop0") == "loop0",
      f"and the default is WRITTEN into the configuration, the way a card's "
      f"is, so the next commit does not remove it: {_target.linux_names!r}")
# The override still wins, for a loopback as for a card.
_sh = card_shell()
_target = parse_nested("interface loop0\n linux-name lo9\n exit\n")
with contextlib.redirect_stdout(io.StringIO()):
    _sh.apply(_target)
check([p[1:] for p in pairs_made(_sh.api)] == [("lo9", True)],
      f"and 'linux-name' on a loopback still names the pair: "
      f"{pairs_made(_sh.api)}")
print("  interface loop0: paired as itself, and the override still wins")

# THE OVERRIDE STAYS. `linux-name wan0` on the card names the pair, and on a
# card already carrying its default pair that is a RENAME: the dataplane
# answers "exists" to a second pair on one interface, so the old one has to
# go first. The running configuration is read off `show lcp`.
_sh = card_shell(running_text="interface ens19\n linux-name ens19\n exit\n",
                 lcp="itf-pair: [0] ens19 tap0 ens19 5 type tap\n")
_target = parse_nested("interface ens19\n linux-name wan0\n exit\n")
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _ok = _sh.apply(_target)
check(_ok is True, f"renaming the pair did not apply: {_buf.getvalue()!r}")
check(pairs_made(_sh.api) == [(1, "", False), (1, "wan0", True)],
      f"the old pair was not removed before the new one was made, which the "
      f"dataplane refuses: {pairs_made(_sh.api)}")
check(_target.linux_names == {"ens19": "wan0"},
      f"the override was overwritten by the default: {_target.linux_names}")
print(f"  linux-name wan0 over the default: pairs {pairs_made(_sh.api)}")

# AND A BOX WHOSE DATAPLANE CANNOT BE ASKED defaults nothing: every case
# above this section runs against a dataplane that answers "" to `show
# hardware-interfaces`, and every one of them expects no pair it did not ask
# for.
_sh = boot_shell()
_target = parse_nested("interface GigabitEthernet0/12/0\n ipv4 "
                              "address 203.0.113.1/24\n exit\n")
with contextlib.redirect_stdout(io.StringIO()):
    _sh.apply(_target)
check(pairs_made(_sh.api) == [],
      f"a dataplane with no card row still got a pair: {pairs_made(_sh.api)}")

print("\n=== the Linux end of a pair whose two ends carry one name (#389) ===")
# frr_acts_on() finds the Linux end by subtracting the dataplane's names from
# the pair's, and with both ends called ens19 the subtraction is empty. It
# must still answer: ens19 IS what FRR acts on, and the tap is not. Both
# shapes, because the old one is still what an unnamed device gets.
_old = routes.lcp_pairs("itf-pair: [0] GigabitEthernet0/13/0 tap0 vppwan0 "
                        "5 type tap\n")
_dp_old = {"GigabitEthernet0/13/0", "tap0", "local0"}
check(prismash.frr_side_names("GigabitEthernet0/13/0", _old, _dp_old)
      == {"vppwan0"},
      "the old shape: the Linux end is the name that is not the dataplane's")
check(prismash.frr_acts_on("vppwan0", _old, _dp_old) is True
      and prismash.frr_acts_on("GigabitEthernet0/13/0", _old, _dp_old) is False,
      "the old shape: FRR acts on vppwan0 and not on the dataplane's name")
_new = routes.lcp_pairs("itf-pair: [0] ens19 tap0 ens19 5 type tap\n")
_dp_new = {"ens19", "tap0", "local0"}
check(prismash.frr_side_names("ens19", _new, _dp_new) == {"ens19"},
      f"the same-name pair: the Linux end is ens19, not nothing: "
      f"{prismash.frr_side_names('ens19', _new, _dp_new)}")
check(prismash.frr_acts_on("ens19", _new, _dp_new) is True
      and prismash.frr_acts_on("tap0", _new, _dp_new) is False,
      "the same-name pair: FRR acts on ens19 and not on the tap")
print("  GigabitEthernet0/13/0 tap0 vppwan0 -> vppwan0; ens19 tap0 ens19 -> "
      "ens19")

print("\n=== --load on the upgraded box: the saved name is the card's old "
      "name (#389) ===")
# MEASURED FIRST, on master, with a file saying `interface
# GigabitEthernet0/13/0` against a box holding ens19: "error: interface
# GigabitEthernet0/13/0: this box has no such interface / it has: ens19,
# local0 / ... / error: refusing to load this file, the check above says
# why", and no API call at all - the box came up with nothing on it. The
# decision: the old name encodes the address, the box holds the card at
# that address under its Linux name, and the file is loaded under the new
# name with a note. Every place the file uses the name, not only the block:
# the CGNAT attachment, the FRR stanza, a sub-interface's tag kept.
SAVED_OLD = """nat
 cgnat
  interface GigabitEthernet0/13/0 inside
  exit
 exit
interface GigabitEthernet0/13/0
 ipv4 address 203.0.113.1/24
 exit
interface GigabitEthernet0/13/0.100
 vlan 100
 exit
! routing
interface GigabitEthernet0/13/0
 ip ospf area 0
exit
"""
_sh = card_shell()
_out = load_text(_sh, SAVED_OLD)
check("refusing to load this file" not in _out,
      f"the upgraded box refused its own saved configuration: {_out!r}")
check("note: GigabitEthernet0/13/0 is ens19 on this box" in _out,
      f"and did not say the name it loaded the file under: {_out!r}")
_addrs = [a for m, a in _sh.api.calls
          if m == "sw_interface_add_del_address"]
check(any(a.get("sw_if_index") == 1 for a in _addrs),
      f"the address did not land on ens19: {_sh.api.calls}")
_subs = [a for m, a in _sh.api.calls if m == "create_vlan_subif"]
check(len(_subs) == 1 and _subs[0].get("sw_if_index") == 1
      and _subs[0].get("vlan_id") == 100,
      f"the sub-interface did not keep its tag on the renamed parent: "
      f"{_subs}")
_feat = [a for m, a in _sh.api.calls
         if m == "bpa_interface_add_del_feature"]
check(any(a.get("sw_if_index") == 1 for a in _feat),
      f"the CGNAT attachment did not follow the rename: {_sh.api.calls}")
# And the sub-interface under its own name, since #461, in the same load
# that created it.
check(pairs_made(_sh.api) == [(1, "ens19", True), (1100, "ens19.100", True)],
      f"and the renamed card was not paired under its name: "
      f"{pairs_made(_sh.api)}")
print(f"  loaded: {_out[:160]!r}")

# A SAVED NAME THE BOX STILL HAS is not renamed: the device is unnamed on
# this box, the old name is what it holds, and what happens then is the trim
# below. The rename is decided by what the box holds, not by the shape.
class OldCardVpp(CardVpp):
    def run(self, cmd):
        self.commands.append(cmd)
        if cmd.startswith("show hardware-interfaces"):
            return 0, HW_CARD.replace("ens19", "GigabitEthernet0/13/0")
        if cmd.startswith("show interface"):
            return 0, IF_CARD.replace("ens19", "GigabitEthernet0/13/0")
        return super().run(cmd)


_sh = boot_shell()
_sh.api = FakeDataplane(existing=("GigabitEthernet0/13/0",))
_sh.dp = OldCardVpp()
_out = load_text(_sh, SAVED_OLD)
check("is ens19 on this box" not in _out,
      f"a name the box still holds was renamed anyway: {_out!r}")
# THE TRIM. FRR never held `interface GigabitEthernet0/13/0` - it refuses
# the name - so the file minus that stanza is what the box had, and the
# dataplane half loads rather than the whole file being refused over a
# block nothing was holding. Said out loud, because until the next `save`
# the file and the running configuration differ by it.
check("refusing to load this file" not in _out,
      f"the file was refused whole over an FRR stanza FRR never held: "
      f"{_out!r}")
check("note: interface GigabitEthernet0/13/0: its routing block was not "
      "loaded" in _out,
      f"and the trim was not said: {_out!r}")
check(any(m == "sw_interface_add_del_address" for m, _ in _sh.api.calls),
      f"the dataplane half was not loaded: {_sh.api.calls}")
check(pairs_made(_sh.api) == [],
      f"a name Linux cannot take was paired under itself: "
      f"{pairs_made(_sh.api)}")
print(f"  unnamed device: {_out[:160]!r}")

# TWO CARDS THAT DIFFER ONLY IN PCI DOMAIN cannot say which the old name
# was, and neither is guessed: the refusal that follows names what the box
# has, which is the state master left every such file in.
class TwinVpp(CardVpp):
    def run(self, cmd):
        self.commands.append(cmd)
        if cmd.startswith("show hardware-interfaces"):
            return 0, HW_CARD + HW_CARD.replace("ens19", "ens20").replace(
                "address 0000:00:13.00", "address 0001:00:13.00")
        return super().run(cmd)


_sh = boot_shell()
_sh.api = FakeDataplane(existing=("ens19", "ens20"))
_sh.dp = TwinVpp()
_out = load_text(_sh, SAVED_OLD)
check("is ens19 on this box" not in _out and "is ens20 on this box" not in _out,
      f"a twin in another domain and the box picked one: {_out!r}")
check("interface GigabitEthernet0/13/0: this box has no such interface" in _out
      and "refusing to load this file" in _out,
      f"and the file was not refused with what the box has: {_out!r}")
print("  a twin in another PCI domain: nothing guessed, the file refused")

print("\n=== a bus spelled with a letter first: the second socket's card "
      "(#389) ===")
# MEASURED FIRST, on 0dbbf73: routes.dpdk_name_address("TenGigabitEthernet"
# "d8/0/0") gave (8, 0, 0). The bus is hexadecimal, VPP prints it without
# padding, and a greedy run of letters took the 'd' for the last letter of
# the class word. On a box holding a card at 08:00.0 as well, the saved file
# was loaded onto THAT card, with a note saying so - the one thing
# card_renames is written never to do; on a box whose only card is at
# d8:00.0 the file was refused whole, the box up with nothing on it. Both
# are the dual-socket server, where the second socket's cards sit at 0xaf,
# 0xb1 and 0xd8.
HW_TWO_SOCKETS = """              Name                Idx   Link  Hardware
ens1f0                             1     up   ens1f0
  Link speed: 10 Gbps
  Ethernet address 3c:fd:fe:00:00:01
  Intel X710
    pci: device 8086:1572 subsystem 8086:0000 address 0000:08:00.00 numa 0
ens5f0                             2     up   ens5f0
  Link speed: 10 Gbps
  Ethernet address 3c:fd:fe:00:00:02
  Intel X710
    pci: device 8086:1572 subsystem 8086:0000 address 0000:d8:00.00 numa 1
local0                             0    down  local0
  Link speed: unknown
"""
IF_TWO_SOCKETS = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
ens1f0                            1      up          9000/0/0/0
ens5f0                            2      up          9000/0/0/0
local0                            0     down          0/0/0/0
"""
HW_D8_ONLY = """              Name                Idx   Link  Hardware
ens5f0                             1     up   ens5f0
  Link speed: 10 Gbps
  Ethernet address 3c:fd:fe:00:00:02
  Intel X710
    pci: device 8086:1572 subsystem 8086:0000 address 0000:d8:00.00 numa 1
local0                             0    down  local0
  Link speed: unknown
"""
IF_D8_ONLY = """              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
ens5f0                            1      up          9000/0/0/0
local0                            0     down          0/0/0/0
"""
SAVED_D8 = """nat
 cgnat
  interface TenGigabitEthernetd8/0/0 inside
  exit
 exit
interface TenGigabitEthernetd8/0/0
 ipv4 address 203.0.113.1/24
 exit
"""


class SocketsVpp(CardVpp):
    """The cards this box holds, given as the two screens --load reads."""

    def __init__(self, hw, iface):
        super().__init__()
        self.hw, self.iface = hw, iface

    def run(self, cmd):
        if cmd.startswith("show hardware-interfaces"):
            self.commands.append(cmd)
            return 0, self.hw
        if cmd.startswith("show interface"):
            self.commands.append(cmd)
            return 0, self.iface
        return super().run(cmd)


_sh = boot_shell()
_sh.api = FakeDataplane(existing=("ens1f0", "ens5f0"))
_sh.dp = SocketsVpp(HW_TWO_SOCKETS, IF_TWO_SOCKETS)
_out = load_text(_sh, SAVED_D8)
check("note: TenGigabitEthernetd8/0/0 is ens5f0 on this box" in _out,
      f"the card at d8:00.0 is ens5f0 and the note does not say so: {_out!r}")
check(not any(a.get("sw_if_index") == 1 for _, a in _sh.api.calls),
      f"a call reached ens1f0, the card at 08:00.0: the file was loaded "
      f"onto the wrong card: {_sh.api.calls}")
_addrs = [a for m, a in _sh.api.calls
          if m == "sw_interface_add_del_address"]
check(any(a.get("sw_if_index") == 2 for a in _addrs),
      f"the address did not land on ens5f0: {_sh.api.calls}")
print("  ens1f0 at 08:00.0 and ens5f0 at d8:00.0: the file goes to ens5f0")

# And the second socket's card ALONE, where the misread found no card at
# bus 8 and refused the file - the state the CHANGELOG says is ruled out.
_sh = boot_shell()
_sh.api = FakeDataplane(existing=("ens5f0",))
_sh.dp = SocketsVpp(HW_D8_ONLY, IF_D8_ONLY)
_out = load_text(_sh, SAVED_D8)
check("refusing to load this file" not in _out,
      f"the only card is at d8:00.0 and the file naming it was refused: "
      f"{_out!r}")
check("note: TenGigabitEthernetd8/0/0 is ens5f0 on this box" in _out,
      f"and the note does not name the card it loaded onto: {_out!r}")
check(any(a.get("sw_if_index") == 1 for m, a in _sh.api.calls
          if m == "sw_interface_add_del_address"),
      f"the address did not land on the box's one card: {_sh.api.calls}")
print("  ens5f0 alone at d8:00.0: the file loads")

print("\n=== the Linux end of the same-name pair, on every reader of it "
      "(#389) ===")
# Three readers here found the Linux end by `name != ifname`: right on
# `GigabitEthernet0/13/0 tap0 vppwan0`, and on `ens19 tap0 ens19` it skips
# the one name that IS the Linux end and keeps the TAP, which Linux has no
# netdev by. Measured on 0dbbf73 with the calls recorded: set_host_mtu ran
# `ip link set dev tap0 mtu 1400` and never ens19, set_admin `ip link set
# dev tap0 up`, set_description `ip link set dev tap0 alias uplink` - each
# failing quietly, on every claimed card with a block. routes.linux_end() is
# the one rule now, and each reader is held on both shapes.
SAME_LCP = "itf-pair: [0] ens19 tap0 ens19 5 type tap\n"
OLD_LCP = "itf-pair: [0] GigabitEthernet0/13/0 tap0 vppwan0 5 type tap\n"


def ip_devs(fn, *args):
    """The `dev` every `ip` call named while fn ran, in order."""
    ran = []

    def fake_run(argv, **kw):
        ran.append(list(argv))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    real = prismash.subprocess.run
    prismash.subprocess.run = fake_run
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fn(*args)
    finally:
        prismash.subprocess.run = real
    return [argv[argv.index("dev") + 1] for argv in ran if "dev" in argv]


for _lcp, _card, _linux in ((SAME_LCP, "ens19", "ens19"),
                            (OLD_LCP, "GigabitEthernet0/13/0", "vppwan0")):
    for _label, _verb in (
            ("set_host_mtu", lambda sh, c=_card: sh.set_host_mtu(c, 1400)),
            ("set_admin", lambda sh, c=_card: sh.set_admin(c, True)),
            ("set_description",
             lambda sh, c=_card: sh.set_description(c, "uplink"))):
        _sh = card_shell(lcp=_lcp)
        _sh.api = FakeDataplane(existing=(_card,))
        _devs = ip_devs(_verb, _sh)
        check(_devs == [_linux],
              f"{_label} on {_card}: ip ran on {_devs}, not on "
              f"[{_linux!r}] alone")
print("  set_host_mtu, set_admin, set_description: ip runs on ens19, never "
      "on tap0")

# KEYED BY THE PAIR'S TAP. `interface tap0` is a block the parser takes and
# apply refuses nowhere: the TAP is an interface the dataplane has, and
# `vppctl create tap id 0` makes a tap0 that is nobody's pair, so the shape
# alone cannot be refused (drop_lcp_taps says why). So the three mirrors
# are asked with the TAP too, and without the dataplane's names
# routes.linux_end() took the TAP as the interface's own and answered the
# CARD. Measured on e44f7c0, old shape: `interface tap0 / description
# uplink / shutdown` ran `ip link set dev GigabitEthernet0/13/0 down` and
# `... alias uplink` - a name Linux has no netdev by - and vppwan0 was
# never touched; on 0dbbf73 the same block at least reached vppwan0. On
# the override pair it ran on ens19 where the Linux end is wan0. Each
# mirror hands linux_end() the names `show interface` lists now, so the
# TAP is placed and `ip` runs on the Linux end alone.
OVERRIDE_LCP = "itf-pair: [0] ens19 tap0 wan0 5 type tap\n"


def if_listing(*names):
    """`show interface` holding exactly these names, plus local0."""
    head = ("              Name               Idx    State  "
            "MTU (L3/IP4/IP6/MPLS)     Counter          Count\n")
    rows = "".join(f"{n:<34}{i:<7}up          9000/0/0/0\n"
                   for i, n in enumerate(names, start=1))
    return head + rows + "local0                            0     down" \
        "          0/0/0/0\n"


for _lcp, _card, _linux in ((OLD_LCP, "GigabitEthernet0/13/0", "vppwan0"),
                            (OVERRIDE_LCP, "ens19", "wan0")):
    for _label, _verb in (
            ("set_host_mtu", lambda sh: sh.set_host_mtu("tap0", 1400)),
            ("set_admin", lambda sh: sh.set_admin("tap0", True)),
            ("set_description",
             lambda sh: sh.set_description("tap0", "uplink"))):
        _sh = card_shell(lcp=_lcp, ifaces=if_listing(_card, "tap0"))
        _sh.api = FakeDataplane(existing=(_card, "tap0"))
        _devs = ip_devs(_verb, _sh)
        check(_devs == [_linux],
              f"{_label} keyed by tap0 on {_lcp.split()[2]} tap0 "
              f"{_lcp.split()[4]}: ip ran on {_devs}, not on "
              f"[{_linux!r}] alone")
print("  keyed by tap0: ip runs on vppwan0 on the old shape and on wan0 on "
      "the override, never on the card")

print("\n=== `no linux-name` on a claimed card: the default returns at "
      "commit, said at the prompt (#389) ===")
# The candidate drops the name and pair_cards puts the default straight back
# at apply - measured on 0dbbf73: linux_names {'ens19': 'ens19'} after the
# pop and one apply. Typed to take an override back, that is what the
# operator wants; typed to unpair the card, it does nothing, and silently.
# There is no statement that unpairs a card with a block, and `no interface
# <card>` is refused, so the prompt says which of the two it is.
_sh = card_shell()
_sh.candidate = parse_nested("interface ens19\n linux-name wan0\n"
                                    " exit\n")
_sh.mode = "config"
_sh.frr_ctx = ["interface ens19"]
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _rv = _sh.do_config("no linux-name")
check(_rv is True and "ens19" not in _sh.candidate.linux_names,
      f"`no linux-name` was not taken: {_rv} {_sh.candidate.linux_names}")
check("warning: no linux-name: the default returns at commit"
      in _buf.getvalue().splitlines(),
      f"the prompt did not say the default comes back: {_buf.getvalue()!r}")
check("ens19 is a card with an interface block" in flat(_buf.getvalue())
      and "does not unpair" in _buf.getvalue(),
      f"and did not name the card, or say what the statement is not: "
      f"{_buf.getvalue()!r}")
# A LOOPBACK IS WARNED TOO SINCE #404: it has a default now, so taking an
# override back on one leaves the pair standing, and an operator told nothing
# would read that as an unpair. Said from the CANDIDATE, because naming a
# loopback is what creates it - one typed a moment ago is on no dataplane to
# be asked about.
_sh = card_shell()
_sh.candidate = parse_nested("interface loop0\n linux-name lo9\n"
                                    " exit\n")
_sh.candidate.loopbacks.add("loop0")
_sh.mode = "config"
_sh.frr_ctx = ["interface loop0"]
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _sh.do_config("no linux-name")
check("the default returns at commit" in _buf.getvalue()
      and "loop0 is a loopback with an interface block" in flat(_buf.getvalue()),
      f"a loopback was not told its default comes back: "
      f"{_buf.getvalue()!r}")
# And a sub-interface still gets no default, so `no linux-name` on one really
# does unpair it and a warning there would be false.
_sh = card_shell()
_sh.candidate = parse_nested("interface ens19.100\n vlan 100\n"
                                    " linux-name sub9\n exit\n")
_sh.mode = "config"
_sh.frr_ctx = ["interface ens19.100"]
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _sh.do_config("no linux-name")
check("warning" not in _buf.getvalue(),
      f"a sub-interface, which gets no default, was warned about one: "
      f"{_buf.getvalue()!r}")
print("  ens19 and loop0: the default returns, said; a sub-interface: not")


print("\n=== a commit that changes only a description is applied (#436) ===")
# FRR has no field for a static route's description, so the box keeps it in
# its own file and commit writes it. A description is its own delta: without
# `route_desc_changed` a commit that changes only a description has nothing
# in it to apply, reports success, and the description is gone at the next
# read. Mutation shown red: the write in apply() removed.
_desc_tmp = tempfile.mkdtemp(prefix="static-desc-")
_saved_desc = prismash.STATIC_DESC_PATH
prismash.STATIC_DESC_PATH = os.path.join(_desc_tmp, "static-route-descriptions")
try:
    _running = "! routing\nip route 10.0.0.0/8 1.2.3.4\n"
    _sh = shell_with(FakeApi(), _running)
    _target = parse_nested(_running)
    _target.static_descriptions = {"ip route 10.0.0.0/8 1.2.3.4": "to POP-1"}
    _ok = _sh.apply(_target)
    with open(prismash.STATIC_DESC_PATH) as _f:
        _written = _f.read()
    check(_ok and _written == "ip route 10.0.0.0/8 1.2.3.4\tto POP-1\n",
          f"the description is written by the commit that changes only it: "
          f"{_ok} {_written!r}")
finally:
    prismash.STATIC_DESC_PATH = _saved_desc
print("  a description-only commit reaches the box")


print("\n=== dns servers reach resolv.conf at commit, and are read back (#437) ===")
# Mutation shown red: the write in apply() removed - the commit reports
# success and the box asks the resolvers it had.
_dns_root = tempfile.mkdtemp(prefix="dns-apply-")
os.makedirs(os.path.join(_dns_root, "etc", "prisma-routing"))
with open(os.path.join(_dns_root, "etc", "resolv.conf"), "w") as _f:
    _f.write("nameserver 192.0.2.53\n")
_sh = shell_with(FakeApi(), "")
_sh.sys = system.System(root=_dns_root)
_target = parse_nested("dns server 1.1.1.1\ndns server 9.9.9.9\n")
_ok = _sh.apply(_target)
check(_ok and _sh.sys.read_dns() == ["1.1.1.1", "9.9.9.9"],
      f"the servers are what the box now asks: {_ok} {_sh.sys.read_dns()!r}")
print("  a commit of `dns server` rewrites resolv.conf and reads back")


print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
