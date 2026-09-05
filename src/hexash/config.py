#!/usr/bin/env python3
"""
Device configuration model: parse, serialise and diff.

The whole device configuration is one text document with two halves: the CGNAT
statements this project owns, and the routing statements FRR owns. Keeping
them in one document is what makes `commit` and `rollback` cover both - a
rollback that restored CGNAT and left routing untouched would leave the box in
a state that never existed.
"""

from __future__ import annotations

import difflib

CGNAT_PREFIXES = ("cgnat ", "no cgnat ")
ROUTING_HEADER = "! routing"
CGNAT_HEADER = "! cgnat"


class Pool:
    """One CGNAT pool. Order of prefixes is preserved for a stable diff."""

    FIELDS = ("block-size", "ports", "policy", "mode")

    def __init__(self, name: str):
        self.name = name
        self.inside: list[str] = []
        self.outside: list[str] = []
        self.block_size = "512"
        self.ports = ""                 # "" means the device default
        self.policy = "interleaved"
        self.mode = "deterministic"

    def lines(self) -> list[str]:
        out = [f"cgnat pool {self.name}"]
        out += [f" inside {p}" for p in self.inside]
        out += [f" outside {p}" for p in self.outside]
        out.append(f" block-size {self.block_size}")
        if self.ports:
            out.append(f" ports {self.ports}")
        out.append(f" policy {self.policy}")
        out.append(f" mode {self.mode}")
        out.append(" exit")
        return out

    def device_command(self) -> str:
        parts = ["bpa pool", self.name]
        for p in self.inside:
            parts += ["inside", p]
        for p in self.outside:
            parts += ["outside", p]
        parts += ["block", self.block_size]
        if self.ports:
            parts += ["ports", self.ports.replace(" ", "-")]
        parts += ["policy", self.policy, "mode", self.mode]
        return " ".join(parts)

    def __eq__(self, other):
        return isinstance(other, Pool) and self.lines() == other.lines()


class Config:
    """A complete device configuration."""

    def __init__(self):
        self.pools: dict[str, Pool] = {}
        self.interfaces: dict[str, str] = {}   # name -> inside|outside
        self.globals: list[str] = []           # other cgnat statements
        self.routing: list[str] = []           # verbatim FRR statements

    # ------------------------------------------------------------ text

    def text(self) -> str:
        out = [CGNAT_HEADER]
        for name in sorted(self.pools):
            out += self.pools[name].lines()
        for ifname in sorted(self.interfaces):
            out.append(f"cgnat interface {ifname} {self.interfaces[ifname]}")
        out += sorted(self.globals)
        out.append(ROUTING_HEADER)
        out += self.routing
        return "\n".join(out) + "\n"

    @classmethod
    def parse(cls, text: str) -> "Config":
        cfg = cls()
        section = "cgnat"
        pool = None
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line.strip():
                continue
            if line.strip() == ROUTING_HEADER:
                section = "routing"
                continue
            if line.strip() == CGNAT_HEADER:
                section = "cgnat"
                continue
            if section == "routing":
                cfg.routing.append(line)
                continue

            s = line.strip()
            if s.startswith("cgnat pool "):
                pool = Pool(s.split()[2])
                cfg.pools[pool.name] = pool
            elif pool is not None and line.startswith(" "):
                cfg._pool_line(pool, s)
                if s == "exit":
                    pool = None
            elif s.startswith("cgnat interface "):
                w = s.split()
                if len(w) >= 4:
                    cfg.interfaces[w[2]] = w[3]
            elif s.startswith("cgnat "):
                cfg.globals.append(s)
        return cfg

    @staticmethod
    def _pool_line(pool: Pool, s: str) -> None:
        w = s.split()
        if not w:
            return
        if w[0] == "inside" and len(w) > 1:
            pool.inside.append(w[1])
        elif w[0] == "outside" and len(w) > 1:
            pool.outside.append(w[1])
        elif w[0] == "block-size" and len(w) > 1:
            pool.block_size = w[1]
        elif w[0] == "ports" and len(w) > 2:
            pool.ports = f"{w[1]} {w[2]}"
        elif w[0] == "policy" and len(w) > 1:
            pool.policy = w[1]
        elif w[0] == "mode" and len(w) > 1:
            pool.mode = w[1]

    # ------------------------------------------------------------ diff

    def compare(self, other: "Config") -> str:
        """Unified diff from `self` (running) to `other` (candidate)."""
        a = self.text().splitlines(keepends=True)
        b = other.text().splitlines(keepends=True)
        d = list(difflib.unified_diff(a, b, fromfile="running", tofile="candidate",
                                      n=1))
        return "".join(d)

    def is_same(self, other: "Config") -> bool:
        return self.text() == other.text()

    # ------------------------------------------------------------ delta

    def delta_to(self, other: "Config") -> dict:
        """
        What has to change on the device to turn `self` into `other`.

        Pools are replaced whole rather than edited in place: the device takes
        a pool definition in one command, and a partial edit would leave an
        invalid pool between the two halves of the change.
        """
        add, remove, replace = [], [], []
        for name, pool in other.pools.items():
            if name not in self.pools:
                add.append(pool)
            elif self.pools[name] != pool:
                replace.append(pool)
        for name in self.pools:
            if name not in other.pools:
                remove.append(name)

        if_add = {k: v for k, v in other.interfaces.items()
                  if self.interfaces.get(k) != v}
        if_del = [k for k in self.interfaces if k not in other.interfaces]

        return {
            "pool_add": add,
            "pool_remove": remove,
            "pool_replace": replace,
            "if_add": if_add,
            "if_remove": if_del,
            "globals": [g for g in other.globals if g not in self.globals],
            "routing_changed": self.routing != other.routing,
        }
