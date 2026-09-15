#!/bin/sh
# appliance/apply-tuning, driven against fake boxes.
#
# This script is what the appliance runs before VPP on every boot: it reads
# /etc/hexarouting/tune.env, turns it into arguments for hexarouting-tune,
# sets the cpufreq governor, binds the dataplane NIC and regenerates GRUB.
# Nothing ran it. test/test_kernel_tuning.py reads it as TEXT, looking for
# candidate knobs that must not be set, and appliance/test_tune.py tests the
# tuner it calls - so the one component that turns an operator's file into a
# kernel command line had never been executed by a test.
#
# What that hid, both measured before this suite existed:
#
#   HEXA_LOW_LATENCY=true produced "--no-low-latency". Any value that was not
#   exactly 1 took the "not 1" branch, so a typo meaning YES stripped the
#   C-state and transparent-hugepage parameters off the kernel command line
#   and left the governor alone - the opposite of what the operator wrote -
#   while the journal said "HEXA_LOW_LATENCY=0", naming a value nobody typed.
#
#   HEXA_SUBSCRIBERS=200k failed the whole unit. The tuner's argument parser
#   refused it with a usage dump that names neither the variable nor the file,
#   this script exited 1, and no tuning was written at all: the kernel command
#   line stayed at whatever the previous boot wrote, disagreeing with the
#   startup.conf beside it.
#
# So every value tune.env can carry is driven here, valid and invalid, and a
# refusal is asserted to say what the box DOES accept - a rejection that only
# says no leaves the operator guessing at the spelling on a console.
#
# The tuner and update-grub are stubs on PATH: what is asserted is the
# DECISION this script makes - which arguments it passes, what it writes, what
# it refuses and in which order it runs things - not the output of a tuner
# that appliance/test_tune.py already owns.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }

REPO=$(cd "$(dirname "$0")/.." && pwd)
BIN=$REPO/appliance/apply-tuning
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# The stubs. hexarouting-tune records its arguments and writes the GRUB file
# this script compares before and after, which is how "only regenerate on a
# change" is exercised at all.
STUB=$WORK/bin
mkdir -p "$STUB"
cat > "$STUB/hexarouting-tune" <<'EOF'
#!/bin/sh
echo "tune" >> "$REC/order"
echo "$*" > "$REC/argv"
[ "${STUB_TUNE_RC:-0}" = 0 ] || { echo "the tuner failed for its own reasons" >&2
                                  exit "$STUB_TUNE_RC"; }
mkdir -p "$REC/root/etc/default/grub.d"
printf '%s\n' "${STUB_GRUB:-GRUB_CMDLINE_LINUX_DEFAULT=\"x\"}" \
  > "$REC/root/etc/default/grub.d/hexarouting.cfg"
echo "# a line the script forwards to the journal"
EOF
cat > "$STUB/update-grub" <<'EOF'
#!/bin/sh
echo "update-grub" >> "$REC/order"
EOF
# Without a stub, "command -v hexarouting-bind-nic" would find whatever is
# installed on the machine running the tests, and an appliance running its own
# suite would bind a real NIC.
cat > "$STUB/hexarouting-bind-nic" <<'EOF'
#!/bin/sh
echo "bind" >> "$REC/order"
exit "${STUB_BIND_RC:-0}"
EOF
# systemd, answered FROM THE FAKE BOX. apply-tuning asks whether the dataplane
# is already running before it takes the card, and without this the answer
# would come from the machine running the suite: a developer box with a
# vpp.service would measure something different from the build runner, and a
# constant here would make one of the three branches unreachable, which is
# what test/test_selftest.sh has had to repair three times (#335).
#
# vpp_absent is how the case with no systemd at all opts out: apply-tuning
# treats "nobody can be asked" as its own answer, not as "not running".
cat > "$STUB/systemctl" <<'EOF'
#!/bin/sh
case "$*" in
  *"is-active"*"vpp.service"*)
    [ "$(cat "$REC/vpp_active" 2>/dev/null || echo no)" = yes ] ;;
  *) : ;;
