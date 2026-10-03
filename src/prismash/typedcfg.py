"""
Typed configuration for the management API (#558, phase 2).

What prisma-web edits as raw lines today - interfaces, CGNAT pools and the
CGNAT log export - read as fields, and written back as the exact lines an
operator would type. Nothing here parses configuration text of its own:

  reads    come off the Config the VTY itself holds - the candidate the
           operator is editing, or the running configuration it reads back -
           so the API and `show running-config` cannot disagree about a value;
  edits    are desired state turned into VTY lines (`description uplink`,
           `no mtu`, `no ipv4 address 10.0.0.1/24`), which the server then
           stages in the candidate through the same feed as ConfigService.Edit.
           Every refusal, gate and reason code of the line form applies,
           because it IS the line form;
  schema   is the grammar the prompt's `?` answers from, for these areas,
           with each argument's hint read as a type, a range or a choice.

Dicts in, dicts out: the server turns them into prisma.v1 messages, and the
unit tests drive this module without a box, a dataplane or gRPC.
"""
import re

AREAS = ("interfaces", "pools", "logging")

#: The CGNAT log streams, as `logging <transport> ...` names them.
TRANSPORTS = ("udp", "tcp", "syslog")

# ------------------------------------------------------------------ reads


def interface_kind(cfg, name):
    """What an interface IS: a bundle, a loopback, a sub-interface or a port."""
    if name in getattr(cfg, "bundles", {}):
        return "bundle"
    if name in getattr(cfg, "loopbacks", set()):
        return "loopback"
    if name in getattr(cfg, "vlans", {}):
        return "subinterface"
    return "port"


def interface_typed(cfg, name):
    """One interface of Config `cfg` as fields; absent statements as defaults."""
    addrs = cfg.addresses.get(name, [])
    vlan = cfg.vlans.get(name) or {}
    linux = cfg.linux_names.get(name, "")
    return {
        "name": name,
        "kind": interface_kind(cfg, name),
        "description": cfg.descriptions.get(name, ""),
        "mtu": int(cfg.mtus.get(name, 0)),
        "shutdown": name in cfg.admin_down,
        "ipv4": [a for a in addrs if ":" not in a],
        "ipv6": [a for a in addrs if ":" in a],
        "cgnat_role": cfg.interfaces.get(name, ""),
        "vrf": int(cfg.vrfs.get(name, 0)),
        # The pair under its own name is no statement (#461): "" here too.
        "linux_name": "" if linux in ("", name) else linux,
        "vlan_outer": int(vlan.get("outer") or 0),
        "vlan_inner": int(vlan.get("inner") or 0),
        "mac_address": cfg.macs.get(name, ""),
        "urpf": cfg.urpf.get(name, ""),
        "router_advertisements": name in cfg.ra_enabled,
    }


def interfaces_typed(cfg):
    """Every interface `cfg` has a block for, by name."""
    return [interface_typed(cfg, n) for n in sorted(cfg.interface_names())]


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def pool_typed(pool):
    """One config.Pool as fields."""
    lo, _, hi = (pool.ports or "").partition(" ")
    return {
        "name": pool.name,
        "prefix_inside": list(pool.inside),
        "prefix_outside": list(pool.outside),
        "block_size": _int(pool.block_size),
        "port_first": _int(lo),
        "port_last": _int(hi),
        "policy": pool.policy,
        "mode": pool.mode,
        # "" is every VRF, which is not VRF 0: None says it.
        "vrf": None if pool.vrf == "" else _int(pool.vrf),
        "outside_vrf": None if pool.outside_vrf == "" else _int(pool.outside_vrf),
        "timeouts": {k: int(v) for k, v in pool.timeouts.items()},
        "max_blocks": max(int(pool.max_blocks), 1) if pool.max_blocks else 1,
        "initial_allocation": int(pool.initial_ports),
        "block_stride": int(pool.block_stride),
        "reserve": int(pool.block_reserve),
        "bypass_source": sorted(pool.bypass_src),
        "bypass_destination": sorted(pool.bypass_dst),
        "utilisation_alarm": int(pool.util_alarm),
        "max_sessions": int(pool.max_sessions),
        "max_session_rate": int(pool.max_rate),
        "tcp_mss": str(pool.tcp_mss),
        "filter": pool.filter,
        "algs": {k: bool(v) for k, v in pool.algs.items()},
        "forwards": [{"protocol": proto, "public_port": int(pub),
                      "private_address": ip, "private_port": int(priv)}
                     for proto, pub, ip, priv in pool.forwards],
    }


