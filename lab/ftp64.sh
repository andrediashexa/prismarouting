#!/bin/sh
# OFF-RED-AT-LEAST: 22
# (the floor: what the off run of 2026-10-04 measured red)
# FTP64 (RFC 6384, #485 phase 5): an IPv6-only subscriber's FTP client
# fetching a file from an IPv4 server that speaks no RFC 2428, passive and
# active, through a NAT64 pool with `alg ftp on`.
#
#   sh tools/lab.sh ftp64          -> LAB PASSED
#   sh tools/lab.sh ftp64 off      -> LAB FAILED (the pool's ALG left off)
#
#   sub  (netns)  2001:db8:1200:ab00::10/56 ... veth ... host-sub  VPP inside
#   srv  (netns)  192.0.2.2/24 ...................... host-srv    VPP outside
#
# The client is Python's ftplib over IPv6, which is what an IPv6-only host's
# FTP looks like: it sends EPSV for passive and EPRT |2|...| for active, and
# never PASV or PORT. The server is the IPv4 FTP RFC 6384 exists for: it
# answers PASV and PORT and refuses EPSV, EPRT and ALGS with 500, and it logs
# every command line it receives. So nothing crosses unless the box
# translates, and what the box sent is read off the server's log, not off a
# counter. Every exchange after the first rewrite moves on renumbered TCP
# streams between two real kernels, which do not forgive a sequence number.
#
# OFF MODE builds the same pool without `alg ftp on`, which is the NAT64
# default (RFC 6384 section 4): the server refuses EPSV and EPRT, nothing is
# fetched, and the checks that say a transfer happened go red.
set -u
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
. /src/lab/verdict.sh
use_binary_api
HX="python3 /src/src/prismash/prismash.py"

# --- the two hosts, as lab/nat64.sh builds them -----------------------------
for ns in sub srv; do
  ip netns add $ns 2>/dev/null
  ip link add v-$ns type veth peer name host-$ns 2>/dev/null
  ip link set v-$ns netns $ns
  ip link set host-$ns up
  ip netns exec $ns ip link set lo up
  ip netns exec $ns ip link set v-$ns up
done
# Full checksums from both hosts (lab/nat64.sh says why): ETHTOOL_STXCSUM and
# ETHTOOL_SSG off, by ioctl, ethtool not being in the image.
txcsum_off() {  # <netns or -> <ifname>
  _ns=""; [ "$1" != - ] && _ns="ip netns exec $1"
  $_ns python3 -c '
import ctypes, fcntl, socket, struct, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for cmd in (0x17, 0x1f):
    b = ctypes.create_string_buffer(struct.pack("II", cmd, 0))
    ifr = struct.pack("16sQ", sys.argv[1].encode(), ctypes.addressof(b))
    try: fcntl.ioctl(s, 0x8946, ifr)
    except OSError as e: print("txcsum_off", sys.argv[1], e)
' "$2"
}
txcsum_off sub v-sub; txcsum_off srv v-srv; txcsum_off - host-sub; txcsum_off - host-srv
ip netns exec sub sysctl -qw net.ipv6.conf.all.accept_dad=0 net.ipv6.conf.v-sub.accept_dad=0
ip netns exec sub ip -6 addr add 2001:db8:1200:ab00::10/56 dev v-sub nodad
ip netns exec sub ip -6 route add 64:ff9b:1::/48 via 2001:db8:1200:ab00::1
ip netns exec srv ip addr add 192.0.2.2/24 dev v-srv
ip netns exec srv ip route add 203.0.113.0/24 via 192.0.2.1

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp

vppctl create host-interface name host-sub cksum-gso-disable >/dev/null
vppctl create host-interface name host-srv cksum-gso-disable >/dev/null
vppctl set interface state host-host-sub up
vppctl set interface state host-host-srv up
vppctl enable ip6 interface host-host-sub
vppctl set interface ip address host-host-sub 2001:db8:1200:ab00::1/56
vppctl set interface ip address host-host-srv 192.0.2.1/24
vppctl set ip neighbor host-host-srv 192.0.2.2 \
  "$(ip netns exec srv cat /sys/class/net/v-srv/address)"
vppctl set ip neighbor host-host-sub 2001:db8:1200:ab00::10 \
  "$(ip netns exec sub cat /sys/class/net/v-sub/address)"
