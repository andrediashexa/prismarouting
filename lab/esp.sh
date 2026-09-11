#!/bin/sh
# IPsec ESP passthrough through the CGNAT.
#
# The claim under test is one sentence: two subscribers sharing one public
# address each keep their own IPsec tunnel. ESP carries no ports, so a plain
# NAT can tell one tunnel from another only by the address it arrived at - and
# behind a CGNAT that address is shared. The CPE that needs this is the one
# that does not do NAT-T (RFC 3948); a CPE that does never gets here, because
# it sends UDP 4500 and that is an ordinary session.
#
# The second claim is what the box must NOT do. The SPI is inside ESP's
# integrity check (RFC 4303 s3.3.2), so a CGNAT that "helpfully" renumbered it
# the way it renumbers a port would hand the subscriber a packet its own stack
# discards, with every counter reporting a success. Every ESP assertion here
# reads the SPI, the sequence number and the ciphertext off the CAPTURED
# PACKET and demands they are byte-identical, and reads the IP checksum to
# demand that the one field that did change is still covered.
#
# "sh tools/lab.sh esp off" runs the whole thing with passthrough switched
# off. Every assertion about a translated tunnel must fail in that run: an
# assertion that passes with the feature removed was never testing it.
set -u
MODE=${1:-on}
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
for g in 8.8.8.8 9.9.9.9 7.7.7.7; do
  vppctl ip route add $g/32 via 200.150.0.2 pg1
done
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0
for h in 5 6; do vppctl set ip neighbor pg0 100.64.0.$h 02:00:00:00:01:0$h; done

# "blocked" on purpose: it groups neighbouring subscribers onto the SAME public
# address, which is the only arrangement in which two tunnels can be confused
# for each other at all. Under the default interleaved policy the two
# subscribers would land on different addresses and the test would pass
# without testing anything.
vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512 policy blocked
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
[ "$MODE" = "off" ] && vppctl bpa alg lab esp off >/dev/null

