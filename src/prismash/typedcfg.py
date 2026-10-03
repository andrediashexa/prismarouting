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

from config import (block_paths, is_number, is_static_route, static_route_key,
                    static_route_words)

#: The configuration-half areas (#558 phase 2) and the routing-half ones
#: (phase 3). Empty in a request is all of them.
CONFIG_AREAS = ("interfaces", "pools", "logging")
ROUTING_AREAS = ("static", "bgp", "ospf", "ospf6", "isis", "interface_routing")
AREAS = CONFIG_AREAS + ROUTING_AREAS

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
    if want & set(ROUTING_AREAS):
        out.update(routing_typed(cfg, want & set(ROUTING_AREAS)))
    # Whatever the areas: the lines a client must show read only.
    out["box_owned"] = box_owned_lines(cfg)
    return out


# ------------------------------------------------------- routing (phase 3)
#
# THE ROUTING HALF IS FRR'S OWN TEXT. This VTY does not model routing the way
# it models a pool: `cfg.routing` is the flat list of FRR statements the
# candidate holds - typed at the prompt or read back from FRR - and every
# screen that shows it (`show running-config`, `show this`) prints those
# statements, grouping the static routes into `router static` on the way.
# So the typed reads below READ THOSE STATEMENTS, block by block through
# `block_paths` - the VTY's own answer to "which block is this line in" -
# and the static routes through the same `static_route_words` the screens
# use. Nothing is reparsed from a screen.
#
# What a reader has no field for is never dropped: every block carries
# `other`, its statements verbatim, so a client can show the rest read only
# and a field added later is a field, not a format change.


def routing_statements(cfg):
    """[(path, statement)] of `cfg.routing`, separators and exits left out."""
    return [(p, line.strip()) for p, line in zip(block_paths(cfg.routing),
                                                 cfg.routing)
            if p is not None]


def _vrf_of_block(header):
    """`router bgp 65000 vrf red` / `router ospf vrf red`: "red", else ""."""
    w = header.split()
    return w[w.index("vrf") + 1] if "vrf" in w and w.index("vrf") + 1 < len(w) \
        else ""


def static_typed(line, description="", vrf=""):
    """One FRR static route as fields, through the VTY's own spelling."""
    words = static_route_words(line).split()
    out = {"prefix": words[0], "next_hop": words[2], "distance": 0, "tag": 0,
           "description": description or "", "vrf": vrf,
           "family": "ipv6" if line.split()[0] == "ipv6" else "ipv4",
           "extra": [], "line": ""}
    i = 3
    while i < len(words):
        if words[i] in ("distance", "tag") and i + 1 < len(words):
            out[words[i]] = _int(words[i + 1])
            i += 2
            continue
        out["extra"].append(words[i])
        i += 1
    out["line"] = static_route_words(line, description or None)
    return out


def static_routes_typed(cfg):
    """Every static route: the top-level ones (`router static` on the
    screens, with the description this box keeps) and each VRF's."""
    desc = getattr(cfg, "static_descriptions", {}) or {}
    out = []
    for path, stmt in routing_statements(cfg):
        if not is_static_route(stmt):
            continue
        if path == []:
            out.append(static_typed(stmt, desc.get(static_route_key(stmt), "")))
        elif len(path) == 1 and path[0].startswith("vrf "):
            out.append(static_typed(stmt, "", path[0].split()[1]))
    return out


def _redistribute(words):
    """`redistribute <proto> [metric N] [metric-type T] [route-map M]` ..."""
    out = {"protocol": words[1] if len(words) > 1 else "", "route_map": "",
           "metric": 0, "metric_type": "", "level": "", "statement":
           " ".join(words)}
    rest = words[2:]
    if out["protocol"] in ("ipv4", "ipv6") and rest:   # IS-IS: family first
        out["protocol"] = f"{out['protocol']} {rest[0]}"
        rest = rest[1:]
    i = 0
    while i < len(rest):
        k = rest[i]
        v = rest[i + 1] if i + 1 < len(rest) else ""
        if k == "route-map":
            out["route_map"] = v
        elif k == "metric":
            out["metric"] = _int(v)
        elif k == "metric-type":
            out["metric_type"] = v
        elif k in ("level-1", "level-2"):
            out["level"] = k
            i += 1
            continue
        else:
            i += 1
            continue
        i += 2
    return out


