#!/bin/sh
# The prisma-bng container's shell (#597): appliance/prisma-bng (the host's
# lifecycle), appliance/prisma-bng-entrypoint (the container's plumbing) and
# appliance/prisma-bng-session (each session's CGNAT, MTU and MSS), driven
# with a stub docker-compose, docker, vppctl and ip that record what they
# were asked. lab/pppoe.sh drives the same three against a real dataplane.
#
# Each behaviour, shown red by a mutation:
#   * apply starts a stopped container and RECREATES a running one, naming
#     the service and its profile (mutation: `up` without the service name
#     - the profile then keeps it out and nothing starts);
#   * a box running the dataplane as units is refused, not half-started;
#   * boot starts it only when a configuration was generated;
#   * terminate takes only the words the doors take (mutation: the user
#     name not checked);
#   * the plumbing: the TAP's Linux MAC is the access card's, one VLAN device
#     per tag, `create pppoe cp` on the TAP (mutation: the MAC not set);
#   * a stale session left by a dead control plane can be deleted
#     (clean_sessions, which stop_all uses; at start accel-pppd removes them
#     itself, after their routes - #607);
#   * the session hook attaches the CGNAT ONCE on a recycled interface
#     (mutation: the features not asked first - VPP stacks a second
#     bpa-in2out, measured in lab/pppoe.sh's first runs);
#   * reload (#607) asks the running container, and keeps its CLI password
#     (mutation: run_conf making a new one - the running accel-pppd's CLI
#     still has the old, and the host is locked out of it).
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fails=0
ok() { echo "  [ ok ] $1"; }
bad() { echo "  FAIL: $1"; fails=$((fails + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

mkdir -p "$T/bin" "$T/etc/bng" "$T/run"
# docker-compose and docker: their argv into a log; `inspect` answers
# from $T/running.
cat > "$T/bin/docker-compose" <<EOF
#!/bin/sh
echo "compose \$*" >> "$T/log"
echo "env PRISMA_VERSION=\${PRISMA_VERSION:-} PRISMA_VPP_GID=\${PRISMA_VPP_GID:-}" >> "$T/log"
EOF
cat > "$T/bin/docker" <<EOF
#!/bin/sh
echo "docker \$*" >> "$T/log"
case "\$1" in
  inspect) cat "$T/running" 2>/dev/null || echo false ;;
  exec) shift 2; echo "exec \$*" >> "$T/log"
        if [ -f "$T/execfail" ]; then echo "accel-pppd refused the file"; exit 1; fi ;;
esac
EOF
chmod +x "$T/bin/"*
export PRISMA_COMPOSE="$T/bin/docker-compose" PRISMA_DOCKER="$T/bin/docker"
export PRISMA_ETC="$T/etc" PRISMA_RUN="$T/run" PRISMA_LOG="$T/varlog"
BNG="sh $REPO/appliance/prisma-bng"
mkdir -p "$T/etc"
mkdir -p "$T/etc/bng"

echo "=== prisma-bng: the container's lifecycle, on the host ==="
: > "$T/log"
out=$($BNG apply 2>&1); rc=$?
check "apply on a box with no compose.yaml is refused: it runs units" \
  "[ $rc -eq 4 ] && echo '$out' | grep -q 'systemd units'"
touch "$T/etc/compose.yaml"
out=$($BNG apply 2>&1); rc=$?
check "apply with nothing generated is refused" "[ $rc -eq 3 ]"
touch "$T/etc/bng/accel-ppp.conf" "$T/etc/bng/access.env"
echo false > "$T/running"; : > "$T/log"
out=$($BNG apply 2>&1)
check "apply starts a stopped container, by its name and its profile" \
  "[ '$out' = started ] && grep -q 'compose -f $T/etc/compose.yaml --profile bng up -d --no-deps bng' '$T/log'"
echo true > "$T/running"; : > "$T/log"
out=$($BNG apply 2>&1)
check "apply recreates a running one on the new files" \
  "[ '$out' = restarted ] && grep -q 'up -d --no-deps --force-recreate bng' '$T/log'"
check "apply makes accel-ppp's log directory on the log volume" "[ -d '$T/varlog/bng' ]"
echo false > "$T/running"; : > "$T/log"
printf 'PRISMA_VERSION=0.154.0\nPRISMA_VPP_GID=997\n' > "$T/run/compose.env"
$BNG apply > /dev/null 2>&1
check "compose is given the stack's tag and vpp group from compose.env, as the unit gives them - an installed box has no :latest" \
  "grep -q 'env PRISMA_VERSION=0.154.0 PRISMA_VPP_GID=997' '$T/log'"
: > "$T/log"
PRISMA_VERSION=0.155.0 $BNG apply > /dev/null 2>&1
check "  and a tag already in the environment wins" \
  "grep -q 'env PRISMA_VERSION=0.155.0 PRISMA_VPP_GID=997' '$T/log'"
rm "$T/run/compose.env"; : > "$T/log"
$BNG apply > /dev/null 2>&1
check "  with no compose.env, nothing is invented" "grep -q 'env PRISMA_VERSION= ' '$T/log'"
echo true > "$T/running"
touch "$T/run/compose-devices.yaml"; : > "$T/log"
$BNG apply > /dev/null 2>&1
check "with the stack's device fragment beside it, as the unit runs compose" \
  "grep -q -- '-f $T/run/compose-devices.yaml --profile bng' '$T/log'"
: > "$T/log"
out=$($BNG stop 2>&1)
check "stop stops and removes it" \
  "[ '$out' = stopped ] && grep -q 'stop -t 20 bng' '$T/log' && grep -q 'rm -f bng' '$T/log'"
check "status: running" "[ \"\$($BNG status)\" = running ]"
echo false > "$T/running"
check "status: stopped, while a configuration is there" "[ \"\$($BNG status)\" = stopped ]"
rm "$T/etc/bng/accel-ppp.conf"
check "status: not-configured" "[ \"\$($BNG status)\" = not-configured ]"
: > "$T/log"
out=$($BNG boot 2>&1); rc=$?
check "boot with nothing configured starts nothing" "[ $rc -eq 0 ] && [ ! -s '$T/log' ]"
touch "$T/etc/bng/accel-ppp.conf"
out=$($BNG boot 2>&1)
check "boot with a configuration starts it" "[ '$out' = started ]"
: > "$T/log"
out=$($BNG sessions 2>&1); rc=$?
check "sessions of a stopped container: rc 3, nothing asked" "[ $rc -eq 3 ] && ! grep -q exec '$T/log'"
echo true > "$T/running"
$BNG sessions > /dev/null 2>&1
check "sessions asked of accel-ppp in the container, by its columns" \
  "grep -q 'exec prisma-bng-cli show sessions sid,ifname,username,ip,calling-sid,state,uptime-raw,rx-bytes-raw,tx-bytes-raw,rate-limit' '$T/log'"
echo false > "$T/running"; : > "$T/log"
out=$($BNG reload 2>&1); rc=$?
check "reload of a stopped container: rc 3, nothing asked (#607)" \
  "[ $rc -eq 3 ] && ! grep -q exec '$T/log'"
echo true > "$T/running"; : > "$T/log"
out=$($BNG reload 2>&1); rc=$?
check "reload asks the entrypoint in the container, and nobody redials" \
  "[ $rc -eq 0 ] && [ '$out' = reloaded ] && grep -q 'exec prisma-bng-entrypoint reload' '$T/log' && ! grep -q compose '$T/log'"
touch "$T/execfail"
out=$($BNG reload 2>&1); rc=$?
check "a reload refused in the container is a failure, with its reason" \
  "[ $rc -eq 1 ] && echo '$out' | grep -q 'refused the file'"
rm -f "$T/execfail"
: > "$T/log"
$BNG terminate username sub1 > /dev/null 2>&1
$BNG terminate sid 8aa8eb1ab0657544 > /dev/null 2>&1
$BNG terminate all > /dev/null 2>&1
check "terminate: a user, a session id, all - hard, so the CPE hears PADT" \
  "grep -q 'exec prisma-bng-cli terminate username sub1 hard' '$T/log' && grep -q 'exec prisma-bng-cli terminate sid 8aa8eb1ab0657544 hard' '$T/log' && grep -q 'exec prisma-bng-cli terminate all hard' '$T/log'"
: > "$T/log"
for w in "username a;b" "sid 12" "all now" "ip 1.2.3.4"; do
  # shellcheck disable=SC2086
  $BNG terminate $w > /dev/null 2>&1 && bad "terminate $w was taken"
done
check "terminate refuses anything else, and asks nothing" "! grep -q exec '$T/log'"
PRISMA_BNG_SOURCE_ONLY=1 . "$REPO/appliance/prisma-bng"
check "terminate_ok: the same rule as the doors'" \
  "terminate_ok all && terminate_ok sid 8aa8eb1ab0657544 && ! terminate_ok username 'a b'"

echo
echo "=== the entrypoint: the plumbing, laid and taken out ==="
# vppctl: answers from files, every command into the log.
cat > "$T/bin/vppctl" <<EOF
#!/bin/sh
echo "vppctl \$*" >> "$T/vlog"
# As vppctl does: what is on stdin goes to the dataplane, so it is read.
[ -n "\${STUB_EAT_STDIN:-}" ] && cat > /dev/null
case "\$*" in
  "show version") echo "vpp v26.06-release" ;;
  "show hardware-interfaces ge0") printf 'ge0  1  up\n  Ethernet address 02:fe:00:00:00:01\n' ;;
  "show interface tap1863") [ -f "$T/tap" ] && echo "tap1863   9   up   1500/0/0/0" ;;
  "create tap"*) touch "$T/tap" ;;
  "delete tap"*) rm -f "$T/tap" ;;
  "show pppoe session") cat "$T/sessions" 2>/dev/null ;;
  "show interface features"*) cat "$T/features" 2>/dev/null ;;
  "show policer") cat "$T/policers" 2>/dev/null ;;
  "show ip fib") cat "$T/fib" 2>/dev/null ;;
  "show interface") printf '%s\n' "              Name               Idx    State" "pppoe_session3                    12     up" ;;
