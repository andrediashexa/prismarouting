"""
appliance/prisma-vty-session (#448, phase C): the VTY in the management plane
when there is one, and here when there is not.

Driven against a real doors daemon on a scratch socket (doors.serve, with the
docker it would run stood in for), and a stand-in prismash that says what it
was run with. Mutation shown red: the 125 fallback removed - a box whose
container is down leaves the operator with nothing.
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(REPO, "src", "prismash"))
import doors  # noqa: E402

SCRIPT = os.path.join(REPO, "appliance", "prisma-vty-session")
fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


work = tempfile.mkdtemp()
stub = os.path.join(work, "prismash")
with open(stub, "w") as f:
    f.write("#!/bin/sh\necho \"HERE $*\"\n")
os.chmod(stub, 0o755)


def run(*args, doors_path):
    env = dict(os.environ, PRISMA_DOORS=doors_path, PRISMA_PRISMASH=stub)
    p = subprocess.run([sys.executable, SCRIPT, *args], env=env,
                       capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout


print("=== no daemon: the VTY runs here, as before phase C ===")
rc, out = run("-c", "show version", doors_path=os.path.join(work, "none.sock"))
check(out.strip() == "HERE -c show version" and rc == 0,
      f"-c with no daemon is prismash here: {rc} {out.strip()!r}")
rc, out = run("--attach", "/tmp/x/vty.sock",
              doors_path=os.path.join(work, "none.sock"))
check(out.strip() == "HERE --attach /tmp/x/vty.sock",
      f"--attach with no daemon too: {out.strip()!r}")

print("\n=== a daemon, and no management plane running: here (125) ===")


def serve(path, running):
    def fake(argv, stdin, timeout):
        if argv[:2] == ["docker", "inspect"]:
            return 0, "true\n" if running else "false\n"
        return 3, "from the container\n"
    ready = threading.Event()
    threading.Thread(target=doors.serve, daemon=True,
                     kwargs={"path": path, "execute": fake,
                             "ready": ready}).start()
    ready.wait(5)


down = os.path.join(work, "down", "doors.sock")
serve(down, running=False)
rc, out = run("-c", "show version", doors_path=down)
check(out.strip() == "HERE -c show version",
      f"the container is down, so the VTY runs here: {out.strip()!r}")

print("\n=== a daemon and a management plane: the container answers ===")
up = os.path.join(work, "up", "doors.sock")
serve(up, running=True)
rc, out = run("-c", "show version", doors_path=up)
check(rc == 3 and out == "from the container\n",
      f"the output and the exit status are the container's: {rc} {out!r}")
rc, out = run("--attach", "/tmp/not-under-run-user/vty.sock", doors_path=up)
check(out.strip() == "HERE --attach /tmp/not-under-run-user/vty.sock",
      "a broker socket outside /run/user is refused by the door (126), and "
      "the VTY runs here")
rc, out = run("show", doors_path=up)
check(rc == 2, f"anything else is a usage error: {rc}")

print("\n=== its functions, by name ===")
os.environ["PRISMA_PRISMASH"] = stub
os.environ["PRISMA_DOORS"] = up
loader = importlib.machinery.SourceFileLoader("vtysession", SCRIPT)
spec = importlib.util.spec_from_loader("vtysession", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
# Through a parameter: test_every_function reads a name bound from importlib
# as foreign, and would not see these as calls into the product.
def asked(session_client, session):
    return session_client.ask(session)


def became(session_client, args):
    session_client.here(args)


check(asked(mod, {"command": "show version"}) == (3, "from the container\n"),
      "ask(): the daemon's answer, as (rc, out)")
mod.SOCKET = os.path.join(work, "none.sock")
check(asked(mod, {"command": "x"}) is None, "ask(): None when nobody listens")
r, w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(r)
    os.dup2(w, 1)
    became(mod, ["-c", "show version"])
    os._exit(99)
os.close(w)
os.waitpid(pid, 0)
said = os.read(r, 1000).decode()
check(said.strip() == "HERE -c show version",
      f"here(): the process becomes prismash, with the same arguments: {said!r}")
check(mod.main(["bad"]) == 2, "main(): a usage error is 2")


print("\n=== the broker runs a statement on the host only when it is one of three ===")
bl = importlib.machinery.SourceFileLoader(
    "broker", os.path.join(REPO, "appliance", "prisma-vty-broker"))
bspec = importlib.util.spec_from_loader("broker", bl)
bmod = importlib.util.module_from_spec(bspec)
bl.exec_module(bmod)


def statement_ok(broker, words):
    return broker.host_statement_ok(words)


for words, want, why in (
        (["install"], True, "install"),
        (["system", "upgrade"], True, "an upgrade"),
        (["system", "upgrade", "0.137.1"], True, "an upgrade to a version"),
        (["system", "dataplane", "restart"], True, "the dataplane's restart"),
        (["install", "now"], False, "install with words after it"),
        (["system", "reboot"], False, "a reboot, which is the container's own door"),
        (["system", "dataplane", "restart", "x"], False, "a restart with more"),
        (["start", "shell"], False, "a shell, which has its own verb"),
        (["system", "upgrade", "a\nb"], False, "a word with a newline"),
        ("install", False, "not a list")):
    check(statement_ok(bmod, words) is want, f"{'run' if want else 'refused'}: {why}")

print("\n=== vtyattach.request_hostrun, against a broker's answer ===")
import socket  # noqa: E402
import vtyattach  # noqa: E402
import threading as _th  # noqa: E402
for reply, want in ((b"HOSTDONE 3\n", ("done", 3)), (b"HOSTREFUSED\n", ("refused", None))):
    a, b = socket.socketpair()
    ch = vtyattach.Channel(a)
    asked = []

    def broker_side(sock=b, reply=reply):
        line = sock.recv(4096)
        asked.append(line)
        sock.sendall(reply)
    t = _th.Thread(target=broker_side)
    t.start()
    got = ch.request_hostrun(["system", "upgrade"])
    t.join(5)
    check(got == want and asked == [b'HOSTRUN ["system", "upgrade"]\n'],
          f"request_hostrun: {reply.strip().decode()} is {got}, asked {asked}")
    a.close(); b.close()

print("\n=== Shell.on_the_host: only from the management plane ===")
sys.path.insert(0, os.path.join(REPO, "src", "prismash"))
import prismash  # noqa: E402


class FakeAttached:
    def __init__(self):
        self.asked = []

    def request_hostrun(self, words):
        self.asked.append(words)
        return "done", 0


sh = prismash.Shell.__new__(prismash.Shell)
fa = FakeAttached()
prismash.ATTACHED = fa
check(sh.on_the_host(["install"]) is False and fa.asked == [],
      "on the host (no PRISMA_IN_MGMT): False, and the statement runs here")
os.environ["PRISMA_IN_MGMT"] = "1"
try:
    check(sh.on_the_host(["system", "upgrade"]) is True
          and fa.asked == [["system", "upgrade"]],
          "in the management plane: handed to the host's VTY through the broker")
finally:
    del os.environ["PRISMA_IN_MGMT"]
    prismash.ATTACHED = None

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
