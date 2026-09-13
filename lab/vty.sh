#!/bin/sh
# The HexaRouting VTY: one CLI for both routing and CGNAT.
set -u
mkdir -p /run/vpp /vpp_plugins /etc/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
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

vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl create host-interface name veth-vpp >/dev/null
vppctl set interface state host-veth-vpp up
# The pair comes from the product, not from this lab (#182). `vppctl lcp create`
# stood here, and every interface assertion below it was made on a box somebody
# had set up by hand: on an installed appliance nothing created a pair, so the
# VTY's own interface-existence check - which reads `show lcp` and accepts
# either name of a pair - had only VPP's names to work with, and an operator
# writing FRR's name for an interface was refused.
$HX -c "configure" -c "interface host-veth-vpp" -c "linux-name vppwan0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pair: /'
vppctl set interface mtu 1500 host-veth-vpp
vppctl set interface ip address host-veth-vpp 10.0.0.1/24
vppctl create packet-generator interface pg0 >/dev/null
vppctl set interface state pg0 up
vppctl set interface ip address pg0 100.64.3.254/22

mkdir -p /var/run/frr /etc/frr
printf 'hostname hexarouting\n' > /etc/frr/zebra.conf
printf 'hostname hexarouting\n' > /etc/frr/bgpd.conf
chown -R frr:frr /var/run/frr /etc/frr
start_frr bgpd

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
#
# 2770 and not 0770, which is also what install.sh states: the group is not
# enough on its own. Without setgid a revision root writes at boot is group
# root, and the operator who comes next cannot read the thing it is supposed to
# be able to roll back to.
chgrp -R hexa-cgnat /etc/hexarouting 2>/dev/null
chmod 2770 /etc/hexarouting 2>/dev/null
mkdir -p /etc/hexarouting/rollback
chgrp hexa-cgnat /etc/hexarouting/rollback 2>/dev/null
chmod 2770 /etc/hexarouting/rollback 2>/dev/null
# 0660 and group hexa-cgnat, which is what appliance/install.sh installs. It
# used to be 0644 here: world-readable, which no appliance ships, and which
# handed the read-only account a copy of every password hash on the box the
# product deliberately does not give it.
chgrp hexa-cgnat /etc/hexarouting/hexarouting.conf 2>/dev/null
chmod 0660 /etc/hexarouting/hexarouting.conf 2>/dev/null
# And the one file in /etc/frr an administrator has to be able to write, which
# install.sh also states. Applying a routing change ends with frr-reload asking
# vtysh to persist the result, and the frr package ships that file 0640
# frr:frr - so the administrator below could check the change and not make it
# (#186). The FILE and not the directory: with the directory group-writable
# vtysh creates a new frr.conf owned by whoever committed, and the next
# administrator cannot write it at all. Nothing else in /etc/frr is touched -
# it keeps the package's own frr:frr 0750, which is what the read-only account
# above proves it can still read through.
chmod 0660 /etc/frr/frr.conf 2>/dev/null

echo "  -- the VTY creates the accounts and grants the roles"
MAKE=$($HX -c "configure" \
    -c "user ro" -c "exit" \
    -c "user cg" -c "role cgnat-operator" -c "exit" \
    -c "user netadm" -c "role administrator" -c "exit" \
    -c "commit" 2>&1)
