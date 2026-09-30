#!/bin/sh
# Binary API: a real return code, instead of matching text patterns.
set -u
mkdir -p /run/vpp /vpp_plugins /vat2_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
# The vat2 plugin where a real box has it, BEFORE the first call rather than
# halfway down. Every vat2 invocation below used to carry "-p /vat2_plugins",
# which is the build tree's layout and no appliance's: the lab proved the wire
# format against a client configuration nobody receives, and would not have
# noticed appliance/install.sh failing to install the plugin at all. lib.sh
# says exactly this and this file was calling it too late to matter.
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
use_binary_api

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp

echo "=== was the API registered? ==="
vppctl show api message-table 2>/dev/null | grep -i bpa | head -6 | sed 's/^/  /'

echo
echo "=== creating a pool through the binary API ==="
# Every field spelled out, including the ones prismash would fill in. That is
# the point of this lab: it drives the WIRE FORMAT, not prismash's model of it,
# so a field added to one and forgotten in the other is caught here.
vat2 bpa_pool_add_del '{
  "is_add": true,
  "name": "via-api",
  "block_size": 512,
  "port_min": 1024,
  "port_max": 65535,
  "policy": "BPA_API_POLICY_INTERLEAVED",
  "mode": "BPA_API_MODE_DETERMINISTIC",
  "filter": "BPA_API_FILTER_ENDPOINT_INDEPENDENT",
  "max_blocks": 0,
  "initial_blocks": 0,
  "block_stride": 0,
  "block_reserve": 0,
  "alg": 0,
  "alg_set": 0,
  "tcp_mss": 0,
  "tcp_mss_auto": false,
  "vrf_id": 4294967295,
  "out_vrf_id": 4294967295,
  "audit_user": "api-lab",
  "audit_uid": 4242,
  "timeouts": {"udp": 0, "icmp": 0, "tcp_syn": 0, "tcp_transitory": 0,
               "tcp_established": 0, "gre": 0, "port_holddown": 0,
               "alg_expect": 0},
  "n_inside": 1,
  "n_prefixes": 3,
  "prefixes": ["100.64.0.0/22", "200.150.0.0/24", "45.170.16.0/26"]
}' 2>&1 | sed 's/^/  /'

echo
echo "=== did the dataplane receive it? ==="
vppctl show bpa pool | head -4 | sed 's/^/  /'

echo
echo "=== dump over the API ==="
vat2 bpa_pool_dump '{}' 2>&1 | head -14 | sed 's/^/  /'

echo
echo "=== an error returns a CODE, not text ==="
echo "  (block 500 is not a power of two)"
vat2 bpa_pool_add_del '{
  "is_add": true, "name": "bad", "block_size": 500,
  "port_min": 1024, "port_max": 65535,
  "policy": "BPA_API_POLICY_INTERLEAVED", "mode": "BPA_API_MODE_DETERMINISTIC",
  "filter": "BPA_API_FILTER_ENDPOINT_INDEPENDENT",
  "max_blocks": 0,
  "initial_blocks": 0,
  "block_stride": 0,
  "block_reserve": 0,
  "alg": 0,
  "alg_set": 0,
  "tcp_mss": 0,
  "tcp_mss_auto": false,
  "vrf_id": 4294967295,
  "out_vrf_id": 4294967295,
  "audit_user": "api-lab",
  "audit_uid": 4242,
  "timeouts": {"udp": 0, "icmp": 0, "tcp_syn": 0, "tcp_transitory": 0,
               "tcp_established": 0, "gre": 0, "port_holddown": 0,
               "alg_expect": 0},
  "n_inside": 1, "n_prefixes": 2,
  "prefixes": ["10.0.0.0/24", "203.0.113.0/24"]
}' 2>&1 | sed 's/^/  /'
echo "  did the 'bad' pool get in? (it must not)"
vppctl show bpa pool | grep -c "pool bad" | sed 's/^/    occurrences: /'
echo "  and the detailed reason went to the log:"
vc show logging 2>/dev/null | grep -i "block size" | tail -1 | sed 's/^/    /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
OK=$(vat2 bpa_pool_dump '{}' 2>&1)
expect_in "the pool created through the API is listed" "via-api" "$OK"

