#!/bin/sh
# Dated configuration trail: the "proof" behind deterministic mode.
#
#   sh tools/lab.sh audit        with the identity travelling
#   sh tools/lab.sh audit off    without it - the identity checks MUST fail
#
# The trail said WHAT changed and when, and could not say WHO (#34). The half
# that is easy to get wrong is the negative one: an entry that could not be
# attributed has to SAY so, because an entry that silently omits the field is
# indistinguishable from one where nobody was accountable.
set -u
MODE=${1:-on}
mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting /etc/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done

echo "=== initial state ==="
vppctl show bpa pool history | sed 's/^/  /'

echo
echo "=== operacoes de configuracao ==="
vppctl bpa pool provider-a inside 100.64.0.0/22 outside 200.150.0.0/24 outside 45.170.16.0/26 block 512 >/dev/null
echo "  created provider-a"
sleep 1
vppctl bpa pool provider-b inside 10.50.0.0/16 outside 177.85.32.0/24 block 2048 policy blocked >/dev/null
echo "  created provider-b"
sleep 1
vppctl bpa pool provider-a del
echo "  removido provider-a"
sleep 1
vppctl bpa pool provider-a inside 100.64.0.0/22 outside 200.150.0.0/24 block 1024 >/dev/null
echo "  recreated provider-a with a different block"

echo
echo "=== a change through the VTY, which knows who is asking ==="
# VPP runs as root. If the plugin recorded its own process, every line would
# say uid 0 - so the lab drives the VTY as somebody who is NOT root and
# demands that name on the line. That is the assertion that separates "the
# identity travelled with the request" from "the box wrote down whoever it
# happened to be running as".
. /src/lab/lib.sh
use_binary_api
HX="python3 /src/src/hexash/hexash.py"
if [ "$MODE" = off ]; then
  echo "  MODE=off: the VTY sends no identity. Every check below that names an"
  echo "  operator MUST fail."
  HX_ENV=""
else
  HX_ENV="SUDO_USER=marina"
fi
env $HX_ENV $HX -c "configure" -c "cgnat pool provider-b" \
    -c "max-sessions 4000" -c "exit" -c "commit" 2>&1 | tail -2 | sed 's/^/  /'
VTY_LINE=$(grep "limits pool provider-b" /var/log/hexarouting/config-history.log | tail -1)
printf '%s\n' "$VTY_LINE" | sed 's/^/  /'

echo
echo "=== and one typed at the VPP CLI, which does not ==="
# There is no session to ask, so there is no operator to name. The line has to
# say that rather than leave the field off.
vppctl bpa limits provider-b max-sessions 5000 >/dev/null
CLI_LINE=$(grep "limits pool provider-b" /var/log/hexarouting/config-history.log | tail -1)
printf '%s\n' "$CLI_LINE" | sed 's/^/  /'

echo
echo "=== an API client that sends no identity ==="
# Not the same situation as the CLI, and not recorded as if it were: the
# request came through the API and carried nothing.
# Output shown, not discarded: a lab that hides the result of its own command
# reports "no trail entry" for a call that never ran, which is what the first
# version of this section did.
# Empty name, uid zero - which is what a client that fills in nothing sends,
# because vat2 requires every field to be present. Zero is a REAL uid: root.
# Recording this as root would be false attribution, so it must come out
# unattributed.
vat2 bpa_limits_set \
  '{"name":"provider-b","max_sessions":6000,"max_rate":0,"audit_user":"","audit_uid":0}' \
  2>&1 | sed 's/^/  /'
BARE_LINE=$(grep "limits pool provider-b" /var/log/hexarouting/config-history.log | tail -1)
printf '%s\n' "$BARE_LINE" | sed 's/^/  /'

echo
echo "=== attaching CGNAT to an interface (#124) ==="
# The pools say HOW to translate; the interfaces say WHERE. Detaching out2in
# stops translation for every subscriber behind that interface, which is the
# same consequence as deleting the pool - so it is the same kind of entry.
# Until #124 the attach went through the binary API with no identity on the
# message at all, and the trail did not mention it: the box could stop
# translating with nobody's name against the change.
LOG=/var/log/hexarouting/config-history.log
vppctl create loopback interface >/dev/null
echo "  the trail before the attach:"
echo "    interface entries: $(grep -c 'interface loop0' $LOG)"
MARK=$(wc -l < $LOG)
env $HX_ENV $HX -c "configure" -c "cgnat interface loop0 inside" \
    -c "commit" 2>&1 | tail -1 | sed 's/^/  /'