send() {   # <interface> <hex>
  _h=$2; _l=$(( ${#_h} / 2 - 1 ))
  vppctl packet-generator delete p >/dev/null 2>&1
  vppctl packet-generator new "name p limit 1 node ip4-input interface $1 \
      size ${_l}-${_l} data { hex $_h }" >/dev/null
  vppctl packet-generator enable >/dev/null; sleep 0.5
  vppctl packet-generator delete p >/dev/null 2>&1
}
arm() {    # <interface where the answer will leave>
  rm -f /tmp/cap.pcap
  vppctl packet-generator capture "$1" pcap /tmp/cap.pcap count 1 >/dev/null
}
seen() { python3 /src/lab/checkesp.py /tmp/cap.pcap 2>/dev/null; }
field() { seen | awk -v n="$1" 'NR==1 {print $n}'; }
# Summed, not read off one line: the same counter exists on both nodes and a
# drop in either direction is the same fact about the same switch.
errs() { vppctl show errors | grep "$1" | awk '{s+=$1} END {print s+0}'; }

# The ciphertext as it was built, so "the payload came through unaltered" is a
# comparison against something rather than against itself.
PAY=$(python3 - <<'EOF'
print(sum((i * 7 + 3) & 0xFF for i in range(16)))
EOF
)

echo "=========== 0. passthrough is on out of the box ==========="
ALG=$(vppctl show bpa alg)
echo "$ALG" | sed 's/^/  /'

echo
echo "=========== 1. subscriber A's tunnel leaves, SPI untouched ==========="
# SPI 0x11111111 is the one A's PEER chose and A is sending to. A CGNAT that
# treated it as a port would rewrite it here.
arm pg1
send pg0 "$(python3 /src/lab/mkesp.py esp 100.64.0.5 8.8.8.8 0x11111111 7)"
seen | sed 's/^/  /'
A_PUB=$(field 2); A_OUT_SPI=$(field 4); A_OUT_SEQ=$(field 5)
A_OUT_PAY=$(field 6); A_OUT_CK=$(field 7)
echo "  100.64.0.5 -> ${A_PUB:-nothing left}"

echo
echo "=========== 2. the peer answers with an SPI this box has never seen ==="
# 0xAAAAAAAA is the SPI A's own stack chose and announced over IKE, which is
# encrypted. Nothing on the wire ever told this box about it, so a design that
# needed to learn it would drop this packet - the first packet of the inbound
# half of every SA.
arm pg0
send pg1 "$(python3 /src/lab/mkesp.py esp 8.8.8.8 "${A_PUB:-200.150.0.16}" 0xAAAAAAAA 11)"
seen | sed 's/^/  /'
A_IN_DST=$(field 3); A_IN_SPI=$(field 4); A_IN_SEQ=$(field 5)
A_IN_PAY=$(field 6); A_IN_CK=$(field 7)

echo
echo "=========== 3. subscriber B, same public address, a different peer ==="
arm pg1
send pg0 "$(python3 /src/lab/mkesp.py esp 100.64.0.6 9.9.9.9 0x22222222 7)"
seen | sed 's/^/  /'
B_PUB=$(field 2); B_OUT_SPI=$(field 4)
echo "  A is ${A_PUB:-none}; B is ${B_PUB:-none}"

echo
echo "=========== 4. B's peer answers, and B gets it - not A ==========="
arm pg0
send pg1 "$(python3 /src/lab/mkesp.py esp 9.9.9.9 "${B_PUB:-200.150.0.16}" 0xBBBBBBBB 11)"
seen | sed 's/^/  /'
B_IN_DST=$(field 3); B_IN_SPI=$(field 4)

echo
echo "=========== 5. and A's tunnel still reaches A afterwards ==========="
# Step 2 already proved this same packet reaches A. Repeating it after B's
# tunnel exists is the only way to tell "both tunnels work" from "the last one
# to arrive works".
arm pg0
send pg1 "$(python3 /src/lab/mkesp.py esp 8.8.8.8 "${A_PUB:-200.150.0.16}" 0xAAAAAAAA 12)"
seen | sed 's/^/  /'
A_AGAIN_DST=$(field 3); A_AGAIN_SPI=$(field 4)

echo
echo "=========== 6. ESP from a peer nobody has a tunnel to is refused ==="
# A full-cone NAT lets unsolicited inbound through on an open mapping. There is
# no mapping to be open here: the binding exists because a subscriber sent to
# that peer, and nothing else opens one.
arm pg0
send pg1 "$(python3 /src/lab/mkesp.py esp 7.7.7.7 "${A_PUB:-200.150.0.16}" 0xCCCCCCCC 1)"
STRAY=$(seen)
NO_TUNNEL=$(errs "ESP from a peer with no tunnel")
echo "  unsolicited ESP: ${STRAY:-nothing arrived}"
vppctl show errors | grep "with no tunnel" | sed 's/^/  /'

echo
echo "=========== 7. two subscribers, one address, the SAME peer ==========="
# The case this cannot serve, and it must say so rather than guess. B now asks
# for a tunnel to A's peer: every field of the two flows would be identical on
# the way back, so B is refused and A keeps working. Delivering B's ciphertext
# into A's IPsec stack would be the guess.
arm pg1
send pg0 "$(python3 /src/lab/mkesp.py esp 100.64.0.6 8.8.8.8 0x33333333 7)"
CLASH=$(seen)
TAKEN=$(errs "ESP peer is another")
echo "  B's tunnel to A's peer: ${CLASH:-nothing left}"
vppctl show errors | grep "ESP peer is another" | sed 's/^/  /'
arm pg0
send pg1 "$(python3 /src/lab/mkesp.py esp 8.8.8.8 "${A_PUB:-200.150.0.16}" 0xAAAAAAAA 13)"
seen | sed 's/^/  /'
A_SURVIVED=$(field 3)

echo
echo "=========== 8. the binding is counted, so 'off' and 'quiet' differ ==="
BOUND=$(errs "IPsec tunnels bound")
echo "  tunnels bound: ${BOUND:-0}"

echo
echo "=========== 9. switched off, ESP does not pass at all ==========="
vppctl bpa alg lab esp off | sed 's/^/  /'
# A subscriber and a peer with no binding of their own, so what is being
# measured is the switch and not a mapping that already existed.
arm pg1
send pg0 "$(python3 /src/lab/mkesp.py esp 100.64.0.5 7.7.7.7 0x44444444 7)"
OFF_OUT=$(seen)
echo "  outbound with passthrough off: ${OFF_OUT:-nothing left}"
# And on a tunnel that DOES already have a binding, which is the half a switch
# read only where a session is created would go on serving: the operator would
# see ESP still leaving while the replies were dropped, which is a one-way
# tunnel and looks like anything but a switch.
arm pg1
send pg0 "$(python3 /src/lab/mkesp.py esp 100.64.0.5 8.8.8.8 0x11111111 8)"
OFF_BOUND=$(seen)
echo "  outbound on an existing tunnel: ${OFF_BOUND:-nothing left}"
# And the return path too: a binding that already exists must stop being
# honoured, or turning the switch off would leave half the feature running.
arm pg0
send pg1 "$(python3 /src/lab/mkesp.py esp 8.8.8.8 "${A_PUB:-200.150.0.16}" 0xAAAAAAAA 14)"
OFF_IN=$(seen)
echo "  inbound with passthrough off: ${OFF_IN:-nothing arrived}"
OFF_CNT=$(errs "passthrough is off")
vppctl show errors | grep "passthrough is off" | sed 's/^/  /'
[ "$MODE" = "off" ] || vppctl bpa alg lab esp on >/dev/null

echo
echo "=========== VERDICT ==========="
# The STATE, not the row label: "show bpa alg" prints one row per ALG whether
# it is on or off, so matching "esp" would pass with passthrough disabled,
# including in the run this lab exists to fail.
expect "ESP passthrough is enabled by default" \
  sh -c "printf '%s' \"$ALG\" | tr -s ' ' ' ' | grep -q 'esp on'"
expect "A's tunnel leaves translated" \
       sh -c "test -n '${A_PUB:-}' && test '${A_PUB:-}' != '100.64.0.5'"
expect "the outbound SPI is NOT rewritten" \
       test "${A_OUT_SPI:-0}" -eq 286331153
expect "the outbound sequence number is untouched" \
       test "${A_OUT_SEQ:-0}" -eq 7
expect "the outbound ciphertext is untouched" \
       test "${A_OUT_PAY:-0}" -eq "$PAY"
expect "the translated packet's IP checksum still covers it" \
       test "${A_OUT_CK:-BAD}" = "ok"
expect "the peer's answer reaches subscriber A" \
       test "${A_IN_DST:-}" = "100.64.0.5"
expect "an SPI this box never saw announced is delivered, not dropped" \
       test "${A_IN_SPI:-0}" -eq 2863311530
expect "the inbound sequence number is untouched" \
       test "${A_IN_SEQ:-0}" -eq 11
expect "the inbound ciphertext is untouched" \
       test "${A_IN_PAY:-0}" -eq "$PAY"
expect "the inbound packet's IP checksum still covers it" \
       test "${A_IN_CK:-BAD}" = "ok"
# The test is only a test if the two subscribers really share one address.
expect "A and B share a public address, so they really can be confused" \
       sh -c "test -n '${A_PUB:-}' && test '${A_PUB:-}' = '${B_PUB:-y}'"
expect "B's tunnel leaves with B's own SPI" \
       test "${B_OUT_SPI:-0}" -eq 572662306
expect "B's peer reaches B" test "${B_IN_DST:-}" = "100.64.0.6"
expect "B's inbound SPI is untouched" test "${B_IN_SPI:-0}" -eq 3149642683
expect "A's tunnel still reaches A with B's tunnel up" \
       test "${A_AGAIN_DST:-}" = "100.64.0.5"
expect "A's inbound SPI is still untouched" \
       test "${A_AGAIN_SPI:-0}" -eq 2863311530
expect "unsolicited ESP reaches nobody" test -z "${STRAY:-}"
expect "and is counted under its own name" test "${NO_TUNNEL:-0}" -ge 1
expect "a second subscriber to the same peer is refused, not guessed at" \
       test -z "${CLASH:-}"
expect "the refusal is counted under its own name" test "${TAKEN:-0}" -ge 1
expect "the first subscriber's tunnel is untouched by the refusal" \
       test "${A_SURVIVED:-}" = "100.64.0.5"
expect "tunnels bound is counted, so 'off' and 'saw nothing' differ" \
       test "${BOUND:-0}" -ge 2
expect "with passthrough off nothing leaves" test -z "${OFF_OUT:-}"
expect "not even on a tunnel that was already bound" test -z "${OFF_BOUND:-}"
expect "with passthrough off nothing comes back either" test -z "${OFF_IN:-}"
expect "and the drop says the switch is off, not that ESP is unsupported" \
       test "${OFF_CNT:-0}" -ge 3
verdict
