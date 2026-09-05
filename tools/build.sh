#!/bin/sh
# Compila o plugin bpa dentro do container de build.
#
# On this VM the docker group only takes effect in a new login, so when the
# socket denies access we re-exec through "sg docker", which reacquires it.
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
       # VPP's macros link straight into /vpp_plugins, which belongs to the
       # container and disappears with --rm. Without this copy the .so never
       # reaches the host, and whoever builds the ISO later cannot find it.
       mkdir -p /src/.build-vpp/lib/vpp_plugins /src/.build-vpp/lib/vat2_plugins
       cp /vpp_plugins/bpa_plugin.so /src/.build-vpp/lib/vpp_plugins/
       # The vat2 plugin is what makes the binary API reachable from a shell.
       # It is linked into /vat2_plugins for the same reason and disappears the
       # same way.
       cp /vat2_plugins/bpa_test_plugin_bpa_plugin.so /src/.build-vpp/lib/vat2_plugins/
       chown -R $(id -u):$(id -g) /src/.build-vpp"
    ;;
  *)
    echo "uso: $0 [image|plugin]" >&2
    exit 1
    ;;
esac
