#!/bin/sh
# Roda um lab no container de build.
#
#   sh tools/lab.sh                lista os labs
#   sh tools/lab.sh datapath       roda lab/datapath.sh
#   sh tools/lab.sh boot-iso       boota a ISO (precisa dela construida)
#
# Nesta maquina o grupo docker so vale em login novo, entao reexecutamos via
# "sg docker" quando o socket nega acesso.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=${IMAGE:-hexarouting-dev:26.06}

if [ $# -eq 0 ]; then
  echo "labs disponiveis:"
  for f in "$REPO"/lab/*.sh; do
    n=$(basename "$f" .sh)
    d=$(sed -n '2s/^# //p' "$f")
    printf "  %-16s %s\n" "$n" "$d"
  done
  exit 0
fi

LAB=$1
[ -f "$REPO/lab/$LAB.sh" ] || { echo "lab '$LAB' nao existe" >&2; exit 1; }

if ! docker ps >/dev/null 2>&1; then
  exec sg docker -c "IMAGE='$IMAGE' '$0' '$LAB'"
fi

# --device=/dev/kvm quando existir: boot de ISO em segundos em vez de minutos
KVM=""
[ -w /dev/kvm ] && KVM="--device=/dev/kvm"

exec docker run --rm --privileged $KVM --memory="${LAB_MEM:-5g}" \
  -v "$REPO:/src" -v "$HOME/.ssh:/root/.ssh:ro" -w /src "$IMAGE" \
  sh -c "apt-get update -qq >/dev/null 2>&1
         apt-get install -y -qq qemu-system-x86 openssh-client iptables tcpdump >/dev/null 2>&1
         sh /src/lab/$LAB.sh"
