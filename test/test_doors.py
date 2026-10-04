"""
src/prismash/doors.py (#448, phase B): the host's doors.

Four things, each shown red by a mutation:

  * every command system.py runs is a door - read off the source, so a
    `self._run([...])` added without one fails here and not on a box where
    the daemon refuses it (mutation: drop "restart snmpd" from FIXED);
  * nothing else is: an option where an account goes, another shell, a group
    the VTY does not manage, root where the VTY never sends it, a second line
    for chpasswd, stdin on a door that takes none (mutation: accept any
    account name);
  * the uid decides, not the request: uid 1000 is refused what uid 0 is
    given (mutation: answer() without the uid check);
  * the socket carries it: a real daemon on a unix socket, asked by the
    client the VTY uses, answers, and a client with no daemon runs the
    command itself.
"""
import ast
import json
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src", "prismash")
sys.path.insert(0, SRC)
import doors  # noqa: E402
from system import (LOGIN_SHELL, MANAGED_GROUPS, GROUP_ADMIN, SNMPD_USER,  # noqa: E402
                    SET_HOSTNAME, SNMPD_CONF)

fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


print("=== every command system.py runs is a door ===")
# Each argument slot is filled with what the VTY puts there: an account for
# `name`, every managed group for `g`, the login shell for `self.shell`. A
# slot this does not know is a failure, so a new kind of argument is looked
# at before it is let through.
# In prismash.py the doors are `self.host_run([...])`: the Linux end of a
# dataplane interface (`host`), its MTU, `up`/`down`, and a description.
SLOTS = {"name": ["ops"], "g": list(MANAGED_GROUPS), "host": ["tap4096", "ens19"],
         "SNMPD_USER": [SNMPD_USER], "SET_HOSTNAME": [SET_HOSTNAME],
         # A tenant VRF's device (#540), and the one a member goes into.
         "vrf": ["red"], "want": ["red"]}


def slot(e):
    """What the VTY puts in one argv slot, or None when this does not know."""
    if isinstance(e, ast.Constant):
        return [e.value]
    if isinstance(e, ast.Name) and e.id in SLOTS:
        return SLOTS[e.id]
    if isinstance(e, ast.Attribute) and e.attr == "shell":
        return [LOGIN_SHELL]
    if isinstance(e, ast.IfExp):
        a, b = slot(e.body), slot(e.orelse)
        return a + b if a is not None and b is not None else None
    if isinstance(e, ast.Call) and isinstance(e.func, ast.Name) \
            and e.func.id == "str" and ast.unparse(e.args[0]) == "mtu":
        return ["68", "1500", "9000"]
    if isinstance(e, ast.BoolOp) and ast.unparse(e) == "text or ''":
        return ["uplink to the transit", ""]
    u = ast.unparse(e)
    return {"verb": ["reboot", "poweroff"],
            "self.TUNE_UNIT": ["prisma-routing-tune"],
            "gw": ["192.0.2.1", "2001:db8::1"],
            "str(table)": ["10", "4294967295"],
            "mgmt['name']": ["ens18"],
            "self.conf_on_host": [SNMPD_CONF]}.get(u)


