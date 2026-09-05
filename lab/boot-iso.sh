#!/bin/sh
# Boota a ISO em QEMU e mostra o autoteste do console.
#
# O modelo de CPU importa duas vezes. O padrao do QEMU (qemu64) nao tem
# SSE4.2 e o VPP morre com SIGILL em 199ms - um loop de reinicio que nao
# existe no produto. Mas "-cpu max" tambem nao serve: ele emula ate AVX-512
# em software e o boot em TCG fica lento a ponto de estourar o timeout.
# Nehalem e o modelo minimo que atende o requisito real (SSE4.2).
#
# BOOT_MEM em 4096: um sistema live mantem a raiz gravavel inteira em RAM
# (overlay sobre o squashfs). Com 2 GB o matador de OOM leva o VPP - medido,
# com "Out of memory: Killed process (vpp)" no console - e derruba o resto
# junto. Nao era o VPP sendo guloso: ele foi morto com 19 MB residentes.
set -u
ISO=${ISO:-/src/hexarouting.iso}
SRC=${SRC:-/src}

# Uma ISO mais velha que as fontes nao testa o que voce mudou. Ja
# aconteceu duas vezes: o build morreu por falta de debootstrap e o
# boot seguinte aprovou a ISO anterior, com as strings antigas.
if [ "${ALLOW_STALE_ISO:-0}" != 1 ] && [ -f "$ISO" ]; then
  newer=$(find "$SRC/appliance" "$SRC/src" -type f -newer "$ISO" -print 2>/dev/null | head -1)
  if [ -n "$newer" ]; then
    echo "ISO is older than the sources (e.g. $newer)" >&2
    echo "rebuild with appliance/build-iso.sh, or set ALLOW_STALE_ISO=1" >&2
    exit 1
  fi
fi
mkdir -p /mnt/iso /tmp/bf
mount -o loop,ro "$ISO" /mnt/iso
cp /mnt/iso/live/vmlinuz /mnt/iso/live/initrd /tmp/bf/
umount /mnt/iso

# Com KVM o boot leva segundos em vez de minutos, e "-cpu host" entrega os
# conjuntos de instrucao reais - o que o VPP precisa. Sem KVM cai para TCG
# com Nehalem, o minimo que atende o requisito (SSE4.2) sem o custo de
# emular AVX-512 como faria "-cpu max".
if [ -w /dev/kvm ]; then
  ACCEL="-enable-kvm -cpu host"
  echo "acelerando com KVM"
else
  ACCEL="-cpu ${BOOT_CPU:-Nehalem}"
  echo "sem KVM: emulando com ${BOOT_CPU:-Nehalem} (lento)"
fi

timeout "${BOOT_SECS:-420}" qemu-system-x86_64 \
  $ACCEL -m "${BOOT_MEM:-4096}" -smp 2 -nographic -no-reboot \
  -cdrom "$ISO" -kernel /tmp/bf/vmlinuz -initrd /tmp/bf/initrd \
  -append "boot=live components console=ttyS0,115200 systemd.show_status=1" \
  > /tmp/boot.log 2>&1

sed 's/\x1b\[[0-9;]*m//g' /tmp/boot.log > /tmp/clean.log

# O log sai do container: sem isso um boot que falha nao deixa nada para
# olhar, e o diagnostico vira adivinhacao - exatamente o que este script
# existe para evitar.
cp /tmp/clean.log "${LOGCOPY:-/src/boot-console.log}" 2>/dev/null || true

sed -n '/boot self-test/,/=====$/p' /tmp/clean.log | head -40
if grep -q "HexaRouting ready" /tmp/clean.log; then
  exit 0
fi

echo
echo "=========== SELF-TEST DID NOT REPORT READY ==========="
echo "--- o autoteste chegou a rodar? ---"
grep -c "boot self-test" /tmp/clean.log | sed 's/^/  ocorrencias: /'
echo "--- servicos ---"
grep -iE "hexarouting|vpp\.service|frr\.service" /tmp/clean.log | tail -10 | sed 's/^/  /'
echo "--- falhas do systemd ---"
grep -iE "FAILED|Failed to start|Dependency failed" /tmp/clean.log | tail -8 | sed 's/^/  /'
echo "--- ultimas 25 linhas do console ---"
tail -25 /tmp/clean.log | sed 's/^/  /'
echo "--- console completo em ${LOGCOPY:-/src/boot-console.log} ---"
exit 1