def _neighbor(table, who):
    if who not in table:
        table[who] = {"address": who, "remote_as": "", "description": "",
                      "update_source": "", "keepalive": 0, "holdtime": 0,
                      "password_set": False, "peer_group": "",
                      "is_peer_group": False, "ebgp_multihop": 0,
                      "shutdown": False, "bfd": False, "other": []}
    return table[who]


def _neighbor_af(table, who):
    if who not in table:
        table[who] = {"neighbor": who, "activate": False, "route_map_in": "",
                      "route_map_out": "", "prefix_list_in": "",
                      "prefix_list_out": "", "maximum_prefix": 0,
                      "next_hop_self": False, "soft_reconfiguration_inbound":
                      False, "default_originate": False, "other": []}
    return table[who]


def _bgp_neighbor_statement(nb, w):
    """`neighbor X <what> ...` at the router level, into `nb` (words after X)."""
    if not w:
        return
    k, v = w[0], w[1:]
    if k == "remote-as" and v:
        nb["remote_as"] = v[0]
    elif k == "description":
        nb["description"] = " ".join(v)
    elif k == "update-source" and v:
        nb["update_source"] = v[0]
    elif k == "timers" and len(v) >= 2 and is_number(v[0]):
        nb["keepalive"], nb["holdtime"] = _int(v[0]), _int(v[1])
    elif k == "password":
        nb["password_set"] = bool(v)      # the value is a secret: never read
    elif k == "peer-group" and v:
        nb["peer_group"] = v[0]
    elif k == "peer-group":
        nb["is_peer_group"] = True
    elif k == "ebgp-multihop":
        nb["ebgp_multihop"] = _int(v[0]) if v else 255
    elif k == "shutdown":
        nb["shutdown"] = True
    elif k == "bfd":
        nb["bfd"] = True
    else:
        nb["other"].append("neighbor " + nb["address"] + " " + " ".join(w))


def _bgp_af_neighbor_statement(af, w):
    if not w:
        return
    k, v = w[0], w[1:]
    if k == "activate":
        af["activate"] = True
    elif k == "route-map" and len(v) >= 2:
        af["route_map_in" if v[1] == "in" else "route_map_out"] = v[0]
    elif k == "prefix-list" and len(v) >= 2:
        af["prefix_list_in" if v[1] == "in" else "prefix_list_out"] = v[0]
    elif k == "maximum-prefix" and v:
        af["maximum_prefix"] = _int(v[0])
    elif k == "next-hop-self":
        af["next_hop_self"] = True
    elif k == "soft-reconfiguration" and v[:1] == ["inbound"]:
        af["soft_reconfiguration_inbound"] = True
    elif k == "default-originate":
        af["default_originate"] = True
    else:
        af["other"].append("neighbor " + af["neighbor"] + " " + " ".join(w))


