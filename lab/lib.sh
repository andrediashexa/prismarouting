# Shared lab setup. Sourced, not executed.

# Puts the vat2 plugin where vat2 looks for it, which is where install.sh puts
# it on a real box.
#
# The labs used to pass "vat2 -p /vat2_plugins" instead. That proves the client
# works in a configuration no appliance ever runs, and this project has already
# shipped one defect of exactly that shape: prismash had never been started from
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

# " -M snmp" when frr-snmp is installed, or nothing (#502): the product loads
# it on zebra, bgpd, ospfd, ospf6d and isisd (appliance/install.sh), and a lab
# that did not would be a control plane the box does not run. Asked for only
# when it is there, for the reason -M rpki is: a daemon told to load a module
# it cannot find refuses to start.
_snmp_opt() {
  # By what ls prints and not by its status: ls fails when either layout is
  # missing, and one always is - the first version of this never loaded it.
  [ -n "$(ls /usr/lib/*/frr/modules/bgpd_snmp.so \
            /usr/lib/frr/modules/bgpd_snmp.so 2>/dev/null | head -1)" ] \
    && printf ' -M snmp'
}

# not a unit: starts zebra, mgmtd and staticd by their absolute paths under /usr/lib/frr and writes /etc/frr, so no PATH stub reaches it; every FRR lab drives it in a container
start_frr() {
  /usr/lib/frr/zebra -d -A 127.0.0.1$(_snmp_opt) -f /etc/frr/zebra.conf 2>/dev/null
  /usr/lib/frr/mgmtd -d -A 127.0.0.1 2>/dev/null
  /usr/lib/frr/staticd -d -A 127.0.0.1 2>/dev/null
  for _d in "$@"; do
    [ -f "/etc/frr/$_d.conf" ] || printf 'hostname prisma-routing\n' > "/etc/frr/$_d.conf"
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
      /usr/lib/frr/bgpd -d -A 127.0.0.1 -M rpki$(_snmp_opt) -f /etc/frr/bgpd.conf 2>/dev/null
    else
      "/usr/lib/frr/$_d" -d -A 127.0.0.1$(_snmp_opt) -f "/etc/frr/$_d.conf" 2>/dev/null
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

# wait_vpp [log] - wait for the dataplane, and say so when it never arrives.
#
#   vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
#   wait_vpp
#
# Every lab that starts VPP has to wait for it. What a lab does when that wait
# runs out is what the whole suite reads: tools/run-all-labs.sh classifies a
# capture by grepping it for "VPP did not start", and a lab that prints it is
# counted NO VPP, which tells the reader to look at the machine. A lab that
# does not print it walks on with no box under it, every vppctl is refused, and
# it reaches its verdict with every value empty - which the suite reports as
# FAIL, sending the reader to the product for a machine that never started a
# dataplane.
#
# Measured rather than reasoned about (#261). lab/frag.sh was given a
# startup.conf VPP refuses. It waited its thirty seconds, printed
# "Could not find VPP CLI socket" forty times and ended "LAB FAILED: 14
# check(s)", and lab_no_vpp() run verbatim over that capture answered FAIL.
#
# The classifier's fallback cannot cover this and is not meant to. It looks for
# "connect: Connection refused", which is what a socket with NOTHING BEHIND IT
# says: a VPP that started, created its socket and then died. A VPP that never
# started leaves no socket to refuse, so this line is the only thing that
# classifies a dataplane that never came.
#
# A lab that starts VPP MUST therefore guard the start, and the guard MUST
# print this exact line. test/test_lab_workers.sh holds both halves, including
# that the line printed here is the line the classifier looks for.
#
# THE THIRTY SECONDS ARE UNCHANGED. #253 measured the release-run failure the
# wait was blamed for and found VPP answering well before it expired, so
# nothing measured supports moving it. LAB_VPP_WAIT exists so the check can
# exercise the expiry without sleeping through it, the same arrangement LAB_SRC
# already has in startup_conf below: a rule about what happens when a wait runs
# out should not need a container and thirty seconds to check.
#
# The ten labs #261 found are converted to this; the forty start sites that
# already print the line keep their own copy, deliberately. They already
# produce the verdict the suite reads, so converting them would move the input
# of every lab in the suite in order to change no lab's verdict, and each would
# then have to be shown not to have moved - the trade vc() makes for the same
# reason. What the check guards is the guarantee and not the spelling: a start
# site is guarded if it reaches this helper or prints the line itself. The copy
# is not free and the drift is already visible, in tail -20 against tail -25
# and /tmp/vpp.log against /tmp/vpp-$1.log, which is the argument for anything
# written next using this instead.
wait_vpp() {
  _log=${1:-/tmp/vpp.log}
  _n=0
  until vppctl show version >/dev/null 2>&1 || [ "$_n" -ge "${LAB_VPP_WAIT:-30}" ]; do
    sleep 1; _n=$((_n + 1))
  done
  vppctl show version >/dev/null 2>&1 && return 0
  # Exits rather than returning, because there is nothing for the caller to do
  # with the answer. A lab that carries on without a dataplane produces a page
  # of red checks about empty strings, and the one fact worth having is the log
  # underneath this line.
  echo "VPP did not start"
  [ -r "$_log" ] && tail -20 "$_log"
  exit 1
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
# while prisma-routing-tune gives every appliance with more than one core two or
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
  # PRISMA_SELFTEST_ROOT and PRISMA_MEMINFO already have, for the same reason: a
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

# vc <vppctl arguments> - vppctl, with the carriage returns taken off.
#
# vppctl ends every line it prints with CR+LF. awk's field splitting and sh's
# IFS both count only space, tab and newline as whitespace, so the CR stays
# attached to the LAST FIELD of every line: a value taken from the end of a
# line comes out as "600<CR>" while the terminal shows "600".
#
# It fails in the direction that hides, and which half survives is not a
# guess. Measured under dash, which is the shell tools/lab.sh runs the labs
# with:
#
#   [ "600<CR>" -eq 600 ]     true     the number parser calls isspace(),
#                                      and isspace('\r') is true
#   [ "600<CR>" = "600" ]     FALSE
#   case 600<CR> in 600)      no match
#   grep -x 600               no match
#
# So every arithmetic comparison survives it and every string comparison does
# not. A needle that can never match makes "this must appear" red on a correct
# box, which somebody notices, and "this must NOT appear" green on a broken
# one, which nobody does.
#
# This is deliberately NOT wrapped around every vppctl call in the labs, and
# the reason was measured rather than assumed (#249). Of the 280 assertion
# sites that read a vppctl-derived value, the needle is a literal string in
# all but a handful, and a literal needle has no CR to lose: it matches inside
# "...bpa-in2out<CR>" like any other substring. Putting roughly a thousand
# call sites behind a filter to repair the few that EXTRACT a value would move
# the input of every lab in order to change no lab's verdict, and each of them
# would then have to be shown not to have moved.
#
# The strip therefore belongs where the value is extracted. The shapes that
# carry a CR out of a pipeline are a closed list - the last awk field, a whole
# line, a whole-line grep, a sed that prints the rest of a line - and
# test/test_lab_crlf.py goes red when a lab writes one of them over vppctl
# without stripping. The helper is the one place that knows; the suite is what
# stops the next lab reintroducing it in silence.
#
# Found by #239 in lab/frag.sh, swept by #249.
vc() { vppctl "$@" | tr -d '\r'; }