def pools_typed(cfg):
    """Every pool of `cfg`, by name."""
    return [pool_typed(cfg.pools[n]) for n in sorted(cfg.pools)]


def _blank_collector(transport):
    c = {"transport": transport, "configured": False, "collector": "",
         "port": 0, "source": ""}
    if transport == "syslog":
        # facility/severity None: not stated, the box's defaults apply.
        c.update({"enterprise": 0, "facility": None, "severity": None})
    return c


def logging_line_typed(words):
    """
    One `logging <transport> ...` statement's words (after `logging`) as a
    collector dict, or None when it is not a collector statement.
    """
    if not words or words[0] not in TRANSPORTS:
        return None
    c = _blank_collector(words[0])
    if len(words) < 2 or words[1] == "disable":
        return c
    c["configured"] = True
    c["collector"] = words[1]
    rest = words[2:]
    keys = {"src": "source", "port": "port", "enterprise": "enterprise",
            "facility": "facility", "severity": "severity"}
    for i in range(0, len(rest) - 1, 2):
        k = keys.get(rest[i])
        if k:
            c[k] = rest[i + 1] if k == "source" else _int(rest[i + 1])
    return c


def logging_typed(cfg):
    """
    The CGNAT log export as `cfg` states it: whether UDP export is enabled,
    and each transport's collector.

    What the box is doing with it - connected, sending, spilling - is the
    runtime's, GetLogging's; this is the configuration only.
    """
    out = {"enabled": False,
           "collectors": {t: _blank_collector(t) for t in TRANSPORTS}}
    for line in cfg.globals:
        w = line.split()
        if w[:1] == ["cgnat"]:
            w = w[1:]
        if w[:1] != ["logging"]:
            continue
        if w[1:2] in (["enable"], ["disable"]):
            out["enabled"] = w[1] == "enable"
            continue
        c = logging_line_typed(w[1:])
        if c is not None:
            out["collectors"][c["transport"]] = c
    out["collectors"] = [out["collectors"][t] for t in TRANSPORTS]
    return out


def config_typed(cfg, areas=()):
    """The areas asked for (every one when none is named) of Config `cfg`."""
    want = set(areas) or set(AREAS)
    bad = want - set(AREAS)
    if bad:
        raise ValueError(f"unknown area {sorted(bad)[0]!r}: "
                         f"the areas are {', '.join(AREAS)}")
    out = {}
    if "interfaces" in want:
        out["interfaces"] = interfaces_typed(cfg)
    if "pools" in want:
        out["pools"] = pools_typed(cfg)
    if "logging" in want:
        out["logging"] = logging_typed(cfg)
    # Whatever the areas: the lines a client must show read only.
    out["box_owned"] = box_owned_lines(cfg)
    return out


# ------------------------------------------------------- box-owned lines

#: Statements the running configuration holds that are the BOX's, not the
#: operator's: no grammar of this VTY takes them, it marks them "<- not
#: checked" and hands them to FRR as they are, and a client shows them read
#: only on purpose rather than as unknown. (words as a pattern, why).
#: Measured on the X520 bench's running configuration: 4 of its 30 lines.
BOX_OWNED = (
    ("log syslog <level>",
     "FRR's own logging, written by FRR into its configuration"),
    ("service integrated-vtysh-config",
     "FRR's one-file configuration mode, which the box's FRR runs in"),
    ("vrf prisma-<name>",
     "a VRF of the box's own (prisma-bp is the containers' backplane, #447); "
     "the VTY never makes, removes or edits one"),
    ("exit-vrf", "closes the box's own VRF block above it"),
)