def bgp_typed(cfg):
    """Every `router bgp` block: its neighbours, families and the rest."""
    routers = {}
    order = []
    for path, stmt in routing_statements(cfg):
        w = stmt.split()
        if not path and w[:2] == ["router", "bgp"]:
            if stmt not in routers:
                routers[stmt] = {"asn": w[2] if len(w) > 2 else "",
                                 "vrf": _vrf_of_block(stmt), "router_id": "",
                                 "default_ipv4_unicast": True,
                                 "neighbors": {}, "families": {}, "other": []}
                order.append(stmt)
            continue
        if not path or not path[0].startswith("router bgp"):
            continue
        r = routers.setdefault(path[0], {"asn": path[0].split()[2],
                                         "vrf": _vrf_of_block(path[0]),
                                         "router_id": "",
                                         "default_ipv4_unicast": True,
                                         "neighbors": {}, "families": {},
                                         "other": []})
        if path[0] not in order:
            order.append(path[0])
        if len(path) == 1:
            if w[:1] == ["address-family"]:
                r["families"].setdefault(stmt, {
                    "afi": w[1] if len(w) > 1 else "",
                    "safi": w[2] if len(w) > 2 else "",
                    "networks": [], "aggregates": [], "redistribute": [],
                    "neighbors": {}, "other": []})
            elif w[:2] == ["bgp", "router-id"] and len(w) > 2:
                r["router_id"] = w[2]
            elif w == ["no", "bgp", "default", "ipv4-unicast"]:
                # FRR's default is on, and with it a neighbour is active in
                # ipv4 unicast WITHOUT an `activate` line - measured on 10.7.1:
                # `neighbor X activate` typed is not printed back. So a
                # family's `activate` is read as stated, and this says what
                # the unstated case means.
                r["default_ipv4_unicast"] = False
            elif w == ["bgp", "default", "ipv4-unicast"]:
                r["default_ipv4_unicast"] = True
            elif w[:1] == ["neighbor"] and len(w) > 2:
                _bgp_neighbor_statement(_neighbor(r["neighbors"], w[1]), w[2:])
            elif w[:1] == ["neighbor"] and len(w) == 2:
                _neighbor(r["neighbors"], w[1])
            else:
                r["other"].append(stmt)
            continue
        fam = r["families"].setdefault(path[1], {
            "afi": path[1].split()[1] if len(path[1].split()) > 1 else "",
            "safi": path[1].split()[2] if len(path[1].split()) > 2 else "",
            "networks": [], "aggregates": [], "redistribute": [],
            "neighbors": {}, "other": []})
        if w[:1] == ["network"] and len(w) > 1:
            fam["networks"].append(w[1])
        elif w[:1] == ["aggregate-address"] and len(w) > 1:
            fam["aggregates"].append({"prefix": w[1],
                                      "summary_only": "summary-only" in w,
                                      "as_set": "as-set" in w})
        elif w[:1] == ["redistribute"]:
            fam["redistribute"].append(_redistribute(w))
        elif w[:1] == ["neighbor"] and len(w) > 2:
            _bgp_af_neighbor_statement(_neighbor_af(fam["neighbors"], w[1]),
                                       w[2:])
        else:
            fam["other"].append(stmt)
    out = []
    for key in order:
        r = routers[key]
        fams = []
        for f in r["families"].values():
            f = dict(f, neighbors=list(f["neighbors"].values()))
            fams.append(f)
        out.append(dict(r, neighbors=list(r["neighbors"].values()),
                        families=fams))
    return out


def _area_entry(areas, area):
    if area not in areas:
        areas[area] = {"area": area, "area_type": "normal", "no_summary": False,
                       "other": []}
    return areas[area]


def is_router(header, proto):
    """Is block header `header` a `router <proto> ...`? (`router ospf` is not
    `router ospf6`.)"""
    return header.split()[:2] == ["router", proto]


def ospf_typed(cfg, version=2):
    """Every `router ospf` (version 2) or `router ospf6` (3) block."""
    proto = "ospf" if version == 2 else "ospf6"
    rid = f"{proto} router-id"
    routers, order = {}, []
    for path, stmt in routing_statements(cfg):
        w = stmt.split()
        if not path and is_router(stmt, proto):
            key = stmt
        elif path and is_router(path[0], proto):
            key = path[0]
        else:
            continue
        if key not in routers:
            routers[key] = {"version": version, "vrf": _vrf_of_block(key),
                            "router_id": "", "networks": [], "areas": {},
                            "passive_default": False, "passive": [],
                            "not_passive": [], "redistribute": [],
                            "default_information": "", "other": []}
            order.append(key)
        if not path:
            continue
        r = routers[key]
        if stmt.startswith(rid + " ") and len(w) > 2:
            r["router_id"] = w[2]
        elif w[:1] == ["network"] and len(w) >= 4 and w[2] == "area":
            r["networks"].append({"prefix": w[1], "area": w[3]})
        elif w[:1] == ["area"] and len(w) >= 3 and w[2] in ("stub", "nssa"):
            a = _area_entry(r["areas"], w[1])
            a["area_type"] = w[2]
            a["no_summary"] = "no-summary" in w
        elif w[:1] == ["area"] and len(w) >= 2:
            _area_entry(r["areas"], w[1])["other"].append(stmt)
        elif w == ["passive-interface", "default"]:
            r["passive_default"] = True
        elif w[:1] == ["passive-interface"] and len(w) > 1:
            r["passive"].append(w[1])
        elif w[:2] == ["no", "passive-interface"] and len(w) > 2:
            r["not_passive"].append(w[2])
        elif w[:1] == ["redistribute"]:
            r["redistribute"].append(_redistribute(w))
        elif w[:1] == ["default-information"]:
            r["default_information"] = stmt
        else:
            r["other"].append(stmt)
    return [dict(routers[k], areas=list(routers[k]["areas"].values()))
            for k in order]


