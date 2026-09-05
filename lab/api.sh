#!/bin/sh
# API binaria: codigo de retorno de verdade, em vez de casar padrao de texto.
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
vat2 -p /vat2_plugins bpa_pool_add_del '{
  "is_add": true,
  "name": "via-api",
  "block_size": 512,
  "port_min": 1024,
  "port_max": 65535,
  "policy": "BPA_API_POLICY_INTERLEAVED",
  "mode": "BPA_API_MODE_DETERMINISTIC",
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
BAD=$(vat2 -p /vat2_plugins bpa_pool_add_del '{"is_add":true,"name":"z","block_size":500,"port_min":1024,"port_max":65535,"policy":"BPA_API_POLICY_INTERLEAVED","mode":"BPA_API_MODE_DETERMINISTIC","n_inside":1,"n_prefixes":2,"prefixes":["10.0.0.0/24","203.0.113.0/24"]}' 2>&1)
expect_not_in "an invalid pool returns an error code" '"retval":\t0' "$BAD"
expect_not_in "and is not created" "pool z " "$(vppctl show bpa pool)"
verdict