def box_owned(line, in_box_vrf=False):
    """
    Why `line` is a box-owned statement (BOX_OWNED), or "" when it is not.
    `in_box_vrf`: the line is inside a `vrf prisma-*` block, whose lines and
    whose `exit-vrf` are the box's too - an operator's VRF's are not.
    """
    w = line.split()
    if not w:
        return ""
    if w[:2] == ["log", "syslog"]:
        return BOX_OWNED[0][1]
    if w == ["service", "integrated-vtysh-config"]:
        return BOX_OWNED[1][1]
    if w[0] == "vrf" and len(w) == 2 and w[1].startswith("prisma-"):
        return BOX_OWNED[2][1]
    if in_box_vrf:
        return BOX_OWNED[3][1] if w == ["exit-vrf"] else BOX_OWNED[2][1]
    return ""


def box_owned_lines(cfg):
    """[{"line", "why"}]: the box-owned statements Config `cfg` holds."""
    out = []
    in_box_vrf = False
    for line in cfg.routing:
        stripped = line.strip()
        if not stripped or stripped == "!":
            continue
        w = stripped.split()
        if w[:1] == ["vrf"]:
            in_box_vrf = False
        why = box_owned(stripped, in_box_vrf)
        if w[:1] == ["vrf"]:
            in_box_vrf = bool(why)
        elif w == ["exit-vrf"]:
            in_box_vrf = False
        if why:
            out.append({"line": stripped, "why": why})
    return out


# ------------------------------------------------------------------ edits

#: Interface fields a typed edit may set, in the order their lines go.
INTERFACE_FIELDS = ("linux_name", "mac_address", "router_advertisements",
                    "vrf", "vlan", "description", "ipv4", "ipv6", "shutdown",
                    "mtu", "urpf", "cgnat_role")

#: Pool fields a typed edit may set, in the order their lines go.
POOL_FIELDS = ("prefix_inside", "prefix_outside", "block_size", "ports",
               "policy", "mode", "reserve", "vrf", "outside_vrf", "timeouts",
               "max_blocks", "initial_allocation", "block_stride",
               "bypass_source", "bypass_destination", "utilisation_alarm",
               "max_sessions", "max_session_rate", "tcp_mss", "filter",
               "algs", "forwards")

LOGGING_FIELDS = ("collectors", "enabled")


def _mask(fields, known):
    """The fields an edit applies: all of `known` when none are named."""
    if not fields:
        return list(known)
    bad = [f for f in fields if f not in known]
    if bad:
        raise ValueError(f"unknown field {bad[0]!r}: the fields are "
                         f"{', '.join(known)}")
    return [f for f in known if f in fields]


def _list_lines(keyword, now, want):
    """Lines that take list `now` to `want`: removals first, in order."""
    out = [f"no {keyword} {v}" for v in now if v not in want]
    out += [f"{keyword} {v}" for v in want if v not in now]
    return out


def interface_lines(current, desired, fields=()):
    """
    The VTY lines, typed under `interface <name>`, that take interface
    `current` (interface_typed's shape, None for a new one) to `desired`, for
    the `fields` named (all when none are). Unchanged fields write nothing.
    """
    cur = current or interface_typed_blank(desired["name"])
    out = []
    for f in _mask(fields, INTERFACE_FIELDS):
        if f == "vlan":
            if (desired.get("vlan_outer", 0), desired.get("vlan_inner", 0)) == \
                    (cur["vlan_outer"], cur["vlan_inner"]):
                continue
            if not desired.get("vlan_outer"):
                raise ValueError("vlan: a sub-interface's tag is what creates "
                                 "it; remove the interface instead")
            out.append(f"vlan {desired['vlan_outer']}" +
                       (f" inner {desired['vlan_inner']}"
                        if desired.get("vlan_inner") else ""))
            continue
        want, now = desired.get(f), cur[f]
        if f in ("ipv4", "ipv6"):
            out += _list_lines(f"{f} address", now, list(want or []))
            continue
        if want == now:
            continue
        if f == "shutdown":
            out.append("shutdown" if want else "no shutdown")
        elif f == "router_advertisements":
            out.append("no ipv6 nd suppress-ra" if want
                       else "ipv6 nd suppress-ra")
        elif f in ("mtu", "vrf"):
            out.append(f"{f} {int(want)}" if want else f"no {f}")
        else:
            word = {"linux_name": "linux-name", "mac_address": "mac-address",
                    "cgnat_role": "cgnat"}.get(f, f)
            out.append(f"{word} {want}" if want else f"no {word}")
    return out