def isis_typed(cfg):
    """Every `router isis <tag>` block."""
    routers, order = {}, []
    for path, stmt in routing_statements(cfg):
        w = stmt.split()
        if not path and w[:2] == ["router", "isis"]:
            key = stmt
        elif path and path[0].split()[:2] == ["router", "isis"]:
            key = path[0]
        else:
            continue
        if key not in routers:
            kw = key.split()
            routers[key] = {"tag": kw[2] if len(kw) > 2 else "",
                            "vrf": _vrf_of_block(key), "nets": [],
                            "is_type": "", "metric_style": "",
                            "redistribute": [], "other": []}
            order.append(key)
        if not path:
            continue
        r = routers[key]
        if w[:1] == ["net"] and len(w) > 1:
            r["nets"].append(w[1])
        elif w[:1] == ["is-type"] and len(w) > 1:
            r["is_type"] = w[1]
        elif w[:1] == ["metric-style"] and len(w) > 1:
            r["metric_style"] = w[1]
        elif w[:1] == ["redistribute"]:
            r["redistribute"].append(_redistribute(w))
        else:
            r["other"].append(stmt)
    return [routers[k] for k in order]


def interface_routing_typed(cfg):
    """What each FRR `interface <name>` block says to OSPF, OSPF6 and IS-IS."""
    out, order = {}, []
    for path, stmt in routing_statements(cfg):
        if len(path) != 1 or not path[0].startswith("interface "):
            continue
        name = path[0].split()[1]
        if name not in out:
            out[name] = {"name": name, "ospf_area": "", "ospf_cost": 0,
                         "ospf_passive": False, "ospf_network": "",
                         "ospf_hello": 0, "ospf_dead": 0, "ospf6_area": "",
                         "ospf6_cost": 0, "ospf6_passive": False,
                         "isis_instances": [], "isis_circuit_type": "",
                         "isis_metric": 0, "isis_passive": False, "other": []}
            order.append(name)
        r, w = out[name], stmt.split()
        v = w[3] if len(w) > 3 else ""
        if w[:3] == ["ip", "ospf", "area"]:
            r["ospf_area"] = v
        elif w[:3] == ["ip", "ospf", "cost"]:
            r["ospf_cost"] = _int(v)
        elif w[:3] == ["ip", "ospf", "passive"]:
            r["ospf_passive"] = True
        elif w[:3] == ["ip", "ospf", "network"]:
            r["ospf_network"] = v
        elif w[:3] == ["ip", "ospf", "hello-interval"]:
            r["ospf_hello"] = _int(v)
        elif w[:3] == ["ip", "ospf", "dead-interval"]:
            r["ospf_dead"] = _int(v)
        elif w[:3] == ["ipv6", "ospf6", "area"]:
            r["ospf6_area"] = v
        elif w[:3] == ["ipv6", "ospf6", "cost"]:
            r["ospf6_cost"] = _int(v)
        elif w[:3] == ["ipv6", "ospf6", "passive"]:
            r["ospf6_passive"] = True
        elif w[1:3] == ["router", "isis"] and len(w) > 3:
            if w[3] not in r["isis_instances"]:
                r["isis_instances"].append(w[3])
        elif w[:2] == ["isis", "circuit-type"] and len(w) > 2:
            r["isis_circuit_type"] = w[2]
        elif w[:2] == ["isis", "metric"] and len(w) > 2:
            r["isis_metric"] = _int(w[2])
        elif w[:2] == ["isis", "passive"]:
            r["isis_passive"] = True
        else:
            r["other"].append(stmt)
    return [out[n] for n in order]


# ------------------------------------------------- routing edits (phase 3)

