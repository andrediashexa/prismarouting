#!/bin/sh
# Instala o HexaRouting sobre um Debian 12 ja existente.
#
# Nao constroi ISO: para instalacao nova, ver build-iso.sh. Este caminho
# serve para atualizar uma caixa em producao sem reinstalar.
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
DESTDIR=${DESTDIR:-}
PLUGIN_DIR=${PLUGIN_DIR:-/usr/lib/x86_64-linux-gnu/vpp_plugins}

say() { echo "==> $*"; }

say "binaries"
install -D -m 0755 "$REPO/src/hexash/hexash.py"      "$DESTDIR/usr/bin/hexash"
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

# Em chroot ou container nao ha systemd rodando; instalar continua valido,
# so nao da para habilitar servico. Isso e o caso normal ao montar a ISO.
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
  # habilitacao offline: cria o link que o systemctl enable criaria
  install -d -m 0755 "$DESTDIR/etc/systemd/system/multi-user.target.wants"
  for u in hexarouting-tune.service hexarouting.service \
           hexarouting-selftest.service; do
    ln -sf "/lib/systemd/system/$u" \
      "$DESTDIR/etc/systemd/system/multi-user.target.wants/$u"
  done
  say "installed into ${DESTDIR:-/} (services enabled offline)"
fi
