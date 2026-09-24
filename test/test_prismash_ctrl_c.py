#!/usr/bin/env python3
"""
Ctrl-C at the VTY, on a real pseudo-terminal (#441).

Only a terminal turns the key into SIGINT for the foreground process group,
so this drives prismash on a pty and presses it. Reported by the maintainer
over ssh: Ctrl-C at the prompt closed the session. It was handled like
Ctrl-D, so prismash exited 0 and prismash-login with it. Reproduced here
first, and a second case was found beside it: Ctrl-C during a command that
did not catch it killed prismash with a traceback, and prismash-login
answers a non-zero exit with a login shell.

Mutations shown red, one at a time: KeyboardInterrupt put back beside
EOFError at input() (A goes red: the VTY exits); the try around feed()
removed (C goes red: the VTY dies of SIGINT).
"""
import os
import pty
import select
import signal
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fail = 0


def check(cond, msg):
    global fail
    if cond:
        print(f"  [ ok ] {msg}")
    else:
        fail += 1
        print(f"  FAIL: {msg}")


_root = tempfile.mkdtemp(prefix="prismash-ctrl-c-")
# A vtysh that takes its time, so a `show` handed to FRR is still running
# when the key is pressed. That is the path nothing caught.
_slow = os.path.join(_root, "vtysh")
with open(_slow, "w") as f:
    f.write("#!/bin/sh\nsleep 20\n")
os.chmod(_slow, 0o755)
ENV = dict(os.environ,
           PRISMA_CONFIG=os.path.join(_root, "prisma-routing.conf"),
           PRISMA_ROLLBACK=os.path.join(_root, "rollback"),
           PRISMA_CONFIRM=os.path.join(_root, "confirm-pending"),
           VTYSH=_slow)


def session(keys):
    """
    prismash on a pty, fed `keys` as (bytes, seconds to wait after). Returns
    whether it is still running at the end, its exit code if not, and what
    it printed. Killed at the end either way.
    """
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(REPO)
        os.execvpe(sys.executable,
                   [sys.executable, "src/prismash/prismash.py"], ENV)
    out = b""

    def drain(seconds):
        nonlocal out
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                try:
                    out += os.read(fd, 4096)
                except OSError:
                    return
    drain(2.0)
    for key, wait in keys:
        os.write(fd, key)
        drain(wait)
    done, status = os.waitpid(pid, os.WNOHANG)
    alive = done == 0
    if alive:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    code = None if alive else os.waitstatus_to_exitcode(status)
    return alive, code, out.decode(errors="replace")


print("=== A: Ctrl-C at the prompt discards the line and asks again ===")
alive, code, out = session([(b"show vers", 0.5), (b"\x03", 1.0),
                            (b"show version\r", 2.0)])
check(alive, f"the VTY is still running after Ctrl-C at the prompt "
             f"(exit {code})")
check("plugin:" in out,
      "and the next command is answered, from a fresh prompt")
check("show versshow version" not in out,
      "and the half-typed line was discarded, not kept in front of the next")

print("\n=== C: Ctrl-C during a command stops it, not the VTY ===")
alive, code, out = session([(b"show ip route\r", 2.0), (b"\x03", 1.5),
                            (b"show version\r", 2.0)])
check(alive, f"the VTY survives Ctrl-C while vtysh is still answering "
             f"(exit {code})")
check("Traceback" not in out, "and no traceback reached the operator")
check("plugin:" in out, "and the next command after it is answered")

print("\n=== D: Ctrl-D still leaves ===")
alive, code, out = session([(b"\x04", 1.5)])
check(not alive and code == 0,
      f"Ctrl-D at the prompt ends the VTY cleanly (alive={alive}, "
      f"exit {code}): leaving is still one key")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