printf '%s\n' "$MAKE" | sed 's/^/    /'
# The modes after a commit run by ROOT, which is the case that made every later
# one fail: the VTY chmod'd 0700/0600 on every save and every commit, so the
# first one on the box - hexarouting.service applying the configuration at boot
# - took the group's access away (#188). They are stated by appliance/install.sh
# and by nothing else, so they must be exactly what was set above.
MODE_AFTER_ROOT="$(stat -c %a /etc/hexarouting) $(stat -c %a /etc/hexarouting/rollback)"
echo "    modes after root's commit: $MODE_AFTER_ROOT"
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
echo "  -- and the rollback revision that commit has to record"
# Asserted rather than read, because the failure is SURVIVABLE: the commit
# still says "commit complete" and the store being out of reach is one warning
# line above it, which is why an operator whose commits could not be undone
# went unnoticed until #188 was filed.
CG_RB=$(as cg -c "'show rollback'")
printf '%s\n' "$CG_RB" | head -2 | sed 's/^/    /'
RB_OWNER=$(stat -c %U /etc/hexarouting/rollback/0.conf 2>/dev/null || echo none)
RB_GROUP=$(stat -c %G /etc/hexarouting/rollback/0.conf 2>/dev/null || echo none)
echo "    revision 0 belongs to: $RB_OWNER:$RB_GROUP"
echo "  -- and the operator saves the running configuration"
CG_SAVE=$(as cg -c "'save'")
printf '%s\n' "$CG_SAVE" | sed 's/^/    /'
MODE_DIR=$(stat -c %a /etc/hexarouting)
MODE_RB=$(stat -c %a /etc/hexarouting/rollback)
MODE_CONF=$(stat -c %a /etc/hexarouting/hexarouting.conf)
GROUP_CONF=$(stat -c %G /etc/hexarouting/hexarouting.conf)
SAVE_STATE="$(printf '%s' "$CG_SAVE" | grep -c 'configuration saved') \
$MODE_DIR $MODE_RB $MODE_CONF $GROUP_CONF"
SAVE_STATE=$(printf '%s' "$SAVE_STATE" | tr -s ' \n' ' ')
echo "    afterwards: dir $MODE_DIR, store $MODE_RB, file $MODE_CONF $GROUP_CONF"
echo "  -- cgnat operator tries to change routing"
CG_ROUTE=$(as cg -c "'configure'" -c "'router bgp 65000'" -c "'exit'" -c "'commit'")
printf '%s\n' "$CG_ROUTE" | sed 's/^/    /'
echo "  -- and tries the shell"
CG_SHELL=$(as cg -c "'start shell'")
printf '%s\n' "$CG_SHELL" | sed 's/^/    /'
ALG_AFTER=$(vppctl show bpa config | grep -c "alg rtsp off")
POOL_AFTER=$(vppctl show bpa pool | grep -c "pool nope")
echo "  -- administrator APPLIES the routing change the cgnat operator was refused"
# "commit", not "commit check". This was a check for as long as no non-root
# account could apply a routing change at all: frr-reload.py opens
# /var/log/frr/frr-reload.log on every --reload whatever --stdout says, and the
# frr package ships that directory drwxr-x--- frr:frr, so the administrator was
# told the change was fine and then could not make it (#186). A role that ends
# at "your change is valid" is not the role this lab says it proves - and the
# split is the worst one available, because it is found in the maintenance
# window, after the check has already said yes.
NETADM_ROUTE=$(as netadm -c "'configure'" -c "'router bgp 65000'" \
  -c "'neighbor 10.0.0.2 description NETADM-WAS-HERE'" -c "'exit'" \
  -c "'commit'")
printf '%s\n' "$NETADM_ROUTE" | sed 's/^/    /'
# From FRR, not from what the VTY printed: "commit complete" is this VTY's own
# sentence, and the question is whether the routing daemon took a change from
# an account that is not root.
NETADM_IN_FRR=$(vtysh -c "show run" 2>/dev/null | grep -c "NETADM-WAS-HERE")
echo "    statements FRR holds with that description: $NETADM_IN_FRR"
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
# Deliberately the OLD spelling: the pool made here is the one the assertions
# below read back, so this run proves that a box driven the old way still
# works after the hierarchy moved (#193).
sh.feed("cgnat pool lab")
sh.feed("inside 100.64.0.0/22")
sh.feed("outside 200.150.0.16/30")
import io, contextlib
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show this")
print("THIS:", out.getvalue().replace("\n", " | "))
sh.feed("exit")
opts, _, _ = sh._options("nat cgnat pool ", False)
print("POOLS:", " ".join(sorted(opts)))
o, _, _ = sh._options("nat ", False)
print("NATSUB:", " ".join(sorted(o)))
o, _, _ = sh._options("router ", False)
print("ROUTERSUB:", " ".join(sorted(o)))
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
# A statement FRR cannot already be holding. `neighbor 10.0.0.2 activate` is
# written into this family by FRR itself under `bgp default ipv4-unicast`, so
# an assertion on that line would be satisfied by what was read back from FRR
# indented - which is the shape that never broke.
sh.feed("maximum-paths 4")
# `show this` in the middle of an edit, which is the only time anybody runs
# it. NOTHING in this session has been committed, so every line it reports
# exists only in the candidate - the case where it printed the header and
# stopped, because a statement typed here reaches the candidate unindented and
# the block was walked by indentation (#180).
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show this")
print("AF_THIS:", " ".join("[" + l + "]" for l in out.getvalue().splitlines()))
sh.feed("exit-address-family")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show this")
print("BGP_THIS:", " ".join("[" + l + "]" for l in out.getvalue().splitlines()))
sh.feed("exit")
# The block whose statements do NOT reach the candidate in the form they were
# typed: FRR has no `router static`, so they are held as top-level `ip route`
# lines. The operator is being shown the hierarchy they are editing, so they
# have to come back in that hierarchy's own words.
sh.feed("router static")
sh.feed("route 198.18.0.0/24 10.0.0.2")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sh.feed("show this")
print("STATIC_THIS:",
      " ".join("[" + l + "]" for l in out.getvalue().splitlines()))