env $HX_ENV $HX -c "configure" -c "no cgnat interface loop0" \
    -c "commit" 2>&1 | tail -1 | sed 's/^/  /'
IF_NEW=$(tail -n +$((MARK + 1)) $LOG)
echo "  the trail after the attach and the detach:"
printf '%s\n' "$IF_NEW" | sed 's/^/    /'
ATTACH_LINE=$(printf '%s\n' "$IF_NEW" | grep " attach interface loop0" | tail -1)
DETACH_LINE=$(printf '%s\n' "$IF_NEW" | grep " detach interface loop0" | tail -1)

echo
echo "=== and one attached at the VPP CLI, which names nobody ==="
# The same third shape the pool entries have: there is no session to ask, so
# the entry says unattributed rather than leaving the change out of the file.
MARK2=$(wc -l < $LOG)
vppctl set interface bpa out2in loop0
CLI_IF_LINE=$(tail -n +$((MARK2 + 1)) $LOG | grep " attach interface loop0" | tail -1)
printf '%s\n' "$CLI_IF_LINE" | sed 's/^/  /'
vppctl set interface bpa out2in loop0 del

echo
echo "=========== A TRILHA ==========="
vppctl show bpa pool history | sed 's/^/  /'

echo
echo "=== the file on disk (append-only, with fsync) ==="
ls -l /var/log/hexarouting/config-history.log | sed 's/^/  /'
echo
echo "=== the question the trail answers ==="
echo "  'what block did provider-a use before the last change?'"
grep "add pool provider-a" /var/log/hexarouting/config-history.log \
  | head -1 | grep -oE "block [0-9]+" | sed 's/^/    /'
echo "  'e agora?'"
grep "add pool provider-a" /var/log/hexarouting/config-history.log \
  | tail -1 | grep -oE "block [0-9]+" | sed 's/^/    /'
echo
echo "  Without the trail, the current formula would answer with block 1024 for"
echo "  a date when the pool still used 512 - naming the wrong subscriber."

echo
echo "=== it also went to VPP's log (which can ship to remote syslog) ==="
# The pools here are provider-a and provider-b; "provedor" never matched, so
# this diagnostic reported 0 on every run since it was written.
vppctl show logging | grep -c "pool provider" | sed 's/^/  lines: /'

echo
echo "=========== the chain (#22) ==========="
TRAIL=/var/log/hexarouting/config-history.log
CLEAN=$(vppctl show bpa pool history verify)
printf '%s\n' "$CLEAN" | sed 's/^/  /'

# Every check below runs against a COPY, with audit-path pointed at it: a lab
# that corrupts the trail it is testing has nothing left to test.
echo
echo "=== one byte changed in the middle ==="
cp $TRAIL /tmp/altered.log
# The CONTENT, not the digest: an attacker who edits the record fixes the
# record, not the checksum they did not know was there.
sed -i '2s/block 2048/block 9999/' /tmp/altered.log
vppctl bpa audit-path /tmp/altered.log >/dev/null
ALTERED=$(vppctl show bpa pool history verify)
printf '%s\n' "$ALTERED" | sed 's/^/  /'

echo
echo "=== an entry removed from the middle ==="
cp $TRAIL /tmp/removed.log
sed -i '3d' /tmp/removed.log
vppctl bpa audit-path /tmp/removed.log >/dev/null
REMOVED=$(vppctl show bpa pool history verify)
printf '%s\n' "$REMOVED" | sed 's/^/  /'

echo
echo "=== entries removed from the END ==="
# The case a file cannot report about itself: every remaining link is genuine,
# so the chain verifies. Only something that remembers what it wrote can tell.
cp $TRAIL /tmp/truncated.log
sed -i '$d' /tmp/truncated.log
sed -i '$d' /tmp/truncated.log
vppctl bpa audit-path /tmp/truncated.log >/dev/null
TRUNCATED=$(vppctl show bpa pool history verify)
printf '%s\n' "$TRUNCATED" | sed 's/^/  /'

echo
echo "=== a trail written before the chain existed ==="
printf '2026-01-01T00:00:00Z add pool old inside 10.0.0.0+256 block 512\n' \
  > /tmp/legacy.log
printf '2026-01-02T00:00:00Z del pool old inside 10.0.0.0+256 block 512\n' \
  >> /tmp/legacy.log
