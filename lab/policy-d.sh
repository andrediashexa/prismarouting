#!/bin/sh
# Routing policy, phase D (#538): per-term hits, test policy, which decided
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# OFF-RED-AT-LEAST: 44
# Routing policy, phase D (#538): what the policies did and would do -
# asserted against the daemons, which are the oracle: the counters `show
# policy <name>` reads must move with the routes at every attach point
# used here, `test policy` must decide each route the way bgpd, zebra and
# ospfd did, and `show policy route` must name the term that decided a real
# route beside FRR's own outcome, with no disagreement.
#
# The box (AS 65000) between an upstream (AS 65001, 10.0.0.2) and a
# downstream (AS 65002, 10.2.0.2). The upstream sends:
#
#   203.0.113.0/24   community 65001:10: UP's term cust takes it, local
#                    preference 200 and 65000:538 added
#   198.51.100.0/24  MED 33: no term of UP, TAIL's term t - local preference
#                    150 - and the table policy's metric 9
#   192.0.2.0/24     community 65001:666: UP's term black rejects it
#   10.99.0.0/24     UP's term bogon rejects it
#   100.64.10.0/24   no term of either: the chain's default, accepted
#
#   100.64.20.0/24   GATE accepts it, so UP's term gate - then reject,
#                    from policy GATE - rejects it (#542)
#   100.64.30.0/24   GATE sets MED 31 and rejects it, so term gate does not
#                    match: on to TAIL, and the chain's default accepts it,
#                    MED 31 on it
#
# and later 10.98.0.0/24, which bogon rejects too: the counter moves by it.
# 0.148.9 to 0.148.12 compiled term gate to a deny clause that never asked
# GATE: it rejected 198.51.100.0/24, 100.64.10.0/24 and 100.64.30.0/24 too.
# The box imports through the chain [ UP TAIL ], exports to the downstream
# through DN, sends BGP's best routes to zebra through TBL, installs BGP's
# routes through FIB, exports into OSPF through OEXP, and originates
# 10.55.0.0/24 through NET.
#
# MODE=off commits the same box and the same policies without attaching
# any of them, and every check that a policy matched or decided something
# must go red.
MODE=${1:-on}
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src

for n in up dn; do
  ip link add veth-$n-o type veth peer name veth-$n 2>/dev/null
  ip link set veth-$n up
  ip netns add $n 2>/dev/null
  ip link set veth-$n-o netns $n
  ip netns exec $n ip link set lo up
  ip netns exec $n ip link set veth-$n-o up
done
ip netns exec up ip addr add 10.0.0.2/24 dev veth-up-o
ip netns exec dn ip addr add 10.2.0.2/24 dev veth-dn-o

sysctl -p /src/appliance/etc/sysctl.conf >/dev/null 2>&1 || true
vpp -c "$(startup_conf lcp)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -25 /tmp/vpp.log; exit 1; }
use_binary_api
HX="python3 /src/src/prismash/prismash.py"

for n in up dn; do
  vppctl create host-interface name veth-$n >/dev/null
  vppctl set interface state host-veth-$n up
done
$HX -c "configure" -c "interface host-veth-up" -c "linux-name vppup0" \
    -c "exit" -c "interface host-veth-dn" -c "linux-name vppdn0" \
    -c "exit" -c "commit" 2>&1 | tail -1 | sed 's/^/  pairs: /'
vppctl set interface ip address host-veth-up 10.0.0.1/24
vppctl set interface ip address host-veth-dn 10.2.0.1/24

# ---------------- Prisma Routing ----------------
mkdir -p /var/run/frr /etc/frr
printf 'hostname prisma-routing\n' > /etc/frr/zebra.conf
printf 'hostname prisma-routing\n' > /etc/frr/bgpd.conf
cat > /etc/frr/ospfd.conf <<'EOF'
hostname prisma-routing
router ospf
 ospf router-id 10.0.0.1
EOF
chown -R frr:frr /var/run/frr /etc/frr
start_frr ospfd bgpd

# ---------------- the upstream and the downstream ----------------
peer_frr() {   # <ns> <bgpd.conf>
  mkdir -p /var/run/frr/$1 /etc/frr/$1
  printf 'hostname %s\n' "$1" > /etc/frr/$1/zebra.conf
  printf '%s\n' "$2" > /etc/frr/$1/bgpd.conf
  touch /etc/frr/$1/vtysh.conf
  chown -R frr:frr /var/run/frr/$1 /etc/frr/$1
  for d in zebra bgpd; do
    ip netns exec $1 /usr/lib/frr/$d -d -N $1 -A 127.0.0.1 -f /etc/frr/$1/$d.conf
  done
}
peer_frr up "hostname up
route-map CUST permit 10
 set community 65001:10
