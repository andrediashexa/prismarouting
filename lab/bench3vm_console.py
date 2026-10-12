#!/usr/bin/env python3
"""The serial console of the bench's box, driven (#475).

  bench3vm_console.py <socket> <log> <hostname> <password> <pubkey file>

Logs in as root at the box's serial console - the line an operator is
handed - leaves the VTY with `start shell`, and installs the bench's public
key for root, so everything after this runs over ssh on the management
card. The console is qemu's, on a unix socket; everything the box prints is
appended to <log>.

Every wait is for a string the BOX prints, with a deadline and the tail of
what it printed instead when the deadline passes. Exits 0 when the key is in
place, 1 otherwise.
"""
import re
import socket
import sys
import time

ESC = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]")


class Console:
    def __init__(self, path, log):
        self.s = socket.socket(socket.AF_UNIX)
        deadline = time.time() + 60
        while True:
            try:
                self.s.connect(path)
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(1)
        self.s.settimeout(1)
        self.log = open(log, "ab")
        self.buf = b""

    def read_some(self):
        try:
            data = self.s.recv(65536)
        except socket.timeout:
            return
        if not data:
            raise EOFError("the console closed")
        self.log.write(data)
        self.log.flush()
        self.buf += ESC.sub(b"", data).replace(b"\r", b"")

    def wait(self, needle, secs, what):
        needle = needle.encode()
        deadline = time.time() + secs
        while time.time() < deadline:
            i = self.buf.find(needle)
            if i >= 0:
                self.buf = self.buf[i + len(needle):]
                return True
            self.read_some()
        tail = self.buf[-1500:].decode(errors="replace")
        print(f"  console: gave up after {secs}s waiting for {needle!r} "
              f"({what}); it printed:")
        for line in tail.splitlines()[-20:]:
            print(f"    | {line}")
        return False

    def send(self, line):
        self.s.sendall(line.encode() + b"\r")
        time.sleep(0.3)


def main():
    path, log, host, pw, keyfile = sys.argv[1:6]
    key = open(keyfile).read().strip()
    c = Console(path, log)
    # The login prompt appears only once the dataplane is up (the box holds
    # it back on purpose), so the first boot's wait is long.
    c.send("")
    if not c.wait(f"{host} login:", 900, "the box to offer a login"):
        return 1
    c.send("root")
    if not c.wait("Password:", 60, "the password prompt"):
        return 1
    c.send(pw)
    if not c.wait(f"{host}> ", 180, "the VTY prompt after login"):
        return 1
    c.send("start shell")
    if not c.wait("#", 60, "the shell the VTY opens"):
        return 1
    c.send(f"mkdir -p /root/.ssh && echo '{key}' >> /root/.ssh/authorized_keys"
           " && chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys"
           " && echo BENCH""KEY=ok")
    if not c.wait("BENCHKEY=ok", 30, "the key to be written"):
        return 1
    c.send("exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
