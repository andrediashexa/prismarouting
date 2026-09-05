#!/bin/sh
# Abre a VTY do HexaRouting.
#
#   sh tools/vty.sh                 sessao interativa
#   sh tools/vty.sh -c "comando"    um comando e sai
#
# A VTY roda onde o dataplane esta: dentro do container que segura o VPP.
# O vppctl fala por um socket que vive la dentro.
set -eu
CONTAINER=${CONTAINER:-hexa-vpp}

if ! docker ps >/dev/null 2>&1; then
  exec sg docker -c "CONTAINER='$CONTAINER' '$0' $*"
fi

docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" || {
  echo "container '$CONTAINER' nao esta rodando." >&2
  echo "suba o lab com: sh tools/lab.sh hw-lab   (ver TESTING.md)" >&2
  exit 1
}

# -t so quando ha terminal: sem isso o modo -c quebra em pipe
TTY=""
[ -t 0 ] && TTY="-it"
exec docker exec $TTY "$CONTAINER" python3 /src/src/hexash/hexash.py "$@"
