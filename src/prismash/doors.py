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
import ipaddress
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading

from system import (LOGIN_SHELL, MANAGED_GROUPS, LocalFiles, RESOLV_CONF,
                    RESOLV_BEFORE, SSHD_DROPIN, SNMPD_CONF, SET_HOSTNAME,
                    hostname_error, is_number)
from config import (LINUX_IFNAME_MAX, PROBE_COUNT_MAX, PROBE_DEADLINE,
                    probe_host_ok, probe_source_ok)

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

#: The host program that puts the management container - the VTY's - on the
#: images an upgrade just installed, and nothing else (#528).
REFRESH_VTY = "/usr/lib/prisma-routing/refresh-the-vty"
#: How `system upgrade` asks for it: DETACHED, a few seconds on, as a unit of
#: its own. The session asking lives in the container this recreates, so run
#: in-line it would be killed half way by its own work; systemd-run returns at
#: once, the timer fires after the session has said what happens, and
#: --collect lets the next upgrade reuse the name even after a failed run.
REFRESH_VTY_ARGV = ("systemd-run", "--collect", "--on-active=3",
                    "--unit=prisma-routing-refresh-vty", REFRESH_VTY)

#: The doors that take no argument: the argv is the whole shape.
FIXED = {
    ("sshd", "-T"): "read sshd's effective configuration",
    ("sshd", "-t"): "check sshd's configuration",
    ("systemctl", "reload", "ssh"): "reload sshd",
    ("systemctl", "is-active", "--quiet", "snmpd"): "ask whether snmpd runs",
    ("systemctl", "disable", "--now", "snmpd"): "stop snmpd",
    ("systemctl", "enable", "snmpd"): "enable snmpd",
    ("systemctl", "restart", "snmpd"): "restart snmpd",
    # `system reboot` and `system shutdown` (Shell.ask_systemd), and the
    # tuner the `system cpu` / `system memory` changes restart. Missed by the
    # first cut, whose suite read system.py only: a box with the daemon would
    # have refused root its own reboot. test_doors.py now reads prismash.py's
    # `self.sys._run` too.
    ("systemctl", "reboot"): "restart the box",
    ("systemctl", "poweroff"): "shut the box down",
    ("systemctl", "restart", "prisma-routing-tune"): "recompute the tuning",
    # The management API's listener (#532): api-configure reads api.env,
    # which the VTY writes, and writes the socket on the management address.
    ("systemctl", "restart", "prisma-routing-api-config.service"):
        "apply the management API's settings",
    # snmpd's configuration, READ (#525). The management container does not
    # mount /etc/snmp, and the file carries the communities, so it is a
    # door and not a mount - and not a query: FIXED is root's, which is who
    # may see a community on the host too (the file is 0600 root there).
    ("cat", SNMPD_CONF): "read snmpd's configuration",
    # `system upgrade`'s last step (#528): the VTY on the new release.
    REFRESH_VTY_ARGV: "restart the VTY on the upgraded release",
}

