#!/bin/sh
# Binary API: a real return code, instead of matching text patterns.
set -u
mkdir -p /run/vpp /vpp_plugins /vat2_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done

echo "=== a API foi registrada? ==="
vppctl show api message-table 2>/dev/null | grep -i bpa | head -6 | sed 's/^/  /'

echo
echo "=== criando um pool pela API binaria ==="
# Every field spelled out, including the ones hexash would fill in. That is
# the point of this lab: it drives the WIRE FORMAT, not hexash's model of it,
# so a field added to one and forgotten in the other is caught here.
vat2 -p /vat2_plugins bpa_pool_add_del '{
  "is_add": true,
  "name": "via-api",
  "block_size": 512,
  "port_min": 1024,
  "port_max": 65535,
  "policy": "BPA_API_POLICY_INTERLEAVED",
  "mode": "BPA_API_MODE_DETERMINISTIC",
  "filter": "BPA_API_FILTER_ENDPOINT_INDEPENDENT",
  "max_blocks": 0,
  "alg": 0,
  "alg_set": 0,
  "timeouts": {"udp": 0, "icmp": 0, "tcp_syn": 0, "tcp_transitory": 0,
               "tcp_established": 0, "gre": 0, "port_holddown": 0,
               "alg_expect": 0},
  "n_inside": 1,
  "n_prefixes": 3,
  "prefixes": ["100.64.0.0/22", "200.150.0.0/24", "45.170.16.0/26"]
}' 2>&1 | sed 's/^/  /'

echo
echo "=== o dataplane recebeu? ==="
vppctl show bpa pool | head -4 | sed 's/^/  /'

echo
echo "=== dump pela API ==="
vat2 -p /vat2_plugins bpa_pool_dump '{}' 2>&1 | head -14 | sed 's/^/  /'

echo
echo "=== an error returns a CODE, not text ==="
echo "  (block 500 is not a power of two)"
vat2 -p /vat2_plugins bpa_pool_add_del '{
  "is_add": true, "name": "bad", "block_size": 500,
  "port_min": 1024, "port_max": 65535,
  "policy": "BPA_API_POLICY_INTERLEAVED", "mode": "BPA_API_MODE_DETERMINISTIC",
  "filter": "BPA_API_FILTER_ENDPOINT_INDEPENDENT",
  "max_blocks": 0,
  "alg": 0,
  "alg_set": 0,
  "timeouts": {"udp": 0, "icmp": 0, "tcp_syn": 0, "tcp_transitory": 0,
               "tcp_established": 0, "gre": 0, "port_holddown": 0,
               "alg_expect": 0},
  "n_inside": 1, "n_prefixes": 2,
  "prefixes": ["10.0.0.0/24", "203.0.113.0/24"]
}' 2>&1 | sed 's/^/  /'
echo "  did the 'bad' pool get in? (it must not)"
vppctl show bpa pool | grep -c "pool bad" | sed 's/^/    occurrences: /'
echo "  and the detailed reason went to the log:"
vppctl show logging 2>/dev/null | grep -i "block size" | tail -1 | sed 's/^/    /'

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
OK=$(vat2 -p /vat2_plugins bpa_pool_dump '{}' 2>&1)
expect_in "the pool created through the API is listed" "via-api" "$OK"
BAD=$(vat2 -p /vat2_plugins bpa_pool_add_del '{"is_add":true,"name":"z","block_size":500,"port_min":1024,"port_max":65535,"policy":"BPA_API_POLICY_INTERLEAVED","mode":"BPA_API_MODE_DETERMINISTIC","filter":"BPA_API_FILTER_ENDPOINT_INDEPENDENT","max_blocks":0,"alg":0,"alg_set":0,"timeouts":{"udp":0,"icmp":0,"tcp_syn":0,"tcp_transitory":0,"tcp_established":0,"gre":0,"port_holddown":0,"alg_expect":0},"n_inside":1,"n_prefixes":2,"prefixes":["10.0.0.0/24","203.0.113.0/24"]}' 2>&1)
expect_not_in "an invalid pool returns an error code" '"retval":\t0' "$BAD"
expect_not_in "and is not created" "pool z " "$(vppctl show bpa pool)"

# --- the client hexash will use, against a real dataplane ---
# The client deliberately takes no plugin path: on an appliance there is none
# to give. use_binary_api sets up the layout a real box runs.
. /src/lab/lib.sh
use_binary_api
CLI=$(python3 /src/lab/api_client_check.py 2>&1)
echo "$CLI" | sed 's/^/  /'
expect_in "the client reports the dataplane ready" "available: ok" "$CLI"
expect_in "a valid pool comes back ok" "valid: ok 0" "$CLI"
expect_in "an invalid pool comes back refused" "invalid: refused -7" "$CLI"
expect_in "and carries a reason an operator can act on" "power of two" "$CLI"
expect_not_in "the refused pool really is absent" "pool client-bad" "$(vppctl show bpa pool)"

# --- with the dataplane stopped: the case that used to be a traceback ---
pkill -f "vpp -c /src/lab/startup-test.conf" 2>/dev/null
sleep 2
DOWN=$(python3 /src/lab/api_client_check.py down 2>&1)
echo "  $DOWN"
expect_in "a stopped dataplane is reported, not crashed into" "not-running" "$DOWN"
expect_in "and says what to check" "systemctl" "$DOWN"
verdict
