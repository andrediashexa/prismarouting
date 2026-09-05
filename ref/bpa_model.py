#!/usr/bin/env python3
"""
Modelo de referencia do mapeamento BPA (Bloco de Portas Alocadas).

Esta e a especificacao executavel do algoritmo. O plugin VPP em
src/plugins/bpa DEVE reproduzir estes resultados bit a bit; os vetores
de teste gerados aqui alimentam os testes unitarios do C.

Dois modos:
  DETERMINISTIC - mapeamento algoritmico fixo, reversivel por formula.
                  Zero log de alocacao. A prova para a autoridade e o
                  algoritmo + a configuracao do pool.
  DYNAMIC (PBA) - bloco alocado sob demanda no primeiro fluxo e liberado
                  por inatividade. Gera 1 log por alocacao/liberacao.
                  Permite oversubscription do pool publico.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import ipaddress
import struct


class MapPolicy(Enum):
    """Como os assinantes sao distribuidos entre os IPs publicos."""
    BLOCKED = "blocked"          # assinantes consecutivos -> mesmo IP publico
    INTERLEAVED = "interleaved"  # assinantes consecutivos -> IPs publicos distintos


class PoolMode(Enum):
    DETERMINISTIC = "deterministic"
    DYNAMIC = "dynamic"


class BpaError(Exception):
    pass



@dataclass
class AddrSet:
    """
    Conjunto de enderecos formado por prefixos possivelmente nao contiguos,
    apresentado como um espaco de indices denso de 0..count-1.

    Provedor nenhum tem IPv4 num bloco unico e alinhado: a alocacao real e
    fragmentada. O BPA precisa de um indice denso, entao a traducao
    endereco <-> indice fica encapsulada aqui.
    """
    prefixes: list
    ranges: list = field(default_factory=list, init=False)  # (base, count, first_idx)
    count: int = field(init=False, default=0)

    def __post_init__(self):
        if not self.prefixes:
            raise BpaError("conjunto de enderecos vazio")

        parsed = []
        for p in self.prefixes:
            n = ipaddress.IPv4Network(p, strict=True)
            parsed.append((int(n.network_address), n.num_addresses))
        parsed.sort()

        # sobreposicao quebraria a bijecao endereco <-> indice: e erro de
        # configuracao, nao algo para corrigir em silencio
        for (b1, c1), (b2, c2) in zip(parsed, parsed[1:]):
            if b2 < b1 + c1:
                raise BpaError(
                    f"prefixos sobrepostos: {ipaddress.IPv4Address(b1)}/+{c1} "
                    f"e {ipaddress.IPv4Address(b2)}/+{c2}")

        # faixas adjacentes viram uma so, o que deixa o datapath mais rapido
        merged = []
        for base, cnt in parsed:
            if merged and merged[-1][0] + merged[-1][1] == base:
                merged[-1][1] += cnt
            else:
                merged.append([base, cnt])

        idx = 0
        for base, cnt in merged:
            self.ranges.append((base, cnt, idx))
            idx += cnt
        self.count = idx

    def index_of(self, addr: int):
        """endereco -> indice denso, ou None se estiver fora do conjunto."""
        lo, hi = 0, len(self.ranges) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            base, cnt, first = self.ranges[mid]
            if addr < base:
                hi = mid - 1
            elif addr >= base + cnt:
                lo = mid + 1
            else:
                return first + (addr - base)
        return None

    def addr_of(self, idx: int):
        """indice denso -> endereco, ou None se estiver fora."""
        if idx < 0 or idx >= self.count:
            return None
        lo, hi = 0, len(self.ranges) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            base, cnt, first = self.ranges[mid]
            if idx < first:
                hi = mid - 1
            elif idx >= first + cnt:
                lo = mid + 1
            else:
                return base + (idx - first)
        return None

    def describe(self) -> str:
        parts = [f"{ipaddress.IPv4Address(b)}+{c}" for b, c, _ in self.ranges]
        return ", ".join(parts)


@dataclass
class PoolConfig:
    """
    Um pool BPA. inside/outside sao listas de prefixos: provedores tem
    alocacao IPv4 fragmentada, entao assumir um bloco unico e alinhado
    inviabilizaria o produto na maioria das instalacoes.
    """
    name: str
    inside_prefixes: list
    outside_prefixes: list
    block_size: int = 512
    port_min: int = 1024
    port_max: int = 65535
    policy: MapPolicy = MapPolicy.INTERLEAVED
    mode: PoolMode = PoolMode.DETERMINISTIC

    inside: AddrSet = field(init=False)
    outside: AddrSet = field(init=False)
    port_count: int = field(init=False)
    blocks_per_ip: int = field(init=False)
    capacity: int = field(init=False)

    def __post_init__(self):
        self.inside = AddrSet(self.inside_prefixes)
        self.outside = AddrSet(self.outside_prefixes)

        if self.port_min < 1 or self.port_max > 65535 or self.port_min > self.port_max:
            raise BpaError(f"faixa de portas invalida: {self.port_min}-{self.port_max}")
        self.port_count = self.port_max - self.port_min + 1

        if self.block_size < 1 or (self.block_size & (self.block_size - 1)) != 0:
            raise BpaError(f"block_size deve ser potencia de 2, recebido {self.block_size}")
        if self.block_size > self.port_count:
            raise BpaError("block_size maior que a faixa de portas")

        self.blocks_per_ip = self.port_count // self.block_size
        self.capacity = self.outside.count * self.blocks_per_ip

    # atalhos de leitura
    @property
    def inside_count(self) -> int:
        return self.inside.count

    @property
    def outside_count(self) -> int:
        return self.outside.count

    # ---------------- mapeamento direto (inside -> outside) ----------------

    def sub_index(self, ip_priv) -> int:
        v = int(ipaddress.IPv4Address(ip_priv)) if isinstance(ip_priv, str) else ip_priv
        idx = self.inside.index_of(v)
        if idx is None:
            raise BpaError(f"{ipaddress.IPv4Address(v)} fora do conjunto interno")
        return idx

    def split(self, sub_idx: int):
        """sub_idx -> (pub_idx, blk_idx) conforme a politica do pool."""
        if self.policy is MapPolicy.BLOCKED:
            return divmod(sub_idx, self.blocks_per_ip)
        blk_idx, pub_idx = divmod(sub_idx, self.outside.count)
        return pub_idx, blk_idx

    def join(self, pub_idx: int, blk_idx: int) -> int:
        """(pub_idx, blk_idx) -> sub_idx. Inverso exato de split()."""
        if self.policy is MapPolicy.BLOCKED:
            return pub_idx * self.blocks_per_ip + blk_idx
        return blk_idx * self.outside.count + pub_idx

    def forward(self, ip_priv) -> dict:
        """Traducao determinista inside->outside."""
        sub_idx = self.sub_index(ip_priv)
        if sub_idx >= self.capacity:
            raise BpaError(
                f"assinante {sub_idx} excede a capacidade do pool ({self.capacity}); "
                f"acrescente faixas publicas ou reduza block_size")
        pub_idx, blk_idx = self.split(sub_idx)
        ip_pub = self.outside.addr_of(pub_idx)
        port_lo = self.port_min + blk_idx * self.block_size
        return {
            "sub_idx": sub_idx,
            "pub_idx": pub_idx,
            "blk_idx": blk_idx,
            "ip_pub": str(ipaddress.IPv4Address(ip_pub)),
            "port_lo": port_lo,
            "port_hi": port_lo + self.block_size - 1,
        }

    # ---------------- mapeamento reverso (outside -> inside) ----------------

    def reverse(self, ip_pub, port: int) -> str:
        """
        Traducao determinista outside->inside: aritmetica mais duas buscas
        binarias em vetores pequenos, sem nenhum lookup de sessao.
        """
        v = int(ipaddress.IPv4Address(ip_pub)) if isinstance(ip_pub, str) else ip_pub
        pub_idx = self.outside.index_of(v)
        if pub_idx is None:
            raise BpaError(f"{ipaddress.IPv4Address(v)} fora do conjunto publico")
        if port < self.port_min or port > self.port_max:
            raise BpaError(f"porta {port} fora da faixa {self.port_min}-{self.port_max}")

        blk_idx = (port - self.port_min) // self.block_size
        if blk_idx >= self.blocks_per_ip:
            raise BpaError(f"bloco {blk_idx} fora do pool")

        sub_idx = self.join(pub_idx, blk_idx)
        ip_priv = self.inside.addr_of(sub_idx)
        if ip_priv is None:
            raise BpaError(f"sub_idx {sub_idx} sem assinante correspondente")
        return str(ipaddress.IPv4Address(ip_priv))

    # ---------------- planejamento ----------------

    def summary(self) -> str:
        usable = min(self.capacity, self.inside.count)
        waste = self.port_count - (self.blocks_per_ip * self.block_size)
        limit = "conjunto interno" if self.inside.count < self.capacity else "pool publico"
        return "\n".join([
            f"pool '{self.name}'  [{self.mode.value} / {self.policy.value}]",
            f"  interno        {self.inside.describe()}  ({self.inside.count:,} enderecos, {len(self.inside.ranges)} faixa(s))",
            f"  publico        {self.outside.describe()}  ({self.outside.count:,} enderecos, {len(self.outside.ranges)} faixa(s))",
            f"  portas         {self.port_min}-{self.port_max}  ({self.port_count:,} por IP)",
            f"  bloco          {self.block_size} portas  -> {self.blocks_per_ip} blocos por IP publico",
            f"  compartilha    {self.blocks_per_ip}:1  (assinantes por IP publico)",
            f"  capacidade     {self.capacity:,} assinantes",
            f"  aproveitavel   {usable:,} assinantes  (limitado pelo {limit})",
            f"  portas ociosas {waste} por IP publico",
        ])


def plan(subscribers: int, block_size: int, port_min: int = 1024,
         port_max: int = 65535) -> dict:
    """Quantos IPs publicos para N assinantes com blocos de block_size portas."""
    port_count = port_max - port_min + 1
    blocks_per_ip = port_count // block_size
    need = -(-subscribers // blocks_per_ip)
    return {
        "assinantes": subscribers,
        "block_size": block_size,
        "blocos_por_ip": blocks_per_ip,
        "ips_publicos": need,
        "equivale_a": f"/{32 - max(0, need - 1).bit_length()}" if need else "-",
    }
