#!/bin/sh
# Compila o plugin bpa dentro do container de build.
#
# Nesta VM o grupo docker so entra em vigor em login novo, entao quando o
# socket nega acesso reexecutamos via "sg docker", que readquire o grupo.
set -e

REPO=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${IMAGE:-hexarouting-dev:26.06}
TARGET=${1:-plugin}

if ! docker ps >/dev/null 2>&1; then
  exec sg docker -c "IMAGE='$IMAGE' '$0' '$TARGET'"
fi

case "$TARGET" in
  image)
    docker build -f "$REPO/build/Dockerfile.dev" -t "$IMAGE" "$REPO"
    ;;
  plugin)
    docker run --rm -v "$REPO:/src" -w /src "$IMAGE" sh -c \
      "set -e
       mkdir -p .build-vpp && cd .build-vpp && cmake -G Ninja .. >/dev/null && ninja
       # As macros do VPP linkam direto em /vpp_plugins, que e do container e
       # some com o --rm. Sem esta copia o .so nunca chega ao host, e quem
       # for montar a ISO depois nao acha o plugin.
       mkdir -p /src/.build-vpp/lib/vpp_plugins
       cp /vpp_plugins/bpa_plugin.so /src/.build-vpp/lib/vpp_plugins/
       chown -R $(id -u):$(id -g) /src/.build-vpp"
    ;;
  *)
    echo "uso: $0 [image|plugin]" >&2
    exit 1
    ;;
esac