esac
EOF
cat > "$T/bin/ip" <<EOF
#!/bin/sh
echo "ip \$*" >> "$T/vlog"
case "\$*" in "link show"*) exit 1 ;; esac
EOF
chmod +x "$T/bin/"*
export PRISMA_VPPCTL="$T/bin/vppctl" PRISMA_IP="$T/bin/ip"
export PRISMA_BNG_CONF="$T/etc/bng" PRISMA_BNG_RUN="$T/run/bng"
mkdir -p "$T/run/bng"
printf 'PARENT=ge0\nVLANS="0 100"\nMTU=1492\nTCP_MSS=1452\n' > "$T/etc/bng/access.env"
PRISMA_BNG_SOURCE_ONLY=1 . "$REPO/appliance/prisma-bng-entrypoint"
: > "$T/vlog"
check "wait_vpp: the dataplane answers" "wait_vpp"
PRISMA_BNG_LOG="$T/varlog2/bng"; LOG_DIR=$PRISMA_BNG_LOG; mkdir -p "$T/varlog2"
check "log_dir: accel-ppp's log directory made where a box has none, 0750" \
  "log_dir && [ \"\$(stat -c %a '$T/varlog2/bng')\" = 750 ]"
check "hw_mac: the card's Ethernet address" "[ \"\$(hw_mac ge0)\" = 02:fe:00:00:00:01 ]"
plumb; rc=$?
check "plumb: laid" "[ $rc -eq 0 ]"
check "  the TAP made, named pppoe-cp on Linux" \
  "grep -q 'vppctl create tap id 1863 host-if-name pppoe-cp' '$T/vlog'"