# In prismash.py, `self.sys._run` and `self.snmp._run` are the doors as well:
# the first cut read system.py only and missed `system reboot`, which a box
# with the daemon would have refused root.
sites = []
for src, attr in (("system.py", "_run"), ("prismash.py", "host_run"),
                  ("prismash.py", "_run")):
    tree = ast.parse(open(os.path.join(SRC, src)).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == attr and node.args \
                and isinstance(node.args[0], ast.List):
            choices = [[]]
            for e in node.args[0].elts:
                vals = slot(e)
                if vals is None:
                    choices = None
                    break
                choices = [c + [v] for c in choices for v in vals]
            stdin = None
            if any(k.arg == "stdin" for k in node.keywords):
                stdin = "ops:$6$salt$hash\n"
            sites.append((f"{src}:{node.lineno}", choices, stdin))
check(len([x for x in sites if x[0].startswith("system")]) >= 12,
      "at least 12 command sites read off system.py")
check(len([x for x in sites if x[0].startswith("prismash")]) >= 10,
      "and the 10 sites off prismash.py: ip-link, systemctl, the route")
for where, choices, stdin in sites:
    if choices is None:
        check(False, f"{where}: an argument this suite does not know")
        continue
    for argv in choices:
        # A question any account may ask (the box's name, #516) is a door
        # too, of the kind answer() takes before verb_of.
        check(doors.verb_of(argv, stdin) is not None
              or (stdin is None and tuple(argv) in doors.QUERIES),
              f"{where}: {' '.join(argv)} is a door")

print("\n=== and nothing else is ===")
for argv, stdin, why in (
        (["userdel", "-r", "--help"], None, "an option where an account goes"),
        (["userdel", "-r", "root"], None, "root, which the VTY never removes"),
        (["useradd", "-m", "-s", "/bin/bash", "ops"], None, "another shell"),
        (["usermod", "-s", "/bin/sh", "ops"], None, "another shell again"),
        (["gpasswd", "-a", "ops", "sudo"], None, "a group the VTY does not manage"),
        (["gpasswd", "-a", "ops", "root"], None, "the root group"),
        (["gpasswd", "-a", "../x", GROUP_ADMIN], None, "a path for an account"),
        (["chpasswd", "-e"], "ops:$6$a\nroot:$6$b\n", "a second chpasswd line"),
        (["chpasswd", "-e"], "ops:plain\n", "a password that is not a hash"),
        (["chpasswd"], "ops:plain\n", "chpasswd without -e"),
        (["systemctl", "restart", "ssh"], None, "a unit it does not name"),
        (["systemctl", "reload", "ssh"], "x", "stdin on a door with none"),
        (["sh", "-c", "id"], None, "a shell"),
        (["useradd", "-m", "-s", LOGIN_SHELL, "a" * 33], None, "33 characters"),
        ("userdel -r ops", None, "a string, not an argv"),
        ([1, 2], None, "not strings"),
        (["ip", "link", "set", "dev", "-x", "up"], None, "an option for a netdev"),
        (["ip", "link", "set", "dev", "a" * 16, "up"], None, "16 characters"),
        (["ip", "link", "set", "dev", "../x", "up"], None, "a path for a netdev"),
        (["ip", "link", "set", "dev", "ens19", "mtu", "67"], None, "MTU 67"),
        (["ip", "link", "set", "dev", "ens19", "mtu", "1e3"], None, "MTU not a number"),
        (["ip", "link", "set", "dev", "ens19", "mtu", "\u0661\u0665\u0660\u0660"], None,
         "MTU in digits int() takes and is_number() does not"),
        (["ip", "link", "set", "dev", "ens19", "alias", "a\nb"], None, "an alias with a newline"),
        (["ip", "link", "set", "dev", "ens19", "alias", "x" * 256], None, "an alias of 256"),
        (["ip", "link", "set", "dev", "ens19", "netns", "1"], None, "another ip-link verb"),
        (["ip", "link", "del", "ens19"], None, "a deletion"),
        ([SET_HOSTNAME, "-f"], None, "an option for a hostname (#516)"),
        ([SET_HOSTNAME, "a;b"], None, "a hostname with a ';'"),
        ([SET_HOSTNAME, "9box"], None, "a hostname FRR would refuse"),
        ([SET_HOSTNAME, "a" * 65], None, "a hostname of 65"),
        ([SET_HOSTNAME, "box", "x"], None, "a second argument"),
        (["hostname", "evil"], None, "renaming through hostname(1)"),
        (["ip", "route", "replace", "default", "via", "gw", "dev", "ens18"],
         None, "a gateway that is not an address"),
        (["ip", "route", "replace", "10.0.0.0/8", "via", "192.0.2.1", "dev",
          "ens18"], None, "a route that is not the default"),
        (["systemctl", "restart", "sshd"], None, "a unit restart it never asks"),
        (["systemctl", "reboot", "--force"], None, "a reboot that skips the drain")):
    check(doors.verb_of(argv, stdin) is None, f"refused: {why}")
check(doors.verb_of(["useradd", "-m", "-s", LOGIN_SHELL, "a" * 32]) is not None,
      "and 32 characters is an account, useradd(8)'s limit")

print("\n=== the uid decides ===")
ran = []


def fake(argv, stdin, timeout):
    ran.append(argv)
    return 0, "done"


req = json.dumps({"argv": ["systemctl", "restart", "snmpd"]})
r = doors.answer(req, 1000, execute=fake)
check(r["rc"] == 126 and "only root" in r["out"] and not ran,
      f"uid 1000 is refused and nothing runs: {r}")
r = doors.answer(req, 0, execute=fake)
check(r == {"rc": 0, "out": "done"} and ran, f"uid 0 is given it: {r}")
ran.clear()
r = doors.answer(json.dumps({"argv": ["sh", "-c", "id"]}), 0, execute=fake)
check(r["rc"] == 126 and not ran, f"and not even root gets a non-door: {r}")
# snmpd.conf carries the communities (#525): read for root, as on the host,
# and for nobody else - and only that file.
ran.clear()
r = doors.answer(json.dumps({"argv": ["cat", SNMPD_CONF]}), 1000, execute=fake)
check(r["rc"] == 126 and "only root" in r["out"] and not ran,
      f"snmpd.conf is not read for uid 1000: {r}")
r = doors.answer(json.dumps({"argv": ["cat", SNMPD_CONF]}), 0, execute=fake)
check(r == {"rc": 0, "out": "done"} and ran == [["cat", SNMPD_CONF]],
      f"and is read for root: {r}")
ran.clear()
r = doors.answer(json.dumps({"argv": ["cat", "/etc/shadow"]}), 0, execute=fake)
check(r["rc"] == 126 and not ran, f"and no other file is, even for root: {r}")
# The VTY on an upgraded release (#528): root's, detached through
# systemd-run, and exactly the one program - a caller that could name
# another would have a root shell a few seconds later.
_rv = list(doors.REFRESH_VTY_ARGV)
ran.clear()
r = doors.answer(json.dumps({"argv": _rv}), 1000, execute=fake)
check(r["rc"] == 126 and "only root" in r["out"] and not ran,
      f"restarting the VTY is refused to uid 1000: {r}")
r = doors.answer(json.dumps({"argv": _rv}), 0, execute=fake)
check(r == {"rc": 0, "out": "done"} and ran == [_rv]
      and _rv[0] == "systemd-run" and _rv[-1] == doors.REFRESH_VTY,
      f"and started for root, detached, on the host: {r} {ran}")
ran.clear()
r = doors.answer(json.dumps({"argv": _rv[:-1] + ["/bin/sh"]}), 0,
                 execute=fake)
check(r["rc"] == 126 and not ran,
      f"and systemd-run is a door for that program only: {r}")
check(doors.runner("/no/such/door.sock").door == "/no/such/door.sock",
      "a runner says which door it asks, for whoever must know who answered")
r = doors.answer("{not json", 0, execute=fake)
check(r["rc"] == 126 and not ran, "a request that is not JSON is refused")
doors.answer(json.dumps({"argv": ["systemctl", "restart", "snmpd"],
                         "timeout": 10 ** 6}), 0,
             execute=lambda a, s, t: ran.append(t) or (0, ""))
check(ran == [doors.MAX_TIMEOUT], f"a timeout is held to {doors.MAX_TIMEOUT}s: {ran}")

print("\n=== over a real socket, with the client the VTY uses ===")
d = tempfile.mkdtemp()
path = os.path.join(d, "run", "doors.sock")
ready = threading.Event()
me = os.getuid()
threading.Thread(target=doors.serve, daemon=True,
                 kwargs={"path": path, "allowed_uids": (me,),
                         "execute": fake, "ready": ready}).start()
check(ready.wait(5), "the daemon listens")
check(oct(os.stat(path).st_mode & 0o777) == "0o666",
      "the socket is 0666: who may is decided per request, by SO_PEERCRED")
run = doors.runner(path)
rc, out = run(["systemctl", "restart", "snmpd"])
check((rc, out) == (0, "done"), f"a door is answered: {(rc, out)}")
rc, out = run(["sh", "-c", "id"])
check(rc == 126, f"a non-door is refused by the daemon: {(rc, out)}")
rc, out = run(["chpasswd", "-e"], stdin="ops:$6$salt$hash\n")
check(rc == 0, "stdin crosses the socket")
threading.Thread(target=doors.serve, daemon=True,
                 kwargs={"path": path + "2", "allowed_uids": (0,),
                         "execute": fake}).start()
time.sleep(0.3)
if me != 0:
    rc, out = doors.runner(path + "2")(["systemctl", "restart", "snmpd"])
    check(rc == 126 and "only root" in out,
          f"the uid the daemon reads is the caller's own: {(rc, out)}")
rc, out = doors.runner(os.path.join(d, "nothing.sock"))(["true"])
check(rc == 0, "no daemon: the command is run here, as before the doors")


print("\n=== the pieces, one at a time ===")
import socket  # noqa: E402
check(doors._account("ops") and not doors._account("root")
      and doors._account("root", root_ok=True) and not doors._account("-x"),
      "_account: a name, not root unless asked, never an option")
rc, out = doors.run_locally(["sh", "-c", "echo hi; exit 3"])
check((rc, out) == (3, "hi\n"), f"run_locally: rc and output: {(rc, out)}")
check(doors.run_locally(["/nonexistent/x"])[0] == 127,
      "run_locally: a missing program is 127")
check(doors.run_locally(["sleep", "5"], timeout=0.2)[0] == 124,
      "run_locally: a timeout is 124")
a, b = socket.socketpair()
a.sendall(b'{"x": 1}\n')
check(doors._read_line(b) == '{"x": 1}\n', "_read_line: one message, to its newline")
a.sendall(b'{"half')
a.close()
check(doors._read_line(b) == '{"half', "_read_line: a peer that hangs up ends the read")
b.close()
a, b = socket.socketpair(socket.AF_UNIX)
check(doors.peer_uid(a) == os.getuid(),
      f"peer_uid: the kernel's answer is this process's uid ({os.getuid()})")
a.sendall(json.dumps({"argv": ["systemctl", "enable", "snmpd"]}).encode() + b"\n")
doors.handle(b, (os.getuid(),), fake)
got = json.loads(doors._read_line(a))
check(got == {"rc": 0, "out": "done"}, f"handle: one request, one answer: {got}")
a.close()


import io, contextlib  # noqa: E401,E402
_o = io.StringIO()
with contextlib.redirect_stdout(_o):
    doors.say(json.dumps({"argv": ["chpasswd", "-e"],
                          "stdin": "ops:$6$SECRETHASH\n"}), 0, {"rc": 0})
check("door: uid 0: chpasswd -e -> 0" in _o.getvalue()
      and "SECRETHASH" not in _o.getvalue(),
      f"say: the argv, the uid and the result, never stdin: {_o.getvalue()!r}")


print("\n=== every file system.py writes is a file door, as the door writes it ===")
# Driven, not read off the source: the paths are expressions (root + a
# constant, a home), so the flows are run against a scratch root with a
# LocalFiles that records each write - relative to that root, which is the
# path the door would be sent on a box - and then does it.
import system  # noqa: E402


class RecFiles:
    """A LocalFiles that records each write, relative to `root`, then does
    it. By __getattr__ and not by overriding: a test that defines `unlink`
    would stop test_every_function seeing the product's."""

    def __init__(self, root):
        self.root, self.did, self.real = root, [], system.LocalFiles()

    def __getattr__(self, op):
        fn = getattr(self.real, op)

        def rec(path, *a, **kw):
            rel = path[len(self.root):]
            if op == "copy":
                self.did.append(("copy", rel, a[0][len(self.root):], None))
            elif op == "write":
                self.did.append(("write", rel, kw.get("mode", a[1] if len(a) > 1 else None),
                                 kw.get("atomic", a[2] if len(a) > 2 else False)))
            elif op == "append":
                self.did.append(("append", rel, kw.get("mode", a[1] if len(a) > 1 else None), None))
            else:
                self.did.append(({"write_keys": "keys"}.get(op, op), rel, None, None))
            return fn(path, *a, **kw)
        return rec


root = tempfile.mkdtemp()
os.makedirs(root + "/etc", exist_ok=True)
with open(root + "/etc/resolv.conf", "w") as f:
    f.write("nameserver 10.0.2.3\n")
rec = RecFiles(root)
sy = system.System(runner=lambda a, stdin=None, timeout=30: (0, ""),
                   root=root, files=rec)
sy.write_dns(["192.0.2.1"])
sy.write_dns([])
sy.write_ssh({"port": "2222"})
sy.write_ssh({"port": "2223"})
u = system.User("ops")
u.keys = ["ssh-ed25519 AAAA ops"]
sy._write_keys("ops", u.keys)
sy._write_keys("root", u.keys)
os.makedirs(root + "/run/systemd/system", exist_ok=True)
sn = system.SnmpDaemon(runner=lambda a, stdin=None, timeout=30: (0, ""),
                       root=root, files=rec, agent="/x/agent")
v3 = system.SnmpUser("zed")
v3.auth, v3.priv = "sha passphrase1", "aes passphrase2"
sn.apply({"enabled": "yes"}, ["public"], {"zed": v3})
sn.apply({"enabled": "no"}, [], {})
ops = {d[0] for d in rec.did}
check(ops >= {"write", "append", "unlink", "copy", "mkdir", "keys"},
      f"the flows reached every kind of write: {sorted(ops)}")
for op, path, a, b in rec.did:
    req = {"file": op, "path": path, "text": ""}
    if op == "copy":
        req["dst"] = a
    verb = doors.file_verb(req)
    if verb is None:
        check(False, f"{op} {path}: not a file door")
        continue
    how = verb[2]
    if op == "write":
        check((how["mode"], how["atomic"]) == (a, b),
              f"write {path}: the door writes it as system.py did "
              f"(mode {oct(a) if a else a}, atomic {b})")
    elif op == "append":
        check(how["mode"] == a, f"append {path}: mode {oct(a)}")
    else:
        check(True, f"{op} {path} is a file door")

print("\n=== and no other file ===")
for req, why in (
        ({"file": "write", "path": "/etc/shadow", "text": "x"}, "the shadow file"),
        ({"file": "write", "path": "/etc/resolv.conf/../shadow", "text": "x"},
         "a path through the one allowed"),
        ({"file": "unlink", "path": "/etc/resolv.conf"}, "an unlink it never does"),
        ({"file": "copy", "path": "/etc/resolv.conf", "dst": "/tmp/x"},
         "a copy to elsewhere"),
        ({"file": "keys", "path": "/home/../etc", "text": "x"}, "a home that climbs"),
        ({"file": "keys", "path": "/etc", "text": "x"}, "a home that is not one"),
        ({"file": "write", "path": system.SSHD_DROPIN}, "a write with no text"),
        ({"file": "chmod", "path": system.SSHD_DROPIN}, "an operation it has not")):
    check(doors.file_verb(req) is None, f"refused: {why}")
r = doors.answer(json.dumps({"file": "mkdir", "path": "/run/sshd"}), 1000,
                 files=RecFiles(root))
check(r["rc"] == 126 and "only root" in r["out"], f"and the uid decides: {r}")

print("\n=== authorized_keys is never written through a link ===")
home = tempfile.mkdtemp()
os.symlink(root, home + "/.ssh")
try:
    system.LocalFiles().write_keys(home, "k\n")
    check(False, "a .ssh that is a link was written through")
except OSError:
    check(not os.path.exists(root + "/authorized_keys"),
          "a .ssh that is a link is refused, and nothing lands where it points")
home2 = tempfile.mkdtemp()
os.makedirs(home2 + "/.ssh")
os.symlink(root + "/target", home2 + "/.ssh/authorized_keys")
try:
    system.LocalFiles().write_keys(home2, "k\n")
    check(False, "an authorized_keys that is a link was written through")
except OSError:
    check(not os.path.exists(root + "/target"),
          "an authorized_keys that is a link is refused")

print("\n=== a file door over the socket ===")
fpath = os.path.join(d, "run", "files.sock")
ready2 = threading.Event()
threading.Thread(target=doors.serve, daemon=True,
                 kwargs={"path": fpath, "allowed_uids": (me,),
                         "ready": ready2}).start()
check(ready2.wait(5), "the daemon listens")
df = doors.DoorFiles(fpath)
try:
    df.write("/etc/shadow", "x")
    check(False, "the daemon wrote /etc/shadow")
except OSError as e:
    check("not a file the VTY writes" in str(e),
          f"a path outside the doors comes back as an OSError: {e}")
kh = tempfile.mkdtemp()
_before = doors.HOME
doors.HOME = __import__("re").compile(r"^" + __import__("re").escape(kh) + r"$")
try:
    df.write_keys(kh, "ssh-ed25519 AAAA x\n")
    body = open(kh + "/.ssh/authorized_keys").read()
    mode = oct(os.stat(kh + "/.ssh/authorized_keys").st_mode & 0o777)
    check(body == "ssh-ed25519 AAAA x\n" and mode == "0o600",
          f"authorized_keys written by the daemon, 0600: {mode}")
finally:
    doors.HOME = _before
nofile = doors.DoorFiles(os.path.join(d, "none.sock"))
lp = os.path.join(root, "local-fallback")
nofile.write(lp, "here")
check(open(lp).read() == "here", "no daemon: the file is written here")


print("\n=== the file doors' pieces, one at a time ===")
lf = system.LocalFiles()
t = tempfile.mkdtemp()
lf.mkdir(t + "/a/b")
check(os.path.isdir(t + "/a/b"), "LocalFiles.mkdir: the directory and its parents")
open(t + "/a/f", "w").write("x")
lf.copy(t + "/a/f", t + "/c/f")
check(open(t + "/c/f").read() == "x", "LocalFiles.copy: into a directory it makes")
lf.unlink(t + "/c/f")
check(not os.path.exists(t + "/c/f"), "LocalFiles.unlink: gone")
got = doors.answer_file({"file": "mkdir", "path": "/run/sshd"}, 0, files=RecFiles(t))
check(got == {"rc": 0, "out": ""}, f"answer_file: a door, for root, is done: {got}")
got = doors.answer_file({"file": "unlink", "path": system.SSHD_DROPIN}, 0,
                        files=system.LocalFiles())
check(got["rc"] == 1 and system.SSHD_DROPIN in got["out"],
      f"answer_file: a failure comes back with the path: {got}")
rf = RecFiles("")
doors.do_file({"file": "mkdir", "path": t + "/m"}, {}, files=rf)
check(rf.did == [("mkdir", t + "/m", None, None)] and os.path.isdir(t + "/m"),
      f"do_file: the operation the request names: {rf.did}")
df2 = doors.DoorFiles(fpath)
for call, why in ((lambda: df2.unlink("/etc/passwd"), "unlink"),
                  (lambda: df2.copy("/etc/passwd", "/tmp/p"), "copy"),
                  (lambda: df2.mkdir("/etc/x"), "mkdir")):
    try:
        call()
        check(False, f"DoorFiles.{why}: the daemon did it outside the doors")
    except OSError as e:
        check("not a file the VTY writes" in str(e),
              f"DoorFiles.{why}: sent to the daemon, which refused it")
try:
    df2._ask({"file": "nothing"}, lambda: None)
    check(False, "DoorFiles._ask: a refusal was not raised")
except OSError as e:
    check(e.errno == __import__("errno").EPERM,
          f"DoorFiles._ask: a refusal is an OSError, EPERM: {e}")


print("\n=== Shell.host_run: the VTY's own way to the host ===")
import prismash  # noqa: E402
got = prismash.Shell.__new__(prismash.Shell).host_run(["sh", "-c", "echo here"])
check(got == (0, "here\n"),
      f"with no daemon listening, the command runs here, as before: {got}")


check(doors._address("192.0.2.1") and doors._address("2001:db8::1")
      and not doors._address("192.0.2.0/24") and not doors._address("gw")
      and not doors._address("-x"),
      "_address: an address and nothing else, not a prefix, not a word")


print("\n=== the session door: the VTY in the management plane, as the caller ===")
import pwd as _pwd  # noqa: E402
import collections  # noqa: E402
pwd_like = collections.namedtuple("pw", "pw_uid pw_gid pw_dir pw_name")
_me = _pwd.getpwuid(os.getuid())
# A real socket where the broker makes one, under this login's own
# /run/user, so the path matches the product's pattern and the checks read
# the file itself. Skipped, and said, where there is no runtime directory.
_rt = f"/run/user/{os.getuid()}"
_ls = None
if os.path.isdir(_rt):
    _sockdir = tempfile.mkdtemp(prefix="prisma-vty-", dir=_rt)
    _sock = os.path.join(_sockdir, "vty.sock")
    _ls = socket.socket(socket.AF_UNIX)
    _ls.bind(_sock)
    argv, why = doors.session_argv({"session": {"attach": _sock}}, os.getuid(),
                                   pw=_me, groups=[_me.pw_gid, 4242])
    check(argv is not None and argv[-3:] == ["prismash", "--attach", _sock],
          f"the caller's own socket, under its own /run/user, is attached: {why}")
    _f = os.path.join(_sockdir, "not-a-socket")
    open(_f, "w").close()
    os.rename(_f, _sockdir + "/vty.sock.file")
    argv, why = doors.session_argv(
        {"session": {"attach": _sockdir + "/vty.sock.file"}}, os.getuid(),
        pw=_me, groups=[_me.pw_gid])
    check(argv is None, f"a path that does not end in vty.sock is refused: {why}")
else:
    print(f"  (no {_rt} here: the accepted-socket case is not run)")
# The shared sticky directory, for a login with no runtime directory of its
# own (0.137.0's first gate: a console login had none, and every session fell
# back to the host). A scratch directory stands in for /run/prisma-routing/vty.
_shared = tempfile.mkdtemp()
os.chmod(_shared, 0o1733)
_real_dir = doors.VTY_RUN_DIR
doors.VTY_RUN_DIR = _shared
try:
    _sd = tempfile.mkdtemp(prefix="prisma-vty-", dir=_shared)
    _ss = os.path.join(_sd, "vty.sock")
    _l2 = socket.socket(socket.AF_UNIX)
    _l2.bind(_ss)
    argv, why = doors.session_argv({"session": {"attach": _ss}}, os.getuid(),
                                   pw=_me, groups=[_me.pw_gid])
    check(argv is not None and argv[-1] == _ss,
          f"a socket in the shared directory, in the caller's own directory, is attached: {why}")
    argv, why = doors.session_argv({"session": {"attach": _ss}}, os.getuid() + 1,
                                   pw=_me, groups=[_me.pw_gid])
    check(argv is None, f"and refused to any other uid: {why}")
    _l2.close()
finally:
    doors.VTY_RUN_DIR = _real_dir
argv, why = doors.session_argv(
    {"session": {"attach": f"/run/user/{os.getuid() + 1}/prisma-vty-x/vty.sock"}},
    os.getuid(), pw=_me, groups=[_me.pw_gid])
check(argv is None and "caller's own" in why,
      f"a socket under ANOTHER uid's /run/user is refused: {why}")
argv, why = doors.session_argv({"session": {"attach": "/tmp/prisma-vty-x/vty.sock"}},
                               os.getuid(), pw=_me, groups=[_me.pw_gid])
check(argv is None, f"a socket outside /run/user is refused: {why}")
argv, why = doors.session_argv({"session": {"command": "show version\nshow ip route"}},
                               os.getuid(), pw=_me, groups=[_me.pw_gid])
check(argv is None and "one line" in why, f"a command of two lines is refused: {why}")
argv, why = doors.session_argv({"session": {"command": "show version"}}, 1234,
                               pw=pwd_like(1234, 1234, "/home/ops", "ops"),
                               groups=[1234, 998, 999])
check(argv[:4] == ["docker", "exec", "-u", "1234:1234"]
      and argv[4:8] == ["--group-add", "998", "--group-add", "999"]
      and argv[-4:] == [doors.MGMT_CONTAINER, "prismash", "-c", "show version"],
      f"the VTY runs as the caller, with the caller's host groups: {argv}")
_asked = []


def _fake(argv, stdin, timeout):
    _asked.append(argv)
    if argv[:2] == ["docker", "inspect"]:
        return 0, "false\n"
    return 0, ""


got = doors.answer_session({"session": {"command": "show version"}}, os.getuid(),
                           execute=_fake)
check(got["rc"] == 125 and len(_asked) == 1,
      f"no management plane running is 125, and nothing is exec'd: {got}")
check(doors.mgmt_running(lambda a, s, t: (0, "true\n")) is True
      and doors.mgmt_running(lambda a, s, t: (1, "")) is False,
      "mgmt_running: docker's answer, and a docker that fails is not running")
_o = io.StringIO()
with contextlib.redirect_stdout(_o):
    doors.say(json.dumps({"session": {"command": "user x password $6$SECRET"}}),
              1000, {"rc": 0})
check("session command" in _o.getvalue() and "SECRET" not in _o.getvalue(),
      f"a session is logged by its kind, never its text: {_o.getvalue()!r}")
if _ls is not None:
    _ls.close()
    __import__("shutil").rmtree(_sockdir, ignore_errors=True)


print("\n=== the probe door: ping, traceroute and mtr, as prismash builds them ===")
for argv, want, why in (
        (["/usr/bin/ping", "-c", "5", "-n", "192.0.2.1"], True, "ping"),
        (["/usr/bin/ping", "-c", "5", "-n", "-I", "10.0.0.1", "example.net"], True,
         "ping from a source, to a name"),
        (["/usr/bin/traceroute", "-q", "1", "-m", "30", "-n", "192.0.2.1"], True,
         "traceroute"),
        (["/usr/bin/mtr", "--report", "--report-cycles", "10", "-n", "-a",
          "10.0.0.1", "192.0.2.1"], True, "mtr from a source"),
        (["/usr/bin/ping", "-f", "192.0.2.1"], False, "a flood"),
        (["/usr/bin/ping", "-c", "101", "-n", "192.0.2.1"], False,
         "a count above PROBE_COUNT_MAX"),
        (["/usr/bin/ping", "-c", "5", "-n", "192.0.2.1 -f"], False,
         "a host with a space"),
        (["/usr/bin/ping", "-c", "5", "-n", "-s", "10.0.0.1", "192.0.2.1"], False,
         "another tool's source flag"),
        (["/bin/sh", "-c", "5"], False, "a shell"),
        (["/usr/bin/ping", "-c", "5", "-n"], False, "no host")):
    check(doors.probe_ok(argv) is want,
          f"{'taken' if want else 'refused'}: {why}")

import subprocess as _sp  # noqa: E402


def _fake_popen(argv, **kw):
    return _sp.Popen(["sh", "-c", "echo row1; sleep 0.2; echo row2; exit 3"], **kw)


a, b = socket.socketpair()
rc = doors.stream_probe(b, ["/usr/bin/ping", "-c", "1", "-n", "x"], popen=_fake_popen)
b.close()
got = b""
while True:
    c = a.recv(4096)
    if not c:
        break
    got += c
a.close()
msgs = [json.loads(l) for l in got.decode().splitlines()]
check(rc == 3 and msgs[-1] == {"rc": 3}
      and "".join(m.get("out", "") for m in msgs[:-1]) == "row1\nrow2\n",
      f"stream_probe: the rows as they come, then the exit status: {msgs}")
a, b = socket.socketpair()
rc = doors.stream_probe(b, ["x"], popen=lambda argv, **kw: _sp.Popen(
    ["sleep", "5"], **kw), deadline=0.3)
check(rc == 124, f"stream_probe: past the deadline, the probe is killed (124): {rc}")
a.close(); b.close()

_real_stream = doors.stream_probe
doors.stream_probe = lambda conn, argv: _real_stream(conn, argv, popen=_fake_popen)
try:
    ppath = os.path.join(d, "run", "probe.sock")
    ready3 = threading.Event()
    threading.Thread(target=doors.serve, daemon=True,
                     kwargs={"path": ppath, "ready": ready3}).start()
    ready3.wait(5)
    sink = io.StringIO()
    rc, refused = doors.probe(["/usr/bin/ping", "-c", "1", "-n", "192.0.2.1"],
                              path=ppath, out=sink)
    check((rc, refused, sink.getvalue()) == (3, "", "row1\nrow2\n"),
          f"probe(): the rows written as they arrive, and the status: "
          f"{(rc, refused, sink.getvalue())}")
    rc, refused = doors.probe(["/bin/sh", "-c", "id"], path=ppath, out=io.StringIO())
    check(rc == 126 and "not a probe" in refused,
          f"probe(): a refusal comes back as words: {refused}")
finally:
    doors.stream_probe = _real_stream

print("\n=== Shell.run_probe, from the management plane ===")
_seen = []
_real_probe = doors.probe
doors.probe = lambda argv: (_seen.append(argv) or (0, ""))
os.environ["PRISMA_IN_MGMT"] = "1"
try:
    prismash.Shell.__new__(prismash.Shell).run_probe(
        "ping", ["/usr/bin/ping", "-c", "1", "-n", "192.0.2.1"])
finally:
    del os.environ["PRISMA_IN_MGMT"]
    doors.probe = _real_probe
check(_seen == [["/usr/bin/ping", "-c", "1", "-n", "192.0.2.1"]],
      f"the probe went to the host's door, not to a local program: {_seen}")


print("\n=== the query doors: questions any account may ask of the host ===")
_q = []
for argv in [list(k) for k in doors.QUERIES]:
    r = doors.answer(json.dumps({"argv": argv}), 1000,
                     execute=lambda a, st, t: (_q.append(a) or (0, "answer")))
    check(r == {"rc": 0, "out": "answer"}, f"uid 1000 may ask: {' '.join(argv)}")
r = doors.answer(json.dumps({"argv": ["systemctl", "is-active", "--quiet",
                                      "ssh.service"]}), 1000,
                 execute=lambda a, st, t: (0, ""))
check(r["rc"] == 126, f"a question about another unit is not one: {r}")

_ask = []
_real_runner = doors.runner
doors.runner = lambda path=doors.SOCKET: (
    lambda argv, stdin=None, timeout=30: (_ask.append(argv) or (0, "vfio-pci\n")))
os.environ["PRISMA_IN_MGMT"] = "1"
try:
    unit = prismash.dataplane_unit()
    drv = prismash.Shell.__new__(prismash.Shell).which_dpdk_driver()
finally:
    del os.environ["PRISMA_IN_MGMT"]
    doors.runner = _real_runner
check(unit == prismash.DATAPLANE_UNIT_CONTAINERS and drv == "vfio-pci"
      and all(tuple(a) in doors.QUERIES for a in _ask) and len(_ask) == 2,
      f"from the management plane both are asked of the host's doors: {_ask}")

print("\n=== the accounts the management container reads, kept current (#532) ===")
import tempfile as _tmpf
with _tmpf.TemporaryDirectory() as _w:
    _src, _dst = os.path.join(_w, "etc"), os.path.join(_w, "accounts")
    os.makedirs(_src)
    for _n, _t, _m in (("passwd", "root:x:0:0::/root:/bin/sh\n", 0o644),
                       ("group", "root:x:0:\n", 0o644),
                       ("shadow", "root:!:1::::::\n", 0o640)):
        with open(os.path.join(_src, _n), "w") as _f:
            _f.write(_t)
        os.chmod(os.path.join(_src, _n), _m)
    _st0 = doors.accounts_state(_src)
    check(sorted(doors.share_accounts(_src, _dst)) == ["group", "passwd", "shadow"],
          "share_accounts: the first run copies the three files")
    check(oct(os.stat(os.path.join(_dst, "shadow")).st_mode & 0o777) == "0o640",
          "with their mode: shadow stays 0640")
    check(doors.share_accounts(_src, _dst) == [], "nothing changed, nothing copied")
    # What useradd does: a new file renamed over the old one, a new inode.
    _new = os.path.join(_src, "passwd.new")
    with open(_new, "w") as _f:
        _f.write("root:x:0:0::/root:/bin/sh\nprisma-web:x:1001:1001::/home/prisma-web:"
                 "/usr/bin/prismash-login\n")
    os.chmod(_new, 0o644)
    os.replace(_new, os.path.join(_src, "passwd"))
    check(doors.accounts_state(_src) != _st0, "accounts_state: a replaced file is a change")
    _ino = os.stat(os.path.join(_dst, "passwd")).st_ino
    check(doors.share_accounts(_src, _dst) == ["passwd"]
          and "prisma-web" in open(os.path.join(_dst, "passwd")).read(),
          "the account created after the copy reaches it")
    check(os.stat(os.path.join(_dst, "passwd")).st_ino != _ino,
          "by rename, so a reader never sees half a file")
    check(not [f for f in os.listdir(_dst) if f.endswith(".new")], "and no temporary is left")
    check(doors.accounts_state(os.path.join(_w, "nowhere")) == (None, None, None),
          "no files is no state, not a crash")

print("\n=== the API's detached runs (#558, phase 5) ===")
_a = doors.api_run_argv("upgrade", "0.149.25")
check(_a[:3] == ("systemd-run", "--collect", "--unit=prisma-routing-api-upgrade")
      and f"truncate:{doors.api_run_log('upgrade')}" in _a[3]
      and _a[-1].startswith("/usr/bin/prismash -c 'system upgrade 0.149.25';")
      and _a[-1].endswith('echo "prisma-api-exit $?"'),
      f"an upgrade: a transient unit, its output in a file the API reads, an exit line: {_a}")
check(doors.verb_of(list(_a)) == "start 'system upgrade' for the API"
      and doors.verb_of(list(doors.api_run_argv("dataplane")))
      == "start 'system dataplane restart' for the API"
      and doors.verb_of(list(doors.api_run_argv("upgrade")))
      == "start 'system upgrade' for the API",
      "each shape is a door")
check(doors.api_run_argv("upgrade", "0.149.25; reboot") is None
      and doors.api_run_argv("upgrade", "../x") is None
      and doors.api_run_argv("dataplane", "0.149.25") is None
      and doors.api_run_argv("shell") is None,
      "a version that is not a release, an argument a kind does not take, or a kind "
      "there is not: no shape")
_b = list(_a)
_b[-1] = _b[-1].replace("0.149.25'", "0.149.25' ; reboot'")
check(doors.verb_of(_b) is None, "an argv edited past the shape is no door")
_b = list(_a)
_b[2] = "--unit=something-else"
check(doors.verb_of(_b) is None, "nor one under another unit's name")
check(doors.api_run_log("dataplane") == "/run/prisma-routing/api/dataplane.log",
      "the dataplane restart's log, beside the history the API keeps")
check(doors.answer(json.dumps({"argv": list(_a)}), 1000,
                   execute=lambda *x: (0, ""))["rc"] == 126,
      "an account that is not root is refused the door")

print("=== the system actions for prisma-operator (#563) ===")
check(doors.operator_door(["systemctl", "reboot"])
      and doors.operator_door(("systemctl", "poweroff"))
      and doors.operator_door(list(doors.api_run_argv("upgrade", "0.149.26")))
      and doors.operator_door(list(doors.api_run_argv("upgrade")))
      and doors.operator_door(list(doors.api_run_argv("dataplane"))),
      "operator_door(): reboot, poweroff, the detached upgrade and restart")
check(not doors.operator_door(["systemctl", "reload", "ssh"])
      and not doors.operator_door(["useradd", "-m", "-s", "x", "y"])
      and not doors.operator_door("systemctl reboot"),
      "and nothing else: not sshd, not an account, not a string")
_ex = lambda a, s, t: (0, "ran")
for _argv, _op, _rc, _what in (
        (["systemctl", "reboot"], True, 0, "an operator reboots"),
        (["systemctl", "reboot"], False, 126, "a non-operator is refused"),
        (["systemctl", "reload", "ssh"], True, 126,
         "an operator is still refused what is not a system action"),
        (list(doors.OPERATOR_GROUPADD), True, 126,
         "and may not create the group: that is root's commit")):
    _r = doors.answer(json.dumps({"argv": _argv}), 1000, execute=_ex,
                      is_operator=lambda u, _o=_op: _o)
    check(_r["rc"] == _rc, f"{_what}: {_r}")
_r = doors.answer(json.dumps({"argv": ["systemctl", "reboot"]}), 1000,
                  execute=_ex, is_operator=lambda u: False)
check("only root and prisma-operator's members may" in _r["out"],
      f"the refusal names who may: {_r['out']}")
check(doors.answer(json.dumps({"argv": list(doors.OPERATOR_GROUPADD)}), 0,
                   execute=_ex)["rc"] == 0,
      "root creates the operator group through its door")
check(doors.account_is_operator(os.getuid()) is False
      and doors.account_is_operator(2 ** 31 - 7) is False,
      "account_is_operator(): False on a host with no prisma-operator group, "
      "and for a uid the system cannot describe")

_r = doors.answer(json.dumps({"argv": list(doors.UPGRADE_CHECK_ARGV)}), 1000,
                  execute=lambda a, s, t: (0, "newest published: 0.149.26"),
                  is_operator=lambda u: False)
check(_r == {"rc": 0, "out": "newest published: 0.149.26"},
      f"the upgrade CHECK is a question any account may ask (#563): {_r}")

print("\n" + ("ALL TESTS PASSED" if not fail else f"{fail} FAILURES"))
sys.exit(1 if fail else 0)
