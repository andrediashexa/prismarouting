"""
The VTY's pager (#469): a show longer than the screen stops at --More--.

Mutations shown red, one at a time: Pager.write() not counting a line (the
screen overflows without a stop); `q` not discarding what follows (the
output goes on after the operator asked it to stop); feed() paging with
`paging` off (a `-c` run waits for a key nobody will press).
"""
import io
import os
import pty
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src", "prismash"))
import prismash  # noqa: E402

fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


def keys(*ks):
    """A read_key that answers these keys in turn, and records each ask."""
    it = iter(ks)
    asked = []

    def read():
        asked.append(1)
        return next(it)
    read.asked = asked
    return read


def lines(n):
    return "".join(f"line {i}\n" for i in range(1, n + 1))


print("=== Pager: stops when the screen is full ===")
out = io.StringIO()
p = prismash.Pager(out, 5, keys(" ", "\r", "q"))
try:
    p.write(lines(20))
    check(False, "q did not stop the output")
except prismash.PagerQuit:
    pass
shown = [l for l in out.getvalue().replace(prismash.Pager.PROMPT, "")
         .replace("\r", "").split("\n") if l.strip()]
shown = [l.strip() for l in shown]
check(shown[:4] == ["line 1", "line 2", "line 3", "line 4"],
      f"a screen of 5 rows shows 4 lines, then --More--: {shown[:5]}")
check(shown[4:8] == ["line 5", "line 6", "line 7", "line 8"],
      f"space: the next screen: {shown[4:8]}")
check(shown[8:] == ["line 9"], f"enter: one more line, then q: {shown[8:]}")
check(out.getvalue().count(prismash.Pager.PROMPT) == 3,
      "and --More-- was asked three times")
p.write("line after q\n")
check("line after q" not in out.getvalue(),
      "after q, whatever the command still prints is discarded")

print("\n=== Pager: short output is never stopped ===")
out = io.StringIO()
k = keys()
prismash.Pager(out, 24, k).write(lines(23))
check(not k.asked and prismash.Pager.PROMPT not in out.getvalue(),
      "23 lines on a 24-row screen: no --More--")

print("\n=== Pager: output written in pieces is counted by line ===")
out = io.StringIO()
k = keys(" ", " ", " ")
p = prismash.Pager(out, 4, k)
for piece in ("line 1\nli", "ne 2\n", "line 3", "\nline 4\n", "line 5\n"):
    p.write(piece)
check(len(k.asked) == 1,
      f"five lines in odd pieces, 3 to a screen: one --More--, asked "
      f"{len(k.asked)}")

print("\n=== Pager: Ctrl-C at --More-- stops, as q does ===")


def ctrl_c():
    raise KeyboardInterrupt


try:
    prismash.Pager(io.StringIO(), 3, ctrl_c).write(lines(10))
    check(False, "Ctrl-C at --More-- went on printing")
except prismash.PagerQuit:
    check(True, "Ctrl-C at --More-- stops the output and not the VTY")

print("\n=== Shell.feed: every show pages, on a terminal only ===")


class Sink(io.StringIO):
    def isatty(self):
        return True


def run_show(sh, line, rows, ks):
    sh.do_operational = lambda l: print(lines(30), end="") or True
    real_rows, real_key = prismash.terminal_rows, prismash.read_key
    prismash.terminal_rows = lambda: rows
    k = keys(*ks)
    prismash.read_key = k
    out = Sink()
    real_out = sys.stdout
    sys.stdout = out
    try:
        rv = sh.feed(line)
    finally:
        sys.stdout = real_out
        prismash.terminal_rows, prismash.read_key = real_rows, real_key
    return rv, out.getvalue(), k


sh = prismash.Shell.__new__(prismash.Shell)
sh.mode = "operational"
sh.frr_ctx, sh.nat_ctx = [], []
sh.check_confirm_timeout = lambda: None
sh.spell_out = lambda l: l
sh.paging = True
rv, said, k = run_show(sh, "show version", 10, [" ", " ", " ", " "])
check(said.count(prismash.Pager.PROMPT) == 3,
      f"30 lines on a 10-row terminal: --More-- three times, "
      f"{said.count(prismash.Pager.PROMPT)}")
rv, said, k = run_show(sh, "show version", 10, ["q"])
check(rv is True and "line 10" not in said and "line 9" in said,
      "q stops the show, and the session goes on (feed returns True)")
rv, said, k = run_show(sh, "show version | no-more", 10, [])
check(prismash.Pager.PROMPT not in said and "line 30" in said and not k.asked,
      "| no-more prints all of it, without asking")
rv, said, k = run_show(sh, "show version | include line 2", 5, [" ", " ", " "])
check("line 29" in said and said.count(prismash.Pager.PROMPT) >= 1,
      "a filtered show pages what survives the filter (11 lines, 4 rows)")
sh.paging = False
rv, said, k = run_show(sh, "show version", 10, [])
check(prismash.Pager.PROMPT not in said and "line 30" in said and not k.asked,
      "paging off (-c, the labs): everything, no key asked")
check(prismash.Shell.paging is False,
      "and it is off unless repl() turns it on at a terminal")

print("\n=== read_key and terminal_rows, on a real pty ===")
# In a child with a terminal of its own (pty.fork, as a login has), the key
# typed by the parent, and everything read with a deadline. The first cut
# shared a pty with this process and hung - once on its own echo, and again
# under test_prismash_refusals, which runs every suite a second time.
import select  # noqa: E402
import time  # noqa: E402
pid, fd = pty.fork()
if pid == 0:
    try:
        k = prismash.read_key()
        os.write(1, f"KEY[{k}]\n".encode())
    finally:
        os._exit(0)
got = b""
deadline = time.time() + 10
typed = False
while time.time() < deadline and b"KEY[" not in got:
    if not typed:
        time.sleep(0.3)
        os.write(fd, b"q")
        typed = True
    r, _, _ = select.select([fd], [], [], 0.5)
    if r:
        try:
            got += os.read(fd, 1024)
        except OSError:
            break
try:
    os.kill(pid, 9)
except OSError:
    pass
os.waitpid(pid, 0)
os.close(fd)
check(b"KEY[q]" in got, f"read_key: one key, no enter needed: {got!r}")
check(isinstance(prismash.terminal_rows(), int) and prismash.terminal_rows() > 0,
      f"terminal_rows: a height, 24 when there is no terminal: "
      f"{prismash.terminal_rows()}")


print("\n=== Pager's pieces, one at a time ===")
out = io.StringIO()
p = prismash.Pager(out, 10, keys("\r"))
p.more()
check(p.left == 1 and prismash.Pager.PROMPT in out.getvalue()
      and out.getvalue().endswith("\r"),
      "more(): the prompt, erased after the key; enter leaves room for one line")
p = prismash.Pager(out, 10, keys(" "))
p.more()
check(p.left == 9, "more(): space leaves room for a screen")
flushed = []
out2 = io.StringIO()
out2.flush = lambda: flushed.append(1)
prismash.Pager(out2, 10, keys()).flush()
check(flushed == [1], "flush(): passed to the terminal")
check(prismash.Pager(Sink(), 10, keys()).__getattr__("isatty")() is True
      and prismash.Pager(Sink(), 10, keys()).isatty() is True,
      "__getattr__: what the pager does not answer, the terminal does (isatty)")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
