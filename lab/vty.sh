#!/bin/sh
# The HexaRouting VTY: one CLI for both routing and CGNAT.
set -u
mkdir -p /run/vpp /vpp_plugins /etc/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
use_binary_api

HX="python3 /src/src/hexash/hexash.py"
export HEXA_CONFIG=/etc/hexarouting/hexarouting.conf

ip link add veth-out type veth peer name veth-vpp 2>/dev/null
ip link set veth-vpp up
ip netns add peer 2>/dev/null
ip link set veth-out netns peer
ip netns exec peer ip link set lo up
ip netns exec peer ip link set veth-out up
ip netns exec peer ip addr add 10.0.0.2/24 dev veth-out
ip netns exec peer ip link add dum type dummy
ip netns exec peer ip addr add 203.0.113.1/24 dev dum
ip netns exec peer ip link set dum up

vpp -c /src/lab/startup-lcp.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl create host-interface name veth-vpp >/dev/null
vppctl set interface state host-veth-vpp up
vppctl lcp create host-veth-vpp host-if vppwan0
vppctl set interface mtu 1500 host-veth-vpp
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
vppctl set interface ip address pg0 100.64.3.254/22

mkdir -p /var/run/frr /etc/frr
printf 'hostname hexarouting\n' > /etc/frr/zebra.conf
printf 'hostname hexarouting\n' > /etc/frr/bgpd.conf
chown -R frr:frr /var/run/frr /etc/frr
/usr/lib/frr/zebra -d -A 127.0.0.1 -f /etc/frr/zebra.conf
/usr/lib/frr/bgpd  -d -A 127.0.0.1 -f /etc/frr/bgpd.conf

mkdir -p /var/run/frr/peer /etc/frr/peer
printf 'hostname upstream\n' > /etc/frr/peer/zebra.conf
cat > /etc/frr/peer/bgpd.conf <<'EOF'
hostname upstream
router bgp 65001
 bgp router-id 10.0.0.2
 no bgp ebgp-requires-policy
 neighbor 10.0.0.1 remote-as 65000
 address-family ipv4 unicast
  network 203.0.113.0/24
  neighbor 10.0.0.1 activate
 exit-address-family
EOF
chown -R frr:frr /var/run/frr/peer /etc/frr/peer
ip netns exec peer /usr/lib/frr/zebra -d -N peer -A 127.0.0.1 -f /etc/frr/peer/zebra.conf
ip netns exec peer /usr/lib/frr/bgpd  -d -N peer -A 127.0.0.1 -f /etc/frr/peer/bgpd.conf
sleep 2

echo "=========== CGNAT pela VTY ==========="
# Under the candidate model nothing reaches the device until "commit".
# This lab used to end at "exit", which now discards the candidate - the
# check that caught it is the reason each lab states its own verdict.
$HX -c "configure" \
    -c "cgnat pool provider-a" \
    -c "inside 100.64.0.0/22" \
    -c "outside 200.150.0.0/24" \
    -c "outside 45.170.16.0/26" \
    -c "block-size 512" \
    -c "policy interleaved" \
    -c "mode deterministic" \
    -c "exit" \
    -c "cgnat interface pg0 inside" \
    -c "cgnat timeout udp 300" \
    -c "commit"
echo "  -- o dataplane recebeu?"
vppctl show bpa pool | sed 's/^/    /'

echo
echo "=========== ROTEAMENTO pela MESMA VTY ==========="
$HX -c "configure" \
    -c "router bgp 65000" \
    -c "bgp router-id 10.0.0.1" \
    -c "no bgp ebgp-requires-policy" \
    -c "neighbor 10.0.0.2 remote-as 65001" \
    -c "commit"
echo "  -- o FRR recebeu?"
vtysh -c "show run" 2>/dev/null | grep -A4 "router bgp" | sed 's/^/    /'

echo
echo "=========== the BGP session comes up and the route reaches the VPP FIB ==========="
n=0
until vppctl show ip fib 203.0.113.0/24 2>/dev/null | grep -q "^203.0.113.0/24" || [ "$n" -ge 40 ]; do sleep 1; n=$((n+1)); done
echo "  (${n}s)"
vppctl show ip fib 203.0.113.0/24 | grep -oE "ipv4 via [0-9.]+ [a-z0-9-]+" | head -1 | sed 's/^/    /'

echo
echo "=========== show running-config: os dois mundos ==========="
$HX -c "show running-config" 2>/dev/null | grep -vE "^(Building|Current|frr version|frr defaults|hostname|log |service |!$)" | head -22 | sed 's/^/  /'

echo
echo "=========== an invalid pool must be refused and NOT enter the config ==========="
$HX -c "configure" \
    -c "cgnat pool bad" \
    -c "inside 100.64.0.0/22" \
    -c "outside 10.0.0.0/24" \
    -c "exit" \
    -c "commit" 2>&1 | sed 's/^/  /'
echo "  -- does 'bad' appear in the running-config? (it must not)"
$HX -c "show running-config" 2>/dev/null | grep -c "cgnat pool bad" | sed 's/^/    occurrences: /'