check "  its Linux MAC is the access card's" \
  "grep -q 'ip link set pppoe-cp address 02:fe:00:00:00:01' '$T/vlog'"
check "  one VLAN device for the tag, none for untagged" \
  "grep -q 'ip link add link pppoe-cp name pppoe-cp.100 type vlan id 100' '$T/vlog' && ! grep -q 'pppoe-cp.0' '$T/vlog'"
check "  and the plugin told the TAP is its control plane" \
  "grep -q 'vppctl create pppoe cp cp-if-index 9' '$T/vlog'"
check "sw_index: the TAP's index" "[ \"\$(sw_index tap1863)\" = 9 ]"
printf '%s\n' "[0] sw-if-index 5 client-ip 0.0.0.0 session-id 448 encap-if-index 1 decap-fib-index 0" \
  "    local-mac 02:fe:de:42:1f:19  client-mac 02:aa:00:00:00:02" > "$T/sessions"
: > "$T/vlog"
n=$(clean_sessions)
check "clean_sessions: a session a dead control plane left, deleted" \
  "[ '$n' = 1 ] && grep -q 'vppctl create pppoe session client-ip 0.0.0.0 session-id 448 client-mac 02:aa:00:00:00:02 del' '$T/vlog'"
printf '%s\n' 'Name "vyos_12_20000_2000000_down" type 1r2c cir 20000' 'conform 1 packets, 2 bytes' \
  'Name "vyos_12_10000_1000000_up" type 1r2c cir 10000' 'Name "operators_own" type 1r2c' > "$T/policers"
: > "$T/vlog"
n=$(STUB_EAT_STDIN=1 clean_policers)
check "clean_policers: a dead control plane's session policers unapplied and deleted, by direction (#607) - with a vppctl that reads its stdin, which took the rest of the list (0.155.1)" \
  "[ '$n' = 2 ] && grep -q 'vppctl policer output unapply name vyos_12_20000_2000000_down pppoe_session3' '$T/vlog' && grep -q 'vppctl policer input unapply name vyos_12_10000_1000000_up pppoe_session3' '$T/vlog' && grep -q 'vppctl policer del name vyos_12_10000_1000000_up' '$T/vlog'"
