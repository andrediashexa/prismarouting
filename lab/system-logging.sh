#!/bin/sh
# system logging and monitor log: where the box's record of events goes, and
# how it is followed (#588 phase 3).
#
#   sh tools/lab.sh system-logging       two collectors configured from the
#                                        VTY, one UDP and one TCP; the events
#                                        reach each at its severity, a TCP
#                                        collector away loses nothing, and
#                                        `monitor log` follows the record
#   sh tools/lab.sh system-logging off   the record cannot be kept - every
#                                        check on what reaches a collector or
#                                        the monitor MUST fail
#
# THE SENDER IS THE PRODUCT'S. On a box it is prisma-routing-events-watch,
# `prismash --watch-events`, forwarding between its ticks; here `prismash
# --watch-events once` is one pass - a tick and a forward - run after each
# thing the lab does, by the same code, from the same events.log, with the
# same positions file. Each `once` is a new process, so each pass is also a
# restart of the watcher, which is what the outage below leans on: what
# waited for a collector is sent by the next process, from the record.
#
# THE COLLECTORS ARE STAND-INS: lab/syslog_sink.py, a UDP socket and a TCP
# one cutting RFC 6587 octet-counted frames, each writing a message a line.
# 127.0.0.1 and 127.0.0.2 are both the container's loopback, two addresses
# because the box keeps one destination per address.
#
# The off run points the record at a path nothing can write, as
# lab/show-log.sh's does. The configuration is committed and read back
# as before and the collectors listen: those controls stay green.
#
# OFF-RED-AT-LEAST: 10
#
# A FLOOR, measured on the off run through tools/run-all-labs.sh (#588 phase
# 3, 2026-10-07): 10 of its 20 checks red. Green in that run and rightly: the
# four controls; the three that say nothing was sent that should not be (no
# commit at the warning collector, no frame it could not cut, nothing from
# before the first pass) and monitor log's "nothing from before it
# started", each as true of a box that sends nothing; and `kind weather`
# refused, which the VTY refuses whether or not anything was kept. See lab/bypass.sh for why it is a
# floor and not a target.
set -u
MODE=${1:-on}

mkdir -p /run/vpp /vpp_plugins /var/log/prisma-routing /etc/prisma-routing \
         /run/prisma-routing/state
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
. /src/lab/lib.sh   # startup_conf, use_binary_api, wait_vpp
. /src/lab/verdict.sh
use_binary_api
export PRISMA_ROLLBACK=/tmp/rollback PRISMA_CONFIG=/tmp/prisma-routing.conf
export PRISMA_EVENTS=/var/log/prisma-routing/events.log
export PRISMA_EVENTS_STATE=/var/log/prisma-routing/events.state
export PRISMA_WATCH_STATE=/var/log/prisma-routing/events-watch.state
export PRISMA_LOGGING_CONF=/etc/prisma-routing/logging.conf
export PRISMA_LOGGING_FWD_STATE=/var/log/prisma-routing/events-forward.state
export PRISMA_LOGGING_STATUS=/run/prisma-routing/state/logging
export PRISMA_WTMP=/tmp/no-wtmp PRISMA_BTMP=/tmp/no-btmp
# No doors daemon: every write is local, as on a box whose daemon is not
# listening.
export PRISMA_DOORS=/tmp/no-doors.sock
rm -rf /tmp/rollback "$PRISMA_EVENTS" "$PRISMA_EVENTS.1" "$PRISMA_EVENTS_STATE" \
       "$PRISMA_WATCH_STATE" "$PRISMA_LOGGING_CONF" "$PRISMA_LOGGING_FWD_STATE" \
       "$PRISMA_LOGGING_STATUS" /tmp/udp.log /tmp/tcp.log
if [ "$MODE" = off ]; then
  echo "  MODE=off: the record of events points at a path nothing can write."
  export PRISMA_EVENTS=/proc/no-such-dir/events.log
fi
HX="python3 /src/src/prismash/prismash.py"
pass_() { $HX --watch-events once 2>&1 | tail -1; }
say() {   # say <source>[=<kind>] <severity> <text> - an event, as the host's scripts write one
  python3 /src/src/prismash/events.py record "$@"
}
lines() { [ -f "$1" ] && wc -l < "$1" || echo 0; }

echo "=========== the box, and two collectors ==========="
vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp
python3 /src/lab/syslog_sink.py udp 127.0.0.1 5514 /tmp/udp.log &
UDP=$!
python3 /src/lab/syslog_sink.py tcp 127.0.0.2 5601 /tmp/tcp.log &
TCP=$!
sleep 1

echo
echo "=========== 1. system > logging, from the VTY ==========="
# A name is refused at the prompt, and a batch with a refusal in it commits
# nothing - so it is a batch of its own.
BADNAME=$($HX -c configure -c system -c logging -c "host syslog.example" 2>&1)
echo "$BADNAME" | sed 's/^/  /'
CFG=$($HX -c configure -c system -c logging -c "host 127.0.0.1 port 5514" \
  -c "host 127.0.0.2 port 5601 protocol tcp severity warning" \
  -c "host 127.0.0.3 vrf nowhere" -c "no host 127.0.0.3" \
  -c exit -c exit -c commit 2>&1)
echo "$CFG" | sed 's/^/  /'
RUN=$($HX -c "show running-config" 2>&1 | sed -n '/^system/,/^ exit/p')
echo "$RUN" | sed 's/^/  /'
SHOW0=$($HX -c "show system logging" 2>&1)
echo "$SHOW0" | sed 's/^/  /'