def interface_typed_blank(name):
    """An interface with nothing configured: what a new block starts as."""
    return {"name": name, "kind": "port", "description": "", "mtu": 0,
            "shutdown": False, "ipv4": [], "ipv6": [], "cgnat_role": "",
            "vrf": 0, "linux_name": "", "vlan_outer": 0, "vlan_inner": 0,
            "mac_address": "", "urpf": "", "router_advertisements": False}


def pool_typed_blank(name):
    """A pool as the VTY creates it: Pool()'s own defaults."""
    from config import Pool
    return pool_typed(Pool(name))


def _forward_line(f):
    return (f"forward {f['protocol']} {int(f['public_port'])} to "
            f"{f['private_address']} {int(f['private_port'])}")


def pool_lines(current, desired, fields=()):
    """
    The VTY lines, typed under `nat` > `cgnat` > `pool <name>`, that take pool
    `current` (pool_typed's shape, None for a new one) to `desired`.
    """
    cur = current or pool_typed_blank(desired["name"])
    out = []
    for f in _mask(fields, POOL_FIELDS):
        if f == "ports":
            want = (int(desired.get("port_first", 0)),
                    int(desired.get("port_last", 0)))
            if want == (cur["port_first"], cur["port_last"]):
                continue
            out.append("no ports" if want == (0, 0)
                       else f"ports {want[0]} {want[1]}")
            continue
        want, now = desired.get(f), cur[f]
        if f in ("prefix_inside", "prefix_outside"):
            out += _list_lines(f.replace("_", "-"), now, list(want or []))
        elif f in ("bypass_source", "bypass_destination"):
            out += _list_lines(f"bypass {f.split('_')[1]}", now,
                               list(want or []))
        elif f == "forwards":
            nl = [_forward_line(x) for x in now]
            wl = [_forward_line(x) for x in (want or [])]
            out += [f"no {x}" for x in nl if x not in wl]
            out += [x for x in wl if x not in nl]
        elif f == "timeouts":
            want = {k: int(v) for k, v in (want or {}).items()}
            out += [f"no timeout {k}" for k in now if k not in want]
            out += [f"timeout {k} {v}" for k, v in want.items()
                    if now.get(k) != v]
        elif f == "algs":
            want = {k: bool(v) for k, v in (want or {}).items()}
            out += [f"no alg {k}" for k in now if k not in want]
            out += [f"alg {k} {'on' if v else 'off'}" for k, v in want.items()
                    if now.get(k) != v]
        elif want == now:
            continue
        elif f in ("block_size", "policy", "mode", "filter") and not want:
            # Every pool has one and none can be removed: the empty value a
            # message carries for a field it does not set leaves it as it is.
            # Measured: `filter` with no value reached the VTY as a line and
            # was refused "list index out of range" (lab/api-grpc.sh).
            continue
        elif f in ("vrf", "outside_vrf"):
            word = f.replace("_", "-")
            out.append(f"no {word}" if want is None else f"{word} {int(want)}")
        elif f in ("block_size", "policy", "mode", "filter"):
            out.append(f"{f.replace('_', '-')} {want}")
        elif f == "max_blocks":
            out.append("no max-blocks" if int(want or 0) <= 1
                       else f"max-blocks {int(want)}")
        elif f == "tcp_mss":
            out.append(f"tcp-mss {want}" if want else "no tcp-mss")
        else:
            word = {"initial_allocation": "initial-allocation",
                    "block_stride": "block-stride",
                    "utilisation_alarm": "utilisation-alarm",
                    "max_sessions": "max-sessions",
                    "max_session_rate": "max-session-rate"}.get(f, f)
            out.append(f"{word} {int(want)}" if want else f"no {word}")
    return out


