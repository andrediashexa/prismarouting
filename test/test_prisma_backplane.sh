#!/bin/sh
# appliance/prisma-backplane against a stand-in `ip` (#447): what `up`
# builds, that a second `up` builds nothing twice, and each leak `check` must
# name. The network itself is measured in lab/backplane.sh, against a real
# VRF, a real VPP and a server on real 128 space; this is the script's logic,
# with no root.
#
# The stand-in keeps the state in files - links, the bridge's master, the
# main table, the VRF table - and answers the few `ip` calls the script
# makes from them. Mutations shown red, one at a time: the unreachable default
# left out of `up`; the master test removed from `check`; `up` adding the
# VRF without asking whether it exists.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fail=0
ok()  { echo "  [ ok ] $1"; }
bad() { echo "  FAIL: $1"; fail=$((fail + 1)); }
S=$T/state; mkdir -p "$S"; : > "$S/links"; : > "$S/main"; : > "$S/t128"; : > "$S/calls"
cat > "$T/ip" <<IPEOF
#!/bin/sh
S=$S
echo "\$*" >> "\$S/calls"
case "\$*" in
  "link show "*)   grep -qx "\$3" "\$S/links"; exit \$? ;;
  "-o link show master "*) n=9
                    for l in \$(cat "\$S/links"); do
                      [ "\$(cat "\$S/master-\$l" 2>/dev/null)" = "\$5" ] || continue
                      n=\$((n + 1)); echo "\$n: \$l@if\$n: <UP> mtu 1500 master \$5 state UP"
                    done ;;
  "-o link show "*) grep -qx "\$4" "\$S/links" || exit 1
                    m=\$(cat "\$S/master-\$4" 2>/dev/null)
                    echo "9: \$4: <UP> mtu 1500 \${m:+master \$m }state UP" ;;
  "link add "*)    echo "\$3" >> "\$S/links" ;;
  *"master "*)     echo "\$5" > "\$S/master-\$3" ;;
  "link del "*)    grep -vx "\$3" "\$S/links" > "\$S/l"; mv "\$S/l" "\$S/links" ;;
  "route show table main") cat "\$S/main" ;;
  "route show table 128")  cat "\$S/t128" ;;
  "route replace unreachable default"*) echo "unreachable default metric 4278198272" > "\$S/t128" ;;
  "route flush table 128") : > "\$S/t128" ;;
esac
exit 0
IPEOF
chmod +x "$T/ip"
BP() { PRISMA_IP="$T/ip" sh "$REPO/appliance/prisma-backplane" "$@"; }

echo "=== up builds the VRF, the bridge in it, the address and the fence ==="
BP up >/dev/null
grep -q "^link add prisma-bp type vrf table 128$" "$S/calls" && ok "the VRF, on table 128" \
  || bad "no VRF was added: $(cat "$S/calls")"
grep -q "^link set bp0 master prisma-bp$" "$S/calls" && ok "the bridge is put inside it" \
  || bad "the bridge was not put in the VRF"
grep -q "^addr replace 128.0.0.1/8 dev bp0$" "$S/calls" && ok "and the host is 128.0.0.1/8 on it" \
  || bad "the host address was not given"
grep -q "^unreachable default" "$S/t128" && ok "with an unreachable default in the VRF's table" \
  || bad "no unreachable default: a lookup the VRF cannot answer would fall into the main table"

echo "=== a second up adds nothing twice ==="
: > "$S/calls"
BP up >/dev/null; rc=$?
[ $rc -eq 0 ] && ! grep -q "^link add" "$S/calls" && ok "no link is added again, and it succeeds" \
  || bad "the second up added links again or failed ($rc): $(grep '^link add' "$S/calls")"

