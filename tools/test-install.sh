#!/bin/sh
# The install-to-disk lab and the control that keeps its driver honest.
#
# A FILE and not a Makefile recipe, and that is the whole point of it. This
# body was inline in `make test-install`, passed through
# `$(call ISO_RUN,<body>)` - and `$(call)` splits its argument on the first
# unescaped COMMA. The body had one, in an ordinary English sentence:
#
#   echo "        refused login, so this lab could run its whole session"
#
# so `$(1)` ended at `refused login`, in the middle of a quoted string, and
# the expanded recipe was:
#
#   ... sh -c 'set -e; ...; echo "        refused login'
#   /bin/sh: 1: Syntax error: Unterminated quoted string
#
# `make test-install` could not run at all. tools/release-gate.sh calls it, so
# the gate reported "the medium installed and the installed box failed its
# checks" for a release whose medium had never been installed (#281). The
# message named the box; the fault was a comma in a Makefile.
#
# Escaping that comma would have fixed this one sentence and left the next
# person to notice the rule. A file has no `$(call)` around it, `sh -n` can
# read it, and the Makefile line that invokes it has nothing in it to get
# wrong.
#
# THREE CONDITIONS, because two of them are easy to write so that they cannot
# go red: the control run must FAIL, it must fail AT THE LOGIN, and this
# script must fail if the control ever starts passing. A driver that no longer
# notices a refused login would run a whole session against a login prompt and
# report a pass, which is what TESTING.md calls keeping the driver honest.
set -e

sh /src/lab/install-iso.sh

echo
echo "=========== THE CONTROL: a password the box refuses ==========="
if sh /src/lab/install-iso.sh wrong-password > /tmp/wrong-password.log 2>&1; then
  echo "  FAIL: the control PASSED. The driver no longer notices a"
  echo "        refused login, so this lab could run its whole session"
  echo "        against a login prompt and report a pass."
  tail -30 /tmp/wrong-password.log | sed "s/^/        | /"
  exit 1
fi

if grep -q "ANSWERED and REFUSED the credentials" /tmp/wrong-password.log; then
  echo "  [ ok ] the control failed at the login, and the driver said so"
else
  echo "  FAIL: the control failed, but NOT at the login. A driver whose"
  echo "        failure mode is a hang proves nothing."
  tail -30 /tmp/wrong-password.log | sed "s/^/        | /"
  exit 1
fi