vppctl set interface bpa in2out host-host-sub
vppctl set interface bpa out2in host-host-srv

echo "=========== 1. the pool, through the VTY ==========="
ALG="alg ftp on"
[ "$MODE" = off ] && ALG=""
$HX -c "configure" -c "nat" -c "cgnat" -c "pool v6" \
    -c "prefix-inside 2001:db8:1200::/40" -c "subscriber-length 56" \
    -c "nat64-prefix 64:ff9b:1::/48" -c "prefix-outside 203.0.113.0/29" \
    -c "block-size 2048" ${ALG:+-c} ${ALG:+"$ALG"} -c "exit" -c "commit" \
    2>&1 | sed 's/^/  /'
vppctl show bpa config | tr -d '\r' | sed -n '/cgnat pool v6/,/exit/p' \
  | tee /tmp/f64-config.txt | sed 's/^/  /'
POOLALG=$(vppctl show bpa pool | tr -d '\r' \
  | awk '/^pool v6/{p=1;next} /^pool /{p=0} p && /alg/{print; exit}')
echo "  show bpa pool: $POOLALG"

# The subscriber's public address and block, from the model (lab/nat64.sh).
EXPECT=$(python3 - <<'EOF'
import sys; sys.path.insert(0, "/src/ref")
from bpa_model import AddrSet6, PoolConfig
s = AddrSet6(["2001:db8:1200::/40"], 56)
idx = s.index_of("2001:db8:1200:ab00::10")
p = PoolConfig("v6", ["0.0.0.0/%d" % (32 - (56 - 40))],
               ["203.0.113.0/29"], block_size=2048)
f = p.forward(idx)
print(f["ip_pub"], f["port_lo"], f["port_hi"])
EOF
)
echo "  the model: public address and block: $EXPECT"

# --- the IPv4 server -------------------------------------------------------
# RFC 959 and nothing newer: PASV, PORT, RETR, NOOP, AUTH (accepted and then
# ignored, so the channel stays readable for the transparency check). EPSV,
# EPRT and ALGS get the 500 a server that predates RFC 2428 gives.
ip netns exec srv python3 - > /tmp/f64-srv.out 2>&1 <<'EOF' &
import socket, threading
DATA = bytes((i * 7 + 3) & 0xff for i in range(200000))
log = open("/tmp/f64-srv-cmds.txt", "w")
def serve(c):
    f = c.makefile("rb")
    def say(t): c.sendall(t.encode() + b"\r\n")
    say("220 an IPv4 server from before RFC 2428")
    pasv = port = None
    while True:
        line = f.readline()
        if not line: break
        log.write(line.decode(errors="replace").rstrip("\r\n") + "\n"); log.flush()
        cmd = line.decode(errors="replace").strip()
        verb = cmd.split(" ")[0].upper()
        if verb == "USER": say("331 password")
        elif verb == "PASS": say("230 in")
        elif verb == "TYPE": say("200 type set")
        elif verb == "NOOP": say("200 NOOP ok")
        elif verb == "AUTH": say("234 AUTH accepted")
        elif verb == "PASV":
            pasv = socket.socket(); pasv.bind(("192.0.2.2", 0)); pasv.listen(1)
            p = pasv.getsockname()[1]; port = None
            say("227 Entering Passive Mode (192,0,2,2,%d,%d)" % (p >> 8, p & 255))
        elif verb == "PORT":
            h = cmd[5:].split(","); port = (".".join(h[:4]), int(h[4]) * 256 + int(h[5]))
            pasv = None; say("200 PORT ok")
        elif verb == "RETR":
            say("150 opening")
            try:
                if pasv:
                    pasv.settimeout(10); d, _ = pasv.accept()
                else:
                    d = socket.create_connection(port, timeout=10)
                d.sendall(DATA); d.close(); say("226 done")
            except Exception as e:
                say("425 no data connection: %s" % e)
            pasv = port = None
        elif verb == "QUIT": say("221 bye"); break
        else: say("500 '%s': command not understood" % verb)
    c.close()
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("192.0.2.2", 21)); s.listen(4); s.settimeout(120)
while True:
    try: c, a = s.accept()
    except socket.timeout: break
    print("control from", a[0], a[1], flush=True)
    threading.Thread(target=serve, args=(c,), daemon=True).start()
