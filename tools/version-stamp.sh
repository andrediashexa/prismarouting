#!/bin/sh
# Imprime o carimbo de versao desta arvore.
#
# Uma copia deste calculo no build-iso.sh e outra no install.sh divergiriam, e
# a divergencia so apareceria numa ISO que mente sobre a propria versao. Entao
# e um lugar so.
#
# O ponto delicado e o git recusar o repositorio: dentro do container de build
# ele responde "detected dubious ownership" porque o bind mount pertence a
# outro usuario. Engolir esse erro faz uma arvore SUJA sair carimbada como
# release limpa - o oposto do que este arquivo existe para garantir. Entao o
# fracasso vira "unverified", que e visivel.
set -u
REPO=${1:-$(cd "$(dirname "$0")/.." && pwd)}

version=$(cat "$REPO/VERSION" 2>/dev/null || echo unknown)

if ! command -v git >/dev/null 2>&1; then
  printf '%s (no git at build time)\n' "$version"
  exit 0
fi

git config --global --add safe.directory "$REPO" >/dev/null 2>&1 || true

if ! git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  printf '%s (not a git tree)\n' "$version"
  exit 0
fi

commit=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo "")
if [ -z "$commit" ]; then
  printf '%s (unverified)\n' "$version"
  exit 0
fi

if git -C "$REPO" diff --quiet HEAD 2>/dev/null; then
  printf '%s (%s)\n' "$version" "$commit"
else
  printf '%s (%s-dirty)\n' "$version" "$commit"
fi