STATIC_FIELDS = ("next_hop", "distance", "tag", "description", "extra")
STATIC_PATH = ["router static"]


def static_line(r):
    """A route's fields as the one line `router static` takes (#436)."""
    if not r.get("prefix") or not r.get("next_hop"):
        raise ValueError("static route: give prefix and next_hop")
    line = f"{r['prefix']} next-hop {r['next_hop']}"
    if r.get("extra"):
        line += " " + " ".join(r["extra"])
    if r.get("distance"):
        line += f" distance {int(r['distance'])}"
    if r.get("tag"):
        line += f" tag {int(r['tag'])}"
    if r.get("description"):
        line += f" description {r['description']}"
    return line


def static_lines(cur, desired, fields=()):
    """
    The line that sets one top-level route. A route IS its prefix and next
    hop, and typing the pair again replaces it whole, description included
    (#436) - so the line carries every attribute, the ones not named in
    `fields` kept from the candidate.
    """
    _mask(fields, STATIC_FIELDS + ("prefix",))
    want = dict(cur or {})
    want.update({k: desired.get(k) for k in ("prefix", "next_hop")})
    for k in STATIC_FIELDS:
        if not fields or k in fields:
            want[k] = desired.get(k) if k != "extra" else \
                list(desired.get("extra") or [])
    return [static_line(want)]


def find_static(cfg, prefix, next_hop):
    for r in static_routes_typed(cfg):
        if r["vrf"] == "" and r["prefix"] == prefix and \
                r["next_hop"] == next_hop:
            return r
    return None


NEIGHBOR_FIELDS = ("remote_as", "description", "update_source", "timers",
                   "password", "peer_group", "ebgp_multihop", "shutdown",
                   "bfd", "families")
FAMILY_FIELDS = ("activate", "route_map_in", "route_map_out",
                 "prefix_list_in", "prefix_list_out", "maximum_prefix",
                 "next_hop_self", "soft_reconfiguration_inbound",
                 "default_originate")


def bgp_path(asn, vrf=""):
    if not asn:
        raise ValueError("bgp: name the router's asn")
    return [f"router bgp {asn}" + (f" vrf {vrf}" if vrf else "")]


def find_bgp(cfg, asn, vrf=""):
    for r in bgp_typed(cfg):
        if r["asn"] == str(asn) and r["vrf"] == (vrf or ""):
            return r
    return None


def _flag(n, word, now, want):
    if bool(now) == bool(want):
        return []
    return [f"{'' if want else 'no '}neighbor {n} {word}"]


def _value(n, word, now, want, render=str):
    if (now or "") == (want or "") or (not now and not want):
        return []
    if not want:
        return [f"no neighbor {n} {word} {render(now)}".rstrip()]
    return [f"neighbor {n} {word} {render(want)}"]


