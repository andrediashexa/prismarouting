#!/bin/sh
# Runs every lab and reports pass or fail.
#
# A lab "fails" when it prints a failure marker or exits non-zero. Labs that
# only print observations for a human to read are reported as "ran" - claiming
# they passed would be asserting something nobody checked.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
ONLY=${1:-}
pass=0; fail=0; ran=0

# Labs with an explicit verdict in their output.

for f in "$REPO"/lab/*.sh; do
  n=$(basename "$f" .sh)
  # verdict.sh nao e lab, e a biblioteca de asserts que os labs carregam.
  # Contado como "ran", ele deixa o resumo com um numero que nunca zera - e o
  # dia em que um lab de verdade ficar sem verdict, ninguem repara.
  case "$n" in hw-lab|hw-vpp|boot-iso|verdict) continue ;; esac
  [ -n "$ONLY" ] && [ "$ONLY" != "$n" ] && continue

  out=$(timeout 300 sh "$REPO/tools/lab.sh" "$n" 2>&1)
  rc=$?
  # The lab's exit status is the verdict. Grepping its output for the word
  # "error" made FRR's startup chatter look like a failing test.
  if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "LAB PASSED"; then
    printf '  %-18s pass\n' "$n"; pass=$((pass+1))
  elif printf '%s' "$out" | grep -q "LAB FAILED"; then
    printf '  %-18s FAIL\n' "$n"
    printf '%s' "$out" | grep -E "\[FAIL\]" | head -3 | sed 's/^/     /'
    fail=$((fail+1))
  elif [ "$rc" -ne 0 ]; then
    printf '  %-18s FAIL (exit %s)\n' "$n" "$rc"; fail=$((fail+1))
  else
    printf '  %-18s ran (no verdict yet)\n' "$n"; ran=$((ran+1))
  fi
done
echo
echo "  pass=$pass  fail=$fail  ran=$ran"
[ "$fail" -eq 0 ]