exit
route-map MED permit 10
 set metric 33
exit
route-map BLACK permit 10
 set community 65001:666
exit
router bgp 65001
 bgp router-id 10.0.0.2
 no bgp ebgp-requires-policy
 no bgp network import-check
 neighbor 10.0.0.1 remote-as 65000
 address-family ipv4 unicast
  network 203.0.113.0/24 route-map CUST
  network 198.51.100.0/24 route-map MED
  network 192.0.2.0/24 route-map BLACK
  network 10.99.0.0/24
  network 100.64.10.0/24
  network 100.64.20.0/24
  network 100.64.30.0/24
  neighbor 10.0.0.1 activate
 exit-address-family"
peer_frr dn "hostname dn
router bgp 65002
 bgp router-id 10.2.0.2
 no bgp ebgp-requires-policy
 neighbor 10.2.0.1 remote-as 65000
 address-family ipv4 unicast
  neighbor 10.2.0.1 activate
 exit-address-family"
up() { ip netns exec up vtysh -N up -c "$1" 2>&1; }
dn() { ip netns exec dn vtysh -N dn -c "$1" 2>&1; }

. /src/lab/verdict.sh

echo "=========== the sessions and a static route, through the VTY ==========="
SETUP=$($HX -c "configure" \
        -c "router static" -c "10.55.0.0/24 next-hop blackhole" -c "exit" \
        -c "router bgp 65000" -c "bgp router-id 10.0.0.1" \
        -c "no bgp ebgp-requires-policy" \
        -c "neighbor 10.0.0.2 remote-as 65001" \
        -c "neighbor 10.2.0.2 remote-as 65002" \
        -c "address-family ipv4 unicast" \
        -c "neighbor 10.0.0.2 activate" \
        -c "neighbor 10.0.0.2 soft-reconfiguration inbound" \
        -c "neighbor 10.2.0.2 activate" \
        -c "exit-address-family" -c "exit" -c "commit" 2>&1)
echo "$SETUP" | grep -vE "^(note|warning):" | tail -2 | sed 's/^/  /'
printf '%s' "$SETUP" | grep -q "commit complete" || {
  echo "the setup did not commit - nothing below would mean anything"
  printf '%s\n' "$SETUP" | tail -20 | sed 's/^/  /'
  exit 1
}

