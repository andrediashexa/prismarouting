"""
The host's doors (#448, phase B): the operations the VTY needs done on the
host, asked of a daemon on a unix socket instead of run by the VTY itself.

WHY. Phase C moves the VTY into a container on the backplane, and from there
it cannot run `useradd`, reload sshd or restart snmpd - those act on the host,
and a container that could do them directly is a container with the host's
root. So each operation becomes a door: the VTY sends the command it would
have run, the daemon checks that it is one of the shapes below, with every
argument checked, and runs it on the host. Phase B is the doors with the VTY
still on the host, so a regression is a door and not the move.

WHAT A DOOR IS NOT: a way to run a command. The daemon runs root, so a door
that took any argv would be a root shell for whoever reached the socket.
`verb_of` accepts a closed list of shapes, and each argument slot is checked
against what the VTY actually puts there: an account name that cannot be read
as an option, the one login shell, the groups the VTY manages, and one
`name:hash` line for chpasswd. Anything else is refused, whoever asks.

WHO. The daemon runs a door only for a caller the host already lets do it:
uid 0, read from the socket with SO_PEERCRED and not from anything the
caller says. That is exactly today's rule - the VTY runs as the account that
logged in, and only root's could run these commands (#212 declined granting
more) - so this phase changes where the command runs and nothing about who
may run it.

The client, `runner()`, has the signature System and SnmpDaemon take as
`runner`: (argv, stdin=None, timeout=30) -> (rc, output). With no daemon
listening it runs the command itself, as the VTY always has, so a box
upgraded before its daemon started is a box that still works.
"""

import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading

from system import LOGIN_SHELL, MANAGED_GROUPS

SOCKET = "/run/prisma-routing/doors.sock"

#: An account name as a door takes it: never an option (no leading '-'),
#: never a path, at most useradd(8)'s 32 characters. root is refused where
#: the VTY never sends it (userdel, useradd, usermod, gpasswd).
ACCOUNT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$")

#: What chpasswd -e reads: ONE line, an account and a crypt(3) hash. The
#: hash has no ':' and no newline, so the line cannot name a second account.
CHPASSWD_LINE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]{0,31}):"
                           r"(\$[A-Za-z0-9./$=,+-]{1,512})\n$")

#: The doors that take no argument: the argv is the whole shape.
FIXED = {
    ("sshd", "-T"): "read sshd's effective configuration",
    ("sshd", "-t"): "check sshd's configuration",
    ("systemctl", "reload", "ssh"): "reload sshd",
    ("systemctl", "is-active", "--quiet", "snmpd"): "ask whether snmpd runs",
    ("systemctl", "disable", "--now", "snmpd"): "stop snmpd",
    ("systemctl", "enable", "snmpd"): "enable snmpd",
    ("systemctl", "restart", "snmpd"): "restart snmpd",
}

#: The longest a door may take. The VTY's own default is 30 seconds.
MAX_TIMEOUT = 120


def _account(name, root_ok=False):
    """True when `name` is an account name a door may be given."""
    return bool(ACCOUNT.match(name)) and (root_ok or name != "root")


def verb_of(argv, stdin=None):
    """
    The door `argv` (with `stdin`) is, or None when it is none of them.

    The whole check. A None is a refusal, and the daemon runs nothing it
    returned None for.
    """
    if not isinstance(argv, (list, tuple)) or \
            not all(isinstance(a, str) for a in argv):
        return None
    argv = tuple(argv)
    if stdin is not None and argv != ("chpasswd", "-e"):
        return None
    if argv in FIXED:
        return FIXED[argv]
    n = len(argv)
    if n == 3 and argv[:2] == ("userdel", "-r") and _account(argv[2]):
        return "remove an account"
    if n == 5 and argv[:3] == ("useradd", "-m", "-s") \
            and argv[3] == LOGIN_SHELL and _account(argv[4]):
        return "create an account"
    if n == 4 and argv[:2] == ("usermod", "-s") and argv[2] == LOGIN_SHELL \
            and _account(argv[3]):
        return "set an account's shell"
    if n == 4 and argv[0] == "gpasswd" and argv[1] in ("-a", "-d") \
            and _account(argv[2]) and argv[3] in MANAGED_GROUPS:
        return "change an account's groups"
    if argv == ("chpasswd", "-e") and isinstance(stdin, str) \
            and CHPASSWD_LINE.match(stdin):
        return "set a password"
    return None


