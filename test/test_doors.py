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
from system import LOGIN_SHELL, MANAGED_GROUPS, GROUP_ADMIN  # noqa: E402

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
SLOTS = {"name": ["ops"], "g": list(MANAGED_GROUPS)}
tree = ast.parse(open(os.path.join(SRC, "system.py")).read())
sites = []
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "_run" and node.args \
            and isinstance(node.args[0], ast.List):
        choices = [[]]
        for e in node.args[0].elts:
            if isinstance(e, ast.Constant):
                vals = [e.value]
            elif isinstance(e, ast.Name) and e.id in SLOTS:
                vals = SLOTS[e.id]
            elif isinstance(e, ast.Attribute) and e.attr == "shell":
                vals = [LOGIN_SHELL]
            else:
                vals = None
            if vals is None:
                choices = None
                break
            choices = [c + [v] for c in choices for v in vals]
        stdin = None
        if any(k.arg == "stdin" for k in node.keywords):
            stdin = "ops:$6$salt$hash\n"
        sites.append((node.lineno, choices, stdin))
check(len(sites) >= 12, f"{len(sites)} command site(s) read off system.py")
for line, choices, stdin in sites:
    if choices is None:
        check(False, f"system.py:{line}: an argument this suite does not know")
        continue
    for argv in choices:
        check(doors.verb_of(argv, stdin) is not None,
              f"system.py:{line}: {' '.join(argv)} is a door")

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
        ([1, 2], None, "not strings")):
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

print("\n" + ("ALL TESTS PASSED" if not fail else f"{fail} FAILURES"))
sys.exit(1 if fail else 0)