sh.feed("exit")
PYEOF
)
printf '%s\n' "$COMP" | sed 's/^/  /'

echo
echo "=========== show this: mid-edit, and the same block after a commit ==========="
# The two shapes the routing half arrives in, against a real FRR. Read back
# from FRR a block is INDENTED; typed at this VTY it is not, because feed()
# strips leading whitespace before dispatch. `show this` walked it by
# indentation, so it reported a block being built RIGHT NOW as empty - the one
# moment anybody runs it (#180).
#
# Both runs ask for the same route in the same block, and both must answer in
# the block's own words: FRR has no `router static` and holds these as
# top-level `ip route` lines, so what comes back is a transform either way.
THIS_MID=$($HX -c "configure" -c "router static" \
               -c "route 198.18.0.0/24 10.0.0.2" -c "show this" 2>&1)
echo "  -- mid-edit, nothing committed:"
printf '%s\n' "$THIS_MID" | sed 's/^/    /'
$HX -c "configure" -c "router static" -c "route 198.18.0.0/24 10.0.0.2" \
    -c "exit" -c "commit" >/dev/null 2>&1
FRR_STATIC=$(vtysh -c "show running-config" 2>/dev/null \
             | grep -c "ip route 198.18.0.0/24 10.0.0.2")
THIS_AFTER=$($HX -c "configure" -c "router static" -c "show this" 2>&1)
echo "  -- and after the commit, read back from FRR:"
printf '%s\n' "$THIS_AFTER" | sed 's/^/    /'
# The route's own line, whole and with its indentation, not the blob: the
# header `router static` is in both outputs and matches any substring test for
# "route", which is how an assertion here passes with nothing under the header.
MID_ROUTE=$(printf '%s\n' "$THIS_MID" | grep -x " route 198.18.0.0/24 10.0.0.2")
AFTER_ROUTE=$(printf '%s\n' "$THIS_AFTER" | grep -x " route 198.18.0.0/24 10.0.0.2")
echo "    mid-edit line: '${MID_ROUTE:-<nothing>}'"
echo "    after commit:  '${AFTER_ROUTE:-<nothing>}'"

echo
echo "=========== every configuration change goes over the binary API (#76) ==========="
# The acceptance criterion of #76, made checkable rather than believed: drive a
# commit that changes a representative piece of everything this box holds, and
# assert that nothing MUTATING went down the VPP CLI socket. An account that
# reaches that socket can run `bpa pool <name> del` and disconnect every
# subscriber behind it, so one write left on it is the whole boundary.
#
# What this does NOT assert, and must not be read as: that the control plane
# can no longer hurt the dataplane. #172 is explicit that #76 is an AUTHORITY
# boundary - who may do what - and not an availability one. Every change below
# is still reachable over the binary API, and moving a write there decides who
# gets to make it, not what it costs when it is made.
#
# The rule is written as a list of what a commit is ALLOWED to send, which is
# the form lab/commit.sh settled on for `commit check` and for the same
# reason: an assertion that names the forbidden commands stops testing the
# moment one of them changes shape. That is not hypothetical here. The
# `commit check` assertion existed, passed, and never looked at a real commit
# at all - so `bpa logging tcp` sat on the socket while the criterion read as
# met, and no lab was looking where it would have shown up.
#
# `cgnat logging enable` is in the list with the statement that makes it
# possible beside it. It used to be here and REFUSED, because VPP's UDP
# exporter needs a collector and no statement in this product reached it -
# nothing below arranged one, deliberately, since a lab that configures the
# box under test cannot notice the box failing to configure itself. #209 gave
# the VTY that statement, so what is asserted here is now that both halves go
# over the API and both read back. The refusal itself did not go away and is
# asserted where it belongs, by lab/ipfix.sh, on a box with no exporter.
API_TRACE=$(HEXA_TRACE=1 $HX -c "configure" \
    -c "cgnat pool provider-a" \
    -c "max-sessions 2000" \
    -c "bypass destination 198.51.100.0/24" \
    -c "exit" \
    -c "cgnat pool trace-only" \
    -c "inside 100.65.0.0/22" \
    -c "outside 45.170.17.0/26" \
    -c "exit" \
    -c "cgnat interface host-veth-vpp outside" \
    -c "cgnat timeout icmp 90" \
    -c "cgnat block-holddown 11" \
    -c "cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739" \
    -c "cgnat logging enable" \
    -c "cgnat logging tcp 10.0.0.2 port 4740" \
    -c "interface pg0" \
    -c "mtu 1480" \
    -c "urpf strict" \
    -c "exit" \
    -c "commit" 2>&1 >/tmp/trace-commit.out)
