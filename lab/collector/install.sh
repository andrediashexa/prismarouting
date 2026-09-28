#!/bin/sh
# install.sh - put the lab collector (#491) on a Debian host, as root.
#
#   sh lab/collector/install.sh
#
# What it serves: IPFIX on udp/4739, syslog on tcp/601, the page on
# http/8080, one SQLite file under /var/lib/prisma-collector. Point a box at
# it under `nat` > `cgnat`:
#
#   logging udp <this host> src <the box's address on that network> port 4739
#   logging syslog <this host> port 601 enterprise <your PEN>
#
# The host has to be reachable from the box's DATAPLANE for IPFIX over UDP
# (docs/logging.md, "Where"): on the #475 bench it has a leg on the outside
# bridge for exactly that.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
install -D -m 0755 "$HERE/prisma_collector.py" /usr/local/lib/prisma-collector/prisma_collector.py
install -D -m 0644 "$HERE/prisma-collector.service" /etc/systemd/system/prisma-collector.service
mkdir -p /var/lib/prisma-collector
systemctl daemon-reload
systemctl enable --now prisma-collector
systemctl restart prisma-collector
echo "prisma-collector: http://$(hostname -I | awk '{print $1}'):8080"