def collector_line(c):
    """One collector's `logging <transport> ...` statement, or its disable."""
    t = c["transport"]
    if not c.get("configured"):
        return f"logging {t} disable"
    if t == "udp" and not c.get("source"):
        raise ValueError("logging udp: a collector needs its source "
                         "address (src)")
    if t == "syslog" and not c.get("enterprise"):
        raise ValueError("logging syslog: a collector needs its enterprise "
                         "number")
    words = ["logging", t, c["collector"]]
    if t == "udp":
        words += ["src", c["source"]]
    if c.get("port"):
        words += ["port", str(int(c["port"]))]
    if t == "syslog":
        words += ["enterprise", str(int(c["enterprise"]))]
        for k in ("facility", "severity"):
            if c.get(k) is not None:
                words += [k, str(int(c[k]))]
    return " ".join(words)


def logging_lines(current, desired, fields=()):
    """
    The VTY lines, typed under `nat` > `cgnat`, that take the log export
    `current` (logging_typed's shape) to `desired`. A collector `desired`
    does not list is left as it is; one it lists unconfigured is disabled.
    """
    out = []
    for f in _mask(fields, LOGGING_FIELDS):
        if f == "collectors":
            now = {c["transport"]: c for c in current["collectors"]}
            for c in desired.get("collectors") or []:
                if c.get("transport") not in TRANSPORTS:
                    raise ValueError(f"logging: unknown transport "
                                     f"{c.get('transport')!r}: "
                                     f"{', '.join(TRANSPORTS)}")
                base = dict(_blank_collector(c["transport"]))
                base.update(c)
                if _same_collector(now[c["transport"]], base):
                    continue
                if not base["configured"] and not now[c["transport"]]["configured"]:
                    continue
                out.append(collector_line(base))
        elif f == "enabled":
            if bool(desired.get("enabled")) != current["enabled"]:
                out.append("logging enable" if desired.get("enabled")
                           else "logging disable")
    return out


def _same_collector(a, b):
    keys = ("configured", "collector", "port", "source", "enterprise",
            "facility", "severity")
    return all(a.get(k) == b.get(k) for k in keys if k in a or k in b)


# ------------------------------------------------------------------ paths

def interface_path(name):
    return [f"interface {name}"]


def pool_path(name):
    return ["nat", "cgnat", f"pool {name}"]


LOGGING_PATH = ["nat", "cgnat"]


def delete_lines(area, name="", transport=""):
    """(path, lines) that remove one object, as the VTY removes it."""
    if area == "interfaces":
        return [], [f"no interface {name}"]
    if area == "pools":
        return ["nat", "cgnat"], [f"no pool {name}"]
    if area == "logging":
        if transport not in TRANSPORTS:
            raise ValueError(f"logging: name the transport to remove: "
                             f"{', '.join(TRANSPORTS)}")
        return list(LOGGING_PATH), [f"logging {transport} disable"]
    raise ValueError(f"unknown area {area!r}: the areas are {', '.join(AREAS)}")


# ------------------------------------------------------------------ schema

_RANGE = re.compile(r"^<(-?\d+)-(\d+)>$")