EOF
sleep 1

# --- the IPv6 client -------------------------------------------------------
ip netns exec sub python3 - > /tmp/f64-client.txt 2>&1 <<'EOF'
import ftplib, socket
WANT = bytes((i * 7 + 3) & 0xff for i in range(200000))
SRV = "64:ff9b:1:c000:2:200::"     # 192.0.2.2 under the /48, RFC 6052
def new():
    f = ftplib.FTP(timeout=10, source_address=("2001:db8:1200:ab00::10", 0))
    f.connect(SRV, 21); f.login("lab", "lab"); return f
def fetch(f, passive):
    f.set_pasv(passive); got = bytearray()
    f.retrbinary("RETR big", got.extend)
    return "ok %d" % len(got) if bytes(got) == WANT else "mismatch %d" % len(got)
def cmd(f, c):
    try: return f.sendcmd(c)
    except ftplib.all_errors as e: return str(e)
def say(k, v): print("%s=%s" % (k, v), flush=True)
def guard(k, fn):
    try: say(k, fn())
    except Exception as e: say(k, "error %s" % e)
try:
    f = new()
except Exception as e:
    say("connect", "error %s" % e); raise SystemExit
say("connect", "ok")
guard("passive", lambda: fetch(f, True))
guard("active", lambda: fetch(f, False))
guard("passive2", lambda: fetch(f, True))
say("status", cmd(f, "ALGS STATUS64"))
say("epsvall", cmd(f, "EPSV ALL"))
say("epsv1", cmd(f, "EPSV 1"))
say("disable", cmd(f, "ALGS DISABLE64"))
guard("disabled", lambda: fetch(f, True))
say("enable", cmd(f, "ALGS ENABLE64"))
guard("enabled", lambda: fetch(f, True))
say("noop", cmd(f, "NOOP"))
guard("quit", lambda: f.quit())
# AUTH: once the server takes it the box translates nothing more (RFC 6384
# section 5). This server only pretends, so the next EPSV is readable on its
# side - and reaches it as EPSV.
try:
    g = new()
    say("auth", cmd(g, "AUTH TLS"))
    say("afterauth", cmd(g, "EPSV"))
    guard("quit2", lambda: g.quit())
except Exception as e:
    say("auth", "error %s" % e)
EOF
sleep 1
sed 's/^/  client: /' /tmp/f64-client.txt
sed 's/^/  server got: /' /tmp/f64-srv-cmds.txt 2>/dev/null
cf() { sed -n "s/^$1=//p" /tmp/f64-client.txt | head -1; }

echo
echo "=========== 2. what the VTY refuses ==========="
REFUSED=$($HX -c "configure" -c "nat" -c "cgnat" -c "pool v6" \
    -c "alg rtsp on" -c "exit" -c "commit" 2>&1)
printf '%s\n' "$REFUSED" | sed 's/^/  /'
DPREFUSED=$(vppctl bpa alg v6 sip on 2>&1 | tr -d '\r')
echo "  vppctl bpa alg v6 sip on: $DPREFUSED"
POOLALG2=$(vppctl show bpa pool | tr -d '\r' \
  | awk '/^pool v6/{p=1;next} /^pool /{p=0} p && /alg/{print; exit}')

echo
echo "=========== 3. the box ==========="
vppctl show errors | tr -d '\r' | grep -iE "ftp64|sequence" | sed 's/^/  /'
# Summed over workers, and matched on the node AND a prefix of the name:
# `show errors` cuts a counter's name at 35 characters, and both nodes count
# renumbered segments under names that differ only past that.
errc() {  # <node> <name prefix>
  vppctl show errors | tr -d '\r' | grep -F "$1" | grep -F "$2" \
    | awk '{s += $1} END {print s + 0}'
}
N_CMD=$(errc bpa-nat64-in2out "FTP64 commands translated")
N_REP=$(errc bpa-out2in "FTP64 replies translated")
N_SEQ_OUT=$(errc bpa-nat64-in2out "TCP sequence numbers shifted")
N_SEQ_IN=$(errc bpa-out2in "TCP sequence or acknowledgement")

