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

    # Distance between the blocks one subscriber holds, in blocks of the same
    # public address. Zero is what the allocator has always done: an extension
    # is whatever comes off the free list next, which is a function of the
    # pool's history and not of its definition - so the model cannot predict
    # it, and says so by returning None rather than guessing.
    #
    # One is contiguous: the extension's ports start where the first block's
    # end. Larger spreads the subscriber across the address's port range, which
    # is what makes it harder to profile a subscriber by watching one range.
    block_stride: int = 0

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

        if self.block_stride < 0:
            raise BpaError(f"block_stride must not be negative, got {self.block_stride}")
        # A stride of blocks_per_ip or more steps past the last block on the
        # subscriber's own public address on its very first extension, so it
        # can never place anything. Refused rather than accepted and silently
        # inert.
        if self.block_stride >= self.blocks_per_ip:
            raise BpaError(
                f"block_stride {self.block_stride} does not fit: a public "
                f"address holds {self.blocks_per_ip} blocks, so the first "
                f"extension would land past the end of it")

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

    # ---------------- the blocks beyond the first --------------------------

    @property
    def stride_unit(self) -> int:
        """
        What "one block further along this public address" is worth in block
        indices.

        The index space is the same one split() divides, so stepping one block
        along a public address is stepping by whatever the policy puts between
        two blocks of the same address: the whole public set under
        interleaved, one under blocked.
        """
        return self.outside.count if self.policy is MapPolicy.INTERLEAVED else 1

    def extension_block(self, primary_block: int, nth: int):
        """
        Block index of the nth block a subscriber holds after its first, or
        None when the stride cannot place it.

        None has two causes and they are not the same thing. With no stride
        configured the answer is not arithmetic at all - it is whatever the
        free list holds - and the model refuses to invent one. With a stride,
        None means the step ran off the end of the subscriber's own public
        address, and the allocator then falls back rather than putting the
        subscriber on a second address where its own worker does not own the
        block.
        """
        if nth < 1:
            raise BpaError(f"nth must be at least 1, got {nth}")
        if self.block_stride == 0:
            return None
        pub_idx, blk_idx = self.split(primary_block)
        target = blk_idx + nth * self.block_stride
        if target >= self.blocks_per_ip:
            return None
        return self.join(pub_idx, target)

    def stride_reachable(self, nth: int, n_workers: int) -> bool:
        """
        Whether worker sharding lets the stride be honoured at all.

        A dynamic pool's blocks are partitioned by `block_idx % n_workers`, and
        a subscriber draws only from its own worker's slice - both directions
        of a flow have to meet on one thread. The nth block under a stride sits
        exactly nth*stride*stride_unit indices away from the first, so it is in
        the same slice only when that distance is a multiple of the worker
        count. When it is not, the stride names a block this worker may not
        hand out, and NO subscriber can be honoured rather than some.
        """
        if self.block_stride == 0:
            return False
        return (nth * self.block_stride * self.stride_unit) % n_workers == 0

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
            f"  stride     {self.block_stride}  "
            + ("(extensions go wherever the allocator has one free)"
               if self.block_stride == 0
               else f"({self.block_stride * self.block_size} ports between "
                    f"a subscriber's blocks, on its own public address)"),
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


# ---------------- planning: the range of ports a subscriber can hold --------
#
# `block_size` and `max_blocks` together already ARE a per-pool block-size
# range: a subscriber starts at block_size ports and grows, one block at a
# time and only when it fills one, up to block_size * max_blocks. A smart
# meter never asks for a second block; the household ends up with all of them.
#
# So the question a growth step has to answer is not "can a pool start small
# and grow" - it already does - but "what does taking the small steps cost".
# These functions are that arithmetic, kept here so the answer is re-measured
# rather than remembered. They take `grow` as an argument and PoolConfig has no
# such field on purpose: a setting in the model that the plugin does not carry
# is the divergence the generated vectors exist to catch.

def grant(nth_event: int, grow: int = 1) -> int:
    """How many blocks the nth exhaustion would hand out, at a growth step."""
    if nth_event < 1:
        raise BpaError(f"nth_event must be at least 1, got {nth_event}")
    if grow < 1:
        raise BpaError(f"grow must be at least 1, got {grow}")
    return grow ** (nth_event - 1)


def holding(block_size: int, max_blocks: int, grow: int = 1,
            merge: bool = False) -> list:
    """
    The port ranges a subscriber ends up holding, oldest first, as port
    counts.

    `merge` is the whole question about a growth step: a grant of g blocks is
    g ranges when they are handed out one at a time, and ONE range of
    g*block_size ports when the grant is contiguous and kept as one. The two
    hold the same ports and are not the same thing to search.
    """
    runs, held, events = [block_size], 1, 0
    while held < max_blocks:
        events += 1
        g = min(grant(events, grow), max_blocks - held)
        held += g
        runs += [g * block_size] if merge else [block_size] * g
    return runs


def grants_for(max_blocks: int, grow: int = 1) -> int:
    """How many exhaustion events it takes to be holding max_blocks blocks.

    Every one of them is a packet that found no free port and took the slow
    path, so this is the number a growth step actually reduces."""
    held, events = 1, 0
    while held < max_blocks:
        events += 1
        held += grant(events, grow)
    return events


def bitmap_walk(sessions: int, block_size: int, max_blocks: int,
                grow: int = 1, merge: bool = False) -> dict:
    """
    What a subscriber's session creations pay for holding many small blocks.

    bpa_session_create() tries the ranges a subscriber holds oldest first and
    takes the first free port, so a session landing in the kth range probes k
    of them. A probe of a FULL range is 2.3 ns measured on this allocator
    (bpa_portmap_alloc returns at once on n_free == 0, whatever the block
    size), so the probe count is the cost and the block size is not.
    """
    runs = holding(block_size, max_blocks, grow, merge)
    probes, left = 0, sessions
    for k, size in enumerate(runs, start=1):
        take = min(left, size)
        probes += take * k
        left -= take
        if left == 0:
            break
    return {
        "ports": block_size * max_blocks,
        "ranges": len(runs),
        "grants": grants_for(max_blocks, grow),
        "probes": probes,
        "probe_ns": probes * 2.3,
    }