COMMIT_OUT=$(cat /tmp/trace-commit.out)
sed 's/^/  /' /tmp/trace-commit.out
echo "  -- what this commit sent down the CLI socket:"
printf '%s\n' "$API_TRACE" | grep "^\[device\] " | sort -u | sed 's/^/    /'
# Every [device] line that is neither a read nor a check. `bpa pool ... check`
# and `bpa forward ... check` are the commit-check path, which #76 lists as
# still on the socket and #3 records as having no API message of its own; they
# are read-only, and the labs that exist say so.
CLI_WRITES=$(printf '%s\n' "$API_TRACE" \
  | grep "^\[device\] " \
  | grep -vE "^\[device\] show " \
  | grep -vE "^\[device\] bpa (pool|forward) .* check$")
# A commit that changed nothing sends nothing and would satisfy the rule
# above without testing it, so what the commit DID is asserted beside it - by
# name, from the trace, and read back off the box afterwards.
TRACE_WRITES=$(printf '%s\n' "$API_TRACE" | grep -c "^\[api\] bpa_")
TRACE_MSGS=$(printf '%s\n' "$API_TRACE" | sed -n 's/^\[api\] \([a-z0-9_]*\) .*/\1/p' \
             | sort -u | tr '\n' ' ')
echo "  -- and over the binary API: $TRACE_MSGS"
BPA_CONFIG_AFTER=$(vppctl show bpa config | tr -d '\r')

# The other half of the criterion: the passthrough that stays is READ-ONLY.
# `show cgnat subscriber <x>` forwards its argument to the socket untouched -
# it is the ONE place an operator's own text reaches that socket, and the
# read-only role has it - so the argument has to stay an argument and must
# never become a second command. Probed with both separators that would make
# it one, against a pool that is carrying the answer.
NL='
'
$HX -c "show cgnat subscriber 1.2.3.4; vppctl bpa pool trace-only del" \
  >/dev/null 2>&1
$HX -c "show cgnat subscriber 1.2.3.4${NL}bpa pool trace-only del" \
  >/dev/null 2>&1
INJECT_SURVIVED=$(vppctl show bpa pool | grep -c "trace-only")
echo "  -- pool rows after two show arguments that try to be a command: $INJECT_SURVIVED"

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

expect_in "an administrator APPLIES the routing change the operator was refused" \
  "commit complete" "$NETADM_ROUTE"
# The half a "commit check" never proved, and the half that was broken: the
# check logs to stdout and the apply logs to a directory the frr package ships
# read-only to group frr, so this account could validate and not commit (#186).
expect "and FRR holds what that account sent it" \
  test "${NETADM_IN_FRR:-0}" -ge 1
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

# --- issue #188: the commit an operator makes can be undone ---
#
# The modes have ONE owner, appliance/install.sh, and the VTY neither restates
# them nor narrows them. It used to chmod 0700/0600 on every save and every
# commit, so the first one by anybody - root applying the configuration at boot
# - closed the directory the operator's own group had been given.
expect_not_in "a cgnat operator's commit records its rollback revision" \
  "no rollback revision could be recorded" "$CG_OK"
# Read back by the operator, off the box. The revision existing is not the
# assertion: the assertion is that the account which made the commit can see
# the revision it would roll back to.
expect_in "and the operator can see the revision it would roll back to" \
  "current" "$CG_RB"
expect "which that account wrote itself" test "${RB_OWNER:-none}" = cg
# setgid on the directory, or a revision root writes at boot is group root and
# the next operator cannot read it.
expect "and which carries the role group, whoever wrote it" \
  test "${RB_GROUP:-none}" = hexa-cgnat
