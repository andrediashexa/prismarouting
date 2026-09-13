# Shared lab setup. Sourced, not executed.

# Puts the vat2 plugin where vat2 looks for it, which is where install.sh puts
# it on a real box.
#
# The labs used to pass "vat2 -p /vat2_plugins" instead. That proves the client
# works in a configuration no appliance ever runs, and this project has already
# shipped one defect of exactly that shape: hexash had never been started from
# its installed location, so a missing config.py reached every ISO. Any lab that
# drives the binary API calls this.
use_binary_api() {
  install -D -m 0644 /vat2_plugins/bpa_test_plugin_bpa_plugin.so \
    /usr/lib/x86_64-linux-gnu/vat2_plugins/bpa_test_plugin_bpa_plugin.so
}

# Start FRR the way frrinit.sh does on a real box.
#
# Every lab that needed FRR started zebra and bgpd by hand, and nothing else.
# That is not the FRR the product ships: frrinit.sh always starts mgmtd and
# staticd as well, and on FRR 10 those two own statements a router cannot do
# without - a static route and a prefix-list are configured THROUGH mgmtd and
# served by staticd. Without them vtysh answers "mgmtd is not running" to every
# one of them, which is how a statement can be typed, accepted, reported as
# committed and never exist.
#
# lab/bgp-policy.sh worked this out and said so in a comment of its own. That
# knowledge then sat in one lab while six others went on starting two daemons,
# so `ip route` had never once been asserted end to end in this repository -
# the labs could not hold one. A lab environment that differs from the product's
# is the shape of #99, #168, #182 and #184, and the fix is the same each time:
# one place that knows, used by everybody.
#
#   start_frr                  zebra, mgmtd, staticd
#   start_frr bgpd             and bgpd
#   start_frr bgpd ospfd       and more
#
# Config files must already exist; this does not write them, because what each
# daemon is told is the lab's own business. mgmtd and staticd take no -f at
# all - they read the integrated configuration themselves - and passing one is
# an "invalid option" that leaves the daemon not running with nothing obviously
# wrong.
# The RPKI module's path, or empty. Both layouts, no architecture in either.
_rpki_module() {
  ls /usr/lib/*/frr/modules/bgpd_rpki.so /usr/lib/frr/modules/bgpd_rpki.so \
     2>/dev/null | head -1
}

start_frr() {
  /usr/lib/frr/zebra -d -A 127.0.0.1 -f /etc/frr/zebra.conf 2>/dev/null
  /usr/lib/frr/mgmtd -d -A 127.0.0.1 2>/dev/null
  /usr/lib/frr/staticd -d -A 127.0.0.1 2>/dev/null
  for _d in "$@"; do
    [ -f "/etc/frr/$_d.conf" ] || printf 'hostname hexarouting\n' > "/etc/frr/$_d.conf"
    chown frr:frr "/etc/frr/$_d.conf" 2>/dev/null
    # bgpd carries the RPKI module when it is installed; without the module
    # bgpd REFUSES TO START, so it is only asked for when it is there.
    #
    # Found by glob rather than by a path. The first version of this looked in
    # /usr/lib/frr/modules, which is where the daemons live but not the
    # modules: frr-rpki-rtrlib ships
    # /usr/lib/x86_64-linux-gnu/frr/modules/bgpd_rpki.so, so bgpd started
    # WITHOUT the module and lab/bgp-policy.sh failed seven checks - quietly,
    # because a bgpd with no RPKI answers every RPKI command as if the feature
    # were simply off. Hardcoding the multiarch triplet would break the same
    # way on arm64.
    if [ "$_d" = bgpd ] && [ -n "$(_rpki_module)" ]; then
      /usr/lib/frr/bgpd -d -A 127.0.0.1 -M rpki -f /etc/frr/bgpd.conf 2>/dev/null
    else
      "/usr/lib/frr/$_d" -d -A 127.0.0.1 -f "/etc/frr/$_d.conf" 2>/dev/null
    fi
  done
  # mgmtd's front-end socket is what vtysh connects to for these statements,
  # so a lab that raced it would see the very symptom this function exists to
  # remove.
  _n=0
  until [ -S /var/run/frr/mgmtd_fe.sock ] || [ "$_n" -ge 10 ]; do
    sleep 1; _n=$((_n + 1))
  done
}

# The startup configuration a lab boots VPP with, with or without workers.
#
#   startup_conf                 lab/startup-test.conf
#   startup_conf lcp             lab/startup-lcp.conf
#   startup_conf test workers    the same, plus workers, always
#   LAB_WORKERS=1 sh tools/lab.sh <lab>    the same, plus workers, for any lab
#   LAB_WORKERS=4 sh tools/lab.sh <lab>    and with four of them
#
# This exists because of #199, and #199 is the reason to read the rest of this
# comment. Every lab that asserted a packet LEAVING the box ran single-threaded,
# while hexarouting-tune gives every appliance with more than one core two or
# more workers. On a worker box the node on the ip4-unicast arc is the handoff
# node, not the translation node, so the translated packet was sent to
# error-drop and nothing left. Every counter said it was working, and the one
# lab that did run with workers asserted only counters.
#
# A small box really does run everything on the main thread - the tuner says so
# in as many words - so single-threaded is not a lab-only configuration and the
# answer is not "make everything use workers". The answer is that a lab which
# asserts forwarding MUST be runnable both ways, and tools/run-all-labs.sh runs
# those labs twice.
#
# Derived from the base file rather than kept beside it as a copy. The copy
# that used to exist, lab/startup-workers.conf, differed from lab/startup-test.conf
# by exactly the cpu stanza - and two files that must differ by exactly one
# stanza are two files that will one day differ by two.
# How many workers the box ACTUALLY has, which is a different question from what
# LAB_WORKERS asked for and is the one a lab wants answered. Three labs used to
# gate their worker sections on `[ "${LAB_WORKERS:-0}" = 1 ]`, which is #229 one
# layer up: now that a count above one really starts that many workers,
# `LAB_WORKERS=4` would give a box with four workers and skip every section that
# is about workers, silently. Read the box instead.
box_workers() {
  _c=$(vppctl show threads 2>/dev/null | grep -c 'vpp_wk_')
  echo "${_c:-0}"
}

startup_conf() {
  _base=${1:-test}
  # LAB_SRC so the three cases below can be read without a container. It is
  # /src everywhere a lab actually runs, and the same arrangement
  # HEXA_SELFTEST_ROOT and HEXA_MEMINFO already have, for the same reason: a
  # rule about how an argument is read is checkable by arithmetic and should
  # not need a VM to check it.
  _src=${LAB_SRC:-/src}/lab/startup-$_base.conf
  [ -f "$_src" ] || { echo "startup_conf: no $_src" >&2; return 1; }
  # LAB_WORKERS IS A COUNT, and it was a 0/1 flag with the name of a count.
  # `LAB_WORKERS=2` is not `1`, so it fell to the else and the box came up with
  # no workers at all - a lab that then measured a single-threaded box while
  # whoever ran it believed they had asked for two. It cost a run on the day
  # the stride lab was repaired, verifying the very case the repair was for
  # (#229).
  #
  # 0 and 1 keep their meanings exactly, because tools/run-all-labs.sh passes
  # those two and nothing else: 0 is off, 1 is on with LAB_WORKER_COUNT. Above
  # 1 the number is the count, which is what the name says. Anything else is
  # refused rather than quietly read as off.
  _want=${LAB_WORKERS:-0}
  case "$_want" in
    ''|*[!0-9]*)
      echo "startup_conf: LAB_WORKERS='$_want' is not a number of workers." >&2
      echo "  0 or unset for none, 1 for the default count, N for N." >&2
      return 1 ;;
  esac
  _n=0
  if [ "${2:-}" = workers ]; then
    _n=${LAB_WORKER_COUNT:-2}
  elif [ "$_want" = 1 ]; then
    _n=${LAB_WORKER_COUNT:-2}
  elif [ "$_want" -gt 1 ]; then
    _n=$_want
  fi
  if [ "$_n" -gt 0 ]; then
    _out=/tmp/startup-$_base-workers.conf
    {
      cat "$_src"
      printf '\ncpu {\n  workers %s\n}\n' "$_n"
    } > "$_out"
    echo "$_out"
  else
    echo "$_src"
  fi
}