echo
echo "=========== 2. the first pass: each destination starts at the end ==========="
say host=gave-up warning "an event from before the destinations were read"
echo "  $(pass_)"
U0=$(lines /tmp/udp.log); T0=$(lines /tmp/tcp.log)
echo "  udp: $U0 message(s), tcp: $T0"

echo
echo "=========== 3. events, at two severities ==========="
$HX -c "configure" -c "nat" -c "cgnat" -c "pool alpha" \
    -c "prefix-inside 100.64.0.0/22" -c "prefix-outside 198.19.24.0/24" \
    -c "block-size 512" -c "exit" -c "exit" -c "exit" \
    -c 'commit comment "first pool"' 2>&1 | tail -1 | sed 's/^/  /'
say host=gave-up warning "the dataplane was given up on, as a lab says it"
echo "  $(pass_)"
sleep 1
UDP1=$(cat /tmp/udp.log 2>/dev/null); TCP1=$(cat /tmp/tcp.log 2>/dev/null)
echo "  -- udp"; echo "$UDP1" | sed 's/^/    /'
echo "  -- tcp"; echo "$TCP1" | sed 's/^/    /'
SHOW1=$($HX -c "show system logging" 2>&1)
echo "$SHOW1" | sed 's/^/  /'

echo
echo "=========== 4. the TCP collector goes away, and comes back ==========="
kill "$TCP" 2>/dev/null; wait "$TCP" 2>/dev/null
say host=gave-up warning "said while the TCP collector was away, one"
say host=gave-up warning "said while the TCP collector was away, two"
echo "  $(pass_)"
SHOW2=$($HX -c "show system logging" 2>&1)
echo "$SHOW2" | sed 's/^/  /'
python3 /src/lab/syslog_sink.py tcp 127.0.0.2 5601 /tmp/tcp.log &
TCP=$!
sleep 1
echo "  $(pass_)"
echo "  $(pass_)"
sleep 1
TCP2=$(cat /tmp/tcp.log 2>/dev/null)
AWAY=$(grep -c "while the TCP collector was away" /tmp/tcp.log 2>/dev/null)
echo "  the TCP collector has $AWAY of the two said while it was away"

echo
echo "=========== 5. monitor log: the record as it is written ==========="
$HX -c "monitor log seconds 6" > /tmp/monitor.out 2>&1 &
MON=$!
sleep 2
say host=gave-up warning "said while monitor log ran"
$HX -c "configure" -c "nat" -c "cgnat" -c "pool beta" \
    -c "prefix-inside 100.64.8.0/22" -c "prefix-outside 198.19.25.0/24" \
    -c "block-size 512" -c "exit" -c "exit" -c "exit" -c "commit" 2>&1 \
  | tail -1 | sed 's/^/  /'
wait "$MON"
MON1=$(cat /tmp/monitor.out)
echo "$MON1" | sed 's/^/  /'
MON2=$($HX -c "monitor log last 2 severity warning seconds 1" 2>&1)
echo "$MON2" | sed 's/^/  /'
MON3=$($HX -c "monitor log kind weather" 2>&1)
echo "  -- kind weather (refused)"; echo "$MON3" | sed 's/^/    /'

kill "$UDP" "$TCP" 2>/dev/null

echo
echo "=========== VERDICT ==========="
# The controls: what the off run keeps green.
expect_in "control: the destinations are in the running configuration" \
  "  host 127.0.0.2 port 5601 protocol tcp severity warning" "$RUN"
expect_in "control: and the UDP one, its defaults left out" \
  "  host 127.0.0.1 port 5514" "$RUN"
expect_said "control: a name is refused - an address, never a name" \
  "logging host 'syslog.example': not an IP address" "$BADNAME"
expect_said "control: show system logging lists them before the watcher said anything" \
  "127.0.0.2:5601 tcp warning and above" "$SHOW0"
# What reached the collectors.
expect "nothing from before the first pass was sent: history is not news" \
  sh -c "[ '$U0' = 0 ] && [ '$T0' = 0 ] && ! grep -q 'from before' /tmp/udp.log /tmp/tcp.log"
expect_in "the commit reached the UDP collector as RFC 5424, daemon.notice" \
  "<29>1 " "$UDP1"
expect_in "with its kind as MSGID and its fields as structured data" \
  'COMMIT [event@67033 source="vty" kind="commit"' "$UDP1"
expect_in "a warning reached the UDP collector, daemon.warning" \
  "<28>1 " "$UDP1"
expect_in "and the TCP one, octet-counted, whole" \
  "the dataplane was given up on, as a lab says it" "$TCP1"
expect_not_in "the TCP collector took warning and above: no commit" \
  "COMMIT" "$TCP1"
expect_not_in "and no frame it could not cut" "BAD FRAME" "$TCP2"
expect_said "show system logging says each is sending" "sending" "$SHOW1"
expect_said "the TCP collector away is said not reached, with why" \
  "not reached: connection refused" "$SHOW2"
expect "and what waited reached it when it was back: both, once each" \
  sh -c "[ '$AWAY' = 2 ]"
expect_in "monitor log said what was written while it ran" \
  "said while monitor log ran" "$MON1"
expect_said "and the commit, by whom" "configuration committed (by" "$MON1"
expect_not_in "and nothing from before it started" \
  "the dataplane was given up on" "$MON1"
expect_in "monitor log last 2 severity warning: the last two warnings first" \
  "said while monitor log ran" "$MON2"
expect_in "monitor log kind weather: refused, with the kinds there are" \
  "kind is one of" "$MON3"
verdict
