"""
appliance/prisma-vty-broker and src/prismash/vtyattach.py, on a real pty
(#448): the login keeps the terminal, the VTY is handed it, and `start shell`
is run on the HOST by the broker - decided by the host's groups.

The operator is played on a pty (pty.fork makes the broker a session leader
with the pty as its controlling terminal, which is what sshd and getty do).
The VTY is test/vty_attach_stub.py, which uses the real vtyattach module. No
container here: the same ride through a container is on #448's prototype and
in the lab; what this asserts is the broker's and the module's logic.

Mutations shown red, one at a time: may_have_shell() answering yes for
everybody (the denied case gets a shell); the shell forked without being made
the foreground group (Ctrl-C in it goes to the broker); the SIGWINCH relay
removed (the VTY keeps its old size); the VTY started in the login's own
session (every Ctrl-C arrives twice, and the late one eats the next line).
"""
import grp, os, pty, select, signal, struct, fcntl, termios, time, sys
HERE = os.path.dirname(os.path.abspath(__file__))
BROKER = os.path.join(HERE, "..", "appliance", "prisma-vty-broker")
STUB = os.path.join(HERE, "vty_attach_stub.py")
fail = 0
def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1

def session(admin_group):
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["PRISMA_ADMIN_GROUP"] = admin_group
        os.environ["PRISMA_SHELL"] = "/bin/sh"
        os.environ["PS1"] = "hostsh$ "
        os.execv(sys.executable, [sys.executable, BROKER, sys.executable, STUB])
    return pid, fd

def read_until(fd, needle, t=20):
    out = b""; end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                d = os.read(fd, 4096)
            except OSError:
                break
            out += d
            if needle.encode() in out:
                return out.decode(errors="replace"), True
    return out.decode(errors="replace"), False

def line_of(fd, marker, t=10):
    """The whole line the VTY prints that starts at `marker`."""
    out, _ = read_until(fd, marker, t)
    more, _ = read_until(fd, "\n", 3)
    text = out + more
    at = text.rfind(marker)
    return text[at:].split("\r\n")[0] if at >= 0 else ""

def send(fd, s):
    os.write(fd, s.encode())

mine = grp.getgrgid(os.getgid()).gr_name
if os.getuid() == 0:
    print("  (running as root: the host allows root a shell, so the denied case "
          "cannot be driven and is skipped)")

print("=== the VTY is handed the login's terminal ===")
pid, fd = session(mine)
out, ok = read_until(fd, "stub# (0)")
check(ok and "STUB-VTY pid" in out, f"the VTY draws its prompt on the login's pty: {out[-80:]!r}")
send(fd, "set pool residencial\r"); read_until(fd, "(1)")

print("=== start shell is a HOST shell, the broker's child ===")
send(fd, "start shell\r"); time.sleep(0.8)
send(fd, "echo HOSTSHELL=$PRISMA_HOST_SHELL PARENT=$PPID\r")
out, ok = read_until(fd, "PARENT=")
out2, _ = read_until(fd, "\n", 3); out += out2
check(f"HOSTSHELL=1 PARENT={pid}" in out,
      f"the shell is the broker's child (pid {pid}), not the VTY's: {out[-100:]!r}")
send(fd, "sleep 20\r"); time.sleep(0.8); send(fd, "\x03"); time.sleep(0.4)
send(fd, "echo STILL-HERE\r")
out, ok = read_until(fd, "STILL-HERE\r\n", 8)
check(ok, f"Ctrl-C in the shell stops the command and not the shell: {out[-100:]!r}")
send(fd, "exit\r")
out, ok = read_until(fd, "BACK done 0")
check(ok, f"exit returns to the VTY with the shell's status: {out[-80:]!r}")
send(fd, "show\r"); row = line_of(fd, "CANDIDATE:")
check(row == "CANDIDATE: pool residencial", f"the candidate survived: {row!r}")

print("=== Ctrl-C and a resize at the VTY reach it through the broker ===")
send(fd, "half a line"); time.sleep(0.3); send(fd, "\x03")
out, ok = read_until(fd, "^C", 5)
check(ok, f"Ctrl-C reaches the VTY: {out[-60:]!r}")
# The prompt drawn again before the next line is typed: the INT is relayed
# asynchronously, and a line typed ahead of it lands on the half line.
read_until(fd, "stub# (1) ", 5)
send(fd, "show\r"); row = line_of(fd, "CANDIDATE:")
check(row == "CANDIDATE: pool residencial",
      f"and the half line was dropped, the session alive: {row!r}")
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 31, 97, 0, 0))
os.kill(pid, signal.SIGWINCH); time.sleep(0.3)
send(fd, "size\r"); out, ok = read_until(fd, "lines=", 5); out2, _ = read_until(fd, ")", 3)
check("columns=97" in out + out2 and "lines=31" in out + out2,
      f"a resize reaches the VTY: {(out + out2)[-60:]!r}")

print("=== the VTY's exit status is the broker's ===")
send(fd, "die\r")
_, status = os.waitpid(pid, 0)
check(os.waitstatus_to_exitcode(status) == 7,
      f"prismash-login reads the VTY's own status: {os.waitstatus_to_exitcode(status)}")

if os.getuid() != 0:
    print("=== an account the host refuses gets no shell ===")
    pid, fd = session("prisma-no-such-group")
    read_until(fd, "stub# (0)")
    send(fd, "start shell\r")
    out, ok = read_until(fd, "BACK denied", 10)
    check(ok, f"the host said no, whatever the VTY asked: {out[-80:]!r}")
    send(fd, "exit\r"); os.waitpid(pid, 0)

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
