#!/bin/sh
# appliance/prisma-identity against a directory that looks like a stock
# bookworm root (#438): what it rewrites, what it leaves alone, and that a
# second run - every upgrade is one - changes nothing more.
#
# The files seeded below are debian:12's own, copied out of the image on
# 2026-09-24, so "it no longer says Debian" is measured against what a box
# really starts with. No root, no dpkg: the directory has no
# var/lib/dpkg/status, so the diversion is skipped and says so.
#
# Mutations shown red, one at a time: GRUB_DISTRIBUTOR appended instead of
# replaced (two lines, the check for one goes red); the chmod on 10-uname
# removed; ID_LIKE dropped from os-release. And the first version of the
# script was red here unmutated: its VERSION_ID was written unquoted.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fail=0
ok()  { echo "  [ ok ] $1"; }
bad() { echo "  FAIL: $1"; fail=$((fail + 1)); }
R=$T/root

mkdir -p "$R/usr/lib" "$R/etc/default" "$R/etc/update-motd.d"
cat > "$R/usr/lib/os-release" <<'OS'
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
VERSION="12 (bookworm)"
VERSION_CODENAME=bookworm
ID=debian
HOME_URL="https://www.debian.org/"
SUPPORT_URL="https://www.debian.org/support"
BUG_REPORT_URL="https://bugs.debian.org/"
OS
ln -s ../usr/lib/os-release "$R/etc/os-release"
printf 'Debian GNU/Linux 12 \\n \\l\n\n' > "$R/etc/issue"
printf 'Debian GNU/Linux 12\n' > "$R/etc/issue.net"
printf '\nThe programs included with the Debian GNU/Linux system are free software;\n' > "$R/etc/motd"
printf '#!/bin/sh\nuname -snrvm\n' > "$R/etc/update-motd.d/10-uname"
chmod 0755 "$R/etc/update-motd.d/10-uname"
cat > "$R/etc/default/grub" <<'GRUB'
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_DISTRIBUTOR=`lsb_release -i -s 2> /dev/null || echo Debian`
GRUB_CMDLINE_LINUX_DEFAULT="quiet"
GRUB
mkdir -p "$R/usr/share/prisma-routing"
echo "0.124.0 (abc1234)" > "$R/usr/share/prisma-routing/VERSION"

echo "=== a stock bookworm root, named ==="
OUT=$(sh "$REPO/appliance/prisma-identity" "$R" 2>&1)
[ $? -eq 0 ] && ok "it ran" || bad "it failed: $OUT"
case $OUT in *"not a dpkg root"*) ok "and said it wrote os-release without a diversion" ;;
  *) bad "no word about the diversion it skipped: $OUT" ;; esac
grep -qx 'PRETTY_NAME="Prisma Routing 0.124.0"' "$R/usr/lib/os-release" &&
  ok "os-release names the product and the version, not the commit" ||
  bad "PRETTY_NAME: $(grep PRETTY_NAME "$R/usr/lib/os-release")"
grep -qx 'ID=prisma-routing' "$R/usr/lib/os-release" && ok "its ID is the product's" ||
  bad "ID: $(grep '^ID=' "$R/usr/lib/os-release")"
grep -qx 'ID_LIKE=debian' "$R/usr/lib/os-release" &&
  ok "and ID_LIKE=debian stays, for apt and every script that asks" ||
  bad "ID_LIKE=debian is gone"
[ "$(readlink "$R/etc/os-release")" = ../usr/lib/os-release ] &&
  ok "/etc/os-release is still the link to it" || bad "/etc/os-release: $(ls -l "$R/etc/os-release")"
[ "$(head -1 "$R/etc/issue")" = 'Prisma Routing 0.124.0 \n \l' ] &&
  ok "the console banner, with getty's escapes kept" || bad "issue: $(head -1 "$R/etc/issue")"
