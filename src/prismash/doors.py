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

import errno
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading

from system import (LOGIN_SHELL, MANAGED_GROUPS, LocalFiles, RESOLV_CONF,
                    RESOLV_BEFORE, SSHD_DROPIN, SNMPD_CONF)
from config import LINUX_IFNAME_MAX

SOCKET = "/run/prisma-routing/doors.sock"

#: An account name as a door takes it: never an option (no leading '-'),
#: never a path, at most useradd(8)'s 32 characters. root is refused where
#: the VTY never sends it (userdel, useradd, usermod, gpasswd).
ACCOUNT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$")

#: What chpasswd -e reads: ONE line, an account and a crypt(3) hash. The
#: hash has no ':' and no newline, so the line cannot name a second account.
CHPASSWD_LINE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]{0,31}):"
                           r"(\$[A-Za-z0-9./$=,+-]{1,512})\n$")

#: A Linux netdev as a door takes it: at most IFNAMSIZ-1 characters, never an
#: option, never a path. The ip-link doors act on the LINUX end of a dataplane
#: interface (its LCP pair), in the host's network namespace - which is why
#: they are doors at all: from a container the same `ip link` would act on the
#: container's own interfaces and report success (#448's inventory).
NETDEV = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:@-]{0,%d}$"
                    % (LINUX_IFNAME_MAX - 1))

#: IFALIASZ is 256 with the terminator. The VTY's description is shorter
#: (63), so this bounds what a door takes, not what the VTY sends.
ALIAS_MAX = 255

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
    if n == 6 and argv[:4] == ("ip", "link", "set", "dev") \
            and NETDEV.match(argv[4]) and argv[5] in ("up", "down"):
        return "set a Linux interface up or down"
    if n == 7 and argv[:4] == ("ip", "link", "set", "dev") \
            and NETDEV.match(argv[4]) and argv[5] == "mtu" \
            and argv[6].isdigit() and 68 <= int(argv[6]) <= 65535:
        return "set a Linux interface's MTU"
    if n == 7 and argv[:4] == ("ip", "link", "set", "dev") \
            and NETDEV.match(argv[4]) and argv[5] == "alias" \
            and len(argv[6]) <= ALIAS_MAX and "\n" not in argv[6] \
            and "\0" not in argv[6]:
        return "set a Linux interface's description"
    if n == 6 and argv[:5] == ("ip", "-o", "link", "show", "dev") \
            and NETDEV.match(argv[5]):
        return "read a Linux interface"
    return None


#: The files a door writes, and HOW: each (operation, path) the daemon takes,
#: with the mode and the atomicity it decides. The caller names a path and
#: sends the text; it cannot choose another path, and cannot choose the mode.
#: They are what system.py set before the doors (test_doors.py drives it).
SNMP_V3_STORE = "/var/lib/snmp/snmpd.conf"
FILE_DOORS = {
    ("write", RESOLV_CONF): {"mode": 0o644, "atomic": True},
    ("write", SSHD_DROPIN): {"mode": None, "atomic": False},
    ("write", SNMPD_CONF): {"mode": 0o600, "atomic": False},
    ("append", SNMP_V3_STORE): {"mode": 0o600},
    ("unlink", RESOLV_BEFORE): {},
    ("unlink", SSHD_DROPIN): {},
    ("unlink", SNMPD_CONF): {},
    ("copy", RESOLV_CONF): {"dst": RESOLV_BEFORE},
    ("mkdir", "/run/sshd"): {},
}

#: The home an authorized_keys door writes under: root's, or an account's.
HOME = re.compile(r"^/(root|home/[A-Za-z0-9_][A-Za-z0-9_.-]{0,31})$")


def file_verb(req):
    """
    The file door `req` is, as (operation, path, how), or None.

    `req` is the request's dict: {"file": operation, "path": ..., ...}.
    """
    op, path = req.get("file"), req.get("path")
    if not isinstance(op, str) or not isinstance(path, str):
        return None
    if op == "keys":
        return (op, path, {}) if HOME.match(path) and ".." not in path \
            else None
    how = FILE_DOORS.get((op, path))
    if how is None:
        return None
    if op == "copy" and req.get("dst") != how["dst"]:
        return None
    if op in ("write", "append", "keys") and \
            not isinstance(req.get("text"), str):
        return None
    return op, path, how


