#!/bin/sh
# A non-first fragment carries no port. Without shallow virtual reassembly it
# matches no session and disappears - large DNS, VPN and gaming break
# intermitente.
#
# Two interfaces, attached two different ways, carrying the same two
# fragments: pg0 by "set interface bpa" at the VPP CLI, pg2 by the
# bpa_interface_add_del_feature binary API message, which is the ONLY way the
# product attaches an interface since #76 moved the VTY off vppctl. Both must
# end up with the same box. Until #183 they did not: the CLI turned
# reassembly on and the API did not, and nothing said so.
set -u
mkdir -p /run/vpp /vpp_plugins /vat2_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh
use_binary_api

vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl create packet-generator interface pg2 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface state pg2 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
# pg2 is a second inside interface and needs its own subnet, so it gets its
# own pool. Subscriber 5 of a pool is the first address of its outside set
# plus block 1536-2047 on both, which is what makes the two halves of this
# lab read the same.
vppctl set interface ip address pg2 100.65.0.254/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl bpa pool api inside 100.65.0.0/24 outside 200.150.0.32/30 block 512

echo "=========== 1. pg0, attached at the VPP CLI ==========="
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

echo "=========== 2. pg2, attached over the binary API ==========="
# Exactly the message hexash sends, on the wire, with every field spelled out
# - not the CLI command with a different spelling. If the handler diverges
# from the CLI's again, it diverges here.
PG2=$(vppctl show interface | awk '$1=="pg2" {print $2}')
echo "  pg2 is sw_if_index $PG2"
vat2 bpa_interface_add_del_feature \
  "{\"is_add\":true,\"is_inside\":true,\"sw_if_index\":$PG2,\
    \"audit_user\":\"frag-lab\",\"audit_uid\":4242}" 2>&1 | sed 's/^/  /'

PG2_FEATURES=$(vppctl show interface features pg2)

echo
echo "=== reassembly virtual raso ficou ligado nas duas interfaces? ==="
for i in pg0 pg2; do
  printf '  %s: ' "$i"
  vppctl show interface features $i | grep -iE "reass" | tr -d ' \n' \
    || printf '(does not appear)'
  echo
done

