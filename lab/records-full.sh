#!/bin/sh
# What the configuration trail does when the filesystem it lives on is full.
#
#   sh tools/lab.sh records-full
#
# The README claims two things about this, in its status table:
#
#   Configuration trail  | Attributable and tamper-evident: every entry names
#                          the operator and is chained by SHA-256
#   Connection records   | On their own filesystem, so a full log cannot stop
#                          the box working
#
# The ARRANGEMENT is well covered elsewhere: appliance/selftest checks the
# records are on their own filesystem and mirrored, lab/install-iso.sh checks
# the installer builds the mirror. The CONSEQUENCE is not covered anywhere, and
# a full records filesystem is not an exotic state: it is the ordinary end of a
# box under a one-year retention obligation.
#
# What is measured here is what the box does at that moment, and what an
# investigator reading the trail afterwards is told. `bpa_audit_verify()` opens
# with a paragraph about why its diagnosis must name the right line - a removed
# entry breaks the digest of the entry AFTER it, so a verifier that checks the
# hash first accuses a line nobody touched. The question this lab asks is
# whether the box produces that same state by itself (#236).
#
# No tmpfs trick stands in for the product's step. The box is pointed at a
# small filesystem with `bpa audit-path`, which is a command it ships, and
# everything after that is the box's own behaviour.
set -u

mkdir -p /run/vpp /vpp_plugins /var/log/hexarouting /etc/hexarouting
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do
  sleep 1; n=$((n+1))
done
vppctl show version >/dev/null 2>&1 || {
  echo "VPP did not start"; tail -20 /tmp/vpp.log; exit 1; }

# A filesystem of its own, small enough to fill on purpose. 64 KiB is the
# smallest tmpfs this kernel will round to something predictable, and a trail
# entry is a few hundred bytes, so there is room for the entries below and no
# room for a surprise.
SMALL=/records
mkdir -p $SMALL
mount -t tmpfs -o size=64k tmpfs $SMALL || { echo "cannot mount tmpfs"; exit 1; }
TRAIL=$SMALL/config-history.log
vppctl bpa audit-path $TRAIL
echo "  trail now at: $(vppctl show bpa pool history | head -1)"

free_bytes() { df --output=avail -B1 $SMALL | tail -1; }
entries()    { grep -c " seq " $TRAIL 2>/dev/null || echo 0; }
last_seq()   { sed -n 's/.* seq \([0-9]*\) sha256 .*/\1/p' $TRAIL | tail -1; }

echo
echo "=========== 1. the trail works, with room to write ==========="
vppctl bpa pool one inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl bpa pool two inside 100.64.4.0/22 outside 200.150.0.32/30 block 512
echo "  entries: $(entries), last seq: $(last_seq), free: $(free_bytes) bytes"
sed -n '1p' $TRAIL | cut -c1-100 | sed 's/^/    /'
GOOD_SEQ=$(last_seq)
GOOD_N=$(entries)

echo
echo "=========== 2. the filesystem fills ==========="
# Leave nothing at all: open() succeeds on an existing file, and write() is
# what fails. That is the shape the code has a branch for.
#
# And PROVE it is full rather than believe df. The first run of this lab padded
# the filesystem, read `0 bytes` out of df, and the trail entry was written
# anyway: tmpfs had room df was not reporting. Every assertion below would have
# been about a filesystem that was not full, and the lab would have reported
# the product as sound. A precondition that is assumed is a lab that measures
# nothing.
dd if=/dev/zero of=$SMALL/pad bs=1024 count=1024 2>/dev/null
i=0
while printf 'x' >> $SMALL/pad 2>/dev/null && [ "$i" -lt 200000 ]; do
  i=$((i+1))
done
echo "  free after padding: $(free_bytes) bytes, $i extra byte(s) accepted"
if printf 'x' >> $SMALL/probe 2>/dev/null; then
  echo "  the filesystem still accepts a write, so there is nothing to measure"
  echo "LAB NOT MEASURED: could not fill $SMALL"
  exit 2
fi
echo "  a one byte write to it now fails, which is the state under test"

echo
echo "=========== 3. a pool changes while there is nowhere to write it ==========="
vppctl bpa pool three inside 100.64.8.0/22 outside 200.150.0.48/30 block 512
FULL_N=$(entries)
echo "  entries in the file: $FULL_N (was $GOOD_N), last seq: $(last_seq)"
# MEASURED AND NOT WHAT WAS EXPECTED, so it is written down rather than tidied
# away. A full filesystem does not always lose the entry: appending a few
# hundred bytes to a file whose last page still has room needs no new block, so
# the write fits and the trail is complete. Whether a full disk costs you an
# entry depends on where the file happens to end, which is not a property
# anybody can reason about from outside.
if [ "$FULL_N" -gt "$GOOD_N" ]; then
  echo "  the entry was written anyway: it fitted in the page the file already had"
else
  echo "  the entry was lost, the file did not grow"
fi