def run_locally(argv, stdin=None, timeout=30):
    """The command run here, as the VTY ran it before the doors."""
    try:
        p = subprocess.run(list(argv), capture_output=True, text=True,
                           input=stdin, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: timed out"


def runner(path=SOCKET):
    """
    A `runner` for System and SnmpDaemon that goes through the door at
    `path`, or runs the command itself when nothing listens there.
    """
    def run(argv, stdin=None, timeout=30):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(path)
        except OSError:
            s.close()
            return run_locally(argv, stdin, timeout)
        try:
            s.settimeout(min(timeout, MAX_TIMEOUT) + 10)
            s.sendall(json.dumps({"argv": list(argv), "stdin": stdin,
                                  "timeout": timeout}).encode() + b"\n")
            reply = _read_line(s)
            r = json.loads(reply)
            return int(r["rc"]), str(r["out"])
        except (OSError, ValueError, KeyError, TypeError) as e:
            return 125, f"{argv[0]}: the host's door did not answer: {e}"
        finally:
            s.close()
    return run


def _read_line(s, limit=1 << 20):
    """One newline-terminated message off `s`, at most `limit` bytes."""
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            raise ValueError("message too long")
    return buf.decode()


def peer_uid(conn):
    """The uid of the process at the other end of `conn`, from the kernel."""
    cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                           struct.calcsize("3i"))
    return struct.unpack("3i", cred)[1]


def answer(request, uid, allowed_uids=(0,), execute=run_locally):
    """
    What the daemon replies to `request` from `uid`: {"rc", "out"}.

    Separate from the socket so the decision is tested on its own. 126 is
    the refusal, the shell's own "found and not permitted".
    """
    try:
        r = json.loads(request)
        argv, stdin = r["argv"], r.get("stdin")
        timeout = r.get("timeout", 30)
    except (ValueError, KeyError, TypeError):
        return {"rc": 126, "out": "the host's door: not a request"}
    verb = verb_of(argv, stdin)
    if verb is None:
        return {"rc": 126, "out": "the host's door: not an operation the "
                                  "VTY is allowed on the host"}
    if uid not in allowed_uids:
        return {"rc": 126, "out": f"the host's door: {verb}: only root may"}
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = 30
    rc, out = execute(argv, stdin, min(timeout, MAX_TIMEOUT))
    return {"rc": rc, "out": out}


def say(request, uid, reply):
    """
    One journal line per request: the argv, the caller's uid and the result.
    NEVER stdin, which is where chpasswd's hash travels.
    """
    try:
        argv = json.loads(request).get("argv")
    except (ValueError, AttributeError):
        argv = None
    words = " ".join(argv) if isinstance(argv, list) and \
        all(isinstance(a, str) for a in argv) else "<not a request>"
    print(f"door: uid {uid}: {words[:200]} -> {reply['rc']}", flush=True)


def serve(path=SOCKET, allowed_uids=(0,), execute=run_locally, ready=None):
    """
    Listen on `path` and answer every connection, one thread each.

    The socket is 0666: who may use a door is decided per request from
    SO_PEERCRED, which a mode cannot express and a caller cannot forge.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o666)
    srv.listen(16)
    if ready:
        ready.set()

    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn, allowed_uids, execute),
                         daemon=True).start()


def handle(conn, allowed_uids=(0,), execute=run_locally):
    """One connection: read the request, answer it, close. Never raises."""
    with conn:
        try:
            uid = peer_uid(conn)
            request = _read_line(conn)
            reply = answer(request, uid, allowed_uids, execute)
            conn.sendall(json.dumps(reply).encode() + b"\n")
            say(request, uid, reply)
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    if sys.argv[1:] != ["--serve"]:
        print("usage: doors.py --serve", file=sys.stderr)
        sys.exit(2)
    serve()
