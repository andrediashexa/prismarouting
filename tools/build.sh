#!/bin/sh
# Compila o plugin bpa dentro do container de build.
#
# Nesta VM o grupo docker so entra em vigor em login novo, entao os comandos
# docker vao prefixados com "sg docker -c".
set -e

REPO=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${IMAGE:-hxcgnat-dev:26.06}
DOCKER="docker"
docker ps >/dev/null 2>&1 || DOCKER="sg docker -c docker"

run() {
  if [ "$DOCKER" = "docker" ]; then
    docker "$@"
  else
    sg docker -c "docker $(for a in "$@"; do printf '%s ' "$a"; done)"
  fi
}

case "${1:-plugin}" in
  image)
    run build -f "$REPO/build/Dockerfile.dev" -t "$IMAGE" "$REPO"
    ;;
  plugin)
    run run --rm -v "$REPO:/src" -w /src "$IMAGE" sh -c \
      "mkdir -p .build-vpp && cd .build-vpp && cmake -G Ninja .. >/dev/null && ninja && chown -R $(id -u):$(id -g) /src/.build-vpp"
    ;;
  *)
    echo "uso: $0 [image|plugin]" >&2
    exit 1
    ;;
esac