echo
echo "=========== write memory e recarga ==========="
$HX -c "save" 2>&1 | head -2 | sed 's/^/  /'
echo "  -- file written:"
cat "$HEXA_CONFIG" | sed 's/^/    /'
echo "  -- deleting the pool from the dataplane and reloading from the file"
vppctl bpa pool provider-a del
vppctl show bpa pool | head -1 | sed 's/^/    antes: /'
$HX --load "$HEXA_CONFIG"
vppctl show bpa pool | head -2 | sed 's/^/    depois: /'

echo
echo "=========== roles: what each account may change ==========="
# Real accounts in real groups, run as themselves. No environment variable
# that says "pretend I am read-only": a security check with a test-only way
# past it is a security check with a way past it.
groupadd -f hexa-admin 2>/dev/null
groupadd -f hexa-cgnat 2>/dev/null
for u in ro cg adm; do
  id "$u" >/dev/null 2>&1 || useradd -M -s /bin/sh "$u" 2>/dev/null
done
usermod -aG hexa-cgnat cg 2>/dev/null
usermod -aG hexa-admin adm 2>/dev/null
# The dataplane socket is group-owned by vpp; every role needs to reach it to
# read anything at all. That membership is what a read-only account MUST NOT
# also give it - see docs/routing.md and issue #76.
# One user per call: "usermod -aG vpp ro cg adm" adds only the first, and the
# rest silently do not get the group - which looks exactly like an
# authorization refusal three commands later.
for u in ro cg adm; do usermod -aG vpp "$u" 2>/dev/null; done
# The dataplane API socket and FRR's own socket are the two things every role
# has to reach before authorization is even the question. Granting them here
# is what makes the checks below measure AUTHORIZATION rather than file
# permissions.
chmod 0777 /run/vpp 2>/dev/null
chmod 0666 /run/vpp/api.sock /run/vpp/cli.sock 2>/dev/null
for g in frr frrvty; do usermod -aG "$g" ro 2>/dev/null; usermod -aG "$g" cg 2>/dev/null; usermod -aG "$g" adm 2>/dev/null; done
chmod -R a+rX /etc/frr 2>/dev/null
# "api-segment { gid vpp }" group-owns /dev/shm/vpe-api and NOT
# /dev/shm/global_vm, which a client maps as well - so a non-root API call
# fails with svm_region_map(mmap open): Permission denied. Corrected here and
# filed as #76: an appliance where only root can use the binary API is an
# appliance where roles are decorative.
chgrp vpp /dev/shm/global_vm 2>/dev/null
chmod 0660 /dev/shm/global_vm 2>/dev/null
# An account that may configure has to be able to write the rollback store and
# the saved configuration. Both hold the whole configuration, password hashes
# included, so granting the capability grants sight of them - which is why the
# directory is group-owned rather than world-anything. Also #76.
chgrp -R hexa-cgnat /etc/hexarouting 2>/dev/null
chmod 0770 /etc/hexarouting 2>/dev/null
mkdir -p /etc/hexarouting/rollback
chgrp hexa-cgnat /etc/hexarouting/rollback 2>/dev/null
chmod 0770 /etc/hexarouting/rollback 2>/dev/null
# hexa-cgnat owns the configuration directory and hexa-admin is ADDITIVE on
# top of it: an administrator is a member of both. Owning it with hexa-admin
# instead would have handed the cgnat operator administrator rights, which is
# the opposite of the point.
usermod -aG hexa-cgnat adm 2>/dev/null
chmod 0644 /etc/hexarouting/hexarouting.conf 2>/dev/null
as() { u=$1; shift; su -s /bin/sh -c "HEXA_CONFIG=$HEXA_CONFIG python3 /src/src/hexash/hexash.py $*" "$u" 2>&1; }

echo "  -- read-only account tries to configure"
RO_CONF=$(as ro -c "'configure'")
printf '%s\n' "$RO_CONF" | sed 's/^/    /'
echo "  -- read-only account tries to commit a pool, command issued directly"
RO_COMMIT=$(as ro -c "'configure'" -c "'cgnat pool nope'" -c "'inside 192.168.0.0/24'" -c "'outside 198.51.100.64/30'" -c "'exit'" -c "'commit'")
printf '%s\n' "$RO_COMMIT" | sed 's/^/    /'
echo "  -- read-only account tries the hidden shell escape"
RO_SHELL=$(as ro -c "'start shell'")
printf '%s\n' "$RO_SHELL" | sed 's/^/    /'
echo "  -- read-only account can still look"
RO_SHOW=$(as ro -c "'show running-config'")
printf '%s\n' "$RO_SHOW" | sed 's/^/    /'
echo "  -- cgnat operator changes a pool setting"
CG_OK=$(as cg -c "'configure'" -c "'cgnat pool provider-a'" -c "'alg rtsp off'" -c "'exit'" -c "'commit'")
printf '%s\n' "$CG_OK" | sed 's/^/    /'
echo "  -- cgnat operator tries to change routing"
CG_ROUTE=$(as cg -c "'configure'" -c "'router bgp 65000'" -c "'exit'" -c "'commit'")
printf '%s\n' "$CG_ROUTE" | sed 's/^/    /'
echo "  -- and tries the shell"
CG_SHELL=$(as cg -c "'start shell'")
printf '%s\n' "$CG_SHELL" | sed 's/^/    /'
ALG_AFTER=$(vppctl show bpa config | grep -c "alg rtsp off")
POOL_AFTER=$(vppctl show bpa pool | grep -c "pool nope")

