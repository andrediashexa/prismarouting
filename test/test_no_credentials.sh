#!/bin/sh
# The rule that nothing secret leaves the building inside the ISO.
#
# docs/appliance.md:1086 - "No answer file is shipped in the ISO, and the build
# MUST NOT put one there. A credential in a published image is the defect the
# locked live root account exists to prevent."
#
# Nothing held it. Writing an answer file into the root build-iso.sh was about
# to squash left all 34 suites green (#245).
#
# THE SPLIT, AND WHY IT IS THIS ONE
#
# Reading a real ISO means building one: root, a privileged container and ten
# minutes. So the refusal lives in appliance/no-credentials-in-image, which is
# a shell script over a directory, and this suite drives THAT script - the one
# the build calls, not a copy of its logic - against fake roots in
# milliseconds. It is the same argument test/test_installer.sh makes for
# PRISMA_INSTALL_CHECK.
#
# A fast check on its own is not enough, and the failure it would have is
# specific: it stays green on a build that ships the file anyway, because it
# never looks at what the build produced. Two things close that gap:
#
#   - this suite asserts that build-iso.sh CALLS the script, at both seams,
#     and before the two commands that make the result unreadable (mksquashfs
#     and grub-mkrescue). A refusal nothing invokes is a refusal that does not
#     exist.
#   - lab/install-iso.sh runs the same script over the squashfs it just
#     unpacked out of the image that was actually built. That is the assertion
#     on the artefact, and it is excluded from the suite (NOT_LABS) for the
#     same reason this file exists.
#
# What is NOT asserted here: that a real image is clean. That claim needs a
# real image, and this suite runs on machines that have never built one.
REPO=$(cd "$(dirname "$0")/.." && pwd)
CHECK=$REPO/appliance/no-credentials-in-image
fail=0

ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail + 1)); }

TMP=$(mktemp -d) || exit 1
trap 'rm -rf "$TMP"' EXIT INT TERM

# A root shaped like the one build-iso.sh squashes: locked accounts, the
# installer on it, and nothing secret.
clean_root() {  # -> prints the path to a fresh clean root
  _r=$TMP/root.$$.$(date +%s%N)
  rm -rf "$_r"
  mkdir -p "$_r/etc/ssh" "$_r/root" "$_r/usr/sbin" "$_r/etc/ssl/certs"
  printf 'root:!*:20704:0:99999:7:::\ndaemon:*:20704:0:99999:7:::\n' \
    > "$_r/etc/shadow"
  # The real thing, not a stand-in: the installer describes the answer-file
  # format at length and the medium carries it. If the content rule were loose
  # enough to call the installer an answer file, the build could never pass.
  cp "$REPO/appliance/prisma-routing-install" "$_r/usr/sbin/"
  cp "$REPO/appliance/install-from-cmdline" "$_r/usr/sbin/"
  cp "$REPO/docs/appliance.md" "$_r/root/"
  printf '%s\n' "$_r"
}

run() {  # <expected 0|1|2> <description> <dir...>
  _want=$1; _desc=$2; shift 2
  OUT=$(sh "$CHECK" "$@" 2>&1)
  _rc=$?
  if [ "$_rc" != "$_want" ]; then
    bad "$_desc (wanted exit $_want, got $_rc)"
    echo "$OUT" | sed 's/^/        /'
    return 1
  fi
  ok "$_desc"
  return 0
}

echo "=== the clean case, which every other case is measured against ==="
R=$(clean_root)
run 0 "a root with the installer, the document and locked accounts is clean" "$R"

echo
echo "=== the answer file, which is the root password ==="

# The mutation from #245, character for character. It is NOT in the format
# read_answers() parses - "root-password:" is not one of the six keys and the
# separator is a colon - so the content rule alone would let it through, and
# saying so is the point: the name rule is what catches it, and the name rule
# is why the name rule is wider than the parser.
R=$(clean_root)
printf 'root-password: hunter2\n' > "$R/root/answers.conf"
run 1 "the sweep's own mutation: a file called answers.conf" "$R"
case "$OUT" in
  *"answers.conf"*) ok "and it names the file" ;;
  *) bad "the refusal does not name the file: $OUT" ;;
esac

R=$(clean_root)
: > "$R/prisma-routing.answers"
run 1 "an EMPTY prisma-routing.answers: refused on its name alone" "$R"

R=$(clean_root)
mkdir -p "$R/run/live/medium"
printf 'disk = /dev/vda\npassword = hunter2\nyes = 1\n' \
  > "$R/run/live/medium/prisma-routing.answers"
