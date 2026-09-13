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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import config      # noqa: E402
import hexash      # noqa: E402
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


class FakeVpp:
    """A dataplane that accepts every CLI command, including validation."""

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
    sh = hexash.Shell()
    sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT} if caps is None else caps
    sh.api = api
    sh.dp = FakeVpp()
    sh.frr = type("F", (), {"available": lambda self: False,
                            "reload": lambda self, l, check_only=False: (0, "")})()
    sh._running_text = running_text
    sh.running_config = lambda: config.Config.parse(running_text)
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
sh = shell_with(api, caps={hexash.CAP_CGNAT})
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
    "the spelling hexash uses must not be")
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
                             shell="/usr/bin/hexash-login")

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
          "noc:x:1000:1000::/home/noc:/usr/bin/hexash-login\n")
GROUPS_NONE = "vpp:x:900:\nfrr:x:901:\nfrrvty:x:902:\n" \
              "hexa-admin:x:903:\nhexa-cgnat:x:904:\nnoc:x:1000:\n"

print("\n=== the role is read back from the box, not remembered ===")
# #161's lesson. A role kept in the saved configuration goes on being reported
# after somebody removes the membership by hand, and the VTY ends up describing
# an account it no longer describes.
tmp = tempfile.mkdtemp()
box = FakeBox(tmp, PASSWD, GROUPS_NONE.replace("hexa-admin:x:903:",
                                               "hexa-admin:x:903:noc"))
u = box.system().read_users()["noc"]
check(u.role == system.ROLE_ADMIN,
      f"membership of hexa-admin must read back as a role: {u.role!r}")
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
                      {"hexa-admin", "hexa-cgnat", "vpp", "frr", "frrvty"}),
                     (system.ROLE_CGNAT,
                      {"hexa-cgnat", "vpp", "frr", "frrvty"}),
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
              GROUPS_NONE.replace("hexa-admin:x:903:", "hexa-admin:x:903:noc")
                         .replace("hexa-cgnat:x:904:", "hexa-cgnat:x:904:noc"))
had = system.User("noc")
had.role = system.ROLE_ADMIN
ok, msgs = box.system().apply_users({"noc": system.User("noc")}, {"noc": had})
check(ok, f"the demotion must apply: {msgs}")
check(["gpasswd", "-d", "noc", "hexa-admin"] in box.calls,
      f"hexa-admin must be taken away: {box.calls}")
check(["gpasswd", "-d", "noc", "hexa-cgnat"] in box.calls,
      f"and hexa-cgnat with it: {box.calls}")
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
              refuse={"hexa-admin"})
want = system.User("noc")
want.role = system.ROLE_ADMIN
ok, msgs = box.system().apply_users({"noc": want}, {"noc": system.User("noc")})
check(ok is False, "a role that could not be granted must fail the commit")
check(any("hexa-admin" in m for m in msgs),
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
    ok = sh.apply(config.Config.parse(DEMOTED), check_only=True)
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
    ok = sh.apply(config.Config.parse(DEMOTED + "user two\n exit\n"),
                  check_only=True)
check(ok is True,
      f"a box with no administrator already must still commit: {buf.getvalue()!r}")

# And a demotion that leaves another administrator behind is not refused, or
# the refusal would make demotion impossible rather than safe.
TWO = RUNNING + f"user two\n role administrator\n ssh-key {KEY}\n exit\n"
sh = shell_with(FakeApi(), running_text=TWO)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ok = sh.apply(config.Config.parse(
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
sh.feed("nat cgnat pool residential")
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
# under it - and hexarouting.service replays that file at every boot, on a
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
    ok = sh.apply(config.Config.parse(SAVED_LOOPBACK))
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
    sh.apply(config.Config.parse("""interface host-veth-vpp
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

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
