#!/bin/sh
# Completion at a real terminal (#557): TAB, a double TAB and `?` in the VTY,
# under a pty, with readline, on a box that has a dataplane and FRR.
#
#   sh tools/lab.sh complete      -> LAB PASSED
#
# What test/test_prismash_complete.py cannot say: whether readline, given what
# _options() answers, puts the right thing on the SCREEN - a list printed, a
# word inserted, `<Enter>` shown and never typed into the line. Asserted here
# on the bytes the terminal received.
#
#   1. `no` names what is configured: `no neighbor <TAB><TAB>` offers the session,
#      `no ipv4 address <TAB>` in an interface inserts the configured address.
#   2. a static route after its next hop: `?` lists distance, tag, description
#      AND `<Enter>`; `d<TAB><TAB>` lists distance and description; once
#      `distance` is on the line it is not offered again.
#   3. BGP `aggregate-address <prefix> ?` lists its optional words and Enter.
#   4. a complete statement says so: `nat ?` prints `<Enter>`, never "(no
#      known options here; ...)", and `<Enter>` is never typed into the line.
#
# NO-OFF-RUN: completion has no switch in the product, and one added only for this lab to flip would be a second completion path to keep; the control is this lab run against v0.149.16, where 1-4 fail on 13 checks (recorded in the #557 commit)
set -u
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /etc/prisma-routing /var/run/frr /etc/frr
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
use_binary_api
export PRISMA_ROLLBACK=/tmp/rollback PRISMA_CONFIG=/tmp/prisma-routing.conf
rm -rf /tmp/rollback

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
printf 'hostname prisma-routing\n' > /etc/frr/bgpd.conf
printf 'hostname prisma-routing\n' > /etc/frr/staticd.conf
chown -R frr:frr /var/run/frr /etc/frr
pgrep zebra >/dev/null || start_frr bgpd staticd
sleep 2

HX="python3 /src/src/prismash/prismash.py"

echo "=========== a box with something to remove ==========="
$HX -c configure -c "interface pg0" -c "ipv4 address 10.9.9.1/24" -c exit \
    -c "router static" -c "0.0.0.0/0 next-hop 10.9.9.2" -c exit \
    -c "router bgp 65000" -c "neighbor 10.9.9.7 remote-as 65001" -c exit \
    -c commit 2>&1 | sed 's/^/  /'

# One pty session per screen, each starting clean in configuration mode; the
# screen is what the terminal received after the keys, escapes stripped.
SCREENS=$(python3 - "$HX" <<'PTYEOF'
import os, pty, re, select, sys, time
def session(keys):
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "dumb"
        os.execvp(sys.argv[1].split()[0], sys.argv[1].split())
    buf = b""
    def drain(t):
        nonlocal buf
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    buf += os.read(fd, 65536)
                except OSError:
                    return
    drain(4)
    for k in [b"configure\r"] + keys:
        mark = len(buf)
        os.write(fd, k)
        drain(1.5)
    os.write(fd, b"\x03")
    drain(0.5)
    os.kill(pid, 9)
    os.waitpid(pid, 0)
    text = re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b"", buf[mark:]).replace(b"\r", b"")
    return text.decode(errors="replace")
screens = [
    ("no-nbr", [b"router bgp 65000\r", b"no neighbor \t\t"]),
    ("no-addr", [b"interface pg0\r", b"no ipv4 address \t"]),
    ("static-q", [b"router static\r", b"10.77.0.0/16 next-hop 10.9.9.2 ?"]),
    ("static-d", [b"router static\r", b"10.77.0.0/16 next-hop 10.9.9.2 d\t\t"]),
    ("static-dist", [b"router static\r",
                     b"10.77.0.0/16 next-hop 10.9.9.2 distance 200 ?"]),
    ("bgp-agg", [b"router bgp 65000\r", b"address-family ipv4 unicast\r",
                 b"aggregate-address 10.0.0.0/8 ?"]),
    ("nat-q", [b"nat ?"]),
]
for name, keys in screens:
    t = session(keys)
    print(f"@@{name}")
    print(t)
PTYEOF
)
screen() { printf '%s\n' "$SCREENS" | awk -v n="@@$1" '$0==n{f=1;next} /^@@/{f=0} f'; }
# A word offered is a ROW of the list readline printed - "  distance  optional"
# - and not the word anywhere on the screen: 0.149.16 printed the static
# route's hint, "[distance <1-255>] [tag ...]", which has every word in it and
# offered none of them, and a check on the bare word passed there (#557).
row() {  # <word> <screen>
  printf '%s\n' "$2" | grep -qE "^ +$1 {2,}"
}

for s in no-nbr no-addr static-q static-d static-dist bgp-agg nat-q; do
  echo; echo "----- $s"; screen "$s" | sed 's/^/  | /'
done

echo
echo "=========== 1. no names what is configured ==========="
expect_in "no neighbor TAB TAB offers the session" "10.9.9.7" "$(screen no-nbr)"
expect_in "no ipv4 address TAB inserts the configured address" \
  "no ipv4 address 10.9.9.1/24" "$(screen no-addr)"

echo
echo "=========== 2. a static route's optional words, and Enter ==========="
S=$(screen static-q)
for w in distance tag description "<Enter>"; do
  expect "after the next hop, ? lists $w as an option" row "$w" "$S"
done
D=$(screen static-d)
expect "d TAB TAB lists distance" row distance "$D"
expect "and description" row description "$D"
expect "and not tag" sh -c "! printf '%s\n' \"\$1\" | grep -qE '^ +tag {2,}'" _ "$D"
T=$(screen static-dist)
expect "distance is not offered once it is on the line" \
  sh -c "! printf '%s\n' \"\$1\" | grep -qE '^ +distance {2,}'" _ "$T"
expect "tag still is" row tag "$T"

echo
echo "=========== 3. BGP aggregate-address, the same way ==========="
A=$(screen bgp-agg)
for w in summary-only suppress-policy "<Enter>"; do
  expect "aggregate-address <prefix> ? lists $w as an option" row "$w" "$A"
done

echo
echo "=========== 4. a complete statement says so ==========="
N=$(screen nat-q)
expect "nat ? prints the Enter line" row "<Enter>" "$N"
expect_not_in "never the line that reads as unknown" "no known options" "$N"
expect_not_in "and <Enter> is never typed into the line" "nat <Enter>" "$N"

echo
echo "=========== how long a TAB takes ==========="
LAT=$(python3 - <<'PYEOF'
import sys, time, io, contextlib
sys.path.insert(0, "/src/src/prismash")
import prismash
sh = prismash.Shell()
with contextlib.redirect_stdout(io.StringIO()):
    sh.feed("configure")
    sh.feed("router static")
ts = []
for i in range(5):
    t0 = time.time()
    sh._options("10.77.0.0/16 next-hop ", False)
    ts.append(time.time() - t0)
print(f"{ts[0]*1000:.0f} {max(ts[1:])*1000:.0f}")
PYEOF
)
set -- $LAT
echo "  the next hop: first TAB $1 ms (asks the dataplane), the next ones at most $2 ms (cached)"
expect "a first TAB answers within 1 s ($1 ms)" test "$1" -lt 1000
expect "and a repeated one within 200 ms ($2 ms)" test "$2" -lt 200

verdict
