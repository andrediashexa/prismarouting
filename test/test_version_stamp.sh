#!/bin/sh
# O carimbo de versao degrada de forma VISIVEL?
#
# O modo de falha que este teste existe para impedir ja aconteceu: dentro do
# container de build o git responde "dubious ownership", o calculo engolia o
# erro, e uma arvore suja saia carimbada como release limpa.
set -u
fail=0
ok()  { echo "  $*"; }
bad() { echo "  FALHA: $*"; fail=$((fail+1)); }
STAMP=$(cd "$(dirname "$0")/.." && pwd)/tools/version-stamp.sh

echo "=== arvore que nao e git ==="
d=$(mktemp -d); echo "0.4.2" > "$d/VERSION"
out=$(sh "$STAMP" "$d")
case "$out" in
  "0.4.2 (not a git tree)") ok "$out" ;;
  *) bad "esperava 'not a git tree', obtive '$out'" ;;
esac

echo "=== arvore git limpa ==="
git -C "$d" init -q 2>/dev/null
git -C "$d" config user.email t@t; git -C "$d" config user.name t
git -C "$d" add -A >/dev/null 2>&1; git -C "$d" commit -qm x >/dev/null 2>&1
out=$(sh "$STAMP" "$d")
case "$out" in
  "0.4.2 ("*")") [ "${out#*dirty}" = "$out" ] && ok "$out" || bad "limpa marcada como suja: $out" ;;
  *) bad "esperava versao com hash, obtive '$out'" ;;
esac

echo "=== arvore suja tem que dizer que esta suja ==="
echo "0.4.3" > "$d/VERSION"
out=$(sh "$STAMP" "$d")
case "$out" in
  *dirty*) ok "$out" ;;
  *) bad "arvore suja saiu como '$out': uma ISO assim se declara release sem ser" ;;
esac

echo "=== sem git no PATH ==="
# Esvaziar o PATH tira o proprio sh junto. O que se quer e um PATH sem git,
# nao um PATH sem shell.
empty=$(mktemp -d)
for t in sh cat dirname mktemp printf; do
  p=$(command -v "$t" 2>/dev/null) && ln -sf "$p" "$empty/$t"
done
out=$(PATH="$empty" sh "$STAMP" "$d")
case "$out" in
  *"no git at build time"*) ok "$out" ;;
  *) bad "sem git deveria dizer isso, obtive '$out'" ;;
esac

rm -rf "$d" "$empty"
if [ "$fail" -eq 0 ]; then echo; echo "TODOS OS TESTES PASSARAM"; exit 0; fi
echo; echo "$fail FALHAS"; exit 1