# The capacity the API reports is the one a subscriber can reach.
#
# `capacity` alone is a ceiling nobody is promised once a dynamic pool's
# blocks are sharded per worker (#210). `show bpa pool` stopped claiming it
# alone; this message went on doing it, and it is the interface read by a
# provisioning system rather than by somebody who might notice.
#
# This pool is DETERMINISTIC, so its blocks are a formula and there is no
# slice to run out of: the floor must be the ceiling, capped by the inside
# set. That identity is asked OF THE BOX - all three numbers come out of the
# same reply - rather than recomputed here, because a lab that recomputes the
# arithmetic agrees with itself instead of with the product.
num_of() {
  printf '%s' "$2" | tr -d ' \t\r' | sed -n "s/.*\"$1\":\([0-9][0-9]*\).*/\1/p" | head -1
}
API_CAP=$(num_of capacity "$OK")
API_IN=$(num_of inside_count "$OK")
API_FLOOR=$(num_of guaranteed "$OK")
API_CEIL=$API_CAP
[ -n "$API_CAP" ] && [ -n "$API_IN" ] && [ "$API_IN" -lt "$API_CAP" ] && API_CEIL=$API_IN
echo "  capacity ${API_CAP:-none}, inside ${API_IN:-none}, guaranteed ${API_FLOOR:-none}"
# Differing defaults on purpose. A missing field would make both sides empty,
# and `[ "$a" = "$b" ]` on two empty strings is a check that passes by being
# unable to read anything - the false green #210's own lab shipped once.
expect "the API reports the number a subscriber can reach" \
  sh -c "[ '${API_FLOOR:-no floor}' = '${API_CEIL:-no ceiling}' ]"
BAD=$(vat2 bpa_pool_add_del '{"is_add":true,"name":"z","block_size":500,"port_min":1024,"port_max":65535,"policy":"BPA_API_POLICY_INTERLEAVED","mode":"BPA_API_MODE_DETERMINISTIC","filter":"BPA_API_FILTER_ENDPOINT_INDEPENDENT","max_blocks":0,"initial_blocks":0,"block_stride":0,"block_reserve":0,"alg":0,"alg_set":0,"tcp_mss":0,"tcp_mss_auto":false,"vrf_id":4294967295,"out_vrf_id":4294967295,"audit_user":"api-lab","audit_uid":4242,"timeouts":{"udp":0,"icmp":0,"tcp_syn":0,"tcp_transitory":0,"tcp_established":0,"gre":0,"port_holddown":0,"alg_expect":0},"n_inside":1,"n_prefixes":2,"prefixes":["10.0.0.0/24","203.0.113.0/24"]}' 2>&1)
# vat2 prints a real TAB between the key and the value. Written as '\t' in a
# quoted shell pattern it is a backslash followed by a t, which matches
# nothing vat2 has ever printed - so this, the ONLY check that the binary API
# reports failure, passed even when the API returned success for a block size
# of 500. Asserted on the value instead, extracted rather than pattern-matched.
BAD_RETVAL=$(printf '%s' "$BAD" | tr -d ' \t\r' | sed -n 's/.*"retval":\(-*[0-9]*\).*/\1/p' | head -1)
echo "  retval do dataplane: ${BAD_RETVAL:-nenhum}"
expect "an invalid pool returns an error code" \
  sh -c "[ -n '${BAD_RETVAL}' ] && [ '${BAD_RETVAL}' != '0' ]"
expect_not_in "and is not created" "pool z " "$(vppctl show bpa pool)"

# --- the client prismash will use, against a real dataplane ---
# The client deliberately takes no plugin path: on an appliance there is none
# to give. use_binary_api, at the top of this file, set up the layout a real
# box runs - and every vat2 call above now uses it too.
CLI=$(python3 /src/lab/api_client_check.py 2>&1)
echo "$CLI" | sed 's/^/  /'
expect_in "the client reports the dataplane ready" "available: ok" "$CLI"
expect_in "a valid pool comes back ok" "valid: ok 0" "$CLI"
expect_in "an invalid pool comes back refused" "invalid: refused -7" "$CLI"
expect_in "and carries a reason an operator can act on" "power of two" "$CLI"
expect_not_in "the refused pool really is absent" "pool client-bad" "$(vppctl show bpa pool)"

# --- the client's OWN failures, told apart from the dataplane's (#136) ---
# vat2 cannot render a bitfield with more than one bit set, and it does not
# refuse: it dereferences a null pointer and dies, or exits 0 with "Failed
# converting JSON to API" on stderr. Both used to arrive here as not-running,
# which sends the operator to restart a dataplane that is answering. VPP is
# still up for these two lines - the stopped-dataplane case is below, and it
# has to keep saying something different.
expect_in "a value the client cannot encode is refused, not blamed on VPP" \
  "unencodable: cannot-send" "$CLI"
