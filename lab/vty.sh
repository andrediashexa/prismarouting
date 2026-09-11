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
#
# The accounts are created BY THE PRODUCT and there is no usermod anywhere
# below. This lab used to grant every membership by hand, so it proved a box
# nobody receives: the VTY created accounts in no group at all, and every role
# it reported was unreachable on every installed box (#184). A lab that
# configures the box under test cannot notice the box failing to configure
# itself.
#
# What is still done by hand is only what appliance/install.sh does at install
# time and no lab image has.
#
# The two role groups. install.sh creates them; without them here the VTY
# would refuse the role - correctly - and this lab would be testing the
# refusal instead of the grant.
groupadd -f hexa-admin 2>/dev/null
groupadd -f hexa-cgnat 2>/dev/null
# The login shell the VTY marks its accounts with. install.sh puts it there.
install -D -m 0755 /src/appliance/hexash-login /usr/bin/hexash-login
# An account that may configure has to be able to write the rollback store and
# the saved configuration. Both hold the whole configuration, password hashes
# included, so granting the capability grants sight of them - which is why the
# directory is group-owned rather than world-anything. Also #76.
#
# hexa-cgnat owns it and hexa-admin is ADDITIVE on top: an administrator is a
# member of both. Owning it with hexa-admin instead would have handed the
# cgnat operator administrator rights, which is the opposite of the point.
chgrp -R hexa-cgnat /etc/hexarouting 2>/dev/null
chmod 0770 /etc/hexarouting 2>/dev/null
mkdir -p /etc/hexarouting/rollback
chgrp hexa-cgnat /etc/hexarouting/rollback 2>/dev/null
chmod 0770 /etc/hexarouting/rollback 2>/dev/null
# 0660 and group hexa-cgnat, which is what appliance/install.sh installs. It
# used to be 0644 here: world-readable, which no appliance ships, and which
# handed the read-only account a copy of every password hash on the box the
# product deliberately does not give it.
chgrp hexa-cgnat /etc/hexarouting/hexarouting.conf 2>/dev/null
chmod 0660 /etc/hexarouting/hexarouting.conf 2>/dev/null

echo "  -- the VTY creates the accounts and grants the roles"
MAKE=$($HX -c "configure" \
    -c "user ro" -c "exit" \
    -c "user cg" -c "role cgnat-operator" -c "exit" \
    -c "user netadm" -c "role administrator" -c "exit" \
    -c "commit" 2>&1)
printf '%s\n' "$MAKE" | sed 's/^/    /'
# The memberships as the SYSTEM reports them, not as the VTY believes them.
# The whole defect was a VTY whose belief and the box's answer never met.
RO_GROUPS=" $(id -nG ro 2>/dev/null) "
CG_GROUPS=" $(id -nG cg 2>/dev/null) "
NETADM_GROUPS=" $(id -nG netadm 2>/dev/null) "
for u in ro cg netadm; do echo "    $u: $(id -nG $u 2>/dev/null)"; done
# And as the VTY reads it BACK off the box, which is where the role in the
# running configuration has to come from.
ROLES_BACK=$($HX -c "show running-config" 2>/dev/null | grep -A2 "^user " \
             | grep -E "^user |^ role " | tr '\n' '|')
echo "    running-config: $ROLES_BACK"

# "api-segment { gid vpp }" group-owns /dev/shm/vpe-api and NOT
# /dev/shm/global_vm, which a client maps as well - so a non-root API call
# fails with svm_region_map(mmap open): Permission denied. On an installed box
# a drop-in on vpp.service does this; here it is done directly. Filed as #76:
# an appliance where only root can use the binary API is an appliance where
# roles are decorative.
#
# AFTER the last command run as root, and measured rather than assumed: a ROOT
# API client puts /dev/shm/global_vm back to root:root, so every non-root
# client that follows one crashes in svm_region_map. Doing it here is what
# makes the accounts below usable; it is also a defect of its own, since on an
# installed box hexarouting.service applies the configuration as root at every
# boot and leaves the region that way.
chgrp vpp /dev/shm/global_vm 2>/dev/null
chmod 0660 /dev/shm/global_vm 2>/dev/null

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
echo "  -- administrator passes the routing change the cgnat operator was refused"
# "commit check" rather than "commit", and the reason is not that a check is
# safer. frr-reload.py logs to /var/log/frr/frr-reload.log and the frr package
# ships that directory drwxr-x--- frr:frr, so NO non-root account can APPLY a
# routing change here whatever its groups - a defect of its own, about a
# directory mode and not about a membership, and not fixed here.
#
# The check is not a weaker assertion of the thing this lab is about: the
# authorization decision is made on a check-only run too, deliberately and for
# its own reasons, so the refusal that hits the cgnat operator two commands
# above would hit an administrator here just the same.
NETADM_ROUTE=$(as netadm -c "'configure'" -c "'router bgp 65000'" \
  -c "'neighbor 10.0.0.2 description NETADM-WAS-HERE'" -c "'exit'" \
  -c "'commit check'")
