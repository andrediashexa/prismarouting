"""
The VTY's end of the login broker (#448).

The login session on the host owns the terminal. It hands the VTY the
terminal's three descriptors over a unix socket (SCM_RIGHTS) and stays on the
other end of that socket for three things: the signals the terminal sends to
the session - Ctrl-C and a resize arrive at the host, which owns the
controlling terminal, and are relayed here - and `start shell`, which the VTY
asks for and the HOST decides and runs.

WHY THE HOST RUNS THE SHELL. The VTY is moving into a container (#448), and
`start shell` has to land on the host, not in the container. A process in the
container cannot start one there, and must not be able to: the host decides,
from the host's own groups, and the container can only ask. The prototype on
#448 measured the ride - the candidate survives the shell, Ctrl-C in the shell
stays in the shell, a resize reaches the VTY, a container that dies mid-shell
leaves the shell working, and an account the host refuses gets nothing
whatever the VTY asked.

The protocol is lines on the socket. Host to VTY: `INT`, `WINCH`, and the
answer to a shell request, `SHELLDONE <status>` or `SHELLDENIED`. VTY to host:
`SHELL`.
"""
import os
import queue
import signal
import socket
import sys
import threading


class Channel:
    """The open socket to the broker, and a reader that relays what arrives."""

    def __init__(self, sock):
        self.sock = sock
        self.replies = queue.Queue()
        self.gone = False
        threading.Thread(target=self._reader, daemon=True).start()

    # not a unit: the thread every Channel starts; test_prismash_units.py
    # drives it through dispatch() and request_shell() over a socketpair
    def _reader(self):
        buf = b""
        while True:
            try:
                data = self.sock.recv(4096)
            except OSError:
                data = b""
            if not data:
                # The broker is gone, which means the login is: nothing is
                # left to type at. A request waiting for an answer gets one.
                self.gone = True
                self.replies.put("GONE")
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self.dispatch(line.decode("ascii", "replace"))

    def dispatch(self, line):
        """One line from the broker: a signal to raise here, or an answer."""
        if line == "INT":
            # Raised in this process, where readline and sigint_ignored() see
            # it exactly as they would see the terminal's own (#441).
            os.kill(os.getpid(), signal.SIGINT)
        elif line == "WINCH":
            os.kill(os.getpid(), signal.SIGWINCH)
        else:
            self.replies.put(line)

    def request_shell(self):
        """
        Ask the host for a shell and wait until it is over.

        Returns ("done", status), ("denied", None) or ("gone", None). The
        terminal is not read while the host shell runs, because this process
        is blocked here: one reader of the tty at a time is the whole point.
        """
        if self.gone:
            return "gone", None
        try:
            self.sock.sendall(b"SHELL\n")
        except OSError:
            return "gone", None
        answer = self.replies.get()
        if answer.startswith("SHELLDONE"):
            # Negative when a signal ended the shell: waitstatus_to_exitcode().
            try:
                status = int(answer.split()[1])
            except (IndexError, ValueError):
                status = 0
            return "done", status
        if answer == "SHELLDENIED":
            return "denied", None
        return "gone", None


def attach(path):
    """
    Connect to the broker at `path`, take the terminal it hands over, and
    return the Channel. stdin, stdout and stderr are the user's terminal from
    here on.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(path)
    _, fds, _, _ = socket.recv_fds(sock, 16, 3)
    for i, fd in enumerate(fds[:3]):
        os.dup2(fd, i)
        os.close(fd)
    # The Python objects were made over whatever the VTY was started with -
    # /dev/null, by the broker - so they are made again over the terminal.
    sys.stdin = open(0, "r", closefd=False)
    sys.stdout = open(1, "w", closefd=False, buffering=1)
    sys.stderr = open(2, "w", closefd=False, buffering=1)
    return Channel(sock)