check "  and a policer that is not a session's is left alone" "! grep -q operators_own '$T/vlog'"
printf '  policer-input\n' > "$T/features"; : > "$T/vlog"
STUB_EAT_STDIN=1 clean_policers > /dev/null
check "  and then clears what a deleted-while-applied policer left on the interfaces (unpoison)" \
  "grep -q 'vppctl policer add name prisma_unpoison' '$T/vlog'"
: > "$T/features"
printf '  policer-input\n' > "$T/features"; : > "$T/vlog"
n=$(unpoison)
check "unpoison: a session interface still carrying a policer feature is cleared through a policer made for it, then that is deleted (0.155.1)" \
  "[ '$n' = 1 ] && grep -q 'vppctl policer add name prisma_unpoison' '$T/vlog' && grep -q 'vppctl policer input unapply name prisma_unpoison pppoe_session3' '$T/vlog' && grep -q 'vppctl policer output unapply name prisma_unpoison pppoe_session3' '$T/vlog' && grep -q 'vppctl policer del name prisma_unpoison' '$T/vlog'"
check "  and gives up after 16 tries rather than for ever" \
  "[ \"\$(grep -c 'policer input unapply name prisma_unpoison' '$T/vlog')\" = 16 ]"
: > "$T/features"; : > "$T/vlog"
n=$(unpoison)
check "  an interface with none is left alone" "[ '$n' = 0 ] && ! grep -q prisma_unpoison '$T/vlog'"
# `show ip fib`'s whole table, as VPP 26.06 prints it: a prefix alone on its
# row (measured - `show ip fib <prefix>` adds "fib:0 index:...", the table
# does not, and the first cut of this read only the latter).
printf '%s\n' "ipv4-VRF:0, fib_index:0, flow hash:[src dst ]" "0.0.0.0/32" \
  "    [0-5] [@6]: ipv4 [features] via 0.0.0.0 pppoe_session3: mtu:1492" \
  "100.64.0.77/32" "  unicast-ip4-chain" "    [0] [@6]: ipv4 [features] via 0.0.0.0 pppoe_session3: mtu:1492" \
  "      [@0]: ipv4 via 0.0.0.0 pppoe_session3: mtu:1492" \
  "100.64.1.0/24" "    [0] [@5]: ipv4 via 100.64.1.2 host-lan0" > "$T/fib"
printf '%s\n' "[0] sw-if-index 12 client-ip 0.0.0.0 session-id 448 encap-if-index 1 decap-fib-index 0" \
  "    local-mac 02:fe:de:42:1f:19  client-mac 02:aa:00:00:00:02" > "$T/sessions"
n=$(stale_state)
check "stale_state: a dead daemon's routes through its sessions, once each, then the sessions - for accel-pppd to remove in that order (#607)" \
  "[ '$n' = 2 ] && [ \"\$(sed -n 1p '$T/run/bng/stale-state')\" = 'route 100.64.0.77/32 12' ] && [ \"\$(sed -n 2p '$T/run/bng/stale-state')\" = 'session 02:aa:00:00:00:02 448' ]"
check "  VPP's own 0.0.0.0/32 and a route through a card are not a session's" \
  "! grep -q '0.0.0.0/32\\|100.64.1.0' '$T/run/bng/stale-state'"
printf 'x=1\n' > "$T/etc/bng/accel-ppp.conf"
run_conf
check "run_conf: the CLI on loopback, with a password made here and kept 0600" \
  "grep -q '^tcp=127.0.0.1:2001' '$T/run/bng/accel-ppp.conf' && grep -q \"^password=\$(cat '$T/run/bng/cli.password')\" '$T/run/bng/accel-ppp.conf' && [ \"\$(stat -c %a '$T/run/bng/cli.password')\" = 600 ]"
pw1=$(cat "$T/run/bng/cli.password")
printf 'x=2\n' > "$T/etc/bng/accel-ppp.conf"
run_conf keep
check "run_conf keep: the new file, the running accel-pppd's password" \
  "[ \"\$(cat '$T/run/bng/cli.password')\" = '$pw1' ] && grep -q '^x=2' '$T/run/bng/accel-ppp.conf' && grep -q '^password=$pw1' '$T/run/bng/accel-ppp.conf'"
run_conf
check "run_conf without keep: a new one, as at every start" \
  "[ \"\$(cat '$T/run/bng/cli.password')\" != '$pw1' ]"
