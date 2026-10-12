#!/bin/sh
# Builds accel-ppp-ng, the PPPoE control plane of the prisma-bng container
# (#597), at its pin and with this repository's one delta, into a root.
#
#   sh build/accel-ppp-build.sh --deps     the Debian packages it needs
#   sh build/accel-ppp-build.sh <destdir>  fetch, patch, build, install
#
# ONE RECIPE, TWO READERS: build/Dockerfile.bng runs it in the image's build
# stage and lab/pppoe.sh runs it in the lab's container, so the daemon a lab
# proves is compiled the way the shipped one is - the shape of #99, #168,
# #182 and #184 is a lab and a product that disagree about what they run.
#
# THE PIN. accel-ppp-ng is the VyOS fork; upstream accel-ppp has no VPP
# support. #170 ran cb69ccc; this is dd07b23 (2026-10-07), for what came
# after it: RADIUS Message-Authenticator in Access requests and responses
# (the BlastRADIUS mitigation), truncated-PADI and RADIUS-attribute checks. A
# git commit is content-addressed, so the checkout below is the same source on
# every build or it fails; it is never a branch.
#
# THE DELTA (build/accel-ppp-ng.patch), three changes, each measured in
# lab/pppoe.sh and written up in docs/bng.md 0.3:
#   * `disable_fib`, a field only VyOS's patched pppoe plugin has, is not
#     set: stock VPP 26.06 refuses to compile against it (#170). The stock
#     plugin then installs a client_ip/32 of its own from the message, where
#     accel-ppp leaves 0.0.0.0 - a 0.0.0.0/32 via the latest session, which
#     no packet is addressed to;
#   * a VPP session's counters, read from VPP's stats segment by a new
#     `read_stats` session hook: accel-ppp read them from a kernel interface
#     the session does not have, and ended the session at its first interim
#     accounting update;
#   * the PPP unit fd of a VPP session is not closed at teardown: it is the
#     channel's own fd, and closing it (dd07b23's port of upstream PR 334)
#     crashed accel-pppd at the first session that ended.
#
# THE OPTIONS: RADIUS on (the product authenticates against the operator's
# RADIUS), the VPP hooks on, the Linux kernel driver off (VPP carries the
# sessions, not the kernel), SNMP, PostgreSQL logging and Lua off - each is a
# dependency the runtime image would otherwise carry for nothing.
set -eu

ACCEL_REPO=https://github.com/accel-ppp/accel-ppp-ng.git
ACCEL_SHA=dd07b230613e208428ab6b70935071260dd102ca
DEPS="git ca-certificates cmake make gcc libc6-dev libssl-dev libpcre2-dev \
libnl-3-dev libnl-route-3-dev libnl-genl-3-dev"

if [ "${1:-}" = --deps ]; then
  echo $DEPS
  exit 0
fi
DEST=${1:?usage: accel-ppp-build.sh <destdir> | --deps}
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=${ACCEL_SRC:-$(mktemp -d)/accel-ppp-ng}

if [ ! -d "$SRC/.git" ]; then
  git clone -q "$ACCEL_REPO" "$SRC"
fi
git -C "$SRC" checkout -q "$ACCEL_SHA"
git -C "$SRC" checkout -q -- .
git -C "$SRC" apply "$HERE/accel-ppp-ng.patch"
# The pin, checked after the fact: a checkout that fell through to anything
# else is a build of a source nobody reviewed.
[ "$(git -C "$SRC" rev-parse HEAD)" = "$ACCEL_SHA" ]

mkdir -p "$SRC/build"
cd "$SRC/build"
cmake -DBUILD_DRIVER=FALSE -DRADIUS=TRUE -DSHAPER=TRUE -DNETSNMP=FALSE \
      -DLOG_PGSQL=FALSE -DLUA=FALSE -DHAVE_VPP=TRUE -DHAVE_SESSION_HOOKS=TRUE \
      -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release .. >/dev/null
make -j"$(nproc)" >/dev/null
make install DESTDIR="$DEST" >/dev/null
echo "accel-ppp-ng $ACCEL_SHA installed into $DEST"
