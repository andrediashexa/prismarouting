#!/bin/sh
# OFF-RED-AT-LEAST: 2
# monitor interface (#519): what sampling costs, then the live view itself.
#
# THE COST FIRST, as the issue requires before an interval default is chosen:
# a box with 201 interfaces and traffic on pg0, read once per second by each
# way the VTY could read its counters - `show interface` through the CLI, and
# the statistics segment through vpp_get_stats - measured in wall time, in the
# CPU the reader itself burns, and in the CPU VPP's main thread spends
# answering.
MODE=${1:-on}
set -u
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, use_binary_api
. /src/lab/verdict.sh
use_binary_api

HX="python3 /src/src/prismash/prismash.py"
export PRISMA_CONFIG=/etc/prisma-routing/prisma-routing.conf

# Workers, as a box has: with none, VPP's main thread polls the cards at
# 100% and whatever answering a sample costs it cannot be seen.
vpp -c "$(startup_conf test workers)" > /tmp/vpp.log 2>&1 &
wait_vpp
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
vppctl create sub-interfaces pg0 1-200 >/dev/null
vppctl packet-generator new \
  "name load rate 1000 size 100-100 interface pg0 node ethernet-input \
   data { IP4: 1.2.3 -> 4.5.6 UDP: 100.64.0.9 -> 8.8.8.8 UDP: 1234 -> 53 \
   incrementing 8 }"
# 1000 packets per second of 100 bytes is 800 kbit/s on pg0's rx, the rate
# lab/vty.sh holds `show interface utilization` to. MODE=off runs the same
# view with no stream: the rate and the chart it draws go red.
[ "$MODE" = off ] || vppctl packet-generator enable-stream load

echo "=========== what one sample costs, on 201 interfaces (#519) ==========="
python3 - <<'PYEOF'
import os, resource, subprocess, time

def vpp_main():
    pid = subprocess.run(["pidof", "vpp"], capture_output=True,
                         text=True).stdout.split()[0]
    for tid in os.listdir(f"/proc/{pid}/task"):
        with open(f"/proc/{pid}/task/{tid}/comm") as f:
            if f.read().strip() == "vpp_main":
                return f"/proc/{pid}/task/{tid}/stat"

def ticks(path):
    with open(path) as f:
        w = f.read().rsplit(")", 1)[1].split()
    return int(w[11]) + int(w[12])          # utime + stime

main = vpp_main()
hz = os.sysconf("SC_CLK_TCK")
# What the main thread burns with nobody reading, over the same wall a run
# takes: the figure below is the difference, since it runs its own processes
# (the packet generator's, the stats collector's) whatever anybody asks.
m0 = ticks(main); time.sleep(6.0); idle = (ticks(main) - m0) / hz / 6.0
print(f"  VPP main thread with nobody reading: {idle * 1000:.0f} ms per second")
ways = {
    "vppctl show interface": ["vppctl", "show", "interface"],
    "vpp_get_stats dump ^/if/": ["vpp_get_stats", "socket-name",
                                  "/run/vpp/stats.sock", "dump", "^/if/"],
}
N = 30
for label, argv in ways.items():
    ru0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    m0 = ticks(main)
    walls, size = [], 0
    t0 = time.monotonic()
    for _ in range(N):
        t = time.monotonic()
        out = subprocess.run(argv, capture_output=True, text=True).stdout
        walls.append(time.monotonic() - t)
        size = len(out)
        time.sleep(0.2)
    ru1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    m1 = ticks(main)
    span = time.monotonic() - t0
    extra = ((m1 - m0) / hz - idle * span) / N
    cpu = (ru1.ru_utime - ru0.ru_utime + ru1.ru_stime - ru0.ru_stime) / N
    walls.sort()
    print(f"  {label}: wall {walls[N // 2] * 1000:.0f} ms median, "
          f"{walls[-1] * 1000:.0f} ms worst; reader CPU {cpu * 1000:.0f} ms; "
          f"VPP main thread {extra * 1000:+.1f} ms over idle; "
          f"{size} bytes; 1 process")
PYEOF

echo
echo "=========== monitor interface, driven under a terminal (#519) ==========="
# A pty of 80x24, as the console is; the frames read back with the escapes
# taken off, the terminal's modes compared before and after the view.
cat > /tmp/drive-monitor.py <<'PYEOF'
import fcntl, os, re, select, struct, subprocess, sys, termios, time

