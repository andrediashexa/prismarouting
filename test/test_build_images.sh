#!/bin/sh
# tools/build-images.sh's two readers, cut out and driven alone.
#
# `pin` decides what VERSION the images are built against, and `size` is what
# the measurement in docs/appliance.md is made of. Neither can be watched by
# running the tool: it builds two 200 MB images and needs docker, a network
# and four minutes, which is exactly what `make test` has not got. Cut out and
# sourced, the way test_release_digits.sh drives rows() and
# test_build_iso_preflight.sh drives say().
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }

echo "=== pin: the version comes out of appliance/build-iso.sh ==="
sed -n '/^pin() {/,/^}/p' "$REPO/tools/build-images.sh" > "$WORK/pin.sh"
[ -s "$WORK/pin.sh" ] || { echo "  FAIL: tools/build-images.sh has no pin()"; exit 1; }

# A fixture build-iso.sh, so this asserts what pin READS rather than what
# today's pin happens to be: a check that compares the tool against the real
# file passes whatever both of them say, including nothing.
mkdir -p "$WORK/appliance"
cat > "$WORK/appliance/build-iso.sh" <<'FIXTURE'
#!/bin/sh
SUITE=bookworm
VPP_VERSION=${VPP_VERSION:-99.01-release}
FRR_VERSION=${FRR_VERSION:-1.2.3-0~deb12u9}
FIXTURE

got=$(REPO=$WORK; . "$WORK/pin.sh"; pin VPP_VERSION)
[ "$got" = "99.01-release" ] \
  && ok "pin VPP_VERSION reads the medium's own line: $got" \
  || bad "pin gave '$got' where appliance/build-iso.sh says 99.01-release"

got=$(REPO=$WORK; . "$WORK/pin.sh"; pin FRR_VERSION)
[ "$got" = "1.2.3-0~deb12u9" ] \
  && ok "and FRR's, revision and all: $got" \
  || bad "pin gave '$got' where appliance/build-iso.sh says 1.2.3-0~deb12u9"

# THE EMPTY ANSWER, which is the one the tool refuses on. A build-iso.sh that
# stopped naming the version would otherwise build both images against
# whatever the repositories hold today - the drift the pins exist to stop -
# and the images would be wrong in a way nothing printed.
cat > "$WORK/appliance/build-iso.sh" <<'FIXTURE'
#!/bin/sh
SUITE=bookworm
FIXTURE
got=$(REPO=$WORK; . "$WORK/pin.sh"; pin VPP_VERSION)
[ -z "$got" ] \
  && ok "a build-iso.sh that names no version gives nothing, not a guess" \
  || bad "pin invented '$got' from a file that pins nothing"

# And the tool refuses on that empty answer rather than carrying on.
grep -q 'if \[ -z "\$VPP_VERSION" \] || \[ -z "\$FRR_VERSION" \]; then' \
     "$REPO/tools/build-images.sh" \
  && ok "and the tool refuses on it instead of building" \
  || bad "tools/build-images.sh no longer refuses an empty pin, so an image "\
"would be built against whatever apt resolves on the day"

echo
echo "=== size: the number the documented measurement is made of ==="
sed -n '/^size() {/,/^}/p' "$REPO/tools/build-images.sh" > "$WORK/size.sh"
[ -s "$WORK/size.sh" ] || { echo "  FAIL: tools/build-images.sh has no size()"; exit 1; }

# A docker on the PATH that answers a known number, so this measures what
# size() asks and what it does with the answer rather than needing an image.
mkdir -p "$WORK/bin"
cat > "$WORK/bin/docker" <<'STUB'
#!/bin/sh
# image inspect <tag> --format {{.Size}}
echo "$@" >> "$TRACE"
echo 211812352
STUB
chmod +x "$WORK/bin/docker"
TRACE=$WORK/trace; export TRACE
got=$(PATH="$WORK/bin:$PATH"; . "$WORK/size.sh"; size prisma-vpp:0.0.0)
[ "$got" = "211812352" ] \
  && ok "size returns the bytes docker reports: $got" \
  || bad "size gave '$got' where docker answered 211812352"

# THE BYTES, not a rendered string. The tool divides by 1048576 where it
# prints, and a size() that formatted here would give the documented table a
# second opinion about what a megabyte is.
case "$got" in
  *MB*|*mb*) bad "size formats its answer: $got" ;;
  *) ok "and it returns bytes, leaving the megabyte to the one printer" ;;
esac

grep -q 'image inspect' "$TRACE" \
  && ok "and it asks the image, not the container: $(head -1 "$TRACE")" \
  || bad "size asked docker '$(head -1 "$TRACE")'"

echo
if [ "$fail" = 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "$fail FAILURES"; exit 1
