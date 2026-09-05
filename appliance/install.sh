#!/bin/sh
# Installs HexaRouting onto an existing Debian 12.
#
# Does not build an ISO: for a fresh install see build-iso.sh. This path is
# for updating a box in production without reinstalling it.
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
DESTDIR=${DESTDIR:-}
PLUGIN_DIR=${PLUGIN_DIR:-/usr/lib/x86_64-linux-gnu/vpp_plugins}

say() { echo "==> $*"; }

say "binaries"
# hexash imports config.py from its own directory, so both have to travel
# together. Installing only hexash.py put a VTY on the box that raised
# ModuleNotFoundError on every start - the product's front door, broken on
# every installed box and every ISO, and invisible because the labs run it
# from the source tree where config.py sits next to it.
install -D -m 0755 "$REPO/src/hexash/hexash.py"      "$DESTDIR/usr/lib/hexarouting/hexash.py"
install -D -m 0644 "$REPO/src/hexash/config.py"      "$DESTDIR/usr/lib/hexarouting/config.py"
install -D -m 0755 "$REPO/appliance/hexash-launcher" "$DESTDIR/usr/bin/hexash"
install -D -m 0755 "$REPO/appliance/hexarouting-tune" "$DESTDIR/usr/bin/hexarouting-tune"
install -D -m 0755 "$REPO/appliance/wait-for-vpp"     "$DESTDIR/usr/lib/hexarouting/wait-for-vpp"
install -D -m 0755 "$REPO/appliance/apply-tuning"     "$DESTDIR/usr/lib/hexarouting/apply-tuning"
install -D -m 0755 "$REPO/appliance/selftest"         "$DESTDIR/usr/lib/hexarouting/selftest"
install -D -m 0755 "$REPO/tools/ipfix_collector.py"   "$DESTDIR/usr/lib/hexarouting/ipfix-collector"

say "dataplane plugin"
if [ -f "$REPO/.build-vpp/lib/vpp_plugins/bpa_plugin.so" ]; then
  SO="$REPO/.build-vpp/lib/vpp_plugins/bpa_plugin.so"
elif [ -f /vpp_plugins/bpa_plugin.so ]; then
  SO=/vpp_plugins/bpa_plugin.so
else
  echo "bpa_plugin.so not found; run tools/build.sh plugin first" >&2
  exit 1
fi
install -D -m 0644 "$SO" "$DESTDIR$PLUGIN_DIR/bpa_plugin.so"

say "systemd"
for u in hexarouting.service hexarouting-tune.service hexarouting-selftest.service; do
  install -D -m 0644 "$REPO/appliance/systemd/$u" "$DESTDIR/lib/systemd/system/$u"
done

say "version stamp"
# The box has to be able to state its own version: the tag identifies the
# release only to whoever has the repository open, and that is not the person
# on the phone. And if the tree had uncommitted changes, the file says so - an
# ISO that declares itself v0.4.1 without being it sends the whole
# investigation the wrong way.
if [ -n "${HEXA_VERSION_STAMP:-}" ]; then
  stamp=$HEXA_VERSION_STAMP
else
  stamp=$(sh "$REPO/tools/version-stamp.sh" "$REPO")
fi
install -d -m 0755 "$DESTDIR/usr/share/hexarouting"
printf '%s\n' "$stamp" > "$DESTDIR/usr/share/hexarouting/VERSION"
chmod 0644 "$DESTDIR/usr/share/hexarouting/VERSION"

say "kernel knobs"
install -D -m 0644 "$REPO/appliance/etc/sysctl.conf" \
    "$DESTDIR/etc/sysctl.d/99-hexarouting.conf"

say "configuration"
install -d -m 0755 "$DESTDIR/etc/hexarouting"
[ -f "$DESTDIR/etc/hexarouting/tune.env" ] || \
  install -m 0644 "$REPO/appliance/etc/tune.env" "$DESTDIR/etc/hexarouting/tune.env"
[ -f "$DESTDIR/etc/hexarouting/vpp-base.conf" ] || \
  install -m 0644 "$REPO/appliance/etc/vpp-base.conf" \
    "$DESTDIR/etc/hexarouting/vpp-base.conf"
[ -f "$DESTDIR/etc/hexarouting/hexarouting.conf" ] || \
  printf '!\n! HexaRouting\n!\n' > "$DESTDIR/etc/hexarouting/hexarouting.conf"

say "documentation"
for d in architecture routing logging; do
  install -D -m 0644 "$REPO/docs/$d.md" "$DESTDIR/usr/share/doc/hexarouting/$d.md"
done

# In a chroot or container there is no systemd running; installing is still
# valid, only enabling services is not. That is the normal case when the ISO
# is being assembled.
if [ -z "$DESTDIR" ] && systemctl --version >/dev/null 2>&1 \
   && [ -d /run/systemd/system ]; then
  say "enabling services"
  systemctl daemon-reload
  systemctl enable hexarouting-tune.service hexarouting.service \
    hexarouting-selftest.service
  echo
  echo "Installed. Adjust /etc/hexarouting/tune.env and reboot so that"
  echo "isolcpus and hugepages take effect."
else
  # offline enablement: creates the link systemctl enable would create
  install -d -m 0755 "$DESTDIR/etc/systemd/system/multi-user.target.wants"
  for u in hexarouting-tune.service hexarouting.service \
           hexarouting-selftest.service; do
    ln -sf "/lib/systemd/system/$u" \
      "$DESTDIR/etc/systemd/system/multi-user.target.wants/$u"
  done
  say "installed into ${DESTDIR:-/} (services enabled offline)"
fi