def run(keys_after, stop_vpp=False, seconds=6.0):
    """Start the view, send `keys_after` after `seconds`; what it drew."""
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    before = termios.tcgetattr(slave)
    p = subprocess.Popen(["python3", "/src/src/prismash/prismash.py", "-c",
                          "monitor interface pg0 interval 1"],
                         stdin=slave, stdout=slave, stderr=slave,
                         start_new_session=True,
                         preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
                         env=dict(os.environ, LANG="C.UTF-8",
                                  PYTHONIOENCODING="utf-8"))
    out, t0, sent, killed = b"", time.monotonic(), False, False
    while True:
        r, _, _ = select.select([master], [], [], 0.2)
        if r:
            try:
                out += os.read(master, 65536)
            except OSError:
                break
        now = time.monotonic() - t0
        if stop_vpp and not killed and now > seconds - 3:
            subprocess.run(["pkill", "-x", "vpp_main"])
            subprocess.run(["pkill", "vpp"])
            killed = True
        if not sent and now > seconds:
            os.write(master, keys_after.encode())
            sent = True
        if p.poll() is not None and not r:
            break
        if now > seconds + 10:
            p.kill()
            break
    after = termios.tcgetattr(slave)
    text = out.decode("utf-8", "replace")
    frames = [re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", f)
              for f in text.split("\x1b[H")[1:]]
    return p.returncode, text, frames, before == after

what = sys.argv[1]
rc, text, frames, same = run({"q": "q", "ctrlc": "\x03", "stop": "q"}[what],
                             stop_vpp=(what == "stop"))
last = frames[-2] if len(frames) > 1 else (frames[-1] if frames else "")
print(f"rc={rc} frames={len(frames)} modes_restored={same} "
      f"left_alt_screen={text.rstrip().endswith(chr(27) + '[?1049l')} "
      f"cursor_back={chr(27) + '[?25h' in text}")
rows = [r.rstrip() for r in last.replace("\r", "").split("\n")]
rx = [r for r in rows if " rx " in r and "pps" not in r]
print("RX " + (rx[0].split(" rx ", 1)[1] if rx else "none"))
chart = [r for r in rows if "\u2502" in r or "\u2524" in r][:4]
print("CELLS %d" % sum(r.split("\u2524" if "\u2524" in r else "\u2502", 1)[1].count(c)
                       for r in chart for c in "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"))
print("WIDEST %d" % max((len(r) for f in frames for r in f.replace("\r", "").split("\n")),
                        default=0))
print("QUIET %s" % any("stopped answering" in f for f in frames))
for r in rows[:11]:
    print("  | " + r)
PYEOF
Q=$(python3 /tmp/drive-monitor.py q 2>&1)
printf '%s\n' "$Q" | sed 's/^/  /'
RXQ=$(printf '%s\n' "$Q" | sed -n 's/^RX //p')
KBIT=$(printf '%s' "$RXQ" | awk '{ if ($2 == "kbit/s") printf "%d", $1;
                                  else if ($2 == "Mbit/s") printf "%d", $1 * 1000;
                                  else print 0 }')
CELLS=$(printf '%s\n' "$Q" | sed -n 's/^CELLS //p')
C=$(python3 /tmp/drive-monitor.py ctrlc 2>&1)
printf '%s\n' "$C" | head -1 | sed 's/^/  ctrl-c: /'
PIPE=$($HX -c "monitor interface pg0" 2>&1)
printf '%s\n' "$PIPE" | sed 's/^/  piped: /'
S=$(python3 /tmp/drive-monitor.py stop 2>&1)
printf '%s\n' "$S" | head -1 | sed 's/^/  vpp stopped: /'

expect "rx on pg0 reads 800 kbit/s as bit/s, not bytes nor a total (${KBIT:-none})" \
  sh -c "[ \"${KBIT:-0}\" -ge 300 ] && [ \"${KBIT:-0}\" -le 2000 ]"
expect "and the rx chart has bars with traffic (${CELLS:-0} cells)" \
  test "${CELLS:-0}" -gt 0
expect "q leaves with the terminal's modes as they were" \
  sh -c "printf '%s' \"\$1\" | head -1 | grep -q 'rc=0 .*modes_restored=True left_alt_screen=True cursor_back=True'" _ "$Q"
expect_in "pg0 named is pg0 alone, not its 200 sub-interfaces" \
  "page 1/1 " "$Q"
expect "every frame fits 79 columns ($(printf '%s\n' "$Q" | sed -n 's/^WIDEST //p'))" \
  sh -c "[ \"$(printf '%s\n' "$Q" | sed -n 's/^WIDEST //p')\" -le 79 ]"
expect "Ctrl-C leaves the same way" \
  sh -c "printf '%s' \"\$1\" | head -1 | grep -q 'modes_restored=True left_alt_screen=True cursor_back=True'" _ "$C"
expect_in "a pipe is refused, and told what serves it" \
  "monitor interface needs a terminal" "$PIPE"
expect_not_in "with no escape sequence in what it printed" "$(printf '\033')" "$PIPE"
expect "a dataplane stopped mid-view is said, and q still restores the terminal" \
  sh -c "printf '%s' \"\$1\" | grep -q 'QUIET True' && printf '%s' \"\$1\" | head -1 | grep -q 'modes_restored=True left_alt_screen=True'" _ "$S"

verdict