printf '%s\n' "$NETADM_ROUTE" | sed 's/^/    /'
echo "  -- and applies a CGNAT change, because hexa-admin carries that too"
NETADM_POOL=$(as netadm -c "'configure'" -c "'cgnat pool provider-a'" \
  -c "'alg ftp off'" -c "'exit'" -c "'commit'")
printf '%s\n' "$NETADM_POOL" | sed 's/^/    /'
NETADM_REACHED=$(vppctl show bpa config | grep -c "alg ftp off")

echo "  -- the demotion: 'no role', and what the box says afterwards"
# A demotion that leaves the membership behind is not a demotion. Asserted
# through what the account can still DO, because that is the thing the
# membership decides - reading the group list back would only prove the VTY
# ran a command.
DEMOTE=$($HX -c "configure" -c "user netadm" -c "no role" -c "exit" -c "commit" 2>&1)
printf '%s\n' "$DEMOTE" | sed 's/^/    /'
NETADM_GROUPS_AFTER=" $(id -nG netadm 2>/dev/null) "
echo "    netadm afterwards: $NETADM_GROUPS_AFTER"
NETADM_AFTER=$(as netadm -c "'configure'")
printf '%s\n' "$NETADM_AFTER" | sed 's/^/    /'
NETADM_SHOW_AFTER=$(as netadm -c "'show running-config'")

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
# Prefix-lists complete from the control plane, not from a model this VTY
# keeps: FRR owns them and the running configuration is read back from FRR.
sh.feed("ip prefix-list PL-LAB seq 10 permit 10.9.0.0/16")
opts, _, _ = sh._options("ip prefix-list ", False)
print("PLISTS:", " ".join(sorted(opts)))
o, _, _ = sh._options("ip ", False)
print("IPSUB:", " ".join(sorted(o)))
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
sh.feed("exit")
# What "?" offers inside a block, against a real box (#112).
sh.feed("interface " + ifname)
o, _, _ = sh._options("", False)
print("IN_BLOCK:", " ".join(sorted(o)))
sh.feed("exit")
# Case-insensitive completion of a real interface name.
o, _, _ = sh._options("interface " + ifname[:4].lower(), True)
print("LOWER:", " ".join(sorted(o)))
# show interface must answer from the dataplane, not from FRR.
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show interface")
print("SHOW_IF:", out.getvalue().replace("\n", " | "))
# BGP: the block the VTY used to pass through without understanding a word of
# it, so "?" offered nothing and a typo was found by FRR mid-commit (#142).
sh.feed("router bgp 65000")
o, _, _ = sh._options("", False)
print("BGP_BLOCK:", " ".join(sorted(o)))
sh.feed("neighbor 10.0.0.2 remote-as 65001")
sh.feed("neighbor TRANSITO peer-group")
# The neighbour completes by ADDRESS, from what has been configured, with the
# AS beside it - an ISP box carries several and they are told apart by AS.
o, _, _ = sh._options("neighbor ", False)
print("BGP_NEIGH:", " ".join(sorted(o)))
o, _, _ = sh._options("neighbor ", False)
print("BGP_NEIGH_DESC:", " | ".join(f"{k}={v}" for k, v in sorted(o.items())))
# And the words that follow an address are the neighbour's, not the block's.
o, _, _ = sh._options("neighbor 10.0.0.2 ", False)
print("BGP_STMT:", " ".join(sorted(o)))
o, _, _ = sh._options("neighbor 10.0.0.2 peer-group ", False)
print("BGP_PG:", " ".join(sorted(o)))
sh.feed("address-family ipv4 unicast")
o, _, _ = sh._options("", False)
print("BGP_AF:", " ".join(sorted(o)))
o, _, _ = sh._options("neighbor 10.0.0.2 ", False)
print("BGP_AF_STMT:", " ".join(sorted(o)))
# Asked as a question rather than by matching a whole sorted line: an
# assertion anchored on the full list breaks every time a statement is added,
# and reads as a regression when it is not one.
print("BGP_AF_HAS_REMOTE_AS:", "yes" if "remote-as" in o else "no")
sh.feed("exit-address-family")
sh.feed("exit")
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
# Which needs the dataplane socket AND FRR's, both group-owned: the first line
# comes from VPP over the binary API and the second from vtysh. An account
# outside vpp, frr and frrvty reads neither, so this is where those three
# memberships are proven rather than merely written.
expect_in "including the routing half, which comes from FRR" \
  "router bgp 65000" "$RO_SHOW"