def config_modes(P):
    """
    Every configuration mode the VTY has, from prismash module `P`: (mode,
    how it is entered, the EditRequest.path as docs/api-coverage.md prints
    it, its grammar node, the roles that change it, the path as a list).

    THE ONE TABLE: tools/api-coverage.py reads it to write the coverage
    document and GetSchema reads it to answer, so the two cannot list
    different modes. The third column is the document's own text - prisma-web
    parses it until GetSchema replaces it - and is not to be reworded.
    """
    adm, cg = "administrator", "administrator, cgnat-operator"
    pol = ["policy-options", "policy-statement <name>"]
    af = ["router bgp <asn>", "address-family <afi> <safi>"]
    return [
        ("config", "configure", "[]", P.TREE["config"], adm, []),
        ("nat", "nat", '["nat"]', P.TREE["nat"], cg, ["nat"]),
        ("cgnat", "nat > cgnat", '["nat","cgnat"]', P.TREE["cgnat"], cg,
         ["nat", "cgnat"]),
        ("pool", "nat > cgnat > pool <name>", '["nat","cgnat","pool <name>"]',
         P.TREE["pool"], cg, ["nat", "cgnat", "pool <name>"]),
        ("system", "system", '["system"]', P.TREE["system"], adm, ["system"]),
        ("system memory", "system > memory", '["system","memory"]',
         P.TREE["memory"], adm, ["system", "memory"]),
        ("system cpu", "system > cpu", '["system","cpu"]', P.TREE["cpu"], adm,
         ["system", "cpu"]),
        ("system dataplane", "system > dataplane", '["system","dataplane"]',
         P.TREE["dataplane"], adm, ["system", "dataplane"]),
        ("policy-options", "policy-options", '["policy-options"]',
         P.TREE["policy-options"], adm, ["policy-options"]),
        ("prefix-list", "policy-options > prefix-list <name>",
         '["policy-options","prefix-list <name>"]', P.TREE["prefix-list"], adm,
         ["policy-options", "prefix-list <name>"]),
        ("policy-statement", "policy-options > policy-statement <name>",
         '["policy-options","policy-statement <name>"]',
         P.TREE["policy-statement"], adm, pol),
        ("term", "... > policy-statement <name> > term <name>",
         '[..., "term <name>"]', P.TREE["term"], adm, pol + ["term <name>"]),
        ("user", "user <name>", '["user <name>"]', P.TREE["user"], adm,
         ["user <name>"]),
        ("snmp-user", "snmp-user <name>", '["snmp-user <name>"]',
         P.TREE["snmp-user"], adm, ["snmp-user <name>"]),
        ("interface (port)", "interface <name>", '["interface <name>"]',
         P.PORT_BLOCK, adm, ["interface <name>"]),
        ("interface (bundle)", "interface bond<n>", '["interface bond<n>"]',
         P.INTERFACE_BLOCK, adm, ["interface bond<n>"]),
        ("router static", "router static", '["router static"]',
         P.STATIC_BLOCK_GRAMMAR, adm, ["router static"]),
        ("rpki", "rpki", '["rpki"]', P.RPKI_BLOCK, adm, ["rpki"]),
        ("router bgp", "router bgp <asn>", '["router bgp <asn>"]', P.BGP_BLOCK,
         adm, ["router bgp <asn>"]),
        ("bgp neighbor", "router bgp <asn>: neighbor <address> ...",
         '["router bgp <asn>"]', {"neighbor <address>": (
             "a BGP session", P.BGP_NEIGHBOUR)}, adm, ["router bgp <asn>"]),
        ("address-family", "router bgp <asn> > address-family <afi> <safi>",
         '["router bgp <asn>","address-family <afi> <safi>"]', P.BGP_AF_BLOCK,
         adm, af),
        ("address-family neighbor",
         "... > address-family: neighbor <address> ...",
         '[..., "address-family <afi> <safi>"]', {"neighbor <address>": (
             "a BGP session", P.BGP_AF_NEIGHBOUR)}, adm, af),
        ("router ospf", "router ospf", '["router ospf"]', P.OSPF_BLOCK, adm,
         ["router ospf"]),
        ("router ospf6", "router ospf6", '["router ospf6"]', P.OSPF6_BLOCK,
         adm, ["router ospf6"]),
        ("router isis", "router isis <tag>", '["router isis <tag>"]',
         P.ISIS_BLOCK, adm, ["router isis <tag>"]),
        ("route-map", "route-map <name> permit|deny <seq>",
         '["route-map <name> permit|deny <seq>"]', P.ROUTE_MAP_BLOCK, adm,
         ["route-map <name> permit|deny <seq>"]),
    ]