#: What any account may ask of the host from the management plane (#448):
#: questions, not changes - which unit runs the dataplane, where this box's
#: ssh host keys came from, which driver DPDK would get. The VTY on the host
#: asked them of the host itself, whatever the account; from the container
#: the answers would be the container's, or nothing.
QUERIES = {
    ("systemctl", "is-active", "--quiet", "prisma-routing-containers.service"):
        "ask which unit runs the dataplane",
    ("/usr/lib/prisma-routing/ssh-host-key-origin",):
        "ask where the ssh host keys came from",
    ("/usr/lib/prisma-routing/bind-dataplane-nic", "--which-driver"):
        "ask which driver DPDK would get",
    # The container has a name of its own (#516).
    ("hostname",): "ask the box's name",
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
            and is_number(argv[6]) and 68 <= int(argv[6]) <= 65535:
        return "set a Linux interface's MTU"
    if n == 7 and argv[:4] == ("ip", "link", "set", "dev") \
            and NETDEV.match(argv[4]) and argv[5] == "alias" \
            and len(argv[6]) <= ALIAS_MAX and "\n" not in argv[6] \
            and "\0" not in argv[6]:
        return "set a Linux interface's description"
    if n == 6 and argv[:5] == ("ip", "-o", "link", "show", "dev") \
            and NETDEV.match(argv[5]):
        return "read a Linux interface"
    if n == 2 and argv[0] == SET_HOSTNAME and hostname_error(argv[1]) is None:
        return "rename the box"
    if n == 8 and argv[:5] == ("ip", "route", "replace", "default", "via") \
            and _address(argv[5]) and argv[6] == "dev" \
            and NETDEV.match(argv[7]):
        return "put the management default route back"
    return None


def _address(text):
    """True when `text` is an IPv4 or IPv6 address and nothing else."""
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


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
    """The command run here, as the VTY ran it before the doors. A timeout
    of None is no limit: a VTY session lasts as long as the operator."""
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
    # Where the answers come from when something listens there: the
    # SnmpDaemon asks, before it believes a file the host says is missing,
    # whether it was the host that said so (#525).
    run.door = path
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
        if isinstance(r, dict) and "session" in r:
            return answer_session(r, uid, execute)
        argv, stdin = r["argv"], r.get("stdin")
        timeout = r.get("timeout", 30)
    except (ValueError, KeyError, TypeError):
        return {"rc": 126, "out": "the host's door: not a request"}
    if stdin is None and isinstance(argv, list) and tuple(argv) in QUERIES:
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            timeout = 30
        rc, out = execute(argv, None, min(timeout, MAX_TIMEOUT))
        return {"rc": rc, "out": out}
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
        if "session" in r and argv is None:
            # Never the command's text: `-c` can carry a password hash.
            kind = [k for k in ("attach", "command")
                    if isinstance(r["session"], dict) and k in r["session"]]
            argv = ["session"] + kind
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


#: The container the VTY runs in (#448, phase C), as docker-compose 1.29 names
#: it on the box.
MGMT_CONTAINER = "prisma-routing_mgmt_1"

#: A broker's socket, as prisma-vty-broker makes it under the login's runtime
#: directory. The uid in the path must be the caller's own.
SESSION_SOCKET = re.compile(r"^/run/user/(\d+)/prisma-vty-[A-Za-z0-9_]+/vty\.sock$")

#: Where a login with no runtime directory of its own puts it: a sticky
#: directory (tmpfiles.d, 1733) the management plane shares. The path names
#: no uid, so the owner of the socket AND of the directory it is in must be
#: the caller - which mkdtemp in a sticky directory guarantees and nobody
#: else can arrange.
VTY_RUN_DIR = "/run/prisma-routing/vty"

#: A command a session runs with -c (`ssh box 'show version'`): one line.
SESSION_COMMAND_MAX = 4096


def session_argv(req, uid, pw=None, groups=None):
    """
    The `docker exec` a session door runs, or (None, why).

    NOT A DOOR THAT GRANTS ANYTHING. It is the door every login takes into
    the management plane, so it runs for every uid - and what it runs is the
    VTY as THAT uid: `-u <uid>:<gid>` and `--group-add` each group the account
    has on the host, read off the host's own files and not from the request.
    The VTY decides the role from those groups, as it does on the host, and
    whatever it then asks of the host's other doors is decided by the same
    uid, which the kernel reports. A read-only account in the container is
    exactly a read-only account on the host.
    """
    s = req.get("session")
    if not isinstance(s, dict):
        return None, "not a session"
    if pw is None:
        import pwd
        try:
            pw = pwd.getpwuid(uid)
        except KeyError:
            return None, f"uid {uid} has no account on the host"
    if groups is None:
        groups = os.getgrouplist(pw.pw_name, pw.pw_gid)
    if "attach" in s:
        path = s["attach"]
        import stat
        m = SESSION_SOCKET.match(path) if isinstance(path, str) else None
        shared = isinstance(path, str) and re.match(
            "^" + re.escape(VTY_RUN_DIR) + r"/prisma-vty-[A-Za-z0-9_]+/vty\.sock$",
            path)
        if not shared and (not m or int(m.group(1)) != uid):
            return None, "a session's socket is under the caller's own /run/user"
        try:
            st = os.lstat(path)
            dst = os.lstat(os.path.dirname(path))
        except OSError:
            return None, "no such socket"
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != uid:
            return None, "not the caller's own socket"
        if not stat.S_ISDIR(dst.st_mode) or dst.st_uid != uid:
            return None, "not the caller's own directory"
        vty = ["prismash", "--attach", path]
    elif "command" in s:
        cmd = s["command"]
        if not isinstance(cmd, str) or "\n" in cmd or "\0" in cmd \
                or len(cmd) > SESSION_COMMAND_MAX:
            return None, "a session's command is one line"
        vty = ["prismash", "-c", cmd]
    else:
        return None, "a session attaches a terminal or runs a command"
    argv = ["docker", "exec", "-u", f"{uid}:{pw.pw_gid}"]
    for g in sorted(set(groups) - {pw.pw_gid}):
        argv += ["--group-add", str(g)]
    argv += ["-e", f"HOME={pw.pw_dir}", "-e", f"USER={pw.pw_name}",
             "-e", f"LOGNAME={pw.pw_name}", MGMT_CONTAINER] + vty
    return argv, ""


def mgmt_running(execute=run_locally):
    """Is the management plane's container running? Asked of docker."""
    rc, out = execute(["docker", "inspect", "-f", "{{.State.Running}}",
                       MGMT_CONTAINER], None, 10)
    return rc == 0 and out.strip() == "true"


def answer_session(r, uid, execute=run_locally):
    """
    answer(), for a session door: the VTY run in the management plane as the
    caller, and its exit status. 125 means there is no management plane to
    run it in, which the client takes as "run it here", as before phase C.
    """
    argv, why = session_argv(r, uid)
    if argv is None:
        return {"rc": 126, "out": f"the host's door: {why}"}
    if not mgmt_running(execute):
        return {"rc": 125, "out": "the management plane is not running"}
    rc, out = execute(argv, None, None)
    return {"rc": rc, "out": out if "command" in r["session"] else ""}


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


#: The probes a door runs (#448, phase C), as prismash.probe_argv() builds
#: them and nothing else: the program at its fixed path on the host, the
#: flags in their order, a count in range, an optional source address, and
#: a host prismash would take. From the management plane these are the only
#: way out - its network ends at the backplane.
PROBE_SHAPES = {
    "/usr/bin/ping": (["-c", "N", "-n"], "-I"),
    "/usr/bin/traceroute": (["-q", "1", "-m", "N", "-n"], "-s"),
    "/usr/bin/mtr": (["--report", "--report-cycles", "N", "-n"], "-a"),
}


def probe_ok(argv):
    """True when `argv` is a probe prismash.probe_argv() would build."""
    if not isinstance(argv, list) or not argv \
            or not all(isinstance(a, str) for a in argv):
        return False
    shape = PROBE_SHAPES.get(argv[0])
    if shape is None:
        return False
    fixed, src_flag = shape
    rest = argv[1:]
    if len(rest) < len(fixed) + 1:
        return False
    for want, got in zip(fixed, rest):
        if want == "N":
            if not is_number(got) or not 1 <= int(got) <= PROBE_COUNT_MAX:
                return False
        elif want != got:
            return False
    tail = rest[len(fixed):]
    if len(tail) == 3 and tail[0] == src_flag and probe_source_ok(tail[1]):
        tail = tail[2:]
    return len(tail) == 1 and probe_host_ok(tail[0])


def stream_probe(conn, argv, popen=subprocess.Popen, deadline=None):
    """
    Run a probe and send its output down `conn` as it comes, one JSON line
    per chunk ({"out": ...}), then {"rc": ...}. A probe's value is arriving
    row by row, which is why this door streams and the others do not.

    A caller that goes away - Ctrl-C at the VTY closes the socket - stops
    the probe: the write fails and the process is killed. So does the
    deadline, the one prismash gives a probe run here.
    """
    import time
    deadline = PROBE_DEADLINE if deadline is None else deadline
    p = popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    start = time.time()
    rc = None
    try:
        while True:
            if time.time() - start > deadline:
                p.kill()
                rc = 124
                break
            r, _, _ = __import__("select").select([p.stdout], [], [], 0.5)
            if r:
                chunk = os.read(p.stdout.fileno(), 4096)
                if not chunk:
                    rc = p.wait()
                    break
                conn.sendall(json.dumps(
                    {"out": chunk.decode(errors="replace")}).encode() + b"\n")
            elif p.poll() is not None:
                continue
        conn.sendall(json.dumps({"rc": rc}).encode() + b"\n")
    except OSError:
        p.kill()
        rc = 130
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()
    return rc


def probe(argv, path=SOCKET, out=None):
    """
    A probe through the host's door, its output written to `out` as it
    arrives. (rc, refusal): refusal is the door's words when it would not
    run the probe, else "". Ctrl-C closes the connection, which is what
    stops the probe on the host.
    """
    out = out or sys.stdout
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
        s.sendall(json.dumps({"probe": list(argv)}).encode() + b"\n")
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                return 125, "the host's door went away"
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                m = json.loads(line)
                if "out" in m and "rc" not in m:
                    out.write(m["out"])
                    out.flush()
                elif "rc" in m:
                    return m["rc"], m.get("out", "") if m["rc"] == 126 else ""
    finally:
        s.close()


def handle(conn, allowed_uids=(0,), execute=run_locally):
    """One connection: read the request, answer it, close. Never raises."""
    with conn:
        try:
            uid = peer_uid(conn)
            request = _read_line(conn)
            try:
                r = json.loads(request)
            except ValueError:
                r = None
            # A probe streams its answer, so it is answered here and not by
            # answer(). Any account may probe, as any account may at the VTY.
            if isinstance(r, dict) and "probe" in r:
                if probe_ok(r["probe"]):
                    rc = stream_probe(conn, r["probe"])
                    say(json.dumps({"argv": r["probe"]}), uid, {"rc": rc})
                else:
                    conn.sendall(json.dumps(
                        {"rc": 126, "out": "the host's door: not a probe the "
                                           "VTY runs"}).encode() + b"\n")
                return
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
