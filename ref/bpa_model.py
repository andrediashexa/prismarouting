#!/usr/bin/env python3
"""
Reference model of the BPA (allocated port block) mapping.

This is the executable specification of the algorithm. The VPP plugin in
src/plugins/bpa MUST reproduce these results bit for bit; the test vectors
generated here feed the C unit tests.

Two modes:
  DETERMINISTIC - fixed algorithmic mapping, reversible by formula.
                  Zero allocation logging. The proof for the authority is
                  the algorithm plus the pool configuration.
  DYNAMIC (PBA) - block allocated on demand at the first flow and released
                  on inactivity. Produces one log record per allocation and
                  release. Allows oversubscribing the public pool.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import ipaddress
import struct


class MapPolicy(Enum):
    """How subscribers are spread across the public IPs."""
    BLOCKED = "blocked"          # consecutive subscribers -> same public IP
    INTERLEAVED = "interleaved"  # consecutive subscribers -> distinct public IPs


class PoolMode(Enum):
    DETERMINISTIC = "deterministic"
    DYNAMIC = "dynamic"


class BpaError(Exception):
    pass



@dataclass
class AddrSet:
    """
    An address set built from possibly non-contiguous prefixes, presented as
    a dense index space from 0..count-1.

    No provider holds its IPv4 in a single aligned block: real allocations are
    fragmented. BPA needs a dense index, so the address <-> index translation
    is encapsulated here.
    """
    prefixes: list
    ranges: list = field(default_factory=list, init=False)  # (base, count, first_idx)
    count: int = field(init=False, default=0)

    def __post_init__(self):
        if not self.prefixes:
            raise BpaError("empty address set")

        parsed = []
        for p in self.prefixes:
            n = ipaddress.IPv4Network(p, strict=True)
            parsed.append((int(n.network_address), n.num_addresses))
        parsed.sort()

        # an overlap would break the address <-> index bijection: that is a
        # configuration error, not something to fix silently
        for (b1, c1), (b2, c2) in zip(parsed, parsed[1:]):
            if b2 < b1 + c1:
                raise BpaError(
                    f"overlapping prefixes: {ipaddress.IPv4Address(b1)}/+{c1} "
                    f"and {ipaddress.IPv4Address(b2)}/+{c2}")

        # adjacent ranges become one, which makes the datapath faster
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
        """address -> dense index, or None when outside the set."""
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
        """dense index -> address, or None when outside."""
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
    A BPA pool. inside/outside are lists of prefixes: providers hold
    fragmented IPv4 allocations, so assuming a single aligned block would rule
    the product out of most deployments.
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
            raise BpaError(f"invalid port range: {self.port_min}-{self.port_max}")
        self.port_count = self.port_max - self.port_min + 1

        if self.block_size < 1 or (self.block_size & (self.block_size - 1)) != 0:
            raise BpaError(f"block_size must be a power of two, got {self.block_size}")
        if self.block_size > self.port_count:
            raise BpaError("block_size larger than the port range")

        self.blocks_per_ip = self.port_count // self.block_size
        self.capacity = self.outside.count * self.blocks_per_ip

    # read-only shortcuts
    @property
    def inside_count(self) -> int:
        return self.inside.count

    @property
    def outside_count(self) -> int:
        return self.outside.count

    # ---------------- forward mapping (inside -> outside) ----------------

    def sub_index(self, ip_priv) -> int:
        v = int(ipaddress.IPv4Address(ip_priv)) if isinstance(ip_priv, str) else ip_priv
        idx = self.inside.index_of(v)
        if idx is None:
            raise BpaError(f"{ipaddress.IPv4Address(v)} outside the inside set")
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
        """Deterministic inside->outside translation."""
        sub_idx = self.sub_index(ip_priv)
        if sub_idx >= self.capacity:
            raise BpaError(
                f"subscriber {sub_idx} exceeds the pool capacity ({self.capacity}); "
                f"add public ranges or reduce block_size")
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

    # ---------------- reverse mapping (outside -> inside) ----------------

    def reverse(self, ip_pub, port: int) -> str:
        """
        Deterministic outside->inside translation: arithmetic plus two binary
        searches over small vectors, with no session lookup at all.
        """
        v = int(ipaddress.IPv4Address(ip_pub)) if isinstance(ip_pub, str) else ip_pub
        pub_idx = self.outside.index_of(v)
        if pub_idx is None:
            raise BpaError(f"{ipaddress.IPv4Address(v)} outside the public set")
        if port < self.port_min or port > self.port_max:
            raise BpaError(f"port {port} outside the range {self.port_min}-{self.port_max}")

        blk_idx = (port - self.port_min) // self.block_size
        if blk_idx >= self.blocks_per_ip:
            raise BpaError(f"block {blk_idx} outside the pool")

        sub_idx = self.join(pub_idx, blk_idx)
        ip_priv = self.inside.addr_of(sub_idx)
        if ip_priv is None:
            raise BpaError(f"sub_idx {sub_idx} has no matching subscriber")
        return str(ipaddress.IPv4Address(ip_priv))

    # ---------------- planning ----------------

    def summary(self) -> str:
        usable = min(self.capacity, self.inside.count)
        waste = self.port_count - (self.blocks_per_ip * self.block_size)
        limit = "inside set" if self.inside.count < self.capacity else "public pool"
        return "\n".join([
            f"pool '{self.name}'  [{self.mode.value} / {self.policy.value}]",
            f"  inside     {self.inside.describe()}  ({self.inside.count:,} addresses, {len(self.inside.ranges)} range(s))",
            f"  public     {self.outside.describe()}  ({self.outside.count:,} addresses, {len(self.outside.ranges)} range(s))",
            f"  ports      {self.port_min}-{self.port_max}  ({self.port_count:,} per IP)",
            f"  block      {self.block_size} ports  -> {self.blocks_per_ip} blocks per public IP",
            f"  sharing    {self.blocks_per_ip}:1  (subscribers per public IP)",
            f"  capacity   {self.capacity:,} subscribers",
            f"  usable     {usable:,} subscribers  (limited by the {limit})",
            f"  idle ports {waste} per public IP",
        ])


def plan(subscribers: int, block_size: int, port_min: int = 1024,
         port_max: int = 65535) -> dict:
    """How many public IPs for N subscribers with block_size-port blocks."""
    port_count = port_max - port_min + 1
    blocks_per_ip = port_count // block_size
    need = -(-subscribers // blocks_per_ip)
    return {
        "subscribers": subscribers,
        "block_size": block_size,
        "blocks_per_ip": blocks_per_ip,
        "public_ips": need,
        "equivalent_to": f"/{32 - max(0, need - 1).bit_length()}" if need else "-",
    }