#: The modes GetConfig/SetConfig read and write as fields (#558, phase 2),
#: and which of their statements: None is every one.
TYPED_MODES = {"interface (port)": ("interfaces", None),
               "interface (bundle)": ("interfaces", None),
               "pool": ("pools", None),
               "cgnat": ("logging", ("logging",))}


def dynamic_sources(P):
    """
    {argument as the grammar names it: the source of its values}, from the
    VTY's own completion table (Shell.DYNAMIC): "<interface>" -> "interfaces",
    "<neighbour>" -> "neighbours", "<next-hop>" -> "next_hops".
    """
    out = {}
    for hint, fn in P.Shell.DYNAMIC.items():
        token = hint.split()[0].rstrip(",")
        out[token] = fn[len("dyn_"):] if fn.startswith("dyn_") else fn
    return out


def argument_of(token, sources=None):
    """
    One word of a VTY hint as a schema argument: {"name", "kind", "min",
    "max", "choices", "optional", "source"}. The kinds are integer,
    ipv4_prefix, ipv4_address, ipv6_prefix, ipv6_address, address, prefix,
    mac, choice, word, text, keyword; `source` names where the VTY completes
    its values from, when it does.
    """
    opt = token.startswith("[") and token.endswith("]")
    t = token.strip("[]").rstrip(",")
    arg = {"name": t, "kind": "word", "min": 0, "max": 0, "choices": [],
           "optional": opt, "source": (sources or {}).get(t, "")}
    m = _RANGE.match(t)
    if m:
        arg.update(kind="integer", min=int(m.group(1)), max=int(m.group(2)))
    elif t == "<A.B.C.D/M>":
        arg["kind"] = "ipv4_prefix"
    elif t == "<A.B.C.D>":
        arg["kind"] = "ipv4_address"
    elif t == "<X:X::X:X/M>":
        arg["kind"] = "ipv6_prefix"
    elif t == "<X:X::X:X>":
        arg["kind"] = "ipv6_address"
    elif t in ("<A.B.C.D|X:X::X:X>", "<collector>", "<source>", "<address>",
               "<private-ip>", "<next-hop>", "<neighbour>", "<router-id>",
               "<host>"):
        arg["kind"] = "address"
    elif t in ("<prefix>", "<A.B.C.D/M|X:X::X:X/M>", "<network>"):
        arg["kind"] = "prefix"
    elif t == "<xx:xx:xx:xx:xx:xx>":
        arg["kind"] = "mac"
    elif t in ("<text>", "<line>", "<rest>"):
        arg["kind"] = "text"
    elif t in ("<n>", "<ports>", "<seconds>", "<port>", "<GB>", "<MB>",
               "<asn>", "<seq>", "<number>", "<metric>", "<value>", "<delay>",
               "<hold>", "<keepalive>", "<init>", "<max>", "<minutes>",
               "<percent>"):
        arg["kind"] = "integer"
    elif t.startswith("<") and "|" in t:
        arg.update(kind="choice", choices=t.strip("<>").split("|"))
    elif not t.startswith("<") and "|" in t:
        arg.update(kind="choice", choices=t.split("|"))
    elif not t.startswith("<"):
        arg["kind"] = "keyword"
    return arg


def hint_arguments(hint, sources=None):
    """A hint string as its arguments, `[...]` groups as optional ones."""
    if not hint:
        return []
    out = []
    opt = 0
    for tok in hint.replace("[", " [ ").replace("]", " ] ").split():
        if tok == "[":
            opt += 1
            continue
        if tok == "]":
            opt = max(0, opt - 1)
            continue
        if tok in ("|", "or", "...", ","):
            continue
        a = argument_of(tok, sources)
        a["optional"] = a["optional"] or opt > 0
        out.append(a)
    return out


