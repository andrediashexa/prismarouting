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

say "binarios"
install -D -m 0755 "$REPO/src/hexash/hexash.py"      "$DESTDIR/usr/bin/hexash"
install -D -m 0755 "$REPO/appliance/hexarouting-tune" "$DESTDIR/usr/bin/hexarouting-tune"
install -D -m 0755 "$REPO/appliance/wait-for-vpp"     "$DESTDIR/usr/lib/hexarouting/wait-for-vpp"
install -D -m 0755 "$REPO/appliance/apply-tuning"     "$DESTDIR/usr/lib/hexarouting/apply-tuning"
install -D -m 0755 "$REPO/tools/ipfix_collector.py"   "$DESTDIR/usr/lib/hexarouting/ipfix-collector"

say "plugin do dataplane"
if [ -f "$REPO/.build-vpp/lib/vpp_plugins/bpa_plugin.so" ]; then
  SO="$REPO/.build-vpp/lib/vpp_plugins/bpa_plugin.so"
elif [ -f /vpp_plugins/bpa_plugin.so ]; then
  SO=/vpp_plugins/bpa_plugin.so
else
  echo "bpa_plugin.so nao encontrado; rode tools/build.sh plugin antes" >&2
  exit 1
fi
install -D -m 0644 "$SO" "$DESTDIR$PLUGIN_DIR/bpa_plugin.so"

say "systemd"
for u in hexarouting.service hexarouting-tune.service; do
  install -D -m 0644 "$REPO/appliance/systemd/$u" "$DESTDIR/lib/systemd/system/$u"
done

say "configuracao"
install -d -m 0755 "$DESTDIR/etc/hexarouting"
install -d -m 0755 "$DESTDIR/etc/vpp/startup.conf.d"
[ -f "$DESTDIR/etc/hexarouting/tune.env" ] || \
  install -m 0644 "$REPO/appliance/etc/tune.env" "$DESTDIR/etc/hexarouting/tune.env"
[ -f "$DESTDIR/etc/hexarouting/hexarouting.conf" ] || \
  printf '!\n! HexaRouting\n!\n' > "$DESTDIR/etc/hexarouting/hexarouting.conf"

say "documentacao"
for d in architecture routing logging; do
  install -D -m 0644 "$REPO/docs/$d.md" "$DESTDIR/usr/share/doc/hexarouting/$d.md"
done

# Em chroot ou container nao ha systemd rodando; instalar continua valido,
# so nao da para habilitar servico. Isso e o caso normal ao montar a ISO.
if [ -z "$DESTDIR" ] && systemctl --version >/dev/null 2>&1 \
   && [ -d /run/systemd/system ]; then
  say "habilitando servicos"
  systemctl daemon-reload
  systemctl enable hexarouting-tune.service hexarouting.service
  echo
  echo "Instalado. Ajuste /etc/hexarouting/tune.env e reinicie para que"
  echo "isolcpus e hugepages entrem em vigor."
else
  # habilitacao offline: cria o link que o systemctl enable criaria
  install -d -m 0755 "$DESTDIR/etc/systemd/system/multi-user.target.wants"
  for u in hexarouting-tune.service hexarouting.service; do
    ln -sf "/lib/systemd/system/$u" \
      "$DESTDIR/etc/systemd/system/multi-user.target.wants/$u"
  done
  say "instalado em ${DESTDIR:-/} (servicos habilitados offline)"
fi