echo
echo "=========== VERDICT ==========="
set -- $EXPECT
SRVCMDS=$(cat /tmp/f64-srv-cmds.txt 2>/dev/null)
# The main session, before AUTH's second connection.
MAIN=$(printf '%s\n' "$SRVCMDS" | sed '/^AUTH/,$d')
expect "the client logged in to the IPv4 server through the box" test "$(cf connect)" = ok
expect "passive: EPSV crossed as PASV and the file arrived whole" test "$(cf passive)" = "ok 200000"
expect "active: EPRT crossed as PORT, the server connected back, the file arrived whole" \
  test "$(cf active)" = "ok 200000"
expect "and passive again, on the stream both rewrites renumbered" test "$(cf passive2)" = "ok 200000"
expect_in "the server was sent PASV" "PASV" "$MAIN"
expect_in "the server was sent PORT with the subscriber's public address ($1)" \
  "PORT $(echo "$1" | tr . ,)," "$MAIN"
expect_not_in "and never an EPRT (it does not speak RFC 2428)" "EPRT" "$MAIN"
PORTP=$(printf '%s\n' "$MAIN" | sed -n 's/^PORT \([0-9]*,\)\{4\}\([0-9]*\),\([0-9]*\)$/\2 \3/p' | head -1)
DPORT=$(echo "$PORTP" | awk '{print $1 * 256 + $2}')
expect "the PORT's port is in the subscriber's block ($2-$3: ${DPORT:-none})" \
  sh -c "[ -n '$DPORT' ] && [ '$DPORT' -ge $2 ] && [ '$DPORT' -le $3 ]"
expect_in "ALGS STATUS64 answered 216 EPSVEPRT" "216 EPSVEPRT" "$(cf status)"
expect_in "EPSV ALL answered 504, by the box" "504" "$(cf epsvall)"
expect_in "EPSV 1 answered 522, by the box" "522" "$(cf epsv1)"
# One EPSV reaches the server before AUTH, and it is the one sent while the
# ALG was disabled - after the fourth NOOP, which stood in for DISABLE64.
expect "neither reached the server: one EPSV in its log, the disabled one" \
  test "$(printf '%s\n' "$MAIN" | grep -c '^EPSV')" = 1
expect "and that one after the four NOOPs the box sent instead" \
  test "$(printf '%s\n' "$MAIN" | sed '/^EPSV/,$d' | grep -c '^NOOP')" = 4
expect_not_in "nor ALGS" "ALGS" "$MAIN"
expect_in "ALGS DISABLE64 answered 216 NONE" "216 NONE" "$(cf disable)"
expect_in "and then the server meets the client's EPSV itself, and refuses it" \
  "500" "$(cf disabled)"
expect_in "ALGS ENABLE64 answered 216 EPSVEPRT" "216 EPSVEPRT" "$(cf enable)"
expect "and passive works again" test "$(cf enabled)" = "ok 200000"
expect_in "after all of it the control stream is still in step: NOOP answered 200" \
  "200" "$(cf noop)"
expect_in "and QUIT answered" "221" "$(cf quit)"
expect_in "AUTH accepted by the server" "234" "$(cf auth)"
expect_in "after AUTH the box translates nothing: EPSV reaches the server" \
  "500" "$(cf afterauth)"
expect_in "the server's log has that EPSV, after the AUTH" "EPSV" \
  "$(printf '%s\n' "$SRVCMDS" | sed -n '/^AUTH/,$p')"
expect "commands translated, counted ($N_CMD)" test "${N_CMD:-0}" -ge 5
expect "replies translated, counted ($N_REP)" test "${N_REP:-0}" -ge 5
expect "the subscriber's segments renumbered, counted ($N_SEQ_OUT)" \
  test "${N_SEQ_OUT:-0}" -gt 0
expect "and the server's, counted ($N_SEQ_IN)" test "${N_SEQ_IN:-0}" -gt 0
expect_in "the running configuration carries alg ftp on" "alg ftp on" "$(cat /tmp/f64-config.txt)"
expect_in "show bpa pool reports ftp on, on the NAT64 pool" "ftp on" "$POOLALG"
expect_in "alg rtsp on is refused at commit, by name" "runs no rtsp ALG" "$REFUSED"
expect_in "the dataplane refuses alg sip on a NAT64 pool, by name" "no SIP ALG" "$DPREFUSED"
expect_in "and the pool still runs no sip" "sip off" "$POOLALG2"
verdict