#: Words every level offers that are the session's, or are read elsewhere.
_SESSION = ("exit", "top", "up", "end", "show", "commit", "rollback", "no",
            "exit-address-family", "save")


def _walk(node, prefix, out, sources, depth=0):
    for key, val in node.items():
        if key in _SESSION and not prefix:
            continue
        if not isinstance(val, tuple):
            continue
        help_ = val[0] if val else ""
        sub = val[1] if len(val) > 1 else None
        if isinstance(sub, dict) and sub and depth < 7:
            _walk(sub, prefix + [key], out, sources, depth + 1)
        else:
            hint = sub if isinstance(sub, str) else ""
            # `<collector> src <source> [port <n>] | disable`: each side of a
            # top-level `|` is a form of its own.
            forms = [hint_arguments(part, sources)
                     for part in hint.split(" | ")] if hint else [[]]
            out.append({"words": prefix + key.split(), "help": help_,
                        "forms": forms,
                        "repeatable": "may repeat" in (help_ or "")})


def _negations(node):
    neg = node.get("no")
    if not isinstance(neg, tuple) or not isinstance(neg[1], dict):
        return set()
    return set(neg[1])


def schema(P, modes=()):
    """
    The grammar of every configuration mode (or the `modes` named) as the
    prompt's `?` answers it: [{"mode", "path", "enter", "roles", "typed",
    "statements": [{"words", "help", "forms", "removable", "no_words",
    "repeatable", "typed"}]}].
    """
    table = config_modes(P)
    known = [m[0] for m in table]
    bad = [m for m in modes if m not in known]
    if bad:
        raise ValueError(f"unknown mode {bad[0]!r}: the modes are "
                         f"{', '.join(known)}")
    sources = dynamic_sources(P)
    out = []
    for mode, enter, _doc, node, roles, path in table:
        if modes and mode not in modes:
            continue
        stmts = []
        _walk(node, [], stmts, sources)
        neg = _negations(node)
        area, only = TYPED_MODES.get(mode, ("", None))
        for st in stmts:
            first = st["words"][0]
            st["removable"] = first in neg or (
                mode == "cgnat" and st["words"][:1] == ["logging"]
                and len(st["words"]) > 1 and st["words"][1] in TRANSPORTS)
            st["no_words"] = ["no"] + st["words"] if first in neg else []
            st["typed"] = bool(area) and (only is None or first in only)
        out.append({"mode": mode, "path": list(path), "enter": enter,
                    "roles": roles, "typed": area, "statements": stmts})
    return out


def schema_digest(modes):
    """A stable hash of a schema: equal for equal grammars, whatever release."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps(modes, sort_keys=True).encode()).hexdigest()


def edit_for(cfg, area, obj, fields=()):
    """
    (path, lines) that set object `obj` (the typed shape) of `area` in Config
    `cfg` - the candidate - for the `fields` named. The current value is read
    off `cfg` itself, so the lines are relative to what the candidate holds,
    not to what a client last read.
    """
    if area == "interfaces":
        name = obj.get("name") or ""
        if not name:
            raise ValueError("interface: name the interface")
        cur = interface_typed(cfg, name) if name in cfg.interface_names() \
            else None
        return interface_path(name), interface_lines(cur, obj, fields)
    if area == "pools":
        name = obj.get("name") or ""
        if not name:
            raise ValueError("pool: name the pool")
        cur = pool_typed(cfg.pools[name]) if name in cfg.pools else None
        return pool_path(name), pool_lines(cur, obj, fields)
    if area == "logging":
        return list(LOGGING_PATH), logging_lines(logging_typed(cfg), obj,
                                                 fields)
    raise ValueError(f"unknown area {area!r}: the areas are {', '.join(AREAS)}")


def names_for(cfg, area, known_interfaces=()):
    """What `<name>` may be for a typed area: the configured objects, and
    for interfaces also those the dataplane has."""
    if area == "interfaces":
        return sorted(set(cfg.interface_names()) | set(known_interfaces))
    if area == "pools":
        return sorted(cfg.pools)
    return []