vppctl bpa audit-path /tmp/legacy.log >/dev/null
LEGACY=$(vppctl show bpa pool history verify)
printf '%s\n' "$LEGACY" | sed 's/^/  /'
LEGACY_READ=$(vppctl show bpa pool history)
vppctl bpa audit-path $TRAIL >/dev/null

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
H=$(vppctl show bpa pool history)
expect_in "an add was recorded" "add pool provider-a" "$H"
expect_in "a del was recorded" "del pool provider-a" "$H"
expect_in "entries are dated in UTC" "Z add" "$H"
expect "the trail survives on disk" test -s /var/log/hexarouting/config-history.log

# --- who changed it (#34) ---
#
# The identity of the CALLER, not of the VPP process. VPP is root here, so a
# line naming marina can only have come from the request.
expect_in "a change through the VTY names the operator" "by marina" "$VTY_LINE"
expect_not_in "and not the account the dataplane runs as" "by root" "$VTY_LINE"
expect_in "and says it arrived over the API" "via api" "$VTY_LINE"

# The negative half, and the one that matters. Silence here would read exactly
# like a trail written before any of this existed.
expect_in "a change with no identity says so, rather than omitting it" \
  "by unattributed" "$CLI_LINE"
expect_in "and says it did not come through the API" "via cli" "$CLI_LINE"
expect_not_in "an unattributed change is not credited to root" "by uid 0" \
  "$CLI_LINE"

expect_in "an API client that sends nothing is unattributed too" \
  "by unattributed" "$BARE_LINE"
expect_in "but is not confused with a change typed at the console" "via api" \
  "$BARE_LINE"

# --- where the pools are applied (#124) ---
#
# A pool that translates nothing because its interface was detached answers a
# court order with a mapping that was not in force. The trail recorded every
# pool change and none of these, so the record could be complete about the
# definition and silent about whether it was doing anything.
expect_in "attaching CGNAT to an interface is recorded" \
  "attach interface loop0" "$IF_NEW"
expect_in "and so is detaching it, which is the change that stops translation" \
  "detach interface loop0" "$IF_NEW"
expect_in "an attach through the VTY names the operator" "by marina" \
  "$ATTACH_LINE"
expect_not_in "and not the account the dataplane runs as" "by root" \
  "$ATTACH_LINE"
expect_in "and says it arrived over the API" "via api" "$ATTACH_LINE"
expect_in "a detach names the operator too" "by marina" "$DETACH_LINE"
# Which half of the translation was attached is not derivable from the rest of
# the entry, so it is on the line: "attach interface loop0" alone leaves the
# reader unable to say what stopped working.
expect_in "the entry says which direction it was" "direction inside" \
  "$ATTACH_LINE"
expect_in "an attach typed at the VPP CLI says nobody can be named" \
  "by unattributed via cli" "$CLI_IF_LINE"
# The identity sits in the same place as on a pool entry, before the
# open-ended part. An entry whose fields are ordered differently from its
# neighbours cannot be compared with them, and comparing them is what a reader
# of this file does.
expect_in "and carries the same fields in the same order as a pool entry" \
  "attach interface loop0 by unattributed via cli direction outside" \
  "$CLI_IF_LINE"

# Entries written before any of this existed still read back.
expect_in "older entries without an identity are still listed" \
  "add pool provider-b" "$H"

# --- the chain (#22) ---
expect_in "a clean trail verifies" "chain intact" "$CLEAN"
expect_in "and reports the head, which is what a remote copy is compared to" \
  "head  seq" "$CLEAN"
# A chain that verifies proves the file was not edited. It does not prove it is
# the same file, and saying so is the difference between a proof and a claim.
expect_in "and says what that does not prove" "not that it is the same file" \
  "$CLEAN"

expect_in "an altered entry is detected" "BROKEN" "$ALTERED"
expect_in "and named, rather than reported as 'somewhere'" \
  "does not match its own digest" "$ALTERED"
expect_in "and the altered text is shown" "9999" "$ALTERED"
expect_not_in "an altered trail is not reported as intact" "chain intact" \
  "$ALTERED"

expect_in "an entry removed from the middle is detected" "BROKEN" "$REMOVED"
expect_in "and diagnosed as a removal, not as an alteration" \
  "removed from the middle" "$REMOVED"

# The three cases have to be TOLD APART. A verifier that says "broken" for all
# of them leaves the operator to guess what happened to their evidence.
expect_in "truncation is detected" "TRUNCATED" "$TRUNCATED"
expect_in "and diagnosed as a removal from the end" "removed from the end" \
  "$TRUNCATED"
expect_not_in "and not confused with an alteration" "does not match its own" \
  "$TRUNCATED"

expect_in "a pre-chain trail says it is not covered" "no digest" "$LEGACY"
expect_in "and is still readable" "add pool old" "$LEGACY_READ"
verdict