echo
echo "=========== 4. the filesystem goes read-only, which is what a failing one does ==========="
# The deterministic trigger, and the realistic one. ext4 remounts itself
# read-only when it hits an error, which is the state a box with a dying
# records disk is in - and unlike a full filesystem it does not depend on page
# slack. open(O_WRONLY) fails, and the code has a branch for exactly that.
rm -f $SMALL/pad $SMALL/probe
mount -o remount,ro $SMALL || { echo "cannot remount read-only"; exit 1; }
BEFORE_RO_N=$(entries)
BEFORE_RO_SEQ=$(last_seq)
vppctl bpa pool five inside 100.64.16.0/22 outside 200.150.0.80/30 block 512
RO_N=$(entries)
echo "  entries: $RO_N (was $BEFORE_RO_N), last seq in the file: $(last_seq)"
echo "  does the pool exist in the dataplane?"
vppctl show bpa pool | grep -c "^pool five" | sed 's/^/    pool five rows: /'
echo "  what the box logged:"
grep -i "trail" /tmp/vpp.log | tail -3 | sed 's/^/    /'
echo "  what an operator asking the box would be told, with the disk still bad:"
RO_VERIFY=$(vppctl show bpa pool history verify)
printf '%s\n' "$RO_VERIFY" | sed 's/^/    /'

echo
echo "=========== 5. the disk comes back, and the box carries on ==========="
mount -o remount,rw $SMALL
vppctl bpa pool six inside 100.64.20.0/22 outside 200.150.0.96/30 block 512
AFTER_N=$(entries)
AFTER_SEQ=$(last_seq)
echo "  entries: $AFTER_N, last seq: $AFTER_SEQ"

echo
echo "=========== 6. the last bytes of the file ==========="
LASTBYTE=$(tail -c1 $TRAIL | od -c | head -1 | awk '{print $2}')
echo "  the file's last byte is: ${LASTBYTE:-<empty>}"
PARTIAL=no
[ "$LASTBYTE" = "\\n" ] || PARTIAL=yes
echo "  a line without its newline is present: $PARTIAL"

echo
echo "=========== 7. what an investigator is told about the whole file ==========="
VERIFY=$(vppctl show bpa pool history verify)
printf '%s\n' "$VERIFY" | sed 's/^/  /'

echo
echo "=========== 8. a file that was cut mid-entry, which is what a bad copy leaves ==========="
# The other half of the same question, and the box must tell it apart from the
# first. A fragment with no newline and no digest is what a write that stopped
# part way leaves behind - the state the fix above now prevents the box from
# creating, and the state a failing disk, a truncated restore or a partial copy
# still hands an investigator.
#
# Arranged by cutting the file, not by making the box write badly: the product
# is not missing a step here, the reader is being asked a question about a file
# it did not write.
cp $TRAIL $SMALL/whole.log
CUT=$(( $(wc -c < $TRAIL) - 40 ))
dd if=$SMALL/whole.log of=$TRAIL bs=1 count=$CUT 2>/dev/null
echo "  the file now ends: ...$(tail -c 30 $TRAIL)"
CUT_VERIFY=$(vppctl show bpa pool history verify)
printf '%s\n' "$CUT_VERIFY" | sed 's/^/    /'
cp $SMALL/whole.log $TRAIL

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh

# The precondition. Without it every assertion below is about a box that never
# recorded anything, and they would all pass on one.
expect "the trail recorded the pools written while there was room" \
  test "${GOOD_N:-0}" -ge 2

# The claim under test, and the one the README makes: the box keeps working.
# This is the half that holds today, and it is asserted so that a fix for the
# rest cannot quietly trade it away.
expect_in "the pool was created even though its trail entry could not be" \
  "pool five" "$(vppctl show bpa pool)"

# The precondition for everything below: the entry really was lost. Without it
# the assertions would be about a box that recorded everything, and they would
# be measuring the lab rather than the product.
expect "the read-only filesystem really cost an entry" \
  test "${RO_N:-0}" -eq "${BEFORE_RO_N:-0}"

# An entry that could not be written is a hole in the evidence, and the box is
# the only thing that knows it happened. Saying nothing is the one answer a
# compliance trail may not give.
expect_in "the box says an entry could not be written" \
  "could not be written" "$RO_VERIFY"

# And it must not call it something else. Nothing was removed: the box failed
# to write. "Removed from the middle" sends an investigator looking for
# somebody who edited the file, which is the misdiagnosis bpa_audit_verify's
# own comment says it was built to avoid.
expect_not_in "and does not report a write it never made as an entry somebody removed" \
  "removed from the middle" "$VERIFY"

# A fragment with no digest is indistinguishable, to both readers, from a line
# written before the chain existed. Taking a short write back is the only way
# the file stays a file of whole entries.
expect "a partly written entry is not left in the file" test "$PARTIAL" = no

# The chain over what IS in the file must stay verifiable. A gap makes every
# later entry read as tampering.
expect_in "the chain over what IS in the file still verifies" \
  "chain intact" "$VERIFY"

# A file cut mid-entry is a truncation and must be named one. Both readers used
# to look for " sha256 " on the line, find none, and count the fragment as an
# entry written before the chain existed: an investigator told the trail is OLD
# where it is in fact CUT.
expect_in "a file cut mid-entry is reported as cut" \
  "TRUNCATED: the file ends mid-entry" "$CUT_VERIFY"
# The needle is the REPORT's wording and not the phrase, because the report
# that names a cut file explains what it is not, in those words. The first
# version of this assertion matched the box's own explanation and went red on
# an answer that was right.
expect_not_in "and not as an entry that predates the chain" \
  "no digest: written before the chain existed" "$CUT_VERIFY"

verdict