echo "=== check: clean, and each leak by name ==="
out=$(BP check); rc=$?
[ $rc -eq 0 ] && ok "a clean backplane passes: $out" || bad "a clean backplane failed: $out"
echo "128.0.0.0/8 dev bp0 proto kernel scope link src 128.0.0.1" > "$S/main"
out=$(BP check); rc=$?
[ $rc -ne 0 ] && echo "$out" | grep -q "LEAK: the main table routes through bp0" \
  && ok "a route through the bridge in the main table is a leak, named" || bad "main-table leak missed: $out"
: > "$S/main"; rm -f "$S/master-bp0"
out=$(BP check); rc=$?
[ $rc -ne 0 ] && echo "$out" | grep -q "LEAK: bp0 is not in prisma-bp" \
  && ok "a bridge outside the VRF is a leak, named" || bad "bridge outside the VRF missed: $out"
echo prisma-bp > "$S/master-bp0"; : > "$S/t128"
out=$(BP check); rc=$?
[ $rc -ne 0 ] && echo "$out" | grep -q "no unreachable default" \
  && ok "a VRF table with no fence is a leak, named" || bad "missing unreachable default missed: $out"
: > "$S/links"
out=$(BP check); rc=$?
[ $rc -ne 0 ] && echo "$out" | grep -q "the backplane is not up" \
  && ok "and no backplane at all is said as such" || bad "absent backplane not said: $out"

echo "=== attach sweeps a stale end off the bridge and the neighbour entry (#546) ==="
# The X520 bench, 0.149.3's upgrade: the old container's netns outlived it,
# its veth stayed on bp0 answering for 128.0.0.2, and the API's readiness
# check was refused by a dead container for ten minutes.
mkdir -p "$T/bin"
printf '#!/bin/sh\necho 4242\n' > "$T/bin/docker"
printf '#!/bin/sh\ncase "$*" in *"addr show"*) exit 1 ;; esac\nexit 0\n' > "$T/bin/nsenter"
chmod +x "$T/bin/docker" "$T/bin/nsenter"
BP up >/dev/null
echo bpm1111 >> "$S/links"; echo bp0 > "$S/master-bpm1111"
echo vpp-tap >> "$S/links"; echo bp0 > "$S/master-vpp-tap"
: > "$S/calls"
out=$(PATH="$T/bin:$PATH" BP attach prisma-routing_mgmt_1); rc=$?
[ $rc -eq 0 ] && ! grep -qx bpm1111 "$S/links" && echo "$out" | grep -q "removed bpm1111" \
  && ok "the stale bpm1111 is deleted, and said" || bad "the stale end stayed ($rc): $out / $(cat "$S/links")"
grep -qx vpp-tap "$S/links" && ok "a member that is not a bpm end is left alone" \
  || bad "attach deleted vpp-tap"
grep -qx bpm4242 "$S/links" && ok "the new container's end bpm4242 is made" \
  || bad "no bpm4242: $(cat "$S/links")"
grep -q "^neigh flush dev bp0 to 128.0.0.2$" "$S/calls" \
  && ok "the host forgets the old MAC of 128.0.0.2" || bad "no neighbour flush: $(cat "$S/calls")"
: > "$S/calls"
out=$(PATH="$T/bin:$PATH" BP attach prisma-routing_mgmt_1)
grep -qx bpm4242 "$S/links" && ok "attach again keeps the live end it would remake" \
  || bad "the live end was swept: $(cat "$S/links")"

echo "=== down takes it away ==="
BP up >/dev/null; BP down >/dev/null
! grep -qx "bp0" "$S/links" && ! grep -qx "prisma-bp" "$S/links" && ok "both links are gone" \
  || bad "down left: $(cat "$S/links")"
out=$(BP sideways 2>&1); rc=$?
[ $rc -eq 2 ] && echo "$out" | grep -q usage && ok "an unknown verb is a usage line, status 2" \
  || bad "an unknown verb: $rc $out"

[ $fail -eq 0 ] && { echo; echo "ALL TESTS PASSED"; exit 0; }
echo; echo "$fail FAILURES"; exit 1