expect_in "a cgnat operator can change a pool" "commit complete" "$CG_OK"
expect "and the change reached the dataplane" test "${ALG_AFTER:-0}" -ge 1
expect_in "but cannot change routing" "permission denied" "$CG_ROUTE"
expect_in "and cannot reach a shell either" "permission denied" "$CG_SHELL"

expect_in "an administrator passes the routing change the operator was refused" \
  "configuration check succeeds" "$NETADM_ROUTE"
expect_in "and applies a CGNAT change, since hexa-admin carries that too" \
  "commit complete" "$NETADM_POOL"
expect "and that change reached the dataplane" test "${NETADM_REACHED:-0}" -ge 1

# --- issue #184: the memberships come from the PRODUCT ---
#
# Nothing above ran usermod. Every membership asserted here was written by
# `commit`, which is the whole difference between a role an operator is given
# and a role a lab hands out on the product's behalf.
expect_in "the VTY says it granted the role group" "added to group hexa-admin" \
  "$MAKE"
expect_in "an administrator is put in hexa-admin" " hexa-admin " "$NETADM_GROUPS"
expect_in "and in hexa-cgnat with it, which owns the configuration directory" \
  " hexa-cgnat " "$NETADM_GROUPS"
expect_in "a cgnat operator is put in hexa-cgnat" " hexa-cgnat " "$CG_GROUPS"
expect_not_in "and NOT in hexa-admin" " hexa-admin " "$CG_GROUPS"
# The reach every role needs before authorization is even the question: VPP's
# sockets are group vpp and /etc/frr is group frr, so an account outside them
# is refused by the SOCKET on every command - including the read-only ones.
#
# Nothing in this lab opens those up, and nothing may. lab/startup-lcp.conf
# carries the same "gid vpp" that appliance/etc/vpp-base.conf ships and
# /etc/frr keeps the frr package's own frr:frr 0750, so the membership the
# product wrote is what reaches them - the mechanism an installed box has. A
# "chmod 0666 cli.sock" or a "chmod -R a+rX /etc/frr" here would make these
# pass on a box where no operator can open either.
for g in vpp frr frrvty; do
  expect_in "every account the VTY makes reaches $g: read-only" " $g " "$RO_GROUPS"
  expect_in "  cgnat operator" " $g " "$CG_GROUPS"
  expect_in "  administrator" " $g " "$NETADM_GROUPS"
done
# Read back off the box rather than remembered, or the running configuration
# would go on reporting a role somebody removed by hand (#161).
expect_in "the running configuration reports the role it granted" \
  "user netadm| role administrator" "$ROLES_BACK"
expect_in "and the other one" "user cg| role cgnat-operator" "$ROLES_BACK"
expect_not_in "and reports no role for the account that has none" \
  "user ro| role" "$ROLES_BACK"

# --- an account with no role can do nothing ---
#
# The negative half. `ro` was created by the product and given nothing, and
# the assertions above already show it cannot configure, cannot commit and
# cannot reach a shell. What is left is that being given nothing is a real
# state and not an accident of the groups.
expect_not_in "an account with no role is in neither role group" \
  " hexa-admin " "$RO_GROUPS"
expect_not_in "  nor the other" " hexa-cgnat " "$RO_GROUPS"

# --- a demotion is one ---
expect_in "'no role' says it took the membership away" \
  "removed from group hexa-admin" "$DEMOTE"
expect_not_in "and the box no longer has the account in it" \
  " hexa-admin " "$NETADM_GROUPS_AFTER"
expect_not_in "nor in hexa-cgnat" " hexa-cgnat " "$NETADM_GROUPS_AFTER"
expect_in "the demoted administrator is refused where it was allowed" \
  "permission denied" "$NETADM_AFTER"
