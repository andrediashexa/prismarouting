#!/bin/sh
# Opens the Prisma Routing VTY.
#
#   sh tools/vty.sh                 interactive session
#   sh tools/vty.sh -c "command"    one command, then exit
#
# The VTY runs where the dataplane is: inside the container holding VPP.
# vppctl speaks over a socket that lives in there.
set -eu
CONTAINER=${CONTAINER:-hexa-vpp}

if ! docker ps >/dev/null 2>&1; then
  exec sg docker -c "CONTAINER='$CONTAINER' '$0' $*"
fi

docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" || {
  # Naming the wrong command is worse than naming none: "sh tools/lab.sh hw-lab"
  # configures addresses and routes on the host and does not create this
  # container, so following the hint produced the same error again.
  echo "container '$CONTAINER' is not running." >&2
  echo "  make hw-up     # create it" >&2
  echo "  make hw-vpp    # start VPP inside it" >&2
  echo "  make hw-down   # when you are finished (it holds ~4 GB)" >&2
  exit 1
}

# -t only when there is a terminal: without that, -c mode breaks in a pipe
TTY=""
[ -t 0 ] && TTY="-it"
exec docker exec $TTY "$CONTAINER" python3 /src/src/hexash/hexash.py "$@"