echo
echo "=========== the box's own names, offered by the VTY (#98) ==========="
# The tree knows a name goes here; the box knows which names. Asserted against
# a REAL dataplane, because the value of this is that it reflects the machine -
# a stub would prove the plumbing and not the answer.
IFACE_LIST=$(vppctl show interface | awk 'NR>1 && $1 !~ /^ / {print $1}' | tr '\n' ' ')
echo "  the dataplane has: $IFACE_LIST"
COMP=$(python3 - <<'PYEOF'
import sys
sys.path.insert(0, "/src/src/hexash")
import hexash
sh = hexash.Shell()
sh.caps = {hexash.CAP_ALL, hexash.CAP_CGNAT}
sh.feed("configure")
opts, hint, _ = sh._options("interface ", False)
print("INTERFACES:", " ".join(sorted(opts)))
print("HINT:", hint)
sh.feed("cgnat pool lab")
sh.feed("inside 100.64.0.0/22")
sh.feed("outside 200.150.0.16/30")
import io, contextlib
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show this")
print("THIS:", out.getvalue().replace("\n", " | "))
sh.feed("exit")
opts, _, _ = sh._options("cgnat pool ", False)
print("POOLS:", " ".join(sorted(opts)))
# The prompt has to name WHICH interface, and show this has to show what that
# interface holds - both against a real dataplane (#108).
ifname = sorted(sh.dyn_interfaces())[0]
sh.feed("interface " + ifname)
print("PROMPT:", sh.prompt().strip())
sh.feed("mtu 1500")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show this")
print("IFACE_THIS:", out.getvalue().replace("\n", " | "))
PYEOF
)
printf '%s\n' "$COMP" | sed 's/^/  /'

echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
R=$($HX -c "show running-config" 2>/dev/null)
expect_in "CGNAT is in the running config" "cgnat pool" "$R"
expect_in "routing is in the same running config" "router bgp 65000" "$R"
expect_not_in "the refused pool stayed out" "cgnat pool bad" "$R"
expect "the BGP route reached the VPP FIB" sh -c "vppctl show ip fib 203.0.113.0/24 | grep -q '^203.0.113.0/24'"

# Issue #35. The refusals are asserted at the point of application, with the
# commands issued directly rather than through the completion tree.
expect_in "a read-only account cannot enter configuration mode" \
  "permission denied" "$RO_CONF"
expect_in "and cannot commit a pool" "permission denied" "$RO_COMMIT"
expect "and the pool it tried to create does not exist" \
  test "${POOL_AFTER:-1}" -eq 0
expect_in "and cannot reach a shell" "permission denied" "$RO_SHELL"
# The other half: a role that refuses everything is not a role, it is a broken
# VTY. Read-only must still be able to read.
expect_in "but can still read the running configuration" \
  "cgnat pool" "$RO_SHOW"

expect_in "a cgnat operator can change a pool" "commit complete" "$CG_OK"
expect "and the change reached the dataplane" test "${ALG_AFTER:-0}" -ge 1
expect_in "but cannot change routing" "permission denied" "$CG_ROUTE"
expect_in "and cannot reach a shell either" "permission denied" "$CG_SHELL"

# --- the VTY offers what the box has (#98) ---
#
# Before this, "interface <TAB>" answered "<word>" and the operator went to
# vppctl or "ip link" in another window - the two tools this VTY exists to
# keep them out of - at the first argument.
expect_in "interface completion names a real dataplane interface" \
  "pg0" "$COMP"
expect_not_in "and not VPP's own loopback, which configures nothing" \
  "local0" "$COMP"
# The pool exists only in the CANDIDATE at that moment, so this also proves
# the source is the configuration being edited and not the committed one.
expect_in "pool completion names the pool being edited" "POOLS: lab" "$COMP"
expect_in "show this prints the hierarchy being edited" \
  "cgnat pool lab" "$COMP"
# The prompt must name the subject, not only the kind: "[edit cgnat pool]" is
# the same prompt on a box with twenty pools.
expect_in "the prompt names which interface is being edited" \
  "edit interface" "$COMP"
# An empty "show this" is worse than no command: somebody who runs it and sees
# nothing concludes the block is empty and configures accordingly.
expect_in "and show this in an interface is not empty" "mtu 1500" "$COMP"
expect_in "with the statements inside it" "inside 100.64.0.0/22" "$COMP"

verdict