# But not stripped of everything: a demotion makes an account read-only, and a
# read-only account that cannot read is a broken account, not a demoted one.
expect_in "and can still read" "cgnat pool" "$NETADM_SHOW_AFTER"
expect_in "  because the reach it needs to read was left alone" " vpp " \
  "$NETADM_GROUPS_AFTER"

# --- the VTY offers what the box has (#98) ---
#
# Before this, "interface <TAB>" answered "<word>" and the operator went to
# vppctl or "ip link" in another window - the two tools this VTY exists to
# keep them out of - at the first argument.
# The INTERFACES line, not the whole blob. This assertion meant "completion
# does not offer local0" and never said which line it was about, so a listing
# that mentions local0 elsewhere - correctly, because the dataplane reports it -
# made it fail. An assertion that does not name what it is about is one that
# breaks for reasons that have nothing to do with it.
COMP_IFACES=$(printf '%s\n' "$COMP" | sed -n 's/^ *INTERFACES: *//p')
# The same reason the line below it needs it: "pg0" is printed on several
# lines of this blob, so asserting it against the whole thing passed with
# completion offering nothing at all - which is exactly the state #98 fixed.
expect_in "interface completion names a real dataplane interface" \
  "pg0" "$COMP_IFACES"
expect_not_in "and not VPP's own loopback, which configures nothing" \
  "local0" "$COMP_IFACES"
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

# --- what the operator can find out from the prompt (#112) ---
# Inside a block, only what is valid there. Offering the level above invites
# somebody to type "cgnat pool" inside an interface.
# The boundary, not the inventory: an interface must offer its own statements
# and none from the level above. Listing them all here meant editing this line
# every time the block grew, which tested the list rather than the rule.
# The label is not the answer. This asserted "IN_BLOCK: " - which print()
# emits whether or not anything follows it - so a block offering NOTHING
# passed. It was written that way while deliberately replacing a hardcoded
# list with a rule, which is how a stronger-looking assertion became a vacuous
# one. Naming two statements that must be there is the rule, checkably.
# ...and the words after it against that ONE line, not against the blob. The
# blob also carries IFACE_THIS, which reads "interface host-veth-vpp | mtu
# 1500 |", so "mtu" was satisfied by the statement the lab had just typed -
# and the assertion that the interface block still OFFERS mtu passed with the
# block offering no mtu at all, which is #112 exactly. Same reason "pg0" and
# "up" needed their own row forty lines up.
COMP_IN_BLOCK=$(printf '%s\n' "$COMP" | sed -n 's/^ *IN_BLOCK: *//p')
# A whole word off a space-separated list: "mtu" must not be matched by a
# statement that merely starts with it, and an empty list must not match at all.
has_word() { printf ' %s ' "$2" | grep -q " $1 "; }
echo "  the interface block offers: ${COMP_IN_BLOCK:-<nothing>}"
expect_in "inside an interface, '?' offers its own statements" \
  "IN_BLOCK: description " "$COMP"
# "urpf" appears exactly once in this blob, on IN_BLOCK's own line, so unlike
# "mtu" below it this one does fail when the block stops offering it. Left as
# it was: an assertion rewritten without being shown to fail first is one
# nobody has verified.
expect_in "and the rest of the block with them" "urpf" "$COMP"
expect "including the ones that were always there" \
  has_word mtu "$COMP_IN_BLOCK"
expect_not_in "and nothing from the level above leaks in" \
  "IN_BLOCK: cgnat" "$COMP"

# --- BGP: the block that used to offer nothing (#142) ---
#
# Every assertion here names a WORD the grammar has to offer, not just that the
# answer is non-empty: "some options came back" passes on the wrong block.
COMP_BGP_BLOCK=$(printf '%s\n' "$COMP" | sed -n 's/^ *BGP_BLOCK: *//p')
COMP_BGP_NEIGH=$(printf '%s\n' "$COMP" | sed -n 's/^ *BGP_NEIGH: *//p')
echo "  router bgp offers: ${COMP_BGP_BLOCK:-<nothing>}"
echo "  neighbor <TAB> offers: ${COMP_BGP_NEIGH:-<nothing>}"
expect_in "inside 'router bgp', '?' offers its own statements" \
  "BGP_BLOCK: address-family " "$COMP"
# Against BGP_BLOCK's own line. The address family two lines below offers a
# "neighbor" of its own, so this passed with `router bgp` offering none - the
# statement that creates a session, missing from the block an operator enters
# to create one, which is the whole of #142.
expect "including the one that creates a session" \
  has_word neighbor "$COMP_BGP_BLOCK"
