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
# design, so it ends with "HexaRouting ready." and the old needle matched. A
# live image that ever ends any other way would have had the gate refuse the
# release and name the wrong cause.
#
# So: one definition, and test/test_selftest.sh refuses a reader that spells
# these anywhere else.
#
# The order matters to nobody here, and the four are not equally good news:
# VERDICT_OK_RE is the three that mean the box is fit to be handed over, and
# the fourth is a box that failed its own checks.
VERDICT_RE="HexaRouting ready|HexaRouting is installed and healthy, and has|check\(s\) failed\. The box is NOT ready\."
VERDICT_OK_RE="HexaRouting ready|HexaRouting is installed and healthy, and has"

# Did the self-test reach a verdict at all, whatever it was?
said_a_verdict() {  # <file>
  grep -qE "$VERDICT_RE" "$1"
}

# And did it reach one that means the box can be handed over?
said_a_good_verdict() {  # <file>
  grep -qE "$VERDICT_OK_RE" "$1"
}
