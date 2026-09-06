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

    # Order matters: lines() has to emit these in the SAME order as the
    # device's own "show bpa config", or the running configuration and a
    # candidate built from it differ on every commit for no reason.
    TIMEOUTS = ("udp", "icmp", "tcp-syn", "gre", "tcp-transitory",
                "tcp-established", "port-holddown", "alg-expect")
    FILTERS = ("endpoint-independent", "address-dependent")

    # Mirrors foreach_bpa_alg in bpa_pool.h, in the same order and with the
    # same bits. A pool a VTY cannot express is a pool an operator cannot
    # configure, so this list and that macro have to be kept in step; the lab
    # asserts a round trip through the dataplane, which is what catches it.
    ALG_BITS = {"ftp": 1 << 0, "rtsp": 1 << 1, "pptp": 1 << 2}
    ALG_DEFAULT_ON = ("ftp", "rtsp", "pptp")

    def __init__(self, name: str):
        self.name = name
        self.inside: list[str] = []
        self.outside: list[str] = []
        self.block_size = "512"
        self.ports = ""                 # "" means the device default
        self.policy = "interleaved"
        self.mode = "deterministic"
        # Overrides only. An absent entry means "inherit", which is what the
        # device means by zero, so emitting the inherited value would turn a
        # global change into a diff against every pool.
        self.timeouts: dict[str, int] = {}
        self.max_blocks = 0             # 0 means the device default of one
        self.filter = "endpoint-independent"
        self.algs: dict[str, bool] = {}  # only where the operator chose

    def lines(self) -> list[str]:
        out = [f"cgnat pool {self.name}"]
        out += [f" inside {p}" for p in self.inside]
        out += [f" outside {p}" for p in self.outside]
        out.append(f" block-size {self.block_size}")
        if self.ports:
            out.append(f" ports {self.ports}")
        out.append(f" policy {self.policy}")
        out.append(f" mode {self.mode}")
        for t in self.TIMEOUTS:
            if t in self.timeouts:
                out.append(f" timeout {t} {self.timeouts[t]}")
        if self.max_blocks > 1:
            out.append(f" max-blocks {self.max_blocks}")
        if self.filter != "endpoint-independent":
            out.append(f" filter {self.filter}")
        for a in self.ALG_BITS:
            if a in self.algs:
                out.append(f" alg {a} {'on' if self.algs[a] else 'off'}")
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
        for t in self.TIMEOUTS:
            if t in self.timeouts:
                parts += ["timeout", t, str(self.timeouts[t])]
        if self.max_blocks > 1:
            parts += ["max-blocks", str(self.max_blocks)]
        if self.filter != "endpoint-independent":
            parts += ["filter", self.filter]
        for a in self.ALG_BITS:
            if a in self.algs:
                parts += ["alg", a, "on" if self.algs[a] else "off"]
        return " ".join(parts)

    def api_args(self, is_add=True) -> dict:
        """
        The same pool as binary-API arguments.

        Kept next to device_command() on purpose: the two describe one pool in
        two languages, and a field added to one and forgotten in the other is a
        setting the operator configures and the dataplane never receives.
        """
        port_min, port_max = 1024, 65535
        if self.ports:
            parts = self.ports.replace("-", " ").split()
            if len(parts) == 2:
                port_min, port_max = int(parts[0]), int(parts[1])
        # Two masks, as the dataplane stores them: what is enabled, and what
        # the operator actually chose. Anything not chosen is left to
        # bpa_pool_init(), so a pool that says nothing about ALGs gets the
        # documented defaults rather than "all off".
        alg = alg_set = 0
        for name, bit in self.ALG_BITS.items():
            on = self.algs.get(name, name in self.ALG_DEFAULT_ON)
            if on:
                alg |= bit
            if name in self.algs:
                alg_set |= bit

        api_t = {"udp": "udp", "icmp": "icmp", "tcp-syn": "tcp_syn",
                 "tcp-transitory": "tcp_transitory",
                 "tcp-established": "tcp_established", "gre": "gre",
                 "port-holddown": "port_holddown",
                 "alg-expect": "alg_expect"}
        timeouts = {v: 0 for v in api_t.values()}
        for k, v in self.timeouts.items():
            timeouts[api_t[k]] = int(v)

        return {
            "is_add": bool(is_add),
            "name": self.name,
            "block_size": int(self.block_size),
            "port_min": port_min,
            "port_max": port_max,
            "policy": f"BPA_API_POLICY_{self.policy.upper()}",
            "mode": f"BPA_API_MODE_{self.mode.upper()}",
            "filter": "BPA_API_FILTER_"
                      + self.filter.upper().replace("-", "_"),
            "max_blocks": int(self.max_blocks),
            "alg": alg,
            "alg_set": alg_set,
            "timeouts": timeouts,
            "n_inside": len(self.inside),
            "n_prefixes": len(self.inside) + len(self.outside),
            "prefixes": list(self.inside) + list(self.outside),
        }

    def __eq__(self, other):
        return isinstance(other, Pool) and self.lines() == other.lines()