# The modes, against what install.sh states - 2770 on the directories, 0660 on
# the file - at the two moments that broke them.
expect "root's own commit left the configuration directory as installed" \
  test "${MODE_AFTER_ROOT:-}" = "2770 2770"
# The save and the modes in ONE assertion, because either alone passes for the
# wrong reason: a save that is refused cannot narrow anything, so a mode read
# after a failed save says only that nothing happened.
expect "and a non-root save succeeds and leaves every one of them alone" \
  test "${SAVE_STATE:-}" = "1 2770 2770 660 hexa-cgnat"
# The constraint none of the above may buy: this file holds the password hash
# of every account on the box, so widening it to everybody would answer the
# group problem by handing out an offline cracking target.
expect "and never readable by everybody: it holds every password hash" \
  sh -c "[ \"\$(stat -c %a /etc/hexarouting/hexarouting.conf | tail -c 2)\" = 0 ]"

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
# The hierarchies asked for in #193, offered where an operator looks for them.
expect_in "CGNAT is offered under nat" "NATSUB: cgnat" "$COMP"
expect_in "and static routes under router, beside the protocols" "static" \
  "$(printf '%s\n' "$COMP" | sed -n 's/^ *ROUTERSUB: *//p')"
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

# --- issue #180: show this, for what is being typed right now ---
#
# Each of these reads ONE line of the output, whole and with its leading space,
# rather than looking for a word in the blob: every one of these blocks prints
# its own header, and a header is a substring match for the block's name, for
# "neighbor" in BGP's case and for "route" in the static block's. That is
# exactly how this defect survived - the lab re-entered the block AFTER
# committing, where the lines come back from FRR indented and display fine.
COMP_BGP_THIS=$(printf '%s\n' "$COMP" | sed -n 's/^ *BGP_THIS: *//p')
COMP_AF_THIS=$(printf '%s\n' "$COMP" | sed -n 's/^ *AF_THIS: *//p')
COMP_STATIC_THIS=$(printf '%s\n' "$COMP" | sed -n 's/^ *STATIC_THIS: *//p')
echo "  mid-edit, in router bgp: ${COMP_BGP_THIS:-<nothing>}"
echo "  mid-edit, in router static: ${COMP_STATIC_THIS:-<nothing>}"
# `neighbor 10.0.0.2 remote-as 65001` is committed and would come back from
# FRR indented whatever this code did. The peer group was typed in this
# session and exists nowhere else, so it is the one that carries the defect.
expect_in "a statement typed in this session shows inside its block" \
  "[ neighbor TRANSITO peer-group]" "$COMP_BGP_THIS"
# `bgp router-id` was committed by an earlier $HX run and is typed nowhere in
# this session, so it can only have come back from FRR. The remote-as beside
# it is BOTH committed and retyped here, which makes it no evidence at all.
expect_in "beside what FRR already had, which came back indented" \
  "[ bgp router-id 10.0.0.1]" "$COMP_BGP_THIS"
# The closing bracket is deliberately not required. These two assertions are
# about WHERE a statement shows - its block and its indentation - and
# `maximum-paths` is a statement this VTY has no grammar for, so since #141 it
# shows with `  <- not checked` after it. Demanding the line end there made a
# nesting assertion fail over a marker, which is a check measuring the wrong
# thing.
expect_in "a nested address-family shows what was typed in it" \
  "[ maximum-paths 4" "$COMP_AF_THIS"
# And the marker itself, said once rather than smuggled into the assertions
# above: it is true, it is the point of #141, and a lab that quotes it is
# checked against the product's own string by test_doc_commands.py.
expect_in "and it carries the mark, because the VTY has no grammar for it" \
  "maximum-paths 4  <- not checked" "$COMP_AF_THIS"
# ...and only its own. The router above it has a remote-as for the same
# neighbour, and printing the level above is the other half of #98.
expect_not_in "and not the router's, one level up" \
  "remote-as" "$COMP_AF_THIS"
expect_in "the router above shows the family under it" \
  "[ address-family ipv4 unicast]" "$COMP_BGP_THIS"
expect_in "with its statements one level deeper" \
  "[  maximum-paths 4" "$COMP_BGP_THIS"
expect_in "a static route shows in the block's own words" \
  "[ route 198.18.0.0/24 10.0.0.2]" "$COMP_STATIC_THIS"
expect_not_in "and not in FRR's, which is not the hierarchy being edited" \
  "ip route" "$COMP_STATIC_THIS"

