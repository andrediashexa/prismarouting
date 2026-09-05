#!/usr/bin/env python3
"""
Confere o erro ICMP traduzido, lido de um pcap.

Traduzir o pacote nao basta: se o checksum do ICMP sair errado, a pilha do
assinante descarta e o PMTUD continua quebrado - com a diferenca de que agora
falha em silencio, com o contador do CGNAT dizendo que traduziu.
"""
import ipaddress, struct, sys

def csum(b):
    if len(b) % 2:
        b += b"\x00"
    s = sum(struct.unpack(f"!{len(b)//2}H", b))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF

def read_pcap(path):
    with open(path, "rb") as f:
        data = f.read()
    magic = data[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        endian, nano = "<", magic[0] == 0x4d
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        endian, nano = ">", magic[1] == 0xb2
    else:
        raise SystemExit(f"pcap com magic desconhecido: {magic.hex()}")
    link = struct.unpack(endian + "I", data[20:24])[0]
    off, pkts = 24, []
    while off + 16 <= len(data):
        _, _, caplen, _ = struct.unpack(endian + "IIII", data[off:off+16])
        off += 16
        pkts.append(data[off:off+caplen])
        off += caplen
    return link, pkts

want_pub, want_priv, want_port = sys.argv[2], sys.argv[3], int(sys.argv[4])
link, pkts = read_pcap(sys.argv[1])
fail, seen = 0, 0

def bad(msg):
    global fail
    fail += 1
    print(f"  FALHA: {msg}")

for raw in pkts:
    p = raw[14:] if link == 1 else raw          # tira Ethernet se houver
    if len(p) < 20 or (p[0] >> 4) != 4 or p[9] != 1:
        continue
    hlen = (p[0] & 0xF) * 4
    icmp = p[hlen:]
    if len(icmp) < 8 or icmp[0] != 3:
        continue
    seen += 1
    outer_dst = str(ipaddress.IPv4Address(p[16:20]))
    print(f"  pacote ICMP tipo {icmp[0]} codigo {icmp[1]}, destino externo {outer_dst}")

    if csum(p[:hlen]) != 0:
        bad("checksum do IP externo invalido")
    if outer_dst != want_priv:
        bad(f"destino externo {outer_dst}, esperado {want_priv}")
    if csum(icmp) != 0:
        bad(f"checksum do ICMP invalido (assinante descartaria o pacote)")
    else:
        print("    checksum do ICMP: valido")

    inner = icmp[8:]
    ihl = (inner[0] & 0xF) * 4
    if csum(inner[:ihl]) != 0:
        bad("checksum do IP embutido invalido")
    else:
        print("    checksum do IP embutido: valido")

    isrc = str(ipaddress.IPv4Address(inner[12:16]))
    idst = str(ipaddress.IPv4Address(inner[16:20]))
    sport = struct.unpack("!H", inner[ihl:ihl+2])[0]
    print(f"    embutido: {isrc}:{sport} -> {idst}")
    if isrc != want_priv:
        bad(f"origem embutida {isrc}, esperado {want_priv}")
    if sport != want_port:
        bad(f"porta embutida {sport}, esperado {want_port}")

if not seen:
    bad("nenhum erro ICMP encontrado no pcap")
print(f"\n  {'ICMP TRADUZIDO CORRETAMENTE' if not fail else str(fail)+' FALHAS'}")
sys.exit(1 if fail else 0)
