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
# Every field spelled out, including the ones hexash would fill in. That is
# the point of this lab: it drives the WIRE FORMAT, not hexash's model of it,
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
BAD=$(vat2 bpa_pool_add_del '{"is_add":true,"name":"z","block_size":500,"port_min":1024,"port_max":65535,"policy":"BPA_API_POLICY_INTERLEAVED","mode":"BPA_API_MODE_DETERMINISTIC","filter":"BPA_API_FILTER_ENDPOINT_INDEPENDENT","max_blocks":0,"block_stride":0,"block_reserve":0,"alg":0,"alg_set":0,"tcp_mss":0,"tcp_mss_auto":false,"vrf_id":4294967295,"out_vrf_id":4294967295,"audit_user":"api-lab","audit_uid":4242,"timeouts":{"udp":0,"icmp":0,"tcp_syn":0,"tcp_transitory":0,"tcp_established":0,"gre":0,"port_holddown":0,"alg_expect":0},"n_inside":1,"n_prefixes":2,"prefixes":["10.0.0.0/24","203.0.113.0/24"]}' 2>&1)
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

# --- the client hexash will use, against a real dataplane ---
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

# --- with the dataplane stopped: the case that used to be a traceback ---
pkill -f "vpp -c" 2>/dev/null
sleep 2
DOWN=$(python3 /src/lab/api_client_check.py down 2>&1)
echo "  $DOWN"
expect_in "a stopped dataplane is reported, not crashed into" "not-running" "$DOWN"
expect_in "and says what to check" "systemctl" "$DOWN"
verdict