def bgp_neighbor_lines(cur_router, edit, fields=()):
    """
    The lines that set one neighbour in a `router bgp` block, at that block:
    router-level statements first, then each family entered, set and left.
    `cur_router` is the block as bgp_typed reads it from the candidate (None
    when the router is new); `edit` is a BgpNeighborEdit as a dict.
    """
    _mask(fields, NEIGHBOR_FIELDS)
    want = edit.get("neighbor") or {}
    n = want.get("address") or ""
    if not n:
        raise ValueError("bgp neighbor: give its address")
    cur = next((x for x in (cur_router or {}).get("neighbors", [])
                if x["address"] == n), None) or {}
    on = (lambda f: not fields or f in fields)
    lines = []
    if want.get("is_peer_group") and not cur.get("is_peer_group"):
        lines.append(f"neighbor {n} peer-group")
    if on("remote_as") and (want.get("remote_as") or "") != \
            (cur.get("remote_as") or ""):
        lines.append(f"neighbor {n} remote-as {want['remote_as']}"
                     if want.get("remote_as")
                     else f"no neighbor {n} remote-as {cur['remote_as']}")
    if on("peer_group"):
        lines += _value(n, "peer-group", cur.get("peer_group"),
                        want.get("peer_group"))
    if on("description"):
        lines += _value(n, "description", cur.get("description"),
                        want.get("description"))
    if on("update_source"):
        lines += _value(n, "update-source", cur.get("update_source"),
                        want.get("update_source"))
    if on("timers"):
        now = (cur.get("keepalive", 0), cur.get("holdtime", 0))
        to = (int(want.get("keepalive") or 0), int(want.get("holdtime") or 0))
        if now != to:
            lines.append(f"neighbor {n} timers {to[0]} {to[1]}" if any(to)
                         else f"no neighbor {n} timers")
    if on("password") and edit.get("password") is not None:
        pw = edit.get("password")
        if pw:
            if any(c.isspace() for c in pw):
                raise ValueError("bgp neighbor password: no spaces")
            lines.append(f"neighbor {n} password {pw}")
        elif cur.get("password_set"):
            lines.append(f"no neighbor {n} password")
    if on("ebgp_multihop") and int(want.get("ebgp_multihop") or 0) != \
            int(cur.get("ebgp_multihop") or 0):
        hop = int(want.get("ebgp_multihop") or 0)
        lines.append(f"neighbor {n} ebgp-multihop {hop}" if hop
                     else f"no neighbor {n} ebgp-multihop")
    if on("shutdown"):
        lines += _flag(n, "shutdown", cur.get("shutdown"),
                       want.get("shutdown"))
    if on("bfd"):
        lines += _flag(n, "bfd", cur.get("bfd"), want.get("bfd"))
    if on("families"):
        fams = {(f["afi"], f["safi"]): f
                for f in (cur_router or {}).get("families", [])}
        for fe in edit.get("families") or []:
            afi, safi = fe.get("afi") or "ipv4", fe.get("safi") or "unicast"
            fw = fe.get("settings") or {}
            fc = next((x for x in fams.get((afi, safi), {}).get("neighbors",
                                                                 [])
                       if x["neighbor"] == n), None) or {}
            fl = _flag(n, "activate", fc.get("activate"), fw.get("activate"))
            for word, key, d in (("route-map", "route_map", ("in", "out")),
                                 ("prefix-list", "prefix_list", ("in", "out"))):
                for direction in d:
                    k = f"{key}_{direction}"
                    now, to = fc.get(k) or "", fw.get(k) or ""
                    if now != to:
                        fl.append(f"neighbor {n} {word} {to} {direction}"
                                  if to else
                                  f"no neighbor {n} {word} {now} {direction}")
            now, to = int(fc.get("maximum_prefix") or 0), \
                int(fw.get("maximum_prefix") or 0)
            if now != to:
                fl.append(f"neighbor {n} maximum-prefix {to}" if to
                          else f"no neighbor {n} maximum-prefix")
            fl += _flag(n, "next-hop-self", fc.get("next_hop_self"),
                        fw.get("next_hop_self"))
            fl += _flag(n, "soft-reconfiguration inbound",
                        fc.get("soft_reconfiguration_inbound"),
                        fw.get("soft_reconfiguration_inbound"))
            fl += _flag(n, "default-originate", fc.get("default_originate"),
                        fw.get("default_originate"))
            if fl:
                lines += [f"address-family {afi} {safi}"] + fl + \
                    ["exit-address-family"]
    return lines


def bgp_neighbor_delete_lines(cfg, asn, vrf, name):
    """
    The lines that take neighbour `name` out of a `router bgp` block: every
    statement the candidate holds for it, each removed as it is written -
    each family's first, then the router level's, `remote-as` and the
    peer-group membership last.

    NOT `no neighbor <name>`, which is what an operator would expect to
    type. Measured on this VTY with FRR 10.7.1 (#558 phase 3): it is staged
    as a statement of its own rather than as the removal of the lines that
    are there, so a neighbour with only a description is deleted by FRR and
    then reported as a statement FRR "did not keep", and one with a family
    statement fails frr-reload ("% Specify remote-as or peer-group commands
    first") and the commit rolls back. Statement by statement it goes, and
    nothing of it is left in FRR or in the configuration.
    """
    header = bgp_path(asn, vrf)[0]
    fams, top, last = {}, [], []
    for path, stmt in routing_statements(cfg):
        w = stmt.split()
        if not path or path[0] != header or w[:2] != ["neighbor", name]:
            continue
        if len(path) == 2:
            fams.setdefault(path[1], []).append("no " + stmt)
        elif len(w) > 2 and w[2] in ("remote-as", "peer-group"):
            last.append("no " + stmt)
        else:
            top.append("no " + stmt)
    if not (fams or top or last):
        raise ValueError(f"bgp: no neighbour {name} in {header}")
    lines = []
    for fam, removes in fams.items():
        lines += [fam] + removes + ["exit-address-family"]
    return lines + top + last