run 1 "the default path the medium is read from" "$R"

# The one that matters most: a real answer file, in the format the installer
# actually parses, under a name nothing would think to look for.
R=$(clean_root)
mkdir -p "$R/var/lib/hexa"
printf '# built by the release script\n\nhostname = cgnat01\ntimezone = America/Sao_Paulo\npassword = hunter2\n' \
  > "$R/var/lib/hexa/install.conf"
run 1 "a file the installer would parse, under any name at all" "$R"
case "$OUT" in
  *PRISMA_ANSWERS*) ok "and it says why: PRISMA_ANSWERS on that path installs from it" ;;
  *) bad "the refusal does not explain what makes it an answer file: $OUT" ;;
esac

# The value is never quoted back. An answer file's password is the one string
# a refusal must not print, for the same reason read_answers() refuses to quote
# the line it could not parse.
case "$OUT" in
  *hunter2*) bad "the refusal printed the password out of the answer file" ;;
  *) ok "and it does not print the password it found" ;;
esac

echo
echo "=== and what is NOT an answer file, which is most of the repository ==="
R=$(clean_root)
mkdir -p "$R/etc/prisma-routing"
# Shell that assigns some of the same names among other lines. Every appliance
# script does this; a rule that fired on it would be switched off within a week.
cat > "$R/etc/prisma-routing/tune.env" <<'ENV'
# tuning
hostname=$(cat /etc/hostname)
disk=/dev/vda
if [ -n "$disk" ]; then echo "$disk"; fi
ENV
run 0 "a shell script that happens to assign hostname= and disk=" "$R"

R=$(clean_root)
printf -- '-----BEGIN CERTIFICATE-----\nMIIBkTCB+w==\n-----END CERTIFICATE-----\n' \
  > "$R/etc/ssl/certs/Some_Root_CA.pem"
run 0 "a CA certificate is not a private key" "$R"

echo
echo "=== the ssh host keys, which every published ISO carried ==="
R=$(clean_root)
printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk=\n-----END OPENSSH PRIVATE KEY-----\n' \
  > "$R/etc/ssh/ssh_host_ed25519_key"
run 1 "a host private key in the image" "$R"
case "$OUT" in
  *"private key"*) ok "and it says what it found" ;;
  *) bad "the refusal does not name the rule: $OUT" ;;
esac

R=$(clean_root)
mkdir -p "$R/root/.ssh"
printf 'ssh-ed25519 AAAAC3Nz nobody@nowhere\n' > "$R/root/.ssh/authorized_keys"
run 1 "an authorized_keys is a login, shipped" "$R"

echo
echo "=== /etc/shadow: locked is the only state the image may ship in ==="
R=$(clean_root)
printf 'root:$y$j9T$vQ4$abcdefghij:20704:0:99999:7:::\n' > "$R/etc/shadow"
run 1 "a usable root hash in the image" "$R"
for locked in '!' '*' '!!' '!*'; do
  R=$(clean_root)
  printf 'root:%s:20704:0:99999:7:::\n' "$locked" > "$R/etc/shadow"
  run 0 "a root entry of '$locked' is locked and passes" "$R"
done

echo
echo "=== called wrongly, it says so rather than passing ==="
run 2 "no arguments at all"
run 2 "a directory that does not exist" "$TMP/nowhere"

echo
echo "=== the two lists that MUST agree ==="
# The checker parses the answer format. The installer defines it. A key added
# to read_answers() and not to the checker is an answer file the build would
# ship, and the whole content rule would be silently one key short.
# The case arms of read_answers(), at their own indentation: the nested arms
# that parse the "yes" value sit deeper, and the "*)" default is not a key.
inst=$(sed -n '/^read_answers()/,/^}/p' "$REPO/appliance/prisma-routing-install" \
       | sed -n 's/^      \([a-z][a-z-]*\)).*/\1/p' \
       | sort -u | tr '\n' ' ')
chk=$(sed -n "s/^ANSWER_KEYS='\(.*\)'$/\1/p" "$CHECK" | tr '|' '\n' | sort -u \
      | tr '\n' ' ')
if [ -z "$inst" ] || [ -z "$chk" ]; then
  bad "could not read one of the key lists (installer='$inst' checker='$chk')"
elif [ "$inst" = "$chk" ]; then
  ok "the checker knows every key read_answers() accepts: $chk"
else
  bad "the answer keys have drifted: installer='$inst' checker='$chk'"
fi