# send_pair <in-interface> <inside-address> <pcap>
#
# The same two fragments both times: only the interface they arrive on and
# the pool they belong to change, so any difference in the result is a
# difference between the two ways the interface was attached.
send_pair() {
  rm -f "$3"
  vppctl packet-generator capture pg1 pcap "$3" count 2 >/dev/null
  for w in first second; do
    H=$(python3 /src/lab/mkfrag.py "$2" 8.8.8.8 12345 53 $w)
    L=$(( ${#H} / 2 - 1 ))
    vppctl packet-generator new "name f$1$w limit 1 node ip4-input interface $1 \
        size ${L}-${L} data { hex $H }" >/dev/null
  done
  vppctl clear trace >/dev/null; vppctl trace add pg-input 6 >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 2
  vppctl packet-generator delete "f$1first" >/dev/null 2>&1
  vppctl packet-generator delete "f$1second" >/dev/null 2>&1
}

# report <label> <expected-outside-address>
report() {
  echo
  echo "=== what the dataplane did on $1 ==="
  vppctl show trace | grep -E "BPA in2out" | sed 's/^/  /'
  echo
  echo "=== what went out on the wire from $1 ==="
  python3 - "$2" "$3" <<'EOF' 2>&1 | sed 's/^/  /'
import ipaddress, struct, sys
want, path = sys.argv[1], sys.argv[2]
d = open(path,"rb").read()
endian = "<" if d[:4] in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
link = struct.unpack(endian+"I", d[20:24])[0]
off, n, ok = 24, 0, True
while off + 16 <= len(d):
    _,_,caplen,_ = struct.unpack(endian+"IIII", d[off:off+16]); off += 16
    p = d[off:off+caplen]; off += caplen
    if link == 1: p = p[14:]
    if len(p) < 20 or (p[0] >> 4) != 4: continue
    n += 1
    hlen = (p[0] & 0xF) * 4
    fo = struct.unpack("!H", p[6:8])[0]
    mf, offs = bool(fo & 0x2000), (fo & 0x1FFF) * 8
    src = str(ipaddress.IPv4Address(p[12:16]))
    if offs == 0:
        sport = struct.unpack("!H", p[hlen:hlen+2])[0]
        print(f"fragment 1: src={src} offset=0 MF={mf} port={sport}")
        if src != want or sport != 1536: ok = False; print("  FALHA")
    else:
        print(f"fragment 2: src={src} offset={offs} MF={mf} (no UDP header)")
        if src != want: ok = False; print("  FALHA")
print(f"\n{'OS DOIS FRAGMENTOS TRADUZIDOS' if ok and n==2 else f'PROBLEMA: {n} fragmentos'}")
EOF
}

echo
echo "=========== enviando os dois fragmentos por pg0 (CLI) ==========="
echo "  expected: both leave as 200.150.0.17; only the first carries a port"
send_pair pg0 100.64.0.5 /tmp/frag-cli.pcap
report "pg0 (CLI)" 200.150.0.17 /tmp/frag-cli.pcap
CLI_TRACE=$(vppctl show trace)

echo
echo "=========== enviando os dois fragmentos por pg2 (binary API) ==========="
echo "  expected: both leave as 200.150.0.33; only the first carries a port"
send_pair pg2 100.65.0.5 /tmp/frag-api.pcap
report "pg2 (API)" 200.150.0.33 /tmp/frag-api.pcap
API_TRACE=$(vppctl show trace)

echo
echo "=========== 3. detaching over the API gives the refcount back ==========="
# A refcount that goes up on attach and not down on detach leaves shallow
# reassembly running on an interface the CGNAT no longer touches, and nothing
# ever turns it off again.
vat2 bpa_interface_add_del_feature \
  "{\"is_add\":false,\"is_inside\":true,\"sw_if_index\":$PG2,\
    \"audit_user\":\"frag-lab\",\"audit_uid\":4242}" 2>&1 | sed 's/^/  /'
PG2_AFTER_DETACH=$(vppctl show interface features pg2)
printf '  pg2 features after the detach: '
printf '%s\n' "$PG2_AFTER_DETACH" | grep -iE "reass" | tr -d ' \n' \
  || printf '(no reassembly, which is right)'
echo

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

# check_path <label> <trace> <inside:port -> outside:port>
#
# Two packets coming out is not the test. Without reassembly both still come
# out: the following fragment is read with whatever bytes sit where the ports
# would be, which opens a SECOND session and burns a port of the block - the
# run that found #183 shows "100.65.0.5:1 -> 200.150.0.33:1536 (new session)"
# next to the real one on :1537, so the datagram left in two halves wearing
# two different source ports. So what is asserted is the number of sessions
# the two fragments opened between them, and the ports the following one
# actually left with.
check_path() {
  echo "  -- $1"
  expect_in "the non-first fragment was recognised" "[non-first fragment]" "$2"
  NEWSESS=$(printf '%s\n' "$2" | grep -c "BPA in2out .*(new session)" || true)
  echo "     sessions opened by the two fragments: $NEWSESS"
  expect "the two fragments opened one session between them" \
    test "$NEWSESS" = "1"
  # The trace line for the following fragment, and only it: which of the two
  # fragments the pg feeds in first is not fixed, so the one that opens the
  # session - and carries "(new session)" in the middle of the line - is
  # sometimes this one. Anchoring the needle to the whole line made the check
  # depend on that order instead of on the ports.
  FRAGLINE=$(printf '%s\n' "$2" | grep -F "[non-first fragment]" || true)
  expect_in "and the following fragment was translated with the first one's ports" \
    "$3 proto udp" "$FRAGLINE"
}

check_path "pg0, attached at the VPP CLI" "$CLI_TRACE" \
  "100.64.0.5:12345 -> 200.150.0.17:1536"
expect_in "reassembly feature is on the interface" "ip4-sv-reassembly-feature" \
  "$(vppctl show interface features pg0)"

# The same three checks, for the path the product actually uses. They are the
# ones that failed before #183: the API handler enabled the feature and
# returned, so pg2 carried CGNAT without reassembly.
check_path "pg2, attached over the binary API" "$API_TRACE" \
  "100.65.0.5:12345 -> 200.150.0.33:1536"
expect_in "an interface attached over the API also gets reassembly" \
  "ip4-sv-reassembly-feature" "$PG2_FEATURES"

expect_not_in "and detaching over the API takes it off again" \
  "ip4-sv-reassembly-feature" "$PG2_AFTER_DETACH"
verdict
