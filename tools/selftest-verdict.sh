# The sentences appliance/selftest can end with, written down ONCE.
#
# Sourced rather than copied, by everything that reads a verdict off a console:
# lab/boot-iso.sh, lab/install-iso.sh and tools/release-gate.sh. A shell
# fragment and not a function library, so a tool that is not a lab can take it
# without taking lab/lib.sh with it.
#
# WHY THIS FILE EXISTS. Until 0.72.0 the self-test had one verdict and every
# reader grepped for it by hand. #297 gave it four, because a box that is
# installed and healthy with no NIC to forward on is neither ready nor failed,
# and the readers were not updated together. The release gate then refused a
# correct medium twice: once on the ordering assertion in lab/install-iso.sh,
# and once on a second reader four hundred lines below it in the same file
# that decided whether the box had booted at all.
#
# Two more were still there after both fixes - lab/boot-iso.sh and the gate
# itself - and they went on passing for a reason worth writing down rather
# than being grateful for: a LIVE medium takes the branch where DPDK is off by
# design, so it ends with "Prisma Routing ready." and the old needle matched. A
# live image that ever ends any other way would have had the gate refuse the
# release and name the wrong cause.
#
# So: one definition, and test/test_selftest.sh refuses a reader that spells
# these anywhere else.
#
# AND THE REST OF THE GATE PATH WAS AUDITED ONCE, BY HAND, so that the next
# reader does not have to wonder whether more of these are waiting. Every
# literal string lab/install-iso.sh, lab/boot-iso.sh and tools/release-gate.sh
# look for in a console: 42 of them, once the regexes and the labs' own HXLAB
# probes are set aside. Nine do not appear literally anywhere under
# appliance/, src/ or tools/, and all nine are accounted for:
#
#   Welcome to GRUB / Login incorrect / Ordering cycle found
#                                      not ours: GRUB, login(1), systemd
#   Prisma Routing installed to /dev/vda
#                                      composed with the chosen disk
#   dataplane NIC: 0000:               composed, matched as a prefix
#   entering /bin                      composed, matched as a prefix
#   records mirrored on /dev/md0, 2 of 2 disks present
#                                      composed with the device and the counts
#   start shell: permission denied for a read-only account
#                                      composed by require(), "{what}: ..."
#   cgnat pool lab-a                   the lab's own configuration, read back
#
# So nothing else on this path had gone stale. The two refusals were not
# needles rotting in general: they were ONE contract read by four files, which
# is what this file exists to end.
#
# Recorded and NOT automated, deliberately. Nine findings and zero defects is
# a false rate somebody switches the check off over, and
# test/test_doc_copies.py's header already records the general version being
# measured and rejected three times at worse rates. A truth that has to agree
# across files gets one holder and a reader, as here. It does not get a grep
# that guesses.
#
# The order matters to nobody here, and the four are not equally good news:
# VERDICT_OK_RE is the three that mean the box is fit to be handed over, and
# the fourth is a box that failed its own checks.
# One line and over the margin, on purpose: a pattern split across two shell
# assignments is a pattern somebody edits half of.
VERDICT_RE="Prisma Routing ready|Prisma Routing is installed and healthy, and has|check\(s\) failed\. The box is NOT ready\."
VERDICT_OK_RE="Prisma Routing ready|Prisma Routing is installed and healthy, and has"

# Did the self-test reach a verdict at all, whatever it was?
said_a_verdict() {  # <file>
  grep -qE "$VERDICT_RE" "$1"
}

VERDICT_BAD_RE="check\(s\) failed\. The box is NOT ready\."

# And the third verdict on its own: installed, healthy, and not forwarding.
#
# A FOURTH CONSTANT BESIDE THREE, which is the shape this file already has -
# VERDICT_BAD_RE re-spells a sentence that is also inside VERDICT_RE, for the
# same reason. What must not happen is a reader spelling it, and the three
# readers are checked for exactly that by test/test_selftest.sh.
#
# WHY IT IS WANTED AS A READER AT ALL. Until #378 this sentence was good news:
# a box with no card named had none to forward on, and demanding any other
# verdict would have demanded the box lie about a NIC nobody gave it (#297).
# Now the box claims its own card, so on a machine that HAS a PCI NIC this
# sentence is the box saying it did not - which is the defect, not the state.
# It is still correct on a box with no NIC at all, so the caller asks what the
# box has before it asks what the box said.
VERDICT_NOFWD_RE="Prisma Routing is installed and healthy, and has"

# Did it end with the one that means it came up and claimed nothing?
said_a_no_forward_verdict() {  # <file>
  grep -qE "$VERDICT_NOFWD_RE" "$1"
}

# And did it reach one that means the box can be handed over?
#
# THIS WAS DEFINED AND CALLED BY NOBODY, for two releases (#311). Every reader
# asked said_a_verdict, which is true of a box that failed its own checks, so
# tools/release-gate.sh and lab/boot-iso.sh accepted a medium whose self-test
# ended "N check(s) failed. The box is NOT ready." and the gate reprinted that
# line underneath its own "is ready to publish". The gate's error text names
# the failure it was written for - "reached no verdict" - and the absence of a
# verdict is not the dangerous case.
said_a_good_verdict() {  # <file>
  grep -qE "$VERDICT_OK_RE" "$1"
}

# And did it reach the one that means it failed?
#
# Separate from "not good", because a console with no verdict at all is a
# different failure needing different work: a box that came up and failed its
# checks sends the reader to the checks, and a box that said nothing sends
# them to the bootloader. lab/install-iso.sh already told those two apart and
# was spelling this sentence by hand in two places to do it.
said_a_failed_verdict() {  # <file>
  grep -qE "$VERDICT_BAD_RE" "$1"
}