# The same block, the two shapes the candidate arrives in, against a real FRR.
expect "mid-edit, the route is shown before anything is committed" \
  test -n "${MID_ROUTE:-}"
expect "and after the commit, read back from FRR, it is shown the same way" \
  test -n "${AFTER_ROUTE:-}"
# What was shown is what committed. Without this the two above could agree
# with each other about a route that never reached the router.
expect "and FRR holds the route those two lines were describing" \
  test "${FRR_STATIC:-0}" -ge 1


# ---------------------------------------------------------------------------
# show interface terse / description / utilization (#192)
#
# Against a real dataplane, because all three of these READ VPP's own output
# and the layout is the whole difficulty: the counter names have spaces in
# them, an idle interface prints no counter line at all, and the link state
# lives in a different command from the admin state. A unit test proves the
# parser against text this repository wrote; only this proves it against text
# VPP wrote.
echo
echo "=== show interface terse / description / utilization ==="

# A description with a SPACE in it. `vppctl set interface tag` truncates at
# the first space; the API does not - so a box that had taken the easy route
# would store "TRANSITO" and this would fail.
$HX -c "configure" -c "interface pg0" \
    -c "description TRANSITO Vivo AS26599" -c "exit" -c "commit" >/dev/null 2>&1
DESCR=$($HX -c "show interface description" 2>&1)
echo "$DESCR" | sed 's/^/  /'
expect_in "a description survives whole, spaces and all" \
  "TRANSITO Vivo AS26599" "$DESCR"
expect_in "and the view has the column it used to deny having" \
  "Description" "$DESCR"
expect_not_in "the box no longer says it cannot hold one" \
  "carries no per-interface description" "$DESCR"

TERSE=$($HX -c "show interface terse" 2>&1)
echo "$TERSE" | sed 's/^/  /'
expect_in "terse has an admin column" "Admin" "$TERSE"
expect_in "and a link column, which is a different fact" "Link" "$TERSE"
# The link column is checked against the dataplane's own answer rather than
# against "up": a reader that returned a constant, or that read the admin
# state twice, passes any check written against a literal.
HW_LINK=$(vppctl show hardware-interfaces brief | awk '$1=="pg0"{print $3}')
VTY_LINK=$(printf '%s\n' "$TERSE" | awk '$1=="pg0"{print $3}')
echo "  dataplane says pg0 link '$HW_LINK'; the VTY says '$VTY_LINK'"
expect "the link column is the dataplane's link state, not a constant" \
  sh -c "[ -n \"$HW_LINK\" ] && [ \"$HW_LINK\" = \"$VTY_LINK\" ]"
# And it has to MOVE. A column read off the admin state would follow this too,
# so the admin state is held still while the link goes down: host-veth-vpp's
# link follows its peer, and the peer is in another namespace.
vppctl set interface state pg0 down
TERSE_DOWN=$($HX -c "show interface terse" 2>&1)
DOWN_LINK=$(printf '%s\n' "$TERSE_DOWN" | awk '$1=="pg0"{print $3}')
DOWN_ADMIN=$(printf '%s\n' "$TERSE_DOWN" | awk '$1=="pg0"{print $2}')
echo "  with pg0 shut: admin '$DOWN_ADMIN' link '$DOWN_LINK'"
expect_in "shutting an interface shows in the admin column" "down" "$DOWN_ADMIN"
vppctl set interface state pg0 up

# A sub-interface is not hardware and VPP reports no link for it. Its link is
# its parent's, and an ISP's subscriber ports are all sub-interfaces - so a
# blank there would empty the column exactly where it is needed.
vppctl create sub-interfaces pg0 100 >/dev/null 2>&1
vppctl set interface state pg0.100 up
SUBTERSE=$($HX -c "show interface terse" 2>&1 | awk '$1=="pg0.100"{print $3}')
echo "  pg0.100 link column: '${SUBTERSE:-<none>}'"
expect "a sub-interface takes its parent's link state" \
  sh -c "[ \"$SUBTERSE\" = \"$HW_LINK\" ]"

