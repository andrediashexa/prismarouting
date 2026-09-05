#!/bin/sh
# Boota a ISO em QEMU e mostra o autoteste do console.
#
# O modelo de CPU importa duas vezes. O padrao do QEMU (qemu64) nao tem
# SSE4.2 e o VPP morre com SIGILL em 199ms - um loop de reinicio que nao
# existe no produto. Mas "-cpu max" tambem nao serve: ele emula ate AVX-512
# em software e o boot em TCG fica lento a ponto de estourar o timeout.
# Nehalem e o modelo minimo que atende o requisito real (SSE4.2).
#
# BOOT_MEM em 2048 e proposital: a maquina de build tambem segura o cache do
# squashfs, e 3 GB de VM somados a isso derrubavam o monitor de memoria. Dois
# gigas sobram para um boot de verificacao.
set -u
ISO=${ISO:-/src/hexarouting.iso}
mkdir -p /mnt/iso /tmp/bf
mount -o loop,ro "$ISO" /mnt/iso
cp /mnt/iso/live/vmlinuz /mnt/iso/live/initrd /tmp/bf/
umount /mnt/iso

timeout "${BOOT_SECS:-420}" qemu-system-x86_64 \
  -cpu "${BOOT_CPU:-Nehalem}" -m "${BOOT_MEM:-2048}" -smp 2 -nographic -no-reboot \
  -cdrom "$ISO" -kernel /tmp/bf/vmlinuz -initrd /tmp/bf/initrd \
  -append "boot=live components console=ttyS0,115200 systemd.show_status=1" \
  > /tmp/boot.log 2>&1

sed 's/\x1b\[[0-9;]*m//g' /tmp/boot.log > /tmp/clean.log

# O log sai do container: sem isso um boot que falha nao deixa nada para
# olhar, e o diagnostico vira adivinhacao - exatamente o que este script
# existe para evitar.
cp /tmp/clean.log "${LOGCOPY:-/src/boot-console.log}" 2>/dev/null || true

sed -n '/autoteste de boot/,/=====$/p' /tmp/clean.log | head -40
if grep -q "HexaRouting pronto" /tmp/clean.log; then
  exit 0
fi

echo
echo "=========== AUTOTESTE NAO REPORTOU PRONTO ==========="
echo "--- o autoteste chegou a rodar? ---"
grep -c "autoteste de boot" /tmp/clean.log | sed 's/^/  ocorrencias: /'
echo "--- servicos ---"
grep -iE "hexarouting|vpp\.service|frr\.service" /tmp/clean.log | tail -10 | sed 's/^/  /'
echo "--- falhas do systemd ---"
grep -iE "FAILED|Failed to start|Dependency failed" /tmp/clean.log | tail -8 | sed 's/^/  /'
echo "--- ultimas 25 linhas do console ---"
tail -25 /tmp/clean.log | sed 's/^/  /'
echo "--- console completo em ${LOGCOPY:-/src/boot-console.log} ---"
exit 1