printf '#!/bin/sh\necho "$@" >> "%s/clilog"\ncat "%s/cliout" 2>/dev/null\n' "$T" "$T" > "$T/bin/prisma-bng-cli"
chmod +x "$T/bin/prisma-bng-cli"
: > "$T/cliout"
PATH="$T/bin:$PATH" reload; rc=$?
check "reload: the file rebuilt and accel-pppd asked to read it" \
  "[ $rc -eq 0 ] && grep -qx reload '$T/clilog'"
echo failed > "$T/cliout"
PATH="$T/bin:$PATH" reload 2>/dev/null; rc=$?
check "reload: accel-cmd's 'failed' (it exits 0) is a failure" "[ $rc -eq 1 ]"
mv "$T/run/bng/cli.password" "$T/run/bng/cli.password.x"
PATH="$T/bin:$PATH" reload 2>/dev/null; rc=$?
check "reload: no accel-pppd running here is 3" "[ $rc -eq 3 ]"
mv "$T/run/bng/cli.password.x" "$T/run/bng/cli.password"
: > "$T/vlog"
unplumb
check "unplumb: the VLAN device out, and the cp and the TAP LEFT - taking them out crashes VPP" \
  "grep -q 'ip link del pppoe-cp.100' '$T/vlog' && ! grep -q 'cp-if-index 9 del' '$T/vlog' && ! grep -q 'delete tap' '$T/vlog'"
printf '  pppoe-input\n' > "$T/features"; : > "$T/vlog"
plumb
check "plumb on a TAP already laid: the cp not enabled a second time" \
  "! grep -q 'create pppoe cp' '$T/vlog' && ! grep -q 'create tap' '$T/vlog'"
: > "$T/features"
rm -f "$T/tap"
PARENT=nosuch
printf 'PARENT=nosuch\nVLANS="0"\n' > "$T/etc/bng/access.env"
plumb > /dev/null 2>&1
check "plumb: an access card the dataplane does not have is refused" "[ $? -ne 0 ]"

echo
echo "=== the session hook ==="
printf 'PARENT=ge0\nVLANS="0"\nMTU=1492\nTCP_MSS=1452\n' > "$T/etc/bng/access.env"
: > "$T/features"; : > "$T/vlog"
sh "$REPO/appliance/prisma-bng-session" pppoe_session3 none 0 100.64.0.1 100.64.0.10 x
check "the CGNAT attached, the PPPoE MTU and the MSS clamp set" \
  "grep -q 'set interface bpa in2out pppoe_session3' '$T/vlog' && grep -q 'set interface mtu packet 1492 pppoe_session3' '$T/vlog' && grep -q 'set interface tcp-mss-clamp pppoe_session3 ip4 enable ip4-mss 1452 ip6 disable' '$T/vlog'"
printf '  bpa-in2out\n  tcp-mss-clamping-ip4-in\n' > "$T/features"; : > "$T/vlog"
sh "$REPO/appliance/prisma-bng-session" pppoe_session3 none 0 100.64.0.1 100.64.0.10 x
check "on a recycled interface that has them, neither is added again" \
  "! grep -q 'bpa in2out' '$T/vlog' && ! grep -q 'tcp-mss-clamp' '$T/vlog'"
printf 'PARENT=ge0\nVLANS="0"\nMTU=1492\n' > "$T/etc/bng/access.env"
: > "$T/features"; : > "$T/vlog"
sh "$REPO/appliance/prisma-bng-session" pppoe_session4 none 0 a b c
check "no tcp-mss configured, no clamp" "! grep -q 'tcp-mss-clamp' '$T/vlog'"

echo
echo "=== the CLI wrapper ==="
out=$(PRISMA_BNG_RUN="$T/none" sh "$REPO/appliance/prisma-bng-cli" show stat 2>&1); rc=$?
check "prisma-bng-cli with no accel-pppd running says so" "[ $rc -eq 3 ]"
printf '#!/bin/sh\necho "$@"\n' > "$T/bin/accel-cmd"; chmod +x "$T/bin/accel-cmd"
echo pw9 > "$T/run/bng/cli.password"
out=$(PRISMA_ACCEL_CMD="$T/bin/accel-cmd" sh "$REPO/appliance/prisma-bng-cli" show stat)
check "prisma-bng-cli: accel-cmd on loopback with the password" \
  "[ '$out' = '-H 127.0.0.1 -p 2001 -t 5 -P pw9 show stat' ]"

echo
if [ "$fails" -eq 0 ]; then echo "ALL TESTS PASSED"; else echo "$fails FAILURES"; exit 1; fi