# Utilisation. A counter is a total since boot; a rate is a difference. The
# traffic runs for the whole sample at a rate the generator was TOLD, so the
# figure the VTY prints can be checked against a number this lab knows rather
# than against "not zero" - which a bytes-for-bits mistake would also pass.
#
# 1000 packets per second of 100 bytes is 800 kbit/s. The window is wide
# (300-2000) because a software generator is not a calibration source; it is
# still narrow enough that reporting bytes (100 kbit/s) or per-sample totals
# (1.6 Mbit/s at this interval) falls outside it.
# No `limit`, so the stream runs for the whole window rather than emptying
# into the first sample; `incrementing 8` because a stream whose data does not
# fill `size` is refused with "failed to parse packet data", which reads like
# a syntax error and is not one.
vppctl packet-generator new \
  "name util rate 1000 size 100-100 interface pg0 node ethernet-input \
   data { IP4: 1.2.3 -> 4.5.6 UDP: 100.64.0.9 -> 8.8.8.8 UDP: 1234 -> 53 \
   incrementing 8 }"
vppctl packet-generator enable-stream util
UTIL=$($HX -c "show interface utilization interval 2 count 2 pg0" 2>&1)
vppctl packet-generator disable-stream util
echo "$UTIL" | sed 's/^/  /'
expect_in "the interval is stated before the command makes anybody wait" \
  "sampling every 2s" "$UTIL"
KBIT=$(printf '%s\n' "$UTIL" | awk '$1=="pg0"{
          if ($3 == "Mbit/s") printf "%d", $2 * 1000;
          else if ($3 == "kbit/s") printf "%d", $2;
          else if ($3 == "Gbit/s") printf "%d", $2 * 1000000;
          else printf "%d", $2 / 1000 }')
echo "  the VTY read ${KBIT:-<none>} kbit/s off a generator set to 800"
expect "a 800 kbit/s stream reads as bit/s, not bytes and not a total" \
  sh -c "[ -n \"$KBIT\" ] && [ \"$KBIT\" -ge 300 ] && [ \"$KBIT\" -le 2000 ]"
# The graph. A run with traffic must draw something above the baseline; the
# same command with the generator stopped must not - which is what makes the
# first assertion mean anything.
IDLE=$($HX -c "show interface utilization interval 1 count 2 pg0" 2>&1)
IDLE_RATE=$(printf '%s\n' "$IDLE" | awk '$1=="pg0"{print $2, $3}')
echo "  with the generator stopped: '$IDLE_RATE'"
expect_in "with nothing passing, the rate is zero" "0 bit/s" "$IDLE_RATE"

# The graph. Checked by COMPARING the two runs rather than by looking for a
# particular character: which characters are drawn depends on whether the
# console is answering in UTF-8, and an assertion on one of them would be an
# assertion about the terminal. What has to be true is that a link carrying
# traffic and the same link carrying none do not draw the same line.
HIST=$(printf '%s\n' "$UTIL" | awk '$1=="pg0"{print $6}')
IDLE_HIST=$(printf '%s\n' "$IDLE" | awk '$1=="pg0"{print $6}')
echo "  history with traffic '$HIST', idle '$IDLE_HIST'"
expect "the history is drawn, not left blank" sh -c "[ -n \"$HIST\" ]"
expect "and traffic does not draw the same line as silence" \
  sh -c "[ \"$HIST\" != \"$IDLE_HIST\" ]"

echo
# LAST IN THE FILE, and it has to be. `--load` applies a file as the WHOLE
# configuration, so the deliberately small one below REMOVES everything it does
# not mention - including this lab's BGP session. Run anywhere but here it takes
# the rest of the lab's setup away, and five assertions fail for a reason that
# has nothing to do with what they test. That was measured, not foreseen.
echo "=========== a file written before the hierarchies moved (#193) ==========="
# The migration property, on a real box rather than on a parser. CGNAT is under
# `nat` now and static routes under `router`; every box already in service has
# a saved configuration and up to twenty rollback revisions written the OLD
# way, and hexarouting.service replays that file at every boot on a machine
# nobody is watching. A grammar that cannot read the box's own file leaves the
# box unconfigured, and nothing would say so until somebody noticed the traffic.
vppctl bpa pool provider-a del 2>/dev/null
cat > /tmp/old-format.conf <<'OLDEOF'
! cgnat
cgnat pool provider-a
 inside 100.64.0.0/22
 outside 200.150.0.16/30
 block-size 512
 policy interleaved
 mode deterministic
 exit