esac
EOF
chmod +x "$STUB"/*

n=0
mkbox() {  # [governor] [available governors]
  n=$((n + 1))
  REC=$WORK/box$n
  R=$REC/root
  mkdir -p "$R/proc" "$R/etc/default/grub.d" "$R/etc/hexarouting" \
           "$R/sys/kernel/mm/hugepages/hugepages-1048576kB"
  echo "BOOT_IMAGE=/vmlinuz ro quiet" > "$R/proc/cmdline"
  echo 0 > "$R/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages"
  : > "$REC/order"
  : > "$REC/argv"
  if [ -n "${1:-}" ]; then
    P=$R/sys/devices/system/cpu/cpufreq/policy0
    mkdir -p "$P"
    echo "$1" > "$P/scaling_governor"
    echo "${2:-performance powersave}" > "$P/scaling_available_governors"
  fi
}

run() {  # VAR=value ... ; the box must already exist
  OUT=$(env PATH="$STUB:$PATH" REC="$REC" HEXA_ROOT="$R" "$@" sh "$BIN" 2>&1)
  RC=$?
}

argv()  { cat "$REC/argv"; }
order() { tr '\n' ' ' < "$REC/order" | sed 's/ *$//'; }
said()  { printf '%s\n' "$OUT" | grep -q -- "$1"; }
gov()   { cat "$R/sys/devices/system/cpu/cpufreq/policy0/scaling_governor"; }

echo "=== the tuner is run at all, and the suite can tell when it is not ==="
# First, because everything below reads $REC/argv: a stub that never ran
# leaves it empty, and an empty file must not read as agreement with anything.
mkbox
run HEXA_LOW_LATENCY=1
if [ -s "$REC/argv" ]; then
  ok "apply-tuning invoked hexarouting-tune: $(argv)"
else
  bad "hexarouting-tune was never invoked, so every argument check below is" \
      "comparing an empty file against an expectation and finding nothing" \
      "wrong. Output was: $OUT"
fi

echo
echo "=== HEXA_LOW_LATENCY, the switch the document promises ==="
# docs/appliance.md: "HEXA_LOW_LATENCY=0 in /etc/hexarouting/tune.env drops
# the C-state and THP parameters from the kernel command line and leaves the
# governor alone". test_kernel_tuning.py asserts --no-low-latency does that to
# the line. Nothing asserted that the operator's 0 becomes that flag.
mkbox powersave
run HEXA_LOW_LATENCY=1
case "$(argv)" in
  *--no-low-latency*) bad "HEXA_LOW_LATENCY=1 passed --no-low-latency: $(argv)" ;;
  *) ok "HEXA_LOW_LATENCY=1 keeps the low-latency parameters: $(argv)" ;;
esac
[ "$(gov)" = performance ] \
  && ok "and the cpufreq governor was set to performance" \
  || bad "HEXA_LOW_LATENCY=1 left the governor at '$(gov)': the governor is" \
         "runtime state and this script is the only thing that re-applies it"

mkbox powersave
run HEXA_LOW_LATENCY=0
case "$(argv)" in
  *--no-low-latency*) ok "HEXA_LOW_LATENCY=0 passes --no-low-latency: $(argv)" ;;
  *) bad "HEXA_LOW_LATENCY=0 did NOT pass --no-low-latency, so the kernel" \
         "command line keeps the C-state and THP parameters the operator" \
         "turned off: $(argv)" ;;
esac
[ "$(gov)" = powersave ] \
  && ok "and the governor was left alone, as the document says" \
  || bad "HEXA_LOW_LATENCY=0 still forced the governor to '$(gov)'"

echo
echo "=== a value tune.env should not carry is refused, and says what is ==="
# The regression this suite was written for. A refusal that only says no is
# read on a serial console by somebody who then has to guess the spelling.
mkbox powersave
run HEXA_LOW_LATENCY=true
said "REFUSED HEXA_LOW_LATENCY='true'" \
  && ok "HEXA_LOW_LATENCY=true is refused, and the refusal quotes the value" \
  || bad "HEXA_LOW_LATENCY=true passed silently. Output: $OUT"
if said "deep C-states off" && said "leave all three alone"; then
  ok "and the refusal says what the box accepts, both values, with what each does"
else
  bad "the refusal does not say what IS accepted, only that this was not:" \
      "$(printf '%s\n' "$OUT" | grep -i refused)"
fi
case "$(argv)" in
  *--no-low-latency*)
    bad "HEXA_LOW_LATENCY=true still stripped the C-state and THP parameters" \
        "off the kernel command line. A typo meaning YES must not turn the" \
        "tuning off: $(argv)" ;;
  *) ok "and the box keeps the shipped default rather than the opposite of" \
        "what the operator wrote: $(argv)" ;;
esac
[ "$RC" = 0 ] \
  && ok "and the unit does not fail: a typo must not cost the box its tuning" \
  || bad "apply-tuning exited $RC on a bad HEXA_LOW_LATENCY, so no tuning was" \
         "written and the kernel command line stays at the previous boot's"

echo
echo "=== HEXA_SUBSCRIBERS, which used to fail the whole unit ==="
mkbox
run HEXA_SUBSCRIBERS=200k
said "REFUSED HEXA_SUBSCRIBERS='200k'" \
  && ok "HEXA_SUBSCRIBERS=200k is refused by name, with the value quoted" \
  || bad "HEXA_SUBSCRIBERS=200k was not refused by this script. Output: $OUT"
said "digits only: 200000, not 200k" \
  && ok "and the refusal says what the box accepts and what it will not take" \
  || bad "the refusal does not name the accepted form"
case "$(argv)" in
  *"--subscribers 200000"*)
    ok "and the tuner still ran, on the shipped default: $(argv)" ;;
  *) bad "the tuner did not run on the default after the refusal: $(argv)" ;;
esac
[ "$RC" = 0 ] \
  && ok "and the unit exits 0 rather than leaving the box untuned" \
  || bad "apply-tuning exited $RC: the boot unit fails and nothing is written"

mkbox
run HEXA_SUBSCRIBERS=50000
case "$(argv)" in
  *"--subscribers 50000"*) ok "a number is passed through unchanged: $(argv)" ;;
  *) bad "HEXA_SUBSCRIBERS=50000 did not reach the tuner: $(argv)" ;;
esac

echo
echo "=== HEXA_MAX_WORKERS ==="
mkbox
run HEXA_MAX_WORKERS=4
case "$(argv)" in
  *"--max-workers 4"*) ok "a cap reaches the tuner: $(argv)" ;;
  *) bad "HEXA_MAX_WORKERS=4 did not reach the tuner: $(argv)" ;;
esac

mkbox
run HEXA_MAX_WORKERS=four
said "REFUSED HEXA_MAX_WORKERS='four'" \
  && ok "a cap that is not a number is refused by name" \
  || bad "HEXA_MAX_WORKERS=four was not refused. Output: $OUT"
said "empty, which is the shipped value and means no cap" \
  && ok "and the refusal names both accepted forms: digits, or empty" \
  || bad "the refusal does not say what a cap may be"
case "$(argv)" in
  *--max-workers*) bad "a refused cap was passed to the tuner anyway: $(argv)" ;;
  *) ok "and no cap is passed: the box keeps every core it had" ;;
esac

mkbox
run HEXA_MAX_WORKERS=
case "$(argv)" in
  *--max-workers*) bad "an empty cap invented one: $(argv)" ;;
  *) ok "an empty cap passes nothing, which is the shipped behaviour" ;;
esac

echo
echo "=== every line a refusal prints fits an 80-column serial console ==="
# This comes out of the journal onto the console of a racked box. A reason
# that wraps mid-value cannot be grepped for or typed back.
mkbox
run HEXA_LOW_LATENCY=maybe HEXA_SUBSCRIBERS=lots HEXA_MAX_WORKERS=some
wide=$(printf '%s\n' "$OUT" | awk 'length > 79 {print length": "$0}')
[ -z "$wide" ] \
  && ok "three refusals at once, widest line $(printf '%s\n' "$OUT" \
         | awk '{print length}' | sort -rn | head -1) columns" \
  || bad "lines over 79 columns:
$wide"

echo
echo "=== the governor is only written when the box has one to write ==="
# Writing a governor the kernel does not offer is a write that fails silently
# and a claim in the journal that it happened.
mkbox powersave "powersave schedutil"
run HEXA_LOW_LATENCY=1
[ "$(gov)" = powersave ] \
  && ok "a box that does not offer 'performance' is left alone: $(gov)" \
  || bad "wrote a governor the kernel does not offer: $(gov)"

mkbox
run HEXA_LOW_LATENCY=1
said "no cpufreq policy exposed" \
  && ok "and a box with no cpufreq at all says so instead of failing" \
  || bad "a box with no cpufreq did not say so: $OUT"

echo
echo "=== order: the tuner chooses the NIC, then the binder takes it ==="
# apply-tuning says it binds HERE rather than in a unit of its own, because
# this is the window between the tuner choosing the device and VPP starting.
# An order that silently inverts gives the binder a startup.conf from the
# previous boot.
mkbox
run HEXA_LOW_LATENCY=1
case "$(order)" in
  "tune bind"*) ok "the order is: $(order)" ;;
  *) bad "expected the tuner before the binder, got: $(order)" ;;
esac

echo
echo "=== and NOT while the dataplane is already running (#336) ==="
# THE COMMIT, not the boot. A `commit` that names the card restarts this unit
# too, and there the same two lines mean something else: the card comes off
# its kernel driver with VPP already up and not about to be restarted, so it
# belongs to neither stack. On a box whose only card carries the management
# address that is the operator losing the box - while the VTY prints, in the
# same breath, "NOT IN EFFECT YET: the dataplane claims its card when it
# starts, and this commit does not restart it".
#
# Measured on a booted guest before this existed: uio_pci_generic, netdev
# gone, three lines under that sentence.
mkbox
echo yes > "$REC/vpp_active"
run HEXA_LOW_LATENCY=1
case "$(order)" in
  *bind*) bad "the card was taken while the dataplane was already running, \
so it is on neither stack: $(order)" ;;
  *) ok "the card is left alone while the dataplane holds the box" ;;
esac
said "the dataplane is already running" \
  && ok "and it says why, rather than skipping in silence" \
  || bad "it skipped the bind and said nothing, which reads the same as a \
box that has no card to take: $OUT"
# The tuning still happens. A commit that changes the subscriber target has to
# regenerate startup.conf whatever the card is doing, and a skip that took the
# rest with it would make the card the price of every other setting.
case "$(order)" in
  tune*) ok "and the tuning still runs, which is what the commit asked for" ;;
  *) bad "skipping the bind took the tuning with it: $(order)" ;;
esac

echo
echo "=== and not where nothing can be asked, which is not a box booting ==="
# Three answers, not two. Without systemd this is a chroot during the install
# or a container, and a bind there takes a card off whichever machine is
# really running. Skipping one that should have happened leaves a box that
# forwards nothing and is reachable; performing one that should not leaves a
# box nobody can reach. This file already carries that trade, in #123's
# refusal, and it points the same way.
mkbox
run HEXA_SYSTEMCTL=/nonexistent-systemctl
case "$(order)" in
  *bind*) bad "a box where nothing could be asked bound the card anyway: \
$(order)" ;;
  *) ok "the card is left alone where the question cannot be put" ;;
esac
said "nothing here can say whether the dataplane is running" \
  && ok "and that is said too, because it is not the same box" \
  || bad "it skipped in silence, so 'not running' and 'cannot ask' read \
alike: $OUT"

mkbox
run STUB_BIND_RC=1
[ "$RC" = 0 ] \
  && ok "a binder that fails does not fail the tuning: the box still gets" \
        "its hugepages, its isolcpus and a console to read the reason on" \
  || bad "a failing binder took the whole tuning unit down with it (rc=$RC)"

echo
echo "=== a tuner that really fails still fails the unit ==="
# The refusals above must not have turned every error into a shrug.
mkbox
run STUB_TUNE_RC=3
[ "$RC" = 1 ] \
  && ok "the unit fails when the tuner does, and forwards its reason" \
  || bad "the tuner exited 3 and apply-tuning exited $RC"
said "the tuner failed for its own reasons" \
  && ok "and the reason reaches the journal rather than being swallowed" \
  || bad "the tuner's own error was not printed: $OUT"

echo
echo "=== update-grub runs on a change and not on a repeat ==="
mkbox
run STUB_GRUB="isolcpus=1-3"
case "$(order)" in
  *update-grub*) ok "a changed command line regenerates GRUB: $(order)" ;;
  *) bad "the GRUB file changed and update-grub did not run: $(order)" ;;
esac
said "reboot for it to take effect" \
  && ok "and the box says the change needs a reboot" \
  || bad "a changed kernel command line said nothing about a reboot: $OUT"

# Same box, same content: the second boot must not pay for update-grub.
: > "$REC/order"
run STUB_GRUB="isolcpus=1-3"
case "$(order)" in
  *update-grub*) bad "update-grub ran again on an unchanged file: $(order)" ;;
  *) ok "an unchanged command line does not run it again: $(order)" ;;
esac
said "tuning unchanged" \
  && ok "and says so" \
  || bad "an unchanged tuning said nothing: $OUT"

echo
echo "=== a live system does not run update-grub at all ==="
# GRUB is not installed to disk there: it would change nothing and can spend
# minutes scanning devices, inside a unit with a 90 second timeout.
mkbox
mkdir -p "$R/run/live/medium"
run STUB_GRUB="isolcpus=1-3"
case "$(order)" in
  *update-grub*) bad "update-grub ran on a live system: $(order)" ;;
  *) ok "skipped, and the box says the tuning applies once installed" ;;
esac
said "live system: skipping update-grub" \
  && ok "and it says why, rather than skipping in silence" \
  || bad "a live system skipped update-grub without saying why: $OUT"

if [ "$fail" -eq 0 ]; then
  echo
  echo "ALL TESTS PASSED"
  exit 0
fi
echo
echo "$fail FAILURES"
exit 1