expect_not_in "and nothing from the level above leaks in" \
  "BGP_BLOCK: cgnat" "$COMP"
# A neighbour completes from what is configured, not from a fixed list.
expect_in "a neighbour completes by address once it exists" \
  "BGP_NEIGH: 10.0.0.2" "$COMP"
# BGP_PG carries TRANSITO too - it is the same name, completed as the value of
# `peer-group` - so this passed with the neighbour completion returning an
# empty list. It is about BGP_NEIGH's line or it is about nothing.
expect "and a peer group is offered by name too" \
  has_word TRANSITO "$COMP_BGP_NEIGH"
# The AS beside it, because an ISP box carries several sessions and they are
# told apart by who is on the other end, not by the last octet.
expect_in "the completion says which AS is on the other end" \
  "10.0.0.2=AS 65001" "$COMP"
# The words AFTER an address are the neighbour's, which is the part the tree
# could not reach: the address is an argument, so the walk stopped there and
# everything past it fell through to the configuration tree.
expect_in "the words after a neighbour's address are the neighbour's" \
  "BGP_STMT: bfd" "$COMP"
# Same as "urpf" above: "remote-as" is on BGP_STMT's line and nowhere else in
# the blob, so it can fail, and it was left alone.
expect_in "including the one that opens the session" "remote-as" "$COMP"
expect_not_in "and not the block's own statements" "BGP_STMT: address-family" "$COMP"
# A peer group completes only where one is declared.
expect_in "a declared peer group completes as a value" \
  "BGP_PG: TRANSITO" "$COMP"
# The address family is a different block with a different vocabulary.
expect_in "an address family offers its own statements" \
  "BGP_AF: exit exit-address-family" "$COMP"
expect_in "and 'activate' is offered there and not above" \
  "BGP_AF_STMT: activate" "$COMP"
expect_in "while 'remote-as' is not, because it does not belong there" \
  "BGP_AF_HAS_REMOTE_AS: no" "$COMP"
# A peer group is completed where a neighbour is, and must not be described as
# one: an operator reading "neighbour" beside it goes looking for a router at
# that name.
expect_in "a peer group is labelled as a group, not as a neighbour" \
  "TRANSITO=peer group" "$COMP"

# A prefix-list an operator typed completes by name afterwards, from the
# configuration the box holds - the same source `show running-config` answers
# from, so completion cannot offer a name the box does not have.
expect_in "a prefix-list completes by name once it exists" "PL-LAB" \
  "$(printf '%s\n' "$COMP" | sed -n 's/^ *PLISTS: *//p')"
expect_in "and 'ip' offers prefix-list beside route" "prefix-list" \
  "$(printf '%s\n' "$COMP" | sed -n 's/^ *IPSUB: *//p')"
# Completion that needs the right case is completion for somebody who already
# knows the answer.
# A lowercase prefix must complete to SOMETHING, and to a name the dataplane
# actually has - checked against the interfaces line rather than a list built
# by a second parse of vppctl output, which is how the first version of this
# ended up comparing against counter columns.
COMP_LOWER=$(printf '%s\n' "$COMP" | sed -n 's/^ *LOWER: *//p')
expect "a lowercase prefix completes a real interface name" \
  sh -c "[ -n \"$COMP_LOWER\" ]"
expect_in "and completes to a name the dataplane has" \
  "$COMP_LOWER" "$COMP_IFACES"
# show interface answered from FRR, which sees only what linux_cp mirrored -
# so a box with dataplane interfaces reported "lo".
# Against SHOW_IF's own rows. "pg0" is already on the INTERFACES line of this
# same blob, so both of these passed with "show interface" answering nothing
# but an error - the very regression the command exists to prevent. "up" is
# worse still: it is a substring of half the words in the blob.
COMP_SHOWIF=$(printf '%s\n' "$COMP" | sed -n 's/^ *SHOW_IF: *//p')
PG0_ROW=$(printf '%s\n' "$COMP_SHOWIF" | tr '|' '\n' \
          | sed -n 's/^ *\(pg0 .*\)$/\1/p' | head -1)
echo "  show interface's own row for pg0: '${PG0_ROW:-<none>}'"
expect_in "show interface reports the dataplane's interfaces" "pg0" "$PG0_ROW"
expect_in "with their state, on that same row" "up" "$PG0_ROW"
expect_in "with the statements inside it" "inside 100.64.0.0/22" "$COMP"

verdict