! routing
ip route 198.51.100.0/24 blackhole
OLDEOF
$HX --load /tmp/old-format.conf 2>&1 | sed 's/^/  /'
OLD_POOL=$(vppctl show bpa pool | grep -c provider-a)
OLD_ROUTE=$(vtysh -c "show running-config" 2>/dev/null | grep -c "ip route 198.51.100.0/24")
echo "  after loading the old format: pool rows $OLD_POOL, route lines $OLD_ROUTE"
# And what the box writes back is the NEW hierarchy, which must itself load.
$HX -c "save" >/dev/null 2>&1
NEW_TEXT=$(cat "$HEXA_CONFIG")
printf '%s\n' "$NEW_TEXT" | grep -E "^nat |^router static|^ route " | sed 's/^/    /'
vppctl bpa pool provider-a del 2>/dev/null
$HX --load "$HEXA_CONFIG" 2>&1 | sed 's/^/  /'
ROUND=$(vppctl show bpa pool | grep -c provider-a)
echo "  after loading what the box itself wrote: pool rows $ROUND"

echo

# The migration property, measured earlier on a real box: a configuration
# written before the hierarchies moved still reaches the dataplane and FRR,
# what the box writes back is the new hierarchy, and the box can read that.
expect "a configuration in the old hierarchy still reaches the dataplane" \
  sh -c "[ \"$OLD_POOL\" -ge 1 ]"
expect "and its static route still reaches FRR" \
  sh -c "[ \"$OLD_ROUTE\" -ge 1 ]"
expect_in "the box writes CGNAT under nat" "nat cgnat pool provider-a" "$NEW_TEXT"
expect_in "and static routes as a block under router" "router static" "$NEW_TEXT"
expect "a box can read back the file it just wrote" sh -c "[ \"$ROUND\" -ge 1 ]"

# --- issue #76: the CLI socket carries reads, and nothing else ---
#
# Stated as "what came back is empty", with the offending lines printed: a
# count would say how many and not which, and the which is the whole
# diagnosis. The commit that produced this trace is above; it changes a pool,
# a pool setting, a bypass list, an interface attachment, two global
# settings, the IPFIX collector, an MTU and a uRPF mode, and asks for an
# export state the box refuses.
echo "  CLI writes during that commit: ${CLI_WRITES:-<none>}"
expect "a commit sends no mutating command down the VPP CLI socket" \
  test -z "${CLI_WRITES:-}"
expect "which is only worth asserting if the commit changed something" \
  sh -c "[ \"${TRACE_WRITES:-0}\" -ge 5 ]"
# By name, because "five bpa_ calls happened" is satisfied by five of the same
# one. These three are the settings that used to be unreachable without the
# socket, and the last is the one #172 found still on it.
expect_in "the pool went over the API" "bpa_pool_add_del" "$TRACE_MSGS"
expect_in "the global settings did" "bpa_global_set" "$TRACE_MSGS"
expect_in "the interface attachment did" "bpa_interface_add_del_feature" \
  "$TRACE_MSGS"
expect_in "and so did the IPFIX export, which did not before (#172)" \
  "bpa_logging_set" "$TRACE_MSGS"
# Read back off the dataplane, not off the trace: a message that was sent and
# ignored leaves the same trace line as one that was applied, and the old CLI
# path threw the answer away for exactly that reason.
# The export state the box used to refuse, now in force - and read off the
# dataplane, not off the trace. Until #209 this line could not be committed at
# all on a box the product had configured, and before #76 the VTY discarded
# the refusal and printed "commit complete" over it.
expect_in "the export state the box used to refuse is now in force" \
  "cgnat logging enable" "$BPA_CONFIG_AFTER"
expect_in "with the exporter that makes it possible (#209)" \
  "cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739" "$BPA_CONFIG_AFTER"
# VPP's own message and not the plugin's: the exporter is core state and
# libvnet exports no setter a plugin could call. Still the binary API, which
# is what this section is about.
expect_in "and it went over VPP's own API message, not the CLI socket" \
  "set_ipfix_exporter" "$TRACE_MSGS"
expect_in "and its TCP collector with it" \
  "cgnat logging tcp 10.0.0.2 port 4740" "$BPA_CONFIG_AFTER"
expect_in "and the global setting that travelled beside them" \
  "cgnat block-holddown 11" "$BPA_CONFIG_AFTER"
# The pool the two probes above asked the box to delete. Asserted on the POOL
# and not on what the VTY printed: a refusal message is this VTY's own
# sentence, and the question is what the dataplane did.
expect "a show argument cannot become a second command" \
  sh -c "[ \"${INJECT_SURVIVED:-0}\" -ge 1 ]"

verdict