echo; echo "=========== the policies, written under policy-options ==========="
POL=$($HX -c "configure" -c "policy-options" \
  -c "prefix-list CUST" -c "203.0.113.0/24" -c "exit" \
  -c "prefix-list MEDP" -c "198.51.100.0/24" -c "exit" \
  -c "prefix-list LOCAL" -c "10.55.0.0/24" -c "exit" \
  -c "community CUSTC members 65001:10" \
  -c "community BLACK members 65001:666" \
  -c "community MINE members 65000:538" \
  -c "policy-statement UP" \
  -c "term cust" -c "from prefix-list CUST" -c "from community CUSTC" \
  -c "set local-preference 200" -c "set community add MINE" \
  -c "then accept" -c "exit" \
  -c "term black" -c "from community BLACK" -c "then reject" -c "exit" \
  -c "term bogon" -c "from route-filter 10.0.0.0/8 orlonger" \
  -c "then reject" -c "exit" \
  -c "term gate" -c "from policy GATE" -c "then reject" -c "exit" -c "exit" \
  -c "policy-statement GATE" \
  -c "term yes" -c "from route-filter 100.64.20.0/24 exact" \
  -c "then accept" -c "exit" \
  -c "term med" -c "from route-filter 100.64.30.0/24 exact" \
  -c "set med 31" -c "exit" -c "default-action reject" -c "exit" \
  -c "policy-statement TAIL" \
  -c "term t" -c "from prefix-list MEDP" -c "set local-preference 150" \
  -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement DN" \
  -c "term mine" -c "from community MINE" -c "set as-path-prepend 65000" \
  -c "then accept" -c "exit" \
  -c "term med" -c "from prefix-list MEDP" -c "set med 77" \
  -c "then accept" -c "exit" \
  -c "term local" -c "from prefix-list LOCAL" -c "then accept" -c "exit" \
  -c "term rest" -c "then reject" -c "exit" -c "exit" \
  -c "policy-statement TBL" \
  -c "term m" -c "from prefix-list MEDP" -c "set metric 9" \
  -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement FIB" \
  -c "term c" -c "from prefix-list CUST" -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement OEXP" \
  -c "term c" -c "from protocol bgp" -c "from prefix-list CUST" \
  -c "set metric 120" -c "then accept" -c "exit" \
  -c "term l" -c "from protocol static" -c "from prefix-list LOCAL" \
  -c "then accept" -c "exit" -c "exit" \
  -c "policy-statement NET" \
  -c "term 1" -c "set med 44" -c "then accept" -c "exit" \
  -c "exit" -c "exit" -c "commit" 2>&1)
printf '%s\n' "$POL" | grep -vE "^(note|warning):" | tail -2 | sed 's/^/  /'

echo; echo "=========== applied, MODE=$MODE ==========="
if [ "$MODE" = off ]; then
  APPLY="(nothing applied: MODE=off)"
else
  APPLY=$($HX -c "configure" -c "router bgp 65000" \
    -c "address-family ipv4 unicast" \
    -c "neighbor 10.0.0.2 import [ UP TAIL ]" \
    -c "neighbor 10.2.0.2 export DN" \
    -c "table-policy TBL" \
    -c "network 10.55.0.0/24 policy NET" \
    -c "exit-address-family" -c "exit" \
    -c "fib-policy bgp FIB" \
    -c "router ospf" -c "export OEXP" -c "exit" -c "commit" 2>&1)
fi
printf "%s\n" "$APPLY" | grep -vE "^(note|warning):" | tail -2 | sed "s/^/  /"
[ "$MODE" = off ] && $HX -c "configure" -c "router bgp 65000" \
  -c "address-family ipv4 unicast" -c "network 10.55.0.0/24" \
  -c "exit-address-family" -c "exit" -c "commit" >/dev/null 2>&1

n=0
while [ "$n" -lt 90 ]; do
  dn "show bgp ipv4 unicast 203.0.113.0/24" | grep -q "^BGP routing table entry" \
    && dn "show bgp ipv4 unicast 10.55.0.0/24" | grep -q "^BGP routing table entry" \
    && vtysh -c "show bgp ipv4 unicast 100.64.10.0/24" 2>/dev/null \
      | grep -q "^BGP routing table entry" \
    && vtysh -c "show bgp ipv4 unicast neighbors 10.0.0.2 received-routes" \
      2>/dev/null | grep -q "100.64.30.0/24" && break
  sleep 1; n=$((n + 1))
done
echo "  the box has the upstream's routes, the downstream the box's (${n}s)"
sleep 3

# hits <show policy output> <label>: the count on the row that ends with it.
hits() {
  printf '%s\n' "$1" | awk -v l="$2" '
    { sub(/[ \t]+$/, "") }
    $0 ~ ("^ +[0-9-]+  " l "$") { print $1; exit }'
}

echo; echo "=========== show policy <name>: the hits ==========="
for p in UP TAIL DN TBL FIB OEXP NET GATE; do
  eval "S_$p=\$(\$HX -c 'show policy $p' 2>&1)"
done
printf '%s\n' "$S_UP" | sed -n '/hits/,$p' | sed 's/^/  /'
printf '%s\n' "$S_DN" | sed -n '/hits/,$p' | sed 's/^/  /'
printf '%s\n' "$S_GATE" | sed -n '/hits/,$p' | sed 's/^/  /'
printf '%s\n' "$S_OEXP" | sed -n '/hits/,$p' | sed 's/^/  /'
for p in TBL FIB NET; do
  eval "printf '%s\n' \"\$S_$p\"" | sed -n '/hits/,$p' | head -6 | sed 's/^/  /'
done
H_CUST=$(hits "$S_UP" "UP term cust")
H_BLACK=$(hits "$S_UP" "UP term black")
H_BOGON=$(hits "$S_UP" "UP term bogon")
H_TAIL=$(hits "$S_UP" "TAIL term t")
H_GATE=$(hits "$S_UP" "UP term gate")
H_GYES=$(hits "$S_GATE" "term yes")
H_GMED=$(hits "$S_GATE" "term med")
H_UPDEF=$(hits "$S_UP" "no term decided: the use's default, accept")
H_MINE=$(hits "$S_DN" "term mine")
H_MED=$(hits "$S_DN" "term med")
H_LOCAL=$(hits "$S_DN" "term local")
H_REST=$(hits "$S_DN" "term rest")
H_TBL=$(hits "$S_TBL" "term m")
H_FIB=$(hits "$S_FIB" "term c")
H_NET=$(hits "$S_NET" "term 1")
# section <show policy output> <heading>: the rows under one use.
section() {
  printf '%s\n' "$1" | awk -v h="    $2" '
    $0 == h { on = 1; next }
    on && /^    [^ ]/ { on = 0 }
    on'
}
OEXP_BGP=$(section "$S_OEXP" "ospf export, from bgp")
OEXP_ST=$(section "$S_OEXP" "ospf export, from static")
H_OBGP=$(hits "$OEXP_BGP" "term c")
H_OST=$(hits "$OEXP_ST" "term l")
echo "  hits: cust=$H_CUST black=$H_BLACK bogon=$H_BOGON t=$H_TAIL" \
     "gate=$H_GATE gate-yes=$H_GYES gate-med=$H_GMED" \
     "default=$H_UPDEF mine=$H_MINE med=$H_MED local=$H_LOCAL rest=$H_REST" \
     "tbl=$H_TBL fib=$H_FIB ospf-bgp=$H_OBGP ospf-static=$H_OST net=$H_NET"

echo; echo "=========== the counter moves with a route ==========="
ip netns exec up vtysh -N up -c "configure terminal" -c "router bgp 65001" \
  -c "address-family ipv4 unicast" -c "network 10.98.0.0/24" >/dev/null 2>&1
n=0
while [ "$n" -lt 30 ]; do
  vtysh -c "show bgp ipv4 unicast neighbors 10.0.0.2 received-routes" 2>/dev/null \
    | grep -q "10.98.0.0/24" && break
  sleep 1; n=$((n + 1))
done
sleep 2
S_UP2=$($HX -c "show policy UP" 2>&1)
H_BOGON2=$(hits "$S_UP2" "UP term bogon")
echo "  10.98.0.0/24 sent (${n}s): bogon $H_BOGON -> $H_BOGON2"

echo; echo "=========== test policy, against what the daemons did ==========="
# The routes as the upstream sends them, described.
T_CUST=$($HX -c "test policy UP+TAIL route 203.0.113.0/24 neighbor 10.0.0.2 community 65001:10 as-path 65001" 2>&1)
T_BLACK=$($HX -c "test policy UP+TAIL route 192.0.2.0/24 neighbor 10.0.0.2 community 65001:666 as-path 65001" 2>&1)
T_MED=$($HX -c "test policy UP+TAIL route 198.51.100.0/24 neighbor 10.0.0.2 med 33 as-path 65001" 2>&1)
T_GYES=$($HX -c "test policy UP+TAIL route 100.64.20.0/24 neighbor 10.0.0.2 med 0 as-path 65001" 2>&1)
T_GMED=$($HX -c "test policy UP+TAIL route 100.64.30.0/24 neighbor 10.0.0.2 med 0 as-path 65001" 2>&1)
printf '%s\n' "$T_GYES" | grep -E "^result|gate" | sed 's/^/  100.64.20.0\/24: /'
printf '%s\n' "$T_GMED" | grep -E "^result|gate|metric|med" | sed 's/^/  100.64.30.0\/24: /'
printf '%s\n' "$T_CUST" | sed 's/^/  /'
printf '%s\n' "$T_BLACK" | grep -E "^result" | sed 's/^/  192.0.2.0\/24: /'
printf '%s\n' "$T_MED" | grep -E "^result|local-pref" | sed 's/^/  198.51.100.0\/24: /'
# The routes as the box holds them.
R_CUST=$($HX -c "test policy UP+TAIL rib 203.0.113.0/24 neighbor 10.0.0.2" 2>&1)
R_BOGON=$($HX -c "test policy UP+TAIL rib 10.99.0.0/24 neighbor 10.0.0.2" 2>&1)
R_DN=$($HX -c "test policy DN rib 203.0.113.0/24 neighbor 10.2.0.2 at export" 2>&1)
R_DEF=$($HX -c "test policy DN rib 100.64.10.0/24 neighbor 10.2.0.2 at export" 2>&1)
R_TBL=$($HX -c "test policy TBL rib 198.51.100.0/24 at table" 2>&1)
R_OSPF=$($HX -c "test policy OEXP rib 203.0.113.0/24 at ospf bgp" 2>&1)
printf '%s\n' "$R_CUST" | sed 's/^/  /'
printf '%s\n' "$R_BOGON" | grep -E "^result|the route:" | sed 's/^/  10.99.0.0\/24: /'
printf '%s\n' "$R_DN" | grep -E "^result|as-path" | sed 's/^/  export: /'
printf '%s\n' "$R_DEF" | grep -E "^result" | sed 's/^/  export 100.64.10.0\/24: /'
printf '%s\n' "$R_TBL" | grep -E "^result|metric" | sed 's/^/  table: /'
printf '%s\n' "$R_OSPF" | grep -E "^result|metric" | sed 's/^/  ospf: /'
# What the daemons did: the oracle.
B_CUST=$(vtysh -c "show bgp ipv4 unicast 203.0.113.0/24 json" 2>&1)
B_MED=$(vtysh -c "show bgp ipv4 unicast 198.51.100.0/24 json" 2>&1)
B_BLACK=$(vtysh -c "show bgp ipv4 unicast 192.0.2.0/24" 2>&1)
B_BOGON=$(vtysh -c "show bgp ipv4 unicast 10.99.0.0/24" 2>&1)
B_GYES=$(vtysh -c "show bgp ipv4 unicast 100.64.20.0/24" 2>&1)
B_GMED=$(vtysh -c "show bgp ipv4 unicast 100.64.30.0/24 json" 2>&1)
B_DEF=$(vtysh -c "show bgp ipv4 unicast 100.64.10.0/24" 2>&1)
MED_GMED=$(printf '%s' "$B_GMED" | python3 -c "import json,sys; print(json.load(sys.stdin)['paths'][0].get('metric'))" 2>/dev/null)
D_CUST=$(dn "show bgp ipv4 unicast 203.0.113.0/24 json")
D_DEF=$(dn "show bgp ipv4 unicast 100.64.10.0/24")
Z_MED=$(vtysh -c "show ip route 198.51.100.0/24 json" 2>&1)
O_CUST=$(vtysh -c "show ip ospf database external 203.0.113.0 self-originate json" 2>&1)
lp() { printf '%s' "$1" | python3 -c "import json,sys; print(json.load(sys.stdin)['paths'][0].get('locPrf'))" 2>/dev/null; }
LP_CUST=$(lp "$B_CUST"); LP_MED=$(lp "$B_MED")
PATH_DN=$(printf '%s' "$D_CUST" | python3 -c "import json,sys; print(json.load(sys.stdin)['paths'][0]['aspath']['string'])" 2>/dev/null)
ZM_MED=$(printf '%s' "$Z_MED" | python3 -c "import json,sys; print([e['metric'] for e in json.load(sys.stdin)['198.51.100.0/24'] if e['protocol']=='bgp'][0])" 2>/dev/null)
OM_CUST=$(printf '%s' "$O_CUST" | python3 -c "import json,sys; print([l['metric'] for l in json.load(sys.stdin)['asExternalLinkStates'] if l['networkMask']==24][0])" 2>/dev/null)
echo "  bgpd: 203.0.113.0/24 localpref $LP_CUST, 198.51.100.0/24 localpref $LP_MED;" \
     "192.0.2.0/24 $(printf '%s\n' "$B_BLACK" | head -1);" \
     "10.99.0.0/24 $(printf '%s\n' "$B_BOGON" | head -1)"
echo "  the downstream: 203.0.113.0/24 path '$PATH_DN';" \
     "100.64.10.0/24 $(printf '%s\n' "$D_DEF" | head -1)"
echo "  bgpd: 100.64.20.0/24 $(printf '%s\n' "$B_GYES" | head -1);" \
     "100.64.30.0/24 MED $MED_GMED; 100.64.10.0/24 $(printf '%s\n' "$B_DEF" | head -1)"
echo "  zebra: 198.51.100.0/24 metric $ZM_MED; ospfd: 203.0.113.0/24 metric $OM_CUST"
# The simulated route after the sets, beside it.
SIM_PATH=$(printf '%s\n' "$R_DN" | sed -n '/after the sets/,$p' | awk '$1 == "as-path" { $1 = ""; sub(/^ /, ""); print; exit }')

echo; echo "=========== show policy route: which decided ==========="
W_CUST=$($HX -c "show policy route 203.0.113.0/24" 2>&1)
W_BOGON=$($HX -c "show policy route 10.99.0.0/24" 2>&1)
W_NET=$($HX -c "show policy route 10.55.0.0/24" 2>&1)
W_GYES=$($HX -c "show policy route 100.64.20.0/24" 2>&1)
W_GMED=$($HX -c "show policy route 100.64.30.0/24" 2>&1)
printf '%s\n' "$W_GYES" | sed 's/^/  /'
printf '%s\n' "$W_GMED" | sed 's/^/  /'
printf '%s\n' "$W_CUST" | sed 's/^/  /'
printf '%s\n' "$W_BOGON" | sed 's/^/  /'
printf '%s\n' "$W_NET" | sed 's/^/  /'
W_ALL=$(printf '%s\n%s\n%s\n%s\n%s\n' "$W_CUST" "$W_BOGON" "$W_NET" \
        "$W_GYES" "$W_GMED" | tr -s ' \n' '  ')
W_GYES1=$(printf '%s' "$W_GYES" | tr -s ' \n' '  ')
W_GMED1=$(printf '%s' "$W_GMED" | tr -s ' \n' '  ')
W_CUST1=$(printf '%s' "$W_CUST" | tr -s ' \n' '  ')
W_BOGON1=$(printf '%s' "$W_BOGON" | tr -s ' \n' '  ')
W_NET1=$(printf '%s' "$W_NET" | tr -s ' \n' '  ')

echo; echo "=========== VERDICT ==========="
expect_in "the policies commit" "commit complete" "$POL"
pos() { [ -n "$1" ] && [ "$1" != "-" ] && [ "$1" -ge 1 ]; }
expect "import, UP term cust counts the customer's route ($H_CUST)" pos "$H_CUST"
expect "  term black, the blackholed one ($H_BLACK)" pos "$H_BLACK"
expect "  term bogon, 10.99.0.0/24 ($H_BOGON)" pos "$H_BOGON"
expect "  the chain's TAIL term t, 198.51.100.0/24 ($H_TAIL)" pos "$H_TAIL"
expect "  and the chain's default, 100.64.10.0/24 ($H_UPDEF)" pos "$H_UPDEF"
expect "export, DN term mine ($H_MINE)" pos "$H_MINE"
expect "  term med ($H_MED)" pos "$H_MED"
expect "  term local, the network ($H_LOCAL)" pos "$H_LOCAL"
expect "  term rest ($H_REST)" pos "$H_REST"
expect "table-policy, TBL term m ($H_TBL)" pos "$H_TBL"
expect "fib-policy bgp, FIB term c, counted by zebra ($H_FIB)" pos "$H_FIB"
expect "ospf export from bgp, OEXP term c, counted by ospfd ($H_OBGP)" pos "$H_OBGP"
expect "ospf export from static, OEXP term l ($H_OST)" pos "$H_OST"
expect "network, NET term 1 ($H_NET)" pos "$H_NET"
expect_in "show policy says which daemon runs each" "zebra, route-map PO-FIB-FIB-BGP" "$S_FIB"
expect "a route sent moves its term's counter ($H_BOGON -> $H_BOGON2)" \
  test "${H_BOGON2:-0}" -gt "${H_BOGON:-0}" 2>/dev/null
expect_in "test policy route: the customer's route, by UP term cust" \
  "result: accept, by UP term cust" "$T_CUST"
expect "  with bgpd's local preference ($LP_CUST)" test "$LP_CUST" = 200
expect_in "  the simulation says the same" "local-preference 200" "$T_CUST"
expect_in "the blackholed route: rejected by UP term black" \
  "result: reject, by UP term black" "$T_BLACK"
expect_in "  and bgpd holds no path to it" "not in table" "$B_BLACK"
expect_in "198.51.100.0/24: TAIL term t, local preference 150" \
  "result: accept, by TAIL term t" "$T_MED"
expect "  as bgpd has it ($LP_MED)" test "$LP_MED" = 150
expect_in "test policy rib: the path before its import, community less what UP adds" \
  "less what it adds" "$(printf '%s' "$R_CUST" | tr -s ' \n' '  ')"
expect_in "  decided by UP term cust, as bgpd did" "result: accept, by UP term cust" "$R_CUST"
expect_in "test policy rib: a rejected route, from soft-reconfiguration's copy" \
  "result: reject, by UP term bogon" "$R_BOGON"
expect_in "  and bgpd holds no path to it" "not in table" "$B_BOGON"
expect_in "test policy rib at export: DN term mine" "result: accept, by term mine" "$R_DN"
expect "  the downstream's path is the simulated one behind the box's AS ($PATH_DN / $SIM_PATH)" \
  test "$PATH_DN" = "65000 $SIM_PATH"
expect_in "  the route no term takes is rejected by DN term rest" \
  "result: reject, by term rest" "$R_DEF"
expect_in "  and the downstream never gets it" "not in table" "$D_DEF"
expect_in "test policy rib at table: TBL term m, metric 9" "result: accept, by term m" "$R_TBL"
expect "  zebra's metric is 9 ($ZM_MED)" test "$ZM_MED" = 9
expect_in "test policy rib at ospf bgp: OEXP term c, metric 120" "metric 120" "$R_OSPF"
expect "  ospfd originates it with metric 120 ($OM_CUST)" test "$OM_CUST" = 120
expect_in "show policy route: the import, by UP term cust" \
  "neighbor 10.0.0.2 import: UP+TAIL simulated: accept, by UP term cust" "$W_CUST1"
expect_in "  bgpd's outcome beside it" "the path from 10.0.0.2 is in the BGP table: accepted" "$W_CUST1"
expect_in "  the export, by DN term mine, advertised" \
  "neighbor 10.2.0.2 export: DN simulated: accept, by term mine FRR: advertised to 10.2.0.2" "$W_CUST1"
expect_in "  the table policy, zebra holds it" "table-policy: TBL" "$W_CUST1"
expect_in "  the FIB policy, zebra installed it" "simulated: accept, by term c FRR: zebra installed it" "$W_CUST1"
expect_in "  the OSPF export, ospfd originates it" "ospfd originates it, as external" "$W_CUST1"
expect_in "a rejected route: UP term bogon, and bgpd rejected it" \
  "simulated: reject, by UP term bogon" "$W_BOGON1"
expect_in "the network: NET term 1, and BGP originates it" \
  "network 10.55.0.0/24: NET simulated: accept, by term 1 FRR: BGP originates it" "$W_NET1"
expect_in "  the upstream sent it back: BGP's loop check, not the policy" \
  "BGP dropped it before the policy: its AS path carries this box's AS, 65000" "$W_NET1"
# #542: a reject term with from policy, the daemon the oracle.
expect "reject + from policy: UP term gate asked GATE ($H_GATE)" pos "$H_GATE"
expect "  GATE, called inverted, accepted 100.64.20.0/24 by term yes ($H_GYES)" \
  pos "$H_GYES"
expect "  and set 100.64.30.0/24's MED by term med ($H_GMED)" pos "$H_GMED"
expect_in "  show policy GATE names the inverted map" "route-map PO-GATE-NOT-IN" "$S_GATE"
expect_in "  what GATE accepts, bgpd holds no path to" "not in table" "$B_GYES"
expect_in "  test policy: rejected by UP term gate" \
  "result: reject, by UP term gate" "$T_GYES"
expect_in "  show policy route: simulated reject by UP term gate, and bgpd rejected it" \
  "simulated: reject, by UP term gate" "$W_GYES1"
expect_in "  FRR's outcome beside it: rejected" \
  "the BGP table holds no path from 10.0.0.2: rejected" "$W_GYES1"
expect "  what GATE rejects goes on: bgpd has 100.64.30.0/24 with GATE's MED 31 ($MED_GMED)" \
  test "$MED_GMED" = 31
expect_in "  test policy: accepted by the chain's default" \
  "result: accept, by no term: the default of the use" "$(printf '%s' "$T_GMED" | tr -s ' \n' '  ')"
expect_in "  test policy: with MED 31" "metric 31" "$T_GMED"
expect_in "  show policy route agrees with bgpd" \
  "simulated: accept, by no term: the default of the use FRR: the path from 10.0.0.2 is in the BGP table: accepted" "$W_GMED1"
expect_in "  the term after gate still sees what GATE rejects: TAIL term t's route" \
  "result: accept, by TAIL term t" "$T_MED"
expect_not_in "  and 100.64.10.0/24 is not rejected by it" "not in table" "$B_DEF"
expect_not_in "the simulation and FRR never disagree" "disagree" "$W_ALL"
verdict