def do_file(req, how, files=None):
    """The file door carried out, as `how` says. Raises OSError."""
    files = files or LocalFiles()
    op, path = req["file"], req["path"]
    if op == "write":
        files.write(path, req["text"], mode=how["mode"], atomic=how["atomic"])
    elif op == "append":
        files.append(path, req["text"], mode=how["mode"])
    elif op == "unlink":
        files.unlink(path)
    elif op == "copy":
        files.copy(path, how["dst"])
    elif op == "mkdir":
        files.mkdir(path)
    elif op == "keys":
        files.write_keys(path, req["text"])


class DoorFiles:
    """
    system.LocalFiles, through the door at `path`: each write is sent to the
    daemon, and a refusal or a failure comes back as the OSError the local
    write would have raised. With no daemon listening, the local write.
    """

    def __init__(self, path=SOCKET):
        self.path = path

    def _ask(self, req, local):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(self.path)
        except OSError:
            s.close()
            return local()
        try:
            s.settimeout(30)
            s.sendall(json.dumps(req).encode() + b"\n")
            r = json.loads(_read_line(s))
        except (OSError, ValueError) as e:
            raise OSError(errno.EIO, f"the host's door did not answer: {e}")
        finally:
            s.close()
        if r.get("rc") != 0:
            raise OSError(errno.EPERM, str(r.get("out", "refused")))

    def write(self, path, text, mode=None, atomic=False):
        self._ask({"file": "write", "path": path, "text": text},
                  lambda: LocalFiles().write(path, text, mode, atomic))

    def append(self, path, text, mode=None):
        self._ask({"file": "append", "path": path, "text": text},
                  lambda: LocalFiles().append(path, text, mode))

    def unlink(self, path):
        self._ask({"file": "unlink", "path": path},
                  lambda: LocalFiles().unlink(path))

    def copy(self, src, dst):
        self._ask({"file": "copy", "path": src, "dst": dst},
                  lambda: LocalFiles().copy(src, dst))

    def mkdir(self, path):
        self._ask({"file": "mkdir", "path": path},
                  lambda: LocalFiles().mkdir(path))

    def write_keys(self, home, text):
        self._ask({"file": "keys", "path": home, "text": text},
                  lambda: LocalFiles().write_keys(home, text))


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


def answer(request, uid, allowed_uids=(0,), execute=run_locally, files=None):
    """
    What the daemon replies to `request` from `uid`: {"rc", "out"}.

    Separate from the socket so the decision is tested on its own. 126 is
    the refusal, the shell's own "found and not permitted".
    """
    try:
        r = json.loads(request)
        if isinstance(r, dict) and "file" in r:
            return answer_file(r, uid, allowed_uids, files)
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
        r = json.loads(request)
        argv = r.get("argv")
        if "file" in r and argv is None:
            argv = [str(r.get("file")), str(r.get("path"))]
    except (ValueError, AttributeError):
        argv = None
    words = " ".join(argv) if isinstance(argv, list) and \
        all(isinstance(a, str) for a in argv) else "<not a request>"
    print(f"door: uid {uid}: {words[:200]} -> {reply['rc']}", flush=True)


def answer_file(r, uid, allowed_uids=(0,), files=None):
    """answer(), for a file door: {"rc": 0} or a refusal or the error."""
    verb = file_verb(r)
    if verb is None:
        return {"rc": 126, "out": "the host's door: not a file the VTY "
                                  "writes on the host"}
    if uid not in allowed_uids:
        return {"rc": 126, "out": f"the host's door: {verb[0]} {verb[1]}: "
                                  "only root may"}
    try:
        do_file(r, verb[2], files)
    except OSError as e:
        return {"rc": 1, "out": f"{verb[1]}: {e.strerror or e}"}
    return {"rc": 0, "out": ""}


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
