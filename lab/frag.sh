#!/bin/sh
# Fragmento que nao e o primeiro nao tem porta. Sem reassembly virtual raso,
# ele nao casa com sessao e some - DNS grande, VPN e jogo param de forma
# intermitente.
set -u
mkdir -p /run/vpp /vpp_plugins
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
vpp -c /src/lab/startup-test.conf > /tmp/vpp.log 2>&1 &
n=0; until vppctl show version >/dev/null 2>&1 || [ "$n" -ge 30 ]; do sleep 1; n=$((n+1)); done

vppctl create packet-generator interface pg0 >/dev/null
vppctl create packet-generator interface pg1 >/dev/null
vppctl set interface state pg0 up; vppctl set interface state pg1 up
vppctl set interface ip address pg0 100.64.3.254/22
vppctl set interface ip address pg1 200.150.0.1/24
vppctl set ip neighbor pg1 200.150.0.2 02:00:00:00:00:02
vppctl ip route add 8.8.8.8/32 via 200.150.0.2 pg1
vppctl bpa pool lab inside 100.64.0.0/22 outside 200.150.0.16/30 block 512
vppctl set interface bpa in2out pg0
vppctl set interface bpa out2in pg1

echo "=== reassembly virtual raso ficou ligado na interface? ==="
vppctl show interface features pg0 | grep -iE "reass" | sed 's/^/  /' || echo "  (nao aparece)"

echo
echo "=== enviando os dois fragmentos ==="
echo "  esperado: os dois saem como 200.150.0.17; so o primeiro tem porta"
rm -f /tmp/frag.pcap
vppctl packet-generator capture pg1 pcap /tmp/frag.pcap count 2
for w in first second; do
  H=$(python3 /src/lab/mkfrag.py 100.64.0.5 8.8.8.8 12345 53 $w)
  L=$(( ${#H} / 2 - 1 ))
  vppctl packet-generator new "name f$w limit 1 node ip4-input interface pg0 \
      size ${L}-${L} data { hex $H }"
done
vppctl clear trace >/dev/null; vppctl trace add pg-input 6 >/dev/null
vppctl packet-generator enable >/dev/null; sleep 2

echo
echo "=== o que o dataplane fez ==="
vppctl show trace | grep -E "BPA in2out" | sed 's/^/  /'
echo
echo "=== o que saiu no fio ==="
python3 - <<'EOF' 2>&1 | sed 's/^/  /'
import ipaddress, struct
d = open("/tmp/frag.pcap","rb").read()
endian = "<" if d[:4] in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
link = struct.unpack(endian+"I", d[20:24])[0]
off, n, ok = 24, 0, True
while off + 16 <= len(d):
    _,_,caplen,_ = struct.unpack(endian+"IIII", d[off:off+16]); off += 16
    p = d[off:off+caplen]; off += caplen
    if link == 1: p = p[14:]
    if len(p) < 20 or (p[0] >> 4) != 4: continue
    n += 1
    hlen = (p[0] & 0xF) * 4
    fo = struct.unpack("!H", p[6:8])[0]
    mf, offs = bool(fo & 0x2000), (fo & 0x1FFF) * 8
    src = str(ipaddress.IPv4Address(p[12:16]))
    if offs == 0:
        sport = struct.unpack("!H", p[hlen:hlen+2])[0]
        print(f"fragmento 1: src={src} offset=0 MF={mf} porta={sport}")
        if src != "200.150.0.17" or sport != 1536: ok = False; print("  FALHA")
    else:
        print(f"fragmento 2: src={src} offset={offs} MF={mf} (sem cabecalho UDP)")
        if src != "200.150.0.17": ok = False; print("  FALHA")
print(f"\n{'OS DOIS FRAGMENTOS TRADUZIDOS' if ok and n==2 else f'PROBLEMA: {n} fragmentos'}")
EOF

echo
echo "=========== VERDICT ==========="
. /src/lab/verdict.sh
TR=$(vppctl show trace)
expect_in "the non-first fragment was recognised" "[non-first fragment]" "$TR"
expect_not_in "it did not open a phantom session" "12346 -> " "$TR"
expect_in "reassembly feature is on the interface" "ip4-sv-reassembly-feature" "$(vppctl show interface features pg0)"
verdict
