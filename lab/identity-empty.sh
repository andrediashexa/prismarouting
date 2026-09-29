# OFF-RED-AT-LEAST: 2
# A lease file with no lease in it is an identity source with nothing to say,
# not a reason for the dataplane to die (#490).
#
# `cgnat identity dhcp-leases <path>` reaches bpa_identity_load_kind(), which
# collapsed a lease file's renewals with vec_set_len() on the entries it had
# read - and a file with none leaves that vector a null pointer, through which
# VPP's vec_set_len writes the length (its ASSERT is compiled out). Measured on
# 0.140.2: an empty file, which is what dhcpd writes before its first lease,
# took the dataplane down with SIGSEGV. It was found because the plugin was
# built at -O2 for the first time and the compiler said so.
#
# Both shapes of "nothing": a file of zero bytes, and one with only a comment.
# Then a file with a lease in it, so the lab also shows the source still reads
# what it is given - a guard that made every file empty would pass the first
# two rows.
. /src/lab/lib.sh
. /src/lab/verdict.sh
set -u
MODE=${1:-on}
mkdir -p /run/vpp /vpp_plugins /var/log/vpp
if [ "$MODE" = off ]; then
  # The guard taken out: the dataplane must die on the first empty file.
  mkdir -p /tmp/build-off && cd /tmp/build-off \
    && cmake -G Ninja -DCMAKE_C_FLAGS=-DBPA_IDENTITY_EMPTY_OFF /src >/dev/null && ninja >/dev/null
else
  (cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
    mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja >/dev/null
  }
fi
cd /src
use_binary_api
vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp /tmp/vpp.log || exit 1

: > /tmp/empty.leases
printf '# dhcpd.leases, before the first lease\n' > /tmp/comment.leases
cat > /tmp/one.leases <<'EOF'
lease 100.64.0.7 {
  starts 1 2026/09/28 10:00:00;
  ends 1 2026/09/28 22:00:00;
  binding state active;
  hardware ethernet 02:00:00:00:00:07;
  client-hostname "cpe-seven";
}
EOF

alive() { vppctl show version >/dev/null 2>&1 && echo yes || echo no; }

echo "=========== an identity source with no lease in it ==========="
vat2 bpa_identity_source_set '{"kind": 1, "path": "/tmp/empty.leases"}' >/dev/null 2>&1
sleep 1; EMPTY=$(alive); echo "  zero bytes: dataplane alive: $EMPTY"
vat2 bpa_identity_source_set '{"kind": 1, "path": "/tmp/comment.leases"}' >/dev/null 2>&1
sleep 1; COMMENT=$(alive); echo "  a comment only: dataplane alive: $COMMENT"

echo; echo "=========== and one with a lease still reads it ==========="
vat2 bpa_identity_source_set '{"kind": 1, "path": "/tmp/one.leases"}' >/dev/null 2>&1
SAID=$(vppctl show bpa identity 2>&1 | tr -d "\r")
printf '%s\n' "$SAID" | sed 's/^/  | /' | head -8

echo; echo "=========== VERDICT ==========="
expect "an empty lease file leaves the dataplane up" test "$EMPTY" = yes
expect "  and so does one with only a comment" test "$COMMENT" = yes
expect_in "a lease file with a lease still reads it" "identity table: 1 entry(s)" "$SAID"
verdict
