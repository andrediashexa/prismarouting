#!/bin/sh
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
# PPTP through the CGNAT.
#
# The claim under test is one sentence: two subscribers that choose the same
# Call ID both work. PPTP lets each end pick its own 16-bit Call ID, and
# Windows and most routers pick from the bottom of the range, so behind one
# public address the collision is not a corner case - it is the normal case.
# A design that only works for one subscriber at a time has reimplemented the
# problem it was meant to solve.
#
# Every assertion here reads the CAPTURED PACKET, not the trace. The trace
# prints what the node believed it wrote; an ALG's whole risk is a rewrite that
# lands two bytes off or leaves the checksum stale, and the trace would say
# "translated" for both.
#
# "sh tools/lab.sh pptp off" runs the whole thing with the ALG switched off.
# Every assertion about a rewrite must fail in that run: an assertion that
# passes with the feature removed was never testing the feature.
set -u
MODE=${1:-on}
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done
vppctl show version >/dev/null 2>&1 || { echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl set ip neighbor pg0 100.64.3.253 02:00:00:00:00:01
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl ip route add 100.64.0.0/22 via 100.64.3.253 pg0
for h in 5 6 7; do vppctl set ip neighbor pg0 100.64.0.$h 02:00:00:00:01:0$h; done

# "blocked" on purpose: it groups neighbouring subscribers onto the SAME public
# address, which is the only arrangement in which two subscribers can collide
# on a Call ID at all. Under the default interleaved policy they would land on
# different addresses and the test would pass without testing anything.
vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512 policy blocked
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1
[ "$MODE" = "off" ] && vppctl bpa alg lab pptp off >/dev/null

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
seen() { python3 /src/lab/checkpptp.py /tmp/cap.pcap 2>/dev/null; }
field() { seen | awk -v n="$1" 'NR==1 {print $n}'; }

echo "=========== 0. the ALG is on out of the box ==========="
ALG=$(vppctl show bpa alg)
echo "$ALG" | sed 's/^/  /'

echo
echo "=========== 1. subscriber A opens the control connection ==========="
arm pg1
send pg0 "$(python3 /src/lab/mkpptp.py syn 100.64.0.5 8.8.8.8 40000 1723)"
A_PUB=$(field 2); A_PORT=$(field 4)
echo "  100.64.0.5:40000 -> ${A_PUB}:${A_PORT}"

echo
echo "=========== 2. A asks for a call with Call ID 1 ==========="
arm pg1
send pg0 "$(python3 /src/lab/mkpptp.py ctrl 100.64.0.5 8.8.8.8 40000 1723 7 1)"
seen | sed 's/^/  /'
A_TYPE=$(field 6); A_CID=$(field 7); A_CK=$(field 9)
echo "  Call ID 1 left as ${A_CID}"

echo
echo "=========== 3. the server accepts, and A must see its own Call ID back ==="
arm pg0
send pg1 "$(python3 /src/lab/mkpptp.py ctrl 8.8.8.8 "$A_PUB" 1723 "$A_PORT" 8 0x9001 "${A_CID:-0}" 1)"
seen | sed 's/^/  /'
A_BACK=$(field 8); A_BACK_CK=$(field 9)

echo
echo "=========== 4. GRE from the server reaches A, carrying A's own Call ID ==="
arm pg0
send pg1 "$(python3 /src/lab/mkpptp.py gre 8.8.8.8 "$A_PUB" "${A_CID:-0}")"
seen | sed 's/^/  /'
A_GRE_DST=$(field 3); A_GRE_CID=$(field 5)

echo
echo "=========== 5. GRE from A keeps the server's Call ID untouched ==========="
# The Call ID going out is the one the SERVER assigned. Rewriting it would be
# the easy mistake and the server would discard every packet of the tunnel.
arm pg1
send pg0 "$(python3 /src/lab/mkpptp.py gre 100.64.0.5 8.8.8.8 0x9001)"
seen | sed 's/^/  /'
A_OUT_SRC=$(field 2); A_OUT_CID=$(field 5)

echo
echo "=========== 6. subscriber B picks the SAME Call ID 1 ==========="
arm pg1
send pg0 "$(python3 /src/lab/mkpptp.py syn 100.64.0.6 8.8.8.8 40000 1723)"
B_PUB=$(field 2); B_PORT=$(field 4)
arm pg1
send pg0 "$(python3 /src/lab/mkpptp.py ctrl 100.64.0.6 8.8.8.8 40000 1723 7 1)"
seen | sed 's/^/  /'
B_CID=$(field 7)
echo "  A is ${A_PUB} with Call ID ${A_CID}; B is ${B_PUB} with Call ID ${B_CID}"

arm pg0
send pg1 "$(python3 /src/lab/mkpptp.py ctrl 8.8.8.8 "$B_PUB" 1723 "$B_PORT" 8 0x9002 "${B_CID:-0}" 1)"
seen | sed 's/^/  /'

echo
echo "=========== 7. both tunnels carry traffic to the right subscriber ==========="
arm pg0
send pg1 "$(python3 /src/lab/mkpptp.py gre 8.8.8.8 "$B_PUB" "${B_CID:-0}")"
seen | sed 's/^/  /'
B_GRE_DST=$(field 3); B_GRE_CID=$(field 5)

arm pg0
send pg1 "$(python3 /src/lab/mkpptp.py gre 8.8.8.8 "$A_PUB" "${A_CID:-0}")"
seen | sed 's/^/  /'
A_AGAIN_DST=$(field 3); A_AGAIN_CID=$(field 5)

echo
echo "=========== 8. PMTUD: an ICMP error about A's outbound tunnel ==========="
# The only signal a PPTP tunnel has. There is no MSS inside a GRE packet to
# clamp, so when a router on the path cannot carry the subscriber's packet this
# error is the whole of what tells them - and a CGNAT that drops it is the
# classic PMTUD black hole: the tunnel authenticates, small packets work, and
# the first large transfer hangs with nothing anywhere saying why (#203).
#
# The Call ID quoted here is the SERVER's, 0x9001, because that is what an
# OUTBOUND GRE packet carries - and it is the one field no per-worker table is
# keyed on. It resolves through the shared binding written when the call
# established.
arm pg0
send pg1 "$(python3 /src/lab/mkicmperr.py 4.4.4.4 "${A_PUB:-200.150.0.17}" \
            8.8.8.8 0 0 gre 36865)"
PMTUD=$(seen)
printf '%s\n' "${PMTUD:-  <nothing reached the subscriber>}" | sed 's/^/  /'
PMTUD_DST=$(field 3); PMTUD_INNER=$(field 4); PMTUD_CID=$(field 6)
PMTUD_CK=$(field 7); PMTUD_ICK=$(field 8)
echo "  reached ${PMTUD_DST:-nobody}, embedded source now ${PMTUD_INNER:-?}"

# And one about a call nobody has. Without this the assertion above passes on a
# box that delivers every ICMP error it cannot resolve to whoever owns the
# public address, which is a different product and a worse one.
arm pg0
send pg1 "$(python3 /src/lab/mkicmperr.py 4.4.4.4 "${A_PUB:-200.150.0.17}" \
            8.8.8.8 0 0 gre 4660)"
PMTUD_STRAY=$(seen)
echo "  an error about Call ID 4660, which no call has: ${PMTUD_STRAY:-nothing arrived}"
echo

echo "=========== 9. plain GRE is refused, and says so ==========="
# RFC 2784 GRE carries no per-flow identifier, so several subscribers behind
# one address cannot be told apart. Dropping it is right; dropping it under
# the counter for a truncated packet would send the diagnosis somewhere else.
send pg0 "$(python3 /src/lab/mkpptp.py gre 100.64.0.5 8.8.8.8 0x0001 0)"
PLAIN=$(vppctl show errors | grep -c "not the enhanced GRE")
vppctl show errors | grep "enhanced GRE" | sed 's/^/  /'

echo
echo "=========== 10. a refused call does not keep its Call ID ==========="
# A subscriber retrying against a server that keeps saying no would otherwise
# spend one entry of its block per attempt, and the block is 512 entries.
arm pg1
send pg0 "$(python3 /src/lab/mkpptp.py syn 100.64.0.7 8.8.8.8 40000 1723)"
C_PUB=$(field 2); C_PORT=$(field 4)
arm pg1
send pg0 "$(python3 /src/lab/mkpptp.py ctrl 100.64.0.7 8.8.8.8 40000 1723 7 1)"
C_CID=$(field 7)
# Before the refusal the mapping exists, so this GRE arrives. Without this
# "before", the assertion after the refusal would pass on a box where the
# mapping was never created at all - which is a different bug wearing the same
# result.
arm pg0
send pg1 "$(python3 /src/lab/mkpptp.py gre 8.8.8.8 "$C_PUB" "${C_CID:-0}")"
C_BEFORE=$(seen)
echo "  GRE for the pending call: ${C_BEFORE:-nothing arrived}"
arm pg0
send pg1 "$(python3 /src/lab/mkpptp.py ctrl 8.8.8.8 "$C_PUB" 1723 "$C_PORT" 8 0x9003 "${C_CID:-0}" 2)"
seen | sed 's/^/  /'
C_BACK=$(field 8)
arm pg0
send pg1 "$(python3 /src/lab/mkpptp.py gre 8.8.8.8 "$C_PUB" "${C_CID:-0}")"
C_AFTER=$(seen)
echo "  GRE for the refused call afterwards: ${C_AFTER:-nothing arrived}"

echo
echo "=========== 11. a disconnected call stops carrying traffic ==========="
# Step 7 already proved this same packet reaches A. If it still does after the
# disconnect, nothing was torn down - and the assertion below would otherwise
# pass by there being nothing to capture in the first place.
arm pg0
send pg1 "$(python3 /src/lab/mkpptp.py ctrl 8.8.8.8 "$A_PUB" 1723 "$A_PORT" 13 "${A_CID:-0}")"
seen | sed 's/^/  /'
A_DISC=$(field 7)
arm pg0
send pg1 "$(python3 /src/lab/mkpptp.py gre 8.8.8.8 "$A_PUB" "${A_CID:-0}")"
A_GONE=$(seen)
echo "  GRE for the disconnected call afterwards: ${A_GONE:-nothing arrived}"
# And the shared binding the ICMP path reads, which is a SECOND table entry
# written when the call established. Section 8 proved this exact packet reaches
# A, so an empty capture here is the teardown and not the packet: a binding
# left behind would keep sending errors about a call that ended to a
# subscriber who is no longer in it.
arm pg0
send pg1 "$(python3 /src/lab/mkicmperr.py 4.4.4.4 "${A_PUB:-200.150.0.17}" \
            8.8.8.8 0 0 gre 36865)"
PMTUD_GONE=$(seen)
echo "  and an ICMP error about it: ${PMTUD_GONE:-nothing arrived}"

echo
echo "=========== 12. switched off, the ALG rewrites nothing ==========="
vppctl bpa alg lab pptp off | sed 's/^/  /'
arm pg1
send pg0 "$(python3 /src/lab/mkpptp.py syn 100.64.0.6 8.8.8.8 41000 1723)"
arm pg1
send pg0 "$(python3 /src/lab/mkpptp.py ctrl 100.64.0.6 8.8.8.8 41000 1723 7 33)"
seen | sed 's/^/  /'
OFF_CID=$(field 7)
[ "$MODE" = "off" ] || vppctl bpa alg lab pptp on >/dev/null

echo
echo "=========== VERDICT ==========="
# The STATE, not the row label. `show bpa alg` prints one row per ALG whether
# it is on or off, so matching "pptp" passed with the ALG disabled - including
# in the BPA_PPTP_OFF run this lab exists to fail. lab/vty-pool.sh already
# asserts "ftp    off" the right way, in this same repository.
expect "the PPTP ALG is enabled by default" \
  sh -c "printf '%s' \"$ALG\" | tr -s ' ' ' ' | grep -q 'pptp on'"
expect "the control connection was translated" test -n "${A_PORT:-}"
# A numeric field compared as a number. Matched as a substring, "7" also
# accepted 17, 27 and 70; every other assertion in this file uses -eq.
expect "the call request is still a PPTP Outgoing-Call-Request" \
  test "${A_TYPE:-0}" -eq 7
expect "the subscriber's Call ID was replaced" test "${A_CID:-1}" -ne 1
expect "the rewritten call request still checksums" test "${A_CK:-BAD}" = "ok"
expect "the reply hands the subscriber back its own Call ID" \
       test "${A_BACK:-0}" -eq 1
expect "the rewritten reply still checksums" test "${A_BACK_CK:-BAD}" = "ok"
expect "inbound GRE reaches subscriber A" test "${A_GRE_DST:-}" = "100.64.0.5"
expect "inbound GRE carries A's own Call ID" test "${A_GRE_CID:-0}" -eq 1
expect "outbound GRE leaves with the public source" \
       test "${A_OUT_SRC:-}" = "${A_PUB:-x}"
expect "outbound GRE keeps the server's Call ID" \
       test "${A_OUT_CID:-0}" -eq 36865
# The test is only a test if both subscribers really share one public address.
expect "A and B share a public address, so they really can collide" \
       test "${A_PUB:-x}" = "${B_PUB:-y}"
expect "two subscribers asking for Call ID 1 got different Call IDs" \
       test "${A_CID:-0}" -ne "${B_CID:-0}"
expect "B's tunnel reaches B" test "${B_GRE_DST:-}" = "100.64.0.6"
expect "B sees its own Call ID 1" test "${B_GRE_CID:-0}" -eq 1
expect "A's tunnel still reaches A afterwards" \
       test "${A_AGAIN_DST:-}" = "100.64.0.5"
expect "A still sees its own Call ID 1" test "${A_AGAIN_CID:-0}" -eq 1
expect "plain GRE is counted under its own name" test "${PLAIN:-0}" -ge 1
expect "a refused call still hands the subscriber back its own Call ID" \
       test "${C_BACK:-0}" -eq 1
expect "the pending call carried traffic before it was refused" \
       test -n "${C_BEFORE:-}"
expect "a refused call carries nothing afterwards" test -z "${C_AFTER:-}"
expect "the disconnect reaches the subscriber as its own Call ID" \
       test "${A_DISC:-0}" -eq 1
expect "a disconnected call carries nothing afterwards" test -z "${A_GONE:-}"
expect "with the ALG off the Call ID is left alone" test "${OFF_CID:-0}" -eq 33
# PMTUD, asserted on the captured packet. The error has to arrive at the
# subscriber's PRIVATE address, or it reaches a host that does not exist.
expect "an ICMP error about the tunnel reaches the subscriber" \
       test "${PMTUD_DST:-}" = "100.64.0.5"
# The half that decides whether it is worth anything: the subscriber's stack
# matches the error against its own socket by the packet quoted INSIDE it, so
# an error delivered to the right host still tells it nothing if the embedded
# header still names the public address.
expect "and the embedded packet names the subscriber, not the public address" \
       test "${PMTUD_INNER:-}" = "100.64.0.5"
# The Call ID must be UNCHANGED. It is the server's, which this box never
# translated - the one it did translate is the subscriber's own, and that one
# is not in an outbound packet at all. Without this a box that rewrote it
# would pass every other assertion here and hand the subscriber's stack a
# packet it cannot match against its own call.
expect "and the embedded Call ID is the server's, untouched" \
       test "${PMTUD_CID:-0}" -eq 36865
expect "the outer IP checksum still covers what was rewritten" \
       test "${PMTUD_CK:-BAD}" = "ok"
# The ICMP checksum covers the whole embedded packet. Rewriting an address in
# there without it is a packet the subscriber's kernel drops in silence.
expect "and so does the ICMP checksum, which covers the embedded packet" \
       test "${PMTUD_ICK:-BAD}" = "ok"
# The negative half. Without it the delivery above passes on a box that hands
# every unresolvable ICMP error to whoever owns the public address.
expect "an error about a Call ID no call has reaches nobody" \
       test -z "${PMTUD_STRAY:-}"
expect "and the binding goes when the call is disconnected" \
       test -z "${PMTUD_GONE:-}"
verdict