_SECRET = re.compile(r"^(\s*(?:no\s+)?neighbor\s+\S+\s+password)\s+.*$")


def shown_lines(lines):
    """`lines` as a client may see them: a BGP neighbour's password masked,
    as `show running-config` masks it - only that statement, so a
    description that says "password" is left alone. The box is given the
    real lines."""
    return [_SECRET.sub(r"\1 <set, not shown>", ln) for ln in lines]


def delete_for(cfg, area, name="", transport="", prefix="", next_hop="",
               asn="", vrf=""):
    """(path, lines) that remove one object from Config `cfg` - the
    candidate: a BGP neighbour is removed by its own statements, read off
    `cfg`; everything else as delete_lines says."""
    if area == "bgp":
        if not name:
            raise ValueError("bgp: name the neighbour to remove")
        return bgp_path(asn, vrf), bgp_neighbor_delete_lines(cfg, asn, vrf,
                                                             name)
    return delete_lines(area, name, transport, prefix, next_hop, asn, vrf)


def routing_typed(cfg, areas):
    """The routing areas asked for, of Config `cfg`."""
    out = {}
    if "static" in areas:
        out["static"] = static_routes_typed(cfg)
    if "bgp" in areas:
        out["bgp"] = bgp_typed(cfg)
    if "ospf" in areas:
        out["ospf"] = ospf_typed(cfg, 2)
    if "ospf6" in areas:
        out["ospf6"] = ospf_typed(cfg, 3)
    if "isis" in areas:
        out["isis"] = isis_typed(cfg)
    if "interface_routing" in areas:
        out["interface_routing"] = interface_routing_typed(cfg)
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


def delete_lines(area, name="", transport="", prefix="", next_hop="",
                 asn="", vrf=""):
    """(path, lines) that remove one object, as the VTY removes it."""
    if area == "static":
        if not prefix or not next_hop:
            raise ValueError("static: give the route's prefix and next_hop")
        return list(STATIC_PATH), [f"no {prefix} next-hop {next_hop}"]
    if area == "bgp":
        raise ValueError("bgp: a neighbour is removed by its statements, "
                         "read off the candidate: delete_for")
    if area == "interfaces":
        return [], [f"no interface {name}"]
    if area == "pools":
        return ["nat", "cgnat"], [f"no pool {name}"]
    if area == "logging":
        if transport not in TRANSPORTS:
            raise ValueError(f"logging: name the transport to remove: "
                             f"{', '.join(TRANSPORTS)}")
        return list(LOGGING_PATH), [f"logging {transport} disable"]
    raise ValueError(f"unknown area {area!r}: the removable areas are "
                     f"interfaces, pools, logging, static, bgp")


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
    if area == "static":
        if obj.get("vrf"):
            raise ValueError("static route: a VRF's routes are edited as "
                             "lines in its `vrf` block, not here")
        cur = find_static(cfg, obj.get("prefix"), obj.get("next_hop"))
        return list(STATIC_PATH), static_lines(cur, obj, fields)
    if area == "bgp":
        return bgp_path(obj.get("asn"), obj.get("vrf")), bgp_neighbor_lines(
            find_bgp(cfg, obj.get("asn"), obj.get("vrf")), obj, fields)
    raise ValueError(f"unknown area {area!r}: the editable areas are "
                     f"interfaces, pools, logging, static, bgp")


def names_for(cfg, area, known_interfaces=()):
    """What `<name>` may be for a typed area: the configured objects, and
    for interfaces also those the dataplane has."""
    if area == "interfaces":
        return sorted(set(cfg.interface_names()) | set(known_interfaces))
    if area == "pools":
        return sorted(cfg.pools)
    return []