class Config:
    """A complete device configuration."""

    def __init__(self):
        # Lines the model did not understand, as (line number, text, why).
        # Never discarded: a configuration that is accepted and silently
        # trimmed is a configuration that lies about the running box.
        self.unknown: list[tuple[int, str, str]] = []
        self.pools: dict[str, Pool] = {}
        self.interfaces: dict[str, str] = {}   # name -> inside|outside
        # MTU is deliberately its own map, not a field of the CGNAT interface
        # entry: an interface can need an explicit MTU without carrying CGNAT
        # at all. docs/routing.md records why it must never be inherited - an
        # unaligned MTU stalls OSPF in Exchange with no log saying why.
        self.mtus: dict[str, int] = {}         # name -> MTU
        self.globals: list[str] = []           # other cgnat statements
        self.routing: list[str] = []           # verbatim FRR statements

    # ------------------------------------------------------------ text

    def text(self) -> str:
        out = [CGNAT_HEADER]
        for name in sorted(self.pools):
            out += self.pools[name].lines()
        for ifname in sorted(self.interfaces):
            out.append(f"cgnat interface {ifname} {self.interfaces[ifname]}")
        for ifname in sorted(self.mtus):
            out.append(f"interface {ifname}")
            out.append(f" mtu {self.mtus[ifname]}")
            out.append(" exit")
        out += sorted(self.globals)
        out.append(ROUTING_HEADER)
        out += self.routing
        return "\n".join(out) + "\n"

    @classmethod
    def parse(cls, text: str) -> "Config":
        cfg = cls()
        section = "cgnat"
        pool = None
        iface = None
        for lineno, raw in enumerate(text.splitlines(), 1):
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
            if s.startswith("cgnat pool"):
                w = s.split()
                if len(w) < 3:
                    cfg.unknown.append((lineno, s, "a pool needs a name"))
                    continue
                pool = Pool(w[2])
                cfg.pools[pool.name] = pool
            elif pool is not None and line.startswith(" "):
                why = cfg._pool_line(pool, s)
                if why:
                    cfg.unknown.append((lineno, s, why))
                if s == "exit":
                    pool = None
            elif s.startswith("cgnat interface "):
                w = s.split()
                if len(w) < 4:
                    cfg.unknown.append(
                        (lineno, s, "expected: cgnat interface <name> "
                                    "<inside|outside>"))
                elif w[3] not in ("inside", "outside"):
                    cfg.unknown.append(
                        (lineno, s, f"'{w[3]}': expected inside or outside"))
                else:
                    cfg.interfaces[w[2]] = w[3]
            elif s.startswith("interface "):
                iface = s.split()[1]
            elif iface is not None and line.startswith(" "):
                w = s.split()
                if s == "exit":
                    iface = None
                elif w[0] == "mtu" and len(w) > 1 and w[1].isdigit():
                    cfg.mtus[iface] = int(w[1])
                else:
                    cfg.unknown.append(
                        (lineno, s, "an interface understands 'mtu <n>'"))
            elif s.startswith("cgnat "):
                cfg.globals.append(s)
            else:
                cfg.unknown.append((lineno, s, "not a CGNAT statement"))
        return cfg

    @staticmethod
    def _pool_line(pool: Pool, s: str):
        """
        Apply one line of a pool block. Returns None, or why it was refused.

        Every branch that does not match falls through to a refusal. It used to
        fall through to nothing at all: `filter`, `max-blocks`, the per-pool
        timeouts and the ALG switches were accepted at the prompt, accepted at
        commit, and never reached the dataplane - four merged features that did
        not exist through the interface an operator actually uses (issue #47).
        """
        w = s.split()
        if not w:
            return None
        if w[0] == "inside" and len(w) > 1:
            pool.inside.append(w[1])
        elif w[0] == "outside" and len(w) > 1:
            pool.outside.append(w[1])
        elif w[0] == "block-size" and len(w) > 1:
            pool.block_size = w[1]
        elif w[0] == "ports" and len(w) > 2:
            pool.ports = f"{w[1]} {w[2]}"
        elif w[0] == "policy" and len(w) > 1:
            if w[1] not in ("interleaved", "blocked"):
                return f"policy '{w[1]}': expected interleaved or blocked"
            pool.policy = w[1]
        elif w[0] == "mode" and len(w) > 1:
            if w[1] not in ("deterministic", "dynamic"):
                return f"mode '{w[1]}': expected deterministic or dynamic"
            pool.mode = w[1]
        elif w[0] == "timeout" and len(w) > 2:
            if w[1] not in Pool.TIMEOUTS:
                return (f"timeout '{w[1]}': expected one of "
                        + ", ".join(Pool.TIMEOUTS))
            if not w[2].isdigit():
                return f"timeout {w[1]}: '{w[2]}' is not a number of seconds"
            pool.timeouts[w[1]] = int(w[2])
        elif w[0] == "max-blocks" and len(w) > 1:
            if not w[1].isdigit() or int(w[1]) < 1:
                return f"max-blocks '{w[1]}': expected a number of at least 1"
            pool.max_blocks = int(w[1])
        elif w[0] == "filter" and len(w) > 1:
            if w[1] not in Pool.FILTERS:
                return (f"filter '{w[1]}': expected one of "
                        + ", ".join(Pool.FILTERS))
            pool.filter = w[1]
        elif w[0] == "alg" and len(w) > 2:
            if w[1] not in Pool.ALG_BITS:
                return (f"alg '{w[1]}': expected one of "
                        + ", ".join(Pool.ALG_BITS))
            if w[2] not in ("on", "off", "enable", "disable"):
                return f"alg {w[1]} '{w[2]}': expected on or off"
            pool.algs[w[1]] = w[2] in ("on", "enable")
        elif w[0] == "exit":
            pass
        else:
            return f"'{s}' is not something a pool understands"
        return None

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
        mtu_set = {k: v for k, v in other.mtus.items() if self.mtus.get(k) != v}
        # Removing the statement has to put the interface back to the driver
        # default, not leave the last committed value behind. A rollback that
        # cannot undo is not a rollback.
        mtu_del = [k for k in self.mtus if k not in other.mtus]

        return {
            "pool_add": add,
            "pool_remove": remove,
            "pool_replace": replace,
            "if_add": if_add,
            "if_remove": if_del,
            "mtu_set": mtu_set,
            "mtu_del": mtu_del,
            "globals": [g for g in other.globals if g not in self.globals],
            "routing_changed": self.routing != other.routing,
        }