grep -qx 'Prisma Routing 0.124.0' "$R/etc/issue.net" && ok "issue.net" || bad "issue.net: $(cat "$R/etc/issue.net")"
grep -q 'Prisma Routing 0.124.0' "$R/etc/motd" && ok "motd" || bad "motd: $(cat "$R/etc/motd")"
[ -x "$R/etc/update-motd.d/10-uname" ] && bad "10-uname still runs at every login" ||
  ok "10-uname no longer runs at login"
[ -f "$R/etc/update-motd.d/10-uname" ] && ok "but is still there, so dpkg does not restore it" ||
  bad "10-uname was deleted: dpkg puts back a missing conffile"
N=$(grep -c '^GRUB_DISTRIBUTOR=' "$R/etc/default/grub")
[ "$N" = 1 ] && grep -qx 'GRUB_DISTRIBUTOR="Prisma Routing"' "$R/etc/default/grub" &&
  ok "the boot menu's name, replaced rather than added to" ||
  bad "GRUB_DISTRIBUTOR ($N line(s)): $(grep GRUB_DISTRIBUTOR "$R/etc/default/grub")"
grep -qx 'GRUB_TIMEOUT=5' "$R/etc/default/grub" && ok "and the rest of that file untouched" ||
  bad "the grub defaults lost a line"
LEFT=$(grep -rl -i debian "$R/etc/issue" "$R/etc/issue.net" "$R/etc/motd" "$R/etc/default/grub")
[ -z "$LEFT" ] && ok "none of the files it owns says Debian any more" || bad "still Debian: $LEFT"

echo "=== a second run, which is what every upgrade is ==="
( cd "$R" && find . -type f -exec sha256sum {} + | sort ) > "$T/first"
sh "$REPO/appliance/prisma-identity" "$R" >/dev/null 2>&1
( cd "$R" && find . -type f -exec sha256sum {} + | sort ) > "$T/second"
cmp -s "$T/first" "$T/second" && ok "changes nothing" ||
  bad "the second run changed: $(diff "$T/first" "$T/second" | head -4)"

echo "=== a version given on the line wins over the stamp ==="
sh "$REPO/appliance/prisma-identity" "$R" 0.125.0 >/dev/null 2>&1
grep -qx 'VERSION_ID="0.125.0"' "$R/usr/lib/os-release" && ok "0.125.0, quoted" ||
  bad "VERSION_ID: $(grep VERSION_ID "$R/usr/lib/os-release")"

echo "=== a root with no grub defaults and no stamp ==="
E=$T/empty; mkdir -p "$E"
sh "$REPO/appliance/prisma-identity" "$E" >/dev/null 2>&1 && ok "runs" || bad "failed on an empty root"
[ ! -e "$E/etc/default/grub" ] && ok "and does not invent a grub defaults file" || bad "it wrote etc/default/grub"
grep -qx 'PRETTY_NAME="Prisma Routing"' "$E/usr/lib/os-release" &&
  ok "the name alone, with no empty version after it" ||
  bad "PRETTY_NAME: $(grep PRETTY_NAME "$E/usr/lib/os-release")"
grep -E '^VERSION(_ID)?=' "$E/usr/lib/os-release" >/dev/null && bad "a VERSION line with nothing in it" ||
  ok "and no VERSION line claiming one"

echo "=== the ssh banner ==="
grep -qx 'DebianBanner no' "$REPO/appliance/etc/sshd-identity.conf" &&
  ok "the drop-in turns the Debian suffix off" || bad "no DebianBanner no"
grep -q 'appliance/etc/sshd-identity.conf.*' "$REPO/appliance/install.sh" &&
  grep -q 'sshd_config.d/00-identity.conf' "$REPO/appliance/install.sh" &&
  ok "and install.sh puts it where sshd reads it first" || bad "install.sh does not install it"

[ $fail -eq 0 ] && { echo; echo "ALL TESTS PASSED"; exit 0; }
echo; echo "$fail FAILURES"; exit 1