echo
echo "=== the build has to CALL it, or none of the above ships ==="
BUILD=$REPO/appliance/build-iso.sh
# Line numbers, not presence. A call placed after mksquashfs runs on a root
# whose contents are already inside the image, and a call after grub-mkrescue
# runs on a tree that is already an ISO on somebody's disk.
n_root=$(grep -n 'no-credentials-in-image" *"\$ROOT"' "$BUILD" | cut -d: -f1)
n_iso=$(grep -n 'no-credentials-in-image" *"\$WORK/iso"' "$BUILD" | cut -d: -f1)
n_squash=$(grep -n '^mksquashfs ' "$BUILD" | cut -d: -f1)
n_rescue=$(grep -n '^grub-mkrescue ' "$BUILD" | cut -d: -f1)
if [ -z "$n_root" ]; then
  bad "build-iso.sh never checks the root it is about to squash"
elif [ -z "$n_squash" ]; then
  bad "could not find the mksquashfs line in build-iso.sh"
elif [ "$n_root" -lt "$n_squash" ]; then
  ok "the root is checked at line $n_root, before mksquashfs at $n_squash"
else
  bad "the root is checked at line $n_root, AFTER mksquashfs at $n_squash"
fi
if [ -z "$n_iso" ]; then
  bad "build-iso.sh never checks the ISO tree, which is the medium's root -"
  echo "        the path 'prisma-routing.answers' reads with no argument"
elif [ -z "$n_rescue" ]; then
  bad "could not find the grub-mkrescue line in build-iso.sh"
elif [ "$n_iso" -lt "$n_rescue" ]; then
  ok "the medium is checked at line $n_iso, before grub-mkrescue at $n_rescue"
else
  bad "the medium is checked at line $n_iso, AFTER grub-mkrescue at $n_rescue"
fi

echo
echo "=== the host keys: removed from the medium AND regenerated per box ==="
# The pair is load-bearing in both directions. Removing them without
# regenerating installs an appliance whose sshd cannot start - no management
# path to a racked box. Regenerating without removing leaves the published key
# in the image, which is the defect.
if grep -q 'rm -f "\$ROOT"/etc/ssh/ssh_host_' "$BUILD"; then
  ok "build-iso.sh removes the live image's ssh host keys"
else
  bad "build-iso.sh leaves ssh host keys in the squashfs; every box installed
        from the release would share one, and its private half is in the
        download"
fi
if grep -q 'ssh-keygen -A' "$REPO/appliance/prisma-routing-install"; then
  ok "and prisma-routing-install generates this box's own set"
else
  bad "nothing generates host keys on the target, so an installed box would
        have none and sshd would not start"
fi

echo
echo "=== and the tree the image is built from ==="
# build-iso.sh copies src, appliance, tools and docs into the chroot. A
# developer's own answer file sitting in one of them is a root password in the
# repository before it is one in the image.
run 0 "src, appliance, tools and docs carry no credential" \
    "$REPO/src" "$REPO/appliance" "$REPO/tools" "$REPO/docs"

echo
echo "=== and the repository, which is the wider rule ==="
# CONTRIBUTING.md section 2 says secrets "MUST NOT go into this repository
# either", not only into what the image carries. A private key in lab/ never
# reaches an ISO and is published just as thoroughly: this repository is
# public, and so is the wiki mirror inside it.
#
# The directories are ENUMERATED rather than listed. A list is something
# somebody has to remember to extend, and the two defects this check sits
# between - a lab excluded from every run (#233) and a test file nothing
# compiled (#235) - were both exactly that. A directory added next year is
# covered without anybody deciding to cover it.
#
# CRED_SUBJECT and CRED_RULE because the same script answers two questions, and
# a message saying "in the image" to somebody looking at lab/ sends them to the
# wrong place.
dirs=""
for d in "$REPO"/*/; do
  case "$(basename "$d")" in
    .git|.build-vpp|__pycache__) continue ;;
  esac
  dirs="$dirs $d"
done
n_dirs=$(printf '%s\n' $dirs | grep -c . || true)
if [ "${n_dirs:-0}" -ge 6 ]; then
  ok "$n_dirs directory(s) in scope, found rather than listed"
else
  bad "only ${n_dirs:-0} directory(s) resolved, so the check below is looking "\
"at almost nothing"
fi
CRED_SUBJECT=repository \
CRED_RULE="a secret MUST NOT go into this repository (CONTRIBUTING.md section 2)" \
  run 0 "no credential anywhere in the repository" $dirs

echo
if [ "$fail" = 0 ]; then echo "ALL TESTS PASSED"; exit 0; fi
echo "$fail FAILURES"
exit 1
