#!/bin/sh
# Compila o plugin bpa dentro do container de build.
#
# On this VM the docker group only takes effect in a new login, so when the
# socket denies access we re-exec through "sg docker", which reacquires it.
set -e

REPO=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${IMAGE:-prisma-routing-dev:26.06}
TARGET=${1:-plugin}

if ! docker ps >/dev/null 2>&1; then
  exec sg docker -c "IMAGE='$IMAGE' '$0' '$TARGET'"
fi

# The version the plugin will carry, read HERE and not in the container (#291).
#
# The container gets /src as a bind mount and nothing else, so a repository
# that is a git WORKTREE has no .git directory inside it - the file there
# points at one under the main clone, outside the mount. Measured on this
# tree:
#
#   on this host                        0.71.0 (d07ebda)
#   inside prisma-routing-dev:26.06        0.71.0 (not a git tree)
#
# The second one would be stamped into every plugin built here, would differ
# from the VERSION file install.sh writes on the host, and `show version`
# would report a pending reboot on a box that owes nobody one.
#
# PRISMA_VERSION_STAMP is not a new knob: it is the override install.sh already
# honours for the same file, so an ISO build that pins a stamp pins both.
PRISMA_VERSION_STAMP=${PRISMA_VERSION_STAMP:-$(sh "$REPO/tools/version-stamp.sh" "$REPO")}
export PRISMA_VERSION_STAMP

# THE IMAGE HAS TO BE THERE, and the refusal is a sentence rather than
# docker's. A `docker run` against a tag this host has not got goes to the
# registry, is refused there, and prints "pull access denied ... may require
# docker login" - which sends whoever reads it to look for credentials for an
# image that was never published anywhere. It is built here, from
# build/Dockerfile.dev, and #202 is how this was met: the rename moved the tag
# and every host still held the image under the old one.
if [ "$TARGET" != image ] && ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "error: this host has no $IMAGE image, and it is not published" >&2
  echo "       anywhere: it is built from build/Dockerfile.dev." >&2
  echo "       Build it:  sh tools/build.sh image" >&2
  echo "       An image under an older tag is the same contents: " >&2
  echo "       'docker tag <old tag> $IMAGE' is enough." >&2
  exit 1
fi

case "$TARGET" in
  image)
    docker build -f "$REPO/build/Dockerfile.dev" -t "$IMAGE" "$REPO"
    ;;
  plugin)
    docker run --rm -v "$REPO:/src" -w /src \
      -e "PRISMA_VERSION_STAMP=$PRISMA_VERSION_STAMP" "$IMAGE" sh -c \
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
