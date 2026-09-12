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
# The WHOLE module directory, not a list of files.
#
# Naming them one by one has now shipped the same defect twice: config.py was
# missing in 0.2.0 and the VTY raised ModuleNotFoundError on every installed
# box, and vppapi.py was missing the moment the binary API landed. A list that
# has to be updated whenever an import is added is a list that will be wrong
# again.
install -d -m 0755 "$DESTDIR/usr/lib/hexarouting"
for f in "$REPO"/src/hexash/*.py; do
  install -D -m 0644 "$f" "$DESTDIR/usr/lib/hexarouting/$(basename "$f")"
done
chmod 0755 "$DESTDIR/usr/lib/hexarouting/hexash.py"
install -D -m 0755 "$REPO/appliance/hexash-launcher" "$DESTDIR/usr/bin/hexash"
install -D -m 0755 "$REPO/appliance/hexarouting-tune" "$DESTDIR/usr/bin/hexarouting-tune"
install -D -m 0755 "$REPO/appliance/wait-for-vpp"     "$DESTDIR/usr/lib/hexarouting/wait-for-vpp"
install -D -m 0755 "$REPO/appliance/apply-tuning"     "$DESTDIR/usr/lib/hexarouting/apply-tuning"
install -D -m 0755 "$REPO/appliance/selftest"         "$DESTDIR/usr/lib/hexarouting/selftest"
install -D -m 0755 "$REPO/appliance/hexarouting-install" "$DESTDIR/usr/sbin/hexarouting-install"
install -D -m 0755 "$REPO/appliance/install-from-cmdline" "$DESTDIR/usr/lib/hexarouting/install-from-cmdline"
install -D -m 0755 "$REPO/tools/ipfix_collector.py"   "$DESTDIR/usr/lib/hexarouting/ipfix-collector"
install -D -m 0755 "$REPO/appliance/hexarouting-snmp-agent" \
    "$DESTDIR/usr/lib/hexarouting/snmp-agent"

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

# The vat2 plugin is what lets anything outside VPP speak the binary API for
# this plugin - which is how hexash learns that a configuration was refused
# rather than applied. Without it, vat2 answers "No such command" and the
# control plane is back to parsing CLI text.
VAT2_DIR=${VAT2_DIR:-/usr/lib/x86_64-linux-gnu/vat2_plugins}
if [ -f "$REPO/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so" ]; then
  VSO="$REPO/.build-vpp/lib/vat2_plugins/bpa_test_plugin_bpa_plugin.so"
elif [ -f /vat2_plugins/bpa_test_plugin_bpa_plugin.so ]; then
  VSO=/vat2_plugins/bpa_test_plugin_bpa_plugin.so
else
  VSO=""
fi
if [ -n "$VSO" ]; then
  install -D -m 0644 "$VSO" "$DESTDIR$VAT2_DIR/bpa_test_plugin_bpa_plugin.so"
else
  echo "warning: no vat2 plugin; the binary API will not be reachable" >&2
fi

say "systemd"
for u in hexarouting.service hexarouting-tune.service hexarouting-selftest.service \
         hexarouting-installer.service hexarouting-confirm.service \
         hexarouting-confirm.timer; do
  install -D -m 0644 "$REPO/appliance/systemd/$u" "$DESTDIR/lib/systemd/system/$u"
done
# A drop-in on VPP's own unit, not a unit of ours: it has to run every time the
# dataplane starts, including a restart nobody ordered, and ordering a separate
# unit after vpp.service would put it in the wrong place on exactly that path.
install -D -m 0644 "$REPO/appliance/systemd/vpp-api-access.conf" \
  "$DESTDIR/etc/systemd/system/vpp.service.d/hexarouting-api-access.conf"
# Same reason it is a drop-in and not a unit of ours, plus one more: a
# protection that only applies to the VPP this boot started is no protection.
# vpp.service carries Restart=always, and the restart after an OOM kill is
# exactly the moment the box must not be choosable again.
install -D -m 0644 "$REPO/appliance/systemd/vpp-oom-protect.conf" \
  "$DESTDIR/etc/systemd/system/vpp.service.d/hexarouting-oom-protect.conf"

say "login shell"
install -D -m 0755 "$REPO/appliance/hexash-login" "$DESTDIR/usr/bin/hexash-login"
# Opt-in on purpose. On an existing Debian the machine may have other jobs, and
# silently replacing root's login shell is hostile - the operator would discover
# it at the worst moment, on the next login. The ISO sets HEXA_VTY_LOGIN=1.
if [ "${HEXA_VTY_LOGIN:-0}" = 1 ]; then
  grep -qx /usr/bin/hexash-login "$DESTDIR/etc/shells" 2>/dev/null || \
    echo /usr/bin/hexash-login >> "$DESTDIR/etc/shells"
  if [ -n "$DESTDIR" ]; then
    sed -i 's|^root:\([^:]*\):\([^:]*\):\([^:]*\):\([^:]*\):\([^:]*\):.*|root:\1:\2:\3:\4:\5:/usr/bin/hexash-login|' \
      "$DESTDIR/etc/passwd"
  else
    chsh -s /usr/bin/hexash-login root
  fi
  say "  root logs into the VTY; 'start shell' escapes, 'hexash=off' bypasses"
else
  say "  not changing any login shell (set HEXA_VTY_LOGIN=1 to land in the VTY)"
fi

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
# Group-readable, never world-readable. Since accounts became part of the
# configuration (#45) this directory holds password hashes - in
# hexarouting.conf and in every rollback revision under it - and a
# world-readable copy hands every local account an offline cracking target.
# The group is a role group, so seeing them requires being trusted to change
# the configuration in the first place (#35).
# The two roles the VTY understands. Created here rather than documented as a
# step somebody must remember: an authorization model whose groups do not exist
# is one where every account falls back to read-only and the operator concludes
# the box is broken. Empty until somebody is added to them; root always has
# both.
for g in hexa-admin hexa-cgnat; do
  if [ -z "${DESTDIR:-}" ]; then
    getent group "$g" >/dev/null 2>&1 || groupadd --system "$g" 2>/dev/null || true
  fi
done

# 0770 and group-owned, not 0700: an account that may configure has to write
# the rollback store and the saved configuration, and both hold the whole
# configuration - password hashes included. Granting the capability grants
# sight of them, which is why the group is a role group and not "users".
#
# hexa-cgnat owns it and hexa-admin is ADDITIVE on top: an administrator is a
# member of both. Owning it with hexa-admin instead would leave a CGNAT
# operator unable to record a rollback revision for the change it just made.
#
# THIS FILE IS THE ONLY PLACE THESE MODES ARE STATED. The VTY does not restate
# them and does not narrow them: it chmod'd 0700/0600 on every save and every
# commit, so the first one by anybody - root applying the configuration at
# boot - took the group's access away and every non-root operation afterwards
# failed (#188). It cannot be the other owner either, because half of this is
# ownership: a mode without the group grants nobody anything, and a non-root
# operator cannot chgrp a directory it does not own.
#
# 2770 rather than 0770. The group is not enough on its own: a revision root
# writes at boot would be group root, and one operator's revision would be
# unreadable to the next. setgid makes the kernel put hexa-cgnat on everything
# created in here - including the rollback directory, since a directory made
# inside a setgid directory inherits the bit - so "rollback 1" works whoever
# made the revision.
#
# install -d re-states it on an upgrade as well as a first install, which is
# what repairs a box the old VTY already narrowed.
install -d -m 2770 "$DESTDIR/etc/hexarouting"
install -d -m 2770 "$DESTDIR/etc/hexarouting/rollback"
if [ -z "${DESTDIR:-}" ] && getent group hexa-cgnat >/dev/null 2>&1; then
  chgrp hexa-cgnat /etc/hexarouting /etc/hexarouting/rollback 2>/dev/null || true
fi
# The configuration trail. The plugin opens it with O_CREAT and cannot create
# the directory, so without this the trail exists in every lab - where the lab
# happens to make the directory - and on no installed box. In deterministic
# mode that trail is the record of which pool definition was in force at a
# given instant, which is what makes an answer to a court order defensible.
# 0750: it names subscribers and their public addresses.
install -d -m 0750 "$DESTDIR/var/log/hexarouting"
[ -f "$DESTDIR/etc/hexarouting/tune.env" ] || \
  install -m 0644 "$REPO/appliance/etc/tune.env" "$DESTDIR/etc/hexarouting/tune.env"
[ -f "$DESTDIR/etc/hexarouting/vpp-base.conf" ] || \
  install -m 0644 "$REPO/appliance/etc/vpp-base.conf" \
    "$DESTDIR/etc/hexarouting/vpp-base.conf"
[ -f "$DESTDIR/etc/hexarouting/hexarouting.conf" ] || \
  printf '!\n! HexaRouting\n!\n' > "$DESTDIR/etc/hexarouting/hexarouting.conf"
# 0660 for the same reason as the directory: an account that may configure
# saves it, and it holds hashes. Never world-readable.
chmod 0660 "$DESTDIR/etc/hexarouting/hexarouting.conf"
if [ -z "${DESTDIR:-}" ] && getent group hexa-cgnat >/dev/null 2>&1; then
  chgrp hexa-cgnat "$DESTDIR/etc/hexarouting/hexarouting.conf" 2>/dev/null || true
fi

# ------------------------------------------------------------ routing daemons
#
# The Debian package ships /etc/frr/daemons with bgpd=no, ospfd=no and
# isisd=no. frrinit.sh always starts zebra, mgmtd and staticd, so a box
# installed without this runs the dataplane, the VTY, and static routes - and
# none of the routing protocols.
#
# The VTY offers all three. "?" lists them, TAB completes them, "commit"
# accepts them, and the daemon is not there. This project's own documentation
# claims BGP, OSPF and IS-IS (#99).
#
# Enabled here rather than left to the distribution default, because inheriting
# a default for something the product claims to support is exactly how it went
# unnoticed: lab/routing.sh starts these daemons by hand, so the lab proved a
# control plane the shipped box did not have.
say "routing daemons"
for d in bgpd ospfd isisd; do
  if grep -qE "^$d=" "$DESTDIR/etc/frr/daemons" 2>/dev/null; then
    sed -i "s/^$d=no$/$d=yes/" "$DESTDIR/etc/frr/daemons"
  else
    echo "$d=yes" >> "$DESTDIR/etc/frr/daemons"
  fi
done
grep -E "^(bgpd|ospfd|isisd)=" "$DESTDIR/etc/frr/daemons" | sed 's/^/  /'

# The one file in /etc/frr an administrator has to be able to write.
#
# Applying a routing change ends with frr-reload asking vtysh to persist the
# result, and with integrated-vtysh-config that is /etc/frr/frr.conf. The
# package ships it 0640 frr:frr, so a non-root administrator got "failed to
# open configuration file: Permission denied" and the whole commit was rolled
# back - the account could check a routing change and not make one (#186).
#
# The FILE and not the directory, measured both ways on FRR 10.7.1: with the
# file group-writable vtysh rewrites it in place and it stays frr:frr, which
# works for every account and for the next one. With the DIRECTORY group-
# writable instead, vtysh renames it aside and creates a new one owned by
# whoever committed - after which the next administrator cannot write it at
# all, and FRR reads its own configuration from a file an operator owns.
#
# Group frr and not a role group: user frr has to read this file at boot, and
# the accounts the VTY creates are in group frr already - they run vtysh, which
# is total control of the routing daemons whatever this mode says. What it adds
# is the ability to persist a configuration they can already make.
#
# The cost, stated because it is real: this is a package conffile, so an frr
# upgrade that reinstalls it puts 0640 back and non-root commits start failing
# again until this installer is re-run. It fails loudly - the commit is refused
# and rolled back - rather than quietly.
if [ -f "$DESTDIR/etc/frr/frr.conf" ]; then
  chmod 0660 "$DESTDIR/etc/frr/frr.conf"
fi

# RPKI is a MODULE, and a module that is installed and never loaded is not
# there. `bgpd --version` reports `--enable-rpki` either way, which is how this
# looks like support and is not: without the module every RPKI command answers
# "bgpd is not running", because bgpd refuses to start when told to load a
# module it cannot find.
#
# Measured: the module ships in frr-rpki-rtrlib, from the same repository and
# at the same version as frr itself, so this adds no new source and no skew.
say "RPKI module"
if grep -q "^bgpd_options=" "$DESTDIR/etc/frr/daemons" 2>/dev/null; then
  if grep -q "^bgpd_options=.*-M rpki" "$DESTDIR/etc/frr/daemons"; then
    echo "  bgpd already loads it"
  else
    sed -i 's/^\(bgpd_options="[^"]*\)"/\1 -M rpki"/' \
      "$DESTDIR/etc/frr/daemons"
  fi
else
  echo 'bgpd_options="  -A 127.0.0.1 -M rpki"' >> "$DESTDIR/etc/frr/daemons"
fi
grep -E "^bgpd_options=" "$DESTDIR/etc/frr/daemons" | sed 's/^/  /'

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
    hexarouting-selftest.service hexarouting-installer.service \
    hexarouting-confirm.timer
  echo
  echo "Installed. Adjust /etc/hexarouting/tune.env and reboot so that"
  echo "isolcpus and hugepages take effect."
else
  # offline enablement: creates the link systemctl enable would create
  install -d -m 0755 "$DESTDIR/etc/systemd/system/multi-user.target.wants"
  for u in hexarouting-tune.service hexarouting.service \
           hexarouting-selftest.service hexarouting-installer.service; do
    ln -sf "/lib/systemd/system/$u" \
      "$DESTDIR/etc/systemd/system/multi-user.target.wants/$u"
  done
  # The timer is wanted by timers.target and not by multi-user.target, so it
  # needs its own directory. Linking it into the wrong one installs a timer
  # that exists, parses, and never elapses - and the feature it enforces is
  # one nobody notices working, so nobody would notice that either.
  install -d -m 0755 "$DESTDIR/etc/systemd/system/timers.target.wants"
  ln -sf "/lib/systemd/system/hexarouting-confirm.timer" \
    "$DESTDIR/etc/systemd/system/timers.target.wants/hexarouting-confirm.timer"
  say "installed into ${DESTDIR:-/} (services enabled offline)"
fi
