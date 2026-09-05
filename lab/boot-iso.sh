#!/bin/sh
# Boota a ISO em QEMU e mostra o autoteste do console.
#
# -cpu max nao e detalhe: o modelo padrao do QEMU (qemu64) nao tem SSE4.2, e
# o VPP morre com SIGILL em 199ms. Sem isso o teste acusa um loop de
# reinicio que nao existe no produto.
set -u
ISO=${ISO:-/src/hexarouting.iso}
mkdir -p /mnt/iso /tmp/bf
mount -o loop,ro "$ISO" /mnt/iso
cp /mnt/iso/live/vmlinuz /mnt/iso/live/initrd /tmp/bf/
umount /mnt/iso

timeout "${BOOT_SECS:-420}" qemu-system-x86_64 \
  -cpu max -m 3072 -smp 2 -nographic -no-reboot \
  -cdrom "$ISO" -kernel /tmp/bf/vmlinuz -initrd /tmp/bf/initrd \
  -append "boot=live components console=ttyS0,115200 systemd.show_status=1" \
  > /tmp/boot.log 2>&1

sed 's/\x1b\[[0-9;]*m//g' /tmp/boot.log > /tmp/clean.log
sed -n '/autoteste de boot/,/=====$/p' /tmp/clean.log | head -40
grep -q "HexaRouting pronto" /tmp/clean.log && exit 0
echo "AUTOTESTE NAO REPORTOU PRONTO"
exit 1
