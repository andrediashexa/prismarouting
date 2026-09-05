#!/usr/bin/env python3
"""
Coletor IPFIX minimo, so para verificar o export do HexaRouting.

Decodifica o template e os registros de dados e imprime os eventos de bloco
da RFC 8158 em texto. Nao e produto: existe para o teste provar que o que sai
do VPP esta conforme a norma, e nao so que "algum pacote saiu".
"""
import socket, struct, sys, time

IE = {
    8:   ("sourceIPv4Address", "ip"),
    225: ("postNATSourceIPv4Address", "ip"),
    230: ("natEvent", "u8"),
    283: ("natPoolId", "u32"),
    323: ("observationTimeMilliseconds", "u64"),
    361: ("portRangeStart", "u16"),
    362: ("portRangeEnd", "u16"),
}
EVENT = {12: "esgotamento", 16: "ALOCACAO", 17: "LIBERACAO"}

templates = {}   # template_id -> [(ie, length)]
records = []
pending = []     # data sets vistos antes do seu template

def decode(kind, raw):
    if kind == "ip":
        return ".".join(str(b) for b in raw)
    v = int.from_bytes(raw, "big")
    return v

def emit(tid, body, late=False):
    fields = templates[tid]
    size = sum(l for _, l in fields)
    b = 0
    while b + size <= len(body):
        rec = {}
        for ie, ln in fields:
            name, kind = IE.get(ie, (f"ie{ie}", "u32"))
            rec[name] = decode(kind, body[b:b+ln]); b += ln
        records.append(rec)
        ev = EVENT.get(rec.get("natEvent"), rec.get("natEvent"))
        ts = rec.get("observationTimeMilliseconds", 0) / 1000.0
        tag = " (reprocessado)" if late else ""
        print(f"  {time.strftime('%H:%M:%S', time.gmtime(ts))} "
              f"{ev:11} {rec.get('sourceIPv4Address')} -> "
              f"{rec.get('postNATSourceIPv4Address')} "
              f"portas {rec.get('portRangeStart')}-"
              f"{rec.get('portRangeEnd')} pool {rec.get('natPoolId')}{tag}")

def handle(pkt):
    if len(pkt) < 16:
        return
    ver, length, exp_time, seq, domain = struct.unpack("!HHIII", pkt[:16])
    if ver != 10:
        print(f"  versao {ver}, esperado 10 (IPFIX)"); return
    off = 16
    while off + 4 <= length:
        set_id, set_len = struct.unpack("!HH", pkt[off:off+4])
        if set_len < 4:
            break
        body = pkt[off+4:off+set_len]
        if set_id == 2:                      # template set
            b = 0
            while b + 4 <= len(body):
                tid, cnt = struct.unpack("!HH", body[b:b+4]); b += 4
                fields = []
                for _ in range(cnt):
                    ie, ln = struct.unpack("!HH", body[b:b+4]); b += 4
                    if ie & 0x8000:
                        b += 4               # enterprise number
                    fields.append((ie & 0x7fff, ln))
                templates[tid] = fields
                names = ", ".join(f"{IE.get(i,(str(i),))[0]}({l})" for i, l in fields)
                print(f"  TEMPLATE {tid}: {cnt} campos, {sum(l for _,l in fields)} bytes")
                print(f"    {names}")
            # um template novo pode destravar dados que chegaram antes dele
            for tid, body_pending in pending[:]:
                if tid in templates:
                    pending.remove((tid, body_pending))
                    emit(tid, body_pending, late=True)
        elif set_id >= 256:                  # data set
            fields = templates.get(set_id)
            if not fields:
                # coletor de verdade guarda e reprocessa quando o template chega
                print(f"  dados para template {set_id} ainda sem template: guardando")
                pending.append((set_id, body))
            else:
                emit(set_id, body)
        off += set_len

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4739
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 25
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    s.settimeout(1.0)
    print(f"coletor IPFIX ouvindo em udp/{port} por {secs:.0f}s")
    end = time.time() + secs
    n = 0
    while time.time() < end:
        try:
            data, _ = s.recvfrom(65535)
        except socket.timeout:
            continue
        n += 1
        handle(data)
    allocs = sum(1 for r in records if r.get("natEvent") == 16)
    deallocs = sum(1 for r in records if r.get("natEvent") == 17)
    exh = sum(1 for r in records if r.get("natEvent") == 12)
    print(f"\nRESUMO: {n} pacotes, {len(records)} registros "
          f"({allocs} alocacoes, {deallocs} liberacoes, {exh} esgotamentos)")
    if allocs and deallocs and allocs == deallocs:
        print("Toda alocacao teve sua liberacao correspondente.")
    elif allocs != deallocs:
        print(f"ATENCAO: {allocs} alocacoes contra {deallocs} liberacoes; "
              f"a diferenca sao blocos ainda em uso ou registros perdidos.")

main()