expect_in "and it names the field that could not be said" "flags" "$CLI"
expect_in "a vat2 that segfaulted is reported as the client's failure" \
  "crashing: cannot-send" "$CLI"
expect_in "with the one fact that decides what to do next" \
  "the dataplane never saw it" "$CLI"

# --- the same checks over ONE CONNECTION: vpp_papi (#527) ---
# prismash's own client, against the same dataplane. The pool outcomes, the
# refusal and its reason must be what vat2 gave; vat2's segfault has no
# counterpart here and is not asked.
PAPI=$(python3 /src/lab/api_client_check.py papi 2>&1)
echo "$PAPI" | sed 's/^/  /'
expect_in "the client uses one vpp_papi connection" "transport: vpp_papi" "$PAPI"
expect_in "and reports the dataplane ready over it" "available: ok" "$PAPI"
expect_in "a valid pool comes back ok over it" "valid: ok 0" "$PAPI"
expect_in "an invalid pool comes back refused -7 over it" \
  "invalid: refused -7" "$PAPI"
expect_in "with the same reason an operator can act on" "power of two" "$PAPI"
expect_in "and a value it cannot encode is still refused before it is sent" \
  "unencodable: cannot-send" "$PAPI"
expect_in "the valid pool is on the dataplane" "client-ok-papi" \
  "$(vppctl show bpa pool)"

# EVERY READ THE VTY MAKES, through both, on a dataplane with something in it:
# a loopback with addresses of both families, a description, a VRF, a
# sub-interface and the pools above. The two answers must be the same once
# vpp_papi's reply is converted - that conversion is what every caller reads.
vppctl create loopback interface >/dev/null
vppctl set interface state loop0 up
vppctl set interface ip address loop0 10.9.0.1/24
vppctl set interface ip address loop0 2001:db8::1/64
vppctl set interface tag loop0 uplink-desc >/dev/null 2>&1
vppctl ip table add 7 >/dev/null
vppctl create loopback interface >/dev/null
vppctl set interface ip table loop1 7
vppctl create sub-interfaces loop0 100 >/dev/null
# A row in each of the other reads, so none of them is compared empty: a
# virtual router, a bundle, and an exporter with addresses in it.
vppctl vrrp vr add loop0 vr_id 7 priority 150 interval 100 10.9.0.254 >/dev/null
vppctl create bond mode lacp >/dev/null
vppctl set ipfix exporter collector 10.9.0.9 src 10.9.0.1 >/dev/null
DEFINED=$(python3 /src/lab/api_client_check.py papi defined 2>&1)
echo "$DEFINED" | sed 's/^/  /'
expect_not_in "every message prismash sends is in the definitions it loads" \
  "; not:" "$DEFINED"
PARITY=$(python3 /src/lab/api_client_check.py papi parity 2>&1)
echo "$PARITY" | sed 's/^/  /'
expect_not_in "every read answers the same through vpp_papi as through vat2" \
  "DIFFERENT" "$PARITY"
expect_in "the interface table was compared, loop0 and all" \
  "parity sw_interface_dump: same" "$PARITY"
expect_in "and a table read through a call" \
  "parity sw_interface_get_table: same" "$PARITY"
expect_in "a virtual router compared, not an empty list" \
  "parity vrrp_vr_dump: same (ok/ok, 1 answer(s))" "$PARITY"
expect_in "a bundle compared" \
  "parity sw_bond_interface_dump: same (ok/ok, 1 answer(s))" "$PARITY"

# --- with the dataplane stopped: the case that used to be a traceback ---
pkill -f "vpp -c" 2>/dev/null
sleep 2
DOWN=$(python3 /src/lab/api_client_check.py down 2>&1)
echo "  $DOWN"
expect_in "a stopped dataplane is reported, not crashed into" "not-running" "$DOWN"
expect_in "and says what to check" "systemctl" "$DOWN"
# And over vpp_papi: no socket to connect to is vat2's answer, the same one.
DOWNP=$(python3 /src/lab/api_client_check.py papi down 2>&1)
echo "$DOWNP" | sed 's/^/  /'
expect_in "a stopped dataplane over vpp_papi falls back and says so" \
  "not-running" "$DOWNP"
verdict
