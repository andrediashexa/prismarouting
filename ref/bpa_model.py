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
class PoolConfig:
    name: str
    inside_prefix: str
    outside_prefix: str
    block_size: int = 512
    port_min: int = 1024
    port_max: int = 65535
    policy: MapPolicy = MapPolicy.INTERLEAVED
    mode: PoolMode = PoolMode.DETERMINISTIC

    # --- campos derivados ---
    inside_base: int = field(init=False)
    inside_count: int = field(init=False)
    outside_base: int = field(init=False)
    outside_count: int = field(init=False)
    port_count: int = field(init=False)
    blocks_per_ip: int = field(init=False)
    capacity: int = field(init=False)

    def __post_init__(self):
        ins = ipaddress.IPv4Network(self.inside_prefix, strict=True)
        out = ipaddress.IPv4Network(self.outside_prefix, strict=True)

        self.inside_base = int(ins.network_address)
        self.inside_count = ins.num_addresses
        self.outside_base = int(out.network_address)
        self.outside_count = out.num_addresses

        if self.port_min < 1 or self.port_max > 65535 or self.port_min > self.port_max:
            raise BpaError(f"faixa de portas invalida: {self.port_min}-{self.port_max}")
        self.port_count = self.port_max - self.port_min + 1

        if self.block_size < 1 or (self.block_size & (self.block_size - 1)) != 0:
            raise BpaError(f"block_size deve ser potencia de 2, recebido {self.block_size}")
        if self.block_size > self.port_count:
            raise BpaError("block_size maior que a faixa de portas")

        self.blocks_per_ip = self.port_count // self.block_size
        self.capacity = self.outside_count * self.blocks_per_ip

    # ---------------- mapeamento direto (inside -> outside) ----------------

    def sub_index(self, ip_priv: str | int) -> int:
        """Indice do assinante dentro do prefixo interno."""
        v = int(ipaddress.IPv4Address(ip_priv)) if isinstance(ip_priv, str) else ip_priv
        idx = v - self.inside_base
        if idx < 0 or idx >= self.inside_count:
            raise BpaError(f"{ipaddress.IPv4Address(v)} fora do prefixo interno {self.inside_prefix}")
        return idx

    def split(self, sub_idx: int) -> tuple[int, int]:
        """sub_idx -> (pub_idx, blk_idx) conforme a politica do pool."""
        if self.policy is MapPolicy.BLOCKED:
            return divmod(sub_idx, self.blocks_per_ip)
        blk_idx, pub_idx = divmod(sub_idx, self.outside_count)
        return pub_idx, blk_idx

    def join(self, pub_idx: int, blk_idx: int) -> int:
        """(pub_idx, blk_idx) -> sub_idx. Inverso exato de split()."""
        if self.policy is MapPolicy.BLOCKED:
            return pub_idx * self.blocks_per_ip + blk_idx
        return blk_idx * self.outside_count + pub_idx

    def forward(self, ip_priv: str | int) -> dict:
        """Traducao determinista inside->outside. O(1), sem estado."""
        sub_idx = self.sub_index(ip_priv)
        if sub_idx >= self.capacity:
            raise BpaError(
                f"assinante {sub_idx} excede a capacidade do pool ({self.capacity}); "
                f"aumente o prefixo publico ou reduza block_size")
        pub_idx, blk_idx = self.split(sub_idx)
        port_lo = self.port_min + blk_idx * self.block_size
        return {
            "sub_idx": sub_idx,
            "pub_idx": pub_idx,
            "blk_idx": blk_idx,
            "ip_pub": str(ipaddress.IPv4Address(self.outside_base + pub_idx)),
            "port_lo": port_lo,
            "port_hi": port_lo + self.block_size - 1,
        }

    # ---------------- mapeamento reverso (outside -> inside) ----------------

    def reverse(self, ip_pub: str | int, port: int) -> str:
        """
        Traducao determinista outside->inside. O(1), aritmetica pura,
        sem nenhum lookup de sessao. E isto que permite log zero e
        e o motivo do modo deterministico escalar tao bem.
        """
        v = int(ipaddress.IPv4Address(ip_pub)) if isinstance(ip_pub, str) else ip_pub
        pub_idx = v - self.outside_base
        if pub_idx < 0 or pub_idx >= self.outside_count:
            raise BpaError(f"{ipaddress.IPv4Address(v)} fora do pool publico {self.outside_prefix}")
        if port < self.port_min or port > self.port_max:
            raise BpaError(f"porta {port} fora da faixa {self.port_min}-{self.port_max}")

        blk_idx = (port - self.port_min) // self.block_size
        if blk_idx >= self.blocks_per_ip:
            raise BpaError(f"bloco {blk_idx} fora do pool")

        sub_idx = self.join(pub_idx, blk_idx)
        if sub_idx >= self.inside_count:
            raise BpaError(f"sub_idx {sub_idx} sem assinante correspondente")
        return str(ipaddress.IPv4Address(self.inside_base + sub_idx))

    # ---------------- planejamento ----------------

    def summary(self) -> str:
        usable = min(self.capacity, self.inside_count)
        ratio = self.blocks_per_ip
        waste = self.port_count - (self.blocks_per_ip * self.block_size)
        return "\n".join([
            f"pool '{self.name}'  [{self.mode.value} / {self.policy.value}]",
            f"  interno        {self.inside_prefix}  ({self.inside_count:,} enderecos)",
            f"  publico        {self.outside_prefix}  ({self.outside_count:,} enderecos)",
            f"  portas         {self.port_min}-{self.port_max}  ({self.port_count:,} por IP)",
            f"  bloco          {self.block_size} portas  -> {self.blocks_per_ip} blocos por IP publico",
            f"  compartilha    {ratio}:1  (assinantes por IP publico)",
            f"  capacidade     {self.capacity:,} assinantes",
            f"  aproveitavel   {usable:,} assinantes  (limitado pelo {'prefixo interno' if self.inside_count < self.capacity else 'pool publico'})",
            f"  portas ociosas {waste} por IP publico",
        ])


def plan(subscribers: int, block_size: int, port_min: int = 1024,
         port_max: int = 65535) -> dict:
    """Quantos IPs publicos para N assinantes com blocos de block_size portas."""
    port_count = port_max - port_min + 1
    blocks_per_ip = port_count // block_size
    need = -(-subscribers // blocks_per_ip)          # ceil
    prefix_len = 32
    while (1 << (32 - prefix_len)) < need:
        prefix_len -= 1
    return {
        "assinantes": subscribers,
        "block_size": block_size,
        "blocos_por_ip": blocks_per_ip,
        "ips_publicos": need,
        "prefixo_sugerido": f"/{prefix_len}  ({1 << (32 - prefix_len)} IPs)",
    }
